"""End-to-end MonitorSession tests with a fake proximity source and locker.

Time is driven by monkeypatching the session's sleep and monotonic clock so a
multi-lock guardrail scenario runs instantly and deterministically. RSSI is
scripted: one sample is fed at the top of each tick.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from stavau.config.settings import Settings
from stavau.core import session as session_mod
from stavau.core.events import EventLog
from stavau.core.session import MonitorSession, Tick


class FakeLocker:
    name = "fake"

    def __init__(self) -> None:
        self.lock_calls = 0

    def lock(self) -> None:
        self.lock_calls += 1


class FakeSource:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True


@pytest.fixture
def virtual_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = [0.0]

    async def fake_sleep(_seconds: float) -> None:
        holder[0] += 1.0

    monkeypatch.setattr(session_mod.time, "monotonic", lambda: holder[0])
    monkeypatch.setattr(session_mod, "_sleep", fake_sleep)


def run_session(
    tmp_path: Path,
    rssi_script: list[float | None],
    monkeypatch: pytest.MonkeyPatch,
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

    locker = FakeLocker()
    session = MonitorSession(settings, locker, EventLog(tmp_path / "events.jsonl"))
    session._source = FakeSource()  # type: ignore[assignment]

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
