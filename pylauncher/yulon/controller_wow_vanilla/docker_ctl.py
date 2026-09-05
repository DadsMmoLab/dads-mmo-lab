"""Docker lifecycle for the Vanilla server: this game's `ContainerSpec` and its readiness.

The shared behaviour (`start`/`stop`/`status`/`health`/polling/
`port_conflicts`) lives in `yulon.docker` and is re-exported below unchanged,
exactly as `controller_wow_wotlk/docker_ctl.py` does it. Two things are this
game's own and are the reason this module is not that one:

**The spec comes from the catalog, not from literals.** `SPEC` is
`entry().container_spec()`, so the container names (`vanilla-db`,
`vanilla-realmd`, `vanilla-mangosd`) and the published ports are the same
values `composegen` generated the compose file from. Its `import_service` is
empty, because the entry names no `db_import` service — a CMaNGOS install has
no one-shot importer.

**`repair_import` is deliberately NOT re-exported.** `docker.repair_import()`
refuses outright without an `import_service`, and offering the name here would
promise a repair this game cannot perform. What replaces it is in `repair.py`.

**Readiness is the entry's marker, not AzerothCore's.** `docker.wait_ready()`
searches the current run's log with `re.search`, and a CMaNGOS worldserver
never prints `ready...`. This entry names `Avg Diff:` and leaves `ready.auth`
null, so `ready_spec()` waits on the world log only. Handing this game
`docker.azerothcore_ready()` would produce a wait that can only ever time out
— except that `ready...` unescaped also matches `alREADY UP-to-date`, so it
would sometimes "succeed" against a still-loading server instead.
"""

from __future__ import annotations

import re

from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import NativeInstall
from yulon.catalog.composegen import fill
from yulon.catalog.native import INSTALL_REALM_HOST
from yulon.controller_wow_vanilla import entry


def _native() -> NativeInstall:
    """The entry's `install.native` block.

    Every shipped entry has one — `Install.native`'s own description says an
    entry with a non-empty `platforms` needs one, and preflight refuses
    otherwise — but the field is typed optional, so the absence is named here
    rather than reaching a caller as `AttributeError: 'NoneType'`.
    """
    native = entry().install.native
    if native is None:  # pragma: no cover - the shipped entry has one
        raise RuntimeError(f"{entry().name} has no `install.native` block to read.")
    return native


SPEC = entry().container_spec()
"""This install's containers, services and ports, straight off the catalog entry."""

DB_CLIENT = _native().db.client
"""The client binary this game's database image ships: `mariadb`, per the entry.

Read rather than assumed. `mariadb:11` removed the `mysql`/`mysqldump`
symlinks, so the name is not interchangeable with AzerothCore's; every module
here that names a client to `sqlplan` passes this value.

`apply.DockerSql` and `maintenance.DockerMysql` resolve the same question a
different way — `apply.mysql_client()` asks the container `command -v` and
caches the answer — so they need no argument from here. The two answers agree
for a healthy install; this one is what the entry DECLARES, and that one is
what the container actually has.
"""

READY = _native().ready
"""This game's `ready` block: `world` marker, `auth`/`fatal`, timeout, restart loop."""

# The shared operations, re-exported so callers import from this package and
# never from `yulon.docker` directly (style-guide §4). The list is the WotLK
# one minus `repair_import` — see the module docstring.
start = docker.start
start_staged = docker.start_staged
remove = docker.remove_staged
stop_staged = docker.stop_staged
status = docker.status
health = docker.health
wait_db_healthy = docker.wait_db_healthy
wait_ready = docker.wait_ready
port_conflicts = docker.port_conflicts


def ready_spec(realm_host: str = INSTALL_REALM_HOST, **kwargs: float) -> docker.ReadySpec:
    """The `ReadySpec` for this game, built from the entry's `ready` block.

    The rule applied to each marker is the spine's: fill `{{REALM_HOST}}` and
    `{{WORLD_PORT}}`, then `re.escape` the result unless the block says
    `regex: true`. That rule is written down twice now — here and in
    `catalog/families/../native.py`'s `StagedInstaller._ready_spec()`, which is
    a private method needing a `StageContext` this post-install path does not
    have. Both sites read the SAME `ReadyMarkers` object, so a marker cannot
    mean one thing to the installer and another to the controller; what could
    still drift is the escaping, and that is what
    `test_controller_wow_vanilla.py` pins against the entry's own text.

    Escaping matters here for the same reason it does there: `Avg Diff:` has no
    metacharacter today, and the first marker that gains a `.` would silently
    become a wildcard.

    Args:
        realm_host: what `{{REALM_HOST}}` becomes. This entry's markers name no
            token, so it changes nothing for `wow-vanilla`; it is accepted so a
            catalog edit that adds one does not need this function edited too.
        kwargs: `timeout` and `interval`, forwarded from a caller that has its
            own. Only those two — `restart_loop` is an int and anything else is
            a typo, so both are refused rather than silently dropped, the same
            guard `docker.azerothcore_ready()` carries.

    Raises:
        TypeError: `kwargs` named something other than timeout/interval.
        ValueError: a marker is not a usable pattern, or names a token this
            function cannot fill. Named here, where `catalog.json` is still the
            thing to fix, rather than as an `re.error` from inside a poll loop.
    """
    unknown = set(kwargs) - {"timeout", "interval"}
    if unknown:
        raise TypeError(f"ready_spec() accepts timeout/interval only, not {sorted(unknown)}")
    tokens = {"REALM_HOST": realm_host, "WORLD_PORT": str(entry().ports.world)}

    def marker(text: str) -> str:
        filled = fill(text, tokens)
        pattern = filled if READY.regex else re.escape(filled)
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(
                f"{entry().name}'s ready marker {text!r} is not a usable pattern ({exc}). "
                "Fix `install.native.ready` in catalog.json."
            ) from exc
        return pattern

    return docker.ReadySpec(
        world=marker(READY.world),
        auth=marker(READY.auth) if READY.auth is not None else None,
        fatal=marker(READY.fatal) if READY.fatal is not None else None,
        # The data wins over `ReadySpec`'s own 480s default, which covers only a
        # spec written in Python. Measured on m910q 2026-09-02 for the sibling
        # CMaNGOS game: 793s from container start to the first `Avg Diff:` on
        # four cores, against the 600s three entries then carried — which is why
        # the entry says 1800 and why this must not quietly halve it.
        timeout=kwargs.get("timeout", float(READY.timeout_s)),
        # The poll interval is not a per-game fact and the catalog has no field
        # for it, so the fallback is `ReadySpec`'s own default READ off the
        # class rather than a second copy of the number written here.
        interval=kwargs.get("interval", docker.ReadySpec.interval),
        restart_loop=READY.restart_loop,
    )


def wait_db_healthy_ready(*, wsl_distro: str | None = None, **kwargs: float) -> bool:
    """`wait_db_healthy()` pre-bound to `SPEC.db`. `kwargs` forward timeout/interval."""
    return docker.wait_db_healthy_for(SPEC, wsl_distro=wsl_distro, **kwargs)


def wait_server_ready(
    realm_host: str = INSTALL_REALM_HOST,
    *,
    wsl_distro: str | None = None,
    **kwargs: float,
) -> bool:
    """`wait_ready()` pre-bound to `SPEC`'s containers and this game's markers.

    The WotLK sibling takes `(realm_host, realm_port)` because its auth marker
    is the address the authserver prints. This entry declares `ready.auth` as
    null — nothing waits on the realmd log — so there is no port to pass and
    none is accepted, rather than one being accepted and ignored.

    `timeout` is the entry's `timeout_s` and it is a QUIET budget: how long
    mangosd may print nothing new, restarted every time it prints, bounded by
    `native.management_ceiling()`. Until 2026-09-05 this spent it once, as a
    fixed total, while the install spine spent the same field as a window — and
    this game is the one that makes the two readings impossible to argue as
    equivalent. On yulon-win11-gate 2026-09-04 Vanilla's first boot took 24.6
    minutes and TBC's took 46.0, both entries carrying `timeout_s: 1800`: one
    fixed total was right for one game and wrong for the other, on one machine,
    on the same day.
    """
    return native.wait_ready_quietly(SPEC, ready_spec(realm_host, **kwargs), wsl_distro=wsl_distro)


def port_conflicts_here() -> list[str]:
    """`port_conflicts()` pre-bound to `SPEC.ports`."""
    return docker.port_conflicts_for(SPEC)
