"""Shared fixtures for session-level tests.

The virtual clock drives MonitorSession deterministically: each fake sleep
advances a monotonic counter by one second, so multi-minute scenarios run
instantly. Shared here because both test_session and test_lockstate script
the same monitoring loop.
"""

from __future__ import annotations

import pytest

from stavau.core import session as session_mod


@pytest.fixture
def virtual_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = [0.0]

    async def fake_sleep(_seconds: float) -> None:
        holder[0] += 1.0

    monkeypatch.setattr(session_mod.time, "monotonic", lambda: holder[0])
    monkeypatch.setattr(session_mod, "_sleep", fake_sleep)
