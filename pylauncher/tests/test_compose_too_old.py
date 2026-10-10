"""T658: a Docker Compose older than 2.10 is found before it stops an AzerothCore import or Start.

A Steam Deck player's install died at the database import with `no such service:
ac-database`, and Start never got past it either. Measured on m910q (2026-10-10) with
the official release binaries against a copy of a generated WotLK compose file: Compose
2.5.0-2.9.0 stop `compose up --no-deps <service>` whenever the service has a
`depends_on` outside the selection -- the import (`up --no-deps ac-db-import`) and every
AzerothCore Start (`up -d --no-deps <db> <auth> <world>`, whose servers wait on the import)
-- while 2.10.0 and every later release measured (to 5.6.0) run both. CMaNGOS and
TrinityCore start only services whose dependencies start with them, and are not refused.
Where Yu'lon can, it offers to put Docker's current static Compose in the user's own
plugin folder instead of sending a Deck player to a read-only `pacman`.
"""

from __future__ import annotations

import hashlib
import stat
import subprocess
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest

from tests.support_native import Recorder, engine
from tests.test_docker import IMPORTED as DOCKER_IMPORTED
from tests.test_docker import UNIMPORTED, _probe, _repair_doubles
from yulon import docker, platform, runner
from yulon.catalog import preflight
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller import ComposeTooOld, Controller
from yulon.docker import ContainerSpec

AC_SPEC = ContainerSpec(
    db="t-db", auth="t-auth", world="t-world", ports=(1111, 2222), import_service="t-import"
)
"""An AzerothCore-shaped server: its servers wait on a compose import service."""
CMANGOS_SPEC = ContainerSpec(db="c-db", auth="c-realmd", world="c-mangosd", ports=(3333, 4444))
"""A CMaNGOS-shaped server: no import service, servers depend on the database only."""

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
        ("podman-compose version 1.0.6\npodman version 4.9.3\n", None),
        ("using podman 4.9.3\nDocker Compose version v2.6.1\n", (2, 6, 1)),
        ("", None),
        ("docker: 'compose' is not a docker command.\n", None),
    ],
)
def test_only_docker_composes_own_version_is_read(said: str, version: object) -> None:
    """podman-compose's `1.0.6` is another program's number, not an old Docker Compose."""
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


def test_the_users_plugin_path_is_where_docker_looks_first(tmp_path: Path) -> None:
    """`~/.docker/cli-plugins` beats the system's folders; `$DOCKER_CONFIG` moves it."""
    assert platform.users_compose_plugin_path(tmp_path, env={}) == (
        tmp_path / ".docker" / "cli-plugins" / "docker-compose"
    )
    moved = platform.users_compose_plugin_path(tmp_path, env={"DOCKER_CONFIG": "/cfg"})
    assert moved == Path("/cfg/cli-plugins/docker-compose")


@pytest.mark.parametrize(
    ("system", "machine", "distro", "offered"),
    [
        ("linux", "x86_64", None, True),
        ("linux", "aarch64", None, True),
        ("linux", "armv7l", None, False),
        ("win32", "x86_64", None, False),
        ("darwin", "arm64", None, False),
        ("linux", "x86_64", "Ubuntu", False),
    ],
)
def test_the_update_is_offered_only_where_it_can_run(
    system: str, machine: str, distro: str | None, offered: bool
) -> None:
    got = platform.compose_update_offered(wsl_distro=distro, system=system, machine=machine)
    assert got is offered


def test_the_sentence_offers_the_press_and_never_a_read_only_pacman() -> None:
    said = platform.compose_too_old_sentence((2, 6, 1), linux=True, offer=True)
    assert "2.6.1" in said and "2.10.0" in said
    assert "no such service" in said
    assert platform.UPDATE_COMPOSE_LABEL in said
    assert platform.COMPOSE_DOWNLOAD_VERSION in said
    assert "pacman" not in said
    assert "rm " not in said
    at_install = platform.compose_too_old_sentence(
        (2, 6, 1), linux=True, offer=True, asked_at_install=True
    )
    assert "Install asks" in at_install


def test_without_the_offer_the_sentence_names_the_packages() -> None:
    said = platform.compose_too_old_sentence((2, 6, 1), linux=True, offer=False)
    assert "sudo apt install docker-compose-v2" in said
    assert "docker compose version" in said
    assert platform.UPDATE_COMPOSE_LABEL not in said
    desktop = platform.compose_too_old_sentence((2, 6, 1), linux=False, offer=False)
    assert "Docker Desktop" in desktop
    assert "pacman" not in desktop


# ------------------------------------------------------------- the update press


BINARY = b"#!/bin/sh\necho 'Docker Compose version v5.5.1'\n"


def _pinned(monkeypatch: pytest.MonkeyPatch, body: bytes = BINARY) -> None:
    monkeypatch.setitem(
        platform.COMPOSE_DOWNLOADS,
        "x86_64",
        ("https://example.invalid/compose", hashlib.sha256(body).hexdigest()),
    )


def _download(body: bytes, asked: list[str]):  # type: ignore[no-untyped-def]
    def download(url: str, dest: Path) -> Path:
        asked.append(url)
        dest.write_bytes(body)
        return dest

    return download


def test_the_update_puts_a_checked_executable_compose_where_docker_looks_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pinned(monkeypatch)
    dest = tmp_path / "cli-plugins" / "docker-compose"
    asked: list[str] = []
    lines = list(
        platform.update_compose(
            dest=dest,
            machine="x86_64",
            download=_download(BINARY, asked),
            version=lambda: (5, 5, 1),
        )
    )
    assert asked == ["https://example.invalid/compose"]
    assert dest.read_bytes() == BINARY
    assert dest.stat().st_mode & stat.S_IXUSR
    assert not list(dest.parent.glob(".docker-compose.yulon-download*"))
    assert any("5.5.1" in line for line in lines), lines


def test_a_download_that_does_not_match_its_checksum_replaces_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pinned(monkeypatch)
    dest = tmp_path / "cli-plugins" / "docker-compose"
    dest.parent.mkdir()
    dest.write_bytes(b"old compose")
    with pytest.raises(platform.ComposeUpdateError, match="checksum"):
        list(
            platform.update_compose(
                dest=dest, machine="x86_64", download=_download(b"tampered", []), version=None
            )
        )
    assert dest.read_bytes() == b"old compose"
    assert not list(dest.parent.glob(".docker-compose.yulon-download*"))


def test_an_update_docker_does_not_use_is_a_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A `DOCKER_CONFIG` elsewhere would leave the old Compose answering: said, not hidden."""
    _pinned(monkeypatch)
    dest = tmp_path / "docker-compose"
    dest.write_bytes(b"old compose")
    with pytest.raises(platform.ComposeUpdateError, match="still answered 2.6.1"):
        list(
            platform.update_compose(
                dest=dest,
                machine="x86_64",
                download=_download(BINARY, []),
                version=lambda: (2, 6, 1),
            )
        )
    assert dest.read_bytes() == b"old compose", "the old one is put back"
    assert not list(tmp_path.glob(".docker-compose.yulon-*"))


def test_a_failed_check_with_no_old_plugin_leaves_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pinned(monkeypatch)
    dest = tmp_path / "docker-compose"
    with pytest.raises(platform.ComposeUpdateError):
        list(
            platform.update_compose(
                dest=dest, machine="x86_64", download=_download(BINARY, []), version=lambda: None
            )
        )
    assert not dest.exists()


def test_an_update_over_an_old_plugin_keeps_no_copy_once_it_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pinned(monkeypatch)
    dest = tmp_path / "docker-compose"
    dest.write_bytes(b"old compose")
    list(
        platform.update_compose(
            dest=dest, machine="x86_64", download=_download(BINARY, []), version=lambda: (5, 5, 1)
        )
    )
    assert dest.read_bytes() == BINARY
    assert not list(tmp_path.glob(".docker-compose.yulon-*"))


def test_a_folder_that_cannot_be_made_is_a_sentence_not_a_traceback(tmp_path: Path) -> None:
    """A read-only or blocked DOCKER_CONFIG got past `except ComposeUpdateError` as an OSError."""
    blocker = tmp_path / "cfg"
    blocker.write_text("a file where the folder should be")
    with pytest.raises(platform.ComposeUpdateError, match="could not write Docker Compose"):
        list(
            platform.update_compose(
                dest=blocker / "cli-plugins" / "docker-compose", machine="x86_64", download=None
            )
        )


def test_a_rename_the_folder_refuses_is_a_sentence_and_changes_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _pinned(monkeypatch)
    dest = tmp_path / "docker-compose"
    dest.write_bytes(b"old compose")
    real = platform.os.replace

    def refuse(src: object, dst: object) -> None:
        if Path(str(src)).name == ".docker-compose.yulon-download":
            raise PermissionError(13, "Permission denied")
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(platform.os, "replace", refuse)
    with pytest.raises(platform.ComposeUpdateError, match="could not write Docker Compose"):
        list(
            platform.update_compose(
                dest=dest, machine="x86_64", download=_download(BINARY, []), version=None
            )
        )
    assert dest.read_bytes() == b"old compose"
    assert not list(tmp_path.glob(".docker-compose.yulon-*"))


class _Response:
    def __init__(self, body: bytes, *, length: int | None, url: str = "https://x/compose") -> None:
        self.body, self.length, self.url = body, length, url

    def geturl(self) -> str:
        return self.url

    def getheader(self, name: str) -> str | None:
        if name == "Content-Length" and self.length is not None:
            return str(self.length)
        return None

    def read(self, size: int) -> bytes:
        chunk, self.body = self.body[:size], self.body[size:]
        return chunk

    def close(self) -> None:
        pass


@pytest.mark.parametrize(
    ("body", "length", "url", "ok"),
    [
        (b"x" * 100, 100, "https://x/compose", True),
        (b"x" * 100, 5000, "https://x/compose", False),  # said bigger than the cap
        (b"x" * 300, None, "https://x/compose", False),  # sent bigger, saying nothing
        (b"x" * 100, 100, "http://x/compose", False),  # redirected off HTTPS
    ],
)
def test_the_download_streams_under_a_cap_over_https_only(
    tmp_path: Path, body: bytes, length: int | None, url: str, ok: bool
) -> None:
    dest = tmp_path / "compose"

    def opener(_request: object) -> _Response:
        return _Response(body, length=length, url=url)

    if ok:
        platform._download_capped("https://x/compose", dest, cap=200, open_url=opener)  # type: ignore[arg-type]
        assert dest.read_bytes() == body
    else:
        with pytest.raises(platform.DownloadError):
            platform._download_capped("https://x/compose", dest, cap=200, open_url=opener)  # type: ignore[arg-type]


def test_a_download_that_fails_replaces_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "docker-compose"
    dest.write_bytes(b"old compose")

    def broken(url: str, part: Path) -> Path:
        raise OSError("no route to github.com")

    with pytest.raises(platform.ComposeUpdateError, match="no route"):
        list(platform.update_compose(dest=dest, machine="x86_64", download=broken))
    assert dest.read_bytes() == b"old compose"


def test_a_machine_docker_publishes_nothing_for_downloads_nothing(tmp_path: Path) -> None:
    with pytest.raises(platform.ComposeUpdateError):
        list(platform.update_compose(dest=tmp_path / "x", machine="armv7l", download=None))


def test_the_pinned_downloads_are_dockers_own_release_files() -> None:
    for machine, (url, sha256) in platform.COMPOSE_DOWNLOADS.items():
        assert url == (
            "https://github.com/docker/compose/releases/download/"
            f"v{platform.COMPOSE_DOWNLOAD_VERSION}/docker-compose-linux-{machine}"
        )
        assert len(sha256) == 64 and int(sha256, 16) >= 0


# ------------------------------------------------------------------- preflight


def test_preflight_refuses_a_compose_that_stops_the_import() -> None:
    facts = preflight.Facts(
        platform_id="linux",
        docker_ready=True,
        compose_ready=True,
        compose_version=(2, 6, 1),
        compose_offer=True,
    )
    check = preflight._compose_check(facts)
    assert check.name == preflight.COMPOSE_CHECK
    assert check.verdict == "refuse"
    assert "2.6.1" in check.detail
    assert "Install asks" in (check.remedy or "")


@pytest.mark.parametrize("version", [(2, 10, 0), (5, 6, 0), None])
def test_preflight_passes_a_compose_that_works_or_could_not_be_read(
    version: tuple[int, int, int] | None,
) -> None:
    facts = preflight.Facts(
        platform_id="linux", docker_ready=True, compose_ready=True, compose_version=version
    )
    assert preflight._compose_check(facts).verdict == "pass"


def _gather(entry_id: str, tmp_path: Path, asked: list[str]) -> preflight.Facts:
    def version() -> tuple[int, int, int]:
        asked.append("version")
        return (2, 6, 1)

    def offer() -> bool:
        asked.append("offer")
        return True

    return preflight.gather(
        load_catalog().get(entry_id),
        tmp_path / "s",
        platform_id=lambda: "linux",
        docker_ready=lambda: True,
        compose_ready=lambda: True,
        vm_resources=lambda: None,
        data_root=lambda: None,
        disk_free=lambda _p: 100 * 1024**3,
        dir_problem=lambda _p: None,
        bind_mount_ok=lambda _p: True,
        port_conflicts=lambda: [],
        probe_port=lambda host, port: platform.PortProbe(host, port, "unknown", ""),
        bind_port=lambda host, port: platform.PortBind(host, port, "free", ""),
        port_holders=lambda _ports: docker.PortHolders(),
        selinux=lambda: None,
        fs_type=lambda _p: None,
        in_wsl=lambda: False,
        compose_version=version,
        compose_offer=offer,
    )


@pytest.mark.parametrize("entry_id", ["wow-wotlk", "wow-unbound"])
def test_an_azerothcore_install_reads_the_version_and_the_offer(
    entry_id: str, tmp_path: Path
) -> None:
    asked: list[str] = []
    facts = _gather(entry_id, tmp_path, asked)
    assert facts.compose_version == (2, 6, 1)
    assert facts.compose_offer is True
    assert asked == ["version", "offer"]


@pytest.mark.parametrize("entry_id", ["wow-tbc", "wow-vanilla", "wow-tortoise", "wow-centurion"])
def test_a_server_whose_start_runs_on_old_compose_is_not_asked(
    entry_id: str, tmp_path: Path
) -> None:
    asked: list[str] = []
    facts = _gather(entry_id, tmp_path, asked)
    assert facts.compose_version is None
    assert preflight._compose_check(facts).verdict == "pass"
    assert asked == []


# --------------------------------------------------------------- the Install press


def _old_then_new(rec: Recorder):  # type: ignore[no-untyped-def]
    answers = [(2, 6, 1), (5, 5, 1)]

    def gather(entry: object, server_dir: Path, **kwargs: object) -> preflight.Facts:
        base = rec.gather(entry, server_dir, **kwargs)
        version = answers.pop(0) if len(answers) > 1 else answers[0]
        return replace(
            base,
            platform_id="linux",
            compose_ready=True,
            compose_version=version,
            compose_offer=platform.compose_too_old(version),
        )

    return gather


def _updates(rec: Recorder):  # type: ignore[no-untyped-def]
    def update() -> Iterator[str]:
        rec.calls.append("update-compose")
        yield "Docker Compose now answers 5.5.1."

    return update


def test_install_asks_then_puts_a_current_compose_in_place_and_goes_on(tmp_path: Path) -> None:
    rec = Recorder()
    questions: list[str] = []

    def ask(question: str) -> str:
        questions.append(question)
        return "yes"

    eng = engine(rec, gather=_old_then_new(rec), update_compose=_updates(rec))
    lines = list(eng.run(InstallOptions(server_dir=tmp_path / "s"), ask=ask))
    assert len(questions) == 1 and "2.6.1" in questions[0]
    assert platform.COMPOSE_DOWNLOAD_VERSION in questions[0]
    assert rec.calls.count("gather") == 2, rec.calls
    first = rec.calls.index("gather")
    assert rec.calls.index("update-compose") < rec.calls.index("gather", first + 1)
    assert "Docker Compose now answers 5.5.1." in lines


@pytest.mark.parametrize("reply", ["no", "", None])
def test_install_without_a_yes_changes_nothing_and_refuses(
    tmp_path: Path, reply: str | None
) -> None:
    rec = Recorder()
    eng = engine(rec, gather=_old_then_new(rec), update_compose=_updates(rec))
    with pytest.raises(InstallerError, match="2.6.1"):
        list(eng.run(InstallOptions(server_dir=tmp_path / "s"), ask=lambda _q: reply))
    assert "update-compose" not in rec.calls


def test_install_with_no_one_to_ask_refuses(tmp_path: Path) -> None:
    rec = Recorder()
    eng = engine(rec, gather=_old_then_new(rec), update_compose=_updates(rec))
    with pytest.raises(InstallerError, match="2.6.1"):
        list(eng.run(InstallOptions(server_dir=tmp_path / "s"), ask=None))
    assert "update-compose" not in rec.calls


def test_an_update_that_fails_stops_the_install_with_its_reason(tmp_path: Path) -> None:
    rec = Recorder()

    def broken() -> Iterator[str]:
        raise platform.ComposeUpdateError("The downloaded Docker Compose did not match")
        yield ""  # pragma: no cover

    eng = engine(rec, gather=_old_then_new(rec), update_compose=broken)
    with pytest.raises(InstallerError, match="did not match"):
        list(eng.run(InstallOptions(server_dir=tmp_path / "s"), ask=lambda _q: "yes"))


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


@pytest.mark.parametrize("offered", [True, False])
def test_an_azerothcore_start_is_refused_and_nothing_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, offered: bool
) -> None:
    fake = _Docker("Docker Compose version v2.6.1\n")
    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(platform, "compose_update_offered", lambda **_kw: offered)
    with pytest.raises(ComposeTooOld) as refused:
        Controller(AC_SPEC, tmp_path).start()
    assert refused.value.offer is offered
    assert "2.6.1" in str(refused.value)
    assert (platform.UPDATE_COMPOSE_LABEL in str(refused.value)) is offered
    assert not any(cmd[:3] == ["docker", "compose", "up"] for cmd in fake.calls)


def test_a_cmangos_start_never_asks_and_goes_on(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Its realmd and mangosd depend only on the database started with them."""
    fake = _Docker("Docker Compose version v2.6.1\n")
    monkeypatch.setattr(runner, "run", fake)
    Controller(CMANGOS_SPEC, tmp_path).refuse_an_old_compose()
    assert ["docker", "compose", "version"] not in fake.calls


@pytest.mark.parametrize("said", ["Docker Compose version v2.10.0\n", "", "podman-compose 1.0.6\n"])
def test_start_goes_on_with_a_compose_that_works_or_could_not_be_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, said: str
) -> None:
    fake = _Docker(said)
    monkeypatch.setattr(runner, "run", fake)
    Controller(AC_SPEC, tmp_path).refuse_an_old_compose()
    assert ["docker", "compose", "version"] in fake.calls


def test_the_start_asks_compose_where_the_server_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """A server inside a WSL distro asks that distro's Compose; no download is offered there."""
    seen: list[tuple[list[str], str | None]] = []

    def fake(
        argv: list[str],
        cwd: object = None,
        timeout: object = None,
        *,
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        seen.append((argv, wsl_distro))
        return _done(0, "Docker Compose version v2.6.1\n")

    monkeypatch.setattr(docker, "_docker", fake)
    said = docker.compose_refusal(wsl_distro="Ubuntu")
    assert seen == [(["compose", "version"], "Ubuntu")]
    assert said is not None and "sudo apt install docker-compose-v2" in said
    assert platform.UPDATE_COMPOSE_LABEL not in said


@pytest.mark.parametrize("press", ["refuse_before_a_stop", "stop_conflicting_and_start"])
def test_a_press_that_stops_something_first_refuses_before_the_stop(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, press: str
) -> None:
    """Restart, Recreate and "stop the other server" leave everything as it was."""
    fake = _Docker("Docker Compose version 2.9.0\n")
    monkeypatch.setattr(runner, "run", fake)
    with pytest.raises(ComposeTooOld):
        getattr(Controller(AC_SPEC, tmp_path), press)()
    assert not any(cmd[:3] == ["docker", "compose", "stop"] for cmd in fake.calls)
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
    with pytest.raises(ComposeTooOld):
        Controller(AC_SPEC, tmp_path).stop_conflicting_and_start()
    assert not any("stop" in cmd or "kill" in cmd for cmd in fake.calls)


# ------------------------------------------------------------------- Repair


def test_repair_is_refused_before_it_touches_anything(monkeypatch: pytest.MonkeyPatch) -> None:
    """Repair's `compose up --no-deps <importer>` is the very command old Compose refuses."""
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running=set())
    monkeypatch.setattr(docker, "compose_refusal", lambda *, wsl_distro=None: "too old")
    with pytest.raises(docker.DockerCommandError, match="too old"):
        docker.repair_import(
            replace(AC_SPEC, import_service="ac-db-import"),
            Path("/tmp/wow"),
            _probe(UNIMPORTED, DOCKER_IMPORTED),
        )
    assert calls == []


# ------------------------------------------------------------------- the Server tab


@pytest.mark.parametrize("offer", [True, False])
def test_the_server_tab_offers_the_update_beside_the_refusal_and_runs_it_when_pressed(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, offer: bool
) -> None:
    from tests.conftest import wait_for_panel
    from tests.test_controller_view import WOTLK, _Ps, _services
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.controller_view import ControllerView
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(runner, "run", _Ps())
    view = ControllerView(
        WOTLK, _services(_Ps(), tmp_path, []), status_poll_ms=0, job_runner=run_inline
    )
    sentence = platform.compose_too_old_sentence((2, 6, 1), linux=True, offer=offer)

    def refused(*_a: object, **_k: object) -> None:
        raise ComposeTooOld(sentence, offer=offer)

    monkeypatch.setattr(view.services.controller, "start", refused)
    view.start_server()
    assert view.update_compose_button.isVisibleTo(view) is offer
    if not offer:
        return
    ran: list[str] = []

    def update() -> Iterator[str]:
        ran.append("update")
        yield "Docker Compose now answers 5.5.1."

    monkeypatch.setattr(platform, "update_compose", update)
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *a, **k: False)
    view.update_compose_button.click()
    assert ran == [], "nothing runs without a yes"
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *a, **k: True)
    view.update_compose_button.click()
    wait_for_panel(view.rebuild_log)
    assert ran == ["update"]
    assert not view.update_compose_button.isVisibleTo(view)


def test_a_download_said_to_be_too_big_is_not_read_at_all(tmp_path: Path) -> None:
    """Refused on the server's own word, before one byte lands on the disk."""
    response = _Response(b"x" * 5000, length=5000)
    reads: list[int] = []
    real_read = response.read

    def counted(size: int) -> bytes:
        reads.append(size)
        return real_read(size)

    response.read = counted  # type: ignore[method-assign]
    with pytest.raises(platform.DownloadError, match="more than the 200 allowed"):
        platform._download_capped(
            "https://x/compose", tmp_path / "compose", cap=200, open_url=lambda _r: response  # type: ignore[arg-type,return-value]
        )
    assert reads == []
