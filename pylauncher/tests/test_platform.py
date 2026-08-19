"""Tests for `yulon.platform` (roadmap Phase 1.2)."""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import platform


@pytest.mark.parametrize(
    ("sys_platform", "expected"),
    [
        ("win32", "windows"),
        ("cygwin", "windows"),
        ("darwin", "macos"),
        ("linux", "linux"),
        ("linux2", "linux"),
    ],
)
def test_detect_returns_normalized_platform(
    monkeypatch: pytest.MonkeyPatch, sys_platform: str, expected: str
) -> None:
    """`detect()` collapses sys.platform to linux/windows/macos."""
    monkeypatch.setattr(platform.sys, "platform", sys_platform)
    assert platform.detect() == expected


def test_detect_falls_back_to_linux_for_unrecognized_platform(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unrecognized sys.platform (e.g. a BSD) defaults to linux, with a warning."""
    monkeypatch.setattr(platform.sys, "platform", "freebsd13")
    with caplog.at_level("WARNING"):
        assert platform.detect() == "linux"
    assert "freebsd13" in caplog.text


def test_config_dir_linux_uses_xdg_data_home(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Linux, XDG_DATA_HOME (when set) is honored."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/tmp/xdg")
    assert platform.config_dir() == Path("/tmp/xdg/yulon")


def test_config_dir_linux_defaults_to_dot_local_share(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Linux without XDG_DATA_HOME, ~/.local/share/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/home/testuser"))
    assert platform.config_dir() == Path("/home/testuser/.local/share/yulon")


def test_config_dir_windows_uses_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    """On Windows, %APPDATA%\\yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    appdata = r"C:\Users\test\AppData\Roaming"
    monkeypatch.setenv("APPDATA", appdata)
    # Path equality is separator-independent; assert component-wise so this test
    # still holds when run on a POSIX host (where Path normalizes to '/').
    assert platform.config_dir() == Path(appdata) / "yulon"


def test_config_dir_windows_falls_back_when_appdata_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows without APPDATA set, ~/AppData/Roaming/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "win32")
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/test"))
    assert platform.config_dir() == Path("/Users/test/AppData/Roaming/yulon")


def test_config_dir_linux_treats_empty_xdg_data_home_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty (but set) XDG_DATA_HOME is treated the same as unset."""
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/home/testuser"))
    assert platform.config_dir() == Path("/home/testuser/.local/share/yulon")


def test_config_dir_macos_uses_application_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On macOS, ~/Library/Application Support/yulon is used."""
    monkeypatch.setattr(platform.sys, "platform", "darwin")
    monkeypatch.setattr(platform.Path, "home", lambda: Path("/Users/test"))
    assert platform.config_dir() == Path("/Users/test/Library/Application Support/yulon")


def test_ensure_docker_not_implemented() -> None:
    """`ensure_docker()` is stubbed until Phase 5."""
    with pytest.raises(NotImplementedError):
        platform.ensure_docker()


def test_ensure_wsl2_not_implemented() -> None:
    """`ensure_wsl2()` is stubbed until Phase 5."""
    with pytest.raises(NotImplementedError):
        platform.ensure_wsl2()
