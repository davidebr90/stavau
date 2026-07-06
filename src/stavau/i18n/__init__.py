"""Minimal, dependency-free internationalization for stavau.

Design goals:
- No Qt dependency: usable from the CLI as well as the GUI.
- Community-extensible: dropping `xx.json` in `i18n/catalogs/` adds a
  language; `available_languages()` discovers catalogs by globbing the
  directory, nothing needs to be registered in code.
- Never raises and never shows a blank string: an unknown key falls back to
  the English catalog, then to the raw key itself, so a missing translation
  degrades to "ugly but informative" instead of crashing the UI.

Catalogs are flat JSON objects (`{"key": "value", ...}`), UTF-8 encoded.
Format placeholders use `str.format`-style `{name}` fields, applied via the
`**fmt` kwargs of `tr()`.
"""

from __future__ import annotations

import json
import locale
from pathlib import Path
from typing import Final

_DEFAULT_LANGUAGE: Final[str] = "en"

_CATALOGS_DIR: Final[Path] = Path(__file__).resolve().parent / "catalogs"

# OS locale prefixes (e.g. "it_IT", "it-IT", "it") mapped to a catalog code.
# Extend this table as new catalogs are added; an unmapped prefix falls back
# to using the prefix itself (lowercased) as the language code, which still
# degrades gracefully via the EN fallback in tr() if no such catalog exists.
_LOCALE_PREFIX_MAP: Final[dict[str, str]] = {
    "it": "it",
    "en": "en",
}

_current_language: str = _DEFAULT_LANGUAGE
_catalog_cache: dict[str, dict[str, str]] = {}


def _load_catalog(code: str) -> dict[str, str]:
    """Load and cache one language's catalog; missing/broken files -> {}."""
    if code in _catalog_cache:
        return _catalog_cache[code]
    path = _CATALOGS_DIR / f"{code}.json"
    catalog: dict[str, str] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                catalog = {str(k): str(v) for k, v in raw.items()}
        except (OSError, json.JSONDecodeError):
            catalog = {}
    _catalog_cache[code] = catalog
    return catalog


def available_languages() -> list[str]:
    """List language codes with a catalog file, sorted, `en` always included.

    Community contributors add a language purely by dropping a new
    `<code>.json` file in `i18n/catalogs/` - no code change required.
    """
    if not _CATALOGS_DIR.is_dir():
        return [_DEFAULT_LANGUAGE]
    codes = {p.stem for p in _CATALOGS_DIR.glob("*.json")}
    codes.add(_DEFAULT_LANGUAGE)
    return sorted(codes)


def set_language(code: str) -> None:
    """Set the active language for subsequent `tr()` calls.

    An unknown/unavailable code is accepted as-is (tr() will simply fall
    back to English for every key) rather than raising - callers should not
    have to validate against `available_languages()` before switching.
    """
    global _current_language
    _current_language = code


def get_language() -> str:
    """Return the currently active language code."""
    return _current_language


def tr(key: str, **fmt: object) -> str:
    """Translate `key` in the active language, formatting with `**fmt`.

    Fallback chain: active-language catalog -> English catalog -> the key
    itself. A key present but missing its format fields (or a formatting
    error) still returns the best available raw string rather than raising.
    """
    text = _catalog_cache_lookup(_current_language, key)
    if text is None and _current_language != _DEFAULT_LANGUAGE:
        text = _catalog_cache_lookup(_DEFAULT_LANGUAGE, key)
    if text is None:
        text = key
    if fmt:
        try:
            return text.format(**fmt)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def _catalog_cache_lookup(code: str, key: str) -> str | None:
    return _load_catalog(code).get(key)


def detect_os_language() -> str:
    """Best-effort OS UI language detection, without any Qt dependency.

    Tries `locale.getlocale()` first (works cross-platform in modern
    Python), then falls back to the deprecated `getdefaultlocale()` for
    older/edge environments. Any failure - or a locale we cannot map - falls
    back to the default language rather than raising, since this feeds the
    "auto" setting on every startup path (GUI and, later, CLI).
    """
    raw: str | None = None
    try:
        lang, _encoding = locale.getlocale()
        raw = lang
    except (ValueError, TypeError):
        raw = None
    if not raw:
        try:
            lang2, _enc2 = locale.getdefaultlocale()
            raw = lang2
        except (ValueError, TypeError, AttributeError):
            raw = None
    if not raw:
        return _DEFAULT_LANGUAGE
    prefix = raw.replace("-", "_").split("_", 1)[0].lower()
    return _LOCALE_PREFIX_MAP.get(prefix, prefix)


def resolve_language(setting: str) -> str:
    """Resolve a Settings.language value ("auto" or an explicit code).

    "auto" maps to `detect_os_language()` when that yields a language with an
    actual catalog, otherwise English. An explicit code is passed through
    unchanged (tr() degrades gracefully even if the catalog is missing).
    """
    if setting != "auto":
        return setting
    detected = detect_os_language()
    return detected if detected in available_languages() else _DEFAULT_LANGUAGE


__all__ = [
    "available_languages",
    "detect_os_language",
    "get_language",
    "resolve_language",
    "set_language",
    "tr",
]
