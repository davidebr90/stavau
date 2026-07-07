"""Classic Bluetooth (BR/EDR) proximity via the CLASSIC_LINK strategy.

Why this exists: idle Android phones stop advertising over BLE, so
advertisement scanning cannot see them. The reliable channel for such devices
is the bonded Classic link — the same approach Windows Dynamic Lock and the
long-standing Linux BlueProximity use.

Platform reality (researched, see docs/device-compatibility.md):
  * Linux: `l2ping` warms/keeps the link and `hcitool rssi` returns a real
    connection RSSI, even for an idle phone. This is genuine distance signal.
  * Windows: there is NO public API for the RSSI of an open Classic
    connection; the WinRT `BluetoothDevice.ConnectionStatus` gives reachability
    only (Connected/Disconnected), so the strategy degrades to in-range /
    out-of-range. Distance is synthesized (present -> near, absent -> far).
  * macOS: planned.

Each backend implements `read_rssi(address) -> float | None`: a dBm-ish value
when the device is reachable, or None when it is not (which the tracker treats
as "no signal" -> fail-safe far).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import shutil
import sys
import time
from typing import Protocol, runtime_checkable

# RSSI synthesized for a device that is reachable but whose true signal
# strength we cannot measure (reachability-only backends). Maps, under default
# calibration, to a sub-metre distance -> firmly "near".
PRESENT_RSSI_DBM = -45.0

_DEFAULT_POLL_SECONDS = 4.0


@runtime_checkable
class ClassicBackend(Protocol):
    name: str

    async def read_rssi(self, address: str) -> float | None:
        """dBm-ish value if reachable, else None. Must not raise for the normal
        'device not reachable' case — return None instead."""
        ...


class ClassicLinkSource:
    """Polls a ClassicBackend and feeds readings into an RssiTracker.

    Mirrors BleProximitySource's interface (start/stop/retarget) so the session
    can use either behind the ProximitySource protocol.
    """

    def __init__(
        self,
        address: str,
        tracker: object,
        backend: ClassicBackend,
        poll_interval: float = _DEFAULT_POLL_SECONDS,
    ) -> None:
        self._address = address.upper()
        self._tracker = tracker
        self._backend = backend
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None

    @property
    def backend_name(self) -> str:
        return self._backend.name

    def retarget(self, address: str) -> None:
        self._address = address.upper()

    async def start(self) -> None:
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _poll_loop(self) -> None:
        while True:
            try:
                rssi = await self._backend.read_rssi(self._address)
            except Exception:  # noqa: BLE001 - a backend hiccup must not kill monitoring
                rssi = None
            if rssi is not None:
                # RssiTracker.push(rssi, now); typed loosely to avoid a cycle.
                self._tracker.push(rssi, time.monotonic())  # type: ignore[attr-defined]
            await asyncio.sleep(self._poll_interval)


# ----------------------------------------------------------------- Linux backend


class HcitoolClassicBackend:
    """Linux: `l2ping` for reachability + `hcitool rssi` for connection RSSI.

    `hcitool rssi` reports a value relative to the receiver's golden range
    (0 ~ within -60..-40 dBm; negative = weaker; positive = stronger), and it
    only works once a connection exists — hence the l2ping first. We map the
    relative value to an approximate dBm so it flows through the same
    distance/smoothing/hysteresis pipeline as BLE.
    """

    name = "linux-hcitool"
    _RSSI_RE = re.compile(r"RSSI return value:\s*(-?\d+)")
    _GOLDEN_MID_DBM = -50.0

    def __init__(self, ping_timeout: float = 3.0) -> None:
        self._ping_timeout = ping_timeout

    @staticmethod
    def available() -> bool:
        return (
            sys.platform.startswith("linux")
            and shutil.which("hcitool") is not None
            and shutil.which("l2ping") is not None
        )

    async def read_rssi(self, address: str) -> float | None:
        reachable = await self._l2ping(address)
        if not reachable:
            return None
        value = await self._hcitool_rssi(address)
        if value is None:
            # Reachable but RSSI unreadable: still present, use the golden mid.
            return self._GOLDEN_MID_DBM
        return self._GOLDEN_MID_DBM + float(value)

    async def _l2ping(self, address: str) -> bool:
        rc, _out = await _run(
            ["l2ping", "-c", "1", "-t", str(int(self._ping_timeout)), address],
            timeout=self._ping_timeout + 2.0,
        )
        return rc == 0

    async def _hcitool_rssi(self, address: str) -> int | None:
        rc, out = await _run(["hcitool", "rssi", address], timeout=5.0)
        if rc != 0:
            return None
        match = self._RSSI_RE.search(out)
        return int(match.group(1)) if match else None


# ----------------------------------------------------------------- Windows backend


class WinRtConnectionBackend:
    """Windows: reachability via WinRT `BluetoothDevice.ConnectionStatus`.

    No public API exposes the RSSI of an open Classic connection on Windows, so
    this is presence-only: Connected -> synthesized near RSSI, Disconnected ->
    None (far). It reflects *active* Classic connections, so it is best for
    devices that keep a link up; an idle phone that has dropped its Classic
    connection can read as Disconnected even when physically near.
    """

    name = "windows-winrt"

    @staticmethod
    def available() -> bool:
        if sys.platform != "win32":
            return False
        try:
            import winrt.windows.devices.bluetooth  # noqa: F401
        except ImportError:
            return False
        return True

    @staticmethod
    def _mac_to_int(address: str) -> int:
        return int(address.replace(":", "").replace("-", ""), 16)

    async def read_rssi(self, address: str) -> float | None:
        # A malformed address is a config problem, not "reachable" — degrade to
        # None (the Protocol forbids raising for the not-reachable case), before
        # any WinRT work.
        try:
            bt_addr = self._mac_to_int(address)
        except ValueError:
            return None
        # Any WinRT/COM error (adapter busy, device never bonded, transient
        # failure) is reachability-unknown -> None, never a raise into the loop.
        try:
            from winrt.windows.devices.bluetooth import (
                BluetoothConnectionStatus,
                BluetoothDevice,
            )

            device = await BluetoothDevice.from_bluetooth_address_async(bt_addr)
            if device is None:
                return None
            if device.connection_status == BluetoothConnectionStatus.CONNECTED:
                return PRESENT_RSSI_DBM
            return None
        except Exception:  # noqa: BLE001 - reachability unknown, fail safe far
            return None


def select_classic_backend() -> ClassicBackend | None:
    """Return the best available Classic backend for this platform, or None."""
    if HcitoolClassicBackend.available():
        return HcitoolClassicBackend()
    if WinRtConnectionBackend.available():
        return WinRtConnectionBackend()
    return None


async def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Run a command, returning (returncode, stdout). Timeout/kill safe."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        # A wedged child (uninterruptible D-state on a flaky adapter) can leave
        # wait() blocked forever; bound it so the poll loop always recovers.
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        return 1, ""
    return proc.returncode or 0, stdout.decode(errors="replace")
