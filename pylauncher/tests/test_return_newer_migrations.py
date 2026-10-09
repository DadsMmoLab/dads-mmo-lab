"""T632: Tortoise's "Return to the tested pin…" refuses a database a newer build already migrated.

T630 did this for AzerothCore by update file name. Tortoise's worldserver applies the
core's and TortoiseBots' migration files itself at start and records each in the
database's `migrations` table as `Module` + `Hash`, the key being
`<module>:<SHA-1 of the file bytes>` (`AutoUpdater.cpp` `GetMigrationKey()`, hex in
upper case), with no name and no order. So the press reads the migration files of the
commit it is on and of the target out of git's trees, hashes them, and asks each
database whether it holds a hash the target does not ship, before anything is built,
stopped or copied.

Every test drives the real `update_to_latest(to_pin=True)` on an installed Tortoise
folder, with git's trees at the `tree_files` seam and the `migrations` ledger at the
`sql_query` seam of `tests.support_native.Recorder`.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from tests.support_native import FakeSnapshot, Recorder
from tests.test_families_cmangos import ENTRY as TBC
from tests.test_update_to_latest import (  # noqa: F401 - `_gated` is an autouse fixture
    OLD,
    _cmangos,
    _gated,
)
from yulon import git, server_build_presses
from yulon.catalog import native, snapshot
from yulon.catalog.catalog import load_catalog
from yulon.catalog.installer import InstallerError, InstallOptions

TORTOISE = load_catalog().get("wow-tortoise")
CORE, BOTS = TORTOISE.emulator.sources
CORE_PIN = CORE.rev or ""
BOTS_PIN = BOTS.rev or ""
CORE_WORLD = "sql/database_updates/world"
CORE_AUTH = "sql/database_updates/auth"
CORE_CHAR = "sql/database_updates/character"
BOTS_WORLD = "data/sql/world"
BOTS_CHAR = "data/sql/char"
"""TortoiseBots keeps its character migrations here; the image gets them as `data/sql/character`."""
BOTS_CMAKE = "TortoiseBots.cmake"
CMAKE = b"""
  if(EXISTS "${TORTOISEBOTS_ROOT}/data/sql/world")
    install(DIRECTORY "${TORTOISEBOTS_ROOT}/data/sql/world/"
      DESTINATION "${CMAKE_INSTALL_PREFIX}/modules/TortoiseBots/data/sql/world")
  endif()
  if(EXISTS "${TORTOISEBOTS_ROOT}/data/sql/char")
    install(DIRECTORY "${TORTOISEBOTS_ROOT}/data/sql/char/"
      DESTINATION "${CMAKE_INSTALL_PREFIX}/modules/TortoiseBots/data/sql/character")
  endif()
"""
"""The module's own install rules, as `TortoiseBots.cmake` states them."""

NEWER = b"INSERT INTO `t` VALUES (2);\n"
NEWER_NAME = "20261001120000_world.sql"
OLDER = b"INSERT INTO `t` VALUES (1);\n"
OLDER_NAME = "20260504194945_world.sql"
NEWER_BOTS = b"ALTER TABLE `bots` DROP COLUMN `x`;\n"
NEWER_BOTS_NAME = "20261002_bots_world.sql"


def _hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest().upper()  # noqa: S324


def _dump(path: Path, keys: tuple[str, ...], *, complete: bool = True, game: str = "") -> None:
    """A mysqldump of a Tortoise database whose `migrations` table holds `keys`."""
    rows = ",".join(
        f"({n},'x','{key.partition(':')[0]}','{key.partition(':')[2]}','2026-10-01 00:00:00')"
        for n, key in enumerate(keys, 1)
    )
    body = f"-- yulon-backup: game={game}\n" if game else ""
    body += (
        "-- MySQL dump 10.13\n"
        "CREATE TABLE `migrations` (\n  `Id` int NOT NULL\n);\n"
        f"INSERT INTO `migrations` VALUES {rows};\n"
    )
    if complete:
        body += "-- Dump completed on 2026-10-08 12:00:00\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _backups(server_dir: Path) -> Path:
    return server_dir / snapshot.BACKUPS_FOLDER


def _ready(tmp_path: Path) -> tuple[Recorder, Path, native.StagedInstaller]:
    rec, server_dir, made = _cmangos(tmp_path, TORTOISE)
    made._snapshot = FakeSnapshot(rec)  # type: ignore[attr-defined]
    # Both commits ship the one old file; the target has nothing newer.
    dest = server_dir / CORE.dest
    rec.trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: OLDER}
    rec.trees[(dest, CORE_PIN, CORE_WORLD)] = {OLDER_NAME: OLDER}
    bots = server_dir / BOTS.dest
    for rev in (OLD, BOTS_PIN):
        rec.blobs[(bots, rev, BOTS_CMAKE)] = CMAKE
    return rec, server_dir, made


def _core_newer(rec: Recorder, server_dir: Path, *, applied: bool = True) -> str:
    """Going back removes a world migration of the core; the database holds it."""
    dest = server_dir / CORE.dest
    rec.trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: OLDER, NEWER_NAME: NEWER}
    key = f":{_hash(NEWER)}"
    if applied:
        rec.migrations["tw_world"] = f":{_hash(OLDER)}\n{key}\n"
    return key


def _bots_newer(rec: Recorder, server_dir: Path, *, applied: bool = True) -> str:
    dest = server_dir / BOTS.dest
    rec.trees[(dest, OLD, BOTS_WORLD)] = {NEWER_BOTS_NAME: NEWER_BOTS}
    key = f"TortoiseBots:{_hash(NEWER_BOTS)}"
    if applied:
        rec.migrations["tw_world"] = f"{key}\n"
    return key


def _return(
    made: native.StagedInstaller, server_dir: Path, *, to_pin: bool = True
) -> tuple[list[str], Exception | None]:
    said: list[str] = []
    try:
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir), to_pin=to_pin):
            said.append(line)
    except InstallerError as exc:
        return said, exc
    return said, None


def _built(rec: Recorder) -> bool:
    return any(call.startswith(("build", "recreate", "stop-servers")) for call in rec.calls)


def _asked(rec: Recorder) -> list[str]:
    return [s for s in rec.sql_calls if "`migrations`" in s and "WHERE" in s]


def _refused_clean(rec: Recorder, server_dir: Path, fake: FakeSnapshot) -> None:
    assert {rec.heads[server_dir / s.dest] for s in TORTOISE.emulator.sources} == {OLD}
    assert not _built(rec), rec.calls
    assert fake.taken == [], "a copy was taken for a refused press"


# -- the refusal ----------------------------------------------------------------


def test_a_return_over_a_core_migration_the_database_holds_refuses_first(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    clean = "20261008_120000_before-new-build_tw_world.sql"
    _dump(_backups(server_dir) / clean, (f":{_hash(OLDER)}",))

    said, raised = _return(made, server_dir)

    assert raised is not None, "the return went ahead over a database it cannot read"
    message = str(raised)
    assert (
        "tw_world has 1 migration the tested commit does not have (20261001120000_world.sql)"
        in (message)
    ), (message)
    assert "Database migrations only go forward" in message
    assert "the older server would meet tw_world as that migration left it" in message
    assert "Nothing was built, stopped or changed" in message
    assert f"backups/{clean}" in message
    assert server_build_presses.under_server_build(server_build_presses.RETURN_TO_PIN) in message
    assert native.SOURCES_PUT_BACK_NOTE in message
    _refused_clean(rec, server_dir, made._snapshot)  # type: ignore[attr-defined]
    assert rec.calls.index("start-db") < rec.calls.index("query")
    assert any("1 database migration" in line for line in said)


def test_a_module_migration_is_keyed_by_its_module(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _bots_newer(rec, server_dir)

    _said, raised = _return(made, server_dir)

    assert raised is not None
    assert "tw_world has 1 migration the tested commit does not have (TortoiseBots/" in str(raised)
    assert NEWER_BOTS_NAME in str(raised)
    _refused_clean(rec, server_dir, made._snapshot)  # type: ignore[attr-defined]


def test_a_changed_migration_counts_as_one_the_target_lacks(tmp_path: Path) -> None:
    """The file kept its name and changed bytes: the database holds the old bytes' hash."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    ran = b"-- the bytes the server ran\n"
    rec.trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: ran}
    rec.migrations["tw_world"] = f":{_hash(ran)}\n"
    # The target ships a newer file too, and the old name with other bytes.
    rec.trees[(dest, CORE_PIN, CORE_WORLD)] = {
        OLDER_NAME: b"-- new bytes\n",
        "20261201000000_world.sql": b"x",
    }

    _said, raised = _return(made, server_dir)

    assert raised is not None and "has 1 migration" in str(raised)


def test_each_database_is_named_with_its_own_count(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    auth = b"ALTER TABLE `account` ADD `y` INT;\n"
    rec.trees[(dest, OLD, CORE_AUTH)] = {"20261003_auth.sql": auth}
    rec.trees[(dest, OLD, CORE_WORLD)] = {"a_world.sql": NEWER, "b_world.sql": OLDER}
    rec.migrations["tw_logon"] = f":{_hash(auth)}\n"
    rec.migrations["tw_world"] = f":{_hash(NEWER)}\n:{_hash(OLDER)}\n"
    # The target ships neither of the world files.
    rec.trees[(dest, CORE_PIN, CORE_WORLD)] = {}

    _said, raised = _return(made, server_dir)

    message = str(raised)
    assert "tw_logon has 1 migration the tested commit does not have (20261003_auth.sql)" in message
    assert "tw_world has 2 migrations the tested commit does not have (" in message
    assert "would meet tw_logon and tw_world as those migrations left them" in message
    assert "found no copy of tw_logon and tw_world from before those migrations" in message


def test_a_ledger_that_holds_none_of_them_lets_the_return_go_on(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir, applied=False)
    rec.migrations["tw_world"] = f":{_hash(OLDER)}\n"

    said, raised = _return(made, server_dir)

    assert raised is None, raised
    assert any("None of them was applied" in line for line in said)
    assert _asked(rec), "the database was never asked"


# -- what counts as lacked: git's trees, per module and database ----------------------


def test_the_same_bytes_renamed_at_the_target_are_shipped(tmp_path: Path) -> None:
    """A key is module + hash: a file the target ships under another name is not lacked."""
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    rec.trees[(server_dir / CORE.dest, CORE_PIN, CORE_WORLD)] = {
        OLDER_NAME: OLDER,
        "20260901000000_renamed.sql": NEWER,
    }

    said, raised = _return(made, server_dir)

    assert raised is None, raised
    assert not _asked(rec), "the database was asked about a migration the target ships"
    assert not any("database migration" in line for line in said)


def test_the_same_bytes_under_another_module_do_not_count_as_shipped(tmp_path: Path) -> None:
    """TortoiseBots' migration is not the core's, whatever the bytes."""
    rec, server_dir, made = _ready(tmp_path)
    key = _bots_newer(rec, server_dir)
    rec.trees[(server_dir / CORE.dest, CORE_PIN, CORE_WORLD)] = {"x.sql": NEWER_BOTS}

    _said, raised = _return(made, server_dir)

    assert raised is not None, f"{key} was waved through because the core ships the same bytes"
    assert "TortoiseBots/" in str(raised)


def test_the_same_bytes_in_another_database_folder_do_not_count_as_shipped(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    rec.trees[(server_dir / CORE.dest, CORE_PIN, CORE_AUTH)] = {"x.sql": NEWER}

    _said, raised = _return(made, server_dir)

    assert raised is not None and "tw_world has 1 migration" in str(raised)


def test_a_copy_of_the_file_on_disk_does_not_hide_the_removal(tmp_path: Path) -> None:
    """Untracked files beside the checkout are not what the target commit ships."""
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    folder = server_dir / CORE.dest / CORE_WORLD
    folder.mkdir(parents=True, exist_ok=True)
    (folder / NEWER_NAME).write_bytes(NEWER)

    _said, raised = _return(made, server_dir)

    assert raised is not None and "tw_world has 1 migration" in str(raised)


def test_an_older_name_that_sorts_after_the_target_is_still_lacked(tmp_path: Path) -> None:
    """No "sorts after" shortcut: Tortoise's ledger has no order, only hashes."""
    rec, server_dir, made = _ready(tmp_path)
    key = _core_newer(rec, server_dir)
    rec.trees[(server_dir / CORE.dest, CORE_PIN, CORE_WORLD)] = {"zzz_later.sql": b"-- other\n"}

    _said, raised = _return(made, server_dir)

    assert raised is not None, key


# -- fail closed ------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["old", "target"])
def test_git_that_cannot_list_a_folder_refuses(tmp_path: Path, which: str) -> None:
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    rec.trees[(dest, OLD if which == "old" else CORE_PIN, CORE_AUTH)] = None

    _said, raised = _return(made, server_dir)

    assert raised is not None
    message = str(raised)
    assert "Yu'lon could not read which database migrations going back takes away" in message
    assert "git could not list sql/database_updates/auth" in message
    assert "Nothing was built, stopped or changed" in message
    _refused_clean(rec, server_dir, made._snapshot)  # type: ignore[attr-defined]
    assert "start-db" not in rec.calls, "the database was started for a question git failed"


def test_a_database_that_cannot_answer_refuses(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    rec.updates_error = "ERROR 2002: cannot connect"

    _said, raised = _return(made, server_dir)

    assert raised is not None
    message = str(raised)
    assert "could not read which database migrations your databases already have" in message
    assert "Yu'lon could not ask tw_world which of them it already has" in message
    assert "ERROR 2002" in message
    _refused_clean(rec, server_dir, made._snapshot)  # type: ignore[attr-defined]


def test_a_database_that_will_not_start_refuses(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    rec.db_start_error = "docker compose up exited 1"

    _said, raised = _return(made, server_dir)

    assert raised is not None
    assert "Yu'lon could not start the database to ask it" in str(raised)
    _refused_clean(rec, server_dir, made._snapshot)  # type: ignore[attr-defined]


def test_a_database_with_no_migrations_table_holds_none(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir, applied=False)

    _said, raised = _return(made, server_dir)

    assert raised is None, raised
    assert not _asked(rec), "the ledger was read from a database with no ledger"


# -- the database goes back down ----------------------------------------------------------


def test_a_database_the_check_started_goes_down_again_on_refusal(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    rec.db_up = False

    _said, raised = _return(made, server_dir)

    assert raised is not None
    assert any(call.startswith("stop-db:") for call in rec.calls), rec.calls
    assert rec.calls.index("start-db") < rec.calls.index(
        next(c for c in rec.calls if c.startswith("stop-db:"))
    )
    assert not _built(rec)


def test_a_database_that_was_up_is_left_up(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    rec.db_up = True

    _said, raised = _return(made, server_dir)

    assert raised is not None
    assert not any(call.startswith("stop-db:") for call in rec.calls), rec.calls


# -- the copy the refusal names --------------------------------------------------------------


def test_the_copy_named_is_the_newest_complete_one_from_before_for_this_game(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = _ready(tmp_path)
    key = _core_newer(rec, server_dir)
    backups = _backups(server_dir)
    older = (f":{_hash(OLDER)}",)
    _dump(backups / "20261001_090000_tw_world.sql", older, game=TORTOISE.id)
    clean = "20261008_120000_before-new-build_tw_world.sql"
    _dump(backups / clean, older, game=TORTOISE.id)
    after = "20261009_205101_before-new-build_tw_world.sql"
    _dump(backups / after, (*older, key), game=TORTOISE.id)
    cut = "20261009_210000_tw_world.sql"
    _dump(backups / cut, older, complete=False, game=TORTOISE.id)
    other = "20261009_220000_tw_world.sql"
    _dump(backups / other, older, game=TBC.id)
    _dump(backups / "20261009_230000_tw_char.sql", older, game=TORTOISE.id)

    _said, raised = _return(made, server_dir)

    message = str(raised)
    assert f"backups/{clean}" in message, message
    for wrong in (after, cut, other, "tw_char.sql", "20261001_090000"):
        assert wrong not in message, wrong
    assert "Restoring loses whatever changed in tw_world since that copy was taken" in message
    assert "without starting the server in between" in message


def test_a_copy_that_records_another_game_is_never_named(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    _dump(_backups(server_dir) / "20261008_120000_tw_world.sql", (f":{_hash(OLDER)}",), game=TBC.id)

    _said, raised = _return(made, server_dir)

    message = str(raised)
    assert "20261008_120000" not in message
    assert "found no copy of tw_world from before that migration" in message


def test_an_unlabelled_old_copy_may_be_named(tmp_path: Path) -> None:
    """A backup from before T603 records no game; a restore takes it after its own ask."""
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    _dump(_backups(server_dir) / "20261008_120000_tw_world.sql", (f":{_hash(OLDER)}",))

    _said, raised = _return(made, server_dir)

    assert "backups/20261008_120000_tw_world.sql" in str(raised)


def test_with_no_copy_it_says_the_way_back_is_closed(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    key = _core_newer(rec, server_dir)
    after = "20261009_205101_before-new-build_tw_world.sql"
    _dump(_backups(server_dir) / after, (key,), game=TORTOISE.id)

    _said, raised = _return(made, server_dir)

    message = str(raised)
    assert after not in message
    assert (
        "Yu'lon found no copy of tw_world from before that migration in the server's backups"
        in (message)
    )
    latest = server_build_presses.under_server_build(server_build_presses.UPDATE_TO_LATEST)
    assert f"keep the build you have ({latest} keeps it current)" in message
    assert "Restoring loses" not in message


# -- the real TortoiseBots layout, the sed'd file, deleted-upstream (cold review) --------------


CHAR_NAME = "20260929100000_char.sql"
CHAR = b"ALTER TABLE `bot_state` ADD COLUMN `z` INT;\n"


def test_a_module_character_migration_is_read_from_the_folder_its_install_rule_names(
    tmp_path: Path,
) -> None:
    """TortoiseBots keeps `data/sql/char`; the image's `data/sql/character` is not in the repo."""
    rec, server_dir, made = _ready(tmp_path)
    rec.trees[(server_dir / BOTS.dest, OLD, BOTS_CHAR)] = {CHAR_NAME: CHAR}
    rec.migrations["tw_char"] = f"TortoiseBots:{_hash(CHAR)}\n"

    _said, raised = _return(made, server_dir)

    assert raised is not None, "a character migration slipped through because the folder differs"
    message = str(raised)
    assert (
        f"tw_char has 1 migration the tested commit does not have (TortoiseBots/{CHAR_NAME})"
        in (message)
    ), (message)
    _refused_clean(rec, server_dir, made._snapshot)  # type: ignore[attr-defined]


def test_the_install_rule_is_read_at_each_commit(tmp_path: Path) -> None:
    """The target's own rules decide where ITS files are; a rule the old commit lacks counts."""
    rec, server_dir, made = _ready(tmp_path)
    bots = server_dir / BOTS.dest
    rec.trees[(bots, OLD, BOTS_CHAR)] = {CHAR_NAME: CHAR}
    rec.migrations["tw_char"] = f"TortoiseBots:{_hash(CHAR)}\n"
    # The target ships the same bytes, but its rules install nothing from `char`.
    rec.trees[(bots, BOTS_PIN, BOTS_CHAR)] = {CHAR_NAME: CHAR}
    rec.blobs[(bots, BOTS_PIN, BOTS_CMAKE)] = CMAKE.split(
        b'if(EXISTS "${TORTOISEBOTS_ROOT}/data/sql/char")'
    )[0]

    _said, raised = _return(made, server_dir)

    assert raised is not None and "tw_char has 1 migration" in str(raised)


@pytest.mark.parametrize("rev", ["old", "target"])
def test_a_module_with_sql_and_no_install_rule_refuses(tmp_path: Path, rev: str) -> None:
    rec, server_dir, made = _ready(tmp_path)
    bots = server_dir / BOTS.dest
    at = OLD if rev == "old" else BOTS_PIN
    rec.blobs[(bots, at, BOTS_CMAKE)] = b"# nothing installs data/sql here\n"
    rec.trees[(bots, at, BOTS_CHAR)] = {CHAR_NAME: CHAR}

    _said, raised = _return(made, server_dir)

    assert raised is not None
    assert "TortoiseBots.cmake at" in str(raised) and "does not say where" in str(raised)
    _refused_clean(rec, server_dir, made._snapshot)  # type: ignore[attr-defined]


def test_a_module_cmake_git_cannot_read_refuses(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    del rec.blobs[(server_dir / BOTS.dest, OLD, BOTS_CMAKE)]
    rec.trees[(server_dir / BOTS.dest, OLD, BOTS_CMAKE)] = None

    _said, raised = _return(made, server_dir)

    assert raised is not None and "git could not list TortoiseBots.cmake" in str(raised)


def test_a_database_the_check_started_goes_down_again_when_nothing_was_applied(
    tmp_path: Path,
) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir, applied=False)
    rec.migrations["tw_world"] = f":{_hash(OLDER)}\n"
    rec.db_up = False

    said, raised = _return(made, server_dir)

    assert raised is None, raised
    assert any(call.startswith("stop-db:") for call in rec.calls), rec.calls
    assert any("None of them was applied" in line for line in said)


def test_a_database_that_was_up_stays_up_when_nothing_was_applied(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir, applied=False)
    rec.migrations["tw_world"] = f":{_hash(OLDER)}\n"
    rec.db_up = True

    _said, raised = _return(made, server_dir)

    assert raised is None, raised
    assert not any(call.startswith("stop-db:") for call in rec.calls), rec.calls


def test_a_migration_upstream_deleted_is_older_than_the_target_not_newer(tmp_path: Path) -> None:
    """A forward Return over a squash: the old commit's file is gone from the target, applied long ago."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    gone = b"-- deleted upstream\n"
    rec.trees[(dest, OLD, CORE_WORLD)] = {OLDER_NAME: OLDER, "20260601000000_world.sql": gone}
    rec.trees[(dest, CORE_PIN, CORE_WORLD)] = {OLDER_NAME: OLDER, "20261101000000_world.sql": b"x"}
    rec.migrations["tw_world"] = f":{_hash(gone)}\n:{_hash(OLDER)}\n"

    said, raised = _return(made, server_dir)

    assert raised is None, raised
    assert not _asked(rec), "an old migration upstream deleted was asked about as if it were newer"


def test_a_deleted_migration_that_sorts_after_the_target_is_still_newer(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    key = _core_newer(rec, server_dir)
    rec.trees[(server_dir / CORE.dest, CORE_PIN, CORE_WORLD)] = {OLDER_NAME: OLDER}

    _said, raised = _return(made, server_dir)

    assert raised is not None, key


def test_an_undated_deleted_migration_is_never_skipped_for_sorting(tmp_path: Path) -> None:
    """The date shortcut needs both names to follow the dated pattern."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    odd = b"-- odd name\n"
    rec.trees[(dest, OLD, CORE_WORLD)] = {"0aaa_odd.sql": odd}
    rec.trees[(dest, CORE_PIN, CORE_WORLD)] = {OLDER_NAME: OLDER}
    rec.migrations["tw_world"] = f":{_hash(odd)}\n"

    _said, raised = _return(made, server_dir)

    assert raised is not None and "0aaa_odd.sql" in str(raised)


def test_the_world_file_the_image_rewrites_is_asked_by_its_rewritten_hash(tmp_path: Path) -> None:
    """The Dockerfile's `sed` rewrites `INSERT INTO` to `INSERT IGNORE INTO`; the database holds that."""
    rec, server_dir, made = _ready(tmp_path)
    dest = server_dir / CORE.dest
    original = b"INSERT INTO `t` VALUES (7);\n  INSERT INTO `u` VALUES (8);\n"
    rewritten = b"INSERT IGNORE INTO `t` VALUES (7);\n  INSERT IGNORE INTO `u` VALUES (8);\n"
    rec.trees[(dest, OLD, CORE_WORLD)] = {"20260903063722_world.sql": original}
    rec.trees[(dest, CORE_PIN, CORE_WORLD)] = {}
    rec.migrations["tw_world"] = f":{_hash(rewritten)}\n"

    _said, raised = _return(made, server_dir)

    assert raised is not None, "the rewritten copy's hash was never asked about"
    assert (
        "tw_world has 1 migration the tested commit does not have (20260903063722_world.sql)"
        in (str(raised))
    )


# -- when it is not asked -------------------------------------------------------------------


def test_an_update_to_latest_is_not_asked(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)

    _said, raised = _return(made, server_dir, to_pin=False)

    assert raised is None, raised
    assert not any(call.startswith("tree-files:") for call in rec.calls)
    assert not _asked(rec)


def test_a_return_that_moves_nothing_asks_nothing(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    for source in TORTOISE.emulator.sources:
        rec.heads[server_dir / source.dest] = source.rev or ""
        rec.upstream[server_dir / source.dest] = source.rev or ""

    _said, _raised = _return(made, server_dir)

    assert not _asked(rec)


def test_a_family_whose_conf_has_no_auto_updater_is_not_asked(tmp_path: Path) -> None:
    """TBC's `refuse_new` already refuses a move that touches core migrations."""
    rec, server_dir, made = _cmangos(tmp_path, TBC)
    _said, raised = _return(made, server_dir)
    assert not any(call.startswith("tree-files:") for call in rec.calls)
    assert "database migrations" not in str(raised), raised


def test_a_moved_checkout_git_cannot_name_refuses(tmp_path: Path) -> None:
    from dataclasses import replace

    rec, server_dir, made = _ready(tmp_path)
    core = server_dir / CORE.dest

    def head_sha(dest: Path) -> str | None:
        if dest == core and rec.heads.get(dest) == CORE_PIN:
            return None
        return rec.head_sha(dest)

    made._seams = replace(made._seams, head_sha=head_sha)

    _said, raised = _return(made, server_dir)

    assert raised is not None
    assert "git did not say its commit" in str(raised)
    assert not _built(rec)


def test_a_copy_with_no_migrations_table_is_not_a_copy_to_name(tmp_path: Path) -> None:
    rec, server_dir, made = _ready(tmp_path)
    _core_newer(rec, server_dir)
    path = _backups(server_dir) / "20261008_120000_tw_world.sql"
    _dump(path, (f":{_hash(OLDER)}",))
    path.write_text(path.read_text().replace("`migrations`", "`other`"), encoding="utf-8")

    _said, raised = _return(made, server_dir)

    assert "20261008_120000" not in str(raised)


# -- git and the tar --------------------------------------------------------------------------


def _tar(files: dict[str, bytes]) -> bytes:
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_the_tar_is_read_byte_for_byte() -> None:
    crlf = b"SELECT 1;\r\nSELECT 2;\r\n"
    raw = _tar({"sql/u/a.sql": crlf, "sql/u/sub/c.sql": b"nested"})
    assert git.parse_tree_files(raw) == {"sql/u/a.sql": crlf, "sql/u/sub/c.sql": b"nested"}
    assert git.parse_tree_files(b"not a tar") is None


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
    )


def test_real_git_lists_a_commits_files_not_the_disks(tmp_path: Path) -> None:
    repo = tmp_path / "t632-repo"
    folder = repo / "sql" / "u"
    folder.mkdir(parents=True)
    (folder / "a.sql").write_bytes(b"one\r\n")
    _git(repo.parent, "init", "-q", str(repo))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "one")
    first = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()
    (folder / "b.sql").write_bytes(b"untracked")
    (folder / "a.sql").write_bytes(b"edited on disk")

    impl = git.RunnerGit()
    assert impl.tree_files(repo, first, "sql/u") == {"sql/u/a.sql": b"one\r\n"}
    assert impl.tree_files(repo, first, "sql/u/a.sql") == {"sql/u/a.sql": b"one\r\n"}
    assert impl.tree_files(repo, first, "sql/none") == {}
    assert impl.tree_files(repo, "f" * 40, "sql/u") is None
    assert impl.tree_files(tmp_path, first, "sql/u") is None
