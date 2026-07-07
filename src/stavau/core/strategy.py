"""Proximity strategy factory: build the right ProximitySource for a device.

Turns the strategy recorded at setup (see core.deviceid) into a concrete,
running source, falling back safely when a strategy's backend is unavailable on
this machine. Keeps the session agnostic to which channel is in use.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from stavau.core.advmonitor import make_source as make_advmonitor_source
from stavau.core.classic import ClassicLinkSource, select_classic_backend
from stavau.core.deviceid import Strategy
from stavau.core.gattlink import GattLinkSource, gattlink_supported
from stavau.core.integration import (
    ExternalPresenceSource,
    make_presence_backend,
    parse_present_values,
)
from stavau.core.monitor import BleProximitySource, NearbyCache, RssiTracker


@runtime_checkable
class ProximitySource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def retarget(self, address: str) -> None: ...


@dataclass(frozen=True)
class BuiltSource:
    source: ProximitySource
    effective_strategy: str
    note: str


def build_source(
    strategy: str,
    address: str,
    tracker: RssiTracker,
    nearby: NearbyCache | None = None,
    *,
    radius_m: float = 3.0,
    grace_seconds: float = 10.0,
    rssi_at_1m: float = -59.0,
    path_loss_exponent: float = 2.0,
    mqtt_host: str = "",
    mqtt_port: int = 1883,
    mqtt_username: str = "",
    presence_topic: str = "",
    present_values_csv: str = "on,home,present,occupied,true,1",
) -> BuiltSource:
    """Construct the ProximitySource for `strategy`, with safe fallback.

    ADV_SCAN → BLE advertisement scanning (universal, needs the device to
    advertise). CLASSIC_LINK → bonded Classic link (real RSSI on Linux via
    hcitool; reachability on Windows). If the classic backend is unavailable we
    fall back to ADV_SCAN and say so.
    """
    if strategy == Strategy.EXTERNAL_PRESENCE.value:
        presence_backend = make_presence_backend(
            mqtt_host,
            mqtt_port,
            presence_topic,
            parse_present_values(present_values_csv),
            mqtt_username,
            os.environ.get("STAVAU_MQTT_PASSWORD", ""),
        )
        if presence_backend is not None:
            return BuiltSource(
                source=ExternalPresenceSource(tracker, presence_backend),
                effective_strategy=Strategy.EXTERNAL_PRESENCE.value,
                note=f"external presence via MQTT topic '{presence_topic}'",
            )
        return BuiltSource(
            source=BleProximitySource(address, tracker, nearby=nearby),
            effective_strategy=Strategy.ADV_SCAN.value,
            note="external_presence needs an MQTT host+topic; using adv_scan",
        )

    if strategy == Strategy.ADV_MONITOR.value:
        if sys.platform.startswith("linux"):
            return BuiltSource(
                source=make_advmonitor_source(
                    address, tracker, radius_m, grace_seconds, rssi_at_1m, path_loss_exponent
                ),
                effective_strategy=Strategy.ADV_MONITOR.value,
                note="BlueZ advertisement monitor (controller-offloaded); degrades "
                "to adv_scan at runtime if the adapter does not support it",
            )
        return BuiltSource(
            source=BleProximitySource(address, tracker, nearby=nearby),
            effective_strategy=Strategy.ADV_SCAN.value,
            note="adv_monitor is Linux-only (BlueZ); using adv_scan",
        )

    if strategy == Strategy.GATT_LINK.value:
        if gattlink_supported():
            return BuiltSource(
                source=GattLinkSource(address, tracker),
                effective_strategy=Strategy.GATT_LINK.value,
                note="gatt-link connected RSSI",
            )
        return BuiltSource(
            source=BleProximitySource(address, tracker, nearby=nearby),
            effective_strategy=Strategy.ADV_SCAN.value,
            note="gatt_link unsupported on this platform; using adv_scan",
        )

    if strategy == Strategy.CLASSIC_LINK.value:
        backend = select_classic_backend()
        if backend is not None:
            return BuiltSource(
                source=ClassicLinkSource(address, tracker, backend),
                effective_strategy=Strategy.CLASSIC_LINK.value,
                note=f"classic-link via {backend.name}",
            )
        return BuiltSource(
            source=BleProximitySource(address, tracker, nearby=nearby),
            effective_strategy=Strategy.ADV_SCAN.value,
            note="classic-link backend unavailable on this platform; using adv_scan",
        )

    return BuiltSource(
        source=BleProximitySource(address, tracker, nearby=nearby),
        effective_strategy=Strategy.ADV_SCAN.value,
        note="advertisement scanning",
    )
