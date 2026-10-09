"""T158: a stop waits until a CMaNGOS world can hear it, then stops it.

Measured on a Linux gate box (2026-09-27, Vanilla, 500 bots): mangosd is the container's PID 1
and installs no SIGTERM/SIGINT handler until it has loaded (`Master::Run()` calls
`_HookSignals()` right after `SetInitialWorldSettings()`). Its SigCgt read `0000000100000000` at
+3, +8, +15 and +25 s of the load and `0000000100004002` once loaded. Linux never applies a default
action to a namespace's init, so a SIGTERM that lands during the load is dropped: the world
finished loading, ran, and was SIGKILLed at the end of `compose stop -t 300` (280.5 s, exit 137, no
final save). A loaded world stopped cleanly in 22.2 s. The owner's decision: Stop waits until the
world has loaded, then sends the stop, and says so on the tab; never kill a world mid-load.

Round 2 waits on that measured fact -- bit 15 of PID 1's SigCgt, read with `docker exec` -- and
not on a log line, because Tortoise's ready banner is printed BEFORE its hook. Every test here
goes through a real stop path (`Controller.stop()`/`remove()`/`stop_conflicting()`,
`docker.recreate_staged()`, `purge.Uninstaller`) with only `runner.run` faked: a docker whose
world state, log and signal mask the test scripts, one frame per look.
"""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import docker, purge, runner
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller

CATALOG = load_catalog()

LOADING_MASK = "0000000100000000"
"""SigCgt of the loading mangosd, as measured at +3/+8/+15/+25 s: no SIGINT, no SIGTERM."""
LOADED_MASK = "0000000100004002"
"""SigCgt once the world had loaded: SIGINT (bit 2) and SIGTERM (bit 15)."""
SIGINT_ONLY = "0000000100000002"
"""A handler for SIGINT alone. Not what a stop sends, so still not able to hear one."""

# The world's own lines (the T158 measurement's log). The log is served on every look so that
# the round-1 build, which waited on these, can be run against the same fakes; this build never
# reads it.
LOADING = "Loading object locations.\n"
LOADED = LOADING + "CMANGOS: World initialized\nAvg Diff: 64. Sessions online: 0.\n"
TORTOISE_BANNER = "World server is up and running! Loading time: 1 minutes 2 seconds\n"

STARTED = "2026-09-27T18:35:42.000000000Z"
RESTARTED = "2026-09-27T18:36:30.000000000Z"

Frame = tuple[str, str, str | None]
"""(status, this run's log, SigCgt or None for a `docker exec` that fails). An empty status is a
`docker inspect` that fails."""


def _status(mask: str) -> str:
    return (
        f"Name:\tmangosd\nSigBlk:\t0000000000000000\nSigIgn:\t0000000000001000\nSigCgt:\t{mask}\n"
    )


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "" if returncode == 0 else "boom")


class _Docker:
    """`runner.run` for one install whose world server loads on the test's script.

    `frames` is what each look at the world finds; the last one repeats. A look is one state
    inspect of the world; the `docker exec` and the log read after it answer from the same
    frame. `events` records every look and every stop in the order they happened, which is the
    whole claim under test: no stop before the world can hear it.
    """

    def __init__(
        self,
        spec: docker.ContainerSpec,
        frames: list[Frame],
        *,
        started: list[str] | None = None,
        project: str = "t158-server",
    ) -> None:
        self.spec = spec
        self.frames = frames
        self.started = started or [STARTED]
        self.project = project
        self.looks = 0
        self.execs = 0
        # The bound each of a look's two commands was given, in order.
        self.timeouts: list[float | None] = []
        self.events: list[str] = []
        self.running = {spec.db, spec.auth, spec.world}
        self.present = set(self.running)
        # Called at every look, so a test can press a button or read the tab at that moment.
        self.on_look: Callable[[], None] | None = None

    def _frame(self) -> Frame:
        return self.frames[min(max(self.looks, 1), len(self.frames)) - 1]

    def _stopped(self, names: set[str]) -> None:
        if self.spec.world in names and self.spec.world in self.running:
            self.events.append("stop")
        self.running -= names

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        verb = cmd[1:]
        if verb[:3] == ["compose", "config", "--format"]:
            return _completed('{"name": "' + self.project + '"}')
        if verb[:2] == ["compose", "stop"]:
            named = {arg for arg in verb[2:] if not arg.startswith("-") and not arg.isdigit()}
            self._stopped(named or set(self.running))
            return _completed()
        if verb[:2] == ["compose", "down"]:
            self._stopped(set(self.running))
            self.present.clear()
            return _completed()
        if verb[:3] == ["compose", "up", "-d"]:
            self.running |= {self.spec.db, self.spec.auth, self.spec.world}
            return _completed()
        if verb[:1] == ["stop"]:
            self._stopped({verb[-1]})
            return _completed()
        if verb == ["kill", "-s", "TERM", self.spec.world]:
            # T384: the world's own stop, before the rest. This world saves at once; the
            # save wait itself is `test_stop_saves_before_exit.py`'s.
            if self.spec.world not in self.running:
                return _completed(returncode=1)
            self._stopped({self.spec.world})
            return _completed()
        if verb == ["kill", self.spec.world]:
            # T600: the world's own SIGKILL, for a run stuck at a failed update.
            self.events.append("kill")
            self.running -= {self.spec.world}
            return _completed()
        if verb[:2] == ["ps", "-a"]:
            return _completed("".join(f"{n}\n" for n in sorted(self.present)))
        if verb[:1] == ["ps"]:
            if "{{.Ports}}" in verb[-1]:
                return _completed(
                    "".join(f"{n}\t0.0.0.0:3724->3724/tcp\n" for n in sorted(self.running))
                )
            return _completed("".join(f"{n}\n" for n in sorted(self.running)))
        if verb[:1] == ["inspect"]:
            fmt = verb[-1]
            if docker.PROJECT_LABEL in fmt:
                return _completed(self.project + "\n")
            if "{{.State.Status}}" in fmt and verb[1] == self.spec.world:
                self.timeouts.append(timeout)
                if self.spec.world not in self.running:
                    return _completed(f"exited\t{STARTED}\t0\n")
                self.looks += 1
                # A wait that never ends fails here rather than hanging the run.
                assert self.looks <= 200, "the wait never ended"
                self.events.append(f"look {self.looks}")
                if self.on_look is not None:
                    self.on_look()
                status = self._frame()[0]
                if not status:
                    return _completed(returncode=1)
                if status == "gone":
                    return subprocess.CompletedProcess(
                        [], 1, "", f"Error response from daemon: No such container: {verb[1]}"
                    )
                run = self.started[min(self.looks, len(self.started)) - 1]
                return _completed(f"{status}\t{run}\t0\n")
            return _completed("running\t" + STARTED + "\t0\n")
        if verb[:1] == ["exec"] and verb[1] == self.spec.world:
            self.timeouts.append(timeout)
            self.execs += 1
            assert verb[2:] == ["cat", "/proc/1/status"], verb
            mask = self._frame()[2]
            return _completed(returncode=1) if mask is None else _completed(_status(mask))
        if verb[:1] == ["logs"] and verb[-1] == self.spec.world:
            return _completed(self._frame()[1])
        return _completed()


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    """No real pause between looks. `raising=False`: the round-1 build had no such constant, and
    the red runs of this file were taken against it."""
    monkeypatch.setattr(docker, "_cwd_is_missing", lambda cwd: False)
    monkeypatch.setattr(docker, "_LOAD_POLL_SECONDS", 0.001, raising=False)
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)


def _install(
    monkeypatch: pytest.MonkeyPatch, game: str, frames: list[Frame], **kw: object
) -> _Docker:
    fake = _Docker(CATALOG.get(game).container_spec(), frames, **kw)  # type: ignore[arg-type]
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _controller(
    fake: _Docker, tmp_path: Path, said: list[str] | None = None
) -> tuple[Controller, docker.StopControl]:
    controller = Controller(fake.spec, tmp_path)
    control = docker.StopControl(say=(said if said is not None else []).append)
    controller.stop_control = control
    return controller, control


# -- which worlds wait, and on what ----------------------------------------------


@pytest.mark.parametrize("game", ["wow-vanilla", "wow-tbc", "wow-tortoise"])
def test_the_three_cmangos_worlds_wait_before_a_stop(game: str) -> None:
    """Vanilla, TBC and Tortoise run mangosd as PID 1 with its handler installed after the load.

    Each Dockerfile's `CMD ["./mangosd"]` with no entrypoint and no init makes it PID 1; the
    cores' `Master::Run()` calls `_HookSignals()` only after `SetInitialWorldSettings()`
    (mangos-classic and tortoise-wow read at their pinned revisions, 2026-09-27).
    """
    assert CATALOG.get(game).container_spec().stop_waits_for_load is True


def test_azerothcore_does_not_wait_because_its_worldserver_hears_a_stop_while_it_loads() -> None:
    """AzerothCore's worldserver installs its SIGINT/SIGTERM `signal_set` before it loads.

    `worldserver/Main.cpp` sets it up ahead of the thread pool and of
    `SetInitialWorldSettings()`; a SIGTERM mid-load calls `World::StopNow()` and the update loop
    exits as soon as the load ends. Its image's entrypoint `exec`s the server, so it is PID 1
    too, but a PID 1 with a handler is not deaf. Nothing to wait for, so nothing is changed.
    """
    assert CATALOG.get("wow-wotlk").container_spec().stop_waits_for_load is False


def test_the_measured_masks_read_as_measured() -> None:
    """Bit 15 of SigCgt, and nothing else: SIGINT alone is not a SIGTERM handler."""
    assert docker._sigterm_caught(_status(LOADED_MASK)) is True
    assert docker._sigterm_caught(_status(LOADING_MASK)) is False
    assert docker._sigterm_caught(_status(SIGINT_ONLY)) is False
    assert docker._sigterm_caught("Name:\tmangosd\n") is None
    assert docker._sigterm_caught("SigCgt:\tnot-hex\n") is None


# -- Stop (`controller.stop()` -> `stop_staged()`) ---------------------------------


def test_a_world_that_can_hear_the_stop_is_stopped_at_once_with_nothing_said(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-vanilla", [("running", LOADED, LOADED_MASK)])
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.stop() is True
    assert fake.events == ["look 1", "stop"]
    assert said == []


def test_a_loading_world_is_waited_for_until_it_catches_sigterm(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [
        ("running", LOADING, LOADING_MASK),
        ("running", LOADING, SIGINT_ONLY),
        ("running", LOADED, LOADED_MASK),
    ]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.stop() is True
    assert fake.events == ["look 1", "look 2", "look 3", "stop"]
    assert said == [docker.WORLD_STILL_LOADING, docker.WORLD_FINISHED_LOADING]
    assert said[0].startswith("The world is finishing its load before it can stop.")


def test_tortoises_ready_banner_is_not_taken_for_a_world_that_can_hear_the_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """tortoise-wow prints its banner as the LAST line of `SetInitialWorldSettings()`
    (World.cpp:2447) and hooks the signals after it (Master.cpp:208). A stop sent on the banner
    can still land before the handler; the mask cannot."""
    frames: list[Frame] = [
        ("running", TORTOISE_BANNER, LOADING_MASK),
        ("running", TORTOISE_BANNER, LOADED_MASK),
    ]
    fake = _install(monkeypatch, "wow-tortoise", frames)
    controller, _ = _controller(fake, tmp_path)
    assert controller.stop() is True
    # The third look is the save's (T411): Tortoise is asked to save everyone at its console
    # before the signal, only if it is running.
    assert fake.events == ["look 1", "look 2", "look 3", "stop"]


def test_a_world_that_restarts_while_it_is_waited_on_is_waited_on_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A new run is loading, and deaf, all over again: `restart: unless-stopped` brought it back.

    Stopping it at once would signal exactly the handler-less process this wait exists for.
    """
    frames: list[Frame] = [
        ("running", LOADING, LOADING_MASK),
        ("running", LOADING, LOADING_MASK),
        ("running", LOADING, LOADING_MASK),
        ("running", LOADED, LOADED_MASK),
    ]
    fake = _install(
        monkeypatch, "wow-vanilla", frames, started=[STARTED, RESTARTED, RESTARTED, RESTARTED]
    )
    controller, _ = _controller(fake, tmp_path)
    assert controller.stop() is True
    assert fake.events == ["look 1", "look 2", "look 3", "look 4", "stop"]


@pytest.mark.parametrize("status", ["exited", "restarting", "dead", "gone"])
def test_a_world_that_is_no_longer_running_is_stopped_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, status: str
) -> None:
    """Docker says there is no process to signal: nothing can ignore the stop. `gone` is Docker
    answering that there is no such container at all."""
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), (status, LOADING, None)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.stop() is True
    assert fake.events == ["look 1", "look 2", "stop"]
    assert said == [docker.WORLD_STILL_LOADING]


def test_a_world_that_cannot_be_asked_is_not_stopped_it_says_so_and_keeps_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Round 3, fail closed: an unreadable mask is not a world able to hear the stop."""
    frames: list[Frame] = [("running", LOADING, None)] * 7 + [
        ("running", LOADING, LOADING_MASK),
        ("running", LOADED, LOADED_MASK),
    ]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.stop() is True
    assert fake.events == [f"look {n}" for n in range(1, 10)] + ["stop"]
    assert said == [
        docker.WORLD_LOAD_UNCHECKED,
        docker.WORLD_STILL_LOADING,
        docker.WORLD_FINISHED_LOADING,
    ]
    assert said[0].startswith("Yu'lon can't check whether the world has finished loading")


def test_a_world_docker_will_not_describe_is_not_taken_for_a_stopped_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An inspect that fails says nothing about the process: it is a look that did not read."""
    frames: list[Frame] = [("", LOADING, None)] * 6 + [("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.stop() is True
    assert fake.events == [f"look {n}" for n in range(1, 8)] + ["stop"]
    assert said == [docker.WORLD_LOAD_UNCHECKED, docker.WORLD_FINISHED_LOADING]
    assert fake.execs == 1


def test_the_tab_is_told_each_time_the_answer_changes_between_loading_and_unknown(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [
        ("running", LOADING, None),
        ("running", LOADING, None),
        ("running", LOADING, LOADING_MASK),
        ("running", LOADING, None),
        ("running", LOADED, LOADED_MASK),
    ]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.stop() is True
    assert said == [
        docker.WORLD_LOAD_UNCHECKED,
        docker.WORLD_STILL_LOADING,
        docker.WORLD_LOAD_UNCHECKED,
        docker.WORLD_FINISHED_LOADING,
    ]


def test_every_command_a_look_makes_is_bounded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A wedged daemon answers neither `inspect` nor `exec`; neither may wait on it for ever."""
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    controller, _ = _controller(fake, tmp_path)
    assert controller.stop() is True
    assert fake.timeouts and set(fake.timeouts) == {docker._LOAD_LOOK_TIMEOUT}, fake.timeouts
    # Two looks of two commands each, and the save wait's one look at a world already down
    # (T384), bounded the same way.
    assert len(fake.timeouts) == 5


def test_stop_now_anyway_ends_a_wait_that_cannot_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-vanilla", [("running", LOADING, None)])
    said: list[str] = []
    controller, control = _controller(fake, tmp_path, said)
    fake.on_look = lambda: control.anyway.set() if fake.looks == 4 else None
    assert controller.stop() is True
    assert fake.events == ["look 1", "look 2", "look 3", "look 4", "stop"]
    assert said == [docker.WORLD_LOAD_UNCHECKED, docker.WORLD_STOPPED_ANYWAY]


def test_a_press_never_turns_a_world_that_can_hear_the_stop_into_a_forced_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The look comes first: a stop asked for regardless of a world that CAN hear it warns of
    nothing -- the rollback hands the wait a Cancel that is already set."""
    fake = _install(monkeypatch, "wow-vanilla", [("running", LOADED, LOADED_MASK)])
    control = docker.StopControl(also_anyway=(threading.Event(),))
    control.also_anyway[0].set()
    assert list(docker.world_load_steps(fake.spec, control)) == []
    assert fake.events == ["look 1"]


def test_the_replace_calls_its_hook_after_the_wait_and_before_the_signal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`before_signal` is the rebuild's "something may have been touched" line."""
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    docker.recreate_staged(
        fake.spec, tmp_path, before_signal=lambda: fake.events.append("about to signal")
    )
    assert fake.events[:4] == ["look 1", "look 2", "about to signal", "stop"]


def test_the_rollbacks_stop_leaves_the_database_up_and_waits_like_every_other(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    docker.stop_servers_staged(fake.spec, tmp_path)
    assert fake.events == ["look 1", "look 2", "stop"]
    assert fake.spec.db in fake.running


def test_stop_now_anyway_ends_the_wait_and_says_the_world_may_be_force_stopped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-vanilla", [("running", LOADING, LOADING_MASK)])
    said: list[str] = []
    controller, control = _controller(fake, tmp_path, said)
    fake.on_look = lambda: control.anyway.set() if fake.looks == 3 else None
    assert controller.stop() is True
    assert fake.events == ["look 1", "look 2", "look 3", "stop"]
    assert said == [docker.WORLD_STILL_LOADING, docker.WORLD_STOPPED_ANYWAY]
    assert "force-stopped" in said[-1]


def test_a_press_left_over_from_an_earlier_stop_does_not_end_the_next_one(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    controller, control = _controller(fake, tmp_path)
    control.anyway.set()
    assert controller.stop() is True
    assert fake.events == ["look 1", "look 2", "stop"]


def test_a_stop_given_up_while_it_waits_sends_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The app closing: the world is left running, never signalled mid-load."""
    fake = _install(monkeypatch, "wow-vanilla", [("running", LOADING, LOADING_MASK)])
    controller, control = _controller(fake, tmp_path)
    fake.on_look = lambda: control.abandon.set() if fake.looks == 2 else None
    with pytest.raises(docker.StopAbandoned, match="was not sent"):
        controller.stop()
    assert "stop" not in fake.events
    assert fake.spec.world in fake.running


def test_a_give_up_wakes_the_pause_between_looks_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Closing the app must not wait out a poll interval, let alone a load."""
    monkeypatch.setattr(docker, "_LOAD_POLL_SECONDS", 60.0)
    fake = _install(monkeypatch, "wow-vanilla", [("running", LOADING, LOADING_MASK)])
    controller, control = _controller(fake, tmp_path)
    timer = threading.Timer(0.2, control.abandon.set)
    timer.start()
    began = time.monotonic()
    try:
        with pytest.raises(docker.StopAbandoned):
            controller.stop()
    finally:
        timer.cancel()
    assert time.monotonic() - began < 5.0
    assert fake.events == ["look 1"]


def test_a_load_longer_than_the_start_up_timeout_is_still_waited_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No time limit: TBC's measured 46-minute first boot on a 9p share must not be forced.

    The clock jumps ten minutes at every reading, so twenty looks span hours of load -- far past
    any catalogue `timeout_s` -- and the stop still waits for the handler.
    """
    clock = [0.0]

    def monotonic() -> float:
        clock[0] += 600.0
        return clock[0]

    frames: list[Frame] = [("running", LOADING, LOADING_MASK)] * 20 + [
        ("running", LOADED, LOADED_MASK)
    ]
    fake = _install(monkeypatch, "wow-tbc", frames)
    controller, _ = _controller(fake, tmp_path)
    monkeypatch.setattr(docker.time, "monotonic", monotonic)
    assert controller.stop() is True
    assert fake.events == [f"look {n}" for n in range(1, 22)] + ["stop"]


def test_an_azerothcore_stop_asks_nothing_of_the_world_before_it_stops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _install(monkeypatch, "wow-wotlk", [("running", LOADING, LOADING_MASK)])
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.stop() is True
    assert fake.events == ["stop"]
    assert fake.execs == 0
    assert said == []


# -- the other stop paths share the one wait -------------------------------------


def test_removing_the_containers_waits_for_a_loading_world_too(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-tbc", frames)
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)
    assert controller.remove() is True
    assert fake.events == ["look 1", "look 2", "stop"]
    assert said[-1] == docker.WORLD_FINISHED_LOADING


def test_the_rebuilds_recreate_waits_for_a_loading_world_before_it_stops_the_servers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    assert docker.recreate_staged(fake.spec, tmp_path) is True
    assert fake.events[:3] == ["look 1", "look 2", "stop"]


def test_the_rebuild_stage_says_the_wait_in_its_own_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The generator shape the rebuild panel reads: the same sentences, yielded as they happen."""
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    lines = list(docker.world_load_steps(fake.spec, docker.StopControl()))
    assert lines == [docker.WORLD_STILL_LOADING, docker.WORLD_FINISHED_LOADING]
    assert "stop" not in fake.events, "the steps only wait; the recreate stops"


def test_stopping_the_other_server_waits_for_its_loading_world(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The ports-in-use offer: the server holding them is another game's, found by its name."""
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    vanilla = fake.spec
    tbc = CATALOG.get("wow-tbc").container_spec()
    said: list[str] = []
    controller = Controller(tbc, tmp_path)
    controller.stop_control = docker.StopControl(say=said.append)
    stopped = controller.stop_conflicting()
    assert set(stopped) == {vanilla.db, vanilla.auth, vanilla.world}
    assert fake.events == ["look 1", "look 2", "stop"]
    assert said[-1] == docker.WORLD_FINISHED_LOADING


def test_the_uninstall_waits_for_a_loading_world_and_says_so_through_its_own_control(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    uninstaller = purge.Uninstaller(
        game="wow-vanilla",
        server_dir=tmp_path,
        spec=fake.spec,
        image_refs=(),
        forget=lambda: None,
    )
    said: list[str] = []
    uninstaller.stop_control = docker.StopControl(say=said.append)
    assert uninstaller._real_remove_containers() is True
    assert fake.events == ["look 1", "look 2", "stop"]
    assert said == [docker.WORLD_STILL_LOADING, docker.WORLD_FINISHED_LOADING]


def test_a_press_left_over_from_an_earlier_uninstall_does_not_force_the_next(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frames: list[Frame] = [("running", LOADING, LOADING_MASK), ("running", LOADED, LOADED_MASK)]
    fake = _install(monkeypatch, "wow-vanilla", frames)
    uninstaller = purge.Uninstaller(
        game="wow-vanilla", server_dir=tmp_path, spec=fake.spec, image_refs=(), forget=lambda: None
    )
    said: list[str] = []
    uninstaller.stop_control = docker.StopControl(say=said.append)
    uninstaller.stop_control.anyway.set()
    assert uninstaller._real_remove_containers() is True
    assert docker.WORLD_STOPPED_ANYWAY not in said
    assert fake.events == ["look 1", "look 2", "stop"]


def test_a_rebuild_panel_that_stops_reading_gives_the_wait_up_and_is_waited_for() -> None:
    """The engine's thread-to-lines bridge: a consumer that goes away (the generator closed)
    must not leave a stop waiting behind it, and must not return before it has ended."""
    abandon = threading.Event()
    ended = threading.Event()

    def work(say: Callable[[str], None]) -> None:
        say(docker.WORLD_STILL_LOADING)
        abandon.wait(10.0)
        ended.set()

    lines = native._speaking(work, abandon)
    assert next(lines) == docker.WORLD_STILL_LOADING
    lines.close()  # type: ignore[attr-defined]
    assert abandon.is_set() and ended.is_set()


# -- T600: a world stuck at a failed update is not loading ----------------------------

FAILED_UPDATE = (
    "[DB Auto-Updater] Attempting to execute update 20260903063722_world, hash AB12.\n"
    "[1062] Duplicate entry '44070' for key 'PRIMARY'\n"
    "[DB Auto-Updater] Migration 20260903063722_world with hash AB12 failed to apply.\n"
)


def test_a_world_whose_log_ends_on_a_failed_update_is_stopped_at_once_and_says_why(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The core waits in a read on its console after `failed to apply.`: no signal handler, no
    SOAP, no load going on (tortoise-wow `AutoUpdater.cpp:236-239`, measured as a stand-in on
    m910q 2026-10-09: SIGTERM ignored for the whole grace, then SIGKILL). The wait would be
    forever; the stop goes at once and kills, because the signal is ignored.

    Mutation: remove the check from `world_load_steps()` and the world is looked at for ever
    (the fake's own cap) instead of killed on the first look.
    """
    fake = _install(monkeypatch, "wow-tortoise", [("running", FAILED_UPDATE, LOADING_MASK)])
    said: list[str] = []
    controller, _ = _controller(fake, tmp_path, said)

    assert controller.stop() is True

    assert fake.events == ["look 1", "look 2", "kill"]  # the second look re-checks the run
    assert len(said) == 1
    assert "20260903063722_world.sql" in said[0]
    assert "[1062] Duplicate entry '44070' for key 'PRIMARY'" in said[0]
    assert docker.WORLD_STILL_LOADING not in said


def test_a_log_that_went_on_after_a_failed_update_line_is_still_a_load_to_wait_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Only a log that ENDS on the failure is stuck: output after it means the run lives on."""
    later = FAILED_UPDATE + "Loading creature templates.\n"
    fake = _install(
        monkeypatch,
        "wow-tortoise",
        [("running", later, LOADING_MASK), ("running", later, LOADED_MASK)],
    )
    controller, _ = _controller(fake, tmp_path)
    assert controller.stop() is True
    assert "kill" not in fake.events
    assert fake.events[:2] == ["look 1", "look 2"]


def test_a_healthy_tortoise_load_is_never_killed_for_its_migration_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    log = (
        "[DB Auto-Updater] Migration 20260918120000_world with hash 0123 for module TortoiseBots "
        "exists in DB but not as file, old migration?\n"
    )
    fake = _install(
        monkeypatch,
        "wow-tortoise",
        [("running", log, LOADING_MASK), ("running", log, LOADED_MASK)],
    )
    controller, _ = _controller(fake, tmp_path)
    assert controller.stop() is True
    assert "kill" not in fake.events


def test_the_update_failure_is_read_from_this_runs_tail_not_the_whole_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A load of tens of thousands of lines is looked at every two seconds."""
    seen: list[list[str]] = []

    def fake(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return _completed("x\n")

    monkeypatch.setattr(runner, "run", fake)
    docker._logs("tortoise-mangosd", this_run_only=True, since="2026-10-09T01:00:00Z", tail=20)
    assert seen[0][seen[0].index("--tail") + 1] == "20"
    assert "--since" in seen[0]


def test_a_world_the_restart_policy_replaced_since_the_look_is_not_killed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The run the failure line was read from must still be the run that is killed.

    Mutation: kill without the second `container_state()` and this kills the new run.
    """
    frames: list[Frame] = [
        ("running", FAILED_UPDATE, LOADING_MASK),
        ("running", LOADING, LOADING_MASK),
        ("running", LOADED, LOADED_MASK),
    ]
    fake = _install(monkeypatch, "wow-tortoise", frames, started=[STARTED, RESTARTED, RESTARTED])
    controller, _ = _controller(fake, tmp_path)
    assert controller.stop() is True
    assert "kill" not in fake.events
