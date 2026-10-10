"""T601 level 1: packing a server's accounts and characters, and bringing them into another.

The REAL maintenance engine runs here (backup, plan_restore, restore, the marker, the
safety copies); only the database container, Docker and the controller are doubles. So
the order of things (stop, dump, start; backup, then load, then fix-ups) is the engine's
order, and the guards are the flow's own.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import IO

import pytest

from yulon import docker, move, move_flows
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import MaintenanceError
from yulon.move import Counts, DumpFile, Evidence, Header
from yulon.move_flows import MoveWorld
from yulon.ownership import Ownership

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
        no_ledger: tuple[str, ...] = (),
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
        self.no_ledger = no_ledger
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
        if "`updates`" in sql:
            schema = re.search(r"`([^`]+)`\.`updates`", sql).group(1)  # type: ignore[union-attr]
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
