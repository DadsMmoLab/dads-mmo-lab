"""T658: a Docker Compose older than 2.10 is refused before it can stop the import or a Start.

A Steam Deck player's install died at the database import with `no such service:
ac-database`, and Start never got past it either. Measured on m910q (2026-10-10) with
the official release binaries against a copy of a generated WotLK compose file: Compose
2.5.0-2.9.0 stop `compose up --no-deps <service>` whenever the service has a
`depends_on` outside the selection -- the import (`up --no-deps ac-db-import`) and every
Start (`up -d --no-deps <db> <auth> <world>`) -- while 2.10.0 and every later release
measured (to 5.6.0) run both.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from yulon import docker, platform, runner
from yulon.catalog import preflight
from yulon.controller import Controller, StartRefused
from yulon.docker import ContainerSpec

SPEC = ContainerSpec(db="t-db", auth="t-auth", world="t-world", ports=(1111, 2222))

REAL_COMPOSE_REFUSAL = docker.compose_refusal
"""Read at import, before `conftest._compose_is_new_enough` stands in for it in each test."""


@pytest.fixture(autouse=True)
def _the_real_compose_question(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(docker, "compose_refusal", REAL_COMPOSE_REFUSAL)


def _done(code: int = 0, out: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], code, out, "")


# ------------------------------------------------------------------ the version


@pytest.mark.parametrize(
    ("said", "version"),
    [
        ("Docker Compose version v2.6.1\n", (2, 6, 1)),
        ("Docker Compose version 5.5.0\n", (5, 5, 0)),
        ("Docker Compose version v2.39.1-desktop.1\n", (2, 39, 1)),
        ("", None),
        ("docker: 'compose' is not a docker command.\n", None),
    ],
)
def test_the_version_is_read_from_composes_own_answer(said: str, version: object) -> None:
    """Docker's builds write `v2.6.1`, Arch's `5.5.0`, Desktop adds a suffix."""
    assert platform.parse_compose_version(said) == version


@pytest.mark.parametrize(
    ("version", "too_old"),
    [
        ((2, 5, 0), True),
        ((2, 9, 0), True),
        ((2, 4, 1), True),
        ((2, 10, 0), False),
        ((2, 39, 1), False),
        ((5, 6, 0), False),
        (None, False),
    ],
)
def test_older_than_two_ten_is_too_old_and_unknown_is_not(
    version: tuple[int, int, int] | None, too_old: bool
) -> None:
    assert platform.compose_too_old(version) is too_old


def test_compose_version_asks_compose_and_reads_its_answer() -> None:
    asked: list[list[str]] = []

    def run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        asked.append(argv)
        return _done(0, "Docker Compose version v2.6.1\n")

    assert platform.compose_version(run) == (2, 6, 1)
    assert asked == [["docker", "compose", "version"]]


def test_compose_version_that_fails_is_not_established() -> None:
    assert platform.compose_version(lambda argv, **_kw: _done(1, "")) is None


def test_the_users_own_plugin_is_found_where_docker_looks_first(tmp_path: Path) -> None:
    """`~/.docker/cli-plugins` beats the system's folders; `$DOCKER_CONFIG` moves it."""
    assert platform.users_compose_plugin(tmp_path, env={}) is None
    plugin = tmp_path / ".docker" / "cli-plugins" / "docker-compose"
    plugin.parent.mkdir(parents=True)
    plugin.write_text("old")
    assert platform.users_compose_plugin(tmp_path, env={}) == plugin
    moved = tmp_path / "cfg" / "cli-plugins" / "docker-compose"
    moved.parent.mkdir(parents=True)
    moved.write_text("old")
    assert (
        platform.users_compose_plugin(tmp_path, env={"DOCKER_CONFIG": str(tmp_path / "cfg")})
        == moved
    )


def test_the_sentence_names_both_versions_the_package_and_the_users_copy(tmp_path: Path) -> None:
    plugin = tmp_path / "docker-compose"
    said = platform.compose_too_old_sentence((2, 6, 1), linux=True, plugin=plugin)
    assert "2.6.1" in said
    assert "2.10.0" in said
    assert "no such service" in said
    assert "sudo pacman -S docker-compose" in said
    assert "sudo apt install docker-compose-v2" in said
    assert f"rm {plugin}" in said
    assert "docker compose version" in said
    assert "Docker Desktop" not in said
    desktop = platform.compose_too_old_sentence((2, 6, 1), linux=False)
    assert "Docker Desktop" in desktop
    assert "pacman" not in desktop


# ------------------------------------------------------------------- preflight


def test_preflight_refuses_a_compose_that_stops_the_import(tmp_path: Path) -> None:
    """The player's Compose answers `docker compose version`, so T56's row passed it."""
    plugin = tmp_path / "docker-compose"
    facts = preflight.Facts(
        platform_id="linux",
        docker_ready=True,
        compose_ready=True,
        compose_version=(2, 6, 1),
        compose_plugin=plugin,
    )
    check = preflight._compose_check(facts)
    assert check.name == preflight.COMPOSE_CHECK
    assert check.verdict == "refuse"
    assert "2.6.1" in check.detail
    assert f"rm {plugin}" in (check.remedy or "")


@pytest.mark.parametrize("version", [(2, 10, 0), (5, 6, 0), None])
def test_preflight_passes_a_compose_that_works_or_could_not_be_read(
    version: tuple[int, int, int] | None,
) -> None:
    facts = preflight.Facts(
        platform_id="linux", docker_ready=True, compose_ready=True, compose_version=version
    )
    assert preflight._compose_check(facts).verdict == "pass"


def test_gather_reads_the_version_only_of_a_compose_that_answered(tmp_path: Path) -> None:
    from yulon.catalog.catalog import load_catalog

    entry = load_catalog().get("wow-wotlk")
    asked: list[str] = []

    def version() -> tuple[int, int, int]:
        asked.append("version")
        return (2, 6, 1)

    def plugin() -> Path:
        asked.append("plugin")
        return tmp_path / "docker-compose"

    common: dict[str, object] = {
        "platform_id": lambda: "linux",
        "vm_resources": lambda: None,
        "data_root": lambda: None,
        "disk_free": lambda _p: 100 * 1024**3,
        "dir_problem": lambda _p: None,
        "bind_mount_ok": lambda _p: True,
        "port_conflicts": lambda: [],
        "probe_port": lambda host, port: platform.PortProbe(host, port, "unknown", ""),
        "selinux": lambda: None,
        "fs_type": lambda _p: None,
        "in_wsl": lambda: False,
        "bind_port": lambda host, port: platform.PortBind(host, port, "free", ""),
        "port_holders": lambda _ports: docker.PortHolders(),
        "compose_version": version,
        "compose_plugin": plugin,
    }
    facts = preflight.gather(
        entry, tmp_path / "s", docker_ready=lambda: True, compose_ready=lambda: True, **common
    )
    assert facts.compose_version == (2, 6, 1)
    assert facts.compose_plugin == tmp_path / "docker-compose"
    asked.clear()
    facts = preflight.gather(
        entry, tmp_path / "s", docker_ready=lambda: True, compose_ready=lambda: False, **common
    )
    assert facts.compose_version is None
    assert asked == []


# ------------------------------------------------------------------- the Start


class _Docker:
    """A runner double: `compose version` says `self.version`; `up` is recorded."""

    def __init__(self, version: str) -> None:
        self.version = version
        self.calls: list[list[str]] = []

    def __call__(self, cmd: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(list(cmd))
        if cmd[:3] == ["docker", "compose", "version"]:
            return _done(0, self.version)
        return _done()


def test_start_is_refused_on_a_compose_that_stops_it_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake = _Docker("Docker Compose version v2.6.1\n")
    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(platform, "users_compose_plugin", lambda: None)
    with pytest.raises(StartRefused) as refused:
        Controller(SPEC, tmp_path).start()
    assert "2.6.1" in str(refused.value)
    assert "no such service" in str(refused.value)
    assert not any(cmd[:3] == ["docker", "compose", "up"] for cmd in fake.calls)


@pytest.mark.parametrize("said", ["Docker Compose version v2.10.0\n", "", "garbled\n"])
def test_start_goes_on_with_a_compose_that_works_or_could_not_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, said: str
) -> None:
    fake = _Docker(said)
    monkeypatch.setattr(runner, "run", fake)
    Controller(SPEC, tmp_path).refuse_an_old_compose()
    assert ["docker", "compose", "version"] in fake.calls


def test_the_start_asks_compose_where_the_server_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server inside a WSL distro asks that distro's Compose, and names no Windows file."""
    seen: list[tuple[list[str], str | None]] = []

    def fake(
        argv: list[str],
        cwd: object = None,
        timeout: object = None,
        *,
        wsl_distro: str | None = None,
    ):
        seen.append((argv, wsl_distro))
        return _done(0, "Docker Compose version v2.6.1\n")

    monkeypatch.setattr(docker, "_docker", fake)
    monkeypatch.setattr(platform, "users_compose_plugin", lambda: Path("/nope/docker-compose"))
    said = docker.compose_refusal(wsl_distro="Ubuntu")
    assert seen == [(["compose", "version"], "Ubuntu")]
    assert said is not None and "sudo pacman -S docker-compose" in said
    assert "/nope/docker-compose" not in said


@pytest.mark.parametrize("press", ["refuse_before_a_stop", "stop_conflicting_and_start"])
def test_a_press_that_stops_something_first_refuses_before_the_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, press: str
) -> None:
    """Restart, Recreate and "stop the other server" leave everything as it was."""
    fake = _Docker("Docker Compose version 2.9.0\n")
    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(platform, "users_compose_plugin", lambda: None)
    with pytest.raises(StartRefused):
        getattr(Controller(SPEC, tmp_path), press)()
    assert not any(
        cmd[:3] in (["docker", "compose", "stop"], ["docker", "stop"]) for cmd in fake.calls
    )
    assert not any(cmd[:3] == ["docker", "compose", "up"] for cmd in fake.calls)


def test_stop_the_other_and_start_stops_nothing_on_a_compose_too_old(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """With another server on our ports, the refusal still comes before its stop."""
    fake = _Docker("Docker Compose version v2.5.0\n")

    def run(cmd: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        if cmd[:2] == ["docker", "ps"]:
            fake.calls.append(list(cmd))
            return _done(0, "other-world\t0.0.0.0:2222->2222/tcp\n")
        return fake(cmd, **kw)

    monkeypatch.setattr(runner, "run", run)
    monkeypatch.setattr(platform, "users_compose_plugin", lambda: None)
    with pytest.raises(StartRefused):
        Controller(SPEC, tmp_path).stop_conflicting_and_start()
    assert not any("stop" in cmd or "kill" in cmd for cmd in fake.calls)
