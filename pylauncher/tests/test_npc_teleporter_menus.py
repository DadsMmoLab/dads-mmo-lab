"""T564: installing NPC Teleporter keeps the two base-game NPCs' gossip menus.

Upstream (Zoidwaffle/sql-npc-teleporter @06e5242) builds its menus on ids 50000..50009
(`teleporter_capital.dist`) and 50009 (`teleporter_starting_zone.dist`), DELETEs those ids
first, and so removes 19 rows of the base game: the Searing Gorge gate menus of Mountaineer
Pebblebitty (creature 3836, menu 50000) and Maggran Earthbinder (11860, menu 50008). The two
files also collide with each other on 50009 (the capital's battleground menu is deleted by
the starting-zone file).

T634: upstream merged the renumbering (Zoidwaffle/sql-npc-teleporter#6, 38078d1d: capital menus
60000..60009, starting-zone menu 60010), so Yu'lon moves nothing any more. What stays, around the
unmodified files (as for T104's `@ONY_LEVEL`):

* before the files, it clears the menus an earlier Install of this module made, found through
  the two NPCs' `gossip_menu_id` and kept to rows carrying the module's own npc_text ids
  (300000..300009), not an id range, which could hold somebody else's menus;
* after the capital file it puts back, only where missing (INSERT IGNORE), the 19 base rows an
  old install took, refuses a stale copy of the files still on 50000..50009, and writes the
  Onyxia answer into the row at the NPC's menu + 4.

These tests run the manifest's own statements, in order, against a SQLite model of the tables
and the REAL `.dist` files (tests/data/npc-teleporter-*): upstream @06e5242 (the old layout) and
upstream since #6 (035da5d = 38078d1d's tree), plus that same file renumbered to 70000/70010. The model understands
what these files and statements use of MySQL: `SET @var := ...` within one step (one mysql
session), `START TRANSACTION`/`COMMIT`, double-quoted strings, `ALTER TABLE ... AUTO_INCREMENT`
(skipped). The live check on a real WotLK database is in the ticket's gate directory.
"""

# ruff: noqa: E501
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

from tests.test_npc_teleporter_remove import _BASE_ROWS, _steps
from yulon.catalog.catalog import load_catalog

NEW_FIRST, NEW_LAST = 60000, 60010
DATA = Path(__file__).resolve().parent / "data"
VARIANTS = ("035da5d", "70000")
"""The .dist files in play: upstream since #6 (38078d1d), and #6 moved elsewhere."""
EXPECTED_MENUS = {"035da5d": (60000, 60010), "70000": (70000, 70010)}

PINS_MEASURED = (
    "7f12e89ee5f467a50e62eba1d525eac7dc953d03",
    "f19a18799",
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
CREATE TABLE gossip_menu (MenuID INT NOT NULL DEFAULT 0, TextID INT NOT NULL DEFAULT 0,
  PRIMARY KEY (MenuID, TextID));
CREATE TABLE gossip_menu_option (MenuID INT NOT NULL DEFAULT 0, OptionID INT NOT NULL DEFAULT 0,
  OptionIcon INT DEFAULT 0, OptionText TEXT, OptionBroadcastTextID INT DEFAULT 0,
  OptionType INT DEFAULT 0, OptionNpcFlag INT DEFAULT 0, ActionMenuID INT DEFAULT 0,
  ActionPoiID INT DEFAULT 0, BoxCoded INT DEFAULT 0, BoxMoney INT DEFAULT 0, BoxText TEXT,
  BoxBroadcastTextID INT DEFAULT 0, VerifiedBuild INT DEFAULT 0, PRIMARY KEY (MenuID, OptionID));
CREATE TABLE conditions (SourceTypeOrReferenceId INT NOT NULL DEFAULT 0,
  SourceGroup INT NOT NULL DEFAULT 0, SourceEntry INT NOT NULL DEFAULT 0,
  SourceId INT NOT NULL DEFAULT 0, ElseGroup INT NOT NULL DEFAULT 0,
  ConditionTypeOrReference INT NOT NULL DEFAULT 0, ConditionTarget INT NOT NULL DEFAULT 0,
  ConditionValue1 INT NOT NULL DEFAULT 0, ConditionValue2 INT NOT NULL DEFAULT 0,
  ConditionValue3 INT NOT NULL DEFAULT 0, NegativeCondition INT DEFAULT 0, ErrorType INT DEFAULT 0,
  ErrorTextId INT DEFAULT 0, ScriptName TEXT DEFAULT '', Comment TEXT,
  PRIMARY KEY (SourceTypeOrReferenceId, SourceGroup, SourceEntry, SourceId, ElseGroup,
    ConditionTypeOrReference, ConditionTarget, ConditionValue1, ConditionValue2, ConditionValue3));
CREATE TABLE smart_scripts (entryorguid INT NOT NULL DEFAULT 0, source_type INT NOT NULL DEFAULT 0,
  id INT NOT NULL DEFAULT 0, link INT NOT NULL DEFAULT 0, event_type INT DEFAULT 0,
  event_param1 INT DEFAULT 0, PRIMARY KEY (entryorguid, source_type, id, link));
CREATE TABLE creature_template (entry INT PRIMARY KEY, gossip_menu_id INT NOT NULL DEFAULT 0);
CREATE TABLE creature_template_model (CreatureID INT);
CREATE TABLE creature_template_addon (entry INT);
CREATE TABLE npc_text (ID INT);
CREATE TABLE creature (guid INTEGER PRIMARY KEY AUTOINCREMENT, id INT);
CREATE TABLE gameobject (guid INTEGER PRIMARY KEY AUTOINCREMENT, id INT);
INSERT INTO sqlite_sequence VALUES ('creature', 199999), ('gameobject', 199999);
INSERT INTO gameobject (guid, id) VALUES (100496, 194394);
"""

_ALL_TABLES = (
    "gossip_menu",
    "gossip_menu_option",
    "conditions",
    "smart_scripts",
    "creature_template",
    "creature_template_model",
    "creature_template_addon",
    "npc_text",
    "creature",
    "gameobject",
)


class Interrupted(Exception):
    """The fault injected between two statements."""


class Refused(Exception):
    """A `verify` entry found no row: the applier would have raised."""


def _statements(text: str) -> list[str]:
    """Split `text` on `;` outside quotes, dropping `--` and `/* */` comments."""
    out: list[str] = []
    cur: list[str] = []
    quote = ""
    i = 0
    while i < len(text):
        ch = text[i]
        if quote:
            cur.append(ch)
            if ch == "\\" and i + 1 < len(text):
                cur.append(text[i + 1])
                i += 1
            elif ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            cur.append(ch)
        elif text.startswith("--", i):
            i = text.find("\n", i)
            if i < 0:
                break
        elif text.startswith("/*", i):
            i = text.find("*/", i) + 1
        elif ch == ";":
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
        i += 1
    if "".join(cur).strip():
        out.append("".join(cur).strip())
    return [s for s in out if s]


def _split_top(text: str) -> list[str]:
    """Split on commas outside quotes and brackets."""
    parts: list[str] = []
    cur: list[str] = []
    depth, quote = 0, ""
    for ch in text:
        if quote:
            quote = "" if ch == quote else quote
        elif ch in "'\"":
            quote = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    parts.append("".join(cur).strip())
    return parts


def _sqlite_text(statement: str) -> str:
    """MySQL's string spellings in SQLite's: "x" and 'it\\'s' become 'x' and 'it''s'."""
    out: list[str] = []
    i = 0
    while i < len(statement):
        ch = statement[i]
        if ch in "'\"":
            end = ch
            out.append("'")
            i += 1
            while statement[i] != end:
                if statement[i] == "\\":
                    i += 1
                    out.append("''" if statement[i] == "'" else statement[i])
                elif statement[i] == "'":
                    out.append("''")
                else:
                    out.append(statement[i])
                i += 1
            out.append("'")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


class World:
    """A WotLK world database in SQLite, with the statement counter the fault injection uses."""

    def __init__(self) -> None:
        self.con = sqlite3.connect(":memory:", isolation_level=None)
        self.con.executescript(_TABLES)
        self.budget: int | None = None
        for table, rows in _BASE_ROWS.items():
            self.con.executescript(
                _sqlite_text(f"INSERT INTO {table} VALUES " + ",".join(rows) + ";")
            )
        self.con.executescript(
            "INSERT INTO creature_template (entry, gossip_menu_id) VALUES (3836, 50000), (11860, 50008), (190, 0);"
            "INSERT INTO smart_scripts (entryorguid, source_type, id, link, event_type, event_param1) VALUES (3836, 0, 1, 0, 62, 50006);"
        )

    def _ensure(self, table: str, columns: list[str]) -> None:
        have = {r[1].lower() for r in self.con.execute(f"PRAGMA table_info({table})")}
        if not have:
            self.con.execute(
                f"CREATE TABLE {table} ({', '.join(c + ' DEFAULT 0' for c in columns)})"
            )
            return
        for column in columns:
            if column.lower() not in have:
                self.con.execute(f"ALTER TABLE {table} ADD COLUMN {column} DEFAULT 0")

    def run(self, text: str) -> None:
        """One mysql session: the statements in order, `@variables` living until the end.

        A session that ends inside `START TRANSACTION` (the fault, or a failed statement)
        rolls back, as the server does when the connection goes.
        """
        variables: dict[str, object] = {}

        def fill(statement: str) -> str:
            def value(m: re.Match[str]) -> str:
                v = variables[m.group(1).lower()]
                if v is None:
                    return "NULL"
                return f"'{v}'" if isinstance(v, str) else str(v)

            return re.sub(r"@(\w+)", value, statement)

        try:
            for statement in _statements(text):
                if self.budget is not None:
                    if self.budget == 0:
                        raise Interrupted
                    self.budget -= 1
                words = statement.upper().split()
                if words[0] == "ALTER":
                    continue
                if words[:2] == ["START", "TRANSACTION"]:
                    self.con.execute("BEGIN")
                    continue
                if words[0] == "COMMIT":
                    self.con.execute("COMMIT")
                    continue
                if words[0] == "SET":
                    for assignment in _split_top(statement[3:].strip()):
                        name, _, expr = assignment.partition(":=")
                        expr = _sqlite_text(fill(expr.strip()))
                        row = self.con.execute(f"SELECT {expr}").fetchone()
                        variables[name.strip().lstrip("@").lower()] = row[0]
                    continue
                sql = re.sub(
                    r"^INSERT\s+IGNORE",
                    "INSERT OR IGNORE",
                    _sqlite_text(fill(statement)),
                    flags=re.I,
                )
                insert = re.match(
                    r"(?:INSERT(?: OR IGNORE)?|REPLACE)\s+INTO\s+(\w+)\s*\(([^)]*)\)", sql, re.I
                )
                if insert:
                    self._ensure(insert.group(1), [c.strip() for c in insert.group(2).split(",")])
                self.con.execute(sql)
        finally:
            if self.con.in_transaction:
                self.con.execute("ROLLBACK")

    def state(self, like: World | None = None) -> dict[str, list[tuple[object, ...]]]:
        """Every row of the tables, without the auto-increment guids; `like`'s columns if given."""
        out = {}
        for table in _ALL_TABLES:
            shape = (like or self).con
            columns = [r[1] for r in shape.execute(f"PRAGMA table_info({table})") if r[1] != "guid"]
            rows = self.con.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            out[table] = sorted(rows, key=repr)
        return out

    def rows(self, sql: str) -> list[tuple[object, ...]]:
        return self.con.execute(sql).fetchall()


def _dist(variant: str, name: str) -> str:
    if variant == "70000":
        text = (DATA / "npc-teleporter-06e5242" / f"teleporter_{name}.dist").read_text("utf-8")
        old, new = ("50000", "70000") if name == "capital" else ("50009", "70010")
        moved, count = re.subn(rf"(@GOSSIP_MENU\s*:=\s*){old}", rf"\g<1>{new}", text)
        assert count == 1
        return moved
    if variant == "ony5":  # upstream moves the Onyxia row off menu+4
        text = _dist("035da5d", name)
        return text.replace(
            "(15, @GOSSIP_MENU+4, 11, 27, @ONY_LEVEL", "(15, @GOSSIP_MENU+5, 11, 27, @ONY_LEVEL"
        )
    if variant == "nonpc" and name == "capital":  # the capital file did not create its NPC
        return "-- nothing\n"
    return (DATA / f"npc-teleporter-{variant}" / f"teleporter_{name}.dist").read_text("utf-8")


def _verify(world: World, step: dict[str, object]) -> None:
    for check in step.get("verify", []):  # type: ignore[attr-defined]
        if not world.rows(check["query"]):
            raise Refused(check["missing"])


def _install(world: World, variant: str) -> None:
    """The manifest's install steps, in its order, each file the variant's."""
    for step in _steps():
        if step.get("when", "install") != "install":
            continue
        if "path" in step:
            world.run(_dist(variant, "capital" if "capital" in step["path"] else "starting_zone"))
        else:
            world.run(step["statement"].replace("{ony_level}", str(_ONY)))
        _verify(world, step)


def _old_install(world: World) -> None:
    """What this app did before T564: the two files, nothing around them."""
    world.run(_dist("06e5242", "capital"))
    world.run(_dist("06e5242", "starting_zone"))


def _remove(world: World) -> None:
    for step in _steps():
        if step.get("when") == "remove":
            world.run(step["statement"])


def _installed(variant: str) -> World:
    world = World()
    _install(world, variant)
    return world


def _ids(ranges: tuple[tuple[int, int], ...]) -> set[int]:
    return {i for lo, hi in ranges for i in range(lo, hi + 1)}


# --------------------------------------------------------------------------- the defect itself


def test_the_model_reproduces_the_defect_without_the_fix() -> None:
    """The reader's own guard: the old install (two files, no correction) loses all 19 rows."""
    world = World()
    _old_install(world)
    assert world.rows(
        "SELECT COUNT(*) FROM gossip_menu WHERE MenuID BETWEEN 50000 AND 50008 AND TextID IN (1833,1834,1835,1836,1837,1838,1839,5443)"
    ) == [(0,)]
    assert world.rows(
        "SELECT COUNT(*) FROM conditions WHERE SourceGroup = 50000 AND ConditionValue1 IN (3181, 3201)"
    ) == [(0,)]
    assert world.rows(
        "SELECT COUNT(*) FROM gossip_menu_option WHERE OptionText LIKE 'Open the gate%'"
    ) == [(0,)]
    # and the two files collide on 50009: the menu holds the zone's options, the capital's are gone
    assert world.rows("SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = 50009") == [(8,)]


# --------------------------------------------------------------------------- install, every file


@pytest.mark.parametrize("variant", VARIANTS)
def test_install_leaves_the_19_base_game_rows_as_the_pin_has_them(variant: str) -> None:
    world = _installed(variant)
    base = World()
    for table, column in (
        ("gossip_menu", "MenuID"),
        ("gossip_menu_option", "MenuID"),
        ("conditions", "SourceGroup"),
    ):
        sql = f"SELECT * FROM {table} WHERE {column} BETWEEN 50000 AND 50017 "
        assert sorted(world.rows(sql), key=repr) == sorted(base.rows(sql), key=repr), table
    assert world.rows(
        "SELECT gossip_menu_id FROM creature_template WHERE entry IN (3836, 11860) ORDER BY entry"
    ) == [(50000,), (50008,)]
    assert world.rows("SELECT event_param1 FROM smart_scripts WHERE entryorguid = 3836") == [
        (50006,)
    ]
    assert world.rows("SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = 50000") == [(2,)]


@pytest.mark.parametrize("variant", VARIANTS)
def test_the_teleporters_menus_are_where_the_file_that_ran_put_them(variant: str) -> None:
    capital, zone = EXPECTED_MENUS[variant]
    world = _installed(variant)
    assert world.rows(
        "SELECT gossip_menu_id FROM creature_template WHERE entry IN (190000, 190001) ORDER BY entry"
    ) == [(capital,), (zone,)]
    ours = {r[0] for r in world.rows("SELECT MenuID FROM gossip_menu")} - set(range(50000, 50018))
    assert ours == set(range(capital, capital + 10)) | {zone}
    assert {
        r[0]
        for r in world.rows("SELECT event_param1 FROM smart_scripts WHERE entryorguid = 190000")
    } == set(range(capital, capital + 10))
    assert {
        r[0]
        for r in world.rows("SELECT event_param1 FROM smart_scripts WHERE entryorguid = 190001")
    } == {zone}
    # an option's "open this menu" target is one of the teleporter's menus (or none)
    actions = {
        r[0]
        for r in world.rows(
            "SELECT ActionMenuID FROM gossip_menu_option WHERE MenuID >= 60000 AND ActionMenuID <> 0"
        )
    }
    assert actions <= ours
    assert world.rows(
        "SELECT COUNT(*) FROM conditions WHERE SourceGroup BETWEEN 50001 AND 50017"
    ) == [(0,)]
    assert world.rows(f"SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = {capital + 9}") == [
        (7,)
    ]  # the battleground menu survives
    assert world.rows(f"SELECT COUNT(*) FROM gossip_menu_option WHERE MenuID = {zone}") == [(8,)]


@pytest.mark.parametrize("variant", VARIANTS)
def test_the_onyxia_answer_lands_on_the_row_at_the_npcs_menu_plus_four(variant: str) -> None:
    capital, _ = EXPECTED_MENUS[variant]
    world = _installed(variant)
    assert world.rows(
        f"SELECT ConditionValue1 FROM conditions WHERE SourceTypeOrReferenceId = 15 AND SourceGroup = {capital + 4} AND SourceEntry = 11 AND ConditionTypeOrReference = 27"
    ) == [(_ONY,)]


def test_a_world_the_new_upstream_file_ran_alone_on_is_left_as_it_is() -> None:
    """Nothing is moved: apart from the Onyxia answer, the install is a no-op after the files."""
    world = World()
    world.run(_dist("035da5d", "capital"))
    world.run(_dist("035da5d", "starting_zone"))
    straight = world.state()
    straight["conditions"] = [
        r for r in straight["conditions"] if not (r[1] == 60004 and r[2] == 11)
    ]
    done = _installed("035da5d").state()
    done["conditions"] = [r for r in done["conditions"] if not (r[1] == 60004 and r[2] == 11)]
    assert done == straight


def test_no_install_step_moves_a_menu() -> None:
    """T634: upstream numbers its menus 60000-60018 itself, so nothing is renumbered here."""
    moving = [
        s
        for step in _steps()
        if "statement" in step
        for s in _statements(step["statement"])
        if re.match(
            r"UPDATE (gossip_menu|gossip_menu_option|conditions|smart_scripts|creature_template)",
            s,
            re.I,
        )
        and "ConditionValue1" not in s
    ]
    assert moving == []


def test_the_put_back_keeps_a_base_row_somebody_edited() -> None:
    """The 19 rows are put back only where they are missing, never over an edit."""
    for variant in VARIANTS:
        world = World()
        world.con.execute("UPDATE gossip_menu_option SET OptionText = 'mine' WHERE MenuID = 50003")
        _install(world, variant)
        assert world.rows("SELECT OptionText FROM gossip_menu_option WHERE MenuID = 50003") == [
            ("mine",)
        ]
        _remove(world)
        assert world.rows("SELECT OptionText FROM gossip_menu_option WHERE MenuID = 50003") == [
            ("mine",)
        ]


def test_files_of_the_old_layout_are_refused_and_a_new_install_mends_it() -> None:
    """A stale clone (menus on 50000) must say so, not pass: Install again with new files heals."""
    world = World()
    with pytest.raises(Refused, match="50000"):
        _install(world, "06e5242")
    _install(world, "035da5d")
    assert world.state() == _installed("035da5d").state()


def test_the_clear_before_the_files_takes_only_menus_with_the_modules_texts() -> None:
    """Step 0 follows the NPC to its menus but spares a row there that is not ours."""
    world = _installed("035da5d")
    for menu in (60003, 60010):
        _foreign(world, menu)
    world.run(_steps()[0]["statement"])
    assert {r[0] for r in world.rows("SELECT MenuID FROM gossip_menu WHERE TextID = 4242")} == {
        60003,
        60010,
    }
    assert world.rows("SELECT COUNT(*) FROM gossip_menu WHERE TextID >= 300000") == [(0,)]


@pytest.mark.parametrize("variant", VARIANTS)
def test_a_second_install_changes_nothing(variant: str) -> None:
    world = _installed(variant)
    first = world.state()
    _install(world, variant)
    assert world.state() == first


@pytest.mark.parametrize("variant", VARIANTS)
def test_an_install_over_one_made_before_the_fix_gives_the_base_rows_back(variant: str) -> None:
    damaged = World()
    _old_install(damaged)
    _install(damaged, variant)
    assert damaged.state() == _installed(variant).state()


@pytest.mark.parametrize("variant", ["035da5d", "70000"])
def test_an_install_over_one_this_fix_relocated_follows_the_new_file(variant: str) -> None:
    """The menus an earlier Install put at 60000..60010 go, wherever the new file builds."""
    world = World()  # what T564's relocation left: the new upstream's layout
    world.run(_dist("035da5d", "capital"))
    world.run(_dist("035da5d", "starting_zone"))
    _install(world, variant)
    assert world.state() == _installed(variant).state()


@pytest.mark.parametrize("variant", ["035da5d"])
def test_an_install_interrupted_between_any_two_statements_is_mended_by_installing_again(
    variant: str,
) -> None:
    probe = World()
    probe.budget = 10**6
    _install(probe, variant)
    statements = 10**6 - probe.budget
    assert statements > 40
    clean = _installed(variant).state()
    for stop in range(statements):
        world = World()
        world.budget = stop
        with pytest.raises(Interrupted):
            _install(world, variant)
        world.budget = None
        _install(world, variant)
        assert world.state() == clean, f"interrupted after {stop} statements"


# --------------------------------------------------------------------------- somebody else's ids


def _foreign(world: World, menu: int) -> None:
    world.con.execute(f"INSERT INTO gossip_menu (MenuID, TextID) VALUES ({menu}, 4242)")


def test_install_deletes_no_menu_that_is_not_the_modules() -> None:
    """Menus around the new range, and in it when upstream builds elsewhere, survive Install."""
    for variant, menus in (
        ("035da5d", (59999, 60019)),
        ("70000", (60000, 60005, 59999, 60011, 70020)),
    ):
        world = World()
        for menu in menus:
            _foreign(world, menu)
        _install(world, variant)
        assert {
            r[0] for r in world.rows("SELECT MenuID FROM gossip_menu WHERE TextID = 4242")
        } == set(menus), variant


# --------------------------------------------------------------------------- remove


@pytest.mark.parametrize("variant", VARIANTS)
def test_remove_after_install_leaves_the_world_as_it_was(variant: str) -> None:
    world = _installed(variant)
    _remove(world)
    assert world.state(like=World()) == World().state()


def test_remove_after_an_install_made_before_the_fix_still_leaves_the_base_game_whole() -> None:
    world = World()
    _old_install(world)
    _remove(world)
    assert world.state(like=World()) == World().state()


@pytest.mark.parametrize("variant", ["035da5d", "70000"])
def test_remove_interrupted_between_any_two_statements_is_mended_by_removing_again(
    variant: str,
) -> None:
    probe = _installed(variant)
    probe.budget = 10**6
    _remove(probe)
    statements = 10**6 - probe.budget
    for stop in range(statements):
        world = _installed(variant)
        world.budget = stop
        with pytest.raises(Interrupted):
            _remove(world)
        world.budget = None
        _remove(world)
        assert world.state(like=World()) == World().state(), f"interrupted after {stop} statements"


@pytest.mark.parametrize("variant", VARIANTS)
def test_remove_deletes_no_menu_that_is_not_the_modules(variant: str) -> None:
    """Nothing but the menus the NPCs pointed at goes: not the old range, not the new one."""
    world = _installed(variant)
    foreign = (50009, 50010, 50017, 59999, 60003, 60011, 70011, 61500)
    for menu in foreign:
        _foreign(world, menu)
    world.con.execute(
        "INSERT INTO gossip_menu_option (MenuID, OptionID, OptionText) VALUES (50012, 0, 'x'), (50003, 9, 'custom')"
    )
    _remove(world)
    assert {r[0] for r in world.rows("SELECT MenuID FROM gossip_menu WHERE TextID = 4242")} == set(
        foreign
    )
    assert world.rows(
        "SELECT MenuID, OptionID FROM gossip_menu_option WHERE OptionText IN ('x', 'custom') ORDER BY 1"
    ) == [(50003, 9), (50012, 0)]


# --------------------------------------------------------------------------- the range is free


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


# --------------------------------------------------------------------------- T634 follow-ups


def test_an_onyxia_row_that_upstream_moved_is_refused_in_plain_words() -> None:
    """The UPDATE would match nothing; the install must say what changed, not pass."""
    world = World()
    with pytest.raises(Refused, match="Onyxia"):
        _install(world, "ony5")


def test_a_missing_capital_npc_gets_its_own_sentence() -> None:
    """No row 190000 is not 'the files still use the old ids'."""
    world = World()
    with pytest.raises(Refused) as caught:
        _install(world, "nonpc")
    assert "50000" not in str(caught.value)
    assert "190000" in str(caught.value)
