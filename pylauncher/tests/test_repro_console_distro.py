"""Repro: does the Console tab's Send button reach a WSL-resident daemon?"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import platform
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import console
from yulon.ui.controller_view import ControllerServices

WOTLK = load_catalog().get("wow-wotlk")


@pytest.fixture
def wsl_only_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tester's machine: no Docker Desktop, wsl.exe present, docker inside the distro."""
    monkeypatch.setattr(platform, "docker_program", lambda: None)
    monkeypatch.setattr(
        platform,
        "_which",
        lambda name: r"C:\Windows\System32\wsl.exe" if name == platform.WSL_PROGRAM else None,
    )


def test_positive_control_the_seam_itself_works(wsl_only_host: None) -> None:
    assert console.attach_argv("ac-worldserver", wsl_distro="dml-arch") == [
        r"C:\Windows\System32\wsl.exe",
        "-d",
        "dml-arch",
        "--",
        "docker",
        "attach",
        "--sig-proxy=false",
        "ac-worldserver",
    ]


def test_what_the_console_tab_actually_does(
    wsl_only_host: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Drive the REAL production wiring for a WSL-resident install."""
    seen: list[object] = []
    real = console.attach_argv

    def spy(container: str, *, wsl_distro: str | None = None) -> list[str]:
        seen.append(wsl_distro)
        return real(container, wsl_distro=wsl_distro)

    monkeypatch.setattr(console, "attach_argv", spy)
    monkeypatch.setattr(console, "pty_supported", lambda: True)  # get past the pty gate

    services = ControllerServices.for_wotlk(WOTLK, tmp_path, None, wsl_distro="dml-arch")
    with pytest.raises(console.ConsoleError) as exc:
        services.send_console("server info")

    print(f"\nattach_argv() received wsl_distro={seen!r}")
    print(f"send_command() raised: {exc.value}")
    assert seen == [None]
    assert platform.DOCKER_CLI_MISSING_HELP in str(exc.value)


# ---------------------------------------------------------------- the refutation attempt


def test_is_that_path_reachable_on_the_testers_windows_box(
    wsl_only_host: None, tmp_path: Path
) -> None:
    """A distro implies wsl.exe implies Windows. Windows has no pty."""
    import os
    import sys

    print(f"\nsys.platform={sys.platform!r}  hasattr(os,'openpty')={hasattr(os, 'openpty')}")
    print(f"console.pty_supported()={console.pty_supported()}")

    services = ControllerServices.for_wotlk(WOTLK, tmp_path, None, wsl_distro="dml-arch")
    with pytest.raises(console.ConsoleError) as exc:
        services.send_console("server info")
    print(f"UNPATCHED send_console raised: {str(exc.value)[:90]}")
    assert platform.DOCKER_CLI_MISSING_HELP not in str(exc.value)
    assert "needs a terminal" in str(exc.value)


def test_the_send_button_a_wsl_user_actually_sees(
    qapp: object, wsl_only_host: None, tmp_path: Path
) -> None:
    from yulon.ui.controller_view import ControllerView

    services = ControllerServices.for_wotlk(WOTLK, tmp_path, None, wsl_distro="dml-arch")
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    print(f"\nsend_button.isEnabled()={view.send_button.isEnabled()}")
    print(f"console_note visible={view.console_note.isVisible()}")
    print(f"console_note={view.console_note.text()[:80]!r}")
    assert view.send_button.isEnabled() is (console.pty_supported())
