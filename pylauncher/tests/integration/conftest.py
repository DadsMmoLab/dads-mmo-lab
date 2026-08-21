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

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import docker, runner

# Unusual, fixed ports so the throwaway project never collides with a real
# install (3724/8085) and so `port_conflicts()` has something to find.
THROWAWAY_PORTS: tuple[int, ...] = (47321, 47322)
THROWAWAY_SPEC = docker.ContainerSpec(
    db="yulon-it-db",
    auth="yulon-it-auth",
    world="yulon-it-world",
    ports=THROWAWAY_PORTS,
)
THROWAWAY_REALM_HOST = "127.0.0.1"
THROWAWAY_REALM_PORT = THROWAWAY_PORTS[1]

_COMPOSE_YML = f"""\
services:
  db:
    image: busybox:1.36
    container_name: {THROWAWAY_SPEC.db}
    command: ["sh", "-c", "sleep 2 && touch /tmp/ready && sleep 600"]
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
    command:
      - sh
      - -c
      - echo 'Added realm "Yulon" at {THROWAWAY_REALM_HOST}:{THROWAWAY_REALM_PORT}'; sleep 600
    ports:
      - "{THROWAWAY_REALM_HOST}:{THROWAWAY_REALM_PORT}:{THROWAWAY_REALM_PORT}"
  world:
    image: busybox:1.36
    container_name: {THROWAWAY_SPEC.world}
    command: ["sh", "-c", "echo 'World initialized, ready...'; sleep 600"]
"""


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


@pytest.fixture(scope="session")
def require_docker() -> None:
    """Skip the requesting test when no Docker daemon is reachable."""
    if not docker_available():
        pytest.skip("no Docker daemon reachable (docker info failed)")


@pytest.fixture
def throwaway_project(tmp_path: Path, require_docker: None) -> Iterator[Path]:
    """A fresh throwaway compose project dir, torn down after the test.

    Also runs `compose down` *before* yielding, so containers left behind by a
    previous crashed run (fixed `container_name`s) never poison this one.
    """
    (tmp_path / "docker-compose.yml").write_text(_COMPOSE_YML, encoding="utf-8")
    _compose_down(tmp_path)
    try:
        yield tmp_path
    finally:
        _compose_down(tmp_path)


# AzerothCore's install runs one-shot containers (`ac-db-import`,
# `ac-client-data-init`) that exit as soon as they succeed. `docker compose up`
# starts every service without a *running* container, so it runs them again on
# every restart — which `dml-start.sh` warns "was killing the database". This
# service is that shape in miniature: it appends one line per run to a
# bind-mounted file, so a test can simply count how many times it ran.
IMPORT_CONTAINER = "yulon-it-import"
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
def staged_project(tmp_path: Path, require_docker: None) -> Iterator[Path]:
    """`throwaway_project` plus a one-shot import container, torn down after.

    Separate from `throwaway_project` so the lifecycle test keeps its exact
    three-container shape; the extra service exists only for the restart tests,
    which need something that must *not* run twice.
    """
    (tmp_path / "docker-compose.yml").write_text(_COMPOSE_YML + _ONE_SHOT_YML, encoding="utf-8")
    (tmp_path / IMPORT_MARKER_DIR).mkdir()
    _compose_down(tmp_path)
    try:
        yield tmp_path
    finally:
        _compose_down(tmp_path)
        subprocess.run(["docker", "rm", "-f", IMPORT_CONTAINER], capture_output=True, check=False)
