"""The lifecycle of one stack install: status, start, stop and its log (`catalog.SingleStack`).

The `single_controller.SingleServer` of the third runtime. A stack is several
containers of one compose project (FFXI: a database, a one-shot update and five
servers), so status is read from every listener, Start and Stop are the project's
own `compose up` / `compose stop`, and the console follows every listener at once.
What it does not know it asks `docker.py` through seams, so no UI file builds a
docker argv (style-guide §3).

Ownership is checked the way the install engine claims it: a container of ours
answers to this install's compose project (`composegen.project_name()`), or it is
not ours to stop.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

from yulon import docker, platform
from yulon.catalog import composegen
from yulon.catalog.catalog import SingleEntry
from yulon.catalog.stack_install import missing_ready_lines, stack_of
from yulon.log import get_logger
from yulon.single_controller import RUNNING, STARTING, STOPPED, UNKNOWN, SingleServerError

logger = get_logger(__name__)

STOP_TIMEOUT_SECONDS = 120.0
"""How long one `compose stop` may take. Each service gets its own 30 s grace period."""

_STOPPED_STATES = ("exited", "dead", "created")
"""What Docker calls a container that is not running and not about to be."""


@dataclass(frozen=True)
class StackControlSeams:
    """Everything the controller reaches outside itself through. Real by default."""

    platform_id: Callable[[], str] = platform.detect
    state_of: Callable[[str], docker.ContainerState] = docker.container_state
    run_log: Callable[[str], docker.RunLog] = docker.current_run_log
    container_project: Callable[[str], str | None] = docker.container_project
    port_conflicts: Callable[[tuple[int, ...]], list[str]] = docker.port_conflicts
    up: Callable[..., None] = docker.compose_up_all
    stop: Callable[..., None] = docker.compose_stop_all
    follow: Callable[[Path, Sequence[str]], Iterator[str]] = docker.compose_follow_logs


class StackController:
    """Start, stop, read the status of, and follow the log of one stack install."""

    follows_with_cancel = False
    """`docker compose logs -f` is a child process, which the panel's own Stop ends."""

    def __init__(
        self,
        entry: SingleEntry,
        server_dir: Path,
        *,
        seams: StackControlSeams | None = None,
    ) -> None:
        self.entry = entry
        self.server_dir = server_dir
        self.stack = stack_of(entry)
        self._seams = seams if seams is not None else StackControlSeams()

    def project(self) -> str:
        """This install's compose project, which its containers are labelled with."""
        return composegen.project_name(
            self.entry.id, self.server_dir, platform_id=self._seams.platform_id
        )

    def status(self) -> str:
        """`running` once every listener is up and printed its ready lines, else what Docker says.

        `stopped` is every listener absent or not running; `unknown` is a daemon that
        would not answer about one of them, which is never read as stopped; anything
        between (some up, some not yet listening, one in restart backoff) is `starting`.
        """
        listeners = self.stack.listeners
        states = [self._seams.state_of(s.container) for s in listeners]
        if any(state.status == "" and not state.missing for state in states):
            return UNKNOWN
        if all(state.missing or state.status in _STOPPED_STATES for state in states):
            return STOPPED
        if any(state.status != "running" for state in states):
            return STARTING
        for service in listeners:
            if not service.ready:
                continue
            log = self._seams.run_log(service.container)
            if not log.this_run_only or missing_ready_lines(service, log.text):
                return STARTING
        return RUNNING

    def is_ours(self) -> bool:
        """Whether every listener that exists belongs to this install's compose project."""
        project = self.project()
        found = False
        for service in self.stack.listeners:
            owner = self._seams.container_project(service.container)
            if owner is None:
                continue
            found = True
            if owner != project:
                return False
        return found

    def start(self, *, account: str = "", client_dir: Path | None = None) -> None:
        """Bring the stack up. Refuses while somebody else's container holds one of its ports.

        `account` and `client_dir` are the process runtime's (`SingleServer`): a stack's
        client logs in through its loader, at its own prompt.
        """
        ours = set(self.entry.containers())
        held = [
            name for name in self._seams.port_conflicts(self.entry.host_ports()) if name not in ours
        ]
        if held:
            raise SingleServerError(
                f"{self.entry.name} needs ports "
                f"{', '.join(str(p) for p in self.entry.host_ports())}, and "
                f"{', '.join(sorted(held))} is running on one of them. Stop it first."
            )
        try:
            self._seams.up(self.server_dir)
        except docker.DockerCommandError as exc:
            raise SingleServerError(f"{self.entry.name} could not be started: {exc}") from exc
        logger.info(f"{self.entry.id} started from {self.server_dir}")

    def stop(self) -> bool:
        """Stop the stack if it is ours and running; False when there was nothing to stop.

        Never stops containers of our names that another install's project owns: two
        folders of one game share the names, and the running one may be the other's.
        """
        if self.status() == STOPPED:
            return False
        if not self.is_ours():
            raise SingleServerError(
                f"The running {self.entry.name} server was not started from {self.server_dir}, "
                "so it was left running. Stop it from the tab of the folder it belongs to."
            )
        try:
            self._seams.stop(self.server_dir, timeout=STOP_TIMEOUT_SECONDS)
        except docker.DockerCommandError as exc:
            raise SingleServerError(f"{self.entry.name} could not be stopped: {exc}") from exc
        logger.info(f"{self.entry.id} stopped in {self.server_dir}")
        return True

    def follow_logs(self, cancel: threading.Event | None = None) -> Iterator[str]:
        """Every listener's log, live, for the tab's console (`docker compose logs -f`).

        `cancel` is not read (`follows_with_cancel` is False): the panel ends the child.
        """
        return self._seams.follow(self.server_dir, [s.service for s in self.stack.listeners])

    def session_account(self) -> str | None:
        """None: a stack server is not started for one account."""
        return None

    def advertised(self) -> str | None:
        """None: a stack publishes on this computer only, and keeps no address file."""
        return None

    def advertise(self, address: str) -> bool:
        """Refused: there is no address file to write (`entry.advertise` is None)."""
        raise SingleServerError(
            f"{self.entry.name} takes players on this computer only, so there is no address "
            "to change."
        )
