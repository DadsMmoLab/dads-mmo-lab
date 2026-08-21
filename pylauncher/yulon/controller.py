"""Base controller every per-game `controller_<acronym>/` package subclasses.

This is the one place the *object-shaped* shared surface lives (roadmap Phase
1.4; style-guide §4). The behavior itself — compose up/down, `docker ps`
parsing, health polling, the port scan — stays in the module-level functions
of `yulon.docker`; this class only composes a `ContainerSpec` with a server
directory and layers the single-instance policy from README §12 on top of
`start()`. A per-game subclass supplies its spec and inherits everything else
with zero reimplementation.

Genuine "is-a" (style-guide §2): a WotLK controller *is a* controller. What it
holds — the spec, the server dir — is composed, not inherited.

Deliberately **not** here: anything manifest-driven. Module/mod knowledge is
Phase 2.3's `modules.py`, layered on later, never stubbed in this class.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from yulon import docker
from yulon.log import get_logger

logger = get_logger(__name__)

# How long `start()` waits for the database between starting it and starting
# auth/world. Shorter than a first-boot import (that path goes through
# `compose up` instead): this is a restart of containers that already exist.
_START_DB_HEALTH_TIMEOUT = 120.0


class PortConflictError(RuntimeError):
    """Raised by `Controller.start()` when another container already binds our ports.

    README §12: every v1 server shares the same ports, so a second install can
    never start while the first still binds them. `containers` names the
    offenders so the UI can say *which* install must be stopped first, instead
    of surfacing a raw port-in-use error from Docker.
    """

    def __init__(self, containers: list[str], ports: tuple[int, ...]) -> None:
        self.containers = containers
        self.ports = ports
        joined = ", ".join(containers)
        super().__init__(
            f"cannot start: port(s) {ports} already bound by running container(s): {joined}"
        )


@dataclass(frozen=True)
class InstallStatus:
    """Which of one install's three containers are currently running."""

    db: bool
    auth: bool
    world: bool

    @property
    def any_running(self) -> bool:
        """True if at least one of the install's containers is up."""
        return self.db or self.auth or self.world

    @property
    def all_running(self) -> bool:
        """True only if the whole install (db + auth + world) is up."""
        return self.db and self.auth and self.world


class Controller:
    """Lifecycle surface for one server install: a `ContainerSpec` + a server dir.

    Subclass per game and pass the game's spec to `super().__init__()`; do not
    override the lifecycle methods — if a game needs different behavior, that
    is a sign the shared `yulon.docker` layer needs the capability, not that
    the subclass should reimplement it (style-guide §4).
    """

    def __init__(self, spec: docker.ContainerSpec, server_dir: Path) -> None:
        self.spec = spec
        self.server_dir = server_dir

    # -- queries ---------------------------------------------------------

    def status(self) -> InstallStatus:
        """Report which of this install's containers are running (`docker ps`)."""
        running = set(docker.status())
        return InstallStatus(
            db=self.spec.db in running,
            auth=self.spec.auth in running,
            world=self.spec.world in running,
        )

    def port_conflicts(self) -> list[str]:
        """Return *foreign* running containers binding this install's ports.

        `yulon.docker.port_conflicts_for()` is a global scan that also reports
        this install's own containers (e.g. mid-restart). Those are not a
        conflict — only something that is not ours counts — so they are
        filtered out here, once, for every game.
        """
        own = {self.spec.db, self.spec.auth, self.spec.world}
        return [name for name in docker.port_conflicts_for(self.spec) if name not in own]

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Bring the install up, refusing if another install holds our ports.

        Uses `docker.start_staged()`, so restarting an installed server never
        re-runs its one-shot database import (see that function).

        Raises:
            PortConflictError: A container that is not part of this install
                already binds one of `spec.ports`. Nothing is started.
            docker.DockerCommandError: The `docker` CLI itself failed.
        """
        conflicts = self.port_conflicts()
        if conflicts:
            logger.warning(f"start() refused: ports {self.spec.ports} bound by {conflicts}")
            raise PortConflictError(conflicts, self.spec.ports)
        docker.start_staged(
            self.spec,
            self.server_dir,
            wait_healthy=lambda container: docker.wait_db_healthy(
                container, timeout=_START_DB_HEALTH_TIMEOUT
            ),
        )

    def stop(self) -> None:
        """Take the install down (`docker compose down` in the server dir)."""
        docker.stop(self.server_dir)

    # -- polling ---------------------------------------------------------

    def wait_db_healthy(self, **kwargs: float) -> bool:
        """Poll until the DB container is healthy. `kwargs` forward timeout/interval."""
        return docker.wait_db_healthy_for(self.spec, **kwargs)

    def wait_ready(self, realm_host: str, realm_port: int, **kwargs: float) -> bool:
        """Poll until auth+world are up and ready. `kwargs` forward timeout/interval."""
        return docker.wait_ready_for(self.spec, realm_host, realm_port, **kwargs)
