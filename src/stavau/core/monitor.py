"""BLE proximity monitoring built on bleak advertisement scanning.

Presence tracking strategy (v0.1): scan continuously and smooth the RSSI of
advertisements from the trusted device. On Linux, BlueZ resolves the rotating
(RPA) address of *bonded* devices to their stable identity address, so bonding
the phone through the OS first makes tracking robust against MAC
randomization. Sampling RSSI over an established GATT connection is the
planned v0.2+ enhancement for platforms that do not resolve RPAs when
scanning.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from stavau.core.distance import RssiSmoother

if TYPE_CHECKING:
    from stavau.core.deviceid import Observation


@dataclass(frozen=True)
class DiscoveredDevice:
    address: str
    name: str
    rssi: int
    # Bluetooth SIG company IDs seen in the advertisement's manufacturer data,
    # used to label the device kind (Apple / Android / ...) in the picker.
    company_ids: frozenset[int] = frozenset()


async def scan_devices(timeout: float = 10.0) -> list[DiscoveredDevice]:
    """One-shot discovery scan for the setup wizard, strongest signal first."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = [
        DiscoveredDevice(
            address=address,
            name=adv.local_name or device.name or "<unnamed>",
            rssi=adv.rssi,
            company_ids=frozenset(adv.manufacturer_data.keys()),
        )
        for address, (device, adv) in found.items()
    ]
    devices.sort(key=lambda d: d.rssi, reverse=True)
    return devices


async def probe_device(address: str, seconds: float) -> Observation:
    """Collect advertisement traits (company IDs, service UUIDs, name) for one
    device, to feed device classification. See core.deviceid.classify."""
    from stavau.core.deviceid import Observation

    target = address.upper()
    company_ids: set[int] = set()
    service_uuids: set[str] = set()
    name = ""
    count = 0

    def on_advertisement(device: BLEDevice, adv: AdvertisementData) -> None:
        nonlocal name, count
        if device.address.upper() != target:
            return
        count += 1
        company_ids.update(adv.manufacturer_data.keys())
        service_uuids.update(adv.service_uuids)
        if adv.local_name:
            name = adv.local_name
        elif device.name and not name:
            name = device.name

    scanner = BleakScanner(detection_callback=on_advertisement)
    await scanner.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await scanner.stop()
    return Observation(
        company_ids=frozenset(company_ids),
        service_uuids=frozenset(service_uuids),
        name=name,
        advertisement_count=count,
    )


async def pair_device(address: str) -> None:
    """Best-effort BLE bonding via bleak. Raises BleakError on failure.

    Bonding reliability varies by OS/backend; on failure the caller should
    guide the user to the native OS Bluetooth pairing dialog instead.
    """
    from bleak import BleakClient

    async with BleakClient(address) as client:
        await client.pair()


async def sample_rssi(address: str, seconds: float) -> list[float]:
    """Collect raw RSSI samples from one address (calibration / status)."""
    samples: list[float] = []
    target = address.upper()

    def on_advertisement(device: BLEDevice, adv: AdvertisementData) -> None:
        if device.address.upper() == target:
            samples.append(float(adv.rssi))

    scanner = BleakScanner(detection_callback=on_advertisement)
    await scanner.start()
    try:
        await asyncio.sleep(seconds)
    finally:
        await scanner.stop()
    return samples


@dataclass(frozen=True)
class NearbyDevice:
    address: str
    name: str
    rssi: float
    age_seconds: float


class NearbyCache:
    """Rolling view of every device seen by the scanner (feeds UI pickers).

    Most phones omit their name from BLE advertisements (privacy + packet
    size), so entries are frequently "<unnamed>"; the OS Bluetooth settings
    UI gets names from the Classic channel or from bonded-device caches.
    """

    def __init__(self, max_age_seconds: float = 30.0) -> None:
        self._max_age = max_age_seconds
        self._seen: dict[str, tuple[str, float, float]] = {}

    def push(self, address: str, name: str | None, rssi: float, now: float) -> None:
        remembered = self._seen.get(address)
        known_name = name or (remembered[0] if remembered else "")
        self._seen[address] = (known_name, rssi, now)

    def list(self, now: float) -> list[NearbyDevice]:
        fresh: list[NearbyDevice] = []
        for address, (name, rssi, seen_at) in list(self._seen.items()):
            age = now - seen_at
            if age > self._max_age:
                del self._seen[address]
                continue
            fresh.append(NearbyDevice(address, name or "<unnamed>", rssi, age))
        fresh.sort(key=lambda device: device.rssi, reverse=True)
        return fresh


class RssiTracker:
    """Smoothed RSSI with staleness.

    No advertisement for longer than `stale_seconds` means "no reliable
    signal" and `smoothed()` returns None — the fail-safe path that the
    presence state machine treats as infinitely far.
    """

    def __init__(self, smoothing_window: int, stale_seconds: float = 15.0) -> None:
        self._window = smoothing_window
        self._stale_seconds = stale_seconds
        self._smoother = RssiSmoother(window=smoothing_window)
        self._last_seen: float | None = None

    def reset(self) -> None:
        """Forget all samples (e.g. after switching to a different device)."""
        self._smoother = RssiSmoother(window=self._window)
        self._last_seen = None

    def push(self, rssi: float, now: float) -> None:
        if self._last_seen is not None and now - self._last_seen > self._stale_seconds:
            # After a long gap old samples describe a stale situation:
            # restart smoothing instead of averaging across the gap.
            self._smoother = RssiSmoother(window=self._window)
        self._smoother.push(rssi)
        self._last_seen = now

    def smoothed(self, now: float) -> float | None:
        if self._last_seen is None or now - self._last_seen > self._stale_seconds:
            return None
        return self._smoother.value

    @property
    def last_seen(self) -> float | None:
        return self._last_seen


class BleProximitySource:
    """Continuously scans and feeds one device's advertisements into a tracker."""

    def __init__(
        self, address: str, tracker: RssiTracker, nearby: NearbyCache | None = None
    ) -> None:
        self._address = address.upper()
        self._tracker = tracker
        self._nearby = nearby
        self._scanner: BleakScanner | None = None

    def retarget(self, address: str) -> None:
        """Switch the tracked device without restarting the scanner."""
        self._address = address.upper()

    def _on_advertisement(self, device: BLEDevice, adv: AdvertisementData) -> None:
        now = time.monotonic()
        if self._nearby is not None:
            self._nearby.push(
                device.address.upper(), adv.local_name or device.name, float(adv.rssi), now
            )
        if device.address.upper() == self._address:
            self._tracker.push(float(adv.rssi), now)

    async def start(self) -> None:
        self._scanner = BleakScanner(detection_callback=self._on_advertisement)
        await self._scanner.start()

    async def stop(self) -> None:
        if self._scanner is not None:
            await self._scanner.stop()
            self._scanner = None
