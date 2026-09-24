"""T112: the free-space preflight asks a resume for what is LEFT, not for a fresh install's floor.

Found by the T95 gate on m910q (2026-09-24): installing WoW TBC into the folder
that already held a finished, built TBC install was refused after one second
with "21 GB free, and the install needs 40 GB". `_preflight_lines()` judged the
disk against the fresh-install floor before `run()` ever read
`.yulon-install.json`, so a press that would have skipped the clone and the
multi-hour build, and needed almost no new space, was refused like a first one.

The rule under test, at both levels (`preflight.evaluate()` is pure; the
engine is what decides what has been spent):

- nothing spent (no record, or a recorded build whose images the daemon does
  not hold): the fresh-install floor, unchanged;
- the build spent (recorded AND every image present, `stage_build`'s own skip
  rule): the build's share of the floor is not asked for again;
- every recorded stage done as well: a shortfall is a warning that still names
  the number, never a refusal, and never silence.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.support_native import ENTRY, TBC, Recorder
from yulon import platform, resources
from yulon.catalog import native, preflight
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

GIB = preflight.GIB
NATIVE = TBC.install.native
assert NATIVE is not None
WOTLK = ENTRY.install.native
assert WOTLK is not None


def _one_drive(free_gb: float) -> preflight.Facts:
    """m910q's shape: Docker's data root and the server folder on one Linux volume."""
    free = int(free_gb * GIB)
    return preflight.Facts(
        platform_id="linux",
        docker_ready=True,
        compose_ready=True,
        vm=platform.VmResources(16 * GIB, 4),
        data_root=Path("/var/lib/docker"),
        data_root_free=free,
        server_dir_free=free,
        same_volume=True,
        bind_mount=True,
    )


def _engine(rec: Recorder, free_gb: float) -> CmangosInstaller:
    return CmangosInstaller(
        TBC,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(
            platform_id=lambda: "linux",
            gather=lambda entry, server_dir, **_kwargs: _one_drive(free_gb),
        ),
    )


def _recorded(installer: native.StagedInstaller) -> tuple[str, ...]:
    return tuple(stage.name for stage in installer.stages() if stage.recorded)


def _lay_record(
    installer: native.StagedInstaller, server_dir: Path, completed: tuple[str, ...]
) -> None:
    server_dir.mkdir(parents=True, exist_ok=True)
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=TBC.id,
            install_id=installer._install_id(server_dir),
            family=installer.family,
            completed=completed,
        ),
    )


def _space_rows(lines: list[str]) -> list[str]:
    return [line for line in lines if "free space on Docker's disk" in line]


def _preflight(installer: native.StagedInstaller, server_dir: Path) -> list[str]:
    return list(installer._preflight_lines(InstallOptions(server_dir=server_dir), None))


# -- the engine: what the T95 gate pressed ------------------------------------


def test_a_reinstall_into_a_finished_built_install_is_not_refused_for_the_build_it_already_did(
    tmp_path: Path,
) -> None:
    """The T95 gate's own numbers: 21 GB free on one drive, every stage recorded, images present."""
    rec = Recorder(images=True)
    installer = _engine(rec, 21)
    server_dir = tmp_path / "tbc-server"
    _lay_record(installer, server_dir, _recorded(installer))

    lines = _preflight(installer, server_dir)

    rows = _space_rows(lines)
    assert len(rows) == 1, lines
    assert "[refuse]" not in rows[0], rows[0]
    assert "21 GB free" in rows[0], "the number is still said"


def test_a_finished_install_on_a_nearly_full_drive_warns_with_the_number_rather_than_refusing(
    tmp_path: Path,
) -> None:
    """Nothing large is left to write, so a short drive is said, not refused and not hidden."""
    rec = Recorder(images=True)
    installer = _engine(rec, 5)
    server_dir = tmp_path / "tbc-server"
    _lay_record(installer, server_dir, _recorded(installer))

    lines = _preflight(installer, server_dir)

    rows = _space_rows(lines)
    assert len(rows) == 1, lines
    assert rows[0].startswith("[warn]"), rows[0]
    assert "5 GB free" in rows[0], rows[0]
    assert f"{NATIVE.min_server_dir_gb:.0f} GB" in rows[0], "the floor it fell short of is named"


def test_a_half_done_resume_past_the_build_is_asked_only_for_what_the_remaining_stages_need(
    tmp_path: Path,
) -> None:
    """Built, not yet extracted or imported: the build's share is spent, the rest is not."""
    rec = Recorder(images=True)
    installer = _engine(rec, 21)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    through_build = recorded[: recorded.index("build") + 1]
    assert len(through_build) < len(recorded), "the arrangement must leave stages to run"
    _lay_record(installer, server_dir, through_build)

    rows = _space_rows(_preflight(installer, server_dir))
    assert len(rows) == 1 and "[refuse]" not in rows[0], rows


def test_a_half_done_resume_past_the_build_on_a_drive_too_small_for_the_rest_is_still_refused(
    tmp_path: Path,
) -> None:
    rec = Recorder(images=True)
    installer = _engine(rec, 10)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build") + 1])

    with pytest.raises(InstallerError) as caught:
        _preflight(installer, server_dir)
    said = str(caught.value)
    assert "10 GB free" in said, said
    assert f"needs {NATIVE.min_server_dir_gb:.0f} GB" in said, said


@pytest.mark.parametrize("images", [False, None], ids=["images-gone", "daemon-would-not-say"])
def test_a_recorded_build_whose_images_are_not_there_is_asked_for_the_whole_floor(
    images: bool | None, tmp_path: Path
) -> None:
    """The record alone never skips the build, so it never lowers the floor either."""
    rec = Recorder(images=images)
    installer = _engine(rec, 21)
    server_dir = tmp_path / "tbc-server"
    _lay_record(installer, server_dir, _recorded(installer))

    with pytest.raises(InstallerError, match="21 GB free, and the install needs 40 GB"):
        _preflight(installer, server_dir)


def test_a_resume_before_the_build_is_asked_for_the_whole_floor(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    installer = _engine(rec, 21)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])

    with pytest.raises(InstallerError, match="21 GB free, and the install needs 40 GB"):
        _preflight(installer, server_dir)


def test_a_fresh_install_is_still_refused_on_the_same_drive(tmp_path: Path) -> None:
    """The images existing proves nothing without this folder's record of having built them."""
    rec = Recorder(images=True)
    installer = _engine(rec, 21)

    with pytest.raises(InstallerError, match="21 GB free, and the install needs 40 GB"):
        _preflight(installer, tmp_path / "tbc-server")


# -- evaluate(): the rule on its own, where the two floors differ ---------------


def _two_drives(docker_gb: float, folder_gb: float, platform_id: str = "linux") -> preflight.Facts:
    return preflight.Facts(
        platform_id=platform_id,
        docker_ready=True,
        compose_ready=True,
        vm=platform.VmResources(16 * GIB, 4),
        data_root=Path("/var/lib/docker"),
        data_root_free=int(docker_gb * GIB),
        server_dir_free=int(folder_gb * GIB),
        same_volume=False,
        bind_mount=True,
    )


def _row(report: preflight.Report, name: str) -> preflight.Check:
    (row,) = [check for check in report.checks if check.name == name]
    return row


def test_nothing_spent_is_the_fresh_install_floor_on_docker_s_own_disk() -> None:
    """WotLK's pair differs (40 on Docker's disk, 8 in the folder), so the floor used is visible."""
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(30, 200))
    row = _row(report, "free space on Docker's disk")
    assert row.verdict == "refuse" and f"needs {WOTLK.min_data_root_gb:.0f} GB" in row.detail


def test_a_spent_build_is_not_asked_for_on_docker_s_own_disk_again() -> None:
    spent = preflight.Spent(build=True)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(30, 200), spent)
    assert _row(report, "free space on Docker's disk").verdict == "pass"


def test_a_spent_build_still_refuses_a_docker_disk_too_small_for_what_is_left() -> None:
    spent = preflight.Spent(build=True)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(3, 200), spent)
    row = _row(report, "free space on Docker's disk")
    assert row.verdict == "refuse", row
    assert f"needs {WOTLK.min_server_dir_gb:.0f} GB" in row.detail, row.detail
    assert preflight.BUILD_SPENT_NOTE in row.detail, "the smaller number must explain itself"


def test_the_one_drive_floors_stop_adding_once_the_build_is_spent() -> None:
    free = int((NATIVE.min_server_dir_gb + 1) * GIB)
    facts = replace(_one_drive(0), data_root_free=free, server_dir_free=free)
    fresh = preflight.evaluate(TBC, Path("/srv/tbc"), facts)
    spent = preflight.evaluate(TBC, Path("/srv/tbc"), facts, preflight.Spent(build=True))
    assert not fresh.ok()
    assert spent.ok(), spent.message()
    assert "add up" not in " ".join(check.detail for check in spent.checks)


@pytest.mark.parametrize("platform_id", ["linux", "macos"])
def test_everything_spent_turns_every_space_refusal_into_a_warning_with_its_number(
    platform_id: str,
) -> None:
    """Both drives, and macOS's bounded Docker row, which has a refusal of its own."""
    spent = preflight.Spent(build=True, everything=True)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(2, 3, platform_id), spent)
    assert report.ok(), report.message()
    docker_row = _row(report, "free space on Docker's disk")
    folder_row = _row(report, "free space on the server folder")
    for row, gb in ((docker_row, 2), (folder_row, 3)):
        assert row.verdict == "warn", row
        assert f"{gb} GB free" in row.detail and "not a refusal" in row.detail, row.detail


def test_an_unmeasured_drive_stays_unchecked_whatever_was_spent() -> None:
    """The tri-state discipline: a reading that was not taken is not rounded to a pass."""
    facts = replace(_two_drives(0, 0), data_root_free=None)
    spent = preflight.Spent(build=True, everything=True)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), facts, spent)
    assert _row(report, "free space on Docker's disk").verdict == "unchecked"
