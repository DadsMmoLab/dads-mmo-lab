"""Tests for a single-container install's lifecycle (`yulon.single_controller`)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from yulon import docker
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.single_controller import (
    RUNNING,
    STARTING,
    STOPPED,
    UNKNOWN,
    SingleController,
    SingleControlSeams,
    SingleServerError,
)

DDON: SingleEntry = load_catalog().get_single("ddon")
READY = "Listening on 0.0.0.0:52000.\nListening on 0.0.0.0:52100.\n"


@dataclass
class Docker:
    state: docker.ContainerState = field(default_factory=lambda: docker.ContainerState("running"))
    log: docker.RunLog = field(default_factory=lambda: docker.RunLog(READY, True))
    owner: str | None = None
    holders: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    up_error: str = ""

    def up(self, server_dir: Path, service: str, **_: Any) -> None:
        if self.up_error:
            raise docker.DockerCommandError(self.up_error)
        self.calls.append(f"up {service}")

    def stop(self, containers: list[str], **_: Any) -> None:
        self.calls.append(f"stop {','.join(containers)}")

    def controller(self, server_dir: Path) -> SingleController:
        seams = SingleControlSeams(
            platform_id=lambda: "linux",
            state_of=lambda _c: self.state,
            run_log=lambda _c: self.log,
            container_project=lambda _c: self.owner,
            port_conflicts=lambda _p: list(self.holders),
            up=self.up,
            stop=self.stop,
            follow=lambda _c: iter(()),
        )
        return SingleController(DDON, server_dir, seams=seams)


@pytest.mark.parametrize(
    ("state", "log", "status"),
    [
        (docker.ContainerState("running"), docker.RunLog(READY, True), RUNNING),
        (docker.ContainerState("running"), docker.RunLog("loading quests", True), STARTING),
        (docker.ContainerState("running"), docker.RunLog(READY, False), STARTING),
        (docker.ContainerState("restarting"), docker.RunLog(READY, True), STARTING),
        (docker.ContainerState("exited"), docker.RunLog(READY, True), STOPPED),
        (docker.ContainerState(missing=True), docker.RunLog("", True), STOPPED),
        (docker.ContainerState(""), docker.RunLog("", True), UNKNOWN),
    ],
)
def test_status_is_running_only_once_this_run_listens(
    tmp_path: Path, state: docker.ContainerState, log: docker.RunLog, status: str
) -> None:
    """An earlier run's ready lines (`this_run_only` False) never count for this one."""
    fake = Docker(state=state, log=log)
    assert fake.controller(tmp_path).status() == status


def test_start_brings_the_one_service_up(tmp_path: Path) -> None:
    fake = Docker(holders=["ddon-server"])  # our own container on our ports is fine
    fake.controller(tmp_path).start()
    assert fake.calls == ["up ddon-server"]


def test_start_refuses_while_a_stranger_holds_a_port(tmp_path: Path) -> None:
    fake = Docker(holders=["ac-worldserver"])
    with pytest.raises(SingleServerError, match="ac-worldserver is running"):
        fake.controller(tmp_path).start()
    assert fake.calls == []


def test_a_docker_failure_on_start_is_said_in_a_sentence(tmp_path: Path) -> None:
    fake = Docker(up_error="port is already allocated")
    with pytest.raises(SingleServerError, match="could not be started: port is already"):
        fake.controller(tmp_path).start()


def test_stop_stops_our_own_running_container(tmp_path: Path) -> None:
    fake = Docker()
    control = fake.controller(tmp_path)
    fake.owner = control.project()
    assert control.stop() is True
    assert fake.calls == ["stop ddon-server"]


def test_stop_of_a_stopped_server_does_nothing(tmp_path: Path) -> None:
    fake = Docker(state=docker.ContainerState("exited"))
    assert fake.controller(tmp_path).stop() is False
    assert fake.calls == []


def test_stop_never_stops_another_folder_s_server(tmp_path: Path) -> None:
    """Two folders of one game share the container name; the running one may be the other's."""
    fake = Docker(owner="yulon-ddon-0000other")
    with pytest.raises(SingleServerError, match="was left running"):
        fake.controller(tmp_path).stop()
    assert fake.calls == []


def _with_address_file(tmp_path: Path) -> Path:
    assert DDON.advertise is not None
    target = tmp_path / DDON.advertise.dest
    target.parent.mkdir(parents=True)
    target.write_text("#Id,Name,IpAddress,Port\n10,Local Host,127.0.0.1,52000", encoding="utf-8")
    return tmp_path


def test_a_new_address_restarts_a_running_server_so_it_is_read(tmp_path: Path) -> None:
    fake = Docker()
    control = fake.controller(_with_address_file(tmp_path))
    fake.owner = control.project()
    assert control.advertise("192.168.0.60") is True
    assert fake.calls == ["stop ddon-server", "up ddon-server"]
    assert control.advertised() == "192.168.0.60"


def test_a_stopped_server_reads_the_new_address_at_its_next_start(tmp_path: Path) -> None:
    fake = Docker(state=docker.ContainerState("exited"))
    control = fake.controller(_with_address_file(tmp_path))
    assert control.advertise("192.168.0.60") is False
    assert fake.calls == []


def test_the_same_address_again_restarts_nothing(tmp_path: Path) -> None:
    fake = Docker()
    control = fake.controller(_with_address_file(tmp_path))
    assert control.advertise("127.0.0.1") is False
    assert fake.calls == []


def test_an_address_file_problem_is_a_server_error_with_its_sentence(tmp_path: Path) -> None:
    with pytest.raises(SingleServerError, match="could not be read"):
        Docker().controller(tmp_path).advertised()
