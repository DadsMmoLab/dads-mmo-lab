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
    """The container names and published ports for a single server install."""

    db: str
    auth: str
    world: str
    ports: tuple[int, ...]


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
    """Start an ALREADY-INSTALLED server without re-running its one-shot containers.

    `docker compose up -d` starts every service that has no running container —
    including AzerothCore's one-shot `ac-db-import` and `ac-client-data-init`,
    which have already exited successfully. Re-running the import on every
    restart is what `dml-start.sh` warns about in as many words:

        # Use docker start (not compose up) so we do NOT re-trigger ac-db-import
        # or ac-client-data-init on every restart — that was killing the database.

    So when this install's three containers already exist, start them by name
    and in order (database first, healthy, then auth and world), exactly as the
    shell script does. When any of them is missing the install has not been
    brought up yet, and `compose up -d` is both correct and necessary — that is
    the path that creates the containers and runs the import once.

    The database must be healthy before auth and world start, or they race it
    and die; `wait_healthy` is that wait, injectable so tests do not sit through
    a real timeout.

    Returns:
        True if the staged path was used, False if it fell back to `compose up`.
    """
    logger.debug(f"start_staged() called: server_dir={server_dir}")
    existing = {
        line.strip() for line in _run(["ps", "-a", "--format", "{{.Names}}"]).stdout.splitlines()
    }
    wanted = (spec.db, spec.auth, spec.world)
    if not all(name in existing for name in wanted):
        missing = [name for name in wanted if name not in existing]
        logger.info(f"start_staged(): {missing} do not exist yet — first `compose up -d`")
        start(server_dir)
        return False
    if compose_config_changed(server_dir):
        logger.info("start_staged(): compose config differs from the containers — `compose up -d`")
        start(server_dir)
        return False
    logger.info("start_staged(): containers exist — `docker start` (never re-runs the DB import)")
    _run_docker_start(spec.db)
    wait = wait_healthy if wait_healthy is not None else wait_db_healthy
    if not wait(spec.db):
        logger.warning(f"{spec.db} did not become healthy; starting auth/world anyway")
    _run_docker_start(spec.auth)
    _run_docker_start(spec.world)
    return True


_SERVICE_LABEL = "com.docker.compose.service"
_CONFIG_HASH_LABEL = "com.docker.compose.config-hash"


def _parse_pairs(text: str) -> dict[str, str]:
    """`"name value"` lines into a dict, skipping anything that is not a pair."""
    pairs = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            pairs[parts[0]] = parts[1]
    return pairs


def _configured_service_hashes(server_dir: Path) -> dict[str, str]:
    """Service to config hash for the compose files *as they are on disk now*."""
    proc = runner.run(["docker", "compose", "config", "--hash=*"], cwd=server_dir)
    if proc.returncode != 0:
        logger.debug(f"compose config --hash failed: {proc.stderr.strip()}")
        return {}
    return _parse_pairs(proc.stdout)


def _deployed_service_hashes(server_dir: Path) -> dict[str, str]:
    """Service to config hash for the containers compose actually created."""
    listed = runner.run(["docker", "compose", "ps", "-a", "-q"], cwd=server_dir)
    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if listed.returncode != 0 or not ids:
        return {}
    fmt = f'{{{{index .Config.Labels "{_SERVICE_LABEL}"}}}} '
    fmt += f'{{{{index .Config.Labels "{_CONFIG_HASH_LABEL}"}}}}'
    proc = runner.run(["docker", "inspect", "--format", fmt, *ids])
    if proc.returncode != 0:
        logger.debug(f"docker inspect for compose labels failed: {proc.stderr.strip()}")
        return {}
    return _parse_pairs(proc.stdout)


def compose_config_changed(server_dir: Path) -> bool:
    """True if the compose files now describe something other than what is deployed.

    `docker start` replays a container exactly as it was created, so an edited
    port mapping or `AC_*` value would be silently ignored — the setting appears
    to do nothing. Compose already answers this question precisely: it stamps
    every container with a `com.docker.compose.config-hash` label and recreates
    a service when the hash of the current config differs. Comparing those
    hashes is therefore exactly the condition under which `docker start` would
    lie.

    This replaces a version that compared compose-file mtimes against the
    container's creation time, which **latched**: `compose up -d` recreates only
    the services whose own config changed, so a database-only edit left the
    world container's creation time frozen behind the file mtime *forever*, and
    every later start took the `compose up -d` path — re-running the one-shot
    import that the staged start exists to avoid. A hash cannot latch, because
    compose updates the label of exactly the containers it recreates. It also
    drops a dependence on two clocks agreeing, which on Docker Desktop they do
    not: the mtime comes from the host and the creation time from the WSL2 VM.

    A service with no container at all is *not* counted as changed. Whether the
    three containers this install starts by name exist is `start_staged()`'s own
    check; a one-shot import container that someone pruned after it succeeded
    must not drag the whole project through a recreate.

    Unknown answers are "no": if either side cannot be read, the staged path
    stays, because a needless recreate re-runs the database import.
    """
    configured = _configured_service_hashes(server_dir)
    if not configured:
        return False
    deployed = _deployed_service_hashes(server_dir)
    if not deployed:
        return False
    for service, digest in configured.items():
        current = deployed.get(service)
        if current is not None and current != digest:
            logger.debug(f"compose_config_changed(): {service} {current} -> {digest}")
            return True
    return False


def _run_docker_start(container: str) -> None:
    """`docker start <container>` (a no-op on one that is already running)."""
    proc = runner.run(["docker", "start", container])
    if proc.returncode != 0:
        raise DockerCommandError(f"docker start {container} failed: {proc.stderr.strip()}")


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


def stop_staged(spec: ContainerSpec, server_dir: Path) -> bool:
    """Stop an ALREADY-INSTALLED server without destroying its containers.

    The counterpart to `start_staged()`, and required for it to ever do
    anything: `docker compose down` *removes* the containers, so a stop/start
    cycle through `stop()` leaves the next `start_staged()` with nothing to
    start by name — it falls back to `compose up -d`, which re-runs the one-shot
    `ac-db-import`. The two halves only hold the invariant together, which is
    why `dml-start.sh` pairs `docker stop` with `docker start` and never uses
    `compose down` on a restart.

    `docker compose stop` is the primary path: it keeps every container, and it
    walks the project's own `depends_on` graph, so the world and auth servers
    close their connections before the database goes away. That graph is
    upstream AzerothCore's, not ours to restate — both servers declare
    `ac-database: service_healthy` — and honouring it matters most exactly when
    it is slowest, with a full playerbot population still writing saves.

    The fallback stops this install's three containers by name, one call at a
    time and in reverse order, for a project whose compose files cannot be read
    (deleted, or a directory that is no longer the project root). Losing the
    ability to stop a running server because a file is missing would be a worse
    failure than losing the ordering guarantee.

    Returns:
        True if the containers were stopped and kept, False if there was
        nothing of this install to stop.
    """
    logger.debug(f"stop_staged() called: server_dir={server_dir}")
    proc = runner.run(["docker", "compose", "stop"], cwd=server_dir)
    if proc.returncode == 0:
        logger.info("stop_staged(): `compose stop` (containers are kept for a fast restart)")
        return True

    logger.warning(f"compose stop failed ({proc.stderr.strip()}); stopping containers by name")
    existing = {
        line.strip() for line in _run(["ps", "-a", "--format", "{{.Names}}"]).stdout.splitlines()
    }
    ordered = [name for name in (spec.world, spec.auth, spec.db) if name in existing]
    if not ordered:
        logger.info("stop_staged(): none of this install's containers exist — nothing to stop")
        return False
    for name in ordered:
        _run_docker_stop(name)
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


def _logs(container: str) -> str:
    """Return a container's combined logs, or `""` if they can't be read."""
    proc = runner.run(["docker", "logs", container])
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
            if target in _logs(auth_container) and "ready..." in _logs(world_container):
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
