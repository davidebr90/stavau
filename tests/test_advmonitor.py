"""Tests for the BlueZ advertisement-monitor proximity source.

Runs on any OS: no real D-Bus is touched. The bus backend is injected via
``bus_factory`` and the degradation path via ``fallback_factory``, so a fake
bus stands in for BlueZ and a fake source stands in for BleProximitySource.
Mirrors the asyncio.run(...)-driven style of test_lockstate_linux.py.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from stavau.core.advmonitor import (
    RSSI_MAX_DBM,
    RSSI_MIN_DBM,
    AdvMonitorSource,
    MonitorSpec,
    advmonitor_supported,
    make_source,
    thresholds_from_settings,
)
from stavau.core.distance import CalibrationModel
from stavau.core.monitor import RssiTracker

TARGET = "AA:BB:CC:DD:EE:FF"
OTHER = "11:22:33:44:55:66"
TARGET_PATH = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
OTHER_PATH = "/org/bluez/hci0/dev_11_22_33_44_55_66"


class FakeTracker:
    """Records every push; duck-types RssiTracker for the source."""

    def __init__(self) -> None:
        self.pushes: list[tuple[float, float]] = []

    def push(self, rssi: float, now: float) -> None:
        self.pushes.append((rssi, now))


class FakeBus:
    """Fake bus backend: captures the registered spec, lets tests fire events."""

    def __init__(
        self,
        types: list[str] | None = None,
        addresses: dict[str, str] | None = None,
        fail_register: bool = False,
        fail_types: bool = False,
    ) -> None:
        self.types = ["or_patterns"] if types is None else types
        self.addresses = addresses if addresses is not None else {TARGET_PATH: TARGET}
        self.fail_register = fail_register
        self.fail_types = fail_types
        self.spec: MonitorSpec | None = None
        self.unregistered = False
        self.disconnected = False

    async def supported_monitor_types(self) -> list[str]:
        if self.fail_types:
            raise RuntimeError("bus exploded")
        return self.types

    async def register_monitor(self, spec: MonitorSpec) -> None:
        if self.fail_register:
            raise RuntimeError("RegisterMonitor failed")
        self.spec = spec

    async def unregister_monitor(self) -> None:
        self.unregistered = True

    async def device_address(self, device_path: str) -> str | None:
        return self.addresses.get(device_path)

    async def disconnect(self) -> None:
        self.disconnected = True


class FakeFallback:
    """Stand-in for the internal BleProximitySource fallback."""

    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.retargets: list[str] = []

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    def retarget(self, address: str) -> None:
        self.retargets.append(address)


def make_advmonitor_source(
    bus: FakeBus,
    tracker: FakeTracker,
    fallback: FakeFallback | None = None,
    address: str = TARGET,
) -> AdvMonitorSource:
    kwargs = {}
    if fallback is not None:
        kwargs["fallback_factory"] = lambda _address, _tracker: fallback
    return AdvMonitorSource(
        address,
        tracker,  # type: ignore[arg-type]  # duck-typed recorder
        high_rssi=-65,
        low_rssi=-72,
        high_timer_s=2,
        low_timer_s=5,
        bus_factory=lambda: bus,
        keepalive_interval_s=0.01,
        **kwargs,
    )


async def _settle(rounds: int = 5) -> None:
    for _ in range(rounds):
        await asyncio.sleep(0)


class TestRssiAtInverse:
    """CalibrationModel.rssi_at (added for CARD-C1) round-trips distance_m."""

    def test_rssi_at_one_metre_is_rssi_at_1m(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        assert model.rssi_at(1.0) == pytest.approx(-59.0)

    def test_known_value_at_ten_metres(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        # -59 - 20 * log10(10) = -79
        assert model.rssi_at(10.0) == pytest.approx(-79.0)

    def test_round_trips_with_distance_m(self) -> None:
        model = CalibrationModel(rssi_at_1m=-62.0, path_loss_exponent=2.7)
        for distance in (0.5, 1.0, 2.0, 3.0, 5.0, 8.0):
            assert model.distance_m(model.rssi_at(distance)) == pytest.approx(distance)

    def test_round_trips_the_other_way(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        for rssi in (-45.0, -59.0, -70.0, -85.0):
            assert model.rssi_at(model.distance_m(rssi)) == pytest.approx(rssi)

    def test_monotonic_decreasing_with_distance(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        assert model.rssi_at(1.0) > model.rssi_at(2.0) > model.rssi_at(5.0)

    def test_zero_or_negative_distance_raises(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        with pytest.raises(ValueError):
            model.rssi_at(0.0)
        with pytest.raises(ValueError):
            model.rssi_at(-1.0)


class TestThresholdsFromSettings:
    def test_default_calibration_radius_three(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        high, low, high_timer, low_timer = thresholds_from_settings(3.0, 10.0, model)
        # high: rssi at 2.4 m = -59 - 20*log10(2.4) = -66.6... -> -67
        # low:  rssi at 3.0 m = -59 - 20*log10(3.0) = -68.5... -> -69
        assert high == round(-59.0 - 20.0 * math.log10(2.4)) == -67
        assert low == round(-59.0 - 20.0 * math.log10(3.0)) == -69
        assert high_timer == 2
        assert low_timer == 5

    def test_high_is_nearer_therefore_stronger_than_low(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        high, low, _high_timer, _low_timer = thresholds_from_settings(5.0, 10.0, model)
        assert high > low

    def test_low_timer_is_ceil_of_half_grace(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        assert thresholds_from_settings(3.0, 7.0, model)[3] == 4  # ceil(3.5)
        assert thresholds_from_settings(3.0, 3.0, model)[3] == 2  # ceil(1.5)
        assert thresholds_from_settings(3.0, 20.0, model)[3] == 10

    def test_clamps_to_lower_dbm_bound(self) -> None:
        # Weak transmitter + steep path loss + far radius -> below -100 dBm.
        model = CalibrationModel(rssi_at_1m=-90.0, path_loss_exponent=4.0)
        high, low, _high_timer, _low_timer = thresholds_from_settings(10.0, 10.0, model)
        assert high == RSSI_MIN_DBM
        assert low == RSSI_MIN_DBM

    def test_clamps_to_upper_dbm_bound(self) -> None:
        # Implausibly strong transmitter at a tiny radius -> above -20 dBm.
        model = CalibrationModel(rssi_at_1m=-10.0, path_loss_exponent=2.0)
        high, low, _high_timer, _low_timer = thresholds_from_settings(1.0, 10.0, model)
        assert high == RSSI_MAX_DBM
        assert low == RSSI_MAX_DBM

    def test_clamping_never_inverts_ordering(self) -> None:
        model = CalibrationModel(rssi_at_1m=-95.0, path_loss_exponent=3.5)
        high, low, _high_timer, _low_timer = thresholds_from_settings(8.0, 10.0, model)
        assert high >= low

    def test_thresholds_are_ints(self) -> None:
        model = CalibrationModel(rssi_at_1m=-59.0, path_loss_exponent=2.0)
        assert all(isinstance(v, int) for v in thresholds_from_settings(3.0, 10.0, model))


class TestMakeSource:
    def test_builds_source_with_mapped_thresholds(self) -> None:
        tracker = RssiTracker(smoothing_window=4)
        source = make_source(
            TARGET,
            tracker,
            radius_m=3.0,
            grace_seconds=10.0,
            rssi_at_1m=-59.0,
            path_loss_exponent=2.0,
        )
        assert isinstance(source, AdvMonitorSource)
        assert source.high_rssi == -67
        assert source.low_rssi == -69
        assert source.high_timer_s == 2
        assert source.low_timer_s == 5


class TestRegistration:
    def test_start_registers_spec_with_thresholds(self) -> None:
        bus = FakeBus()
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)

        async def drive() -> None:
            await source.start()
            await source.stop()

        asyncio.run(drive())
        assert bus.spec is not None
        assert bus.spec.high_rssi == -65
        assert bus.spec.low_rssi == -72
        assert bus.spec.high_timer_s == 2
        assert bus.spec.low_timer_s == 5
        assert len(bus.spec.patterns) > 0
        assert not source.fallback_active

    def test_stop_unregisters_and_disconnects(self) -> None:
        bus = FakeBus()
        source = make_advmonitor_source(bus, FakeTracker())

        async def drive() -> None:
            await source.start()
            await source.stop()

        asyncio.run(drive())
        assert bus.unregistered
        assert bus.disconnected

    def test_stop_is_idempotent(self) -> None:
        bus = FakeBus()
        source = make_advmonitor_source(bus, FakeTracker())

        async def drive() -> None:
            await source.start()
            await source.stop()
            await source.stop()

        asyncio.run(drive())


class TestDeviceFoundLost:
    def test_found_target_pushes_high_rssi(self) -> None:
        bus = FakeBus()
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            await source.stop()

        asyncio.run(drive())
        assert len(tracker.pushes) >= 1
        assert all(rssi == -65.0 for rssi, _now in tracker.pushes)

    def test_keepalive_keeps_pushing_while_found(self) -> None:
        bus = FakeBus()
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            await asyncio.sleep(0.06)  # several 0.01 s keepalive ticks
            await source.stop()

        asyncio.run(drive())
        assert len(tracker.pushes) >= 3

    def test_lost_stops_pushing(self) -> None:
        bus = FakeBus()
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)
        counts: list[int] = []

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            await asyncio.sleep(0.03)
            bus.spec.on_device_lost(TARGET_PATH)
            await _settle()
            counts.append(len(tracker.pushes))
            await asyncio.sleep(0.05)  # no more keepalive ticks may land
            counts.append(len(tracker.pushes))
            await source.stop()

        asyncio.run(drive())
        assert counts[0] == counts[1]

    def test_found_other_device_pushes_nothing(self) -> None:
        bus = FakeBus(addresses={TARGET_PATH: TARGET, OTHER_PATH: OTHER})
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(OTHER_PATH)
            await _settle()
            await asyncio.sleep(0.03)
            await source.stop()

        asyncio.run(drive())
        assert tracker.pushes == []

    def test_lost_for_other_device_keeps_target_tracking(self) -> None:
        bus = FakeBus(addresses={TARGET_PATH: TARGET, OTHER_PATH: OTHER})
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)
        counts: list[int] = []

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            bus.spec.on_device_lost(OTHER_PATH)  # not our device
            counts.append(len(tracker.pushes))
            await asyncio.sleep(0.03)  # keepalive must still be running
            counts.append(len(tracker.pushes))
            await source.stop()

        asyncio.run(drive())
        assert counts[1] > counts[0]

    def test_release_stops_pushing_fail_safe(self) -> None:
        bus = FakeBus()
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)
        counts: list[int] = []

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            bus.spec.on_release()
            await _settle()
            counts.append(len(tracker.pushes))
            await asyncio.sleep(0.05)
            counts.append(len(tracker.pushes))
            await source.stop()

        asyncio.run(drive())
        assert counts[0] == counts[1]

    def test_unresolvable_device_path_pushes_nothing(self) -> None:
        bus = FakeBus(addresses={})
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            await source.stop()

        asyncio.run(drive())
        assert tracker.pushes == []


class TestRetarget:
    def test_retarget_resets_matching_and_stops_keepalive(self) -> None:
        bus = FakeBus(addresses={TARGET_PATH: TARGET, OTHER_PATH: OTHER})
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker)
        counts: list[int] = []

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            source.retarget(OTHER)
            await _settle()
            counts.append(len(tracker.pushes))
            await asyncio.sleep(0.05)  # old keepalive must be dead
            counts.append(len(tracker.pushes))
            # Old target found again: no longer a match.
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            counts.append(len(tracker.pushes))
            # New target found: matches.
            bus.spec.on_device_found(OTHER_PATH)
            await _settle()
            counts.append(len(tracker.pushes))
            await source.stop()

        asyncio.run(drive())
        assert counts[0] == counts[1] == counts[2]
        assert counts[3] > counts[2]

    def test_retarget_is_case_insensitive(self) -> None:
        bus = FakeBus()
        tracker = FakeTracker()
        source = make_advmonitor_source(bus, tracker, address=OTHER)

        async def drive() -> None:
            await source.start()
            source.retarget(TARGET.lower())
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await _settle()
            await source.stop()

        asyncio.run(drive())
        assert len(tracker.pushes) >= 1

    def test_retarget_forwards_to_fallback(self) -> None:
        bus = FakeBus(types=[])  # unsupported -> fallback engages
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, FakeTracker(), fallback=fallback)

        async def drive() -> None:
            await source.start()
            source.retarget(OTHER)
            await source.stop()

        asyncio.run(drive())
        assert fallback.retargets == [OTHER]


class TestFallback:
    def test_unsupported_types_starts_fallback(self) -> None:
        bus = FakeBus(types=[])
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, FakeTracker(), fallback=fallback)

        async def drive() -> None:
            await source.start()

        asyncio.run(drive())
        assert fallback.started
        assert source.fallback_active
        assert bus.disconnected  # probe bus not leaked
        assert "fallback" in source.note

    def test_wrong_monitor_type_starts_fallback(self) -> None:
        bus = FakeBus(types=["and_patterns"])
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, FakeTracker(), fallback=fallback)
        asyncio.run(source.start())
        assert fallback.started

    def test_bus_factory_raising_starts_fallback(self) -> None:
        fallback = FakeFallback()

        def broken_factory() -> FakeBus:
            raise RuntimeError("no system bus on this machine")

        source = AdvMonitorSource(
            TARGET,
            RssiTracker(smoothing_window=4),
            high_rssi=-65,
            low_rssi=-72,
            high_timer_s=2,
            low_timer_s=5,
            bus_factory=broken_factory,
            fallback_factory=lambda _address, _tracker: fallback,
        )
        asyncio.run(source.start())
        assert fallback.started
        assert source.fallback_active

    def test_register_failure_starts_fallback(self) -> None:
        bus = FakeBus(fail_register=True)
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, FakeTracker(), fallback=fallback)
        asyncio.run(source.start())
        assert fallback.started
        assert bus.disconnected

    def test_probe_failure_starts_fallback(self) -> None:
        bus = FakeBus(fail_types=True)
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, FakeTracker(), fallback=fallback)
        asyncio.run(source.start())
        assert fallback.started

    def test_stop_stops_fallback(self) -> None:
        bus = FakeBus(types=[])
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, FakeTracker(), fallback=fallback)

        async def drive() -> None:
            await source.start()
            await source.stop()

        asyncio.run(drive())
        assert fallback.stopped

    def test_successful_registration_does_not_start_fallback(self) -> None:
        bus = FakeBus()
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, FakeTracker(), fallback=fallback)

        async def drive() -> None:
            await source.start()
            await source.stop()

        asyncio.run(drive())
        assert not fallback.started


class TestAdvmonitorSupported:
    def test_true_when_types_non_empty(self) -> None:
        bus = FakeBus(types=["or_patterns"])
        assert asyncio.run(advmonitor_supported(lambda: bus)) is True
        assert bus.disconnected

    def test_false_when_types_empty(self) -> None:
        bus = FakeBus(types=[])
        assert asyncio.run(advmonitor_supported(lambda: bus)) is False
        assert bus.disconnected

    def test_false_when_factory_raises(self) -> None:
        def broken_factory() -> FakeBus:
            raise RuntimeError("no bus")

        assert asyncio.run(advmonitor_supported(broken_factory)) is False

    def test_false_when_probe_raises(self) -> None:
        bus = FakeBus(fail_types=True)
        assert asyncio.run(advmonitor_supported(lambda: bus)) is False
        assert bus.disconnected


class DyingBus(FakeBus):
    """FakeBus with a controllable liveness flag (review regression)."""

    def __init__(self) -> None:
        super().__init__()
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive


class TestBusDeathFailSafe:
    def test_bus_death_stops_keepalive_and_starts_fallback(self) -> None:
        # Review finding: without a liveness anchor, a dead BlueZ/adapter that
        # never delivers Release would keep synthesizing presence forever
        # (fail-open). The keepalive must stop pushing and hand off.
        bus = DyingBus()
        tracker = FakeTracker()
        fallback = FakeFallback()
        source = make_advmonitor_source(bus, tracker, fallback=fallback)

        async def drive() -> None:
            await source.start()
            assert bus.spec is not None
            bus.spec.on_device_found(TARGET_PATH)
            await asyncio.sleep(0.1)  # keepalive beating at 0.01 s
            pushes_alive = len(tracker.pushes)
            assert pushes_alive > 0
            bus.alive = False
            await asyncio.sleep(0.2)  # > 5 beats: liveness check must trip
            pushes_at_death = len(tracker.pushes)
            await asyncio.sleep(0.2)
            assert len(tracker.pushes) == pushes_at_death  # no further pushes
            assert fallback.started  # handed off to scanning fallback
            await source.stop()

        asyncio.run(drive())
