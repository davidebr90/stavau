"""Lock-state observer protocol and per-OS factory.

The observer is a feedback seam: it reports whether the OS session is already
locked so the monitoring loop can avoid redundant lock actions and surface the
state to UIs. It is strictly advisory — when the state is unknown (``None``)
or the observer misbehaves, callers must behave exactly as if no observer
existed and keep locking (invariant I1).

Adding a platform means adding one module implementing ``LockStateObserver``
and one branch in :func:`get_lock_state_observer` — core code never imports
OS-specific modules directly (same shape as ``base.get_locker``).
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import Protocol


class LockStateObserver(Protocol):
    name: str

    def current(self) -> bool | None:
        """Return the current lock state: True locked, False unlocked, None unknown."""
        ...

    def subscribe(self, cb: Callable[[bool], None]) -> None:
        """Register a callback invoked with the new state on each transition."""
        ...

    def close(self) -> None:
        """Release any OS resources held by the observer. Idempotent."""
        ...


def get_lock_state_observer() -> LockStateObserver | None:
    """Return the platform lock-state observer, or None when unsupported.

    Unlike ``get_locker`` this never raises: observing the lock state is an
    optional enhancement, so an unsupported platform simply degrades to
    "unknown state" behavior in the monitoring loop.
    """
    if sys.platform.startswith("linux"):
        # Backend planned: logind LockedHint over D-Bus.
        return None
    if sys.platform == "win32":
        # Backend planned: WTS session lock/unlock notifications.
        return None
    if sys.platform == "darwin":
        # Backend planned: screenIsLocked distributed notifications.
        return None
    return None
