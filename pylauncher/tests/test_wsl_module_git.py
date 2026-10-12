"""T772: the Modules tab's git for a server inside a WSL distro runs in that distro.

Measured on yulon-win11, 2026-10-12 (gate live-t744-t756-yulon-win11-2026-10-12,
c-hostgit-unc.txt): the second Install of mod-arac on the adopted WSL Unbound server
refused with "modules\\mod-arac is a git checkout, but git would not say what it is a
checkout of". The applier had picked host git (Git for Windows 2.55 is on PATH), and
Git for Windows refuses a checkout reached over `\\\\wsl.localhost`:

    remote get-url rc=128 :: fatal: detected dubious ownership in repository at
    '//wsl.localhost/Ubuntu/home/pk/wow-unbound/modules/mod-arac' ...

The same checkout answered through the distro's docker (`wsl -d Ubuntu --exec docker
run ... alpine/git ... remote get-url origin` -> the mod-arac URL, rc 0), which is the
route the server's own git already takes (`git.ContainerGit(wsl_distro=)`).

The fakes below answer like those two: host git refuses with the measured text, and the
distro's docker answers. Each test goes through the factory the Modules tab calls, with
no `git=` handed in, which is how the tab builds it.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from yulon import apply, git, platform, runner
from yulon.controller_wow_wotlk import modules as wotlk_modules

DISTRO = "Ubuntu"
SERVER = "\\\\wsl.localhost\\Ubuntu\\home\\pk\\wow-unbound"
URL = "https://github.com/heyitsbench/mod-arac.git"
SHA = "c" * 40

# Git for Windows' answer from the desktop session, as captured (c-hostgit-unc.txt),
# with the PowerShell wrapping taken back out.
DUBIOUS = (
    "fatal: detected dubious ownership in repository at "
    "'//wsl.localhost/Ubuntu/home/pk/wow-unbound/modules/mod-arac'\n"
    "'//wsl.localhost/Ubuntu/home/pk/wow-unbound/modules/mod-arac' may refer to a "
    "non-local directory\n"
    "To add an exception for this directory, call:\n\n"
    "\tgit config --global --add safe.directory "
    "'%(prefix)///wsl.localhost/Ubuntu/home/pk/wow-unbound/modules/mod-arac'\n"
)


class _Box:
    """Host git that refuses a `\\\\wsl.localhost` checkout, and a distro that answers."""

    def __init__(self) -> None:
        self.host: list[list[str]] = []
        self.distro: list[list[str]] = []

    def run(self, cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        argv = [str(part) for part in cmd]
        if argv[:2] == ["git", "--version"]:
            return subprocess.CompletedProcess(argv, 0, "git version 2.55.0.windows.1\n", "")
        if argv[0] == "git":
            self.host.append(argv)
            return subprocess.CompletedProcess(argv, 128, "", DUBIOUS)
        if argv[:4] == ["wsl", "-d", DISTRO, "--exec"]:
            self.distro.append(argv)
            if argv[4:6] == ["stat", "-c"]:
                return subprocess.CompletedProcess(argv, 0, "1000:1000\n", "")
            if argv[-3:] == ["remote", "get-url", "origin"]:
                return subprocess.CompletedProcess(argv, 0, URL + "\n", "")
            if "status" in argv:
                return subprocess.CompletedProcess(argv, 0, "", "")
            if "log" in argv:
                return subprocess.CompletedProcess(argv, 0, f"{SHA} 2026-10-12\n", "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        raise AssertionError(f"unexpected command {argv}")


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _Box:
    """A Windows box with Git for Windows on PATH and the server's checkout in a distro.

    The UNC folder is created relative to a scratch working directory: on the Linux test
    host `Path(SERVER)` is one relative component, so `is_dir()` reads the folder made here.
    """
    monkeypatch.chdir(tmp_path)
    (Path(SERVER) / "modules" / "mod-arac" / ".git").mkdir(parents=True)
    monkeypatch.setattr(
        platform, "_which", lambda name, path=None: "wsl" if name == "wsl" else None
    )
    monkeypatch.setattr(git.shutil, "which", lambda name, *a, **k: "C:\\Git\\cmd\\git.exe")
    made = _Box()
    monkeypatch.setattr(runner, "run", made.run)
    return made


def _applier() -> apply.Applier:
    return wotlk_modules.applier(Path(SERVER), world_running=lambda: False, sql=None)


def test_the_measured_refusal_is_what_host_git_says_here(box: _Box) -> None:
    """The fixture's host git is the one the gate measured: it refuses this checkout."""
    clone = Path(SERVER) / "modules" / "mod-arac"
    assert git.RunnerGit().remote_url(clone) is None
    assert box.host and "dubious ownership" in DUBIOUS


def test_a_wsl_servers_module_checkout_is_read_through_the_distro(box: _Box) -> None:
    """The three questions an Install over a checkout asks, from the tab's own applier."""
    applier = _applier()
    clone = Path(SERVER) / "modules" / "mod-arac"

    assert applier.remote_url(clone) == URL
    assert applier.unmodified(clone, "conf/arac.conf.dist") is True
    assert box.host == [], box.host
    reads = [argv for argv in box.distro if argv[4] == "docker"]
    assert reads, box.distro
    for argv in reads:
        assert argv[argv.index("-v") + 1].startswith("/home/pk/wow-unbound/modules/mod-arac:")


def test_the_applier_clones_and_updates_through_the_distros_git(box: _Box) -> None:
    """Install, Update and Remove all go through `Applier.git`: the distro's, with host git
    on PATH."""
    seam = _applier().git
    assert isinstance(seam, git.ContainerGit)
    assert seam.wsl_distro == DISTRO


def test_a_wsl_server_without_host_git_gets_the_distro_route_too(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before T772 this was a refusal ("this machine has none it can run"): the distro's
    docker was not a git route yet when T58 wrote it. It is the route now."""
    monkeypatch.setattr(git.shutil, "which", lambda name, *a, **k: None)
    seam = apply._default_git(Path(SERVER))
    assert isinstance(seam, git.ContainerGit) and seam.wsl_distro == DISTRO


def test_a_server_outside_wsl_keeps_host_git(box: _Box, tmp_path: Path) -> None:
    assert isinstance(apply._default_git(tmp_path), git.RunnerGit)


def test_check_for_updates_counts_a_wsl_servers_modules_through_the_distro(box: _Box) -> None:
    rows = wotlk_modules.module_updates(Path(SERVER))
    assert box.host == [], box.host
    assert any(argv[-3:-1] == ["fetch", "origin"] for argv in box.distro), box.distro
    assert [row.key for row in rows] == ["mod-arac"]


def test_the_background_refresh_does_not_ask_host_git_about_a_wsl_server(box: _Box) -> None:
    """The refresh is host git only and never starts a `docker run` unasked (T621); for a
    server in a distro host git is refused, so it counts nothing and the press counts."""
    assert wotlk_modules.refresh_module_updates(Path(SERVER), threading.Event()) == ()
    assert box.host == [] and box.distro == []


def test_a_failed_distro_update_is_not_handed_to_host_git(
    box: _Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ContainerGit` falls back to host git when its container fails; in a distro host git
    can only be refused, so the player would read Git for Windows' refusal instead of the
    failure that happened."""
    clone = Path(SERVER) / "modules" / "mod-arac"
    answer = box.run

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if cmd[0] != "git" and "fetch" in cmd:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal: unable to access")
        return answer(cmd, **kw)

    monkeypatch.setattr(runner, "run", run)
    seam = git.ContainerGit(wsl_distro=DISTRO)
    spec = git.CloneSpec(url=URL, dest=clone)
    with pytest.raises(git.GitError, match="unable to access"):
        seam.clone(spec)
    with pytest.raises(git.GitError, match="unable to access"):
        list(seam.clone_lines(spec))
    assert box.host == [], box.host
