# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (v0.2 in progress)
- **Anti-runaway safety guardrail** (`core/breaker.py`): a circuit breaker that pauses locking after `breaker_max_locks` locks within `breaker_window_seconds` (defaults 3 / 120 s), suppressing further locks for `breaker_cooldown_seconds` (300 s) so a bug or flapping signal can never lock the user out of their machine. Configurable, logged, covered by unit and end-to-end tests.
- **Proximity strategy engine** (`core/strategy.py`, `core/classic.py`): a pluggable `ProximitySource` with `ADV_SCAN` (BLE advertisement scanning) and `CLASSIC_LINK` (bonded Bluetooth Classic) strategies. Classic link gives real connection RSSI on Linux (`l2ping` + `hcitool rssi`) and reachability on Windows (WinRT `BluetoothDevice.ConnectionStatus`); unavailable backends fall back to `ADV_SCAN` with a logged reason. `stavau setup --strategy classic_link` forces the channel for idle Android phones that do not advertise.
- **Device intelligence** (`core/deviceid.py`): classifies the trusted device (Apple / Android / Microsoft / wearable / generic) from advertised company IDs and recommends a proximity strategy; setup probes the device, records `device_kind` / `strategy` / `association` in config, and reports them in `stavau status`.
- **Docs:** `docs/os-native-apis.md` (native Bluetooth/lock/lock-state APIs per OS, with sources) and `docs/agent-team-plan.md` (exhaustive technical plan for future contributors: invariants, contracts, workstream decomposition, findings from live testing).
- **Pairing-less and paired association**: `stavau setup --pair` and a new `stavau pair` command attempt BLE bonding (best-effort via bleak) for a stable cross-rotation identity, falling back to pairing-less advertisement association with clear guidance.
- **Shared `MonitorSession`** (`core/session.py`): unifies the `run` and `tray` monitoring loops so fail-safe and guardrail logic live in one place.
- **System-tray preview** (`stavau tray`, optional `[tray]` extra): notification-area padlock coloured by state (near/leaving/returning/away/no-signal) and a paused padlock when the guardrail trips, live tooltip, and a "Nearby devices" picker that retargets the trusted device on the fly.

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
