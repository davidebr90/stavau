# stavau — Threat Model

> **Positioning statement (repeat it everywhere):** stavau is a *convenience layer* that reduces the window of exposure of an unlocked screen. It is **not** an authentication mechanism and must never be the only barrier protecting a workstation. Password/PIN/biometrics and full-disk encryption remain the security boundary.

## Assets

| Asset | Description |
|---|---|
| A1 | Content visible/accessible on the unlocked workstation (documents, sessions, credentials) |
| A2 | Local stavau configuration (bonded device identity, thresholds) |
| A3 | Local event log (lock/unlock timestamps → presence patterns) |
| A4 | Integrity of the stavau process itself (it decides when to lock) |

## Trust boundaries

- The **OS Bluetooth stack and bonding storage** are trusted.
- The **radio environment is untrusted**: anyone can transmit, record and replay BLE traffic.
- The **local user account is trusted**; other OS accounts are not (config/log files use per-user permissions).

## Risk register

| # | Threat | Impact | Likelihood | Mitigation |
|---|---|---|---|---|
| T1 | **BLE MAC address spoofing** — attacker advertises with the trust device's address to appear "present" | Screen kept unlocked while user is away (defeats A1 protection) | Medium | stavau does **not** trust advertisement MAC addresses. Presence = active link to the **bonded** device (OS-level pairing keys). Documented residual risk: none via plain spoofing; see T2 for relay. |
| T2 | **BLE relay / range-extension attack** — attacker relays radio between the distant phone and the PC, faking proximity | Screen not locked, or (worse) auto-unlock triggered | Low in opportunistic scenarios; realistic for targeted attacks | Auto-unlock **off by default** and gated behind an explicit "I understand the risk" setting. Lock-side impact limited: relay must be sustained continuously to *prevent* locking. Documented as known limitation in README. |
| T3 | **MAC randomization of the trust device** (iOS/Android rotate advertised addresses ~every 15 min) | Availability failure: device "disappears" → spurious locks (annoyance → user disables stavau) | High if naive scanning is used | Design decision: track the **bonded connection**, not advertisements. Empirically confirmed during project smoke test (majority of scanned devices showed randomized, unnamed addresses). |
| T4 | **RSSI noise / multipath** (walls, bodies, interference) | False positives (spurious locks) or delayed locks | High (physics) | Moving-average smoothing, Schmitt-trigger hysteresis (distinct leave/return thresholds), configurable grace time, guided per-environment calibration. Accepted accuracy: ±1.5 m indoors. |
| T5 | **Radio jamming / Bluetooth off / adapter failure** | Denial of service | Low | **Fail-safe policy:** link loss or stack failure ⇒ screen locks (never fails open). Jamming therefore causes a lock, not an exposure. |
| T6 | **Tampering with stavau process or config** by someone at the unlocked machine | Disable protection silently (A4) | Medium | Config files with per-user permissions; tray icon always reflects real state; event log records daemon stop/start. Out of scope: an attacker at an *unlocked* session already has full user powers — this is exactly the scenario stavau shrinks. |
| T7 | **Event log leakage** — lock/unlock history reveals presence/absence patterns (A3) | Privacy leak (stalking, workplace surveillance) | Low-Medium | Log is local-only, per-user permissions, no device identifiers beyond a user-chosen alias, size-capped, one-command purge (`stavau log --clear`). No remote transmission — project invariant. |
| T8 | **Supply chain** — malicious dependency exfiltrates data | Total (A1–A4) | Low | Minimal dependency set, pinned versions + lockfile, license & network-call review in PR checklist, CI runs `pip-audit`. |
| T9 | **Auto-unlock replay/abuse** (if user enables the advanced feature) | Unlocking without user presence | Medium *when enabled* | Feature off by default; when on: requires bonded link + distance below a *stricter* threshold for N consecutive seconds; never after manual lock; prominent warning in UI and docs. |

## Non-goals (explicitly out of scope)

- Defending against an attacker with physical access to an **unlocked** session or with admin rights.
- Precise indoor positioning. RSSI gives a noisy distance *estimate*, good enough for a 1–10 m threshold with hysteresis — not for localization.
- Replacing OS authentication in any form.

## Telemetry policy

None by default, forever. If an opt-in diagnostic upload is ever added, it must be: explicit (off by default), documented in README + privacy section, reviewable (plain-text payload preview before send), and stripped of identifiers.
