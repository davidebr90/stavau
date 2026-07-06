"""Shared fixtures for session-level tests.

The virtual clock drives MonitorSession deterministically: each fake sleep
advances a monotonic counter by one second, so multi-minute scenarios run
instantly. Shared here because both test_session and test_lockstate script
the same monitoring loop.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from stavau.core import session as session_mod
from stavau.i18n import set_language


@pytest.fixture
def virtual_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    holder = [0.0]

    async def fake_sleep(_seconds: float) -> None:
        holder[0] += 1.0

    monkeypatch.setattr(session_mod.time, "monotonic", lambda: holder[0])
    monkeypatch.setattr(session_mod, "_sleep", fake_sleep)


@pytest.fixture(autouse=True)
def _reset_i18n_language() -> Iterator[None]:
    """Every test starts from (and leaves) English: i18n.set_language() is
    process-global module state, so a test that switches languages must never
    leak that choice into an unrelated test running later in the same session.
    """
    set_language("en")
    yield
    set_language("en")
