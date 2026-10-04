"""Docker lifecycle for a Centurion server: the shared operations, this entry's facts.

The behaviour (`start`/`stop`/`status`/polling/port conflicts) is `yulon.docker`'s
and is re-exported, as every sibling package does. What is this package's own is
where the facts come from: the ENTRY handed in, never a catalog read at import
(see the package docstring), so the functions below take it.

**No `repair_import` is exported**, for the CMaNGOS packages' reason: the stack has
no one-shot import service (`containers.db_import` is unset), so
`docker.repair_import()` would refuse before it touched anything.
"""

from __future__ import annotations

import re
from dataclasses import replace
from typing import Final

from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, NativeInstall, ReadyMarkers, TrinityCoreData

GAME: Final = "wow-centurion"
"""The catalog id this package manages (`controller_view._FACTORIES`)."""

start = docker.start
start_staged = docker.start_staged
remove = docker.remove_staged
stop_staged = docker.stop_staged
status = docker.status
health = docker.health
wait_db_healthy = docker.wait_db_healthy
port_conflicts = docker.port_conflicts

_TOKEN = re.compile(r"\{\{[A-Z_]+\}\}")


def native_block(entry: CatalogEntry) -> tuple[NativeInstall, TrinityCoreData]:
    """The entry's install block and its `trinitycore` block, or why it cannot be managed.

    Raises:
        RuntimeError: the entry has no native install or is not a TrinityCore one.
            Raised when the controller is BUILT, where the catalog can be named.
    """
    block = entry.install.native
    if block is None or block.trinitycore is None:
        raise RuntimeError(
            f"{entry.id} is not a TrinityCore install (family "
            f"{block.family if block is not None else None!r}), so this package cannot "
            "manage it"
        )
    return block, block.trinitycore


def _pattern(entry: CatalogEntry, text: str, *, regex: bool) -> str:
    """One ready marker as the regular expression `docker.wait_ready()` searches with.

    A literal marker is escaped (`re.search` would read its `.` as a wildcard).
    A marker carrying a `{{TOKEN}}` is refused: only the install engine has a
    realm host and a world port to fill it with, and escaping the braces instead
    would match nothing -- a server that is up, reported as never ready.

    Raises:
        ValueError: the marker needs filling, or `regex: true` and it will not compile.
    """
    found = _TOKEN.search(text)
    if found is not None:
        raise ValueError(
            f"{entry.id}'s ready marker {text!r} carries {found.group()}, which only the install "
            "engine can fill; a controller has no realm host or world port to put there"
        )
    pattern = text if regex else re.escape(text)
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(
            f"{entry.id}'s ready marker {text!r} is not a usable pattern ({exc}); "
            "fix `install.native.ready` in catalog.json"
        ) from exc
    return pattern


def ready_spec(
    entry: CatalogEntry, *, timeout: float | None = None, interval: float | None = None
) -> docker.ReadySpec:
    """This server's `ReadySpec`, from `install.native.ready` -- never AzerothCore's.

    `docker.azerothcore_ready()` waits for `ready...` in the world log and a
    `<host>:<port>` line in the auth log. A TrinityCore worldserver prints
    neither in that form; the entry says what it does print. `timeout` and
    `interval` override the data for a caller that wants a shorter wait.
    """
    block, _ = native_block(entry)
    ready: ReadyMarkers = block.ready
    spec = docker.ReadySpec(
        world=_pattern(entry, ready.world, regex=ready.regex),
        auth=None if ready.auth is None else _pattern(entry, ready.auth, regex=ready.regex),
        fatal=None if ready.fatal is None else _pattern(entry, ready.fatal, regex=ready.regex),
        timeout=float(ready.timeout_s) if timeout is None else timeout,
        restart_loop=ready.restart_loop,
    )
    return spec if interval is None else replace(spec, interval=interval)


def wait_server_ready(
    entry: CatalogEntry, *, wsl_distro: str | None = None, **kwargs: float
) -> bool:
    """Poll until the worldserver has printed its ready line. `kwargs`: timeout/interval.

    `timeout` is a QUIET budget, as everywhere in this app: how long the world may
    print nothing new (`native.wait_ready_quietly`). Centurion's first start loads
    a 300 MB world and logs 150 bots in, printing all the way.
    """
    unknown = set(kwargs) - {"timeout", "interval"}
    if unknown:
        raise TypeError(f"wait_server_ready() accepts timeout/interval only, not {sorted(unknown)}")
    ready = ready_spec(entry, timeout=kwargs.get("timeout"), interval=kwargs.get("interval"))
    return native.wait_ready_quietly(entry.container_spec(), ready, wsl_distro=wsl_distro)


def wait_db_healthy_ready(
    entry: CatalogEntry, *, wsl_distro: str | None = None, **kwargs: float
) -> bool:
    """`wait_db_healthy()` pre-bound to this entry's database container."""
    return docker.wait_db_healthy_for(entry.container_spec(), wsl_distro=wsl_distro, **kwargs)


def port_conflicts_here(entry: CatalogEntry) -> list[str]:
    """`port_conflicts()` pre-bound to this entry's ports."""
    return docker.port_conflicts_for(entry.container_spec())
