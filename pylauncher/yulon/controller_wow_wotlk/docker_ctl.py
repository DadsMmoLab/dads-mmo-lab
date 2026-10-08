"""Docker lifecycle for the WotLK server.

This is the per-game surface only — the shared behavior (`start`/`stop`/
`status`/`health`/polling/`port_conflicts`) lives in `yulon.docker` (DRY,
style-guide §4). What belongs *here* is exclusively the WotLK-specific
`ContainerSpec`, read from the `wow-wotlk` catalog entry (T552).
"""

from __future__ import annotations

from typing import Final

from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog

_ENTRY = load_catalog().get("wow-wotlk")
_NATIVE = _ENTRY.install.native
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

SPEC = _ENTRY.container_spec()
"""WotLK's containers and ports, read from its catalog entry (T552) as Vanilla's are.

Until T552 a literal `ac-database`/`ac-authserver`/`ac-worldserver`, `(3724, 8085)`
and `import_service="ac-db-import"` -- the same values, spelled a second time.
`SPEC` is WotLK's and no other server's: every seam the AzerothCore tab builds
is handed `entry.container_spec()` instead (`ui.controller_view._for_wotlk`), and
`tests/test_second_azerothcore.py` refuses a WotLK-bound default left to it.
`import_service` is still the one-shot only `repair_import()` may select.
"""

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
# NOT `wait_ready = docker.wait_ready`. That alias stood here until 2026-09-05,
# in all three CMaNGOS/AzerothCore packages, publishing the SINGLE-SHOT ready
# primitive under this package's public name while `wait_server_ready()` below
# spends the same catalogue number as a quiet budget. Nothing imported it (a
# tree-wide grep, 2026-09-05, found no caller), and the audit that claims to
# enumerate every ready wait in the app could not see it either: it matched
# function DEFINITIONS, and a module-level binding is an `ast.Assign`. It now
# reads bindings too, so re-adding this line is red rather than dormant.
port_conflicts = docker.port_conflicts


def wait_db_healthy_ready(*, wsl_distro: str | None = None, **kwargs: float) -> bool:
    """`wait_db_healthy()` pre-bound to `SPEC.db`. `kwargs` forward timeout/interval."""
    return docker.wait_db_healthy_for(SPEC, wsl_distro=wsl_distro, **kwargs)


def wait_server_ready(
    realm_host: str, realm_port: int, *, wsl_distro: str | None = None, **kwargs: float
) -> bool:
    """`wait_ready()` pre-bound to `SPEC`'s auth/world containers.

    `timeout` is a QUIET budget, as it is everywhere else in this app: how long
    the world server may print nothing new, restarted every time it prints,
    bounded by `native.management_ceiling()`. AzerothCore is the core the
    2026-09-04 incident did NOT happen to, and that is not a reason to leave
    this one reading the number differently — what was slow that day was the
    mount, not the core, and this game ships to the same Docker Desktop 9p
    share. It spent `timeout` as a fixed total until 2026-09-05, a day after the
    other five sites moved, because the test that claimed to cover "every ready
    wait in the app" named its sites in a parameter list.
    """
    ready = docker.azerothcore_ready(realm_host, realm_port, **kwargs)
    return native.wait_ready_quietly(SPEC, ready, wsl_distro=wsl_distro)


def port_conflicts_here() -> list[str]:
    """`port_conflicts()` pre-bound to `SPEC.ports`."""
    return docker.port_conflicts_for(SPEC)
