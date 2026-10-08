"""A module that reads a folder the player fills has that folder made at install.

`mod-ale` loads every `.lua` under `env/dist/etc/modules/lua_scripts`, and the
conf step points `ALE.ScriptPath` there. Nothing created the folder, so a Lua
engine installed on its own started with nothing to load and nothing told the
player where scripts go. The manifest's `folders` field now names it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_apply import ALE, LUA, _FakeGit, _shipped, _untouched
from yulon.apply import Applier
from yulon.manifest import parse_manifest

ALE_CONF = 'ALE.ScriptPath = "lua_scripts"\nALE.Enabled = true\n'


def _install_ale(tmp_path: Path) -> tuple[Applier, Any]:
    manifest = _shipped("mod-ale")
    git = _FakeGit({"conf/mod_ale.conf.dist": ALE_CONF})
    applier = _untouched(Applier(tmp_path, git=git), manifest)
    applier.install(manifest)
    return applier, manifest


def test_mod_ale_declares_the_script_folder_its_conf_points_at() -> None:
    manifest = _shipped("mod-ale")

    assert manifest.folders == (LUA,)
    script_path = next(k for k in manifest.conf[0].keys if k.key == "ALE.ScriptPath").default
    assert script_path == f'"/azerothcore/{LUA}"', "folder and conf name different places"


def test_mod_ale_tells_the_player_where_the_lua_files_go() -> None:
    sentence = _shipped("mod-ale").description

    assert "env/dist/etc/modules/lua_scripts" in sentence
    assert ".lua" in sentence


def test_installing_mod_ale_makes_the_empty_script_folder(tmp_path: Path) -> None:
    _install_ale(tmp_path)

    folder = tmp_path / LUA
    assert folder.is_dir()
    assert list(folder.iterdir()) == []


def test_a_second_install_keeps_the_scripts_already_in_the_folder(tmp_path: Path) -> None:
    applier, manifest = _install_ale(tmp_path)
    mine = tmp_path / LUA / "mine.lua"
    mine.write_text("print('mine')\n", encoding="utf-8")
    sub = tmp_path / LUA / "mine_dir"
    sub.mkdir()
    (sub / "a.lua").write_text("-- a\n", encoding="utf-8")

    applier.install(manifest)

    assert mine.read_text(encoding="utf-8") == "print('mine')\n"
    assert (sub / "a.lua").is_file()


def test_remove_takes_back_the_folder_it_made_when_it_is_still_empty(tmp_path: Path) -> None:
    applier, manifest = _install_ale(tmp_path)

    report = applier.remove(manifest)

    assert not (tmp_path / LUA).exists()
    assert not any(LUA in line for line in report.left_behind)


def test_remove_leaves_a_folder_that_holds_scripts_and_says_so(tmp_path: Path) -> None:
    applier, manifest = _install_ale(tmp_path)
    mine = tmp_path / LUA / "mine.lua"
    mine.write_text("print('mine')\n", encoding="utf-8")

    report = applier.remove(manifest)

    assert mine.read_text(encoding="utf-8") == "print('mine')\n"
    said = [line for line in report.left_behind if LUA in line]
    assert len(said) == 1, report.left_behind
    assert "kept" in said[0]


def test_remove_leaves_a_folder_that_holds_only_a_subfolder(tmp_path: Path) -> None:
    applier, manifest = _install_ale(tmp_path)
    (tmp_path / LUA / "paragon").mkdir()

    applier.remove(manifest)

    assert (tmp_path / LUA / "paragon").is_dir()


def test_a_file_where_the_folder_should_be_is_reported_not_overwritten(tmp_path: Path) -> None:
    manifest = _shipped("mod-ale")
    (tmp_path / LUA).parent.mkdir(parents=True)
    (tmp_path / LUA).write_text("not a folder\n", encoding="utf-8")
    git = _FakeGit({"conf/mod_ale.conf.dist": ALE_CONF})
    applier = _untouched(Applier(tmp_path, git=git), manifest)

    report = applier.install(manifest)

    assert (tmp_path / LUA).read_text(encoding="utf-8") == "not a folder\n"
    assert any(LUA in line for line in report.skipped), report.skipped


@pytest.mark.parametrize("bad", ["/etc/x", "../x", "a/../../x", "", "C:/x", "env\\dist"])
def test_a_folder_outside_the_server_dir_does_not_load(bad: str) -> None:
    with pytest.raises(ValueError):
        parse_manifest({**ALE, "folders": [bad]})


def test_a_folder_below_the_server_dir_loads() -> None:
    assert parse_manifest({**ALE, "folders": [LUA]}).folders == (LUA,)
