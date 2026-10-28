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
is put back rather than moved twice. It travels in the server dir, never in a
database, because the databases are what the rename is moving.
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
    """Publish the active type id, last of all (T812 C3a).

    Through `composegen._replace_whole()`: whole beside the file, fsynced, and
    `os.replace`d over it, so a crash leaves the old file and a retry still
    reads the old type. The rename is what moves the data; this file only says
    which set the plain schemas hold. Written after the crash marker is
    cleared, so a crash before it leaves the marker for the next press.
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


def ensure_marker(sql: SqlRunner, entry: CatalogEntry, type_id: str) -> None:
    """Create the marker table and row if needed; INSERT IGNORE makes it idempotent."""
    _check_id(type_id)
    auth = entry.databases.characters
    _check_name(auth)
    _check_name(MARKER_TABLE)
    sql.execute(
        f"CREATE TABLE IF NOT EXISTS `{auth}`.`{MARKER_TABLE}` "
        "(id VARCHAR(16) PRIMARY KEY) CHARACTER SET utf8mb4;"
    )
    sql.execute(f"INSERT IGNORE INTO `{auth}`.`{MARKER_TABLE}` (id) VALUES ('{type_id}');")


def read_marker(query: Callable[[str], str], entry: CatalogEntry) -> str | None:
    """The type id the data says it holds, or None when the table is absent.

    Takes the plain query callable rather than a `SqlRunner`: the start
    refusals only ever read, and a read needs no ledger row. Anything the query
    raises with reads as absent, never as a refusal: a database that cannot be
    asked is not a verdict about what it holds.
    """
    auth = entry.databases.characters
    _check_name(auth)
    _check_name(MARKER_TABLE)
    try:
        rows = query(f"SELECT id FROM `{auth}`.`{MARKER_TABLE}`;")
    except Exception as exc:  # noqa: BLE001 - absent/unreachable reads as None
        logger.debug(f"{entry.id}: the realm-type marker could not be read: {exc}")
        return None
    for line in rows.splitlines():
        value = line.strip()
        if value and _ID.fullmatch(value):
            return value
    logger.debug(f"{entry.id}: the realm-type marker reads no realm type id; asked as absent")
    return None


def refusal(entry: CatalogEntry, server_dir: Path, marker: str | None) -> str | None:
    """Why the server must not start now, or None.

    The marker travels WITH the data and the file travels with the folder: a
    mismatch means this folder's databases hold another type's characters, and
    starting would run them under the wrong rules. An absent marker is a
    pre-marker install, which the caller marks with the file's type and starts.
    """
    if not has_realm_types(entry):
        return None
    if marker is None:
        return None
    want = active(entry, server_dir).id
    if marker != want:
        return (
            f"The databases hold the {marker} realm type but this server is set "
            f"to {want}. Switch realm type to {marker} on the Server tab to "
            "put it right."
        )
    return None


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


def _tables_in(sql: SqlRunner, schemas: Sequence[str]) -> list[list[str]]:
    """Each schema's tables, in name order; an unreadable schema reads as empty."""
    found: list[list[str]] = []
    for schema in schemas:
        _check_name(schema)
        try:
            rows = sql.query(
                "SELECT TABLE_NAME FROM information_schema.TABLES "
                f"WHERE TABLE_SCHEMA = '{schema}' ORDER BY TABLE_NAME;"
            )
        except Exception:
            found.append([])
            continue
        found.append([line.strip() for line in rows.splitlines() if line.strip()])
    return found


def switch(
    entry: CatalogEntry,
    server_dir: Path,
    target_id: str,
    *,
    sql: SqlRunner,
    servers_running: Callable[[], bool],
    run_import: Callable[[], Iterator[str]],
    set_realm_row: Callable[[str, str], None],
    write_override: Callable[[Mapping[str, str]], None],
    bot_prefix: str,
) -> Iterator[str]:
    """Move every table set to `target_id`, then point the folder at it.

    1 refuse while a server runs (its connections hold metadata locks); 2 read
    the marker and refuse a file/marker split unless the target IS the marker
    (the repair: rewrite the file + override, no rename); 3 refuse while the
    active type's park schemas are non-empty; 4 create the active parks with
    the source schemas' charset/collation; 5 ONE `RENAME TABLE` moving all
    three sets both ways; 6 drop the emptied target parks; 7 on a first switch
    run the import, name the realm row, mark the type and copy the player's
    accounts; verify, clear the crash marker, 8 write the file, 9 rewrite the
    override. A crash marker BEFORE the first rename names the target for a
    killed press to retry.
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
    if current == target_id:
        yield f"The server is already on the {target_id} realm type; nothing was switched."
        return
    marker = read_marker(sql.query, entry)
    if marker is not None and marker != current:
        if target_id != marker:
            raise RealmSwitchError(
                f"The databases hold the {marker} realm type but this server is set to "
                f"{current}. Switch realm type to {marker} on the Server tab to put "
                "it right."
            )
        yield (
            f"The databases already hold {marker} but the file said {current}; "
            "the file and the override were rewritten, and no database was touched."
        )
        write_active(server_dir, marker)
        write_override(switch_env_for(entry, marker))
        return
    if marker is None:
        yield from _recover_interrupted(entry, sql, server_dir, current, target_id)
        marker = read_marker(sql.query, entry)
        if marker is not None and marker != current:
            if target_id != marker:
                raise RealmSwitchError(
                    f"The databases hold the {marker} realm type but this server is set to "
                    f"{current}. Switch realm type to {marker} on the Server tab to put "
                    "it right."
                )
            yield (
                f"The databases already hold {marker} but the file said {current}; "
                "the file and the override were rewritten, and no database was touched."
            )
            write_active(server_dir, marker)
            write_override(switch_env_for(entry, marker))
            return
    yield from _refuse_non_empty_parks(entry, sql, current)
    parked = _parked(entry, current)
    for schema, source in zip(parked, _plain(entry), strict=True):
        _create_park_like(sql, source, schema)
    target_park = _parked(entry, target_id)
    parked_tables = _tables_in(sql, target_park)
    _write_crash(server_dir, target_id)
    yield from _rename_both_ways(entry, sql, current, target_id, parked, target_park, parked_tables)
    for schema in target_park:
        _drop_if_empty(sql, schema)
    if not any(parked_tables):
        yield (
            f"No {target_id} databases exist yet; importing them for the first time "
            "(a few minutes), then copying the player accounts over."
        )
        yield from run_import()
        set_realm_row(types[target_id].realm_name, str(entry.ports.world))
        ensure_marker(sql, entry, target_id)
        yield from _copy_player_accounts(entry, sql, current, bot_prefix)
    _verify(sql, entry, current, target_id)
    _clear_crash(server_dir)
    write_active(server_dir, target_id)
    write_override(switch_env_for(entry, target_id))
    yield f"The server now runs the {target_id} realm type ({types[target_id].realm_name})."


def _crash_target(server_dir: Path) -> str | None:
    try:
        raw = json.loads((server_dir / SWITCH_MARKER_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    value = raw.get("target")
    return value if isinstance(value, str) and value else None


def _write_crash(server_dir: Path, target_id: str) -> None:
    (server_dir / SWITCH_MARKER_FILE).write_text(
        json.dumps({"version": 1, "target": target_id}) + "\n", encoding="utf-8"
    )


def _clear_crash(server_dir: Path) -> None:
    (server_dir / SWITCH_MARKER_FILE).unlink(missing_ok=True)


def _recover_interrupted(
    entry: CatalogEntry, sql: SqlRunner, server_dir: Path, current: str, target_id: str
) -> Iterator[str]:
    """Put back a first switch killed during its import, so this press retries fresh.

    Only when the crash marker names this same target, the plain marker is
    still absent, and the parked set carries the current type's marker: the
    parked set is then provably the complete pre-switch set (the rename is
    atomic) and the plain set is claimed by nothing (no marker), so dropping
    the plain's partial import and renaming back is safe. Anything else falls
    through to the normal path, which refuses or proceeds on what it reads.
    """
    if _crash_target(server_dir) != target_id:
        yield from ()
        return
    if read_marker(sql.query, entry) is not None:
        yield from ()
        return
    parked = _parked(entry, current)
    try:
        rows = sql.query(f"SELECT id FROM `{parked[1]}`.`{MARKER_TABLE}`;")
    except Exception:
        yield from ()
        return
    if [line.strip() for line in rows.splitlines() if line.strip()] != [current]:
        yield from ()
        return
    yield (
        f"The last switch to {target_id} was interrupted during its first import; "
        "its partial databases were dropped and the previous type's tables put back, "
        "so this press starts the switch again."
    )
    plain = _plain(entry)
    for schema, names in zip(plain, _tables_in(sql, plain), strict=True):
        for table in names:
            _check_name(table)
        if names:
            sql.execute(f"DROP TABLE {', '.join(f'`{schema}`.`{t}`' for t in names)};")
    moves = [
        f"`{park}`.`{table}` TO `{schema}`.`{table}`"
        for schema, park, names in zip(plain, parked, _tables_in(sql, parked), strict=True)
        for table in names
    ]
    if moves:
        sql.execute("RENAME TABLE " + ", ".join(moves) + ";")
    for schema in parked:
        _drop_if_empty(sql, schema)


def _refuse_non_empty_parks(entry: CatalogEntry, sql: SqlRunner, current: str) -> Iterator[str]:
    tables = _tables_in(sql, _parked(entry, current))
    holding = sorted(
        f"{schema}.{table}"
        for schema, names in zip(_parked(entry, current), tables, strict=True)
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
    collation = ""
    parts = rows.split()
    if len(parts) >= 2:
        _check_name(parts[0])
        if not all(ch.isalnum() or ch in "_-" for ch in parts[1]):
            raise RealmSwitchError(
                f"The {source} schema's collation {parts[1]!r} is not one this switch "
                "writes; nothing was switched."
            )
        collation = f" CHARACTER SET {parts[0]} COLLATE {parts[1]}"
    sql.execute(f"CREATE DATABASE IF NOT EXISTS `{schema}`{collation};")


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
    for schema, park in zip(plain, parked, strict=True):
        for table in _live_tables(sql, schema):
            moves.append(f"`{schema}`.`{table}` TO `{park}`.`{table}`")
    for park, schema, names in zip(target_parked, plain, parked_tables, strict=True):
        for table in names:
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


def _live_tables(sql: SqlRunner, schema: str) -> list[str]:
    _check_name(schema)
    try:
        rows = sql.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{schema}' ORDER BY TABLE_NAME;"
        )
    except Exception as exc:
        raise RealmSwitchError(
            f"The {schema} schema could not be listed ({exc}); nothing was switched."
        ) from exc
    tables = [line.strip() for line in rows.splitlines() if line.strip()]
    for table in tables:
        _check_name(table)
    return tables


def _drop_if_empty(sql: SqlRunner, schema: str) -> None:
    _check_name(schema)
    try:
        rows = sql.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{schema}' ORDER BY TABLE_NAME;"
        )
    except Exception:
        return
    if not [line for line in rows.splitlines() if line.strip()]:
        sql.execute(f"DROP DATABASE IF EXISTS `{schema}`;")


def _copy_player_accounts(
    entry: CatalogEntry, sql: SqlRunner, source_id: str, bot_prefix: str
) -> Iterator[str]:
    from yulon import useraccounts

    _check_id(source_id)
    if not bot_prefix or not _SAFE_PREFIX.fullmatch(bot_prefix):
        raise RealmSwitchError(
            f"The bot account prefix {bot_prefix!r} cannot be used in a query; "
            "nothing was copied."
        )
    databases = entry.databases
    source_schema = f"{databases.auth}_{source_id}"
    _check_name(source_schema)
    target_schema = databases.auth
    _check_name(target_schema)
    try:
        rows = sql.query(
            f"SELECT username FROM `{source_schema}`.account "
            f"WHERE UPPER(username) NOT LIKE '{bot_prefix.upper()}%' "
            f"AND username <> '{useraccounts.AHBOT_ACCOUNT}';"
        )
    except Exception as exc:
        raise RealmSwitchError(
            f"The player accounts could not be listed ({exc}); nothing was copied."
        ) from exc
    names = [line.strip() for line in rows.splitlines() if line.strip()]
    skipped = sorted({name for name in names if not _SAFE_NAME.fullmatch(name)})
    copied = 0
    for name in sorted({name for name in names if _SAFE_NAME.fullmatch(name)}):
        sql.execute(
            f"INSERT IGNORE INTO `{target_schema}`.account SELECT * FROM `{source_schema}`.account "
            f"WHERE username = '{name}';"
        )
        try:
            sql.execute(
                f"INSERT IGNORE INTO `{target_schema}`.account_access SELECT * "
                f"FROM `{source_schema}`.account_access WHERE account_id = "
                f"(SELECT id FROM `{source_schema}`.account WHERE username = '{name}');"
            )
        except Exception:
            pass
        copied += 1
    yield f"Copied {copied} player accounts into the new realm type's databases."
    if skipped:
        yield (
            f"Left {len(skipped)} accounts uncopied, whose names cannot be copied "
            f"by name ({', '.join(skipped[:5])}{'…' if len(skipped) > 5 else ''})."
        )


def _verify(sql: SqlRunner, entry: CatalogEntry, current: str, target_id: str) -> None:
    """The crash marker is cleared only after this passes: one set is plain.

    The target parks must be empty (their tables moved, the shells dropped),
    and the plain marker must name the target (it traveled with the target's
    set, or the first switch just wrote it). Anything else leaves the crash
    marker for the next press and writes neither the file nor the override.
    """
    for schema, names in zip(_parked(entry, target_id), _tables_in(sql, _parked(entry, target_id))):
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
