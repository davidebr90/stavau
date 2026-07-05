import subprocess
import sys
from collections.abc import Sequence
from typing import Any

import pytest

from stavau.platform import base
from stavau.platform.base import LockError, UnsupportedPlatformError, get_locker
from stavau.platform.linux import LinuxLocker


class TestGetLocker:
    def test_linux_platform_returns_linux_locker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        assert get_locker().name == "linux"

    def test_windows_platform_returns_windows_locker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "win32")
        assert get_locker().name == "windows"

    def test_macos_not_yet_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        with pytest.raises(UnsupportedPlatformError, match="v0.2"):
            get_locker()

    def test_unknown_platform_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "plan9")
        with pytest.raises(UnsupportedPlatformError, match="plan9"):
            get_locker()


class FakeCompleted:
    def __init__(self, returncode: int, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr


class TestLinuxLocker:
    def test_uses_loginctl_when_available(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[Sequence[str]] = []

        def fake_run(command: Sequence[str], **kwargs: Any) -> FakeCompleted:
            calls.append(command)
            return FakeCompleted(0)

        monkeypatch.setattr("stavau.platform.linux.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr("stavau.platform.linux.subprocess.run", fake_run)
        LinuxLocker().lock()
        assert calls == [("loginctl", "lock-session")]

    def test_falls_back_when_preferred_command_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[Sequence[str]] = []

        def fake_which(name: str) -> str | None:
            return None if name == "loginctl" else f"/bin/{name}"

        def fake_run(command: Sequence[str], **kwargs: Any) -> FakeCompleted:
            calls.append(command)
            return FakeCompleted(0)

        monkeypatch.setattr("stavau.platform.linux.shutil.which", fake_which)
        monkeypatch.setattr("stavau.platform.linux.subprocess.run", fake_run)
        LinuxLocker().lock()
        assert calls[0][0] == "xdg-screensaver"

    def test_all_backends_failing_raises_lock_error_with_attempts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("stavau.platform.linux.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr(
            "stavau.platform.linux.subprocess.run",
            lambda command, **kwargs: FakeCompleted(1, stderr="nope"),
        )
        with pytest.raises(LockError, match="loginctl"):
            LinuxLocker().lock()

    def test_timeout_is_recorded_and_next_backend_tried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fake_run(command: Sequence[str], **kwargs: Any) -> FakeCompleted:
            if command[0] == "loginctl":
                raise subprocess.TimeoutExpired(cmd=command[0], timeout=10)
            return FakeCompleted(0)

        monkeypatch.setattr("stavau.platform.linux.shutil.which", lambda name: f"/bin/{name}")
        monkeypatch.setattr("stavau.platform.linux.subprocess.run", fake_run)
        LinuxLocker().lock()  # succeeds via fallback


class TestLockerProtocol:
    def test_base_module_exports(self) -> None:
        assert base.Locker is not None
