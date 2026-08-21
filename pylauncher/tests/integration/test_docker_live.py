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
    assert docker.health(THROWAWAY_SPEC.db) == "unknown"


def test_wait_db_healthy_times_out_on_missing_container(require_docker: None) -> None:
    """A container that never appears makes the poll return False, not hang or raise."""
    assert docker.wait_db_healthy("yulon-it-does-not-exist", timeout=1.5, interval=0.5) is False
