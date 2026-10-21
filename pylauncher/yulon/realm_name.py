"""The name a player sees for this server in the game's realm list (T808).

The name is the `name` column of the login database's realm row, the same row
`networking.realmlist_sql()` writes the advertised address into. The
authserver re-reads the row about every ten seconds, so a change shows the next
time the client opens its realm list.

**What a person types never becomes SQL text.** `DockerSql` has no parameter
binding, and a quoted literal is the trap `NO_BACKSLASH_ESCAPES` sets (a
backslash stops being an escape and the statement changes meaning). The name
goes into the statement as a hex blob (`_utf8mb4 X'...'`), the shape
`useraccounts`, `play` and `botlist` use, which has no escape character at all
and so means the same under every `sql_mode`. `check()` still refuses what
cannot be a realm name, BEFORE any statement is built.

**Where the choice is kept.** `<server_dir>/.yulon-realm-name.json`, beside
`.yulon-network.json`, for the reasons `networking.INTENT_FILE` gives: a
person's choice about ONE install belongs with that install. The install's
closing step (`catalog.native.StagedInstaller._apply_realm_name()`) and every
Start re-apply it, so a rebuild's import, which seeds the catalog's own name,
does not undo it.

**Applies to** every `CatalogEntry`, because every WoW core's realm row has a
`name` column. A `SingleEntry` (DDON, DFO) has no realm row and gets no seam.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon.catalog.catalog import CatalogEntry, DbPasswordUnknown
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

REALM_NAME_FILE = ".yulon-realm-name.json"
"""The record's name in the server folder. One of `catalog.native.OUR_OWN_FILES`."""

NAME_COLUMN = "name"
"""The realm row's name column: the same word on AzerothCore, both CMaNGOS trees, Tortoise and
TrinityCore (Centurion), and `UNIQUE KEY idx_name` on every one of them."""

MAX_LENGTH = 32
"""Characters. `realmlist.name` is `varchar(32)` on every core this app installs: utf8mb4 on
AzerothCore and TrinityCore, three-byte utf8 on CMaNGOS and Tortoise (read 2026-10-21 from each
pinned tree's base auth schema), so a BMP character is one character on every one."""


_DUPLICATE = re.compile(r"\bERROR 1062\b")
"""The mysql and mariadb clients' words for a taken key (`ER_DUP_ENTRY`): here `idx_name`."""


class RealmNameError(ValueError):
    """A name that cannot be a realm name; the message is a sentence for the player."""


def check(name: str) -> str:
    """`name` as it will be written (surrounding blanks removed), or `RealmNameError`.

    Refused: nothing at all, more than `MAX_LENGTH` characters, a control or
    other unprintable character (a newline in a name breaks the realm list's
    row), and a character beyond the Basic Multilingual Plane, which the
    three-byte `utf8` the realm tables use cannot hold. Quotes, backslashes and
    semicolons are NOT refused: they are ordinary characters in a name
    (`Jerra's Realm`), and the hex literal makes them harmless.
    """
    if not isinstance(name, str):
        raise RealmNameError("The realm name must be text.")
    cleaned = name.strip()
    if not cleaned:
        raise RealmNameError("Type a realm name: it cannot be empty.")
    if len(cleaned) > MAX_LENGTH:
        raise RealmNameError(
            f"The realm name can be at most {MAX_LENGTH} characters; this one has {len(cleaned)}."
        )
    for ch in cleaned:
        if not ch.isprintable():
            raise RealmNameError("The realm name cannot hold control or invisible characters.")
        if ord(ch) > 0xFFFF:
            raise RealmNameError(
                "The realm name cannot hold emoji or other characters outside the basic "
                "multilingual plane: the realm list cannot store them."
            )
    return cleaned


def text_literal(text: str) -> str:
    """One string as a hex blob, so the server never parses what is inside it."""
    return "_utf8mb4 X'" + text.encode("utf-8").hex().upper() + "'"


def name_sql(entry: CatalogEntry, name: str) -> str:
    """The UPDATE that sets the realm's name; `RealmNameError` for a name `check()` refuses.

    Restricted to the one row this app owns: the same `WHERE id=` that
    `networking.realmlist_sql()` uses.
    """
    return row_name_sql(entry, check(name))


def row_name_sql(entry: CatalogEntry, name: str) -> str:
    """`name_sql()`'s UPDATE for a name exactly as given, unchecked.

    For a name read out of another realm row (a move's "use the old realm name"), which
    the row already held. The hex literal is what makes it safe, not `check()`: the name
    means the same under every `sql_mode`, `NO_BACKSLASH_ESCAPES` included.
    """
    rl = entry.realmlist
    return (
        f"UPDATE {entry.databases.auth}.{rl.table} SET {NAME_COLUMN}={text_literal(name)} "
        f"WHERE id={rl.realm_id};"
    )


def name_query(entry: CatalogEntry) -> str:
    """The SELECT that reads the realm's name back, as hex of its UTF-8 bytes.

    Hex, because the mysql client's batch output escapes a backslash, a tab and a
    newline, so a name read as text would not equal the name written. Converted
    to `utf8mb4` first, so a column in another charset still answers in one.
    """
    rl = entry.realmlist
    return (
        f"SELECT HEX(CONVERT({NAME_COLUMN} USING utf8mb4)) "
        f"FROM {entry.databases.auth}.{rl.table} WHERE id={rl.realm_id};"
    )


def name_from_answer(answer: str) -> str | None:
    """The name `name_query()`'s answer holds, or None unless it is exactly one readable row."""
    rows = [line.strip() for line in answer.splitlines() if line.strip()]
    if len(rows) != 1:
        return None
    try:
        return bytes.fromhex(rows[0]).decode("utf-8")
    except ValueError:
        return None


# -- the saved choice ----------------------------------------------------


def read_saved_name(server_dir: Path) -> str | None:
    """The name saved for this install, or None if nobody saved one.

    Every unreadable shape answers None, and a saved name that `check()` would
    refuse is none either: a damaged record must never put a bad value into the
    database.
    """
    path = server_dir / REALM_NAME_FILE
    try:
        with path.open(encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"no usable realm name in {server_dir}: {exc}")
        return None
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("name")
    if not isinstance(name, str):
        return None
    try:
        return check(name)
    except RealmNameError:
        return None


def record_name(server_dir: Path, name: str) -> str:
    """Remember `name` for this install; `""` if it was written, else why not.

    Written atomically, so a half-written record is never read as a record.
    """
    cleaned = check(name)
    path = server_dir / REALM_NAME_FILE
    tmp = path.with_name(path.name + ".new")
    try:
        server_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps({"name": cleaned}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        logger.warning(f"could not remember the realm name in {path}: {exc}")
        return str(exc)
    return ""


def forget_name(server_dir: Path) -> str:
    """Drop the saved choice; `""` if there is none now, else why it could not be dropped.

    For a press that has just put a name in the row which this record cannot hold (one
    `check()` refuses): left behind, the record would put its own name back at the next Start.
    """
    path = server_dir / REALM_NAME_FILE
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning(f"could not forget the realm name in {path}: {exc}")
        return str(exc)
    return ""


# -- what the Networking tab calls ---------------------------------------


class _Sql(Protocol):
    """The two methods of `apply.DockerSql` this module uses."""

    def run_statement(self, db: Db, statement: str) -> None: ...

    def query(self, db: Db, statement: str) -> str: ...


@dataclass(frozen=True)
class NameState:
    """What the name field shows, and where that came from."""

    shown: str
    """The name to put in the field; `""` when there is nothing to show."""
    from_database: bool
    """True when `shown` was read from the realm row just now."""
    note: str = ""
    """A sentence about where `shown` came from when it is not the database."""


@dataclass(frozen=True)
class SaveResult:
    """What pressing Save did."""

    text: str
    """The sentence to show."""
    ok: bool
    """False only when nothing was done (a refused name)."""
    remembered: bool = False
    """The name is now kept for this install."""
    applied: bool = False
    """The realm row says the name now."""


class RealmNames:
    """One install's realm name: reading it, and changing it."""

    def __init__(self, entry: CatalogEntry, server_dir: Path, sql: _Sql) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self.sql = sql

    def _row_name(self) -> str | None:
        """The name the realm row holds, or None when it could not be read."""
        try:
            answer = self.sql.query("auth", name_query(self.entry))
        except Exception as exc:  # noqa: BLE001 - a database that is down is an answer here
            logger.debug(f"the realm name could not be read: {exc}")
            return None
        return name_from_answer(answer)

    def current(self) -> NameState:
        """The row's name when the database answers, else the saved one."""
        found = self._row_name()
        if found is not None:
            return NameState(found, True)
        saved = read_saved_name(self.server_dir)
        if saved is not None:
            return NameState(
                saved,
                False,
                "The database did not answer, so this is the name saved for this server.",
            )
        return NameState("", False, "The database did not answer and no name is saved yet.")

    def save(self, name: str) -> SaveResult:
        """Check, apply (if the database is up) and remember `name`.

        A refused name touches nothing: no file, no statement. Neither does a name
        another realm row already has (`idx_name` is UNIQUE on every core): it would
        fail the same way at every start, so it is said and not kept. Any other
        failure to apply still keeps the choice for the next start.
        """
        try:
            cleaned = check(name)
            statement = name_sql(self.entry, cleaned)
        except RealmNameError as exc:
            return SaveResult(str(exc), ok=False)
        try:
            self.sql.run_statement("auth", statement)
        except DbPasswordUnknown as exc:
            # Not "the next start": that start asks with the same unknown password (review).
            logger.info(f"the realm name could not be applied: {exc}")
            unwritten = record_name(self.server_dir, cleaned)
            if not unwritten:
                text = (
                    f"Saved the realm name '{cleaned}', but it could not be applied: {exc} "
                    "It is applied at the first start after that is put right."
                )
            else:
                text = (
                    f"{exc} The name could not be saved either ({unwritten}), so nothing changed."
                )
            return SaveResult(text, ok=True, remembered=not unwritten)
        except Exception as exc:  # noqa: BLE001 - any failure means "not applied now"
            if _DUPLICATE.search(str(exc)):
                logger.info(f"the realm name is another realm's: {exc}")
                return SaveResult(
                    f"Another realm on this server is already called '{cleaned}', and two "
                    "realms cannot share a name. Nothing was changed.",
                    ok=False,
                )
            logger.info(f"the realm name could not be applied now: {exc}")
            unwritten = record_name(self.server_dir, cleaned)
            if not unwritten:
                text = (
                    f"Saved the realm name '{cleaned}'. The database could not be reached, so it "
                    "is applied the next time the server starts."
                )
            else:
                text = (
                    f"The database could not be reached and the name could not be saved "
                    f"({unwritten}), so nothing changed."
                )
            return SaveResult(text, ok=True, remembered=not unwritten)
        unwritten = record_name(self.server_dir, cleaned)
        remembered = not unwritten
        if self._row_name() != cleaned:
            text = f"The realm row did not take the name '{cleaned}'." + (
                " It is saved and tried again at the next start." if remembered else ""
            )
            return SaveResult(text, ok=True, remembered=remembered)
        text = (
            f"The realm is now called '{cleaned}'. A client has to go back to its realm list "
            "(log out to the realm screen) to see it."
        )
        if not remembered:
            text += (
                f" It could not be saved ({unwritten}), so a rebuild would put the old name back."
            )
        return SaveResult(text, ok=True, remembered=remembered, applied=True)


def keep_at_start(
    entry: CatalogEntry,
    server_dir: Path,
    sql: _Sql,
    bring_up_database: Callable[[], object],
    previous: Callable[[], str | None] | None,
) -> Callable[[], str | None]:
    """A `Controller.before_servers` step that puts the saved name in the row; never refuses.

    `previous` (the step already there, the realm-port keeper) goes first and its
    refusal is passed on untouched. With no saved name this does nothing at all,
    not even start the database. A failure is logged and swallowed: a realm name
    is not worth a server that will not start.
    """

    def keep() -> str | None:
        if previous is not None:
            refusal = previous()
            if refusal:
                return refusal
        saved = read_saved_name(server_dir)
        if saved is None:
            return None
        try:
            bring_up_database()
            sql.run_statement("auth", name_sql(entry, saved))
        except Exception as exc:  # noqa: BLE001 - never refuse a start over a name
            logger.warning(f"the saved realm name '{saved}' could not be applied at start: {exc}")
        return None

    return keep
