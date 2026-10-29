"""Packing a server's accounts and characters, and bringing a package into another (T601).

Level 1 only: auth, characters and, where the game has them, the playerbots and Lua-engine
databases, into a server of the SAME game that already exists. `move.py` is the file and its
checks; this is the part that touches a server, through the same engine Back up and Restore
use (`maintenance.backup`, `plan_restore`, `restore`), so every guard those have is still in
force: the lease, the hold, the census, the verified dump, the game record, the marker, the
safety copy.

**What this adds to them**, each a guard with a test and a mutation:

* The package's game is the target's game, AND every dump in it carries a record that says
  so. A dump with no record is refused here, stricter than Restore, which asks the player;
  a record that is nearly right is a record that cannot be read, and refused likewise.
* The databases are at the same version (`move.version_difference`). A start of this app
  never runs the core's own import, so data one step behind would run unmigrated.
* A target that already has accounts or characters is replaced only on a token the plan
  made, and only if the counts are still what the plan saw.
* The package is read again when the run begins, and must be the file that was planned.

**Order of an import.** Everything that can be checked is checked before the first byte is
written: the package, the game of every dump, the version, the counts, the extracted dumps
against the engine's own plan. Then the copy (`before-move`), then the loads in the order
auth, characters, playerbots, ale, each with the engine's safety copy and marker, then the
fix-ups. The server is never started by an import.

**What a replace is.** The engine merges: every table a dump holds is replaced and every
table it does not hold stays. So the accounts and characters of the target go, a table that
only the target's modules made stays as it was, and so does `realmlist` (the auth dump of a
package leaves it out), which is why this server keeps its own address and port.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import zipfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import IO, Protocol

from yulon import docker, forgetting, move, realm_name
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.catalog.composegen import INSTALL_ID_LENGTH
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import (
    BackupReport,
    Game,
    MaintenanceError,
    RestorePlan,
    RestoreReport,
)
from yulon.docker import OutputSink
from yulon.log import get_logger
from yulon.move import (
    Counts,
    DumpFile,
    Evidence,
    Header,
    Manifest,
    MovePackageError,
    Role,
    UpdateNames,
    checked_names,
    counts_phrase,
    digest_of,
    updates_names_in_dump,
)
from yulon.ownership import Ownership
from yulon.server_build_presses import MAINTENANCE_TAB

logger = get_logger(__name__)

ROLES: tuple[Role, ...] = ("auth", "characters", "playerbots", "ale")
"""The roles a package holds, in the order they are loaded (accounts before their characters)."""

EVIDENCE_ROLES: tuple[Role, ...] = ("auth", "characters")
"""The roles whose database version decides whether a package fits."""

OPTIONAL_EVIDENCE_ROLES: tuple[Role, ...] = ("playerbots", "ale")
"""Roles that are compared too, but may have no update record at all (the Lua engine's has none)."""

BEFORE_MOVE_LABEL = "before-move"
"""The label of the copy of the target's databases taken before anything is loaded."""

ENGINE_COPY_LABEL = "before-move-load"
"""The label of the copy `restore()` takes of each database right before it loads into it.

Not the same label as the copy above: backup file names are `<second>_<label>_<schema>.sql`
and a backup refuses to overwrite a file, so two copies with one label made in one second
collide (found by running the real engine in the flow's tests).
"""

TAIL_MARKER_NAME = "move-tail-pending.json"
"""The unfinished-import marker for a tail one-shot, in the server's backups folder (T802).

Written just before the first load of an import that owes a tail, never earlier: a
refusal before that loads nothing and leaves none. It holds the before-move backup
paths and the ledgers the server will hold right after the dumps load (the
package's own evidence), before the one-shot runs. While this server's ledgers
still EQUAL those, a plan refuses: the databases are the file's, and the updates
it lacked may be missing. A restore (or a one-shot that got part-way) changes the
ledgers and frees the server; a verified success clears the marker. A marker that
is there but cannot be read refuses as well (fail closed): absent, valid and
unreadable are three different answers.
"""


@dataclass(frozen=True)
class _TailMarker:
    """What an unfinished tail import wrote down (T802)."""

    copies: tuple[str, ...]
    ledger: Mapping[str, Evidence]
    """Schema -> the ledger it holds right after the load, before the one-shot."""


class _TailMarkerUnreadable(Exception):
    """A marker is there but does not say what it should (T802)."""


def _tail_marker_path(world: MoveWorld) -> Path:
    """Where the unfinished tail-import marker lives (T802)."""
    return maintenance.backups_dir(world.server_dir) / TAIL_MARKER_NAME


def _read_tail_marker(world: MoveWorld) -> _TailMarker | None:
    """The unfinished tail import's record, None when there is none (T802).

    Raises `_TailMarkerUnreadable` for a marker that is there but cannot be read,
    is not JSON, or lacks its backups or ledgers: the caller refuses on it.
    """
    try:
        raw = _tail_marker_path(world).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise _TailMarkerUnreadable(str(exc)) from exc
    try:
        found = json.loads(raw)
    except ValueError as exc:
        raise _TailMarkerUnreadable(str(exc)) from exc
    if not isinstance(found, dict):
        raise _TailMarkerUnreadable("not an object")
    copies = found.get("copies")
    ledger = found.get("ledger")
    if (
        not isinstance(copies, list)
        or not copies
        or not all(isinstance(p, str) and p for p in copies)
    ):
        raise _TailMarkerUnreadable("no before-move copies")
    if not isinstance(ledger, dict) or not ledger:
        raise _TailMarkerUnreadable("no ledgers")
    try:
        held = {str(schema): Evidence.model_validate(ev) for schema, ev in ledger.items()}
    except ValueError as exc:
        raise _TailMarkerUnreadable(str(exc)) from exc
    return _TailMarker(copies=tuple(copies), ledger=held)


def _same_ledger(a: Evidence | None, b: Evidence | None) -> bool:
    if a is None or b is None:
        return False
    return (a.kind, a.count, a.digest, tuple(sorted(a.modules))) == (
        b.kind,
        b.count,
        b.digest,
        tuple(sorted(b.modules)),
    )


def _tail_marker_refusal(world: MoveWorld, here: Mapping[str, Evidence | None]) -> str | None:
    """The refusal an unfinished tail import owes this server now, else None (T802).

    Absent: None. Unreadable: a refusal. Valid: a refusal only while every ledger it
    holds still equals this server's (`here`), the state right after its load.
    """
    try:
        marker = _read_tail_marker(world)
    except _TailMarkerUnreadable as exc:
        logger.warning(f"could not read the unfinished tail-import marker: {exc}")
        return _unreadable_marker_sentence(_tail_marker_path(world))
    if marker is None:
        return None
    if all(_same_ledger(here.get(schema), ev) for schema, ev in marker.ledger.items()):
        return _unfinished_tail_sentence(marker.copies)
    return None


def _write_tail_marker(
    world: MoveWorld, copies: Sequence[object], ledger: Mapping[str, Evidence]
) -> None:
    """Write down the import's before-move backups and its post-load ledgers (T802).

    Atomic: a temporary file in the same folder, flushed and synced, then renamed
    over the marker. A marker an earlier press left keeps its backup paths: those
    are the server's state before the FIRST press, which a second press's copies
    (taken over a half-updated server) are not. Raises OSError, and
    `_TailMarkerUnreadable` for an earlier marker that cannot be read.
    """
    path = _tail_marker_path(world)
    earlier = _read_tail_marker(world)
    kept = list(earlier.copies) if earlier is not None else [str(p) for p in copies]
    body = json.dumps(
        {
            "copies": kept,
            "ledger": {schema: ev.model_dump(mode="json") for schema, ev in ledger.items()},
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with open(temporary, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _clear_tail_marker(world: MoveWorld) -> None:
    """Forget an unfinished tail import after a verified success (T802)."""
    try:
        _tail_marker_path(world).unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(f"could not clear the unfinished tail-import marker: {exc}")


def _unfinished_tail_sentence(copies: Sequence[object]) -> str:
    """The refusal while an earlier tail import's marker names backups (T802)."""
    named = ", ".join(str(p) for p in copies) or "the copies taken first (before-move)"
    return (
        "An earlier import did not finish: restore the databases from "
        f"{named} first, then import again."
    )


def _unreadable_marker_sentence(path: Path) -> str:
    """The refusal while an unfinished import's record cannot be read (T802)."""
    return (
        f"An earlier import left a record Yu'lon cannot read ({path}), so it will not bring a "
        "file in until the databases are restored from the copies taken first (before-move) "
        "and that record is removed."
    )


APP_ACCOUNT_PREFIX = "YULON_"
"""The command channel's account (`channel_setup.ACCOUNT_PREFIX`): the app's, never a player's."""

RUNNING_NEEDS_A_YES_EXPORT = (
    "The server is running. Packing needs it stopped for a moment so the copy is one "
    "consistent picture, and Yu'lon starts it again afterwards. Say yes to that, or stop the "
    "server first. Nothing was packed."
)
RUNNING_NEEDS_A_YES_IMPORT = (
    "The server is running. Bringing accounts and characters in needs it stopped, and it "
    "stays stopped afterwards. Say yes to stopping it, or stop the server first. Nothing was "
    "brought in."
)
REPLACE_NEEDS_A_YES = (
    "This server has accounts or characters that bringing these in would replace, and the "
    "yes to that was not given for this file. Nothing was brought in."
)
CHANGED_SINCE_THE_PLAN = (
    "The file or this server changed after the plan was shown, so the yes given then no "
    "longer covers what would happen now. Nothing was brought in; look at it again."
)
WHOLE_SERVER_FILE = (
    "This file holds a whole server, not only its accounts and characters. Bring it in from "
    "the Catalog (Bring from another computer… on the game's tile): it is installed as a new "
    "server there. Nothing was brought in."
)
INSTALL_RECORD_DAMAGED = (
    "This server's install record is damaged, so Yu'lon cannot tell that the folder is its "
    "own and will not move accounts in or out of it."
)
_UNFINISHED_RESTORE = (
    "A restore of this server's databases did not finish, so they may be half-written. "
    f"Restore again from the {MAINTENANCE_TAB} tab (that is how it is put right) before "
    "moving anything."
)


class MoveError(MaintenanceError):
    """A move that was refused or failed, in a sentence for the player."""


@dataclass(frozen=True)
class BotMarker:
    """The prefix random-bot accounts carry on this server (`dbreads.Marker`'s one fact)."""

    prefix: str


class FlowMysql(Protocol):
    """The database container: the dump/load seam, plus a read and a write."""

    def databases(self) -> tuple[str, ...]: ...
    def dump_into(self, database: str, sink: IO[bytes], ignore: tuple[str, ...] = ...) -> None: ...
    def load_from(self, source: IO[bytes]) -> None: ...
    def query(self, sql: str) -> str: ...
    def execute(self, sql: str) -> None: ...
    def tables(self, database: str) -> tuple[tuple[str, str], ...]: ...


BackupFn = Callable[..., BackupReport]
PlanRestoreFn = Callable[[Path], RestorePlan]
RestoreFn = Callable[..., RestoreReport]
"""`(plan, safety_label, *, before_load=None) -> RestoreReport`."""


@dataclass(frozen=True)
class MoveWorld:
    """One server, as the move sees it: its entry, its engine, and every seam it acts through."""

    entry: CatalogEntry
    game: Game
    server_dir: Path
    mysql: FlowMysql
    backup: BackupFn
    """`(only=, label=, ignore_tables=, directory=) -> BackupReport`, bound by `engine_for`."""
    plan_restore: PlanRestoreFn
    restore: RestoreFn
    """`(plan, safety_label) -> RestoreReport`."""
    running: Callable[[], list[str]]
    ownership: Callable[[], Ownership]
    stop_server: Callable[[], object]
    start_server: Callable[[], object]
    bring_up: Callable[[str], bool]
    """Start the database alone; True if it had to. `controller_view.DatabaseAlone.bring_up`."""
    take_down: Callable[[], None]
    channel_account: str | None
    """This install's command-channel account name, for the record in a package."""
    marker: Callable[[], BotMarker | None]
    now: Callable[[], datetime] = datetime.now
    spec: docker.ContainerSpec | None = None
    """This server's containers. Given, a move reserves the server across processes (T568) for
    its whole database stretch, as the other writes do; None (a harness) holds in this process."""
    wsl_distro: str | None = None
    apply_updates: Callable[[OutputSink], docker.AttachedRun] | None = None
    """Run the importer one-shot over this server's checkout (T802).

    Bound by the tab's wiring to `wotlk_modules.apply_module_sql()` on an
    AzerothCore-family server Yu'lon owns, and None for every other family. The
    one-shot applies the bundled updates the file lacks after the load, with the
    servers stopped and the database up; None binds the tail refusal at plan time.
    """


def engine_for(
    entry: CatalogEntry,
    server_dir: Path,
    mysql: maintenance.MysqlDocker,
    *,
    running: maintenance.RunningNames,
    wsl_distro: str | None,
) -> tuple[BackupFn, PlanRestoreFn, RestoreFn]:
    """The shared maintenance engine, bound to one entry the way the Maintenance tab binds it."""
    game = maintenance.game_of(entry)
    spec = entry.container_spec()
    core = entry.core_databases()

    def backup(
        *,
        only: Sequence[str] | None = None,
        label: str | None = None,
        ignore_tables: Mapping[str, Sequence[str]] | None = None,
        directory: Path | None = None,
    ) -> BackupReport:
        return maintenance.backup(
            server_dir,
            mysql,
            game=game,
            only=only,
            label=label,
            ignore_tables=ignore_tables,
            directory=directory,
            spec=spec,
            core_databases=core,
            running=running,
            wsl_distro=wsl_distro,
        )

    def plan_restore(path: Path) -> RestorePlan:
        return maintenance.plan_restore(
            path, server_dir, game=game, spec=spec, running=running, wsl_distro=wsl_distro
        )

    def restore(
        plan: RestorePlan, safety_label: str, *, before_load: Callable[[], object] | None = None
    ) -> RestoreReport:
        return maintenance.restore(
            plan,
            mysql,
            game=game,
            confirm=plan.token,
            spec=spec,
            core_databases=core,
            running=running,
            wsl_distro=wsl_distro,
            safety_label=safety_label,
            before_load=before_load,
        )

    return backup, plan_restore, restore


# --------------------------------------------------------------- small shared pieces


def _literal(value: str) -> str:
    """A SQL string literal's content: quotes doubled, backslashes doubled (both SQL modes)."""
    return value.replace("\\", "\\\\").replace("'", "''")


def _game_name(game_id: str) -> str:
    try:
        return load_catalog().get(game_id).name
    except Exception:  # noqa: BLE001 - an id this build has no entry for is shown as it is
        return game_id


def _server_is_up(world: MoveWorld) -> bool:
    names = set(world.running())
    spec = world.entry.container_spec()
    return spec.world in names or spec.auth in names


def move_held_reason(server_dir: Path | str) -> str | None:
    """`docker.move_held_reason()`'s sentence, or None when nothing holds the server.

    T737: the Maintenance tab's Restore holds the server with the sentence a Start or Stop is
    told (why starting or stopping would harm the restore). A Pack or Bring in is told the
    holder's own `for_a_move` sentence (`RESTORE_HOLDS_A_MOVE`) instead. T738: likewise a
    whole-server bring-in's load (`MOVE_IN_HOLDS_A_MOVE`). Every other holder's sentence --
    and a hold with no `for_a_move` -- is passed as it is. The orphaned-load path is unchanged.

    T738 part 4: one Docker-layer call, never the split `holder()` / `held_reason()`
    reads. A hold taken between the two read as unheld -- the first saw nothing, then
    `orphaned_restore_reason()` saw `_HELD` and answered None -- so a move wrote its
    record under an active hold.
    """
    return docker.move_held_reason(server_dir)


def _refuse_if_unsafe(world: MoveWorld) -> None:
    """Refuse a move out of or into a server whose databases cannot be trusted right now.

    A hold on the server is asked before the restore record (`docker.held_reason()`): a job of
    THIS Yu'lon (T736: the Maintenance tab's own Restore, whose record is in place while it
    loads), else a restore load another Yu'lon left running (T734, the T730 sentence). Either
    way the record is there too, but "did not finish ... Restore again" would have the player
    fight the load. They are told the holder's sentence instead, in a move's words
    (`move_held_reason()`, T737): wait for it.
    """
    if world.ownership() is Ownership.UNKNOWN:
        raise MoveError(INSTALL_RECORD_DAMAGED)
    held = move_held_reason(world.server_dir)
    if held is not None:
        raise MoveError(held)
    if maintenance.interrupted_restore(world.server_dir) is not None:
        raise MoveError(_UNFINISHED_RESTORE)


def refuse_over_a_running_load(world: MoveWorld) -> None:
    """Refuse (T730) when a restore load another Yu'lon left running still writes here.

    Asked just before a move stops this server: `docker.orphaned_restore_reason()`'s answer
    from the restore record, so the stop cannot cut the load off. Never raises anything else.
    """
    said = docker.orphaned_restore_reason(world.server_dir)
    if said is not None:
        raise MoveError(f"{said} Nothing was stopped.")


def _present_roles(world: MoveWorld, wanted: Sequence[Role] = ROLES) -> dict[Role, str]:
    """Role -> schema, for the roles this game has AND this server's database holds now."""
    schemas = world.entry.schema_map()
    present = set(world.mysql.databases())
    return {role: schemas[role] for role in wanted if role in schemas and schemas[role] in present}


# --------------------------------------------------------------- reading a server


def _read_version(world: MoveWorld, schema: str) -> Evidence | None:
    """What `schema` says about its own version, or None when it cannot be read.

    Three ledgers exist among the cores: an `updates` table (AzerothCore, TrinityCore), a
    `migrations` table (Tortoise) and a `*_db_version` table whose `required_<rev>` column
    is the version (CMaNGOS). The first that exists decides, and its kind travels with the
    answer so two servers are only compared on the same ledger.
    """
    try:
        found = world.mysql.query(
            "SELECT TABLE_NAME FROM information_schema.TABLES "
            f"WHERE TABLE_SCHEMA = '{_literal(schema)}' AND TABLE_NAME IN "
            "('updates', 'migrations', 'character_db_version', 'realmd_db_version');"
        ).split()
        tables = set(found)
        quoted = f"`{schema}`"
        module_sql = ""
        if "updates" in tables:
            kind = "updates"
            sql, module_sql = move.update_name_queries(schema)
        elif "migrations" in tables:
            kind = "migrations"
            sql = f"SELECT `Hash` FROM {quoted}.`migrations` WHERE `Module` = '' ORDER BY `Hash`;"
            module_sql = (
                f"SELECT `Module`, `Hash` FROM {quoted}.`migrations` "
                "WHERE `Module` <> '' ORDER BY `Module`, `Hash`;"
            )
        elif tables & {"character_db_version", "realmd_db_version"}:
            kind = "db_version"
            table = sorted(tables & {"character_db_version", "realmd_db_version"})[0]
            sql = (
                "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                f"WHERE TABLE_SCHEMA = '{_literal(schema)}' AND TABLE_NAME = '{table}' "
                "AND LEFT(COLUMN_NAME, 9) = 'required_' ORDER BY COLUMN_NAME;"
            )
        else:
            return None
        items = [line.strip() for line in world.mysql.query(sql).splitlines() if line.strip()]
        modules = tuple(
            sorted(
                line.strip().replace("\t", "|")
                for line in (world.mysql.query(module_sql).splitlines() if module_sql else ())
                if line.strip()
            )
        )
    except Exception as exc:  # noqa: BLE001 - a version that cannot be asked is not a version
        logger.info(f"could not read the version of {schema}: {exc}")
        return None
    if not items:
        return None
    return Evidence(kind=kind, count=len(items), digest=digest_of(items), modules=modules)


def _read_versions(world: MoveWorld, roles: Mapping[Role, str]) -> dict[str, Evidence | None]:
    wanted = (*EVIDENCE_ROLES, *OPTIONAL_EVIDENCE_ROLES)
    return {roles[r]: _read_version(world, roles[r]) for r in wanted if r in roles}


def _read_update_names(world: MoveWorld, schema: str, evidence: Evidence) -> UpdateNames | None:
    """This server's `updates` names for `schema`, or None when unsure (T802).

    Asked again only once a refusal needs to say which side is behind. The answer must add up
    to the `evidence` already read (count, digest, module rows), else it is not used.
    """
    if evidence.kind != "updates":
        return None
    core_sql, module_sql = move.update_name_queries(schema)
    try:
        core = world.mysql.query(core_sql).splitlines()
        modules = world.mysql.query(module_sql).splitlines()
    except Exception as exc:  # noqa: BLE001 - names that cannot be asked keep the plain sentence
        logger.info(f"could not read the update names of {schema}: {exc}")
        return None
    return checked_names(
        [line.strip() for line in core if line.strip()],
        [line.strip().replace("\t", "|") for line in modules if line.strip()],
        evidence,
    )


def _version_problem(
    world: MoveWorld,
    manifest: Manifest,
    roles: Mapping[Role, str],
    here: Mapping[str, Evidence | None],
    file: move.Package | None = None,
) -> str | None:
    """The sentence for a package that is not at this server's version or modules, else None.

    The playerbots and Lua-engine schemas are compared only where this server has them: a server
    without one gets it created by the dump, and has no record there to disagree with.
    """
    package = dict(manifest.schema_evidence)
    schemas = world.entry.schema_map()
    optional: set[str] = set()
    for role in OPTIONAL_EVIDENCE_ROLES:
        schema = schemas.get(role)
        if schema is None:
            continue
        if roles.get(role) == schema:
            optional.add(schema)
        else:
            package.pop(schema, None)
    if file is None:
        return move.version_difference(package, here, frozenset(optional))

    def server_names(schema: str) -> UpdateNames | None:
        ours = here.get(schema)
        return None if ours is None else _read_update_names(world, schema, ours)

    return move.version_difference(
        package,
        here,
        frozenset(optional),
        file_names=lambda schema: updates_names_in_dump(file, schema),
        server_names=server_names,
        built_here=world.ownership() is Ownership.OWNED,
    )


TailRowsOf = Callable[[str], "Sequence[move.TailRow] | None"]
"""Schema -> its `(name, sha1, state)` rows, or None when they are not known."""


def _tail_rows_of_dump(package: move.Package, schema: str) -> Sequence[move.TailRow] | None:
    """A package's `updates` rows for `schema`, or None when unsure (T802).

    `updates_names_in_dump()` decides whether the names are known at all; the
    full rows (with the hashes the tail proof compares) are re-parsed here from
    the same statement, so one parse decides and this one only carries.
    """
    from yulon.move import _rows_of_insert  # noqa: PLC2701 - the dump grammar's own reader

    if move.updates_names_in_dump(package, schema) is None:
        return None
    try:
        member = package.member(schema)
        found: list[move.TailRow] = []
        with zipfile.ZipFile(package.path) as archive, archive.open(member.file) as fh:
            for raw in fh:
                if not raw.startswith(b"INSERT INTO `updates`"):
                    continue
                rows = _rows_of_insert(raw.decode("utf-8"))
                if rows is None:
                    return None
                found.extend(rows)
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, MovePackageError):
        return None
    return found or None


def _tail_rows_of_server(world: MoveWorld, schema: str) -> Sequence[move.TailRow] | None:
    """This server's `updates` rows for `schema`, or None when unsure (T802).

    The statement lives with the ledger knowledge that reads it
    (`move.tail_row_queries()`); the tab-separated answer is turned into rows
    the same way `_read_update_names()` turns its two answers into checked names.
    """
    if world.entry.install.native is None or world.entry.install.native.family != "azerothcore":
        return None
    try:
        out = world.mysql.query(move.tail_row_queries(schema))
    except Exception as exc:  # noqa: BLE001 - rows that cannot be asked are not a tail
        logger.info(f"could not read the update rows of {schema}: {exc}")
        return None
    rows: list[move.TailRow] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, tab, rest = line.partition("\t")
        sha1, tab2, state = rest.partition("\t")
        if not tab or not tab2 or not name.strip() or not sha1.strip() or not state.strip():
            return None
        rows.append((name.strip(), sha1.strip(), state.strip()))
    return rows or None


def tail_updates(
    world: MoveWorld,
    manifest: Manifest,
    here: Mapping[str, Evidence | None],
    package: move.Package | None,
) -> dict[str, move.Tail] | None:
    """The per-schema tails the file lacks that the updater may apply, or None (T802).

    Every schema the version check compares on an AzerothCore-family server
    Yu'lon owns must classify as an appliable tail; anything else (a server
    that is behind, a different set, an unknown row, a missing file or a hash
    mismatch on disk, or a module or `updates_include` row that differs) answers
    None and keeps the old refusal. The caller also needs `apply_updates`: None
    for every other family binds the refusal too.
    """
    if package is None:
        return None
    if world.entry.install.native is None or world.entry.install.native.family != "azerothcore":
        return None
    if world.entry.containers.db_import is None:
        return None
    if world.ownership() is not Ownership.OWNED:
        return None
    # Asked through the world's own spec only when the caller already bound the
    # seam: the harness MoveWorld carries no `apply_updates`, and a compose config
    # probe here would shell out on every refused plan (the T802 tests forbid
    # that). `None` below binds the same refusal as a failed probe.
    if world.apply_updates is None:
        return None
    try:
        sees = docker.importer_sees_modules(world.entry.containers.db_import, world.server_dir)
    except Exception:  # noqa: BLE001 - unreadable compose config is not a tail
        sees = None
    if sees is False:
        return None
    for schema, ours in here.items():
        # A ledger this server holds that the file has none of is a difference the
        # version check refuses (an optional schema the dump would not replace); no
        # tail answers it.
        if ours is not None and schema not in manifest.schema_evidence:
            return None
    found: dict[str, move.Tail] = {}
    compared = 0
    for schema, theirs in manifest.schema_evidence.items():
        if theirs.kind != "updates":
            return None
        ours = here.get(schema)
        if ours is None or ours.kind != "updates":
            return None
        compared += 1
        if ours.digest == theirs.digest:
            if tuple(sorted(ours.modules)) != tuple(sorted(theirs.modules)):
                return None
            continue
        if tuple(sorted(ours.modules)) != tuple(sorted(theirs.modules)):
            return None
        file_rows = _tail_rows_of_dump(package, schema)
        server_rows = _tail_rows_of_server(world, schema)
        if file_rows is None or server_rows is None:
            return None
        tail = move.appliable_tail(file_rows, server_rows)
        if not tail:
            return None
        by_name = {name: sha1 for name, sha1, _ in server_rows}
        for name in tail:
            if not move.tail_file_matches(
                move.tail_update_path(world.server_dir, schema, name), by_name[name]
            ):
                return None
        include_here = _include_rows_of_server(world, schema)
        include_file = _include_rows_of_dump(package, schema)
        if include_here is None or include_file is None or include_here != include_file:
            return None
        found[schema] = tail
    if not compared or not found:
        return None
    return found


def _include_rows_of_server(world: MoveWorld, schema: str) -> set[str] | None:
    """This server's `updates_include` rows for `schema`, or None when unread (T802)."""
    try:
        out = world.mysql.query(
            f"SELECT `path`, `state` FROM `{schema}`.`updates_include` ORDER BY `path`;"
        )
    except Exception as exc:  # noqa: BLE001 - rows that cannot be asked are not a tail
        logger.info(f"could not read the updates_include rows of {schema}: {exc}")
        return None
    rows: set[str] = set()
    for line in out.splitlines():
        if not line.strip():
            continue
        path, tab, state = line.partition("\t")
        if not tab or not path.strip() or not state.strip():
            return None
        rows.add(f"{path.strip()}|{state.strip()}")
    return rows


def _include_rows_of_dump(package: move.Package, schema: str) -> set[str] | None:
    """A package dump's `updates_include` rows for `schema`, or None when unsure (T802)."""
    try:
        member = package.member(schema)
        found: set[str] = set()
        seen = False
        with zipfile.ZipFile(package.path) as archive, archive.open(member.file) as fh:
            for raw in fh:
                if not raw.startswith(b"INSERT INTO `updates_include`"):
                    continue
                seen = True
                rows = _include_tuples(raw.decode("utf-8"))
                if rows is None:
                    return None
                found.update(rows)
        return found if seen else set()
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, MovePackageError):
        return None


_INCLUDE_VALUE = re.compile(r"^INSERT INTO `updates_include` VALUES (.*);\s*$", re.DOTALL)
"""The value list of one `updates_include` insert, or no match when unsure."""


def _include_tuples(statement: str) -> set[str] | None:
    """The `path|state` rows of one `updates_include` insert, or None when unsure."""
    found = _INCLUDE_VALUE.match(statement)
    if found is None:
        return None
    rows: set[str] = set()
    for part in found.group(1).split("),("):
        fields = part.removeprefix("(").removesuffix(")").split("','")
        if len(fields) != 2:
            return None
        path, state = fields[0].removeprefix("'"), fields[1].removesuffix("'")
        if not path or not state or "'" in path or "'" in state:
            return None
        rows.add(f"{path}|{state}")
    return rows


_ONE_SHOT_POOLS: tuple[str, ...] = ("auth", "characters", "world")
"""The databases the importer one-shot updates: one `DBUpdater` pass each (T802).

The playerbots database is not one: mod-playerbots runs its own updater at a Start."""


PendingFile = tuple[str, str, str]
"""`(schema, file name, SHA1 of its bytes)`: one file the one-shot would run (T802)."""


def _one_shot_schemas(world: MoveWorld) -> dict[str, str]:
    """Pool -> schema for the databases the one-shot updates (T802)."""
    dbs = world.entry.databases
    return {"auth": dbs.auth, "characters": dbs.characters, "world": dbs.world}


def _ledger_rows_of_server(world: MoveWorld, schema: str) -> Sequence[move.TailRow] | None:
    """Every `updates` row of `schema` on this server, or None when unsure (T802)."""
    try:
        out = world.mysql.query(move.ledger_row_queries(schema))
    except Exception as exc:  # noqa: BLE001 - a ledger that cannot be asked is not known
        logger.info(f"could not read the update ledger of {schema}: {exc}")
        return None
    rows: list[move.TailRow] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        name, tab, rest = line.partition("\t")
        sha1, tab2, state = rest.partition("\t")
        if not tab or not tab2 or not name.strip() or not sha1.strip() or not state.strip():
            return None
        rows.append((name.strip(), sha1.strip(), state.strip()))
    return rows


def _sql_files_under(folder: Path) -> list[Path] | None:
    """Every `.sql` file under `folder`, recursively, as the updater lists them (T802).

    A folder that is not there holds none (the updater skips it with a warning).
    None when a listing fails or a folder is a link: what the updater would see
    there is not known, so nothing is decided on it.
    """
    if not folder.is_dir():
        return []
    found: list[Path] = []
    failed: list[OSError] = []
    for root, dirs, files in os.walk(folder, onerror=failed.append):
        here = Path(root)
        if any((here / d).is_symlink() for d in dirs):
            return None
        found.extend(here / f for f in files if f.endswith(".sql"))
    if failed:
        return None
    return sorted(found)


def _sha1_of_file(path: Path) -> str | None:
    digest = hashlib.sha1()
    try:
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest().upper()


def _module_sql_folders(server_dir: Path, pool: str) -> list[Path] | None:
    """Every module folder the updater walks for `pool`, or None when unreadable (T802).

    Every module on disk, not only the ones the one-shot is handed: a superset, so
    a module that is withheld can only make this refuse, never let a file through.
    """
    from yulon.apply import IMPORTER_SQL_ROOT, updater_folder_dbs

    modules = server_dir / docker.MODULES_DIR_NAME
    if not modules.exists():
        return []
    try:
        names = sorted(p.name for p in modules.iterdir() if p.is_dir())
        found: list[Path] = []
        for name in names:
            root = modules / name / IMPORTER_SQL_ROOT
            if not root.is_dir():
                continue
            found.extend(
                sub
                for sub in sorted(root.iterdir())
                if sub.is_dir() and pool in updater_folder_dbs(sub.name)
            )
    except OSError as exc:
        logger.info(f"could not list the modules' SQL folders: {exc}")
        return None
    return found


def _one_shot_pending(
    world: MoveWorld, package: move.Package, loaded: Sequence[str]
) -> set[PendingFile] | None:
    """Every file the one-shot would run right after the load, or None when unsure (T802).

    What the updater does, per pool (auth, characters, world): it lists every
    `.sql` under the `updates_include` folders (`$` is the server folder) and
    under each module's `data/sql/<folder naming the pool>`, and runs a file whose
    name the ledger lacks, or whose bytes changed, unless both the row and the
    folder are ARCHIVED. The ledger is the one the load leaves: the dump's for a
    schema the package loads, this server's for the rest (world). Two files with
    one name make the updater stop, so they answer None too.
    """
    pending: set[PendingFile] = set()
    for pool, schema in _one_shot_schemas(world).items():
        if schema in loaded:
            rows = _tail_rows_of_dump(package, schema)
            include = _include_rows_of_dump(package, schema)
        else:
            rows = _ledger_rows_of_server(world, schema)
            include = _include_rows_of_server(world, schema)
        modules = _module_sql_folders(world.server_dir, pool)
        if rows is None or include is None or modules is None:
            return None
        folders: list[tuple[Path, str]] = []
        for row in sorted(include):
            where, _bar, state = row.partition("|")
            relative = where.removeprefix("$/")
            if relative == where or ".." in PurePosixPath(relative).parts:
                return None
            folders.append((world.server_dir / relative, state))
        folders.extend((folder, "MODULE") for folder in modules)
        files: dict[str, tuple[Path, str]] = {}
        for folder, state in folders:
            listed = _sql_files_under(folder)
            if listed is None:
                return None
            for path in listed:
                if path.name in files:
                    return None
                files[path.name] = (path, state)
        applied = {name: (sha1, state) for name, sha1, state in rows}
        for name, (path, state) in sorted(files.items()):
            held = applied.get(name)
            if held is not None and held[1] == "ARCHIVED" and state == "ARCHIVED":
                continue
            sha1 = _sha1_of_file(path)
            if sha1 is None:
                return None
            if held is None or held[0].upper() != sha1:
                pending.add((schema, name, sha1))
    return pending


def _one_shot_scope_problem(
    world: MoveWorld, package: move.Package, tails: Mapping[str, move.Tail]
) -> str | None:
    """The refusal when the one-shot would run more than the approved tail, else None (T802).

    The tail proof approves the auth and characters names the file lacks; the
    one-shot runs EVERY pending file, so the two sets must be equal, by name, by
    hash and by database. Asked before anything is backed up or loaded.
    """
    run_tail = _run_tail(world, tails)
    if not run_tail:
        return None
    approved: set[PendingFile] = set()
    for schema, names in run_tail.items():
        rows = _tail_rows_of_server(world, schema)
        if rows is None:
            return _SCOPE_UNKNOWN
        by_name = {name: sha1.upper() for name, sha1, _ in rows}
        approved.update((schema, name, by_name.get(name, "")) for name in names)
    loaded = [m.schema_name for m in package.manifest.databases]
    pending = _one_shot_pending(world, package, loaded)
    if pending is None:
        return _SCOPE_UNKNOWN
    if pending != approved:
        extra = sorted(f"{schema}: {name}" for schema, name, _ in pending - approved)
        shown = ", ".join(extra[:4]) + (f" and {len(extra) - 4} more" if len(extra) > 4 else "")
        return _SCOPE_WIDER.format(names=shown or "the missing ones differ on disk")
    return None


_SCOPE_UNKNOWN = (
    "Yu'lon could not tell which database updates this server would run after the load, so "
    "it will not bring the file in."
)
_SCOPE_WIDER = (
    "This server has other database updates waiting besides the ones the file lacks "
    "({names}), and applying the missing ones would run those too, so Yu'lon will not bring "
    "the file in."
)


def _run_tail(world: MoveWorld, tails: Mapping[str, move.Tail]) -> dict[str, move.Tail]:
    """The part of a tail the one-shot applies: auth and characters (T802)."""
    dbs = world.entry.databases
    return {s: n for s, n in tails.items() if s in (dbs.auth, dbs.characters)}


def _bot_and_app_clauses(world: MoveWorld) -> tuple[str, str]:
    """`(bot clause, app clause)` over the auth `account` table's `username`.

    Exact prefix matches (`LEFT(...) = ...`), not `LIKE`, where `_` would be a wildcard.
    Without a readable marker the bot clause is false, so every account counts as a player:
    a server that cannot tell its bots from people asks before replacing, never guesses.
    """
    marker = world.marker()
    if marker is None or not marker.prefix:
        bot = "0"
    else:
        prefix = _literal(marker.prefix.upper())
        bot = f"LEFT(UPPER(`username`), {len(marker.prefix)}) = '{prefix}'"
    app = f"LEFT(UPPER(`username`), {len(APP_ACCOUNT_PREFIX)}) = '{APP_ACCOUNT_PREFIX}'"
    return bot, app


def _read_counts(world: MoveWorld, roles: Mapping[Role, str]) -> Counts:
    """Player accounts, player characters, bot accounts: the three numbers a move quotes."""
    ops = world.entry.observability
    if ops is None or "auth" not in roles or "characters" not in roles:
        raise MoveError(f"{world.entry.name} has no measured account tables to count.")
    auth, chars = roles["auth"], roles["characters"]
    bot, app = _bot_and_app_clauses(world)
    table, column = ops.characters.table, ops.characters.account
    out = world.mysql.query(
        "SELECT "
        f"(SELECT COUNT(*) FROM `{auth}`.`account` WHERE NOT ({bot}) AND NOT ({app})), "
        f"(SELECT COUNT(*) FROM `{chars}`.`{table}` WHERE `{column}` NOT IN "
        f"(SELECT `id` FROM `{auth}`.`account` WHERE ({bot}) OR ({app}))), "
        f"(SELECT COUNT(*) FROM `{auth}`.`account` WHERE ({bot})), "
        f"(SELECT COUNT(*) FROM `{chars}`.`{table}` WHERE `{column}` IN "
        f"(SELECT `id` FROM `{auth}`.`account` WHERE ({bot})));"
    )
    fields = out.strip().split("\t")
    if len(fields) != 4 or not all(f.strip().isdigit() for f in fields):
        raise MoveError(f"The count of accounts came back as {out.strip()!r}, not four numbers.")
    return Counts(
        accounts=int(fields[0]),
        characters=int(fields[1]),
        bot_accounts=int(fields[2]),
        bot_characters=int(fields[3]),
    )


def _read_realm_name(world: MoveWorld, auth: str) -> str | None:
    rl = world.entry.realmlist
    try:
        out = world.mysql.query(
            f"SELECT `name` FROM `{auth}`.`{rl.table}` WHERE `id` = {rl.realm_id};"
        )
    except Exception as exc:  # noqa: BLE001 - the name is a courtesy, never a reason to refuse
        logger.info(f"could not read the realm name: {exc}")
        return None
    # `--batch` writes a backslash as two; a realm name never holds a control character
    # (the manifest grammar), so that is the only escape to undo.
    name = out.strip("\r\n").replace("\\\\", "\\")
    return name or None


# --------------------------------------------------------------- the database session


PRESS_PACK = "Pack for a move"
PRESS_BRING_IN = "Bring in a move"
"""The press names another Yu'lon's refusal quotes while a move holds the server (T568)."""


@contextlib.contextmanager
def _database_session(
    world: MoveWorld,
    *,
    because: str,
    undone: str,
    press: str,
    alone: str | None = None,
    leased: str = forgetting.MOVE_HOLDS_THE_DATABASES,
    held: str = forgetting.MOVE_HOLDS_THE_SERVER,
    for_a_move: str | None = None,
) -> Iterator[None]:
    """The leased, held, database-up stretch a move works in. `undone` names what did not happen.

    `leased` and `held` are what another press is told while it runs: the Maintenance tab's
    Pack and Bring in by default, the whole-server bring-in's own sentences from its load (T737).
    `for_a_move` (T738 part 4) is what a Pack, a Bring in, or a second "Bring from another
    computer…" on the same folder is told while it holds: the holder's own move sentence.

    The same four steps the Maintenance tab's Backup and Restore take, in the same order: the
    lease (one Backup, Restore or move of a server at a time), the hold (no Start, Stop or
    recreate under it), the database started on its own if it is down, and put back after.
    The hold is also the cross-process reservation under `press` when the world names its
    `spec`: a move rewrites the server's databases, so it refuses while another Yu'lon works on
    the server, and another Yu'lon's presses refuse while it runs (lead's decision, 2026-10-09).
    """
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(docker.maintenance_lease(world.server_dir, leased, press=press))
        except docker.MaintenanceLeaseTaken as exc:
            raise MoveError(f"{exc} {undone}") from exc
        try:
            stack.enter_context(
                docker.hold_the_server(
                    world.server_dir,
                    held,
                    press=press,
                    spec=world.spec,
                    wsl_distro=world.wsl_distro,
                    # A load passes `alone` (T681 review): no Modules SQL mid-load.
                    alone=alone,
                    for_a_move=for_a_move,
                )
            )
        except docker.ServerHeldError as exc:
            raise MoveError(f"{undone} {exc}") from exc
        try:
            started = world.bring_up(because)
        except Exception as exc:
            raise MoveError(
                f"{undone} The database could not be started on its own for it. {exc}"
            ) from exc
        try:
            yield
        finally:
            if started:
                world.take_down()


# --------------------------------------------------------------- export


@dataclass(frozen=True)
class ExportPlan:
    """What the pack dialog needs before it asks: whether the server must be stopped."""

    server_running: bool
    refusals: tuple[str, ...]

    @property
    def allowed(self) -> bool:
        return not self.refusals


def plan_export(world: MoveWorld) -> ExportPlan:
    refusals: list[str] = []
    try:
        _refuse_if_unsafe(world)
    except MoveError as exc:
        refusals.append(str(exc))
    try:
        running = _server_is_up(world)
    except Exception as exc:  # noqa: BLE001 - Docker not answering is a refusal, not "stopped"
        refusals.append(f"Yu'lon could not ask Docker what is running: {exc}")
        running = False
    return ExportPlan(server_running=running, refusals=tuple(refusals))


@dataclass(frozen=True)
class ExportResult:
    path: Path
    manifest: Manifest
    restarted: bool | None
    """True: stopped, packed, started again. False: the start failed. None: never stopped."""
    notes: tuple[str, ...] = ()

    def text(self) -> str:
        counts = self.manifest.counts
        server = self.manifest.server
        if server is not None:
            confs = sum(1 for f in server.files if f.kind == "conf")
            lines = [
                f"Packed the whole server into {self.path}: "
                f"{', '.join(m.schema_name for m in self.manifest.databases)}, {confs} setting "
                f"files and {len(server.modules)} modules, with {counts.accounts} accounts and "
                f"{counts.characters} characters ({counts.bot_accounts} bot accounts).",
                f"{self.path.stat().st_size / (1024 * 1024):.1f} MB.",
                self.manifest.secrets,
                "On the new computer, press Bring from another computer… on this game's Catalog "
                "tile: it builds the server again at the same version, then puts all of this in.",
                *self.notes,
            ]
            return "\n".join(lines)
        lines = [
            f"Packed {counts_phrase(counts)} into {self.path}.",
            f"{self.path.stat().st_size / (1024 * 1024):.1f} MB.",
            self.manifest.secrets,
            "The world database (custom items, NPCs, objects you placed) is not in it: it "
            "stays on this computer.",
            *self.notes,
        ]
        return "\n".join(lines)


SERVER_ROLES: tuple[Role, ...] = ("auth", "characters", "world", "playerbots", "ale")
"""The roles a whole-server package holds (level 2), in the order they are loaded."""


def export_package(
    world: MoveWorld,
    folder: Path,
    *,
    stop_allowed: bool,
    whole: Callable[[], move.ServerFacts] | None = None,
) -> ExportResult:
    """Pack this server's accounts and characters into `folder`, and say what was done.

    With `whole` (level 2), the whole server: every database but the logs, the realm row kept,
    and what `whole()` reads off the server folder (`move_server.gather_server_facts`). That is
    read FIRST, before the server is stopped, so a refusal there stops nothing.
    """
    _refuse_if_unsafe(world)
    facts = whole() if whole is not None else None
    if _server_is_up(world) and not stop_allowed:
        raise MoveError(RUNNING_NEEDS_A_YES_EXPORT)
    stopped = False
    try:
        if _server_is_up(world):
            refuse_over_a_running_load(world)
            stopped = True  # set first: a stop that fails half-way still leaves it to start again
            world.stop_server()
        result = _export_locked(world, folder, facts)
    except BaseException as exc:
        if stopped:
            problem = _start_again(world)
            if problem and isinstance(exc, MaintenanceError):
                raise MoveError(f"{exc} {problem}", detail=exc.detail) from exc
        raise
    if not stopped:
        return result
    problem = _start_again(world)
    notes = (*result.notes, problem) if problem else result.notes
    return ExportResult(
        path=result.path, manifest=result.manifest, restarted=problem is None, notes=notes
    )


def _start_again(world: MoveWorld) -> str | None:
    """Start the server this pack stopped. A sentence if that did not work, else None."""
    try:
        world.start_server()
    except Exception as exc:  # noqa: BLE001 - the pack is done; this is a note, not a failure
        logger.warning(f"the server could not be started again after a pack: {exc}")
        return (
            "The server could not be started again. Press Start on the Server tab; if that "
            "fails too, the Logs tab says why."
        )
    return None


def _export_locked(
    world: MoveWorld, folder: Path, facts: move.ServerFacts | None = None
) -> ExportResult:
    undone = "Nothing was packed."
    with _database_session(world, because="nothing was packed", undone=undone, press=PRESS_PACK):
        roles = _present_roles(world, SERVER_ROLES if facts is not None else ROLES)
        schemas = world.entry.schema_map()
        for needed in EVIDENCE_ROLES:
            if needed not in roles:
                raise MoveError(
                    f"This server has no {schemas[needed]}, so there are no accounts and "
                    f"characters to pack. {undone}"
                )
        versions = _read_versions(world, roles)
        required = {roles[r] for r in EVIDENCE_ROLES}
        for schema, version in versions.items():
            if version is None and schema in required:
                raise MoveError(
                    f"Yu'lon could not read the database version of {schema}, so a package "
                    f"made from it could not be checked on the other computer. {undone}"
                )
        evidence = {schema: v for schema, v in versions.items() if v is not None}
        counts = _read_counts(world, roles)
        realm = _read_realm_name(world, roles["auth"])
        if facts is not None and "world" not in roles:
            raise MoveError(
                f"This server has no {schemas['world']}, so there is no whole server to pack. "
                f"{undone}"
            )
        wanted = list(roles.values())
        report = _backup_for_the_package(world, wanted, roles["auth"] if facts is None else None)
        try:
            marker = world.marker()
            why = "not part of accounts and characters" if facts is None else "logs"
            left_out = tuple(
                f"{name} ({why})"
                for name in world.mysql.databases()
                if name not in wanted and name not in maintenance.SYSTEM_SCHEMAS
            )
            realm_note = (
                (f"{world.entry.realmlist.table} of {roles['auth']} (the realm's own address)",)
                if facts is None
                else ()
            )
            made = world.now()
            header = Header(
                game_id=world.game.id,
                game_name=world.game.name,
                realm_name=realm,
                channel_account=world.channel_account,
                bot_prefix=marker.prefix if marker is not None else None,
                counts=counts,
                schema_evidence=evidence,
                excluded=(*left_out, *realm_note),
                made=made,
            )
            by_schema = {schema: role for role, schema in roles.items()}
            dumps = [
                DumpFile(d.database, by_schema[d.database], d.path, _tables_of(d.database, d.path))
                for d in report.dumps
            ]
            kind: move.Kind = "server" if facts is not None else "characters"
            dest = folder / move.package_filename(world.game.id, made, kind=kind)
            manifest = move.write_package(
                dest,
                header,
                dumps,
                server=facts.spec if facts is not None else None,
                files=facts.files if facts is not None else (),
            )
        finally:
            # The working folder goes with them (T747), whether the pack worked or not.
            shutil.rmtree(pack_folder(world.server_dir), ignore_errors=True)
    return ExportResult(path=dest, manifest=manifest, restarted=None)


def _tables_of(schema: str, path: Path) -> tuple[str, ...]:
    """The tables a finished dump holds, read from the file itself."""
    tables = maintenance.tables_in_copy(path).get(schema)
    if not tables:
        raise MoveError(f"The copy of {schema} lists no table, so it was not packed.")
    return tables


PACK_FOLDER = ".move-out"
"""The pack's working folder in `backups/` (T747): the Backups list shows top-level files only."""


def pack_folder(server_dir: Path) -> Path:
    """Where a pack writes the dumps it zips: never where the Backups list reads."""
    return maintenance.backups_dir(server_dir) / PACK_FOLDER


def clear_pack_folder(server_dir: Path) -> None:
    """Take away what a pack killed before its clean-up left in its working folder (T765).

    That folder holds the pack's dumps, an auth one with every account's login verifier, and
    the Backups list never shows it. A link or file at its name is unlinked, never followed.
    The caller holds the server: a pack holds it from start to clean-up, so under the hold
    whatever is there is a dead pack's. Every pack calls this first; an uninstall that keeps
    the backups should call it before it moves them (`purge.py` is waiting on its redesign).

    Raises:
        OSError: it could not be removed.
    """
    work = pack_folder(server_dir)
    if work.is_symlink() or work.is_file():
        work.unlink()
    elif work.exists():
        shutil.rmtree(work)


def _backup_for_the_package(
    world: MoveWorld, wanted: Sequence[str], auth: str | None
) -> BackupReport:
    """The engine's backup of the packed schemas, into the pack's own working folder.

    An auth dump among them holds every account's verifier and, for accounts and characters,
    leaves out the realm row. Written into `backups/` itself, a pack killed before its clean-up
    left it there as an ordinary backup the list kept and would not delete (T747). The working
    folder is the owner's alone, emptied before this pack and removed after it; what a killed
    pack left there goes at the next one.
    """
    work = pack_folder(world.server_dir)
    try:
        clear_pack_folder(world.server_dir)
        maintenance.backups_dir(world.server_dir).mkdir(parents=True, exist_ok=True, mode=0o700)
        work.mkdir(mode=0o700)
    except OSError as exc:
        raise MoveError(
            f"Yu'lon could not clear its working folder {work} ({exc}). Nothing was packed."
        ) from exc
    try:
        return world.backup(
            only=wanted,
            label="move",
            # A whole server keeps its realm row (the old name travels; the address is put
            # right on the new computer). Accounts and characters leave it with the server.
            ignore_tables={auth: (world.entry.realmlist.table,)} if auth is not None else None,
            directory=work,
        )
    except BaseException:
        shutil.rmtree(work, ignore_errors=True)
        raise


# --------------------------------------------------------------- import: the plan


@dataclass(frozen=True)
class Replaces:
    """What an import would overwrite on a server that already has people on it."""

    accounts: int
    characters: int
    sentence: str


@dataclass(frozen=True)
class ImportPlan:
    """What bringing a package in would do, read without changing anything.

    `refusals` holds every reason it cannot go ahead, all at once. `token` is what the run
    wants back when the plan `replaces` something: derived from the package's manifest and the
    target's counts, so a yes is an answer about THIS file and THESE numbers.
    """

    path: Path
    manifest: Manifest | None
    refusals: tuple[str, ...]
    schemas: tuple[str, ...] = ()
    server_running: bool = False
    counts: tuple[int, int] | None = None
    replaces: Replaces | None = None
    updates_to_apply: Mapping[str, move.Tail] | None = None
    """Per-schema tails the file lacks the updater applies after the load (T802).

    Set exactly when the plan is allowed through the tail: every name here is an
    update the target already ran, proved against the checkout files. `None` on
    every refused plan and on every plan whose versions already match. Part of
    the tokened answer: the run re-classifies and refuses when it differs.
    """

    @property
    def allowed(self) -> bool:
        return not self.refusals

    @property
    def token(self) -> str:
        manifest = self.manifest.model_dump_json(by_alias=True) if self.manifest else ""
        tails = (
            "|".join(
                f"{schema}:{','.join(names)}"
                for schema, names in sorted((self.updates_to_apply or {}).items())
            )
            if self.updates_to_apply
            else ""
        )
        material = f"{self.path.resolve()}|{manifest}|{self.counts}|{tails}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _replaces(accounts: int, characters: int) -> Replaces | None:
    if accounts == 0 and characters == 0:
        return None
    if characters:
        what = (
            f"{_plural(characters, 'character', 'characters')} on "
            f"{_plural(accounts, 'account', 'accounts')}"
        )
    else:
        what = f"{_plural(accounts, 'account', 'accounts')} (no characters)"
    return Replaces(
        accounts=accounts,
        characters=characters,
        sentence=(
            f"This server already has {what}. Bringing these in REPLACES them; a copy of this "
            "server's accounts and characters is taken first. Replace?"
        ),
    )


def _record_refusals(world: MoveWorld, package: move.Package) -> list[str]:
    """One refusal per dump whose game record is missing, unreadable or another game's."""
    return record_refusals(world.game.id, world.game.name, package)


def record_refusals(game_id: str, game_name: str, package: move.Package) -> list[str]:
    """`_record_refusals` for a caller with no server yet (a whole-server import, level 2)."""
    refusals: list[str] = []
    for member in package.manifest.databases:
        try:
            found = maintenance.game_recorded_in(package.head(member.schema_name), member.file)
        except MaintenanceError as exc:
            refusals.append(str(exc))
            continue
        if found is None:
            refusals.append(move.unlabeled_dump(member.file))
        elif found != game_id:
            refusals.append(move.dump_from_another_game(member.file, _game_name(found), game_name))
    return refusals


def _shape_refusals(world: MoveWorld, manifest: Manifest) -> list[str]:
    """The package must hold this game's accounts and characters, under this game's names."""
    schemas = world.entry.schema_map()
    held = {m.role: m.schema_name for m in manifest.databases}
    refusals: list[str] = []
    for role in EVIDENCE_ROLES:
        if role not in held:
            refusals.append(
                f"This package does not hold the {role} database, so it is not a set of "
                "accounts and characters."
            )
    for role, schema in held.items():
        if schemas.get(role) != schema:
            refusals.append(
                f"This package holds {schema} as its {role} database, which is not what a "
                f"{world.game.name} server calls it."
            )
    for role in EVIDENCE_ROLES:
        named = held.get(role)
        if named is not None and named not in manifest.schema_evidence:
            refusals.append(
                f"{named} in this package does not record its database version, so Yu'lon "
                "cannot tell that it fits this server."
            )
    return refusals


def plan_import(world: MoveWorld, path: Path) -> ImportPlan:
    """Read a package and this server, and say what bringing it in would do.

    Nothing is written. The package is checked first and the database is asked last, so a
    package that is the wrong game, unlabelled or damaged is refused without the database
    being touched; when the database does have to be asked and is down, it is started alone
    and put back.
    """
    try:
        package = move.read_package(path)
        move.refuse_another_game(package.manifest, world.game.id, world.game.name)
    except MovePackageError as exc:
        return ImportPlan(path=path, manifest=None, refusals=(str(exc),))
    manifest = package.manifest
    if manifest.kind != "characters":
        return ImportPlan(path=path, manifest=None, refusals=(WHOLE_SERVER_FILE,))
    refusals = [*_record_refusals(world, package), *_shape_refusals(world, manifest)]
    try:
        _refuse_if_unsafe(world)
    except MoveError as exc:
        refusals.append(str(exc))
    if refusals:
        return ImportPlan(path=path, manifest=manifest, refusals=tuple(refusals))
    schemas = tuple(sorted((m.schema_name for m in manifest.databases), key=_load_order(world)))
    try:
        running = _server_is_up(world)
        with _database_session(
            world, because="nothing was brought in", undone=_NOT_IN, press=PRESS_BRING_IN
        ):
            roles = _present_roles(world)
            here = _read_versions(world, roles)
            counts = _survey_counts(world, roles)
            uncovered = _data_the_file_does_not_cover(world, manifest, roles)
    except MaintenanceError as exc:
        return ImportPlan(path=path, manifest=manifest, refusals=(str(exc),), schemas=schemas)
    except Exception as exc:  # noqa: BLE001 - Docker not answering is a refusal
        return ImportPlan(
            path=path,
            manifest=manifest,
            refusals=(f"Yu'lon could not look at this server: {exc}",),
            schemas=schemas,
        )
    said = _tail_marker_refusal(world, here)
    tails: dict[str, move.Tail] | None = None
    if said is None:
        said = _version_problem(world, manifest, roles, here, package)
        if said:
            tails = tail_updates(world, manifest, here, package)
            if tails is not None:
                said = _one_shot_scope_problem(world, package, tails)
                if said is not None:
                    tails = None
    if counts is None:
        refusals.append(_COUNT_UNKNOWN)
    if uncovered:
        refusals.append(_uncovered_sentence(uncovered))
    return ImportPlan(
        path=path,
        manifest=manifest,
        refusals=tuple(refusals) if said is None else (*refusals, said),
        schemas=schemas,
        server_running=running,
        counts=counts,
        replaces=_replaces(*counts) if counts is not None else None,
        updates_to_apply=tails,
    )


_NOT_IN = "Nothing was brought in."
_COUNT_UNKNOWN = (
    "Yu'lon could not tell whether this server already has accounts or characters, so it "
    "will not replace anything."
)


def _survey_counts(world: MoveWorld, roles: Mapping[Role, str]) -> tuple[int, int] | None:
    try:
        counts = _read_counts(world, roles)
    except Exception as exc:  # noqa: BLE001 - unknown is refused, never read as "none"
        logger.info(f"could not count the target's players: {exc}")
        return None
    return counts.accounts, counts.characters


_UNCOVERED = (
    "This server holds data that the file does not cover and that refers to its characters "
    "({names}). Bringing the file in would leave that data pointing at the wrong characters, "
    "so Yu'lon will not do it. Install the same modules on both servers (or remove those "
    "here), then try again. {undone}"
)


def _data_the_file_does_not_cover(
    world: MoveWorld, manifest: Manifest, roles: Mapping[Role, str]
) -> list[str]:
    """Non-empty tables, in the schemas an import touches, that the package holds no copy of.

    The engine's load is a merge: a table the file holds is replaced and one it does not hold
    stays. Rows kept that way are keyed by character and account ids the import reuses
    (a module's per-character table, the random-bot registry of a server whose file has no
    bots), so they would attach to the wrong people. Empty ones are harmless and not listed.
    """
    held = {m.schema_name: set(m.tables) for m in manifest.databases}
    # The realm row is the one table left out of a package on purpose (it is this server's own).
    kept_on_purpose = {(roles.get("auth"), world.entry.realmlist.table)}
    found: list[str] = []
    for role in ROLES:
        schema = roles.get(role)
        if schema is None:
            continue
        for name, kind in world.mysql.tables(schema):
            if name in held.get(schema, set()) or kind == "VIEW":
                continue
            if (schema, name) in kept_on_purpose:
                continue
            quoted = name.replace("`", "``")
            rows = world.mysql.query(f"SELECT COUNT(*) FROM `{schema}`.`{quoted}`;").strip()
            if not rows.isdigit():
                raise MoveError(f"The row count of {schema}.{name} came back as {rows!r}.")
            if int(rows) > 0:
                found.append(f"{schema}.{name}")
    return found


def _uncovered_sentence(found: Sequence[str]) -> str:
    shown = ", ".join(found[:6]) + (f" and {len(found) - 6} more" if len(found) > 6 else "")
    return _UNCOVERED.format(names=shown, undone=_NOT_IN)


def _load_order(world: MoveWorld) -> Callable[[str], int]:
    schemas = world.entry.schema_map()
    order = {schemas[r]: i for i, r in enumerate(ROLES) if r in schemas}
    return lambda name: order.get(name, len(order))


# --------------------------------------------------------------- import: the run


@dataclass(frozen=True)
class ImportResult:
    manifest: Manifest
    schemas: tuple[str, ...]
    copies: tuple[Path, ...]
    stopped: bool
    notes: tuple[str, ...] = ()

    def text(self) -> str:
        counts = self.manifest.counts
        lines = [
            f"Brought in {counts_phrase(counts)}: {', '.join(self.schemas)}.",
            "GM levels came with the accounts. Logins and passwords are the old ones.",
            "The copy of this server taken first (before-move): "
            + (", ".join(str(p) for p in self.copies) or "none, there was nothing to copy")
            + ".",
            "The world database (custom items, NPCs, objects placed with GM commands) stayed "
            "on the old computer; this server keeps its own.",
            *self.notes,
            "Start the server to play. Yu'lon's command channel used an account that was "
            "part of what was replaced: if the Server tab offers Repair for it, press it.",
        ]
        return "\n".join(lines)


_SESSION_KEY_COLUMNS = ("session_key", "session_key_auth", "sessionkey")
"""The names the cores give the stored session key of an account (AzerothCore, TrinityCore, CMaNGOS
and Tortoise). Asked of the database rather than decided per core: a column that is not there is
not updated, and one that is there is cleared whatever core says it."""


def session_key_sql(entry: CatalogEntry, columns: Sequence[tuple[str, bool]]) -> str:
    """`UPDATE` that clears the stored session key of every account, or "" if there is none.

    `columns` is `(name, nullable)` for each session-key column the account table has: a nullable
    one is set to NULL (a binary key has no meaningful empty string), a NOT NULL text one to ''.
    """
    sets = [f"`{name}` = {'NULL' if nullable else repr('')}" for name, nullable in columns]
    if not sets:
        return ""
    return f"UPDATE `{entry.databases.auth}`.`account` SET {', '.join(sets)};"


def _session_key_columns(world: MoveWorld) -> list[tuple[str, bool]]:
    names = ", ".join(f"'{c}'" for c in _SESSION_KEY_COLUMNS)
    out = world.mysql.query(
        "SELECT COLUMN_NAME, IS_NULLABLE FROM information_schema.COLUMNS "
        f"WHERE TABLE_SCHEMA = '{_literal(world.entry.databases.auth)}' AND TABLE_NAME = 'account' "
        f"AND COLUMN_NAME IN ({names}) ORDER BY COLUMN_NAME;"
    )
    found: list[tuple[str, bool]] = []
    for line in out.splitlines():
        name, _tab, nullable = line.partition("\t")
        if name.strip() in _SESSION_KEY_COLUMNS:
            found.append((name.strip(), nullable.strip().upper() == "YES"))
    return found


def realm_name_sql(entry: CatalogEntry, name: str) -> str:
    """The old realm name into this server's realm row, as a hex literal (T808 review).

    It was a quoted text literal: under `NO_BACKSLASH_ESCAPES` a backslash in the name was
    stored doubled. The hex literal means the same under every `sql_mode`.
    """
    return realm_name.row_name_sql(entry, name)


def _row_realm_name(world: MoveWorld) -> str | None:
    """The configured realm row's name now, exactly; None when it could not be read as one row."""
    try:
        answer = world.mysql.query(realm_name.name_query(world.entry))
    except Exception as exc:  # noqa: BLE001 - unread is an answer: the choice is not kept
        logger.info(f"could not read the realm name back: {exc}")
        return None
    return realm_name.name_from_answer(answer)


def channel_account_sql(entry: CatalogEntry, packed_account: str | None) -> str:
    """Delete the command-channel accounts a package brought over, and their level rows.

    Both the account the manifest names and any other `YULON_<install id>`: after an import
    the target's own channel account is gone with the account table it lived in, so every
    such row left is another computer's, a GM account whose password that computer knows.
    """
    auth = entry.databases.auth
    exact = f"`username` = '{_literal(packed_account)}' OR " if packed_account else ""
    where = f"({exact}`username` REGEXP '^YULON_[0-9A-Fa-f]{{{INSTALL_ID_LENGTH}}}$')"
    statements: list[str] = []
    level = entry.accounts.level
    if level is not None and level.table:
        statements.append(
            f"DELETE FROM `{auth}`.`{level.table}` WHERE `{level.account_column}` IN "
            f"(SELECT `id` FROM `{auth}`.`account` WHERE {where});"
        )
    statements.append(f"DELETE FROM `{auth}`.`account` WHERE {where};")
    return "\n".join(statements)


def run_import(
    world: MoveWorld,
    plan: ImportPlan,
    *,
    confirm: str | None,
    use_old_realm_name: bool,
    stop_allowed: bool,
) -> ImportResult:
    """Bring the package in. Raises `MoveError` before writing anything if any guard fails."""
    if not plan.allowed or plan.manifest is None:
        raise MoveError(" ".join(plan.refusals) or _NOT_IN)
    if plan.replaces is not None and not (confirm is not None and confirm == plan.token):
        raise MoveError(REPLACE_NEEDS_A_YES)
    package = _reopen(plan)
    running = _server_is_up(world)
    if running and not stop_allowed:
        raise MoveError(RUNNING_NEEDS_A_YES_IMPORT)
    if running:
        refuse_over_a_running_load(world)
        world.stop_server()
    with _database_session(
        world,
        because="nothing was brought in",
        undone=_NOT_IN,
        press=PRESS_BRING_IN,
        alone=forgetting.MOVE_WRITES_ALONE,
    ):
        return _import_locked(world, plan, package, use_old_realm_name, stopped=running)


def _reopen(plan: ImportPlan) -> move.Package:
    """The package again, and it must be the one the plan was made from."""
    try:
        package = move.read_package(plan.path)
    except MovePackageError as exc:
        raise MoveError(f"{exc} {_NOT_IN}") from exc
    if package.manifest != plan.manifest:
        raise MoveError(CHANGED_SINCE_THE_PLAN)
    return package


def _import_locked(
    world: MoveWorld,
    plan: ImportPlan,
    package: move.Package,
    use_old_realm_name: bool,
    *,
    stopped: bool,
) -> ImportResult:
    manifest = package.manifest
    roles = _present_roles(world)
    here = _read_versions(world, roles)
    unfinished = _tail_marker_refusal(world, here)
    if unfinished is not None:
        raise MoveError(f"{unfinished} {_NOT_IN}")
    said = _version_problem(world, manifest, roles, here, package)
    tails: dict[str, move.Tail] | None = None
    if said:
        tails = tail_updates(world, manifest, here, package)
        planned = dict(plan.updates_to_apply or {})
        if tails is None or tails != planned:
            raise MoveError(f"{said} {_NOT_IN}")
        wider = _one_shot_scope_problem(world, package, tails)
        if wider is not None:
            raise MoveError(f"{wider} {_NOT_IN}")
    counts = _survey_counts(world, roles)
    if counts is None or counts != plan.counts:
        raise MoveError(CHANGED_SINCE_THE_PLAN)
    uncovered = _data_the_file_does_not_cover(world, manifest, roles)
    if uncovered:
        raise MoveError(_uncovered_sentence(uncovered))

    present = set(world.mysql.databases())
    existing = [s for s in plan.schemas if s in present]
    copies: tuple[Path, ...] = ()
    if existing:
        copies = tuple(d.path for d in world.backup(only=existing, label=BEFORE_MOVE_LABEL).dumps)

    folder = maintenance.backups_dir(world.server_dir) / (
        ".move-in-" + world.now().strftime("%Y%m%d-%H%M%S")
    )
    # The owner's alone (T745): the auth dump unpacked here holds every verifier, and a
    # backups folder made before T683 S5 is 0775.
    folder.mkdir(parents=True, exist_ok=False, mode=0o700)
    try:
        plans = _checked_plans(world, package, plan.schemas, folder)
        if tails is not None:
            _mark_unfinished(world, manifest, plan.schemas, copies)
        loaded: list[str] = []
        safety: list[Path] = list(copies)
        for schema, restore_plan in plans:
            try:
                report = world.restore(restore_plan, ENGINE_COPY_LABEL)
            except MaintenanceError as exc:
                raise MoveError(
                    _failed_part_way(loaded, schema, plan.schemas, safety, exc),
                    detail=exc.detail,
                ) from exc
            loaded.append(schema)
            safety.extend(report.safety_backup)
        notes = _fix_ups(world, manifest, roles, use_old_realm_name)
        notes = (*notes, *_apply_tail_updates(world, package, tails, here))
        _clear_tail_marker(world)
    finally:
        shutil.rmtree(folder, ignore_errors=True)
    left = [name for name in (roles.get(r) for r in ROLES) if name and name not in plan.schemas]
    if left:
        notes = (*notes, f"Left as they were (the package had none): {', '.join(left)}.")
    return ImportResult(
        manifest=manifest,
        schemas=tuple(loaded),
        copies=tuple(dict.fromkeys(safety)),
        stopped=stopped,
        notes=notes,
    )


def _mark_unfinished(
    world: MoveWorld, manifest: Manifest, schemas: Sequence[str], copies: Sequence[Path]
) -> None:
    """Write the unfinished-import marker just before the first load (T802).

    Its ledgers are the package's evidence for the schemas the load replaces: what
    this server holds once the dumps are in and before the one-shot has run.
    """
    ledger = {s: manifest.schema_evidence[s] for s in schemas if s in manifest.schema_evidence}
    try:
        _write_tail_marker(world, copies, ledger)
    except _TailMarkerUnreadable as exc:
        logger.warning(f"could not read the unfinished tail-import marker: {exc}")
        raise MoveError(
            f"{_unreadable_marker_sentence(_tail_marker_path(world))} {_NOT_IN}"
        ) from exc
    except OSError as exc:
        raise MoveError(
            f"Yu'lon could not write down that this import was starting, so it loaded "
            f"nothing. {_NOT_IN} The copies taken first (before-move) are "
            f"{', '.join(str(p) for p in copies) or 'none'}.",
            detail=str(exc),
        ) from exc


def _checked_plans(
    world: MoveWorld, package: move.Package, schemas: Sequence[str], folder: Path
) -> list[tuple[str, RestorePlan]]:
    """Extract every dump and have the engine plan every load, before any load starts."""
    plans: list[tuple[str, RestorePlan]] = []
    for schema in schemas:
        path = package.extract(schema, folder)
        try:
            maintenance.verify_dump(path, schema)
            recorded = maintenance.backup_game(path)
        except MaintenanceError as exc:
            raise MoveError(f"{exc} {_NOT_IN}") from exc
        if recorded is None:
            # Refused here and never acknowledged: Restore may ask the player about an old
            # backup; a move may not. (`restore()` refuses an unacknowledged one as well.)
            raise MoveError(f"{move.unlabeled_dump(f'db/{schema}.sql')} {_NOT_IN}")
        restore_plan = world.plan_restore(path)
        if not restore_plan.allowed:
            raise MoveError(f"{' '.join(restore_plan.refusals)} {_NOT_IN}")
        if restore_plan.databases != (schema,):
            # `verify_dump` reads only the head of the file; the engine's plan scans all of it.
            # A dump that goes on to `USE` another database would load into that one.
            raise MoveError(
                f"db/{schema}.sql writes into {', '.join(restore_plan.databases) or 'nothing'}, "
                f"not only into {schema}, so Yu'lon will not load it. {_NOT_IN}"
            )
        plans.append((schema, restore_plan))
    return plans


def _failed_part_way(
    loaded: Sequence[str],
    failing: str,
    every: Sequence[str],
    copies: Sequence[Path],
    exc: MaintenanceError,
) -> str:
    done = f"{', '.join(loaded)} was brought in" if loaded else "Nothing was brought in"
    if len(loaded) > 1:
        done = f"{', '.join(loaded)} were brought in"
    rest = [s for s in every if s not in loaded]
    return (
        f"{exc} {done}, and {', '.join(rest)} was not. The databases may be half-written. "
        f"The copies taken first (before-move) are {', '.join(str(p) for p in copies) or 'none'}; "
        f"the {MAINTENANCE_TAB} tab restores from them."
    )


def _fix_ups(
    world: MoveWorld, manifest: Manifest, roles: Mapping[Role, str], use_old_realm_name: bool
) -> tuple[str, ...]:
    """What has to be put right after the load. Each failure is a note; the load stands."""
    notes: list[str] = []

    def run(sql: str, failed: str) -> None:
        if not sql:
            return
        try:
            world.mysql.execute(sql)
        except Exception as exc:  # noqa: BLE001 - the load is done; say what is still to do
            logger.warning(f"after the move: {failed}: {exc}")
            notes.append(failed)

    run(
        channel_account_sql(world.entry, manifest.channel_account),
        "!! The old computer's command-channel account could not be removed from this "
        "server. Remove it on the Accounts tab: it is a GM account whose password the old "
        "computer knows.",
    )
    try:
        session_sql = session_key_sql(world.entry, _session_key_columns(world))
    except Exception as exc:  # noqa: BLE001 - a note, like every fix-up after the load
        logger.warning(f"after the move: could not look for session keys: {exc}")
        session_sql = ""
        notes.append(
            "The stored session keys could not be looked for; they are harmless and expire."
        )
    run(
        session_sql,
        "The stored session keys could not be cleared; they are harmless and expire.",
    )
    if use_old_realm_name and manifest.realm_name:
        failed = len(notes)
        run(
            realm_name_sql(world.entry, manifest.realm_name),
            "The realm name could not be changed to the old one.",
        )
        if len(notes) == failed:
            # An UPDATE that matched no row exits 0 too: kept only when the row says it.
            reads = _row_realm_name(world)
            if reads == manifest.realm_name:
                # Not on a realm-type entry (CoA): its realm type decides the name (T812).
                if not realm_name.follows_realm_type(world.entry):
                    notes.extend(
                        _keep_the_imported_realm_name(world.server_dir, manifest.realm_name)
                    )
            else:
                said = "could not be read back" if reads is None else f"reads '{reads}'"
                notes.append(
                    f"The realm name could not be changed to the old one: the realm row {said}. "
                    "This server's own realm name stays its choice."
                )
    return tuple(notes)


def _apply_tail_updates(
    world: MoveWorld,
    package: move.Package,
    tails: dict[str, move.Tail] | None,
    here: Mapping[str, Evidence | None],
) -> tuple[str, ...]:
    """Apply the tails the file lacks with the import one-shot, then re-read (T802).

    Only auth and characters run here, with the servers stopped and the database
    up: the same state the Update route runs its one-shot in. Every schema named
    here was classified as a tail at plan time and re-classified just before the
    loads, and the one-shot was shown to have nothing else to run
    (`_one_shot_scope_problem()`). After it, EVERY database it can write is read
    again: the tail's must hold the file's names plus the tail and their module rows
    as before, and every other (world among them) must be as it was just before.
    Playerbots is left to the next Start, which applies it the way an update does
    (T217); the result says so.
    """
    if not tails:
        return ()
    if world.apply_updates is None:
        raise MoveError(f"Yu'lon cannot apply the updates the file lacks here. {_NOT_IN}")
    run_tail = _run_tail(world, tails)
    if not run_tail:
        return (_playerbots_next_start_note(tails),)
    present = set(world.mysql.databases())
    expected: dict[str, tuple[Evidence | None, tuple[str, ...], tuple[str, ...]]] = {}
    file_core_names = _tail_expected_core_names(package, tails, run_tail)
    for schema in run_tail:
        want = here.get(schema)
        expected[schema] = (
            want,
            file_core_names.get(schema, ()),
            tuple(sorted(want.modules)) if want is not None else (),
        )
    for schema in _one_shot_schemas(world).values():
        if schema in run_tail or schema not in present:
            continue
        now = _read_version(world, schema)
        names = _read_update_names(world, schema, now) if now is not None else None
        if now is None or names is None:
            raise MoveError(
                f"Yu'lon could not read the update ledger of {schema} before applying the "
                "updates the file lacked, so it did not apply them. The accounts and characters "
                f"are loaded without those updates. {_tail_copies_sentence()} {_NOT_IN}"
            )
        expected[schema] = (now, tuple(sorted(names[0])), tuple(sorted(names[1])))
    lines: list[str] = []
    try:
        run = world.apply_updates(lines.append)
    except (docker.DockerCommandError, OSError) as exc:
        detail = (
            getattr(exc, "detail", "") if isinstance(exc, docker.DockerCommandError) else str(exc)
        )
        raise MoveError(
            _tail_failure_sentence(tails, world, detail),
            detail=detail,
        ) from exc
    if run.returncode != 0:
        detail = "\n".join(run.tail)
        raise MoveError(_tail_failure_sentence(tails, world, detail), detail=detail)
    for schema, (want, core, modules) in expected.items():
        got = _read_version(world, schema)
        names = _read_update_names(world, schema, got) if got is not None else None
        if (
            want is None
            or got is None
            or names is None
            or got.digest != want.digest
            or tuple(sorted(names[0])) != core
            or tuple(sorted(names[1])) != modules
        ):
            have = got.count if got is not None else "?"
            need = want.count if want is not None else "?"
            raise MoveError(
                f"The database updates the file lacked did not land as expected ({schema}): "
                f"it should hold {need} updates and holds {have} now. "
                "The accounts and characters are loaded but their databases may be partly "
                f"updated. {_tail_copies_sentence()} {_NOT_IN}"
            )
    return _tail_run_notes(tails, run_tail)


def _tail_expected_core_names(
    package: move.Package, tails: Mapping[str, move.Tail], run_tail: Mapping[str, move.Tail]
) -> dict[str, tuple[str, ...]]:
    """The core update names each run schema should hold after the one-shot (T802).

    The file's own ledger names plus the planned tail for that schema: the
    digest alone cannot tell a silent updater from a rename that kept the
    count, and the names alone cannot tell the tail from a different set.
    """
    found: dict[str, tuple[str, ...]] = {}
    for schema in run_tail:
        names = move.updates_names_in_dump(package, schema)
        if names is None:
            continue
        found[schema] = tuple(sorted((*names[0], *tails.get(schema, ()))))
    return found


def _tail_run_notes(
    tails: Mapping[str, move.Tail], run_tail: Mapping[str, move.Tail]
) -> tuple[str, ...]:
    """The done sentences for a one-shot that landed every name it owed (T802)."""
    notes = [_tail_applied_sentence(run_tail)]
    if any(schema not in run_tail for schema in tails):
        notes.append(_playerbots_next_start_note(tails))
    return tuple(notes)


def _tail_applied_sentence(tails: Mapping[str, move.Tail]) -> str:
    parts = []
    for schema in sorted(tails):
        names = tails[schema]
        parts.append(f"{schema}: {len(names)} {'update' if len(names) == 1 else 'updates'}")
    return (
        "The updates the file lacked were applied after the load "
        f"({'; '.join(parts)}), so this server's databases match what they were."
    )


def _playerbots_next_start_note(tails: Mapping[str, move.Tail]) -> str:
    bots = {schema: names for schema, names in tails.items() if schema == "acore_playerbots"}
    left = sorted(f"{schema} ({len(names)})" for schema, names in sorted(bots.items()))
    return (
        "The updates the file lacked for the bot database are applied by the next Start, "
        f"the way an update applies them ({'; '.join(left)})."
    )


def _tail_copies_sentence() -> str:
    return (
        "The copies taken first (before-move) hold this server's prior state, and the "
        f"{MAINTENANCE_TAB} tab restores from them."
    )


def _tail_failure_sentence(
    tails: Mapping[str, move.Tail],
    world: MoveWorld,
    detail: str,
) -> str:
    names = sorted(f"{schema}:{','.join(names)}" for schema, names in sorted(tails.items()))
    run = docker.AttachedRun(returncode=1, tail=tuple(detail.splitlines()) if detail else ())
    return (
        f"{docker.importer_failure(run, 'Applying the updates the file lacked')}: "
        "the accounts and characters are loaded but their databases may be partly updated "
        f"({'; '.join(names)}). {_tail_copies_sentence()} The package is unchanged: "
        f"bring it in again after that restore. {_NOT_IN}"
    )


def _keep_the_imported_realm_name(server_dir: Path, name: str) -> tuple[str, ...]:
    """The old realm name the person chose to bring in becomes this server's saved choice (T808).

    Otherwise a name saved on the Networking tab earlier would be put back over it by the next
    Start (`realm_name.keep_at_start()`), undoing the choice just made. A name the record cannot
    hold (`realm_name.check()` refuses it) drops the record instead, so nothing puts another name
    back. Inside the move's hold, like the UPDATE before it. Returns notes, never raises.
    """
    saved = realm_name.read_saved_name(server_dir)
    if saved == name:
        return ()
    try:
        holdable = realm_name.check(name) == name  # blanks around it would be trimmed
    except realm_name.RealmNameError:
        holdable = False
    unkept = (
        realm_name.record_name(server_dir, name) if holdable else realm_name.forget_name(server_dir)
    )
    if not unkept:
        return ()
    lead = f"The realm is called '{name}' now, but that could not be kept as its saved name"
    if saved is not None:
        return (
            f"{lead} ({unkept}), so the next Start may put '{saved}' back. "
            "Set it on the Networking tab.",
        )
    return (f"{lead} ({unkept}). Set it on the Networking tab so a rebuild keeps it.",)


# --------------------------------------------------------------- what the tab calls


def default_folder() -> Path:
    """Where a package is offered to be saved: Documents, else the home folder. Never the server."""
    home = Path.home()
    documents = home / "Documents"
    return documents if documents.is_dir() else home


@dataclass(frozen=True)
class MoveServices:
    """The four presses of the Move group, bound to one server. Fakes in tests."""

    plan_export: Callable[[], ExportPlan]
    export: Callable[[Path, bool], ExportResult]
    """`(folder, stop_allowed)`."""
    plan_import: Callable[[Path], ImportPlan]
    run_import: Callable[[ImportPlan, str | None, bool, bool], ImportResult]
    """`(plan, confirm, use_old_realm_name, stop_allowed)`."""
    default_folder: Callable[[], Path] = default_folder
    export_server: Callable[[Path, bool], ExportResult] | None = None
    """`(folder, stop_allowed)`: the whole server (level 2). None draws no such press."""
    world: MoveWorld | None = None
    """The server these presses act on, for a whole-server import's steps after its install."""


def services_for(
    world: MoveWorld, whole: Callable[[], move.ServerFacts] | None = None
) -> MoveServices:
    return MoveServices(
        world=world,
        export_server=(
            (
                lambda folder, stop_allowed: export_package(
                    world, folder, stop_allowed=stop_allowed, whole=whole
                )
            )
            if whole is not None
            else None
        ),
        plan_export=lambda: plan_export(world),
        export=lambda folder, stop_allowed: export_package(
            world, folder, stop_allowed=stop_allowed
        ),
        plan_import=lambda path: plan_import(world, path),
        run_import=lambda plan, confirm, old_name, stop_allowed: run_import(
            world,
            plan,
            confirm=confirm,
            use_old_realm_name=old_name,
            stop_allowed=stop_allowed,
        ),
    )


__all__ = [
    "BotMarker",
    "ExportPlan",
    "ExportResult",
    "ImportPlan",
    "ImportResult",
    "MoveError",
    "MoveServices",
    "MoveWorld",
    "PACK_FOLDER",
    "Replaces",
    "clear_pack_folder",
    "engine_for",
    "export_package",
    "pack_folder",
    "plan_export",
    "plan_import",
    "run_import",
    "services_for",
]
