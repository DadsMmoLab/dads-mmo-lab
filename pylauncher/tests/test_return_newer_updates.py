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


def _dump(path: Path, names: tuple[str, ...], *, complete: bool = True) -> None:
    """A mysqldump of an AzerothCore database whose `updates` table holds `names`."""
    rows = ",".join(f"('{name}','0a1b','RELEASED','2026-10-01 00:00:00',0)" for name in names)
    body = (
        "-- MySQL dump 10.13\n"
        "CREATE TABLE `updates` (\n  `name` varchar(200) NOT NULL\n);\n"
        f"INSERT INTO `updates` VALUES {rows};\n"
    )
    if complete:
        body += "-- Dump completed on 2026-10-08 12:00:00\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _backups(server_dir: Path) -> Path:
    return server_dir / snapshot.BACKUPS_FOLDER


def _target_ships(server_dir: Path, folder: str, *names: str) -> None:
    """The files the target commit has in `folder`, on disk (the fake clone moves only heads)."""
    where = server_dir / BOTS.dest / folder
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
    _target_ships(server_dir, BOTS_UPDATES, TARGET_NEWEST)
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
    assert "the older server would meet acore_playerbots as those updates left it" in message
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
    (server_dir / world).mkdir(parents=True, exist_ok=True)
    (server_dir / world / "2026_09_19_02.sql").write_text("--\n", encoding="utf-8")
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
    _target_ships(server_dir, "data/sql/playerbots/archive/2026", SPEECH)
    rec.applied_updates["acore_playerbots"] = f"{SPEECH}\n"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is None, raised
    assert _asked_updates(rec) == []


def test_an_older_update_the_target_squashed_away_is_not_newer(tmp_path: Path) -> None:
    """A Return moving FORWARD (T588) over a squash: the gone file is older than the target's."""
    rec, server_dir = _ready(tmp_path)
    old_one = "2026_01_02_00_ai_playerbot_texts.sql"
    rec.diffs[(server_dir / BOTS.dest, OLD, BOTS_PIN)] = (("D", f"{BOTS_UPDATES}/{old_one}"),)
    _target_ships(server_dir, BOTS_UPDATES, TARGET_NEWEST)
    rec.applied_updates["acore_playerbots"] = f"{old_one}\n"

    _said, raised, _fake = _return(rec, server_dir)

    assert raised is None, raised
    assert _asked_updates(rec) == []


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
    got = snapshot.copy_from_before(tmp_path, "acore_playerbots", (SPEECH,))
    assert (got == path) is found, got


def test_copy_from_before_finds_a_name_split_across_reads(tmp_path: Path) -> None:
    """The dump is read in pieces; a name straddling two of them is still seen."""
    path = tmp_path / CLEAN
    head = (
        "CREATE TABLE `updates` (\n  `name` varchar(200) NOT NULL\n);\n"
        "INSERT INTO `updates` VALUES ('"
    )
    pad = snapshot.DUMP_READ_BYTES - 10 - len(head) - len("-- \n")
    body = (
        f"-- {'x' * pad}\n{head}{SPEECH}','0a1b','RELEASED','2026-10-01 00:00:00',0);\n"
        "-- Dump completed on 2026-10-08 12:00:00\n"
    )
    assert body.index(SPEECH) == snapshot.DUMP_READ_BYTES - 10
    path.write_text(body, encoding="utf-8")
    assert snapshot.copy_from_before(tmp_path, "acore_playerbots", (SPEECH,)) is None
