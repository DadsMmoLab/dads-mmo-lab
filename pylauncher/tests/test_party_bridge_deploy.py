"""My Party's bridge scripts reach the server folder on their own, and on a press (T792).

`party.deploy()` had no production caller: every player's My Party stopped at "the bridge
scripts are not in the server folder yet." These tests pin the four ways in:

* the Modules tab's install of the Lua engine (`Applier.install` of `mod-ale`), and its Remove;
* a Rebuild or Update of an AzerothCore server that has `modules/mod-ale`
  (`stage_recreate`, with the old world stopped or about to be replaced), and the install's
  own `lua-and-sql` stage, which is how WoW Unbound gets them on a fresh install;
* the seam's `deploy_bridge()`, behind the panel's **Set up My Party** button.

A deploy that fails is logged and said, and never fails the install or the rebuild.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder
from yulon import party, resources
from yulon.apply import Applier
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallOptions
from yulon.git import CloneSpec

LUA_DIR = Path("env") / "dist" / "etc" / "modules" / "lua_scripts"
SIX = tuple(sorted(party.BRIDGE_SCRIPTS))


def _scripts(server_dir: Path) -> list[str]:
    folder = server_dir / LUA_DIR
    return sorted(p.name for p in folder.glob("*.lua")) if folder.is_dir() else []


# -- the helper every route shares -----------------------------------------


def test_the_helper_does_nothing_for_a_server_without_the_lua_engine(tmp_path: Path) -> None:
    assert party.lay_bridge(tmp_path) is None
    assert _scripts(tmp_path) == []


def test_the_helper_lays_all_six_where_the_engine_is_cloned(tmp_path: Path) -> None:
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)

    laid = party.lay_bridge(tmp_path)

    assert isinstance(laid, party.Deployment) and laid.changed
    assert _scripts(tmp_path) == list(SIX)
    again = party.lay_bridge(tmp_path)
    assert isinstance(again, party.Deployment) and not again.changed


def test_the_helper_says_a_failure_and_never_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    empty = tmp_path / "no-lua"
    empty.mkdir()
    monkeypatch.setattr(party, "lua_root", lambda: empty)

    said = party.lay_bridge(tmp_path)

    assert isinstance(said, str)
    assert "My Party" in said and "Set up My Party" in said


def test_the_helper_says_a_disk_failure_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)

    def full(*_args: object, **_kwargs: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(party.shutil, "copy2", full)

    said = party.lay_bridge(tmp_path)

    assert isinstance(said, str) and "No space left" in said


def test_taking_the_bridge_back_removes_only_our_own_bytes(tmp_path: Path) -> None:
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    party.lay_bridge(tmp_path)
    folder = tmp_path / LUA_DIR
    (folder / "LootPet.lua").write_text("-- somebody else's\n", encoding="utf-8")
    edited = folder / SIX[0]
    edited.write_text("-- the player changed this\n", encoding="utf-8")

    gone = party.take_back_bridge(tmp_path)

    assert isinstance(gone, tuple) and set(gone) == set(SIX[1:])
    assert _scripts(tmp_path) == sorted(["LootPet.lua", SIX[0]])


# -- the Modules tab: install and remove of the Lua engine -----------------


class _Git:
    """Writes the module's files instead of cloning."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def clone(self, spec: CloneSpec) -> None:
        for rel, text in self.files.items():
            target = spec.dest / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        (spec.dest / ".git").mkdir(exist_ok=True)

    def is_unmodified(self, dest: Path, relative_path: str) -> bool | None:
        return None

    def no_local_commits(self, dest: Path, branch: str | None) -> bool | None:
        return None


ALE_DIST = 'ALE.Enabled = 1\nALE.ScriptPath = "lua_scripts"\n'


def _ale() -> Any:
    from yulon.controller_wow_wotlk import modules as wotlk_modules

    return wotlk_modules.store().load("module", "mod-ale")


def _install_ale(server: Path) -> Any:
    applier = Applier(server, git=_Git({"conf/mod_ale.conf.dist": ALE_DIST}))
    return applier, applier.install(_ale())


def test_installing_the_lua_engine_leaves_all_six_bridge_scripts(tmp_path: Path) -> None:
    _applier, report = _install_ale(tmp_path)

    assert _scripts(tmp_path) == list(SIX), report
    assert any("My Party" in line for line in report.done), report.done


def test_a_failed_deploy_does_not_fail_the_engine_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "no-lua"
    empty.mkdir()
    monkeypatch.setattr(party, "lua_root", lambda: empty)

    _applier, report = _install_ale(tmp_path / "server")

    assert (tmp_path / "server" / "modules" / "mod-ale").is_dir()
    assert any("My Party" in line for line in report.skipped), report.skipped


def test_removing_the_lua_engine_takes_the_bridge_scripts_it_placed(tmp_path: Path) -> None:
    applier, _report = _install_ale(tmp_path)
    folder = tmp_path / LUA_DIR
    (folder / "player_made.lua").write_text("-- mine\n", encoding="utf-8")
    assert _scripts(tmp_path) == sorted(["player_made.lua", *SIX])

    applier.remove(_ale())

    assert _scripts(tmp_path) == ["player_made.lua"]


# -- the rebuild and the install of an AzerothCore server ------------------


def _rebuilt(
    tmp_path: Path, *, engine: bool, lay: bool = True
) -> tuple[Path, list[str], AzerothCoreInstaller]:
    root = tmp_path / "installers"
    shutil.copytree(resources.installers_dir(), root)
    rec = Recorder(images=True)
    server_dir = tmp_path / "server"
    made = AzerothCoreInstaller(
        ENTRY,
        installers_root=root,
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )
    list(made.run(InstallOptions(server_dir=server_dir)))
    for stale in (server_dir / LUA_DIR).glob("dml_*.lua"):
        stale.unlink()
    if engine:
        (server_dir / "modules" / "mod-ale").mkdir(parents=True, exist_ok=True)
    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))
    return server_dir, said, made


def test_a_rebuild_of_a_server_with_the_engine_and_no_scripts_deploys_them(
    tmp_path: Path,
) -> None:
    server_dir, said, _made = _rebuilt(tmp_path, engine=True)

    assert _scripts(server_dir) == list(SIX), said


def test_a_rebuild_without_the_engine_lays_nothing(tmp_path: Path) -> None:
    server_dir, _said, _made = _rebuilt(tmp_path, engine=False)

    assert _scripts(server_dir) == []


def test_a_failed_deploy_does_not_fail_the_rebuild(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "no-lua"
    empty.mkdir()
    monkeypatch.setattr(party, "lua_root", lambda: empty)

    server_dir, said, _made = _rebuilt(tmp_path, engine=True)

    assert _scripts(server_dir) == []
    assert any("My Party" in line for line in said), said
    assert "The containers were replaced." in said


# -- the seam behind the button --------------------------------------------


def _seam(server: Path) -> party.InstallParty:
    return party.InstallParty(
        load_catalog().get("wow-wotlk"),
        server,
        sql=None,  # type: ignore[arg-type]
        channel_for_saved=lambda: None,
        container="ac-worldserver",
        world_running=lambda: True,
        engine=lambda: party.BinaryRead(True, "in"),
    )


def test_the_seam_deploys_and_says_a_restart_is_owed(tmp_path: Path) -> None:
    done = _seam(tmp_path).deploy_bridge()

    assert done.changed is True
    assert done.names == SIX
    assert _scripts(tmp_path) == list(SIX)


def test_the_seam_says_unchanged_for_a_second_deploy(tmp_path: Path) -> None:
    seam = _seam(tmp_path)
    seam.deploy_bridge()

    assert seam.deploy_bridge().changed is False


def test_the_seam_names_nothing_to_deploy_in_the_apps_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = tmp_path / "no-lua"
    empty.mkdir()
    monkeypatch.setattr(party, "lua_root", lambda: empty)

    with pytest.raises(party.NothingToDeploy) as raised:
        _seam(tmp_path / "server").deploy_bridge()

    assert "no bridge scripts were found" in str(raised.value)


def test_the_state_says_when_the_press_would_help(tmp_path: Path) -> None:
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    conf = tmp_path / party.ALE_CONF
    conf.parent.mkdir(parents=True)
    conf.write_text(f'ALE.Enabled = 1\nALE.ScriptPath = "{party.ALE_SCRIPT_PATH}"\n')
    seam = _seam(tmp_path)

    before = seam.state("Pakka")
    assert before.deploy_blocked is True
    assert before.blocker == party.preconditions(seam.facts())[7].sentence

    seam.deploy_bridge()
    after = seam.state("Pakka")
    assert after.deploy_blocked is False
