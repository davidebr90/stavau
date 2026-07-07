"""CLI-level tests for the fail-safe abort path of `stavau run`.

The monitoring loop is faked; these tests pin the invariant that an ARMED
`run` that crashes — even before its first tick — locks the screen before
exiting, and that a failure of that precautionary lock is surfaced, not hidden.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from stavau import cli
from stavau.config.settings import Settings
from stavau.platform.base import LockError


class FakeLocker:
    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.lock_calls = 0
        self._fail = fail

    def lock(self) -> None:
        self.lock_calls += 1
        if self._fail:
            raise LockError("simulated precautionary lock failure")


class _DiesBeforeTicking:
    """A stand-in MonitorSession whose run() raises before any on_tick."""

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    async def run(self, **_kwargs: object) -> None:
        raise RuntimeError("source failed to start")


def _patch_common(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, locker: FakeLocker | None
) -> None:
    settings = Settings(device_address="AA:BB:CC:DD:EE:FF", device_alias="test")
    monkeypatch.setattr(Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(cli, "event_log_path", lambda: tmp_path / "events.jsonl")
    monkeypatch.setattr(cli, "get_locker", lambda: locker)
    monkeypatch.setattr(cli, "MonitorSession", _DiesBeforeTicking)

    async def _radio_off() -> bool:
        return False

    monkeypatch.setattr("stavau.core.radiostate.radio_available", _radio_off)


def test_armed_crash_before_first_tick_locks_fail_safe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    locker = FakeLocker()
    _patch_common(monkeypatch, tmp_path, locker)
    rc = cli.cmd_run(argparse.Namespace(dry_run=False, duration=None))
    assert rc == 3
    assert locker.lock_calls == 1  # locked despite dying before ticking


def test_precautionary_lock_failure_is_reported_not_suppressed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    locker = FakeLocker(fail=True)
    _patch_common(monkeypatch, tmp_path, locker)
    rc = cli.cmd_run(argparse.Namespace(dry_run=False, duration=None))
    assert rc == 3
    assert locker.lock_calls == 1
    err = capsys.readouterr().err
    assert "precautionary lock FAILED" in err


def test_dry_run_crash_does_not_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Dry-run is never armed: get_locker() is not called and nothing locks.
    locker = FakeLocker()
    _patch_common(monkeypatch, tmp_path, locker)
    rc = cli.cmd_run(argparse.Namespace(dry_run=True, duration=None))
    assert rc == 3
    assert locker.lock_calls == 0
