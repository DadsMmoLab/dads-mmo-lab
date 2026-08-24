"""The native install engine: install a server without a shell script (roadmap 6.2).

One typed engine for every platform whose install is not a bash script — macOS
today, native Windows next (6.3) — driving Docker Desktop, which 5.1 already
provisions. It has the SAME contract as `installer.Installer`
(`run(options, *, cancel, ask) -> Iterator[str]`), so the catalog view, the log
panel and the job runner need no changes and Linux takes no new code path at
all: `installer.installer_for()` picks between the two from `catalog.json`
data.

**Staged and resumable.** The stages are recorded by NAME in
`.yulon-install.json`, so reordering them can never re-interpret an existing
install. The rules below each cost the earlier Rust launcher an evening
(`pyplan/rust-prior-art.md` §1), and they are the reason this file is more
careful than its length suggests:

* `preflight` and `guard` are NEVER recorded complete — a guard a resume skips
  is not a guard. `start-db`, `up` and `ready` are not recorded either: a resume
  must always end by actually starting and verifying the server, and it must
  bring the database back up before the import stage can ask it anything.
* **The state file is a hint.** Every stage re-checks disk evidence before
  skipping: the clone stages ask git for the remote, the build asks compose for
  images, compose generation reads its own marker. An `is_done` short-circuit
  once let a state file dropped into a directory make the generator rewrite a
  real server's compose file and orphan its character volumes.
* `install_id` is a hash of the ABSOLUTE server directory, so a COPIED install
  directory is refused rather than adopted.
* A failure mid-stage records nothing, so the stage re-runs.

**Nothing on this path may prompt.** `ask` is accepted to match the contract
and never used: a step that turns out to need an answer is a design failure to
fix rather than a dialog to add.

That is now structural rather than a claim about data. This engine calls
`ensure_docker()` itself, and on Linux that call CAN escalate — so "there is no
`sudo` here" was only true because `catalog.json` happens not to dispatch any
entry natively on Linux, which is one JSON key away from being false. Passing
no `ask` down makes the consent seam decline by default, so a future Linux
native entry fails honestly ("nobody to ask") instead of joining the docker
group behind the user's back.

**Verified where, exactly.** Everything here is unit-tested against seams and
NONE of it has been run against a real daemon by this project — there is no Mac
on it. Docstrings say which claims are inherited from the Rust launcher's
incidents, which are measured on yulon-ubuntu (Linux), and which are merely
written.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path

from yulon import docker, git, platform, resources, runner
from yulon.catalog import composegen, preflight
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.installer import (
    DockerUnavailableError,
    InstallerError,
    InstallOptions,
    UnsupportedPlatformError,
    unsupported_platform_message,
)
from yulon.log import get_logger

logger = get_logger(__name__)

STATE_FILE = ".yulon-install.json"
STATE_VERSION = 1

STAGE_ORDER: tuple[str, ...] = (
    "preflight",
    "guard",
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

NEVER_RECORDED = frozenset({"preflight", "guard", "start-db", "up", "ready"})
"""Stages whose completion is never written down. See the module docstring.

`start-db` is here because it is a precondition rather than progress: the
import stage after it needs a live database both to probe and to write to, and
a resume that skipped it would fail the same way a first install without it
did. It costs seconds against an already-running container.
"""

OPENING_NOTE = (
    "You can stop this at any time. Nothing is written outside the folder below, and starting the "
    "install again continues from the last step that finished — only the step that was interrupted "
    "runs again."
)
"""What a Stop costs, said before it is pressed, and true of every stage.

The sentence this replaced was about the build (see `BUILD_CANCEL_NOTE`) and
was said as the second line of every install and appended to every
cancellation, so a user who stopped during the clone or the download was told
Docker was finishing a build step (review, 2026-08-23).
"""

BUILD_CANCEL_NOTE = (
    "Stopping now leaves Docker finishing the build step it is already on, in the background. "
    "That is deliberate: the work it has done is kept, and starting this install again picks up "
    "from there instead of compiling it all a second time."
)
"""What a Stop does DURING THE BUILD, said just before the build starts.

Abandoning the compose client does not abandon the daemon's work, and the
layer cache is precisely what makes a resume cheap — a user told "cancelled"
without this sentence reaches for `docker builder prune` and throws away the
thing that would have saved them three hours.
"""

DOWNLOAD_CANCEL_NOTE = (
    "The part of the download that finished is kept: the fetch resumes from where it stopped."
)
"""True of this stage only, and it is the generated entrypoint that makes it true.

`curl --continue-at -` against a version-keyed file, plus an `unzip -t` before
anything is extracted (see the base template). Upstream's own downloader
truncates and restarts at byte zero, which is why the fetch is replaced.
"""

IMPORT_CANCEL_NOTE = (
    "Databases left half-written are detected and cleared before the import is run again, so "
    "nothing here has to be undone by hand."
)
"""Why a stopped import is recoverable — measured, not assumed.

Re-running the importer over a schema that already exists reports success in 28
seconds and leaves `acore_world` permanently unimportable (yulon-ubuntu,
2026-08-23). The `partial` branch of `_import()` is what makes this sentence
true; without it the honest copy would be the opposite.
"""

# What the auth server prints once it is serving the realm. A fresh install's
# realmlist row is 127.0.0.1 and the world port, which is what `wait_ready()`
# looks for in the auth log; the Networking tab is what changes it afterwards.
_READY_REALM_HOST = "127.0.0.1"


@dataclass(frozen=True)
class InstallState:
    """`.yulon-install.json`: what a previous run of THIS install got through.

    A hint and a claim of ownership, never an authority on what is on disk.
    `install_id` is what makes it a claim: it is derived from the absolute path
    of the directory holding it, so a state file copied into another directory
    describes an install that is not there.
    """

    game_id: str
    install_id: str
    completed: tuple[str, ...] = ()
    last_error: str = ""
    updated_unix: int = 0
    version: int = STATE_VERSION

    def with_stage(self, stage: str) -> InstallState:
        """This state plus `stage`, in stage order, with nothing recorded twice."""
        if stage in NEVER_RECORDED or stage in self.completed:
            return self
        done = set(self.completed) | {stage}
        return replace(self, completed=tuple(s for s in STAGE_ORDER if s in done), last_error="")

    def has(self, stage: str) -> bool:
        """Did a previous run finish `stage`? Never a reason to skip on its own."""
        return stage in self.completed


def read_state(server_dir: Path) -> InstallState | None:
    """The state file in `server_dir`, or None if there is none this engine wrote.

    An unreadable or malformed file answers None: it is a hint, and a hint that
    cannot be parsed is simply no hint. It is never deleted here — a file this
    engine cannot read may be somebody else's, and `guard` is what decides
    whether the directory can be used at all.
    """
    path = server_dir / STATE_FILE
    try:
        with path.open(encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, ValueError) as exc:
        logger.debug(f"no usable install state in {server_dir}: {exc}")
        return None
    if not isinstance(parsed, dict):
        return None
    game_id = parsed.get("game_id")
    install_id = parsed.get("install_id")
    if not isinstance(game_id, str) or not isinstance(install_id, str):
        logger.warning(f"{path} does not say which install it belongs to; ignoring it")
        return None
    completed = parsed.get("completed")
    stages = (
        tuple(stage for stage in completed if isinstance(stage, str) and stage in STAGE_ORDER)
        if isinstance(completed, list)
        else ()
    )
    error = parsed.get("last_error")
    updated = parsed.get("updated_unix")
    version = parsed.get("version")
    return InstallState(
        game_id=game_id,
        install_id=install_id,
        completed=stages,
        last_error=error if isinstance(error, str) else "",
        updated_unix=updated if isinstance(updated, int) else 0,
        version=version if isinstance(version, int) else STATE_VERSION,
    )


def write_state(server_dir: Path, state: InstallState) -> None:
    """Write the state file atomically, never leaving a half-written one behind.

    A truncated state file is worse than none: `read_state()` would answer None
    and the resume would redo a two-hour build it had already finished.
    """
    path = server_dir / STATE_FILE
    payload = {
        "version": state.version,
        "game_id": state.game_id,
        "install_id": state.install_id,
        "completed": list(state.completed),
        "last_error": state.last_error,
        "updated_unix": int(time.time()),
    }
    tmp = path.with_name(path.name + ".new")
    try:
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)  # atomic on POSIX and on Windows
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.warning(f"could not record install progress in {path}: {exc}")


def _git_file_unmodified(dest: Path, relative_path: str) -> bool | None:
    """`git status --porcelain -- <path>` inside a container; see `ContainerGit`.

    Defined above `Seams` rather than beside `_git_remote_url()` because it is a
    default value, evaluated when the dataclass is created.
    """
    return git.ContainerGit().is_unmodified(dest, relative_path)


@dataclass
class Seams:
    """Everything the engine reaches outside itself through. Real by default.

    Grouped rather than spread over twenty constructor keywords, because the
    list is long for a reason: an engine whose every external effect is a seam
    is one whose control flow can be tested without a daemon, a network or a
    four-hour build — which is the only kind of test anyone on this project can
    run for this file.
    """

    platform_id: Callable[[], str] = platform.detect
    docker_ready: Callable[[], bool] = platform.docker_ready
    ensure_docker: Callable[..., platform.ProvisionReport] = platform.ensure_docker
    gather: Callable[..., preflight.Facts] = preflight.gather
    clone: Callable[[git.CloneSpec], None] = field(default_factory=lambda: git.ContainerGit().clone)
    remote_url: Callable[[Path], str | None] | None = None
    file_unmodified: Callable[[Path, str], bool | None] = _git_file_unmodified
    images_built: Callable[[Path, Sequence[str]], bool | None] = docker.images_built
    build: Callable[..., docker.AttachedRun] = docker.build_staged
    one_shot: Callable[..., docker.AttachedRun] = docker.run_one_shot
    verify_import: Callable[..., docker.ImportState] = docker.verify_import
    container_exists: Callable[[str], bool] = docker.container_exists
    container_project: Callable[[str], str | None] = docker.container_project
    start_db: Callable[[docker.ContainerSpec, Path], None] = docker.start_database
    start: Callable[[docker.ContainerSpec, Path], bool] = docker.start_staged
    wait_db_healthy: Callable[[docker.ContainerSpec], bool] = docker.wait_db_healthy_for
    wait_ready: Callable[[docker.ContainerSpec, str, int], bool] = docker.wait_ready_for
    keep_awake: Callable[[], AbstractContextManager[None]] = platform.keep_awake


class NativeInstaller:
    """Install one catalog entry natively: clone, generate compose, build, import, start.

    Constructed by `installer.installer_for()`, which is the only thing that
    decides between this and the script path. `import_probe`/`reset_unfinished`
    are supplied by the CALLER (the app wires `controller_wow_wotlk.repair`),
    because they are per-game facts and `catalog/` must not import a controller
    package — the same shape `controller_view.py` already uses.
    """

    def __init__(
        self,
        entry: CatalogEntry,
        *,
        installers_root: Path | None = None,
        import_probe: docker.ImportProbe | None = None,
        reset_unfinished: docker.ResetUnfinished | None = None,
        seams: Seams | None = None,
    ) -> None:
        self.entry = entry
        self.installers_root = (
            installers_root if installers_root is not None else resources.installers_dir()
        )
        self._probe = import_probe
        self._reset = reset_unfinished
        self._seams = seams if seams is not None else Seams()

    # -- the contract ----------------------------------------------------

    def server_dir(self, options: InstallOptions) -> Path:
        """Where this install goes: what the user picked, or the entry's default under $HOME."""
        if options.server_dir is not None:
            return options.server_dir
        return Path.home() / self.entry.install.default_server_dir

    def preflight(
        self,
        options: InstallOptions,
        cancel: threading.Event | None = None,
        *,
        ask: runner.Prompter | None = None,
    ) -> None:
        """Everything that must be true before anything is written. Raises, or returns.

        Same signature as `Installer.preflight()` so the two are
        interchangeable. Docker provisioning is attempted exactly once before
        the machine facts are gathered, because every number below it is
        fabricated without a daemon.

        `ask` is accepted for that interchangeability and deliberately NOT
        forwarded — see the module docstring on why declining by default is the
        native path's rule rather than a consequence of today's catalog data.
        """
        for _ in self._preflight_lines(options, cancel):
            pass

    def run(
        self,
        options: InstallOptions | None = None,
        *,
        cancel: threading.Event | None = None,
        ask: runner.Prompter | None = None,
    ) -> Iterator[str]:
        """Run the install, yielding output live. Resumes whatever a previous run finished.

        `ask` is accepted and never used — see the module docstring.

        Raises:
            InstallerError: any refusal, any stage that failed, or a cancel.
                The message is the sentence a user reads in the failure dialog.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        yield f"Installing {self.entry.name} into {server_dir}"
        yield OPENING_NOTE
        yield from self._preflight_lines(opts, cancel)
        self._check_cancel(cancel)

        state = self._guard(server_dir)
        yield f"Using {server_dir} ({'resuming' if state.completed else 'a fresh install'})"
        if state.completed:
            yield f"Already finished: {', '.join(state.completed)}"

        try:
            with self._held_awake() as note:
                if note:
                    yield note
                for stage in STAGE_ORDER:
                    if stage in ("preflight", "guard"):
                        continue
                    self._check_cancel(cancel)
                    yield f"--- {stage}"
                    state = yield from self._run_stage(stage, state, server_dir, cancel)
        except InstallerError as exc:
            # Recorded only into a state file that already exists. Creating one
            # to hold an error would make the state file itself the content
            # that stops `guard` calling this directory empty — so the retry
            # would be refused by the record of the failure it is retrying.
            self._record_error(server_dir, state, str(exc))
            raise
        yield f"{self.entry.name} is installed and running in {server_dir}"

    # -- preflight -------------------------------------------------------

    def _preflight_lines(
        self, options: InstallOptions, cancel: threading.Event | None
    ) -> Iterator[str]:
        here = self._seams.platform_id()
        if not self.entry.install.supports(here):
            raise UnsupportedPlatformError(unsupported_platform_message(self.entry, here))
        if self.entry.install.native is None:
            raise InstallerError(
                f"{self.entry.name} is not set up for a native install on {here} — its "
                "catalog entry has no `install.native` section. Nothing was started."
            )
        if self.entry.containers.db_import and self._probe is None:
            # An installer that cannot ask what state the databases are in
            # cannot know whether its own import finished, and an installer
            # with no reset seam would strand exactly the interrupted install a
            # resumable engine exists for (see `docker.repair_import()`'s
            # `partial` branch, measured on yulon-ubuntu 2026-08-23).
            raise InstallerError(
                f"{self.entry.name} names a database import service but this installer was "
                "built without a way to check it. That is a bug in the app, not something to "
                "fix on this machine."
            )
        server_dir = self.server_dir(options)
        if not self._seams.docker_ready():
            report = self._seams.ensure_docker(cancel=cancel)
            if report.reboot_required:
                raise DockerUnavailableError(
                    "Docker's prerequisites were installed but a reboot is needed first. "
                    + " ".join(report.manual_steps)
                )
            if not report.docker_ready and not self._seams.docker_ready():
                details = " ".join(report.manual_steps) or "; ".join(report.skipped)
                raise DockerUnavailableError(
                    "Docker isn't available and could not be set up automatically. "
                    + (details or "Install Docker, start it, and try again.")
                )
        try:
            # The engine's own seams are handed down rather than letting
            # preflight fall back to its real defaults: without this an engine
            # built with `platform_id=lambda: "macos"` dispatches as macOS and
            # then gathers facts about whatever host it is really on, and the
            # two can disagree with nothing noticing.
            facts = self._seams.gather(
                self.entry,
                server_dir,
                platform_id=self._seams.platform_id,
                docker_ready=self._seams.docker_ready,
            )
        except docker.DockerCommandError as exc:
            # `gather()`'s port scan goes through `docker._run()`, which RAISES.
            # Every other outward call in this engine is wrapped, and an
            # unwrapped one escapes `run()` as a traceback instead of the
            # sentence this method's contract promises.
            raise InstallerError(
                f"Docker answered once and then would not answer again, so nothing was started: "
                f"{exc}"
            ) from exc
        report_checks = preflight.evaluate(self.entry, server_dir, facts)
        yield from preflight.lines(report_checks)
        if not report_checks.ok():
            raise InstallerError(
                "This machine cannot install the server yet:\n" + report_checks.message()
            )

    # -- the guard -------------------------------------------------------

    def _guard(self, server_dir: Path) -> InstallState:
        """Claim the directory, or refuse it. Never recorded, so a resume re-runs it.

        Distinct from preflight, which is about the machine. This is about this
        one folder and this one install: it must be empty, or ours by
        `install_id`, and no container wearing this entry's names may belong to
        a different compose project.
        """
        install_id = composegen.install_id(server_dir, platform_id=self._seams.platform_id)
        existing = read_state(server_dir) if server_dir.is_dir() else None
        if existing is not None and existing.install_id != install_id:
            raise InstallerError(
                f"{server_dir} holds an install record made for a different folder, so this "
                "looks like a copy of another install. Copying a server folder does not copy "
                "its containers or its database. Install into an empty folder instead."
            )
        if existing is not None and existing.game_id != self.entry.id:
            raise InstallerError(
                f"{server_dir} already holds an install of {existing.game_id}. Pick another "
                "folder."
            )
        if existing is None and server_dir.is_dir() and not (server_dir / ".git").is_dir():
            # A directory that is a git checkout is deliberately NOT judged
            # here: `clone-core` can ask whose it is, and "this is a checkout of
            # somebody else's fork" is a far better sentence than "this folder
            # is not empty". Everything else is refused before a byte is
            # written.
            leftovers = [item.name for item in server_dir.iterdir() if item.name != STATE_FILE]
            if leftovers:
                raise InstallerError(
                    f"{server_dir} is not empty and was not created by this app "
                    f"({', '.join(sorted(leftovers)[:5])}). Nothing was written. Pick an empty "
                    "folder, or remove that one yourself if you no longer want it."
                )
        self._refuse_foreign_containers(server_dir, install_id)
        return existing or InstallState(game_id=self.entry.id, install_id=install_id)

    def _refuse_foreign_containers(self, server_dir: Path, install_id: str) -> None:
        """Refuse when a container wearing our names belongs to somebody else's project.

        Container names are global per engine and every AzerothCore install in
        the wild uses the same three, so this is not exotic: a second install
        while the first exists is the normal way to meet it. The remedy named
        is Remove, which the app has.

        **Existence is asked FIRST, and that is the whole of the fresh-install
        case.** `container_project()` answers `UNREADABLE` for any non-zero
        `docker inspect`, and `docker inspect <missing>` exits 1 — so asking it
        about a container that is not there refused every install on every
        machine that had never run this server, naming a container the user
        could then not find (review, 2026-08-23). The pre-existing caller in
        `docker._running()` guards the same way, `if name not in running:
        continue`; this is that check, spelled for containers that exist but are
        stopped as well as running ones.
        """
        ours = composegen.project_name(
            self.entry.id, server_dir, platform_id=self._seams.platform_id
        )
        spec = self.entry.container_spec()
        for name in (spec.db, spec.auth, spec.world):
            try:
                if not self._seams.container_exists(name):
                    continue
            except docker.DockerCommandError as exc:
                raise InstallerError(
                    "Docker would not say what containers already exist on this machine "
                    f"({exc}), so this install cannot prove it is safe to create its own. "
                    "Nothing was written."
                ) from exc
            owner = self._seams.container_project(name)
            if owner is None or owner == ours:
                # `None` is a container that exists and carries no compose label
                # — something started outside compose, wearing our name. Not a
                # project conflict, and the daemon's own duplicate-name error is
                # the honest report if it is still there at `up`.
                continue
            if owner == docker.UNREADABLE:
                raise InstallerError(
                    f"Docker would not say which install the existing {name} container belongs "
                    "to, so this install cannot prove it is safe to create one. Nothing was "
                    "written."
                )
            raise InstallerError(
                f"A container called {name} already exists and belongs to another install "
                f"({owner}). Two servers cannot share that name. Remove the other install's "
                "containers from its own tab first, then try again."
            )

    # -- stages ----------------------------------------------------------

    def _run_stage(
        self,
        stage: str,
        state: InstallState,
        server_dir: Path,
        cancel: threading.Event | None,
    ) -> Generator[str, None, InstallState]:
        if stage == "clone-core":
            yield from self._clone_core(state, server_dir)
        elif stage == "clone-modules":
            yield from self._clone_modules(server_dir)
        elif stage == "generate-compose":
            yield from self._generate_compose(server_dir)
        elif stage == "build":
            yield from self._build(state, server_dir, cancel)
        elif stage == "client-data":
            yield from self._client_data(server_dir, cancel)
        elif stage == "start-db":
            yield from self._start_db(server_dir)
        elif stage == "import":
            yield from self._import(server_dir, cancel)
        elif stage == "up":
            yield from self._up(server_dir)
        elif stage == "ready":
            yield from self._ready(server_dir)
        else:  # pragma: no cover - STAGE_ORDER and this dispatch are one list
            raise InstallerError(f"unknown install stage {stage!r}")
        if stage in NEVER_RECORDED:
            return state
        recorded = state.with_stage(stage)
        write_state(server_dir, recorded)
        return recorded

    def _clone_core(self, state: InstallState, server_dir: Path) -> Iterator[str]:
        """Clone (or update) the emulator itself INTO the server dir — it is the checkout.

        Disk evidence beats the state file: a `.git` whose `origin` is this
        entry's source is an existing clone and gets fetch+reset through the
        seam's own update path; a `.git` pointing somewhere else is refused BY
        NAME and never deleted, because a directory holding somebody's fork is
        not this installer's to remove.
        """
        source = self.entry.emulator.sources[0]
        has_git = (server_dir / ".git").is_dir()
        existing = self._remote_of(server_dir)
        if has_git and existing is None:
            # A checkout whose origin cannot be read is not an empty directory
            # and not ours either. Refused rather than cloned over, because the
            # clone seam DELETES a destination it does not recognise.
            raise InstallerError(
                f"{server_dir} contains a git checkout, but git would not say what it is a "
                "checkout of, so nothing was changed. Pick an empty folder."
            )
        if existing is not None and not _same_repo(existing, source.url):
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
        yield f"Cloning {source.repo} into {server_dir} (this is a large repository)"
        if state.has("clone-core") and existing is not None:
            yield "It is already cloned; updating it instead."
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

    def _clone_modules(self, server_dir: Path) -> Iterator[str]:
        """Clone every other source under `modules/`, which is what the build mounts.

        Guarded exactly like `_clone_core()`, and for a reason this loop once
        did not have: the clone seam `shutil.rmtree`s a destination it does not
        recognise, and `_remote_of()` answers `None` for a directory with no
        `.git`. A `modules/mod-playerbots` a user had put there by hand — a
        tarball, a copied tree, a checkout without its `.git` — fell straight
        through the only check here and was deleted (review, 2026-08-23). One
        engine that refuses to touch what it does not own must do it at every
        level, not just the top one.
        """
        sources = self.entry.emulator.sources[1:]
        if not sources:
            yield "This server has no extra modules to clone."
            return
        for source in sources:
            name = source.repo.rstrip("/").split("/")[-1]
            dest = server_dir / "modules" / name
            has_git = (dest / ".git").is_dir()
            existing = self._remote_of(dest)
            if has_git and existing is None:
                raise InstallerError(
                    f"{dest} contains a git checkout, but git would not say what it is a "
                    "checkout of, so nothing was changed. Move that folder aside and try again."
                )
            if existing is not None and not _same_repo(existing, source.url):
                raise InstallerError(
                    f"{dest} is a checkout of {existing}, not of {source.url}. Nothing was "
                    "changed."
                )
            if not has_git and dest.is_dir() and any(dest.iterdir()):
                raise InstallerError(
                    f"{dest} has files in it but is not a checkout of {source.url}, so it was "
                    "left alone. Move that folder aside and try again."
                )
            yield f"Cloning {source.repo} into modules/{name}"
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

    def _generate_compose(self, server_dir: Path) -> Iterator[str]:
        """Write the three compose files, and refuse to overwrite ones we did not write.

        With one recognised exception, which every install needs. The server dir
        IS the emulator checkout and that repository ships its own
        `docker-compose.yml` at the root — the Linux installer's whole mechanism
        depends on it being there — so the clone stage lays an unmarked base
        file down before this ever runs. Refusing it refused every install, with
        "point the install at an empty folder" said to a user who had (review,
        2026-08-23).

        The exception is narrow and mechanical: git is asked whether that path
        is tracked and unmodified in this checkout. Empty output from `git
        status --porcelain -- docker-compose.yml` proves the file is byte for
        byte what the clone wrote and that `git checkout -- docker-compose.yml`
        restores it, so replacing it destroys nothing. A file a user has edited
        answers ` M`, an untracked one answers `??`, and a git that cannot be
        asked answers `None` — all three keep the refusal. The override and
        build files get no exception at all: upstream ships neither, so an
        unmarked one there is somebody's own settings.
        """
        plan = composegen.render(
            self.entry,
            server_dir,
            templates_root=self.installers_root,
            platform_id=self._seams.platform_id,
        )
        replaceable = self._replaceable_compose(server_dir)
        if replaceable:
            yield (
                f"Replacing the {composegen.BASE_FILE} that came with the repository; it is "
                "unchanged from what git has, so `git checkout` brings it back."
            )
        try:
            written = composegen.write_plan(plan, server_dir, replaceable=replaceable)
        except composegen.ComposeGenError as exc:
            raise InstallerError(str(exc)) from exc
        except OSError as exc:
            raise InstallerError(f"the compose files could not be written: {exc}") from exc
        if not written:
            yield "The compose files are already exactly what this install needs."
        for path in written:
            yield f"Wrote {path.name}"

    def _replaceable_compose(self, server_dir: Path) -> tuple[str, ...]:
        """`docker-compose.yml`, when git proves it is the one the clone wrote. See above."""
        path = server_dir / composegen.BASE_FILE
        if not path.exists() or composegen.is_ours(path):
            return ()
        if self._seams.file_unmodified(server_dir, composegen.BASE_FILE):
            return (composegen.BASE_FILE,)
        return ()

    def _build(
        self, state: InstallState, server_dir: Path, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Compile the server. Hours, and the one stage whose output is worth watching.

        The state file alone never skips this: `compose images -q` is asked,
        and only an answer of "there are images" plus a recorded build counts.
        A daemon that will not answer is not "no images" — it is unknown, and
        an unknown re-runs the build, which is slow and safe rather than fast
        and wrong.
        """
        built = self._seams.images_built(server_dir, composegen.COMPOSE_FILES)
        if state.has("build") and built:
            yield "The server is already built; skipping the compile."
            return
        if state.has("build") and built is None:
            yield "Docker would not say whether this install is built, so it is being rebuilt."
        yield "Building the server. This takes hours on a first install; the output below is live."
        yield BUILD_CANCEL_NOTE
        run = yield from self._pump(
            lambda sink: self._seams.build(
                server_dir, composegen.COMPOSE_FILES, sink=sink, cancel=cancel
            )
        )
        self._check_run(run, "the build", cancel, BUILD_CANCEL_NOTE)
        yield "The build finished."

    def _client_data(self, server_dir: Path, cancel: threading.Event | None) -> Iterator[str]:
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
            lambda sink: self._seams.one_shot(service, server_dir, sink=sink, cancel=cancel)
        )
        self._check_run(run, "the server-data download", cancel, DOWNLOAD_CANCEL_NOTE)
        yield "Server data is in place."

    def _start_db(self, server_dir: Path) -> Iterator[str]:
        """Bring the database up alone, before the import stage asks it anything.

        Not an optimisation and not tidiness — without it the install cannot
        finish. `_import()` probes first, and the real probe is a `docker exec
        <db container> mysql …`; with no such container it raises and the probe
        answers `unreadable`, which `_import()` turns into a hard refusal. So
        the fresh install died at the import stage AFTER the multi-hour build,
        every time, and every resume died in the same place (review,
        2026-08-23). Running the one-shot anyway would not have helped:
        `run_one_shot()` passes `--no-deps`, which prunes exactly the
        `depends_on: <db>: condition: service_healthy` edge the generated base
        file declares on the import service.

        `pyplan/phase6-decisions.md` had already settled this for the repair
        button — "The action starts the database, and that is a deliberate
        widening of 'runs only the one-shot service'… Without it the action is
        unreachable" — and `docker.start_database()` is that same code, shared
        so the two paths cannot drift.

        Never recorded: it is a precondition, not progress, and it returns
        immediately when the container is already up.
        """
        if not self.entry.containers.db_import:
            yield "This server has no database step, so nothing needs the database yet."
            return
        yield "Starting the database, which the import writes into."
        try:
            self._seams.start_db(self.entry.container_spec(), server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The database could not be started, so nothing was imported: {exc}"
            ) from exc
        yield "The database is up."

    def _import(self, server_dir: Path, cancel: threading.Event | None) -> Iterator[str]:
        """Populate the databases, using the same probe/reset machinery as the repair button.

        Four answers, four different things to do — the branch table is
        `docker.repair_import()`'s, because an installer and a repair ask the
        same question of the same databases:

        * `absent` — run the one-shot;
        * `partial` — reset the half-written schemas FIRST, then run. Re-running
          the importer over a schema that already exists reported success in 28
          seconds and left `acore_world` permanently unimportable (measured on
          yulon-ubuntu, 2026-08-23);
        * `imported`, or `populated` with every schema complete — skip. A resume
          must not touch a finished import, and rows alone are not failure:
          modules seed them;
        * `unreadable`, or `populated` but incomplete — refuse. An unanswerable
          database is not an empty one, and a database with rows in it is
          somebody's.

        `unreadable` means what it says here only because `start-db` ran first:
        the probe reaches the databases through `docker exec` on the database
        container, so without one running it answers `unreadable` for a machine
        with nothing wrong with it. See `_start_db()`.
        """
        service = self.entry.containers.db_import
        if not service or self._probe is None:
            yield "This server has no separate database import step."
            return
        before = self._probe()
        yield f"The databases read as {before.state}: {before.detail}"
        if before.state == "imported" or (before.state == "populated" and before.complete):
            yield "They are already imported; leaving them alone."
            return
        if before.state == "unreadable":
            raise InstallerError(
                f"The databases could not be asked what state they are in ({before.detail}), so "
                "nothing was imported. Nothing can be established about them either way."
            )
        if before.state == "populated":
            raise InstallerError(
                f"These databases already hold data ({before.detail}) but are not finished. "
                "Importing over them would overwrite it, so nothing was run. Use an empty "
                "folder for a new install."
            )
        if before.state == "partial":
            if self._reset is None:
                raise InstallerError(
                    f"This install's databases were left half-written ({before.detail}) and "
                    "this installer has no way to clear them, so nothing was run."
                )
            yield f"Clearing the half-written databases first ({before.detail})."
            dropped = self._reset()
            if not dropped:
                raise InstallerError(
                    "The databases read as unfinished, but nothing was found to clear, so the "
                    "import was not run. Nothing was changed."
                )
            yield f"Cleared {', '.join(dropped)}."
        yield f"Importing the databases ({service}). This takes several minutes."
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(service, server_dir, sink=sink, cancel=cancel)
        )
        if run.returncode == docker.CANCELLED_RETURNCODE:
            raise InstallerError(_cancelled_message("the database import", IMPORT_CANCEL_NOTE))
        try:
            after = self._seams.verify_import(self._probe, service, server_dir, run)
        except docker.DockerCommandError as exc:
            raise InstallerError(str(exc)) from exc
        yield f"The databases now read as {after.state}."

    def _up(self, server_dir: Path) -> Iterator[str]:
        """Start the three long-running services, and only those.

        Never recorded: a resume has to end with the server actually running,
        and this is cheap. `start_staged()` names the services explicitly so
        `up` can never select the one-shot import — the thing `dml-start.sh`
        warns about in as many words.
        """
        yield "Starting the server."
        try:
            self._seams.start(self.entry.container_spec(), server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(f"The server would not start: {exc}") from exc

    def _ready(self, server_dir: Path) -> Iterator[str]:
        """Wait until the database is healthy and both servers have said they are up.

        Nothing new: `wait_db_healthy_for()` and `wait_ready_for()` already poll
        `StartedAt` and read `logs --since` that timestamp rather than `--tail`
        — the marker prints once and scrolls out of any tail window on a busy
        playerbots boot, which this project and the Rust launcher hit
        independently on the same day.
        """
        spec = self.entry.container_spec()
        yield "Waiting for the database."
        if not self._seams.wait_db_healthy(spec):
            raise InstallerError(
                f"The database never reported healthy. `docker compose logs {spec.db}` in "
                f"{server_dir} will say why."
            )
        yield "Waiting for the world server to finish loading (this can take many minutes)."
        if not self._seams.wait_ready(spec, _READY_REALM_HOST, self.entry.ports.world):
            raise InstallerError(
                f"The server started but never reported ready. `docker compose logs "
                f"{spec.world}` in {server_dir} has what it printed."
            )
        yield "The server is up."

    # -- plumbing --------------------------------------------------------

    def _clone(self, spec: git.CloneSpec) -> None:
        try:
            self._seams.clone(spec)
        except git.GitError as exc:
            raise InstallerError(f"Cloning {spec.url} failed: {exc}") from exc

    def _remote_of(self, dest: Path) -> str | None:
        """What `origin` points at in an existing checkout at `dest`; None if there is none."""
        if not (dest / ".git").is_dir():
            return None
        ask = self._seams.remote_url if self._seams.remote_url is not None else _git_remote_url
        return ask(dest)

    def _pump(
        self, call: Callable[[docker.OutputSink], docker.AttachedRun]
    ) -> Generator[str, None, docker.AttachedRun]:
        """Turn a push-style docker call into yielded lines, without buffering the run.

        `docker.run_attached()` pushes lines into a sink (the shape the repair
        button's UI needs) and this engine pulls them (the shape `run()`'s
        contract needs). A queue between a worker thread and this generator is
        the whole bridge: nothing is collected into a list first, so a
        four-hour build appears line by line rather than at the end.
        """
        lines: queue.Queue[str | None] = queue.Queue()
        outcome: list[docker.AttachedRun] = []
        failure: list[BaseException] = []

        def work() -> None:
            try:
                outcome.append(call(lines.put))
            except BaseException as exc:  # noqa: BLE001 - re-raised on the caller's thread below
                failure.append(exc)
            finally:
                lines.put(None)

        worker = threading.Thread(target=work, daemon=True, name="yulon-install-output")
        worker.start()
        while True:
            item = lines.get()
            if item is None:
                break
            yield item
        worker.join()
        if failure:
            raise InstallerError(f"the command could not be run: {failure[0]}") from failure[0]
        return outcome[0]

    def _check_run(
        self,
        run: docker.AttachedRun,
        what: str,
        cancel: threading.Event | None,
        note: str,
    ) -> None:
        """`note` is what a Stop costs FOR THIS STAGE, and only this stage.

        Required rather than defaulted, because there is no sentence about
        keeping work that is true of the build, the download and the import
        alike — and the copy that was true of one was being said for all three.
        A default here is the shape that mistake had.
        """
        if run.returncode == docker.CANCELLED_RETURNCODE:
            raise InstallerError(_cancelled_message(what, note))
        if run.returncode != 0:
            raise InstallerError(
                f"{what} failed (exit {run.returncode}). Its last words were: "
                f"{docker.last_words(run.tail)}"
            )
        self._check_cancel(cancel)

    def _check_cancel(self, cancel: threading.Event | None) -> None:
        if cancel is not None and cancel.is_set():
            raise InstallerError(_cancelled_message("the install"))

    @contextmanager
    def _held_awake(self) -> Iterator[str]:
        """Hold the machine awake for the stages that take hours, and say if we cannot.

        Yields the sentence to put in the log, empty when the assertion was
        actually taken.

        A failure to assert it is not a reason to refuse an install: on Windows
        the assertion is per-thread and `platform.keep_awake()` refuses the main
        thread outright, which is right for the app (the install runs on a
        worker) and wrong to abort a command-line run over. The engine says so
        in its output instead of promising something it did not get.

        Written with an `ExitStack` so the `except` covers ONLY entering the
        context. Wrapping the `yield` too would have swallowed every
        `InstallerError` a stage raises — `InstallerError` is a `RuntimeError`
        — and turned a failed build into a sleep warning.
        """
        with ExitStack() as stack:
            note = ""
            try:
                stack.enter_context(self._seams.keep_awake())
            except RuntimeError as exc:
                logger.warning(f"not holding this machine awake: {exc}")
                note = (
                    "This machine may go to sleep during the build; leave it awake, and leave "
                    "the lid open on a laptop."
                )
            yield note

    def _record_error(self, server_dir: Path, state: InstallState, message: str) -> None:
        if not (server_dir / STATE_FILE).is_file():
            return
        write_state(server_dir, replace(state, last_error=message))


def _cancelled_message(what: str, note: str = "") -> str:
    """ "<what> was stopped", plus whatever is TRUE of the stage that was stopped.

    No note is the honest default. A cancel between stages has nothing to add
    beyond `OPENING_NOTE`, which the user was already told.
    """
    return f"{what} was stopped. {note}".rstrip()


def _same_repo(existing: str, wanted: str) -> bool:
    """Do two clone URLs name the same repository?

    Compared loosely on purpose: `https://github.com/x/y.git`,
    `https://github.com/x/y` and `git@github.com:x/y.git` are one repository,
    and refusing an install because git wrote the URL back with a `.git` on it
    would be a refusal about punctuation.
    """
    return _repo_key(existing) == _repo_key(wanted)


def _repo_key(url: str) -> str:
    text = url.strip().rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    for prefix in ("https://", "http://", "ssh://", "git@"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
    return text.replace(":", "/").lower()


def _git_remote_url(dest: Path) -> str | None:
    """`git remote get-url origin`, run inside a container so no host git is needed.

    The default for the `remote_url` seam. It is a `git` question, and this
    engine's whole point on macOS and Windows is that the machine may not have
    one — so it goes through the same containerized git the clones use.
    """
    return git.ContainerGit().remote_url(dest)
