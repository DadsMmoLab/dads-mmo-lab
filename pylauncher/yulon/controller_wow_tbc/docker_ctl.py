"""Docker lifecycle for the WoW TBC server, and this package's one source of game facts.

The shared behavior (`start`/`stop`/`status`/`health`/polling/`port_conflicts`)
lives in `yulon.docker` and is re-exported below, exactly as
`controller_wow_wotlk/docker_ctl.py` re-exports it. What differs from that file
is where the per-game facts come from: WotLK's spells its three container names
as literals, and this one reads them — with the schema names, the database
client, the ready markers and the console prompt — out of the `wow-tbc` entry
in `catalog.json`.

That is not tidiness. `catalog.json` already carries every one of those values
because `composegen` writes the compose file from them, so a name retyped here
is a second source of truth that can drift from the file that actually creates
the container. The generated stack names its services after its containers
(`shared/cmangos/base.yml.tmpl`), so `ContainerSpec.services` stays empty and
`docker compose up -d --no-deps <db>` keeps working unchanged.

**No `repair_import` is exported, and that is the entry speaking.** AzerothCore
imports its databases from a one-shot compose service (`ac-db-import`) which
`docker.repair_import()` can re-run; the CMaNGOS stack has no such service —
its import is a stage of the installer, streaming SQL files into the database
client — so the entry's `containers` block names no `db_import` and
`ContainerSpec.import_service` is empty. `docker.repair_import()` refuses on
exactly that fact, before it touches anything, so exporting it here would only
put a name in front of a refusal.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Final

from yulon import docker
from yulon.catalog.catalog import (
    CatalogEntry,
    CmangosData,
    DbFacts,
    NativeInstall,
    ReadyMarkers,
    load_catalog,
)

GAME: Final = "wow-tbc"

ENTRY: Final[CatalogEntry] = load_catalog().get(GAME)
"""The `wow-tbc` catalog entry — every per-game fact in this package comes from here.

Read at import. The alternative (a lazy accessor) would buy nothing: a catalog
that will not validate is a broken bundle and every other path in the app fails
on it too, so failing loudly at import names the file to fix rather than
producing a controller that mostly works.
"""

_NATIVE = ENTRY.install.native
if _NATIVE is None:
    raise RuntimeError(f"{GAME} has no install.native block, so nothing can install or manage it")
NATIVE: Final[NativeInstall] = _NATIVE

_CMANGOS = NATIVE.cmangos
if _CMANGOS is None:
    raise RuntimeError(f"{GAME} declares family {NATIVE.family!r} but carries no cmangos block")
CMANGOS: Final[CmangosData] = _CMANGOS

SPEC: Final = ENTRY.container_spec()
"""tbc-db / tbc-realmd / tbc-mangosd, ports 3724 and 8085, and no import service."""

DB: Final[DbFacts] = NATIVE.db
DB_CLIENT: Final[str] = DB.client
"""`mariadb`, not `mysql`, and taken from data rather than assumed.

`mariadb:11` ships neither `mysql` nor `mysqldump` — MariaDB removed those
symlinks in 11 — so every statement addressed to a CMaNGOS database by the
AzerothCore spelling died before it reached a server (measured on a live TBC
server, 2026-08-26, and recorded on `apply.mysql_client()`). `apply.DockerSql`
and `maintenance.DockerMysql` resolve the name by asking the container what it
has; `repair.py`, which picks its own client, uses THIS value.
"""

READY: Final[ReadyMarkers] = NATIVE.ready
"""What this game's logs say when it is up: `world='Avg Diff:'`, no auth marker."""

SCHEMAS: Final[tuple[str, ...]] = (
    ENTRY.databases.auth,
    ENTRY.databases.characters,
    ENTRY.databases.world,
    *ENTRY.databases.extra,
)
"""Every schema this install owns, in the core's own spelling (`realmd`, ..., `logs`)."""

# Re-export the shared operations so callers import from here, not from
# yulon.docker directly — this package stays the single entry point for TBC.
start = docker.start
start_staged = docker.start_staged
remove = docker.remove_staged
stop_staged = docker.stop_staged
status = docker.status
health = docker.health
wait_db_healthy = docker.wait_db_healthy
wait_ready = docker.wait_ready
port_conflicts = docker.port_conflicts

_TOKEN = re.compile(r"\{\{[A-Z_]+\}\}")
"""The `{{TOKEN}}` grammar a `ready` marker may use. See `_pattern()`."""


def _pattern(text: str, *, regex: bool) -> str:
    """One ready marker as a regular expression `docker.wait_ready()` can search with.

    `wait_ready()` matches with `re.search`, so a LITERAL marker (`regex:
    false`, which is what this entry declares) has to be escaped first —
    unescaped, the `.` in a marker is a wildcard, which is the bug
    `docker.azerothcore_ready()` escapes `ready...` to avoid.

    A marker carrying a `{{TOKEN}}` is refused rather than escaped. The install
    spine fills those from its own `REALM_HOST`/`WORLD_PORT` before it builds a
    `ReadySpec` (`native.NativeInstaller._ready_spec`); a controller holds
    neither, and escaping the braces instead would produce a pattern that
    silently matches nothing — a server that is up, reported as never ready.
    This entry's markers carry no token today, so this guard is about the next
    edit to `catalog.json`, not about the current one.

    Raises:
        ValueError: the marker needs filling, or `regex: true` and it will not
            compile. Compiled here, where `catalog.json` can still be named as
            the thing to fix, rather than inside a poll loop.
    """
    found = _TOKEN.search(text)
    if found is not None:
        raise ValueError(
            f"{GAME}'s ready marker {text!r} carries {found.group()}, which only the install "
            "engine can fill; a controller has no realm host or world port to put there"
        )
    pattern = text if regex else re.escape(text)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(
            f"{GAME}'s ready marker {text!r} is not a usable pattern ({exc}); "
            "fix `install.native.ready` in catalog.json"
        ) from exc
    return pattern


def ready_spec(*, timeout: float | None = None, interval: float | None = None) -> docker.ReadySpec:
    """This game's `ReadySpec`, built from `install.native.ready`.

    The CMaNGOS counterpart of `docker.azerothcore_ready()`, which cannot be
    reused for either of its two halves: its world marker is AzerothCore's
    `ready...` (mangosd never prints it — this entry waits for `Avg Diff:`,
    the world's per-tick diff line), and its auth marker is the `host:port` the
    authserver announces, where this entry's `ready.auth` is null and nothing is
    waited for on the realmd log at all.

    `timeout` and `interval` override the data for a caller that wants a shorter
    wait; left alone, the entry's `timeout_s` wins. That number is 1800 and it
    is a measurement, not a margin: on m910q on 2026-09-02 a first TBC boot on 4
    cores took 793s from container start to its first `Avg Diff:`, against the
    600 the entry then carried (see `catalog.ReadyMarkers.timeout_s`).
    """
    spec = docker.ReadySpec(
        world=_pattern(READY.world, regex=READY.regex),
        auth=None if READY.auth is None else _pattern(READY.auth, regex=READY.regex),
        fatal=None if READY.fatal is None else _pattern(READY.fatal, regex=READY.regex),
        timeout=float(READY.timeout_s) if timeout is None else timeout,
        restart_loop=READY.restart_loop,
    )
    # `replace()` rather than an `interval=` argument above, so the poll interval
    # keeps `ReadySpec`'s own default instead of this module naming a second
    # number for it — there is no interval in `catalog.json` to take it from.
    return spec if interval is None else replace(spec, interval=interval)


def wait_db_healthy_ready(*, wsl_distro: str | None = None, **kwargs: float) -> bool:
    """`wait_db_healthy()` pre-bound to `SPEC.db`. `kwargs` forward timeout/interval."""
    return docker.wait_db_healthy_for(SPEC, wsl_distro=wsl_distro, **kwargs)


def wait_server_ready(*, wsl_distro: str | None = None, **kwargs: float) -> bool:
    """Poll until mangosd has printed its ready line. `kwargs` forward timeout/interval.

    No `realm_host`/`realm_port`, which is the whole difference from
    `controller_wow_wotlk.docker_ctl.wait_server_ready()`: those two arguments
    exist there to spell AzerothCore's auth marker `<host>:<port>`, and this
    entry has no auth marker to spell. Taking them anyway would let a caller
    believe the realmd log was being watched.
    """
    unknown = set(kwargs) - {"timeout", "interval"}
    if unknown:
        raise TypeError(f"wait_server_ready() accepts timeout/interval only, not {sorted(unknown)}")
    ready = ready_spec(timeout=kwargs.get("timeout"), interval=kwargs.get("interval"))
    return docker.wait_ready_for(SPEC, ready, wsl_distro=wsl_distro)


def port_conflicts_here() -> list[str]:
    """`port_conflicts()` pre-bound to `SPEC.ports`."""
    return docker.port_conflicts_for(SPEC)
