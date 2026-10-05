"""T219: the fingerprint of the map data, and every start that writes it.

On Windows a Centurion world server reads its map data from the `world-data`
volume, and its entrypoint (`test_world_data_sync.py`) copies into the volume the
folders whose line in `data/.yulon-world-data` changed. Yu'lon writes that file
before every start it makes -- the Server tab's Start (`Controller.start()`), the
install's `up`, a rebuild's recreate, the finish of a world update and the
pathfinding job's start -- so the copy follows the server folder. Each of those
paths is DRIVEN here, on a mirrored install, and the file is checked at the moment
the start's own seam is called; a Linux install's file must never appear.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from tests import support_rendered
from tests.support_trinitycore import FakeMmapsDocker, centurion_like
from tests.test_families_trinitycore import (  # noqa: F401 - fixtures, as pytest resolves them
    ENTRY,
    OPTIONAL_PACKS,
    REQUIRED_PACKS,
    REV,
    Machine,
    context,
    engine,
    known_password,
    machine,
)
from tests.test_mmaps_background import WORLD_CONF, lay_server
from tests.test_trinitycore_updates import WORLD_SQL, Box, box, on_the_built_commit  # noqa: F401
from yulon import docker, resources
from yulon.catalog import composegen, world_data
from yulon.catalog.catalog import CatalogEntry
from yulon.catalog.families import mmaps
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller import StartRefused
from yulon.controller_wow_centurion.controller import CenturionController

DIRS = ("dbc", "maps", "vmaps", "mmaps", "Cameras")
MIRRORED = centurion_like(packs=[*REQUIRED_PACKS, *OPTIONAL_PACKS], rev=REV, world_data_dirs=DIRS)
"""The engine tests' entry, with the folders the shipped Centurion copies into its volume."""
FILE = world_data.FINGERPRINT_FILE


def lay_compose(server_dir: Path, platform_id: str, entry: CatalogEntry = MIRRORED) -> None:
    """The install's base file as `generate-compose` writes it on `platform_id`."""
    plan = composegen.render(
        entry,
        server_dir,
        templates_root=resources.installers_dir(),
        db_password="tc-0123456789abcdef",
        platform_id=lambda: platform_id,
    )
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / composegen.BASE_FILE).write_text(plan.base, encoding="utf-8")
    (server_dir / composegen.OVERRIDE_FILE).write_text(plan.override, encoding="utf-8")


def lay_data(server_dir: Path) -> Path:
    data = server_dir / "data"
    for name, body in {
        "dbc/Spell.dbc": b"spell",
        "maps/0003232.map": b"map",
        "vmaps/000.vmtree": b"tree",
        "vmaps/sub/000_32_32.vmtile": b"tile",
        "mmaps/000.mmap": b"navmesh",
        "Cameras/FlyBy.m2": b"camera",
    }.items():
        path = data / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    return data


def expected(server_dir: Path, *, mmaps_done: bool = False) -> str:
    return world_data.fingerprint(server_dir / "data", DIRS, mmaps_done)


def line_of(text: str, name: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(f"{name} "))


def say_done(server_dir: Path) -> None:
    (server_dir / mmaps.RECORD_FILE).write_text(
        json.dumps({"version": 1, "state": "done", "container": "c"}), encoding="utf-8"
    )


# -- the fingerprint -----------------------------------------------------------------------


@pytest.fixture
def data(tmp_path: Path) -> Path:
    return lay_data(tmp_path / "server")


def test_a_files_size_moves_its_folders_line_and_no_other(data: Path) -> None:
    before = world_data.fingerprint(data, DIRS, True)
    path = data / "maps" / "0003232.map"
    stamp = path.stat().st_mtime_ns
    path.write_bytes(b"map, longer")
    os.utime(path, ns=(stamp, stamp))
    after = world_data.fingerprint(data, DIRS, True)
    assert line_of(after, "maps") != line_of(before, "maps")
    for name in ("dbc", "vmaps", "mmaps", "Cameras"):
        assert line_of(after, name) == line_of(before, name)


def test_a_files_modification_time_moves_its_folders_line(data: Path) -> None:
    before = world_data.fingerprint(data, DIRS, True)
    path = data / "vmaps" / "sub" / "000_32_32.vmtile"
    os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
    assert line_of(world_data.fingerprint(data, DIRS, True), "vmaps") != line_of(before, "vmaps")


def test_a_file_added_removed_or_renamed_moves_its_folders_line(data: Path) -> None:
    before = line_of(world_data.fingerprint(data, DIRS, True), "dbc")
    added = data / "dbc" / "Item.dbc"
    added.write_bytes(b"item")
    assert line_of(world_data.fingerprint(data, DIRS, True), "dbc") != before
    added.unlink()
    assert line_of(world_data.fingerprint(data, DIRS, True), "dbc") == before
    spell = data / "dbc" / "Spell.dbc"
    stamp = spell.stat().st_mtime_ns
    renamed = spell.rename(data / "dbc" / "Spelx.dbc")
    os.utime(renamed, ns=(stamp, stamp))
    assert line_of(world_data.fingerprint(data, DIRS, True), "dbc") != before


def test_the_same_bytes_size_and_time_written_again_do_not_move_it(data: Path) -> None:
    before = world_data.fingerprint(data, DIRS, True)
    path = data / "maps" / "0003232.map"
    stamp = path.stat().st_mtime_ns
    path.write_bytes(b"map")
    os.utime(path, ns=(stamp, stamp))
    assert world_data.fingerprint(data, DIRS, True) == before


def test_the_order_the_files_were_made_in_does_not_move_it(tmp_path: Path) -> None:
    """The folder listing's order is the filesystem's; the lines are sorted."""
    first, second = tmp_path / "a" / "maps", tmp_path / "b" / "maps"
    names = [f"{index:07d}.map" for index in range(40)]
    for folder, order in ((first, names), (second, list(reversed(names)))):
        folder.mkdir(parents=True)
        for name in order:
            (folder / name).write_bytes(name.encode())
            os.utime(folder / name, ns=(1_700_000_000_000_000_000,) * 2)
    assert world_data.fingerprint(first.parent, ("maps",), True) == world_data.fingerprint(
        second.parent, ("maps",), True
    )


def test_unfinished_pathfinding_and_a_missing_folder_read_as_a_dash(data: Path) -> None:
    """`-` is what the copy reads as "the server sees this folder empty"."""
    (data / "Cameras" / "FlyBy.m2").unlink()
    (data / "Cameras").rmdir()
    text = world_data.fingerprint(data, DIRS, False)
    assert text.splitlines() == [
        line_of(text, "dbc"),
        line_of(text, "maps"),
        line_of(text, "vmaps"),
        "mmaps -",
        "Cameras -",
    ]
    assert line_of(world_data.fingerprint(data, DIRS, True), "mmaps") != "mmaps -"
    assert all(len(line.split(" ")[1]) == 64 for line in text.splitlines()[:3])


# -- is this install mirrored? ----------------------------------------------------------------


def test_mirrored_is_read_from_the_installs_compose_file(tmp_path: Path) -> None:
    """Decision 6: the file, never the platform -- a Windows install still on its bind
    file gets no fingerprint until Repair server files... rewrites it."""
    windows, linux = tmp_path / "windows", tmp_path / "linux"
    lay_compose(windows, "windows")
    lay_compose(linux, "linux")
    assert world_data.mirrored(windows) is True
    assert world_data.mirrored(linux) is False
    assert world_data.mirrored(tmp_path / "nothing-here") is False
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    (snapshot / composegen.BASE_FILE).write_text(
        (support_rendered.CENTURION_SNAPSHOT_ROOT / "linux" / composegen.BASE_FILE).read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )
    assert world_data.mirrored(snapshot) is False


# -- refresh -----------------------------------------------------------------------------------


@pytest.fixture
def windows_install(tmp_path: Path) -> Path:
    server_dir = tmp_path / "wow-centurion-server"
    lay_compose(server_dir, "windows")
    lay_data(server_dir)
    return server_dir


def test_refresh_writes_the_fingerprint_on_a_mirrored_install(windows_install: Path) -> None:
    assert world_data.refresh(MIRRORED, windows_install) is None
    written = (windows_install / "data" / FILE).read_text(encoding="utf-8")
    assert written == expected(windows_install)
    say_done(windows_install)
    assert world_data.refresh(MIRRORED, windows_install) is None
    assert (windows_install / "data" / FILE).read_text(encoding="utf-8") == expected(
        windows_install, mmaps_done=True
    ), "a finished pathfinding run is the record's to say"


def test_refresh_leaves_a_current_fingerprint_untouched(windows_install: Path) -> None:
    world_data.refresh(MIRRORED, windows_install)
    path = windows_install / "data" / FILE
    os.utime(path, ns=(1_000_000_000, 1_000_000_000))
    world_data.refresh(MIRRORED, windows_install)
    assert path.stat().st_mtime_ns == 1_000_000_000, "the same bytes were written again"


def test_refresh_writes_nothing_on_a_bind_install(tmp_path: Path) -> None:
    server_dir = tmp_path / "wow-centurion-server"
    lay_compose(server_dir, "linux")
    lay_data(server_dir)
    assert world_data.refresh(MIRRORED, server_dir) is None
    assert not (server_dir / "data" / FILE).exists()


def test_refresh_writes_nothing_for_an_entry_without_world_folders(windows_install: Path) -> None:
    assert world_data.refresh(ENTRY, windows_install) is None
    assert not (windows_install / "data" / FILE).exists()


REFUSED = "Yu'lon could not record which map data the server should use, so it does not start"


def wedge(server_dir: Path) -> Path:
    """A fingerprint that can be neither written nor removed: a folder where the file goes."""
    path = server_dir / "data" / FILE
    path.mkdir()
    (path / "something").write_text("x", encoding="utf-8")
    return path


def test_a_fingerprint_that_can_be_neither_written_nor_removed_refuses(
    windows_install: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Lead's ruling: a stale fingerprint left in place lets the world read old map data
    without anyone noticing, so that one case refuses -- naming the file and the reason."""
    path = wedge(windows_install)
    with pytest.raises(world_data.FingerprintNotRecorded) as refused:
        world_data.refresh(MIRRORED, windows_install)
    said = str(refused.value)
    assert said.startswith(REFUSED), said
    assert str(path) in said
    assert "Is a directory" in said, "the reason is the system's own"
    assert any(said in record.getMessage() for record in caplog.records)


def test_a_fingerprint_that_cannot_be_written_where_none_was_says_so_and_goes_on(
    windows_install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing stale is left, so the start goes on: the copy takes all of the map data."""

    def unreadable(*_args: object) -> str:
        raise PermissionError(13, "Permission denied", str(windows_install / "data" / "maps"))

    monkeypatch.setattr(world_data, "fingerprint", unreadable)
    said = world_data.refresh(MIRRORED, windows_install)
    assert said is not None and "copies all of the map data again" in said
    assert not (windows_install / "data" / FILE).exists()


# -- every start path writes it before it starts --------------------------------------------


class Seen:
    """What `data/.yulon-world-data` held when a start seam was called."""

    def __init__(self, server_dir: Path) -> None:
        self.server_dir = server_dir
        self.at_start: list[str | None] = []

    def look(self) -> None:
        path = self.server_dir / "data" / FILE
        self.at_start.append(path.read_text(encoding="utf-8") if path.exists() else None)


@pytest.fixture
def tc(machine: Machine) -> Machine:  # noqa: F811 - the fixture imported above
    """The TrinityCore engine tests' machine (`test_families_trinitycore.machine`)."""
    return machine


def mirrored_machine(m: Machine, platform_id: str = "windows") -> Seen:
    lay_compose(m.server_dir, platform_id)
    lay_data(m.server_dir)
    return Seen(m.server_dir)


@pytest.mark.parametrize("platform_id", ["windows", "linux"])
def test_the_server_tabs_start_writes_it_before_compose_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform_id: str
) -> None:
    server_dir = tmp_path / "wow-centurion-server"
    lay_compose(server_dir, platform_id)
    lay_data(server_dir)
    seen = Seen(server_dir)

    def start_staged(spec: docker.ContainerSpec, where: Path, **_kw: object) -> bool:
        seen.look()
        return True

    monkeypatch.setattr(docker, "start_staged", start_staged)
    controller = CenturionController(MIRRORED, server_dir)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    controller.start()
    assert seen.at_start == [expected(server_dir) if platform_id == "windows" else None]


def scan_fails(monkeypatch: pytest.MonkeyPatch, server_dir: Path) -> None:
    def unreadable(*_args: object) -> str:
        raise PermissionError(13, "Permission denied", str(server_dir / "data" / "maps"))

    monkeypatch.setattr(world_data, "fingerprint", unreadable)


def tab_controller(
    server_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[CenturionController, list[Path]]:
    started: list[Path] = []
    monkeypatch.setattr(docker, "start_staged", lambda spec, where, **_kw: started.append(where))
    controller = CenturionController(MIRRORED, server_dir)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    return controller, started


def test_a_start_whose_fingerprint_could_be_removed_still_starts_and_says_why(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = tmp_path / "wow-centurion-server"
    lay_compose(server_dir, "windows")
    lay_data(server_dir)
    world_data.refresh(MIRRORED, server_dir)
    scan_fails(monkeypatch, server_dir)
    controller, started = tab_controller(server_dir, monkeypatch)
    controller.start()
    assert started == [server_dir]
    said = controller.world_data_problem
    assert said is not None and "copies all of the map data again" in said, said


def test_the_server_tabs_start_is_refused_when_a_stale_fingerprint_must_stay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = tmp_path / "wow-centurion-server"
    lay_compose(server_dir, "windows")
    lay_data(server_dir)
    wedge(server_dir)
    controller, started = tab_controller(server_dir, monkeypatch)
    with pytest.raises(StartRefused) as refused:
        controller.start()
    assert str(refused.value).startswith(REFUSED)
    assert started == [], "compose was asked to start"


@pytest.mark.parametrize("platform_id", ["windows", "linux"])
def test_the_installs_up_writes_it_before_the_start(tc: Machine, platform_id: str) -> None:
    seen = mirrored_machine(tc, platform_id)

    def start(spec: docker.ContainerSpec, server_dir: Path) -> bool:
        seen.look()
        return True

    list(engine(tc, entry=MIRRORED, start=start).stage_up(context(tc)))
    assert seen.at_start == [expected(tc.server_dir) if platform_id == "windows" else None]


def test_the_installs_up_says_when_it_had_to_remove_the_fingerprint(
    tc: Machine, monkeypatch: pytest.MonkeyPatch
) -> None:
    mirrored_machine(tc)
    scan_fails(monkeypatch, tc.server_dir)
    said = list(engine(tc, entry=MIRRORED, start=lambda spec, where: True).stage_up(context(tc)))
    assert any("copies all of the map data again" in line for line in said), said


def test_the_installs_up_is_refused_when_a_stale_fingerprint_must_stay(tc: Machine) -> None:
    seen = mirrored_machine(tc)
    wedge(tc.server_dir)

    def start(spec: docker.ContainerSpec, server_dir: Path) -> bool:
        seen.look()
        return True

    with pytest.raises(InstallerError, match=REFUSED):
        list(engine(tc, entry=MIRRORED, start=start).stage_up(context(tc)))
    assert seen.at_start == []


def test_a_rebuilds_recreate_is_refused_when_a_stale_fingerprint_must_stay(tc: Machine) -> None:
    seen = mirrored_machine(tc)
    wedge(tc.server_dir)
    eng = engine(tc, entry=MIRRORED, docker_ready=lambda: True, recreate=recording_recreate(seen))
    with pytest.raises(InstallerError, match=REFUSED):
        list(eng.stage_recreate(context(tc)))
    assert seen.at_start == []


def test_finishing_a_world_update_is_refused_when_a_stale_fingerprint_must_stay(
    tc: Machine,
) -> None:
    seen = mirrored_machine(tc)
    wedge(tc.server_dir)
    eng = engine(tc, entry=MIRRORED, recreate=recording_recreate(seen))
    with pytest.raises(InstallerError, match=REFUSED):
        list(eng._start_after_finish(context(tc)))
    assert seen.at_start == []


def test_the_pathfinding_job_is_not_a_world_start_and_still_runs(tmp_path: Path) -> None:
    """The ruling is about starting the world; the job only reads `data/` beside it."""
    server_dir = tmp_path / "wow-centurion-server"
    lay_server(server_dir)
    lay_compose(server_dir, "windows")
    wedge(server_dir)
    fake = FakeMmapsDocker()
    mmaps.start_mmaps(
        server_dir, MIRRORED, runner=fake, platform_id=lambda: "windows", user_args=()
    )
    assert len(fake.started) == 1


def recording_recreate(seen: Seen) -> Callable[..., bool]:
    def recreate(spec: docker.ContainerSpec, server_dir: Path, **_kw: object) -> bool:
        seen.look()
        return True

    return recreate


@pytest.mark.parametrize("platform_id", ["windows", "linux"])
def test_a_rebuilds_recreate_writes_it_before_the_containers_are_replaced(
    tc: Machine, platform_id: str
) -> None:
    seen = mirrored_machine(tc, platform_id)
    eng = engine(tc, entry=MIRRORED, docker_ready=lambda: True, recreate=recording_recreate(seen))
    said = list(eng.stage_recreate(context(tc)))
    assert seen.at_start == [expected(tc.server_dir) if platform_id == "windows" else None]
    assert not any("may not be brought up to date" in line for line in said)


@pytest.mark.parametrize("platform_id", ["windows", "linux"])
def test_finishing_a_world_update_writes_it_before_the_servers_start(
    tc: Machine, platform_id: str
) -> None:
    seen = mirrored_machine(tc, platform_id)
    eng = engine(tc, entry=MIRRORED, recreate=recording_recreate(seen))
    list(eng._start_after_finish(context(tc)))
    assert seen.at_start == [expected(tc.server_dir) if platform_id == "windows" else None]


@pytest.mark.parametrize("platform_id", ["windows", "linux"])
def test_the_pathfinding_jobs_start_writes_it_before_the_job_runs(
    tmp_path: Path, platform_id: str
) -> None:
    """While the job makes `data/mmaps`, the volume's copy must stay empty: a world that
    Docker restarts meanwhile reads the last fingerprint, which must say `-`."""
    server_dir = tmp_path / "wow-centurion-server"
    lay_server(server_dir)
    assert (server_dir / "etc" / "worldserver.conf").read_text(encoding="utf-8") == WORLD_CONF
    lay_compose(server_dir, platform_id)
    seen = Seen(server_dir)

    class Spy(FakeMmapsDocker):
        def run_detached(self, spec: docker.ContainerRun, name: str, *, timeout: float) -> str:
            seen.look()
            return super().run_detached(spec, name, timeout=timeout)

    mmaps.start_mmaps(
        server_dir,
        MIRRORED,
        runner=Spy(),
        platform_id=lambda: platform_id,
        install_id="0123abcd",
        user_args=(),
    )
    if platform_id == "linux":
        assert seen.at_start == [None]
        return
    [text] = seen.at_start
    assert text is not None and "mmaps -" in text.splitlines()
    assert text == expected(server_dir)


def test_the_folder_held_back_until_pathfinding_is_done_is_the_jobs_own() -> None:
    """`world_data` cannot import the families package (it would import `native` back), so
    it names the pathfinding folder itself; this holds the two names together."""
    assert world_data.MMAPS_DIR == mmaps.MMAPS_DIR
    assert world_data.MMAPS_DIR in DIRS


# -- an install made before the volume: Repair server files... ----------------------------------


def lay_old_windows_compose(server_dir: Path) -> None:
    """What a Windows Centurion install wrote before T219: the bind, on every platform."""
    lay_compose(server_dir, "windows", entry=centurion_like(packs=[*REQUIRED_PACKS], rev=REV))
    assert not world_data.mirrored(server_dir)


def test_a_windows_install_on_the_old_bind_file_is_offered_the_repair_and_its_cost(
    tc: Machine,
) -> None:
    lay_old_windows_compose(tc.server_dir)
    check = engine(tc, entry=MIRRORED, platform_id=lambda: "windows").base_compose_check(
        InstallOptions(server_dir=tc.server_dir)
    )
    assert check.state == "stale"
    assert check.world_data_gb == 4


def test_a_repair_that_adds_nothing_about_the_volume_says_nothing_about_it(tc: Machine) -> None:
    """A Windows file that already has the volume, but differs elsewhere, and a Linux file
    that differs: neither repair adds the volume, so neither confirmation names a copy."""
    lay_compose(tc.server_dir, "windows")
    path = tc.server_dir / composegen.BASE_FILE
    path.write_text(
        path.read_text(encoding="utf-8").replace("    stop_grace_period: 5m\n", "", 1),
        encoding="utf-8",
    )
    windows = engine(tc, entry=MIRRORED, platform_id=lambda: "windows").base_compose_check(
        InstallOptions(server_dir=tc.server_dir)
    )
    assert windows.state == "stale" and windows.world_data_gb == 0
    lay_compose(tc.server_dir, "linux")
    path.write_text(
        path.read_text(encoding="utf-8").replace("    stop_grace_period: 5m\n", "", 1),
        encoding="utf-8",
    )
    linux = engine(tc, entry=MIRRORED).base_compose_check(InstallOptions(server_dir=tc.server_dir))
    assert linux.state == "stale" and linux.world_data_gb == 0


def test_finished_pathfinding_writes_the_fingerprint_that_copies_it(tmp_path: Path) -> None:
    """Codex review: the job's start wrote `mmaps -`, and the run then turned `done` with
    pathfinding switched on -- so a world Docker restarted before the next Start from Yu'lon
    would copy an empty `mmaps` under a conf that asks for it. The status that sees the run
    finish writes the fingerprint again."""
    from tests.test_mmaps_background import MIN_FILES

    server_dir = tmp_path / "wow-centurion-server"
    lay_server(server_dir)
    lay_compose(server_dir, "windows")
    fake = FakeMmapsDocker()
    mmaps.start_mmaps(
        server_dir, MIRRORED, runner=fake, platform_id=lambda: "windows", user_args=()
    )
    path = server_dir / "data" / FILE
    assert "mmaps -" in path.read_text(encoding="utf-8").splitlines()
    fake.finish(0, tiles=MIN_FILES)
    assert mmaps.mmaps_status(server_dir, MIRRORED, runner=fake).state == "done"
    lines = path.read_text(encoding="utf-8").splitlines()
    assert "mmaps -" not in lines
    assert path.read_text(encoding="utf-8") == expected(server_dir, mmaps_done=True)


def test_a_fingerprint_that_cannot_be_worked_out_removes_the_old_one(
    windows_install: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Codex adversarial review: keeping the last fingerprint after a failure lets the copy
    trust a volume the server folder no longer matches. Without one the copy takes all of
    the map data again (`test_world_data_sync`), which is slow but never stale."""
    world_data.refresh(MIRRORED, windows_install)
    path = windows_install / "data" / FILE
    assert path.exists()

    def unreadable(*_args: object) -> str:
        raise PermissionError(13, "Permission denied", str(windows_install / "data" / "maps"))

    monkeypatch.setattr(world_data, "fingerprint", unreadable)
    said = world_data.refresh(MIRRORED, windows_install)
    assert not path.exists()
    assert said is not None and "copies all of the map data again" in said


def test_a_start_with_a_current_fingerprint_has_nothing_to_say(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = tmp_path / "wow-centurion-server"
    lay_compose(server_dir, "windows")
    lay_data(server_dir)
    monkeypatch.setattr(docker, "start_staged", lambda spec, where, **_kw: True)
    controller = CenturionController(MIRRORED, server_dir)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    controller.world_data_problem = "left from an earlier start"
    controller.start()
    assert controller.world_data_problem is None


# -- the refusal comes before any stop (cold review of 2a70b82c) --------------------------------


def wedged_controller(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[CenturionController, list[str]]:
    """A Windows install whose fingerprint can be neither written nor removed, its
    controller's stop, remove and start recorded instead of reaching Docker."""
    server_dir = tmp_path / "wow-centurion-server"
    lay_compose(server_dir, "windows")
    lay_data(server_dir)
    wedge(server_dir)
    calls: list[str] = []
    controller = CenturionController(MIRRORED, server_dir)
    monkeypatch.setattr(controller, "port_conflicts", lambda: [])
    monkeypatch.setattr(controller, "stop", lambda: calls.append("stop") or True)
    monkeypatch.setattr(controller, "remove", lambda: calls.append("remove") or True)
    monkeypatch.setattr(controller, "stop_conflicting", lambda: calls.append("stop-other") or [])
    monkeypatch.setattr(
        docker, "start_staged", lambda spec, where, **_kw: calls.append("start") or True
    )
    return controller, calls


def test_refuse_start_is_where_a_stale_fingerprint_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every press that stops something on its way to a start asks this first."""
    controller, calls = wedged_controller(tmp_path, monkeypatch)
    with pytest.raises(StartRefused, match=REFUSED):
        controller.refuse_start()
    assert calls == []


def test_restart_with_a_stale_fingerprint_never_stops_the_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from yulon.ui.controller_view import ControllerView

    controller, calls = wedged_controller(tmp_path, monkeypatch)
    view = SimpleNamespace(services=SimpleNamespace(controller=controller))
    with pytest.raises(StartRefused, match=REFUSED):
        ControllerView._do_restart(view)  # type: ignore[arg-type]
    assert calls == [], "the server was stopped before the start was refused"


def test_recreate_with_a_stale_fingerprint_never_removes_the_containers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from yulon.ui.controller_view import ControllerView

    controller, calls = wedged_controller(tmp_path, monkeypatch)
    view = SimpleNamespace(services=SimpleNamespace(controller=controller))
    with pytest.raises(StartRefused, match=REFUSED):
        ControllerView._do_recreate(view)  # type: ignore[arg-type]
    assert calls == [], "the containers were removed before the start was refused"


def test_stop_the_other_server_with_a_stale_fingerprint_stops_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    controller, calls = wedged_controller(tmp_path, monkeypatch)
    with pytest.raises(StartRefused, match=REFUSED):
        controller.stop_conflicting_and_start()
    assert calls == []


def test_a_modules_restart_with_a_stale_fingerprint_never_stops_the_world(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.controller_wow_tortoise import botpool

    controller, calls = wedged_controller(tmp_path, monkeypatch)
    with pytest.raises(StartRefused, match=REFUSED):
        botpool.restart_world(controller)
    assert calls == []


def test_finishing_a_world_update_refuses_before_it_stops_the_world(
    box: Box,  # noqa: F811 - the fixture imported above
) -> None:
    on_the_built_commit(box)
    box.leave_pending([f"{WORLD_SQL}/creature.sql"])
    lay_compose(box.server_dir, "windows")
    wedge(box.server_dir)
    eng = box.engine()
    eng.entry = MIRRORED
    with pytest.raises(InstallerError, match=REFUSED):
        list(eng.finish_world_reimport(InstallOptions(server_dir=box.server_dir)))
    assert not any(
        call.startswith("stop-world") or call == "stop_servers" for call in box.m.rec.calls
    ), box.m.rec.calls
    assert box.world.running is True
    assert box.pending() is not None, "the world update still waits"
