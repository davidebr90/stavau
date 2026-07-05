# Security Policy

## Supported versions

stavau is pre-1.0: only the latest release (and `main`) receive security fixes.

| Version | Supported |
|---|---|
| latest release / `main` | ✅ |
| anything older | ❌ |

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Report privately via **GitHub Security Advisories** ("Report a vulnerability" on the repository's Security tab) or by email to **davidebr90@gmail.com** with subject `[stavau security]`.

Please include: affected version/commit, OS and Bluetooth stack, reproduction steps, and impact assessment (e.g. "screen stays unlocked when…").

You can expect an acknowledgement within **72 hours** and a status update within **14 days**. Coordinated disclosure is appreciated; credit will be given in the release notes unless you prefer otherwise.

## Scope notes

- stavau is explicitly documented as a **convenience layer**, not an authentication boundary. Reports demonstrating that BLE proximity can be spoofed *in general* (MAC spoofing, relay attacks) are known limitations documented in [docs/threat-model.md](docs/threat-model.md) — unless they defeat a specific mitigation we claim to have.
- The highest-severity class of bug for this project: **any code path where a failure leaves the screen unlocked when it should have locked** (fail-open). Report these immediately.
