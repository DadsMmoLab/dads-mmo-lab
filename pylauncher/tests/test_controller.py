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


@pytest.fixture(autouse=True)
def _server_dirs_are_treated_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """`SERVER_DIR` is symbolic, never created; see `test_docker.py`'s twin fixture."""
    monkeypatch.setattr(docker, "_cwd_is_missing", lambda cwd: False)


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
        if cmd[:3] == ["docker", "compose", "down"]:
            self.ps_lines = ""  # and compose really removed them
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


def test_wait_helpers_are_bound_to_the_spec(
    monkeypatch: pytest.MonkeyPatch, a_world_container_that_answers: None
) -> None:
    """`wait_db_healthy()`/`wait_ready()` forward the spec's containers + kwargs."""
    seen: dict[str, object] = {}

    def fake_wait_db_healthy(spec: docker.ContainerSpec, **kwargs: float) -> bool:
        seen["db"] = (spec, kwargs)
        return True

    def fake_wait_ready(
        spec: docker.ContainerSpec, ready: docker.ReadySpec, **kwargs: object
    ) -> bool:
        seen["ready"] = (spec, ready, kwargs)
        return False

    monkeypatch.setattr(docker, "wait_db_healthy_for", fake_wait_db_healthy)
    monkeypatch.setattr(docker, "wait_ready_for", fake_wait_ready)

    ctl = Controller(SPEC, SERVER_DIR)
    assert ctl.wait_db_healthy(timeout=1.0, interval=0.5) is True
    assert ctl.wait_ready("127.0.0.1", 8085, timeout=2.0) is False
    assert seen["db"] == (SPEC, {"wsl_distro": None, "timeout": 1.0, "interval": 0.5})
    assert seen["ready"] == (
        SPEC,
        docker.azerothcore_ready("127.0.0.1", 8085, timeout=2.0),
        {"wsl_distro": None},
    )


def test_wait_helpers_forward_the_distro_a_wsl_install_lives_in(
    monkeypatch: pytest.MonkeyPatch, a_world_container_that_answers: None
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
        spec: docker.ContainerSpec, ready: docker.ReadySpec, **kwargs: object
    ) -> bool:
        seen["ready"] = kwargs
        # The timeout rides in the ReadySpec now, so assert it still arrives.
        seen["ready_timeout"] = ready.timeout
        return True

    monkeypatch.setattr(docker, "wait_db_healthy_for", fake_wait_db_healthy)
    monkeypatch.setattr(docker, "wait_ready_for", fake_wait_ready)

    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    ctl.wait_db_healthy(timeout=1.0)
    ctl.wait_ready("127.0.0.1", 8085, timeout=1.0)
    assert seen["db"] == {"wsl_distro": "dml-arch", "timeout": 1.0}
    assert seen["ready"] == {"wsl_distro": "dml-arch"}
    assert seen["ready_timeout"] == 1.0


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


def _wsl_exe(
    monkeypatch: pytest.MonkeyPatch,
    *,
    listed: tuple[str, ...] | Exception | int,
    running: tuple[str, ...] | Exception | int,
) -> None:
    """`wsl.exe -l -q [--running]` as the machine answers it: names, an exit code, or a raise."""
    wsl = controller_module.wsl
    monkeypatch.setattr(wsl.platform, "_which", lambda _program: "wsl.exe")

    def run(cmd: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        answer = running if "--running" in cmd else listed
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return subprocess.CompletedProcess(cmd, answer, b"", b"")
        out = "".join(f"{name}\r\n" for name in answer).encode("utf-16le")
        return subprocess.CompletedProcess(cmd, 0, out, b"")

    monkeypatch.setattr(wsl.subprocess, "run", run)


def _stop_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []
    # The T132 hold's release follows a stop that took something down; these
    # tests are about the stop, so it is answered here rather than refused by
    # the suite's guard against touching a real distro.
    monkeypatch.setattr(controller_module.wsl, "release", lambda distro, *keys: None)
    monkeypatch.setattr(
        docker, "stop_staged", lambda *a, **kw: asked.append("stop") or True  # type: ignore[func-returns-value]
    )
    return asked


def test_stopping_does_not_start_a_stopped_distro(monkeypatch: pytest.MonkeyPatch) -> None:
    """T95: a removal always stops first, and `wsl -d` STARTS a distro.

    Nothing runs in a distro that is down, so "nothing was stopped" is true
    without asking; asking would boot the distro to learn it. Only when both
    listings ANSWERED: the full one names the distro, `--running` does not.
    """
    asked = _stop_recorder(monkeypatch)
    _wsl_exe(monkeypatch, listed=("dml-arch", "docker-desktop"), running=("docker-desktop",))
    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    assert ctl.stop() is False
    assert asked == [], "the stop shelled into a stopped distro and started it"

    _wsl_exe(monkeypatch, listed=("dml-arch",), running=("dml-arch",))
    assert ctl.stop() is True
    assert asked == ["stop"]


def test_a_stop_whose_distro_listing_failed_still_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail CLOSED (T95 re-review): an unanswered listing is not "the distro is down".

    `_wsl_list()` answers `()` for no wsl.exe, a raise (the 60 s timeout among
    them) and a non-zero exit alike, and read as "not running" that made Stop
    say nothing ran, a removal forget a running server, and Restart skip its
    stop. The `--running` half failing alone is the case a check on
    `distro_states()` still misses: the full list names the distro, and the
    empty running set reads as "stopped".
    """
    timeout = subprocess.TimeoutExpired(["wsl.exe"], 60)
    failures: tuple[tuple[object, object], ...] = (
        ((), ()),  # the listing named nothing: no proof either way
        (timeout, timeout),
        (1, 1),
        (("dml-arch",), timeout),  # only `--running` failed
        (("dml-arch",), 4294967295),
    )
    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    for listed, running in failures:
        asked = _stop_recorder(monkeypatch)
        _wsl_exe(monkeypatch, listed=listed, running=running)  # type: ignore[arg-type]
        assert ctl.stop() is True, (listed, running)
        assert asked == ["stop"], f"a failed listing skipped the stop: {(listed, running)}"

    asked = _stop_recorder(monkeypatch)
    monkeypatch.setattr(controller_module.wsl.platform, "_which", lambda _program: None)
    assert ctl.stop() is True
    assert asked == ["stop"], "no wsl.exe was read as a stopped distro"


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


def test_wotlk_controller_stop_uses_stop_grace_seconds_and_drains_properly(
    fake_runner: _FakeRunner,
) -> None:
    """Stopping a WotLK server passes STOP_GRACE_SECONDS so the worldserver can flush."""
    fake_runner.ps_lines = "ac-database\nac-authserver\nac-worldserver\n"
    ctl = WotlkController(SERVER_DIR)
    ctl.stop()
    assert ["docker", "compose", "stop", "-t", str(docker.STOP_GRACE_SECONDS)] in fake_runner.calls


def test_wotlk_controller_port_conflicts_flags_foreign_bindings_on_3724_and_8085(
    fake_runner: _FakeRunner,
) -> None:
    """Port collisions on 3724 or 8085 reject WotLK server start with PortConflictError."""
    fake_runner.ps_lines = (
        "foreign-auth\t0.0.0.0:3724->3724/tcp\n" "foreign-world\t0.0.0.0:8085->8085/tcp\n"
    )
    ctl = WotlkController(SERVER_DIR)
    assert set(ctl.port_conflicts()) == {"foreign-auth", "foreign-world"}

    with pytest.raises(PortConflictError) as excinfo:
        ctl.start()

    assert set(excinfo.value.containers) == {"foreign-auth", "foreign-world"}
    assert excinfo.value.ports == (3724, 8085)
    assert "3724" in str(excinfo.value)
    assert "8085" in str(excinfo.value)


# -- the pre-stop log snapshot (Phase 8.1a) --------------------------------
#
# The hook is a plain callable, injected. The controller must not know where a
# snapshot goes, what it is called or that `logsnap` exists — it knows only that
# something wants to run before the container it is about to stop is gone.


def test_stop_saves_the_log_before_it_stops_anything(fake_runner: _FakeRunner) -> None:
    """Before, not after: `compose stop` is what takes the log away."""
    fake_runner.ps_lines = "t-db\nt-auth\nt-world\n"
    stopped_when_called: list[bool] = []

    def hook() -> None:
        stopped_when_called.append(
            any(c[:3] == ["docker", "compose", "stop"] for c in fake_runner.calls)
        )

    Controller(SPEC, SERVER_DIR, pre_stop=hook).stop()

    assert stopped_when_called == [False], "the snapshot ran after the stop, or not at all"


def test_a_snapshot_that_raises_does_not_prevent_the_stop(fake_runner: _FakeRunner) -> None:
    """Evidence collection may fail; the action the user asked for still happens."""
    fake_runner.ps_lines = "t-db\nt-auth\nt-world\n"

    def hook() -> None:
        raise RuntimeError("the log driver is wedged")

    assert Controller(SPEC, SERVER_DIR, pre_stop=hook).stop() is True
    assert any(c[:3] == ["docker", "compose", "stop"] for c in fake_runner.calls)


def test_remove_saves_the_log_too_because_it_destroys_the_container(
    fake_runner: _FakeRunner,
) -> None:
    """`stop()` only stops the container; `remove()` takes the log with it."""
    fake_runner.ps_lines = "t-db\nt-auth\nt-world\n"
    calls: list[str] = []

    Controller(SPEC, SERVER_DIR, pre_stop=lambda: calls.append("snap")).remove()

    assert calls == ["snap"]


def test_a_controller_with_no_hook_stops_exactly_as_it_did_before(
    fake_runner: _FakeRunner,
) -> None:
    """Every existing caller constructs without one and must see today's behaviour."""
    fake_runner.ps_lines = "t-db\nt-auth\nt-world\n"
    Controller(SPEC, SERVER_DIR).stop()
    with_hook = list(fake_runner.calls)

    fake_runner.calls.clear()
    fake_runner.ps_lines = "t-db\nt-auth\nt-world\n"
    Controller(SPEC, SERVER_DIR, pre_stop=None).stop()

    assert fake_runner.calls == with_hook


# -- T132: a WSL distro lives only while a wsl.exe session is attached --------------
#
# Measured on yulon-win11 (WSL 2.7.12, 2026-09-26): a distro stops 15-25 s after
# the last wsl.exe exits, with systemd, dockerd and running containers inside it.
# Start is one short `wsl -d ... docker compose up -d`, so a WSL server lived
# only while something kept calling into the distro -- in practice the Server
# tab's five-second poll -- and was killed hard when the app closed.


class _HoldRecorder:
    """Stands in for `wsl.hold`/`wsl.release`, recording the order of events."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
        self.events = events
        monkeypatch.setattr(controller_module.wsl, "hold", self.hold)
        monkeypatch.setattr(controller_module.wsl, "release", self.release)

    def hold(self, distro: str, key: str) -> controller_module.wsl.Hold:
        self.events.append(f"hold {distro} {key}")
        return controller_module.wsl.Hold(held=True)

    def release(self, distro: str, *keys: str) -> None:
        self.events.append(f"release {distro} {' '.join(keys)}")


def _no_conflicts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "port_conflicts_for", lambda spec, **kw: [])


def test_starting_a_wsl_server_holds_its_distro_open_after_the_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start ends with a held session, and only AFTER the containers were asked up."""
    events: list[str] = []
    _HoldRecorder(monkeypatch, events)
    _no_conflicts(monkeypatch)
    monkeypatch.setattr(
        docker,
        "start_staged",
        lambda spec, sd, **kw: events.append(f"start {kw.get('wsl_distro')}"),
    )
    Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch").start()
    assert events == ["start dml-arch", f"hold dml-arch {SPEC.world}"]


def test_a_start_that_failed_holds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A held session for a server that never came up would pin the distro for nothing."""
    events: list[str] = []
    _HoldRecorder(monkeypatch, events)
    _no_conflicts(monkeypatch)

    def refuse(spec: docker.ContainerSpec, sd: Path, **kw: object) -> None:
        raise docker.DockerCommandError("compose up failed")

    monkeypatch.setattr(docker, "start_staged", refuse)
    with pytest.raises(docker.DockerCommandError):
        Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch").start()
    assert events == []


def test_a_local_server_never_holds_or_releases_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[str] = []
    _HoldRecorder(monkeypatch, events)
    _no_conflicts(monkeypatch)
    monkeypatch.setattr(docker, "start_staged", lambda spec, sd, **kw: None)
    monkeypatch.setattr(docker, "stop_staged", lambda spec, sd, **kw: True)
    monkeypatch.setattr(docker, "remove_staged", lambda spec, sd, **kw: True)
    ctl = Controller(SPEC, SERVER_DIR)
    ctl.start()
    ctl.stop()
    ctl.remove()
    assert events == []


@pytest.mark.parametrize("action", ["stop", "remove"])
def test_stopping_a_wsl_server_releases_the_hold_after_the_containers_are_down(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    events: list[str] = []
    _HoldRecorder(monkeypatch, events)
    monkeypatch.setattr(
        docker, f"{action}_staged", lambda spec, sd, **kw: events.append(action) or True
    )
    assert getattr(Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch"), action)() is True
    assert events == [action, f"release dml-arch {SPEC.world}"]


@pytest.mark.parametrize("action", ["stop", "remove"])
def test_a_stop_that_found_nothing_of_ours_leaves_the_hold_alone(
    monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """Nothing of THIS install was up, so a hold under these names is not ours to end.

    Two installs of one game share container names; the one that IS running
    owns the hold, and pressing Stop on the other tab must not let its distro go.
    """
    events: list[str] = []
    _HoldRecorder(monkeypatch, events)
    monkeypatch.setattr(docker, f"{action}_staged", lambda spec, sd, **kw: False)
    assert getattr(Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch"), action)() is False
    assert events == []


# -- T132 review: a world running without a Start, and the conflict path ------------


class _Distro:
    """One distro's holds as the scripts keep them: a set of keys, and whether it is up."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, *, running: bool = True) -> None:
        self.running = running
        self.holds: set[str] = set()
        self.spawned = 0
        self.alive = True
        wsl = controller_module.wsl
        monkeypatch.setattr(wsl, "is_running", lambda distro: self.running)
        monkeypatch.setattr(wsl, "hold", self.hold)
        monkeypatch.setattr(wsl, "release", self.release)

    def hold(self, distro: str, key: str) -> controller_module.wsl.Hold:
        assert self.running, "a hold was asked of a stopped distro"
        self.spawned += 1
        self.holds.add(key)
        distro_ = self

        class _Proc:
            def poll(self) -> int | None:
                return None if distro_.alive else 1

        return controller_module.wsl.Hold(held=True, proc=_Proc())

    def release(self, distro: str, *keys: str) -> None:
        self.holds -= set(keys)


def _docker_ps(monkeypatch: pytest.MonkeyPatch, names: list[str]) -> list[list[str]]:
    asked: list[list[str]] = []

    def status(**kw: object) -> list[str]:
        asked.append(["ps"])
        return list(names)

    monkeypatch.setattr(docker, "status", status)
    return asked


def test_a_world_already_running_when_the_app_opens_is_held_without_a_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Up before Yu'lon opened, nobody pressed Start: the poll that sees it holds it."""
    distro = _Distro(monkeypatch)
    _docker_ps(monkeypatch, [SPEC.db, SPEC.auth, SPEC.world])
    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    assert ctl.status().world
    assert distro.holds == {SPEC.world}
    for _ in range(3):
        ctl.status()
    assert distro.spawned == 1, "the poll held again while the first hold was alive"


def test_a_hold_that_went_away_is_made_again_by_the_next_poll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    distro = _Distro(monkeypatch)
    _docker_ps(monkeypatch, [SPEC.world])
    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    ctl.status()
    distro.alive = False
    ctl.status()
    assert distro.spawned == 2


def test_a_stopped_distro_is_never_held_or_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    distro = _Distro(monkeypatch, running=False)
    asked = _docker_ps(monkeypatch, [SPEC.world])
    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    assert not ctl.status().any_running
    assert asked == [] and distro.spawned == 0


def test_a_world_seen_down_is_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    distro = _Distro(monkeypatch)
    _docker_ps(monkeypatch, [SPEC.db])
    Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch").status()
    assert distro.spawned == 0


def test_a_hold_that_never_took_is_not_retried_every_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """No flock in the distro: one failing spawn, not one per five-second poll."""
    spawned: list[str] = []
    monkeypatch.setattr(controller_module.wsl, "is_running", lambda distro: True)
    monkeypatch.setattr(
        controller_module.wsl,
        "hold",
        lambda distro, key: spawned.append(key) or controller_module.wsl.Hold(held=False),
    )
    names = [SPEC.world]
    _docker_ps(monkeypatch, names)
    ctl = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    ctl.status()
    ctl.status()
    assert spawned == [SPEC.world]
    names.clear()
    ctl.status()
    names.append(SPEC.world)
    ctl.status()
    assert spawned == [SPEC.world, SPEC.world], "a world seen down and up again is asked again"


def test_a_local_install_is_never_held_by_its_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    distro = _Distro(monkeypatch)
    _docker_ps(monkeypatch, [SPEC.world])
    Controller(SPEC, SERVER_DIR).status()
    assert distro.spawned == 0


def test_switching_servers_through_the_conflict_path_leaves_no_hold_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Start A, start B over it (A stopped by the conflict path), stop B: nothing held.

    `stop_conflicting()` stops the OTHER install's containers by name and
    never went through that install's `stop()`, so its hold stayed and pinned
    the distro for good (review, Codex).
    """
    distro = _Distro(monkeypatch)
    a = docker.ContainerSpec(db="a-db", auth="a-auth", world="a-world", ports=(1111, 2222))
    running: set[str] = set()
    monkeypatch.setattr(
        docker,
        "start_staged",
        lambda spec, sd, **kw: running.update({spec.db, spec.auth, spec.world}),
    )
    monkeypatch.setattr(
        docker,
        "port_conflicts_for",
        lambda spec, **kw: sorted(running - {spec.db, spec.auth, spec.world}),
    )
    monkeypatch.setattr(docker, "container_project", lambda name, **kw: "a-project")
    monkeypatch.setattr(
        docker, "project_containers", lambda project, **kw: ["a-db", "a-auth", "a-world"]
    )
    monkeypatch.setattr(
        docker, "stop_containers", lambda names, **kw: running.difference_update(names)
    )

    def stop_b(spec: docker.ContainerSpec, sd: Path, **kw: object) -> bool:
        had = bool(running & {spec.db, spec.auth, spec.world})
        running.difference_update({spec.db, spec.auth, spec.world})
        return had

    monkeypatch.setattr(docker, "stop_staged", stop_b)

    Controller(a, Path("/tmp/a"), wsl_distro="dml-arch").start()
    assert distro.holds == {"a-world"}
    b = Controller(SPEC, SERVER_DIR, wsl_distro="dml-arch")
    assert b.stop_conflicting_and_start() == ["a-db", "a-auth", "a-world"]
    assert distro.holds == {SPEC.world}
    assert b.stop() is True
    assert distro.holds == set()
