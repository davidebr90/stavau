"""Tests for the macOS lock-state observer — runnable on any OS, zero Objective-C.

Strategy: the observer's constructor takes injectable `center_factory` and
`runloop_runner` callables, so tests supply a fake notification center that
just records the registered handler, and a no-op runloop runner (a thread
that immediately waits on the stop event). Driving `_handle_notification`
directly (or invoking the captured handler) exercises the real state machine
without touching any Objective-C runtime.
"""

from __future__ import annotations

import builtins
import sys
import threading
from typing import Any

import pytest

from stavau.platform import lockstate_macos
from stavau.platform.lockstate_macos import MacLockStateObserver, make_observer


class FakeNotificationCenter:
    """Captures registered (name -> selector-ish callable) pairs.

    The real `_register` calls `objc.selector(...)` on the bound method and
    passes that through `addObserver_selector_name_object_`; since our fake
    doesn't touch `objc`, we monkeypatch `_register` in most tests to bypass
    the pyobjc-only selector wrapping and register directly against
    `_on_notification`-equivalent behavior via `_handle_notification`.
    """

    def __init__(self) -> None:
        self.added: list[tuple[Any, Any, str, Any]] = []
        self.removed: list[Any] = []

    def addObserver_selector_name_object_(
        self, observer: Any, selector: Any, name: str, obj: Any
    ) -> None:
        self.added.append((observer, selector, name, obj))

    def removeObserver_(self, observer: Any) -> None:
        self.removed.append(observer)


def _noop_runloop_runner(stop_event: threading.Event) -> None:
    # Stand-in for spinning NSRunLoop: just wait until close() signals stop.
    stop_event.wait()


def make_test_observer() -> tuple[MacLockStateObserver, FakeNotificationCenter]:
    """Build an observer with fakes, bypassing the pyobjc-only `_register`."""
    center = FakeNotificationCenter()

    def factory() -> FakeNotificationCenter:
        return center

    original_register = MacLockStateObserver._register
    MacLockStateObserver._register = lambda self, c: None  # type: ignore[method-assign]
    try:
        observer = MacLockStateObserver(center_factory=factory, runloop_runner=_noop_runloop_runner)
    finally:
        MacLockStateObserver._register = original_register  # type: ignore[method-assign]
    return observer, center


class TestHandleNotification:
    def test_lock_notification_sets_true(self) -> None:
        observer, _center = make_test_observer()
        try:
            observer._handle_notification("com.apple.screenIsLocked")
            assert observer.current() is True
        finally:
            observer.close()

    def test_unlock_notification_sets_false(self) -> None:
        observer, _center = make_test_observer()
        try:
            observer._handle_notification("com.apple.screenIsUnlocked")
            assert observer.current() is False
        finally:
            observer.close()

    def test_unknown_notification_is_ignored(self) -> None:
        observer, _center = make_test_observer()
        try:
            assert observer.current() is None
            observer._handle_notification("com.apple.somethingElse")
            assert observer.current() is None
        finally:
            observer.close()

    def test_initial_state_is_none(self) -> None:
        observer, _center = make_test_observer()
        try:
            assert observer.current() is None
        finally:
            observer.close()

    def test_callbacks_invoked_with_new_state(self) -> None:
        observer, _center = make_test_observer()
        try:
            received: list[bool] = []
            observer.subscribe(received.append)
            observer._handle_notification("com.apple.screenIsLocked")
            observer._handle_notification("com.apple.screenIsUnlocked")
            assert received == [True, False]
        finally:
            observer.close()

    def test_callback_exception_is_suppressed_and_others_still_run(self) -> None:
        observer, _center = make_test_observer()
        try:
            received: list[bool] = []

            def broken(_state: bool) -> None:
                raise RuntimeError("boom")

            observer.subscribe(broken)
            observer.subscribe(received.append)
            # Must not raise despite the broken subscriber.
            observer._handle_notification("com.apple.screenIsLocked")
            assert received == [True]
        finally:
            observer.close()

    def test_state_consistent_across_two_threads(self) -> None:
        # Smoke test: hammer _handle_notification from two threads and make
        # sure current() always reads a valid, lock-guarded value (never
        # raises, never a torn value from the small set {True, False}).
        observer, _center = make_test_observer()
        try:
            names = ["com.apple.screenIsLocked", "com.apple.screenIsUnlocked"]
            errors: list[Exception] = []

            def hammer(name: str) -> None:
                try:
                    for _ in range(200):
                        observer._handle_notification(name)
                        assert observer.current() in (True, False)
                except Exception as exc:  # noqa: BLE001 - captured for the assertion below
                    errors.append(exc)

            threads = [threading.Thread(target=hammer, args=(n,)) for n in names]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=5.0)

            assert errors == []
            assert observer.current() in (True, False)
        finally:
            observer.close()


class TestFakeCenterDrivesState:
    def test_injected_center_factory_is_used_and_registration_bypassed(self) -> None:
        # Confirms the constructor actually calls the injected center_factory
        # (rather than the real Foundation one) and that, with registration
        # bypassed, no notification arrives until we drive it ourselves via
        # _handle_notification — i.e. no runloop/Objective-C is involved.
        observer, center = make_test_observer()
        try:
            assert observer._center is center
            assert observer.current() is None
            observer._handle_notification("com.apple.screenIsLocked")
            assert observer.current() is True
            observer._handle_notification("com.apple.screenIsUnlocked")
            assert observer.current() is False
        finally:
            observer.close()


class TestClose:
    def test_close_is_idempotent_and_stops_thread(self) -> None:
        observer, center = make_test_observer()
        thread = observer._thread
        observer.close()
        assert not thread.is_alive() or _join_quickly(thread)
        # Second close must not raise.
        observer.close()

    def test_close_removes_observer_from_center(self) -> None:
        # Use a center that actually gets removeObserver_ called: bypass the
        # no-op _register patch by registering manually beforehand isn't
        # needed since close() calls removeObserver_ unconditionally.
        observer, center = make_test_observer()
        observer.close()
        assert observer in center.removed

    def test_close_never_raises_even_if_center_remove_fails(self) -> None:
        class ExplodingCenter(FakeNotificationCenter):
            def removeObserver_(self, observer: Any) -> None:
                raise RuntimeError("center gone")

        center = ExplodingCenter()
        original_register = MacLockStateObserver._register
        MacLockStateObserver._register = lambda self, c: None  # type: ignore[method-assign]
        try:
            observer = MacLockStateObserver(
                center_factory=lambda: center, runloop_runner=_noop_runloop_runner
            )
        finally:
            MacLockStateObserver._register = original_register  # type: ignore[method-assign]
        observer.close()  # must not raise


def _join_quickly(thread: threading.Thread) -> bool:
    thread.join(timeout=2.0)
    return not thread.is_alive()


class TestMakeObserver:
    def test_none_on_non_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert make_observer() is None

    def test_none_on_win32(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        assert make_observer() is None

    def test_none_on_darwin_when_pyobjc_import_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in ("Foundation", "objc"):
                raise ImportError(f"no module named {name}")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert make_observer() is None

    def test_constructs_observer_on_darwin_when_import_succeeds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Simulate a successful pyobjc import and a successful construction by
        # monkeypatching MacLockStateObserver itself to a stand-in that
        # doesn't touch real Foundation objects.
        monkeypatch.setattr(sys, "platform", "darwin")

        real_import = builtins.__import__

        def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in ("Foundation", "objc"):
                return object()
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        sentinel = object()

        def fake_constructor() -> Any:
            return sentinel

        monkeypatch.setattr(lockstate_macos, "MacLockStateObserver", fake_constructor)
        assert make_observer() is sentinel
