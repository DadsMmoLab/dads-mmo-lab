"""T558: every SQL statement a WotLK manifest ships names columns the pinned world database has.

AzerothCore's update `2026_06_16_00.sql` renamed `creature.id1` to `id` and
dropped `id2`/`id3`. NPC Teleporter's Remove still said `WHERE id1 IN …`, so it
failed on every server Yu'lon builds, and the teleporters stayed. Nothing ran
those statements against the real schema before a player pressed Remove.

`PIN_COLUMNS` is the world schema of the tables Yu'lon's statements touch, at the
catalog's WotLK pin: the pin's `data/sql/base/db_world/<table>.sql`, with every
`data/sql/updates/db_world/*.sql` at that pin that alters the table applied (for
`creature`, only `2026_06_16_00.sql`; for `gameobject`, none). Measured
2026-10-08 with the GitHub API at the pin. T560 added the tables NPC Teleporter's Remove
touches and the mob-stat manifests update (base SQL only: the pin's db_world updates change
none of their columns). The first test fails when the pin moves, so the snapshot is measured
again rather than trusted. T389 moved the pin from 7f12e89e to f19a1879 (2026-10-08): no base
SQL file changed between the two, and the 20 db_world updates between them alter no table
(their one DDL is `CREATE TABLE IF NOT EXISTS` for two new `*_dbc` tables), so the snapshot holds.
"""

from __future__ import annotations

import json
import re

import pytest

from yulon.catalog.catalog import load_catalog
from yulon.resources import manifests_dir

PIN = "f19a18799a35f7c24bdcdc9ea399c601f166259b"
"""mod-playerbots/azerothcore-wotlk, the revision `PIN_COLUMNS` was read at."""

PIN_COLUMNS: dict[str, frozenset[str]] = {
    "creature": frozenset(
        "guid id map zoneId areaId spawnMask phaseMask equipment_id position_x position_y "
        "position_z orientation spawntimesecs wander_distance currentwaypoint curhealth "
        "curmana MovementType npcflag unit_flags dynamicflags ScriptName VerifiedBuild "
        "CreateObject Comment".split()
    ),
    "gameobject": frozenset(
        "guid id map zoneId areaId spawnMask phaseMask position_x position_y position_z "
        "orientation rotation0 rotation1 rotation2 rotation3 spawntimesecs animprogress state "
        "ScriptName VerifiedBuild Comment".split()
    ),
    "creature_template": frozenset(
        "entry difficulty_entry_1 difficulty_entry_2 difficulty_entry_3 KillCredit1 KillCredit2 "
        "name subname IconName gossip_menu_id minlevel maxlevel exp faction npcflag speed_walk "
        "speed_run speed_swim speed_flight detection_range rank dmgschool DamageModifier "
        "BaseAttackTime RangeAttackTime BaseVariance RangeVariance unit_class unit_flags "
        "unit_flags2 dynamicflags family type type_flags lootid pickpocketloot skinloot "
        "PetSpellDataId VehicleId mingold maxgold AIName MovementType HoverHeight "
        "HealthModifier ManaModifier ArmorModifier ExperienceModifier RacialLeader movementId "
        "RegenHealth CreatureImmunitiesId flags_extra ScriptName VerifiedBuild".split()
    ),
    "creature_template_model": frozenset(
        "CreatureID Idx CreatureDisplayID DisplayScale Probability VerifiedBuild".split()
    ),
    "creature_template_addon": frozenset(
        "entry path_id mount bytes1 bytes2 emote visibilityDistanceType auras".split()
    ),
    "gossip_menu": frozenset("MenuID TextID".split()),
    "gossip_menu_option": frozenset(
        "MenuID OptionID OptionIcon OptionText OptionBroadcastTextID OptionType OptionNpcFlag "
        "ActionMenuID ActionPoiID BoxCoded BoxMoney BoxText BoxBroadcastTextID "
        "VerifiedBuild".split()
    ),
    "npc_text": frozenset(
        "ID text0_0 text0_1 BroadcastTextID0 lang0 Probability0 em0_0 em0_1 em0_2 em0_3 em0_4 "
        "em0_5 text1_0 text1_1 BroadcastTextID1 lang1 Probability1 em1_0 em1_1 em1_2 em1_3 "
        "em1_4 em1_5 text2_0 text2_1 BroadcastTextID2 lang2 Probability2 em2_0 em2_1 em2_2 "
        "em2_3 em2_4 em2_5 text3_0 text3_1 BroadcastTextID3 lang3 Probability3 em3_0 em3_1 "
        "em3_2 em3_3 em3_4 em3_5 text4_0 text4_1 BroadcastTextID4 lang4 Probability4 em4_0 "
        "em4_1 em4_2 em4_3 em4_4 em4_5 text5_0 text5_1 BroadcastTextID5 lang5 Probability5 "
        "em5_0 em5_1 em5_2 em5_3 em5_4 em5_5 text6_0 text6_1 BroadcastTextID6 lang6 "
        "Probability6 em6_0 em6_1 em6_2 em6_3 em6_4 em6_5 text7_0 text7_1 BroadcastTextID7 "
        "lang7 Probability7 em7_0 em7_1 em7_2 em7_3 em7_4 em7_5 VerifiedBuild".split()
    ),
    "smart_scripts": frozenset(
        "entryorguid source_type id link event_type event_phase_mask event_chance event_flags "
        "event_param1 event_param2 event_param3 event_param4 event_param5 event_param6 "
        "action_type action_param1 action_param2 action_param3 action_param4 action_param5 "
        "action_param6 target_type target_param1 target_param2 target_param3 target_param4 "
        "target_x target_y target_z target_o comment".split()
    ),
    "conditions": frozenset(
        "SourceTypeOrReferenceId SourceGroup SourceEntry SourceId ElseGroup "
        "ConditionTypeOrReference ConditionTarget ConditionValue1 ConditionValue2 "
        "ConditionValue3 NegativeCondition ErrorType ErrorTextId ScriptName Comment".split()
    ),
}

_TABLE = re.compile(
    r"\b(?:DELETE\s+FROM|UPDATE|INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO|FROM|JOIN)\s+"
    r"(?:`?\w+`?\s*\.\s*)?`?(\w+)`?",
    re.IGNORECASE,
)
"""A table after its keyword; a `schema.` in front of it (`acore_world.creature`) is skipped,
so the table is read as `creature` and not as the schema (T560, cold review of T558)."""
_QUALIFIER = re.compile(r"`?\b\w+`?\s*\.\s*(?=`?\w)")
"""`schema.` or `alias.` in front of a name: the qualifier is not a column."""
_ALIAS = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+`?\w+`?\s+(?:AS\s+)?`?(\w+)`?", re.IGNORECASE)
_WORD = re.compile(r"`([^`]+)`|@?\w+")
_PLACEHOLDER = re.compile(r"\{\w+\}")
"""`{ony_level}`, `{hp}`: Yu'lon fills these with the player's answer before the statement runs."""
_STRING = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_SQL_WORDS = frozenset("""
    SELECT FROM WHERE AND OR NOT IN IS NULL LIKE BETWEEN EXISTS DELETE UPDATE SET INSERT INTO
    IGNORE REPLACE VALUES ORDER GROUP BY HAVING LIMIT OFFSET ASC DESC AS ON JOIN LEFT RIGHT
    INNER OUTER CROSS USING DISTINCT UNION ALL CASE WHEN THEN ELSE END COUNT SUM MIN MAX AVG
    IF IFNULL COALESCE CONCAT LOWER UPPER TRUE FALSE DUPLICATE KEY DEFAULT INTERVAL
    """.split())
"""SQL's own words. Every other identifier in a statement on a pinned table must be a
column of a table the statement names, or the statement fails: the reader fails closed
on a shape it does not know (Codex adversarial review: a projection, the right side of a
SET, an ORDER BY were not seen by the first reader)."""


def _wotlk_statements() -> list[tuple[str, str]]:
    """`(manifest file, one statement)` for every SQL text a WotLK manifest carries."""
    found: list[tuple[str, str]] = []
    for path in sorted((manifests_dir() / "wow-wotlk").glob("*/*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [step.get("statement") or "" for step in data.get("sql", [])]
        texts += [(step.get("check") or {}).get("query") or "" for step in data.get("sql", [])]
        texts += [(p.get("exists") or {}).get("query") or "" for p in data.get("prompts", [])]
        for text in texts:
            found += [(path.name, s.strip()) for s in text.split(";") if s.strip()]
    return found


def _columns_named(statement: str) -> set[str]:
    """Each identifier in `statement` but SQL words, numbers, strings, variables and tables."""
    text = _QUALIFIER.sub("", _PLACEHOLDER.sub(" 1 ", _STRING.sub(" ", statement)))
    tables = {t.lower() for t in _TABLE.findall(text)}
    tables |= {
        a.lower() for a in _ALIAS.findall(text) if a.upper() not in _SQL_WORDS
    }  # `FROM creature c`: `c` is the table, not a column
    named: set[str] = set()
    for m in _WORD.finditer(text):
        word = m.group(1) or m.group(0)
        if word.startswith("@") or word.isdigit() or word.upper() in _SQL_WORDS:
            continue
        if word.lower() in tables:
            continue
        named.add(word.split(".")[-1])
    return named


def test_the_snapshot_is_of_the_catalogs_wotlk_pin() -> None:
    """Moving the pin fails here first: measure `PIN_COLUMNS` again at the new revision."""
    core = load_catalog().get("wow-wotlk").emulator.sources[0]
    assert core.rev == PIN, (
        f"the WotLK pin moved to {core.rev}: re-read the PIN_COLUMNS tables at it "
        "(base SQL plus every db_world update that alters them) and update PIN_COLUMNS; "
        "re-copy test_npc_teleporter_remove.py's _BASE_ROWS from the new base SQL and "
        "re-check that no base row moved into the teleporter's removed ranges"
    )


_PINNED_CASES = [
    (m, s)
    for m, s in _wotlk_statements()
    if {t.lower() for t in _TABLE.findall(s)} & set(PIN_COLUMNS)
]


@pytest.mark.parametrize(
    ("manifest", "statement"),
    [pytest.param(m, s, id=f"{m}:{s[:40]}") for m, s in _PINNED_CASES],
)
def test_every_statement_on_a_pinned_table_names_columns_it_has(
    manifest: str, statement: str
) -> None:
    named = {t.lower() for t in _TABLE.findall(statement)}
    assert named <= set(PIN_COLUMNS), (
        f"{manifest}: a statement on a pinned table also names {sorted(named - set(PIN_COLUMNS))}; "
        f"add that table's columns at the pin to PIN_COLUMNS: {statement}"
    )
    tables = sorted(named)
    columns = frozenset().union(*(PIN_COLUMNS[t] for t in tables))
    unknown = sorted(_columns_named(statement) - columns)
    assert unknown == [], f"{manifest}: {tables[0]} has no {unknown} at {PIN[:8]}: {statement}"


@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM creature WHERE id1 IN (190000,190001)",
        "SELECT id1 FROM creature",
        "UPDATE creature SET id = id1",
        "DELETE FROM creature ORDER BY id1",
        "SELECT c.id1 FROM creature c WHERE c.guid = 1",
        "INSERT INTO creature (guid, id1, map) VALUES (1, 2, 0)",
        "DELETE FROM creature WHERE guid IN (SELECT guid FROM creature WHERE id2 = 5)",
    ],
)
def test_the_column_reader_sees_a_dropped_column_wherever_it_stands(statement: str) -> None:
    """The reader is the whole check, so it is pinned on every shape a column can take."""
    assert _columns_named(statement) - PIN_COLUMNS["creature"], statement


def test_the_column_reader_passes_what_the_pin_has() -> None:
    assert _columns_named(
        "UPDATE creature_template SET HealthModifier = HealthModifier * {hp}"
    ) == {"HealthModifier"}
    assert _columns_named("SELECT c.guid FROM creature c WHERE c.id = 1") == {"guid", "id"}
    assert _columns_named("SELECT g.guid FROM gameobject AS g WHERE g.id = 1") == {"guid", "id"}
    assert _columns_named("DELETE FROM creature WHERE id IN (190000,190001)") == {"id"}
    assert _columns_named("UPDATE `gameobject` SET `state` = 1 WHERE `id` = 5") == {"state", "id"}
    assert _columns_named("SELECT guid FROM creature WHERE ScriptName = 'npc_x' AND map = 0") == {
        "guid",
        "ScriptName",
        "map",
    }


def test_a_schema_qualified_table_is_read_as_its_table() -> None:
    """`acore_world.creature` is the table `creature`, not a schema to filter out (T558 review)."""
    for text in (
        "DELETE FROM acore_world.creature WHERE id1 = 5",
        "DELETE FROM `acore_world`.`creature` WHERE `id1` = 5",
        "UPDATE acore_world.creature SET id1 = 5",
    ):
        assert {t.lower() for t in _TABLE.findall(text)} == {"creature"}, text
        assert _columns_named(text) - PIN_COLUMNS["creature"] == {"id1"}, text
    assert _columns_named("DELETE FROM acore_world.creature WHERE id = 5") == {"id"}


def test_there_are_real_statements_to_check() -> None:
    """An empty manifest read would make every parametrized case vanish and the file pass."""
    assert _PINNED_CASES, "no WotLK manifest statement on a pinned table was found"
    assert {"creature", "creature_template"} <= {
        t.lower() for _, s in _PINNED_CASES for t in _TABLE.findall(s)
    }
