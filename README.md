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
[![Status](https://img.shields.io/badge/status-alpha-yellow.svg)](#roadmap)
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
5. When you come back, you unlock as usual (password/PIN/biometrics). Optional **auto-unlock on return** is an *advanced* feature that is **off by default**, **Linux-only**, and gated behind an explicit risk acknowledgement — it only ever undoes a lock stavau itself made, never a manual one (see [Auto-unlock](#auto-unlock-advanced-off-by-default)).

stavau picks the right presence channel per device (BLE advertisements, bonded Bluetooth Classic, a held GATT connection, or the controller-offloaded BlueZ monitor) — see [Proximity strategies](#proximity-strategies-works-with-every-device).

> ⚠️ **Design note on MAC randomization.** Modern iOS/Android devices rotate their advertised Bluetooth MAC address every few minutes, so stavau does **not** track advertisement MAC addresses. It relies on the OS-level **bond** with your device and samples RSSI over the established link. This is both more reliable and more privacy-preserving. See [docs/threat-model.md](docs/threat-model.md).

## Features

- 🔒 **Auto-lock on walk-away** — native lock on **Windows, macOS and Linux**.
- 📡 **Four proximity strategies, auto-selected per device** — BLE advertisements, bonded Bluetooth Classic, held GATT connection, and controller-offloaded BlueZ monitoring. Works with iPhones, Android phones, wearables and low-energy tags (see [Proximity strategies](#proximity-strategies-works-with-every-device)).
- 📏 **Configurable safety radius** — 1 to 10 metres, with per-environment calibration.
- ⏱️ **Anti-false-positive engine** — RSSI moving-average smoothing + temporal hysteresis + minimum out-of-range time.
- 🛡️ **Anti-runaway guardrail** — a circuit breaker pauses locking after 3 locks in quick succession, so a bug or a flapping signal can never lock you out of your own machine.
- 🔁 **Closed-loop lock state** — per-OS observers tell stavau whether the screen is actually locked, so it never issues redundant locks.
- 📶 **Radio-off detection** — when Bluetooth is turned off, the UI says **BLUETOOTH OFF** instead of a vague "no signal".
- 🖼️ **Graphical app + system tray** — `stavau gui` (PySide6): device picker, radius slider, live monitor, calibration wizard, and a **state-coloured taskbar/tray icon** that shifts blue → grey → green → yellow → orange → red with distance.
- 🧙 **First-run wizard** — device pairing and RSSI→distance calibration, step by step.
- 🔓 **Optional auto-unlock on return** — advanced, off by default, Linux-only, heavily gated (see below).
- 📜 **Local event log** — lock/unlock history stored only on your machine.
- 🌓 **Dark/light mode**, accessible UI.
- 🌍 **i18n** — auto-detects your OS language, falls back to English; **Italian included**, and community translations are just a JSON file away.
- 🕵️ **Zero telemetry** — no network calls, no accounts, no cloud. Ever. (Verifiable: it's AGPL.)

## Supported platforms

| Platform | Minimum version | Lock | Lock-state feedback | Auto-unlock |
|---|---|---|---|---|
| Windows | 10 (1809+) | `LockWorkStation()` (user32) | WTS session notifications | ❌ (no public unlock API) |
| macOS | 10.15 Catalina | `CGSession -suspend` / `pmset displaysleepnow` | `com.apple.screenIsLocked` notifications | ❌ (no public unlock API) |
| Linux | BlueZ ≥ 5.55 | `loginctl lock-session` (systemd-logind), DE fallbacks | logind `LockedHint` + signals | ✅ `loginctl unlock-session` |

BLE is provided by [Bleak](https://github.com/hbldh/bleak) (WinRT / CoreBluetooth / BlueZ under one API) on every platform.

**Trust devices:** any Android or Apple device (iPhone, Apple Watch, Android phone/watch) that supports BLE. No companion app required.

## Installation

### From source (all platforms)

```bash
git clone https://github.com/davidebr90/stavau.git
cd stavau
python -m venv .venv
# Windows: .venv\Scripts\activate    |    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
stavau --help
```

**Optional extras** (combine as needed, e.g. `pip install -e ".[tray,gui]"`):

| Extra | Adds |
|---|---|
| `tray` | system-tray icon host (`stavau tray`) — pystray + Pillow |
| `gui` | graphical app (`stavau gui`) — PySide6 |
| `macos` | lock-state notifications on macOS — pyobjc |

### Prebuilt bundles

Tagged releases ship self-contained **PyInstaller bundles for Windows, Linux and macOS** (with SHA-256 checksums) on the [Releases](https://github.com/davidebr90/stavau/releases) page. See [docs/install.md](docs/install.md) for download, checksum verification and autostart (Startup shortcut / systemd user unit / macOS LaunchAgent).

### Platform notes

- **Windows:** Bluetooth must be on; no admin rights required.
- **macOS:** grant the Bluetooth permission when prompted (System Settings → Privacy & Security → Bluetooth).
- **Linux:** ensure `bluez ≥ 5.55` and that your user can access the D-Bus system bus (default on major distros). Works on X11 and Wayland via `loginctl`.

## Quick start

Prefer a window? Run **`stavau gui`** (needs the `gui` extra) — it wraps everything below with a device picker, a radius slider, a live monitor and the calibration wizard, and opens in your OS language. Or use the CLI:

```bash
stavau setup      # guided wizard: pick your device, calibrate distances
stavau run        # start monitoring (add --dry-run to log without locking)
stavau status     # connection state, RSSI, estimated distance, strategy
stavau log        # recent lock/unlock events (--clear to purge, --export for JSONL)
stavau tray       # run with a state-coloured system-tray icon (needs the tray extra)
stavau pair       # bond the trusted device via the OS Bluetooth stack
```

Useful `setup` flags:

- `--strategy {auto,adv_scan,classic_link,adv_monitor,gatt_link}` — force a proximity channel (default `auto` detects it).
- `--radius <1..10>` — safety radius in metres.
- `--pair` — bond the device during setup for a stable identity.
- `--enable-auto-unlock --i-understand-the-risk` — opt into auto-unlock (Linux, paired device only).

Key settings (also editable in the GUI):

| Setting | Default | Range |
|---|---|---|
| `radius_m` — safety radius | 3 | 1–10 m |
| `grace_seconds` — time out-of-range before locking | 10 | 3–60 s |
| `smoothing_window` — RSSI moving-average samples | 8 | 3–30 |
| `language` — UI language (`auto` = follow the OS) | `auto` | any catalog |
| `auto_unlock` — unlock on return (**advanced, Linux-only**) | `false` | — |

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
├── core/          # strategies (adv_scan/classic_link/adv_monitor/gatt_link), RSSI
│                  # smoothing, distance model, presence machine, breaker, autounlock
├── platform/      # per-OS plugins: lock, unlock, lock-state observers
├── ui/            # tray icon + PySide6 GUI (ui/gui/)
├── i18n/          # JSON translation catalogs + OS-language detection
├── config/        # local persistence (JSON), schema & validation
└── cli.py         # setup / run / status / log / tray / gui / pair
```

- **Core:** Python ≥ 3.10 + [Bleak](https://github.com/hbldh/bleak) (WinRT / CoreBluetooth / BlueZ under one API).
- **Distance model:** log-distance path loss, `d = 10^((RSSI₀ − RSSI) / (10·n))`, with `RSSI₀` (reference at 1 m) and `n` (environment exponent) fitted during the calibration wizard.
- **Stability:** moving-average smoothing + Schmitt-trigger-style hysteresis (separate "leave" and "return" thresholds) + minimum dwell time.
- **GUI:** PySide6 (Qt) — chosen over Tauri to keep a single-language stack and first-class tray support; revisit at v0.3 if footprint becomes an issue.

Full details: [docs/architecture.md](docs/architecture.md) · [docs/rssi-calibration.md](docs/rssi-calibration.md) · [docs/device-compatibility.md](docs/device-compatibility.md) · [docs/os-native-apis.md](docs/os-native-apis.md) · [docs/threat-model.md](docs/threat-model.md) · [docs/agent-team-plan.md](docs/agent-team-plan.md) (roadmap for contributors)

## Proximity strategies (works with every device)

Different devices expose presence over different channels, so stavau picks the right one per device (and you can override it):

| Strategy | Best for | How it senses presence | Signal quality |
|---|---|---|---|
| `adv_scan` | iPhone/iPad/Watch, beacons, wearables, low-energy tags | BLE advertisement scanning + RSSI | real RSSI → distance (all OSes) |
| `classic_link` | idle Android phones, legacy Classic devices | bonded Bluetooth Classic link | real RSSI on **Linux** (`hcitool`); **reachability only on Windows** (in-range / out-of-range, not distance) |
| `adv_monitor` | low/ultra-low-energy devices, battery-conscious setups | BlueZ `AdvertisementMonitor1`, **offloaded to the controller** (low power) | in/out-of-range vs RSSI thresholds — **Linux only** |
| `gatt_link` | connectable devices that don't advertise usefully | RSSI polled over a **held GATT connection** | real connected RSSI on **macOS/Linux**; unsupported on Windows (no public API) |

`stavau setup` probes the device and chooses automatically. You can override with `--strategy`; for an idle Android that isn't advertising during setup, force `classic_link`. Unavailable strategies fall back to `adv_scan` and say so. Full per-OS capability matrix: [docs/device-compatibility.md](docs/device-compatibility.md) and [docs/os-native-apis.md](docs/os-native-apis.md).

## Auto-unlock (advanced, off by default)

stavau can optionally **unlock** your screen when the trusted device comes back — but this is the riskiest thing a proximity tool can do, so it is deliberately hard to enable and narrow in scope. Every one of these conditions must hold:

- **Off by default** and only enabled with an explicit acknowledgement: `stavau setup --enable-auto-unlock --i-understand-the-risk`.
- **Linux only.** Windows and macOS expose no public API to unlock a session without credentials (by design), so stavau **refuses** to auto-unlock there rather than storing your password.
- **Paired (bonded) device only** — a pairing-less advertisement identity is too easy to spoof.
- **Only undoes stavau's own lock.** If you press `Win+L`, or a screensaver or another tool locks the screen, stavau classifies it as *foreign* and **never** auto-unlocks it.
- **Stricter proximity + dwell.** The device must be well *inside* the radius (a stricter fraction of it) continuously for a dwell period; **no signal never unlocks** (absence of evidence is not presence).

The residual risk is a BLE **relay attack** (making a distant device appear near) — the acknowledgement warns about exactly this. See [docs/threat-model.md](docs/threat-model.md) (T9).

## Roadmap

| Version | Scope | Status |
|---|---|---|
| **v0.1 (MVP)** | BLE monitoring + RSSI distance estimate + screen lock, CLI | ✅ done |
| **v0.2** | Proximity **strategy engine** (`adv_scan` / `classic_link` / `adv_monitor` / `gatt_link`), device intelligence, pairing/pairing-less association, lock backends for **all three OSes**, closed-loop lock-state feedback, anti-runaway guardrail, radio-off detection, safe auto-unlock (Linux) | ✅ largely done |
| **v0.3** | **GUI** (`stavau gui`): device picker, radius slider, live monitor, calibration wizard, i18n | ✅ landed (MVP) |
| **v0.4** | System tray with state-coloured icon, event log, dark mode, i18n (EN/IT) | ✅ landed |
| **v1.0** | Security hardening, full multi-OS hardware test matrix, docs freeze, submissions to awesome-lists | ⏳ |

**Shipped safety guardrail:** an anti-runaway circuit breaker pauses locking after 3 locks in quick succession (configurable), so a bug or a flapping signal can never lock you out of your own machine — see [docs/threat-model.md](docs/threat-model.md) (T10).

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
