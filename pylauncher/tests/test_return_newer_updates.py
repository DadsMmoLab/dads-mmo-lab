"""T630: "Return to the tested pin…" refuses a database a newer build already updated.

Live on m910q, 2026-10-09: an Unbound install that "Update the server to latest…"
had moved to mod-playerbots 037c0141 was sent back to 7bae1b5c. The old build
compiled for fifty minutes and then crash-looped: 037c0141's update
`2026_09_21_00_playerbots_speech.sql` had dropped `playerbots_speech`, which
7bae1b5c reads, and AzerothCore's updaters only go forward. The rollback then
pruned the copy the earlier update had kept from before it.

The fix asks, right after the move and before anything is built, stopped or
copied: which `.sql` files does the move take away that the target no longer
ships, and does the database's `updates` table hold any of them? If it does, the
press refuses, every source goes back, and the sentence names a copy in the
backups folder that is really from before those updates.

Every test drives the real `update_to_latest(to_pin=True)` on an installed WotLK
folder (`tests.support_native.Recorder`), with the database's `updates` table
answered at the `sql_query` seam the route asks.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support_native import ENTRY, FakeSnapshot, Recorder, engine
from tests.test_update_to_latest import OLD, _ready
from yulon import server_build_presses
from yulon.catalog import native, snapshot
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions, installer_for
from yulon.controller_wow_wotlk import maintenance

CORE, BOTS = ENTRY.emulator.sources
BOTS_PIN = BOTS.rev or ""
CORE_PIN = CORE.rev or ""
SPEECH = "2026_09_21_00_playerbots_speech.sql"
REQUESTER = "2026_09_13_00_ai_playerbot_target_requester_text.sql"
BOTS_UPDATES = "data/sql/playerbots/updates"
"""Where mod-playerbots keeps its own database's updates (037c0141 and 7bae1b5c alike)."""
TARGET_NEWEST = "2026_07_26_00_ai_playerbot_autogear_texts.sql"
"""7bae1b5c's newest playerbots update, measured on GitHub 2026-10-09."""

CLEAN = "20261008_120000_before-new-build_acore_playerbots.sql"
AFTER = "20261009_205101_before-new-build_acore_playerbots.sql"
CUT_SHORT = "20261009_210000_acore_playerbots.sql"


def _dump(
    path: Path, names: tuple[str, ...], *, complete: bool = True, game: str | None = "wow-wotlk"
) -> None:
    """A mysqldump of an AzerothCore database whose `updates` table holds `names`.

    With the game record Yu'lon writes above the banner (T603), unless `game` is None.
    """
    rows = ",".join(f"('{name}','0a1b','RELEASED','2026-10-01 00:00:00',0)" for name in names)
    record = f"-- yulon-backup: game={game}\n" if game else ""
    body = (
        f"{record}-- MySQL dump 10.13\n"
        "CREATE TABLE `updates` (\n  `name` varchar(200) NOT NULL\n);\n"
        f"INSERT INTO `updates` VALUES {rows};\n"
    )
    if complete:
        body += "-- Dump completed on 2026-10-08 12:00:00\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _backups(server_dir: Path) -> Path:
    return server_dir / snapshot.BACKUPS_FOLDER


def _target_ships(
    rec: Recorder,
    server_dir: Path,
    folder: str,
    *names: str,
    dest: str = BOTS.dest,
    rev: str = BOTS_PIN,
) -> None:
    """The files the target commit tracks in `folder`: in its tree, and on disk as checked out.

    The route must read the tree (`git ls-tree`); the disk copy is there so a route that
    read the disk instead would see the same files and pass or fail for the same reason.
    """
    key = (server_dir / dest, rev)
    rec.trees[key] = (*(rec.trees.get(key) or ()), *(f"{folder}/{name}" for name in names))
    where = server_dir / dest / folder
    where.mkdir(parents=True, exist_ok=True)
    for name in names:
        (where / name).write_text("-- an update\n", encoding="utf-8")


def _newer_bots_update(rec: Recorder, server_dir: Path, *, applied: bool = True) -> None:
    """The live case: going back removes 037c0141's two playerbots updates; the DB has them."""
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (
        ("D", f"{BOTS_UPDATES}/{REQUESTER}"),
        ("D", f"{BOTS_UPDATES}/{SPEECH}"),
        ("M", "src/PlayerbotAI.cpp"),
    )
    _target_ships(rec, server_dir, BOTS_UPDATES, TARGET_NEWEST)
    if applied:
        rec.applied_updates["acore_playerbots"] = f"{TARGET_NEWEST}\n{REQUESTER}\n{SPEECH}\n"


def _return(
    rec: Recorder, server_dir: Path, *, to_pin: bool = True
) -> tuple[list[str], Exception | None, FakeSnapshot]:
    made = engine(rec)
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    said: list[str] = []
    try:
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=to_pin):
            said.append(line)
    except InstallerError as exc:
        return said, exc, fake
    return said, None, fake


def _asked_updates(rec: Recorder) -> list[str]:
    return [statement for statement in rec.sql_calls if "FROM updates WHERE name IN" in statement]


def _built(rec: Recorder) -> bool:
    return any(call.startswith(("build", "recreate", "stop-servers")) for call in rec.calls)


# -- the refusal ----------------------------------------------------------------


def test_a_return_over_a_newer_playerbots_update_refuses_before_anything_is_built(
    tmp_path: Path,
) -> None:
    """The live press, refused where it is still free: nothing built, stopped or copied."""
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    _dump(_backups(server_dir) / CLEAN, (TARGET_NEWEST,))

    said, raised, fake = _return(rec, server_dir)

    assert raised is not None, "the return went ahead over a database it cannot read"
    message = str(raised)
    assert "acore_playerbots has 2 updates the tested commit does not have" in message, message
    assert SPEECH in message and REQUESTER in message
    assert "Database updates only go forward" in message
    assert "that commit's server would meet acore_playerbots as those updates left it" in message
    assert not re.search(r"\bolder\b|Going back", message), message
    assert "Nothing was built, stopped or changed" in message
    assert f"backups/{CLEAN}" in message
    assert server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN) in message
    assert native.SOURCES_PUT_BACK_NOTE in message
    # Every source back on the commit it was on, and nothing built or copied.
    assert {rec.heads[server_dir / s.dest] for s in ENTRY.emulator.sources} == {OLD}
    assert not _built(rec), rec.calls
    assert fake.taken == [], "a copy was taken (and older ones pruned) for a refused press"
    # The database was brought up (alone) before it was asked.
    assert "start-db" in rec.calls
    assert rec.calls.index("start-db") < rec.calls.index("query")
    assert any("acore_playerbots" in line or "database update" in line for line in said)


def test_the_core_worlds_newer_updates_refuse_too(tmp_path: Path) -> None:
    """The core's own `db_world` updates that T220's import applied, f19a1879 -> 7f12e89e."""
    rec, server_dir = _ready(tmp_path)
    world = "data/sql/updates/db_world"
    rec.diffs[(server_dir, OLD, CORE_PIN)] = (("D", f"{world}/2026_09_21_05.sql"),)
    _target_ships(rec, server_dir, world, "2026_09_19_02.sql", dest=".", rev=CORE_PIN)
    rec.applied_updates["acore_world"] = "2026_09_21_05.sql\n"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is not None
    message = str(raised)
    assert "acore_world has 1 update the tested commit does not have (2026_09_21_05.sql)" in (
        message
    ), message
    assert "acore_playerbots" not in message
    assert rec.heads[server_dir] == OLD
    assert not _built(rec)


def test_the_copy_named_is_the_newest_complete_one_from_before_the_updates(
    tmp_path: Path,
) -> None:
    """Never a copy that already holds them, and never one that was cut short."""
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    backups = _backups(server_dir)
    _dump(backups / "20261001_090000_acore_playerbots.sql", (TARGET_NEWEST,))
    _dump(backups / CLEAN, (TARGET_NEWEST,))
    _dump(backups / AFTER, (TARGET_NEWEST, REQUESTER, SPEECH))
    _dump(backups / CUT_SHORT, (TARGET_NEWEST,), complete=False)
    _dump(backups / "20261008_120000_before-new-build_acore_world.sql", ())

    _said, raised, _fake = _return(rec, server_dir)

    message = str(raised)
    assert f"backups/{CLEAN}" in message, message
    for wrong in (AFTER, CUT_SHORT, "20261001_090000", "acore_world.sql"):
        assert wrong not in message, wrong
    assert "Restoring loses whatever changed in acore_playerbots since that copy was taken" in (
        message
    )
    assert "without starting the server in between" in message
    assert "Do not press Clean up… on Maintenance before restoring" in message


def test_with_no_copy_from_before_it_says_the_way_back_is_closed(tmp_path: Path) -> None:
    """Only copies that already hold the updates: none is named, and the sentence says so."""
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    _dump(_backups(server_dir) / AFTER, (TARGET_NEWEST, REQUESTER, SPEECH))

    _said, raised, _fake = _return(rec, server_dir)

    message = str(raised)
    assert AFTER not in message
    assert "Yu'lon found no copy of acore_playerbots from before those updates" in message, message
    assert server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST) in message
    assert {rec.heads[server_dir / s.dest] for s in ENTRY.emulator.sources} == {OLD}


# -- what does not refuse ---------------------------------------------------------


def test_removed_updates_the_database_never_applied_let_the_return_build(tmp_path: Path) -> None:
    """The files go, but the ledger holds neither: the old build reads nothing it lacks."""
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir, applied=False)
    rec.applied_updates["acore_playerbots"] = f"{TARGET_NEWEST}\n"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is None, raised
    assert rec.heads[server_dir / BOTS.dest] == BOTS_PIN
    assert _asked_updates(rec), "the database was never asked"


def test_a_return_that_removes_no_update_asks_the_database_nothing(tmp_path: Path) -> None:
    """The ordinary Return: no `.sql` goes, so no database question and no extra start."""
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("M", "src/PlayerbotAI.cpp"),)

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is None, raised
    assert _asked_updates(rec) == []


def test_an_update_moved_to_the_archive_is_not_a_removal(tmp_path: Path) -> None:
    """Upstream archives updates at a release: the name is still shipped, only elsewhere."""
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("D", f"{BOTS_UPDATES}/{SPEECH}"),)
    _target_ships(rec, server_dir, "data/sql/playerbots/archive/2026", SPEECH)
    rec.applied_updates["acore_playerbots"] = f"{SPEECH}\n"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is None, raised
    assert _asked_updates(rec) == []


def test_an_older_update_the_target_squashed_away_is_not_newer(tmp_path: Path) -> None:
    """A Return moving FORWARD (T588) over a squash: the gone file is older than the target's."""
    rec, server_dir = _ready(tmp_path)
    old_one = "2026_01_02_00_ai_playerbot_texts.sql"
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("D", f"{BOTS_UPDATES}/{old_one}"),)
    _target_ships(rec, server_dir, BOTS_UPDATES, TARGET_NEWEST)
    rec.applied_updates["acore_playerbots"] = f"{old_one}\n"
    rec.ancestors.add((server_dir / BOTS.dest, OLD, BOTS_PIN))

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is None, raised
    assert _asked_updates(rec) == []


def test_a_back_dated_update_added_since_the_target_is_newer_on_a_backward_return(
    tmp_path: Path,
) -> None:
    """mod-playerbots names an update by the day it was written, not merged: the one the
    running commit added since the target can sort before the target's newest, and the
    database holds it all the same. Going back, no name order may skip it."""
    rec, server_dir = _ready(tmp_path)
    late = "2026_07_01_00_ai_playerbot_written_in_july.sql"
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("D", f"{BOTS_UPDATES}/{late}"),)
    _target_ships(rec, server_dir, BOTS_UPDATES, TARGET_NEWEST)
    rec.applied_updates["acore_playerbots"] = f"{late}\n"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is not None, "an applied back-dated update was skipped by its name"
    assert late in str(raised)


def test_update_to_latest_asks_nothing_of_the_kind(tmp_path: Path) -> None:
    """Scoped to the way back: the forward press is T217/T220's and is left exactly as it was."""
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir / BOTS.dest, OLD, "b" * 40)] = (("D", f"{BOTS_UPDATES}/{SPEECH}"),)
    rec.applied_updates["acore_playerbots"] = f"{SPEECH}\n"

    _said, raised, _fake = _return(rec, server_dir, to_pin=False)

    assert raised is None, raised
    assert _asked_updates(rec) == []


# -- fail closed --------------------------------------------------------------------


def test_git_that_cannot_say_what_the_move_removes_refuses(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = None

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is not None
    assert "could not read which database updates" in str(raised), raised
    assert "Nothing was built, stopped or changed" in str(raised)
    assert {rec.heads[server_dir / s.dest] for s in ENTRY.emulator.sources} == {OLD}
    assert not _built(rec)


def test_a_database_that_cannot_be_asked_refuses(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    rec.updates_error = "ERROR 2002 (HY000): Can't connect to local MySQL server"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is not None
    message = str(raised)
    assert "could not ask acore_auth which of them it already has" in message, message
    assert "Can't connect" in message
    assert {rec.heads[server_dir / s.dest] for s in ENTRY.emulator.sources} == {OLD}
    assert not _built(rec)


def test_a_database_that_will_not_start_refuses(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    rec.db_start_error = "ac-database exited 1"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is not None
    assert "could not start the database to ask it" in str(raised), raised
    assert _asked_updates(rec) == []
    assert not _built(rec)


# -- the parts ------------------------------------------------------------------------


def test_the_backups_folder_is_the_maintenance_tabs() -> None:
    """`catalog/` may not import a controller; the two spellings are pinned equal here."""
    assert snapshot.BACKUPS_FOLDER == maintenance.BACKUP_SUBDIR


def test_unbound_runs_the_same_check() -> None:
    """Unbound is the AzerothCore family on the same sources (it is where this was seen)."""
    assert isinstance(installer_for(load_catalog().get("wow-unbound")), AzerothCoreInstaller)


@pytest.mark.parametrize(
    ("names", "complete", "table", "found"),
    [
        ((TARGET_NEWEST,), True, True, True),
        ((TARGET_NEWEST, SPEECH), True, True, False),
        ((TARGET_NEWEST,), False, True, False),
        ((TARGET_NEWEST,), True, False, False),
    ],
    ids=["clean", "holds-the-update", "cut-short", "no-updates-table"],
)
def test_copy_from_before_reads_the_dump_itself(
    tmp_path: Path, names: tuple[str, ...], complete: bool, table: bool, found: bool
) -> None:
    path = tmp_path / CLEAN
    _dump(path, names, complete=complete)
    if not table:
        path.write_text(
            path.read_text(encoding="utf-8").replace("CREATE TABLE `updates`", "CREATE TABLE `x`"),
            encoding="utf-8",
        )
    got = snapshot.copy_from_before(tmp_path, "acore_playerbots", (SPEECH,), game="wow-wotlk")
    assert (got == path) is found, got


def test_copy_from_before_finds_a_name_split_across_reads(tmp_path: Path) -> None:
    """The dump is read in pieces; a name straddling two of them is still seen.

    The same dump with another name in that place IS named, so the refusal to name it
    is the name's doing and not the dump's.
    """
    path = tmp_path / CLEAN
    banner = "-- MySQL dump 10.13\n"
    head = (
        "CREATE TABLE `updates` (\n  `name` varchar(200) NOT NULL\n);\n"
        "INSERT INTO `updates` VALUES ('"
    )
    pad = snapshot.DUMP_READ_BYTES - 10 - len(banner) - len(head) - len("-- \n")

    def body(name: str) -> str:
        return (
            f"{banner}-- {'x' * pad}\n{head}{name}','0a1b','RELEASED','2026-10-01 00:00:00',0);\n"
            "-- Dump completed on 2026-10-08 12:00:00\n"
        )

    assert body(SPEECH).index(SPEECH) == snapshot.DUMP_READ_BYTES - 10
    path.write_text(body(SPEECH), encoding="utf-8")
    assert (
        snapshot.copy_from_before(tmp_path, "acore_playerbots", (SPEECH,), game="wow-wotlk") is None
    )
    path.write_text(body(TARGET_NEWEST), encoding="utf-8")
    assert (
        snapshot.copy_from_before(tmp_path, "acore_playerbots", (SPEECH,), game="wow-wotlk") == path
    )


# -- cold review of a72e048f: what must still count as newer ----------------------------


def test_a_name_that_is_not_dated_beside_the_updates_does_not_hide_one(tmp_path: Path) -> None:
    """Letters sort after digits: a `playerbots_readme.sql` is not a newer update."""
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("D", f"{BOTS_UPDATES}/{SPEECH}"),)
    _target_ships(rec, server_dir, BOTS_UPDATES, TARGET_NEWEST, "playerbots_readme.sql")
    rec.applied_updates["acore_playerbots"] = f"{SPEECH}\n"
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None, "a non-dated sibling skipped the applied update"


def test_the_same_name_for_another_database_is_not_the_same_update(tmp_path: Path) -> None:
    """`db_world/2026_09_21_00.sql` gone; `db_characters/2026_09_21_00.sql` is another file."""
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir, OLD, CORE_PIN)] = (("D", "data/sql/updates/db_world/2026_09_21_00.sql"),)
    _target_ships(
        rec,
        server_dir,
        "data/sql/updates/db_characters",
        "2026_09_21_00.sql",
        dest=".",
        rev=CORE_PIN,
    )
    rec.applied_updates["acore_world"] = "2026_09_21_00.sql\n"
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None, "the same base name in another database's folder hid it"
    assert "acore_world has 1 update" in str(raised)


def test_an_archived_core_update_still_counts_as_shipped(tmp_path: Path) -> None:
    """The core's own archive layout: `archive/db_world/<version>/` is db_world's too."""
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir, OLD, CORE_PIN)] = (("D", "data/sql/updates/db_world/2026_09_21_00.sql"),)
    _target_ships(
        rec,
        server_dir,
        "data/sql/archive/db_world/6.x",
        "2026_09_21_00.sql",
        dest=".",
        rev=CORE_PIN,
    )
    rec.applied_updates["acore_world"] = "2026_09_21_00.sql\n"
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is None, raised
    assert _asked_updates(rec) == []


def test_a_file_on_disk_the_target_does_not_track_is_not_shipped(tmp_path: Path) -> None:
    """The target's list is its commit's tree, so an untracked copy on disk hides nothing."""
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("D", f"{BOTS_UPDATES}/{SPEECH}"),)
    stray = server_dir / BOTS.dest / "data/sql/custom" / SPEECH
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("-- a copy somebody left\n", encoding="utf-8")
    rec.applied_updates["acore_playerbots"] = f"{SPEECH}\n"
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None, "an untracked same-name file on disk hid the update"


def test_git_that_cannot_list_the_target_refuses(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("D", f"{BOTS_UPDATES}/{SPEECH}"),)
    rec.trees[(server_dir / BOTS.dest, BOTS_PIN)] = None
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None
    assert "could not list" in str(raised), raised
    assert not _built(rec)


def test_a_database_that_was_down_is_stopped_again_after_a_refusal(tmp_path: Path) -> None:
    """The refusal says nothing was started or changed, so the database goes back down."""
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    rec.db_was_up = False
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None
    started = rec.calls.index("start-db")
    assert any(call.startswith("stop-db:") for call in rec.calls[started:]), rec.calls


def test_a_database_that_was_up_is_left_up(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None
    assert not any(call.startswith("stop-db:") for call in rec.calls), rec.calls


def test_a_database_that_was_down_goes_back_down_when_the_return_goes_on(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir, applied=False)
    rec.db_was_up = False
    said, raised, _fake = _return(rec, server_dir)
    assert raised is None, raised
    asked = rec.calls.index("start-db")
    assert any(call.startswith("stop-db:") for call in rec.calls[asked:]), rec.calls
    assert any("The database is stopped again" in line for line in said)


def test_a_dump_another_game_recorded_is_never_named(tmp_path: Path) -> None:
    """WotLK and Unbound share the acore_* names; Restore refuses the other game's dump."""
    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    backups = _backups(server_dir)
    _dump(backups / AFTER.replace("205101", "200000"), (TARGET_NEWEST,), game="wow-unbound")
    _dump(backups / CLEAN, (TARGET_NEWEST,), game=None)
    _said, raised, _fake = _return(rec, server_dir)
    message = str(raised)
    assert "200000" not in message, message
    assert (
        f"backups/{CLEAN}" in message
    ), "an old dump with no record is still one Restore asks about"


@pytest.mark.parametrize(
    ("record", "kept"),
    [
        ("-- yulon-backup: game=wow-wotlk\n", True),
        ("", True),
        ("-- yulon-backup: game=wow-unbound\n", False),
        ("-- yulon-backup: game=Wow WotLK\n", False),
        ("-- yulon-backup: game=wow-wotlk\n-- yulon-backup: game=wow-unbound\n", False),
    ],
    ids=["this-game", "no-record", "other-game", "garbled", "two-games"],
)
def test_the_game_record_is_read_as_restore_reads_it(
    tmp_path: Path, record: str, kept: bool
) -> None:
    """`copy_from_before()`'s reading agrees with `maintenance.backup_game()` on each shape."""
    path = tmp_path / CLEAN
    _dump(path, (TARGET_NEWEST,), game=None)
    path.write_text(record + path.read_text(encoding="utf-8"), encoding="utf-8")
    found = snapshot.copy_from_before(tmp_path, "acore_playerbots", (SPEECH,), game="wow-wotlk")
    assert (found == path) is kept
    try:
        restore_reads = maintenance.backup_game(path)
    except maintenance.MaintenanceError:
        restore_reads = "refused"
    assert (restore_reads in (None, "wow-wotlk")) is kept, restore_reads


def test_tree_files_lists_what_the_commit_tracks_and_not_the_disk(tmp_path: Path) -> None:
    """Real git: an untracked file beside the tracked one is not in the commit's list."""
    import subprocess

    from yulon import git

    if not git.git_available():
        pytest.skip("no host git")
    repo = tmp_path / "repo"
    (repo / BOTS_UPDATES).mkdir(parents=True)
    (repo / BOTS_UPDATES / TARGET_NEWEST).write_text("--\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "x.cpp").write_text("//\n", encoding="utf-8")

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    run("init", "-q")
    run("add", ".")
    run("commit", "-q", "-m", "one")
    head = run("rev-parse", "HEAD")
    (repo / BOTS_UPDATES / SPEECH).write_text("-- untracked\n", encoding="utf-8")

    got = git.RunnerGit().tree_files(repo, head, ("data/sql",))
    assert got == (f"{BOTS_UPDATES}/{TARGET_NEWEST}",)
    assert git.tree_files_args(head, ("data/sql",)) == [
        "ls-tree",
        "-r",
        "-z",
        "--name-only",
        head,
        "--",
        "data/sql",
    ]


def test_a_stopped_database_is_started_from_yulons_compose_not_the_targets(
    tmp_path: Path,
) -> None:
    """Live on m910q, 2026-10-09: with the server stopped the check had to start the database,
    and the move had just put the TARGET's own `docker-compose.yml` in the core checkout.
    `compose up ub-database` refused that file ("service ub-worldserver has neither an image
    nor a build context"), so the press refused as "could not start the database" instead of
    naming the updates. Yu'lon's compose is written back before the family's check now.
    """
    from tests.support_native import UPSTREAM_COMPOSE
    from yulon import docker
    from yulon.catalog import composegen

    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    rec.db_was_up = False
    compose = server_dir / composegen.BASE_FILE

    def start_db(spec: object, folder: Path, *, because: str = "") -> None:
        rec.calls.append("start-db")
        if compose.read_text(encoding="utf-8") == UPSTREAM_COMPOSE:
            raise docker.DockerCommandError(
                'service "ub-worldserver" has neither an image nor a build context specified'
            )
        rec.db_started = True

    made = engine(rec, start_db=start_db)
    made._snapshot = FakeSnapshot(rec)
    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=True))
    message = str(raised.value)
    assert "acore_playerbots has 2 updates the tested commit does not have" in message, message
    assert compose.read_text(encoding="utf-8") != UPSTREAM_COMPOSE, "the compose went back"
    assert {rec.heads[server_dir / s.dest] for s in ENTRY.emulator.sources} == {OLD}


# -- scoped re-review of a2f7ef7a ----------------------------------------------------------

PENDING = "rev_1727000000000000000.sql"
SQUASHED = "2026_10_01_00.sql"
PENDING_SQL = ("UPDATE `creature_template` SET `speed_walk` = 1 WHERE `entry` = 18910;",)


def _pending_squash(rec: Recorder, server_dir: Path, *, same_sql: bool) -> None:
    """AzerothCore's import of pending files: `pending_db_world/rev_*` -> a dated file."""
    rec.diffs[(server_dir, OLD, CORE_PIN)] = (
        ("D", f"data/sql/updates/pending_db_world/{PENDING}"),
        ("A", f"data/sql/updates/db_world/{SQUASHED}"),
    )
    _target_ships(
        rec,
        server_dir,
        "data/sql/updates/db_world",
        "2026_09_30_00.sql",
        SQUASHED,
        dest=".",
        rev=CORE_PIN,
    )
    rec.lines[(server_dir, OLD, f"data/sql/updates/pending_db_world/{PENDING}")] = PENDING_SQL
    rec.lines[(server_dir, CORE_PIN, f"data/sql/updates/db_world/{SQUASHED}")] = (
        "-- DB update 2026_09_30_00 -> 2026_10_01_00",
        "--",
        *(PENDING_SQL if same_sql else ("DELETE FROM `creature` WHERE `guid` = 1;",)),
        "",
    )
    rec.applied_updates["acore_world"] = f"{PENDING}\n"


def test_a_return_over_a_pending_squash_is_not_refused(tmp_path: Path) -> None:
    """The reviewer's probe: the applied `rev_` file was only re-filed, header and all."""
    rec, server_dir = _ready(tmp_path)
    _pending_squash(rec, server_dir, same_sql=True)
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is None, f"a wrong refusal on a squash: {raised}"
    assert _asked_updates(rec) == []


def test_a_removed_file_whose_sql_went_nowhere_still_refuses(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _pending_squash(rec, server_dir, same_sql=False)
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None
    assert f"acore_world has 1 update the tested commit does not have ({PENDING})" in str(raised)


def test_git_that_cannot_read_the_files_refuses(tmp_path: Path) -> None:
    rec, server_dir = _ready(tmp_path)
    _pending_squash(rec, server_dir, same_sql=True)
    rec.lines_unreadable = True
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None
    assert "git could not read the update files it removes and adds" in str(raised), raised
    assert not _built(rec)


def test_an_unknown_database_state_is_said_not_hidden(tmp_path: Path) -> None:
    """`db_running` that cannot say: the refusal must not claim nothing was started."""
    from yulon.catalog.families import azerothcore

    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir)
    rec.db_was_up = None
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None
    assert azerothcore.DATABASE_MAYBE_STARTED in str(raised), raised
    assert not any(call.startswith("stop-db:") for call in rec.calls)


def test_file_lines_reads_every_file_in_one_git_run(tmp_path: Path) -> None:
    """Real git: two files' lines at a commit, an empty line kept, an uncommitted edit not."""
    import subprocess

    from yulon import git

    if not git.git_available():
        pytest.skip("no host git")
    repo = tmp_path / "repo"
    (repo / "data/sql").mkdir(parents=True)
    (repo / "data/sql/a.sql").write_text("-- a\n\nSELECT 1;\n", encoding="utf-8")
    (repo / "data/sql/b.sql").write_text("SELECT 2;\n", encoding="utf-8")

    def run(*args: str) -> str:
        return subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
            cwd=repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    run("init", "-q")
    run("add", ".")
    run("commit", "-q", "-m", "one")
    head = run("rev-parse", "HEAD")
    (repo / "data/sql/a.sql").write_text("changed on disk\n", encoding="utf-8")
    got = git.RunnerGit().file_lines(repo, head, ("data/sql/a.sql", "data/sql/b.sql"))
    assert got == {
        "data/sql/a.sql": ("-- a", "", "SELECT 1;"),
        "data/sql/b.sql": ("SELECT 2;",),
    }


# -- re-review of 2db10bd9 ------------------------------------------------------------------


def test_file_lines_asks_git_by_folder_so_thousands_of_files_stay_under_the_windows_limit() -> None:
    """An install months behind: thousands of added update files, two folders.

    Windows caps a command line at 32,767 characters (docker.exe and wsl.exe alike), so the
    argv names the folders and the answer is filtered to the files asked.
    """
    from yulon import git

    paths = [
        f"data/sql/updates/db_world/2026_{n // 100:02d}_{n % 100:02d}_00.sql" for n in range(4000)
    ]
    paths += [f"data/sql/updates/pending_db_world/rev_{n}.sql" for n in range(1000)]
    args = git.file_lines_args("b" * 40, paths)
    assert len(" ".join(args)) < 1000, len(" ".join(args))
    assert "data/sql/updates/db_world" in args and "data/sql/updates/pending_db_world" in args


def test_file_lines_matches_every_line_with_a_pattern_that_cannot_vanish() -> None:
    """An empty argument may be dropped on the way through wsl.exe; `^` cannot be."""
    from yulon import git

    args = git.file_lines_args("b" * 40, ["data/sql/a.sql"])
    assert "" not in args, args
    assert args[args.index("-e") + 1] == "^"
    assert "-I" in args


def test_file_lines_keeps_only_the_files_asked(tmp_path: Path) -> None:
    """Real git: the folder is grepped, the answer holds only the named file."""
    import subprocess

    from yulon import git

    if not git.git_available():
        pytest.skip("no host git")
    repo = tmp_path / "repo"
    (repo / "data/sql").mkdir(parents=True)
    (repo / "data/sql/a.sql").write_text("SELECT 1;\n\n", encoding="utf-8")
    (repo / "data/sql/b.sql").write_text("SELECT 2;\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "one"]):
        subprocess.run(
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True
        )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    assert git.RunnerGit().file_lines(repo, head, ("data/sql/a.sql",)) == {
        "data/sql/a.sql": ("SELECT 1;", "")
    }


def test_the_same_sql_filed_for_another_database_is_not_a_refiling(tmp_path: Path) -> None:
    """One added `db_characters/` file holding SQL that a removed world file also holds.

    It re-files the removed CHARACTERS file, never the world one: the world update stays
    lacked even though it comes first and its SQL is the same.
    """
    rec, server_dir = _ready(tmp_path)
    chars = "rev_1727000000000000009.sql"
    rec.diffs[(server_dir, OLD, CORE_PIN)] = (
        ("D", f"data/sql/updates/pending_db_world/{PENDING}"),
        ("D", f"data/sql/updates/pending_db_characters/{chars}"),
        ("A", f"data/sql/updates/db_characters/{SQUASHED}"),
    )
    _target_ships(
        rec, server_dir, "data/sql/updates/db_characters", SQUASHED, dest=".", rev=CORE_PIN
    )
    rec.lines[(server_dir, OLD, f"data/sql/updates/pending_db_world/{PENDING}")] = PENDING_SQL
    rec.lines[(server_dir, OLD, f"data/sql/updates/pending_db_characters/{chars}")] = PENDING_SQL
    rec.lines[(server_dir, CORE_PIN, f"data/sql/updates/db_characters/{SQUASHED}")] = PENDING_SQL
    rec.applied_updates["acore_world"] = f"{PENDING}\n"
    rec.applied_updates["acore_characters"] = f"{chars}\n"
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None, "identical SQL for another database counted as the same update"
    message = str(raised)
    assert f"acore_world has 1 update the tested commit does not have ({PENDING})" in message
    assert chars not in message, message


def test_a_removed_file_with_no_sql_is_never_a_refiling(tmp_path: Path) -> None:
    """Comments only: nothing to compare, so an added comments-only file proves nothing."""
    rec, server_dir = _ready(tmp_path)
    _pending_squash(rec, server_dir, same_sql=True)
    rec.lines[(server_dir, OLD, f"data/sql/updates/pending_db_world/{PENDING}")] = ("-- empty",)
    rec.lines[(server_dir, CORE_PIN, f"data/sql/updates/db_world/{SQUASHED}")] = ("-- header",)
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None, "an empty update matched an empty one"


def test_one_added_file_re_files_one_removed_file(tmp_path: Path) -> None:
    """Two removed files with the same SQL, one added file: one of them is still lacked."""
    rec, server_dir = _ready(tmp_path)
    second = "rev_1727000000000000001.sql"
    rec.diffs[(server_dir, OLD, CORE_PIN)] = (
        ("D", f"data/sql/updates/pending_db_world/{PENDING}"),
        ("D", f"data/sql/updates/pending_db_world/{second}"),
        ("A", f"data/sql/updates/db_world/{SQUASHED}"),
    )
    _target_ships(
        rec,
        server_dir,
        "data/sql/updates/db_world",
        "2026_09_30_00.sql",
        SQUASHED,
        dest=".",
        rev=CORE_PIN,
    )
    for name in (PENDING, second):
        rec.lines[(server_dir, OLD, f"data/sql/updates/pending_db_world/{name}")] = PENDING_SQL
    rec.lines[(server_dir, CORE_PIN, f"data/sql/updates/db_world/{SQUASHED}")] = PENDING_SQL
    rec.applied_updates["acore_world"] = f"{PENDING}\n{second}\n"
    _said, raised, _fake = _return(rec, server_dir)
    assert raised is not None, "one added file re-filed two removed ones"
    assert "acore_world has 1 update the tested commit does not have" in str(raised), raised


def test_an_unknown_database_state_is_said_when_the_return_goes_on(tmp_path: Path) -> None:
    from yulon.catalog.families import azerothcore

    rec, server_dir = _ready(tmp_path)
    _newer_bots_update(rec, server_dir, applied=False)
    rec.db_was_up = None
    said, raised, _fake = _return(rec, server_dir)
    assert raised is None, raised
    assert azerothcore.DATABASE_MAYBE_STARTED in said


def test_the_containerised_git_logs_a_bounded_argv(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Thousands of paths must not become one log line thousands of characters long."""
    import logging
    import subprocess

    from yulon import git, runner

    monkeypatch.setattr(git.platform, "docker_program", lambda: "docker")
    monkeypatch.setattr(git.platform, "selinux_enforcing", lambda: False)
    monkeypatch.setattr(git.platform, "filesystem_type", lambda _p: "ext4")
    monkeypatch.setattr(
        runner, "run", lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
    )
    dest = tmp_path / "core"
    (dest / ".git").mkdir(parents=True)
    paths = [f"data/sql/x/{n:05d}.sql" for n in range(3000)]
    with caplog.at_level(logging.INFO, logger="yulon.git"):
        git.ContainerGit()._capture(dest, ["ls-tree", "-r", "HEAD", "--", *paths], writes=False)
    lines = [r.getMessage() for r in caplog.records if "containerized git" in r.getMessage()]
    assert lines and max(len(line) for line in lines) < 2000, max(len(x) for x in lines)
