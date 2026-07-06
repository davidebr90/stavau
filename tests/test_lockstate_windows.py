"""Tests for the Windows WTS lock-state observer.

These tests must run on any OS without creating a real window or touching
ctypes/windll: the constructor never starts the message-loop thread on its
own (see ``WindowsLockStateObserver._start``), so exercising
``_handle_session_change`` and the public protocol methods directly is
platform-independent. The seam is deliberate: ``_start()`` is the only place
that touches win32 plumbing, and tests never call it.
"""

from __future__ import annotations

import sys
import threading

import pytest

from stavau.platform.lockstate_windows import (
    WTS_SESSION_LOCK,
    WTS_SESSION_UNLOCK,
    WindowsLockStateObserver,
    make_observer,
)


class TestHandleSessionChange:
    def test_lock_code_sets_state_true(self) -> None:
        observer = WindowsLockStateObserver()
        observer._handle_session_change(WTS_SESSION_LOCK)
        assert observer.current() is True

    def test_unlock_code_sets_state_false(self) -> None:
        observer = WindowsLockStateObserver()
        observer._handle_session_change(WTS_SESSION_UNLOCK)
        assert observer.current() is False

    def test_initial_state_is_unknown(self) -> None:
        observer = WindowsLockStateObserver()
        assert observer.current() is None

    @pytest.mark.parametrize("wparam", [0x0, 0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x9, 999])
    def test_other_wparam_values_are_ignored(self, wparam: int) -> None:
        observer = WindowsLockStateObserver()
        observer._handle_session_change(WTS_SESSION_LOCK)
        observer._handle_session_change(wparam)
        # Still True: the unrelated notification did not touch cached state.
        assert observer.current() is True

    def test_unknown_stays_unknown_for_unrelated_code(self) -> None:
        observer = WindowsLockStateObserver()
        observer._handle_session_change(0x3)  # WTS_SESSION_LOGON, e.g.
        assert observer.current() is None


class TestCallbacks:
    def test_callback_invoked_with_new_state_on_lock(self) -> None:
        observer = WindowsLockStateObserver()
        seen: list[bool] = []
        observer.subscribe(seen.append)
        observer._handle_session_change(WTS_SESSION_LOCK)
        assert seen == [True]

    def test_callback_invoked_with_new_state_on_unlock(self) -> None:
        observer = WindowsLockStateObserver()
        seen: list[bool] = []
        observer.subscribe(seen.append)
        observer._handle_session_change(WTS_SESSION_UNLOCK)
        assert seen == [False]

    def test_multiple_callbacks_all_invoked(self) -> None:
        observer = WindowsLockStateObserver()
        seen_a: list[bool] = []
        seen_b: list[bool] = []
        observer.subscribe(seen_a.append)
        observer.subscribe(seen_b.append)
        observer._handle_session_change(WTS_SESSION_LOCK)
        assert seen_a == [True]
        assert seen_b == [True]

    def test_callback_not_invoked_for_ignored_wparam(self) -> None:
        observer = WindowsLockStateObserver()
        seen: list[bool] = []
        observer.subscribe(seen.append)
        observer._handle_session_change(0x4)
        assert seen == []

    def test_callback_exception_is_suppressed(self) -> None:
        observer = WindowsLockStateObserver()

        def broken(_state: bool) -> None:
            raise RuntimeError("subscriber exploded")

        observer.subscribe(broken)
        # Must not raise.
        observer._handle_session_change(WTS_SESSION_LOCK)
        assert observer.current() is True

    def test_callback_exception_does_not_block_other_callbacks(self) -> None:
        observer = WindowsLockStateObserver()
        seen: list[bool] = []

        def broken(_state: bool) -> None:
            raise RuntimeError("subscriber exploded")

        observer.subscribe(broken)
        observer.subscribe(seen.append)
        observer._handle_session_change(WTS_SESSION_LOCK)
        assert seen == [True]


class TestThreadSafety:
    def test_concurrent_transitions_do_not_corrupt_state(self) -> None:
        observer = WindowsLockStateObserver()
        iterations = 500

        def hammer_lock() -> None:
            for _ in range(iterations):
                observer._handle_session_change(WTS_SESSION_LOCK)

        def hammer_unlock() -> None:
            for _ in range(iterations):
                observer._handle_session_change(WTS_SESSION_UNLOCK)

        threads = [
            threading.Thread(target=hammer_lock),
            threading.Thread(target=hammer_unlock),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)
            assert not t.is_alive()

        # No crash/corruption: final state is a definite bool, one of the two.
        assert observer.current() in (True, False)

    def test_concurrent_current_reads_never_see_torn_state(self) -> None:
        observer = WindowsLockStateObserver()
        stop = threading.Event()
        bad_reads: list[object] = []

        def reader() -> None:
            while not stop.is_set():
                value = observer.current()
                if value not in (True, False, None):
                    bad_reads.append(value)

        def writer() -> None:
            for i in range(500):
                observer._handle_session_change(
                    WTS_SESSION_LOCK if i % 2 == 0 else WTS_SESSION_UNLOCK
                )

        reader_thread = threading.Thread(target=reader)
        writer_thread = threading.Thread(target=writer)
        reader_thread.start()
        writer_thread.start()
        writer_thread.join(timeout=10.0)
        stop.set()
        reader_thread.join(timeout=10.0)

        assert bad_reads == []


class TestConstructionIsLazy:
    def test_construct_without_starting_message_thread(self) -> None:
        # Constructing must never spin up the win32 message loop: _start()
        # is the only seam that touches ctypes/windll, and it is never
        # called here.
        observer = WindowsLockStateObserver()
        assert observer.current() is None
        assert observer._thread is None

    def test_close_without_start_is_a_safe_noop(self) -> None:
        observer = WindowsLockStateObserver()
        observer.close()  # must not raise
        observer.close()  # idempotent

    def test_name_is_windows_wts(self) -> None:
        assert WindowsLockStateObserver().name == "windows-wts"


class TestMakeObserver:
    def test_returns_none_on_non_windows_platform(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert make_observer() is None

    def test_returns_none_on_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert make_observer() is None

    def test_start_failure_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Simulate a win32 platform where the win32 plumbing fails to start
        # (e.g. old Windows, no session) without touching real ctypes/windll.
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(WindowsLockStateObserver, "_start", lambda self: False)
        assert make_observer() is None

    def test_start_success_returns_observer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(WindowsLockStateObserver, "_start", lambda self: True)
        observer = make_observer()
        assert observer is not None
        assert observer.name == "windows-wts"
