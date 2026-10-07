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
    assert asked == ["SELECT COUNT(*) FROM `acore_world`.`creature_template` WHERE entry = 1;"]
    assert any("has what this server needs" in line for line in said), said


@pytest.mark.parametrize("answer", ["0\n", ""], ids=("zero", "no-rows"))
def test_a_short_count_refuses_before_the_world_starts(
    tmp_path: Path, installers: Path, answer: str
) -> None:
    rec = Recorder(images=False)
    server_dir = tmp_path / "server"
    rec.on_clone = lay_tree(server_dir)
    rec.query_answer = answer
    made = make(scratch_entry(**FULL), rec, installers)

    with pytest.raises(InstallerError, match="scratch NPC is not in the world database"):
        list(made.run(InstallOptions(server_dir=server_dir)))
    assert "start" not in rec.calls
    assert not (server_dir / LAID).exists(), "the scripts were laid over a refused database"


def test_a_missing_table_is_a_short_count_and_an_unanswered_one_refuses(
    tmp_path: Path,
) -> None:
    from yulon.catalog.catalog import SqlCheck

    check = SqlCheck(db="world", table="unbound_catalog", reason="no catalog.")

    def no_table(schema: str, statement: str) -> str:
        raise docker.DockerCommandError("ERROR 1146 (42S02): Table doesn't exist")

    with pytest.raises(InstallerError, match="no catalog"):
        list(scriptdeploy.check_sql([check], {"world": "w"}, no_table, "Scratch"))

    def down(schema: str, statement: str) -> str:
        raise docker.DockerCommandError("container is not running")

    # Fail closed (Codex): "could not tell" is not "it is there".
    with pytest.raises(InstallerError, match="could not be checked"):
        list(scriptdeploy.check_sql([check], {"world": "w"}, down, "Scratch"))

    with pytest.raises(InstallerError, match="not a count"):
        list(scriptdeploy.check_sql([check], {"world": "w"}, lambda s, q: "Warning\n", "S"))


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


def test_a_lua_source_that_is_a_link_is_not_followed(tmp_path: Path) -> None:
    from yulon.catalog.catalog import LuaScripts

    server_dir = tmp_path / "srv"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "secret.lua").write_text("x\n", encoding="utf-8")
    (server_dir / "modules/m").mkdir(parents=True)
    (server_dir / "modules/m/lua").symlink_to(elsewhere, target_is_directory=True)

    said = list(
        scriptdeploy.lay(server_dir, [LuaScripts(src="modules/m/lua", dest=f"{LUA_SCRIPTS_DIR}/m")])
    )

    assert not (server_dir / LUA_SCRIPTS_DIR / "m" / "secret.lua").exists()
    assert any("is a link" in line for line in said), said


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
