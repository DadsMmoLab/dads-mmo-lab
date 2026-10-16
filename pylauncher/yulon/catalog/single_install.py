"""The single-container install engine: one checkout, one image, one container (`SingleEntry`).

The catalog's second kind of entry has no database container, no auth/world split
and no import, so it does not ride `native.StagedInstaller`, whose stages, guards
and realm advertising are written around exactly those. What the two engines share
is taken from the same places, so it cannot drift:

* the state file and its claim of ownership — `native.InstallState`,
  `read_state()`/`write_state()`, `STATE_FILE`, `OUR_OWN_FILES`;
* the install id and the compose project name — `composegen.install_id()`,
  `composegen.project_name()`, and the `{{TOKEN}}` fill, `composegen.fill()`;
* the Dockerfile renderer and its refusal to overwrite a file it did not write —
  `families.dockerfile`;
* the clone seam, which needs no git on the host — `git.ContainerGit`;
* Docker provisioning and the sentences that report it — `platform.ensure_docker`,
  `installer.provision_lines()`, `installer.docker_unavailable()`;
* the one builder and the bridge that streams it — `docker.build_staged()`,
  `native.pump_lines()`.

The stages are named, recorded and resumable as the spine's are: a second press of
Install on the same folder skips what finished, and says so.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from yulon import docker, git, platform, resources, runner
from yulon.catalog import composegen, native
from yulon.catalog.catalog import SingleEntry, SingleNative
from yulon.catalog.families import dockerfile
from yulon.catalog.installer import (
    DockerUnavailableError,
    InstallerError,
    InstallOptions,
    InstallStopped,
    UnsupportedPlatformError,
    docker_unavailable,
    provision_lines,
    unsupported_platform_message,
)
from yulon.log import get_logger

logger = get_logger(__name__)

STAGE_NAMES: tuple[str, ...] = (
    "clone-sources",
    "write-dockerfile",
    "seed-files",
    "generate-compose",
    "build",
    "up",
    "ready",
)
"""The stages, in order. What `read_state()` validates a state file's `completed` against."""

READY_POLL_SECONDS = 2.0
"""How often the ready wait reads the container's current-run log."""

NO_SECRETS = native.Secrets(db_password="")
"""What `dockerfile.render()` is told this install's secrets are: none.

A single-container server of this family keeps its data in SQLite inside a named
volume and has no database password, so there is nothing a build context could leak.
`dockerfile.carries_a_secret()` answers False for an empty secret, which is the
honest reading of "this install has none".
"""


@dataclass(frozen=True)
class SingleSeams:
    """Everything the single-container engine reaches outside itself through. Real by default.

    For `native.Seams`' reason: an engine whose every external effect is a seam can
    have its control flow tested without a daemon, a network or a build.
    """

    platform_id: Callable[[], str] = platform.detect
    docker_ready: Callable[[], bool] = platform.docker_ready
    ensure_docker: Callable[..., platform.ProvisionReport] = platform.ensure_docker
    dir_problem: Callable[[Path], str | None] = platform.server_dir_problem
    clone: Callable[[git.CloneSpec], None] = field(default_factory=lambda: git.ContainerGit().clone)
    remote_url: Callable[[Path], str | None] = field(
        default_factory=lambda: git.ContainerGit().remote_url
    )
    port_conflicts: Callable[[tuple[int, ...]], list[str]] = docker.port_conflicts
    container_exists: Callable[[str], bool] = docker.container_exists
    container_project: Callable[[str], str | None] = docker.container_project
    images_built: Callable[[Sequence[str]], bool | None] = docker.images_built
    build: Callable[..., docker.AttachedRun] = docker.build_staged
    up: Callable[..., None] = docker.compose_up_service
    run_log: Callable[[str], docker.RunLog] = docker.current_run_log
    state_of: Callable[[str], docker.ContainerState] = docker.container_state
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


def ready_lines(entry: SingleEntry) -> tuple[str, ...]:
    """The entry's ready lines with each port's `PORT_<ROLE>` filled in (either runtime's)."""
    tokens = entry.port_tokens()
    return tuple(composegen.fill(line, tokens) for line in entry.install.runtime.ready)


def container_parts(entry: SingleEntry) -> tuple[SingleNative, str, str]:
    """A container entry's `native` block, container and service; refuses a `process` entry.

    Asked once, where an engine or a controller is built, so nothing below it holds
    a `None` it would have to ask about again.
    """
    native_block = entry.install.native
    if native_block is None or entry.container is None or entry.service is None:
        raise ValueError(f"{entry.id} runs no container, so it has no container install")
    return native_block, entry.container, entry.service


def missing_ready_lines(entry: SingleEntry, log_text: str) -> tuple[str, ...]:
    """Which of the entry's ready lines a log does not contain yet; () once it is up."""
    return tuple(line for line in ready_lines(entry) if line not in log_text)


def _read_record(
    entry: SingleEntry,
    server_dir: Path,
    expected: str,
    *,
    stages: Sequence[str] = STAGE_NAMES,
) -> native.InstallState:
    """The install record in `server_dir`, refused unless it is this entry's, for this folder.

    One set of checks for the two ways a folder's record is trusted: Install resuming
    into it (`SingleInstaller._claim()`), and "Use existing…" adopting it
    (`existing_install()`). A record copied from another folder is refused in both:
    its install id names that folder's compose project and containers, so managing
    it from here would start and stop a server that lives somewhere else.
    """
    family = entry.install.runtime.family
    state = native.read_state(server_dir, valid=stages)
    if state is None:
        raise InstallerError(
            f"{server_dir} holds a Yu'lon install record this version cannot read, so "
            "it was left alone. Pick a different folder. Nothing was written."
        )
    if state.game_id != entry.id:
        raise InstallerError(
            f"{server_dir} already holds a {state.game_id} install. Pick a different "
            "folder. Nothing was written."
        )
    if state.install_id != expected:
        raise InstallerError(
            f"{server_dir} holds an install record copied from another folder, so this "
            "install would take over a server that lives somewhere else. Pick a "
            "different folder. Nothing was written."
        )
    if state.family and state.family != family:
        raise InstallerError(
            f"{server_dir} was installed as a {state.family} server, not {family}. "
            "Pick a different folder. Nothing was written."
        )
    return state


def existing_install(
    entry: SingleEntry, server_dir: Path, *, platform_id: Callable[[], str] = platform.detect
) -> native.InstallState:
    """The finished install in `server_dir`, for "Use existing…"; refuse anything else.

    Install's own record checks (`_read_record()`), plus two of adoption's: the record
    must be there, because a folder without one is not a server Yu'lon can manage, and
    every stage must have finished, because a tab over a half-built install has no
    image to start. An install made by the CLI harness, or remembered by another copy
    of the app, passes both; nothing is written either way.
    """
    if not (server_dir / native.STATE_FILE).is_file():
        raise InstallerError(
            f"{server_dir} holds no {entry.name} server that Yu'lon installed. Pick the folder "
            f"Install made, or press Install."
        )
    state = _read_record(
        entry, server_dir, composegen.install_id(server_dir, platform_id=platform_id)
    )
    unfinished = [stage for stage in STAGE_NAMES if not state.has(stage)]
    if unfinished:
        raise InstallerError(
            f"The {entry.name} install in {server_dir} did not finish (it stopped before "
            f"{unfinished[0]}). Press Install and pick this folder to finish it."
        )
    return state


def cancelled_message(entry: SingleEntry, server_dir: Path) -> str:
    """What Stop left, said plainly, and which button to press next.

    Never promises the files are there or gone: a clone stopped early may have
    removed its own half-written folder, and one stopped after it finished keeps it.
    """
    return (
        f"The {entry.name} install was stopped, so it has not been remembered. Nothing was "
        f"undone: whatever had been fetched or built is still in {server_dir} and in Docker. "
        "Press Install again and choose the same folder to carry on from where it stopped, or "
        "delete the folder to start over."
    )


class SingleInstaller:
    """Install a `SingleEntry`: clone, render, build, start, and wait for its ready lines."""

    def __init__(
        self,
        entry: SingleEntry,
        *,
        installers_root: Path | None = None,
        seams: SingleSeams | None = None,
    ) -> None:
        self.entry = entry
        self._native, self._container, self._service = container_parts(entry)
        self.installers_root = (
            installers_root if installers_root is not None else resources.installers_dir()
        )
        self._seams = seams if seams is not None else SingleSeams()

    # -- identity ----------------------------------------------------------------

    def server_dir(self, options: InstallOptions) -> Path:
        """Where this install goes: what the user picked, or the entry's folder under $HOME."""
        if options.server_dir is not None:
            return options.server_dir
        return Path.home() / self.entry.install.default_server_dir

    def install_id(self, server_dir: Path) -> str:
        """The install id the state file and the compose project carry (`composegen`)."""
        return composegen.install_id(server_dir, platform_id=self._seams.platform_id)

    def image_ref(self, server_dir: Path) -> str:
        """The image this install builds: the entry's name, tagged with the install id.

        Per install, for the reason compose projects are: two installs of one game
        must not share a tag, or rebuilding one would swap the other's image.
        """
        return f"{self._native.image}:{self.install_id(server_dir)}"

    def tokens(self, server_dir: Path) -> dict[str, str]:
        """Every `{{TOKEN}}` the five templates may spend, for this install."""
        native_block = self._native
        values = {
            "PROJECT_NAME": composegen.project_name(
                self.entry.id, server_dir, install_id=self.install_id(server_dir)
            ),
            "SERVICE": self._service,
            "CONTAINER": self._container,
            "IMAGE": self.image_ref(server_dir),
            "CHECKOUT": native_block.checkout,
            **self.entry.port_tokens(),
        }
        if self.entry.advertise is not None:
            values["ADVERTISE_DEST"] = self.entry.advertise.dest
        return values

    # -- the InstallEngine half a Catalog tile drives ----------------------------

    def preflight(
        self,
        options: InstallOptions,
        cancel: threading.Event | None = None,
        *,
        ask: runner.Prompter | None = None,
    ) -> None:
        """Everything that must be true before anything is written. Raises, or returns."""
        for _ in self._preflight_lines(options, cancel, ask):
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
            InstallerError: any refusal, any stage that failed, or a Stop. The message
                is the sentence a user reads in the failure dialog.
        """
        opts = options or InstallOptions()
        server_dir = self.server_dir(opts)
        yield f"Installing {self.entry.name} into {server_dir}"
        yield from self._preflight_lines(opts, cancel, ask)
        self._check_cancel(cancel, server_dir)
        state = self._claim(server_dir)
        yield f"Using {server_dir} ({'resuming' if state.completed else 'a fresh install'})"
        if state.completed:
            yield f"Already finished: {', '.join(state.completed)}"
        server_dir.mkdir(parents=True, exist_ok=True)
        native.write_state(server_dir, state)
        bodies: dict[
            str, Callable[[Path, native.InstallState, threading.Event | None], Iterator[str]]
        ]
        bodies = {
            "clone-sources": self._clone_sources,
            "write-dockerfile": self._write_dockerfile,
            "seed-files": self._seed_files,
            "generate-compose": self._generate_compose,
            "build": self._build,
            "up": self._up,
            "ready": self._ready,
        }
        try:
            total = len(STAGE_NAMES)
            for number, name in enumerate(STAGE_NAMES, start=1):
                self._check_cancel(cancel, server_dir)
                # The spine's two lines, in its format: `^--- <name>` is what log captures
                # and gate scripts match on (`StagedInstaller._staged()`).
                yield f"Step {number} of {total} ({number * 100 // total}%): {name}"
                yield f"--- {name}"
                yield from bodies[name](server_dir, state, cancel)
                state = state.with_stage(name, STAGE_NAMES)
                native.write_state(server_dir, state)
        except InstallerError as exc:
            native.write_state(
                server_dir,
                replace(state, last_error=str(exc), error_run=native.ERROR_RUN_INSTALL),
            )
            raise
        native.write_state(server_dir, replace(state, last_error="", error_run=""))
        logger.info(f"install of {self.entry.id} finished")
        yield f"{self.entry.name} is installed and running in {server_dir}"

    # -- preflight and the folder's claim ----------------------------------------

    def _preflight_lines(
        self,
        options: InstallOptions,
        cancel: threading.Event | None,
        ask: runner.Prompter | None,
    ) -> Iterator[str]:
        here = self._seams.platform_id()
        if not self.entry.install.supports(here):
            raise UnsupportedPlatformError(unsupported_platform_message(self.entry, here))
        server_dir = self.server_dir(options)
        folder_problem = self._seams.dir_problem(server_dir)
        if folder_problem is not None:
            raise InstallerError(
                f"{folder_problem} Pick a different folder and try again. Nothing was written."
            )
        # Before Docker, for the spine's reason: every refusal here says "nothing was
        # written", which provisioning would have made false.
        self._claim(server_dir)
        yield "Checking Docker."
        if not self._seams.docker_ready():
            yield (
                "Docker is not answering yet. Setting it up - this can mean downloading "
                "Docker Desktop and waiting for its engine, up to a few minutes with no "
                "output. You can stop at any time."
            )
            report = self._seams.ensure_docker(cancel=cancel, ask=ask)
            yield from provision_lines(report)
            self._check_cancel(cancel, server_dir)
            if report.reboot_required:
                raise DockerUnavailableError(
                    "Docker's prerequisites were installed but a reboot is needed first. "
                    + " ".join(report.manual_steps)
                )
            if not report.docker_ready and not self._seams.docker_ready():
                raise docker_unavailable(report)
        yield from self._refuse_what_holds_our_names(server_dir)

    def _refuse_what_holds_our_names(self, server_dir: Path) -> Iterator[str]:
        """Refuse a container of ours under another project, or a stranger on our ports.

        Both are global to the daemon. The container name is checked against THIS
        install's project, so a resumed install's own container passes; a port held by
        anything other than our container is somebody else's running server.
        """
        name = self._container
        project = composegen.project_name(
            self.entry.id, server_dir, install_id=self.install_id(server_dir)
        )
        if self._seams.container_exists(name):
            owner = self._seams.container_project(name)
            if owner != project:
                said = (
                    "Docker could not say who owns it"
                    if owner == docker.UNREADABLE
                    else (
                        f"it belongs to the compose project {owner!r}"
                        if owner
                        else "it was not made by Docker Compose"
                    )
                )
                raise InstallerError(
                    f"A container named {name} already exists, and {said}, so this install "
                    f"would collide with it. Remove that server first (or that container, if it "
                    "is a leftover), then try again. Nothing was written."
                )
        held = [c for c in self._seams.port_conflicts(self.entry.host_ports()) if c != name]
        if held:
            ports = ", ".join(str(p) for p in self.entry.host_ports())
            raise InstallerError(
                f"{self.entry.name} needs ports {ports}, and {', '.join(sorted(held))} is "
                "running on one of them. Stop it, then try again. Nothing was written."
            )
        yield f"Ports {', '.join(str(p) for p in self.entry.host_ports())} are free."

    def _claim(self, server_dir: Path) -> native.InstallState:
        """This folder's state, or a new one; refuse a folder that is not ours to fill."""
        expected = self.install_id(server_dir)
        if (server_dir / native.STATE_FILE).is_file():
            return _read_record(self.entry, server_dir, expected)
        if server_dir.is_dir():
            # `native._listing()`, not a bare `iterdir()`: a folder that cannot be listed is
            # refused in a sentence, never taken for empty or let out as a traceback.
            leftovers = sorted(native._listing(server_dir, ignoring=native.OUR_OWN_FILES))
            if leftovers:
                raise InstallerError(
                    f"{server_dir} already has files in it ({', '.join(leftovers[:3])}"
                    f"{', …' if len(leftovers) > 3 else ''}), so nothing was installed there. "
                    "Pick an empty folder or a new one. Nothing was written."
                )
        return native.InstallState(
            game_id=self.entry.id, install_id=expected, family=self._native.family
        )

    @staticmethod
    def _stopped(cancel: threading.Event | None) -> bool:
        return cancel is not None and cancel.is_set()

    def _check_cancel(self, cancel: threading.Event | None, server_dir: Path) -> None:
        if self._stopped(cancel):
            raise InstallStopped(cancelled_message(self.entry, server_dir))

    # -- the stages ---------------------------------------------------------------

    def _clone_sources(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Clone every source at its `dest`; a finished clone of the right repository stays."""
        for source in self.entry.emulator.sources:
            dest = server_dir / source.dest
            existing = self._seams.remote_url(dest) if (dest / ".git").is_dir() else None
            if existing is not None and not git.same_repo(existing, source.url):
                raise InstallerError(
                    f"{dest} is a checkout of {existing}, not of {source.url}. Nothing was changed."
                )
            if existing is None and dest.is_dir() and native._listing(dest):
                raise InstallerError(
                    f"{dest} has files in it but is not a checkout of {source.url}, so it was "
                    "left alone. Move that folder aside and try again."
                )
            if existing is not None and state.has("clone-sources"):
                yield f"{source.repo} is already in {source.dest}; leaving it exactly as it is."
                continue
            yield f"Cloning {source.repo} into {source.dest}"
            try:
                yield from git.clone_lines(
                    self._seams.clone,
                    git.CloneSpec(
                        url=source.url,
                        dest=dest,
                        branch=source.branch,
                        sparse_path=source.sparse_path,
                        depth=source.depth,
                        rev=source.rev,
                    ),
                    stage="clone-sources",
                )
            except git.GitError as exc:
                if self._stopped(cancel):
                    raise InstallStopped(cancelled_message(self.entry, server_dir)) from exc
                raise InstallerError(f"Could not fetch {source.repo}: {exc}") from exc
        yield "Sources are in place."

    def _write_dockerfile(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Render the family's Dockerfile and .dockerignore beside the checkout."""
        template_dir = self.installers_root / self._native.templates
        try:
            text, ignore = dockerfile.render(
                template_dir, self.tokens(server_dir), secrets=NO_SECRETS
            )
            written = dockerfile.write(server_dir, text, ignore)
        except (dockerfile.DockerfileError, composegen.ComposeGenError, OSError) as exc:
            raise InstallerError(f"The Dockerfile could not be written: {exc}") from exc
        yield (
            "Wrote " + ", ".join(p.name for p in written)
            if written
            else "The Dockerfile is already up to date."
        )

    def _seed_files(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Copy the advertised-address file out of the checkout, once.

        Never over an existing copy: Networking rewrites it, and a resumed install
        must not put the shipped address back over the one the player chose.
        """
        advertise = self.entry.advertise
        if advertise is None:
            yield "This server keeps no address file of its own."
            return
        dest = server_dir / advertise.dest
        if dest.is_file():
            yield f"{advertise.dest} is already in place; leaving it as it is."
            return
        source = server_dir / advertise.source
        try:
            data = source.read_bytes()
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        except OSError as exc:
            raise InstallerError(
                f"Could not copy {advertise.source} to {advertise.dest}: {exc}. The checkout "
                "may not be the version this app expects."
            ) from exc
        yield f"Copied {advertise.source} to {advertise.dest}."

    def _generate_compose(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Write the three compose files, refusing to overwrite one this app did not write."""
        template_dir = self.installers_root / self._native.templates
        tokens = self.tokens(server_dir)
        rendered: list[tuple[Path, str]] = []
        for name, template in zip(composegen.COMPOSE_FILES, _COMPOSE_TEMPLATES, strict=True):
            try:
                body = (template_dir / template).read_text(encoding="utf-8")
                text = composegen.fill(body, tokens)
            except (OSError, composegen.ComposeGenError) as exc:
                raise InstallerError(f"{name} could not be generated: {exc}") from exc
            target = server_dir / name
            if not composegen.is_ours(target):
                raise InstallerError(
                    f"{target} was not written by Yu'lon, so it was left alone. Move it aside and "
                    "try again."
                )
            rendered.append((target, text))
        for target, text in rendered:
            if target.is_file() and target.read_text(encoding="utf-8") == text:
                continue
            target.write_text(text, encoding="utf-8", newline="\n")
            yield f"Wrote {target.name}"
        yield "The compose files are in place."

    def _build(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Build the image, unless a recorded build's image is still on the daemon."""
        image = self.image_ref(server_dir)
        if state.has("build") and self._seams.images_built([image]):
            yield "The server is already built; skipping the build."
            return
        yield "Building the server image. The output below is live; this takes a few minutes."
        run = yield from native.pump_lines(
            lambda sink: self._seams.build(
                server_dir, composegen.COMPOSE_FILES, sink=sink, cancel=cancel
            ),
            cancel=cancel,
            stage="build",
        )
        if self._stopped(cancel) or run.returncode == docker.CANCELLED_RETURNCODE:
            raise InstallStopped(cancelled_message(self.entry, server_dir))
        if run.returncode != 0:
            raise InstallerError(
                f"The {self.entry.name} image did not build (exit {run.returncode}). "
                + docker.last_words(run.tail, from_build=True)
            )
        yield "The build finished."

    def _up(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Start the one service."""
        yield f"Starting {self._container}."
        try:
            self._seams.up(server_dir, self._service)
        except docker.DockerCommandError as exc:
            raise InstallerError(f"{self.entry.name} could not be started: {exc}") from exc
        yield f"{self._container} is up; waiting for it to listen."

    def _ready(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Wait until the current run's log holds every ready line.

        The current run's log only (`docker.current_run_log`): a resumed install
        must not take a line an earlier run printed for this run's. A container
        that stops while it is waited on is a failure with its last lines quoted.
        """
        container = self._container
        deadline = self._seams.clock() + self._native.ready_seconds
        said: set[str] = set()
        while True:
            self._check_cancel(cancel, server_dir)
            log = self._seams.run_log(container)
            missing = missing_ready_lines(self.entry, log.text) if log.this_run_only else None
            for line in ready_lines(self.entry):
                if missing is not None and line not in missing and line not in said:
                    said.add(line)
                    yield f"Seen: {line}"
            if missing == ():
                yield f"{self.entry.name} is listening on ports " + ", ".join(
                    str(p) for p in self.entry.host_ports()
                )
                return
            status = self._seams.state_of(container).status
            if status in ("exited", "dead"):
                tail = log.text.strip().splitlines()[-12:]
                raise InstallerError(
                    f"{container} stopped while it was starting. Its last lines:\n"
                    + "\n".join(tail)
                )
            if self._seams.clock() >= deadline:
                waiting = ", ".join(missing or ready_lines(self.entry))
                raise InstallerError(
                    f"{self.entry.name} did not finish starting within "
                    f"{self._native.ready_seconds} seconds (still waiting for: "
                    f"{waiting}). It may still come up; its log is on the server's tab."
                )
            self._seams.sleep(READY_POLL_SECONDS)


_COMPOSE_TEMPLATES: tuple[str, ...] = ("base.yml.tmpl", "override.yml.tmpl", "build.yml.tmpl")
"""The templates behind `composegen.COMPOSE_FILES`, in the same order."""
