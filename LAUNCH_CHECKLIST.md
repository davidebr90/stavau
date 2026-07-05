# stavau — GitHub Launch Checklist

> Operational checklist for taking the repository public. Delete this file (or move it to an issue) after v0.1 is released.

## 1. Repository setup

- [ ] Create the GitHub repository `davidebr90/stavau` (public), description: *"Privacy by proximity — auto-lock your workstation when you walk away (BLE)"*, topics: `privacy`, `bluetooth-low-energy`, `screen-lock`, `security`, `cross-platform`, `python`.
- [ ] `git init` (done locally), review every file, first commit: `chore: bootstrap project structure and specification`.
- [ ] Push `main`; protect it: require PR + green CI before merge, no force-push.
- [ ] Verify README badges render (CI badge goes green after the first push).

## 2. License & governance

- [ ] Confirm `LICENSE` contains the verbatim AGPL-3.0 text and GitHub auto-detects it ("AGPL-3.0" chip on the repo page).
- [ ] SPDX headers policy decided (recommended: `# SPDX-License-Identifier: AGPL-3.0-or-later` in each source file — add via pre-commit hook).
- [ ] Enable GitHub features: Issues ✅, Discussions (optional), Security advisories ✅ ("Private vulnerability reporting" on).
- [ ] Verify issue templates and PR template appear correctly in the GitHub UI.

## 3. CI/CD

- [ ] First push triggers `ci.yml`; matrix (ubuntu/windows/macos × py3.10/3.12) all green.
- [ ] `pip-audit` job green.
- [ ] Add branch protection rule requiring the `test` jobs.
- [ ] (Later, v1.0) Release workflow: tag → build wheels/PyInstaller bundles → GitHub Release with checksums.

## 4. First release (v0.1.0 — MVP Linux CLI)

- [ ] Implement `core/monitor.py` (Bleak bonded-link RSSI sampling) and `platform/linux.py` (`loginctl lock-session`).
- [ ] Wire `stavau setup|run|status|log` CLI.
- [ ] Pass **all** v0.1 acceptance criteria in [docs/acceptance-criteria.md](docs/acceptance-criteria.md) and record results in the release notes.
- [ ] Update `CHANGELOG.md`: move `[Unreleased]` → `[0.1.0] - YYYY-MM-DD`.
- [ ] Tag `v0.1.0`, create GitHub Release, attach test evidence.

## 5. Announcement & listing (v1.0 timeframe)

- [ ] Screenshot / demo GIF in README (tray + lock in action).
- [ ] Submit to relevant awesome lists (awesome-privacy, awesome-python-applications).
- [ ] Post on r/privacy, r/selfhosted, Hacker News (Show HN) — after the false-positive soak tests pass, not before.
- [ ] F-Droid: only applicable if a companion Android app is ever built (not planned for 1.0).

## 6. Ongoing hygiene

- [ ] Enable Dependabot (pip, github-actions).
- [ ] Label set created: `good first issue`, `help wanted`, `bug`, `enhancement`, `security`, `platform:linux|windows|macos`, `calibration-data`.
- [ ] Milestones created: v0.1 … v1.0 mirroring the roadmap.
