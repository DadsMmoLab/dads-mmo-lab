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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import docker, wsl
from yulon.catalog import composegen, native, time_zone, world_data
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.log import get_logger
from yulon.said import SaidByYulon

logger = get_logger(__name__)

# How long `start()` waits for the database between starting it and starting
# auth/world. Shorter than a first-boot import (that path goes through
# `compose up` instead): this is a restart of containers that already exist.
_START_DB_HEALTH_TIMEOUT = 120.0


def _catalog_specs() -> tuple[docker.ContainerSpec, ...]:
    """Every catalogue game's `ContainerSpec`, for naming another install's containers (T158)."""
    return tuple(entry.container_spec() for entry in load_catalog().games)


def _entry_for(spec: docker.ContainerSpec) -> CatalogEntry | None:
    """The catalogue game whose containers these are, by the world's and the login's names."""
    for entry in load_catalog().games:
        own = entry.container_spec()
        if own.world == spec.world and own.auth == spec.auth:
            return entry
    return None


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
    distro: wsl.DistroState | None = None
    """What WSL's listing said about this install's distro on this poll; None on this host.

    The tab's readings wait until it says `running` (T133): reading the
    distro's folder starts it, and `stopped` and `unknown` alike are no
    permission. Nothing is up unless it is `running`.
    """

    @property
    def any_running(self) -> bool:
        """True if at least one of the install's containers is up."""
        return self.db or self.auth or self.world

    @property
    def all_running(self) -> bool:
        """True only if the whole install (db + auth + world) is up."""
        return self.db and self.auth and self.world


class StartRefused(RuntimeError, SaidByYulon):
    """Raised by `Controller.start()` when the install's `start_guard` says it must not start.

    T179: a Centurion server whose last update did not finish importing its world
    tables. The message is the sentence the player reads; nothing was started or stopped.
    """


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
        pre_stop: Callable[[], object] | None = None,
        start_guard: Callable[[], str | None] | None = None,
    ) -> None:
        self.spec = spec
        self.server_dir = server_dir
        # Why this install must not be started now, or None (T179): asked first by
        # `start()` and `stop_conflicting_and_start()`, the one door every Start,
        # Start and play, the launcher's PLAY, Restart and recreate goes through.
        # Composed, like `import_probe`: the reason is a family's fact.
        self.start_guard = start_guard
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
        # Runs immediately before anything that ends this install's containers.
        # A plain callable, so this class does not learn where a log snapshot
        # goes or that `yulon.logsnap` exists; what it does learn is the one
        # thing only it knows — the moment before the evidence is destroyed.
        self.pre_stop = pre_stop
        # The T132 hold this controller made or found, if any. None means "not
        # known to be held", which the status poll reads as a reason to hold
        # a world it sees running (`_keep_the_distro_up()`).
        self._hold: wsl.Hold | None = None
        # A release the distro refused (T132, Codex final pass): owed until one
        # works, so a later Stop or Remove retries it even when it finds
        # nothing of this install running. Separate from `_hold`, which the
        # status poll forgets the moment it sees the world down.
        self._release_owed = False
        # How a stop of this install is heard and steered while it waits for
        # a loading world (T158, `docker.StopControl`): its sentences, "Stop
        # now anyway", and the app's own give-up at exit. Set by the tab that
        # shows this install, after both exist; its `say` is called on the
        # stop's worker thread, so it must be something that can cross threads.
        self.stop_control: docker.StopControl | None = None
        # What the last `start()` could not put right about the server's time
        # zone (T171): `None` when nothing. Read by the tab after any job that
        # starts, since `start()` is the one door every Start, Restart and
        # recreate goes through.
        self.zone_problem: str | None = None
        # What the last `start()` could not do about a Windows Centurion world's copy of
        # the map data (T219, `world_data.refresh()`): `None` when nothing. Read by the
        # tab beside `zone_problem`.
        self.world_data_problem: str | None = None
        # The catalog entry this install is, where the subclass knows it (T179).
        # `None` reads it off the shipped catalog by container names
        # (`_entry_for`), which every game but one in the making can answer.
        self.entry: CatalogEntry | None = None

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
        distro = wsl.distro_state(self.wsl_distro) if self.wsl_distro is not None else None
        if distro not in (None, "running"):
            # Asking docker anything inside a distro STARTS that distro, and
            # this runs on a five-second timer - so an adopted server would boot
            # its distro simply by opening the app. Nothing is running when the
            # distro is down, so the empty answer is true rather than merely
            # convenient; Start still starts it, because that is asked for. A
            # listing that did not answer is treated the same (T133 review),
            # and the tab is told which it was.
            logger.debug(f"{self.wsl_distro} is {distro}; reporting nothing up")
            return InstallStatus(db=False, auth=False, world=False, distro=distro)
        running = set(docker.status(wsl_distro=self.wsl_distro))
        status = InstallStatus(
            db=self.spec.db in running,
            auth=self.spec.auth in running,
            world=self.spec.world in running,
            distro=distro,
        )
        self._keep_the_distro_up(world_running=status.world)
        return status

    def _keep_the_distro_up(self, *, world_running: bool) -> None:
        """Hold the distro of a world this poll SAW running, once, and again if the hold went.

        Start is not the only way a WSL world comes to be running: it was up
        before Yu'lon opened (started by an earlier launch whose hold died, by
        hand, or by `restart: unless-stopped` when something booted the
        distro), and a world nobody holds dies 15-25 s after this app closes
        -- the T132 failure, reached without pressing Start (review, Codex).

        Only from a poll that already found the world up, which it can only do
        in a distro that `status()` saw running and asked: nothing here starts
        a distro (§2). At most one `wsl.hold()` per install per session while
        the hold is alive; the flock makes a repeat a no-op anyway. A world seen
        down forgets the hold, so the next time it is seen up it is held again.
        """
        if self.wsl_distro is None:
            return
        if not world_running:
            self._hold = None
            return
        if self._hold is not None and (self._hold.alive() or not self._hold.held):
            # Alive: nothing to do. Never held (no flock in the distro, say):
            # asking again every five seconds would spawn a failing wsl.exe per
            # poll, so it waits for the world to be seen down and up again.
            return
        self._hold = wsl.hold(self.wsl_distro, self.spec.world)

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
            StartRefused: `start_guard` gave a reason. Nothing is started.
        """
        self.refuse_start()
        conflicts = self.port_conflicts()
        if conflicts:
            logger.warning(f"start() refused: ports {self.spec.ports} bound by {conflicts}")
            raise PortConflictError(conflicts, self.spec.ports, self._owners_of(conflicts))
        self.zone_problem = self._put_back_the_zone_file()
        # The map-data fingerprint was written by `refuse_start()` above (T219).
        # No `wait_healthy` closure: `start_staged()` deleted the argument on
        # entry, so the lambda that used to be built here was dead code reading
        # like a health wait that no longer happens. Compose does the waiting
        # now, through the project's own `service_healthy` conditions.
        docker.start_staged(self.spec, self.server_dir, wsl_distro=self.wsl_distro)
        if self.wsl_distro is not None:
            # AFTER the start, so a start that failed pins nothing. The distro
            # would otherwise stop 15-25 s after this app's last call into it,
            # killing the server it just started (T132, `wsl.hold()`).
            self._hold = wsl.hold(self.wsl_distro, self.spec.world)

    def _put_back_the_zone_file(self) -> str | None:
        """T171: a CMaNGOS server's zone file, put back before every start; the warning if not.

        The images carry no zone files, so a `zoneinfo/<Area>/<City>` deleted,
        cut short or left from an older Yu'lon would start the server on UTC
        without a word. `time_zone.refresh()` copies it again only when the
        bytes differ. A failure never stops the Start: the server comes up on
        UTC and the sentence says so. WotLK's image has its own tzdata, so
        nothing is done there, and a game this catalogue does not know is left
        alone.
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None or not time_zone.needs_files(entry):
            return None
        try:
            with (self.server_dir / composegen.OVERRIDE_FILE).open(
                encoding="utf-8", newline=""
            ) as handle:
                override = handle.read()
        except (OSError, UnicodeDecodeError):
            return None  # no override to name a zone; compose says what is wrong with it
        return time_zone.refresh(entry, self.server_dir, override)

    def _refresh_world_data(self) -> str | None:
        """T219: the map-data fingerprint a Windows Centurion world copies by, before every start.

        `world_data.refresh()` writes it only on an install whose compose file declares
        the `world-data` volume, and only when it changed. A failure never stops the
        Start; its sentence is returned for the tab to show -- except one that leaves a
        stale fingerprint in place, which refuses the Start (`StartRefused`).
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None:
            return None
        try:
            return world_data.refresh(entry, self.server_dir)
        except world_data.FingerprintNotRecorded as exc:
            raise StartRefused(str(exc)) from exc

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
        self._fresh_stop()
        # The catalogue's specs, so a blocker that is another game's world is
        # waited for if it is still loading (T158): a server started a minute
        # ago is the likeliest one to be holding the ports.
        docker.stop_containers(
            to_stop, wsl_distro=self.wsl_distro, known=_catalog_specs(), control=self.stop_control
        )
        if self.wsl_distro is not None:
            # That server's hold is keyed by ITS world container, which is one
            # of these and cannot be told from the others by name alone; a key
            # nobody held releases nothing, so every stopped name is offered.
            if not wsl.release(self.wsl_distro, *to_stop):
                logger.warning(
                    f"the hold that kept {self.wsl_distro} open for {to_stop} may still be in place"
                )
        return to_stop

    def refuse_start(self) -> None:
        """Raise `StartRefused` when the folder or `start_guard` gives a reason; before any stop.

        Called by `start()` itself, and first by every press that would stop
        something on the way to a start (Restart, recreate, "stop the other
        server"), so a refused start leaves everything as it was.
        """
        # T197 fix round 2: a press left this folder in a state no start may run on
        # (`native.START_REFUSED_FILE`), on every family, before the family's own guard.
        reason = native.owed_start_refusal(self.server_dir)
        if not reason and self.start_guard is not None:
            reason = self.start_guard()
        if reason:
            logger.warning(f"start() refused: {reason}")
            raise StartRefused(reason)
        # T219: here and not in `start()`, so a fingerprint that can be neither written
        # nor removed refuses BEFORE a Restart's stop, a Recreate's removal or "stop the
        # other server" (cold review of 2a70b82c). Safe while the world runs: a running
        # Windows world reads the volume, never this file. Written only when it changed.
        self.world_data_problem = self._refresh_world_data()

    def stop_conflicting_and_start(self) -> list[str]:
        """Stop the server holding our ports, then start this one."""
        self.refuse_start()
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
        if self.wsl_distro is not None and wsl.known_stopped(self.wsl_distro):
            # `status()`'s reason: asking docker in a distro STARTS it, and
            # nothing runs in a distro that is down. A removal stops first
            # every time (T95), so without this it would boot the distro to
            # learn there was nothing to stop. Only when WSL SAID it is down:
            # a listing that did not answer falls through to the real stop,
            # because a skipped stop leaves a server running (T95 re-review).
            logger.debug(f"{self.wsl_distro} is not running; nothing to stop")
            return False
        self._save_evidence()
        self._fresh_stop()
        stopped = docker.stop_staged(
            self.spec, self.server_dir, wsl_distro=self.wsl_distro, control=self.stop_control
        )
        self._let_the_distro_go(stopped)
        return stopped

    def remove(self) -> bool:
        """Stop the install and remove its containers, keeping every volume.

        The deliberate teardown for a project that needs recreating rather than
        restarting. Characters are not at risk: the database is a named volume
        and `docker.remove_staged()` never passes `-v`.

        Returns:
            True if this install had containers and they are now gone, False if
            there was nothing of it to remove.
        """
        self._save_evidence()
        self._fresh_stop()
        removed = docker.remove_staged(
            self.spec, self.server_dir, wsl_distro=self.wsl_distro, control=self.stop_control
        )
        self._let_the_distro_go(removed)
        return removed

    def _fresh_stop(self) -> None:
        """Forget a "Stop now anyway" pressed for an earlier stop, as this one begins (T158).

        The wait never clears the event itself -- a rebuild's rollback hands it
        events that are ALREADY set on purpose -- so the owner of the event does,
        at the start of each of its own stops.
        """
        if self.stop_control is not None:
            self.stop_control.anyway.clear()

    def _let_the_distro_go(self, ours_went_down: bool) -> None:
        """End the hold `start()` put on this server's distro, once the server is down.

        Only when something of THIS install was stopped: two installs of one
        game share container names, so the hold under these names belongs to
        whichever of them is running, and Stop pressed on the other tab found
        nothing of its own and must not let that distro go (T132). Or when an
        earlier release from this controller was refused: that one is still
        owed, whatever this stop found.
        """
        if self.wsl_distro is None or not (ours_went_down or self._release_owed):
            return
        self._release_owed = not wsl.release(self.wsl_distro, self.spec.world)
        if not self._release_owed:
            self._hold = None

    def _save_evidence(self) -> None:
        """Run the pre-stop hook, and never let it stand between a user and a stop.

        Every exception is swallowed on purpose, including the ones a lint rule
        would rather see narrowed. The hook's own module already answers its
        expected failures with a message instead of raising; what is caught here
        is the unexpected — and the alternative to catching it is a Stop button
        that does nothing because collecting a log file went wrong.
        """
        if self.pre_stop is None:
            return
        try:
            self.pre_stop()
        except Exception as exc:  # noqa: BLE001 - a stop must not depend on evidence
            logger.warning(f"the pre-stop hook failed, stopping anyway: {exc}")

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
            raise docker.DockerRefusal(
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
        """Poll until auth+world are up and ready. `kwargs` forward timeout/interval.

        `timeout` is a QUIET budget here, as it is everywhere else in this app:
        how long the world server may print nothing new, restarted every time it
        prints, bounded by `native.management_ceiling()`. It was a fixed total
        wall clock until 2026-09-05 — this call spent `ReadySpec`'s 480 seconds
        once — and a fixed total is the reading the 2026-09-04 incident
        disproved: a CMaNGOS world server took 46.0 minutes to its first
        `Avg Diff:` on a 9p share while printing the whole way, and 480 seconds
        would have called that a dead server five times over. Nothing about
        AzerothCore makes it immune; the mount is what was slow.
        """
        ready = docker.azerothcore_ready(realm_host, realm_port, **kwargs)
        return native.wait_ready_quietly(self.spec, ready, wsl_distro=self.wsl_distro)
