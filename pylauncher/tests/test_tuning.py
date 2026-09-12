"""Tests for `yulon.tuning` — the Tuning tab's reading, writing and guard (T43).

No Qt anywhere in this file: everything the tab decides about a setting is
decided here, against a directory on disk, so the rules are assertable without
a `QApplication`. The widgets are `tests/test_tuning_panel.py`'s.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

import pytest

from yulon import tuning
from yulon.manifest import Manifest, parse_manifest

BASE: dict[str, Any] = {
    "schema_version": 1,
    "id": "mod-beast",
    "name": "NPC Beastmaster",
    "type": "module",
    "game": "wow-wotlk",
    "description": "Pets for every class.",
    "source": {"repo": "azerothcore/mod-npc-beastmaster"},
}


def _manifest(**over: Any) -> Manifest:
    return parse_manifest({**BASE, **over})


def _conf(file: str, keys: list[dict[str, Any]]) -> dict[str, Any]:
    return {"file": file, "keys": keys}


CONF = "env/dist/etc/modules/mod_npc_beastmaster.conf"


def _never(path: Path) -> NoReturn:
    """A `_read` that must not be called: `(DB table)` and a glob are not paths."""
    raise AssertionError(f"the reader opened {path}, which is not a file it may open")


def _write(server_dir: Path, rel: str, text: str) -> Path:
    path = server_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


# -- point 2: the reader ----------------------------------------------------


def test_only_an_installed_module_contributes_rows(tmp_path: Path) -> None:
    """An uninstalled module has no file to tune, so it has no row."""
    here = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}])])
    gone = _manifest(
        id="mod-other",
        name="Other",
        conf=[_conf("env/dist/etc/modules/other.conf", [{"key": "A"}])],
    )
    rows = tuning.rows_for([here, gone], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert [row.module_id for row in rows] == ["mod-beast"]
    assert all(row.installed for row in rows)


def test_a_row_carries_every_field_the_tab_draws(tmp_path: Path) -> None:
    _write(server_dir := tmp_path, CONF, "[worldserver]\nBeastMaster.MinLevel = 30\n")
    manifest = _manifest(
        conf=[
            _conf(
                CONF,
                [
                    {
                        "key": "BeastMaster.MinLevel",
                        "default": "10",
                        "label": "Minimum level",
                        "explain": "Level a character must reach before adopting a pet.",
                        "type": "int",
                        "min": 0,
                        "max": 80,
                    }
                ],
            )
        ]
    )
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, server_dir)
    assert row.module_id == "mod-beast"
    assert row.module_name == "NPC Beastmaster"
    assert row.family == "module"
    assert row.file == CONF
    assert row.key == "BeastMaster.MinLevel"
    assert row.label == "Minimum level"
    assert row.explain == "Level a character must reach before adopting a pet."
    assert (row.type, row.min, row.max) == ("int", 0, 80)
    assert row.default == "10"
    assert row.current == "30"
    assert row.backend == "conf"
    assert row.read_only_reason is None


def test_a_key_with_no_label_is_drawn_under_its_own_key(tmp_path: Path) -> None:
    manifest = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert row.label == "BeastMaster.Enable"
    assert row.explain is None and row.type is None


def test_a_conf_that_is_not_there_gives_no_current_value_and_still_lists(tmp_path: Path) -> None:
    """Never a fabricated `current`: the row shows its default and says nothing else."""
    manifest = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable", "default": "1"}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert row.current is None and row.default == "1"


def test_a_conf_that_cannot_be_read_gives_no_current_value(tmp_path: Path) -> None:
    (tmp_path / CONF).mkdir(parents=True)  # a directory where the file should be
    manifest = _manifest(conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}])])
    (row,) = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert row.current is None


def test_the_last_active_assignment_wins_and_a_commented_one_is_not_read(tmp_path: Path) -> None:
    """`party.read_conf()`'s rule, for `party.read_conf()`'s measured reason."""
    _write(
        tmp_path,
        CONF,
        # The commented and indented copies come AFTER the real one on purpose.
        # Written above it, "the last active assignment wins" masks them and the
        # test passes with the column-0 rule deleted -- which is what it did
        # until the mutation was run.
        "BeastMaster.Enable = 1\n"
        "BeastMaster.Enable = 0\n"
        "# BeastMaster.Enable = 9\n"
        "  BeastMaster.Enable = 8\n"
        'BeastMaster.Name = "White Fang"\n',
    )
    manifest = _manifest(
        conf=[_conf(CONF, [{"key": "BeastMaster.Enable"}, {"key": "BeastMaster.Name"}])]
    )
    rows = tuning.rows_for([manifest], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert [row.current for row in rows] == ["0", "White Fang"]


def test_a_lua_backed_key_is_listed_read_only_and_says_why(tmp_path: Path) -> None:
    """T43 decision 5: never silently dropped, and never written by v1."""
    lua = "env/dist/etc/modules/lua_scripts/SitMeansRest.lua"
    _write(tmp_path, lua, "DURATION = 20\n")
    manifest = _manifest(
        id="sitmeanrest",
        name="Sit Means Rest",
        type="ale",
        conf=[_conf(lua, [{"key": "DURATION"}])],
    )
    (row,) = tuning.rows_for([manifest], {"ale": frozenset({"sitmeanrest"})}, tmp_path)
    assert row.backend == "lua"
    assert row.current == "20"
    assert row.read_only_reason == tuning.LUA_IS_NOT_IN_V1


def test_a_key_whose_file_is_not_a_file_is_read_only_and_never_touches_the_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tuning, "_read", _never)
    table = "acore_ale.paragon_config (DB table)"
    manifest = _manifest(
        id="paragon", name="Paragon", type="ale", conf=[_conf(table, [{"key": "xp"}])]
    )
    (row,) = tuning.rows_for([manifest], {"ale": frozenset({"paragon"})}, tmp_path)
    assert row.backend == "other"
    assert row.current is None
    assert row.read_only_reason == tuning.NOT_A_CONF_FILE.format(file=table)


def test_a_key_whose_file_is_a_glob_is_read_only_because_there_is_no_one_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tuning, "_read", _never)
    glob = "env/dist/etc/modules/lua_scripts/accountwide/*.lua"
    manifest = _manifest(
        id="accountwide", name="Account Wide", type="ale", conf=[_conf(glob, [{"key": "X"}])]
    )
    (row,) = tuning.rows_for([manifest], {"ale": frozenset({"accountwide"})}, tmp_path)
    assert row.current is None
    assert row.read_only_reason == tuning.MORE_THAN_ONE_FILE.format(file=glob)


def test_rows_are_ordered_by_family_then_catalog_then_file_then_key(tmp_path: Path) -> None:
    ale = _manifest(
        id="sitmeanrest",
        name="Sit",
        type="ale",
        conf=[_conf("env/dist/etc/modules/lua_scripts/S.lua", [{"key": "B"}, {"key": "A"}])],
    )
    module = _manifest(
        conf=[
            _conf("env/dist/etc/modules/z.conf", [{"key": "Z1"}]),
            _conf("env/dist/etc/modules/a.conf", [{"key": "A1"}]),
        ]
    )
    installed = {"module": frozenset({"mod-beast"}), "ale": frozenset({"sitmeanrest"})}
    rows = tuning.rows_for([ale, module], installed, tmp_path)
    assert [(row.family, row.file.rsplit("/", 1)[-1], row.key) for row in rows] == [
        ("module", "z.conf", "Z1"),
        ("module", "a.conf", "A1"),
        ("ale", "S.lua", "B"),
        ("ale", "S.lua", "A"),
    ]


def test_a_family_is_read_from_its_own_clone_folder(tmp_path: Path) -> None:
    """T41's defect from the other side: an ale is not installed because a module is."""
    ale = _manifest(
        id="mod-beast",
        type="ale",
        conf=[_conf("env/dist/etc/modules/lua_scripts/S.lua", [{"key": "A"}])],
    )
    rows = tuning.rows_for([ale], {"module": frozenset({"mod-beast"})}, tmp_path)
    assert rows == ()
