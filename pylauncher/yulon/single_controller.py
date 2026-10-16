"""The lifecycle of one single-container install: status, start, stop and its log.

The `Controller` of the catalog's second kind of entry (`catalog.SingleEntry`). The WoW
base `controller.Controller` composes a `ContainerSpec` of db, auth and world and runs
its import and realm checks around every start; a server of this kind has one
container and none of those, so it gets this class instead and the WoW one is left
exactly as it is. What it does not know it asks `docker.py`, the shared, game-agnostic
lifecycle module, through seams — so no UI file builds a docker argv (style-guide §3).

Ownership is checked the way the install engine claims it: the container answers to
this install's compose project (`composegen.project_name()`), or it is not ours to stop.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from yulon import docker, platform, single_network
from yulon.catalog import composegen
from yulon.catalog.catalog import SingleEntry
from yulon.catalog.single_install import container_parts, missing_ready_lines
from yulon.log import get_logger

logger = get_logger(__name__)

STOP_DEADLINE_SECONDS = 60.0
"""How long one `docker stop` may take. The compose file gives the server 30 s to stop."""

RUNNING = "running"
STARTING = "starting"
STOPPED = "stopped"
UNKNOWN = "unknown"
"""The four statuses `status()` answers, in the words `DadcraftRealmBadge` already draws."""


class SingleServerError(RuntimeError):
    """A start or stop refused or failed; the message is the sentence a player reads."""


class SingleServer(Protocol):
    """What the Server tab of a single server asks of its controller, in either runtime.

    `SingleController` answers it for one container (DDON) and
    `process_controller.ProcessController` for host programs (DFO), so
    `ui/single_server_view.py` draws both and builds neither.
    """

    entry: SingleEntry
    server_dir: Path
    follows_with_cancel: bool
    """True when `follow_logs()` ends once its `cancel` event is set.

    The tab then hands `LogPanel` that event, which promises the panel the source
    ends: a log read by polling a file has no child process for the panel's Stop
    to end, and a thread left polling would outlive the window.
    """

    def status(self) -> str: ...

    def start(self, *, account: str = "", client_dir: Path | None = None) -> None: ...

    def stop(self) -> bool: ...

    def follow_logs(self, cancel: threading.Event | None = None) -> Iterator[str]: ...

    def advertised(self) -> str | None: ...

    def advertise(self, address: str) -> bool: ...

    def session_account(self) -> str | None: ...


@dataclass(frozen=True)
class SingleControlSeams:
    """Everything the controller reaches outside itself through. Real by default."""

    platform_id: Callable[[], str] = platform.detect
    state_of: Callable[[str], docker.ContainerState] = docker.container_state
    run_log: Callable[[str], docker.RunLog] = docker.current_run_log
    container_project: Callable[[str], str | None] = docker.container_project
    port_conflicts: Callable[[tuple[int, ...]], list[str]] = docker.port_conflicts
    up: Callable[..., None] = docker.compose_up_service
    stop: Callable[..., None] = docker.stop_containers
    follow: Callable[[str], Iterator[str]] = docker.follow_logs


class SingleController:
    """Start, stop, read the status of, and follow the log of one single-container install."""

    follows_with_cancel = False
    """`docker logs -f` is a child process, which the panel's own Stop ends."""

    def __init__(
        self,
        entry: SingleEntry,
        server_dir: Path,
        *,
        seams: SingleControlSeams | None = None,
    ) -> None:
        self.entry = entry
        self.server_dir = server_dir
        _, self._container, self._service = container_parts(entry)
        self._seams = seams if seams is not None else SingleControlSeams()

    @property
    def container(self) -> str:
        """The one container this install runs."""
        return self._container

    def project(self) -> str:
        """This install's compose project, which its container is labelled with."""
        return composegen.project_name(
            self.entry.id, self.server_dir, platform_id=self._seams.platform_id
        )

    def status(self) -> str:
        """`running` once every ready line is in this run's log, else what Docker says.

        `starting` covers both a container that has not listened yet and one in restart
        backoff; `stopped` is a container that is not there or not running; `unknown` is
        a daemon that would not answer, which is never read as stopped.
        """
        state = self._seams.state_of(self.container)
        if state.missing or state.status in ("exited", "dead", "created"):
            return STOPPED
        if state.status == "":
            return UNKNOWN
        if state.status != "running":
            return STARTING
        log = self._seams.run_log(self.container)
        if log.this_run_only and not missing_ready_lines(self.entry, log.text):
            return RUNNING
        return STARTING

    def is_ours(self) -> bool:
        """Whether the container by our name belongs to this install's compose project."""
        return self._seams.container_project(self.container) == self.project()

    def start(self, *, account: str = "", client_dir: Path | None = None) -> None:
        """Bring the server up. Refuses while somebody else's container holds one of its ports.

        `account` and `client_dir` are the process runtime's (`SingleServer`): a container
        server reads neither, since its client names the account at its own login.
        """
        held = [
            name
            for name in self._seams.port_conflicts(self.entry.host_ports())
            if name != self.container
        ]
        if held:
            raise SingleServerError(
                f"{self.entry.name} needs ports "
                f"{', '.join(str(p) for p in self.entry.host_ports())}, and "
                f"{', '.join(sorted(held))} is running on one of them. Stop it first."
            )
        try:
            self._seams.up(self.server_dir, self._service)
        except docker.DockerCommandError as exc:
            raise SingleServerError(f"{self.entry.name} could not be started: {exc}") from exc
        logger.info(f"{self.entry.id} started from {self.server_dir}")

    def stop(self) -> bool:
        """Stop the server if it is ours and running; False when there was nothing to stop.

        Never stops a container of our name that another install's project owns:
        two folders of one game share the container name, and the running one may be
        the other folder's.
        """
        if self.status() == STOPPED:
            return False
        if not self.is_ours():
            raise SingleServerError(
                f"The running {self.container} was not started from {self.server_dir}, so it "
                "was left running. Stop it from the tab of the folder it belongs to."
            )
        try:
            self._seams.stop([self.container], deadline=STOP_DEADLINE_SECONDS)
        except docker.DockerCommandError as exc:
            raise SingleServerError(f"{self.entry.name} could not be stopped: {exc}") from exc
        logger.info(f"{self.entry.id} stopped in {self.server_dir}")
        return True

    def follow_logs(self, cancel: threading.Event | None = None) -> Iterator[str]:
        """The container's log, live (`docker logs -f`), for the tab's console.

        `cancel` is not read (`follows_with_cancel` is False): the panel ends the child.
        """
        return self._seams.follow(self.container)

    def session_account(self) -> str | None:
        """None: a container server is not started for one account."""
        return None

    def advertised(self) -> str | None:
        """The address clients are told to connect to, or None if this server keeps none."""
        if self.entry.advertise is None:
            return None
        try:
            return single_network.read_address(self.entry, self.server_dir)
        except single_network.AddressFileError as exc:
            raise SingleServerError(str(exc)) from exc

    def advertise(self, address: str) -> bool:
        """Tell clients to connect to `address`; True when a running server was restarted.

        The server reads the address file only as it starts, so a running one is
        stopped and started again for the change to reach the next player who logs in.
        A stopped server simply reads it at its next Start.
        """
        try:
            changed = single_network.write_address(self.entry, self.server_dir, address)
        except single_network.AddressFileError as exc:
            raise SingleServerError(str(exc)) from exc
        if not changed or self.status() not in (RUNNING, STARTING):
            return False
        self.stop()
        self.start()
        return True
