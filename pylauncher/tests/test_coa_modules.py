"""T855: Conquest of Azeroth reads its own manifest tree, not WotLK's.

`wow-coa` used to carry `manifests_from: wow-wotlk`, so its Modules tab
offered WotLK's twenty-one C++ modules and eleven SQL mods — none of which the
CoA server builds. It now reads `manifests/wow-coa/`, which holds exactly the
opt-in ConsolePort controller add-on; WotLK, Unbound and Centurion keep theirs
(T853's client pack, not this module).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import coa_client, resources, steam_input
from yulon.catalog.catalog import load_catalog
from yulon.manifest_store import ManifestStore
from yulon.ui.controller_view import ControllerServices

COA = "wow-coa"

FOLDERS = (
    "ConsolePort",
    "ConsolePortAdvanced",
    "ConsolePortBar",
    "ConsolePortHelp",
    "ConsolePortKeyboard",
    "ConsolePortLoader",
    "ConsolePortUI_Loot",
    "ConsolePortUI_Menu",
)


def _services(tmp_path: Path) -> ControllerServices:
    entry = load_catalog().get(COA)
    server_dir = tmp_path / "coa"
    server_dir.mkdir()
    password_file = entry.install.password.file
    if password_file:
        (server_dir / password_file).write_text("hunter2", encoding="utf-8")
    return ControllerServices.for_entry(entry, server_dir)


def test_coa_reads_its_own_tree() -> None:
    entry = load_catalog().get(COA)
    assert entry.has_manifests is True
    assert entry.manifest_game() == COA
    assert (resources.manifests_dir() / COA).is_dir()


def test_only_consoleport_is_offered_on_coa(tmp_path: Path) -> None:
    services = _services(tmp_path)
    assert services.store is not None and services.store.game == COA
    mods = {m.id: m for m in services.store.load_all("mod")}
    assert set(mods) == {"consoleport"}
    manifest = mods["consoleport"]
    assert manifest.game == COA and manifest.type == "mod"
    assert [(step.src, step.dest, step.name) for step in manifest.client] == [
        (folder, "addons", folder) for folder in FOLDERS
    ]
    assert manifest.build.rebuild is False and manifest.build.restart is False
    assert manifest.source is not None and manifest.source.follow == "releases"


def test_coa_shows_no_wotlk_module(tmp_path: Path) -> None:
    coa = {m.id for m in _services(tmp_path).store.load_all("module")}  # type: ignore[union-attr]
    assert coa == set()
    wotlk_ids = set(
        ManifestStore(resources.manifests_dir(), "wow-wotlk").load_index("module").items
    )
    assert len(wotlk_ids) > 20, "the tree this test contrasts against shrank away"


@pytest.mark.parametrize(
    "game", ["wow-wotlk", "wow-unbound", "wow-tortoise", "wow-centurion", "wow-tbc"]
)
def test_no_other_game_lists_the_coa_item(game: str, tmp_path: Path) -> None:
    entry = load_catalog().get(game)
    if not entry.has_manifests:
        pytest.skip(f"{game} has no manifests")
    services = ControllerServices.for_entry(entry, tmp_path / game)
    assert services.store is not None
    assert "consoleport" not in {m.id for m in services.store.load_all("mod")}


def test_default_addons_never_puts_consoleport_in(tmp_path: Path) -> None:
    from yulon import default_addons

    assert "consoleport" not in default_addons._NAMES


@pytest.mark.parametrize(
    "folder",
    [
        "ConsolePort",
        "ConsolePortAdvanced",
        "ConsolePortBar",
        "ConsolePortHelp",
        "ConsolePortKeyboard",
        "ConsolePortLoader",
        "ConsolePortUI_Loot",
        "ConsolePortUI_Menu",
    ],
)
def test_update_client_leaves_every_consoleport_tree_alone(folder: str) -> None:
    assert coa_client.classify(f"Interface/AddOns/{folder}/{folder}.toc") == "excluded"
    assert coa_client.classify(f"Interface/AddOns/{folder}/nested/file.lua") == "excluded"


def test_consoleport_chosen_for_coa_follows_the_install_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon.ui import controller_view

    monkeypatch.setattr(controller_view.platform, "detect", lambda: "linux")
    entry = load_catalog().get(COA)
    server = tmp_path / "srv"
    server.mkdir()

    seam = controller_view._steam_seam(entry, server, tmp_path / "client")
    assert seam is not None and seam.controller_layout is not None
    assert seam.controller_layout() is False, "no clone: no layout"

    (server / "sql_scripts" / "clones" / "consoleport").mkdir(parents=True)
    assert seam.controller_layout() is True, "installed module writes the layout"
    assert steam_input.consoleport_module_installed(server) is True

    import shutil

    shutil.rmtree(server / "sql_scripts")
    assert seam.controller_layout() is False, "a Remove takes the layout back"
