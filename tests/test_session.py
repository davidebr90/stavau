"""End-to-end MonitorSession tests with a fake proximity source and locker.

Time is driven by the shared `virtual_clock` fixture (see conftest.py) so a
multi-lock guardrail scenario runs instantly and deterministically. RSSI is
scripted: one sample is fed at the top of each tick.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stavau.config.settings import Settings
from stavau.core.events import EventLog
from stavau.core.session import MonitorSession, Tick
from stavau.platform.lockstate import LockStateObserver


class FakeLocker:
    name = "fake"

    def __init__(self) -> None:
        self.lock_calls = 0

    def lock(self) -> None:
        self.lock_calls += 1


class FailingLocker:
    """A locker that raises LockError on its first `fail_times` calls."""

    name = "fake"

    def __init__(self, fail_times: int = 1) -> None:
        self.lock_calls = 0
        self._fail_times = fail_times

    def lock(self) -> None:
        from stavau.platform.base import LockError

        self.lock_calls += 1
        if self.lock_calls <= self._fail_times:
            raise LockError("simulated lock failure")


class FakeSource:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


class RadioProbeStub:
    """Deterministic stand-in for radiostate.radio_available with a call counter."""

    def __init__(self, result: bool | None = None) -> None:
        self.result = result
        self.calls = 0

    async def __call__(self) -> bool | None:
        self.calls += 1
        return self.result


def run_session(
    tmp_path: Path,
    rssi_script: list[float | None],
    monkeypatch: pytest.MonkeyPatch,
    observer: LockStateObserver | None = None,
    radio_probe: RadioProbeStub | None = None,
    locker: object | None = None,
    **overrides: object,
) -> tuple[FakeLocker, list[Tick]]:
    settings = Settings(
        device_address="AA:BB:CC:DD:EE:FF",
        device_alias="test",
        radius_m=3.0,
        grace_seconds=3.0,
        return_seconds=2.0,
        # These tests exercise session/breaker logic, not smoothing (covered
        # elsewhere): a window of 1 lets scripted RSSI cross the radius promptly.
        smoothing_window=1,
        rssi_at_1m=-59.0,
        path_loss_exponent=2.0,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)

    locker = locker or FakeLocker()
    session = MonitorSession(
        settings, locker, EventLog(tmp_path / "events.jsonl"), observer=observer
    )
    session._source = FakeSource()  # type: ignore[assignment]

    # Keep the radio probe deterministic and hardware-free in every test.
    from stavau.core import session as session_mod

    monkeypatch.setattr(session_mod, "radio_available", radio_probe or RadioProbeStub(None))

    # Feed one scripted RSSI sample at the start of each tick, right before the
    # session reads the smoothed value.
    real_smoothed = session._tracker.smoothed
    cursor = [0]

    def feed_then_read(now: float) -> float | None:
        if cursor[0] < len(rssi_script):
            value = rssi_script[cursor[0]]
            cursor[0] += 1
            if value is not None:
                session._tracker.push(value, now)
        return real_smoothed(now)

    monkeypatch.setattr(session._tracker, "smoothed", feed_then_read)

    ticks: list[Tick] = []
    asyncio.run(session.run(duration=float(len(rssi_script)), on_tick=ticks.append))
    return locker, ticks


class TestSessionLocking:
    def test_locks_once_after_walk_away(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script: list[float | None] = [-50.0] * 4 + [-90.0] * 15
        locker, ticks = run_session(tmp_path, script, monkeypatch)
        assert locker.lock_calls == 1
        assert any(t.state.value == "away" for t in ticks)

    def test_no_lock_when_staying_near(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script: list[float | None] = [-50.0] * 8
        locker, ticks = run_session(tmp_path, script, monkeypatch)
        assert locker.lock_calls == 0

    def test_signal_loss_locks_fail_safe(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Near, then the device vanishes (None) long enough to go stale -> lock.
        script: list[float | None] = [-50.0, -50.0] + [None] * 20
        locker, ticks = run_session(tmp_path, script, monkeypatch)
        assert locker.lock_calls == 1


class TestRadioOff:
    def _events(self, tmp_path: Path) -> list[str]:
        return [r.event for r in EventLog(tmp_path / "events.jsonl").tail(200)]

    def test_radio_off_false_while_signal_present(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Even a probe that would say "off" is irrelevant while rssi flows.
        probe = RadioProbeStub(False)
        _locker, ticks = run_session(tmp_path, [-50.0] * 8, monkeypatch, radio_probe=probe)
        assert all(not t.radio_off for t in ticks)
        assert probe.calls == 0  # never probed while signal is healthy

    def test_radio_off_true_after_stale_and_probe_false(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script: list[float | None] = [-50.0, -50.0] + [None] * 20
        _locker, ticks = run_session(
            tmp_path, script, monkeypatch, radio_probe=RadioProbeStub(False)
        )
        assert any(t.radio_off for t in ticks)
        events = self._events(tmp_path)
        assert events.count("radio_off") == 1  # single transition log, no spam
        assert "radio_on" not in events

    def test_radio_recovery_logs_radio_on_once(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        script: list[float | None] = [-50.0] * 2 + [None] * 18 + [-50.0] * 4
        _locker, _ticks = run_session(
            tmp_path, script, monkeypatch, radio_probe=RadioProbeStub(False)
        )
        events = self._events(tmp_path)
        assert events.count("radio_off") == 1
        assert events.count("radio_on") == 1

    def test_probe_is_throttled_while_stale(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probe = RadioProbeStub(False)
        # The tracker goes stale 15 virtual seconds after the last sample, so a
        # long None tail yields ~15 stale ticks; unthrottled, that would be ~15
        # probe calls, throttled (first stale tick, then every 6th) it is ~3.
        script: list[float | None] = [-50.0] + [None] * 30
        run_session(tmp_path, script, monkeypatch, radio_probe=probe)
        assert 2 <= probe.calls <= 4

    def test_radio_off_never_prevents_the_fail_safe_lock(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # I1: radio-off is explanation only — the staleness lock still fires.
        script: list[float | None] = [-50.0, -50.0] + [None] * 20
        locker, _ticks = run_session(
            tmp_path, script, monkeypatch, radio_probe=RadioProbeStub(False)
        )
        assert locker.lock_calls == 1


class _Screen:
    """A shared lock flag linking the fake locker, observer and unlocker."""

    def __init__(self, locked: bool = False) -> None:
        self.locked = locked


class _LinkedLocker:
    name = "fake"

    def __init__(self, screen: _Screen) -> None:
        self._screen = screen
        self.lock_calls = 0

    def lock(self) -> None:
        self.lock_calls += 1
        self._screen.locked = True


class _LinkedObserver:
    name = "fake-observer"

    def __init__(self, screen: _Screen) -> None:
        self._screen = screen

    def current(self) -> bool | None:
        return self._screen.locked

    def subscribe(self, cb) -> None:  # noqa: ANN001
        pass

    def close(self) -> None:
        pass


class _LinkedUnlocker:
    name = "fake-unlocker"

    def __init__(self, screen: _Screen) -> None:
        self._screen = screen
        self.calls = 0

    def unlock(self) -> None:
        self.calls += 1
        self._screen.locked = False


def _drive_autounlock(
    tmp_path: Path,
    rssi_script: list[float | None],
    monkeypatch: pytest.MonkeyPatch,
    screen: _Screen,
) -> tuple[_LinkedUnlocker, EventLog]:
    from stavau.core import session as session_mod

    settings = Settings(
        device_address="AA:BB:CC:DD:EE:FF",
        device_alias="test",
        radius_m=3.0,
        grace_seconds=3.0,
        return_seconds=2.0,
        smoothing_window=1,
        rssi_at_1m=-59.0,
        path_loss_exponent=2.0,
        association="paired",
        auto_unlock=True,
        auto_unlock_ack=True,
        auto_unlock_strict_ratio=0.5,
        auto_unlock_dwell_seconds=1.0,
    )
    unlocker = _LinkedUnlocker(screen)
    monkeypatch.setattr(session_mod, "get_unlocker", lambda: unlocker)
    monkeypatch.setattr(session_mod, "radio_available", RadioProbeStub(None))

    log = EventLog(tmp_path / "events.jsonl")
    session = MonitorSession(settings, _LinkedLocker(screen), log, observer=_LinkedObserver(screen))
    session._source = FakeSource()  # type: ignore[assignment]

    real_smoothed = session._tracker.smoothed
    cursor = [0]

    def feed_then_read(now: float) -> float | None:
        if cursor[0] < len(rssi_script):
            value = rssi_script[cursor[0]]
            cursor[0] += 1
            if value is not None:
                session._tracker.push(value, now)
        return real_smoothed(now)

    monkeypatch.setattr(session._tracker, "smoothed", feed_then_read)
    asyncio.run(session.run(duration=float(len(rssi_script))))
    return unlocker, log


class TestAutoUnlock:
    def test_unlocks_after_stavau_lock_on_return(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Near, walk away (stavau locks), then return close: auto-unlock fires.
        script: list[float | None] = [-50.0] * 3 + [-90.0] * 8 + [-50.0] * 6
        screen = _Screen(locked=False)
        unlocker, log = _drive_autounlock(tmp_path, script, monkeypatch, screen)
        assert unlocker.calls >= 1
        events = [r.event for r in log.tail(200)]
        assert "auto_unlock_triggered" in events

    def test_never_unlocks_a_manual_lock(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The screen is locked by the user (foreign) and the device is right
        # here the whole time. Auto-unlock must NEVER fire (threat-model T9).
        script: list[float | None] = [-50.0] * 12
        screen = _Screen(locked=True)  # pre-locked, not by stavau
        unlocker, log = _drive_autounlock(tmp_path, script, monkeypatch, screen)
        assert unlocker.calls == 0
        assert "auto_unlock_triggered" not in [r.event for r in log.tail(200)]


class TestGuardrail:
    def test_breaker_caps_locks_and_pauses(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Oscillate near/far repeatedly. Without a breaker this locks many times;
        # with max_locks=3 it must stop at 3 and then report a paused state.
        script: list[float | None] = []
        for _ in range(6):
            script += [-90.0] * 7  # far -> lock after grace
            script += [-50.0] * 5  # back near -> re-arm
        locker, ticks = run_session(
            tmp_path,
            script,
            monkeypatch,
            breaker_max_locks=3,
            breaker_window_seconds=10000.0,
            breaker_cooldown_seconds=10000.0,
        )
        assert locker.lock_calls == 3
        assert any(t.breaker_paused for t in ticks)

    def test_breaker_resumes_after_cooldown(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Short cooldown: after pausing, locking must become possible again.
        script: list[float | None] = []
        for _ in range(10):
            script += [-90.0] * 7
            script += [-50.0] * 5
        locker, ticks = run_session(
            tmp_path,
            script,
            monkeypatch,
            breaker_max_locks=3,
            breaker_window_seconds=10000.0,
            breaker_cooldown_seconds=5.0,  # resumes within the scripted horizon
        )
        # Tripped at 3, paused, then cooldown elapses and more locks can occur.
        assert locker.lock_calls > 3
        assert any(t.breaker_paused for t in ticks)
        assert any(not t.breaker_paused for t in ticks[-5:])


class TestStrategyDegraded:
    def test_degraded_strategy_is_logged(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Finding 6: external_presence with no MQTT host falls back to adv_scan;
        # that silent downgrade must be surfaced in the event log.
        run_session(
            tmp_path,
            [-50.0] * 2,
            monkeypatch,
            strategy="external_presence",
        )
        events = [r.event for r in EventLog(tmp_path / "events.jsonl").tail(200)]
        assert "strategy_degraded" in events

    def test_native_strategy_is_not_logged_as_degraded(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        run_session(tmp_path, [-50.0] * 2, monkeypatch)  # default adv_scan
        events = [r.event for r in EventLog(tmp_path / "events.jsonl").tail(200)]
        assert "strategy_degraded" not in events


class TestRetryGating:
    def test_failed_lock_does_not_relock_a_returning_user(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A lock fails (retry pending). The user then RETURNS (within the return
        # threshold) but the return dwell hasn't completed. The stale 5 s retry
        # must NOT fire and lock the user back out while they are coming back.
        script: list[float | None] = [-50.0] * 3 + [-90.0] * 7 + [-50.0] * 15
        failing = FailingLocker(fail_times=1)
        locker, ticks = run_session(
            tmp_path,
            script,
            monkeypatch,
            locker=failing,
            return_seconds=20.0,  # keep the machine in RETURNING for the window
        )
        # Exactly one (failed) attempt: no retry-lock while returning.
        assert failing.lock_calls == 1
        assert any(t.state.value == "returning" for t in ticks)

    def test_breaker_paused_at_away_still_locks_after_cooldown(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Trip the breaker, then a fresh walk-away happens WHILE paused (the lock
        # is suppressed) and the user stays away. After the cooldown ends the
        # owed lock must still fire — the departure must not be silently forgotten.
        script: list[float | None] = []
        for _ in range(3):  # three quick cycles trip the breaker
            script += [-90.0] * 7
            script += [-50.0] * 5
        script += [-50.0] * 5  # re-arm near
        script += [-90.0] * 45  # leave while paused, then stay away past cooldown
        locker, ticks = run_session(
            tmp_path,
            script,
            monkeypatch,
            breaker_max_locks=3,
            breaker_window_seconds=10000.0,
            breaker_cooldown_seconds=25.0,
        )
        assert any(t.breaker_paused for t in ticks)
        # 3 while tripping + at least one more after the cooldown while still away.
        assert locker.lock_calls >= 4
