"""Tests for the base `Controller` abstraction (roadmap Phase 1.4).

All subprocess calls are mocked at the `yulon.runner.run` boundary (the same
seam `tests/test_docker.py` uses), so nothing here needs a real Docker daemon.
The point of these tests is the *controller* contract: a per-game subclass
inherits start/stop/status/polling with zero reimplementation, and `start()`
is guarded by the shared single-instance/port-conflict check (README §12).
"""

from __future__ import annotations

import ast
import subprocess
from pathlib import Path

import pytest

from yulon import controller as controller_module
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
    assert seen["db"] == (SPEC, {"wsl_distro": None, "timeout": 1.0, "interval": 0.5})
    assert seen["ready"] == (SPEC, "127.0.0.1", 8085, {"wsl_distro": None, "timeout": 2.0})


def test_wait_helpers_forward_the_distro_a_wsl_install_lives_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Polling has to ask the right daemon, and asking the wrong one does not fail.

    A server inside a WSL distro answers only to that distro's docker. Poll
    Docker Desktop instead and it reports no containers - so `wait_ready()`
    would sit out its full timeout on a server that came up seconds in, and
    `wait_db_healthy()` would call a healthy database dead. Nothing raises,
    which is why this is asserted rather than assumed.
    """
    seen: dict[str, object] = {}

    def fake_wait_db_healthy(spec: docker.ContainerSpec, **kwargs: object) -> bool:
        seen["db"] = kwargs
        return True

    def fake_wait_ready(
        spec: docker.ContainerSpec, realm_host: str, realm_port: int, **kwargs: object
    ) -> bool:
        seen["ready"] = kwargs
        return True

    monkeypatch.setattr(docker, "wait_db_healthy_for", fake_wait_db_healthy)
    monkeypatch.setattr(docker, "wait_ready_for", fake_wait_ready)

    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    ctl.wait_db_healthy(timeout=1.0)
    ctl.wait_ready("127.0.0.1", 8085, timeout=1.0)
    assert seen["db"] == {"wsl_distro": "dml-arch", "timeout": 1.0}
    assert seen["ready"] == {"wsl_distro": "dml-arch", "timeout": 1.0}


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


def test_repair_import_hands_the_output_sink_through_to_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sink is the whole of the progress feature, so dropping it is silent.

    Nothing else would notice: the import still runs, still refuses on the same
    things, still reports the same result — and the window shows one frozen
    sentence for the 10-30 minutes it takes, which is what this was built to
    end.
    """
    seen: list[tuple[object, ...]] = []

    def fake_repair_import(
        spec: docker.ContainerSpec,
        server_dir: Path,
        probe: docker.ImportProbe,
        *,
        reset: docker.ResetUnfinished | None = None,
        output: docker.OutputSink | None = None,
        db_timeout: float = 1.0,
        **_kw: object,
    ) -> bool:
        seen.append((spec, server_dir, output))
        return True

    monkeypatch.setattr(docker, "repair_import", fake_repair_import)
    lines: list[str] = []
    ctl = Controller(SPEC, SERVER_DIR, import_probe=lambda: docker.ImportState("absent"))
    assert ctl.repair_import(lines.append) is True
    # And a caller that wants nothing shown still gets an import.
    assert ctl.repair_import() is True
    assert seen == [(SPEC, SERVER_DIR, lines.append), (SPEC, SERVER_DIR, None)]


# Controller methods that deliberately do not name a daemon, with the reason.
_NOT_THIS_INSTALLS_DAEMON: dict[str, str] = {}


def _controller_docker_calls() -> dict[str, list[str]]:
    """Every `docker.<fn>(...)` call inside `Controller`, by method, that could
    name a daemon and does not.

    The companion to docker.py's completeness test, and the gap it left. That
    one proves every docker function CAN be told which daemon to ask; this one
    proves the caller actually tells it. Both are needed, because a controller
    that holds a distro and forgets to pass it produces the exact silent failure
    the distro exists to prevent - Docker Desktop answers "no containers" and a
    running server reads as stopped.
    """
    source = Path(controller_module.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    takes_distro = {
        node.name
        for node in ast.walk(ast.parse(Path(docker.__file__).read_text(encoding="utf-8")))
        if isinstance(node, ast.FunctionDef)
        and "wsl_distro"
        in [a.arg for a in (*node.args.args, *node.args.kwonlyargs, *node.args.posonlyargs)]
    }

    missing: dict[str, list[str]] = {}
    for klass in ast.walk(tree):
        if not isinstance(klass, ast.ClassDef) or klass.name != "Controller":
            continue
        for method in klass.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for call in ast.walk(method):
                if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                    continue
                value = call.func.value
                if not isinstance(value, ast.Name) or value.id != "docker":
                    continue
                if call.func.attr not in takes_distro:
                    continue
                if any(k.arg == "wsl_distro" for k in call.keywords):
                    continue
                missing.setdefault(method.name, []).append(call.func.attr)
    return missing


def test_every_controller_call_says_which_daemon_it_means() -> None:
    """The gap the docker.py completeness test cannot see.

    That test proves each docker function ACCEPTS `wsl_distro`. It says nothing
    about whether a caller passes one - and the first version of this feature
    threaded all 33 functions and then forwarded the distro from exactly two of
    the Controller's eight call sites. Start, Stop, Remove, Status,
    port_conflicts and repair_import all addressed the local daemon, on a
    machine that in the reported case has no local daemon at all.

    Nothing failed loudly: `docker.status()` against Docker Desktop answers "no
    containers", so a running server reads as stopped. That is why this is a
    test and not a review checklist.
    """
    missing = {
        method: calls
        for method, calls in _controller_docker_calls().items()
        if method not in _NOT_THIS_INSTALLS_DAEMON
    }
    assert not missing, (
        "these Controller methods call docker without saying which daemon, so a "
        f"WSL-resident server would be asked of the wrong one: {missing}\n"
        "Pass `wsl_distro=self.wsl_distro`, or name the method in "
        "_NOT_THIS_INSTALLS_DAEMON with the reason it must not."
    )


def test_the_caller_scan_would_notice_a_forgotten_call() -> None:
    """The guard's own guard: prove it reads the class rather than an empty set."""
    source = """
import ast
class Controller:
    def good(self):
        return docker.status(wsl_distro=self.wsl_distro)
    def bad(self):
        return docker.status()
"""
    tree = ast.parse(source)
    forgot = []
    for klass in ast.walk(tree):
        if not isinstance(klass, ast.ClassDef):
            continue
        for method in klass.body:
            if not isinstance(method, ast.FunctionDef):
                continue
            for call in ast.walk(method):
                if (
                    isinstance(call, ast.Call)
                    and isinstance(call.func, ast.Attribute)
                    and isinstance(call.func.value, ast.Name)
                    and call.func.value.id == "docker"
                    and not any(k.arg == "wsl_distro" for k in call.keywords)
                ):
                    forgot.append(method.name)
    assert forgot == ["bad"], f"the scan does not distinguish the two: {forgot}"


def test_polling_status_does_not_start_a_stopped_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Server tab polls every five seconds, and `wsl -d` STARTS a distro.

    So an adopted WSL server would boot its distro simply by opening the app -
    the exact side effect discovery was designed to avoid, reintroduced through
    the back door by polling. Nothing is running when the distro is down, so the
    empty answer is true rather than merely convenient; Start still starts it,
    because that is something the user asked for.
    """
    ran: list[list[str]] = []
    monkeypatch.setattr(
        docker, "status", lambda **kw: ran.append(["docker", "ps"]) or []  # type: ignore[func-returns-value]
    )
    monkeypatch.setattr(controller_module.wsl, "is_running", lambda distro: False)

    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    assert not ctl.status().any_running
    assert ran == [], "the poll shelled into a stopped distro and started it"


def test_polling_status_asks_docker_when_the_distro_is_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """And the guard must not turn a running server into a permanently dead one."""
    monkeypatch.setattr(docker, "status", lambda **kw: [SPEC.db])
    monkeypatch.setattr(controller_module.wsl, "is_running", lambda distro: True)
    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    # `status()` returns an InstallStatus, not a list of names.
    assert ctl.status().any_running


class _ForeignProjectRunner(_FakeRunner):
    """A neighbour install whose stack is bigger than the ports it publishes.

    Its `stranger-auth` holds one of our ports; `stranger-world` holds none of
    them and is still part of the same server. Answering the project-filtered
    `docker ps -a` honestly is the whole point - the base fake returns every
    name it knows, which would make this test pass for the wrong reason.
    """

    theirs = ("stranger-db", "stranger-auth", "stranger-world")

    def __call__(self, cmd, cwd=None, timeout=None):
        if cmd[:3] == ["docker", "ps", "-a"] and any("their-project" in arg for arg in cmd):
            self.calls.append(cmd)
            self.cwds.append(cwd)
            return _completed(0, "".join(name + "\n" for name in self.theirs))
        if cmd[:2] == ["docker", "inspect"] and any(docker.PROJECT_LABEL in arg for arg in cmd):
            self.calls.append(cmd)
            self.cwds.append(cwd)
            return _completed(0, "their-project\n")
        return super().__call__(cmd, cwd, timeout)


def test_stopping_a_conflict_stops_that_whole_server_not_just_the_port_holders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pulling the database out from under a worldserver is not "stopping the server".

    Measured on yulon-fedora, 2026-08-29. The guard stopped `ac-authserver` and
    `ac-database`, which published the two colliding ports, and left
    `ac-worldserver` running with its database gone: it published only 8085 and
    7878, so it was correctly not a blocker, and `restart: unless-stopped` then
    looped it - RestartCount 18 and climbing.

    So the unit is the compose PROJECT. This asserts the container that was never
    a blocker gets stopped too, which is exactly what the first version did not do.
    """
    fake = _ForeignProjectRunner(
        "stranger-auth\t0.0.0.0:1111->1111/tcp\n" "stranger-world\t0.0.0.0:9999->9999/tcp\n"
    )
    monkeypatch.setattr(runner, "run", fake)

    stopped = Controller(SPEC, SERVER_DIR).stop_conflicting()

    assert "stranger-auth" in stopped, "the blocker itself was not stopped"
    assert (
        "stranger-world" in stopped
    ), "the rest of that server was left running against a stack that is gone"
    assert "stranger-db" in stopped, "a project member that publishes nothing was skipped"

    issued = [cmd for cmd in fake.calls if cmd[:2] == ["docker", "stop"]]
    stopped_names = {cmd[-1] for cmd in issued}
    assert stopped_names == set(_ForeignProjectRunner.theirs), "the stops issued were " + str(
        sorted(stopped_names)
    )


def test_a_blocker_with_no_compose_project_is_stopped_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Something started outside compose has no project to widen to, and is stopped alone.

    The widening must not become a licence to stop unrelated things: a container
    with no project label is exactly one container.
    """

    class _NoProject(_FakeRunner):
        def __call__(self, cmd, cwd=None, timeout=None):
            if cmd[:2] == ["docker", "inspect"] and any(docker.PROJECT_LABEL in arg for arg in cmd):
                self.calls.append(cmd)
                self.cwds.append(cwd)
                return _completed(0, "\n")  # no label at all
            return super().__call__(cmd, cwd, timeout)

    fake = _NoProject("rogue-mysql\t0.0.0.0:1111->1111/tcp\n")
    monkeypatch.setattr(runner, "run", fake)

    assert Controller(SPEC, SERVER_DIR).stop_conflicting() == ["rogue-mysql"]
