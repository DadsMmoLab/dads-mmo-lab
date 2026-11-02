"""T855-B: Conquest of Azeroth keeps every WotLK item and adds its own.

`wow-coa` carries `manifests_from: wow-wotlk`, so its Modules tab offers every
WotLK item in every index — plus CoA's own `yulon-consoleport` mod from
`manifests/wow-coa/`. An own item with a borrowed id would replace it; none
collides today. WotLK and Unbound never offer `yulon-consoleport` (T853's client
pack is `consoleport`, not this module).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import coa_client, resources, steam_input
from yulon.catalog.catalog import load_catalog
from yulon.manifest import ManifestType
from yulon.manifest_store import ManifestStore
from yulon.ui.controller_view import ControllerServices

COA = "wow-coa"
WOTLK = "wow-wotlk"

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

KINDS: tuple[ManifestType, ...] = ("module", "mod", "ale", "keg")


def _services(tmp_path: Path) -> ControllerServices:
    entry = load_catalog().get(COA)
    server_dir = tmp_path / "coa"
    server_dir.mkdir()
    password_file = entry.install.password.file
    if password_file:
        (server_dir / password_file).write_text("hunter2", encoding="utf-8")
    return ControllerServices.for_entry(entry, server_dir)


def _borrowed(kind: ManifestType) -> tuple[str, ...]:
    return ManifestStore(resources.manifests_dir(), WOTLK).load_index(kind).items


def test_coa_borrows_wotlks_tree_and_layers_its_own() -> None:
    entry = load_catalog().get(COA)
    assert entry.has_manifests is True
    assert entry.manifests_from == WOTLK
    assert entry.manifest_game() == WOTLK
    assert (resources.manifests_dir() / COA).is_dir()


def test_coa_offers_every_wotlk_item_plus_consoleport(tmp_path: Path) -> None:
    services = _services(tmp_path)
    assert services.store is not None and services.store.game == WOTLK
    for kind in KINDS:
        assert set(services.store.load_index(kind).items) >= set(_borrowed(kind)), kind
    mods = {m.id: m for m in services.store.load_all("mod")}
    assert set(_borrowed("mod")) | {"yulon-consoleport"} <= set(mods)
    for kind in ("module", "ale", "keg"):
        offered = {m.id for m in services.store.load_all(kind)}
        assert set(_borrowed(kind)) <= offered, kind


def test_consoleport_is_coas_own_item(tmp_path: Path) -> None:
    services = _services(tmp_path)
    assert services.store is not None
    manifest = services.store.load("mod", "yulon-consoleport")
    assert manifest.game == COA and manifest.type == "mod"
    assert [(step.src, step.dest, step.name) for step in manifest.client] == [
        (folder, "addons", folder) for folder in FOLDERS
    ]
    assert manifest.build.rebuild is False and manifest.build.restart is False
    assert manifest.source is not None and manifest.source.follow == "releases"


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-unbound", "wow-tortoise", "wow-tbc"])
def test_no_other_game_lists_the_coa_item(game: str, tmp_path: Path) -> None:
    entry = load_catalog().get(game)
    if not entry.has_manifests:
        pytest.skip(f"{game} has no manifests")
    services = ControllerServices.for_entry(entry, tmp_path / game)
    assert services.store is not None
    assert "yulon-consoleport" not in {m.id for m in services.store.load_all("mod")}


def test_default_addons_never_puts_consoleport_in(tmp_path: Path) -> None:
    """T1: assert on what `put_in()` installs, not on display names. The CoA
    services' `default_addons` holds no `yulon-consoleport`, so a Play or a
    fresh install leaves the client alone until the Modules tab's own Install."""
    from yulon import default_addons

    services = _services(tmp_path)
    assert services.store is not None
    assert services.default_addons == ()

    manifests = list(services.store.load_all("mod"))
    assert "yulon-consoleport" in {m.id for m in manifests}
    out = default_addons.put_in(
        tmp_path / "coa",
        services.applier,  # type: ignore[arg-type]
        manifests,
        services.default_addons,
        clone=True,
    )
    assert out.installed == () and "yulon-consoleport" not in out.installed


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
    """C1: completed claim -> layout; unfinished, empty, malformed record,
    and a foreign clone -> no layout. An empty dir used to enable it."""
    from yulon import apply as _apply
    from yulon.ui import controller_view

    monkeypatch.setattr(controller_view.platform, "detect", lambda: "linux")
    entry = load_catalog().get(COA)
    server = tmp_path / "srv"
    server.mkdir()

    seam = controller_view._steam_seam(entry, server, tmp_path / "client")
    assert seam is not None and seam.controller_layout is not None
    assert seam.controller_layout() is False, "no clone: no layout"

    clone = server / "sql_scripts" / "clones" / "yulon-consoleport"
    clone.mkdir(parents=True)
    assert seam.controller_layout() is False, "an empty clone dir writes no layout"
    assert steam_input.consoleport_module_installed(server) is False

    _apply.write_clone_claim(clone, item_id="yulon-consoleport", url="", completed=False)
    assert steam_input.consoleport_module_installed(server) is False, "unfinished: no layout"
    assert seam.controller_layout() is False

    (clone / _apply.CLAIM_FILE).write_text("{not json", encoding="utf-8")
    assert steam_input.consoleport_module_installed(server) is False, "malformed: no layout"

    _apply.write_clone_claim(clone, item_id="something-else", url="", completed=True)
    assert steam_input.consoleport_module_installed(server) is False, "foreign clone: no layout"

    _apply.write_clone_claim(clone, item_id="yulon-consoleport", url="", completed=True)
    assert steam_input.consoleport_module_installed(server) is True
    assert seam.controller_layout() is True, "installed mod writes the layout"

    import shutil

    shutil.rmtree(server / "sql_scripts")
    assert seam.controller_layout() is False, "a Remove takes the layout back"


def test_a_players_own_consoleport_coexists_with_the_shipped_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M1: a CoA player who added ConsolePortLK through the add-on box already
    owns the id `consoleport`. The shipped item is `yulon-consoleport`, so both
    are listed separately, and removing the shipped one leaves the player's
    own record and clone untouched."""
    from yulon import apply as _apply
    from yulon import module_source
    from yulon.controller_wow_wotlk import modules as wotlk_modules
    from yulon.manifest import parse_manifest

    monkeypatch.setattr(wotlk_modules, "user_manifests_dir", lambda *a, **k: tmp_path / "user")
    services = _services(tmp_path)
    assert services.store is not None
    user_root = tmp_path / "user"

    mine = parse_manifest(
        {
            "id": "consoleport",
            "name": "ConsolePortLK",
            "type": "mod",
            "game": COA,
            "origin": {"kind": "folder", "path": "/x", "added": "2026-11-02"},
            "client": [{"src": ".", "dest": "addons", "name": "ConsolePort"}],
        }
    )
    module_source.persist(user_root, mine, shipped_ids=("yulon-consoleport",))

    from yulon.manifest_store import ManifestStore

    store = ManifestStore(
        services.store.root,
        WOTLK,
        user_root,
        user_game=COA,
        own_game=COA,
    )
    assert {"consoleport", "yulon-consoleport"} <= {m.id for m in store.load_all("mod")}

    server = tmp_path / "coa"
    mine_clone = server / "sql_scripts" / "clones" / "consoleport"
    mine_clone.mkdir(parents=True)
    _apply.write_clone_claim(mine_clone, item_id="consoleport", url="", completed=True)
    mine_bytes = (user_root / COA / "mods" / "consoleport.json").read_bytes()

    assert services.module_forget is not None
    shipped = store.load("mod", "yulon-consoleport")
    assert services.module_forget(shipped) is False

    assert (user_root / COA / "mods" / "consoleport.json").read_bytes() == mine_bytes
    assert mine_clone.is_dir()
