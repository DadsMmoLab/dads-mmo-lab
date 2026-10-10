"""T627 and T628: a Start whose build is gone, and a Rebuild the old fork's tree cannot take.

T627 (live, yulon-arch 2026-10-09): Start on a server whose `yulon.local/...` image was pruned
let `compose up` try to PULL it from a registry called `yulon.local`; the tab said only that the
server did not start, the database was left up and the realm row already rewritten. Start now
asks Docker for the image first, and a compose failure that names the gone image is said the
same way.

T628: Rebuild on a server made from the retired playerbots fork (`modules/mod-playerbots`, no
`modules/TortoiseBots`) compiled today's recipe for ~25 minutes and failed. It now refuses
before compiling, and Start's advice never points such a server at it.

The fakes are Docker's own CLI (`runner.run`); every check runs through the real code that asks.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any

import pytest

from tests.support_player_text import command_faults, text_faults
from tests.test_families_cmangos import Recorder, client_folder, engine, install
from tests.test_start_notices_a_missing_database import _DbDocker
from yulon import docker, runner, server_build_gone, server_build_presses
from yulon.catalog import composegen, native
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller import Controller, StartRefused
from yulon.ui import controller_view as controller_view_module

pytestmark = pytest.mark.usefixtures("real_database_read", "real_image_read")

TORTOISE = load_catalog().get("wow-tortoise")
SPEC = TORTOISE.container_spec()
CORE = TORTOISE.emulator.sources[0].dest
BOTS = TORTOISE.emulator.sources[1].dest

# Compose's own words for the pull it attempts, captured from yulon-arch (T627).
COMPOSE_SAID = (
    "docker compose up -d --no-deps tortoise-db tortoise-realmd tortoise-mangosd exited 1: "
    'Error response from daemon: failed to resolve reference "yulon.local/cmangos-tortoise-'
    'server:native-54fa64f0": failed to do request: Head "https://yulon.local/v2/cmangos-'
    'tortoise-server/manifests/native-54fa64f0": dial tcp: lookup yulon.local: Temporary '
    "failure in name resolution / Error response from daemon: No such image: "
    "yulon.local/cmangos-tortoise-server:native-54fa64f0"
)


class _ImageDocker(_DbDocker):
    """`_DbDocker`, plus `docker image inspect`: `present` is the set of image names it knows."""

    def __init__(self) -> None:
        super().__init__(TORTOISE)
        self.present: set[str] | None = set()
        self.silent = False
        # What `compose config` reports as each service's image; None = the real fake's answer.
        self.config_images: dict[str, str] | None = None
        self.config_rc = 0
        self.config_text: str | None = None

    def __call__(
        self, cmd: list[str], cwd: Path | None = None, timeout: float | None = None
    ) -> subprocess.CompletedProcess[str]:
        if "image" in cmd and "inspect" in cmd and cmd[-1].startswith("yulon.local/"):
            self.calls.append(cmd)
            if self.silent:
                return subprocess.CompletedProcess(
                    cmd, 1, "", "Cannot connect to the Docker daemon"
                )
            if self.present is not None and cmd[-1] in self.present:
                return subprocess.CompletedProcess(cmd, 0, "sha256:abc\n", "")
            return subprocess.CompletedProcess(
                cmd, 1, "", f"Error response from daemon: No such image: {cmd[-1]}\n"
            )
        if "compose" in cmd and "config" in cmd and "--format" in cmd:
            self.calls.append(cmd)
            if self.config_rc:
                return subprocess.CompletedProcess(cmd, self.config_rc, "", "yaml: line 3: bad")
            if self.config_text is not None:
                return subprocess.CompletedProcess(cmd, 0, self.config_text, "")
            if self.config_images is not None:
                services = {svc: {"image": img} for svc, img in self.config_images.items()}
                return subprocess.CompletedProcess(cmd, 0, json.dumps({"services": services}), "")
        return super().__call__(cmd, cwd, timeout)


@pytest.fixture
def box(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> _ImageDocker:
    fake = _ImageDocker()
    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(docker, "exec_stdin", fake.exec_stdin)
    monkeypatch.setattr(docker, "_POLL_INTERVAL_SECONDS", 0.05)
    plan = TORTOISE.install.password
    assert plan.file is not None
    (tmp_path / plan.file).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / plan.file).write_text("tortoise0123456789ab\n", encoding="utf-8")
    return fake


def _old_fork(server: Path) -> None:
    """The shape the retired fork left: its bots in the core's modules/, no TortoiseBots."""
    modules = server / CORE / "modules"
    (modules / "mod-playerbots").mkdir(parents=True)
    (modules / "mod-dungeon-clear").mkdir()
    (modules / "CMakeLists.txt").write_text("# the core's modules\n", encoding="utf-8")


def _new_stack(server: Path) -> None:
    (server / BOTS).mkdir(parents=True)


def _ups(fake: _DbDocker) -> list[list[str]]:
    return [c for c in fake.calls if c[:3] == ["docker", "compose", "up"]]


REBUILD = server_build_presses.under_server_build(server_build_presses.REBUILD)


# -- T627: Start -----------------------------------------------------------------------------


def test_start_with_the_build_gone_says_so_before_anything_is_started(
    box: _ImageDocker, tmp_path: Path
) -> None:
    _new_stack(tmp_path)
    with pytest.raises(StartRefused) as refused:
        Controller(SPEC, tmp_path).start()
    said = str(refused.value)
    assert "build is gone from Docker" in said
    assert REBUILD in said
    assert _ups(box) == [], "no compose up: nothing for compose to pull, no database left up"
    assert box.names.split() == []
    assert text_faults(said) == [] and command_faults(said) == []


def test_start_goes_on_when_the_image_is_there(box: _ImageDocker, tmp_path: Path) -> None:
    _new_stack(tmp_path)
    box.present = set(composegen.built_image_refs(TORTOISE, tmp_path))
    Controller(SPEC, tmp_path).start()
    assert any(SPEC.world in c for c in _ups(box))


def test_start_goes_on_when_docker_would_not_say_whether_the_image_is_there(
    box: _ImageDocker, tmp_path: Path
) -> None:
    box.silent = True
    _new_stack(tmp_path)
    Controller(SPEC, tmp_path).start()
    assert any(SPEC.world in c for c in _ups(box))


def test_start_does_not_point_an_old_fork_install_at_a_rebuild_that_cannot_work(
    box: _ImageDocker, tmp_path: Path
) -> None:
    _old_fork(tmp_path)
    with pytest.raises(StartRefused) as refused:
        Controller(SPEC, tmp_path).start()
    said = str(refused.value)
    assert "build is gone from Docker" in said
    assert server_build_presses.REBUILD not in said
    assert "new folder" in said
    assert _ups(box) == []


def _view_failed(qapp: object, tmp_path: Path, exc: Exception) -> Any:
    from tests.test_controller_view import _services
    from yulon.ui.controller_view import ControllerView

    view = ControllerView(TORTOISE, _services(_ImageDocker(), tmp_path, []), status_poll_ms=0)
    view._start_failed(exc)
    return view


def _compose_error(text: str) -> Exception:
    return docker.DockerCommandError(text)


def test_the_compose_pull_error_from_the_box_is_said_as_a_gone_build(
    qapp: object, box: _ImageDocker, tmp_path: Path
) -> None:
    """The text captured on yulon-arch, through the real Start-failure path (T627)."""
    _new_stack(tmp_path)
    view = _view_failed(qapp, tmp_path, _compose_error(COMPOSE_SAID))
    assert "build is gone from Docker" in view.problem_label.text()
    assert REBUILD in view.problem_label.text()
    assert view.problem_label.text() != controller_view_module.START_FAILED_BROKE
    assert "yulon.local" in view.problem_details.text(), "Docker's words stay under Details"


def test_the_compose_pull_error_on_an_old_fork_does_not_name_rebuild(
    qapp: object, box: _ImageDocker, tmp_path: Path
) -> None:
    _old_fork(tmp_path)
    view = _view_failed(qapp, tmp_path, _compose_error(COMPOSE_SAID))
    assert "build is gone from Docker" in view.problem_label.text()
    assert server_build_presses.REBUILD not in view.problem_label.text()


def test_another_compose_failure_is_still_the_broken_line(
    qapp: object, box: _ImageDocker, tmp_path: Path
) -> None:
    view = _view_failed(qapp, tmp_path, _compose_error("dependency failed to start: db exited (1)"))
    assert view.problem_label.text() == controller_view_module.START_FAILED_BROKE


# -- T628: Rebuild ---------------------------------------------------------------------------


def _installed(tmp_path: Path) -> tuple[Recorder, Path]:
    """A finished install, recorded as Tortoise's: the Tortoise stages need a real client's data.

    The record is what a Rebuild reads first (`game_id`), so a finished CMaNGOS install made
    through the real `run()` has that one line changed to this game's.
    """
    server = tmp_path / "srv"
    server.mkdir()
    rec = Recorder()
    install(rec, server, client_folder(tmp_path))
    record = server / native.STATE_FILE
    record.write_text(
        record.read_text(encoding="utf-8").replace("wow-tbc", "wow-tortoise"), encoding="utf-8"
    )
    return rec, server


def test_rebuild_on_an_old_fork_tree_refuses_before_compiling(tmp_path: Path) -> None:
    rec, server = _installed(tmp_path)
    _old_fork(server)
    builds = len([c for c in rec.calls if c.startswith("build")])
    recipe = (server / "Dockerfile").read_bytes() if (server / "Dockerfile").exists() else b""

    with pytest.raises(InstallerError) as refused:
        list(engine(rec, entry=TORTOISE).rebuild(InstallOptions(server_dir=server)))

    said = str(refused.value)
    assert "retired playerbots fork" in said and "new folder" in said
    assert "press Rebuild" not in said and server_build_presses.REBUILD not in said
    assert len([c for c in rec.calls if c.startswith("build")]) == builds, "no compile started"
    after = (server / "Dockerfile").read_bytes() if (server / "Dockerfile").exists() else b""
    assert after == recipe, "the recipe was not rewritten"


def test_the_rebuild_question_is_not_asked_of_an_old_fork_tree(tmp_path: Path) -> None:
    rec, server = _installed(tmp_path)
    _old_fork(server)
    refusal = engine(rec, entry=TORTOISE).rebuild_refusal_before_asking(server)
    assert refusal is not None and "retired playerbots fork" in refusal


def test_rebuild_on_the_new_stack_is_not_refused(tmp_path: Path) -> None:
    rec, server = _installed(tmp_path)
    (server / CORE / "modules" / "mod-playerbots").mkdir(parents=True)
    _new_stack(server)
    assert engine(rec, entry=TORTOISE).rebuild_refusal_before_asking(server) is None


def test_the_other_games_have_no_old_fork(tmp_path: Path) -> None:
    tbc = load_catalog().get("wow-tbc")
    (tmp_path / tbc.emulator.sources[0].dest / "modules" / "mod-playerbots").mkdir(parents=True)
    assert server_build_gone.rebuild_refusal(tbc, tmp_path) is None


def test_the_sentences_are_plain_words(tmp_path: Path) -> None:
    _old_fork(tmp_path)
    for said in (
        server_build_gone.gone_sentence(TORTOISE, tmp_path),
        server_build_gone.rebuild_refusal(TORTOISE, tmp_path) or "",
    ):
        assert said and text_faults(said) == [] and command_faults(said) == []


def test_a_pull_error_that_only_says_it_could_not_resolve_the_image_is_a_gone_build(
    qapp: object, box: _ImageDocker, tmp_path: Path
) -> None:
    """Compose's first line alone (no "No such image" beside it) is the same failure."""
    _new_stack(tmp_path)
    only_resolve = COMPOSE_SAID.split(" / ")[0]
    assert "No such image" not in only_resolve
    view = _view_failed(qapp, tmp_path, _compose_error(only_resolve))
    assert "build is gone from Docker" in view.problem_label.text()


# -- the id the images are named after, and every press that stops first ----------------------

RECORDED = "0123abcd"
ELSEWHERE_IMAGE = (
    f"yulon.local/cmangos-tortoise-server:native-{RECORDED}"  # spelled out, not derived
)


def _record(server: Path, ident: str = RECORDED) -> None:
    """The record Yu'lon wrote inside a distro: its id is not the hash of this folder's path."""
    (server / native.STATE_FILE).write_text(
        '{"version": 1, "game_id": "wow-tortoise", "family": "cmangos", '
        f'"install_id": "{ident}", "completed": []}}',
        encoding="utf-8",
    )


@pytest.fixture
def distro_docker(box: _ImageDocker, monkeypatch: pytest.MonkeyPatch) -> _ImageDocker:
    """Docker as a distro's own answers it: there is no `wsl.exe` on this machine to prefix with."""

    def ask(
        argv: list[str], cwd: Path | None = None, timeout: float | None = None, **_k: Any
    ) -> Any:
        return box(["docker", *argv], cwd, timeout)

    monkeypatch.setattr(docker, "_docker", ask)
    return box


def test_a_server_in_a_distro_is_asked_about_the_image_its_record_names(
    distro_docker: _ImageDocker, tmp_path: Path
) -> None:
    """Its images carry the recorded id; the folder's own hash names nothing there (T627 review)."""
    _new_stack(tmp_path)
    _record(tmp_path)
    distro_docker.present = {ELSEWHERE_IMAGE}
    assert composegen.built_image_refs(TORTOISE, tmp_path) != (ELSEWHERE_IMAGE,)
    Controller(SPEC, tmp_path, wsl_distro="Ubuntu").refuse_a_missing_image()


def test_a_server_in_a_distro_whose_recorded_image_is_gone_is_refused(
    distro_docker: _ImageDocker, tmp_path: Path
) -> None:
    _new_stack(tmp_path)
    _record(tmp_path)
    distro_docker.present = set(
        composegen.built_image_refs(TORTOISE, tmp_path)
    )  # the path-hash name only
    with pytest.raises(StartRefused):
        Controller(SPEC, tmp_path, wsl_distro="Ubuntu").refuse_a_missing_image()


def test_a_distro_record_with_no_usable_id_does_not_refuse(
    distro_docker: _ImageDocker, tmp_path: Path
) -> None:
    _new_stack(tmp_path)
    Controller(SPEC, tmp_path, wsl_distro="Ubuntu").refuse_a_missing_image()


def _running_here(box: _ImageDocker) -> None:
    box.names = "".join(f"{name}\n" for name in SPEC.compose_services())


@pytest.fixture
def nothing_may_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """A press that gets past the refusal fails at once instead of waiting on a real stop."""

    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("the press went on to stop or start something")

    for name in ("stop", "start", "remove", "stop_conflicting"):
        monkeypatch.setattr(Controller, name, forbidden)


def _stops(box: _ImageDocker) -> list[list[str]]:
    return [
        c
        for c in box.calls
        if c[:2] == ["docker", "stop"] or c[:3] == ["docker", "compose", "stop"]
    ]


@pytest.mark.parametrize("press", ["refuse_before_a_stop", "stop_conflicting_and_start"])
def test_a_press_that_stops_first_is_refused_before_it_stops_anything(
    box: _ImageDocker, nothing_may_stop: None, tmp_path: Path, press: str
) -> None:
    _new_stack(tmp_path)
    _running_here(box)
    with pytest.raises(StartRefused) as refused:
        getattr(Controller(SPEC, tmp_path), press)()
    assert "build is gone from Docker" in str(refused.value)
    assert _stops(box) == [] and _ups(box) == []


def _view(tmp_path: Path) -> Any:
    from tests.test_controller_view import _services
    from yulon.ui.controller_view import ControllerView

    services = _services(_ImageDocker(), tmp_path, [])
    services.controller = Controller(SPEC, tmp_path)
    return ControllerView(TORTOISE, services, status_poll_ms=0)


@pytest.mark.parametrize("press", ["_do_restart", "_do_recreate"])
def test_restart_and_recreate_leave_a_running_server_alone_when_its_build_is_gone(
    qapp: object, box: _ImageDocker, nothing_may_stop: None, tmp_path: Path, press: str
) -> None:
    _new_stack(tmp_path)
    _running_here(box)
    with pytest.raises(StartRefused) as refused:
        getattr(_view(tmp_path), press)()
    assert "build is gone from Docker" in str(refused.value)
    assert _stops(box) == []
    assert not [c for c in box.calls if c[:3] == ["docker", "compose", "down"] or "rm" in c[:3]]


def test_the_sentence_after_a_compose_failure_does_not_say_nothing_was_started(
    qapp: object, box: _ImageDocker, tmp_path: Path
) -> None:
    """The database may be up and the realm row written by then: PARTLY UP (T627)."""
    _new_stack(tmp_path)
    view = _view_failed(qapp, tmp_path, _compose_error(COMPOSE_SAID))
    said = view.problem_label.text()
    assert "Nothing was started" not in said and "may have started" in said
    assert REBUILD in said
    _old_fork(tmp_path)
    view = _view_failed(qapp, tmp_path, _compose_error(COMPOSE_SAID))
    assert "Nothing was started" not in view.problem_label.text()
    assert "Nothing was started" in str(
        pytest.raises(StartRefused, Controller(SPEC, tmp_path).start).value
    )


# -- the images the folder's own compose files name -----------------------------------------

NAMED = (
    "yulon.local/cmangos-tortoise-server:native-feedf00d"  # not this path's hash, not a record's
)


def _compose_names(box: _ImageDocker, server: Path, image: str = NAMED) -> None:
    """A folder whose compose files (on disk) name `image` for the world, as a moved one's do."""
    (server / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
    box.config_images = {
        SPEC.compose_services()[0]: "mariadb:10.6",
        SPEC.compose_services()[2]: image,
    }


def test_a_moved_folder_is_asked_about_the_image_its_compose_file_names(
    box: _ImageDocker, tmp_path: Path
) -> None:
    """Moved since it was made: the new path hashes to another id; the files name the old (T627)."""
    _new_stack(tmp_path)
    _record(tmp_path)  # a record carrying yet another id: the compose file is what is started
    _compose_names(box, tmp_path)
    box.present = {NAMED}
    Controller(SPEC, tmp_path).start()
    assert any(SPEC.world in c for c in _ups(box))


def test_a_compose_file_naming_an_image_that_is_gone_refuses(
    box: _ImageDocker, tmp_path: Path
) -> None:
    _new_stack(tmp_path)
    _compose_names(box, tmp_path)
    box.present = set(composegen.built_image_refs(TORTOISE, tmp_path))  # the path's own name only
    with pytest.raises(StartRefused) as refused:
        Controller(SPEC, tmp_path).start()
    assert "build is gone from Docker" in str(refused.value)
    assert _ups(box) == []


def test_a_distro_folder_is_asked_about_the_image_its_compose_file_names(
    distro_docker: _ImageDocker, tmp_path: Path
) -> None:
    _new_stack(tmp_path)
    _record(tmp_path)  # another id: only the compose file's name lets this pass
    _compose_names(distro_docker, tmp_path)
    distro_docker.present = {NAMED}
    assert NAMED != ELSEWHERE_IMAGE
    Controller(SPEC, tmp_path, wsl_distro="Ubuntu").refuse_a_missing_image()


def test_only_the_images_this_app_builds_are_asked_about(box: _ImageDocker, tmp_path: Path) -> None:
    """A database image compose would pull is not a gone build."""
    _new_stack(tmp_path)
    (tmp_path / composegen.BASE_FILE).write_text("services: {}\n", encoding="utf-8")
    box.config_images = {svc: "mariadb:10.6" for svc in SPEC.compose_services()}
    Controller(SPEC, tmp_path).refuse_a_missing_image()
    assert not [c for c in box.calls if "image" in c and "inspect" in c]


@pytest.mark.parametrize("trouble", ["failing", "not-json"])
def test_a_compose_file_that_cannot_be_read_does_not_refuse(
    box: _ImageDocker, tmp_path: Path, trouble: str, caplog: pytest.LogCaptureFixture
) -> None:
    _new_stack(tmp_path)
    (tmp_path / composegen.BASE_FILE).write_text("services: [\n", encoding="utf-8")
    if trouble == "failing":
        box.config_rc = 1
    else:
        box.config_text = "not json at all"
    Controller(SPEC, tmp_path).refuse_a_missing_image()
    warned = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("did not check that its build is in Docker" in m for m in warned), warned
