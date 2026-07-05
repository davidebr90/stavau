"""Windows screen lock via the user32 LockWorkStation API."""

from __future__ import annotations

import sys

from stavau.platform.base import LockError


class WindowsLocker:
    name = "windows"

    def lock(self) -> None:
        if sys.platform != "win32":  # pragma: no cover — the factory prevents this
            raise LockError("WindowsLocker used on a non-Windows platform")
        import ctypes

        # LockWorkStation returns 0 on failure per the Win32 API contract.
        if ctypes.windll.user32.LockWorkStation() == 0:
            raise LockError("LockWorkStation() failed")
