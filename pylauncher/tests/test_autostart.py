"""Start Yu'lon in the tray when the player signs in (T540): one entry per system.

Windows: a value under HKCU `...\\CurrentVersion\\Run` (a fake `winreg` here).
Linux: an XDG autostart `.desktop` file. macOS: a LaunchAgent plist. Each names
the installed Yu'lon with `--tray`; a source checkout has nothing to name.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any

import pytest

from yulon import autostart
from yulon.selfupdate.detect import Install, InstallKind


class FakeWinreg:
    """The calls `autostart` makes, over one dict per key."""

    HKEY_CURRENT_USER = "HKCU"
    KEY_SET_VALUE = 2
    KEY_READ = 1
    REG_SZ = 1

    def __init__(self) -> None:
        self.values: dict[str, tuple[str, int]] = {}

    class _Key:
        def __enter__(self) -> FakeWinreg._Key:
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    def OpenKey(self, hive: str, sub: str, reserved: int = 0, access: int = 1) -> Any:  # noqa: N802
        assert hive == "HKCU" and sub == autostart.RUN_KEY
        return FakeWinreg._Key()

    def CreateKeyEx(
        self, hive: str, sub: str, reserved: int = 0, access: int = 1
    ) -> Any:  # noqa: N802
        return self.OpenKey(hive, sub)

    def QueryValueEx(self, key: Any, name: str) -> tuple[str, int]:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        return self.values[name]

    def SetValueEx(
        self, key: Any, name: str, reserved: int, kind: int, value: str
    ) -> None:  # noqa: N802
        self.values[name] = (value, kind)

    def DeleteValue(self, key: Any, name: str) -> None:  # noqa: N802
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def _installed(tmp_path: Path, kind: InstallKind, exe_name: str = "yulon") -> Install:
    folder = tmp_path / "Yu'lon here"
    folder.mkdir(exist_ok=True)
    if kind is InstallKind.APPIMAGE:
        target = folder / "Yulon-x86_64.AppImage"
        target.write_text("")
        return Install(kind, target, "", True)
    (folder / exe_name).write_text("")
    return Install(kind, folder, exe_name, True)


# ------------------------------------------------------------ what is launched


def test_a_source_checkout_cannot_start_at_sign_in(tmp_path: Path) -> None:
    source = Install(InstallKind.SOURCE, None, "", False)
    assert autostart.launch_program(source) is None
    assert "installed" in (autostart.why_not(source, platform_id="linux") or "")


def test_the_appimage_is_launched_by_its_own_file(tmp_path: Path) -> None:
    install = _installed(tmp_path, InstallKind.APPIMAGE)
    assert autostart.launch_program(install) == install.target


def test_a_folder_install_is_launched_by_its_executable(tmp_path: Path) -> None:
    install = _installed(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    assert install.target is not None
    assert autostart.launch_program(install) == install.target / "yulon.exe"


# ------------------------------------------------------------------- Windows


def test_windows_writes_a_quoted_run_value_with_tray(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reg = FakeWinreg()
    monkeypatch.setattr(autostart, "_winreg", lambda: reg)
    install = _installed(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    assert not autostart.is_enabled(install, platform_id="windows")
    autostart.set_enabled(True, install, platform_id="windows")
    value, kind = reg.values[autostart.RUN_VALUE]
    assert kind == reg.REG_SZ
    exe = tmp_path / "Yu'lon here" / "yulon.exe"
    # QUOTED: an unquoted path with a space sends Windows hunting `C:\\Program.exe` first.
    assert value == f'"{exe}" --tray'
    assert autostart.is_enabled(install, platform_id="windows")
    autostart.set_enabled(False, install, platform_id="windows")
    assert autostart.RUN_VALUE not in reg.values
    autostart.set_enabled(False, install, platform_id="windows")  # absent is fine


def test_windows_calls_a_run_value_for_a_deleted_build_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """rust-main's rule: an entry naming a file that is gone starts nothing."""
    reg = FakeWinreg()
    monkeypatch.setattr(autostart, "_winreg", lambda: reg)
    reg.values[autostart.RUN_VALUE] = (f'"{tmp_path / "gone.exe"}" --tray', reg.REG_SZ)
    install = _installed(tmp_path, InstallKind.WINDOWS_ZIP, "yulon.exe")
    assert not autostart.is_enabled(install, platform_id="windows")


# --------------------------------------------------------------------- Linux


def test_linux_writes_an_xdg_autostart_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    install = _installed(tmp_path, InstallKind.APPIMAGE)
    assert not autostart.is_enabled(install, platform_id="linux")
    autostart.set_enabled(True, install, platform_id="linux")
    entry = config / "autostart" / "org.dadsmmolab.yulon.desktop"
    text = entry.read_text(encoding="utf-8")
    assert "[Desktop Entry]" in text
    assert "Type=Application" in text
    assert f'Exec="{install.target}" --tray' in text
    assert autostart.is_enabled(install, platform_id="linux")
    assert not list((config / "autostart").glob("*.tmp")), "a temporary file was left"
    autostart.set_enabled(False, install, platform_id="linux")
    assert not entry.exists()


def test_linux_quotes_what_the_desktop_spec_reserves(tmp_path: Path) -> None:
    assert autostart.desktop_exec(Path('/a b/$x"y`z\\w%')) == '"/a b/\\$x\\"y\\`z\\\\w%%" --tray'


def test_linux_falls_back_to_dot_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert autostart.desktop_entry_path() == tmp_path / ".config" / "autostart" / (
        "org.dadsmmolab.yulon.desktop"
    )


# --------------------------------------------------------------------- macOS


def test_macos_writes_a_launch_agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    exe = tmp_path / "Yulon.app" / "Contents" / "MacOS" / "yulon"
    exe.parent.mkdir(parents=True)
    exe.write_text("")
    monkeypatch.setattr(autostart.sys, "executable", str(exe))
    install = Install(InstallKind.MACOS_APP, None, "", False)
    autostart.set_enabled(True, install, platform_id="macos")
    plist = tmp_path / "Library" / "LaunchAgents" / "org.dadsmmolab.yulon.plist"
    data = plistlib.loads(plist.read_bytes())
    assert data["Label"] == "org.dadsmmolab.yulon"
    assert data["ProgramArguments"] == [str(exe), "--tray"]
    assert data["RunAtLoad"] is True
    assert autostart.is_enabled(install, platform_id="macos")
    autostart.set_enabled(False, install, platform_id="macos")
    assert not plist.exists()
