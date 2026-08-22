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
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

from yulon import runner
from yulon.log import get_logger

logger = get_logger(__name__)

_DB_HEALTHY_TIMEOUT_SECONDS = 180.0
_READY_TIMEOUT_SECONDS = 480.0
_POLL_INTERVAL_SECONDS = 2.0


class DockerCommandError(RuntimeError):
    """Raised when a `docker` CLI command exits with a non-zero status."""


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

    def compose_services(self) -> tuple[str, ...]:
        """The long-running compose services, in dependency order (db first).

        Deliberately excludes one-shot services such as `ac-db-import`: naming
        the services explicitly is what keeps `compose up` from ever selecting
        the import job. See `start_staged()`.
        """
        return self.services or (self.db, self.auth, self.world)


def _run(argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run `docker <argv...>`; raise `DockerCommandError` on non-zero exit."""
    proc = runner.run(["docker", *argv], cwd=cwd)
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
    proc = runner.run(["docker", "compose", "config", "--format", "json"], cwd=server_dir)
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
    this behaviour comes from; doing it at install and attach time instead means
    the folder can be moved by any means — a file manager, a backup restore —
    and still work.

    Returns the pinned name, or None if nothing was written (already pinned, or
    compose could not be asked).
    """
    env_path = server_dir / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip().startswith(f"{PROJECT_NAME_VAR}="):
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
    """
    env_path = server_dir / ".env"
    if not env_path.is_file():
        return None
    try:
        text = env_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{PROJECT_NAME_VAR}="):
            value = stripped.split("=", 1)[1].strip().strip("\"'")
            return value or None
    return None


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
    proc = runner.run(["docker", "inspect", container, "--format", fmt])
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


def start_staged(
    spec: ContainerSpec,
    server_dir: Path,
    *,
    wait_healthy: Callable[[str], bool] | None = None,
) -> bool:
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
    other one, silently. Addressing the project by its directory fixes that by
    construction.

    Args:
        wait_healthy: Retained for callers that still pass it; unused, because
            compose now performs the wait. Accepting and ignoring it keeps the
            signature stable for one release rather than breaking every caller.

    Returns:
        True — the servers were started without touching the one-shot jobs.
        The bool is kept so callers need not change; there is no longer a
        destructive path for it to warn about.
    """
    del wait_healthy  # compose does the health wait now; see the docstring
    services = spec.compose_services()
    logger.info(f"start_staged(): `compose up -d --no-deps {' '.join(services)}` in {server_dir}")
    _run(["compose", "up", "-d", "--no-deps", *services], cwd=server_dir)
    return True


def stop(server_dir: Path) -> None:
    """Take the compose project in `server_dir` down, REMOVING its containers.

    This is the teardown path (uninstall, or recovering from a broken project).
    For the stop half of a normal start/stop cycle use `stop_staged()`: removing
    the containers here is what forces the next start back onto `compose up -d`,
    and with it the one-shot database import.
    """
    logger.debug(f"stop() called: server_dir={server_dir}")
    _run(["compose", "down"], cwd=server_dir)


def _run_docker_stop(container: str) -> None:
    """`docker stop <container>`, blocking until that one container has exited.

    One call per container on purpose. `docker stop a b c` looks ordered and is
    not: the CLI fans a multi-name stop out one goroutine per name, so all three
    receive SIGTERM in the same instant and the order in argv means nothing —
    measured at 6.19s total for three containers where the *first* one traps
    SIGTERM for 6s. A single-container stop blocks until that container is gone,
    which is what makes "world before the database" real rather than decorative.

    A container that has vanished since it was listed is not an error: the goal
    state is "not running", and it is already there.
    """
    proc = runner.run(["docker", "stop", container])
    if proc.returncode == 0:
        return
    if "No such container" in proc.stderr:
        logger.debug(f"docker stop {container}: already gone")
        return
    raise DockerCommandError(f"docker stop {container} failed: {proc.stderr.strip()}")


def _refuse_without_an_identity(spec: ContainerSpec, server_dir: Path) -> None:
    """Raise if anything is running under our names while we cannot name our project.

    Returns quietly when nothing of the sort is up: there is simply nothing to
    stop, which is not an error.
    """
    named = {line.strip() for line in _status_safe() or []}
    up = [name for name in (spec.world, spec.auth, spec.db) if name in named]
    if not up:
        logger.info("stop_staged(): no project name, and nothing running under our names")
        return
    raise DockerCommandError(
        f"cannot tell which containers belong to the install in {server_dir}: its compose files "
        "are unreadable and no COMPOSE_PROJECT_NAME is pinned, while "
        f"{', '.join(up)} are running. Nothing was stopped."
    )


def _stranger_message(
    strangers: tuple[tuple[str, str | None], ...], project: str, server_dir: Path
) -> str:
    """Explain a name/label mismatch, and name the remedy that actually works.

    This used to end with "re-attach this install", which is worse than useless:
    attaching no longer pins at all, and the version that did would have written
    the *current* basename — the very value that produces this mismatch — after
    which the `.env` outranks the directory forever and the recoverable state
    becomes permanent. The two remedies that do work are naming the containers'
    own project in `.env`, or putting the folder back under the name compose
    created them with (review, 2026-08-22).
    """
    owners = sorted({owner for _name, owner in strangers if owner})
    unlabelled = [name for name, owner in strangers if not owner]
    names = ", ".join(name for name, _owner in strangers)
    lines = [
        f"{names} are running, but they do not belong to the install in {server_dir}, which "
        f"is compose project '{project}'. Nothing was stopped: this could equally be another "
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
            f"They belong to compose project {which}. If that is really this install, make the "
            f"two agree: set {PROJECT_NAME_VAR}={owners[0]} in {server_dir / '.env'}, or move "
            f"the folder back to a directory named {owners[0]}. If it is not this install, stop "
            "that server from its own folder."
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
    if not before.ours:
        logger.info("stop_staged(): nothing of this install is running")
        return False

    proc = runner.run(["docker", "compose", "stop"], cwd=server_dir)
    if proc.returncode != 0:
        logger.warning(f"compose stop failed ({proc.stderr.strip()}); stopping containers by name")

    lingering = _running(spec, project).ours
    if lingering:
        # Either `compose stop` failed, or it succeeded having matched nothing —
        # the moved-folder case, where compose names the project after the
        # directory. Finish the job by name rather than believing the exit code.
        logger.warning(f"compose stop left {list(lingering)} running; stopping by name")
        for name in lingering:
            _run_docker_stop(name)
        lingering = _running(spec, project).ours

    if lingering:
        raise DockerCommandError(f"still running after stop: {', '.join(lingering)}")
    stopped_something = True
    logger.info("stop_staged(): stopped; containers kept for a fast restart")
    return stopped_something


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
    """Like `status()`, but returns `None` instead of raising on failure.

    Used by polling loops (`wait_ready()`) where a single transient `docker ps`
    failure (daemon restart, brief overload, etc.) must be treated as "not
    ready this iteration, try again," not as a reason to abort the whole wait.
    """
    try:
        return status()
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
    logger.debug(f"health() called: container={container}")
    proc = runner.run(["docker", "inspect", container, "--format", "{{.State.Health.Status}}"])
    if proc.returncode != 0 or not proc.stdout.strip():
        return "unknown"
    return proc.stdout.strip()


def started_at(container: str) -> str:
    """When the container's CURRENT run began, or `""` if it cannot be read."""
    proc = runner.run(["docker", "inspect", container, "--format", "{{.State.StartedAt}}"])
    return proc.stdout.strip() if proc.returncode == 0 else ""


def _logs(container: str, *, this_run_only: bool = False) -> str:
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
    argv = ["docker", "logs"]
    if this_run_only:
        since = started_at(container)
        if since:
            argv += ["--since", since]
    proc = runner.run([*argv, container])
    return proc.stdout if proc.returncode == 0 else ""


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
    while time.monotonic() < deadline:
        if health(db_container) == "healthy":
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
    while time.monotonic() < deadline:
        running = _status_safe()
        if running is not None and auth_container in running and world_container in running:
            if target in _logs(auth_container, this_run_only=True) and "ready..." in _logs(
                world_container, this_run_only=True
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
    """
    logger.debug(f"follow_logs() called: container={container} tail={tail}")
    yield from runner.stream(["docker", "logs", "-f", "--tail", str(tail), container])
