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
import shutil
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol

from yulon import docker, forgetting, move
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
from yulon.log import get_logger
from yulon.move import Counts, DumpFile, Evidence, Header, Manifest, MovePackageError, Role
from yulon.ownership import Ownership

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
    "Restore again from the Maintenance tab (that is how it is put right) before moving "
    "anything."
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
    """`(only=, label=, ignore_tables=) -> BackupReport`, bound by `engine_for`."""
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
    ) -> BackupReport:
        return maintenance.backup(
            server_dir,
            mysql,
            game=game,
            only=only,
            label=label,
            ignore_tables=ignore_tables,
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


def digest_of(names: Sequence[str]) -> str:
    """SHA-256 of a version's items, order-free: how two servers' versions are compared."""
    return hashlib.sha256("\n".join(sorted(names)).encode("utf-8")).hexdigest()


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


def _refuse_if_unsafe(world: MoveWorld) -> None:
    if world.ownership() is Ownership.UNKNOWN:
        raise MoveError(INSTALL_RECORD_DAMAGED)
    if maintenance.interrupted_restore(world.server_dir) is not None:
        raise MoveError(_UNFINISHED_RESTORE)


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
            sql = (
                f"SELECT `name` FROM {quoted}.`updates` "
                "WHERE `state` IN ('RELEASED', 'ARCHIVED') ORDER BY `name`;"
            )
            module_sql = (
                f"SELECT `name`, `hash` FROM {quoted}.`updates` "
                "WHERE `state` NOT IN ('RELEASED', 'ARCHIVED') ORDER BY `name`;"
            )
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


def _version_problem(
    world: MoveWorld,
    manifest: Manifest,
    roles: Mapping[Role, str],
    here: Mapping[str, Evidence | None],
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
    return move.version_difference(package, here, frozenset(optional))


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
        f"(SELECT COUNT(*) FROM `{auth}`.`account` WHERE ({bot}));"
    )
    fields = out.strip().split("\t")
    if len(fields) != 3 or not all(f.strip().isdigit() for f in fields):
        raise MoveError(f"The count of accounts came back as {out.strip()!r}, not three numbers.")
    return Counts(accounts=int(fields[0]), characters=int(fields[1]), bot_accounts=int(fields[2]))


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


@contextlib.contextmanager
def _database_session(world: MoveWorld, *, because: str, undone: str) -> Iterator[None]:
    """The leased, held, database-up stretch a move works in. `undone` names what did not happen.

    The same four steps the Maintenance tab's Backup and Restore take, in the same order: the
    lease (one Backup, Restore or move of a server at a time), the hold (no Start, Stop or
    recreate under it), the database started on its own if it is down, and put back after.
    """
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(
                docker.maintenance_lease(world.server_dir, forgetting.MOVE_HOLDS_THE_DATABASES)
            )
        except docker.MaintenanceLeaseTaken as exc:
            raise MoveError(f"{exc} {undone}") from exc
        try:
            stack.enter_context(
                docker.hold_the_server(world.server_dir, forgetting.MOVE_HOLDS_THE_SERVER)
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
            f"Packed {counts.accounts} accounts and {counts.characters} characters "
            f"({counts.bot_accounts} bot accounts) into {self.path}.",
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
    with _database_session(world, because="nothing was packed", undone=undone):
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
            for dump in report.dumps:
                dump.path.unlink(missing_ok=True)
    return ExportResult(path=dest, manifest=manifest, restarted=None)


def _tables_of(schema: str, path: Path) -> tuple[str, ...]:
    """The tables a finished dump holds, read from the file itself."""
    tables = maintenance.tables_in_copy(path).get(schema)
    if not tables:
        raise MoveError(f"The copy of {schema} lists no table, so it was not packed.")
    return tables


def _backup_for_the_package(
    world: MoveWorld, wanted: Sequence[str], auth: str | None
) -> BackupReport:
    """The engine's backup of the packed schemas, and no stray dump if it fails part-way.

    The dumps are this run's own files in `backups/`, and an auth dump among them holds every
    account's verifier: left behind after a failure, Restore would list it as an ordinary backup.
    """
    directory = maintenance.backups_dir(world.server_dir)
    before = {p.name for p in directory.glob("*")} if directory.is_dir() else set()
    try:
        return world.backup(
            only=wanted,
            label="move",
            # A whole server keeps its realm row (the old name travels; the address is put
            # right on the new computer). Accounts and characters leave it with the server.
            ignore_tables={auth: (world.entry.realmlist.table,)} if auth is not None else None,
        )
    except BaseException:
        for path in directory.glob("*_move_*"):
            if path.name not in before:
                path.unlink(missing_ok=True)
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

    @property
    def allowed(self) -> bool:
        return not self.refusals

    @property
    def token(self) -> str:
        manifest = self.manifest.model_dump_json(by_alias=True) if self.manifest else ""
        material = f"{self.path.resolve()}|{manifest}|{self.counts}"
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
        with _database_session(world, because="nothing was brought in", undone=_NOT_IN):
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
    said = _version_problem(world, manifest, roles, here)
    if said:
        refusals.append(said)
    if counts is None:
        refusals.append(_COUNT_UNKNOWN)
    if uncovered:
        refusals.append(_uncovered_sentence(uncovered))
    return ImportPlan(
        path=path,
        manifest=manifest,
        refusals=tuple(refusals),
        schemas=schemas,
        server_running=running,
        counts=counts,
        replaces=_replaces(*counts) if counts is not None else None,
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
            f"Brought in {counts.accounts} accounts and {counts.characters} characters "
            f"(and {counts.bot_accounts} bot accounts): {', '.join(self.schemas)}.",
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
    rl = entry.realmlist
    return (
        f"UPDATE `{entry.databases.auth}`.`{rl.table}` SET `name` = '{_literal(name)}' "
        f"WHERE `id` = {rl.realm_id};"
    )


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
        world.stop_server()
    with _database_session(world, because="nothing was brought in", undone=_NOT_IN):
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
    said = _version_problem(world, manifest, roles, here)
    if said:
        raise MoveError(f"{said} {_NOT_IN}")
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
    folder.mkdir(parents=True, exist_ok=False)
    try:
        plans = _checked_plans(world, package, plan.schemas, folder)
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
        "the Maintenance tab restores from them."
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
        run(
            realm_name_sql(world.entry, manifest.realm_name),
            "The realm name could not be changed to the old one.",
        )
    return tuple(notes)


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
    "Replaces",
    "engine_for",
    "export_package",
    "plan_export",
    "plan_import",
    "run_import",
    "services_for",
]
