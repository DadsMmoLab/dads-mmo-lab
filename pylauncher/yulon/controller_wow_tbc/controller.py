"""The TBC controller: the base `Controller` bound to this game's `ContainerSpec`.

One method is reimplemented, and only one. `Controller.wait_ready()` calls
`docker.azerothcore_ready()` directly — AzerothCore's `ready...` world marker
and its `<host>:<port>` auth marker — which is a per-game fact sitting in the
shared base. Inherited unchanged on a CMaNGOS install it polls a mangosd log
for a line that server never prints, and answers False after the full timeout,
so the install reads as "started but never ready" while it is serving. The
override takes the markers from `catalog.json` through `docker_ctl.ready_spec()`
instead; everything else — start/stop/remove/status/port conflicts/the README
§12 guard — is inherited with nothing added.

`import_probe` and `reset_unfinished` are left at the base's `None` on purpose.
They exist for the Repair button, whose only action is
`docker.repair_import()`, whose first refusal is "this game does not say which
compose service imports its databases" — this stack has no such service (see
`docker_ctl`). Handing them in would put a button in front of that sentence and,
for `reset_unfinished`, in front of something worse; `repair.py` says what and
hands the decision to whoever wires the view.
"""

from __future__ import annotations

from pathlib import Path

from yulon import docker
from yulon.controller import Controller
from yulon.controller_wow_tbc import docker_ctl


class TbcController(Controller):
    """Lifecycle controller for one WoW TBC (CMaNGOS) install."""

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

    def wait_ready(self, realm_host: str = "", realm_port: int = 0, **kwargs: float) -> bool:
        """Poll until mangosd has printed its ready line. `kwargs` forward timeout/interval.

        `realm_host` and `realm_port` are accepted, defaulted and unused. They
        are the base class's way of spelling AzerothCore's auth marker, and this
        entry's `install.native.ready.auth` is null — there is no realmd line to
        build out of them. They stay in the signature because a caller holding a
        `Controller` may pass them (the base declares them required), and they
        are given defaults so a caller who knows which game this is need not
        invent a host to satisfy an argument nothing reads.
        """
        del realm_host, realm_port
        return docker_ctl.wait_server_ready(wsl_distro=self.wsl_distro, **kwargs)
