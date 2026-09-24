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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from yulon import dbreads, docker
from yulon.catalog.catalog import CatalogEntry
from yulon.log import get_logger

logger = get_logger(__name__)

State = Literal["up", "stopped", "restart_loop", "unknown", "missing"]

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

_DOCKER_FRACTION = re.compile(r"\.(\d{1,9})")


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
        return self.state == "up" and not self.database_unreachable and not self.after_a_loop


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
        return "stopped"
    if verdict.state == "unknown":
        return "could not be asked — docker did not answer about this container"
    if verdict.state == "missing":
        return (
            "no container — docker says this server's world container does not exist "
            "(it was removed, or has not been created yet)"
        )
    parts: list[str] = []
    if verdict.state == "restart_loop":
        head = f"restart loop — {verdict.restarts} restarts"
        parts.append(head + (f", this run {_uptime(verdict.uptime)}" if verdict.uptime else ""))
    else:
        counts = (
            f"{verdict.players} players, {verdict.bots} bots"
            if verdict.players is not None and verdict.bots is not None
            else verdict.problem
        )
        parts.append(f"up — {counts}" if counts else "up")
        if verdict.uptime is not None:
            parts[-1] += f", {_uptime(verdict.uptime)}"
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
    return " · ".join(parts)


def _uptime(uptime: timedelta | None) -> str:
    """`up 2h 14m`, and `up 42s` for a run that has not seen a minute yet."""
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
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.spec = spec
        self.entry = entry
        self.server_dir = server_dir
        self.sql = sql
        self._state_of = state_of or (
            lambda container: docker.container_state(container, wsl_distro=wsl_distro)
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._last_restarts: int | None = None
        self._strikes = 0
        self._looping = False
        self._loop_is_current = False

    def tick(self) -> Verdict:
        """Ask once, and answer with everything that was learned."""
        state = self._state_of(self.spec.world)
        uptime = self._uptime(state.started_at)
        if state.status == "":
            # A read that failed said nothing about the count, and `0` is what
            # it leaves in the field. Kept out of the history, it stays a gap in
            # the record; stored, it makes the next honest read look like growth.
            # T95: docker's own "no such container" is an answer, not a silence,
            # and the player whose containers were deleted by hand must be told so.
            kind: State = "missing" if state.missing else "unknown"
            return Verdict(kind, state.restart_count, state.started_at, uptime)
        if self._restarted(state):
            self._loop_is_current = False
            self._strikes = 0
        new_restarts = (
            state.restart_count - self._last_restarts
            if self._last_restarts is not None and state.restart_count > self._last_restarts
            else 0
        )
        self._last_restarts = state.restart_count
        if new_restarts:
            self._strikes += new_restarts
            if self._strikes >= LOOP_RESTART_STRIKES:
                self._looping = True
                self._loop_is_current = True
        elif self._looping and uptime is not None and uptime >= SETTLED_AFTER:
            self._looping = False
            self._strikes = 0

        if state.status == "restarting" or (
            self._looping and self._loop_is_current and state.status == "running"
        ):
            return Verdict("restart_loop", state.restart_count, state.started_at, uptime)
        if state.status != "running":
            return Verdict("stopped", state.restart_count, state.started_at, uptime)
        return self._with_population(state, uptime, after_a_loop=self._looping)

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
        if answer.marker is None:
            return Verdict(
                "up",
                state.restart_count,
                state.started_at,
                uptime,
                problem=answer.problem,
                after_a_loop=after_a_loop,
            )
        counts = dbreads.population(self.sql, self.entry, answer.marker)
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
        )

    def _uptime(self, started_at: str) -> timedelta | None:
        """How long the current run has lasted, or `None` if that cannot be read.

        Docker prints nine fractional digits and `fromisoformat` accepts three
        or six, so the fraction is trimmed rather than the whole timestamp
        thrown away. A timestamp that still will not parse leaves the uptime
        absent — an absent duration is honest, a wrong one is not.
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
        return self._now() - started
