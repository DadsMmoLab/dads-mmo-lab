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
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from yulon import dashboard, docker
from yulon.catalog import catalog as catalog_module
from yulon.catalog import native

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
        daemon_of=lambda: "bridge-before",
        log_of=lambda _container, _since: "",
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
        daemon_of=lambda: "bridge-before",
        log_of=lambda _c, _since: BANNER,
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
        daemon_of=lambda: "bridge-before",
        log_of=lambda _c, _since: BANNER,
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
        daemon_of=lambda: "bridge-before",
        log_of=lambda _c, _since: BANNER,
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
        SPEC,
        WOTLK,
        server_dir,
        sql=_FakeSql(),
        state_of=lambda _c: _running(),
        daemon_of=lambda: "bridge-before",
        log_of=lambda _c, _since: BANNER,
        now=lambda: NOW,
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

_AWAY = docker.ContainerState()
"""A read that failed, as with docker.service stopped ("Cannot connect to the Docker daemon")."""


def _restart_at(seconds: float) -> Callable[[timedelta], str]:
    """The daemon's identity at each moment: Docker restarted `seconds` after NOW.

        Docker recreates its default bridge network on every start, so it answers with
        a new identity from then on.

    Measured on yulon-ubuntu, Docker 29.1.3: `docker network inspect bridge` gave a new
    `Id`, `Created` the daemon's start time, after every `systemctl restart docker`
    (7e1d2a79… created 20:01:55 → b10dec55… created 22:13:47), while `docker info`'s
    `ID` stayed the same.
    """
    return lambda at: "bridge-before" if at < timedelta(seconds=seconds) else "bridge-after"


RESTARTED = _restart_at(7)
"""Docker stopped after the tick at 5 s (which failed) and was back before the one at 10 s."""


def _stamp(at: datetime) -> str:
    return at.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


class _ScriptedSql(_FakeSql):
    """A database that is down (`False`) or answers (`True`), one entry per population read.

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
    daemons: Callable[[timedelta], str] = lambda _at: "bridge-before",
    logs: Callable[[timedelta, str], str] = lambda _at, _since: "",
) -> tuple[dashboard.Dashboard, list[str]]:
    """A dashboard ticked at the given offsets from NOW, one container state per tick.

    `daemons` gives the daemon's identity at each moment (offset from NOW); returns
    the dashboard and the list of identities it was handed. `logs` gives what the run
    that started at `since` has printed by each moment (T390).
    """
    clock = [NOW]
    remaining = list(script)
    handed: list[str] = []

    def state_of(_container: str) -> docker.ContainerState:
        offset, state = remaining.pop(0)
        clock[0] = NOW + offset
        return state

    def daemon_of() -> str:
        handed.append(daemons(clock[0] - NOW))
        return handed[-1]

    watch = dashboard.Dashboard(
        SPEC,
        WOTLK,
        _install(tmp_path),
        sql=sql if sql is not None else _FakeSql(),
        state_of=state_of,
        daemon_of=daemon_of,
        log_of=lambda _container, since: logs(clock[0] - NOW, since),
        now=lambda: clock[0],
    )
    return watch, handed


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

    Mutation: count restarts during the window, and the fourth tick's four new
    restarts make a loop that the last tick still reports.
    """
    up = _stamp(NOW - timedelta(hours=2))
    back = _stamp(NOW + timedelta(seconds=13))
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 1)),
            (timedelta(seconds=15), _running(back, 5)),
            (timedelta(seconds=45), _running(back, 5)),
        ],
        # Read 1: before Docker stopped. Read 2: the database is not up yet.
        _ScriptedSql(True, False),
        RESTARTED,
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

    Mutation: let `restarting` win over the window, and this reads `restart_loop`.
    """
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), docker.ContainerState("restarting", "", 3)),
        ],
        daemons=RESTARTED,
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
    """The window forgives Docker's own restart, not a world that keeps dying after it.

    Measured with the same stand-in while the daemon stayed up: a container that
    exits 3 after four seconds went 0 → 1 → 2 → 3 → 4 → 5 → 6 restarts in forty
    seconds. Here the database never answers, so only the two-minute cap closes
    the window; past it, the count growing three times is a loop again.

    Mutation: make the window never end, and the last tick reads `up`.
    """
    grace = dashboard.DOCKER_RESTORE_GRACE
    later = grace + timedelta(seconds=15)
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 2)),
            (grace + timedelta(seconds=5), docker.ContainerState("restarting", "", 4)),
            (later, _running(_stamp(NOW + later - timedelta(seconds=1)), 7)),
        ],
        _ScriptedSql(True, False, False),
        RESTARTED,
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert verdicts[3].state == "starting", "still inside the window"
    assert verdicts[-1].state == "restart_loop"


def test_a_crash_while_docker_could_not_be_asked_is_still_a_crash(tmp_path: Path) -> None:
    """The same daemon before and after the silence restarted nothing; the world did.

    Codex adversarial review, 2026-10-05, round 6: a crash-looping world can start a
    new run while the CLI cannot reach the daemon for a moment, and a new
    `StartedAt` after the silence was taken for Docker's restore. Only the daemon's
    own identity changing says Docker restarted.

    Mutation: open the window on any return from a silence, and the last tick reads `up`.
    """
    watch, handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 2)),
            (timedelta(seconds=15), _running(_stamp(NOW + timedelta(seconds=14)), 3)),
        ],
        _ScriptedSql(True, False),
    )

    verdicts = [watch.tick() for _ in range(4)]

    assert set(handed) == {"bridge-before"}, "the same daemon throughout"
    assert verdicts[-1].state == "restart_loop"


def test_a_daemon_whose_identity_cannot_be_read_opens_no_window(tmp_path: Path) -> None:
    """Without the daemon's identity there is no evidence Docker restarted: count as before.

    Mutation: take an unreadable identity for a changed one, and the last tick reads `up`.
    """
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 2)),
            (timedelta(seconds=15), _running(_stamp(NOW + timedelta(seconds=14)), 3)),
        ],
        _ScriptedSql(True, False),
        lambda at: "bridge-before" if at < timedelta(seconds=7) else "",
    )

    verdicts = [watch.tick() for _ in range(4)]

    assert verdicts[-1].state == "restart_loop"


def test_a_first_look_that_failed_is_no_evidence_that_docker_restarted(tmp_path: Path) -> None:
    """With no daemon seen before the silence, the one after it cannot be called a new one.

    Codex review, 2026-10-05: a dashboard whose very first read failed would take
    any later answer for Docker's restore and forgive a crash loop already going.

    Mutation: open the window when nothing was seen before, and the last tick reads `up`.
    """
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _AWAY),
            (timedelta(seconds=5), _running(_stamp(NOW + timedelta(seconds=4)), 0)),
            (timedelta(seconds=10), _running(_stamp(NOW + timedelta(seconds=9)), 3)),
        ],
        _ScriptedSql(False),
    )

    verdicts = [watch.tick() for _ in range(3)]

    assert verdicts[-1].state == "restart_loop"


def test_a_container_docker_said_was_missing_does_not_open_a_window(tmp_path: Path) -> None:
    """`missing` is Docker ANSWERING (T95): whatever runs after it is a new container.

    Codex review and adversarial review, 2026-10-05, round 4: the outage outlived
    the `missing` answer, and the container recreated much later took its first
    run for Docker's restore. Round 7's identity reads make the same mistake
    another way: the daemon that restarted during the silence differs from the
    one seen before it, and the recreated container's first growth would open a
    window, unless the `missing` answer makes the daemon answering then the one
    to compare with.

    Mutation: keep the daemon seen before a `missing` answer, and the last tick reads `up`.
    """
    watch, _handed = _clocked(
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
        _ScriptedSql(True, False),
        RESTARTED,
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert verdicts[-1].state == "restart_loop"


def test_a_missing_answer_closes_a_window_already_open(tmp_path: Path) -> None:
    """A world container removed while the window is open: what comes next is a new container.

    Codex adversarial review, 2026-10-05, round 5: an open window kept running
    through `missing`, and a container recreated inside it had its crash loop forgiven.

    Mutation: leave the window open through a `missing` answer, and the last tick reads `up`.
    """
    watch, _handed = _clocked(
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
        RESTARTED,
    )

    verdicts = [watch.tick() for _ in range(6)]

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
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), _running(up, 2)),
            (timedelta(seconds=10), _AWAY),
            (timedelta(seconds=15), _running(_stamp(NOW + timedelta(seconds=14)), 2)),
            (after, _running(young, 3)),
        ],
        daemons=_restart_at(12),
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert verdicts[-1].state == "up", "strikes from the run before Docker restarted counted"


def test_the_window_closes_when_the_database_answers_and_not_before(tmp_path: Path) -> None:
    """Docker's restore race is over once the world's database answers, and only then.

    Codex adversarial review, 2026-10-05, round 1: a window that always runs its
    full length erases a slow loop's deaths inside it. Round 4: closing it once a
    run lasts from one tick to the next is no proof either, as a world can run a
    while waiting for its database and still die of it. So the window closes on
    the tick whose population read reached the database; every death after that is
    the world's own.

    Mutation: never close on the database's answer, and the last tick reads `up`.
    """
    held = _stamp(NOW + timedelta(seconds=9))
    again = _stamp(NOW + timedelta(seconds=24))
    young = _stamp(NOW + timedelta(seconds=38))
    watch, _handed = _clocked(
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
        RESTARTED,
    )

    verdicts = [watch.tick() for _ in range(7)]

    assert [v.state for v in verdicts[:6]] == ["up", "unknown", "up", "up", "up", "up"]
    assert verdicts[-1].state == "restart_loop"


def test_the_real_readers_ask_docker_for_the_daemons_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The unpatched `container_state()` and `daemon_identity()`; only the CLI is stood in for.

    Mutation: let `daemon_identity()` answer nothing, and the last tick reads
    `restart_loop`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    worlds = [
        (0, f"running\t{up}\t0\n", ""),
        (1, "", "Cannot connect to the Docker daemon at unix:///var/run/docker.sock.\n"),
        (0, f"running\t{_stamp(NOW + timedelta(seconds=9))}\t1\n", ""),
        (0, f"running\t{_stamp(NOW + timedelta(seconds=13))}\t5\n", ""),
    ]
    bridges = ["7e1d2a79ddbf\n", "b10dec5534ec\n", "b10dec5534ec\n"]
    asked: list[list[str]] = []

    def run(
        cmd: list[str], cwd: object = None, timeout: object = None
    ) -> subprocess.CompletedProcess[str]:
        if "logs" in cmd:
            return subprocess.CompletedProcess(cmd, 0, BANNER, "")
        if "network" in cmd:
            asked.append(cmd[cmd.index("network") :])
            return subprocess.CompletedProcess(cmd, 0, bridges.pop(0), "")
        code, out, err = worlds.pop(0)
        return subprocess.CompletedProcess(cmd, code, out, err)

    monkeypatch.setattr(docker.runner, "run", run)
    sql = _ScriptedSql(True, False, False)
    watch = dashboard.Dashboard(SPEC, WOTLK, _install(tmp_path), sql=sql, now=lambda: NOW)

    verdicts = [watch.tick() for _ in range(4)]

    assert asked == [["network", "inspect", "bridge", "--format", "{{.Id}}"]] * 3
    assert [v.state for v in verdicts] == ["up", "unknown", "up", "up"]


def test_a_docker_restart_between_two_ticks_is_still_told_by_the_daemon(tmp_path: Path) -> None:
    """No read failed, but the count grew: the daemon is asked who it is before it counts.

    Codex review, 2026-10-05, round 7: a Docker that stops and starts between two
    five-second ticks leaves no failed read, and its restore race was counted as
    crashes again.

    Mutation: ask the daemon only on the first look, and the last tick reads `restart_loop`.
    """
    back = _stamp(NOW + timedelta(seconds=8))
    watch, handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _running(_stamp(NOW + timedelta(seconds=4)), 1)),
            (timedelta(seconds=10), _running(back, 5)),
            (timedelta(seconds=15), _running(back, 5)),
        ],
        _ScriptedSql(True, False, False),
        _restart_at(3),
    )

    verdicts = [watch.tick() for _ in range(4)]

    assert handed[:2] == ["bridge-before", "bridge-after"]
    assert [v.state for v in verdicts] == ["up", "up", "up", "up"]
    assert verdicts[-1].stable is True


def test_an_identity_read_that_failed_keeps_the_one_seen_before(tmp_path: Path) -> None:
    """One unreadable identity is no identity: the last one read stays what Docker was.

    Codex adversarial review, 2026-10-05, round 7: storing the blank answer made
    every later daemon restart compare against nothing, so none opened a window.

    Mutation: store a blank identity, and the last tick reads `restart_loop`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), _running(up, 0)),  # the identity read fails here
            (timedelta(seconds=15), _AWAY),
            (timedelta(seconds=20), _running(_stamp(NOW + timedelta(seconds=19)), 1)),
            (timedelta(seconds=25), _running(_stamp(NOW + timedelta(seconds=23)), 4)),
        ],
        _ScriptedSql(True, True, False, False),
        # Blank while Docker is down the first time; back with a new bridge the second.
        lambda at: {0: "bridge-before", 1: ""}.get(int(at.total_seconds() // 8), "bridge-after"),
    )

    verdicts = [watch.tick() for _ in range(6)]

    assert verdicts[-1].state == "up"


def test_the_first_answer_after_a_silence_asks_the_daemon_even_with_no_new_restart(
    tmp_path: Path,
) -> None:
    """Docker resets the count on its restore, so the first answer can read lower than before.

    Measured: 5 → 3 (`restarting`) → 5 across `systemctl restart docker`, so the
    first answer can even read the count it had before. With no change to prompt
    it, the silence itself must, or that first tick says "restart loop" about
    Docker's own restart.

    Mutation: ask the daemon only on the first look, and this reads `restart_loop`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 3)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), docker.ContainerState("restarting", "", 3)),
        ],
        daemons=RESTARTED,
    )

    verdicts = [watch.tick() for _ in range(3)]

    assert verdicts[-1].state == "starting"


def test_a_docker_restart_between_two_ticks_that_lowered_the_count_is_told_too(
    tmp_path: Path,
) -> None:
    """Docker's restore resets the count, so between two ticks it can read lower (5 → 3).

    Codex review, 2026-10-05, round 8: only a growing count asked the daemon, and
    the tick that found the world `restarting` at a lower count called it a loop.

    Mutation: ask the daemon only on the first look, and this reads `restart_loop`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 5)),
            (timedelta(seconds=5), docker.ContainerState("restarting", "", 3)),
        ],
        daemons=_restart_at(3),
    )

    verdicts = [watch.tick() for _ in range(2)]

    assert verdicts[-1].state == "starting"


def test_a_clean_docker_restart_is_noticed_by_the_new_run_not_by_a_later_crash(
    tmp_path: Path,
) -> None:
    """Docker restarted between ticks and the world came back at once, count 0 as before.

    Codex adversarial review, 2026-10-05, round 9: with nothing failed and no count
    moved, the new daemon went unasked until the world's first real crash, which
    then opened a window and was forgiven. Docker's restore starts every container
    again, so the new run is when to ask.

    Mutation: ask the daemon only on the first look, and the last tick reads `up`.
    """
    up = _stamp(NOW - timedelta(hours=2))
    back = _stamp(NOW + timedelta(seconds=4))
    watch, _handed = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(up, 0)),
            (timedelta(seconds=5), _running(back, 0)),  # Docker restarted at 3 s
            (timedelta(seconds=10), _running(back, 0)),
            (timedelta(seconds=15), _running(_stamp(NOW + timedelta(seconds=14)), 1)),
            (timedelta(seconds=20), _running(_stamp(NOW + timedelta(seconds=19)), 2)),
            (timedelta(seconds=25), _running(_stamp(NOW + timedelta(seconds=24)), 3)),
        ],
        daemons=_restart_at(3),
    )

    verdicts = [watch.tick() for _ in range(6)]

    assert verdicts[-1].state == "restart_loop"


# ------------------------------------------- T390: a loop that was fixed reads up again

BANNER = "AzerothCore rev. 1 ready..."


def _said_ready_from(at: timedelta, run: str) -> Callable[[timedelta, str], str]:
    """The log of run `run`: loading, then the ready banner from `at` on (offset from NOW)."""

    def log(now: timedelta, since: str) -> str:
        if since != run:
            return "Loading maps..."
        return "Loading maps...\n" + (BANNER if now >= at else "")

    return log


def _a_loop_then(
    *after: tuple[timedelta, docker.ContainerState],
) -> list[tuple[timedelta, docker.ContainerState]]:
    """Three new restarts in the first two ticks -- a loop -- and then `after`."""
    long_ago = _stamp(NOW - timedelta(hours=2))
    return [
        (timedelta(0), _running(long_ago, 10)),
        (timedelta(seconds=5), _running(_stamp(NOW + timedelta(seconds=4)), 13)),
        *after,
    ]


GRACE = timedelta(seconds=native.READY_GRACE_SECONDS)


def test_a_loop_that_was_fixed_reads_up_once_its_world_said_ready_and_stayed_up(
    tmp_path: Path,
) -> None:
    """T390, from the T306 live check on m910q (2026-10-05).

    The loop was fixed and the world ran with 500 bots, and the line still read
    "restart loop — 13 restarts, this run up 9m" until the run reached ten
    minutes. The count did not go back (the run that survived was one more of
    the policy's restarts), so nothing ended the loop but the settle rule.

    Now the start rule ends it: the run's own log says ready, and the same run
    is still up `READY_GRACE_SECONDS` later. It reads up, with the crash-loop
    note, and is not called steady until `SETTLED_AFTER` as before.

    Mutation: drop the ready check from `tick()`, and the last tick reads
    `restart_loop`.
    """
    fixed = _stamp(NOW + timedelta(seconds=9))
    said_ready = timedelta(seconds=40)
    watch, _ = _clocked(
        tmp_path,
        _a_loop_then(
            (timedelta(seconds=10), _running(fixed, 14)),
            (said_ready, _running(fixed, 14)),
            (said_ready + GRACE - timedelta(seconds=5), _running(fixed, 14)),
            (said_ready + GRACE, _running(fixed, 14)),
        ),
        logs=_said_ready_from(said_ready, fixed),
    )

    verdicts = [watch.tick() for _ in range(6)]

    assert [v.state for v in verdicts[1:5]] == ["restart_loop"] * 4, "up only after the watch"
    last = verdicts[-1]
    assert last.state == "up"
    assert last.after_a_loop is True and last.stable is False
    said = dashboard.line(last)
    assert "restart loop" not in said and "crash loop" in said, said


def test_a_run_that_said_ready_and_then_died_does_not_end_the_loop(tmp_path: Path) -> None:
    """The banner is not the verdict (T71): the next run starting is a crash, not a recovery."""
    first = _stamp(NOW + timedelta(seconds=9))
    again = _stamp(NOW + timedelta(seconds=60))
    watch, _ = _clocked(
        tmp_path,
        _a_loop_then(
            (timedelta(seconds=10), _running(first, 14)),
            (timedelta(seconds=40), _running(first, 14)),
            (timedelta(seconds=65), _running(again, 15)),
            (GRACE + timedelta(seconds=45), _running(again, 15)),
        ),
        logs=_said_ready_from(timedelta(seconds=40), first),
    )

    verdicts = [watch.tick() for _ in range(6)]

    assert verdicts[-1].state == "restart_loop"


def test_the_world_s_log_is_read_only_while_a_loop_is_current_and_once_per_run(
    tmp_path: Path,
) -> None:
    """A tick is one inspect and one SQL read; the log is read only until a run says ready."""
    fixed = _stamp(NOW + timedelta(seconds=9))
    reads: list[timedelta] = []
    ready = _said_ready_from(timedelta(seconds=10), fixed)

    def logs(at: timedelta, since: str) -> str:
        reads.append(at)
        return ready(at, since)

    healthy, _ = _clocked(
        tmp_path,
        [(timedelta(seconds=n), _running(restarts=0)) for n in (0, 5, 10)],
        logs=logs,
    )
    for _ in range(3):
        healthy.tick()
    assert reads == [], "a settled server's log was read"

    looping, _ = _clocked(
        tmp_path,
        _a_loop_then(
            *[(timedelta(seconds=n), _running(fixed, 14)) for n in (10, 20, 30, 40)],
        ),
        logs=logs,
    )
    for _ in range(6):
        looping.tick()
    # 5 s: the loop's own run, still loading. 10 s: the fixed run, ready. Then no more.
    assert reads == [timedelta(seconds=5), timedelta(seconds=10)], "read again after ready"


def test_a_loop_caught_inside_the_docker_restore_window_gets_the_note_once_fixed(
    tmp_path: Path,
) -> None:
    """T390 item 2, from the T306 live check: no "restarted after a crash loop" note.

    A world that crashes after Docker came back is `starting` inside the window
    and `restart_loop` (by its `restarting` status) after it, but its restarts
    inside the window are never strikes, so the loop was never recorded and the
    run that was fixed read steady at once. Docker still backing off the same
    dead run a tick later is a loop on Docker's own word.

    Mutation: drop the two-`restarting`-ticks rule, and the last tick's
    `after_a_loop` is False (and `stable` True).
    """
    # The window opens at the 10 s tick, the first new run after Docker came back.
    closed = timedelta(seconds=10) + dashboard.DOCKER_RESTORE_GRACE
    dead = _stamp(NOW + closed)
    fixed = _stamp(NOW + closed + timedelta(seconds=14))
    said_ready = closed + timedelta(seconds=30)
    watch, _ = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            # Its restarts all inside the window: none of them is a strike.
            (timedelta(seconds=10), docker.ContainerState("restarting", _stamp(NOW), 8)),
            (closed + timedelta(seconds=5), docker.ContainerState("restarting", dead, 8)),
            (closed + timedelta(seconds=10), docker.ContainerState("restarting", dead, 8)),
            (closed + timedelta(seconds=15), _running(fixed, 9)),
            (said_ready, _running(fixed, 9)),
            (said_ready + GRACE, _running(fixed, 9)),
        ],
        daemons=RESTARTED,
        logs=_said_ready_from(said_ready, fixed),
    )

    verdicts = [watch.tick() for _ in range(8)]

    assert verdicts[2].state == "starting"
    assert verdicts[3].state == verdicts[4].state == "restart_loop"
    last = verdicts[-1]
    assert last.state == "up"
    assert last.after_a_loop is True and last.stable is False


def test_one_restarting_tick_outside_the_window_is_not_yet_a_recorded_loop(
    tmp_path: Path,
) -> None:
    """One back-off seen once is one crash: the run after it is up and steady."""
    long_ago = _stamp(NOW - timedelta(hours=2))
    back = _stamp(NOW + timedelta(seconds=6))
    watch, _ = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(long_ago, 0)),
            (timedelta(seconds=5), docker.ContainerState("restarting", long_ago, 1)),
            (timedelta(seconds=10), _running(back, 1)),
        ],
    )

    verdicts = [watch.tick() for _ in range(3)]

    assert verdicts[1].state == "restart_loop"
    assert verdicts[-1].state == "up" and verdicts[-1].after_a_loop is False


@pytest.mark.parametrize("entry", catalog_module.load_catalog().games, ids=lambda e: e.id)
def test_every_game_s_dashboard_knows_its_own_ready_marker(
    tmp_path: Path, entry: catalog_module.CatalogEntry
) -> None:
    """Without it a fixed loop ends only at `SETTLED_AFTER` again, silently (T390)."""
    block = entry.install.native
    assert block is not None
    banner = dashboard._ready_banner(entry)
    assert banner is not None
    assert banner.pattern == native.ready_spec_for(entry, block.ready).world


def test_docker_backing_off_inside_the_restore_window_is_not_recorded_as_a_loop(
    tmp_path: Path,
) -> None:
    """T306's race, seen twice on one dead run: Docker's restore, not a crash loop (T390).

    Mutation: take `not restoring` off the two-`restarting`-ticks rule, and the
    healthy run after it carries the crash-loop note and is not steady.
    """
    dead = _stamp(NOW + timedelta(seconds=9))
    back = _stamp(NOW + timedelta(seconds=19))
    watch, _ = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(restarts=0)),
            (timedelta(seconds=5), _AWAY),
            (timedelta(seconds=10), docker.ContainerState("restarting", dead, 4)),
            (timedelta(seconds=15), docker.ContainerState("restarting", dead, 4)),
            (timedelta(seconds=20), _running(back, 5)),
        ],
        daemons=RESTARTED,
    )

    verdicts = [watch.tick() for _ in range(5)]

    assert [v.state for v in verdicts[2:4]] == ["starting", "starting"]
    assert verdicts[-1].state == "up"
    assert verdicts[-1].after_a_loop is False and verdicts[-1].stable is True


def test_a_world_that_crashes_again_after_it_recovered_is_a_loop_again_at_once(
    tmp_path: Path,
) -> None:
    """Cold review: ending the loop zeroed the strikes, so the next crash read up.

    A world that dies a couple of minutes after ready (bot spawn, a first login)
    loads past Docker's back-off reset, so only the strikes can catch it, and three
    fresh ones took three more cycles while the badge said REALM ONLINE.

    Mutation: zero `_strikes` where the ready rule ends the loop, and the tick
    after the crash reads `up`.
    """
    fixed = _stamp(NOW + timedelta(seconds=9))
    again = _stamp(NOW + timedelta(seconds=204))
    said_ready = timedelta(seconds=40)
    watch, _ = _clocked(
        tmp_path,
        _a_loop_then(
            (timedelta(seconds=10), _running(fixed, 14)),
            (said_ready, _running(fixed, 14)),
            (said_ready + GRACE, _running(fixed, 14)),
            (timedelta(seconds=205), _running(again, 15)),
        ),
        logs=_said_ready_from(said_ready, fixed),
    )

    verdicts = [watch.tick() for _ in range(6)]

    assert verdicts[4].state == "up", "recovered"
    assert verdicts[5].state == "restart_loop", "crashed again: a loop, not up"


def test_a_running_world_is_not_ready_until_its_log_says_so(tmp_path: Path) -> None:
    """T451: `docker ps` says the world runs seconds before it can take a login.

    The first tick of a run reads up with `ready` False and says "starting", and
    the tick after the run's own log printed the ready marker reads ready.

    Mutation: leave `ready` at its default in `_with_population()`, and the first
    tick reads ready.
    """
    run = _stamp(NOW - timedelta(seconds=3))
    watch, _ = _clocked(
        tmp_path,
        [
            (timedelta(0), _running(run)),
            (timedelta(seconds=30), _running(run)),
        ],
        logs=_said_ready_from(timedelta(seconds=20), run),
    )

    loading, loaded = watch.tick(), watch.tick()

    assert loading.state == "up" and loading.ready is False
    assert dashboard.line(loading).startswith("starting")
    assert loaded.ready is True and dashboard.line(loaded).startswith("up")


def test_a_run_that_said_ready_is_not_asked_again(tmp_path: Path) -> None:
    """The log is read until the marker is seen, and not on the healthy ticks after."""
    run = _stamp(NOW - timedelta(minutes=3))
    asked: list[str] = []
    watch = dashboard.Dashboard(
        SPEC,
        WOTLK,
        _install(tmp_path),
        sql=_FakeSql(),
        state_of=lambda _container: _running(run),
        daemon_of=lambda: "bridge-before",
        log_of=lambda _container, since: asked.append(since) or BANNER,
        now=lambda: NOW,
    )

    assert [watch.tick().ready for _ in range(3)] == [True] * 3
    assert len(asked) == 1


def test_a_run_past_the_settle_time_is_ready_without_its_marker(tmp_path: Path) -> None:
    """A rotated or unreadable log must not hold the header at STARTING for good.

    Mutation: drop the `SETTLED_AFTER` clause in `_with_population()`, and this reads not ready.
    """
    run = _stamp(NOW - dashboard.SETTLED_AFTER - timedelta(seconds=1))
    watch, _ = _clocked(
        tmp_path, [(timedelta(0), _running(run))], logs=lambda _now, _since: "no marker here"
    )

    assert watch.tick().ready is True


def test_a_bots_table_missing_for_good_warns_once_on_the_real_tick_not_every_five_seconds(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """T437: the uptime the dashboard already measures decides young (info) from old (warn)."""

    class _NoTable:
        def query(self, db: str, statement: str) -> str:
            raise RuntimeError("ERROR 1146 (42S02): Table 'acore_playerbots.x' doesn't exist")

    young = _running("2026-09-06T17:59:00.000000000Z")  # up 1 minute
    old = _running("2026-09-06T17:50:00.000000000Z")  # up 10 minutes
    dash = dashboard.Dashboard(
        SPEC,
        WOTLK,
        _install(tmp_path),
        sql=_NoTable(),
        state_of=lambda _c: states.pop(0),
        daemon_of=lambda: "bridge-before",
        log_of=lambda _c, _s: "",
        now=lambda: NOW,
    )
    restarted = _running("2026-09-06T17:48:00.000000000Z")  # a new run, 12 minutes up
    states = [young, young, old, old, old, old, restarted, restarted]
    with caplog.at_level("INFO"):
        for _ in range(2):
            dash.tick()
        assert not [r for r in caplog.records if r.levelname == "WARNING"]
        for _ in range(4):
            dash.tick()
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1
    with caplog.at_level("INFO"):
        for _ in range(2):
            dash.tick()
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 2


# ------------------------------------------- T555 T5: "Unbound loaded" on the Server tab

UNBOUND = catalog_module.load_catalog().get("wow-unbound")
UNBOUND_LOG = (
    "[UNBOUND] free reagents: off\n"
    "[UNBOUND] instant summons: off\n"
    "[UNBOUND] Character cleanup covers: characters.\n"
    "[UNBOUND] Prereq map built.\n"
    "[dml_autobuff] off (Unbound.AutoBuff = 0)\n"
    "AzerothCore rev. 1 ready...\n"
)
GOOD_LINE = "Unbound loaded: Mentor in 9 places; free reagents off, instant summons off, #buffs off"
CONF_OFF = "Unbound.ReagentFree = 0\nUnbound.InstantSummons = 0\nUnbound.AutoBuff = 0\n"


class _UnboundSql(_FakeSql):
    """The population answer, the tables question and the counts of an Unbound world database."""

    def __init__(self) -> None:
        super().__init__()
        checks = UNBOUND.install.native.azerothcore.sql_checks  # type: ignore[union-attr]
        self.counts = {c.table: c.at_least for c in checks}
        self.tables_asked = 0
        self.down: str | None = None

    def query(self, db: str, statement: str) -> str:
        if db == "characters":
            return super().query(db, statement)
        if self.down is not None:
            raise RuntimeError(self.down)
        if "information_schema.tables" in statement:
            self.tables_asked += 1
            return "".join(f"acore_world\t{table}\n" for table in self.counts)
        for table, count in self.counts.items():
            if f".`{table}`" in statement:
                return f"{count}\n"
        raise AssertionError(statement)


def _unbound_watch(
    tmp_path: Path,
    sql: _UnboundSql,
    *,
    runs: list[str] | None = None,
    log: str = UNBOUND_LOG,
    conf: str | None = CONF_OFF,
) -> dashboard.Dashboard:
    """An Unbound dashboard on a run three minutes old; each tick reads the next of `runs`."""
    server = _install(tmp_path)
    if conf is not None:
        file = server / "env/dist/etc/modules/mod_unbound.conf"
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(conf, encoding="utf-8")
    stamps = list(runs or [_stamp(NOW - timedelta(minutes=3))])
    return dashboard.Dashboard(
        UNBOUND.container_spec(),
        UNBOUND,
        server,
        sql=sql,
        state_of=lambda _c: _running(stamps[0] if len(stamps) == 1 else stamps.pop(0)),
        daemon_of=lambda: "bridge-before",
        log_of=lambda _c, _since: log,
        now=lambda: NOW,
    )


def test_an_unbound_server_that_loaded_says_so_after_its_up_line(tmp_path: Path) -> None:
    verdict = _unbound_watch(tmp_path, _UnboundSql()).tick()

    assert verdict.module_line == GOOD_LINE
    assert dashboard.line(verdict).endswith(f" · {GOOD_LINE}")
    assert dashboard.line(verdict).startswith("up — 3 players, 497 bots")


def test_a_server_with_no_health_block_has_no_module_line_and_asks_nothing_more(
    tmp_path: Path,
) -> None:
    sql = _FakeSql()

    verdict = _watch(tmp_path, [_running()], sql).tick()

    assert verdict.module_line == ""
    assert all("information_schema" not in s for s in sql.statements)


def test_the_module_is_asked_once_per_run_and_again_after_a_restart(tmp_path: Path) -> None:
    sql = _UnboundSql()
    first, second = _stamp(NOW - timedelta(minutes=3)), _stamp(NOW - timedelta(minutes=1))
    watch = _unbound_watch(tmp_path, sql, runs=[first, first, first, second, second])

    lines = [watch.tick().module_line for _ in range(5)]

    assert lines == [GOOD_LINE] * 5
    assert sql.tables_asked == 2, "once for each run"


def test_a_read_that_failed_is_not_kept_so_the_next_tick_asks_again(tmp_path: Path) -> None:
    sql = _UnboundSql()
    watch = _unbound_watch(tmp_path, sql)
    sql.down = "connection refused"

    failed = watch.tick()
    sql.down = None
    healed = watch.tick()

    assert failed.module_line.startswith("Unbound could not be checked:")
    assert not any(ch.isdigit() for ch in failed.module_line), "a number from nowhere"
    assert healed.module_line == GOOD_LINE


def test_a_world_that_has_not_said_ready_is_not_asked_about_its_module(tmp_path: Path) -> None:
    sql = _UnboundSql()
    run = _stamp(NOW - timedelta(seconds=20))  # younger than the settle time, no ready line yet
    watch = _unbound_watch(tmp_path, sql, runs=[run], log="[UNBOUND] Prereq map built.\n")

    verdict = watch.tick()

    assert verdict.ready is False and verdict.module_line == ""
    assert sql.tables_asked == 0


def test_a_switch_the_log_never_said_reads_not_said_and_a_changed_file_says_when(
    tmp_path: Path,
) -> None:
    log = UNBOUND_LOG.replace("[UNBOUND] instant summons: off\n", "")
    conf = CONF_OFF.replace("Unbound.ReagentFree = 0", "Unbound.ReagentFree = 1")

    line = _unbound_watch(tmp_path, _UnboundSql(), log=log, conf=conf).tick().module_line

    assert "free reagents off (on at the next start)" in line
    assert "instant summons not said" in line and "instant summons off" not in line


def test_missing_tables_reach_the_line_with_no_traceback(tmp_path: Path) -> None:
    sql = _UnboundSql()
    del sql.counts["unbound_milestones"]

    verdict = _unbound_watch(tmp_path, sql).tick()

    assert verdict.module_line.startswith("Unbound tables missing: unbound_milestones.")
    assert dashboard.line(verdict).count("Unbound") == 1


def test_the_health_comes_from_the_catalog_block_not_from_the_entrys_id(tmp_path: Path) -> None:
    """A second server that carries the module and the block gets the sentence under any id."""
    scratch = UNBOUND.model_copy(update={"id": "wow-unbound-scratch"})
    server = _install(tmp_path)
    watch = dashboard.Dashboard(
        scratch.container_spec(),
        scratch,
        server,
        sql=_UnboundSql(),
        state_of=lambda _c: _running(_stamp(NOW - timedelta(minutes=3))),
        daemon_of=lambda: "bridge-before",
        log_of=lambda _c, _since: UNBOUND_LOG,
        now=lambda: NOW,
    )

    assert watch.tick().module_line.startswith("Unbound loaded: Mentor in 9 places")
