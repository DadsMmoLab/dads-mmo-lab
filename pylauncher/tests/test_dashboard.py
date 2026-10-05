"""Tests for `yulon.dashboard` — the one line above the three up/down words (8.1a).

The bug this exists to close, verbatim from `pyplan/bug-checklist.md:499`:

    HIGH — A TBC server whose mangosd is stuck in a restart loop is reported as
    a fully successful install, end to end through the GUI.

A crash-looping worldserver appears in `docker ps` between restarts, so every
check that asks "is it running" says yes. What separates the two is the restart
count and whether the current run has lasted.
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from yulon import dashboard, docker
from yulon.catalog import catalog as catalog_module

WOTLK = catalog_module.load_catalog().get("wow-wotlk")
SPEC = WOTLK.container_spec()
NOW = datetime.fromisoformat("2026-09-06T18:00:00+00:00")


class _FakeSql:
    def __init__(self, answer: str = "3\t497\t500\t512\n") -> None:
        self.statements: list[str] = []
        self.answer = answer

    def query(self, db: str, statement: str) -> str:
        self.statements.append(statement)
        return self.answer


def _install(tmp_path: Path) -> Path:
    conf = tmp_path / WOTLK.observability.bots.prefix_conf_file
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("AiPlayerbot.RandomBotAccountPrefix = rndbot\n", encoding="utf-8")
    return tmp_path


def _watch(
    tmp_path: Path, states: list[docker.ContainerState], sql: _FakeSql | None = None
) -> dashboard.Dashboard:
    """A dashboard whose container states are handed to it, one per tick."""
    remaining = list(states)
    return dashboard.Dashboard(
        SPEC,
        WOTLK,
        _install(tmp_path),
        sql=sql if sql is not None else _FakeSql(),
        state_of=lambda _container: remaining.pop(0),
        now=lambda: NOW,
    )


def _running(started: str = "2026-09-06T12:00:00.123456789Z", restarts: int = 0):
    return docker.ContainerState("running", started, restarts)


def test_a_server_that_is_up_reports_its_population(tmp_path: Path) -> None:
    sql = _FakeSql("3\t497\t500\t512\n")

    verdict = _watch(tmp_path, [_running()], sql).tick()

    assert verdict.state == "up"
    assert (verdict.players, verdict.bots) == (3, 497)
    assert verdict.stable is True


def test_a_stopped_server_is_not_asked_how_many_players_it_has(tmp_path: Path) -> None:
    """Its database is down too; the query would time out once per tick to say nothing."""
    sql = _FakeSql()

    verdict = _watch(tmp_path, [docker.ContainerState("exited", "", 0)], sql).tick()

    assert verdict.state == "stopped"
    assert sql.statements == []
    assert verdict.players is None


def test_one_new_restart_is_a_hiccup_and_not_yet_a_loop(tmp_path: Path) -> None:
    """A single OOM-kill the next boot survives is not a pattern.

    This used to call a loop on ONE new restart, and the false alarm is
    photographed in 8.2b's own evidence (`1-refusal-while-running.png`: a
    healthy server, the command-channel button greyed, "restart loop -- 1
    restarts"). rust-main had already built this on the same `RestartCount`
    signal, chose three, and wrote down why (`crates/dml-wow/src/lifecycle.rs`,
    BOOT_LOOP_RESTART_STRIKES): Docker only increments the count for a death, so
    one is already abnormal -- but calling one a loop "would train users to
    ignore the warning", and three consecutive failures to get through boot is
    a pattern no healthy start produces. Owner's decision, 2026-09-08.
    """
    watch = _watch(tmp_path, [_running(restarts=2), _running(restarts=3)])

    first = watch.tick()
    second = watch.tick()

    assert first.state == "up"
    assert second.state == "up", "one new restart read as a loop"
    assert second.restarts == 3


def test_three_new_restarts_since_the_watch_began_are_a_restart_loop(tmp_path: Path) -> None:
    """The count is the only thing that separates a loop from a server that is up.

    Three NEW since this watcher first looked -- a delta, never the absolute
    count, so a long-lived server carrying hundreds of historical restarts can
    never trip it on its first tick.
    """
    watch = _watch(
        tmp_path,
        [
            _running(restarts=200),
            _running(restarts=201),
            _running(restarts=202),
            _running(restarts=203),
        ],
    )

    states = [watch.tick().state for _ in range(4)]

    assert states == ["up", "up", "up", "restart_loop"], states


def test_a_container_docker_calls_restarting_is_a_loop_on_the_very_first_tick(
    tmp_path: Path,
) -> None:
    """No history needed: `restarting` is the daemon saying so itself."""
    verdict = _watch(tmp_path, [docker.ContainerState("restarting", "", 7)]).tick()

    assert verdict.state == "restart_loop"
    assert verdict.stable is False


def test_a_young_run_after_a_restart_still_reads_as_a_loop(tmp_path: Path) -> None:
    """A worldserver takes minutes to load, so a loop is quiet between its crashes.

    Clearing the verdict as soon as one tick sees a steady count would call a
    server with a three-minute crash cycle healthy for most of every cycle —
    which is the shape of the bug this closes.
    """
    just_started = (NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    watch = _watch(
        tmp_path,
        [
            _running(restarts=4),
            _running(started=just_started, restarts=7),
            _running(just_started, 7),
        ],
    )

    watch.tick()
    watch.tick()
    third = watch.tick()

    assert third.state == "restart_loop"


def test_a_server_that_restarted_hours_ago_and_has_been_up_since_is_not_a_loop(
    tmp_path: Path,
) -> None:
    """Every server has restarted at some point; that alone is not instability."""
    long_ago = "2026-09-06T12:00:00.000000000Z"
    watch = _watch(tmp_path, [_running(long_ago, 4), _running(long_ago, 4)])

    watch.tick()
    second = watch.tick()

    assert second.state == "up"
    assert second.restarts == 4


def test_a_daemon_that_cannot_be_asked_says_unknown_rather_than_stopped(tmp_path: Path) -> None:
    """ "Stopped" is a claim. An empty answer is the absence of one."""
    verdict = _watch(tmp_path, [docker.ContainerState()]).tick()

    assert verdict.state == "unknown"
    assert verdict.stable is False
    assert verdict.players is None


def test_a_container_docker_says_does_not_exist_is_called_that_not_unanswered(
    tmp_path: Path,
) -> None:
    """Andood's screenshot (T95): containers deleted by hand read "docker did not answer"."""
    verdict = _watch(tmp_path, [docker.ContainerState(missing=True)]).tick()
    assert verdict.state == "missing"
    assert not verdict.stable
    said = dashboard.line(verdict)
    assert "does not exist" in said
    assert "did not answer" not in said


def test_the_uptime_survives_dockers_nanosecond_timestamps(tmp_path: Path) -> None:
    """`fromisoformat` takes 3 or 6 fractional digits; docker prints 9."""
    verdict = _watch(tmp_path, [_running("2026-09-06T17:00:00.000000000Z")]).tick()

    assert verdict.uptime == timedelta(hours=1)


def test_a_timestamp_that_cannot_be_parsed_leaves_the_uptime_absent_not_wrong(
    tmp_path: Path,
) -> None:
    verdict = _watch(tmp_path, [_running("some day in the future")]).tick()

    assert verdict.uptime is None
    assert verdict.state == "up"


def test_a_marker_that_cannot_be_resolved_refuses_the_counts_without_hiding_the_server(
    tmp_path: Path,
) -> None:
    """The tab still says the server is up; it just does not invent two numbers."""
    server_dir = _install(tmp_path)
    (server_dir / WOTLK.observability.bots.prefix_conf_file).write_text(
        "AiPlayerbot.RandomBotAccountPrefix =\n", encoding="utf-8"
    )
    sql = _FakeSql()
    watch = dashboard.Dashboard(
        SPEC,
        WOTLK,
        server_dir,
        sql=sql,
        state_of=lambda _c: _running(),
        now=lambda: NOW,
    )

    verdict = watch.tick()

    assert verdict.state == "up"
    assert verdict.players is None
    assert verdict.problem != ""
    assert sql.statements == []


def test_the_counts_warning_reaches_the_verdict(tmp_path: Path) -> None:
    """A marker matching nothing is a question for the user, not a silent zero."""
    verdict = _watch(tmp_path, [_running()], _FakeSql("2\t0\t0\t812\n")).tick()

    assert verdict.bots == 0
    assert verdict.warning != ""


def test_a_game_with_no_measured_block_yet_says_so_and_still_reports_the_container(
    tmp_path: Path,
) -> None:
    """Every shipped tree has a block now, so this drives the path with a made-up entry.

    The code path is still reachable — a game added tomorrow arrives without
    one — and it must answer "I have no measured marker for this" rather than
    counting nothing and calling it zero.
    """
    unmeasured = (
        catalog_module.load_catalog().get("wow-tortoise").model_copy(update={"observability": None})
    )
    watch = dashboard.Dashboard(
        unmeasured.container_spec(),
        unmeasured,
        tmp_path,
        sql=_FakeSql(),
        state_of=lambda _c: _running(),
        now=lambda: NOW,
    )

    verdict = watch.tick()

    assert verdict.state == "up"
    assert verdict.players is None
    assert "wow-tortoise" in verdict.problem


# -- the sentence the tab shows --------------------------------------------
#
# Pure, and tested here rather than through the widget: what this says is the
# whole user-visible half of 8.1a, and a phrase that only a GUI test can reach
# is a phrase nobody reads twice.


def test_the_line_leads_with_the_two_counts_because_that_is_what_was_asked_for() -> None:
    verdict = dashboard.Verdict("up", players=3, bots=497, uptime=timedelta(hours=2, minutes=14))

    assert dashboard.line(verdict) == "up — 3 players, 497 bots, up 2h 14m"


def test_the_line_says_restart_loop_in_those_words_with_the_count() -> None:
    """The word a person searches for when their server "keeps going down"."""
    verdict = dashboard.Verdict("restart_loop", restarts=4, uptime=timedelta(seconds=30))

    assert "restart loop" in dashboard.line(verdict)
    assert "4 restarts" in dashboard.line(verdict)


def test_a_refused_count_is_given_as_its_reason_and_never_as_two_zeroes() -> None:
    verdict = dashboard.Verdict("up", problem="AiPlayerbot.RandomBotAccountPrefix is blank")

    line = dashboard.line(verdict)

    assert "blank" in line
    assert "0 players" not in line


def test_the_warning_rides_along_with_the_counts_rather_than_replacing_them() -> None:
    verdict = dashboard.Verdict("up", players=2, bots=0, warning="no account matched 'rndbot'")

    line = dashboard.line(verdict)

    assert "2 players, 0 bots" in line
    assert "rndbot" in line


def test_an_unknown_state_says_docker_could_not_be_asked_rather_than_pretending() -> None:
    assert "could not" in dashboard.line(dashboard.Verdict("unknown"))
    assert "did not answer" in dashboard.line(dashboard.Verdict("unknown"))
    assert "did not answer" not in dashboard.line(dashboard.Verdict("missing"))


def test_a_stopped_server_says_stopped_and_nothing_else() -> None:
    assert dashboard.line(dashboard.Verdict("stopped")) == "stopped"


def test_an_uptime_under_a_minute_still_reads_as_a_duration() -> None:
    verdict = dashboard.Verdict("up", players=0, bots=0, uptime=timedelta(seconds=42))

    assert "up 42s" in dashboard.line(verdict)


def test_a_world_whose_database_is_gone_is_not_stable_even_though_it_is_up(
    tmp_path: Path,
) -> None:
    """Measured on m910q, 2026-09-06, and it refuted what `stable` did.

    Take the database away from an AzerothCore worldserver and it exits; the
    daemon restarts it, the count climbs, and the verdict says `restart_loop`.
    Take it away from a CMaNGOS one and the process STAYS UP, retrying the
    connection: `running`, `RestartCount 0`, for as long as you leave it. So on
    that tree `state == "up"` was true of a server nobody could play on, and
    `stable` — the value 8.2a's command interlock keys off — said yes.

    A server whose database cannot be read is not one to aim a command at.
    """

    class _Dead:
        def query(self, db: str, statement: str) -> str:
            raise RuntimeError("ERROR 2002 (HY000): Can't connect to local MySQL server")

    watch = dashboard.Dashboard(
        SPEC,
        WOTLK,
        _install(tmp_path),
        sql=_Dead(),
        state_of=lambda _c: _running(),
        now=lambda: NOW,
    )

    verdict = watch.tick()

    assert verdict.state == "up"
    assert verdict.stable is False
    assert "could not read" in dashboard.line(verdict)


def test_a_marker_problem_does_not_make_a_healthy_server_unstable(tmp_path: Path) -> None:
    """The other half: a blank bot prefix says nothing about the server.

    Refusing every command because someone emptied a conf key would be the
    mirror of the bug above — and the database was never even asked.
    """
    server_dir = _install(tmp_path)
    (server_dir / WOTLK.observability.bots.prefix_conf_file).write_text(
        "AiPlayerbot.RandomBotAccountPrefix =" + chr(10), encoding="utf-8"
    )
    watch = dashboard.Dashboard(
        SPEC, WOTLK, server_dir, sql=_FakeSql(), state_of=lambda _c: _running(), now=lambda: NOW
    )

    verdict = watch.tick()

    assert verdict.problem != ""
    assert verdict.stable is True, "a conf key is not a reason to refuse commands"


def test_a_restarted_container_is_not_called_a_loop_and_is_not_called_settled_either(
    tmp_path: Path,
) -> None:
    """Measured on m910q, 2026-09-07: the tab said `restart loop — 0 restarts`.

    A watcher left running across 8.1d's crash-loop check went on calling a
    healthy server a loop for minutes after the world came back — zero restarts,
    and still a loop — while a dashboard made fresh at that moment read `up`.
    A count of zero cannot be a loop, and the sentence was simply false.

    But the count resetting is not proof of health either, and the first fix for
    this treated it as though it were (adversarial review, 2026-09-07). Docker
    resets `RestartCount` on a MANUAL start as readily as on a recreate — the
    live run measured `Container tortoise-mangosd Started`, not `Recreated`,
    with the count going 8 → 0 — so a user pressing Start on a server whose
    crash cause is still there would have been handed `stable=True` for as long
    as the world takes to load and die again, which on these trees is minutes.
    That is the bug 8.1a exists to close, arriving from the other side.

    So the reset moves only the LABEL. The interlock stays shut until this run
    has outlasted `SETTLED_AFTER`, which is the same evidence the settle rule
    has always asked for, now measured from the run that is actually going.
    """
    long_ago = "2026-09-06T12:00:00.000000000Z"
    three_minutes_in = (NOW - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    eleven_minutes_in = (NOW - timedelta(minutes=11)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    watch = _watch(
        tmp_path,
        [
            _running(long_ago, 8),
            _running(long_ago, 11),  # three new restarts: a loop, under the three-strike rule
            _running(three_minutes_in, 0),
            _running(eleven_minutes_in, 0),
        ],
    )

    watch.tick()
    looping = watch.tick()
    restarted = watch.tick()
    settled = watch.tick()

    assert looping.state == "restart_loop"
    assert restarted.state == "up", "a container with no restarts is not looping"
    assert "restart loop" not in dashboard.line(restarted)
    assert restarted.stable is False, "a reset count is not evidence the crash cause is gone"
    assert "crash" in dashboard.line(restarted), "the tab must say why it is still holding back"
    assert settled.stable is True, "ten minutes of this run is what clears it"


def test_a_loop_in_the_run_that_followed_is_caught_on_its_own_evidence(tmp_path: Path) -> None:
    """The new run is watched like any other: its own count growing is a loop again."""
    long_ago = "2026-09-06T12:00:00.000000000Z"
    three_minutes_in = (NOW - timedelta(minutes=3)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    thirty_seconds_in = (NOW - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%S.000000000Z")
    watch = _watch(
        tmp_path,
        [
            _running(long_ago, 8),
            _running(long_ago, 9),
            _running(three_minutes_in, 0),
            _running(thirty_seconds_in, 3),
        ],
    )

    watch.tick()
    watch.tick()
    restarted = watch.tick()
    crashing_again = watch.tick()

    assert restarted.state == "up"
    assert crashing_again.state == "restart_loop"


def test_a_read_that_failed_does_not_become_a_restart_loop_on_the_next_tick(
    tmp_path: Path,
) -> None:
    """A docker that would not answer said nothing about the count, not zero.

    `ContainerState()` carries `restart_count=0` because that is also what a
    container which has never restarted says, and a read that failed leaves the
    same value in the field. Kept, it turns the NEXT honest read into a count
    that grew: one hiccup, and a healthy server reads as a loop until it has
    been up ten minutes.
    """
    watch = _watch(tmp_path, [_running(restarts=4), docker.ContainerState(), _running(restarts=4)])

    assert watch.tick().state == "up"
    assert watch.tick().state == "unknown"
    assert watch.tick().state == "up"


def test_the_realm_poll_logs_a_silent_docker_once_not_every_tick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The realm poll's own reader, unpatched (Linux live test of PR 291, item 1).

    With docker.service and docker.socket stopped, a Server tab left open logged
    "could not read the state of ac-worldserver" once per five-second tick. This
    ticks the dashboard's DEFAULT state reader, so the real `container_state()`
    runs and only the docker CLI is stood in for.

    Mutation: log on every failed read in `container_state()`, and this counts six.
    """
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: subprocess.CompletedProcess(
            cmd,
            1,
            "",
            "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
            "Is the docker daemon running?\n",
        ),
    )
    watch = dashboard.Dashboard(SPEC, WOTLK, _install(tmp_path), sql=_FakeSql(), now=lambda: NOW)
    with caplog.at_level("WARNING", logger="yulon.docker"):
        verdicts = [watch.tick() for _ in range(6)]
    assert {v.state for v in verdicts} == {"unknown"}
    said = [
        r for r in caplog.records if f"could not read the state of {SPEC.world}" in r.getMessage()
    ]
    assert len(said) == 1, [r.getMessage() for r in said]


# ---------------------------------------------------------------- T306: Docker restarted itself


_AWAY = docker.ContainerState(
    said=(
        "Cannot connect to the Docker daemon at unix:///var/run/docker.sock. "
        "Is the docker daemon running?"
    )
)
"""A read that failed because no daemon answered: Docker's own words on Linux (T248 log)."""


def _stamp(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


class _ScriptedSql(_FakeSql):
    """A database that is down (`None`) or answers, one entry per population read.

    Docker's restore starts the database and the world at once; until the
    database answers, the world's deaths are that race. Past the script it answers.
    """

    def __init__(self, *script: bool) -> None:
        super().__init__()
        self.script = list(script)

    def query(self, db: str, statement: str) -> str:
        if self.script and not self.script.pop(0):
            raise RuntimeError("ERROR 2002 (HY000): Can't connect to local MySQL server")
        return super().query(db, statement)


def _clocked(
    tmp_path: Path,
    script: list[tuple[timedelta, docker.ContainerState]],
    sql: _FakeSql | None = None,
) -> tuple[dashboard.Dashboard, list[datetime]]:
    """A dashboard ticked at the given offsets from NOW, one container state per tick."""
    clock = [NOW]
    remaining = list(script)

    def state_of(_container: str) -> docker.ContainerState:
        offset, state = remaining.pop(0)
        clock[0] = NOW + offset
        return state

    watch = dashboard.Dashboard(
        SPEC,
        WOTLK,
        _install(tmp_path),
        sql=sql if sql is not None else _FakeSql(),
        state_of=state_of,
        now=lambda: clock[0],
    )
    return watch, clock


def test_restarts_while_docker_brings_itself_back_are_not_a_restart_loop(
    tmp_path: Path,
) -> None:
    """T306, from the T248 live check on yulon-ubuntu (2026-10-05): "restart loop — 6 restarts".

    docker.service was stopped and started; the world server never crashed on its
    own. Measured with a busybox stand-in on yulon-ubuntu, Docker 29.1.3: a daemon
    restart sets every container's `RestartCount` back to 0 and starts them all at
    once, ignoring compose's `depends_on`, so a world whose database is not up yet
    exits and is restarted by its policy until the database answers. The count went
    1 → 5 (restarting, exit 1) → 5 (running) in the five seconds after `systemctl
    start docker`, and the app's log shows Docker answering again at 16:38:59 while
    the world still died until 16:39:02. The ticks below are that sequence.

    Mutation: count restarts during the grace, and the third tick's four new
    restarts make a loop that the last tick still reports.
    """
    up = _stamp(NOW - timedelta(hours=2))
    back = _stamp(NOW + timedelta(seconds=13))
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), _AWAY),  # Docker is away
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 1)),
            (timedelta(seconds=15), _running(back, 5)),
            (timedelta(seconds=45), _running(back, 5)),
        ],
        # Read 1: before Docker stopped. Read 2: the database is not up yet.
        _ScriptedSql(True, False),
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert [v.state for v in verdicts] == ["up", "unknown", "up", "up", "up"]
    assert verdicts[-1].stable is True, "Docker restarting itself is not a crash"
    assert "restart loop" not in dashboard.line(verdicts[-1])


def test_a_world_docker_is_restarting_while_docker_comes_back_is_starting_not_looping(
    tmp_path: Path,
) -> None:
    """The tick that lands in the world's back-off, right after Docker came back.

    Measured: five seconds after the daemon started, the world read `restarting`,
    exit 1, count 5. That is Docker bringing the stack back in the wrong order, and
    the tab must not call it a restart loop. Nor `stopped`: that would open the
    enable press (`_press_is_allowed`) under a world about to be running.

    Mutation: let `restarting` win over the grace, and this reads `restart_loop`.
    """
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), docker.ContainerState("restarting", "", 3)),
        ],
    )

    watch.tick()
    watch.tick()
    verdict = watch.tick()

    assert verdict.state == "starting"
    assert verdict.stable is False
    said = dashboard.line(verdict)
    assert "restart loop" not in said
    assert "Docker" in said


def test_a_real_crash_loop_after_docker_came_back_is_still_called_one(tmp_path: Path) -> None:
    """The grace forgives Docker's own restart, not a world that keeps dying after it.

    Measured with the same stand-in while the daemon stayed up: a container that
    exits 3 after four seconds went 0 → 1 → 2 → 3 → 4 → 5 → 6 restarts in forty
    seconds. Here the database never answers, so only the two-minute cap closes
    the window; past it, the count growing three times is a loop again.

    Mutation: make the grace never end, and the last tick reads `up`.
    """
    grace = dashboard.DOCKER_RESTORE_GRACE
    later = grace + timedelta(seconds=15)
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 2)),
            (grace + timedelta(seconds=5), docker.ContainerState("restarting", "", 4)),
            (later, _running(_stamp(NOW + later - timedelta(seconds=1)), 7)),
        ],
        _ScriptedSql(True, False, False),
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert verdicts[3].state == "starting", "still inside the window"
    assert verdicts[-1].state == "restart_loop"


def test_a_container_docker_said_was_missing_does_not_open_a_grace(tmp_path: Path) -> None:
    """`missing` is Docker ANSWERING (T95); it did not go away, so nothing is forgiven.

    Mutation: open the grace on any empty read, missing or not, and this reads `up`.
    """
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), docker.ContainerState(missing=True)),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=8)), 0)),
            (timedelta(seconds=15), _running(_stamp(NOW + timedelta(seconds=14)), 3)),
        ],
    )

    verdicts = [watch.tick() for _ in range(4)]

    assert verdicts[-1].state == "restart_loop"


def test_the_starting_line_says_docker_restarted_and_names_no_restart_loop() -> None:
    said = dashboard.line(dashboard.Verdict("starting", 4))
    assert said.startswith("starting")
    assert "Docker restarted" in said
    assert "restart loop" not in said


def test_strikes_from_before_docker_restarted_are_not_carried_into_the_new_run(
    tmp_path: Path,
) -> None:
    """Docker restarting itself ends every run, and its count with it (measured: 5 → 3 → 5).

    So two restarts seen before the outage are not two strikes against the run
    Docker started afterwards, even when the new count happens to read no lower and
    the drop `_restarted()` looks for never shows. One later restart is a hiccup.

    Mutation: clear the strikes only on a falling count, and the last tick reads
    `restart_loop` on one new restart.
    """
    up = _stamp(NOW - timedelta(hours=2))
    after = dashboard.DOCKER_RESTORE_GRACE + timedelta(seconds=30)
    young = _stamp(NOW + after - timedelta(seconds=3))
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), _running(up, 2)),
            (timedelta(seconds=10), _AWAY),
            (timedelta(seconds=15), _running(_stamp(NOW + timedelta(seconds=14)), 2)),
            (after, _running(young, 3)),
        ],
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert verdicts[-1].state == "up", "strikes from the run before Docker restarted counted"


def test_docker_silent_without_restarting_the_world_opens_no_grace(tmp_path: Path) -> None:
    """A Docker that went quiet and came back with the same run did not restart anything.

    Docker restarting itself starts every container again (measured: every
    container's `StartedAt` was new after `systemctl start docker`). A world whose
    run is the one from before the silence was not restored, so its restarts after
    are counted at once, and nothing reads "Docker restarted".

    Mutation: open the grace on any return, and the last tick reads `up`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    young = _stamp(NOW + timedelta(seconds=18))
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(up, 0)),
            (timedelta(seconds=20), _running(young, 3)),
        ],
        _ScriptedSql(True, False),  # its database not answering keeps any window open
    )

    verdicts = [watch.tick() for _ in range(4)]

    assert verdicts[-1].state == "restart_loop"


def test_the_window_closes_when_the_database_answers_and_not_before(tmp_path: Path) -> None:
    """Docker's restore race is over once the world's database answers, and only then.

    Codex adversarial review, 2026-10-05, round 1: a window that always runs its
    full length erases a slow loop's deaths inside it. Round 4: closing it once a
    run lasts from one tick to the next is no proof either, as a world can run a
    while waiting for its database and still die of it. So the window closes on
    the tick whose population read reached the database; every death after that is
    the world's own.

    Mutations: never close on the database's answer, and the last tick reads
    `up`; close on a run held across two ticks, and the fifth reads `restart_loop`.
    """
    held = _stamp(NOW + timedelta(seconds=9))
    again = _stamp(NOW + timedelta(seconds=24))
    young = _stamp(NOW + timedelta(seconds=38))
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(held, 1)),
            (timedelta(seconds=15), _running(held, 1)),  # held, database still down
            (timedelta(seconds=25), _running(again, 4)),  # died of it three times more
            (timedelta(seconds=30), _running(again, 4)),  # the database answers
            (timedelta(seconds=40), _running(young, 7)),  # the world's own deaths
        ],
        _ScriptedSql(True, False, False, False, True),
    )

    verdicts = [watch.tick() for _ in range(7)]

    assert [v.state for v in verdicts[:6]] == ["up", "unknown", "up", "up", "up", "up"]
    assert verdicts[-1].state == "restart_loop"


def test_a_missing_answer_after_docker_went_away_settles_the_outage(tmp_path: Path) -> None:
    """`missing` is Docker answering, so the outage before it is over and opens nothing later.

    Codex review and adversarial review, 2026-10-05, round 4: the flag set by the
    unreachable read outlived the `missing` answer, and the container recreated
    much later took its first run for Docker's restore.

    Mutation: keep the outage flag through a `missing` answer, and the last tick reads `up`.
    """
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), docker.ContainerState(missing=True)),
            (timedelta(minutes=5), _running(_stamp(NOW + timedelta(minutes=5)), 0)),
            (
                timedelta(minutes=5, seconds=10),
                _running(_stamp(NOW + timedelta(minutes=5, seconds=9)), 3),
            ),
        ],
        _ScriptedSql(True, False),  # its database not answering keeps any window open
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert verdicts[-1].state == "restart_loop"


def test_a_first_look_that_failed_is_no_evidence_that_docker_restarted(tmp_path: Path) -> None:
    """With no run seen before the silence, a run after it cannot be called a new one.

    Codex review, 2026-10-05: a dashboard whose very first read failed would take
    any later answer for Docker's restore and forgive a crash loop already going.

    Mutation: open the window when nothing was seen before, and the last tick reads `up`.
    """
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _AWAY),
            (timedelta(seconds=5), _running(_stamp(NOW + timedelta(seconds=4)), 0)),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 3)),
        ],
        _ScriptedSql(False),  # its database not answering keeps any window open
    )

    verdicts = [watch.tick() for _ in range(3)]

    assert verdicts[-1].state == "restart_loop"


def test_a_failed_read_that_was_not_docker_going_away_opens_no_window(tmp_path: Path) -> None:
    """Codex adversarial review, 2026-10-05: a read can fail while the daemon is up.

    Only a failure in which the CLI reached no daemon is evidence that Docker
    itself went away. Any other failed read, followed by a crash-looping world's
    next run, is that loop going on, and its restarts are counted.

    Mutation: let any failed read open the window, and the last tick reads `up`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), docker.ContainerState(said="unexpected EOF")),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 1)),
            (timedelta(seconds=15), _running(_stamp(NOW + timedelta(seconds=14)), 3)),
        ],
    )

    verdicts = [watch.tick() for _ in range(4)]

    assert verdicts[-1].state == "restart_loop"


def test_the_real_state_reader_carries_dockers_words_to_the_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the unpatched `container_state()`: only the docker CLI is stood in for.

    The window hangs on what Docker said when the read failed, so that has to
    survive the trip from the CLI's stderr to the dashboard.

    Mutation: drop stderr from the failed `ContainerState`, and the last tick
    reads `restart_loop`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    answers = [
        (0, f"running\t{up}\t0\n", ""),
        (1, "", _AWAY.said + "\n"),
        (0, f"running\t{_stamp(NOW + timedelta(seconds=9))}\t1\n", ""),
        (0, f"running\t{_stamp(NOW + timedelta(seconds=13))}\t5\n", ""),
    ]

    def run(
        cmd: list[str], cwd: object = None, timeout: object = None
    ) -> subprocess.CompletedProcess[str]:
        code, out, err = answers.pop(0)
        return subprocess.CompletedProcess(cmd, code, out, err)

    monkeypatch.setattr(docker.runner, "run", run)
    sql = _ScriptedSql(True, False, False)
    watch = dashboard.Dashboard(SPEC, WOTLK, _install(tmp_path), sql=sql, now=lambda: NOW)

    verdicts = [watch.tick() for _ in range(4)]

    assert [v.state for v in verdicts] == ["up", "unknown", "up", "up"]


def test_a_missing_answer_closes_a_window_already_open(tmp_path: Path) -> None:
    """A world container removed while the window is open: what comes next is a new container.

    Codex adversarial review, 2026-10-05, round 5: clearing only the outage flag
    on `missing` left an open window running, and a container recreated inside it
    had its crash loop forgiven.

    Mutation: leave the window open through a `missing` answer, and the last tick reads `up`.
    """
    watch, _clock = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 1)),
            (timedelta(seconds=15), docker.ContainerState(missing=True)),
            (timedelta(seconds=20), _running(_stamp(NOW + timedelta(seconds=19)), 0)),
            (timedelta(seconds=25), _running(_stamp(NOW + timedelta(seconds=24)), 3)),
        ],
        _ScriptedSql(True, False, False),
    )

    verdicts = [watch.tick() for _ in range(6)]

    assert verdicts[-1].state == "restart_loop"
