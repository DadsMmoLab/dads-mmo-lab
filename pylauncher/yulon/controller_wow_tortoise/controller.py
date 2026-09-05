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

from pathlib import Path

from yulon.catalog import native
from yulon.controller import Controller
from yulon.controller_wow_tortoise import docker_ctl


class TortoiseController(Controller):
    """Lifecycle controller for one Tortoise (CMaNGOS-lineage) install."""

    def __init__(self, server_dir: Path, *, wsl_distro: str | None = None) -> None:
        super().__init__(docker_ctl.SPEC, server_dir, wsl_distro=wsl_distro)

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


def controller_for(server_dir: Path, *, wsl_distro: str | None = None) -> TortoiseController:
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
    return TortoiseController(server_dir, wsl_distro=wsl_distro)
