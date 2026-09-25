"""T114: T112's resume-aware free-space rule, on the AzerothCore (WotLK) engine.

Found by the T101 lane (yulon-ubuntu, 2026-09-24): a Repair of a finished,
built WotLK install through `python -m yulon.install_wiring` was refused before
stage 1 with "28 GB free, and the install needs 48 GB" -- WotLK's data-root
floor (40, the build's share) plus its server-folder floor (8) on one drive.
That tree did not carry T112. T112's rule lives on `StagedInstaller`
(`_preflight_lines()` and `_spent()`), which `AzerothCoreInstaller` inherits,
but every engine-level test T112 wrote drove `CmangosInstaller`, so nothing
held the WotLK path to it. These do, with T112's own helpers and WotLK's
numbers, whose two floors differ (40 and 8) where TBC's are equal.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY, Recorder, engine
from tests.test_preflight_resume import (
    _lay_record,
    _one_drive,
    _preflight,
    _recorded,
    _space_rows,
)
from yulon.catalog import native, preflight
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError

WOTLK = ENTRY.install.native
assert WOTLK is not None
FRESH_FLOOR = WOTLK.min_data_root_gb + WOTLK.min_server_dir_gb
REFUSED_AT_28 = f"28 GB free, and the install needs {FRESH_FLOOR:.0f} GB"


def _wotlk(rec: Recorder, free_gb: float) -> AzerothCoreInstaller:
    return engine(
        rec,
        platform_id=lambda: "linux",
        gather=lambda entry, server_dir, **_kwargs: _one_drive(free_gb),
    )


def test_the_wotlk_floors_are_the_ones_the_t101_refusal_named() -> None:
    """48 GB is 40 (the build's share) + 8 (the folder's); the catalog moves them."""
    assert (WOTLK.min_data_root_gb, WOTLK.min_server_dir_gb) == (40, 8)


@pytest.mark.parametrize("free_gb", [28, 17], ids=["t101-yulon-ubuntu", "m910q-t92"])
def test_a_repair_of_a_finished_built_wotlk_install_is_not_asked_for_its_build_again(
    free_gb: int, tmp_path: Path
) -> None:
    """T101's 28 GB, and m910q's 17 GB on 2026-09-25: every stage recorded, every image present."""
    rec = Recorder(images=True)
    installer = _wotlk(rec, free_gb)
    server_dir = tmp_path / "wow"
    _lay_record(installer, server_dir, _recorded(installer))

    rows = _space_rows(_preflight(installer, server_dir))

    assert len(rows) == 1, rows
    assert "[refuse]" not in rows[0], rows[0]
    assert f"{free_gb} GB free" in rows[0], "the number is still said"


def test_a_spent_wotlk_build_between_the_folder_floors_warns_and_says_why(
    tmp_path: Path,
) -> None:
    """10 GB: above the folder's 8, below its 15. The smaller number explains itself."""
    rec = Recorder(images=True)
    installer = _wotlk(rec, 10)
    server_dir = tmp_path / "wow"
    _lay_record(installer, server_dir, _recorded(installer))

    (row,) = _space_rows(_preflight(installer, server_dir))

    assert row.startswith("[warn]"), row
    assert f"{WOTLK.warn_server_dir_gb:.0f} GB" in row, row
    assert preflight.BUILD_SPENT_NOTE.strip() in row, row


def test_a_finished_wotlk_install_below_the_folder_floor_is_still_refused(tmp_path: Path) -> None:
    """Client data runs every press and re-fetches a missing volume, so the record buys no room."""
    rec = Recorder(images=True)
    installer = _wotlk(rec, 5)
    server_dir = tmp_path / "wow"
    _lay_record(installer, server_dir, _recorded(installer))

    with pytest.raises(InstallerError) as caught:
        _preflight(installer, server_dir)
    said = str(caught.value)
    assert "5 GB free" in said, said
    assert f"needs {WOTLK.min_server_dir_gb:.0f} GB" in said, said


@pytest.mark.parametrize("images", [False, None], ids=["images-gone", "daemon-would-not-say"])
def test_a_recorded_wotlk_build_without_its_images_is_asked_for_the_whole_floor(
    images: bool | None, tmp_path: Path
) -> None:
    rec = Recorder(images=images)
    installer = _wotlk(rec, 28)
    server_dir = tmp_path / "wow"
    _lay_record(installer, server_dir, _recorded(installer))

    with pytest.raises(InstallerError, match=REFUSED_AT_28):
        _preflight(installer, server_dir)


def test_a_wotlk_resume_before_the_build_is_asked_for_the_whole_floor(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    installer = _wotlk(rec, 28)
    server_dir = tmp_path / "wow"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])

    with pytest.raises(InstallerError, match=REFUSED_AT_28):
        _preflight(installer, server_dir)
    assert rec.images_asked == [], "no recorded build, so the daemon is not asked"


def test_a_fresh_wotlk_install_is_still_refused_and_asks_the_daemon_nothing(tmp_path: Path) -> None:
    rec = Recorder(images=True)
    installer = _wotlk(rec, 28)

    with pytest.raises(InstallerError, match=REFUSED_AT_28):
        _preflight(installer, tmp_path / "wow")
    assert rec.images_asked == []


def test_wotlk_preflight_asks_about_every_image_the_build_stage_asks_about(
    tmp_path: Path,
) -> None:
    """All four AzerothCore images, through the one spelling (`image_refs_at()`).

    `client-data` and `db-import` are one-shots, not the running server, and a
    ref list that left them out would lower the floor on a build the build
    stage would run again.
    """
    rec = Recorder(images=True)
    installer = _wotlk(rec, 28)
    server_dir = tmp_path / "wow"
    _lay_record(installer, server_dir, _recorded(installer))

    _preflight(installer, server_dir)

    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(ENTRY.id, installer._install_id(server_dir)),
        cancel=None,
        secrets=native.Secrets("unused"),
    )
    assert rec.images_asked == [installer.built_image_refs(ctx)]
    names = [ref.split(":")[0].removeprefix(WOTLK.image_prefix) for ref in rec.images_asked[0]]
    assert names == list(WOTLK.images)
    assert set(names) == {"worldserver", "authserver", "db-import", "client-data"}
