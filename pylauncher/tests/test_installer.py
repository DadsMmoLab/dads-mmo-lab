"""Tests for the Phase 3a installer (`yulon.catalog.installer`, roadmap 3.2/3.3).

`runner.interact` is exercised for real against a tiny bash script that
behaves like the installers (colour codes, `read -r` prompts with and without
trailing newlines); the `Installer` control flow is tested through its seams
so no Docker, network, or two-hour build is involved.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import runner
from yulon.catalog import installer as installer_module
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import (
    PROMPT_RULES,
    DockerUnavailableError,
    Installer,
    InstallerError,
    InstallOptions,
    bash_available,
    make_responder,
)

# Not just `which bash`: on Windows that finds the Store alias for WSL, which fails
# with execvpe(/bin/bash) when no distro is installed (Windows test VM, 2026-08-21).
needs_bash = pytest.mark.skipif(
    not bash_available(), reason="no bash that can run a script on this machine"
)

FAKE_INSTALLER = r"""
W='\033[1;37m'; NC='\033[0m'
echo -e "${W}Where should the server files be installed?${NC}"
echo -ne "  ${W}Install path: ${NC}"
read -r user_input
echo "dir=[$user_input]"
echo -e "${W}Ready to begin? (y/n): ${NC}"
read -r answer
echo "answer=[$answer]"
echo -e "${W}Press ENTER to continue...${NC}"
read -r
echo "Remove it and start fresh? (y/n): "
read -r fresh
echo "fresh=[$fresh]"
echo "building..." >&2
exit ${EXIT_CODE:-0}
"""


@needs_bash
def test_interact_answers_prompts_with_and_without_newlines(tmp_path: Path) -> None:
    """Partial-line prompts (`echo -ne`) and full-line prompts both get answered."""
    script = tmp_path / "fake.sh"
    script.write_text(FAKE_INSTALLER, encoding="utf-8")
    seen: list[str] = []
    respond = make_responder(InstallOptions(server_dir=Path("/srv/wow")))
    lines = list(runner.interact(["bash", str(script)], respond=respond))
    stripped = [runner.strip_ansi(line) for line in lines]
    seen.extend(stripped)
    assert "dir=[/srv/wow]" in stripped
    assert "answer=[y]" in stripped
    assert "fresh=[n]" in stripped  # destructive offer declined by default
    assert "building..." in stripped  # stderr is merged


@needs_bash
def test_interact_raises_on_nonzero_exit_after_yielding_output(tmp_path: Path) -> None:
    script = tmp_path / "fake.sh"
    script.write_text(FAKE_INSTALLER, encoding="utf-8")
    got: list[str] = []
    with pytest.raises(subprocess.CalledProcessError):
        for line in runner.interact(
            ["bash", str(script)], respond=make_responder(InstallOptions()), env={"EXIT_CODE": "3"}
        ):
            got.append(runner.strip_ansi(line))
    assert "dir=[]" in got  # blank = script default dir


def test_prompt_rules_are_ordered_and_decline_optional_offers() -> None:
    respond = make_responder(InstallOptions(reinstall=True, client_dir=Path("/c/wow")))
    assert respond("Enter path to your WoW TBC client folder:") == "/c/wow"
    assert respond("Remove it and start fresh? (y/n): ") == "y"
    assert respond("Open the GitHub README in your browser now? (y/n): ") == "n"
    assert respond("Download wow-manage.sh to your home folder now? (y/n): ") == "n"
    assert respond("Continue anyway? (NOT recommended) (y/n): ") == "n"
    assert respond("Type yes to reset the keyring, or anything else to cancel: ") == "yes"
    assert respond("Ready to build your Playerbots server? (y/n): ") == "y"
    assert respond("Building worldserver 42%") is None
    # Hints that merely MENTION the key are not prompts (the live gate caught this one).
    assert respond("  Leave blank and press ENTER to use the default location.") is None
    assert respond("Press ENTER to continue...") == ""
    assert respond("Press ENTER when done creating accounts...") == ""
    assert respond("  ── or press ENTER to shut down manually ──") == ""
    assert respond("  WoW not detected — press ENTER to shut down.") == ""
    assert respond("Install path: ") == ""  # blank = default when no server_dir was chosen
    assert (
        make_responder(InstallOptions(server_dir=Path("/srv/wow")))("  Install path: ")
        == "/srv/wow"
    )
    assert len(PROMPT_RULES) >= 8


def _fake_interact(calls: list[dict[str, object]]) -> object:
    def interact(command: list[str], **kwargs: object) -> Iterator[str]:
        calls.append({"command": command, **kwargs})
        yield "hello"
        yield "done"

    return interact


def test_installer_runs_the_entry_script_through_interact(tmp_path: Path) -> None:
    """`run()` resolves `<repo_root>/<install.script>`, streams lines, answers via rules."""
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/bash\n", encoding="utf-8")
    calls: list[dict[str, object]] = []
    installer = Installer(
        entry,
        repo_root=tmp_path,
        docker_check=lambda: True,
        interact=_fake_interact(calls),  # type: ignore[arg-type]
        package_manager=lambda: None,
        bash_check=lambda: True,
    )
    assert list(installer.run(InstallOptions(server_dir=Path("/srv")))) == ["hello", "done"]
    assert calls[0]["command"] == ["bash", str(script)]
    assert calls[0]["cwd"] == script.parent
    assert callable(calls[0]["respond"])


@pytest.mark.parametrize(
    ("package_manager", "expected"),
    [
        (None, "archive/guides/wow-wotlk/install-wow-wotlk.sh"),
        ("pacman", "archive/guides/wow-wotlk/install-wow-wotlk.sh"),
        ("apt", "archive/guides/wow-wotlk/install-wow-wotlk-ubuntu.sh"),
        ("dnf", "archive/guides/wow-wotlk/install-wow-wotlk-fedora.sh"),
        ("zypper", "archive/guides/wow-wotlk/install-wow-wotlk.sh"),
    ],
)
def test_installer_picks_the_script_variant_for_the_host_package_manager(
    tmp_path: Path, package_manager: str | None, expected: str
) -> None:
    """Ubuntu/Fedora hosts get the Debian/Fedora ports; everything else the default script."""
    entry = load_catalog().get("wow-wotlk")
    installer = Installer(entry, repo_root=tmp_path, package_manager=lambda: package_manager)
    assert installer.script == tmp_path / expected


def test_script_env_inherits_ours_and_defaults_term(monkeypatch: pytest.MonkeyPatch) -> None:
    """A desktop-launched app has no TERM; the scripts' `clear` needs one. Overrides win."""
    entry = load_catalog().get("wow-wotlk")
    monkeypatch.delenv("TERM", raising=False)
    monkeypatch.setenv("YULON_MARKER", "1")
    env = Installer(entry, env={"EXIT_CODE": "3"}).script_env()
    assert env["TERM"] == installer_module.DEFAULT_TERM
    assert env["YULON_MARKER"] == "1" and env["EXIT_CODE"] == "3"
    monkeypatch.setenv("TERM", "")
    assert Installer(entry).script_env()["TERM"] == installer_module.DEFAULT_TERM
    monkeypatch.setenv("TERM", "screen")
    assert Installer(entry, env={"TERM": "dumb"}).script_env()["TERM"] == "dumb"
    assert Installer(entry).script_env()["TERM"] == "screen"


def test_host_package_manager_is_none_off_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(installer_module.sys, "platform", "win32")
    assert installer_module.host_package_manager() is None
    monkeypatch.setattr(installer_module.sys, "platform", "linux")
    monkeypatch.setattr(installer_module.platform, "linux_package_manager", lambda: "apt")
    assert installer_module.host_package_manager() == "apt"


def test_installer_fails_gracefully_without_docker(tmp_path: Path) -> None:
    """Roadmap 3.3: no daemon + provisioning not implemented → a clear, catchable error."""
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    calls: list[dict[str, object]] = []
    from yulon.platform import ProvisionReport

    installer = Installer(
        entry,
        repo_root=tmp_path,
        docker_check=lambda: False,
        ensure_docker=lambda **_: ProvisionReport(
            "linux", manual_steps=("Install Docker Engine by hand: https://docs.docker.com/",)
        ),
        interact=_fake_interact(calls),  # type: ignore[arg-type]
        package_manager=lambda: None,  # the file below is the default script, not a variant
        bash_check=lambda: True,  # this box's bash is irrelevant to what is asserted
    )
    with pytest.raises(DockerUnavailableError, match="could not be set up automatically"):
        list(installer.run())
    assert calls == []  # the script never started

    rebooter = Installer(
        entry,
        repo_root=tmp_path,
        docker_check=lambda: False,
        ensure_docker=lambda **_: ProvisionReport(
            "windows", done=("wsl --install",), reboot_required=True, manual_steps=("Reboot.",)
        ),
        package_manager=lambda: None,  # default script, not a distro variant
        bash_check=lambda: True,  # this box's bash is irrelevant to what is asserted
    )
    with pytest.raises(DockerUnavailableError, match="reboot is needed"):
        rebooter.preflight(InstallOptions())


def test_installer_requires_the_client_dir_when_the_script_asks_for_it(tmp_path: Path) -> None:
    """README §3a: the app never fetches a client; games that need one refuse to start without."""
    entry = load_catalog().get("wow-tbc")
    assert entry.install.requires_client_dir is True
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    installer = Installer(
        entry, repo_root=tmp_path, docker_check=lambda: True, bash_check=lambda: True
    )
    with pytest.raises(InstallerError, match="never downloads game clients"):
        list(installer.run())
    with pytest.raises(InstallerError, match="does not exist"):
        list(installer.run(InstallOptions(client_dir=tmp_path / "nope")))


def test_installer_reports_a_missing_script(tmp_path: Path) -> None:
    entry = load_catalog().get("wow-wotlk")
    with pytest.raises(InstallerError, match="not found"):
        Installer(entry, repo_root=tmp_path, docker_check=lambda: True).preflight(InstallOptions())


def test_installer_wraps_script_failure(tmp_path: Path) -> None:
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")

    def failing(command: list[str], **kwargs: object) -> Iterator[str]:
        yield "step 1"
        raise subprocess.CalledProcessError(7, command)

    installer = Installer(
        entry,
        repo_root=tmp_path,
        docker_check=lambda: True,
        interact=failing,  # type: ignore[arg-type]
        package_manager=lambda: None,  # the file below is the default script, not a variant
        bash_check=lambda: True,  # this box's bash is irrelevant to what is asserted
    )
    got: list[str] = []
    with pytest.raises(InstallerError, match="status 7"):
        for line in installer.run():
            got.append(line)
    assert got == ["step 1"]


def test_preflight_refuses_when_bash_cannot_run(tmp_path: Path) -> None:
    """A `bash` that is only the WSL alias must produce advice, not an execvpe error."""
    entry = load_catalog().get("wow-wotlk")
    script = tmp_path / entry.install.script
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    installer = Installer(
        entry,
        repo_root=tmp_path,
        docker_check=lambda: True,
        package_manager=lambda: None,
        bash_check=lambda: False,
    )
    with pytest.raises(InstallerError, match="no working `bash`"):
        list(installer.run())


def test_bash_available_probes_that_bash_actually_runs() -> None:
    """`which bash` is not enough — the probe must execute something."""
    import subprocess as sp

    calls: list[list[str]] = []

    def ok(argv: list[str], *a: object, **k: object) -> sp.CompletedProcess[str]:
        calls.append(argv)
        return sp.CompletedProcess(argv, 0, "", "")

    def broken(argv: list[str], *a: object, **k: object) -> sp.CompletedProcess[str]:
        calls.append(argv)
        return sp.CompletedProcess(argv, 1, "", "execvpe(/bin/bash) failed")

    if shutil.which("bash") is None:
        assert bash_available(ok) is False  # nothing to probe
        return
    assert bash_available(ok) is True
    assert calls[-1] == ["bash", "-c", "exit 0"]
    assert bash_available(broken) is False
