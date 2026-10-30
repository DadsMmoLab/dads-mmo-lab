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
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.ui import controller_view as view_module
from yulon.ui.controller_view import ControllerServices

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


def test_a_wsl_servers_module_rows_show_their_version(box: _Box) -> None:
    """T774: the per-module version seam is host git, which Git for Windows refuses over
    `\\\\wsl.localhost`, so every row's version is blank. Both tab factories must read
    through `apply.module_git(server_dir)` -- the distro's git inside WSL, host git
    elsewhere -- so a refused host git still leaves a version on the row.

    Mutation: wire either site back to `RunnerGit().head_version` and the seam
    answers None where the fixture's host git refuses.
    """
    catalog = load_catalog()
    for game in ("wow-wotlk", "wow-tortoise"):
        entry = catalog.get(game)
        services = ControllerServices.for_entry(entry, Path(SERVER))
        assert services.module_version is not None
        clone = Path(SERVER) / "modules" / "mod-arac"
        assert services.module_version(clone) is not None, "the distro's git answered nothing"
        assert git.RunnerGit().head_version(clone) is None, "host git stopped refusing"


def test_a_wsl_version_read_runs_off_the_gui_thread(qapp: object, box: _Box) -> None:
    """T774 trap: `_fill_next_version` reads versions on the GUI thread, and a
    `ContainerGit` call is `wsl.exe ... docker run alpine/git` per module -- seconds per
    row, freezing the window. A WSL server's reads must go through `self._run()` (one at
    a time, same `_filling_versions` gate, same `_closed` check in the done slot) while
    host-git servers keep the inline walk. The seam records `threading.current_thread()`.

    Mutation: run the distro fill inline and the seam answers from the GUI thread.
    """
    from tests.conftest import pump_until

    server_dir = Path(SERVER)
    assert platform.wsl_location(server_dir) is not None
    entry = load_catalog().get("wow-wotlk")
    services = ControllerServices.for_entry(entry, server_dir)
    assert services.module_version is not None

    real_version = services.module_version
    ran_on: list[threading.Thread] = []

    def recording(path: Path) -> str | None:
        ran_on.append(threading.current_thread())
        return real_version(path)

    object.__setattr__(services, "installed_modules", lambda: {"module": frozenset({"mod-arac"})})
    object.__setattr__(services, "module_version", recording)
    here = threading.current_thread()
    view = view_module.ControllerView(entry, services, status_poll_ms=0)
    pump_until(lambda: not view._filling_versions, "the version fill never finished")

    assert ran_on, "the walk never ran its read"
    assert all(thread is not here for thread in ran_on), "a WSL version read ran on the GUI thread"


def test_a_wsl_version_answer_after_close_touches_nothing(qapp: object, box: _Box) -> None:
    """T213 for the T774 job: the WSL done slot checks `_closed` like the tick does.

    A read that was already out when `shutdown()` ran answers to a tab that is
    going away; without the check it draws on deleted widgets (`set_version` on
    a deleted label) exactly the way the old late tick did.

    Mutation: drop the `_closed` check from `_version_read` and the fill below
    stays up while the label is drawn on a closed tab.
    """
    from tests.conftest import process_events, pump_until

    server_dir = Path(SERVER)
    entry = load_catalog().get("wow-wotlk")
    services = ControllerServices.for_entry(entry, server_dir)
    assert services.module_version is not None
    object.__setattr__(services, "installed_modules", lambda: {"module": frozenset({"mod-arac"})})
    view = view_module.ControllerView(entry, services, status_poll_ms=0)
    pump_until(lambda: not view._filling_versions, "the version fill never finished")
    before = view.modules_panel.row("mod-arac").version_label.text()
    assert before != ""

    view.shutdown()
    view._version_read(("module", "mod-arac", "MUTATED · 2026-10-12"))
    process_events(100)

    assert not view._filling_versions, "a stopped walk still claims to be running"
    assert (
        view.modules_panel.row("mod-arac").version_label.text() == before
    ), "a WSL answer after close drew on a closed tab"
