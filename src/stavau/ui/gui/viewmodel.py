"""Presentation logic for the GUI, kept free of any Qt import.

Every function here is pure (or operates on plain dataclasses/values) so it can
be unit-tested without a QApplication. Widgets in this package call into these
helpers instead of embedding formatting or validation rules directly — the
GUI stays a thin shell over stavau's core (config/settings.py, core/session.py,
core/monitor.py, core/distance.py, core/calibrate.py, core/deviceid.py).
"""

from __future__ import annotations

from dataclasses import dataclass

from stavau.config.settings import ConfigError, Settings
from stavau.core.monitor import DiscoveredDevice, NearbyDevice
from stavau.core.presence import PresenceState
from stavau.core.session import Tick

# ---------------------------------------------------------------- status text


def format_status(tick: Tick) -> str:
    """Render one status line from a Tick, mirroring the tray's text rules.

    Precedence matches ui/tray.py::TrayApp._on_tick: guardrail pause first,
    then radio-off, then no-signal, then the normal state/distance/rssi line.
    """
    if tick.breaker_paused:
        return f"guardrail paused - {tick.breaker_seconds_remaining:.0f} s left"
    if tick.rssi is None and tick.radio_off:
        return f"{tick.state.value} - BLUETOOTH OFF"
    if tick.rssi is None:
        return f"{tick.state.value} - no signal"
    return f"{tick.state.value} - {tick.distance:.1f} m ({tick.rssi:.0f} dBm)"


# A short label for the state, used to colour or badge the status line.
_STATE_LABELS: dict[PresenceState, str] = {
    PresenceState.NEAR: "near",
    PresenceState.LEAVING: "leaving",
    PresenceState.AWAY: "away",
    PresenceState.RETURNING: "returning",
}


def state_label(state: PresenceState) -> str:
    return _STATE_LABELS[state]


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
        return (
            "classic_link on Windows reports reachability only (connected / out of "
            "range), not a metric distance. The radius slider has no effect here; "
            "the lock fires on disconnect, similar to Windows Dynamic Lock."
        )
    return (
        "classic_link reports real RSSI on this platform, so the radius slider "
        "behaves as a metric threshold, same as adv_scan."
    )


# ---------------------------------------------------------------- settings validation


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    message: str = ""


def validate_settings_message(settings: Settings) -> ValidationResult:
    """Run Settings.validate() and translate a ConfigError into a UI message.

    Never raises: callers can always show `result.message` directly.
    """
    try:
        settings.validate()
    except ConfigError as exc:
        return ValidationResult(ok=False, message=str(exc))
    return ValidationResult(ok=True, message="Settings are valid.")


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


def format_scan_rows(devices: list[DiscoveredDevice]) -> list[ScanRow]:
    """Sort discovered devices strongest-signal-first and adapt them for the table.

    scan_devices() already sorts, but the view model does not trust callers to
    preserve that invariant (e.g. after future filtering) — sort again here.
    """
    rows = [ScanRow(address=d.address, name=d.name, rssi=float(d.rssi)) for d in devices]
    rows.sort(key=lambda r: r.rssi, reverse=True)
    return rows


def format_nearby_rows(devices: list[NearbyDevice]) -> list[ScanRow]:
    """Same shape as format_scan_rows, for the live NearbyCache listing."""
    rows = [ScanRow(address=d.address, name=d.name, rssi=float(d.rssi)) for d in devices]
    rows.sort(key=lambda r: r.rssi, reverse=True)
    return rows


def format_rssi(rssi: float) -> str:
    return f"{rssi:.0f} dBm"


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
            message=(
                f"Only {len(samples)} advertisement(s) received at {distance_m:g} m - "
                "device not advertising or out of range. This station will be skipped."
            ),
        )
    median = float(statistics.median(samples))
    return CalibrationStationResult(
        distance_m=distance_m,
        sample_count=len(samples),
        median_rssi=median,
        ok=True,
        message=f"Median RSSI at {distance_m:g} m: {median:.0f} dBm ({len(samples)} samples).",
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
            message="Calibration failed: no usable samples were collected. Move closer to "
            "the device or check that Bluetooth is on, then try again.",
        )
    try:
        model = fit_model(usable)
    except ValueError as exc:
        if len(usable) > 1:
            try:
                model = fit_model(usable[:1])
            except ValueError:
                return CalibrationOutcome(ok=False, message=f"Calibration fit rejected: {exc}")
        else:
            return CalibrationOutcome(ok=False, message=f"Calibration fit rejected: {exc}")
    return CalibrationOutcome(
        ok=True,
        message=(
            f"Calibrated: rssi_at_1m={model.rssi_at_1m:.1f} dBm, "
            f"path loss exponent n={model.path_loss_exponent:.2f}"
        ),
        rssi_at_1m=model.rssi_at_1m,
        path_loss_exponent=model.path_loss_exponent,
    )
