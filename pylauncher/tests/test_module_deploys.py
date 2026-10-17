"""T684: a deploy writes a record of its files, and Remove and Update act on that record only.

Battle Pass deploys `lua_scripts/` into the `lua_scripts` folder every ALE script
shares, and its Remove ran `rm -r lua_scripts/lib`: the player's own Lua libraries
went with it. Each test drives the real `Applier.install()` / `update()` / `remove()`.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_apply import (
    LUA,
    _FakeGit,
    _FakeSql,
    _git,
    _LocalOrigin,
    _origin,
    _origin_answers_as_the_manifest,
    _publish,
    _untouched,
)
from yulon import apply as apply_module
from yulon import module_deploys
from yulon.apply import Applier, ApplyError, ApplyRefusal
from yulon.git import CloneSpec, git_available
from yulon.manifest import parse_manifest

BATTLEPASS_SHAPE = {
    "id": "battlepass",
    "name": "Battle Pass",
    "type": "ale",
    "game": "wow-wotlk",
    "source": {"repo": "x/battlepass"},
    "deploy": [{"src": "lua_scripts/", "dest": f"{LUA}/"}],
}
BP_FILES = {
    "lua_scripts/battlepass/01_BP_Core.lua": "-- bp core",
    "lua_scripts/lib/CSMH/CSMH_SMH.ext": "-- csmh from battle pass",
}


def _player_file(server: Path, rel: str, text: str) -> Path:
    path = server / LUA / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_remove_keeps_the_players_own_library_beside_the_modules(tmp_path: Path) -> None:
    """C2: the player's `lib/MyLib/mylib.ext` survives a Battle Pass install and Remove."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    mine = _player_file(tmp_path, "lib/MyLib/mylib.ext", "-- the player's own")

    applier.install(m)
    applier.remove(m)

    assert mine.read_text(encoding="utf-8") == "-- the player's own"
    assert not (tmp_path / LUA / "lib" / "CSMH").exists()
    assert not (tmp_path / LUA / "battlepass").exists()


def test_remove_of_an_install_without_a_record_deletes_files_not_folders(tmp_path: Path) -> None:
    """An install from before the record: Remove deletes the checkout's files, one by one."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    applier.install(m)
    (tmp_path / module_deploys.DEPLOYS_FILE).unlink(missing_ok=True)  # an older Yu'lon's install
    mine = _player_file(tmp_path, "lib/MyLib/mylib.ext", "-- the player's own")

    applier.remove(m)

    assert mine.is_file()
    assert not (tmp_path / LUA / "lib" / "CSMH").exists()
    assert not (tmp_path / LUA / "battlepass").exists()


def test_install_sets_aside_a_players_file_at_a_module_path_and_remove_puts_it_back(
    tmp_path: Path,
) -> None:
    """The player's own `lib/CSMH/CSMH_SMH.ext` is never overwritten unseen, and comes back."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    theirs = _player_file(tmp_path, "lib/CSMH/CSMH_SMH.ext", "-- the player's csmh")

    report = applier.install(m)

    assert theirs.read_text(encoding="utf-8") == "-- csmh from battle pass"
    aside = theirs.with_name(theirs.name + ".yulon-module-old")
    assert aside.read_text(encoding="utf-8") == "-- the player's csmh"
    assert any("set aside" in line for line in report.done)

    applier.remove(m)

    assert theirs.read_text(encoding="utf-8") == "-- the player's csmh"
    assert not aside.exists()


def test_a_reinstall_does_not_set_aside_the_modules_own_files(tmp_path: Path) -> None:
    """The second install meets the first one's files: they are the module's, not the player's."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = _untouched(Applier(tmp_path, git=_FakeGit(BP_FILES)), m)
    applier.install(m)
    applier.install(m)

    assert not list((tmp_path / LUA).rglob("*.yulon-module-old"))
    applier.remove(m)
    assert not (tmp_path / LUA / "lib").exists()


def test_remove_with_the_checkout_gone_still_takes_back_the_recorded_files(
    tmp_path: Path,
) -> None:
    """The record, not the checkout, says what was deployed: a missing clone loses nothing."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    mine = _player_file(tmp_path, "lib/MyLib/mylib.ext", "-- the player's own")
    applier.install(m)
    clone = applier.clone_dir(m)
    subprocess.run(["rm", "-rf", str(clone)], check=True)

    applier.remove(m)

    assert mine.is_file()
    assert not (tmp_path / LUA / "battlepass").exists()
    assert not (tmp_path / LUA / "lib" / "CSMH").exists()


def test_remove_drops_the_items_record(tmp_path: Path) -> None:
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    applier.install(m)
    assert "ale/battlepass" in (module_deploys.read(tmp_path) or {})
    applier.remove(m)
    assert "ale/battlepass" not in (module_deploys.read(tmp_path) or {})


def test_an_unreadable_record_sets_nothing_aside_and_is_left_alone(tmp_path: Path) -> None:
    """A torn record is not "nothing recorded": nothing is moved on its word, and it stays."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    record = tmp_path / module_deploys.DEPLOYS_FILE
    record.write_text("{torn", encoding="utf-8")
    theirs = _player_file(tmp_path, "lib/CSMH/CSMH_SMH.ext", "-- the player's csmh")

    report = applier.install(m)

    assert record.read_text(encoding="utf-8") == "{torn"
    assert not theirs.with_name(theirs.name + ".yulon-module-old").exists()
    assert any(module_deploys.DEPLOYS_FILE in line for line in report.skipped)


def test_a_recorded_path_through_a_link_is_not_followed_on_remove(tmp_path: Path) -> None:
    """A folder the player swapped for a link after the install: nothing is deleted through it."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    applier.install(m)
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "CSMH").mkdir(parents=True)
    keep = elsewhere / "CSMH" / "CSMH_SMH.ext"
    keep.write_text("-- not under the server folder", encoding="utf-8")
    lib = tmp_path / LUA / "lib"
    subprocess.run(["rm", "-rf", str(lib)], check=True)
    lib.symlink_to(elsewhere, target_is_directory=True)

    report = applier.remove(m)

    assert keep.is_file()
    assert any("link" in line for line in report.skipped)


AW_SHAPE = {
    "id": "aw",
    "name": "Accountwide",
    "type": "ale",
    "game": "wow-wotlk",
    "source": {"repo": "x/aw"},
    "deploy": [{"src": "lua_scripts/AccountWide/", "dest": f"{LUA}/accountwide/"}],
}


@pytest.mark.skipif(not git_available(), reason="needs a host git to make a real checkout")
def test_update_takes_back_a_script_upstream_dropped_and_remove_leaves_none(
    tmp_path: Path,
) -> None:
    """C4: upstream renamed `AccountOld.lua`; after Update only the new one loads."""
    origin = _origin(tmp_path)
    _publish(
        origin,
        {
            "lua_scripts/AccountWide/AccountMoney.lua": "-- money {v}",
            "lua_scripts/AccountWide/AccountOld.lua": "-- old {v}",
        },
        "v1",
    )
    m = parse_manifest(AW_SHAPE)
    server = tmp_path / "server"
    applier = _origin_answers_as_the_manifest(Applier(server, git=_LocalOrigin(origin)), m)
    applier.install(m)
    dest = server / LUA / "accountwide"
    assert sorted(p.name for p in dest.iterdir()) == ["AccountMoney.lua", "AccountOld.lua"]

    _git(
        origin,
        "mv",
        "lua_scripts/AccountWide/AccountOld.lua",
        "lua_scripts/AccountWide/AccountNew.lua",
    )
    _publish(origin, {"lua_scripts/AccountWide/AccountMoney.lua": "-- money {v}"}, "v2")
    applier.update(m)

    assert sorted(p.name for p in dest.iterdir()) == ["AccountMoney.lua", "AccountNew.lua"]
    applier.remove(m)
    assert not dest.exists() or not list(dest.iterdir())


def test_the_record_keeps_only_paths_inside_the_server_folder(tmp_path: Path) -> None:
    """A hand-edited record that names `../x` is unusable whole, never half-trusted."""
    (tmp_path / module_deploys.DEPLOYS_FILE).write_text(
        json.dumps({"version": 1, "items": {"ale/x": {"files": ["../outside.lua"], "roots": []}}}),
        encoding="utf-8",
    )
    assert module_deploys.read(tmp_path) is None


def test_a_record_that_cannot_be_written_moves_none_of_the_players_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A player's file is moved aside only once the record names it: no record, no move."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    theirs = _player_file(tmp_path, "lib/CSMH/CSMH_SMH.ext", "-- the player's csmh")
    monkeypatch.setattr(module_deploys, "write", lambda *_a, **_k: "the disk is full")

    report = applier.install(m)

    assert not theirs.with_name(theirs.name + ".yulon-module-old").exists()
    assert any("the disk is full" in line for line in report.skipped)


# ---------------------------------------------------------------- review round 2


class _ResettingGit(_FakeGit):
    """`_FakeGit` whose second clone is what `reset --hard` leaves: only the new files."""

    def clone(self, spec: CloneSpec) -> None:
        if spec.dest.is_dir():
            for entry in spec.dest.iterdir():
                if entry.name in (".git", apply_module.CLAIM_FILE):
                    continue
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
        super().clone(spec)


def _replace_failing_on(monkeypatch: pytest.MonkeyPatch, nth: int) -> None:
    """`os.replace` in the applier, refusing its `nth` move of a file to an aside name."""
    real = os.replace
    seen = {"n": 0}

    def replace(src: object, dst: object) -> None:
        if str(dst).endswith(apply_module.ASIDE_SUFFIX):
            seen["n"] += 1
            if seen["n"] == nth:
                raise PermissionError(13, "The process cannot access the file", str(src))
        real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(apply_module.os, "replace", replace)


def _record(server: Path) -> module_deploys.Deployed:
    ledger = module_deploys.read(server)
    assert ledger is not None
    return ledger["ale/battlepass"]


def test_a_set_aside_that_fails_copies_nothing_and_leaves_the_players_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MUST 1: the second move refused (a file held open on Windows): no file of the player's
    is overwritten, the one already moved is back, and the record claims nothing."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    core = _player_file(tmp_path, "battlepass/01_BP_Core.lua", "-- the player's core")
    csmh = _player_file(tmp_path, "lib/CSMH/CSMH_SMH.ext", "-- the player's csmh")
    _replace_failing_on(monkeypatch, 2)

    with pytest.raises(ApplyRefusal) as refused:
        applier.install(m)

    assert core.read_text(encoding="utf-8") == "-- the player's core"
    assert csmh.read_text(encoding="utf-8") == "-- the player's csmh"
    assert not list((tmp_path / LUA).rglob("*" + apply_module.ASIDE_SUFFIX))
    assert "ale/battlepass" not in (module_deploys.read(tmp_path) or {})
    assert "could not be set aside" in str(refused.value)


def test_after_a_failed_set_aside_the_record_names_only_the_moves_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An Update (not a first install) keeps its record: only the move that happened is in it."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    git = _ResettingGit({"lua_scripts/battlepass/01_BP_Core.lua": "-- bp core"})
    applier = _untouched(Applier(tmp_path, git=git), m)
    applier.install(m)
    first = _player_file(tmp_path, "lib/A.ext", "-- the player's a")
    second = _player_file(tmp_path, "lib/B.ext", "-- the player's b")
    git.files = {
        "lua_scripts/battlepass/01_BP_Core.lua": "-- bp core",
        "lua_scripts/lib/A.ext": "-- a from battle pass",
        "lua_scripts/lib/B.ext": "-- b from battle pass",
    }
    _replace_failing_on(monkeypatch, 2)

    with pytest.raises(ApplyRefusal):
        applier.install(m)

    asides = _record(tmp_path).asides
    assert list(asides) == [f"{LUA}/lib/A.ext"]
    assert (tmp_path / asides[f"{LUA}/lib/A.ext"]).read_text(
        encoding="utf-8"
    ) == "-- the player's a"
    assert second.read_text(encoding="utf-8") == "-- the player's b"
    assert not first.exists()


def _a_planned_move_never_made(tmp_path: Path) -> tuple[Applier, Path]:
    """The record a process killed between its write and its moves leaves: a set-aside it
    lists, no file at the aside name, and the player's file still at the module's name."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = _untouched(Applier(tmp_path, git=_FakeGit(BP_FILES)), m)
    applier.install(m)
    record = _record(tmp_path)
    rel = f"{LUA}/lib/CSMH/CSMH_SMH.ext"
    module_deploys.write(
        tmp_path,
        "ale/battlepass",
        module_deploys.Deployed(
            files=record.files,
            roots=record.roots,
            asides={rel: rel + apply_module.ASIDE_SUFFIX},
        ),
    )
    theirs = tmp_path / rel
    theirs.write_text("-- the player's csmh", encoding="utf-8")
    return applier, theirs


def test_a_planned_set_aside_never_made_does_not_let_the_next_install_overwrite(
    tmp_path: Path,
) -> None:
    applier, theirs = _a_planned_move_never_made(tmp_path)
    applier.install(parse_manifest(BATTLEPASS_SHAPE))
    aside = theirs.with_name(theirs.name + apply_module.ASIDE_SUFFIX)
    assert aside.read_text(encoding="utf-8") == "-- the player's csmh"


def test_a_planned_set_aside_never_made_does_not_let_remove_delete_the_players_file(
    tmp_path: Path,
) -> None:
    applier, theirs = _a_planned_move_never_made(tmp_path)
    applier.remove(parse_manifest(BATTLEPASS_SHAPE))
    assert theirs.read_text(encoding="utf-8") == "-- the player's csmh"


def test_remove_keeps_a_module_file_the_player_changed_and_says_so(tmp_path: Path) -> None:
    """SHOULD 2: the record's hash says whose bytes these are."""
    m = parse_manifest(BATTLEPASS_SHAPE)
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    applier.install(m)
    core = tmp_path / LUA / "battlepass" / "01_BP_Core.lua"
    core.write_text("-- bp core, tuned by the player", encoding="utf-8")

    report = applier.remove(m)

    assert core.read_text(encoding="utf-8") == "-- bp core, tuned by the player"
    assert any("kept because you changed it" in line for line in report.skipped)


def test_an_install_time_patch_is_not_mistaken_for_the_players_change(tmp_path: Path) -> None:
    """The hash is taken after this app's own patches, so Remove still deletes a patched file."""
    m = parse_manifest(
        {
            **BATTLEPASS_SHAPE,
            "patches": [
                {"file": f"{LUA}/battlepass/01_BP_Core.lua", "find": "core", "replace": "x"}
            ],
        }
    )
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    applier.install(m)
    applier.remove(m)
    assert not (tmp_path / LUA / "battlepass" / "01_BP_Core.lua").exists()


def test_an_update_keeps_a_dropped_file_the_player_changed(tmp_path: Path) -> None:
    m = parse_manifest(BATTLEPASS_SHAPE)
    git = _ResettingGit(BP_FILES)
    applier = _untouched(Applier(tmp_path, git=git), m)
    applier.install(m)
    old = tmp_path / LUA / "lib" / "CSMH" / "CSMH_SMH.ext"
    old.write_text("-- csmh, tuned by the player", encoding="utf-8")
    git.files = {"lua_scripts/battlepass/01_BP_Core.lua": "-- bp core"}

    report = applier.install(m)

    assert old.read_text(encoding="utf-8") == "-- csmh, tuned by the player"
    assert any("kept because you changed it" in line for line in report.skipped)


def test_a_file_another_module_deployed_refuses_the_install_by_name(tmp_path: Path) -> None:
    """SHOULD 3: another module's file is not the player's: refused, both named, nothing copied."""
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES))
    applier.install(parse_manifest(BATTLEPASS_SHAPE))
    other = parse_manifest({**BATTLEPASS_SHAPE, "id": "csmh-pack", "name": "CSMH pack"})
    git = _FakeGit(
        {"lua_scripts/lib/CSMH/CSMH_SMH.ext": "-- csmh pack", "lua_scripts/pack/p.lua": "-- p"}
    )

    with pytest.raises(ApplyRefusal) as refused:
        Applier(tmp_path, git=git).install(other)

    assert "battlepass" in str(refused.value)
    assert not (tmp_path / LUA / "pack").exists()
    assert not list((tmp_path / LUA).rglob("*" + apply_module.ASIDE_SUFFIX))


def test_a_case_only_rename_upstream_does_not_delete_the_new_file_where_case_folds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SHOULD 4: on Windows and macOS `Foo.lua` and `foo.lua` are one file; taking back the
    old name would delete the one just deployed."""
    monkeypatch.setattr(module_deploys, "folds_case", lambda _server: True)
    m = parse_manifest(BATTLEPASS_SHAPE)
    git = _ResettingGit({"lua_scripts/battlepass/Foo.lua": "-- foo"})
    applier = _untouched(Applier(tmp_path, git=git), m)
    applier.install(m)
    git.files = {"lua_scripts/battlepass/foo.lua": "-- foo"}

    report = applier.install(m)

    assert not any(line.endswith("Foo.lua") and line.startswith("rm ") for line in report.done)
    assert (tmp_path / LUA / "battlepass" / "foo.lua").is_file()


def test_a_first_install_refused_while_the_world_runs_takes_its_files_back(
    tmp_path: Path,
) -> None:
    """SHOULD 5: the running-world guard refuses after the deploy; nothing of it stays."""
    m = parse_manifest(
        {
            **BATTLEPASS_SHAPE,
            # A patch of the install's own, before the refusal: still the install's bytes.
            "patches": [
                {"file": f"{LUA}/battlepass/01_BP_Core.lua", "find": "core", "replace": "x"}
            ],
            "sql": [{"db": "world", "statement": "DELETE FROM creature WHERE id = 0;"}],
        }
    )
    applier = Applier(tmp_path, git=_FakeGit(BP_FILES), sql=_FakeSql(), world_running=lambda: True)
    theirs = _player_file(tmp_path, "lib/CSMH/CSMH_SMH.ext", "-- the player's csmh")

    with pytest.raises(ApplyError) as refused:
        applier.install(m)

    assert not (tmp_path / LUA / "battlepass").exists()
    assert theirs.read_text(encoding="utf-8") == "-- the player's csmh"
    assert "ale/battlepass" not in (module_deploys.read(tmp_path) or {})
    assert "taken back" in str(refused.value)
