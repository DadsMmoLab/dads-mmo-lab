"""Docker lifecycle for the WotLK server.

Mirrors the polling logic in dml-start.sh (_wait_db_healthy, _wait_ready,
_pin_realm_local). Wraps `docker compose` / `docker inspect` via py.runner.
"""

from __future__ import annotations

# Default container names (mirrors dml-start.sh constants).
DB_CONTAINER = "ac-database"
AUTH_CONTAINER = "ac-authserver"
WORLD_CONTAINER = "ac-worldserver"


def start(server_dir: str) -> None:
    """Bring the compose project up."""
    raise NotImplementedError


def stop(server_dir: str) -> None:
    """Take the compose project down."""
    raise NotImplementedError


def status() -> None:
    """Report container status from `docker ps`."""
    raise NotImplementedError


def health(container: str) -> str:
    """Return a container's health status via `docker inspect`."""
    raise NotImplementedError