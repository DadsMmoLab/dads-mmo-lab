"""What a module leaves in the databases after Remove (T751), and which custom-module SQL
the core updater applies (T752).

T751: `_left_behind()` named install-time SQL only when it was `direct` and only when the
manifest had no remove step at all, so City Bots' 400 accounts and 400 characters (its
db-import SQL) and Battle Pass's tables (it has one remove step, for its vendor NPC) went
unnamed. A module's own uninstall script was never mentioned either.

T752: `module_source._sql_steps()` used its own fixed folder list instead of the updater's
rule (`apply.updater_walks()`, T689): `data/sql/playerbots/` became a step no press ever
applies, and `data/sql/world/` / `data/sql/characters/` got none.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest

from tests.test_apply import OWNED_URL, _FakeGit, _FakeSql, _have_requirements, _Origins
from yulon import apply as apply_module
from yulon import module_source
from yulon.apply import Applier
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import SqlStep, parse_manifest

# ------------------------------------------------------------------ T751 (a) and (b)


def _bp_files() -> dict[str, str]:
    return {
        "lua_scripts/battlepass/01_BP_Core.lua": "-- core",
        "lua_scripts/battlepass/05_BP_Communication.lua": 'require("lib.CSMH.CSMH_SMH")\n',
        "lua_scripts/lib/CSMH/CSMH_SMH.ext": "-- csmh",
        "sql/battlepass_world.sql": "-- world",
        "sql/battlepass_characters.sql": "-- characters",
        "BattlePass/BattlePass.toc": "## Title: BattlePass",
    }


def test_battle_pass_remove_names_the_tables_its_remove_step_does_not_take_back(
    tmp_path: Path,
) -> None:
    """One remove step (the vendor NPC) no longer hides both install files, through the real
    install and the real remove."""
    m = wotlk_modules.store().load("ale", "battlepass")
    applier = Applier(tmp_path, git=_FakeGit(_bp_files()), sql=_FakeSql())
    _have_requirements(tmp_path, m)
    applier.install(m)
    report = applier.remove(m)
    world = [line for line in report.left_behind if "sql/battlepass_world.sql" in line]
    chars = [line for line in report.left_behind if "sql/battlepass_characters.sql" in line]
    assert len(world) == 1 and "world database" in world[0], report.left_behind
    assert len(chars) == 1 and "characters database" in chars[0], report.left_behind


def test_a_statement_its_own_remove_step_takes_back_is_not_named(tmp_path: Path) -> None:
    """Battle Pass's vendor NPC statement is undone by its remove step on the same database."""
    m = wotlk_modules.store().load("ale", "battlepass")
    said = apply_module._left_behind(m)
    assert not any("what its SQL wrote" in line for line in said), said


def test_city_bots_remove_names_its_accounts_characters_and_world_tables() -> None:
    """The db-import groups are named by database, in plain words (finding 2a)."""
    m = wotlk_modules.store().load("module", "mod-city-bots")
    said = apply_module._left_behind(m)
    auth = [line for line in said if "data/sql/db-auth/updates/*.sql" in line]
    chars = [line for line in said if "data/sql/db-characters/updates/*.sql" in line]
    world = [line for line in said if "data/sql/db-world/updates/*.sql" in line]
    assert len(auth) == 1 and "auth database" in auth[0] and "accounts" in auth[0], said
    assert len(chars) == 1 and "characters database" in chars[0], said
    assert "characters or tables" in chars[0], said
    assert len(world) == 1 and "world database" in world[0], said


def test_a_paired_undo_file_is_named_with_what_it_undid() -> None:
    """An install file with a remove file on the same database is named, and so is the undo."""
    m = wotlk_modules.store().load("mod", "portals-capitals")
    said = apply_module._left_behind(m)
    assert said == (
        "what portals-in-all-capitals.up.sql wrote into the world database (tables or rows), "
        "apart from what portals-in-all-capitals.down.sql undid",
    ), said


def test_a_statement_mod_with_its_own_undo_names_nothing() -> None:
    """The mob modifiers change rows with one statement and put them back with another."""
    m = wotlk_modules.store().load("mod", "baby-mobs")
    assert apply_module._left_behind(m) == ()


def test_a_direct_statement_without_an_undo_is_still_named() -> None:
    """The old rule's one true case stays: no remove step on that database, so it is named."""
    m = parse_manifest(
        {
            "schema_version": 1,
            "id": "x",
            "name": "X",
            "type": "mod",
            "game": "wow-wotlk",
            "sql": [
                {"db": "world", "statement": "UPDATE a SET b = 1;"},
                {"db": "characters", "statement": "DELETE FROM c;", "when": "remove"},
            ],
        }
    )
    assert apply_module._left_behind(m) == ("what its SQL wrote into the world database",)


# ------------------------------------------------------------------ T751 uninstall scripts

CITY_BOTS_WORLD_UNINSTALL = """-- mod-city-bots uninstall (acore_world)
-- DROP TABLE IF EXISTS `not_this_one`;
DROP TABLE IF EXISTS `city_bot_poi`;
DELETE FROM `smart_scripts`
 WHERE `source_type` = 0;
DELETE FROM `playercreateinfo_action` WHERE `race` IN (1, 5) AND `class` = 3;
DELETE FROM playercreateinfo WHERE `race` IN (1, 5) AND `class` = 3;
"""

UNINSTALL_ITEM: dict[str, Any] = {
    "schema_version": 1,
    "id": "mod-ah-bot",
    "name": "AH Bot",
    "type": "module",
    "game": "wow-wotlk",
    "source": {"repo": "azerothcore/mod-ah-bot"},
    "sql": [{"db": "world", "path": "data/sql/db-world/updates/*.sql", "applied_by": "db-import"}],
}


def test_remove_names_the_modules_own_uninstall_script_and_what_it_would_do(
    tmp_path: Path,
) -> None:
    """Named, not run: `_FakeSql` records every file and statement sent."""
    files = {
        "data/sql/db-world/updates/2026_01_01_00_poi.sql": "CREATE TABLE city_bot_poi (a INT);",
        "data/sql/uninstall/db-world/uninstall_mod_x.sql": CITY_BOTS_WORLD_UNINSTALL,
    }
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_FakeGit(files), sql=sql, remote_url=_Origins(OWNED_URL))
    m = parse_manifest(UNINSTALL_ITEM)
    applier.install(m)
    report = applier.remove(m)
    assert sql.files == [] and sql.statements == [], "the uninstall script is never run"
    said = [line for line in report.left_behind if "uninstall_mod_x.sql" in line]
    assert said == [
        "the module's own uninstall SQL data/sql/uninstall/db-world/uninstall_mod_x.sql "
        "(in azerothcore/mod-ah-bot), which Yu'lon did not run: against the world database it "
        "would drop the table city_bot_poi and delete rows from smart_scripts, "
        "playercreateinfo_action and playercreateinfo"
    ], report.left_behind


def test_a_delete_folder_counts_as_an_uninstall_script(tmp_path: Path) -> None:
    """mod-1v1-arena's layout: data/sql/delete/1v1_delete.sql."""
    clone = tmp_path / "c"
    (clone / "data/sql/delete").mkdir(parents=True)
    (clone / "data/sql/delete/1v1_delete.sql").write_text(
        "DELETE FROM `creature_template` WHERE `entry`=999991;\n", encoding="utf-8"
    )
    m = parse_manifest(UNINSTALL_ITEM)
    said = apply_module._uninstall_scripts(m, clone)
    assert said == (
        "the module's own uninstall SQL data/sql/delete/1v1_delete.sql (in "
        "azerothcore/mod-ah-bot), which Yu'lon did not run: it would delete rows from "
        "creature_template",
    ), said


def test_an_update_the_updater_walks_is_never_called_an_uninstall_script(tmp_path: Path) -> None:
    """`remove` in an update's name: the updater applies it, so it is no uninstall script.

    The manifest declares no `sql` here, so it is the updater rule alone that leaves the
    file out (with `UNINSTALL_ITEM`'s db-world step, the declared-file rule would too).
    """
    clone = tmp_path / "c"
    (clone / "data/sql/db-world/updates").mkdir(parents=True)
    (clone / "data/sql/db-world/updates/2024_remove_dupes.sql").write_text(
        "DELETE FROM creature WHERE guid = 1;\n", encoding="utf-8"
    )
    bare = parse_manifest({**UNINSTALL_ITEM, "sql": []})
    assert apply_module._uninstall_scripts(bare, clone) == ()


def test_a_remove_step_the_manifest_ran_is_not_called_unrun(tmp_path: Path) -> None:
    """A declared remove-time file was run by Remove, so it is not named as left unrun."""
    clone = tmp_path / "c"
    (clone / "data/sql/uninstall").mkdir(parents=True)
    (clone / "data/sql/uninstall/down.sql").write_text("DROP TABLE t;\n", encoding="utf-8")
    m = parse_manifest(
        {
            **UNINSTALL_ITEM,
            "sql": [{"db": "world", "path": "data/sql/uninstall/down.sql", "when": "remove"}],
        }
    )
    assert apply_module._uninstall_scripts(m, clone) == ()


def test_an_unremarkable_folder_is_not_an_uninstall_script(tmp_path: Path) -> None:
    """City Bots' data/sql/dev/ holds fixups, not an uninstall."""
    clone = tmp_path / "c"
    (clone / "data/sql/dev/db-world").mkdir(parents=True)
    (clone / "data/sql/dev/db-world/fix.sql").write_text("DELETE FROM t;\n", encoding="utf-8")
    assert apply_module._uninstall_scripts(parse_manifest(UNINSTALL_ITEM), clone) == ()


# ------------------------------------------------------------------ T752


def _shipped_sql_folders() -> set[str]:
    """Every folder name directly under data/sql/ that a shipped manifest names."""
    names: set[str] = set()
    for root in sorted(Path(apply_module.__file__).resolve().parent.parent.glob("manifests")):
        for path in root.glob("*/*/*.json"):
            text = path.read_text(encoding="utf-8")
            for part in text.split('"data/sql/')[1:]:
                names.add(part.split("/")[0])
    return names


UPDATER_FOLDERS = sorted(
    {
        spelled
        for db in apply_module.IMPORTER_DB_NAMES
        for spelled in (db, f"db-{db}", f"db_{db}", f"{db}-updates", f"custom_{db}")
    }
    | {
        name
        for name in _shipped_sql_folders()
        if apply_module.updater_walks(f"data/sql/{name}/a.sql")
    }
)
UNWALKED_FOLDERS = ["playerbots", "db-playerbots", "dev", "uninstall", "delete", "optional"]


def test_the_folder_lists_are_what_they_say() -> None:
    """The two lists feed the tests below; a wrong one would make them prove nothing."""
    assert {"world", "db-world", "db_world", "characters", "db-characters", "db-auth"} <= set(
        UPDATER_FOLDERS
    )
    assert all(apply_module.updater_walks(f"data/sql/{n}/x/a.sql") for n in UPDATER_FOLDERS)
    assert not any(apply_module.updater_walks(f"data/sql/{n}/a.sql") for n in UNWALKED_FOLDERS)


@pytest.mark.parametrize("folder", UPDATER_FOLDERS)
def test_every_folder_the_updater_walks_gets_its_step(tmp_path: Path, folder: str) -> None:
    clone = tmp_path / "mod-x"
    (clone / "data/sql" / folder / "base").mkdir(parents=True)
    (clone / "data/sql" / folder / "base/a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    steps = module_source._sql_steps(clone)
    want = tuple(
        SqlStep(db=db, path=f"data/sql/{folder}/**/*.sql", applied_by="db-import")
        for db in apply_module.IMPORTER_DB_NAMES
        if db in folder
    )
    assert steps == want and want, steps


@pytest.mark.parametrize("folder", UNWALKED_FOLDERS)
def test_a_folder_the_updater_never_opens_gets_no_step(tmp_path: Path, folder: str) -> None:
    clone = tmp_path / "mod-x"
    (clone / "data/sql" / folder).mkdir(parents=True)
    (clone / "data/sql" / folder / "a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    assert module_source._sql_steps(clone) == ()


def test_a_derived_playerbots_folder_is_said_to_be_applied_by_nothing(tmp_path: Path) -> None:
    """No step, but a note: the player is told nothing applies it, rather than nothing at all."""
    clone = tmp_path / "mod-x"
    (clone / "src").mkdir(parents=True)
    (clone / "data/sql/playerbots/updates").mkdir(parents=True)
    (clone / "data/sql/playerbots/updates/a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    (clone / "data/sql/db-world").mkdir(parents=True)
    (clone / "data/sql/db-world/b.sql").write_text("SELECT 1;\n", encoding="utf-8")
    base = module_source.derive_folder(clone, "wow-wotlk", today=date(2026, 10, 12))
    done = module_source.complete(base, clone)
    said = [n for n in done.notes if "data/sql/playerbots/" in n]
    assert len(said) == 1 and "Nothing applies" in said[0], done.notes
    again = module_source.complete(done, clone)
    assert again.notes == done.notes, "completing twice does not repeat the note"
    assert module_source.beyond_derived_shape(done) is None


def test_a_carried_description_from_before_the_rule_is_still_a_derivation() -> None:
    """A move package packed by an older Yu'lon carries `data/sql/playerbots/**` steps."""
    m = parse_manifest(
        {
            **UNINSTALL_ITEM,
            "sql": [
                {
                    "db": "playerbots",
                    "path": "data/sql/playerbots/**/*.sql",
                    "applied_by": "db-import",
                },
                {"db": "world", "path": "data/sql/world/**/*.sql", "applied_by": "db-import"},
            ],
        }
    )
    assert module_source.beyond_derived_shape(m) is None


def test_a_carried_step_for_a_folder_no_derivation_names_is_refused() -> None:
    m = parse_manifest(
        {
            **UNINSTALL_ITEM,
            "sql": [{"db": "world", "path": "data/sql/dev/**/*.sql", "applied_by": "db-import"}],
        }
    )
    assert module_source.beyond_derived_shape(m) == "sql"


@pytest.mark.parametrize("folder", ["[w]orld", "my world", "..world.."])
def test_a_walked_folder_with_an_unsafe_name_gets_no_step_and_no_carried_one(
    tmp_path: Path, folder: str
) -> None:
    """A glob character or a space: the derivation and the move check agree on refusing it."""
    clone = tmp_path / "mod-x"
    (clone / "data/sql" / folder).mkdir(parents=True)
    (clone / "data/sql" / folder / "a.sql").write_text("SELECT 1;\n", encoding="utf-8")
    steps = module_source._sql_steps(clone)
    carried = parse_manifest(
        {
            **UNINSTALL_ITEM,
            "sql": [
                {"db": "world", "path": f"data/sql/{folder}/**/*.sql", "applied_by": "db-import"}
            ],
        }
    )
    if folder == "..world..":
        assert [s.db for s in steps] == ["world"]
        assert module_source.beyond_derived_shape(carried) is None
    else:
        assert steps == ()
        assert module_source.beyond_derived_shape(carried) == "sql"
