"""T560: NPC Teleporter's Remove leaves the world database as the install found it.

The install (`teleporter_capital.dist`, `teleporter_starting_zone.dist`, upstream
Zoidwaffle/sql-npc-teleporter @06e5242) DELETEs a range of ids in nine tables and then
INSERTs its own rows. Remove must delete the same ranges, or its menus, texts, scripts and
models stay in the world (T560).

But the capital file's `@GOSSIP_MENU := 50000` range also holds nine rows of the BASE GAME at the
WotLK pin 7f12e89e (`data/sql/base/db_world/*.sql`; no db_world update there touches them):
the Searing Gorge gate menus of Mountaineer Pebblebitty (creature 3836, gossip_menu_id 50000)
and Maggran Earthbinder (creature 11860, gossip_menu_id 50008). The install deletes them
(upstream defect, T564), so Remove puts those 19 rows back, copied from the pin's base SQL.
Nothing else in the removed ranges exists in the base SQL at the pin (measured 2026-10-08:
npc_text 300000-300009, smart_scripts 190000/190001, creature_template 190000/190001, creature,
gossip_menu/_option 50009-50017 and conditions 14/15 in 50009-50017 have no base rows; the
rune gameobject 194394 has one at guid 100496, below the install's `guid >= 200000`).
"""

# ruff: noqa: E501
# (the .dist DELETE lines and the base-game rows are quoted verbatim, one per line)
from __future__ import annotations

import json
import re
from typing import cast

from yulon.resources import manifests_dir

# The .dist DELETE blocks, verbatim, with the SET variables they use (capital.dist lines 8-31,
# starting_zone.dist lines 5-42, upstream @06e5242).
_DIST_VARS = {
    "capital": {"@ENTRY": 190000, "@TEXT_ID": 300000, "@GOSSIP_MENU": 50000, "@RUNE": 194394},
    "starting_zone": {"@ENTRY": 190001, "@TEXT_ID": 300005, "@GOSSIP_MENU": 50009},
}
_DIST_DELETES = {
    "capital": """
DELETE FROM creature_template WHERE entry = @ENTRY;
DELETE FROM creature_template_model WHERE CreatureID = @ENTRY;
DELETE FROM creature_template_addon WHERE Entry = @ENTRY ;
DELETE FROM gossip_menu WHERE menuid BETWEEN @GOSSIP_MENU AND @GOSSIP_MENU+9;
DELETE FROM npc_text WHERE ID BETWEEN @TEXT_ID AND @TEXT_ID+5;
DELETE FROM gossip_menu_option WHERE menuid BETWEEN @GOSSIP_MENU AND @GOSSIP_MENU+9;
DELETE FROM smart_scripts WHERE entryorguid = @ENTRY AND source_type = 0;
DELETE FROM conditions WHERE (SourceTypeOrReferenceId = 15 OR SourceTypeOrReferenceId = 14) AND SourceGroup BETWEEN @GOSSIP_MENU AND @GOSSIP_MENU+9;
DELETE from creature WHERE id = @ENTRY;
DELETE from gameobject WHERE ID = @RUNE AND guid >= 200000;
""",
    "starting_zone": """
DELETE FROM creature_template WHERE entry = @ENTRY;
DELETE FROM creature_template_model WHERE CreatureID = @ENTRY;
DELETE FROM creature_template_addon WHERE Entry = @ENTRY ;
DELETE FROM gossip_menu WHERE menuid BETWEEN @GOSSIP_MENU AND @GOSSIP_MENU+8;
DELETE FROM npc_text WHERE ID BETWEEN @TEXT_ID AND @TEXT_ID+4;
DELETE FROM gossip_menu_option WHERE menuid = @GOSSIP_MENU;
DELETE FROM gossip_menu_option WHERE menuid BETWEEN @GOSSIP_MENU AND @GOSSIP_MENU+8;
DELETE FROM smart_scripts WHERE entryorguid = @ENTRY AND source_type = 0;
DELETE FROM conditions WHERE (SourceTypeOrReferenceId = 15 OR SourceTypeOrReferenceId = 14) AND SourceGroup BETWEEN @GOSSIP_MENU AND @GOSSIP_MENU+8;
DELETE from creature WHERE id = @ENTRY;
""",
}
# The column each table's ids are matched on, in the .dist spelling's case-insensitive form.
_KEYS = {
    "creature": "id",
    "creature_template": "entry",
    "creature_template_model": "creatureid",
    "creature_template_addon": "entry",
    "gossip_menu": "menuid",
    "gossip_menu_option": "menuid",
    "npc_text": "id",
    "smart_scripts": "entryorguid",
    "conditions": "sourcegroup",
    "gameobject": "id",
}

# Rows of the base game inside those ranges, copied byte for byte from the pin's base SQL
# (mod-playerbots/azerothcore-wotlk @7f12e89e, data/sql/base/db_world: gossip_menu.sql lines
# 6048-6056, gossip_menu_option.sql 4686-4693, conditions.sql 10967-10968). Moving the pin fails
# test_the_snapshot_is_of_the_catalogs_wotlk_pin in test_wotlk_world_columns.py first.
_BASE_ROWS = {
    "gossip_menu": (
        "(50000,1833)",
        "(50001,1833)",
        "(50002,1834)",
        "(50003,1835)",
        "(50004,1836)",
        "(50005,1837)",
        "(50006,1838)",
        "(50007,1839)",
        "(50008,5443)",
    ),
    "gossip_menu_option": (
        "(50000,0,0,'Open the gate please, I need to get to Searing Gorge',0,1,1,50001,0,0,0,NULL,0,0)",
        "(50000,1,0,'Umm... Pebblebitty... the gate is open.',0,1,1,50007,0,0,0,NULL,0,0)",
        "(50001,0,0,'But I need to get there, now open the gate!',0,1,1,50002,0,0,0,NULL,0,0)",
        "(50002,0,0,'Ok, so what is this other way?',0,1,1,50003,0,0,0,NULL,0,0)",
        "(50003,0,0,'Doesn\\'t matter, I\\'m invulnerable!',0,1,1,50004,0,0,0,NULL,0,0)",
        "(50004,0,0,'Yes...',0,1,1,50005,0,0,0,NULL,0,0)",
        "(50005,0,0,'Ok, I\\'ll try to remember that.',0,1,1,50006,0,0,0,NULL,0,0)",
        "(50006,0,0,'A key? Ok!',0,1,1,0,0,0,0,NULL,0,0)",
    ),
    "conditions": (
        "(15,50000,0,0,0,14,0,3181,0,0,0,0,0,'','Show Gossip 50000 if \\'The Horn of the Beast\\' is rewarded AND')",
        "(15,50000,1,0,0,8,0,3201,0,0,0,0,0,'','Show Gossip 50000  if \\'At Last!\\' is not taken')",
    ),
}


def _steps() -> list[dict[str, str]]:
    path = manifests_dir() / "wow-wotlk" / "mods" / "npc-teleporter.json"
    return cast("list[dict[str, str]]", json.loads(path.read_text(encoding="utf-8"))["sql"])


def _remove_text() -> str:
    return " ".join(s["statement"] for s in _steps() if s.get("when") == "remove")


def _remove_statements() -> list[str]:
    return [
        s.strip() for s in re.split(r";\s*(?=(?:DELETE|REPLACE)\b)", _remove_text()) if s.strip()
    ]


def _evaluate(expr: str, variables: dict[str, int]) -> int:
    name, _, plus = expr.partition("+")
    return variables[name] + (int(plus) if plus else 0)


def _dist_ids() -> dict[str, set[int]]:
    """The ids each table's `.dist` DELETEs clear, by table, both files together."""
    out: dict[str, set[int]] = {}
    for file, text in _DIST_DELETES.items():
        variables = _DIST_VARS[file]
        for table, where in re.findall(r"DELETE\s+FROM\s+(\w+)\s+WHERE\s+(.*?);", text, re.I):
            key = _KEYS[table.lower()]
            hit = re.search(
                rf"\b{key}\s+BETWEEN\s+(@\w+(?:\+\d+)?)\s+AND\s+(@\w+(?:\+\d+)?)", where, re.I
            )
            if hit:
                lo, hi = (_evaluate(g, variables) for g in hit.groups())
                ids = set(range(lo, hi + 1))
            else:
                hit = re.search(rf"\b{key}\s*=\s*(@\w+)", where, re.I)
                assert hit, f"{file}: no id test on {key} in {where!r}"
                ids = {_evaluate(hit.group(1), variables)}
            out.setdefault(table.lower(), set()).update(ids)
    return out


def _removed_ids() -> dict[str, set[int]]:
    """The ids Remove's DELETEs clear, by table."""
    out: dict[str, set[int]] = {}
    for statement in _remove_statements():
        found = re.match(r"DELETE\s+FROM\s+(\w+)\s+WHERE\s+(.*)", statement, re.I | re.S)
        if not found:
            continue
        table, where = found.group(1).lower(), found.group(2)
        key = _KEYS[table]
        between = re.search(rf"\b{key}\s+BETWEEN\s+(\d+)\s+AND\s+(\d+)", where, re.I)
        listed = re.search(rf"\b{key}\s+IN\s*\(([\d,\s]+)\)", where, re.I)
        single = re.search(rf"\b{key}\s*=\s*(\d+)", where, re.I)
        if between:
            ids = set(range(int(between.group(1)), int(between.group(2)) + 1))
        elif listed:
            ids = {int(v) for v in listed.group(1).split(",")}
        else:
            assert single, f"no id test on {key} in {where!r}"
            ids = {int(single.group(1))}
        out.setdefault(table, set()).update(ids)
    return out


def test_remove_clears_exactly_the_ids_the_install_clears() -> None:
    """Every table, every id: the union of the two `.dist` DELETE blocks, no more and no less."""
    assert _removed_ids() == _dist_ids()


def test_remove_keeps_the_install_s_other_conditions_on_the_row_it_matches() -> None:
    """The `.dist` DELETEs conditions of types 14 and 15 only, and a script of source type 0."""
    text = _remove_text()
    assert re.search(r"SourceTypeOrReferenceId\s+IN\s*\(14,\s*15\)", text)
    assert re.search(r"entryorguid\s+IN\s*\(190000,\s*190001\)\s+AND\s+source_type\s*=\s*0", text)
    assert re.search(r"id\s*=\s*194394\s+AND\s+guid\s*>=\s*200000", text)


def test_the_dist_blocks_are_what_the_test_thinks_they_are() -> None:
    """A mutation guard on the reader: it must see the ranges (10 menus + 9, 6 + 5 texts)."""
    ids = _dist_ids()
    assert ids["gossip_menu"] == set(range(50000, 50018))
    assert ids["npc_text"] == set(range(300000, 300010))
    assert ids["creature"] == {190000, 190001}
    assert ids["gameobject"] == {194394}


def _row_values(statement: str, table: str) -> list[str]:
    """The tuples a `REPLACE INTO <table> VALUES …` statement lists, as written."""
    head = re.match(rf"REPLACE\s+INTO\s+{table}\s+VALUES\s*", statement, re.I)
    assert head, statement[:60]
    body = statement[head.end() :]
    rows: list[str] = []
    depth, quote, start, i = 0, "", 0, 0
    while i < len(body):
        ch = body[i]
        if quote:
            if ch == "\\":
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            if depth == 0:
                start = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                rows.append(body[start : i + 1])
        i += 1
    return rows


def test_remove_puts_back_the_base_game_rows_the_install_deleted() -> None:
    """T560: 9 gossip_menu + 8 gossip_menu_option + 2 conditions rows, byte for byte from the pin."""
    replaces = {
        m.group(1): s
        for s in _remove_statements()
        if (m := re.match(r"REPLACE\s+INTO\s+(\w+)", s, re.I))
    }
    assert set(replaces) == set(_BASE_ROWS)
    for table, expected in _BASE_ROWS.items():
        assert tuple(_row_values(replaces[table], table)) == expected, table
    assert sum(len(v) for v in _BASE_ROWS.values()) == 19


def test_the_restored_rows_come_after_the_deletes_that_would_remove_them() -> None:
    statements = _remove_statements()
    last_delete = max(i for i, s in enumerate(statements) if s.upper().startswith("DELETE"))
    first_replace = min(i for i, s in enumerate(statements) if s.upper().startswith("REPLACE"))
    assert first_replace > last_delete


def test_every_restored_row_lies_in_a_range_remove_clears() -> None:
    """Otherwise the REPLACE would be putting back something the DELETE never took."""
    removed = _removed_ids()
    for table, rows in _BASE_ROWS.items():
        for row in rows:
            first = int(row.strip("(").split(",")[0])
            keyed = int(row.strip("(").split(",")[1]) if table == "conditions" else first
            assert keyed in removed[table], (table, row)
