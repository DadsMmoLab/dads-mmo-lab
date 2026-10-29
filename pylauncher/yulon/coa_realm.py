"""Realm types for Conquest of Azeroth: the engine, no Qt (T812 C3a).

The ACTIVE type always lives under the plain `acore_auth`/`acore_characters`/
`acore_playerbots` names, so every module reading `entry.databases.*` stays
right with no change. Inactive types are parked as `<db>_<id>` schemas and a
switch is ONE atomic `RENAME TABLE` statement, with the servers stopped: either
every table set moves or none does, and the marker file is written only after.

The one family branch in this module is `_block()`: realm types are an
AzerothCore-block feature, and the other families never reach here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon.catalog.catalog import AcRealmType, AzerothCoreData, CatalogEntry
from yulon.said import SaidByYulon

logger = logging.getLogger(__name__)

ACTIVE_FILE = ".yulon-coa-realm.json"
"""The file in the server dir naming the type the plain schemas hold."""

MARKER_TABLE = "yulon_realm_type"
"""The one-row table in the characters schema naming the type the data holds."""

SWITCH_MARKER_FILE = ".yulon-coa-switch.json"
"""The crash marker a switch writes before the first rename and clears after.

A killed switch must be retryable: the marker names the target, and the next
press reads the parked schemas before it renames anything, so a half-moved set
is put back rather than moved twice. `imported` turns true only once the
rename is done and the first switch's fresh import is about to begin: only a
plain set this switch began importing is ever dropped. While it exists no
start runs (`refusal()`). It travels in the server dir, never in a database,
because the databases are what the rename is moving.
"""

_ID = re.compile(r"[a-z][a-z0-9]{1,9}")
"""A realm type id, the catalog's own shape, before it is spliced into SQL."""

_SAFE_PREFIX = re.compile(r"[A-Za-z0-9_.-]+")
"""What a bot prefix may hold before it lands inside a quoted `LIKE`."""

_SAFE_NAME = re.compile(r"[A-Za-z0-9_.@-]+")
"""What a copied account name may hold: it is spliced into SQL as it stands."""


class RealmSwitchError(RuntimeError, SaidByYulon):
    """A switch or a start check was refused; the message is the user sentence."""


class SqlRunner(Protocol):
    """The statements a switch runs, in the caller's session.

    `execute()` runs a statement that writes; `query()` runs a SELECT and
    returns its rows. Both raise on failure: a failure at any step leaves the
    state the next press retries from, never two types active.
    """

    def execute(self, statement: str) -> None: ...
    def query(self, statement: str) -> str: ...


@dataclass(frozen=True)
class FnSql:
    """A `SqlRunner` over two callables: the tab's `DockerSql`, the engine's seams.

    The walker sees the `execute()` calls its users make (one ledger row each)
    and not the seam behind `run`/`ask`, which is the point: one shape for the
    install, the start guard and the switch, whatever runs it.
    """

    run: Callable[[str], None]
    ask: Callable[[str], str]

    def execute(self, statement: str) -> None:
        self.run(statement)

    def query(self, statement: str) -> str:
        return self.ask(statement)


def _block(entry: CatalogEntry) -> AzerothCoreData | None:
    """This entry's AzerothCore block, or None for a family that has none."""
    native = entry.install.native
    return native.azerothcore if native is not None else None


def _types(entry: CatalogEntry) -> dict[str, AcRealmType]:
    block = _block(entry)
    return {realm.id: realm for realm in block.realm_types} if block is not None else {}


def _default_id(entry: CatalogEntry) -> str | None:
    block = _block(entry)
    return block.default_realm_type if block is not None else None


def has_realm_types(entry: CatalogEntry) -> bool:
    """Whether this entry switches realm types at all."""
    return bool(_types(entry))


def types_of(entry: CatalogEntry) -> tuple[AcRealmType, ...]:
    """This entry's realm types in catalog order; empty for every other entry (T812 C3b)."""
    block = _block(entry)
    return tuple(block.realm_types) if block is not None else ()


def active_id(entry: CatalogEntry, server_dir: Path) -> str | None:
    """The file's active type id, or None when the file is missing or unreadable."""
    try:
        raw = json.loads((server_dir / ACTIVE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("active")
    return value if isinstance(value, str) and value else None


def active(entry: CatalogEntry, server_dir: Path) -> AcRealmType:
    """The file's active type; missing/unreadable/unknown reads as the default, logged."""
    types = _types(entry)
    default = _default_id(entry)
    if not types or default is None:
        raise RealmSwitchError(f"{entry.id} has no realm types to be active")
    found = active_id(entry, server_dir)
    if found is None:
        logger.info(f"{entry.id}: no readable realm-type file, reading the default {default!r}")
        return types[default]
    known = types.get(found)
    if known is None:
        logger.warning(
            f"{entry.id}: the realm-type file names {found!r}, which this entry does not "
            f"list; reading the default {default!r}"
        )
        return types[default]
    return known


def write_active(server_dir: Path, type_id: str) -> None:
    """Publish the active type id (T812 C3a).

    Through `composegen._replace_whole()`: whole beside the file, fsynced, and
    `os.replace`d over it, so a crash leaves the old file and a retry still
    reads the old type. The rename is what moves the data; this file only says
    which set the plain schemas hold. A switch publishes in this order: the
    target's override first, then this file, then it clears the crash marker
    last, so a press killed between any two finds the marker and finishes the
    publication (`switch()`).
    """
    from yulon.catalog import composegen

    _check_id(type_id)
    composegen._replace_whole(
        server_dir / ACTIVE_FILE,
        json.dumps({"version": 1, "active": type_id}, indent=2) + "\n",
    )


def world_env_for(
    entry: CatalogEntry, server_dir: Path, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The block's `world_env` with the active realm type's keys laid over it.

    One function for the question, asked by the compose render and by any panel
    that says what the world reads: two spellings of it would be two answers.
    `base` is an explicit map the caller already built (the install's
    environment from a channel press): the type's keys are laid between the
    block and it, so the caller keeps winning and the type still beats the
    block. The bot-count carry-over still wins: it is laid over this mapping
    by the render, never inside it.
    """
    from yulon.catalog import composegen

    block = _block(entry)
    block_env = dict(block.world_env) if block is not None else {}
    if not has_realm_types(entry):
        return {**composegen.DEFAULT_WORLD_ENV, **block_env}
    layered = {**composegen.DEFAULT_WORLD_ENV, **block_env, **active(entry, server_dir).world_env}
    if base is None:
        return layered
    return {**layered, **base}


def switch_env_for(entry: CatalogEntry, type_id: str) -> dict[str, str]:
    """`world_env_for()` for `type_id`, for the switch's override rewrite.

    The switch rewrites the override for the TARGET before the servers start
    again, and the file still names the old type until the end, so the file's
    answer would be the wrong one here.
    """
    from yulon.catalog import composegen

    types = _types(entry)
    if type_id not in types:
        raise RealmSwitchError(
            f"{type_id!r} is not one of this server's realm types "
            f"{sorted(types)}; nothing was switched"
        )
    block = _block(entry)
    base = dict(block.world_env) if block is not None else {}
    return {**composegen.DEFAULT_WORLD_ENV, **base, **types[type_id].world_env}


class DatabaseUnreadable(RealmSwitchError):
    """A read a decision rests on could not be made: neither present nor absent.

    Every read the switch, the recovery, the verification or the start gate
    decides on is three-valued: present, definitively absent (the query ran
    and returned no such row or table), or unreadable (the query raised, or
    answered something no one realm type can be). Unreadable always refuses:
    an empty answer from a database that could not be asked is not a verdict.
    """


CRASH_START_REFUSAL = (
    "A realm type switch did not finish, so the server was not started. Switch realm type "
    "again on the Server tab to finish it."
)
"""The start refusal while `SWITCH_MARKER_FILE` exists: the plain schemas may hold a partial set."""

UNREADABLE_START_REFUSAL = (
    "The realm type this server's databases hold could not be read, so the server was not "
    "started. Press Start again once its database answers."
)
"""The start refusal when the marker cannot be read and a switch has ever been made here."""

IMPORT_REFUSAL = (
    "A realm type switch did not finish, so no database import was run. Switch realm type "
    "again on the Server tab to finish it first."
)
"""Install, Repair and the import's marking refuse while `SWITCH_MARKER_FILE` exists.

An import over the plain schemas mid-switch would fill them with a fresh set
and mark it as the file's type, while that type's real set sits parked: the
next switch would then call the crash note stale and strand those characters.
"""

MARK_START_REFUSAL = (
    "The databases could not be marked with this server's realm type, so the server was not "
    "started. Press Start again."
)
"""The start refusal when a pre-marker install's marker would not write or read back."""

ACCOUNT_COLUMNS: tuple[str, ...] = (
    "username",
    "salt",
    "verifier",
    "email",
    "reg_mail",
    "joindate",
    "expansion",
)
"""The AzerothCore `account` columns a first switch copies, by name and never `SELECT *`.

The same columns `controller_wow_wotlk.accounts` writes for a new AzerothCore
account (`INSERT INTO account (username, salt, verifier, expansion, reg_mail,
email, joindate)`), so a copied account is one the core's own create would
make: the SRP6 pair logs it in, `id` is the target's own AUTO_INCREMENT.
"""


def ensure_marker(sql: SqlRunner, entry: CatalogEntry, type_id: str) -> None:
    """Create the marker table and row if needed; INSERT IGNORE makes it idempotent."""
    _check_id(type_id)
    characters = entry.databases.characters
    _check_name(characters)
    _check_name(MARKER_TABLE)
    sql.execute(
        f"CREATE TABLE IF NOT EXISTS `{characters}`.`{MARKER_TABLE}` "
        "(id VARCHAR(16) PRIMARY KEY) CHARACTER SET utf8mb4;"
    )
    sql.execute(f"INSERT IGNORE INTO `{characters}`.`{MARKER_TABLE}` (id) VALUES ('{type_id}');")


def _rows(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _tables_in(query: Callable[[str], str], schemas: Sequence[str]) -> list[list[str]]:
    """Each schema's tables, in name order; a schema that does not exist has none.

    Raises `DatabaseUnreadable` when a listing cannot be made: an error is never
    an empty schema, or a switch would import a fresh set over a park it could
    not see, and a verification would pass on a park it could not read.
    """
    found: list[list[str]] = []
    for schema in schemas:
        _check_name(schema)
        try:
            rows = query(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA = '{schema}' ORDER BY TABLE_NAME;"
            )
        except Exception as exc:
            raise DatabaseUnreadable(
                f"The {schema} schema could not be listed ({exc}); nothing was switched."
            ) from exc
        tables = _rows(rows)
        for table in tables:
            _check_name(table)
        found.append(tables)
    return found


def _marker_in(query: Callable[[str], str], schema: str) -> str | None:
    """The realm type id the marker in `schema` names, None when it has none.

    None only when the listing ran and showed no marker table, or the table is
    there with no row yet (`ensure_marker()` killed between its two
    statements): neither claims a type. A table that cannot be read, or reads
    anything but one realm type id, raises `DatabaseUnreadable`.
    """
    (tables,) = _tables_in(query, [schema])
    if MARKER_TABLE not in tables:
        return None
    try:
        rows = query(f"SELECT id FROM `{schema}`.`{MARKER_TABLE}`;")
    except Exception as exc:
        raise DatabaseUnreadable(
            f"The realm type marker in {schema} could not be read ({exc}); nothing was switched."
        ) from exc
    values = _rows(rows)
    if not values:
        return None
    if len(values) == 1 and _ID.fullmatch(values[0]):
        return values[0]
    raise DatabaseUnreadable(
        f"The realm type marker in {schema} reads {values[:3]}, which is not one realm type; "
        "nothing was switched."
    )


def read_marker(query: Callable[[str], str], entry: CatalogEntry) -> str | None:
    """The type id the plain characters schema says it holds; None when it has no marker.

    Raises `DatabaseUnreadable` when the schema or its marker cannot be read:
    absent and unreadable are two answers, and only the first is a verdict.
    """
    return _marker_in(query, entry.databases.characters)


@dataclass(frozen=True)
class SwitchImport:
    """The trusted context a first switch hands its own import (T812 C8 D7).

    `switch()` writes its crash note (`imported` true) and then runs the import
    of the target's fresh databases; every other import refuses on that very
    note (`IMPORT_REFUSAL`). Only this context lets an import through, and only
    while the note on disk names the same target with its import begun: the
    note, not the object, is the proof, so a context built anywhere else for
    any other state still refuses. Install and "Repair the database" never
    pass one, and keep refusing while the note exists.
    """

    server_dir: Path
    target: str


def import_refusal(
    entry: CatalogEntry, server_dir: Path, *, switch: SwitchImport | None = None
) -> str | None:
    """`IMPORT_REFUSAL` while a switch is unfinished on a realm-types entry, else None.

    `switch` is the first switch's own import (`SwitchImport`): it passes only
    when the crash note in this folder names its target with `imported` true.
    A note that cannot be read refuses like any other.
    """
    if not has_realm_types(entry) or not (server_dir / SWITCH_MARKER_FILE).exists():
        return None
    if switch is None or switch.server_dir != server_dir:
        return IMPORT_REFUSAL
    try:
        crash = _read_crash(server_dir)
    except RealmSwitchError:
        return IMPORT_REFUSAL
    if crash is None or crash.target != switch.target or not crash.imported:
        return IMPORT_REFUSAL
    return None


def mismatch_sentence(marker: str, want: str) -> str:
    """The start and switch refusal for databases holding another type than the file says."""
    return (
        f"The databases hold the {marker} realm type but this server is set to {want}. "
        f"Switch realm type to {marker} on the Server tab to put it right."
    )


def refusal(
    entry: CatalogEntry, server_dir: Path, sql: SqlRunner | None, *, strict: bool = False
) -> str | None:
    """Why the server must not start now, or None; marks a pre-marker install (T812 C3a).

    A crash marker refuses outright: a switch that did not finish may have left
    a partial import in the plain schemas, with no marker to refuse on. The
    marker travels WITH the data and the file travels with the folder: a
    mismatch means this folder's databases hold another type's characters, and
    starting would run them under the wrong rules. An absent marker over a
    characters table is a pre-marker install, marked here with the file's type
    and read back before the start goes on; an absent marker over no
    characters table is a database with nothing in it yet, which nothing here
    marks. `sql` is None when the database cannot be asked at all.

    A marker that cannot be read refuses once a realm-type file exists (a
    switch has been made here, so the plain set may be any type); before one
    exists only the default type can ever have been in the plain schemas.
    `strict` (the Server tab's gate, which started the database itself) refuses
    every unreadable read, file or no file.
    """
    if not has_realm_types(entry):
        return None
    if (server_dir / SWITCH_MARKER_FILE).exists():
        return CRASH_START_REFUSAL
    claimed = strict or (server_dir / ACTIVE_FILE).exists()
    if sql is None:
        return UNREADABLE_START_REFUSAL if claimed else None
    want = active(entry, server_dir).id
    try:
        marker = read_marker(sql.query, entry)
        if marker is None:
            return _mark_at_start(entry, sql, want)
    except RealmSwitchError as exc:
        logger.warning(f"{entry.id}: the realm type marker could not be read: {exc}")
        return UNREADABLE_START_REFUSAL if claimed else None
    return None if marker == want else mismatch_sentence(marker, want)


def _mark_at_start(entry: CatalogEntry, sql: SqlRunner, want: str) -> str | None:
    (tables,) = _tables_in(sql.query, [entry.databases.characters])
    if "characters" not in tables:
        logger.info(f"{entry.id}: no characters table yet; the realm type marker waits")
        return None
    try:
        ensure_marker(sql, entry, want)
        back = read_marker(sql.query, entry)
    except Exception as exc:  # noqa: BLE001 - any failure refuses the start, worded once
        logger.warning(f"{entry.id}: the realm type marker could not be written: {exc}")
        return MARK_START_REFUSAL
    if back != want:
        logger.warning(f"{entry.id}: the realm type marker reads {back!r} after writing {want!r}")
        return MARK_START_REFUSAL
    logger.info(f"{entry.id}: marked the pre-marker databases as the {want} realm type")
    return None


def before_servers_gate(
    entry: CatalogEntry,
    server_dir: Path,
    *,
    start_db: Callable[[], object],
    sql: SqlRunner,
) -> Callable[[], str | None] | None:
    """The Controller's realm-type gate, asked once every other refusal has passed.

    The database alone is started first (`start_db`), then `refusal()` reads
    the marker: the gate never decides on a database that was not up. A crash
    marker refuses before anything is started, and any start or read failure
    refuses (`strict`), realm-type file or not. None for an entry without
    realm types, so every other server starts exactly as it did.
    """
    if not has_realm_types(entry):
        return None

    def gate() -> str | None:
        if (server_dir / SWITCH_MARKER_FILE).exists():
            return CRASH_START_REFUSAL
        try:
            start_db()
        except Exception as exc:  # noqa: BLE001 - a database that will not start is unreadable
            logger.warning(f"{entry.id}: the database could not be started to read its type: {exc}")
            return UNREADABLE_START_REFUSAL
        return refusal(entry, server_dir, sql, strict=True)

    return gate


def realm_name_update(entry: CatalogEntry, realm_name: str) -> str:
    """The UPDATE giving the realm row `realm_name`; the catalog already refused quotes."""
    if any(mark in realm_name for mark in ("'", '"', "\\")):
        raise RealmSwitchError(
            f"the realm name {realm_name!r} cannot be written into an UPDATE statement"
        )
    databases = entry.databases
    _check_name(databases.auth)
    table = entry.realmlist.table
    _check_name(table)
    return (
        f"UPDATE `{databases.auth}`.`{table}` SET name='{realm_name}' "
        f"WHERE id={entry.realmlist.realm_id};"
    )


def _check_name(name: str) -> None:
    if not name or not all(ch.isalnum() or ch == "_" for ch in name):
        raise RealmSwitchError(f"{name!r} is not a schema or table name this switch writes")


def _check_id(type_id: str) -> None:
    if not _ID.fullmatch(type_id):
        raise RealmSwitchError(f"{type_id!r} is not a realm type id this switch writes")


def _parked(entry: CatalogEntry, type_id: str) -> tuple[str, str, str]:
    """The `<db>_<id>` schemas holding `type_id` while it is inactive."""
    _check_id(type_id)
    databases = entry.databases
    if not databases.playerbots:
        raise RealmSwitchError(f"{entry.id} names no playerbots schema to switch")
    for name in (databases.auth, databases.characters, databases.playerbots):
        _check_name(name)
    return (
        f"{databases.auth}_{type_id}",
        f"{databases.characters}_{type_id}",
        f"{databases.playerbots}_{type_id}",
    )


def _plain(entry: CatalogEntry) -> tuple[str, str, str]:
    databases = entry.databases
    if not databases.playerbots:
        raise RealmSwitchError(f"{entry.id} names no playerbots schema to switch")
    return (databases.auth, databases.characters, databases.playerbots)


def switch(
    entry: CatalogEntry,
    server_dir: Path,
    target_id: str,
    *,
    sql: SqlRunner,
    servers_running: Callable[[], bool],
    run_import: Callable[[SwitchImport], Iterator[str]],
    set_realm_row: Callable[[str, str], None],
    write_override: Callable[[Mapping[str, str]], None],
    bot_prefix: str,
    check_import: Callable[[], None] | None = None,
) -> Iterator[str]:
    """Move every table set to `target_id`, then point the folder at it.

    1 refuse while a server runs (its connections hold metadata locks); 2 a
    crash marker left by an earlier press is settled first (an interrupted
    first import is put back, but only under every check in
    `_recover_interrupted()`); 3 read the marker and refuse a file/marker split
    unless the target IS the marker (the repair: rewrite the file + override,
    no rename); 4 a plain set with no marker is marked as the file's type
    before anything moves, so it travels marked; 5 refuse while the active
    type's park schemas are non-empty, or the target's park reads as another
    type; 6 create the active parks with the source schemas' charset/collation;
    7 ONE `RENAME TABLE` moving all three sets both ways; 8 drop the emptied
    target parks; 9 on a first switch record that its import began, run the
    import, name the realm row, copy and verify the player's accounts, and
    mark the type LAST (the marker is the import's commit); verify, then
    publish: 10 rewrite the override, 11 write the file, 12 clear the crash
    marker last. A press killed between those finds the marker and finishes
    (the repair, or a press for the type the file names, which clears a crash
    marker only when `_check_stale_crash()` proves it stale). Every read a
    step decides on refuses when it cannot be made.

    `run_import` is the import STAGE alone (T812 C8 D8): the db-import into
    the fresh plain schemas, handed the `SwitchImport` context that lets it
    past the crash note (D7); never the installer's start or its marking,
    since the marker is this function's to write, last. `check_import` raises
    every refusal of that import that can be known beforehand (D6); a first
    switch asks it before the crash note and the rename, so nothing is moved
    for an import that was always going to refuse.
    """
    types = _types(entry)
    if target_id not in types:
        raise RealmSwitchError(
            f"{target_id!r} is not one of this server's realm types "
            f"{sorted(types)}; nothing was switched"
        )
    if servers_running():
        raise RealmSwitchError(
            "A server container is still running: Stop the server first, then switch "
            "realm type. Nothing was switched."
        )
    current = active(entry, server_dir).id
    crash = _read_crash(server_dir)
    if crash is None and current == target_id:
        yield f"The server is already on the {target_id} realm type; nothing was switched."
        return
    if crash is not None:
        yield from _recover_interrupted(entry, sql, server_dir, current, target_id, crash)
        crash = _read_crash(server_dir)
    marker = read_marker(sql.query, entry)
    if marker is not None and marker != current:
        if crash is not None and target_id == current:
            # The way out of a half-done switch its retry cannot finish (cold review).
            yield from _undo_late(entry, sql, server_dir, current, marker, crash, write_override)
            return
        if target_id != marker:
            raise RealmSwitchError(mismatch_sentence(marker, current))
        if crash is not None:
            # T812 C8 D11: a switch that failed after its marker (the override, the file):
            # its own half-done state, proven before it is finished, never refused.
            _check_resumable(current, marker, crash)
            yield from _carry_realm_address(entry, sql, current, marker)
            _verify(sql, entry, current, marker)
            yield (
                f"The last switch to {marker} stopped after its databases were moved; "
                "finishing it."
            )
        else:
            yield (
                f"The databases already hold {marker} but the file said {current}; "
                "the file and the override were rewritten, and no database was touched."
            )
        write_override(switch_env_for(entry, marker))
        write_active(server_dir, marker)
        _clear_crash(server_dir)
        return
    if marker is None:
        yield from _mark_before_switch(entry, sql, current)
    if current == target_id:
        if crash is not None:
            _check_stale_crash(entry, sql, current, crash)
            write_override(switch_env_for(entry, current))
            _clear_crash(server_dir)
        yield f"The server is already on the {target_id} realm type; nothing was switched."
        return
    yield from _refuse_non_empty_parks(entry, sql, current)
    parked = _parked(entry, current)
    for schema, source in zip(parked, _plain(entry), strict=True):
        _create_park_like(sql, source, schema)
    target_park = _parked(entry, target_id)
    parked_tables = _tables_in(sql.query, target_park)
    first = not any(parked_tables)
    if not first:
        held = _marker_in(sql.query, target_park[1])
        if held != target_id:
            raise RealmSwitchError(
                f"The parked {target_id} databases do not read as the {target_id} realm type "
                f"({held or 'no marker'}): renaming them in would start the wrong "
                "characters. Nothing was switched."
            )
    if first and check_import is not None:
        check_import()
    _write_crash(server_dir, target_id, imported=False, source=current)
    yield from _rename_both_ways(entry, sql, current, target_id, parked, target_park, parked_tables)
    for schema in target_park:
        _drop_if_empty(sql, schema)
    if first:
        # Recorded BEFORE the import writes anything: only a set this switch began
        # importing is ever dropped by `_recover_interrupted()`.
        _write_crash(server_dir, target_id, imported=True, source=current)
        yield (
            f"No {target_id} databases exist yet; importing them for the first time "
            "(a few minutes), then copying the player accounts over."
        )
        yield from run_import(SwitchImport(server_dir=server_dir, target=target_id))
        set_realm_row(types[target_id].realm_name, str(entry.ports.world))
        yield from _carry_realm_address(entry, sql, current, target_id)
        yield from _copy_player_accounts(entry, sql, current, bot_prefix)
        ensure_marker(sql, entry, target_id)
    else:
        yield from _carry_realm_address(entry, sql, current, target_id)
    _verify(sql, entry, current, target_id)
    # Published override, then file, then the crash marker cleared LAST: a press
    # killed between any two still finds the marker and finishes the publication.
    write_override(switch_env_for(entry, target_id))
    write_active(server_dir, target_id)
    _clear_crash(server_dir)
    yield f"The server now runs the {target_id} realm type ({types[target_id].realm_name})."


def pre_check(
    entry: CatalogEntry,
    server_dir: Path,
    target_id: str,
    *,
    sql: SqlRunner,
    bot_prefix: str,
    check_import: Callable[[], None] | None = None,
) -> str:
    """Every validation that runs without the server stopped (T812 C3b fix round 1).

    `check_import` (T812 C8 D6): on a first switch to `target_id` (its park
    empty) the import's own refusals are asked here too, before the Stop.
    """
    types = _types(entry)
    if target_id not in types:
        raise RealmSwitchError(
            f"{target_id!r} is not one of this server's realm types "
            f"{sorted(types)}; nothing was switched"
        )
    if not bot_prefix or not _SAFE_PREFIX.fullmatch(bot_prefix):
        raise RealmSwitchError(
            f"The bot account prefix {bot_prefix!r} cannot be used in a query; "
            "nothing was copied."
        )
    current = active(entry, server_dir).id
    crash = _read_crash(server_dir)
    if crash is None and current == target_id:
        return current
    pending_recovery = False
    if crash is not None:
        pending_recovery = _refuse_unless_recoverable(
            entry, sql, server_dir, current, target_id, crash
        )
    marker = read_marker(sql.query, entry)
    if marker is not None and marker != current and crash is not None and target_id == current:
        # The undo of a half-done switch (`_undo_late`): its own state, read only here.
        _check_resumable(current, marker, crash)
        _verify(sql, entry, current, marker)
        return current
    if marker is not None and marker != current and target_id != marker:
        raise RealmSwitchError(mismatch_sentence(marker, current))
    if marker is not None and marker != current:
        # The repair, or (with a crash note) a switch that failed after its marker:
        # nothing is renamed again, so the park guards below do not apply (T812 C8 D11).
        if crash is not None:
            _check_resumable(current, marker, crash)
            _verify(sql, entry, current, marker)
        return current
    if marker is None and not pending_recovery:
        _check_markable(entry, sql, current)
    if current == target_id:
        if crash is not None and not pending_recovery:
            _check_stale_crash(entry, sql, current, crash)
        return current
    if not pending_recovery:
        _check_parks(entry, sql, current, target_id)
    if check_import is not None and not any(_tables_in(sql.query, _parked(entry, target_id))):
        check_import()
    return current


def _refuse_unless_recoverable(
    entry: CatalogEntry,
    sql: SqlRunner,
    server_dir: Path,
    current: str,
    target_id: str,
    crash: _Crash,
) -> bool:
    """The recovery's refusals, read without writing; True when it would write."""
    if read_marker(sql.query, entry) is not None:
        return False
    if crash.target != target_id and target_id != current:
        raise RealmSwitchError(
            f"A switch to {crash.target} did not finish. Switch realm type to {crash.target} "
            f"again to finish it, or to {current} to undo it. Nothing was switched."
        )
    parked = _parked(entry, current)
    held = _marker_in(sql.query, parked[1])
    if held != current:
        raise RealmSwitchError(
            f"A switch to {crash.target} did not finish, and the parked {current} databases "
            f"do not read as the {current} realm type ({held or 'no marker'}), so nothing "
            "was dropped or switched."
        )
    plain = _plain(entry)
    plain_tables = _tables_in(sql.query, plain)
    if any(plain_tables) and not crash.imported:
        raise RealmSwitchError(
            f"A switch to {crash.target} did not finish, and the plain databases hold tables "
            "no import of that switch wrote, so nothing was dropped or switched."
        )
    return True


def _check_markable(entry: CatalogEntry, sql: SqlRunner, current: str) -> None:
    """The mark guard's read, refusing before the server stops."""
    tables = _tables_in(sql.query, [entry.databases.characters])[0]
    if "characters" not in tables:
        raise RealmSwitchError(
            f"The {entry.databases.characters} schema holds no characters table and no realm "
            "type marker, so there is no set to switch. Nothing was switched."
        )


def _check_parks(entry: CatalogEntry, sql: SqlRunner, current: str, target_id: str) -> None:
    """The park guards' reads, refusing before the server stops (T812 C3b fix round 1)."""
    parked = _parked(entry, current)
    holding = sorted(
        f"{schema}.{table}"
        for schema, names in zip(parked, _tables_in(sql.query, parked), strict=True)
        for table in names
    )
    if holding:
        listed = ", ".join(holding[:5]) + ("…" if len(holding) > 5 else "")
        raise RealmSwitchError(
            f"The parked {current} schemas are not empty ({listed}): a switch that "
            "renamed now would orphan those tables. Nothing was switched."
        )
    target_park = _parked(entry, target_id)
    parked_tables = _tables_in(sql.query, target_park)
    if any(parked_tables):
        held = _marker_in(sql.query, target_park[1])
        if held != target_id:
            raise RealmSwitchError(
                f"The parked {target_id} databases do not read as the {target_id} realm type "
                f"({held or 'no marker'}): renaming them in would start the wrong "
                "characters. Nothing was switched."
            )


def _mark_before_switch(entry: CatalogEntry, sql: SqlRunner, current: str) -> Iterator[str]:
    """Mark a pre-marker plain set as the file's type before it is parked.

    Without it the parked set carries no marker, and the switch back cannot
    tell it from a partial import: the review's reproduced data loss. Only a
    set with a characters table is marked; anything else refuses.
    """
    (tables,) = _tables_in(sql.query, [entry.databases.characters])
    if "characters" not in tables:
        raise RealmSwitchError(
            f"The {entry.databases.characters} schema holds no characters table and no realm "
            "type marker, so there is no set to switch. Nothing was switched."
        )
    ensure_marker(sql, entry, current)
    if read_marker(sql.query, entry) != current:
        raise RealmSwitchError(
            f"The databases could not be marked as the {current} realm type before the "
            "switch. Nothing was switched."
        )
    yield f"Marked the databases as the {current} realm type before switching."


@dataclass(frozen=True)
class _Crash:
    """What `SWITCH_MARKER_FILE` says: the target, and whether its fresh import began."""

    target: str
    imported: bool
    source: str | None
    """The file's type when the switch began; None in a note from before it was recorded."""


def _read_crash(server_dir: Path) -> _Crash | None:
    """The crash marker, None when there is none; refuses one that cannot be read."""
    path = server_dir / SWITCH_MARKER_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RealmSwitchError(
            f"The note of an unfinished realm type switch ({path}) cannot be read ({exc}), "
            "so nothing was switched."
        ) from exc
    target = raw.get("target") if isinstance(raw, dict) else None
    if not isinstance(target, str) or not _ID.fullmatch(target):
        raise RealmSwitchError(
            f"The note of an unfinished realm type switch ({path}) names no realm type, "
            "so nothing was switched."
        )
    source = raw.get("source")
    return _Crash(
        target=target,
        imported=raw.get("imported") is True,
        source=source if isinstance(source, str) and _ID.fullmatch(source) else None,
    )


def _write_crash(server_dir: Path, target_id: str, *, imported: bool, source: str) -> None:
    from yulon.catalog import composegen

    _check_id(target_id)
    _check_id(source)
    composegen._replace_whole(
        server_dir / SWITCH_MARKER_FILE,
        json.dumps({"version": 2, "target": target_id, "imported": imported, "source": source})
        + "\n",
    )


def _check_stale_crash(entry: CatalogEntry, sql: SqlRunner, current: str, crash: _Crash) -> None:
    """Refuse to clear a crash marker unless the databases prove it stale.

    Called with the plain marker already read as `current`. The marker is
    stale only when `current`'s own park is empty (its set is the plain one,
    not parked as well: an import mid-switch would leave a fresh plain set
    marked `current` beside the real parked one), and the other type the
    switch involved is either not parked at all or parked under its own
    marker. Any read that cannot be made refuses too.
    """
    own = _parked(entry, current)
    held = sorted(
        f"{schema}.{table}"
        for schema, names in zip(own, _tables_in(sql.query, own), strict=True)
        for table in names
    )
    if held:
        raise RealmSwitchError(
            f"The {current} databases are in the plain schemas and also parked ({held[0]}), so "
            f"the note of the unfinished switch to {crash.target} was kept and nothing was "
            "switched."
        )
    other = crash.source if crash.target == current else crash.target
    if other is None or other == current:
        return
    parked = _parked(entry, other)
    if any(_tables_in(sql.query, parked)) and _marker_in(sql.query, parked[1]) != other:
        raise RealmSwitchError(
            f"The parked {other} databases do not read as the {other} realm type, so the note "
            f"of the unfinished switch to {crash.target} was kept and nothing was switched."
        )


def _clear_crash(server_dir: Path) -> None:
    (server_dir / SWITCH_MARKER_FILE).unlink(missing_ok=True)


def _recover_interrupted(
    entry: CatalogEntry,
    sql: SqlRunner,
    server_dir: Path,
    current: str,
    target_id: str,
    crash: _Crash,
) -> Iterator[str]:
    """Put back a first switch killed after its rename, so this press starts clean.

    Nothing is dropped unless EVERY one of these reads holds: the plain marker
    is definitively absent (a marked plain set is a whole set: the repair or
    the normal path takes it); the press is for the crash's own target, or
    back to the file's type (the undo); the parked set of the file's type
    reads that type's marker (so it is the whole pre-switch set, the rename
    being atomic); and the plain schemas are empty or the crash marker
    records that this switch began their import. Any other state refuses with
    nothing changed.
    """
    if read_marker(sql.query, entry) is not None:
        return
    if crash.target != target_id and target_id != current:
        raise RealmSwitchError(
            f"A switch to {crash.target} did not finish. Switch realm type to {crash.target} "
            f"again to finish it, or to {current} to undo it. Nothing was switched."
        )
    parked = _parked(entry, current)
    held = _marker_in(sql.query, parked[1])
    if held != current:
        raise RealmSwitchError(
            f"A switch to {crash.target} did not finish, and the parked {current} databases "
            f"do not read as the {current} realm type ({held or 'no marker'}), so nothing "
            "was dropped or switched."
        )
    plain = _plain(entry)
    plain_tables = _tables_in(sql.query, plain)
    if any(plain_tables) and not crash.imported:
        raise RealmSwitchError(
            f"A switch to {crash.target} did not finish, and the plain databases hold tables "
            "no import of that switch wrote, so nothing was dropped or switched."
        )
    yield (
        f"The last switch to {crash.target} was interrupted during its first import; "
        "its partial databases were dropped and the previous type's tables put back."
    )
    for schema, names in zip(plain, plain_tables, strict=True):
        if names:
            sql.execute(f"DROP TABLE {', '.join(f'`{schema}`.`{t}`' for t in names)};")
    moves = [
        f"`{park}`.`{table}` TO `{schema}`.`{table}`"
        for schema, park, names in zip(plain, parked, _tables_in(sql.query, parked), strict=True)
        for table in names
    ]
    if moves:
        sql.execute("RENAME TABLE " + ", ".join(moves) + ";")
    for schema in parked:
        _drop_if_empty(sql, schema)
    _clear_crash(server_dir)


def _refuse_non_empty_parks(entry: CatalogEntry, sql: SqlRunner, current: str) -> Iterator[str]:
    parked = _parked(entry, current)
    holding = sorted(
        f"{schema}.{table}"
        for schema, names in zip(parked, _tables_in(sql.query, parked), strict=True)
        for table in names
    )
    if holding:
        listed = ", ".join(holding[:5]) + ("…" if len(holding) > 5 else "")
        raise RealmSwitchError(
            f"The parked {current} schemas are not empty ({listed}): a switch that "
            "renamed now would orphan those tables. Nothing was switched."
        )
    yield from ()


def _create_park_like(sql: SqlRunner, source: str, schema: str) -> None:
    _check_name(source)
    _check_name(schema)
    try:
        rows = sql.query(
            "SELECT DEFAULT_CHARACTER_SET_NAME, DEFAULT_COLLATION_NAME FROM "
            f"information_schema.SCHEMATA WHERE SCHEMA_NAME = '{source}';"
        )
    except Exception as exc:
        raise RealmSwitchError(
            f"The {source} schema could not be read ({exc}); nothing was switched."
        ) from exc
    parts = rows.split()
    if len(parts) != 2:
        raise RealmSwitchError(
            f"The {source} schema's character set reads {rows.strip()!r}; nothing was switched."
        )
    _check_name(parts[0])
    if not all(ch.isalnum() or ch in "_-" for ch in parts[1]):
        raise RealmSwitchError(
            f"The {source} schema's collation {parts[1]!r} is not one this switch "
            "writes; nothing was switched."
        )
    sql.execute(
        f"CREATE DATABASE IF NOT EXISTS `{schema}` CHARACTER SET {parts[0]} COLLATE {parts[1]};"
    )


def _rename_both_ways(
    entry: CatalogEntry,
    sql: SqlRunner,
    current: str,
    target_id: str,
    parked: Sequence[str],
    target_parked: Sequence[str],
    parked_tables: Sequence[Sequence[str]],
) -> Iterator[str]:
    plain = _plain(entry)
    moves: list[str] = []
    for schema, park, names in zip(plain, parked, _tables_in(sql.query, plain), strict=True):
        for table in names:
            moves.append(f"`{schema}`.`{table}` TO `{park}`.`{table}`")
    for park, schema, held in zip(target_parked, plain, parked_tables, strict=True):
        for table in held:
            _check_name(table)
            moves.append(f"`{park}`.`{table}` TO `{schema}`.`{table}`")
    if not moves:
        raise RealmSwitchError(
            f"The {current} schemas hold no tables to park; nothing was switched."
        )
    try:
        sql.execute("RENAME TABLE " + ", ".join(moves) + ";")
    except Exception as exc:
        raise RealmSwitchError(
            f"The databases could not be renamed ({exc}); nothing was switched. "
            "Press again to retry."
        ) from exc
    yield (
        f"Moved {len(moves)} tables between the plain schemas and the "
        f"{current}/{target_id} parks in one statement."
    )


def _drop_if_empty(sql: SqlRunner, schema: str) -> None:
    """Drop `schema` only when it reads empty; one that cannot be listed is left standing."""
    try:
        (tables,) = _tables_in(sql.query, [schema])
    except RealmSwitchError as exc:
        logger.warning(f"{schema} was left in place: {exc}")
        return
    if not tables:
        sql.execute(f"DROP DATABASE IF EXISTS `{schema}`;")


def _like_prefix(prefix: str) -> str:
    """`prefix` for a `LIKE ... ESCAPE '!'`: `_`, `%` and `!` match only themselves."""
    return "".join(f"!{ch}" if ch in "!_%" else ch for ch in prefix)


def _one_int(sql: SqlRunner, statement: str, what: str) -> int | None:
    """The one integer `statement` answers, None for no row; anything else refuses."""
    try:
        values = _rows(sql.query(statement))
    except Exception as exc:
        raise DatabaseUnreadable(
            f"{what} could not be read ({exc}); nothing further was written. Press again to "
            "retry."
        ) from exc
    if not values:
        return None
    if len(values) == 1 and values[0].isdigit():
        return int(values[0])
    raise DatabaseUnreadable(
        f"{what} reads {values[:3]}; nothing further was written. Press again to retry."
    )


def _copy_player_accounts(
    entry: CatalogEntry, sql: SqlRunner, source_id: str, bot_prefix: str
) -> Iterator[str]:
    """Copy the player's accounts into the fresh auth schema, each verified (D2).

    Explicit columns (`ACCOUNT_COLUMNS`), never `SELECT *` and never `INSERT
    IGNORE`: the target allocates its own `id`, a username the target already
    holds refuses, and `account_access` (keyed `id`, the account's id) is
    copied under the TARGET's id. Each account then reads back with its SRP6
    pair intact and every GM-rights row matched, or the switch refuses before
    the marker is written; only verified accounts are counted.
    """
    from yulon import useraccounts

    _check_id(source_id)
    if not bot_prefix or not _SAFE_PREFIX.fullmatch(bot_prefix):
        raise RealmSwitchError(
            f"The bot account prefix {bot_prefix!r} cannot be used in a query; "
            "nothing was copied."
        )
    databases = entry.databases
    source = f"{databases.auth}_{source_id}"
    _check_name(source)
    target = databases.auth
    _check_name(target)
    try:
        rows = sql.query(
            f"SELECT username FROM `{source}`.account "
            f"WHERE UPPER(username) NOT LIKE '{_like_prefix(bot_prefix.upper())}%' ESCAPE '!' "
            f"AND username <> '{useraccounts.AHBOT_ACCOUNT}';"
        )
    except Exception as exc:
        raise RealmSwitchError(
            f"The player accounts could not be listed ({exc}); nothing was copied."
        ) from exc
    names = _rows(rows)
    skipped = sorted({name for name in names if not _SAFE_NAME.fullmatch(name)})
    columns = ", ".join(ACCOUNT_COLUMNS)
    copied = 0
    for name in sorted({name for name in names if _SAFE_NAME.fullmatch(name)}):
        account = f"The {name} account"
        source_key = _one_int(
            sql, f"SELECT id FROM `{source}`.account WHERE username = '{name}';", account
        )
        if source_key is None:
            raise RealmSwitchError(
                f"{account} is gone from {source}; nothing further was written. Press again "
                "to retry."
            )
        lookup = f"SELECT id FROM `{target}`.account WHERE username = '{name}';"
        if _one_int(sql, lookup, account) is not None:
            raise RealmSwitchError(
                f"The new realm type's databases already hold an account named {name}; it "
                "was not overwritten and nothing further was written."
            )
        sql.execute(
            f"INSERT INTO `{target}`.account ({columns}) SELECT {columns} "
            f"FROM `{source}`.account WHERE id = {source_key};"
        )
        target_key = _one_int(sql, lookup, account)
        same = _one_int(
            sql,
            f"SELECT COUNT(*) FROM `{target}`.account t JOIN `{source}`.account s "
            "ON s.username = t.username AND s.salt = t.salt AND s.verifier = t.verifier "
            f"WHERE t.id = {target_key} AND s.id = {source_key};",
            account,
        )
        if target_key is None or same != 1:
            raise RealmSwitchError(
                f"{account} did not arrive in the new realm type's databases with its "
                "password intact; nothing further was written. Press again to retry."
            )
        sql.execute(
            f"INSERT INTO `{target}`.account_access (id, gmlevel, RealmID) "
            f"SELECT {target_key}, gmlevel, RealmID FROM `{source}`.account_access "
            f"WHERE id = {source_key};"
        )
        want = _one_int(
            sql,
            f"SELECT COUNT(*) FROM `{source}`.account_access WHERE id = {source_key};",
            f"{account}'s GM rights",
        )
        got = _one_int(
            sql,
            f"SELECT COUNT(*) FROM `{target}`.account_access t JOIN `{source}`.account_access s "
            "ON s.RealmID = t.RealmID AND s.gmlevel = t.gmlevel "
            f"WHERE t.id = {target_key} AND s.id = {source_key};",
            f"{account}'s GM rights",
        )
        if want != got:
            raise RealmSwitchError(
                f"{account}'s GM rights did not all arrive in the new realm type's databases "
                f"({got} of {want}); nothing further was written. Press again to retry."
            )
        copied += 1
    yield f"Copied {copied} player accounts into the new realm type's databases."
    if skipped:
        yield (
            f"Left {len(skipped)} accounts uncopied, whose names cannot be copied "
            f"by name ({', '.join(skipped[:5])}{'…' if len(skipped) > 5 else ''})."
        )


def _check_resumable(current: str, marker: str, crash: _Crash) -> None:
    """Refuse a late-failed switch's state unless it is provably that switch's own (D11).

    The plain set already reads as `marker` (it traveled with the rename, or the
    first switch wrote it), so the crash note must name that same target and the
    file's type as its source. `_verify()` then proves the rest: the target's
    park is empty and the file's type is parked whole under its own marker.
    """
    if crash.target != marker or (crash.source is not None and crash.source != current):
        raise RealmSwitchError(
            f"The note of an unfinished switch names {crash.target} (from "
            f"{crash.source or 'an unrecorded type'}), but the databases hold {marker} and this "
            f"server is set to {current}, so nothing was switched."
        )


_ADDRESS = re.compile(r"[A-Za-z0-9.:-]{1,255}")
"""What a realm row address may be before it is spliced into SQL (`networking._sql_literal`)."""

FALLBACK_ADDRESS = "127.0.0.1"
"""The address a realm row gets when neither the parked row nor the new one names a usable one."""


def _carry_failed_sentence(target_id: str, current: str, port: int, why: str) -> str:
    """The refusal when the new type's realm row cannot be set: the state, and the ways out."""
    return (
        f"The {target_id} realm type's realm row could not be given this server's address and "
        f"world port {port} ({why}). The server is stopped and still set to {current}. Press "
        f"Switch realm type to {target_id} again to retry, or to {current} to put the {current} "
        "realm type back."
    )


def _realm_cells(sql: SqlRunner, statement: str, width: int, what: str) -> list[list[str]] | None:
    """The tab-separated rows `statement` answers, each `width` cells; None if unreadable."""
    try:
        text = sql.query(statement)
    except Exception as exc:  # noqa: BLE001 - an unreadable row is the fallback's case
        logger.warning(f"{what} could not be read: {exc}")
        return None
    rows = [line.split("\t") for line in text.splitlines() if line.strip()]
    if any(len(row) != width for row in rows):
        logger.warning(f"{what} reads {rows[:2]}, which is not a realm row")
        return None
    return rows


def _usable(value: str | None) -> str | None:
    """An address cell that can be written back: not NULL, not empty, a plain address token."""
    if value is None or value == "NULL" or not _ADDRESS.fullmatch(value):
        return None
    return value


def _carry_realm_address(
    entry: CatalogEntry, sql: SqlRunner, source_id: str, target_id: str
) -> Iterator[str]:
    """The realm row's address and this server's world port, onto the type now plain (D13).

    The address a client is told belongs to the server, not to a realm type: the
    Network tab set it on the row of the type that was plain then. A fresh import
    seeds `127.0.0.1:8085`, and a parked type keeps the address of the day it was
    parked, so on every switch the row of the type moving in takes the address
    (and LAN address) of the row just parked, and the world port this server
    publishes.

    Robust to the rows it finds (cold review): a parked row that is missing, NULL
    or not an address falls back to the new row's own address, then to
    `FALLBACK_ADDRESS`, and says so; a missing LAN address takes the address; a
    new type with no realm row gets one INSERTed with the type's realm name. The
    values are written as literals and read back exactly. A row that still cannot
    be set refuses with the server's state and both ways out: the retry, or the
    switch back to the file's type, which `_undo_late()` makes possible.
    """
    _check_id(source_id)
    _check_id(target_id)
    auth = entry.databases.auth
    source = f"{auth}_{source_id}"
    realm = entry.realmlist
    for name in (auth, source, realm.table, realm.address_column):
        _check_name(name)
    lan = realm.local_address_column
    if lan:
        _check_name(lan)
    port = int(entry.ports.world)
    key = int(realm.realm_id)
    columns = f"`{realm.address_column}`" + (f", `{lan}`" if lan else "")
    width = 2 if lan else 1

    def read(schema: str, extra: str = "") -> list[list[str]] | None:
        return _realm_cells(
            sql,
            f"SELECT {columns}{extra} FROM `{schema}`.`{realm.table}` WHERE id = {key};",
            width + (1 if extra else 0),
            f"The {schema} realm row",
        )

    parked = read(source)
    here = read(auth)
    address = _usable(parked[0][0]) if parked else None
    local = _usable(parked[0][1]) if parked and lan else None
    if address is None:
        address = (_usable(here[0][0]) if here else None) or FALLBACK_ADDRESS
        yield (
            f"The {source_id} realm row named no address to carry over, so the {target_id} "
            f"realm row says {address}. Set the realm address on the Network tab if players "
            "connect from another computer."
        )
    local = local or address
    sets = f"`{realm.address_column}` = '{address}'" + (f", `{lan}` = '{local}'" if lan else "")
    try:
        if here:
            sql.execute(
                f"UPDATE `{auth}`.`{realm.table}` SET {sets}, port = {port} WHERE id = {key};"
            )
        else:
            name = _types(entry)[target_id].realm_name
            if any(mark in name for mark in ("'", '"', "\\")):
                raise RealmSwitchError(f"the realm name {name!r} cannot be written")
            values = f"'{address}'" + (f", '{local}'" if lan else "")
            sql.execute(
                f"INSERT INTO `{auth}`.`{realm.table}` (id, name, {columns}, port) "
                f"VALUES ({key}, '{name}', {values}, {port});"
            )
    except Exception as exc:  # noqa: BLE001 - worded once, with the way out
        raise RealmSwitchError(
            _carry_failed_sentence(target_id, source_id, port, f"the database refused: {exc}")
        ) from exc
    back = read(auth, ", port")
    want = [address, *([local] if lan else []), str(port)]
    if back != [want]:
        raise RealmSwitchError(
            _carry_failed_sentence(
                target_id, source_id, port, f"the row reads {back[:1] if back else 'nothing'}"
            )
        )


def _undo_late(
    entry: CatalogEntry,
    sql: SqlRunner,
    server_dir: Path,
    current: str,
    marker: str,
    crash: _Crash,
    write_override: Callable[[Mapping[str, str]], None],
) -> Iterator[str]:
    """Put the file's type back after a switch to `marker` failed after its rename.

    The way out of a half-done switch that its own retry cannot finish (a realm
    row that will not take its address, cold review): only for the switch's own
    state (`_check_resumable`, `_verify`), it is a normal switch back, ONE rename
    both ways, so the `marker` set is parked whole under its own marker and the
    file's type is plain again. The file never changed; the override is written
    for the file's type again and the crash note cleared last.
    """
    _check_resumable(current, marker, crash)
    _verify(sql, entry, current, marker)
    back = _parked(entry, marker)
    for schema, source in zip(back, _plain(entry), strict=True):
        _create_park_like(sql, source, schema)
    home = _parked(entry, current)
    yield from _rename_both_ways(
        entry, sql, marker, current, back, home, _tables_in(sql.query, home)
    )
    for schema in home:
        _drop_if_empty(sql, schema)
    _verify(sql, entry, marker, current)
    write_override(switch_env_for(entry, current))
    _clear_crash(server_dir)
    yield (
        f"The unfinished switch to {marker} was put back: the server is on the {current} "
        f"realm type again, and the {marker} databases are kept for the next switch."
    )


def _verify(sql: SqlRunner, entry: CatalogEntry, current: str, target_id: str) -> None:
    """The crash marker is cleared only after this passes: one set is plain, one parked.

    The target parks must be empty (their tables moved, the shells dropped),
    the plain marker must name the target (it traveled with the target's set,
    or the first switch just wrote it), and the parked set of the previous type
    must still read as that type. Anything else, an unreadable read included,
    leaves the crash marker for the next press and writes neither the file nor
    the override.
    """
    target_park = _parked(entry, target_id)
    for schema, names in zip(target_park, _tables_in(sql.query, target_park), strict=True):
        if names:
            raise RealmSwitchError(
                f"The parked {schema} schema is not empty after the rename; "
                "nothing further was written. Press again to retry."
            )
    if read_marker(sql.query, entry) != target_id:
        raise RealmSwitchError(
            f"The plain databases do not read as the {target_id} realm type after "
            "the rename; nothing further was written. Press again to retry."
        )
    if _marker_in(sql.query, _parked(entry, current)[1]) != current:
        raise RealmSwitchError(
            f"The parked {current} databases do not read as the {current} realm type after "
            "the rename; nothing further was written. Press again to retry."
        )
