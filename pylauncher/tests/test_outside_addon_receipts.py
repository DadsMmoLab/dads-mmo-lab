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

from yulon.apply import CLAIM_FILE, Applier, ClientCopy, FolderSource, read_client_copies
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
) -> tuple[Applier, Manifest, Path]:
    client = client if client is not None else _client(tmp_path)
    server_dir = tmp_path / server
    server_dir.mkdir(exist_ok=True)
    applier = Applier(server_dir, client_dir=client)
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


def test_a_shipped_add_ons_install_records_no_receipts_as_before(tmp_path: Path) -> None:
    """Its folder is never deleted, so there is nothing a receipt would be read for."""
    applier, manifest, addon = _install(tmp_path, outside=False)

    assert (addon / "pfUI.lua").is_file(), "the install did not happen"
    assert read_client_copies(applier.clone_dir(manifest), item_id=ITEM) == ()


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

    second, manifest, _addon = _install(tmp_path, server="two", client=client)

    assert lua.stat().st_mtime == 1_000_000_000, "the same bytes were copied again"
    copies = read_client_copies(second.clone_dir(manifest), item_id=ITEM)
    assert str(lua) in {c.path for c in copies}
    assert not list(addon.glob("*.yulon-module-old*")), "the first server's file was set aside"


def test_a_file_another_servers_receipt_names_is_left_for_it(tmp_path: Path) -> None:
    client = _client(tmp_path)
    first, manifest, addon = _install(tmp_path, server="one", client=client)
    second, _m, _addon = _install(tmp_path, server="two", client=client)
    second.other_server_dirs = lambda: (first.server_dir,)

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


def test_with_no_other_servers_seam_every_file_is_this_servers_to_take_back(
    tmp_path: Path,
) -> None:
    """The seam absent (a tab built outside the window): no other server is known."""
    client = _client(tmp_path)
    _install(tmp_path, server="one", client=client)
    second, manifest, addon = _install(tmp_path, server="two", client=client)

    second.remove(manifest)

    assert not os.path.lexists(addon)


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
