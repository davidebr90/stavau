## What & why

Closes #<issue>

Short description of the change and its motivation.

## Checklist

- [ ] Tests added/updated (core algorithm changes include recorded-trace fixtures)
- [ ] `ruff check`, `ruff format --check`, `mypy`, `pytest` pass locally
- [ ] `CHANGELOG.md` updated under `[Unreleased]`
- [ ] No new network calls; no new runtime dependency (or justified in the description with license check)
- [ ] Error paths fail **safe** (locked / refuse to start), never open
- [ ] No sensitive data added to logs
- [ ] Commits signed off (DCO, `git commit -s`)

## Platforms tested

- [ ] Linux
- [ ] Windows
- [ ] macOS
- [ ] n/a (platform-independent, covered by unit tests)
