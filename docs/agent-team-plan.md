# stavau — Technical Plan for a Coordinated Agent Team

> **Audience:** a future team of coordinated engineering agents that will extend
> stavau. This is your source of truth: current state, hard-won findings,
> invariants you must not break, and a workstream decomposition with concrete
> APIs, contracts, and acceptance criteria. Read [architecture.md](architecture.md),
> [device-compatibility.md](device-compatibility.md), [os-native-apis.md](os-native-apis.md)
> and [threat-model.md](threat-model.md) before starting.
>
> Last updated: 2026-07-06 (after v0.2 classic_link landed).

---

## 0. TL;DR for the orchestrator

- stavau is a from-scratch, AGPL-3.0, privacy-by-proximity screen locker: a
  daemon scans the distance of a trusted Bluetooth device and locks the PC when
  it leaves. Repo `davidebr90/stavau`. CLI + tray preview shipped; 97 tests,
  cross-OS CI green.
- **Two proximity strategies run today:** `adv_scan` (BLE advertisements, real
  RSSI, universal for advertising devices) and `classic_link` (bonded Classic —
  real RSSI on Linux, reachability on Windows). Auto-selected by device
  intelligence; user-overridable.
- **The engine abstraction is the load-bearing design.** New channels plug in as
  `ProximitySource` backends without touching the session/lock/guardrail core.
- Spend your effort in the order of Section 5 (dependencies matter). Preserve the
  invariants in Section 3 at all costs — they are safety properties, not
  preferences.
- **Coordination, model assignment, task cards and scheduling live in
  [agent-orchestration.md](agent-orchestration.md)** — the orchestrator executes
  that document; workers receive only their task card.

---

## 1. Current state (what is DONE and verified)

| Area | State | Evidence |
|---|---|---|
| Core pipeline: scan → smoothing → log-distance → hysteresis FSM → lock | ✅ | `core/{monitor,distance,presence,session}.py`; field-tested on Win11 (real locks) |
| Anti-runaway guardrail (circuit breaker) | ✅ | `core/breaker.py`; threat-model T10; unit + e2e tests |
| Strategy engine (`ProximitySource`, factory, fallback) | ✅ | `core/strategy.py` |
| `adv_scan` strategy | ✅ | `core/monitor.py::BleProximitySource` |
| `classic_link` — Linux (hcitool+l2ping, real RSSI) | ✅ | `core/classic.py::HcitoolClassicBackend`; unit-tested (subprocess mocked) |
| `classic_link` — Windows (WinRT ConnectionStatus, reachability) | ✅ | `core/classic.py::WinRtConnectionBackend`; **verified live vs connected AirPods** |
| Device intelligence (classify + recommend) | ✅ | `core/deviceid.py` |
| Pairing / pairing-less association | ✅ | `stavau pair`, `setup --pair` |
| Config, event log, CLI (setup/run/status/log/tray/pair) | ✅ | `config/`, `core/events.py`, `cli.py` |
| System-tray preview | ✅ | `ui/tray.py` |
| Lock backends: Windows, Linux | ✅ | `platform/{windows,linux}.py` |

## 2. Findings from live testing & research (build on these)

1. **The strategy engine is not optional — it is essential.** Live proof: with
   AirPods **connected** to Windows, a BLE advertisement scan did **not** see
   them at all (18 other advertisers seen, zero AirPods), while `classic_link`
   via WinRT `ConnectionStatus` reported them **CONNECTED → near (−45 dBm, 0.2 m)
   stably**. A single-strategy tool would have failed on this common case.
2. **Windows classic_link is binary** (present→near / absent→far), not metric.
   The radius slider has no metric effect there; it locks on full
   disconnect/out-of-range (~10 m), like Windows Dynamic Lock. Documented; do not
   pretend otherwise in the UI.
3. **Connected-classic RSSI is the fragmented axis:** real on Linux
   (`hcitool`), real on macOS (`CBPeripheral.readRSSI` / `IOBluetooth.rawRSSI`),
   **absent on Windows** (no public API — same wall Dynamic Lock hit).
4. **BlueZ `AdvertisementMonitor1`** is the biggest efficiency opportunity:
   controller-offloaded, low-power, event-driven RSSI in/out-of-range. Right
   primitive for the daemon and for low/ultra-low-energy devices on Linux.
5. **Windows `BluetoothSignalStrengthFilter`** gives OS-side in/out thresholds +
   sampling — a lighter path than software smoothing on Windows.
6. **Lock-state feedback is documented on every OS** (WTS `SessionSwitch` /
   logind `LockedHint` / `com.apple.screenIsLocked`). Wiring it makes the loop
   closed and is a prerequisite for safe auto-unlock.
7. **Idle Android does not advertise** during setup probing → classifies as
   *unknown*; the `--strategy classic_link` escape hatch exists for this. Bonding
   is what makes it trackable.

## 3. Invariants — DO NOT BREAK (safety properties)

These are enforced by tests and design. Any PR violating one is wrong.

- **I1 — Fail safe, never fail open.** Every error path (signal lost, BT off,
  permission denied, backend crash, unreadable config) must lead to a *locked*
  screen or a refusal to start — never a silently disabled protection.
- **I2 — The anti-runaway guardrail is inviolable.** Locking must always pass
  through `LockCircuitBreaker`. Never add a lock path that bypasses it.
- **I3 — No network. Ever.** Zero outbound traffic by default; any future
  diagnostic upload is opt-in, explicit, documented, and off by default.
- **I4 — OS specifics stay behind interfaces.** New OS/channel code goes in a
  `platform/*` locker or a `ProximitySource`/`ClassicBackend`. Core stays
  platform-agnostic and unit-testable without radios.
- **I5 — Honest degradation.** When a strategy gives reachability not distance
  (Windows classic), say so; never render a fake distance.
- **I6 — Convenience layer, not authentication.** Never position stavau as the
  security boundary. Password/biometrics/FDE remain primary.
- **I7 — Local-only, minimal identifiers.** No persistent device identifiers
  beyond what tracking needs; logs purgeable.
- **I8 — Authorship golden rule.** Commits, pushes, code, comments and docs must
  never contain any reference to AI assistants, AI tools, or their model/product
  names: no `Co-Authored-By` trailers for non-human authors, no "generated
  with/by" banners. Repository history shows the repository owner only. Model
  tiers in orchestration docs are referred to abstractly (M1/M2/M3).

## 4. Contracts (the seams you extend)

```python
# A proximity channel. Feeds RSSI (or synthesized RSSI) into the session's tracker.
class ProximitySource(Protocol):
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
    def retarget(self, address: str) -> None: ...

# A per-OS classic backend. Returns dBm-ish when reachable, else None (fail-safe far).
class ClassicBackend(Protocol):
    name: str
    async def read_rssi(self, address: str) -> float | None: ...

# A per-OS screen locker.
class Locker(Protocol):
    name: str
    def lock(self) -> None: ...        # raises LockError on failure

# New: a per-OS lock-state observer (WS3). Proposed contract:
class LockStateObserver(Protocol):
    name: str
    def is_locked(self) -> bool | None: ...          # None = unknown
    def on_change(self, cb: Callable[[bool], None]) -> None: ...
```

Extend by adding implementations and wiring them in the factory
(`core/strategy.py::build_source`, `platform/base.py::get_locker`). Keep
`MonitorSession` unchanged unless the loop semantics genuinely change.

## 5. Workstreams (assignable, ordered by dependency)

Each is sized for one agent. `[P]` = parallelizable with siblings; `[S]` =
should follow its listed dependency. Every workstream ends with the Global DoD in
[acceptance-criteria.md](acceptance-criteria.md) plus its own criteria.

### WS-A `[P]` — macOS lock backend
- **Goal:** `platform/macos.py::MacLocker` so v0.2 covers all three OSes for the
  lock action.
- **Approach:** `CGSession -suspend` (primary); `pmset displaysleepnow` +
  verify "require password immediately" (fallback). Mirror the Linux fallback
  chain and error reporting.
- **APIs:** [os-native-apis.md](os-native-apis.md) §3.
- **DoD:** lock fires within grace on macOS 10.15+; wizard checks the
  require-password setting; unit test with the subprocess mocked; CI lint/type
  clean (macOS runner already in matrix).

### WS-B `[P]` — Lock-state feedback (all OSes) → closed loop
- **Goal:** know whether the screen is actually locked; stop issuing redundant
  locks; foundation for auto-unlock.
- **Approach:** implement `LockStateObserver` per OS —
  Windows `WTSRegisterSessionNotification`/`SystemEvents.SessionSwitch`;
  Linux logind `LockedHint` + `Lock`/`Unlock` signals; macOS
  `com.apple.screenIsLocked`/`Unlocked` distributed notifications. Feed state
  into `MonitorSession`: if already locked, don't re-lock; log real transitions.
- **APIs:** os-native-apis §1–§3 (lock-state rows).
- **DoD:** `stavau status` reports real lock state; no duplicate `lock_triggered`
  while already locked; tests with observers faked.

### WS-C `[S: engine]` — BlueZ AdvertisementMonitor backend (Linux, low power)
- **Goal:** a power-efficient `adv_scan` variant using controller offload;
  first-class support for low/ultra-low-energy devices.
- **Approach:** new `ProximitySource` using `org.bluez.AdvertisementMonitor1` /
  `AdvertisementMonitorManager1` with High/Low RSSI thresholds + timers mapped
  from `radius_m`/`grace_seconds`. Emits in/out-of-range events instead of
  polling. Falls back to bleak scan if unsupported (kernel/controller).
- **APIs:** os-native-apis §2 (AdvertisementMonitor row); BlueZ Advertisement
  Monitor API docs.
- **DoD:** on a BlueZ ≥5.55 host, presence tracked with measurably lower CPU
  wakeups than polling; graceful fallback path tested; documented battery note.

### WS-D `[S: WS-B]` — GATT_LINK strategy (connected-RSSI where public)
- **Goal:** implement the third strategy for platforms that expose connected
  RSSI: macOS (`CBPeripheral.readRSSI`, bleak `get_rssi` on Darwin) and Linux
  (HCI Read RSSI on an LE connection). Windows stays classic/reachability.
- **Approach:** `GattLinkSource` holds a bleak connection and polls RSSI with
  adaptive back-off (long interval when comfortably in range → battery). Mark
  implemented per-OS in `deviceid.IMPLEMENTED_STRATEGIES` guarded by capability.
- **APIs:** os-native-apis (connected-RSSI rows); bleak `BleakClient`.
- **DoD:** stable connected-RSSI distance on macOS + Linux; adaptive interval
  verified; honest capability gating; e2e test with a fake client.

### WS-E `[S: WS-A]` — GUI v0.3 (PySide6): radius slider + calibration wizard
- **Goal:** the graphical setup/monitor experience from the roadmap.
- **Approach:** PySide6 (ADR-001). Reuse `MonitorSession`, `calibrate.py`,
  `deviceid.py`, `strategy.py` — the GUI is a thin shell over the core. Slider
  (1–10 m), guided calibration stations, device picker (reuse `NearbyCache`),
  strategy selector with the honest per-OS caveat text, guardrail settings,
  live state readout. Dark/light per OS.
- **DoD:** first-time user completes setup without docs (hallway test ×3);
  settings persist; corrupt config → safe defaults; EN/IT i18n scaffolding.

### WS-F `[S: WS-B]` — Safe auto-unlock (advanced, off by default)
- **Goal:** optional unlock on return, without weakening the security posture.
- **Approach:** requires bonded link + distance below a *stricter* threshold for
  N consecutive seconds; **never** after a manual lock (needs WS-B lock-state);
  relay-attack warning; explicit opt-in with a hard warning. Design against
  threat-model T2/T9 first, code second.
- **DoD:** off by default; cannot fire after manual lock; threshold + dwell
  enforced; refuses to enable without acknowledgement; documented risk.

### WS-G `[P]` — Robustness: radio-off & permission detection
- **Goal:** detect Bluetooth turned off / permission revoked at runtime and
  fail-safe explicitly (I1), with actionable messages.
- **Approach:** Windows `Windows.Devices.Radios` `RadioState`; Linux
  `Adapter1.Powered` / rfkill; macOS `CBCentralManager.state`. On loss → lock +
  clear log/tray state, not a silent stall.
- **DoD:** toggling BT off at runtime locks within grace and surfaces the reason
  on each OS; tests with radio state faked.

### WS-H `[P]` — Packaging & distribution
- **Goal:** installable artifacts, not source-only.
- **Approach:** PyInstaller bundles per OS (release workflow, checksums);
  autostart/service integration (Windows Task Scheduler/Startup, systemd user
  service, macOS LaunchAgent); optional signing. Evaluate an F-Droid **companion
  app** only if a mobile piece is ever needed (none required today).
- **DoD:** one-command install per OS; daemon can autostart; release workflow
  green with signed/checksummed artifacts.

### WS-I `[P]` — Hardware test matrix & CI depth
- **Goal:** turn acceptance-criteria into executed evidence.
- **Approach:** structured manual test protocol across ≥2 Android + ≥1 Apple
  device × 3 OSes; record RSSI/calibration traces as fixtures; add a soak-test
  harness (30-min false-positive run). Consider self-hosted runners with radios.
- **DoD:** acceptance-criteria.md rows checked with linked evidence; new
  regression fixtures committed.

## 6. Coordination guidance

> **Superseded for multi-agent execution** by
> [agent-orchestration.md](agent-orchestration.md) (topology, model map, waves,
> ready-to-launch task cards, hardware-checkpoint protocol). The notes below
> remain valid as general background.

- **Parallel now:** WS-A, WS-B, WS-G, WS-H, WS-I are largely independent — assign
  in parallel. WS-B unblocks WS-D and WS-F, so prioritize it among the first wave.
- **Shared-file hotspots** (coordinate writes / use worktrees): `cli.py`
  (subcommands/flags), `config/settings.py` (new fields — always additive +
  forward-compatible), `deviceid.py::IMPLEMENTED_STRATEGIES`, `strategy.py`
  factory, `CHANGELOG.md`. Prefer additive changes; never renumber threat-model
  rows.
- **Definition of ready for a WS:** contracts in §4 understood; APIs confirmed
  against os-native-apis.md; tests planned before code.
- **Every PR:** preserve invariants §3; `ruff format --check`, `ruff check`,
  `mypy --strict`, `pytest` green on the full OS matrix; update CHANGELOG under
  `[Unreleased]`; add/adjust tests; keep new deps justified (AGPL-compatible,
  maintained, minimal) and network-free.
- **Verification bar:** anything observable on hardware must be tested live on at
  least one target and reported honestly (this session's standard). Mock
  subprocess/WinRT/D-Bus in unit tests so CI stays hardware-free and green.

## 7. Known traps (learned this session)

- `winrt` is Windows-only → keep `winrt.*` in mypy `ignore_missing_imports` or
  Linux/macOS CI fails at type-check.
- WinRT `BluetoothDevice.from_bluetooth_address_async` returns a non-None object
  even for unknown addresses → `ConnectionStatus` is the real signal, not object
  presence.
- Phones rotate BLE MAC (RPA) every ~15 min → never track advertisement MACs;
  rely on bonding (IRK resolution) or the classic link.
- `hcitool rssi` needs an active connection and returns a value **relative to the
  golden range** (0 ≈ −60..−40 dBm), not absolute dBm — map it, don't trust it
  raw.
- Connected audio devices (AirPods) don't appear in BLE advertisement scans →
  `adv_scan` can't see them; `classic_link` can. This is the canonical
  strategy-engine justification.
- Console output must be ASCII on Windows cp1252 terminals; flush per-tick prints.
