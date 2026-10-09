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
    ADDON_ASIDES_FILE,
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
        "An add-on named pfUI is already in this game client, and Yu'lon did not put it there, "
        "so it did not replace it. Move or rename your own pfUI folder in Interface/AddOns "
        "first, then install again. Nothing was changed."
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
        f"is there again); Yu'lon keeps a note of it in {server / ADDON_ASIDES_FILE}; rename it "
        "back to pfUI when you want it again"
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


@pytest.mark.parametrize("where", ["elsewhere", "beside, named otherwise"])
def test_a_recorded_folder_aside_that_is_not_beside_the_add_on_is_never_moved(
    tmp_path: Path, where: str
) -> None:
    """One rule each: the right name in another folder, and another name beside it."""
    client = _client(tmp_path)
    _hand_installed(client)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    manifest = _manifest()
    applier.install(
        manifest, folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
    )
    if where == "elsewhere":
        elsewhere = tmp_path / "Documents" / "pfUI.yulon-addon-old"
    else:
        elsewhere = client / "Interface" / "AddOns" / "Bagnon"
    (elsewhere / "precious").mkdir(parents=True)
    claim_path = applier.clone_dir(manifest) / CLAIM_FILE
    claim = json.loads(claim_path.read_text())
    for entry in claim["client_files"]:
        if entry.get("folder"):
            entry["aside"] = str(elsewhere)
    claim_path.write_text(json.dumps(claim))
    # The note beside the server says the same, or it would put the real aside back.
    notes = _noted(server)
    for note in notes:
        note["aside"] = str(elsewhere)
    (server / ADDON_ASIDES_FILE).write_text(json.dumps(notes), encoding="utf-8")

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


# ------------------------------------------------------------------ re-review (63674663)

MINE = "-- my own hand-installed pfUI\n"


def _replaced(tmp_path: Path) -> tuple[Applier, Manifest, Path, Path]:
    """The player's own pfUI, replaced with their yes: it sits aside, recorded."""
    client = _client(tmp_path)
    own = client / "Interface" / "AddOns" / "pfUI"
    own.mkdir()
    (own / "pfUI.lua").write_text(MINE)
    (own / "extra.lua").write_text("mine\n")
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)
    manifest = _manifest()
    applier.install(
        manifest, folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
    )
    return applier, manifest, own, own.parent / "pfUI.yulon-addon-old"


def _noted(server: Path) -> list[dict[str, str]]:
    from yulon.apply import ADDON_ASIDES_FILE

    path = server / ADDON_ASIDES_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []


def test_setting_the_players_folder_aside_notes_it_beside_the_server(tmp_path: Path) -> None:
    applier, _manifest_, own, aside = _replaced(tmp_path)

    assert _noted(applier.server_dir) == [
        {"item": ITEM, "addon": "pfUI", "target": str(own), "aside": str(aside)}
    ]


def test_putting_it_back_drops_the_note(tmp_path: Path) -> None:
    applier, manifest, own, aside = _replaced(tmp_path)

    applier.remove(manifest)

    assert (own / "pfUI.lua").read_text() == MINE and not aside.exists()
    assert _noted(applier.server_dir) == []


def test_an_update_that_moves_the_add_on_in_its_source_still_puts_the_folder_back(
    tmp_path: Path,
) -> None:
    """(C) Matched by add-on name, not by the step: `pfUI` became `sub/pfUI` upstream."""
    applier, _m, own, aside = _replaced(tmp_path)
    moved = tmp_path / "source2"
    for rel, text in FILES.items():
        (moved / "sub" / "pfUI" / rel).parent.mkdir(parents=True, exist_ok=True)
        (moved / "sub" / "pfUI" / rel).write_text(text, encoding="utf-8")
    newer = parse_manifest(
        {
            "id": ITEM,
            "name": "pfUI",
            "type": "mod",
            "game": "wow-vanilla",
            "origin": {"kind": "folder", "path": "/somewhere/pfUI", "added": "2026-10-09"},
            "client": [{"src": "sub/pfUI", "dest": "addons", "name": "pfUI"}],
        }
    )
    updated = applier.install(newer, folder=FolderSource(moved, copy_folder), replacing=True)

    assert aside.is_dir(), "still written by the item, so still aside"
    assert not any("yulon-addon-old" in line for line in updated.skipped), updated.skipped
    report = applier.remove(newer)

    assert (own / "pfUI.lua").read_text() == MINE, report.left_behind
    assert not aside.exists()


def test_an_update_that_drops_the_add_on_still_puts_the_folder_back_at_remove(
    tmp_path: Path,
) -> None:
    applier, _m, own, aside = _replaced(tmp_path)
    other = tmp_path / "source3"
    (other / "Other").mkdir(parents=True)
    (other / "Other" / "Other.toc").write_text("## Interface: 11200\n")
    renamed = parse_manifest(
        {
            "id": ITEM,
            "name": "pfUI",
            "type": "mod",
            "game": "wow-vanilla",
            "origin": {"kind": "folder", "path": "/somewhere/pfUI", "added": "2026-10-09"},
            "client": [{"src": "Other", "dest": "addons", "name": "Other"}],
        }
    )
    applier.install(renamed, folder=FolderSource(other, copy_folder), replacing=True)

    assert (own / "pfUI.lua").read_text() == MINE, "no longer written: back at the Update"
    assert not aside.exists() and _noted(applier.server_dir) == []
    report = applier.remove(renamed)

    assert (own / "pfUI.lua").read_text() == MINE or any(
        str(aside) in line for line in report.left_behind
    ), report.left_behind
    assert (own / "pfUI.lua").read_text() == MINE


def test_with_the_other_servers_unreadable_the_aside_is_named_and_its_note_kept(
    tmp_path: Path,
) -> None:
    """(B) The files stay (whose they are cannot be read), so the name is taken."""
    applier, manifest, own, aside = _replaced(tmp_path)

    def unreadable() -> tuple[Path, ...]:
        raise OSError("nope")

    applier.other_server_dirs = unreadable

    report = applier.remove(manifest)

    assert not applier.clone_dir(manifest).exists()
    assert aside.is_dir() and (aside / "pfUI.lua").read_text() == MINE
    assert any(str(aside) in line for line in report.left_behind), report.left_behind
    assert [n["aside"] for n in _noted(applier.server_dir)] == [str(aside)]


def test_with_no_client_folder_the_aside_is_named_and_its_note_kept(tmp_path: Path) -> None:
    """(D) Removed from a record that has lost its client folder."""
    applier, manifest, _own, aside = _replaced(tmp_path)
    blind = Applier(applier.server_dir, client_dir=None)

    report = blind.remove(manifest)

    assert not blind.clone_dir(manifest).exists()
    assert any(str(aside) in line for line in report.left_behind), report.left_behind
    assert [n["aside"] for n in _noted(applier.server_dir)] == [str(aside)]


def test_a_note_that_outlived_its_clone_is_acted_on_by_the_next_remove(tmp_path: Path) -> None:
    """The note is the record once the clone is gone: the next Install and Remove use it."""
    applier, manifest, own, aside = _replaced(tmp_path)
    Applier(applier.server_dir, client_dir=None).remove(manifest)
    for p in sorted(own.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    own.rmdir()

    applier.install(manifest, folder=FolderSource(_source(tmp_path / "again"), copy_folder))
    applier.remove(manifest)

    assert (own / "pfUI.lua").read_text() == MINE
    assert _noted(applier.server_dir) == []


def test_the_refusal_without_a_yes_says_what_to_do_instead(tmp_path: Path) -> None:
    client = _client(tmp_path)
    _hand_installed(client)
    server = tmp_path / "server"
    server.mkdir()

    with pytest.raises(ApplyRefusal) as refused:
        Applier(server, client_dir=client).install(
            _manifest(), folder=FolderSource(_source(tmp_path), copy_folder)
        )

    assert str(refused.value) == (
        "An add-on named pfUI is already in this game client, and Yu'lon did not put it there, "
        "so it did not replace it. Move or rename your own pfUI folder in Interface/AddOns "
        "first, then install again. Nothing was changed."
    )


def test_an_update_that_drops_one_of_two_add_ons_puts_back_only_that_ones_folder(
    tmp_path: Path,
) -> None:
    """The add-on still written keeps its aside, said nowhere; the dropped one's comes back."""
    client = _client(tmp_path)
    addons = client / "Interface" / "AddOns"
    for name in ("pfUI", "pfUI_Config"):
        (addons / name).mkdir()
        (addons / name / "mine.lua").write_text(f"-- my {name}\n")
    src = tmp_path / "two"
    for name in ("pfUI", "pfUI_Config"):
        (src / name).mkdir(parents=True)
        (src / name / f"{name}.toc").write_text("## Interface: 11200\n")
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client)

    def item(*names: str) -> Manifest:
        return parse_manifest(
            {
                "id": ITEM,
                "name": "pfUI",
                "type": "mod",
                "game": "wow-vanilla",
                "origin": {"kind": "folder", "path": "/x", "added": "2026-10-09"},
                "client": [{"src": n, "dest": "addons", "name": n} for n in names],
            }
        )

    applier.install(
        item("pfUI", "pfUI_Config"), folder=FolderSource(src, copy_folder), replace_addons=True
    )

    updated = applier.install(item("pfUI"), folder=FolderSource(src, copy_folder), replacing=True)

    assert (addons / "pfUI_Config" / "mine.lua").read_text() == "-- my pfUI_Config\n"
    assert (addons / "pfUI.yulon-addon-old" / "mine.lua").read_text() == "-- my pfUI\n"
    assert not any("pfUI.yulon-addon-old" in line for line in updated.skipped), updated.skipped
    assert [n["addon"] for n in _noted(server)] == ["pfUI"]


# ------------------------------------------------------------------ round 3 (14eab2c6)


@pytest.mark.parametrize("bad", ["elsewhere", "../../pfUI", ""])
def test_a_tampered_notes_add_on_name_never_moves_the_folder_out_of_add_ons(
    tmp_path: Path, bad: str
) -> None:
    """The reviewer's F: `addons / "/elsewhere/pfUI"` is `/elsewhere/pfUI`."""
    import shutil

    applier, manifest, _own, aside = _replaced(tmp_path)
    outside = tmp_path / "elsewhere" / "pfUI"
    outside.parent.mkdir()
    name = str(outside) if bad == "elsewhere" else bad
    notes = _noted(applier.server_dir)
    notes[0]["addon"] = name
    (applier.server_dir / ADDON_ASIDES_FILE).write_text(json.dumps(notes), encoding="utf-8")
    shutil.rmtree(applier.clone_dir(manifest))  # only the note speaks now

    _done, left = applier.take_back_everything()

    assert not outside.exists(), "moved out of Interface/AddOns by a tampered note"
    assert aside.is_dir() and (aside / "pfUI.lua").read_text() == MINE
    assert any(
        "is not one folder in Interface/AddOns, so Yu'lon left it alone" in line for line in left
    ), left


def test_read_addon_asides_drops_an_entry_whose_add_on_is_not_one_folder(tmp_path: Path) -> None:
    from yulon.apply import read_addon_asides

    good = {
        "item": "pfui",
        "addon": "pfUI",
        "target": "/c/pfUI",
        "aside": "/c/pfUI.yulon-addon-old",
    }
    (tmp_path / ADDON_ASIDES_FILE).write_text(
        json.dumps([good, {**good, "addon": "../x"}, {**good, "addon": "/abs"}]), encoding="utf-8"
    )

    assert read_addon_asides(tmp_path) == [good]


def test_at_uninstall_a_kept_aside_is_named_by_its_path_and_no_note_is_promised(
    tmp_path: Path,
) -> None:
    """The server folder, note and all, is deleted right after: the path is what is left."""
    applier, _manifest_, own, aside = _replaced(tmp_path)
    (own / "player-new.lua").write_text("x")  # keeps the name taken

    _done, left = applier.take_back_everything()

    assert aside.is_dir()
    lines = [line for line in left if str(aside) in line]
    assert lines and not any("keeps a note" in line for line in lines), left


def test_two_threads_noting_asides_lose_neither(tmp_path: Path) -> None:
    """The note is read, changed and written whole: one module lock serialises that."""
    import threading

    from yulon import apply as apply_module

    server = tmp_path / "server"
    server.mkdir()
    real = apply_module._write_addon_asides
    gate = threading.Barrier(2, timeout=1)

    def slow(server_dir: Path, entries: object) -> None:
        try:
            gate.wait()
        except threading.BrokenBarrierError:
            pass
        real(server_dir, entries)  # type: ignore[arg-type]

    apply_module._write_addon_asides = slow  # type: ignore[assignment]
    try:
        threads = [
            threading.Thread(
                target=apply_module._add_addon_aside,
                args=(server, {"item": n, "addon": n, "target": f"/c/{n}", "aside": f"/c/{n}.a"}),
            )
            for n in ("one", "two")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    finally:
        apply_module._write_addon_asides = real  # type: ignore[assignment]

    assert sorted(e["item"] for e in apply_module.read_addon_asides(server)) == ["one", "two"]


# ------------------------------------------------------------------ round 4 closes (44b0afc2)


@pytest.mark.parametrize(
    "bad",
    [
        "D:pfUI",
        "pf<UI",
        'pf"UI',
        "pf|UI",
        "pf?UI",
        "pf*UI",
        "pf\x01UI",
        "pfUI.",
        "pfUI ",
        "CON",
        "com1.x",
    ],
)
def test_a_name_windows_cannot_hold_as_a_folder_is_not_one_folder_name(bad: str) -> None:
    """`addons / "D:pfUI"` on Windows is drive-relative: outside Interface/AddOns."""
    from yulon.apply import _one_folder_name

    assert not _one_folder_name(bad)
    assert _one_folder_name("pfUI") and _one_folder_name("Bagnon_Config-2.0")


def test_a_note_naming_a_drive_relative_add_on_is_dropped(tmp_path: Path) -> None:
    from yulon.apply import read_addon_asides

    good = {
        "item": "pfui",
        "addon": "pfUI",
        "target": "/c/pfUI",
        "aside": "/c/pfUI.yulon-addon-old",
    }
    (tmp_path / ADDON_ASIDES_FILE).write_text(
        json.dumps([good, {**good, "addon": "D:pfUI"}]), encoding="utf-8"
    )

    assert read_addon_asides(tmp_path) == [good]


def test_the_real_parent_check_is_the_last_guard_when_a_name_slips_past(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Round 4: with the name rule gone, only the real-parent check keeps the folder in."""
    import shutil

    from yulon import apply as apply_module

    applier, manifest, _own, aside = _replaced(tmp_path)
    outside = tmp_path / "elsewhere" / "pfUI"
    outside.parent.mkdir()
    notes = _noted(applier.server_dir)
    notes[0]["addon"] = str(outside)
    (applier.server_dir / ADDON_ASIDES_FILE).write_text(json.dumps(notes), encoding="utf-8")
    shutil.rmtree(applier.clone_dir(manifest))
    monkeypatch.setattr(apply_module, "_one_folder_name", lambda value: isinstance(value, str))

    _done, left = applier.take_back_everything()

    assert not outside.exists()
    assert aside.is_dir()
    assert any("is not in" in line and "so Yu'lon did not move it" in line for line in left), left


# ------------------------------------------- the asides note under the server hold (batch combine)


def test_a_second_yulons_take_back_is_refused_while_the_first_holds_the_server(
    tmp_path: Path,
) -> None:
    """The note beside the server is per-server state another Yu'lon shares (T568's hold).

    Mutation this catches: `_put_players_folders_back()` under the process lock only.
    """
    from tests.test_more_writes_hold import _Hold

    applier, _manifest_, own, aside = _replaced(tmp_path)
    note = (applier.server_dir / ADDON_ASIDES_FILE).read_bytes()
    other = Applier(
        applier.server_dir, client_dir=applier.client_dir, hold_server=_Hold(refuse=True)
    )

    with pytest.raises(ApplyRefusal, match="Another Yu'lon is working on"):
        other.take_back_everything()

    assert (applier.server_dir / ADDON_ASIDES_FILE).read_bytes() == note
    assert aside.is_dir() and (aside / "pfUI.lua").read_text() == MINE


def test_the_take_back_holds_the_server_under_its_own_press(tmp_path: Path) -> None:
    from tests.test_more_writes_hold import _Hold
    from yulon.apply import PUT_FOLDERS_BACK_PRESS

    applier, _manifest_, own, aside = _replaced(tmp_path)
    hold = _Hold()
    held = Applier(applier.server_dir, client_dir=applier.client_dir, hold_server=hold)
    held.take_back_everything()
    assert hold.events[:1] == [f"hold:{PUT_FOLDERS_BACK_PRESS}"]
    assert not aside.exists() and (own / "pfUI.lua").read_text() == MINE


def test_setting_a_folder_aside_is_refused_whole_while_another_yulon_holds_the_server(
    tmp_path: Path,
) -> None:
    """Install takes the hold first, so the player's folder is never moved and nothing noted."""
    from tests.test_more_writes_hold import _Hold

    client = _client(tmp_path)
    own = client / "Interface" / "AddOns" / "pfUI"
    own.mkdir()
    (own / "pfUI.lua").write_text(MINE)
    server = tmp_path / "server"
    server.mkdir()
    applier = Applier(server, client_dir=client, hold_server=_Hold(refuse=True))
    with pytest.raises(ApplyRefusal, match="Another Yu'lon is working on"):
        applier.install(
            _manifest(), folder=FolderSource(_source(tmp_path), copy_folder), replace_addons=True
        )
    assert (own / "pfUI.lua").read_text() == MINE
    assert not (own.parent / "pfUI.yulon-addon-old").exists()
    assert not (server / ADDON_ASIDES_FILE).exists()
