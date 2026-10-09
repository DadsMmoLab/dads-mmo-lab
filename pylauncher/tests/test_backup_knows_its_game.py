"""T603: a backup says which game it is from, and Restore refuses another game's.

WotLK and Unbound both use `acore_auth/acore_characters/acore_world`, and TBC and
Vanilla both use `realmd/characters/mangos`, so nothing in a dump's schema names
tells those pairs apart. Before this, a WotLK dump put into an Unbound server's
backups folder restored without a word.

The record is one comment line ahead of mysqldump's banner, inside the .sql: a
rename, a copy to another folder or a package cannot separate it from the dump.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import IO

import pytest

from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import (
    Game,
    MaintenanceError,
    backup,
    backup_game,
    plan_restore,
    restore,
    verify_dump,
)

CATALOG = load_catalog()
WOTLK = Game("wow-wotlk", "WoW WotLK")
UNBOUND = Game("wow-unbound", "WoW Unbound")
TBC = Game("wow-tbc", "WoW TBC")
VANILLA = Game("wow-vanilla", "WoW Vanilla")

DB = "ac-database"
AT = datetime(2026, 10, 9, 12, 0, 0)

BANNER = b"-- MySQL dump 10.13  Distrib 8.0.36, for Linux (x86_64)\n--\n"


def dump_of(*databases: str, preamble: bytes = b"") -> bytes:
    parts = [preamble, BANNER]
    for name in databases:
        raw = name.encode()
        parts.append(b"USE `" + raw + b"`;\nINSERT INTO `t` VALUES (1);\n")
    parts.append(b"-- Dump completed on 2026-10-09 12:00:00\n")
    return b"".join(parts)


def line_for(game_id: str) -> bytes:
    return f"-- yulon-backup: game={game_id}\n".encode()


class FakeMysql:
    def __init__(self, present: tuple[str, ...]) -> None:
        self.present = present
        self.loaded: list[bytes] = []

    def databases(self) -> tuple[str, ...]:
        return self.present

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        sink.write(dump_of(database))

    def load_from(self, source: IO[bytes]) -> None:
        self.loaded.append(source.read())


def running(*names: str) -> maintenance.RunningNames:
    return lambda: list(names)


def write(tmp_path: Path, body: bytes, name: str = "b.sql") -> Path:
    path = tmp_path / name
    path.write_bytes(body)
    return path


def plan(path: Path, tmp_path: Path, game: Game):
    return plan_restore(path, tmp_path, game=game, running=running(DB))


# ----------------------------------------------------------- new backups carry it


def test_a_new_backup_names_its_game_in_every_file(tmp_path: Path) -> None:
    report = backup(
        tmp_path,
        FakeMysql(("acore_auth", "acore_world")),
        game=UNBOUND,
        running=running(DB),
        now=AT,
    )
    assert len(report.dumps) == 2
    for dump in report.dumps:
        assert backup_game(dump.path) == "wow-unbound"
        verify_dump(dump.path, dump.database)  # still a complete, checkable dump


class FdMysql(FakeMysql):
    """A container whose dump goes to the file DESCRIPTOR, as mysqldump's does.

    `DockerMysql` hands the sink to `subprocess.run(stdout=sink)`, and the child
    writes to the fd directly, bypassing Python's buffer. A fake that writes through
    `sink.write()` shares that buffer with the record and hides any ordering bug.
    """

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        os.write(sink.fileno(), dump_of(database))  # type: ignore[attr-defined]


def test_the_record_stays_ahead_of_a_dump_written_to_the_file_descriptor(
    tmp_path: Path,
) -> None:
    """Found on a live mariadb-dump: the record sat in Python's buffer and landed AFTER it."""
    report = backup(tmp_path, FdMysql(("acore_auth",)), game=WOTLK, running=running(DB), now=AT)
    body = report.dumps[0].path.read_bytes()
    assert body.startswith(b"-- yulon-backup: game=wow-wotlk\n-- MySQL dump")
    assert backup_game(report.dumps[0].path) == "wow-wotlk"


def test_the_record_is_ahead_of_the_banner_and_the_dump_still_starts_like_one(
    tmp_path: Path,
) -> None:
    report = backup(tmp_path, FakeMysql(("acore_auth",)), game=WOTLK, running=running(DB), now=AT)
    body = report.dumps[0].path.read_bytes()
    assert body.startswith(b"-- yulon-backup: game=wow-wotlk\n")
    assert body.index(b"yulon-backup") < body.index(b"MySQL dump")


def test_the_safety_copy_a_restore_takes_names_the_game_too(tmp_path: Path) -> None:
    path = write(tmp_path, line_for("wow-wotlk") + dump_of("acore_world"))
    mysql = FakeMysql(("acore_world",))
    made = plan_restore(path, tmp_path, game=WOTLK, running=running(DB))
    report = restore(made, mysql, game=WOTLK, confirm=made.token, running=running(DB), now=AT)
    assert report.safety_backup
    assert all(backup_game(p) == "wow-wotlk" for p in report.safety_backup)


# ------------------------------------------------------------- a wrong game is refused


@pytest.mark.parametrize(
    ("made_on", "restored_on"),
    [
        (WOTLK, UNBOUND),
        (UNBOUND, WOTLK),
        (TBC, VANILLA),
        (VANILLA, TBC),
    ],
)
def test_a_backup_from_the_other_game_that_shares_its_schema_names_is_refused(
    tmp_path: Path, made_on: Game, restored_on: Game
) -> None:
    path = write(tmp_path, line_for(made_on.id) + dump_of("acore_characters"))
    made = plan(path, tmp_path, restored_on)
    assert not made.allowed
    said = " ".join(made.refusals)
    assert made_on.name in said
    assert restored_on.name in said
    assert "will not do it" in said


def test_the_refusal_is_in_plain_words(tmp_path: Path) -> None:
    path = write(tmp_path, line_for("wow-unbound") + dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK)
    assert made.refusals == (
        "This backup is from WoW Unbound, and this server is WoW WotLK. Restoring it here "
        "would put another game's data into this server, so Yu'lon will not do it. "
        "Restore it on a WoW Unbound server instead.",
    )


def test_restore_itself_refuses_a_backup_from_another_game(tmp_path: Path) -> None:
    """The engine, not only the plan on screen: a plan built for another server is no cover."""
    path = write(tmp_path, line_for("wow-wotlk") + dump_of("acore_characters"))
    ok = plan(path, tmp_path, WOTLK)
    assert ok.allowed
    mysql = FakeMysql(("acore_characters",))
    with pytest.raises(MaintenanceError, match="will not do it"):
        restore(ok, mysql, game=UNBOUND, confirm=ok.token, running=running(DB), now=AT)
    assert mysql.loaded == []


def test_a_backup_from_the_same_game_is_allowed_and_is_not_asked_about(tmp_path: Path) -> None:
    path = write(tmp_path, line_for("wow-unbound") + dump_of("acore_characters"))
    made = plan(path, tmp_path, UNBOUND)
    assert made.allowed
    assert not made.game_unproven


def test_a_backup_made_by_backup_restores_on_the_same_game_and_not_the_other(
    tmp_path: Path,
) -> None:
    report = backup(tmp_path, FakeMysql(("acore_world",)), game=WOTLK, running=running(DB), now=AT)
    path = report.dumps[0].path
    assert plan(path, tmp_path, WOTLK).allowed
    assert not plan(path, tmp_path, UNBOUND).allowed


# ------------------------------------------------ the record cannot be forged or garbled


def test_a_line_inside_row_data_is_not_the_record(tmp_path: Path) -> None:
    """A support-ticket table holding the marker text must not relabel the dump."""
    body = (
        BANNER
        + b"USE `acore_characters`;\n"
        + b"INSERT INTO `t` VALUES (1);\n"
        + line_for("wow-wotlk")
        + b"-- Dump completed on 2026-10-09 12:00:00\n"
    )
    path = write(tmp_path, body)
    assert backup_game(path) is None
    assert plan(path, tmp_path, UNBOUND).game_unproven


def test_a_line_after_the_banner_cannot_relabel_a_dump_that_names_another_game(
    tmp_path: Path,
) -> None:
    body = line_for("wow-unbound") + dump_of("acore_characters").replace(
        b"--\n", b"--\n" + line_for("wow-wotlk"), 1
    )
    path = write(tmp_path, body)
    assert backup_game(path) == "wow-unbound"
    assert not plan(path, tmp_path, WOTLK).allowed


def test_two_different_games_in_one_file_are_refused(tmp_path: Path) -> None:
    path = write(tmp_path, line_for("wow-wotlk") + line_for("wow-unbound") + dump_of("acore_world"))
    for game in (WOTLK, UNBOUND):
        made = plan(path, tmp_path, game)
        assert not made.allowed
        assert any("two different games" in r for r in made.refusals)


def test_two_equal_lines_are_one_record(tmp_path: Path) -> None:
    path = write(tmp_path, line_for("wow-wotlk") * 2 + dump_of("acore_world"))
    assert backup_game(path) == "wow-wotlk"
    assert plan(path, tmp_path, WOTLK).allowed


def test_a_garbled_record_is_refused_not_read_as_no_record(tmp_path: Path) -> None:
    path = write(tmp_path, b"-- yulon-backup: game=\n" + dump_of("acore_world"))
    made = plan(path, tmp_path, WOTLK)
    assert not made.allowed
    assert any("cannot be read" in r for r in made.refusals)


def test_the_record_sits_amid_the_sandbox_directive_a_mariadb_dump_opens_with(
    tmp_path: Path,
) -> None:
    body = (
        line_for("wow-tortoise")
        + b"/*M!999999\\- enable the sandbox mode */\n"
        + dump_of("tw_char")
    )
    path = write(tmp_path, body)
    assert backup_game(path) == "wow-tortoise"


# ----------------------------------------------------- an old backup needs an explicit yes


def test_an_old_backup_with_no_record_is_allowed_only_with_an_acknowledgement(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK)
    assert made.allowed  # it is a question, not a refusal: every backup so far is like this
    assert made.game_unproven


def test_restore_will_not_load_an_old_backup_nobody_vouched_for(tmp_path: Path) -> None:
    path = write(tmp_path, dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK)
    mysql = FakeMysql(("acore_characters",))
    with pytest.raises(MaintenanceError, match="does not say which game it is from"):
        restore(made, mysql, game=WOTLK, confirm=made.token, running=running(DB), now=AT)
    assert mysql.loaded == []
    assert not list(
        (tmp_path / "sql_scripts").glob("**/*pre-restore*")
    )  # nothing was dumped either


def test_an_acknowledged_old_backup_is_restored(tmp_path: Path) -> None:
    path = write(tmp_path, dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK).with_unlabeled_accepted()
    mysql = FakeMysql(("acore_characters",))
    report = restore(made, mysql, game=WOTLK, confirm=made.token, running=running(DB), now=AT)
    assert report.databases == ("acore_characters",)
    assert len(mysql.loaded) == 1


def test_acknowledging_does_not_change_what_the_confirmation_is(tmp_path: Path) -> None:
    path = write(tmp_path, dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK)
    assert made.with_unlabeled_accepted().token == made.token


def test_an_acknowledgement_is_no_cover_for_a_file_that_has_since_been_labelled_for_another_game(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK).with_unlabeled_accepted()
    path.write_bytes(line_for("wow-unbound") + dump_of("acore_characters"))
    # the size changed, so the plan is stale; either way nothing loads
    mysql = FakeMysql(("acore_characters",))
    with pytest.raises(MaintenanceError):
        restore(made, mysql, game=WOTLK, confirm=made.token, running=running(DB), now=AT)
    assert mysql.loaded == []


def test_a_labelled_plan_is_no_cover_for_a_same_size_file_that_has_lost_its_record(
    tmp_path: Path,
) -> None:
    """The token is the file's size and schemas, so a swap of equal length keeps it: the
    engine judges the FRESH read of the file, not the plan's memory of it."""
    record = line_for("wow-wotlk")
    path = write(tmp_path, record + dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK)
    assert made.allowed and not made.game_unproven
    path.write_bytes(b"-- " + b"x" * (len(record) - 4) + b"\n" + dump_of("acore_characters"))
    mysql = FakeMysql(("acore_characters",))
    with pytest.raises(MaintenanceError, match="does not say which game it is from"):
        restore(made, mysql, game=WOTLK, confirm=made.token, running=running(DB), now=AT)
    assert mysql.loaded == []


def test_an_acknowledgement_does_not_unrefuse_a_wrong_game(tmp_path: Path) -> None:
    path = write(tmp_path, line_for("wow-unbound") + dump_of("acore_characters"))
    made = dataclasses.replace(plan(path, tmp_path, WOTLK), unlabeled_accepted=True)
    assert not made.allowed
    mysql = FakeMysql(("acore_characters",))
    with pytest.raises(MaintenanceError, match="will not do it"):
        restore(made, mysql, game=WOTLK, confirm=made.token, running=running(DB), now=AT)
    assert mysql.loaded == []


def test_game_names_come_from_the_catalog_for_a_game_the_file_names(tmp_path: Path) -> None:
    """A file naming a game this build has never heard of is still refused, by its id."""
    path = write(tmp_path, line_for("wow-nonesuch") + dump_of("acore_characters"))
    made = plan(path, tmp_path, WOTLK)
    assert not made.allowed
    assert "wow-nonesuch" in " ".join(made.refusals)


def test_every_catalog_game_has_an_id_the_record_can_hold() -> None:
    """The record's grammar is the catalog's slug grammar, so no real id is unrecordable."""
    for entry in CATALOG.games:
        assert maintenance.GAME_RECORD.fullmatch(f"-- yulon-backup: game={entry.id}"), entry.id


# ------------------------- every way into the engine says which game it is (the bindings)


@dataclasses.dataclass
class Way:
    """One game's route into the shared engine, spelled the way its tab or wrapper spells it."""

    game_id: str
    plan: Callable[[Path], maintenance.RestorePlan]
    restore: Callable[[maintenance.RestorePlan, FakeMysql], object]
    backup: Callable[[FakeMysql], maintenance.BackupReport] | None = None


def _every_way(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[Way]:
    from yulon.controller_wow_centurion import maintenance as centurion
    from yulon.controller_wow_tbc import maintenance as tbc
    from yulon.controller_wow_tortoise import maintenance as tortoise
    from yulon.controller_wow_vanilla import maintenance as vanilla
    from yulon.controller_wow_wotlk import docker_ctl
    from yulon.ui.controller_view import ControllerServices

    # The AzerothCore tab asks Docker what runs, with no seam to hand a census to.
    monkeypatch.setattr(
        docker_ctl,
        "status",
        lambda *, wsl_distro=None: [entry.containers.db for entry in CATALOG.games],
    )

    def up(game_id: str) -> maintenance.RunningNames:
        return running(CATALOG.get(game_id).containers.db)

    cent = CATALOG.get("wow-centurion")
    ways = [
        Way(
            "wow-tbc",
            lambda p: tbc.plan_restore(p, tmp_path, running=up("wow-tbc")),
            lambda pl, m: tbc.restore(pl, m, confirm=pl.token, running=up("wow-tbc"), now=AT),
            lambda m: tbc.backup(tmp_path / "wow-tbc", m, running=up("wow-tbc"), now=AT),
        ),
        Way(
            "wow-vanilla",
            lambda p: vanilla.plan_restore(p, tmp_path, running=up("wow-vanilla")),
            lambda pl, m: vanilla.restore(
                pl, m, confirm=pl.token, running=up("wow-vanilla"), now=AT
            ),
            lambda m: vanilla.backup(
                tmp_path / "wow-vanilla", m, running=up("wow-vanilla"), now=AT
            ),
        ),
        Way(
            "wow-tortoise",
            lambda p: tortoise.plan_restore(p, tmp_path, running=up("wow-tortoise")),
            lambda pl, m: tortoise.restore(
                pl, m, confirm=pl.token, running=up("wow-tortoise"), now=AT
            ),
            lambda m: tortoise.backup(
                tmp_path / "wow-tortoise", m, running=up("wow-tortoise"), now=AT
            ),
        ),
        Way(
            "wow-centurion",
            lambda p: centurion.plan_restore(cent, p, tmp_path, running=up("wow-centurion")),
            lambda pl, m: centurion.restore(
                cent, pl, m, confirm=pl.token, running=up("wow-centurion"), now=AT
            ),
            lambda m: centurion.backup(
                cent, tmp_path / "wow-centurion", m, running=up("wow-centurion"), now=AT
            ),
        ),
    ]
    # The AzerothCore tab binds its own lambdas inside `_for_wotlk()`. The real services are
    # built and asked, so a census that cannot reach Docker adds a refusal and the game
    # refusal is still there beside it.
    for entry_id in ("wow-wotlk", "wow-unbound"):
        services = ControllerServices.for_wotlk(CATALOG.get(entry_id), tmp_path, None)
        ways.append(Way(entry_id, services.plan_restore, lambda pl, m, s=services: s.restore(pl)))
    return ways


def _a_different_game_from(game_id: str) -> str:
    return "wow-wotlk" if game_id == "wow-unbound" else "wow-unbound"


def test_every_way_into_the_engine_refuses_the_other_games_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each binding names ITS game: a binding that forgot would pass a wrong id or none."""
    for way in _every_way(tmp_path, monkeypatch):
        other = _a_different_game_from(way.game_id)
        path = write(tmp_path, line_for(other) + dump_of("acore_characters"), f"{way.game_id}.sql")
        made = way.plan(path)
        said = " ".join(made.refusals)
        assert "will not do it" in said, way.game_id
        assert CATALOG.get(way.game_id).name in said, way.game_id
        mysql = FakeMysql(("acore_characters",))
        with pytest.raises(MaintenanceError, match="will not do it"):
            way.restore(made, mysql)
        assert mysql.loaded == [], way.game_id


def test_every_way_into_the_engine_accepts_its_own_games_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for way in _every_way(tmp_path, monkeypatch):
        path = write(
            tmp_path, line_for(way.game_id) + dump_of("acore_characters"), f"{way.game_id}.sql"
        )
        made = way.plan(path)
        assert not any("will not do it" in r for r in made.refusals), way.game_id
        assert not made.game_unproven, way.game_id


def test_every_per_game_backup_writes_its_own_games_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for way in _every_way(tmp_path, monkeypatch):
        if way.backup is None:
            continue  # the AzerothCore tab's backup is bound inside the tab
        entry = CATALOG.get(way.game_id)
        report = way.backup(FakeMysql((entry.databases.characters,)))
        assert backup_game(report.dumps[0].path) == way.game_id, way.game_id


def test_the_azerothcore_tab_backs_up_as_the_entry_it_was_opened_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui.controller_view import ControllerServices

    asked: list[Game] = []

    def spy(*args: object, game: Game, **kwargs: object) -> str:
        asked.append(game)
        return "done"

    monkeypatch.setattr(maintenance, "backup", spy)
    for entry_id in ("wow-wotlk", "wow-unbound"):
        ControllerServices.for_wotlk(CATALOG.get(entry_id), tmp_path, None).backup()
    assert [g.id for g in asked] == ["wow-wotlk", "wow-unbound"]
    assert [g.name for g in asked] == ["WoW WotLK", "WoW Unbound"]


def test_every_per_game_restore_loads_its_own_games_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`restore()` binds its game apart from `plan_restore()`, and a wrong one there
    refuses a good backup (or waves a bad one through) with the plan none the wiser."""
    for way in _every_way(tmp_path, monkeypatch):
        if way.backup is None:
            continue  # the AzerothCore tab's restore is bound inside the tab; see below
        shutil.rmtree(tmp_path / "sql_scripts", ignore_errors=True)  # one safety copy per stamp
        entry = CATALOG.get(way.game_id)
        database = entry.databases.characters
        path = write(tmp_path, line_for(way.game_id) + dump_of(database), f"own-{way.game_id}.sql")
        made = way.plan(path)
        assert made.allowed, (way.game_id, made.refusals)
        mysql = FakeMysql((database,))
        way.restore(made, mysql)
        assert len(mysql.loaded) == 1, way.game_id


def test_the_azerothcore_tab_plans_and_restores_as_the_entry_it_was_opened_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui.controller_view import ControllerServices

    planned: list[Game] = []
    restored: list[Game] = []

    def plan_spy(path: Path, server_dir: Path, *, game: Game, **kwargs: object) -> str:
        planned.append(game)
        return "plan"

    def restore_spy(plan: object, mysql: object, *, game: Game, **kwargs: object) -> str:
        restored.append(game)
        return "done"

    monkeypatch.setattr(maintenance, "plan_restore", plan_spy)
    monkeypatch.setattr(maintenance, "restore", restore_spy)
    for entry_id in ("wow-wotlk", "wow-unbound"):
        services = ControllerServices.for_wotlk(CATALOG.get(entry_id), tmp_path, None)
        services.plan_restore(tmp_path / "x.sql")
        services.restore(dataclasses.make_dataclass("P", [("token", str)])("t"))  # type: ignore[arg-type]
    assert [g.id for g in planned] == ["wow-wotlk", "wow-unbound"]
    assert [g.id for g in restored] == ["wow-wotlk", "wow-unbound"]
