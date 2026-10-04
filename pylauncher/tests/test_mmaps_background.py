"""The movement maps in the background, after the server is up (T179 Task 4).

`yulon.catalog.families.mmaps` and its three hooks: the install's end
(`after_ready`), the rebuild routes' start (`before_rebuild`) and Uninstall
(`purge.Uninstaller`'s background-job seam). Docker is `FakeMmapsDocker`
(tests/support_trinitycore.py), a dict of containers by name, so every test
drives the job through the states a real daemon would report.

Review Focus 3 is the property most of these are about: pathfinding is switched
on in worldserver.conf ONLY after a complete run -- exit status the plan calls
finished AND at least `min_files` files -- and a failed, short, stopped or
vanished run leaves it off. Since T209 such a run keeps its finished tiles and
removes only a cut-off one, and the next start continues from them while the
map data is the one the run began with. Tiles here are real bytes laid out as
the pinned source's `MmapTileHeader` (`tests/support_trinitycore.mmtile`).
Nothing here proves the real generator runs in the image (the live proof).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tests.support_trinitycore import (
    CORE_DIR,
    MMAP_MAGIC,
    TILE_HEADER,
    FakeJob,
    FakeMmapsDocker,
    mmtile,
)
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
    (data / extract.EVIDENCE_FILE).write_text('{"plan_hash": "first"}\n', encoding="utf-8")
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
    assert spec.argv == (f"{CORE_DIR}/bin/mmaps_generator", "--threads", "4")
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
    """The generator skips every tile it finds a file for: old files would look finished.

    WHOLE tiles with no record (T209): nothing says which map data they were made
    from, so even a tile the resume would keep goes.
    """
    (server / "data" / "mmaps").mkdir()
    (server / "data" / "mmaps" / "0003232.mmtile").write_bytes(mmtile())
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
    assert len(output(server)) == tiles, "its finished tiles are kept (T209)"
    assert NAME not in fake.jobs
    start(server, fake)
    assert record(server)["state"] == "running", "a failed job starts again"
    assert len(fake.mmaps_at_run[-1]) == tiles, "and continues from the tiles it kept"


def test_a_container_that_vanished_while_running_is_a_failed_run_that_keeps_its_tiles(
    server: Path,
) -> None:
    """Docker losing the container (a restart of Docker Desktop) keeps the finished tiles."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(40)
    fake.write_cut_tile()
    fake.vanish()
    now = status(server, fake)
    assert now.state == "failed" and now.kept == 40
    assert "container is gone" in now.error
    assert len(output(server)) == 40 and "0013251.mmtile" not in output(server)
    assert "mmap.enablePathFinding = 0" in conf_text(server)
    assert now.line().endswith(
        "Its 40 finished tiles are kept, and the next run continues from there."
    )
    start(server, fake)
    assert len(fake.mmaps_at_run[-1]) == 40
    assert fake.started[-1].argv[-2:] == ("--threads", "4"), "not a crash: the usual threads"


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


def test_stop_removes_the_container_and_keeps_the_finished_tiles(server: Path) -> None:
    """The player's Stop (T209): the container goes, the finished tiles stay, a start continues."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(12)
    fake.write_cut_tile()
    said = mmaps.stop_mmaps(server, ENTRY, runner=fake, install_id=INSTALL_ID)
    assert said == (
        "Stopped making the pathfinding data. Its 12 finished tiles are kept, and the next "
        "run continues from there. Pathfinding stays off until a run finishes."
    )
    assert NAME not in fake.jobs
    assert len(output(server)) == 12 and "0013251.mmtile" not in output(server)
    now = status(server, fake)
    assert now.state == "failed" and now.can_start and now.kept == 12
    assert now.line() == (
        "Pathfinding data stopped part-way: you stopped it. Its 12 finished tiles are kept, "
        "and the next run continues from there."
    )
    assert "mmap.enablePathFinding = 0" in conf_text(server)
    start(server, fake)
    assert len(fake.mmaps_at_run[-1]) == 12, "the next start continues from them"
    assert fake.started[-1].argv[-2:] == ("--threads", "4"), "a Stop is not a crash"


def test_a_stop_before_any_tile_was_finished_leaves_nothing_behind(server: Path) -> None:
    """Nothing to keep: as before T209, no record and not started."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_cut_tile()
    said = mmaps.stop_mmaps(server, ENTRY, runner=fake, install_id=INSTALL_ID)
    assert said.startswith("Stopped making the pathfinding data and removed what it had made")
    assert output(server) == []
    assert not (server / mmaps.RECORD_FILE).exists()
    assert status(server, fake).state == "not-started"


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
    """Rebuild keeps the finished tiles (T209, same pin) and the run continues from them."""
    install(box)
    box.mmaps.write_tiles(30)
    box.mmaps.write_cut_tile()
    eng = engine(box)
    said: list[str] = []
    seen: list[tuple[str, bool, list[str]]] = []
    for line in eng.rebuild(InstallOptions(server_dir=box.server_dir)):
        said.append(line)
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
    assert all(not running for _line, running, _files in seen), (
        "no stage of the rebuild ran beside the job",
        seen,
    )
    assert all(len(files) == 30 for _line, _running, files in seen), "only the cut tile went"
    assert (
        "Stopped making the pathfinding data before the rebuild; its 30 finished tiles are "
        "kept and pathfinding stays off. It continues from there once the server has been "
        "rebuilt, or from the Server tab."
    ) in said
    assert "mmap.enablePathFinding = 0" in conf_text(box.server_dir)
    assert len(box.mmaps.started) == 2, "started again once the rebuilt server was ready"
    assert len(box.mmaps.mmaps_at_run[-1]) == 30, "continuing from the kept tiles"
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


def test_the_update_routes_stop_the_job_after_the_source_checks_and_before_the_rebuild(
    box: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """After every refusal -- the moved sources' own check included (T179 Task 6) -- and
    before the compile: a press the source checks refuse leaves the job running."""
    install(box)
    eng = engine(box)
    order: list[str] = []
    monkeypatch.setattr(
        eng, "_refuse_unless_updatable", lambda server_dir, moving: order.append("refusals") or []
    )
    monkeypatch.setattr(eng, "_release_targets", lambda plan, ok=(): {})
    monkeypatch.setattr(
        eng,
        "rebuild",
        lambda opts, cancel=None, **_kw: iter([order.append("rebuild") or "rebuilt"]),
    )
    real_check = eng.check_moved_sources

    def check(server_dir: Path, moved: object, *, to_pin: bool) -> object:
        order.append("source checks")
        return real_check(server_dir, moved, to_pin=to_pin)  # type: ignore[arg-type]

    real = eng.before_rebuild

    def before(server_dir: Path, route: str, press: str) -> object:
        order.append(f"stop:{route}:{press}")
        return real(server_dir, route, press)

    monkeypatch.setattr(eng, "check_moved_sources", check)
    monkeypatch.setattr(eng, "before_rebuild", before)
    box.mmaps.write_tiles(30)
    said = list(eng.update_to_latest(InstallOptions(server_dir=box.server_dir), to_pin=True))
    assert order == [
        "refusals",
        "source checks",
        "stop:the return to the tested commit:Return to the tested pin…",
        "rebuild",
    ]
    assert not box.mmaps.jobs and output(box.server_dir) == [], "a new pin clears them (T209)"
    assert not (box.server_dir / mmaps.RECORD_FILE).exists()
    assert (
        "Stopped making the pathfinding data before the return to the tested commit; what it "
        "had made so far was removed and pathfinding stays off. It starts again from the "
        "beginning once the server has been rebuilt, or from the Server tab."
    ) in said


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


class NoDocker:
    """A runner that fails the test on ANY call: what "asks Docker nothing" means."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"the uninstall asked the job's Docker for {name}")


def test_the_real_uninstall_seam_asks_docker_nothing_without_a_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every other family's uninstall goes through it too: no record, no Docker."""
    monkeypatch.setattr(mmaps, "DockerRunner", NoDocker)
    monkeypatch.setattr(purge, "catalog_entry", NoDocker().__getattr__)
    rec = PurgeRecorder(tmp_path)
    rec.uninstaller().run(keep_characters=False)
    assert "remove_containers" in rec.order


def test_uninstall_removes_a_job_whose_record_cannot_be_read_by_its_derived_name(
    real_seam: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A record nobody can read says nothing about the container, which may still run."""
    box = real_seam
    install(box)
    name = job_name(box)
    (box.server_dir / mmaps.RECORD_FILE).write_text("{torn", "utf-8")
    monkeypatch.setattr(purge, "catalog_entry", lambda game: ENTRY if game == ENTRY.id else None)
    rec = PurgeRecorder(box.server_dir, remove_folder=purge.remove_tree)
    rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    assert f"remove:{name}" in box.mmaps.calls and name not in box.mmaps.jobs


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
        # Centurion, the one shipped TrinityCore entry (T179 Task 7), and no other.
        has_job = mmaps.background_block(entry) is not None
        assert has_job is (entry.id == "wow-centurion"), entry.id
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


# -- fix round 1 ----------------------------------------------------------------------------


def test_a_route_stops_a_job_whose_record_cannot_be_read(server: Path) -> None:
    """Unreadable is not "not running": the derived container goes, and its output."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(9)
    (server / mmaps.RECORD_FILE).write_text("{torn", "utf-8")
    said = mmaps.stop_for_route(
        server, ENTRY, "the rebuild", clear=False, runner=fake, install_id=INSTALL_ID
    )
    assert said is not None and said.startswith("Stopped making the pathfinding data")
    assert NAME not in fake.jobs and output(server) == []
    assert not (server / mmaps.RECORD_FILE).exists()


@pytest.mark.parametrize(
    ("threads", "cpus", "expected"),
    [("half", 8, "4"), ("half", 13, "6"), ("half", 1, "1"), ("half", None, "1"), (3, 16, "3")],
)
def test_the_generators_threads_come_from_the_daemons_cores(
    server: Path, threads: object, cpus: int | None, expected: str
) -> None:
    """Half the cores of the daemon running it (Docker Desktop's VM, not this host), at least 1."""
    raw = ENTRY.model_dump(mode="json")
    raw["install"]["native"]["trinitycore"]["mmaps"]["threads"] = threads
    entry = type(ENTRY).model_validate(raw)
    fake = FakeMmapsDocker()
    fake.ncpu = cpus
    mmaps.start_mmaps(
        server,
        entry,
        runner=fake,
        platform_id=lambda: "linux",
        install_id=INSTALL_ID,
        user_args=(),
    )
    assert fake.started[0].argv[-2:] == ("--threads", expected)


def test_the_threads_option_refuses_nonsense() -> None:
    raw = ENTRY.model_dump(mode="json")
    for bad in (0, -2, "all", "half ", True, "4", 2.0):
        raw["install"]["native"]["trinitycore"]["mmaps"]["threads"] = bad
        with pytest.raises(ValueError):
            type(ENTRY).model_validate(raw)


def test_a_finished_set_that_was_emptied_goes_back_to_not_started_and_off(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(0, tiles=MIN_FILES)
    assert status(server, fake).pathfinding_on
    for tile in (server / "data" / "mmaps").iterdir():
        tile.unlink()
    now = status(server, fake)
    assert now.state == "not-started" and now.can_start and not now.pathfinding_on
    assert "mmap.enablePathFinding = 0" in conf_text(server)
    assert not (server / mmaps.RECORD_FILE).exists()


def test_every_docker_call_is_bounded(server: Path) -> None:
    """Under the lock, a hung daemon must not hold a poll or a rebuild's hook for ever."""
    fake = FakeMmapsDocker()
    start(server, fake)
    status(server, fake)
    mmaps.stop_mmaps(server, ENTRY, runner=fake, install_id=INSTALL_ID)
    reads = [t for call, t in fake.timeouts if call in ("inspect", "log_tail", "started_at")]
    writes = [t for call, t in fake.timeouts if call in ("run", "remove", "cpus")]
    assert reads and all(0 < t <= mmaps.STATUS_TIMEOUT == 30 for t in reads)
    assert writes and all(0 < t <= mmaps.CHANGE_TIMEOUT == 120 for t in writes)
    assert {call for call, _t in fake.timeouts} == {
        "inspect",
        "log_tail",
        "run",
        "remove",
        "cpus",
    }


def test_a_hung_daemon_reads_as_unanswered_and_refuses_the_route(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.hang = True
    assert status(server, fake).docker_unanswered
    with pytest.raises(mmaps.MmapsError, match="the rebuild was not started"):
        mmaps.stop_for_route(
            server, ENTRY, "the rebuild", clear=False, runner=fake, install_id=INSTALL_ID
        )
    assert record(server)["state"] == "running"


def test_the_real_runner_hands_every_call_its_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import subprocess

    seen: list[tuple[str, object]] = []

    def fake_docker(argv: list[str], *args: object, **kwargs: object) -> object:
        seen.append((argv[0], kwargs.get("timeout")))
        return subprocess.CompletedProcess(["docker", *argv], 0, "8\n", "")

    monkeypatch.setattr(docker, "_docker", fake_docker)
    run = mmaps.DockerRunner()
    spec = docker.ContainerRun(image="img", argv=("x",))
    run.run_detached(spec, NAME, timeout=111)
    run.inspect(NAME, timeout=22)
    run.log_tail(NAME, 5, timeout=23)
    run.remove(NAME, timeout=112)
    run.started_at("w", timeout=24)
    assert run.cpus(timeout=25) == 8
    assert seen == [
        ("run", 111),
        ("inspect", 22),
        ("logs", 23),
        ("rm", 112),
        ("inspect", 24),
        ("info", 25),
    ]


@pytest.mark.parametrize(
    ("world_started", "due"),
    [
        ("2026-10-02T14:59:30.000000000Z", False),
        ("2026-10-02T15:00:45.000000000Z", False),
        ("2026-10-02T14:58:30.000000000Z", True),
    ],
    ids=("30s-before-is-skew", "after", "90s-before"),
)
def test_restart_needed_allows_a_minute_of_clock_skew(
    server: Path, world_started: str, due: bool
) -> None:
    """The switch's stamp is this host's clock and StartedAt is the daemon's (a VM's)."""
    fake = FakeMmapsDocker()
    clock = Clock()
    start(server, fake, clock)
    clock.now = datetime(2026, 10, 2, 15, 0, 0, tzinfo=UTC)
    fake.finish(0, tiles=MIN_FILES)
    fake.world_started_at = world_started
    assert status(server, fake, clock).restart_needed is due


# -- fix round 2 ----------------------------------------------------------------------------


def test_a_run_that_timed_out_is_removed_in_case_it_started(server: Path) -> None:
    """`docker run -d` that does not answer in time may still have made the container."""
    fake = FakeMmapsDocker()

    def slow_run(spec: docker.ContainerRun, name: str, *, timeout: float) -> str:
        fake.calls.append(f"run:{name}")
        fake.jobs[name] = FakeJob(spec, "late")
        raise docker.DockerCommandError("timed out after 120 s")

    fake.run_detached = slow_run  # type: ignore[method-assign]
    with pytest.raises(mmaps.MmapsError, match="timed out"):
        start(server, fake)
    assert fake.calls[-1] == f"remove:{NAME}" and NAME not in fake.jobs
    assert record(server)["state"] == "failed"


def test_a_route_removes_a_failed_jobs_container_if_it_is_still_there(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    raw = record(server)
    raw["state"] = "failed"
    (server / mmaps.RECORD_FILE).write_text(json.dumps(raw), "utf-8")
    assert NAME in fake.jobs, "a live container the record no longer vouches for"
    mmaps.stop_for_route(
        server, ENTRY, "the rebuild", clear=False, runner=fake, install_id=INSTALL_ID
    )
    assert NAME not in fake.jobs


def test_a_failed_jobs_container_that_cannot_be_removed_refuses_the_route(server: Path) -> None:
    fake = FakeMmapsDocker()
    (server / mmaps.RECORD_FILE).write_text(
        json.dumps({"state": "failed", "container": NAME}), "utf-8"
    )
    fake.refuse_remove = "daemon busy"
    with pytest.raises(mmaps.MmapsError, match="the rebuild was not started"):
        mmaps.stop_for_route(
            server, ENTRY, "the rebuild", clear=False, runner=fake, install_id=INSTALL_ID
        )


def test_a_poll_over_an_emptied_set_it_cannot_clear_is_a_failed_state_not_a_raise(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(0, tiles=MIN_FILES)
    status(server, fake)
    for tile in (server / "data" / "mmaps").iterdir():
        tile.unlink()

    def refuse(path: Path, keys: object) -> bool:
        raise InstallerError(f"{path} is read-only")

    monkeypatch.setattr(mmaps.conf, "set_keys", refuse)
    now = status(server, fake)
    assert now.state == "failed" and "read-only" in now.error


def test_clearing_never_makes_a_data_folder_that_is_gone(server: Path) -> None:
    import shutil

    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(0, tiles=MIN_FILES)
    status(server, fake)
    shutil.rmtree(server / "data")
    assert status(server, fake).state == "not-started"
    assert not (server / "data").exists()


def test_a_finished_set_is_counted_once_until_its_folder_changes(
    server: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Thousands of tiles on Windows: a poll must not walk them every time."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.finish(0, tiles=MIN_FILES)
    status(server, fake)
    walks: list[Path] = []
    real = extract.counts

    def counting(produces: dict[str, int], data_dir: Path) -> dict[str, int]:
        walks.append(data_dir)
        return real(produces, data_dir)

    monkeypatch.setattr(mmaps.extract, "counts", counting)
    status(server, fake)
    status(server, fake)
    assert walks == []
    gone = next((server / "data" / "mmaps").iterdir())
    gone.unlink()
    import os

    stamp = (server / "data" / "mmaps").stat().st_mtime_ns + 1_000_000_000
    os.utime(server / "data" / "mmaps", ns=(stamp, stamp))
    assert status(server, fake).state == "not-started"
    assert len(walks) == 1


def test_uninstall_removes_the_derived_job_and_never_a_container_a_record_names(
    real_seam: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    box = real_seam
    install(box)
    name = job_name(box)
    raw = record(box.server_dir)
    raw["container"] = "centurion-worldserver"
    (box.server_dir / mmaps.RECORD_FILE).write_text(json.dumps(raw), "utf-8")
    monkeypatch.setattr(purge, "catalog_entry", lambda game: ENTRY if game == ENTRY.id else None)
    rec = PurgeRecorder(box.server_dir, remove_folder=purge.remove_tree)
    box.mmaps.calls.clear()
    rec.uninstaller(game=ENTRY.id).run(keep_characters=False)
    removed = [call for call in box.mmaps.calls if call.startswith("remove:")]
    assert removed == [f"remove:{name}"]


# -- T209: a run that stops part-way keeps its finished tiles ------------------------------


def lay_tiles(server_dir: Path, tiles: dict[str, bytes]) -> None:
    out = server_dir / "data" / "mmaps"
    out.mkdir(exist_ok=True)
    for name, body in tiles.items():
        (out / name).write_bytes(body)


@pytest.mark.parametrize(
    ("name", "body", "kept"),
    [
        ("0014251.mmtile", mmtile(64), True),
        ("0014251.mmtile", mmtile(0), True),
        ("0014251.mmtile", mmtile(64, cut=20 + 32), False),
        ("0014251.mmtile", mmtile(64, cut=20), False),
        ("0014251.mmtile", mmtile(64) + b"\0", False),
        ("0014251.mmtile", mmtile(64, magic=MMAP_MAGIC ^ 1), False),
        ("0014251.mmtile", mmtile(64, cut=7), False),
        ("001.mmap", b"\x01" * 7, True),
        ("notes.txt", b"x", True),
    ],
    ids=(
        "whole",
        "whole-empty-data",
        "header-and-half-its-data",
        "header-only",
        "one-byte-too-long",
        "bad-magic",
        "seven-bytes",
        "map-file-left-alone",
        "not-a-tile-left-alone",
    ),
)
def test_a_crash_keeps_exactly_the_whole_tiles(
    server: Path, name: str, body: bytes, kept: bool
) -> None:
    """Each fixture breaks ONE rule of the pinned header (MapDefines.h:27-38): its length
    against `size`, or its magic. Driven through the failure a poll finds (`_finished`)."""
    fake = FakeMmapsDocker()
    start(server, fake)
    lay_tiles(server, {"0000000.mmtile": mmtile(16), name: body})
    fake.finish(139)
    now = status(server, fake)
    assert now.state == "failed"
    assert (name in output(server)) is kept
    assert "0000000.mmtile" in output(server), "the whole neighbour is never touched"
    if name == "0014251.mmtile":
        assert now.kept == (2 if kept else 1)


def test_the_test_tiles_are_the_pinned_structs_size() -> None:
    """`sizeof(MmapTileHeader) == 20` is the pinned source's own static_assert (MapDefines.h:41)."""
    assert TILE_HEADER.size == 20
    assert len(mmtile(64)) == 84


def test_a_crash_keeps_three_tiles_removes_the_cut_one_and_the_retry_continues_on_one_thread(
    server: Path,
) -> None:
    """The crash seen live (exit 139 at 16 %): the retry continues, on `retry_threads`."""
    fake = FakeMmapsDocker()
    start(server, fake)
    assert fake.started[-1].argv[-2:] == ("--threads", "4"), "the first run: half the cores"
    fake.write_tiles(3)
    fake.write_cut_tile()
    fake.finish(139)
    now = status(server, fake)
    assert now.state == "failed" and now.kept == 3 and not now.pathfinding_on
    saved = record(server)
    assert saved["resumable"] is True and saved["kept"] == 3 and saved["crashed"] is True
    assert output(server) == ["0000000.mmtile", "0000001.mmtile", "0000002.mmtile"]
    assert "mmap.enablePathFinding = 0" in conf_text(server)
    assert now.line().startswith("Pathfinding data stopped part-way: the generator stopped with")
    assert now.line().endswith(
        "Its 3 finished tiles are kept, and the next run continues from there."
    )
    said = start(server, fake)
    assert fake.mmaps_at_run[-1] == ["0000000.mmtile", "0000001.mmtile", "0000002.mmtile"]
    assert fake.started[-1].argv[-2:] == ("--threads", "1"), "the retry after a crash"
    assert said.startswith("Continuing the pathfinding data in the background from its 3 ")
    assert record(server)["crashed"] is True, "a Stop of the retry must not go back to 4"


def test_a_retry_after_a_crash_keeps_the_usual_threads_without_retry_threads(
    server: Path,
) -> None:
    raw = ENTRY.model_dump(mode="json")
    raw["install"]["native"]["trinitycore"]["mmaps"]["retry_threads"] = None
    entry = type(ENTRY).model_validate(raw)
    fake = FakeMmapsDocker()

    def begin() -> None:
        mmaps.start_mmaps(
            server,
            entry,
            runner=fake,
            platform_id=lambda: "linux",
            install_id=INSTALL_ID,
            user_args=(),
        )

    begin()
    fake.write_tiles(3)
    fake.finish(139)
    mmaps.mmaps_status(server, entry, runner=fake, install_id=INSTALL_ID)
    begin()
    assert fake.started[-1].argv[-2:] == ("--threads", "4")
    assert len(fake.mmaps_at_run[-1]) == 3


def test_changed_map_data_between_the_runs_clears_everything_and_says_why(
    server: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    (server / "data" / extract.EVIDENCE_FILE).write_text('{"plan_hash": "second"}\n', "utf-8")
    with caplog.at_level("WARNING"):
        start(server, fake)
    assert fake.mmaps_at_run[-1] == [], "tiles made from other map data are not continued"
    assert any(
        "map data" in rec.getMessage() and "changed" in rec.getMessage() for rec in caplog.records
    )
    assert record(server)["kept"] == 0
    assert fake.started[-1].argv[-2:] == ("--threads", "4"), "a new set: the usual threads"
    assert record(server)["crashed"] is False, "the crash belonged to the old map data"


def test_the_retry_after_a_crash_that_kept_no_tile_still_uses_one_thread(server: Path) -> None:
    """Owner, 2026-10-04: the retry after a crash runs on `retry_threads`, tiles or not."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_cut_tile()
    fake.finish(139)
    assert status(server, fake).kept == 0
    start(server, fake)
    assert fake.mmaps_at_run[-1] == []
    assert fake.started[-1].argv[-2:] == ("--threads", "1")


def test_map_data_changed_during_a_run_is_never_switched_on(server: Path) -> None:
    """Codex adversarial review: a run that ends well over map data that changed under it
    is a set made from two map datas; its tiles go and pathfinding stays off."""
    fake = FakeMmapsDocker()
    start(server, fake)
    _change_size_only(next(iter(sorted((server / "data" / "maps").iterdir()))))
    fake.finish(0, tiles=MIN_FILES)
    now = status(server, fake)
    assert now.state == "failed" and not now.pathfinding_on and now.kept == 0
    assert "map data changed while" in now.error
    assert output(server) == []
    assert "mmap.enablePathFinding = 0" in conf_text(server)


def _change_size_only(path: Path) -> None:
    stamp = path.stat().st_mtime_ns
    path.write_bytes(path.read_bytes() + b"!")
    os.utime(path, ns=(stamp, stamp))


def _change_mtime_only(path: Path) -> None:
    stamp = path.stat().st_mtime_ns + 5_000_000_000
    os.utime(path, ns=(stamp, stamp))


def _add_a_file(path: Path) -> None:
    (path.parent / "999.vmtree").write_bytes(b"VMAP_4.8")


@pytest.mark.parametrize(
    ("folder", "change"),
    [
        ("maps", _change_size_only),
        ("dbc", _change_mtime_only),
        ("vmaps", _add_a_file),
    ],
    ids=("a-map-file-of-another-size", "a-dbc-file-rewritten", "a-vmap-file-added"),
)
def test_map_data_changed_behind_an_unchanged_evidence_file_clears_the_tiles(
    server: Path, folder: str, change: Callable[[Path], None]
) -> None:
    """Codex adversarial review: the evidence file alone does not prove the maps are the same.
    Each fixture changes ONE fact of ONE file the generator reads; the evidence file stays."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    change(next(iter(sorted((server / "data" / folder).iterdir()))))
    start(server, fake)
    assert fake.mmaps_at_run[-1] == []


def test_a_run_whose_map_data_had_no_evidence_is_never_continued(server: Path) -> None:
    """Nothing to compare with is not "unchanged"."""
    (server / "data" / extract.EVIDENCE_FILE).unlink()
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    start(server, fake)
    assert fake.mmaps_at_run[-1] == []


def test_a_cut_tile_laid_while_the_run_was_failed_is_removed_before_the_resume(
    server: Path,
) -> None:
    """The start checks the tiles again: a file can change between the failure and the start."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    lay_tiles(server, {"0000001.mmtile": mmtile(16 + 1, cut=30)})
    start(server, fake)
    assert fake.mmaps_at_run[-1] == ["0000000.mmtile", "0000002.mmtile"]
    assert record(server)["kept"] == 2


def test_a_record_that_cannot_be_read_clears_the_tiles_on_start(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    (server / mmaps.RECORD_FILE).write_text("{torn", "utf-8")
    start(server, fake)
    assert fake.mmaps_at_run[-1] == []


def test_a_rebuild_route_keeps_the_tiles_of_a_failed_run(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    said = mmaps.stop_for_route(
        server, ENTRY, "the rebuild", clear=False, runner=fake, install_id=INSTALL_ID
    )
    assert said is None
    assert len(output(server)) == 3 and record(server)["resumable"] is True


def test_an_update_route_clears_the_tiles_of_a_failed_run_and_forgets_it(server: Path) -> None:
    """The generator's code may change while `MMAP_VERSION` does not (owner, 2026-10-04)."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    said = mmaps.stop_for_route(
        server,
        ENTRY,
        "the update to the newest code",
        clear=True,
        runner=fake,
        install_id=INSTALL_ID,
    )
    assert output(server) == []
    assert not (server / mmaps.RECORD_FILE).exists()
    assert said == (
        "The 3 pathfinding tiles kept from an earlier run were removed before the update to "
        "the newest code, which can change how they are made. It starts again from the "
        "beginning once the server has been rebuilt, or from the Server tab."
    )
    start(server, fake)
    assert fake.mmaps_at_run[-1] == []
    assert fake.started[-1].argv[-2:] == ("--threads", "4"), "a new start: the usual threads"


def test_an_update_of_a_failed_run_after_a_real_crash_clears_the_tiles(box: Machine) -> None:
    """Through the engine's update route, after the run crashed (T209): nothing is continued."""
    install(box)
    box.mmaps.write_tiles(30)
    box.mmaps.finish(139)
    eng = engine(box)
    assert eng.mmaps_status(box.server_dir).kept == 30
    said = list(
        eng.before_rebuild(
            box.server_dir, "the update to the newest code", "Update the server to latest…"
        )
    )
    assert output(box.server_dir) == [] and said
    rebuilt = list(eng.before_rebuild(box.server_dir, "the rebuild"))
    assert rebuilt == []


def test_a_rebuild_through_the_engine_keeps_the_tiles_of_a_failed_run(box: Machine) -> None:
    install(box)
    box.mmaps.write_tiles(30)
    box.mmaps.finish(139)
    eng = engine(box)
    assert eng.mmaps_status(box.server_dir).kept == 30
    list(eng.rebuild(InstallOptions(server_dir=box.server_dir)))
    assert len(box.mmaps.mmaps_at_run[-1]) == 30
    assert box.mmaps.started[-1].argv[-2:] == ("--threads", "1"), "still the crash's retry"


def test_reextract_discards_the_kept_tiles(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    mmaps.discard(server, ENTRY, install_id=INSTALL_ID)
    assert output(server) == [] and not (server / mmaps.RECORD_FILE).exists()


def test_a_resumed_run_is_done_only_with_the_success_code_and_min_files(server: Path) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(300)
    fake.finish(139)
    status(server, fake)
    start(server, fake)
    fake.finish(0, tiles=0)
    short = status(server, fake)
    assert short.state == "failed" and "300 files where at least 500" in short.error
    start(server, fake)
    fake.write_tiles(MIN_FILES - 300, first=300)
    fake.finish(0)
    done = status(server, fake)
    assert done.state == "done" and done.kept == 0 and done.pathfinding_on


def test_one_warning_per_failure_however_often_it_is_polled(
    server: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """F15: the live "logged twice" was not in yulon.log (one line at 03:07:00); pinned here."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    with caplog.at_level("WARNING"):
        for _ in range(3):
            status(server, fake)
        mmaps.stop_for_route(
            server, ENTRY, "the rebuild", clear=False, runner=fake, install_id=INSTALL_ID
        )
    failed = [rec for rec in caplog.records if " failed: " in rec.getMessage()]
    assert len(failed) == 1


def test_an_update_route_clears_the_tiles_of_a_run_that_crashed_since_the_last_poll(
    server: Path,
) -> None:
    """Codex review: the route's own reconcile turns the run `failed` and keeps its tiles;
    an update must still throw them away (the record still said `running`)."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    assert record(server)["state"] == "running", "nobody polled since it crashed"
    said = mmaps.stop_for_route(
        server,
        ENTRY,
        "the update to the newest code",
        clear=True,
        runner=fake,
        install_id=INSTALL_ID,
    )
    assert output(server) == []
    assert not (server / mmaps.RECORD_FILE).exists()
    assert said is not None and said.startswith("The 3 pathfinding tiles kept from an earlier run")


def test_a_rebuild_route_keeps_the_tiles_of_a_run_that_crashed_since_the_last_poll(
    server: Path,
) -> None:
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    said = mmaps.stop_for_route(
        server, ENTRY, "the rebuild", clear=False, runner=fake, install_id=INSTALL_ID
    )
    assert said is None
    assert len(output(server)) == 3 and record(server)["resumable"] is True


def test_a_record_written_before_t209_is_never_continued(server: Path) -> None:
    """No `resumable` and no `evidence` in it: its tiles are not known to be whole or current."""
    lay_tiles(server, {"0000000.mmtile": mmtile(), "0000001.mmtile": mmtile()})
    (server / mmaps.RECORD_FILE).write_text(
        json.dumps({"version": 1, "state": "failed", "container": NAME}), "utf-8"
    )
    fake = FakeMmapsDocker()
    start(server, fake)
    assert fake.mmaps_at_run[-1] == []


def test_an_update_route_forgets_a_crashed_run_that_kept_no_tile(server: Path) -> None:
    """Codex review: a crash before any whole tile still marks the set `crashed`; an update
    starts a new set, which runs on the usual threads."""
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_cut_tile()
    fake.finish(139)
    assert status(server, fake).state == "failed"
    said = mmaps.stop_for_route(
        server,
        ENTRY,
        "the update to the newest code",
        clear=True,
        runner=fake,
        install_id=INSTALL_ID,
    )
    assert said is None, "nothing kept, so nothing to say"
    assert not (server / mmaps.RECORD_FILE).exists()
    start(server, fake)
    assert fake.started[-1].argv[-2:] == ("--threads", "4")


@pytest.mark.parametrize("linked", ["file", "folder"])
def test_map_data_reached_through_a_link_is_never_continued(server: Path, linked: str) -> None:
    """Codex adversarial review: a link's own size and time say nothing about what it points
    at, which the generator reads. Unchanged otherwise, the run still starts from 0."""
    elsewhere = server.parent / "elsewhere"
    elsewhere.mkdir()
    if linked == "file":
        (elsewhere / "0004331.map").write_bytes(b"MAPS")
        (server / "data" / "maps" / "0004331.map").symlink_to(elsewhere / "0004331.map")
    else:
        (server / "data" / "vmaps" / "extra").symlink_to(elsewhere, target_is_directory=True)
    fake = FakeMmapsDocker()
    start(server, fake)
    fake.write_tiles(3)
    fake.finish(139)
    status(server, fake)
    start(server, fake)
    assert fake.mmaps_at_run[-1] == []
