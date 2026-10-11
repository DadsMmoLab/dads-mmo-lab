"""T671: Start knows every host port its server publishes, and binds them before anything starts.

Every database but WoW Unbound's publishes 127.0.0.1:3306. A server's database is
left running alone by design (an SQL module installed on a stopped server, a Start
refused after its database was asked), and the next Start of ANOTHER server used to
look only at the auth and world ports: no "Stop the other server" offer, and
compose then failed on 3306 with the database and world of the second server half
up. A program holding a port at Start (usertest U-6, measured on Linux) did the same.

Driven through `Controller.start()` with `runner.run` answered by a table (the seam
`tests/test_controller.py` uses), and through the real bind probe where the point
is a real listener.
"""

from __future__ import annotations

import socket
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import controller as controller_module
from yulon import docker, platform, runner
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller, PortConflictError, PortTaken

WOTLK = next(entry for entry in load_catalog().games if entry.id == "wow-wotlk")
TBC_DB_ALONE = "tbc-db\t127.0.0.1:3306->3306/tcp\n"


@pytest.fixture(autouse=True)
def _server_dirs_are_treated_as_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "_cwd_is_missing", lambda cwd: False)


def _completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


class _Docker:
    """`docker ps` from a table (`name<TAB>ports` lines); records every argv."""

    def __init__(self, ps_lines: str = "") -> None:
        self.ps_lines = ps_lines
        self.calls: list[list[str]] = []

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append(cmd)
        if cmd[:2] == ["docker", "ps"]:
            if "{{.Ports}}" in cmd[-1]:
                return _completed(0, self.ps_lines)
            names = [line.split("\t")[0] for line in self.ps_lines.splitlines() if line.strip()]
            return _completed(0, "".join(name + "\n" for name in names))
        if cmd[:5] == ["docker", "compose", "up", "-d", "--no-deps"]:
            self.ps_lines += "".join(f"{name}\n" for name in cmd[5:])
            return _completed()
        if cmd[:2] == ["docker", "inspect"]:
            return _completed(0, "healthy\n")
        return _completed()

    def started_anything(self) -> bool:
        return any(c[:3] in (["docker", "compose", "up"], ["docker", "start"]) for c in self.calls)


@pytest.fixture
def daemon(monkeypatch: pytest.MonkeyPatch) -> _Docker:
    fake = _Docker()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture
def real_bind_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo conftest's always-free probe: these tests bind for real."""
    monkeypatch.setattr(controller_module, "bind_host_port", platform.bind_tcp)


@pytest.fixture
def listener() -> Iterator[int]:
    """A real program (this test) listening on a loopback port nothing else chose."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    try:
        yield sock.getsockname()[1]
    finally:
        sock.close()


# --- another server's database, up alone on 3306 ---------------------------------


def test_another_servers_lone_database_on_3306_is_a_port_conflict_at_start(
    daemon: _Docker, tmp_path: Path
) -> None:
    """The offer to stop the other server must see its database, and nothing may start."""
    daemon.ps_lines = TBC_DB_ALONE
    with pytest.raises(PortConflictError) as caught:
        Controller(WOTLK.container_spec(), tmp_path).start()
    assert caught.value.containers == ["tbc-db"]
    assert caught.value.ports == (3306,), "the message must name the port that is taken"
    assert not daemon.started_anything()


def test_port_conflicts_names_a_lone_database_for_stop_the_other_server(
    daemon: _Docker, tmp_path: Path
) -> None:
    daemon.ps_lines = TBC_DB_ALONE + "ac-database\t127.0.0.1:3307->3306/tcp\n"
    assert Controller(WOTLK.container_spec(), tmp_path).port_conflicts() == ["tbc-db"]


def test_a_database_port_moved_in_dotenv_is_the_one_checked(
    daemon: _Docker, tmp_path: Path
) -> None:
    """The port-conflict remedy writes DOCKER_DB_EXTERNAL_PORT; 3306 is then not ours."""
    (tmp_path / ".env").write_text("DOCKER_DB_EXTERNAL_PORT=3316\n", encoding="utf-8")
    daemon.ps_lines = TBC_DB_ALONE
    assert Controller(WOTLK.container_spec(), tmp_path).port_conflicts() == []
    daemon.ps_lines = "tbc-db\t127.0.0.1:3316->3306/tcp\n"
    assert Controller(WOTLK.container_spec(), tmp_path).port_conflicts() == ["tbc-db"]


def test_stop_conflicting_stops_the_other_servers_lone_database(
    daemon: _Docker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon.ps_lines = TBC_DB_ALONE
    stopped: list[list[str]] = []
    monkeypatch.setattr(docker, "container_project", lambda name, **_k: "tbc")
    monkeypatch.setattr(docker, "project_containers", lambda project, **_k: ["tbc-db"])
    monkeypatch.setattr(docker, "container_working_dir", lambda name, **_k: None)
    monkeypatch.setattr(docker, "stop_containers", lambda names, **_k: stopped.append(names))
    assert Controller(WOTLK.container_spec(), tmp_path).stop_conflicting() == ["tbc-db"]
    assert stopped == [["tbc-db"]]


def test_stop_conflicting_refuses_a_server_a_job_of_this_yulon_holds(
    daemon: _Docker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restore runs the other server's database alone: "Stop the other server" must wait.

    Its own reservation would not stop it -- this process already holds that one, and a
    take in the same process is re-entrant -- so the in-process hold is what refuses.
    """
    other = tmp_path / "tbc"
    other.mkdir()
    (other / native.STATE_FILE).write_text("{}", encoding="utf-8")
    daemon.ps_lines = TBC_DB_ALONE
    stopped: list[list[str]] = []
    monkeypatch.setattr(docker, "container_project", lambda name, **_k: "tbc")
    monkeypatch.setattr(docker, "project_containers", lambda project, **_k: ["tbc-db"])
    monkeypatch.setattr(docker, "container_working_dir", lambda name, **_k: str(other))
    monkeypatch.setattr(docker, "stop_containers", lambda names, **_k: stopped.append(names))
    with docker.hold_the_server(other, "A restore of this server is running."):
        with pytest.raises(docker.ServerHeldError) as caught:
            Controller(WOTLK.container_spec(), tmp_path / "wotlk").stop_conflicting()
    assert "restore" in str(caught.value)
    assert stopped == []


# --- a program holding a port: the bind probe before anything starts -------------


@pytest.mark.usefixtures("real_bind_probe")
def test_a_program_on_a_port_refuses_the_start_before_any_container_starts(
    daemon: _Docker, tmp_path: Path, listener: int
) -> None:
    """U-6: nothing half up, and the sentence names the port, in plain words."""
    spec = docker.ContainerSpec(db="t-db", auth="t-auth", world="t-world", ports=(listener,))
    with pytest.raises(PortTaken) as caught:
        Controller(spec, tmp_path).start()
    said = str(caught.value)
    assert str(listener) in said and "Another program" in said
    assert not daemon.started_anything()


@pytest.mark.usefixtures("real_bind_probe")
def test_a_port_our_own_container_publishes_is_not_probed(
    daemon: _Docker, tmp_path: Path, listener: int
) -> None:
    """Our database up alone holds its port through Docker: that is no refusal."""
    spec = docker.ContainerSpec(db="t-db", auth="t-auth", world="t-world", ports=(listener,))
    daemon.ps_lines = f"t-db\t127.0.0.1:{listener}->3306/tcp\n"
    Controller(spec, tmp_path).start()
    assert any(c[:5] == ["docker", "compose", "up", "-d", "--no-deps"] for c in daemon.calls)


@pytest.mark.usefixtures("real_bind_probe")
def test_the_refusal_carries_the_bind_for_the_channel_rollback(
    daemon: _Docker, tmp_path: Path, listener: int
) -> None:
    """The Server tab undoes the command channel when the start failed on ITS port (T188)."""
    from yulon import channel_setup

    spec = docker.ContainerSpec(db="t-db", auth="t-auth", world="t-world", ports=(listener,))
    with pytest.raises(PortTaken) as caught:
        Controller(spec, tmp_path).start()
    assert channel_setup.blames_the_host_port(caught.value.detail, listener)


def test_a_port_freed_a_moment_later_does_not_refuse_the_start(
    daemon: _Docker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A server stopped just now may hand its listener back late: the probe asks twice."""
    answers = iter(["in_use", "free"])
    asked: list[int] = []

    def bind(host: str, port: int) -> platform.PortBind:
        asked.append(port)
        return platform.PortBind(host, port, next(answers, "free"), "test")

    monkeypatch.setattr(controller_module, "bind_host_port", bind)
    monkeypatch.setattr(controller_module, "_REBIND_PAUSE", 0.0)
    spec = docker.ContainerSpec(db="t-db", auth="t-auth", world="t-world", ports=(4321,))
    Controller(spec, tmp_path).start()
    assert asked == [4321, 4321]
    assert any(c[:5] == ["docker", "compose", "up", "-d", "--no-deps"] for c in daemon.calls)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_the_program_holding_a_port_is_named_where_linux_lets_us(listener: int) -> None:
    named = platform.listening_program(listener)
    assert named is not None and "python" in named.lower()


def test_no_program_is_named_for_a_port_nothing_listens_on() -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    free = sock.getsockname()[1]
    sock.close()
    assert platform.listening_program(free) is None


def test_windows_names_the_program_from_netstat_and_tasklist() -> None:
    netstat = (
        "\r\nActive Connections\r\n\r\n  Proto  Local Address          Foreign Address        "
        "State           PID\r\n"
        "  TCP    0.0.0.0:135            0.0.0.0:0              LISTENING       1100\r\n"
        "  TCP    127.0.0.1:3306         127.0.0.1:50122        ESTABLISHED     77\r\n"
        "  TCP    0.0.0.0:33060          0.0.0.0:0              LISTENING       88\r\n"
        "  TCP    127.0.0.1:3306         0.0.0.0:0              ABHÖREN         4242\r\n"
    )
    asked: list[list[str]] = []

    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        if argv[0] == "netstat":
            return _completed(0, netstat)
        return _completed(0, '"mysqld.exe","4242","Services","0","412,000 K"\r\n')

    assert platform.listening_program(3306, here="windows", run=run) == "mysqld.exe"
    assert asked[1][-1].endswith("4242") or "PID eq 4242" in " ".join(asked[1])


def test_macos_names_the_program_from_lsof() -> None:
    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _completed(0, "p812\ncmysqld\nf23\n")

    assert platform.listening_program(3306, here="macos", run=run) == "mysqld"


# --- Linux Docker Engine's own wording, when a port is taken after the check -------

# Measured on m910q, Docker Engine 29.7.2 / Compose 5.5.0 (audit lifecycle L1, usertest U-6).
_LINUX_PROGRAM_HOLDS_IT = (
    "Error response from daemon: failed to set up container networking: driver failed "
    "programming external connectivity on endpoint vanilla-realmd (b5454d): failed to bind "
    "host port 0.0.0.0:3724/tcp: address already in use\n"
)
_LINUX_CONTAINER_HOLDS_IT = (
    "Error response from daemon: driver failed programming external connectivity on endpoint "
    "t2-a (9f1c): Bind for 127.0.0.1:3399 failed: port is already allocated\n"
)


@pytest.mark.parametrize(("stderr", "port"), [(_LINUX_PROGRAM_HOLDS_IT, 3724)])
def test_linux_engines_port_wording_is_said_in_plain_words(
    stderr: str, port: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        docker, "_docker", lambda argv, cwd=None, timeout=None, **_kw: _fail(stderr)
    )
    with pytest.raises(docker.DockerRefusal) as caught:
        docker._run(["compose", "up", "-d"], cwd=Path("."))
    assert str(caught.value) == docker.blocked_port_sentence(port, "in_use", windows=False)
    assert caught.value.detail == stderr.strip(), "the channel rollback reads the daemon's text"


def _fail(stderr: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 1, "", stderr)


def test_a_port_another_container_holds_is_said_as_another_server_not_a_program(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Engine's "port is already allocated" is a container's: stop that server, not a program."""
    monkeypatch.setattr(
        docker,
        "_docker",
        lambda argv, cwd=None, timeout=None, **_kw: _fail(_LINUX_CONTAINER_HOLDS_IT),
    )
    with pytest.raises(docker.DockerRefusal) as caught:
        docker._run(["compose", "up", "-d"], cwd=Path("."))
    said = str(caught.value)
    assert said == docker.another_server_sentence(3399)
    assert "3399" in said and "server" in said and "program" not in said
    assert caught.value.detail == _LINUX_CONTAINER_HOLDS_IT.strip()


# --- a server inside a WSL distro: its loopback ports are the distro's, not this host's -------


def _probe(
    monkeypatch: pytest.MonkeyPatch, taken: set[int], *, publishers: object = None
) -> list[int]:
    asked: list[int] = []

    def bind(host: str, port: int) -> platform.PortBind:
        asked.append(port)
        return platform.PortBind(host, port, "in_use" if port in taken else "free", "test")

    def published(ports: object, **_kw: object) -> dict[int, tuple[str, ...]]:
        if isinstance(publishers, Exception):
            raise publishers
        return {}

    monkeypatch.setattr(controller_module, "bind_host_port", bind)
    monkeypatch.setattr(controller_module, "_REBIND_PAUSE", 0.0)
    monkeypatch.setattr(docker, "port_publishers", published)
    monkeypatch.setattr(platform, "listening_program", lambda port, **_kw: "mysqld.exe")
    return asked


def test_a_wsl_servers_database_port_is_not_probed_on_the_windows_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A native Windows MySQL on 3306 never stopped a WSL server, whose db is distro loopback."""
    asked = _probe(monkeypatch, {3306, 7878})
    Controller(WOTLK.container_spec(), tmp_path, wsl_distro="Ubuntu").refuse_a_taken_port()
    assert 3306 not in asked and 7878 not in asked
    assert 3724 in asked and 8085 in asked, "the game ports are still this host's to check"


def test_a_wsl_servers_game_port_taken_on_the_windows_side_still_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _probe(monkeypatch, {3724})
    with pytest.raises(PortTaken) as caught:
        Controller(WOTLK.container_spec(), tmp_path, wsl_distro="Ubuntu").refuse_a_taken_port()
    assert "3724" in str(caught.value)


def test_a_local_servers_database_port_is_probed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a distro the database is published on this host's own loopback."""
    _probe(monkeypatch, {3306})
    with pytest.raises(PortTaken) as caught:
        Controller(WOTLK.container_spec(), tmp_path).refuse_a_taken_port()
    assert "3306" in str(caught.value) and "mysqld.exe" in str(caught.value)


def test_a_docker_that_cannot_say_who_publishes_a_port_does_not_fail_the_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cannot tell is not a refusal, and not a Docker error on the Start line either."""
    _probe(monkeypatch, {3724}, publishers=docker.DockerCommandError("docker ps exited 1"))
    Controller(WOTLK.container_spec(), tmp_path).refuse_a_taken_port()
