"""Screen *unlock* capability — deliberately narrow and mostly absent.

Auto-unlock is fundamentally different from auto-lock. Locking a session is
always available (and fail-safe); unlocking one without the user's credentials
is, by design of every mainstream desktop OS, NOT possible:

  * Windows: there is no public "unlock workstation" API. `LockWorkStation` is
    one-way on purpose (Windows Dynamic Lock itself only ever locks). Returning
    None here is correct, not a gap.
  * macOS: no public API unlocks the login window without credentials.
  * Linux: systemd-logind exposes `loginctl unlock-session`, which unlocks the
    session (the DE screensaver honours the logind Unlock signal). This is the
    only place stavau can genuinely auto-unlock — and it is gated behind heavy
    policy checks (see core.autounlock) because "anyone who can drive this can
    unlock your screen" is exactly the risk.

`get_unlocker()` returns None where unlocking is impossible, so the auto-unlock
feature refuses to enable rather than pretending. An Unlocker never unlocks on
its own; it only acts when core.autounlock's policy explicitly permits it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Protocol


class UnlockError(RuntimeError):
    """The OS unlock could not be performed."""


class Unlocker(Protocol):
    name: str

    def unlock(self) -> None:
        """Unlock the session now. Raises UnlockError on failure."""
        ...


class LinuxUnlocker:
    """Unlock via systemd-logind (`loginctl unlock-session`)."""

    name = "linux-loginctl"

    @staticmethod
    def available() -> bool:
        return sys.platform.startswith("linux") and shutil.which("loginctl") is not None

    def unlock(self) -> None:
        try:
            result = subprocess.run(
                ["loginctl", "unlock-session"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise UnlockError(f"loginctl unlock-session failed: {exc}") from exc
        if result.returncode != 0:
            raise UnlockError(
                f"loginctl unlock-session exit {result.returncode}: {result.stderr.strip()}"
            )


def get_unlocker() -> Unlocker | None:
    """Return a real unlocker, or None where the OS has no safe unlock API.

    None on Windows and macOS is intentional: auto-unlock is refused there.
    """
    if LinuxUnlocker.available():
        return LinuxUnlocker()
    return None
