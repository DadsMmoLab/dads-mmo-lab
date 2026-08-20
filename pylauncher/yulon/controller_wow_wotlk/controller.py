"""The WotLK controller: the base `Controller` bound to the WotLK `ContainerSpec`.

Nothing is reimplemented here (roadmap 1.4's definition of done). The only
WotLK-specific fact — which containers and ports make up an install — already
lives in `docker_ctl.SPEC`; this class hands it to the shared base and
inherits start/stop/status/polling and the README §12 port-conflict guard.
"""

from __future__ import annotations

from pathlib import Path

from yulon.controller import Controller
from yulon.controller_wow_wotlk import docker_ctl


class WotlkController(Controller):
    """Lifecycle controller for one WotLK (AzerothCore) install."""

    def __init__(self, server_dir: Path) -> None:
        super().__init__(docker_ctl.SPEC, server_dir)
