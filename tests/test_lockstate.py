"""Lock-state observer contract and its wiring into MonitorSession.

Reuses the scripted-session harness from test_session (virtual clock, scripted
RSSI, fake locker) and adds a scripted fake observer. The critical invariants
under test: an already-locked screen suppresses the redundant lock action
without touching the circuit breaker, while an unknown state (None or observer
error) must NEVER suppress locking (I1).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from stavau.core.events import EventLog
from stavau.platform.lockstate import get_lock_state_observer
from test_session import run_session

# One walk-away: near long enough to arm, then far past the grace period.
WALK_AWAY: list[float | None] = [-50.0] * 4 + [-90.0] * 15
# Stay near for the whole run: no lock is ever requested.
STAY_NEAR: list[float | None] = [-50.0] * 8


class FakeLockObserver:
    """Scripted observer: current() consumes the script, then repeats the last value."""

    name = "fake-lockstate"

    def __init__(self, script: list[bool | None]) -> None:
        self._script = list(script)
        self.subscribed: list[Callable[[bool], None]] = []
        self.closed = False

    def current(self) -> bool | None:
        if not self._script:
            return None
        if len(self._script) > 1:
            return self._script.pop(0)
        return self._script[0]

    def subscribe(self, cb: Callable[[bool], None]) -> None:
        self.subscribed.append(cb)

    def close(self) -> None:
        self.closed = True


class BrokenLockObserver:
    """Observer whose current() always raises, exercising the error path."""

    name = "broken-lockstate"

    def current(self) -> bool | None:
        raise RuntimeError("backend exploded")

    def subscribe(self, cb: Callable[[bool], None]) -> None:  # pragma: no cover
        pass

    def close(self) -> None:
        pass


def logged_events(tmp_path: Path) -> list[str]:
    return [record.event for record in EventLog(tmp_path / "events.jsonl").tail(200)]


class TestFactory:
    @pytest.mark.parametrize("platform", ["linux", "win32", "darwin", "plan9"])
    def test_no_backend_yet_on_any_platform(
        self, monkeypatch: pytest.MonkeyPatch, platform: str
    ) -> None:
        # Backends land in follow-up cards; until then every platform degrades
        # to "no observer" rather than raising (observing is optional).
        monkeypatch.setattr(sys, "platform", platform)
        assert get_lock_state_observer() is None


class TestAlreadyLockedSuppression:
    def test_skips_locker_and_logs_event(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observer = FakeLockObserver([True])
        locker, _ticks = run_session(tmp_path, WALK_AWAY, monkeypatch, observer=observer)
        assert locker.lock_calls == 0
        assert "lock_skipped_already_locked" in logged_events(tmp_path)

    def test_skip_does_not_register_on_breaker(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # With max_locks=1 a single *registered* lock trips the breaker, so a
        # skip that leaked into the breaker would show up as breaker_tripped.
        observer = FakeLockObserver([True])
        locker, ticks = run_session(
            tmp_path,
            WALK_AWAY,
            monkeypatch,
            observer=observer,
            breaker_max_locks=1,
            breaker_window_seconds=10000.0,
            breaker_cooldown_seconds=10000.0,
        )
        assert locker.lock_calls == 0
        events = logged_events(tmp_path)
        assert "lock_skipped_already_locked" in events
        assert "breaker_tripped" not in events
        assert not any(t.breaker_paused for t in ticks)


class TestUnknownStatePassthrough:
    def test_none_state_locks_exactly_as_today(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # I1: unknown state must never disable locking.
        observer = FakeLockObserver([None])
        locker, _ticks = run_session(tmp_path, WALK_AWAY, monkeypatch, observer=observer)
        assert locker.lock_calls == 1
        assert "lock_skipped_already_locked" not in logged_events(tmp_path)

    def test_observer_error_locks_exactly_as_today(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # I1 again: a broken observer degrades to unknown, and the error is
        # logged once per streak rather than once per tick.
        locker, ticks = run_session(tmp_path, WALK_AWAY, monkeypatch, observer=BrokenLockObserver())
        assert locker.lock_calls == 1
        events = logged_events(tmp_path)
        assert events.count("lock_observer_error") == 1
        assert all(t.screen_locked is None for t in ticks)

    def test_unlocked_state_locks_exactly_as_today(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observer = FakeLockObserver([False])
        locker, _ticks = run_session(tmp_path, WALK_AWAY, monkeypatch, observer=observer)
        assert locker.lock_calls == 1

    def test_truthy_non_true_state_does_not_suppress_lock(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The skip condition must be `is True`, not truthiness: a contract-
        # violating observer returning 1 (truthy, not the bool True) must NOT
        # suppress locking. Guards against a future backend breaking I1.
        observer = FakeLockObserver([1])  # type: ignore[list-item]
        locker, _ticks = run_session(tmp_path, WALK_AWAY, monkeypatch, observer=observer)
        assert locker.lock_calls == 1
        assert "lock_skipped_already_locked" not in logged_events(tmp_path)


class TestTransitionEvents:
    def test_locked_then_unlocked_transitions_are_logged(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observer = FakeLockObserver([False, False, True, True, False])
        _locker, _ticks = run_session(tmp_path, STAY_NEAR, monkeypatch, observer=observer)
        events = logged_events(tmp_path)
        transitions = [e for e in events if e in ("session_locked", "session_unlocked")]
        assert transitions == ["session_locked", "session_unlocked"]

    def test_first_known_state_is_a_baseline_not_a_transition(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # A steady state produces no transition events, whatever its value.
        observer = FakeLockObserver([True])
        _locker, _ticks = run_session(tmp_path, STAY_NEAR, monkeypatch, observer=observer)
        events = logged_events(tmp_path)
        assert "session_locked" not in events
        assert "session_unlocked" not in events

    def test_unknown_gaps_do_not_fake_transitions(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # locked -> unknown -> locked is one continuous locked state.
        observer = FakeLockObserver([True, None, None, True])
        _locker, _ticks = run_session(tmp_path, STAY_NEAR, monkeypatch, observer=observer)
        events = logged_events(tmp_path)
        assert "session_locked" not in events
        assert "session_unlocked" not in events


class TestTickExposure:
    def test_ticks_carry_observed_state(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observer = FakeLockObserver([None, False, True])
        _locker, ticks = run_session(tmp_path, STAY_NEAR, monkeypatch, observer=observer)
        assert [t.screen_locked for t in ticks[:3]] == [None, False, True]
        assert all(t.screen_locked is True for t in ticks[3:])

    def test_no_observer_reports_unknown(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _locker, ticks = run_session(tmp_path, STAY_NEAR, monkeypatch, observer=None)
        assert all(t.screen_locked is None for t in ticks)


class TestLifecycle:
    def test_observer_closed_when_run_ends(
        self, tmp_path: Path, virtual_clock: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        observer = FakeLockObserver([False])
        run_session(tmp_path, STAY_NEAR, monkeypatch, observer=observer)
        assert observer.closed
