"""T613 PR-2: receipts for an OUTSIDE add-on's files, and its Remove by receipt.

The owner's rule (2026-10-09, Q1): Remove deletes an outside add-on's own files
by receipt -- each file whose bytes still match what Yu'lon copied -- then the
folders that are left empty; an edited file is kept and named; `WTF/` is never
touched. A shipped add-on keeps the old rule: its folder is never deleted.

A set client shared by two servers: a file another server's receipt names is
left for that server on Remove, and an install that finds the same bytes already
there records them without copying.

Every test drives the REAL `Applier.install()` (from a folder, through
`module_source.copy_folder`, as the add-on route does) and the REAL
`Applier.remove()` into a directory under `tmp_path` standing in for the game
client; the assertions are about the bytes on disk and the receipts in the claim.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from yulon.apply import (
    CLAIM_FILE,
    Applier,
    ApplyRefusal,
    ClientCopy,
    FolderSource,
    read_client_copies,
)
from yulon.manifest import Manifest, parse_manifest
from yulon.module_source import copy_folder

ITEM = "pfui"
FILES = {
    "pfUI.toc": "## Interface: 11200\n## Title: pfUI\npfUI.lua\nmodules/bags.lua\n",
    "pfUI.lua": "-- pfUI core\n",
    "modules/bags.lua": "-- bags\n",
}


def _manifest(*, outside: bool = True, item: str = ITEM, name: str = "pfUI") -> Manifest:
    data: dict[str, object] = {
        "id": item,
        "name": name,
        "type": "mod",
        "game": "wow-vanilla",
        "client": [{"src": name, "dest": "addons", "name": name}],
    }
    if outside:
        data["origin"] = {"kind": "folder", "path": "/somewhere/pfUI", "added": "2026-10-09"}
    return parse_manifest(data)


def _source(tmp_path: Path, files: dict[str, str] = FILES, name: str = "pfUI") -> Path:
    root = tmp_path / "source"
    for rel, text in files.items():
        path = root / name / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def _client(tmp_path: Path) -> Path:
    client = tmp_path / "client"
    (client / "Interface" / "AddOns").mkdir(parents=True)
    (client / "WTF" / "Account" / "ME" / "SavedVariables").mkdir(parents=True)
    (client / "WTF" / "Account" / "ME" / "SavedVariables" / "pfUI.lua").write_text("saved\n")
    return client


def _install(
    tmp_path: Path,
    *,
    outside: bool = True,
    server: str = "server",
    client: Path | None = None,
    files: dict[str, str] = FILES,
    others: tuple[Path, ...] = (),
) -> tuple[Applier, Manifest, Path]:
    client = client if client is not None else _client(tmp_path)
    server_dir = tmp_path / server
    server_dir.mkdir(exist_ok=True)
    applier = Applier(server_dir, client_dir=client)
    if others:
        applier.other_server_dirs = lambda: others
    manifest = _manifest(outside=outside)
    applier.install(manifest, folder=FolderSource(_source(tmp_path, files), copy_folder))
    return applier, manifest, client / "Interface" / "AddOns" / "pfUI"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------ receipts


def test_an_outside_add_ons_install_records_every_file_it_copied_with_its_bytes(
    tmp_path: Path,
) -> None:
    applier, manifest, addon = _install(tmp_path)

    copies = read_client_copies(applier.clone_dir(manifest), item_id=ITEM)

    assert {(c.path, c.sha256, c.addon, c.step) for c in copies} == {
        (str(addon / rel), _sha(text), "pfUI", "pfUI") for rel, text in FILES.items()
    }


def test_a_shipped_add_ons_install_records_its_files_marked_shipped(tmp_path: Path) -> None:
    """Review round 1: so no outside add-on takes them for its own. Never taken back."""
    applier, manifest, addon = _install(tmp_path, outside=False)

    copies = read_client_copies(applier.clone_dir(manifest), item_id=ITEM)
    assert {(Path(c.path).name, c.shipped, c.addon) for c in copies} == {
        ("pfUI.toc", True, "pfUI"),
        ("pfUI.lua", True, "pfUI"),
        ("bags.lua", True, "pfUI"),
    }


def test_an_add_on_receipt_with_an_unreadable_add_on_name_is_not_a_receipt(
    tmp_path: Path,
) -> None:
    """Read as a `Data/` receipt it would be deleted by that rule, anywhere it names."""
    applier, manifest, addon = _install(tmp_path)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    for entry in claim["client_files"]:
        entry["addon"] = 7
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    assert read_client_copies(applier.clone_dir(manifest), item_id=ITEM) == ()


def test_the_receipt_round_trips_its_add_on_name() -> None:
    copy = ClientCopy(step="pfUI", path="/c/Interface/AddOns/pfUI/a.lua", sha256="0", addon="pfUI")

    assert copy.as_json()["addon"] == "pfUI"
    assert "addon" not in ClientCopy(step="x", path="/c/Data/p.MPQ", sha256="0").as_json()


# ------------------------------------------------------------------ Remove


def test_remove_takes_back_every_unchanged_file_and_the_empty_folders(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path)

    report = applier.remove(manifest)

    assert not os.path.lexists(addon), sorted(p.name for p in addon.rglob("*"))
    assert addon.parent.is_dir(), "took the AddOns folder itself"
    assert f"took back 3 files of the pfUI add-on from {addon}" in report.done
    assert not any("pfUI" in line for line in report.left_behind), report.left_behind


def test_an_edited_file_is_kept_and_named_and_its_folder_stays(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path)
    (addon / "modules" / "bags.lua").write_text("-- bags, as I like them\n", encoding="utf-8")

    report = applier.remove(manifest)

    assert (addon / "modules" / "bags.lua").read_text() == "-- bags, as I like them\n"
    assert not (addon / "pfUI.lua").exists() and not (addon / "pfUI.toc").exists()
    assert (
        "modules/bags.lua in the pfUI add-on folder (it has changed since Yu'lon copied it, "
        "so it left it alone)"
    ) in report.left_behind
    assert f"took back 2 files of the pfUI add-on from {addon}" in report.done


def test_a_file_the_player_added_keeps_the_folder_and_is_not_deleted(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path)
    (addon / "my-notes.txt").write_text("mine\n", encoding="utf-8")

    report = applier.remove(manifest)

    assert (addon / "my-notes.txt").read_text() == "mine\n"
    assert not (addon / "modules").exists(), "an emptied sub-folder stays"
    assert (
        f"the pfUI add-on folder in {addon.parent} stays: it still holds files Yu'lon did not "
        "put there"
    ) in report.left_behind


def test_remove_never_touches_wtf_even_when_a_receipt_names_a_file_there(
    tmp_path: Path,
) -> None:
    """A claim edited by hand (or by a bug) naming the saved variables with their own bytes.

    The one rule that holds it: a receipt is acted on only inside its own add-on's
    folder, so `WTF/` beside `Interface/` is never reached.
    """
    applier, manifest, addon = _install(tmp_path)
    saved = tmp_path / "client" / "WTF" / "Account" / "ME" / "SavedVariables" / "pfUI.lua"
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["client_files"].append(
        {"step": "pfUI", "path": str(saved), "sha256": _sha("saved\n"), "addon": "pfUI"}
    )
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    report = applier.remove(manifest)

    assert saved.read_text() == "saved\n"
    assert (
        f"{saved} (Yu'lon's record names it outside the pfUI add-on folder, so it left it alone)"
    ) in report.left_behind
    assert not os.path.lexists(addon), "the add-on's own files are still taken back"


def test_a_receipt_naming_another_add_ons_folder_is_left_alone(tmp_path: Path) -> None:
    applier, manifest, _addon = _install(tmp_path)
    other = tmp_path / "client" / "Interface" / "AddOns" / "Bagnon" / "Bagnon.lua"
    other.parent.mkdir()
    other.write_text("-- bagnon\n", encoding="utf-8")
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["client_files"].append(
        {"step": "pfUI", "path": str(other), "sha256": _sha("-- bagnon\n"), "addon": "pfUI"}
    )
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    applier.remove(manifest)

    assert other.read_text() == "-- bagnon\n"


def test_a_shipped_add_on_keeps_the_never_delete_rule(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path, outside=False)

    report = applier.remove(manifest)

    assert (addon / "pfUI.lua").is_file()
    assert (
        "the pfUI addon folder in your game client's Interface/AddOns "
        "(Yu'lon does not delete addons — disable it in the game's AddOns menu)"
    ) in report.left_behind


def test_an_outside_add_on_with_no_receipts_is_left_alone_and_said(tmp_path: Path) -> None:
    """Installed by a build before receipts (T596a): nothing proves which files are its."""
    applier, manifest, addon = _install(tmp_path)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    claim["client_files"] = []
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    report = applier.remove(manifest)

    assert (addon / "pfUI.lua").is_file()
    assert (
        f"the pfUI add-on folder in {addon.parent} (Yu'lon has no record of the files it "
        "copied there, so it left them alone; disable it in the game's AddOns menu)"
    ) in report.left_behind


def test_an_update_that_drops_a_file_takes_the_old_one_back(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path)
    newer = {k: v for k, v in FILES.items() if k != "modules/bags.lua"}
    newer["pfUI.toc"] = "## Interface: 11200\npfUI.lua\n"
    source = tmp_path / "newer"
    for rel, text in newer.items():
        (source / "pfUI" / rel).parent.mkdir(parents=True, exist_ok=True)
        (source / "pfUI" / rel).write_text(text, encoding="utf-8")

    applier.install(manifest, folder=FolderSource(source, copy_folder))

    assert not (addon / "modules" / "bags.lua").exists()
    assert not (addon / "modules").exists(), "taken back by the add-on rule: its folder too"
    assert (addon / "pfUI.toc").read_text() == newer["pfUI.toc"]
    copies = read_client_copies(applier.clone_dir(manifest), item_id=ITEM)
    assert {Path(c.path).name for c in copies} == {"pfUI.toc", "pfUI.lua"}


def test_uninstall_takes_an_outside_add_ons_files_back_by_the_same_rule(tmp_path: Path) -> None:
    applier, _manifest_, addon = _install(tmp_path)
    (addon / "pfUI.lua").write_text("-- mine now\n", encoding="utf-8")

    done, left = applier.take_back_everything()

    assert not (addon / "pfUI.toc").exists()
    assert (addon / "pfUI.lua").read_text() == "-- mine now\n"
    assert any("pfUI.lua in the pfUI add-on folder" in line for line in left), left
    saved = tmp_path / "client" / "WTF" / "Account" / "ME" / "SavedVariables" / "pfUI.lua"
    assert saved.read_text() == "saved\n"


# ------------------------------------------------------------------ a shared set client


def test_identical_bytes_already_there_are_recorded_and_not_copied(tmp_path: Path) -> None:
    """The second server's install over the first's: the file is not written again."""
    client = _client(tmp_path)
    first, _m, addon = _install(tmp_path, server="one", client=client)
    lua = addon / "pfUI.lua"
    os.utime(lua, (1_000_000_000, 1_000_000_000))

    second, manifest, _addon = _install(
        tmp_path, server="two", client=client, others=(first.server_dir,)
    )

    assert lua.stat().st_mtime == 1_000_000_000, "the same bytes were copied again"
    copies = read_client_copies(second.clone_dir(manifest), item_id=ITEM)
    assert str(lua) in {c.path for c in copies}
    assert not list(addon.glob("*.yulon-module-old*")), "the first server's file was set aside"


def test_a_file_another_servers_receipt_names_is_left_for_it(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first, manifest, addon = _install(tmp_path, server="one", client=client)
    second, _m, _addon = _install(tmp_path, server="two", client=client, others=(first.server_dir,))

    report = second.remove(manifest)

    assert (addon / "pfUI.lua").is_file() and (addon / "modules" / "bags.lua").is_file()
    assert (
        f"3 files of the pfUI add-on, which the server in {first.server_dir} also installed "
        "into this game client: they stay until it removes them too"
    ) in report.left_behind
    assert not any("took back" in line for line in report.done), report.done

    first.other_server_dirs = lambda: (second.server_dir,)
    first.remove(manifest)

    assert not os.path.lexists(addon), "the last server's Remove takes them back"


def test_with_no_other_servers_seam_another_servers_add_on_reads_as_the_players(
    tmp_path: Path,
) -> None:
    """The seam absent (a tab built outside the window): nothing proves whose it is, so ask."""
    client = _client(tmp_path)
    _install(tmp_path, server="one", client=client)

    with pytest.raises(ApplyRefusal, match="already in this game client"):
        _install(tmp_path, server="two", client=client)


@pytest.mark.parametrize("bad", ["", "../pfUI", "pfUI/../Bagnon"])
def test_a_receipt_whose_add_on_name_is_not_one_folder_is_left_alone(
    tmp_path: Path, bad: str
) -> None:
    applier, manifest, addon = _install(tmp_path)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    for entry in claim["client_files"]:
        entry["addon"] = bad
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    applier.remove(manifest)

    assert (addon / "pfUI.lua").is_file()


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs privileges on Windows")
def test_a_folder_inside_the_add_on_that_became_a_link_is_not_deleted_through(
    tmp_path: Path,
) -> None:
    """`modules/` swapped for a link to a copy elsewhere with the same bytes: nothing there goes."""
    applier, manifest, addon = _install(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "bags.lua").write_text(FILES["modules/bags.lua"], encoding="utf-8")
    (addon / "modules" / "bags.lua").unlink()
    (addon / "modules").rmdir()
    (addon / "modules").symlink_to(elsewhere, target_is_directory=True)

    report = applier.remove(manifest)

    assert (elsewhere / "bags.lua").read_text() == FILES["modules/bags.lua"]
    assert any(
        line.endswith(
            "(Yu'lon's record names it outside the pfUI add-on folder, so it left it " "alone)"
        )
        and "bags.lua" in line
        for line in report.left_behind
    ), report.left_behind


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs privileges on Windows")
def test_an_add_on_folder_that_is_a_link_now_is_left_whole(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path)
    moved = tmp_path / "my-dev-copy"
    addon.rename(moved)
    addon.symlink_to(moved, target_is_directory=True)

    report = applier.remove(manifest)

    assert (moved / "pfUI.lua").is_file() and (moved / "modules" / "bags.lua").is_file()
    assert (
        f"the pfUI add-on folder in {addon.parent} (it is a link to another place now, so "
        "Yu'lon took nothing back through it)"
    ) in report.left_behind


def test_when_the_other_servers_cannot_be_read_every_file_stays(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path)

    def unreadable() -> tuple[Path, ...]:
        raise OSError("state.json could not be read")

    applier.other_server_dirs = unreadable

    report = applier.remove(manifest)

    assert (addon / "pfUI.lua").is_file()
    assert (
        f"the pfUI add-on folder in {addon.parent} (Yu'lon could not read whether another "
        "server also installed it into this game client, so it left it alone)"
    ) in report.left_behind


def test_uninstall_reads_a_receipt_with_an_empty_add_on_name_as_none(tmp_path: Path) -> None:
    """Read as a `Data/` receipt, Uninstall's rule would delete the file wherever it is."""
    applier, manifest, addon = _install(tmp_path)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text(encoding="utf-8"))
    for entry in claim["client_files"]:
        entry["addon"] = ""
    claim_path.write_text(json.dumps(claim), encoding="utf-8")

    applier.take_back_everything()

    assert (addon / "pfUI.lua").is_file() and (addon / "pfUI.toc").is_file()


# ------------------------------------------------------------------ review round 1 (4040f913)


def _hand_installed(client: Path, files: dict[str, str] = FILES) -> Path:
    """The player's own pfUI, put there by hand before Yu'lon: no receipt names it."""
    folder = client / "Interface" / "AddOns" / "pfUI"
    for rel, text in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8")
    return folder


def test_a_players_own_add_on_of_that_name_is_refused_without_consent(tmp_path: Path) -> None:
    """Identical bytes are not "already ours": nothing names them, so they are the player's."""
    client = _client(tmp_path)
    mine = _hand_installed(client)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    manifest = _manifest()

    with pytest.raises(ApplyRefusal) as refused:
        applier.install(manifest, folder=FolderSource(_source(tmp_path), copy_folder))

    assert str(refused.value) == (
        "An add-on named pfUI is already in this game client, and Yu'lon did not put it there. "
        "Say to replace it, and Yu'lon sets it aside and puts it back when this one is "
        "removed. Nothing was changed."
    )
    assert not applier.clone_dir(manifest).exists()
    assert sorted(p.name for p in mine.rglob("*") if p.is_file()) == [
        "bags.lua",
        "pfUI.lua",
        "pfUI.toc",
    ]
    assert not list(mine.parent.glob("pfUI.yulon-*"))


@pytest.mark.parametrize("theirs", [FILES, {**FILES, "pfUI.lua": "-- my older pfUI\n"}])
def test_with_consent_the_players_add_on_is_set_aside_and_put_back_on_remove(
    tmp_path: Path, theirs: dict[str, str]
) -> None:
    client = _client(tmp_path)
    mine = _hand_installed(client, theirs)
    before = {
        p.relative_to(mine).as_posix(): p.read_bytes() for p in mine.rglob("*") if p.is_file()
    }
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    manifest = _manifest()

    report = applier.install(
        manifest, folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
    )

    aside = mine.parent / "pfUI.yulon-addon-old"
    assert aside.is_dir()
    assert {
        p.relative_to(aside).as_posix(): p.read_bytes() for p in aside.rglob("*") if p.is_file()
    } == before
    assert (mine / "pfUI.lua").read_text() == FILES["pfUI.lua"]
    assert f"set your own pfUI add-on aside as {aside.name} in {mine.parent}" in report.done

    removed = applier.remove(manifest)

    assert not aside.exists()
    assert {
        p.relative_to(mine).as_posix(): p.read_bytes() for p in mine.rglob("*") if p.is_file()
    } == before
    assert f"put your own pfUI add-on back in {mine.parent}" in removed.done


def test_a_players_add_on_set_aside_stays_aside_while_the_name_is_taken(tmp_path: Path) -> None:
    client = _client(tmp_path)
    mine = _hand_installed(client)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    manifest = _manifest()
    applier.install(
        manifest, folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
    )
    (mine / "pfUI.lua").write_text("-- edited after the install\n", encoding="utf-8")

    removed = applier.remove(manifest)

    aside = mine.parent / "pfUI.yulon-addon-old"
    assert aside.is_dir() and (mine / "pfUI.lua").is_file()
    assert (
        f"your own pfUI add-on, which Yu'lon set aside as {aside} when it installed this (pfUI "
        "is there again); rename it back to pfUI when you want it again"
    ) in removed.left_behind


def test_an_update_keeps_the_record_of_the_players_folder_set_aside(tmp_path: Path) -> None:
    client = _client(tmp_path)
    mine = _hand_installed(client)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    manifest = _manifest()
    applier.install(
        manifest, folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
    )

    applier.install(manifest, folder=FolderSource(_source(tmp_path), copy_folder))
    applier.remove(manifest)

    assert not (mine.parent / "pfUI.yulon-addon-old").exists()
    assert (mine / "pfUI.lua").read_text() == FILES["pfUI.lua"], "the player's copy is back"


def test_two_items_of_one_server_sharing_an_add_on_never_take_each_others_files(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    src = _source(tmp_path)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    one, two = _manifest(item="pfui"), _manifest(item="pfui-two")
    applier.install(one, folder=FolderSource(src, copy_folder))
    applier.install(two, folder=FolderSource(src, copy_folder))

    report = applier.remove(one)

    addon = client / "Interface" / "AddOns" / "pfUI"
    assert (addon / "pfUI.lua").is_file() and (addon / "modules" / "bags.lua").is_file()
    assert (
        "3 files of the pfUI add-on, which pfui-two on this server also installed into this "
        "game client: they stay until it is removed too"
    ) in report.left_behind

    applier.remove(two)

    assert not addon.exists()


def test_a_shipped_add_ons_files_are_never_taken_by_an_outside_one(tmp_path: Path) -> None:
    client = _client(tmp_path)
    src = _source(tmp_path)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    shipped, outside = _manifest(outside=False, item="shippedpf"), _manifest(item="pfui")
    applier.install(shipped, folder=FolderSource(src, copy_folder))
    applier.install(outside, folder=FolderSource(src, copy_folder))

    applier.remove(outside)

    assert (client / "Interface" / "AddOns" / "pfUI" / "pfUI.lua").is_file()


def test_uninstall_leaves_a_shipped_add_on_as_before(tmp_path: Path) -> None:
    applier, _manifest_, addon = _install(tmp_path, outside=False)

    applier.take_back_everything()

    assert (addon / "pfUI.lua").is_file()


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs privileges on Windows")
def test_a_receipt_spelled_with_dot_dot_through_a_link_is_refused(tmp_path: Path) -> None:
    """`AddOns/pfUI/L/../victim.txt` reads as inside, and through the link L it is not."""
    applier, manifest, addon = _install(tmp_path)
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    victim = outside / "victim.txt"
    victim.write_text("precious\n")
    (addon / "L").symlink_to(outside / "sub", target_is_directory=True)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text())
    entry = dict(claim["client_files"][0])
    entry["path"] = str(addon / "L" / ".." / "victim.txt")
    entry["sha256"] = _sha("precious\n")
    claim["client_files"].append(entry)
    claim_path.write_text(json.dumps(claim))

    report = applier.remove(manifest)

    assert victim.read_text() == "precious\n"
    assert any(
        "victim.txt" in line and "outside the pfUI add-on folder" in line
        for line in report.left_behind
    )


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs privileges on Windows")
def test_a_receipt_whose_real_place_is_outside_the_add_on_is_refused(tmp_path: Path) -> None:
    """No `..` at all: the add-on's folder holds a link made after the look, to elsewhere."""
    applier, manifest, addon = _install(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    victim = outside / "victim.txt"
    victim.write_text("precious\n")
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text())
    entry = dict(claim["client_files"][0])
    entry["path"] = str(addon / "Real" / "victim.txt")
    entry["sha256"] = _sha("precious\n")
    claim["client_files"].append(entry)
    claim_path.write_text(json.dumps(claim))
    (addon / "Real").symlink_to(outside, target_is_directory=True)

    applier.remove(manifest)

    assert victim.read_text() == "precious\n"


def test_an_aside_a_receipt_names_outside_its_files_place_is_never_moved(tmp_path: Path) -> None:
    """A put-back renames the aside onto the receipt's name: from anywhere, into the client."""
    applier, manifest, addon = _install(tmp_path)
    elsewhere = tmp_path / "elsewhere.lua"
    elsewhere.write_text("not an aside\n", encoding="utf-8")
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text())
    for entry in claim["client_files"]:
        if entry["path"].endswith("pfUI.lua"):
            entry["aside"] = str(elsewhere)
    claim_path.write_text(json.dumps(claim))

    applier.remove(manifest)

    assert elsewhere.read_text() == "not an aside\n"
    assert not (addon / "pfUI.lua").exists()


def test_a_receipt_not_spelled_as_its_own_plain_path_is_refused(tmp_path: Path) -> None:
    """`modules/../pfUI.lua`: inside, by any reading, and still not a path Yu'lon wrote."""
    applier, manifest, addon = _install(tmp_path)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text())
    for entry in claim["client_files"]:
        if entry["path"].endswith("pfUI.lua"):
            entry["path"] = str(addon / "modules" / ".." / "pfUI.lua")
    claim_path.write_text(json.dumps(claim))

    report = applier.remove(manifest)

    assert (addon / "pfUI.lua").is_file()
    assert any("modules/../pfUI.lua" in line for line in report.left_behind), report.left_behind


def test_data_receipts_leave_out_every_add_on_receipt(tmp_path: Path) -> None:
    from yulon.apply import client_receipts, data_receipts

    applier, _manifest_, _addon = _install(tmp_path)

    assert client_receipts(applier.server_dir) and data_receipts(applier.server_dir) == ()


def test_an_identical_file_of_the_players_in_a_known_folder_is_set_aside_and_put_back(
    tmp_path: Path,
) -> None:
    """The folder is another item's, so no question; the one file in it is the player's."""
    client = _client(tmp_path)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    smaller = {k: v for k, v in FILES.items() if k != "modules/bags.lua"}
    two = _manifest(item="pfui-two")
    applier.install(two, folder=FolderSource(_source(tmp_path / "two", smaller), copy_folder))
    addon = client / "Interface" / "AddOns" / "pfUI"
    (addon / "modules").mkdir()
    (addon / "modules" / "bags.lua").write_text(FILES["modules/bags.lua"], encoding="utf-8")
    one = _manifest(item="pfui")
    applier.install(one, folder=FolderSource(_source(tmp_path / "one"), copy_folder))

    applier.remove(one)

    assert (addon / "modules" / "bags.lua").read_text() == FILES["modules/bags.lua"]
    assert not list((addon / "modules").glob("*.yulon-module-old*"))


def test_an_update_of_a_shipped_add_on_never_takes_a_dropped_file_back(tmp_path: Path) -> None:
    applier, manifest, addon = _install(tmp_path, outside=False)
    newer = {k: v for k, v in FILES.items() if k != "modules/bags.lua"}

    applier.install(manifest, folder=FolderSource(_source(tmp_path / "v2", newer), copy_folder))

    assert (addon / "modules" / "bags.lua").is_file()


def test_a_recorded_folder_aside_that_is_not_beside_the_add_on_is_never_moved(
    tmp_path: Path,
) -> None:
    client = _client(tmp_path)
    _hand_installed(client)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    manifest = _manifest()
    applier.install(
        manifest, folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
    )
    elsewhere = tmp_path / "Documents"
    (elsewhere / "precious").mkdir(parents=True)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text())
    for entry in claim["client_files"]:
        if entry.get("folder"):
            entry["aside"] = str(elsewhere)
    claim_path.write_text(json.dumps(claim))

    report = applier.remove(manifest)

    assert (elsewhere / "precious").is_dir()
    assert not (client / "Interface" / "AddOns" / "pfUI").exists()
    assert any(str(elsewhere) in line and "not beside it" in line for line in report.left_behind)


def test_a_failed_install_after_the_players_folder_was_set_aside_puts_it_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import apply as apply_module

    client = _client(tmp_path)
    mine = _hand_installed(client, {"pfUI.toc": "## Interface: 11200\n", "mine.lua": "-- mine\n"})
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    real = apply_module._copy_unshared
    copied: list[str] = []

    def breaks_on_the_second(src: object, dst: object) -> object:
        copied.append(str(dst))
        if len(copied) == 2:
            raise OSError(28, "No space left on device", str(dst))
        return real(src, dst)  # type: ignore[arg-type]

    monkeypatch.setattr(apply_module, "_copy_unshared", breaks_on_the_second)

    with pytest.raises(OSError):
        applier.install(
            _manifest(), folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
        )

    assert (mine / "mine.lua").read_text() == "-- mine\n"
    assert not (mine.parent / "pfUI.yulon-addon-old").exists()
    assert sorted(p.name for p in mine.iterdir()) == ["mine.lua", "pfUI.toc"]


def test_the_play_keep_list_leaves_out_a_folder_set_aside(tmp_path: Path) -> None:
    from yulon.ui.controller_view import module_kept_files

    client = _client(tmp_path)
    _hand_installed(client)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    applier.install(
        _manifest(), folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
    )

    kept = module_kept_files(server, client)

    assert Path("Interface/AddOns/pfUI") not in kept
    assert Path("Interface/AddOns/pfUI/pfUI.lua") in kept
