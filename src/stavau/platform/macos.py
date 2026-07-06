"""macOS screen lock via CGSession, with a pmset fallback."""

from __future__ import annotations

import shutil
import subprocess

from stavau.platform.base import LockError

# Ordered by preference: CGSession -suspend locks the screen immediately and
# is the closest macOS equivalent to loginctl lock-session on Linux.
# pmset displaysleepnow is the fallback — it puts the display to sleep, which
# also triggers the lock screen when "require password after sleep" is set.
_CG_SESSION = "/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession"

_COMMANDS: tuple[tuple[str, ...], ...] = (
    (_CG_SESSION, "-suspend"),
    ("pmset", "displaysleepnow"),
)


class MacLocker:
    name = "macos"

    def lock(self) -> None:
        attempts: list[str] = []
        for command in _COMMANDS:
            if shutil.which(command[0]) is None:
                attempts.append(f"{command[0]}: not installed")
                continue
            try:
                result = subprocess.run(
                    command, capture_output=True, text=True, timeout=10, check=False
                )
            except subprocess.TimeoutExpired:
                attempts.append(f"{command[0]}: timed out")
                continue
            if result.returncode == 0:
                return
            attempts.append(
                f"{' '.join(command)}: exit {result.returncode} {result.stderr.strip()}"
            )
        raise LockError("could not lock the screen; attempts: " + "; ".join(attempts))
