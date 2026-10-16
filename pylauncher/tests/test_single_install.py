"""Tests for the single-container install engine (`yulon.catalog.single_install`).

Every external effect is a `SingleSeams` fake, so these run with no daemon, no network
and no build: what they prove is the engine's own control flow — the stage order, the
resume, the refusals and the ready wait.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from yulon import docker, git, platform
from yulon.catalog import composegen, native
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.catalog.installer import (
    InstallerError,
    InstallOptions,
    InstallStopped,
    UnsupportedPlatformError,
)
from yulon.catalog.single_install import (
    STAGE_NAMES,
    SingleInstaller,
    SingleSeams,
    existing_install,
    missing_ready_lines,
    ready_lines,
)
from yulon.ui import lines

DDON: SingleEntry = load_catalog().get_single("ddon")
READY_LOG = "\n".join(
    [
        "Info - TcpServer: [Game]  TrySocketListen - Listening on 0.0.0.0:52000.",
        "Info - TcpServer: [Login]  TrySocketListen - Listening on 0.0.0.0:52100.",
    ]
)
SHIPPED_CSV = (
    chr(0xFEFF)  # the shipped file starts with a UTF-8 byte order mark
    + "#Id,Name,Brief,MaxLoginNum,IpAddress,Port\n"
    + "10,Local Host,Brief,1000,127.0.0.1,52000\n"
)


@dataclass
class Daemon:
    """A fake Docker and git: records what the engine asked and answers as told."""

    built: list[tuple[Path, tuple[str, ...]]] = field(default_factory=list)
    started: list[tuple[Path, str]] = field(default_factory=list)
    cloned: list[git.CloneSpec] = field(default_factory=list)
    build_exit: int = 0
    images_present: bool | None = False
    containers: dict[str, str | None] = field(default_factory=dict)
    port_holders: list[str] = field(default_factory=list)
    logs: list[docker.RunLog] = field(default_factory=lambda: [docker.RunLog(READY_LOG, True)])
    status: str = "running"
    now: float = 0.0

    def clone(self, spec: git.CloneSpec) -> None:
        self.cloned.append(spec)
        (spec.dest / ".git").mkdir(parents=True, exist_ok=True)
        csv = spec.dest / "Arrowgene.Ddon.Shared/Files/Assets/GameServerList.csv"
        csv.parent.mkdir(parents=True, exist_ok=True)
        csv.write_text(SHIPPED_CSV, encoding="utf-8")

    def build(
        self,
        server_dir: Path,
        files: Sequence[str],
        *,
        sink: Callable[[str], None],
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        self.built.append((server_dir, tuple(files)))
        sink("#5 [build 3/3] RUN dotnet publish")
        return docker.AttachedRun(returncode=self.build_exit, tail=("error CS0001: boom",))

    def up(self, server_dir: Path, service: str, **_: Any) -> None:
        self.started.append((server_dir, service))

    def run_log(self, container: str) -> docker.RunLog:
        return self.logs.pop(0) if len(self.logs) > 1 else self.logs[0]

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def seams(self, **overrides: Any) -> SingleSeams:
        return replace(
            SingleSeams(
                platform_id=lambda: "linux",
                docker_ready=lambda: True,
                ensure_docker=_never_provision,
                dir_problem=lambda _p: None,
                clone=self.clone,
                remote_url=lambda _dest: DDON.emulator.sources[0].url,
                port_conflicts=lambda _ports: list(self.port_holders),
                container_exists=lambda name: name in self.containers,
                container_project=lambda name: self.containers.get(name),
                images_built=lambda _refs: self.images_present,
                build=self.build,
                up=self.up,
                run_log=self.run_log,
                state_of=lambda _c: docker.ContainerState(status=self.status),
                sleep=self.sleep,
                clock=lambda: self.now,
            ),
            **overrides,
        )


def _never_provision(**_: Any) -> platform.ProvisionReport:
    raise AssertionError("Docker answered; nothing should be provisioned")


def _run(engine: SingleInstaller, server_dir: Path, **kwargs: Any) -> list[str]:
    said = engine.run(InstallOptions(server_dir=server_dir), **kwargs)
    return [lines.parse(line).text for line in said]


@pytest.fixture
def daemon() -> Daemon:
    return Daemon()


def test_a_fresh_install_runs_every_stage_in_order_and_records_them(
    tmp_path: Path, daemon: Daemon
) -> None:
    server = tmp_path / "yulon-ddon"
    said = _run(SingleInstaller(DDON, seams=daemon.seams()), server)

    assert [line[4:] for line in said if line.startswith("--- ")] == list(STAGE_NAMES)
    assert said[-1] == f"DDON is installed and running in {server}"
    state = native.read_state(server, valid=STAGE_NAMES)
    assert state is not None
    assert state.completed == STAGE_NAMES
    assert state.game_id == "ddon" and state.family == "arrowgene"
    assert state.last_error == ""
    assert daemon.built == [(server, composegen.COMPOSE_FILES)]
    assert daemon.started == [(server, "ddon-server")]
    (spec,) = daemon.cloned
    assert spec.dest == server / "src" and spec.rev == DDON.emulator.sources[0].rev


def test_the_rendered_files_carry_this_install_s_identity(tmp_path: Path, daemon: Daemon) -> None:
    server = tmp_path / "yulon-ddon"
    engine = SingleInstaller(DDON, seams=daemon.seams())
    _run(engine, server)

    base = (server / composegen.BASE_FILE).read_text(encoding="utf-8")
    ident = engine.install_id(server)
    assert f"name: yulon-ddon-{ident}" in base
    assert f"image: yulon.local/ddon-server:{ident}" in base
    for port in (52100, 52000, 52099):
        assert f'"{port}:{port}"' in base
    assert "./etc/GameServerList.csv:/var/ddon/server/Files/Assets/GameServerList.csv:ro" in base
    assert "{{" not in base
    override = (server / composegen.OVERRIDE_FILE).read_text(encoding="utf-8")
    assert 'DB_ENABLE_TRACING: "false"' in override
    dockerfile = (server / "Dockerfile").read_text(encoding="utf-8")
    assert dockerfile.startswith(composegen.GENERATED_MARKER)
    assert 'COPY ["src", "/src"]' in dockerfile
    assert "rm -f /out/libhostfxr.so /out/apphost" in dockerfile
    assert "!src/Arrowgene.Ddon.Metrics/" in (server / ".dockerignore").read_text(encoding="utf-8")
    assert (server / "etc/GameServerList.csv").read_text(encoding="utf-8") == SHIPPED_CSV


def test_a_second_run_resumes_and_redoes_nothing_that_finished(
    tmp_path: Path, daemon: Daemon
) -> None:
    server = tmp_path / "yulon-ddon"
    _run(SingleInstaller(DDON, seams=daemon.seams()), server)
    advertised = server / "etc/GameServerList.csv"
    advertised.write_text("chosen by Networking", encoding="utf-8")
    daemon.images_present = True

    said = _run(SingleInstaller(DDON, seams=daemon.seams()), server)

    assert "Using " + str(server) + " (resuming)" in said
    assert len(daemon.cloned) == 1, "a finished clone is left exactly as it is"
    assert len(daemon.built) == 1, "a recorded build whose image exists is not rebuilt"
    assert advertised.read_text(encoding="utf-8") == "chosen by Networking"


def test_a_recorded_build_whose_image_is_gone_is_built_again(
    tmp_path: Path, daemon: Daemon
) -> None:
    server = tmp_path / "yulon-ddon"
    _run(SingleInstaller(DDON, seams=daemon.seams()), server)
    daemon.images_present = None  # the daemon would not say

    _run(SingleInstaller(DDON, seams=daemon.seams()), server)

    assert len(daemon.built) == 2


def test_a_folder_with_someone_else_s_files_is_refused_before_docker_is_asked(
    tmp_path: Path, daemon: Daemon
) -> None:
    server = tmp_path / "taken"
    server.mkdir()
    (server / "notes.txt").write_text("mine", encoding="utf-8")
    asked: list[bool] = []

    def docker_ready() -> bool:
        asked.append(True)
        return True

    engine = SingleInstaller(DDON, seams=daemon.seams(docker_ready=docker_ready))
    with pytest.raises(InstallerError, match="already has files in it"):
        _run(engine, server)
    assert asked == []
    assert sorted(p.name for p in server.iterdir()) == ["notes.txt"]


def test_a_folder_that_cannot_be_listed_is_refused_rather_than_called_empty(
    tmp_path: Path, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`native._listing()`'s refusal, not a `PermissionError` traceback and not "empty".

    `Path.iterdir` raises for this ONE folder, as in `test_spine.py`'s `_unlistable()`.
    """
    server = tmp_path / "locked"
    server.mkdir()
    listing = Path.iterdir

    def refuse(self: Path) -> Iterator[Path]:
        if self == server:
            raise PermissionError(13, "Permission denied")
        return listing(self)

    monkeypatch.setattr(Path, "iterdir", refuse)
    with pytest.raises(InstallerError, match="could not be listed"):
        _run(SingleInstaller(DDON, seams=daemon.seams()), server)


def test_a_folder_holding_another_game_is_refused(tmp_path: Path, daemon: Daemon) -> None:
    server = tmp_path / "wotlk"
    engine = SingleInstaller(DDON, seams=daemon.seams())
    native.write_state(
        server, native.InstallState(game_id="wow-wotlk", install_id=engine.install_id(server))
    )
    with pytest.raises(InstallerError, match="already holds a wow-wotlk install"):
        _run(engine, server)


def test_a_state_file_copied_from_another_folder_is_refused(tmp_path: Path, daemon: Daemon) -> None:
    server = tmp_path / "copy"
    native.write_state(server, native.InstallState(game_id="ddon", install_id="0000000000"))
    with pytest.raises(InstallerError, match="copied from another folder"):
        _run(SingleInstaller(DDON, seams=daemon.seams()), server)


def test_our_container_name_under_another_project_is_refused(
    tmp_path: Path, daemon: Daemon
) -> None:
    daemon.containers["ddon-server"] = "ddon"
    with pytest.raises(InstallerError, match="compose project 'ddon'"):
        _run(SingleInstaller(DDON, seams=daemon.seams()), tmp_path / "s")
    assert not (tmp_path / "s").exists()


def test_our_own_container_from_an_earlier_run_is_not_a_collision(
    tmp_path: Path, daemon: Daemon
) -> None:
    server = tmp_path / "s"
    engine = SingleInstaller(DDON, seams=daemon.seams())
    daemon.containers["ddon-server"] = engine.tokens(server)["PROJECT_NAME"]
    daemon.port_holders = ["ddon-server"]
    said = _run(engine, server)
    assert said[-1].endswith("is installed and running in " + str(server))


def test_a_stranger_on_our_ports_is_refused(tmp_path: Path, daemon: Daemon) -> None:
    daemon.port_holders = ["some-web-app"]
    with pytest.raises(InstallerError, match="some-web-app is running on one of them"):
        _run(SingleInstaller(DDON, seams=daemon.seams()), tmp_path / "s")


def test_a_platform_the_entry_does_not_list_is_refused(tmp_path: Path, daemon: Daemon) -> None:
    engine = SingleInstaller(DDON, seams=daemon.seams(platform_id=lambda: "macos"))
    with pytest.raises(UnsupportedPlatformError, match="cannot be installed on macOS"):
        _run(engine, tmp_path / "s")


def test_a_folder_docker_cannot_use_is_refused_in_the_platform_s_words(
    tmp_path: Path, daemon: Daemon
) -> None:
    engine = SingleInstaller(
        DDON, seams=daemon.seams(dir_problem=lambda _p: "That folder is your home folder.")
    )
    with pytest.raises(InstallerError, match="home folder. Pick a different folder"):
        _run(engine, tmp_path / "s")


@pytest.mark.parametrize(
    "theirs",
    ["services: {}\n", f"{composegen.GENERATED_MARKER} - my note\nservices: {{}}\n"],
    ids=["unmarked", "look-alike-marker"],
)
def test_an_unmarked_compose_file_is_never_overwritten(
    tmp_path: Path, daemon: Daemon, theirs: str
) -> None:
    """The look-alike is T25's: a prefix test read it as ours, `composegen.is_ours()` does not."""
    server = tmp_path / "s"
    original_clone = daemon.clone

    def clone_then_hand_write(spec: git.CloneSpec) -> None:
        original_clone(spec)
        (server / composegen.BASE_FILE).write_text(theirs, encoding="utf-8")

    engine = SingleInstaller(DDON, seams=daemon.seams(clone=clone_then_hand_write))
    with pytest.raises(InstallerError, match="was not written by Yu'lon"):
        _run(engine, server)
    assert (server / composegen.BASE_FILE).read_text(encoding="utf-8") == theirs


def test_a_failed_build_is_an_error_that_the_state_file_keeps(
    tmp_path: Path, daemon: Daemon
) -> None:
    server = tmp_path / "s"
    daemon.build_exit = 1
    with pytest.raises(InstallerError, match="did not build"):
        _run(SingleInstaller(DDON, seams=daemon.seams()), server)
    state = native.read_state(server, valid=STAGE_NAMES)
    assert state is not None
    assert "build" not in state.completed and "generate-compose" in state.completed
    assert "did not build" in state.last_error
    assert state.error_run == native.ERROR_RUN_INSTALL


def test_the_ready_wait_waits_for_every_line_of_this_run(tmp_path: Path, daemon: Daemon) -> None:
    game_only = READY_LOG.splitlines()[0]
    daemon.logs = [
        docker.RunLog("", True),
        docker.RunLog(READY_LOG, False),  # every run's log: not evidence of THIS run
        docker.RunLog(game_only, True),
        docker.RunLog(READY_LOG, True),
    ]
    said = _run(SingleInstaller(DDON, seams=daemon.seams()), tmp_path / "s")
    assert daemon.now == 6.0
    assert said.count("Seen: Listening on 0.0.0.0:52000.") == 1
    assert "DDON is listening on ports 52100, 52000, 52099" in said


def test_a_container_that_stops_while_starting_fails_with_its_last_lines(
    tmp_path: Path, daemon: Daemon
) -> None:
    daemon.logs = [docker.RunLog("Database version is 60. Please update", True)]
    daemon.status = "exited"
    with pytest.raises(InstallerError, match="stopped while it was starting(.|\n)*version is 60"):
        _run(SingleInstaller(DDON, seams=daemon.seams()), tmp_path / "s")


def test_a_server_that_never_listens_times_out_and_says_what_it_waited_for(
    tmp_path: Path, daemon: Daemon
) -> None:
    daemon.logs = [docker.RunLog("still loading quests", True)]
    with pytest.raises(InstallerError, match="within 300 seconds(.|\n)*0.0.0.0:52100"):
        _run(SingleInstaller(DDON, seams=daemon.seams()), tmp_path / "s")


def test_stop_ends_the_install_with_the_stop_s_own_sentence(tmp_path: Path, daemon: Daemon) -> None:
    cancel = threading.Event()
    original_clone = daemon.clone

    def clone_then_stop(spec: git.CloneSpec) -> None:
        original_clone(spec)
        cancel.set()

    engine = SingleInstaller(DDON, seams=daemon.seams(clone=clone_then_stop))
    with pytest.raises(InstallStopped, match="has not been remembered"):
        _run(engine, tmp_path / "s", cancel=cancel)
    assert daemon.built == []


def test_ready_lines_are_the_catalog_s_with_the_ports_filled_in() -> None:
    assert ready_lines(DDON) == (
        "Listening on 0.0.0.0:52000.",
        "Listening on 0.0.0.0:52100.",
    )
    assert missing_ready_lines(DDON, READY_LOG) == ()
    assert missing_ready_lines(DDON, "") == ready_lines(DDON)


# -- "Use existing…": adopting a finished install -------------------------------------


def _linux() -> str:
    return "linux"


def _record(server: Path, completed: tuple[str, ...], **fields: str) -> None:
    native.write_state(
        server,
        native.InstallState(
            game_id=fields.get("game_id", "ddon"),
            install_id=fields.get("install_id", composegen.install_id(server, platform_id=_linux)),
            family="arrowgene",
            completed=completed,
        ),
    )


def test_use_existing_adopts_a_finished_install_recorded_for_this_folder(tmp_path: Path) -> None:
    server = tmp_path / "Yulon DDON"
    server.mkdir()
    _record(server, STAGE_NAMES)
    state = existing_install(DDON, server, platform_id=_linux)
    assert state.completed == STAGE_NAMES


def test_use_existing_refuses_a_folder_without_a_record(tmp_path: Path) -> None:
    with pytest.raises(InstallerError, match="holds no DDON server that Yu'lon installed"):
        existing_install(DDON, tmp_path, platform_id=_linux)


def test_use_existing_refuses_an_install_that_did_not_finish(tmp_path: Path) -> None:
    """A tab over a half-built install has no image to start: finish it with Install."""
    _record(tmp_path, STAGE_NAMES[:4])
    with pytest.raises(InstallerError, match=r"did not finish \(it stopped before build\)"):
        existing_install(DDON, tmp_path, platform_id=_linux)


def test_use_existing_refuses_another_game_and_a_copied_record(tmp_path: Path) -> None:
    """Install's own record checks: a WoW folder, and a record that names another folder."""
    wow = tmp_path / "wotlk"
    wow.mkdir()
    _record(wow, STAGE_NAMES, game_id="wow-wotlk")
    with pytest.raises(InstallerError, match="already holds a wow-wotlk install"):
        existing_install(DDON, wow, platform_id=_linux)
    copy = tmp_path / "copy"
    copy.mkdir()
    _record(copy, STAGE_NAMES, install_id="0000000000")
    with pytest.raises(InstallerError, match="copied from another folder"):
        existing_install(DDON, copy, platform_id=_linux)
