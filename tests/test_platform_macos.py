import subprocess
from collections.abc import Sequence
from typing import Any

import pytest

from stavau.platform.base import LockError
from stavau.platform.macos import MacLocker


class FakeCompleted:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


class TestMacLocker:
    def test_uses_cgsession_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[Sequence[str]] = []

        def fake_run(command: Sequence[str], **kwargs: Any) -> FakeCompleted:
            calls.append(command)
            return FakeCompleted(0)

        monkeypatch.setattr("stavau.platform.macos.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr("stavau.platform.macos.subprocess.run", fake_run)
        MacLocker().lock()
        assert len(calls) == 1
        assert calls[0][0].endswith("CGSession")
        assert calls[0][1] == "-suspend"

    def test_falls_back_to_pmset_when_cgsession_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Sequence[str]] = []

        def fake_which(name: str) -> str | None:
            return None if name.endswith("CGSession") else f"/bin/{name}"

        def fake_run(command: Sequence[str], **kwargs: Any) -> FakeCompleted:
            calls.append(command)
            return FakeCompleted(0)

        monkeypatch.setattr("stavau.platform.macos.shutil.which", fake_which)
        monkeypatch.setattr("stavau.platform.macos.subprocess.run", fake_run)
        MacLocker().lock()
        assert calls[0][0] == "pmset"
        assert calls[0][1] == "displaysleepnow"

    def test_all_backends_failing_raises_lock_error_with_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("stavau.platform.macos.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(
            "stavau.platform.macos.subprocess.run",
            lambda command, **kwargs: FakeCompleted(1, stderr="nope"),
        )
        with pytest.raises(LockError, match="CGSession") as exc_info:
            MacLocker().lock()
        assert "pmset" in str(exc_info.value)
        assert "nope" in str(exc_info.value)

    def test_timeout_is_recorded_and_next_backend_tried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(command: Sequence[str], **kwargs: Any) -> FakeCompleted:
            if command[0].endswith("CGSession"):
                raise subprocess.TimeoutExpired(cmd=command[0], timeout=10)
            return FakeCompleted(0)

        monkeypatch.setattr("stavau.platform.macos.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr("stavau.platform.macos.subprocess.run", fake_run)
        MacLocker().lock()  # succeeds via fallback
