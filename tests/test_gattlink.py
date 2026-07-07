import asyncio

import pytest

from stavau.core import gattlink as gatt_mod
from stavau.core.gattlink import (
    BACKOFF_INITIAL_SECONDS,
    BASE_POLL_SECONDS,
    RELAXED_POLL_SECONDS,
    GattLinkSource,
    choose_poll_interval,
    gattlink_supported,
    read_rssi_darwin,
    read_rssi_linux,
    read_rssi_unavailable,
)


class FakeTracker:
    def __init__(self) -> None:
        self.pushed: list[tuple[float, float]] = []

    def push(self, rssi: float, now: float) -> None:
        self.pushed.append((rssi, now))


class FakeClient:
    def __init__(self, address: str, *, fail_connect: bool = False, drop_now: bool = False):
        self.address = address
        self.fail_connect = fail_connect
        self.drop_now = drop_now  # connect "succeeds" but the link is already down
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0

    @property
    def is_connected(self) -> bool:
        return self.connected

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.fail_connect:
            raise OSError("connect failed")
        self.connected = not self.drop_now

    async def disconnect(self) -> None:
        self.connected = False
        self.disconnect_calls += 1


class FakeFactory:
    """Creates FakeClients; the first `fail_first` connects fail."""

    def __init__(self, *, fail_first: int = 0, drop_now: bool = False) -> None:
        self.clients: list[FakeClient] = []
        self.fail_first = fail_first
        self.drop_now = drop_now

    def __call__(self, address: str) -> FakeClient:
        client = FakeClient(
            address,
            fail_connect=len(self.clients) < self.fail_first,
            drop_now=self.drop_now,
        )
        self.clients.append(client)
        return client


def scripted_reader(readings, *, drop_when_exhausted: bool = True):
    """Reader returning the scripted values; drops the link when they run out."""
    remaining = list(readings)

    async def reader(client, address):
        if remaining:
            return remaining.pop(0)
        if drop_when_exhausted:
            client.connected = False
        return None

    return reader


def constant_reader(value):
    async def reader(client, address):
        return value

    return reader


class SleepRecorder:
    """Injectable sleep: records delays, yields control, parks after a limit."""

    def __init__(self, park_after: int | None = None) -> None:
        self.delays: list[float] = []
        self.parked = asyncio.Event()
        self._park_after = park_after

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)
        if self._park_after is not None and len(self.delays) >= self._park_after:
            self.parked.set()
            await asyncio.Event().wait()  # park until cancelled by stop()
        await asyncio.sleep(0)


def drive_until_parked(source: GattLinkSource, recorder: SleepRecorder) -> None:
    async def run() -> None:
        await source.start()
        await asyncio.wait_for(recorder.parked.wait(), timeout=5.0)
        await source.stop()

    asyncio.run(run())


class TestChoosePollInterval:
    def test_five_strong_readings_relax_to_six_seconds(self) -> None:
        assert choose_poll_interval([-50.0] * 5) == RELAXED_POLL_SECONDS

    def test_weak_readings_stay_at_base(self) -> None:
        assert choose_poll_interval([-70.0] * 5) == BASE_POLL_SECONDS

    def test_mixed_readings_stay_at_base(self) -> None:
        assert choose_poll_interval([-50.0, -50.0, -70.0, -50.0, -50.0]) == BASE_POLL_SECONDS

    def test_fewer_than_streak_stays_at_base(self) -> None:
        assert choose_poll_interval([-50.0] * 4) == BASE_POLL_SECONDS

    def test_threshold_is_strict(self) -> None:
        # Exactly -60 dBm is not "stronger than -60": stay attentive.
        assert choose_poll_interval([-60.0] * 5) == BASE_POLL_SECONDS

    def test_only_the_last_streak_readings_count(self) -> None:
        assert choose_poll_interval([-90.0] + [-50.0] * 5) == RELAXED_POLL_SECONDS


class TestReadingsFlow:
    def test_pushes_readings_and_skips_none(self) -> None:
        tracker = FakeTracker()
        factory = FakeFactory()
        recorder = SleepRecorder(park_after=6)
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            tracker,
            client_factory=factory,
            rssi_reader=scripted_reader([-50.0, None, -55.0]),
            sleep=recorder,
        )
        drive_until_parked(source, recorder)
        assert [rssi for rssi, _ in tracker.pushed] == [-50.0, -55.0]

    def test_reader_exception_does_not_kill_loop_and_pushes_nothing(self) -> None:
        tracker = FakeTracker()
        factory = FakeFactory()
        calls = 0

        async def boom(client, address):
            nonlocal calls
            calls += 1
            raise RuntimeError("transient")

        recorder = SleepRecorder(park_after=4)
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            tracker,
            client_factory=factory,
            rssi_reader=boom,
            sleep=recorder,
        )
        drive_until_parked(source, recorder)
        assert calls >= 3  # survived the exceptions and kept polling
        assert tracker.pushed == []

    def test_adaptive_interval_relaxes_after_strong_streak(self) -> None:
        tracker = FakeTracker()
        factory = FakeFactory()
        recorder = SleepRecorder(park_after=7)
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            tracker,
            client_factory=factory,
            rssi_reader=constant_reader(-50.0),
            sleep=recorder,
        )
        drive_until_parked(source, recorder)
        # Attentive (2 s) until 5 strong readings accumulate, then relaxed (6 s).
        assert recorder.delays == [2.0, 2.0, 2.0, 2.0, 6.0, 6.0, 6.0]


class TestReconnectBackoff:
    def test_backoff_grows_and_caps_at_thirty_seconds(self) -> None:
        factory = FakeFactory(fail_first=10_000)  # connect never succeeds
        recorder = SleepRecorder(park_after=6)
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            FakeTracker(),
            client_factory=factory,
            rssi_reader=constant_reader(None),
            sleep=recorder,
        )
        drive_until_parked(source, recorder)
        # Only backoff sleeps happen while disconnected: 2/4/8/16 then capped.
        assert recorder.delays == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0]

    def test_successful_connect_resets_backoff(self) -> None:
        # Two failed connects (2 s, 4 s), then connects that drop immediately:
        # each success resets the backoff, so subsequent delays return to 2 s.
        factory = FakeFactory(fail_first=2, drop_now=True)
        recorder = SleepRecorder(park_after=5)
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            FakeTracker(),
            client_factory=factory,
            rssi_reader=constant_reader(None),
            sleep=recorder,
        )
        drive_until_parked(source, recorder)
        assert recorder.delays[:2] == [2.0, 4.0]
        assert recorder.delays[2] == 2.0  # reset after the first successful connect

    def test_nothing_pushed_while_disconnected(self) -> None:
        tracker = FakeTracker()
        factory = FakeFactory(fail_first=10_000)
        recorder = SleepRecorder(park_after=4)
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            tracker,
            client_factory=factory,
            rssi_reader=constant_reader(-40.0),  # would push if it ever ran
            sleep=recorder,
        )
        drive_until_parked(source, recorder)
        assert tracker.pushed == []


class TestStop:
    def test_stop_cancels_and_disconnects(self) -> None:
        factory = FakeFactory()
        recorder = SleepRecorder()
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            FakeTracker(),
            client_factory=factory,
            rssi_reader=constant_reader(-50.0),
            sleep=recorder,
        )

        async def run() -> None:
            await source.start()
            for _ in range(20):
                await asyncio.sleep(0)
            await source.stop()

        asyncio.run(run())
        assert source._task is None
        assert factory.clients, "should have connected at least once"
        assert factory.clients[0].disconnect_calls >= 1
        assert factory.clients[0].is_connected is False

    def test_stop_is_idempotent_and_safe_without_start(self) -> None:
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            FakeTracker(),
            client_factory=FakeFactory(),
            rssi_reader=constant_reader(None),
        )

        async def run() -> None:
            await source.stop()  # never started
            await source.start()
            await asyncio.sleep(0)
            await source.stop()
            await source.stop()  # second stop is a no-op

        asyncio.run(run())
        assert source._task is None


class TestRetarget:
    def test_retarget_switches_address_and_resets_backoff(self) -> None:
        factory = FakeFactory(fail_first=10_000)
        recorder = SleepRecorder()
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            FakeTracker(),
            client_factory=factory,
            rssi_reader=constant_reader(None),
            sleep=recorder,
        )

        async def run() -> None:
            await source.start()
            for _ in range(30):  # let the backoff grow past its initial value
                await asyncio.sleep(0)
            assert source._backoff > BACKOFF_INITIAL_SECONDS
            source.retarget("11:22:33:44:55:66")
            assert source._address == "11:22:33:44:55:66"
            assert source._backoff == BACKOFF_INITIAL_SECONDS
            await source.stop()

        asyncio.run(run())

    def test_retarget_drops_connection_and_reconnects_to_new_address(self) -> None:
        factory = FakeFactory()
        recorder = SleepRecorder()
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            FakeTracker(),
            client_factory=factory,
            rssi_reader=constant_reader(-50.0),
            sleep=recorder,
        )

        async def run() -> None:
            await source.start()
            for _ in range(20):
                await asyncio.sleep(0)
            assert factory.clients[0].is_connected
            source.retarget("11:22:33:44:55:66")
            for _ in range(300):
                if len(factory.clients) >= 2:
                    break
                await asyncio.sleep(0)
            await source.stop()

        asyncio.run(run())
        assert factory.clients[0].disconnect_calls >= 1  # old link dropped
        assert len(factory.clients) >= 2
        assert factory.clients[1].address == "11:22:33:44:55:66"

    def test_retarget_reconnects_without_backoff_delay(self) -> None:
        # Finding 17: a retarget must reconnect to the new device immediately,
        # not after the connect-failure backoff. base_poll and backoff_initial
        # are distinct so a backoff sleep is identifiable in the recorder.
        factory = FakeFactory()
        recorder = SleepRecorder()
        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            FakeTracker(),
            client_factory=factory,
            rssi_reader=constant_reader(-50.0),
            base_poll=0.5,
            relaxed_poll=0.5,
            backoff_initial=5.0,
            backoff_max=5.0,
            sleep=recorder,
        )

        async def run() -> None:
            await source.start()
            for _ in range(20):
                await asyncio.sleep(0)
            source.retarget("11:22:33:44:55:66")
            for _ in range(300):
                if len(factory.clients) >= 2:
                    break
                await asyncio.sleep(0)
            await source.stop()

        asyncio.run(run())
        assert len(factory.clients) >= 2
        # No backoff-length sleep (5.0) was applied around the retarget: the
        # only sleeps are the 0.5 s poll intervals.
        assert 5.0 not in recorder.delays


class TestGattlinkSupported:
    def test_darwin_is_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gatt_mod.sys, "platform", "darwin")
        assert gattlink_supported() is True

    def test_linux_with_hcitool_is_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gatt_mod.sys, "platform", "linux")
        monkeypatch.setattr(gatt_mod.shutil, "which", lambda name: "/usr/bin/hcitool")
        assert gattlink_supported() is True

    def test_linux_without_hcitool_is_unsupported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gatt_mod.sys, "platform", "linux")
        monkeypatch.setattr(gatt_mod.shutil, "which", lambda name: None)
        assert gattlink_supported() is False

    def test_windows_is_unsupported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gatt_mod.sys, "platform", "win32")
        assert gattlink_supported() is False


class TestDarwinReader:
    def test_reads_async_get_rssi(self) -> None:
        class Client:
            async def get_rssi(self) -> int:
                return -42

        rssi = asyncio.run(read_rssi_darwin(Client(), "AA:BB:CC:DD:EE:FF"))
        assert rssi == pytest.approx(-42.0)

    def test_reads_sync_get_rssi(self) -> None:
        class Client:
            def get_rssi(self) -> float:
                return -47.5

        rssi = asyncio.run(read_rssi_darwin(Client(), "AA:BB:CC:DD:EE:FF"))
        assert rssi == pytest.approx(-47.5)

    def test_missing_api_returns_none(self) -> None:
        class Client:
            pass

        assert asyncio.run(read_rssi_darwin(Client(), "AA:BB:CC:DD:EE:FF")) is None

    def test_raising_api_returns_none(self) -> None:
        class Client:
            async def get_rssi(self) -> int:
                raise RuntimeError("not connected")

        assert asyncio.run(read_rssi_darwin(Client(), "AA:BB:CC:DD:EE:FF")) is None

    def test_non_numeric_result_returns_none(self) -> None:
        class Client:
            async def get_rssi(self) -> object:
                return object()

        assert asyncio.run(read_rssi_darwin(Client(), "AA:BB:CC:DD:EE:FF")) is None


class TestLinuxReader:
    def test_maps_golden_range_value_to_dbm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            assert cmd[:2] == ["hcitool", "rssi"]
            return 0, "RSSI return value: 0"

        monkeypatch.setattr(gatt_mod, "_run", fake_run)
        rssi = asyncio.run(read_rssi_linux(object(), "AA:BB:CC:DD:EE:FF"))
        assert rssi == pytest.approx(-50.0)

    def test_negative_value_is_weaker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 0, "RSSI return value: -12"

        monkeypatch.setattr(gatt_mod, "_run", fake_run)
        rssi = asyncio.run(read_rssi_linux(object(), "AA:BB:CC:DD:EE:FF"))
        assert rssi == pytest.approx(-62.0)

    def test_command_failure_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 1, ""

        monkeypatch.setattr(gatt_mod, "_run", fake_run)
        assert asyncio.run(read_rssi_linux(object(), "AA:BB:CC:DD:EE:FF")) is None

    def test_unparsable_output_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 0, "unexpected"

        monkeypatch.setattr(gatt_mod, "_run", fake_run)
        assert asyncio.run(read_rssi_linux(object(), "AA:BB:CC:DD:EE:FF")) is None


class TestUnavailableReader:
    def test_always_none(self) -> None:
        assert asyncio.run(read_rssi_unavailable(object(), "AA:BB:CC:DD:EE:FF")) is None


class TestRetargetRace:
    def test_reading_in_flight_during_retarget_is_not_pushed(self) -> None:
        # Review finding: retarget() while a reader call is in flight must not
        # let the OLD device reading pollute the tracker after the generation
        # has advanced.
        tracker = FakeTracker()
        factory = FakeFactory()
        recorder = SleepRecorder(park_after=3)
        holder: dict = {}

        async def racing_reader(client, address):
            if not tracker.pushed and not holder.get("retargeted"):
                holder["retargeted"] = True
                holder["source"].retarget("11:22:33:44:55:66")  # mid-flight
                return -42.0  # OLD-device reading arriving after retarget
            return None

        source = GattLinkSource(
            "AA:BB:CC:DD:EE:FF",
            tracker,
            client_factory=factory,
            rssi_reader=racing_reader,
            sleep=recorder,
        )
        holder["source"] = source
        drive_until_parked(source, recorder)
        assert all(rssi != -42.0 for rssi, _ in tracker.pushed)
        assert tracker.pushed == []  # nothing else was scripted to push
