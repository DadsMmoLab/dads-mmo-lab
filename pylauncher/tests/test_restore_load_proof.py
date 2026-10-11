"""A restore load that outlived Yu'lon and FINISHED leaves the tab clean (T710).

"Quit anyway" (T690) ends Yu'lon while the restore's `docker exec ... mysql` load carries
on. Measured on yulon-win11 (2026-10-11): the orphaned load ran to the end of the dump, and
the relaunched Maintenance tab still said the restore "did not finish, may be half-written",
because nothing recorded how the load ended. An empty `restore-load.err` is no proof: a
killed load leaves one too.

So the load runs inside the container under a few lines of `sh` that print a completion
line carrying mysql's exit status and a per-load proof word into `restore-load.out` (a file
the docker client writes, so it lands only while the client is alive to the end). The next
reader of the restore record puts the record down ONLY for that line with this load's proof
word and exit 0; anything else keeps the cautious "did not finish".

Every test here runs the real argv `DockerMysql.load_from()` builds: a stub stands in for
the `docker` client and runs the in-container command with the real `sh` and a fake `mysql`
that reads the dump and exits as told. Yu'lon's quit is a `BaseException` raised right after
the real marker write that records the load, so the load is left running exactly as
`os._exit` leaves it.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import sys
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
    a_marker,
    an_earlier_copy_of,
    plan_restore,
    restore,
    running,
)
from yulon import docker
from yulon.catalog import native
from yulon.controller_wow_wotlk import docker_ctl, maintenance
from yulon.controller_wow_wotlk.maintenance import DockerMysql, interrupted_restore

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None,
    reason="runs the in-container `sh` wrapper on this machine's own sh",
)

# Stands in for the `docker` client: drops `exec` and its options up to the container name,
# says who it is, waits to be released, then runs the in-container command with the dump on
# its stdin and its output where the client's own goes (the load's files).
DOCKER_STUB = """
import os, subprocess, sys, time
here = os.path.dirname(os.path.abspath(__file__))
args = sys.argv[1:]
assert args[0] == "exec", args
args = args[1:]
while args[0].startswith("-"):
    flag = args.pop(0)
    if flag == "-e":
        args.pop(0)
args.pop(0)  # the container
with open(os.path.join(here, "argv"), "w") as f:
    f.write("\\n".join(args))
with open(os.path.join(here, "load.pid"), "w") as f:
    f.write(str(os.getpid()))
while not os.path.exists(os.path.join(here, "release")):
    time.sleep(0.05)
sys.exit(subprocess.call(args))
"""

QUIT_LINE = "yulon-load-ended"


class _QuitAnyway(BaseException):
    """Yu'lon ending right after the load was recorded, as `os._exit` does."""


@dataclass(frozen=True)
class _LoadingMysql(DockerMysql):
    """The real `load_from` (a child process), everything else answered from memory."""

    def databases(self) -> tuple[str, ...]:
        return ("acore_world", "acore_characters")

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        FakeMysql(("acore_world", "acore_characters")).dump_into(database, sink)


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "stub"
    folder.mkdir()
    script = folder / "docker_stub.py"
    script.write_text(DOCKER_STUB, encoding="utf-8")
    bin_dir = folder / "bin"
    bin_dir.mkdir()
    fake_mysql = bin_dir / "mysql"
    fake_mysql.write_text(
        f'#!/bin/sh\ncat > /dev/null\nexit "$(cat {folder / "exit_code"} 2>/dev/null || echo 0)"\n',
        encoding="utf-8",
    )
    fake_mysql.chmod(fake_mysql.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(
        maintenance.platform, "docker_prefix", lambda _distro: [sys.executable, str(script)]
    )
    monkeypatch.setattr(maintenance, "mysql_client", lambda *a, **k: "mysql")
    # The real one is this environment plus MYSQL_PWD: the fake `mysql` is found on its PATH.
    monkeypatch.setattr(maintenance, "mysql_env", lambda *a, **k: dict(os.environ))
    return folder


def _wait_for(path: Path, seconds: float = 20.0) -> None:
    end = time.monotonic() + seconds
    while not path.exists():
        assert time.monotonic() < end, f"{path.name} never appeared"
        time.sleep(0.05)


def _quit_once_the_load_is_recorded(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Patch `_write_marker` to end "Yu'lon" right after the load is recorded; the real one."""
    real = maintenance._write_marker

    def write_then_quit(record: maintenance.InterruptedRestore, *, consequence: str) -> None:
        real(record, consequence=consequence)
        if record.load_pid > 0:
            raise _QuitAnyway

    monkeypatch.setattr(maintenance, "_write_marker", write_then_quit)
    return real


def _restore_and_quit(
    tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch, *databases: str
) -> tuple[Path, int]:
    """A restore whose Yu'lon quits once the load runs; (server folder, the load's pid)."""
    server = tmp_path / "server"
    server.mkdir(exist_ok=True)
    mysql = _LoadingMysql(DB, "pw")
    backup = a_backup_of(tmp_path, *(databases or ("acore_world",)))
    plan = plan_restore(backup, server, running=running(DB))
    assert plan.allowed, plan.refusals
    real = _quit_once_the_load_is_recorded(monkeypatch)
    with pytest.raises(_QuitAnyway):
        restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    # The next marker writes are the reader's own, not a quitting restore's.
    monkeypatch.setattr(maintenance, "_write_marker", real)
    _wait_for(stub / "load.pid")
    return server, int((stub / "load.pid").read_text())


def _let_the_load_end(stub: Path, pid: int, *, exit_code: int) -> int:
    """Release the orphaned load and reap it (the test process is its parent, not init)."""
    (stub / "exit_code").write_text(str(exit_code))
    (stub / "release").write_text("go")
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status)


def test_an_orphaned_load_that_ended_with_exit_0_leaves_no_record(
    tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The T710 live case: Quit anyway, the load runs to the end, the next look is clean.

    Mutation: make `_finished_cleanly()` answer False: the record stays. And: drop the
    import record's removal from `_settle_a_finished_load()` (T678: a finished restore
    removes it, or Start keeps refusing the restored server).
    """
    server, pid = _restore_and_quit(tmp_path, stub, monkeypatch)
    recorded = interrupted_restore(server)
    assert recorded is not None and recorded.load_pid == pid and recorded.load_running
    import_record = docker.one_shot_ended_marker(server, docker_ctl.SPEC.import_service)
    import_record.write_text("import under way\n", encoding="utf-8")
    assert _let_the_load_end(stub, pid, exit_code=0) == 0

    argv = (stub / "argv").read_text().splitlines()
    assert argv[:2] == ["sh", "-c"], f"the load is not wrapped: {argv}"

    assert interrupted_restore(server) is None, "a load proven finished still reads as unfinished"
    assert not maintenance.marker_path(server).exists()
    folder = maintenance.backups_dir(server)
    assert not (folder / "restore-load.out").exists(), "the finished load's output was left"
    assert not (folder / "restore-load.err").exists()
    assert native.unfinished_restore_refusal(server) is None, "Start still refused"
    assert not import_record.exists(), "the unfinished-import record outlived the restore"


def test_an_orphaned_load_that_failed_keeps_the_record(
    tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit 1 is printed too, and it is not a finish.

    Mutation: accept any exit status in `_finished_cleanly()`.
    """
    server, pid = _restore_and_quit(tmp_path, stub, monkeypatch)
    assert _let_the_load_end(stub, pid, exit_code=1) == 1, "mysql's exit must reach the client"

    record = interrupted_restore(server)
    assert record is not None and not record.load_running
    assert QUIT_LINE in (maintenance.backups_dir(server) / "restore-load.out").read_text()
    assert native.unfinished_restore_refusal(server) is not None


def test_a_killed_load_with_an_empty_error_file_is_not_a_finish(
    tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty `.err` is what the live finish left, and what a killed load leaves too.

    Mutation: count "the load's process is gone and its `.err` is empty" as a finish.
    """
    server, pid = _restore_and_quit(tmp_path, stub, monkeypatch)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    folder = maintenance.backups_dir(server)
    assert (folder / "restore-load.err").read_bytes() == b""

    record = interrupted_restore(server)
    assert record is not None and record.load_pid == pid and not record.load_running
    assert "cannot tell whether it finished" in maintenance.unfinished_restore_sentence(record)


def test_a_finish_line_of_another_load_proves_nothing(
    tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The proof word is this load's: an older load's line in the file does not count.

    Mutation: drop the proof word from the match in `_finished_cleanly()`.
    """
    server, pid = _restore_and_quit(tmp_path, stub, monkeypatch)
    os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    out = maintenance.backups_dir(server) / "restore-load.out"
    out.write_text(f"\n{QUIT_LINE} not-this-load 0\n", encoding="utf-8")

    assert interrupted_restore(server) is not None


def test_a_finished_load_leaves_what_an_earlier_unfinished_restore_still_owes(
    tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record shrinks to what this load did not cover, as `restore()` does on success.

    Mutation: unlink the marker whatever `load_after` says.
    """
    server = tmp_path / "server"
    server.mkdir()
    copy = an_earlier_copy_of(server, "acore_characters")
    a_marker(
        server,
        backup=tmp_path / "older.sql",
        databases=("acore_characters",),
        safety=(copy,),
    )
    server, pid = _restore_and_quit(tmp_path, stub, monkeypatch, "acore_world")
    assert _let_the_load_end(stub, pid, exit_code=0) == 0

    record = interrupted_restore(server)
    assert record is not None, "the earlier restore's record went with this one"
    assert record.databases == ("acore_characters",)
    assert record.safety_backup == (copy,)
    assert record.load_pid == 0
    again = interrupted_restore(server)
    assert again is not None and again.databases == ("acore_characters",), "not kept on disk"


def test_the_tab_is_clean_after_a_proven_finish(
    qapp: object, tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Maintenance tab reads the same record: no "did not finish", no Forget."""
    from tests.test_teardown_guard import _view

    server, pid = _restore_and_quit(tmp_path, stub, monkeypatch)
    view, _fake, _ = _view(tmp_path, "tab")
    view.services.interrupted_restore = lambda: interrupted_restore(server)  # type: ignore[method-assign,assignment]

    view._show_interrupted()
    assert "still running" in view.interrupted_label.text()

    assert _let_the_load_end(stub, pid, exit_code=0) == 0
    view._show_interrupted()
    view.shutdown()
    assert view.interrupted_label.isHidden(), view.interrupted_label.text()
    assert view.forget_button.isHidden()


def test_the_in_container_wrapper_needs_no_windows_quoting(stub: Path) -> None:
    """From Windows the argv goes through `list2cmdline` to docker.exe: no `"` and no `\\"`.

    A script with neither round-trips by being wrapped in one pair of quotes, so docker.exe
    hands `sh` the same text it gets on Linux.
    """
    script = maintenance._LOAD_PROOF_SCRIPT
    assert '"' not in script and "\\\\" not in script
    line = __import__("subprocess").list2cmdline(["sh", "-c", script])
    assert line == f'sh -c "{script}"'


def test_a_server_in_a_wsl_distro_keeps_the_plain_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`wsl -- docker ...` hands the line to the distro's shell, which would expand the
    wrapper's `$1` for itself: there the load stays as it was, and the tab stays cautious."""
    seen: list[list[str]] = []

    def fake_run(argv: list[str], watch: Any, **_kw: Any) -> Any:
        seen.append(argv)
        return __import__("subprocess").CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(maintenance.platform, "docker_prefix", lambda d: ["wsl", "--", "docker"])
    monkeypatch.setattr(maintenance, "mysql_client", lambda *a, **k: "mysql")
    monkeypatch.setattr(maintenance, "mysql_env", lambda *a, **k: {})
    monkeypatch.setattr(maintenance, "_run_reporting_pid", fake_run)
    mysql = DockerMysql(DB, "pw", wsl_distro="dml-arch")
    with maintenance.recording_load_pid(lambda pid: None, output_dir=tmp_path, proof="p1"):
        mysql.load_from(a_backup_of(tmp_path, "acore_world").open("rb"))
    assert "sh" not in seen[0], seen[0]


def test_a_record_whose_leftover_cannot_be_read_is_never_put_down(
    tmp_path: Path, stub: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`load_after` of the wrong shape: what the load leaves is unknown, so nothing is settled.

    Mutation: keep `load_proof` when `load_after` cannot be read in `_read_record()`.
    """
    server, pid = _restore_and_quit(tmp_path, stub, monkeypatch)
    marker = maintenance.marker_path(server)
    raw = json.loads(marker.read_text(encoding="utf-8"))
    raw["load_after"] = "acore_characters"
    marker.write_text(json.dumps(raw), encoding="utf-8")
    assert _let_the_load_end(stub, pid, exit_code=0) == 0

    assert interrupted_restore(server) is not None
