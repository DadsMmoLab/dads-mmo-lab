"""T564: installing NPC Teleporter keeps the two base-game NPCs' gossip menus.

Upstream (Zoidwaffle/sql-npc-teleporter @06e5242) builds its menus on ids 50000..50009
(`teleporter_capital.dist`) and 50009 (`teleporter_starting_zone.dist`), DELETEs those ids
first, and so removes 19 rows of the base game: the Searing Gorge gate menus of Mountaineer
Pebblebitty (creature 3836, menu 50000) and Maggran Earthbinder (11860, menu 50008). The two
files also collide with each other on 50009 (the capital's battleground menu is deleted by
the starting-zone file).

Yu'lon cannot edit the clone (a changed clone is a refused Update), so, as for T104's
`@ONY_LEVEL`, the manifest runs each file unmodified and corrects the database right after
it: it moves the teleporter's menus to 60000..60010 (capital 60000..60009, starting zone
60010) with every reference to them, and puts the 19 base rows back.

These tests run the manifest's own install and remove statements, in order, against a
SQLite model of the tables. The `.dist` files are not in this repository, so `_run_dist`
reproduces their DELETE blocks verbatim (`test_npc_teleporter_remove._DIST_DELETES`) and
the SHAPE of their INSERTs (which menus, which options point at which menu, which scripts
listen on which menu), read off @06e5242. A model proves the statements move what the model
holds; the live check on a real WotLK database is in the ticket.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
import sqlite3

import pytest

from tests.test_npc_teleporter_remove import _BASE_ROWS, _DIST_DELETES, _DIST_VARS, _steps
from yulon.catalog.catalog import load_catalog

NEW_FIRST, NEW_LAST = 60000, 60010
CAPITAL_FIRST, ZONE_MENU = 60000, 60010

PINS_MEASURED = (
    "7f12e89ee5f467a50e62eba1d525eac7dc953d03",
    "f19a18799",  # prefix: T389's pin, mod-playerbots/azerothcore-wotlk f19a1879
)
USED_AT_BOTH_PINS = (
    (50000, 50008),
    (51000, 51003),
    (51005, 51013),
    (54000, 54000),
    (55000, 55000),
    (55002, 55002),
    (56000, 56002),
    (57000, 57012),
    (57014, 57032),
    (61021, 61021),
    (61023, 61023),
    (61025, 61026),
    (61028, 61030),
    (65536, 65536),
)
"""Every id in 49990..70000 that mod-playerbots/azerothcore-wotlk's db_world uses as a gossip
menu: `gossip_menu.MenuID`, `gossip_menu_option.MenuID` and `.ActionMenuID`, `conditions.SourceGroup`
of types 14 and 15, `smart_scripts.event_param1` of event 62 (and the menu of action 98),
`creature_template.gossip_menu_id`. Measured 2026-10-08 over base SQL plus every db_world
update, at 7f12e89e AND at f19a1879: the two sets are identical. Unbound's Mentor (900001)
carries `gossip_menu_id` 0 and talks through Lua. 58000..60999 and 62000..89999 are unused.
"""

_ONY = 80

_TABLES = """
CREATE TABLE gossip_menu (MenuID INT, TextID INT, PRIMARY KEY (MenuID, TextID));
CREATE TABLE gossip_menu_option (MenuID INT, OptionID INT, OptionIcon INT, OptionText TEXT,
  OptionBroadcastTextID INT, OptionType INT, OptionNpcFlag INT, ActionMenuID INT, ActionPoiID INT,
  BoxCoded INT, BoxMoney INT, BoxText TEXT, BoxBroadcastTextID INT, VerifiedBuild INT,
  PRIMARY KEY (MenuID, OptionID));
CREATE TABLE conditions (SourceTypeOrReferenceId INT, SourceGroup INT, SourceEntry INT, SourceId INT,
  ElseGroup INT, ConditionTypeOrReference INT, ConditionTarget INT, ConditionValue1 INT,
  ConditionValue2 INT, ConditionValue3 INT, NegativeCondition INT, ErrorType INT, ErrorTextId INT,
  ScriptName TEXT, Comment TEXT,
  PRIMARY KEY (SourceTypeOrReferenceId, SourceGroup, SourceEntry, SourceId, ElseGroup,
    ConditionTypeOrReference, ConditionTarget, ConditionValue1, ConditionValue2, ConditionValue3));
CREATE TABLE smart_scripts (entryorguid INT, source_type INT, id INT, link INT, event_type INT,
  event_param1 INT, PRIMARY KEY (entryorguid, source_type, id, link));
CREATE TABLE creature_template (entry INT PRIMARY KEY, gossip_menu_id INT);
CREATE TABLE creature_template_model (CreatureID INT);
CREATE TABLE creature_template_addon (entry INT);
CREATE TABLE npc_text (ID INT);
CREATE TABLE creature (id INT);
CREATE TABLE gameobject (id INT, guid INT);
"""

_WATCHED = (
    "gossip_menu",
    "gossip_menu_option",
    "conditions",
    "smart_scripts",
    "creature_template",
)


def _sqlite(sql: str) -> str:
    """MySQL's `\\'` inside a string is SQLite's `''`."""
    return sql.replace("\\'", "''")


def _base_world() -> sqlite3.Connection:
    """A world holding what the pin's base SQL holds for the two NPCs: 9 + 8 + 2 rows."""
    con = sqlite3.connect(":memory:")
    con.executescript(_TABLES)
    for table, rows in _BASE_ROWS.items():
        con.executescript(_sqlite(f"INSERT INTO {table} VALUES " + ",".join(rows) + ";"))
    con.executescript(
        "INSERT INTO creature_template VALUES (3836, 50000), (11860, 50008), (190, 0);"
        "INSERT INTO smart_scripts VALUES (3836, 0, 1, 0, 62, 50006);"
    )
    return con


def _substituted(file: str) -> str:
    variables = _DIST_VARS[file]

    def value(m: re.Match[str]) -> str:
        return str(variables[m.group(1)] + int(m.group(2) or 0))

    return re.sub(r"(@\w+)(?:\+(\d+))?", value, _DIST_DELETES[file])


def _condition(group: int, entry: int, kind: int, value: int, source: int = 15) -> str:
    return f"({source},{group},{entry},0,0,{kind},0,{value},0,0,0,0,0,'','x')"


def _run_dist(con: sqlite3.Connection, file: str) -> None:
    """What `teleporter_<file>.dist` does: its DELETE block, then its INSERTs (shape only)."""
    con.executescript(_substituted(file))
    if file == "capital":
        menus = range(50000, 50010)
        con.executescript(
            "INSERT INTO gossip_menu VALUES "
            + ",".join(f"({m},{300001 + m % 3})" for m in menus)
            + ",(50000,300000);"
        )
        options = [f"(50000,{i},2,'Go {i}',0,1,1,{50000 + i},0,0,0,'',0,0)" for i in range(1, 10)]
        options += [f"(50000,{i + 20},2,'Spot {i}',0,1,1,0,0,0,0,'',0,0)" for i in range(3)]
        for m in range(50001, 50010):
            options += [f"({m},{i},2,'Spot {i}',0,1,1,0,0,0,0,'',0,0)" for i in range(3)]
            options.append(f"({m},99,0,'Back',0,1,1,50000,0,0,0,'',0,0)")
        con.executescript("INSERT INTO gossip_menu_option VALUES " + ",".join(options) + ";")
        conds = [_condition(m, i, 6, 469) for m in range(50001, 50010) for i in range(3)]
        conds.append("(15,50004,11,0,0,27,0,60,3,0,0,0,0,'','Portal Master - Level req')")
        conds.append(_condition(50000, 300001, 6, 469, source=14))
        con.executescript("INSERT INTO conditions VALUES " + ",".join(conds) + ";")
        scripts = [f"(190000,0,{m - 49999},0,62,{m})" for m in menus]
        con.executescript("INSERT INTO smart_scripts VALUES " + ",".join(scripts) + ";")
        con.executescript("INSERT INTO creature_template VALUES (190000, 50000);")
    else:
        con.executescript("INSERT INTO gossip_menu VALUES (50009,300005);")
        options = [f"(50009,{i},2,'Zone {i}',0,1,1,50009,0,0,0,'',0,0)" for i in range(1, 9)]
        con.executescript("INSERT INTO gossip_menu_option VALUES " + ",".join(options) + ";")
        conds = [_condition(50009, i, 6, 469) for i in range(1, 9)]
        con.executescript("INSERT INTO conditions VALUES " + ",".join(conds) + ";")
        scripts = [f"(190001,0,{i},0,62,50009)" for i in range(1, 9)]
        con.executescript("INSERT INTO smart_scripts VALUES " + ",".join(scripts) + ";")
        con.executescript("INSERT INTO creature_template VALUES (190001, 50009);")


def _install(con: sqlite3.Connection) -> None:
    """The manifest's install steps, in its order."""
    for step in _steps():
        if step.get("when", "install") != "install":
            continue
        if "path" in step:
            _run_dist(con, "capital" if "capital" in step["path"] else "starting_zone")
        else:
            con.executescript(_sqlite(step["statement"].replace("{ony_level}", str(_ONY))))


def _old_install(con: sqlite3.Connection) -> None:
    """What this app did before T564: the two files, nothing between."""
    _run_dist(con, "capital")
    _run_dist(con, "starting_zone")


def _remove(con: sqlite3.Connection) -> None:
    for step in _steps():
        if step.get("when") == "remove":
            con.executescript(_sqlite(step["statement"]))


def _state(con: sqlite3.Connection) -> dict[str, list[tuple[object, ...]]]:
    return {t: sorted(con.execute(f"SELECT * FROM {t}").fetchall(), key=repr) for t in _WATCHED}


def _installed() -> sqlite3.Connection:
    con = _base_world()
    _install(con)
    return con


def _base_state() -> dict[str, list[tuple[object, ...]]]:
    return _state(_base_world())


def _menu_ids(con: sqlite3.Connection, table: str, column: str) -> set[int]:
    return {r[0] for r in con.execute(f"SELECT {column} FROM {table}")}


# --------------------------------------------------------------------------- the base rows


def test_the_model_reproduces_the_defect_without_the_fix() -> None:
    """The reader's own guard: the old install (two files, no correction) loses all 19 rows."""
    con = _base_world()
    _old_install(con)
    assert con.execute(
        "SELECT COUNT(*) FROM gossip_menu WHERE MenuID BETWEEN 50000 AND 50008 AND TextID IN (1833,1834,1835,1836,1837,1838,1839,5443)"
    ).fetchone() == (0,)
    assert con.execute(
        "SELECT COUNT(*) FROM conditions WHERE SourceGroup = 50000 AND ConditionValue1 IN (3181, 3201)"
    ).fetchone() == (0,)
    assert con.execute(
        "SELECT COUNT(*) FROM gossip_menu_option WHERE OptionText LIKE 'Open the gate%'"
    ).fetchone() == (0,)
    # and the two files collide on 50009: one menu holds the zone's options, the capital's are gone
    assert con.execute(
        "SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = 50009"
    ).fetchone() == (8,)


def test_install_leaves_the_19_base_game_rows_as_the_pin_has_them() -> None:
    con = _installed()
    for table, rows in _BASE_ROWS.items():
        got = sorted(
            con.execute(
                f"SELECT * FROM {table} WHERE "
                + ("SourceGroup" if table == "conditions" else "MenuID")
                + " BETWEEN 50000 AND 50017"
            ).fetchall(),
            key=repr,
        )
        want = sorted(
            _base_world()
            .execute(
                f"SELECT * FROM {table} WHERE "
                + ("SourceGroup" if table == "conditions" else "MenuID")
                + " BETWEEN 50000 AND 50017"
            )
            .fetchall(),
            key=repr,
        )
        assert got == want, table
        assert len(got) == len(rows), table


def test_the_two_npcs_keep_their_menu_and_their_script() -> None:
    con = _installed()
    assert con.execute(
        "SELECT gossip_menu_id FROM creature_template WHERE entry IN (3836, 11860) ORDER BY entry"
    ).fetchall() == [(50000,), (50008,)]
    assert con.execute(
        "SELECT event_param1 FROM smart_scripts WHERE entryorguid = 3836"
    ).fetchall() == [(50006,)]
    assert con.execute(
        "SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = 50000"
    ).fetchone() == (2,)


def test_nothing_of_the_teleporter_is_left_on_a_base_game_id() -> None:
    """Between 50000 and 50017 the world holds exactly what the base game put there."""
    con = _installed()
    base = _base_world()
    for table, column in (
        ("gossip_menu", "MenuID"),
        ("gossip_menu_option", "MenuID"),
        ("conditions", "SourceGroup"),
    ):
        sql = f"SELECT * FROM {table} WHERE {column} BETWEEN 50000 AND 50017"
        assert sorted(con.execute(sql).fetchall(), key=repr) == sorted(
            base.execute(sql).fetchall(), key=repr
        ), table


# --------------------------------------------------------------------------- the new range


def test_the_teleporters_menus_land_in_the_new_range() -> None:
    con = _installed()
    ours = _menu_ids(con, "gossip_menu", "MenuID") - set(range(50000, 50018))
    assert ours == set(range(NEW_FIRST, NEW_LAST + 1))
    assert _menu_ids(con, "gossip_menu_option", "MenuID") - set(range(50000, 50018)) == ours
    assert con.execute(
        "SELECT gossip_menu_id FROM creature_template WHERE entry IN (190000, 190001) ORDER BY entry"
    ).fetchall() == [(CAPITAL_FIRST,), (ZONE_MENU,)]


def test_every_reference_to_a_teleporter_menu_follows_it() -> None:
    con = _installed()
    capital = {
        r[0]
        for r in con.execute("SELECT event_param1 FROM smart_scripts WHERE entryorguid = 190000")
    }
    zone = {
        r[0]
        for r in con.execute("SELECT event_param1 FROM smart_scripts WHERE entryorguid = 190001")
    }
    assert capital == set(range(60000, 60010))
    assert zone == {ZONE_MENU}
    # an option's "open this menu" target is a new id (or none), never an old one
    actions = {
        r[0]
        for r in con.execute(
            "SELECT ActionMenuID FROM gossip_menu_option WHERE MenuID >= 60000 AND ActionMenuID <> 0"
        )
    }
    assert actions == set(range(60000, 60010)) | {ZONE_MENU}
    assert con.execute(
        "SELECT COUNT(*) FROM conditions WHERE SourceGroup BETWEEN 50001 AND 50017"
    ).fetchone() == (0,)
    assert con.execute(
        "SELECT COUNT(*) FROM conditions WHERE SourceGroup >= 60000 AND SourceTypeOrReferenceId IN (14, 15)"
    ).fetchone() == (len(range(1, 10)) * 3 + 2 + 8,)


def test_the_capitals_battleground_menu_and_the_zone_menu_no_longer_share_an_id() -> None:
    con = _installed()
    assert con.execute(
        "SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = 60009"
    ).fetchone() == (4,)
    assert con.execute(
        "SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = 60010"
    ).fetchone() == (8,)
    assert con.execute(
        "SELECT DISTINCT ActionMenuID FROM gossip_menu_option WHERE MenuID = 60010"
    ).fetchall() == [(60010,)]


def test_the_onyxia_answer_is_written_into_the_row_at_its_new_id() -> None:
    con = _installed()
    assert con.execute(
        "SELECT ConditionValue1 FROM conditions WHERE SourceTypeOrReferenceId = 15 AND SourceGroup = 60004 AND SourceEntry = 11"
    ).fetchall() == [(_ONY,)]


def test_a_second_install_changes_nothing() -> None:
    con = _installed()
    first = _state(con)
    _install(con)
    assert _state(con) == first


def test_an_install_over_one_made_before_the_fix_gives_the_base_rows_back() -> None:
    """The repair: a world the old install damaged is whole after the next Install."""
    damaged = _base_world()
    _old_install(damaged)
    _install(damaged)
    assert _state(damaged) == _state(_installed())


# --------------------------------------------------------------------------- remove


def test_remove_after_install_leaves_the_world_as_it_was() -> None:
    con = _installed()
    _remove(con)
    assert _state(con) == _base_state()


def test_remove_after_an_install_made_before_the_fix_still_leaves_the_base_game_whole() -> None:
    con = _base_world()
    _old_install(con)
    _remove(con)
    assert _state(con) == _base_state()


def test_remove_clears_the_new_range() -> None:
    con = _installed()
    _remove(con)
    for table, column in (
        ("gossip_menu", "MenuID"),
        ("gossip_menu_option", "MenuID"),
        ("conditions", "SourceGroup"),
    ):
        assert con.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} BETWEEN 60000 AND 60010"
        ).fetchone() == (0,), table


# --------------------------------------------------------------------------- the range is free


def _ids(ranges: tuple[tuple[int, int], ...]) -> set[int]:
    return {i for lo, hi in ranges for i in range(lo, hi + 1)}


def test_the_new_range_is_free_in_the_base_game_at_both_pins() -> None:
    assert not _ids(USED_AT_BOTH_PINS) & set(range(NEW_FIRST, NEW_LAST + 1))
    assert _ids(USED_AT_BOTH_PINS) >= set(range(50000, 50009))  # the reader sees Pebblebitty's


def test_the_snapshot_covers_the_pin_the_catalog_ships() -> None:
    """Moving the pin to a revision this was not measured at fails here: measure it again."""
    core = load_catalog().get("wow-wotlk").emulator.sources[0]
    assert any(core.rev.startswith(p) for p in PINS_MEASURED), (
        f"the WotLK pin moved to {core.rev}: re-run the gossip menu id scan over its base SQL "
        "and db_world updates, and re-check that 60000..60010 is still unused"
    )


def test_the_manifest_moves_to_the_range_the_tests_assume() -> None:
    text = " ".join(
        s.get("statement", "") for s in _steps() if s.get("when", "install") == "install"
    )
    found = {int(n) for n in re.findall(r"\b6\d{4}\b", text)}
    assert found and found <= set(range(NEW_FIRST, NEW_LAST + 1))


@pytest.mark.parametrize("table", ["gossip_menu", "gossip_menu_option"])
def test_the_install_corrections_never_delete_below_the_new_range(table: str) -> None:
    """The only DELETEs the install's own statements run are on the new range."""
    for step in _steps():
        if step.get("when", "install") == "install" and "statement" in step:
            for found in re.findall(
                rf"DELETE\s+FROM\s+{table}\s+WHERE\s+MenuID\s+BETWEEN\s+(\d+)\s+AND\s+(\d+)",
                step["statement"],
                re.I,
            ):
                assert (int(found[0]), int(found[1])) == (NEW_FIRST, NEW_LAST)
