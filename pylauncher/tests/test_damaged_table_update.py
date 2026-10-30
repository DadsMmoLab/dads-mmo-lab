"""T839: an update that would stop on a crashed table names it and offers Repair, before it builds.

The reported case (WoW Tortoise, 2026-10-30, 'Crest'): the update compiled for about five
minutes, then its copy of tw_logon, tw_char and tw_world failed with "The backup of
tw_char did not finish", because `character_honor_cp` was a crashed MyISAM table, and
the press rolled back. The player was never told which table, the T822 Repair press did
not appear, and every retry cost another build. Now:

* before the first fetch, the update asks the server which MyISAM/Aria tables of the
  databases it will copy are damaged (`CHECK TABLE … FAST QUICK`, read-only, bounded),
  and refuses in plain words naming the table;
* when that check cannot run, the copy's own failure is the backstop, with the same
  sentence and ONE rollback;
* either refusal carries the tables, and the Server tab's Repair banner offers them.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import IO

import pytest

from tests.support_native import FakeSnapshot
from tests.test_install_wiring import _census
from tests.test_update_rollback_database import _old_build_comes_back, _press
from tests.test_update_to_latest import _gated  # noqa: F401 - an autouse fixture
from yulon import install_wiring, table_repair
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError
from yulon.controller_wow_wotlk import docker_ctl
from yulon.controller_wow_wotlk import maintenance as wotlk_maintenance
from yulon.table_repair import CrashedTable

TORTOISE = load_catalog().get("wow-tortoise")
WOTLK = load_catalog().get("wow-wotlk")
COPIED = ("tw_logon", "tw_char", "tw_world")
HONOR = CrashedTable("tw_char", "character_honor_cp")

MYSQLDUMP_1194 = (
    "mysqldump: Got error: 1194: \"Table 'character_honor_cp' is marked as crashed and "
    'should be repaired" when using LOCK TABLES'
)
"""mysqldump's own words for the reported table (MariaDB 10.6)."""

THE_SENTENCE = (
    "Table tw_char.character_honor_cp is damaged (a crash left it half-written), so a "
    "complete copy of your databases cannot be made and the update did not start. Use "
    "Repair the damaged table… on the server card, then update again. Nothing was changed."
)


# -- the parser on a dump's words -----------------------------------------------


def test_the_reported_mysqldump_error_names_tw_char_character_honor_cp() -> None:
    """A bare name in a dump of tw_char is tw_char's table, never the first schema's."""
    assert table_repair.damaged_in_dump(MYSQLDUMP_1194, "tw_char", COPIED) == (HONOR,)


def test_the_144_and_145_variants_are_read_too() -> None:
    said_144 = (
        "mysqldump: Got error: 144: Table './tw_char/characters' is marked as crashed and "
        "last (automatic?) repair failed when using LOCK TABLES"
    )
    said_145 = (
        "mysqldump: Got error: 145: Table './tw_world/creature' is marked as crashed and "
        "should be repaired when using LOCK TABLES"
    )
    assert table_repair.damaged_in_dump(said_144, "tw_char", COPIED) == (
        CrashedTable("tw_char", "characters"),
    )
    assert table_repair.damaged_in_dump(said_145, "tw_world", COPIED) == (
        CrashedTable("tw_world", "creature"),
    )


def test_a_dump_that_failed_for_another_reason_names_nothing() -> None:
    said = "mysqldump: Got error: 2013: Lost connection to server during query"
    assert table_repair.damaged_in_dump(said, "tw_char", COPIED) == ()


def test_the_sentence_names_the_table_and_the_press() -> None:
    assert (
        table_repair.damaged_refusal((HONOR,), repairable=("tw_char", "tw_logon")) == THE_SENTENCE
    )


# -- the copy the update takes (the real `backup()`) -----------------------------


class _DumpServer:
    """A MariaDB that dumps every schema but fails `failing` with mysqldump's words."""

    def __init__(self, failing: str = "", said: str = MYSQLDUMP_1194) -> None:
        self.failing = failing
        self.said = said

    def databases(self) -> tuple[str, ...]:
        return COPIED

    def dump_into(self, database: str, sink: IO[bytes], ignore: Sequence[str] = ()) -> None:
        if database == self.failing:
            raise wotlk_maintenance.MaintenanceError(
                f"The backup of {database} did not finish.", detail=self.said
            )
        sink.write(
            b"-- MariaDB dump 10.19\n"
            + f"CREATE DATABASE `{database}`;\nUSE `{database}`;\n".encode()
            + b"-- Dump completed on 2026-10-30 15:13:59\n"
        )

    def load_from(self, source: IO[bytes]) -> None:  # pragma: no cover - never a copy's job
        raise AssertionError("a copy loads nothing")


def _copy_on(
    monkeypatch: pytest.MonkeyPatch, server: object, entry: object = TORTOISE
) -> install_wiring.DatabaseSnapshot:
    _census(monkeypatch, up=True)
    db = entry.container_spec().db  # type: ignore[attr-defined]
    monkeypatch.setattr(docker_ctl, "status", lambda *, wsl_distro=None: [db])
    monkeypatch.setattr(install_wiring.docker, "status", lambda *, wsl_distro=None: [db])
    monkeypatch.setattr(
        install_wiring._MaintenanceSnapshot, "_mysql", lambda self, server_dir: server
    )
    copy = install_wiring.database_snapshot_for(entry)  # type: ignore[arg-type]
    assert copy is not None
    return copy


def test_a_copy_that_fails_on_a_crashed_table_carries_the_table(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Mutation: drop `database=` in `backup()`'s re-raise (the bare name lands in tw_logon)."""
    copy = _copy_on(monkeypatch, _DumpServer(failing="tw_char"))
    with pytest.raises(InstallerError) as raised:
        copy.take(tmp_path, COPIED)
    assert table_repair.damaged_in(raised.value) == (HONOR,)
    assert "the backup is INCOMPLETE" in str(raised.value)


def test_a_copy_that_fails_for_another_reason_carries_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    copy = _copy_on(monkeypatch, _DumpServer(failing="tw_char", said="Lost connection"))
    with pytest.raises(InstallerError) as raised:
        copy.take(tmp_path, COPIED)
    assert table_repair.damaged_in(raised.value) == ()


def test_azerothcore_copies_are_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Mutation: `checks_damage=True` for every family."""

    class _Wotlk(_DumpServer):
        def databases(self) -> tuple[str, ...]:
            return ("acore_characters",)

    said = MYSQLDUMP_1194.replace("character_honor_cp", "characters")
    copy = _copy_on(monkeypatch, _Wotlk(failing="acore_characters", said=said), WOTLK)
    with pytest.raises(InstallerError) as raised:
        copy.take(tmp_path, ("acore_characters",))
    assert table_repair.damaged_in(raised.value) == ()
    assert copy.damaged_tables(tmp_path, ("acore_characters",)) is None


# -- the check before the build --------------------------------------------------


class _Checker:
    """`DockerMysql.query` for the two reads `find_damaged()` makes; records every statement."""

    def __init__(self, listed: str, checked: str) -> None:
        self.listed = listed
        self.checked = checked
        self.statements: list[str] = []

    def __call__(self, sql: str) -> str:
        self.statements.append(sql)
        if sql.startswith("SELECT TABLE_SCHEMA, TABLE_NAME FROM information_schema.TABLES"):
            return self.listed
        if "CHECK TABLE" in sql:
            return self.checked
        raise AssertionError(f"unexpected statement: {sql}")


LISTED = "tw_char\tcharacters\ntw_char\tcharacter_honor_cp\ntw_world\tcreature\n"


def test_the_check_names_the_crashed_table_and_only_reads() -> None:
    """Mutation: count a warning row as damage (the running server's tables would refuse)."""
    checker = _Checker(
        LISTED,
        "tw_char.characters\tcheck\twarning\t1 client is using or hasn't closed the table "
        "properly\ntw_char.characters\tcheck\tstatus\tOK\n"
        "tw_char.character_honor_cp\tcheck\twarning\tTable is marked as crashed\n"
        "tw_char.character_honor_cp\tcheck\terror\tCorrupt\n"
        "tw_world.creature\tcheck\tstatus\tTable is already up to date\n",
    )
    assert table_repair.find_damaged(checker, COPIED) == (HONOR,)
    check = checker.statements[-1]
    assert "FAST QUICK" in check and "max_statement_time" in check
    assert "`tw_char`.`character_honor_cp`" in check
    for statement in checker.statements:
        assert statement.lstrip().upper().startswith(("SELECT", "SET SESSION")), statement


def test_a_clean_check_finds_nothing() -> None:
    checker = _Checker(
        LISTED,
        "tw_char.characters\tcheck\tstatus\tOK\n"
        "tw_char.character_honor_cp\tcheck\tstatus\tOK\n"
        "tw_world.creature\tcheck\tstatus\tOK\n",
    )
    assert table_repair.find_damaged(checker, COPIED) == ()


def test_a_check_of_no_myisam_table_sends_no_check() -> None:
    checker = _Checker("", "")
    assert table_repair.find_damaged(checker, COPIED) == ()
    assert len(checker.statements) == 1


def test_the_snapshot_checks_through_the_running_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class _Server(_DumpServer):
        def __init__(self) -> None:
            super().__init__()
            self.checker = _Checker(LISTED, "tw_char.character_honor_cp\tcheck\terror\tCorrupt\n")

        def query(self, sql: str) -> str:
            return self.checker(sql)

    copy = _copy_on(monkeypatch, _Server())
    assert copy.damaged_tables(tmp_path, COPIED) == (HONOR,)


def test_a_stopped_database_is_not_checked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    copy = _copy_on(monkeypatch, _DumpServer())
    monkeypatch.setattr(install_wiring.docker, "status", lambda *, wsl_distro=None: [])
    assert copy.damaged_tables(tmp_path, COPIED) is None


# -- the update press -------------------------------------------------------------


def _press_with(
    tmp_path: Path, make: Callable[..., FakeSnapshot]
) -> tuple[list[str], FakeSnapshot, list[str], Exception | None]:
    rec, _dir, _made, fake, said, raised = _press(
        tmp_path, TORTOISE, copy=make, wait_ready=_old_build_comes_back()
    )
    return rec.calls, fake, said, raised


def test_a_crashed_table_found_before_the_build_refuses_before_any_build_step(
    tmp_path: Path,
) -> None:
    """Mutation: drop the `_refuse_a_damaged_table()` call (the press builds, then fails)."""
    calls, fake, _said, raised = _press_with(
        tmp_path, lambda rec: FakeSnapshot(rec, damaged=(HONOR,))
    )
    assert raised is not None
    assert str(raised) == THE_SENTENCE
    assert table_repair.damaged_in(raised) == (HONOR,)
    assert fake.damage_checks == [COPIED]
    started = ("clone:", "build", "stop_servers", "snapshot:", "recreate", "restore:", "tag:")
    assert not [call for call in calls if call.startswith(started)], calls


def test_a_clean_check_says_so_and_the_update_goes_on(tmp_path: Path) -> None:
    calls, fake, said, _raised = _press_with(tmp_path, lambda rec: FakeSnapshot(rec, damaged=()))
    assert native.damaged_checked_line(COPIED) in said
    assert fake.taken, "the copy was still taken"
    assert any(call.startswith("clone:") for call in calls)


def test_a_check_that_did_not_run_leaves_the_press_as_it_was(tmp_path: Path) -> None:
    calls, fake, said, _raised = _press_with(tmp_path, FakeSnapshot)
    assert native.damaged_checked_line(COPIED) not in said
    assert fake.damage_checks == [COPIED]
    assert any(call.startswith("clone:") for call in calls)


def test_the_copy_failing_on_a_crashed_table_names_it_and_rolls_back_once(
    tmp_path: Path,
) -> None:
    """The backstop. Mutation: drop the `damaged_in()` branch in `_take_copy()`."""
    refused = table_repair.mark_damaged(
        InstallerError("The backup of tw_char did not finish."), (HONOR,)
    )
    calls, fake, _said, raised = _press_with(
        tmp_path, lambda rec: FakeSnapshot(rec, take_error=refused)
    )
    assert raised is not None
    text = str(raised)
    assert text.startswith(
        "Table tw_char.character_honor_cp is damaged (a crash left it half-written), so a "
        "complete copy of your databases cannot be made and the update did not start. Use "
        "Repair the damaged table… on the server card, then update again."
    ), text
    assert "did not finish" not in text
    assert table_repair.damaged_in(raised) == (HONOR,)
    # ONE rollback: the old build's recreate, and the new build never started.
    assert calls.count("recreate") == 1, calls
    assert fake.put_back_calls == []


def test_a_copy_failing_for_another_reason_keeps_the_old_sentence(tmp_path: Path) -> None:
    calls, _fake, _said, raised = _press_with(
        tmp_path, lambda rec: FakeSnapshot(rec, take_error=InstallerError("disk full"))
    )
    assert raised is not None
    text = str(raised)
    assert (
        "could not copy tw_logon, tw_char and tw_world before starting the new build (disk "
        "full), so it did not start it" in text
    )
    assert table_repair.damaged_in(raised) == ()
    assert calls.count("recreate") == 1, calls
