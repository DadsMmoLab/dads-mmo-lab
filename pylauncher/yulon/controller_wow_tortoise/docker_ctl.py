"""The per-game Docker surface for Tortoise: the container spec, and what "ready" means here.

The shared behaviour (`start`/`stop`/`status`/`health`/polling/`port_conflicts`)
lives in `yulon.docker` and is not re-exported here. `controller_wow_wotlk.docker_ctl`
does re-export it, one alias per operation; nothing in the app imports those
aliases (the app drives the `Controller` object, and the only name any caller
outside that package reads is `SPEC`), so nine aliases here would be nine names
to keep in step with no reader to notice when they drift.

Two things genuinely belong to this game and are here:

* `SPEC`, built from the entry rather than written out. Its `import_service` is
  empty, because `containers.db_import` is null for this entry — this core has
  no one-shot import job to re-run, so `docker.repair_import()` refuses it by
  construction and this package exposes no repair action (see `repair.py`).
* `ready_spec()`, because "the server is up" is a different sentence on a
  mangosd than on an AzerothCore worldserver, and the base controller's is the
  latter.
"""

from __future__ import annotations

import re

from yulon import docker
from yulon.catalog import composegen, native
from yulon.catalog.catalog import ReadyMarkers
from yulon.controller_wow_tortoise import game

SPEC = game.entry().container_spec()
"""This install's containers and published ports, off the catalog entry.

Read at import so every module in this package shares one object, exactly as
the WotLK `SPEC` is a module constant — the difference is where the names come
from, not when.
"""


class ReadyMarkerError(RuntimeError):
    """A `ready` marker in the catalog is not a usable pattern, so nothing was waited on."""


def ready_spec(realm_host: str, realm_port: int, **kwargs: float) -> docker.ReadySpec:
    """This game's `ReadySpec`: the entry's markers, filled and made regexes.

    `docker.azerothcore_ready()` is the shape this replaces, and the two differ
    in every field. Its world marker is the literal `ready...`; a mangosd prints
    nothing of the kind, so a Tortoise server waited on with it is reported as
    never coming up. Its auth marker is `<host>:<port>` in the authserver log;
    this entry declares `auth: null`, meaning the realmd log is not waited on at
    all, and inventing a line for it would fail an install that is fine. It also
    has no `fatal` marker, which this game does: a worldserver that says its map
    files are missing is never going to be ready, and `wait_ready()` ends at once
    rather than polling out the timeout.

    `kwargs` forwards `timeout`/`interval` the way `azerothcore_ready()` does,
    and an explicit `timeout` wins over the entry's `timeout_s`; with neither,
    the entry's is used, which is the point of it being data. Markers other than
    the entry's go through `ready_spec_from()`, which this is a binding of.

    Filling and escaping mirror what the install spine does at the end of an
    install (`native.StagedInstaller._ready_spec`), because they are the same
    markers read at a different moment. Two things differ and neither can be
    borrowed: that method fills `REALM_HOST` with the installer's fixed
    loopback address while this one fills it with the host the caller is
    watching for, and it raises `InstallerError`, which is the vocabulary of an
    install and not of a running server.

    Raises:
        TypeError: a keyword other than `timeout`/`interval`, as
            `azerothcore_ready()` refuses them — `restart_loop` is an int and
            comes from the entry, and anything else is a typo.
        ReadyMarkerError: a marker left a `{{TOKEN}}` unfilled, or is declared
            `regex: true` and does not compile. Raised before anything is
            polled, so a typo in a data file is never a crash in the middle of
            a wait.
    """
    return ready_spec_from(game.ready_markers(), realm_host, realm_port, **kwargs)


def ready_spec_from(
    declared: ReadyMarkers, realm_host: str, realm_port: int, **kwargs: float
) -> docker.ReadySpec:
    """`ready_spec()` over markers handed in rather than read from the entry.

    Its own function, not a defaulted argument, because `**kwargs: float` and a
    `ReadyMarkers` parameter cannot share one signature: a forwarded
    `**kwargs` could land on the marker parameter, and the type checker says so.
    The tests drive the `regex: false` branch through here — this entry declares
    `regex: true`, so its own markers never reach the escaping path.
    """
    unknown = set(kwargs) - {"timeout", "interval"}
    if unknown:
        raise TypeError(f"ready_spec() accepts timeout/interval only, not {sorted(unknown)}")
    tokens = {"REALM_HOST": realm_host, "WORLD_PORT": str(realm_port)}

    def pattern(text: str) -> str:
        try:
            filled = composegen.fill(text, tokens)
        except composegen.ComposeGenError as exc:
            raise ReadyMarkerError(f"{game.GAME}'s ready marker {text!r} is broken: {exc}") from exc
        # A literal marker is escaped, because `wait_ready()` searches with
        # `re.search`: unescaped, the dots in an address match any character.
        # `regex: true` markers are handed over as written — this entry's world
        # marker is an alternation, which is why the flag exists.
        out = filled if declared.regex else re.escape(filled)
        try:
            re.compile(out)
        except re.error as exc:
            raise ReadyMarkerError(
                f"{game.GAME}'s ready marker {text!r} is not a usable pattern ({exc}); "
                "nothing was waited on."
            ) from exc
        return out

    return docker.ReadySpec(
        world=pattern(declared.world),
        auth=pattern(declared.auth) if declared.auth is not None else None,
        fatal=pattern(declared.fatal) if declared.fatal is not None else None,
        timeout=kwargs.get("timeout", float(declared.timeout_s)),
        # `ReadySpec.interval` read off the CLASS is that field's default: the
        # poll interval is not a per-game fact and the catalog does not carry
        # one, so the shared default stands unless a caller overrides it.
        interval=kwargs.get("interval", docker.ReadySpec.interval),
        restart_loop=declared.restart_loop,
    )


def wait_db_healthy_ready(*, wsl_distro: str | None = None, **kwargs: float) -> bool:
    """`wait_db_healthy()` pre-bound to `SPEC.db`. `kwargs` forward timeout/interval."""
    return docker.wait_db_healthy_for(SPEC, wsl_distro=wsl_distro, **kwargs)


def wait_server_ready(
    realm_host: str, realm_port: int, *, wsl_distro: str | None = None, **kwargs: float
) -> bool:
    """Poll until this install's world container reports ready, or clearly never will.

    `timeout` is the entry's `timeout_s` and it is a QUIET budget: how long the
    world server may print nothing new, restarted every time it prints, bounded
    by `native.management_ceiling()`. Until 2026-09-05 this spent it once, as a
    fixed total, while the install spine spent the same field as a window. This
    entry is also the one where the two readings differ most in wall clock: it
    is the only shipped entry that declares a `fatal` marker and the only one
    whose `timeout_s` is not 1800 — it is 3600 (read off `catalog.json`
    2026-09-05) — so the single-shot reading gave it an hour and the quiet one
    gives it six.
    """
    return native.wait_ready_quietly(
        SPEC, ready_spec(realm_host, realm_port, **kwargs), wsl_distro=wsl_distro
    )


def port_conflicts_here(*, wsl_distro: str | None = None) -> list[str]:
    """`port_conflicts()` pre-bound to `SPEC.ports`."""
    return docker.port_conflicts_for(SPEC, wsl_distro=wsl_distro)
