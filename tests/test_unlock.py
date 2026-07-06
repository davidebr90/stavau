import subprocess
import sys
from typing import Any

import pytest

from stavau.platform import unlock as unlock_mod
from stavau.platform.unlock import LinuxUnlocker, UnlockError, get_unlocker


class TestGetUnlocker:
    def test_none_on_windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Windows has no public unlock API: auto-unlock must be impossible.
        monkeypatch.setattr(sys, "platform", "win32")
        assert get_unlocker() is None

    def test_none_on_macos(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "darwin")
        assert get_unlocker() is None

    def test_linux_with_loginctl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(unlock_mod.shutil, "which", lambda name: f"/bin/{name}")
        unlocker = get_unlocker()
        assert unlocker is not None and unlocker.name == "linux-loginctl"

    def test_linux_without_loginctl(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(unlock_mod.shutil, "which", lambda name: None)
        assert get_unlocker() is None


class TestLinuxUnlocker:
    def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(unlock_mod.subprocess, "run", fake_run)
        LinuxUnlocker().unlock()
        assert calls == [["loginctl", "unlock-session"]]

    def test_nonzero_exit_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, 1, "", "no session")

        monkeypatch.setattr(unlock_mod.subprocess, "run", fake_run)
        with pytest.raises(UnlockError, match="no session"):
            LinuxUnlocker().unlock()

    def test_timeout_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd, 10)

        monkeypatch.setattr(unlock_mod.subprocess, "run", fake_run)
        with pytest.raises(UnlockError):
            LinuxUnlocker().unlock()
