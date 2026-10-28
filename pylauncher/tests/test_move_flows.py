"""T601 level 1: packing a server's accounts and characters, and bringing them into another.

The REAL maintenance engine runs here (backup, plan_restore, restore, the marker, the
safety copies); only the database container, Docker and the controller are doubles. So
the order of things (stop, dump, start; backup, then load, then fix-ups) is the engine's
order, and the guards are the flow's own.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import IO

import pytest

from tests.test_restore_load_pid import _marker_with
from yulon import docker, move, move_flows
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import MaintenanceError
from yulon.move import Counts, DumpFile, Evidence, Header
from yulon.move_flows import MoveWorld
from yulon.ownership import Ownership
from yulon.server_build_presses import UPDATE_TO_LATEST, under_server_build

CATALOG = load_catalog()
AT = datetime(2026, 10, 9, 15, 30, 0)


def entry(game_id: str) -> CatalogEntry:
    return CATALOG.get(game_id)


def dump_text(database: str, *, record: str | None = "wow-wotlk") -> bytes:
    if record is None:
        lead = b""
    elif record.startswith("--"):
        lead = record.encode() + b"\n"  # a hand-written line, whatever it says
    else:
        lead = f"-- yulon-backup: game={record}\n".encode()
    return (
        lead
        + b"-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)\n--\n"
        + f"CREATE DATABASE /*!32312 IF NOT EXISTS*/ `{database}`;\nUSE `{database}`;\n".encode()
        + b"DROP TABLE IF EXISTS `t`;\nCREATE TABLE `t` (\n  `a` int\n);\n"
        + b"INSERT INTO `t` VALUES (1);\n-- Dump completed on 2026-10-09 15:30:00\n"
    )


class Db:
    """The database container: schemas, scripted answers to the move's questions, a log."""

    def __init__(
        self,
        events: list[str],
        present: tuple[str, ...] = ("acore_auth", "acore_characters", "acore_world"),
        *,
        updates: tuple[str, ...] = ("2024_01_a", "2024_01_b"),
        counts: tuple[int, int, int, int] = (0, 0, 0, 0),
        realm: str = "Local Realm",
        version_table: str | None = "updates",
        fail_load_of: str | None = None,
        extra_tables: dict[str, dict[str, int]] | None = None,
        fail_dump_of: str | None = None,
        module_rows: dict[str, tuple[tuple[str, str], ...]] | None = None,
        updates_by_schema: dict[str, tuple[str, ...]] | None = None,
        tail_rows: dict[str, tuple[tuple[str, str, str], ...]] | None = None,
        include_rows: dict[str, tuple[str, ...]] | None = None,
        no_ledger: tuple[str, ...] = (),
        migration_hashes: tuple[str, ...] = ("hash-a", "hash-b", "hash-c"),
    ) -> None:
        self.events = events
        self.present = present
        self.updates = updates
        self.counts = counts
        self.realm = realm
        self.version_table = version_table
        self.fail_load_of = fail_load_of
        self.fail_dump_of = fail_dump_of
        self.module_rows = module_rows or {}  # schema -> ((name, hash), ...) of non-core rows
        self.updates_by_schema = updates_by_schema or {}
        self.tail_rows = tail_rows or {}
        self.include_rows = include_rows or {}
        self.no_ledger = no_ledger
        self.migration_hashes = migration_hashes
        self.session_columns = "session_key\tYES\n"
        self.extra_tables = extra_tables or {}  # schema -> {table: rows} the target alone has
        self.queries: list[str] = []
        self.executed: list[str] = []
        self.ignored: dict[str, tuple[str, ...]] = {}

    def databases(self) -> tuple[str, ...]:
        return self.present

    def dump_into(self, database: str, sink: IO[bytes], ignore: tuple[str, ...] = ()) -> None:
        self.events.append(f"dump:{database}")
        self.ignored[database] = ignore
        sink.write(dump_text(database, record=None))
        if self.fail_dump_of == database:
            raise MaintenanceError(f"The backup of {database} did not finish.")

    def load_from(self, source: IO[bytes]) -> None:
        body = source.read()
        used = re.search(rb"USE `([^`]+)`", body)
        assert used is not None
        name = used.group(1).decode()
        if self.fail_load_of == name:
            raise MaintenanceError("The database did not load the backup.")
        self.events.append(f"load:{name}")
        # T802: a load replaces the ledger with the dump's, so scripted answers
        # follow it. The dump holds the file's names; the updater restores the
        # target's afterwards (the `apply-updates` fake) or not (the silent one).
        found = re.findall(rb"INSERT INTO `updates` VALUES (.*?);", body, re.DOTALL)
        if found:
            names = re.findall(rb"\('([^']*)','[0-9A-Fa-f]{40}','", found[0])
            self.updates_by_schema[name] = tuple(n.decode() for n in names)
            self.tail_rows[name] = tuple(
                (n.decode(), h.decode(), s.decode())
                for n, h, s in re.findall(
                    rb"\('([^']*)','([0-9A-Fa-f]{40})','([A-Za-z_]+)',", found[0]
                )
            )

    def execute(self, sql: str) -> None:
        self.events.append("exec")
        self.executed.append(sql)

    def tables(self, database: str) -> tuple[tuple[str, str], ...]:
        own = (("t", "BASE TABLE"),) if database != "acore_playerbots" else (("t", "BASE TABLE"),)
        extra = tuple((n, "BASE TABLE") for n in self.extra_tables.get(database, {}))
        realm = (("realmlist", "BASE TABLE"),) if database == "acore_auth" else ()
        return (*own, *extra, *realm)

    def query(self, sql: str) -> str:
        self.queries.append(sql)
        rows = re.fullmatch(r"SELECT COUNT\(\*\) FROM `([^`]+)`\.`([^`]+)`;", sql)
        if rows is not None:
            schema, table = rows.groups()
            if table == "realmlist":
                return "1\n"
            return f"{self.extra_tables.get(schema, {}).get(table, 0)}\n"
        if "'session_key'" in sql and "information_schema.COLUMNS" in sql:
            return self.session_columns
        if "information_schema.TABLES" in sql:
            asked = re.search(r"TABLE_SCHEMA = '([^']+)'", sql)
            if asked is not None and asked.group(1) in self.no_ledger:
                return ""
            return f"{self.version_table}\n" if self.version_table else ""
        if "COUNT(*)" in sql:
            return "\t".join(str(n) for n in self.counts) + "\n"
        if "realmlist" in sql:
            return f"{self.realm}\n"
        if "`updates_include`" in sql:
            schema = re.search(r"`([^`]+)`\.`updates_include`", sql).group(1)  # type: ignore[union-attr]
            return "".join(
                f"{row}\n".replace("|", "\t") for row in self.include_rows.get(schema, ())
            )
        if "`migrations`" in sql:
            if "`Module`, `Hash`" in sql:
                return ""
            return "".join(f"{h}\n" for h in self.migration_hashes)
        if "`updates`" in sql:
            schema = re.search(r"`([^`]+)`\.`updates`", sql).group(1)  # type: ignore[union-attr]
            if "`name`, `hash`, `state`" in sql:
                return "".join(f"{n}\t{h}\t{s}\n" for n, h, s in self.tail_rows.get(schema, ()))
            if "NOT IN ('RELEASED'" in sql:
                return "".join(f"{n}\t{h}\n" for n, h in self.module_rows.get(schema, ()))
            names = self.updates_by_schema.get(schema, self.updates)
            return "".join(f"{name}\n" for name in names)
        raise AssertionError(f"unscripted query: {sql}")


class Box:
    """One server's doubles, wired the way the app wires them."""

    def __init__(
        self,
        tmp_path: Path,
        game_id: str = "wow-wotlk",
        *,
        running_now: tuple[str, ...] = (),
        db: Db | None = None,
        ownership: Ownership = Ownership.OWNED,
    ) -> None:
        self.entry = entry(game_id)
        self.spec = self.entry.container_spec()
        self.server_dir = tmp_path / f"server-{game_id}"
        self.server_dir.mkdir()
        self.events: list[str] = []
        self.db = db or Db(self.events)
        self.db.events = self.events
        self.up: list[str] = list(running_now)
        self.ownership = ownership
        self.stop_fails = False
        self.start_fails = False

        def running() -> list[str]:
            return list(self.up)

        def stop_server() -> bool:
            self.events.append("stop")
            if self.stop_fails:
                self.up.remove(self.spec.world)  # half-way: the world is down, the rest is not
                raise RuntimeError("docker stop failed")
            self.up.clear()
            return True

        def start_server() -> None:
            self.events.append("start")
            if self.start_fails:
                raise RuntimeError("compose could not start")
            self.up.extend([self.spec.db, self.spec.auth, self.spec.world])

        def bring_up(because: str) -> bool:
            if self.spec.db in self.up:
                return False
            self.events.append("db-up")
            self.up.append(self.spec.db)
            return True

        def take_down() -> None:
            self.events.append("db-down")
            self.up.remove(self.spec.db)

        backup, plan_restore, restore = move_flows.engine_for(
            self.entry, self.server_dir, self.db, running=running, wsl_distro=None
        )
        self.world = MoveWorld(
            entry=self.entry,
            game=maintenance.game_of(self.entry),
            server_dir=self.server_dir,
            mysql=self.db,
            backup=backup,
            plan_restore=plan_restore,
            restore=restore,
            running=running,
            ownership=lambda: self.ownership,
            stop_server=stop_server,
            start_server=start_server,
            bring_up=bring_up,
            take_down=take_down,
            channel_account="YULON_AAAA1111",
            marker=lambda: move_flows.BotMarker("RNDBOT"),
            now=lambda: AT,
        )


def packed(tmp_path: Path, *, game_id: str = "wow-wotlk", realm: str = "Source Realm") -> Path:
    """A package made by the real export, from a server of `game_id`."""
    root = tmp_path / f"source-{game_id}"
    root.mkdir(exist_ok=True)
    source = Box(
        root, game_id, running_now=(entry(game_id).container_spec().db,), db=Db([], realm=realm)
    )
    folder = tmp_path / f"out-{game_id}"
    folder.mkdir(exist_ok=True)
    return move_flows.export_package(source.world, folder, stop_allowed=False).path


# =============================================================== export


def test_export_packs_auth_and_characters_and_never_the_world(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    folder = tmp_path / "docs"
    folder.mkdir()
    result = move_flows.export_package(box.world, folder, stop_allowed=False)
    package = move.read_package(result.path)
    assert [m.schema_name for m in package.manifest.databases] == [
        "acore_auth",
        "acore_characters",
    ]
    assert result.path.name == "yulon-move-wow-wotlk-20261009-1530-keep-private.zip"
    assert result.path.parent == folder
    assert "dump:acore_world" not in box.events


def test_export_includes_the_bot_and_lua_schemas_when_the_server_has_them(tmp_path: Path) -> None:
    db = Db(
        [],
        present=("acore_auth", "acore_characters", "acore_world", "acore_playerbots", "acore_ale"),
    )
    box = Box(tmp_path, running_now=("ac-database",), db=db)
    folder = tmp_path / "docs"
    folder.mkdir()
    result = move_flows.export_package(box.world, folder, stop_allowed=False)
    assert {m.schema_name: m.role for m in result.manifest.databases} == {
        "acore_auth": "auth",
        "acore_characters": "characters",
        "acore_playerbots": "playerbots",
        "acore_ale": "ale",
    }


def test_the_auth_dump_leaves_the_realm_row_behind(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    folder = tmp_path / "docs"
    folder.mkdir()
    move_flows.export_package(box.world, folder, stop_allowed=False)
    assert box.db.ignored["acore_auth"] == ("realmlist",)
    assert box.db.ignored["acore_characters"] == ()


def test_the_manifest_carries_the_evidence_the_counts_and_the_realm(tmp_path: Path) -> None:
    db = Db([], counts=(2, 3, 500, 1000), realm="Baerthe's Realm", updates=("a", "b", "c"))
    box = Box(tmp_path, running_now=("ac-database",), db=db)
    folder = tmp_path / "docs"
    folder.mkdir()
    manifest = move_flows.export_package(box.world, folder, stop_allowed=False).manifest
    assert manifest.counts == Counts(
        accounts=2, characters=3, bot_accounts=500, bot_characters=1000
    )
    assert manifest.realm_name == "Baerthe's Realm"
    assert manifest.channel_account == "YULON_AAAA1111"
    assert manifest.bot_prefix == "RNDBOT"
    evidence = manifest.schema_evidence["acore_auth"]
    assert (evidence.kind, evidence.count) == ("updates", 3)
    assert "acore_world" not in manifest.schema_evidence
    assert "acore_world" in manifest.excluded[0]


def test_the_temporary_dumps_are_gone_and_nothing_else_in_backups_is_touched(
    tmp_path: Path,
) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    backups = box.server_dir / "sql_scripts" / "backups"
    backups.mkdir(parents=True)
    mine = backups / "20260101_000000_acore_auth.sql"
    mine.write_bytes(b"an older backup the player made")
    folder = tmp_path / "docs"
    folder.mkdir()
    move_flows.export_package(box.world, folder, stop_allowed=False)
    assert sorted(p.name for p in backups.iterdir()) == [mine.name]


def test_the_packs_dumps_never_land_where_the_backups_list_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T747: a pack killed mid-zip left `<stamp>_move_acore_auth.sql` that Delete refused."""
    from yulon import backup_shelf

    box = Box(tmp_path, running_now=("ac-database",))
    listed: list[tuple[str, ...]] = []
    packed_from: list[Path] = []
    real = move.write_package

    def watching(dest: Path, header: Header, dumps: list[DumpFile], **kw: object) -> object:
        shelf = backup_shelf.read_shelf(box.server_dir, game_id="wow-wotlk", installed={})
        listed.append(tuple(r.name for r in shelf.rows))
        packed_from.extend(d.path for d in dumps)
        return real(dest, header, dumps, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(move, "write_package", watching)
    folder = tmp_path / "docs"
    folder.mkdir()
    move_flows.export_package(box.world, folder, stop_allowed=False)
    assert listed == [()], listed
    work = move_flows.pack_folder(box.server_dir)
    assert packed_from and all(p.parent == work for p in packed_from), packed_from
    assert not work.exists(), "the working folder was left after a pack that worked"


def test_a_killed_packs_leftovers_are_cleared_when_the_next_pack_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    work = move_flows.pack_folder(box.server_dir)
    work.mkdir(parents=True)
    leftover = work / "20261001_000000_move_acore_auth.sql"
    leftover.write_bytes(b"every verifier, from a pack that was killed")
    seen: list[bool] = []
    real = move.write_package

    def watching(*a: object, **kw: object) -> object:
        seen.append(leftover.exists())
        return real(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(move, "write_package", watching)
    folder = tmp_path / "docs"
    folder.mkdir()
    move_flows.export_package(box.world, folder, stop_allowed=False)
    assert seen == [False], "the last pack's leftover was still there while this one packed"
    assert not work.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows has the folder's ACL")
def test_the_packs_working_folder_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    backups = maintenance.backups_dir(box.server_dir)
    backups.mkdir(parents=True)
    backups.chmod(0o775)  # a backups folder made before T683 S5
    modes: list[int] = []
    real = move.write_package

    def watching(*a: object, **kw: object) -> object:
        modes.append(stat.S_IMODE(move_flows.pack_folder(box.server_dir).stat().st_mode))
        return real(*a, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(move, "write_package", watching)
    folder = tmp_path / "docs"
    folder.mkdir()
    old = os.umask(0o002)
    try:
        move_flows.export_package(box.world, folder, stop_allowed=False)
    finally:
        os.umask(old)
    assert modes == [0o700], [oct(m) for m in modes]


def test_a_failed_pack_leaves_no_working_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = Box(tmp_path, running_now=("ac-database",))

    def broken(*_a: object, **_kw: object) -> object:
        raise move.MovePackageError("the disk went away")

    monkeypatch.setattr(move, "write_package", broken)
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError, match="the disk went away"):
        move_flows.export_package(box.world, folder, stop_allowed=False)
    assert not move_flows.pack_folder(box.server_dir).exists()
    backups = maintenance.backups_dir(box.server_dir)
    assert not [p.name for p in backups.iterdir()] if backups.exists() else True


def test_a_running_server_is_not_stopped_without_a_yes(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"))
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError) as raised:
        move_flows.export_package(box.world, folder, stop_allowed=False)
    assert str(raised.value) == move_flows.RUNNING_NEEDS_A_YES_EXPORT
    assert box.events == []
    assert not list(folder.iterdir())


def test_with_a_yes_the_server_is_stopped_then_packed_then_started_again(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"))
    folder = tmp_path / "docs"
    folder.mkdir()
    result = move_flows.export_package(box.world, folder, stop_allowed=True)
    assert box.events[0] == "stop"
    assert box.events[-1] == "start"
    assert box.events.index("stop") < box.events.index("dump:acore_auth")
    assert box.events.index("dump:acore_characters") < box.events.index("start")
    assert result.restarted is True


def test_the_server_is_started_again_even_when_the_packing_fails(tmp_path: Path) -> None:
    db = Db([], version_table=None)  # the version cannot be read: the export refuses
    box = Box(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"), db=db)
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError):
        move_flows.export_package(box.world, folder, stop_allowed=True)
    assert box.events[0] == "stop"
    assert box.events[-1] == "start"
    assert not list(folder.iterdir())


def test_a_start_that_fails_is_said_and_the_package_is_still_given(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"))
    box.start_fails = True
    folder = tmp_path / "docs"
    folder.mkdir()
    result = move_flows.export_package(box.world, folder, stop_allowed=True)
    assert result.path.exists()
    assert result.restarted is False
    assert "could not be started again" in " ".join(result.notes)
    assert "compose could not start" not in " ".join(result.notes)  # raw words stay in the log


def test_a_stopped_server_has_its_database_started_alone_and_put_back(tmp_path: Path) -> None:
    box = Box(tmp_path)
    folder = tmp_path / "docs"
    folder.mkdir()
    move_flows.export_package(box.world, folder, stop_allowed=False)
    assert box.events[0] == "db-up"
    assert box.events[-1] == "db-down"
    assert "start" not in box.events and "stop" not in box.events


def test_a_database_that_was_already_up_is_left_up(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    folder = tmp_path / "docs"
    folder.mkdir()
    move_flows.export_package(box.world, folder, stop_allowed=False)
    assert "db-down" not in box.events


def test_the_database_is_put_back_when_the_packing_fails(tmp_path: Path) -> None:
    box = Box(tmp_path, db=Db([], version_table=None))
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError):
        move_flows.export_package(box.world, folder, stop_allowed=False)
    assert box.events[-1] == "db-down"


def test_an_unreadable_version_refuses_the_export_in_plain_words(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",), db=Db([], version_table=None))
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError) as raised:
        move_flows.export_package(box.world, folder, stop_allowed=False)
    assert str(raised.value) == (
        "Yu'lon could not read the database version of acore_auth, so a package made from "
        "it could not be checked on the other computer. Nothing was packed."
    )


def test_a_damaged_install_record_refuses_both_directions(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",), ownership=Ownership.UNKNOWN)
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError, match="install record"):
        move_flows.export_package(box.world, folder, stop_allowed=False)


def test_an_unfinished_restore_refuses_the_export(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    marker = maintenance.marker_path(box.server_dir)
    marker.parent.mkdir(parents=True)
    marker.write_text("{}")
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError, match="did not finish"):
        move_flows.export_package(box.world, folder, stop_allowed=False)


def test_a_server_without_characters_cannot_be_packed(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",), db=Db([], present=("acore_auth",)))
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError, match="has no acore_characters"):
        move_flows.export_package(box.world, folder, stop_allowed=False)


def test_the_maintenance_lease_is_held_for_the_whole_export(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",))
    folder = tmp_path / "docs"
    folder.mkdir()
    with docker.maintenance_lease(box.server_dir, "a backup is running"):
        with pytest.raises(MaintenanceError, match="a backup is running"):
            move_flows.export_package(box.world, folder, stop_allowed=False)
    assert box.events == []


# =============================================================== import: the plan


def target(
    tmp_path: Path,
    name: str = "target",
    game_id: str = "wow-wotlk",
    *,
    db: Db | None = None,
    running_now: tuple[str, ...] = ("ac-database",),
) -> Box:
    root = tmp_path / name
    root.mkdir(exist_ok=True)
    return Box(root, game_id, running_now=running_now, db=db)


def test_a_package_for_the_same_game_and_version_is_allowed(tmp_path: Path) -> None:
    package = packed(tmp_path)
    plan = move_flows.plan_import(target(tmp_path).world, package)
    assert plan.allowed, plan.refusals
    assert plan.replaces is None
    assert plan.schemas == ("acore_auth", "acore_characters")


def test_a_package_from_another_game_is_refused_before_the_database_is_asked(
    tmp_path: Path,
) -> None:
    package = packed(tmp_path, game_id="wow-wotlk")
    box = target(tmp_path, game_id="wow-unbound")
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert plan.refusals == (
        "This file holds WoW WotLK characters; this server is WoW Unbound. "
        "Characters can only go into a server of the same game.",
    )
    assert box.db.queries == []


def make_package(
    tmp_path: Path, records: dict[str, str | None], *, manifest_game: str = "wow-wotlk"
) -> Path:
    """A package whose dump files carry exactly the given records, as a hand-made one might."""
    src = tmp_path / "hand"
    src.mkdir(exist_ok=True)
    dumps = []
    for schema, record in records.items():
        path = src / f"{schema}.sql"
        path.write_bytes(dump_text(schema, record=record))
        dumps.append(
            DumpFile(schema, "auth" if schema.endswith("auth") else "characters", path, ("t",))
        )
    evidence = {
        s: Evidence(kind="updates", count=2, digest=_digest(("2024_01_a", "2024_01_b")))
        for s in records
    }
    header = Header(
        game_id=manifest_game,
        game_name="WoW WotLK" if manifest_game == "wow-wotlk" else "WoW Unbound",
        realm_name="Old Realm",
        channel_account="YULON_BBBB2222",
        bot_prefix="RNDBOT",
        counts=Counts(accounts=1, characters=1, bot_accounts=0, bot_characters=0),
        schema_evidence=evidence,
        excluded=(),
        made=AT,
    )
    dest = tmp_path / f"hand-{len(list(tmp_path.glob('hand-*.zip')))}.zip"
    move.write_package(dest, header, dumps)
    return dest


def _digest(names: tuple[str, ...]) -> str:
    return move_flows.digest_of(names)


def test_a_dump_with_no_game_record_is_refused_stricter_than_restore(tmp_path: Path) -> None:
    package = make_package(tmp_path, {"acore_auth": "wow-wotlk", "acore_characters": None})
    plan = move_flows.plan_import(target(tmp_path).world, package)
    assert not plan.allowed
    assert plan.refusals == (move.unlabeled_dump("db/acore_characters.sql"),)
    assert "does not say which game it is from" in plan.refusals[0]


@pytest.mark.parametrize(
    "spelling",
    [
        "-- yulon-backup: game = wow-wotlk",
        "-- yulon-backup game=wow-wotlk",
        "-- Yulon-Backup: game=wow-wotlk",
        "--yulon-backup: game=wow-wotlk",
        "-- yulon-backup: game=WOW-WOTLK",
    ],
)
def test_a_nearly_right_record_is_no_record_at_all(tmp_path: Path, spelling: str) -> None:
    package = make_package(tmp_path, {"acore_auth": "wow-wotlk", "acore_characters": spelling})
    plan = move_flows.plan_import(target(tmp_path).world, package)
    assert not plan.allowed, spelling
    assert plan.refusals, spelling


def test_a_dump_from_another_game_inside_a_package_that_claims_this_one_is_refused(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path, {"acore_auth": "wow-wotlk", "acore_characters": "wow-unbound"})
    plan = move_flows.plan_import(target(tmp_path).world, package)
    assert not plan.allowed
    assert plan.refusals == (
        move.dump_from_another_game("db/acore_characters.sql", "WoW Unbound", "WoW WotLK"),
    )


def test_a_package_at_another_database_version_is_refused_and_nothing_is_changed(
    tmp_path: Path,
) -> None:
    package = packed(tmp_path)
    box = target(tmp_path, db=Db([], updates=("2024_01_a", "2024_01_b", "2024_02_new")))
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "not at the same version as this server's" in plan.refusals[0]
    assert "acore_auth: 2 updates in the file, 3 here" in plan.refusals[0]
    assert "dump:acore_auth" not in box.events


def test_a_target_whose_version_cannot_be_read_refuses(tmp_path: Path) -> None:
    package = packed(tmp_path)
    box = target(tmp_path, db=Db([], version_table=None))
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "this server's version could not be read" in plan.refusals[0]


def test_a_package_that_does_not_open_is_a_refusal_not_a_crash(tmp_path: Path) -> None:
    junk = tmp_path / "junk.zip"
    junk.write_bytes(b"nope")
    plan = move_flows.plan_import(target(tmp_path).world, junk)
    assert plan.refusals == (move.NOT_A_PACKAGE,)


def test_a_damaged_install_record_refuses_the_import(tmp_path: Path) -> None:
    package = packed(tmp_path)
    box = target(tmp_path)
    box.ownership = Ownership.UNKNOWN
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "install record" in plan.refusals[0]


# ------------------------------------------------ import: players are asked about


def test_a_target_with_players_must_be_asked_and_says_how_many(tmp_path: Path) -> None:
    package = packed(tmp_path)
    box = target(tmp_path, db=Db([], counts=(2, 3, 500, 1000)))
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed
    assert plan.replaces is not None
    assert plan.replaces.sentence == (
        "This server already has 3 characters on 2 accounts. Bringing these in REPLACES them; "
        "a copy of this server's accounts and characters is taken first. Replace?"
    )


def test_a_target_with_only_accounts_is_asked_too(tmp_path: Path) -> None:
    package = packed(tmp_path)
    box = target(tmp_path, db=Db([], counts=(1, 0, 0, 0)))
    plan = move_flows.plan_import(box.world, package)
    assert plan.replaces is not None
    assert "1 account" in plan.replaces.sentence
    assert "no characters" in plan.replaces.sentence


def test_bots_and_the_apps_own_account_are_not_players(tmp_path: Path) -> None:
    package = packed(tmp_path)
    box = target(tmp_path, db=Db([], counts=(0, 0, 500, 1000)))
    plan = move_flows.plan_import(box.world, package)
    assert plan.replaces is None
    counting = [q for q in box.db.queries if "COUNT(*)" in q]
    assert counting, "the target was counted"
    assert "RNDBOT" in counting[0], "bots are told apart by the marker prefix"
    assert "YULON_" in counting[0], "the command-channel account is not a player"


def test_a_count_that_cannot_be_read_refuses_instead_of_assuming_none(tmp_path: Path) -> None:
    class NoCount(Db):
        def query(self, sql: str) -> str:
            if "COUNT(*)" in sql:
                raise MaintenanceError("the query failed: ERROR 1146")
            return super().query(sql)

    package = packed(tmp_path)
    box = target(tmp_path, db=NoCount([]))
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert (
        "could not tell whether this server already has accounts or characters" in plan.refusals[0]
    )


def test_a_running_target_is_planned_without_a_database_start(tmp_path: Path) -> None:
    package = packed(tmp_path)
    box = target(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"))
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed
    assert plan.server_running is True
    assert "db-up" not in box.events


def test_planning_puts_a_database_it_started_back(tmp_path: Path) -> None:
    package = packed(tmp_path)
    box = target(tmp_path, running_now=())
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed
    assert box.events == ["db-up", "db-down"]


# =============================================================== import: the run


def ready(tmp_path: Path, **kw: object) -> tuple[Box, Path, move_flows.ImportPlan]:
    package = packed(tmp_path)
    box = target(tmp_path, **kw)  # type: ignore[arg-type]
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    box.events.clear()
    return box, package, plan


def test_the_run_takes_its_copy_first_then_loads_auth_then_characters(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    result = move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    events = [e for e in box.events if e.startswith(("dump:", "load:"))]
    first_load = events.index("load:acore_auth")
    assert events[:first_load] and all(e.startswith("dump:") for e in events[:first_load])
    assert events.index("load:acore_auth") < events.index("load:acore_characters")
    assert "load:acore_world" not in events
    assert result.schemas == ("acore_auth", "acore_characters")


def test_the_copy_before_the_move_is_named_in_the_result(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    result = move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert result.copies
    assert all("before-move" in p.name for p in result.copies)
    assert all(p.exists() for p in result.copies)
    assert "before-move" in result.text()


def test_the_extracted_dumps_do_not_stay_behind(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    backups = maintenance.backups_dir(box.server_dir)
    assert not [p for p in backups.iterdir() if p.is_dir()]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes; Windows has the folder's ACL")
def test_the_folder_a_bring_in_unpacks_into_is_owner_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unpacked auth dump holds every verifier: the folder is 0700 whatever backups/ is."""
    box, _package, plan = ready(tmp_path)
    backups = maintenance.backups_dir(box.server_dir)
    backups.mkdir(parents=True, exist_ok=True)
    backups.chmod(0o775)  # a backups folder made before T683 S5
    seen: list[int] = []
    real = move_flows._checked_plans

    def recording(world: object, package: object, schemas: object, folder: Path) -> object:
        seen.append(stat.S_IMODE(folder.stat().st_mode))
        return real(world, package, schemas, folder)  # type: ignore[arg-type]

    monkeypatch.setattr(move_flows, "_checked_plans", recording)
    old = os.umask(0o002)
    try:
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    finally:
        os.umask(old)
    assert seen == [0o700], [oct(m) for m in seen]


def test_replacing_players_needs_the_plans_own_token(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path, db=Db([], counts=(2, 3, 500, 1000)))
    for wrong in (None, "", "yes", "True", plan.token[::-1]):
        with pytest.raises(MaintenanceError) as raised:
            move_flows.run_import(
                box.world, plan, confirm=wrong, use_old_realm_name=False, stop_allowed=False
            )
        assert str(raised.value) == move_flows.REPLACE_NEEDS_A_YES
    assert not [e for e in box.events if e.startswith("load:")]


def test_with_the_token_the_players_are_replaced_and_the_copy_exists_before_the_first_load(
    tmp_path: Path,
) -> None:
    box, _package, plan = ready(tmp_path, db=Db([], counts=(2, 3, 500, 1000)))
    result = move_flows.run_import(
        box.world, plan, confirm=plan.token, use_old_realm_name=False, stop_allowed=False
    )
    loads = [i for i, e in enumerate(box.events) if e.startswith("load:")]
    dumps = [i for i, e in enumerate(box.events) if e.startswith("dump:")]
    assert loads and dumps and max(dumps[:2]) < min(loads)
    assert result.schemas == ("acore_auth", "acore_characters")


def test_the_target_gaining_players_since_the_plan_refuses(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    assert plan.replaces is None
    box.db.counts = (1, 1, 0, 0)  # somebody made a character after the plan was shown
    with pytest.raises(MaintenanceError) as raised:
        move_flows.run_import(
            box.world, plan, confirm=plan.token, use_old_realm_name=False, stop_allowed=False
        )
    assert str(raised.value) == move_flows.CHANGED_SINCE_THE_PLAN
    assert not [e for e in box.events if e.startswith("load:")]


def test_a_package_replaced_since_the_plan_refuses(tmp_path: Path) -> None:
    box, package, plan = ready(tmp_path)
    other = make_package(tmp_path, {"acore_auth": "wow-wotlk", "acore_characters": "wow-wotlk"})
    other.replace(package)
    with pytest.raises(MaintenanceError) as raised:
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert str(raised.value) == move_flows.CHANGED_SINCE_THE_PLAN
    assert not [e for e in box.events if e.startswith("load:")]


def test_a_refused_plan_cannot_be_run(tmp_path: Path) -> None:
    package = packed(tmp_path, game_id="wow-wotlk")
    box = target(tmp_path, game_id="wow-unbound")
    plan = move_flows.plan_import(box.world, package)
    with pytest.raises(MaintenanceError, match="same game"):
        move_flows.run_import(
            box.world, plan, confirm=plan.token, use_old_realm_name=False, stop_allowed=True
        )
    assert box.events == []


def test_a_running_server_is_not_stopped_for_an_import_without_a_yes(tmp_path: Path) -> None:
    box, _package, plan = ready(
        tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver")
    )
    with pytest.raises(MaintenanceError) as raised:
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert str(raised.value) == move_flows.RUNNING_NEEDS_A_YES_IMPORT
    assert box.events == []


def test_with_a_yes_the_server_is_stopped_and_left_stopped(tmp_path: Path) -> None:
    box, _package, plan = ready(
        tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver")
    )
    result = move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=True
    )
    assert box.events[0] == "stop"
    assert "start" not in box.events
    assert "Start the server" in result.text()


def test_a_load_that_fails_names_what_was_loaded_and_where_the_copies_are(
    tmp_path: Path,
) -> None:
    box, _package, plan = ready(tmp_path, db=Db([], fail_load_of="acore_characters"))
    with pytest.raises(MaintenanceError) as raised:
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    said = str(raised.value)
    assert "acore_auth was brought in" in said
    assert "acore_characters was not" in said
    assert "before-move" in said
    backups = maintenance.backups_dir(box.server_dir)
    assert not [p for p in backups.iterdir() if p.is_dir()], "the extracted dumps are cleaned up"
    assert maintenance.marker_path(box.server_dir).exists(), "the engine's marker stays"


# ------------------------------------------------ import: the fix-ups


def test_the_old_command_channel_accounts_are_deleted_before_the_account_table_is_touched(
    tmp_path: Path,
) -> None:
    box, _package, plan = ready(tmp_path)
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    sql = "\n".join(box.db.executed)
    assert "DELETE FROM `acore_auth`.`account_access`" in sql
    assert "DELETE FROM `acore_auth`.`account`" in sql
    assert sql.index("account_access") < sql.index("DELETE FROM `acore_auth`.`account`")
    assert "YULON_AAAA1111" in sql
    assert "REGEXP" in sql


def test_session_keys_are_cleared_with_the_column_each_core_uses(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert "UPDATE `acore_auth`.`account` SET `session_key` = NULL" in "\n".join(box.db.executed)


@pytest.mark.parametrize(
    ("game_id", "columns", "sql"),
    [
        (
            "wow-wotlk",
            [("session_key", True)],
            "UPDATE `acore_auth`.`account` SET `session_key` = NULL;",
        ),
        ("wow-tbc", [("sessionkey", False)], "UPDATE `realmd`.`account` SET `sessionkey` = '';"),
        (
            "wow-centurion",
            [("session_key_auth", True)],
            "UPDATE `centurion_auth`.`account` SET `session_key_auth` = NULL;",
        ),
        ("wow-wotlk", [], ""),
    ],
)
def test_session_key_columns_are_cleared_as_the_database_has_them(
    game_id: str, columns: list[tuple[str, bool]], sql: str
) -> None:
    assert move_flows.session_key_sql(entry(game_id), columns) == sql


def test_a_server_whose_account_table_has_no_session_key_column_gets_no_update(
    tmp_path: Path,
) -> None:
    box, _package, plan = ready(tmp_path)
    box.db.session_columns = ""
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert "session_key" not in "\n".join(box.db.executed)


def test_the_realm_name_stays_this_servers_by_default(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert "SET `name`" not in "\n".join(box.db.executed)


def test_the_old_realm_name_comes_over_only_when_asked_for(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=True, stop_allowed=False
    )
    assert (
        "UPDATE `acore_auth`.`realmlist` SET `name` = 'Source Realm' WHERE `id` = 1"
        in "\n".join(box.db.executed)
    )


def test_a_realm_name_with_a_quote_cannot_break_out_of_the_statement() -> None:
    sql = move_flows.realm_name_sql(entry("wow-wotlk"), "Bob's \\ Realm")
    assert sql == "UPDATE `acore_auth`.`realmlist` SET `name` = 'Bob''s \\\\ Realm' WHERE `id` = 1;"


def test_the_result_says_what_stayed_behind_and_what_to_do_next(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    text = move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    ).text()
    assert "world database" in text
    assert "GM levels" in text
    assert "Start the server" in text
    assert "Repair" in text  # the command channel is repaired from the Server tab


def test_the_database_is_put_back_after_an_import_on_a_stopped_server(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path, running_now=())
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert box.events[0] == "db-up"
    assert box.events[-1] == "db-down"


def test_the_lease_refuses_an_import_beside_a_backup(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    with docker.maintenance_lease(box.server_dir, "a backup is running"):
        with pytest.raises(MaintenanceError, match="a backup is running"):
            move_flows.run_import(
                box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
            )
    assert not [e for e in box.events if e.startswith("load:")]


@pytest.fixture
def reservations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The fake docker CLI with the cross-process reservation on, as test_controller_reservation."""
    from tests.support_fake_docker import end_fake_containers, lay_fake_docker
    from yulon import platform

    cli, state = lay_fake_docker(tmp_path / "docker")
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text("yulon.local/wotlk-server:native\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


def _reserving(box: Box) -> MoveWorld:
    """`box.world` as the app wires it since the lead's 2026-10-09 decision: with its spec."""
    from dataclasses import replace

    return replace(box.world, spec=box.entry.container_spec(), wsl_distro=None)


def test_a_bring_in_while_another_yulon_holds_the_server_loads_nothing(
    tmp_path: Path, reservations: Path
) -> None:
    """B's bring-in is refused while A holds the server; nothing is dumped, loaded or started.

    Mutation this catches: `_database_session()` taking the hold without `spec=` again
    (this process only), which lets the load run under the other Yu'lon's job.
    """
    from tests.test_controller_reservation import _holds

    box, _package, plan = ready(tmp_path)
    theirs = _holds(reservations, box.server_dir, press="Update the server to latest…")
    try:
        with pytest.raises(MaintenanceError, match="Another Yu'lon is working on"):
            move_flows.run_import(
                _reserving(box), plan, confirm=None, use_old_realm_name=False, stop_allowed=False
            )
    finally:
        theirs.kill()
    assert not [e for e in box.events if e.startswith(("load:", "dump:", "db-up"))]


def test_a_pack_while_another_yulon_holds_the_server_packs_nothing(
    tmp_path: Path, reservations: Path
) -> None:
    from tests.test_controller_reservation import _holds

    box = target(tmp_path)
    folder = tmp_path / "docs"
    folder.mkdir()
    theirs = _holds(reservations, box.server_dir, press="Rebuild the server…")
    try:
        with pytest.raises(MaintenanceError, match="Another Yu'lon is working on"):
            move_flows.export_package(_reserving(box), folder, stop_allowed=False)
    finally:
        theirs.kill()
    assert list(folder.iterdir()) == []
    assert not [e for e in box.events if e.startswith(("dump:", "db-up"))]


def test_a_move_names_its_press_to_another_yulon(tmp_path: Path, reservations: Path) -> None:
    """While B brings a move in, A's refusal quotes "Bring in a move"."""
    box, _package, plan = ready(tmp_path)
    seen: list[str | None] = []
    real = box.world.restore

    def restore(*a: object, **k: object):
        holder = docker.reservation_holder(box.server_dir)
        seen.append(None if holder is None else holder.press)
        return real(*a, **k)  # type: ignore[arg-type]

    from dataclasses import replace

    world = replace(_reserving(box), restore=restore)
    move_flows.run_import(world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False)
    assert seen and set(seen) == {move_flows.PRESS_BRING_IN}


def test_a_dump_without_a_record_is_refused_at_the_load_even_if_the_plan_let_it_through(
    tmp_path: Path,
) -> None:
    """Defence in depth: the engine's own plan is asked again of the extracted file, and an
    unlabelled one is never accepted (Restore may ask the player; a move may not)."""
    package = make_package(tmp_path, {"acore_auth": "wow-wotlk", "acore_characters": None})
    box = target(tmp_path)
    manifest = move.read_package(package).manifest
    forged = move_flows.ImportPlan(
        path=package,
        manifest=manifest,
        refusals=(),
        schemas=("acore_auth", "acore_characters"),
        counts=(0, 0),
    )
    with pytest.raises(MaintenanceError) as raised:
        move_flows.run_import(
            box.world, forged, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert move.unlabeled_dump("db/acore_characters.sql") in str(raised.value)
    assert not [e for e in box.events if e.startswith("load:")]


def test_the_target_moving_to_another_version_since_the_plan_refuses(tmp_path: Path) -> None:
    box, _package, plan = ready(tmp_path)
    box.db.updates = ("2024_01_a", "2024_01_b", "2024_03_newer")  # updated after the plan
    with pytest.raises(MaintenanceError, match="not at the same version as this server's"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert not [e for e in box.events if e.startswith(("load:", "dump:"))]


# ------------------------------------------------------------ cold review (Opus) findings


def test_a_dump_that_goes_on_to_write_into_another_database_is_not_loaded(
    tmp_path: Path,
) -> None:
    """`verify_dump` reads the head; the engine's plan scans all of the file."""
    package = make_package(tmp_path, {"acore_auth": "wow-wotlk", "acore_characters": "wow-wotlk"})
    # Rebuild the characters member with a second database named after its own.
    src = tmp_path / "joined"
    src.mkdir()
    auth = src / "acore_auth.sql"
    auth.write_bytes(dump_text("acore_auth"))
    chars = src / "acore_characters.sql"
    chars.write_bytes(
        dump_text("acore_characters").replace(
            b"-- Dump completed", b"USE `acore_world`;\nDROP TABLE `creature`;\n-- Dump completed"
        )
    )
    header = Header(
        game_id="wow-wotlk",
        game_name="WoW WotLK",
        realm_name=None,
        channel_account=None,
        bot_prefix=None,
        counts=Counts(accounts=0, characters=0, bot_accounts=0, bot_characters=0),
        schema_evidence={
            s: Evidence(kind="updates", count=2, digest=_digest(("2024_01_a", "2024_01_b")))
            for s in ("acore_auth", "acore_characters")
        },
        excluded=(),
        made=AT,
    )
    joined = tmp_path / "joined.zip"
    move.write_package(
        joined,
        header,
        [
            DumpFile("acore_auth", "auth", auth, ("t",)),
            DumpFile("acore_characters", "characters", chars, ("t",)),
        ],
    )
    del package
    box = target(tmp_path)
    forged = move_flows.ImportPlan(
        path=joined,
        manifest=move.read_package(joined).manifest,
        refusals=(),
        schemas=("acore_auth", "acore_characters"),
        counts=(0, 0),
    )
    with pytest.raises(MaintenanceError, match="writes into acore_characters, acore_world"):
        move_flows.run_import(
            box.world, forged, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert not [e for e in box.events if e.startswith("load:")]


def test_a_package_that_calls_the_world_database_its_characters_is_refused(
    tmp_path: Path,
) -> None:
    package = make_package(tmp_path, {"acore_auth": "wow-wotlk", "acore_world": "wow-wotlk"})
    plan = move_flows.plan_import(target(tmp_path).world, package)
    assert not plan.allowed
    joined = " ".join(plan.refusals)
    assert "holds acore_world as its characters database" in joined


def test_non_empty_data_the_file_does_not_cover_refuses_the_plan(tmp_path: Path) -> None:
    package = packed(tmp_path)
    db = Db(
        [],
        present=("acore_auth", "acore_characters", "acore_world", "acore_playerbots"),
        extra_tables={
            "acore_playerbots": {"playerbots_random_bots": 5},
            "acore_characters": {"mod_transmog": 12, "mod_empty": 0},
        },
    )
    plan = move_flows.plan_import(target(tmp_path, db=db).world, package)
    assert not plan.allowed
    said = " ".join(plan.refusals)
    assert "acore_playerbots.playerbots_random_bots" in said
    assert "acore_characters.mod_transmog" in said
    assert "mod_empty" not in said
    assert "refers to its characters" in said


def test_a_server_with_only_the_realm_row_and_empty_extras_is_not_refused(tmp_path: Path) -> None:
    package = packed(tmp_path)
    db = Db([], extra_tables={"acore_characters": {"mod_empty": 0}})
    assert move_flows.plan_import(target(tmp_path, db=db).world, package).allowed


def test_data_the_file_does_not_cover_that_appears_after_the_plan_refuses_the_run(
    tmp_path: Path,
) -> None:
    box, _package, plan = ready(tmp_path)
    box.db.extra_tables = {"acore_characters": {"mod_transmog": 3}}
    with pytest.raises(MaintenanceError, match="mod_transmog"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert not [e for e in box.events if e.startswith(("load:", "dump:"))]


def test_a_backup_that_fails_part_way_leaves_no_dump_behind(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database",), db=Db([], fail_dump_of="acore_characters"))
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(MaintenanceError, match="INCOMPLETE"):
        move_flows.export_package(box.world, folder, stop_allowed=False)
    backups = maintenance.backups_dir(box.server_dir)
    assert not [p for p in backups.glob("*_move_*")], "an auth dump with every verifier stayed"
    # T747: the dumps are written into the pack's working folder now, so that goes too.
    assert not move_flows.pack_folder(box.server_dir).exists(), "the working folder stayed"


def test_a_stop_that_fails_half_way_still_has_the_server_started_again(tmp_path: Path) -> None:
    box = Box(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"))
    box.stop_fails = True
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(RuntimeError, match="docker stop failed"):
        move_flows.export_package(box.world, folder, stop_allowed=True)
    assert box.events[-1] == "start"


def test_a_realm_name_with_a_backslash_survives_the_clients_escaping(tmp_path: Path) -> None:
    db = Db([], realm="Back\\\\slash")  # what mysql --batch prints for Back\slash
    box = Box(tmp_path, running_now=("ac-database",), db=db)
    folder = tmp_path / "docs"
    folder.mkdir()
    manifest = move_flows.export_package(box.world, folder, stop_allowed=False).manifest
    assert manifest.realm_name == "Back\\slash"


# ------------------------------------------------------------ module rows, other ledgers (lead)

MODULES_A = (("0000_playerbots_names.sql", "aa11"), ("transmog.sql", "bb22"))


def test_the_manifest_carries_the_module_rows_of_each_ledger(tmp_path: Path) -> None:
    db = Db([], module_rows={"acore_characters": MODULES_A})
    box = Box(tmp_path, running_now=("ac-database",), db=db)
    folder = tmp_path / "docs"
    folder.mkdir()
    manifest = move_flows.export_package(box.world, folder, stop_allowed=False).manifest
    assert manifest.schema_evidence["acore_characters"].modules == (
        "0000_playerbots_names.sql|aa11",
        "transmog.sql|bb22",
    )
    assert manifest.schema_evidence["acore_auth"].modules == ()


def packed_with(tmp_path: Path, **db: object) -> Path:
    root = tmp_path / "source-mod"
    root.mkdir(exist_ok=True)
    source = Box(root, running_now=("ac-database",), db=Db([], **db))  # type: ignore[arg-type]
    folder = tmp_path / "out-mod"
    folder.mkdir(exist_ok=True)
    return move_flows.export_package(source.world, folder, stop_allowed=False).path


def test_a_target_with_the_same_module_rows_is_allowed(tmp_path: Path) -> None:
    package = packed_with(tmp_path, module_rows={"acore_characters": MODULES_A})
    box = target(tmp_path, db=Db([], module_rows={"acore_characters": MODULES_A}))
    assert move_flows.plan_import(box.world, package).allowed


def test_a_target_with_other_module_rows_is_refused_naming_them(tmp_path: Path) -> None:
    package = packed_with(tmp_path, module_rows={"acore_characters": MODULES_A})
    here = (("0000_playerbots_names.sql", "aa11"), ("ah_bot.sql", "cc33"))
    box = target(tmp_path, db=Db([], module_rows={"acore_characters": here}))
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert plan.refusals == (
        "This package was made on a server with transmog.sql, this one has ah_bot.sql: "
        "install the same modules first, or move the whole server (level 2).",
    )


def test_module_rows_that_change_after_the_plan_refuse_the_run(tmp_path: Path) -> None:
    package = packed_with(tmp_path, module_rows={"acore_characters": MODULES_A})
    box = target(tmp_path, db=Db([], module_rows={"acore_characters": MODULES_A}))
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed
    box.db.module_rows = {"acore_characters": ()}
    box.events.clear()
    with pytest.raises(MaintenanceError, match="install the same modules first"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert not [e for e in box.events if e.startswith(("load:", "dump:"))]


PLAYERBOTS = ("acore_auth", "acore_characters", "acore_world", "acore_playerbots")


def test_the_playerbots_ledger_at_another_version_is_refused_naming_it(tmp_path: Path) -> None:
    package = packed_with(tmp_path, present=PLAYERBOTS)
    box = target(
        tmp_path,
        db=Db([], present=PLAYERBOTS, updates_by_schema={"acore_playerbots": ("x", "y", "z")}),
    )
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "acore_playerbots: 2 updates in the file, 3 here" in plan.refusals[0]


def test_a_schema_without_any_ledger_on_both_sides_is_not_a_difference(tmp_path: Path) -> None:
    present = (*PLAYERBOTS, "acore_ale")
    package = packed_with(tmp_path, present=present, no_ledger=("acore_ale",))
    assert "acore_ale" not in move.read_package(package).manifest.schema_evidence
    box = target(tmp_path, db=Db([], present=present, no_ledger=("acore_ale",)))
    assert move_flows.plan_import(box.world, package).allowed


def test_a_ledger_on_one_side_only_is_refused(tmp_path: Path) -> None:
    present = (*PLAYERBOTS, "acore_ale")
    package = packed_with(tmp_path, present=present, no_ledger=("acore_ale",))
    box = target(tmp_path, db=Db([], present=present))  # this server's ale has a ledger
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "acore_ale: this server has an update record and the file has none" in plan.refusals[0]


def test_a_target_without_the_playerbots_schema_is_not_asked_about_its_ledger(
    tmp_path: Path,
) -> None:
    package = packed_with(tmp_path, present=PLAYERBOTS)
    box = target(tmp_path)  # acore_auth, acore_characters, acore_world only
    assert move_flows.plan_import(box.world, package).allowed


# =============================================================== a restore load still running


@pytest.fixture
def live_load() -> Iterator[subprocess.Popen[bytes]]:
    """A real process the restore record names as its load (never a fake pid)."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    yield child
    child.kill()
    child.wait()


def _ended_load_pid() -> int:
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    return gone.pid


STILL_LOADING = "is still loading its databases in Docker"


def test_a_running_load_refuses_the_export_plan_in_its_own_words(
    tmp_path: Path, live_load: subprocess.Popen[bytes]
) -> None:
    """T734: a load another Yu'lon left running is waited for, not "restored again"."""
    box = Box(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"))
    _marker_with(box.server_dir, live_load.pid)
    plan = move_flows.plan_export(box.world)
    assert len(plan.refusals) == 1, plan.refusals
    assert STILL_LOADING in plan.refusals[0]
    assert "Wait for it to finish" in plan.refusals[0]
    assert "did not finish" not in plan.refusals[0]
    assert "Restore again" not in plan.refusals[0]


@pytest.mark.parametrize("stop_allowed", [False, True])
def test_a_running_load_refuses_the_export_before_anything_is_stopped_or_packed(
    tmp_path: Path, live_load: subprocess.Popen[bytes], stop_allowed: bool
) -> None:
    box = Box(tmp_path, running_now=("ac-database", "ac-authserver", "ac-worldserver"))
    _marker_with(box.server_dir, live_load.pid)
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(move_flows.MoveError) as raised:
        move_flows.export_package(box.world, folder, stop_allowed=stop_allowed)
    said = str(raised.value)
    assert STILL_LOADING in said and "did not finish" not in said, said
    assert box.events == [], "nothing was stopped, dumped or started"
    assert not list(folder.iterdir())


def test_a_running_load_refuses_the_import_plan_in_its_own_words(
    tmp_path: Path, live_load: subprocess.Popen[bytes]
) -> None:
    package = packed(tmp_path)
    box = target(tmp_path)
    _marker_with(box.server_dir, live_load.pid)
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert [r for r in plan.refusals if STILL_LOADING in r] == list(plan.refusals), plan.refusals
    assert len(plan.refusals) == 1
    assert "did not finish" not in plan.refusals[0]
    assert box.events == []


def test_a_load_that_ended_still_refuses_the_export_and_the_import_as_unfinished(
    tmp_path: Path,
) -> None:
    """The record outlives its load: "did not finish ... Restore again" is the right advice."""
    package = packed(tmp_path)
    source = Box(tmp_path, running_now=("ac-database",))
    _marker_with(source.server_dir, _ended_load_pid())
    record = maintenance.interrupted_restore(source.server_dir)
    assert record is not None and not record.load_running
    assert move_flows.plan_export(source.world).refusals == (move_flows._UNFINISHED_RESTORE,)
    folder = tmp_path / "docs"
    folder.mkdir()
    with pytest.raises(move_flows.MoveError) as raised:
        move_flows.export_package(source.world, folder, stop_allowed=False)
    assert str(raised.value) == move_flows._UNFINISHED_RESTORE
    box = target(tmp_path)
    _marker_with(box.server_dir, _ended_load_pid())
    assert move_flows.plan_import(box.world, package).refusals == (move_flows._UNFINISHED_RESTORE,)


def test_a_damaged_install_record_is_still_said_first_over_a_running_load(
    tmp_path: Path, live_load: subprocess.Popen[bytes]
) -> None:
    box = Box(tmp_path, running_now=("ac-database",), ownership=Ownership.UNKNOWN)
    _marker_with(box.server_dir, live_load.pid)
    assert move_flows.plan_export(box.world).refusals == (move_flows.INSTALL_RECORD_DAMAGED,)


# =============================================================== this Yu'lon's own restore (T736)


def _press_during_own_restore(
    box: Box, press: Callable[[], object], *, live_pid: int | None = None
) -> tuple[object, bool]:
    """A real restore of `box`, held the way the Maintenance tab holds one; `press` mid-load.

    The lease and the hold are `controller_view._restore_with_the_database()` and
    `_restore_under_the_lease()`'s own: `RESTORE_HOLDS_THE_DATABASES`, then
    `hold_the_server(alone=RESTORE_WRITES_ALONE)`. The engine's restore then writes its record
    and loads; `press` runs on a thread of its own (the tab's JobRunner gives each press one)
    while the first database is being loaded. `live_pid` names a real process as the load's,
    as a real `docker exec` load is recorded. Returns what the press returned or raised, and
    whether the restore record was in place when it was pressed.
    """
    from yulon import forgetting

    report = box.world.backup()
    plan = box.world.plan_restore(
        next(d.path for d in report.dumps if d.database == "acore_characters")
    )
    assert plan.allowed, plan.refusals
    load = box.db.load_from
    got: list[object] = []
    record_there: list[bool] = []

    def load_and_press(source: IO[bytes]) -> None:
        if not got:
            if live_pid is not None:
                _marker_with(box.server_dir, live_pid)
            record_there.append(maintenance.interrupted_restore(box.server_dir) is not None)

            def pressed() -> None:
                try:
                    got.append(press())
                except Exception as exc:  # noqa: BLE001 - the press's answer, whatever it is
                    got.append(exc)

            worker = threading.Thread(target=pressed)
            worker.start()
            worker.join(30)
        load(source)

    box.db.load_from = load_and_press  # type: ignore[method-assign]
    with (
        docker.maintenance_lease(
            box.server_dir,
            forgetting.RESTORE_HOLDS_THE_DATABASES,
            press=forgetting.PRESS_RESTORE,
        ),
        docker.hold_the_server(
            box.server_dir,
            forgetting.RESTORE_HOLDS_THE_SERVER,
            press=forgetting.PRESS_RESTORE,
            alone=forgetting.RESTORE_WRITES_ALONE,
        ),
    ):
        box.world.restore(plan, "before-the-test")
    box.db.load_from = load  # type: ignore[method-assign]
    assert got and record_there, "the press never ran mid-load"
    return got[0], record_there[0]


@pytest.mark.parametrize("load_named", [False, True], ids=["no-load-pid", "live-load-pid"])
def test_a_pack_during_this_yulons_own_restore_names_the_restore(
    tmp_path: Path, live_load: subprocess.Popen[bytes], load_named: bool
) -> None:
    """T736: the restore is named, never "did not finish ... Restore again".

    T737: in a move's own words (`RESTORE_HOLDS_A_MOVE`), not the Start/Stop sentence the hold
    carries, whose reason is what starting or stopping would do.
    Mutation: ask only `orphaned_restore_reason()` in `_refuse_if_unsafe` (it steps aside for
    this process's own hold, so the record's "did not finish" was said).
    """
    from yulon import forgetting

    box = Box(tmp_path, running_now=("ac-database",))
    plan, record_there = _press_during_own_restore(
        box,
        lambda: move_flows.plan_export(box.world),
        live_pid=live_load.pid if load_named else None,
    )
    assert record_there, "the restore record was not in place: the test proves nothing"
    assert isinstance(plan, move_flows.ExportPlan), plan
    assert plan.refusals == (forgetting.RESTORE_HOLDS_A_MOVE,), plan.refusals
    assert move_flows.plan_export(box.world).allowed, "the restore is over: Pack goes ahead"


def test_a_pack_during_this_yulons_own_restore_stops_and_packs_nothing(tmp_path: Path) -> None:
    from yulon import forgetting

    box = Box(tmp_path, running_now=("ac-database",))
    folder = tmp_path / "docs"
    folder.mkdir()
    events_at_press: list[int] = []

    def pack() -> object:
        events_at_press.append(len(box.events))
        try:
            return move_flows.export_package(box.world, folder, stop_allowed=True)
        finally:
            events_at_press.append(len(box.events))

    raised, _ = _press_during_own_restore(box, pack)
    assert isinstance(raised, move_flows.MoveError), raised
    assert str(raised) == forgetting.RESTORE_HOLDS_A_MOVE
    assert events_at_press[0] == events_at_press[1], "the pack stopped, dumped or started"
    assert not list(folder.iterdir())


def test_a_bring_in_plan_during_this_yulons_own_restore_names_the_restore(
    tmp_path: Path,
) -> None:
    from yulon import forgetting

    package = packed(tmp_path)
    box = target(tmp_path)
    plan, record_there = _press_during_own_restore(
        box, lambda: move_flows.plan_import(box.world, package)
    )
    assert record_there
    assert isinstance(plan, move_flows.ImportPlan), plan
    assert plan.refusals == (forgetting.RESTORE_HOLDS_A_MOVE,), plan.refusals


def test_a_start_during_the_same_restore_keeps_the_start_and_stop_sentence(tmp_path: Path) -> None:
    """T737: only a move is told the move's sentence; a Start in the same restore keeps T216's.

    Mutation: rewrite the hold's sentence itself instead of what a move is told (a Start would
    then be told about packing).
    """
    from yulon import forgetting

    box = Box(tmp_path, running_now=("ac-database",))

    def start() -> object:
        with docker._in_flight(box.server_dir, press=forgetting.PRESS_START):
            return "started"

    raised, record_there = _press_during_own_restore(box, start)
    assert record_there
    assert isinstance(raised, docker.ServerHeldError), raised
    assert str(raised) == forgetting.RESTORE_HOLDS_THE_SERVER
    said = forgetting.RESTORE_HOLDS_A_MOVE
    assert said != forgetting.RESTORE_HOLDS_THE_SERVER
    assert "Starting" not in said and "started or stopped" not in said, said
    assert "Wait for the restore to finish, then try again." in said, said


def test_a_start_during_an_accounts_bring_in_is_told_accounts_and_characters(
    tmp_path: Path,
) -> None:
    """T737's other side: the Maintenance tab's Bring in keeps its own sentences.

    Mutation: make `_database_session`'s hold and lease sentences the whole-server ones.
    """
    from yulon import forgetting

    package = packed(tmp_path)
    box = target(tmp_path)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    said: dict[str, object] = {}
    load = box.db.load_from

    def start() -> object:
        with docker._in_flight(box.server_dir, press=forgetting.PRESS_START):
            return "started"

    def backup() -> object:
        with docker.maintenance_lease(
            box.server_dir, forgetting.BACKUP_HOLDS_THE_DATABASES, press=forgetting.PRESS_BACKUP
        ):
            return "backed up"

    def answer(name: str, press: Callable[[], object]) -> None:
        try:
            said[name] = press()
        except Exception as exc:  # noqa: BLE001 - the press's answer, whatever it is
            said[name] = exc

    def load_and_press(source: IO[bytes]) -> None:
        if not said:
            for name, press in (("start", start), ("backup", backup)):
                worker = threading.Thread(target=answer, args=(name, press))
                worker.start()
                worker.join(30)
        load(source)

    box.db.load_from = load_and_press  # type: ignore[method-assign]
    move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert str(said.get("start")) == forgetting.MOVE_HOLDS_THE_SERVER, said
    assert isinstance(said["start"], docker.ServerHeldError)
    assert str(said.get("backup")) == forgetting.MOVE_HOLDS_THE_DATABASES, said
    assert isinstance(said["backup"], docker.MaintenanceLeaseTaken)


def test_clear_pack_folder_takes_a_dead_packs_folder_and_follows_no_link(tmp_path: Path) -> None:
    """T765: the engine half of the uninstall's sweep (purge.py calls it after its redesign)."""
    server = tmp_path / "server"
    work = move_flows.pack_folder(server)
    work.mkdir(parents=True)
    (work / "20261001_000000_move_acore_auth.sql").write_bytes(b"every verifier")
    move_flows.clear_pack_folder(server)
    assert not os.path.lexists(work)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "mine.sql").write_bytes(b"the player's")
    work.symlink_to(elsewhere, target_is_directory=True)
    move_flows.clear_pack_folder(server)
    assert not os.path.lexists(work)
    assert (elsewhere / "mine.sql").read_bytes() == b"the player's"
    move_flows.clear_pack_folder(server)  # nothing there: nothing to do


# ------------------------------------------------ which server is behind (T802)


def _sql_names(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"2026_10_{n:02d}_00_{prefix}.sql" for n in range(count))


def ledger_package(
    tmp_path: Path,
    names: dict[str, tuple[str, ...]],
    *,
    name: str = "ledger.zip",
    include: dict[str, tuple[str, ...]] | None = None,
) -> Path:
    """A package whose dumps hold a real `updates` insert for each schema in `names`.

    `include` adds an `updates_include` insert (`path|state` rows) to a schema's dump.
    """
    src = tmp_path / "ledger"
    src.mkdir(exist_ok=True)
    dumps = []
    for schema, held in names.items():
        rows = ",".join(
            f"('{n}','{hashlib.sha1(n.encode()).hexdigest().upper()}','RELEASED',"
            "'2026-10-01 00:00:00',0)"
            for n in held
        )
        insert = f"INSERT INTO `updates` VALUES {rows};\n".encode()
        folders = (include or {}).get(schema, ())
        if folders:
            values = ",".join("('{}','{}')".format(*row.split("|", 1)) for row in folders)
            insert += f"INSERT INTO `updates_include` VALUES {values};\n".encode()
        path = src / f"{schema}.sql"
        body = dump_text(schema).replace(b"-- Dump completed", insert + b"-- Dump completed")
        path.write_bytes(body)
        role = (
            "auth"
            if schema.endswith("auth")
            else "playerbots" if schema.endswith("playerbots") else "characters"
        )
        dumps.append(DumpFile(schema, role, path, ("t",)))  # type: ignore[arg-type]
    header = Header(
        game_id="wow-wotlk",
        game_name="WoW WotLK",
        realm_name="Old Realm",
        channel_account="YULON_BBBB2222",
        bot_prefix="RNDBOT",
        counts=Counts(accounts=1, characters=1, bot_accounts=0, bot_characters=0),
        schema_evidence={
            s: Evidence(kind="updates", count=len(n), digest=_digest(n)) for s, n in names.items()
        },
        excluded=(),
        made=AT,
    )
    dest = tmp_path / name
    move.write_package(dest, header, dumps)
    return dest


def _ledger_refusal(
    tmp_path: Path,
    in_file: dict[str, tuple[str, ...]],
    here: dict[str, tuple[str, ...]],
    *,
    ownership: Ownership = Ownership.OWNED,
) -> str:
    package = ledger_package(tmp_path, in_file)
    db = Db(
        [],
        present=("acore_world", *here),
        updates_by_schema=here,
    )
    box = target(tmp_path, db=db)
    box.ownership = ownership
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    return plan.refusals[0]


AUTH_23 = _sql_names("auth", 23)
BOTS_36 = _sql_names("bots", 36)


def test_a_package_behind_names_the_file_as_behind(tmp_path: Path) -> None:
    said = _ledger_refusal(
        tmp_path,
        {
            "acore_auth": AUTH_23[:22],
            "acore_characters": _sql_names("chars", 5),
            "acore_playerbots": BOTS_36[:31],
        },
        {
            "acore_auth": AUTH_23,
            "acore_characters": _sql_names("chars", 5),
            "acore_playerbots": BOTS_36,
        },
    )
    assert said.startswith(
        "The file is behind this server (acore_auth: 22 updates in the file, 23 here; "
        "acore_playerbots: 31 updates in the file, 36 here). Bring the OLD server up to date "
        "and pack again"
    )


def test_a_package_ahead_names_this_server_as_behind(tmp_path: Path) -> None:
    said = _ledger_refusal(
        tmp_path,
        {"acore_auth": AUTH_23, "acore_characters": _sql_names("chars", 5)},
        {"acore_auth": AUTH_23[:22], "acore_characters": _sql_names("chars", 5)},
    )
    assert said.startswith(
        "This server is behind the file (acore_auth: 23 updates in the file, 22 here)."
    )
    assert under_server_build(UPDATE_TO_LATEST) in said


def test_updates_sets_that_differ_both_ways_still_refuse(tmp_path: Path) -> None:
    said = _ledger_refusal(
        tmp_path,
        {
            "acore_auth": (*AUTH_23[:20], "x_file_only.sql"),
            "acore_characters": _sql_names("chars", 5),
        },
        {
            "acore_auth": (*AUTH_23[:20], "x_here_only.sql"),
            "acore_characters": _sql_names("chars", 5),
        },
    )
    assert said.startswith("The two servers ran different updates")
    assert "whole server" in said
    assert "Update the server to latest" not in said


def test_refusal_never_advises_update_to_latest_for_a_server_without_an_install_record(
    tmp_path: Path,
) -> None:
    said = _ledger_refusal(
        tmp_path,
        {"acore_auth": AUTH_23, "acore_characters": _sql_names("chars", 5)},
        {"acore_auth": AUTH_23[:22], "acore_characters": _sql_names("chars", 5)},
        ownership=Ownership.UNCLAIMED,
    )
    assert "This server is behind the file" in said
    assert UPDATE_TO_LATEST not in said
    assert "launcher that built it" in said


def test_a_package_with_no_readable_names_keeps_the_plain_refusal(tmp_path: Path) -> None:
    package = packed(tmp_path)  # its dumps hold no updates insert
    box = target(tmp_path, db=Db([], updates=("2024_01_a", "2024_01_b", "2024_02_new")))
    said = move_flows.plan_import(box.world, package).refusals[0]
    assert said.startswith("The databases in this file are not at the same version")


# ------------------------------------------------------------ appliable tails (T802)


def _tail_names(prefix: str, count: int) -> tuple[str, ...]:
    return tuple(f"2026_10_{n:02d}_00_{prefix}.sql" for n in range(count))


TAIL_INCLUDE = {
    schema: (f"$/data/sql/updates/db_{schema.removeprefix('acore_')}|RELEASED",)
    for schema in ("acore_auth", "acore_characters", "acore_world")
}
"""The `updates_include` rows of a stock AzerothCore ledger, as far as these tests need."""


def _tail_box(
    tmp_path: Path, file_names: dict[str, tuple[str, ...]], here_names: dict[str, tuple[str, ...]]
) -> tuple[Box, Path]:
    """A target whose ledger holds `here_names` and a package holding `file_names`.

    Both sides' `updates` rows are scripted `(name, hash, state)` answers; the
    checkout files the tail proof hashes are laid under the target's server dir.
    """
    package = ledger_package(tmp_path, file_names, include=TAIL_INCLUDE)
    hashes = {
        name: hashlib.sha1(name.encode()).hexdigest().upper()
        for names in here_names.values()
        for name in names
    }
    rows = {
        schema: tuple((n, hashes[n], "RELEASED") for n in names)
        for schema, names in here_names.items()
    }
    present = ("acore_auth", "acore_characters", "acore_world", "acore_playerbots")
    db = Db(
        [],
        present=present,
        tail_rows=rows,
        updates_by_schema=dict(here_names),
        include_rows=dict(TAIL_INCLUDE),
    )
    db.updates_by_schema.setdefault("acore_playerbots", ())  # no bot ledger unless given
    box = target(tmp_path, db=db)
    for schema, names in here_names.items():
        for name in names:
            path = move.tail_update_path(box.server_dir, schema, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
    runs: list[docker.AttachedRun] = []

    def apply_updates(sink: object) -> docker.AttachedRun:
        box.events.append("apply-updates")
        both = set(file_names) | set(here_names)
        for schema in both:
            db.updates_by_schema[schema] = here_names.get(schema, ())
        run = docker.AttachedRun(returncode=0, tail=())
        runs.append(run)
        return run

    from dataclasses import replace

    box.world = replace(box.world, apply_updates=apply_updates)
    return box, package


def test_a_tail_behind_is_allowed_and_runs_the_one_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    assert plan.updates_to_apply == {"acore_auth": (auth[22],)}
    result = move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert "apply-updates" in box.events
    assert box.events.index("apply-updates") > box.events.index("load:acore_characters")
    assert "applied after the load" in result.text()


def test_a_tail_without_the_seam_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    from dataclasses import replace

    box.world = replace(box.world, apply_updates=None)
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "The file is behind this server" in " ".join(plan.refusals)


def test_a_tail_with_a_failing_one_shot_names_the_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    from dataclasses import replace

    def failing(sink: object) -> docker.AttachedRun:
        return docker.AttachedRun(returncode=1, tail=("Applying of file 'x.sql' failed",))

    box.world = replace(box.world, apply_updates=failing)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match="before-move"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )


def test_a_one_shot_that_applies_nothing_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the post-run re-read and a silent updater passes."""
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    from dataclasses import replace

    box.world = replace(box.world, apply_updates=lambda sink: docker.AttachedRun(0, ()))
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match="did not land as expected"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )


def _tortoise_package(tmp_path: Path) -> Path:
    """A tortoise package two updates behind this server, updates-ledgers both sides (T802)."""
    from yulon import move as _move
    from yulon.move import Counts as _Counts
    from yulon.move import DumpFile as _DumpFile
    from yulon.move import Evidence as _Evidence
    from yulon.move import Header as _Header

    behind = ("2024_01_a", "2024_01_b")
    src = tmp_path / "tortoise-src"
    src.mkdir(exist_ok=True)
    dumps = []
    for schema, role in (("tw_logon", "auth"), ("tw_char", "characters")):
        rows = ",".join(
            f"('{n}','{__import__('hashlib').sha1(n.encode()).hexdigest().upper()}','RELEASED',"
            "'2026-10-01 00:00:00',0)"
            for n in behind
        )
        body = (
            f"-- yulon-backup: game=wow-tortoise\n-- MySQL dump 10.13\nUSE `{schema}`;\n"
            f"INSERT INTO `updates` VALUES {rows};\n-- Dump completed\n"
        ).encode()
        path = src / f"{schema}.sql"
        path.write_bytes(body)
        dumps.append(_DumpFile(schema, role, path, ("t",)))
    header = _Header(
        game_id="wow-tortoise",
        game_name="WoW Tortoise",
        realm_name="Old Realm",
        channel_account="YULON_BBBB2222",
        bot_prefix="RNDBOT",
        counts=_Counts(accounts=1, characters=1, bot_accounts=0, bot_characters=0),
        schema_evidence={
            "tw_logon": _Evidence(kind="updates", count=2, digest=move_flows.digest_of(behind)),
            "tw_char": _Evidence(kind="updates", count=2, digest=move_flows.digest_of(behind)),
        },
        excluded=(),
        made=AT,
    )
    dest = tmp_path / "tortoise.zip"
    _move.write_package(dest, header, dumps)
    return dest


def test_a_tail_on_another_family_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A CMaNGOS server never takes the tail path (T802).

    Masked while `_tail_rows_of_server` also gates on family: dropping only the
    `tail_updates` line still refuses at the rows. This pins the refusal, and
    both gates together turn the plan green.
    """
    from yulon.catalog import catalog as _catalog

    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    tortoise = _catalog.load_catalog().get("wow-tortoise")
    assert tortoise.install is not None and tortoise.install.native is not None
    assert tortoise.install.native.family != "azerothcore"
    package = _tortoise_package(tmp_path)
    ahead = (
        "2024_01_a",
        "2024_01_b",
        "2024_01_c",
    )
    tail_rows = {
        schema: tuple((n, hashlib.sha1(n.encode()).hexdigest().upper(), "RELEASED") for n in ahead)
        for schema in ("tw_logon", "tw_char")
    }
    db = Db(
        [],
        present=("tw_logon", "tw_char", "tw_world"),
        updates_by_schema={"tw_logon": ahead, "tw_char": ahead},
        tail_rows=tail_rows,
    )
    box = Box(tmp_path, "wow-tortoise", running_now=(), db=db)
    from dataclasses import replace

    for schema in ("tw_logon", "tw_char"):
        for name in ahead:
            path = move.tail_update_path(box.server_dir, schema, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
    containers = box.world.entry.containers.model_copy(update={"db_import": "tw-db-import"})
    entry = box.world.entry.model_copy(update={"containers": containers})
    box.world = replace(
        box.world, entry=entry, apply_updates=lambda sink: docker.AttachedRun(0, ())
    )
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "Bring the OLD server up to date" in " ".join(plan.refusals)
    package = move.read_package(package)
    assert move_flows.tail_updates(box.world, package.manifest, {"tw_logon": None}, package) is None


def test_a_tail_without_a_db_import_service_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the db_import check and a server with no importer takes the tail path."""
    from dataclasses import replace

    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    containers = box.world.entry.containers.model_copy(update={"db_import": None})
    entry = box.world.entry.model_copy(update={"containers": containers})
    box.world = replace(box.world, entry=entry)
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "The file is behind this server" in " ".join(plan.refusals)


def test_a_tail_on_an_unowned_server_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the ownership check and an adopted server takes the tail path."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    box.ownership = Ownership.UNCLAIMED
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed


def test_a_tail_the_compose_file_hides_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the `sees is False` check and a blind importer takes the tail path."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: False)
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "The file is behind this server" in " ".join(plan.refusals)


def test_a_migrations_ledger_is_never_a_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Migrations ledgers never take the tail path (T802).

    Both kind checks are masked while `updates_names_in_dump` answers None for
    non-`updates` evidence: with the names unknown the tail proof cannot run,
    so the refusal stands with or without either line. This pins the refusal,
    not the line.
    """
    from yulon import move as _move
    from yulon.move import Evidence as _Evidence

    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    behind = ("hash-a", "hash-b")
    file_evidence = {
        "acore_auth": _Evidence(kind="migrations", count=2, digest=move_flows.digest_of(behind)),
        "acore_characters": _Evidence(
            kind="migrations", count=2, digest=move_flows.digest_of(behind)
        ),
    }
    src = tmp_path / "migsrc"
    src.mkdir(exist_ok=True)
    dumps = []
    for schema, role in (("acore_auth", "auth"), ("acore_characters", "characters")):
        body = (
            f"-- yulon-backup: game=wow-wotlk\n-- MySQL dump 10.13\nUSE `{schema}`;\n"
            "INSERT INTO `t` VALUES (1);\n-- Dump completed\n"
        ).encode()
        path = src / f"{schema}.sql"
        path.write_bytes(body)
        dumps.append(_move.DumpFile(schema, role, path, ("t",)))
    dest = tmp_path / "migledger.zip"
    _move.write_package(
        dest,
        _move.Header(
            game_id="wow-wotlk",
            game_name="WoW WotLK",
            realm_name="Old Realm",
            channel_account="YULON_BBBB2222",
            bot_prefix="RNDBOT",
            counts=_move.Counts(accounts=1, characters=1, bot_accounts=0, bot_characters=0),
            schema_evidence=file_evidence,
            excluded=(),
            made=AT,
        ),
        dumps,
    )
    db = Db(
        [], present=("acore_auth", "acore_characters", "acore_world"), version_table="migrations"
    )
    db.migration_hashes = ("hash-a", "hash-b", "hash-c")
    box = target(tmp_path, db=db)
    plan = move_flows.plan_import(box.world, dest)
    assert not plan.allowed
    package = _move.read_package(dest)
    from yulon.move_flows import _present_roles as _roles
    from yulon.move_flows import _read_versions as _versions

    roles = _roles(box.world)
    here = _versions(box.world, roles)
    assert here["acore_auth"] is not None and here["acore_auth"].kind == "migrations"
    assert package.manifest.schema_evidence["acore_auth"].kind == "migrations"
    assert move_flows.tail_updates(box.world, package.manifest, here, package) is None
    assert (
        move_flows.tail_updates(box.world, package.manifest, {"acore_auth": None}, package) is None
    )


def test_a_module_row_that_differs_refuses_the_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The evidence compare pins equal digests; the file-rows pin the same module hash (T802)."""
    from yulon import move as _move

    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    file_modules = (("mod_a.sql", "aa"),)
    file_evidence = {
        "acore_auth": _move.Evidence(
            kind="updates",
            count=len(auth[:22]),
            digest=move_flows.digest_of(auth[:22]),
            modules=tuple(sorted(f"{n}|{h * 40}" for n, h in file_modules)),
        ),
        "acore_characters": _move.Evidence(
            kind="updates", count=len(chars), digest=move_flows.digest_of(chars)
        ),
    }
    src = tmp_path / "modsrc"
    src.mkdir(exist_ok=True)
    dumps = []
    for schema, role, names in (
        ("acore_auth", "auth", auth[:22]),
        ("acore_characters", "characters", chars),
    ):
        mods = file_modules if schema == "acore_auth" else ()
        rows = [
            f"('{n}','{hashlib.sha1(n.encode()).hexdigest().upper()}','RELEASED',"
            "'2026-10-01 00:00:00',0)"
            for n in names
        ]
        rows += [f"('{n}','{h * 40}','MODULE','2026-10-01 00:00:00',0)" for n, h in mods]
        body = (
            f"-- yulon-backup: game=wow-wotlk\n-- MySQL dump 10.13\nUSE `{schema}`;\n"
            "INSERT INTO `t` VALUES (1);\n"
            f"INSERT INTO `updates` VALUES {','.join(rows)};\n"
        ).encode()
        path = src / f"{schema}.sql"
        path.write_bytes(body)
        dumps.append(_move.DumpFile(schema, role, path, ("t",)))
    dest = tmp_path / "modledger.zip"
    _move.write_package(
        dest,
        _move.Header(
            game_id="wow-wotlk",
            game_name="WoW WotLK",
            realm_name="Old Realm",
            channel_account="YULON_BBBB2222",
            bot_prefix="RNDBOT",
            counts=_move.Counts(accounts=1, characters=1, bot_accounts=0, bot_characters=0),
            schema_evidence=file_evidence,
            excluded=(),
            made=AT,
        ),
        dumps,
    )
    hashes = {
        name: hashlib.sha1(name.encode()).hexdigest().upper()
        for names in (auth, chars)
        for name in names
    }
    rows = {
        "acore_auth": tuple((n, hashes[n], "RELEASED") for n in auth),
        "acore_characters": tuple((n, hashes[n], "RELEASED") for n in chars),
    }
    present = ("acore_auth", "acore_characters", "acore_world", "acore_playerbots")
    db = Db(
        [],
        present=present,
        tail_rows=rows,
        updates_by_schema={"acore_auth": auth, "acore_characters": chars},
        module_rows={"acore_auth": (("mod_a.sql", "b" * 40),)},
    )
    box = target(tmp_path, db=db)
    for schema, names in (("acore_auth", auth), ("acore_characters", chars)):
        for name in names:
            path = move.tail_update_path(box.server_dir, schema, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(name.encode())
    from dataclasses import replace

    box.world = replace(box.world, apply_updates=lambda sink: docker.AttachedRun(0, ()))
    plan = move_flows.plan_import(box.world, dest)
    assert not plan.allowed
    assert "not at the same version" in " ".join(plan.refusals)
    tails = move_flows.tail_updates(
        box.world, _move.read_package(dest).manifest, {"acore_auth": None}, None
    )
    assert tails is None


def test_a_tail_whose_file_is_missing_on_disk_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the on-disk proof and a tail with no file takes the tail path."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    missing = move.tail_update_path(box.server_dir, "acore_auth", auth[22])
    missing.unlink()
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "The file is behind this server" in " ".join(plan.refusals)


def test_a_tail_whose_bytes_differ_on_disk_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: hash the file wrongly and changed bytes take the tail path."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    changed = move.tail_update_path(box.server_dir, "acore_auth", auth[22])
    changed.write_bytes(b"changed bytes")
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "The file is behind this server" in " ".join(plan.refusals)


def test_a_tail_whose_include_rows_differ_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the updates_include compare and a skipped file takes the tail path."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    box.db.include_rows = {"acore_auth": ("some_update.sql|RELEASED",)}
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "The file is behind this server" in " ".join(plan.refusals)


def test_a_target_updated_after_the_plan_refuses_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the run-time re-classify and a moved target still loads."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    assert plan.updates_to_apply == {"acore_auth": (auth[22],)}
    box.db.updates_by_schema["acore_auth"] = tuple(auth[:21])
    box.db.tail_rows["acore_auth"] = tuple(
        (n, hashlib.sha1(n.encode()).hexdigest().upper(), "RELEASED") for n in auth[:21]
    )
    with pytest.raises(MaintenanceError, match="behind|changed after the plan"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    assert plan.updates_to_apply != {"acore_auth": (auth[21],)}


def test_a_tail_with_a_crashing_one_shot_names_the_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: catch only DockerCommandError and an OSError escapes as a crash."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    from dataclasses import replace

    def crashing(sink: object) -> docker.AttachedRun:
        raise OSError("the engine did not answer")

    box.world = replace(box.world, apply_updates=crashing)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match="before-move"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )


def test_a_failing_one_shot_names_its_own_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the returncode branch and a failed one-shot loads silently."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    from dataclasses import replace

    def failing(sink: object) -> docker.AttachedRun:
        return docker.AttachedRun(returncode=1, tail=("Applying of file 'x.sql' failed",))

    box.world = replace(box.world, apply_updates=failing)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match="Applying the updates the file lacked"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )


def test_a_one_shot_that_renames_an_update_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: compare the digest only and a renamed ledger passes as landed."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    from dataclasses import replace

    renamed = (*auth[:22], "renamed.sql")
    assert move_flows.digest_of(renamed) != move_flows.digest_of(auth)

    def renaming(sink: object) -> docker.AttachedRun:
        box.events.append("apply-updates")
        box.db.updates_by_schema["acore_auth"] = renamed
        return docker.AttachedRun(returncode=0, tail=())

    box.world = replace(box.world, apply_updates=renaming)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match="did not land as expected"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )


def test_a_failed_tail_leaves_a_marker_that_refuses_the_next_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-shot that applies nothing leaves the databases half-written: the next
    plan finds no ledger difference and must refuse, naming the before-move backups."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    from dataclasses import replace

    def silent(sink: object) -> docker.AttachedRun:
        box.events.append("apply-updates")
        box.db.updates_by_schema["acore_characters"] = chars
        box.db.updates_by_schema["acore_playerbots"] = ()
        return docker.AttachedRun(returncode=0, tail=())

    box.world = replace(box.world, apply_updates=silent)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match="did not land as expected"):
        move_flows.run_import(
            box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
        )
    marker = move_flows._tail_marker_path(box.world)
    assert marker.exists()
    again = move_flows.plan_import(box.world, package)
    assert not again.allowed
    assert "did not finish" in " ".join(again.refusals)
    assert "before-move" in " ".join(again.refusals)
    assert "import again" in " ".join(again.refusals)


def test_a_restored_database_clears_the_way_past_the_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After the named backups are restored the ledger differs again, so the normal
    path proceeds; a verified success clears the marker."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    box.db.updates_by_schema["acore_auth"] = tuple(auth[:22])
    box.db.updates_by_schema["acore_playerbots"] = ()
    box.db.tail_rows["acore_playerbots"] = ()
    marker_here = move_flows._read_versions(box.world, move_flows._present_roles(box.world))
    move_flows._write_tail_marker(
        box.world,
        ["some/before-move_acore_auth.sql"],
        {s: e for s, e in marker_here.items() if e is not None},
    )
    again = move_flows.plan_import(box.world, package)
    assert not again.allowed
    assert "did not finish" in " ".join(again.refusals)
    second = _tail_names("second", 24)
    restored = ledger_package(
        tmp_path,
        {"acore_auth": second[:22], "acore_characters": chars},
        name="ledger-second.zip",
        include=TAIL_INCLUDE,
    )
    box.db.updates_by_schema["acore_auth"] = tuple(second)
    box.db.tail_rows["acore_auth"] = tuple(
        (n, hashlib.sha1(n.encode()).hexdigest().upper(), "RELEASED") for n in second
    )
    for name in auth:  # the checkout moved on with the server: only `second` is on disk
        move.tail_update_path(box.server_dir, "acore_auth", name).unlink()
    for name in second:
        path = move.tail_update_path(box.server_dir, "acore_auth", name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode())
    from dataclasses import replace as _replace

    def _second_updates(sink: object) -> docker.AttachedRun:
        box.events.append("apply-updates")
        box.db.updates_by_schema["acore_auth"] = tuple(second)
        box.db.updates_by_schema["acore_characters"] = chars
        return docker.AttachedRun(returncode=0, tail=())

    box.world = _replace(box.world, apply_updates=_second_updates)
    third = move_flows.plan_import(box.world, restored)
    assert third.allowed, third.refusals
    assert third.updates_to_apply == {"acore_auth": (second[22], second[23])}
    result = move_flows.run_import(
        box.world, third, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert "applied after the load" in result.text()
    assert not move_flows._tail_marker_path(box.world).exists()


def test_a_verified_tail_clears_the_marker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The green path writes the marker before the one-shot and clears it after."""
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    result = move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert "applied after the load" in result.text()
    assert not move_flows._tail_marker_path(box.world).exists()


def test_a_playerbots_only_tail_needs_no_one_shot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    bots = _tail_names("bots", 36)
    here = {
        "acore_auth": _tail_names("auth", 5),
        "acore_characters": ("2024_01_a", "2024_01_b"),
        "acore_playerbots": bots,
    }
    box, package = _tail_box(
        tmp_path,
        {
            "acore_auth": _tail_names("auth", 5),
            "acore_characters": ("2024_01_a", "2024_01_b"),
            "acore_playerbots": bots[:31],
        },
        here,
    )
    from dataclasses import replace

    seen: list[str] = []
    box.world = replace(
        box.world, apply_updates=lambda sink: seen.append("ran") or docker.AttachedRun(0, ())
    )
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    result = move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )
    assert seen == []
    assert "next Start" in result.text()


# ------------------------------------- T802 round 2: marker states and one-shot scope


def _standard_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Box, Path, tuple[str, ...], tuple[str, ...]]:
    """A target one auth update ahead of the package, with its seam bound."""
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars},
        {"acore_auth": auth, "acore_characters": chars},
    )
    return box, package, auth, chars


def _run(box: Box, plan: move_flows.ImportPlan) -> move_flows.ImportResult:
    return move_flows.run_import(
        box.world, plan, confirm=None, use_old_realm_name=False, stop_allowed=False
    )


def _failing_one_shot(box: Box) -> None:
    from dataclasses import replace

    def failing(sink: object) -> docker.AttachedRun:
        box.events.append("apply-updates")
        return docker.AttachedRun(returncode=1, tail=("Applying of file 'x.sql' failed",))

    box.world = replace(box.world, apply_updates=failing)


def _loads(box: Box) -> list[str]:
    return [e for e in box.events if e.startswith(("dump:", "load:")) or e == "apply-updates"]


def _set_aside(box: Box) -> None:
    """Move a press's copies away: the backup names carry the wall clock's second, so a
    second press within it would collide with them (the marker keeps their paths)."""
    for p in maintenance.backups_dir(box.server_dir).glob("*.sql"):
        p.rename(f"{p}.first")


def test_the_marker_holds_the_post_load_ledgers_and_the_first_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    _failing_one_shot(box)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match="Applying the updates the file lacked"):
        _run(box, plan)
    marker = move_flows._read_tail_marker(box.world)
    assert marker is not None
    assert marker.copies and all("before-move" in c for c in marker.copies)
    evidence = move.read_package(package).manifest.schema_evidence
    assert dict(marker.ledger) == {s: evidence[s] for s in ("acore_auth", "acore_characters")}


def test_a_restore_after_a_failed_tail_frees_a_same_version_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The round-1 lockout: a restore and a fresh same-version file were refused forever."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    _failing_one_shot(box)
    plan = move_flows.plan_import(box.world, package)
    with pytest.raises(MaintenanceError):
        _run(box, plan)
    assert move_flows._tail_marker_path(box.world).exists()
    refused = move_flows.plan_import(box.world, package)
    assert not refused.allowed
    assert "did not finish" in " ".join(refused.refusals)
    first = move_flows._read_tail_marker(box.world)
    assert first is not None
    _set_aside(box)
    # The Maintenance tab put the before-move copies back: the target's own ledger again.
    box.db.updates_by_schema["acore_auth"] = auth
    box.db.tail_rows["acore_auth"] = tuple(
        (n, hashlib.sha1(n.encode()).hexdigest().upper(), "RELEASED") for n in auth
    )
    same = ledger_package(
        tmp_path,
        {"acore_auth": auth, "acore_characters": chars},
        name="same-version.zip",
        include=TAIL_INCLUDE,
    )
    fresh = move_flows.plan_import(box.world, same)
    assert fresh.allowed, fresh.refusals
    assert fresh.updates_to_apply is None
    _run(box, fresh)
    assert not move_flows._tail_marker_path(box.world).exists()


def test_a_second_press_keeps_the_first_presses_copies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    _failing_one_shot(box)
    with pytest.raises(MaintenanceError):
        _run(box, move_flows.plan_import(box.world, package))
    first = move_flows._read_tail_marker(box.world)
    assert first is not None
    # The ledger moved off the post-load state (a restore, or a one-shot that got part-way).
    box.db.updates_by_schema["acore_auth"] = auth
    box.db.tail_rows["acore_auth"] = tuple(
        (n, hashlib.sha1(n.encode()).hexdigest().upper(), "RELEASED") for n in auth
    )
    box.world = replace(box.world, now=lambda: datetime(2026, 10, 9, 16, 0, 0))
    _set_aside(box)
    again = move_flows.plan_import(box.world, package)
    assert again.allowed, again.refusals
    with pytest.raises(MaintenanceError):
        _run(box, again)
    kept = move_flows._read_tail_marker(box.world)
    assert kept is not None
    assert kept.copies == first.copies
    every = {str(p) for p in maintenance.backups_dir(box.server_dir).rglob("*before-move*.sql")}
    assert every - set(first.copies), "the second press made copies of its own"


@pytest.mark.parametrize(
    "body",
    [
        "",
        '{"copies": ["a.sql"], "ledger": {',
        "[]",
        '{"copies": [], "ledger": {"acore_auth": {"kind": "updates", "count": 1, '
        '"digest": "' + "0" * 64 + '"}}}',
        '{"copies": ["a.sql"]}',
        '{"copies": ["a.sql"], "ledger": {}}',
        '{"copies": ["a.sql"], "ledger": {"acore_auth": {"kind": "updates"}}}',
        '{"copies": [""], "ledger": {"acore_auth": {"kind": "updates", "count": 1, '
        '"digest": "' + "0" * 64 + '"}}}',
    ],
    ids=[
        "empty",
        "truncated",
        "not-an-object",
        "no-copies",
        "no-ledger",
        "empty-ledger",
        "bad-evidence",
        "blank-copy",
    ],
)
def test_a_marker_that_cannot_be_read_refuses_the_plan_and_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, body: str
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    path = move_flows._tail_marker_path(box.world)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    refused = move_flows.plan_import(box.world, package)
    assert not refused.allowed
    said = " ".join(refused.refusals)
    assert "cannot read" in said and str(path) in said
    with pytest.raises(MaintenanceError, match="cannot read"):
        _run(box, plan)
    assert _loads(box) == []


def test_a_marker_the_os_will_not_let_us_read_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: let a read OSError mean "no marker" and the plan goes through."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    path = move_flows._tail_marker_path(box.world)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    real = Path.read_text

    def denied(self: Path, *a: object, **k: object) -> str:
        if self.name == move_flows.TAIL_MARKER_NAME:
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", denied)
    refused = move_flows.plan_import(box.world, package)
    assert not refused.allowed
    assert "cannot read" in " ".join(refused.refusals)


def test_a_marker_that_appears_between_plan_and_run_refuses_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the run's own marker check and a server another press half-loaded loads."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    here = move_flows._read_versions(box.world, move_flows._present_roles(box.world))
    move_flows._write_tail_marker(
        box.world, ["elsewhere/before-move.sql"], {s: e for s, e in here.items() if e is not None}
    )
    with pytest.raises(MaintenanceError, match="did not finish"):
        _run(box, plan)
    assert _loads(box) == []


def test_a_marker_whose_ledgers_differ_from_this_server_does_not_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    other = Evidence(kind="updates", count=1, digest=_digest(("x.sql",)))
    move_flows._write_tail_marker(box.world, ["old/before-move.sql"], {"acore_auth": other})
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    _run(box, plan)
    assert not move_flows._tail_marker_path(box.world).exists()


def test_a_refusal_before_the_first_load_leaves_no_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    plan = move_flows.plan_import(box.world, package)

    def refuse(path: Path, schema: str) -> None:
        raise MaintenanceError("That file is not a database copy.")

    monkeypatch.setattr(maintenance, "verify_dump", refuse)
    with pytest.raises(MaintenanceError, match="not a database copy"):
        _run(box, plan)
    assert not move_flows._tail_marker_path(box.world).exists()
    assert not [e for e in box.events if e.startswith("load:")]


def test_a_marker_that_cannot_be_written_stops_before_the_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    plan = move_flows.plan_import(box.world, package)
    real = os.replace

    def full(src: object, dst: object) -> None:
        if Path(str(dst)).name == move_flows.TAIL_MARKER_NAME:
            raise OSError(28, "No space left on device")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "replace", full)
    with pytest.raises(MaintenanceError) as raised:
        _run(box, plan)
    said = str(raised.value)
    assert "loaded nothing" in said and "before-move" in said
    assert not [e for e in box.events if e.startswith("load:")]
    assert not move_flows._tail_marker_path(box.world).exists()
    assert not list(move_flows._tail_marker_path(box.world).parent.glob(".*.tmp"))


def test_a_marker_that_cannot_be_cleared_still_finishes_the_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: let a clear OSError escape and a verified import reads as failed."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    plan = move_flows.plan_import(box.world, package)
    real = Path.unlink

    def denied(self: Path, *a: object, **k: object) -> None:
        if self.name == move_flows.TAIL_MARKER_NAME:
            raise PermissionError(13, "Permission denied", str(self))
        real(self, *a, **k)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", denied)
    result = _run(box, plan)
    assert "applied after the load" in result.text()


def test_a_target_that_gains_an_update_after_the_plan_refuses_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: compare the run's tail to None only and a longer tail runs unplanned."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    plan = move_flows.plan_import(box.world, package)
    assert plan.updates_to_apply == {"acore_auth": (auth[22],)}
    extra = "2026_10_30_00_auth.sql"
    box.db.updates_by_schema["acore_auth"] = (*auth, extra)
    box.db.tail_rows["acore_auth"] = tuple(
        (n, hashlib.sha1(n.encode()).hexdigest().upper(), "RELEASED") for n in (*auth, extra)
    )
    path = move.tail_update_path(box.server_dir, "acore_auth", extra)
    path.write_bytes(extra.encode())
    package_now = move.read_package(package)
    here = move_flows._read_versions(box.world, move_flows._present_roles(box.world))
    tails = move_flows.tail_updates(box.world, package_now.manifest, here, package_now)
    assert tails == {"acore_auth": (auth[22], extra)}  # a tail still, only not the planned one
    with pytest.raises(MaintenanceError, match="The file is behind this server"):
        _run(box, plan)
    assert _loads(box) == []


def _verify_breaker(box: Box, change: Callable[[], None]) -> None:
    """A one-shot that lands the tail and then also does `change`."""
    from dataclasses import replace

    def landing(sink: object) -> docker.AttachedRun:
        box.events.append("apply-updates")
        box.db.updates_by_schema["acore_auth"] = _tail_names("auth", 23)
        box.db.updates_by_schema["acore_characters"] = ("2024_01_a", "2024_01_b")
        change()
        return docker.AttachedRun(returncode=0, tail=())

    box.world = replace(box.world, apply_updates=landing)


def test_a_one_shot_that_changes_module_rows_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop the module-rows compare after the one-shot and a new module row passes."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)

    def module_row() -> None:
        box.db.module_rows["acore_auth"] = (("mod_extra.sql", "C" * 40),)

    _verify_breaker(box, module_row)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    with pytest.raises(MaintenanceError, match=r"did not land as expected \(acore_auth\)"):
        _run(box, plan)


def test_a_one_shot_that_changes_the_world_ledger_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)

    def world_row() -> None:
        box.db.updates_by_schema["acore_world"] = ("2024_01_a", "2024_01_b", "2026_11_w.sql")

    _verify_breaker(box, world_row)
    plan = move_flows.plan_import(box.world, package)
    with pytest.raises(MaintenanceError, match=r"did not land as expected \(acore_world\)"):
        _run(box, plan)


def test_a_world_module_row_the_one_shot_adds_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)

    def world_module() -> None:
        box.db.module_rows["acore_world"] = (("mod_world.sql", "D" * 40),)

    _verify_breaker(box, world_module)
    plan = move_flows.plan_import(box.world, package)
    with pytest.raises(MaintenanceError, match=r"did not land as expected \(acore_world\)"):
        _run(box, plan)


def test_a_ledger_whose_names_cannot_be_read_after_the_one_shot_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop `names is None` and an unread ledger crashes instead of refusing."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    flaky = {"on": False, "asked": 0}

    def after() -> None:
        flaky["on"] = True

    _verify_breaker(box, after)
    real = box.db.query
    core = move.update_name_queries("acore_auth")[0]

    def query(sql: str) -> str:
        if flaky["on"] and sql == core:
            flaky["asked"] += 1
            if flaky["asked"] == 2:  # the version reads; the names re-read does not answer
                raise RuntimeError("the database went away")
        return real(sql)

    box.db.query = query  # type: ignore[method-assign]
    plan = move_flows.plan_import(box.world, package)
    with pytest.raises(MaintenanceError, match=r"did not land as expected \(acore_auth\)"):
        _run(box, plan)


def test_a_mixed_tail_names_only_the_bot_database_for_the_next_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: name every tail in the next-Start note and auth reads as still owed."""
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    auth = _tail_names("auth", 23)
    chars = ("2024_01_a", "2024_01_b")
    bots = _tail_names("bots", 36)
    box, package = _tail_box(
        tmp_path,
        {"acore_auth": auth[:22], "acore_characters": chars, "acore_playerbots": bots[:31]},
        {"acore_auth": auth, "acore_characters": chars, "acore_playerbots": bots},
    )
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    text = _run(box, plan).text()
    assert "applied by the next Start, the way an update applies them (acore_playerbots (5))." in (
        text
    )
    assert "acore_auth (" not in text


@pytest.mark.parametrize(
    "where",
    [
        "data/sql/updates/db_world/2026_11_01_00_world.sql",
        "data/sql/updates/db_auth/2026_10_99_00_local.sql",
        "modules/mod-extra/data/sql/db-world/extra.sql",
        "modules/mod-extra/data/sql/db-characters/base/extra_chars.sql",
    ],
    ids=["world", "unreleased-auth", "module-world", "module-characters"],
)
def test_other_pending_sql_refuses_the_plan_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """Mutation: drop the scope check and the one-shot would run files nobody approved."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    path = box.server_dir / where
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"DROP TABLE `account`;\n")
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    said = " ".join(plan.refusals)
    assert "other database updates waiting" in said
    assert Path(where).name in said
    assert plan.updates_to_apply is None


def test_other_pending_sql_that_appears_after_the_plan_refuses_before_any_copy_or_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals
    path = box.server_dir / "data/sql/updates/db_world/2026_11_01_00_world.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"UPDATE `creature` SET `id1` = 1;\n")
    with pytest.raises(MaintenanceError, match="other database updates waiting"):
        _run(box, plan)
    assert _loads(box) == []
    assert box.db.executed == []
    assert not move_flows._tail_marker_path(box.world).exists()


def test_a_released_file_whose_bytes_changed_on_disk_counts_as_pending(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The updater re-runs a RELEASED file whose hash changed: that is a pending file too."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    move.tail_update_path(box.server_dir, "acore_auth", auth[0]).write_bytes(b"edited")
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert f"acore_auth: {auth[0]}" in " ".join(plan.refusals)


def test_a_bot_ledger_the_file_lacks_is_not_answered_by_a_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A one-sided playerbots ledger stays the version refusal, tail or no tail."""
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    box.db.updates_by_schema["acore_playerbots"] = _tail_names("bots", 3)
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "acore_playerbots: this server has an update record" in " ".join(plan.refusals)


def test_an_archived_file_in_an_archived_folder_is_not_rehashed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    folder = box.server_dir / "data/sql/archive/db_world"
    folder.mkdir(parents=True)
    (folder / "2019_old.sql").write_bytes(b"changed since")
    box.db.include_rows["acore_world"] = (
        *TAIL_INCLUDE["acore_world"],
        "$/data/sql/archive/db_world|ARCHIVED",
    )
    box.db.tail_rows["acore_world"] = (("2019_old.sql", "A" * 40, "ARCHIVED"),)
    plan = move_flows.plan_import(box.world, package)
    assert plan.allowed, plan.refusals


@pytest.mark.parametrize(
    "shape",
    ["outside", "duplicate", "unreadable-ledger"],
)
def test_a_one_shot_whose_files_cannot_be_known_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, shape: str
) -> None:
    box, package, auth, chars = _standard_tail(tmp_path, monkeypatch)
    if shape == "outside":
        box.db.include_rows["acore_world"] = ("/opt/custom/sql|RELEASED",)
    elif shape == "duplicate":
        twin = box.server_dir / "modules/mod-twin/data/sql/db-auth" / auth[0]
        twin.parent.mkdir(parents=True)
        twin.write_bytes(auth[0].encode())
    else:
        real = box.db.query

        def query(sql: str) -> str:
            if sql == move.ledger_row_queries("acore_world"):
                raise RuntimeError("no answer")
            return real(sql)

        box.db.query = query  # type: ignore[method-assign]
    plan = move_flows.plan_import(box.world, package)
    assert not plan.allowed
    assert "could not tell which database updates" in " ".join(plan.refusals)
