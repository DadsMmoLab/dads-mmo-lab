"""An item installed over a module that has never been compiled is told to rebuild first (T750).

`requires: mod-ale` is met by the folder alone (`missing_requirements()` asks the disk), so
an ALE script installed after mod-ale's Install and before its Rebuild went through and was
told "Press Stop and then Start". The world has no Lua engine until the Rebuild. The
install of a `rebuild: true` module is recorded in the server folder
(`module_answers.REBUILD_OWED`) and read again by a later Applier, which is what an app
restart is; a build that finished since (`native.BUILT_FROM_FILE` written anew) settles it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.test_apply import _FakeGit, _FakeSql, _untouched
from yulon import module_answers
from yulon.apply import Applier
from yulon.catalog import native
from yulon.controller_wow_wotlk import modules as wotlk_modules
from yulon.manifest import Manifest, parse_manifest

MOD_ALE: dict[str, Any] = {
    "id": "mod-ale",
    "name": "AzerothCore Lua Engine (ALE)",
    "type": "module",
    "game": "wow-wotlk",
    "source": {"repo": "azerothcore/mod-ale"},
    "build": {"rebuild": True},
}


def _bp_files() -> dict[str, str]:
    return {
        "lua_scripts/battlepass/01_BP_Core.lua": "-- core",
        "lua_scripts/battlepass/05_BP_Communication.lua": 'require("lib.CSMH.CSMH_SMH")\n',
        "lua_scripts/lib/CSMH/CSMH_SMH.ext": "-- csmh",
        "sql/battlepass_world.sql": "-- world",
        "sql/battlepass_characters.sql": "-- characters",
        "BattlePass/BattlePass.toc": "## Title: BattlePass",
    }


def _install_mod_ale(server_dir: Path) -> Manifest:
    ale = parse_manifest(MOD_ALE)
    Applier(server_dir, git=_FakeGit({"README.md": "ale"}), sql=_FakeSql()).install(ale)
    return ale


def _install_battle_pass(server_dir: Path) -> Any:
    """In a NEW Applier, the state a restarted app is in: nothing carried over in memory."""
    bp = wotlk_modules.store().load("ale", "battlepass")
    return Applier(server_dir, git=_FakeGit(_bp_files()), sql=_FakeSql()).install(bp)


def _built(server_dir: Path) -> None:
    """What a compile that finished leaves: the build record written anew (T589)."""
    native.remember_built_from(server_dir, {"azerothcore/azerothcore-wotlk": "a" * 40})


def test_a_script_over_a_mod_ale_installed_and_never_built_is_told_to_rebuild_first(
    tmp_path: Path,
) -> None:
    _install_mod_ale(tmp_path)
    report = _install_battle_pass(tmp_path)
    assert report.rebuild_first == ("mod-ale",)
    said = [line for line in report.skipped if "mod-ale" in line]
    assert said and "rebuild" in said[0].lower(), report.skipped


def test_a_build_since_mod_ale_went_in_settles_it(tmp_path: Path) -> None:
    _install_mod_ale(tmp_path)
    _built(tmp_path)
    report = _install_battle_pass(tmp_path)
    assert report.rebuild_first == ()
    assert not [line for line in report.skipped if "rebuild" in line.lower()], report.skipped


def test_a_build_that_was_there_before_mod_ale_went_in_does_not_settle_it(
    tmp_path: Path,
) -> None:
    """The record holds WHICH build was there; only a newer one settles it."""
    _built(tmp_path)
    _install_mod_ale(tmp_path)
    assert _install_battle_pass(tmp_path).rebuild_first == ("mod-ale",)


def test_a_rebuild_that_stopped_part_way_does_not_settle_it(tmp_path: Path) -> None:
    """`rebuild()` forgets the build record before it changes anything; none means not known."""
    _built(tmp_path)
    _install_mod_ale(tmp_path)
    native.forget_built_from(tmp_path)
    assert _install_battle_pass(tmp_path).rebuild_first == ("mod-ale",)


def test_an_update_of_a_mod_ale_that_was_built_does_not_say_rebuild_first(tmp_path: Path) -> None:
    """The world already has an engine; the update's own report owes its rebuild."""
    ale = _install_mod_ale(tmp_path)
    _built(tmp_path)
    again = Applier(tmp_path, git=_FakeGit({"README.md": "ale 2"}), sql=_FakeSql())
    _untouched(again, ale).install(ale)
    assert _install_battle_pass(tmp_path).rebuild_first == ()
    assert module_answers.rebuilds_owed(tmp_path, native_stamp(tmp_path)) == {
        "module/mod-ale": False
    }


def test_the_record_says_rebuild_pending_until_a_build_and_remove_drops_it(
    tmp_path: Path,
) -> None:
    ale = _install_mod_ale(tmp_path)
    assert module_answers.rebuilds_owed(tmp_path, native_stamp(tmp_path)) == {
        "module/mod-ale": True
    }
    Applier(tmp_path, git=_FakeGit({}), sql=_FakeSql()).remove(ale)
    assert module_answers.rebuilds_owed(tmp_path, native_stamp(tmp_path)) == {}


def native_stamp(server_dir: Path) -> str | None:
    from yulon.apply import built_stamp

    return built_stamp(server_dir)
