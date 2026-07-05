import asyncio

import pytest

from stavau.core import classic as classic_mod
from stavau.core.classic import (
    PRESENT_RSSI_DBM,
    ClassicLinkSource,
    HcitoolClassicBackend,
    WinRtConnectionBackend,
)


class FakeTracker:
    def __init__(self) -> None:
        self.pushed: list[tuple[float, float]] = []

    def push(self, rssi: float, now: float) -> None:
        self.pushed.append((rssi, now))


class ScriptedBackend:
    name = "scripted"

    def __init__(self, readings: list[float | None]) -> None:
        self._readings = readings
        self._i = 0

    async def read_rssi(self, address: str) -> float | None:
        if self._i < len(self._readings):
            value = self._readings[self._i]
            self._i += 1
            return value
        return None


class TestClassicLinkSource:
    def test_pushes_readings_into_tracker(self) -> None:
        tracker = FakeTracker()
        backend = ScriptedBackend([-45.0, -50.0, None, -47.0])
        source = ClassicLinkSource("AA:BB:CC:DD:EE:FF", tracker, backend, poll_interval=0.0)

        async def drive() -> None:
            await source.start()
            await asyncio.sleep(0.05)
            await source.stop()

        asyncio.run(drive())
        # None readings are skipped; real ones are pushed.
        assert any(r == -45.0 for r, _ in tracker.pushed)
        assert all(r is not None for r, _ in tracker.pushed)

    def test_backend_exception_does_not_kill_loop(self) -> None:
        tracker = FakeTracker()

        class Boom:
            name = "boom"

            def __init__(self) -> None:
                self.calls = 0

            async def read_rssi(self, address: str) -> float | None:
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("transient")
                return -45.0

        backend = Boom()
        source = ClassicLinkSource("AA:BB:CC:DD:EE:FF", tracker, backend, poll_interval=0.0)

        async def drive() -> None:
            await source.start()
            await asyncio.sleep(0.05)
            await source.stop()

        asyncio.run(drive())
        assert backend.calls >= 2  # survived the first exception and kept polling

    def test_retarget_changes_address(self) -> None:
        source = ClassicLinkSource("AA:BB:CC:DD:EE:FF", FakeTracker(), ScriptedBackend([]))
        source.retarget("11:22:33:44:55:66")
        assert source._address == "11:22:33:44:55:66"


class TestHcitoolBackend:
    def test_maps_golden_range_value_to_dbm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # l2ping ok, hcitool reports RSSI 0 (golden range) -> golden mid dBm.
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            if cmd[0] == "l2ping":
                return 0, "1 received"
            return 0, "RSSI return value: 0"

        monkeypatch.setattr(classic_mod, "_run", fake_run)
        backend = HcitoolClassicBackend()
        rssi = asyncio.run(backend.read_rssi("AA:BB:CC:DD:EE:FF"))
        assert rssi == pytest.approx(-50.0)

    def test_negative_rssi_is_weaker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            if cmd[0] == "l2ping":
                return 0, ""
            return 0, "RSSI return value: -12"

        monkeypatch.setattr(classic_mod, "_run", fake_run)
        rssi = asyncio.run(HcitoolClassicBackend().read_rssi("AA:BB:CC:DD:EE:FF"))
        assert rssi == pytest.approx(-62.0)

    def test_unreachable_device_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 1, ""  # l2ping fails -> not reachable

        monkeypatch.setattr(classic_mod, "_run", fake_run)
        rssi = asyncio.run(HcitoolClassicBackend().read_rssi("AA:BB:CC:DD:EE:FF"))
        assert rssi is None

    def test_reachable_but_rssi_unreadable_uses_golden_mid(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            if cmd[0] == "l2ping":
                return 0, ""
            return 1, ""  # hcitool failed

        monkeypatch.setattr(classic_mod, "_run", fake_run)
        rssi = asyncio.run(HcitoolClassicBackend().read_rssi("AA:BB:CC:DD:EE:FF"))
        assert rssi == pytest.approx(-50.0)


class TestBackendSelection:
    def test_present_rssi_maps_to_near(self) -> None:
        # Sanity: the synthesized "present" RSSI is a strong (near) value.
        assert PRESENT_RSSI_DBM > -60.0

    def test_winrt_backend_unavailable_off_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(classic_mod.sys, "platform", "linux")
        assert WinRtConnectionBackend.available() is False
