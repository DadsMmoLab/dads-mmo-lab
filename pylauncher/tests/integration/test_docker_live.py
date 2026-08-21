"""Live-Docker exercise of `yulon.docker` and the base `Controller` (roadmap 1.5).

Runs the real `docker compose` / `docker ps` / `docker inspect` / `docker logs`
paths against the throwaway busybox project from `conftest.py`. Skipped when
no daemon is reachable. One test carries the whole lifecycle on purpose: the
steps depend on each other (you cannot check "stopped" before "started"), and
bringing the project up/down once per assertion would be slow for no gain.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integration.conftest import (
    THROWAWAY_PORTS,
    THROWAWAY_REALM_HOST,
    THROWAWAY_REALM_PORT,
    THROWAWAY_SPEC,
    import_runs,
)
from yulon import docker
from yulon.controller import Controller, PortConflictError

pytestmark = pytest.mark.integration

# Generous for a cold `busybox` pull; the happy path finishes in a few seconds.
_DB_TIMEOUT = 60.0
_READY_TIMEOUT = 60.0
_INTERVAL = 0.5


def test_docker_lifecycle_end_to_end(throwaway_project: Path) -> None:
    """start → healthy → ready → status → conflict guard → stop, for real."""
    ctl = Controller(THROWAWAY_SPEC, throwaway_project)

    # Nothing of ours is up yet, and nothing else holds our ports.
    assert ctl.status().any_running is False
    assert ctl.port_conflicts() == []

    ctl.start()
    try:
        assert ctl.wait_db_healthy(timeout=_DB_TIMEOUT, interval=_INTERVAL) is True
        assert docker.health(THROWAWAY_SPEC.db) == "healthy"
        assert (
            ctl.wait_ready(
                THROWAWAY_REALM_HOST,
                THROWAWAY_REALM_PORT,
                timeout=_READY_TIMEOUT,
                interval=_INTERVAL,
            )
            is True
        )
        assert ctl.status().all_running is True

        # The global scan sees our own published ports ...
        assert set(docker.port_conflicts(THROWAWAY_PORTS)) >= {
            THROWAWAY_SPEC.db,
            THROWAWAY_SPEC.auth,
        }
        # ... but the controller filters its own containers out, so a restart
        # of *this* install is not a conflict ...
        assert ctl.port_conflicts() == []
        ctl.start()  # idempotent `compose up -d`; must not raise

        # ... while a *different* install wanting the same ports is refused
        # before `docker compose up` ever runs (README §12).
        stranger = docker.ContainerSpec(
            db="yulon-it-other-db",
            auth="yulon-it-other-auth",
            world="yulon-it-other-world",
            ports=THROWAWAY_PORTS,
        )
        with pytest.raises(PortConflictError) as excinfo:
            Controller(stranger, throwaway_project / "nonexistent").start()
        assert set(excinfo.value.containers) >= {THROWAWAY_SPEC.db, THROWAWAY_SPEC.auth}
    finally:
        ctl.stop()

    assert ctl.status().any_running is False
    assert ctl.port_conflicts() == []
    # Stopped, but still *there*: `stop()` keeps the containers so the next
    # start can go through `docker start`. A stopped container reports the
    # health of its last run rather than `unknown` (which means "no such
    # container"), so the check here is existence, not the health string.
    assert docker.container_exists(THROWAWAY_SPEC.db) is True


def test_wait_db_healthy_times_out_on_missing_container(require_docker: None) -> None:
    """A container that never appears makes the poll return False, not hang or raise."""
    assert docker.wait_db_healthy("yulon-it-does-not-exist", timeout=1.5, interval=0.5) is False


def test_compose_up_reruns_the_one_shot_import(staged_project: Path) -> None:
    """Why `start_staged()` exists: a bare `compose up -d` runs the import again.

    This is the failure `dml-start.sh` documents in as many words. Asserting it
    against a real daemon keeps the reason for the staged path from decaying
    into a comment nobody can check.
    """
    docker.start(staged_project)
    assert import_runs(staged_project) == 1

    docker.start(staged_project)
    assert import_runs(staged_project) == 2, "expected the documented re-run"


def test_launcher_restart_never_reruns_the_one_shot_import(staged_project: Path) -> None:
    """The launcher's own stop → start cycle must not re-run the import.

    `start_staged()` alone is not enough: it takes the staged path only while
    the containers still exist, so a `stop()` that *removes* them puts the next
    start straight back on `compose up -d`. Driving `Controller` here rather
    than `yulon.docker` is the point — this is the pair of buttons a user
    presses, and the pair has to hold the invariant together.
    """
    ctl = Controller(THROWAWAY_SPEC, staged_project)

    ctl.start()
    assert ctl.wait_db_healthy(timeout=_DB_TIMEOUT, interval=_INTERVAL) is True
    assert import_runs(staged_project) == 1

    ctl.stop()
    assert docker.container_exists(THROWAWAY_SPEC.world) is True, "stop() removed the containers"

    ctl.start()
    assert ctl.wait_db_healthy(timeout=_DB_TIMEOUT, interval=_INTERVAL) is True
    assert import_runs(staged_project) == 1, "the restart re-ran the one-shot import"


def test_changed_compose_file_is_applied_on_the_next_start(staged_project: Path) -> None:
    """A changed compose file must reach the containers, not be skipped.

    `docker start` replays a container exactly as created, so the staged path
    has to stand aside when the user has edited the compose file — otherwise a
    changed port or setting silently does nothing.
    """
    compose = staged_project / "docker-compose.yml"
    docker.start(staged_project)
    assert import_runs(staged_project) == 1

    compose.write_text(
        compose.read_text(encoding="utf-8").replace(
            "World initialized, ready...", "World initialized (edited), ready..."
        ),
        encoding="utf-8",
    )
    staged = docker.start_staged(
        THROWAWAY_SPEC,
        staged_project,
        wait_healthy=lambda name: docker.wait_db_healthy(
            name, timeout=_DB_TIMEOUT, interval=_INTERVAL
        ),
    )
    assert staged is False, "an edited compose file must fall back to `compose up -d`"
    assert import_runs(staged_project) == 2
