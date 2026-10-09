"""T568 (Opus adversarial review): `Controller.start` and "Stop the other server and start this
one" reserve the server BEFORE the first thing they change, and the other server too.

The docker CLI is `support_fake_docker`'s; the controller's reads and writes are recorded.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import HANG_BOUND
from tests.support_fake_docker import calls as fake_calls
from tests.support_fake_docker import containers as fake_containers
from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from yulon import docker, platform
from yulon.catalog import native
from yulon.controller import Controller

IMAGE = "yulon.local/wotlk-server:native"
SPEC = docker.ContainerSpec(db="ac-database", auth="ac-auth", world="ac-world", ports=(3724,))


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text(IMAGE + "\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


def _holds(
    state: Path, folder: Path, press: str = "Rebuild the server…"
) -> subprocess.Popen[bytes]:
    ident = docker.folder_id(folder)
    name = docker.SERVER_CLAIM_PREFIX + str(ident)
    proc = subprocess.Popen(
        [
            str(state.parent / "fake-docker"),
            "run", "--rm", "-i", "--name", name,
            "--label", f"{docker.OWNER_LABEL}=someone-else",
            "--label", f"{docker.CLAIM_LABEL}=theirs",
            "--label", f"{docker.PRESS_LABEL}={press}",
            "--label", f"{docker.WHO_LABEL}=pk@THEIR-PC (Windows)",
            "--entrypoint", "sh", IMAGE, "-c", "cat >/dev/null",
        ],
        stdin=subprocess.PIPE,
    )  # fmt: skip
    deadline = time.monotonic() + HANG_BOUND
    while name not in fake_containers(state):
        assert time.monotonic() < deadline
        time.sleep(0.02)
    return proc


class _Recorded(Controller):
    """A controller whose every step before `start_staged()` is recorded, and does nothing."""

    def __init__(self, server_dir: Path) -> None:
        super().__init__(SPEC, server_dir)
        self.steps: list[str] = []

    def refuse_start(self) -> None:
        self.steps.append("refuse_start")

    def port_conflicts(self) -> list[str]:
        self.steps.append("port_conflicts")
        return []

    def refuse_a_missing_database(self) -> None:
        self.steps.append("start the database to look")

    def _ask_before_the_servers(self) -> None:
        self.steps.append("realm-port UPDATE")

    def _before_the_servers_start(self) -> None:
        self.steps.append("realm flag, dashboard")

    def _put_back_the_zone_file(self) -> str | None:
        self.steps.append("zone file")
        return None


@pytest.mark.parametrize("method", ["start", "stop_conflicting_and_start"])
def test_a_start_under_another_yulons_hold_changes_nothing_at_all(
    fake_docker: Path, tmp_path: Path, method: str
) -> None:
    """Mutation this catches: the reservation taken only at `start_staged()`, at the END (the
    database started, the SQL sent and the dashboard written beside another Yu'lon's Rebuild)."""
    server = tmp_path / "server"
    server.mkdir()
    theirs = _holds(fake_docker, server)
    try:
        controller = _Recorded(server)
        with pytest.raises(docker.ServerReserved):
            getattr(controller, method)()
        assert controller.steps == [], controller.steps
    finally:
        theirs.kill()


def test_a_start_with_nobody_else_holding_goes_through_every_step(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    server.mkdir()
    monkeypatch.setattr(docker, "start_staged", lambda *_a, **_kw: True)
    controller = _Recorded(server)
    controller.start()
    assert "realm flag, dashboard" in controller.steps and "zone file" in controller.steps


def test_stopping_the_other_server_reserves_its_folder_and_is_refused_while_it_is_held(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The raw `stop_containers` of "Stop the other server and start this one" must reserve the
    OTHER server, as a Stop does. Mutation: the other folder never reserved."""
    ours, other = tmp_path / "ours", tmp_path / "other"
    ours.mkdir()
    other.mkdir()
    (other / native.STATE_FILE).write_text("{}", encoding="utf-8")  # a Yu'lon server
    stopped: list[tuple[list[str], bool]] = []

    def stop_containers(names: list[str], **_kw: Any) -> None:
        stopped.append((names, docker.reservation_held_here(other)))

    monkeypatch.setattr(docker, "stop_containers", stop_containers)
    monkeypatch.setattr(docker, "container_project", lambda *_a, **_kw: "other")
    monkeypatch.setattr(docker, "project_containers", lambda *_a, **_kw: ["o-db", "o-world"])
    monkeypatch.setattr(docker, "container_working_dir", lambda *_a, **_kw: str(other))
    controller = _Recorded(ours)
    controller.port_conflicts = lambda: ["o-world"]  # type: ignore[method-assign]

    theirs = _holds(fake_docker, other, press="Update the server to latest…")
    try:
        with pytest.raises(docker.ServerReserved):
            controller.stop_conflicting()
        assert stopped == [], "the other server was stopped under another Yu'lon's job"
    finally:
        theirs.kill()
    # The fake daemon keeps a container whose CLI was killed; a real one removes it (--rm).
    (
        fake_docker / "containers" / (docker.SERVER_CLAIM_PREFIX + str(docker.folder_id(other)))
    ).unlink()

    assert controller.stop_conflicting() == ["o-db", "o-world"]
    assert stopped == [(["o-db", "o-world"], True)], "the stop ran without the other's reservation"


# ------------------------------------------------------------------ T607 item 7


class _InDistro(_Recorded):
    def __init__(self, server_dir: Path, distro: str) -> None:
        super().__init__(server_dir)
        self.wsl_distro = distro


def _wsl_other(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, list[tuple[list[str], bool]]]:
    """Our server beside an OTHER server whose working dir is a Linux path inside our distro.

    Only the UNC spelling of that path (`platform.wsl_unc_path`) is a folder this host can
    read, as on a Windows host with the other server in the WSL distro this server lives in.
    """
    ours, other = tmp_path / "ours", tmp_path / "unc" / "other"
    ours.mkdir()
    other.mkdir(parents=True)
    (other / native.STATE_FILE).write_text("{}", encoding="utf-8")  # a Yu'lon server
    stopped: list[tuple[list[str], bool]] = []

    def stop_containers(names: list[str], **_kw: Any) -> None:
        stopped.append((names, docker.reservation_held_here(other)))

    monkeypatch.setattr(docker, "stop_containers", stop_containers)
    monkeypatch.setattr(docker, "container_project", lambda *_a, **_kw: "other")
    monkeypatch.setattr(docker, "project_containers", lambda *_a, **_kw: ["o-db", "o-world"])
    monkeypatch.setattr(docker, "container_working_dir", lambda *_a, **_kw: "/home/u/other")
    monkeypatch.setattr(
        platform,
        "wsl_unc_path",
        lambda distro, inside: other if (distro, inside) == ("Ubuntu", "/home/u/other") else None,
    )
    monkeypatch.setattr("yulon.wsl.release", lambda *_a, **_kw: True)
    real_prefix = platform.docker_prefix
    # The fake CLI stands for the distro's docker, whatever distro is asked.
    monkeypatch.setattr(platform, "docker_prefix", lambda distro=None, **_kw: real_prefix(None))
    return ours, other, stopped


def test_a_server_inside_our_wsl_distro_is_reserved_when_the_other_one_is_stopped(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ours, other, stopped = _wsl_other(tmp_path, monkeypatch)
    controller = _InDistro(ours, "Ubuntu")
    controller.port_conflicts = lambda: ["o-world"]  # type: ignore[method-assign]
    assert controller.stop_conflicting() == ["o-db", "o-world"]
    assert stopped == [(["o-db", "o-world"], True)], "the WSL-resident server was not reserved"


def test_a_wsl_resident_server_held_by_another_yulon_is_not_stopped(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ours, other, stopped = _wsl_other(tmp_path, monkeypatch)
    controller = _InDistro(ours, "Ubuntu")
    controller.port_conflicts = lambda: ["o-world"]  # type: ignore[method-assign]
    theirs = _holds(fake_docker, other, press="Update the server to latest…")
    try:
        with pytest.raises(docker.ServerReserved):
            controller.stop_conflicting()
        assert stopped == [], "the other server was stopped under another Yu'lon's job"
    finally:
        theirs.kill()


def test_a_linux_path_is_not_turned_into_a_wsl_share_for_a_server_outside_any_distro(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No `wsl_distro`: the daemon is the host's, and a path this host cannot see stays unseen."""
    ours, other, stopped = _wsl_other(tmp_path, monkeypatch)
    controller = _Recorded(ours)
    controller.port_conflicts = lambda: ["o-world"]  # type: ignore[method-assign]
    assert controller.stop_conflicting() == ["o-db", "o-world"]
    assert stopped == [(["o-db", "o-world"], False)]


def test_a_wsl_share_that_is_not_there_is_left_unreserved_and_the_stop_goes_ahead(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ours, other, stopped = _wsl_other(tmp_path, monkeypatch)
    monkeypatch.setattr(platform, "wsl_unc_path", lambda *_a: tmp_path / "no-such-share")
    controller = _InDistro(ours, "Ubuntu")
    controller.port_conflicts = lambda: ["o-world"]  # type: ignore[method-assign]
    assert controller.stop_conflicting() == ["o-db", "o-world"]
    assert stopped == [(["o-db", "o-world"], False)]


def test_a_blocker_that_is_not_a_yulon_server_is_stopped_as_before_and_gets_no_id_file(
    fake_docker: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The user's own `~/myproj` MySQL compose holding the port: no id file written into it, no
    reservation container started from its image, and the stop is not refused when that image
    cannot run one (a distroless image). Opus review of the rework.

    Mutation this catches: every visible working folder reserved, not only Yu'lon's.
    """
    ours, myproj, yulon_other = tmp_path / "ours", tmp_path / "myproj", tmp_path / "yulon-other"
    for folder in (ours, myproj, yulon_other):
        folder.mkdir()
    (yulon_other / native.STATE_FILE).write_text("{}", encoding="utf-8")
    (fake_docker / "claim-refused").write_text("", encoding="utf-8")  # an image with no `sh`
    dirs = {"my-mysql": str(myproj), "y-world": str(yulon_other)}
    stopped: list[list[str]] = []
    monkeypatch.setattr(docker, "stop_containers", lambda names, **_kw: stopped.append(names))
    monkeypatch.setattr(docker, "container_project", lambda *_a, **_kw: None)
    monkeypatch.setattr(docker, "container_working_dir", lambda name, **_kw: dirs[name])
    controller = _Recorded(ours)
    controller.port_conflicts = lambda: ["my-mysql"]  # type: ignore[method-assign]

    assert controller.stop_conflicting() == ["my-mysql"]
    assert stopped == [["my-mysql"]]
    assert not (myproj / docker.FOLDER_ID_FILE).exists(), "an id file was written into ~/myproj"
    assert [c for c in fake_calls(fake_docker) if c.startswith("run ")] == []

    # A Yu'lon server in the way IS reserved, and a refused claim refuses the stop.
    controller.port_conflicts = lambda: ["y-world"]  # type: ignore[method-assign]
    with pytest.raises(docker.ServerReservationUnavailable):
        controller.stop_conflicting()
    assert stopped == [["my-mysql"]]


def test_the_conf_half_of_repair_server_files_reserves_too(
    fake_docker: Path, tmp_path: Path
) -> None:
    """Opus review: "Repair server files" had its compose half reserved and its conf half not.

    Mutation this catches: the conf repair writing straight through.
    """
    from yulon import install_wiring
    from yulon.catalog.catalog import load_catalog
    from yulon.catalog.installer import InstallerError

    entry = load_catalog().get("wow-wotlk")
    server = tmp_path / "server"
    server.mkdir()
    (server / native.STATE_FILE).write_text("{}", encoding="utf-8")
    route = install_wiring.repair_confs_for_app(entry, server)
    assert route is not None
    theirs = _holds(fake_docker, server, press="Update the server to latest…")
    try:
        with pytest.raises(InstallerError, match="Another Yu'lon is working on"):
            route.repair()
    finally:
        theirs.kill()
    assert sorted(p.name for p in server.iterdir() if p.name != docker.FOLDER_ID_FILE) == [
        native.STATE_FILE
    ], "a conf was written under another Yu'lon's job"
