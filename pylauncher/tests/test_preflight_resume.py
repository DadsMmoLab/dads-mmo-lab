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
  rule): the build's share of the floor is not asked for again, and what is
  left is judged against the server-folder pair -- and REFUSED below it, even
  when every stage is recorded done. The record is a hint: stages re-check
  their own evidence and can write maps, mmaps or client data again (fix wave,
  Codex review, 2026-09-24).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from tests.support_native import ENTRY, TBC, Recorder
from yulon import docker, platform, resources
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
            game_id=installer.entry.id,
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


def test_a_finished_install_on_a_drive_below_the_server_folder_floor_is_still_refused(
    tmp_path: Path,
) -> None:
    """Every stage recorded is a hint, not room: a stage that finds its output gone writes it again.

    The first cut of T112 turned this into a warning at any free space, 0 GB
    included (Codex review, 2026-09-24). Only the build's share is spent; the
    server-folder floor still refuses.
    """
    rec = Recorder(images=True)
    installer = _engine(rec, 5)
    server_dir = tmp_path / "tbc-server"
    _lay_record(installer, server_dir, _recorded(installer))

    with pytest.raises(InstallerError) as caught:
        _preflight(installer, server_dir)
    said = str(caught.value)
    assert "5 GB free" in said, said
    assert f"needs {NATIVE.min_server_dir_gb:.0f} GB" in said, said


def test_preflight_asks_the_daemon_about_exactly_the_refs_the_build_stage_asks_about(
    tmp_path: Path,
) -> None:
    """One spelling of this install's images: a second could lower the floor on the wrong tag."""
    rec = Recorder(images=True)
    installer = _engine(rec, 21)
    server_dir = tmp_path / "tbc-server"
    _lay_record(installer, server_dir, _recorded(installer))

    _preflight(installer, server_dir)

    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(TBC.id, installer._install_id(server_dir)),
        cancel=None,
        secrets=native.Secrets("unused"),
    )
    assert rec.images_asked == [installer.built_image_refs(ctx)]
    assert rec.images_asked[0], "an empty ref tuple would be a question nobody can answer"


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


def test_a_spent_build_on_macos_still_refuses_below_the_server_folder_floor() -> None:
    """macOS's bounded Docker row refuses on its own; a spent build lowers it, never lifts it."""
    spent = preflight.Spent(build=True)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(2, 200, "macos"), spent)
    row = _row(report, "free space on Docker's disk")
    assert row.verdict == "refuse", row
    assert f"needs {WOTLK.min_server_dir_gb:.0f} GB" in row.detail, row.detail


def test_an_unmeasured_drive_stays_unchecked_whatever_was_spent() -> None:
    """The tri-state discipline: a reading that was not taken is not rounded to a pass."""
    facts = replace(_two_drives(0, 0), data_root_free=None)
    spent = preflight.Spent(build=True)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), facts, spent)
    assert _row(report, "free space on Docker's disk").verdict == "unchecked"


# -- T203: a resumed build is not asked for the space its own cache already takes --
#
# Found by the T179 live check on a Windows test VM (2026-10-03, phase A7): a
# Centurion install whose compile had finished failed after it, and pressing
# Install again was refused, "free space on Docker's disk: 30 GB free, and the
# install needs 40 GB" -- with 12.91 GB of build cache from that compile on the
# same disk, which the resumed build reuses instead of writing again. The way
# through was pruning the cache and compiling again (~35 minutes).
#
# The rule: when the press will run the build again (every stage before it is
# recorded, and the build is not spent), the build cache Docker GAINED since this
# install's build last started -- `stage_build` records the figure it starts on
# -- counts toward the build's share of the floor, and the floor never drops
# below what a FINISHED build is asked for. Only the growth, because Yu'lon never
# prunes and the whole machine's cache may be other servers' (review, 2026-10-04).

A7_CACHE = 12_910_000_000
"""`docker system df` on the VM after the failed press: Build Cache 8 / 12.91GB (decimal)."""


def test_the_a7_refusal_passes_once_the_reused_cache_is_counted() -> None:
    """WotLK's Docker-disk pair is Centurion's (40/60): 30 GB free, 12.91 GB of it cache."""
    spent = preflight.Spent(build_cache_bytes=A7_CACHE)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(30, 200), spent)
    row = _row(report, "free space on Docker's disk")
    assert row.verdict == "warn", row
    # In this module's GB (GiB, as every free-space figure here): 12.91e9 bytes is 12.0.
    assert "build cache" in row.detail and f"{A7_CACHE / GIB:.1f} GB" in row.detail, row.detail
    assert "Docker counts it as 12.91 GB" in row.detail, "Docker's own figure, to compare"


def test_without_a_cache_the_a7_numbers_are_still_refused() -> None:
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(30, 200), preflight.Spent())
    assert _row(report, "free space on Docker's disk").verdict == "refuse"


def test_a_huge_cache_never_lowers_the_floor_below_a_finished_build_s() -> None:
    spent = preflight.Spent(build_cache_bytes=500 * GIB)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(3, 200), spent)
    row = _row(report, "free space on Docker's disk")
    assert row.verdict == "refuse", row
    assert f"needs {WOTLK.min_server_dir_gb:.0f} GB" in row.detail, row.detail


def test_the_cache_leaves_the_server_folder_s_own_row_alone_on_two_drives() -> None:
    spent = preflight.Spent(build_cache_bytes=A7_CACHE)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(200, 5), spent)
    row = _row(report, "free space on the server folder")
    assert row.verdict == "refuse", row
    assert "build cache" not in row.detail


def test_the_cache_counts_against_the_added_floor_on_one_drive() -> None:
    """TBC on one drive asks 40 (20 + 20); 16 GB of cache brings that to 24, never below 20."""
    facts = replace(_one_drive(0), data_root_free=int(25 * GIB), server_dir_free=int(25 * GIB))
    fresh = preflight.evaluate(TBC, Path("/srv/tbc"), facts)
    cached = preflight.evaluate(
        TBC, Path("/srv/tbc"), facts, preflight.Spent(build_cache_bytes=16 * GIB)
    )
    assert not fresh.ok()
    assert cached.ok(), cached.message()
    floor = preflight.evaluate(
        TBC,
        Path("/srv/tbc"),
        replace(facts, data_root_free=int(19 * GIB), server_dir_free=int(19 * GIB)),
        preflight.Spent(build_cache_bytes=500 * GIB),
    )
    assert f"needs {NATIVE.min_server_dir_gb:.0f} GB" in floor.message(), floor.message()


def test_a_spent_build_ignores_the_cache() -> None:
    """Already judged against the server-folder pair; the cache cannot lower it further."""
    spent = preflight.Spent(build=True, build_cache_bytes=500 * GIB)
    report = preflight.evaluate(ENTRY, Path("/srv/wow"), _two_drives(3, 200), spent)
    row = _row(report, "free space on Docker's disk")
    assert f"needs {WOTLK.min_server_dir_gb:.0f} GB" in row.detail, row.detail
    assert "build cache" not in row.detail


def _lay_baseline(server_dir: Path, cache: int) -> None:
    """What `stage_build` records as it starts: Docker's build cache before this build ran."""
    native.write_build_cache_baseline(server_dir, cache)


def test_growth_since_this_install_s_build_started_lowers_the_floor(tmp_path: Path) -> None:
    """The engine: every stage before `build` recorded, 16 GB of cache grown since it started."""
    rec = Recorder(images=False, build_cache=17 * GIB)
    installer = _engine(rec, 25)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])
    _lay_baseline(server_dir, 1 * GIB)

    rows = _space_rows(_preflight(installer, server_dir))
    assert len(rows) == 1 and "[refuse]" not in rows[0], rows
    assert "16.0 GB of build cache" in rows[0], rows[0]
    assert rec.build_cache_asked == 1


def test_a_big_cache_that_was_there_before_this_build_does_not_lower_the_floor(
    tmp_path: Path,
) -> None:
    """Another server's cache is not this build's: 400 GB before, 400 GB now, nothing credited."""
    rec = Recorder(images=False, build_cache=400 * GIB)
    installer = _engine(rec, 25)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])
    _lay_baseline(server_dir, 400 * GIB)

    with pytest.raises(InstallerError, match="25 GB free, and the install needs 40 GB"):
        _preflight(installer, server_dir)


def test_a_cache_pruned_below_the_baseline_credits_nothing(tmp_path: Path) -> None:
    rec = Recorder(images=False, build_cache=2 * GIB)
    installer = _engine(rec, 25)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])
    _lay_baseline(server_dir, 10 * GIB)

    with pytest.raises(InstallerError, match="25 GB free, and the install needs 40 GB"):
        _preflight(installer, server_dir)


@pytest.mark.parametrize(
    "sidecar",
    [None, "", "not json", '{"baseline_bytes": "lots"}', '{"baseline_bytes": -5}', "[]", "null"],
    ids=["missing", "empty", "garbled", "string", "negative", "list", "null"],
)
def test_a_missing_or_garbled_baseline_credits_nothing(sidecar: str | None, tmp_path: Path) -> None:
    rec = Recorder(images=False, build_cache=400 * GIB)
    installer = _engine(rec, 25)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])
    if sidecar is not None:
        (server_dir / native.BUILD_CACHE_FILE).write_text(sidecar, encoding="utf-8")

    with pytest.raises(InstallerError, match="25 GB free, and the install needs 40 GB"):
        _preflight(installer, server_dir)


def test_the_build_stage_records_the_baseline_as_it_starts(tmp_path: Path) -> None:
    """A7 end to end: the build starts on 1.75 GB of cache and fails at 12.91; the next passes."""
    rec = Recorder(images=False, build_cache=1_754_000_000)
    rec.build_result = docker.AttachedRun(
        1,
        (
            "failed to receive status: rpc error: code = "
            "Unavailable desc = error reading from server: EOF",
        ),
    )
    installer = _engine(rec, 30)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(TBC.id, installer._install_id(server_dir)),
        cancel=None,
        secrets=native.Secrets("unused"),
    )
    with pytest.raises(InstallerError, match="lost its connection"):
        list(installer.stage_build(ctx))
    assert native.read_build_cache_baseline(server_dir) == 1_754_000_000

    rec.build_cache = A7_CACHE
    rows = _space_rows(_preflight(installer, server_dir))
    assert len(rows) == 1 and "[refuse]" not in rows[0], rows
    assert "Docker counts it as 11.16 GB" in rows[0], rows[0]


def test_a_build_that_could_not_measure_the_cache_leaves_a_baseline_that_credits_nothing(
    tmp_path: Path,
) -> None:
    """An older baseline must not survive a build that started on an unknown cache."""
    rec = Recorder(images=False, build_cache=None)
    installer = _engine(rec, 25)
    server_dir = tmp_path / "tbc-server"
    server_dir.mkdir()
    _lay_baseline(server_dir, 0)
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(TBC.id, installer._install_id(server_dir)),
        cancel=None,
        secrets=native.Secrets("unused"),
    )
    list(installer.stage_build(ctx))
    assert native.read_build_cache_baseline(server_dir) is None


@pytest.mark.parametrize("cache", [None, 0], ids=["could-not-ask", "no-cache"])
def test_a_resume_at_the_build_with_no_cache_to_count_is_asked_for_the_whole_floor(
    cache: int | None, tmp_path: Path
) -> None:
    rec = Recorder(images=False, build_cache=cache)
    installer = _engine(rec, 25)
    server_dir = tmp_path / "tbc-server"
    recorded = _recorded(installer)
    _lay_record(installer, server_dir, recorded[: recorded.index("build")])
    _lay_baseline(server_dir, 0)

    with pytest.raises(InstallerError, match="25 GB free, and the install needs 40 GB"):
        _preflight(installer, server_dir)


def test_an_earlier_resume_and_a_fresh_install_do_not_ask_about_the_cache(tmp_path: Path) -> None:
    """Another install's cache must not lower the floor of a build this folder never reached."""
    rec = Recorder(images=False, build_cache=500 * GIB)
    installer = _engine(rec, 25)
    early = tmp_path / "early"
    recorded = _recorded(installer)
    _lay_record(installer, early, recorded[: recorded.index("build") - 1])
    _lay_baseline(early, 0)

    with pytest.raises(InstallerError, match="needs 40 GB"):
        _preflight(installer, early)
    with pytest.raises(InstallerError, match="needs 40 GB"):
        _preflight(installer, tmp_path / "fresh")
    assert rec.build_cache_asked == 0
