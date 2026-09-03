"""Docker lifecycle for the WotLK server.

This is the per-game surface only — the shared behavior (`start`/`stop`/
`status`/`health`/polling/`port_conflicts`) lives in `yulon.docker` (DRY,
style-guide §4). What belongs *here* is exclusively the WotLK-specific
`ContainerSpec`: the three AzerothCore container names (mirroring
`dml-start.sh` constants) and the published ports shared by all v1 WoW servers.
"""

from __future__ import annotations

from typing import Final

from yulon import docker
from yulon.catalog.catalog import load_catalog

# WotLK AzerothCore containers (mirrors dml-start.sh constants).
_NATIVE = load_catalog().get("wow-wotlk").install.native
if _NATIVE is None:  # pragma: no cover - a catalog this broken fails everywhere
    raise RuntimeError("wow-wotlk has no install.native block, so nothing can install or manage it")

DB_CLIENT: Final[str] = _NATIVE.db.client
"""`mysql`, taken from `install.native.db.client` rather than written here.

This package spells its container names as literals (see the TBC module's
docstring, which contrasts the two) and that stays as it is -- this is the one
fact that had to come from data. `maintenance.DockerMysql` needs it for the
case where the container cannot be asked what client it has: the unbound
fallback is `mysql`, which is RIGHT for AzerothCore and wrong for all three
CMaNGOS games, and a value that is only ever correct by luck is the kind that
survives being copied to a game where it is wrong.
"""

SPEC = docker.ContainerSpec(
    db="ac-database",
    auth="ac-authserver",
    world="ac-worldserver",
    ports=(3724, 8085),
    # The one-shot that populates the three schemas. It is named here so
    # `repair_import()` can select it deliberately; every other path in this
    # package exists to make sure nothing selects it by accident.
    import_service="ac-db-import",
)

# Re-export the shared operations so callers import from here, not from
# yulon.docker directly — this package stays the single entry point for WotLK.
start = docker.start
start_staged = docker.start_staged
# Not `stop`. Sitting next to `stop_staged` that name read as its peer --
# two ways to stop -- when one keeps the containers and the other deletes
# them. Checklist 6.5 asks for exactly this rename (2026-08-23).
remove = docker.remove_staged
# The repair for an install interrupted before its import finished. Not a peer
# of `start`/`start_staged` either: it is the only export here that may run
# `SPEC.import_service`, and it refuses far more often than it acts.
repair_import = docker.repair_import
stop_staged = docker.stop_staged
status = docker.status
health = docker.health
wait_db_healthy = docker.wait_db_healthy
wait_ready = docker.wait_ready
port_conflicts = docker.port_conflicts


def wait_db_healthy_ready(*, wsl_distro: str | None = None, **kwargs: float) -> bool:
    """`wait_db_healthy()` pre-bound to `SPEC.db`. `kwargs` forward timeout/interval."""
    return docker.wait_db_healthy_for(SPEC, wsl_distro=wsl_distro, **kwargs)


def wait_server_ready(
    realm_host: str, realm_port: int, *, wsl_distro: str | None = None, **kwargs: float
) -> bool:
    """`wait_ready()` pre-bound to `SPEC`'s auth/world containers."""
    ready = docker.azerothcore_ready(realm_host, realm_port, **kwargs)
    return docker.wait_ready_for(SPEC, ready, wsl_distro=wsl_distro)


def port_conflicts_here() -> list[str]:
    """`port_conflicts()` pre-bound to `SPEC.ports`."""
    return docker.port_conflicts_for(SPEC)
