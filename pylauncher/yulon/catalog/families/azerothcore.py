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
import queue
import re
import stat
import tempfile
import threading
from collections.abc import Callable, Generator, Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, cast

from yulon import (
    ale_playerbots,
    city_bots_guard,
    docker,
    git,
    networking,
    party,
    platform,
    server_build_presses,
)
from yulon.catalog import composegen, snapshot
from yulon.catalog.catalog import AcClientExtract, AzerothCoreData, CatalogEntry, EmulatorSource
from yulon.catalog.families import carried, extract, scriptdeploy, surgical
from yulon.catalog.families.direction import moves_forward
from yulon.catalog.installer import (
    ImportWindowStopped,
    InstallerError,
    InstallStopped,
    OneShotLeftRunning,
    ReadyWaitStopped,
)
from yulon.catalog.native import (
    DOWNLOAD_CANCEL_NOTE,
    IMPORT_STAGE_CANCEL_NOTE,
    OUR_OWN_FILES,
    CallableGate,
    ConfCheck,
    ConfRepaired,
    Seams,
    ServersDownWork,
    Stage,
    StageContext,
    StagedInstaller,
    _listed,
    _listing,
    _put_all,
    build_cancel_note,
    download_left_sentence,
    stop_abandoned_worker,
)
from yulon.log import get_logger
from yulon.manifest import Db
from yulon.server_build_presses import MAINTENANCE_TAB

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


_TLS_CERTIFICATE_EXIT = frozenset({58, 77})
"""curl's LOCAL-certificate exit numbers, as `curl --manual` documents them (T810).

58 problem with the local certificate, 77 problem reading the SSL CA cert
file. The reporter's 77 — its own image's ca-certificates.crt missing,
unreadable or corrupt — is the case: material inside the image, which a retry
on the same image can never fix. 35 (SSL connect error), 51 and 60 (peer
certificate not verified / not known) are NOT here: they can come from a
remote certificate, a proxy or flaky routing, so they are retried like any
other error and carry no advice to remove the image. The numbers are read ONLY
from the `curl: (NN)` line curl printed, never from the container's exit
status: that status can be another program's (unzip's own 51 is a truncated
zip, not a certificate).
"""

_CURL_CERTIFICATE_FAILURE = re.compile(
    r"curl: \((" + "|".join(str(code) for code in sorted(_TLS_CERTIFICATE_EXIT)) + r")\)"
)
"""A curl error line carrying a certificate/TLS exit number (T810).

Matched against the run's own lines after `docker.unprefixed()` strips
compose's `<service>  | `. The NUMBER is the signal, never the words beside
it: `curl: (56) OpenSSL SSL_read: Connection reset by peer` carries `SSL`
but is a reset link, not a bad image, and must stay a generic failure.
"""


def client_data_certificate_failure(run: docker.AttachedRun) -> bool:
    """Is this client-data run a certificate/TLS failure that retrying cannot fix (T810)?

    Only for a FAILED run: a download that printed `curl: (35)` then exited 0
    recovered, and must not be read as a failure. Past that gate only the number
    in the tail's own `curl: (N)` line counts, never the words around it and never
    the container's exit status (unzip's own 51 is a truncated zip). A family
    helper, not a spine rule: only the AzerothCore client-data fetch downloads
    with curl.
    """
    if run.returncode == 0:
        return False
    return any(_CURL_CERTIFICATE_FAILURE.search(docker.unprefixed(line)) for line in run.tail)


def stale_client_data_remedy(entry: CatalogEntry, server_dir: Path) -> str:
    """What to tell the player about a possibly bad client-data image (T810).

    A 77 survives every retry because the image
    `{{IMAGE_PREFIX}}client-data:{{IMAGE_TAG}}` was built on this machine from
    the checkout's Dockerfile and may hold a missing or corrupt
    ca-certificates.crt. Removing the image (not the volume — the map data was
    never written) is the step; it is NOT promised to cure it, because Docker's
    build cache can remake the image from the same cached layers (a known
    limit: no cache-bypassing build path exists here), hence the build-cache
    clause. The references are the same strings `built_image_refs()` is made of,
    so the name cannot drift from what the build produces; only the
    `client-data` one is named, never the world the player keeps.
    """
    refs = composegen.built_image_refs(entry, server_dir)
    image = next(
        (ref for ref in refs if ref.split(":")[0].endswith("client-data")),
        "the client-data image",
    )
    return (
        f"The locally built client-data image named {image} may hold a bad certificate "
        "file. Remove it from Docker's Images list (Docker Desktop shows it there), and if "
        "Docker offers to clear its build cache, do so, then press Install again."
    )


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
        f"restore {files} on the {MAINTENANCE_TAB} tab (it works with the server stopped), then "
        f"press {back} again without starting the server in between, since a start would apply "
        f"{those} again. Restoring loses whatever changed in {_listed(databases)} since that "
        f"copy was taken. Do not press Clean up… on the {MAINTENANCE_TAB} tab before restoring: "
        f"it keeps only the newest copies and may remove "
        f"{'this one' if len(found) == 1 else 'these'}."
    )


@dataclass
class _Direction:
    """Whether a move goes forward, asked at most once and only when a file needs it."""

    seams: Seams
    repo: str
    dest: Path
    old: str
    new: str
    _asked: bool = False
    _said: bool | None = None
    _why: str = ""

    def peek(self) -> bool | None:
        """True / False when git or GitHub can say; None when neither can (T767). Never refuses."""
        if not self._asked:
            self._said, self._why = moves_forward(
                self.seams, self.repo, self.dest, self.old, self.new
            )
            self._asked = True
        return self._said

    def known(self) -> bool:
        """True when forward; refuses (fail closed) when neither git nor GitHub can say."""
        said = self.peek()
        if said is None:
            raise InstallerError(
                updates_unread(
                    f"the move takes away in {self.repo}",
                    f"{self._why}, so the direction of the move is unknown",
                )
            )
        return said


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


def _ignore_answer(_answer: object) -> None:
    """`FnSql.run` is for statements whose answer nobody reads."""


DOCKERIGNORE = ".dockerignore"
DOCKERIGNORE_DATA_BLOCK: tuple[str, ...] = (
    "# Yu'lon: `data/` holds the client data this install extracts (maps, vmaps,",
    "# mmaps and the DBC files). It is several gigabytes after install and no",
    "# build stage reads it, so it is left out of the build context; `data/sql`",
    "# is the one folder the db-import image copies.",
    "data/*",
    "!data/sql",
)
"""The generated build context leaves the extracted game data out (T812 D9).

The core checkout IS the build context and ships a `.dockerignore` with no
`data/` line. After extraction that folder holds 6.6 GB (live proof,
yulon-ubuntu2 2026-10-28), so every Rebuild uploaded it: BuildKit logged
`transferring context: 2.02GB` and the build-context fingerprint gave no answer
(over its 4 GiB cap), while the install's disk filled. `data/*` excludes the
extracted folders and `!data/sql` keeps the base SQL and update files the
db-import image's own `COPY data data` needs; nothing else in `data/` is read by
a built stage (`apps/docker/Dockerfile` copies `apps`, `conf`, `deps`, `modules`,
`src` and `data`).
"""

DOCKERIGNORE_DATA_MARKER = "`data/` holds the client data this install extracts"
"""Its presence in `.dockerignore` means the block is already there (idempotent)."""


def exclude_extracted_data(server_dir: Path) -> bool:
    """Append `DOCKERIGNORE_DATA_BLOCK` to the checkout's `.dockerignore`; True if written.

    Appended, never replaced: the file is the core's own and its lines decide
    what else a build sends. Idempotent by the marker line, because a repair and
    every update's put-back run `stage_generate_compose()` again. Raises
    `OSError`/`UnicodeDecodeError` to the stage, which words it.
    """
    path = server_dir / DOCKERIGNORE
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    if DOCKERIGNORE_DATA_MARKER in text:
        return False
    separator = "" if not text or text.endswith("\n") else "\n"
    block = "\n".join((*DOCKERIGNORE_DATA_BLOCK, ""))
    path.write_text(f"{text}{separator}{block}", encoding="utf-8")
    return True


class AzerothCoreInstaller(StagedInstaller):
    """Install one AzerothCore entry: clone, generate compose, build, fetch data, import, start."""

    family = "azerothcore"
    STAGE_NAMES: ClassVar[tuple[str, ...]] = (
        "clone-core",
        "clone-modules",
        "db-password",
        "generate-compose",
        "build",
        "client-data",
        "start-db",
        "import",
        "up",
        "ready",
    )
    """Pinned by `test_wotlk_stage_names_are_the_historical_tuple`; see the module docstring.

    `db-password` (T748) is never recorded, so no state file names it and an older build reading
    a newer folder's record meets nothing it does not know."""

    PATCH_STAGE: ClassVar[str] = "patch-sources"
    LUA_STAGE: ClassVar[str] = "lua-and-sql"
    """T553's two stages, in the tuple only for an entry whose catalog block asks for them.

    Not in `STAGE_NAMES`: an entry with no patches, scripts or checks (WotLK) runs
    the historical tuple, word for word, and its state files and install log are
    what they were. An entry that has them records `patch-sources` (the patched
    tree is the evidence, and the body reads it on every press anyway);
    `lua-and-sql` is never recorded, because it runs on every press like `up`.
    """

    WORLD_BOOTSTRAP_STAGE: ClassVar[str] = "world-bootstrap"
    """T812 C5: the CoA world's base data, loaded after `start-db`, before `import`.

    The base data of `acore_world` is not an import of SQL updates but a
    versioned baseline (`apps/coa-world/world_data.py` in the core checkout),
    run by the compose one-shot the entry's `world_bootstrap_service` names.
    Not in `STAGE_NAMES`: an entry without that service runs the historical
    tuple, word for word, exactly as a patch-less entry skips `patch-sources`.
    Not recorded: a resume must always run it again (cheap when the world is
    already loaded: the service exits 0 at its done-marker) — the same reason
    `client-data` re-asks its volume.
    """

    WORLD_BOOTSTRAP_DONE_TABLE: ClassVar[str] = "version"
    """The table that script loads LAST, so its presence means the load finished.

    `world_data.py` loads `updates`, `updates_include` and `version` last, and the
    service's own skip rule reads `version` (`base.yml.tmpl`); the import gate
    asks the same table when a partial database is only that loaded world
    (`install_wiring.import_gate_for`).
    """

    EXTRACT_STAGE: ClassVar[str] = "extract-maps"
    MMAPS_STAGE: ClassVar[str] = "mmaps"
    """T812 C4a's two stages, in the tuple only for an entry with `client_extract`.

    Not in `STAGE_NAMES` for the same reason as the T553 stages: an entry
    without one (WotLK, Unbound) runs the historical tuple word for word.
    """

    def stages(self) -> tuple[Stage, ...]:
        block = self._block()
        patch_stage = (Stage(self.PATCH_STAGE, self._patch_sources),) if block.patches else ()
        lua_stage = (
            (Stage(self.LUA_STAGE, self._lua_and_sql, recorded=False),)
            if block.lua_scripts or block.sql_checks
            else ()
        )
        bootstrap_stage = (
            (
                Stage(
                    self.WORLD_BOOTSTRAP_STAGE,
                    self._world_bootstrap,
                    recorded=False,
                    cancel_note=IMPORT_STAGE_CANCEL_NOTE,
                ),
            )
            if block.world_bootstrap_service
            else ()
        )
        extract_stage = (
            (
                Stage(self.EXTRACT_STAGE, self._extract_maps),
                Stage(self.MMAPS_STAGE, self._mmaps, recorded=False),
            )
            if block.client_extract is not None
            else ()
        )
        return (
            Stage("clone-core", self._clone_core),
            Stage("clone-modules", self._clone_modules),
            *patch_stage,
            # T748: never recorded, and before `generate-compose` writes `.env` and before the
            # database is first started, so the password exists on disk before anything uses
            # it. After the clones, which need an empty folder or their own checkout.
            Stage("db-password", self._db_password, recorded=False),
            Stage("generate-compose", self.stage_generate_compose),
            Stage("build", self.stage_build, cancel_note=build_cancel_note()),
            Stage("client-data", self._client_data, cancel_note=DOWNLOAD_CANCEL_NOTE),
            *extract_stage,
            Stage("start-db", self._start_db, recorded=False),
            *bootstrap_stage,
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

    def stage_generate_compose(self, ctx: StageContext) -> Iterator[str]:
        """The spine's three compose files, plus leaving extracted data out of the build context.

        Only an entry that extracts client data into `data/` has anything to
        leave out (CoA's `client_extract`, T812 C4a); every other entry runs
        the spine's body and returns. Runs here rather than in a stage of its
        own because a Repair and an update's put-back already rewrite the
        generated files through this method, and the ignore belongs with them.
        """
        yield from super().stage_generate_compose(ctx)
        if self._block().client_extract is None:
            return
        try:
            written = exclude_extracted_data(ctx.server_dir)
        except (OSError, UnicodeDecodeError) as exc:
            raise InstallerError(
                f"The build context's {DOCKERIGNORE} could not be written in {ctx.server_dir} "
                f"({exc}), so the next build would upload the extracted game data. Nothing else "
                "was changed."
            ) from exc
        if written:
            yield "The extracted game data is left out of the build context."

    def stage_build(self, ctx: StageContext) -> Iterator[str]:
        """The spine's compile, with mod-ale's Playerbots names bridged for it alone (T645).

        Every press that compiles comes through here: the install's `build` stage,
        `rebuild()`'s (Rebuild, "Update the server to latest…", "Return to the tested
        pin…", a moved-in server's rebuild). `ale_playerbots.bridge()` is a no-op unless
        mod-ale and mod-playerbots disagree on a config name; whatever it rewrote is put
        back once the compile returns, fails or is stopped, so no git question outside
        the compile ever sees it. Not yielded from the `finally`: a generator closed
        mid-compile must not yield, so there the lines go to the log.

        The bridge runs INSIDE the `try` and returns its lines only once every write is
        done (cold review of db5da4df): a press closed at one of those lines (GeneratorExit)
        still reaches the put-back.

        Also before the update route's dirty-tree guard: `_refuse_unless_updatable()`.
        """
        finished = False
        try:
            yield from ale_playerbots.bridge(ctx.server_dir)
            yield from super().stage_build(ctx)
            finished = True
        finally:
            said = ale_playerbots.put_back(ctx.server_dir)
            if not finished:
                for line in said:
                    logger.info(line)
        yield from said

    def _refuse_unless_updatable(
        self, server_dir: Path, moving: Sequence[EmulatorSource]
    ) -> tuple[tuple[EmulatorSource, Path, str], ...]:
        """The spine's source refusals, after putting back a mod-ale bridge left on disk (T645).

        On Unbound mod-ale is a source this route moves, and a compile Yu'lon never saw
        finish leaves the bridged file behind: the dirty-tree guard would refuse it as the
        player's own change, before any compile could clean it up. Called inside the
        press's reservation (T568), so no other Yu'lon's compile is under way; the
        put-back touches only a file whose bytes are still the ones the bridge wrote.
        """
        for line in ale_playerbots.put_back(server_dir):
            logger.info(line)
        return super()._refuse_unless_updatable(server_dir, moving)

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
        yield from self.lay_party_bridge(ctx.server_dir)

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
        if self._block().client_extract is not None and "." in found:
            # `stage_generate_compose()` appends the build-context block to the
            # checkout's own tracked .dockerignore (T812 D9), so a fetch's reset
            # restores upstream's copy and the same stage writes it again. The
            # update route's dirty-tree guard must read the modified file as
            # this app's, or every CoA install refuses to update.
            found["."] = (*found["."], DOCKERIGNORE)
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
        record of what an earlier press laid is still there. Also for a server with the Lua
        engine cloned (T792): My Party's bridge scripts are laid in the same window.
        """
        return (
            bool(self._block().lua_scripts)
            or scriptdeploy.record_path(server_dir).exists()
            or party.engine_cloned(server_dir)
        )

    def lay_scripts(self, server_dir: Path, *, quiet: bool) -> Iterator[str]:
        """Lay the entry's Lua scripts from the checkout as it stands now (T562).

        The world reads them when it starts, on a GM `.reload ale` and with
        `ALE.AutoReload`, so a rebuild or an update lays them with the old world
        stopped: laid during the compile they would be read by a world still running
        the old binary. A rollback calls this again from the put-back sources.
        """
        yield from scriptdeploy.lay(server_dir, self._block().lua_scripts, quiet=quiet)

    def lay_party_bridge(self, server_dir: Path) -> Iterator[str]:
        """My Party's bridge scripts, for a server that has `modules/mod-ale` (T792).

        WotLK gets the engine from the Modules tab and WoW Unbound builds it in; both read the
        same script folder, so one call serves both. A rebuild, an update and a return reach it
        through `stage_recreate()`, the fresh install through `_lua_and_sql()`. A failure is a
        line and the press goes on: `party.lay_bridge()` does not raise.
        """
        laid = party.lay_bridge(server_dir)
        if isinstance(laid, str):
            yield laid
            return
        if laid is None:
            return
        if laid.changed:
            yield "My Party's bridge scripts are in the server folder."
        if laid.kept:
            yield party.kept_sentence(laid.kept)

    def imports_with_the_servers_down(self) -> bool:
        """True for an entry whose databases are imported by a compose one-shot (T706)."""
        return bool(self.entry.containers.db_import)

    def import_with_the_servers_down(self, ctx: StageContext) -> Iterator[str]:
        """The importer one-shot on the new build, before its first start: a Rebuild's (T706).

        Live on yulon-ubuntu (2026-10-11, v0.9.16 WotLK and Unbound): Install City Bots,
        then Rebuild, and the new world aborted on `Table 'acore_world.city_bot_poi'
        doesn't exist`. A module's `data/sql/db-world` files reach the database only
        through this one-shot -- the world server runs with `Updates.EnableDatabases` 0
        -- and the install runs it in its `import` stage, the update route in its own
        window (`servers_down_work()`, T220), while a Rebuild ran it nowhere. Running it
        by hand (`docker compose up -d`) applied six files and the world came up.

        The same one-shot, on the image the tags now name, with the servers stopped and
        the database up. What upstream's updater does with each file, read from the pinned
        source (`UpdateFetcher.cpp` at 2a2211cd, the WotLK and Unbound core pin) and
        `dbimport.conf.dist`, neither of which this app overrides: a file its `updates`
        table does not hold is applied; one it holds with the same SHA1 is skipped; one
        whose SHA1 changed is applied AGAIN (`Updates.Redundancy` 1, "Reapplying update
        ... (it changed)"), except an archived core file (`ArchivedRedundancy` 0). So a
        Rebuild with nothing new applies nothing, and a module file edited after it was
        applied runs again -- and if it then fails, every Rebuild and update fails on it
        until the file applies, which `docker.importer_failure()` says by name.

        The modules it may update are the Modules tab's own split (`importer_modules`,
        T78), which every engine the app builds is handed (`installer_for_app()`; the
        reviewed exceptions in `test_rebuild_import_copy` build no engine that imports);
        without one, what the compose file says. Not cancellable part-way, for T220's
        reason; a Stop is honoured once the updates are in, before the new build starts.
        A failure raises, and the rebuild puts the build from before back -- with the copy
        of its databases taken just before this (`_rebuild_import_work()`).
        """
        service = self.entry.containers.db_import
        if not service:
            return
        yield (
            f"Applying the database updates of the server and its modules ({service}) with the "
            "servers stopped, before the new build first starts. Updates already applied are "
            "not run again."
        )
        try:
            self._seams.start_db(
                self.entry.container_spec(),
                ctx.server_dir,
                because="no database update was applied and the new build was not started",
            )
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The database could not be started to apply the new build's database updates, "
                f"so the new build was not started: {exc}"
            ) from exc
        allowed = (
            self._importer_modules(ctx.server_dir) if self._importer_modules is not None else None
        )
        self._refuse_bot_accounts_in_the_way(ctx.server_dir, allowed)
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(
                service, ctx.server_dir, sink=sink, cancel=None, allowed_modules=allowed
            ),
            cancel=None,
            stage="import",
        )
        if run.returncode != 0:
            raise InstallerError(docker.importer_failure(run, "Applying the database updates"))
        if ctx.cancel is not None and ctx.cancel.is_set():
            raise ImportWindowStopped(
                "The rebuild was stopped once the database updates were in, before the new "
                "build started."
            )
        yield "The database updates are in."

    def _refuse_bot_accounts_in_the_way(self, server_dir: Path, allowed: str | None) -> None:
        """Before an importer run that carries City Bots' SQL: no real account on a bot's id (T779).

        The Modules tab's button asks the same question in `docker.apply_module_sql()`. Here the
        database is up and the servers are down; `allowed` None is the compose file's default,
        which is every module in the folder. Raises `InstallerError` in the player's words, with
        nothing applied.
        """
        try:
            city_bots_guard.refuse_if_accounts_in_the_way(
                self.entry, server_dir, docker.ALL_MODULES if allowed is None else allowed
            )
        except city_bots_guard.CityBotsAccountRefusal as exc:
            raise InstallerError(str(exc)) from exc

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
            "None of them was applied, so the tested commit's server can start on your databases."
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
        * the move goes forward in history (`direction.moves_forward()`: git, else GitHub's
          compare), whatever the file is called (T767): the target descends from the
          running commit, so upstream removed the file after the database met it, as
          mod-playerbots 79bd428 removed 7bae1b5's undated `world/base/charsections_dbc.sql`.
          A Return that moves forward (T588) is an update, which asks nothing of the kind.

        Going back, or when neither git nor GitHub can give the direction, the name says
        nothing: an update written before the target's newest but merged after it is
        newer, so every such file counts and the database is asked. One exception keeps
        the T632 refusal: with no direction, a dated (`YYYY_MM_DD_NN`) file the target
        ships a later-dated file beside refuses and says why, as it may be a squash or a
        newer update (cold review of a72e048f).
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
            unshipped = [
                path
                for path in removed
                if (_database_part(path), posixpath.basename(path)) not in shipped
            ]
            if not unshipped:
                continue
            # T767: a target that descends from the running commit removed these files
            # itself, whatever they are called (mod-playerbots 79bd428 removed 7bae1b5's
            # undated base files): nothing it lacks is newer. Going back, every update the
            # running commit added since is one the target lacks, however its name is dated
            # (mod-playerbots names one by the day it was written).
            forward = _Direction(self._seams, source.repo, dest, old, new)
            if forward.peek():
                continue
            left: list[str] = []
            for path in unshipped:
                name = posixpath.basename(path)
                # With no direction (`known()` refuses), a dated file older than one the
                # target ships beside it may be a squash or a newer update: say so. Going
                # back (`known()` is False) it counts like any other.
                if (
                    _DATED.match(name)
                    and any(
                        _DATED.match(other) and other >= name
                        for other in beside.get(posixpath.dirname(path), ())
                    )
                    and forward.known()
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
                    f"Yu'lon could not start the database to ask it: {exc}",
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
            # T707: the Modules tab's own split, as a Rebuild's import is given.
            allowed = (
                self._importer_modules(ctx.server_dir)
                if self._importer_modules is not None
                else None
            )
            self._refuse_bot_accounts_in_the_way(ctx.server_dir, allowed)
            run = yield from self._pump(
                lambda sink: self._seams.one_shot(
                    service, ctx.server_dir, sink=sink, cancel=None, allowed_modules=allowed
                ),
                cancel=None,
                stage="import",
            )
            # `cancel=None`, then the press by name: `_check_run()`'s own check
            # says "the install was stopped", and this is an update (cold review).
            # T706 round 2: a failure names the file and the way past it.
            if run.returncode != 0:
                raise InstallerError(
                    docker.importer_failure(run, "Applying the new build's database updates")
                )
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
        # `_check_run()` first: a Stop is told as a Stop whatever the tail printed.
        # Only a failure it would raise is then re-read for a certificate number.
        try:
            self._check_run(run, "the server-data download", ctx.cancel, DOWNLOAD_CANCEL_NOTE)
        except InstallStopped:
            raise
        except InstallerError:
            if client_data_certificate_failure(run):
                # T810: the image's own ca-certificates.crt is missing, unreadable
                # or corrupt — a retry reuses the same cached bad layer (a rebuild
                # reuses it too; only removing the image remakes it), so the fetch
                # script already quit without retrying and this press ends at once.
                # The remedy names the image as `built_image_refs()` spells it.
                raise InstallerError(
                    f"the server-data download failed (exit {run.returncode}): "
                    f"{docker.last_words(run.tail)}. "
                    f"{stale_client_data_remedy(self.entry, ctx.server_dir)}"
                ) from None
            raise
        yield "Server data is in place."

    def _world_bootstrap(self, ctx: StageContext) -> Iterator[str]:
        """Load the CoA world's base data, before the import applies updates over it.

        `import` runs the db-import one-shot, whose updater applies only what its
        `updates` tables do not hold: with no bootstrap first it would apply the
        core's `updates/db_world` over a missing schema. The bootstrap is its own
        `_client_data`-pattern one-shot rather than part of `import` because an
        interrupted world bootstrap is dropped and loaded again, while an
        interrupted import is refused or cleared by its own gate — one service,
        one verdict. Done-marker: `world_data.py` loads `updates`,
        `updates_include` and `version` LAST (`world_data.py: bootstrap()`), so a
        world with tables but no `version` is an interrupted load and is
        dropped, while one WITH `version` exits 0 in seconds and the stage ends.
        `version` — not `updates`: a bare import also writes `updates` rows, so
        only the table nothing else writes says the bootstrap ran.

        `start-db` ran first and the import's probe answers afterwards, so the
        import stage that follows still decides; this stage asks nothing of the
        databases itself. Rebuild and update never run it: the world exists.
        """
        service = self._block().world_bootstrap_service
        if not service:
            yield "This server has no separate world bootstrap step."
            return
        # T539, as in `_client_data()`: a bootstrap a Stop or a closed Yu'lon
        # left running is ended before another starts into the same database.
        left = self._seams.end_one_shot(service, ctx.server_dir)
        if left is not None:
            raise OneShotLeftRunning(download_left_sentence(left, earlier=True))
        yield f"Loading the CoA world's base data ({service})."
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(
                service, ctx.server_dir, sink=sink, cancel=ctx.cancel
            ),
            cancel=ctx.cancel,
            stage="import",
        )
        if ctx.cancel is not None and ctx.cancel.is_set():
            # Not read as a clean Stop until nothing of the bootstrap is still running.
            left = self._seams.end_one_shot(service, ctx.server_dir)
            if left is not None:
                raise OneShotLeftRunning(download_left_sentence(left, earlier=False))
        self._check_run(run, "the world bootstrap", ctx.cancel, IMPORT_STAGE_CANCEL_NOTE)
        yield "The CoA world's base data is in place."

    DATA_DIR = "data"
    """`data/` under the server directory: what the extract stage fills and mmaps reads."""

    def _data_dir(self, ctx: StageContext) -> Path:
        """`data/` under the server directory -- refused if it resolves anywhere else.

        `CmangosInstaller._data_dir`'s rule: `run_mmaps()` deletes `data/mmaps`
        outright, so a `data/` that is a symlink into a client would carry that
        delete there. One `resolve()` per stage; a link refused by name.
        """
        server_dir = ctx.server_dir
        data_dir = server_dir / self.DATA_DIR
        landing = data_dir.resolve()
        if not landing.is_relative_to(server_dir.resolve()):
            raise InstallerError(
                f"{data_dir} leads to {landing}, which is outside this install's folder "
                f"({server_dir}). This install writes extracted game data into that folder and "
                "deletes a folder inside it when it regenerates movement maps, so a link pointing "
                "elsewhere would let it overwrite and delete files that are not its own. "
                "This stage ran nothing and removed nothing. Remove the link so "
                f"{self.DATA_DIR} can be this install's own folder, or install this server "
                "in the folder you want its data to live in."
            )
        data_dir.mkdir(parents=True, exist_ok=True)
        return data_dir

    def _image_ref(self, ctx: StageContext, service: str) -> str:
        """The fully qualified image reference for `service` in `native.images`.

        `CmangosInstaller._image_ref`'s body through `composegen.built_image_refs`.
        """
        prefix = self._native().image_prefix
        refs = composegen.built_image_refs(
            self.entry,
            ctx.server_dir,
            platform_id=self._seams.platform_id,
            install_id=self._install_id(ctx.server_dir),
        )
        for ref in refs:
            if ref.startswith(f"{prefix}{service}:"):
                return ref
        raise InstallerError(
            f"{service} is not one of the images this install builds "
            f"({', '.join(self._native().images)}). That is a bug in the app, not something "
            "to fix on this machine."
        )

    def _user_args(self) -> tuple[str, ...]:
        """`--user uid:gid` on Linux, nothing on Docker Desktop: `platform.py`'s one policy."""
        ask = cast("Callable[[], platform.PlatformId]", self._seams.platform_id)
        return tuple(platform.container_user_args(platform_id=ask))

    def _stream(
        self,
        call: Callable[[docker.OutputSink], Iterator[str]],
        *,
        cancel: threading.Event | None,
        stage: str,
    ) -> Iterator[str]:
        """A stage-kind generator that ALSO takes a sink: yield both streams live.

        `CmangosInstaller._stream`'s bridge: one queue, one worker thread, the
        sink's lines and the generator's own progress lines on it together.
        """
        queued: queue.Queue[str | None] = queue.Queue()
        failure: list[BaseException] = []

        def work() -> None:
            try:
                for line in call(lambda pushed: _put_all(queued, pushed, stage)):
                    queued.put(line)
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread
                failure.append(exc)
            finally:
                queued.put(None)

        worker = threading.Thread(target=work, daemon=True, name="yulon-azerothcore-output")
        worker.start()
        try:
            while True:
                item = queued.get()
                if item is None:
                    break
                yield item
        except BaseException:
            stop_abandoned_worker(worker, cancel, what="the AzerothCore stage output")
            raise
        worker.join()
        if failure:
            exc = failure[0]
            if isinstance(exc, InstallerError):
                raise exc
            raise InstallerError(f"the step could not be run: {exc}") from exc

    def _case_view(self, client_dir: Path, view: Path) -> Generator[str, None, Path]:
        """The folder the tools read the client from: itself, or a view of it in `view`.

        `CmangosInstaller._case_view`'s rule: the tools open archives by fixed
        names, and a client named in another case on a case-sensitive disk gives
        them nothing to read. The view is links under the names they open.
        """
        if not client_dir.is_dir():
            return client_dir
        try:
            extract.remove_case_view(view)
            if not extract.lay_case_view(client_dir, view):
                return client_dir
        except OSError as exc:
            try:
                extract.remove_case_view(view)
            except OSError as left:
                logger.warning(f"the links in {view} could not be removed: {left}")
            try:
                renames = extract.renamed_in_view(client_dir)
            except OSError:
                renames = []
            named = ", ".join(f"{theirs} to {ours}" for theirs, ours in renames[:3])
            more = f" and {len(renames) - 3} more" if len(renames) > 3 else ""
            raise InstallerError(
                f"Some of your client's files are named in another case than {self.entry.name}'s "
                "map tools open, and Yu'lon could not make the links that let the tools read "
                f"them in {view} ({exc}). Nothing was extracted. Free that folder, or rename the "
                f"client's files ({named}{more}), then press Install again."
            ) from exc
        renames = extract.renamed_in_view(client_dir)
        named = ", ".join(f"{theirs} as {ours}" for theirs, ours in renames[:3])
        more = f" and {len(renames) - 3} more" if len(renames) > 3 else ""
        yield (
            "Some of your client's files are named in another case than the map tools open; "
            f"they read them through links in {view} ({named}{more}). Your client is not changed."
        )
        return view

    def _extract_maps(self, ctx: StageContext) -> Iterator[str]:
        """Pull dbc/maps/vmaps out of the player's client, mounted read-only (T812 C4a).

        `CmangosInstaller._extract`'s shape: the image ref is the `tools` entry
        of `composegen.built_image_refs`, the client is `ctx.client_dir`, the
        output the server's own `data/`, the plan the entry's
        `client_extract.extract` plus one appended tool per surgical map (C4b:
        `surgical.effective_plan()`). After the run, `extract.missing_map_data()`
        of `required_maps` is the gate: a press that cannot start its world
        must not record an extract that cannot run it.
        """
        plan_block = self._block().client_extract
        if plan_block is None:
            yield "This server has no separate client-extract step."
            return
        tools_bin = self._tools_bin(plan_block)
        plan = surgical.effective_plan(self._block(), tools_bin)
        client_dir = ctx.client_dir
        if client_dir is None:
            raise InstallerError(
                f"{self.entry.name} needs the game client folder to extract its maps from, and "
                "none was given. Pick the client folder and try again."
            )
        data_dir = self._data_dir(ctx)
        image_ref = self._image_ref(ctx, plan_block.extract.image)
        user_args = self._user_args()
        yield f"Extracting server data from {client_dir} into {data_dir} (the client is read-only)."
        view = ctx.server_dir / extract.CASE_VIEW_DIR
        read_from = client_dir
        try:
            read_from = yield from self._case_view(client_dir, view)
            yield from self._stream(
                lambda sink: extract.run_plan(
                    plan,
                    image_ref=image_ref,
                    client_dir=read_from,
                    data_dir=data_dir,
                    run_container=self._seams.run_container,
                    user_args=user_args,
                    sink=sink,
                    cancel=ctx.cancel,
                    required_file=plan_block.client.required_file,
                    client_build=self.entry.client.build,
                    selinux_enforcing=self._seams.ask_selinux,
                    evidence_client_dir=client_dir,
                    client_files=None if read_from == client_dir else client_dir,
                ),
                cancel=ctx.cancel,
                stage="extract",
            )
        finally:
            try:
                extract.remove_case_view(view)
            except OSError as exc:
                logger.warning(f"the links in {view} could not be removed: {exc}")
        self._check_cancel(ctx.cancel)
        missing = extract.missing_map_data(data_dir, plan_block.required_maps)
        if missing:
            raise InstallerError(
                "The map data the world server needs at start is not all there "
                f"({'; '.join(missing)}). The extract stage made it from {client_dir}: check "
                "that it is a complete Ascension 3.3.5a client. Nothing was started."
            )
        yield "Extraction finished."

    def _mmaps(self, ctx: StageContext) -> Iterator[str]:
        """Generate movement maps from the extracted maps, before the import (T812 C4a/C4b).

        Never recorded, like `up`: a resume must always end with the movement
        maps made, and `extract.run_mmaps()` skips itself when its record and
        counts already vouch for the run. C4b lays `data/mmaps-config.yaml`
        beside it first — `mmaps_generator` opens that name in its cwd
        (`PathGenerator.cpp:169`), and the workdir here is `/out` = `data/`.
        Copied from the checkout the install was built from (the core source),
        refused by name when it is not there.
        """
        plan_block = self._block().client_extract
        if plan_block is None:  # unreachable: the stage is only in the tuple with one
            yield "This server has no separate movement-map step."
            return
        data_dir = self._data_dir(ctx)
        self._lay_mmaps_config(ctx, plan_block, data_dir)
        image_ref = self._image_ref(ctx, plan_block.extract.image)
        user_args = self._user_args()
        yield "Making movement maps for the bots"
        yield from self._stream(
            lambda sink: extract.run_mmaps(
                plan_block.mmaps,
                image_ref=image_ref,
                data_dir=data_dir,
                run_container=self._seams.run_container,
                user_args=user_args,
                sink=sink,
                cancel=ctx.cancel,
                progress_every=60.0,
            ),
            cancel=ctx.cancel,
            stage="mmaps",
        )
        self._check_cancel(ctx.cancel)
        yield "Map generation finished."

    MMAPS_CONFIG_NAME = "mmaps-config.yaml"
    """The name `mmaps_generator` opens in its cwd: beside the mmaps it writes."""

    def _tools_bin(self, plan_block: AcClientExtract) -> str:
        """The container-absolute folder of the extract tools (C4b surgical script).

        Read off the first tool's argv, not written here: if the install prefix
        ever moves, the catalog moves this with it and the script follows.
        """
        first = plan_block.extract.tools[0].argv[0]
        return first.rpartition("/")[0] or "/"

    def _lay_mmaps_config(
        self, ctx: StageContext, plan_block: AcClientExtract, data_dir: Path
    ) -> None:
        source = ctx.server_dir / plan_block.mmaps_config
        try:
            text = source.read_bytes()
        except OSError as err:
            raise InstallerError(
                f"{source} holds no {self.MMAPS_CONFIG_NAME}: the checkout this install was "
                "built from has no mmaps config where the entry says it is "
                f"({plan_block.mmaps_config}). Nothing was run."
            ) from err
        target = data_dir / self.MMAPS_CONFIG_NAME
        target.write_bytes(text)

    def start_refusal(self, server_dir: Path, *, rebuilding: bool = False) -> str | None:
        """The spine's refusal, the realm-type gate, then the extract gate (T812 C3a, C4a).

        The server must not start without the map data its start check would
        refuse on: `extract.missing_map_data()` of the entry's `required_maps`
        over the server's own `data/`. Asked in the start path so a `data/`
        emptied after a finished extract still refuses with the map sentence.

        The realm-type gate (C3a) is the spine's `family_start_refusal()`,
        which `super()` already asks last: asked once, never twice.
        """
        refused = super().start_refusal(server_dir, rebuilding=rebuilding)
        if refused is not None:
            return refused
        plan_block = self._block().client_extract
        if plan_block is None:
            return None
        missing = extract.missing_map_data(server_dir / self.DATA_DIR, plan_block.required_maps)
        if not missing:
            return None
        return (
            "The map data the world server needs at start is not all there "
            f"({'; '.join(missing)}). Press Install to extract it from your client first. "
            "Nothing was started."
        )

    def family_start_refusal(self, server_dir: Path) -> str | None:
        """The C3a realm-type gate, through the spine's family hook (T812 C3a).

        `coa_realm.refusal()` over the engine's own SQL seams (the import
        probe's client, `sql_query`): an unfinished switch refuses, a marker
        naming another type than the folder's file refuses, and a pre-marker
        install is marked with the file's type. The engine asks it where its
        database is up (the install's `up`, a rebuild's start); a database
        that cannot be asked refuses once a switch has been made here
        (`coa_realm.UNREADABLE_START_REFUSAL`), and secrets that cannot be
        read are such a database.
        """
        from yulon import coa_realm

        if not coa_realm.has_realm_types(self.entry):
            return None
        try:
            password = self.resolve_secrets(server_dir).db_password
        except Exception as exc:  # noqa: BLE001 - unknown secrets: a database nobody can ask
            logger.warning(f"the realm type gate could not read the secrets: {exc}")
            return coa_realm.refusal(self.entry, server_dir, None)
        spec = self.entry.container_spec()
        client = self._native().db.client

        def ask(statement: str) -> str:
            return self._seams.sql_query(spec.db, client, password, None, statement)

        sql = coa_realm.FnSql(run=lambda statement: _ignore_answer(ask(statement)), ask=ask)
        return coa_realm.refusal(self.entry, server_dir, sql)

    def _start_servers(self, ctx: StageContext) -> None:
        """The ordered start for an entry that asks for it, else the spine's (T812).

        For these entries `stage_ready` below only re-confirms: the waits
        already happened here, with `ctx.cancel` ending them.
        """
        from yulon.catalog import ordered_start

        if not ordered_start.ordered(self.entry):
            super()._start_servers(ctx)
            return
        spec = self.entry.container_spec()
        ordered_start.start(
            spec,
            ctx.server_dir,
            entry=self.entry,
            start_services=self._start_ordered_services,
            wait_world=self._wait_ordered_world,
            wait_auth=self._wait_ordered_world,
            open_realm=lambda: self._open_ordered_realm(ctx),
            cancel=ctx.cancel,
            say=logger.info,
        )

    def _start_ordered_services(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        *,
        services: Sequence[str] | None = None,
        wsl_distro: str | None = None,
        force_recreate: bool = False,
    ) -> bool:
        """`docker.start_staged` through the seams, naming the ordered subset."""
        return self._seams.start(spec, server_dir, services=services)

    def _wait_ordered_world(
        self,
        spec: docker.ContainerSpec,
        ready: docker.ReadySpec,
        *,
        wsl_distro: str | None = None,
    ) -> bool:
        """One ordered wait through the seams.

        A single `timeout_s` window, not the spine's quiet budget: a
        slow-but-printing first boot past that total fails here where
        `wait_for_ready()` would have kept waiting. CoA boot times are
        unmeasured; if the live proof shows one past 30 min, this wait must
        spend the quiet budget too (cold review D6).
        """
        return self._seams.wait_ready(spec, ready)

    def _open_ordered_realm(self, ctx: StageContext) -> str | None:
        """Open the realm row through the seams, read back; a sentence or None."""
        from yulon import realm_flag

        return realm_flag.open_realm(
            self.entry,
            self.entry.container_spec(),
            ctx.server_dir,
            wsl_distro=self._seams.distro,
        )

    def _recreate_servers(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        *,
        control: docker.StopControl,
        before_signal: Callable[[], None] | None = None,
    ) -> bool:
        """The ordered recreate for an entry that asks for it, else the spine's (T812)."""
        from yulon.catalog import ordered_start

        if not ordered_start.ordered(self.entry):
            return super()._recreate_servers(
                spec, server_dir, control=control, before_signal=before_signal
            )
        return self._recreate_ordered(
            spec, server_dir, control=control, before_signal=before_signal
        )

    def _recreate_ordered(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        *,
        control: docker.StopControl,
        before_signal: Callable[[], None] | None = None,
    ) -> bool:
        """Replace db+world, wait the world, replace auth, open the realm."""
        from yulon import realm_flag
        from yulon.catalog import ordered_start

        if before_signal is not None:
            before_signal()
        services = spec.compose_services()
        by_container = dict(zip((spec.db, spec.auth, spec.world), services, strict=False))
        auth_service = by_container.get(spec.auth, spec.auth)
        others = [service for service in services if service != auth_service]
        self._seams.recreate(spec, server_dir, control=control, services=others)
        world_ready, auth_ready = ordered_start.ready_specs(self.entry)
        self._wait_ordered_ready(
            world_ready, control, spec, server_dir, ordered_start.WORLD_WAIT_STOPPED
        )
        self._seams.recreate(spec, server_dir, control=control, services=[auth_service])
        self._wait_ordered_ready(
            auth_ready, control, spec, server_dir, ordered_start.AUTH_WAIT_STOPPED
        )
        refused = realm_flag.open_realm(self.entry, spec, server_dir, wsl_distro=self._seams.distro)
        if refused is not None:
            logger.info(refused)
        return True

    def _wait_ordered_ready(
        self,
        ready: docker.ReadySpec,
        control: docker.StopControl,
        spec: docker.ContainerSpec,
        server_dir: Path,
        stopped: str,
    ) -> None:
        """One ordered recreate wait: Stop lets the load finish, then still stops (T158).

        A rebuild never kills a loading world (the lead's ruling of
        2026-10-05): a Stop during the wait is said and the wait goes on
        without the cancel until the server is ready, crashes or runs out.
        Then it still raises, because the player asked to stop.
        """
        from yulon.catalog import ordered_start

        if self._seams.wait_ready(spec, replace(ready, cancel=control.abandon)):
            return
        if not control.abandon.is_set():
            raise docker.DockerRefusal(
                (
                    ordered_start.WORLD_NEVER_READY
                    if ready.auth is None
                    else ordered_start.AUTH_NEVER_READY
                ),
                detail=docker.logs_command(spec.service_for(spec.world), server_dir),
            )
        logger.info(docker.STOP_WAITS_FOR_THE_LOAD)
        self._seams.wait_ready(spec, ready)
        raise ReadyWaitStopped(stopped)

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
        service" are the same skip, worded here and not in the spine (A7). A
        realm-types entry's import then marks the plain databases with the
        default type (T812 C3a): the marker travels with the data. While a
        realm-type switch is unfinished the stage refuses before anything is
        imported (`coa_realm.import_refusal()`): Install and "Repair the
        database" both run this stage.

        On a realm-types entry the import runs under the Server tab's
        per-server hold (T812 C3b, `coa_switch.held_import`): a switch moving
        the plain schemas refuses with its own sentence instead of racing this
        import, and the guards are re-checked under it.
        """
        from yulon import coa_switch
        from yulon.catalog import native as native_mod

        if not coa_switch.needs_hold(self.entry):
            yield from self._import_inner(ctx)
            return
        yield from coa_switch.held_import(
            coa_switch.install_hold(
                lambda press: self.reserved(ctx.server_dir, press), native_mod.INSTALL_PRESS
            ),
            coa_switch.INSTALL_PRESS,
            lambda: self._import_inner(ctx),
        )

    def _import_inner(self, ctx: StageContext) -> Iterator[str]:
        """`_import()` past the hold; the guards re-checked under it (T812 C3b)."""
        from yulon import coa_realm

        refused = coa_realm.import_refusal(self.entry, ctx.server_dir)
        if refused is not None:
            raise InstallerError(refused)
        service = self.entry.containers.db_import
        probe, reset = self._gate_for(ctx.server_dir)
        if probe is None or not service:
            yield "This server has no separate database import step."
            return
        yield from self.stage_import(ctx, CallableGate(probe, reset), service)
        yield from self._mark_realm_type(ctx)

    def _mark_realm_type(self, ctx: StageContext) -> Iterator[str]:
        """Mark the plain databases with the default realm type, once (T812 C3a).

        Called after the install's `import`: the marker travels with the data,
        and a fresh import (like a fresh switch's one) names the type it was
        built for. A marker already there is left as it is (a repair import
        over a switched set; the start gate judges it), and a marker that
        cannot be read or written fails the stage.

        On a realm-types entry this runs under the same hold as `_import`
        (T812 C3b): it touches the same plain schemas a switch moves.
        """
        from yulon import coa_switch
        from yulon.catalog import native as native_mod

        if not coa_switch.needs_hold(self.entry):
            yield from self._mark_realm_type_inner(ctx)
            return
        yield from coa_switch.held_import(
            coa_switch.install_hold(
                lambda press: self.reserved(ctx.server_dir, press), native_mod.INSTALL_PRESS
            ),
            coa_switch.INSTALL_PRESS,
            lambda: self._mark_realm_type_inner(ctx),
        )

    def _mark_realm_type_inner(self, ctx: StageContext) -> Iterator[str]:
        """`_mark_realm_type()` past the hold."""
        from yulon import coa_realm

        if not coa_realm.has_realm_types(self.entry):
            return
            yield  # unreachable: keeps this a generator on the no-types path
        refused = coa_realm.import_refusal(self.entry, ctx.server_dir)
        if refused is not None:
            raise InstallerError(refused)
        sql = coa_realm.FnSql(
            run=lambda statement: _ignore_answer(
                self._seams.sql_query(
                    self.entry.container_spec().db,
                    self._native().db.client,
                    ctx.secrets.db_password,
                    None,
                    statement,
                )
            ),
            ask=lambda statement: self._seams.sql_query(
                self.entry.container_spec().db,
                self._native().db.client,
                ctx.secrets.db_password,
                None,
                statement,
            ),
        )
        file_type = coa_realm.active(self.entry, ctx.server_dir).id
        try:
            held = coa_realm.read_marker(sql.query, self.entry)
            if held is None:
                coa_realm.ensure_marker(sql, self.entry, file_type)
        except Exception as exc:  # noqa: BLE001 - every failure refuses, worded once
            raise InstallerError(
                f"The databases could not be marked as the {file_type} realm type ({exc}). "
                "Press Install again to retry; the server was not started."
            ) from exc
        if held is not None:
            yield f"The databases are already marked as the {held} realm type."
            return
        yield f"The databases are marked as the {file_type} realm type."


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
