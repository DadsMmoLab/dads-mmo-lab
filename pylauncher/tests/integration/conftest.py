"""Shared gates and fixtures for the live-Docker integration suite.

Two layers of opt-in, both deliberate:

- **Docker reachable** — every test here is skipped (not failed) when
  `docker info` cannot reach a daemon or the `docker` binary is missing. This
  is what keeps the default `pytest` run green in CI without Docker
  (roadmap 1.5 definition of done).
- **A throwaway compose project** (`throwaway_project`) — three tiny `busybox`
  containers shaped like an install (db with a HEALTHCHECK, auth + world that
  print the same ready markers `yulon.docker.wait_ready()` looks for, all
  publishing ports) so the *real* CLI paths in `yulon.docker` are exercised
  end-to-end without needing the multi-GB AzerothCore fixture. The AzerothCore
  fixture itself (`tests/fixture.md`) is exercised by `test_wotlk_live.py`,
  which is additionally gated on `YULON_WOTLK_SERVER_DIR`.
"""

from __future__ import annotations

import os
import socket
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import docker, runner

THROWAWAY_REALM_HOST = "127.0.0.1"

# One tag per pytest process, mixed into every name this suite creates.
#
# Fixed names meant the suite could not be run twice at once on one machine: a
# second run's `compose up` died with `Conflict. The container name
# "/yulon-it-db" is already in use`, taking 8 of its 10 tests with it (measured
# 2026-08-23, two concurrent runs on this box — the same failure a shared dev
# box or two parallel CI jobs on one runner produce). A pid is unique among
# live processes, which is exactly the set of runs that can overlap, and it
# covers `pytest-xdist` workers for free since each is its own process.
_RUN_TAG = f"{os.getpid():x}"


def _free_ports(count: int) -> tuple[int, ...]:
    """`count` TCP ports nothing holds, asked of the OS rather than picked.

    Every socket is kept open until all of them are bound, so the same port is
    never handed out twice. The window between closing them and compose binding
    them is a race in principle; in practice the OS hands out ephemeral ports
    (32768+ on Linux, 49152+ on Windows) round-robin, which is also why these
    can never collide with the real install's 3724/8085 the way a hand-picked
    number could.
    """
    held = [socket.socket(socket.AF_INET, socket.SOCK_STREAM) for _ in range(count)]
    try:
        for sock in held:
            sock.bind((THROWAWAY_REALM_HOST, 0))
        return tuple(sock.getsockname()[1] for sock in held)
    finally:
        for sock in held:
            sock.close()


# Published, so `port_conflicts()` has something to find. Per-run rather than
# fixed for the same reason the names are: two runs publishing one port is the
# other half of the collision, and `Controller.port_conflicts() == []` is only
# true of a run whose ports nobody else has.
THROWAWAY_PORTS: tuple[int, ...] = _free_ports(2)
# The compose services here are `db`/`auth`/`world` while the containers are
# `yulon-it-*`, so this fixture also exercises the case `services` exists for:
# a project whose service names differ from its container names. AzerothCore's
# happen to match, which is exactly why it is worth testing the other way.
THROWAWAY_SPEC = docker.ContainerSpec(
    db=f"yulon-it-{_RUN_TAG}-db",
    auth=f"yulon-it-{_RUN_TAG}-auth",
    world=f"yulon-it-{_RUN_TAG}-world",
    ports=THROWAWAY_PORTS,
    services=("db", "auth", "world"),
)
THROWAWAY_REALM_PORT = THROWAWAY_PORTS[1]

_COMPOSE_YML = f"""\
services:
  db:
    image: busybox:1.36
    container_name: {THROWAWAY_SPEC.db}
    command: ["sh", "-c", "trap 'exit 0' TERM; sleep 2 && touch /tmp/ready; sleep 600 & wait"]
    healthcheck:
      test: ["CMD", "test", "-f", "/tmp/ready"]
      interval: 1s
      timeout: 1s
      retries: 3
    ports:
      - "{THROWAWAY_REALM_HOST}:{THROWAWAY_PORTS[0]}:{THROWAWAY_PORTS[0]}"
  auth:
    image: busybox:1.36
    container_name: {THROWAWAY_SPEC.auth}
    depends_on:
      db:
        condition: service_healthy
    command:
      - sh
      - -c
      - >-
        trap 'exit 0' TERM;
        echo 'Added realm "Yulon" at {THROWAWAY_REALM_HOST}:{THROWAWAY_REALM_PORT}';
        sleep 600 & wait
    ports:
      - "{THROWAWAY_REALM_HOST}:{THROWAWAY_REALM_PORT}:{THROWAWAY_REALM_PORT}"
  world:
    image: busybox:1.36
    container_name: {THROWAWAY_SPEC.world}
    depends_on:
      db:
        condition: service_healthy
    command:
      - sh
      - -c
      - trap 'exit 0' TERM; echo 'World initialized, ready...'; sleep 600 & wait
"""
# Every stand-in traps SIGTERM and waits on a BACKGROUNDED sleep. Both halves are
# required, and without them the suite pays five minutes twice.
#
# `sleep` as PID 1 does not die on SIGTERM: the kernel gives PID 1 no default
# action for it, so `docker compose stop -t 300` waits out the whole grace period
# (`docker.STOP_GRACE_SECONDS`, correct at 300 for a real worldserver draining its
# save queue) and then SIGKILLs. Measured on yulon-fedora 2026-08-28:
# `time docker stop -t 300` on `busybox sh -c "sleep 600"` took 5m0.748s, and two
# tests do it, which is most of a 32-42 minute local run.
#
# The trap alone is not enough. A foreground `sleep` blocks the shell from
# running the handler until it returns, so the sleep is backgrounded and `wait`
# holds the shell in a signal-interruptible state.

# The `depends_on: condition: service_healthy` edges above are not decoration.
# `start_staged()` deleted its Python health-wait on the grounds that compose
# owns the dependency graph and fails closed when the database never becomes
# healthy — and nothing in this suite exercised either claim, because the
# fixture declared a healthcheck and no depends_on at all (review, 2026-08-22).
# `--no-deps` keeps these edges precisely because `db` is one of the named
# services; it prunes only edges pointing outside the selected set, which is
# what keeps the one-shot import out.

# The same project with a database that can never report healthy: the
# fail-closed case. A worldserver started against a dead database is the
# outcome the health gate exists to prevent.
_NEVER_HEALTHY_YML = _COMPOSE_YML.replace(
    'command: ["sh", "-c", "sleep 2 && touch /tmp/ready && sleep 600"]',
    'command: ["sh", "-c", "sleep 600"]',  # /tmp/ready is never created
)


def docker_available() -> bool:
    """True if a Docker daemon answers `docker info`; False if missing/unreachable."""
    try:
        return runner.run(["docker", "info"]).returncode == 0
    except OSError:
        return False


def _compose_down(project_dir: Path) -> None:
    """Best-effort teardown; never raises so a failed test still cleans up."""
    subprocess.run(
        ["docker", "compose", "down", "-v", "--remove-orphans", "--timeout", "2"],
        cwd=str(project_dir),
        capture_output=True,
        check=False,
    )


def _project_dir(tmp_path: Path, compose_yml: str) -> Path:
    """A compose project directory whose BASENAME belongs to this run alone.

    Compose names a project after its directory unless told otherwise, and
    pytest names `tmp_path` after the test — so two concurrent runs of the same
    test get two different paths with the *same* basename, and therefore one
    project name. `compose down` selects by that name, so each run's teardown
    would reach into the other's containers. The tag is what stops it, and it
    is why the teardown here can stay a plain `compose down`: the project it
    names is this run's own.
    """
    project = tmp_path / f"yulon-it-{_RUN_TAG}"
    project.mkdir()
    (project / "docker-compose.yml").write_text(compose_yml, encoding="utf-8")
    return project


@pytest.fixture(scope="session")
def require_docker() -> None:
    """Skip the requesting test when no Docker daemon is reachable."""
    if not docker_available():
        pytest.skip("no Docker daemon reachable (docker info failed)")


@pytest.fixture
def throwaway_project(tmp_path: Path, require_docker: None) -> Iterator[Path]:
    """A fresh throwaway compose project dir, torn down after the test.

    Also runs `compose down` *before* yielding, because every test in one run
    shares that run's tag and so its project name: containers an earlier test
    in this process failed to tear down would otherwise poison this one.
    Leftovers from a run that was *killed* can no longer poison anything — they
    carry that run's tag and hold no port this one wants — but they are also no
    longer cleaned up by the next run, so a machine that has had runs killed
    accumulates them (`docker rm -f $(docker ps -aq --filter name=yulon-it-)`).
    """
    project = _project_dir(tmp_path, _COMPOSE_YML)
    _compose_down(project)
    try:
        yield project
    finally:
        _compose_down(project)


# AzerothCore's install runs one-shot containers (`ac-db-import`,
# `ac-client-data-init`) that exit as soon as they succeed. `docker compose up`
# starts every service without a *running* container, so it runs them again on
# every restart — which `dml-start.sh` warns "was killing the database". This
# service is that shape in miniature: it appends one line per run to a
# bind-mounted file, so a test can simply count how many times it ran.
IMPORT_CONTAINER = f"yulon-it-{_RUN_TAG}-import"
IMPORT_MARKER_DIR = "marker"
IMPORT_MARKER_FILE = "import.log"

_ONE_SHOT_YML = f"""\
  import:
    image: busybox:1.36
    container_name: {IMPORT_CONTAINER}
    volumes:
      - ./{IMPORT_MARKER_DIR}:/marker
    command: ["sh", "-c", "echo ran >> /marker/{IMPORT_MARKER_FILE}"]
"""


def import_runs(project_dir: Path) -> int:
    """How many times the one-shot import container has run in this project."""
    marker = project_dir / IMPORT_MARKER_DIR / IMPORT_MARKER_FILE
    if not marker.is_file():
        return 0
    return len([line for line in marker.read_text(encoding="utf-8").splitlines() if line.strip()])


@pytest.fixture
def never_healthy_project(tmp_path: Path, require_docker: None) -> Iterator[Path]:
    """Like `throwaway_project`, but the database never reports healthy."""
    project = _project_dir(tmp_path, _NEVER_HEALTHY_YML)
    _compose_down(project)
    try:
        yield project
    finally:
        _compose_down(project)


@pytest.fixture
def staged_project(tmp_path: Path, require_docker: None) -> Iterator[Path]:
    """`throwaway_project` plus a one-shot import container, torn down after.

    Separate from `throwaway_project` so the lifecycle test keeps its exact
    three-container shape; the extra service exists only for the restart tests,
    which need something that must *not* run twice.
    """
    project = _project_dir(tmp_path, _COMPOSE_YML + _ONE_SHOT_YML)
    (project / IMPORT_MARKER_DIR).mkdir()
    _compose_down(project)
    try:
        yield project
    finally:
        _compose_down(project)
        subprocess.run(["docker", "rm", "-f", IMPORT_CONTAINER], capture_output=True, check=False)
