# Hardening plan — full-project defect sweep (2026-07-07)

> **Status: executed.** All cards H0–H12 below landed on `main` (13 commits),
> each with a failing-first regression test and the full gate (pytest, `mypy
> --strict` on the three platforms, ruff). The one deferred item is the
> intra-tick observer-poll race noted under finding 19/[3] (needs edge-driven
> origin + a hardware test). See the CHANGELOG `[Unreleased]` for the summary.

A whole-codebase adversarial review (five parallel readers, one per slice, each
re-reading before reporting) surfaced the defects below. Every item was
re-verified against the source at `file:line` before landing here. The list is
ordered by **increasing fix difficulty**; each item carries a **severity** and
the failure scenario it closes. The execution plan (waves, cards, dependencies,
regression guards) follows.

The guiding rule for this project is unchanged: **fail-safe on the lock axis**
(any doubt ⇒ lock) and **fail-shut on the unlock axis** (any doubt ⇒ do not
unlock). Findings are ranked with that lens — a *fail-open* (screen stays
unlocked when it should lock, or unlocks when it must not) outranks everything.

## Legend

- Severity: **critical** (fail-open / T9) · **high** · **medium** · **low**.
- Difficulty: effort to fix *safely*, not lines changed.

---

## Ranked findings (increasing fix difficulty)

### Trivial

| # | File:line | Sev | Defect → fix |
|---|-----------|-----|-------------|
| 1 | `core/autounlock.py:53-57,131-134` | medium | `validate()` permits `dwell_seconds == 0`, which disables the anti-relay dwell (unlock on the first in-range tick); the two identical return branches are a latent trap inviting a future "simplify to `return True`". → first-in-range branch returns `False` unconditionally; require `dwell_seconds > 0` in both `AutoUnlockConfig.validate()` and `Settings.validate()`. |
| 2 | `core/breaker.py:69` | low | Window eviction uses `< cutoff`, so a lock exactly `window_seconds` old still counts. Trips *slightly more eagerly* — safe direction, cosmetic. → decide inclusive/exclusive intentionally and pin it with a test. |
| 3 | `config/settings.py:95-98` | low | `auto_unlock_strict_ratio` / `dwell_seconds` are validated only when `auto_unlock` is on, so a stale bad value can persist. → validate those bounds unconditionally. |
| 4 | `core/deviceid.py:84-151` | low | `classify()` vendor precedence (Apple > Android > MS > wearable) is implicit in code order; a future insertion silently changes mixed-signal results. → document the order + pin it with a multi-vendor test. |
| 5 | `ui/gui/viewmodel.py:176-178` | low | `_matches_word` uses space-padded substring, so "mouse"/"watch"/"buds" match inside compound names ("Mousepad Pro"). → real `\b…\b` boundary for every `_NAME_RULES` entry, as already done for `_TV_RE`. |
| 6 | `core/strategy.py:79-83` | low | A deliberately-chosen `external_presence` that is misconfigured (blank topic) silently degrades to `adv_scan`, then locks constantly for an idle phone. → have the session log a warning whenever `effective_strategy != requested`. |

### Easy

| # | File:line | Sev | Defect → fix |
|---|-----------|-----|-------------|
| 7 | `config/settings.py:71-101,118-138` | **high** | `validate()` never bounds `rssi_at_1m` / `path_loss_exponent`; `load()` does no numeric coercion. A hand-edited/corrupt config (bad exponent, or `smoothing_window: 8.0`) throws a raw `ValueError`/`TypeError` from deep in `distance.py`/`deque` — *outside* the `ConfigError` umbrella `main()` handles, so it crashes with a traceback instead of "re-run setup". → add exponent + rssi bounds using `CalibrationModel.MIN/MAX_EXPONENT`; coerce declared numeric fields in `load()`, folding failures into `ConfigError`. |
| 8 | `core/session.py:236-243` | **high** | `retry_due` is not gated on presence state. If a lock *fails* (`lock_pending_since` set) and the user is actively RETURNING (not yet NEAR — line 236 only clears on NEAR), the 5 s retry fires and **locks a returning user out**. → gate the whole lock block on `state in (AWAY, LEAVING)`, or clear `lock_pending_since` on leaving AWAY. |
| 9 | `core/classic.py:181-192` | medium | `WinRtConnectionBackend.read_rssi` has no `try/except`; `from_bluetooth_address_async` or `_mac_to_int` on a bad address raises. Caught by the outer poll guard today (invisible), fatal if that guard is ever refactored. → wrap the body `try/except: return None`; validate the address. |
| 10 | `core/classic.py:204-217` | medium | On timeout `_run` does `proc.kill()` then an **unbounded** `await proc.wait()`; a wedged `l2ping` in D-state hangs the poll loop forever (source silently stops updating, never recovers). → `asyncio.wait_for(proc.wait(), 2.0)` with suppression. |
| 11 | `core/advmonitor.py:371-386` | medium | Keepalive pushes synthesized "present" every beat but checks `_bus_alive()` only every 5th → up to ~5 s of fake presence after BlueZ dies, weakening the very watchdog it exists for. Also confirm the dbus-fast liveness attribute name (defaults truthy). → check liveness every beat (cheap), push only after it passes. |
| 12 | `core/advmonitor.py` (`_on_release`) | medium | A BlueZ-revoked monitor stops tracking but starts no fallback → the source goes **permanently silent** until stop/start. → start the scanning fallback on release, like the bus-death path. |
| 13 | `packaging/stavau.spec:21-23`, `.github/workflows/release.yml:40` | medium | Release installs only `.[tray]` and collects only pystray/PIL, so **every packaged binary silently lacks `stavau gui` and MQTT integration** (documented features). → add `[gui,integration]` to the build + `collect_submodules("paho")` (and PySide6), or document the exclusion loudly. |
| 14 | `core/events.py:16,35-44` | low | `detail` (e.g. `error=str(exc)`) is serialized unbounded — a huge exception string can dominate the 1 MB rotation budget and evict real history. → truncate detail strings (~500 chars). |
| 15 | `core/radiostate.py:88-92` | low | `bluetoothctl show` (no arg) reports the *default* controller; on multi-adapter hosts that may not be the one bleak scans with → "phone left" mislabeled "radio off" (reporting-only). → target the active controller. |
| 16 | `platform/lockstate_linux.py:256-262` | low | `os.environ["XDG_SESSION_ID"]` raises `KeyError` in a headless/re-parented service; degrades to "unknown" (I1 holds) but logs nothing, masking a real misconfig. → `.get()` + a one-time diagnostic. |
| 17 | `core/gattlink.py:264-297` | low | Backoff is doubled unconditionally after every `_connect_and_poll` return, including a `retarget`-triggered return, so the first (re)connect to a *new* device is needlessly delayed. → reconnect immediately when the exit cause was a generation change. |

### Medium

| # | File:line | Sev | Defect → fix |
|---|-----------|-----|-------------|
| 18 | `core/integration.py:204,225-233` | **critical** | **Fail-open.** `_present` is a latched boolean; `_poll_loop` pushes `PRESENT_RSSI_DBM` at 2 Hz forever while it stays `True`. If Home Assistant stops publishing (sensor crash, retained stale `on`, person left but no `off`) while the TCP link stays up, the tracker **never goes stale and the screen never locks**. Directly violates the module's own I1 claim ("evidence only while present"). → stamp each presence update with a monotonic time; only push when `present is True AND now - present_at <= max_presence_age` (new setting, a few × the sensor cadence). |
| 19 | `core/autounlock.py:83-106` (+ `session.py:198-202,265-270`) | **critical** | **T9 fail-open.** `_expecting_own_lock` has no time bound: if a stavau lock action reports success but the observer never sees the `True` edge (missed edge, or the lock silently no-ops), the flag persists indefinitely, and a *later* manual `Win+L` is then classified `STAVAU` and becomes auto-unlockable. Intra-tick unlock→relock is also misattributed because the policy consumes per-tick snapshots, not edges. → time-bound the expectation (record `_expecting_since`, honour only within a short window); drive `note_lock_observed` from the observer's edge callbacks (or a transition counter) so a foreign re-lock re-classifies. |
| 20 | `core/session.py:243-253` | medium | If the breaker is paused at the exact NEAR/LEAVING→AWAY transition, the one-shot `must_lock` is dropped and `lock_pending_since` cleared; the machine won't re-emit, so **after cooldown ends an already-away user is never locked** until a fresh departure. → while AWAY + unlocked + not paused, re-assert the lock need (don't forget the owed lock). |
| 21 | `core/monitor.py:214-221` vs `session.py:203` | medium | `RssiTracker` is mutated from bleak's scanner-callback thread while the event loop reads `smoothed()` — unsynchronized; can read a half-swapped smoother or a torn average, yielding a spurious `None`/wrong distance (false or missed lock). → guard `push`/`smoothed`/`reset`/`_last_seen` with a `threading.Lock` (or marshal via `call_soon_threadsafe`). |
| 22 | `cli.py:348-354` | medium | Pre-tick death isn't fail-safed: if `source.start()` throws before the first tick, `saw_ticks` is `False` so the armed abort path **skips the precautionary lock** — a monitor that died before protecting is arguably the more dangerous case. The precautionary `lock()` is also `suppress`-ed, so its own failure is silent while the user is told "screen locked". → lock whenever `locker is not None`; catch `LockError` and report if the precautionary lock also failed. |
| 23 | `ui/gui/app.py` (no `closeEvent`) | **high** | Closing the window via the title-bar X can quit the app while the monitor `QThread` is still in `asyncio.run(session.run())`, skipping the `finally` cleanup (`source.stop()`, `notifier.close()`, `observer.close()`, `monitor_stopped`); or it orphans an unstoppable thread (Stop button + tray gone with the window). → `closeEvent` → hide-to-tray when a tray exists; a real `_shutdown()` that stops the worker and `quit()/wait()`s the thread before `QApplication.quit()`. |
| 24 | `ui/gui/app.py:248-276,563-575` | medium | Scan and calibration `QThread`s are never `wait()`-joined; closing the window / starting a second run during an in-flight 8 s scan can destroy a running `QThread` ("destroyed while still running" → abort/UB). → `finished→deleteLater`, join in shutdown, and block re-entry while `isRunning()`. |
| 25 | `platform/lockstate_windows.py:100,302-314` | medium | `RegisterClassW` return is unchecked (a 2nd observer instance hits `ERROR_CLASS_ALREADY_EXISTS`, cross-wiring session callbacks); the `except` teardown leaks the WTS registration + window if it fires after registration. → check the ATOM / per-instance class name; unregister+destroy in the except path. |
| 26 | `platform/lockstate_linux.py:150-161` | medium | `close()` needs a running loop to schedule `disconnect()`; called during shutdown with no loop it silently no-ops, leaking the system-bus connection and signal matches (stale callbacks can still fire). → disconnect synchronously when no loop is running; null out subscribers on close. |
| 27 | `core/events.py:35-44,76-78` | medium | `rotate-check-then-append` is not process-safe; two writers (service + a manual `status`/`tray`) can clobber the `.1` file or interleave torn lines. → an OS file lock around rotate+append, or an explicit documented single-writer contract. |

### Lower-value / hardening batch (fix opportunistically)

`viewmodel.address_is_private` docstring vs the reserved `0b10` case and the
"assumed-random" caveat (`viewmodel.py:187-200`); the dead 16-bit branch in
`_has_service` (`viewmodel.py:181-184`); calibration-wizard re-validate empty
address + defensive `_use_button` reset on rescan (`app.py`); `EventLog.tail()`
reads whole files (perf, not correctness); `stavau pair --address` help clarity;
radio-off reporting latency up to ~5 s (`session.py:209-219`, reporting-only).

---

## Execution plan — waves, cards, guards

**Topology** (as in `agent-orchestration.md`): one worker per card in an
isolated worktree; sequential cherry-pick integration; an adversarial reviewer
per safety-critical card whose sole job is to prove the specific failure
scenario is closed *and* that no new fail-open was introduced. No recursive
delegation (rule 7). Safety-critical cards (session, autounlock, integration,
monitor) run on the top model tier.

**Per-card Definition of Done (no-regression discipline):**
1. A **failing-first** regression test reproducing the scenario in the finding.
2. The fix; the test now passes.
3. Full suite green (`pytest`), `mypy --strict` **and** `mypy --platform linux`
   **and** `--platform darwin` (mandatory for ctypes/winrt/dbus code), `ruff
   format` + `ruff check`.
4. Adversarial review CONFIRMED for critical/high cards.
5. `[HV]` items flagged for the batched hardware checkpoint (they cannot be
   proven in CI without a radio).

### Wave 0 — foundation (unblocks the rest, land first)
- **CARD-H0 · `config/settings.py`** — finding 7 (bounds + coercion) and 3
  (unconditional auto-unlock bounds), plus the schema fields Wave 1 needs:
  `dwell_seconds > 0` rule and a new `integration_presence_max_age` setting.
  Landing settings first means Wave 1 cards don't collide on this file.

### Wave 1 — fail-open / fail-safe (critical + high; sequential integration, top tier)
- **CARD-H1 · `core/integration.py`** — finding 18 (MQTT presence freshness). *[HV: round-trip with Davide's Home Assistant.]*
- **CARD-H2 · `core/autounlock.py`** — findings 19 (T9 expectation bound + edge-driven origin) and 1 (dwell). *[HV: Linux unlock path.]*
- **CARD-H3 · `core/session.py`** — findings 8 (retry gating), 20 (breaker-pause re-assert). Coordinates with H2 on the observed-lock signal.
- **CARD-H4 · `cli.py`** — finding 22 (pre-tick fail-safe lock + loud precautionary-lock failure).

*H2/H3 both touch the session↔autounlock seam; integrate H2 then H3 and re-run the auto-unlock + breaker end-to-end tests together.*

### Wave 2 — robustness (medium; parallel worktrees)
- **CARD-H5 · `core/monitor.py`** — finding 21 (tracker lock). Safety-adjacent.
- **CARD-H6 · strategies** (`classic.py`, `advmonitor.py`, `gattlink.py`) — findings 9, 10, 11, 12, 17. *[HV: adv_monitor/gatt_link on real Linux.]*
- **CARD-H7 · platform observers** (`lockstate_windows.py`, `lockstate_linux.py`, `radiostate.py`) — findings 25, 26, 15, 16. *[HV: Win + Linux.]*
- **CARD-H8 · `ui/gui/app.py`** — findings 23, 24. *[HV: GUI visual close/scan/calibration.]*

### Wave 3 — polish (easy/trivial; parallel, mechanical, lower tier)
- **CARD-H9 · `ui/gui/viewmodel.py` + `core/deviceid.py`** — findings 5, 4 (+ viewmodel doc nits).
- **CARD-H10 · `core/events.py`** — findings 14, 27.
- **CARD-H11 · packaging** — finding 13 (+ smoke-test the frozen binary imports paho/PySide6). *[HV: run a bundle per OS.]*
- **CARD-H12 · nits batch** — findings 2, 6 + the hardening batch above.

### Sequencing rationale
Risk-first, dependency-aware: Wave 0 removes the shared-file collision and gives
Wave 1 its config knobs; Wave 1 closes the three genuine fail-opens (MQTT
stickiness, T9 misclassification, the returning-user relock) plus the pre-tick
gap before anything cosmetic; Waves 2–3 are independent by file and parallelize
cleanly. Each `[HV]` tag collects into one batched hardware session rather than
blocking a wave.
