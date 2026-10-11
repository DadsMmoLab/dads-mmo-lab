"""A restore whose Yu'lon has quit may still be loading: the next start must know (T690).

"Quit anyway" ends Yu'lon with `os._exit`, and the restore load is a plain
`docker exec -i ... mysql` child that nothing kills: it carries on writing the
databases. A relaunch that reported an "interrupted restore" and offered the
safety copy would put a second writer on the same databases. So the load's pid goes
into the restore marker, and the next start says the restore is still running, and
offers neither the interrupted-restore action nor a second restore.

The child here is a real process (a stub standing in for the docker client), never a
fake pid.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Any

import pytest

from tests.test_maintenance import (
    AT,
    DB,
    FakeMysql,
    a_backup_of,
    plan_restore,
    restore,
    running,
)
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import (
    DockerMysql,
    MaintenanceError,
    forget_interrupted_restore,
    interrupted_restore,
)

# Stands in for the `docker` client: reads the dump from stdin, says who it is, and keeps
# "loading" until it is told to stop.
STUB = """
import os, stat, sys, time
sys.stdin.buffer.read()
here = os.path.dirname(os.path.abspath(__file__))
kinds = "".join("f" if stat.S_ISREG(os.fstat(fd).st_mode) else "p" for fd in (1, 2))
with open(os.path.join(here, "load.pid"), "w") as f:
    f.write(str(os.getpid()) + " " + kinds)
if os.path.exists(os.path.join(here, "noise")):
    # more than any pipe buffer holds, on both streams, with nobody reading yet
    sys.stdout.write("o" * 300000)
    sys.stderr.write("mysql said: " + "e" * 300000 + " THE END")
while not os.path.exists(os.path.join(here, "release")):
    time.sleep(0.05)
sys.exit(1 if os.path.exists(os.path.join(here, "fail")) else 0)
"""


@dataclass(frozen=True)
class _LoadingMysql(DockerMysql):
    """The real `load_from` (a child process), everything else answered from memory."""

    def databases(self) -> tuple[str, ...]:
        return ("acore_world",)

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        FakeMysql(("acore_world",)).dump_into(database, sink)


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "stub"
    folder.mkdir()
    script = folder / "docker_stub.py"
    script.write_text(STUB, encoding="utf-8")
    monkeypatch.setattr(
        maintenance.platform, "docker_prefix", lambda _distro: [sys.executable, str(script)]
    )
    monkeypatch.setattr(maintenance, "mysql_client", lambda *a, **k: "mysql")
    monkeypatch.setattr(maintenance, "mysql_env", lambda *a, **k: {})
    return folder


def _wait_for(path: Path, seconds: float = 20.0) -> None:
    end = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < end, f"{path.name} never appeared"
        time.sleep(0.05)


def test_the_running_load_is_named_in_the_marker_while_it_runs(tmp_path: Path, stub: Path) -> None:
    """Mutation: drop the pid hook from `DockerMysql._exec()`: `load_pid` stays 0."""
    server = tmp_path / "server"
    server.mkdir()
    mysql = _LoadingMysql(DB, "pw")
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), server, running=running(DB))
    outcome: list[Any] = []

    def run() -> None:
        try:
            outcome.append(restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT))
        except Exception as exc:  # noqa: BLE001 - reported by the test thread
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        _wait_for(stub / "load.pid")
        child = int((stub / "load.pid").read_text().split()[0])
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            marker = interrupted_restore(server)
            if marker is not None and marker.load_pid:
                break
            time.sleep(0.05)
        assert marker is not None and marker.load_pid == child, marker
        assert marker.load_running, "a live load must read as running"
        assert plan_restore(
            plan.backup, server, running=running(DB)
        ).refusals, "a second restore must be refused while the first one writes"
        with pytest.raises(MaintenanceError, match="still running"):
            forget_interrupted_restore(server)
    finally:
        (stub / "release").write_text("go")
        worker.join(30)
    assert not isinstance(outcome[0], Exception), outcome
    assert interrupted_restore(server) is None, "a finished restore leaves no marker"


def _marker_with(
    server: Path,
    pid: int,
    *,
    host: str | None = None,
    token: str | None = None,
    name: str | None = None,
) -> None:
    """A marker naming `pid`; its start token/name are the live process's own unless given."""
    here_token, here_name = maintenance.process_identity(pid)
    folder = maintenance.backups_dir(server)
    folder.mkdir(parents=True, exist_ok=True)
    record = maintenance.InterruptedRestore(
        marker=maintenance.marker_path(server),
        backup=server / "b.sql",
        databases=("acore_world",),
        safety_backup=(),
        started_at="2026-10-11T04:00:00",
        load_pid=pid,
        load_host=host if host is not None else maintenance.this_host(),
        load_token=here_token if token is None else token,
        load_name=here_name if name is None else name,
    )
    record.marker.write_text(record.as_json(), encoding="utf-8")


@pytest.fixture
def live_child() -> Any:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield child
    child.kill()
    child.wait()


def test_a_load_that_outlived_yulon_reads_as_still_running(tmp_path: Path, live_child: Any) -> None:
    _marker_with(tmp_path, live_child.pid)
    marker = interrupted_restore(tmp_path)
    assert marker is not None and marker.load_running
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running(DB))
    assert any("still running" in refusal for refusal in plan.refusals), plan.refusals


def test_a_load_that_has_ended_is_an_interrupted_restore_as_before(tmp_path: Path) -> None:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    _marker_with(tmp_path, gone.pid)
    marker = interrupted_restore(tmp_path)
    assert marker is not None and not marker.load_running
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running(DB))
    assert not any("still running" in refusal for refusal in plan.refusals)
    assert forget_interrupted_restore(tmp_path) is True


def test_a_pid_from_another_computer_or_a_very_old_marker_is_not_trusted(
    tmp_path: Path, live_child: Any
) -> None:
    """A trap otherwise: a shared folder, or a pid the system gave to something else since."""
    _marker_with(tmp_path, live_child.pid, host="some-other-computer")
    marker = interrupted_restore(tmp_path)
    assert marker is not None and not marker.load_running
    _marker_with(tmp_path, live_child.pid)
    old = time.time() - 3 * 24 * 3600
    os.utime(maintenance.marker_path(tmp_path), (old, old))
    marker = interrupted_restore(tmp_path)
    assert marker is not None and not marker.load_running


def test_the_tab_says_wait_and_offers_nothing_while_the_load_runs(
    qapp: object, tmp_path: Path, live_child: Any
) -> None:
    """Mutation: drop the `load_running` branch from `_show_interrupted()`."""
    from tests.test_teardown_guard import _view

    view, _fake, _ = _view(tmp_path)
    _marker_with(tmp_path, live_child.pid)
    record = interrupted_restore(tmp_path)
    view.services.interrupted_restore = lambda: record  # type: ignore[method-assign,assignment]

    view._show_interrupted()
    view.shutdown()

    assert not view.interrupted_label.isHidden()
    text = view.interrupted_label.text()
    assert "still running" in text and "wait" in text.lower(), text
    assert "Restoring again" not in text, "the escape was offered over a live load"
    assert view.forget_button.isHidden(), "Forget was offered over a live load"


# ---- round 4: a dead or foreign pid must never block the next restore


def test_a_reused_pid_is_not_the_load(tmp_path: Path, live_child: Any) -> None:
    """The pid names a live process, but not the one that was started for the load.

    Mutation: drop the token comparison from `InterruptedRestore.load_running`.
    """
    _marker_with(tmp_path, live_child.pid)
    assert interrupted_restore(tmp_path).load_running  # type: ignore[union-attr]
    token, _name = maintenance.process_identity(live_child.pid)
    assert token, "this platform gives no start time, and the test cannot stand in for one"
    _marker_with(tmp_path, live_child.pid, token="not-this-process")
    assert not interrupted_restore(tmp_path).load_running  # type: ignore[union-attr]
    _marker_with(tmp_path, live_child.pid, name="not-the-docker-client")
    if maintenance.process_identity(live_child.pid)[1]:
        assert not interrupted_restore(tmp_path).load_running  # type: ignore[union-attr]


def test_a_process_owned_by_someone_else_is_not_our_docker_client(
    tmp_path: Path, live_child: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`kill(pid, 0)` answering PermissionError used to count as alive.

    Mutation: make `_load_process_alive()` return True for PermissionError.
    """
    _marker_with(tmp_path, live_child.pid)

    def refused(pid: int, sig: int) -> None:
        raise PermissionError

    # Undone before the `live_child` fixture kills its process with the real `os.kill`.
    with monkeypatch.context() as patched:
        patched.setattr(maintenance.os, "kill", refused)
        assert not interrupted_restore(tmp_path).load_running  # type: ignore[union-attr]


def test_a_failed_load_does_not_leave_its_dead_pid_in_the_marker(
    tmp_path: Path, stub: Path
) -> None:
    """The marker stays (the databases are in an unknown state) but names no process.

    Mutation: drop the marker rewrite from `_restore_from()`'s except branch.
    """
    (stub / "fail").write_text("x")
    (stub / "release").write_text("go")
    server = tmp_path / "server"
    server.mkdir()
    mysql = _LoadingMysql(DB, "pw")
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), server, running=running(DB))

    with pytest.raises(MaintenanceError, match="failed part-way"):
        restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)

    marker = interrupted_restore(server)
    assert marker is not None, "the evidence of the half-applied load must stay"
    assert marker.load_pid == 0 and not marker.load_running
    assert forget_interrupted_restore(server) is True


def test_the_load_writes_to_files_not_pipes_and_a_flood_of_output_does_not_deadlock(
    tmp_path: Path, stub: Path
) -> None:
    """A mysql warning after Yu'lon quit must not meet a closed pipe (SIGPIPE on Linux).

    The child is a stub that checks what its stdout/stderr ARE, then writes 300 KB to each
    (far past a pipe buffer) while the parent is still waiting. Mutation: send the load's
    output to pipes again in `_run_reporting_pid()`.
    """
    (stub / "noise").write_text("x")
    (stub / "fail").write_text("x")
    mysql = _LoadingMysql(DB, "pw")
    out = tmp_path / "out"
    out.mkdir()
    problems: list[BaseException] = []

    def run() -> None:
        try:
            with maintenance.recording_load_pid(lambda pid: None, output_dir=out):
                mysql.load_from(a_backup_of(tmp_path, "acore_world").open("rb"))
        except MaintenanceError as exc:
            problems.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        _wait_for(stub / "load.pid")
        assert (stub / "load.pid").read_text().split()[1] == "ff", "stdout/stderr must be files"
    finally:
        (stub / "release").write_text("go")
        worker.join(30)
    assert not worker.is_alive(), "the load deadlocked on its own output"
    assert problems and "THE END" in problems[0].detail, "stderr must still reach the error"


# ---- T711: a Stop over a load that outlived Yu'lon asks first


def _stop_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str | None) -> Any:
    """A real tab over `tmp_path`'s restore record; the questions it asks and the jobs it ran."""
    from tests.test_teardown_guard import _view
    from yulon.ui import controller_view as controller_view_module

    view, _fake, _ = _view(tmp_path)
    view.services.interrupted_restore = lambda: interrupted_restore(tmp_path)  # type: ignore[method-assign,assignment]
    asked: list[str] = []

    def ask(_parent: object, title: str, text: str, yes: str, save: object = None) -> Any:
        asked.append(f"{title}|{text}|{yes}")
        return answer

    monkeypatch.setattr(controller_view_module, "_ask_with", ask)
    jobs: list[Any] = []
    view._run = lambda *args, **_kw: jobs.append(args)  # type: ignore[method-assign]
    return view, asked, jobs


def test_a_stop_over_a_load_that_outlived_yulon_asks_and_keeps_it_on_no(
    qapp: object, tmp_path: Path, live_child: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T711: the server hold ended with the quitting Yu'lon, so nothing asked before a Stop
    stopped the database under the orphaned load (its Restart is refused by the record).

    Mutation: drop the `_stop_over_a_running_load_agreed()` ask from `stop_server()`.
    """
    _marker_with(tmp_path, live_child.pid)
    view, asked, jobs = _stop_view(tmp_path, monkeypatch, answer=None)

    view.stop_server()
    view.shutdown()

    assert len(asked) == 1 and "half-written" in asked[0] and "Stop anyway" in asked[0], asked
    assert jobs == [], "the Stop ran over a load the player chose to keep"
    assert not view._busy


def test_a_stop_over_a_running_load_stops_when_told_to(
    qapp: object, tmp_path: Path, live_child: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Stop always stops" (owner, 2026-10-09): asked once, then it stops."""
    _marker_with(tmp_path, live_child.pid)
    view, asked, jobs = _stop_view(tmp_path, monkeypatch, answer="yes")

    view.stop_server()
    view.shutdown()

    assert len(asked) == 1 and len(jobs) == 1


def test_a_stop_with_no_running_load_asks_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dead load (an interrupted restore) or no record at all: Stop as always."""
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    _marker_with(tmp_path, gone.pid)
    view, asked, jobs = _stop_view(tmp_path, monkeypatch, answer=None)

    view.stop_server()
    view.shutdown()

    assert asked == [] and len(jobs) == 1


def test_a_stop_during_this_sessions_own_restore_asks_nothing_new(
    qapp: object, tmp_path: Path, live_child: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record names THIS session's live load: the restore's own hold refuses the Stop in
    its words, and "from the last session" would be untrue.

    Mutation: drop the `_restore_running` clause from `_stop_over_a_running_load_agreed()`.
    """
    _marker_with(tmp_path, live_child.pid)
    view, asked, jobs = _stop_view(tmp_path, monkeypatch, answer=None)
    view._restore_running = True

    view.stop_server()
    view.shutdown()

    assert asked == [] and len(jobs) == 1
