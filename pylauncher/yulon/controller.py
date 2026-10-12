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

import contextlib
import dataclasses
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from yulon import (
    database_presence,
    docker,
    forgetting,
    platform,
    playerbots_rename,
    seeded_accounts,
    server_build_gone,
    wsl,
)
from yulon.catalog import composegen, installer, native, preflight, time_zone, world_data
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.log import get_logger
from yulon.said import SaidByYulon

logger = get_logger(__name__)

# How long `start()` waits for the database between starting it and starting
# auth/world. Shorter than a first-boot import (that path goes through
# `compose up` instead): this is a restart of containers that already exist.
_START_DB_HEALTH_TIMEOUT = 120.0

_REBIND_PAUSE = 1.0
"""How long Start's port probe waits before it binds a refused port a second time (T671).

A server stopped a moment ago (a Restart's own stop, "Stop the other server") may
not have handed its listener back yet; a port still taken after this is taken."""

PRESS_RESTART = "Restart the server"
"""How `Controller.restart()`'s reservation names it to another Yu'lon (T761)."""

PRESS_RECREATE = "Remove the server's containers"
"""How `Controller.recreate()`'s reservation names it to another Yu'lon (T761)."""


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


class PortTaken(StartRefused):
    """A Start refused because a host port this server publishes cannot be bound (T671).

    Found by binding each port no container publishes BEFORE anything starts, so the
    refusal leaves nothing half up. The message names the port, what it is for and,
    where the OS says, the program holding it; `detail` is the bind's own answer.
    """

    def __init__(self, message: str, *, detail: str) -> None:
        super().__init__(message)
        self.detail = detail


def bind_host_port(host: str, port: int) -> platform.PortBind:
    """Bind `host:port` and let go: Start's probe of a port no container holds (T671)."""
    return platform.bind_tcp(host, port)


class ComposeTooOld(StartRefused):
    """A Start refused because this machine's Docker Compose cannot run it (T658).

    `offer`: `platform.update_compose()` can put a current one in place here, so the
    Server tab shows **Update Docker Compose** beside the sentence.
    """

    def __init__(self, message: str, *, offer: bool) -> None:
        super().__init__(message)
        self.offer = offer


class ImportEnded(StartRefused):
    """A Start refused because this server's last database import was cut off (T658).

    `docker.one_shot_ended_marker()` is there and the databases read as imported or
    half-written: they are unfinished whatever their tables say. `state` is the
    reading the Server tab offers Repair on.
    """

    def __init__(self, message: str, *, state: docker.ImportState) -> None:
        super().__init__(message)
        self.state = state


ENDED_IMPORT_DETAIL = (
    "an earlier import was cut off before it finished, so these databases are unfinished "
    "whatever their tables say"
)
"""The import state's detail while `docker.one_shot_ended_marker()` stands (T658)."""

IMPORT_ENDED = (
    "This server's last database import was cut off before it finished, so its databases are "
    "unfinished. Nothing was started. Press **Repair** to clear them and import again."
)

UNFINISHED_IMPORT_WROTE_DETAIL = (
    "the first database import did not finish, and what it wrote before it stopped is "
    "cleared and imported again"
)
"""The import state's detail when that import left rows (T678 review round 2)."""

FIRST_IMPORT_UNFINISHED = (
    "This server's first database import did not finish, so its databases are unfinished, "
    "whatever rows it wrote before it stopped. Nothing was started. Press **Repair** to clear "
    "what it wrote and import again."
)
"""Start's refusal for that case (owner decision, 2026-10-11: refuse and offer a clean redo)."""


def unfinished_import_wrote(state: docker.ImportState) -> bool:
    """Whether `Controller.import_state()` read rows an unfinished first import wrote (T678)."""
    return state.detail.startswith(UNFINISHED_IMPORT_WROTE_DETAIL)


class DatabaseMissing(StartRefused):
    """Raised by `Controller.start()` when Docker no longer has this server's database (T377).

    Its volume is gone, or holds no login database: starting would put the
    server on an empty one. The message is `database_presence.MISSING`; nothing
    was started, and nothing is imported until the player presses Repair.
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
        before_servers: Callable[[], str | None] | None = None,
        safety_copy: Callable[[], str] | None = None,
    ) -> None:
        self.spec = spec
        # T678 review round 3: dumps the core databases and says where, before Repair
        # clears what an unfinished first import wrote; that clear is refused without it.
        self.safety_copy = safety_copy
        # A family's last word before its servers start, asked once every
        # refusal has passed -- the port check and the missing database (T377)
        # included, so it may write to the database without recreating one
        # Docker lost (T552, Codex review). A reason refuses the start like
        # `start_guard`'s. Also asked by `refuse_before_a_stop()`, so a Restart
        # or Recreate cannot reach the servers without it.
        self.before_servers = before_servers
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
        # The line the last `start()` said when it renamed the server's bot settings to
        # the prefix its mod-playerbots reads (T657): `None` when there was nothing to do.
        self.bot_settings_renamed: str | None = None
        # The line the last `start()` said about CMaNGOS's built-in accounts (T668): they were
        # locked, or could not be and still log in with their own name. `None` when nothing.
        self.seeded_accounts_locked: str | None = None
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

    def host_ports(self) -> list[tuple[str, int, str]]:
        """`(address, port, what)` for every host port this server publishes (T671).

        The install preflight's own list (`preflight.bind_targets()`): auth, world,
        SOAP or command channel, and the database, with a port `.env` moves (the
        port-conflict remedy's `DOCKER_DB_EXTERNAL_PORT`) moved with it. Every
        database but WoW Unbound's publishes 3306, so a database another server
        left up alone blocks this one as surely as its world does. A game the
        catalog does not know is its `ContainerSpec.ports`, as before.
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None:
            return [("0.0.0.0", port, "") for port in self.spec.ports]
        targets, problems = preflight.bind_targets(entry, self.server_dir)
        for problem in problems:
            logger.info(f"port check: {problem}")
        return targets

    def _port_numbers(self) -> tuple[int, ...]:
        return tuple(dict.fromkeys(port for _host, port, _what in self.host_ports()))

    def port_conflicts(self) -> list[str]:
        """Return *foreign* running containers binding this install's ports.

        Every host port it publishes (`host_ports()`), the database's too (T671):
        another server's database running alone is a conflict, and the one
        "Stop the other server" has to stop.

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
        every = dataclasses.replace(self.spec, ports=self._port_numbers())
        return [
            name
            for name in docker.port_conflicts_for(every, wsl_distro=self.wsl_distro)
            if name not in own
        ]

    def _ports_taken_by(self, containers: list[str]) -> tuple[int, ...]:
        """The ports of ours that `containers` publish, for the sentence; all of them if unread."""
        ports = self._port_numbers()
        try:
            published = docker.port_publishers(ports, wsl_distro=self.wsl_distro)
        except docker.DockerCommandError:
            return ports
        taken = tuple(p for p in ports if set(published.get(p, ())) & set(containers))
        return taken or ports

    def refuse_a_taken_port(self) -> None:
        """Raise `PortTaken` when the OS will not let a port of this server be bound (T671).

        Asked before anything starts, so a refusal leaves nothing half up: compose
        used to start the database and world and then fail on the login port. Each
        port is bound and let go (`bind_host_port()`, the install preflight's own
        probe); one a container publishes is Docker's, not a program's, and is left
        alone (a foreign container was refused by name already). A refused port is
        bound once more after `_REBIND_PAUSE`. The sentence names the port, what it
        is for and, where the OS says, the program holding it.

        A server inside a WSL distro publishes inside that distro: a loopback port
        there (the database, SOAP, the channel) is the distro's own and is not this
        host's to bind, so a Windows program on 3306 never stopped such a server and
        is not asked about. Its auth and world ports, on every interface, still are
        (cold review of T671). A Docker that cannot say who publishes a refused port
        is "cannot tell": that port refuses nothing here, and compose says the rest.
        """
        windows = platform.detect() == "windows"
        published: dict[int, tuple[str, ...]] | None = None
        targets = self.host_ports()
        if self.wsl_distro is not None:
            targets = [t for t in targets if t[0] != preflight.LOOPBACK]
        for host, port, what in targets:
            got = bind_host_port(host, port)
            if got.status not in ("in_use", "reserved"):
                continue
            if published is None:
                try:
                    published = docker.port_publishers(
                        [p for _h, p, _w in targets], wsl_distro=self.wsl_distro
                    )
                except docker.DockerCommandError as exc:
                    logger.info(f"port check: cannot tell who holds {port}: {exc}")
                    return
            if port in published:
                continue
            time.sleep(_REBIND_PAUSE)
            got = bind_host_port(host, port)
            if got.status not in ("in_use", "reserved"):
                continue
            program = platform.listening_program(port) if got.status == "in_use" else None
            sentence = docker.blocked_port_sentence(
                port, got.status, windows=windows, what=what, program=program or ""
            )
            logger.warning(f"start() refused: binding {host}:{port} answered {got.status}")
            # "bind" and the port on one line: the Server tab's channel rollback reads it.
            raise PortTaken(
                sentence, detail=f"binding {host}:{port} ({what or 'port'}): {got.detail}"
            )

    # -- lifecycle -------------------------------------------------------

    def start(self, *, asked_before_the_servers: bool = False) -> None:
        """Bring the install up, refusing if another install holds our ports.

        Uses `docker.start_staged()`, so restarting an installed server never
        re-runs its one-shot database import (see that function).

        `asked_before_the_servers`: the caller already asked `before_servers` and it
        agreed (`stop_conflicting_and_start()`), so it is not asked a second time (T565).

        Raises:
            PortConflictError: A container that is not part of this install
                already binds one of its host ports (`host_ports()`). Nothing is started.
            PortTaken: A program holds one of them. Nothing is started.
            docker.DockerCommandError: The `docker` CLI itself failed.
            StartRefused: `start_guard` gave a reason. Nothing is started.
            DatabaseMissing: Docker no longer has this server's database
                (T377). Nothing is started, and a database started to ask is
                stopped again.
        """
        # T568: reserved BEFORE the first thing it changes (the database `refuse_a_missing_database`
        # starts, the realm-port UPDATE, Tortoise's realm flag, dashboard and zone file), not
        # only around `start_staged()` at its end: another Yu'lon's job on this server then
        # leaves all of it untouched.
        with docker.lifecycle(
            self.server_dir,
            press=forgetting.PRESS_START,
            spec=self.spec,
            wsl_distro=self.wsl_distro,
        ):
            self.refuse_start()
            self.refuse_an_old_compose()
            conflicts = self.port_conflicts()
            if conflicts:
                taken = self._ports_taken_by(conflicts)
                logger.warning(f"start() refused: ports {taken} bound by {conflicts}")
                raise PortConflictError(conflicts, taken, self._owners_of(conflicts))
            self.refuse_a_taken_port()
            self.bot_settings_renamed = None
            self.seeded_accounts_locked = None
            self.refuse_a_missing_image()
            self.refuse_a_missing_database()
            self.refuse_an_unfinished_install()
            self.refuse_an_ended_import()
            if not asked_before_the_servers:
                self._ask_before_the_servers()
            # T657: after every refusal and the player's own answer, so a Start that does not
            # happen renames nothing and the line is said only when it did. A rename that
            # cannot be written refuses here, with the database `refuse_a_missing_database`
            # started left up, as a refused `before_servers` answer leaves it.
            self.bot_settings_renamed = self._rename_bot_settings()
            # T668: the database is up (`refuse_a_missing_database`); never refuses a start.
            self.seeded_accounts_locked = self._lock_seeded_accounts()
            self._before_the_servers_start()
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

    def _rename_bot_settings(self) -> str | None:
        """T657: the bot settings under the prefix this server's mod-playerbots reads.

        Raises:
            StartRefused: they could not be renamed; nothing is started.
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None:
            return None
        try:
            return playerbots_rename.settle(entry, self.server_dir)
        except playerbots_rename.RenameRefused as exc:
            logger.warning(f"start() refused: {exc}")
            raise StartRefused(str(exc)) from exc

    def _lock_seeded_accounts(self) -> str | None:
        """T668: CMaNGOS's four built-in accounts given random passwords; the line to say or None.

        TBC and Vanilla only (`seeded_accounts.settle()` asks no other core anything), and
        only rows still holding the seeded password. Never raises.
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None:
            return None
        return seeded_accounts.settle(entry, self.spec, self.server_dir, wsl_distro=self.wsl_distro)

    def lock_seeded_accounts_if_up(self) -> str | None:
        """T668: the same lock for a server whose database is already running; the line or None.

        For the app opening on a server Docker restarted by itself. Asks `status()` (the read
        the status poll makes, a stopped WSL distro included) and does nothing while the
        database is down; starts nothing. A note said earlier stays on `seeded_accounts_locked`
        until the next Start. Nothing for a core that does not seed the accounts.

        Raises:
            docker.ServerHeldError: a job or another Yu'lon holds the server (T716); nothing
            was written and the poll asks again.
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None or entry.accounts.scheme != seeded_accounts.SCHEME:
            return None
        if not self.status().db:
            return None
        # T716: a write to the auth database is a press on the server like the Accounts tab's.
        # A job of this process holding it is asked first, so the poll's expected refusal is
        # not logged as a warning every five seconds.
        held = docker.held_reason(self.server_dir)
        if held is not None:
            raise docker.ServerHeldError(held)
        try:
            with docker.server_hold(
                self.server_dir,
                "Locking the built-in accounts",
                spec=self.spec,
                wsl_distro=self.wsl_distro,
            ):
                said = seeded_accounts.settle(
                    entry,
                    self.spec,
                    self.server_dir,
                    wsl_distro=self.wsl_distro,
                    start_database=False,
                )
        except docker.ServerReservationUnavailable as exc:
            # No reservation could be made: not a wait, so the player is told as before.
            logger.warning(f"{entry.id}: the seeded accounts were not locked: {exc}")
            first = str(exc).strip().splitlines()
            said = seeded_accounts.NOT_LOCKED.format(why=first[0] if first else "no reservation")
        if said is not None:
            self.seeded_accounts_locked = said
        elif seeded_accounts.is_not_locked(self.seeded_accounts_locked):
            # T718: an earlier try failed (database not ready); this one found nothing seeded.
            self.seeded_accounts_locked = None
        return said

    def refuse_an_old_compose(self) -> None:
        """Raise `StartRefused` when this machine's Compose is one that stops every Start (T658).

        Compose 2.5.0-2.9.0 refuse `compose up -d --no-deps <db> <auth> <world>`
        with "no such service: <the import>" when the servers depend on an import
        service outside that selection, which is an AzerothCore server's shape only
        (measured, `platform.COMPOSE_OLDEST_WORKING`).
        Asked with one `docker compose version` before anything is changed, so the
        player reads what to update instead of Compose's own words after the realm
        row and the database were touched. A version that cannot be read refuses nothing.
        """
        if not self.spec.import_service:
            # CMaNGOS and TrinityCore start only services whose dependencies start with
            # them, which Compose 2.5-2.9 run fine (measured on m910q, 2026-10-10).
            return
        reason = docker.compose_refusal(wsl_distro=self.wsl_distro)
        if reason:
            logger.warning(f"start() refused: {reason}")
            raise ComposeTooOld(
                reason, offer=platform.compose_update_offered(wsl_distro=self.wsl_distro)
            )

    def refuse_an_unfinished_install(self) -> None:
        """Raise `StartRefused` for a CMaNGOS or Centurion install that did not finish installing.

        T755. "Use existing…" could take a folder whose install was stopped during
        its import, and these games' login tables read `present` long before the
        world is in, so Start ran on a half-imported world. Refused only when Yu'lon's
        own record says the import never finished AND the family's import gate reads
        the databases `absent` or `partial` (`installer.unfinished_install_refusal()`);
        a played world, a finished import or a database that cannot be asked starts as
        before. After `refuse_a_missing_database()`, which has the database up.
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None:
            return
        reason = installer.unfinished_install_refusal(
            entry, self.server_dir, wsl_distro=self.wsl_distro
        )
        if reason:
            logger.warning(f"start() refused: {reason}")
            raise StartRefused(reason)

    def refuse_an_ended_import(self) -> None:
        """Raise `ImportEnded` while this server's last import stands cut off (T658).

        The record alone asks nothing: only with `docker.one_shot_ended_marker()`
        present are the databases asked, after `refuse_a_missing_database()` has
        them up. Player data never refuses here (`populated` is not touched).
        """
        service = self.spec.import_service
        if not service or self.import_probe is None:
            return
        if not docker.one_shot_ended_marker(self.server_dir, service).is_file():
            return
        state = self.import_state()
        if state.detail == ENDED_IMPORT_DETAIL:
            logger.warning(f"start() refused: {IMPORT_ENDED}")
            raise ImportEnded(IMPORT_ENDED, state=state)
        if unfinished_import_wrote(state):
            # T678 review round 2: rows a first import wrote before it died (City Bots'
            # accounts), with the world half-imported; never a player's (see Repair).
            logger.warning(f"start() refused: {FIRST_IMPORT_UNFINISHED}")
            raise ImportEnded(FIRST_IMPORT_UNFINISHED, state=state)

    def refuse_a_missing_image(self) -> None:
        """Raise `StartRefused` when Docker no longer has this server's built image (T627).

        Asked BEFORE anything is started, with one `docker image inspect`: without it
        `compose up` tries to pull `yulon.local/...` from a registry of that name, and
        dies on a name lookup after the database was started and the realm row written.
        Docker not answering is not "gone"; the Start goes on as it did.
        """
        entry = self.entry or _entry_for(self.spec)
        reason = server_build_gone.refusal_before_start(
            entry,
            self.server_dir,
            lambda refs: docker.images_built(refs, wsl_distro=self.wsl_distro),
            wsl_distro=self.wsl_distro,
            services=self.spec.compose_services(),
            compose_images=lambda: docker.compose_service_images(
                self.server_dir, self.spec.compose_services(), wsl_distro=self.wsl_distro
            ),
        )
        if reason:
            logger.warning(f"start() refused: {reason}")
            raise StartRefused(reason)

    def refuse_a_missing_database(self) -> None:
        """Raise `DatabaseMissing` when Docker says this server's database is gone or empty (T377).

        After the port check and before anything starts: the volume is asked of
        Docker first, and only a volume that is there gets its database started
        to be asked whether it holds a login database (`database_presence.take_reading()`).
        A reading Docker could not give is `unknown` and starts the server as
        before -- a slow database is not an empty one. A game this catalogue does
        not know is not asked.
        """
        entry = self.entry or _entry_for(self.spec)
        if entry is None:
            return
        reading = database_presence.take_reading(
            entry, self.server_dir, self.wsl_distro, keep_running=True, spec=self.spec
        )
        if reading.refuses:
            logger.warning(f"start() refused: the database is {reading.presence} ({reading.why})")
            raise DatabaseMissing(database_presence.sentence_for(reading.presence))

    def _before_the_servers_start(self) -> None:
        """What a game does once every refusal has passed and before its servers start.

        Nothing here. Tortoise brings its bot dashboard up (T127), at this point
        so that a refused start -- a missing database included -- starts nothing.
        """

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
        in_the_way = self._servers_in_the_way(to_stop)
        for folder in in_the_way:
            # T671: a database left up alone may be a restore's or a backup's of this
            # process, whose reservation of that folder would let this stop through.
            held = docker.held_reason(folder)
            if held:
                logger.warning(f"not stopping {folder}: {held}")
                raise docker.ServerHeldError(held)
            # T714: a restore load another Yu'lon left running ("Quit anyway") holds nothing
            # here, and only its record says it is still writing.
            loading = native.restore_still_loading(folder)
            if loading:
                logger.warning(f"not stopping {folder}: {loading}")
                raise docker.ServerHeldError(loading)
        logger.info(f"stopping the server(s) holding {self._port_numbers()}: {to_stop}")
        self._fresh_stop()
        # T568: the OTHER server is another Yu'lon's to be working on too. Its folder is
        # reserved for the stop, so a rebuild running there is refused with its holder's
        # sentence, and nothing is stopped.
        with contextlib.ExitStack() as reserved:
            for folder, container in in_the_way.items():
                try:
                    reserved.enter_context(
                        docker.server_claim(
                            folder,
                            press=forgetting.PRESS_STOP,
                            spec=docker.ContainerSpec(
                                db=container, auth=container, world=container, ports=()
                            ),
                            wsl_distro=self.wsl_distro,
                            label=folder.name,
                        )
                    )
                except docker.ServerReservationUnavailable as unavailable:
                    if not unavailable.moot:
                        raise
                    logger.warning(f"stopping {folder} unreserved: {unavailable}")
            # T754: a database import that server runs is ended, and recorded in ITS
            # folder, before the stop below ends it unrecorded.
            self._end_their_imports(to_stop)
            # The catalogue's specs, so a blocker that is another game's world is
            # waited for if it is still loading (T158): a server started a minute
            # ago is the likeliest one to be holding the ports.
            docker.stop_containers(
                to_stop,
                wsl_distro=self.wsl_distro,
                known=_catalog_specs(),
                control=self.stop_control,
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

    def _end_their_imports(self, containers: list[str]) -> None:
        """End each running database import among `containers`, recorded in its folder (T754).

        `docker.stop_containers()` ends an importer with `docker stop` and writes
        nothing, so the other server's next Start ran on half-imported schemas that
        read `imported`. `end_one_shot(record_ended=True)` writes T658's record into
        the folder compose stamped on the importer, where that server's own tab reads
        it, and only for one it finds running: `containers` lists exited ones too.
        Its compose names the container after the service (`ac-db-import`), which is
        how one is told among `containers`. Never raises; a folder this host cannot
        see is logged and the stop goes on.
        """
        importers = {spec.import_service for spec in _catalog_specs() if spec.import_service}
        for name in containers:
            if name not in importers:
                continue
            try:
                working = docker.container_working_dir(name, wsl_distro=self.wsl_distro)
            except docker.DockerCommandError:
                working = None
            folder = self._visible_folder(working)
            if folder is None:
                logger.warning(f"no folder this host can see for {name} ({working!r}); unrecorded")
                continue
            left = docker.end_one_shot(name, folder, wsl_distro=self.wsl_distro, record_ended=True)
            if left is not None:
                logger.warning(f"{name} could not be ended before the stop: {left.reason}")

    def _servers_in_the_way(self, containers: list[str]) -> dict[Path, str]:
        """The server folders (by compose working dir) of `containers`, each with one container.

        A folder this host can see: a path on this host, or -- when this server's Docker lives
        in a WSL distro (`wsl_distro`) -- a Linux path in that distro, read through its
        `\\\\wsl.localhost` share (T607; the other server's Docker is this one's, so its path is
        in this distro). A working dir this host cannot see, or one Docker has no label for, is
        skipped (and logged). So is a folder Yu'lon did not build (no `native.STATE_FILE`): the
        user's own compose project; Yu'lon writes no id file into a folder that is not its own.
        """
        found: dict[Path, str] = {}
        for name in containers:
            try:
                working = docker.container_working_dir(name, wsl_distro=self.wsl_distro)
            except docker.DockerCommandError:
                working = None
            folder = self._visible_folder(working)
            if folder is None:
                logger.info(f"no server folder this host can see for {name} ({working!r})")
                continue
            if folder.resolve() == self.server_dir.resolve():
                continue
            if not (folder / native.STATE_FILE).is_file():
                # Not a server Yu'lon built: the user's own compose project in the way.
                # Stopped as before, unreserved -- no id file written into their folder, and
                # no reservation container run from their image (T568, Opus review).
                logger.info(f"{folder} is not a Yu'lon server; stopping {name} unreserved")
                continue
            found.setdefault(folder, name)
        return found

    def _visible_folder(self, working: str | None) -> Path | None:
        """`working` as a folder this host can read, or None."""
        if not working:
            return None
        direct = Path(working)
        if direct.is_dir():
            return direct
        if self.wsl_distro is not None and working.startswith("/"):
            share = platform.wsl_unc_path(self.wsl_distro, working)
            if share is not None and share.is_dir():
                return share
        return None

    def refuse_start(self) -> None:
        """Raise `StartRefused` when the folder or `start_guard` gives a reason; before any stop.

        Called by `start()` itself, and first by every press that would stop
        something on the way to a start (Restart, recreate, "stop the other
        server"), so a refused start leaves everything as it was.
        """
        # T197 fix round 2: a press left this folder in a state no start may run on
        # (`native.START_REFUSED_FILE`), on every family, before the family's own guard.
        # T225 (live): and a stopped rebuild whose build may have landed since, which
        # asks Docker only while the folder records such a stop.
        reason = native.folder_start_refusal(
            self.server_dir, lambda ref: docker.image_id(ref, wsl_distro=self.wsl_distro)
        )
        if not reason:
            # T217 (a), the owner's decision of 2026-10-05: a source folder a failed
            # update could not put back refuses every start (`native.SOURCES_OFF_FILE`).
            reason = native.sources_off_refusal(self.server_dir)
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

    def refuse_before_a_stop(self) -> None:
        """Every refusal a start would make, asked by a press that stops something first.

        `refuse_start()`, then the image (T627), then the database (T377):
        Restart, Recreate, the bots
        restart and "stop the other server" each stop something before their
        start, and a database Docker no longer has must refuse before that stop,
        not after it. Its world may well be up -- on the empty database compose
        made in place of the lost one -- and a refusal after the stop would leave
        it down while saying nothing was stopped (cold review). `start()` asks
        the database again, after the stop: a second look, never a different rule.
        """
        self.refuse_start()
        self.refuse_an_old_compose()
        self.refuse_a_missing_image()
        self.refuse_a_missing_database()
        self.refuse_an_unfinished_install()
        self.refuse_an_ended_import()
        self._ask_before_the_servers()

    def _ask_before_the_servers(self) -> None:
        """Raise `StartRefused` when `before_servers` gives a reason (T552)."""
        if self.before_servers is None:
            return
        reason = self.before_servers()
        if reason:
            logger.warning(f"start() refused: {reason}")
            raise StartRefused(reason)

    def stop_conflicting_and_start(self) -> list[str]:
        """Stop the server holding our ports, then start this one.

        `before_servers` starts our database, and the server in the way may hold
        that port too, so with something in the way it waits for `start()`, after
        the stop (T552, Codex review). With nothing in the way it is asked first,
        like every press that stops something: a refusal leaves everything as it was.
        """
        # T568: this server is reserved before anything is asked or stopped (see `start()`).
        with docker.lifecycle(
            self.server_dir,
            press=forgetting.PRESS_START,
            spec=self.spec,
            wsl_distro=self.wsl_distro,
        ):
            self.refuse_start()
            self.refuse_an_old_compose()
            self.refuse_a_missing_image()
            self.refuse_a_missing_database()
            self.refuse_an_unfinished_install()
            self.refuse_an_ended_import()
            asked = not self.port_conflicts()
            if asked:
                self._ask_before_the_servers()
            stopped = self.stop_conflicting()
            # A conflict that appeared between the two looks was stopped just now, and the step
            # asked before it ran with that server still holding our ports: ask it again.
            self.start(asked_before_the_servers=asked and not stopped)
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

    def restart(
        self,
        *,
        stopping: Callable[[], None] | None = None,
        stopped: Callable[[], None] | None = None,
    ) -> bool:
        """Stop, then start, as ONE lifecycle command reserved before anything is asked (T761).

        The reservation comes first, as `start()`'s does (T568): `refuse_before_a_stop()`
        starts the database and sends `before_servers` (the realm-port UPDATE), so asked
        outside it, a Restart under another Yu'lon's job wrote to that server before it was
        refused. Then every start refusal, before the stop, so a refusal leaves a running
        world running (T179, T377). One command from the stop to the start (T216 review
        round 3), so a restore cannot take its hold between them.

        `stopping` is called once every refusal has passed, just before the stop; `stopped`
        once the stop has returned, before the start. Both on the calling thread. They tell a
        caller which half raised: neither called, nothing changed; `stopping` only, the stop
        raised and nothing was started; both, the start raised.

        Returns:
            What `stop()` returned: True if something of this install was running.

        Raises:
            docker.ServerHeldError: a job of this process or another Yu'lon holds the
                server; nothing was asked or changed.
            StartRefused, DatabaseMissing, ImportEnded: a start refusal; nothing was stopped.
        """
        with docker.lifecycle(
            self.server_dir, press=PRESS_RESTART, spec=self.spec, wsl_distro=self.wsl_distro
        ):
            self.refuse_before_a_stop()
            if stopping is not None:
                stopping()
            was_up = self.stop()
            if stopped is not None:
                stopped()
            self.start()
        return was_up

    def recreate(self) -> bool:
        """Remove the containers, then start: `restart()` with `remove()` for its stop (T761).

        `remove()` keeps every volume, so the characters are not touched and the start
        creates the containers again with the current environment and conf files.
        Reserved before anything is asked, and one lifecycle command, as `restart()` is:
        a gap between the two would leave the server removed.

        Returns:
            What `remove()` returned: True if this install had containers.

        Raises:
            docker.ServerHeldError: the server is held; nothing was asked or changed.
            StartRefused, DatabaseMissing, ImportEnded: a start refusal; nothing was removed.
        """
        with docker.lifecycle(
            self.server_dir, press=PRESS_RECREATE, spec=self.spec, wsl_distro=self.wsl_distro
        ):
            self.refuse_before_a_stop()
            removed = self.remove()
            self.start()
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
        state = self.import_probe()
        service = self.spec.import_service
        if (
            service
            and state.state in ("imported", "partial")
            and docker.one_shot_ended_marker(self.server_dir, service).is_file()
        ):
            # T658: a cut-off import can read `imported`; it is `partial`, so Repair is offered,
            # and Repair clears it whole (`docker.repair_import()` reads the same record).
            return docker.ImportState("partial", ENDED_IMPORT_DETAIL)
        if (
            service
            and state.state == "populated"
            and docker.import_record_is_from_empty(self.server_dir, service)
        ):
            # T678 review round 2: rows written by a first import that never finished, so
            # Repair is offered; `docker.repair_import()` clears them under the same record.
            return docker.ImportState(
                "partial",
                f"{UNFINISHED_IMPORT_WROTE_DETAIL} ({state.detail})",
                characters=state.characters,
            )
        return state

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
            safety_copy=self.safety_copy,
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
