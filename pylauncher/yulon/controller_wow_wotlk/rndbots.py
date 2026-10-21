"""The Bots tab's "Delete every random bot…" (T805), on the AzerothCore trees (WotLK, Unbound).

Tortoise's "Rebuild random bots…" (`controller_wow_tortoise.poolreset`) asks the bots
MODULE to delete its own pool. mod-playerbots has a switch of that kind too,
`Playerbots.DeleteRandomBotAccounts` (`RandomPlayerbotFactory::CreateRandomBots`, read at
79bd428), and it is deliberately NOT used: it deletes EVERY account under the prefix,
the AddClass pool (account type 2) included -- the conf's own words are "Prefix for
created bot accounts (of any type)" -- it needs a world start with the key set and a
second start with it cleared, and its deletes are queued one by one with no
transaction. So the deleting happens HERE, through the same `DockerSql` the tab's reads
go through (the write half, `run_statement()`).

**Which bots.** An account is deleted only when BOTH are true of it: its name starts
with the bot prefix in force (`dbreads.resolve_marker`: the world's environment, then
`playerbots.conf`, then the module default -- the order AzerothCore reads them in), AND
the module registered it as a random bot (`playerbots_account_type`, type 1). AddClass
bots (2), city bots (3) and accounts the module has not assigned (0, or no row) are kept,
and so is every player's account. A server whose module keeps no such registry is
refused: without it a random bot and an AddClass bot cannot be told apart.

**What goes with them.** AzerothCore's tables have no foreign keys, and a new character
takes its guid from MAX(guid) + 1, so a row left behind is one the next bot inherits.
The characters' rows go the way `Player::DeleteFromDB` (CHAR_DELETE_REMOVE) and
`AccountMgr::DeleteAccount` take them, read in azerothcore-wotlk 4f66bddb, table by
table, plus what those two leave to the running server (pets' rows, guild and arena
membership, groups, petitions, the bots' auctions) and the bots module's own rows. A
guild a deleted bot leads passes to its highest-ranked member left (`Guild::DeleteMember`);
one with nobody left is disbanded with its bank. An arena team does the same.

**One transaction.** Every statement goes to ONE `mysql` in ONE `run_statement()`,
between `START TRANSACTION` and `COMMIT`. mysql stops at the first error and the server
rolls the open transaction back when the connection ends, so a failure deletes nothing.
That holds only for transactional tables, so every table the delete writes is checked to
be InnoDB first (AzerothCore's and mod-playerbots' own definitions all are) and any other
engine is a refusal. A stop asked for is honoured up to the moment the transaction is
sent, and not inside it.

The world-stopped rule is 8.7a's, the one `apply.Applier` enforces for direct SQL: ONLY
the terminal container statuses count as stopped, `docker.world_running()` is asked,
and anything short of a clear "no" refuses. All of it runs under the server's hold; the
database, when it has to, is brought up alone and put back down, the way Backup does it
(`DatabaseAlone`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from yulon.catalog.catalog import BotRegistry, CatalogEntry
from yulon.dbreads import Marker, prefix_match, resolve_marker
from yulon.manifest import Db

PRESS_DELETE_BOTS = "Delete the random bots"
"""This job's press on the server's cross-process reservation (`docker.server_hold`)."""

RANDOM_BOT_TYPES: tuple[int, ...] = (1,)
"""`playerbots_account_type.account_type` of a random bot: 0 unassigned, 1 RNDbot, 2 AddClass."""

TRANSACTIONAL_ENGINES = frozenset({"innodb"})
"""Engines a rollback undoes. A MyISAM table keeps a delete whatever the transaction does."""

# The doomed ids, collected once at the start of the transaction. MySQL cannot open one
# TEMPORARY table twice in a statement (ER_CANT_REOPEN_TABLE), so the statements that
# need the characters twice read the second copy.
ACCOUNTS = "yulon_rb_accounts"
CHARACTERS = "yulon_rb_characters"
CHARACTERS_AGAIN = "yulon_rb_characters_again"
PETS = "yulon_rb_pets"
GUILD_HEIRS = "yulon_rb_guild_heirs"
GUILDS_GONE = "yulon_rb_guilds_gone"
TEAM_HEIRS = "yulon_rb_team_heirs"
TEAMS_GONE = "yulon_rb_teams_gone"
GROUPS_GONE = "yulon_rb_groups_gone"
LEFTOVERS = "yulon_rb_leftovers"
"""The guard at the end of the transaction: one row keyed 1, and a second insert of key 1
for any doomed row still standing, which fails as a duplicate key and so rolls the whole
transaction back. A duplicate key is an error under every `sql_mode`, on MySQL, MariaDB and
the tests' SQLite alike, which a `SIGNAL` (stored programs only) or a NULL into NOT NULL
(a warning outside strict mode) is not."""

_REALM_FIRSTS = (
    " AND achievement NOT BETWEEN 456 AND 467 AND achievement NOT BETWEEN 1400 AND 1427"
    " AND achievement NOT IN (1463, 3117, 3259)"
)
"""CHAR_DEL_CHAR_ACHIEVEMENTS's own filter: the core keeps realm-first rows on a delete."""

CHARACTER_ROWS: tuple[tuple[str, str, str], ...] = (
    # Player::DeleteFromDB, CHAR_DELETE_REMOVE, in its order (Player.cpp, 4f66bddb).
    ("character_account_data", "guid", ""),
    ("character_declinedname", "guid", ""),
    ("character_action", "guid", ""),
    ("character_aura", "guid", ""),
    ("character_gifts", "guid", ""),
    ("character_homebind", "guid", ""),
    ("character_instance", "guid", ""),
    ("character_inventory", "guid", ""),
    ("character_queststatus", "guid", ""),
    ("character_queststatus_rewarded", "guid", ""),
    ("character_reputation", "guid", ""),
    ("character_spell", "guid", ""),
    ("character_spell_cooldown", "guid", ""),
    ("gm_ticket", "playerGuid", ""),
    ("item_instance", "owner_guid", ""),
    ("character_social", "friend", ""),
    ("character_social", "guid", ""),
    ("mail", "receiver", ""),
    ("mail_items", "receiver", ""),
    ("character_pet_declinedname", "owner", ""),
    ("character_achievement", "guid", _REALM_FIRSTS),
    ("character_achievement_progress", "guid", ""),
    ("character_equipmentsets", "guid", ""),
    ("guild_eventlog", "PlayerGuid1", ""),
    ("guild_eventlog", "PlayerGuid2", ""),
    ("guild_bank_eventlog", "PlayerGuid", ""),
    ("character_entry_point", "guid", ""),
    ("character_glyphs", "guid", ""),
    ("character_queststatus_daily", "guid", ""),
    ("character_queststatus_weekly", "guid", ""),
    ("character_queststatus_monthly", "guid", ""),
    ("character_queststatus_seasonal", "guid", ""),
    ("character_talent", "guid", ""),
    ("character_skills", "guid", ""),
    ("character_settings", "guid", ""),
    ("corpse", "guid", ""),
    # AccountMgr::DeleteAccount's CHAR_DEL_CHARACTER_BAN, and what the running server
    # would have done through the guild, arena, group and petition managers.
    ("character_banned", "guid", ""),
    ("character_arena_stats", "guid", ""),
    ("guild_member", "guid", ""),
    ("guild_member_withdraw", "guid", ""),
    ("arena_team_member", "guid", ""),
    ("group_member", "memberGuid", ""),
    ("petition_sign", "ownerguid", ""),
    ("petition_sign", "playerguid", ""),
    ("petition", "ownerguid", ""),
)
"""(table, column, extra condition) of every characters-database row keyed by a bot's guid."""

PET_ROWS: tuple[tuple[str, str], ...] = (
    ("pet_aura", "guid"),
    ("pet_spell", "guid"),
    ("pet_spell_cooldown", "guid"),
    ("character_pet_declinedname", "id"),
)
"""Pet::DeleteFromDB's rows, keyed by the pet's id."""

GUILD_ROWS: tuple[str, ...] = (
    "guild_bank_item",
    "guild_bank_tab",
    "guild_bank_right",
    "guild_bank_eventlog",
    "guild_eventlog",
    "guild_rank",
    "guild_member",
    "guild",
)
"""Guild::Disband's tables, all keyed by `guildid`; the bank's items go first, by guid."""

ACCOUNT_CHARACTER_ROWS: tuple[tuple[str, str], ...] = (
    ("account_tutorial", "accountId"),
    ("account_data", "accountId"),
)
"""AccountMgr::DeleteAccount's characters-database rows, keyed by account id."""

AUTH_ROWS: tuple[tuple[str, str], ...] = (
    ("realmcharacters", "acctid"),
    ("account_banned", "id"),
    ("account_muted", "guid"),
)
"""AccountMgr::DeleteAccount's login-database rows besides the account and its GM row."""

BOT_CHARACTER_ROWS: tuple[tuple[str, str], ...] = (
    ("playerbots_random_bots", "bot"),
    ("playerbots_db_store", "guid"),
    ("playerbots_guild_tasks", "owner"),
    ("playerbots_preferred_mounts", "guid"),
)
"""mod-playerbots' rows keyed by a bot character's guid."""

BOT_ACCOUNT_ROWS: tuple[tuple[str, str], ...] = (("playerbots_random_bots", "owner"),)
"""mod-playerbots' rows keyed by a bot account's id (the registry is added from the catalog)."""


class RndbotDeleteError(RuntimeError):
    """A refusal or a failure, said in plain words: what was NOT deleted, and why."""


@runtime_checkable
class SqlWriter(Protocol):
    """`apply.DockerSql`, typed here as only what this module is allowed to reach.

    `query` is a read and `run_statement` a write; both are the same object the
    Accounts tab creates accounts with. Typed narrowly, `dbreads.SqlReader`'s
    reasoning: what is not in the type cannot be called through it.
    """

    def query(self, db: Db, statement: str) -> str: ...

    def run_statement(self, db: Db, statement: str) -> None: ...


class _Cancel(Protocol):
    """What the press hands in to stop a delete: a `threading.Event` or anything shaped like one."""

    def is_set(self) -> bool: ...


@dataclass(frozen=True)
class DatabaseAlone:
    """`ui.controller_view.DatabaseAlone`, carried as its two callables (no import cycle)."""

    bring_up: Callable[[str], bool]
    """Start the database alone; True when THIS call had to start it (T76)."""

    take_down: Callable[[], None]
    """Stop the database again; called only where `bring_up` answered True."""


@dataclass(frozen=True)
class _Counts:
    character_ids: tuple[int, ...]
    account_ids: tuple[int, ...]
    """The doomed rows, read BEFORE the delete: what is left afterwards is asked by these ids,
    never through the auth accounts the delete removes (a join over those reads zero)."""
    kept: int
    """Accounts under the prefix that are NOT random bots (AddClass, city, unassigned)."""

    @property
    def characters(self) -> int:
        return len(self.character_ids)

    @property
    def accounts(self) -> int:
        return len(self.account_ids)


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


@dataclass(frozen=True)
class RndbotDelete:
    """The Bots tab's "Delete every random bot…", bound to one AzerothCore install."""

    entry: CatalogEntry
    server_dir: Path
    sql: SqlWriter
    world_running: Callable[[], bool | None]
    alone: DatabaseAlone
    hold_server: Callable[[str], AbstractContextManager[object]]

    def delete(
        self,
        *,
        backup: Callable[[], object] | None = None,
        cancel: _Cancel | None = None,
    ) -> Iterator[str]:
        """The confirmation already answered: take the bots out, one stage a line.

        The server's hold is taken FIRST, so no other press of any Yu'lon can start the
        world or touch the database between the checks and the delete. The backup, when
        asked for, is the first step under it -- the view's `_backup_with_the_database()`,
        whose own hold nests in this one -- and a failed one stops the job before anything
        is written. A refusal raises `RndbotDeleteError`, which the log panel reports as
        the job's failure.
        """
        with self.hold_server(PRESS_DELETE_BOTS):
            marker = self._marker()
            self._refuse_a_running_world()
            if backup is not None:
                yield "Backing up the server first…"
                backup()
            started = self.alone.bring_up("no bot was deleted")
            try:
                # Asked again, after the backup: the backup waits for the database's
                # health (up to 180 s). The same sentence as the first refusal, as the
                # applier's twice-asked guard words it.
                self._refuse_a_running_world()
                yield from self._delete_with_the_database_up(marker, cancel)
            finally:
                if started:
                    # Put back down, as Backup does: a press that quietly left a stopped
                    # server's database running changed something nobody asked about.
                    self.alone.take_down()

    # -- refusals -----------------------------------------------------------------

    def _marker(self) -> Marker:
        """The bot prefix this install runs with; a problem is a refusal."""
        found = resolve_marker(self.entry, self.server_dir)
        if found.marker is None:
            raise RndbotDeleteError(f"No bot was deleted: {found.problem}.")
        return found.marker

    def _refuse_a_running_world(self) -> None:
        """8.7a's rule, fails closed: only a plain "no" lets the deletes through."""
        try:
            running = self.world_running()
        except Exception as exc:  # noqa: BLE001 - any seam failure is one answer here
            raise RndbotDeleteError(
                f"Could not tell whether the server is running ({type(exc).__name__}: {exc}), "
                "and these deletes need it stopped."
            ) from exc
        if running:
            raise RndbotDeleteError(
                "The server is running: it holds the bots in memory and would save them "
                "back over the deletes. Press Stop on the Server tab, then press this again."
            )
        if running is None:
            raise RndbotDeleteError(
                "Could not tell whether the server is running, and these deletes need it "
                "stopped. Refresh the Server tab, then press this again."
            )

    # -- the counts and the delete ------------------------------------------------

    def _delete_with_the_database_up(self, marker: Marker, cancel: _Cancel | None) -> Iterator[str]:
        tables = self._tables()
        self._refuse_without_the_registry(tables)
        before = self._counts(marker)
        if before.characters == 0 and before.accounts == 0:
            yield "There are no random bots to delete."
            return
        if cancel is not None and cancel.is_set():
            yield "Stopped before anything was deleted."
            return
        script = self._script(marker, tables)
        yield (
            f"Deleting {_plural(before.characters, 'random-bot character')} and "
            f"{_plural(before.accounts, 'bot account')} with everything they own, in one "
            "transaction…"
        )
        try:
            self.sql.run_statement("characters", script)
        except Exception as exc:  # noqa: BLE001 - every failure gets the same recount
            raise RndbotDeleteError(self._after_a_failure(marker, before, exc)) from exc
        left_characters, left_accounts = self._left(before)
        if left_characters or left_accounts:
            raise RndbotDeleteError(
                f"The delete reported success, but {left_characters} of {before.characters} "
                f"random-bot characters and {left_accounts} of {before.accounts} bot accounts "
                "are still there; the backup is the way back."
            )
        kept = (
            f" {_plural(before.kept, 'other bot account')} (AddClass, city or unassigned "
            "bots) stayed."
            if before.kept
            else ""
        )
        yield (
            f"Deleted {_plural(before.characters, 'random-bot character')} and "
            f"{_plural(before.accounts, 'bot account')}.{kept} The bots module makes its "
            "random-bot accounts again when the server starts."
        )

    def _after_a_failure(self, marker: Marker, before: _Counts, exc: Exception) -> str:
        """The failure's sentence, with what a recount says was left."""
        why = f"{type(exc).__name__}: {exc}"
        try:
            now = self._left(before)
        except Exception:  # noqa: BLE001 - the first failure is the one to report
            return (
                f"The delete failed ({why}). It ran as one transaction, which the database "
                "rolls back whole on a failure, but the bots could not be counted again to "
                "confirm it; check the Bots tab's counts, and the backup is the way back."
            )
        if now == (before.characters, before.accounts):
            return (
                f"No bot was deleted: the delete failed ({why}) and the database rolled the "
                "whole of it back."
            )
        return (
            f"The delete failed ({why}), and {now[0]} of {before.characters} "
            f"random-bot characters and {now[1]} of {before.accounts} bot accounts are "
            "left; the backup is the way back."
        )

    def _counts(self, marker: Marker) -> _Counts:
        """The random-bot characters' and accounts' ids, and how many prefix accounts stay."""
        schemas = self.entry.schema_map()
        chars = self._ids(
            self.sql.query(
                "characters",
                f"SELECT `guid` FROM `{schemas['characters']}`.`{self._characters_table()}` "
                f"WHERE `{self._account_column()}` IN ({self._doomed_accounts_select(marker)});",
            ),
            "the bot characters",
        )
        accounts = self._ids(
            self.sql.query(
                "auth",
                f"SELECT `id` FROM `{schemas['auth']}`.`account` "
                f"WHERE {self._doomed_accounts_where(marker)};",
            ),
            "the bot accounts",
        )
        kept = self._one_number(
            self.sql.query(
                "auth",
                f"SELECT COUNT(*) FROM `{schemas['auth']}`.`account` "
                f"WHERE {self._prefix_where(marker)} AND NOT ({self._registry_where()});",
            ),
            "the other bot accounts",
        )
        return _Counts(chars, accounts, kept)

    def _left(self, before: _Counts) -> tuple[int, int]:
        """How many of the characters and accounts read before the delete are still there."""
        schemas = self.entry.schema_map()
        characters = self._left_of(
            "characters",
            f"`{schemas['characters']}`.`{self._characters_table()}`",
            "guid",
            before.character_ids,
        )
        accounts = self._left_of("auth", f"`{schemas['auth']}`.`account`", "id", before.account_ids)
        return characters, accounts

    def _left_of(self, db: Db, table: str, column: str, ids: tuple[int, ...]) -> int:
        if not ids:
            return 0
        listed = ", ".join(str(i) for i in ids)
        return self._one_number(
            self.sql.query(db, f"SELECT COUNT(*) FROM {table} WHERE `{column}` IN ({listed});"),
            f"the rows left in {table}",
        )

    def _ids(self, answer: str, what: str) -> tuple[int, ...]:
        """One id a line; anything that is not a whole number is a refusal."""
        rows = [line.strip() for line in answer.splitlines() if line.strip()]
        for row in rows:
            if not row.isdigit():
                raise RndbotDeleteError(
                    f"the ids of {what} came back as {row!r}, which is not a number; nothing "
                    "was deleted"
                )
        return tuple(int(row) for row in rows)

    def _one_number(self, answer: str, what: str) -> int:
        text = answer.strip()
        if not text.isdigit():
            raise RndbotDeleteError(
                f"the count of {what} came back as {text!r}, which is not a number; nothing "
                "was deleted"
            )
        return int(text)

    # -- which accounts -----------------------------------------------------------

    def _prefix_where(self, marker: Marker) -> str:
        """The account-name test: starts with the prefix, `_` meaning `_` (`prefix_match`)."""
        return prefix_match("`username`", marker.prefix)

    def _registry_where(self) -> str:
        """The account-id test: registered by the module as a RANDOM bot (type 1)."""
        registry = self._registry()
        bots = self.entry.schema_map()[registry.database]
        types = ", ".join(str(t) for t in RANDOM_BOT_TYPES)
        return (
            f"`id` IN (SELECT `{registry.account_column}` FROM `{bots}`.`{registry.table}` "
            f"WHERE `{registry.type_column}` IN ({types}))"
        )

    def _doomed_accounts_where(self, marker: Marker) -> str:
        """BOTH tests, joined by AND: an rndbot account that is not a random bot stays."""
        return f"{self._prefix_where(marker)} AND {self._registry_where()}"

    def _doomed_accounts_select(self, marker: Marker) -> str:
        auth = self.entry.schema_map()["auth"]
        return f"SELECT `id` FROM `{auth}`.`account` WHERE {self._doomed_accounts_where(marker)}"

    def _registry(self) -> BotRegistry:
        observability = self.entry.observability
        registry = observability.bots.registry if observability is not None else None
        if registry is None or registry.database not in self.entry.schema_map():
            raise RndbotDeleteError(
                f"No bot was deleted: {self.entry.name}'s bots module keeps no record of which "
                "accounts are random bots, so they cannot be told from the AddClass bots."
            )
        return registry

    def _characters_table(self) -> str:
        return self.entry.observability.characters.table  # type: ignore[union-attr]

    def _account_column(self) -> str:
        return self.entry.observability.characters.account  # type: ignore[union-attr]

    # -- the tables ---------------------------------------------------------------

    def _tables(self) -> dict[tuple[str, str], str]:
        """`(schema, table) -> engine` for this install's auth, characters and bots schemas.

        Asked over `auth`: the information_schema answer does not depend on which schema
        the connection names, and `auth` exists on every install.
        """
        schemas = self.entry.schema_map()
        names = [schemas["auth"], schemas["characters"]]
        if "playerbots" in schemas:
            names.append(schemas["playerbots"])
        listed = ", ".join(f"'{name}'" for name in names)
        answer = self.sql.query(
            "auth",
            "SELECT TABLE_SCHEMA, TABLE_NAME, ENGINE FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA IN ({listed});",
        )
        found: dict[tuple[str, str], str] = {}
        for line in answer.splitlines():
            parts = line.split("\t")
            if len(parts) == 3:
                found[(parts[0], parts[1])] = parts[2]
        return found

    def _refuse_without_the_registry(self, tables: dict[tuple[str, str], str]) -> None:
        registry = self._registry()
        schemas = self.entry.schema_map()
        needed = (
            (schemas[registry.database], registry.table),
            (schemas["auth"], "account"),
            (schemas["characters"], self._characters_table()),
        )
        for schema, table in needed:
            if (schema, table) not in tables:
                raise RndbotDeleteError(
                    f"No bot was deleted: the table {schema}.{table} is not there, so which "
                    "accounts are random bots cannot be read."
                )

    def _script(self, marker: Marker, tables: dict[tuple[str, str], str]) -> str:
        """The whole delete as one transaction, one statement a line.

        Run on a `characters` connection: the characters database's tables are named
        bare (and the temporary tables land there), the auth and bots tables in full.
        Only tables this install has are written, and every one of them must roll back.
        """
        schemas = self.entry.schema_map()
        auth, chars = schemas["auth"], schemas["characters"]
        registry = self._registry()
        bots = schemas[registry.database]
        written: list[tuple[str, str]] = []

        def has(schema: str, table: str) -> bool:
            if (schema, table) not in tables:
                return False
            written.append((schema, table))
            return True

        def ids(temp: str, column: str = "guid") -> str:
            return f"(SELECT `{column}` FROM `{temp}`)"

        def temp(name: str, columns: str) -> str:
            return f"CREATE TEMPORARY TABLE `{name}` ({columns});"

        guid = "`guid` INT UNSIGNED NOT NULL PRIMARY KEY"
        out = [
            "START TRANSACTION;",
            temp(ACCOUNTS, "`id` INT UNSIGNED NOT NULL PRIMARY KEY"),
            f"INSERT INTO `{ACCOUNTS}` (`id`) {self._doomed_accounts_select(marker)};",
            temp(CHARACTERS, guid),
            f"INSERT INTO `{CHARACTERS}` (`guid`) SELECT `guid` FROM `{self._characters_table()}` "
            f"WHERE `{self._account_column()}` IN {ids(ACCOUNTS, 'id')};",
            temp(CHARACTERS_AGAIN, guid),
            f"INSERT INTO `{CHARACTERS_AGAIN}` (`guid`) SELECT `guid` FROM `{CHARACTERS}`;",
        ]
        if has(chars, "character_pet"):
            out += [
                temp(PETS, "`id` INT UNSIGNED NOT NULL PRIMARY KEY"),
                f"INSERT INTO `{PETS}` (`id`) SELECT `id` FROM `character_pet` "
                f"WHERE `owner` IN {ids(CHARACTERS)};",
            ]
            for table, column in PET_ROWS:
                if has(chars, table):
                    out.append(f"DELETE FROM `{table}` WHERE `{column}` IN {ids(PETS, 'id')};")
            out.append(f"DELETE FROM `character_pet` WHERE `owner` IN {ids(CHARACTERS)};")
        if has(chars, "guild") and has(chars, "guild_member"):
            out += self._guilds(has, chars)
        if has(chars, "arena_team") and has(chars, "arena_team_member"):
            out += [
                temp(
                    TEAM_HEIRS,
                    "`arenaTeamId` INT UNSIGNED NOT NULL PRIMARY KEY, `guid` INT UNSIGNED NULL",
                ),
                f"INSERT INTO `{TEAM_HEIRS}` (`arenaTeamId`, `guid`) SELECT t.`arenaTeamId`, "
                "(SELECT m.`guid` FROM `arena_team_member` m WHERE m.`arenaTeamId` = "
                f"t.`arenaTeamId` AND m.`guid` NOT IN {ids(CHARACTERS)} ORDER BY m.`guid` "
                f"LIMIT 1) FROM `arena_team` t WHERE t.`captainGuid` IN {ids(CHARACTERS_AGAIN)};",
                "UPDATE `arena_team` SET `captainGuid` = COALESCE((SELECT h.`guid` FROM "
                f"`{TEAM_HEIRS}` h WHERE h.`arenaTeamId` = `arena_team`.`arenaTeamId`), "
                "`captainGuid`);",
                temp(TEAMS_GONE, "`arenaTeamId` INT UNSIGNED NOT NULL PRIMARY KEY"),
                f"INSERT INTO `{TEAMS_GONE}` (`arenaTeamId`) SELECT `arenaTeamId` FROM "
                f"`{TEAM_HEIRS}` WHERE `guid` IS NULL;",
                "DELETE FROM `arena_team_member` WHERE `arenaTeamId` IN "
                f"{ids(TEAMS_GONE, 'arenaTeamId')};",
                "DELETE FROM `arena_team` WHERE `arenaTeamId` IN "
                f"{ids(TEAMS_GONE, 'arenaTeamId')};",
            ]
        if has(chars, "groups") and has(chars, "group_member"):
            out += [
                temp(GROUPS_GONE, guid),
                f"INSERT INTO `{GROUPS_GONE}` (`guid`) SELECT `guid` FROM `groups` "
                f"WHERE `leaderGuid` IN {ids(CHARACTERS)};",
                f"DELETE FROM `group_member` WHERE `guid` IN {ids(GROUPS_GONE)};",
                f"DELETE FROM `groups` WHERE `guid` IN {ids(GROUPS_GONE)};",
            ]
        if has(chars, "auctionhouse"):
            if has(chars, "item_instance"):
                out.append(
                    "DELETE FROM `item_instance` WHERE `guid` IN (SELECT `itemguid` FROM "
                    f"`auctionhouse` WHERE `itemowner` IN {ids(CHARACTERS)});"
                )
            out.append(f"DELETE FROM `auctionhouse` WHERE `itemowner` IN {ids(CHARACTERS)};")
        for table, column, extra in CHARACTER_ROWS:
            if has(chars, table):
                out.append(f"DELETE FROM `{table}` WHERE `{column}` IN {ids(CHARACTERS)}{extra};")
        for table, column in BOT_CHARACTER_ROWS:
            if has(bots, table):
                out.append(f"DELETE FROM `{bots}`.`{table}` WHERE `{column}` IN {ids(CHARACTERS)};")
        has(chars, self._characters_table())
        out.append(f"DELETE FROM `{self._characters_table()}` WHERE `guid` IN {ids(CHARACTERS)};")
        for table, column in ACCOUNT_CHARACTER_ROWS:
            if has(chars, table):
                out.append(f"DELETE FROM `{table}` WHERE `{column}` IN {ids(ACCOUNTS, 'id')};")
        for table, column in (*BOT_ACCOUNT_ROWS, (registry.table, registry.account_column)):
            if has(bots, table):
                out.append(
                    f"DELETE FROM `{bots}`.`{table}` WHERE `{column}` IN {ids(ACCOUNTS, 'id')};"
                )
        level = self.entry.accounts.level
        auth_rows = list(AUTH_ROWS)
        if level is not None and level.table:
            auth_rows.append((level.table, level.account_column))
        for table, column in auth_rows:
            if has(auth, table):
                out.append(
                    f"DELETE FROM `{auth}`.`{table}` WHERE `{column}` IN {ids(ACCOUNTS, 'id')};"
                )
        has(auth, "account")
        out.append(f"DELETE FROM `{auth}`.`account` WHERE `id` IN {ids(ACCOUNTS, 'id')};")
        # The guard (`LEFTOVERS`): a doomed character or account still standing here makes a
        # duplicate key, and the transaction fails whole instead of committing half a plan.
        doomed_accounts = ids(ACCOUNTS, "id")
        out += [
            temp(LEFTOVERS, "`k` INT NOT NULL PRIMARY KEY"),
            f"INSERT INTO `{LEFTOVERS}` (`k`) VALUES (1);",
            f"INSERT INTO `{LEFTOVERS}` (`k`) SELECT 1 FROM `{self._characters_table()}` "
            f"WHERE `guid` IN {ids(CHARACTERS)} LIMIT 1;",
            f"INSERT INTO `{LEFTOVERS}` (`k`) SELECT 1 FROM `{self._characters_table()}` "
            f"WHERE `{self._account_column()}` IN {doomed_accounts} LIMIT 1;",
            f"INSERT INTO `{LEFTOVERS}` (`k`) SELECT 1 FROM `{auth}`.`account` "
            f"WHERE `id` IN {doomed_accounts} LIMIT 1;",
        ]
        out.append("COMMIT;")
        self._refuse_what_cannot_roll_back(tables, written)
        return "\n".join(out)

    def _guilds(self, has: Callable[[str, str], bool], chars: str) -> list[str]:
        """A deleted leader's guild passes to the best-ranked member left, else is disbanded.

        `Guild::DeleteMember`'s rule: the lowest rank id (0 is the guild master) wins, and
        with nobody left the guild is disbanded (`Guild::Disband`), its bank with it.
        """
        out = [
            f"CREATE TEMPORARY TABLE `{GUILD_HEIRS}` (`guildid` INT UNSIGNED NOT NULL PRIMARY "
            "KEY, `guid` INT UNSIGNED NULL);",
            f"INSERT INTO `{GUILD_HEIRS}` (`guildid`, `guid`) SELECT g.`guildid`, (SELECT "
            "m.`guid` FROM `guild_member` m WHERE m.`guildid` = g.`guildid` AND m.`guid` NOT IN "
            f"(SELECT `guid` FROM `{CHARACTERS}`) ORDER BY m.`rank`, m.`guid` LIMIT 1) FROM "
            f"`guild` g WHERE g.`leaderguid` IN (SELECT `guid` FROM `{CHARACTERS_AGAIN}`);",
            "UPDATE `guild` SET `leaderguid` = COALESCE((SELECT h.`guid` FROM "
            f"`{GUILD_HEIRS}` h WHERE h.`guildid` = `guild`.`guildid`), `leaderguid`);",
            f"UPDATE `guild_member` SET `rank` = 0 WHERE `guid` IN (SELECT `guid` FROM "
            f"`{GUILD_HEIRS}`);",
            f"CREATE TEMPORARY TABLE `{GUILDS_GONE}` (`guildid` INT UNSIGNED NOT NULL PRIMARY "
            "KEY);",
            f"INSERT INTO `{GUILDS_GONE}` (`guildid`) SELECT `guildid` FROM `{GUILD_HEIRS}` "
            "WHERE `guid` IS NULL;",
        ]
        gone = f"(SELECT `guildid` FROM `{GUILDS_GONE}`)"
        if has(chars, "guild_bank_item") and has(chars, "item_instance"):
            out.append(
                "DELETE FROM `item_instance` WHERE `guid` IN (SELECT `item_guid` FROM "
                f"`guild_bank_item` WHERE `guildid` IN {gone});"
            )
        for table in GUILD_ROWS:
            if has(chars, table):
                out.append(f"DELETE FROM `{table}` WHERE `guildid` IN {gone};")
        return out

    def _refuse_what_cannot_roll_back(
        self, tables: dict[tuple[str, str], str], written: list[tuple[str, str]]
    ) -> None:
        """Every table the transaction writes must be one a rollback undoes."""
        for schema, table in dict.fromkeys(written):
            engine = tables.get((schema, table), "")
            if engine.lower() not in TRANSACTIONAL_ENGINES:
                raise RndbotDeleteError(
                    f"No bot was deleted: {schema}.{table} is a {engine or 'unknown'} table, "
                    "which a failed delete could not roll back, so the delete would not be "
                    "all-or-nothing."
                )


def for_entry(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    sql: SqlWriter,
    world_running: Callable[[], bool | None],
    alone: DatabaseAlone,
    hold_server: Callable[[str], AbstractContextManager[object]],
) -> RndbotDelete | None:
    """The press for an install whose bot marker is measured, and None where it is not.

    The delete is written against the marker and the schema names the catalog carries
    for the entry (`observability`, `schema_map()`), so an entry without them is not a
    server this can delete bots on. Every AzerothCore entry has them; the None is for
    the CMaNGOS trees, whose factories never call this.
    """
    if entry.observability is None:
        return None
    return RndbotDelete(
        entry=entry,
        server_dir=server_dir,
        sql=sql,
        world_running=world_running,
        alone=alone,
        hold_server=hold_server,
    )
