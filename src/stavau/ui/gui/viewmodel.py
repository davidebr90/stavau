"""Presentation logic for the GUI, kept free of any Qt import.

Every function here is pure (or operates on plain dataclasses/values) so it can
be unit-tested without a QApplication. Widgets in this package call into these
helpers instead of embedding formatting or validation rules directly — the
GUI stays a thin shell over stavau's core (config/settings.py, core/session.py,
core/monitor.py, core/distance.py, core/calibrate.py, core/deviceid.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from stavau.config.settings import ConfigError, Settings
from stavau.core.monitor import DiscoveredDevice, NearbyDevice
from stavau.core.presence import PresenceState
from stavau.core.session import Tick
from stavau.i18n import tr

# ---------------------------------------------------------------- status text


# A short, translated label for the state, used in the status line.
_STATE_KEYS: dict[PresenceState, str] = {
    PresenceState.NEAR: "status.state.near",
    PresenceState.LEAVING: "status.state.leaving",
    PresenceState.AWAY: "status.state.away",
    PresenceState.RETURNING: "status.state.returning",
}


def state_label(state: PresenceState) -> str:
    return tr(_STATE_KEYS[state])


def format_status(tick: Tick) -> str:
    """Render one status line from a Tick, mirroring the tray's text rules.

    Precedence matches ui/tray.py::TrayApp._on_tick: guardrail pause first,
    then radio-off, then no-signal, then the normal state/distance/rssi line.
    """
    if tick.breaker_paused:
        return tr("status.breaker_paused", seconds=tick.breaker_seconds_remaining)
    state = state_label(tick.state)
    if tick.rssi is None and tick.radio_off:
        return tr("status.bluetooth_off", state=state)
    if tick.rssi is None:
        return tr("status.no_signal", state=state)
    assert tick.distance is not None
    return tr("status.with_distance", state=state, distance=tick.distance, rssi=tick.rssi)


# ---------------------------------------------------------------- strategy caveat


def strategy_caveat(strategy: str, platform: str) -> str:
    """Honest per-(strategy, OS) caveat text (invariant I5).

    classic_link on win32 is reachability-only (no metric distance): the
    radius slider has no effect there, matching Windows Dynamic Lock. On
    Linux/macOS classic_link reports real RSSI. adv_scan has no caveat.
    """
    if strategy != "classic_link":
        return ""
    if platform == "win32":
        return tr("caveat.classic_link_windows")
    return tr("caveat.classic_link_other")


# ---------------------------------------------------------------- settings validation


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""


def validate_settings_message(settings: Settings) -> ValidationResult:
    """Run Settings.validate() and translate a ConfigError into a UI message.

    Never raises: callers can always show `result.message` directly. The
    failure message is Settings.validate()'s own ConfigError text (defined in
    the settings hotspot, English-only for now); only the success message is
    translated here.
    """
    try:
        settings.validate()
    except ConfigError as exc:
        return ValidationResult(ok=False, message=str(exc))
    return ValidationResult(ok=True, message=tr("settings.valid_message"))


def clamp_radius(value: float) -> float:
    """Clamp a slider value into the valid radius range (1-10 m)."""
    return max(1.0, min(10.0, value))


def clamp_grace(value: float) -> float:
    """Clamp a spinbox value into the valid grace range (>= 3 s, cap at 60 s for the UI)."""
    return max(3.0, min(60.0, value))


# ---------------------------------------------------------------- scan rows


@dataclass(frozen=True)
class ScanRow:
    address: str
    name: str
    rssi: float
    kind_label: str = ""
    distance_m: float | None = None


# Emoji hints per device kind, prepended to the translated kind word so the
# table communicates "what is this device" at a glance. Distinct glyphs per
# vendor were an explicit request: apple vs robot vs a plain phone.
# Glyph + i18n key per kind token. Tokens beyond the vendor DeviceKind values
# (apple/android/microsoft/wearable/generic/unknown) are inferred from the name
# or advertised behaviour: tv, headphones, speaker, keyboard, mouse, phone.
_KIND_EMOJI = {
    "apple": "\U0001f34e",  # red apple
    "android": "\U0001f916",  # robot
    "microsoft": "\U0001f4bb",  # laptop
    "wearable": "⌚",  # watch
    "generic": "\U0001f535",  # blue circle
    "unknown": "\U0001f535",  # blue circle
    "tv": "\U0001f4fa",  # television
    "headphones": "\U0001f3a7",  # headphone
    "speaker": "\U0001f50a",  # loudspeaker
    "keyboard": "\U00002328\U0000fe0f",  # keyboard
    "mouse": "\U0001f5b1\U0000fe0f",  # computer mouse
    "phone": "\U0001f4f1",  # smartphone (rotating-MAC personal device)
}
_KIND_KEYS = {
    token: f"device.kind.{token}"
    for token in (
        "apple",
        "android",
        "microsoft",
        "wearable",
        "generic",
        "unknown",
        "tv",
        "headphones",
        "speaker",
        "keyboard",
        "mouse",
        "phone",
    )
}

# "TV" is a 2-letter token, so it needs a word boundary (a plain substring
# would false-match "netvibes"); the rest are distinctive enough for substrings.
_TV_RE = re.compile(r"\bTV\b|TELEVIS|SOUNDBAR", re.IGNORECASE)

# Name heuristics, most specific first. Each is a set of case-insensitive words.
_NAME_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("headphones", ("headphone", "headset", "earbud", "earphone", "airpods", "buds", "accentum")),
    ("speaker", ("speaker", "soundlink", "boombox", "sound bar")),
    ("keyboard", ("keyboard", "tastiera")),
    ("mouse", ("mouse",)),
    ("wearable", ("watch", "orolog")),
]

# Advertised 16-bit GATT service UUIDs that reveal a device class, regardless of
# name (the strongest name-independent signal bleak exposes).
_HID_SERVICE = 0x1812  # Human Interface Device -> keyboard/mouse
_FITNESS_SERVICES = (0x180D, 0x1814, 0x1816)  # heart rate / running / cycling
_LE_AUDIO_SERVICES = (0x1850, 0x184E, 0x1855, 0x1844)  # PACS/ASCS/TMAS/VCS


def _matches_word(name: str, words: tuple[str, ...]) -> bool:
    lowered = f" {name.lower()} "
    return any(w in lowered for w in words)


def _has_service(service_uuids: frozenset[str], value16: int) -> bool:
    needle = f"0000{value16:04x}-0000-1000-8000-00805f9b34fb"
    short = f"{value16:04x}"
    return any(u in (needle, short) for u in service_uuids)


def address_is_private(address: str) -> bool | None:
    """True if the address looks like a rotating *private* BLE address (the two
    high bits are 00 or 01: non-resolvable / resolvable private), which is
    characteristic of phones and other personal privacy devices; False for a
    public/static (fixed vendor) address; None when the address is not a MAC
    (e.g. the opaque CoreBluetooth UUID on macOS)."""
    parts = address.split(":")
    if len(parts) != 6:
        return None
    try:
        first = int(parts[0], 16)
    except ValueError:
        return None
    return (first >> 6) in (0b00, 0b01)


def _classify_kind(
    company_ids: frozenset[int], service_uuids: frozenset[str], name: str, address: str
) -> str:
    from stavau.core.deviceid import DeviceKind, Observation, classify

    if name and _TV_RE.search(name):
        return "tv"
    for token, words in _NAME_RULES:
        if _matches_word(name, words):
            return token
    if _has_service(service_uuids, _HID_SERVICE):
        return "keyboard"  # generic input device; name would refine to mouse
    if any(_has_service(service_uuids, s) for s in _FITNESS_SERVICES):
        return "wearable"
    if any(_has_service(service_uuids, s) for s in _LE_AUDIO_SERVICES):
        return "headphones"
    kind = classify(
        Observation(company_ids=company_ids, name=name or "", advertisement_count=1)
    ).kind
    if kind not in (DeviceKind.GENERIC, DeviceKind.UNKNOWN):
        return str(kind.value)
    # No vendor id and no behaviour hint: a rotating private MAC strongly
    # suggests a phone / personal device (e.g. an idle Android that advertises
    # nothing identifying but still rotates its address).
    if address_is_private(address):
        return "phone"
    return str(kind.value)


def device_kind_label(
    company_ids: frozenset[int],
    service_uuids: frozenset[str] = frozenset(),
    name: str = "",
    address: str = "",
) -> str:
    """Human-friendly device type ('🍎 Apple', '📺 TV', '🎧 Headphones', ...).

    Layers, most reliable first: the advertised name (TV / headphones / speaker
    / keyboard / mouse / watch), then behaviour from advertised service UUIDs
    (HID, fitness, LE audio), then the vendor company id, and finally a rotating
    private MAC as a weak "this is a phone" hint. See the module docstring notes
    in core/deviceid and docs/device-compatibility for why names are so often
    absent.
    """
    token = _classify_kind(company_ids, service_uuids, name, address)
    emoji = _KIND_EMOJI.get(token, "\U0001f535")
    return f"{emoji} {tr(_KIND_KEYS.get(token, 'device.kind.unknown'))}"


def estimate_distance(rssi: float, rssi_at_1m: float, path_loss_exponent: float) -> float | None:
    """Estimate distance (m) from RSSI using the calibration model. None if the
    model parameters are implausible (e.g. never calibrated to a valid range)."""
    from stavau.core.distance import CalibrationModel

    try:
        model = CalibrationModel(rssi_at_1m=rssi_at_1m, path_loss_exponent=path_loss_exponent)
    except ValueError:
        return None
    return model.distance_m(rssi)


def format_scan_rows(
    devices: list[DiscoveredDevice],
    rssi_at_1m: float = -59.0,
    path_loss_exponent: float = 2.0,
) -> list[ScanRow]:
    """Sort discovered devices strongest-signal-first and adapt them for the table.

    Each row is enriched with a device-kind label (from advertised company IDs)
    and an estimated distance (from the current calibration), so the picker
    shows *what* a device is and *how far* it is, not just a raw address.
    """
    rows = [
        ScanRow(
            address=d.address,
            name=d.name,
            rssi=float(d.rssi),
            kind_label=device_kind_label(d.company_ids, d.service_uuids, d.name, d.address),
            distance_m=estimate_distance(float(d.rssi), rssi_at_1m, path_loss_exponent),
        )
        for d in devices
    ]
    rows.sort(key=lambda r: r.rssi, reverse=True)
    return rows


def format_nearby_rows(devices: list[NearbyDevice]) -> list[ScanRow]:
    """Same shape as format_scan_rows, for the live NearbyCache listing."""
    rows = [ScanRow(address=d.address, name=d.name, rssi=float(d.rssi)) for d in devices]
    rows.sort(key=lambda r: r.rssi, reverse=True)
    return rows


def format_rssi(rssi: float) -> str:
    return f"{rssi:.0f} dBm"


def format_device_name(name: str) -> str:
    """Show a friendly '(no name)' instead of the raw '<unnamed>' sentinel.

    Most phones/earbuds omit their name from BLE advertisements (privacy +
    packet size), so this is the common case — Type and Distance identify them.
    """
    if not name or name == "<unnamed>":
        return tr("device.unnamed")
    return name


def format_distance(distance_m: float | None) -> str:
    """Compact estimated-distance label for the picker ('0.4 m', '~2 m', '?')."""
    if distance_m is None:
        return "?"
    if distance_m < 1.0:
        return f"{distance_m:.1f} m"
    return f"~{distance_m:.0f} m"


# ---------------------------------------------------------------- calibration wizard


@dataclass(frozen=True)
class CalibrationStationResult:
    distance_m: float
    sample_count: int
    median_rssi: float | None
    ok: bool
    message: str


def summarize_station(distance_m: float, samples: list[float]) -> CalibrationStationResult:
    """Describe one calibration station's outcome without crashing on too few samples."""
    import statistics

    if len(samples) < 3:
        return CalibrationStationResult(
            distance_m=distance_m,
            sample_count=len(samples),
            median_rssi=None,
            ok=False,
            message=tr("calibration.station_skipped", count=len(samples), distance=distance_m),
        )
    median = float(statistics.median(samples))
    return CalibrationStationResult(
        distance_m=distance_m,
        sample_count=len(samples),
        median_rssi=median,
        ok=True,
        message=tr(
            "calibration.station_ok", distance=distance_m, median=median, count=len(samples)
        ),
    )


@dataclass(frozen=True)
class CalibrationOutcome:
    ok: bool
    message: str
    rssi_at_1m: float | None = None
    path_loss_exponent: float | None = None


def summarize_calibration_fit(
    stations: list[CalibrationStationResult],
) -> CalibrationOutcome:
    """Fit the path-loss model from station results, reporting failures as text.

    Mirrors cli.py::_run_calibration's graceful-degradation behaviour: usable
    stations only, a single-station fallback, and a plain message (never a
    raised exception) when nothing usable was collected.
    """
    from stavau.core.calibrate import fit_model

    usable = [(s.distance_m, s.median_rssi) for s in stations if s.ok and s.median_rssi is not None]
    if not usable:
        return CalibrationOutcome(
            ok=False,
            message=tr("calibration.fit_no_samples"),
        )
    try:
        model = fit_model(usable)
    except ValueError as exc:
        if len(usable) > 1:
            try:
                model = fit_model(usable[:1])
            except ValueError:
                return CalibrationOutcome(
                    ok=False, message=tr("calibration.fit_rejected", error=exc)
                )
        else:
            return CalibrationOutcome(ok=False, message=tr("calibration.fit_rejected", error=exc))
    return CalibrationOutcome(
        ok=True,
        message=tr(
            "calibration.fit_ok",
            rssi=model.rssi_at_1m,
            exponent=model.path_loss_exponent,
        ),
        rssi_at_1m=model.rssi_at_1m,
        path_loss_exponent=model.path_loss_exponent,
    )


# ---------------------------------------------------------------- icon color (taskbar + tray)

# Distance-graded palette, shared by the GUI's window/tray icon. Kept as
# plain RGB tuples (no Qt/PIL types) so this stays a pure, Qt-free function.
ICON_BLUE: tuple[int, int, int] = (0, 120, 215)  # no trusted device configured yet
ICON_GREY: tuple[int, int, int] = (128, 128, 128)  # device set, no signal / radio off
ICON_GREEN: tuple[int, int, int] = (56, 176, 72)  # comfortably within radius
ICON_YELLOW: tuple[int, int, int] = (230, 200, 0)  # inner band of the radius
ICON_ORANGE: tuple[int, int, int] = (240, 140, 0)  # beyond radius, leaving/grace running
ICON_RED: tuple[int, int, int] = (204, 62, 52)  # away: locked / fail-safe fired
ICON_PAUSED: tuple[int, int, int] = (150, 90, 200)  # guardrail paused (purple, pause bars)

IconToken = Literal["paused"]


def icon_color(
    tick_or_none: Tick | None, radius_m: float, has_device: bool
) -> tuple[int, int, int] | IconToken:
    """Pure decision function for the taskbar/tray icon colour.

    Precedence (highest first), per the distance-graded scheme requested:
    1. guardrail paused -> "paused" token (renderer draws purple + pause bars)
    2. away state -> red (locked / fail-safe fired), regardless of distance
    3. no trusted device configured -> blue
    4. no signal (rssi is None, including the BLUETOOTH OFF case) -> grey
    5. otherwise, distance vs. radius bands: green / yellow / orange

    `tick_or_none=None` means "no monitor running": blue if no device is
    configured yet, grey if one is configured but idle.
    """
    if tick_or_none is not None and tick_or_none.breaker_paused:
        return "paused"
    if tick_or_none is not None and tick_or_none.state is PresenceState.AWAY:
        return ICON_RED
    if not has_device:
        return ICON_BLUE
    if tick_or_none is None or tick_or_none.rssi is None:
        return ICON_GREY
    distance = tick_or_none.distance
    if distance is None:
        return ICON_GREY
    if distance <= 0.6 * radius_m:
        return ICON_GREEN
    if distance <= radius_m:
        return ICON_YELLOW
    return ICON_ORANGE
