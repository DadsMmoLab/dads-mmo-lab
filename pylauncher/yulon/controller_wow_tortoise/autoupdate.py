"""This fork's own database auto-updater, and the guard that keeps a module install off it.

Checklist 2504 gives 8.7d one clause no sibling box has: *nothing may run this
fork's own database auto-update path while the world is up*. This module is
that clause. Everything below was measured on m910q, 2026-09-08, against this
install's own source at `~/tortoise-server/src/tortoise-wow` (`3a8472e`) and its
own `etc/mangosd.conf` — per-tree facts, measured per tree.

**What the path is.** `World::SetInitialWorldSettings()` calls
`sAutoUpdater.ProcessUpdates()` and, on a false answer, logs
`DB AutoUpdater FAILED, cancelling server.` and calls `exit(1)`
(`src/game/World.cpp:1987-1994`). So on this fork the updater IS the
worldserver, running at startup, and **one bad migration cancels the whole
world**. That is the difference from AzerothCore, where the same idea is
`ac-db-import`, a separate one-shot container that can fail on its own without
taking a world down. `SqlStep.applied_by="db-import"` therefore means something
much more dangerous here than the word suggests.

**What arms it.**

* `Database.AutoUpdate.Enabled` — read as
  `sConfig.GetBoolDefault("Database.AutoUpdate.Enabled", true)`
  (`src/shared/Database/AutoUpdater.cpp:493`). **An absent key means ENABLED.**
  A reader that treated "not written down" as "off" would wave through the exact
  configuration every stock install runs.
* `Database.AutoUpdate.Path`, plus `AuthUpdateName`, `CharUpdateName` and
  `WorldUpdateName` — the three folders under it. This fork's shipped values are
  `auth`/`character`/`world`; MaNGOS's compiled defaults are `Logon`/`Char`/
  `World` (`AutoUpdater.cpp:499-501`), and inheriting those would look in three
  directories that do not exist and report a confident zero.
* The files themselves: every `*.sql` directly inside a folder, not recursively
  (`directory_iterator`, `AutoUpdater.cpp:102-106`).
* The ledger: a `migrations` table **in each target database**, keyed
  `<module>:<sha1 of the file bytes>` (`AutoUpdater.cpp:83-86`, `:114-127`,
  `:443`). Rows whose file is gone are old migrations and are harmless; files
  whose hash has no row are what would run at the next start.

That subtraction is computable from outside the server, which is what makes a
guard possible at all: `arming_from()` does exactly what `ProcessTargetUpdates`
does, without starting anything.

**Why this is not theoretical.** Rebuilding this fork onto its own head on
2026-09-07 crash-looped the worldserver twice on precisely this path —
`ALTER TABLE ai_playerbot_random_bots DROP INDEX idx_owner_bot_event` against an
index that was already gone (`[1091]`), then `[1062] Duplicate entry '51818-0'`
out of 173 outstanding world migrations. The owner's answer is recorded in
`pyplan/phase8-owner-answers-2026-09-08.md` §1. A module install is what asks
for the restart that re-enters it, so the guard belongs on the install path.

**Three answers, never two.** `Arming.armed` is `True`, `False` or `None`, and
`None` — "could not read" — refuses rather than passing. `migrations` genuinely
may not exist yet (the updater `CREATE TABLE IF NOT EXISTS`es it on its first
run, `AutoUpdater.cpp:135`), so "no such table" is a real answer this reader
gets, and answering it with "nothing outstanding" would be the same defect
`PendingSql` and `Ownership` were each given a third answer for.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import docker
from yulon.apply import Applier, ApplyError, ApplyReport, Completer, FolderSource, SqlRunner
from yulon.dbreads import SqlReader
from yulon.git import Git
from yulon.log import get_logger
from yulon.manifest import Db, Manifest, When

logger = get_logger(__name__)

CONF_FILE = "etc/mangosd.conf"
"""Where this install's copy of the fork's conf lives, relative to the server dir.

The installer materialises it out of the image (`families/cmangos.ETC_DIR`), and
the entry's own conf table rewrites some of its keys on every install and
repair — `Database.AutoUpdate.Path` among them, which is why no manifest may
set that key and `test_no_manifest_writes_a_key_the_installer_rewrites_on_every_run`
asserts the entry still says so.
"""

ENABLED_KEY = "Database.AutoUpdate.Enabled"
PATH_KEY = "Database.AutoUpdate.Path"

FOLDER_KEYS: Mapping[Db, tuple[str, str]] = {
    "auth": ("Database.AutoUpdate.AuthUpdateName", "Logon"),
    "characters": ("Database.AutoUpdate.CharUpdateName", "Char"),
    "world": ("Database.AutoUpdate.WorldUpdateName", "World"),
}
"""Manifest `db` key → (conf key, the CORE's compiled default for it).

The defaults are MaNGOS's (`AutoUpdater.cpp:499-501`) and are deliberately NOT
this fork's shipped values (`auth`/`character`/`world`): they are what the
server would use if the key were missing, which is the only thing a fallback is
allowed to be. The shipped values are read from the file like everything else.
"""

TARGETS: tuple[Db, ...] = ("auth", "characters", "world")
"""The three databases the updater walks, in its own order (`ProcessUpdates()`).

`tw_logs` is not one of them: this fork's updater never touches it.
"""

MIGRATION_TABLE = "migrations"
"""`AutoUpdater::MigrationTable`, `AutoUpdater.cpp:30` — one per target database."""


class AutoUpdateRefused(ApplyError):
    """This install would have handed work to the fork's own auto-updater.

    An `ApplyError` subclass so a caller that only knows the engine's vocabulary
    still stops, and a named type so `controller_view` can tell this refusal —
    which is about the SERVER's safety and has a switch the operator can throw —
    from a missing prompt value or a failed clone.
    """


# ------------------------------------------------------------------ reading


@dataclass(frozen=True)
class Settings:
    """The updater's configuration on one install, and whether the file said so."""

    enabled: bool
    declared: bool
    """False when `Database.AutoUpdate.Enabled` was absent and `enabled` is the
    fork's own `GetBoolDefault(..., true)`. Kept apart from `enabled` because
    "on because it says so" and "on because nobody said otherwise" are the same
    behaviour and very different things to show an operator."""
    path: str
    folders: Mapping[Db, str]
    conf: Path
    """The file these values were read from, so a report can name it."""


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _conf_value(text: str, key: str) -> str | None:
    """The last `key = value` in a worldserver conf, unquoted, or None if absent.

    The LAST rather than the first: `sConfig` reads the file top to bottom and a
    later assignment wins, so a conf carrying a commented block above a live
    value must not answer with the commented one. Comment lines are `#`, and a
    key inside one never matches because the pattern is anchored at the start of
    the line with only whitespace allowed in front.
    """
    pattern = re.compile(rf"^[ \t]*{re.escape(key)}[ \t]*=[ \t]*(.*?)[ \t]*$", re.MULTILINE)
    found: list[str] = pattern.findall(text)
    if not found:
        return None
    value = found[-1]
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def read_settings(server_dir: Path) -> Settings:
    """Read the updater's configuration out of this install's own `etc/mangosd.conf`.

    A missing or unreadable file answers the same way an empty one does —
    ENABLED, undeclared — for the same reason the absent key does: the fork's
    default is on, and a guard whose failure mode is "assume off" guards
    nothing.
    """
    conf = server_dir / CONF_FILE
    try:
        text = conf.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.debug(f"could not read {conf}: {exc}")
        text = ""
    raw = _conf_value(text, ENABLED_KEY)
    if raw is None:
        enabled, declared = True, False
    else:
        declared = True
        enabled = raw.strip().lower() not in _FALSE
        if raw.strip().lower() not in _TRUE | _FALSE:
            logger.warning(f"{ENABLED_KEY} = {raw!r} is not a boolean; reading it as enabled")
    return Settings(
        enabled=enabled,
        declared=declared,
        path=_conf_value(text, PATH_KEY) or "",
        folders={
            db: (_conf_value(text, key) or default) for db, (key, default) in FOLDER_KEYS.items()
        },
        conf=conf,
    )


@dataclass(frozen=True)
class Arming:
    """What this fork's updater would do at the next start of this world.

    `outstanding` is per target database, each a tuple of file names the ledger
    has no hash for — or `None`, meaning this reader could not find out, with
    `unreadable` carrying why.
    """

    enabled: bool
    outstanding: Mapping[Db, tuple[str, ...]] | None = None
    unreadable: tuple[str, ...] = ()

    @property
    def armed(self) -> bool | None:
        """True / False / None — would a start run a migration, or is it unknown?

        `enabled=False` short-circuits to False even with files waiting: they
        are waiting for a switch nobody has thrown, and refusing over them would
        refuse forever on an install that has deliberately turned the path off.
        """
        if not self.enabled:
            return False
        if self.outstanding is None:
            return None
        return any(self.outstanding.values())

    def summary(self) -> str:
        if self.outstanding is None:
            return f"could not be read ({'; '.join(self.unreadable) or 'no reason recorded'})"
        counted = ", ".join(f"{db} {len(names)}" for db, names in sorted(self.outstanding.items()))
        return f"enabled={self.enabled}, outstanding by database: {counted}"

    def names(self) -> tuple[str, ...]:
        if self.outstanding is None:
            return ()
        return tuple(name for _, names in sorted(self.outstanding.items()) for name in names)


def arming_from(
    *,
    enabled: bool,
    files: Mapping[Db, Mapping[str, str]],
    applied: Mapping[Db, set[str]],
) -> Arming:
    """The subtraction `ProcessTargetUpdates()` does, from outside the server.

    `files` is per database `{name: sha1-of-its-bytes}` and `applied` is the
    hashes that database's `migrations` table holds for the un-namespaced module
    (`Module = ''`). A file whose hash is in the ledger is skipped by the fork
    whatever its NAME now is — the fork only logs the rename
    (`AutoUpdater.cpp:188-191`) — so the comparison is by hash, never by name.
    """
    outstanding: dict[Db, tuple[str, ...]] = {}
    for db in TARGETS:
        have = {h.strip().lower() for h in applied.get(db, set())}
        outstanding[db] = tuple(
            sorted(name for name, digest in files.get(db, {}).items() if digest.lower() not in have)
        )
    return Arming(enabled=enabled, outstanding=outstanding)


def read_arming(
    server_dir: Path,
    *,
    world_container: str,
    schemas: Mapping[Db, str],
    sql: SqlRunner | None,
    settings: Settings | None = None,
    wsl_distro: str | None = None,
) -> Arming:
    """Ask the running install what its updater would do at the next start.

    Both halves live where only the running world can reach them, so both are
    read through the pieces this app already has: the `*.sql` files are inside
    the IMAGE (`/opt/tortoise/sql/database_updates/`, not under the server dir
    and not on the host at all), so they are listed with `docker exec` in the
    world container; the ledger is a table, so it is queried with the same
    `DockerSql` the module's own SQL goes through — the one already carrying
    this install's generated password.

    Every failure becomes `outstanding=None` with a sentence, never an empty
    set. Returning "nothing outstanding" because a query failed is the exact
    shape of lie this guard exists to prevent.
    """
    settings = settings or read_settings(server_dir)
    if not settings.enabled:
        # Nothing to read: the fork will not open these folders at all
        # (`ProcessUpdates()` returns before touching one).
        return Arming(enabled=False, outstanding={db: () for db in TARGETS})
    if not settings.path:
        return Arming(enabled=True, unreadable=(f"{PATH_KEY} is not set in {CONF_FILE}",))

    files: dict[Db, dict[str, str]] = {}
    applied: dict[Db, set[str]] = {}
    problems: list[str] = []
    reader = sql if isinstance(sql, SqlReader) else None
    if reader is None:
        problems.append("no database connection to read the migrations ledger with")

    for db in TARGETS:
        folder = f"{settings.path.rstrip('/')}/{settings.folders[db]}"
        listed = _list_update_files(world_container, folder, wsl_distro=wsl_distro)
        if listed is None:
            problems.append(f"{folder}: could not be listed in {world_container}")
        else:
            files[db] = listed
        if reader is not None:
            hashes = _applied_hashes(reader, db, schemas.get(db, db))
            if hashes is None:
                problems.append(f"{schemas.get(db, db)}.{MIGRATION_TABLE}: could not be read")
            else:
                applied[db] = hashes

    if problems:
        return Arming(enabled=True, unreadable=tuple(problems))
    return arming_from(enabled=True, files=files, applied=applied)


def _list_update_files(
    container: str, folder: str, *, wsl_distro: str | None
) -> dict[str, str] | None:
    """`{name: sha1}` for the `*.sql` directly inside `folder`, or None if unreadable.

    An EMPTY folder is a real, readable answer and comes back `{}` — which is
    why the shell prints a marker line before the loop rather than letting
    "no output" mean both "empty" and "the exec never ran". Not recursive,
    matching `directory_iterator` (`AutoUpdater.cpp:102`).
    """
    script = (
        f'cd "{folder}" 2>/dev/null || exit 3; echo OK; '
        'for f in *.sql; do [ -e "$f" ] || continue; sha1sum "$f"; done'
    )
    proc = docker.exec_output(container, ["sh", "-c", script], wsl_distro=wsl_distro)
    if proc.returncode != 0:
        logger.debug(f"listing {folder} in {container} exited {proc.returncode}: {proc.stderr}")
        return None
    lines = proc.stdout.splitlines()
    if not lines or lines[0].strip() != "OK":
        logger.debug(f"listing {folder} in {container} printed no marker: {proc.stdout[:200]!r}")
        return None
    found: dict[str, str] = {}
    for line in lines[1:]:
        digest, _, name = line.strip().partition(" ")
        name = name.strip()
        if digest and name:
            found[name] = digest.lower()
    return found


def _applied_hashes(reader: SqlReader, db: Db, schema: str) -> set[str] | None:
    """The hashes `schema`.`migrations` holds for the un-namespaced module, or None.

    `Module = ''` is the un-namespaced set — the files under
    `Database.AutoUpdate.Path` itself. A module's own updates carry its name
    (`ProcessModuleUpdates()`) and live under `modules/<name>/sql/...` inside the
    image rather than in these three folders, so they are a different question
    and this reader does not pretend to answer it.
    """
    try:
        answer = reader.query(db, f"SELECT Hash FROM `{MIGRATION_TABLE}` WHERE Module = ''")
    except Exception as exc:  # noqa: BLE001 - any failure is "could not read"
        logger.debug(f"{schema}.{MIGRATION_TABLE} could not be read: {exc}")
        return None
    return {line.strip().lower() for line in answer.splitlines() if line.strip()}


# ----------------------------------------------------------------- refusing


def check_manifest(manifest: Manifest) -> None:
    """Refuse an item that hands SQL to this fork's updater instead of running it.

    The static half of the guard, and the one that holds for a manifest this
    checkout never saw: `ManifestFetcher` mirrors these from GitHub into a cache
    the store then reads, so a test over the files in this repository cannot
    cover the file the applier is actually handed.
    """
    deferred = [
        step.path or step.statement or "?"
        for step in manifest.sql
        if step.applied_by == "db-import"
    ]
    if deferred:
        raise AutoUpdateRefused(
            f"{manifest.id}: this item defers SQL to the server's own importer "
            f"({', '.join(deferred)}), and on this fork that importer is the database "
            "auto-update path INSIDE the worldserver: World.cpp calls "
            "sAutoUpdater.ProcessUpdates() at startup and cancels the whole world with "
            "exit(1) if one migration fails. Tortoise items must run their SQL themselves "
            '(`"applied_by": "direct"`).'
        )


def check_restart_is_survivable(
    manifest: Manifest,
    *,
    settings: Settings,
    arming: Arming,
    world_running: bool,
) -> str:
    """Refuse an install whose restart would re-enter an armed updater. Returns a note.

    Scoped to `world_running`, which is checklist 2504's own scope — *while the
    world is UP*. With the world already stopped there is no running world to
    lose: the operator is starting one, and the start's own log is where they
    find out. Refusing there would block every module install done on a stopped
    server, which is when most of them are done.

    An item that asks for no restart is not refused either: it changes nothing
    the world reads at startup, so nothing about it brings the updater forward.
    """
    if not world_running:
        return f"auto-update guard: not applied, the world is not running " f"({arming.summary()})"
    if not manifest.build.restart:
        return f"auto-update guard: this item asks for no restart ({arming.summary()})"
    armed = arming.armed
    if armed is False:
        return f"auto-update guard: a restart is safe ({arming.summary()})"
    where = f"{settings.conf.parent.name}/{settings.conf.name}"
    declared = "" if settings.declared else " (absent from the file, so on by this fork's default)"
    if armed is None:
        raise AutoUpdateRefused(
            f"{manifest.id}: refused while the world is up. This item asks for a restart, and "
            f"this fork runs its own database auto-updater at every startup — one failing "
            f"migration logs 'DB AutoUpdater FAILED, cancelling server.' and the world does not "
            f"come back. Whether it has anything to run {arming.summary()}. Stop the world "
            f"first, or set {ENABLED_KEY} = 0 in {where}{declared} and start it by hand once."
        )
    names = arming.names()
    listed = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    raise AutoUpdateRefused(
        f"{manifest.id}: refused while the world is up. This item asks for a restart, and that "
        f"restart would hand {len(names)} migration(s) to this fork's own auto-updater, which "
        f"runs inside the worldserver at startup and cancels the whole world on one failure: "
        f"{listed}. Stop the world first, or set {ENABLED_KEY} = 0 in {where}{declared} and "
        f"start it by hand once."
    )


class GuardedApplier(Applier):
    """An `Applier` that will not let a Tortoise item near this fork's updater.

    A subclass rather than a wrapper because `ControllerServices` is typed on
    `Applier` and the Modules tab calls three methods on it — `install`,
    `configure` and `remove` — all three of which end in "and now restart".
    Guarding only `install` is the shape of defect `pyplan` records as *reviews
    check functions, not call sites*: `remove()` puts the shipped value back and
    asks for exactly the same restart.

    Both checks run BEFORE `super()` does anything, so a refused press leaves the
    conf file and the database untouched.
    """

    def __init__(
        self,
        server_dir: Path,
        *,
        arming: Callable[[], Arming],
        world_running: Callable[[], bool | None],
        settings_for: Callable[[Path], Settings] = read_settings,
        **kwargs: object,
    ) -> None:
        # The seam goes to the BASE as well as onto this object, and until T7 it
        # did neither by accident. `Applier` keeps its own running-world seam
        # private (`_world_running`) precisely so this public attribute could
        # not wire that guard live on Tortoise alone; the cost of that choice is
        # that a subclass swallowing the keyword leaves the base's guard at
        # `None` — which is what shipped, and what T2's reviewer found. One
        # callable, both guards, so this fork cannot end up with the updater
        # guard armed and the direct-SQL guard asleep.
        super().__init__(server_dir, world_running=world_running, **kwargs)  # type: ignore[arg-type]
        self.arming = arming
        self.world_running = world_running
        self.settings_for = settings_for

    def _guard(self, manifest: Manifest, action: When) -> str:
        check_manifest(manifest)
        return check_restart_is_survivable(
            manifest,
            settings=self.settings_for(self.server_dir),
            arming=self.arming(),
            # `is True`, not truthiness: the seam widened to three-valued in T7
            # for the base's guard, which fails CLOSED on "could not ask". THIS
            # guard's scope is checklist 2504's own — *while the world is UP* —
            # and an unreadable inspect used to arrive here as `False` through
            # `container_state().settled`. It still does, deliberately: 2504's
            # behaviour is unchanged by T7, and widening it is not this lane's.
            world_running=self.world_running() is True,
        )

    def install(
        self,
        manifest: Manifest,
        values: Mapping[str, str] | None = None,
        *,
        folder: FolderSource | None = None,
        complete: Completer | None = None,
    ) -> ApplyReport:
        # `folder` and `complete` are the base class's second way to fill
        # `modules/<id>` (a module from a link or a folder). Passed THROUGH,
        # not dropped: custom modules are wow-wotlk-only, so nothing reaches
        # this class with either set today, and a subclass that silently
        # ignored a keyword its base accepts would report an install it copied
        # nothing for. The guard still runs first, whichever route fills the
        # folder -- the restart a C++ module asks for is the same restart.
        note = self._guard(manifest, "install")
        return _with_note(super().install(manifest, values, folder=folder, complete=complete), note)

    def configure(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        note = self._guard(manifest, "configure")
        return _with_note(super().configure(manifest, values), note)

    def remove(self, manifest: Manifest, values: Mapping[str, str] | None = None) -> ApplyReport:
        note = self._guard(manifest, "remove")
        return _with_note(super().remove(manifest, values), note)


def _with_note(report: ApplyReport, note: str) -> ApplyReport:
    """Put the guard's reading into the report the tab prints.

    A guard that only speaks when it refuses leaves the operator with no way to
    tell "checked and safe" from "never ran" — which is how a guard comes to be
    believed while its own bug is live. So the permitted case says what it saw
    too, in `done`, next to the steps it permitted.
    """
    return ApplyReport(
        action=report.action,
        item_id=report.item_id,
        family=report.family,
        done=(*report.done, note),
        skipped=report.skipped,
        rebuild_required=report.rebuild_required,
        restart_recommended=report.restart_recommended,
        pending_sql=report.pending_sql,
    )


def guarded_applier(
    server_dir: Path,
    *,
    sql: SqlRunner | None,
    arming: Callable[[], Arming],
    world_running: Callable[[], bool | None],
    start_database: Callable[[], bool] | None = None,
    git: Git | None = None,
    client_dir: Path | None = None,
) -> GuardedApplier:
    """The applier the Tortoise Modules tab is handed. See `modules.applier()`."""
    return GuardedApplier(
        server_dir,
        git=git,
        sql=sql,
        client_dir=client_dir,
        arming=arming,
        world_running=world_running,
        start_database=start_database,
    )


Verdict = Literal["safe", "armed", "unknown", "off", "world-down"]
"""Named for a caller that wants the reading without the exception; unused today."""
