"""The install spine: named, resumable stages, game-free (roadmap 6.2 → 7.1).

One typed engine for every server on every platform. `StagedInstaller` owns
what is true of every install — the state file and its hint semantics, the
directory/ownership guard, preflight and Docker provisioning, the
refuse-not-delete clone safety, the compose marker rules, streaming, cancel
copy, keep-awake — and a FAMILY (`families/azerothcore.py`, `families/cmangos.py`)
composes its stages into an ordered tuple. `installer.installer_for()` picks
the family from `catalog.json`'s `install.native.family`. The contract is the
same as `installer.Installer`'s (`run(options, *, cancel, ask) -> Iterator[str]`),
so the catalog view, the log panel and the job runner need no changes.

**Staged and resumable.** The stages are recorded by NAME in
`.yulon-install.json`, so reordering them can never re-interpret an existing
install. The rules below each cost the earlier Rust launcher an evening
(`pyplan/rust-prior-art.md` §1), and they are the reason this file is more
careful than its length suggests:

* preflight and the guard are not stages: the spine runs them itself, so a
  family can neither forget nor record them — a guard a resume skips is not a
  guard. A `Stage` with `recorded=False` (`start-db`, `up`, `ready`) is run by
  every resume: an install must end by actually starting and verifying the
  server, and the database must be back up before the import can ask it
  anything.
* **The state file is a hint.** Every stage re-checks disk evidence before
  skipping: the clone stages ask git for the remote, the build asks the daemon
  for images, compose generation reads its own marker. An `is_done`
  short-circuit once let a state file dropped into a directory make the
  generator rewrite a real server's compose file and orphan its character
  volumes.
* `install_id` is a hash of the ABSOLUTE server directory, so a COPIED install
  directory is refused rather than adopted; `family` is recorded too, so a
  catalog edit that moves a game between families is a refusal, never a
  reinterpretation.
* A failure mid-stage records nothing, so the stage re-runs.
* A stage's cancel note is said by the spine, right after `--- <name>`, and
  by nothing else.

**Nothing on this path prompts for its own decisions.** Exactly two questions
pass THROUGH it, both inside Docker provisioning before stage 1 and both via
the forwarded `ask`: the docker-group consent and, on Linux, the sudo password.
A stage that turns out to need an answer is a design failure to fix rather
than a dialog to add.

**Verified where, exactly.** Everything here is unit-tested against seams;
docstrings say which claims are inherited from the Rust launcher's incidents,
which are measured on yulon-ubuntu (Linux), and which are merely written.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections.abc import Callable, Generator, Iterator, Sequence
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from secrets import token_hex
from typing import ClassVar, Protocol

from yulon import docker, git, platform, resources, runner
from yulon.catalog import composegen, preflight
from yulon.catalog.catalog import CatalogEntry, EmulatorSource, NativeInstall, ReadyMarkers
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
2026-08-23). The `partial` branch of `stage_import()` is what makes this sentence
true; without it the honest copy would be the opposite.
"""

INSTALL_REALM_HOST = "127.0.0.1"
"""The realm address every fresh install advertises, and what the `ready` stage expects.

A fresh install's realmlist row is the loopback address and the world port —
AzerothCore's default row, and the row the CMaNGOS plan writes — so the auth
log's own line is what the catalog's `ready.auth` marker names through
`{{REALM_HOST}}:{{WORLD_PORT}}`. Public: `families/cmangos.py` fills its
templates and SQL from the same constant (A3). The Networking tab is what
changes it afterwards; the engine never does.
"""


@dataclass(frozen=True)
class InstallState:
    """`.yulon-install.json`: what a previous run of THIS install got through.

    A hint and a claim of ownership, never an authority on what is on disk.
    `install_id` is what makes it a claim: it is derived from the absolute path
    of the directory holding it, so a state file copied into another directory
    describes an install that is not there.

    `family` (7.1) is the second claim: a folder installed as one emulator
    family is never reinterpreted as another by a catalog edit — the guard
    refuses instead. Empty in files written before 7.1, which the guard reads
    as the entry's current family; `version` stays 1 because the key is
    additive.
    """

    game_id: str
    install_id: str
    family: str = ""
    completed: tuple[str, ...] = ()
    last_error: str = ""
    updated_unix: int = 0
    version: int = STATE_VERSION

    def with_stage(self, stage: str, order: Sequence[str]) -> InstallState:
        """This state plus `stage`, in `order`, with nothing recorded twice.

        `order` is the ENTRY's stage tuple rather than a module constant
        because 7.1 has two families with two tuples. A name outside it is
        dropped — the same rule `read_state()` applies on the way in — so the
        file can never carry a name nobody can interpret.
        """
        if stage in self.completed:
            return self
        done = set(self.completed) | {stage}
        return replace(self, completed=tuple(s for s in order if s in done), last_error="")

    def has(self, stage: str) -> bool:
        """Did a previous run finish `stage`? Never a reason to skip on its own."""
        return stage in self.completed


def read_state(server_dir: Path, *, valid: Sequence[str]) -> InstallState | None:
    """The state file in `server_dir`, or None if there is none this engine wrote.

    An unreadable or malformed file answers None: it is a hint, and a hint that
    cannot be parsed is simply no hint. It is never deleted here — a file this
    engine cannot read may be somebody else's, and `guard` is what decides
    whether the directory can be used at all.

    `valid` is the entry's stage tuple: a name outside it is dropped rather
    than kept, so a stage that no longer exists can never become a skip.
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
        tuple(stage for stage in completed if isinstance(stage, str) and stage in valid)
        if isinstance(completed, list)
        else ()
    )
    # A missing or non-string `family` reads as "" — the one value that means
    # "this file does not say". It is never a family name, so it can never
    # match the wrong one; the guard treats it as the entry's own family, which
    # is safe because every state file written before 7.1 was written by the
    # only family that existed then.
    family = parsed.get("family")
    error = parsed.get("last_error")
    updated = parsed.get("updated_unix")
    version = parsed.get("version")
    return InstallState(
        game_id=game_id,
        install_id=install_id,
        family=family if isinstance(family, str) else "",
        completed=stages,
        last_error=error if isinstance(error, str) else "",
        updated_unix=updated if isinstance(updated, int) else 0,
        version=version if isinstance(version, int) else STATE_VERSION,
    )


def write_state(server_dir: Path, state: InstallState) -> None:
    """Write the state file atomically, never leaving a half-written one behind.

    A truncated state file is worse than none: `read_state()` would answer None
    and the resume would redo a two-hour build it had already finished.

    The directory is created if it is not there. It always was under the 6.2
    stage order, where `clone-core` made it before anything was recorded; a
    family whose first recorded stage writes nothing to disk (7.3's
    `db-password`) would otherwise have its record silently dropped and redo
    that stage on every resume.
    """
    path = server_dir / STATE_FILE
    payload = {
        "version": state.version,
        "game_id": state.game_id,
        "family": state.family,
        "install_id": state.install_id,
        "completed": list(state.completed),
        "last_error": state.last_error,
        "updated_unix": int(time.time()),
    }
    tmp = path.with_name(path.name + ".new")
    try:
        server_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")
        os.replace(tmp, path)  # atomic on POSIX and on Windows
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.warning(f"could not record install progress in {path}: {exc}")


@dataclass(frozen=True)
class Secrets:
    """What a stage may need that must never be printed: the database password.

    A holder rather than a bare `str` so a `StageContext` can be logged or
    land in a traceback without the value going with it — `repr()` masks it
    on purpose, and there is no `__str__` that gives it back.
    """

    db_password: str

    def __repr__(self) -> str:
        return "Secrets(db_password=***)"


@dataclass(frozen=True)
class StageContext:
    """Everything a stage body is handed. Frozen: a stage reads, the spine decides.

    `secrets` is resolved by the spine BEFORE the first stage (see
    `StagedInstaller.resolve_secrets()`), so a frozen context can carry it and
    no stage has to know where the password came from.
    """

    server_dir: Path
    client_dir: Path | None
    state: InstallState
    cancel: threading.Event | None
    secrets: Secrets


@dataclass(frozen=True)
class Stage:
    """One named, resumable step: data plus a bound callable, not a name in an if-chain.

    `name` is what `.yulon-install.json` records, so renaming one reinterprets
    every state file in the wild — the AzerothCore names are pinned by a test
    for that reason. `recorded=False` is the old `NEVER_RECORDED`: a resume
    must always run it again. `cancel_note` is what a Stop costs HERE and only
    here; the SPINE says it right after `--- <name>`, and no stage body yields
    its own, so no family can say the build's sentence over the download.
    """

    name: str
    run: Callable[[StageContext], Iterator[str]]
    recorded: bool = True
    cancel_note: str = ""


class ImportGate(Protocol):
    """What the import stage asks of a database: its state, and a way to clear a half-written one.

    Family-neutral: AzerothCore answers through the injected `acore_*` probe
    pair (`CallableGate`), CMaNGOS through its own marker table (7.3). The
    stage's five-branch table is the same for both.
    """

    def probe(self) -> docker.ImportState: ...

    def reset(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class CallableGate:
    """An `ImportGate` from the two callables the app already injects for AzerothCore.

    `reset_fn` may be None — an engine built without a reset seam — and then
    `reset()` is the refusal the old `_import()` made inline: a half-written
    database with no way to clear it is a stop, not a guess.
    """

    probe_fn: docker.ImportProbe
    reset_fn: docker.ResetUnfinished | None

    def probe(self) -> docker.ImportState:
        return self.probe_fn()

    def reset(self) -> tuple[str, ...]:
        if self.reset_fn is None:
            raise InstallerError(
                "This install's databases were left half-written and this installer has no way "
                "to clear them, so nothing was run."
            )
        return self.reset_fn()


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
    images_built: Callable[[Sequence[str]], bool | None] = docker.images_built
    build: Callable[..., docker.AttachedRun] = docker.build_staged
    one_shot: Callable[..., docker.AttachedRun] = docker.run_one_shot
    verify_import: Callable[..., docker.ImportState] = docker.verify_import
    container_exists: Callable[[str], bool] = docker.container_exists
    container_project: Callable[[str], str | None] = docker.container_project
    start_db: Callable[[docker.ContainerSpec, Path], None] = docker.start_database
    start: Callable[[docker.ContainerSpec, Path], bool] = docker.start_staged
    wait_db_healthy: Callable[[docker.ContainerSpec], bool] = docker.wait_db_healthy_for
    wait_ready: Callable[[docker.ContainerSpec, docker.ReadySpec], bool] = docker.wait_ready_for
    keep_awake: Callable[[], AbstractContextManager[None]] = platform.keep_awake


class StagedInstaller:
    """Abstract spine: everything an install needs that is not about one emulator.

    Subclasses implement `stages()` and nothing else is required of them. The
    spine runs preflight, the guard and keep-awake itself, then each stage in
    the family's order, recording the ones the family says to record.

    Constructed by `installer.installer_for()`, which is the only thing that
    decides between families. `import_probe`/`reset_unfinished` are supplied
    by the CALLER (the app wires `controller_wow_wotlk.repair`), because they
    are per-game facts and `catalog/` must not import a controller package —
    the same shape `controller_view.py` already uses.
    """

    family: ClassVar[str]
    """The `install.native.family` this class installs; asserted against the entry in preflight."""

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
        self._check_stage_tuple()

    # -- the family's contract -------------------------------------------

    def stages(self) -> tuple[Stage, ...]:
        """The ordered stages, each bound to a body. Names unique; never `preflight`/`guard`."""
        raise NotImplementedError(f"{type(self).__name__} must define its stages")

    def stage_names(self) -> tuple[str, ...]:
        """The names `stages()` records, in order — what `read_state()` validates against."""
        return tuple(stage.name for stage in self.stages())

    def _check_stage_tuple(self) -> None:
        """Refuse a broken family at construction, not after a two-hour build.

        A `ValueError`, not an `InstallerError`: this is a bug in a family
        class, never something a user did (A8). A repeated name would let one
        stage's record skip another; `preflight` and `guard` are the spine's
        own and must never be recordable.
        """
        names = self.stage_names()
        if len(set(names)) != len(names):
            raise ValueError(f"{type(self).__name__} lists a stage twice: {names}")
        reserved = [name for name in ("preflight", "guard") if name in names]
        if reserved:
            raise ValueError(
                f"{type(self).__name__} may not name a stage {reserved[0]!r}: the spine owns it"
            )

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
        ctx = StageContext(
            server_dir=server_dir,
            client_dir=opts.client_dir,
            state=state,
            cancel=cancel,
            secrets=self.resolve_secrets(server_dir),
        )

        try:
            with self._held_awake() as note:
                if note:
                    yield note
                for stage in self.stages():
                    self._check_cancel(cancel)
                    yield f"--- {stage.name}"
                    if stage.cancel_note:
                        # The spine says it, once, here (A4); no body yields its own.
                        yield stage.cancel_note
                    state = yield from self._run_one(stage, replace(ctx, state=state))
        except InstallerError as exc:
            # Recorded only into a state file that already exists. Creating one
            # to hold an error would make the state file itself the content
            # that stops `guard` calling this directory empty — so the retry
            # would be refused by the record of the failure it is retrying.
            self._record_error(server_dir, state, str(exc))
            raise
        # The bash path logs `install of <id> finished` (installer.py); this path
        # logged nothing at the end, so the only sign a run had ended was the
        # compose-project pin - which is how a tester on yulon-win11 (2026-08-28)
        # read a seven-minute readiness wait as "the install was not remembered".
        logger.info(f"install of {self.entry.id} finished")
        yield f"{self.entry.name} is installed and running in {server_dir}"

    def _run_one(self, stage: Stage, ctx: StageContext) -> Generator[str, None, InstallState]:
        """Run one stage and, if the family says so, write it down."""
        yield from stage.run(ctx)
        if not stage.recorded:
            return ctx.state
        recorded = ctx.state.with_stage(stage.name, self.stage_names())
        write_state(ctx.server_dir, recorded)
        return recorded

    def resolve_secrets(self, server_dir: Path) -> Secrets:
        """The database password this install uses, decided before stage 1.

        `fixed` is the catalog value (WotLK's `password` — a contract with
        backup, console and every guide). `generated` reads the file a previous
        run persisted (written with a trailing newline, read with `.strip()`),
        else mints `<prefix><16 hex>`; persisting it is the family's
        `db-password` stage (7.3), not this method's job, so a preflight
        refusal after this point leaves no secret on disk.

        A file that is already there is taken AS WRITTEN. `prefix` decorates a
        value this app mints; it is not a shape an existing password has to
        have, and a shipped bash installer minted the same passwords without
        the dash `catalog.json` now carries.
        """
        plan = self.entry.install.password
        if plan.mode == "fixed":
            if plan.value is None:
                raise InstallerError(
                    f"{self.entry.name}'s catalog entry says its database password is fixed "
                    "but gives no value. That is a bug in the app."
                )
            return Secrets(plan.value)
        if plan.file is None:
            raise InstallerError(
                f"{self.entry.name}'s catalog entry says its database password is generated "
                "but names no file to keep it in. That is a bug in the app."
            )
        path = server_dir / plan.file
        try:
            return Secrets(path.read_text(encoding="utf-8").strip())
        except FileNotFoundError:
            return Secrets(f"{plan.prefix}{token_hex(8)}")
        except OSError as exc:
            raise InstallerError(
                f"{path} exists but could not be read ({exc}), so this install cannot know its "
                "own database password. Nothing was started."
            ) from exc

    def _native(self) -> NativeInstall:
        """The entry's `install.native` block; preflight has already refused its absence."""
        native = self.entry.install.native
        if native is None:
            raise InstallerError(
                f"{self.entry.name} has no `install.native` section, so nothing was started."
            )
        return native

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
        if self.entry.install.native.family != self.family:
            raise InstallerError(
                f"{self.entry.name} is catalogued as a `{self.entry.install.native.family}` "
                f"install but was handed to the `{self.family}` engine. That is a bug in the "
                "app, not something to fix on this machine."
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
        yield "Checking Docker."
        if not self._seams.docker_ready():
            # Provisioning prints nothing of its own and can be a Docker
            # Desktop download followed by a three-minute readiness poll. The
            # first macOS tester watched an empty panel through all of it and
            # reported the install as silently dead (macOS gate, 2026-08-25).
            yield (
                "Docker is not answering yet. Setting it up - this can mean downloading "
                "Docker Desktop and waiting for its engine, up to a few minutes with no "
                "output. You can stop at any time."
            )
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
                client_dir=options.client_dir,
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
        a different compose project. It must also be ours by `family`: a
        catalog edit that moves a game between families is a refusal here,
        never a reinterpretation.
        """
        install_id = composegen.install_id(server_dir, platform_id=self._seams.platform_id)
        existing = read_state(server_dir, valid=self.stage_names()) if server_dir.is_dir() else None
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
        if existing is not None and existing.family and existing.family != self.family:
            raise InstallerError(
                f"{server_dir} was installed as `{existing.family}`, but the catalog now says "
                f"{self.entry.name} is `{self.family}`. A folder is never reinterpreted: pick "
                "another folder, or remove that install first."
            )
        if existing is not None and not existing.family:
            # Written before 7.1: ours, and the next write says which family.
            existing = replace(existing, family=self.family)
        if existing is None and server_dir.is_dir() and not (server_dir / ".git").is_dir():
            # A directory that is a git checkout is deliberately NOT judged
            # here: the family's clone stage can ask whose it is, and "this is
            # a checkout of somebody else's fork" is a far better sentence than
            # "this folder is not empty". Everything else is refused before a
            # byte is written.
            leftovers = [item.name for item in server_dir.iterdir() if item.name != STATE_FILE]
            if leftovers:
                raise InstallerError(
                    f"{server_dir} is not empty and was not created by this app "
                    f"({', '.join(sorted(leftovers)[:5])}). Nothing was written. Pick an empty "
                    "folder, or remove that one yourself if you no longer want it."
                )
        self._refuse_foreign_containers(server_dir, install_id)
        return existing or InstallState(
            game_id=self.entry.id, install_id=install_id, family=self.family
        )

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

    # -- stage bodies a family binds into `Stage.run` --------------------

    def stage_clone_sources(
        self, ctx: StageContext, sources: Sequence[EmulatorSource]
    ) -> Iterator[str]:
        """Clone (or update) every source at its `dest`, refusing what is not ours.

        Disk evidence beats the state file: a `.git` whose `origin` is this
        source is an existing clone and gets fetch+reset through the seam's
        own update path; a `.git` pointing somewhere else is refused BY NAME
        and never deleted, because a directory holding somebody's fork is not
        this installer's to remove. A directory with files and no `.git` is
        refused too: the clone seam `shutil.rmtree`s a destination it does
        not recognise, and a tree a user unpacked by hand must not fall
        through (review, 2026-08-23). `dest == "."` is the server dir itself,
        where the state file is the one leftover that does not count.
        """
        if not sources:
            yield "This server has nothing to clone."
            return
        for source in sources:
            dest = ctx.server_dir / source.dest
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
            if not has_git and dest.is_dir():
                leftovers = [item.name for item in dest.iterdir() if item.name != STATE_FILE]
                if leftovers:
                    raise InstallerError(
                        f"{dest} has files in it but is not a checkout of {source.url}, so it "
                        "was left alone. Move that folder aside and try again."
                    )
            yield f"Cloning {source.repo} into {source.dest}"
            if existing is not None:
                yield "It is already cloned; updating it instead."
            self._clone(
                git.CloneSpec(
                    url=source.url,
                    dest=dest,
                    branch=source.branch,
                    sparse_path=source.sparse_path,
                    depth=source.depth,
                )
            )
        yield "Sources are in place."

    def stage_generate_compose(self, ctx: StageContext) -> Iterator[str]:
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
            ctx.server_dir,
            templates_root=self.installers_root,
            db_password=ctx.secrets.db_password,
            platform_id=self._seams.platform_id,
        )
        replaceable = self._replaceable_compose(ctx.server_dir)
        if replaceable:
            yield (
                f"Replacing the {composegen.BASE_FILE} that came with the repository; it is "
                "unchanged from what git has, so `git checkout` brings it back."
            )
        try:
            written = composegen.write_plan(plan, ctx.server_dir, replaceable=replaceable)
        except composegen.ComposeGenError as exc:
            raise InstallerError(str(exc)) from exc
        except OSError as exc:
            raise InstallerError(f"the compose files could not be written: {exc}") from exc
        if not written:
            yield "The compose files are already exactly what this install needs."
        for path in written:
            yield f"Wrote {path.name}"

    def stage_build(self, ctx: StageContext) -> Iterator[str]:
        """Compile the server. Hours, and the one stage whose output is worth watching.

        The state file alone never skips this: the daemon is asked whether this
        install's image references exist, and only "they all do" plus a
        recorded build counts. A daemon that will not answer is not "no images"
        — it is unknown, and an unknown re-runs the build, which is slow and
        safe rather than fast and wrong.

        It used to ask `compose images -q`, which cannot answer the question:
        compose enumerates the images of a project's CREATED CONTAINERS, so in
        the built-but-not-yet-up window this runs in it returned nothing and
        every resume re-ran the compile (measured 2026-08-24; see
        `docker.images_built()`).

        The `BUILD_CANCEL_NOTE` this stage used to yield is gone from the body:
        the spine says a stage's cancel note right after `--- <name>` (A4).
        """
        built = self._seams.images_built(
            composegen.built_image_refs(
                self.entry, ctx.server_dir, platform_id=self._seams.platform_id
            )
        )
        if ctx.state.has("build") and built:
            yield "The server is already built; skipping the compile."
            return
        if ctx.state.has("build") and built is None:
            yield "Docker would not say whether this install is built, so it is being rebuilt."
        yield "Building the server. This takes hours on a first install; the output below is live."
        run = yield from self._pump(
            lambda sink: self._seams.build(
                ctx.server_dir, composegen.COMPOSE_FILES, sink=sink, cancel=ctx.cancel
            )
        )
        self._check_run(run, "the build", ctx.cancel, BUILD_CANCEL_NOTE)
        yield "The build finished."

    def stage_start_db(self, ctx: StageContext) -> Iterator[str]:
        """Bring the database up alone, before the import stage asks it anything.

        Not an optimisation and not tidiness — without it the install cannot
        finish. `stage_import()` probes first, and the real probe is a `docker
        exec <db container> mysql …`; with no such container it raises and the
        probe answers `unreadable`, which `stage_import()` turns into a hard
        refusal. So the fresh install died at the import stage AFTER the
        multi-hour build, every time, and every resume died in the same place
        (review, 2026-08-23). Running the one-shot anyway would not have
        helped: `run_one_shot()` passes `--no-deps`, which prunes exactly the
        `depends_on: <db>: condition: service_healthy` edge the generated base
        file declares on the import service.

        `pyplan/phase6-decisions.md` had already settled this for the repair
        button — "The action starts the database, and that is a deliberate
        widening of 'runs only the one-shot service'… Without it the action is
        unreachable" — and `docker.start_database()` is that same code, shared
        so the two paths cannot drift.

        Never recorded: it is a precondition, not progress, and it returns
        immediately when the container is already up.

        Unconditional (A7): every family's import needs the database up, and
        CMaNGOS has no `db_import` service at all. The AzerothCore family keeps
        its no-service short-circuit in its own wrapper.
        """
        yield "Starting the database, which the import writes into."
        try:
            self._seams.start_db(self.entry.container_spec(), ctx.server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(
                f"The database could not be started, so nothing was imported: {exc}"
            ) from exc
        yield "The database is up."

    def stage_import(
        self, ctx: StageContext, gate: ImportGate, service: str | None
    ) -> Iterator[str]:
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
        with nothing wrong with it. See `stage_start_db()`.

        The five-branch table ALWAYS runs first (A7). With `service` given the
        compose one-shot and `verify_import` follow, as before; with `service`
        None this returns after the table and the family applies the SQL
        itself, re-probes and writes its own marker (7.3).
        """
        before = gate.probe()
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
            yield f"Clearing the half-written databases first ({before.detail})."
            dropped = gate.reset()
            if not dropped:
                raise InstallerError(
                    "The databases read as unfinished, but nothing was found to clear, so the "
                    "import was not run. Nothing was changed."
                )
            yield f"Cleared {', '.join(dropped)}."
        if service is None:
            return
        yield f"Importing the databases ({service}). This takes several minutes."
        run = yield from self._pump(
            lambda sink: self._seams.one_shot(service, ctx.server_dir, sink=sink, cancel=ctx.cancel)
        )
        if run.returncode == docker.CANCELLED_RETURNCODE:
            raise InstallerError(_cancelled_message("the database import", IMPORT_CANCEL_NOTE))
        try:
            after = self._seams.verify_import(gate.probe, service, ctx.server_dir, run)
        except docker.DockerCommandError as exc:
            raise InstallerError(str(exc)) from exc
        yield f"The databases now read as {after.state}."

    def stage_up(self, ctx: StageContext) -> Iterator[str]:
        """Start the three long-running services, and only those.

        Never recorded: a resume has to end with the server actually running,
        and this is cheap. `start_staged()` names the services explicitly so
        `up` can never select the one-shot import — the thing `dml-start.sh`
        warns about in as many words.
        """
        yield "Starting the server."
        try:
            self._seams.start(self.entry.container_spec(), ctx.server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(f"The server would not start: {exc}") from exc

    def stage_ready(self, ctx: StageContext) -> Iterator[str]:
        """Wait until the database is healthy and both servers have said they are up.

        Nothing new: `wait_db_healthy_for()` polls the container's health
        status and reads no logs at all, and `wait_ready_for()` already polls
        `StartedAt` and reads `logs --since` that timestamp rather than `--tail`
        — the marker prints once and scrolls out of any tail window on a busy
        playerbots boot, which this project and the Rust launcher hit
        independently on the same day. What it waits FOR is catalog data now
        (`install.native.ready`), filled through the same `fill()` as the
        compose templates so a typo is an error and not a 600-second timeout.

        The give-up sentence names both logs and the crash loop on purpose.
        `wait_ready()` has four ways to answer False — the timeout, a `fatal`
        line, a crash loop under `restart: unless-stopped`, and a missing
        docker CLI — and it tells the caller only `False`. The world log is
        where three of them show; a crash loop shows as a container that keeps
        coming back, which `docker compose ps` says and a log tail does not.

        Two known gaps in that wait, recorded rather than fixed in 7.1 because
        `docker.wait_ready()` is not this task's to re-home: the crash-loop
        latch counts the WORLD container's restarts only, so an auth container
        in a crash loop sits out the whole timeout, and `spec.fatal` is
        searched in the world log only, so a fatal line an auth server prints
        is not seen. Both are conservative — they make the wait slower to give
        up, never quicker to call a dead server ready — which is why they are
        an entry in the checklist and not a blocker here.
        """
        spec = self.entry.container_spec()
        yield "Waiting for the database."
        if not self._seams.wait_db_healthy(spec):
            raise InstallerError(
                f"The database never reported healthy. `docker compose logs "
                f"{spec.service_for(spec.db)}` in {ctx.server_dir} will say why."
            )
        yield "Waiting for the world server to finish loading (this can take many minutes)."
        if not self._seams.wait_ready(spec, self._ready_spec(self._native().ready)):
            raise InstallerError(
                f"The server started but never reported ready. `docker compose logs "
                f"{spec.service_for(spec.world)}` in {ctx.server_dir} has what it printed, and "
                f"`docker compose ps` says whether it is restarting over and over — a server "
                f"that keeps crashing on boot never reaches the line this waits for."
            )
        yield "The server is up."

    def _ready_spec(self, markers: ReadyMarkers) -> docker.ReadySpec:
        """`ReadyMarkers` with `{{REALM_HOST}}`/`{{WORLD_PORT}}` filled, then made a regex.

        `wait_ready()` searches the log with `re.search`, so a literal marker
        (`regex: false`, the default) is `re.escape`d after filling — otherwise
        the `.` in `127.0.0.1` is a wildcard, the very thing
        `docker.azerothcore_ready()` escapes (A5). Tortoise's alternations set
        `regex: true` and are handed over as written.

        Every pattern is COMPILED here, where `catalog.json` can still be named
        as the thing to fix. `wait_ready()` calls `re.search` inside its poll
        loop, so a `regex: true` marker with an unbalanced group would raise
        `re.error` in the middle of the last stage of an install — after the
        clone, the build and the import — and read as a crash rather than as a
        typo in a data file (A.2 review finding).
        """
        tokens = {"REALM_HOST": INSTALL_REALM_HOST, "WORLD_PORT": str(self.entry.ports.world)}

        def marker(text: str) -> str:
            filled = composegen.fill(text, tokens)
            pattern = filled if markers.regex else re.escape(filled)
            try:
                re.compile(pattern)
            except re.error as exc:
                raise InstallerError(
                    f"{self.entry.name}'s ready marker {text!r} is not a usable pattern "
                    f"({exc}). Fix `install.native.ready` in catalog.json; nothing was started."
                ) from exc
            return pattern

        try:
            world = marker(markers.world)
            auth = marker(markers.auth) if markers.auth is not None else None
            fatal = marker(markers.fatal) if markers.fatal is not None else None
        except composegen.ComposeGenError as exc:
            raise InstallerError(f"{self.entry.name}'s ready markers are broken: {exc}") from exc
        # The catalogue's `timeout_s` wins over `docker.ReadySpec`'s own 480s
        # default, which covers only a spec written in Python. Data beats a
        # constant wherever there is data, and this is the only place the two
        # numbers meet.
        return docker.ReadySpec(
            world=world,
            auth=auth,
            fatal=fatal,
            timeout=float(markers.timeout_s),
            restart_loop=markers.restart_loop,
        )

    # -- plumbing --------------------------------------------------------

    def _replaceable_compose(self, server_dir: Path) -> tuple[str, ...]:
        """`docker-compose.yml`, when git proves it is the one the clone wrote. See above."""
        path = server_dir / composegen.BASE_FILE
        if not path.exists() or composegen.is_ours(path):
            return ()
        if self._seams.file_unmodified(server_dir, composegen.BASE_FILE):
            return (composegen.BASE_FILE,)
        return ()

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
