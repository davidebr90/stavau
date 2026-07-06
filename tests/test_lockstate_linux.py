"""Tests for the Linux logind lock-state observer.

Runs on any OS: no real D-Bus is touched. The observer's session-proxy
factory is a constructor parameter, so a fake proxy stands in for
dbus_fast's real session interface. Mirrors the asyncio.run(...)-driven
style used in test_classic.py rather than pytest-asyncio async defs.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from typing import Any

import pytest

from stavau.platform import lockstate_linux as lsl
from stavau.platform.lockstate_linux import (
    LinuxLockStateObserver,
    _parse_locked_hint,
    make_observer,
)


class _Variant:
    """Duck-typed stand-in for dbus_fast.Variant."""

    def __init__(self, value: Any) -> None:
        self.value = value


class FakeSessionProxy:
    """Fake session proxy: seeds an initial value and lets tests fire signals."""

    def __init__(self, initial: bool | None = False, fail_connect: bool = False) -> None:
        self._initial = initial
        self._fail_connect = fail_connect
        self.props_changed_cb: Callable[[str, dict[str, Any], list[str]], None] | None = None
        self.lock_cb: Callable[[], None] | None = None
        self.unlock_cb: Callable[[], None] | None = None
        self.disconnected = False

    async def get_locked_hint(self) -> bool | None:
        if self._fail_connect:
            raise RuntimeError("bus exploded")
        return self._initial

    def on_properties_changed(
        self, callback: Callable[[str, dict[str, Any], list[str]], None]
    ) -> None:
        self.props_changed_cb = callback

    def on_lock(self, callback: Callable[[], None]) -> None:
        self.lock_cb = callback

    def on_unlock(self, callback: Callable[[], None]) -> None:
        self.unlock_cb = callback

    async def disconnect(self) -> None:
        self.disconnected = True

    def fire_properties_changed(self, locked: bool | None) -> None:
        assert self.props_changed_cb is not None
        payload: dict[str, Any] = {} if locked is None else {"LockedHint": _Variant(locked)}
        self.props_changed_cb(lsl._SESSION_IFACE, payload, [])

    def fire_lock(self) -> None:
        assert self.lock_cb is not None
        self.lock_cb()

    def fire_unlock(self) -> None:
        assert self.unlock_cb is not None
        self.unlock_cb()


async def _settle() -> None:
    """Let scheduled tasks run at least one full round of the event loop."""
    for _ in range(5):
        await asyncio.sleep(0)


class TestParseLockedHint:
    def test_true_variant_like(self) -> None:
        assert _parse_locked_hint({"LockedHint": _Variant(True)}) is True

    def test_false_variant_like(self) -> None:
        assert _parse_locked_hint({"LockedHint": _Variant(False)}) is False

    def test_plain_bool_fallback(self) -> None:
        assert _parse_locked_hint({"LockedHint": True}) is True
        assert _parse_locked_hint({"LockedHint": False}) is False

    def test_missing_key_is_none(self) -> None:
        assert _parse_locked_hint({}) is None

    def test_garbage_value_is_none(self) -> None:
        assert _parse_locked_hint({"LockedHint": _Variant("not-a-bool")}) is None
        assert _parse_locked_hint({"LockedHint": "nope"}) is None
        assert _parse_locked_hint({"LockedHint": _Variant(None)}) is None


class TestObserverConnectAndSeed:
    def test_seeds_initial_state_true(self) -> None:
        proxy = FakeSessionProxy(initial=True)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> bool | None:
            observer.current()  # schedules connect task
            await _settle()
            return observer.current()

        assert asyncio.run(drive()) is True

    def test_seeds_initial_state_false(self) -> None:
        proxy = FakeSessionProxy(initial=False)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            return observer.current()

        assert asyncio.run(drive()) is False

    def test_current_before_running_loop_is_none_and_does_not_raise(self) -> None:
        proxy = FakeSessionProxy(initial=True)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)
        # No running loop here: get_running_loop() must fail gracefully.
        assert observer.current() is None


class TestObserverSignals:
    def test_properties_changed_flips_state(self) -> None:
        proxy = FakeSessionProxy(initial=False)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> tuple[bool | None, bool | None]:
            observer.current()
            await _settle()
            before = observer.current()
            proxy.fire_properties_changed(True)
            after = observer.current()
            return before, after

        before, after = asyncio.run(drive())
        assert before is False
        assert after is True

    def test_properties_changed_missing_key_leaves_state_unchanged(self) -> None:
        proxy = FakeSessionProxy(initial=True)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            proxy.fire_properties_changed(None)  # no LockedHint key
            return observer.current()

        assert asyncio.run(drive()) is True

    def test_lock_signal_sets_true(self) -> None:
        proxy = FakeSessionProxy(initial=False)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            proxy.fire_lock()
            return observer.current()

        assert asyncio.run(drive()) is True

    def test_unlock_signal_sets_false(self) -> None:
        proxy = FakeSessionProxy(initial=True)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            proxy.fire_unlock()
            return observer.current()

        assert asyncio.run(drive()) is False

    def test_subscribers_notified_on_change(self) -> None:
        proxy = FakeSessionProxy(initial=False)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)
        seen: list[bool] = []
        observer.subscribe(seen.append)

        async def drive() -> None:
            observer.current()
            await _settle()
            proxy.fire_lock()

        asyncio.run(drive())
        # The initial seed (False) notifies too, then the lock signal (True).
        assert seen == [False, True]

    def test_subscriber_exception_is_suppressed(self) -> None:
        proxy = FakeSessionProxy(initial=False)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        def boom(_: bool) -> None:
            raise RuntimeError("subscriber blew up")

        observer.subscribe(boom)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            proxy.fire_lock()  # must not raise despite the broken subscriber
            return observer.current()

        assert asyncio.run(drive()) is True


class TestObserverErrors:
    def test_connect_failure_keeps_state_none(self) -> None:
        proxy = FakeSessionProxy(fail_connect=True)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            return observer.current()

        assert asyncio.run(drive()) is None

    def test_factory_raising_keeps_state_none(self) -> None:
        def broken_factory() -> Any:
            raise RuntimeError("no bus available")

        observer = LinuxLockStateObserver(session_proxy_factory=broken_factory)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            return observer.current()

        assert asyncio.run(drive()) is None


class TestClose:
    def test_close_is_idempotent_and_never_raises(self) -> None:
        proxy = FakeSessionProxy(initial=True)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> None:
            observer.current()
            await _settle()
            observer.close()
            observer.close()  # second call must be a no-op, not an error
            await _settle()

        asyncio.run(drive())

    def test_close_before_any_connect_never_raises(self) -> None:
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: FakeSessionProxy())
        observer.close()
        observer.close()

    def test_current_after_close_stays_cached_and_does_not_reconnect(self) -> None:
        proxy = FakeSessionProxy(initial=True)
        observer = LinuxLockStateObserver(session_proxy_factory=lambda: proxy)

        async def drive() -> bool | None:
            observer.current()
            await _settle()
            observer.close()
            return observer.current()

        assert asyncio.run(drive()) is True


class TestMakeObserver:
    def test_returns_observer_on_linux(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        observer = make_observer()
        assert observer is not None
        assert observer.name == "linux-logind"

    def test_none_on_win32(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        assert make_observer() is None

    def test_none_on_darwin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert make_observer() is None
