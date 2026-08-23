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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
        calls.append(cmd)
        return _completed()

    monkeypatch.setattr(docker.runner, "run", fake_run)
    docker.stop(Path("/tmp/wow"))
    assert calls == [["docker", "compose", "down"]]


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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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
        docker.wait_ready(SPEC.auth, SPEC.world, "127.0.0.1", 8085, timeout=0.2, interval=0.1)
        is False
    )
    assert any("--since" in cmd for cmd in seen), "readiness must scope logs to the current run"


def test_wait_ready_still_succeeds_when_this_run_is_actually_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The scoping must not break the case it exists to make honest."""

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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
        docker.wait_ready(SPEC.auth, SPEC.world, "127.0.0.1", 8085, timeout=2.0, interval=0.1)
        is True
    )


def test_logs_without_a_readable_start_time_falls_back_to_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable start time must degrade to the old behaviour, not to silence."""

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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
                live.discard(cmd[2])
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
    assert ["docker", "compose", "stop"] in calls
    assert ["docker", "compose", "down"] not in calls
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
    assert ["docker", "compose", "stop"] in calls
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
    assert ["docker", "compose", "down"] not in calls
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
            live.discard(cmd[2])
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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
        docker.wait_ready(SPEC.auth, SPEC.world, "127.0.0.1", 8085, timeout=0.3, interval=0.1)
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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
    assert [cmd[1:3] for cmd in off_path_docker] == [
        ["compose", "up"],
        ["compose", "config"],
        ["inspect", SPEC.world],
        ["stop", SPEC.world],
        ["inspect", SPEC.world],
        ["inspect", SPEC.world],
        ["logs", SPEC.world],
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
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
        ("wait_ready()", lambda: docker.wait_ready("a", "w", "h", 1, timeout=60.0, interval=0.02)),
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

    def fake_run(cmd: list[str], cwd: Path | None = None, timeout: float | None = None):
        if cmd[1] == "ps":
            if next(stumbles):
                return _completed(returncode=1, stderr="Cannot connect to the Docker daemon")
            return _completed(stdout="ac-authserver\nac-worldserver\n")
        if cmd[1] == "inspect":
            return _completed(stdout="running\t2026-08-23T00:00:00Z")
        return _completed(stdout="realm.example:3724 ready...")

    monkeypatch.setattr(docker.runner, "run", fake_run)
    assert docker.wait_ready(SPEC.auth, SPEC.world, "realm.example", 3724, 5.0, 0.01) is True
