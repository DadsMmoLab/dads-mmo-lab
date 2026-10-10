"""A stop lets the world server save every character before it is ever killed.

Live, yulon-ubuntu, 2026-10-05 (WotLK, 500 bots): Yu'lon's Stop ended with ac-worldserver exit 137.
The world had logged `Closing down DatabasePool 'acore_characters'. Waiting for 2294 queries to
finish...` and was still writing them when `compose stop -t 300` ran out and Docker SIGKILLed it.
AzerothCore drains that queue on a SIGTERM (`DatabaseWorkerPool::Close()` -> `_queue->Shutdown()`,
the worker finishes the queue before the join), so the save was going fine -- just slower than any
fixed number. The same box, the same ~2200 queued saves, drained in 9 s to 165 s across ten clean
stops in its own log, 150 s on a `server shutdown` the same night, and more than 300 s on the one
that was killed.

So the world is signalled on its own and waited for while it is still writing (its network
traffic, the only traffic it has left once the players are gone, still moving). It is killed only
when it has written nothing for `WORLD_SAVE_STALL_SECONDS`, or after `WORLD_SAVE_CEILING_SECONDS`,
and either is said plainly. Every test goes through a real stop path with only `runner.run` faked.
"""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from yulon import docker, runner
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller

CATALOG = load_catalog()
STARTED = "2026-10-05T22:13:10.000000000Z"
RESTARTED = "2026-10-05T22:20:00.000000000Z"

NET_DEV = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop "
    "fifo colls carrier compressed\n"
    "    lo:  {lo} 10 0 0 0 0 0 0  {lo} 10 0 0 0 0 0 0\n"
    "  eth0: {rx} 900 0 0 0 0 0 0 {tx} 800 0 0 0 0 0 0\n"
)
"""`/proc/net/dev` as the worldserver's own namespace shows it (read on the box, 2026-10-05)."""


def _net(tx: int, rx: int = 1000, lo: int = 0) -> str:
    return NET_DEV.format(tx=tx, rx=rx, lo=lo)


def _done(stdout: str = "", returncode: int = 0, stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class _Docker:
    """`runner.run` for one install whose world saves on the test's script after a SIGTERM.

    `traffic` is what each look at a signalled world finds in its `/proc/net/dev`: a byte count,
    or None for an `exec` that fails. When it runs out the world has finished and exits 0 --
    unless `exits` is False, when the last frame repeats for as long as the world is waited on.
    Each look advances the clock by `step` seconds. `events` is the order of everything that
    signals a container, which is the claim under test.
    """

    def __init__(
        self,
        game: str,
        traffic: list[int | None],
        *,
        exits: bool = True,
        step: float = 2.0,
        restart_at: int | None = None,
    ) -> None:
        self.spec = CATALOG.get(game).container_spec()
        self.traffic = traffic
        self.exits = exits
        self.step = step
        self.restart_at = restart_at
        self.now = 0.0
        self.looks = 0
        self.termed = False
        self.started = STARTED
        self.events: list[str] = []
        self.running = {self.spec.db, self.spec.auth, self.spec.world}
        self.on_look: object = None
        self.term_refused = ""
        self.blind_looks: set[int] = set()
        """Looks at which `docker inspect` does not answer."""
        self.termed_at: float | None = None
        """The clock when the world was signalled."""
        self.exit_code: str = "0"
        """What `docker inspect --format {{.State.ExitCode}}` answers once the world exited."""
        self.log_tail = "Halting process...\n"
        self.working_dir = ""
        """The world's compose working-dir label (T496), for telling two installs apart."""

    def _world_look(self) -> subprocess.CompletedProcess:
        world = self.spec.world
        if world not in self.running:
            return _done(f"exited\t{self.started}\t0\n")
        if self.termed:
            self.looks += 1
            self.now += self.step
            assert self.looks <= 2000, "the wait never ended"
            if callable(self.on_look):
                self.on_look()
            if self.looks in self.blind_looks:
                return _done(returncode=1, stderr="Cannot connect to the Docker daemon")
            if self.restart_at is not None and self.looks >= self.restart_at:
                self.started = RESTARTED
                self.termed = False
            elif self.looks > len(self.traffic) and self.exits:
                self.running.discard(world)
                self.events.append("world exited by itself")
                return _done(f"exited\t{self.started}\t0\n")
        return _done(f"running\t{self.started}\t0\n")

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess:
        verb = cmd[1:]
        world = self.spec.world
        if verb[:3] == ["compose", "config", "--format"]:
            return _done('{"name": "t384-server"}')
        if verb[:2] in (["compose", "stop"], ["compose", "down"]):
            named = {a for a in verb[2:] if not a.startswith("-") and not a.isdigit()}
            targets = named or set(self.running)
            self.events.append(
                f"{' '.join(verb[:2])} (world {'up' if world in self.running else 'down'})"
            )
            if world in targets and world in self.running:
                self.events.append("world stopped by the grace")
            self.running -= targets
            return _done()
        if verb[:3] == ["compose", "up", "-d"]:
            self.running |= {self.spec.db, self.spec.auth, world}
            return _done()
        if verb == ["kill", "-s", "TERM", world]:
            if self.term_refused:
                return _done(returncode=1, stderr=self.term_refused)
            if world not in self.running:
                return _done(returncode=1, stderr=f"cannot kill container: {world} is not running")
            self.events.append("SIGTERM")
            self.termed = True
            self.termed_at = self.now
            return _done()
        if verb == ["kill", world]:
            self.events.append("SIGKILL")
            self.running.discard(world)
            return _done()
        if verb[:1] == ["stop"]:
            self.events.append(f"docker stop {verb[-1]}")
            self.running.discard(verb[-1])
            return _done()
        if verb[:1] == ["ps"]:
            return _done("".join(f"{n}\n" for n in sorted(self.running)))
        if verb[:1] == ["inspect"] and verb[-1] == "{{.State.ExitCode}}":
            assert timeout is not None, "a look at the world must be bounded"
            if not self.exit_code:
                return _done(returncode=1, stderr="Error: No such object")
            return _done(f"{self.exit_code}\n")
        if verb[:2] == ["logs", "--tail"]:
            return _done(self.log_tail)
        if verb[:1] == ["inspect"]:
            if docker.WORKING_DIR_LABEL in verb[-1]:
                return _done(f"{self.working_dir}\n")
            if docker.PROJECT_LABEL in verb[-1]:
                return _done("t384-server\n")
            if "{{.State.Status}}" in verb[-1] and verb[1] == world:
                assert timeout is not None, "a look at the world must be bounded"
                return self._world_look()
            return _done(f"running\t{STARTED}\t0\n")
        if verb[:1] == ["exec"] and verb[1] == world:
            assert timeout is not None, "a look at the world must be bounded"
            assert verb[2:] == ["cat", "/proc/net/dev"], verb
            tx = self.traffic[min(max(self.looks, 1), len(self.traffic)) - 1]
            return _done(returncode=1, stderr="boom") if tx is None else _done(_net(tx))
        return _done()


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "_cwd_is_missing", lambda cwd: False)
    monkeypatch.setattr(docker, "_SAVE_POLL_SECONDS", 0.0)
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)


def _install(monkeypatch: pytest.MonkeyPatch, *args: object, **kw: object) -> _Docker:
    fake = _Docker(*args, **kw)  # type: ignore[arg-type]
    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(docker, "_save_clock", lambda: fake.now)
    return fake


def _stop(fake: _Docker, tmp_path: Path) -> tuple[bool, list[str], docker.StopControl]:
    said: list[str] = []
    controller = Controller(fake.spec, tmp_path)
    controller.stop_control = docker.StopControl(say=said.append)
    return controller.stop(), said, controller.stop_control


STALL = docker.WORLD_SAVE_STALL_SECONDS
CEILING = docker.WORLD_SAVE_CEILING_SECONDS


def _rising(seconds: float, step: float = 2.0) -> list[int | None]:
    """A world that writes something at every look for `seconds`."""
    return [1_000_000 + 4096 * n for n in range(int(seconds / step) + 1)]


# -- the live failure ---------------------------------------------------------------


def test_a_save_longer_than_the_old_grace_is_waited_out_and_never_killed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The live failure: still writing when 300 s ran out. Now the world is signalled alone,
    waited for while it writes, and the rest of the server is stopped only after it exited."""
    fake = _install(monkeypatch, "wow-wotlk", _rising(docker.STOP_GRACE_SECONDS + 200))
    stopped, said, _ = _stop(fake, tmp_path)
    assert stopped is True
    assert fake.events == ["SIGTERM", "world exited by itself", "compose stop (world down)"]
    assert fake.now > docker.STOP_GRACE_SECONDS
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVED]


def test_saving_is_said_in_words_on_the_tab(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(10))
    _, said, _ = _stop(fake, tmp_path)
    assert said[0].startswith("Saving characters")
    assert said[1] == docker.WORLD_SAVED


def test_a_world_that_writes_slowly_with_pauses_shorter_than_the_stall_is_not_killed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """(b) on the box: the drain slowed to ~2.5 KB/s for two minutes and finished on its own."""
    quiet = int((STALL - 10) / 2.0)
    traffic: list[int | None] = [100] + [100] * quiet + [200] + [200] * quiet + [300]
    fake = _install(monkeypatch, "wow-wotlk", traffic)
    _, said, _ = _stop(fake, tmp_path)
    assert "SIGKILL" not in fake.events
    assert said[-1] == docker.WORLD_SAVED


# -- when it is killed, and how that is said -----------------------------------------


def test_a_world_that_writes_nothing_for_the_stall_is_killed_and_it_is_said(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", [5000], exits=False)
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events == ["SIGTERM", "SIGKILL", "compose stop (world down)"]
    assert STALL <= fake.now < STALL + 3 * fake.step
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVE_STALLED]
    assert docker.WORLD_SAVE_STALLED in docker.FORCE_STOP_WARNINGS


def test_the_stall_is_counted_from_the_last_byte_written_not_from_the_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    writes = _rising(STALL * 2)
    fake = _install(monkeypatch, "wow-wotlk", writes, exits=False)
    _stop(fake, tmp_path)
    assert fake.events[1] == "SIGKILL"
    last_write = len(writes) * fake.step
    assert last_write + STALL <= fake.now < last_write + STALL + 3 * fake.step


def test_a_world_still_writing_at_the_ceiling_is_killed_and_it_is_said(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(CEILING * 2), exits=False)
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events == ["SIGTERM", "SIGKILL", "compose stop (world down)"]
    assert CEILING <= fake.now < CEILING + 3 * fake.step
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVE_TOO_LONG]
    assert docker.WORLD_SAVE_TOO_LONG in docker.FORCE_STOP_WARNINGS


def test_the_measured_numbers_bound_the_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Silence longer than the whole old grace before a kill; a ceiling far past the worst save
    seen (more than 300 s, killed) and its rate (fewer than 8 saves a second)."""
    assert docker.WORLD_SAVE_STALL_SECONDS >= docker.STOP_GRACE_SECONDS
    assert docker.WORLD_SAVE_CEILING_SECONDS >= 6 * docker.STOP_GRACE_SECONDS


def test_a_look_that_cannot_be_read_is_not_taken_for_silence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed for the saves: an unreadable look is not a stalled world. Only the ceiling
    bounds a wait that can never be read."""
    fake = _install(monkeypatch, "wow-wotlk", [None], exits=False)
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events[:2] == ["SIGTERM", "SIGKILL"]
    assert fake.now >= CEILING
    assert said[-1] == docker.WORLD_SAVE_TOO_LONG


def test_only_traffic_off_loopback_counts_as_writing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert docker._bytes_moved(_net(tx=7, rx=5, lo=10_000)) == 12
    assert docker._bytes_moved("garbage\n") is None
    assert docker._bytes_moved(" lo: 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16\n") is None


# -- what does not end the wait -------------------------------------------------------


def test_stop_now_anyway_from_a_load_wait_does_not_kill_a_saving_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The press was for a world that could not hear the stop; it is not leave to kill one that
    heard it and is saving."""
    fake = _install(monkeypatch, "wow-wotlk", _rising(60))
    said: list[str] = []
    control = docker.StopControl(say=said.append)
    control.anyway.set()
    docker.save_then_stop_the_world(fake.spec, control)
    assert fake.events == ["SIGTERM", "world exited by itself"]
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVED]


def test_closing_yulon_mid_save_leaves_the_world_saving_and_the_database_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing kills a signalled world when the app gives up: no Docker timer runs on it, and
    the database it is writing to is not stopped."""
    fake = _install(monkeypatch, "wow-wotlk", _rising(600), exits=False)
    controller = Controller(fake.spec, tmp_path)
    controller.stop_control = docker.StopControl()
    fake.on_look = lambda: controller.stop_control.abandon.set() if fake.looks == 3 else None
    with pytest.raises(docker.SaveAbandoned) as raised:
        controller.stop()
    assert fake.events == ["SIGTERM"]
    assert fake.spec.db in fake.running
    assert "saving" in str(raised.value)


def test_a_give_up_mid_save_is_not_taken_for_one_that_sent_nothing() -> None:
    """Every `StopAbandoned` handler says the world is still running (the rebuild: "the server
    you have is still the one that was running"). After the signal that is false."""
    assert not issubclass(docker.SaveAbandoned, docker.StopAbandoned)
    assert issubclass(docker.SaveAbandoned, docker.DockerCommandError)


def test_a_world_that_restarts_mid_save_is_left_to_the_ordinary_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(600), exits=False, restart_at=4)
    stopped, _, _ = _stop(fake, tmp_path)
    assert stopped is True
    assert fake.events == ["SIGTERM", "compose stop (world up)", "world stopped by the grace"]


def test_a_signal_docker_refuses_falls_back_to_the_ordinary_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(10))
    fake.term_refused = "Error response from daemon: something else"
    stopped, said, _ = _stop(fake, tmp_path)
    assert stopped is True
    assert fake.events == ["compose stop (world up)", "world stopped by the grace"]
    assert said == []


def test_a_world_that_is_already_down_is_not_signalled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(10))
    fake.running.discard(fake.spec.world)
    _stop(fake, tmp_path)
    assert "SIGTERM" not in fake.events


def test_a_world_that_exits_before_the_first_look_says_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", [])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events == ["SIGTERM", "world exited by itself", "compose stop (world down)"]
    assert said == []


# -- every stop path saves first --------------------------------------------------------


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-vanilla", "wow-tbc", "wow-tortoise"])
def test_every_world_that_drains_its_saves_on_a_signal_is_waited_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, game: str
) -> None:
    fake = _install(monkeypatch, game, _rising(STALL * 2))
    if fake.spec.stop_waits_for_load:
        monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _stop(fake, tmp_path)
    assert fake.events == ["SIGTERM", "world exited by itself", "compose stop (world down)"]


def test_remove_saves_before_compose_down(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(STALL * 2))
    controller = Controller(fake.spec, tmp_path)
    assert controller.remove() is True
    assert fake.events == ["SIGTERM", "world exited by itself", "compose down (world down)"]


def test_the_rebuilds_stop_saves_after_its_hook_and_before_the_servers_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(STALL * 2))
    docker.stop_servers_staged(
        fake.spec, tmp_path, before_signal=lambda: fake.events.append("about to signal")
    )
    assert fake.events == [
        "about to signal",
        "SIGTERM",
        "world exited by itself",
        "compose stop (world down)",
    ]
    assert fake.spec.db in fake.running


def test_stopping_another_installs_world_saves_before_its_docker_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(STALL * 2))
    docker.stop_containers([fake.spec.db, fake.spec.world], known=[fake.spec])
    assert fake.events == [
        "SIGTERM",
        "world exited by itself",
        f"docker stop {fake.spec.world}",
        f"docker stop {fake.spec.db}",
    ]


def test_the_wait_is_heard_while_it_waits(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The line reaches the tab before the world is down, not after the stop returns."""
    fake = _install(monkeypatch, "wow-wotlk", _rising(20))
    heard_while_running: list[bool] = []
    control = docker.StopControl(
        say=lambda text: heard_while_running.append(fake.spec.world in fake.running)
    )
    docker.save_then_stop_the_world(fake.spec, control)
    assert heard_while_running == [True, False]


def test_a_pause_between_looks_wakes_for_a_give_up(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "_SAVE_POLL_SECONDS", 60.0)
    fake = _install(monkeypatch, "wow-wotlk", _rising(600), exits=False)
    control = docker.StopControl()
    timer = threading.Timer(0.2, control.abandon.set)
    timer.start()
    with pytest.raises(docker.SaveAbandoned):
        docker.save_then_stop_the_world(fake.spec, control)
    timer.cancel()
    assert fake.looks <= 2


def test_a_world_docker_never_describes_after_the_signal_is_left_to_the_ordinary_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Never seen running since the signal and Docker will not say: the stop goes on as it did
    before, with its grace, rather than wait half an hour on nothing."""
    fake = _install(monkeypatch, "wow-wotlk", _rising(60), exits=False)
    fake.blind_looks = set(range(1, 2000))
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events == ["SIGTERM", "compose stop (world up)", "world stopped by the grace"]
    assert said == []


def test_a_look_docker_will_not_answer_mid_save_is_not_taken_for_silence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Seen saving, then Docker stops answering for longer than the stall: not a hang of the
    world's, so it is not killed for it."""
    blind = int(STALL / 2.0) + 10
    fake = _install(monkeypatch, "wow-wotlk", [100, 100] + [100] * blind + [200])
    fake.blind_looks = set(range(3, 3 + blind))
    _, said, _ = _stop(fake, tmp_path)
    assert "SIGKILL" not in fake.events
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVED]


# -- T410 / T411: a world whose own close loses saves is asked to save everyone FIRST ----------
#
# Read at the pins, 2026-10-06. Centurion (TrinityCore112 faac5fc9) DELETES every statement still
# queued when it closes its character database (`~DatabaseWorker` -> `_queue->Cancel()`), so the
# logout saves its shutdown queues are lost on a clean exit. Tortoise (187af788) stops its
# character database before it logs the headless sessions out, so a bot a player owns loses its
# last save ("Cant begin transaction."). Both have a console `saveall` that saves every player in
# the world, bots included; TrinityCore's `server debug` prints the character queue's length.


class _Console:
    """The world's console as `docker._console_send` reaches it: the commands typed, in order.

    `queue` is what each `server debug` finds in the character save queue (None: a reply with no
    queue line in it); the last value repeats. Each `server debug` after the first advances the
    fake's clock by its step. `refuse` makes every command fail as a console this host cannot
    reach does; `prompted` False answers every command with no prompt in the window, which is
    also what an attach that never reached the console looks like.
    """

    def __init__(self, fake: _Docker, queue: list[int | None] | None = None) -> None:
        self.fake = fake
        self.queue = list(queue or [])
        self.looks = 0
        self.refuse = ""
        self.refused: set[str] = set()
        """Commands that alone fail as a console this host cannot reach does."""
        self.prompted = True
        self.after_look: object = None
        self.markers: dict[str, object] = {}
        """The text each command was told to end its wait on (None: the whole window)."""
        self.windows: dict[str, float] = {}
        """The listening window each command was typed with."""

    def __call__(self, command: str, **kw: object) -> object:
        from yulon.controller_wow_wotlk.console import ConsoleError, ConsoleReply

        assert kw["container"] == self.fake.spec.world
        assert kw["prompt"] == self.fake.spec.save_first.prompt  # type: ignore[union-attr]
        if self.refuse or command in self.refused:
            raise ConsoleError(self.refuse or "no attach")
        self.fake.events.append(f"console: {command}")
        self.windows[command] = float(kw["window"])  # type: ignore[arg-type]
        self.markers[command] = kw.get("answer_marker")
        if command != "server debug":
            return ConsoleReply(command, ("All players saved.",), prompted=self.prompted)
        self.looks += 1
        if self.looks > 1:
            self.fake.now += self.fake.step
        size = self.queue[min(self.looks, len(self.queue)) - 1] if self.queue else None
        lines = ["Using World DB: TDB 335.21101", "LoginDatabase queue size: 0"]
        if size is not None:
            lines.append(f"CharacterDatabase queue size: {size}")
        if callable(self.after_look):
            self.after_look()
        return ConsoleReply(command, tuple(lines), prompted=self.prompted)


def _with_console(
    monkeypatch: pytest.MonkeyPatch, fake: _Docker, queue: list[int | None] | None = None
) -> _Console:
    console = _Console(fake, queue)
    monkeypatch.setattr(docker, "_console_send", console)
    # The console path is this host's, whatever host the suite runs on (T496).
    monkeypatch.setattr(docker, "_console_reaches", lambda wsl_distro: True)
    return console


BOTS_OUT = ["playerbot population stop", "server plimit administrator"]
"""What Centurion is told before `saveall` so nothing new joins its save queue (T498)."""

BOTS_BACK = ["server plimit reset", "playerbot population start"]
"""What puts `BOTS_OUT` back when the stop is given up and the world is left running (T498)."""


def test_centurion_and_tortoise_say_how_they_save_first_and_the_rest_do_not() -> None:
    centurion = CATALOG.get("wow-centurion").container_spec().save_first
    assert centurion is not None
    assert (centurion.command, centurion.queue_command) == ("saveall", "server debug")
    assert (list(centurion.first), list(centurion.if_given_up)) == (BOTS_OUT, BOTS_BACK)
    assert centurion.queue_length("CharacterDatabase queue size: 2137") == 2137
    assert centurion.queue_length("LoginDatabase queue size: 9") is None
    assert (centurion.prompt, centurion.prompt_precedes_answer) == ("TC>", True)
    tortoise = CATALOG.get("wow-tortoise").container_spec().save_first
    assert tortoise is not None
    assert (tortoise.command, tortoise.queue_command) == ("saveall", "")
    assert (tortoise.first, tortoise.if_given_up) == ((), ())
    assert tortoise.prompt == "mangos>"
    for game in ("wow-wotlk", "wow-vanilla", "wow-tbc"):
        assert CATALOG.get(game).container_spec().save_first is None, game


def test_a_queue_command_without_a_pattern_with_one_group_is_refused() -> None:
    from pydantic import ValidationError

    from yulon.catalog.catalog import SaveBeforeStop

    SaveBeforeStop(command="saveall")
    SaveBeforeStop(command="saveall", queue_command="server debug", queue_pattern=r"size: (\d+)")
    for bad in (
        {"command": "saveall", "queue_command": "server debug"},
        {"command": "saveall", "queue_pattern": r"size: (\d+)"},
        {"command": "saveall", "queue_command": "server debug", "queue_pattern": r"size: \d+"},
        {"command": "saveall", "queue_command": "server debug", "queue_pattern": r"(a)(b)"},
        {"command": "saveall", "queue_command": "server debug", "queue_pattern": r"(unclosed"},
        {"command": ""},
        {"command": "save\nall"},
        {"command": "saveall", "first": ["server plimit\nadministrator"]},
        {"command": "saveall", "first": [""]},
        {"command": "saveall", "if_given_up": ["server plimit reset"]},
        {"command": "saveall", "first": ["a"], "if_given_up": ["reset\nall"]},
    ):
        with pytest.raises(ValidationError):
            SaveBeforeStop(**bad)  # type: ignore[arg-type]


def test_centurion_saves_everyone_and_waits_for_the_queue_before_the_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The T410 loss: whatever is still queued when Centurion closes is thrown away. So the world
    is told to save everyone while it still runs, and signalled only once the queue is empty."""
    fake = _install(monkeypatch, "wow-centurion", _rising(10))
    console = _with_console(monkeypatch, fake, [2137, 1500, 600, 40, 0])
    stopped, said, _ = _stop(fake, tmp_path)
    assert stopped is True
    assert fake.events[:9] == [
        *[f"console: {line}" for line in BOTS_OUT],
        "console: saveall",
        *["console: server debug"] * 5,
        "SIGTERM",
    ]
    assert fake.events[-2:] == ["world exited by itself", "compose stop (world down)"]
    assert console.looks == 5
    assert said == [
        docker.SAVE_FIRST_ASKING,
        docker.SAVE_FIRST_QUEUED.format(count=2137),
        docker.SAVE_FIRST_WRITTEN,
        docker.WORLD_SAVING,
        docker.WORLD_SAVED,
    ]


def test_a_queue_back_at_an_older_level_is_not_taken_for_the_saves_written(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One worker writes the queue in order. With items queued before the save, the length is
    back at its earlier level while every save `saveall` queued is still behind them; only an
    empty queue holds none of them (cold review)."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    console = _with_console(monkeypatch, fake, [2140, 3, 3, 3, 0])
    _, said, _ = _stop(fake, tmp_path)
    assert console.looks == 5
    assert fake.events.index("SIGTERM") == len(BOTS_OUT) + 1 + 5
    assert docker.SAVE_FIRST_WRITTEN in said


def test_a_queue_already_empty_is_not_waited_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [0])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events[2:5] == ["console: saveall", "console: server debug", "SIGTERM"]
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_WRITTEN]


def test_a_queue_that_stops_shrinking_is_given_up_on_and_the_stop_goes_on_said_plainly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [900, 800])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.termed_at is not None
    assert STALL <= fake.termed_at < STALL + 4 * fake.step
    assert docker.SAVE_FIRST_UNFINISHED in said
    assert docker.SAVE_FIRST_UNFINISHED in docker.FORCE_STOP_WARNINGS
    assert said.index(docker.SAVE_FIRST_UNFINISHED) < said.index(docker.WORLD_SAVING)


def test_a_queue_that_keeps_shrinking_slowly_is_waited_out_past_the_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    looks = int(STALL / 2.0) * 2
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [*range(looks, -1, -1)])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.termed_at is not None and fake.termed_at > STALL
    assert docker.SAVE_FIRST_UNFINISHED not in said
    assert docker.SAVE_FIRST_WRITTEN in said


def test_a_queue_that_cannot_be_read_mid_wait_does_not_hold_the_stop_past_the_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Not like the world's traffic after the signal: here the world is still RUNNING, so a wait
    on looks that never answer would hold a running server for half an hour on nothing."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [900, None])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.termed_at is not None
    assert fake.termed_at < STALL + 4 * fake.step
    assert docker.SAVE_FIRST_UNFINISHED in said


def test_the_ceiling_bounds_a_queue_that_shrinks_forever(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [*range(10**6, 0, -1)])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.termed_at is not None
    assert CEILING <= fake.termed_at < CEILING + 4 * fake.step
    assert docker.SAVE_FIRST_UNFINISHED in said


def test_a_queue_length_that_cannot_be_read_at_all_is_not_waited_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Nothing to wait for: the save was asked for (the console answered), and the stop goes on."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [None])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events[2:5] == ["console: saveall", "console: server debug", "SIGTERM"]
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.WORLD_SAVING]


def test_a_console_with_no_prompt_and_no_queue_line_is_not_said_to_have_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An attach that never reached the console comes back as a reply with no prompt, not as an
    error (cold review): "asked" would be untrue."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    console = _with_console(monkeypatch, fake, [None])
    console.prompted = False
    _, said, _ = _stop(fake, tmp_path)
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_NOT_ASKED]
    assert docker.SAVE_FIRST_WRITTEN not in said


def test_a_queue_line_proves_the_console_answered_even_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    console = _with_console(monkeypatch, fake, [40, 0])
    console.prompted = False
    _, said, _ = _stop(fake, tmp_path)
    assert said[:3] == [
        docker.SAVE_FIRST_ASKING,
        docker.SAVE_FIRST_QUEUED.format(count=40),
        docker.SAVE_FIRST_WRITTEN,
    ]


def test_tortoise_saves_everyone_before_the_signal_and_does_not_wait_on_a_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T411: the owned bots' save is the one Tortoise's own close fails, so `saveall` saves them
    while the database is up; Tortoise drains what that queued on the signal, which the wait after
    the signal already sees."""
    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _with_console(monkeypatch, fake)
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events == [
        "console: saveall",
        "SIGTERM",
        "world exited by itself",
        "compose stop (world down)",
    ]
    assert said == [docker.SAVE_FIRST_ASKING, docker.WORLD_SAVING, docker.WORLD_SAVED]


@pytest.mark.parametrize(
    ("game", "window"),
    [
        # A console that prompts after its answer ends the wait at the answer, so its ceiling
        # can cover the first minutes' world stalls (13-18 s measured) at no cost (T561).
        ("wow-tortoise", 30.0),
        # A readline console sleeps its whole window: it keeps the 10 s.
        ("wow-centurion", 10.0),
    ],
)
def test_saveall_is_given_the_window_its_console_can_afford(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, game: str, window: float
) -> None:
    fake = _install(monkeypatch, game, _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    console = _with_console(monkeypatch, fake, [0])
    _stop(fake, tmp_path)
    assert console.windows["saveall"] == window
    # Only a console that prompts after its answer is told what `saveall`'s answer looks like.
    assert console.markers["saveall"] == ("All players saved." if window > 10 else None)


def test_a_tortoise_console_with_no_prompt_is_said_as_not_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-tortoise", _rising(4))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    console = _with_console(monkeypatch, fake)
    console.prompted = False
    _, said, _ = _stop(fake, tmp_path)
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_NOT_ASKED]
    assert docker.SAVE_FIRST_NOT_ASKED in docker.FORCE_STOP_WARNINGS


def test_wotlk_types_nothing_at_its_console(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(10))
    _with_console(monkeypatch, fake)
    _stop(fake, tmp_path)
    assert not [e for e in fake.events if e.startswith("console")]


def test_a_console_that_cannot_be_reached_is_said_and_the_stop_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(10))
    console = _with_console(monkeypatch, fake)
    console.refuse = "no pty on this computer"
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events[0] == "SIGTERM"
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_NOT_ASKED]
    assert said[2:] == [docker.WORLD_SAVING, docker.WORLD_CLOSED]


def test_a_world_that_is_not_running_is_not_asked_to_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(10))
    _with_console(monkeypatch, fake, [0])
    fake.running.discard(fake.spec.world)
    docker.save_then_stop_the_world(fake.spec, docker.StopControl())
    assert fake.events == []


def test_giving_up_while_the_queue_drains_sends_nothing_and_says_the_world_still_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Before the signal the world is still running, so this give-up IS a `StopAbandoned`: every
    handler of that one says so, and here it is true. The rebuild's `before_signal` -- the moment
    past which it reads the servers as touched -- is not reached (cold review)."""
    fake = _install(monkeypatch, "wow-centurion", _rising(10))
    console = _with_console(monkeypatch, fake, [900, 800, 700, 600, 500])
    control = docker.StopControl()
    console.after_look = lambda: control.abandon.set() if console.looks == 3 else None
    touched: list[bool] = []
    with pytest.raises(docker.SaveFirstAbandoned) as raised:
        docker.stop_servers_staged(
            fake.spec, tmp_path, control=control, before_signal=lambda: touched.append(True)
        )
    assert isinstance(raised.value, docker.StopAbandoned)
    assert "SIGTERM" not in fake.events
    assert fake.spec.world in fake.running
    assert touched == []


def test_giving_up_puts_back_the_logins_the_stop_closed_before_it_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T498: the world is left running after a give-up, so the bots and players it logged out
    for the save may come back: the login limit is reset and the bot population restarted."""
    fake = _install(monkeypatch, "wow-centurion", _rising(10))
    console = _with_console(monkeypatch, fake, [900, 800, 700, 600, 500])
    control = docker.StopControl()
    console.after_look = lambda: control.abandon.set() if console.looks == 2 else None
    with pytest.raises(docker.SaveFirstAbandoned):
        docker.save_then_stop_the_world(fake.spec, control)
    assert fake.events[-len(BOTS_BACK) :] == [f"console: {line}" for line in BOTS_BACK]
    assert "SIGTERM" not in fake.events


def test_a_stop_that_goes_on_does_not_let_the_logins_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [900, 800])
    _, said, _ = _stop(fake, tmp_path)
    assert docker.SAVE_FIRST_UNFINISHED in said
    assert not [e for e in fake.events if e.endswith(tuple(BOTS_BACK))]


def test_a_bots_out_line_the_console_refuses_does_not_stop_the_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Logging the bots out makes the wait shorter; it is not the save. The save is asked and
    waited for as before when it could not be typed."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    console = _with_console(monkeypatch, fake, [40, 0])
    console.refused = set(BOTS_OUT)
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events[:4] == [
        "console: saveall",
        "console: server debug",
        "console: server debug",
        "SIGTERM",
    ]
    assert said[:3] == [
        docker.SAVE_FIRST_ASKING,
        docker.SAVE_FIRST_QUEUED.format(count=40),
        docker.SAVE_FIRST_WRITTEN,
    ]


def test_tortoise_types_no_bots_out_line(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _with_console(monkeypatch, fake)
    _stop(fake, tmp_path)
    assert [e for e in fake.events if e.startswith("console")] == ["console: saveall"]


# -- T498: a queue the world keeps filling is not a stuck one ----------------------------------
#
# Live, yulon-win11, 2026-10-06 (Centurion, 150 bots): after `saveall` the character queue sat at
# 1948 and never went below it for 5 minutes, because the bots' 90-second autosaves kept topping
# it up while it drained. The stop read that as stuck, gave up, and the close threw away every
# save queued since 14:58. The bots are now logged out first; and a queue is stuck only when it
# stops getting shorter at all, not when it is never shorter than its lowest.


def test_a_queue_that_keeps_draining_while_it_is_refilled_is_waited_out_past_the_stall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    looks = int(STALL / 2.0) + 40
    refilled: list[int | None] = [1948 if n % 2 else 1990 for n in range(looks)]
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [1948, *refilled, 600, 0])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.termed_at is not None and fake.termed_at > STALL
    assert docker.SAVE_FIRST_UNFINISHED not in said
    assert docker.SAVE_FIRST_WRITTEN in said


def test_a_queue_that_only_grows_is_stuck(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A worker that writes nothing while saves still arrive: longer at every look, never
    shorter. Movement is not progress; only a shorter queue is."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [*range(900, 900 + 10**4)])
    _, said, _ = _stop(fake, tmp_path)
    assert fake.termed_at is not None
    assert STALL <= fake.termed_at < STALL + 4 * fake.step
    assert docker.SAVE_FIRST_UNFINISHED in said


def test_the_stall_is_counted_from_the_last_look_that_found_the_queue_shorter(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Shorter than the look before counts, even when it is not below the lowest seen."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    flat = int(STALL / 2.0) - 10
    _with_console(monkeypatch, fake, [500, 900, *[900] * flat, 899, *[899] * flat, 0])
    _, said, _ = _stop(fake, tmp_path)
    assert docker.SAVE_FIRST_UNFINISHED not in said
    assert docker.SAVE_FIRST_WRITTEN in said


def test_the_rebuilds_hook_comes_after_the_save_and_right_before_the_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [10, 0])
    docker.stop_servers_staged(
        fake.spec, tmp_path, before_signal=lambda: fake.events.append("about to signal")
    )
    assert fake.events[:7] == [
        *[f"console: {line}" for line in BOTS_OUT],
        "console: saveall",
        "console: server debug",
        "console: server debug",
        "about to signal",
        "SIGTERM",
    ]


def test_a_give_up_mid_save_tells_the_panel_what_it_left() -> None:
    """After the signal the world is closing and the database is up: that sentence must be shown
    after a job's Stop (T228's mark), not folded into "cancelled"."""
    from yulon.after_stop import TrueAfterStop

    assert issubclass(docker.SaveAbandoned, TrueAfterStop)


# -- "Stop now anyway" on a world that may still be deaf (cold review) --------------------------


@pytest.mark.parametrize("game", ["wow-tortoise", "wow-vanilla"])
def test_a_forced_stop_of_a_loading_world_is_neither_saved_first_nor_watched(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, game: str
) -> None:
    """T158's premise: a CMaNGOS world still loading drops the SIGTERM. "Stop now anyway" ends
    the load wait with the world possibly deaf; its loading traffic is not a save, so it is left
    to the ordinary stop and its grace, as before T384 -- not watched for half an hour and said to
    be "saving every character"."""
    fake = _install(monkeypatch, game, _rising(CEILING * 2), exits=False)
    console = _with_console(monkeypatch, fake, [0])

    def forced(*_a: object, **_k: object) -> object:
        yield docker.WORLD_STOPPED_ANYWAY

    monkeypatch.setattr(docker, "world_load_steps", forced)
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events == ["compose stop (world up)", "world stopped by the grace"]
    assert console.looks == 0
    assert docker.WORLD_SAVING not in said
    assert said == [docker.WORLD_STOPPED_ANYWAY]


def test_the_load_wait_says_whether_the_world_can_hear_the_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CATALOG.get("wow-tortoise").container_spec()
    monkeypatch.setattr(
        docker, "world_load_steps", lambda *a, **k: iter([docker.WORLD_STOPPED_ANYWAY])
    )
    assert docker.wait_for_the_world_to_load(spec, docker.StopControl()) is False
    monkeypatch.setattr(
        docker, "world_load_steps", lambda *a, **k: iter([docker.WORLD_FINISHED_LOADING])
    )
    assert docker.wait_for_the_world_to_load(spec, docker.StopControl()) is True
    monkeypatch.setattr(docker, "world_load_steps", lambda *a, **k: iter([]))
    assert docker.wait_for_the_world_to_load(spec, docker.StopControl()) is True


# -- T414: "saved every character" only for a world that exited cleanly -----------------------


def test_a_world_that_crashes_on_its_way_out_is_not_said_to_have_saved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Live, 2026-10-06 (Tortoise): "Cant begin transaction." and exit 139 after "Halting
    process...", and the tab said "The world server saved every character and closed."."""
    from yulon.said import split_details

    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    fake.exit_code = "139"
    fake.log_tail = "Halting process...\nCant begin transaction.\n"
    _, said, _ = _stop(fake, tmp_path)
    assert docker.WORLD_SAVED not in said
    sentence, details = split_details(said[-1])
    assert "139" in sentence and "error" in sentence and "characters" in sentence
    assert "Cant begin transaction." in details
    assert docker.outlives_the_stop(said[-1])


def test_a_world_that_exited_at_a_failed_update_is_not_blamed_on_a_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T600: a patched core exits 1 at a failed update and Docker restarts it; the stop that
    finds it must say the update, not "while it was saving characters" (seen live 2026-10-09).

    Mutation: drop the update check in `_how_the_world_ended()` and the save blame is back.
    """
    from yulon.said import split_details

    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    fake.exit_code = "1"
    fake.log_tail = (
        "[DB Auto-Updater] Attempting to execute update 20260903063722_world, hash AB12.\n"
        "[1050] Table 'item_template' already exists\n"
        "[DB Auto-Updater] Migration 20260903063722_world with hash AB12 failed to apply.\n"
        "DB AutoUpdater FAILED, cancelling server.\n"
    )
    _, said, _ = _stop(fake, tmp_path)
    sentence, _details = split_details(said[-1])
    assert "20260903063722_world.sql" in sentence
    assert "[1050] Table 'item_template' already exists" in sentence
    assert "saving" not in sentence and "saves" not in sentence
    assert docker.outlives_the_stop(said[-1])


def test_every_other_exit_one_keeps_the_save_sentence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An exit code 1 whose log does not end on a failed update is still a save that went wrong."""
    from yulon.said import split_details

    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    fake.exit_code = "1"
    fake.log_tail = "Halting process...\nCant begin transaction.\n"
    _, said, _ = _stop(fake, tmp_path)
    sentence, details = split_details(said[-1])
    assert "exit code 1" in sentence and "while it was saving characters" in sentence
    assert "Cant begin transaction." in details


def test_a_crash_before_the_first_look_is_said_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", [])
    fake.exit_code = "134"
    _, said, _ = _stop(fake, tmp_path)
    assert len(said) == 1 and "134" in said[0]


def test_an_exit_code_that_cannot_be_read_claims_neither(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(10))
    fake.exit_code = ""
    _, said, _ = _stop(fake, tmp_path)
    assert said == [docker.WORLD_SAVING, docker.WORLD_SAVE_UNREAD]
    assert docker.outlives_the_stop(docker.WORLD_SAVE_UNREAD)


def test_a_clean_exit_says_saved_and_does_not_outlive_the_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", _rising(10))
    _, said, _ = _stop(fake, tmp_path)
    assert said[-1] == docker.WORLD_SAVED
    assert not docker.outlives_the_stop(docker.WORLD_SAVED)


# -- T415: the save-first line is said when the save starts, not after it --------------------


def test_the_save_first_line_is_heard_before_the_save_is_typed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Live: `saveall` and `server debug` took 10-15 s and the line came only after, replaced in
    the same second."""
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [40, 0])
    heard: list[tuple[str, list[str]]] = []
    control = docker.StopControl(say=lambda text: heard.append((text, list(fake.events))))
    docker.save_then_stop_the_world(fake.spec, control)
    assert heard[0] == (docker.SAVE_FIRST_ASKING, [])


def test_a_save_that_could_not_be_asked_takes_back_the_line_said_before_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    console = _with_console(monkeypatch, fake)
    console.refuse = "no pty on this computer"
    _, said, _ = _stop(fake, tmp_path)
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_NOT_ASKED]


# -- T496: where the console needs a terminal, the save is asked through the command channel ----
#
# Live, yulon-win11-gate, 2026-10-06 (Tortoise): Stop said "could not type 'saveall' ... The
# worldserver console needs a terminal, and Yu'lon cannot open one on this computer", then
# `SAVE_FIRST_NOT_ASKED`, then "The world server saved every character and closed." -- so the save
# #326 added never reached Windows, and one Stop said both that it could not ask and that every
# character was saved. Docker Desktop's CLI attaches a tty container's console only from a
# terminal; the command channel (SOAP) is a plain TCP request and needs none.


class _Channel:
    """A command channel as `InstallChannel.live_channel()` hands one out: `send()` -> `Answer`.

    `queue` is what each `server debug` finds, as `_Console` has it. `outcome` other than "yes"
    answers every command that way, with `reason`.
    """

    def __init__(self, fake: _Docker, queue: list[int | None] | None = None) -> None:
        self.fake = fake
        self.queue = list(queue or [])
        self.looks = 0
        self.outcome = "yes"
        self.indeterminate = False

    def send(self, command: str) -> object:
        from yulon.channel import Answer

        self.fake.events.append(f"channel: {command}")
        if self.outcome != "yes":
            return Answer(
                self.outcome,  # type: ignore[arg-type]
                reason="the server is running but the command channel did not answer",
                indeterminate=self.indeterminate,
            )
        if command != "server debug":
            return Answer("yes", "All players saved.")
        self.looks += 1
        if self.looks > 1:
            self.fake.now += self.fake.step
        size = self.queue[min(self.looks, len(self.queue)) - 1] if self.queue else None
        lines = ["Using World DB: TDB 335.21101", "LoginDatabase queue size: 0"]
        if size is not None:
            lines.append(f"CharacterDatabase queue size: {size}")
        return Answer("yes", "\n".join(lines))


SERVER = Path("/srv/this-install")
"""The install folder the tests' channel is registered for."""


def _on_windows(
    monkeypatch: pytest.MonkeyPatch, fake: _Docker, channel: _Channel | None
) -> _Console:
    """This host cannot type at the console (no terminal), and `channel` is the install's own."""
    console = _with_console(monkeypatch, fake)
    monkeypatch.setattr(docker, "_console_reaches", lambda wsl_distro: False)
    if channel is not None:
        docker.save_through_channel(fake.spec.world, SERVER, lambda: channel)
    return console


def test_tortoise_on_windows_saves_everyone_through_its_command_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _on_windows(monkeypatch, fake, _Channel(fake))
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events == [
        "channel: saveall",
        "SIGTERM",
        "world exited by itself",
        "compose stop (world down)",
    ]
    assert said == [docker.SAVE_FIRST_ASKING, docker.WORLD_SAVING, docker.WORLD_SAVED]


def test_centurion_on_windows_waits_for_its_save_queue_through_the_channel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _on_windows(monkeypatch, fake, _Channel(fake, [40, 0]))
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events[:6] == [
        *[f"channel: {line}" for line in BOTS_OUT],
        "channel: saveall",
        "channel: server debug",
        "channel: server debug",
        "SIGTERM",
    ]
    assert said[:3] == [
        docker.SAVE_FIRST_ASKING,
        docker.SAVE_FIRST_QUEUED.format(count=40),
        docker.SAVE_FIRST_WRITTEN,
    ]
    assert said[-1] == docker.WORLD_SAVED


@pytest.mark.parametrize("why", ["no channel", "not set up", "unknown", "no", "raises"])
def test_on_windows_a_channel_that_cannot_ask_is_said_and_never_claims_the_save(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, why: str
) -> None:
    """The one Stop says one thing: the warning that it could not ask, and a close that does not
    say every character was saved."""
    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    channel = _Channel(fake)
    if why in ("unknown", "no"):
        channel.outcome = why
    _on_windows(monkeypatch, fake, None if why == "no channel" else channel)
    if why == "not set up":
        docker.save_through_channel(fake.spec.world, SERVER, lambda: None)
    if why == "raises":

        def broken() -> object:
            raise OSError("the credential file could not be read")

        docker.save_through_channel(fake.spec.world, SERVER, broken)
    _, said, _ = _stop(fake, tmp_path)
    assert not [e for e in fake.events if e.startswith("console")]
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_NOT_ASKED_HERE]
    assert docker.SAVE_FIRST_NOT_ASKED_HERE in docker.FORCE_STOP_WARNINGS
    assert docker.WORLD_SAVED not in said
    assert said[-1] == docker.WORLD_CLOSED
    assert not docker.outlives_the_stop(docker.WORLD_CLOSED)


def test_where_the_console_can_be_typed_at_the_channel_is_not_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Linux (and a WSL-resident server) keep #326's console path exactly."""
    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _with_console(monkeypatch, fake)
    docker.save_through_channel(fake.spec.world, SERVER, lambda: _Channel(fake))
    _, said, _ = _stop(fake, tmp_path)
    assert fake.events[:2] == ["console: saveall", "SIGTERM"]
    assert said == [docker.SAVE_FIRST_ASKING, docker.WORLD_SAVING, docker.WORLD_SAVED]


def test_the_console_is_reached_where_the_shared_transport_says_it_can_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from yulon.controller_wow_wotlk import console

    monkeypatch.setattr(console, "pty_supported", lambda: False)
    assert docker._console_reaches(None) is False
    assert docker._console_reaches("Ubuntu") is True
    monkeypatch.setattr(console, "pty_supported", lambda: True)
    assert docker._console_reaches(None) is True


def test_a_save_queue_that_ran_out_does_not_end_in_saved_every_character(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-centurion", _rising(4))
    _with_console(monkeypatch, fake, [900, None])
    _, said, _ = _stop(fake, tmp_path)
    assert docker.SAVE_FIRST_UNFINISHED in said
    assert docker.WORLD_SAVED not in said
    assert said[-1] == docker.WORLD_CLOSED


@pytest.mark.parametrize("game", ["wow-tortoise", "wow-centurion"])
def test_the_real_wiring_hands_the_stop_this_installs_command_channel(
    tmp_path: Path, game: str
) -> None:
    """The mechanism is called: the factory the tab is built from registers the install's own
    `live_channel` for its world, so every stop route (Stop, Restart, rebuild, uninstall) finds it.
    """
    from yulon.ui.controller_view import ControllerServices

    entry = CATALOG.get(game)
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    services = ControllerServices.for_entry(entry, server_dir, client_dir=None)
    setup = services.channel_setup
    assert setup is not None
    registered = docker._save_channels[entry.container_spec().world]
    assert registered == {docker._folder_key(str(server_dir)): setup.live_channel}  # type: ignore[attr-defined]


def test_a_game_that_does_not_save_first_registers_no_channel(tmp_path: Path) -> None:
    from yulon.ui.controller_view import ControllerServices

    entry = CATALOG.get("wow-wotlk")
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    ControllerServices.for_entry(entry, server_dir, client_dir=None)
    assert entry.container_spec().world not in docker._save_channels


def test_with_two_installs_of_one_game_the_running_worlds_own_channel_is_asked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A world's container name is the game's, so two installs register under one name; the
    world's compose working-dir label says which install it was brought up from (cold review)."""
    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _on_windows(monkeypatch, fake, None)
    other, mine = _Channel(fake), _Channel(fake)
    other.outcome = "unknown"
    docker.save_through_channel(fake.spec.world, tmp_path / "other", lambda: other)
    docker.save_through_channel(fake.spec.world, tmp_path / "mine", lambda: mine)
    docker.save_through_channel(fake.spec.world, tmp_path / "third", lambda: other)
    fake.working_dir = str(tmp_path / "mine")
    _, said, _ = _stop(fake, tmp_path)
    assert said == [docker.SAVE_FIRST_ASKING, docker.WORLD_SAVING, docker.WORLD_SAVED]
    fake2 = _install(monkeypatch, "wow-tortoise", _rising(10))
    fake2.working_dir = ""
    other.fake = mine.fake = fake2
    _, said, _ = _stop(fake2, tmp_path)
    assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_NOT_ASKED_HERE]
    assert "channel: saveall" not in fake2.events


@pytest.mark.parametrize(
    ("outcome", "saved"), [("answered", True), ("unauthorised", False), ("silent", False)]
)
def test_the_real_soap_channel_answers_the_save_on_windows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, outcome: str, saved: bool
) -> None:
    """Through `SoapChannel` itself, so the mapping of a SOAP reply onto "asked" is the shipped
    one: only a command the server ran and reported as succeeding is a save."""
    from yulon import soap
    from yulon.channel import SoapChannel

    fake = _install(monkeypatch, "wow-tortoise", _rising(10))
    monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: True)
    _on_windows(monkeypatch, fake, None)
    sent: list[str] = []

    def execute(endpoint: soap.Endpoint, command: str, **_kw: object) -> soap.Reply:
        sent.append(command)
        return soap.Reply(outcome, "All players saved." if outcome == "answered" else "")  # type: ignore[arg-type]

    endpoint = soap.Endpoint("127.0.0.1", 7878, "YULON", "pw", namespace="urn:MaNGOS")
    channel = SoapChannel(
        endpoint=endpoint, state_of=lambda: docker.ContainerState("running"), send=execute
    )
    docker.save_through_channel(fake.spec.world, SERVER, lambda: channel)
    _, said, _ = _stop(fake, tmp_path)
    assert sent == ["saveall"]
    if saved:
        assert said == [docker.SAVE_FIRST_ASKING, docker.WORLD_SAVING, docker.WORLD_SAVED]
    else:
        assert said[:2] == [docker.SAVE_FIRST_ASKING, docker.SAVE_FIRST_NOT_ASKED_HERE]
        assert said[-1] == docker.WORLD_CLOSED
