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
        if verb[:1] == ["inspect"]:
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


def test_saving_is_said_in_words_on_the_tab(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
        monkeypatch.setattr(docker, "wait_for_the_world_to_load", lambda *a, **k: None)
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
