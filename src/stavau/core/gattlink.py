"""GATT_LINK strategy: RSSI over a held BLE (GATT) connection.

Why this exists: some phones are connectable over BLE but do not advertise
usefully when idle (or rotate their address in ways scanning cannot follow).
Holding a GATT connection to the (ideally bonded) device and sampling the
*connection* RSSI is the high-quality proximity channel for them.

Platform reality (researched, see docs/os-native-apis.md):
  * macOS: the one platform with a public connected-RSSI API
    (`CBPeripheral.readRSSI`); bleak exposes it on Darwin as `get_rssi()`.
    The default Darwin reader calls it defensively. [HV] — behaviour on real
    macOS hardware is pending verification.
  * Linux: no bleak API, but once a link exists the kernel exposes it via
    HCI `Read RSSI` — we shell `hcitool rssi <addr>` and reuse the
    golden-range -> dBm mapping of core.classic.HcitoolClassicBackend.
  * Windows: NO public API for the RSSI of an open connection (same
    limitation as Classic) — `gattlink_supported()` is False there and the
    strategy factory should fall back to advertisement scanning.

Battery-friendly adaptive polling: RSSI is read every BASE_POLL_SECONDS (2 s);
when the last STRONG_STREAK (5) readings are all stronger than
STRONG_RSSI_DBM (-60 dBm) — i.e. the phone is clearly nearby — polling relaxes
to RELAXED_POLL_SECONDS (6 s), and drops back to 2 s as soon as a weaker (or
missing) reading arrives, so the leave-detection latency stays sharp exactly
when it matters.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import shutil
import sys
import time
from collections import deque
from collections.abc import Awaitable, Callable, Sequence
from typing import Protocol, runtime_checkable

from stavau.core.classic import HcitoolClassicBackend, _run

# Adaptive polling knobs (documented in the module docstring).
BASE_POLL_SECONDS = 2.0
RELAXED_POLL_SECONDS = 6.0
STRONG_RSSI_DBM = -60.0
STRONG_STREAK = 5

CONNECT_TIMEOUT_SECONDS = 10.0
BACKOFF_INITIAL_SECONDS = 2.0
BACKOFF_MAX_SECONDS = 30.0

_HCITOOL_TIMEOUT_SECONDS = 5.0


@runtime_checkable
class GattClient(Protocol):
    """The slice of bleak's BleakClient that GattLinkSource needs.

    Kept minimal so tests can inject trivial fakes and so any future backend
    only has to provide connect/disconnect/is_connected.
    """

    @property
    def is_connected(self) -> bool: ...

    def connect(self) -> Awaitable[object]: ...

    def disconnect(self) -> Awaitable[object]: ...


class _TrackerLike(Protocol):
    def push(self, rssi: float, now: float) -> None: ...


ClientFactory = Callable[[str], GattClient]
RssiReader = Callable[[GattClient, str], Awaitable[float | None]]
SleepFn = Callable[[float], Awaitable[None]]


def gattlink_supported() -> bool:
    """Whether this platform can read the RSSI of a held BLE connection.

    True on Darwin (public CBPeripheral.readRSSI, surfaced by bleak) and on
    Linux when `hcitool` is installed (HCI Read RSSI on the open link). False
    on Windows: there is no public connected-RSSI API — a documented platform
    limitation (docs/os-native-apis.md).
    """
    if sys.platform == "darwin":
        return True
    if sys.platform.startswith("linux"):
        return shutil.which("hcitool") is not None
    return False


def choose_poll_interval(
    recent: Sequence[float],
    *,
    base: float = BASE_POLL_SECONDS,
    relaxed: float = RELAXED_POLL_SECONDS,
    strong_dbm: float = STRONG_RSSI_DBM,
    streak: int = STRONG_STREAK,
) -> float:
    """Pure adaptive-interval decision: relax polling only on solid evidence.

    Returns `relaxed` when the last `streak` readings are all strictly
    stronger than `strong_dbm`; `base` otherwise (including when there are
    fewer than `streak` readings — start attentive, relax later).
    """
    if streak <= 0 or len(recent) < streak:
        return base
    window = list(recent)[-streak:]
    if all(sample > strong_dbm for sample in window):
        return relaxed
    return base


def _default_client_factory(address: str) -> GattClient:
    from bleak import BleakClient

    return BleakClient(address)


async def read_rssi_darwin(client: GattClient, address: str) -> float | None:
    """Darwin: connected RSSI via the bleak client's CoreBluetooth backend.

    bleak exposes `get_rssi()` on Darwin only, so we look it up defensively:
    missing attribute, a raised error or a non-numeric result all become None
    (no reading) instead of an exception. [HV] pending verification on real
    macOS hardware — this dev box cannot execute Darwin code.
    """
    getter = getattr(client, "get_rssi", None)
    if getter is None or not callable(getter):
        return None
    try:
        value = getter()
        if inspect.isawaitable(value):
            value = await value
    except Exception:
        return None
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def read_rssi_linux(client: GattClient, address: str) -> float | None:
    """Linux: HCI Read RSSI on the open link, shelling `hcitool rssi <addr>`.

    Reuses HcitoolClassicBackend's parsing and golden-range -> dBm mapping
    (the held GATT connection replaces l2ping as the link warmer). Any failure
    (hcitool missing/erroring, unparsable output) yields None.
    """
    rc, out = await _run(["hcitool", "rssi", address], timeout=_HCITOOL_TIMEOUT_SECONDS)
    if rc != 0:
        return None
    match = HcitoolClassicBackend._RSSI_RE.search(out)
    if match is None:
        return None
    return HcitoolClassicBackend._GOLDEN_MID_DBM + float(int(match.group(1)))


async def read_rssi_unavailable(client: GattClient, address: str) -> float | None:
    """Fallback reader for platforms without a connected-RSSI API: always None,
    so nothing is pushed and the tracker's staleness fail-safe reads 'far'."""
    return None


def default_rssi_reader() -> RssiReader:
    """Pick the platform's connected-RSSI reader (see module docstring)."""
    if sys.platform == "darwin":
        return read_rssi_darwin
    if sys.platform.startswith("linux"):
        return read_rssi_linux
    return read_rssi_unavailable


class GattLinkSource:
    """Holds a GATT connection to one device and feeds its RSSI to a tracker.

    Implements the ProximitySource protocol (start/stop/retarget) like
    BleProximitySource and ClassicLinkSource, so the session stays agnostic.

    Behaviour:
      * Connection loop: connect (CONNECT_TIMEOUT_SECONDS, default 10 s), then
        poll RSSI adaptively — base 2 s, relaxing to 6 s once the last 5
        readings are all stronger than -60 dBm, snapping back to 2 s on any
        weaker or missing reading (see choose_poll_interval).
      * Each reading is pushed into the tracker; a None reading pushes nothing
        so the tracker's staleness window remains the fail-safe.
      * On disconnect or connect failure it reconnects with capped exponential
        backoff (2/4/8/... capped at 30 s); while disconnected nothing is
        pushed. A successful connect resets the backoff.
      * stop() cancels the loop and disconnects, suppressing errors; it is
        idempotent. retarget() switches address, drops the current connection
        (at the next loop iteration) and resets the backoff.
      * Failure-safe: no exception escapes the polling task.

    Everything environment-dependent is injectable for tests: the client
    factory (default bleak), the RSSI reader (default per-OS) and the sleep
    function.
    """

    def __init__(
        self,
        address: str,
        tracker: _TrackerLike,
        *,
        client_factory: ClientFactory = _default_client_factory,
        rssi_reader: RssiReader | None = None,
        connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
        base_poll: float = BASE_POLL_SECONDS,
        relaxed_poll: float = RELAXED_POLL_SECONDS,
        strong_dbm: float = STRONG_RSSI_DBM,
        strong_streak: int = STRONG_STREAK,
        backoff_initial: float = BACKOFF_INITIAL_SECONDS,
        backoff_max: float = BACKOFF_MAX_SECONDS,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._address = address.upper()
        self._tracker = tracker
        self._client_factory = client_factory
        self._rssi_reader = rssi_reader if rssi_reader is not None else default_rssi_reader()
        self._connect_timeout = connect_timeout
        self._base_poll = base_poll
        self._relaxed_poll = relaxed_poll
        self._strong_dbm = strong_dbm
        self._strong_streak = strong_streak
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._sleep = sleep

        self._backoff = backoff_initial
        self._recent: deque[float] = deque(maxlen=max(1, strong_streak))
        self._task: asyncio.Task[None] | None = None
        self._client: GattClient | None = None
        # Bumped by retarget(); the poll loop drops a connection whose
        # generation no longer matches.
        self._generation = 0

    def retarget(self, address: str) -> None:
        """Switch the tracked device: drop the connection, reset the backoff."""
        self._address = address.upper()
        self._generation += 1
        self._backoff = self._backoff_initial
        self._recent.clear()

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await client.disconnect()

    async def _run_loop(self) -> None:
        """Reconnect forever; only cancellation (via stop) ends the loop."""
        while True:
            # Belt and braces: _connect_and_poll already guards its awaits,
            # but no exception whatsoever may kill the monitoring task.
            with contextlib.suppress(Exception):
                await self._connect_and_poll()
            delay = self._backoff
            self._backoff = min(self._backoff * 2.0, self._backoff_max)
            await self._sleep(delay)

    async def _connect_and_poll(self) -> None:
        address = self._address
        generation = self._generation
        client = self._client_factory(address)
        try:
            await asyncio.wait_for(client.connect(), timeout=self._connect_timeout)
        except Exception:
            # Connect failed/timed out: the caller applies backoff.
            return
        self._client = client
        self._backoff = self._backoff_initial  # connected: future drops retry fast
        self._recent.clear()
        try:
            while self._generation == generation and _is_connected(client):
                try:
                    reading = await self._rssi_reader(client, address)
                except Exception:
                    reading = None
                if self._generation != generation:
                    # retarget() happened while the reader was in flight: this
                    # reading belongs to the OLD device and must not pollute
                    # the tracker for the new one.
                    return
                if reading is None:
                    # Push nothing (tracker staleness is the fail-safe) and
                    # forget the strong streak so polling returns to base rate.
                    self._recent.clear()
                else:
                    self._recent.append(reading)
                    self._tracker.push(reading, time.monotonic())
                await self._sleep(self._poll_interval())
        finally:
            with contextlib.suppress(Exception):
                await client.disconnect()
            if self._client is client:
                self._client = None

    def _poll_interval(self) -> float:
        return choose_poll_interval(
            tuple(self._recent),
            base=self._base_poll,
            relaxed=self._relaxed_poll,
            strong_dbm=self._strong_dbm,
            streak=self._strong_streak,
        )


def _is_connected(client: GattClient) -> bool:
    """Defensive is_connected: any error reading the property counts as down."""
    try:
        return bool(client.is_connected)
    except Exception:
        return False
