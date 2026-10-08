"""T581: the dashboard tick keeps the Tortoise realm row honest while Yu'lon is open.

T577 marks the realm offline from Yu'lon's own lifecycle. Two holes were left: Docker's
`restart: unless-stopped` reviving a crashed mangosd without Yu'lon (the core never sets the
bit, so the realm stayed listed online through the reload), and a cancelled Stop whose
put-back failed (a running world left listed Offline). The tick, which already reads the
world container's status, run and ready marker every five seconds off the GUI thread,
marks the first and clears the second. It never clears while one of Yu'lon's own Stops or
replaces holds the realm offline on purpose, and never before the world said ready.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from yulon import dashboard, docker, realm_flag
from yulon.catalog.catalog import load_catalog

TORTOISE = load_catalog().get("wow-tortoise")
WOTLK = load_catalog().get("wow-wotlk")
SPEC = TORTOISE.container_spec()
NOW = datetime.fromisoformat("2026-10-08T12:00:00+00:00")
READY_LINE = "World server is up and running! Loading time: 1 minutes 2 seconds"
RUN_A = "2026-10-08T11:00:00.000000000Z"
RUN_B = "2026-10-08T11:59:58.000000000Z"
FLAGS_READ = "SELECT realmflags FROM realmlist WHERE id=1;"


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class Sql:
    """The read seam: answers the population read and the realm flags read."""

    def __init__(self, flags: int = 0) -> None:
        self.flags = flags
        self.reads: list[str] = []
        self.fails = False

    def query(self, db: str, statement: str) -> str:
        if statement.startswith("SELECT realmflags"):
            self.reads.append(f"{db}:{statement}")
            if self.fails:
                raise RuntimeError("the database did not answer")
            return f"{self.flags}\n"
        return "0\t0\t0\t0\n"


class Writes:
    """The two writes, recorded; each one also moves the fake row like the real one would."""

    def __init__(self, sql: Sql) -> None:
        self.sql = sql
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.mark_ok = True
        self.during_clear: Callable[[], None] | None = None

    def mark(self, entry: Any, spec: Any, server_dir: Path, **kwargs: Any) -> bool:
        self.calls.append(("mark", kwargs))
        if self.mark_ok:
            self.sql.flags |= 2
        return self.mark_ok

    def clear(self, entry: Any, spec: Any, server_dir: Path, **kwargs: Any) -> bool:
        self.calls.append(("clear", kwargs))
        if self.during_clear is not None:
            self.during_clear()
        self.sql.flags &= ~2
        return True

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class World:
    """The world container as the tick sees it: status, run, and that run's log."""

    def __init__(self) -> None:
        self.status = "running"
        self.run = RUN_A
        self.log = READY_LINE
        self.after_read: Callable[[], None] | None = None
        self.log_reads = 0
        self.state_reads = 0

    def state(self, _container: str) -> docker.ContainerState:
        self.state_reads += 1
        answer = docker.ContainerState(self.status, self.run, 0)
        if self.after_read is not None:
            self.after_read()
        return answer

    def log_of(self, _container: str, _since: str) -> str:
        self.log_reads += 1
        return self.log


@pytest.fixture(autouse=True)
def _no_hold_left_over() -> Any:
    yield
    assert realm_flag.held(SPEC) is False, "a test left the realm held offline"


def _watch(
    tmp_path: Path, world: World, sql: Sql, writes: Writes, clock: Clock
) -> dashboard.Dashboard:
    keeper = realm_flag.Keeper(
        TORTOISE, SPEC, tmp_path, sql, now=clock, mark=writes.mark, clear=writes.clear
    )
    return dashboard.Dashboard(
        SPEC,
        TORTOISE,
        tmp_path,
        sql=sql,
        state_of=world.state,
        daemon_of=lambda: "daemon",
        log_of=world.log_of,
        login_log_of=lambda _c, _s: "",
        now=clock,
        realm=keeper,
    )


@pytest.fixture
def rig(tmp_path: Path) -> tuple[dashboard.Dashboard, World, Sql, Writes, Clock]:
    world, sql, clock = World(), Sql(), Clock()
    writes = Writes(sql)
    return _watch(tmp_path, world, sql, writes, clock), world, sql, writes, clock


Rig = tuple[dashboard.Dashboard, World, Sql, Writes, Clock]


# -- hole 1: Docker revives a crashed world ----------------------------------------------


def test_a_world_docker_is_restarting_marks_the_realm_offline_once(rig: Rig) -> None:
    watch, world, sql, writes, clock = rig
    watch.tick()  # up, ready: nothing to do
    world.status = "restarting"
    for _ in range(16):  # Docker's back-off grows to minutes on a world that keeps dying
        watch.tick()
        clock.advance(5)
    assert writes.names == ["mark"]
    assert writes.calls[0][1]["start_database"] is False, "a tick never starts a database"
    assert sql.flags & 2


def test_the_new_run_after_a_crash_is_marked_until_it_says_ready(rig: Rig) -> None:
    """Docker's first back-off is 100 ms: the tick may only ever see the new run loading."""
    watch, world, sql, writes, clock = rig
    watch.tick()
    world.run, world.log = RUN_B, "Loading maps..."
    for _ in range(3):
        watch.tick()
        clock.advance(5)
    assert writes.names == ["mark"]
    assert sql.flags & 2


def test_the_mark_is_retried_but_not_every_tick_when_it_failed(rig: Rig) -> None:
    watch, world, _sql, writes, clock = rig
    writes.mark_ok = False
    world.status = "restarting"
    for _ in range(6):  # 25 s of ticks
        watch.tick()
        clock.advance(5)
    assert writes.names == ["mark"]
    clock.advance(10)
    watch.tick()
    assert writes.names == ["mark", "mark"]


def test_a_world_stopped_outside_yulon_is_marked_once(rig: Rig) -> None:
    """A `docker stop` of the world alone leaves realmd listing it; mark it (Codex adversarial)."""
    watch, world, sql, writes, clock = rig
    watch.tick()
    world.status = "exited"
    for _ in range(20):
        watch.tick()
        clock.advance(5)
    assert writes.names == ["mark"]
    assert writes.calls[0][1]["start_database"] is False
    assert sql.flags & 2


def test_a_stopped_world_whose_database_is_down_is_retried_rarely_and_quietly(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    """After Yu'lon's own Stop the database is down too: a try every five minutes, nothing said."""
    watch, world, sql, writes, clock = rig
    caplog.set_level("INFO", logger="yulon.realm_flag")
    writes.mark_ok = False
    world.status = "exited"
    for _ in range(60):  # five minutes
        watch.tick()
        clock.advance(5)
    assert writes.names == ["mark"]
    assert [r.getMessage() for r in caplog.records] == []
    writes.mark_ok = True  # its database came back (a world stopped alone)
    watch.tick()
    assert writes.names == ["mark", "mark"]
    assert sql.flags & 2


def test_a_run_stopped_right_after_it_said_ready_is_marked_again(rig: Rig) -> None:
    """Marked while loading, cleared by the core, stopped before a tick saw it up (Codex)."""
    watch, world, sql, writes, clock = rig
    world.run, world.log = RUN_B, "Loading maps..."
    watch.tick()
    sql.flags &= ~2  # the core's own clear on listen
    world.status = "exited"
    clock.advance(5)
    watch.tick()
    assert writes.names == ["mark", "mark"]
    assert sql.flags & 2


def test_a_world_docker_cannot_find_is_left_alone(rig: Rig) -> None:
    watch, world, _sql, writes, clock = rig
    world.status = ""
    watch.tick()
    assert writes.names == []


def test_a_run_that_was_marked_loading_is_marked_again_when_it_crashes(rig: Rig) -> None:
    """Docker's `restarting` carries the dead run's StartedAt: the same run, a new spell (Codex)."""
    watch, world, sql, writes, clock = rig
    world.run, world.log = RUN_B, "Loading maps..."
    watch.tick()
    world.log = READY_LINE
    sql.flags &= ~2  # the core's own clear on listen
    clock.advance(5)
    watch.tick()
    world.status = "restarting"
    clock.advance(5)
    watch.tick()
    assert writes.names == ["mark", "mark"]
    assert sql.flags & 2


def test_a_world_that_is_up_and_ready_is_not_marked(rig: Rig) -> None:
    watch, _world, _sql, writes, clock = rig
    for _ in range(3):
        watch.tick()
        clock.advance(5)
    assert "mark" not in writes.names


# -- hole 2: a stuck bit on a world that is up -------------------------------------------


def test_a_bit_still_set_a_minute_after_ready_is_cleared(rig: Rig) -> None:
    watch, _world, sql, writes, clock = rig
    sql.flags = 2  # a cancelled Stop whose put-back failed
    watch.tick()  # ready seen now
    clock.advance(59)
    watch.tick()
    assert writes.names == [], "inside the grace the core may still be about to clear it"
    clock.advance(2)
    watch.tick()
    assert writes.names == ["clear"]
    assert sql.flags == 0
    assert sql.reads == [f"auth:{FLAGS_READ}"]


def test_an_old_run_that_never_said_ready_is_never_cleared(rig: Rig) -> None:
    """Ten minutes of uptime calls the header ready; it is no proof the world listens (Codex)."""
    watch, world, sql, writes, clock = rig
    world.log = "Loading maps..."  # RUN_A is an hour old: the tab calls it settled
    sql.flags = 2
    for _ in range(40):
        watch.tick()
        clock.advance(5)
    assert "clear" not in writes.names
    assert world.log_reads == 1, "a run's log is searched once for the keeper, not every minute"


def test_a_run_that_says_ready_late_is_asked_again(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    """A load longer than the tab's ten-minute fallback is not written off for its whole run."""
    watch, world, sql, writes, clock = rig
    caplog.set_level("INFO", logger="yulon.realm_flag")
    world.log = "Loading maps..."
    sql.flags = 2
    for _ in range(140):  # 700 s: asked at ~65 s (no) and again ten minutes on (no)
        watch.tick()
        clock.advance(5)
    assert world.log_reads == 2
    assert len([r for r in caplog.records if "does not show it ready" in r.getMessage()]) == 1
    world.log = READY_LINE
    for _ in range(130):  # the next ask, ten minutes on, finds the marker
        watch.tick()
        clock.advance(5)
    assert writes.names == ["clear"]
    assert world.log_reads == 3


def test_a_mark_that_lands_after_the_core_cleared_is_taken_off_at_once(rig: Rig) -> None:
    """The world said ready and cleared the bit between the tick's read and its mark (Codex)."""
    watch, world, sql, writes, clock = rig
    world.run, world.log = RUN_B, "Loading maps..."
    real_mark = writes.mark

    def the_world_gets_there_first(*a: Any, **k: Any) -> bool:
        world.log = READY_LINE
        sql.flags &= ~2  # the core's own clear, Master.cpp:228
        return real_mark(*a, **k)

    writes.mark = the_world_gets_there_first  # type: ignore[method-assign]
    watch._realm._mark = the_world_gets_there_first  # type: ignore[union-attr]
    watch.tick()
    assert writes.names == ["mark", "clear"]
    assert sql.flags == 0


def test_a_world_that_died_again_before_the_recheck_stays_marked(rig: Rig) -> None:
    watch, world, sql, writes, clock = rig
    world.run, world.log = RUN_B, "Loading maps..."
    real_mark = writes.mark

    def it_dies(*a: Any, **k: Any) -> bool:
        world.log, world.status = READY_LINE, "restarting"
        return real_mark(*a, **k)

    watch._realm._mark = it_dies  # type: ignore[union-attr]
    watch.tick()
    assert writes.names == ["mark"]
    assert sql.flags & 2


def test_a_world_restarting_after_it_had_said_ready_stays_marked(rig: Rig) -> None:
    """The dead run's log has the marker; that says nothing about the run Docker is starting."""
    watch, world, sql, writes, clock = rig
    watch.tick()
    world.status = "restarting"  # log still READY_LINE: the run that crashed
    for _ in range(3):
        watch.tick()
        clock.advance(5)
    assert writes.names == ["mark"]
    assert sql.flags & 2
    assert world.state_reads == 4, "no recheck is asked of a world that is not running"


def test_a_start_in_progress_is_never_cleared(rig: Rig) -> None:
    watch, world, sql, writes, clock = rig
    world.run, world.log = RUN_B, "Loading maps..."
    sql.flags = 2
    for _ in range(40):  # 200 s of loading
        watch.tick()
        clock.advance(5)
    assert "clear" not in writes.names
    assert sql.reads == [], "the bit is not even read before the world said ready"


def test_the_bit_is_read_once_a_minute_not_every_tick(rig: Rig) -> None:
    watch, _world, sql, _writes, clock = rig
    watch.tick()
    for _ in range(36):  # three minutes
        clock.advance(5)
        watch.tick()
    assert 2 <= len(sql.reads) <= 3


def test_a_clear_bit_is_left_alone(rig: Rig) -> None:
    watch, _world, sql, writes, clock = rig
    watch.tick()
    clock.advance(61)
    watch.tick()
    assert len(sql.reads) == 1
    assert writes.names == []


def test_a_read_that_fails_changes_nothing_and_raises_nothing(rig: Rig) -> None:
    watch, _world, sql, writes, clock = rig
    sql.flags, sql.fails = 2, True
    watch.tick()
    clock.advance(61)
    assert watch.tick().state == "up"
    assert writes.names == []


# -- never fighting Yu'lon's own Stop -----------------------------------------------------


def test_a_deliberate_stop_in_progress_is_not_put_back_online(rig: Rig) -> None:
    watch, _world, sql, writes, clock = rig
    watch.tick()
    clock.advance(61)
    with realm_flag.deliberately_offline(SPEC):
        sql.flags = 2  # the Stop's own mark; the world takes long to save
        for _ in range(30):
            watch.tick()
            clock.advance(5)
    assert writes.names == []


def test_a_stop_that_ends_during_a_tick_is_not_put_back_online(
    tmp_path: Path,
) -> None:
    """The tick read a running world, then the Stop ended (world gone, bit set) before its clear."""
    world, sql, clock = World(), Sql(), Clock()
    writes = Writes(sql)
    watch = _watch(tmp_path, world, sql, writes, clock)
    watch.tick()
    clock.advance(61)
    sql.flags = 2

    def stop_ends_after_the_read() -> None:
        world.after_read = None  # once: the tick's own read of the container
        with realm_flag.deliberately_offline(SPEC):
            pass

    world.after_read = stop_ends_after_the_read
    watch.tick()
    assert writes.names == []


def test_a_stop_that_begins_while_the_flags_are_read_is_not_undone(rig: Rig) -> None:
    """The Stop marked the bit and is still running when the clear would be written."""
    watch, _world, sql, writes, clock = rig
    watch.tick()
    clock.advance(61)
    sql.flags = 2
    hold = realm_flag.deliberately_offline(SPEC)
    real_query = sql.query

    def stop_begins_during_the_read(db: str, statement: str) -> str:
        answer = real_query(db, statement)
        if statement.startswith("SELECT realmflags"):
            hold.__enter__()
        return answer

    sql.query = stop_begins_during_the_read  # type: ignore[method-assign]
    try:
        watch.tick()
    finally:
        hold.__exit__(None, None, None)
    assert writes.names == []


def test_a_stop_that_marks_while_the_clear_runs_gets_its_mark_back(rig: Rig) -> None:
    """The Stop's hold began and its mark landed while the clear was in Docker (cold review)."""
    watch, _world, sql, writes, clock = rig
    watch.tick()
    clock.advance(61)
    sql.flags = 2
    hold = realm_flag.deliberately_offline(SPEC)

    def stop_marks_during_the_clear() -> None:
        hold.__enter__()
        sql.flags |= 2  # the Stop's own mark, before the clear's UPDATE lands

    writes.during_clear = stop_marks_during_the_clear
    try:
        watch.tick()
    finally:
        hold.__exit__(None, None, None)
    assert writes.names == ["clear", "mark"]
    assert writes.calls[1][1]["start_database"] is False
    assert sql.flags & 2


def test_a_stop_that_began_and_ended_during_the_clear_gets_its_mark_back(rig: Rig) -> None:
    """The whole Stop (hold, mark, world down, release) fell inside the clear's Docker calls."""
    watch, _world, sql, writes, clock = rig
    watch.tick()
    clock.advance(61)
    sql.flags = 2

    def a_whole_stop_during_the_clear() -> None:
        with realm_flag.deliberately_offline(SPEC):
            sql.flags |= 2  # the Stop's own mark, before the clear's UPDATE lands

    writes.during_clear = a_whole_stop_during_the_clear
    watch.tick()
    assert writes.names == ["clear", "mark"]
    assert sql.flags & 2


def test_a_stops_hold_never_waits_for_a_clear_stuck_in_docker(rig: Rig) -> None:
    """The hold's lock is never held across a Docker call: a wedged Docker blocks no Stop."""
    watch, _world, sql, writes, clock = rig
    watch.tick()
    clock.advance(61)
    sql.flags = 2
    took: list[float] = []

    def stop_during_the_clear() -> None:
        def stop() -> None:
            started = time.monotonic()
            with realm_flag.deliberately_offline(SPEC):
                took.append(time.monotonic() - started)

        stopper = threading.Thread(target=stop)
        stopper.start()
        stopper.join(timeout=2)

    writes.during_clear = stop_during_the_clear
    watch.tick()
    assert took and took[0] < 1.0


def test_a_mark_recheck_does_not_clear_under_a_stops_hold(rig: Rig) -> None:
    """The world says ready right after the mark, but a Stop holds the realm offline."""
    watch, world, sql, writes, clock = rig
    world.run, world.log = RUN_B, "Loading maps..."
    real_mark = writes.mark
    hold = realm_flag.deliberately_offline(SPEC)

    def ready_and_held(*a: Any, **k: Any) -> bool:
        world.log = READY_LINE
        hold.__enter__()
        return real_mark(*a, **k)

    watch._realm._mark = ready_and_held  # type: ignore[union-attr]
    try:
        watch.tick()
    finally:
        hold.__exit__(None, None, None)
    assert writes.names == ["mark"]
    assert sql.flags & 2


# -- a clock that goes backwards (cold review) --------------------------------------------


def test_a_failed_mark_is_retried_after_the_clock_went_back(rig: Rig) -> None:
    watch, world, _sql, writes, clock = rig
    writes.mark_ok = False
    world.status = "restarting"
    watch.tick()
    clock.advance(-3600)
    watch.tick()
    assert writes.names == ["mark", "mark"]


def test_a_stuck_bit_is_still_cleared_after_the_clock_went_back(rig: Rig) -> None:
    watch, _world, sql, writes, clock = rig
    sql.flags = 2
    watch.tick()  # ready seen
    clock.advance(30)
    watch.tick()
    clock.advance(-3600)
    for _ in range(30):  # two and a half minutes on the new clock
        watch.tick()
        clock.advance(5)
    assert writes.names == ["clear"]


def test_a_read_after_the_clock_went_back_is_not_put_off(rig: Rig) -> None:
    watch, _world, sql, writes, clock = rig
    watch.tick()
    clock.advance(61)
    watch.tick()  # read: clear
    assert len(sql.reads) == 1
    sql.flags = 2
    clock.advance(-3600)
    for _ in range(14):  # 70 s on the new clock
        watch.tick()
        clock.advance(5)
    assert writes.names == ["clear"]


def test_an_unconfirmed_run_is_asked_again_after_the_clock_went_back(rig: Rig) -> None:
    watch, world, sql, writes, clock = rig
    world.log = "Loading maps..."
    sql.flags = 2
    for _ in range(14):  # asked at ~65 s: no
        watch.tick()
        clock.advance(5)
    assert world.log_reads == 1
    world.log = READY_LINE
    clock.advance(-3600)
    for _ in range(30):
        watch.tick()
        clock.advance(5)
    assert writes.names == ["clear"]


# -- the keeper's log read is bounded (cold review) ---------------------------------------


def test_the_keepers_ready_read_is_bounded_to_the_runs_first_half_hour(tmp_path: Path) -> None:
    world, sql, clock = World(), Sql(flags=2), Clock()
    writes = Writes(sql)
    windows: list[tuple[str, str]] = []

    def bounded(_container: str, since: str, until: str) -> str:
        windows.append((since, until))
        return READY_LINE

    keeper = realm_flag.Keeper(
        TORTOISE, SPEC, tmp_path, sql, now=clock, mark=writes.mark, clear=writes.clear
    )
    watch = dashboard.Dashboard(
        SPEC,
        TORTOISE,
        tmp_path,
        sql=sql,
        state_of=world.state,
        daemon_of=lambda: "daemon",
        log_of=lambda _c, _s: "",  # the tab's own read: an hour-old run is not read at all
        login_log_of=lambda _c, _s: "",
        ready_log_of=bounded,
        now=clock,
        realm=keeper,
    )
    for _ in range(14):
        watch.tick()
        clock.advance(5)
    assert windows == [(RUN_A, "2026-10-08T11:30:00+00:00")]
    assert writes.names == ["clear"]


def test_the_tortoise_stop_holds_the_realm_offline_while_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.controller_wow_tortoise.controller import TortoiseController

    seen: list[bool] = []
    monkeypatch.setattr(realm_flag, "mark_offline", lambda *_a, **_k: seen.append(True))
    monkeypatch.setattr(
        "yulon.controller.Controller.stop", lambda _self: seen.append(realm_flag.held(SPEC))
    )
    TortoiseController(tmp_path).stop()
    assert seen == [True, True]
    assert realm_flag.held(SPEC) is False


def test_a_rebuilds_replace_holds_the_realm_offline_while_it_runs(tmp_path: Path) -> None:
    from tests.test_families_cmangos import Recorder
    from tests.test_families_cmangos import context as cm_context
    from tests.test_families_cmangos import engine as cm_engine

    held: list[bool] = []

    def recreate(spec: docker.ContainerSpec, server_dir: Path, **_k: Any) -> bool:
        held.append(realm_flag.held(spec))
        return True

    eng = cm_engine(Recorder(), entry=TORTOISE, docker_ready=lambda: True, recreate=recreate)
    list(eng.stage_recreate(cm_context(tmp_path)))
    assert held == [True]


# -- wiring and logging -------------------------------------------------------------------


def test_only_an_entry_with_the_flag_column_gets_a_keeper(tmp_path: Path) -> None:
    sql = Sql()
    assert realm_flag.keeper_for(TORTOISE, SPEC, tmp_path, sql) is not None
    assert realm_flag.keeper_for(WOTLK, WOTLK.container_spec(), tmp_path, sql) is None


def test_the_real_dashboard_builds_its_keeper(tmp_path: Path) -> None:
    watch = dashboard.Dashboard(SPEC, TORTOISE, tmp_path, sql=Sql())
    assert isinstance(watch._realm, realm_flag.Keeper)
    other = dashboard.Dashboard(WOTLK.container_spec(), WOTLK, tmp_path, sql=Sql())
    assert other._realm is None


def test_a_keeper_that_breaks_never_breaks_the_tick(rig: Rig) -> None:
    watch, world, _sql, writes, clock = rig

    def broken(*_a: Any, **_k: Any) -> bool:
        raise OSError("boom")

    writes.mark = broken  # type: ignore[method-assign]
    watch._realm._mark = broken  # type: ignore[union-attr]
    world.status = "restarting"
    assert watch.tick().state == "restart_loop"


def test_the_keeper_logs_once_per_spell_not_per_tick(
    rig: Rig, caplog: pytest.LogCaptureFixture
) -> None:
    watch, world, sql, writes, clock = rig
    writes.mark_ok = False
    world.status = "restarting"
    caplog.set_level("DEBUG", logger="yulon.realm_flag")
    for _ in range(30):  # two and a half minutes of a failing mark
        watch.tick()
        clock.advance(5)
    said = [r for r in caplog.records if r.levelno >= 20]
    assert len(said) == 1, [r.getMessage() for r in said]
    writes.mark_ok = True
    caplog.clear()
    watch.tick()  # the dead run, marked now
    world.status, world.run, world.log = "running", RUN_B, "Loading maps..."
    for _ in range(6):  # the new run, marked again: one spell, so said once
        watch.tick()
        clock.advance(5)
    assert writes.names[-2:] == ["mark", "mark"]
    said = [r for r in caplog.records if r.levelno >= 20]
    assert len(said) == 1, [r.getMessage() for r in said]


def test_a_bounded_log_read_asks_docker_for_until(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    seen: list[list[str]] = []

    def fake_docker(argv: list[str], *_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=READY_LINE, stderr="")

    monkeypatch.setattr(docker, "_docker", fake_docker)
    out = docker._logs(
        SPEC.world, this_run_only=True, since=RUN_A, until="2026-10-08T11:30:00+00:00"
    )
    assert out == READY_LINE
    assert seen == [["logs", "--since", RUN_A, "--until", "2026-10-08T11:30:00+00:00", SPEC.world]]
