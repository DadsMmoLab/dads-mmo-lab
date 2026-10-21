"""The Bots tab's "Delete every random bot…" (T805), on the AzerothCore trees (WotLK, Unbound).

Tortoise's "Rebuild random bots…" (`controller_wow_tortoise.poolreset`) asks the bots
MODULE to delete its own pool; AzerothCore's mod-playerbots has no such request, so the
same one-press action does the deleting HERE, through the same `DockerSql` the tab's
reads already go through -- the write half (`run_statement()`) that
`wotlk_accounts.create_account()` and My Party's account link already write with.

What "every random bot" is here is what the catalog measures (`entry.observability`):
every character whose account is a `RandomBotAccountPrefix` account (`rndbot` by
default, resolved off the install's `playerbots.conf` like every other read) and every
character whose account the module registered as a random bot (`account_type` 1).
AddClass bots (2) and city bots (3) are deliberately kept: they are accounts somebody
made on purpose, and this press is for the ones the module makes by itself. The rows go
from `characters`, from the module's own tables (`playerbots_random_bots`,
`playerbots_account_type`), and the accounts go from `account` with their GM rows.

The world-stopped rule is 8.7a's, the one `apply.Applier` enforces for direct SQL: ONLY
the terminal container statuses count as stopped, `docker.world_running()` is asked,
and anything short of a clear "no" refuses. The database, when it has to, is brought up
alone and put back down, the way Backup does it (`DatabaseAlone`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from yulon.catalog.catalog import CatalogEntry
from yulon.dbreads import Marker, bot_clause, resolve_marker
from yulon.manifest import Db

PRESS_DELETE_BOTS = "Delete the random bots"
"""This job's press on the server's cross-process reservation (`docker.server_hold`)."""

RANDOM_BOTS_TABLE = "playerbots_random_bots"
"""The module's event log, keyed `owner` = account id, `bot` = character guid.

Deleted FIRST, by subqueries over the rows still standing: the characters delete comes
after and would leave those subqueries finding nothing.
"""


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

        The backup, when asked for, is the first step OF the job -- the view's
        `_backup_with_the_database()`, which starts the database alone if the server is
        down -- and a failed one stops it before anything is written. A refusal raises
        `RndbotDeleteError`, which the log panel reports as the job's failure.
        """
        marker = self._marker()
        self._refuse_a_running_world()
        if backup is not None:
            yield "Backing up the server first…"
            backup()
        started = self.alone.bring_up("no bot was deleted")
        try:
            # Asked again, after the backup: the backup waits for the database's health
            # (up to 180 s) and a Start pressed in that window would have brought the
            # world up on the rows about to be deleted. The same sentence as the first
            # refusal, as the applier's twice-asked guard words it.
            self._refuse_a_running_world()
            yield from self._delete_under_the_hold(marker, cancel)
        finally:
            if started:
                # Put back down, as Backup does: the deletes are over, and a press that
                # quietly left a stopped server's database running changed something the
                # user did not ask about.
                self.alone.take_down()

    # -- refusals -----------------------------------------------------------------

    def _marker(self) -> Marker:
        """The bot prefix this install runs with; a problem is a refusal."""
        found = resolve_marker(self.entry, self.server_dir)
        if found.marker is None:
            raise RndbotDeleteError(found.problem)
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

    # -- the counts and the deletes -----------------------------------------------

    def delete_under_the_hold(self, marker: Marker, cancel: _Cancel | None) -> Iterator[str]:
        """Public to the tests only: the held block, counts before and summary after."""
        yield from self._delete_under_the_hold(marker, cancel)

    def _delete_under_the_hold(self, marker: Marker, cancel: _Cancel | None) -> Iterator[str]:
        with self.hold_server(PRESS_DELETE_BOTS):
            bots, accounts = self._counts(marker)
            if cancel is not None and cancel.is_set():
                yield "Stopped before anything was deleted."
                return
            yield (
                f"Deleting {bots} random-bot character{'' if bots == 1 else 's'} and "
                f"{accounts} bot account{'' if accounts == 1 else 's'}…"
            )
            yield from self._delete_everything(marker, cancel)
            if self._cancelled(cancel):
                yield ("Stopped. The deletions above are what ran; the backup is the way back.")
                return
            bots_after, accounts_after = self._counts(marker)
            if bots_after == 0 and accounts_after == 0:
                yield (
                    f"Deleted {bots} random-bot character{'' if bots == 1 else 's'} and "
                    f"{accounts} bot account{'' if accounts == 1 else 's'}. The bots module "
                    "makes its accounts again when the server starts."
                )
            else:
                yield (
                    f"{bots_after} random-bot character(s) and {accounts_after} bot account(s) "
                    "are still there; the log above says which delete did not run."
                )

    def _delete_everything(self, marker: Marker, cancel: _Cancel | None) -> Iterator[str]:
        """Every statement, oldest reference first.

        `playerbots_random_bots` first (its subqueries need the rows still there), then
        the characters, then what hangs off the ACCOUNT ids -- the accounts themselves
        last, because they drive every other subquery. A failure raises: what landed,
        landed; the backup is the way back.
        """
        schemas = self.entry.schema_map()
        auth, chars, bots = schemas["auth"], schemas["characters"], schemas.get("playerbots")
        clause = self._characters_clause(marker)
        prefix = self._prefix_clause(marker)
        if bots is not None and self._table_exists("auth", bots, RANDOM_BOTS_TABLE):
            yield "Deleting the module's random-bots rows…"
            self.sql.run_statement(
                "playerbots",
                f"DELETE FROM `{bots}`.`{RANDOM_BOTS_TABLE}` WHERE `bot` IN "
                f"(SELECT guid FROM `{chars}`.`{self._characters_table()}` WHERE ({clause}));",
            )
            self.sql.run_statement(
                "playerbots",
                f"DELETE FROM `{bots}`.`{RANDOM_BOTS_TABLE}` WHERE `owner` IN "
                f"(SELECT id FROM `{auth}`.`account` WHERE {prefix});",
            )
            if self._cancelled(cancel):
                return
        yield "Deleting the bots' characters…"
        self.sql.run_statement(
            "characters",
            f"DELETE FROM `{chars}`.`{self._characters_table()}` WHERE ({clause});",
        )
        if self._cancelled(cancel):
            return
        yield "Deleting the bots' account rows…"
        observability = self.entry.observability
        registry = observability.bots.registry if observability is not None else None
        if registry is not None and bots is not None:
            self.sql.run_statement(
                "playerbots",
                f"DELETE FROM `{bots}`.`{registry.table}` WHERE `{registry.account_column}` IN "
                f"(SELECT id FROM `{auth}`.`account` WHERE {prefix});",
            )
        level = self.entry.accounts.level
        if level is not None and level.table:
            self.sql.run_statement(
                "auth",
                f"DELETE FROM `{auth}`.`{level.table}` WHERE `{level.account_column}` IN "
                f"(SELECT id FROM `{auth}`.`account` WHERE {prefix});",
            )
        self.sql.run_statement("auth", f"DELETE FROM `{auth}`.`account` WHERE {prefix};")

    # -- the SQL parts, shared with the counts ------------------------------------

    def _characters_clause(self, marker: Marker) -> str:
        """The character-row SQL true of a RANDOM bot only.

        `bot_clause`'s prefix arm, plus its registry arm narrowed to `account_type` 1:
        the broad arm would also take the AddClass bots (2) and city bots (3).
        """
        return bot_clause(self.entry, marker, only_types=(1,))

    def _prefix_clause(self, marker: Marker) -> str:
        """The account-name SQL true of a bot account. `resolve_marker` validated the prefix."""
        return f"UPPER(username) LIKE '{marker.prefix.upper()}%'"

    def _counts(self, marker: Marker) -> tuple[int, int]:
        """How many random-bot characters and how many bot accounts this server has."""
        schemas = self.entry.schema_map()
        chars = self._one_number(
            self.sql.query(
                "characters",
                f"SELECT COUNT(*) FROM `{schemas['characters']}`.`{self._characters_table()}` "
                f"WHERE ({self._characters_clause(marker)});",
            ),
            "the bot characters",
        )
        accounts = self._one_number(
            self.sql.query(
                "auth",
                f"SELECT COUNT(*) FROM `{schemas['auth']}`.`account` "
                f"WHERE {self._prefix_clause(marker)};",
            ),
            "the bot accounts",
        )
        return chars, accounts

    def _one_number(self, answer: str, what: str) -> int:
        text = answer.strip()
        if not text.isdigit():
            raise RndbotDeleteError(
                f"the count of {what} came back as {text!r}, which is not a number; nothing "
                "was deleted"
            )
        return int(text)

    def _characters_table(self) -> str:
        return self.entry.observability.characters.table  # type: ignore[union-attr]

    def _table_exists(self, ask: Db, schema: str, table: str) -> bool:
        """Whether `schema.table` exists, asked over a database that does (`ask`).

        The information_schema answer does not depend on which schema the connection
        names, and asking over `auth` keeps the check working on an install whose
        `playerbots` schema was never created.
        """
        answer = self.sql.query(
            ask,
            "SELECT COUNT(*) FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{schema}' AND TABLE_NAME = '{table}';",
        ).strip()
        return answer == "1"

    @staticmethod
    def _cancelled(cancel: _Cancel | None) -> bool:
        return cancel is not None and cancel.is_set()


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
