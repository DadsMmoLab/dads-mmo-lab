"""Tests for the stack install engine (`yulon.catalog.stack_install`).

Every external effect is a `StackSeams` fake, so these run with no daemon, no network
and no registry: what they prove is the engine's own control flow — the stage order,
the resume, the generated passwords, the rendered compose files, the refusals and the
ready wait over several containers.
"""

from __future__ import annotations

import hashlib
import os
import stat
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

from yulon import docker, platform
from yulon.catalog import composegen, native, stack_install
from yulon.catalog.catalog import SingleEntry, load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions, InstallStopped
from yulon.catalog.stack_install import (
    CERTIFICATES_SERVICE,
    SEED_PROFILE,
    STACK_TEMPLATES,
    STAGE_NAMES,
    StackInstaller,
    StackSeams,
    existing_install,
    listener_containers,
    loader_path,
    missing_ready_lines,
    stack_of,
    stages_of,
    token_name,
)

FFXI: SingleEntry = load_catalog().get_single("ffxi")
FOREVER: SingleEntry = load_catalog().get_single("wow-forever")
STACK = stack_of(FFXI)
LOADER = b"MZ pretend xiloader"


def _ffxi_with_loader_pin(data: bytes) -> SingleEntry:
    """FFXI with its loader pinned to `data`, so a fake download can match the pin."""
    play = FFXI.play
    assert play is not None and play.loader is not None
    file = play.loader.file.model_copy(
        update={"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    )
    loader = play.loader.model_copy(update={"file": file})
    return FFXI.model_copy(update={"play": play.model_copy(update={"loader": loader})})


def _ready_log(container: str) -> str:
    service = next(s for s in STACK.services if s.container == container)
    return "\n".join(f"[info] {line} after 0.25 seconds..." for line in service.ready)


@dataclass
class Daemon:
    """A fake Docker and network: records what the engine asked and answers as told."""

    pulled: list[tuple[Path, tuple[str, ...]]] = field(default_factory=list)
    seeded: list[tuple[Path, str, tuple[str, ...]]] = field(default_factory=list)
    started: list[Path] = field(default_factory=list)
    downloads: list[str] = field(default_factory=list)
    download_bytes: list[bytes] = field(default_factory=lambda: [LOADER])
    pull_exit: int = 0
    seed_exit: int = 0
    containers: dict[str, str | None] = field(default_factory=dict)
    port_holders: list[str] = field(default_factory=list)
    silent: set[str] = field(default_factory=set)
    status: str = "running"
    now: float = 0.0
    tokens: int = 0

    def download(self, url: str, dest: Path) -> Path:
        self.downloads.append(url)
        data = (
            self.download_bytes.pop(0) if len(self.download_bytes) > 1 else self.download_bytes[0]
        )
        dest.write_bytes(data)
        return dest

    def pull(
        self,
        server_dir: Path,
        *,
        profiles: Sequence[str] = (),
        sink: Callable[[str], None],
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        self.pulled.append((server_dir, tuple(profiles)))
        sink("server Pulled")
        return docker.AttachedRun(returncode=self.pull_exit, tail=("pull access denied",))

    def run_once(
        self,
        server_dir: Path,
        service: str,
        *,
        profiles: Sequence[str] = (),
        sink: Callable[[str], None],
        cancel: threading.Event | None = None,
    ) -> docker.AttachedRun:
        self.seeded.append((server_dir, service, tuple(profiles)))
        return docker.AttachedRun(returncode=self.seed_exit, tail=("no space left",))

    def up(self, server_dir: Path, **_: Any) -> None:
        self.started.append(server_dir)

    def run_log(self, container: str) -> docker.RunLog:
        if container in self.silent:
            return docker.RunLog("[info] still loading", True)
        return docker.RunLog(_ready_log(container), True)

    def token(self, nbytes: int) -> str:
        self.tokens += 1
        return f"{self.tokens:02d}" * nbytes

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def seams(self, **overrides: Any) -> StackSeams:
        return replace(
            StackSeams(
                platform_id=lambda: "linux",
                docker_ready=lambda: True,
                ensure_docker=_never_provision,
                dir_problem=lambda _p: None,
                download=self.download,
                port_conflicts=lambda _ports: list(self.port_holders),
                container_exists=lambda name: name in self.containers,
                container_project=lambda name: self.containers.get(name),
                pull=self.pull,
                run_once=self.run_once,
                up=self.up,
                run_log=self.run_log,
                state_of=lambda _c: docker.ContainerState(status=self.status),
                token=self.token,
                sleep=self.sleep,
                clock=lambda: self.now,
            ),
            **overrides,
        )


def _never_provision(**_: Any) -> platform.ProvisionReport:
    raise AssertionError("Docker answered; nothing should be provisioned")


def _engine(
    daemon: Daemon, *, entry: SingleEntry | None = None, **overrides: Any
) -> StackInstaller:
    return StackInstaller(
        entry if entry is not None else _ffxi_with_loader_pin(LOADER),
        seams=daemon.seams(**overrides),
    )


def _run(
    engine: StackInstaller, server_dir: Path, cancel: threading.Event | None = None
) -> list[str]:
    return list(engine.run(InstallOptions(server_dir=server_dir), cancel=cancel))


# -- the catalog entry this engine installs ------------------------------------------


def test_ffxi_is_a_stack_of_pinned_images_with_its_seed_and_two_secrets() -> None:
    assert FFXI.install.stack is STACK
    assert {image.role for image in STACK.images} == {"server", "database", "meshes"}
    assert all("@sha256:" in image.ref for image in STACK.images)
    assert STACK.seed is not None and STACK.seed.image == "meshes"
    assert STACK.secrets == ("MARIADB_ROOT_PASSWORD", "MARIADB_PASSWORD")


def test_the_listeners_are_every_service_but_the_one_shot_update() -> None:
    assert listener_containers(FFXI) == (
        "ffxi-database",
        "ffxi-connect",
        "ffxi-search",
        "ffxi-profile",
        "ffxi-world",
        "ffxi-map",
    )
    once = [s.container for s in STACK.services if s.once]
    assert once == ["ffxi-database-update"]


def test_token_names_spell_a_service_or_role_in_capitals() -> None:
    assert token_name("database-update") == "DATABASE_UPDATE"
    assert token_name("map") == "MAP"


def test_the_tokens_name_every_image_container_and_port(tmp_path: Path) -> None:
    tokens = _engine(Daemon()).tokens(tmp_path / "ffxi")
    assert tokens["IMAGE_SERVER"] == STACK.image("server").ref
    assert tokens["CONTAINER_DATABASE_UPDATE"] == "ffxi-database-update"
    assert tokens["PORT_DATA"] == "54230"
    assert tokens["PROJECT_NAME"].startswith("yulon-ffxi-")


# -- a whole install ------------------------------------------------------------------


def test_a_fresh_install_runs_every_stage_in_order(tmp_path: Path) -> None:
    daemon = Daemon()
    server_dir = tmp_path / "ffxi"
    out = _run(_engine(daemon), server_dir)
    stages = [line.removeprefix("--- ") for line in out if line.startswith("--- ")]
    # FFXI names no downloads and no client extract, so it runs the seven stages it always did.
    assert stages == list(stages_of(STACK))
    assert len(stages) == 7
    assert daemon.pulled == [(server_dir, (SEED_PROFILE,))]
    assert daemon.seeded == [(server_dir, "meshes", (SEED_PROFILE,))]
    assert daemon.started == [server_dir]
    state = native.read_state(server_dir, valid=STAGE_NAMES)
    assert state is not None and state.completed == stages_of(STACK)
    assert state.family == "landsandboat" and state.last_error == ""
    assert out[-1] == f"FFXI is installed and running in {server_dir}"


def test_the_loader_is_fetched_into_the_server_folder_at_its_pin(tmp_path: Path) -> None:
    daemon = Daemon()
    server_dir = tmp_path / "ffxi"
    entry = _ffxi_with_loader_pin(LOADER)
    _run(_engine(daemon, entry=entry), server_dir)
    path = loader_path(entry, server_dir)
    assert path is not None and path.read_bytes() == LOADER
    assert daemon.downloads == [entry.play.loader.file.url]  # type: ignore[union-attr]


def test_a_loader_that_does_not_match_its_pin_is_fetched_once_more_then_refused(
    tmp_path: Path,
) -> None:
    daemon = Daemon(download_bytes=[b"tampered"])
    server_dir = tmp_path / "ffxi"
    with pytest.raises(InstallerError, match="did not match the size and sha256"):
        _run(_engine(daemon), server_dir)
    assert len(daemon.downloads) == 2
    assert not (server_dir / "xiloader" / "xiloader.exe").exists()


def test_a_second_install_resumes_and_downloads_nothing_again(tmp_path: Path) -> None:
    daemon = Daemon()
    server_dir = tmp_path / "ffxi"
    _run(_engine(daemon), server_dir)
    out = _run(_engine(daemon), server_dir)
    assert any(line.startswith("Already finished: fetch-loader") for line in out)
    assert len(daemon.downloads) == 1
    assert len(daemon.seeded) == 1, "a recorded seed is not copied over itself"


# -- the passwords ------------------------------------------------------------------


def test_the_passwords_are_generated_once_and_never_rewritten(tmp_path: Path) -> None:
    daemon = Daemon()
    server_dir = tmp_path / "ffxi"
    _run(_engine(daemon), server_dir)
    first = (server_dir / ".env").read_text(encoding="utf-8")
    assert first.splitlines()[0].startswith(composegen.GENERATED_MARKER)
    for name in STACK.secrets:
        value = composegen.dotenv_secret(server_dir, name)
        assert value is not None and len(value) == 48
    _run(_engine(daemon), server_dir)
    assert (server_dir / ".env").read_text(encoding="utf-8") == first


@pytest.mark.skipif(os.name == "nt", reason="the POSIX mode is a no-op on Windows")
def test_the_env_holding_the_passwords_is_owner_only(tmp_path: Path) -> None:
    """The stack's `.env` carries the database passwords, so it is written private (T127's rule)."""
    server_dir = tmp_path / "ffxi"
    old = os.umask(0o022)
    try:
        _run(_engine(Daemon()), server_dir)
    finally:
        os.umask(old)
    assert stat.S_IMODE((server_dir / ".env").stat().st_mode) == 0o600


def test_a_dotenv_missing_one_password_gets_it_added_and_keeps_its_lines(tmp_path: Path) -> None:
    server_dir = tmp_path / "ffxi"
    server_dir.mkdir()
    (server_dir / ".env").write_text("MARIADB_ROOT_PASSWORD='kept'\n", encoding="utf-8")
    (server_dir / native.STATE_FILE).write_text("", encoding="utf-8")
    engine = _engine(Daemon())
    state = native.InstallState(
        game_id="ffxi", install_id=engine.install_id(server_dir), family="landsandboat"
    )
    lines = list(engine._write_secrets(server_dir, state, None))
    assert lines == ["Generated MARIADB_PASSWORD in .env."]
    assert composegen.dotenv_secret(server_dir, "MARIADB_ROOT_PASSWORD") == "kept"
    assert composegen.dotenv_secret(server_dir, "MARIADB_PASSWORD") is not None


# -- the compose files ------------------------------------------------------------------


def test_the_compose_files_are_ours_and_every_token_is_filled(tmp_path: Path) -> None:
    server_dir = tmp_path / "ffxi"
    _run(_engine(Daemon()), server_dir)
    for name, _template in STACK_TEMPLATES:
        text = (server_dir / name).read_text(encoding="utf-8")
        assert composegen.is_ours(server_dir / name)
        assert "{{" not in text and "}}" not in text
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    for service in STACK.services:
        assert f"container_name: {service.container}" in base
    for image in STACK.images:
        assert f"image: {image.ref}" in base
    assert '"127.0.0.1:54230:54230/udp"' in base
    assert not (server_dir / composegen.BUILD_FILE).exists(), "a stack builds nothing"


def test_remaking_a_pre_t888_install_renders_the_certificates_service_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T888 item 4 (Claude #3, Codex #1): the old layout has no service for the run to name.

    An install made before Yu'lon's own certificate script has a compose file without
    `certificates` and no `certs-yulon.sh` beside it. The remake renders both first, with
    the installer's own function, then runs the one-shot with `--force`.
    """
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    (server_dir / composegen.BASE_FILE).write_text(
        "# generated by Yu'lon — do not hand-edit; the install engine rewrites this file.\n"
        "name: old\nservices:\n  world:\n    image: x\n",
        encoding="utf-8",
    )
    ran: list[tuple[Path, str, tuple[str, ...], bool]] = []

    def run_once(
        where: Path, service: str, *, command: Sequence[str] = (), **_: Any
    ) -> docker.AttachedRun:
        base = (where / composegen.BASE_FILE).read_text(encoding="utf-8")
        ran.append((where, service, tuple(command), "certificates:" in base))
        return docker.AttachedRun(0)

    monkeypatch.setattr(docker, "compose_run_once", run_once)
    stack_install.remake_certificates(FOREVER, server_dir)
    assert ran == [(server_dir, CERTIFICATES_SERVICE, ("--force",), True)], (
        "the service is in the compose file before the run that names it"
    )
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    assert "certificates:" in base and "./certs-yulon.sh:/yulon-certs.sh:ro" in base
    assert (server_dir / "certs-yulon.sh").is_file(), "the script the service runs is copied"


def test_a_compose_file_someone_else_wrote_is_left_alone(tmp_path: Path) -> None:
    server_dir = tmp_path / "ffxi"
    engine = _engine(Daemon())
    with pytest.raises(InstallerError, match="was not written by Yu'lon"):
        _run(engine, server_dir)  # a fresh install first, so the folder is ours
        (server_dir / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
        _run(engine, server_dir)


# -- refusals ------------------------------------------------------------------


def test_a_container_of_our_name_in_another_project_is_refused(tmp_path: Path) -> None:
    daemon = Daemon(containers={"ffxi-map": "yulon-ffxi-0000000000"})
    with pytest.raises(InstallerError, match="ffxi-map already exists"):
        _run(_engine(daemon), tmp_path / "ffxi")
    assert not (tmp_path / "ffxi").exists(), "nothing was written"


def test_a_stranger_on_one_of_our_ports_is_refused(tmp_path: Path) -> None:
    daemon = Daemon(port_holders=["ffxi-proto-connect-1"])
    with pytest.raises(InstallerError, match="ffxi-proto-connect-1 is running"):
        _run(_engine(daemon), tmp_path / "ffxi")


def test_our_own_containers_on_our_ports_are_not_strangers(tmp_path: Path) -> None:
    daemon = Daemon(port_holders=["ffxi-connect", "ffxi-map"])
    out = _run(_engine(daemon), tmp_path / "ffxi")
    assert any(line.startswith("Ports 54001") for line in out)


def test_a_failed_pull_says_so_and_is_recorded(tmp_path: Path) -> None:
    daemon = Daemon(pull_exit=1)
    server_dir = tmp_path / "ffxi"
    with pytest.raises(InstallerError, match="images could not be pulled"):
        _run(_engine(daemon), server_dir)
    state = native.read_state(server_dir, valid=STAGE_NAMES)
    assert state is not None and "pull-images" not in state.completed
    assert "images could not be pulled" in state.last_error


def test_a_stop_during_the_pull_is_a_stop(tmp_path: Path) -> None:
    daemon = Daemon(pull_exit=docker.CANCELLED_RETURNCODE)
    with pytest.raises(InstallStopped):
        _run(_engine(daemon), tmp_path / "ffxi")


def test_a_failed_seed_says_what_could_not_be_copied(tmp_path: Path) -> None:
    daemon = Daemon(seed_exit=1)
    with pytest.raises(InstallerError, match="navigation meshes"):
        _run(_engine(daemon), tmp_path / "ffxi")


# -- the ready wait ------------------------------------------------------------------


def test_the_ready_wait_names_each_server_once_it_listens(tmp_path: Path) -> None:
    out = _run(_engine(Daemon()), tmp_path / "ffxi")
    seen = [line for line in out if line.startswith("Seen: ")]
    assert len(seen) == len([s for s in STACK.listeners if s.ready])
    assert "Seen: The map-server is ready to work" in seen


def test_a_server_that_never_listens_times_out_by_name(tmp_path: Path) -> None:
    daemon = Daemon(silent={"ffxi-map"})
    with pytest.raises(InstallerError, match="still waiting for ffxi-map"):
        _run(_engine(daemon), tmp_path / "ffxi")


def test_a_server_that_stops_while_starting_is_a_failure_with_its_last_lines(
    tmp_path: Path,
) -> None:
    daemon = Daemon(silent={"ffxi-map"}, status="exited")
    with pytest.raises(InstallerError, match="ffxi-map stopped while it was starting"):
        _run(_engine(daemon), tmp_path / "ffxi")


def test_missing_ready_lines_reads_one_service() -> None:
    world = next(s for s in STACK.services if s.service == "world")
    assert missing_ready_lines(world, "") == ("The world-server is ready to work",)
    assert missing_ready_lines(world, "x The world-server is ready to work after 0.02") == ()


# -- Use existing… ------------------------------------------------------------------


def test_use_existing_takes_a_finished_install_and_refuses_an_unfinished_one(
    tmp_path: Path,
) -> None:
    daemon = Daemon()
    server_dir = tmp_path / "ffxi"
    _run(_engine(daemon), server_dir)
    state = existing_install(FFXI, server_dir, platform_id=lambda: "linux")
    assert state.completed == stages_of(STACK)
    half = tmp_path / "half"
    with pytest.raises(InstallerError, match="pull access denied|images could not be pulled"):
        _run(_engine(Daemon(pull_exit=1)), half)
    with pytest.raises(InstallerError, match="did not finish"):
        existing_install(FFXI, half, platform_id=lambda: "linux")
