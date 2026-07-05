# Acceptance Criteria — Definition of Done per Milestone

Every milestone ships only when **all** its criteria pass and are recorded (test log or CI run linked in the release notes).

## Global criteria (every release)

- [ ] **Fail-safe audit:** every error path (Bluetooth off at runtime, permission revoked, device unreachable, daemon crash/restart, config corrupt) results in a locked screen or a loud refusal to start — never a silently disabled protection.
- [ ] **No-network verification:** run under a network monitor for a full session; zero outbound connections.
- [ ] **Dependency review:** licenses AGPL-3.0-compatible; `pip-audit` clean; no dependency makes undeclared network calls.
- [ ] **Privacy review:** no sensitive data in logs (no raw MAC beyond user alias, no cleartext identifiers beyond the necessary bonded-device handle); log purge command works.
- [ ] CI green on the full OS matrix supported by the milestone.

## v0.1 — MVP (Linux, CLI)

- [ ] Lock triggers within **grace_seconds + 3 s** of physically exceeding the radius (measured with a stopwatch at radius 3 m, 3 runs).
- [ ] **False-positive soak test:** user seated at desk with phone, 30 continuous minutes, zero spurious locks (repeat in 3 environments).
- [ ] Calibration accuracy: estimated distance within **±1.5 m** at 1/3/5/8 m line-of-sight indoor.
- [ ] Bluetooth disabled at runtime ⇒ lock within grace period + clear log entry.
- [ ] Setup refused with actionable error if no bonded device / no adapter.
- [ ] Unit tests: distance model, smoothing, hysteresis state machine (recorded-trace fixtures) ≥ 90% coverage on `core/`.

## v0.2 — Windows & macOS

- [ ] All v0.1 functional tests repeated on Windows 10, Windows 11, macOS ≥ 10.15.
- [ ] macOS wizard verifies "require password immediately after sleep/screensaver" and warns if off.
- [ ] Windows: works without admin privileges.
- [ ] **Cross-device matrix:** ≥ 2 Android models + ≥ 1 Apple device (iPhone or Apple Watch) verified per OS.

## v0.3 — GUI

- [ ] Radius slider (1–10 m) applies without daemon restart.
- [ ] Calibration wizard completable by a first-time user without documentation (hallway test, ≥ 3 users).
- [ ] Settings persist across restarts; corrupt config ⇒ safe defaults + warning, not crash.

## v0.4 — Tray, logs, polish

- [ ] Tray icon reflects real state (near/leaving/away/disconnected) within 2 s of transitions.
- [ ] Event log viewer paginates ≥ 10,000 events without UI freeze; purge works.
- [ ] Dark/light mode follows OS on all three platforms.
- [ ] EN and IT translations complete (100% strings); language switch without restart.

## v1.0 — Hardening & launch

- [ ] Full threat-model review against implementation; every mitigation in `docs/threat-model.md` verified or re-labelled "planned".
- [ ] Auto-unlock (if shipped): off by default, stricter threshold verified, warning flow tested, never fires after manual lock.
- [ ] 7-day continuous run on each OS: RAM stable (no leak), average CPU < 1% on reference hardware.
- [ ] Battery impact on trust device measured and documented.
- [ ] Docs complete: README (EN+IT), all `docs/`, packaged releases with checksums.
- [ ] Submissions: awesome-privacy / awesome-selfhosted lists; F-Droid evaluated **only if** a companion app exists (none planned for 1.0).
