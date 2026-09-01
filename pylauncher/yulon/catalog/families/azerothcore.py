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

from collections.abc import Iterator
from typing import ClassVar

from yulon import git
from yulon.catalog.installer import InstallerError
from yulon.catalog.native import (
    BUILD_CANCEL_NOTE,
    DOWNLOAD_CANCEL_NOTE,
    IMPORT_CANCEL_NOTE,
    STATE_FILE,
    CallableGate,
    Stage,
    StageContext,
    StagedInstaller,
)


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
            Stage("import", self._import, cancel_note=IMPORT_CANCEL_NOTE),
            Stage("up", self.stage_up, recorded=False),
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
            leftovers = [item.name for item in server_dir.iterdir() if item.name != STATE_FILE]
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
        self._clone(
            git.CloneSpec(
                url=source.url,
                dest=server_dir,
                branch=source.branch,
                sparse_path=source.sparse_path,
                # Data, not a constant: the core repo says `null` in
                # catalog.json because its CMake reads the revision out of git
                # history and a shallow clone hands the build the wrong answer.
                depth=source.depth,
            )
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
            if not has_git and dest.is_dir() and any(dest.iterdir()):
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
            self._clone(
                git.CloneSpec(
                    url=source.url,
                    dest=dest,
                    branch=source.branch,
                    sparse_path=source.sparse_path,
                    depth=source.depth,
                )
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
            lambda sink: self._seams.one_shot(service, ctx.server_dir, sink=sink, cancel=ctx.cancel)
        )
        self._check_run(run, "the server-data download", ctx.cancel, DOWNLOAD_CANCEL_NOTE)
        yield "Server data is in place."

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
