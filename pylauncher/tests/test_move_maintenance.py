"""T601: the three things the move needs of the shared maintenance engine.

A dump that leaves one table out (the auth dump without `realmlist`, which belongs to
the server the package lands on), a way to ask the database a question and read the
answer, and a public reader for the game record in the head of a file already in hand.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path
from typing import IO

import pytest

from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import DockerMysql, Game, MaintenanceError

DB = "ac-database"
WOTLK = Game("wow-wotlk", "WoW WotLK")
AT = datetime(2026, 10, 9, 12, 0, 0)

DUMP = (
    b"-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)\n--\n"
    b"USE `{db}`;\nINSERT INTO `t` VALUES (1);\n-- Dump completed on 2026-10-09 12:00:00\n"
)


def test_a_dump_can_leave_named_tables_out() -> None:
    argv = DockerMysql(DB, "pw")._dump_argv("acore_auth", ignore=("realmlist", "logs"))
    assert "--ignore-table=acore_auth.realmlist" in argv
    assert "--ignore-table=acore_auth.logs" in argv
    assert argv[-2:] == ["--databases", "acore_auth"]


def test_a_dump_leaves_nothing_out_by_default() -> None:
    argv = DockerMysql(DB, "pw")._dump_argv("acore_auth")
    assert not [a for a in argv if a.startswith("--ignore-table")]


class Recording:
    """A database container that records what each dump was asked to leave out."""

    def __init__(self, present: tuple[str, ...]) -> None:
        self.present = present
        self.asked: dict[str, tuple[str, ...] | None] = {}

    def databases(self) -> tuple[str, ...]:
        return self.present

    def dump_into(self, database: str, sink: IO[bytes], ignore: tuple[str, ...] = ()) -> None:
        self.asked[database] = ignore or None
        sink.write(DUMP.replace(b"{db}", database.encode()))

    def load_from(self, source: IO[bytes]) -> None:  # pragma: no cover - not used
        raise AssertionError


def test_backup_hands_each_database_only_its_own_ignore_list(tmp_path: Path) -> None:
    mysql = Recording(("acore_auth", "acore_characters"))
    maintenance.backup(
        tmp_path,
        mysql,
        game=WOTLK,
        ignore_tables={"acore_auth": ("realmlist",)},
        running=lambda: [DB],
        now=AT,
    )
    assert mysql.asked == {"acore_auth": ("realmlist",), "acore_characters": None}


def test_backup_without_an_ignore_list_calls_the_old_two_argument_dump(tmp_path: Path) -> None:
    """A container written before this (a double, a fork) takes no `ignore` and must still work."""

    class Old:
        def databases(self) -> tuple[str, ...]:
            return ("acore_auth",)

        def dump_into(self, database: str, sink: IO[bytes]) -> None:
            sink.write(DUMP.replace(b"{db}", database.encode()))

        def load_from(self, source: IO[bytes]) -> None:  # pragma: no cover
            raise AssertionError

    report = maintenance.backup(tmp_path, Old(), game=WOTLK, running=lambda: [DB], now=AT)
    assert len(report.dumps) == 1


def test_a_query_sends_the_statement_over_stdin_and_returns_the_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        seen["argv"] = argv
        seen["input"] = kw.get("input")
        return subprocess.CompletedProcess(argv, 0, b"3\t4\n", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    out = DockerMysql(DB, "hunter2").query("SELECT 3, 4;")
    assert out == "3\t4\n"
    assert seen["input"] == b"SELECT 3, 4;\n"
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert "--batch" in argv and "--skip-column-names" in argv
    assert "SELECT" not in " ".join(argv), "the statement goes over stdin, not argv"
    assert "hunter2" not in " ".join(argv)


def test_a_query_that_fails_is_a_maintenance_error_with_the_database_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(argv, 1, b"", b"ERROR 1146 no such table")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(MaintenanceError, match="ERROR 1146"):
        DockerMysql(DB, "hunter2").query("SELECT 1 FROM nope;")


def test_the_game_record_of_a_head_in_hand_reads_without_a_path() -> None:
    head = b"-- yulon-backup: game=wow-wotlk\n-- MySQL dump 10.13\n"
    assert maintenance.game_recorded_in(head, "x.sql") == "wow-wotlk"
    assert maintenance.game_recorded_in(b"-- MySQL dump 10.13\n", "x.sql") is None


def test_a_garbled_record_in_a_head_in_hand_is_an_error_not_no_record() -> None:
    head = b"-- yulon-backup: game=Wow WotLK!\n-- MySQL dump 10.13\n"
    with pytest.raises(MaintenanceError, match="cannot be read"):
        maintenance.game_recorded_in(head, "x.sql")
