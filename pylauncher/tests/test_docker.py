"""Tests for the shared Docker lifecycle (`yulon.docker` and WotLK wrapper).

All subprocess calls are mocked at the `yulon.runner.run` boundary, so nothing
here requires a real Docker daemon — mirroring roadmap 1.3's "mocked control
flow" intent. The integration suite (Phase 1.5, `tests/fixture.md`) is where a
real AzerothCore compose project gets exercised.
"""

from __future__ import annotations

import ast
import re
import subprocess
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import pytest

from yulon import docker, runner
from yulon.controller_wow_wotlk import docker_ctl

SPEC = docker_ctl.SPEC
_GRACE = str(docker.STOP_GRACE_SECONDS)
"""The stop grace as it appears in argv, so an expected command reads like the real one."""


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def test_container_spec_has_expected_wotlk_names_and_ports() -> None:
    """The WotLK spec pins the three AzerothCore containers and shared ports."""
    assert docker_ctl.SPEC.db == "ac-database"
    assert docker_ctl.SPEC.auth == "ac-authserver"
    assert docker_ctl.SPEC.world == "ac-worldserver"
    assert docker_ctl.SPEC.ports == (3724, 8085)


def test_start_runs_compose_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """`start()` shells out to `docker compose up -d` in the server dir."""
    calls: list[list[str]] = []
    cwds: list[Path | None] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        cwds.append(cwd)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    server_dir = Path("/tmp/wow")
    docker.start(server_dir)
    assert calls == [["docker", "compose", "up", "-d"]]
    assert cwds == [server_dir]


def test_start_raises_docker_command_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero `docker` exit surfaces as `DockerCommandError`."""
    monkeypatch.setattr(
        docker.runner, "run", lambda cmd, cwd=None, timeout=None: _completed(1, "", "boom")
    )
    with pytest.raises(docker.DockerCommandError):
        docker.start(Path("/tmp/wow"))


def test_status_returns_running_container_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """`status()` parses `docker ps --format '{{.Names}}'` into a name list."""
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(0, "ac-database\nac-worldserver\n", ""),
    )
    assert docker.status() == ["ac-database", "ac-worldserver"]


def test_status_gives_docker_ps_a_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    """`docker ps` must not be allowed to hang the five-second status poll forever.

    `refresh_status()` sets `_status_pending` and clears it only from a
    callback, so a call that never returns is not a slow poll - it is a
    permanently wedged Server tab, reading "status: unknown" until the app is
    restarted, with every later poll returning early at the guard. Measured on
    yulon-win11 (2026-08-28): `docker ps` hung 8+ minutes under memory
    pressure, and 125 seconds on an earlier run.

    Asserted on the timeout that reaches the run seam rather than on the
    constant, because the bug was never the value - it was that no value was
    passed at all.
    """
    seen: list[float | None] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(timeout)
        return _completed(0, "ac-worldserver\n", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)

    assert docker.status() == ["ac-worldserver"]
    assert seen and seen[0] is not None, "docker ps was run with no deadline at all"
    assert 0 < seen[0] <= 120, f"deadline of {seen[0]}s is not a bound worth having"


def test_a_status_timeout_surfaces_as_an_error_the_poll_can_recover_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A timed-out `docker ps` must raise, not return an empty list.

    `runner.run()` reports a timeout as a non-zero `CompletedProcess`, the same
    shape as a missing CLI. If `status()` swallowed that it would answer "no
    containers are running", which reads as a stopped server rather than an
    unreachable daemon - and `_status_failed()`, the callback that clears
    `_status_pending`, would never fire.
    """
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(1, "", "timed out after 30.0s"),
    )
    with pytest.raises(docker.DockerCommandError):
        docker.status()


def test_health_returns_status_or_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """`health()` returns the inspect status, or `unknown` on failure/empty."""
    monkeypatch.setattr(
        docker.runner, "run", lambda cmd, cwd=None, timeout=None: _completed(0, "healthy", "")
    )
    assert docker.health("ac-database") == "healthy"

    monkeypatch.setattr(
        docker.runner, "run", lambda cmd, cwd=None, timeout=None: _completed(1, "", "")
    )
    assert docker.health("missing") == "unknown"


def test_port_conflicts_detects_binding_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container publishing 8085 shows up in the conflict list."""
    # docker ps -format "<name>\t<ports>" with a host-side publish for 8085.
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(
            0, "ac-worldserver\t0.0.0.0:8085->8085/tcp\nac-database\t3306/tcp\n", ""
        ),
    )
    assert docker.port_conflicts((3724, 8085)) == ["ac-worldserver"]


def test_port_conflicts_returns_none_when_no_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No container publishing the watched ports yields an empty list."""
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(0, "ac-database\t3306/tcp\n", ""),
    )
    assert docker.port_conflicts((3724, 8085)) == []


def test_wait_db_healthy_returns_true_once_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_db_healthy()` returns True as soon as health() reports healthy."""
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        docker.runner, "run", lambda cmd, cwd=None, timeout=None: _completed(0, "healthy", "")
    )
    assert docker.wait_db_healthy("ac-database", timeout=10, interval=0.01) is True


def test_wait_db_healthy_times_out_if_never_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_db_healthy()` returns False once the deadline passes."""
    fake_time = [0.0]
    monkeypatch.setattr(docker.time, "monotonic", lambda: fake_time[0])
    monkeypatch.setattr(
        docker.time, "sleep", lambda seconds: fake_time.__setitem__(0, fake_time[0] + seconds)
    )
    monkeypatch.setattr(
        docker.runner, "run", lambda cmd, cwd=None, timeout=None: _completed(0, "starting", "")
    )
    assert docker.wait_db_healthy("ac-database", timeout=5, interval=1) is False


def test_wait_db_healthy_rejects_non_positive_interval() -> None:
    """A zero/negative interval is rejected rather than busy-looping."""
    with pytest.raises(ValueError):
        docker.wait_db_healthy("ac-database", interval=0)
    with pytest.raises(ValueError):
        docker.wait_db_healthy("ac-database", interval=-1)


def test_wait_ready_returns_true_once_markers_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_ready()` returns True once both containers are up with ready markers."""
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "ps"]:
            return _completed(0, "ac-authserver\nac-worldserver\n", "")
        if cmd[:2] == ["docker", "inspect"] and "{{.State.Status}}" in cmd[-1]:
            return _completed(0, "running" + chr(9) + "2026-01-01T00:00:00Z" + chr(10), "")
        if cmd[:2] == ["docker", "logs"] and cmd[-1] == "ac-authserver":
            return _completed(0, "listening on 127.0.0.1:3724", "")
        if cmd[:2] == ["docker", "logs"] and cmd[-1] == "ac-worldserver":
            return _completed(0, "World initialized... ready...", "")
        return _completed(0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert (
        docker.wait_ready(
            "ac-authserver",
            "ac-worldserver",
            docker.azerothcore_ready("127.0.0.1", 3724, timeout=10, interval=0.01),
        )
        is True
    )


def test_wait_ready_tolerates_transient_docker_ps_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single failing `docker ps` during polling must not abort the wait.

    Regression test: `wait_ready()` previously called `status()` directly,
    which raises `DockerCommandError` on any non-zero `docker ps` exit —
    aborting the entire wait instead of retrying. It now uses `_status_safe()`.
    """
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)
    calls = {"ps": 0}

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "ps"]:
            calls["ps"] += 1
            if calls["ps"] == 1:
                return _completed(1, "", "the docker daemon is restarting")
            return _completed(0, "ac-authserver\nac-worldserver\n", "")
        if cmd[:2] == ["docker", "inspect"] and "{{.State.Status}}" in cmd[-1]:
            return _completed(0, "running" + chr(9) + "2026-01-01T00:00:00Z" + chr(10), "")
        if cmd[:2] == ["docker", "logs"] and cmd[-1] == "ac-authserver":
            return _completed(0, "listening on 127.0.0.1:3724", "")
        if cmd[:2] == ["docker", "logs"] and cmd[-1] == "ac-worldserver":
            return _completed(0, "ready...", "")
        return _completed(0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    # Must not raise DockerCommandError despite the first docker ps failing.
    assert (
        docker.wait_ready(
            "ac-authserver",
            "ac-worldserver",
            docker.azerothcore_ready("127.0.0.1", 3724, timeout=10, interval=0.01),
        )
        is True
    )
    assert calls["ps"] >= 2


def test_wait_ready_rejects_non_positive_interval() -> None:
    """A zero/negative interval is rejected rather than busy-looping."""
    with pytest.raises(ValueError):
        docker.wait_ready("a", "w", docker.ReadySpec(world="ready...", interval=0))


def test_ready_spec_defaults_and_the_azerothcore_builder() -> None:
    """`ReadySpec` is data; `azerothcore_ready()` is the legacy wrapper's two strings.

    The auth marker is `<host>:<port>` escaped, because `.` in a host is a regex
    wildcard and `10.0.0.1:8085` must not match `10x0y0z1:8085`.
    """
    spec = docker.ReadySpec(world="Avg Diff:")
    assert spec.auth is None and spec.fatal is None
    assert spec.timeout == docker._READY_TIMEOUT_SECONDS
    assert spec.interval == docker._POLL_INTERVAL_SECONDS
    assert spec.restart_loop == 4
    ac = docker.azerothcore_ready("127.0.0.1", 8085, timeout=5.0, interval=0.1)
    # Escaped, not the bare literal: `ready...` unescaped matches `ready up`
    # inside `already up-to-date`, which a loading worldserver prints.
    assert ac.world == re.escape(docker.AZEROTHCORE_READY_WORLD) == r"ready\.\.\."
    assert ac.auth == re.escape("127.0.0.1:8085")
    assert ac.timeout == 5.0 and ac.interval == 0.1 and ac.restart_loop == 4
    assert re.search(ac.auth, "Added realm at 127.0.0.1:8085")
    assert not re.search(ac.auth, "Added realm at 127x0x0x1:8085")
    assert docker.azerothcore_ready("127.0.0.1", 8085).timeout == docker._READY_TIMEOUT_SECONDS
    with pytest.raises(TypeError, match="restart_loop"):
        docker.azerothcore_ready("127.0.0.1", 8085, restart_loop=2)


def _ready_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world_log: str,
    auth_log: str = "Added realm at 127.0.0.1:8085\n",
    inspect: Callable[[], str] | None = None,
) -> list[list[str]]:
    """A running auth+world pair whose logs and inspect answer are the test's choice."""
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)
    seen: list[list[str]] = []
    state = inspect if inspect is not None else (lambda: "running\t2026-08-22T01:24:53Z\t0\n")

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout=state())
        if cmd[:2] == ["docker", "logs"]:
            return _completed(stdout=auth_log if cmd[-1] == SPEC.auth else world_log)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    return seen


def test_wait_ready_matches_the_world_marker_as_a_regex(monkeypatch: pytest.MonkeyPatch) -> None:
    """A CMaNGOS-style marker (`Avg Diff:`) and an anchored one both work; no literal search.

    The log line is what a worldserver really prints, not a string built from
    the pattern, so a `wait_ready()` that had gone back to `pattern in log`
    would fail both halves rather than pass the first by accident.
    """
    _ready_fakes(monkeypatch, world_log="Server: Avg Diff: 12 ms\n")
    spec = docker.ReadySpec(world=r"Avg Diff:\s+\d+", timeout=1.0, interval=0.01)
    assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is True
    # `^` binds to the start of the whole log, and this line is not there.
    spec = docker.ReadySpec(world=r"^Avg Diff", timeout=0.05, interval=0.01)
    assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is False


def test_wait_ready_matches_the_escaped_auth_marker_against_a_real_log_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`azerothcore_ready()` escapes its host, so the match MUST be a regex one.

    A substring search for `127\\.0\\.0\\.1:8085` can never succeed against a
    log that says `127.0.0.1:8085`; readiness would poll out its timeout on a
    server that came up. This project shipped that defect twice.
    """
    _ready_fakes(
        monkeypatch,
        auth_log="Added realm at 127.0.0.1:8085\n",
        world_log="World initialized in 2 minutes 5 seconds. ready...\n",
    )
    ready = docker.azerothcore_ready("127.0.0.1", 8085, timeout=1.0, interval=0.01)
    assert "\\." in ready.auth, "the builder is supposed to escape the host"
    assert docker.wait_ready(SPEC.auth, SPEC.world, ready) is True


def test_wait_ready_does_not_read_already_up_to_date_as_the_ready_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dots in `ready...` are literal dots, not three wildcards.

    Real line from a worldserver that is still loading, with no ready banner in
    sight: `>> Database is already up-to-date! Containing 0 new and 1245
    archived updates.` Unescaped, `ready...` matches `ready up` inside
    `alREADY UP-to-date`, and the wait calls a loading server up — the ready
    stage succeeds, the install is recorded complete, and the user meets a
    world that has not finished loading. That polarity is the twin of the
    never-ready defect, and worse for being silent.

    All four shipped bash scripts escaped these dots on purpose
    (`dml-start.sh`'s `_wait_ready` greps `ready\\.\\.\\.`, as do the three
    `install-wow-wotlk*.sh`); the port dropped what its own lineage carried.
    """
    _ready_fakes(
        monkeypatch,
        world_log=">> Database is already up-to-date! Containing 0 new and 1245 archived.\n",
    )
    ready = docker.azerothcore_ready("127.0.0.1", 8085, timeout=0.05, interval=0.01)
    assert docker.wait_ready(SPEC.auth, SPEC.world, ready) is False


def test_the_fatal_warning_quotes_what_the_server_printed_not_the_pattern(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A catalogue `fatal` is an alternation; echoing it back tells the user nothing.

    The fatal test below cannot see this, because there the pattern and the
    matching line are the same string. With a real pattern they are not, and
    the server's own words are the whole point of the message.
    """
    _ready_fakes(
        monkeypatch,
        world_log="FATAL: could not open configuration file 'worldserver.conf'\n",
    )
    spec = docker.ReadySpec(
        world="ready", fatal=r"Could not connect|FATAL:.*", timeout=60.0, interval=0.01
    )
    with caplog.at_level("WARNING", logger="yulon.docker"):
        assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is False
    assert "could not open configuration file 'worldserver.conf'" in caplog.text


def test_wait_ready_does_not_latch_the_restart_count_from_a_failed_inspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An inspect that failed said nothing — not "this container has restarted 0 times".

    `container_state()` degrades an unreadable inspect to an empty
    `ContainerState`, whose `restart_count` is 0. Latch that as the baseline
    and the next successful read of a server with an ordinary restart history
    reads as "it restarted 97 times while we watched", so a healthy server is
    refused on the second poll. The `world.status` guard on the latch is the
    only thing standing between those two, and without this test it can be
    simplified away with the suite still green.
    """
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)
    inspects = {"n": 0}

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "inspect"]:
            inspects["n"] += 1
            if inspects["n"] == 1:
                return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
            return _completed(stdout="running\t2026-08-22T01:24:53Z\t97\n")
        if cmd[:2] == ["docker", "logs"]:
            return _completed(stdout="World initialized, ready...\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    spec = docker.ReadySpec(world="ready", timeout=1.0, interval=0.01)
    assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is True


def test_wait_ready_with_no_auth_marker_never_reads_the_auth_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`auth=None` means "do not wait on auth": its log is not read and its absence is fine."""
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)
    seen: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.world}\n")  # no auth container at all
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="running\t2026-08-22T01:24:53Z\t0\n")
        if cmd[:2] == ["docker", "logs"]:
            return _completed(stdout="World initialized, ready...\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    spec = docker.ReadySpec(world="ready", auth=None, timeout=1.0, interval=0.01)
    assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is True
    assert not [c for c in seen if c[-1] == SPEC.auth], "the auth container was consulted"


def test_wait_ready_stops_at_once_on_a_fatal_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A line that means "never" ends the wait now, not after the timeout, and says why."""
    seen = _ready_fakes(monkeypatch, world_log="Could not connect to the database\n")
    spec = docker.ReadySpec(
        world="ready", fatal="Could not connect to the database", timeout=60.0, interval=0.01
    )
    with caplog.at_level("WARNING", logger="yulon.docker"):
        assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is False
    assert len([c for c in seen if c[:2] == ["docker", "ps"]]) == 1, "it kept polling"
    assert "Could not connect to the database" in caplog.text


def test_wait_ready_declares_a_crash_loop_when_the_restart_count_keeps_growing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """`RestartCount` growing by `restart_loop` since the first look is a loop, not a slow start.

    Growth is measured from the first observation, not from zero: a server that
    restarted twice last week and is now loading fine must not be refused. The
    poll count is asserted because giving up on the 60s timeout instead of on
    the restart count is a different, much slower behaviour with the same
    return value.
    """
    counter = {"n": 10}

    def inspect() -> str:
        counter["n"] += 1
        return f"running\t2026-08-22T01:24:53Z\t{counter['n']}\n"

    seen = _ready_fakes(monkeypatch, world_log="loading...\n", inspect=inspect)
    spec = docker.ReadySpec(world="ready", restart_loop=3, timeout=60.0, interval=0.01)
    with caplog.at_level("WARNING", logger="yulon.docker"):
        assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is False
    polls = len([c for c in seen if c[:2] == ["docker", "ps"]])
    assert polls == 4, f"polled {polls} times; the loop was not detected on the fourth look"
    assert "restart" in caplog.text.lower()


def test_wait_ready_does_not_call_a_server_that_restarted_last_week_a_crash_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Growth is measured from the first look, so a high but STILL count is fine."""
    _ready_fakes(monkeypatch, world_log="ready...\n", inspect=lambda: "running\tT\t97\n")
    spec = docker.ReadySpec(world="ready", restart_loop=1, timeout=1.0, interval=0.01)
    assert docker.wait_ready(SPEC.auth, SPEC.world, spec) is True


def test_wait_ready_for_hands_the_spec_containers_and_the_ready_spec_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`wait_ready_for()` unpacks the container spec and still names the daemon."""
    seen: list[tuple[str, str, docker.ReadySpec, str | None]] = []
    ready = docker.ReadySpec(world="ready")

    def fake_wait_ready(
        auth: str, world: str, spec: docker.ReadySpec, *, wsl_distro: str | None = None
    ) -> bool:
        seen.append((auth, world, spec, wsl_distro))
        return True

    monkeypatch.setattr(docker, "wait_ready", fake_wait_ready)
    assert docker.wait_ready_for(SPEC, ready, wsl_distro="dml-arch") is True
    assert seen == [(SPEC.auth, SPEC.world, ready, "dml-arch")]


def test_container_state_reads_the_restart_count_in_the_same_inspect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One `docker inspect` answers status, StartedAt AND RestartCount — never a second call."""
    seen: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return _completed(stdout="restarting\t2026-08-22T01:24:53Z\t7\n")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    state = docker.container_state("ac-worldserver")
    assert state == docker.ContainerState("restarting", "2026-08-22T01:24:53Z", 7)
    assert len(seen) == 1 and "{{.RestartCount}}" in seen[0][-1]
    # A two-field answer (older fakes, an unreadable count) still parses, count 0.
    monkeypatch.setattr(
        docker.runner, "run", lambda cmd, cwd=None, timeout=None: _completed(stdout="running\tT\n")
    )
    assert docker.container_state("x") == docker.ContainerState("running", "T", 0)


def test_wait_db_healthy_for_uses_spec_db_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_db_healthy_for()` reads the container name from the spec."""
    seen: list[str] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "inspect"]:
            seen.append(cmd[2])
            return _completed(0, "healthy", "")
        return _completed(0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    spec = docker.ContainerSpec(db="my-db", auth="a", world="w", ports=(1,))
    assert docker.wait_db_healthy_for(spec, timeout=5, interval=0.01) is True
    assert seen == ["my-db"]


def test_port_conflicts_for_uses_spec_ports(monkeypatch: pytest.MonkeyPatch) -> None:
    """`port_conflicts_for()` checks exactly the spec's ports."""
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(0, "other\t0.0.0.0:9999->9999/tcp\n", ""),
    )
    spec = docker.ContainerSpec(db="d", auth="a", world="w", ports=(9999,))
    assert docker.port_conflicts_for(spec) == ["other"]


def test_foreign_port_conflicts_drops_our_own_containers_and_keeps_everything_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The global scan cannot tell "somebody else's server" from "mine, still running".

    A caller that refuses on its raw answer refuses its own install on every
    resume, which is what the native engine's preflight did (review,
    2026-08-23). An UNREADABLE owner is deliberately kept: not knowing who owns
    a container is not proof that we do.
    """
    labels = {"mine": "yulon-wow-wotlk-abc", "theirs": "some-other", "?": docker.UNREADABLE}
    monkeypatch.setattr(docker, "port_conflicts", lambda _ports, **_kw: list(labels))
    monkeypatch.setattr(docker, "container_project", lambda name, **_kw: labels.get(name))
    spec = docker.ContainerSpec(db="d", auth="a", world="w", ports=(9999,))
    assert docker.foreign_port_conflicts(spec, "yulon-wow-wotlk-abc") == ["theirs", "?"]
    # Nothing publishing the ports means nothing is asked about ownership either.
    monkeypatch.setattr(docker, "port_conflicts", lambda _ports, **_kw: [])
    monkeypatch.setattr(
        docker, "container_project", lambda _n, **_kw: pytest.fail("asked who owns nothing")
    )
    assert docker.foreign_port_conflicts(spec, "yulon-wow-wotlk-abc") == []


def test_docker_ctl_convenience_wrappers_delegate_to_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker_ctl`'s pre-bound wrappers use `SPEC`, not caller-supplied names."""
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(
            0, "ac-worldserver\t0.0.0.0:8085->8085/tcp\n", ""
        ),
    )
    assert docker_ctl.port_conflicts_here() == ["ac-worldserver"]


def _start_runner(calls: list[list[str]], up: tuple[str, ...] | None = None):
    """A `runner.run` double for the start path.

    `start_staged()` confirms with `docker ps` that the services it named are
    actually running, because `compose up` exits 0 for a container that started
    and died — so a double that answers nothing now means "nothing came up".
    """
    names = up if up is not None else (SPEC.db, SPEC.auth, SPEC.world)

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + chr(10) for n in names))
        return _completed()

    return fake_run


def test_start_staged_names_the_services_so_compose_cannot_pick_the_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole fix in one assertion: only the three long-running services are asked for.

    A bare `compose up -d` starts every service without a running container,
    which on an installed server means AzerothCore's one-shot `ac-db-import`
    runs again and takes the database with it. Compose cannot select a service
    nobody named, and `--no-deps` stops it being pulled back in as a dependency.
    """
    calls: list[list[str]] = []
    cwds: list[Path | None] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        cwds.append(cwd)
        if cmd[:2] == ["docker", "ps"]:  # the post-start confirmation
            names = (SPEC.db, SPEC.auth, SPEC.world)
            return _completed(stdout="".join(n + chr(10) for n in names))
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    server_dir = Path("/tmp/wow")
    assert docker.start_staged(SPEC, server_dir) is True
    up = ["docker", "compose", "up", "-d", "--no-deps", SPEC.db, SPEC.auth, SPEC.world]
    assert calls[0] == up
    assert cwds[0] == server_dir, "must address the project by directory, not by global name"
    assert ["docker", "compose", "up", "-d"] not in calls


def test_start_staged_never_starts_a_container_by_global_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two installs of one game share container names; only the directory tells them apart.

    The previous implementation listed containers with a global `docker ps -a`
    and started them by name, so pressing Start on install B could start
    install A's server while showing B's tab.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _start_runner(calls))
    docker.start_staged(SPEC, Path("/tmp/install-b"))
    assert not any(cmd[:2] == ["docker", "start"] for cmd in calls)
    assert not any(cmd[:3] == ["docker", "ps", "-a"] for cmd in calls)


def test_compose_services_defaults_to_the_container_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """AzerothCore names its services and containers alike; other games may not."""
    assert SPEC.compose_services() == (SPEC.db, SPEC.auth, SPEC.world)

    renamed = docker.ContainerSpec(
        db="c-db",
        auth="c-auth",
        world="c-world",
        ports=(1,),
        services=("s-db", "s-auth", "s-world"),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner, "run", _start_runner(calls, up=("c-db", "c-auth", "c-world"))
    )
    docker.start_staged(renamed, Path("/tmp/x"))
    assert calls[0][-3:] == ["s-db", "s-auth", "s-world"]


def test_pin_project_name_writes_what_compose_already_calls_the_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pinned value must equal the current name, or pinning renames the project.

    Compose derives the project from the directory basename by rules that are
    its own (`WoW_Server 2` → `wow_server2`, `Ünïcode` → `ncode`), so the name
    is asked for rather than recomputed here.
    """
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(
            stdout='{"name": "wow_server2", "services": {}}'
        ),
    )
    assert docker.pin_project_name(tmp_path) == "wow_server2"
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "COMPOSE_PROJECT_NAME=wow_server2\n" in env
    assert "\r\n" not in env, "a CRLF .env is read inside a Linux container"


def test_pin_project_name_never_overwrites_an_existing_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Re-attaching an install must not repoint it at its current folder name.

    The pin exists precisely because the folder may have moved; rewriting it
    from the new basename would undo the thing it is for.
    """
    (tmp_path / ".env").write_text(
        "AC_SOMETHING=1\nCOMPOSE_PROJECT_NAME=original-name\n", encoding="utf-8", newline="\n"
    )
    called: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: (called.append(cmd), _completed())[1],
    )
    assert docker.pin_project_name(tmp_path) is None
    assert called == [], "must not even ask compose when a pin is already there"
    assert "original-name" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_pin_project_name_appends_without_clobbering_an_env_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The installer's own .env holds the database password; it must survive."""
    (tmp_path / ".env").write_text("DB_ROOT_PASSWORD=hunter2", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(stdout='{"name": "srv"}'),
    )
    docker.pin_project_name(tmp_path)
    env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "DB_ROOT_PASSWORD=hunter2\n" in env, "the existing .env was clobbered"
    assert "COMPOSE_PROJECT_NAME=srv\n" in env


def test_pin_project_name_declines_rather_than_guess_when_compose_cannot_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A wrong pin is worse than none: it renames the project and orphans containers."""
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(returncode=1, stderr="no such file"),
    )
    assert docker.pin_project_name(tmp_path) is None
    assert not (tmp_path / ".env").exists()


def test_wait_ready_ignores_the_previous_runs_ready_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A restarted server is not ready just because it once was.

    Docker keeps a container's output across restarts, so after a stop/start the
    previous run's `ready...` is still in the log. Reading the whole log made
    this return True the instant the container came back, while the server was
    still loading — measured on a real AzerothCore server, whose last words
    before being killed were `>> Loaded 13567 Quest Offer Reward Locale
    Strings`. Scoping the read to the current run is the fix.
    """
    seen: list[list[str]] = []
    # What `docker logs` returns for the WHOLE history: the old run said ready.
    whole_history = "starting up\nWorld initialized, ready...\nstopping\nstarting up again\n"
    # What it returns for THIS run only: still loading.
    this_run = "starting up again\n>> Loaded 13567 Quest Offer Reward Locale Strings\n"

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "inspect"]:
            if "{{.State.Status}}" in cmd[-1]:
                return _completed(
                    stdout="running" + chr(9) + "2026-08-22T01:24:53.575296627Z" + chr(10)
                )
            return _completed(stdout="2026-08-22T01:24:53.575296627Z" + chr(10))
        if cmd[:2] == ["docker", "logs"]:
            scoped = "--since" in cmd
            if cmd[-1] == SPEC.auth:
                return _completed(stdout="Added realm at 127.0.0.1:8085\n")
            return _completed(stdout=this_run if scoped else whole_history)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert (
        docker.wait_ready(
            SPEC.auth,
            SPEC.world,
            docker.azerothcore_ready("127.0.0.1", 8085, timeout=0.2, interval=0.1),
        )
        is False
    )
    assert any("--since" in cmd for cmd in seen), "readiness must scope logs to the current run"


def test_wait_ready_still_succeeds_when_this_run_is_actually_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scoping must not break the case it exists to make honest."""

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "inspect"]:
            if "{{.State.Status}}" in cmd[-1]:
                return _completed(
                    stdout="running" + chr(9) + "2026-08-22T01:24:53.575296627Z" + chr(10)
                )
            return _completed(stdout="2026-08-22T01:24:53.575296627Z" + chr(10))
        if cmd[:2] == ["docker", "logs"]:
            if cmd[-1] == SPEC.auth:
                return _completed(stdout="Added realm at 127.0.0.1:8085\n")
            return _completed(stdout="World initialized, ready...\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert (
        docker.wait_ready(
            SPEC.auth,
            SPEC.world,
            docker.azerothcore_ready("127.0.0.1", 8085, timeout=2.0, interval=0.1),
        )
        is True
    )


def test_logs_without_a_readable_start_time_falls_back_to_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable start time must degrade to the old behaviour, not to silence."""

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(returncode=1, stderr="no such container")
        return _completed(stdout="everything\n")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.started_at("gone") == ""


PROJECT = "wow-server"


def _stop_runner(
    calls: list[list[str]],
    *,
    running: set[str] | None = None,
    owner: str | None = PROJECT,
    owners: dict[str, str | None] | None = None,
    inspect_fails: bool = False,
    inspect_fails_after_stop: bool = False,
    compose_stop_fails: bool = False,
    compose_stop_matches: bool = True,
    stop_really_works: bool = True,
):
    """A `runner.run` double for the stop path.

    `running` is what `docker ps` reports; `owner` is the compose project label
    every container claims. A container name proves nothing about ownership, so
    a test can make those two disagree.

    The three ways ownership can read differently are kept apart on purpose:
    `owner="x"` is a label naming project x, `owner=None` is a container with no
    compose label at all (started outside compose), and `inspect_fails=True` is
    Docker refusing to answer. The first two are "not ours"; the third is "ask
    again later", and collapsing them is the bug this distinction fixes.

    `compose_stop_matches=False` models the moved folder: compose exits 0 having
    matched no container, so nothing actually stops.
    """
    live = set() if running is None else set(running)
    state = {"stopped": False}

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "' + PROJECT + '"}')
        if cmd[:3] == ["docker", "compose", "stop"]:
            state["stopped"] = True  # set even when it fails: the moment has passed
            if compose_stop_fails:
                return _completed(returncode=1, stderr="no configuration file provided")
            if compose_stop_matches:
                live.clear()
            return _completed()
        if cmd[:2] == ["docker", "inspect"]:
            if inspect_fails or (inspect_fails_after_stop and state["stopped"]):
                return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
            # Per-container when `owners` is given, so one container can be
            # ours while another belongs to a neighbour -- the state two
            # installs of one game can genuinely reach.
            who = owners.get(cmd[2], owner) if owners is not None else owner
            return _completed(stdout="" if who is None else who + chr(10))
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            if stop_really_works:
                live.discard(cmd[-1])
            return _completed()
        return _completed()

    return fake_run


def test_stop_staged_uses_compose_stop_so_the_containers_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compose stop` keeps every container and honours the project's depends_on order."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner, "run", _stop_runner(calls, running={SPEC.db, SPEC.auth, SPEC.world})
    )
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is True
    assert any(cmd[:3] == ["docker", "compose", "stop"] for cmd in calls)
    assert not any(
        cmd[:3] == ["docker", "compose", "down"] for cmd in calls
    ), "a stop removed containers"
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls), "did not trust compose stop"


def test_stop_staged_says_false_when_there_was_nothing_of_ours_to_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The return value used to be `compose stop`'s exit code, which is 0 for an empty project.

    So a Stop pressed on a server that was never running reported "stopped" —
    indistinguishable, from the caller's side, from a stop that really happened.
    Nothing of ours running means False, and there is nothing to ask compose.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _stop_runner(calls))
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is False
    # `compose stop` still runs: the project also holds ac-db-import and
    # ac-client-data-init, and an interrupted install leaves one of those
    # downloading. What must NOT happen is a container stopped by name.
    assert any(cmd[:3] == ["docker", "compose", "stop"] for cmd in calls)
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)


def test_stop_staged_will_not_stop_a_container_it_cannot_prove_is_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The container names are global; two installs of one game share them exactly.

    Install A is running. The user presses Stop on install B, whose own
    containers are already down. Going by name, B's postcondition sees A's
    running containers, concludes its own stop failed, and stops them — killing
    a server somebody else is playing on. The compose project label is the only
    ownership proof, so a foreign owner means: touch nothing.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(
            calls,
            running={SPEC.db, SPEC.auth, SPEC.world},
            owner="somebody-elses-install",
        ),
    )
    with pytest.raises(docker.DockerCommandError, match="do not belong to the install") as caught:
        docker.stop_staged(SPEC, Path("/tmp/install-b"))
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls), "stopped a foreign server"
    assert not any(
        cmd[:3] == ["docker", "compose", "stop"] for cmd in calls
    ), "asked compose to stop a project it had already been shown is not ours"

    # The remedy has to be one that works. "Re-attach this install" did not:
    # attach no longer pins, and the version that did would have written the
    # current basename — the exact value that produces this mismatch.
    message = str(caught.value)
    assert "somebody-elses-install" in message, "did not say who does own them"
    assert "COMPOSE_PROJECT_NAME=somebody-elses-install" in message
    assert "re-attach" not in message.lower()


def test_stop_staged_gives_up_rather_than_guessing_when_ownership_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable label is not permission to fall back to matching by name.

    Nor is it evidence of a second install: `docker inspect` failing means Docker
    would not answer, which is a different situation from a container that
    answers with somebody else's project. Reporting the first as the second sent
    the user chasing an install that does not exist.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(
            calls,
            running={SPEC.db, SPEC.auth, SPEC.world},
            inspect_fails=True,
        ),
    )
    with pytest.raises(docker.DockerCommandError, match="would not say which project owns") as e:
        docker.stop_staged(SPEC, Path("/tmp/wow"))
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)
    assert not any(
        cmd[:3] == ["docker", "compose", "down"] for cmd in calls
    ), "a stop removed containers"
    assert "another install" not in str(e.value) or "rather than" in str(e.value)


def test_a_container_with_no_compose_label_at_all_is_a_stranger_not_ours(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Someone ran `docker run --name ac-database` by hand. That is not this install."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(calls, running={SPEC.db}, owner=None),
    )
    with pytest.raises(docker.DockerCommandError, match="no compose project at all") as caught:
        docker.stop_staged(SPEC, Path("/tmp/wow"))
    assert SPEC.db in str(caught.value)
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)


def test_stop_staged_finishes_the_job_when_compose_stopped_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The moved-install case: `compose stop` succeeds and stops nothing.

    Compose identifies a project by its directory basename, so a renamed folder
    makes `compose stop` exit 0 having matched no container. These containers
    ARE ours — the label agrees — so the job is finished by name, world first.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(calls, running={SPEC.db, SPEC.auth, SPEC.world}, compose_stop_matches=False),
    )
    assert docker.stop_staged(SPEC, Path("/tmp/moved-install")) is True
    assert [cmd for cmd in calls if cmd[:2] == ["docker", "stop"]] == [
        ["docker", "stop", "-t", _GRACE, SPEC.world],
        ["docker", "stop", "-t", _GRACE, SPEC.auth],
        ["docker", "stop", "-t", _GRACE, SPEC.db],
    ]


def test_stop_staged_raises_when_the_containers_will_not_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still up after being told twice: say so rather than claiming success."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(
            calls, running={SPEC.world}, compose_stop_matches=False, stop_really_works=False
        ),
    )
    with pytest.raises(docker.DockerCommandError, match="still running after stop"):
        docker.stop_staged(SPEC, Path("/tmp/wow"))


def test_docker_stop_treats_a_vanished_container_as_already_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container removed between listing and stopping is the goal state, not an error."""
    live = {SPEC.world}

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "' + PROJECT + '"}')
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout=PROJECT + "\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            live.discard(cmd[-1])
            return _completed(
                returncode=1,
                stderr="Error response from daemon: No such container: " + cmd[-1],
            )
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is True


def test_pin_project_name_never_truncates_the_env_on_a_write_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The .env holds the database root password; a failed pin must not empty it."""
    env = tmp_path / ".env"
    env.write_text("DB_ROOT_PASSWORD=hunter2\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(stdout='{"name": "srv"}'),
    )

    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", boom)
    assert docker.pin_project_name(tmp_path) is None
    assert env.read_text(encoding="utf-8") == "DB_ROOT_PASSWORD=hunter2\n"


def test_pin_project_name_leaves_non_utf8_bytes_alone(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A password with odd bytes must survive being appended to, not be rewritten."""
    env = tmp_path / ".env"
    odd = b"DB_ROOT_PASSWORD=caf\xe9\n"
    env.write_bytes(odd)
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(stdout='{"name": "srv"}'),
    )
    docker.pin_project_name(tmp_path)
    assert env.read_bytes().startswith(odd), "the original bytes were altered"
    assert b"COMPOSE_PROJECT_NAME=srv" in env.read_bytes()


def test_stop_staged_reads_the_pin_when_compose_cannot_be_parsed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Ownership must still be provable in the exact case the fallback exists for.

    The by-name path exists for a project whose compose files cannot be read —
    but `compose_project_name()` needs those same files, so ownership became
    unprovable at the one moment it mattered, and a running server was left up
    while the user was told it stopped. The pinned `.env` value needs no compose.
    """
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=wow-server\n", encoding="utf-8", newline="\n"
    )
    calls: list[list[str]] = []
    live = {SPEC.db, SPEC.auth, SPEC.world}

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        if cmd[:3] == ["docker", "compose", "stop"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="wow-server\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            live.discard(cmd[-1])
            return _completed()
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, tmp_path) is True
    assert [cmd for cmd in calls if cmd[:2] == ["docker", "stop"]] == [
        ["docker", "stop", "-t", _GRACE, SPEC.world],
        ["docker", "stop", "-t", _GRACE, SPEC.auth],
        ["docker", "stop", "-t", _GRACE, SPEC.db],
    ]


def test_stop_staged_says_so_rather_than_claiming_a_stop_it_cannot_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unprovable ownership with our names running is an error, not a quiet False.

    `Controller.stop()` discards the return value, so returning False here would
    leave the UI reporting a stopped server while it is still up.
    """

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        if cmd[:3] == ["docker", "compose", "stop"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=SPEC.world + "\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    with pytest.raises(docker.DockerCommandError, match="cannot tell which containers"):
        docker.stop_staged(SPEC, tmp_path)


def test_pinned_project_name_reads_the_env_without_compose(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "DB_ROOT_PASSWORD=x\nCOMPOSE_PROJECT_NAME=my-server\n", encoding="utf-8", newline="\n"
    )
    assert docker.pinned_project_name(tmp_path) == "my-server"
    assert docker.pinned_project_name(tmp_path / "nope") is None


def test_stop_staged_reports_rather_than_guesses_when_a_moved_install_was_never_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No pin plus a moved folder is indistinguishable from somebody else's install.

    Compose names a project after the directory basename, so a moved install
    reports a name none of its containers carry — and an install created before
    pinning existed has no `.env` value to correct it. From here that looks
    exactly like a *second* install of the same game whose containers belong to
    someone else, because the container names are shared.

    Guessing either way is unacceptable: adopt the containers and a stopped
    install kills a running one; ignore them and the user is told a running
    server stopped. So it says so, and points at the fix.
    """
    calls: list[list[str]] = []
    live = {SPEC.db, SPEC.auth, SPEC.world}

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "renamed-by-the-user"}')
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="original-name\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            live.discard(cmd[-1])
            return _completed()
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    with pytest.raises(docker.DockerCommandError, match="do not belong to the install") as caught:
        docker.stop_staged(SPEC, tmp_path)
    assert live == {SPEC.db, SPEC.auth, SPEC.world}, "stopped what it could not prove was its own"
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)
    # It has to name the way out, and the way out is the containers' own project.
    message = str(caught.value)
    assert "COMPOSE_PROJECT_NAME=original-name" in message
    assert str(tmp_path / ".env") in message


def test_stop_staged_stops_a_moved_install_that_WAS_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pin is what makes a moved folder unambiguous, and therefore stoppable."""
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=original-name\n", encoding="utf-8", newline="\n"
    )
    calls: list[list[str]] = []
    live = {SPEC.db, SPEC.auth, SPEC.world}

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "renamed-by-the-user"}')
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="original-name\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            live.discard(cmd[-1])
            return _completed()
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, tmp_path) is True
    assert [cmd for cmd in calls if cmd[:2] == ["docker", "stop"]] == [
        ["docker", "stop", "-t", _GRACE, SPEC.world],
        ["docker", "stop", "-t", _GRACE, SPEC.auth],
        ["docker", "stop", "-t", _GRACE, SPEC.db],
    ]
    assert live == set()


def test_install_project_prefers_the_pin_over_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pin survives the folder moving; the directory basename does not."""
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=pinned-name\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(stdout='{"name": "just-the-folder-name"}'),
    )
    assert docker.install_project(SPEC, tmp_path) == "pinned-name"


def test_a_stop_cannot_be_confirmed_when_docker_will_not_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unanswerable verification is not a pass — the premise is verify, don't believe."""
    (tmp_path / ".env").write_text("COMPOSE_PROJECT_NAME=proj\n", encoding="utf-8", newline="\n")

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        if cmd[:2] == ["docker", "ps"]:
            return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="proj\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    with pytest.raises(docker.DockerCommandError, match="cannot be confirmed"):
        docker.stop_staged(SPEC, tmp_path)


def test_stop_staged_will_not_claim_success_when_ownership_goes_dark_mid_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still up after the stop, and Docker has stopped answering: that is not "stopped".

    The post-stop census used to read only `.ours`. A container that is plainly
    still in `docker ps` but whose `docker inspect` now fails lands in
    `unreadable`, so `.ours` was empty and the function reported a clean stop —
    the exact outcome its own docstring calls the worst possible one. The same
    condition is a hard refusal *before* the stop; it was silently discarded
    after it (review, 2026-08-22).
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(
            calls,
            running={SPEC.db, SPEC.auth, SPEC.world},
            compose_stop_fails=True,
            compose_stop_matches=False,
            inspect_fails_after_stop=True,
        ),
    )
    with pytest.raises(docker.DockerCommandError, match="cannot be confirmed"):
        docker.stop_staged(SPEC, Path("/tmp/wow"))


def test_stop_staged_refuses_a_half_and_half_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """One container ours, one a neighbour's — the state shared container names allow.

    Install A holds `ac-database`; install B later created `ac-authserver` and
    `ac-worldserver` because those names happened to be free. Stopping either
    would take down half of somebody else's server, so neither is touched.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(
            calls,
            running={SPEC.db, SPEC.auth, SPEC.world},
            owners={SPEC.db: PROJECT, SPEC.auth: "install-b", SPEC.world: "install-b"},
        ),
    )
    with pytest.raises(docker.DockerCommandError, match="do not belong to the install") as caught:
        docker.stop_staged(SPEC, Path("/tmp/wow"))
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)
    message = str(caught.value)
    named_as_strangers = message.split(" are running", 1)[0]
    assert SPEC.db not in named_as_strangers, "listed our own container among the strangers"
    assert "install-b" in message


def test_the_stop_grace_covers_the_slowest_shutdown_ever_measured() -> None:
    """Docker's 10-second default was measured killing a live save; this is the floor.

    Two clean shutdowns of a populated worldserver on yulon-ubuntu (1980
    characters online, AzerothCore + playerbots, 2026-08-23) took 90.7s and
    73.4s under a grace long enough not to bind. A grace below the worse of
    those two would have SIGKILLed the first one mid-save, which is exactly what
    a 10-second `compose stop` did on that box the same day: exit 137.

    The bound is the measurement, not the chosen value, so re-tuning the margin
    stays possible without editing a test — dropping back towards Docker's
    default does not.
    """
    assert docker.STOP_GRACE_SECONDS >= 91, "shorter than a shutdown we have actually watched"


def test_compose_stop_asks_for_the_measured_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without `--timeout`, `compose stop` takes Docker's 10s and kills the save queue."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner, "run", _stop_runner(calls, running={SPEC.db, SPEC.auth, SPEC.world})
    )
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is True
    stops = [cmd for cmd in calls if cmd[:3] == ["docker", "compose", "stop"]]
    assert stops == [["docker", "compose", "stop", "-t", _GRACE]]


def test_the_by_name_fallback_asks_for_the_measured_grace_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback is the path an unreadable-compose install stops on — the same server.

    It is the easier one to leave on the 10-second default, since it is only
    reached when `compose stop` could not run or stopped nothing, and it is
    where the worldserver is stopped first and alone.
    """
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    docker._run_docker_stop(SPEC.world)
    assert calls == [["docker", "stop", "-t", _GRACE, SPEC.world]]


def test_the_stop_paths_impose_no_subprocess_deadline_of_their_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `runner.run()` timeout shorter than the grace would kill the CLI mid-shutdown.

    `runner.run()` reports a timeout as a non-zero return code, so a deadline
    here would not raise — `stop_staged()` would fall through to its by-name
    fallback while the daemon was still stopping the same containers, and the
    user would be told the stop could not be confirmed on a server that was
    shutting down normally.
    """
    calls: list[list[str]] = []
    # `compose_stop_matches=False` is the moved folder, so both stop paths run.
    inner = _stop_runner(
        calls, running={SPEC.db, SPEC.auth, SPEC.world}, compose_stop_matches=False
    )
    seen: list[float | None] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "stop"] or cmd[:3] == ["docker", "compose", "stop"]:
            seen.append(timeout)
        return inner(cmd, cwd, timeout)

    monkeypatch.setattr(docker.runner, "run", fake_run)
    docker.stop_staged(SPEC, Path("/tmp/moved-install"))
    assert seen, "no stop command ran"
    assert set(seen) == {None}, f"a stop carried a subprocess deadline: {seen}"


def test_the_message_for_two_owners_offers_no_single_name_to_pin() -> None:
    """`owners[0]` is only the alphabetically first; pinning it reconciles nothing.

    It used to say "set COMPOSE_PROJECT_NAME=install-a", which is a permanent,
    irreversible write that leaves half the containers still foreign — and the
    next Stop still refuses (review, 2026-08-22).
    """
    message = docker._stranger_message(
        ((SPEC.auth, "install-a"), (SPEC.world, "zzz-other")), PROJECT, Path("/tmp/wow")
    )
    assert f"{docker.PROJECT_NAME_VAR}=install-a" not in message
    assert "More than one project" in message
    assert "docker compose ls" in message


def test_the_remedy_offers_deleting_a_copied_pin_not_a_folder_rename(tmp_path: Path) -> None:
    """With a pin in place, a folder move cannot be the cause — so do not suggest it.

    The pin outranks the directory, so renaming the folder back is inert. The
    causes that remain are a genuinely different install and a `.env` copied
    along with the folder — and telling a user in the copy case to "change
    COMPOSE_PROJECT_NAME because the folder was moved" is how the copy ends up
    stopping the original's server (review, 2026-08-22).
    """
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=stale-pin" + chr(10), encoding="utf-8", newline=chr(10)
    )
    message = docker._stranger_message(((SPEC.world, "real-project"),), "stale-pin", tmp_path)
    assert "copied here from another install, delete it" in message
    assert "the folder was moved" not in message
    assert "rename this folder" not in message


def test_no_single_remedy_is_offered_when_an_unlabelled_stranger_is_also_present(
    tmp_path: Path,
) -> None:
    """Adopting the one project leaves the unlabelled container foreign, so Stop still refuses.

    The next refusal then has no owners at all and offers no remedy — a
    permanent write that bought nothing (review, 2026-08-22).
    """
    strangers = ((SPEC.world, "install-b"), (SPEC.db, None))
    message = docker._stranger_message(strangers, "ours", tmp_path)
    assert f"{docker.PROJECT_NAME_VAR}=install-b" not in message
    assert "no compose project at all" in message


def test_a_stop_does_not_write_a_pin_even_though_it_could(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The census proves the basename is right — and writing it down is still wrong.

    A pin lives in `.env`, `.env` travels with the folder, and `install_project()`
    prefers it over the directory. So a pin written here is inherited by any COPY
    of the install (a second realm, a restored backup), and pressing Stop in the
    copy stops the ORIGINAL's running server — the copy having been handed the
    original's identity. Unpinned, the copy's basename disagrees with the
    container labels and the census refuses.

    This was implemented, measured doing exactly that, and reverted the same day
    (review, 2026-08-22).
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner, "run", _stop_runner(calls, running={SPEC.db, SPEC.auth, SPEC.world})
    )
    assert docker.stop_staged(SPEC, tmp_path) is True
    assert docker.pinned_project_name(tmp_path) is None, "a Stop wrote a claimable identity"
    assert not (tmp_path / ".env").exists()


def test_pinned_project_name_takes_the_last_assignment_and_accepts_export(
    tmp_path: Path,
) -> None:
    """Appending is how the app's own advice gets followed; the last line is what counts."""
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=old-wrong-name\nexport COMPOSE_PROJECT_NAME=real-project\n",
        encoding="utf-8",
        newline="\n",
    )
    assert docker.pinned_project_name(tmp_path) == "real-project"


def test_refusing_without_an_identity_does_not_read_a_failed_ps_as_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_status_safe() or []` turned "Docker would not answer" into "nothing is running".

    The user was then told the server had stopped while it was still serving.
    Socket permissions, a wrong DOCKER_HOST and an API timeout all land here.
    """
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None, timeout=None: _completed(
            returncode=1, stderr="permission denied while trying to connect"
        ),
    )
    with pytest.raises(docker.DockerCommandError, match="nothing about it can be established"):
        docker.stop_staged(SPEC, Path("/tmp/unpinned"))


def test_start_staged_will_not_report_success_for_a_container_that_died(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compose up` exits 0 for a container that started and immediately exited.

    Reported as started, the caller then sat out `wait_ready()`'s 480 seconds
    before hearing anything at all (review, 2026-08-22).
    """
    calls: list[list[str]] = []
    # Only the database came up; auth and world died on start.
    monkeypatch.setattr(docker.runner, "run", _start_runner(calls, up=(SPEC.db,)))
    with pytest.raises(docker.DockerCommandError, match="compose reported success"):
        docker.start_staged(SPEC, Path("/tmp/wow"))


def test_wait_ready_is_not_fooled_by_a_container_in_restart_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker ps` lists a crash-looping container, and its StartedAt is the last run's.

    So both of the other checks pass while the worldserver restarts on a loop —
    the same false "ready" the `--since` scoping was added to remove, arriving
    by a different route. Every service here carries `restart: unless-stopped`
    (review, 2026-08-22).
    """
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "inspect"]:
            if "{{.State.Status}}" in cmd[-1]:
                return _completed(
                    stdout="restarting" + chr(9) + "2026-08-22T01:24:53.575296627Z" + chr(10)
                )
            return _completed(stdout="2026-08-22T01:24:53.575296627Z" + chr(10))
        if cmd[:2] == ["docker", "logs"]:
            if cmd[-1] == SPEC.auth:
                return _completed(stdout="Added realm at 127.0.0.1:8085" + chr(10))
            return _completed(stdout="World initialized, ready..." + chr(10))
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert (
        docker.wait_ready(
            SPEC.auth,
            SPEC.world,
            docker.azerothcore_ready("127.0.0.1", 8085, timeout=0.3, interval=0.1),
        )
        is False
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("alpha", "alpha"),
        # An inline comment is not part of the value. Measured against the real
        # CLI: `COMPOSE_PROJECT_NAME=alpha # my realm` is project `alpha`, and
        # reading it as `alpha # my realm` made Yu'lon believe in a project no
        # container carries — a live server reported down and unstoppable. The
        # app's own refusal text invites exactly this, by telling the user to
        # add a line to `.env` (review, 2026-08-22).
        ("alpha # my realm", "alpha"),
        # No space before the `#`. Implementations differ here, and it cannot
        # matter: compose project names are `[a-z0-9][a-z0-9_-]*`, so either
        # reading of `alpha#x` is an impossible name. Stripping is the reading
        # that still finds the containers.
        ("alpha#no-space", "alpha"),
        ("'eps' # hi", "eps"),
        ('"a#b" # c', "a#b"),  # a `#` inside quotes is part of the value
        ('"has space"', "has space"),
        ("  spaced  ", "spaced"),
        ('"unterminated', "unterminated"),
        ("", ""),
        ("#", ""),
    ],
)
def test_env_values_are_read_the_way_compose_reads_them(raw: str, expected: str) -> None:
    """This parsing decides which containers the app believes are its own."""
    assert docker._env_value(raw) == expected


def test_an_empty_last_assignment_unsets_the_pin(tmp_path: Path) -> None:
    """Compose falls back to the basename; leaving the earlier value standing would not.

    `found = value or found` kept the first line's value alive, so the app and
    compose disagreed about the project with nothing on screen to say so
    (review, 2026-08-22).
    """
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=alpha" + chr(10) + "COMPOSE_PROJECT_NAME=" + chr(10),
        encoding="utf-8",
        newline=chr(10),
    )
    assert docker.pinned_project_name(tmp_path) is None


def test_a_pin_that_needs_expanding_is_reported_as_no_pin_at_all(tmp_path: Path) -> None:
    """Compose expands `${VAR}` in `.env`; reimplementing that here would be a second copy.

    So a value needing expansion is treated as unpinned and ownership falls
    through to asking compose. That fails closed — the install stays stoppable
    while its compose files are readable — instead of believing in a project
    literally named `ac-${REALM}` that no container carries
    (review, 2026-08-23).
    """
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=ac-${REALM}" + chr(10), encoding="utf-8", newline=chr(10)
    )
    assert docker.pinned_project_name(tmp_path) is None


def test_export_is_accepted_with_a_tab_as_well_as_a_space(tmp_path: Path) -> None:
    """`"export "` as a literal missed `export\\tNAME=x`, which compose accepts."""
    (tmp_path / ".env").write_text(
        "export" + chr(9) + "COMPOSE_PROJECT_NAME=tabbed" + chr(10),
        encoding="utf-8",
        newline=chr(10),
    )
    assert docker.pinned_project_name(tmp_path) == "tabbed"


def test_a_utf8_bom_does_not_hide_the_pin(tmp_path: Path) -> None:
    """PowerShell writes a BOM; compose reads past it and this used not to.

    `_stranger_message()` tells the user to add `COMPOSE_PROJECT_NAME=<x>` to
    this file. On Windows the tools to hand put `EF BB BF` in front of it
    (PowerShell 5.1's `Set-Content -Encoding utf8`, Notepad's "UTF-8 with BOM"),
    and under a plain `utf-8` decode the first line began `\\ufeffCOMPOSE_...`,
    so the pin was invisible.

    That is a disagreement rather than a quirk, which is what makes it a bug:
    measured on Windows 11 / Docker 29.7.2 (2026-08-23), `docker compose config`
    read the very same file and reported the project as `bomtest` while this
    function reported `None`. The fallback that hides it — asking compose — is
    exactly what is unavailable in the case this function exists for.
    """
    (tmp_path / ".env").write_bytes(
        b"\xef\xbb\xbfCOMPOSE_PROJECT_NAME=bomtest\r\n# written on Windows\r\n"
    )
    assert docker.pinned_project_name(tmp_path) == "bomtest"


def test_a_utf8_bom_does_not_make_pinning_append_a_second_assignment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The BOM's second victim: a pin the app cannot see is a pin it writes over.

    `pin_project_name()` asks `pinned_project_name()` first and returns early
    when something is already pinned. Blind to the BOM'd line it wrote a second
    `COMPOSE_PROJECT_NAME=` below it, and compose takes the LAST assignment — so
    the app silently overrode the name the user had set, on the app's own
    instructions.
    """
    (tmp_path / ".env").write_bytes(b"\xef\xbb\xbfCOMPOSE_PROJECT_NAME=the-users-name\r\n")
    monkeypatch.setattr(docker, "compose_project_name", lambda _dir: "the-directory-basename")

    assert docker.pin_project_name(tmp_path) is None
    raw = (tmp_path / ".env").read_bytes()
    assert raw.count(b"COMPOSE_PROJECT_NAME=") == 1, "the user's pin was written over"
    assert docker.pinned_project_name(tmp_path) == "the-users-name"


def test_the_unpinned_remedy_warns_about_the_copy_case(tmp_path: Path) -> None:
    """This is the common branch now, and following it literally on a copy is destructive.

    A copy that adopts the original's project name makes the next Stop here take
    down the original's server — the measured failure that got the Stop-time pin
    deleted. The remedy has to say so (review, 2026-08-23).
    """
    message = docker._stranger_message(((SPEC.world, "install-b"),), "ours", tmp_path)
    assert "not copied" in message
    assert "take down the other server" in message


# ------------------------------------------------- naming the docker CLI
# Windows hands a process its environment once. Docker Desktop's installer adds
# `resources\bin` to the PATH in the REGISTRY, which the launcher that just ran
# that installer is never handed — so `platform.docker_programs()` was added to
# find the binary anyway, and until 2026-08-23 nothing in this module used it.
# Provisioning succeeded and the next `docker compose up` still died with
# `[WinError 2] The system cannot find the file specified`.

OFF_PATH_EXE = r"C:\Users\pk\AppData\Local\Programs\DockerDesktop\resources\bin\docker.EXE"


@pytest.fixture
def off_path_docker(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A host where docker is reachable only by absolute path; records every argv."""
    monkeypatch.setattr(docker.platform, "_resolved_docker_cli", OFF_PATH_EXE)
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        return _completed(stdout="running\tsomewhen")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    return calls


@pytest.fixture
def no_docker(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A host with no docker CLI at all; records anything that still reached `runner`."""
    monkeypatch.setattr(docker.platform, "_resolved_docker_cli", None)
    monkeypatch.setattr(docker.platform, "docker_programs", lambda: ("docker",))
    monkeypatch.setattr(docker.platform, "_which", lambda name, path=None: None)
    escaped: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        escaped.append(cmd)
        raise AssertionError(f"spawned {cmd[0]} on a host that has no docker")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    return escaped


def test_every_command_is_built_with_the_resolved_cli(off_path_docker: list[list[str]]) -> None:
    """The regression. Every one of these used to hardcode `docker` as argv[0].

    Covered in one test rather than seven because this is one mistake made
    nine times; seven separate tests would let the tenth site be written
    without one.
    """
    server_dir = Path("/tmp/wow")
    docker.start(server_dir)  # through _run()
    docker.compose_project_name(server_dir)
    docker.container_project(SPEC.world)
    docker._run_docker_stop(SPEC.world)
    docker.health(SPEC.world)
    docker.container_state(SPEC.world)
    docker._logs(SPEC.world)
    assert off_path_docker, "nothing ran"
    assert all(cmd[0] == OFF_PATH_EXE for cmd in off_path_docker), off_path_docker
    # ...and nothing else moved: the command each site sends is unchanged.
    # The stop row is read whole rather than through the two-element slice the
    # others use. Sliced, it became `["stop", "-t"]` when the grace arrived — a
    # row that no longer asserted the container name reaches argv at all, unlike
    # every one of its neighbours (review, 2026-08-23).
    assert [cmd[1:3] for cmd in off_path_docker if cmd[1] != "stop"] == [
        ["compose", "up"],
        ["compose", "config"],
        ["inspect", SPEC.world],
        ["inspect", SPEC.world],
        ["inspect", SPEC.world],
        ["logs", SPEC.world],
    ]
    assert [cmd[1:] for cmd in off_path_docker if cmd[1] == "stop"] == [
        ["stop", "-t", _GRACE, SPEC.world]
    ]


def test_stop_staged_reaches_compose_through_the_resolved_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`compose stop` ends a play session; it must not be the one site left behind.

    Its own fake, keyed on `cmd[1:]` rather than `cmd[:3]`, because that is the
    point: the shared `_stop_runner()` above dispatches on a literal `docker`
    at argv[0] and so cannot see this at all.
    """
    monkeypatch.setattr(docker.platform, "_resolved_docker_cli", OFF_PATH_EXE)
    calls: list[list[str]] = []
    live = {SPEC.db, SPEC.auth, SPEC.world}

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        rest = cmd[1:]
        if rest[:3] == ["compose", "config", "--format"]:
            return _completed(stdout='{"name": "ours"}')
        if rest[:2] == ["compose", "stop"]:
            live.clear()
            return _completed()
        if rest[0] == "inspect":
            return _completed(stdout="ours" + chr(10))
        if rest[0] == "ps":
            return _completed(stdout="".join(n + chr(10) for n in sorted(live)))
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, tmp_path) is True
    assert ["compose", "stop"] in [cmd[1:3] for cmd in calls]
    assert all(cmd[0] == OFF_PATH_EXE for cmd in calls), calls


def test_follow_logs_streams_from_the_resolved_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Console tab's log source goes through `runner.stream`, not `runner.run`.

    Which is why it is the easiest of the nine to miss: a search for
    `runner.run(["docker"` does not find it.
    """
    monkeypatch.setattr(docker.platform, "_resolved_docker_cli", OFF_PATH_EXE)
    seen: list[list[str]] = []

    def fake_stream(cmd: list[str], cwd: Path | None = None):
        seen.append(cmd)
        return iter(["a line"])

    monkeypatch.setattr(docker.runner, "stream", fake_stream)
    assert list(docker.follow_logs("ac-worldserver", tail=5)) == ["a line"]
    assert seen == [[OFF_PATH_EXE, "logs", "-f", "--tail", "5", "ac-worldserver"]]


def test_a_host_without_docker_is_told_so_not_shown_a_winerror(
    no_docker: list[list[str]], tmp_path: Path
) -> None:
    """An unresolvable CLI must never reach the UI as `FileNotFoundError`.

    The degrading callers keep the shape they already have — a missing CLI
    arrives as a failed `CompletedProcess`, exactly as a timeout does — and the
    ones that raise carry a sentence the user can act on.
    """
    assert docker.health("ac-worldserver") == "unknown"
    assert docker.container_state("ac-worldserver") == docker.ContainerState()
    assert docker._logs("ac-worldserver") == ""
    assert docker.compose_project_name(Path("/tmp/wow")) is None
    assert docker.container_project("ac-worldserver") == docker.UNREADABLE

    with pytest.raises(docker.DockerCommandError) as raised:
        docker.start(Path("/tmp/wow"))
    assert "Docker could not be found" in str(raised.value)
    assert "Docker Desktop" in str(raised.value)

    with pytest.raises(docker.DockerCommandError, match="Docker could not be found"):
        list(docker.follow_logs("ac-worldserver"))

    # Stop was the entry point this list did not cover, and the one that got it
    # wrong: it degraded to "your install has no COMPOSE_PROJECT_NAME pinned",
    # which blames the install for the absence of Docker (review, 2026-08-23).
    with pytest.raises(docker.DockerCommandError, match="Docker could not be found"):
        docker.stop_staged(SPEC, tmp_path)
    (tmp_path / ".env").write_text("COMPOSE_PROJECT_NAME=wow\n", encoding="utf-8")
    with pytest.raises(docker.DockerCommandError, match="Docker could not be found"):
        docker.stop_staged(SPEC, tmp_path)

    assert no_docker == [], "a command was spawned on a host with no docker binary"


def test_a_stop_with_no_docker_does_not_blame_the_install(
    no_docker: list[list[str]], tmp_path: Path
) -> None:
    """The pin is irrelevant when there is no Docker, so Stop must not raise it.

    Separate from the sweep above because "says the right sentence" and "says
    only the right sentence" are different claims, and it was the second that
    failed: the old message named `COMPOSE_PROJECT_NAME` and the install's own
    folder, sending a user whose machine has no Docker on it to go and edit a
    `.env` file.
    """
    with pytest.raises(docker.DockerCommandError) as raised:
        docker.stop_staged(SPEC, tmp_path)
    said = str(raised.value)
    assert docker.PROJECT_NAME_VAR not in said, said
    assert str(tmp_path) not in said, said
    assert "could not ask Docker what is running" not in said, said


def test_a_docker_uninstalled_mid_run_reads_as_missing_docker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one case the cache cannot follow: the pinned path stops existing.

    `docker_program()` keeps a hit for the life of the process, so uninstalling
    Docker while the launcher is open leaves it aimed at a deleted
    `docker.exe`. `subprocess` reports that as `OSError`, and the user still
    has to get the sentence rather than the errno.
    """
    monkeypatch.setattr(docker.platform, "_resolved_docker_cli", OFF_PATH_EXE)

    def gone(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
        raise FileNotFoundError(2, "The system cannot find the file specified")

    monkeypatch.setattr(docker.runner, "run", gone)
    assert docker.health("ac-worldserver") == "unknown"
    with pytest.raises(docker.DockerCommandError, match="Docker could not be found"):
        docker.start(Path("/tmp/wow"))

    def gone_stream(cmd: list[str], cwd: Path | None = None):
        raise FileNotFoundError(2, "The system cannot find the file specified")
        yield  # pragma: no cover - a generator that only ever raises

    monkeypatch.setattr(docker.runner, "stream", gone_stream)
    with pytest.raises(docker.DockerCommandError, match="Docker could not be found"):
        list(docker.follow_logs("ac-worldserver"))


def test_the_polls_say_why_and_stop_when_this_host_has_no_docker_cli(
    no_docker: list[list[str]], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A missing CLI must not turn a hard, instant failure into a silent spin.

    Before this, `_status_safe()` degraded "there is no Docker on this machine"
    exactly as it degrades a daemon hiccup, so `wait_ready()` polled out its
    full 480s default and emitted nothing above DEBUG the whole time. Measured
    at the time: `wait_ready('a','w','h',1,timeout=1.0,interval=0.1)` returned
    False after 1.00s, 10 polls, zero records at WARNING or above.

    The grace window is shortened here, not removed. Giving up on the first
    miss is the other wrong answer — `docker_program()` deliberately never
    caches one so that Docker arriving mid-run is picked up — so both loops are
    also asserted to have polled more than once.
    """
    monkeypatch.setattr(docker, "_CLI_MISSING_GRACE_SECONDS", 0.2)
    for label, poll in (
        (
            "wait_ready()",
            lambda: docker.wait_ready(
                "a", "w", docker.azerothcore_ready("h", 1, timeout=60.0, interval=0.02)
            ),
        ),
        ("wait_db_healthy()", lambda: docker.wait_db_healthy("db", timeout=60.0, interval=0.02)),
    ):
        caplog.clear()
        with caplog.at_level("DEBUG", logger="yulon.docker"):
            assert poll() is False
        loud = [r.getMessage() for r in caplog.records if r.levelno >= 30]
        assert len(loud) == 2, f"{label}: expected one cause + one give-up, got {loud}"
        assert "Docker could not be found" in loud[0], loud[0]
        assert "Giving up" in loud[1], loud[1]
        assert all(label in line for line in loud), loud
        waited = [r for r in caplog.records if "still no docker CLI" in r.getMessage()]
        assert waited, f"{label}: gave up on the first miss; a mid-run install is now locked out"
    assert no_docker == [], "a command was spawned on a host with no docker binary"


def test_a_readiness_poll_still_rides_out_a_docker_that_only_stumbles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The degradation the missing-CLI change must not have taken away.

    `_status_safe()` turning a failed `docker ps` into None is what keeps a
    daemon restart mid-start from ending the wait. Only `DockerCliMissingError`
    is exempt from that, and this is the test that says so — without it, "stop
    swallowing the missing CLI" could quietly become "stop swallowing anything".
    """
    stumbles = iter([True, False])

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if cmd[1] == "ps":
            if next(stumbles):
                return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
            return _completed(stdout="ac-authserver\nac-worldserver\n")
        if cmd[1] == "inspect":
            return _completed(stdout="running\t2026-08-23T00:00:00Z")
        return _completed(stdout="realm.example:3724 ready...")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    ready = docker.azerothcore_ready("realm.example", 3724, timeout=5.0, interval=0.01)
    assert docker.wait_ready(SPEC.auth, SPEC.world, ready) is True


# ------------------------------------------------- remove_staged (teardown)


def _remove_runner(
    calls: list[list[str]],
    *,
    present: set[str] | None = None,
    running: set[str] | None = None,
    owner: str | None = PROJECT,
    owners: dict[str, str | None] | None = None,
    inspect_fails: bool = False,
    down_removes: bool = True,
    list_fails: bool = False,
    rm_works: bool = True,
):
    """A `runner.run` double for the teardown path.

    `present` is what `docker ps -a --filter label=...` reports (existence);
    `running` is what `docker ps` reports. They are separate because removal is
    about the first and ownership refusals are about the second, and a container
    can be stopped-but-present, which is the state this action exists for.
    """
    live = set() if running is None else set(running)
    exists = set(present if present is not None else live)

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "' + PROJECT + '"}')
        if cmd[:3] == ["docker", "compose", "down"]:
            if down_removes:
                exists.clear()
                live.clear()
            return _completed()
        if cmd[:2] == ["docker", "inspect"]:
            if inspect_fails:
                return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
            who = owners.get(cmd[2], owner) if owners is not None else owner
            return _completed(stdout="" if who is None else who + chr(10))
        if cmd[:3] == ["docker", "ps", "-a"]:
            if list_fails:
                return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
            return _completed(stdout="".join(n + "\n" for n in sorted(exists)))
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:3] == ["docker", "rm", "-f"]:
            if rm_works:
                exists.discard(cmd[3])
            return _completed()
        return _completed()

    return fake_run


def test_remove_staged_never_passes_a_flag_that_would_delete_a_volume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one mistake here is unrecoverable, so it is pinned rather than trusted.

    The database is a named volume (`ac-database:/var/lib/mysql`), so
    `compose down` keeps every character. `compose down -v` deletes them. There
    is no legitimate reason for this argv to grow a `-v`, and a test is cheaper
    than finding out.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _remove_runner(calls, present={SPEC.db, SPEC.world}))
    assert docker.remove_staged(SPEC, Path("/tmp/wow")) is True

    down = [cmd for cmd in calls if cmd[:3] == ["docker", "compose", "down"]]
    assert down, "nothing was taken down"
    assert "--remove-orphans" in down[0], down[0]
    for cmd in calls:
        assert "-v" not in cmd, cmd
        assert "--volumes" not in cmd, cmd


def test_remove_staged_asks_by_project_label_not_by_container_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AzerothCore pins container names globally, so a name search finds the neighbour.

    `container_exists()` answers "is there an ac-worldserver", which is a
    different question from "does THIS install still have containers" whenever
    two installs of the same game exist.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _remove_runner(calls, present={SPEC.db}))
    docker.remove_staged(SPEC, Path("/tmp/wow"))

    listings = [c for c in calls if c[:3] == ["docker", "ps", "-a"]]
    assert listings, "existence was never asked about"
    for cmd in listings:
        assert any(a.startswith("label=com.docker.compose.project=") for a in cmd), cmd


def test_remove_staged_says_false_when_this_install_has_no_containers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compose down` exits 0 for a project that never existed; that is not a removal."""
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _remove_runner(calls, present=set()))
    assert docker.remove_staged(SPEC, Path("/tmp/wow")) is False
    assert not any(c[:3] == ["docker", "compose", "down"] for c in calls), "nothing to take down"


def test_remove_staged_will_not_remove_containers_it_cannot_prove_are_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same refusal as the stop path, so the two cannot disagree about ownership."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _remove_runner(calls, running={SPEC.world}, owner="somebody-elses-server"),
    )
    with pytest.raises(docker.DockerCommandError, match="another install"):
        docker.remove_staged(SPEC, Path("/tmp/wow"))
    assert not any(c[:3] == ["docker", "compose", "down"] for c in calls)


def test_remove_staged_gives_up_rather_than_guessing_when_ownership_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Docker refusing to answer is not the same as "not ours", and must not read as it."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner, "run", _remove_runner(calls, running={SPEC.world}, inspect_fails=True)
    )
    with pytest.raises(docker.DockerCommandError, match="cannot prove"):
        docker.remove_staged(SPEC, Path("/tmp/wow"))
    assert not any(c[:3] == ["docker", "compose", "down"] for c in calls)


def test_remove_staged_finishes_the_job_when_compose_down_removed_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The moved-folder case: compose exits 0 having matched no container.

    The by-name removal that follows is only reached for names the census has
    already proved carry this project's label.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _remove_runner(calls, present={SPEC.db, SPEC.world}, down_removes=False),
    )
    assert docker.remove_staged(SPEC, Path("/tmp/wow")) is True
    removed = {c[3] for c in calls if c[:3] == ["docker", "rm", "-f"]}
    assert removed == {SPEC.db, SPEC.world}


def test_remove_staged_refuses_to_report_success_while_containers_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reporting a teardown that did not happen is how a stale install gets reused."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _remove_runner(calls, present={SPEC.db}, down_removes=False, rm_works=False),
    )
    with pytest.raises(docker.DockerCommandError, match="still present"):
        docker.remove_staged(SPEC, Path("/tmp/wow"))


def test_remove_staged_will_not_claim_success_when_docker_stops_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unanswerable "what is left" is not an empty one."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner, "run", _remove_runner(calls, present={SPEC.db}, list_fails=True)
    )
    with pytest.raises(docker.DockerCommandError):
        docker.remove_staged(SPEC, Path("/tmp/wow"))


# ------------------------------------------- repair_import (the re-import)


def _probe(*answers: docker.ImportState) -> Callable[[], docker.ImportState]:
    """A probe that gives each answer in turn, repeating the last one forever.

    Repeating matters: `repair_import()` asks before and again after, and a test
    that supplied one answer would otherwise fail on the post-check for reasons
    that have nothing to do with what it is testing.
    """
    remaining = list(answers)

    def probe() -> docker.ImportState:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return probe


UNIMPORTED = docker.ImportState("absent", "acore_world holds no tables")
IMPORTED = docker.ImportState("imported", "acore_world has 1103 tables")
HAS_ROWS = docker.ImportState("populated", "651 rows in acore_auth.account")


def _repair_doubles(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[list[str]],
    *,
    running: set[str] | None = None,
    owner: str | None = PROJECT,
    inspect_fails: bool = False,
    health: str = "healthy",
    import_exit: int = 0,
    import_output: Callable[[], Iterable[str]] = tuple,
    cwds: list[Path | None] | None = None,
) -> None:
    """Fake BOTH `runner.run` and `runner.stream` for the repair path.

    Two doubles rather than one because the import is the only command here that
    streams: `repair_import()` reads it through `run_attached()`, so that it can
    show its output while it runs, and everything else it asks Docker still goes
    through `runner.run`. A test that patched only `run` would leave the import
    talking to a real `docker` binary.

    `running` is what `docker ps` reports before anything is done; a
    `compose up -d --no-deps <db>` adds the database to it, which is how a test
    can tell whether the database was started rather than merely demanded.
    `import_output` is called for each run of the import and its lines are
    yielded one at a time, so a test can prove they arrive rather than land in
    one block at the end.
    """
    live = set() if running is None else set(running)

    def fake_run(cmd: list[str], cwd=None, timeout: float | None = None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "' + PROJECT + '"}')
        if cmd[:5] == ["docker", "compose", "up", "-d", "--no-deps"]:
            live.update(cmd[5:])
            return _completed()
        if cmd[:2] == ["docker", "inspect"]:
            if "Health" in cmd[-1]:
                return _completed(stdout=health)
            if inspect_fails:
                return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
            return _completed(stdout="" if owner is None else owner + chr(10))
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        return _completed()

    def fake_stream(
        cmd: list[str], cwd: Path | None = None, *, merge_stderr: bool = False
    ) -> Iterator[str]:
        calls.append(cmd)
        if cwds is not None:
            cwds.append(cwd)
        yield from import_output()
        if import_exit:
            # What `runner.stream()` does at the end of a non-zero run, and the
            # reason `run_attached()` catches rather than propagates it.
            raise subprocess.CalledProcessError(import_exit, cmd)

    monkeypatch.setattr(docker.runner, "run", fake_run)
    monkeypatch.setattr(docker.runner, "stream", fake_stream)


def test_repair_import_names_only_the_one_shot_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the action is the services it does NOT select.

    `docker compose up -d` with no arguments re-runs the import and everything
    else, and `start_staged()` names three services precisely so an ordinary
    start can never reach `ac-db-import`. This is the one caller allowed to
    reach it, so it must reach nothing else: the import command names one
    service, and `--no-deps` is what stops compose adding a second.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    assert docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED)) is True

    ups = [c for c in calls if c[:3] == ["docker", "compose", "up"]]
    assert ups == [["docker", "compose", "up", "--no-deps", "ac-db-import"]], ups
    for cmd in calls:
        assert SPEC.world not in cmd, cmd
        assert SPEC.auth not in cmd, cmd


def test_repair_import_starts_the_database_it_needs_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stop takes the database down with everything else, so repair has to bring it back.

    Without this the action is unreachable through the buttons this app has: it
    refuses while the servers are running, and the only way to stop them also
    stops the database it must ask and write to.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running=set())
    assert docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED)) is True

    started = [c for c in calls if c[:5] == ["docker", "compose", "up", "-d", "--no-deps"]]
    assert started == [["docker", "compose", "up", "-d", "--no-deps", SPEC.db]], started


def test_repair_import_does_not_restart_a_database_that_is_already_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A running database is already the state this needs; recreating one is not free."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED))
    assert not any(c[:4] == ["docker", "compose", "up", "-d"] for c in calls)


def test_repair_import_refuses_over_a_database_with_player_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal this whole action is built around, and it is not offered twice.

    Re-importing over a populated database destroys characters, so a probe that
    finds accounts ends the action — with the way back (Restore) named, because
    that path exists and was live-gated against a real backup.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    with pytest.raises(docker.DockerCommandError, match="restore the last backup") as raised:
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(HAS_ROWS))
    assert "651 rows in acore_auth.account" in str(raised.value)
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls), "it imported anyway"


def test_repair_import_refuses_while_this_installs_servers_are_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live worldserver holds characters in memory and writes them back over the import."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db, SPEC.world})
    with pytest.raises(docker.DockerCommandError, match="Press Stop first"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED))
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls)


def test_repair_import_will_not_run_against_containers_it_cannot_prove_are_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same ownership census as the stop and teardown paths, so the three agree."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db}, owner="somebody-elses-server")
    with pytest.raises(docker.DockerCommandError, match="another install"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED))
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls)

    calls.clear()
    _repair_doubles(monkeypatch, calls, running={SPEC.db}, inspect_fails=True)
    with pytest.raises(docker.DockerCommandError, match="cannot prove"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED))
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls)


def test_repair_import_catches_an_import_that_exited_zero_having_done_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The exit code is not the answer, exactly as it is not for `compose down`.

    A one-shot that died part-way and one that never touched a table look
    identical from outside, so the database is asked again instead, and only a
    database that now reads as imported counts as a repair.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    with pytest.raises(docker.DockerCommandError, match="still read as absent") as raised:
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED))
    assert "ac-db-import" in str(raised.value), "did not say which logs to read"
    assert ["docker", "compose", "up", "--no-deps", "ac-db-import"] in calls


def test_repair_import_accepts_an_import_that_seeded_its_own_accounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A finished import can leave player data behind, and that is a repair, not a failure.

    The live gate on yulon-ubuntu (2026-08-23) ran a first-ever import against
    an empty volume on an install carrying mod-city-bots. It finished exit 0
    with every schema full — and with 400 accounts and 400 characters the
    module's own `db-auth`/`db-characters` update files had written. Demanding
    `imported` from the second probe therefore failed the action over its own
    success, and would have failed it on every install this project ships,
    since the shipped ones carry modules.

    Safe only because of the order this asserts around: `populated` is a refusal
    *before* the one-shot runs, so a database populated afterwards was populated
    by the run that just happened.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    seeded = docker.ImportState(
        "populated",
        "400 rows in acore_auth.account, 400 rows in acore_characters.characters",
        complete=True,
    )
    assert docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, seeded)) is True
    assert ["docker", "compose", "up", "--no-deps", "ac-db-import"] in calls


def test_repair_import_refuses_a_database_it_could_not_ask(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unanswerable database is not an empty one — the fail-closed rule again."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    unreadable = docker.ImportState("unreadable", "mysql: connection refused")
    with pytest.raises(docker.DockerCommandError, match="could not be asked"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(unreadable))
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls)


def test_repair_import_declines_an_install_that_is_already_imported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nothing to repair is a refusal, not a no-op: the import would overwrite a good database."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    with pytest.raises(docker.DockerCommandError, match="already completed"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(IMPORTED))
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls)


def test_repair_import_refuses_a_game_that_never_named_an_import_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A guessed service name is a guess about which container gets run."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    spec = docker.ContainerSpec(db="a-db", auth="a-auth", world="a-world", ports=(1,))
    with pytest.raises(docker.DockerCommandError, match="does not say which compose service"):
        docker.repair_import(spec, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED))
    assert calls == [], "something was asked of Docker before the refusal"


def test_repair_import_says_no_when_the_database_never_becomes_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Importing into a database that has not come up writes into nothing."""
    # The poll always sleeps once after its final check; without this the test
    # spends the default two-second interval proving nothing.
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running=set(), health="starting")
    with pytest.raises(docker.DockerCommandError, match="did not report healthy"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED), db_timeout=0.05)
    assert not any(c[:4] == ["docker", "compose", "up", "--no-deps"] for c in calls)


def test_repair_import_refuses_an_install_that_cannot_name_its_project(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With no identity there is no telling whose database would be overwritten."""
    calls: list[list[str]] = []

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:3] == ["docker", "compose", "config"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    with pytest.raises(docker.DockerCommandError, match="which compose project"):
        docker.repair_import(SPEC, tmp_path, _probe(UNIMPORTED, IMPORTED))
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls)


# ---------------------------------- the import says what it is doing while it runs


def _import_command(calls: list[list[str]]) -> list[list[str]]:
    """Every `compose up` that was not the `-d` one that starts the database."""
    return [c for c in calls if c[:3] == ["docker", "compose", "up"] and "-d" not in c]


def test_showing_the_import_did_not_change_the_command_it_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The argv is what a live AzerothCore import was gated against; only the reading moved.

    Both spellings of the call have to produce the same command, because the
    sink is optional and the evidence is about `compose up --no-deps
    ac-db-import` and nothing else — including that it still runs in the
    install's own folder, which is what makes it that install's compose project.
    """
    server_dir = Path("/tmp/wow")
    with_sink: list[list[str]] = []
    cwds: list[Path | None] = []
    _repair_doubles(monkeypatch, with_sink, running={SPEC.db}, cwds=cwds)
    docker.repair_import(SPEC, server_dir, _probe(UNIMPORTED, IMPORTED), output=lambda _line: None)

    without_sink: list[list[str]] = []
    _repair_doubles(monkeypatch, without_sink, running={SPEC.db})
    docker.repair_import(SPEC, server_dir, _probe(UNIMPORTED, IMPORTED))

    expected = [["docker", "compose", "up", "--no-deps", "ac-db-import"]]
    assert _import_command(with_sink) == expected
    assert _import_command(without_sink) == expected
    assert cwds == [server_dir], "the import ran somewhere other than the install"


def test_the_imports_lines_reach_the_sink_as_it_prints_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live, not in one block at the end — the block at the end is the thing being replaced.

    The generator records what the sink has already been given each time it is
    resumed, which is the only way to tell "streamed" from "buffered and handed
    over at the end" after the fact: a buffered implementation leaves every
    snapshot empty and still ends with the same list.
    """
    seen: list[str] = []
    snapshots: list[list[str]] = []
    printed = ("applying acore_auth", "applying acore_characters", "applying acore_world")

    def printing() -> Iterator[str]:
        for line in printed:
            yield line
            snapshots.append(list(seen))

    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db}, import_output=printing)
    assert (
        docker.repair_import(
            SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED), output=seen.append
        )
        is True
    )
    assert seen == list(printed), "the sink did not see every line, in order"
    assert snapshots == [list(printed[:1]), list(printed[:2]), list(printed)], snapshots


def test_an_import_that_exited_non_zero_is_logged_and_the_database_asked_anyway(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Streaming must not turn the exit code into an exception.

    `runner.stream()` raises `CalledProcessError` on a non-zero exit, and this
    is the one caller that must not let it: a one-shot that failed part-way and
    one that failed having done nothing exit alike, so the post-import probe is
    the only thing that can tell them apart and it has to run either way.
    """
    calls: list[list[str]] = []
    _repair_doubles(
        monkeypatch,
        calls,
        running={SPEC.db},
        import_exit=1,
        import_output=lambda: ("ERROR 1045 (28000): Access denied for user 'root'",),
    )
    asked: list[int] = []

    def probe() -> docker.ImportState:
        asked.append(1)
        return UNIMPORTED if len(asked) == 1 else IMPORTED

    with caplog.at_level("DEBUG", logger="yulon.docker"):
        assert docker.repair_import(SPEC, Path("/tmp/wow"), probe) is True
    assert len(asked) == 2, "the post-import probe never ran"
    said = [r.getMessage() for r in caplog.records if r.levelno >= 30]
    assert any("exited 1" in m and "Access denied" in m for m in said), said


def test_the_failure_text_of_a_broken_import_reaches_the_error_on_screen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stream()` yields stderr in one block at the END, so the tail IS the explanation.

    Without it the user gets "the databases still read as absent" and is sent to
    a `docker compose logs` command to find out why — which is a fair pointer
    and a poor answer when the reason was on screen a second ago.
    """
    calls: list[list[str]] = []
    _repair_doubles(
        monkeypatch,
        calls,
        running={SPEC.db},
        import_exit=1,
        import_output=lambda: (
            "applying acore_auth",
            "ERROR 1698 (28000): Access denied for user 'root'@'localhost'",
        ),
    )
    with pytest.raises(docker.DockerCommandError) as raised:
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED))
    said = str(raised.value)
    assert "ERROR 1698" in said, said
    assert "still read as absent" in said, "the state it is in stopped being said"


def test_a_thirty_minute_import_is_not_kept_in_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The sink sees everything; this process remembers a bounded end of it.

    A full import prints a line per SQL file for 10-30 minutes, and this runs
    inside a window that may then stay open for days. Keeping all of it would
    replace one defect with another.
    """
    lines = [f"line {n}" for n in range(10_000)]
    seen: list[str] = []

    def fake_stream(
        cmd: list[str], cwd: Path | None = None, *, merge_stderr: bool = False
    ) -> Iterator[str]:
        # A generator, not `iter(list)`: `run_attached()` closes what it is
        # given so an early exit terminates the child, and a double that cannot
        # be closed is not the thing being replaced.
        yield from lines

    monkeypatch.setattr(docker.runner, "stream", fake_stream)
    run = docker.run_attached(
        ["compose", "up", "--no-deps", "ac-db-import"],
        Path("/tmp/wow"),
        sink=seen.append,
        keep=3,
    )
    assert seen == lines, "the sink was denied lines it was there to receive"
    assert run.tail == ("line 9997", "line 9998", "line 9999")
    assert run.returncode == 0

    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db}, import_output=lambda: iter(lines))
    with pytest.raises(docker.DockerCommandError) as raised:
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED))
    said = str(raised.value)
    assert "line 9999" in said and "line 0 " not in said, said
    assert len(said) < 1000, f"a {len(said)}-character message for a QLabel"


def test_a_sink_that_has_gone_away_cannot_kill_a_running_import(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A closed window must not stop a database import half-written.

    Letting the sink's exception out abandons `stream()`'s generator, and
    `stream()` terminates the child when that happens — so a `RuntimeError` from
    a deleted widget would come out as an `ac-db-import` killed mid-write.
    """
    calls: list[list[str]] = []
    _repair_doubles(
        monkeypatch,
        calls,
        running={SPEC.db},
        import_output=lambda: ("applying acore_auth", "applying acore_world", "done"),
    )

    def deleted(_line: str) -> None:
        raise RuntimeError("Internal C++ object (LineRelay) already deleted.")

    with caplog.at_level("DEBUG", logger="yulon.docker"):
        assert (
            docker.repair_import(
                SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED), output=deleted
            )
            is True
        )
    complaints = [r for r in caplog.records if "output sink" in r.getMessage()]
    assert len(complaints) == 1, "one dead sink was complained about once per line"


# ------------------------------------------- what the review of 2026-08-23 found


def test_no_stop_path_uses_a_flag_spelling_docker_only_learned_in_28(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--timeout` is a Docker CLI 28.0.0 spelling; `-t` has always worked.

    Through 27.x the long form of this flag is `--time`, so `docker stop
    --timeout 300 x` exits 125 with `unknown flag` on any older CLI — turning
    the by-name fallback, which exists for installs that can least afford to
    fail, into a hard error. The short form means the same thing on every
    version this project can meet, so it is the only one that is safe to send.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner, "run", _stop_runner(calls, running={SPEC.db, SPEC.auth, SPEC.world})
    )
    docker.stop_staged(SPEC, Path("/tmp/wow"))
    monkeypatch.setattr(docker.runner, "run", _remove_runner(calls, present={SPEC.db}))
    docker.remove_staged(SPEC, Path("/tmp/wow"))

    graced = [cmd for cmd in calls if _GRACE in cmd]
    assert graced, "no command carried the grace at all"
    for cmd in graced:
        assert "-t" in cmd, cmd
        assert "--timeout" not in cmd, cmd
        assert "--time" not in cmd, cmd


def test_the_teardown_gives_a_populated_server_the_same_grace_a_stop_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remove is offered on a RUNNING server, under copy promising no data loss.

    `compose down` at Docker's 10s default SIGKILLs a populated worldserver
    mid-drain — measured — so the button whose armed text says the characters
    are kept was the one path still able to lose them.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _remove_runner(calls, present={SPEC.db, SPEC.world}))
    assert docker.remove_staged(SPEC, Path("/tmp/wow")) is True

    down = [cmd for cmd in calls if cmd[:3] == ["docker", "compose", "down"]]
    assert down == [["docker", "compose", "down", "-t", _GRACE, "--remove-orphans"]], down


def test_the_teardowns_by_name_fallback_stops_before_it_removes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker rm -f` is a SIGKILL with no grace at all.

    Putting the grace on `compose down` alone left a hard-kill path reachable
    from the same button, in exactly the case that reaches it: a moved install
    whose compose files no longer match, which is not a reason to lose a save
    queue.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _remove_runner(calls, present={SPEC.db, SPEC.world}, down_removes=False),
    )
    assert docker.remove_staged(SPEC, Path("/tmp/wow")) is True

    order = [
        cmd for cmd in calls if cmd[:2] == ["docker", "stop"] or cmd[:3] == ["docker", "rm", "-f"]
    ]
    for name in (SPEC.db, SPEC.world):
        stopped = order.index(["docker", "stop", "-t", _GRACE, name])
        removed = order.index(["docker", "rm", "-f", name])
        assert stopped < removed, f"{name} was removed before it was stopped: {order}"


def test_repair_starts_a_compose_service_not_a_container_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compose up` takes SERVICE names; `spec.db` is a CONTAINER name.

    They happen to be equal for AzerothCore, which is why reaching past
    `compose_services()` looked harmless. `ContainerSpec` exists so a game whose
    compose file names its services differently can say so, and this is the one
    call that would have silently ignored it.
    """
    spec = docker.ContainerSpec(
        db="pinned-db-container",
        auth="pinned-auth-container",
        world="pinned-world-container",
        ports=(3724, 8085),
        services=("db-service", "auth-service", "world-service"),
        import_service="import-service",
    )
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running=set())
    assert docker.repair_import(spec, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED)) is True

    started = [c for c in calls if c[:5] == ["docker", "compose", "up", "-d", "--no-deps"]]
    assert started == [["docker", "compose", "up", "-d", "--no-deps", "db-service"]], started


def test_repair_asks_the_database_only_after_it_has_started_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The order is the whole justification for starting the database at all.

    Both the probe and the import need a running database, so a probe asked
    first would answer `unreadable` for the very install this action exists for
    and refuse it. Nothing pinned that ordering, so moving the two lines was
    free.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running=set())

    asked_after: list[bool] = []

    def probe() -> docker.ImportState:
        asked_after.append(
            any(c[:5] == ["docker", "compose", "up", "-d", "--no-deps"] for c in calls)
        )
        return UNIMPORTED if len(asked_after) == 1 else IMPORTED

    assert docker.repair_import(SPEC, Path("/tmp/wow"), probe) is True
    assert asked_after[0] is True, "the database was probed before it was started"


def test_repair_import_refuses_an_import_that_seeded_rows_and_then_died(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The hole the widened post-check opened, closed.

    Accepting `populated` was right — a finished import really does leave rows,
    because every module's `db-auth` updates run in the same one-shot — but the
    probe answers `populated` on the FIRST row it finds, before it has looked at
    whether the schemas are finished. So an import that applies mod-city-bots'
    400 accounts and then dies on the world schema reads exactly like a finished
    one.

    Reported as success, that hides the Repair button (the probe no longer says
    `repairable`) and leaves the user a broken server, a success message, and no
    way back. `complete` is what separates the two (review, 2026-08-23).
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db}, import_exit=1)
    half_done = docker.ImportState(
        "populated",
        "400 rows in acore_auth.account, but acore_world holds no tables",
        complete=False,
    )
    with pytest.raises(docker.DockerCommandError, match="did not finish"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, half_done))


def test_a_probe_that_forgets_to_say_complete_cannot_claim_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`complete` defaults False so completeness is never claimed by omission.

    `ImportProbe` is a seam any per-game module implements. One written against
    the old two-field `ImportState` would answer `populated` with no third
    argument, and a default of True would silently hand it the success path.
    """
    assert docker.ImportState("populated", "some rows").complete is False
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    with pytest.raises(docker.DockerCommandError, match="did not finish"):
        docker.repair_import(
            SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, docker.ImportState("populated", "rows"))
        )


def test_repair_import_refuses_a_half_written_schema_and_says_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-running the one-shot here is destructive, not useless, and that is the point.

    Measured on yulon-ubuntu, 2026-08-23. An import killed 19 seconds in left
    `acore_world` with 3 tables of 316. Re-running `ac-db-import` over it took
    it to 5 tables and **2671 rows in `acore_world.updates`** — AzerothCore
    skips the base data for a database that already exists, then records every
    remaining SQL file as applied. The schema is unimportable from that moment
    on: no later run will ever apply those files.

    So the refusal is not tidiness. It is the difference between a user who can
    still delete a volume and reinstall, and one who cannot.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    half_written = docker.ImportState(
        "partial", "acore_world has 3 tables but no import record, so it was never finished"
    )
    with pytest.raises(docker.DockerCommandError) as caught:
        # No `reset`: nothing can hand the importer an empty schema, so there is
        # nothing safe to do.
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(half_written))

    said = str(caught.value)
    assert "cannot finish the job" in said, said
    assert "install again" in said, "no way out was offered"
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls), "the import was run"


def test_repair_import_clears_the_half_written_schemas_before_re_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one thing that makes a `partial` repair work, and the order it must happen in.

    An empty schema is the only input AzerothCore's importer treats as work to
    do. Re-running over a schema that already exists leaves it permanently
    unimportable, so the drop has to happen BEFORE the one-shot, not as a
    cleanup afterwards.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    ran_import_before_the_clear: list[bool] = []

    def reset() -> tuple[str, ...]:
        ran_import_before_the_clear.append(
            any(c[:4] == ["docker", "compose", "up", "--no-deps"] for c in calls)
        )
        return ("acore_world",)

    half = docker.ImportState("partial", "acore_world has 3 tables but no import record")
    assert docker.repair_import(SPEC, Path("/tmp/wow"), _probe(half, IMPORTED), reset=reset) is True

    assert ran_import_before_the_clear == [False], "the import ran before the clear"
    assert any(c[:4] == ["docker", "compose", "up", "--no-deps"] for c in calls), "no import ran"


def test_repair_import_will_not_run_the_import_if_the_clear_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed drop leaves the schema exactly as unimportable as before.

    Running the one-shot anyway is the specific mistake that made this state
    unrecoverable in the first place, so a `reset` that raises stops everything.
    The seam belongs to the per-game package and may raise its own types, so
    this is caught broadly and re-raised as the error the caller contracted for.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})

    def reset() -> tuple[str, ...]:
        raise RuntimeError("mysql said no")

    half = docker.ImportState("partial", "acore_world has 3 tables but no import record")
    with pytest.raises(docker.DockerCommandError, match="could not be cleared"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(half), reset=reset)
    assert not any(c[:4] == ["docker", "compose", "up", "--no-deps"] for c in calls)


def test_repair_import_will_not_run_the_import_if_the_clear_found_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ "Unfinished" and "nothing to clear" cannot both be true, so something is wrong.

    Most likely the probe and the reset disagree about what finished means. The
    one thing that must not follow is the one-shot running over the schemas the
    probe just called half-written.
    """
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    half = docker.ImportState("partial", "acore_world has 3 tables but no import record")
    with pytest.raises(docker.DockerCommandError, match="nothing was found to clear"):
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(half), reset=lambda: ())
    assert not any(c[:4] == ["docker", "compose", "up", "--no-deps"] for c in calls)


def test_an_absent_database_is_never_cleared(monkeypatch: pytest.MonkeyPatch) -> None:
    """There is nothing to drop, and dropping is the one thing here that destroys."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    cleared: list[int] = []

    def reset() -> tuple[str, ...]:
        cleared.append(1)
        return ("acore_world",)

    assert (
        docker.repair_import(SPEC, Path("/tmp/wow"), _probe(UNIMPORTED, IMPORTED), reset=reset)
        is True
    )
    assert cleared == [], "an absent database was dropped"


# ------------------------------------------------- the build (roadmap 6.2)


def _stream_double(
    monkeypatch: pytest.MonkeyPatch, lines: Iterable[str]
) -> tuple[list[list[str]], list[bool]]:
    """Record what `run_attached()` asks `runner.stream()` to run, and how."""
    seen: list[list[str]] = []
    merged: list[bool] = []

    def fake_stream(
        cmd: list[str], cwd: Path | None = None, *, merge_stderr: bool = False
    ) -> Iterator[str]:
        seen.append(cmd)
        merged.append(merge_stderr)
        yield from lines

    monkeypatch.setattr(docker.runner, "stream", fake_stream)
    return seen, merged


def test_build_staged_passes_all_three_compose_files_and_plain_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trap: a bare `docker compose build` here builds NOTHING and exits 0.

    The `build:` blocks live in a file compose never auto-loads, and naming any
    `-f` disables auto-loading — so the base and the override have to be listed
    too, or the build loses the image tags and env it is meant to produce.
    """
    seen, merged = _stream_double(monkeypatch, ["#1 [internal] load build definition"])
    run = docker.build_staged(
        Path("/tmp/wow"),
        ("docker-compose.yml", "docker-compose.override.yml", "docker-compose.build.yml"),
    )
    assert seen == [
        [
            "docker",
            "compose",
            "-f",
            "docker-compose.yml",
            "-f",
            "docker-compose.override.yml",
            "-f",
            "docker-compose.build.yml",
            "build",
            "--progress",
            "plain",
        ]
    ]
    # BuildKit writes ALL of its progress to stderr, which `stream()` otherwise
    # withholds until the child exits — a blank log panel for the whole build.
    assert merged == [True]
    assert run.returncode == 0


def test_run_one_shot_keeps_the_argv_that_was_live_gated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--no-deps` is what makes an attached `up` terminate. Byte-identical since the gate."""
    seen, merged = _stream_double(monkeypatch, ["importing"])
    docker.run_one_shot("ac-db-import", Path("/tmp/wow"))
    assert seen == [["docker", "compose", "up", "--no-deps", "ac-db-import"]]
    assert merged == [False]


def test_a_cancelled_run_is_not_reported_as_a_failed_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stop and a failed build are different events; the exit code has to say which."""
    _stream_double(monkeypatch, ["#5 2.1 building", "#5 4.0 still building"])
    cancel = docker.threading.Event()
    cancel.set()
    run = docker.run_attached(["compose", "build"], Path("/tmp/wow"), cancel=cancel)
    assert run.returncode == docker.CANCELLED_RETURNCODE
    assert run.returncode not in (0, 1)  # never mistakable for a real exit status


REFS = ("yulon.local/ac-wotlk-worldserver:native-abc", "yulon.local/ac-wotlk-authserver:native-abc")


def test_images_built_says_unknown_rather_than_no_when_docker_will_not_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`None` is not `False`: a resume must not conclude "nothing is built" from silence.

    The two non-zero cases are not the same event and the difference is hours:
    "no such image" is an answer, and a daemon that will not talk is not.
    """
    monkeypatch.setattr(
        docker.runner, "run", lambda *a, **k: _completed(returncode=1, stderr="permission denied")
    )
    assert docker.images_built(REFS) is None
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda *a, **k: _completed(returncode=1, stderr="Error: No such image: x"),
    )
    assert docker.images_built(REFS) is False
    monkeypatch.setattr(docker.runner, "run", lambda *a, **k: _completed(stdout="sha256:abc"))
    assert docker.images_built(REFS) is True
    assert docker.images_built(()) is None


def test_images_built_asks_the_daemon_by_reference_not_compose_by_project(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compose cannot answer "has this been built?", so it is no longer asked.

    Measured on yulon-ubuntu (Docker 29.1.3, Compose 2.40.3, 2026-08-24): after
    a successful `compose -f base -f override -f build build`, `compose images
    -q` returned nothing — both bare and with the same `-f` set — and only
    began answering once containers existed (`compose create` was enough; `up`
    was not needed). Compose enumerates the images of a project's CREATED
    CONTAINERS. That window is exactly the one a resume asks in, so every
    resume re-ran the compile.
    """
    seen: list[list[str]] = []

    def record(argv: list[str], **_kwargs: object) -> object:
        seen.append(argv)
        return _completed(stdout="sha256:abc")

    monkeypatch.setattr(docker.runner, "run", record)
    assert docker.images_built(REFS) is True
    assert [a[1:] for a in seen] == [
        ["image", "inspect", "--format", "{{.Id}}", REFS[0]],
        ["image", "inspect", "--format", "{{.Id}}", REFS[1]],
    ]
    assert not [a for a in seen if "compose" in a]


def test_a_partial_build_is_not_a_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Three images of four is not "built" — starting on it means a missing binary."""
    answers = iter(
        [_completed(stdout="sha256:abc"), _completed(returncode=1, stderr="No such image")]
    )
    monkeypatch.setattr(docker.runner, "run", lambda *a, **k: next(answers))
    assert docker.images_built(REFS) is False


def test_the_bind_mount_probe_mounts_the_folder_and_tells_no_from_no_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A wedged daemon must not be reported as "your folder is not shared with Docker"."""
    seen: list[list[str]] = []

    def answer(
        returncode: int, stdout: str = "", stderr: str = ""
    ) -> Callable[..., subprocess.CompletedProcess[str]]:
        def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            seen.append(argv)
            return _completed(returncode=returncode, stdout=stdout, stderr=stderr)

        return run

    (tmp_path / "already-here.txt").write_text("x", encoding="utf-8")
    server_dir = tmp_path / "wow"  # the folder the user picked; not created yet
    monkeypatch.setattr(docker.runner, "run", answer(0, "already-here.txt\n"))
    # The SELinux seam is answered explicitly, not left to the host: this asserts
    # the argv exactly, and the suite runs on boxes whose real answer differs.
    assert docker.bind_mount_ok(server_dir, "alpine/git", selinux_enforcing=lambda: False) is True
    # The mount source is the nearest ancestor that HAS something in it, not the
    # chosen folder: `-v <missing>:/probe` makes Docker create the directory,
    # and an empty directory's listing proves nothing either way.
    assert seen[-1] == [
        "docker",
        "run",
        "--rm",
        # LOAD-BEARING, and its absence is the defect this test failed to catch
        # for as long as it existed. The probe image is the pinned `alpine/git`
        # — deliberately, so the probe pulls the digest the clone stages pull
        # rather than a second image — and that image's ENTRYPOINT is `git`. So
        # `<image> ls -A /probe` ran `git ls -A /probe`, exited 1 with "'ls' is
        # not a git command", and `bind_mount_ok()` read that as "Docker cannot
        # see this folder". Preflight refused EVERY native install on EVERY
        # platform, and this assertion pinned the broken argv while a
        # monkeypatched runner returned a canned success that could never know
        # the image had an entrypoint. Found live, not here (2026-08-24).
        "--entrypoint",
        "ls",
        "-v",
        # Read-only: that ancestor is routinely the user's home directory, and
        # listing it is all this asks.
        f"{tmp_path}:/probe:ro",
        "alpine/git",
        "-A",
        "/probe",
    ]
    assert not server_dir.exists()

    def refuse_the_mount(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        # The image is in hand — `docker run` pulls before it mounts — so this
        # non-zero exit really is Docker answering about the folder. Without
        # that half the fake, a failure the probe never reached the mount for
        # would be indistinguishable from this one; see
        # `test_a_probe_that_never_reached_the_mount_is_unchecked_not_a_refusal`.
        if argv[1:3] == ["image", "inspect"]:
            return _completed(stdout="sha256:abc")
        return _completed(returncode=1, stderr="invalid mount config")

    monkeypatch.setattr(docker.runner, "run", refuse_the_mount)
    assert docker.bind_mount_ok(server_dir, "alpine/git") is False
    # 124 is what `runner.run()` reports for a command that never answered.
    monkeypatch.setattr(docker.runner, "run", answer(124, stderr="timed out after 30.0s"))
    assert docker.bind_mount_ok(server_dir, "alpine/git") is None


def _probe_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, enforcing: bool | None
) -> list[str]:
    """The argv the bind probe actually runs, with SELinux answering `enforcing`."""
    (tmp_path / "already-here.txt").write_text("x", encoding="utf-8")
    seen: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.append(argv)
        return _completed(stdout="already-here.txt\n")

    monkeypatch.setattr(docker.runner, "run", run)
    assert (
        docker.bind_mount_ok(tmp_path / "wow", "alpine/git", selinux_enforcing=lambda: enforcing)
        is True
    )
    return seen[-1]


def test_the_bind_probe_runs_unconfined_when_selinux_is_enforcing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Every install on an enforcing box was refused here, and this is why.

    Measured on a clean Fedora 44 box (2026-08-30). The probe mounts the nearest
    POPULATED ancestor of the chosen folder, which is routinely `$HOME`; `$HOME`
    is `user_home_dir_t`; a confined container may read `container_file_t` and
    nothing else, so `ls` answered `can't open '/probe': Permission denied` with
    an empty listing and a non-zero exit — exactly the shape `bind_mount_ok()`
    reads as "Docker cannot see that folder". Preflight then refused the install
    one line after printing `[pass] SELinux`, and sent a Fedora user to a Docker
    Desktop settings pane that does not exist on Docker Engine.
    """
    argv = _probe_argv(monkeypatch, tmp_path, enforcing=True)
    assert argv[:4] == ["docker", "run", "--rm", "--security-opt"]
    assert argv[4] == "label:disable"
    # `:z` is NOT the alternative and must never appear here: the mount source is
    # an ancestor of the chosen folder, routinely the user's home directory, and
    # `:z` would recursively relabel that whole tree to `container_file_t` for
    # the sake of one read-only `ls`.
    assert f"{tmp_path}:/probe:ro" in argv
    assert not [a for a in argv if a.endswith(":z") or a.endswith(":Z")]


def test_the_bind_probe_stays_confined_when_selinux_is_not_enforcing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An Ubuntu or Windows box gets no `label:disable`: there is nothing to disable."""
    assert "label:disable" not in _probe_argv(monkeypatch, tmp_path, enforcing=False)


def test_a_selinux_answer_that_could_not_be_read_does_not_disable_labels(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`selinux_enforcing()` has THREE answers, and `None` is not a quiet "yes".

    None means the question went unanswered — no `getenforce`, an unreadable
    `/sys/fs/selinux/enforce`, a tool that said something new. Turning the
    container's confinement off on a machine that never claimed to be enforcing
    is a security decision taken on no evidence, and folding "could not ask"
    into an answer is the mistake this project keeps having to unmake.
    """
    assert "label:disable" not in _probe_argv(monkeypatch, tmp_path, enforcing=None)


def test_a_probe_that_never_reached_the_mount_is_unchecked_not_a_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`docker run` also fails BEFORE it mounts anything, and that is not an answer.

    Reported from a Mac (2026-08-26), where every failure mode below was the
    same one: the app found `docker` inside Docker Desktop's bundle but ran it
    with launchd's PATH, so the CLI could not exec `docker-credential-desktop`,
    so the probe's first-ever pull of `alpine/git` died at authentication:

        docker: error getting credentials - err: exec:
        "docker-credential-desktop": executable file not found in $PATH

    A non-zero exit was read as "Docker says it cannot see that folder", and
    preflight refused the install with "a container could not see
    /Users/js/wow-wotlk" — a folder that WAS in Docker Desktop's file-sharing
    list, on a machine where the same `docker run` worked from Terminal. The
    user re-added the folder, tried others, and verified read/write inside a
    container before sending the log that named the real error.

    The exit code cannot tell the two apart — a denied mount and a failed pull
    both exit non-zero — so the daemon is asked a second question: is the image
    here? `docker run` pulls before it mounts, so an image that never arrived
    proves the mount was never attempted. Present means the failure really was
    the mount, and the refusal stands.
    """
    (tmp_path / "already-here.txt").write_text("x", encoding="utf-8")
    server_dir = tmp_path / "wow"
    asked: list[list[str]] = []

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return _completed(returncode=1, stderr="Error: No such image: alpine/git")
        return _completed(
            returncode=125,
            stderr=(
                "Unable to find image 'alpine/git' locally\n"
                "docker: error getting credentials - err: exec: "
                '"docker-credential-desktop": executable file not found in $PATH'
            ),
        )

    monkeypatch.setattr(docker.runner, "run", run)
    assert docker.bind_mount_ok(server_dir, "alpine/git") is None
    assert [a[1:3] for a in asked] == [["run", "--rm"], ["image", "inspect"]]


def test_a_mount_the_daemon_refused_with_the_image_in_hand_is_still_a_refusal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The case the check exists for must survive the fix above.

    Docker Desktop answers an unshared path with a non-zero exit and a "mounts
    denied" line. The image is local by then — `docker run` pulls first — so the
    second question separates it from the pull failure without either of them
    having to match on error wording.
    """
    (tmp_path / "already-here.txt").write_text("x", encoding="utf-8")

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ["image", "inspect"]:
            return _completed(stdout="sha256:abc")
        return _completed(returncode=125, stderr="Mounts denied: the path is not shared from OS X")

    monkeypatch.setattr(docker.runner, "run", run)
    assert docker.bind_mount_ok(tmp_path / "wow", "alpine/git") is False


def test_a_listing_with_entries_in_it_is_a_listing_however_ls_exited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The probe's question is answered by stdout. The exit code is not the answer.

    This refused EVERY macOS install whose chosen folder was new, which is
    every first install. The chosen folder is empty or absent at preflight
    time, so the probe walks up to the nearest populated ancestor — routinely
    the user's home directory — and on a Mac `ls -A` of a home directory prints
    a full listing AND exits non-zero, because Docker Desktop cannot stat the
    TCC-protected entries in it. The tester's own run, pasted verbatim
    (2026-08-26):

        $ docker run --rm --entrypoint ls -v /Users/js:/probe:ro alpine/git@… -A /probe
        ls: /probe/.Trash: No such file or directory
        ls: /probe/Documents: No such file or directory
        .CFUserTextEncoding
        …
        wow-wotlk

    busybox `ls` exits non-zero when it could not stat something, so this
    listing — 15 entries the container plainly saw, including the folder he
    picked — was read as "Docker cannot see that folder". He re-added the
    folder to file sharing, added its parent, tried several other folders and
    read a file back out of a container against that exact path; nothing could
    have made it pass, because nothing he could do would make `.Trash`
    stat-able.

    So stdout is asked first. Entries in it mean the container saw the folder,
    whatever `ls` thought of the parts it could not reach. Only an EMPTY
    listing sends the question to the exit code — which is the silently-empty
    mount this check exists for, and it still refuses.
    """
    (tmp_path / "already-here.txt").write_text("x", encoding="utf-8")
    partial = (
        "ls: /probe/.Trash: No such file or directory\n"
        "ls: /probe/Documents: No such file or directory\n"
    )

    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        if argv[1:3] == ["image", "inspect"]:
            return _completed(stdout="sha256:abc")
        return _completed(
            returncode=1,
            stdout=".CFUserTextEncoding\nDesktop\nDownloads\nLibrary\nwow-server\n",
            stderr=partial,
        )

    monkeypatch.setattr(docker.runner, "run", run)
    assert docker.bind_mount_ok(tmp_path / "wow-server", "alpine/git") is True


def test_the_bind_mount_probe_catches_the_silently_empty_mount_it_exists_for(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The failure this check was written for, which its exit code could not see.

    Docker Desktop mounts a folder outside its file-sharing list as an EMPTY
    directory instead of failing the run. `ls` on an empty directory exits 0, so
    a probe that read the exit code answered True for exactly the broken case
    and preflight printed `[pass] sharing the folder with Docker` (review,
    2026-08-23).
    """
    (tmp_path / "the-host-can-see-this").write_text("x", encoding="utf-8")
    monkeypatch.setattr(docker.runner, "run", lambda *a, **k: _completed(returncode=0, stdout="\n"))
    assert docker.bind_mount_ok(tmp_path / "wow", "alpine/git") is False


def test_the_probe_walks_up_to_a_directory_that_has_something_in_it(tmp_path: Path) -> None:
    """An empty directory's listing proves nothing, so it is not what gets mounted."""
    empty = tmp_path / "a" / "b"
    empty.mkdir(parents=True)
    # `b` is empty and `wow` does not exist, so the walk goes up to `a`, which
    # holds `b`. A directory holding only an empty subdirectory still counts:
    # the comparison needs a non-empty listing, not files.
    assert docker._first_populated_ancestor(empty / "wow") == tmp_path / "a"
    (empty / "something").write_text("x", encoding="utf-8")
    assert docker._first_populated_ancestor(empty) == empty


def test_a_directory_that_cannot_be_looked_into_is_unchecked_not_a_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """ "We could not tell" must never reach preflight as a shared folder."""

    def refuse(_self: Path) -> object:
        raise PermissionError("Operation not permitted")

    monkeypatch.setattr(Path, "iterdir", refuse)
    assert docker._first_populated_ancestor(tmp_path) is None
    assert docker.bind_mount_ok(tmp_path / "wow", "alpine/git") is None


def test_a_missing_image_is_told_apart_from_a_daemon_that_will_not_talk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both spellings of "no such image", because only one of them was measured.

    `"Error response from daemon: No such image: <ref>"` is what Docker 29.1.3
    actually said, and is quoted in `pyplan/checklist.md`. The `"not found"`
    half of the match is belt-and-braces for a wording this project has not
    seen, and it had no test at all (review, 2026-08-24). Both must answer
    False — "that image is not here" — while anything else answers None, and
    the difference matters because False and None differ by a multi-hour build
    only in the log line, never in the action.
    """
    for said in (
        "Error response from daemon: No such image: yulon.local/x:t",
        "Error: No such image: yulon.local/x:t",
        "Error response from daemon: image not found",
    ):
        monkeypatch.setattr(
            docker.runner, "run", lambda *a, s=said, **k: _completed(returncode=1, stderr=s)
        )
        assert docker.images_built(REFS) is False, said

    for said in ("permission denied while trying to connect", "context deadline exceeded", ""):
        monkeypatch.setattr(
            docker.runner, "run", lambda *a, s=said, **k: _completed(returncode=1, stderr=s)
        )
        assert docker.images_built(REFS) is None, said


# --------------------------------------------------------- WSL-resident servers


def test_docker_commands_for_a_wsl_install_go_through_that_distro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The whole point, asserted on the emitted argv rather than on prose.

    A server inside a distro is reached by that distro's own docker. Every
    lifecycle call goes through `_docker()`, so pinning it here pins all 21.
    """
    seen: list[list[str]] = []

    def fake_run(cmd, cwd=None, timeout=None, env=None):  # type: ignore[no-untyped-def]
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    monkeypatch.setattr(docker.platform, "_which", lambda name, path=None: "wsl.exe")

    docker._docker(["ps"], wsl_distro="dml-arch")
    assert seen[0][:5] == ["wsl.exe", "-d", "dml-arch", "--", "docker"]
    assert seen[0][-1] == "ps"


def test_a_wsl_install_sends_its_directory_as_a_distro_path_not_a_windows_cwd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Windows process cannot cd into a distro, so the location rides in argv.

    `wsl --cd <linux path>` rather than compose's `--project-directory`, because
    `_docker()` runs every docker subcommand and only compose understands the
    latter.
    """
    seen: list[dict[str, object]] = []

    def fake_run(cmd, cwd=None, timeout=None, env=None):  # type: ignore[no-untyped-def]
        seen.append({"cmd": list(cmd), "cwd": cwd})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    monkeypatch.setattr(docker.platform, "_which", lambda name, path=None: "wsl.exe")

    unc = Path(r"\\wsl.localhost\dml-arch\home\dml\games\srv")
    docker._docker(["compose", "ps"], cwd=unc, wsl_distro="dml-arch")

    cmd = seen[0]["cmd"]
    assert "--cd" in cmd, f"the distro path never reached the command: {cmd}"
    assert cmd[cmd.index("--cd") + 1] == "/home/dml/games/srv"
    # WHERE it sits is the whole of the bug this caught. Everything after `--`
    # is the command line for the distro's shell, so a `--cd` placed there
    # reaches bash, which answers "--: invalid option". The first version of
    # this test asserted only that the flag was present, and passed a command
    # that could not run; a live run against a real distro found it.
    assert cmd.index("--cd") < cmd.index("--"), f"--cd landed after the separator: {cmd}"
    assert seen[0]["cwd"] is None, "a UNC path was handed to the process as its cwd"


def test_a_local_install_is_unchanged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """No distro means exactly what it meant before this existed."""
    seen: list[dict[str, object]] = []

    def fake_run(cmd, cwd=None, timeout=None, env=None):  # type: ignore[no-untyped-def]
        seen.append({"cmd": list(cmd), "cwd": cwd})
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")

    docker._docker(["ps"], cwd=tmp_path)
    assert seen[0]["cmd"] == ["docker", "ps"]
    assert seen[0]["cwd"] == tmp_path


def test_no_wsl_on_this_host_is_the_existing_missing_cli_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new topology must not bring a new failure channel.

    Callers already know how to report `_cli_missing()`; a WSL install on a box
    without WSL reports through that rather than raising something nobody
    catches.
    """
    monkeypatch.setattr(docker.platform, "_which", lambda name, path=None: None)
    proc = docker._docker(["ps"], wsl_distro="dml-arch")
    assert docker._cli_missing(proc)


# Functions that reach the docker seam without needing to name a daemon, each
# with the reason. Anything NOT here and NOT taking `wsl_distro` fails the
# completeness test below.
_DAEMON_AGNOSTIC: dict[str, str] = {
    "_docker": "the seam itself - it takes the distro and builds the argv",
    "run_container": (
        "a host bind mount is a local-daemon concept: `Mount.host` is a path on THIS "
        "machine and `to_argv()` translates nothing, so forwarding a distro would send "
        "a Windows drive path to a docker that cannot see it - a container silently "
        "mounting the wrong directory, which is worse than the missing-CLI answer it "
        "gives today. Making it WSL-capable is a question about where the mounts are "
        "built; `git.ContainerGit._capture()`, the app's other `docker run` over a host "
        "bind, resolves `docker_program()` directly for the same reason."
    ),
    "copy_from_image": (
        "only an INSTALL reaches it - the conf stage, pulling `*.conf.dist` out of an image "
        "this run just built - and an install is local by construction: "
        "`install_wiring.installer_for_app()` passes no distro and says why ('an install "
        "creates the server here'), because only an EXISTING install can live in a distro "
        "the app has to name. Reaching a WSL daemon would also change what `dest` means: "
        "`docker cp` resolves its host side wherever the CLI runs, so the same argv would "
        "write the conf files into the distro's filesystem instead of the user's server "
        "folder - and unlike `run_container`, whose mount sources are sealed inside an "
        "opaque argv, there IS a translation for `dest` (`platform.wsl_linux_path()`). It "
        "belongs to whoever holds the distro, not here: that is the caller, and no caller "
        "has one yet."
    ),
}


def _seam_reachers() -> dict[str, list[str]]:
    """Every function in `docker.py` that reaches `_docker`, directly or not.

    Transitive on purpose. `wait_ready()` never calls `_docker` itself - it calls
    `_health()`, which does - so a direct-callers-only rule would bless it while
    it quietly asked the wrong daemon. The closure is what makes this a
    guarantee rather than a spot check.

    Parsed rather than grepped, so a call inside a nested block counts and a
    mention in a docstring does not.
    """
    tree = ast.parse(Path(docker.__file__).read_text(encoding="utf-8"))
    defs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
    calls: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        defs[node.name] = node
        # Both call shapes. `_docker(...)` is an ast.Name, but `docker.status(...)`
        # or `self._logs(...)` is an ast.Attribute - and a function reaching the
        # seam that way was INVISIBLE to the first version of this scan. Proved
        # by writing one: a helper calling `me.container_state(...)` through an
        # `import yulon.docker as me` passed this test while being unable to name
        # a daemon.
        calls[node.name] = {
            child.func.id if isinstance(child.func, ast.Name) else child.func.attr
            for child in ast.walk(node)
            if isinstance(child, ast.Call) and isinstance(child.func, (ast.Name, ast.Attribute))
        }

    # Only names this module actually defines: an attribute call to something
    # else entirely (`proc.stdout.splitlines()`) must not be mistaken for one of
    # ours because the attribute happens to share a name.
    defined = set(defs)
    calls = {name: called & defined for name, called in calls.items()}

    # Rooted at every function that asks platform HOW to reach docker, not at
    # `_docker` alone. There are two spawn seams in this module - `_docker()`
    # buffers, `follow_logs()` and `run_attached()` stream - and a closure rooted
    # at the first blessed the second: between them they carry the Console tab's
    # log stream and `build_staged()`, so a WSL-resident server's logs and image
    # build both addressed the local daemon while this test passed.
    roots = {
        name
        for name, node in defs.items()
        if any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Attribute)
            and c.func.attr in {"docker_prefix", "docker_program"}
            for c in ast.walk(node)
        )
    }
    reaching = {"_docker", *roots}
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in reaching and called & reaching:
                reaching.add(name)
                changed = True

    out: dict[str, list[str]] = {}
    for name in reaching:
        node = defs[name]
        args = node.args
        out[name] = [a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    return out


def test_every_function_that_talks_to_docker_can_say_which_daemon() -> None:
    """A missed call site is SILENT, which is why this is a test and not a habit.

    A WSL-resident server's containers live in that distro's docker. A function
    that cannot pass the distro asks Docker Desktop instead - and Docker Desktop
    answers cheerfully, with an empty list. The server reads as stopped while it
    is running fine, and nothing raises.

    So the rule is checked by parsing the module rather than by remembering:
    every function that reaches the seam, at any depth, either takes
    `wsl_distro` or is named in `_DAEMON_AGNOSTIC` with the reason.
    """
    missing = [
        name
        for name, args in _seam_reachers().items()
        if "wsl_distro" not in args and name not in _DAEMON_AGNOSTIC
    ]
    assert not missing, (
        "these reach docker but cannot say which daemon, so a WSL-resident "
        f"server would be asked of the wrong one: {sorted(missing)}\n"
        "Add `wsl_distro: str | None = None` and forward it, or add the name to "
        "_DAEMON_AGNOSTIC with the reason it needs none."
    )


def test_the_completeness_test_would_notice_a_new_function() -> None:
    """The guard's own guard: prove it reads the module rather than a list.

    Without this, `_seam_reachers()` could quietly return almost nothing - a
    parse error, a renamed seam - and the test above would pass by finding
    nothing to complain about, which is the failure mode it exists to prevent.
    """
    reaching = _seam_reachers()
    assert len(reaching) > 15, f"only found {len(reaching)} - is the parse working?"
    for known in ("status", "_run", "_docker", "wait_ready"):
        assert known in reaching, f"{known} should reach the seam but was not found"


def test_the_completeness_scan_sees_a_module_qualified_call() -> None:
    """The hole the first version had, pinned so it cannot come back.

    A function reaching the seam through `docker.status(...)` or
    `me.container_state(...)` rather than a bare name was invisible, because the
    scan matched only `ast.Name` callees. Proved by writing exactly such a helper
    into docker.py: the completeness test passed while the helper could not name
    a daemon, so the guarantee was narrower than it claimed.

    Asserted against a synthetic module, so proving it needs no edit to the real
    one - and the unrelated-attribute case is asserted too, because the fix must
    not start counting `proc.stdout.splitlines()` as a call to one of ours.
    """
    source = """
def _docker(argv, *, wsl_distro=None): ...
def reached_by_name(*, wsl_distro=None):
    return _docker([], wsl_distro=wsl_distro)
def reached_by_attribute():
    import yulon.docker as me
    return me.reached_by_name()
def unrelated(proc):
    return proc.stdout.splitlines()
"""
    tree = ast.parse(source)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    calls: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            calls[node.name] = {
                c.func.id if isinstance(c.func, ast.Name) else c.func.attr
                for c in ast.walk(node)
                if isinstance(c, ast.Call) and isinstance(c.func, (ast.Name, ast.Attribute))
            } & defined

    reaching = {"_docker"}
    changed = True
    while changed:
        changed = False
        for name, called in calls.items():
            if name not in reaching and called & reaching:
                reaching.add(name)
                changed = True

    assert "reached_by_attribute" in reaching, "a module-qualified call is still invisible"
    assert "unrelated" not in reaching, "an unrelated attribute call was miscounted"


def test_a_deleted_distro_is_explained_rather_than_reported_as_a_bare_exit_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The helper that explains this was written, and then nothing called it.

    A helper with no caller reads as a fix in the diff and changes nothing for
    the user, so this asserts through `_run()` - the seam every buffered docker
    call goes through - rather than by calling `wsl.missing_distro_problem()`
    directly. That distinction is the whole lesson of this branch: four blockers
    were functions that accepted something no caller passed.

    The bare message is empty here, not merely terse: wsl.exe complains on
    STDOUT, and `_run()` quotes stderr, so what the user actually saw was
    `docker ps exited 4294967295: ` with nothing after the colon.
    """
    from yulon import wsl

    gone = "T\x00h\x00e\x00r\x00e\x00 \x00i\x00s\x00 \x00n\x00o\x00"
    monkeypatch.setattr(
        docker, "_docker", lambda *a, **k: _completed(4294967295, stdout=gone, stderr="")
    )
    monkeypatch.setattr(wsl, "distro_states", lambda: (wsl.Distro("other-distro", True),))

    with pytest.raises(docker.DockerCommandError) as raised:
        docker._run(["ps"], wsl_distro="dml-arch")

    said = str(raised.value)
    assert "dml-arch" in said and "no longer exists" in said, said
    assert "4294967295" not in said, "the raw exit code is still what the user reads"


def test_an_ordinary_docker_failure_still_reports_the_command_and_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard for the above: the new branch must not swallow every failure.

    A wrong container name, a daemon that refused, a compose file with a typo -
    all of those still need the command, the exit code and whatever docker put
    on stderr, and none of them are a missing distro.
    """
    monkeypatch.setattr(
        docker, "_docker", lambda *a, **k: _completed(1, stderr="no such container: nope")
    )
    with pytest.raises(docker.DockerCommandError) as raised:
        docker._run(["inspect", "nope"], wsl_distro="dml-arch")
    said = str(raised.value)
    assert "docker inspect nope exited 1" in said and "no such container" in said, said


# wsl.exe writes UTF-16LE, and `runner.stream()` decodes as UTF-8, so each ASCII
# character arrives followed by a NUL. This is what the Console tab was showing.
_GONE_LINE = "T\x00h\x00e\x00r\x00e\x00 \x00i\x00s\x00 \x00n\x00o\x00"


def _stream_that_fails(*lines: str, returncode: int = 4294967295):
    """A `runner.stream` stand-in: yields lines, then fails the way the real one does."""

    def fake(command: list[str], cwd: object = None, **_kw: object):
        yield from lines
        raise subprocess.CalledProcessError(returncode, command)

    return fake


def _wsl_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend this box can reach a distro, whatever OS the suite is running on.

    Without this the three tests below pass on Windows and fail on CI's Ubuntu
    for a reason that has nothing to do with what they check: `docker_prefix()`
    looks for `wsl.exe` on PATH, finds none, and both seams return
    `_CLI_MISSING_RETURNCODE` before `runner.stream` is ever reached - so the
    assertion reads 127 and the Docker-is-missing help text. The seam under
    test is what happens AFTER the command runs, so the prefix is pinned rather
    than discovered.
    """
    monkeypatch.setattr(
        docker.platform,
        "docker_prefix",
        lambda wsl_distro=None, *, inside=None: ("wsl.exe", "-d", str(wsl_distro), "--", "docker"),
    )


def test_a_deleted_distro_is_explained_in_the_console_log_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_run()` was wired for this and the two STREAMING seams were not.

    Start, Stop and Status go through `_run()`; the Console tab's log goes
    through `follow_logs()`, which raises whatever `runner.stream()` raises. So
    the same dead distro was explained on one tab and shown as NUL-riddled
    gibberish followed by "CalledProcessError: ... exit status 4294967295" on
    another - naming neither the distro nor anything to do about it.

    A seam-by-seam fix that stops at the first seam is how this branch produced
    four blockers; the second one is not optional.
    """
    from yulon import wsl

    _wsl_prefix(monkeypatch)
    monkeypatch.setattr(runner, "stream", _stream_that_fails(_GONE_LINE))
    monkeypatch.setattr(wsl, "distro_states", lambda: (wsl.Distro("other-distro", True),))

    with pytest.raises(docker.DockerCommandError) as raised:
        list(docker.follow_logs("ac-worldserver", wsl_distro="dml-arch"))

    said = str(raised.value)
    assert "dml-arch" in said and "no longer exists" in said, said


def test_a_streamed_command_in_a_deleted_distro_says_so_instead_of_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The build and the import read this one, and it must not start raising.

    `run_attached()` promises its callers a status back rather than an
    exception - `repair_import()` needs to go on and check something else - so
    the translation replaces the retained lines and leaves the contract alone.
    Replaced, not appended: on this failure every retained line IS the
    complaint, and printing it above the explanation buries the explanation in
    the noise it was written to translate.
    """
    from yulon import wsl

    _wsl_prefix(monkeypatch)
    monkeypatch.setattr(runner, "stream", _stream_that_fails(_GONE_LINE, _GONE_LINE))
    monkeypatch.setattr(wsl, "distro_states", lambda: (wsl.Distro("other-distro", True),))

    result = docker.run_attached(["compose", "up"], Path("/srv"), wsl_distro="dml-arch")

    assert result.returncode == 4294967295, "the status was swallowed"
    assert len(result.tail) == 1, result.tail
    assert "dml-arch" in result.tail[0] and "no longer exists" in result.tail[0]
    assert "\x00" not in result.tail[0], "the raw UTF-16 complaint is still what is shown"


def test_an_ordinary_streamed_failure_keeps_its_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard: a compile that failed on line 900 still needs its last 200 lines.

    The translation above only fires on a distro that is really gone, so every
    other non-zero exit - a broken compose file, a failed build - comes back
    exactly as it did.
    """
    _wsl_prefix(monkeypatch)
    monkeypatch.setattr(
        runner, "stream", _stream_that_fails("error: undefined reference", returncode=2)
    )
    result = docker.run_attached(["compose", "build"], Path("/srv"), wsl_distro="dml-arch")
    assert result.returncode == 2
    assert result.tail == ("error: undefined reference",)


def test_the_diagnostic_it_offers_is_a_command_that_actually_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker compose logs` takes a SERVICE, and the sentence used to pass a container.

    The argv three lines above it was already correct; the advice under it was
    not, so the one command the app hands a stuck user answered
    `no such service` — the very error the Discord report opened with
    (2026-08-26). Invisible on AzerothCore, where the two names are the same.
    """
    renamed = docker.ContainerSpec(
        db="c-db",
        auth="c-auth",
        world="c-world",
        ports=(1,),
        services=("s-db", "s-auth", "s-world"),
    )
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _start_runner(calls, up=("c-db",)))
    with pytest.raises(docker.DockerCommandError) as raised:
        docker.start_staged(renamed, Path("/tmp/x"))
    said = str(raised.value)
    assert (
        "c-auth, c-world are not running" in said
    ), "it must still name the containers it looked for"
    assert "docker compose logs s-auth" in said, said
    assert "docker compose logs c-auth" not in said


def test_container_spec_translates_a_container_name_to_its_service() -> None:
    """The two-name mapping in one place, so no caller has to index tuples by hand."""
    renamed = docker.ContainerSpec(
        db="c-db",
        auth="c-auth",
        world="c-world",
        ports=(1,),
        services=("s-db", "s-auth", "s-world"),
    )
    assert renamed.service_for("c-db") == "s-db"
    assert renamed.service_for("c-world") == "s-world"
    # A name it does not know is returned unchanged: better a possibly-right
    # command than a confidently wrong one.
    assert renamed.service_for("ac-database") == "ac-database"
    assert SPEC.service_for(SPEC.db) == SPEC.db


# ------------------------------------------------------ 7.3: docker run by field


def _flag_values(argv: list[str], flag: str) -> list[str]:
    """Every value that follows `flag` in argv — how a run is audited by field, not by string."""
    return [argv[i + 1] for i, item in enumerate(argv[:-1]) if item == flag]


def test_a_container_run_is_audited_by_field_not_by_string(tmp_path: Path) -> None:
    """Which mount is `:ro`, which user, which cwd — each readable from the argv on its own.

    The extraction stage's whole safety argument is that the client is mounted
    read-only and the output goes somewhere else; a test that matched one long
    string would pass a reordered flag and fail a harmless one.
    """
    client = tmp_path / "client"
    out = tmp_path / "out"
    spec = docker.ContainerRun(
        image="yulon.local/tbc-server:native-abc",
        argv=("/opt/tools/ad", "-i", "/client", "-o", "/out"),
        mounts=(docker.Mount(client, "/client", read_only=True), docker.Mount(out, "/out")),
        workdir="/out",
        env={"TERM": "dumb"},
        user_args=("--user", "1000:1000"),
        ulimits=("stack=-1",),
    )
    argv = spec.to_argv()
    assert argv[:2] == ["run", "--rm"]
    assert _flag_values(argv, "-v") == [f"{client}:/client:ro", f"{out}:/out"]
    assert _flag_values(argv, "-w") == ["/out"]
    assert _flag_values(argv, "-e") == ["TERM=dumb"]
    assert _flag_values(argv, "--user") == ["1000:1000"]
    assert _flag_values(argv, "--ulimit") == ["stack=-1"]
    # The tool's own argv is verbatim after the image: docker stops reading
    # options at the image name, so anything of ours after it would reach the tool.
    image_at = argv.index("yulon.local/tbc-server:native-abc")
    assert argv[image_at + 1 :] == ["/opt/tools/ad", "-i", "/client", "-o", "/out"]


def test_a_bare_run_is_just_rm_image_and_command() -> None:
    """No mounts, no user, no env: nothing is emitted for a field left at its default."""
    spec = docker.ContainerRun(image="busybox:1.36", argv=("true",))
    assert spec.to_argv() == ["run", "--rm", "busybox:1.36", "true"]


def test_a_relative_mount_source_is_refused_before_docker_sees_it() -> None:
    """Docker rejects a relative bind source daemon-side; the message should name our caller."""
    spec = docker.ContainerRun(
        image="busybox:1.36", argv=("true",), mounts=(docker.Mount(Path("data"), "/out"),)
    )
    with pytest.raises(ValueError, match="absolute"):
        spec.to_argv()


# ----------------------------------------- 7.3: one attached `docker run`


def test_run_container_streams_the_spelled_argv_with_stderr_merged(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The argv is `to_argv()`'s, and progress on stderr reaches the sink live.

    The extractors print their progress to stderr, which `runner.stream()`
    otherwise holds back until the tool has exited — an hour of extraction as
    an hour of blank panel, the same trade `build_staged()` refuses.
    """
    seen, merged = _stream_double(monkeypatch, ["extracting 1/3", "extracting 2/3"])
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    spec = docker.ContainerRun(
        image="busybox:1.36",
        argv=("sh", "-c", "true"),
        mounts=(docker.Mount(tmp_path, "/out"),),
        workdir="/out",
    )
    heard: list[str] = []
    run = docker.run_container(spec, sink=heard.append)
    assert seen == [["docker", *spec.to_argv()]]
    assert merged == [True]
    assert heard == ["extracting 1/3", "extracting 2/3"]
    assert run.returncode == 0
    assert run.tail == ("extracting 1/3", "extracting 2/3")


def test_a_cancelled_container_run_reports_the_cancel_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same contract as the build: a Stop is not an exit status the tool produced."""
    _stream_double(monkeypatch, ["still going"])
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    cancel = docker.threading.Event()
    cancel.set()
    spec = docker.ContainerRun(image="busybox:1.36", argv=("sleep", "600"))
    run = docker.run_container(spec, sink=lambda _line: None, cancel=cancel)
    assert run.returncode == docker.CANCELLED_RETURNCODE


def test_a_container_that_exited_non_zero_keeps_the_words_that_say_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing image and a tool that failed are both non-zero, and neither is a cancel.

    `docker run` answers an image it cannot pull with 125 and a sentence on
    stderr; the extractor answers a corrupt MPQ with its own status and its own
    sentence. Both have to arrive as themselves — the caller decides what to
    say, and it cannot decide from a status this function flattened.
    """
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(
        docker.runner,
        "stream",
        _stream_that_fails(
            "Unable to find image 'yulon.local/nope:native-abc' locally",
            "Error response from daemon: pull access denied",
            returncode=125,
        ),
    )
    spec = docker.ContainerRun(image="yulon.local/nope:native-abc", argv=("true",))
    run = docker.run_container(spec, sink=lambda _line: None)
    assert run.returncode == 125
    assert run.returncode not in (0, docker.CANCELLED_RETURNCODE)
    assert "pull access denied" in run.tail[-1]


def test_a_host_with_no_docker_cli_is_told_apart_from_a_container_that_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The third state is "could not ask", and it carries help text, not an exit code.

    Nothing is spawned: the answer comes back before `runner.stream()` is
    reached, so a caller that reports "the extractor failed" for this would be
    naming the wrong culprit entirely.
    """
    seen, _merged = _stream_double(monkeypatch, ["never read"])
    monkeypatch.setattr(docker.platform, "docker_program", lambda: None)
    spec = docker.ContainerRun(image="busybox:1.36", argv=("true",))
    run = docker.run_container(spec, sink=lambda _line: None)
    assert run.returncode == docker._CLI_MISSING_RETURNCODE
    assert run.returncode not in (0, docker.CANCELLED_RETURNCODE)
    assert run.tail == (docker.platform.DOCKER_CLI_MISSING_HELP,)
    assert seen == [], "a host with no docker CLI must not reach the spawn seam"


def test_a_relative_mount_is_refused_before_any_container_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`to_argv()`'s refusal is a caller bug, and it must not be dressed up as a run.

    A `ValueError` reaching the stage says "this code built a bad spec"; an
    `AttachedRun` with a non-zero status would say "your machine could not run
    the extractor", and the install engine would retry it forever.
    """
    seen, _merged = _stream_double(monkeypatch, ["never read"])
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    spec = docker.ContainerRun(
        image="busybox:1.36", argv=("true",), mounts=(docker.Mount(Path("data"), "/out"),)
    )
    with pytest.raises(ValueError, match="absolute"):
        docker.run_container(spec, sink=lambda _line: None)
    assert seen == []


def _copy_runner(
    calls: list[list[str]],
    *,
    create_id: str = "0123456789abcdef",
    create_stdout: str | None = None,
    create_rc: int = 0,
    create_stderr: str = "",
    cp_fails: bool = False,
    cp_stderr: str | None = None,
    cp_raises: BaseException | None = None,
    rm_fails: bool = False,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A runner that answers `docker create` with an id and lets `cp`/`rm` succeed or fail.

    Every knob is one exit path out of `copy_from_image()`, because the `rm`
    guarantee is only a guarantee if each of them is asked separately: a single
    happy-path test passes against an implementation that removes nothing when
    anything goes wrong, which is the leak the function exists to close.
    """

    def fake_run(
        cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["docker", "create"]:
            stdout = f"{create_id}\n" if create_stdout is None else create_stdout
            return _completed(create_rc, stdout=stdout, stderr=create_stderr)
        if cmd[:2] == ["docker", "cp"]:
            if cp_raises is not None:
                raise cp_raises
            if cp_fails:
                inside = cmd[2].partition(":")[2]
                default = f"Error: No such container:path: {create_id}:{inside}"
                return _completed(1, stderr=default if cp_stderr is None else cp_stderr)
        if cmd[:2] == ["docker", "rm"] and rm_fails:
            return _completed(1, stderr="Error response from daemon: removal already in progress")
        return _completed()

    return fake_run


def _rm_calls(calls: list[list[str]]) -> list[list[str]]:
    """Just the removals, so a test can say "exactly one" or "none at all"."""
    return [cmd for cmd in calls if cmd[:2] == ["docker", "rm"]]


def test_copy_from_image_creates_copies_and_removes_in_that_order(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No shell in the image, no bind mount, no root-owned files: create + cp + rm."""
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _copy_runner(calls))
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert calls == [
        # `--entrypoint true`: `docker create` refuses an image with neither an
        # ENTRYPOINT nor a CMD, and nothing is ever started.
        ["docker", "create", "--entrypoint", "true", "yulon.local/x:t"],
        ["docker", "cp", "0123456789abcdef:/opt/etc", str(tmp_path / "etc")],
        ["docker", "rm", "-f", "0123456789abcdef"],
    ]


def test_the_container_id_is_the_last_stdout_line_not_the_whole_of_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    r"""A first-time pull can put a line on stdout ahead of the id; the id is still the id.

    Taking `stdout.strip()` whole would hand `Unable to find image ...\nabc123`
    to `docker cp` as a container name, and the copy would fail with a sentence
    about a container nobody named.
    """
    calls: list[list[str]] = []
    runner_fn = _copy_runner(
        calls, create_stdout="Unable to find image 'yulon.local/x:t' locally\n\nabc123def456\n"
    )
    monkeypatch.setattr(docker.runner, "run", runner_fn)
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert calls[1] == ["docker", "cp", "abc123def456:/opt/etc", str(tmp_path / "etc")]
    assert _rm_calls(calls) == [["docker", "rm", "-f", "abc123def456"]]


def test_a_failed_copy_still_removes_the_container_it_created(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stopped container per failed resume is the leak this guards against."""
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _copy_runner(calls, cp_fails=True))
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with pytest.raises(docker.DockerCommandError, match="No such container:path"):
        docker.copy_from_image("yulon.local/x:t", "/nope", tmp_path / "nope")
    assert calls[-1] == ["docker", "rm", "-f", "0123456789abcdef"]


def test_a_create_that_prints_no_id_is_an_error_and_removes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Without an id there is nothing to copy from and nothing to remove - say so."""
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _copy_runner(calls, create_id=""))
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with pytest.raises(docker.DockerCommandError, match="no container id"):
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert [cmd for cmd in calls if cmd[1] in ("cp", "rm")] == []


def test_a_create_that_fails_outright_removes_nothing_and_names_the_image(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No container was made, so a removal would be the only mistake left to make.

    `docker rm -f ""` is not harmless noise: it exits non-zero, and the warning
    it produces would blame the removal for an image that was never pulled.
    """
    calls: list[list[str]] = []
    runner_fn = _copy_runner(
        calls,
        create_rc=125,
        create_stdout="",
        create_stderr="Unable to find image 'yulon.local/x:t' locally",
    )
    monkeypatch.setattr(docker.runner, "run", runner_fn)
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with pytest.raises(docker.DockerCommandError, match="Unable to find image"):
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert _rm_calls(calls) == []


def test_an_interrupted_copy_still_removes_the_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Cancel is not an exit status, and the container must not outlive it either.

    `_run()` has no cancel event, so a stop during the copy arrives as whatever
    the interpreter raises - `KeyboardInterrupt` from the CLI, and a
    `BaseException` at that, which a `try/except Exception` around the copy
    would let straight past the removal.
    """
    calls: list[list[str]] = []
    runner_fn = _copy_runner(calls, cp_raises=KeyboardInterrupt())
    monkeypatch.setattr(docker.runner, "run", runner_fn)
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with pytest.raises(KeyboardInterrupt):
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert _rm_calls(calls) == [["docker", "rm", "-f", "0123456789abcdef"]]


def test_an_unexpected_error_during_the_copy_is_not_dressed_up_as_a_copy_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bug in this process is not the image's fault - but the container still goes."""
    calls: list[list[str]] = []
    runner_fn = _copy_runner(calls, cp_raises=MemoryError("out of memory"))
    monkeypatch.setattr(docker.runner, "run", runner_fn)
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with pytest.raises(MemoryError):
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert _rm_calls(calls) == [["docker", "rm", "-f", "0123456789abcdef"]]


def test_a_removal_that_fails_is_logged_and_does_not_replace_the_copys_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Two things went wrong; the one that explains the install is the copy's."""
    calls: list[list[str]] = []
    runner_fn = _copy_runner(calls, cp_fails=True, rm_fails=True)
    monkeypatch.setattr(docker.runner, "run", runner_fn)
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with caplog.at_level("WARNING"):
        with pytest.raises(docker.DockerCommandError, match="No such container:path"):
            docker.copy_from_image("yulon.local/x:t", "/nope", tmp_path / "nope")
    assert _rm_calls(calls) == [["docker", "rm", "-f", "0123456789abcdef"]]
    assert "removal already in progress" in caplog.text
    assert "0123456789ab" in caplog.text


def test_a_successful_removal_says_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The warning means a container was left behind; a clean run must not cry wolf."""
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _copy_runner(calls))
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with caplog.at_level("WARNING"):
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert caplog.text == ""


def test_a_missing_source_in_the_image_is_not_the_same_sentence_as_a_broken_copy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Docker blames a *container* for a path the *image* never had.

    `Error: No such container:path: 0123456789abcdef:/opt/etc` is docker's own
    wording, and read at face value it says the throwaway container vanished -
    a machine problem, worth a retry. The truth is that this image does not ship
    that file, which is a catalog bug that retrying forever cannot fix. So the
    message leads with the image and the path, and the two failures stay two.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _copy_runner(calls, cp_fails=True))
    monkeypatch.setattr(docker.platform, "docker_program", lambda: "docker")
    with pytest.raises(docker.DockerCommandError) as absent:
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert "yulon.local/x:t has no /opt/etc" in str(absent.value)

    calls.clear()
    broken = _copy_runner(calls, cp_fails=True, cp_stderr="Error response from daemon: disk full")
    monkeypatch.setattr(docker.runner, "run", broken)
    with pytest.raises(docker.DockerCommandError) as failed:
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert "has no /opt/etc" not in str(failed.value)
    assert "could not copy /opt/etc out of yulon.local/x:t" in str(failed.value)
    assert "disk full" in str(failed.value)


def test_no_docker_cli_at_all_is_a_third_answer_and_stays_recognisable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Could not ask is neither the copy failed nor the image has no such path.

    `DockerCliMissingError` carries the help text and nothing else on purpose,
    so wrapping it in a sentence about the image would bury the one instruction
    the user can act on. Nothing was created, so nothing is removed either.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _copy_runner(calls))
    monkeypatch.setattr(docker.platform, "docker_program", lambda: None)
    with pytest.raises(docker.DockerCliMissingError) as absent:
        docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert str(absent.value) == docker.platform.DOCKER_CLI_MISSING_HELP
    assert calls == [], "with no CLI to run, nothing may reach the spawn seam"


def test_a_cli_that_disappears_mid_copy_is_still_could_not_ask(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Docker uninstalled between the create and the copy: the container is still ours.

    The `DockerCliMissingError` must reach the caller unwrapped - rewrapping it
    would say the image was missing a file, when nothing was ever asked. The
    removal cannot work either, so this is the one path that genuinely leaks,
    and the log has to say which container by name rather than repeating the
    install-Docker help text a second time.
    """
    calls: list[list[str]] = []
    programs = iter(["docker", None, None])
    monkeypatch.setattr(docker.runner, "run", _copy_runner(calls))
    monkeypatch.setattr(docker.platform, "docker_program", lambda: next(programs))
    with caplog.at_level("WARNING"):
        with pytest.raises(docker.DockerCliMissingError) as absent:
            docker.copy_from_image("yulon.local/x:t", "/opt/etc", tmp_path / "etc")
    assert str(absent.value) == docker.platform.DOCKER_CLI_MISSING_HELP
    assert calls == [["docker", "create", "--entrypoint", "true", "yulon.local/x:t"]]
    assert "0123456789ab" in caplog.text
    assert docker.platform.DOCKER_CLI_MISSING_HELP not in caplog.text
