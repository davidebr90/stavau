<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="logo/stavau_dark_transparent.png">
  <img src="logo/stavau_light_transparent.png" alt="stavau" width="480">
</picture>

**Privacy by proximity.**

*"Stavau"* — Brindisi dialect (Puglia, Italy) for **"I'm leaving."**
Say it, and your PC locks itself.

[![CI](https://github.com/davidebr90/stavau/actions/workflows/ci.yml/badge.svg)](https://github.com/davidebr90/stavau/actions/workflows/ci.yml)
[![License: AGPL-3.0](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![Platforms](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey.svg)](#supported-platforms)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](#roadmap)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](CONTRIBUTING.md)

🇮🇹 [Leggilo in italiano](README.it.md)

</div>

---

## Vision

**stavau automatically locks your workstation when you walk away from it**, using Bluetooth Low Energy proximity detection of a personal trusted device (smartphone or smartwatch) — so forgetting to press `Win+L` never again exposes your data.

**Who it's for:** people working on shared or public desks — open-plan offices, coworking spaces, libraries, labs, multi-user workstations — plus IT managers who want an extra safety net, and anyone who is privacy-conscious.

## Why

Walking away from an unlocked screen, even for two minutes, exposes:

- open email, chats and documents,
- active sessions (VPN, SSO, password managers),
- anything a passer-by can photograph, read or type.

Screen-lock timeouts are either too long to protect you or too short to be usable. **stavau replaces the timer with physical presence**: your phone is with you; when it (and you) leave the safety radius, the screen locks within seconds.

## How it works

```
┌──────────────┐   bonded BLE link    ┌─────────────────────────────────────┐
│ Trust device │ ◄──────────────────► │ stavau daemon                       │
│ (phone/watch)│    RSSI sampling     │  RSSI → smoothing → distance est.   │
└──────────────┘                      │  distance > radius for N seconds?   │
                                      │        └─► OS screen lock           │
                                      └─────────────────────────────────────┘
```

1. **Pair** your phone/watch once (guided wizard, standard OS Bluetooth bonding).
2. stavau keeps a low-energy link to the device and **samples RSSI** (signal strength).
3. RSSI is smoothed (moving average) and converted to an **estimated distance** using a calibration you perform once ("stand at 1 m… now at 3 m…").
4. When estimated distance exceeds your **safety radius (1–10 m)** for a **configurable grace time** (default 10 s), stavau triggers the **native OS screen lock**.
5. When you come back, you unlock as usual (password/PIN/biometrics). Optional auto-unlock on return is a planned *advanced* feature, **off by default**, with explicit security warnings.

> ⚠️ **Design note on MAC randomization.** Modern iOS/Android devices rotate their advertised Bluetooth MAC address every few minutes, so stavau does **not** track advertisement MAC addresses. It relies on the OS-level **bond** with your device and samples RSSI over the established link. This is both more reliable and more privacy-preserving. See [docs/threat-model.md](docs/threat-model.md).

## Features

- 🔒 **Auto-lock on walk-away** — native lock on Windows, macOS and Linux.
- 📏 **Configurable safety radius** — 1 to 10 metres, with per-environment calibration.
- ⏱️ **Anti-false-positive engine** — RSSI moving-average smoothing + temporal hysteresis + minimum out-of-range time.
- 🧙 **First-run wizard** — device pairing and RSSI→distance calibration, step by step.
- 🖥️ **System tray / menu bar icon** — connection state, current RSSI and estimated distance at a glance.
- 📜 **Local event log** — lock/unlock history stored only on your machine.
- 🌓 **Dark/light mode**, accessible UI.
- 🌍 **i18n** — English and Italian first, community translations welcome.
- 🕵️ **Zero telemetry** — no network calls, no accounts, no cloud. Ever. (Verifiable: it's AGPL.)

## Supported platforms

| Platform | Minimum version | Lock mechanism | BLE backend |
|---|---|---|---|
| Windows | 10 (1809+) | `LockWorkStation()` (user32) | WinRT via [Bleak](https://github.com/hbldh/bleak) |
| macOS | 10.15 Catalina | `SACLockScreenImmediate` / `pmset displaysleepnow` + require-password | CoreBluetooth via Bleak |
| Linux | BlueZ ≥ 5.55 | `loginctl lock-session` (systemd-logind), DE-specific fallbacks | BlueZ/D-Bus via Bleak |

**Trust devices:** any Android or Apple device (iPhone, Apple Watch, Android phone/watch) that supports BLE bonding. No companion app required for v1.x.

## Installation

> stavau is **pre-alpha**: no binary releases yet. See the [Roadmap](#roadmap).

### From source (all platforms)

```bash
git clone https://github.com/davidebr90/stavau.git
cd stavau
python -m venv .venv
# Windows: .venv\Scripts\activate    |    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
stavau --help
```

### Platform notes

- **Windows:** Bluetooth must be on; no admin rights required.
- **macOS:** grant the Bluetooth permission when prompted (System Settings → Privacy & Security → Bluetooth).
- **Linux:** ensure `bluez ≥ 5.55` and that your user can access the D-Bus system bus (default on major distros). Works on X11 and Wayland via `loginctl`.

## Quick start

```bash
stavau setup      # guided wizard: pick your device, bond, calibrate distances
stavau run        # start monitoring (foreground; use --daemon for background)
stavau status     # connection state, RSSI, estimated distance
stavau log        # recent lock/unlock events
```

Key settings (also editable in the GUI, from v0.3):

| Setting | Default | Range |
|---|---|---|
| `radius_m` — safety radius | 3 | 1–10 m |
| `grace_seconds` — time out-of-range before locking | 10 | 3–60 s |
| `smoothing_window` — RSSI moving-average samples | 8 | 3–30 |
| `auto_unlock` — unlock on return (**advanced, discouraged**) | `false` | — |

Configuration lives in a local file (`%APPDATA%\stavau\`, `~/Library/Application Support/stavau/`, `~/.config/stavau/`). Nothing leaves your machine.

## Security model — read this

stavau is a **convenience layer**, not an authentication system.

- ✅ It makes forgetting to lock your screen a non-event.
- ❌ It does **not** replace your password, PIN, biometrics or full-disk encryption.
- ❌ It must **never** be your only defence against a determined attacker.

Known limitations (documented in full in [docs/threat-model.md](docs/threat-model.md)):

- **BLE relay/amplification attacks** can make a distant device appear near. This mainly matters if you enable auto-unlock — which is why it ships **off**.
- **RSSI is noisy**: walls, bodies and interference affect it. Calibration + hysteresis mitigate but cannot eliminate estimation error (target: ±1.5 m indoors).
- **Fail-safe policy**: if the device link drops, Bluetooth turns off, or stavau crashes, the screen **locks** (never the opposite).

## Privacy

- No telemetry, no analytics, no crash reporting by default. Any future opt-in diagnostic feature will be explicit, documented, and off by default.
- Event logs are local, contain no precise identifiers beyond your own device alias, and can be purged with `stavau log --clear`.
- AGPL-3.0 guarantees you (and your IT department) can audit every line that runs.

## Architecture (short version)

```
src/stavau/
├── core/          # scanner loop, RSSI smoothing, distance model, hysteresis state machine
├── platform/      # one module per OS: lock trigger + session integration (plugin interface)
├── ui/            # tray icon + settings GUI (v0.3+)
├── config/        # local persistence (JSON), schema & migrations
└── cli.py         # setup / run / status / log commands
```

- **Core:** Python ≥ 3.10 + [Bleak](https://github.com/hbldh/bleak) (WinRT / CoreBluetooth / BlueZ under one API).
- **Distance model:** log-distance path loss, `d = 10^((RSSI₀ − RSSI) / (10·n))`, with `RSSI₀` (reference at 1 m) and `n` (environment exponent) fitted during the calibration wizard.
- **Stability:** moving-average smoothing + Schmitt-trigger-style hysteresis (separate "leave" and "return" thresholds) + minimum dwell time.
- **GUI:** PySide6 (Qt) — chosen over Tauri to keep a single-language stack and first-class tray support; revisit at v0.3 if footprint becomes an issue.

Full details: [docs/architecture.md](docs/architecture.md) · [docs/rssi-calibration.md](docs/rssi-calibration.md) · [docs/device-compatibility.md](docs/device-compatibility.md) · [docs/os-native-apis.md](docs/os-native-apis.md) · [docs/threat-model.md](docs/threat-model.md) · [docs/agent-team-plan.md](docs/agent-team-plan.md) (roadmap for contributors)

## Proximity strategies (works with every device)

Different devices expose presence over different channels, so stavau picks the right one per device (and you can override it):

| Device | Channel | Strategy | Signal |
|---|---|---|---|
| iPhone / iPad / Apple Watch | BLE (advertises Continuity constantly) | `adv_scan` | real RSSI → distance |
| BLE beacons, wearables, low-energy tags | BLE advertisements | `adv_scan` | real RSSI → distance |
| Android phone (idle, not advertising) | bonded Bluetooth Classic | `classic_link` | real RSSI on Linux; in-range/out on Windows |
| Any bonded Classic device | Bluetooth Classic link | `classic_link` | see above |

`stavau setup` probes and chooses automatically. For an idle Android that isn't advertising during setup, force the channel: `stavau setup --strategy classic_link`. Details and the per-OS capability matrix: [docs/device-compatibility.md](docs/device-compatibility.md).

## Roadmap

| Version | Scope | Status |
|---|---|---|
| **v0.1 (MVP)** | BLE monitoring + RSSI distance estimate + screen lock, **CLI** (Linux target; Windows lock backend landed early) | ✅ implemented — release pending acceptance tests |
| **v0.2** | Proximity **strategy engine** — device intelligence, pairing/pairing-less association, and **classic-link** (BLE + Bluetooth Classic: real RSSI on Linux, reachability on Windows) ✅ landed; GATT-link strategy and macOS lock backend ⏳ (see [docs/device-compatibility.md](docs/device-compatibility.md)) | 🚧 in progress |
| **v0.3** | GUI: radius slider, calibration wizard | ⏳ |
| **v0.4** | System tray ✅ (preview: `stavau tray`), event log viewer, dark mode, i18n (EN/IT) | 🚧 |

**Safety guardrail (shipped early):** an anti-runaway circuit breaker pauses locking after 3 locks in quick succession (configurable), so a bug or a flapping signal can never lock you out of your own machine. See [docs/threat-model.md](docs/threat-model.md) (T10).
| **v1.0** | Security hardening, full multi-OS test matrix, docs freeze, submissions to awesome-lists | ⏳ |

Each milestone has explicit acceptance criteria — see [docs/acceptance-criteria.md](docs/acceptance-criteria.md).

## Contributing

Contributions are very welcome — code, docs, translations, and real-world RSSI calibration data from different environments are all valuable.

1. Read [CONTRIBUTING.md](CONTRIBUTING.md) (coding style, PR process, DCO).
2. Check [good first issues](https://github.com/davidebr90/stavau/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22).
3. Be excellent to each other: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Security issues: please follow [SECURITY.md](SECURITY.md) — do not open public issues for vulnerabilities.

## License

[AGPL-3.0](LICENSE). stavau is a privacy tool: users must always be able to inspect, modify and share the exact code that watches their presence. Strong copyleft (vs. MIT) guarantees derivatives stay open; the AGPL network clause additionally protects users if anyone ever wraps stavau's logic in a hosted/managed service (e.g. fleet management dashboards).

---

<div align="center">
<sub>Made in Puglia 🇮🇹 · <em>Stavau. Il PC lo sa.</em></sub>
</div>
