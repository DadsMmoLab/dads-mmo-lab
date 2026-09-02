"""The Vanilla controller: the base `Controller` bound to this game's `ContainerSpec`.

Almost nothing is reimplemented here, which is roadmap 1.4's definition of
done: the containers and ports come from `docker_ctl.SPEC`, and start / stop /
remove / status / the README §12 port-conflict guard are inherited untouched.

`wait_ready()` is the one exception, and it is overridden because the base
class's body is a statement about AzerothCore rather than about controllers:
it builds `docker.azerothcore_ready()`, whose world marker is `ready...` and
whose auth marker is the realm address the authserver logs. A CMaNGOS
worldserver prints neither. Left inherited, this method would poll a healthy
server until it timed out — or worse, match `alREADY UP-to-date` in the
loading log and report a server that is still reading maps as up.

The signature is kept so the override is a substitution and not a new method,
and `realm_port` is accepted and unused: this entry declares `ready.auth` as
null, so there is no auth line for an address to go into. Saying so here is
the point — an argument silently dropped is how a caller comes to believe a
port was checked.
"""

from __future__ import annotations

from pathlib import Path

from yulon import docker
from yulon.controller import Controller
from yulon.controller_wow_vanilla import docker_ctl


class VanillaController(Controller):
    """Lifecycle controller for one Vanilla (CMaNGOS mangos-classic) install."""

    def __init__(
        self,
        server_dir: Path,
        *,
        wsl_distro: str | None = None,
        import_probe: docker.ImportProbe | None = None,
        reset_unfinished: docker.ResetUnfinished | None = None,
    ) -> None:
        super().__init__(
            docker_ctl.SPEC,
            server_dir,
            wsl_distro=wsl_distro,
            import_probe=import_probe,
            reset_unfinished=reset_unfinished,
        )

    def wait_ready(self, realm_host: str, realm_port: int, **kwargs: float) -> bool:
        """Poll until the worldserver says it is up, using THIS game's ready markers.

        `realm_port` is unused — see the module docstring. `kwargs` forward
        `timeout`/`interval` exactly as the base class's do; omitted, the wait
        is the entry's own `ready.timeout_s` rather than `ReadySpec`'s 480s
        default, because 480s has been measured to be short enough to call a
        working CMaNGOS first boot a failed install.
        """
        return docker_ctl.wait_server_ready(realm_host, wsl_distro=self.wsl_distro, **kwargs)
