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

import pytest

from tests.world_columns import PIN, PIN_COLUMNS, TABLE, columns_named
from yulon.catalog.catalog import load_catalog
from yulon.resources import manifests_dir


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
    if {t.lower() for t in TABLE.findall(s)} & set(PIN_COLUMNS)
]


@pytest.mark.parametrize(
    ("manifest", "statement"),
    [pytest.param(m, s, id=f"{m}:{s[:40]}") for m, s in _PINNED_CASES],
)
def test_every_statement_on_a_pinned_table_names_columns_it_has(
    manifest: str, statement: str
) -> None:
    named = {t.lower() for t in TABLE.findall(statement)}
    assert named <= set(PIN_COLUMNS), (
        f"{manifest}: a statement on a pinned table also names {sorted(named - set(PIN_COLUMNS))}; "
        f"add that table's columns at the pin to PIN_COLUMNS: {statement}"
    )
    tables = sorted(named)
    columns = frozenset().union(*(PIN_COLUMNS[t] for t in tables))
    unknown = sorted(columns_named(statement) - columns)
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
    assert columns_named(statement) - PIN_COLUMNS["creature"], statement


def test_the_column_reader_passes_what_the_pin_has() -> None:
    assert columns_named("UPDATE creature_template SET HealthModifier = HealthModifier * {hp}") == {
        "HealthModifier"
    }
    assert columns_named("SELECT c.guid FROM creature c WHERE c.id = 1") == {"guid", "id"}
    assert columns_named("SELECT g.guid FROM gameobject AS g WHERE g.id = 1") == {"guid", "id"}
    assert columns_named("DELETE FROM creature WHERE id IN (190000,190001)") == {"id"}
    assert columns_named("UPDATE `gameobject` SET `state` = 1 WHERE `id` = 5") == {"state", "id"}
    assert columns_named("SELECT guid FROM creature WHERE ScriptName = 'npc_x' AND map = 0") == {
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
        assert {t.lower() for t in TABLE.findall(text)} == {"creature"}, text
        assert columns_named(text) - PIN_COLUMNS["creature"] == {"id1"}, text
    assert columns_named("DELETE FROM acore_world.creature WHERE id = 5") == {"id"}


def test_there_are_real_statements_to_check() -> None:
    """An empty manifest read would make every parametrized case vanish and the file pass."""
    assert _PINNED_CASES, "no WotLK manifest statement on a pinned table was found"
    assert {"creature", "creature_template"} <= {
        t.lower() for _, s in _PINNED_CASES for t in TABLE.findall(s)
    }
