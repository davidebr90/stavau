"""Pure-logic tests for the GUI viewmodel: no QApplication, no Qt import.

These tests are the coverage bar for stavau.ui.gui.viewmodel (CARD-E1). The
first test additionally asserts that the module itself can be imported
without pulling in PySide6, since app.py must be the only place Qt is
imported (lazy, from the CLI's `gui` subcommand).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from stavau.config.settings import ConfigError, Settings
from stavau.core.monitor import DiscoveredDevice, NearbyDevice
from stavau.core.presence import PresenceState
from stavau.core.session import Tick
from stavau.i18n import set_language, tr
from stavau.ui.gui import viewmodel as vm

_CATALOGS_DIR = Path(__file__).resolve().parents[1] / "src" / "stavau" / "i18n" / "catalogs"


def test_viewmodel_imports_without_qt() -> None:
    """viewmodel must be importable even if PySide6 is not installed.

    We can't literally uninstall PySide6 in this environment, but we can
    assert that importing viewmodel does not itself import any PySide6/Qt
    module as a side effect.
    """
    qt_modules = [name for name in sys.modules if name.startswith(("PySide6", "PyQt"))]
    # This assertion only has teeth if nothing else in the test session imported
    # Qt first; check the module's own declared dependency graph instead.
    import stavau.ui.gui.viewmodel as reloaded

    assert reloaded.__name__ == "stavau.ui.gui.viewmodel"
    assert not any(mod.startswith(("PySide6", "PyQt")) for mod in _direct_imports(reloaded))
    del qt_modules  # not asserted on directly; see _direct_imports check above


def _direct_imports(module: object) -> list[str]:
    import dis

    code = module.__loader__.get_code(module.__name__)  # type: ignore[attr-defined]
    names: list[str] = []
    for instr in dis.get_instructions(code):
        if instr.opname == "IMPORT_NAME":
            names.append(str(instr.argval))
    return names


def _tick(
    *,
    state: PresenceState = PresenceState.NEAR,
    rssi: float | None = -50.0,
    distance: float | None = 1.5,
    breaker_paused: bool = False,
    breaker_seconds_remaining: float = 0.0,
    radio_off: bool = False,
    screen_locked: bool | None = None,
    elapsed: float = 0.0,
) -> Tick:
    return Tick(
        elapsed=elapsed,
        rssi=rssi,
        distance=distance,
        state=state,
        breaker_paused=breaker_paused,
        breaker_seconds_remaining=breaker_seconds_remaining,
        screen_locked=screen_locked,
        radio_off=radio_off,
    )


# ---------------------------------------------------------------- format_status


def test_format_status_near_with_distance() -> None:
    tick = _tick(state=PresenceState.NEAR, rssi=-55.0, distance=1.23)
    assert vm.format_status(tick) == "near - 1.2 m (-55 dBm)"


def test_format_status_away_with_distance() -> None:
    tick = _tick(state=PresenceState.AWAY, rssi=-80.0, distance=9.87)
    assert vm.format_status(tick) == "away - 9.9 m (-80 dBm)"


def test_format_status_no_signal() -> None:
    tick = _tick(state=PresenceState.AWAY, rssi=None, distance=None, radio_off=False)
    assert vm.format_status(tick) == "away - no signal"


def test_format_status_radio_off() -> None:
    tick = _tick(state=PresenceState.AWAY, rssi=None, distance=None, radio_off=True)
    assert vm.format_status(tick) == "away - BLUETOOTH OFF"


def test_format_status_breaker_paused_takes_precedence() -> None:
    """Guardrail pause must win even if rssi/radio_off would also apply."""
    tick = _tick(
        state=PresenceState.AWAY,
        rssi=None,
        distance=None,
        radio_off=True,
        breaker_paused=True,
        breaker_seconds_remaining=42.0,
    )
    assert vm.format_status(tick) == "guardrail paused - 42 s left"


def test_format_status_leaving_and_returning_states() -> None:
    leaving = _tick(state=PresenceState.LEAVING, rssi=-60.0, distance=3.4)
    returning = _tick(state=PresenceState.RETURNING, rssi=-50.0, distance=1.1)
    assert vm.format_status(leaving) == "leaving - 3.4 m (-60 dBm)"
    assert vm.format_status(returning) == "returning - 1.1 m (-50 dBm)"


def test_state_label_covers_all_states() -> None:
    for state in PresenceState:
        assert vm.state_label(state) == state.value


# ---------------------------------------------------------------- strategy_caveat


def test_caveat_classic_link_windows_is_reachability_warning() -> None:
    text = vm.strategy_caveat("classic_link", "win32")
    assert "reachability" in text.lower()
    assert "no effect" in text.lower() or "not" in text.lower()


def test_caveat_classic_link_linux_is_real_rssi_text() -> None:
    text = vm.strategy_caveat("classic_link", "linux")
    assert "real rssi" in text.lower()
    assert "reachability" not in text.lower()


def test_caveat_classic_link_darwin_is_real_rssi_text() -> None:
    text = vm.strategy_caveat("classic_link", "darwin")
    assert "real rssi" in text.lower()


def test_caveat_adv_scan_has_none() -> None:
    assert vm.strategy_caveat("adv_scan", "win32") == ""
    assert vm.strategy_caveat("adv_scan", "linux") == ""


# ---------------------------------------------------------------- settings validation


def _valid_settings(**overrides: object) -> Settings:
    base = Settings(device_address="AA:BB:CC:DD:EE:FF", device_alias="phone")
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_validate_settings_message_ok() -> None:
    result = vm.validate_settings_message(_valid_settings())
    assert result.ok
    assert result.message


def test_validate_settings_message_no_device() -> None:
    settings = Settings()  # device_address empty
    result = vm.validate_settings_message(settings)
    assert not result.ok
    assert "trusted device" in result.message


def test_validate_settings_message_bad_radius() -> None:
    settings = _valid_settings(radius_m=20.0)
    result = vm.validate_settings_message(settings)
    assert not result.ok
    assert "radius_m" in result.message


def test_validate_settings_message_bad_grace() -> None:
    settings = _valid_settings(grace_seconds=1.0)
    result = vm.validate_settings_message(settings)
    assert not result.ok
    assert "grace_seconds" in result.message


def test_validate_settings_message_never_raises_configerror() -> None:
    settings = _valid_settings(breaker_window_seconds=0.0)
    try:
        result = vm.validate_settings_message(settings)
    except ConfigError:
        raise AssertionError("validate_settings_message must not raise ConfigError") from None
    assert not result.ok


def test_clamp_radius() -> None:
    assert vm.clamp_radius(0.0) == 1.0
    assert vm.clamp_radius(15.0) == 10.0
    assert vm.clamp_radius(5.0) == 5.0


def test_clamp_grace() -> None:
    assert vm.clamp_grace(0.0) == 3.0
    assert vm.clamp_grace(999.0) == 60.0
    assert vm.clamp_grace(10.0) == 10.0


# ---------------------------------------------------------------- scan row formatting


def test_format_scan_rows_sorts_strongest_first() -> None:
    devices = [
        DiscoveredDevice(address="AA", name="weak", rssi=-90),
        DiscoveredDevice(address="BB", name="strong", rssi=-40),
        DiscoveredDevice(address="CC", name="mid", rssi=-60),
    ]
    rows = vm.format_scan_rows(devices)
    assert [r.address for r in rows] == ["BB", "CC", "AA"]


def test_format_scan_rows_empty() -> None:
    assert vm.format_scan_rows([]) == []


def test_format_scan_rows_enriches_kind_and_distance() -> None:
    from stavau.core.deviceid import APPLE_COMPANY_ID

    devices = [
        DiscoveredDevice(
            address="AA", name="phone", rssi=-59, company_ids=frozenset({APPLE_COMPANY_ID})
        )
    ]
    rows = vm.format_scan_rows(devices, rssi_at_1m=-59.0, path_loss_exponent=2.0)
    assert "Apple" in rows[0].kind_label
    # -59 dBm at the 1 m reference -> ~1 m.
    assert rows[0].distance_m is not None
    assert abs(rows[0].distance_m - 1.0) < 0.1


def test_device_kind_label_variants() -> None:
    from stavau.core.deviceid import APPLE_COMPANY_ID, SAMSUNG_COMPANY_ID

    set_language("en")
    assert "Apple" in vm.device_kind_label(frozenset({APPLE_COMPANY_ID}), "")
    assert "Android" in vm.device_kind_label(frozenset({SAMSUNG_COMPANY_ID}), "")
    # No vendor company id but it advertises -> a generic BLE device.
    assert "BLE device" in vm.device_kind_label(frozenset(), "")


def test_format_distance_bands() -> None:
    assert vm.format_distance(None) == "?"
    assert vm.format_distance(0.4) == "0.4 m"
    assert vm.format_distance(2.3) == "~2 m"


def test_estimate_distance_invalid_model_is_none() -> None:
    # path_loss_exponent out of the plausible range -> CalibrationModel rejects.
    assert vm.estimate_distance(-60.0, -59.0, 99.0) is None


def test_format_nearby_rows_sorts_strongest_first() -> None:
    devices = [
        NearbyDevice(address="AA", name="weak", rssi=-90.0, age_seconds=1.0),
        NearbyDevice(address="BB", name="strong", rssi=-40.0, age_seconds=2.0),
    ]
    rows = vm.format_nearby_rows(devices)
    assert [r.address for r in rows] == ["BB", "AA"]


def test_format_rssi() -> None:
    assert vm.format_rssi(-55.4) == "-55 dBm"


# ---------------------------------------------------------------- calibration wizard logic


def test_summarize_station_ok() -> None:
    result = vm.summarize_station(1.0, [-50.0, -52.0, -49.0, -51.0])
    assert result.ok
    assert result.sample_count == 4
    assert result.median_rssi == -50.5
    assert "1 m" in result.message


def test_summarize_station_not_enough_samples() -> None:
    result = vm.summarize_station(3.0, [-60.0])
    assert not result.ok
    assert result.median_rssi is None
    assert "skipped" in result.message


def test_summarize_station_zero_samples_no_crash() -> None:
    result = vm.summarize_station(3.0, [])
    assert not result.ok
    assert result.sample_count == 0


def test_summarize_calibration_fit_two_good_stations() -> None:
    stations = [
        vm.summarize_station(1.0, [-50.0, -51.0, -49.0]),
        vm.summarize_station(3.0, [-65.0, -66.0, -64.0]),
    ]
    outcome = vm.summarize_calibration_fit(stations)
    assert outcome.ok
    assert outcome.rssi_at_1m is not None
    assert outcome.path_loss_exponent is not None


def test_summarize_calibration_fit_one_good_station_falls_back() -> None:
    stations = [
        vm.summarize_station(1.0, [-50.0, -51.0, -49.0]),
        vm.summarize_station(3.0, []),  # not enough samples
    ]
    outcome = vm.summarize_calibration_fit(stations)
    assert outcome.ok
    assert outcome.rssi_at_1m is not None


def test_summarize_calibration_fit_no_usable_stations_message_no_crash() -> None:
    stations = [
        vm.summarize_station(1.0, []),
        vm.summarize_station(3.0, [-60.0]),
    ]
    outcome = vm.summarize_calibration_fit(stations)
    assert not outcome.ok
    assert "no usable samples" in outcome.message.lower()
    assert outcome.rssi_at_1m is None


# ---------------------------------------------------------------- icon_color (CARD-E2 request 2)


def _paused_tick(**overrides: object) -> Tick:
    return _tick(breaker_paused=True, breaker_seconds_remaining=30.0, **overrides)  # type: ignore[arg-type]


def test_icon_color_no_device_configured_is_blue_even_with_no_tick() -> None:
    assert vm.icon_color(None, radius_m=3.0, has_device=False) == vm.ICON_BLUE


def test_icon_color_no_device_configured_is_blue_even_with_a_tick() -> None:
    """Missing device takes precedence over whatever the (stale) tick says."""
    tick = _tick(state=PresenceState.NEAR, rssi=-50.0, distance=1.0)
    assert vm.icon_color(tick, radius_m=3.0, has_device=False) == vm.ICON_BLUE


def test_icon_color_no_monitor_running_device_configured_is_grey() -> None:
    assert vm.icon_color(None, radius_m=3.0, has_device=True) == vm.ICON_GREY


def test_icon_color_no_signal_is_grey() -> None:
    tick = _tick(state=PresenceState.LEAVING, rssi=None, distance=None, radio_off=False)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_GREY


def test_icon_color_bluetooth_off_case_is_also_grey() -> None:
    tick = _tick(state=PresenceState.LEAVING, rssi=None, distance=None, radio_off=True)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_GREY


def test_icon_color_green_band_well_within_radius() -> None:
    tick = _tick(state=PresenceState.NEAR, rssi=-50.0, distance=1.0)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_GREEN


def test_icon_color_green_yellow_boundary_is_green() -> None:
    """distance == 0.6 * radius is inclusive on the green side."""
    radius = 3.0
    tick = _tick(state=PresenceState.NEAR, rssi=-50.0, distance=0.6 * radius)
    assert vm.icon_color(tick, radius_m=radius, has_device=True) == vm.ICON_GREEN


def test_icon_color_just_above_green_boundary_is_yellow() -> None:
    tick = _tick(state=PresenceState.NEAR, rssi=-50.0, distance=1.81)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_YELLOW


def test_icon_color_yellow_band() -> None:
    tick = _tick(state=PresenceState.NEAR, rssi=-50.0, distance=2.5)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_YELLOW


def test_icon_color_yellow_orange_boundary_is_yellow() -> None:
    """distance == radius is inclusive on the yellow side."""
    tick = _tick(state=PresenceState.LEAVING, rssi=-70.0, distance=3.0)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_YELLOW


def test_icon_color_just_above_radius_is_orange() -> None:
    tick = _tick(state=PresenceState.LEAVING, rssi=-70.0, distance=3.01)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_ORANGE


def test_icon_color_orange_band_leaving_grace() -> None:
    tick = _tick(state=PresenceState.LEAVING, rssi=-80.0, distance=5.0)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_ORANGE


def test_icon_color_away_state_is_red_regardless_of_distance() -> None:
    """Away (locked/fail-safe) wins over the distance bands entirely."""
    tick = _tick(state=PresenceState.AWAY, rssi=-40.0, distance=0.1)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_RED


def test_icon_color_away_state_with_no_signal_is_still_red() -> None:
    tick = _tick(state=PresenceState.AWAY, rssi=None, distance=None, radio_off=True)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == vm.ICON_RED


def test_icon_color_paused_takes_precedence_over_away() -> None:
    tick = _paused_tick(state=PresenceState.AWAY, rssi=-40.0, distance=0.1)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == "paused"


def test_icon_color_paused_takes_precedence_over_no_device() -> None:
    tick = _paused_tick(state=PresenceState.NEAR, rssi=-40.0, distance=0.1)
    assert vm.icon_color(tick, radius_m=3.0, has_device=False) == "paused"


def test_icon_color_paused_takes_precedence_over_no_signal() -> None:
    tick = _paused_tick(state=PresenceState.LEAVING, rssi=None, distance=None)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == "paused"


def test_icon_color_paused_takes_precedence_over_normal_bands() -> None:
    tick = _paused_tick(state=PresenceState.NEAR, rssi=-50.0, distance=1.0)
    assert vm.icon_color(tick, radius_m=3.0, has_device=True) == "paused"


def test_icon_color_tokens_are_distinct() -> None:
    tokens = {
        vm.ICON_BLUE,
        vm.ICON_GREY,
        vm.ICON_GREEN,
        vm.ICON_YELLOW,
        vm.ICON_ORANGE,
        vm.ICON_RED,
        vm.ICON_PAUSED,
    }
    assert len(tokens) == 7


# ---------------------------------------------------------------- i18n-backed viewmodel strings


def test_catalogs_have_identical_key_sets() -> None:
    en = json.loads((_CATALOGS_DIR / "en.json").read_text(encoding="utf-8"))
    it = json.loads((_CATALOGS_DIR / "it.json").read_text(encoding="utf-8"))
    assert set(en.keys()) == set(it.keys())


def test_format_status_english_default() -> None:
    tick = _tick(state=PresenceState.NEAR, rssi=-55.0, distance=1.23)
    assert vm.format_status(tick) == "near - 1.2 m (-55 dBm)"


def test_format_status_italian() -> None:
    set_language("it")
    tick = _tick(state=PresenceState.NEAR, rssi=-55.0, distance=1.23)
    assert vm.format_status(tick) == "vicino - 1.2 m (-55 dBm)"


def test_format_status_italian_no_signal() -> None:
    set_language("it")
    tick = _tick(state=PresenceState.AWAY, rssi=None, distance=None, radio_off=False)
    assert vm.format_status(tick) == "assente - nessun segnale"


def test_format_status_italian_bluetooth_off() -> None:
    set_language("it")
    tick = _tick(state=PresenceState.AWAY, rssi=None, distance=None, radio_off=True)
    assert vm.format_status(tick) == "assente - BLUETOOTH SPENTO"


def test_format_status_italian_breaker_paused() -> None:
    set_language("it")
    tick = _tick(
        state=PresenceState.AWAY,
        rssi=None,
        distance=None,
        breaker_paused=True,
        breaker_seconds_remaining=42.0,
    )
    assert vm.format_status(tick) == "protezione in pausa - 42 s rimanenti"


def test_state_label_italian() -> None:
    set_language("it")
    assert vm.state_label(PresenceState.NEAR) == "vicino"
    assert vm.state_label(PresenceState.LEAVING) == "in allontanamento"
    assert vm.state_label(PresenceState.AWAY) == "assente"
    assert vm.state_label(PresenceState.RETURNING) == "in ritorno"


def test_strategy_caveat_italian_windows() -> None:
    set_language("it")
    text = vm.strategy_caveat("classic_link", "win32")
    assert "raggiungibilità" in text.lower()


def test_strategy_caveat_italian_linux() -> None:
    set_language("it")
    text = vm.strategy_caveat("classic_link", "linux")
    assert "rssi reale" in text.lower()


def test_validate_settings_message_ok_italian() -> None:
    set_language("it")
    result = vm.validate_settings_message(_valid_settings())
    assert result.ok
    assert result.message == "Le impostazioni sono valide."


def test_summarize_station_italian() -> None:
    set_language("it")
    result = vm.summarize_station(1.0, [-50.0, -52.0, -49.0, -51.0])
    assert result.ok
    assert "campioni" in result.message


def test_summarize_station_skipped_italian() -> None:
    set_language("it")
    result = vm.summarize_station(3.0, [-60.0])
    assert not result.ok
    assert "saltata" in result.message.lower()


def test_summarize_calibration_fit_italian() -> None:
    set_language("it")
    stations = [
        vm.summarize_station(1.0, [-50.0, -51.0, -49.0]),
        vm.summarize_station(3.0, [-65.0, -66.0, -64.0]),
    ]
    outcome = vm.summarize_calibration_fit(stations)
    assert outcome.ok
    assert "Calibrato" in outcome.message


def test_summarize_calibration_fit_no_samples_italian() -> None:
    set_language("it")
    stations = [vm.summarize_station(1.0, []), vm.summarize_station(3.0, [])]
    outcome = vm.summarize_calibration_fit(stations)
    assert not outcome.ok
    assert "non riuscita" in outcome.message.lower()


def test_tr_direct_smoke_test_both_languages() -> None:
    assert tr("tab.settings") == "Settings"
    set_language("it")
    assert tr("tab.settings") == "Impostazioni"


def test_theme_stylesheet_is_non_empty_and_uses_palette_colors() -> None:
    from stavau.ui.gui.theme import DARK, LIGHT, build_stylesheet

    for palette in (LIGHT, DARK):
        css = build_stylesheet(palette)
        assert len(css) > 500
        assert palette.primary in css
        assert palette.window in css
        assert "QPushButton#Primary" in css
        assert "#Sidebar" in css


def test_theme_light_and_dark_differ() -> None:
    from stavau.ui.gui.theme import DARK, LIGHT, build_stylesheet

    assert build_stylesheet(LIGHT) != build_stylesheet(DARK)
