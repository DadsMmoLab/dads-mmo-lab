"""Database backup and restore for a WotLK install (checklist 6.5, "Maintenance").

The behaviour to match is `archive/guides/wow-wotlk/WoW-WotLK-CONTROLS-1.md`
§"Backing Up Your Characters", not its shell. Four things in the guide's
commands are wrong for a program to reproduce and are deliberately not
reproduced:

* `-ppassword` puts the database root password in argv, which every local
  process can read. `apply.mysql_env()` is the one place this codebase decides
  how a password is handed to a MySQL client, and it uses `MYSQL_PWD`.
* `> ~/wow-backup-$(date).sql` is created and truncated by the shell *before*
  mysqldump runs, so a dump that fails still leaves a file with a backup's
  name. Here every dump is written to a `.partial` and only renamed once it
  has been checked, so a file with a backup's name has always passed
  `verify_dump()`.
* the databases are hardcoded to three. An install with playerbots or ALE has
  more, and `--databases acore_playerbots` against a server without it fails
  the whole dump. What exists is asked for, per install — see `backup()`.
* restore is a one-liner with a warning printed above it. A warning is not a
  mechanism; see "Restore" below.

Scope, stated rather than implied:

**There is no `clear_cache()`, and this is why.** The placeholder that used to
live here said "clear worldserver cache". No such thing exists to clear:
AzerothCore's worldserver holds no on-disk cache — world data is read out of
`acore_world` at startup and refreshed with the console's `.reload` commands or
a restart, both of which are lifecycle, not maintenance. `CONTROLS-1.md`
contains the word "cache" zero times. The two things the WotLK tooling really
does call a cache belong to other layers:

1. the **client's** `Cache/WDB/<locale>/itemcache.wdb`, which every connected
   player must delete themselves after an item-template change. It lives in the
   user's WoW client folder, on the player's own machine, and `wow-manage.sh`
   only ever *reminds* the user about it (four separate sites do). It is not a
   server action, so there is nothing here to call. `CLIENT_CACHE_NOTE` is the
   sentence to show instead of offering a button that cannot work.
2. **Docker's build cache** — `docker builder prune`, the CMake build volume,
   unused images (`wow-manage.sh`'s `cleanup_docker`). That is disk-space and
   rebuild hygiene, it is game-agnostic, and by style-guide §3 it belongs in
   `yulon/docker.py` with the rest of the shared Docker lifecycle, not in one
   game's maintenance module. It is not implemented anywhere yet; that is a
   checklist item, not a gap this file should fill badly.

**There is no `apply_sql()` either.** `apply.DockerSql.run_file()` and
`.run_statement()` already are "apply raw SQL against the appropriate
database", they already keep the password out of argv, and the applier already
routes every manifest SQL step through them. A third spelling would be a
style-guide §4 duplicate. What "SQL changes" needs that did *not* exist is the
half `wow-manage.sh` does before every SQL mod it ships — take a backup first —
and that is `backup(..., only=("acore_world",))`.

**Restore.** This is the one operation here that destroys player data, so it is
plan-then-apply, like `networking.plan()`/`apply()`, and the plan is a census
rather than a permission slip:

* `restore()` takes a `RestorePlan`, never a path, so a mistyped path fails in
  `plan_restore()` — before anything is touched — instead of part-way through.
* it refuses, rather than warns, while the worldserver or authserver is
  running. A live worldserver holds character state in memory and writes it
  back on save, so a restore under it is overwritten again within minutes: the
  operation would report success and then silently undo itself.
* `plan_restore()` re-runs inside `restore()` and both censuses must agree, the
  way `docker.stop_staged()` takes its census before acting and again after.
  A plan that was built while the server was down does not authorise a restore
  once it is up again.
* a restore that is interrupted (power, a killed process, a dead daemon) leaves
  the marker written in step 4 behind, so `interrupted_restore()` can tell a
  half-applied database from a finished one — mysql applies a dump statement by
  statement and has no other trace. A safety dump of what is about to be
  overwritten is taken first and named in that marker, which is the only thing
  that makes the half state recoverable.
* that marker is evidence about the databases whose copies it points at, and
  about nothing else. A restore skips the safety dump for exactly those
  databases an earlier marker already holds a usable copy of, and dumps every
  other database it is about to overwrite. Until 2026-08-23 the decision was
  made once for all of them, by asking whether the marker file existed.

**Not implemented, deliberately:** compression (see `_dump_argv()`), pruning old
backups (deleting a user's backups is destructive, nobody asked for it, and
`wow-manage.sh`'s keep-the-last-2 is a policy for a caller to choose), and
restoring a `.sql.gz` written by `wow-manage.sh` (`plan_restore()` refuses it by
name and says what to run).

Everything that reaches Docker goes through the `MysqlDocker` seam, so the unit
tests need neither a daemon nor a database.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import IO, Protocol

from yulon import docker, platform, runner
from yulon.apply import DB_NAMES, mysql_env
from yulon.controller_wow_wotlk import docker_ctl
from yulon.log import get_logger

logger = get_logger(__name__)

# Where backups live. The same directory `wow-manage.sh` uses (`sqlmod_init()`),
# so a user who has both tools sees one set of backups rather than two.
BACKUP_SUBDIR = Path("sql_scripts") / "backups"

# `wow-manage.sh`'s own filename shape, for the same reason.
_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"

# Schemas the server does not own. Dumping them would carry the container's own
# user accounts and grants into a file that then looks like a character backup,
# and restoring that over a different container is how a database stops
# accepting its own root password.
SYSTEM_SCHEMAS = frozenset({"information_schema", "performance_schema", "mysql", "sys"})

# The three every AzerothCore install has, and the DEFAULT only. `DB_NAMES` also
# knows `acore_playerbots` and `acore_ale`, which exist only where those modules
# are installed — so their absence is normal and is not reported, while the
# absence of one of these is an alarm worth carrying in the report (see
# `BackupReport.missing_core`).
#
# A CMaNGOS install has none of these names, and the alarm fired on every
# successful backup it took: "expected but absent: acore_auth, acore_characters,
# acore_world" on a dump that had taken tw_logon, tw_char and tw_world
# (Discord report, 2026-08-26). `backup(core_databases=...)` is what a caller
# holding the catalog entry passes instead.
CORE_DATABASES: tuple[str, ...] = (DB_NAMES["auth"], DB_NAMES["characters"], DB_NAMES["world"])

CLIENT_CACHE_NOTE = (
    "Item and spell changes are cached by the WoW client, not the server. Each "
    "player has to close the game and delete the Cache folder inside their own "
    "WoW folder (Cache/WDB/<locale>/itemcache.wdb) before the change shows up "
    "for them. Nothing on the server can do this for them."
)
"""What to tell a user who asks to "clear the cache" — see the module docstring."""

# What mysqldump writes first and last. The trailer is the whole point: mysqldump
# can exit 0 on a dump that stopped early, and the guide already knows the file
# has to be checked ("If the file is missing or 0 bytes, something went wrong").
# A size check alone passes a dump that died after the first table.
_DUMP_HEADER = re.compile(rb"^--\s+(MySQL|MariaDB)\s+dump\s")
_DUMP_TRAILER = b"-- Dump completed"

# Which schemas a dump file will write into. `USE` is what `--databases` always
# emits; `CREATE DATABASE` is checked too so a dump taken with `--no-create-db`
# still identifies itself. Both are anchored to a line start, so an occurrence
# inside row data would need a newline in front of it — possible in principle,
# and harmless: an over-reported name only means the plan promises to overwrite
# something it will not, and the safety dump covers one database too many.
_USE_LINE = re.compile(rb"(?:\A|\n)USE\s+`([^`\n]+)`")
_CREATE_DB_LINE = re.compile(rb"(?:\A|\n)CREATE DATABASE[^\n`]*`([^`\n]+)`")

# Enough for mysqldump's banner and for its trailer, neither of which is long.
_EDGE_BYTES = 8192
# Read size for the full-file scan, with an overlap longer than any pattern above
# so a match that straddles a boundary is still found (duplicates are deduped).
_SCAN_CHUNK = 1 << 20
_SCAN_OVERLAP = 512


class MaintenanceError(RuntimeError):
    """A backup or restore could not be completed, and nothing may be assumed about it."""


# ------------------------------------------------------------------- seams


class MysqlDocker(Protocol):
    """Everything this module needs of the install's database container."""

    def databases(self) -> tuple[str, ...]:
        """Every schema the server currently has, system schemas included."""

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        """Write a complete mysqldump of `database` to `sink`. Raises on failure."""

    def load_from(self, source: IO[bytes]) -> None:
        """Feed `source` (a mysqldump) to the client. Raises on failure."""


RunningNames = Callable[[], list[str]]
"""What is running right now, by container name — `yulon.docker.status`."""


@dataclass(frozen=True)
class DockerMysql:
    """`MysqlDocker` over `docker exec <db_container> mysql|mysqldump`.

    The client is the container's own, exactly as the guide runs it, which is
    also why none of the version-skew flags a host-installed mysqldump needs
    (`--column-statistics=0` against a MariaDB server, say) appear here: client
    and server are the same install.

    This calls `subprocess.run` directly instead of going through
    `yulon.runner`, a style-guide §3 deviation of the same kind
    `apply.DockerSql._mysql()` documents, and for a harder reason than that one.
    `runner.run()` captures the child's output into a `str` and `runner.stream()`
    yields decoded lines; a WotLK `acore_world` dump is far too large to hold in
    memory either way, and both decode with `errors="replace"`, which does not
    round-trip — a dump is bytes and a byte that fails to decode would be
    *rewritten* on its way to disk. So the requirement is not "runner has no
    argument for this", it is "no text-mode helper can carry this payload at
    all". `stdout=<the open file>` keeps CPython out of the data path entirely:
    the child writes to the fd, and this process only waits.
    """

    db_container: str
    root_password: str = field(repr=False)
    """Kept out of the generated `repr` because everything else here already
    keeps it out of argv, out of log lines and off disk, and a default `repr`
    would put it back — in a pytest assertion diff, a logged object, or a
    traceback frame dump in a UI error handler (review, 2026-08-23)."""

    def databases(self) -> tuple[str, ...]:
        proc = self._exec(
            ["mysql", "-uroot", "--batch", "--skip-column-names"],
            # Over stdin like `DockerSql.run_statement()`, though this particular
            # statement holds no secret — one rule, not one rule with exceptions.
            input_text="SHOW DATABASES;\n",
        )
        if proc.returncode != 0:
            raise MaintenanceError(f"could not list the databases: {_stderr(proc)}")
        out = proc.stdout or b""
        return tuple(
            name
            for name in (line.strip() for line in out.decode("utf-8", "replace").splitlines())
            if name
        )

    def dump_into(self, database: str, sink: IO[bytes]) -> None:
        proc = self._exec(self._dump_argv(database), stdout=sink)
        if proc.returncode != 0:
            raise MaintenanceError(f"mysqldump of {database} failed: {_stderr(proc)}")

    def load_from(self, source: IO[bytes]) -> None:
        # No database in argv: a dump taken with `--databases` carries its own
        # `CREATE DATABASE`/`USE`, so the file decides where it lands and there
        # is no second place for that answer to be wrong.
        proc = self._exec(["mysql", "-uroot"], stdin=source)
        if proc.returncode != 0:
            raise MaintenanceError(f"restore failed: {_stderr(proc)}")

    def _dump_argv(self, database: str) -> list[str]:
        """`mysqldump` for one database, with the flags each justified.

        `--databases` (rather than a bare name) is what makes the file
        self-describing: it emits `CREATE DATABASE`/`USE`, so `plan_restore()`
        can read the target out of the file instead of guessing it from the
        filename the way `wow-manage.sh` does — where renaming a backup makes
        the tool ask the user which database it was.

        `--single-transaction` takes the dump inside one consistent read view,
        so a backup can be taken while people are playing without locking them
        out. It only covers transactional (InnoDB) tables; anything
        non-transactional is dumped as it is found. The alternative,
        `--lock-tables`, holds the whole schema still for the length of a
        multi-gigabyte dump, which on a live server is the worse trade.

        `--routines` and `--events` are asked for because their absence is
        silent: a dump without them restores cleanly and is simply missing
        things. `--triggers` is mysqldump's default and is named anyway, so the
        set is a list rather than a list plus an assumption.

        **`--add-drop-database` is deliberately NOT asked for, and that makes a
        restore a merge rather than a replacement.** mysqldump emits
        `DROP TABLE IF EXISTS` before each table it carries and no
        `DROP DATABASE` at all, so loading this file replaces every table the
        backup holds and leaves every table it does not. Measured on Windows 11
        / Docker 29.7.2 (2026-08-23): a table created in `acore_world` after the
        backup was taken was still there after a full 306 MB restore of that
        schema — 313 tables where the backup had 312.

        That is the safer of the two, which is why it stays. Adding the flag
        would make a restore drop the whole schema first, so a load that dies
        part-way — a killed process, a full disk, the connection going — would
        leave *nothing* where today it leaves a database missing only whatever
        the load had not reached. The recovery story here is built on the second
        shape: `interrupted_restore()` reports a half-applied database and names
        the safety copy taken beforehand.

        What it costs is stated rather than hidden: a restore does not return a
        schema to exactly the state the backup describes. `restore()` says so.

        Not compressed. Compression would put this process back in the data path
        for the whole payload, and the plain `.sql` produced here is exactly
        what the guide's own restore line consumes.
        """
        return [
            "mysqldump",
            "-uroot",
            "--single-transaction",
            "--routines",
            "--events",
            "--triggers",
            "--databases",
            database,
        ]

    def _exec(
        self,
        command: list[str],
        *,
        input_text: str | None = None,
        stdin: IO[bytes] | None = None,
        stdout: IO[bytes] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        """One `docker exec`, with the missing-CLI guards in one place.

        Raises:
            MaintenanceError: there is no docker CLI to run — either never
                resolved, or resolved earlier to a path a Docker Desktop update
                has since moved (the `OSError`). Both roads end at the same
                sentence, as they do in `docker._docker()` and
                `apply.DockerSql._mysql()`; without this the second surfaces as
                a bare `[WinError 2]`.
        """
        program = platform.docker_program()
        if program is None:
            raise MaintenanceError(platform.DOCKER_CLI_MISSING_HELP)
        # `-i` only when something is being written to the child: a mysqldump
        # reads nothing, and an idle open stdin is one more thing to get wrong.
        interactive = ["-i"] if (input_text is not None or stdin is not None) else []
        argv = [
            program,
            "exec",
            *interactive,
            "-e",
            "MYSQL_PWD",  # value taken from OUR env by `docker exec`, never written here
            self.db_container,
            *command,
        ]
        try:
            return subprocess.run(
                argv,
                input=input_text.encode("utf-8") if input_text is not None else None,
                stdin=stdin,
                stdout=stdout if stdout is not None else subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                env=runner.child_env(mysql_env(self.root_password)),
                creationflags=runner.creationflags(),
            )
        except OSError as exc:
            logger.warning(f"{argv[0]} could not be started: {exc}")
            raise MaintenanceError(platform.DOCKER_CLI_MISSING_HELP) from exc


def _stderr(proc: subprocess.CompletedProcess[bytes]) -> str:
    return (proc.stderr or b"").decode("utf-8", "replace").strip() or "no error output"


def mysql_for(root_password: str) -> DockerMysql:
    """A `DockerMysql` bound to the WotLK database container (`docker_ctl.SPEC.db`).

    The per-game binding, in the package that owns the per-game facts — the same
    shape as `docker_ctl.wait_db_healthy_ready()`.
    """
    return DockerMysql(docker_ctl.SPEC.db, root_password)


# ------------------------------------------------------------------ backup


@dataclass(frozen=True)
class Dump:
    """One database's verified dump file."""

    database: str
    path: Path
    size_bytes: int


@dataclass(frozen=True)
class BackupReport:
    """What a backup wrote, and what the caller has to be told about it."""

    directory: Path
    dumps: tuple[Dump, ...]
    missing_core: tuple[str, ...] = ()
    """Core databases the server did not have. Empty on any healthy install."""

    server_was_running: bool = False
    """The dumps were taken one after another on a live server, so each is
    internally consistent and they are not consistent with each other."""

    @property
    def databases(self) -> tuple[str, ...]:
        """Exactly what is in this backup — the answer to "was mine included?"."""
        return tuple(dump.database for dump in self.dumps)

    @property
    def total_bytes(self) -> int:
        return sum(dump.size_bytes for dump in self.dumps)


def backups_dir(server_dir: Path) -> Path:
    """Where this install's backups live."""
    return server_dir / BACKUP_SUBDIR


def server_databases(mysql: MysqlDocker) -> tuple[str, ...]:
    """The schemas this install owns, sorted — everything minus `SYSTEM_SCHEMAS`.

    Asked, never assumed. A fixed list of three is the guide's data-loss trap
    (an install with playerbots silently loses its bot data), and a fixed list
    of five is the opposite trap (`--databases acore_ale` fails the whole dump
    on a plain install). Anything else present is included too: a module that
    created its own schema is exactly the case an allow-list would drop without
    saying so.
    """
    return tuple(sorted(set(mysql.databases()) - SYSTEM_SCHEMAS))


def backup(
    server_dir: Path,
    mysql: MysqlDocker,
    *,
    only: Sequence[str] | None = None,
    label: str | None = None,
    spec: docker.ContainerSpec = docker_ctl.SPEC,
    core_databases: Sequence[str] = CORE_DATABASES,
    running: RunningNames | None = None,
    now: datetime | None = None,
) -> BackupReport:
    """Dump every database this install has into `backups_dir(server_dir)`.

    Safe to run while people are playing — that is the case the guide is written
    for ("before doing anything major, back up first") and what
    `--single-transaction` is for. The database container does have to be up,
    since it is the thing being asked.

    Args:
        only: Restrict the backup to these schemas — the "take a backup before
            this SQL change" case, where dumping a multi-gigabyte `acore_world`
            for a change to `acore_characters` is the difference between a habit
            and a habit nobody keeps. A name that is not on the server is an
            error, not a silent omission.
        label: Goes in the filename between the timestamp and the database, so
            the copy taken automatically before a restore is not mistaken for
            one the user asked for (`wow-manage.sh` does the same with
            `pre_restore`).
        now: The timestamp in the filenames; defaults to the clock.

    Raises:
        MaintenanceError: the database container is not running, `only` named a
            database this server does not have, there was nothing to dump, a
            dump failed, or a dump came back untrustworthy (`verify_dump()`).
            Dumps already finished are left in place and named in the message —
            they are complete and verified, and deleting them would be the
            second mistake.
    """
    names = _census(running)
    if spec.db not in names:
        raise MaintenanceError(
            f"{spec.db} is not running, so there is no database to back up. Start the server "
            "first — a backup is taken from the running database, not from the files on disk."
        )
    present = server_databases(mysql)
    if only is not None:
        absent = [name for name in only if name not in present]
        if absent:
            raise MaintenanceError(
                f"this server has no {', '.join(absent)} — nothing was backed up. It has: "
                f"{', '.join(present) or 'nothing'}."
            )
        wanted = tuple(only)
    else:
        wanted = present
    if not wanted:
        raise MaintenanceError(f"{spec.db} is running but reports no databases; nothing to back up")

    directory = backups_dir(server_dir)
    directory.mkdir(parents=True, exist_ok=True)
    stamp = (now or datetime.now()).strftime(_TIMESTAMP_FORMAT)
    missing_core = tuple(name for name in core_databases if name not in present)
    if missing_core:
        logger.warning(f"this install has no {', '.join(missing_core)}; backing up what it does")

    middle = f"{label}_" if label else ""
    done: list[Dump] = []
    for database in wanted:
        try:
            done.append(_dump_one(mysql, database, directory / f"{stamp}_{middle}{database}.sql"))
        except MaintenanceError as exc:
            finished = ", ".join(d.path.name for d in done) or "nothing"
            raise MaintenanceError(
                f"{exc} — the backup is INCOMPLETE. Written and verified so far: {finished}. "
                f"Not backed up: {', '.join(wanted[len(done) :])}."
            ) from exc
    report = BackupReport(
        directory=directory,
        dumps=tuple(done),
        missing_core=missing_core,
        server_was_running=spec.world in names or spec.auth in names,
    )
    written = ", ".join(f"{d.database} ({d.size_bytes} bytes)" for d in report.dumps)
    logger.info(f"backed up {len(report.dumps)} database(s) into {directory}: {written}")
    return report


def _dump_one(mysql: MysqlDocker, database: str, target: Path) -> Dump:
    """Dump one database to `target`, via a `.partial` that is only renamed if it verifies.

    The rename is the whole point. The guide's `> file` redirect creates the
    file before mysqldump has done anything, so its documented failure mode is
    a zero-byte file wearing a backup's name; here the name is applied last, to
    bytes that have already passed `verify_dump()`.
    """
    if target.exists():
        # `os.replace` would take this file out silently, and the thing it would
        # take out is a backup.
        raise MaintenanceError(f"{target} already exists; refusing to overwrite a backup")
    partial = target.with_name(target.name + ".partial")
    try:
        with partial.open("wb") as sink:
            mysql.dump_into(database, sink)
        verify_dump(partial, database)
    except MaintenanceError:
        partial.unlink(missing_ok=True)
        raise
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise MaintenanceError(f"could not write {target}: {exc}") from exc
    os.replace(partial, target)
    return Dump(database=database, path=target, size_bytes=target.stat().st_size)


def verify_dump(path: Path, database: str | None = None) -> int:
    """Check a dump is worth calling a backup; return its size in bytes.

    A zero exit from mysqldump is not evidence: `--force` continues past errors,
    and a dump whose connection dies part-way still leaves a file that starts
    correctly and ends in the middle of an `INSERT`. So three things are
    checked, and only the first is the one the guide already knows about:

    1. it is not empty (the guide's own check, and the shape its `>` redirect
       produces on a failure);
    2. it begins with mysqldump's banner, so a file that is not a dump at all —
       a picked wrong path, a half-downloaded file — is rejected as such;
    3. it ends with `-- Dump completed`, which mysqldump writes after the last
       table. This is the check that catches a truncated dump that exited 0,
       and no amount of looking at the size can substitute for it.

    Head and tail only: the size is `stat`, and both markers are within a few
    hundred bytes of the ends, so this stays constant-time on a file that may be
    gigabytes.

    Args:
        database: If given, a `USE`/`CREATE DATABASE` line in the head must name
            it — proof this is the dump that was just asked for and not a stale
            file under the same name. Read as a whole identifier, not searched
            for as a substring: until 2026-08-23 this was `database in head`,
            which accepted a dump of `acore_worldxyz` as one of `acore_world`.

    Raises:
        MaintenanceError: naming the check that failed.
    """
    try:
        size = path.stat().st_size
        head = _read_edge(path, 0)
        tail = _read_edge(path, max(size - _EDGE_BYTES, 0))
    except OSError as exc:
        raise MaintenanceError(f"could not read {path}: {exc}") from exc
    if size == 0:
        raise MaintenanceError(f"{path.name} is empty, so nothing was dumped")
    if not _DUMP_HEADER.match(head):
        raise MaintenanceError(
            f"{path.name} does not start like a mysqldump, so it is not a database backup"
        )
    if _DUMP_TRAILER not in tail:
        raise MaintenanceError(
            f"{path.name} stops before mysqldump's end marker, so the dump was cut short "
            f"({size} bytes written). Restoring it would load part of a database."
        )
    if database is not None and database not in _databases_named_in_bytes(head):
        raise MaintenanceError(
            f"{path.name} does not name {database}; it is a dump of something else"
        )
    return size


def _read_edge(path: Path, offset: int) -> bytes:
    with path.open("rb") as fh:
        fh.seek(offset)
        return fh.read(_EDGE_BYTES)


# ----------------------------------------------------------------- restore


@dataclass(frozen=True)
class InterruptedRestore:
    """The marker a restore leaves behind while it is in flight.

    Its existence is the only way to tell a database that is half-overwritten
    from one that is not: mysql applies a dump statement by statement and leaves
    no trace of how far it got, so a restore that is killed part-way is
    otherwise indistinguishable from one that finished.
    """

    marker: Path
    backup: Path
    databases: tuple[str, ...]
    safety_backup: tuple[Path, ...]
    started_at: str

    readable: bool = True
    """False when the marker was there but could not be parsed.

    Such a marker is evidence that a restore was in flight and evidence of
    nothing else — no database, no safety copy — so it must not be read as
    cover for anything. Not part of `as_json()`: it describes how the file was
    read, not what it says.
    """

    def as_json(self) -> str:
        return json.dumps(
            {
                "backup": str(self.backup),
                "databases": list(self.databases),
                "safety_backup": [str(p) for p in self.safety_backup],
                "started_at": self.started_at,
            },
            indent=2,
        )


@dataclass(frozen=True)
class RestorePlan:
    """What a restore would overwrite, and every reason it must not happen.

    A plan is a census, not a permission slip: `restore()` builds a fresh one
    and refuses if it disagrees with this one.
    """

    backup: Path
    server_dir: Path
    databases: tuple[str, ...]
    size_bytes: int
    refusals: tuple[str, ...] = ()
    interrupted: InterruptedRestore | None = None
    """A previous restore that never finished. Not a refusal — running this one
    is how that state is escaped — but the caller has to know the databases IT
    names are already part-overwritten, and that for those the safety copy worth
    keeping is that restore's, not one taken now. It says nothing about any
    other database: `restore()` still dumps everything it is about to overwrite
    that this marker does not already hold a usable copy of."""

    @property
    def allowed(self) -> bool:
        return not self.refusals

    @property
    def token(self) -> str:
        """What `restore(confirm=...)` must be handed.

        Derived from the file's identity and length plus the schemas it names,
        so a caller cannot restore a file it never inspected, and a plan cannot
        be reused against a file that has been replaced by one of a different
        size. It is not a content hash and does not claim to notice a
        same-length substitution — `restore()` re-plans for that. What it does
        buy is that no confirmation can be spelled `True`.
        """
        material = f"{self.backup.resolve()}|{self.size_bytes}|{','.join(self.databases)}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class RestoreReport:
    """A restore that ran to completion."""

    backup: Path
    databases: tuple[str, ...]
    safety_backup: tuple[Path, ...]


def marker_path(server_dir: Path) -> Path:
    """The in-flight restore marker for this install."""
    return backups_dir(server_dir) / "restore-in-progress.json"


def interrupted_restore(server_dir: Path) -> InterruptedRestore | None:
    """The restore that was in flight and never finished, if there was one.

    Worth asking on start-up: a database left half-overwritten looks like a
    working server until somebody logs in.
    """
    marker = marker_path(server_dir)
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        # The marker exists and cannot be read, which still means a restore was
        # in flight. Saying "none" here would be the one wrong answer.
        logger.warning(f"unreadable restore marker at {marker}: {exc}")
        return InterruptedRestore(marker, Path(), (), (), "", readable=False)
    if not isinstance(raw, dict):
        logger.warning(f"restore marker at {marker} is not an object; it names nothing")
        return InterruptedRestore(marker, Path(), (), (), "", readable=False)
    databases = raw.get("databases", [])
    safety = raw.get("safety_backup", [])
    if not isinstance(databases, list) or not isinstance(safety, list):
        # A readable dict whose VALUES are the wrong shape is not a marker this
        # code wrote, and reading it as one invents facts: `tuple(str(n) for n
        # in "acore_world")` iterates a STRING CHARACTER BY CHARACTER, which
        # produced a marker naming eleven fictional one-letter databases and,
        # since a marker naming databases nothing will ever restore is never
        # cleared, kept it forever (review, 2026-08-23).
        logger.warning(
            f"restore marker at {marker} has fields of the wrong shape; it names nothing"
        )
        return InterruptedRestore(marker, Path(), (), (), "", readable=False)
    return InterruptedRestore(
        marker=marker,
        backup=Path(str(raw.get("backup", ""))),
        databases=tuple(str(name) for name in databases),
        safety_backup=tuple(Path(str(p)) for p in safety),
        started_at=str(raw.get("started_at", "")),
    )


def forget_interrupted_restore(server_dir: Path) -> bool:
    """Put down the record of an interrupted restore. True if there was one to put down.

    `restore()` deliberately KEEPS a marker whose databases it did not put
    right, so the evidence survives every later restore of every other database.
    That is correct, and without this it is also a trap: a marker naming a
    database no restore will ever cover — a schema this server does not have, an
    install that has moved on, a copy the user restored by hand — warns on every
    start forever, and nothing could acknowledge it (review, 2026-08-23).

    It removes the RECORD, not the backups. The safety copies the marker named
    are left exactly where they are, because the usual reason to acknowledge a
    marker is that you have already used one of them and know where it went.
    """
    marker = marker_path(server_dir)
    try:
        marker.unlink()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise MaintenanceError(f"could not remove {marker}: {exc}") from exc
    logger.warning(f"forgot the interrupted-restore record at {marker}")
    return True


def plan_restore(
    backup_file: Path,
    server_dir: Path,
    *,
    spec: docker.ContainerSpec = docker_ctl.SPEC,
    running: RunningNames | None = None,
) -> RestorePlan:
    """Work out what restoring `backup_file` would do, without doing any of it.

    Every reason it must not go ahead is collected into `refusals` rather than
    raised, so a caller can show the user all of them at once. Nothing here
    writes, and the file is only read.

    The refusals, and why each is a refusal and not a warning:

    * the worldserver or authserver is running. A live worldserver holds
      character state in memory and writes it back on save, so rows restored
      underneath it are overwritten again minutes later — the restore would
      report success and then undo itself. The check is by container name, which
      is honest about what it proves (see `Controller.status()`): something is
      using these names. That is the conservative direction here.
    * the database container is not running. There is nothing to restore into.
    * Docker would not say what is running. Fail closed, like
      `docker._refuse_without_an_identity()`: an unprovable "the server is
      stopped" is not one.
    * the file is not a complete mysqldump. `verify_dump()`'s reasons, applied
      to somebody else's file — which is where a wrong path, a truncated
      download or a `.gz` shows up.
    * the file names no database. With no `USE`/`CREATE DATABASE` in it there is
      no telling where its contents would land.
    """
    refusals: list[str] = []
    databases: tuple[str, ...] = ()
    size = 0

    if backup_file.suffix == ".gz":
        refusals.append(
            f"{backup_file.name} is compressed. Yu'lon restores plain .sql files; run "
            f"`gunzip -k {backup_file.name}` and choose the .sql it writes."
        )
    elif not backup_file.is_file():
        refusals.append(f"there is no file at {backup_file}")
    else:
        try:
            size = verify_dump(backup_file)
        except MaintenanceError as exc:
            refusals.append(str(exc))
        else:
            databases = _databases_named_in(backup_file)
            if not databases:
                refusals.append(
                    f"{backup_file.name} names no database (no USE or CREATE DATABASE), so "
                    "there is no telling what it would overwrite"
                )

    try:
        names = _census(running)
    except MaintenanceError as exc:
        refusals.append(str(exc))
    else:
        up = [name for name in (spec.world, spec.auth) if name in names]
        if up:
            # "ac-worldserver are running" is what a live run actually printed
            # (2026-08-23). This refusal is the one a user is most likely to
            # read, because it is the one standing between them and losing
            # characters, so it should not read as broken.
            verb = "is" if len(up) == 1 else "are"
            refusals.append(
                f"{', '.join(up)} {verb} running. A restore has to happen with the game servers "
                "stopped: the worldserver keeps characters in memory and saves them back, so it "
                "would overwrite the restored data within minutes. Stop the server and try again."
            )
        if spec.db not in names:
            refusals.append(f"{spec.db} is not running, so there is nothing to restore into")

    plan = RestorePlan(
        backup=backup_file,
        server_dir=server_dir,
        databases=databases,
        size_bytes=size,
        refusals=tuple(refusals),
        interrupted=interrupted_restore(server_dir),
    )
    if refusals:
        logger.info(f"restore of {backup_file.name} refused: {'; '.join(refusals)}")
    return plan


def restore(
    plan: RestorePlan,
    mysql: MysqlDocker,
    *,
    confirm: str,
    spec: docker.ContainerSpec = docker_ctl.SPEC,
    running: RunningNames | None = None,
    now: datetime | None = None,
) -> RestoreReport:
    """Overwrite the databases `plan.backup` names. This destroys player data.

    Takes a `RestorePlan`, never a path, so a mistyped path is rejected by
    `plan_restore()` before anything is touched rather than half-way through a
    load. `confirm` must be `plan.token`.

    **What "overwrite" means here, exactly: every table the backup holds is
    replaced, and every table it does not hold is left alone.** A restore is
    therefore a merge, not a return to the state the backup describes — a table
    created since it was taken survives it. This follows from `_dump_argv()`
    declining `--add-drop-database`, which is a deliberate choice recorded
    there, and it was measured rather than reasoned about: a marker table made
    after the backup was still in `acore_world` after a full 306 MB restore of
    that schema (Windows 11 / Docker 29.7.2, 2026-08-23). Rows inside a table
    the backup does carry are replaced wholesale, which is the case a user
    restoring characters is actually in.

    The order is: re-census, safety dump, marker, load, marker removed. The
    marker is what makes the middle of that sequence recognisable afterwards —
    if this raises or the process dies, `interrupted_restore()` reports what was
    in flight and where the safety copy is.

    A database an `interrupted` marker already holds a usable copy of does NOT
    get a fresh safety dump: it is already part-overwritten, so dumping it now
    would capture the mess and the pointer to the last good copy would be lost.
    That copy is carried forward instead. Every other database this restore
    overwrites is dumped, including when the marker cannot be parsed at all —
    such a marker names no database and stands in for nothing. Until 2026-08-23
    the presence of any marker file suppressed the dump for every database.

    Raises:
        MaintenanceError: the confirmation does not match, the plan no longer
            holds (the server came up, the file changed), the safety dump
            failed, or the load failed. In the last case the marker is left
            behind on purpose and named in the message.
    """
    if confirm != plan.token:
        raise MaintenanceError(
            "the restore was not confirmed against this backup, so nothing was changed"
        )
    fresh = plan_restore(plan.backup, plan.server_dir, spec=spec, running=running)
    if fresh.refusals:
        raise MaintenanceError(f"restore refused: {' '.join(fresh.refusals)}")
    if fresh.token != plan.token:
        raise MaintenanceError(
            f"{plan.backup.name} is not the file that was checked — it changed in between. "
            "Nothing was restored; look at it again."
        )

    marker = marker_path(plan.server_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    # `fresh`, not `plan`: the marker is the one thing here that can appear
    # BETWEEN a plan being made and it being used, and reading the stale copy
    # would take a fresh safety dump of a database another restore had already
    # part-overwritten — capturing the mess and losing the pointer to the last
    # good copy, which is the exact accident the carry-forward exists to stop.
    earlier = fresh.interrupted
    if earlier is not None and not earlier.readable:
        logger.warning(
            f"the restore marker at {earlier.marker} cannot be read, so it names neither a "
            "database nor a safety copy. A copy of everything this restore overwrites is being "
            "taken — it may itself be a copy of a part-restored database, and it is still the "
            "only copy there would otherwise be."
        )
    kept = _usable_copies(earlier)
    safety = _safety_backup(plan, mysql, kept, spec=spec, running=running, now=now)
    unresolved = _still_unresolved(earlier, plan, kept)

    # The record carries what this restore is doing AND whatever an earlier
    # interrupted restore left unresolved that this one does not cover. The
    # marker is the only place either fact is written down, so a record naming
    # this restore alone would erase the evidence that some other database is
    # still half-applied (review, 2026-08-23).
    record = InterruptedRestore(
        marker=marker,
        backup=plan.backup,
        databases=plan.databases + (unresolved.databases if unresolved else ()),
        safety_backup=tuple(
            dict.fromkeys(safety + (unresolved.safety_backup if unresolved else ()))
        ),
        started_at=(now or datetime.now()).isoformat(timespec="seconds"),
    )
    _write_marker(
        record,
        consequence="An interrupted restore could not have been detected afterwards, so "
        "nothing was restored.",
    )
    logger.warning(f"restoring {', '.join(plan.databases)} from {plan.backup}")
    try:
        with plan.backup.open("rb") as source:
            mysql.load_from(source)
    except (MaintenanceError, OSError) as exc:
        raise MaintenanceError(
            f"the restore of {', '.join(plan.databases)} failed part-way: {exc}. Those databases "
            f"are now in an unknown state — {marker} records it, and the copy taken beforehand is "
            f"{', '.join(str(p) for p in safety) or 'missing (none was taken)'}."
        ) from exc
    if unresolved is None:
        marker.unlink(missing_ok=True)
    else:
        # This restore finished; the earlier one it did not cover has not, so
        # the marker shrinks to what is still unfinished instead of vanishing.
        #
        # Failing to shrink it must NOT fail the restore. The data is already
        # loaded — raising here reported a completed restore as
        # "Nothing was restored", which invites the user to run it again
        # (review, 2026-08-23). A marker that still over-names databases is a
        # false alarm on the next start, which is recoverable; a second restore
        # of a database that was already fine is not.
        try:
            _write_marker(
                unresolved,
                consequence=f"{', '.join(plan.databases)} WERE restored successfully; only the "
                "record of the earlier unfinished restore could not be updated.",
            )
        except (MaintenanceError, OSError) as exc:
            logger.error(
                f"{', '.join(plan.databases)} were restored successfully, but {marker} could not "
                f"be updated ({exc}); it still names them as part-restored, so the next start may "
                f"warn about databases that are in fact fine. Nothing needs re-running."
            )
        else:
            logger.warning(
                f"{', '.join(unresolved.databases)} were left part-restored by an earlier restore "
                f"that this one does not put right; {marker} still records them"
            )
    logger.info(f"restored {', '.join(plan.databases)} from {plan.backup}")
    return RestoreReport(backup=plan.backup, databases=plan.databases, safety_backup=safety)


def _usable_copies(earlier: InterruptedRestore | None) -> dict[str, Path]:
    """Which databases an interrupted restore's safety copies can still restore.

    Keyed by the database each file is a dump OF, read out of the file rather
    than taken from the marker's word for it: the marker is evidence about the
    databases its copies actually hold and about nothing else. `verify_dump()`
    is called WITHOUT a database here, because which database the file holds is
    the thing being discovered — so it answers two of the three questions in one
    head-and-tail read (the file is still there, and it is a complete dump) and
    the third, identity, comes from reading the head. An earlier version of this
    sentence claimed all three (review, 2026-08-23). So a copy
    the user has since deleted, or one that was cut short, stops counting as
    cover and the database it named is dumped afresh. Before 2026-08-23 nothing
    was checked: the marker file existing was the whole test, and a restore
    could report a safety copy that was not on disk.

    A file holding several databases counts only for the ones its head names,
    because a head read is all that is proved here. Being wrong in that
    direction costs one extra safety dump.
    """
    if earlier is None:
        return {}
    covers: dict[str, Path] = {}
    for path in earlier.safety_backup:
        try:
            verify_dump(path)
            head = _read_edge(path, 0)
        except (MaintenanceError, OSError) as exc:
            logger.warning(
                f"the safety copy an interrupted restore recorded at {path} cannot be used "
                f"({exc}), so it does not stand in for a fresh one"
            )
            continue
        for name in _databases_named_in_bytes(head):
            covers.setdefault(name, path)
    return covers


def _safety_backup(
    plan: RestorePlan,
    mysql: MysqlDocker,
    kept: dict[str, Path],
    *,
    spec: docker.ContainerSpec,
    running: RunningNames | None,
    now: datetime | None,
) -> tuple[Path, ...]:
    """A copy of every database this restore will overwrite, so it can be put back.

    "Undoable" is what this used to claim and it is not quite true, which
    matters because it is the only unqualified promise in the restore path.
    Loading this copy back merges the same way the restore did: it returns every
    table the copy holds, and leaves behind anything the restore ADDED that was
    not in the live database before. So it recovers the data, and it does not
    guarantee the exact bytes of the schema you started with (review,
    2026-08-24, following the merge finding measured on Windows).

    Only the schemas the restore will touch, and only those that exist — a
    backup file for a database this server does not have yet is creating
    something, not replacing it, and there is nothing to save.

    `kept` is what an interrupted restore's copies still cover
    (`_usable_copies()`). A database in there is deliberately NOT dumped again:
    it is part-overwritten already, so a dump now would capture the mess and the
    pointer to the last good copy would be lost. Every other database is dumped,
    whatever else that marker says. Until 2026-08-23 this was decided once for
    all of them, on the existence of the marker file, so a marker about
    `acore_world` sent `acore_characters` under with no copy of it anywhere.
    """
    present = set(server_databases(mysql))
    at_risk = [name for name in plan.databases if name in present]
    covered = {name: kept[name] for name in at_risk if name in kept}
    for name, path in covered.items():
        logger.warning(
            f"{name} was left part-restored by an earlier restore; keeping its safety copy "
            f"{path.name} rather than dumping a half-restored database over it"
        )
    to_dump = [name for name in at_risk if name not in covered]
    if to_dump:
        report = backup(
            plan.server_dir,
            mysql,
            only=to_dump,
            label="pre-restore",
            spec=spec,
            running=running,
            now=now,
        )
        covered.update({dump.database: dump.path for dump in report.dumps})
    return tuple(covered[name] for name in at_risk)


def _still_unresolved(
    earlier: InterruptedRestore | None, plan: RestorePlan, kept: dict[str, Path]
) -> InterruptedRestore | None:
    """What of an earlier interrupted restore this one does not put right.

    Restoring `acore_characters` says nothing about an `acore_world` some
    earlier restore left half-applied, so unlinking the marker once this one
    succeeds would erase the only record that `acore_world` is broken — and a
    half-overwritten database looks like a working server until somebody logs
    in. What this restore does not cover is written back instead (review,
    2026-08-23).
    """
    if earlier is None:
        return None
    covered = set(plan.databases)
    remaining = tuple(name for name in earlier.databases if name not in covered)
    if not remaining:
        return None
    return InterruptedRestore(
        marker=earlier.marker,
        backup=earlier.backup,
        databases=remaining,
        safety_backup=tuple(dict.fromkeys(kept[name] for name in remaining if name in kept)),
        started_at=earlier.started_at,
    )


def _write_marker(record: InterruptedRestore, *, consequence: str) -> None:
    """Write the marker atomically, and fail the restore if it cannot be written.

    Not best-effort. The marker is the only evidence a half-applied restore
    leaves, so a restore that could not write one would be a restore nobody
    could tell had been interrupted.
    """
    tmp = record.marker.with_name(record.marker.name + ".tmp")
    try:
        tmp.write_text(record.as_json() + "\n", encoding="utf-8")
        os.replace(tmp, record.marker)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            # Losing the temp file matters less than losing the real error, and
            # an unguarded cleanup let a raw PermissionError escape a function
            # that documents `MaintenanceError` (review, 2026-08-23).
            logger.warning(f"could not remove {tmp} after failing to write {record.marker}")
        raise MaintenanceError(f"could not write {record.marker}: {exc}. {consequence}") from exc


def _databases_named_in(path: Path) -> tuple[str, ...]:
    """Every schema a dump file writes into, in the order it first names them.

    The whole file is scanned, not just the head. The guide's own backup command
    puts three databases in one file (`--databases acore_characters acore_auth
    acore_world`), and the second and third `USE` lines are wherever the first
    database's data ends — so a head-only read would promise to overwrite one
    database and overwrite three. Chunked with an overlap so a match across a
    boundary is still found; memory is one chunk regardless of file size.
    """
    found: list[str] = []
    seen: set[str] = set()
    carry = b""
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_SCAN_CHUNK)
            if not chunk:
                break
            data = carry + chunk
            for name in _databases_named_in_bytes(data):
                if name not in seen:
                    seen.add(name)
                    found.append(name)
            carry = data[-_SCAN_OVERLAP:]
    return tuple(found)


def _databases_named_in_bytes(data: bytes) -> list[str]:
    """Every schema a `USE`/`CREATE DATABASE` line in `data` names, first use first.

    One reading of "which database is this", used by the whole-file scan above
    and by `verify_dump()`'s identity check, so the two cannot drift into
    disagreeing about what naming a database means.
    """
    found: list[str] = []
    for pattern in (_USE_LINE, _CREATE_DB_LINE):
        for match in pattern.finditer(data):
            name = match.group(1).decode("utf-8", "replace")
            if name not in found:
                found.append(name)
    return found


def _census(running: RunningNames | None) -> set[str]:
    """What is running, by container name; a Docker that will not answer is fatal.

    `docker.status()` raises rather than degrading, which is what this wants:
    every caller here is about to decide whether it is safe to touch a database,
    and "Docker did not say" must never read as "nothing is running" (that
    mistake is written up in `docker._refuse_without_an_identity()`).
    """
    # `docker_ctl.status`, not `docker.status`: this package re-exports the
    # shared operations so its callers have one entry point, and reaching past
    # that from inside the package would make the rule advice rather than
    # practice (style guide §3/§4; review, 2026-08-23). The two names below are
    # a type and an exception rather than operations, and `docker_ctl` does not
    # re-export those.
    source = running if running is not None else docker_ctl.status
    try:
        return set(source())
    except docker.DockerCommandError as exc:
        raise MaintenanceError(
            f"could not ask Docker what is running, so nothing about this server can be "
            f"established: {exc}"
        ) from exc
