"""Start auth only after the world said ready, then open the realm row (T812 C6).

Conquest of Azeroth's authserver lists a realm as online the moment its row's
flag says so, and a 3.3.5a client sent to a world that is not listening goes
back to the realm list with no error. So for an entry with
`azerothcore.auth_after_world_ready`, the start is split in two: db+world
first, the world's ready line, then auth, the auth's ready line, then the
realm row's offline bit is cleared. The clear is then re-asserted by two
backstops outside this module: the dashboard tick's Keeper (T581) clears a
stuck bit 2 after 60 s, and the world's uptime tick
(`AC_UPDATE_UPTIME_INTERVAL=1`) re-asserts the row each minute. A failed open
is said on the line, never raised: with the backstops the realm opens within
about a minute anyway.

One function used by all three start paths: the install's `up`, the
rebuild/update's `recreate`, and `Controller.start`. The flag off is exactly
today's single start: the same function, so WotLK and Unbound run the same
call, word for word.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.installer import ReadyWaitStopped
from yulon.log import get_logger

logger = get_logger(__name__)

OutputSink = Callable[[str], None]

WORLD_NEVER_READY = "The world server never said it was ready, so the login server was not started."
AUTH_NEVER_READY = "The login server never said it was ready, so the realm was not opened."
WORLD_WAIT_STOPPED = (
    "Stop was pressed while the world server was still loading, so the login server was not "
    "started. The world is still loading, and the Server tab shows when it is up."
)
AUTH_READY_SECONDS = 300.0
"""How long the login server may take to say it is ready once it was started (T812 C8 D13).

The world is already up when auth starts, and auth prints its ready line within
seconds; the quiet-budget wait the world uses never ran out on it, because the
world keeps printing (the live run waited 20+ minutes for a realm row that named
another port). Five minutes is generous for a start that takes seconds."""

AUTH_NEVER_READY_IN_TIME = (
    "The login server did not say it was ready within {minutes} minutes, so the realm was not "
    "opened. Its realm row may name another address or port than this server's: open the "
    "Network tab, or press Start again."
)

AUTH_WAIT_STOPPED = (
    "Stop was pressed while the login server was still starting, so the realm was not opened. "
    "Both servers are still coming up, and the Server tab shows when they are up."
)


def ordered(entry: CatalogEntry) -> bool:
    """Whether this entry starts its auth only after its world said ready."""
    native_block = entry.install.native
    return (
        native_block is not None
        and native_block.azerothcore is not None
        and native_block.azerothcore.auth_after_world_ready
    )


def ready_specs(entry: CatalogEntry) -> tuple[docker.ReadySpec, docker.ReadySpec]:
    """The world wait and the auth wait, from the entry's own ready markers.

    The world's wait has `auth=None`: it must not wait on a server that is
    deliberately still down. The auth's wait is the full spec: the world
    banner is still in the world's current-run log (re-confirming it is
    harmless, and catches a world that died while auth started), and the
    auth line is read off the auth log, the only log that prints it.
    """
    native_block = entry.install.native
    if native_block is None:
        raise ValueError(f"{entry.id} has no native install section, so it has no ready markers.")
    full = native.ready_spec_for(entry, native_block.ready)
    return replace(full, auth=None), full


def start(
    spec: docker.ContainerSpec,
    server_dir: Path,
    *,
    entry: CatalogEntry,
    start_services: Callable[..., bool],
    wait_world: Callable[..., bool],
    wait_auth: Callable[..., bool],
    open_realm: Callable[[], str | None],
    wsl_distro: str | None = None,
    force_recreate: bool = False,
    cancel: threading.Event | None = None,
    say: OutputSink = logger.info,
    auth_limit: float = AUTH_READY_SECONDS,
) -> bool:
    """Bring the install up; True once its servers are up and its realm opened.

    `start_services` is `docker.start_staged` (or the rebuild's recreate): it
    is handed `services=` naming which services to bring. `wait_world` and
    `wait_auth` are `native.wait_ready_quietly` for the two waits (the
    install's `stage_ready` re-confirms afterwards). `open_realm` is
    `realm_flag.open_realm`: a sentence back is said, not raised.

    Flag off: exactly one `start_services` over all three services, as today.
    Flag on: up db+world, wait the world ready, up auth, wait the auth ready,
    then `open_realm()`. An auth already running is left as it is: it only
    clears its offline bit at its own start, so a second up would not make it
    re-read the row.
    """
    if not ordered(entry):
        return bool(
            start_services(spec, server_dir, wsl_distro=wsl_distro, force_recreate=force_recreate)
        )
    services = spec.compose_services()
    by_container = dict(zip((spec.db, spec.auth, spec.world), services, strict=False))
    world_service = by_container.get(spec.world, spec.world)
    auth_service = by_container.get(spec.auth, spec.auth)
    start_services(
        spec,
        server_dir,
        services=[service for service in services if service != auth_service],
        wsl_distro=wsl_distro,
        force_recreate=force_recreate,
    )
    world_ready, auth_ready = ready_specs(entry)
    if not wait_world(spec, replace(world_ready, cancel=cancel), wsl_distro=wsl_distro):
        if cancel is not None and cancel.is_set():
            raise ReadyWaitStopped(WORLD_WAIT_STOPPED)
        raise docker.DockerRefusal(
            WORLD_NEVER_READY,
            detail=docker.logs_command(world_service, server_dir),
        )
    try:
        running = set(docker.status(wsl_distro=wsl_distro))
    except docker.DockerCommandError:
        running = set()
    if spec.auth not in running:
        start_services(
            spec,
            server_dir,
            services=[auth_service],
            wsl_distro=wsl_distro,
            force_recreate=force_recreate,
        )
    bounded = _Deadline(cancel, auth_limit)
    try:
        heard = wait_auth(spec, replace(auth_ready, cancel=bounded.event), wsl_distro=wsl_distro)
    finally:
        bounded.close()
    if not heard:
        if cancel is not None and cancel.is_set():
            raise ReadyWaitStopped(AUTH_WAIT_STOPPED)
        if bounded.ran_out:
            raise docker.DockerRefusal(
                AUTH_NEVER_READY_IN_TIME.format(minutes=max(1, round(auth_limit / 60))),
                detail=docker.logs_command(auth_service, server_dir),
            )
        raise docker.DockerRefusal(
            AUTH_NEVER_READY,
            detail=docker.logs_command(auth_service, server_dir),
        )
    refused = open_realm()
    if refused is not None:
        say(refused)
    return True


class _Deadline:
    """One event the auth wait is handed: set by the job's Stop OR when `limit` runs out.

    The wait takes a single cancel event, so a small watcher sets this one when
    either happens; `ran_out` says which. `close()` ends the watcher.
    """

    def __init__(self, cancel: threading.Event | None, limit: float) -> None:
        self.event = threading.Event()
        self.ran_out = False
        self._done = threading.Event()
        self._end = time.monotonic() + limit
        self._cancel = cancel
        self._watcher = threading.Thread(target=self._watch, daemon=True, name="yulon-auth-wait")
        self._watcher.start()

    def _watch(self) -> None:
        while not self._done.wait(0.2):
            if self._cancel is not None and self._cancel.is_set():
                self.event.set()
                return
            if time.monotonic() >= self._end:
                self.ran_out = True
                self.event.set()
                return

    def close(self) -> None:
        self._done.set()
        self._watcher.join(timeout=1.0)
