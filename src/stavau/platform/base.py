"""Locker protocol and per-OS factory.

Adding a platform means adding one module implementing `Locker` and one
branch here — core code never imports OS-specific modules directly.
"""

from __future__ import annotations

import sys
from typing import Protocol


class LockError(RuntimeError):
    """The OS screen lock could not be triggered. Callers must surface this loudly."""


class UnsupportedPlatformError(RuntimeError):
    pass


class Locker(Protocol):
    name: str

    def lock(self) -> None:
        """Lock the screen now. Raises LockError on failure."""
        ...


def get_locker() -> Locker:
    if sys.platform.startswith("linux"):
        from stavau.platform.linux import LinuxLocker

        return LinuxLocker()
    if sys.platform == "win32":
        from stavau.platform.windows import WindowsLocker

        return WindowsLocker()
    if sys.platform == "darwin":
        from stavau.platform.macos import MacLocker

        return MacLocker()
    raise UnsupportedPlatformError(f"unsupported platform: {sys.platform}")
