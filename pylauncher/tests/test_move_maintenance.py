"""T601: the three things the move needs of the shared maintenance engine.

A dump that leaves one table out (the auth dump without `realmlist`, which belongs to
the server the package lands on), a way to ask the database a question and read the
answer, and a public reader for the game record in the head of a file already in hand.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from pathlib import Path
from typing import IO

import pytest

from yulon import platform
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


# -- the read lock a file copy is taken under (T822) -----------------------


class _Stdin:
    def __init__(self) -> None:
        self.written = b""
        self.closed = False

    def write(self, data: bytes) -> int:
        self.written += data
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _Client:
    """A database client process that answers what it is given from `lines`."""

    def __init__(self, lines: list[bytes], *, returncode: int = 0) -> None:
        self.stdin = _Stdin()
        self.stdout = iter(lines)
        self.returncode = returncode
        self.killed = False

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def kill(self) -> None:
        self.killed = True


class _Silent:
    """A client that never prints and never ends, until the test lets it."""

    def __init__(self) -> None:
        self.ended = threading.Event()
        self.stdin = _Stdin()
        self.stdout = self
        self.returncode = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None

    def __iter__(self) -> _Silent:
        return self

    def __next__(self) -> bytes:
        self.ended.wait(5)
        raise StopIteration


def _locking(monkeypatch: pytest.MonkeyPatch, *clients: _Client | _Silent) -> list[list[str]]:
    """Hand the calls to Popen these clients in order, and return the argvs it saw."""
    remaining = list(clients)
    argvs: list[list[str]] = []

    def fake_popen(argv: list[str], **kwargs: object) -> object:
        argvs.append(list(argv))
        return remaining.pop(0)

    monkeypatch.setattr(maintenance, "mysql_client", lambda *_a, **_k: "mariadb")
    monkeypatch.setattr(platform, "docker_prefix", lambda *_a, **_k: ["docker"])
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    return argvs


def test_a_read_lock_keeps_one_client_until_the_block_ends_then_unlocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: take the lock with `execute()`; its session ends and the lock is gone."""
    client = _Client([b"yulon-table-locked\n"])
    argvs = _locking(monkeypatch, client)
    client_stdin = client.stdin
    with DockerMysql(DB, "hunter2").read_lock(
        "FLUSH TABLES `tw_char`.`characters` WITH READ LOCK;"
    ):
        assert b"FLUSH TABLES `tw_char`.`characters` WITH READ LOCK;" in client_stdin.written
        assert not client_stdin.closed, "the session must stay open while the table is held"
    assert client_stdin.written.endswith(b"\nUNLOCK TABLES;\n")
    assert client_stdin.closed
    assert "--unbuffered" in argvs[0]
    assert "hunter2" not in " ".join(argvs[0])


def test_a_refused_lock_falls_back_to_the_next_statement_in_a_new_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Batch clients abort on the first error, so a fallback cannot share the session."""
    refused = _Client(
        [b"ERROR 1194 (HY000): Table 'characters' is marked as crashed\n"], returncode=1
    )
    granted = _Client([b"yulon-table-locked\n"])
    argvs = _locking(monkeypatch, refused, granted)
    with DockerMysql(DB, "pw").read_lock(
        "FLUSH TABLES `tw_char`.`characters` WITH READ LOCK;",
        "LOCK TABLES `tw_char`.`characters` READ;",
    ):
        pass
    assert len(argvs) == 2
    assert b"LOCK TABLES `tw_char`.`characters` READ;" in granted.stdin.written
    assert refused.stdin.closed


def test_a_lock_no_statement_can_take_raises_with_the_clients_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _Client(
        [b"ERROR 1194 (HY000): Table 'characters' is marked as crashed\n"], returncode=1
    )
    second = _Client([b"ERROR 1044 (42000): Access denied\n"], returncode=1)
    _locking(monkeypatch, first, second)
    with pytest.raises(MaintenanceError) as caught:
        with DockerMysql(DB, "pw").read_lock(
            "FLUSH TABLES t WITH READ LOCK;", "LOCK TABLES t READ;"
        ):
            raise AssertionError("the block must not be entered")
    assert "Access denied" in caught.value.detail


def test_a_lock_that_is_never_answered_is_reported_not_waited_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    silent = _Silent()
    _locking(monkeypatch, silent)
    monkeypatch.setattr(maintenance, "LOCK_ACQUIRE_S", 0.05)
    monkeypatch.setattr(maintenance, "LOCK_RELEASE_S", 0.05)
    try:
        with pytest.raises(MaintenanceError) as caught:
            with DockerMysql(DB, "pw").read_lock("FLUSH TABLES t WITH READ LOCK;"):
                raise AssertionError("the block must not be entered")
    finally:
        silent.ended.set()
    assert "no answer" in caught.value.detail


def test_an_exception_inside_the_block_still_unlocks(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _Client([b"yulon-table-locked\n"])
    _locking(monkeypatch, client)
    with pytest.raises(RuntimeError):
        with DockerMysql(DB, "pw").read_lock("LOCK TABLES t READ;"):
            raise RuntimeError("the copy went wrong")
    assert client.stdin.closed
    assert b"UNLOCK TABLES" in client.stdin.written
