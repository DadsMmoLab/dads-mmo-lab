"""Backup/restore behaviour for the WotLK maintenance module.

Everything runs through the `MysqlDocker` seam, so no test here needs a Docker
daemon or a database. The two tests that pin argv/env drive the real
`DockerMysql` with `subprocess.run` replaced.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import IO

import pytest

from yulon import docker
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import (
    DockerMysql,
    MaintenanceError,
    backup,
    backups_dir,
    interrupted_restore,
    plan_restore,
    restore,
    verify_dump,
)

WORLD = "ac-worldserver"
AUTH = "ac-authserver"
DB = "ac-database"

AT = datetime(2026, 8, 23, 14, 30, 5)


def good_dump(*databases: str) -> bytes:
    """A dump shaped like the real thing: banner, a USE per database, end marker."""
    parts = [b"-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)\n--\n"]
    for name in databases:
        raw = name.encode("utf-8")
        parts.append(
            b"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `" + raw + b"`;\n"
            b"USE `" + raw + b"`;\n"
            b"INSERT INTO `t` VALUES (1);\n"
        )
    parts.append(b"-- Dump completed on 2026-08-23 14:30:05\n")
    return b"".join(parts)


class FakeMysql:
    """A database container that answers from memory."""

    def __init__(self, present: tuple[str, ...], *, body: bytes | None = None) -> None:
        self.present = present
        self._body = body
        self.dumped: list[str] = []
        self.loaded: list[bytes] = []

    def databases(self) -> tuple[str, ...]:
        return self.present

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        self.dumped.append(database)
        sink.write(self._body if self._body is not None else good_dump(database))

    def load_from(self, source: IO[bytes]) -> None:
        self.loaded.append(source.read())


def running(*names: str) -> maintenance.RunningNames:
    return lambda: list(names)


def a_backup_of(tmp_path: Path, *databases: str, name: str = "backup.sql") -> Path:
    path = tmp_path / name
    path.write_bytes(good_dump(*databases))
    return path


# ------------------------------------------------------------------ backup


def test_backup_dumps_the_databases_this_install_actually_has(tmp_path: Path) -> None:
    """Not the guide's fixed three: an install with playerbots gets its bots backed up."""
    mysql = FakeMysql(("acore_auth", "acore_characters", "acore_world", "acore_playerbots"))
    report = backup(tmp_path, mysql, running=running(DB), now=AT)
    assert report.databases == (
        "acore_auth",
        "acore_characters",
        "acore_playerbots",
        "acore_world",
    )
    assert report.missing_core == ()
    assert sorted(p.name for p in backups_dir(tmp_path).glob("*.sql")) == [
        "20260823_143005_acore_auth.sql",
        "20260823_143005_acore_characters.sql",
        "20260823_143005_acore_playerbots.sql",
        "20260823_143005_acore_world.sql",
    ]


def test_backup_includes_a_database_no_expected_list_knows_about(tmp_path: Path) -> None:
    """An allow-list would drop a module's own schema without saying so."""
    mysql = FakeMysql(("acore_auth", "acore_characters", "acore_world", "acore_citybots"))
    report = backup(tmp_path, mysql, running=running(DB), now=AT)
    assert "acore_citybots" in report.databases


def test_backup_leaves_the_mysql_system_schemas_alone(tmp_path: Path) -> None:
    """`mysql` holds the container's own accounts; it is not part of a character backup."""
    mysql = FakeMysql(("mysql", "information_schema", "performance_schema", "sys", "acore_world"))
    report = backup(tmp_path, mysql, running=running(DB), now=AT)
    assert report.databases == ("acore_world",)


def test_backup_says_which_core_database_the_server_was_missing(tmp_path: Path) -> None:
    """A silently short backup is the trap; `acore_characters` absent is an alarm."""
    mysql = FakeMysql(("acore_auth", "acore_world"))
    report = backup(tmp_path, mysql, running=running(DB), now=AT)
    assert report.missing_core == ("acore_characters",)


def test_backup_refuses_when_the_database_container_is_not_running(tmp_path: Path) -> None:
    """There is nothing to dump from; a file is not the source."""
    mysql = FakeMysql(("acore_world",))
    with pytest.raises(MaintenanceError, match="is not running"):
        backup(tmp_path, mysql, running=running(), now=AT)
    assert mysql.dumped == []


def test_backup_refuses_a_named_database_the_server_does_not_have(tmp_path: Path) -> None:
    """`only=` is an instruction, so an unhonoured name must not pass as success."""
    mysql = FakeMysql(("acore_world",))
    with pytest.raises(MaintenanceError, match="no acore_playerbots"):
        backup(tmp_path, mysql, only=("acore_playerbots",), running=running(DB), now=AT)


def test_backup_notes_that_the_server_was_running_while_it_ran(tmp_path: Path) -> None:
    """The dumps are consistent one by one, not with each other; the caller is told."""
    mysql = FakeMysql(("acore_world",))
    report = backup(tmp_path, mysql, running=running(DB, AUTH, WORLD), now=AT)
    assert report.server_was_running is True


def test_backup_rejects_a_dump_that_exited_zero_but_stopped_mid_file(tmp_path: Path) -> None:
    """mysqldump's end marker is the only thing that separates truncated from finished."""
    truncated = good_dump("acore_world").split(b"-- Dump completed")[0]
    mysql = FakeMysql(("acore_world",), body=truncated)
    with pytest.raises(MaintenanceError, match="stops before mysqldump's end marker"):
        backup(tmp_path, mysql, running=running(DB), now=AT)


def test_backup_rejects_an_empty_dump_that_exited_zero(tmp_path: Path) -> None:
    """The failure the guide has actually seen, which its `>` redirect produces."""
    mysql = FakeMysql(("acore_world",), body=b"")
    with pytest.raises(MaintenanceError, match="is empty"):
        backup(tmp_path, mysql, running=running(DB), now=AT)


def test_a_rejected_dump_never_leaves_a_file_that_looks_like_a_backup(tmp_path: Path) -> None:
    """The name is applied last, to bytes that already verified — nothing part-written keeps it."""
    mysql = FakeMysql(("acore_world",), body=b"-- MySQL dump 10.13\nINSERT INTO t VALUES (1);\n")
    with pytest.raises(MaintenanceError):
        backup(tmp_path, mysql, running=running(DB), now=AT)
    assert list(backups_dir(tmp_path).iterdir()) == []


def test_a_failed_backup_names_what_it_did_and_did_not_write(tmp_path: Path) -> None:
    """A partial set reported as success is the whole trap; the message is explicit."""

    class HalfBroken(FakeMysql):
        def dump_into(self, database: str, sink: IO[bytes]) -> None:
            self.dumped.append(database)
            sink.write(good_dump(database) if database == "acore_auth" else b"")

    mysql = HalfBroken(("acore_auth", "acore_world"))
    with pytest.raises(MaintenanceError) as caught:
        backup(tmp_path, mysql, running=running(DB), now=AT)
    message = str(caught.value)
    assert "INCOMPLETE" in message
    assert "20260823_143005_acore_auth.sql" in message
    assert "Not backed up: acore_world" in message


def test_backup_never_overwrites_an_existing_backup_file(tmp_path: Path) -> None:
    """Two backups in the same second must not silently become one."""
    mysql = FakeMysql(("acore_world",))
    backup(tmp_path, mysql, running=running(DB), now=AT)
    with pytest.raises(MaintenanceError, match="refusing to overwrite a backup"):
        backup(tmp_path, mysql, running=running(DB), now=AT)


def test_backup_fails_closed_when_docker_will_not_say_what_is_running(tmp_path: Path) -> None:
    """ "Docker did not answer" must never be read as "nothing is running"."""

    def unreachable() -> list[str]:
        raise docker.DockerCommandError("daemon down")

    mysql = FakeMysql(("acore_world",))
    with pytest.raises(MaintenanceError, match="could not ask Docker"):
        backup(tmp_path, mysql, running=unreachable, now=AT)


def test_verify_dump_rejects_a_dump_of_a_different_database(tmp_path: Path) -> None:
    """A stale file under the expected name is not this database's backup."""
    path = a_backup_of(tmp_path, "acore_auth")
    with pytest.raises(MaintenanceError, match="does not name acore_world"):
        verify_dump(path, "acore_world")


# ----------------------------------------------------------------- restore


def test_restore_refuses_while_the_worldserver_is_running(tmp_path: Path) -> None:
    """A live worldserver saves characters back over the restore, so this is a refusal."""
    path = a_backup_of(tmp_path, "acore_characters")
    plan = plan_restore(path, tmp_path, running=running(DB, WORLD))
    assert not plan.allowed
    assert any("are running" in reason for reason in plan.refusals)


def test_restore_refuses_when_the_database_container_is_down(tmp_path: Path) -> None:
    """There is nothing to restore into."""
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running())
    assert any("nothing to restore into" in reason for reason in plan.refusals)


def test_restore_refuses_a_path_that_does_not_exist(tmp_path: Path) -> None:
    """A mistyped path fails at plan time, before anything is touched."""
    plan = plan_restore(tmp_path / "typo.sql", tmp_path, running=running(DB))
    assert any("there is no file at" in reason for reason in plan.refusals)


def test_restore_refuses_a_file_that_is_not_a_mysqldump(tmp_path: Path) -> None:
    """A picked wrong file is rejected as such, not loaded to see what happens."""
    other = tmp_path / "notes.txt"
    other.write_text("my characters\n", encoding="utf-8")
    plan = plan_restore(other, tmp_path, running=running(DB))
    assert any("does not start like a mysqldump" in reason for reason in plan.refusals)


def test_restore_refuses_a_truncated_backup(tmp_path: Path) -> None:
    """The same end-marker check, applied to a file this module did not write."""
    path = tmp_path / "half.sql"
    path.write_bytes(good_dump("acore_world").split(b"-- Dump completed")[0])
    plan = plan_restore(path, tmp_path, running=running(DB))
    assert any("stops before mysqldump's end marker" in reason for reason in plan.refusals)


def test_restore_refuses_a_gzipped_backup_and_says_what_to_run(tmp_path: Path) -> None:
    """`wow-manage.sh` writes .sql.gz into the same folder; refuse rather than half-work."""
    path = tmp_path / "20260506_acore_world.sql.gz"
    path.write_bytes(b"\x1f\x8b")
    plan = plan_restore(path, tmp_path, running=running(DB))
    assert any("gunzip" in reason for reason in plan.refusals)


def test_a_plan_names_every_database_the_file_will_overwrite(tmp_path: Path) -> None:
    """The guide's own backup puts three in one file, and the later USE lines are deep in it."""
    path = tmp_path / "all.sql"
    body = good_dump("acore_characters")
    filler = b"INSERT INTO `t` VALUES ('x');\n" * 60_000  # past one scan chunk
    path.write_bytes(
        body.split(b"-- Dump completed")[0]
        + filler
        + good_dump("acore_auth", "acore_world").split(b"-- MySQL dump", 1)[1].split(b"--\n", 1)[1]
    )
    plan = plan_restore(path, tmp_path, running=running(DB))
    assert plan.refusals == ()
    assert plan.databases == ("acore_characters", "acore_auth", "acore_world")


def test_restore_refuses_a_confirmation_that_does_not_match_the_plan(tmp_path: Path) -> None:
    """Nothing here can be confirmed with a constant."""
    mysql = FakeMysql(("acore_world",))
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running(DB))
    with pytest.raises(MaintenanceError, match="was not confirmed"):
        restore(plan, mysql, confirm="yes", running=running(DB), now=AT)
    assert mysql.loaded == []


def test_restore_refuses_when_the_server_came_up_after_the_plan_was_made(tmp_path: Path) -> None:
    """A plan is a census, not a permission slip — it is re-taken before acting."""
    mysql = FakeMysql(("acore_world",))
    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))
    assert plan.allowed
    with pytest.raises(MaintenanceError, match="are running"):
        restore(plan, mysql, confirm=plan.token, running=running(DB, WORLD), now=AT)
    assert mysql.loaded == []


def test_restore_refuses_when_the_backup_changed_after_it_was_checked(tmp_path: Path) -> None:
    """The confirmation is bound to the file that was inspected, not to its path."""
    mysql = FakeMysql(("acore_world",))
    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))
    path.write_bytes(good_dump("acore_world") + b"-- Dump completed\n")
    with pytest.raises(MaintenanceError, match="not the file that was checked"):
        restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert mysql.loaded == []


def test_restore_takes_a_copy_of_what_it_is_about_to_overwrite(tmp_path: Path) -> None:
    """The undo path; named so it cannot be mistaken for a backup the user asked for."""
    mysql = FakeMysql(("acore_world",))
    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert [p.name for p in report.safety_backup] == ["20260823_143005_pre-restore_acore_world.sql"]
    assert mysql.loaded == [good_dump("acore_world")]


def test_a_finished_restore_leaves_no_marker(tmp_path: Path) -> None:
    """The marker means "in flight"; a completed restore must not look interrupted."""
    mysql = FakeMysql(("acore_world",))
    plan = plan_restore(a_backup_of(tmp_path, "acore_world"), tmp_path, running=running(DB))
    restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert interrupted_restore(tmp_path) is None


def test_an_interrupted_restore_leaves_a_marker_saying_what_was_in_flight(tmp_path: Path) -> None:
    """mysql leaves no trace of how far it got, so a half-overwritten DB needs one."""

    class Dies(FakeMysql):
        def load_from(self, source: IO[bytes]) -> None:
            raise MaintenanceError("connection lost")

    mysql = Dies(("acore_world",))
    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))
    with pytest.raises(MaintenanceError, match="unknown state"):
        restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    left = interrupted_restore(tmp_path)
    assert left is not None
    assert left.backup == path
    assert left.databases == ("acore_world",)
    assert [p.name for p in left.safety_backup] == ["20260823_143005_pre-restore_acore_world.sql"]


def test_a_retry_after_an_interrupted_restore_keeps_the_original_safety_copy(
    tmp_path: Path,
) -> None:
    """Dumping a half-restored database over it would destroy the last good copy."""

    class DiesOnce(FakeMysql):
        def __init__(self, present: tuple[str, ...]) -> None:
            super().__init__(present)
            self.attempts = 0

        def load_from(self, source: IO[bytes]) -> None:
            self.attempts += 1
            if self.attempts == 1:
                raise MaintenanceError("connection lost")
            super().load_from(source)

    mysql = DiesOnce(("acore_world",))
    path = a_backup_of(tmp_path, "acore_world")
    first = plan_restore(path, tmp_path, running=running(DB))
    with pytest.raises(MaintenanceError):
        restore(first, mysql, confirm=first.token, running=running(DB), now=AT)
    original = interrupted_restore(tmp_path)
    assert original is not None

    second = plan_restore(path, tmp_path, running=running(DB))
    assert second.interrupted is not None
    later = datetime(2026, 8, 23, 15, 0, 0)
    report = restore(second, mysql, confirm=second.token, running=running(DB), now=later)
    assert report.safety_backup == original.safety_backup
    assert mysql.dumped == ["acore_world"], "no second dump of a part-restored database"
    assert interrupted_restore(tmp_path) is None


def test_a_restore_into_a_database_that_does_not_exist_yet_takes_no_safety_copy(
    tmp_path: Path,
) -> None:
    """There is nothing to save: the file creates the schema rather than replacing one."""
    mysql = FakeMysql(("acore_world",))
    plan = plan_restore(a_backup_of(tmp_path, "acore_playerbots"), tmp_path, running=running(DB))
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert report.safety_backup == ()
    assert mysql.dumped == []


# ------------------------------------------------------- the docker command


def _capture(monkeypatch: pytest.MonkeyPatch) -> tuple[list[list[str]], list[dict[str, object]]]:
    argvs: list[list[str]] = []
    kwargs: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        argvs.append(argv)
        kwargs.append(kw)
        sink = kw.get("stdout")
        if hasattr(sink, "write"):
            sink.write(good_dump("acore_world"))  # type: ignore[union-attr]
        return subprocess.CompletedProcess(argv, 0, b"acore_world\nmysql\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return argvs, kwargs


def test_the_root_password_never_reaches_the_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """argv is world-readable; the password goes over `MYSQL_PWD` like everywhere else."""
    argvs, kwargs = _capture(monkeypatch)
    with (tmp_path / "out.sql").open("wb") as sink:
        DockerMysql(DB, "hunter2").dump_into("acore_world", sink)
    assert argvs[0] == [
        "docker",
        "exec",
        "-e",
        "MYSQL_PWD",
        "ac-database",
        "mysqldump",
        "-uroot",
        "--single-transaction",
        "--routines",
        "--events",
        "--triggers",
        "--databases",
        "acore_world",
    ]
    assert "hunter2" not in " ".join(argvs[0])
    env = kwargs[0]["env"]
    assert isinstance(env, dict) and env["MYSQL_PWD"] == "hunter2"


def test_the_dump_goes_straight_to_the_file_and_never_through_this_process(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A gigabyte cannot be carried in a Python string; the child writes to the fd."""
    _argvs, kwargs = _capture(monkeypatch)
    target = tmp_path / "out.sql"
    with target.open("wb") as sink:
        DockerMysql(DB, "hunter2").dump_into("acore_world", sink)
    assert kwargs[0]["stdout"] is not subprocess.PIPE
    assert target.read_bytes() == good_dump("acore_world")


def test_listing_databases_sends_the_statement_over_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """One rule for SQL, not one rule with exceptions — and `-i` only where input is sent."""
    argvs, kwargs = _capture(monkeypatch)
    assert DockerMysql(DB, "hunter2").databases() == ("acore_world", "mysql")
    assert argvs[0][:3] == ["docker", "exec", "-i"]
    assert kwargs[0]["input"] == b"SHOW DATABASES;\n"


def test_a_restore_names_no_database_on_the_command_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The dump carries its own CREATE DATABASE/USE, so there is one source of that truth."""
    argvs, _kwargs = _capture(monkeypatch)
    path = a_backup_of(tmp_path, "acore_world")
    with path.open("rb") as source:
        DockerMysql(DB, "hunter2").load_from(source)
    assert argvs[0] == ["docker", "exec", "-i", "-e", "MYSQL_PWD", "ac-database", "mysql", "-uroot"]


def test_a_missing_docker_cli_is_reported_as_a_maintenance_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not a bare FileNotFoundError — the same sentence every other module gives."""
    from yulon import platform

    monkeypatch.setattr(platform, "_resolved_docker_cli", None)
    monkeypatch.setattr(platform, "docker_programs", lambda: ("docker",))
    monkeypatch.setattr(platform, "_which", lambda name, path=None: None)
    with pytest.raises(MaintenanceError, match="Docker could not be found"):
        DockerMysql(DB, "hunter2").databases()
