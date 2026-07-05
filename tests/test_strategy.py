import pytest

from stavau.core import strategy as strategy_mod
from stavau.core.classic import ClassicLinkSource
from stavau.core.deviceid import Strategy
from stavau.core.monitor import BleProximitySource, RssiTracker
from stavau.core.strategy import build_source


class FakeBackend:
    name = "fake-backend"

    async def read_rssi(self, address: str) -> float | None:
        return -45.0


class TestBuildSource:
    def test_adv_scan_builds_ble_source(self) -> None:
        built = build_source("adv_scan", "AA:BB:CC:DD:EE:FF", RssiTracker(smoothing_window=4))
        assert isinstance(built.source, BleProximitySource)
        assert built.effective_strategy == Strategy.ADV_SCAN.value

    def test_classic_link_builds_classic_source_when_backend_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(strategy_mod, "select_classic_backend", lambda: FakeBackend())
        built = build_source("classic_link", "AA:BB:CC:DD:EE:FF", RssiTracker(smoothing_window=4))
        assert isinstance(built.source, ClassicLinkSource)
        assert built.effective_strategy == Strategy.CLASSIC_LINK.value
        assert "fake-backend" in built.note

    def test_classic_link_falls_back_to_adv_scan_without_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(strategy_mod, "select_classic_backend", lambda: None)
        built = build_source("classic_link", "AA:BB:CC:DD:EE:FF", RssiTracker(smoothing_window=4))
        assert isinstance(built.source, BleProximitySource)
        assert built.effective_strategy == Strategy.ADV_SCAN.value
        assert "unavailable" in built.note

    def test_unknown_strategy_defaults_to_adv_scan(self) -> None:
        built = build_source("nonsense", "AA:BB:CC:DD:EE:FF", RssiTracker(smoothing_window=4))
        assert isinstance(built.source, BleProximitySource)
        assert built.effective_strategy == Strategy.ADV_SCAN.value
