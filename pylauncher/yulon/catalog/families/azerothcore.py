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
"""

from __future__ import annotations

import os
import stat
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

from yulon import git
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.installer import InstallerError
from yulon.catalog.native import (
    BUILD_CANCEL_NOTE,
    DOWNLOAD_CANCEL_NOTE,
    IMPORT_STAGE_CANCEL_NOTE,
    OUR_OWN_FILES,
    CallableGate,
    ConfCheck,
    ConfRepaired,
    Stage,
    StageContext,
    StagedInstaller,
    _listing,
)
from yulon.log import get_logger

logger = get_logger(__name__)

DIST_SUFFIX = ".dist"


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


def write_from_dist(server_dir: Path, file: str) -> bool:
    """Write `file` as a byte-for-byte copy of its `.dist`, if it is not there. The ONE writer.

    The install's `up` stage and the Server tab's Repair both call this, so a
    repaired install ends with the same file a fresh one does (T137). Returns
    False, touching nothing, when anything is already at `file`: a person's own
    conf is never replaced, and a resume finds its own.

    Bytes, not text: the shipped file has non-ASCII comments, and it is what the
    module's authors wrote. The mode is the `.dist`'s, not `conf.CONF_MODE`'s
    0600: the world runs as the image's `acore` user, which reads the `.dist` the
    same container wrote, and a host user that is not uid 1000 would otherwise
    hand it a file it cannot open. Nothing secret is in it: the database logins
    are container environment on this family.

    Through a temporary sibling renamed into place, so the world never reads half
    a file; a failure removes the sibling. The existence check comes first, before
    the `.dist` is even read, so a conf a person has is left alone whether or not
    its `.dist` is still there; the check and the rename are two steps, so two
    writers racing on one install is out of scope, as it is for T106's
    `_replace_if_unchanged()`.

    Raises:
        FileNotFoundError: there is no `.dist` to copy.
        OSError: the copy could not be written; nothing was left behind.
    """
    target = server_dir / file
    if target.exists() or target.is_symlink():
        return False
    dist = dist_of(server_dir, file)
    data = dist.read_bytes()
    mode = stat.S_IMODE(dist.stat().st_mode)
    fd, name = tempfile.mkstemp(prefix=target.name + ".", suffix=".yulon-new", dir=target.parent)
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    logger.info(f"wrote {target} from {dist.name}")
    return True


def conf_check(entry: CatalogEntry, server_dir: Path) -> ConfCheck:
    """Which of the entry's `confs_from_dist` are absent while their `.dist` is there. Never raises.

    A path that cannot be asked about (a permission error on the folder) is not
    offered: the press could not write there either, and the tab's reading has
    nowhere to put an exception.
    """
    missing: list[str] = []
    for file in confs_from_dist(entry):
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

    def stages(self) -> tuple[Stage, ...]:
        return (
            Stage("clone-core", self._clone_core),
            Stage("clone-modules", self._clone_modules),
            Stage("generate-compose", self.stage_generate_compose),
            Stage("build", self.stage_build, cancel_note=BUILD_CANCEL_NOTE),
            Stage("client-data", self._client_data, cancel_note=DOWNLOAD_CANCEL_NOTE),
            Stage("start-db", self._start_db, recorded=False),
            Stage("import", self._import, cancel_note=IMPORT_STAGE_CANCEL_NOTE),
            Stage("up", self._up, recorded=False),
            Stage("ready", self.stage_ready, recorded=False),
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
                    f"{dest} is a checkout of {existing}, not of {source.url}. Nothing was "
                    "changed."
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
        yield f"Fetching server data ({service}). The download resumes if it is interrupted."
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(
                service, ctx.server_dir, sink=sink, cancel=ctx.cancel
            ),
            cancel=ctx.cancel,
            stage="import",
        )
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
        yield from self.stage_up(ctx)

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
