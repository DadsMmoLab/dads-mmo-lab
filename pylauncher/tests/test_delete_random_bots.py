"""T805: "Delete every random bot…" on the AzerothCore trees' Bots tab (WotLK, Unbound).

The same one-press, backup-first, confirmed action Tortoise's Bots tab has (T144), doing
the deleting itself, over the same `DockerSql` the tab's reads go through, with 8.7a's
world-stopped rule.

The SQL is not read as text here: it is RUN, against an SQLite stand-in for the three
databases (`_Db`), seeded with players, random bots, AddClass and city bots, and every
table the delete touches. What the tests assert is what is left in the tables, so a
statement that deletes the wrong rows fails whatever its text looks like.
"""

from __future__ import annotations

import contextlib
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from tests.conftest import pump_until
from yulon.apply import ApplyError
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import rndbots
from yulon.docker import ServerHeldError
from yulon.manifest import Db
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import UpdateChoice
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
TORTOISE = load_catalog().get("wow-tortoise")
SCHEMAS = WOTLK.schema_map()
AUTH, CHARS, BOTS = SCHEMAS["auth"], SCHEMAS["characters"], SCHEMAS["playerbots"]

CONF = "env/dist/etc/modules/playerbots.conf"

# Accounts: a player, two random bots (type 1), an AddClass bot (2), a city bot (3),
# an rndbot account the module has not assigned (no registry row), and a random-bot
# TYPE whose name is not under the prefix (a player the registry got wrong).
PLAYER, RB1, RB2, ADDCLASS, CITY, UNASSIGNED, MISREGISTERED = 1, 10, 11, 20, 30, 21, 2
ACCOUNTS = {
    PLAYER: "PLAYER",
    RB1: "RNDBOT1",
    RB2: "RNDBOT2",
    ADDCLASS: "RNDBOT50",
    CITY: "RNDBOT70",
    UNASSIGNED: "RNDBOT60",
    MISREGISTERED: "ALICE",
}
REGISTRY = {RB1: 1, RB2: 1, ADDCLASS: 2, CITY: 3, MISREGISTERED: 1}

# Characters: guid -> account.
HUMAN, BOT_A, BOT_B, BOT_C, ADD_BOT, LOOSE_BOT, CITY_BOT, ALICE = (
    100,
    101,
    102,
    103,
    120,
    121,
    130,
    140,
)
CHARACTERS = {
    HUMAN: PLAYER,
    BOT_A: RB1,
    BOT_B: RB1,
    BOT_C: RB2,
    ADD_BOT: ADDCLASS,
    LOOSE_BOT: UNASSIGNED,
    CITY_BOT: CITY,
    ALICE: MISREGISTERED,
}
DOOMED = {BOT_A, BOT_B, BOT_C}
SURVIVORS = set(CHARACTERS) - DOOMED

_EXTRA_COLUMNS = {
    "characters": ("account", "name"),
    "character_achievement": ("achievement",),
    "character_pet": ("id", "owner"),
    "character_pet_declinedname": ("id", "owner"),
    "guild": ("guildid", "leaderguid"),
    "guild_member": ("guildid", "guid", "rank"),
    "guild_bank_item": ("guildid", "item_guid"),
    "guild_eventlog": ("guildid", "PlayerGuid1", "PlayerGuid2"),
    "guild_bank_eventlog": ("guildid", "PlayerGuid"),
    "arena_team": ("arenaTeamId", "captainGuid"),
    "arena_team_member": ("arenaTeamId", "guid"),
    "groups": ("guid", "leaderGuid"),
    "group_member": ("guid", "memberGuid"),
    "auctionhouse": ("id", "itemguid", "itemowner"),
    "item_instance": ("guid", "owner_guid"),
}


def _layout() -> dict[str, dict[str, list[str]]]:
    """schema -> table -> columns: every table the delete may write, and the ones it reads."""
    chars: dict[str, list[str]] = {"characters": ["guid"]}

    def add(table: str, *columns: str) -> None:
        have = chars.setdefault(table, [])
        for column in (*_EXTRA_COLUMNS.get(table, ()), *columns):
            if column not in have:
                have.append(column)

    for table, column, _extra in rndbots.CHARACTER_ROWS:
        add(table, column)
    for table, column in rndbots.PET_ROWS:
        add(table, column)
    for table in rndbots.GUILD_ROWS:
        add(table, "guildid")
    for table, column in rndbots.ACCOUNT_CHARACTER_ROWS:
        add(table, column)
    for table in ("character_pet", "arena_team", "groups", "auctionhouse", "item_instance"):
        add(table)
    add("characters")
    auth = {
        "account": ["id", "username"],
        "account_access": ["id", "gmlevel"],
        **{table: [column] for table, column in rndbots.AUTH_ROWS},
    }
    bots: dict[str, list[str]] = {"playerbots_account_type": ["account_id", "account_type"]}
    for table, column in (*rndbots.BOT_CHARACTER_ROWS, *rndbots.BOT_ACCOUNT_ROWS):
        bots.setdefault(table, [])
        if column not in bots[table]:
            bots[table].append(column)
    return {AUTH: auth, CHARS: chars, BOTS: bots}


class _Db:
    """`DockerSql` over SQLite: the three schemas attached under their real names.

    `run_statement` runs the script a line at a time as mysql does, `START TRANSACTION`
    as SQLite's `BEGIN`, and a failure at line `fail_at` ends the "session" the way
    mysql's does: the open transaction is rolled back, then the error is raised.
    `engines` overrides what `information_schema` reports for a table.
    """

    def __init__(
        self,
        *,
        fail_at: int | None = None,
        engines: dict[str, str] | None = None,
        drop: tuple[str, ...] = (),
    ) -> None:
        self.conn = sqlite3.connect(":memory:", isolation_level=None)
        self.layout = _layout()
        for schema, tables in self.layout.items():
            self.conn.execute(f"ATTACH DATABASE ':memory:' AS `{schema}`")
            for table, columns in tables.items():
                if table in drop:
                    continue
                cols = ", ".join(
                    f"`{c}` {'TEXT' if c == 'username' else 'INTEGER'}" for c in columns
                )
                self.conn.execute(f"CREATE TABLE `{schema}`.`{table}` ({cols})")
        self.dropped = set(drop)
        self.fail_at = fail_at
        self.engines = engines or {}
        self.scripts: list[tuple[Db, str]] = []
        self.queries: list[tuple[Db, str]] = []

    def query(self, db: Db, statement: str) -> str:
        self.queries.append((db, statement))
        if "information_schema.TABLES" in statement:
            return "\n".join(
                f"{schema}\t{table}\t{self.engines.get(table, 'InnoDB')}"
                for schema, tables in self.layout.items()
                for table in tables
                if table not in self.dropped
            )
        rows = self.conn.execute(statement.rstrip().rstrip(";")).fetchall()
        return "\n".join("\t".join(str(v) for v in row) for row in rows)

    def run_statement(self, db: Db, statement: str) -> None:
        self.scripts.append((db, statement))
        for index, line in enumerate(statement.split("\n")):
            if index == self.fail_at:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise ApplyError(f"mysql failed at line {index + 1}")
            self.conn.execute("BEGIN" if line == "START TRANSACTION;" else line)

    # -- seeding and reading back -------------------------------------------------

    def insert(self, schema: str, table: str, **values: object) -> None:
        if table in self.dropped:
            return
        columns = self.layout[schema][table]
        row = [values.get(c, 0) for c in columns]
        marks = ", ".join("?" for _ in columns)
        self.conn.execute(f"INSERT INTO `{schema}`.`{table}` VALUES ({marks})", row)

    def rows(self, schema: str, table: str, where: str = "1") -> list[tuple[object, ...]]:
        return sorted(
            self.conn.execute(f"SELECT * FROM `{schema}`.`{table}` WHERE {where}").fetchall(),
            key=repr,
        )

    def snapshot(self) -> dict[tuple[str, str], list[tuple[object, ...]]]:
        return {
            (schema, table): self.rows(schema, table)
            for schema, tables in self.layout.items()
            for table in tables
            if table not in self.dropped
        }


def _seeded(**kw: object) -> _Db:
    db = _Db(**kw)  # type: ignore[arg-type]
    for account, name in ACCOUNTS.items():
        db.insert(AUTH, "account", id=account, username=name)
        db.insert(AUTH, "account_access", id=account, gmlevel=0)
        for table, column in rndbots.AUTH_ROWS:
            db.insert(AUTH, table, **{column: account})
        for table, column in rndbots.ACCOUNT_CHARACTER_ROWS:
            db.insert(CHARS, table, **{column: account})
        db.insert(BOTS, "playerbots_random_bots", owner=account, bot=0)
    for account, kind in REGISTRY.items():
        db.insert(BOTS, "playerbots_account_type", account_id=account, account_type=kind)
    for guid, account in CHARACTERS.items():
        db.insert(CHARS, "characters", guid=guid, account=account, name=f"c{guid}")
        for table, column, _extra in rndbots.CHARACTER_ROWS:
            db.insert(CHARS, table, **{column: guid, "achievement": 1})
        for table, column in rndbots.BOT_CHARACTER_ROWS:
            db.insert(BOTS, table, **{column: guid})
    # A realm first the core keeps, as CHAR_DEL_CHAR_ACHIEVEMENTS does.
    db.insert(CHARS, "character_achievement", guid=BOT_A, achievement=457)
    # Pets: one of a bot, one of the player.
    for pet, owner in ((500, BOT_A), (501, HUMAN)):
        db.insert(CHARS, "character_pet", id=pet, owner=owner)
        for table, column in rndbots.PET_ROWS:
            db.insert(CHARS, table, **{column: pet})
    # Guild 1: led by a bot, with the player (rank 2) and the AddClass bot (rank 1):
    # the AddClass bot inherits it. Guild 2: only a bot, disbanded with its bank.
    # Guild 3: the player's, with a bot member.
    db.insert(CHARS, "guild", guildid=1, leaderguid=BOT_A)
    db.insert(CHARS, "guild_member", guildid=1, guid=HUMAN, rank=2)
    db.insert(CHARS, "guild_member", guildid=1, guid=ADD_BOT, rank=1)
    db.insert(CHARS, "guild", guildid=2, leaderguid=BOT_C)
    db.insert(CHARS, "guild_member", guildid=2, guid=BOT_C, rank=0)
    db.insert(CHARS, "guild_bank_item", guildid=2, item_guid=900)
    db.insert(CHARS, "item_instance", guid=900, owner_guid=0)
    for table in ("guild_bank_tab", "guild_bank_right", "guild_rank"):
        db.insert(CHARS, table, guildid=2)
        db.insert(CHARS, table, guildid=1)
    db.insert(CHARS, "guild", guildid=3, leaderguid=HUMAN)
    # Arena team 1: a bot captain and the player; team 2: a bot alone.
    db.insert(CHARS, "arena_team", arenaTeamId=1, captainGuid=BOT_A)
    db.insert(CHARS, "arena_team_member", arenaTeamId=1, guid=HUMAN)
    db.insert(CHARS, "arena_team", arenaTeamId=2, captainGuid=BOT_C)
    db.insert(CHARS, "arena_team_member", arenaTeamId=2, guid=BOT_C)
    # Group 7 led by a bot (disbanded, the player's membership with it); 8 by the player.
    db.insert(CHARS, "groups", guid=7, leaderGuid=BOT_A)
    db.insert(CHARS, "group_member", guid=7, memberGuid=HUMAN)
    db.insert(CHARS, "groups", guid=8, leaderGuid=HUMAN)
    db.insert(CHARS, "group_member", guid=8, memberGuid=HUMAN)
    # Auctions: a bot's (its item held with no owner, as the auction house keeps it) and
    # the player's.
    db.insert(CHARS, "auctionhouse", id=1, itemguid=800, itemowner=BOT_A)
    db.insert(CHARS, "item_instance", guid=800, owner_guid=0)
    db.insert(CHARS, "auctionhouse", id=2, itemguid=801, itemowner=HUMAN)
    db.insert(CHARS, "item_instance", guid=801, owner_guid=0)
    return db


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the view's background jobs synchronously, as every view test here does."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _conf(server_dir: Path, text: str = "AiPlayerbot.RandomBotAccountPrefix = rndbot\n") -> Path:
    path = server_dir / CONF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class _Alone:
    """The database-alone pair, recording what came up and what went down."""

    def __init__(self, started: bool = False) -> None:
        self.started = started
        self.because: list[str] = []
        self.taken_down = 0

    def bring_up(self, because: str) -> bool:
        self.because.append(because)
        return self.started

    def take_down(self) -> None:
        self.taken_down += 1


class _Holds:
    """The server hold, recording its press."""

    def __init__(self) -> None:
        self.presses: list[str] = []

    def __call__(self, press: str) -> contextlib.AbstractContextManager[object]:
        self.presses.append(press)
        return contextlib.nullcontext()


class _Backup:
    """The view's backup seam, raising when asked to."""

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def __call__(self) -> object:
        self.calls += 1
        if self.fail:
            raise RuntimeError("mysqldump said no")
        return None


def _seam(
    tmp_path: Path,
    sql: object,
    *,
    running: object = False,
    alone: _Alone | None = None,
    holds: object = None,
    entry: object = WOTLK,
) -> rndbots.RndbotDelete:
    if not (tmp_path / CONF).exists():
        _conf(tmp_path)
    return rndbots.RndbotDelete(
        entry=entry,  # type: ignore[arg-type]
        server_dir=tmp_path,
        sql=sql,  # type: ignore[arg-type]
        world_running=lambda: running,  # type: ignore[arg-type,return-value]
        alone=alone if alone is not None else _Alone(),  # type: ignore[arg-type]
        hold_server=holds if holds is not None else _Holds(),  # type: ignore[arg-type]
    )


def _delete(seam: rndbots.RndbotDelete, **kw: object) -> list[str]:
    return list(seam.delete(**kw))  # type: ignore[arg-type]


def _guids(db: _Db, schema: str, table: str, column: str) -> set[int]:
    return {int(v) for (v,) in db.conn.execute(f"SELECT `{column}` FROM `{schema}`.`{table}`")}


# -- what is deleted, and what stays ----------------------------------------------


def test_the_random_bots_go_and_nothing_else_does(tmp_path: Path) -> None:
    db = _seeded()
    lines = _delete(_seam(tmp_path, db))

    assert _guids(db, CHARS, "characters", "guid") == SURVIVORS
    assert _guids(db, AUTH, "account", "id") == set(ACCOUNTS) - {RB1, RB2}
    assert any(
        line.startswith("Deleted 3 random-bot characters and 2 bot accounts") for line in lines
    )
    assert any("3 other bot accounts" in line for line in lines), lines


def test_an_addclass_or_city_account_under_the_prefix_is_never_deleted(tmp_path: Path) -> None:
    """Both arms are joined by AND: the prefix alone does not doom an account (fails if OR)."""
    db = _seeded()
    _delete(_seam(tmp_path, db))

    accounts = _guids(db, AUTH, "account", "id")
    assert {ADDCLASS, CITY, UNASSIGNED} <= accounts, "rndbot accounts that are not random bots"
    assert MISREGISTERED in accounts, "a type-1 row alone does not doom a player's account"
    chars = _guids(db, CHARS, "characters", "guid")
    assert {ADD_BOT, CITY_BOT, LOOSE_BOT, ALICE} <= chars
    for table, column in rndbots.AUTH_ROWS:
        assert {ADDCLASS, CITY} <= _guids(db, AUTH, table, column), table
    assert {ADDCLASS, CITY} <= _guids(db, BOTS, "playerbots_account_type", "account_id")


def test_every_row_a_deleted_character_owned_goes_with_it(tmp_path: Path) -> None:
    """No orphans for the next bot with a reused guid to inherit (Player::DeleteFromDB)."""
    db = _seeded()
    _delete(_seam(tmp_path, db))

    for table, column, extra in rndbots.CHARACTER_ROWS:
        left = _guids(db, CHARS, table, column) & DOOMED
        if table == "character_achievement":
            # The core keeps realm firsts; the ordinary achievement row went.
            kept = db.conn.execute(
                f"SELECT achievement FROM `{CHARS}`.`{table}` WHERE guid = {BOT_A}"
            ).fetchall()
            assert kept == [(457,)], extra
            continue
        assert not left, f"{table}.{column} still holds {left}"
        assert SURVIVORS <= _guids(db, CHARS, table, column), f"{table} lost a survivor's row"
    for table, column in rndbots.BOT_CHARACTER_ROWS:
        assert not _guids(db, BOTS, table, column) & DOOMED, table
    for table, column in rndbots.ACCOUNT_CHARACTER_ROWS:
        assert _guids(db, CHARS, table, column) == set(ACCOUNTS) - {RB1, RB2}, table
    for table, column in rndbots.AUTH_ROWS:
        assert _guids(db, AUTH, table, column) == set(ACCOUNTS) - {RB1, RB2}, table
    assert _guids(db, AUTH, "account_access", "id") == set(ACCOUNTS) - {RB1, RB2}
    owners = _guids(db, BOTS, "playerbots_random_bots", "owner") - {0}  # 0: the by-bot rows
    assert owners == set(ACCOUNTS) - {RB1, RB2}


def test_pets_auctions_and_their_items_go(tmp_path: Path) -> None:
    db = _seeded()
    _delete(_seam(tmp_path, db))

    assert _guids(db, CHARS, "character_pet", "id") == {501}
    for table, column in rndbots.PET_ROWS:
        pets = _guids(db, CHARS, table, column)
        assert 500 not in pets and 501 in pets, table
    assert _guids(db, CHARS, "auctionhouse", "id") == {2}
    items = _guids(db, CHARS, "item_instance", "guid")
    assert 800 not in items and 801 in items, "the bot's auctioned item, not the player's"


def test_a_bots_guild_passes_to_the_best_ranked_member_left_or_is_disbanded(
    tmp_path: Path,
) -> None:
    db = _seeded()
    _delete(_seam(tmp_path, db))

    assert db.rows(CHARS, "guild") == [(1, ADD_BOT), (3, HUMAN)]
    assert db.rows(CHARS, "guild_member", "guildid = 1") == [(1, HUMAN, 2), (1, ADD_BOT, 0)]
    assert db.rows(CHARS, "guild_member", "guildid = 2") == []
    for table in ("guild_bank_item", "guild_bank_tab", "guild_bank_right", "guild_rank"):
        assert 2 not in _guids(db, CHARS, table, "guildid"), table
    assert 1 in _guids(db, CHARS, "guild_rank", "guildid")
    assert 900 not in _guids(db, CHARS, "item_instance", "guid"), "the disbanded bank's item"


def test_arena_teams_and_groups_led_by_a_bot(tmp_path: Path) -> None:
    db = _seeded()
    _delete(_seam(tmp_path, db))

    assert db.rows(CHARS, "arena_team") == [(1, HUMAN)], "captaincy passes; a team of none goes"
    assert db.rows(CHARS, "arena_team_member", "arenaTeamId IN (1, 2)") == [(1, HUMAN)]
    assert _guids(db, CHARS, "groups", "guid") == {8}
    assert db.rows(CHARS, "group_member", "guid IN (7, 8)") == [(8, HUMAN)]


def test_the_environment_prefix_is_the_one_deleted_by(tmp_path: Path) -> None:
    """`AC_…_RANDOM_BOT_ACCOUNT_PREFIX` beats the conf and the catalog default (finding A)."""
    db = _seeded()
    db.insert(AUTH, "account", id=40, username="ENVBOT1")
    db.insert(BOTS, "playerbots_account_type", account_id=40, account_type=1)
    db.insert(CHARS, "characters", guid=150, account=40, name="env")
    _conf(tmp_path, "AiPlayerbot.RandomBotAccountPrefix = rndbot\n")
    world = WOTLK.container_spec().world
    (tmp_path / "docker-compose.override.yml").write_text(
        f"services:\n  {world}:\n    environment:\n"
        '      AC_AI_PLAYERBOT_RANDOM_BOT_ACCOUNT_PREFIX: "envbot"\n'
    )
    _delete(_seam(tmp_path, db))

    assert 40 not in _guids(db, AUTH, "account", "id")
    assert 150 not in _guids(db, CHARS, "characters", "guid")
    assert {RB1, RB2} <= _guids(db, AUTH, "account", "id"), "the conf's prefix is not in force"


def test_an_underscore_in_the_prefix_is_a_letter_not_a_wildcard(tmp_path: Path) -> None:
    """`rnd_bot` deletes `RND_BOT1`, never `RNDXBOT1`, though both are registered type 1.

    Run on SQLite, whose LIKE also reads `_` as any one character: a bare `LIKE 'RND_BOT%'`
    deletes both, so this fails if the predicate goes back to plain LIKE.
    """
    db = _seeded()
    for account, name, guid in ((50, "RND_BOT1", 160), (51, "RNDXBOT1", 161)):
        db.insert(AUTH, "account", id=account, username=name)
        db.insert(BOTS, "playerbots_account_type", account_id=account, account_type=1)
        db.insert(CHARS, "characters", guid=guid, account=account, name=f"c{guid}")
    _conf(tmp_path, "AiPlayerbot.RandomBotAccountPrefix = rnd_bot\n")
    lines = _delete(_seam(tmp_path, db))

    accounts = _guids(db, AUTH, "account", "id")
    assert 50 not in accounts and 160 not in _guids(db, CHARS, "characters", "guid")
    assert 51 in accounts, "an account matched only through `_` as a wildcard was deleted"
    assert 161 in _guids(db, CHARS, "characters", "guid")
    assert {RB1, RB2} <= accounts, "the `rndbot` accounts are not under `rnd_bot`"
    assert any(
        line.startswith("Deleted 1 random-bot character and 1 bot account") for line in lines
    )


def test_an_environment_prefix_it_cannot_read_refuses_every_write(tmp_path: Path) -> None:
    db = _seeded()
    world = WOTLK.container_spec().world
    (tmp_path / "docker-compose.override.yml").write_text(
        f"services:\n  {world}:\n    environment:\n"
        "      - AC_AI_PLAYERBOT_RANDOM_BOT_ACCOUNT_PREFIX=listbot\n"
    )
    before = db.snapshot()
    with pytest.raises(rndbots.RndbotDeleteError, match="No bot was deleted"):
        _delete(_seam(tmp_path, db))
    assert db.scripts == [] and db.snapshot() == before


# -- all or nothing ------------------------------------------------------------------


def test_the_delete_is_one_statement_batch_in_one_transaction(tmp_path: Path) -> None:
    db = _seeded()
    _delete(_seam(tmp_path, db))

    assert len(db.scripts) == 1, "one mysql, one connection"
    lines = db.scripts[0][1].split("\n")
    assert lines[0] == "START TRANSACTION;" and lines[-1] == "COMMIT;"
    assert db.scripts[0][0] == "characters"


def test_a_failure_at_any_statement_deletes_nothing(tmp_path: Path) -> None:
    """Failure injected at every line of the script: the tables are as they were."""
    probe = _seeded()
    _delete(_seam(tmp_path, probe))
    length = len(probe.scripts[0][1].split("\n"))
    assert length > 50
    for fail_at in range(length):
        db = _seeded(fail_at=fail_at)
        before = db.snapshot()
        with pytest.raises(rndbots.RndbotDeleteError, match="No bot was deleted") as raised:
            _delete(_seam(tmp_path, db))
        assert db.snapshot() == before, f"a failure at line {fail_at + 1} left a partial delete"
        assert "rolled the whole of it back" in str(raised.value)


def test_a_table_that_cannot_roll_back_is_a_refusal(tmp_path: Path) -> None:
    db = _seeded(engines={"playerbots_db_store": "MyISAM"})
    before = db.snapshot()
    with pytest.raises(rndbots.RndbotDeleteError, match="MyISAM table"):
        _delete(_seam(tmp_path, db))
    assert db.scripts == [] and db.snapshot() == before


def test_a_server_without_the_registry_is_refused(tmp_path: Path) -> None:
    """Without `playerbots_account_type` a random bot cannot be told from an AddClass bot."""
    db = _seeded(drop=("playerbots_account_type",))
    with pytest.raises(rndbots.RndbotDeleteError, match="is not there"):
        _delete(_seam(tmp_path, db))
    assert db.scripts == []


def test_a_missing_optional_table_is_skipped_not_a_failure(tmp_path: Path) -> None:
    """An older core without a table loses only what it has."""
    db = _seeded(drop=("character_settings", "playerbots_preferred_mounts"))
    _delete(_seam(tmp_path, db))
    script = db.scripts[0][1]
    assert "`character_settings`" not in script and "playerbots_preferred_mounts" not in script
    assert _guids(db, CHARS, "characters", "guid") == SURVIVORS


def test_nothing_to_delete_writes_nothing(tmp_path: Path) -> None:
    db = _Db()
    lines = _delete(_seam(tmp_path, db))
    assert db.scripts == []
    assert lines == ["There are no random bots to delete."]


# -- the order of the job ------------------------------------------------------------


def test_the_hold_is_taken_before_the_checks_and_the_backup(tmp_path: Path) -> None:
    db = _seeded()
    order: list[str] = []
    holds = _Holds()

    def noted_hold(press: str) -> contextlib.AbstractContextManager[object]:
        order.append("hold")
        return holds(press)

    def running() -> bool:
        order.append("world?")
        return False

    def backup() -> object:
        order.append("backup")
        return None

    alone = _Alone()

    def bring_up(because: str) -> bool:
        order.append("database up")
        return alone.bring_up(because)

    seam = replace(
        _seam(tmp_path, db, holds=noted_hold),
        world_running=running,
        alone=rndbots.DatabaseAlone(bring_up, alone.take_down),
    )
    lines = _delete(seam, backup=backup)
    assert order == ["hold", "world?", "backup", "database up", "world?"]
    assert any("Backing up" in line for line in lines)


def test_a_failing_backup_stops_before_anything_is_deleted(tmp_path: Path) -> None:
    db = _seeded()
    with pytest.raises(RuntimeError, match="mysqldump said no"):
        _delete(_seam(tmp_path, db), backup=_Backup(fail=True))
    assert db.scripts == [], "nothing was written over a failed backup"


def test_a_stopped_world_is_the_only_state_that_deletes(tmp_path: Path) -> None:
    db = _seeded()
    with pytest.raises(rndbots.RndbotDeleteError, match="Press Stop on the Server tab"):
        _delete(_seam(tmp_path, db, running=True))
    assert db.scripts == [], "nothing was deleted into a running world"


def test_an_unknown_world_refuses_too(tmp_path: Path) -> None:
    """Docker not answering is not "stopped" (8.7a's fails-closed reading)."""
    db = _seeded()
    with pytest.raises(rndbots.RndbotDeleteError, match="Could not tell whether the server"):
        _delete(_seam(tmp_path, db, running=None))
    assert db.scripts == []


def test_a_world_started_while_the_backup_ran_is_refused_before_the_writes(
    tmp_path: Path,
) -> None:
    """The guard is asked twice: before the backup and after it (the applier's rule)."""
    db = _seeded()
    answers = [False, True]  # stopped before the backup, running after it
    seam = replace(_seam(tmp_path, db), world_running=lambda: answers.pop(0))
    backup = _Backup()
    with pytest.raises(rndbots.RndbotDeleteError, match="Press Stop"):
        _delete(seam, backup=backup)
    assert backup.calls == 1 and db.scripts == []


def test_the_database_started_for_the_deletes_is_put_back_down(tmp_path: Path) -> None:
    alone = _Alone(started=True)
    _delete(_seam(tmp_path, _seeded(), alone=alone))
    assert alone.because == ["no bot was deleted"], "the sentence names what was not done"
    assert alone.taken_down == 1


def test_a_database_that_was_already_up_is_not_stopped(tmp_path: Path) -> None:
    alone = _Alone(started=False)
    _delete(_seam(tmp_path, _seeded(), alone=alone))
    assert alone.taken_down == 0


def test_the_deletes_run_under_the_servers_hold(tmp_path: Path) -> None:
    holds = _Holds()
    _delete(_seam(tmp_path, _seeded(), holds=holds))
    assert holds.presses == [rndbots.PRESS_DELETE_BOTS]


def test_a_delete_while_another_yulon_holds_the_server_writes_nothing(tmp_path: Path) -> None:
    """The hold refuses (another Yu'lon owns the server): no count, no DELETE, nothing sent."""

    def refused(press: str) -> contextlib.AbstractContextManager[object]:
        raise ServerHeldError(f"another Yu'lon is holding this server; {press} did not run")

    db = _seeded()
    with pytest.raises(ServerHeldError):
        _delete(_seam(tmp_path, db, holds=refused))
    assert db.scripts == [] and db.queries == []


def test_a_cancel_before_the_transaction_deletes_nothing(tmp_path: Path) -> None:
    import threading

    db = _seeded()
    cancel = threading.Event()
    cancel.set()
    lines = _delete(_seam(tmp_path, db), cancel=cancel)  # an event ALREADY set
    assert db.scripts == []
    assert any("Stopped before anything was deleted" in line for line in lines)


def test_a_broken_prefix_is_a_refusal_not_a_write(tmp_path: Path) -> None:
    db = _seeded()
    _conf(tmp_path, 'AiPlayerbot.RandomBotAccountPrefix = "rnd\'bot"\n')
    with pytest.raises(rndbots.RndbotDeleteError, match="cannot be used in a query"):
        _delete(_seam(tmp_path, db))
    assert db.scripts == []


def test_the_counts_that_are_not_numbers_are_a_refusal(tmp_path: Path) -> None:
    db = _seeded()
    original = db.query

    def broken(dbname: Db, statement: str) -> str:
        if "information_schema.TABLES" not in statement:
            return "junk"
        return original(dbname, statement)

    db.query = broken  # type: ignore[method-assign]
    with pytest.raises(rndbots.RndbotDeleteError, match="not a number"):
        _delete(_seam(tmp_path, db))
    assert db.scripts == []


# -- the wiring ----------------------------------------------------------------------


def test_the_factory_returns_none_without_a_measured_marker() -> None:
    from types import SimpleNamespace

    assert (
        rndbots.for_entry(
            SimpleNamespace(observability=None),  # type: ignore[arg-type]
            Path("/nowhere"),
            sql=_Db(),
            world_running=lambda: False,
            alone=_Alone(),
            hold_server=_Holds(),
        )
        is None
    )


def test_the_delete_is_wired_on_the_azerothcore_trees_not_the_cmangos_ones(
    qapp: object, tmp_path: Path
) -> None:
    """Both WotLK entries draw the press; the CMaNGOS trees still draw Tortoise's or none."""
    from tests.test_controller_view import _Ps, _services

    for entry, wants_delete, wants_rebuild in (
        (WOTLK, True, False),
        (TORTOISE, False, True),
    ):
        services = _services(_Ps(), tmp_path, [])
        if entry is TORTOISE:
            services = replace(services, bot_pool_rebuild=object())
        else:
            services = replace(
                services,
                bot_rndbot_delete=rndbots.for_entry(
                    entry,
                    tmp_path,
                    sql=_Db(),
                    world_running=lambda: False,
                    alone=_Alone(),
                    hold_server=_Holds(),
                ),
            )
        view = controller_view_module.ControllerView(
            entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
        )
        label = view.bot_rebuild_button.text() if view.bot_rebuild_button else None
        if wants_delete:
            assert label == "Delete every random bot…", entry.id
        if wants_rebuild:
            assert label == "Rebuild random bots…", entry.id


def test_the_real_wotlk_factory_wires_the_delete_and_the_button(
    qapp: object, tmp_path: Path
) -> None:
    """The press rides the REAL `ControllerServices.for_entry()` wiring, not a test's replace.

    The factory's wiring is what a player gets; a seam only ever handed to the view by a
    test would draw a button on no real server.
    """
    from tests.test_characters_tab import _view as people_view

    view = people_view(tmp_path)
    assert view.services.bot_rndbot_delete is not None
    assert view.bot_rebuild_button is not None
    assert view.bot_rebuild_button.text() == "Delete every random bot…"


# -- the tab -------------------------------------------------------------------------


class _DeleteSeam:
    """The Bots tab's delete seam, recording what the view asked of it."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls = 0
        self.backups: list[object] = []
        self.fail = fail

    def delete(self, *, backup: object = None, cancel: object = None) -> object:
        self.calls += 1
        self.backups.append(backup)
        if self.fail:
            raise rndbots.RndbotDeleteError(self.fail)
        yield "Deleted 2 random-bot characters and 2 bot accounts."


def _view(
    tmp_path: Path,
    seam: object | None,
    entry: object = WOTLK,
    **extra: object,
) -> controller_view_module.ControllerView:
    from tests.test_controller_view import _Ps, _services

    services = replace(
        _services(_Ps(), tmp_path, []),
        bot_rndbot_delete=seam,
        **extra,
    )
    return controller_view_module.ControllerView(
        entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
    )


def _wait(view: object) -> None:
    """Until the panel's job has ended AND its finish handler has run (which unlocks `_busy`)."""
    log = view.bot_rebuild_log
    assert log is not None
    pump_until(lambda: not log.running and not view._busy, "the delete job finished")


def _answer(monkeypatch: pytest.MonkeyPatch, which: object) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    def question(_parent: object, title: str, text: str, **_kw: object) -> UpdateChoice:
        asked.append((title, text))
        return which  # type: ignore[return-value]

    monkeypatch.setattr(controller_view_module, "ask_backup_choice", question)
    return asked


def test_the_press_runs_the_job_with_or_without_the_backup(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for answer, backs_up in (
        (UpdateChoice.BACK_UP_FIRST, True),
        (UpdateChoice.WITHOUT_BACKUP, False),
    ):
        _answer(monkeypatch, answer)
        seam = _DeleteSeam()
        view = _view(tmp_path, seam)
        assert view.delete_random_bots() is True
        _wait(view)
        assert seam.calls == 1
        assert (seam.backups[0] is not None) is backs_up
        assert "Deleted 2 random-bot characters" in view.bot_rebuild_log.text()  # type: ignore[union-attr]


def test_cancel_escape_and_the_close_button_do_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seam = _DeleteSeam()
    view = _view(tmp_path, seam)
    for answer in (UpdateChoice.CANCEL, UpdateChoice.CANCEL, UpdateChoice.CANCEL):
        _answer(monkeypatch, answer)
        assert view.delete_random_bots() is False
    assert seam.calls == 0


def test_the_dialog_names_the_rule_and_the_default_is_cancel(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _answer(monkeypatch, UpdateChoice.CANCEL)
    view = _view(tmp_path, _DeleteSeam())
    assert view.delete_random_bots() is False
    assert len(asked) == 1
    title, text = asked[0]
    assert title == "Delete every random bot?"
    for said in ("random bot", "AddClass and city bots", "must be stopped", "nothing is deleted"):
        assert said in text, said


def test_a_refusal_is_put_where_the_player_is_looking(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, UpdateChoice.WITHOUT_BACKUP)
    seam = _DeleteSeam(fail="The server is running: press Stop on the Server tab, then again.")
    view = _view(tmp_path, seam)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.delete_random_bots()
    _wait(view)
    assert "press Stop" in view.bot_rebuild_report.text()  # type: ignore[union-attr]
    assert failures and "press Stop" in failures[0]


def test_a_press_without_the_seam_is_false_and_draws_nothing(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path, None)
    assert view.bot_rebuild_button is None
    assert view.delete_random_bots() is False


def test_the_press_is_refused_while_another_job_writes_this_server(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boxes = _answer(monkeypatch, UpdateChoice.BACK_UP_FIRST)
    seam = _DeleteSeam()
    view = _view(tmp_path, seam)
    view._restore_running = True
    assert view.delete_random_bots() is False
    assert seam.calls == 0 and boxes == [], "refused before the question"
