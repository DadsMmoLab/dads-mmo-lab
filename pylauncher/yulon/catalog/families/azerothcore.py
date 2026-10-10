"""The AzerothCore family: WotLK's stage tuple on the shared spine (roadmap 7.1).

Today's `native.py` bodies, moved verbatim with their names, their evidence
rules and their cancel notes: `clone-core`, `clone-modules`, `generate-compose`,
`build`, `client-data`, `start-db`, `import`, `up`, `ready`. The names are
pinned by a test because a state file written by the 6.3 Windows partial
install (2026-08-25) exists and must still read.

What is AzerothCore-shaped and therefore here rather than in the spine: the
server dir IS the core checkout (source `dest` "."), the modules go under
`modules/`, the server data is fetched by a compose one-shot, and the import
is a compose one-shot gated by the injected `acore_*` probe pair. The probe is
INJECTED by the caller (`install_wiring.py`): this module never imports a
`controller_*` package.

T553 (Unbound P2) added two stages that only an entry asking for them runs:
`patch-sources` after `clone-modules` (`AzerothCoreData.patches`, through
`carried.py`) and `lua-and-sql` after `import` (`lua_scripts` and `sql_checks`,
through `scriptdeploy.py`). An entry with none of the three -- WotLK -- runs the
nine stages above and nothing else.
"""

from __future__ import annotations

import os
import posixpath
import re
import stat
import tempfile
from collections.abc import Generator, Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

from yulon import docker, git, networking, server_build_presses
from yulon.catalog import snapshot
from yulon.catalog.catalog import AzerothCoreData, CatalogEntry, EmulatorSource
from yulon.catalog.families import ale_playerbots, carried, scriptdeploy
from yulon.catalog.installer import InstallerError, InstallStopped, OneShotLeftRunning
from yulon.catalog.native import (
    DOWNLOAD_CANCEL_NOTE,
    IMPORT_STAGE_CANCEL_NOTE,
    OUR_OWN_FILES,
    CallableGate,
    ConfCheck,
    ConfRepaired,
    ServersDownWork,
    Stage,
    StageContext,
    StagedInstaller,
    _listed,
    _listing,
    build_cancel_note,
    download_left_sentence,
)
from yulon.log import get_logger
from yulon.manifest import Db

logger = get_logger(__name__)

CORE_UPDATES_NOTE = (
    "The new build was not started, so the build you have is put back, with the copy of its "
    "databases taken before the updates ran."
)
"""What a Stop during an update's core database updates costs (T220)."""

DIST_SUFFIX = ".dist"

SQL_FOLDER = "data/sql"
"""Where the core and every module keep the SQL their updaters apply (T630).

The core's `data/sql/updates/db_*` and `data/sql/archive`, mod-playerbots'
`data/sql/playerbots/updates`, a module's `data/sql/db-world`: one folder to ask git
about per source, whatever the layout under it."""

APPLIED_UPDATES_QUESTION = "SELECT name FROM updates WHERE name IN "
"""Which of the named files a database's update ledger holds (T630).

AzerothCore's updater, and mod-playerbots' own on its database, write one `updates`
row per file it applied, keyed by the file's base name (`apply.read_ledger()` asks
the same table), and nothing ever takes a row back: updates only go forward."""

DATABASE_MAYBE_STARTED = (
    "Yu'lon could not tell whether the database was running before it asked it, so it may "
    "have started the database for that; Stop on the Server tab takes it down."
)
"""Said instead of keeping quiet when the database's state before the check is unknown (T630)."""

_DATED = re.compile(r"^\d{4}_\d{2}_\d{2}_\d{2}")
"""An update named by its date, `YYYY_MM_DD_NN`: the only names whose order means age."""

_SORTING_FOLDERS = frozenset({"updates", "archive", "base", "custom", "pending"})
"""Folders under `data/sql` that sort updates rather than name a database (T630)."""

NOTHING_DONE = "Nothing was built, stopped or changed: your server stays on the code it runs."


def newer_updates_refusal(
    applied: Mapping[str, Sequence[str]], copies: Mapping[str, Path | None]
) -> str:
    """Why "Return to the tested pin…" stopped: the databases are ahead of the pin (T630).

    `applied` is, per database, the updates it holds that the tested commit does not
    ship; `copies` the dump `snapshot.copy_from_before()` found for each, or None. The
    way back is said only when every one of those databases has a copy from before.
    """
    each = []
    for database, names in applied.items():
        one = len(names) == 1
        each.append(
            f"{database} has {len(names)} update{'' if one else 's'} the tested commit does "
            f"not have ({', '.join(names)})"
        )
    databases = list(applied)
    total = sum(len(names) for names in applied.values())
    those = "that update" if total == 1 else "those updates"
    head = (
        "Your databases already hold updates the commit this app was tested against does not "
        f"have: {'; '.join(each)}. Database updates only go forward, so that commit's server "
        f"would meet {_listed(databases)} as {those} left "
        f"{'it' if len(databases) == 1 else 'them'}, which it was not built for and may not "
        f"start on. {NOTHING_DONE}"
    )
    missing = [database for database in databases if copies.get(database) is None]
    if missing:
        latest = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
        return (
            f"{head} Yu'lon found no copy of {_listed(missing)} from before {those} in the "
            "server's backups folder, so this server cannot move to the tested commit: keep "
            f"the build you have ({latest} keeps it current)."
        )
    found = [copy for copy in copies.values() if copy is not None]
    files = _listed([f"{path.parent.name}/{path.name}" for path in found])
    back = server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN)
    one = len(databases) == 1
    return (
        f"{head} To move it there, first put {_listed(databases)} back as "
        f"{'it was' if one else 'they were'} before {those}: press Stop on the Server tab, "
        f"restore {files} on Maintenance (it works with the server stopped), then press "
        f"{back} again without starting the server in between, since a start would apply "
        f"{those} again. Restoring loses whatever changed in {_listed(databases)} since that "
        f"copy was taken. Do not press Clean up… on Maintenance before restoring: it keeps "
        f"only the newest copies and may remove {'this one' if len(found) == 1 else 'these'}."
    )


def updates_unread(source: str, why: str) -> str:
    """The fail-closed refusal: git or the database could not say (T630)."""
    return (
        f"Yu'lon could not read which database updates {source}, so it could not tell "
        f"whether the tested commit's server can start on your databases ({why}). "
        f"{NOTHING_DONE}"
    )


def confs_from_dist(entry: CatalogEntry) -> tuple[str, ...]:
    """The module confs this entry's install writes from their `.dist` (T137), from the catalog."""
    block = entry.install.native
    if block is None or block.azerothcore is None:
        return ()
    return block.azerothcore.confs_from_dist


def dist_of(server_dir: Path, file: str) -> Path:
    """The `.dist` a conf is made from: the file beside it that the image's entrypoint copied."""
    path = server_dir / file
    return path.with_name(path.name + DIST_SUFFIX)


EVERYONE_READS = 0o444


def conf_mode(dist: Path) -> int:
    """The mode a conf made from `dist` gets: the `.dist`'s, readable by everyone.

    Never `conf.CONF_MODE`'s 0600, and not the `.dist`'s alone either (Codex,
    round 2): the world runs as the image's `acore` user (uid 1000), and a host
    user that is not uid 1000 owns what Yu'lon writes, so only an "others" read
    bit is sure to let the world open it. Safe to give because the file holds no
    secret: this family's database logins are container environment, never in
    the conf.
    """
    return stat.S_IMODE(dist.stat().st_mode) | EVERYONE_READS


def write_from_dist(server_dir: Path, file: str) -> bool:
    """Write `file` as a byte-for-byte copy of its `.dist`, if it is not there. The ONE writer.

    The install's `up` stage and the Server tab's Repair both call this, so a
    repaired install ends with the same file a fresh one does (T137). Returns
    False, and changes nothing that was there, when anything is already at
    `file`: a person's own conf is never replaced, and a resume finds its own.

    Bytes, not text: the shipped file has non-ASCII comments, and it is what the
    module's authors wrote. The mode is `conf_mode()`'s.

    **Never over a file, even one that appears mid-write (Codex, round 2).** The
    first check, before the `.dist` is even read, answers the ordinary case -- a
    conf a person has is left alone whether or not its `.dist` is still there.
    The copy is then written whole to a temporary sibling and PUBLISHED with a
    primitive that refuses an existing target, so a file that appears after that
    check is not replaced either:

    * `os.link()` of the finished temp file to the conf's name, which is atomic
      and fails with EEXIST when the name is taken. Local Linux filesystems and
      NTFS support hard links, and this is the path they take; a reader sees no
      file or the whole one.
    * Where the filesystem refuses a link (any other `OSError` -- vfat says
      EPERM; a 9p/drvfs share or a `\\wsl.localhost` path may refuse too, not
      measured), an exclusive create (`O_CREAT | O_EXCL`), which also fails on
      an existing name but is written in place: a reader at that instant could
      see part of it. The world reads this file only when it starts, and neither
      caller starts it during the write, so that is accepted; a write that fails
      part-way removes what it created, or half a conf would read as a person's
      own and never be offered again.

    The temp file is removed on every path.

    Raises:
        FileNotFoundError: there is no `.dist` to copy.
        OSError: the copy could not be written; nothing was left behind.
    """
    target = server_dir / file
    if target.exists() or target.is_symlink():
        return False
    dist = dist_of(server_dir, file)
    data = dist.read_bytes()
    mode = conf_mode(dist)
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".yulon-new", dir=target.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        try:
            os.link(tmp, target)
        except FileExistsError:
            return False
        except OSError as exc:
            logger.info(f"no hard link for {target} ({exc}); creating it exclusively instead")
            if not _create_exclusively(target, data, mode):
                return False
    finally:
        tmp.unlink(missing_ok=True)
    logger.info(f"wrote {target} from {dist.name}")
    return True


def _create_exclusively(target: Path, data: bytes, mode: int) -> bool:
    """`write_from_dist()`'s publish where hard links are refused: create-if-absent, or False.

    A failed write removes the file it created -- and only that file (Codex,
    round 3). The name can change hands between the create and the failure (the
    half file moved aside, a person's own conf put at the name), so the removal
    is by IDENTITY: the device and inode `fstat` gave the new descriptor, matched
    against a `stat` of the name that does not follow a symlink. Anything else at
    the name is left, and so is the half file if its identity was never read.

    Raises:
        OSError: the write failed; the file it had created is removed.
    """
    try:
        fd = os.open(
            target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), mode
        )
    except FileExistsError:
        return False
    created: os.stat_result | None = None
    try:
        with os.fdopen(fd, "wb") as handle:
            created = os.fstat(handle.fileno())
            handle.write(data)
            handle.flush()
            # `os.open`'s mode passed through the umask; this is the whole of it,
            # set on the descriptor so it lands on the file this call made.
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        if not hasattr(os, "fchmod"):
            # Windows has no `fchmod`, and a POSIX mode is a no-op there anyway
            # (measured, `conf._write`'s docstring); by name is the only spelling.
            os.chmod(target, mode)
    except BaseException:
        if created is not None and _is_same_file(target, created):
            target.unlink(missing_ok=True)
        raise
    return True


def _is_same_file(path: Path, created: os.stat_result) -> bool:
    """Is `path` itself (not a symlink's target) still the file `created` describes?"""
    try:
        now = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    return (now.st_dev, now.st_ino) == (created.st_dev, created.st_ino)


def conf_check(entry: CatalogEntry, server_dir: Path) -> ConfCheck:
    """Which of the entry's `confs_from_dist` are absent while their `.dist` is there. Never raises.

    A path that cannot be asked about (a permission error on the folder) is not
    offered: the press could not write there either, and the tab's reading has
    nowhere to put an exception.
    """
    missing: list[str] = []
    for file in confs_from_dist(entry):
        # Not decoration: `Path.exists()`, `is_file()` and `is_symlink()` RAISE
        # PermissionError under a folder this user cannot search, measured on
        # both CI legs (3.11 and 3.13) -- they swallow only "not there" errors.
        try:
            absent = not (server_dir / file).exists() and not (server_dir / file).is_symlink()
            if absent and dist_of(server_dir, file).is_file():
                missing.append(file)
        except OSError as exc:
            logger.warning(f"could not check {server_dir / file}: {exc}")
    return ConfCheck(missing=tuple(missing))


WRITE_FAILED = "{file} could not be written from {dist} ({exc}); nothing was changed"


def repair_confs(entry: CatalogEntry, server_dir: Path) -> ConfRepaired:
    """The Repair press: write every conf the check offers, asked again now (T137).

    A conf that appeared since the check is left as it is, and one whose `.dist`
    went is skipped: the check at the next Refresh says so by offering nothing.

    Raises:
        InstallerError: a conf could not be written; the sentence names it and why.
            Any written before it stay: each is a whole copy, and the next check
            offers only what is left.
    """
    written: list[str] = []
    for file in conf_check(entry, server_dir).missing:
        try:
            if write_from_dist(server_dir, file):
                written.append(file)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise InstallerError(
                WRITE_FAILED.format(
                    file=Path(file).name, dist=dist_of(server_dir, file).name, exc=exc
                )
            ) from exc
    return ConfRepaired(written=tuple(written))


SEEDED_WORLD_PORT = 8085
"""The realm row's port as AzerothCore's import seeds it, and the port the template's
world server listens on inside its container (`…:8085`). T552."""


class AzerothCoreInstaller(StagedInstaller):
    """Install one AzerothCore entry: clone, generate compose, build, fetch data, import, start."""

    family = "azerothcore"
    STAGE_NAMES: ClassVar[tuple[str, ...]] = (
        "clone-core",
        "clone-modules",
        "generate-compose",
        "build",
        "client-data",
        "start-db",
        "import",
        "up",
        "ready",
    )
    """Pinned by `test_wotlk_stage_names_are_the_historical_tuple`; see the module docstring."""

    PATCH_STAGE: ClassVar[str] = "patch-sources"
    LUA_STAGE: ClassVar[str] = "lua-and-sql"
    """T553's two stages, in the tuple only for an entry whose catalog block asks for them.

    Not in `STAGE_NAMES`: an entry with no patches, scripts or checks (WotLK) runs
    the historical tuple, word for word, and its state files and install log are
    what they were. An entry that has them records `patch-sources` (the patched
    tree is the evidence, and the body reads it on every press anyway);
    `lua-and-sql` is never recorded, because it runs on every press like `up`.
    """

    def stages(self) -> tuple[Stage, ...]:
        block = self._block()
        patch_stage = (Stage(self.PATCH_STAGE, self._patch_sources),) if block.patches else ()
        lua_stage = (
            (Stage(self.LUA_STAGE, self._lua_and_sql, recorded=False),)
            if block.lua_scripts or block.sql_checks
            else ()
        )
        return (
            Stage("clone-core", self._clone_core),
            Stage("clone-modules", self._clone_modules),
            *patch_stage,
            Stage("generate-compose", self.stage_generate_compose),
            Stage("build", self.stage_build, cancel_note=build_cancel_note()),
            Stage("client-data", self._client_data, cancel_note=DOWNLOAD_CANCEL_NOTE),
            Stage("start-db", self._start_db, recorded=False),
            Stage("import", self._import, cancel_note=IMPORT_STAGE_CANCEL_NOTE),
            *lua_stage,
            Stage("up", self._up, recorded=False),
            Stage("ready", self.stage_ready, recorded=False),
        )

    def _block(self) -> AzerothCoreData:
        """This entry's AzerothCore block, or an empty one (no patches, scripts or checks)."""
        native = self.entry.install.native
        if native is None or native.azerothcore is None:
            return AzerothCoreData()
        return native.azerothcore

    def stage_build(self, ctx: StageContext) -> Iterator[str]:
        """The spine's compile, with mod-ale's Playerbots names bridged for it alone (T645).

        Every press that compiles comes through here: the install's `build` stage,
        `rebuild()`'s (Rebuild, "Update the server to latest…", "Return to the tested
        pin…", a moved-in server's rebuild). `ale_playerbots.bridge()` is a no-op unless
        mod-ale and mod-playerbots disagree on a config name; whatever it rewrote is put
        back once the compile returns, fails or is stopped, so no git question outside
        the compile ever sees it. Not yielded from the `finally`: a generator closed
        mid-compile must not yield, so there the lines go to the log.
        """
        yield from ale_playerbots.bridge(ctx.server_dir)
        finished = False
        try:
            yield from super().stage_build(ctx)
            finished = True
        finally:
            said = ale_playerbots.put_back(ctx.server_dir)
            if not finished:
                for line in said:
                    logger.info(line)
        yield from said

    # -- T553: the source patches and Lua scripts this entry carries ---------

    def _patch_sources(self, ctx: StageContext) -> Iterator[str]:
        """Apply every `SourcePatch` the entry carries, after the clone and before the build.

        `CmangosInstaller._patch_sources()`'s stage, through `carried`: read on every
        press, never skipped on the record (a re-cloned checkout under a surviving
        record would otherwise compile unpatched), and a hunk already on disk is
        "already carries" and is not written again. A patch that no longer applies
        refuses with `patch.PatchError`'s own sentence, naming the file and the line,
        and nothing is written.

        One refusal of its own: a patch that would change the tree of a server
        whose build this press is going to skip. The compile would never see the
        change, so the press stops before writing it, and names the press that
        writes it AND compiles it (`before_rebuild()` lays the patches).
        """
        specs = self._block().patches
        if not specs:
            yield carried.NONE_CARRIED
            return
        patches = carried.loaded(self.entry.name, self.installers_root, specs)
        changing = carried.would_change(patches, ctx.server_dir)
        if changing is not None and self.build_would_be_skipped(ctx):
            raise InstallerError(
                f"{self.entry.name} was built before {changing.file} was carried, so this "
                "press would patch its source and not compile it, and the server would run "
                "without the change. Nothing was changed. Press "
                f"{server_build_presses.under_server_build(server_build_presses.REBUILD)}: it "
                "writes the patch and compiles it."
            )
        yield from carried.apply_lines(patches, ctx.server_dir)
        yield "Source patches are in place."

    def _lua_and_sql(self, ctx: StageContext) -> Iterator[str]:
        """Check the entry's SQL after the import, then lay its Lua scripts before the start.

        After `import` because the counts read what the import applied, and before
        `up` because the world reads its scripts only when it starts. The SQL first:
        a database missing what the scripts need is refused before anything is laid.
        """
        block = self._block()
        if block.sql_checks:
            yield from scriptdeploy.check_sql(
                block.sql_checks,
                self.entry.databases.schema_map(),
                lambda schema, statement: self._seams.sql_query(
                    self.entry.container_spec().db,
                    self._native().db.client,
                    ctx.secrets.db_password,
                    schema,
                    statement,
                ),
                self.entry.name,
            )
        yield from scriptdeploy.lay(ctx.server_dir, block.lua_scripts)

    def _carried(self, server_dir: Path, *, quiet: bool) -> Iterator[str]:
        """Write the carried patches; what an update and a rebuild need before the compile."""
        block = self._block()
        if block.patches:
            patches = carried.loaded(self.entry.name, self.installers_root, block.patches)
            yield from carried.apply_lines(patches, server_dir, quiet=quiet)

    def app_written_paths(self, server_dir: Path) -> Mapping[str, tuple[str, ...]]:
        """The spine's compose files, plus every path a carried patch edits (T553).

        The patched files are tracked and modified on every install that carries a
        patch, so the update route's dirty-tree guard must read them as this app's
        own, and `apply_carried_patches()` writes them again after the reset. Read
        out of the patch files, as the CMaNGOS family does; a patch this build does
        not ship adds nothing here and refuses where it is loaded.
        """
        found: dict[str, tuple[str, ...]] = dict(super().app_written_paths(server_dir))
        specs = self._block().patches
        if not specs:
            return found
        try:
            patches = carried.loaded(self.entry.name, self.installers_root, specs)
        except InstallerError as exc:
            logger.warning(f"could not read a carried patch to exempt the paths it edits: {exc}")
            return found
        for dest, paths in carried.written_paths(patches).items():
            found[dest] = tuple(dict.fromkeys((*found.get(dest, ()), *paths)))
        return found

    def check_carried_patches(self, server_dir: Path) -> Iterator[str]:
        """The update route's dry run: every patch still applies, every script source is there."""
        block = self._block()
        if block.patches:
            patches = carried.loaded(self.entry.name, self.installers_root, block.patches)
            yield from carried.check_lines(patches, server_dir)
        missing = scriptdeploy.missing_sources(server_dir, block.lua_scripts)
        if missing:
            raise InstallerError(
                f"The new sources have no {missing[0]}, so this server's Lua scripts could not "
                "be laid from them. Nothing was built."
            )
        linked = scriptdeploy.linked_sources(server_dir, block.lua_scripts)
        if linked:
            raise InstallerError(
                f"The new sources have {linked[0]} as a link, so this server's Lua scripts "
                "could not be laid from them. Nothing was built."
            )

    def apply_carried_patches(self, server_dir: Path) -> Iterator[str]:
        """Write the patches into moved (or put back) sources (T553).

        The update route calls this after the fetch's reset, and the restore calls
        it after putting the old commits back. The Lua scripts are not laid here
        (T562): they wait for the old world to stop (`lay_scripts()`).
        """
        yield from self._carried(server_dir, quiet=False)

    def lays_scripts_with_the_servers_down(self, server_dir: Path) -> bool:
        """Does this install have Lua scripts to lay, or lay again, or remove (T562)?

        True for an entry with `lua_scripts`, and for one that dropped them while the
        record of what an earlier press laid is still there.
        """
        return bool(self._block().lua_scripts) or scriptdeploy.record_path(server_dir).exists()

    def lay_scripts(self, server_dir: Path, *, quiet: bool) -> Iterator[str]:
        """Lay the entry's Lua scripts from the checkout as it stands now (T562).

        The world reads them when it starts, on a GM `.reload ale` and with
        `ALE.AutoReload`, so a rebuild or an update lays them with the old world
        stopped: laid during the compile they would be read by a world still running
        the old binary. A rollback calls this again from the put-back sources.
        """
        yield from scriptdeploy.lay(server_dir, self._block().lua_scripts, quiet=quiet)

    def before_rebuild(
        self, server_dir: Path, route: str, press: str = server_build_presses.REBUILD
    ) -> Iterator[str]:
        """Before any rebuild compiles: the patches on disk, and the scripts' refusals asked (T553).

        A Rebuild compiles the tree as it stands, so a checkout that lost its patch
        (re-cloned, or reset by hand) gets it back here rather than compiling
        without it; a tree that has it says nothing. Quiet: on the update route the
        same work has just run in `apply_carried_patches()`.

        The scripts are NOT laid here (T562): `lay_scripts()` lays them once the old
        world has stopped. What is asked here is whether they can be, a source that is
        gone or a link, a link where they go, so that refusal still comes before an hour
        of compiling and with nothing changed.
        """
        yield from self._carried(server_dir, quiet=True)
        scriptdeploy.check_layable(server_dir, self._block().lua_scripts)

    def check_moved_sources(
        self,
        server_dir: Path,
        moved: Sequence[tuple[EmulatorSource, Path, str]],
        *,
        to_pin: bool,
    ) -> Generator[str, None, object]:
        """Refuse a Return onto databases that hold updates the tested commit lacks (T630).

        Asked by the update route right after the move and before anything is built,
        stopped or copied; a refusal puts every source back. Only for "Return to the
        tested pin…": an update moves forward, where its own updates are what it brings.

        Live on m910q (2026-10-09): Unbound's Update to latest had applied
        mod-playerbots 037c0141's `2026_09_21_00_playerbots_speech.sql`, which drops
        `playerbots_speech`; the Return compiled 7bae1b5c for fifty minutes, and its
        world crash-looped on that missing table. The core's `db_world` updates that
        T220's import applies go the same one way.

        So: the `.sql` files the move takes away that the target no longer ships, and
        that are newer than what it ships beside them (`_updates_the_target_lacks()`);
        then, only if there are any, which of them the databases' `updates` tables hold.
        Git or a database that cannot say refuses too.
        """
        yield from ()
        if not to_pin:
            return None
        lacked = self._updates_the_target_lacks(moved)
        if not lacked:
            return None
        count = len(lacked)
        yield (
            f"The tested commit does not ship {count} database update{'' if count == 1 else 's'} "
            "the code you run has; asking the databases whether they already hold "
            f"{'it' if count == 1 else 'them'}."
        )
        # The database is put back down if this started it, on every way out: the
        # refusal says nothing was started or changed (cold review of a72e048f).
        database = self.entry.container_spec().db
        was_up = self._database_was_up(database)
        try:
            applied = self._applied_of(server_dir, lacked)
            if applied:
                backups = server_dir / snapshot.BACKUPS_FOLDER
                copies = {
                    name: snapshot.copy_from_before(backups, name, held, game=self.entry.id)
                    for name, held in applied.items()
                }
                raise InstallerError(newer_updates_refusal(applied, copies))
        except InstallerError as exc:
            if was_up is False:
                logger.info(self._stop_the_database_again(database))
            if was_up is None:
                # Re-review of a2f7ef7a: "nothing changed" would not be known to be true.
                raise InstallerError(f"{exc} {DATABASE_MAYBE_STARTED}") from exc
            raise
        except BaseException:
            if was_up is False:
                logger.info(self._stop_the_database_again(database))
            raise
        if was_up is False:
            yield self._stop_the_database_again(database)
        elif was_up is None:
            yield DATABASE_MAYBE_STARTED
        yield (
            "None of them was applied, so the tested commit's server can start on your "
            "databases."
        )
        return None

    def _database_was_up(self, container: str) -> bool | None:
        """Whether the database container ran before the check asked it; None = unknown."""
        try:
            return self._seams.ask_db_running(container)
        except Exception as exc:  # noqa: BLE001 - any seam failure is one answer here
            logger.warning(f"could not tell whether {container} is running: {exc}")
            return None

    def _updates_the_target_lacks(
        self, moved: Sequence[tuple[EmulatorSource, Path, str]]
    ) -> tuple[str, ...]:
        """Base names of the update files going back removes and the target does not ship.

        Per moved source, git's answer about `SQL_FOLDER` between the commit it was on
        and the one it stands on now, and the target commit's own file list (`git
        ls-tree`, never the disk: an untracked or sparse-checkout file is not shipped).
        A removed file still counts out when:

        * the target ships the same name for the same database (`_database_part()`:
          an update upstream moved to its archive), never one for another database --
          `db_characters/2026_09_21_00.sql` is not `db_world/2026_09_21_00.sql`; or
        * it and a file the target ships in the same folder are both dated
          (`YYYY_MM_DD_NN`, how AzerothCore and mod-playerbots name their updates) and
          the target's sorts at or after it: it is older than what the target has,
          which a Return that moves forward (T588) over a squash removes. A name that
          is not dated says nothing about order (cold review of a72e048f).
        """
        lacked: dict[str, None] = {}
        for source, dest, old in moved:
            new = self._seams.head_sha(dest)
            if new is None:
                raise InstallerError(
                    updates_unread(
                        f"the move takes away in {source.repo}", "git did not say its commit"
                    )
                )
            if new == old:
                continue
            pairs = self._seams.changed_files(dest, old, new, (SQL_FOLDER,))
            if pairs is None:
                raise InstallerError(
                    updates_unread(
                        f"the move takes away in {source.repo}",
                        f"git could not compare {old[:7]} with {new[:7]}",
                    )
                )
            removed = [
                path for status, path in pairs if status.startswith("D") and path.endswith(".sql")
            ]
            if not removed:
                continue
            tracked = self._seams.tree_files(dest, new, (SQL_FOLDER,))
            if tracked is None:
                raise InstallerError(
                    updates_unread(
                        f"the move takes away in {source.repo}",
                        f"git could not list the SQL files {new[:7]} ships",
                    )
                )
            shipped = {
                (_database_part(path), posixpath.basename(path))
                for path in tracked
                if path.endswith(".sql")
            }
            beside: dict[str, list[str]] = {}
            for path in tracked:
                if path.endswith(".sql"):
                    beside.setdefault(posixpath.dirname(path), []).append(posixpath.basename(path))
            left: list[str] = []
            for path in removed:
                name = posixpath.basename(path)
                if (_database_part(path), name) in shipped:
                    continue
                if _DATED.match(name) and any(
                    _DATED.match(other) and other >= name
                    for other in beside.get(posixpath.dirname(path), ())
                ):
                    continue
                left.append(path)
            added = [
                path for status, path in pairs if status.startswith("A") and path.endswith(".sql")
            ]
            refiled = self._refiled(source.repo, dest, old, new, left, added) if left else set()
            for path in left:
                if path not in refiled:
                    lacked[posixpath.basename(path)] = None
        return tuple(lacked)

    def _refiled(
        self,
        repo: str,
        dest: Path,
        old: str,
        new: str,
        removed: Sequence[str],
        added: Sequence[str],
    ) -> set[str]:
        """The removed update files the move only re-filed under a new name (re-review, a2f7ef7a).

        AzerothCore's routine squash moves `updates/pending_db_world/rev_*.sql` into a
        dated `updates/db_world/` file, with a `-- DB update A -> B` header in front:
        never dated, never the same name, and a forward Return (T588) over it took the
        applied `rev_` row as an update the target lacks. So a removed file whose SQL --
        its lines less blank ones and `--` comments -- is that of a file the move ADDED
        for the same database is the same update, re-filed. Read in one git run per
        side (`Seams.file_lines`); git that cannot say refuses.
        """
        wanted = {_database_part(path) for path in removed}
        beside = [path for path in added if _database_part(path) in wanted]
        if not beside:
            return set()
        before = self._seams.file_lines(dest, old, removed)
        after = self._seams.file_lines(dest, new, beside)
        if before is None or after is None:
            raise InstallerError(
                updates_unread(
                    f"the move takes away in {repo}",
                    "git could not read the update files it removes and adds",
                )
            )
        # Each added file re-files ONE removed file (re-review of 2db10bd9): two removed
        # updates with the same SQL and one added file leave one of them lacked.
        filed = [(_database_part(path), _sql_of(after.get(path, ()))) for path in beside]
        refiled: set[str] = set()
        for path in removed:
            key = (_database_part(path), _sql_of(before.get(path, ())))
            if not key[1] or key not in filed:
                continue
            filed.remove(key)
            refiled.add(path)
        return refiled

    def _applied_of(self, server_dir: Path, names: Sequence[str]) -> dict[str, tuple[str, ...]]:
        """Per database, which of `names` its `updates` table holds; only those holding any.

        The database is brought up alone first (a stopped server has it down), never the
        world: a start after a restore would apply the very updates again.
        """
        spec = self.entry.container_spec()
        try:
            self._seams.start_db(spec, server_dir, because="nothing was built or changed")
        except docker.DockerCommandError as exc:
            raise InstallerError(
                updates_unread(
                    "your databases already have",
                    f"Yu'lon could not start the database to " f"ask it: {exc}",
                )
            ) from exc
        password = self.resolve_secrets(server_dir).db_password
        client = self._native().db.client
        quoted = ", ".join("'" + n.replace("\\", "\\\\").replace("'", "''") + "'" for n in names)
        applied: dict[str, tuple[str, ...]] = {}
        for database in self.snapshot_databases():
            try:
                rows = self._seams.sql_query(
                    spec.db, client, password, database, f"{APPLIED_UPDATES_QUESTION}({quoted})"
                )
            except docker.DockerCommandError as exc:
                raise InstallerError(
                    updates_unread(
                        "your databases already have",
                        f"Yu'lon could not ask {database} which of them it already has: {exc}",
                    )
                ) from exc
            held = {line.strip() for line in rows.splitlines() if line.strip()}
            found = tuple(name for name in names if name in held)
            if found:
                applied[database] = found
        return applied

    def repair_database_stages(self) -> tuple[Stage, ...]:
        """The spine's four, after the client-data download (T377).

        The server data lives in its own volume, and a prune that removed the
        database's volume removed that one too (live, yulon-win11 2026-10-05: the
        hand ran `ac-client-data-init` as well as the import). The download
        checks what the volume holds and ends in seconds when it is all there.
        """
        return (
            replace(self.stage_named("client-data"), recorded=False),
        ) + super().repair_database_stages()

    def databases_a_new_build_changes(self) -> tuple[Db, ...]:
        """Every database the update changes before and at the new build's first start (T217, T220).

        * playerbots (T217): `AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES=1` is
          structural (`composegen.DEFAULT_WORLD_ENV`), so the playerbots updater
          runs on every start whatever `Updates.EnableDatabases` says, and it reads
          the module's `data/sql` from the HOST folder the worldserver bind-mounts
          (`override.yml.tmpl`), not from the image. A player's update of
          2026-10-03 applied mod-playerbots' "remove obsolete tables" update on the
          new build's first start, and the old build then crash-looped on the
          missing table.
        * auth, characters and world (T220): the worldserver's own updater is off
          (`Updates.EnableDatabases` 0), so the update applies the new core's
          `data/sql/updates/db_*` itself, through the import one-shot, with the
          servers stopped (`servers_down_work()`). A rollback must undo those too,
          so the copy is taken before they run.
        """
        return ("auth", "characters", "world", "playerbots")

    def servers_down_work(
        self, server_dir: Path, changes: object, *, press: str
    ) -> ServersDownWork | None:
        """The new core's own database updates, applied before its first start (T220).

        AzerothCore's world server runs here with `Updates.EnableDatabases` 0, and
        a start never runs the import one-shot (`docker.start_staged()` names the
        long-running services), so until T220 an update to a core that adds a table
        it reads at boot never came up: f19a187 renames the DBC override table to
        `emotestextsound_dbc` and creates it only in
        `data/sql/updates/db_world/2026_09_21_05.sql`, and the new world server
        aborted on `ER_NO_SUCH_TABLE` -- after the playerbots updater had already
        migrated its database (T217's player).

        So `forward()` runs the same one-shot the install's `import` stage runs, on
        the NEW image (the tags name it by then), with the servers stopped and the
        database up, AFTER the update's copy of the databases (the route takes it
        first): upstream's updater applies only what its `updates` table does not
        hold yet, and a rollback puts the copy back over whatever it applied. A
        rollback needs nothing of its own here, so `back()` does nothing. Not
        cancellable part-way: a Stop is honoured once the updates are in, so the
        copy is never put back under an import that is still writing.
        """
        service = self.entry.containers.db_import
        if not service:
            return None

        def forward(ctx: StageContext) -> Iterator[str]:
            yield (
                f"Applying the new build's own database updates ({service}) with the servers "
                "stopped, before it first starts."
            )
            run = yield from self._pump(
                lambda sink: self._seams.one_shot(service, ctx.server_dir, sink=sink, cancel=None),
                cancel=None,
                stage="import",
            )
            # `cancel=None`, then the press by name: `_check_run()`'s own check
            # says "the install was stopped", and this is an update (cold review).
            self._check_run(run, "Applying the new build's database updates", None, "")
            if ctx.cancel is not None and ctx.cancel.is_set():
                raise InstallStopped(f"{press} was stopped. {CORE_UPDATES_NOTE}")
            yield "The new build's database updates are in."

        return ServersDownWork(
            prepare=lambda: iter(()),
            forward=forward,
            back=lambda ctx: iter(()),
            finishes_start_refusal=False,
        )

    def _clone_core(self, ctx: StageContext) -> Iterator[str]:
        """Clone the emulator itself INTO the server dir — it is the checkout.

        Disk evidence beats the state file in BOTH directions, which is
        `StagedInstaller.already_cloned()`'s rule: recorded and on disk is a
        finished clone and is left exactly as it is — no fetch, no reset,
        nothing moved; recorded but gone, or on disk with nothing recorded, is
        the repair case and clones. A `.git` pointing somewhere else is refused
        BY NAME and never deleted, because a directory holding somebody's fork
        is not this installer's to remove.
        """
        cores = [source for source in self.entry.emulator.sources if source.dest == "."]
        if len(cores) != 1:
            raise InstallerError(
                f'{self.entry.name} must name exactly one source with dest "." (the core '
                f"checkout); it names {len(cores)}. That is a bug in the catalog."
            )
        source = cores[0]
        server_dir = ctx.server_dir
        has_git = (server_dir / ".git").is_dir()
        existing = self._remote_of(server_dir)
        # A checkout whose origin cannot be read is not an empty directory and
        # not ours either, and it is refused rather than cloned over because the
        # clone seam DELETES a destination it does not recognise. That refusal
        # is `refuse_unowned_checkout()`'s own, below: it used to be copied into
        # this body and two others, which left the method whose docstring calls
        # itself "the one path in this engine that could still destroy a user's
        # work" unable to protect itself (review, 2026-08-31).
        if existing is not None and not git.same_repo(existing, source.url):
            raise InstallerError(
                f"{server_dir} is already a git checkout of {existing}, not of {source.url}. "
                "Nothing was changed. Install into an empty folder instead."
            )
        if not has_git and server_dir.is_dir():
            # Doubled with `_guard()` on purpose, and for the reason
            # `repair.reset_unfinished()` doubles its own check: the clone seam
            # deletes a non-git destination before cloning, and the guard that
            # protects a user's files should still be there after somebody
            # reorders the stages.
            leftovers = _listing(server_dir, ignoring=OUR_OWN_FILES)
            if leftovers:
                raise InstallerError(
                    f"{server_dir} has files in it but is not a checkout of {source.url}, so it "
                    "was left alone. Pick an empty folder."
                )
        if self.already_cloned(ctx, "clone-core", existing):
            yield f"{source.repo} is already cloned in {server_dir}; leaving it exactly as it is."
            return
        self.refuse_unowned_checkout(ctx, server_dir, source.url, existing)
        yield f"Cloning {source.repo} into {server_dir} (this is a large repository)"
        if existing is not None:
            yield "A previous run of this install left it part-way through; finishing it off."
        yield from self._clone_lines(
            git.CloneSpec(
                url=source.url,
                dest=server_dir,
                branch=source.branch,
                sparse_path=source.sparse_path,
                # Data, not a constant: the core repo says `null` in
                # catalog.json because its CMake reads the revision out of git
                # history and a shallow clone hands the build the wrong answer.
                depth=source.depth,
                rev=source.rev,
            ),
            "clone-core",
        )
        yield f"{source.repo} is in place."

    def _clone_modules(self, ctx: StageContext) -> Iterator[str]:
        """Clone every other source at its `dest` under `modules/`, which is what the build mounts.

        Guarded exactly like `_clone_core()`, and for a reason this loop once
        did not have: the clone seam `shutil.rmtree`s a destination it does
        not recognise, and `_remote_of()` answers `None` for a directory with no
        `.git`. A `modules/mod-playerbots` a user had put there by hand — a
        tarball, a copied tree, a checkout without its `.git` — fell straight
        through the only check here and was deleted (review, 2026-08-23). One
        engine that refuses to touch what it does not own must do it at every
        level, not just the top one.
        """
        sources = [source for source in self.entry.emulator.sources if source.dest != "."]
        if not sources:
            yield "This server has no extra modules to clone."
            return
        for source in sources:
            dest = ctx.server_dir / source.dest
            has_git = (dest / ".git").is_dir()
            existing = self._remote_of(dest)
            if existing is not None and not git.same_repo(existing, source.url):
                raise InstallerError(
                    f"{dest} is a checkout of {existing}, not of {source.url}. Nothing was changed."
                )
            if not has_git and dest.is_dir() and _listing(dest):
                raise InstallerError(
                    f"{dest} has files in it but is not a checkout of {source.url}, so it was "
                    "left alone. Move that folder aside and try again."
                )
            if self.already_cloned(ctx, "clone-modules", existing):
                yield f"{source.repo} is already in {source.dest}; leaving it exactly as it is."
                continue
            self.refuse_unowned_checkout(ctx, dest, source.url, existing)
            yield f"Cloning {source.repo} into {source.dest}"
            if existing is not None:
                yield "A previous run of this install left it part-way through; finishing it off."
            yield from self._clone_lines(
                git.CloneSpec(
                    url=source.url,
                    dest=dest,
                    branch=source.branch,
                    sparse_path=source.sparse_path,
                    depth=source.depth,
                    rev=source.rev,
                ),
                "clone-modules",
            )
        yield "Modules are in place."

    def _client_data(self, ctx: StageContext) -> Iterator[str]:
        """Fetch the server-side map/DBC data into its volume.

        Run every time rather than skipped on the state file, and that IS the
        disk-evidence rule rather than an exception to it: the evidence lives
        inside a Docker volume, and the generated entrypoint asks it directly —
        it compares the installed `data-version` with upstream's own and exits 0
        in seconds when they match. Re-running is the check.

        This is server data (maps, vmaps, DBC), not a game client: the app
        never ships or fetches the latter (README §3a).
        """
        service = self.entry.containers.client_data
        if not service:
            yield "This server has no separate client-data step."
            return
        # T539 (re-review of 7312223b): a download a Stop or a closed Yu'lon left running
        # is ended before another starts into the same data.
        left = self._seams.end_one_shot(service, ctx.server_dir)
        if left is not None:
            raise OneShotLeftRunning(download_left_sentence(left, earlier=True))
        yield f"Fetching server data ({service}). The download resumes if it is interrupted."
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(
                service, ctx.server_dir, sink=sink, cancel=ctx.cancel
            ),
            cancel=ctx.cancel,
            stage="import",
        )
        if ctx.cancel is not None and ctx.cancel.is_set():
            # Not read as a clean Stop until nothing of the download is still running.
            left = self._seams.end_one_shot(service, ctx.server_dir)
            if left is not None:
                raise OneShotLeftRunning(download_left_sentence(left, earlier=False))
        self._check_run(run, "the server-data download", ctx.cancel, DOWNLOAD_CANCEL_NOTE)
        yield "Server data is in place."

    def _up(self, ctx: StageContext) -> Iterator[str]:
        """Write the catalog's module confs from their `.dist`, then the spine's `up` (T137).

        HERE, before the start, because the world reads its confs only when it
        starts, and not earlier because the `.dist` is not on disk until the
        `import` stage's one-shot has run the image's entrypoint (`cp -rnv
        /azerothcore/env/ref/etc/* "$CONF_DIR"`, captured in T134's install
        log). `dml-start.sh` copied `playerbots.conf` at the same point, before
        `compose up` (`pyplan/phase7-decisions.md`). Inside `up` rather than a
        stage of its own: `STAGE_NAMES` is pinned for the state files already on
        disk, and `up` runs on every resume, which a copy that never overwrites
        can afford.

        Only the install runs `up`: a rebuild has its own `recreate`, so an
        existing install gets the file through Repair server files, when its
        owner chooses (the owner's decision, 2026-09-27).
        """
        yield from self._confs_from_dist(ctx)
        yield from self._realm_port(ctx)
        yield from self.stage_up(ctx)

    def _realm_port(self, ctx: StageContext) -> Iterator[str]:
        """Give the realm row this server's own world port, before the first start (T552).

        The import seeds AzerothCore's 8085, which is WotLK's published port.
        A second AzerothCore server publishes another one, and the authserver
        both hands clients the row's port and prints it once, when it starts,
        in the line the ready wait reads (`ready.auth`). So the row is set
        here, before `up`, and a row that cannot be set stops the install:
        left at 8085 it would send this server's players to WotLK's world.
        Nothing is sent for an entry on 8085, so a WotLK install does exactly
        what it did before.
        """
        port = self.entry.ports.world
        if port == SEEDED_WORLD_PORT:
            return
        failed = self._run_auth_statement(networking.realm_port_sql(self.entry), ctx)
        if not failed:
            failed = self._realm_port_reads(port, ctx)
        if failed:
            raise InstallerError(
                f"The realm could not be given this server's world port {port} ({failed}), so "
                "the server was not started: its players would be sent to another server's "
                "world. Press Install again to retry."
            )
        yield f"The realm hands players this server's world port, {port}."

    def _realm_port_reads(self, port: int, ctx: StageContext) -> str:
        """`""` when the realm row reads back `port`, else what it read (T552).

        An UPDATE that matched no row exits 0 like one that changed it, so the
        row is read back rather than the exit code trusted (Codex review).
        """
        try:
            answer = self._seams.sql_query(
                self.entry.container_spec().db,
                self._native().db.client,
                ctx.secrets.db_password,
                None,
                networking.realm_port_query(self.entry),
            )
        except docker.DockerCommandError as exc:
            return f"its row could not be read back: {exc}"
        said = answer.split()
        if said == [str(port)]:
            return ""
        return f"its row reads {' '.join(said) or 'nothing'}"

    def _confs_from_dist(self, ctx: StageContext) -> Iterator[str]:
        """One line per conf: written, already there, or why not. Never fails the install.

        A conf that could not be written is a sentence, not a failed install,
        for `_advertise_realm()`'s reason: the server runs without it, on the
        module's compiled defaults, exactly as every WotLK install did before
        T137 -- and the Server tab's Repair offers it again.
        """
        for file in confs_from_dist(self.entry):
            name = Path(file).name
            dist = dist_of(ctx.server_dir, file).name
            try:
                wrote = write_from_dist(ctx.server_dir, file)
            except FileNotFoundError:
                yield (
                    f"There is no {dist} yet, so {name} was not written; the bots use their "
                    "built-in settings. Repair server files on the Server tab offers it once "
                    f"{dist} is there."
                )
                continue
            except OSError as exc:
                yield (
                    f"{name} could not be written from {dist} ({exc}); the bots use their "
                    "built-in settings. Repair server files on the Server tab offers it again."
                )
                continue
            if wrote:
                yield f"{name} was written from {dist}, so the bots read their settings from it."
            else:
                yield f"{name} is already there; left as it is."

    def _start_db(self, ctx: StageContext) -> Iterator[str]:
        """The spine's start-db, short-circuited for an entry with no import service.

        The spine's `stage_start_db()` is unconditional (A7) because CMaNGOS
        has no `db_import` and still needs the database; AzerothCore keeps the
        6.2 wording for an entry that has no database step at all.
        """
        if not self.entry.containers.db_import:
            yield "This server has no database step, so nothing needs the database yet."
            return
        yield from self.stage_start_db(ctx)

    def _import(self, ctx: StageContext) -> Iterator[str]:
        """The spine's import stage, gated by the injected `acore_*` probe pair.

        `_probe` may be absent only for an entry with no `db_import` service —
        preflight refuses the other combination — so "no probe" and "no
        service" are the same skip, worded here and not in the spine (A7).
        """
        service = self.entry.containers.db_import
        if self._probe is None or not service:
            yield "This server has no separate database import step."
            return
        yield from self.stage_import(ctx, CallableGate(self._probe, self._reset), service)


def _sql_of(lines: Sequence[str]) -> tuple[str, ...]:
    """An update file's SQL: its lines less blank ones and `--` comments, right-stripped."""
    return tuple(
        line.rstrip() for line in lines if line.strip() and not line.lstrip().startswith("--")
    )


def _database_part(path: str) -> tuple[str, ...]:
    """Which database an update file under `SQL_FOLDER` is for, as its folders name it (T630).

    The folders between `data/sql` and the file, less those that only sort updates
    (`updates`, `archive`, `base`, `custom`, `pending`) and version or year folders
    (`6.x`, `2026`); `pending_db_world` and `db-world` read as `db_world`. So the
    core's `updates/db_world/` and `archive/db_world/6.x/` are one database, and
    mod-playerbots' `playerbots/updates/` and `playerbots/archive/2026/` another.
    """
    parts = posixpath.dirname(path).split("/")
    if parts[:2] == SQL_FOLDER.split("/"):
        parts = parts[2:]
    named = []
    for part in parts:
        if not part or part in _SORTING_FOLDERS or part[0].isdigit():
            continue
        part = part.removeprefix("pending_").replace("-", "_")
        named.append(part)
    return tuple(named)
