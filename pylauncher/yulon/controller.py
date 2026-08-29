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

from yulon import docker, wsl
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

    def __init__(
        self,
        containers: list[str],
        ports: tuple[int, ...],
        owners: dict[str, str | None] | None = None,
    ) -> None:
        self.containers = containers
        self.ports = ports
        # Container name -> the directory its compose project was brought up
        # from, so the offer to stop it can say WHICH install that is. Optional
        # because the ports can be held by something that is not compose at all.
        self.owners = owners or {}
        joined = ", ".join(containers)
        super().__init__(
            f"cannot start: port(s) {ports} already bound by running container(s): {joined}"
        )

    def owner_summary(self) -> str:
        """One line naming the install to stop, for a person rather than a log."""
        dirs = sorted({d for d in self.owners.values() if d and d != docker.UNREADABLE})
        if len(dirs) == 1:
            return f"the install in {dirs[0]}"
        if dirs:
            return "the installs in " + ", ".join(dirs)
        return "another server"


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

    def __init__(
        self,
        spec: docker.ContainerSpec,
        server_dir: Path,
        *,
        wsl_distro: str | None = None,
        import_probe: docker.ImportProbe | None = None,
        reset_unfinished: docker.ResetUnfinished | None = None,
    ) -> None:
        self.spec = spec
        self.server_dir = server_dir
        # The WSL2 distro this server lives inside, if it does. Every docker
        # command this controller issues carries it, because a server inside a
        # distro is reached by that distro's own docker - and asking the wrong
        # daemon does not fail, it answers "no containers" and the server reads
        # as stopped while it is running. See `pyplan/wsl-resident-servers.md`.
        self.wsl_distro = wsl_distro
        # Composed, not inherited, and optional: asking a database what state it
        # is in needs a SQL client and per-game schema names, neither of which
        # this class may know (style-guide §3). A controller built without one
        # simply never offers the repair — see `import_state()`.
        self.import_probe = import_probe
        # Optional, and separate from the probe: without it `repair_import()`
        # refuses a half-written database instead of making it unimportable.
        self.reset_unfinished = reset_unfinished

    # -- queries ---------------------------------------------------------

    def status(self) -> InstallStatus:
        """Report which containers carrying this install's names are running.

        By NAME, and deliberately so — an ownership-filtered version of this was
        written and reverted the same day. It read `.env` and filtered
        `docker ps` by the compose project label, which sounds strictly better
        and was worse in three ways (review, 2026-08-22):

        * An unpinned install (every one adopted through "Use existing…") fell
          back to `docker ps` with an `or []`, so a daemon that would not answer
          read as "everything is down" — measured, with the Stop button then
          disabled while the server was serving.
        * A pinned install whose `.env` disagreed with the containers showed
          "down" and disabled Stop, which is the only button that produces the
          explanation of *why* they disagree. A live server, reported down, with
          no way to act and nothing on screen.
        * It was the source of truth moving from `docker ps` to a file that can
          be copied and hand-edited.

        Names are honest about what they are: proof that something is using
        these names, not proof it is ours. `stop_staged()` is where ownership is
        established, because that is where acting on the wrong container does
        damage, and its refusal is now shown on the tab.
        """
        if self.wsl_distro is not None and not wsl.is_running(self.wsl_distro):
            # Asking docker anything inside a distro STARTS that distro, and
            # this runs on a five-second timer - so an adopted server would boot
            # its distro simply by opening the app. Nothing is running when the
            # distro is down, so the empty answer is true rather than merely
            # convenient; Start still starts it, because that is asked for.
            logger.debug(f"{self.wsl_distro} is not running; reporting nothing up")
            return InstallStatus(db=False, auth=False, world=False)
        running = set(docker.status(wsl_distro=self.wsl_distro))
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

        By name, for the reasons in `status()`, and for one more of its own: the
        ownership-filtered version needed a second `docker ps`, and a single
        blip on either of them made Start refuse with "another server is already
        using ports (3724, 8085): ac-authserver, ac-worldserver" — naming the
        user's own containers (review, 2026-08-22).

        The known limit, stated rather than papered over: with two installs of
        one game the other install's containers wear these same names and are
        excused here, so this guard cannot catch that collision. `compose up`
        then reports the daemon's own "container name is already in use".
        """
        own = {self.spec.db, self.spec.auth, self.spec.world}
        return [
            name
            for name in docker.port_conflicts_for(self.spec, wsl_distro=self.wsl_distro)
            if name not in own
        ]

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
            raise PortConflictError(conflicts, self.spec.ports, self._owners_of(conflicts))
        # No `wait_healthy` closure: `start_staged()` deleted the argument on
        # entry, so the lambda that used to be built here was dead code reading
        # like a health wait that no longer happens. Compose does the waiting
        # now, through the project's own `service_healthy` conditions.
        docker.start_staged(self.spec, self.server_dir, wsl_distro=self.wsl_distro)

    def _owners_of(self, containers: list[str]) -> dict[str, str | None]:
        """Where each blocking container came from, best effort and never fatal."""
        owners: dict[str, str | None] = {}
        for name in containers:
            try:
                owners[name] = docker.container_working_dir(name, wsl_distro=self.wsl_distro)
            except docker.DockerCommandError:
                owners[name] = None
        return owners

    def stop_conflicting(self) -> list[str]:
        """Stop the SERVER holding our ports - all of it - and say what was stopped.

        Every v1 server publishes the same ports, so only one can be live at a
        time. Both guards used to stop at "no" and leave the user to go and find
        the other install themselves; this is the doing half of the offer.

        The unit stopped is the compose PROJECT, not the set of containers that
        happen to publish the colliding ports. Stopping only those leaves the
        rest of that install running against a stack that is no longer there:
        measured on yulon-fedora, 2026-08-29, stopping `ac-authserver` and
        `ac-database` left `ac-worldserver` up with its database gone from under
        it, and `restart: unless-stopped` looped it - RestartCount 18 and
        climbing. It published only 8085 and 7878, so it was correctly not a
        blocker, and just as correctly part of the same server.

        A blocker carrying no compose project is stopped alone, because there is
        nothing to widen to; one whose project cannot be read is treated the same
        way rather than being skipped, since an unreadable owner is not a reason
        to leave a port held.

        Containers are stopped BY NAME, not with `compose down`: stopping is
        reversible and keeps the other install's containers, so its next start
        is still the staged one that does not re-run its database import.
        """
        conflicts = self.port_conflicts()
        if not conflicts:
            return []
        to_stop: list[str] = []
        for name in conflicts:
            project = docker.container_project(name, wsl_distro=self.wsl_distro)
            siblings: list[str] | None = None
            if project and project != docker.UNREADABLE:
                siblings = docker.project_containers(project, wsl_distro=self.wsl_distro)
            for candidate in siblings if siblings is not None else [name]:
                if candidate not in to_stop:
                    to_stop.append(candidate)
        logger.info(f"stopping the server(s) holding {self.spec.ports}: {to_stop}")
        docker.stop_containers(to_stop, wsl_distro=self.wsl_distro)
        return to_stop

    def stop_conflicting_and_start(self) -> list[str]:
        """Stop the server holding our ports, then start this one."""
        stopped = self.stop_conflicting()
        self.start()
        return stopped

    def stop(self) -> bool:
        """Stop the install, keeping its containers so the next start is staged.

        Uses `docker.stop_staged()`, which keeps the containers so the next
        start reuses them instead of recreating them.

        An earlier version of this docstring said removing them would put the
        next start "back on `compose up -d` and re-running the one-shot database
        import". That has not been true since `start_staged()` began naming its
        three services explicitly: it selects `db auth world` with `--no-deps`,
        so compose cannot reach `ac-db-import` even when the containers are
        gone. Keeping them is now a matter of speed, not of safety, and saying
        otherwise made the safe action look dangerous (2026-08-23). Teardown
        that really should remove them is `remove()`.

        Returns:
            True if something of this install was running and is now down, False
            if there was nothing to stop. This used to be discarded, so the tab
            said the same thing either way (review, 2026-08-22).
        """
        return docker.stop_staged(self.spec, self.server_dir, wsl_distro=self.wsl_distro)

    def remove(self) -> bool:
        """Stop the install and remove its containers, keeping every volume.

        The deliberate teardown for a project that needs recreating rather than
        restarting. Characters are not at risk: the database is a named volume
        and `docker.remove_staged()` never passes `-v`.

        Returns:
            True if this install had containers and they are now gone, False if
            there was nothing of it to remove.
        """
        return docker.remove_staged(self.spec, self.server_dir, wsl_distro=self.wsl_distro)

    def import_state(self) -> docker.ImportState:
        """Ask this install's databases whether the one-shot import ever finished.

        Never raises: a probe that cannot reach the database answers
        `unreadable`, and so does a controller built without one. The caller is
        a five-second status path and a button's visibility, and neither has
        anywhere useful to put an exception — while `unreadable` is not
        `repairable`, so the destructive action stays hidden either way.
        """
        if self.import_probe is None:
            return docker.ImportState(
                "unreadable", "this game has no way to ask its databases what state they are in"
            )
        return self.import_probe()

    def repair_import(self, output: docker.OutputSink | None = None) -> bool:
        """Re-run the one-shot database import. Only for an install broken before it ran.

        See `docker.repair_import()` for every refusal, in particular the one
        that matters: a database holding accounts or characters is never
        re-imported, however many times the button is pressed.

        `output` receives the import's own lines as they arrive, which is the
        only thing that distinguishes a 30-minute import from a hang. It is
        called on the thread this runs on — a worker thread in the app — so a
        caller in the UI layer hands in something that can cross threads rather
        than something that touches a widget.

        Raises:
            docker.DockerCommandError: any of those refusals, or an import that
                ran and left the databases exactly as unimported as they were.
        """
        if self.import_probe is None:
            raise docker.DockerCommandError(
                "this game cannot be asked what state its databases are in, so its import will "
                "not be re-run — an import that cannot be checked afterwards is a guess."
            )
        return docker.repair_import(
            self.spec,
            self.server_dir,
            self.import_probe,
            reset=self.reset_unfinished,
            output=output,
            wsl_distro=self.wsl_distro,
        )

    # -- polling ---------------------------------------------------------

    def wait_db_healthy(self, **kwargs: float) -> bool:
        """Poll until the DB container is healthy. `kwargs` forward timeout/interval."""
        return docker.wait_db_healthy_for(self.spec, wsl_distro=self.wsl_distro, **kwargs)

    def wait_ready(self, realm_host: str, realm_port: int, **kwargs: float) -> bool:
        """Poll until auth+world are up and ready. `kwargs` forward timeout/interval."""
        return docker.wait_ready_for(
            self.spec, realm_host, realm_port, wsl_distro=self.wsl_distro, **kwargs
        )
