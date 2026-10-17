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
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.support_native import ENTRY, Recorder
from yulon import party, resources
from yulon.apply import Applier
from yulon.catalog.catalog import load_catalog
from yulon.catalog.families import scriptdeploy
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallOptions
from yulon.git import CloneSpec
from yulon.said import SaidByYulon

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

    monkeypatch.setattr(scriptdeploy, "publish", full)

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

    assert isinstance(gone, party.TakenBack)
    assert set(gone.gone) == set(SIX[1:]) and gone.kept == (SIX[0],)
    assert _scripts(tmp_path) == sorted(["LootPet.lua", SIX[0]])
    assert not (folder / party.BRIDGE_RECORD).exists(), "nothing left to remember"


def test_the_helper_never_raises_when_the_engine_folder_cannot_be_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python 3.13's `Path.is_dir()` raises PermissionError on EACCES."""
    real = Path.is_dir

    def refused(self: Path, *args: object, **kwargs: object) -> bool:
        if self.name == "mod-ale":
            raise PermissionError(13, "Permission denied", str(self))
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "is_dir", refused)

    assert party.lay_bridge(tmp_path) is None
    assert party.engine_cloned(tmp_path) is False


# -- hand edits and older bundles: only the app's own copies are replaced ---


def _bundle(root: Path, body: str) -> Path:
    folder = root / "party"
    folder.mkdir(parents=True, exist_ok=True)
    for name in SIX:
        (folder / name).write_text(f"-- {name} {body}\n", encoding="utf-8")
    return root


def _with_engine(server: Path) -> None:
    (server / "modules" / "mod-ale").mkdir(parents=True, exist_ok=True)


def test_an_old_bundled_version_is_replaced_by_the_new_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    _with_engine(server)
    monkeypatch.setattr(party, "lua_root", lambda: _bundle(tmp_path / "v1", "one"))
    party.lay_bridge(server)
    monkeypatch.setattr(party, "lua_root", lambda: _bundle(tmp_path / "v2", "two"))

    laid = party.lay_bridge(server)

    assert isinstance(laid, party.Deployment) and laid.changed and laid.kept == ()
    assert (server / LUA_DIR / SIX[0]).read_text(encoding="utf-8") == f"-- {SIX[0]} two\n"


def test_an_edited_script_is_left_alone_and_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    _with_engine(server)
    monkeypatch.setattr(party, "lua_root", lambda: _bundle(tmp_path / "v1", "one"))
    party.lay_bridge(server)
    mine = server / LUA_DIR / SIX[0]
    mine.write_text("-- my own change\n", encoding="utf-8")
    monkeypatch.setattr(party, "lua_root", lambda: _bundle(tmp_path / "v2", "two"))

    laid = party.lay_bridge(server)

    assert isinstance(laid, party.Deployment) and laid.kept == (SIX[0],)
    assert mine.read_text(encoding="utf-8") == "-- my own change\n"
    assert (server / LUA_DIR / SIX[1]).read_text(encoding="utf-8") == f"-- {SIX[1]} two\n"
    assert SIX[0] in party.kept_sentence(laid.kept)


def test_a_differing_file_nobody_recorded_is_the_players(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    _with_engine(server)
    folder = server / LUA_DIR
    folder.mkdir(parents=True)
    (folder / SIX[0]).write_text("-- copied by hand from an older build\n", encoding="utf-8")

    laid = party.lay_bridge(server)

    assert isinstance(laid, party.Deployment) and laid.kept == (SIX[0],)
    assert (folder / SIX[0]).read_text(
        encoding="utf-8"
    ) == "-- copied by hand from an older build\n"


def test_a_hand_copied_current_script_is_claimed_and_taken_back(tmp_path: Path) -> None:
    server = tmp_path / "server"
    _with_engine(server)
    folder = server / LUA_DIR
    folder.mkdir(parents=True)
    shutil.copy(party.lua_root() / "party" / SIX[0], folder / SIX[0])

    laid = party.lay_bridge(server)

    assert isinstance(laid, party.Deployment) and laid.kept == ()
    gone = party.take_back_bridge(server)
    assert isinstance(gone, party.TakenBack) and set(gone.gone) == set(SIX)


def test_taking_back_removes_an_older_bundled_version_and_keeps_an_edited_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = tmp_path / "server"
    _with_engine(server)
    monkeypatch.setattr(party, "lua_root", lambda: _bundle(tmp_path / "v1", "one"))
    party.lay_bridge(server)
    (server / LUA_DIR / SIX[0]).write_text("-- my own change\n", encoding="utf-8")
    monkeypatch.setattr(party, "lua_root", lambda: _bundle(tmp_path / "v2", "two"))

    gone = party.take_back_bridge(server)

    assert isinstance(gone, party.TakenBack)
    assert set(gone.gone) == set(SIX[1:]), "the v1 copies are not the v2 bundle, but were ours"
    assert gone.kept == (SIX[0],)
    assert _scripts(server) == [SIX[0]]


def test_scripts_are_written_as_plain_bytes_at_0644(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundle = _bundle(tmp_path / "v1", "one")
    (bundle / "party" / SIX[0]).chmod(0o600)
    server = tmp_path / "server"
    _with_engine(server)
    monkeypatch.setattr(party, "lua_root", lambda: bundle)

    party.lay_bridge(server)

    mode = stat.S_IMODE((server / LUA_DIR / SIX[0]).stat().st_mode)
    assert mode == scriptdeploy.SCRIPT_MODE


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
    server_dir, said, made, _rec = _rebuilt_with(tmp_path, engine=engine, lay=lay)
    return server_dir, said, made


def _rebuilt_with(
    tmp_path: Path, *, engine: bool, lay: bool = True, spy: Any = None
) -> tuple[Path, list[str], AzerothCoreInstaller, Recorder]:
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
    if spy is not None:
        spy(rec)
    said = list(made.rebuild(InstallOptions(server_dir=server_dir)))
    return server_dir, said, made, rec


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


def test_the_rebuild_lays_the_bridge_after_the_old_world_stopped_and_before_the_new_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[list[str]] = []

    def spy(rec: Recorder) -> None:
        real = party.lay_bridge
        rec.calls.clear()  # only what this rebuild did, not the install before it

        def watching(server_dir: Path) -> object:
            seen.append(list(rec.calls))
            return real(server_dir)

        monkeypatch.setattr(party, "lay_bridge", watching)

    _rebuilt_with(tmp_path, engine=True, spy=spy)

    assert seen, "the rebuild never laid the bridge"
    at_lay = seen[-1]
    assert "stop_servers" in at_lay, at_lay
    assert "recreate" not in at_lay, at_lay


# -- the seam behind the button --------------------------------------------


def _seam(server: Path, hold_server: Any = None) -> party.InstallParty:
    return party.InstallParty(
        load_catalog().get("wow-wotlk"),
        server,
        sql=None,  # type: ignore[arg-type]
        channel_for_saved=lambda: None,
        container="ac-worldserver",
        world_running=lambda: True,
        engine=lambda: party.BinaryRead(True, "in"),
        hold_server=hold_server,
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


def test_the_press_takes_the_servers_hold_before_it_copies(tmp_path: Path) -> None:
    events: list[str] = []

    @contextmanager
    def hold(press: str) -> Iterator[None]:
        events.append(f"hold:{press}")
        assert _scripts(tmp_path) == [], "the hold is taken before anything is copied"
        yield
        events.append("released")

    seam = _seam(tmp_path, hold_server=hold)

    seam.deploy_bridge()

    assert events == [f"hold:{party.DEPLOY_PRESS}", "released"]
    assert _scripts(tmp_path) == list(SIX)


def test_a_refused_hold_copies_nothing_and_says_why(tmp_path: Path) -> None:
    @contextmanager
    def hold(press: str) -> Iterator[None]:
        raise SaidByYulon("Another Yu'lon is rebuilding this server.")
        yield

    with pytest.raises(SaidByYulon):
        _seam(tmp_path, hold_server=hold).deploy_bridge()

    assert _scripts(tmp_path) == []
