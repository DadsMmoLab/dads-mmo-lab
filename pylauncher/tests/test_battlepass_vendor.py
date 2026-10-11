"""T684 (catalog audit C3): the Battle Pass vendor can be spawned, and goes on Remove.

At the WotLK pin, `Creature::InitEntry` refuses a `creature_template` entry with no
`creature_template_model` row ("has no model defined ... can't load"), so the app's
own instruction `.npc add 90100` failed. The scripts find the vendor through
`battlepass_config.npc_entry`, which a prompt let the player point at an entry
nobody created.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.test_apply import _FakeGit, _FakeSql, _have_requirements
from yulon.apply import Applier
from yulon.controller_wow_wotlk import modules as wotlk_modules

_CREATED = re.compile(r"INSERT INTO creature_template \([^)]*\) VALUES \((\d+),", re.I)
_MODELLED = re.compile(r"INSERT INTO creature_template_model \([^)]*\) VALUES \((\d+),", re.I)


def _battlepass():  # type: ignore[no-untyped-def]
    return wotlk_modules.store().load("ale", "battlepass")


def _upstream_files() -> dict[str, str]:
    return {
        "lua_scripts/battlepass/01_BP_Core.lua": "-- core",
        "lua_scripts/battlepass/05_BP_Communication.lua": 'require("lib.CSMH.CSMH_SMH")\n',
        "lua_scripts/lib/CSMH/CSMH_SMH.ext": "-- csmh",
        "sql/battlepass_world.sql": "-- world",
        "sql/battlepass_characters.sql": "-- characters",
        "BattlePass/BattlePass.toc": "## Title: BattlePass",
    }


def _installed(tmp_path: Path) -> tuple[Applier, _FakeSql]:
    m = _battlepass()
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_FakeGit(_upstream_files()), sql=sql)
    _have_requirements(tmp_path, m)
    applier.install(m)
    return applier, sql


def test_the_vendor_the_install_creates_has_a_model(tmp_path: Path) -> None:
    """Every creature entry the install writes gets a model row in the same install."""
    _applier, sql = _installed(tmp_path)
    world = " ".join(text for db, text in sql.statements if db == "world")
    created = set(_CREATED.findall(world))
    assert created == {"90100"}
    assert created <= set(_MODELLED.findall(world))


def test_the_scripts_are_pointed_at_the_entry_the_install_created(tmp_path: Path) -> None:
    """`npc_entry` in battlepass_config names the vendor that exists, whatever was answered."""
    m = _battlepass()
    sql = _FakeSql()
    applier = Applier(tmp_path, git=_FakeGit(_upstream_files()), sql=sql)
    _have_requirements(tmp_path, m)
    applier.install(m, {"npc_entry": "12345"})
    world = " ".join(text for db, text in sql.statements if db == "world")
    said = re.findall(r"config_value='([^']*)' WHERE config_key='npc_entry'", world)
    assert said == ["90100"]
    assert "90100" in _CREATED.findall(world)


def test_remove_takes_the_vendor_back(tmp_path: Path) -> None:
    applier, sql = _installed(tmp_path)
    sent = len(sql.statements)
    applier.remove(_battlepass())
    removed = " ".join(text for db, text in sql.statements[sent:] if db == "world")
    assert re.search(r"DELETE FROM creature_template_model WHERE CreatureID=90100", removed)
    assert re.search(r"DELETE FROM creature_template WHERE entry=90100", removed)
