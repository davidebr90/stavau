"""Bluetooth radio state probe: is the adapter itself on, or off/unavailable?

Why this exists: a "no signal" reading is ambiguous. It could mean the trusted
device walked away (the expected case the whole tool is built around), or it
could mean the local Bluetooth radio was switched off, disabled in the OS, or
its permission was revoked. Both end in the same fail-safe lock (see the
staleness handling in presence.py / session.py - I1), but the second case
deserves an explicit reason instead of a silent "no signal", so the user can
tell "phone left" apart from "my own radio is off".

`radio_available()` is the single entry point: True/False when the platform
can tell, None when it cannot (unsupported platform, missing tooling, or any
error probing it). None must never be treated as "off" - it means "unknown",
and callers keep whatever behavior they already have for a missing signal.

Platform probes, mirroring classic.py's guarded-import pattern (sys.platform
check + availability probe + import inside the async function):
  * Windows: WinRT `windows.devices.radios` - Radio.get_radios_async(),
    filtered to RadioKind.BLUETOOTH, read via RadioState.
  * Linux: `bluetoothctl show`, parsed for a "Powered: yes/no" line. Async
    subprocess, same shape as classic.py's `_run` helper.
  * macOS: not implemented yet - always None.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys

_POWERED_RE = re.compile(r"Powered:\s*(yes|no)", re.IGNORECASE)


async def radio_available() -> bool | None:
    """True if the Bluetooth radio is on, False if off, None if unknown.

    Never raises: any probing failure (missing tool, platform quirk, OS
    error) is reported as None so a probe hiccup cannot be mistaken for the
    radio actually being off.
    """
    if sys.platform == "win32":
        return await _windows_radio_available()
    if sys.platform.startswith("linux"):
        return await _linux_radio_available()
    return None  # macOS and anything else: not implemented yet.


# ----------------------------------------------------------------- Windows


def _winrt_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winrt.windows.devices.radios  # noqa: F401
    except ImportError:
        return False
    return True


async def _windows_radio_available() -> bool | None:
    if not _winrt_available():
        return None
    try:
        from winrt.windows.devices.radios import Radio, RadioKind, RadioState

        radios = await Radio.get_radios_async()
        for radio in radios:
            if radio.kind == RadioKind.BLUETOOTH:
                return bool(radio.state == RadioState.ON)
        return None  # no Bluetooth radio reported: cannot determine
    except Exception:  # noqa: BLE001 - any WinRT hiccup must not reach the caller
        return None


# ----------------------------------------------------------------- Linux


async def _linux_radio_available() -> bool | None:
    if shutil.which("bluetoothctl") is None:
        return None
    try:
        rc, out = await _run(["bluetoothctl", "show"], timeout=5.0)
    except Exception:  # noqa: BLE001 - subprocess launch failure -> unknown
        return None
    if rc != 0:
        return None
    match = _POWERED_RE.search(out)
    if match is None:
        return None
    return match.group(1).lower() == "yes"


async def _run(cmd: list[str], timeout: float) -> tuple[int, str]:
    """Run a command, returning (returncode, stdout). Timeout/kill safe.

    A local copy of classic.py's `_run` helper: importing it would create a
    dependency from this standalone probe module onto the Classic-link
    strategy module for no shared state, so a small duplication is cleaner.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 1, ""
    return proc.returncode or 0, stdout.decode(errors="replace")
