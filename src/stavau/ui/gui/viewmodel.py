"""Presentation logic for the GUI, kept free of any Qt import.

Every function here is pure (or operates on plain dataclasses/values) so it can
be unit-tested without a QApplication. Widgets in this package call into these
helpers instead of embedding formatting or validation rules directly — the
GUI stays a thin shell over stavau's core (config/settings.py, core/session.py,
core/monitor.py, core/distance.py, core/calibrate.py, core/deviceid.py).
"""

from __future__ import annotations

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
# table communicates "what is this device" at a glance.
_KIND_EMOJI = {
    "apple": "\U0001f4f1",  # phone
    "android": "\U0001f4f1",  # phone
    "microsoft": "\U0001f4bb",  # laptop
    "wearable": "⌚",  # watch
    "generic": "\U0001f535",  # blue circle
    "unknown": "•",  # bullet
}
_KIND_KEYS = {
    "apple": "device.kind.apple",
    "android": "device.kind.android",
    "microsoft": "device.kind.microsoft",
    "wearable": "device.kind.wearable",
    "generic": "device.kind.generic",
    "unknown": "device.kind.unknown",
}


def device_kind_label(company_ids: frozenset[int], name: str) -> str:
    """Human-friendly device type ('📱 Apple', '⌚ Wearable', ...) from advertised
    company IDs, using the same classifier the setup wizard uses."""
    from stavau.core.deviceid import Observation, classify

    kind = classify(
        Observation(company_ids=company_ids, name=name or "", advertisement_count=1)
    ).kind
    emoji = _KIND_EMOJI.get(kind.value, "•")
    return f"{emoji} {tr(_KIND_KEYS.get(kind.value, 'device.kind.unknown'))}"


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
            kind_label=device_kind_label(d.company_ids, d.name),
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
