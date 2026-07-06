"""macOS lock-state observer via loginwindow distributed notifications.

Event source: loginwindow posts two distributed notifications on the default
``NSDistributedNotificationCenter`` whenever the session locks or unlocks:
``com.apple.screenIsLocked`` and ``com.apple.screenIsUnlocked``. Foundation
(via pyobjc) is the only way to observe these from Python.

``pyobjc-framework-Cocoa`` is an OPTIONAL dependency (see the ``macos`` extra
in pyproject.toml) so this module must import cleanly, and type-check, on
Windows and Linux. All pyobjc imports are therefore pushed inside functions
and guarded by ``sys.platform == "darwin"`` checks first, mirroring the
guarded-import style of ``stavau.core.classic.WinRtConnectionBackend``.
``make_observer`` returns ``None`` when the import fails for any reason —
degrading to "unknown state" is always safe because an unknown lock state
never suppresses locking (invariant I1); only an affirmative ``True`` does.

Polling alternative (not implemented here): ``Quartz.CGSessionCopyCurrentDictionary``
can be polled for the ``kCGSessionOnConsoleKey`` / ``CGSSessionScreenIsLocked``
keys to get a queryable (rather than edge-triggered) snapshot of the lock
state. That would remove the "unknown until first transition" gap this
observer has, but it requires the separate ``pyobjc-framework-Quartz``
dependency, so it is left as a documented future enhancement rather than
implemented in this card.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol

_LOCKED_NOTIFICATION = "com.apple.screenIsLocked"
_UNLOCKED_NOTIFICATION = "com.apple.screenIsUnlocked"

# Polling interval for the run-loop-spinning thread's stop check. Short enough
# that close() returns promptly, long enough to avoid busy-waiting.
_RUNLOOP_SLICE_SECONDS = 0.2


class _NotificationCenter(Protocol):
    """Shape of NSDistributedNotificationCenter that this observer relies on."""

    def addObserver_selector_name_object_(  # noqa: N802 - Objective-C bridge naming
        self, observer: Any, selector: Any, name: str, obj: Any
    ) -> None: ...

    def removeObserver_(self, observer: Any) -> None:  # noqa: N802
        ...


def _default_center_factory() -> _NotificationCenter:  # pragma: no cover - real macOS only
    """Create the real distributed notification center. macOS/pyobjc only."""
    from Foundation import NSDistributedNotificationCenter

    center: _NotificationCenter = NSDistributedNotificationCenter.defaultCenter()
    return center


def _default_runloop_runner(stop_event: threading.Event) -> None:  # pragma: no cover
    """Spin the current thread's NSRunLoop until stop_event is set.

    Runs on a dedicated daemon thread: NSDistributedNotificationCenter
    delivers notifications on the run loop of the thread that registered the
    observer, so that thread must keep a run loop alive for callbacks to
    fire.
    """
    from Foundation import NSDate, NSRunLoop

    run_loop = NSRunLoop.currentRunLoop()
    while not stop_event.is_set():
        run_loop.runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(_RUNLOOP_SLICE_SECONDS))


class MacLockStateObserver:
    """Observes loginwindow's screen lock/unlock distributed notifications.

    Edge-triggered: ``current()`` starts as ``None`` (unknown) and only
    becomes a definite ``True``/``False`` once the first notification arrives.
    This is safe under invariant I1 because ``None`` never suppresses
    locking — only an observed ``True`` does.

    ``center_factory`` and ``runloop_runner`` are injectable for testability:
    the defaults talk to real Foundation/AppKit objects (macOS-only), while
    tests inject fakes that drive ``_handle_notification`` synchronously with
    no Objective-C runtime involved.
    """

    name = "macos-notifications"

    def __init__(
        self,
        *,
        center_factory: Callable[[], _NotificationCenter] = _default_center_factory,
        runloop_runner: Callable[[threading.Event], None] = _default_runloop_runner,
    ) -> None:
        self._lock = threading.Lock()
        self._state: bool | None = None
        self._callbacks: list[Callable[[bool], None]] = []
        self._stop_event = threading.Event()
        self._closed = False

        self._center = center_factory()
        self._register(self._center)

        self._thread = threading.Thread(
            target=runloop_runner,
            args=(self._stop_event,),
            name="stavau-macos-lockstate-runloop",
            daemon=True,
        )
        self._thread.start()

    def _register(self, center: _NotificationCenter) -> None:  # pragma: no cover - real macOS only
        import objc

        selector = objc.selector(self._on_notification, signature=b"v@:@")
        for notification_name in (_LOCKED_NOTIFICATION, _UNLOCKED_NOTIFICATION):
            center.addObserver_selector_name_object_(self, selector, notification_name, None)

    def _on_notification(self, notification: Any) -> None:  # pragma: no cover - real macOS only
        name = str(notification.name())
        self._handle_notification(name)

    def _handle_notification(self, name: str) -> None:
        """Pure state transition: map a notification name to a lock state.

        ``com.apple.screenIsLocked`` -> True, ``com.apple.screenIsUnlocked``
        -> False, anything else is ignored. Flips the lock-guarded cached
        state and invokes subscribed callbacks, suppressing any exception a
        callback raises so one broken subscriber cannot break the others or
        this observer.
        """
        if name == _LOCKED_NOTIFICATION:
            new_state = True
        elif name == _UNLOCKED_NOTIFICATION:
            new_state = False
        else:
            return

        with self._lock:
            self._state = new_state
            callbacks = list(self._callbacks)

        for callback in callbacks:
            with contextlib.suppress(Exception):  # a subscriber hiccup must not break others
                callback(new_state)

    def current(self) -> bool | None:
        with self._lock:
            return self._state

    def subscribe(self, cb: Callable[[bool], None]) -> None:
        with self._lock:
            self._callbacks.append(cb)

    def close(self) -> None:
        """Stop the run loop thread and remove the notification observer.

        Idempotent and never raises: this is an advisory resource, so a
        failure while tearing it down must not propagate.
        """
        if self._closed:
            return
        self._closed = True
        self._stop_event.set()
        with contextlib.suppress(Exception):  # best-effort join
            self._thread.join(timeout=2.0)
        with contextlib.suppress(Exception):  # best-effort cleanup
            self._center.removeObserver_(self)


def make_observer() -> MacLockStateObserver | None:
    """Build the macOS observer, or None when unsupported or pyobjc is absent.

    Returns None unless both hold: ``sys.platform == "darwin"`` AND the
    pyobjc Foundation/objc import succeeds. Any ImportError (extra not
    installed) degrades to None rather than raising, matching
    ``get_lock_state_observer``'s "observing is optional" contract.
    """
    if sys.platform != "darwin":
        return None
    try:
        import Foundation  # noqa: F401
        import objc  # noqa: F401
    except ImportError:
        return None
    return MacLockStateObserver()
