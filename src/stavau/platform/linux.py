"""Linux screen lock via systemd-logind, with desktop-specific fallbacks."""

from __future__ import annotations

import shutil
import subprocess

from stavau.platform.base import LockError

# Ordered by preference: loginctl works on every systemd distro, X11 and Wayland.
_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("loginctl", "lock-session"),
    ("xdg-screensaver", "lock"),
    (
        "dbus-send",
        "--type=method_call",
        "--dest=org.freedesktop.ScreenSaver",
        "/org/freedesktop/ScreenSaver",
        "org.freedesktop.ScreenSaver.Lock",
    ),
)


class LinuxLocker:
    name = "linux"

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
