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
_COMPARED = re.compile(
    r"`?(\w+)`?\s*(?:=|<>|!=|<=|>=|<|>|\bIN\b|\bLIKE\b|\bBETWEEN\b|\bIS\b)", re.IGNORECASE
)
_INSERT_COLUMNS = re.compile(r"\bINTO\s+`?\w+`?\s*\(([^)]*)\)", re.IGNORECASE)


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
    """The identifiers a statement compares, sets, or inserts into."""
    named = {m.group(1) for m in _COMPARED.finditer(statement)}
    for m in _INSERT_COLUMNS.finditer(statement):
        named |= {c.strip(" `") for c in m.group(1).split(",") if c.strip()}
    return {n for n in named if not n.isdigit() and n.upper() not in {"AND", "OR", "NOT"}}


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
    tables = [t for t in _TABLE.findall(statement) if t.lower() in PIN_COLUMNS]
    assert len(set(tables)) == 1, f"{manifest}: one pinned table per statement: {statement}"
    columns = PIN_COLUMNS[tables[0].lower()]
    unknown = sorted(_columns_named(statement) - columns - {tables[0]})
    assert unknown == [], f"{manifest}: {tables[0]} has no {unknown} at {PIN[:8]}: {statement}"


def test_the_column_reader_sees_what_it_must() -> None:
    """The reader above is the whole check, so it is pinned on the statement that broke."""
    assert _columns_named("DELETE FROM creature WHERE id1 IN (190000,190001)") == {"id1"}
    assert _columns_named("UPDATE `gameobject` SET `state` = 1 WHERE `id` = 5") == {"state", "id"}
    assert _columns_named("INSERT INTO creature (guid, id, map) VALUES (1, 2, 0)") >= {
        "guid",
        "id",
        "map",
    }
    assert {"creature"} == {
        t.lower() for t in _TABLE.findall("DELETE FROM creature WHERE id1 IN (1)")
    }
