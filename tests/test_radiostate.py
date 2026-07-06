import asyncio

import pytest

from stavau.core import radiostate


class TestDispatchByPlatform:
    def test_windows_dispatches_to_windows_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.sys, "platform", "win32")

        async def fake_windows() -> bool | None:
            return True

        monkeypatch.setattr(radiostate, "_windows_radio_available", fake_windows)
        assert asyncio.run(radiostate.radio_available()) is True

    def test_linux_dispatches_to_linux_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.sys, "platform", "linux")

        async def fake_linux() -> bool | None:
            return False

        monkeypatch.setattr(radiostate, "_linux_radio_available", fake_linux)
        assert asyncio.run(radiostate.radio_available()) is False

    def test_macos_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.sys, "platform", "darwin")
        assert asyncio.run(radiostate.radio_available()) is None

    def test_unrecognized_platform_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.sys, "platform", "some-other-os")
        assert asyncio.run(radiostate.radio_available()) is None


class TestLinuxProbe:
    def test_powered_yes_is_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.shutil, "which", lambda name: "/usr/bin/bluetoothctl")

        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 0, "Controller AA:BB:CC:DD:EE:FF\n\tPowered: yes\n\tDiscoverable: no\n"

        monkeypatch.setattr(radiostate, "_run", fake_run)
        assert asyncio.run(radiostate._linux_radio_available()) is True

    def test_powered_no_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.shutil, "which", lambda name: "/usr/bin/bluetoothctl")

        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 0, "Controller AA:BB:CC:DD:EE:FF\n\tPowered: no\n"

        monkeypatch.setattr(radiostate, "_run", fake_run)
        assert asyncio.run(radiostate._linux_radio_available()) is False

    def test_missing_bluetoothctl_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.shutil, "which", lambda name: None)

        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            raise AssertionError("_run must not be called when bluetoothctl is missing")

        monkeypatch.setattr(radiostate, "_run", fake_run)
        assert asyncio.run(radiostate._linux_radio_available()) is None

    def test_nonzero_exit_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.shutil, "which", lambda name: "/usr/bin/bluetoothctl")

        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 1, ""

        monkeypatch.setattr(radiostate, "_run", fake_run)
        assert asyncio.run(radiostate._linux_radio_available()) is None

    def test_unparseable_output_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.shutil, "which", lambda name: "/usr/bin/bluetoothctl")

        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            return 0, "No default controller available\n"

        monkeypatch.setattr(radiostate, "_run", fake_run)
        assert asyncio.run(radiostate._linux_radio_available()) is None

    def test_subprocess_exception_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate.shutil, "which", lambda name: "/usr/bin/bluetoothctl")

        async def fake_run(cmd: list[str], timeout: float) -> tuple[int, str]:
            raise OSError("boom")

        monkeypatch.setattr(radiostate, "_run", fake_run)
        assert asyncio.run(radiostate._linux_radio_available()) is None


class TestWindowsProbe:
    """Exercises the Windows probe logic via a faked winrt module tree.

    winrt is not installed/importable off Windows, so we cannot monkeypatch
    real attributes on it. Instead we fake `_winrt_available` (the
    availability probe) and inject a fake module into sys.modules so the
    `from winrt.windows.devices.radios import ...` inside the function
    resolves to our stand-ins. This keeps the test runnable on any OS while
    still exercising the real dispatch/parsing logic in
    `_windows_radio_available`.
    """

    @staticmethod
    def _install_fake_winrt(monkeypatch: pytest.MonkeyPatch, radios: list[object]) -> None:
        import sys
        import types

        class FakeRadioKind:
            BLUETOOTH = "bluetooth"
            OTHER = "other"

        class FakeRadioState:
            ON = "on"
            OFF = "off"

        class FakeRadio:
            @staticmethod
            async def get_radios_async() -> list[object]:
                return radios

        fake_module = types.ModuleType("winrt.windows.devices.radios")
        fake_module.Radio = FakeRadio  # type: ignore[attr-defined]
        fake_module.RadioKind = FakeRadioKind  # type: ignore[attr-defined]
        fake_module.RadioState = FakeRadioState  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "winrt.windows.devices.radios", fake_module)
        monkeypatch.setattr(radiostate, "_winrt_available", lambda: True)

    def test_winrt_unavailable_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate, "_winrt_available", lambda: False)
        assert asyncio.run(radiostate._windows_radio_available()) is None

    def test_bluetooth_radio_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeBtRadio:
            kind = "bluetooth"
            state = "on"

        self._install_fake_winrt(monkeypatch, [FakeBtRadio()])
        assert asyncio.run(radiostate._windows_radio_available()) is True

    def test_bluetooth_radio_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeBtRadio:
            kind = "bluetooth"
            state = "off"

        self._install_fake_winrt(monkeypatch, [FakeBtRadio()])
        assert asyncio.run(radiostate._windows_radio_available()) is False

    def test_no_bluetooth_radio_present_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class FakeOtherRadio:
            kind = "other"
            state = "on"

        self._install_fake_winrt(monkeypatch, [FakeOtherRadio()])
        assert asyncio.run(radiostate._windows_radio_available()) is None

    def test_winrt_exception_is_unknown(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(radiostate, "_winrt_available", lambda: True)

        import sys
        import types

        def _boom() -> None:
            raise RuntimeError("winrt hiccup")

        class FakeRadio:
            @staticmethod
            async def get_radios_async() -> list[object]:
                _boom()
                return []

        fake_module = types.ModuleType("winrt.windows.devices.radios")
        fake_module.Radio = FakeRadio  # type: ignore[attr-defined]
        fake_module.RadioKind = type("FakeRadioKind", (), {"BLUETOOTH": "bluetooth"})
        fake_module.RadioState = type("FakeRadioState", (), {"ON": "on", "OFF": "off"})
        monkeypatch.setitem(sys.modules, "winrt.windows.devices.radios", fake_module)

        assert asyncio.run(radiostate._windows_radio_available()) is None
