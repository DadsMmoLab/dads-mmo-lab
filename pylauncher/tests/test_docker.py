"""Tests for the shared Docker lifecycle (`yulon.docker` and WotLK wrapper).

All subprocess calls are mocked at the `yulon.runner.run` boundary, so nothing
here requires a real Docker daemon — mirroring roadmap 1.3's "mocked control
flow" intent. The integration suite (Phase 1.5, `tests/fixture.md`) is where a
real AzerothCore compose project gets exercised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import docker
from yulon.controller_wow_wotlk import docker_ctl

SPEC = docker_ctl.SPEC


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

    def fake_run(cmd: list[str], cwd: Path | None = None):
        calls.append(cmd)
        cwds.append(cwd)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    server_dir = Path("/tmp/wow")
    assert docker.start_staged(SPEC, server_dir) is True
    assert calls == [["docker", "compose", "up", "-d", "--no-deps", SPEC.db, SPEC.auth, SPEC.world]]
    assert cwds == [server_dir], "must address the project by directory, not by global name"
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
    monkeypatch.setattr(
        docker.runner, "run", lambda cmd, cwd=None: (calls.append(cmd), _completed())[1]
    )
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
        docker.runner, "run", lambda cmd, cwd=None: (calls.append(cmd), _completed())[1]
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
        lambda cmd, cwd=None: _completed(stdout='{"name": "wow_server2", "services": {}}'),
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
        docker.runner, "run", lambda cmd, cwd=None: (called.append(cmd), _completed())[1]
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
        docker.runner, "run", lambda cmd, cwd=None: _completed(stdout='{"name": "srv"}')
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
        docker.runner, "run", lambda cmd, cwd=None: _completed(returncode=1, stderr="no such file")
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

    def fake_run(cmd: list[str], cwd: Path | None = None):
        seen.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="2026-08-22T01:24:53.575296627Z\n")
        if cmd[:2] == ["docker", "logs"]:
            scoped = "--since" in cmd
            if cmd[-1] == SPEC.auth:
                return _completed(stdout="Added realm at 127.0.0.1:8085\n")
            return _completed(stdout=this_run if scoped else whole_history)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert (
        docker.wait_ready(SPEC.auth, SPEC.world, "127.0.0.1", 8085, timeout=0.2, interval=0.1)
        is False
    )
    assert any("--since" in cmd for cmd in seen), "readiness must scope logs to the current run"


def test_wait_ready_still_succeeds_when_this_run_is_actually_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scoping must not break the case it exists to make honest."""

    def fake_run(cmd: list[str], cwd: Path | None = None):
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout=f"{SPEC.auth}\n{SPEC.world}\n")
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="2026-08-22T01:24:53.575296627Z\n")
        if cmd[:2] == ["docker", "logs"]:
            if cmd[-1] == SPEC.auth:
                return _completed(stdout="Added realm at 127.0.0.1:8085\n")
            return _completed(stdout="World initialized, ready...\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert (
        docker.wait_ready(SPEC.auth, SPEC.world, "127.0.0.1", 8085, timeout=2.0, interval=0.1)
        is True
    )


def test_logs_without_a_readable_start_time_falls_back_to_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable start time must degrade to the old behaviour, not to silence."""

    def fake_run(cmd: list[str], cwd: Path | None = None):
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
    compose_stop_fails: bool = False,
    stop_really_works: bool = True,
):
    """A `runner.run` double for the stop path.

    `running` is what `docker ps` reports; `owner` is the compose project label
    every container claims. A container name proves nothing about ownership, so
    a test can make those two disagree.
    """
    live = set() if running is None else set(running)

    def fake_run(cmd: list[str], cwd=None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "' + PROJECT + '"}')
        if cmd[:3] == ["docker", "compose", "stop"]:
            if compose_stop_fails:
                return _completed(returncode=1, stderr="no configuration file provided")
            return _completed()
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="" if owner is None else owner + "\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            if stop_really_works:
                live.discard(cmd[2])
            return _completed()
        return _completed()

    return fake_run


def test_stop_staged_uses_compose_stop_so_the_containers_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compose stop` keeps every container and honours the project's depends_on order."""
    calls: list[list[str]] = []
    monkeypatch.setattr(docker.runner, "run", _stop_runner(calls))
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is True
    assert ["docker", "compose", "stop"] in calls
    assert ["docker", "compose", "down"] not in calls


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
    with pytest.raises(docker.DockerCommandError, match="different compose project"):
        docker.stop_staged(SPEC, Path("/tmp/install-b"))
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls), "stopped a foreign server"


def test_stop_staged_gives_up_rather_than_guessing_when_ownership_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable label is not permission to fall back to matching by name."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(
            calls,
            running={SPEC.db, SPEC.auth, SPEC.world},
            owner=None,
            compose_stop_fails=True,
        ),
    )
    assert docker.stop_staged(SPEC, Path("/tmp/wow")) is False
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)
    assert ["docker", "compose", "down"] not in calls


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
        _stop_runner(calls, running={SPEC.db, SPEC.auth, SPEC.world}),
    )
    assert docker.stop_staged(SPEC, Path("/tmp/moved-install")) is True
    assert [cmd for cmd in calls if cmd[:2] == ["docker", "stop"]] == [
        ["docker", "stop", SPEC.world],
        ["docker", "stop", SPEC.auth],
        ["docker", "stop", SPEC.db],
    ]


def test_stop_staged_raises_when_the_containers_will_not_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Still up after being told twice: say so rather than claiming success."""
    calls: list[list[str]] = []
    monkeypatch.setattr(
        docker.runner,
        "run",
        _stop_runner(calls, running={SPEC.world}, stop_really_works=False),
    )
    with pytest.raises(docker.DockerCommandError, match="still running after stop"):
        docker.stop_staged(SPEC, Path("/tmp/wow"))


def test_docker_stop_treats_a_vanished_container_as_already_stopped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A container removed between listing and stopping is the goal state, not an error."""
    live = {SPEC.world}

    def fake_run(cmd: list[str], cwd=None):
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "' + PROJECT + '"}')
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout=PROJECT + "\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            live.discard(cmd[2])
            return _completed(
                returncode=1, stderr="Error response from daemon: No such container: " + cmd[2]
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
        docker.runner, "run", lambda cmd, cwd=None: _completed(stdout='{"name": "srv"}')
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
        docker.runner, "run", lambda cmd, cwd=None: _completed(stdout='{"name": "srv"}')
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

    def fake_run(cmd: list[str], cwd=None):
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
            live.discard(cmd[2])
            return _completed()
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, tmp_path) is True
    assert [cmd for cmd in calls if cmd[:2] == ["docker", "stop"]] == [
        ["docker", "stop", SPEC.world],
        ["docker", "stop", SPEC.auth],
        ["docker", "stop", SPEC.db],
    ]


def test_stop_staged_says_so_rather_than_claiming_a_stop_it_cannot_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Unprovable ownership with our names running is an error, not a quiet False.

    `Controller.stop()` discards the return value, so returning False here would
    leave the UI reporting a stopped server while it is still up.
    """

    def fake_run(cmd: list[str], cwd=None):
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

    def fake_run(cmd: list[str], cwd=None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "renamed-by-the-user"}')
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="original-name\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            live.discard(cmd[2])
            return _completed()
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    with pytest.raises(docker.DockerCommandError, match="different compose project"):
        docker.stop_staged(SPEC, tmp_path)
    assert live == {SPEC.db, SPEC.auth, SPEC.world}, "stopped what it could not prove was its own"
    assert not any(cmd[:2] == ["docker", "stop"] for cmd in calls)


def test_stop_staged_stops_a_moved_install_that_WAS_pinned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pin is what makes a moved folder unambiguous, and therefore stoppable."""
    (tmp_path / ".env").write_text(
        "COMPOSE_PROJECT_NAME=original-name\n", encoding="utf-8", newline="\n"
    )
    calls: list[list[str]] = []
    live = {SPEC.db, SPEC.auth, SPEC.world}

    def fake_run(cmd: list[str], cwd=None):
        calls.append(cmd)
        if cmd[:4] == ["docker", "compose", "config", "--format"]:
            return _completed(stdout='{"name": "renamed-by-the-user"}')
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="original-name\n")
        if cmd[:2] == ["docker", "ps"]:
            return _completed(stdout="".join(n + "\n" for n in sorted(live)))
        if cmd[:2] == ["docker", "stop"]:
            live.discard(cmd[2])
            return _completed()
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.stop_staged(SPEC, tmp_path) is True
    assert [cmd for cmd in calls if cmd[:2] == ["docker", "stop"]] == [
        ["docker", "stop", SPEC.world],
        ["docker", "stop", SPEC.auth],
        ["docker", "stop", SPEC.db],
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
        lambda cmd, cwd=None: _completed(stdout='{"name": "just-the-folder-name"}'),
    )
    assert docker.install_project(SPEC, tmp_path) == "pinned-name"


def test_a_stop_cannot_be_confirmed_when_docker_will_not_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An unanswerable verification is not a pass — the premise is verify, don't believe."""
    (tmp_path / ".env").write_text("COMPOSE_PROJECT_NAME=proj\n", encoding="utf-8", newline="\n")

    def fake_run(cmd: list[str], cwd=None):
        if cmd[:2] == ["docker", "ps"]:
            return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(stdout="proj\n")
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    with pytest.raises(docker.DockerCommandError, match="cannot be confirmed"):
        docker.stop_staged(SPEC, tmp_path)
