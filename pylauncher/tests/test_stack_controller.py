"""Tests for the stack controller (`yulon.stack_controller`): status, Start, Stop and the console.

Every Docker question is a `StackControlSeams` fake answered per container, so the
statuses a stack can be in — all up, some still loading, all stopped, a daemon that
will not answer — are each a plain table.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import docker
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.catalog.stack_install import stack_of
from yulon.single_controller import RUNNING, STARTING, STOPPED, UNKNOWN, SingleServerError
from yulon.stack_controller import StackController, StackControlSeams

FFXI: SingleEntry = load_catalog().get_single("ffxi")
STACK = stack_of(FFXI)
LISTENERS = [s.container for s in STACK.listeners]


def _ready(container: str) -> str:
    service = next(s for s in STACK.services if s.container == container)
    return "\n".join(service.ready)


@dataclass
class Fake:
    states: dict[str, docker.ContainerState] = field(default_factory=dict)
    logs: dict[str, docker.RunLog] = field(default_factory=dict)
    projects: dict[str, str | None] = field(default_factory=dict)
    port_holders: list[str] = field(default_factory=list)
    started: list[Path] = field(default_factory=list)
    stopped: list[tuple[Path, float | None]] = field(default_factory=list)
    followed: list[tuple[Path, tuple[str, ...]]] = field(default_factory=list)
    up_error: Exception | None = None

    def all_up(self, project: str) -> Fake:
        for name in LISTENERS:
            self.states[name] = docker.ContainerState(status="running")
            self.logs[name] = docker.RunLog(_ready(name), True)
            self.projects[name] = project
        return self

    def up(self, server_dir: Path, **_: Any) -> None:
        if self.up_error is not None:
            raise self.up_error
        self.started.append(server_dir)

    def stop(self, server_dir: Path, *, timeout: float | None = None) -> None:
        self.stopped.append((server_dir, timeout))

    def follow(self, server_dir: Path, services: Sequence[str]) -> Iterator[str]:
        self.followed.append((server_dir, tuple(services)))
        return iter(["map  | The map-server is ready to work"])

    def seams(self) -> StackControlSeams:
        return StackControlSeams(
            platform_id=lambda: "linux",
            state_of=lambda name: self.states.get(name, docker.ContainerState(missing=True)),
            run_log=lambda name: self.logs.get(name, docker.RunLog("", True)),
            container_project=lambda name: self.projects.get(name),
            port_conflicts=lambda _ports: list(self.port_holders),
            up=self.up,
            stop=self.stop,
            follow=self.follow,
        )


def _controller(fake: Fake, server_dir: Path) -> StackController:
    return StackController(FFXI, server_dir, seams=fake.seams())


def test_no_containers_at_all_is_stopped(tmp_path: Path) -> None:
    assert _controller(Fake(), tmp_path).status() == STOPPED


def test_every_listener_up_and_ready_is_running(tmp_path: Path) -> None:
    controller = _controller(Fake(), tmp_path)
    fake = Fake().all_up(controller.project())
    assert _controller(fake, tmp_path).status() == RUNNING


def test_a_listener_still_loading_is_starting(tmp_path: Path) -> None:
    fake = Fake().all_up("p")
    fake.logs["ffxi-map"] = docker.RunLog("[info] loading zones", True)
    assert _controller(fake, tmp_path).status() == STARTING


def test_a_log_that_may_hold_an_older_run_is_never_running(tmp_path: Path) -> None:
    fake = Fake().all_up("p")
    fake.logs["ffxi-world"] = docker.RunLog(_ready("ffxi-world"), False)
    assert _controller(fake, tmp_path).status() == STARTING


def test_one_listener_in_restart_backoff_is_starting(tmp_path: Path) -> None:
    fake = Fake().all_up("p")
    fake.states["ffxi-connect"] = docker.ContainerState(status="restarting")
    assert _controller(fake, tmp_path).status() == STARTING


def test_a_daemon_that_will_not_answer_is_unknown_never_stopped(tmp_path: Path) -> None:
    fake = Fake().all_up("p")
    fake.states["ffxi-database"] = docker.ContainerState(status="")
    assert _controller(fake, tmp_path).status() == UNKNOWN


def test_every_listener_exited_is_stopped(tmp_path: Path) -> None:
    fake = Fake()
    for name in LISTENERS:
        fake.states[name] = docker.ContainerState(status="exited")
    assert _controller(fake, tmp_path).status() == STOPPED


def test_start_runs_the_whole_project(tmp_path: Path) -> None:
    fake = Fake()
    _controller(fake, tmp_path).start()
    assert fake.started == [tmp_path]


def test_start_refuses_a_stranger_on_our_ports_and_names_it(tmp_path: Path) -> None:
    fake = Fake(port_holders=["ffxi-proto-connect-1", "ffxi-map"])
    with pytest.raises(SingleServerError, match="ffxi-proto-connect-1 is running"):
        _controller(fake, tmp_path).start()
    assert fake.started == []


def test_a_failed_start_says_so_in_a_sentence(tmp_path: Path) -> None:
    fake = Fake(up_error=docker.DockerCommandError("port is already allocated"))
    with pytest.raises(SingleServerError, match="could not be started: port is already allocated"):
        _controller(fake, tmp_path).start()


def test_stop_stops_our_running_project(tmp_path: Path) -> None:
    controller = _controller(Fake(), tmp_path)
    fake = Fake().all_up(controller.project())
    assert _controller(fake, tmp_path).stop() is True
    assert fake.stopped == [(tmp_path, 120.0)]


def test_stop_with_nothing_running_is_nothing_to_stop(tmp_path: Path) -> None:
    fake = Fake()
    assert _controller(fake, tmp_path).stop() is False
    assert fake.stopped == []


def test_stop_never_stops_another_folders_server(tmp_path: Path) -> None:
    fake = Fake().all_up("yulon-ffxi-someoneelse")
    with pytest.raises(SingleServerError, match="was not started from"):
        _controller(fake, tmp_path).stop()
    assert fake.stopped == []


def test_the_console_follows_every_listener_by_service(tmp_path: Path) -> None:
    fake = Fake()
    lines = list(_controller(fake, tmp_path).follow_logs())
    assert lines == ["map  | The map-server is ready to work"]
    assert fake.followed == [(tmp_path, tuple(s.service for s in STACK.listeners))]


def test_a_stack_keeps_no_address_and_no_account(tmp_path: Path) -> None:
    controller = _controller(Fake(), tmp_path)
    assert controller.advertised() is None
    assert controller.session_account() is None
    with pytest.raises(SingleServerError, match="this computer only"):
        controller.advertise("192.168.0.60")
