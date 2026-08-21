"""Tests for the shared Docker lifecycle (`yulon.docker` and WotLK wrapper).

All subprocess calls are mocked at the `yulon.runner.run` boundary, so nothing
here requires a real Docker daemon — mirroring roadmap 1.3's "mocked control
flow" intent. The integration suite (Phase 1.5, `tests/fixture.md`) is where a
real AzerothCore compose project gets exercised.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from yulon import docker, runner
from yulon.controller_wow_wotlk import docker_ctl

SPEC = docker_ctl.SPEC

_MATCHING_HASHES = "db h1 auth h2 world h3"


def _completed(
    returncode: int = 0, stdout: str = "", stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _pairs(spaced: str) -> str:
    """`"db h1 auth h2"` as the two-column output `docker` prints, one pair per line."""
    words = spaced.split()
    return "".join(
        f"{name} {digest}\n" for name, digest in zip(words[::2], words[1::2], strict=True)
    )


def _hash_aware_runner(
    calls: list[list[str]],
    *,
    configured: str = _MATCHING_HASHES,
    deployed: str | None = None,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    """A `runner.run` double that answers compose's config-hash questions.

    `configured` is what the compose files on disk hash to; `deployed` is what
    the existing containers are labelled with (the same, unless a test says
    otherwise). Everything else answers success with no output, and the three
    install containers always exist, so a test can vary one thing at a time.
    """
    labelled = configured if deployed is None else deployed

    def fake_run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:3] == ["docker", "compose", "config"]:
            return _completed(stdout=_pairs(configured))
        if cmd[:3] == ["docker", "compose", "ps"]:
            return _completed(stdout="id-db\nid-auth\nid-world\n" if labelled else "")
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout=_pairs(labelled))
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.db}\n{SPEC.auth}\n{SPEC.world}\n")
        return _completed()

    return fake_run


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

    def fake_run(cmd: list[str], cwd: Path | None = None):
        calls.append(cmd)
        cwds.append(cwd)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    server_dir = Path("/tmp/wow")
    docker.start(server_dir)
    assert calls == [["docker", "compose", "up", "-d"]]
    assert cwds == [server_dir]


def test_stop_runs_compose_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """`stop()` shells out to `docker compose down` in the server dir."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None):
        calls.append(cmd)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    docker.stop(Path("/tmp/wow"))
    assert calls == [["docker", "compose", "down"]]


def test_stop_staged_uses_compose_stop_so_the_containers_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compose stop` keeps every container and honours the project's depends_on order."""
    calls: list[list[str]] = []
    cwds: list[Path | None] = []

    def fake_run(cmd: list[str], cwd: Path | None = None):
        calls.append(cmd)
        cwds.append(cwd)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    server_dir = Path("/tmp/wow")
    assert docker.stop_staged(SPEC, server_dir) is True
    assert calls == [["docker", "compose", "stop"]]
    assert cwds == [server_dir]


def test_stop_staged_falls_back_to_one_docker_stop_per_container_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a readable compose project, stop by name — one call each, world first.

    One call per container is the whole point: `docker stop a b c` signals all
    three at once, so a single multi-name call cannot express "let the world
    server finish its saves before the database goes away".
    """
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None):
        calls.append(cmd)
        if cmd[:3] == ["docker", "compose", "stop"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        if cmd[:3] == ["docker", "ps", "-a"]:
            return _completed(stdout=f"{SPEC.db}\n{SPEC.auth}\n{SPEC.world}\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is True
    assert [cmd for cmd in calls if cmd[:2] == ["docker", "stop"]] == [
        ["docker", "stop", SPEC.world],
        ["docker", "stop", SPEC.auth],
        ["docker", "stop", SPEC.db],
    ]
    assert ["docker", "compose", "down"] not in calls


def test_stop_staged_never_removes_containers_when_there_is_nothing_to_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No compose project and none of our containers: report it, do not tear anything down."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None):
        calls.append(cmd)
        if cmd[:3] == ["docker", "compose", "stop"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        if cmd[:3] == ["docker", "ps", "-a"]:
            return _completed(stdout="somebody-elses-container\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is False
    assert ["docker", "compose", "down"] not in calls
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)


def test_docker_stop_treats_a_vanished_container_as_already_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container removed between listing and stopping is the goal state, not an error."""

    def fake_run(cmd: list[str], cwd: Path | None = None):
        if cmd[:3] == ["docker", "compose", "stop"]:
            return _completed(returncode=1, stderr="no configuration file provided")
        if cmd[:3] == ["docker", "ps", "-a"]:
            return _completed(stdout=f"{SPEC.db}\n{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "stop"]:
            return _completed(
                returncode=1, stderr=f"Error response from daemon: No such container: {cmd[2]}"
            )
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is True


def test_start_raises_docker_command_error_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-zero `docker` exit surfaces as `DockerCommandError`."""
    monkeypatch.setattr(docker.runner, "run", lambda cmd, cwd=None: _completed(1, "", "boom"))
    with pytest.raises(docker.DockerCommandError):
        docker.start(Path("/tmp/wow"))


def test_status_returns_running_container_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """`status()` parses `docker ps --format '{{.Names}}'` into a name list."""
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None: _completed(0, "ac-database\nac-worldserver\n", ""),
    )
    assert docker.status() == ["ac-database", "ac-worldserver"]


def test_health_returns_status_or_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """`health()` returns the inspect status, or `unknown` on failure/empty."""
    monkeypatch.setattr(docker.runner, "run", lambda cmd, cwd=None: _completed(0, "healthy", ""))
    assert docker.health("ac-database") == "healthy"

    monkeypatch.setattr(docker.runner, "run", lambda cmd, cwd=None: _completed(1, "", ""))
    assert docker.health("missing") == "unknown"


def test_port_conflicts_detects_binding_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """A container publishing 8085 shows up in the conflict list."""
    # docker ps -format "<name>\t<ports>" with a host-side publish for 8085.
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None: _completed(
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
        lambda cmd, cwd=None: _completed(0, "ac-database\t3306/tcp\n", ""),
    )
    assert docker.port_conflicts((3724, 8085)) == []


def test_wait_db_healthy_returns_true_once_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_db_healthy()` returns True as soon as health() reports healthy."""
    monkeypatch.setattr(docker.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(docker.runner, "run", lambda cmd, cwd=None: _completed(0, "healthy", ""))
    assert docker.wait_db_healthy("ac-database", timeout=10, interval=0.01) is True


def test_wait_db_healthy_times_out_if_never_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_db_healthy()` returns False once the deadline passes."""
    fake_time = [0.0]
    monkeypatch.setattr(docker.time, "monotonic", lambda: fake_time[0])
    monkeypatch.setattr(
        docker.time, "sleep", lambda seconds: fake_time.__setitem__(0, fake_time[0] + seconds)
    )
    monkeypatch.setattr(docker.runner, "run", lambda cmd, cwd=None: _completed(0, "starting", ""))
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

    def fake_run(cmd: list[str], cwd: Path | None = None):
        if cmd[:2] == ["docker", "ps"]:
            return _completed(0, "ac-authserver\nac-worldserver\n", "")
        if cmd[:2] == ["docker", "logs"] and cmd[2] == "ac-authserver":
            return _completed(0, "listening on 127.0.0.1:3724", "")
        if cmd[:2] == ["docker", "logs"] and cmd[2] == "ac-worldserver":
            return _completed(0, "World initialized... ready...", "")
        return _completed(0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert (
        docker.wait_ready(
            "ac-authserver", "ac-worldserver", "127.0.0.1", 3724, timeout=10, interval=0.01
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

    def fake_run(cmd: list[str], cwd: Path | None = None):
        if cmd[:2] == ["docker", "ps"]:
            calls["ps"] += 1
            if calls["ps"] == 1:
                return _completed(1, "", "the docker daemon is restarting")
            return _completed(0, "ac-authserver\nac-worldserver\n", "")
        if cmd[:2] == ["docker", "logs"] and cmd[2] == "ac-authserver":
            return _completed(0, "listening on 127.0.0.1:3724", "")
        if cmd[:2] == ["docker", "logs"] and cmd[2] == "ac-worldserver":
            return _completed(0, "ready...", "")
        return _completed(0, "", "")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    # Must not raise DockerCommandError despite the first docker ps failing.
    assert (
        docker.wait_ready(
            "ac-authserver", "ac-worldserver", "127.0.0.1", 3724, timeout=10, interval=0.01
        )
        is True
    )
    assert calls["ps"] >= 2


def test_wait_ready_rejects_non_positive_interval() -> None:
    """A zero/negative interval is rejected rather than busy-looping."""
    with pytest.raises(ValueError):
        docker.wait_ready("a", "w", "127.0.0.1", 3724, interval=0)


def test_wait_db_healthy_for_uses_spec_db_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """`wait_db_healthy_for()` reads the container name from the spec."""
    seen: list[str] = []

    def fake_run(cmd: list[str], cwd: Path | None = None):
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
        lambda cmd, cwd=None: _completed(0, "other\t0.0.0.0:9999->9999/tcp\n", ""),
    )
    spec = docker.ContainerSpec(db="d", auth="a", world="w", ports=(9999,))
    assert docker.port_conflicts_for(spec) == ["other"]


def test_docker_ctl_convenience_wrappers_delegate_to_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docker_ctl`'s pre-bound wrappers use `SPEC`, not caller-supplied names."""
    monkeypatch.setattr(
        docker.runner,
        "run",
        lambda cmd, cwd=None: _completed(0, "ac-worldserver\t0.0.0.0:8085->8085/tcp\n", ""),
    )
    assert docker_ctl.port_conflicts_here() == ["ac-worldserver"]


def test_start_staged_never_re_runs_the_one_shot_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """`compose up -d` restarts ac-db-import; dml-start.sh warns that "was killing the database"."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(
                cmd, 0, "ac-database\nac-authserver\nac-worldserver\n", ""
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runner, "run", fake_run)
    used_staged = docker.start_staged(SPEC, Path("/srv"), wait_healthy=lambda _c: True)

    assert used_staged is True
    assert not any(c[:3] == ["docker", "compose", "up"] for c in calls)
    assert [c for c in calls if c[:2] == ["docker", "start"]] == [
        ["docker", "start", SPEC.db],
        ["docker", "start", SPEC.auth],
        ["docker", "start", SPEC.world],
    ]


def test_start_staged_falls_back_to_compose_up_when_a_container_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A server that was never brought up MUST go through compose — that is what creates it."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(cmd, 0, "ac-database\n", "")  # world/auth missing
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runner, "run", fake_run)
    used_staged = docker.start_staged(SPEC, Path("/srv"), wait_healthy=lambda _c: True)

    assert used_staged is False
    assert ["docker", "compose", "up", "-d"] in calls
    assert not any(c[:2] == ["docker", "start"] for c in calls)


def test_start_staged_starts_auth_and_world_even_if_the_db_never_reports_healthy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unhealthy DB is worth a warning, not a refusal to start the rest."""
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return subprocess.CompletedProcess(
                cmd, 0, "ac-database\nac-authserver\nac-worldserver\n", ""
            )
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(runner, "run", fake_run)
    assert docker.start_staged(SPEC, Path("/srv"), wait_healthy=lambda _c: False) is True
    assert len([c for c in calls if c[:2] == ["docker", "start"]]) == 3


def test_start_staged_recreates_when_a_compose_file_changed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`docker start` reuses a container as created, so a changed setting would be ignored.

    Republishing a port or setting an AC_* env var edits a compose file; if the
    staged path ran anyway, the change would silently do nothing. Recreating
    costs a re-run of the DB import, which is the lesser evil when the user has
    just changed something on purpose.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner,
        "run",
        _hash_aware_runner(calls, configured="db h1 auth h2 world hZZZ", deployed=_MATCHING_HASHES),
    )
    assert docker.start_staged(SPEC, tmp_path, wait_healthy=lambda _c: True) is False
    assert ["docker", "compose", "up", "-d"] in calls
    assert not any(c[:2] == ["docker", "start"] for c in calls)


def test_start_staged_keeps_the_staged_path_when_the_config_still_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The common case: the deployed containers already match the compose files."""
    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "run", _hash_aware_runner(calls))
    assert docker.start_staged(SPEC, tmp_path, wait_healthy=lambda _c: True) is True
    assert ["docker", "compose", "up", "-d"] not in calls


def test_compose_config_changed_ignores_a_service_with_no_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pruned one-shot import container must not drag the project into a recreate.

    The import exits as soon as it succeeds; someone tidying up exited
    containers removes it. That is not a configuration change, and treating it
    as one would re-run the very import this whole path exists to avoid.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(
        runner,
        "run",
        _hash_aware_runner(
            calls,
            configured="db h1 auth h2 world h3 import h4",
            deployed="db h1 auth h2 world h3",
        ),
    )
    assert docker.compose_config_changed(tmp_path) is False


def test_compose_config_changed_treats_an_unreadable_answer_as_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unreadable answer must not trigger a needless import re-run."""
    monkeypatch.setattr(
        runner, "run", lambda cmd, cwd=None: subprocess.CompletedProcess(cmd, 1, "", "boom")
    )
    assert docker.compose_config_changed(tmp_path) is False

    calls: list[list[str]] = []
    monkeypatch.setattr(runner, "run", _hash_aware_runner(calls, deployed=""))
    assert docker.compose_config_changed(tmp_path) is False


def test_compose_config_changed_does_not_latch_after_a_partial_recreate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression that killed the mtime version: a db-only edit, then quiet again.

    `compose up -d` recreates only the services whose own config changed, so the
    world container keeps its original creation time. The old mtime comparison
    stayed True from then on and sent every single start through
    `compose up -d`, re-running the DB import each time. Hashes settle instead.
    """
    calls: list[list[str]] = []
    edited = _hash_aware_runner(
        calls, configured="db hNEW auth h2 world h3", deployed=_MATCHING_HASHES
    )
    monkeypatch.setattr(runner, "run", edited)
    assert docker.compose_config_changed(tmp_path) is True

    # `compose up -d` recreated the database only; world and auth are untouched.
    settled = _hash_aware_runner(
        calls,
        configured="db hNEW auth h2 world h3",
        deployed="db hNEW auth h2 world h3",
    )
    monkeypatch.setattr(runner, "run", settled)
    assert docker.compose_config_changed(tmp_path) is False
