"""The movement maps in the background, after the server is up (T179 Task 4).

`yulon.catalog.families.mmaps` and its three hooks: the install's end
(`after_ready`), the rebuild routes' start (`before_rebuild`) and Uninstall
(`purge.Uninstaller`'s background-job seam). Docker is `FakeMmapsDocker`
(tests/support_trinitycore.py), a dict of containers by name, so every test
drives the job through the states a real daemon would report.

Review Focus 3 is the property most of these are about: pathfinding is switched
on in worldserver.conf ONLY after a complete run -- exit status the plan calls
finished AND at least `min_files` files -- and a failed, short, stopped or
vanished run leaves it off with its partial output removed. Nothing here proves
the real generator runs in the image (the live proof, Task 9).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support_trinitycore import CORE_DIR, FakeMmapsDocker
from tests.test_families_trinitycore import (  # noqa: F401 - fixtures, as pytest resolves them
    ENTRY,
    MAP_NAMES,
    VMAP_TREES,
    Machine,
    engine,
    install,
    known_password,
    machine,
)
from tests.test_purge import Recorder as PurgeRecorder
from yulon import docker, platform, purge
from yulon.catalog import composegen
from yulon.catalog.families import extract, mmaps
from yulon.catalog.installer import InstallerError, InstallOptions

INSTALL_ID = "0123abcd"
NAME = f"centurion-mmaps-{INSTALL_ID}"
MIN_FILES = 500
WORLD_CONF = (
    "[worldserver]\n"
    'DataDir = "/opt/trinitycore/data"\n'
    "# mmap.enablePathFinding\n"
    "mmap.enablePathFinding = 0\n"
    "Updates.EnableDatabases = 0\n"
)
T0 = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)


class Clock:
    """A clock the test moves."""

    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def box(machine: Machine) -> Machine:  # noqa: F811 - the fixture imported above
    return machine


def lay_server(server_dir: Path) -> None:
    """What `ready` leaves: map data for the start maps, and the switch off in worldserver.conf."""
    data = server_dir / "data"
    (data / "maps").mkdir(parents=True)
    (data / "vmaps").mkdir()
    (data / "dbc").mkdir()
    for name in MAP_NAMES:
        (data / "maps" / name).write_bytes(b"MAPS")
    for name in VMAP_TREES:
        (data / "vmaps" / name).write_bytes(b"VMAP_4.8")
    (data / "dbc" / "LiquidType.dbc").write_bytes(b"LIQUID")
    (server_dir / "etc").mkdir()
    (server_dir / "etc" / "worldserver.conf").write_text(WORLD_CONF, encoding="utf-8")


@pytest.fixture
def server(tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow-centurion-server"
    lay_server(server_dir)
    return server_dir


def conf_text(server_dir: Path) -> str:
    return (server_dir / "etc" / "worldserver.conf").read_text(encoding="utf-8")


def record(server_dir: Path) -> dict[str, object]:
    raw: dict[str, object] = json.loads((server_dir / mmaps.RECORD_FILE).read_text("utf-8"))
    return raw


def start(server_dir: Path, fake: FakeMmapsDocker, clock: Clock | None = None) -> str:
    return mmaps.start_mmaps(
        server_dir,
        ENTRY,
        runner=fake,
        clock=clock or Clock(),
        platform_id=lambda: "linux",
        install_id=INSTALL_ID,
        user_args=("--user", "1000:1000"),
    )


def status(
    server_dir: Path, fake: FakeMmapsDocker, clock: Clock | None = None
) -> mmaps.MmapsStatus:
    return mmaps.mmaps_status(
        server_dir, ENTRY, runner=fake, clock=clock or Clock(), install_id=INSTALL_ID
    )


def output(server_dir: Path) -> list[str]:
    return sorted(path.name for path in (server_dir / "data" / "mmaps").iterdir())


# -- starting ---------------------------------------------------------------------------


def test_a_start_runs_the_generator_detached_and_records_it_running(server: Path) -> None:
    fake = FakeMmapsDocker()
    said = start(server, fake)
    assert "in the background" in said
    (spec,) = fake.started
    assert fake.calls[-1] == f"run:{NAME}"
    assert spec.argv == (f"{CORE_DIR}/bin/mmaps_generator",)
    assert (
        spec.image
        == composegen.built_image_refs(
            ENTRY, server, platform_id=lambda: "linux", install_id=INSTALL_ID
        )[0]
    )
    data = server / "data"
    assert spec.mounts == (
        docker.Mount(data, mmaps.WORK_MOUNT, read_only=True),
        docker.Mount(data / "mmaps", f"{mmaps.WORK_MOUNT}/mmaps"),
    ), "data/ read-only, only data/mmaps writable"
    assert spec.workdir == mmaps.WORK_MOUNT
    assert spec.user_args == ("--user", "1000:1000")
    assert spec.security_args == extract.EXTRACT_HARDENING
    saved = record(server)
    assert saved["state"] == "running"
    assert saved["container"] == NAME
    assert saved["container_id"] == fake.jobs[NAME].container_id
    assert saved["started"] == "2026-10-02T12:00:00.000000Z"
    assert "mmap.enablePathFinding = 0" in conf_text(server)


def test_the_detached_argv_keeps_the_container_to_read_its_exit_and_log() -> None:
    spec = docker.ContainerRun(
        image="img:tag",
        argv=("/opt/trinitycore/bin/mmaps_generator",),
        mounts=(docker.Mount(Path("/srv/data"), "/out", read_only=True),),
        workdir="/out",
    )
    argv = spec.to_detached_argv(NAME)
    assert argv[:4] == ["run", "-d", "--name", NAME]
    assert "--rm" not in argv
    assert argv[4:] == spec.to_argv()[2:], "the same options as an attached run"
    with pytest.raises(ValueError):
        spec.to_detached_argv("")


def test_never_two_jobs_at_once(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    again = start(server, fake)
    assert again == "Pathfinding data is already being made in the background."
    assert [call for call in fake.calls if call.startswith("run:")] == [f"run:{NAME}"]
    assert len(fake.jobs) == 1


def test_a_start_refuses_while_the_map_data_is_not_there(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    lay_server(server_dir)
    (server_dir / "data" / "vmaps" / "530.vmtree").unlink()
    fake = FakeMmapsDocker()
    with pytest.raises(mmaps.MmapsError, match="530.vmtree"):
        start(server_dir, fake)
    assert fake.started == []
    assert not (server_dir / mmaps.RECORD_FILE).exists()


def test_docker_refusing_the_start_is_a_failed_job_that_can_start_again(server: Path) -> None:
    fake = FakeMmapsDocker()
    fake.refuse_run = "Unable to find image"
    with pytest.raises(mmaps.MmapsError, match="Unable to find image"):
        start(server, fake)
    assert record(server)["state"] == "failed"
    now = status(server, fake)
    assert now.state == "failed" and now.can_start
    fake.refuse_run = ""
    start(server, fake)
    assert record(server)["state"] == "running"


def test_a_start_over_an_old_partial_set_empties_it_first(server: Path) -> None:
    """The generator skips every tile it finds a file for: old files would look finished."""
    (server / "data" / "mmaps").mkdir()
    (server / "data" / "mmaps" / "0003232.mmtile").write_bytes(b"half")
    fake = FakeMmapsDocker()
    start(server, fake)
    assert fake.mmaps_at_run == [[]]


# -- progress ---------------------------------------------------------------------------


def test_progress_is_read_from_the_generators_own_lines(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.say(
        "Discovering maps... found 61.",
        "Using 4 threads to generate mmaps",
        "[Map 000] We have 1031 tiles.",
        "36% [Map 000] Building tile [31,48]",
        "[Map 000] [48,31]:  Building movemap tiles...",
        "37% [Map 001] Building tile [32,48]",
        "[Map 001] [48,32]:  Building navmesh tile...",
    )
    now = status(server, fake)
    assert (now.state, now.percent, now.map) == ("running", 37, 1)
    assert now.line() == "Pathfinding data: 37 % — the server already runs without it."
    assert record(server)["percent"] == 37
    assert not now.restart_needed and not now.pathfinding_on


def test_a_fresh_status_after_an_app_restart_reads_the_same_job(server: Path) -> None:
    """The record is on disk and the container in Docker: a new process sees both."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.say("12% [Map 000] Building tile [30,30]")
    first = status(server, fake)
    restarted = FakeMmapsDocker()
    restarted.jobs = fake.jobs
    second = mmaps.mmaps_status(server, ENTRY, runner=restarted, install_id=INSTALL_ID)
    assert second == first
    assert second.state == "running" and second.percent == 12


def test_docker_not_answering_changes_nothing(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.answers = False
    now = status(server, fake)
    assert now.state == "running" and now.docker_unanswered
    assert "Docker did not answer" in now.line()
    assert output(server) == ["0000000.mmtile", "0000001.mmtile", "0000002.mmtile"]
    assert record(server)["state"] == "running"


# -- finishing --------------------------------------------------------------------------


def test_a_complete_run_switches_pathfinding_on_and_asks_for_a_restart(server: Path) -> None:
    fake = FakeMmapsDocker()
    clock = Clock()
    start(server, fake, clock)
    clock.now = datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)
    fake.finish(0, tiles=MIN_FILES)
    now = status(server, fake, clock)
    assert now.state == "done" and now.percent == 100
    assert now.pathfinding_on and now.restart_needed
    assert now.line() == "Pathfinding data is ready. Restart the server to use it."
    text = conf_text(server)
    assert "mmap.enablePathFinding = 1\n" in text and "mmap.enablePathFinding = 0" not in text
    assert "# mmap.enablePathFinding\n" in text, "the rest of the conf as it was"
    assert NAME not in fake.jobs, "the finished container is removed once read"
    assert record(server)["pathfinding_on_at"] == "2026-10-02T15:00:00.000000Z"
    assert len(output(server)) == MIN_FILES


def test_done_is_switched_on_once_and_needs_no_restart_after_the_next_start(
    server: Path,
) -> None:
    fake = FakeMmapsDocker()
    clock = Clock()
    start(server, fake, clock)
    clock.now = datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)
    fake.finish(0, tiles=MIN_FILES)
    status(server, fake, clock)
    conf = server / "etc" / "worldserver.conf"
    stamp = conf.stat().st_mtime_ns
    clock.now = datetime(2026, 10, 2, 16, 0, 0, tzinfo=UTC)
    again = status(server, fake, clock)
    assert again.restart_needed and conf.stat().st_mtime_ns == stamp
    fake.world_started_at = "2026-10-02T15:30:01.000000001Z"
    after = status(server, fake, clock)
    assert after.state == "done" and after.pathfinding_on and not after.restart_needed
    assert after.line() == "Pathfinding data is ready and in use."


def test_a_player_who_switches_it_off_again_is_not_overruled(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(0, tiles=MIN_FILES)
    status(server, fake)
    conf = server / "etc" / "worldserver.conf"
    conf.write_text(conf_text(server).replace("PathFinding = 1", "PathFinding = 0"), "utf-8")
    now = status(server, fake)
    assert now.state == "done" and not now.pathfinding_on and not now.restart_needed
    assert "mmap.enablePathFinding = 0" in conf_text(server)


@pytest.mark.parametrize(
    ("code", "tiles", "why"),
    [
        (253, MIN_FILES, "exit 253"),
        (0, MIN_FILES - 1, f"{MIN_FILES - 1} files where at least {MIN_FILES}"),
        (139, 7, "exit 139"),
    ],
    ids=("failed-exit", "short-set", "crashed"),
)
def test_an_incomplete_run_never_switches_pathfinding_on(
    server: Path, code: int, tiles: int, why: str
) -> None:
    """Review Focus 3: only a run that finished AND made a whole set turns it on."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(code, tiles=tiles)
    now = status(server, fake)
    assert now.state == "failed" and now.can_start and not now.pathfinding_on
    assert why in now.error
    assert "mmap.enablePathFinding = 0" in conf_text(server)
    assert output(server) == [], "the partial output is removed"
    assert NAME not in fake.jobs
    start(server, fake)
    assert record(server)["state"] == "running", "a failed job starts again from scratch"


def test_a_container_that_vanished_while_running_is_a_failed_run_cleared(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(40)
    fake.vanish()
    now = status(server, fake)
    assert now.state == "failed"
    assert "container is gone" in now.error
    assert output(server) == []
    assert "mmap.enablePathFinding = 0" in conf_text(server)
    assert now.line().endswith("It can be started again.")


def test_an_app_that_died_between_queued_and_the_container_finds_a_failed_start(
    server: Path,
) -> None:
    fake = FakeMmapsDocker()
    (server / mmaps.RECORD_FILE).write_text(
        json.dumps({"version": 1, "state": "queued", "container": NAME}), "utf-8"
    )
    now = status(server, fake)
    assert now.state == "failed" and "interrupted" in now.error


def test_a_run_that_finished_while_nobody_asked_is_kept_by_a_stop(server: Path) -> None:
    """A Stop (or a rebuild) after an unseen finish must not throw a complete set away."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(0, tiles=MIN_FILES)
    said = mmaps.stop_mmaps(server, ENTRY, runner=fake, install_id=INSTALL_ID)
    assert said == "Pathfinding data is already made; there was nothing to stop."
    assert len(output(server)) == MIN_FILES
    assert "mmap.enablePathFinding = 1" in conf_text(server)


def test_a_lost_record_never_leaves_pathfinding_on_over_emptied_maps(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(0, tiles=MIN_FILES)
    status(server, fake)
    (server / mmaps.RECORD_FILE).write_text("{not json", "utf-8")
    assert status(server, fake).state == "failed"
    start(server, fake)
    assert "mmap.enablePathFinding = 0" in conf_text(server)
    assert output(server) == []


# -- stopping ---------------------------------------------------------------------------


def test_stop_removes_the_container_and_the_partial_output(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(12)
    said = mmaps.stop_mmaps(server, ENTRY, runner=fake, install_id=INSTALL_ID)
    assert said.startswith("Stopped making the pathfinding data")
    assert NAME not in fake.jobs
    assert output(server) == []
    assert not (server / mmaps.RECORD_FILE).exists()
    assert status(server, fake).state == "not-started"
    assert "mmap.enablePathFinding = 0" in conf_text(server)


def test_a_stop_docker_refuses_keeps_the_record(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.refuse_remove = "permission denied"
    with pytest.raises(mmaps.MmapsError, match="could not be stopped"):
        mmaps.stop_mmaps(server, ENTRY, runner=fake, install_id=INSTALL_ID)
    assert record(server)["state"] == "running"


# -- the hooks: install, rebuild, update, uninstall --------------------------------------


def job_name(box: Machine) -> str:
    return f"centurion-mmaps-{composegen.install_id(box.server_dir, platform_id=lambda: 'linux')}"


def test_a_whole_install_ends_by_starting_the_job_and_the_install_is_finished(box: Machine) -> None:
    said = install(box)
    assert said[-1] == f"{ENTRY.name} is installed and running in {box.server_dir}"
    assert any("Making the pathfinding data in the background" in line for line in said)
    assert list(box.mmaps.jobs) == [job_name(box)]
    assert record(box.server_dir)["state"] == "running"
    assert "mmap.enablePathFinding = 0" in conf_text(box.server_dir)
    status_now = engine(box).mmaps_status(box.server_dir)
    assert status_now.state == "running"


def test_pressing_install_again_does_not_start_a_second_job(box: Machine) -> None:
    install(box)
    said = install(box, world_running=lambda _container: False)
    assert "Pathfinding data is already being made in the background." in said
    assert len(box.mmaps.started) == 1


def test_a_job_that_cannot_start_is_a_warning_and_the_install_still_finishes(
    box: Machine,
) -> None:
    box.mmaps.refuse_run = "no space left on device"
    said = install(box)
    assert said[-1] == f"{ENTRY.name} is installed and running in {box.server_dir}"
    (warning,) = [line for line in said if line.startswith("warning: the pathfinding")]
    assert "no space left on device" in warning


def test_a_rebuild_stops_the_job_first_and_starts_it_again_after(box: Machine) -> None:
    install(box)
    box.mmaps.write_tiles(30)
    eng = engine(box)
    seen: list[tuple[str, bool, list[str]]] = []
    for line in eng.rebuild(InstallOptions(server_dir=box.server_dir)):
        if line.startswith("--- "):
            seen.append((line, bool(box.mmaps.jobs), output(box.server_dir)))
    assert [line for line, _running, _files in seen] == [
        "--- write-dockerfile",
        "--- build",
        "--- recreate",
        "--- ready",
    ]
    stopped = [call for call in box.mmaps.calls if call.startswith("remove:")]
    assert stopped, "the job was stopped"
    assert all(not running and not files for _line, running, files in seen), (
        "no stage of the rebuild ran beside the job or its partial output",
        seen,
    )
    assert "mmap.enablePathFinding = 0" in conf_text(box.server_dir)
    assert len(box.mmaps.started) == 2, "started again once the rebuilt server was ready"
    assert record(box.server_dir)["state"] == "running"


def test_a_rebuild_whose_stop_fails_starts_nothing(box: Machine) -> None:
    install(box)
    box.rec.calls.clear()
    box.mmaps.refuse_remove = "daemon not answering"
    with pytest.raises(InstallerError, match="the rebuild was not started"):
        list(engine(box).rebuild(InstallOptions(server_dir=box.server_dir)))
    assert "build" not in box.rec.calls
    assert record(box.server_dir)["state"] == "running"


def test_a_rebuild_of_a_finished_set_leaves_it_and_its_switch(box: Machine) -> None:
    install(box)
    box.mmaps.finish(0, tiles=MIN_FILES)
    list(engine(box).rebuild(InstallOptions(server_dir=box.server_dir)))
    assert len(box.mmaps.started) == 1
    assert len(output(box.server_dir)) == MIN_FILES
    assert "mmap.enablePathFinding = 1" in conf_text(box.server_dir)


def test_the_update_routes_stop_the_job_before_the_first_fetch(
    box: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    install(box)
    eng = engine(box)
    order: list[str] = []
    monkeypatch.setattr(
        eng, "_refuse_unless_updatable", lambda server_dir, moving: order.append("refusals") or []
    )
    monkeypatch.setattr(eng, "_release_targets", lambda plan, ok=(): {})
    monkeypatch.setattr(
        eng, "rebuild", lambda opts, cancel=None: iter([order.append("rebuild") or "rebuilt"])
    )
    real = eng.before_rebuild

    def before(server_dir: Path, route: str) -> object:
        order.append(f"stop:{route}")
        return real(server_dir, route)

    monkeypatch.setattr(eng, "before_rebuild", before)
    list(eng.update_to_latest(InstallOptions(server_dir=box.server_dir), to_pin=True))
    assert order == ["refusals", "stop:the return to the tested commit", "rebuild"]
    assert not box.mmaps.jobs and output(box.server_dir) == []


@pytest.fixture
def real_seam(box: Machine, monkeypatch: pytest.MonkeyPatch) -> Machine:
    """`purge.Uninstaller`'s OWN seam, reaching this machine's Docker and this platform's id."""
    monkeypatch.setattr(mmaps, "DockerRunner", lambda: box.mmaps)
    monkeypatch.setattr(platform, "detect", lambda: "linux")
    return box


def test_uninstall_removes_the_job_before_the_containers_and_the_folder(
    real_seam: Machine,
) -> None:
    box = real_seam
    install(box)
    name = job_name(box)
    rec = PurgeRecorder(box.server_dir, remove_folder=purge.remove_tree)

    def remove_containers() -> bool:
        rec.order.append(f"remove_containers (job left: {sorted(box.mmaps.jobs)})")
        return True

    rec.uninstaller(game=ENTRY.id, remove_containers=remove_containers).run(keep_characters=False)
    assert f"remove:{name}" in box.mmaps.calls and name not in box.mmaps.jobs
    assert "remove_containers (job left: [])" in rec.order
    assert not box.server_dir.exists()


def test_uninstall_refuses_when_the_job_cannot_be_removed(real_seam: Machine) -> None:
    box = real_seam
    install(box)
    box.mmaps.refuse_remove = "permission denied"
    rec = PurgeRecorder(box.server_dir)
    with pytest.raises(purge.PurgeError, match="could not be stopped"):
        rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    assert "remove_containers" not in rec.order
    assert not [entry for entry in rec.order if entry.startswith("remove_")]
    assert box.server_dir.exists()


def test_the_real_uninstall_seam_asks_docker_nothing_without_a_record(tmp_path: Path) -> None:
    """Every other family's uninstall goes through it too: no record, no Docker."""
    rec = PurgeRecorder(tmp_path)
    rec.uninstaller().run(keep_characters=False)
    assert rec.order.index("remove_containers") > 0


def test_uninstall_leaves_a_container_another_folder_named(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    (server_dir / mmaps.RECORD_FILE).write_text(
        json.dumps({"state": "running", "container": "centurion-mmaps-ffffffff"}), "utf-8"
    )
    fake = FakeMmapsDocker()
    mmaps.remove_for_uninstall(server_dir, runner=fake, platform_id=lambda: "linux")
    assert fake.calls == []


# -- what it is offered for ---------------------------------------------------------------


def test_only_a_background_trinitycore_block_has_a_job(tmp_path: Path) -> None:
    from yulon.catalog.catalog import load_catalog

    for entry in load_catalog().games:
        assert mmaps.background_block(entry) is None, entry.id
    assert mmaps.background_block(ENTRY) is not None
    wotlk = load_catalog().get("wow-wotlk")
    with pytest.raises(mmaps.MmapsError, match="during the install"):
        mmaps.mmaps_status(tmp_path, wotlk, runner=FakeMmapsDocker(), install_id=INSTALL_ID)


def test_a_record_edited_to_name_another_container_never_reaches_it(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    raw = record(server)
    raw["container"] = "centurion-worldserver"
    (server / mmaps.RECORD_FILE).write_text(json.dumps(raw), "utf-8")
    status(server, fake)
    mmaps.stop_mmaps(server, ENTRY, runner=fake, install_id=INSTALL_ID)
    touched = {call.split(":", 1)[1] for call in fake.calls if ":" in call}
    assert touched == {NAME}


# -- the docker functions -----------------------------------------------------------------


def _answer(
    monkeypatch: pytest.MonkeyPatch, code: int, out: str = "", err: str = ""
) -> list[list[str]]:
    import subprocess

    asked: list[list[str]] = []

    def fake_docker(argv: list[str], *args: object, **kwargs: object) -> object:
        asked.append(argv)
        return subprocess.CompletedProcess(["docker", *argv], code, out, err)

    monkeypatch.setattr(docker, "_docker", fake_docker)
    return asked


def test_container_exit_reads_the_status_the_code_and_the_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _answer(monkeypatch, 0, "abc123\texited\t253\t2026-10-02T15:00:00.1Z\n")
    assert docker.container_exit(NAME) == docker.ContainerExit(
        "exited", 253, "2026-10-02T15:00:00.1Z", "abc123"
    )
    assert asked[0][:2] == ["inspect", NAME]


def test_container_exit_keeps_gone_apart_from_unanswered(monkeypatch: pytest.MonkeyPatch) -> None:
    _answer(monkeypatch, 1, err=f"Error: No such object: {NAME}")
    assert docker.container_exit(NAME) == docker.ContainerExit(missing=True)
    _answer(monkeypatch, 1, err="Cannot connect to the Docker daemon")
    assert docker.container_exit(NAME) == docker.ContainerExit()


def test_removing_a_container_that_is_already_gone_is_not_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    asked = _answer(monkeypatch, 1, err=f"Error response from daemon: No such container: {NAME}")
    docker.remove_container(NAME)
    assert asked == [["rm", "-f", NAME]]
    _answer(monkeypatch, 1, err="permission denied")
    with pytest.raises(docker.DockerCommandError, match="permission denied"):
        docker.remove_container(NAME)
