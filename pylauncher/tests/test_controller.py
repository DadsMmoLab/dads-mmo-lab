"""Tests for the base `Controller` abstraction (roadmap Phase 1.4).

All subprocess calls are mocked at the `yulon.runner.run` boundary (the same
seam `tests/test_docker.py` uses), so nothing here needs a real Docker daemon.
The point of these tests is the *controller* contract: a per-game subclass
inherits start/stop/status/polling with zero reimplementation, and `start()`
is guarded by the shared single-instance/port-conflict check (README §12).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import docker, runner
from yulon.controller import Controller, InstallStatus, PortConflictError
from yulon.controller_wow_wotlk import docker_ctl
from yulon.controller_wow_wotlk.controller import WotlkController

SPEC = docker.ContainerSpec(db="t-db", auth="t-auth", world="t-world", ports=(1111, 2222))
SERVER_DIR = Path("/tmp/t-server")


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class _FakeRunner:
    """Records every `runner.run` argv and answers `docker ps` from a canned table.

    `ps_lines` answers BOTH `docker ps` formats, which is how the existing tests
    use it: a test that cares about ports puts `name<TAB>ports` lines in it, and
    the name column is then also what the ownership check reads.
    """

    project = "t-project"

    def __init__(self, ps_lines: str = "") -> None:
        self.calls: list[list[str]] = []
        self.cwds: list[Path | None] = []
        self.ps_lines = ps_lines
        self.health = "healthy\n"

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        self.cwds.append(cwd)
        if cmd[:2] == ["docker", "ps"]:
            if "{{.Ports}}" in cmd[-1]:
                return _completed(0, self.ps_lines)
            # The name-only format: drop the ports column so an ownership check
            # sees names, not "t-world\t0.0.0.0:2222->2222/tcp".
            names = [line.split("\t")[0] for line in self.ps_lines.splitlines() if line.strip()]
            return _completed(0, "".join(name + "\n" for name in names))
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(0, '{"name": "' + self.project + '"}')
        if cmd[:3] == ["docker", "compose", "stop"]:
            self.ps_lines = ""  # compose really stopped them
            return _completed()
        if cmd[:5] == ["docker", "compose", "up", "-d", "--no-deps"]:
            # `start_staged()` confirms with `docker ps` that they really came
            # up; a double that stayed silent would mean "nothing started".
            # Whatever compose was asked to start is what comes up.
            self.ps_lines = "".join(f"{name}\n" for name in cmd[5:])
            return _completed()
        if cmd[:2] == ["docker", "inspect"]:
            # One verb, several questions: ownership asks for the compose project
            # label, the start path asks for health. Answering both with
            # "healthy" would make every container look like a stranger.
            if any(docker.PROJECT_LABEL in arg for arg in cmd):
                return _completed(0, self.project + "\n")
            return _completed(0, self.health)  # so start()'s health wait never polls for real
        return _completed()


@pytest.fixture
def fake_runner(monkeypatch: pytest.MonkeyPatch) -> _FakeRunner:
    fake = _FakeRunner()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def test_controller_exposes_spec_and_server_dir() -> None:
    """The base controller is composed from a spec + server dir, both readable."""
    ctl = Controller(SPEC, SERVER_DIR)
    assert ctl.spec is SPEC
    assert ctl.server_dir == SERVER_DIR


def test_start_runs_compose_up_in_server_dir(fake_runner: _FakeRunner) -> None:
    """With no conflicting containers, `start()` delegates to `docker.start()`."""
    Controller(SPEC, SERVER_DIR).start()
    up = ["docker", "compose", "up", "-d", "--no-deps", SPEC.db, SPEC.auth, SPEC.world]
    assert up in fake_runner.calls
    assert fake_runner.cwds[fake_runner.calls.index(up)] == SERVER_DIR


def test_start_is_blocked_by_a_foreign_container_on_our_ports(fake_runner: _FakeRunner) -> None:
    """README §12: another install binding our ports blocks `start()` before compose runs."""
    fake_runner.ps_lines = "other-world\t0.0.0.0:2222->2222/tcp\n"
    with pytest.raises(PortConflictError) as excinfo:
        Controller(SPEC, SERVER_DIR).start()
    assert excinfo.value.containers == ["other-world"]
    assert "other-world" in str(excinfo.value)
    assert ["docker", "compose", "up", "-d"] not in fake_runner.calls


def test_start_is_not_blocked_by_our_own_containers(fake_runner: _FakeRunner) -> None:
    """Our own containers already binding the ports (a restart) are not a conflict."""
    fake_runner.ps_lines = "t-world\t0.0.0.0:2222->2222/tcp\nt-auth\t0.0.0.0:1111->1111/tcp\n"
    Controller(SPEC, SERVER_DIR).start()
    assert any(
        cmd[:5] == ["docker", "compose", "up", "-d", "--no-deps"] for cmd in fake_runner.calls
    )


def test_port_conflicts_filters_out_own_containers(fake_runner: _FakeRunner) -> None:
    """`port_conflicts()` reports only *foreign* containers, not this install's own."""
    fake_runner.ps_lines = (
        "t-world\t0.0.0.0:2222->2222/tcp\n"
        "stranger\t0.0.0.0:1111->1111/tcp\n"
        "bystander\t3306/tcp\n"
    )
    assert Controller(SPEC, SERVER_DIR).port_conflicts() == ["stranger"]


def test_stop_keeps_the_containers_so_the_next_start_is_staged(
    fake_runner: _FakeRunner,
) -> None:
    """`stop()` delegates to `docker.stop_staged()`, which never removes containers.

    The regression this guards is subtle and was found only on a real daemon:
    `compose down` removes the containers, so the *next* `start()` finds nothing
    to start by name and falls back to `compose up -d` — re-running the one-shot
    database import that `start_staged()` exists to avoid. Start and stop only
    hold that invariant as a pair.
    """
    # Something of ours has to be up, or there is correctly nothing to stop.
    fake_runner.ps_lines = "t-db\nt-auth\nt-world\n"
    Controller(SPEC, SERVER_DIR).stop()
    assert any(c[:3] == ["docker", "compose", "stop"] for c in fake_runner.calls)
    assert not any(
        cmd[:3] == ["docker", "compose", "down"] for cmd in fake_runner.calls
    ), "a stop removed containers"


def test_status_reports_which_of_our_containers_are_running(fake_runner: _FakeRunner) -> None:
    """`status()` narrows `docker ps` to this install's three containers."""
    fake_runner.ps_lines = "t-db\nt-world\nunrelated\n"
    status = Controller(SPEC, SERVER_DIR).status()
    assert status == InstallStatus(db=True, auth=False, world=True)
    assert status.any_running is True
    assert status.all_running is False


def test_status_all_running_when_every_container_is_up(fake_runner: _FakeRunner) -> None:
    fake_runner.ps_lines = "t-auth\nt-db\nt-world\n"
    status = Controller(SPEC, SERVER_DIR).status()
    assert status.all_running is True


def test_status_nothing_running(fake_runner: _FakeRunner) -> None:
    status = Controller(SPEC, SERVER_DIR).status()
    assert status == InstallStatus(db=False, auth=False, world=False)
    assert status.any_running is False


def test_wait_helpers_are_bound_to_the_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_db_healthy()`/`wait_ready()` forward the spec's containers + kwargs."""
    seen: dict[str, object] = {}

    def fake_wait_db_healthy(spec: docker.ContainerSpec, **kwargs: float) -> bool:
        seen["db"] = (spec, kwargs)
        return True

    def fake_wait_ready(
        spec: docker.ContainerSpec, realm_host: str, realm_port: int, **kwargs: float
    ) -> bool:
        seen["ready"] = (spec, realm_host, realm_port, kwargs)
        return False

    monkeypatch.setattr(docker, "wait_db_healthy_for", fake_wait_db_healthy)
    monkeypatch.setattr(docker, "wait_ready_for", fake_wait_ready)

    ctl = Controller(SPEC, SERVER_DIR)
    assert ctl.wait_db_healthy(timeout=1.0, interval=0.5) is True
    assert ctl.wait_ready("127.0.0.1", 8085, timeout=2.0) is False
    assert seen["db"] == (SPEC, {"timeout": 1.0, "interval": 0.5})
    assert seen["ready"] == (SPEC, "127.0.0.1", 8085, {"timeout": 2.0})


def test_wotlk_controller_inherits_everything_with_its_own_spec(
    fake_runner: _FakeRunner,
) -> None:
    """Roadmap 1.4 DoD: the WotLK subclass reimplements nothing, only supplies SPEC."""
    ctl = WotlkController(SERVER_DIR)
    assert isinstance(ctl, Controller)
    assert ctl.spec is docker_ctl.SPEC
    assert ctl.server_dir == SERVER_DIR

    # Nothing is overridden — every lifecycle method is the base class's.
    for name in ("start", "stop", "status", "port_conflicts", "wait_db_healthy", "wait_ready"):
        assert getattr(WotlkController, name) is getattr(Controller, name)

    fake_runner.ps_lines = "ac-database\nac-authserver\nac-worldserver\n"
    assert ctl.status().all_running is True
    ctl.start()  # own containers bind the ports → allowed
    # Only the three long-running services are named, so compose cannot select
    # ac-db-import — which dml-start.sh warns "was killing the database".
    assert ["docker", "compose", "up", "-d"] not in fake_runner.calls
    assert [
        "docker",
        "compose",
        "up",
        "-d",
        "--no-deps",
        "ac-database",
        "ac-authserver",
        "ac-worldserver",
    ] in fake_runner.calls


def test_status_raises_rather_than_reporting_a_dead_daemon_as_a_stopped_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The headline reason `status()` went back to `docker ps`.

    A label-filtered version fell back to `_status_safe() or []` when no project
    was pinned — which is every install adopted through "Use existing…" — so a
    daemon that would not answer read as "everything is down", and the tab then
    DISABLED Stop while the server was serving. Raising is what puts "Docker not
    reachable" on screen instead (review, 2026-08-22).
    """
    monkeypatch.setattr(
        runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(
            1, "", "Cannot connect to the Docker daemon"
        ),
    )
    with pytest.raises(docker.DockerCommandError):
        Controller(SPEC, SERVER_DIR).status()


def test_status_reports_a_neighbours_containers_and_that_is_deliberate(
    fake_runner: _FakeRunner,
) -> None:
    """The accepted limit of going by name, pinned so it is a decision and not a surprise.

    AzerothCore's container names are global, so a second install of the game
    wears these exact names and this tab shows them as up. That is safe because
    nothing ACTS on it: `stop_staged()` checks the compose project label and
    refuses, and the refusal is shown on the Server tab. The alternative —
    filtering status by label too — hid a live server behind "down" and disabled
    the only button that explains why (review, 2026-08-22).
    """
    fake_runner.ps_lines = "t-db\nt-auth\nt-world\n"
    fake_runner.project = "somebody-elses-install"  # the labels disagree with us
    status = Controller(SPEC, SERVER_DIR).status()
    assert status.all_running is True, "status is a view of names, not a claim of ownership"


def test_port_conflicts_excuses_our_own_names_including_a_neighbours(
    fake_runner: _FakeRunner,
) -> None:
    """The other half of that trade, stated rather than discovered.

    Subtracting the three names excuses a second install's containers too, so
    this guard cannot fire for the one collision it exists for. The label-based
    version that could needed a second `docker ps`, and a blip on either made
    Start refuse while naming the user's OWN containers.
    """
    fake_runner.ps_lines = "t-world\t0.0.0.0:2222->2222/tcp\nstranger\t0.0.0.0:1111->1111/tcp\n"
    fake_runner.project = "somebody-elses-install"
    assert Controller(SPEC, SERVER_DIR).port_conflicts() == ["stranger"]
