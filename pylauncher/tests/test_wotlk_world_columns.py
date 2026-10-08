"""T558: every SQL statement a WotLK manifest ships names columns the pinned world database has.

AzerothCore's update `2026_06_16_00.sql` renamed `creature.id1` to `id` and
dropped `id2`/`id3`. NPC Teleporter's Remove still said `WHERE id1 IN …`, so it
failed on every server Yu'lon builds, and the teleporters stayed. Nothing ran
those statements against the real schema before a player pressed Remove.

`PIN_COLUMNS` is the world schema of the tables Yu'lon's statements touch, at the
catalog's WotLK pin: the pin's `data/sql/base/db_world/<table>.sql`, with every
`data/sql/updates/db_world/*.sql` at that pin that alters the table applied (for
`creature`, only `2026_06_16_00.sql`; for `gameobject`, none). Measured
2026-10-08 with the GitHub API at the pin. The first test fails when the pin
moves, so the snapshot is measured again rather than trusted.
"""

from __future__ import annotations

import json
import re

import pytest

from yulon.catalog.catalog import load_catalog
from yulon.resources import manifests_dir

PIN = "7f12e89ee5f467a50e62eba1d525eac7dc953d03"
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
}

_TABLE = re.compile(
    r"\b(?:DELETE\s+FROM|UPDATE|INSERT\s+(?:IGNORE\s+)?INTO|REPLACE\s+INTO|FROM|JOIN)\s+`?(\w+)`?",
    re.IGNORECASE,
)
_ALIAS = re.compile(r"\b(?:FROM|JOIN|UPDATE|INTO)\s+`?\w+`?\s+(?:AS\s+)?`?(\w+)`?", re.IGNORECASE)
_WORD = re.compile(r"`([^`]+)`|@?\w+")
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
    text = _STRING.sub(" ", statement)
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
        f"the WotLK pin moved to {core.rev}: re-read creature/gameobject at it "
        "(base SQL plus every db_world update that alters them) and update PIN_COLUMNS"
    )


@pytest.mark.parametrize(
    ("manifest", "statement"),
    [
        pytest.param(m, s, id=f"{m}:{s[:40]}")
        for m, s in _wotlk_statements()
        if {t.lower() for t in _TABLE.findall(s)} & set(PIN_COLUMNS)
    ],
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
    assert _columns_named("SELECT c.guid FROM creature c WHERE c.id = 1") == {"guid", "id"}
    assert _columns_named("SELECT g.guid FROM gameobject AS g WHERE g.id = 1") == {"guid", "id"}
    assert _columns_named("DELETE FROM creature WHERE id IN (190000,190001)") == {"id"}
    assert _columns_named("UPDATE `gameobject` SET `state` = 1 WHERE `id` = 5") == {"state", "id"}
    assert _columns_named("SELECT guid FROM creature WHERE ScriptName = 'npc_x' AND map = 0") == {
        "guid",
        "ScriptName",
        "map",
    }
