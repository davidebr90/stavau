# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffolding: repository structure, governance docs, CI matrix (Windows/macOS/Linux).
- Software specification: functional & non-functional requirements, architecture, threat model, acceptance criteria (`docs/`).
- Core distance module: log-distance path loss model, RSSI moving-average smoothing with median spike rejection, hysteresis presence state machine (fail-safe on signal loss).
- RSSI→distance calibration fitting (`core/calibrate.py`): least-squares over guided stations, implausible-fit rejection, single-station fallback.
- BLE proximity monitoring (`core/monitor.py`) on bleak: discovery scan, raw sampling for calibration, continuous tracking with staleness fail-safe.
- Platform lock layer: `loginctl`/`xdg-screensaver`/D-Bus fallback chain on Linux, `LockWorkStation` on Windows (landed early), macOS explicitly deferred to v0.2.
- Local JSON configuration (atomic writes, schema-versioned, forward-compatible) and size-capped JSONL event log with purge.
- CLI: `stavau setup` (scan/pick/calibrate, non-interactive flags), `stavau run` (`--dry-run`, lock retry on failure), `stavau status`, `stavau log` (`--clear`, `--export`).
- Test suite: 57 unit tests covering distance model, smoothing, presence machine, calibration fit, event log, settings persistence and platform fallback logic.
- Project logos (dark/light GUI variants) with transparent backgrounds (`logo/`), wired into the READMEs with automatic dark/light switching.
- Device compatibility research (`docs/device-compatibility.md`): pairing/bonding analysis for iPhone, Apple Watch, Android and Wear OS; per-PC-platform capability matrix; v0.2 proximity strategy engine design (ADV_SCAN / GATT_LINK / CLASSIC_LINK with per-device auto-selection).

[Unreleased]: https://github.com/davidebr90/stavau/compare/main...HEAD
