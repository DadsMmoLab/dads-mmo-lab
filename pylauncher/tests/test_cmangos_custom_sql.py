"""T659: the db repo's `utilities/cmangos_custom.sql`, which upstream's InstallFullDB.sh applies.

tbc-db and classic-db ship a file of known data corrections (the spell no-stack list, GM-only
traps, flags the core does not handle yet). Upstream's `apply_full_content_db` runs it LAST,
after the dbc data and ACID, on every full install, with no option to leave it out. Yu'lon's plans
never ran it. Now a fresh install applies it after the core updates, and an update applies it
to an existing server (once, and again whenever upstream changes the file) because every
statement in it is safe to repeat.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import Recorder
from tests.test_families_cmangos import install
from tests.test_world_content_updates import (  # noqa: F401 - `_gated` is an autouse fixture
    TBC_DB,
    _gated,
    _installed,
    _lay,
    _press,
)
from yulon.catalog import native
from yulon.catalog.catalog import SqlPhase, load_catalog
from yulon.catalog.families import sqlplan


def _sent(rec: Recorder, rel: str) -> int:
    return rec.sql_calls.count(f"-- {rel}")


PHASE = "cmangos custom"
FILE = f"{TBC_DB.dest}/utilities/cmangos_custom.sql"
DATA = Path(__file__).parent / "data" / "cmangos-custom-sql"


def _plan(game: str) -> tuple[list[SqlPhase], str, str]:
    entry = load_catalog().get(game)
    assert entry.install.native is not None and entry.install.native.cmangos is not None
    assert entry.databases is not None
    db = next(s.dest for s in entry.emulator.sources if s.repo.endswith("-db"))
    return list(entry.install.native.cmangos.sql.phases), db, entry.databases.world


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla"])
def test_both_plans_apply_the_custom_file_where_upstream_does(game: str) -> None:
    """After everything upstream runs before it (core updates, dbc, ACID), into the world."""
    phases, db, world = _plan(game)
    names = [p.name for p in phases]
    assert names.count(PHASE) == 1, names
    phase = phases[names.index(PHASE)]
    assert phase.files == (f"{db}/utilities/cmangos_custom.sql",)
    assert phase.into == world
    assert phase.on_update == "reapply_changed"
    assert phase.on_error == "warn"
    assert not phase.rerun_on_marked and not phase.reapply_when_changed
    for before in ("content updates", "instance updates", "ACID", "dbc data", "core updates"):
        if before in names:
            assert names.index(before) < names.index(PHASE), before


def test_reapply_changed_needs_files_into_one_schema() -> None:
    base: dict[str, object] = {
        "name": PHASE,
        "into": "mangos",
        "files": ("src/x-db/utilities/cmangos_custom.sql",),
        "on_update": "reapply_changed",
    }
    assert SqlPhase.model_validate(base).on_update == "reapply_changed"
    with pytest.raises(ValueError, match="on_update"):
        SqlPhase.model_validate({**base, "files": (), "statements": ("SELECT 1",)})
    with pytest.raises(ValueError, match="on_update"):
        SqlPhase.model_validate({**base, "rerun_on_marked": True})


# -- the repeat guard -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("UPDATE t SET a=1 WHERE id=2;", None),
        ("UPDATE `t` SET `a`=`a`|0x00000800 WHERE id IN (1,2);", None),
        ("UPDATE t SET a=a|640, b=65536*6 WHERE id=1;", None),
        ("UPDATE t SET QuestFlags=`QuestFlags`&~1 WHERE id=1;", None),
        ("UPDATE t SET a='it''s, ok' WHERE id=1;", None),
        ("DELETE FROM t WHERE id=1; INSERT INTO t (id) VALUES (1);", None),
        ("INSERT INTO t (id) VALUES (1);", "without emptying"),
        ("UPDATE t SET a=a+1 WHERE id=1;", "not safe to repeat"),
        ("UPDATE t SET a=b WHERE id=1;", "not safe to repeat"),
        ("UPDATE t SET a=a|1, b=a WHERE id=1;", "not safe to repeat"),
        ("UPDATE t SET a=(SELECT 1) WHERE id=1;", "not safe to repeat"),
        ("UPDATE t SET a=1 WHERE id IN (SELECT id FROM u);", "not safe to repeat"),
        ("UPDATE t, u SET t.a=1 WHERE t.id=u.id;", "more than one table"),
        ("ALTER TABLE t ADD COLUMN x INT;", "not safe to repeat"),
        ("DELETE FROM t WHERE id=1 LIMIT 1;", "not safe to repeat"),
    ],
)
def test_the_repeat_guard(tmp_path: Path, text: str, problem: str | None) -> None:
    path = _lay(tmp_path, "x.sql", text)
    found = sqlplan.repeat_problem(path)
    if problem is None:
        assert found is None, found
    else:
        assert found is not None and problem in found, found


def test_the_whole_table_guard_stays_strict(tmp_path: Path) -> None:
    """A bot table reloads whole: an UPDATE of a table nothing emptied is still refused there."""
    path = _lay(tmp_path, "x.sql", "UPDATE t SET a=1 WHERE id=2;")
    assert sqlplan.whole_table_problem(path) is not None


@pytest.mark.parametrize("name", ["tbc-db-ebe51a83.sql", "classic-db-ec4f5961.sql"])
def test_the_real_pinned_files_are_ones_the_update_will_run(name: str) -> None:
    path = DATA / name
    assert sqlplan.repeat_problem(path) is None
    assert sqlplan.foreign_schemas(path, {"characters", "realmd", "logs"}) == ()


# -- an update -------------------------------------------------------------------------


def test_an_update_applies_the_file_once_and_again_only_when_upstream_changes_it(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    _lay(server_dir, FILE, f"-- {FILE}\nUPDATE t SET a=1 WHERE id=1;\n")
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, FILE) == 1
    assert db.rows[(PHASE, FILE)][1] == "applied"
    assert any(PHASE in line or "cmangos_custom" in line for line in lines), lines

    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, FILE) == 0, "unchanged: not again"

    bigger = f"-- {FILE}\nUPDATE t SET a=1 WHERE id=1;\nUPDATE t SET a=2 WHERE id=2;\n"
    _lay(server_dir, FILE, bigger)
    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, FILE) == 1, "upstream changed it: applied again"


def test_the_custom_file_goes_in_after_the_content_updates_it_may_depend_on(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    _lay(server_dir, FILE, f"-- {FILE}\nUPDATE t SET a=1 WHERE id=1;\n")
    _press(rec, server_dir, db, world)
    calls = rec.sql_calls
    assert calls.index("-- src/tbc-db/Updates/0003_new_at_the_pin.sql") < calls.index(f"-- {FILE}")


def test_a_file_that_is_not_safe_to_repeat_is_named_and_not_run(tmp_path: Path) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    _lay(server_dir, FILE, f"-- {FILE}\nUPDATE t SET a=a+1 WHERE id=1;\n")
    lines = _press(rec, server_dir, db, world)
    assert _sent(rec, FILE) == 0
    assert any("cmangos_custom.sql" in line and "not safe" in line for line in lines), lines
    assert (PHASE, FILE) not in db.rows


def test_a_file_the_database_refuses_is_failed_and_tried_again_next_time(
    tmp_path: Path,
) -> None:
    rec, server_dir, db, world = _installed(tmp_path)
    _lay(server_dir, FILE, f"-- {FILE}\nUPDATE t SET a=1 WHERE id=1;\n")
    rec.failing_sql = "UPDATE t SET a=1"
    lines = _press(rec, server_dir, db, world)
    assert db.rows[(PHASE, FILE)][1] == "failed"
    assert any("refused" in line for line in lines), lines
    rec.failing_sql = ""
    rec.sql_calls.clear()
    _press(rec, server_dir, db, world)
    assert _sent(rec, FILE) == 1
    assert db.rows[(PHASE, FILE)][1] == "applied"


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla"])
def test_a_fresh_install_applies_it_after_the_core_updates_and_before_the_bots(
    tmp_path: Path, game: str
) -> None:
    entry = load_catalog().get(game)
    rec = Recorder()
    install(rec, tmp_path / "srv", tmp_path / "client", entry=entry)
    db = next(s.dest for s in entry.emulator.sources if s.repo.endswith("-db"))
    custom = f"-- {db}/utilities/cmangos_custom.sql"
    calls = rec.sql_calls
    assert calls.count(custom) == 1, "applied by the import itself, once"
    core = [c for c in calls if "/sql/updates/mangos/" in c]
    assert core and calls.index(custom) > max(calls.index(c) for c in core)
    bots = [c for c in calls if "/Bots/sql/world/" in c]
    assert bots and calls.index(custom) < min(calls.index(c) for c in bots)


@pytest.mark.parametrize(
    ("game", "says"),
    [("wow-tbc", True), ("wow-vanilla", True), ("wow-tortoise", False), ("wow-wotlk", False)],
)
def test_both_confirmations_say_the_data_corrections_file_is_applied(game: str, says: bool) -> None:
    entry = load_catalog().get(game)
    for text in (
        native.update_to_latest_confirmation(entry, Path("/srv"), "x/y"),
        native.return_to_pin_confirmation(entry, Path("/srv"), "x/y"),
    ):
        assert ("data corrections file" in text) is says, (game, text)
