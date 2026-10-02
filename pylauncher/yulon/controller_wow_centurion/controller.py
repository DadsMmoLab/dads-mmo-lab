"""The Centurion controller: the base `Controller` over this entry's own containers.

One method is reimplemented, for the TBC package's reason: `Controller.wait_ready()`
calls `docker.azerothcore_ready()`, AzerothCore's `ready...` marker and its auth
`<host>:<port>` line. Inherited unchanged on a TrinityCore install it would poll a
worldserver log for a line that server never prints and answer False after the
whole budget -- an install that serves, read as one that never came up. The
override takes the markers from the entry through `docker_ctl.ready_spec()`.

The entry is handed in and kept (`Controller.entry`), because it is not in the
shipped catalog the base class looks entries up in (`controller._entry_for`) until
T179 Task 7, and the time zone put back before every start needs it.

No `import_probe` and no `reset_unfinished`: the Repair button's only action is
`docker.repair_import()`, which refuses an entry with no import service, and this
one has none (its import is the engine's marker-gated SQL plan).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from yulon.catalog.catalog import CatalogEntry
from yulon.controller import Controller
from yulon.controller_wow_centurion import docker_ctl


class CenturionController(Controller):
    """Lifecycle controller for one TrinityCore (Centurion) install."""

    entry: CatalogEntry
    """Always set here, so narrower than the base's `CatalogEntry | None`."""

    def __init__(
        self,
        entry: CatalogEntry,
        server_dir: Path,
        *,
        wsl_distro: str | None = None,
        pre_stop: Callable[[], object] | None = None,
    ) -> None:
        docker_ctl.native_block(entry)  # refuses an entry that is not TrinityCore's, here
        super().__init__(
            entry.container_spec(),
            server_dir,
            wsl_distro=wsl_distro,
            pre_stop=pre_stop,
        )
        self.entry = entry

    def wait_ready(self, realm_host: str = "", realm_port: int = 0, **kwargs: float) -> bool:
        """Poll until the worldserver has printed the entry's ready line.

        `realm_host`/`realm_port` are accepted and unused, as on TBC: they spell
        AzerothCore's auth marker, and this entry names its own markers.
        """
        del realm_host, realm_port
        return docker_ctl.wait_server_ready(self.entry, wsl_distro=self.wsl_distro, **kwargs)
