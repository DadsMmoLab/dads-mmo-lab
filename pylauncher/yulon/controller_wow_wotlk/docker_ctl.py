"""Docker lifecycle for the WotLK server.

This is the per-game surface only — the shared behavior (`start`/`stop`/
`status`/`health`/polling/`port_conflicts`) lives in `yulon.docker` (DRY,
style-guide §4). What belongs *here* is exclusively the WotLK-specific
`ContainerSpec`: the three AzerothCore container names (mirroring
`dml-start.sh` constants) and the published ports shared by all v1 WoW servers.
"""

from __future__ import annotations

from yulon import docker

# WotLK AzerothCore containers (mirrors dml-start.sh constants).
SPEC = docker.ContainerSpec(
    db="ac-database",
    auth="ac-authserver",
    world="ac-worldserver",
    ports=(3724, 8085),
)

# Re-export the shared operations so callers import from here, not from
# yulon.docker directly — this package stays the single entry point for WotLK.
start = docker.start
start_staged = docker.start_staged
stop = docker.stop
stop_staged = docker.stop_staged
status = docker.status
health = docker.health
wait_db_healthy = docker.wait_db_healthy
wait_ready = docker.wait_ready
port_conflicts = docker.port_conflicts


def wait_db_healthy_ready(**kwargs: float) -> bool:
    """`wait_db_healthy()` pre-bound to `SPEC.db`. `kwargs` forward timeout/interval."""
    return docker.wait_db_healthy_for(SPEC, **kwargs)


def wait_server_ready(realm_host: str, realm_port: int, **kwargs: float) -> bool:
    """`wait_ready()` pre-bound to `SPEC`'s auth/world containers."""
    return docker.wait_ready_for(SPEC, realm_host, realm_port, **kwargs)


def port_conflicts_here() -> list[str]:
    """`port_conflicts()` pre-bound to `SPEC.ports`."""
    return docker.port_conflicts_for(SPEC)
