"""Power-efficient Linux presence via BlueZ ``org.bluez.AdvertisementMonitor1``.

Instead of keeping a continuous software scan alive (``BleProximitySource``),
this source registers an *advertisement monitor* with BlueZ
(``AdvertisementMonitorManager1.RegisterMonitor``) so that in/out-of-range
detection is offloaded to the Bluetooth controller: BlueZ calls our exported
monitor object's ``DeviceFound`` / ``DeviceLost`` methods when a matching
device crosses the configured RSSI thresholds, and the CPU sleeps in between.
See docs/os-native-apis.md (section 2) and the BlueZ advertisement-monitor-api
for the protocol.

Signal model
------------
The advertisement monitor is a *binary* presence primitive (found / lost),
while the rest of stavau consumes a smoothed RSSI stream through
``RssiTracker``. The bridge is deliberately simple and honest:

* On ``DeviceFound`` for the *target* device we push one synthesized in-range
  sample equal to ``high_rssi`` (the monitor's own high threshold — i.e. "at
  least this strong") into the tracker, then keep pushing the same value at
  1 Hz while the device stays found. The tracker therefore reads "just inside
  the safety radius", never closer than the evidence supports.
* On ``DeviceLost`` (and on ``Release``, when BlueZ revokes the monitor) we
  simply *stop pushing*. No synthetic out-of-range sample is injected: the
  tracker's staleness window expires naturally and ``smoothed()`` returns
  ``None``, which the presence machine already treats as "infinitely far" —
  the fail-safe path.

Runtime degradation (composition)
---------------------------------
``build_source`` (core.strategy) is synchronous, but probing for
``AdvertisementMonitorManager1`` support requires the bus. So this source is
constructed *optimistically* and performs the support check inside
``start()``: when the manager is missing, reports no usable monitor type, or
registration fails for any reason, it silently starts an internal fallback
``BleProximitySource`` (software scanning) so monitoring always works. The
active backend is visible via ``fallback_active`` / ``note``.

All ``dbus_fast`` imports are function-local and guarded (mirroring
``stavau.platform.lockstate_linux``) so this module imports and type-checks
cleanly on every platform; ``dbus_fast`` has a mypy override in pyproject.

Pattern caveat: BlueZ requires at least one content pattern for
``or_patterns`` monitors and patterns match advertisement *data*, not
addresses, so a truly universal filter is not expressible. The default
pattern set matches the common Flags AD values (covers phones/wearables that
advertise as discoverable/connectable); address filtering happens in our
``DeviceFound`` handler. Hardware validation of the pattern set is a pending
[HV] item.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from stavau.core.distance import CalibrationModel
from stavau.core.monitor import BleProximitySource, RssiTracker

_BLUEZ_BUS = "org.bluez"
_MANAGER_IFACE = "org.bluez.AdvertisementMonitorManager1"
_MONITOR_IFACE = "org.bluez.AdvertisementMonitor1"
_DEVICE_IFACE = "org.bluez.Device1"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"
_OBJECT_MANAGER_IFACE = "org.freedesktop.DBus.ObjectManager"

_MONITOR_TYPE = "or_patterns"
_APP_ROOT = "/org/stavau/advmon"
_MONITOR_PATH = "/org/stavau/advmon/monitor0"

# Sane dBm bounds for controller-side thresholds.
RSSI_MIN_DBM = -100
RSSI_MAX_DBM = -20

_KEEPALIVE_INTERVAL_S = 1.0

# Flags AD (type 0x01) values commonly seen from phones and wearables
# (LE general/limited discoverable, with/without BR/EDR support bits).
# Patterns match AD payload content, so this is "broad", not universal.
_BROAD_PATTERNS: tuple[tuple[int, int, bytes], ...] = (
    (0, 0x01, b"\x02"),
    (0, 0x01, b"\x04"),
    (0, 0x01, b"\x05"),
    (0, 0x01, b"\x06"),
    (0, 0x01, b"\x1a"),
    (0, 0x01, b"\x1b"),
)


@dataclass(frozen=True)
class MonitorSpec:
    """Everything the bus backend needs to export and register one monitor."""

    high_rssi: int
    low_rssi: int
    high_timer_s: int
    low_timer_s: int
    patterns: tuple[tuple[int, int, bytes], ...]
    on_device_found: Callable[[str], None]
    on_device_lost: Callable[[str], None]
    on_release: Callable[[], None]


class _AdvMonitorBusLike(Protocol):
    """Duck-typed bus backend so tests can inject a fake (no real D-Bus)."""

    async def supported_monitor_types(self) -> list[str]: ...

    async def register_monitor(self, spec: MonitorSpec) -> None: ...

    async def unregister_monitor(self) -> None: ...

    async def device_address(self, device_path: str) -> str | None: ...

    async def disconnect(self) -> None: ...

    def is_alive(self) -> bool:
        """Liveness check consulted by the keepalive (getattr-checked at the
        call site, so duck-typed fakes without it simply count as alive)."""
        ...


BusFactory = Callable[[], Any]
"""Factory returning a bus backend; may be sync (tests) or async (real)."""


class _FallbackLike(Protocol):
    """Shape of the internal fallback source (matches ProximitySource)."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def retarget(self, address: str) -> None: ...


FallbackFactory = Callable[[str, RssiTracker], "_FallbackLike"]


async def _call_factory(factory: Callable[[], Any]) -> Any:
    """Call a bus factory, awaiting the result if it is awaitable.

    The real factory is async (it must connect to the system bus); fakes in
    tests may be plain sync callables. Supporting both keeps doubles simple.
    """
    result = factory()
    if asyncio.iscoroutine(result):
        return await result
    return result


async def _disconnect_quietly(bus: _AdvMonitorBusLike) -> None:
    with contextlib.suppress(Exception):
        await bus.disconnect()


def _clamp_dbm(value: int) -> int:
    return max(RSSI_MIN_DBM, min(RSSI_MAX_DBM, value))


def thresholds_from_settings(
    radius_m: float, grace_seconds: float, model: CalibrationModel
) -> tuple[int, int, int, int]:
    """Map the safety radius to controller RSSI thresholds and timers.

    Returns ``(high_rssi, low_rssi, high_timer_s, low_timer_s)``:

    * ``high_rssi``: expected RSSI at 80% of the radius — the device must get
      comfortably inside the radius before the controller reports "found".
    * ``low_rssi``: expected RSSI at the radius itself — dropping below it
      (for ``low_timer_s``) means the device left the safety zone.
    * ``high_timer_s`` is fixed at 2 s (fast, debounced entry); ``low_timer_s``
      is ``ceil(grace_seconds / 2)`` so the controller-side exit debounce
      consumes at most half of the user's grace period, leaving the presence
      machine's own grace logic in charge of the rest.

    Both thresholds are clamped to [-100, -20] dBm. The log-distance model is
    monotonic, so ``high_rssi >= low_rssi`` always holds (clamping can at most
    make them equal).
    """
    high_rssi = _clamp_dbm(round(model.rssi_at(radius_m * 0.8)))
    low_rssi = _clamp_dbm(round(model.rssi_at(radius_m)))
    high_timer_s = 2
    low_timer_s = max(1, math.ceil(grace_seconds / 2))
    return high_rssi, low_rssi, high_timer_s, low_timer_s


class AdvMonitorSource:
    """ProximitySource backed by a BlueZ advertisement monitor.

    Constructed optimistically (no I/O); ``start()`` probes for
    AdvertisementMonitorManager1 support and degrades to an internal
    ``BleProximitySource`` when the monitor cannot be registered. See the
    module docstring for the found/lost -> RssiTracker signal model.
    """

    def __init__(
        self,
        address: str,
        tracker: RssiTracker,
        high_rssi: int,
        low_rssi: int,
        high_timer_s: int,
        low_timer_s: int,
        *,
        bus_factory: BusFactory | None = None,
        fallback_factory: FallbackFactory | None = None,
        keepalive_interval_s: float = _KEEPALIVE_INTERVAL_S,
        patterns: Sequence[tuple[int, int, bytes]] | None = None,
    ) -> None:
        self._address = address.upper()
        self._tracker = tracker
        self._high_rssi = high_rssi
        self._low_rssi = low_rssi
        self._high_timer_s = high_timer_s
        self._low_timer_s = low_timer_s
        self._bus_factory = bus_factory
        self._fallback_factory = fallback_factory
        self._keepalive_interval_s = keepalive_interval_s
        self._patterns = tuple(patterns) if patterns is not None else _BROAD_PATTERNS
        self._bus: _AdvMonitorBusLike | None = None
        self._fallback: _FallbackLike | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._resolve_tasks: set[asyncio.Task[None]] = set()
        self._found_path: str | None = None
        self.note = "not started"

    # ------------------------------------------------------------ properties

    @property
    def high_rssi(self) -> int:
        return self._high_rssi

    @property
    def low_rssi(self) -> int:
        return self._low_rssi

    @property
    def high_timer_s(self) -> int:
        return self._high_timer_s

    @property
    def low_timer_s(self) -> int:
        return self._low_timer_s

    @property
    def fallback_active(self) -> bool:
        return self._fallback is not None

    # ---------------------------------------------------- ProximitySource API

    async def start(self) -> None:
        """Register the monitor, or degrade to software scanning.

        Never raises for lack of BlueZ support: any probe/registration
        failure starts the internal ``BleProximitySource`` fallback instead.
        """
        try:
            factory = self._bus_factory or _default_bus_factory
            bus: _AdvMonitorBusLike = await _call_factory(factory)
        except Exception:
            await self._start_fallback()
            return
        try:
            types = await bus.supported_monitor_types()
            if _MONITOR_TYPE not in types:
                raise RuntimeError(f"monitor type {_MONITOR_TYPE!r} unsupported")
            await bus.register_monitor(self._make_spec())
        except Exception:
            await _disconnect_quietly(bus)
            await self._start_fallback()
            return
        self._bus = bus
        self.note = "BlueZ advertisement monitor registered (controller-offloaded)"

    async def stop(self) -> None:
        """Unregister and disconnect; cancels the keepalive. Idempotent."""
        self._stop_tracking()
        await self._reap_tasks()
        fallback = self._fallback
        self._fallback = None
        if fallback is not None:
            await fallback.stop()
        bus = self._bus
        self._bus = None
        if bus is not None:
            with contextlib.suppress(Exception):
                await bus.unregister_monitor()
            await _disconnect_quietly(bus)

    def retarget(self, address: str) -> None:
        """Switch the tracked device; resets found-state and the keepalive."""
        self._address = address.upper()
        self._stop_tracking()
        if self._fallback is not None:
            self._fallback.retarget(address)

    # ------------------------------------------------------------- internals

    def _make_spec(self) -> MonitorSpec:
        return MonitorSpec(
            high_rssi=self._high_rssi,
            low_rssi=self._low_rssi,
            high_timer_s=self._high_timer_s,
            low_timer_s=self._low_timer_s,
            patterns=self._patterns,
            on_device_found=self._on_device_found,
            on_device_lost=self._on_device_lost,
            on_release=self._on_release,
        )

    async def _start_fallback(self) -> None:
        if self._fallback is not None:
            return
        factory = self._fallback_factory or _default_fallback_factory
        fallback = factory(self._address, self._tracker)
        await fallback.start()
        self._fallback = fallback
        self.note = "advertisement monitor unavailable; using adv_scan fallback"

    def _on_device_found(self, device_path: str) -> None:
        """Sync callback from the bus backend: resolve the address async."""
        with contextlib.suppress(RuntimeError):
            loop = asyncio.get_running_loop()
            task = loop.create_task(self._resolve_and_track(device_path))
            self._resolve_tasks.add(task)
            task.add_done_callback(self._resolve_tasks.discard)

    async def _resolve_and_track(self, device_path: str) -> None:
        bus = self._bus
        if bus is None:
            return
        try:
            address = await bus.device_address(device_path)
        except Exception:
            return
        if address is None or address.upper() != self._address:
            return
        self._found_path = device_path
        self._tracker.push(float(self._high_rssi), time.monotonic())
        if self._keepalive_task is None or self._keepalive_task.done():
            self._keepalive_task = asyncio.get_running_loop().create_task(self._keepalive())

    def _on_device_lost(self, device_path: str) -> None:
        """Stop pushing; the tracker's staleness window fails safe."""
        if self._found_path is not None and device_path != self._found_path:
            return
        self._stop_tracking()

    def _on_release(self) -> None:
        """BlueZ revoked the monitor: stop pushing (staleness fails safe)."""
        self._stop_tracking()
        self.note = "advertisement monitor released by BlueZ; awaiting restart"

    def _stop_tracking(self) -> None:
        self._found_path = None
        task = self._keepalive_task
        self._keepalive_task = None
        if task is not None:
            task.cancel()
            # Park it with the resolver tasks so stop() awaits the
            # cancellation and nothing leaks past shutdown.
            self._resolve_tasks.add(task)
            task.add_done_callback(self._resolve_tasks.discard)

    async def _keepalive(self) -> None:
        # Fail-safe anchor: synthesized presence must stay tied to bus
        # liveness. Without this, a BlueZ/adapter death that never delivers
        # Release would keep faking presence forever (fail-open). Every 5th
        # beat we verify the bus; on death we stop pushing (staleness locks)
        # and hand off to the scanning fallback.
        beats = 0
        while True:
            await asyncio.sleep(self._keepalive_interval_s)
            beats += 1
            if beats % 5 == 0 and not self._bus_alive():
                self.note = "D-Bus/BlueZ connection lost; synthesized presence stopped"
                await self._start_fallback()
                self._stop_tracking()  # cancels this task; nothing awaits after
                return
            self._tracker.push(float(self._high_rssi), time.monotonic())

    def _bus_alive(self) -> bool:
        """Best-effort bus liveness. Unknown (no checker) counts as alive;
        any checker error counts as dead (conservative)."""
        bus = self._bus
        if bus is None:
            return False
        checker = getattr(bus, "is_alive", None)
        if checker is None:
            return True
        try:
            return bool(checker())
        except Exception:  # noqa: BLE001 - a broken checker means a broken bus
            return False

    async def _reap_tasks(self) -> None:
        """Await cancelled/pending helper tasks so nothing leaks past stop()."""
        tasks = list(self._resolve_tasks)
        self._resolve_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


async def advmonitor_supported(bus_factory: BusFactory | None = None) -> bool:
    """Probe for AdvertisementMonitorManager1 on any adapter. Never raises."""
    try:
        bus: _AdvMonitorBusLike = await _call_factory(bus_factory or _default_bus_factory)
    except Exception:
        return False
    try:
        return len(await bus.supported_monitor_types()) > 0
    except Exception:
        return False
    finally:
        await _disconnect_quietly(bus)


def make_source(
    address: str,
    tracker: RssiTracker,
    radius_m: float,
    grace_seconds: float,
    rssi_at_1m: float,
    path_loss_exponent: float,
    bus_factory: BusFactory | None = None,
) -> AdvMonitorSource:
    """Build an AdvMonitorSource from settings-shaped values."""
    model = CalibrationModel(rssi_at_1m=rssi_at_1m, path_loss_exponent=path_loss_exponent)
    high_rssi, low_rssi, high_timer_s, low_timer_s = thresholds_from_settings(
        radius_m, grace_seconds, model
    )
    return AdvMonitorSource(
        address,
        tracker,
        high_rssi,
        low_rssi,
        high_timer_s,
        low_timer_s,
        bus_factory=bus_factory,
    )


def _default_fallback_factory(address: str, tracker: RssiTracker) -> _FallbackLike:
    return BleProximitySource(address, tracker)


# ------------------------------------------------------------- real backend


def _build_monitor_interface(spec: MonitorSpec) -> Any:
    """Create the dbus_fast ServiceInterface implementing AdvertisementMonitor1.

    dbus_fast derives D-Bus member names from ``__name__`` and signatures from
    ``__annotations__``, so both are assigned explicitly *before* decorating.
    This sidesteps PEP 563 (module-wide ``from __future__ import annotations``
    would stringify inline signature annotations like ``"o"`` into ``'"o"'``)
    and keeps mypy strict happy without platform-specific ignores.
    """
    from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method

    def release(self: Any) -> None:
        spec.on_release()

    release.__name__ = "Release"
    release.__annotations__ = {}

    def activate(self: Any) -> None:
        return None

    activate.__name__ = "Activate"
    activate.__annotations__ = {}

    def device_found(self: Any, device: Any) -> None:
        spec.on_device_found(str(device))

    device_found.__name__ = "DeviceFound"
    device_found.__annotations__ = {"device": "o"}

    def device_lost(self: Any, device: Any) -> None:
        spec.on_device_lost(str(device))

    device_lost.__name__ = "DeviceLost"
    device_lost.__annotations__ = {"device": "o"}

    def type_prop(self: Any) -> Any:
        return _MONITOR_TYPE

    type_prop.__name__ = "Type"
    type_prop.__annotations__ = {"return": "s"}

    def high_threshold(self: Any) -> Any:
        return spec.high_rssi

    high_threshold.__name__ = "RSSIHighThreshold"
    high_threshold.__annotations__ = {"return": "n"}

    def low_threshold(self: Any) -> Any:
        return spec.low_rssi

    low_threshold.__name__ = "RSSILowThreshold"
    low_threshold.__annotations__ = {"return": "n"}

    def high_timeout(self: Any) -> Any:
        return spec.high_timer_s

    high_timeout.__name__ = "RSSIHighTimeout"
    high_timeout.__annotations__ = {"return": "q"}

    def low_timeout(self: Any) -> Any:
        return spec.low_timer_s

    low_timeout.__name__ = "RSSILowTimeout"
    low_timeout.__annotations__ = {"return": "q"}

    def patterns(self: Any) -> Any:
        return [
            [start_position, ad_type, bytes(content)]
            for (start_position, ad_type, content) in spec.patterns
        ]

    patterns.__name__ = "Patterns"
    patterns.__annotations__ = {"return": "a(yyay)"}

    read = PropertyAccess.READ
    namespace: dict[str, Any] = {
        "Release": method()(release),
        "Activate": method()(activate),
        "DeviceFound": method()(device_found),
        "DeviceLost": method()(device_lost),
        "Type": dbus_property(access=read)(type_prop),
        "RSSIHighThreshold": dbus_property(access=read)(high_threshold),
        "RSSILowThreshold": dbus_property(access=read)(low_threshold),
        "RSSIHighTimeout": dbus_property(access=read)(high_timeout),
        "RSSILowTimeout": dbus_property(access=read)(low_timeout),
        "Patterns": dbus_property(access=read)(patterns),
    }
    interface_cls = type("StavauAdvertisementMonitor", (ServiceInterface,), namespace)
    return interface_cls(_MONITOR_IFACE)


def _unwrap(value: Any) -> Any:
    """Unwrap a dbus Variant-like (duck-typed via .value) to its raw value."""
    return value.value if hasattr(value, "value") else value


class _DbusFastAdvMonitorBus:
    """Real bus backend over dbus_fast. All dbus_fast imports are local.

    Registration follows the BlueZ advertisement-monitor-api flow: export the
    monitor object under an application root, then hand the root to
    ``AdvertisementMonitorManager1.RegisterMonitor``; BlueZ discovers the
    monitor via the ObjectManager that dbus_fast serves for exported paths.
    """

    def __init__(self) -> None:
        self._bus: Any = None
        self._manager_iface: Any = None
        self._monitor: Any = None
        self._exported = False

    @classmethod
    async def connect(cls) -> _DbusFastAdvMonitorBus:
        # dbus_fast is Linux-only (a transitive bleak dependency) and ships no
        # stubs; covered by the mypy override in pyproject.
        from dbus_fast import BusType
        from dbus_fast.aio import MessageBus

        self = cls()
        self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        return self

    async def _find_manager(self) -> tuple[str, dict[str, Any]] | None:
        introspection = await self._bus.introspect(_BLUEZ_BUS, "/")
        root = self._bus.get_proxy_object(_BLUEZ_BUS, "/", introspection)
        object_manager = root.get_interface(_OBJECT_MANAGER_IFACE)
        managed = await object_manager.call_get_managed_objects()
        for path, interfaces in managed.items():
            if _MANAGER_IFACE in interfaces:
                return str(path), dict(interfaces[_MANAGER_IFACE])
        return None

    async def supported_monitor_types(self) -> list[str]:
        found = await self._find_manager()
        if found is None:
            return []
        _path, properties = found
        raw = _unwrap(properties.get("SupportedMonitorTypes"))
        if not isinstance(raw, (list, tuple)):
            return []
        return [str(entry) for entry in raw]

    async def register_monitor(self, spec: MonitorSpec) -> None:
        found = await self._find_manager()
        if found is None:
            raise RuntimeError("no adapter exposes AdvertisementMonitorManager1")
        manager_path, _properties = found
        self._monitor = _build_monitor_interface(spec)
        self._bus.export(_MONITOR_PATH, self._monitor)
        self._exported = True
        introspection = await self._bus.introspect(_BLUEZ_BUS, manager_path)
        proxy = self._bus.get_proxy_object(_BLUEZ_BUS, manager_path, introspection)
        self._manager_iface = proxy.get_interface(_MANAGER_IFACE)
        await self._manager_iface.call_register_monitor(_APP_ROOT)

    async def unregister_monitor(self) -> None:
        if self._manager_iface is not None:
            with contextlib.suppress(Exception):
                await self._manager_iface.call_unregister_monitor(_APP_ROOT)
            self._manager_iface = None
        if self._exported:
            with contextlib.suppress(Exception):
                self._bus.unexport(_MONITOR_PATH)
            self._exported = False
        self._monitor = None

    async def device_address(self, device_path: str) -> str | None:
        try:
            introspection = await self._bus.introspect(_BLUEZ_BUS, device_path)
            proxy = self._bus.get_proxy_object(_BLUEZ_BUS, device_path, introspection)
            properties = proxy.get_interface(_PROPS_IFACE)
            raw = _unwrap(await properties.call_get(_DEVICE_IFACE, "Address"))
            return str(raw) if isinstance(raw, str) else None
        except Exception:
            return None

    def is_alive(self) -> bool:
        """dbus-fast exposes `connected` on MessageBus; absent attr counts alive."""
        return self._bus is not None and bool(getattr(self._bus, "connected", True))

    async def disconnect(self) -> None:
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None


async def _default_bus_factory() -> _AdvMonitorBusLike:
    return await _DbusFastAdvMonitorBus.connect()
