"""The Tortoise controller: the base `Controller` bound to this game's spec and ready markers.

One method is overridden, and the base class's own rule says a per-game
subclass should not need to — "if a game needs different behavior, that is a
sign the shared layer needs the capability". The capability is there:
`docker.wait_ready()` takes a `ReadySpec` and knows nothing about any game.
What is not shared is `Controller.wait_ready()`, which builds that spec by
calling `docker.azerothcore_ready()` — an AzerothCore fact compiled into the
shared class. Waiting for `ready...` on a mangosd polls until it times out on a
server that came up minutes ago.

Overriding it here is the smaller of the two changes available to this task:
the alternative is editing `yulon/controller.py`, which every game shares and
which this agent does not own. The override keeps the signature exactly, so a
caller holding a `Controller` cannot tell which one it has — `realm_host` and
`realm_port` still mean what they meant, and they fill the `{{REALM_HOST}}` and
`{{WORLD_PORT}}` tokens the entry's markers are written against.

The controller is built WITHOUT an import probe, and that is a decision rather
than an omission: see `controller_for()`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from yulon import realm_flag, wsl
from yulon.catalog import native
from yulon.controller import Controller
from yulon.controller_wow_tortoise import botdash, docker_ctl, game
from yulon.log import get_logger

logger = get_logger(__name__)


class TortoiseController(Controller):
    """Lifecycle controller for one Tortoise (CMaNGOS-lineage) install."""

    def __init__(
        self,
        server_dir: Path,
        *,
        wsl_distro: str | None = None,
        pre_stop: Callable[[], object] | None = None,
    ) -> None:
        super().__init__(docker_ctl.SPEC, server_dir, wsl_distro=wsl_distro, pre_stop=pre_stop)

    def _mark_the_realm_offline(
        self, *, start_database: bool, unless_world_up: bool = False
    ) -> None:
        """Set the realm row's offline bit, so the realm list says Offline while the world is down.

        T577: this core's world server never sets the bit, only clears it when it starts
        listening, so without this the realm shows online for the whole load and a client
        that logs in then goes back to the realm list with no word. Best effort and never
        raising: `realm_flag.mark_offline()` logs what it could not do.
        """
        try:
            entry = game.entry()
        except game.CatalogFactsError as exc:
            logger.warning(f"the realm was not marked offline: {exc}")
            return
        realm_flag.mark_offline(
            entry,
            self.spec,
            self.server_dir,
            wsl_distro=self.wsl_distro,
            start_database=start_database,
            unless_world_up=unless_world_up,
        )

    def _put_the_realm_back(self) -> None:
        """Take the offline bit off a realm whose world is still running; never raises."""
        try:
            entry = game.entry()
        except game.CatalogFactsError:
            return
        realm_flag.clear_offline_if_world_up(
            entry, self.spec, self.server_dir, wsl_distro=self.wsl_distro
        )

    def stop(self) -> bool:
        """Mark the realm offline while the database is still up, then stop (T577).

        Only when the database is already running: a Stop must not start one to say the
        realm is closing, and the Start that follows marks it again before its world starts.
        """
        if self.wsl_distro is not None and wsl.known_stopped(self.wsl_distro):
            return super().stop()
        # T581: held offline on purpose for the whole Stop, so the dashboard tick does not
        # put a realm back online while its world saves on the way down.
        with realm_flag.deliberately_offline(self.spec):
            self._mark_the_realm_offline(start_database=False)
            try:
                return super().stop()
            except Exception:
                # A Stop given up (a Cancel while the world loads or saves) or refused leaves
                # the world running, and only a start clears the bit: take it off again. If
                # that fails, the dashboard tick takes it off once the hold is over (T581).
                self._put_the_realm_back()
                raise

    def _before_the_servers_start(self) -> None:
        """Mark the realm offline (T577), then bring the bot dashboard up when it is on (T127).

        The offline mark goes first and needs the database, which it starts if it is not up
        (compose would start it for the world a moment later); the world clears the mark
        itself once it listens.

        The dashboard goes first among the servers, because the bots module resolves the
        dashboard's service name once, when it loads: a world that starts before the
        dashboard sends nowhere until its next start. Called by the base `start()` only once
        every refusal has passed -- its folder and guard, the ports, the
        database (T377) -- so a refused start does not leave the dashboard
        running on its own. `botdash.start_if_on()` never raises: the dashboard
        is never the reason a server does not start.
        """
        self._mark_the_realm_offline(start_database=True, unless_world_up=True)
        try:
            entry = game.entry()
        except game.CatalogFactsError as exc:
            logger.warning(f"the bot dashboard was not checked before the start: {exc}")
        else:
            botdash.start_if_on(entry, self.server_dir, wsl_distro=self.wsl_distro)

    def wait_ready(self, realm_host: str, realm_port: int, **kwargs: float) -> bool:
        """Poll until the world container is up and this core's ready marker appears.

        `kwargs` forward `timeout`/`interval` as the base class's do; with no
        `timeout` the entry's `ready.timeout_s` is used rather than the shared
        480s default, which is the number a first boot on a small box was
        measured against (`ReadyMarkers.timeout_s` carries that measurement).

        `timeout` is a QUIET budget, as it is everywhere else in this app: how
        long the world server may print nothing new, restarted every time it
        prints, bounded by `native.management_ceiling()`. This override and
        `controller_wow_wotlk.docker_ctl.wait_server_ready()` were the last two
        sites still spending it as a fixed total (2026-09-05). The round that
        moved the base controller and the three CMaNGOS `wait_server_ready()`
        functions across missed both, and the test claiming to cover "every
        ready wait in the app" named four sites in a parameter list rather than
        walking the package, so it could not have seen them.

        Raises:
            docker_ctl.ReadyMarkerError: a marker in the catalog is unusable.
                Raised before the first poll, so nothing waits on a typo.
        """
        return native.wait_ready_quietly(
            self.spec,
            docker_ctl.ready_spec(realm_host, realm_port, **kwargs),
            wsl_distro=self.wsl_distro,
        )


def controller_for(
    server_dir: Path,
    *,
    wsl_distro: str | None = None,
    pre_stop: Callable[[], object] | None = None,
) -> TortoiseController:
    """The controller for the install at `server_dir`, with no repair action attached.

    `Controller` takes an `import_probe`/`reset_unfinished` pair, and the Server
    tab shows its Repair button whenever the probe answers `absent` or
    `partial`. This game names no one-shot import service, so the button's
    action — `docker.repair_import()` — can only refuse: "this game never said
    which service imports, and guessing a service name is guessing which
    container gets run". A button whose sole outcome is a refusal is the exact
    shape `install_wiring.import_gate_for()` was written to stop offering to
    CMaNGOS installs, so the pair is left off and `Controller.import_state()`
    answers `unreadable`, which is not `repairable`.

    The question "is this install's database imported?" still has an answer here
    — `repair.import_state()` — and it is deliberately not wired to a button
    that would drop schemas nothing in this package can re-fill.
    """
    return TortoiseController(server_dir, wsl_distro=wsl_distro, pre_stop=pre_stop)
