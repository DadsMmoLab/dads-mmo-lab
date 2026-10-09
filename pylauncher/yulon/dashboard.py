"""One verdict about one install, composed from state the app already has (8.1a).

The bug this closes, verbatim from `pyplan/bug-checklist.md:499`:

    HIGH — A TBC server whose mangosd is stuck in a restart loop is reported as
    a fully successful install, end to end through the GUI.

A crash-looping worldserver is in `docker ps` between its restarts, so every
check that asks "is a container running" answers yes, and the install reports
success while nobody can play. Two facts separate the two states and both come
from the single `docker inspect` this module already pays for: the restart
count, and how long the CURRENT run has lasted.

**Nothing here runs a command on the world thread**, on this timer or any other
(the decision page's "no periodic world-thread command, on any timer, for any
reason"). A tick is one `docker inspect` and, when the server is up, one SQL
read. That is also why `tick()` asks the database nothing when the world is
down: its database is down too, so the query would spend the tick timing out to
say what the container state already said.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from yulon import dbreads, docker, module_health, realm_flag, unbound_settings, update_failure
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.installer import InstallerError
from yulon.log import get_logger

logger = get_logger(__name__)

State = Literal["up", "stopped", "restart_loop", "starting", "unknown", "missing"]

LOOP_RESTART_STRIKES = 3
"""How many restarts NEW SINCE THIS WATCHER FIRST LOOKED make a loop rather than a hiccup.

Docker's restart policy increments `RestartCount` only when it revives a
container that DIED; a healthy boot, however slow, never increments it. So one
new restart is already abnormal -- and this used to call a loop on exactly
one, which is the false alarm photographed in 8.2b's own evidence: a healthy
server, the command-channel button greyed, "restart loop -- 1 restarts".

Three, because a single OOM-kill or a transient the next boot survives is a
hiccup, and calling that a loop trains users to ignore the warning; three
consecutive failures to get through boot is a pattern no healthy start
produces. The Rust launcher measured the same signal and wrote that reason down
(`origin/rust-main:crates/dml-wow/src/lifecycle.rs`, BOOT_LOOP_RESTART_STRIKES);
this port re-derived one strike without reading it, and the retrospective audit
of 2026-09-08 priced that. Owner's decision, the same day.

A DELTA against the count at the first tick, never the absolute count, so a
long-lived server carrying hundreds of historical restarts cannot trip it by
being looked at. It resets with the run (`_restarted`) and with the settle.
"""

SETTLED_AFTER = timedelta(minutes=10)
"""How long a run must have lasted before a restarted server is called steady again.

A **policy**, not a measurement, and it is written down here rather than left
implicit. The rule it replaces — "clear the loop as soon as one tick sees the
count unchanged" — reads a crash cycle as health for most of every cycle,
because a worldserver takes minutes to load its maps and a loop is therefore
quiet between crashes. Ten minutes is past any first-boot load measured on these
four trees and short enough that a server which really has settled stops being
called unstable within one session.
"""

DOCKER_RESTORE_GRACE = timedelta(minutes=2)
"""How long after Docker restarted itself its restarts are not counted as crashes (T306).

Measured on yulon-ubuntu, 2026-10-05, Docker 29.1.3, with a busybox stand-in of
the compose shape (a database that takes five seconds to listen, a world that
exits 1 when it cannot reach it, both `restart: unless-stopped`). Stopping and
starting docker.service:

- sets every container's `RestartCount` back to 0; a container with no
  dependency came back at 0 and stayed there;
- starts every container at once, without compose's `depends_on` order, so the
  world exited and was restarted by its policy until the database answered:
  1 → 5 (`restarting`, exit 1) → 5 (`running`) within five seconds. The real
  WotLK stack read `restart loop — 6 restarts` that way in the T248 live check,
  and its log has Docker answering again three seconds before the world's last
  exit, so forgetting only the count at the first answer is not enough.

Those deaths are real exits, so neither the count, the exit code (0 again once
it runs) nor `OOMKilled` tells them from a crash. What says Docker restarted is
the daemon itself: `docker.daemon_identity()` changes only when the daemon
started again. A new `StartedAt` alone proves nothing, as a crash-looping world
starts a new run too, even while the CLI cannot reach the daemon for a moment
(Codex adversarial review, round 6). But Docker's restore always starts a new
run, so the identity is read when the dashboard first looks and whenever the
world is on a new run: a restart between two ticks, or with the count reading
higher, lower or the same afterwards, is still told apart before its restarts
are counted (Codex reviews, rounds 7 to 9). No identity seen before, or none
readable now, opens no window: the restarts count as before.

The window closes early, at the first tick whose population read reached the
world's database: the dependency the race is about is up, and every death from
then on is the world's own (Codex adversarial review: a window that always ran
its full length erased a slow loop's deaths inside it, and a run that merely
lasted from one tick to the next proved nothing, as a world can wait a while
for its database and still die of it). Otherwise it lasts two minutes, because
Docker's back-off doubles from 100 ms, so a database that takes T seconds to
come up ends the race by about 2T: this covers one that takes a minute. A world
still dying when it closes is counted from then on, and one that dies fast is
`restarting` most of the time, which reads `restart_loop` again at once.

A `missing` answer is Docker answering: it closes a window already open and
takes the daemon answering next as the one to compare with, since whatever runs
next is a new container, not Docker's restore of the old one. The
cost of the window is that delay, never a loop called steady: a server that was
looping keeps `after_a_loop`.
"""

RECOVERED_AFTER = timedelta(seconds=native.READY_GRACE_SECONDS)
"""How long a run that said ready must stay up to end a crash loop (T390). Start's own rule.

An install's ready stage calls a world up once it printed its ready marker and
was still the same run `READY_GRACE_SECONDS` later (`native.watch_after_ready()`),
and a world that did that after a loop is up by that rule too. Until T390 only
`SETTLED_AFTER` ended a loop whose count did not go back, so a loop fixed in
place read "restart loop — 13 restarts, this run up 9m" with 500 bots online
(m910q, 2026-10-05). The ready marker is read from this run's log only while a
loop is current, and only until it is seen: a healthy tick still reads no log.
Ending the loop moves the sentence, not the interlock: `after_a_loop` stays
until `SETTLED_AFTER`, as after any loop.
"""

_DOCKER_FRACTION = re.compile(r"\.(\d{1,9})")

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

READY_READ_SPAN = timedelta(minutes=30)
"""How much of a run the realm keeper reads for its ready marker (T581, cold review).

The marker comes at the end of the load (1-3 min measured on Tortoise with 500 bots); a run
that did not print it in its first half hour is not called ready, the safe direction."""

FAILURE_READ_EVERY = timedelta(seconds=60)
"""How often a run past `SETTLED_AFTER` and not yet ready is read for a failed update (T600)."""

HEALTH_RETRY_EVERY = timedelta(seconds=60)
"""How long a module's health reading made without the world's ready line is replayed.

Such a reading is not kept for the run (the log may yet catch up), and it is not remade every
tick either: each remake reads the world's log and asks the database. T555."""
WRONG_CLIENT_EVERY = timedelta(seconds=60)
"""How often the world log is read for a client that was turned away (T576)."""

WRONG_CLIENT_OVERLAP = timedelta(seconds=10)
"""Each read starts this far before the last one: the two reads' edges are not exact."""

WRONG_CLIENT_STAYS = timedelta(minutes=15)
"""How long the sentence stays after the last such line: the line only comes with an attempt."""

WRONG_CLIENT_LINE = re.compile(
    r"requested connecting with realm id (\d+) but this realm has id \d+ set in config"
)
"""The world server's refusal of a client whose login packet is not 3.3.5a's (T576).

AzerothCore reads the realm id out of CMSG_AUTH_SESSION at a fixed place; an older build
puts other bytes there, so the number differs on every attempt. The authserver had
already accepted the password, so the player sees a login that loads and drops.
"""

WRONG_CLIENT_MAX_REALM_ID = 255
"""The largest realm id a real realm list holds (the realm id is one byte in the list).

A correct client sends its realm list's id, so a line asking for a small id means the realm
row and the world server's `RealmID` disagree (a hand edit, a second row), not a wrong client.
Only an id above this, the random value of a packet that is not 3.3.5a's, blames the client.
"""


@dataclass(frozen=True)
class Verdict:
    """What one tick found, with every field a caller might otherwise guess at.

    `players` and `bots` are `None` — never zero — whenever they are not known,
    for the reason `dbreads.Population` gives: a number nobody could read must
    not look like a number that was read and came back empty.
    """

    state: State
    restarts: int = 0
    started_at: str = ""
    uptime: timedelta | None = None
    players: int | None = None
    bots: int | None = None
    problem: str = ""
    warning: str = ""
    after_a_loop: bool = False
    """This run followed a crash loop and has not yet outlasted `SETTLED_AFTER`.

    The label and the interlock are not the same question, and this field is
    where they part. A restart count that has gone back to zero says the run
    that looped is over — so calling this one a loop would be false, and the tab
    said exactly that on m910q for minutes. It does NOT say the crash cause is
    gone: docker resets the count on a manual start as readily as on a recreate,
    so a user pressing Start on a server that is still broken would otherwise be
    handed a steady verdict for as long as the world takes to load and die
    again, which on these trees is minutes.

    So the reset moves the sentence and not the permission. `stable` stays False
    until this run has lasted `SETTLED_AFTER`, which is the same evidence the
    settle rule always asked for, measured against the run that is actually
    going (adversarial review, 2026-09-07).
    """
    ready: bool = True
    """Whether this run's own log has printed the world's ready marker (T451).

    `docker ps` calls a world running seconds, or a whole map load, before it
    can take a login, and the header read REALM ONLINE all that time. False
    says the run is up and not yet ready: the line says "starting" and the
    badge follows it. True when the install has no marker to look for, since
    nothing could ever say otherwise.
    """
    failure: str = ""
    """The sentence for a world that stopped at a database update it could not apply (T600).

    Read from this run's own log (`update_failure.explain()`), so it outranks the
    uptime rule that calls a silent world ready after `SETTLED_AFTER`: a world stuck at
    a failed update has no port, no SOAP and no bots, and the population read goes to a
    database that is up. Set means `ready` is False and `stable` is False.
    """
    module_line: str = ""
    """A module's own health sentence (`module_health`), once this run is ready; empty before
    that, for an entry whose catalog has no `health` block, and for a run that is not up (T555 T5).
    """
    database_unreachable: bool = False
    """Set only when the READ failed, never when the bot marker was the problem.

    The distinction is the whole point of the field. A database that will not
    answer means the server cannot be acted on; a blank prefix in a conf file
    means two numbers are missing and nothing else. Collapsing them would refuse
    every command because somebody emptied a configuration key.
    """

    @property
    def stable(self) -> bool:
        """Whether a later step may aim a command at this server.

        The interlock itself belongs to 8.2a, where the first command button
        exists. This box publishes the state it will key off, so that when the
        interlock arrives it is reading a value that has already been proved
        against a real crash loop rather than one written the same day.

        `unknown` is False on purpose: a server nobody could ask about is not a
        server anyone should fire a command at.

        So is a world whose database has gone, and that clause was added after
        the machine refuted the first version (m910q, 2026-09-06). Take the
        database away from an AzerothCore worldserver and it exits, the daemon
        restarts it, and this reads `restart_loop`. Take it away from a CMaNGOS
        one and the process stays up retrying the connection — `running`,
        `RestartCount 0`, indefinitely — so `state == "up"` was true of a server
        nobody could play on, and this said yes.

        And so is a run that has only just replaced a crash loop, for the reason
        `after_a_loop` gives.
        """
        return (
            self.state == "up"
            and not self.database_unreachable
            and not self.after_a_loop
            and not self.failure
        )


def line(verdict: Verdict) -> str:
    """The one sentence the Server tab shows above its three up/down words.

    Pure, so what it says can be asserted without a widget: this is the whole
    user-visible half of 8.1a, and a phrase reachable only through a GUI test is
    a phrase nobody reads twice.

    It never fills a gap with a number. A count that could not be read is given
    as the reason it could not, because "0 players" and "I could not ask" look
    identical on a tab and mean opposite things.
    """
    if verdict.state == "stopped":
        # T608: a world a Stop killed at a failed update keeps the sentence on the tab.
        return f"stopped — {verdict.failure}" if verdict.failure else "stopped"
    if verdict.state == "starting":
        return "starting — Docker restarted and is bringing this server back up"
    if verdict.state == "unknown":
        return "could not be asked — docker did not answer about this container"
    if verdict.state == "missing":
        return (
            "no container — docker says this server's world container does not exist "
            "(it was removed, or has not been created yet)"
        )
    parts: list[str] = []
    if verdict.failure:
        parts.append(verdict.failure)
        if verdict.state == "restart_loop":
            parts[-1] += f" (restart loop — {verdict.restarts} restarts)"
        elif verdict.uptime is not None:
            parts[-1] += f" ({uptime_text(verdict.uptime)})"
    elif verdict.state == "restart_loop":
        head = f"restart loop — {verdict.restarts} restarts"
        parts.append(head + (f", this run {uptime_text(verdict.uptime)}" if verdict.uptime else ""))
    else:
        counts = (
            f"{verdict.players} players, {verdict.bots} bots"
            if verdict.players is not None and verdict.bots is not None
            else verdict.problem
        )
        word = "up" if verdict.ready else "starting — the world server has not reported ready"
        parts.append(f"{word} — {counts}" if counts and verdict.ready else word)
        if verdict.uptime is not None:
            parts[-1] += f", {uptime_text(verdict.uptime)}"
        if verdict.after_a_loop:
            minutes = int(SETTLED_AFTER.total_seconds() // 60)
            parts.append(
                f"restarted after a crash loop — not called steady until this run "
                f"has lasted {minutes}m"
            )
    if verdict.warning:
        parts.append(verdict.warning)
    if verdict.problem and verdict.players is not None:
        parts.append(verdict.problem)
    if verdict.module_line:
        parts.append(verdict.module_line)
    return " · ".join(parts)


def uptime_text(uptime: timedelta | None) -> str:
    """`up 2h 14m`, and `up 42s` for a run that has not seen a minute yet.

    Public since T187: the client launcher's banner says a run's length in the
    same words as the Server tab's verdict line.
    """
    if uptime is None:
        return ""
    seconds = int(uptime.total_seconds())
    if seconds < 60:
        return f"up {seconds}s"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    return f"up {hours}h {minutes}m" if hours else f"up {minutes}m"


class Dashboard:
    """Watches one install. Stateful, because a restart count only means something twice.

    A single count says nothing — every server that has ever been restarted has
    a non-zero one. What says something is the count CHANGING between two ticks,
    so the previous value is kept here rather than asked for again.

    What is kept is about one RUN, and a restart -- by hand or by the daemon --
    ends it and resets docker's count. `_restarted()` is where that is noticed.
    Without it the loop verdict outlives its own run and a server that is back
    goes on reading as the broken one it replaced; with it read as proof of
    health, a server that is still broken reads as steady for as long as its
    world takes to die again. It is neither, so it moves the sentence and leaves
    the interlock where it was.
    """

    def __init__(
        self,
        spec: docker.ContainerSpec,
        entry: CatalogEntry,
        server_dir: Path,
        *,
        sql: dbreads.SqlReader,
        wsl_distro: str | None = None,
        state_of: Callable[[str], docker.ContainerState] | None = None,
        daemon_of: Callable[[], str] | None = None,
        log_of: Callable[[str, str], str] | None = None,
        login_log_of: Callable[[str, str], str] | None = None,
        ready_log_of: Callable[[str, str, str], str] | None = None,
        now: Callable[[], datetime] | None = None,
        realm: realm_flag.Keeper | None = None,
    ) -> None:
        self.spec = spec
        self.entry = entry
        self.server_dir = server_dir
        self.sql = sql
        self._state_of = state_of or (
            lambda container: docker.container_state(container, wsl_distro=wsl_distro)
        )
        self._daemon_of = daemon_of or (lambda: docker.daemon_identity(wsl_distro=wsl_distro))
        self._log_of = log_of or (
            lambda container, since: docker._logs(
                container, this_run_only=True, since=since, wsl_distro=wsl_distro
            )
        )
        # T576: the log read for a refused client. An injected `log_of` stands in for Docker,
        # so a test that gave only that one asked nothing of this read; it names its own.
        self._login_log_of: Callable[[str, str], str] | None
        if login_log_of is not None:
            self._login_log_of = login_log_of
        elif log_of is None:
            self._login_log_of = lambda container, since: docker._logs(
                container, this_run_only=True, since=since, wsl_distro=wsl_distro
            )
        else:
            self._login_log_of = None
        self._login_run: str | None = None
        self._login_read_at: datetime | None = None
        self._wrong_client_at: datetime | None = None
        self._banner = _ready_banner(entry)
        self._reads_update_failures = _reads_update_failures(entry)
        # T600: what this run's log said about a failed update, kept per run. `_failure_text`
        # is the sentence ("" for none), `_failure_settled` that no later read can change it.
        self._failure_run: str | None = None
        self._failure_text = ""
        self._failure_settled = False
        self._ticks = 0
        self._early: tuple[str, str, set[str]] | None = None
        self._failure_read_at: datetime | None = None
        self._run_log_cache: tuple[int, str, str] | None = None
        self._now = now or (lambda: datetime.now(UTC))
        self._missing_table_said = dbreads.MissingTableSaid()
        self._last_restarts: int | None = None
        self._strikes = 0
        self._looping = False
        self._loop_is_current = False
        self._restoring_until: datetime | None = None
        self._daemon: str | None = None
        self._daemon_asked = False
        self._last_started: str | None = None
        # T390: the dead run Docker was backing off at the last tick, and the run
        # whose log said ready while a loop was current, with when that was seen.
        self._restarting_run: str | None = None
        self._ready_run: str | None = None
        self._ready_seen_at: datetime | None = None
        # T555 T5: the module's health sentence for the run it was last read for.
        self._health_run: str | None = None
        self._health_got: module_health.HealthReading | None = None
        self._health_running: dict[str, bool | None] | None = None
        self._health_waiting: tuple[str, datetime, str] | None = None
        """`(run, when, sentence)` of the last reading that was not kept: replayed for
        `HEALTH_RETRY_EVERY`, then asked again."""
        # T581: the realm row's offline bit, kept in step with what this tick reads. Built
        # here only over real Docker, for the reason `login_log_of` is: a test that injects
        # the container state hands its own keeper or asks nothing of this.
        # T581: the keeper's own read for the ready marker, bounded to the run's first
        # `READY_READ_SPAN`: it may be asked of a run that has lasted days. An injected
        # `log_of` stands in for Docker, so a test that gave only that one is read through it.
        self._ready_log_of: Callable[[str, str, str], str]
        if ready_log_of is not None:
            self._ready_log_of = ready_log_of
        elif log_of is None:
            self._ready_log_of = lambda container, since, until: docker._logs(
                container, this_run_only=True, since=since, until=until, wsl_distro=wsl_distro
            )
        else:
            injected = log_of
            self._ready_log_of = lambda container, since, _until: injected(container, since)
        self._realm: realm_flag.Keeper | None
        if realm is not None:
            self._realm = realm
        elif state_of is None:
            self._realm = realm_flag.keeper_for(entry, spec, server_dir, sql, wsl_distro=wsl_distro)
        else:
            self._realm = None
        if self._realm is not None:
            self._realm.said_ready = self._world_said_ready
        self._seen = docker.ContainerState()

    def _world_said_ready(self, run: str) -> bool:
        """Whether the world still runs run `run` and its log shows the ready marker (T581).

        Positive evidence only, for the realm keeper's clear: no marker to look for, a world
        that is not running that run any more, or a log without the marker all say no.
        """
        if self._banner is None:
            return False
        state = self._state_of(self.spec.world)
        if state.status != "running" or state.started_at != run:
            return False
        if self._ready_run == run:
            return True
        if not self._banner.search(self._early_log(run, as_keeper=True)):
            return False
        self._ready_run, self._ready_seen_at = run, self._now()
        return True

    def tick(self) -> Verdict:
        """Ask once, and answer with everything that was learned.

        T581: then hands what it read to the realm keeper, when this entry has one: the hold
        epoch is taken before the container is read, so a deliberate Stop that began or
        ended during this tick is seen by the keeper's clear.
        """
        realm = self._realm
        begun = realm.begin() if realm is not None else 0
        verdict = self._tick()
        if realm is not None:
            state = self._seen
            uptime = self._uptime(state.started_at)
            ready = state.status == "running" and (
                self._banner is None
                or (uptime is not None and uptime >= SETTLED_AFTER)
                or self._ready_run == state.started_at
            )
            if ready and self._failure_run == state.started_at and self._failure_text:
                ready = False  # T600: no uptime rule outranks a failed update in this run's log
            realm.after_tick(state.status, state.started_at, ready, begun)
        return verdict

    def _tick(self) -> Verdict:
        self._ticks += 1
        state = self._state_of(self.spec.world)
        self._seen = state
        uptime = self._uptime(state.started_at)
        if state.status == "":
            # A read that failed said nothing about the count, and `0` is what
            # it leaves in the field. Kept out of the history, it stays a gap in
            # the record; stored, it makes the next honest read look like growth.
            # T95: docker's own "no such container" is an answer, not a silence,
            # and the player whose containers were deleted by hand must be told so.
            kind: State = "missing" if state.missing else "unknown"
            if state.missing:
                # Docker answered: whatever runs next is a new container, not
                # Docker's restore of the old one, and the daemon answering now
                # is the one to compare with from here on.
                self._restoring_until = None
                self._daemon = None
                self._daemon_asked = False
            return Verdict(kind, state.restart_count, state.started_at, uptime)
        new_run = self._last_started is not None and state.started_at != self._last_started
        self._last_started = state.started_at
        restoring = self._docker_is_restoring(new_run)
        if self._restarted(state) or restoring:
            self._loop_is_current = False
            self._strikes = 0
        new_restarts = (
            state.restart_count - self._last_restarts
            if self._last_restarts is not None and state.restart_count > self._last_restarts
            else 0
        )
        self._last_restarts = state.restart_count
        if new_restarts and not restoring:
            self._strikes += new_restarts
            if self._strikes >= LOOP_RESTART_STRIKES:
                self._looping = True
                self._loop_is_current = True
        elif self._looping and uptime is not None and uptime >= SETTLED_AFTER:
            self._looping = False
            self._strikes = 0
        if state.status == "restarting" and not restoring:
            if self._restarting_run == state.started_at:
                # T390: Docker still backing off the same dead run a tick later is
                # a loop on Docker's own word, counted or not. A loop whose restarts
                # all fell in the T306 window had no strikes, so it was never
                # recorded, and the run that was fixed read steady at once.
                self._looping = True
                self._loop_is_current = True
            self._restarting_run = state.started_at
        else:
            self._restarting_run = None
        if self._looping and self._loop_is_current and state.status == "running":
            if self._said_ready_and_stayed_up(state.started_at):
                # The strikes stay (cold review): a world that dies again after
                # this, past Docker's back-off reset, is a loop at its next one.
                # `SETTLED_AFTER` clears them, as it clears `_looping`.
                self._loop_is_current = False

        if restoring and state.status == "restarting":
            return Verdict("starting", state.restart_count, state.started_at, uptime)
        if state.status == "restarting" or (
            self._looping and self._loop_is_current and state.status == "running"
        ):
            return Verdict(
                "restart_loop",
                state.restart_count,
                state.started_at,
                uptime,
                failure=self._update_failure(state.started_at, uptime),
            )
        if state.status != "running":
            return Verdict(
                "stopped",
                state.restart_count,
                state.started_at,
                uptime,
                failure=self._kept_failure(state.started_at),
            )
        verdict = self._with_population(state, uptime, after_a_loop=self._looping)
        if restoring and verdict.players is not None:
            self._restoring_until = None  # its database answered: the race is over
        failure = self._update_failure(state.started_at, uptime)
        if failure:
            verdict = replace(verdict, ready=False, failure=failure)
        if verdict.ready:
            line = self._module_line(state.started_at)
            if line:
                verdict = replace(verdict, module_line=line)
        return self._with_wrong_client(verdict, state.started_at)

    def _first_since(self, run: str) -> str:
        """Where a run's first read starts: the run, but not further back than the sentence lives.

        A server up for days has old refused logins in its log; read whole they would show as
        fresh for `WRONG_CLIENT_STAYS`, and a large log would be read in one go.
        """
        uptime = self._uptime(run)
        if uptime is None or uptime <= WRONG_CLIENT_STAYS:
            return run
        return f"{int(WRONG_CLIENT_STAYS.total_seconds())}s"

    def _with_wrong_client(self, verdict: Verdict, run: str) -> Verdict:
        """`verdict`, saying so when the world log shows a client turned away (T576).

        Only for a server whose catalog names the build its players' clients must be
        (`client.required_build`). The log is read once a minute and only from the last
        read on, never every tick; the sentence stays `WRONG_CLIENT_STAYS` after the
        last such line and goes with the run.
        """
        required = self.entry.client.required_build
        if required is None or self._login_log_of is None:
            return verdict
        now = self._now()
        if self._login_run != run:
            self._login_run, self._login_read_at, self._wrong_client_at = run, None, None
        elapsed = None if self._login_read_at is None else now - self._login_read_at
        if elapsed is not None and elapsed < timedelta(0):
            # This watcher's clock went backwards: what was read and seen is on a timeline
            # that no longer exists, so read the run again and let the sentence age from now.
            self._login_read_at, elapsed = None, None
            if self._wrong_client_at is not None:
                self._wrong_client_at = now
        if elapsed is None or elapsed >= WRONG_CLIENT_EVERY:
            # `--since` is told how long ago, not when: Docker works that out on ITS clock, which
            # a Docker Desktop VM lets drift from this machine's, and an absolute stamp from
            # here would then skip lines or repeat them.
            since = (
                self._first_since(run)
                if elapsed is None
                else f"{int((elapsed + WRONG_CLIENT_OVERLAP).total_seconds()) + 1}s"
            )
            self._login_read_at = now
            if any(
                int(asked) > WRONG_CLIENT_MAX_REALM_ID
                for asked in WRONG_CLIENT_LINE.findall(self._login_log_of(self.spec.world, since))
            ):
                self._wrong_client_at = now
        if self._wrong_client_at is None or now - self._wrong_client_at > WRONG_CLIENT_STAYS:
            return verdict
        version = self.entry.client.version
        sentence = (
            f"a game client that is not {version} (build {required}) logged in and was "
            "dropped by the world server: every player must use a stock "
            f"{version} client"
        )
        return replace(verdict, warning=" · ".join(w for w in (verdict.warning, sentence) if w))

    def _module_line(self, run: str) -> str:
        """The module's health sentence for run `run` (T555 T5).

        Empty for an entry without a `health` block. Asked only after the world said ready, so
        the module has printed its lines and made its tables. What the database and the log
        said is kept by `run` and asked once; one that could not be read is not kept, so the next
        tick asks again. The switches are laid beside it afresh at every tick: the settings file
        can change under a running world (the Tuning card), and "(on at the next start)" is
        about the file as it is now.
        """
        native_block = self.entry.install.native
        block = native_block.azerothcore if native_block is not None else None
        health = block.health if block is not None else None
        if block is None or health is None:
            return ""
        if self._health_run != run or self._health_got is None:
            waiting = self._health_waiting
            if (
                waiting is not None
                and waiting[0] == run
                and timedelta(0) <= self._now() - waiting[1] < HEALTH_RETRY_EVERY
            ):
                return waiting[2]
            try:
                log = self._log_of(self.spec.world, run)
            except Exception as exc:  # noqa: BLE001 - an unreadable log is an answer, not a crash
                logger.warning(f"could not read {self.entry.id}'s world log for its health: {exc}")
                log = ""
            got = module_health.reading(
                health, block.sql_checks, self.entry.databases.schema_map(), self.sql, log, ()
            )
            if got.unreadable:
                return module_health.sentence(got)
            running = (
                unbound_settings.running_state(log)
                if unbound_settings.shown_for(self.entry)
                else None
            )
            # A bad reading is kept only once the world's own ready line is in this run's log.
            # "Ready" can come from uptime alone (SETTLED_AFTER), before the module's lines or
            # tables are all there, and a verdict kept from then would never heal. A good one
            # holds the module's own lines, so it cannot be early. No ready marker to look for
            # at all (`_banner is None`) leaves nothing to wait for. Readiness is judged from the
            # very log the reading was made from, never from a second read of it.
            if got.good or self._banner is None or self._banner.search(log):
                self._health_run, self._health_got, self._health_running = run, got, running
            else:
                if got.absent_marker:
                    # Neither the world's ready line nor any of the module's own is in this
                    # run's log. That says what was seen, not why: the world may still be
                    # loading or hung, or the log may have been cut. "Did not load" would be a
                    # guess, so the line says only what was not there.
                    got = module_health.HealthReading(
                        got.name,
                        unreadable=(
                            "this run's world log has neither its ready line nor "
                            f"{got.name}'s start-up lines"
                        ),
                    )
                line = module_health.sentence(got)  # a bad one never says a switch
                self._health_waiting = (run, self._now(), line)
                return line
            self._health_waiting = None
        got = self._health_got
        if self._health_running is not None:
            got = replace(
                got, switches=unbound_settings.switches_for(self.server_dir, self._health_running)
            )
        return module_health.sentence(got)

    def _said_ready_and_stayed_up(self, run: str) -> bool:
        """Whether run `run` printed its ready marker and is still up `RECOVERED_AFTER` on (T390).

        The log is read until the marker is seen in it, once per tick, and only
        from here: while a loop is current and the world is running. Seen is
        timed from this watcher's clock, not the log's, so a marker printed
        before the first look is given the whole watch again, never less.
        """
        if not self._saw_ready(run):
            return False
        seen_at = self._ready_seen_at
        return seen_at is not None and self._now() - seen_at >= RECOVERED_AFTER

    def _saw_ready(self, run: str) -> bool:
        """Whether run `run`'s own log has printed the ready marker; read until it has (T390, T451).

        No marker to look for reads False here, so a loop is ended by the settle
        rule as before; `tick()` treats that case as ready for the header.
        """
        if self._banner is None:
            return False
        if self._ready_run != run:
            if not self._banner.search(self._run_log(run)):
                return False
            self._ready_run = run
            self._ready_seen_at = self._now()
        return True

    def _early_log(self, run: str, *, as_keeper: bool) -> str:
        """Run `run`'s log up to `READY_READ_SPAN`, one read serving both who ask for it (T600).

        The realm keeper looks for the ready marker in it and `_update_failure()` for a failed
        update. Whoever asks first reads; the other takes that same text once, so a run is
        read as often as before this question was added, and the keeper's own later asks
        (which expect a fresh read) are untouched.
        """
        who = "keeper" if as_keeper else "failure"
        slot = self._early
        if slot is not None and slot[0] == run and who not in slot[2]:
            slot[2].add(who)
            return slot[1]
        started = _run_start(run)
        until = (started + READY_READ_SPAN).isoformat() if started is not None else ""
        text = self._ready_log_of(self.spec.world, run, until)
        self._early = (run, text, {who})
        return text

    def _run_log(self, run: str) -> str:
        """Run `run`'s whole log, read once per tick however many questions are put to it."""
        cached = self._run_log_cache
        if cached is not None and cached[0] == self._ticks and cached[1] == run:
            return cached[2]
        text = self._log_of(self.spec.world, run)
        self._run_log_cache = (self._ticks, run, text)
        return text

    def _kept_failure(self, run: str) -> str:
        """The sentence already read for run `run`, once that run has stopped (T608).

        Never a new read: a stopped world is looked at every tick for as long as it stays
        stopped. A Stop kills a world stuck at a failed update, and what `_update_failure()`
        read while it ran is still true of that run; a new run has its own `started_at`.
        """
        return self._failure_text if self._failure_run == run else ""

    def _update_failure(self, run: str, uptime: timedelta | None) -> str:
        """The sentence for a failed update in run `run`'s own log, or `""` (T600).

        Asked only for an entry whose catalog `ready.fatal` covers the core's failure line,
        and only while the run has not said ready: the marker ends the question, and so does
        a run that outlived `READY_READ_SPAN` once that span has been read. A run younger
        than `SETTLED_AFTER` is read whole, as `_saw_ready()` reads it (one read per tick,
        shared); an older one is read bounded to its first span, like the realm keeper's,
        because the updater runs in the first minutes and a long-lived world's log is large.
        A log that could not be read (an exception, or the empty text `_logs()` answers for a
        Docker that would not talk) settles nothing and is asked again.
        """
        if not self._reads_update_failures:
            return ""
        if self._failure_run != run:
            self._failure_run, self._failure_text, self._failure_settled = run, "", False
            self._failure_read_at = None
        if self._failure_text:
            return self._failure_text
        if self._failure_settled or self._ready_run == run:
            return ""
        now = self._now()
        try:
            if uptime is None or uptime < SETTLED_AFTER:
                log = self._run_log(run)  # the same read `_saw_ready()` makes every tick
            else:
                # Past the tab's settle time the run is called ready anyway, so the log is read
                # to see what that rule must not outrank: once a `FAILURE_READ_EVERY` at most.
                at = self._failure_read_at
                if at is not None and timedelta(0) <= now - at < FAILURE_READ_EVERY:
                    return ""
                self._failure_read_at = now
                log = self._early_log(run, as_keeper=False)
                # Settled only by a read that returned something: `_logs()` answers "" for a
                # Docker that would not answer, and no run has an empty first half hour.
                self._failure_settled = uptime >= READY_READ_SPAN and bool(log.strip())
        except Exception as exc:  # noqa: BLE001 - an unreadable log is no answer, not a crash
            logger.warning(f"could not read {self.entry.id}'s world log for a failed update: {exc}")
            return ""
        self._failure_text = update_failure.explain(log)
        return self._failure_text

    def _docker_is_restoring(self, new_run: bool) -> bool:
        """Whether this answer falls in `DOCKER_RESTORE_GRACE` after Docker restarted (T306).

        The daemon's identity is read on the first answer and whenever the world
        is on a new run (`DOCKER_RESTORE_GRACE` says why). A different one opens
        the window; one that could not be read changes nothing, and the last one
        read stays what Docker was. Docker restarting itself starts every
        container again, which ends the run the loop evidence was about, so the
        caller clears it the way `_restarted()` does, and only that: `_looping`
        stays, so a server that was looping before is still not called steady.
        """
        now = self._now()
        if new_run or not self._daemon_asked:
            self._daemon_asked = True
            seen = self._daemon_of()
            if seen:
                if self._daemon and seen != self._daemon:
                    self._restoring_until = now + DOCKER_RESTORE_GRACE
                self._daemon = seen
        return self._restoring_until is not None and now < self._restoring_until

    def _restarted(self, state: docker.ContainerState) -> bool:
        """Whether the run the loop evidence is about has ended.

        Measured on m910q, 2026-09-07: a watcher left running across 8.1d's
        crash-loop check went on printing `restart loop — 0 restarts, this run
        up 3m` for minutes after the world came back, while a dashboard made
        fresh at that moment read `up`. A container with no restarts is not
        looping, and the sentence was false.

        The count going BACKWARDS is the evidence, and it needs nothing this
        module does not already read: within one run docker's count only ever
        grows, so a drop means the run it was counting is over. It does NOT say
        which way it ended, and the live run showed why that matters — compose
        answered `Container tortoise-mangosd Started`, not `Recreated`, and the
        count still went 8 → 0. A manual start resets it exactly as a recreate
        does, so this cannot be read as "somebody fixed it".

        That is why only `_loop_is_current` moves here, and never `_looping`
        itself: the tab stops saying a false sentence, and `stable` stays shut
        until the new run has lasted `SETTLED_AFTER`. `.Id` was written first
        and taken out — it would name the two endings apart, and neither ending
        is evidence of health, so nothing downstream could act on the
        difference.
        """
        return self._last_restarts is not None and state.restart_count < self._last_restarts

    def _with_population(
        self,
        state: docker.ContainerState,
        uptime: timedelta | None,
        *,
        after_a_loop: bool = False,
    ) -> Verdict:
        """The two counts, or the reason there are none. Never a wrong number."""
        answer = dbreads.resolve_marker(self.entry, self.server_dir)
        # A run past SETTLED_AFTER is called ready without its marker: a rotated or
        # unreadable log must not hold the header at STARTING for good (review).
        ready = (
            self._banner is None
            or (uptime is not None and uptime >= SETTLED_AFTER)
            or self._saw_ready(state.started_at)
        )
        if answer.marker is None:
            return Verdict(
                "up",
                state.restart_count,
                state.started_at,
                uptime,
                problem=answer.problem,
                after_a_loop=after_a_loop,
                ready=ready,
            )
        counts = dbreads.population(
            self.sql,
            self.entry,
            answer.marker,
            world_up=uptime,
            said=self._missing_table_said,
            run=state.started_at,
        )
        return Verdict(
            "up",
            state.restart_count,
            state.started_at,
            uptime,
            players=counts.players,
            bots=counts.bots,
            problem=counts.problem,
            warning=counts.warning,
            database_unreachable=bool(counts.problem),
            after_a_loop=after_a_loop,
            ready=ready,
        )

    def _uptime(self, started_at: str) -> timedelta | None:
        """How long the current run has lasted (`run_length`), at this watcher's clock."""
        return run_length(started_at, self._now())


def _ready_banner(entry: CatalogEntry) -> re.Pattern[str] | None:
    """`entry`'s world ready marker, as the install waits on it (T390); None if it has none.

    A marker that does not compile is logged and leaves the loop to the settle
    rule, as before T390: an instrument must not break the tab.
    """
    block = entry.install.native
    if block is None:
        return None
    try:
        return re.compile(native.ready_spec_for(entry, block.ready).world)
    except InstallerError as exc:
        logger.warning(f"the dashboard cannot read {entry.id}'s ready marker: {exc}")
        return None


def _reads_update_failures(entry: CatalogEntry) -> bool:
    """Whether `entry`'s catalog `ready.fatal` covers the core's failed-update line (T600).

    Opt-in by the catalog and not by game id: only a core whose updater logs that line, and
    whose entry says so, has its start-up log read for it.
    """
    block = entry.install.native
    if block is None or block.ready.fatal is None:
        return False
    fatal = block.ready.fatal
    if not block.ready.regex:
        return fatal in update_failure.PROBE
    try:
        return re.search(fatal, update_failure.PROBE) is not None
    except re.error:
        return False


def _run_start(started_at: str) -> datetime | None:
    """When a run that docker says started at `started_at` began, or `None` (see `run_length`)."""
    length = run_length(started_at, _EPOCH)
    return None if length is None else _EPOCH - length


def run_length(started_at: str, now: datetime) -> timedelta | None:
    """How long a run that docker says started at `started_at` has lasted, or `None`.

    Docker prints nine fractional digits and `fromisoformat` accepts three
    or six, so the fraction is trimmed rather than the whole timestamp
    thrown away. A timestamp that still will not parse leaves the uptime
    absent — an absent duration is honest, a wrong one is not. A module
    function since T187, so the client launcher reads a start time the way
    the dashboard does.
    """
    if not started_at:
        return None
    text = _DOCKER_FRACTION.sub(lambda m: "." + m.group(1)[:6], started_at.strip())
    try:
        started = datetime.fromisoformat(text)
    except ValueError:
        logger.debug(f"could not read the container's start time {started_at!r}")
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return now - started
