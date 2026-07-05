# stavau — Technical Architecture

## Overview

```
                ┌───────────────────────────────────────────────────────────┐
                │                       stavau daemon                       │
                │                                                           │
 BLE (bonded)   │ ┌──────────┐   ┌───────────┐   ┌────────────┐  ┌────────┐ │
 phone/watch ◄──┼─┤ monitor  ├──►│ smoothing ├──►│ presence   ├─►│ lock   │ │
                │ │ (bleak)  │   │ + distance│   │ state      │  │ action │ │
                │ └──────────┘   │ model     │   │ machine    │  └───┬────┘ │
                │                └───────────┘   └─────┬──────┘      │      │
                │                                      │             ▼      │
                │  ┌────────────┐   ┌──────────┐   ┌───▼──────┐  platform/  │
                │  │ config     │   │ event    │   │ tray/GUI │  (per-OS)   │
                │  │ (local)    │   │ log      │   │ (v0.3+)  │             │
                │  └────────────┘   └──────────┘   └──────────┘             │
                └───────────────────────────────────────────────────────────┘
```

## Layers

### 1. BLE monitor (`core/monitor.py`)

- Built on **[Bleak](https://github.com/hbldh/bleak)** (asyncio): WinRT on Windows, CoreBluetooth on macOS, BlueZ/D-Bus on Linux — one API.
- **Presence = bonded identity, not raw advertisements.** iOS/Android rotate advertised MAC addresses (RPA), so scanning for a fixed address is unreliable *by design of the phone OS*. The strategy:
  1. Setup wizard has the user bond the device through the OS first.
  2. **v0.1 (implemented):** continuous advertisement scanning filtered on the trusted address. On Linux, BlueZ resolves the RPAs of bonded devices to their stable identity address, so the filter keeps matching across rotations.
  3. **v0.2+ (planned): proximity strategy engine.** Research (see [device-compatibility.md](device-compatibility.md)) shows no single channel covers all devices: iPhones advertise Continuity packets continuously (advertisement strategy works natively), while idle Android phones do not advertise at all but keep the bonded **Bluetooth Classic** link reachable (the same channel Windows Dynamic Lock and BlueProximity use). The monitor therefore becomes pluggable — `ADV_SCAN` / `GATT_LINK` / `CLASSIC_LINK` — with per-device auto-selection at setup and fail-safe runtime fallback.
  4. No advertisement beyond a staleness window ⇒ treated as *out of range* (fail-safe) — verified live: the monitor starts in `leaving` until the first advertisement arrives.
- All Bluetooth I/O isolated behind a `ProximitySource` protocol so tests can inject recorded RSSI traces.

### 2. Distance estimation (`core/distance.py`)

- **Log-distance path loss model**: `d = 10 ^ ((RSSI₁ₘ − RSSI) / (10 · n))` where `RSSI₁ₘ` is the calibrated reference at 1 m and `n` the environment exponent (typ. 1.8–3.5 indoor).
- **Calibration wizard** fits `RSSI₁ₘ` and `n` from samples the user records at guided distances (1 m, 3 m, 5 m). See [rssi-calibration.md](rssi-calibration.md).
- **Smoothing**: moving average over a configurable window (default 8 samples); median pre-filter drops single-sample spikes.

### 3. Presence state machine (`core/presence.py`)

Schmitt-trigger hysteresis with temporal dwell:

```
          d > radius for grace_seconds
  NEAR ────────────────────────────────► AWAY ──► lock()
   ▲                                      │
   └──────────────────────────────────────┘
       d < radius · 0.8 for return_seconds
```

- Distinct leave/return thresholds prevent oscillation at the boundary.
- States: `NEAR`, `LEAVING` (timer running), `AWAY`, `RETURNING`. Every transition is logged locally.
- Link lost / Bluetooth off / permission revoked ⇒ `AWAY` after grace (fail-safe).

### 4. Platform layer (`platform/`)

One module per OS implementing a small `Locker` protocol (`lock()`, `is_locked()`, `capabilities()`):

| OS | Lock | Notes |
|---|---|---|
| Windows | `ctypes.windll.user32.LockWorkStation()` | Presence of API verified in smoke test; session events via `WTSRegisterSessionNotification` (later) |
| macOS | `SACLockScreenImmediate` (login framework) with `pmset displaysleepnow` fallback | Requires "require password immediately" setting; wizard checks it |
| Linux | `loginctl lock-session` (systemd-logind) | Fallbacks: `xdg-screensaver lock`, DE-specific D-Bus calls |

New platforms = new module, zero core changes.

### 5. Persistence (`config/`)

- Single JSON file, schema-versioned, atomic writes. Locations: `%APPDATA%\stavau\`, `~/Library/Application Support/stavau/`, `~/.config/stavau/` (via `platformdirs`).
- Stores: bonded device identity (OS pairing handle + user alias), radius, grace times, calibration constants, UI prefs. **No secrets** — pairing keys live in the OS Bluetooth stack.
- Event log: append-only JSONL, size-capped rotation, local only.

### 6. UI

- **v0.1–v0.2: CLI only** (`stavau setup|run|status|log`).
- **v0.3+: PySide6 (Qt)** — chosen over Tauri for: single-language stack (contributor friendliness), mature `QSystemTrayIcon`, no webview runtime. Decision recorded as ADR-001; revisit if bundle size/RAM become problems.
- i18n via Qt translation files, `locales/` with EN + IT sources.

## Technology summary

| Concern | Choice | Rationale |
|---|---|---|
| Language | Python ≥ 3.10 | Bleak requirement, contributor accessibility |
| BLE | bleak ≥ 3.0 | Only mature cross-platform asyncio BLE lib (validated in smoke test) |
| GUI | PySide6 (v0.3+) | Tray support, one stack; LGPL-compatible with AGPL |
| Paths | platformdirs | Correct per-OS config locations |
| Packaging | pyproject + pipx; PyInstaller bundles at v1.0 | Low-friction install path first |
| Lint/type | ruff + mypy | Fast, standard |
| Tests | pytest (+ recorded RSSI traces as fixtures) | Core testable without radio hardware |
