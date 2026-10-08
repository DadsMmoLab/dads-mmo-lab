"""T555 T4: the SQL the pinned mod-unbound ships names only columns the pinned world has.

AzerothCore's update `2026_06_16_00.sql` renamed `creature.id1` to `id` and dropped
`id2`/`id3` (issue N1). A Mentor spawn file that still said `id1` fails the import and
leaves the server without a Mentor. T558's fail-closed column reader (`tests/world_columns.py`)
is run here on a byte snapshot of the module's own SQL, `tests/fixtures/mod-unbound`, taken at
the revision the `wow-unbound` entry pins. `REV` there is that revision, so moving the pin
fails the first test until the snapshot is taken again; nothing is trusted past the pin.

The snapshot is the module's `data/sql/**` plus its `MANIFEST.sha256`, vendored byte for byte
(`.gitattributes` marks it `-text`). The guard also reads the Mentor spawn list out of the SQL
and ties it to the entry's `sql_checks` row, so the count the installer checks after the
import and the rows the file inserts cannot drift apart.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

from tests.world_columns import PIN_COLUMNS, TABLE, columns_named, insert_rows, split_statements
from yulon.catalog.catalog import load_catalog

FIXTURE = Path(__file__).parent / "fixtures" / "mod-unbound"
MODULE_DEST = "modules/mod-unbound"
MENTOR_ID = 900001
SPAWN_GUIDS = list(range(9000101, 9000110))
DROPPED_CREATURE_COLUMNS = {"id1", "id2", "id3"}
"""`creature` has had one `id` column since `2026_06_16_00.sql`; these are gone at the pin."""

GIT_BLOBS = {
    "MANIFEST.sha256": "4004ea79d9451c7856bd5cba1ce4c5dc7b4e9016",
    "data/sql/db-characters/01_unbound_characters.sql": "9636121a034fcb53f177cdad2214f06534a61b95",
    "data/sql/db-characters/02_dml_autobuff_kv.sql": "d28d4c0d5473a6d8cffd556e3fa38ef77ba71c25",
    "data/sql/db-world/00_npc_setup.sql": "38da9ac832d90dd2fc6a1d77209c8622841d8159",
    "data/sql/db-world/01_unbound_world.sql": "f61a34f89237a41ca86a6b21d1daafddd882915c",
    "data/sql/db-world/02_fix_catalog_req_level.sql": "b64a893b8950190b4e98ffff1a08b8ccd1ee2b4a",
    "data/sql/db-world/03_creation_gift_spells.sql": "4c66b9484d6490fb8224edfeced474a4210f6a76",
    "data/sql/db-world/04_catalog_druid_forms.sql": "c323eeccec8705dbc41795469dfc29217c6ced06",
    "data/sql/db-world/05_individual_purchase_prereqs.sql": "cddefb119cd8463adf39aa4fc019a99447f5d44d",  # noqa: E501
    "data/sql/db-world/06_universal_skill_access.sql": "0d73d84e3b81a88a1d59f88c8dbd9e9a6d306fc0",
    "data/sql/db-world/07_mentor_stone.sql": "d885cb7a5205cfcaab49b0fb18e09f2429814f8e",
    "data/sql/db-world/08_catalog_additions.sql": "1318f2bbb8da1d31e7106370ce12f1dd2194a7fb",
    "data/sql/db-world/10_catalog_audit_fixes.sql": "3c4f0a4e4331675b2824c8872bae4eedba590f49",
    "data/sql/db-world/11_catalog_gap_additions.sql": "01be695b731869a265808264d826ed7a44daf6e4",
    "data/sql/db-world/12_mount_spell_fix.sql": "87339fb5971df8ee55bd52cb44b57e73470d438f",
    "data/sql/db-world/13_flight_form_fix.sql": "b21c90efdd129fb2be2220805a028ead42da93cd",
    "data/sql/db-world/14_judgement_fix.sql": "129b95780dd9a08b743805eada240029614b4bb0",
    "data/sql/db-world/15_unbound_mentor_spawns.sql": "abd81d0ea1d85fff12d58bcdc60e7c9b16193870",
    "data/sql/db-world/base/multiclass_summons.sql": "ffd768cc8275ce3c25b22c429226afd25207bf95",
}
"""Git's own blob id of each file of the snapshot at the pinned revision, as the GitHub trees API
lists them for DadsMmoLab/dads-mmo-lab `fd247bed` (`gh api repos/DadsMmoLab/dads-mmo-lab/git/trees/
<rev>?recursive=1`). The module's `MANIFEST.sha256` sits beside the files it attests, so it cannot
tell a snapshot of another revision; these ids can, because they are the repository's."""


def _unbound_module_rev() -> str:
    entry = load_catalog().get("wow-unbound")
    (source,) = [s for s in entry.emulator.sources if s.dest == MODULE_DEST]
    assert source.rev is not None
    return source.rev


def _sql_files() -> list[Path]:
    return sorted((FIXTURE / "data" / "sql").rglob("*.sql"))


def _statements() -> list[tuple[str, str]]:
    """`(file, one statement)` for every statement the module's SQL files hold."""
    found: list[tuple[str, str]] = []
    for path in _sql_files():
        text = path.read_bytes().decode("utf-8")
        found += [(path.relative_to(FIXTURE).as_posix(), s) for s in split_statements(text)]
    return found


def _pinned_statements() -> list[tuple[str, str]]:
    return [
        (f, s) for f, s in _statements() if {t.lower() for t in TABLE.findall(s)} & set(PIN_COLUMNS)
    ]


def _tables(statement: str) -> set[str]:
    return {t.lower() for t in TABLE.findall(statement)}


def _creature_inserts() -> list[str]:
    return [
        s
        for _, s in _statements()
        if re.match(r"INSERT\s+(?:IGNORE\s+)?INTO\s+`?creature`?\s*\(", s, re.IGNORECASE)
    ]


def _git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()  # noqa: S324


def _spawns_in(statements: list[str]) -> list[tuple[int, int]]:
    """`(guid, id)` of every row of every `INSERT INTO creature`, read by column NAME.

    The INSERT's own column list says which value is the guid and which the id, so a file that
    lists `(id, guid, ...)` over values written as `(guid, id, ...)` is read as what the
    database will store. A column missing, listed twice, or a value that is not an integer
    literal raises, so it cannot pass as a spawn list.
    """
    rows: list[tuple[int, int]] = []
    for statement in statements:
        columns, values = insert_rows(statement)
        names = [c.lower() for c in columns]
        for needed in ("guid", "id"):
            assert (
                names.count(needed) == 1
            ), f"INSERT INTO creature has {needed} {names.count(needed)} times"
        gi, ii = names.index("guid"), names.index("id")
        for row in values:
            assert len(row) == len(
                names
            ), f"a creature row has {len(row)} values for {len(names)} columns"
            assert (
                row[gi].isdigit() and row[ii].isdigit()
            ), f"guid/id are not integer literals: {row[gi]} {row[ii]}"
            rows.append((int(row[gi]), int(row[ii])))
    return rows


def _mentor_spawns() -> list[tuple[int, int]]:
    return _spawns_in(_creature_inserts())


def test_the_snapshot_is_of_the_pinned_module_revision() -> None:
    """Moving the pin fails here first: take the snapshot again at the new revision."""
    rev = (FIXTURE / "REV").read_text(encoding="ascii").strip()
    assert rev == _unbound_module_rev(), (
        f"the wow-unbound entry pins mod-unbound at {_unbound_module_rev()} but "
        f"tests/fixtures/mod-unbound is the snapshot of {rev}: copy that revision's "
        "data/sql/** and MANIFEST.sha256 over it, byte for byte, then set REV"
    )


def test_every_snapshot_file_is_the_one_the_modules_manifest_lists() -> None:
    lines = (FIXTURE / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
    listed = {}
    for line in lines:
        digest, _, name = line.partition("  ")
        if name.startswith("data/sql/"):
            listed[name.lstrip("*")] = digest
    shipped = {p.relative_to(FIXTURE).as_posix(): p for p in _sql_files()}
    assert set(shipped) == set(listed), "the snapshot and the manifest list different SQL files"
    for name, path in shipped.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == listed[name], name


def test_the_snapshot_is_the_git_objects_of_the_pinned_revision() -> None:
    """The snapshot is what the repository holds at the pin, not a label beside a manifest."""
    shipped = {
        p.relative_to(FIXTURE).as_posix(): _git_blob_id(p.read_bytes())
        for p in [*_sql_files(), FIXTURE / "MANIFEST.sha256"]
    }
    assert shipped == GIT_BLOBS, (
        "the snapshot is not byte for byte the pinned revision's data/sql and MANIFEST.sha256: "
        "take it again from that revision and list its blob ids in GIT_BLOBS"
    )


def test_there_are_real_statements_to_check() -> None:
    """An empty read of the module's SQL would make every check below pass on nothing."""
    assert _sql_files(), "no SQL in the snapshot"
    pinned = _pinned_statements()
    assert pinned, "no statement on a pinned table was found in the module's SQL"
    assert any(
        "creature" in _tables(s) and s.upper().startswith("INSERT") for _, s in pinned
    ), "the module ships no INSERT INTO creature"


def test_the_mentor_spawn_list_is_nine_rows_of_the_mentor() -> None:
    spawns = _mentor_spawns()
    assert spawns, "the module's SQL spawns no Mentor at all"
    assert sorted(spawns) == [(g, MENTOR_ID) for g in SPAWN_GUIDS]


def test_the_installers_mentor_count_is_the_spawn_list() -> None:
    """The row `sql_checks` reads after the import is exactly what the SQL inserts."""
    checks = load_catalog().get("wow-unbound").install.native.azerothcore.sql_checks  # type: ignore[union-attr]
    (check,) = [c for c in checks if c.table == "creature"]
    assert check.where == (
        f"id = {MENTOR_ID} AND guid >= {SPAWN_GUIDS[0]} AND guid <= {SPAWN_GUIDS[-1]}"
    )
    assert check.at_least == len(_mentor_spawns())


def test_every_statement_on_a_pinned_table_names_columns_it_has() -> None:
    bad: list[str] = []
    for file, statement in _pinned_statements():
        tables = sorted(_tables(statement) & set(PIN_COLUMNS))
        columns = frozenset().union(*(PIN_COLUMNS[t] for t in tables))
        unknown = sorted(columns_named(statement) - columns)
        if unknown:
            bad.append(f"{file}: {tables} has no {unknown}: {statement[:80]}")
    assert bad == []


def test_no_statement_on_creature_names_a_dropped_id_column() -> None:
    """Named on its own so the N1 rule reads as a rule, not as a side effect of the reader."""
    bad = [
        f"{file}: {statement[:80]}"
        for file, statement in _statements()
        if "creature" in _tables(statement) and columns_named(statement) & DROPPED_CREATURE_COLUMNS
    ]
    assert bad == []


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT IGNORE INTO `creature` (`guid`, `id1`) VALUES (9000101, 900001)",
        "DELETE FROM `creature` WHERE `guid` = 9000101 AND `id2` = 900001",
        "UPDATE creature SET id3 = 0 WHERE guid = 1",
    ],
)
def test_the_n1_rule_sees_a_dropped_column(statement: str) -> None:
    assert columns_named(statement) & DROPPED_CREATURE_COLUMNS


def test_the_statement_splitter_keeps_strings_and_drops_comments() -> None:
    text = (
        "-- a note; with a semicolon\n"
        "INSERT INTO t (a) VALUES ('x; y', 'it''s'); # trailing; note\n"
        "/* block; comment */ DELETE FROM t WHERE a = 1;\n"
        "SELECT 1"
    )
    assert split_statements(text) == [
        "INSERT INTO t (a) VALUES ('x; y', 'it''s')",
        "DELETE FROM t WHERE a = 1",
        "SELECT 1",
    ]


_SWAPPED = (
    "INSERT INTO `creature` (`id`, `guid`, `map`) VALUES (9000101, 900001, 0), (9000102, 900001, 0)"
)


def test_the_spawn_reader_follows_the_insert_column_list() -> None:
    """`(id, guid)` over values written `(guid, id)` stores guid 900001, not a Mentor spawn."""
    assert _spawns_in([_SWAPPED]) == [(900001, 9000101), (900001, 9000102)]
    assert _spawns_in(["INSERT INTO creature (map, guid, id) VALUES (0, 9000101, 900001)"]) == [
        (9000101, 900001)
    ]


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO creature (guid, map) VALUES (9000101, 0)",
        "INSERT INTO creature (guid, id, `ID`) VALUES (9000101, 900001, 900001)",
        "INSERT INTO creature (guid, id) VALUES (9000101, NULL)",
        "INSERT INTO creature (guid, id) VALUES (9000101)",
    ],
)
def test_the_spawn_reader_refuses_a_row_it_cannot_map(statement: str) -> None:
    with pytest.raises(AssertionError):
        _spawns_in([statement])


def test_the_insert_reader_keeps_commas_and_parens_inside_values() -> None:
    columns, rows = insert_rows(
        "INSERT IGNORE INTO `creature` (`guid`, `Comment`, `id`) VALUES "
        "(1, 'a, (b)', 900001), (2, 'it''s', 900001)"
    )
    assert columns == ["guid", "Comment", "id"]
    assert rows == [["1", "'a, (b)'", "900001"], ["2", "'it''s'", "900001"]]


def test_the_statement_splitter_keeps_a_backslash_escaped_quote_inside_its_string() -> None:
    """`'a\\'; b'` is one string: its `;` and the quote after the backslash do not end it."""
    text = "INSERT INTO t (a) VALUES ('a\\'; b'); DELETE FROM t WHERE a = 1"
    assert split_statements(text) == [
        "INSERT INTO t (a) VALUES ('a\\'; b')",
        "DELETE FROM t WHERE a = 1",
    ]


def test_the_mentor_check_tells_a_player_what_to_press() -> None:
    """The install shows this after \"is missing what it needs:\", so it says what to press."""
    checks = load_catalog().get("wow-unbound").install.native.azerothcore.sql_checks  # type: ignore[union-attr]
    (check,) = [c for c in checks if c.table == "creature"]
    assert "Rebuild" in check.reason and "Where to get help" in check.reason
    assert "spawn ids" not in check.reason


# -- the health line's log markers are lines the pinned module prints at every start -------------

SOURCE_BLOBS = {
    "src/UnboundReagentFree.cpp": "702af0f6a651bedbd7008c7a3b9a178efc9d2c9b",
    "lua_scripts/unbound_mentor.lua": "70e9909aa8c056e12284ba423305b0d9c2272e55",
}
"""Git's blob id of the two source files that print the health markers, at the pinned revision
(`git rev-parse <rev>:<path>`)."""


def _marker_sources() -> str:
    return "\n".join((FIXTURE / name).read_text(encoding="utf-8") for name in sorted(SOURCE_BLOBS))


def test_the_marker_sources_are_the_pinned_modules_own_files() -> None:
    manifest = {
        name: digest
        for digest, _, name in (
            line.partition("  ")
            for line in (FIXTURE / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines()
        )
    }
    for name, blob in SOURCE_BLOBS.items():
        data = (FIXTURE / name).read_bytes()
        assert _git_blob_id(data) == blob, f"{name} is not the pinned revision's file"
        assert hashlib.sha256(data).hexdigest() == manifest[name], name


def test_every_health_marker_is_printed_by_the_pinned_module_source() -> None:
    """Cold review: a marker the module does not print on a start reads 'did not load' on a
    healthy server (the 'Character cleanup covers' line did exactly that on a fresh install)."""
    markers = load_catalog().get("wow-unbound").install.native.azerothcore.health.log_markers  # type: ignore[union-attr]
    source = _marker_sources()
    assert markers, "no markers: nothing below would be checked"
    for marker in markers:
        assert source.count(marker) >= 1, f"the vendored start-up sources never print {marker!r}"
    # The switch lines are printed in both the on and the off branch, so one of them always runs.
    assert source.count("[UNBOUND] free reagents: off") == 1
    assert source.count("[UNBOUND] free reagents: on") == 1
    assert source.count("[UNBOUND] instant summons: off") == 1
    assert source.count("[UNBOUND] instant summons: on") == 1
