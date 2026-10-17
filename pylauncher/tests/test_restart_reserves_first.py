"""T761: a Restart, a Recreate and the Tortoise bot restart take the server's reservation first.

`Controller.start()` reserves the server BEFORE its first change (T568): the database
`refuse_a_missing_database()` starts and the realm-port UPDATE `before_servers` sends.
The composites that stop something before their start asked `refuse_before_a_stop()` --
which asks `before_servers` -- and only then entered `docker.lifecycle()`, so under another
Yu'lon's reservation they had already written to the auth database when they were refused.
`Controller.restart()` and `Controller.recreate()` enter the reservation before they ask
anything; the bot restart goes through `restart()`.

Driven through the real reservation, against a fake Docker CLI holding another Yu'lon's
reservation container (as `test_server_reservation.py` does). From the 2026-10-12 audit's
proof `proofs/lifecycle/test_restart_writes_before_its_reservation.py`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from tests.test_server_reservation import (  # noqa: F401
    _another_yulon_holds,
    _name,
    fake_docker,
    server,
)
from yulon import docker
from yulon.controller import Controller
from yulon.controller_wow_tortoise import botpool

SPEC = docker.ContainerSpec(db="x-db", auth="x-auth", world="x-world", ports=(1,))


def _controller(folder: Path, asked: list[str]) -> Controller:
    def before_servers() -> None:
        asked.append("before_servers")  # stands for the realm-port UPDATE (T552)
        return None

    return Controller(SPEC, folder, before_servers=before_servers)


def _under_another_yulon(
    fake_docker: Path, server: Path, press: Callable[[], object]  # noqa: F811
) -> None:
    theirs = _another_yulon_holds(fake_docker, _name(server))
    try:
        press()
    finally:
        theirs.kill()


def test_start_under_another_yulons_reservation_writes_nothing(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """The control: Start reserved first already (T568)."""
    asked: list[str] = []

    def press() -> None:
        with pytest.raises(docker.ServerReserved):
            _controller(server, asked).start()

    _under_another_yulon(fake_docker, server, press)
    assert asked == []


def test_restart_under_another_yulons_reservation_writes_nothing(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    asked: list[str] = []

    def press() -> None:
        with pytest.raises(docker.ServerReserved):
            _controller(server, asked).restart()

    _under_another_yulon(fake_docker, server, press)
    assert asked == []


def test_recreate_under_another_yulons_reservation_writes_nothing(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    asked: list[str] = []

    def press() -> None:
        with pytest.raises(docker.ServerReserved):
            _controller(server, asked).recreate()

    _under_another_yulon(fake_docker, server, press)
    assert asked == []


def test_the_bot_restart_under_another_yulons_reservation_writes_nothing(
    fake_docker: Path, server: Path  # noqa: F811
) -> None:
    """Refused as `StopFailed`, which its callers read as "nothing was started"."""
    asked: list[str] = []

    def press() -> None:
        with pytest.raises(botpool.StopFailed):
            botpool.restart_world(_controller(server, asked))

    _under_another_yulon(fake_docker, server, press)
    assert asked == []


class _Steps(Controller):
    """The primitives recorded, so the composite's own order is what is read.

    `stop()`, `remove()` and `start()` are replaced only here: each is its own
    lifecycle command, tested on its own. Every refusal is asked for real.
    """

    def __init__(self, folder: Path, steps: list[str], *, fail: str = "") -> None:
        def before_servers() -> None:
            steps.append("asked")
            return None

        super().__init__(SPEC, folder, before_servers=before_servers)
        self.steps = steps
        self.fail = fail

    def refuse_a_missing_database(self) -> None:
        self.steps.append("database")

    def refuse_a_missing_image(self) -> None:
        return None

    def _step(self, name: str) -> bool:
        self.steps.append(name)
        if name == self.fail:
            raise RuntimeError(f"no {name}")
        return True

    def stop(self) -> bool:
        return self._step("stop")

    def remove(self) -> bool:
        return self._step("remove")

    def start(self, *, asked_before_the_servers: bool = False) -> None:
        self._step("start")


def test_restart_asks_then_stops_then_starts_and_says_between(server: Path) -> None:  # noqa: F811
    steps: list[str] = []
    controller = _Steps(server, steps)
    assert controller.restart(stopped=lambda: steps.append("stopped")) is True
    assert steps == ["database", "asked", "stop", "stopped", "start"]


def test_a_refused_restart_stops_nothing(server: Path) -> None:  # noqa: F811
    steps: list[str] = []
    controller = _Steps(server, steps)
    controller.before_servers = lambda: "the realm row could not be written"
    with pytest.raises(Exception, match="realm row"):
        controller.restart(stopping=lambda: steps.append("stopping"))
    assert steps == ["database"]


def test_recreate_asks_then_removes_then_starts(server: Path) -> None:  # noqa: F811
    steps: list[str] = []
    assert _Steps(server, steps).recreate() is True
    assert steps == ["database", "asked", "remove", "start"]


@pytest.mark.parametrize("fail", ["stop", "start"])
def test_the_bot_restart_says_which_half_failed_through_the_real_restart(
    server: Path, fail: str  # noqa: F811
) -> None:
    steps: list[str] = []
    with pytest.raises(Exception, match=f"no {fail}") as caught:
        botpool.restart_world(_Steps(server, steps, fail=fail))
    assert isinstance(caught.value, botpool.StopFailed) == (fail == "stop")
    assert ("start" in steps) == (fail == "start")


def test_a_held_refusal_from_the_bot_restart_s_start_is_the_start_s_own(
    server: Path,  # noqa: F811
) -> None:
    """Only a hold met before anything was asked is a failed stop: after the stop, the world
    may be the NEW run, so a start that met a hold keeps its own type (T144)."""

    class _HeldAtStart(_Steps):
        def start(self, *, asked_before_the_servers: bool = False) -> None:
            self.steps.append("start")
            raise docker.ServerHeldError("held at the start")

    steps: list[str] = []
    with pytest.raises(docker.ServerHeldError) as caught:
        botpool.restart_world(_HeldAtStart(server, steps))
    assert not isinstance(caught.value, botpool.StopFailed)
    assert steps[-2:] == ["stop", "start"]
