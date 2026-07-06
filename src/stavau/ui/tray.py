"""Minimal system-tray host for the stavau monitor (v0.4 feature, early preview).

Runs the shared MonitorSession, but reports state through a notification-area
icon: green (near), amber (leaving, grace running), red (away/locked), blue
(returning), grey (no signal), and a paused padlock when the anti-runaway
guardrail has tripped. Tooltip shows live RSSI, estimated distance and guardrail
state. Requires the optional `tray` extra (pystray + Pillow).
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time

import pystray
from PIL import Image, ImageDraw

from stavau import __version__
from stavau.config.settings import Settings, event_log_path
from stavau.core.events import EventLog
from stavau.core.monitor import NearbyCache, NearbyDevice
from stavau.core.presence import PresenceState
from stavau.core.session import MonitorSession, Tick
from stavau.platform.base import Locker

_STATE_COLORS: dict[PresenceState, tuple[int, int, int]] = {
    PresenceState.NEAR: (56, 176, 72),  # green
    PresenceState.LEAVING: (240, 160, 0),  # amber
    PresenceState.RETURNING: (0, 120, 215),  # blue
    PresenceState.AWAY: (204, 62, 52),  # red
}
_NO_SIGNAL_COLOR = (128, 128, 128)  # grey
_PAUSED_COLOR = (150, 90, 200)  # purple: guardrail active


def _padlock_image(color: tuple[int, int, int], paused: bool = False) -> Image.Image:
    """Draw a 64x64 padlock silhouette filled with the state color."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = (*color, 255)
    draw.arc((18, 8, 46, 36), start=180, end=360, fill=fill, width=7)
    draw.line((21, 22, 21, 34), fill=fill, width=7)
    draw.line((43, 22, 43, 34), fill=fill, width=7)
    draw.rounded_rectangle((12, 30, 52, 58), radius=9, fill=fill)
    if paused:
        # Pause bars instead of a keyhole when the guardrail is active.
        draw.rectangle((26, 38, 30, 52), fill=(255, 255, 255, 235))
        draw.rectangle((34, 38, 38, 52), fill=(255, 255, 255, 235))
    else:
        draw.ellipse((27, 37, 37, 47), fill=(255, 255, 255, 235))
        draw.rectangle((30, 44, 34, 52), fill=(255, 255, 255, 235))
    return img


class TrayApp:
    def __init__(self, settings: Settings, locker: Locker | None) -> None:
        self._settings = settings
        self._locker = locker
        self._log = EventLog(event_log_path())
        self._nearby = NearbyCache()
        self._session = MonitorSession(settings, locker, self._log, nearby=self._nearby)
        self._stop = threading.Event()
        self._status = "starting..."
        self._icon_key: object = None
        mode = "dry-run" if locker is None else "armed"
        self._icon = pystray.Icon(
            "stavau",
            _padlock_image(_NO_SIGNAL_COLOR),
            f"stavau {__version__} [{mode}]",
            menu=pystray.Menu(
                pystray.MenuItem(lambda item: self._status, None),
                pystray.MenuItem(
                    lambda item: (
                        f"device: {self._settings.device_alias}"
                        f"  radius: {self._settings.radius_m:g} m"
                    ),
                    None,
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Nearby devices", pystray.Menu(self._nearby_menu)),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit stavau", self._on_quit),
            ),
        )

    # ---------------------------------------------------------------- public

    def run(self) -> int:
        """Blocks until Quit is chosen. Returns a process exit code."""
        self._icon.run(setup=self._on_ready)
        return 0

    # ---------------------------------------------------------------- tray

    def _on_ready(self, icon: pystray.Icon) -> None:
        icon.visible = True
        threading.Thread(target=self._monitor_thread, name="stavau-monitor", daemon=True).start()

    def _on_quit(self, icon: pystray.Icon, item: pystray.MenuItem) -> None:
        self._stop.set()
        icon.stop()

    def _nearby_menu(self) -> list[pystray.MenuItem]:
        """Rebuilt every time the submenu opens: devices seen in the last 30 s.

        Most entries are '<unnamed>': phones omit their name from BLE
        advertisements for privacy — the names in the OS Bluetooth settings
        come from the Classic channel / bonded-device cache instead.
        """
        devices = self._nearby.list(time.monotonic())
        if not devices:
            return [pystray.MenuItem("(no devices seen in the last 30 s)", None)]
        return [
            pystray.MenuItem(
                f"{dev.name}  {dev.rssi:.0f} dBm  {dev.address}",
                lambda icon, item, d=dev: self._switch_device(d),
                checked=lambda item, a=dev.address: a == self._settings.device_address,
            )
            for dev in devices[:12]
        ]

    def _switch_device(self, device: NearbyDevice) -> None:
        """Retarget the monitor to another device, live, and persist the choice."""
        self._settings.device_address = device.address
        self._settings.device_alias = device.name if device.name != "<unnamed>" else device.address
        self._settings.save()
        self._session.retarget(device.address)
        self._log.append("device_switched", device=self._settings.device_alias)

    def _on_tick(self, tick: Tick) -> None:
        if tick.breaker_paused:
            color = _PAUSED_COLOR
        elif tick.rssi is None:
            color = _NO_SIGNAL_COLOR
        else:
            color = _STATE_COLORS[tick.state]
        key = (color, tick.breaker_paused)
        if key != self._icon_key:
            self._icon_key = key
            self._icon.icon = _padlock_image(color, paused=tick.breaker_paused)
        if tick.breaker_paused:
            self._status = f"guardrail paused - {tick.breaker_seconds_remaining:.0f} s left"
        elif tick.rssi is None and tick.radio_off:
            self._status = f"{tick.state.value} - BLUETOOTH OFF"
        elif tick.rssi is None:
            self._status = f"{tick.state.value} - no signal"
        else:
            self._status = f"{tick.state.value} - {tick.distance:.1f} m ({tick.rssi:.0f} dBm)"
        mode = "dry-run" if self._locker is None else "armed"
        self._icon.title = f"stavau [{mode}] {self._status}"

    # ---------------------------------------------------------------- monitor

    def _monitor_thread(self) -> None:
        try:
            asyncio.run(self._session.run(stop=self._stop.is_set, on_tick=self._on_tick))
        except Exception as exc:  # noqa: BLE001 - surface, then die visibly
            self._status = f"error: {exc}"
            self._icon.title = f"stavau error: {exc}"
            self._log.append("monitor_error", error=str(exc))
            print(f"stavau tray monitor error: {exc}", file=sys.stderr, flush=True)


def run_tray(settings: Settings, locker: Locker | None) -> int:
    return TrayApp(settings, locker).run()
