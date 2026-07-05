# Contributing to stavau

Thank you for considering a contribution! stavau is a privacy tool: correctness, auditability and restraint matter more than feature count.

## Ways to contribute

- **Code** — see open issues, especially `good first issue` and `help wanted`.
- **Testing on real hardware** — RSSI behaviour varies wildly across adapters and environments; reproducible reports from your setup are gold.
- **Calibration data** — anonymised RSSI/distance samples from different indoor environments.
- **Translations** — see `src/stavau/locales/`.
- **Docs** — clarity fixes are always welcome.

## Ground rules

1. **No network calls.** Any PR introducing outbound network traffic will be rejected unless it is opt-in, documented, and essential. This is a hard project invariant.
2. **Fail-safe by default.** Error paths must lead to a *locked* screen or a no-op, never to an unlocked/disabled state.
3. **Dependencies are liabilities.** New runtime dependencies need justification (license compatibility with AGPL-3.0, maintenance status, footprint).
4. **OS-specific code stays in `src/stavau/platform/`.** Core logic must remain platform-agnostic and unit-testable without Bluetooth hardware.
5. **Authorship golden rule.** Commit messages, code, comments and docs must never reference AI assistants, AI tools, or their model/product names — no `Co-Authored-By` trailers for non-human authors, no "generated with/by" banners. Repository history must show human contributors only. PRs violating this will be asked to rewrite their commits.

## Development setup

```bash
git clone https://github.com/davidebr90/stavau.git
cd stavau
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pre-commit install
pytest
```

## Coding style

- Python ≥ 3.10, formatted and linted with **ruff** (`ruff format` + `ruff check`), type-checked with **mypy** (strict on `core/`).
- Public functions get docstrings; comments explain *why*, not *what*.
- Conventional Commits for messages: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`, `ci:`, `chore:`.

## Pull request process

1. Fork, create a branch from `main`: `feat/<short-name>` or `fix/<issue-number>`.
2. Add/update tests. Core algorithm changes (smoothing, distance model, hysteresis) **require** unit tests with recorded RSSI traces (see `tests/fixtures/`).
3. Run `ruff check`, `mypy`, `pytest` locally — CI runs them on Windows, macOS and Linux.
4. Update `CHANGELOG.md` under `[Unreleased]`.
5. Open the PR using the template. One logical change per PR.
6. A maintainer reviews within a few days. Squash-merge is the default.

## Reporting bugs

Use the bug report template. Always include: OS + version, Bluetooth adapter, BlueZ version (Linux), trust device model, and — if relevant — a `stavau log --export` excerpt (it contains no personal data, but review it before posting).

**Security vulnerabilities: do NOT open a public issue.** Follow [SECURITY.md](SECURITY.md).

## Developer Certificate of Origin

By contributing you certify the [DCO 1.1](https://developercertificate.org/). Sign your commits with `git commit -s`.

## License of contributions

All contributions are accepted under [AGPL-3.0](LICENSE), the project license.
