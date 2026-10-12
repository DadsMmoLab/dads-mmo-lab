"""Back up now over a restore that did not finish (T746, audit 2026-10-12 lane B finding 2).

Start, Update and Rebuild refuse a server whose restore record is still there (T681): its
databases may be half-written. `maintenance.backup()` asked nothing, so Back up now dumped the
half-written databases and the Backups list showed the dump as "a backup you took", usable, the
newest good copy, and the automatic keep then swept older good copies against it.

Now `backup()` refuses with the T681 sentence, adapted to a backup. The one copy still taken
over such a record is the restore's own safety copy, and only by its explicit exemption
(`restore_safety_copy=True`), never because "there is no record". A safety copy of a database
the record says is part-written is labelled `half-restored`, and the shelf never counts it as a
good copy, so the automatic keep cannot delete a good backup because of it.
"""

from __future__ import annotations

import functools
from datetime import datetime
from pathlib import Path

import pytest

from tests.test_maintenance import FakeMysql, a_backup_of, a_marker, an_earlier_copy_of, running
from yulon import backup_shelf, docker
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import Game, MaintenanceError

GAME = Game("wow-wotlk", "WoW WotLK")
plan_restore = functools.partial(maintenance.plan_restore, game=GAME)
restore = functools.partial(maintenance.restore, game=GAME)
DB = "ac-database"
AT = datetime(2026, 10, 12, 9, 0, 0)


def _half_restored(server: Path, tmp_path: Path, *, safety: bool = True) -> Path:
    """The record a restore of acore_characters leaves when its load dies part-way."""
    picked = a_backup_of(tmp_path, "acore_characters", name="picked.sql")
    copies = (an_earlier_copy_of(server, "acore_characters"),) if safety else ()
    return a_marker(server, backup=picked, databases=("acore_characters",), safety=copies)


def _backups(server: Path) -> set[str]:
    return {p.name for p in maintenance.backups_dir(server).iterdir()}


def test_back_up_now_over_an_unfinished_restore_is_refused_and_dumps_nothing(
    tmp_path: Path,
) -> None:
    """The audit's proof, through the lease and the shared engine, as Back up now runs it.

    Mutation: drop the record check from `backup()` -> the dump is written, no refusal.
    """
    server = tmp_path / "srv"
    server.mkdir()
    _half_restored(server, tmp_path)
    assert maintenance.unfinished_restore_refusal(server) is not None  # what Start says
    before = _backups(server)
    mysql = FakeMysql(("acore_auth", "acore_characters", "acore_world"))

    with (
        pytest.raises(MaintenanceError) as refused,
        docker.maintenance_lease(server, "test"),
    ):
        maintenance.backup(server, mysql, game=GAME, running=running(DB), now=AT)

    assert mysql.dumped == [], "a half-written database was dumped"
    assert _backups(server) == before, "a file was added to the backups folder"
    said = str(refused.value)
    assert "acore_characters" in said
    assert "did not finish" in said
    assert "Forget that record" in said
    assert "No backup was taken." in said
    assert "not started" not in said, "the Start sentence, not the backup's"


def test_the_refusal_names_the_copy_taken_before_the_restore(tmp_path: Path) -> None:
    """The way back is named, as Start's sentence names it (T681)."""
    _half_restored(tmp_path, tmp_path)
    with pytest.raises(MaintenanceError, match="20260823_120000_pre-restore_acore_characters"):
        maintenance.backup(
            tmp_path, FakeMysql(("acore_characters",)), game=GAME, running=running(DB)
        )


def test_a_record_that_cannot_be_read_refuses_a_backup_too(tmp_path: Path) -> None:
    """Unreadable is "a restore did not finish and Yu'lon cannot say which databases"."""
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("not json", encoding="utf-8")
    mysql = FakeMysql(("acore_world",))
    with pytest.raises(MaintenanceError, match="cannot be read"):
        maintenance.backup(tmp_path, mysql, game=GAME, running=running(DB))
    assert mysql.dumped == []


def test_a_load_yu_lon_lost_track_of_refuses_with_its_own_words(tmp_path: Path) -> None:
    """A recorded load whose process is gone: "cannot tell whether it finished", adapted."""
    picked = a_backup_of(tmp_path, "acore_world", name="picked.sql")
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        maintenance.InterruptedRestore(
            marker=marker,
            backup=picked,
            databases=("acore_world",),
            safety_backup=(),
            started_at="2026-10-12T08:00:00",
            load_pid=2**22 + 12345,
            load_host=maintenance.this_host(),
        ).as_json(),
        encoding="utf-8",
    )
    record = maintenance.interrupted_restore(tmp_path)
    assert record is not None and not record.load_running
    with pytest.raises(MaintenanceError) as refused:
        maintenance.backup(tmp_path, FakeMysql(("acore_world",)), game=GAME, running=running(DB))
    said = str(refused.value)
    assert "cannot tell whether it finished" in said
    assert "No backup was taken." in said


def test_the_exemption_is_explicit_and_only_the_restores_own(tmp_path: Path) -> None:
    """`restore_safety_copy=True` is the one way past the record; nothing else is.

    Mutation: let the exemption flag be ignored (always refuse) -> this test and every
    re-restore test in test_maintenance go red.
    """
    _half_restored(tmp_path, tmp_path)
    mysql = FakeMysql(("acore_characters",))
    report = maintenance.backup(
        tmp_path,
        mysql,
        game=GAME,
        only=("acore_characters",),
        label="pre-restore",
        running=running(DB),
        now=AT,
        restore_safety_copy=True,
    )
    assert mysql.dumped == ["acore_characters"]
    assert report.dumps[0].path.is_file()


def test_with_no_record_back_up_now_works_as_before(tmp_path: Path) -> None:
    """The guard is the record, not anything else about the folder."""
    mysql = FakeMysql(("acore_characters", "acore_world"))
    report = maintenance.backup(tmp_path, mysql, game=GAME, running=running(DB), now=AT)
    assert mysql.dumped == ["acore_characters", "acore_world"]
    assert len(report.dumps) == 2


def test_restoring_again_over_an_unfinished_restore_still_takes_its_safety_copy(
    tmp_path: Path,
) -> None:
    """The way out T681 names (restore a backup again) keeps working over the record."""
    _half_restored(tmp_path, tmp_path)
    mysql = FakeMysql(("acore_auth", "acore_characters"))
    auth = a_backup_of(tmp_path, "acore_auth", name="auth.sql")
    plan = plan_restore(auth, tmp_path, running=running(DB))
    assert plan.allowed, plan.refusals
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert mysql.dumped == ["acore_auth"]
    assert [p.name for p in report.safety_backup] == ["20261012_090000_pre-restore_acore_auth.sql"]


def test_a_safety_copy_of_a_half_written_database_is_labelled_so(tmp_path: Path) -> None:
    """The record names acore_characters but its own copy is gone: the copy taken now is of
    the half-written database, and its name says so.

    Mutation: label every safety copy `pre-restore` again -> red.
    """
    _half_restored(tmp_path, tmp_path, safety=False)
    mysql = FakeMysql(("acore_characters", "acore_world"))
    both = a_backup_of(tmp_path, "acore_characters", "acore_world", name="both.sql")
    plan = plan_restore(both, tmp_path, running=running(DB))
    report = restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert sorted(p.name for p in report.safety_backup) == [
        "20261012_090000_half-restored_acore_characters.sql",
        "20261012_090000_pre-restore_acore_world.sql",
    ]


def test_the_automatic_keep_never_deletes_a_good_copy_because_of_a_half_written_one(
    tmp_path: Path,
) -> None:
    """A good backup you took, then a restore over a half restore whose copy was gone.

    Before: the half-written database's safety copy was the newest "good copy" of
    acore_characters, so keep-1 selected the good backup for deletion.

    Mutation: let the shelf count `half-restored` copies as good ones -> red.
    """
    good = maintenance.backup(
        tmp_path,
        FakeMysql(("acore_characters",)),
        game=GAME,
        running=running(DB),
        now=datetime(2026, 10, 11, 9, 0, 0),
    ).dumps[0]
    _half_restored(tmp_path, tmp_path, safety=False)
    chars = a_backup_of(tmp_path, "acore_characters", name="chars.sql")
    plan = plan_restore(chars, tmp_path, running=running(DB))
    restore(plan, FakeMysql(("acore_characters",)), confirm=plan.token, running=running(DB), now=AT)
    assert maintenance.interrupted_restore(tmp_path) is None  # the restore finished

    shelf = backup_shelf.read_shelf(tmp_path, game_id="wow-wotlk", installed={})
    plan_ = backup_shelf.plan_clean_up(shelf, backup_shelf.Rule(keep_newest=1), now=AT)
    assert good.path.name not in plan_.names, "the good backup was chosen for deletion"
    rows = {r.name: r for r in shelf.rows}
    half = rows["20261012_090000_half-restored_acore_characters.sql"]
    assert "newest good copy" not in (half.kept_because or "")
    assert "half-written" in backup_shelf.what_made_it(half)
    assert rows[good.path.name].database == half.database == "acore_characters"
