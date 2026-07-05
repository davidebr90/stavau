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

from bleak import BleakScanner
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from stavau.core.distance import RssiSmoother


@dataclass(frozen=True)
class DiscoveredDevice:
    address: str
    name: str
    rssi: int


async def scan_devices(timeout: float = 10.0) -> list[DiscoveredDevice]:
    """One-shot discovery scan for the setup wizard, strongest signal first."""
    found = await BleakScanner.discover(timeout=timeout, return_adv=True)
    devices = [
        DiscoveredDevice(address=address, name=device.name or "<unnamed>", rssi=adv.rssi)
        for address, (device, adv) in found.items()
    ]
    devices.sort(key=lambda d: d.rssi, reverse=True)
    return devices


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

    def __init__(self, address: str, tracker: RssiTracker) -> None:
        self._address = address.upper()
        self._tracker = tracker
        self._scanner: BleakScanner | None = None

    def _on_advertisement(self, device: BLEDevice, adv: AdvertisementData) -> None:
        if device.address.upper() == self._address:
            self._tracker.push(float(adv.rssi), time.monotonic())

    async def start(self) -> None:
        self._scanner = BleakScanner(detection_callback=self._on_advertisement)
        await self._scanner.start()

    async def stop(self) -> None:
        if self._scanner is not None:
            await self._scanner.stop()
            self._scanner = None
