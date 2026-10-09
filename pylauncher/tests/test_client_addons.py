"""T613 PR-2: the add-on route for every game -- derive, guard, install, update, remove.

Drives the REAL `ClientAddons` over a REAL `Applier` writing into a directory
under `tmp_path` that stands in for the game client. Only the network (a zip
link's opener, a git link's clone) is replaced; the assertions are about the
files in the client, the record in the user layer and the manifest derived.
"""

from __future__ import annotations

import hashlib
import io
import os
import zipfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest

from tests.test_addon_archive import _Opener, _Response
from yulon import client_packs
from yulon.addon_archive import AddonRefusal
from yulon.apply import Applier, ApplyRefusal, read_client_copies
from yulon.client_addons import (
    AddonOnlyApplier,
    ClientAddons,
    client_only_refusal,
    item_name,
    repository_source,
)
from yulon.git import CloneSpec
from yulon.manifest import Manifest, parse_manifest

NOTHING = " Nothing was changed."
GAME = "wow-vanilla"
TOC = "## Interface: 11200\n## Title: pfUI\npfUI.lua\n"
PFUI = {"pfUI-master/pfUI.toc": TOC, "pfUI-master/pfUI.lua": "-- pfUI\n"}


@pytest.fixture(autouse=True)
def _cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cache = tmp_path / "cache"
    monkeypatch.setattr(client_packs, "cache_dir", lambda: cache)
    return cache


def _tree(root: Path, files: Mapping[str, str]) -> Path:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _zip(path: Path, files: Mapping[str, str]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for rel, text in files.items():
            archive.writestr(rel, text)
    return path


def _zip_bytes(files: Mapping[str, str]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        for rel, text in files.items():
            archive.writestr(rel, text)
    return out.getvalue()


class _Git:
    """Clones by writing `files`; records every clone, so a guard can show none happened."""

    def __init__(self, files: Mapping[str, str] = PFUI) -> None:
        self.files = dict(files)
        self.clones: list[CloneSpec] = []

    def clone(self, spec: CloneSpec) -> None:
        self.clones.append(spec)
        _tree(spec.dest, self.files)
        (spec.dest / ".git").mkdir(exist_ok=True)


def _route(
    tmp_path: Path,
    *,
    applier: Applier | None = None,
    shipped: Mapping[str, str] | None = None,
    opener: object = client_packs._open,
    git: _Git | None = None,
) -> tuple[ClientAddons, Path]:
    client = tmp_path / "client"
    (client / "Interface" / "AddOns").mkdir(parents=True, exist_ok=True)
    server = tmp_path / "server"
    server.mkdir(exist_ok=True)
    if applier is None:
        applier = Applier(server, client_dir=client, git=git)  # type: ignore[arg-type]
    route = ClientAddons(
        applier=applier,
        game=GAME,
        interface=11200,
        shipped=shipped or {},
        shipped_ids=("bigger-stacks",),
        user_root=tmp_path / "user",
        today=lambda: date(2026, 10, 9),
        opener=opener,  # type: ignore[arg-type]
        stage_clone=git.clone if git is not None else None,
    )
    return route, client / "Interface" / "AddOns"


def _refused(call: object) -> str:
    with pytest.raises((AddonRefusal, ApplyRefusal)) as caught:
        call()  # type: ignore[operator]
    said = str(caught.value)
    assert said.endswith(NOTHING), said
    return said


# ------------------------------------------------------------------ deriving


def test_a_folder_gives_a_client_only_mod_named_for_its_toc(tmp_path: Path) -> None:
    route, _addons = _route(tmp_path)
    folder = _tree(tmp_path / "Downloads" / "pfUI-master", {"pfUI.toc": TOC, "pfUI.lua": "x\n"})

    prepared = route.from_folder(folder)

    manifest = prepared.manifest
    assert (manifest.id, manifest.name, manifest.type, manifest.game) == (
        "pfui",
        "pfUI",
        "mod",
        GAME,
    )
    assert [(c.src, c.dest, c.name) for c in manifest.client] == [(".", "addons", "pfUI")]
    assert manifest.origin is not None and manifest.origin.kind == "folder"
    assert manifest.origin.path == str(folder)
    assert manifest.source is None and manifest.build.rebuild is False
    assert client_only_refusal(manifest) == ""
    assert prepared.folder == folder


def test_a_zip_gives_the_same_item_as_the_folder_and_records_the_zips_bytes(
    tmp_path: Path,
) -> None:
    route, _addons = _route(tmp_path)
    zipped = _zip(tmp_path / "pfUI-master.zip", PFUI)
    folder = _tree(tmp_path / "pfUI", {"pfUI.toc": TOC})

    from_zip = route.from_zip(zipped)
    from_folder = route.from_folder(folder)

    assert from_zip.manifest.id == from_folder.manifest.id == "pfui"
    origin = from_zip.manifest.origin
    assert origin is not None and origin.kind == "archive" and origin.path == str(zipped)
    assert origin.sha256 == hashlib.sha256(zipped.read_bytes()).hexdigest()
    assert [c.src for c in from_zip.manifest.client] == ["pfUI-master"]
    from_zip.discard()


def test_a_zip_link_records_its_url_and_bytes(tmp_path: Path) -> None:
    body = _zip_bytes(PFUI)
    route, _addons = _route(tmp_path, opener=_Opener(_Response(body)))
    url = "https://github.com/shagu/pfUI/archive/refs/heads/master.zip"

    prepared = route.from_link(url)

    origin = prepared.manifest.origin
    assert origin is not None and (origin.kind, origin.url, origin.path) == ("archive", url, None)
    assert origin.sha256 == hashlib.sha256(body).hexdigest()
    prepared.discard()


def test_a_repository_link_is_cloned_once_to_be_read_and_the_copy_let_go(
    tmp_path: Path, _cache: Path
) -> None:
    git = _Git()
    route, _addons = _route(tmp_path, git=git)

    prepared = route.from_link("https://github.com/shagu/pfUI")

    manifest = prepared.manifest
    assert manifest.id == "pfui" and manifest.source is not None
    assert manifest.source.url == "https://github.com/shagu/pfUI"
    assert manifest.origin is not None and manifest.origin.kind == "link"
    assert prepared.folder is None
    assert len(git.clones) == 1 and not os.path.lexists(git.clones[0].dest.parent)


def test_a_releases_link_follows_the_newest_release() -> None:
    source = repository_source("https://github.com/shagu/pfUI/releases/latest")
    assert (source.repo, source.follow) == ("https://github.com/shagu/pfUI", "releases")
    assert repository_source("https://github.com/shagu/pfUI.git").follow == "branch"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/shagu/pfUI",
        "http://github.com/shagu/pfUI",
        "https://github.com/shagu/pfUI/tree/master/modules",
        "https://github.com/shagu",
    ],
)
def test_a_link_that_is_no_repository_on_the_three_forges_is_refused(url: str) -> None:
    said = _refused(lambda: repository_source(url))
    assert said.startswith(f"{url} is not a link Yu'lon can take an add-on from")


def test_the_item_of_several_add_ons_is_named_for_the_one_they_all_start_with(
    tmp_path: Path,
) -> None:
    route, _addons = _route(tmp_path)
    # `Bagnon_Config` at the top and `Bagnon` under `addons/`: the reader finds the
    # top one first, so only the naming rule makes the item Bagnon.
    folder = _tree(
        tmp_path / "Bagnon-10.2",
        {
            "Bagnon_Config/Bagnon_Config.toc": "## Interface: 11200\n",
            "addons/Bagnon/Bagnon.toc": "## Interface: 11200\n",
        },
    )

    manifest = route.from_folder(folder).manifest

    assert (manifest.id, manifest.name) == ("bagnon", "Bagnon")
    assert sorted(c.name or "" for c in manifest.client) == ["Bagnon", "Bagnon_Config"]


def test_item_name_falls_back_to_the_first_when_no_name_leads() -> None:
    from yulon.addon_layout import Addon

    addons = [Addon("Zeta", "Zeta", "Zeta/Zeta.toc", 11200), Addon("Alpha", "A", "A/A.toc", 11200)]
    assert item_name(addons) == "Zeta"


def test_a_shipped_add_on_name_is_refused_before_anything_is_copied(tmp_path: Path) -> None:
    route, addons = _route(tmp_path, shipped={"pfUI": "pfUI (shipped)"})
    folder = _tree(tmp_path / "pfUI", {"pfUI.toc": TOC})

    said = _refused(lambda: route.from_folder(folder))

    assert "already installs for this server (pfUI (shipped))" in said
    assert list(addons.iterdir()) == []


def test_a_program_in_the_folder_is_refused(tmp_path: Path) -> None:
    route, _addons = _route(tmp_path)
    folder = _tree(tmp_path / "pfUI", {"pfUI.toc": TOC, "helper.exe": "x"})

    said = _refused(lambda: route.from_folder(folder))

    assert said.startswith("helper.exe is a program file")


def test_an_id_this_game_ships_is_refused(tmp_path: Path) -> None:
    route, _addons = _route(tmp_path)
    folder = _tree(tmp_path / "x", {"Bigger_Stacks.toc": "## Interface: 11200\n"})

    said = _refused(lambda: route.from_folder(folder))

    assert said.startswith("Bigger_Stacks is an item Yu'lon already ships for this server")


def test_a_refused_zip_leaves_no_staging_behind(tmp_path: Path, _cache: Path) -> None:
    route, _addons = _route(tmp_path)
    zipped = _zip(tmp_path / "wotlk.zip", {"Wrath/Wrath.toc": "## Interface: 30300\n"})

    _refused(lambda: route.from_zip(zipped))

    staging = _cache / "addons" / "staging"
    assert not staging.exists() or list(staging.iterdir()) == []


# ------------------------------------------------------------------ the guard


def _carrying(**extra: object) -> Manifest:
    return parse_manifest(
        {
            "id": "pfui",
            "name": "pfUI",
            "type": "mod",
            "game": GAME,
            "origin": {"kind": "folder", "path": "/x", "added": "2026-10-09"},
            "client": [{"src": ".", "dest": "addons", "name": "pfUI"}],
            **extra,
        }
    )


CARRIES = [
    ({"sql": [{"db": "world", "statement": "DELETE FROM x;"}]}, "database changes"),
    ({"conf": [{"file": "etc/x.conf", "keys": []}]}, "settings files"),
    ({"deploy": [{"src": "a.lua", "dest": "lua_scripts/a.lua"}]}, "files for the server"),
    ({"patches": [{"file": "a.conf", "find": "a", "replace": "b"}]}, "patches"),
    ({"server_dbc": [{"src": "dbc"}]}, "server DBC files"),
    ({"build": {"rebuild": True}}, "a rebuild"),
    ({"folders": ["lua_scripts/mine"]}, "server folders"),
    ({"client": [{"src": "p.MPQ", "dest": "data"}]}, "game client files outside Interface/AddOns"),
]


@pytest.mark.parametrize(("extra", "said"), CARRIES)
def test_a_manifest_carrying_anything_but_add_ons_is_refused_by_name(
    extra: dict[str, object], said: str
) -> None:
    assert client_only_refusal(_carrying(**extra)) == (
        f"pfUI is not only a client add-on: it carries {said}, and Yu'lon's add-on route "
        "installs add-ons alone." + NOTHING
    )


def test_a_server_module_is_refused_by_its_type() -> None:
    module = parse_manifest(
        {
            "id": "mod-x",
            "name": "mod-x",
            "type": "module",
            "game": GAME,
            "origin": {"kind": "folder", "path": "/x", "added": "2026-10-09"},
            "build": {"rebuild": False},
        }
    )
    assert "it carries a module for the server" in client_only_refusal(module)


@pytest.mark.parametrize("press", ["install", "update", "remove"])
def test_every_press_refuses_such_a_manifest_before_anything_is_cloned(
    tmp_path: Path, press: str
) -> None:
    git = _Git()
    route, addons = _route(tmp_path, git=git)
    manifest = _carrying(
        source={"repo": "shagu/pfUI"},
        origin={"kind": "link", "added": "x"},
        sql=[{"db": "world", "statement": "DELETE FROM x;"}],
    )
    from yulon.client_addons import Prepared

    calls = {
        "install": lambda: route.install(Prepared(manifest=manifest)),
        "update": lambda: route.update(manifest),
        "remove": lambda: route.remove(manifest),
    }

    said = _refused(calls[press])

    assert "it carries database changes" in said
    assert git.clones == [] and list(addons.iterdir()) == []
    assert not route.applier.clone_dir(manifest).exists()


@pytest.mark.parametrize("press", ["install", "update", "remove", "configure"])
def test_the_add_on_only_applier_refuses_by_itself_too(tmp_path: Path, press: str) -> None:
    """Centurion's applier, called by anything at all: the guard is in the applier."""
    git = _Git()
    applier = AddonOnlyApplier(tmp_path / "server", client_dir=tmp_path / "client", git=git)  # type: ignore[arg-type]
    manifest = _carrying(
        source={"repo": "shagu/pfUI"},
        origin={"kind": "link", "added": "x"},
        conf=[{"file": "etc/x.conf", "keys": []}],
    )

    with pytest.raises(ApplyRefusal, match="it carries settings files"):
        getattr(applier, press)(manifest)

    assert git.clones == []


def test_the_add_on_only_applier_installs_an_add_on(tmp_path: Path) -> None:
    client = tmp_path / "client"
    (client / "Interface" / "AddOns").mkdir(parents=True)
    applier = AddonOnlyApplier(tmp_path / "server", client_dir=client)
    (tmp_path / "server").mkdir()
    route, addons = _route(tmp_path, applier=applier)

    route.install(route.from_folder(_tree(tmp_path / "pfUI", {"pfUI.toc": TOC})))

    assert (addons / "pfUI" / "pfUI.toc").read_text() == TOC


# ------------------------------------------------------------------ install, update, remove


def test_install_copies_records_and_says_the_readers_notes(tmp_path: Path, _cache: Path) -> None:
    route, addons = _route(tmp_path)
    zipped = _zip(
        tmp_path / "Old.zip",
        {"Old/Old.toc": "## Interface: 11100\n## Dependencies: Ace2\n", "Old/a.lua": "x\n"},
    )

    report = route.install(route.from_zip(zipped))

    assert (addons / "Old" / "a.lua").read_text() == "x\n"
    assert any("made for an older patch (Interface 11100)" in line for line in report.done)
    assert any(line.startswith("Old needs Ace2") for line in report.done), report.done
    assert [m.id for m in route.installed()] == ["old"]
    assert list((_cache / "addons" / "staging").iterdir()) == [], "the staging stayed"
    copies = read_client_copies(route.applier.clone_dir(route.installed()[0]), item_id="old")
    assert {Path(c.path).name for c in copies} == {"Old.toc", "a.lua"}


def test_remove_takes_the_files_back_and_then_the_record(tmp_path: Path) -> None:
    route, addons = _route(tmp_path)
    route.install(route.from_folder(_tree(tmp_path / "pfUI", {"pfUI.toc": TOC})))
    (manifest,) = route.installed()

    report = route.remove(manifest)

    assert not (addons / "pfUI").exists()
    assert any("took back 1 file of the pfUI add-on" in line for line in report.done)
    assert route.installed() == []


def test_a_refused_remove_keeps_the_record(tmp_path: Path) -> None:
    route, _addons = _route(tmp_path)
    route.install(route.from_folder(_tree(tmp_path / "pfUI", {"pfUI.toc": TOC})))
    (manifest,) = route.installed()

    def broken(*_a: object, **_k: object) -> object:
        raise ApplyRefusal("the world is running. Nothing was changed.")

    route.applier.remove = broken  # type: ignore[method-assign]
    with pytest.raises(ApplyRefusal):
        route.remove(manifest)

    assert [m.id for m in route.installed()] == ["pfui"]


def test_a_first_install_refused_by_its_completion_leaves_no_record_and_no_folder(
    tmp_path: Path,
) -> None:
    """The folder changed between the read and the copy: it now holds another add-on."""
    route, addons = _route(tmp_path)
    folder = _tree(tmp_path / "pfUI", {"pfUI.toc": TOC})
    prepared = route.from_folder(folder)
    (folder / "pfUI.toc").rename(folder / "Other.toc")

    with pytest.raises(ApplyRefusal, match="now holds the add-on Other instead"):
        route.install(prepared)

    assert route.installed() == []
    assert not route.applier.clone_dir(prepared.manifest).exists()
    assert list(addons.iterdir()) == []


def test_the_completer_leaves_any_other_manifest_as_it_is(tmp_path: Path) -> None:
    route, _addons = _route(tmp_path)
    shipped = _carrying(origin=None)
    module = _carrying(conf=[{"file": "etc/x.conf", "keys": []}])

    assert route.completer(shipped, tmp_path) is shipped
    assert route.completer(module, tmp_path) is module


def test_a_zip_link_whose_bytes_did_not_change_is_up_to_date(tmp_path: Path) -> None:
    body = _zip_bytes(PFUI)
    opener = _Opener(_Response(body))
    route, addons = _route(tmp_path, opener=opener)
    url = "https://github.com/shagu/pfUI/archive/refs/heads/master.zip"
    route.install(route.from_link(url))
    (manifest,) = route.installed()
    opener.response = _Response(body)

    report = route.update(manifest)

    assert report.done == ("pfUI is already up to date: the zip is the one installed",)
    assert len(opener.calls) == 2


def test_a_zip_link_with_new_bytes_is_installed_over_the_old_copy(tmp_path: Path) -> None:
    opener = _Opener(_Response(_zip_bytes(PFUI)))
    route, addons = _route(tmp_path, opener=opener)
    url = "https://github.com/shagu/pfUI/archive/refs/heads/master.zip"
    route.install(route.from_link(url))
    (manifest,) = route.installed()
    opener.response = _Response(_zip_bytes({**PFUI, "pfUI-master/new.lua": "-- new\n"}))

    route.update(manifest)

    assert (addons / "pfUI" / "new.lua").read_text() == "-- new\n"
    (updated,) = route.installed()
    assert updated.origin is not None and manifest.origin is not None
    assert updated.origin.sha256 != manifest.origin.sha256


def test_a_folder_add_on_is_updated_from_the_same_folder(tmp_path: Path) -> None:
    route, addons = _route(tmp_path)
    folder = _tree(tmp_path / "pfUI", {"pfUI.toc": TOC})
    route.install(route.from_folder(folder))
    (manifest,) = route.installed()
    (folder / "new.lua").write_text("-- new\n", encoding="utf-8")

    route.update(manifest)

    assert (addons / "pfUI" / "new.lua").read_text() == "-- new\n"


def test_an_update_that_now_holds_another_add_on_is_refused(tmp_path: Path) -> None:
    route, addons = _route(tmp_path)
    folder = _tree(tmp_path / "pfUI", {"pfUI.toc": TOC})
    route.install(route.from_folder(folder))
    (manifest,) = route.installed()
    (folder / "pfUI.toc").rename(folder / "Other.toc")

    said = _refused(lambda: route.update(manifest))

    assert "now holds the add-on Other, not pfUI" in said
    assert (addons / "pfUI" / "pfUI.toc").is_file()


def test_a_repository_add_on_is_updated_by_the_appliers_own_update(tmp_path: Path) -> None:
    updated: list[str] = []

    class _Spy(Applier):
        def update(self, manifest: Manifest, *a: object, **k: object) -> object:  # type: ignore[override]
            updated.append(manifest.id)
            return "the applier's report"

    git = _Git()
    client = tmp_path / "client"
    route, _addons = _route(tmp_path, applier=_Spy(tmp_path / "server", client_dir=client), git=git)
    manifest = route.from_link("https://github.com/shagu/pfUI").manifest

    assert route.update(manifest) == "the applier's report"
    assert updated == ["pfui"]


@pytest.mark.skipif(os.name == "nt", reason="a symlink needs privileges on Windows")
def test_a_first_install_refused_after_its_record_was_written_drops_that_record(
    tmp_path: Path,
) -> None:
    """The completion recorded it; a link in the client refused the copy after. No record stays."""
    route, addons = _route(tmp_path)
    elsewhere = tmp_path / "elsewhere.toc"
    elsewhere.write_text("not yours\n", encoding="utf-8")
    (addons / "pfUI").mkdir()
    (addons / "pfUI" / "pfUI.toc").symlink_to(elsewhere)
    prepared = route.from_folder(_tree(tmp_path / "pfUI", {"pfUI.toc": TOC}))

    with pytest.raises(ApplyRefusal, match="through a link"):
        route.install(prepared)

    assert route.installed() == []
    assert not route.applier.clone_dir(prepared.manifest).exists()
    assert elsewhere.read_text() == "not yours\n"
