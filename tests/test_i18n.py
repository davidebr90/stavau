"""Tests for stavau.i18n: catalog loading, fallback chain, OS detection.

No Qt import anywhere in this module or in stavau.i18n itself - the package
must be usable from the CLI as well as the GUI.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import stavau.i18n as i18n_module
from stavau.i18n import (
    available_languages,
    detect_os_language,
    get_language,
    resolve_language,
    set_language,
    tr,
)

# The autouse `_reset_i18n_language` fixture in conftest.py resets to English
# before and after every test in the whole suite, so no fixture is needed here.

# ---------------------------------------------------------------- catalog loading


def test_available_languages_includes_en_and_it() -> None:
    langs = available_languages()
    assert "en" in langs
    assert "it" in langs


def test_available_languages_is_sorted() -> None:
    langs = available_languages()
    assert langs == sorted(langs)


def test_available_languages_from_dir_discovers_new_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A new <code>.json file must be picked up purely by being present - no registration."""
    (tmp_path / "fr.json").write_text('{"a": "b"}', encoding="utf-8")
    monkeypatch.setattr(i18n_module, "_CATALOGS_DIR", tmp_path)
    assert "fr" in available_languages()


def test_available_languages_missing_dir_falls_back_to_en(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(i18n_module, "_CATALOGS_DIR", tmp_path / "does-not-exist")
    assert available_languages() == ["en"]


# ---------------------------------------------------------------- tr() fallback chain


def test_tr_returns_english_by_default() -> None:
    assert get_language() == "en"
    assert tr("tab.device") == "Device"


def test_tr_switches_language() -> None:
    set_language("it")
    assert tr("tab.device") == "Dispositivo"


def test_tr_unknown_key_falls_back_to_key_itself() -> None:
    assert tr("this.key.does.not.exist.anywhere") == "this.key.does.not.exist.anywhere"


def test_tr_missing_catalog_falls_back_to_english() -> None:
    set_language("xx-does-not-exist")
    assert tr("tab.device") == "Device"


def test_tr_key_missing_in_active_language_falls_back_to_english(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A catalog present but missing one key must fall back to EN for that key only."""
    (tmp_path / "en.json").write_text(json.dumps({"only_in_en": "hello"}), encoding="utf-8")
    (tmp_path / "zz.json").write_text(json.dumps({}), encoding="utf-8")
    monkeypatch.setattr(i18n_module, "_CATALOGS_DIR", tmp_path)
    monkeypatch.setattr(i18n_module, "_catalog_cache", {})
    set_language("zz")
    assert tr("only_in_en") == "hello"


def test_tr_formats_kwargs() -> None:
    assert tr("device.scan_found", count=3) == "Found 3 device(s), strongest signal first."


def test_tr_formatting_error_falls_back_to_raw_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key requiring a field the caller didn't pass must not raise."""
    (tmp_path / "en.json").write_text(json.dumps({"needs_field": "hi {missing}"}), encoding="utf-8")
    monkeypatch.setattr(i18n_module, "_CATALOGS_DIR", tmp_path)
    monkeypatch.setattr(i18n_module, "_catalog_cache", {})
    set_language("en")
    assert tr("needs_field", other="x") == "hi {missing}"


def test_tr_broken_json_catalog_degrades_to_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "en.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(i18n_module, "_CATALOGS_DIR", tmp_path)
    monkeypatch.setattr(i18n_module, "_catalog_cache", {})
    set_language("broken")
    assert tr("anything") == "anything"


# ---------------------------------------------------------------- OS language detection


def test_detect_os_language_maps_it_it_to_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n_module.locale, "getlocale", lambda: ("it_IT", "UTF-8"))
    assert detect_os_language() == "it"


def test_detect_os_language_maps_en_us_to_en(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n_module.locale, "getlocale", lambda: ("en_US", "UTF-8"))
    assert detect_os_language() == "en"


def test_detect_os_language_handles_hyphenated_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n_module.locale, "getlocale", lambda: ("it-IT", "UTF-8"))
    assert detect_os_language() == "it"


def test_detect_os_language_none_locale_falls_back_to_getdefaultlocale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i18n_module.locale, "getlocale", lambda: (None, None))
    monkeypatch.setattr(
        i18n_module.locale, "getdefaultlocale", lambda: ("it_IT", "cp1252"), raising=False
    )
    assert detect_os_language() == "it"


def test_detect_os_language_total_failure_falls_back_to_en(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise() -> tuple[str | None, str | None]:
        raise ValueError("no locale")

    monkeypatch.setattr(i18n_module.locale, "getlocale", _raise)
    monkeypatch.setattr(i18n_module.locale, "getdefaultlocale", _raise, raising=False)
    assert detect_os_language() == "en"


def test_detect_os_language_unmapped_prefix_returned_lowercased(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i18n_module.locale, "getlocale", lambda: ("FR_FR", "UTF-8"))
    assert detect_os_language() == "fr"


# ---------------------------------------------------------------- resolve_language


def test_resolve_language_auto_detects_available_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(i18n_module, "detect_os_language", lambda: "it")
    assert resolve_language("auto") == "it"


def test_resolve_language_auto_falls_back_to_en_when_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(i18n_module, "detect_os_language", lambda: "fr")
    assert resolve_language("auto") == "en"


def test_resolve_language_explicit_code_passes_through() -> None:
    assert resolve_language("it") == "it"
    assert resolve_language("xx-not-a-real-language") == "xx-not-a-real-language"
