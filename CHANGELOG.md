# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Smart-home / mesh integration** (`core/integration.py`, optional `[integration]` extra, **off by default, local-LAN MQTT only**): a boundary to Home Assistant that reaches Matter, Z-Wave, Thread and Wi-Fi presence without embedding any radio stack. Two directions: consume an external presence signal as the `external_presence` proximity strategy (fail-safe — absent/unknown/lost-connection never keep the screen unlocked), and emit `locked`/`unlocked` events to an MQTT topic so home-automation routines can react (emission never affects locking). MQTT password read from `$STAVAU_MQTT_PASSWORD`, never stored. Threat-model T11; see `docs/integrations.md`. Invariant I3 refined: no network by default, this is the only opt-in, local-only exception.

## [0.3.0] - 2026-07-06

The graphical milestone: a real app, richer strategies, and safe auto-unlock.

### Added
- **Graphical interface** (`stavau gui`, optional `[gui]` extra, PySide6): device scan/picker, radius slider and settings editor, live monitor panel, calibration wizard — a thin shell over the existing core with a Qt-free, fully tested viewmodel.
- **Internationalization** (`stavau/i18n/`): JSON translation catalogs with OS-language auto-detection and English fallback; **Italian included**, community-extensible by dropping a catalog file. Persisted `language` setting with a GUI selector.
- **State-coloured app icon** (taskbar + tray): distance-graded — blue (no device), grey (no signal), green/yellow/orange by distance band, red (away), purple (guardrail paused).
- **Safe auto-unlock on return** (`core/autounlock.py`, `platform/unlock.py`; off by default): unlocks the screen when the trusted device comes back, gated by a threat-model-first policy — explicit acknowledged opt-in, a paired device, Linux only (`loginctl unlock-session`; Windows/macOS have no public unlock API and the feature refuses there), unlocks only a lock stavau itself caused (never a manual `Win+L`), requires the device within a stricter fraction of the radius continuously for a dwell period, and never unlocks without positive proximity evidence. `stavau setup --enable-auto-unlock --i-understand-the-risk`. Threat-model T9 documents the full policy.
- **adv_monitor strategy** (`core/advmonitor.py`, Linux): controller-offloaded presence via BlueZ AdvertisementMonitor1 with RSSI thresholds derived from the calibrated safety radius; degrades internally to advertisement scanning when unsupported, and a bus-liveness watchdog stops synthesized presence if BlueZ dies (fail-safe).
- **gatt_link strategy** (`core/gattlink.py`, macOS/Linux): RSSI polled over a held BLE connection with adaptive battery-friendly intervals and capped reconnect backoff; unsupported on Windows (no public connected-RSSI API) with honest fallback.
- `CalibrationModel.rssi_at`: inverse of the distance model, used to map the safety radius to controller-side RSSI thresholds.

## [0.2.0] - 2026-07-06

Cross-OS locking, a closed feedback loop, the strategy engine, and the guardrail.

### Added
- **macOS lock backend** (`platform/macos.py`): `CGSession -suspend` with `pmset displaysleepnow` fallback, mirroring the Linux attempt-recording chain — the lock action now covers all three OSes.
- **Lock-state feedback (closed loop)**: `LockStateObserver` contract (`platform/lockstate.py`) wired into the monitoring session — redundant locks are skipped only on an affirmative already-locked state (unknown/error never suppresses locking); real `session_locked`/`session_unlocked` transitions are logged; `Tick` exposes `screen_locked`. Per-OS backends: Windows (WTS session notifications via a ctypes message-only window), Linux (systemd-logind `LockedHint` + signals via dbus-fast), macOS (`com.apple.screenIsLocked` distributed notifications via the new optional `[macos]` extra).
- **Radio-off detection** (`core/radiostate.py`): when the signal is stale and the Bluetooth adapter is off (WinRT Radios on Windows, `bluetoothctl` on Linux), CLI and tray show "BLUETOOTH OFF" instead of a generic no-signal; explanation only — the staleness fail-safe lock is unchanged.
- **Packaging & release**: tag-triggered GitHub Actions release workflow (3-OS PyInstaller bundles, SHA256 checksums), `packaging/` spec + systemd user unit + macOS LaunchAgent samples, and `docs/install.md` (pipx, bundles, autostart per OS).
- **Hardware test protocol** (`docs/hw-test-protocol.md`): 8 executable checklists turning the acceptance criteria into step-by-step procedures with evidence capture.
- **Anti-runaway safety guardrail** (`core/breaker.py`): a circuit breaker that pauses locking after `breaker_max_locks` locks within `breaker_window_seconds` (defaults 3 / 120 s), suppressing further locks for `breaker_cooldown_seconds` (300 s) so a bug or flapping signal can never lock the user out of their machine. Configurable, logged, covered by unit and end-to-end tests.
- **Proximity strategy engine** (`core/strategy.py`, `core/classic.py`): a pluggable `ProximitySource` with `ADV_SCAN` (BLE advertisement scanning) and `CLASSIC_LINK` (bonded Bluetooth Classic) strategies. Classic link gives real connection RSSI on Linux (`l2ping` + `hcitool rssi`) and reachability on Windows (WinRT `BluetoothDevice.ConnectionStatus`); unavailable backends fall back to `ADV_SCAN` with a logged reason. `stavau setup --strategy classic_link` forces the channel for idle Android phones that do not advertise.
- **Device intelligence** (`core/deviceid.py`): classifies the trusted device (Apple / Android / Microsoft / wearable / generic) from advertised company IDs and recommends a proximity strategy; setup probes the device, records `device_kind` / `strategy` / `association` in config, and reports them in `stavau status`.
- **Docs:** `docs/os-native-apis.md` (native Bluetooth/lock/lock-state APIs per OS, with sources) and `docs/agent-team-plan.md` (exhaustive technical plan for future contributors: invariants, contracts, workstream decomposition, findings from live testing).
- **Docs:** `docs/agent-orchestration.md` — multi-agent execution plan: orchestrator/integrator/reviewer topology, model-per-task map, anti-waste rules, wave scheduling with the hardware-in-the-loop constraint ([AV]/[HV] DoD split), and ready-to-launch Wave 1 task cards.
- **Pairing-less and paired association**: `stavau setup --pair` and a new `stavau pair` command attempt BLE bonding (best-effort via bleak) for a stable cross-rotation identity, falling back to pairing-less advertisement association with clear guidance.
- **Shared `MonitorSession`** (`core/session.py`): unifies the `run` and `tray` monitoring loops so fail-safe and guardrail logic live in one place.
- **System-tray preview** (`stavau tray`, optional `[tray]` extra): notification-area padlock coloured by state (near/leaving/returning/away/no-signal) and a paused padlock when the guardrail trips, live tooltip, and a "Nearby devices" picker that retargets the trusted device on the fly.

## [0.1.0] - 2026-07-05

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

[0.3.0]: https://github.com/davidebr90/stavau/releases/tag/v0.3.0
[0.2.0]: https://github.com/davidebr90/stavau/releases/tag/v0.2.0
