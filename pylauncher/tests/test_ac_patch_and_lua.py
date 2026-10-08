"""T553: the AzerothCore family patches its sources and lays Lua scripts, from catalog data.

Unbound phase P2 (`.notes/designs/unbound-server-2026-10-07.md` §2.3 items 7-9):
`AzerothCoreData.patches` (the CMaNGOS `SourcePatch`), `lua_scripts` and
`sql_checks`, and the two stages behind them. Driven through a SCRATCH entry --
WotLK's own entry with a block that carries one tiny patch, one Lua file and one
SQL count -- because no shipped entry carries any of them yet, and WotLK, which
declares none, must run exactly the tuple it ran before.

The machine is `tests/support_native.Recorder`, so nothing here is evidence that a
server builds; it is evidence about what each press writes, in what order, and
what it refuses.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from tests.support_native import ENTRY, Recorder
from yulon import docker, resources
from yulon.catalog import native
from yulon.catalog.catalog import LUA_SCRIPTS_DIR, CatalogEntry
from yulon.catalog.families import scriptdeploy
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

PATCH_FILE = "scratch-ac/tiny.patch"
TARGET = "src/server/game/Scratch/Scratch.cpp"
PRE_IMAGE = "int a;\nint b;\nint c;\n"
PATCHED = "int a;\nint b;\nint unbound;\nint c;\n"
TINY_PATCH = (
    f"--- a/{TARGET}\n"
    f"+++ b/{TARGET}\n"
    "@@ -1,3 +1,4 @@\n"
    " int a;\n"
    " int b;\n"
    "+int unbound;\n"
    " int c;\n"
)
MODULE = ENTRY.emulator.sources[1].dest
LUA_SRC = f"{MODULE}/lua_scripts"
LUA_DEST = f"{LUA_SCRIPTS_DIR}/scratch"
LUA_NAME = "scratch_mentor.lua"
LUA_BODY = 'print("[SCRATCH] mentor loaded")\n'
LAID = f"{LUA_DEST}/{LUA_NAME}"


def scratch_entry(*, without_db: str = "", **block: Any) -> CatalogEntry:
    """WotLK's entry with an AzerothCore block carrying `block`, VALIDATED as catalog data is."""
    data = ENTRY.model_dump(mode="json", by_alias=True, exclude_none=True)
    data["id"] = "wow-scratch-ac"
    data["databases"].pop(without_db, None)
    data["install"]["native"]["azerothcore"].update(block)
    return CatalogEntry.model_validate(data)


FULL = {
    "patches": [{"file": PATCH_FILE, "source": ".", "reason": "Scratch: one line added."}],
    "lua_scripts": [{"src": LUA_SRC, "dest": LUA_DEST}],
    "sql_checks": [
        {
            "db": "world",
            "table": "creature_template",
            "where": "entry = 1",
            "reason": "the scratch NPC is not in the world database.",
        }
    ],
}


@pytest.fixture
def installers(tmp_path: Path) -> Path:
    """The shipped installers tree plus the scratch patch, so the templates still render."""
    root = tmp_path / "installers"
    shutil.copytree(resources.installers_dir(), root)
    (root / PATCH_FILE).parent.mkdir(parents=True)
    (root / PATCH_FILE).write_text(TINY_PATCH, encoding="utf-8")
    return root


def lay_tree(server_dir: Path) -> Callable[[Path], None]:
    """What the two clones leave behind: the patch's pre-image in the core, a Lua in the module."""

    def on_clone(dest: Path) -> None:
        if dest == server_dir:
            target = dest / TARGET
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(PRE_IMAGE, encoding="utf-8")
        elif dest == server_dir / MODULE:
            lua = dest / "lua_scripts" / LUA_NAME
            lua.parent.mkdir(parents=True, exist_ok=True)
            lua.write_text(LUA_BODY, encoding="utf-8")

    return on_clone


def make(entry: CatalogEntry, rec: Recorder, installers: Path) -> AzerothCoreInstaller:
    return AzerothCoreInstaller(
        entry,
        installers_root=installers,
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )


def installed(
    tmp_path: Path, installers: Path, entry: CatalogEntry | None = None, **rec_args: Any
) -> tuple[Recorder, Path, AzerothCoreInstaller, list[str]]:
    rec = Recorder(images=False, **rec_args)
    server_dir = tmp_path / "server"
    rec.on_clone = lay_tree(server_dir)
    rec.query_answer = "1\n"
    made = make(entry or scratch_entry(**FULL), rec, installers)
    said = list(made.run(InstallOptions(server_dir=server_dir)))
    return rec, server_dir, made, said


def marker_index(said: list[str], stage: str) -> int:
    return said.index(f"--- {stage}")


# -- WotLK declares neither stage ---------------------------------------------


def test_wotlk_declares_neither_stage_and_runs_the_historical_tuple(installers: Path) -> None:
    rec = Recorder()
    made = make(ENTRY, rec, installers)
    assert made.stage_names() == AzerothCoreInstaller.STAGE_NAMES
    assert "patch-sources" not in made.stage_names()
    assert "lua-and-sql" not in made.stage_names()


def test_wotlk_has_nothing_carried_on_any_route(tmp_path: Path, installers: Path) -> None:
    """The update route's and the rebuild's hooks say nothing and write nothing for WotLK."""
    made = make(ENTRY, Recorder(), installers)
    server_dir = tmp_path / "wotlk"
    server_dir.mkdir()
    assert list(made.check_carried_patches(server_dir)) == []
    assert list(made.apply_carried_patches(server_dir)) == []
    assert list(made.before_rebuild(server_dir, "the rebuild")) == []
    assert list(server_dir.iterdir()) == []


def test_the_scratch_entry_runs_both_stages_where_they_belong(installers: Path) -> None:
    names = make(scratch_entry(**FULL), Recorder(), installers).stage_names()
    assert names.index("clone-modules") < names.index("patch-sources")
    assert names.index("patch-sources") < names.index("generate-compose") < names.index("build")
    assert names.index("import") < names.index("lua-and-sql") < names.index("up")


# -- P2a: patch-sources -------------------------------------------------------


def test_the_patch_is_applied_after_the_clone_and_before_the_build(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, _made, said = installed(tmp_path, installers)

    assert (server_dir / TARGET).read_text(encoding="utf-8") == PATCHED
    assert marker_index(said, "clone-modules") < marker_index(said, "patch-sources")
    assert marker_index(said, "patch-sources") < marker_index(said, "build")
    assert f"Patched {TARGET}." in said, said
    state = native.read_state(
        server_dir, valid=make(scratch_entry(**FULL), rec, installers).stage_names()
    )
    assert state is not None and state.has("patch-sources")


def test_a_second_press_finds_the_patch_there_and_never_applies_it_twice(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.on_clone = None
    said = list(made.run(InstallOptions(server_dir=server_dir)))

    assert (server_dir / TARGET).read_text(encoding="utf-8") == PATCHED
    assert any("already carries the fix" in line for line in said), said


def test_a_patch_that_no_longer_applies_refuses_by_file_and_line_before_the_build(
    tmp_path: Path, installers: Path
) -> None:
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"

    def moved_upstream(dest: Path) -> None:
        lay_tree(server_dir)(dest)
        if dest == server_dir:
            (dest / TARGET).write_text("int a;\nint z;\nint c;\n", encoding="utf-8")

    rec.on_clone = moved_upstream
    made = make(scratch_entry(**FULL), rec, installers)

    with pytest.raises(InstallerError) as raised:
        list(made.run(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert "does not apply" in said and f"{TARGET} no longer has the lines" in said, said
    assert "nothing was changed" in said, said
    assert "build" not in rec.calls
    assert (server_dir / TARGET).read_text(encoding="utf-8") == "int a;\nint z;\nint c;\n"


def test_a_patch_the_build_would_skip_refuses_and_names_the_rebuild(
    tmp_path: Path, installers: Path
) -> None:
    """A finished build whose tree lost the patch: this press would not compile it in."""
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None
    (server_dir / TARGET).write_text(PRE_IMAGE, encoding="utf-8")
    rec.calls.clear()

    with pytest.raises(InstallerError, match="Rebuild the server") as raised:
        list(made.run(InstallOptions(server_dir=server_dir)))

    assert "Nothing was changed" in str(raised.value)
    assert (server_dir / TARGET).read_text(encoding="utf-8") == PRE_IMAGE
    assert "build" not in rec.calls


def test_a_rebuild_keeps_one_copy_and_puts_a_lost_patch_back_before_compiling(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None

    list(made.rebuild(InstallOptions(server_dir=server_dir)))
    assert (server_dir / TARGET).read_text(encoding="utf-8") == PATCHED

    # A fresh checkout of the same commit: the patch is gone from the tree.
    (server_dir / TARGET).write_text(PRE_IMAGE, encoding="utf-8")
    seen: list[str] = []
    rec.calls.clear()
    original_build = rec.seams().build

    def build_reads_the_tree(*args: object, **kwargs: object) -> docker.AttachedRun:
        seen.append((server_dir / TARGET).read_text(encoding="utf-8"))
        return original_build(*args, **kwargs)

    made._seams = rec.seams(build=build_reads_the_tree)
    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == [PATCHED], "the compile saw the tree without the patch"
    assert (server_dir / TARGET).read_text(encoding="utf-8") == PATCHED
    assert f"Patched {TARGET}." in said, said


def test_the_patched_files_are_the_apps_own_to_the_update_guard(
    tmp_path: Path, installers: Path
) -> None:
    from yulon.catalog import composegen

    _rec, server_dir, made, _said = installed(tmp_path, installers)
    owned = made.app_written_paths(server_dir)
    assert set(owned) == {"."}
    assert set(owned["."]) == {*composegen.COMPOSE_FILES, TARGET}


OLD = "a" * 40
NEW = "b" * 40


def ready_to_update(
    tmp_path: Path, installers: Path
) -> tuple[Recorder, Path, AzerothCoreInstaller]:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    for source in ENTRY.emulator.sources:
        rec.heads[server_dir / source.dest] = OLD
        rec.upstream[server_dir / source.dest] = NEW
    from yulon.catalog import composegen

    # The tree as the install left it: the compose file and the patched file are
    # tracked and modified, and both are the app's own.
    rec.edits[server_dir] = (composegen.BASE_FILE, TARGET)
    rec.calls.clear()
    return rec, server_dir, made


def test_an_update_puts_the_patch_back_on_the_reset_checkout_once(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made = ready_to_update(tmp_path, installers)
    # The fetch's `reset --hard`: upstream's unpatched bytes come back.
    rec.on_clone = lay_tree(server_dir)

    said = list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert rec.heads[server_dir] == NEW
    assert (server_dir / TARGET).read_text(encoding="utf-8") == PATCHED
    checked = said.index(
        f"Checking {PATCH_FILE} still applies to the server's source as it now stands."
    )
    patched = said.index(f"Patched {TARGET}.")
    built = said.index("--- build")
    assert checked < patched < built, said


def test_an_update_whose_new_source_no_longer_takes_the_patch_is_put_back(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def moved_under_the_patch(dest: Path) -> None:
        if dest == server_dir:
            (dest / TARGET).write_text("int a;\nint z;\nint c;\n", encoding="utf-8")

    rec.on_clone = moved_under_the_patch

    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert "does not apply" in str(raised.value)
    assert native.SOURCES_PUT_BACK_NOTE in str(raised.value)
    assert rec.heads[server_dir] == OLD
    assert "build" not in rec.calls


# -- P2b: Lua scripts and the SQL check ----------------------------------------


def test_the_lua_lands_before_the_world_first_starts(tmp_path: Path, installers: Path) -> None:
    rec, server_dir, _made, said = installed(tmp_path, installers)

    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY
    record = json.loads(scriptdeploy.record_path(server_dir).read_text(encoding="utf-8"))
    assert LAID in record["files"]
    assert marker_index(said, "import") < marker_index(said, "lua-and-sql")
    assert marker_index(said, "lua-and-sql") < marker_index(said, "up")
    assert f"Laid {LAID}." in said, said


def test_the_sql_check_is_asked_of_the_world_schema_read_only(
    tmp_path: Path, installers: Path
) -> None:
    rec, _server_dir, _made, said = installed(tmp_path, installers)
    asked = [s for s in rec.sql_calls if "creature_template" in s]
    assert asked == [
        "SELECT table_schema, table_name FROM information_schema.tables WHERE "
        "(table_schema = 'acore_world' AND table_name IN ('creature_template'));",
        "SELECT COUNT(*) FROM `acore_world`.`creature_template` WHERE entry = 1;",
    ]
    assert any("has what this server needs" in line for line in said), said


@pytest.mark.parametrize(
    ("answer", "refusal"),
    [
        ("0\n", "scratch NPC is not in the world database"),
        # T555 T3 (N4): a count with no answer is not a count of 0.
        ("", "could not be checked"),
        ("Table missing", "could not be checked"),
    ],
    ids=("zero", "no-answer", "junk"),
)
def test_a_short_count_refuses_before_the_world_starts(
    tmp_path: Path, installers: Path, answer: str, refusal: str
) -> None:
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"
    rec.on_clone = lay_tree(server_dir)
    rec.query_answer = answer
    made = make(scratch_entry(**FULL), rec, installers)

    with pytest.raises(InstallerError, match=refusal):
        list(made.run(InstallOptions(server_dir=server_dir)))
    assert "start" not in rec.calls
    assert not (server_dir / LAID).exists(), "the scripts were laid over a refused database"


def test_a_table_the_import_did_not_make_refuses_naming_it_before_the_world_starts(
    tmp_path: Path, installers: Path
) -> None:
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"
    rec.on_clone = lay_tree(server_dir)
    rec.missing_tables = frozenset({"creature_template"})
    made = make(scratch_entry(**FULL), rec, installers)

    with pytest.raises(InstallerError) as raised:
        list(made.run(InstallOptions(server_dir=server_dir)))

    assert f"{ENTRY.name} tables missing: creature_template" in str(raised.value)
    assert "matching rows" not in str(raised.value)
    assert "start" not in rec.calls
    assert not any(s.startswith("SELECT COUNT") for s in rec.sql_calls if "creature_template" in s)


def test_an_unanswered_or_unreadable_count_refuses_as_could_not_be_checked(
    tmp_path: Path,
) -> None:
    from yulon.catalog.catalog import SqlCheck

    check = SqlCheck(db="world", table="unbound_catalog", reason="no catalog.")
    there = _Db({"w": {"unbound_catalog": "1\n"}})

    def down(schema: str, statement: str) -> str:
        raise docker.DockerCommandError("container is not running")

    # Fail closed (Codex): "could not tell" is not "it is there".
    with pytest.raises(InstallerError, match="could not be checked"):
        list(scriptdeploy.check_sql([check], {"world": "w"}, down, "Scratch"))

    there.counts["unbound_catalog"] = "Warning\n"
    with pytest.raises(InstallerError, match="not a count"):
        list(scriptdeploy.check_sql([check], {"world": "w"}, there, "S"))


# -- T555 T3 (N4): tables first, then counts, and never a false 0 --------------------------------

UNBOUND_TABLES = (
    "unbound_class_catalog",
    "unbound_milestones",
    "unbound_spell_catalog",
    "unbound_talent_bridge",
    "creature_template",
)


class _Db:
    """A fake `ask` over a database that holds `tables` (schema -> table -> count answer).

    It answers `information_schema.tables` the way the mysql client does under
    `--batch --skip-column-names` (one `schema<TAB>table` line per row, nothing for
    no rows), a count with the answer it was given, and a count of a table it does
    not hold with MySQL's own ERROR 1146. `dropped` tables are listed but refuse
    their count: dropped between the two questions. Every statement is kept.
    """

    def __init__(self, tables: dict[str, dict[str, str]], *, dropped: frozenset[str] = frozenset()):
        self.tables = tables
        self.counts = {name: answer for held in tables.values() for name, answer in held.items()}
        self.dropped = dropped
        self.asked: list[str] = []

    def __call__(self, schema: str, statement: str) -> str:
        self.asked.append(statement)
        if "information_schema.tables" in statement:
            return "".join(
                f"{where}\t{name}\n"
                for where, held in self.tables.items()
                for name in held
                if f"'{where}'" in statement and f"'{name}'" in statement
            )
        table = statement.split("`.`")[1].split("`")[0]
        if table in self.dropped or table not in self.tables.get(schema, {}):
            raise docker.DockerCommandError(
                f"ERROR 1146 (42S02) at line 1: Table '{schema}.{table}' doesn't exist"
            )
        return self.counts[table]


def _unbound_checks() -> list[Any]:
    from yulon.catalog.catalog import SqlCheck

    return [
        SqlCheck(db="world", table=name, reason=f"{name} is not filled.") for name in UNBOUND_TABLES
    ]


def _counts(names: tuple[str, ...], answer: str = "1200\n") -> dict[str, str]:
    return {name: answer for name in names}


def test_tables_the_import_did_not_make_are_named_missing_never_counted_as_0() -> None:
    db = _Db({"acore_world": _counts(UNBOUND_TABLES[2:])})

    with pytest.raises(InstallerError) as raised:
        list(scriptdeploy.check_sql(_unbound_checks(), {"world": "acore_world"}, db, "WoW Unbound"))

    text = str(raised.value)
    assert "WoW Unbound tables missing: unbound_class_catalog, unbound_milestones" in text
    assert "0 matching rows" not in text and " 0 " not in text, text
    assert "The server was not started" in text
    counted = [s for s in db.asked if s.startswith("SELECT COUNT")]
    assert not any("unbound_class_catalog" in s or "unbound_milestones" in s for s in counted)
    assert "information_schema.tables" in db.asked[0], "the tables are asked about first"


def test_an_empty_answer_to_a_count_could_not_be_checked_and_is_never_a_0() -> None:
    db = _Db({"acore_world": _counts(UNBOUND_TABLES)})
    db.counts["unbound_milestones"] = ""

    with pytest.raises(InstallerError) as raised:
        list(scriptdeploy.check_sql(_unbound_checks(), {"world": "acore_world"}, db, "WoW Unbound"))

    text = str(raised.value)
    assert "could not be checked" in text and "acore_world.unbound_milestones" in text
    assert "0 matching rows" not in text and "has 0" not in text, text


def test_a_table_dropped_between_the_two_questions_is_missing_not_0() -> None:
    db = _Db({"acore_world": _counts(UNBOUND_TABLES)}, dropped=frozenset({"unbound_talent_bridge"}))

    with pytest.raises(InstallerError) as raised:
        list(scriptdeploy.check_sql(_unbound_checks(), {"world": "acore_world"}, db, "WoW Unbound"))

    text = str(raised.value)
    assert "WoW Unbound tables missing: unbound_talent_bridge" in text
    assert "0 matching rows" not in text, text


def test_no_table_at_all_is_every_table_missing() -> None:
    db = _Db({"acore_world": {}})

    with pytest.raises(
        InstallerError, match="tables missing: " + ", ".join(sorted(UNBOUND_TABLES))
    ):
        list(scriptdeploy.check_sql(_unbound_checks(), {"world": "acore_world"}, db, "WoW Unbound"))


@pytest.mark.parametrize(
    "answer",
    ["acore_world unbound_milestones\n", "Warning: Using a password\n"],
    ids=("no-tab", "a-warning"),
)
def test_a_tables_answer_that_is_not_rows_could_not_be_checked(answer: str) -> None:
    def ask(schema: str, statement: str) -> str:
        return answer

    with pytest.raises(InstallerError, match="could not be checked"):
        list(
            scriptdeploy.check_sql(_unbound_checks(), {"world": "acore_world"}, ask, "WoW Unbound")
        )


def test_a_database_that_does_not_answer_the_tables_question_could_not_be_checked() -> None:
    def down(schema: str, statement: str) -> str:
        raise docker.DockerCommandError("ERROR 2002 (HY000): Can't connect to local MySQL server")

    with pytest.raises(InstallerError) as raised:
        list(
            scriptdeploy.check_sql(_unbound_checks(), {"world": "acore_world"}, down, "WoW Unbound")
        )

    assert "could not be checked" in str(raised.value)
    assert "missing" not in str(raised.value)


def test_every_table_there_and_filled_says_what_it_said_before() -> None:
    db = _Db({"acore_world": _counts(UNBOUND_TABLES)})
    db.counts["creature_template"] = "34000\n"

    said = list(
        scriptdeploy.check_sql(_unbound_checks(), {"world": "acore_world"}, db, "WoW Unbound")
    )

    assert said == [
        *(
            f"acore_world.{name} has what this server needs (1200 rows)."
            for name in UNBOUND_TABLES[:4]
        ),
        "acore_world.creature_template has what this server needs (34000 rows).",
    ]


def test_a_short_table_says_its_real_count() -> None:
    checks = _unbound_checks()
    checks[2] = checks[2].model_copy(update={"at_least": 1000})
    db = _Db({"acore_world": _counts(UNBOUND_TABLES)})
    db.counts["unbound_spell_catalog"] = "12\n"

    with pytest.raises(InstallerError) as raised:
        list(scriptdeploy.check_sql(checks, {"world": "acore_world"}, db, "WoW Unbound"))

    assert str(raised.value) == (
        "WoW Unbound's world database is missing what it needs: unbound_spell_catalog is not "
        "filled. (acore_world.unbound_spell_catalog has 12 matching rows, at least 1000 are "
        "needed). The server was not started."
    )


def test_read_checks_gives_the_reading_without_wording_it() -> None:
    """The pure half T5's Server-tab line builds on: what is missing, short and counted."""
    checks = _unbound_checks()
    db = _Db({"acore_world": _counts(UNBOUND_TABLES[1:])})
    db.counts["unbound_talent_bridge"] = "0\n"

    reading = scriptdeploy.read_checks(checks, {"world": "acore_world"}, db)

    assert reading.missing == ("unbound_class_catalog",)
    assert reading.short == ((checks[3], 0),)
    assert [found for _check, found in reading.counts] == [1200, 1200, 0, 1200]


def test_a_schema_name_that_is_not_one_plain_name_is_never_spliced_into_the_question() -> None:
    db = _Db({"acore_world": _counts(UNBOUND_TABLES)})

    with pytest.raises(scriptdeploy.ChecksUnreadable, match="not one plain schema name"):
        scriptdeploy.read_checks(_unbound_checks(), {"world": "w' OR '1'='1"}, db)

    assert db.asked == []


def test_an_edited_lua_is_kept_and_said_on_the_next_press(tmp_path: Path, installers: Path) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.on_clone = None
    (server_dir / LAID).write_text("-- mine\n", encoding="utf-8")
    shipped = server_dir / LUA_SRC / LUA_NAME
    shipped.write_text('print("v2")\n', encoding="utf-8")

    said = list(made.run(InstallOptions(server_dir=server_dir)))

    assert (server_dir / LAID).read_text(encoding="utf-8") == "-- mine\n"
    assert any(line.startswith(f"{LAID} was changed on this machine") for line in said), said


def test_yulons_own_copy_follows_the_shipped_one(tmp_path: Path, installers: Path) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.on_clone = None
    (server_dir / LUA_SRC / LUA_NAME).write_text('print("v2")\n', encoding="utf-8")

    said = list(made.run(InstallOptions(server_dir=server_dir)))

    assert (server_dir / LAID).read_text(encoding="utf-8") == 'print("v2")\n'
    assert f"Updated {LAID}." in said, said


def test_a_script_no_longer_shipped_goes_only_if_it_is_still_yulons(tmp_path: Path) -> None:
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "a.lua").write_text("a\n", encoding="utf-8")
    (src / "b.lua").write_text("b\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")
    list(scriptdeploy.lay(server_dir, [spec]))
    laid = server_dir / LUA_SCRIPTS_DIR / "m"
    (laid / "b.lua").write_text("b, edited\n", encoding="utf-8")
    (src / "a.lua").unlink()
    (src / "b.lua").unlink()
    (src / "c.lua").write_text("c\n", encoding="utf-8")

    said = list(scriptdeploy.lay(server_dir, [spec]))

    assert not (laid / "a.lua").exists()
    assert (laid / "b.lua").read_text(encoding="utf-8") == "b, edited\n"
    assert (laid / "c.lua").read_text(encoding="utf-8") == "c\n"
    assert f"Removed {LUA_SCRIPTS_DIR}/m/a.lua: this server no longer ships it." in said, said


def test_a_file_that_was_there_before_yulon_is_never_replaced(tmp_path: Path) -> None:
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    (server_dir / "modules/m/lua").mkdir(parents=True)
    (server_dir / "modules/m/lua/a.lua").write_text("shipped\n", encoding="utf-8")
    laid = server_dir / LUA_SCRIPTS_DIR / "m" / "a.lua"
    laid.parent.mkdir(parents=True)
    laid.write_text("hand-installed\n", encoding="utf-8")

    said = list(
        scriptdeploy.lay(server_dir, [LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")])
    )

    assert laid.read_text(encoding="utf-8") == "hand-installed\n"
    assert any("was changed on this machine" in line for line in said), said


def test_a_rebuild_lays_the_scripts_again(tmp_path: Path, installers: Path) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None
    (server_dir / LAID).unlink()

    list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY


def test_an_update_lays_the_moved_checkouts_scripts(tmp_path: Path, installers: Path) -> None:
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def fetched(dest: Path) -> None:
        lay_tree(server_dir)(dest)
        if dest == server_dir / MODULE:
            (dest / "lua_scripts" / LUA_NAME).write_text('print("new")\n', encoding="utf-8")

    rec.on_clone = fetched

    list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert (server_dir / LAID).read_text(encoding="utf-8") == 'print("new")\n'


def test_an_update_whose_new_source_lost_its_scripts_is_put_back(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def fetched(dest: Path) -> None:
        lay_tree(server_dir)(dest)
        if dest == server_dir / MODULE:
            shutil.rmtree(dest / "lua_scripts")

    rec.on_clone = fetched

    with pytest.raises(InstallerError, match=f"have no {LUA_SRC}"):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert rec.heads[server_dir / MODULE] == OLD
    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY


NEW_LUA = 'print("new")\n'


def watch_the_lua(rec: Recorder, server_dir: Path) -> list[tuple[str, str | None]]:
    """What the laid script holds at each stop and each replace of the world (T562).

    The fake seams answer `stop_servers` and `recreate` in the order the press asks,
    so a script that changed between two entries was laid between those two asks.
    """
    seen: list[tuple[str, str | None]] = []

    def at(what: str) -> Callable[[object], None]:
        def snap(_control: object) -> None:
            laid = server_dir / LAID
            seen.append((what, laid.read_text(encoding="utf-8") if laid.exists() else None))

        return snap

    rec.on_stop_servers = at("stop")
    rec.on_recreate = at("replace")
    return seen


def test_a_rebuild_lays_the_new_lua_only_after_the_old_world_stopped(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None
    (server_dir / MODULE / "lua_scripts" / LUA_NAME).write_text(NEW_LUA, encoding="utf-8")
    rec.calls.clear()
    seen = watch_the_lua(rec, server_dir)

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == [("stop", LUA_BODY), ("replace", NEW_LUA)], seen
    assert rec.calls.index("stop_servers") < rec.calls.index("recreate"), rec.calls
    assert (server_dir / LAID).read_text(encoding="utf-8") == NEW_LUA
    assert f"Updated {LAID}." in said, said


def test_an_update_lays_the_new_lua_only_after_the_old_world_stopped(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def fetched(dest: Path) -> None:
        lay_tree(server_dir)(dest)
        if dest == server_dir / MODULE:
            (dest / "lua_scripts" / LUA_NAME).write_text(NEW_LUA, encoding="utf-8")

    rec.on_clone = fetched
    seen = watch_the_lua(rec, server_dir)

    said = list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert seen == [("stop", LUA_BODY), ("replace", NEW_LUA)], seen
    assert (server_dir / LAID).read_text(encoding="utf-8") == NEW_LUA
    assert f"Updated {LAID}." in said, said


def test_a_rebuild_with_unchanged_lua_lays_nothing(
    tmp_path: Path, installers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None
    written: list[Path] = []
    real = scriptdeploy._publish
    monkeypatch.setattr(scriptdeploy, "_publish", lambda t, d: (written.append(t), real(t, d)))

    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert written == [], written
    assert not [line for line in said if LAID in line or "Lua scripts" in line], said


def test_a_rebuild_whose_lua_source_is_a_link_refuses_before_anything_is_touched(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None
    source = server_dir / MODULE / "lua_scripts" / LUA_NAME
    elsewhere = tmp_path / "elsewhere.lua"
    elsewhere.write_text(NEW_LUA, encoding="utf-8")
    source.unlink()
    source.symlink_to(elsewhere)
    rec.calls.clear()

    with pytest.raises(InstallerError, match="is a link"):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert not {"build", "stop_servers", "recreate"} & set(rec.calls), rec.calls
    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY


def test_a_script_that_cannot_be_laid_with_the_world_stopped_rolls_the_rebuild_back(
    tmp_path: Path, installers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None
    (server_dir / MODULE / "lua_scripts" / LUA_NAME).write_text(NEW_LUA, encoding="utf-8")
    monkeypatch.setattr(
        scriptdeploy, "_publish", lambda *_a: (_ for _ in ()).throw(OSError(28, "full"))
    )
    rec.calls.clear()

    with pytest.raises(InstallerError, match="could not be laid"):
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.calls.index("stop_servers") < rec.calls.index("recreate"), rec.calls
    assert rec.calls.count("recreate") == 1, "only the rollback's start: the new build never began"
    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY


def _spec(src: str) -> Any:
    from yulon.catalog.catalog import LuaScripts

    return LuaScripts(src=src, dest=f"{LUA_SCRIPTS_DIR}/m")


def _plain_source(tmp_path: Path) -> Path:
    server_dir = tmp_path / "srv"
    (server_dir / "modules/m/lua").mkdir(parents=True)
    (server_dir / "modules/m/lua/a.lua").write_text("a\n", encoding="utf-8")
    return server_dir


def test_a_lua_source_that_is_a_link_is_refused_whole(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.lua").write_text("x\n", encoding="utf-8")
    (server_dir / "modules/m").mkdir(parents=True)
    (server_dir / "modules/m/lua").symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(InstallerError, match="is a link"):
        list(scriptdeploy.lay(server_dir, [_spec("modules/m/lua")]))

    assert not (server_dir / LUA_SCRIPTS_DIR).exists()
    assert not scriptdeploy.record_path(server_dir).exists()


def test_a_linked_file_in_the_source_refuses_before_any_script_is_laid(tmp_path: Path) -> None:
    server_dir = _plain_source(tmp_path)
    (server_dir / "modules/m/real.txt").write_text("r\n", encoding="utf-8")
    (server_dir / "modules/m/lua/b.lua").symlink_to(server_dir / "modules/m/real.txt")

    with pytest.raises(InstallerError, match="b.lua"):
        list(scriptdeploy.lay(server_dir, [_spec("modules/m/lua")]))

    assert not (server_dir / LUA_SCRIPTS_DIR / "m" / "a.lua").exists()
    assert not scriptdeploy.record_path(server_dir).exists()


def test_a_linked_subfolder_in_the_source_refuses_before_any_script_is_laid(
    tmp_path: Path,
) -> None:
    server_dir = _plain_source(tmp_path)
    (server_dir / "modules/m/other").mkdir()
    (server_dir / "modules/m/lua/sub").symlink_to(
        server_dir / "modules/m/other", target_is_directory=True
    )

    with pytest.raises(InstallerError, match="sub"):
        list(scriptdeploy.lay(server_dir, [_spec("modules/m/lua")]))

    assert not (server_dir / LUA_SCRIPTS_DIR / "m" / "a.lua").exists()


def test_an_update_whose_new_source_is_a_link_is_refused_and_put_back(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def fetched(dest: Path) -> None:
        lay_tree(server_dir)(dest)
        if dest == server_dir / MODULE:
            real = dest / "lua_scripts"
            real.rename(dest / "lua_scripts_copy")
            real.symlink_to(dest / "lua_scripts_copy", target_is_directory=True)

    rec.on_clone = fetched

    with pytest.raises(InstallerError, match="as a link") as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))
    assert "Nothing was built" in str(raised.value)
    assert rec.heads[server_dir / MODULE] == OLD
    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY


def fail_the_final_record_write(monkeypatch: pytest.MonkeyPatch, *, on_call: int = 1) -> None:
    """Make the record's closing save fail (the `pending` saves before each file still work).

    `on_call` counts closing saves only: 1 fails the first, 2 the second.
    """
    real = scriptdeploy._write_record
    closing: list[int] = []

    def write(root: Path, files: dict[str, str], pending: dict[str, str] | None = None) -> None:
        if pending is None:
            closing.append(1)
            if len(closing) >= on_call:
                raise OSError(28, "No space left on device")
        real(root, files, pending)

    monkeypatch.setattr(scriptdeploy, "_write_record", write)


def test_a_failed_record_write_stops_the_press_and_says_how_to_recover(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _plain_source(tmp_path)

    fail_the_final_record_write(monkeypatch)
    said: list[str] = []
    with pytest.raises(InstallerError, match="delete") as raised:
        for line in scriptdeploy.lay(server_dir, [_spec("modules/m/lua")]):
            said.append(line)
    assert scriptdeploy.RECORD_FILE in str(raised.value)
    assert not any("Lua scripts are in place" in line for line in said), said


def test_a_failed_record_write_keeps_the_world_from_starting(
    tmp_path: Path, installers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fail_the_final_record_write(monkeypatch)
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"
    rec.on_clone = lay_tree(server_dir)
    rec.query_answer = "1\n"
    made = make(scratch_entry(**FULL), rec, installers)

    with pytest.raises(InstallerError, match=scriptdeploy.RECORD_FILE):
        list(made.run(InstallOptions(server_dir=server_dir)))
    assert "up" not in rec.calls


def test_a_write_that_fails_mid_loop_keeps_its_own_sentence_over_the_record_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _plain_source(tmp_path)

    def broken(*_args: Any) -> None:
        raise OSError(28, "No space left on device")

    fail_the_final_record_write(monkeypatch)
    monkeypatch.setattr(scriptdeploy, "_publish", broken)
    with pytest.raises(InstallerError, match="could not be laid"):
        list(scriptdeploy.lay(server_dir, [_spec("modules/m/lua")]))


def test_a_failing_stale_removal_is_not_hidden_by_a_finished_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _plain_source(tmp_path)
    list(scriptdeploy.lay(server_dir, [_spec("modules/m/lua")]))
    (server_dir / "modules/m/lua/a.lua").unlink()
    (server_dir / "modules/m/lua/c.lua").write_text("c\n", encoding="utf-8")
    real_unlink = Path.unlink

    def refuse(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == "a.lua":
            raise PermissionError(13, "denied")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    fail_the_final_record_write(monkeypatch)
    with pytest.raises(InstallerError, match="could not be laid"):
        list(scriptdeploy.lay(server_dir, [_spec("modules/m/lua")]))


def test_closing_the_laying_early_never_raises_for_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir = _plain_source(tmp_path)

    fail_the_final_record_write(monkeypatch)
    gen = scriptdeploy.lay(server_dir, [_spec("modules/m/lua")])
    assert next(gen).startswith("Laid")
    gen.close()


# -- the catalog data ------------------------------------------------------------


@pytest.mark.parametrize(
    "dest",
    [LUA_SCRIPTS_DIR, f"{LUA_SCRIPTS_DIR}/", "env/dist/etc/modules/x", f"{LUA_SCRIPTS_DIR}/../x"],
)
def test_a_lua_dest_must_be_a_folder_of_its_own_under_the_script_dir(dest: str) -> None:
    with pytest.raises(ValidationError):
        scratch_entry(lua_scripts=[{"src": LUA_SRC, "dest": dest}])


@pytest.mark.parametrize(
    "where",
    [
        "1; DROP TABLE x",
        "1 -- x",
        "1 /* x */",
        "`a` = 1",
        "entry = 1 INTO OUTFILE '/tmp/x'",
        "entry = 1 UNION SELECT 1",
        "SLEEP(5) = 0",
        "entry IN (SELECT 1)",
        "entry = 1 OR 1 = 1",
        "name = 'a' 'b'",
    ],
)
def test_an_sql_check_is_comparisons_only(where: str) -> None:
    with pytest.raises(ValidationError):
        scratch_entry(sql_checks=[{"db": "world", "table": "t", "where": where, "reason": "r"}])


def test_an_sql_check_on_a_database_the_entry_lacks_is_refused() -> None:
    with pytest.raises(ValidationError, match="does not name"):
        scratch_entry(without_db="ale", sql_checks=[{"db": "ale", "table": "t", "reason": "r"}])


def test_a_patch_inside_a_folder_no_source_clones_is_refused() -> None:
    with pytest.raises(ValidationError, match="not a dest"):
        scratch_entry(patches=[{"file": PATCH_FILE, "source": "modules/nothing", "reason": "r"}])


def test_a_refused_update_patches_the_put_back_checkout_again(
    tmp_path: Path, installers: Path
) -> None:
    """The restore's `checkout --force` puts the OLD commit's unpatched bytes back.

    `_restore_the_folder()` then writes the carried patch again through
    `apply_carried_patches()`; without it the folder would hold the old commit
    without its patch, and the next Rebuild would compile that.
    """
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def moved_under_the_patch(dest: Path) -> None:
        if dest == server_dir:
            (dest / TARGET).write_text("int a;\nint z;\nint c;\n", encoding="utf-8")

    def checkout_force(dest: Path, rev: str) -> None:
        rec.restore_rev(dest, rev)
        lay_tree(server_dir)(dest)

    rec.on_clone = moved_under_the_patch
    made._seams = rec.seams(restore_rev=checkout_force)

    with pytest.raises(InstallerError, match="does not apply"):
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert rec.heads[server_dir] == OLD
    assert (server_dir / TARGET).read_text(encoding="utf-8") == PATCHED


@pytest.mark.parametrize("where", ["entry = 900001", "ID >= 10000 AND ClassMask = 0", "n <> 'x y'"])
def test_an_sql_check_takes_plain_comparisons(where: str) -> None:
    scratch_entry(sql_checks=[{"db": "world", "table": "t", "where": where, "reason": "r"}])


def test_a_file_that_was_there_first_and_matches_is_not_claimed(tmp_path: Path) -> None:
    """Identical bytes are not ownership: a later shipped change must not replace it (Codex)."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "a.lua").write_text("same\n", encoding="utf-8")
    laid = server_dir / LUA_SCRIPTS_DIR / "m" / "a.lua"
    laid.parent.mkdir(parents=True)
    laid.write_text("same\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")

    list(scriptdeploy.lay(server_dir, [spec]))
    assert f"{LUA_SCRIPTS_DIR}/m/a.lua" not in scriptdeploy.read_record(server_dir)
    (src / "a.lua").write_text("shipped v2\n", encoding="utf-8")
    said = list(scriptdeploy.lay(server_dir, [spec]))

    assert laid.read_text(encoding="utf-8") == "same\n"
    assert any("was changed on this machine" in line for line in said), said


@pytest.mark.parametrize(
    "key",
    ["../outside.lua", f"{LUA_SCRIPTS_DIR}/../../../../../../outside.lua", "/abs/outside.lua"],
)
def test_a_record_key_outside_the_script_folder_is_never_deleted(tmp_path: Path, key: str) -> None:
    """The record is an editable file; its keys are not trusted as deletion paths (Codex)."""
    import hashlib

    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    (server_dir / "modules/m/lua").mkdir(parents=True)
    (server_dir / "modules/m/lua/a.lua").write_text("a\n", encoding="utf-8")
    outside = tmp_path / "outside.lua"
    outside.write_text("victim\n", encoding="utf-8")
    digest = hashlib.sha256(b"victim\n").hexdigest()
    record = scriptdeploy.record_path(server_dir)
    record.parent.mkdir(parents=True)
    target = str(outside) if key.startswith("/") else key
    record.write_text(json.dumps({"version": 1, "files": {target: digest}}), encoding="utf-8")

    list(
        scriptdeploy.lay(server_dir, [LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")])
    )

    assert outside.read_text(encoding="utf-8") == "victim\n"


def test_a_recorded_folder_that_became_a_link_is_not_deleted_through(tmp_path: Path) -> None:
    """A stale script under a folder now linked elsewhere stays where the link points (Codex)."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "a.lua").write_text("a\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")
    list(scriptdeploy.lay(server_dir, [spec]))
    laid_dir = server_dir / LUA_SCRIPTS_DIR / "m"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "a.lua").write_text("a\n", encoding="utf-8")
    shutil.rmtree(laid_dir)
    laid_dir.symlink_to(elsewhere, target_is_directory=True)
    (src / "a.lua").unlink()
    (src / "b.lua").write_text("b\n", encoding="utf-8")

    said = list(
        scriptdeploy.lay(server_dir, [LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/n")])
    )

    assert (elsewhere / "a.lua").read_text(encoding="utf-8") == "a\n"
    assert any("nothing was removed through it" in line for line in said), said


# -- cold review (T553 rework) -------------------------------------------------------


@pytest.mark.parametrize("linked", [LUA_SCRIPTS_DIR, f"{LUA_SCRIPTS_DIR}/m"], ids=("root", "dest"))
def test_a_linked_script_folder_refuses_and_nothing_is_written_through_it(
    tmp_path: Path, linked: str
) -> None:
    """The script folder (or the entry's own folder in it) is a link: the press refuses.

    Nothing lands where the link points -- no script, and no record either.
    """
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    (server_dir / "modules/m/lua").mkdir(parents=True)
    (server_dir / "modules/m/lua/a.lua").write_text("a\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = server_dir / linked
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(elsewhere, target_is_directory=True)
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")

    with pytest.raises(InstallerError, match="is a link") as raised:
        list(scriptdeploy.lay(server_dir, [spec]))

    assert "Nothing was written" in str(raised.value)
    assert sorted(p.relative_to(elsewhere).as_posix() for p in elsewhere.rglob("*")) == []


def test_a_linked_folder_inside_the_scripts_refuses_and_writes_nothing_there(
    tmp_path: Path,
) -> None:
    """A subfolder of the entry's dest that is a link (the per-file guard)."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    (server_dir / "modules/m/lua/sub").mkdir(parents=True)
    (server_dir / "modules/m/lua/sub/a.lua").write_text("a\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    dest = server_dir / LUA_SCRIPTS_DIR / "m"
    dest.mkdir(parents=True)
    (dest / "sub").symlink_to(elsewhere, target_is_directory=True)
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")

    with pytest.raises(InstallerError, match="is a link"):
        list(scriptdeploy.lay(server_dir, [spec]))

    assert list(elsewhere.iterdir()) == []


def test_a_resumed_install_on_a_built_and_patched_tree_goes_through(
    tmp_path: Path, installers: Path
) -> None:
    """The ordinary second press: build done, images there, patch on disk. No refusal."""
    rec, server_dir, made, _said = installed(tmp_path, installers)
    rec.images = True
    rec.on_clone = None
    rec.calls.clear()

    said = list(made.run(InstallOptions(server_dir=server_dir)))

    assert "build" not in rec.calls, "the resume compiled a finished build"
    assert any("already carries the fix" in line for line in said), said
    assert (server_dir / TARGET).read_text(encoding="utf-8") == PATCHED


def test_an_unreadable_record_is_said_with_what_to_delete(tmp_path: Path) -> None:
    """A corrupt record keeps every script (nothing counts as Yu'lon's) and says how out."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    (server_dir / "modules/m/lua").mkdir(parents=True)
    (server_dir / "modules/m/lua/a.lua").write_text("v2\n", encoding="utf-8")
    laid = server_dir / LUA_SCRIPTS_DIR / "m" / "a.lua"
    laid.parent.mkdir(parents=True)
    laid.write_text("v1\n", encoding="utf-8")
    scriptdeploy.record_path(server_dir).write_text("{not json", encoding="utf-8")

    said = list(
        scriptdeploy.lay(server_dir, [LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")])
    )

    assert laid.read_text(encoding="utf-8") == "v1\n"
    told = [line for line in said if scriptdeploy.RECORD_FILE in line]
    assert told and "could not be read" in told[0], said
    assert f"{LUA_SCRIPTS_DIR}/m" in told[0] and "delete" in told[0], told


def test_a_linked_script_folder_never_has_its_record_rewritten(tmp_path: Path) -> None:
    """With nothing to lay, only the record would be written -- through the link. Refused."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    (server_dir / "modules/m/lua").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    record = elsewhere / scriptdeploy.RECORD_FILE
    stale = f"{LUA_SCRIPTS_DIR}/m/old.lua"
    record.write_text(json.dumps({"version": 1, "files": {stale: "0" * 64}}), encoding="utf-8")
    before = record.read_bytes()
    root = server_dir / LUA_SCRIPTS_DIR
    root.parent.mkdir(parents=True)
    root.symlink_to(elsewhere, target_is_directory=True)

    with pytest.raises(InstallerError, match="is a link"):
        list(
            scriptdeploy.lay(
                server_dir, [LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")]
            )
        )

    assert record.read_bytes() == before
    assert sorted(p.name for p in elsewhere.iterdir()) == [scriptdeploy.RECORD_FILE]


def failed_start_after_laying(
    tmp_path: Path, installers: Path
) -> tuple[Recorder, Path, list[str], list[tuple[str, str | None]]]:
    """An update whose new checkout changes one script and adds one; the new world never comes up.

    The old world is stopped, the new scripts are laid, the new build fails its ready
    wait, and the rollback starts the old build again (it answers ready).
    """
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def fetched(dest: Path) -> None:
        lay_tree(server_dir)(dest)
        if dest == server_dir / MODULE:
            (dest / "lua_scripts" / LUA_NAME).write_text(NEW_LUA, encoding="utf-8")
            (dest / "lua_scripts" / "extra.lua").write_text("extra\n", encoding="utf-8")

    def checkout_force(dest: Path, rev: str) -> None:
        rec.restore_rev(dest, rev)
        if dest == server_dir / MODULE:
            (dest / "lua_scripts" / "extra.lua").unlink(missing_ok=True)
        lay_tree(server_dir)(dest)

    answers = [False, True]

    def wait_ready(_spec: object, _ready: object) -> bool:
        return answers.pop(0) if len(answers) > 1 else answers[0]

    rec.on_clone = fetched
    made._seams = rec.seams(restore_rev=checkout_force, wait_ready=wait_ready)
    seen = watch_the_lua(rec, server_dir)
    said: list[str] = []
    with pytest.raises(InstallerError):
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)
    return rec, server_dir, said, seen


def test_a_failed_start_after_laying_puts_the_old_scripts_back_with_the_servers_down(
    tmp_path: Path, installers: Path
) -> None:
    """Rollback re-lays the OLD set: the checkout goes back, so the scripts must follow it.

    The put-back tests above refuse in `check_carried_patches` before anything is
    laid, so "LAID unchanged" proves nothing there. Here the new checkout's scripts
    ARE laid (one changed, one new) with the old world stopped, the new world then
    fails its ready wait, the rollback stops it again, and the old bytes are back
    BEFORE the old build starts; the new-only file goes, and the record agrees with
    the disk.
    """
    rec, server_dir, said, seen = failed_start_after_laying(tmp_path, installers)

    assert f"Updated {LAID}." in said and f"Laid {LUA_DEST}/extra.lua." in said, said
    assert seen == [
        ("stop", LUA_BODY),
        ("replace", NEW_LUA),
        ("stop", NEW_LUA),
        ("replace", LUA_BODY),
    ], seen
    assert rec.heads[server_dir / MODULE] == OLD
    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY
    assert not (server_dir / LUA_DEST / "extra.lua").exists()
    assert scriptdeploy.read_record(server_dir) == {LAID: scriptdeploy._sha(LUA_BODY.encode())}


def test_a_failed_compile_never_lays_the_new_scripts(tmp_path: Path, installers: Path) -> None:
    """The old world runs through the compile, so it must still be reading the old scripts."""
    rec, server_dir, made = ready_to_update(tmp_path, installers)

    def fetched(dest: Path) -> None:
        lay_tree(server_dir)(dest)
        if dest == server_dir / MODULE:
            (dest / "lua_scripts" / LUA_NAME).write_text(NEW_LUA, encoding="utf-8")

    def checkout_force(dest: Path, rev: str) -> None:
        rec.restore_rev(dest, rev)
        lay_tree(server_dir)(dest)

    rec.on_clone = fetched
    made._seams = rec.seams(restore_rev=checkout_force)
    rec.build_result = docker.AttachedRun(1, ("boom",))
    seen = watch_the_lua(rec, server_dir)
    said: list[str] = []
    with pytest.raises(InstallerError):
        for line in made.update_to_latest(InstallOptions(server_dir=server_dir)):
            said.append(line)

    assert seen == [], seen
    assert (server_dir / LAID).read_text(encoding="utf-8") == LUA_BODY
    assert not [line for line in said if line.startswith(("Updated", "Laid"))], said


def test_a_rollback_that_cannot_save_the_script_record_says_that_not_the_patch(
    tmp_path: Path, installers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The re-lay's own sentence goes through; "press again" would not mend a full disk."""
    fail_the_final_record_write(monkeypatch, on_call=3)

    _rec, _server_dir, said, _seen = failed_start_after_laying(tmp_path, installers)

    back = next(line for line in said if "back on their old commits, but" in line)
    assert scriptdeploy.RECORD_FILE in back and "delete" in back, back
    assert "source patch" not in back and "again: it writes" not in back, back


@pytest.mark.parametrize("dangling", [False, True], ids=("to-a-file", "to-nothing"))
def test_a_link_planted_at_the_temp_name_is_not_written_through(
    tmp_path: Path, dangling: bool
) -> None:
    """`_publish` writes a temp sibling, then renames it; a link at that name leads elsewhere."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "a.lua").write_text("shipped\n", encoding="utf-8")
    dest = server_dir / LUA_SCRIPTS_DIR / "m"
    dest.mkdir(parents=True)
    victim = tmp_path / "victim.txt"
    if not dangling:
        victim.write_text("keep me\n", encoding="utf-8")
    try:
        (dest / ".a.lua.yulon-new").symlink_to(victim)
    except OSError:  # pragma: no cover - a Windows account without the link privilege
        pytest.skip("cannot make a symlink here")

    list(
        scriptdeploy.lay(server_dir, [LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")])
    )

    assert (dest / "a.lua").read_text(encoding="utf-8") == "shipped\n"
    assert not (dest / "a.lua").is_symlink()
    assert (victim.read_text(encoding="utf-8") if victim.exists() else None) == (
        None if dangling else "keep me\n"
    )
    assert not (dest / ".a.lua.yulon-new").is_symlink() and not (dest / ".a.lua.yulon-new").exists()


def test_an_entry_that_drops_all_its_scripts_removes_the_ones_it_laid(tmp_path: Path) -> None:
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "a.lua").write_text("a\n", encoding="utf-8")
    (src / "b.lua").write_text("b\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")
    laid = server_dir / LUA_SCRIPTS_DIR / "m"
    list(scriptdeploy.lay(server_dir, [spec]))
    (laid / "b.lua").write_text("edited by hand\n", encoding="utf-8")
    assert scriptdeploy.record_path(server_dir).exists()

    said = list(scriptdeploy.lay(server_dir, [], quiet=True))

    assert not (laid / "a.lua").exists()
    assert (laid / "b.lua").read_text(encoding="utf-8") == "edited by hand\n"
    assert "Removed" in " ".join(said) and "changed on this machine" in " ".join(said), said
    assert not any("in place" in line for line in said), said
    assert not scriptdeploy.record_path(server_dir).exists()


def test_a_rebuild_of_an_entry_that_dropped_its_scripts_removes_them(
    tmp_path: Path, installers: Path
) -> None:
    rec, server_dir, _made, _said = installed(tmp_path, installers)
    assert (server_dir / LAID).exists()
    rec.images = True
    rec.on_clone = None
    dropped = make(scratch_entry(**{**FULL, "lua_scripts": []}), rec, installers)
    seen = watch_the_lua(rec, server_dir)

    said = list(dropped.rebuild(InstallOptions(server_dir=server_dir)))

    assert seen == [("stop", LUA_BODY), ("replace", None)], seen
    assert not (server_dir / LAID).exists()
    assert not scriptdeploy.record_path(server_dir).exists()
    assert f"Removed {LAID}: this server no longer ships it." in said, said


def test_no_scripts_and_no_record_writes_and_refuses_nothing(tmp_path: Path) -> None:
    """WotLK declares none and never had any: nothing is laid, written or refused for it."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    (server_dir / "env").symlink_to(tmp_path / "elsewhere", target_is_directory=True)

    assert list(scriptdeploy.lay(server_dir, [])) == []
    assert not (tmp_path / "elsewhere").exists()


@pytest.mark.parametrize("new_bytes", [False, True], ids=("same-bytes", "new-bytes"))
def test_a_case_only_rename_on_a_case_insensitive_folder_keeps_the_script(
    tmp_path: Path, new_bytes: bool
) -> None:
    """Upstream renames Mentor.lua to mentor.lua; there the folder holds ONE file for both.

    A case-insensitive folder is simulated with a hard link: the new name and the
    old one are the same inode, which is what `samefile` sees there too. Without
    the check the old name reads as "no longer shipped" and is removed, which on a
    real case-insensitive folder removes the file the press just called in place.
    """
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "Mentor.lua").write_text("mentor\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")
    laid = server_dir / LUA_SCRIPTS_DIR / "m"
    list(scriptdeploy.lay(server_dir, [spec]))
    (src / "Mentor.lua").unlink()
    body = "mentor v2\n" if new_bytes else "mentor\n"
    (src / "mentor.lua").write_text(body, encoding="utf-8")
    try:
        os.link(laid / "Mentor.lua", laid / "mentor.lua")
    except OSError:  # pragma: no cover - a filesystem without hard links
        pytest.skip("cannot make a hard link here")

    said = list(scriptdeploy.lay(server_dir, [spec]))

    key = f"{LUA_SCRIPTS_DIR}/m/mentor.lua"
    assert (laid / "Mentor.lua").exists(), said
    assert (laid / "mentor.lua").read_text(encoding="utf-8") == body
    assert not any("Removed" in line or "changed on this machine" in line for line in said), said
    assert scriptdeploy.read_record(server_dir) == {key: scriptdeploy._sha(body.encode())}


def test_a_case_only_rename_on_a_case_sensitive_folder_still_removes_the_old_name(
    tmp_path: Path,
) -> None:
    """Two files there: the old spelling is stale, and nothing ties it to the new one."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "Mentor.lua").write_text("mentor\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")
    laid = server_dir / LUA_SCRIPTS_DIR / "m"
    list(scriptdeploy.lay(server_dir, [spec]))
    (src / "Mentor.lua").unlink()
    (src / "mentor.lua").write_text("mentor\n", encoding="utf-8")

    said = list(scriptdeploy.lay(server_dir, [spec]))

    assert not (laid / "Mentor.lua").exists() and (laid / "mentor.lua").exists(), said
    assert f"Laid {LUA_SCRIPTS_DIR}/m/mentor.lua." in said, said


def test_a_hard_link_under_another_name_is_not_a_case_only_rename(tmp_path: Path) -> None:
    """Codex review: sharing an inode is not enough; the names must differ only by case."""
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "Mentor.lua").write_text("mentor\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")
    laid = server_dir / LUA_SCRIPTS_DIR / "m"
    list(scriptdeploy.lay(server_dir, [spec]))
    (src / "Mentor.lua").unlink()
    (src / "other.lua").write_text("shipped other\n", encoding="utf-8")
    try:
        os.link(laid / "Mentor.lua", laid / "other.lua")
    except OSError:  # pragma: no cover - a filesystem without hard links
        pytest.skip("cannot make a hard link here")

    said = list(scriptdeploy.lay(server_dir, [spec]))

    assert (laid / "other.lua").read_text(encoding="utf-8") == "mentor\n", said
    assert any("was changed on this machine" in line for line in said), said


def lay_then_crash_before_the_record(
    server_dir: Path, spec: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One press that dies after it wrote the files: its closing record save never happens."""
    real = scriptdeploy._write_record

    def dies_before_the_closing_save(
        root: Path, files: dict[str, str], pending: dict[str, str] | None = None
    ) -> None:
        if pending is not None:
            real(root, files, pending)

    with monkeypatch.context() as patched:
        patched.setattr(scriptdeploy, "_write_record", dies_before_the_closing_save)
        list(scriptdeploy.lay(server_dir, [spec]))


def crashed_press_tree(tmp_path: Path) -> tuple[Path, Path, Any, Path]:
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    src = server_dir / "modules/m/lua"
    src.mkdir(parents=True)
    (src / "a.lua").write_text("v1\n", encoding="utf-8")
    spec = LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")
    return server_dir, src, spec, server_dir / LUA_SCRIPTS_DIR / "m" / "a.lua"


def test_a_crash_between_laying_and_the_record_leaves_the_script_claimable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, src, spec, laid = crashed_press_tree(tmp_path)
    lay_then_crash_before_the_record(server_dir, spec, monkeypatch)
    assert laid.read_text(encoding="utf-8") == "v1\n"
    (src / "a.lua").write_text("v2\n", encoding="utf-8")

    said = list(scriptdeploy.lay(server_dir, [spec]))

    assert laid.read_text(encoding="utf-8") == "v2\n", said
    assert f"Updated {LUA_SCRIPTS_DIR}/m/a.lua." in said, said
    assert not any("changed on this machine" in line for line in said), said
    record = json.loads(scriptdeploy.record_path(server_dir).read_text(encoding="utf-8"))
    assert "pending" not in record and list(record["files"]) == [f"{LUA_SCRIPTS_DIR}/m/a.lua"]


def test_a_pending_digest_claims_only_bytes_that_match_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Somebody else's bytes at the pending name stay theirs: the digest is the proof."""
    server_dir, src, spec, laid = crashed_press_tree(tmp_path)
    lay_then_crash_before_the_record(server_dir, spec, monkeypatch)
    laid.write_text("edited after the crash\n", encoding="utf-8")
    (src / "a.lua").write_text("v2\n", encoding="utf-8")

    said = list(scriptdeploy.lay(server_dir, [spec]))

    assert laid.read_text(encoding="utf-8") == "edited after the crash\n"
    assert any("was changed on this machine" in line for line in said), said


def test_a_crash_before_the_write_keeps_the_old_claim_on_the_old_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pending digest is only adopted when the file IS those bytes.

    The press died after saving "v2 is pending" and before writing v2: the file is
    still Yu'lon's v1, recorded as such, and the next press must update it. Taking
    the pending digest over the old one would make v1 read as somebody's edit.
    """
    server_dir, src, spec, laid = crashed_press_tree(tmp_path)
    list(scriptdeploy.lay(server_dir, [spec]))
    (src / "a.lua").write_text("v2\n", encoding="utf-8")
    real = scriptdeploy._write_record

    def dies_before_the_closing_save(
        root: Path, files: dict[str, str], pending: dict[str, str] | None = None
    ) -> None:
        if pending is not None:
            real(root, files, pending)

    real_publish = scriptdeploy._publish

    def dies_before_the_write(target: Path, data: bytes) -> None:
        if target.name == scriptdeploy.RECORD_FILE:
            real_publish(target, data)
        else:
            raise OSError(5, "the machine went away")

    with monkeypatch.context() as patched:
        patched.setattr(scriptdeploy, "_write_record", dies_before_the_closing_save)
        patched.setattr(scriptdeploy, "_publish", dies_before_the_write)
        with pytest.raises(InstallerError):
            list(scriptdeploy.lay(server_dir, [spec]))
    assert laid.read_text(encoding="utf-8") == "v1\n"
    kept = json.loads(scriptdeploy.record_path(server_dir).read_text(encoding="utf-8"))
    assert kept["pending"], "the fixture never saved the pending digest"

    said = list(scriptdeploy.lay(server_dir, [spec]))

    assert laid.read_text(encoding="utf-8") == "v2\n", said
    assert not any("changed on this machine" in line for line in said), said


def test_a_stale_pending_entry_cannot_claim_a_file_the_player_put_there_later(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cold review: `pending` must not outlive the next press, or its digest claims a stranger.

    A press saved "a.lua is pending" and died before writing it. Upstream then dropped
    a.lua, and the next press had nothing to do (so it never rewrote the record). The
    player later put their own a.lua there, with those exact bytes; the press after
    that must leave it alone, not adopt it and then remove it as no longer shipped.
    """
    server_dir, src, spec, laid = crashed_press_tree(tmp_path)
    (src / "a.lua").unlink()
    (src / "b.lua").write_text("b\n", encoding="utf-8")
    list(scriptdeploy.lay(server_dir, [spec]))
    (src / "a.lua").write_text("v1\n", encoding="utf-8")
    real_write, real_publish = scriptdeploy._write_record, scriptdeploy._publish

    def dies_before_the_closing_save(
        root: Path, files: dict[str, str], pending: dict[str, str] | None = None
    ) -> None:
        if pending is not None:
            real_write(root, files, pending)

    def dies_before_the_script(target: Path, data: bytes) -> None:
        if target.name != scriptdeploy.RECORD_FILE:
            raise OSError(5, "the machine went away")
        real_publish(target, data)

    with monkeypatch.context() as patched:
        patched.setattr(scriptdeploy, "_write_record", dies_before_the_closing_save)
        patched.setattr(scriptdeploy, "_publish", dies_before_the_script)
        with pytest.raises(InstallerError):
            list(scriptdeploy.lay(server_dir, [spec]))
    assert "pending" in scriptdeploy.record_path(server_dir).read_text(encoding="utf-8")
    (src / "a.lua").unlink()

    list(scriptdeploy.lay(server_dir, [spec], quiet=True))

    assert "pending" not in scriptdeploy.record_path(server_dir).read_text(encoding="utf-8")
    laid.write_text("v1\n", encoding="utf-8")
    said = list(scriptdeploy.lay(server_dir, [spec], quiet=True))
    assert laid.read_text(encoding="utf-8") == "v1\n", said
    assert not any("Removed" in line for line in said), said


def test_the_laying_failures_name_the_press_and_never_claim_the_server_was_not_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """These sentences are also said on a rollback, where the server may well have been started."""
    from yulon import server_build_presses
    from yulon.catalog.catalog import LuaScripts

    press = server_build_presses.under_server_build(server_build_presses.REBUILD)
    server_dir, _src, spec, _laid = crashed_press_tree(tmp_path)
    missing = LuaScripts(src="modules/gone/lua", dest=f"{LUA_SCRIPTS_DIR}/gone")
    with pytest.raises(InstallerError) as absent:
        list(scriptdeploy.lay(server_dir, [missing]))
    monkeypatch.setattr(
        scriptdeploy, "_publish", lambda *_a: (_ for _ in ()).throw(OSError(28, "full"))
    )
    with pytest.raises(InstallerError) as unwritable:
        list(scriptdeploy.lay(server_dir, [spec]))

    for raised in (absent, unwritable):
        assert press in str(raised.value), raised.value
        assert "not started" not in str(raised.value), raised.value
        assert "would start without" not in str(raised.value), raised.value


def test_a_rollback_that_cannot_write_a_script_names_the_press_not_the_patch(
    tmp_path: Path, installers: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import server_build_presses

    real = scriptdeploy._publish
    scripts: list[int] = []

    def fourth_script_fails(target: Path, data: bytes) -> None:
        if target.name != scriptdeploy.RECORD_FILE:
            scripts.append(1)
            if len(scripts) >= 4:  # the install, the update's two, then the rollback's
                raise OSError(5, "the disk went away")
        real(target, data)

    monkeypatch.setattr(scriptdeploy, "_publish", fourth_script_fails)

    _rec, _server_dir, said, _seen = failed_start_after_laying(tmp_path, installers)

    back = next(line for line in said if "back on their old commits, but" in line)
    assert server_build_presses.under_server_build(server_build_presses.REBUILD) in back, back
    assert "source patch" not in back and "not started" not in back, back


def test_a_pending_only_record_is_cleared_by_an_entry_with_no_scripts(tmp_path: Path) -> None:
    """The no-specs path: files={} and a pending entry, and the entry no longer ships scripts.

    The press must not return early and leave `pending` behind, or a file the
    player puts there later with those very bytes is adopted on the next press and
    removed as "no longer shipped".
    """
    server_dir, _src, spec, laid = crashed_press_tree(tmp_path)
    laid.parent.mkdir(parents=True)
    key = f"{LUA_SCRIPTS_DIR}/m/a.lua"
    scriptdeploy.record_path(server_dir).write_text(
        json.dumps({"version": 1, "files": {}, "pending": {key: scriptdeploy._sha(b"v1\n")}}),
        encoding="utf-8",
    )

    list(scriptdeploy.lay(server_dir, [], quiet=True))

    assert not scriptdeploy.record_path(server_dir).exists()
    laid.write_text("v1\n", encoding="utf-8")
    said = list(scriptdeploy.lay(server_dir, [spec], quiet=True))
    assert laid.read_text(encoding="utf-8") == "v1\n", said
    assert not any("Removed" in line for line in said), said
    assert list(scriptdeploy.lay(server_dir, [], quiet=True)) == []
    assert laid.read_text(encoding="utf-8") == "v1\n"


def test_a_failed_record_save_with_no_folders_does_not_name_an_empty_list(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server_dir, src, spec, _laid = crashed_press_tree(tmp_path)
    list(scriptdeploy.lay(server_dir, [spec]))
    (src / "a.lua").unlink()
    fail_the_final_record_write(monkeypatch)

    with pytest.raises(InstallerError, match="delete") as raised:
        list(scriptdeploy.lay(server_dir, []))

    assert "delete  " not in str(raised.value) and "delete that file" in str(raised.value)
