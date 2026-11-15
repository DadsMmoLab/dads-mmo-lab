"""The stack install engine: published images run as several compose services (`SingleStack`).

The catalog's second kind of entry in its third runtime. DDON's engine
(`single_install.py`) clones a source and builds ONE image for ONE container; DFO's
(`process_install.py`) builds host programs. LandSandBoat publishes an image of
every build of its FFXI server, so this engine builds nothing: it pulls the images
at the digests the catalog pins, writes the stack's passwords once, renders the two
compose files, copies the seed into the volumes, and starts the services in the
order the compose file gives. What it shares with the other two engines is taken
from the same places, so it cannot drift:

* the state file and its claim of ownership — `native.InstallState`,
  `read_state()`/`write_state()`, `STATE_FILE`, `OUR_OWN_FILES`, and the record
  checks `single_install._read_record()` makes for both other engines;
* the install id, the compose project name and the `{{TOKEN}}` fill — `composegen`;
* Docker provisioning and the sentences that report it — `platform.ensure_docker`,
  `installer.provision_lines()`, `installer.docker_unavailable()`;
* the verified download — `platform.download_verified()` and
  `verified_download.sha256_of()`, for the loader Play starts (`catalog.PlayLoader`);
* the bridge that streams a docker command into the log — `native.pump_lines()`.

The stages are named, recorded and resumable as the spine's are: a second press of
Install on the same folder skips what finished, and says so.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path

from yulon import docker, platform, resources, runner, winacl
from yulon.catalog import composegen, native
from yulon.catalog.catalog import (
    CLIENT_MOUNT,
    ClientExtract,
    PlayLoader,
    SingleEntry,
    SingleStack,
    StackService,
    VerifiedFile,
)
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
from yulon.catalog.single_install import _read_record
from yulon.catalog.verified_download import sha256_of
from yulon.log import get_logger

logger = get_logger(__name__)

STAGE_NAMES: tuple[str, ...] = (
    "fetch-loader",
    "fetch-downloads",
    "write-secrets",
    "generate-compose",
    "pull-images",
    "seed-volumes",
    "extract-client",
    "certificates",
    "up",
    "ready",
)
"""Every stage, in order. What `read_state()` validates a state file's `completed` against.

An entry runs `stages_of()` its stack: `fetch-downloads` and `extract-client` only where
the stack names downloads or a client extract (WoW Forever), so an FFXI install keeps the
seven stages it was recorded with.
"""

OPTIONAL_STAGES: dict[str, Callable[[SingleStack], bool]] = {
    "fetch-downloads": lambda stack: bool(stack.downloads),
    "extract-client": lambda stack: stack.client_extract is not None,
    "certificates": lambda stack: stack.certificates,
}
"""The stages a stack runs only when its entry asks for them."""


def stages_of(stack: SingleStack) -> tuple[str, ...]:
    """The stages an install of this stack runs, in order (`STAGE_NAMES` less what it lacks)."""
    return tuple(
        name for name in STAGE_NAMES if name not in OPTIONAL_STAGES or OPTIONAL_STAGES[name](stack)
    )


SEED_PROFILE = "seed"
"""The compose profile the templates put the seed service under, which `up` never starts."""

CERTIFICATES_SERVICE = "certificates"
"""The one-shot service that runs the entry's own certificate script (`stack.certificates`).

Under the seed profile, so `up` never starts it: `compose run --rm certificates` makes the
files once and the container goes with its temporary folder, where the CA's key lives.
"""

CERTIFICATES_COMMAND = ("bash", "/yulon-certs.sh")
"""The whole command the `certificates` service runs, rendered into its template and repeated.

`docker compose run <service> <words...>` REPLACES the service's `command:` with those words
(it does not append them to it), so the forced run cannot hand the script `--force` alone:
that would make `--force` the command and never run `certs-yulon.sh`. One tuple feeds both
the template's `{{CERTIFICATES_COMMAND}}` and `make_certificates()`'s forced run.
"""

READY_POLL_SECONDS = 2.0
"""How often the ready wait reads the containers' current-run logs."""

SECRET_BYTES = 24
"""Random bytes per generated password: 48 hex digits, nothing a .env line must quote."""

STACK_TEMPLATES: tuple[tuple[str, str], ...] = (
    (composegen.BASE_FILE, "base.yml.tmpl"),
    (composegen.OVERRIDE_FILE, "override.yml.tmpl"),
)
"""The two compose files a stack renders, and their templates. No build overlay: none is built."""

STACK_FILES = "files"
"""Beside the templates: files copied as they are to the same paths in the install folder."""


def stack_of(entry: SingleEntry) -> SingleStack:
    """The entry's `stack` block; refuses an entry of another runtime.

    Asked once, where an engine or a controller is built, so nothing below it holds
    a `None` it would have to ask about again.
    """
    stack = entry.install.stack
    if stack is None:
        raise ValueError(f"{entry.id} is not run as a stack of published images")
    return stack


def token_name(name: str) -> str:
    """A service or image role as a token's tail: `database-update` -> `DATABASE_UPDATE`."""
    return name.upper().replace("-", "_")


def ready_lines(service: StackService) -> tuple[str, ...]:
    """One service's ready lines. They spell no tokens, but go through `fill` as every line does."""
    return tuple(composegen.fill(line, {}) for line in service.ready)


def missing_ready_lines(service: StackService, log_text: str) -> tuple[str, ...]:
    """Which of a service's ready lines a log does not contain yet; () once it is up."""
    return tuple(line for line in ready_lines(service) if line not in log_text)


def client_problem(extract: ClientExtract, client_dir: Path | None) -> str | None:
    """Why `client_dir` is not a client a stack's data is made from, or None.

    Read only: the folder is the player's (README §3a). A `.build.info` that names none
    of the accepted builds, or a marker whose size is not that build's, is a client the
    server refuses at login, so its data is not made either.
    """
    if client_dir is None:
        return f"Choose {extract.client} first: the server's maps are made from it."
    exe = client_dir / extract.exe
    try:
        size = exe.stat().st_size
    except OSError:
        return (
            f"There is no {extract.exe} in {client_dir}. Choose the folder that holds it "
            f"({extract.client})."
        )
    if not extract.builds:
        return None
    accepted = ", ".join(b.build for b in extract.builds)
    try:
        info = (client_dir / BUILD_INFO).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return f"{client_dir} has no readable {BUILD_INFO}, so its build cannot be checked."
    named = [
        b for b in extract.builds if re.search(rf"(?<![0-9]){re.escape(b.build)}(?![0-9])", info)
    ]
    if not named:
        return (
            f"{client_dir / BUILD_INFO} names none of the builds this server accepts "
            f"({accepted}). Choose {extract.client}, and keep a copy of it out of the Battle.net "
            "app, which would update it past them."
        )
    build = named[0]
    if build.exe_size is not None and size != build.exe_size:
        return (
            f"{exe} is {size:,} bytes, not the {build.exe_size:,} of build {build.build}, so the "
            f"folder is half updated or another version. Choose {extract.client}."
        )
    return None


def client_build(extract: ClientExtract, client_dir: Path) -> str | None:
    """The accepted build `client_dir`'s `.build.info` names, or None (read only)."""
    try:
        info = (client_dir / BUILD_INFO).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for build in extract.builds:
        if re.search(rf"(?<![0-9]){re.escape(build.build)}(?![0-9])", info):
            return build.build
    return None


BUILD_INFO = ".build.info"
"""The Battle.net install record at a WoW client's root, which names its build."""


def loader_path(entry: SingleEntry, server_dir: Path) -> Path | None:
    """Where the install put the program Play starts in place of the client, if there is one."""
    play = entry.play
    if play is None or play.loader is None:
        return None
    return server_dir / play.loader.dest


def existing_install(
    entry: SingleEntry, server_dir: Path, *, platform_id: Callable[[], str] = platform.detect
) -> native.InstallState:
    """The finished install in `server_dir`, for "Use existing…"; refuse anything else.

    `single_install.existing_install()`'s rule over this engine's stages: the record
    must be there, be this entry's and this folder's, and have every stage finished.
    """
    if not (server_dir / native.STATE_FILE).is_file():
        raise InstallerError(
            f"{server_dir} holds no {entry.name} server that Yu'lon installed. Pick the folder "
            f"Install made, or press Install."
        )
    state = _read_record(
        entry,
        server_dir,
        composegen.install_id(server_dir, platform_id=platform_id),
        stages=STAGE_NAMES,
    )
    unfinished = [stage for stage in stages_of(stack_of(entry)) if not state.has(stage)]
    if unfinished:
        raise InstallerError(
            f"The {entry.name} install in {server_dir} did not finish (it stopped before "
            f"{unfinished[0]}). Press Install and pick this folder to finish it."
        )
    return state


def cancelled_message(entry: SingleEntry, server_dir: Path) -> str:
    """What Stop left, said plainly, and which button to press next."""
    return (
        f"The {entry.name} install was stopped, so it has not been remembered. Nothing was "
        f"undone: whatever had been downloaded or started is still in {server_dir} and in "
        "Docker. Press Install again and choose the same folder to carry on from where it "
        "stopped, or delete the folder to start over."
    )


@dataclass(frozen=True)
class StackSeams:
    """Everything the stack engine reaches outside itself through. Real by default.

    For `native.Seams`' reason: an engine whose every external effect is a seam can
    have its control flow tested without a daemon, a registry or a network.
    """

    platform_id: Callable[[], str] = platform.detect
    docker_ready: Callable[[], bool] = platform.docker_ready
    ensure_docker: Callable[..., platform.ProvisionReport] = platform.ensure_docker
    dir_problem: Callable[[Path], str | None] = platform.server_dir_problem
    download: Callable[[str, Path], Path] = platform.download_verified
    port_conflicts: Callable[[tuple[int, ...]], list[str]] = docker.port_conflicts
    container_exists: Callable[[str], bool] = docker.container_exists
    container_project: Callable[[str], str | None] = docker.container_project
    pull: Callable[..., docker.AttachedRun] = docker.compose_pull
    run_once: Callable[..., docker.AttachedRun] = docker.compose_run_once
    up: Callable[..., None] = docker.compose_up_all
    run_log: Callable[[str], docker.RunLog] = docker.current_run_log
    state_of: Callable[[str], docker.ContainerState] = docker.container_state
    token: Callable[[int], str] = field(default=secrets.token_hex)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic


class StackInstaller:
    """Install a `stack` single server: loader, secrets, compose, pull, seed, up, ready."""

    def __init__(
        self,
        entry: SingleEntry,
        *,
        installers_root: Path | None = None,
        seams: StackSeams | None = None,
    ) -> None:
        self.entry = entry
        self.stack = stack_of(entry)
        self.installers_root = (
            installers_root if installers_root is not None else resources.installers_dir()
        )
        self._seams = seams if seams is not None else StackSeams()

    # -- identity ----------------------------------------------------------------

    def server_dir(self, options: InstallOptions) -> Path:
        """Where this install goes: what the user picked, or the entry's folder under $HOME."""
        if options.server_dir is not None:
            return options.server_dir
        return Path.home() / self.entry.install.default_server_dir

    def install_id(self, server_dir: Path) -> str:
        """The install id the state file and the compose project carry (`composegen`)."""
        return composegen.install_id(server_dir, platform_id=self._seams.platform_id)

    def project(self, server_dir: Path) -> str:
        """This install's compose project, which every container it runs is labelled with."""
        return composegen.project_name(
            self.entry.id, server_dir, install_id=self.install_id(server_dir)
        )

    def tokens(self, server_dir: Path) -> dict[str, str]:
        """Every `{{TOKEN}}` the two templates may spend, for this install."""
        values = {
            "PROJECT_NAME": self.project(server_dir),
            **self.entry.port_tokens(),
            # The certificates service's whole command, one list literal from the same tuple
            # the forced run repeats (T888).
            "CERTIFICATES_COMMAND": json.dumps(list(CERTIFICATES_COMMAND)),
        }
        for image in self.stack.images:
            values[f"IMAGE_{token_name(image.role)}"] = image.ref
        for service in self.stack.services:
            values[f"CONTAINER_{token_name(service.service)}"] = service.container
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
        client_dir = opts.client_dir
        bodies = {
            "fetch-loader": self._fetch_loader,
            "fetch-downloads": self._fetch_downloads,
            "write-secrets": self._write_secrets,
            "generate-compose": self._generate_compose,
            "pull-images": self._pull_images,
            "seed-volumes": self._seed_volumes,
            "extract-client": lambda d, s, c: self._extract_client(d, s, c, client_dir),
            "certificates": self._certificates,
            "up": self._up,
            "ready": self._ready,
        }
        stages = stages_of(self.stack)
        try:
            total = len(stages)
            for number, name in enumerate(stages, start=1):
                self._check_cancel(cancel, server_dir)
                # The spine's two lines, in its format (`StagedInstaller._staged()`).
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
        extract = self.stack.client_extract
        if extract is not None:
            problem = client_problem(extract, options.client_dir)
            if problem is not None:
                raise InstallerError(f"{problem} Nothing was written.")
            yield f"Client: {options.client_dir} ({extract.client})"
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

        `single_install`'s rule for every container the stack names: both names and
        ports are global to the daemon, and a resumed install's own containers pass.
        """
        project = self.project(server_dir)
        ours = set(self.entry.containers())
        for name in self.entry.containers():
            if not self._seams.container_exists(name):
                continue
            owner = self._seams.container_project(name)
            if owner == project:
                continue
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
                f"A container named {name} already exists, and {said}, so this install would "
                "collide with it. Remove that server first (or that container, if it is a "
                "leftover), then try again. Nothing was written."
            )
        held = [c for c in self._seams.port_conflicts(self.entry.host_ports()) if c not in ours]
        ports = ", ".join(str(p) for p in self.entry.host_ports())
        if held:
            raise InstallerError(
                f"{self.entry.name} needs ports {ports}, and {', '.join(sorted(held))} is "
                "running on one of them. Stop it, then try again. Nothing was written."
            )
        yield f"Ports {ports} are free."

    def _claim(self, server_dir: Path) -> native.InstallState:
        """This folder's state, or a new one; refuse a folder that is not ours to fill."""
        expected = self.install_id(server_dir)
        if (server_dir / native.STATE_FILE).is_file():
            return _read_record(self.entry, server_dir, expected, stages=STAGE_NAMES)
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
            game_id=self.entry.id, install_id=expected, family=self.stack.family
        )

    @staticmethod
    def _stopped(cancel: threading.Event | None) -> bool:
        return cancel is not None and cancel.is_set()

    def _check_cancel(self, cancel: threading.Event | None, server_dir: Path) -> None:
        if self._stopped(cancel):
            raise InstallStopped(cancelled_message(self.entry, server_dir))

    # -- the stages ---------------------------------------------------------------

    def _fetch_loader(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Fetch the program Play starts in place of the client, at its pinned sha256.

        A file already there with the pin's size and sha256 is left as it is; any other
        file at that path is fetched again, never trusted.
        """
        play = self.entry.play
        loader: PlayLoader | None = play.loader if play is not None else None
        if loader is None:
            yield "This server's client starts without a loader."
            return
        path = server_dir / loader.dest
        if path.is_file() and self._matches(path, loader):
            yield f"{loader.name} is already in {loader.dest} at its pinned sha256."
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        for attempt in (1, 2):
            path.unlink(missing_ok=True)
            yield f"Downloading {loader.name} from {loader.file.url}"
            try:
                self._seams.download(loader.file.url, path)
            except OSError as exc:
                raise InstallerError(
                    f"{loader.name} could not be downloaded from {loader.file.url}: {exc}"
                ) from exc
            if self._matches(path, loader):
                yield f"{loader.name} matches its pinned size and sha256."
                return
            if attempt == 2:
                path.unlink(missing_ok=True)
                raise InstallerError(
                    f"{loader.file.url} did not match the size and sha256 this version of "
                    f"Yu'lon pins for {loader.name}. It was deleted."
                )
            yield f"The download did not match its pinned sha256; fetching {loader.name} again."

    @staticmethod
    def _matches(path: Path, loader: PlayLoader) -> bool:
        return _file_matches(path, loader.file)

    def _fetch_downloads(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Fetch each `stack.downloads` file at its pinned size and sha256 (`_fetch_loader`'s rule).

        A file already there that matches is kept; anything else at that path is fetched
        again and checked, and a second mismatch is deleted and refused.
        """
        for item in self.stack.downloads:
            self._check_cancel(cancel, server_dir)
            path = server_dir / item.dest
            if path.is_file() and _file_matches(path, item.file):
                yield f"{item.what} is already in {item.dest} at its pinned sha256."
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            for attempt in (1, 2):
                path.unlink(missing_ok=True)
                megabytes = item.file.size // 1_000_000
                yield f"Downloading {item.what} ({megabytes} MB) from {item.file.url}"
                try:
                    self._seams.download(item.file.url, path)
                except OSError as exc:
                    raise InstallerError(
                        f"{item.what} could not be downloaded from {item.file.url}: {exc}"
                    ) from exc
                if _file_matches(path, item.file):
                    yield f"{item.what} matches its pinned size and sha256."
                    break
                if attempt == 2:
                    path.unlink(missing_ok=True)
                    raise InstallerError(
                        f"{item.file.url} did not match the size and sha256 this version of "
                        f"Yu'lon pins for {item.what}. It was deleted."
                    )
                yield f"The download did not match its pinned sha256; fetching {item.what} again."

    def _extract_client(
        self,
        server_dir: Path,
        state: native.InstallState,
        cancel: threading.Event | None,
        client_dir: Path | None,
    ) -> Iterator[str]:
        """Make the server's game data from the player's client, mounted read-only.

        The extractor is the stack's own service (`ClientExtract`), run once with
        `compose run --rm`; it keeps a marker per finished step, so an install stopped
        here resumes inside the extraction rather than starting it over.
        """
        extract = self.stack.client_extract
        if extract is None:
            yield "This server makes nothing from the client."
            return
        problem = client_problem(extract, client_dir)
        if problem is not None:
            raise InstallerError(problem)
        yield (
            f"Making {extract.what} from {client_dir}, read-only. This is the long step: "
            "about 15 to 40 minutes, most of it the bots' pathfinding."
        )
        run = yield from native.pump_lines(
            lambda sink: self._seams.run_once(
                server_dir,
                extract.service,
                profiles=(SEED_PROFILE,),
                volumes=(f"{client_dir}:{CLIENT_MOUNT}:ro",),
                sink=sink,
                cancel=cancel,
            ),
            cancel=cancel,
            stage="extract-client",
        )
        self._check_run(run, server_dir, f"{extract.what} could not be made")
        yield f"{extract.what} are ready."

    def _write_secrets(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Write the stack's .env with a fresh password per secret, once.

        Never over a value that is there: the database was made with it, and a new
        one would lock every server out of its own data. A .env with some of the
        secrets gets the missing ones added and keeps every line it had.
        """
        env = server_dir / composegen.DOTENV_FILE
        missing = [
            name for name in self.stack.secrets if composegen.dotenv_value(server_dir, name) is None
        ]
        if not missing:
            # Nothing is generated, but the file is still the one file this folder holds a
            # password in: an install made when the mode was looser (or a copy from a
            # backup) comes back owner-only, without its bytes changing (T888).
            winacl.secure_folder(server_dir)
            if env.is_file():
                try:
                    platform.write_private_atomically(env, env.read_bytes())
                except OSError as exc:
                    raise InstallerError(
                        f"{env} already holds this server's passwords but could not be made "
                        f"owner-only: {exc}"
                    ) from exc
            yield f"{composegen.DOTENV_FILE} already holds this server's passwords; leaving it."
            return
        try:
            existing = env.read_text(encoding="utf-8") if env.is_file() else ""
        except OSError as exc:
            raise InstallerError(f"{env} could not be read: {exc}") from exc
        lines = [] if not existing else [existing.rstrip("\r\n")]
        if not existing:
            lines.append(
                f"{composegen.GENERATED_MARKER} — the stack's passwords, made once at install; "
                "the database was created with them."
            )
        lines += [f"{name}='{self._seams.token(SECRET_BYTES)}'" for name in missing]
        # The database root password is in these lines, so they land the way the other
        # secret writers land theirs (T127): atomically, 0600 from the first byte, and
        # under this folder's owner-only DACL on Windows, where the mode is a no-op
        # (T151/T174). The DACL is applied before the file exists, so it is inherited.
        winacl.secure_folder(server_dir)
        try:
            platform.write_private_atomically(env, ("\n".join(lines) + "\n").encode("utf-8"))
        except OSError as exc:
            raise InstallerError(f"{env} could not be written: {exc}") from exc
        yield f"Generated {', '.join(missing)} in {composegen.DOTENV_FILE}."

    def _generate_compose(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Write the two compose files, refusing to overwrite one this app did not write."""
        yield from self.write_compose(server_dir)
        yield "The compose files are in place."

    def write_compose(self, server_dir: Path) -> list[str]:
        """Render both compose files and copy the stack's files; the lines that say what changed.

        One function for the install stage and for the certificate remake (T888), because
        a remake on an install made before Yu'lon's own certificate script existed has a
        compose file with no `certificates` service and no `certs-yulon.sh` beside it: the
        one-shot it needs would not be there to run.
        """
        template_dir = self.installers_root / self.stack.templates
        tokens = self.tokens(server_dir)
        rendered: list[tuple[Path, str]] = []
        for name, template in STACK_TEMPLATES:
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
        written: list[str] = []
        for target, text in rendered:
            if target.is_file() and target.read_text(encoding="utf-8") == text:
                continue
            try:
                target.write_text(text, encoding="utf-8", newline="\n")
            except OSError as exc:
                raise InstallerError(f"{target} could not be written: {exc}") from exc
            written.append(f"Wrote {target.name}")
        written.extend(self._place_stack_files(template_dir / STACK_FILES, server_dir))
        return written

    def _place_stack_files(self, source: Path, server_dir: Path) -> Iterator[str]:
        """Copy the entry's own files (`stack/files/...`) to the same paths in the install folder.

        What the compose files mount beside the images: WoW Forever's hotfixes update, which
        the world server's updater applies once from its custom folder. Rewritten only when
        the content differs, so a later Yu'lon with a corrected file replaces the old one.
        """
        if not source.is_dir():
            return
        for path in sorted(p for p in source.rglob("*") if p.is_file()):
            target = server_dir / path.relative_to(source)
            data = path.read_bytes()
            if target.is_file() and target.read_bytes() == data:
                continue
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            except OSError as exc:
                raise InstallerError(f"{target} could not be written: {exc}") from exc
            yield f"Wrote {target.relative_to(server_dir).as_posix()}"

    def _certificates(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Make the stack's own TLS certificates, in its own image, before the first start.

        Yu'lon's script, not the image's (`certs-yulon.sh`): the CA is constrained to the
        one name this stack's login server answers for, and its key stays in the one-shot
        container's temporary folder. It writes `certs/` only, and a second run leaves the
        files it finds.
        """
        if not self.stack.certificates:
            yield "This server makes no certificate of its own."
            return
        yield (
            "Making this server's own certificate: the login name it answers for, and the "
            "CA a computer trusts for that name alone."
        )
        run = yield from native.pump_lines(
            lambda sink: self._seams.run_once(
                server_dir,
                CERTIFICATES_SERVICE,
                profiles=(SEED_PROFILE,),
                sink=sink,
                cancel=cancel,
            ),
            cancel=cancel,
            stage="certificates",
        )
        self._check_run(run, server_dir, "The server's certificate could not be made")
        yield "The server's certificate is in certs/."

    def _pull_images(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Pull every pinned image. Compose skips one already on the daemon at its digest."""
        for image in self.stack.images:
            yield f"{image.label}: {image.ref}"
        yield "Pulling the images. The output below is live; the first pull is a few gigabytes."
        run = yield from native.pump_lines(
            lambda sink: self._seams.pull(
                server_dir, profiles=self._profiles(), sink=sink, cancel=cancel
            ),
            cancel=cancel,
            stage="pull-images",
        )
        self._check_run(run, server_dir, "The images could not be pulled")
        yield "Every image is on this computer."

    def _seed_volumes(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Copy the seed into the stack's volumes, once per install.

        Once: a recorded seed is not run again, so a resumed install does not copy
        the same data over itself.
        """
        seed = self.stack.seed
        if seed is None:
            yield "This server needs nothing copied into its volumes."
            return
        if state.has("seed-volumes"):
            yield f"{seed.what} were copied in by an earlier run; leaving them."
            return
        yield f"Copying {seed.what} into this server's volumes."
        run = yield from native.pump_lines(
            lambda sink: self._seams.run_once(
                server_dir, seed.service, profiles=(SEED_PROFILE,), sink=sink, cancel=cancel
            ),
            cancel=cancel,
            stage="seed-volumes",
        )
        self._check_run(run, server_dir, f"{seed.what} could not be copied in")
        yield "The volumes are filled."

    def _profiles(self) -> tuple[str, ...]:
        uses_seed = self.stack.seed is not None or self.stack.client_extract is not None
        return (SEED_PROFILE,) if uses_seed else ()

    def _check_run(self, run: docker.AttachedRun, server_dir: Path, failed: str) -> None:
        if run.returncode == docker.CANCELLED_RETURNCODE:
            raise InstallStopped(cancelled_message(self.entry, server_dir))
        if run.returncode != 0:
            raise InstallerError(
                f"{failed} (exit {run.returncode}). " + docker.last_words(run.tail)
            )

    def _up(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Start every service; compose waits for the database and the one-shot update first."""
        if self.stack.family == "forevercore":
            yield (
                "Starting the realm. The first start imports the world database, which takes "
                "about 15 minutes; the world server's log on the server's tab shows it."
            )
        else:
            yield (
                "Starting the stack. The first start creates the database, which takes about a "
                "minute with no output here."
            )
        try:
            self._seams.up(server_dir)
        except docker.DockerCommandError as exc:
            raise InstallerError(f"{self.entry.name} could not be started: {exc}") from exc
        yield "Every service is up; waiting for the servers to listen."

    def _ready(
        self, server_dir: Path, state: native.InstallState, cancel: threading.Event | None
    ) -> Iterator[str]:
        """Wait until every listener's current-run log holds its ready lines.

        `single_install._ready()`'s rule per container: the current run's log only, and
        a listener that stops while it is waited on is a failure with its last lines.
        """
        deadline = self._seams.clock() + self.stack.ready_seconds
        waiting: list[StackService] = [s for s in self.stack.listeners if s.ready]
        while True:
            self._check_cancel(cancel, server_dir)
            still: list[StackService] = []
            for service in waiting:
                log = self._seams.run_log(service.container)
                if log.this_run_only and not missing_ready_lines(service, log.text):
                    yield f"Seen: {', '.join(ready_lines(service))}"
                    continue
                status = self._seams.state_of(service.container).status
                if status in ("exited", "dead"):
                    tail = log.text.strip().splitlines()[-12:]
                    raise InstallerError(
                        f"{service.container} stopped while it was starting. Its last lines:\n"
                        + "\n".join(tail)
                    )
                still.append(service)
            waiting = still
            if not waiting:
                yield f"{self.entry.name} is listening on ports " + ", ".join(
                    str(p) for p in self.entry.host_ports()
                )
                return
            if self._seams.clock() >= deadline:
                names = ", ".join(s.container for s in waiting)
                raise InstallerError(
                    f"{self.entry.name} did not finish starting within "
                    f"{self.stack.ready_seconds} seconds (still waiting for {names}). It may "
                    "still come up; its log is on the server's tab."
                )
            self._seams.sleep(READY_POLL_SECONDS)


def _file_matches(path: Path, file: VerifiedFile) -> bool:
    """Whether `path` is that file: its pinned size first (cheap), then its sha256."""
    try:
        return path.stat().st_size == file.size and sha256_of(path) == file.sha256
    except OSError:
        return False


class CertificatesError(RuntimeError):
    """The stack's certificate script did not finish; the message says why."""


def make_certificates(server_dir: Path, *, force: bool = False) -> None:
    """Make a stack's own certificates now, in its image (`compose run --rm certificates`).

    The install's `certificates` stage and Play's remake of an existing install (T888)
    both come through here, so the two cannot run different things. `force` makes a new
    set even though a complete one is already there -- the script builds it in a staging
    folder inside the container, verifies it, and only then swaps it in, so a failed run
    leaves the old files exactly as they were. `compose run` REPLACES the service's
    `command:` with the words it is given, so even the forced run spells the whole command
    (`CERTIFICATES_COMMAND`), with `--force` on the end. Raises `CertificatesError` with
    the run's last words when the script fails.
    """
    command = (*CERTIFICATES_COMMAND, "--force") if force else CERTIFICATES_COMMAND
    run = docker.compose_run_once(
        server_dir,
        CERTIFICATES_SERVICE,
        profiles=(SEED_PROFILE,),
        command=command,
    )
    if run.returncode != 0:
        raise CertificatesError(
            f"The server's certificate could not be made (exit {run.returncode}). "
            + docker.last_words(run.tail)
        )


def remake_certificates(entry: SingleEntry, server_dir: Path) -> None:
    """Render this install's compose files and files, then make its certificates again.

    Play's remake (T888). The render comes first and for a reason: an install made before
    Yu'lon's own certificate script existed has no `certificates` service in its compose
    file and no `certs-yulon.sh` beside it, so `compose run --rm certificates` would name
    a service the project does not have -- or run the image's broad CA again. The same
    `write_compose()` the install stage uses puts both back. Raises `InstallerError` or
    `CertificatesError` with the sentence a player reads.
    """
    installer = StackInstaller(entry)
    installer.write_compose(server_dir)
    make_certificates(server_dir, force=True)


def project_of(entry: SingleEntry, server_dir: Path) -> str:
    """This install's compose project name (`StackInstaller.project()`), for ownership checks."""
    return StackInstaller(entry).project(server_dir)


def listener_containers(entry: SingleEntry) -> Sequence[str]:
    """The containers that keep running once the stack is up, in catalog order."""
    return tuple(service.container for service in stack_of(entry).listeners)
