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
it running" (`wsl.distro_state()`) is the real one.
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

from tests.conftest import process_events, pump_until
from yulon import channel_setup, docker, platform, runner, wsl
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller import Controller
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import (
    DISTRO_STOPPED,
    DISTRO_UNKNOWN,
    ControllerServices,
    ControllerView,
)
from yulon.ui.widgets.job import run_inline

DISTRO = "Ubuntu-yulon"
INSIDE = "/home/pk/wow"
NO_WSL_EXE = "/nonexistent/yulon-test/wsl"
"""What `wsl` resolves to here: a name that is SEEN when the app starts it, and runs nothing."""

GAMES = ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise")

_THREADED = controller_view_module.threaded_job_runner
"""The view's real job runner, taken before `_inline_jobs` replaces it for each test."""


class _DistroDisk:
    """The distro's folder, and every touch of it while WSL does not say it runs.

    `wsl` is the one switch -- `running`, `stopped`, or `unknown` for a
    `--running` listing that does not answer: WSL's listing answers from it,
    and a touch is recorded whenever it is not `running`. `stopped` is the
    two-way spelling of it. Each touch carries the app frames that made it, so
    a failure names the site rather than just the file.
    """

    def __init__(self, root: Path) -> None:
        self.root = os.fspath(root)
        self.wsl = "stopped"
        self.touched: list[str] = []

    @property
    def stopped(self) -> bool:
        return self.wsl != "running"

    @stopped.setter
    def stopped(self, value: bool) -> None:
        self.wsl = "stopped" if value else "running"

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

    def listing(*args: str) -> tuple[str, ...] | None:
        # WSL's own listing, which starts nothing: the one place the app may ask.
        if "--running" not in args:
            return (DISTRO,)
        return {"running": (DISTRO,), "stopped": (), "unknown": None}[recorder.wsl]

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


def test_a_listing_that_does_not_answer_reads_nothing_and_says_so(
    qapp: object, disk: _DistroDisk
) -> None:
    """T133 review: `unknown` is no permission. Only a listing that SAYS running drains."""
    disk.wsl = "unknown"
    view = _open_the_tab(_entry("wow-wotlk"), Path(disk.root))
    try:
        _poll(view)
        assert disk.touched == [], "\n".join(disk.touched)
        assert not view.distro_label.isHidden()
        assert view.distro_label.text() == DISTRO_UNKNOWN.format(distro=DISTRO)
        disk.wsl = "stopped"
        _poll(view)
        assert view.distro_label.text() == DISTRO_STOPPED.format(distro=DISTRO)
        assert view.backup_list.count() == 0 and disk.touched == []
        disk.wsl = "running"
        _poll(view)
        assert view.distro_label.isHidden() and view.backup_list.count() == 1
    finally:
        disk.stopped = False
        view.shutdown()


def test_the_verdict_on_its_worker_does_not_start_a_distro_stopped_since_the_last_poll(
    qapp: object, disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T133 review: the verdict runs every five seconds on the timer, beside the poll, off
    the tab's last answer -- up to one poll old. A distro stopped from outside since then
    (`wsl -t`, `--shutdown`) must not be started again by its `docker inspect`. With the
    view's REAL worker thread, so the order is the app's."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", _THREADED)
    disk.stopped = False
    view = _open_the_tab(_entry("wow-wotlk"), Path(disk.root))
    try:
        pump_until(lambda: view._distro == "running", "the first answer said running")
        pump_until(
            lambda: not view._verdict_pending and not view._status_pending,
            "the opening poll and verdict finished",
        )
        disk.stopped = True  # `wsl -t` from outside; the tab has not polled since
        assert view._distro == "running"
        view.refresh_verdict()
        pump_until(lambda: not view._verdict_pending, "the verdict came back")
        assert disk.touched == [], "\n".join(disk.touched)
    finally:
        disk.stopped = False
        view.shutdown()


def test_a_tab_that_does_not_poll_still_asks_and_fills_in_on_a_running_distro(
    qapp: object, disk: _DistroDisk
) -> None:
    """The tab is built not asked; its own ask, off the GUI thread, answers it without a poll."""
    disk.stopped = False
    entry = _entry("wow-wotlk")
    services = ControllerServices.for_entry(entry, Path(disk.root), None, DISTRO)
    view = ControllerView(entry, services, status_poll_ms=0)
    try:
        assert view._distro == "running"
        assert view.backup_list.count() == 1, "a running distro's tab never filled in"
    finally:
        view.shutdown()


def test_the_update_route_reads_nothing_while_wsl_does_not_answer(
    disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T125's two readings take the same rule: `unknown` is no permission (T133 review)."""
    from yulon import install_wiring

    disk.wsl = "unknown"
    route = install_wiring.update_to_latest_for_app(
        _entry("wow-wotlk"), Path(disk.root), wsl_distro=DISTRO
    )
    assert route is not None and route.upstream_news is not None
    said = route.source_version()
    news = route.upstream_news()
    assert said.line == "" and news.sources == ()
    assert disk.touched == [], "\n".join(disk.touched)


def test_the_later_settle_on_its_worker_does_not_start_a_distro_stopped_since_the_last_poll(
    qapp: object, disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T138's settle a minute after opening is a timer too: the same worker-side ask."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", _THREADED)
    entry = _entry("wow-wotlk")
    server_dir = Path(disk.root)
    channel_setup.save_pending(
        channel_setup.Pending(account="YULON-T133", password="not-a-real-one"),
        game=entry.id,
        install_id=composegen.install_id(server_dir),
    )
    disk.stopped = False
    services = ControllerServices.for_entry(entry, server_dir, None, DISTRO)
    view = ControllerView(entry, services, status_poll_ms=0)
    try:
        pump_until(lambda: view._distro == "running", "the first answer said running")
        # The version walk the opening reload starts is one event-loop turn per
        # clone; it is over before a minute is.
        pump_until(lambda: not view._filling_versions, "the module version walk finished")
        came_back: list[object] = []
        monkeypatch.setattr(view, "_channel_resettled", came_back.append)
        monkeypatch.setattr(view, "_channel_settle_failed", came_back.append)
        disk.stopped = True  # `wsl --shutdown` from outside, before the minute is up
        view._resettle_if_pending()
        pump_until(lambda: bool(came_back), "the settle came back")
        assert disk.touched == [], "\n".join(disk.touched)
    finally:
        disk.stopped = False
        view.shutdown()


def test_a_distro_stopped_from_outside_takes_the_last_verdict_off_the_server_tab(
    qapp: object, disk: _DistroDisk
) -> None:
    """T133 live gate: after `wsl -t`, "up — 0 players, 0 bots" stayed above "world down"
    and the stopped line. The poll that says stopped clears it, and so does a verdict
    the worker skipped, or one that lands after that poll."""
    from yulon import dashboard

    entry = _entry("wow-wotlk")
    disk.stopped = False
    services = ControllerServices.for_entry(entry, Path(disk.root), None, DISTRO)
    services.dashboard = lambda: dashboard.Verdict("up", players=0, bots=0)
    view = ControllerView(entry, services)
    try:
        _poll(view)
        assert not view.verdict_label.isHidden() and view.verdict_label.text().startswith("up")

        disk.stopped = True  # `wsl -t` from outside
        _poll(view)
        assert view.verdict_label.isHidden() and view.verdict_label.text() == ""
        assert not view.distro_label.isHidden()

        view._verdict_ready(dashboard.Verdict("up", players=0, bots=0))
        assert view.verdict_label.isHidden(), "a verdict landing after the stop was drawn"
        disk.stopped = False
        _poll(view)
        assert view.verdict_label.text().startswith("up"), "the verdict did not come back"
        view._verdict_ready(None)
        assert view.verdict_label.isHidden(), "a verdict the worker skipped left the old one"
    finally:
        disk.stopped = False
        view.shutdown()


# -- the pieces the gate is made of --------------------------------------------------------


def test_only_a_listing_that_says_running_lets_a_reading_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wsl.distro_state()` and `wsl.may_read()`: an unanswered listing is no permission."""
    answers: dict[tuple[str, ...], tuple[str, ...] | None] = {
        ("--running",): (),
        (): (DISTRO,),
    }
    asked: list[tuple[str, ...]] = []

    def listing(*args: str) -> tuple[str, ...] | None:
        asked.append(args)
        return answers[args]

    monkeypatch.setattr(wsl, "_wsl_listing", listing)
    assert wsl.may_read(None) is True
    assert asked == [], "a server on this host asked WSL about a distro"
    assert wsl.distro_state(DISTRO) == "stopped" and wsl.may_read(DISTRO) is False
    answers[("--running",)] = (DISTRO,)
    asked.clear()
    assert wsl.distro_state(DISTRO) == "running"
    assert asked == [("--running",)], "a running distro cost more than the one listing"
    assert wsl.may_read(DISTRO) is True
    answers[("--running",)] = None
    assert wsl.distro_state(DISTRO) == "unknown"
    assert wsl.may_read(DISTRO) is False, "a --running listing that did not answer let it read"
    answers[("--running",)] = ()
    answers[()] = None
    assert wsl.distro_state(DISTRO) == "unknown", "an unanswered full listing read as stopped"
    answers[()] = ("Other",)
    assert wsl.distro_state(DISTRO) == "unknown", "a distro WSL does not list read as stopped"
    assert wsl.known_stopped(DISTRO) is False and wsl.is_running(DISTRO) is False


class _Listed:
    """`subprocess.run` for one `wsl -l -q` call: an exit code and what it printed."""

    def __init__(self, code: int, stdout: str = "", stderr: bytes = b"") -> None:
        self.code, self.stdout, self.stderr = code, stdout.encode("utf-16le"), stderr

    def __call__(self, argv: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, self.code, self.stdout, self.stderr)


@pytest.mark.parametrize(
    ("listed", "means"),
    [
        (_Listed(0, "Ubuntu-yulon\r\ndocker-desktop\r\n"), (DISTRO, "docker-desktop")),
        (_Listed(0, ""), ()),
        (_Listed(0, "There are no running distributions.\r\n"), ()),
        (_Listed(1, ""), None),
        (_Listed(1, "There are no running distributions.\r\n"), ()),
        (_Listed(0xFFFFFFFF, "Es werden keine Verteilungen ausgef\u00fchrt.\r\n"), None),
        (_Listed(1, "", b"There are no running distributions.\n"), ()),
        (_Listed(1, "Catastrophic failure\r\nError code: Wsl/Service/E_UNEXPECTED\r\n"), None),
        (_Listed(1, "", b"Error code: Wsl/Service/E_UNEXPECTED\n"), None),
    ],
    ids=[
        "names",
        "nothing, exit 0",
        "the sentence, exit 0",
        "nothing, exit 1: no answer (T95)",
        "the sentence, exit 1",
        "translated, exit -1: no answer",
        "the sentence on stderr in UTF-8",
        "a failure with its code",
        "a failure's code on stderr in UTF-8",
    ],
)
def test_a_running_listing_that_says_none_runs_is_empty_whatever_its_exit_code(
    monkeypatch: pytest.MonkeyPatch, listed: _Listed, means: tuple[str, ...] | None
) -> None:
    """Measured with `-q` (T133 live gate): nothing running is exit 0 and no output. The
    sentence -- printed only without `-q` -- is still read as "none", in either stream and
    either encoding. A non-zero exit without it stays no answer: T95's stop must not skip."""
    monkeypatch.setattr(platform, "_which", lambda name, path=None: NO_WSL_EXE)
    monkeypatch.setattr(wsl.subprocess, "run", listed)
    assert wsl._wsl_listing("--running") == means


def test_a_full_listing_that_fails_is_no_answer_even_with_the_sentence_in_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The none-running reading is `--running`'s alone: the full listing names distros."""
    monkeypatch.setattr(platform, "_which", lambda name, path=None: NO_WSL_EXE)
    monkeypatch.setattr(
        wsl.subprocess, "run", _Listed(1, "There are no running distributions.\r\n")
    )
    assert wsl._wsl_listing() is None


def test_the_status_poll_says_what_wsl_said_about_the_distro(
    disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`InstallStatus.distro` is what tells the tab when its readings may go."""
    spec = _entry("wow-wotlk").container_spec()
    controller = Controller(spec, Path(disk.root), wsl_distro=DISTRO)
    assert controller.status().distro == "stopped"
    disk.wsl = "unknown"
    assert controller.status().distro == "unknown"
    assert disk.touched == [], "the poll of a distro not known to run asked inside it"
    disk.stopped = False
    status = controller.status()
    assert status.distro == "running" and not status.any_running
    disk.stopped = True
    monkeypatch.setattr(docker, "status", lambda wsl_distro=None: [])
    assert Controller(spec, Path(disk.root)).status().distro is None


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


def test_a_password_that_could_not_be_read_is_asked_again_rather_than_kept_as_the_default(
    disk: _DistroDisk, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T133 review: only a read that FOUND the password is kept."""
    services = ControllerServices.for_entry(_entry("wow-tbc"), Path(disk.root), None, DISTRO)
    disk.stopped = False
    password_file = Path(disk.root) / ".db_password"
    password_file.unlink()
    seen = _mysql_passwords(monkeypatch)
    with contextlib.suppress(Exception):
        services.create_account("bob", "pw", 0)
    first = set(seen)
    assert first and "generated-0123456789" not in first, "the missing file was read somehow"
    password_file.write_text("generated-0123456789\n", encoding="utf-8")
    seen.clear()
    with contextlib.suppress(Exception):
        services.create_account("bob", "pw", 0)
    assert seen and set(seen) == {"generated-0123456789"}, "the default was kept"


def test_a_local_install_still_reads_its_generated_password_when_the_tab_is_built(
    tmp_path: Path,
) -> None:
    """Only a WSL install waits: on this host `_db_password()` is the file's text, read now."""
    (tmp_path / ".db_password").write_text("local-0123456789\n", encoding="utf-8")
    read = controller_view_module._db_password(_entry("wow-tbc"), tmp_path)
    (tmp_path / ".db_password").unlink()
    assert read == "local-0123456789"
