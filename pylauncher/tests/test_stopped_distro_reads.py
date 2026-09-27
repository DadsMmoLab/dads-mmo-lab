"""Opening Yu'lon never boots a stopped WSL distro (T133, wsl-resident-servers §2).

Measured on a Windows 11 box (T132 M7): from the user's desktop session ANY read
under `\\\\wsl.localhost\\<distro>\\...` starts a stopped distro (1.35 s), so a
Server tab that read its install's files when it opened booted the distro, and a
server killed earlier came back by itself through `restart: unless-stopped`.

The disk double below records every touch of the distro's folder while WSL says
the distro is stopped -- an open, a listing, a stat, or a child process run in
it or naming it (`wsl -d <distro>`, git with its cwd there) -- and the tests
open the real tab, built through the real `ControllerServices.for_entry()`, and
run its polls. WSL is faked at its listing only, so the app's own answer to "is
it stopped" (`wsl.known_stopped()`) is the real one.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import traceback
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import process_events
from yulon import channel_setup, docker, platform, runner, wsl
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller import Controller
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import DISTRO_STOPPED, ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

DISTRO = "Ubuntu-yulon"
INSIDE = "/home/pk/wow"
NO_WSL_EXE = "/nonexistent/yulon-test/wsl"
"""What `wsl` resolves to here: a name that is SEEN when the app starts it, and runs nothing."""

GAMES = ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise")


class _DistroDisk:
    """The distro's folder, and every touch of it while the distro is stopped.

    `stopped` is the one switch: WSL's listing answers from it, and a touch is
    recorded only while it is set. Each touch carries the app frames that made
    it, so a failure names the site rather than just the file.
    """

    def __init__(self, root: Path) -> None:
        self.root = os.fspath(root)
        self.stopped = True
        self.touched: list[str] = []

    def under(self, path: object) -> bool:
        if isinstance(path, int):
            return False
        try:
            text = os.fsdecode(os.fspath(path))  # type: ignore[arg-type]
        except TypeError:
            return False
        return text == self.root or text.startswith(self.root + os.sep)

    def _note(self, what: str) -> None:
        frames = [
            f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
            for frame in traceback.extract_stack()
            if f"{os.sep}yulon{os.sep}" in frame.filename
        ]
        self.touched.append(f"{what.replace(self.root, '<distro>')}  <- {' > '.join(frames[-5:])}")

    def saw(self, what: str, path: object) -> None:
        if self.stopped and self.under(path):
            self._note(f"{what} {os.fsdecode(os.fspath(path))}")  # type: ignore[arg-type]

    def saw_child(self, argv: object, cwd: object) -> None:
        if not self.stopped:
            return
        words = [os.fsdecode(a) for a in argv] if isinstance(argv, (list, tuple)) else [str(argv)]
        in_distro = any(
            words[i] in ("-d", "--distribution") and words[i + 1] == DISTRO
            for i in range(len(words) - 1)
        )
        there = cwd is not None and self.under(cwd)
        if in_distro or there or any(self.under(word) for word in words):
            self._note(f"child {words!r} cwd={cwd!r}")


_ARMED: list[_DistroDisk] = []
"""The disk the audit hook reports to. A hook cannot be removed, so one serves every test."""


def _audit(event: str, args: tuple[Any, ...]) -> None:
    if not _ARMED:
        return
    disk = _ARMED[-1]
    if event == "open":
        disk.saw("open", args[0])
    elif event in ("os.listdir", "os.scandir", "os.chdir"):
        disk.saw(event, args[0])
    elif event == "subprocess.Popen":
        disk.saw_child(args[1], args[2])


sys.addaudithook(_audit)


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view's background jobs, run inline: a read on a worker thread is still a read."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _lay_an_install(root: Path) -> None:
    """The files a real install has, so a read that stops at a missing file goes on past it."""
    for rel in (
        "docker-compose.yml",
        "docker-compose.override.yml",
        ".env",
        ".yulon-install.json",
        ".yulon-upstream.json",
        ".yulon-module-answers.json",
        ".yulon-module-updates.json",
        "env/dist/etc/worldserver.conf",
        "env/dist/etc/authserver.conf",
        "env/dist/etc/modules/playerbots.conf",
        "etc/mangosd.conf",
        "etc/realmd.conf",
        "etc/aiplayerbot.conf",
        "sql_scripts/backups/2026-09-01.sql",
        "sql_scripts/clones/tortoise-bots-manager/.yulon-clone.json",
        "modules/mod-playerbots/.yulon-clone.json",
        "ale_scripts/one/.yulon-clone.json",
    ):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n" if rel.endswith(".json") else "x = 1\n", encoding="utf-8")
    (root / ".db_password").write_text("generated-0123456789\n", encoding="utf-8")
    # A real clone, so the version walk has a `git` to run in it.
    subprocess.run(["git", "init", "-q", str(root / "modules" / "mod-playerbots")], check=True)


@pytest.fixture
def disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[_DistroDisk]:
    """An adopted server's folder inside `DISTRO`, which WSL says is stopped."""
    root = tmp_path / "wsl.localhost" / DISTRO / "home" / "pk" / "wow"
    root.mkdir(parents=True)
    _lay_an_install(root)
    recorder = _DistroDisk(root)

    def listing(*args: str) -> tuple[str, ...]:
        # WSL's own listing, which starts nothing: the one place the app may ask.
        running = () if recorder.stopped else (DISTRO,)
        return running if "--running" in args else (DISTRO,)

    monkeypatch.setattr(wsl, "_wsl_listing", listing)

    # `root` is the folder as Windows names it (`\\wsl.localhost\<distro>\...`).
    real_location = platform.wsl_location

    def location(path: Path) -> tuple[str, str] | None:
        if recorder.under(path):
            return DISTRO, INSIDE + os.fspath(path)[len(recorder.root) :].replace(os.sep, "/")
        return real_location(path)

    monkeypatch.setattr(platform, "wsl_location", location)
    monkeypatch.setattr(platform, "wsl_linux_path", lambda path: (location(path) or ("", None))[1])
    real_which = platform._which
    monkeypatch.setattr(
        platform,
        "_which",
        lambda name, path=None: (
            NO_WSL_EXE if name == platform.WSL_PROGRAM else real_which(name, path)
        ),
    )

    # `os.stat`/`os.lstat` raise no audit event, so they are wrapped instead.
    real_stat, real_lstat = os.stat, os.lstat

    def stat(path: Any, *args: Any, **kwargs: Any) -> Any:
        recorder.saw("stat", path)
        return real_stat(path, *args, **kwargs)

    def lstat(path: Any, *args: Any, **kwargs: Any) -> Any:
        recorder.saw("lstat", path)
        return real_lstat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", stat)
    monkeypatch.setattr(os, "lstat", lstat)

    # Every `wsl -d` the app runs through `runner` is answered here, whether or
    # not it is recorded: docker in the distro says it has nothing running.
    # Ahead of conftest's docker guard, which would refuse the argv outright
    # and so stop at the first site instead of listing them all.
    guarded_run, guarded_stream = runner.run, runner.stream

    def run(command: Any, *args: Any, **kwargs: Any) -> Any:
        recorder.saw_child(command, kwargs.get("cwd"))
        if command and command[0] == NO_WSL_EXE:
            return subprocess.CompletedProcess(command, 0, "", "")
        return guarded_run(command, *args, **kwargs)

    def stream(command: Any, *args: Any, **kwargs: Any) -> Any:
        recorder.saw_child(command, kwargs.get("cwd"))
        if command and command[0] == NO_WSL_EXE:
            return iter(())
        return guarded_stream(command, *args, **kwargs)

    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setattr(runner, "stream", stream)
    _ARMED.append(recorder)
    try:
        yield recorder
    finally:
        _ARMED.remove(recorder)


def _entry(game: str) -> CatalogEntry:
    return load_catalog().get(game)


def _open_the_tab(entry: CatalogEntry, server_dir: Path) -> ControllerView:
    """The tab `main.py` builds for a remembered WSL install, with its sub-tabs visited."""
    # A channel credential on file, so the tab's opening check has something to
    # ask about: an unanswered SOAP channel asks the world's container why.
    channel_setup.save_credential(
        channel_setup.Verified(account="YULON-T133", password="not-a-real-one"),
        game=entry.id,
        install_id=composegen.install_id(server_dir),
        host="127.0.0.1",
        port=1,
        namespace="urn:AC",
    )
    services = ControllerServices.for_entry(entry, server_dir, None, DISTRO)
    view = ControllerView(entry, services)
    for index in range(view._tabs.count()):
        view._tabs.setCurrentIndex(index)
        process_events(10)
    return view


def _poll(view: ControllerView) -> None:
    """One tick of the five-second timer: the status poll and the dashboard verdict."""
    view._tick()
    view.refresh_verdict()
    process_events(10)


@pytest.mark.parametrize("game", GAMES)
def test_opening_a_tab_on_a_stopped_distro_reads_nothing_in_it(
    qapp: object, disk: _DistroDisk, game: str
) -> None:
    """Build, visit every sub-tab, poll twice: not one touch of the distro's folder."""
    view = _open_the_tab(_entry(game), Path(disk.root))
    try:
        _poll(view)
        _poll(view)
        assert disk.touched == [], "\n".join(disk.touched)
    finally:
        disk.stopped = False
        view.shutdown()


@pytest.mark.parametrize("game", ("wow-wotlk", "wow-tortoise"))
def test_the_tab_says_the_distro_is_stopped_and_fills_in_once_it_is_up(
    qapp: object, disk: _DistroDisk, game: str
) -> None:
    """What waited runs when a poll finds the distro up -- started outside Yu'lon here."""
    view = _open_the_tab(_entry(game), Path(disk.root))
    try:
        assert not view.distro_label.isHidden()
        assert view.distro_label.text() == DISTRO_STOPPED.format(distro=DISTRO)
        assert view.backup_list.count() == 0, "the backups folder was listed while stopped"
        assert len(view.modules_panel.rows()) == 0, "module rows were drawn while stopped"

        disk.stopped = False
        _poll(view)

        assert view.distro_label.isHidden()
        assert view.backup_list.count() == 1, "the backups were not listed once it was up"
        assert len(view.modules_panel.rows()) > 0, "the Modules tab never filled in"
        assert view._waiting_on_distro == {}
    finally:
        disk.stopped = False
        view.shutdown()


def test_a_reading_asked_twice_while_stopped_runs_once_when_the_distro_is_up(
    qapp: object, disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kept by what it reads, so a second ask while stopped replaces the first."""
    view = _open_the_tab(_entry("wow-wotlk"), Path(disk.root))
    try:
        listed: list[str] = []
        real = view.refresh_backups

        def counting() -> None:
            listed.append("asked")
            real()

        monkeypatch.setattr(view, "refresh_backups", counting)
        view.refresh_backups()
        view.refresh_backups()
        assert disk.touched == [], "\n".join(disk.touched)
        count = view.backup_list.count

        disk.stopped = False
        _poll(view)
        # The two asks above, then ONE run of what waited -- the gate kept the
        # attribute the tab would call, which is `counting` here.
        assert listed == ["asked", "asked", "asked"] and count() == 1
        _poll(view)
        assert listed == ["asked", "asked", "asked"], "a second poll ran it again"
    finally:
        disk.stopped = False
        view.shutdown()


def test_start_still_reaches_a_stopped_distro_and_the_tab_then_fills_in(
    qapp: object, disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A press may start the distro -- the player asked for it -- and nothing waits after it."""
    view = _open_the_tab(_entry("wow-wotlk"), Path(disk.root))
    try:
        started: list[str | None] = []

        def start_staged(spec: object, server_dir: Path, *, wsl_distro: str | None) -> None:
            started.append(wsl_distro)
            disk.stopped = False  # the distro booted to run it

        monkeypatch.setattr(docker, "start_staged", start_staged)
        monkeypatch.setattr(wsl, "hold", lambda distro, key: wsl.Hold(held=True))
        view.start_server()
        process_events(10)

        assert started == [DISTRO]
        assert any(
            f"'-d', '{DISTRO}'" in touch for touch in disk.touched
        ), "Start did not ask the distro's docker about its ports first"
        assert view.distro_label.isHidden()
        assert view.backup_list.count() == 1, "the tab did not fill in after Start"
    finally:
        disk.stopped = False
        view.shutdown()


def test_a_pending_channel_is_not_resettled_inside_a_stopped_distro(
    qapp: object, disk: _DistroDisk
) -> None:
    """T138's later ask, scheduled by the tab opening, waits for the distro like the rest.

    A `Pending` channel found at open gets one settle a minute later, and a
    settle the world does not answer asks the world's container why -- through
    the distro's docker. Fired here by hand rather than waited for.
    """
    entry = _entry("wow-wotlk")
    server_dir = Path(disk.root)
    channel_setup.save_pending(
        channel_setup.Pending(account="YULON-T133", password="not-a-real-one"),
        game=entry.id,
        install_id=composegen.install_id(server_dir),
    )
    view = ControllerView(entry, ControllerServices.for_entry(entry, server_dir, None, DISTRO))
    try:
        assert isinstance(view.services.channel_setup.setup_state(), channel_setup.Pending)
        view._resettle_if_pending()
        process_events(10)
        assert disk.touched == [], "\n".join(disk.touched)
        assert "channel resettle" in view._waiting_on_distro
    finally:
        disk.stopped = False
        view.shutdown()


# -- the pieces the gate is made of --------------------------------------------------------


def test_only_a_distro_wsl_said_is_stopped_makes_a_reading_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wsl.reading_would_start()`: fail-closed, and never a question for a local server."""
    answers: dict[tuple[str, ...], tuple[str, ...] | None] = {
        ("--running",): (),
        (): (DISTRO,),
    }
    asked: list[tuple[str, ...]] = []

    def listing(*args: str) -> tuple[str, ...] | None:
        asked.append(args)
        return answers[args]

    monkeypatch.setattr(wsl, "_wsl_listing", listing)
    assert wsl.reading_would_start(None) is False
    assert asked == [], "a server on this host asked WSL about a distro"
    assert wsl.reading_would_start(DISTRO) is True
    answers[("--running",)] = (DISTRO,)
    assert wsl.reading_would_start(DISTRO) is False, "a running distro was read as stopped"
    answers[("--running",)] = None
    assert wsl.reading_would_start(DISTRO) is False, "a listing that did not answer skipped"


def test_the_status_poll_says_whether_wsl_called_the_distro_stopped(
    disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`InstallStatus.distro_stopped` is what tells the tab when its readings may go."""
    spec = _entry("wow-wotlk").container_spec()
    controller = Controller(spec, Path(disk.root), wsl_distro=DISTRO)
    assert controller.status().distro_stopped is True
    assert disk.touched == [], "the poll of a stopped distro asked inside it"
    disk.stopped = False
    status = controller.status()
    assert status.distro_stopped is False and not status.any_running
    disk.stopped = True
    monkeypatch.setattr(docker, "status", lambda wsl_distro=None: [])
    assert Controller(spec, Path(disk.root)).status().distro_stopped is False


def _mysql_passwords(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every `MYSQL_PWD` a database call hands its child, answered with a refusal."""
    seen: list[str] = []

    def run(argv: Any, *args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.append(kwargs["env"].get("MYSQL_PWD", ""))
        return subprocess.CompletedProcess(argv, 1, "", "refused")

    monkeypatch.setattr(subprocess, "run", run)
    return seen


@pytest.mark.parametrize("game", ("wow-tbc", "wow-vanilla", "wow-tortoise"))
def test_a_generated_password_in_a_distro_is_read_at_its_first_use_and_only_then(
    disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch, game: str
) -> None:
    """Building the tab reads no `.db_password`; the first SQL call reads it, and once."""
    services = ControllerServices.for_entry(_entry(game), Path(disk.root), None, DISTRO)
    assert disk.touched == [], "\n".join(disk.touched)
    disk.stopped = False
    seen = _mysql_passwords(monkeypatch)
    with contextlib.suppress(Exception):
        services.create_account("bob", "pw", 0)
    assert seen and set(seen) == {"generated-0123456789"}
    (Path(disk.root) / ".db_password").write_text("changed-afterwards\n", encoding="utf-8")
    with contextlib.suppress(Exception):
        services.create_account("bob", "pw", 0)
    assert set(seen) == {"generated-0123456789"}, "the password file was read again"


def test_a_local_install_still_reads_its_generated_password_when_the_tab_is_built(
    tmp_path: Path,
) -> None:
    """Only a WSL install waits: on this host `_db_password()` is the file's text, read now."""
    (tmp_path / ".db_password").write_text("local-0123456789\n", encoding="utf-8")
    read = controller_view_module._db_password(_entry("wow-tbc"), tmp_path)
    (tmp_path / ".db_password").unlink()
    assert read == "local-0123456789"
