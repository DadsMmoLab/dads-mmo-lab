"""Backup/restore behaviour for the WotLK maintenance module.

Everything runs through the `MysqlDocker` seam, so no test here needs a Docker
daemon or a database. The two tests that pin argv/env drive the real
`DockerMysql` with `subprocess.run` replaced.
"""

from __future__ import annotations

import io
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


def an_earlier_copy_of(server_dir: Path, database: str, *, body: bytes | None = None) -> Path:
    """A `pre-restore` copy in the backups dir, as an earlier restore would have left it."""
    path = backups_dir(server_dir) / f"20260823_120000_pre-restore_{database}.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(good_dump(database) if body is None else body)
    return path


def a_marker(
    server_dir: Path, *, backup: Path, databases: tuple[str, ...], safety: tuple[Path, ...]
) -> Path:
    """The marker an interrupted restore of `databases` would have left behind."""
    marker = maintenance.marker_path(server_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        maintenance.InterruptedRestore(
            marker=marker,
            backup=backup,
            databases=databases,
            safety_backup=safety,
            started_at="2026-08-23T12:00:00",
        ).as_json(),
        encoding="utf-8",
    )
    return marker


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


def test_a_dump_that_fails_verification_is_deleted_rather_than_kept(tmp_path: Path) -> None:
    """Bytes that did not verify are not a backup, and are not left lying about as one."""
    mysql = FakeMysql(("acore_world",), body=b"-- MySQL dump 10.13\nINSERT INTO t VALUES (1);\n")
    with pytest.raises(MaintenanceError):
        backup(tmp_path, mysql, running=running(DB), now=AT)
    assert list(backups_dir(tmp_path).iterdir()) == []


def test_a_dump_only_takes_a_backups_name_after_it_has_verified(tmp_path: Path) -> None:
    """While it is being written it is a `.partial`; the rename is the last step."""

    class Watches(FakeMysql):
        seen = ""

        def dump_into(self, database: str, sink: IO[bytes]) -> None:
            Watches.seen = Path(sink.name).name
            super().dump_into(database, sink)

    report = backup(tmp_path, Watches(("acore_world",)), running=running(DB), now=AT)
    assert Watches.seen == "20260823_143005_acore_world.sql.partial"
    assert report.dumps[0].path.name == "20260823_143005_acore_world.sql"


def test_a_dump_killed_part_way_leaves_nothing_wearing_a_backups_name(tmp_path: Path) -> None:
    """No `except` runs when the process is killed, so the naming has to be what saves it.

    This is the case the guide's `> ~/wow-backup-$(date).sql` cannot survive: the
    shell makes the file before mysqldump writes a byte, so an interrupted dump
    leaves a truncated file called a backup.
    """

    class Killed(FakeMysql):
        def dump_into(self, database: str, sink: IO[bytes]) -> None:
            sink.write(good_dump(database)[:40])
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        backup(tmp_path, Killed(("acore_world",)), running=running(DB), now=AT)
    assert list(backups_dir(tmp_path).glob("*.sql")) == []
    assert [p.name for p in backups_dir(tmp_path).iterdir()] == [
        "20260823_143005_acore_world.sql.partial"
    ]


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
    assert any("is running" in reason for reason in plan.refusals)


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
    with pytest.raises(MaintenanceError, match="is running"):
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


def test_a_restore_notices_a_marker_that_appeared_after_its_plan_was_made(
    tmp_path: Path,
) -> None:
    """The marker is re-read with the census, not carried over from a stale plan.

    Reading the plan's copy would take a safety dump of a database some other
    restore had already part-overwritten, which captures the mess and drops the
    pointer to the last good copy.
    """
    mysql = FakeMysql(("acore_world",))
    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))
    assert plan.interrupted is None

    earlier = backups_dir(tmp_path) / "20260823_120000_pre-restore_acore_world.sql"
    earlier.parent.mkdir(parents=True, exist_ok=True)
    earlier.write_bytes(good_dump("acore_world"))
    maintenance.marker_path(tmp_path).write_text(
        maintenance.InterruptedRestore(
            marker=maintenance.marker_path(tmp_path),
            backup=path,
            databases=("acore_world",),
            safety_backup=(earlier,),
            started_at="2026-08-23T12:00:00",
        ).as_json(),
        encoding="utf-8",
    )

    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert report.safety_backup == (earlier,)
    assert mysql.dumped == [], "no dump of a database a previous restore had part-overwritten"


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


def test_a_marker_about_one_database_is_no_cover_for_another(tmp_path: Path) -> None:
    """The safety dump is decided per database, not by whether a marker file exists.

    An interrupted restore of `acore_world` says nothing about `acore_characters`,
    which is healthy and about to be overwritten. Until 2026-08-23 the marker's
    mere existence suppressed the dump for every database and the report then
    named that world dump as this restore's safety copy: characters went under
    with no copy of it anywhere (review, 2026-08-23).
    """
    mysql = FakeMysql(("acore_auth", "acore_characters", "acore_world"))
    world_copy = an_earlier_copy_of(tmp_path, "acore_world")
    a_marker(
        tmp_path,
        backup=tmp_path / "world.sql",
        databases=("acore_world",),
        safety=(world_copy,),
    )

    chars = a_backup_of(tmp_path, "acore_characters", name="chars.sql")
    plan = plan_restore(chars, tmp_path, running=running(DB))
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)

    assert mysql.dumped == ["acore_characters"]
    assert [p.name for p in report.safety_backup] == [
        "20260823_143005_pre-restore_acore_characters.sql"
    ]


def test_a_marker_covers_only_the_databases_it_names_of_the_ones_being_overwritten(
    tmp_path: Path,
) -> None:
    """The mixed case: one database is part-restored, the other is not."""
    mysql = FakeMysql(("acore_characters", "acore_world"))
    world_copy = an_earlier_copy_of(tmp_path, "acore_world")
    a_marker(
        tmp_path,
        backup=tmp_path / "world.sql",
        databases=("acore_world",),
        safety=(world_copy,),
    )

    both = a_backup_of(tmp_path, "acore_world", "acore_characters", name="both.sql")
    plan = plan_restore(both, tmp_path, running=running(DB))
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)

    assert mysql.dumped == ["acore_characters"], "the half-restored world was dumped over"
    assert [p.name for p in report.safety_backup] == [
        world_copy.name,
        "20260823_143005_pre-restore_acore_characters.sql",
    ]


def test_a_restore_that_does_not_finish_an_earlier_one_keeps_its_marker(tmp_path: Path) -> None:
    """Restoring characters does not make a half-applied world whole, or forgettable.

    The marker is the only record that `acore_world` is half-overwritten, and a
    half-overwritten database looks like a working server until somebody logs
    in. Unlinking it here because *this* restore finished would destroy that
    record (review, 2026-08-23).
    """
    mysql = FakeMysql(("acore_characters", "acore_world"))
    world_copy = an_earlier_copy_of(tmp_path, "acore_world")
    a_marker(
        tmp_path,
        backup=tmp_path / "world.sql",
        databases=("acore_world",),
        safety=(world_copy,),
    )

    chars = a_backup_of(tmp_path, "acore_characters", name="chars.sql")
    plan = plan_restore(chars, tmp_path, running=running(DB))
    restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)

    left = interrupted_restore(tmp_path)
    assert left is not None, "the record that acore_world is half-restored was destroyed"
    assert left.databases == ("acore_world",)
    assert left.safety_backup == (world_copy,)


@pytest.mark.parametrize("body", ["not json at all", "{}", "[]", ""])
def test_a_marker_that_cannot_be_read_stands_in_for_no_safety_copy(
    tmp_path: Path, body: str
) -> None:
    """It is evidence a restore was in flight and evidence of nothing else.

    A corrupt, empty or foreign file at the marker path used to buy the restore
    out of taking any safety copy at all — no dump, an empty `safety_backup`,
    and no refusal saying the undo path had been skipped (review, 2026-08-23).
    """
    mysql = FakeMysql(("acore_world",))
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(body, encoding="utf-8")

    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)

    assert mysql.dumped == ["acore_world"]
    assert [p.name for p in report.safety_backup] == ["20260823_143005_pre-restore_acore_world.sql"]
    assert all(p.is_file() for p in report.safety_backup)


def test_a_marker_that_could_not_be_parsed_says_so_rather_than_reading_as_empty(
    tmp_path: Path,
) -> None:
    """ "A restore was in flight" and "a restore of acore_world was" are different answers.

    The unparseable case used to be a record indistinguishable from a real one
    with empty fields, which is how it came to read as a restore that had
    already taken a safety copy of nothing (review, 2026-08-23).
    """
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("not json at all", encoding="utf-8")
    unreadable = interrupted_restore(tmp_path)
    assert unreadable is not None and unreadable.readable is False

    a_marker(
        tmp_path,
        backup=tmp_path / "world.sql",
        databases=("acore_world",),
        safety=(an_earlier_copy_of(tmp_path, "acore_world"),),
    )
    parsed = interrupted_restore(tmp_path)
    assert parsed is not None and parsed.readable is True


@pytest.mark.parametrize("state", ["deleted", "truncated"])
def test_a_carried_forward_safety_copy_that_is_not_there_is_not_reported_as_one(
    tmp_path: Path, state: str
) -> None:
    """The report names the undo path at the one moment the user needs it to be real.

    A path was taken from the marker verbatim and never checked, so a copy the
    user had since deleted — or one that was itself cut short — was reported as
    this restore's safety copy (review, 2026-08-23).
    """
    mysql = FakeMysql(("acore_world",))
    if state == "deleted":
        gone = backups_dir(tmp_path) / "deleted_by_the_user.sql"
        gone.parent.mkdir(parents=True, exist_ok=True)
    else:
        gone = an_earlier_copy_of(
            tmp_path, "acore_world", body=good_dump("acore_world").split(b"-- Dump completed")[0]
        )
    path = a_backup_of(tmp_path, "acore_world")
    a_marker(tmp_path, backup=path, databases=("acore_world",), safety=(gone,))

    plan = plan_restore(path, tmp_path, running=running(DB))
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)

    assert mysql.dumped == ["acore_world"], "no copy was taken and none existed"
    assert [(p.name, p.is_file()) for p in report.safety_backup] == [
        ("20260823_143005_pre-restore_acore_world.sql", True)
    ]


def test_a_restore_that_cannot_write_its_marker_changes_nothing(tmp_path: Path) -> None:
    """Not best-effort: a restore nobody could tell had been interrupted must not start.

    Nothing is mocked here — the marker path is made a directory, so the real
    `os.replace` fails the way a read-only or full disk would. Only the
    docstring claimed this; deleting the raise left the suite green (review,
    2026-08-23).
    """
    mysql = FakeMysql(("acore_world",))
    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))
    maintenance.marker_path(tmp_path).mkdir(parents=True)

    with pytest.raises(MaintenanceError, match="could not write"):
        restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert mysql.loaded == []


def test_verify_dump_rejects_a_database_whose_name_merely_contains_the_wanted_one(
    tmp_path: Path,
) -> None:
    """`acore_worldxyz` is not `acore_world`, and a substring test cannot tell.

    The name is read out of the head's `USE`/`CREATE DATABASE` line as a whole
    identifier; `database.encode() in head` accepted this file (review,
    2026-08-23). `_usable_copies()` now leans on this check to decide whether a
    carried-forward safety copy really is the database it is standing in for.
    """
    path = a_backup_of(tmp_path, "acore_worldxyz")
    with pytest.raises(MaintenanceError, match="does not name acore_world"):
        verify_dump(path, "acore_world")


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


# The shape `platform.docker_program()` caches: a real path, on no PATH here.
OFF_PATH_EXE = r"C:\Users\pk\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"


def test_a_resolved_docker_that_has_since_gone_is_reported_the_same_way(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The other way to have no Docker, on all three of `DockerMysql`'s calls.

    `docker_program()` remembers a hit for the life of the process, so a Docker
    Desktop that updates or uninstalls itself while the launcher is open leaves
    that pinned path aimed at a file that is gone. `subprocess` reports it as
    `OSError`, which without this guard reaches the user as a bare `[WinError 2]`
    while `docker.start()` in the same session says "Docker could not be found".
    `apply.DockerSql._mysql()` carries the identical guard and the identical
    test (`test_docker_sql_says_the_same_thing_when_a_resolved_docker_has_gone`,
    written for the 2026-08-23 review); the copy here shipped with the docstring
    and without the test, and deleting the conversion left the suite green
    (review, 2026-08-23).
    """
    from yulon import platform

    monkeypatch.setattr(platform, "_resolved_docker_cli", OFF_PATH_EXE)

    def gone(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        raise FileNotFoundError(2, "The system cannot find the file specified", OFF_PATH_EXE)

    monkeypatch.setattr(subprocess, "run", gone)
    mysql = DockerMysql(DB, "hunter2")
    with pytest.raises(MaintenanceError, match="Docker could not be found"):
        mysql.databases()
    with (tmp_path / "out.sql").open("wb") as sink:
        with pytest.raises(MaintenanceError, match="Docker could not be found"):
            mysql.dump_into("acore_world", sink)
    with a_backup_of(tmp_path, "acore_world").open("rb") as source:
        with pytest.raises(MaintenanceError, match="Docker could not be found"):
            mysql.load_from(source)


def test_the_root_password_is_not_in_the_repr() -> None:
    """The one channel argv, stderr, the logs and the temp files all keep it out of.

    A frozen dataclass reprs every field by default, and this object is about to
    be handed to a worker thread: a pytest assertion diff, a logged object or a
    traceback frame dump in a UI error handler would each print the password
    (review, 2026-08-23).
    """
    mysql = DockerMysql(DB, "hunter2")
    assert "hunter2" not in repr(mysql)
    assert mysql.root_password == "hunter2", "still readable where it is actually needed"


def test_a_finished_restore_is_not_reported_as_failed_when_the_marker_will_not_shrink(
    tmp_path: Path,
) -> None:
    """The data is already loaded, so "Nothing was restored" is the one wrong thing to say.

    After the load the marker shrinks to whatever an earlier interrupted restore
    left unfinished. If THAT write fails, the restore has still happened —
    raising there reported a completed restore as a failure, in the exact words
    that would send a user to run it a second time (review, 2026-08-23).

    Nothing is mocked but the disk turning hostile mid-load, which is how a real
    one behaves: the `.tmp` path becomes a directory while mysql is applying the
    dump, so `os.replace` fails the way a full or read-only disk would.
    """
    marker = maintenance.marker_path(tmp_path)

    class HostileDisk(FakeMysql):
        def load_from(self, source: IO[bytes]) -> None:
            super().load_from(source)
            marker.with_name(marker.name + ".tmp").mkdir(parents=True, exist_ok=True)

    mysql = HostileDisk(("acore_ale", "acore_world"))
    a_marker(
        tmp_path,
        backup=tmp_path / "ale.sql",
        databases=("acore_ale",),
        safety=(an_earlier_copy_of(tmp_path, "acore_ale"),),
    )
    path = a_backup_of(tmp_path, "acore_world")
    plan = plan_restore(path, tmp_path, running=running(DB))

    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)

    assert report.databases == ("acore_world",)
    assert mysql.loaded, "the dump was applied, so the restore really did happen"


def test_an_interrupted_restore_can_be_acknowledged_and_stops_warning(tmp_path: Path) -> None:
    """A kept marker with nothing left to put it right would warn forever.

    Keeping the marker across later restores is deliberate — it is the only
    record that a database was left half-written. But a marker naming a schema
    this server does not have, or one the user restored by hand, is never
    covered by any future restore, so nothing ever cleared it and there was no
    way to say "I have dealt with this" (review, 2026-08-23).

    The backups it named must survive: acknowledging usually means one of them
    has already been used.
    """
    copy = an_earlier_copy_of(tmp_path, "acore_ale")
    a_marker(tmp_path, backup=tmp_path / "ale.sql", databases=("acore_ale",), safety=(copy,))
    assert interrupted_restore(tmp_path) is not None

    assert maintenance.forget_interrupted_restore(tmp_path) is True
    assert interrupted_restore(tmp_path) is None
    assert copy.exists(), "acknowledging the record must not delete the safety copy"

    assert maintenance.forget_interrupted_restore(tmp_path) is False, "nothing left to forget"


def test_a_marker_whose_fields_are_the_wrong_shape_names_nothing(tmp_path: Path) -> None:
    """A readable dict is not automatically a marker this code wrote.

    `{"databases": "acore_world"}` is valid JSON and a valid object, and
    `tuple(str(n) for n in ...)` iterates a STRING CHARACTER BY CHARACTER — so it
    produced a marker naming eleven fictional one-letter databases, which then
    could never be cleared because no restore would ever cover them
    (review, 2026-08-23).
    """
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text('{"databases": "acore_world"}', encoding="utf-8")

    record = interrupted_restore(tmp_path)
    assert record is not None, "a marker that is there still means a restore was in flight"
    assert record.readable is False
    assert record.databases == ()


def test_the_running_server_refusal_reads_as_english_for_one_container(
    tmp_path: Path,
) -> None:
    """A live run printed "ac-worldserver are running" (2026-08-23).

    This is the refusal a user is most likely to actually read, because it is
    the one standing between them and losing every character on the server. It
    should not look broken while it is doing that.
    """
    path = a_backup_of(tmp_path, "acore_world")

    one = plan_restore(path, tmp_path, running=running(DB, WORLD))
    assert any("ac-worldserver is running" in r for r in one.refusals), one.refusals

    both = plan_restore(path, tmp_path, running=running(DB, WORLD, AUTH))
    assert any(
        "ac-worldserver, ac-authserver are running" in r for r in both.refusals
    ), both.refusals


def test_a_dump_does_not_ask_mysqldump_to_drop_the_database() -> None:
    """A restore is a merge, and this argv is the reason. Measured, not assumed.

    Without `--add-drop-database`, mysqldump writes `DROP TABLE IF EXISTS`
    before each table it carries and no `DROP DATABASE` at all, so loading the
    file replaces the tables the backup holds and leaves every table it does
    not. Live evidence, Windows 11 / Docker 29.7.2 (2026-08-23): a marker table
    created in `acore_world` after the backup was taken was still there after a
    full 306 MB restore of that schema — 313 tables where the backup had 312.

    The flag stays absent on purpose. Adding it would make the load drop the
    whole schema first, so a restore that dies part-way would leave nothing at
    all, where today it leaves a database missing only what the load had not yet
    reached — the shape `interrupted_restore()` and the pre-restore safety copy
    are both built around. This test exists so the flag cannot be added as an
    obvious improvement without meeting that argument first, and so the merge
    behaviour `restore()` documents cannot be changed silently.
    """
    argv = DockerMysql("ac-database", "pw")._dump_argv("acore_world")

    assert "--add-drop-database" not in argv, "a restore would stop being recoverable part-way"
    assert argv[:2] == ["mysqldump", "-uroot"]
    assert argv[-2:] == ["--databases", "acore_world"]
    assert "--single-transaction" in argv, "a backup must be takeable while people are playing"


def test_backup_runs_mysqldump_in_the_distro_and_announces_the_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backup shells out itself, so it needs both halves of the crossing.

    The argv has to reach the distro's docker, and `MYSQL_PWD` has to be named
    in WSLENV or `mysqldump` is handed an empty password and reports an
    authentication failure against a database that is perfectly healthy.
    """
    seen: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen["argv"] = list(argv)
        seen["env"] = kwargs.get("env") or {}
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(maintenance.subprocess, "run", fake_run)
    monkeypatch.setattr(maintenance.platform, "_which", lambda name, path=None: "wsl.exe")

    mysql = maintenance.DockerMysql("ac-database", "hunter2", wsl_distro="dml-arch")
    mysql.dump_into("acore_auth", io.BytesIO())

    argv = seen["argv"]
    assert argv[:5] == ["wsl.exe", "-d", "dml-arch", "--", "docker"]
    assert not any("hunter2" in part for part in argv), f"password in argv: {argv}"
    env = seen["env"]
    assert env.get("MYSQL_PWD") == "hunter2"
    assert "MYSQL_PWD" in env.get("WSLENV", "").split(":")
def test_a_cmangos_backup_is_not_told_its_databases_are_missing(tmp_path: Path) -> None:
    """The alarm names the schemas THIS core has, not AzerothCore's three.

    A Tortoise install dumps tw_logon/tw_char/tw_world correctly and was then
    told `!! expected but absent: acore_auth, acore_characters, acore_world` —
    a data-loss alarm on a backup that took everything (Discord report,
    2026-08-26).
    """
    mysql = FakeMysql(("tw_logon", "tw_char", "tw_world", "tw_logs"))
    report = backup(
        tmp_path,
        mysql,
        running=running(DB),
        now=AT,
        core_databases=("tw_logon", "tw_char", "tw_world"),
    )
    assert report.databases == ("tw_char", "tw_logon", "tw_logs", "tw_world")
    assert report.missing_core == ()


def test_a_cmangos_backup_still_raises_the_alarm_for_its_own_missing_schema(
    tmp_path: Path,
) -> None:
    """Per-game names must not turn the alarm off, only point it at the right schemas."""
    mysql = FakeMysql(("tw_logon", "tw_world"))
    report = backup(
        tmp_path,
        mysql,
        running=running(DB),
        now=AT,
        core_databases=("tw_logon", "tw_char", "tw_world"),
    )
    assert report.missing_core == ("tw_char",)
