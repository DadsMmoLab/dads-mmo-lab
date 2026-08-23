"""Shared Docker lifecycle helpers (game-agnostic).

Implements the Docker-CLI operations every per-game controller needs — start/stop
via `docker compose`, status/health/polling via `docker ps`/`docker inspect`/
`docker logs` — by shelling out through `yulon.runner`, which owns all subprocess
concerns (style-guide §3). This module is deliberately game-agnostic: the
per-game specifics (container names, published ports) are grouped in
`ContainerSpec` for readability/typing, and the `*_for(spec, ...)` convenience
wrappers accept a spec directly; the lower-level functions still take
individual container names/ports for callers that don't have a full spec.
Nothing in this module knows about any particular server.

The polling helpers mirror `dml-start.sh`'s `_wait_db_healthy` / `_wait_ready`
logic, generalized and given explicit, overridable timeouts.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yulon import platform, runner
from yulon.log import get_logger

logger = get_logger(__name__)

_DB_HEALTHY_TIMEOUT_SECONDS = 180.0
_READY_TIMEOUT_SECONDS = 480.0
_POLL_INTERVAL_SECONDS = 2.0
# `docker compose config` on the GUI thread; see compose_project_name().
_COMPOSE_CONFIG_TIMEOUT_SECONDS = 10.0


class DockerCommandError(RuntimeError):
    """Raised when a `docker` CLI command exits with a non-zero status."""


class DockerCliMissingError(DockerCommandError):
    """Raised when there is no docker CLI to run at all — nothing was asked of Docker.

    A subclass, so every `except DockerCommandError` already written keeps
    catching it and no caller has to learn a second type to stay correct. What
    the subclass buys is the callers that must NOT treat it as "Docker was asked
    and would not answer": that answer is transient and worth retrying or
    degrading past, and this one is neither.

    Added 2026-08-23. The commit that routed every argv through
    `platform.docker_program()` said "failure stays honest" and it was not quite
    true: `_status_safe()` degraded a missing CLI to `None` exactly as it
    degrades a daemon hiccup, so `stop_staged()` told the user their install had
    no `COMPOSE_PROJECT_NAME` pinned — blaming the install for the absence of
    Docker — and `wait_ready()` polled out its full 480s without a word above
    DEBUG.
    """


@dataclass(frozen=True)
class ContainerSpec:
    """How one server install is addressed: its containers, services and ports.

    Two different names for the same three things, because Docker uses two:
    `docker ps`/`docker inspect` answer to **container** names, while
    `docker compose` addresses **services**. For every AzerothCore-derived game
    they happen to be identical (`ac-database`, `ac-authserver`,
    `ac-worldserver`), which is why `services` may be left empty and defaults to
    the container names — but the distinction is real, and a game whose compose
    file names its services differently must say so rather than get silently
    wrong behaviour.
    """

    db: str
    auth: str
    world: str
    ports: tuple[int, ...]
    services: tuple[str, ...] = ()

    import_service: str = ""
    """The one-shot compose service that populates the databases, if the game has one.

    Named here rather than deduced, and empty for a game that has not said.
    `compose_services()` exists to keep this service *out* of every ordinary
    start; `repair_import()` is the only thing allowed to select it, and it
    refuses outright when this is empty rather than guessing at a service name
    and running whatever happens to answer to it.
    """

    def compose_services(self) -> tuple[str, ...]:
        """The long-running compose services, in dependency order (db first).

        Deliberately excludes one-shot services such as `ac-db-import`: naming
        the services explicitly is what keeps `compose up` from ever selecting
        the import job. See `start_staged()`.
        """
        return self.services or (self.db, self.auth, self.world)


CANCELLED_RETURNCODE = -1
"""What `run_attached()` reports when the caller's cancel token stopped the read.

Negative on purpose: a real process exit status is 0-255, and Python already
spells "killed by signal N" as `-N`, so no docker command can produce this by
exiting. A caller must not read it as a failure of the thing being run — the
build step or the one-shot is still finishing inside the daemon.
"""

_CLI_MISSING_RETURNCODE = 127
"""What a shell reports for "command not found", and what `_docker()` returns.

Borrowed rather than invented so the value means something to anyone reading a
log: no docker command can exit 127 itself, and every caller here already
branches on `returncode != 0`.
"""


def _docker(
    argv: list[str], cwd: Path | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run `docker <argv...>` under whatever name this host can actually start it.

    The single place this module names the CLI. `platform.docker_program()`
    exists because a Windows process cannot see the PATH entry Docker Desktop's
    installer just wrote, so the run that provisions Docker is exactly the run
    that must not spell the command `docker` and hope (see there).

    A host with no docker CLI at all comes back as a non-zero
    `CompletedProcess` carrying `DOCKER_CLI_MISSING_HELP` in `stderr`, not as an
    exception — the same shape `runner.run()` gives a timeout, and for the same
    reason: `health()` answers `"unknown"` and `container_state()` answers
    empty, and both of those degraded answers are right for "docker is missing"
    too. Raising instead would take a polling loop off the GUI thread's rails to
    say something its caller already knows how to report.

    It stays *recognisable* while it degrades, though — see `_cli_missing()`.
    The callers that must not confuse it with "Docker was asked and would not
    answer" are the ones that go on to explain themselves to the user.

    `OSError` is caught for the one case resolution cannot cover: Docker
    uninstalled while the launcher is open, leaving `docker_program()`'s pinned
    path aimed at a file that is gone. The user hears "Docker could not be
    found", which is true.

    The two branches log at different levels on purpose. "No CLI at all" is
    already in the `stderr` this returns, and every caller here logs that — a
    warning would print the same sentence twice for every command, and
    `wait_ready()` issues five per poll. The `OSError` carries what the sentence
    does not: which path was tried, and the errno.
    """
    program = platform.docker_program()
    if program is not None:
        try:
            return runner.run([program, *argv], cwd=cwd, timeout=timeout)
        except OSError as exc:
            logger.warning(f"{program} could not be started: {exc}")
    else:
        logger.debug(f"no docker CLI on this host; not running: docker {' '.join(argv)}")
    return subprocess.CompletedProcess(
        ["docker", *argv], _CLI_MISSING_RETURNCODE, "", platform.DOCKER_CLI_MISSING_HELP
    )


def _cli_missing(proc: subprocess.CompletedProcess[str]) -> bool:
    """True if this result is `_docker()`'s "there is no docker CLI" sentinel.

    Both halves are checked on purpose. The exit code alone would be a guess:
    `docker run` and `docker exec` return the *container's* status, so a real
    docker command can genuinely exit 127 (a command not found inside the
    image). Nothing in this module runs either — but `_CLI_MISSING_RETURNCODE`'s
    "no docker command can exit 127 itself" is only true of the commands here
    today, and pairing it with the exact help text means adding one costs
    nothing.
    """
    return (
        proc.returncode == _CLI_MISSING_RETURNCODE
        and proc.stderr == platform.DOCKER_CLI_MISSING_HELP
    )


def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run `docker <argv...>`; raise `DockerCommandError` on non-zero exit.

    Raises `DockerCliMissingError` — a subclass — when there was no CLI to run,
    carrying `DOCKER_CLI_MISSING_HELP` and nothing else. The argv is dropped
    from the message deliberately: `docker ps --format {{.Names}} exited 127:`
    in front of the sentence is noise to the user reading it in a dialog, and
    `_docker()` has already put the command in the log at DEBUG.
    """
    proc = _docker(argv, cwd=cwd)
    if _cli_missing(proc):
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    if proc.returncode != 0:
        raise DockerCommandError(
            f"docker {' '.join(argv)} exited {proc.returncode}: {proc.stderr.strip()}"
        )
    return proc


def start(server_dir: Path) -> None:
    """Bring the compose project in `server_dir` up in the background.

    Creates whatever does not exist yet, which on an installed server also
    re-runs the one-shot containers. Prefer `start_staged()` for a server that
    has already been installed — see the warning there.
    """
    logger.debug(f"start() called: server_dir={server_dir}")
    _run(["compose", "up", "-d"], cwd=server_dir)


PROJECT_NAME_VAR = "COMPOSE_PROJECT_NAME"


def compose_project_name(server_dir: Path) -> str | None:
    """What compose currently calls this project, or None if it cannot say.

    Asked rather than computed. Compose derives the name from the directory
    basename by lowercasing it, dropping every character outside `[a-z0-9_-]`
    and then trimming leading punctuation — measured: `WoW_Server 2` becomes
    `wow_server2`, `_leading` becomes `leading`, `Ünïcode` becomes `ncode`.
    Reimplementing that here would be a second copy of somebody else's rule,
    free to drift, and a wrong guess is worse than no guess: pinning the wrong
    value *renames* the project and orphans the containers it was meant to keep.
    """
    # Bounded, because two callers run on the GUI thread — pinning after an
    # install, and the ownership lookup behind a Stop. Measured floor is about
    # 0.6s even with the daemon down, and it is unbounded on a picked folder
    # that lives on a sleeping NAS or behind a stalled docker CLI. Failing to
    # name the project is already a handled outcome; freezing the window is not
    # (review, 2026-08-22).
    proc = _docker(
        ["compose", "config", "--format", "json"],
        cwd=server_dir,
        timeout=_COMPOSE_CONFIG_TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        logger.debug(f"compose config failed in {server_dir}: {proc.stderr.strip()}")
        return None
    try:
        parsed = json.loads(proc.stdout)
    except ValueError:
        logger.debug("compose config did not return JSON")
        return None
    name = parsed.get("name") if isinstance(parsed, dict) else None
    return name if isinstance(name, str) and name else None


def pin_project_name(server_dir: Path) -> str | None:
    """Freeze this install's compose project name into its own `.env`.

    Compose identifies a project by its directory basename unless told
    otherwise, but AzerothCore pins its container names, which are global. Move
    or rename the install folder and the two identities come apart: `compose`
    commands in the new directory address a project that owns nothing, so
    `compose stop` stops nothing and `compose up` collides with the containers
    that are still there under the old project. Writing the name down once, in
    the install itself, is what makes the folder movable.

    `wow-manage.sh` does the same thing on its own move command, which is where
    this behaviour comes from; writing it down whenever the name is provably
    right means the folder can then be moved by any means — a file manager, a
    backup restore — and still work.

    Only two callers may use this, and both are moments where the directory
    basename provably IS what compose named the containers: straight after this
    app's own installer finished, and after a stop that confirmed our own
    containers by label. Attaching an existing install is NOT such a moment —
    see `install_project()`.

    Returns the pinned name, or None if nothing was written (already pinned, or
    compose could not be asked).
    """
    env_path = server_dir / ".env"
    if pinned_project_name(server_dir) is not None:
        logger.debug(f"{PROJECT_NAME_VAR} already pinned in {env_path}")
        return None
    name = compose_project_name(server_dir)
    if name is None:
        logger.info(f"could not ask compose for the project name in {server_dir}; not pinning")
        return None
    # Byte-preserving and atomic, because this file holds the database root
    # password. `write_text` truncates before it writes, so a crash or a full
    # disk in between would leave the user with an empty `.env` and a database
    # they can no longer reach. Reading as BYTES also means a non-UTF-8 value
    # survives untouched instead of being silently rewritten through
    # `errors="replace"` (adversarial review finding, 2026-08-22).
    try:
        existing = env_path.read_bytes() if env_path.is_file() else b""
    except OSError as exc:
        logger.warning(f"could not read {env_path}; not pinning: {exc}")
        return None
    if existing and not existing.endswith(b"\n"):
        existing += b"\n"
    addition = (
        "# Pinned by Yu'lon so this install keeps working if the folder is moved.\n"
        f"{PROJECT_NAME_VAR}={name}\n"
    ).encode()
    tmp = env_path.with_name(env_path.name + ".yulon-new")
    try:
        tmp.write_bytes(existing + addition)
        os.replace(tmp, env_path)  # atomic on POSIX and on Windows
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        logger.warning(f"could not write {env_path}; not pinning: {exc}")
        return None
    logger.info(f"pinned {PROJECT_NAME_VAR}={name} in {env_path}")
    return name


def pinned_project_name(server_dir: Path) -> str | None:
    """The project name this install pinned into its own `.env`, if any.

    Read directly, without asking compose. `compose_project_name()` needs to
    parse the compose files, which is exactly what is unavailable in the case
    the by-name stop path exists for — so ownership would be unprovable at the
    one moment it is needed most, and a running server would be left up while
    the user is told it stopped.

    Every rule here was measured against the real `docker compose` rather than
    assumed, because a disagreement is not cosmetic: this value decides which
    containers the app believes are its own. Checked cases (review, 2026-08-22):

    * the LAST assignment wins, not the first — appending is exactly how a user
      follows the app's own "set COMPOSE_PROJECT_NAME=X in .env" advice;
    * `export ` is accepted;
    * an inline `# comment` is not part of the value — but a `#` inside quotes
      is, so it can only be stripped outside them;
    * surrounding single or double quotes are removed, and only a matched pair;
    * an empty assignment UNSETS it (compose falls back to the basename), so it
      cannot leave an earlier value standing;
    * a **UTF-8 byte-order mark** in front of the first line is not part of the
      first variable's name — see `utf-8-sig` below.

    `utf-8-sig`, not `utf-8`, and this one was found on Windows rather than
    reasoned about. `_stranger_message()` tells the user in as many words to add
    `COMPOSE_PROJECT_NAME=<x>` to this file, and on Windows the tools they have
    to hand put a BOM in front of it: PowerShell 5.1's `Set-Content -Encoding
    utf8` writes `EF BB BF`, and Notepad's "UTF-8 with BOM" does the same.
    Decoded as plain `utf-8` that becomes a leading `\\ufeff` on the first line,
    so `startswith("COMPOSE_PROJECT_NAME=")` is False and the pin is invisible.

    What makes it a defect rather than a quirk is that **compose reads the same
    file and does not agree**. Measured on Windows 11 / Docker 29.7.2
    (2026-08-23): with a BOM'd `.env`, `docker compose config` reports the
    project as `bomtest` while this function reported `None`. The two then
    disagree about which project this install is, which is the exact condition
    `install_project()` exists to prevent — and the fallback that hides it,
    asking compose, is unavailable in the one case this function exists for (an
    install whose compose files cannot be read). It also made `pin_project_name()`
    believe nothing was pinned and append a SECOND assignment, where compose's
    last-one-wins then silently overrides whatever the user had set.

    `utf-8-sig` strips a BOM if there is one and is byte-for-byte `utf-8` if
    there is not, so nothing else changes. A UTF-16 `.env` — what PowerShell's
    bare `>` and `Out-File` write — is deliberately NOT handled: compose cannot
    read one either, so that file is broken for both of us and guessing at an
    encoding here would be the app inventing an agreement that does not exist.
    """
    env_path = server_dir / ".env"
    if not env_path.is_file():
        return None
    try:
        text = env_path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return None
    found: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("export"):
            stripped = stripped[len("export") :].lstrip()
        if stripped.startswith(f"{PROJECT_NAME_VAR}="):
            value = _env_value(stripped.split("=", 1)[1])
            if "${" in value or "$" in value:
                # Needs interpolation; see `_env_value`. Report no pin rather
                # than a literal no container carries.
                logger.info(f"{PROJECT_NAME_VAR} in {env_path} needs expanding; asking compose")
                value = ""
            found = value or None
    return found


def _env_value(raw: str) -> str:
    """One `.env` right-hand side, read the way compose reads it.

    With one deliberate exception: compose expands `${VAR}` in unquoted and
    double-quoted values, and this does not. Reimplementing interpolation here
    would be a second copy of somebody else's rule — the thing
    `compose_project_name()`'s docstring refuses to do — so a value that needs
    expanding is reported as *no pin at all*, and ownership falls through to
    asking compose. That fails closed: the install stays stoppable while its
    compose files are readable, and refuses honestly when they are not, instead
    of silently believing in a project literally named `ac-${REALM}` that no
    container carries (review, 2026-08-23).
    """
    raw = raw.strip()
    for quote in ('"', "'"):
        if len(raw) >= 2 and raw.startswith(quote):
            end = raw.find(quote, 1)
            if end > 0:
                return raw[1:end]  # anything after the closing quote is a comment
            return raw[1:].strip()  # unterminated: take the rest, minus the quote
    return raw.split("#", 1)[0].strip()


PROJECT_LABEL = "com.docker.compose.project"


UNREADABLE = "\x00unreadable"
"""Docker would not say who owns a container — not the same as "nobody owns it".

Two failures used to collapse into `None` here: `docker inspect` erroring out,
and a container that genuinely carries no compose label. They need different
answers — the first is "ask again later", the second is "this is not ours" — so
the unreadable case gets a value no project name can collide with (review,
2026-08-22).
"""


def container_project(container: str) -> str | None:
    """Which compose project owns this container.

    Returns the project name, `None` for a container carrying no compose label
    (something started outside compose), or `UNREADABLE` when Docker could not
    be asked at all.
    """
    fmt = '{{index .Config.Labels "' + PROJECT_LABEL + '"}}'
    proc = _docker(["inspect", container, "--format", fmt])
    if proc.returncode != 0:
        logger.warning(f"could not read the compose project of {container}: {proc.stderr.strip()}")
        return UNREADABLE
    return proc.stdout.strip() or None


def install_project(spec: ContainerSpec, server_dir: Path) -> str | None:
    """This install's compose project, asked of the containers themselves first.

    The directory is the WRONG source of truth here. Compose derives a project
    from the directory basename, so a folder that has been moved reports a name
    no existing container carries — and an install created before pinning
    existed (which is every install in the wild) has no `.env` value to correct
    it. Comparing the directory's answer against the containers' labels then
    never matches, `_running()` finds nothing of ours, and a stop that stopped
    nothing reports success while players are still connected.

    It is tempting to read the identity off a container's own
    `com.docker.compose.project` label instead — that is what compose actually
    stamped, and it would fix the moved install outright. It also breaks the
    case this whole ownership check exists for: two installs of one game share
    container NAMES, so a stopped install would adopt the *running* install's
    project as its own and then stop it. Without a pin the two situations are
    genuinely indistinguishable from here, so the unresolved case is reported
    rather than guessed — see `stop_staged()`.

    The pin is therefore written at the one moment it is provably right: after
    this app's own installer finished, when the directory IS what compose just
    named the containers after. Attaching an existing install does not pin, an
    already-moved folder being exactly what that path exists to adopt.
    """
    return pinned_project_name(server_dir) or compose_project_name(server_dir)


@dataclass(frozen=True)
class Running:
    """Containers wearing this install's names, split by who actually owns them.

    A container name proves existence, never ownership. Two installs of one game
    carry identical container names — AzerothCore pins them globally — so a
    check that goes by name alone reports the *other* install's running server
    as this one's, and then acts on it.

    Every list is in stop order: world, then auth, then the database.
    """

    ours: tuple[str, ...] = ()
    """Running and carrying our project's label. Safe to act on."""

    strangers: tuple[tuple[str, str | None], ...] = ()
    """(container, the project that owns it) — `None` for "no compose label"."""

    unreadable: tuple[str, ...] = ()
    """Running, but Docker would not say who owns them. Not proof of anything."""


def _running(spec: ContainerSpec, project: str) -> Running:
    """Classify what is running under this install's names by compose project label.

    Compose stamps every container it creates with the project it belongs to,
    and that label is the only ownership proof available. Anything that is not
    provably ours ends up in `strangers` or `unreadable`, never in `ours` — the
    caller must fail closed on those and say so rather than fall back to a name.

    Raises:
        DockerCliMissingError: There is no docker CLI here — allowed through
            from `_status_safe()` so the user hears that rather than "the stop
            cannot be confirmed", which describes a Docker that answered badly
            and says nothing about one that is not installed.
        DockerCommandError: Docker could not be asked what is running at all,
            so no claim about this install can be made.
    """
    listed = _status_safe()
    if listed is None:
        raise DockerCommandError(
            "could not ask Docker what is running, so the stop cannot be confirmed"
        )
    running = {line.strip() for line in listed}
    ours: list[str] = []
    strangers: list[tuple[str, str | None]] = []
    unreadable: list[str] = []
    for name in (spec.world, spec.auth, spec.db):
        if name not in running:
            continue
        owner = container_project(name)
        if owner == UNREADABLE:
            unreadable.append(name)
        elif owner == project:
            ours.append(name)
        else:
            strangers.append((name, owner))
    return Running(tuple(ours), tuple(strangers), tuple(unreadable))


def container_exists(container: str) -> bool:
    """True if a container by that name exists at all, running or exited."""
    proc = _run(["ps", "-a", "--format", "{{.Names}}"])
    return any(line.strip() == container for line in proc.stdout.splitlines())


def start_staged(spec: ContainerSpec, server_dir: Path) -> bool:
    """Start this install's long-running services, and only those.

    `docker compose up -d` with no arguments starts every service that has no
    running container — including AzerothCore's one-shot `ac-db-import` and
    `ac-client-data-init`, which have already exited successfully. Re-running
    the import is what `dml-start.sh` warns about in as many words:

        # Use docker start (not compose up) so we do NOT re-trigger ac-db-import
        # or ac-client-data-init on every restart — that was killing the database.

    Naming the three services explicitly is the whole fix: compose cannot select
    a service that was not asked for, and `--no-deps` stops it pulling the
    import back in as a dependency of the servers. Measured against Docker
    29.1.3, this single command:

    - never runs the one-shot import — not on a plain restart, not when a
      compose file changed, and not when a container is missing;
    - recreates a service whose configuration changed, so an edited port or
      `AC_*` value actually takes effect;
    - recreates a container that no longer exists, without treating "missing
      container" as "never installed";
    - waits for `ac-database` to report healthy before starting the servers,
      because upstream's compose declares `condition: service_healthy` — and
      **fails closed** if it never does, rather than starting a worldserver
      against a dead database.

    That last point is why there is no health-polling here any more. `compose`
    owns the dependency graph; restating it in Python was how the ordering came
    to be documented but not delivered.

    An earlier version of this function tried to be clever: it checked whether
    the containers existed, compared compose config hashes, and started
    containers by name with `docker start`, falling back to a bare
    `compose up -d` whenever it was unsure. Every one of those fallbacks ran the
    destructive command, so the feature defeated itself exactly when it mattered
    — on a missing container or a changed setting. It also looked up containers
    by *global* name, so with two installs of the same game it could start the
    other one, silently. Addressing the project by its directory means the
    command never names a container, though `port_conflicts()` still does.

    What `--no-deps` costs, stated plainly: it prunes every `depends_on` edge
    whose target is outside the selected set, which drops `ac-db-import` (the
    point) and ALSO `ac-client-data-init`'s
    `condition: service_completed_successfully`. So an install interrupted
    part-way through the multi-GB client-data download leaves an incomplete
    volume that upstream would have finished and this will not. That is a
    narrow case with a loud symptom (the worldserver complains about missing
    DBC/map data), and the alternative — putting the init back in the service
    list — is only safe if its entrypoint is idempotent, which is unverified
    (review, 2026-08-22).

    Returns:
        True once every named service is actually running. `compose up` exiting
        0 is not on its own evidence of that: it is happy to report success for
        a container that started and died, and the caller would then sit out
        `wait_ready()`'s full timeout before hearing about it.

    Raises:
        DockerCommandError: compose failed, or a named service is not running
            once it returned.
    """
    services = spec.compose_services()
    logger.info(f"start_staged(): `compose up -d --no-deps {' '.join(services)}` in {server_dir}")
    _run(["compose", "up", "-d", "--no-deps", *services], cwd=server_dir)
    listed = _status_safe()
    if listed is None:
        logger.warning("start_staged(): could not confirm what is running; taking compose's word")
        return True
    running = {line.strip() for line in listed}
    missing = [name for name in (spec.db, spec.auth, spec.world) if name not in running]
    if missing:
        raise DockerCommandError(
            f"compose reported success but {', '.join(missing)} are not running. "
            f"`docker compose logs {missing[0]}` in {server_dir} will say why."
        )
    return True


def _project_containers(project: str) -> list[str] | None:
    """Every container compose stamped with `project`, running or exited.

    `container_exists()` cannot answer this: AzerothCore pins container names
    GLOBALLY (`ac-worldserver`, not `<project>-worldserver`), so a name search
    finds a second install's container and calls it ours. Filtering on the
    project label is the only way to ask about this install, and `-a` is what
    makes it about existence rather than about running.

    Returns None when Docker could not be asked, which callers must not read as
    "nothing is there".
    """
    proc = _docker(
        ["ps", "-a", "--filter", f"label={PROJECT_LABEL}={project}", "--format", "{{.Names}}"]
    )
    if proc.returncode != 0:
        logger.warning(f"could not list containers for project {project}: {proc.stderr.strip()}")
        return None
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def remove_staged(spec: ContainerSpec, server_dir: Path) -> bool:
    """Stop this install and REMOVE its containers. Volumes are never touched.

    The deliberate teardown, for a project that needs recreating rather than
    restarting: a container wedged in a state `compose up` will not fix, a
    compose change that needs a fresh container, or clearing the way before an
    install is deleted by hand.

    WHAT THIS DOES NOT DO, because it is the whole reason the action is safe:
    it never removes a volume. The database lives in a named volume
    (`${DOCKER_VOL_DB:-ac-database}:/var/lib/mysql`) and the client data in
    another, so `compose down` WITHOUT `-v` leaves every character exactly where
    it was. `-v` is the flag that would turn this into data loss, and a test
    asserts the argv never grows one.

    It also no longer costs the next start its database. An earlier version of
    this module warned that removing containers "forces the next start back onto
    `compose up -d`, and with it the one-shot database import" — that stopped
    being true when `start_staged()` began naming its three services explicitly.
    It selects `db auth world` and `--no-deps`, so compose cannot reach
    `ac-db-import` whether the containers are missing or not, and it recreates
    what is gone. The warning outlived the danger (2026-08-23).

    Ownership is proved the same way `stop_staged()` proves it, and refuses the
    same way, so the two actions cannot disagree about whose containers these
    are. `compose down` is project-scoped and would not touch a stranger anyway,
    but a census that cannot establish ownership means something is wrong with
    this install, and acting confidently on it is how the wrong server gets torn
    down.

    Returns:
        True if this install had containers and they are now gone; False if
        there was nothing of it to remove. Not `compose down`'s exit code, which
        is 0 for a project that never existed.

    Raises:
        DockerCommandError: Ownership could not be established, or containers of
            this install are still there afterwards.
    """
    logger.debug(f"remove_staged() called: server_dir={server_dir}")
    project = install_project(spec, server_dir)
    if project is None:
        _refuse_without_an_identity(spec, server_dir, "Nothing was removed.")
        return False

    # The same look-before-touching census as the stop path, for the same
    # reason: a refusal has to happen before the command, not after it.
    running = _running(spec, project)
    if running.unreadable:
        raise DockerCommandError(
            f"Docker would not say which project owns {', '.join(running.unreadable)}, so this "
            f"install in {server_dir} cannot prove those containers are its own. Nothing was "
            "removed."
        )
    if running.strangers:
        raise DockerCommandError(_stranger_message(running.strangers, project, server_dir))

    before = _project_containers(project)
    if before is None:
        raise DockerCommandError(
            "could not ask Docker which containers this install has, so nothing was removed"
        )
    if not before:
        logger.info("remove_staged(): this install has no containers")
        return False

    # `down` and not `rm`: it walks the project's own depends_on graph in
    # reverse, so the servers close their database connections before the
    # database goes. `--remove-orphans` clears services dropped from the compose
    # file, which are exactly the leftovers that make a project need recreating.
    # No `-v`, ever.
    # The grace matters here as much as it does on the stop path, and it was
    # missed the first time round: this button is offered on a RUNNING server,
    # under copy that says the characters are not affected. At Docker's 10s
    # default that copy is false for a populated realm — the worldserver is
    # SIGKILLed mid-drain and the save queue is what is lost, not the containers
    # (review, 2026-08-23; the measurement is under `STOP_GRACE_SECONDS`).
    proc = _docker(
        ["compose", "down", "-t", str(STOP_GRACE_SECONDS), "--remove-orphans"], cwd=server_dir
    )
    if proc.returncode != 0:
        logger.warning(f"compose down failed ({proc.stderr.strip()}); removing by name")

    after = _project_containers(project)
    if after is None:
        raise DockerCommandError(
            "the containers were asked to go, but Docker will no longer say what this install "
            "has, so the removal cannot be confirmed. Check with `docker ps -a`."
        )
    if after:
        # Only names the census already proved carry this project's label.
        logger.warning(f"compose down left {after}; removing by name")
        for name in after:
            # Stopped with the full grace BEFORE it is removed. `rm -f` is a
            # SIGKILL with no grace at all, so leaving it as the only fallback
            # left a hard-kill path reachable from the same button whose copy
            # promises the characters survive (review, 2026-08-23).
            _run_docker_stop(name)
            _docker(["rm", "-f", name])
        after = _project_containers(project) or []
    if after:
        raise DockerCommandError(f"still present after remove: {', '.join(after)}")

    logger.info(f"remove_staged(): removed {len(before)} container(s); volumes untouched")
    return True


STOP_GRACE_SECONDS = 300
"""Seconds a container gets between SIGTERM and the SIGKILL that follows it.

Docker's own default is 10, and 10 was measured killing a live save: a plain
`docker compose stop` against the populated worldserver on yulon-ubuntu came
back with exit code 137 (2026-08-23).

Measured on that box the same day — AzerothCore + playerbots, 1980 characters
online — two shutdowns run under a grace so long it could not bind
(`docker stop --timeout 900`) took **90.7s** and **73.4s**, both exit 0. A
third, run through `stop_staged()` itself with this constant in force, stopped
the whole project in **58.3s** with all three containers exit 0 and none of
them removed. So the same server, at the same population, varies by more than
half a minute between runs; the value has to cover the bad run.

Almost all of it is one phase. After `Halting process...` and `Logging out all
bots...` the worldserver logs `Closing down DatabasePool 'acore_characters'.
Waiting for 7662 queries to finish...` and then spends 86s, 69s and 52s in the
three samples draining that character save queue — 7400-7700 queued saves at
90-145 a second. The other two containers are nowhere near the constraint:
ac-authserver stopped in 0.22s and ac-database in 1.4s.

300 is therefore about 3.3x the worst thing seen. The margin is deliberately
generous and deliberately asymmetric. The queue depth scales with population
and drains at whatever the disk gives, so a larger realm or a slower disk moves
the number — while the only cost of an over-long grace is that a genuinely hung
server takes longer to give up, and the cost of being 30s short is a player's
characters rolled back to their last save. It happens to agree with the
`stop_grace_period: 5m` the earlier Rust launcher wrote into its generated
compose file (`pyplan/rust-prior-art.md` §2); that is now a confirmed number
rather than an inherited one.

This is the CLI grace on the stop path, not compose's `stop_grace_period` key.
The key belongs to the install engine that generates compose files, which this
repo does not do yet.
"""


DatabaseImport = Literal["absent", "partial", "imported", "populated", "unreadable"]
"""What a probe found in the databases the one-shot import is supposed to fill.

* `absent` — nothing of the schema set is there. Never imported.
* `partial` — some of it is there and some is not. An import that stopped.
* `imported` — all of it is there, and nobody has played on it yet.
* `populated` — there are accounts or characters. Possibly somebody's server.
* `unreadable` — the database could not be asked, so nothing is established.

The line that matters is between `populated` and everything else: it is drawn
on player data, not on completeness, because player data is the thing whose loss
cannot be undone. A half-imported database that somehow holds characters is
`populated` and is refused, even though the import really did stop half-way.

`populated` does NOT mean "somebody played here", and calling it that was
wrong until a live import said so (yulon-ubuntu, 2026-08-23). The one-shot
applies every module's `data/sql/db-auth` and `db-characters` updates as well
as AzerothCore's own, so a module is free to seed rows: a first-ever import of
an install carrying mod-city-bots came out of the box with 400 accounts and 400
characters, none of them a person's. The state is still refused, because
nothing here can tell a seeded row from a made one and the fail-closed answer
is the only safe one — but a caller must read it as "there is data that a
re-import would destroy", not as "this server has been played on".
"""


@dataclass(frozen=True)
class ImportState:
    """One probe's answer, with the sentence that produced it.

    `detail` is carried rather than recomputed by the caller because the probe
    is the only thing that knows *why* — which schema was missing, how many
    accounts were found — and a refusal that cannot say why is a refusal the
    user has no way to act on.
    """

    state: DatabaseImport
    detail: str = ""
    complete: bool = False
    """Whether every schema the import is supposed to fill actually has tables.

    Separate from `state` because the two answer different questions and only
    one of them is about danger. `populated` short-circuits the moment a single
    row exists, precisely so a database with player data in it is refused before
    anything is written — which means it says nothing at all about whether the
    schemas are finished. That is fine for the refusal and useless for the
    post-check, which needs to know whether the one-shot did its job.

    A review found the hole this closes: an import that applies a module's
    `db-auth` updates (mod-city-bots seeds 400 accounts) and then dies on the
    world schema leaves `populated` with `acore_world` empty. Reading only the
    state, the action reported that as a finished repair, hid its own button,
    and left the user with a broken server and a success message.

    Defaults False so a probe that does not compute it cannot claim completeness
    by omission.
    """

    @property
    def repairable(self) -> bool:
        """True only where re-running the import can put something right.

        `partial` is here only because `repair_import()` can be handed a
        `reset` that drops the half-written schemas before the one-shot runs.
        Without one it must not be, and a live gate is why. Re-running
        `ac-db-import` over a schema that already exists does NOT finish it:
        AzerothCore skips the base dump for a database that is already there,
        creates its `updates` bookkeeping, and seeds it with every known SQL
        file marked as applied. Measured on yulon-ubuntu, 2026-08-23 — an import
        killed 19 seconds in left `acore_world` with 3 tables of 316, and
        re-running the one-shot took it to 5 tables and **2671 rows in
        `acore_world.updates`**, after which no run would ever apply those files
        again. The action did not merely fail to repair the state it was built
        for, it destroyed the only route out of it. `repair_import()` still
        refuses `partial` outright when no `reset` is supplied, which is what
        makes this property safe to widen.

        Deliberately false for `unreadable` too. An unanswerable database is not
        an empty one — the same fail-closed rule `_refuse_without_an_identity()`
        applies to ownership — and offering a destructive button on the strength
        of a question nobody answered is how it gets pressed by accident.
        """
        return self.state in ("absent", "partial")


ResetUnfinished = Callable[[], tuple[str, ...]]
"""Drops the schemas an interrupted import left half-written; returns their names.

A second seam rather than a wider `ImportProbe`, for the same reason the probe
is read-only: this module must not know that a schema is called `acore_world`
(style-guide §3), and the one thing on this path that destroys data should be
the one thing whose type says it writes. The per-game module owns both.

Optional. Given none, `repair_import()` refuses a `partial` database rather than
making it worse — see `ImportState.repairable`.
"""

ImportProbe = Callable[[], ImportState]
"""Asks the install's databases what state they are in.

A seam, because this module must not know what a schema is called or which
table holds an account (style-guide §3) — those are per-game facts, and the
per-game module owns them. See `controller_wow_wotlk/repair.py`.
"""


OutputSink = Callable[[str], None]
"""Where a long-running docker command's output goes, one line at a time.

Called on whatever thread is running the command — which for every caller in
this app is a worker thread, never the GUI thread. A sink that writes into a
widget directly is therefore the bug this seam exists to make avoidable, not
the use it exists for: the UI hands in something that can cross a thread
boundary (`ui/widgets/job.py`'s `LineRelay`), and `run_attached()` never learns
what happens at the far end.
"""


def repair_import(
    spec: ContainerSpec,
    server_dir: Path,
    probe: ImportProbe,
    *,
    reset: ResetUnfinished | None = None,
    output: OutputSink | None = None,
    db_timeout: float = _DB_HEALTHY_TIMEOUT_SECONDS,
) -> bool:
    """Re-run this install's one-shot database import. For a BROKEN install only.

    The installer is the only thing that runs the import on a healthy path, and
    `start_staged()` exists precisely so an ordinary Start can never reach it.
    This is the repair for the one state that leaves behind: an install
    interrupted *after* its containers were created but *before* the import
    finished, whose Start now brings a worldserver up against empty schemas and
    fails in a way that explains nothing.

    It refuses far more often than it acts, and the refusals are the feature:

    * no `spec.import_service` — this game never said which service imports, and
      guessing a service name is guessing which container gets run;
    * ownership cannot be established, or containers wearing our names belong to
      another compose project. Identical to `remove_staged()`/`stop_staged()`, so
      the three cannot disagree about whose install this is;
    * an authserver or worldserver of this install is running. A live worldserver
      holds character state in memory, and the import writes underneath it;
    * the probe says the database holds player data. **This is the refusal that
      matters.** Re-importing over a populated database destroys characters, and
      it is not offered a second time for a user who asks twice — the way back
      from a bad database is Restore, which exists, and which was live-gated
      against a real 386 MB backup. On an install whose modules seed accounts
      this is also the refusal a *finished* repair leaves behind — pressing
      again lands here rather than on "already imported", which is a worse
      sentence for that case and still the right answer, since nothing can
      prove the rows are not a person's (see `DatabaseImport`);
    * the probe says the import already completed, or could not be asked at all.

    Two commands, both naming their service explicitly:

    1. `compose up -d --no-deps <db>`, only when the database is not already
       running. The import has to have somewhere to write, and the probe has to
       have something to ask — and a user cannot reach "database up, servers
       down" with the buttons this app has, since Start starts all three and
       Stop stops all three. Naming the database by hand is narrower than
       dropping `--no-deps` and letting compose's dependency graph decide what
       else comes up, which this repo has never measured.
    2. `compose up --no-deps <import_service>`, attached. `--no-deps` is what
       makes attached mode terminate: the one-shot is then the only container
       compose brings up, so `up` returns when it exits. Without it, `up` would
       also attach to the database and wait for a container that never stops.

    The exit code of that second command is not the answer, for the same reason
    `remove_staged()` does not believe `compose down`'s: the probe is run again
    afterwards, and only a database that is no longer repairable counts.

    **Live-gated on yulon-ubuntu, 2026-08-23**, against a copy of a real
    AzerothCore + playerbots + city-bots install on a brand-new empty volume,
    Docker 29.1.3. Three things this docstring had asserted without a daemon:

    * *Attached `up` terminates.* It does, and so does the detached database
      start. One whole call took 209.0s against a one-shot container that ran
      208.0s, so `up` came back within about a second of the import exiting.
      The database half: 23s from `compose up -d --no-deps <db>` to healthy on
      a brand-new volume, and 7.1s start-to-refusal on an existing container.
      "A full import takes several minutes", which is what the button says, is
      the right order of magnitude — 215s and 208s for two full imports.
    * *`compose up` re-runs a one-shot whose container already exists and has
      exited.* It does, with no `--force-recreate`. The same `rt-ac-db-import`
      container went `StartedAt 16:59:10 → 17:05:06`, `FinishedAt 17:02:45 →
      17:08:34`, exit 0 both times, and the second run refilled three schemas
      that had been `DROP DATABASE`d in between.
    * *A finished import leaves `acore_auth.account` empty.* **False**, and
      this is what the post-check below had to change for — see the comment
      there.

    `output`, when given, receives each line of the import's STDOUT as it prints
    it. Not every line: `runner.stream()` withholds stderr until the child has
    exited and then yields it in one block, so anything the one-shot writes
    there — compose's own `Container ... Created/Started` progress among it —
    arrives at the end rather than live. That is the same trade `follow_logs()`
    makes and it is what keeps a full pipe from deadlocking the child, but a
    caller putting these lines on screen should not promise the user more than
    it delivers (review, 2026-08-23). What is guaranteed is:
    see `run_attached()`, which is what turned that second command from a
    10-30 minute silence into something a caller can show. It is called on
    whatever thread is running this, so a UI sink has to be one that can cross
    threads. Passing nothing changes nothing except that the lines are dropped
    instead of forwarded; they are retained and reported either way.

    **This cannot be cancelled, deliberately.** There is no cancel token here
    and the UI offers no Stop, because the only way to abandon a running
    `compose up` is to terminate it — which stops `ac-db-import` part-way
    through writing schemas. That is a recoverable state (the probe reads it as
    `partial` and this action is offered again), but it is not one to hand a
    user a button for while the alternative is waiting.

    Returns:
        True once the probe says the import finished — every schema filled,
        whether or not the run also seeded rows of its own.

    Raises:
        DockerCommandError: any of the refusals above, the database never became
            healthy, or the import ran and the databases still read as `absent`,
            `partial` or `unreadable`.
    """
    logger.debug(f"repair_import() called: server_dir={server_dir}")
    service = spec.import_service
    if not service:
        raise DockerCommandError(
            "this game does not say which compose service imports its databases, so there is "
            "nothing to re-run. Nothing was changed."
        )

    project = install_project(spec, server_dir)
    if project is None:
        _refuse_without_an_identity(spec, server_dir, "The import was not re-run.")
        raise DockerCommandError(
            f"the install in {server_dir} cannot say which compose project it is — its compose "
            f"files are unreadable and no {PROJECT_NAME_VAR} is pinned — so the import was not "
            "re-run. Running it against the wrong project would overwrite the wrong database."
        )

    running = _running(spec, project)
    if running.unreadable:
        raise DockerCommandError(
            f"Docker would not say which project owns {', '.join(running.unreadable)}, so this "
            f"install in {server_dir} cannot prove those containers are its own. The import was "
            "not re-run."
        )
    if running.strangers:
        raise DockerCommandError(_stranger_message(running.strangers, project, server_dir))
    servers = [name for name in (spec.world, spec.auth) if name in running.ours]
    if servers:
        verb = "is" if len(servers) == 1 else "are"
        raise DockerCommandError(
            f"{', '.join(servers)} {verb} running. The import rewrites the databases underneath "
            "them, and a running worldserver holds characters in memory and saves them back over "
            "whatever it finds. Press Stop first, then try again."
        )

    start_database(spec, server_dir, timeout=db_timeout, because="nothing was imported")

    before = probe()
    logger.info(f"repair_import(): the databases read as {before.state} — {before.detail}")
    if before.state == "populated":
        raise DockerCommandError(
            f"this install's databases hold player data ({before.detail}). Re-running the import "
            "would overwrite it, so it was not run. If the database is damaged, restore the last "
            "backup from the Maintenance tab instead — that is the path that keeps characters."
        )
    if before.state == "imported":
        raise DockerCommandError(
            f"the import has already completed ({before.detail}), so there is nothing to repair. "
            "If the server still will not start, its logs are where the reason is."
        )
    if before.state == "partial":
        # Re-running the one-shot over a half-written schema is not a no-op, it
        # is destructive — see `ImportState.repairable` for the measurement. The
        # only thing that makes it work is handing the importer an EMPTY schema,
        # which is what `reset` does; without one this refuses rather than
        # leaving the install permanently unimportable.
        if reset is None:
            raise DockerCommandError(
                f"this install's import stopped part-way ({before.detail}), and re-running it "
                "cannot finish the job: AzerothCore skips the base data for a database that "
                "already exists, so the import would record every remaining file as applied and "
                "leave the schema permanently unfinished. Nothing was run. Remove this install's "
                "containers and database volume, then install again."
            )
        logger.warning(f"repair_import(): clearing the half-written schemas ({before.detail})")
        try:
            dropped = reset()
        except Exception as exc:
            # Deliberately broad, and re-raised as this module's own error. The
            # seam belongs to the per-game package and may raise its own types;
            # what a caller here has contracted for is `DockerCommandError`, and
            # a failure to clear must not reach the user as a failed import.
            raise DockerCommandError(
                "the half-written databases could not be cleared, so the import was not re-run "
                f"and nothing else was changed: {exc}"
            ) from exc
        if not dropped:
            raise DockerCommandError(
                f"the databases read as unfinished ({before.detail}), but nothing was found to "
                "clear, so the import was not re-run. Nothing was changed."
            )
        logger.warning(f"repair_import(): dropped {', '.join(dropped)}; re-running the import")
    if not before.repairable:
        raise DockerCommandError(
            f"the databases could not be asked what state they are in ({before.detail}), so the "
            "import was not re-run. Nothing can be established about them either way."
        )

    logger.warning(f"repair_import(): `compose up --no-deps {service}` in {server_dir}")
    run = run_one_shot(service, server_dir, sink=output)
    verify_import(probe, service, server_dir, run)
    return True


def start_database(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    timeout: float = _DB_HEALTHY_TIMEOUT_SECONDS,
    because: str = "nothing was run",
) -> None:
    """Start this install's database alone and wait for it to report healthy.

    Shared by `repair_import()` and by the native install engine's `start-db`
    stage (roadmap 6.2) so the two cannot drift, and byte-identical to the argv
    live-gated on yulon-ubuntu 2026-08-23 (23s from this call to healthy on a
    brand-new volume; 7.1s when the container already existed).

    Both callers need it for the same reason and it is not optional for either:
    the import one-shot runs with `--no-deps`, so compose will NOT bring up the
    database the `depends_on: service_healthy` edge names, and the import PROBE
    is a `docker exec ac-database mysql …` that answers `unreadable` when there
    is no such container. An install that skipped this refused itself at the
    import stage after a multi-hour build (review, 2026-08-23).

    Does nothing when the database container is already running, which is the
    ordinary case on a resume.

    `because` completes the sentence a timeout raises with, so a repair and an
    install each say what was not done.

    Raises:
        DockerCommandError: compose would not start it, or it never became
            healthy inside `timeout`.
    """
    if spec.db in set(status()):
        return
    # Started rather than demanded, because Stop takes the database down with
    # everything else — a user who followed the repair refusals would otherwise
    # have no way back to a state that action accepts.
    logger.info(f"start_database(): starting {spec.db} alone")
    # `compose_services()[0]`, not `spec.db`: compose takes SERVICE names and
    # `spec.db` is a CONTAINER name. They are equal for AzerothCore and
    # `ContainerSpec` exists precisely so a game whose compose file disagrees
    # can say so — reaching past it here would have made that promise false
    # for the first such game (review, 2026-08-23).
    _run(["compose", "up", "-d", "--no-deps", spec.compose_services()[0]], cwd=server_dir)
    if not wait_db_healthy(spec.db, timeout=timeout):
        raise DockerCommandError(
            f"{spec.db} did not report healthy within {timeout:.0f}s, so {because}. "
            f"`docker compose logs {spec.db}` in {server_dir} will say why."
        )


def run_one_shot(
    service: str,
    server_dir: Path,
    *,
    sink: OutputSink | None = None,
    cancel: threading.Event | None = None,
) -> AttachedRun:
    """Run one compose one-shot service attached, and return what it left behind.

    Byte-identical argv to the version live-gated against a real AzerothCore
    import on yulon-ubuntu (2026-08-23) — `--no-deps` is what makes an attached
    `up` terminate, because the one-shot is then the only container compose
    brings up. It is shared by the repair button and by the native install
    engine's `import`/`client-data` stages (roadmap 6.2) so the two can never
    drift into running different commands for the same job.

    The exit status is returned rather than raised for the reason
    `repair_import()` records: a one-shot that failed part-way and one that
    failed having done nothing exit alike, and only a probe of the result can
    tell them apart.
    """
    run = run_attached(
        ["compose", "up", "--no-deps", service], server_dir, sink=sink, cancel=cancel
    )
    if run.returncode != 0:
        # Not raised here. See above — the probe is the only thing that can
        # tell the two failures apart, so this only makes sure the reason is in
        # the log.
        logger.warning(f"{service} exited {run.returncode}: {last_words(run.tail)}")
    return run


def verify_import(
    probe: ImportProbe, service: str, server_dir: Path, run: AttachedRun
) -> ImportState:
    """Ask the databases whether the one-shot actually finished; raise if not.

    The post-check both the repair button and the install engine use, so
    "finished" means one thing in this codebase. Its two accepting answers are
    `imported`, and `populated` *with* `complete` — the second because an
    AzerothCore import applies every module's own `db-auth`/`db-characters`
    updates and a module is free to seed rows (measured: a first-ever import
    carrying mod-city-bots came out with 400 accounts and 400 characters,
    yulon-ubuntu 2026-08-23), and the `complete` half because `populated` alone
    reports an import that seeded rows and then died on the world schema as a
    success (review, 2026-08-23).

    Safe only because of the order the callers keep: `populated` is refused
    *before* the one-shot runs, so a database that is populated afterwards was
    populated by the run that just happened.

    Raises:
        DockerCommandError: the databases do not read as finished.
    """
    after = probe()
    # `populated` counts as success, and finding that out took a live import.
    # An AzerothCore import is not only AzerothCore's SQL: every module in the
    # tree gets its own `data/sql/db-auth` and `db-characters` updates applied
    # by the same one-shot, and a module is free to seed rows. Measured on
    # yulon-ubuntu, 2026-08-23: a first-ever import of an install carrying
    # mod-city-bots finished exit 0 with all three schemas full AND 400 rows in
    # `acore_auth.account` plus 400 in `acore_characters.characters`, every one
    # of them written by that module's own update files. Against `!= "imported"`
    # this raised "Nothing is imported that was not imported before" over an
    # import that had just done everything — the action reporting failure for
    # its own success, on the servers this project actually ships.
    #
    # But `populated` on its own is not enough, and a review caught that after
    # the gate had run. The probe answers `populated` the instant one row
    # exists, before it has looked at whether the schemas are finished — which
    # is right for the refusal and useless here. An import that applies the
    # module's `db-auth` updates and then dies on the world schema is
    # `populated` with `acore_world` empty, and this reported it as a finished
    # repair. So the post-check reads `complete` too, which is the question it
    # was actually asking all along.
    if after.state == "imported" or (after.state == "populated" and after.complete):
        pass
    elif after.state == "populated":
        # Rows but not every schema: the one-shot got far enough to apply a
        # module's `db-auth` updates and then died. Reading the state alone
        # called this a finished repair, hid the button, and left the user a
        # broken server with a success message (review, 2026-08-23).
        raise DockerCommandError(
            f"{service} ran and wrote some rows, but did not finish: {after.detail}. The "
            f"databases are in a half-imported state. Its last words were: "
            f"{last_words(run.tail)}. `docker compose logs {service}` in {server_dir} has the "
            "rest of what it printed."
        )
    else:
        raise DockerCommandError(
            f"{service} ran, but the databases still read as {after.state} ({after.detail}). "
            f"Nothing is imported that was not imported before. Its last words were: "
            f"{last_words(run.tail)}. `docker compose logs {service}` in {server_dir} has the "
            "rest of what it printed."
        )
    logger.info(f"{service} finished; the databases now read as {after.state}")
    return after


def _run_docker_stop(container: str) -> None:
    """`docker stop <container>`, blocking until that one container has exited.

    One call per container on purpose. `docker stop a b c` looks ordered and is
    not: the CLI fans a multi-name stop out one goroutine per name, so all three
    receive SIGTERM in the same instant and the order in argv means nothing —
    measured at 6.19s total for three containers where the *first* one traps
    SIGTERM for 6s. A single-container stop blocks until that container is gone,
    which is what makes "world before the database" real rather than decorative.

    The grace is `STOP_GRACE_SECONDS`, not Docker's 10-second default, for the
    reason recorded there: at 10s the populated worldserver is SIGKILLed while
    it is still writing character saves. This path is the fallback, so it is the
    one that runs for an install whose compose files cannot be read — exactly
    the install least able to survive losing a save queue.

    A container that has vanished since it was listed is not an error: the goal
    state is "not running", and it is already there.
    """
    # `-t` and not `--timeout`. Docker renamed the long form: through 27.x the
    # flag is spelled `-t, --time`, and `--timeout` only became valid in CLI
    # 28.0.0 (docker/cli#5485). The short form has meant the same thing across
    # every version this project can meet, so it is the only spelling that is
    # safe here — `--timeout` would exit 125 with `unknown flag` on any older
    # daemon, turning a working by-name stop into a hard failure (review).
    proc = _docker(["stop", "-t", str(STOP_GRACE_SECONDS), container])
    if proc.returncode == 0:
        return
    if "No such container" in proc.stderr:
        logger.debug(f"docker stop {container}: already gone")
        return
    raise DockerCommandError(f"docker stop {container} failed: {proc.stderr.strip()}")


def _refuse_without_an_identity(
    spec: ContainerSpec, server_dir: Path, nothing_was: str = "Nothing was stopped."
) -> None:
    """Raise if anything is running under our names while we cannot name our project.

    Returns quietly when nothing of the sort is up: there is simply nothing to
    stop, which is not an error.

    Raises:
        DockerCliMissingError: There is no docker CLI here. Deliberately allowed
            straight through from `_status_safe()` rather than folded into the
            message below. When Docker is absent, the project name is not
            *also* a problem — it is not a problem at all, since compose would
            have nothing to say it to — and naming it sends the user to edit
            their install's `.env` over a machine that has no Docker on it
            (review, 2026-08-23).
        DockerCommandError: Docker was asked and would not answer.
    """
    listed = _status_safe()
    if listed is None:
        # `or []` used to live here, which turned "Docker would not answer" into
        # "nothing is running" and returned False — the caller then told the user
        # the server had stopped while it was still serving. Socket permissions,
        # a wrong DOCKER_HOST and an API timeout under load all land here
        # (review, 2026-08-22).
        raise DockerCommandError(
            f"could not ask Docker what is running, and the install in {server_dir} has no "
            f"{PROJECT_NAME_VAR} pinned either, so nothing about it can be established. "
            f"{nothing_was}"
        )
    named = {line.strip() for line in listed}
    up = [name for name in (spec.world, spec.auth, spec.db) if name in named]
    if not up:
        logger.info("stop_staged(): no project name, and nothing running under our names")
        return
    raise DockerCommandError(
        f"cannot tell which containers belong to the install in {server_dir}: its compose files "
        "are unreadable and no COMPOSE_PROJECT_NAME is pinned, while "
        f"{', '.join(up)} are running. {nothing_was}"
    )


def _stranger_message(
    strangers: tuple[tuple[str, str | None], ...], project: str, server_dir: Path
) -> str:
    """Explain a name/label mismatch, and put the diagnosis before any remedy.

    Two rewrites, both from review. It first ended with "re-attach this
    install", which is worse than useless: attaching no longer pins at all, and
    the version that did would have written the *current* basename — the very
    value that produces this mismatch — after which `.env` outranks the
    directory forever and a recoverable state becomes permanent.

    It then led with "set COMPOSE_PROJECT_NAME=<their project>", which was
    measured doing real damage: a user who followed it literally on a genuine
    second install had Yu'lon adopt and stop the OTHER server, on Yu'lon's own
    instructions. So the first thing offered is now the command that tells the
    two cases apart, and the pin is offered only after it, only when there is
    exactly one candidate, and only as the branch that begins "if it is".
    """
    owners = sorted({owner for _name, owner in strangers if owner})
    unlabelled = [name for name, owner in strangers if not owner]
    labelled = [name for name, owner in strangers if owner]
    names = ", ".join(name for name, _owner in strangers)
    lines = [
        f"{names} are running, but they do not belong to the install in {server_dir}, which is "
        f"compose project '{project}'. Nothing was stopped: this could equally be another "
        "install of the same game, or this one after its folder was moved, and stopping the "
        "wrong one would take down a server somebody is playing on."
    ]
    if unlabelled:
        lines.append(
            f"{', '.join(unlabelled)} carry no compose project at all, so they were started "
            "outside compose — Yu'lon will not touch them."
        )
    if owners:
        which = " and ".join(f"'{owner}'" for owner in owners)
        lines.append(
            f"{', '.join(labelled)} belong to compose project {which}. Find out which folder "
            "that is before changing anything: `docker compose ls` prints each running "
            "project's own compose file. If it is a different install, stop that server from "
            "its own folder and leave this one alone."
        )
    if len(owners) == 1 and not unlabelled:
        # Both conditions matter. With a second, unlabelled stranger in the set,
        # adopting the one project still leaves that container foreign — the
        # next Stop refuses again, `owners` is then empty, and the message
        # offers no remedy at all. A permanent write that bought nothing.
        pinned = pinned_project_name(server_dir)
        if pinned is not None:
            # A folder move CANNOT produce this state once a pin exists — the
            # pin outranks the directory. So the causes are a genuinely
            # different install, or a `.env` copied along with the folder, and
            # saying "the folder was moved" here sent the user to change a file
            # in the copy case, which is how the copy stops the original's
            # server (review, 2026-08-22).
            lines.append(
                f"This install claims project '{pinned}' from {server_dir / '.env'}. If that "
                f"line was copied here from another install, delete it. If this install really "
                f"is '{owners[0]}', change it to that."
            )
        else:
            # The copy warning is not optional here. This is now the common
            # branch — almost nothing pins any more — and a user in the copy
            # case who follows the remedy literally reproduces the exact
            # failure that got the Stop-time pin deleted: the copy adopts the
            # original's project and the next Stop takes down the original's
            # server (review, 2026-08-23).
            lines.append(
                f"Only if this folder IS the install those containers came from — moved or "
                f"renamed, not copied — add {PROJECT_NAME_VAR}={owners[0]} to "
                f"{server_dir / '.env'}, or rename this folder to {owners[0]}. If it is a copy "
                f"of that install, do neither: adopting the name would make Stop here take "
                f"down the other server."
            )
    elif len(owners) > 1:
        lines.append(
            "More than one project is involved, so no single name can reconcile them: sort the "
            "installs out from their own folders first."
        )
    return " ".join(lines)


def stop_staged(spec: ContainerSpec, server_dir: Path) -> bool:
    """Stop this install without destroying its containers.

    The counterpart to `start_staged()`. `docker compose down` *removes* the
    containers, which is why it is not used here: a stop that removes leaves the
    next start with nothing to reuse. `compose stop` keeps them and walks the
    project's own `depends_on` graph, so the servers close their connections
    before the database goes away — upstream AzerothCore declares that graph and
    it is not ours to restate.

    The fallback stops this install's containers by name, one call at a time and
    in reverse order, for a project whose compose files cannot be read. Losing
    the ability to stop a running server because a file is missing would be a
    worse failure than losing the ordering guarantee. One call per container is
    not decorative: `docker stop a b c` signals all three at once (measured at
    6.19s for three containers where the *first* traps SIGTERM for 6s), so a
    multi-name call cannot express "world before the database".

    Both paths ask for `STOP_GRACE_SECONDS` instead of taking Docker's
    10-second default, which was measured SIGKILLing a populated worldserver
    while it was still flushing its character save queue. A Stop can therefore
    take minutes rather than seconds, and every caller of this runs it off the
    GUI thread already.

    Either way the result is verified rather than assumed. A zero exit from
    `compose stop` only means compose had nothing to complain about — it says
    so even for a project where nothing was running, and even where the
    containers holding these names belong to a different install. So the census
    is taken from `docker ps` plus the compose project label, once before the
    stop and again after it, and the exit code is never the answer.

    Returns:
        True if something of this install was running and is now down; False if
        there was nothing of it to stop. The old version returned `compose
        stop`'s exit code, which is 0 for an empty project — it said "stopped"
        having stopped nothing (review, 2026-08-22).

    Raises:
        DockerCommandError: Ownership could not be established, or something of
            this install is still running after the stop. Reporting success in
            either case would be the worst outcome: the user is told the server
            is down while players are still connected.
    """
    logger.debug(f"stop_staged() called: server_dir={server_dir}")
    project = install_project(spec, server_dir)
    if project is None:
        _refuse_without_an_identity(spec, server_dir)
        return False

    # Look before touching anything. Taking the census first is what lets the
    # refusals below happen before a `compose stop`, and what makes the return
    # value mean "there was something of ours and it is now down" rather than
    # "compose had nothing to complain about" (review, 2026-08-22).
    before = _running(spec, project)
    if before.unreadable:
        raise DockerCommandError(
            f"Docker would not say which project owns {', '.join(before.unreadable)}, so this "
            f"install in {server_dir} cannot prove those containers are its own. Nothing was "
            "stopped. This is usually Docker being unwell rather than a second install — try "
            "again in a moment."
        )
    if before.strangers:
        raise DockerCommandError(_stranger_message(before.strangers, project, server_dir))

    # No pin is written here, though the census has just proved the basename and
    # the labels agree. Writing it down was tried and reverted the same day: a
    # pin lives in `.env`, `.env` travels with the folder, and `install_project()`
    # prefers it over the directory — so copying an install (a second realm, a
    # restored backup) hands the copy the original's identity, and pressing Stop
    # in the copy stops the ORIGINAL's running server. Measured end to end.
    #
    # Not writing one does NOT make the copy safe, and an earlier version of
    # this comment claimed it did. An unpinned copy resolves to its own
    # basename, which catches `~/wow` copied to `~/wow2` and misses
    # `~/wow-server` copied to `/mnt/backup/wow-server` — same basename, same
    # project, same outcome. And the install-time pin in
    # `catalog_view._on_run_finished()` is inherited by any copy of a folder it
    # wrote, so that path carries the hazard too. This only declines to ADD a
    # third way in (review, 2026-08-23). The candidate real fix — reading the
    # containers' `com.docker.compose.project.working_dir` label and checking
    # whether that directory still exists, which separates a move from a copy —
    # is recorded in `pyplan/phase6-decisions.md`, not implemented here.
    #
    # The cost of not pinning is that an attached install whose compose files
    # later become unreadable cannot prove ownership, and
    # `_refuse_without_an_identity()` says so rather than guessing. That is the
    # right way round: refusing to stop a server is recoverable, stopping
    # somebody else's is not.

    # Run this even with nothing of ours in `docker ps`. The project holds more
    # than the three named containers — `ac-db-import` and `ac-client-data-init`
    # are services too — and an install interrupted during the multi-GB client
    # data download leaves one of those running. Skipping the command told the
    # user "nothing was running" while the download carried on (review).
    # `--timeout`, because compose's default is Docker's 10s and that was
    # measured SIGKILLing a populated worldserver mid-save (see
    # `STOP_GRACE_SECONDS`). It is per container, not for the whole project, and
    # only the worldserver ever comes close to using it.
    # `-t` for the same reason as `_run_docker_stop()`; compose has always
    # accepted the short form, and spelling the two call sites alike means a
    # future reader does not have to know which CLI they are looking at.
    proc = _docker(["compose", "stop", "-t", str(STOP_GRACE_SECONDS)], cwd=server_dir)
    if proc.returncode != 0:
        logger.warning(f"compose stop failed ({proc.stderr.strip()}); stopping containers by name")
    if not before.ours:
        # False means "none of the three SERVERS were up". It cannot mean
        # "nothing was stopped": the `compose stop` above may well have stopped
        # a one-shot service, and compose does not say what it touched. The
        # caller's wording has to match that (review, 2026-08-22).
        logger.info("stop_staged(): none of this install's servers were running")
        return False

    after = _running(spec, project)
    if after.ours:
        # Either `compose stop` failed, or it succeeded having matched nothing —
        # the moved-folder case, where compose names the project after the
        # directory. Finish the job by name rather than believing the exit code.
        logger.warning(f"compose stop left {list(after.ours)} running; stopping by name")
        for name in after.ours:
            _run_docker_stop(name)
        after = _running(spec, project)

    if after.ours:
        raise DockerCommandError(f"still running after stop: {', '.join(after.ours)}")
    if after.unreadable:
        # Reading only `.ours` here let a container that is plainly still up be
        # reported as stopped, because `docker inspect` had started failing and
        # dropped it into `unreadable` — the same condition that is a hard
        # refusal three lines above, silently discarded (review, 2026-08-22).
        raise DockerCommandError(
            f"{', '.join(after.unreadable)} are still running and Docker will no longer say "
            "which project owns them, so this stop cannot be confirmed. Check with "
            "`docker ps` before assuming the server is down."
        )
    logger.info("stop_staged(): stopped; containers kept for a fast restart")
    return True


def status() -> list[str]:
    """Return the names of all currently-running containers (`docker ps`).

    Raises:
        DockerCommandError: If the `docker` CLI itself fails (e.g. the daemon
            is unreachable). Callers polling in a loop (see `wait_ready()`)
            must not call this directly without handling that — use
            `_status_safe()`/the polling helpers below instead.
    """
    logger.debug("status() called")
    proc = _run(["ps", "--format", "{{.Names}}"])
    return [name for name in proc.stdout.splitlines() if name.strip()]


def _status_safe() -> list[str] | None:
    """Like `status()`, but returns `None` instead of raising on a *transient* failure.

    Used by polling loops (`wait_ready()`) where a single `docker ps` failure
    (daemon restart, brief overload, etc.) must be treated as "not ready this
    iteration, try again," not as a reason to abort the whole wait.

    `DockerCliMissingError` is not one of those and is re-raised. Degrading it
    here was measured doing real harm (2026-08-23): the two stop-path callers
    turn `None` into their own message, so a Windows box with no Docker was told
    its install had no `COMPOSE_PROJECT_NAME` pinned, and `wait_ready()` treated
    "there is no Docker on this machine" as a hiccup worth retrying 240 times.
    Every caller that wants the degradation still gets it for the failure it was
    written for.
    """
    try:
        return status()
    except DockerCliMissingError:
        raise
    except DockerCommandError as exc:
        logger.debug(f"status() failed during poll, will retry: {exc}")
        return None


def health(container: str) -> str:
    """Return a container's health status, or `"unknown"` if it can't be read.

    `"unknown"` is deliberately overloaded and covers several distinct cases
    that cannot be told apart from this return value alone: the container
    doesn't exist yet, the daemon is unreachable, *and* the container exists
    and is running fine but simply has no `HEALTHCHECK` defined (a common,
    valid configuration — `docker inspect`'s health-status template fails in
    that case too). Callers relying on a `"healthy"` result to mean "the
    container is up" should be aware an unhealthchecked container will never
    report `"healthy"` and will look identical to "container missing."
    Mirrors `dml-start.sh`'s `... || echo unknown`.
    """
    return _health(container)[0]


def _health(container: str) -> tuple[str, bool]:
    """`health()`'s answer, plus whether there was a docker CLI to ask.

    Split out so `wait_db_healthy()` can tell "no Docker on this machine" from
    every other reason the status is `"unknown"` without spending a second
    command per poll to find out. `health()`'s own answer still collapses the
    two, deliberately — see there — so this is additive, not a contract change.
    """
    logger.debug(f"health() called: container={container}")
    proc = _docker(["inspect", container, "--format", "{{.State.Health.Status}}"])
    if proc.returncode != 0 or not proc.stdout.strip():
        return "unknown", _cli_missing(proc)
    return proc.stdout.strip(), False


@dataclass(frozen=True)
class ContainerState:
    """A container's status and the start time of its current run."""

    status: str = ""
    started_at: str = ""

    @property
    def settled(self) -> bool:
        """True if it is running right now, rather than between restarts.

        Every compose service here carries `restart: unless-stopped`, and a
        container in restart backoff still appears in a plain `docker ps` while
        `.State.StartedAt` holds the previous run's timestamp. A crash-looping
        worldserver therefore satisfies both of `wait_ready()`'s other checks
        and is reported ready — the same false pass the `--since` scoping was
        added to remove, arriving by a different route (review, 2026-08-22).
        """
        return self.status == "running"


def container_state(container: str) -> ContainerState:
    """Status and current-run start time in ONE `docker inspect`.

    One call, not two, because `wait_ready()` asks for both every two seconds
    for up to eight minutes and a `docker inspect` costs about 0.3s of CLI
    startup on its own — two of them per container per poll took a healthy poll
    from five docker invocations to seven, enough to overrun the interval it
    names (review, 2026-08-22).
    """
    fmt = "{{.State.Status}}\t{{.State.StartedAt}}"
    proc = _docker(["inspect", container, "--format", fmt])
    if proc.returncode != 0:
        logger.warning(f"could not read the state of {container}: {proc.stderr.strip()}")
        return ContainerState()
    status, _, started = proc.stdout.strip().partition("\t")
    return ContainerState(status.strip(), started.strip())


def started_at(container: str) -> str:
    """When the container's CURRENT run began, or `""` if it cannot be read."""
    return container_state(container).started_at


def _logs(container: str, *, this_run_only: bool = False, since: str = "") -> str:
    """Return a container's logs, or `""` if they can't be read.

    `docker logs` prints everything the container has ever written, across every
    restart. That is the right default for showing a user what happened, and
    exactly wrong for deciding whether a server is ready *now*: a restarted
    worldserver still has the previous run's `ready...` sitting in its log, so a
    readiness check reads it and says yes while the new run is still loading.

    Measured on a real AzerothCore server (2026-08-22): after a stop/start
    cycle, `wait_ready()` returned True immediately, and the container's own
    last words were still `>> Loaded 13567 Quest Offer Reward Locale Strings` —
    it was mid-startup, and the stop that followed killed it there (exit 137).

    `this_run_only` scopes the read to the current run by asking when that run
    started. `--tail` is not an alternative: the marker is printed once, so a
    tail window either misses it or slides past it.
    """
    argv = ["logs"]
    if this_run_only:
        # The caller may already have the start time from the state read it
        # had to do anyway; asking again is a second `docker inspect`.
        since = since or started_at(container)
        if since:
            argv += ["--since", since]
    proc = _docker([*argv, container])
    if proc.returncode != 0:
        # Silently returning "" turned a rejected --since, a container removed
        # mid-wait, or an unreadable log driver into eight minutes of "starting"
        # followed by a generic failure with nothing naming the cause
        # (review, 2026-08-22). `_status_safe()` already logs its equivalent.
        logger.warning(f"could not read the logs of {container}: {proc.stderr.strip()}")
        return ""
    return proc.stdout


_CLI_MISSING_GRACE_SECONDS = 30.0
"""How long a poll keeps going with no docker CLI before it gives up.

Neither zero nor the full timeout, and both extremes were the wrong answer.

Zero — stop the first time the CLI does not resolve — would fight the cache
design in `platform.docker_program()`, which refuses to remember a miss
precisely so that Docker arriving mid-run is picked up. It would also turn a
Docker Desktop self-update, which replaces the executable the pinned path names,
into a failed start.

The full timeout is what this constant exists to end: 480s of `wait_ready()`
polling for a container that cannot appear, because there is nothing to ask.

30s is `_ensure_docker_linux()`'s own `min(wait_seconds, 30.0)` — the window the
provisioner already believes is enough for a Docker it just installed to become
answerable. Borrowing it keeps one number instead of inventing a second, and a
CLI that has not appeared inside the window the installer is given is not going
to appear because we waited another 450 seconds in silence.
"""


def _cli_missing_run(since: float | None, what: str) -> tuple[float, bool]:
    """Track a poll's run of consecutive missing-CLI iterations; True once it should stop.

    The stopping rule for both polling loops, in one place (style-guide §4).
    Pass the previous return's timestamp (or `None` on the first miss) and get
    back the timestamp to carry forward plus whether to give up.

    The user learns the cause once. WARNING on the first miss and on the
    decision to stop, DEBUG for everything between: `wait_ready()` issues five
    docker commands per poll for up to 480s, and 1200 copies of the same
    sentence is how a log stops being read.
    """
    now = time.monotonic()
    if since is None:
        logger.warning(f"{what}: {platform.DOCKER_CLI_MISSING_HELP}")
        return now, False
    waited = now - since
    if waited < _CLI_MISSING_GRACE_SECONDS:
        logger.debug(f"{what}: still no docker CLI after {waited:.1f}s; waiting")
        return since, False
    logger.warning(
        f"{what}: no docker CLI for {waited:.0f}s, so nothing can be watched or started here. "
        "Giving up rather than polling out the rest of the timeout in silence."
    )
    return since, True


def wait_db_healthy(
    db_container: str,
    timeout: float = _DB_HEALTHY_TIMEOUT_SECONDS,
    interval: float = _POLL_INTERVAL_SECONDS,
) -> bool:
    """Poll `health()` until the DB container reports `healthy` or time out.

    Mirrors `dml-start.sh`'s `_wait_db_healthy` (~90 checks at 2s), generalized
    with explicit timeouts so callers/tests aren't forced to sleep. Never
    raises `DockerCommandError` — `health()` already swallows CLI failures
    into `"unknown"`, which this treats as "not yet healthy."

    The one failure it does not treat that way is "there is no docker CLI on
    this machine": `"unknown"` is then not a container that is still starting,
    it is a question nobody was asked, and waiting 180s for the answer to change
    without saying why is not a diagnosis. See `_cli_missing_run()`.

    Note: worst-case wall-clock time before returning `False` is
    `timeout + interval` (the loop always sleeps once after its final check),
    not exactly `timeout`.

    Args:
        interval: Must be positive. A zero/negative interval would busy-loop
            the `docker` CLI with no delay; this is asserted, not silently
            allowed.
    """
    if interval <= 0:
        raise ValueError(f"interval must be positive, got {interval!r}")
    logger.debug(f"wait_db_healthy() called: db_container={db_container} timeout={timeout}")
    deadline = time.monotonic() + timeout
    cli_missing_since: float | None = None
    while time.monotonic() < deadline:
        status, cli_missing = _health(db_container)
        if cli_missing:
            cli_missing_since, give_up = _cli_missing_run(cli_missing_since, "wait_db_healthy()")
            if give_up:
                return False
        else:
            cli_missing_since = None
            if status == "healthy":
                return True
        time.sleep(interval)
    return False


def wait_ready(
    auth_container: str,
    world_container: str,
    realm_host: str,
    realm_port: int,
    timeout: float = _READY_TIMEOUT_SECONDS,
    interval: float = _POLL_INTERVAL_SECONDS,
) -> bool:
    """Poll until auth+world are up and both have emitted their ready markers.

    Mirrors `dml-start.sh`'s `_wait_ready`: the auth log must contain
    `<realm_host>:<realm_port>` and the world log must contain `ready...`. A
    transient `docker ps`/CLI failure during polling is treated as "not ready
    this iteration" and retried, never raised — see `_status_safe()`.

    A missing docker CLI is not treated as transient. It still does not raise —
    the answer is `False`, as it is for every other way a server fails to come
    up — but it is said out loud at WARNING and it stops within
    `_CLI_MISSING_GRACE_SECONDS` instead of polling out the default 480s with
    nothing above DEBUG to show for it (this happened; fixed 2026-08-23).

    Both markers are looked for in the CURRENT run's logs only. Docker keeps a
    container's output across restarts, so a restarted server still carries the
    previous run's `ready...`; reading the whole log made this return True
    instantly on every restart, while the server was in fact still loading.

    Note: worst-case wall-clock time before returning `False` is
    `timeout + interval`, same caveat as `wait_db_healthy()`.

    Args:
        interval: Must be positive (see `wait_db_healthy()`).
    """
    if interval <= 0:
        raise ValueError(f"interval must be positive, got {interval!r}")
    logger.debug(
        f"wait_ready() called: auth_container={auth_container} "
        f"world_container={world_container} realm={realm_host}:{realm_port}"
    )
    target = f"{realm_host}:{realm_port}"
    deadline = time.monotonic() + timeout
    cli_missing_since: float | None = None
    while time.monotonic() < deadline:
        try:
            running = _status_safe()
        except DockerCliMissingError:
            cli_missing_since, give_up = _cli_missing_run(cli_missing_since, "wait_ready()")
            if give_up:
                return False
            time.sleep(interval)
            continue
        cli_missing_since = None
        listed = running is not None and auth_container in running and world_container in running
        if listed:
            # `docker ps` lists a container in restart backoff, so being listed
            # is not the same as being up. One inspect per container answers
            # both that and "when did THIS run start".
            auth = container_state(auth_container)
            world = container_state(world_container)
            if (
                auth.settled
                and world.settled
                and target in _logs(auth_container, this_run_only=True, since=auth.started_at)
                and "ready..." in _logs(world_container, this_run_only=True, since=world.started_at)
            ):
                return True
        time.sleep(interval)
    return False


def port_conflicts(ports: tuple[int, ...]) -> list[str]:
    """Return the names of *any* running containers currently binding `ports`.

    This is the single-instance guard from README §12: all v1 servers share
    the same ports, so a second install cannot be started while the first
    still binds them. This is a **global** port scan — it has no concept of
    "which install" a container belongs to, so it will also flag the same
    install's own containers (e.g. on a restart) or an unrelated non-Yu'lon
    container that happens to publish the same port; it does not distinguish
    those cases from a genuine second-install conflict. Parses host-side
    publishes (`:PORT->`) from `docker ps`.
    """
    logger.debug(f"port_conflicts() called: ports={ports}")
    if not ports:
        return []
    proc = _run(["ps", "--format", "{{.Names}}\t{{.Ports}}"])
    conflicts: list[str] = []
    for line in proc.stdout.splitlines():
        if "\t" not in line:
            continue
        name, ports_field = line.split("\t", 1)
        for port in ports:
            if f":{port}->" in ports_field:
                conflicts.append(name)
                break
    return conflicts


def wait_db_healthy_for(spec: ContainerSpec, **kwargs: float) -> bool:
    """`wait_db_healthy()` for `spec.db`. `kwargs` forwards `timeout`/`interval`."""
    return wait_db_healthy(spec.db, **kwargs)


def wait_ready_for(spec: ContainerSpec, realm_host: str, realm_port: int, **kwargs: float) -> bool:
    """`wait_ready()` for `spec.auth`/`spec.world`. `kwargs` forwards timeout/interval."""
    return wait_ready(spec.auth, spec.world, realm_host, realm_port, **kwargs)


def port_conflicts_for(spec: ContainerSpec) -> list[str]:
    """`port_conflicts()` for `spec.ports` — the convenience form callers want."""
    return port_conflicts(spec.ports)


def foreign_port_conflicts(spec: ContainerSpec, project: str) -> list[str]:
    """`port_conflicts_for()` minus the containers belonging to `project`.

    The global scan cannot answer "is this MY server?", and a caller that
    refuses on its raw answer refuses its own install. That is not theoretical:
    the native engine's preflight re-runs on every resume, so once `up` had
    started the three containers — which carry `restart: unless-stopped` — the
    next attempt was told "ac-authserver, ac-worldserver already publish the
    ports this server needs. Stop that server first (or remove its containers)",
    naming the containers of the install being finished (review, 2026-08-23).

    Ownership is decided the way `_running()` decides it: the compose project
    label, which is the only proof there is. A container Docker will not answer
    for is NOT filtered out — an unreadable owner is not proof of ownership, and
    the caller refusing is the fail-closed direction here.
    """
    conflicts = port_conflicts(spec.ports)
    if not conflicts:
        return []
    return [name for name in conflicts if container_project(name) != project]


def published_bindings() -> dict[int, str]:
    """Host address each published port is bound to, parsed from `docker ps` (`{{.Ports}}`).

    The guide's LAN check: `0.0.0.0:3724->3724/tcp` reaches the network,
    `127.0.0.1:3724->3724/tcp` does not (and on WSL2 needs a portproxy). IPv6
    publishes (`[::]:3724->`) are ignored; the first IPv4 binding per port wins.
    """
    logger.debug("published_bindings() called")
    proc = _run(["ps", "--format", "{{.Ports}}"])
    bindings: dict[int, str] = {}
    for line in proc.stdout.splitlines():
        for part in line.split(","):
            part = part.strip()
            if "->" not in part or part.startswith("["):
                continue
            host_side = part.split("->", 1)[0]
            if ":" not in host_side:
                continue
            address, _, port_text = host_side.rpartition(":")
            if port_text.isdigit():
                bindings.setdefault(int(port_text), address)
    return bindings


def follow_logs(container: str, tail: int = 200) -> Iterator[str]:
    """Stream `docker logs -f` for one container (the Console tab's log source).

    Lives here so no `ui/` module ever builds a docker argv itself
    (style-guide §3; review finding, 2026-08-21).

    The one site in this module that raises rather than returning a failed
    `CompletedProcess`: `runner.stream()` yields lines, so there is no exit
    status to hand back. `LogPanel`'s worker catches everything the source
    raises and shows it as `"<type>: <message>"`, so a missing CLI reads as
    "DockerCliMissingError: Docker could not be found on this machine..." in the
    panel — where the unresolved name used to surface a bare WinError 2.
    """
    logger.debug(f"follow_logs() called: container={container} tail={tail}")
    program = platform.docker_program()
    if program is None:
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)
    try:
        yield from runner.stream([program, "logs", "-f", "--tail", str(tail), container])
    except OSError as exc:
        # The same uninstalled-mid-run case `_docker()` handles, arriving from
        # `Popen` on the first line instead of from `subprocess.run`. Both roads
        # have to end at the same sentence, or the panel shows a WinError for
        # one kind of missing docker and an explanation for the other.
        raise DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP) from exc


KEEP_OUTPUT_LINES = 200
"""How many of a streamed command's output lines are kept for the failure text.

A bound rather than a buffer, and the number is chosen for what has to survive
rather than for what looks generous. `runner.stream()` yields all of stdout as
it arrives and then every line of stderr in one block at the end, so the LAST
lines are the ones that explain a failure — which is the whole reason anything
is retained at all.

The alternative, keeping everything, is what this replaces: a full AzerothCore
import runs 10-30 minutes and prints a line per SQL file, and the previous
version held all of it in one string inside a GUI process that may then stay
open for days. Unbounded growth driven by how long somebody's import took is a
defect of its own, and one that gets worse on exactly the slow machine whose
import is worth watching.

200 lines is roughly 20 KB. What it costs: the middle of a failed import is
gone from this process. It is not gone from Docker — `docker compose logs
<service>` still has all of it, and the failure message says so.
"""

_LAST_WORDS_LINES = 5
_LAST_WORDS_CHARS = 400
"""How much of the retained tail goes into a message a user reads.

The retained tail is for the log; a `QLabel` on the Server tab is for one
paragraph. Five lines capped at 400 characters is enough to carry a mysql error
and not enough to push the rest of the tab off screen.
"""


def last_words(tail: tuple[str, ...]) -> str:
    """The end of a command's output, short enough to put inside a sentence.

    Blank lines are dropped before the count, because a shell script's spacing
    is exactly what a five-line window cannot afford to spend itself on.
    """
    said = [line.strip() for line in tail if line.strip()]
    if not said:
        return "it printed nothing at all"
    text = " / ".join(said[-_LAST_WORDS_LINES:])
    return text if len(text) <= _LAST_WORDS_CHARS else "…" + text[-_LAST_WORDS_CHARS:]


@dataclass(frozen=True)
class AttachedRun:
    """What a streamed docker command left behind: its exit code and the end of its output."""

    returncode: int
    tail: tuple[str, ...] = ()


def run_attached(
    argv: list[str],
    cwd: Path,
    *,
    sink: OutputSink | None = None,
    keep: int = KEEP_OUTPUT_LINES,
    cancel: threading.Event | None = None,
    merge_stderr: bool = False,
) -> AttachedRun:
    """Run `docker <argv...>` attached, handing stdout lines to `sink` as they arrive.

    The streaming counterpart to `_docker()`, and the second site in this module
    that goes through `runner.stream()` rather than `runner.run()`. It lives
    beside `follow_logs()` for the reason recorded there: no `ui/` module builds
    a docker argv (style-guide §3), so a view that wants to show a command's
    output asks for a sink rather than for a subprocess.

    Why it exists: `_docker()` buffers a whole run into one string and returns
    it at the end, which for the database import means 10-30 minutes in which
    the caller has nothing to say and the user cannot tell a long job from a
    hung one. The argv is unchanged by this — only the reading of it is.

    Three deliberate differences from `runner.stream()`'s own contract:

    * **The exit status is returned, never raised.** `stream()` raises
      `CalledProcessError` once an exhausted generator finds a non-zero exit;
      that would take the decision away from callers who must go on and check
      something else (see `repair_import()`, where a one-shot that failed
      part-way and one that failed having done nothing exit alike).
    * **Only the last `keep` lines are retained** — see `KEEP_OUTPUT_LINES`.
      The sink sees every line, though not all of them live — stdout arrives as
      it is written and stderr in one block after the child exits. This process
      remembers a bounded end of them.
    * **A sink that raises is dropped, not propagated.** Letting it out would
      abandon the generator mid-iteration, and `stream()` terminates the child
      when that happens — so a widget deleted while an import was running would
      kill the import. The first failure is logged and nothing is sent onward
      after it.

    A host with no docker CLI comes back as `_CLI_MISSING_RETURNCODE` carrying
    `DOCKER_CLI_MISSING_HELP`, the same shape `_docker()` gives it, rather than
    as an exception — the callers here already have to handle a failed run.

    `cancel`, when set mid-run, stops reading and lets `runner.stream()`'s
    generator-abandonment path terminate the compose client. The result comes
    back as `CANCELLED_RETURNCODE`. What it does NOT do is stop work already
    handed to the daemon: BuildKit finishes the build step it is on, and a
    one-shot container keeps running to completion. That is desirable (the
    layer cache keeps the work, and a resumed install re-probes the databases)
    and it is the caller's job to say so, per stage — see
    `native.BUILD_CANCEL_NOTE` and its neighbours. `repair_import()`
    deliberately passes no cancel at all; see there.

    `merge_stderr` is for the build, whose entire progress output is stderr;
    see `runner.stream()`.
    """
    logger.debug(f"run_attached() called: argv={argv} cwd={cwd}")
    tail: deque[str] = deque(maxlen=keep)
    program = platform.docker_program()
    if program is None:
        logger.debug(f"no docker CLI on this host; not running: docker {' '.join(argv)}")
        return AttachedRun(_CLI_MISSING_RETURNCODE, (platform.DOCKER_CLI_MISSING_HELP,))
    live = sink
    try:
        # `closing`, not a bare `for`: leaving the loop early has to CLOSE the
        # generator for `stream()`'s finally to terminate the child, and
        # relying on the loop variable falling out of scope makes that depend
        # on refcounting rather than on the code saying so.
        with closing(runner.stream([program, *argv], cwd=cwd, merge_stderr=merge_stderr)) as lines:
            for line in lines:
                if cancel is not None and cancel.is_set():
                    logger.warning(f"docker {' '.join(argv)} was cancelled; abandoning the client")
                    return AttachedRun(CANCELLED_RETURNCODE, tuple(tail))
                tail.append(line)
                if live is not None:
                    try:
                        live(line)
                    except Exception as exc:  # noqa: BLE001 - a dead sink must not kill the child
                        logger.warning(f"the output sink stopped accepting lines: {exc}")
                        live = None
    except subprocess.CalledProcessError as exc:
        return AttachedRun(exc.returncode, tuple(tail))
    except OSError as exc:
        # Docker uninstalled while the launcher is open, arriving from `Popen`;
        # `follow_logs()` handles the same case one line above.
        logger.warning(f"{program} could not be started: {exc}")
        return AttachedRun(_CLI_MISSING_RETURNCODE, (platform.DOCKER_CLI_MISSING_HELP,))
    return AttachedRun(0, tuple(tail))


# ------------------------------------------------------------- the build

_TIMEOUT_RETURNCODE = 124
"""What `runner.run()` reports for a command that did not answer in time.

Its own choice, borrowed from `timeout(1)`; named here so `bind_mount_ok()`
can tell "Docker said no" from "Docker said nothing".
"""

BIND_PROBE_TIMEOUT_SECONDS = 30.0
"""How long the bind-mount probe gets, first image pull included.

Reconciles two numbers. `pyplan/rust-prior-art.md` §3 bounds probes at 30s
because a wedged dockerd accepts the socket and never answers; the 5-second
figure in roadmap 6.2 assumed the image was already pulled. The probe uses the
same `alpine/git` image the clone stages need anyway, so on a first install the
bound has to cover pulling it — a bound too short to pull under would report
"your folder is not shared with Docker" for what is really a slow network.
"""


def build_staged(
    server_dir: Path,
    compose_files: Sequence[str],
    *,
    sink: OutputSink | None = None,
    cancel: threading.Event | None = None,
) -> AttachedRun:
    """Build this install's images from the THREE compose files, streamed.

    The only builder. `compose_files` must be the base, the override and the
    build overlay, in that order, because of a trap that costs a whole install:
    a bare `docker compose build` in a generated install's directory builds
    NOTHING and exits 0 — the `build:` blocks live in a file compose never
    auto-loads — and naming any `-f` at all disables auto-loading, so the base
    and the override have to be listed too or the build loses the image tags
    and runtime env it is meant to produce (`rust-prior-art.md` §2).
    Centralising the argv here is what keeps a caller from spelling that
    discipline a second, wrong way (style-guide §4).

    `--progress plain` is deliberate: the default renders an ANSI progress
    display for a terminal that is not there, and a log panel would show the
    escape sequences instead of the build. Output is read with `merge_stderr`,
    because BuildKit writes ALL of its progress to stderr and `runner.stream()`
    otherwise withholds it until the child exits — which for a two-to-four-hour
    compile is a blank panel for the entire build.

    Unbounded on purpose (rust-prior-art §1: probes are bounded, builds are
    not). Returns the run rather than raising, so the caller can tell a
    cancellation from a failure.
    """
    argv = ["compose"]
    for name in compose_files:
        argv += ["-f", name]
    argv += ["build", "--progress", "plain"]
    logger.info(f"build_staged(): `docker {' '.join(argv)}` in {server_dir}")
    return run_attached(argv, server_dir, sink=sink, cancel=cancel, merge_stderr=True)


def images_built(server_dir: Path, compose_files: Sequence[str]) -> bool | None:
    """Has this install's build already produced images? None = could not ask.

    A hint that can contradict the state file's "built", for the install
    engine's `build` stage (rust-prior-art §1). The same `-f` discipline as
    `build_staged()` applies, since the build overlay is where the built stages'
    image refs come from.

    **Not the disk evidence it was once documented as, and the difference is on
    the first gate's list.** Compose v2's `images` enumerates the images of the
    project's CREATED CONTAINERS, not of its service definitions — so in the
    exact window this is asked in (build finished, `up` not yet run) it very
    likely answers empty and a resume re-runs `compose build` (review,
    2026-08-23). That direction is safe rather than wrong — BuildKit's cache
    makes the re-run cheap and a false "built" would start a server with no
    binaries — but nobody here has watched it answer, and the gate's job is to
    run `compose images -q` against a built project with no containers and
    record what it says.

    `None` is not `False`. A resume that cannot ask must not conclude "nothing
    is built" and spend four hours proving itself wrong, and it must not
    conclude "built" either; the caller decides.
    """
    argv = ["compose"]
    for name in compose_files:
        argv += ["-f", name]
    argv += ["images", "-q"]
    proc = _docker(argv, cwd=server_dir)
    if proc.returncode != 0:
        logger.warning(f"could not list built images in {server_dir}: {proc.stderr.strip()}")
        return None
    return any(line.strip() for line in proc.stdout.splitlines())


def bind_mount_ok(
    server_dir: Path, image: str, *, timeout: float = BIND_PROBE_TIMEOUT_SECONDS
) -> bool | None:
    """Can a container actually see the chosen folder? None = could not ask.

    Docker Desktop shares only the directories its file-sharing settings list.
    A folder outside them mounts as an EMPTY directory rather than failing the
    run, so the clone appears to succeed, the build gets an empty context, and
    the first thing that reports the problem is a compile error hours later.

    **The exit code of an `ls` cannot detect that**, and an earlier version of
    this function claimed it could. `ls` on an empty directory exits 0, and the
    server directory is empty or absent at preflight time by construction — so
    against the silently-empty mount this exists for, the probe answered True
    and preflight printed `[pass] sharing the folder with Docker` (review,
    2026-08-23). What it can do is compare CONTENTS: mount a directory the host
    can see files in, and check the container sees files too.

    So the probe runs against the deepest ANCESTOR of `server_dir` that exists
    and has entries in it, not against `server_dir`. Two reasons, both
    load-bearing. Docker Desktop shares whole trees, so an ancestor's answer is
    the chosen folder's answer; and `-v <server_dir>:/probe` on a folder that
    does not exist yet makes Docker CREATE it, which put a directory on disk
    before `guard` had claimed it.

    `None` means the question could not be answered: no docker CLI, the daemon
    did not reply inside `timeout`, or no ancestor with anything in it could be
    found to compare against. That is preflight's *unchecked* tri-state, never a
    pass and never a refusal — a caller that read it as False would refuse an
    install that would have worked.

    Unverified on macOS, and this is the check most in need of the first gate:
    that Docker Desktop mounts an unshared folder as empty rather than failing
    is inherited from the Rust launcher, and what a Mac actually does with an
    unshared path is exactly what nobody here can run.
    """
    mount = _first_populated_ancestor(server_dir)
    if mount is None:
        logger.info(
            f"nothing on the way up from {server_dir} had files in it, so a container's view of "
            "it cannot be compared against the host's"
        )
        return None
    # `:ro`, because the mount is now an ANCESTOR of the chosen folder and that
    # is routinely the user's home directory. The probe only lists; the clone
    # stage is the thing that needs write access, and it gets its own mount.
    proc = _docker(
        ["run", "--rm", "-v", f"{mount}:/probe:ro", image, "ls", "-A", "/probe"], timeout=timeout
    )
    if _cli_missing(proc):
        return None
    # A non-zero exit is the daemon answering "no", which IS an answer — unless
    # it never answered at all. `runner.run()` reports a timeout as 124 with the
    # reason in stderr rather than raising, so that case has to be separated out
    # or a wedged dockerd reaches the user as "your folder is not shared with
    # Docker Desktop", which is a different fix entirely.
    if proc.returncode == _TIMEOUT_RETURNCODE:
        logger.warning(f"the bind-mount probe of {mount} did not answer within {timeout:.0f}s")
        return None
    if proc.returncode != 0:
        logger.warning(f"the bind-mount probe of {mount} failed: {proc.stderr.strip()}")
        return False
    if any(line.strip() for line in proc.stdout.splitlines()):
        return True
    logger.warning(
        f"a container saw {mount} as empty although the host sees files in it — Docker is not "
        "sharing that folder"
    )
    return False


def _first_populated_ancestor(path: Path) -> Path | None:
    """The nearest directory at or above `path` that exists and is not empty.

    The probe needs a host directory whose contents it can hold the container's
    answer up against; an empty one proves nothing either way.
    """
    probe = path
    while True:
        try:
            if probe.is_dir() and any(probe.iterdir()):
                return probe
        except OSError as exc:
            logger.info(f"could not look inside {probe}: {exc}")
            return None
        if probe == probe.parent:
            return None
        probe = probe.parent
