"""OS-specific integrations. Each module implements the Locker protocol."""

from stavau.platform.base import Locker, LockError, UnsupportedPlatformError, get_locker

__all__ = ["LockError", "Locker", "UnsupportedPlatformError", "get_locker"]
