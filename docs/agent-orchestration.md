# stavau — Multi-Agent Orchestration Plan

> Operational companion to [agent-team-plan.md](agent-team-plan.md). That document
> says **what** to build (contracts, invariants, workstreams); this one says **who
> builds it, with which model, in what order, and how to not waste resources**.
> The orchestrator agent executes this document; worker agents receive only their
> task card — never this whole file.
>
> Last updated: 2026-07-06.

---

## 1. The dominant constraint: hardware-in-the-loop

**CI runners and cloud agents have no Bluetooth radio.** Everything "live"
(real RSSI, real locks, AirPods/phone behaviour) can only be verified on the
maintainer's machine, one session at a time, with the maintainer present.

Therefore every Definition of Done item is classified as:

- **[AV] Agent-verifiable** — unit tests with mocked subprocess/WinRT/D-Bus,
  lint, types, CI matrix. Agents must complete these autonomously.
- **[HV] Hardware-verifiable** — needs a radio and a human. Agents do NOT block
  on these: they produce a *hardware checkpoint entry* (§8) and mark the card
  "done pending HV". HV items are batched per wave into a single 15-minute
  maintainer session.

An agent claiming an [HV] item is verified without hardware evidence is a
protocol violation.

## 2. Topology

```
ORCHESTRATOR (Fable/Opus, 1 instance, long-lived)
│  plans waves, writes context packets, arbitrates, decides escalations,
│  batches HV checkpoints for the maintainer
│
├── INTEGRATOR (Sonnet, 1) — the only writer of hotspot files
│     sequential merge of worker branches; applies proposed hotspot patches;
│     owns cli.py, config/settings.py, strategy.py factory, CHANGELOG.md
│
├── WAVE WORKERS (parallel, one git worktree each, additive-only)
│     implement exactly one task card; never touch hotspot files directly
│
├── REVIEWER (Sonnet, high effort; spawned per PR, stateless)
│     adversarial review: tries to refute the DoD and break invariants I1–I7;
│     verdict CONFIRMED/REJECTED with evidence, no style nits
│
└── CHORE (Haiku, spawned ad hoc)
      changelog entries, badge/docs sync, i18n string extraction, fixtures,
      mass lint fixes
```

Communication rules: workers report to the orchestrator only (card done /
blocked / needs decision). Workers never message each other. The integrator
pulls, never gets pushed to.

## 3. Model & effort map

| Role / card | Model | Effort | Why |
|---|---|---|---|
| Orchestrator | Fable / Opus | high | planning, arbitration, security judgement; low token volume, high stakes |
| Integrator | Sonnet | medium | mechanical-but-careful merging against green CI |
| WS-A macOS locker | Sonnet | medium | clear contract, existing pattern to mirror |
| WS-B contract card (B1) | Opus | high | one contract must fit three different OS event systems |
| WS-B backend cards (B2–B4) | Sonnet | medium | implement a fixed contract each |
| WS-C AdvertisementMonitor | Opus | high | low-level D-Bus, power semantics, fallback design |
| WS-D GATT_LINK | Opus | high | per-OS capability gating, adaptive polling, honest degradation |
| WS-E GUI (PySide6) | Sonnet | medium | thin shell over existing core |
| WS-E i18n/strings | Haiku | low | mechanical extraction |
| WS-F auto-unlock | **Fable/Opus — mandatory** | max | security-critical: a bug = unlocked screen without presence (T2/T9) |
| WS-G radio-off | Sonnet | medium | three small platform probes + fail-safe wiring |
| WS-H packaging | Sonnet | medium | PyInstaller + workflows, well-trodden |
| WS-I test protocols | Haiku | low | checklist authoring from acceptance-criteria.md |
| Reviewer | Sonnet | high | adversarial reading beats generation here; cheaper than Opus per PR |
| Chore | Haiku | low | volume work, zero design |

Default rule: **Haiku for mechanical, Sonnet for implementation, Opus/Fable only
where the cost of a mistake is high** (security, cross-OS contracts, arbitration).

## 4. Anti-waste rules (binding)

1. **Context packets, not "read everything".** The orchestrator distills each
   card to ≤2k tokens: contract to implement, files to touch, relevant
   invariants, DoD. Workers never read the full docs/ tree.
2. **Additive-only on hotspots.** Workers write new modules + tests. Required
   changes to `cli.py` / `config/settings.py` / `strategy.py` / `CHANGELOG.md`
   are described in a `PATCH-NOTES.md` in their branch; only the Integrator
   applies them, sequentially.
3. **Cheap gates first.** Worker runs `ruff format --check && ruff check && mypy
   src && pytest` in its worktree before reporting done. Reviewer is spawned only
   on green. Orchestrator sees only conflicts and escalations. Never use an
   expensive model as a linter.
4. **Escalation, not retries.** Two failed attempts on the same card → back to
   the orchestrator (re-scope the card or raise the model). Never loop.
5. **Batched HV checkpoints.** One maintainer hardware session per wave, driven
   by the checklist in §8 — not per card.
6. **Event-driven scheduling.** Dependent cards are launched by the orchestrator
   when the dependency merges. Workers never poll or wait.

## 5. Waves

| Wave | Cards | Gate to next wave |
|---|---|---|
| **1** | A1, B1→(B2,B3,B4), G1, H1, I1 | B-series merged (unblocks D/F); HV checkpoint #1 done |
| **2** | C1, D1, E1(+E2 strings) | D merged; HV checkpoint #2 done |
| **3** | F1 (auto-unlock), release hardening | full acceptance pass, v0.2/v0.3 tags |

## 6. Wave 1 task cards (ready to launch)

Each card below **is** the context packet — hand it to the worker verbatim,
plus nothing else except repo access.

---

### CARD-A1 — macOS lock backend · Sonnet · worktree `ws-a-macos`

**Goal:** `src/stavau/platform/macos.py::MacLocker` implementing the `Locker`
protocol (`platform/base.py`), mirroring `linux.py`'s fallback-chain style.

**Do:** primary `CGSession -suspend` (path:
`/System/Library/CoreServices/Menu Extras/User.menu/Contents/Resources/CGSession`);
fallback `pmset displaysleepnow`. Record failed attempts and raise `LockError`
with the attempt list. Add the `darwin` branch in a PATCH-NOTES.md (do not edit
`platform/base.py::get_locker` yourself — hotspot).

**Invariants:** I1 (a failed lock must raise loudly), I4 (all macOS code stays in
this module).

**Tests (new `tests/test_platform_macos.py`):** mock `subprocess.run` /
`shutil.which`; cover primary-success, fallback, all-fail→LockError-with-attempts,
timeout-recorded. Mirror `TestLinuxLocker`.

**DoD [AV]:** ruff+mypy+pytest green incl. new tests; CI matrix green.
**DoD [HV]:** real lock on macOS ≥10.15 within grace; "require password
immediately" verified → checkpoint entry.

---

### CARD-B1 — LockStateObserver contract + session wiring · Opus · worktree `ws-b-contract`

**Goal:** define the lock-state feedback seam and wire it into the monitoring
loop, with a fake for tests. No OS backends in this card.

**Do:** new `src/stavau/platform/lockstate.py`:

```python
class LockStateObserver(Protocol):
    name: str
    def current(self) -> bool | None: ...            # None = unknown
    def subscribe(self, cb: Callable[[bool], None]) -> None: ...
    def close(self) -> None: ...

def get_lock_state_observer() -> LockStateObserver | None:  # None = unsupported OS
```

Wire into `core/session.py`: (a) skip `_trigger_lock` when observer reports
already-locked (log `lock_skipped_already_locked`); (b) log real
`session_locked`/`session_unlocked` transitions; (c) observer errors → treat
state as unknown (None) and behave exactly as today (I1: unknown never disables
locking). Session changes are yours to make directly (you are the exception to
the hotspot rule for `session.py` only — coordinate via PATCH-NOTES for
anything else).

**Invariants:** I1 (unknown state ⇒ keep locking), I2 (breaker still wraps every
lock), I4.

**Tests:** fake observer; already-locked suppression; unknown-state passthrough;
event log entries.

**DoD [AV]:** all checks green; e2e session tests extended.
**DoD [HV]:** none (contract card).

**Follow-ups you must specify precisely for B2–B4 (one paragraph each):**
Windows = `WTSRegisterSessionNotification`/`SystemEvents.SessionSwitch`
equivalent via pywin32 or ctypes message window; Linux = logind
`org.freedesktop.login1.Session` `LockedHint` + signals via D-Bus; macOS =
`com.apple.screenIsLocked`/`Unlocked` distributed notifications.

---

### CARD-B2/B3/B4 — observers Windows / Linux / macOS · Sonnet · worktrees `ws-b-win|linux|mac`

Launched by the orchestrator after B1 merges, each with B1's follow-up paragraph
as its context packet plus the contract source. Tests mock the OS event source.
**DoD [AV]:** contract-conformance tests green. **DoD [HV]:** manual
lock/unlock reflected in `stavau status` on the target OS → checkpoint entry.

---

### CARD-G1 — radio-off & permission fail-safe · Sonnet · worktree `ws-g-radio`

**Goal:** detect "Bluetooth adapter off / permission revoked" at runtime and
surface it as an explicit reason instead of a silent no-signal.

**Do:** new `src/stavau/core/radiostate.py` with per-OS probes behind one
function `radio_available() -> bool | None` (None = cannot determine): Windows
`winrt.windows.devices.radios` (import-guarded like `classic.py`), Linux
`org.bluez` `Adapter1.Powered` via bleak's D-Bus (or `bluetoothctl show`
subprocess fallback), macOS return None for now. Session: when tracker is stale
AND `radio_available() is False`, log `radio_off` once per transition and set
status text "Bluetooth OFF" (tick gains a `radio_off: bool` field —
PATCH-NOTES for `session.py`/`cli.py`/`tray.py` display strings).

**Invariants:** I1 (radio off must still end in a lock via staleness — you add
*explanation*, never a new non-locking path), I4, I5.

**Tests:** probe mocked per platform; transition logged once; tick flag.

**DoD [AV]:** checks green. **DoD [HV]:** toggling BT off on Windows locks
within grace and shows the reason → checkpoint entry.

---

### CARD-H1 — packaging & release workflow · Sonnet · worktree `ws-h-pkg`

**Goal:** installable artifacts per OS + autostart documentation.

**Do:** `.github/workflows/release.yml` (on tag `v*`): PyInstaller one-folder
bundles for Windows/Linux/macOS, SHA256SUMS, GitHub Release upload. Spec file
`packaging/stavau.spec` (include `[tray]` extras). `docs/install.md`: pipx
install, bundle install, autostart per OS (Startup shortcut / systemd user unit
with sample file `packaging/stavau.service` / LaunchAgent plist sample). No
telemetry, no auto-update (I3).

**DoD [AV]:** workflow lints (actionlint if available), dry-run build job green
on the three runners, docs complete. **DoD [HV]:** Windows bundle runs on this
machine → checkpoint entry.

---

### CARD-I1 — hardware test protocol · Haiku · worktree `ws-i-protocol`

**Goal:** turn [acceptance-criteria.md](acceptance-criteria.md) into executable
checklists for a human tester.

**Do:** `docs/hw-test-protocol.md`: per-OS, per-strategy step-by-step scripts
(exact commands, expected outputs, pass/fail boxes, trace-capture instructions
`stavau log --export > fixtures/...`), covering: walk-away lock, false-positive
soak (30 min), calibration accuracy stations, MAC-rotation behaviour,
classic_link with connected audio device (AirPods scenario from
device-compatibility.md §5a), guardrail trip (3 fast locks), BT-off fail-safe.

**DoD [AV]:** document complete, commands verified against current CLI help.
**DoD [HV]:** n/a (this card *produces* the HV instrument).

---

## 7. Integration protocol (Integrator)

1. Merge order within a wave: B1 → (A1, G1, B2–B4, H1, I1 in CI-green order).
2. For each branch: apply its PATCH-NOTES.md to hotspot files, run full checks,
   squash-merge, delete worktree.
3. Conflicting hotspot proposals → orchestrator arbitrates (never guess).
4. After each merge: one CHANGELOG entry (Chore agent), push, watch CI to green
   before the next merge. A red CI freezes the queue.

## 8. Hardware checkpoint template (per wave)

The orchestrator assembles this and hands it to the maintainer; results feed
back as card evidence.

```markdown
## HV Checkpoint — Wave N — date
Machine: Win11 (this PC) / Linux box / macOS
- [ ] CARD-A1: lock fires on macOS ≤ grace+3 s        → evidence: log excerpt
- [ ] CARD-B2: manual Win+L reflected in status        → evidence: status output
- [ ] CARD-G1: BT off ⇒ lock + "Bluetooth OFF" reason  → evidence: log excerpt
- [ ] CARD-H1: Windows bundle runs                     → evidence: version output
Duration target: ≤ 15 min. Failures → orchestrator re-opens the card.
```

## 9. Escalation policy

| Situation | Action |
|---|---|
| Card fails checks twice | stop; report to orchestrator with both diffs |
| Contract found inadequate mid-card | stop; propose contract change to orchestrator (never fork the contract locally) |
| Invariant conflict discovered | stop immediately; orchestrator + maintainer decide |
| Reviewer REJECTED | one fix round by the same worker; second rejection → orchestrator |
| HV checkpoint failure | card re-opened with the evidence attached; same worker, same model first |

---

*This plan supersedes §6 of agent-team-plan.md for coordination purposes; the
contracts (§4) and invariants (§3) there remain the single source of truth.*
