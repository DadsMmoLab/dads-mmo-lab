"""T613 PR-2: the add-on route is wired once, in `ControllerServices.for_entry()`, for every game.

And the user layer the route records into is read by every game's store: TBC,
Vanilla and Centurion as WotLK and Tortoise already did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import module_source
from yulon.catalog.catalog import load_catalog
from yulon.client_addons import AddonOnlyApplier, ClientAddons
from yulon.controller_wow_centurion import modules as centurion_modules
from yulon.controller_wow_tbc import modules as tbc_modules
from yulon.controller_wow_vanilla import modules as vanilla_modules
from yulon.controller_wow_wotlk.modules import user_manifests_dir
from yulon.manifest import Manifest, parse_manifest
from yulon.ui.controller_view import ControllerServices

CATALOG = load_catalog()
GAMES = [entry.id for entry in CATALOG.games if entry.client.addon_interface is not None]


def test_every_game_yulon_runs_takes_add_ons() -> None:
    """Not vacuous: the six games of the plan, each with its client's number."""
    assert {game: CATALOG.get(game).client.addon_interface for game in GAMES} == {
        "wow-wotlk": 30300,
        "wow-unbound": 30300,
        "wow-centurion": 30300,
        "wow-tbc": 20400,
        "wow-vanilla": 11200,
        "wow-tortoise": 11200,
    }


@pytest.mark.parametrize("game", GAMES)
def test_for_entry_gives_every_game_the_route_over_its_own_applier(
    game: str, tmp_path: Path
) -> None:
    (tmp_path / "c" / "Interface").mkdir(parents=True)
    services = ControllerServices.for_entry(CATALOG.get(game), tmp_path / game, tmp_path / "c")

    route = services.client_addons
    assert isinstance(route, ClientAddons)
    assert (route.game, route.interface) == (game, CATALOG.get(game).client.addon_interface)
    if services.applier is not None:
        assert route.applier is services.applier, "a second applier beside the tab's"
    else:
        assert isinstance(route.applier, AddonOnlyApplier)
    assert route.applier.client_dir == tmp_path / "c"
    assert route.applier.server_dir == tmp_path / game


def test_centurion_writes_no_add_on_into_a_folder_with_no_interface_folder(
    tmp_path: Path,
) -> None:
    """T30's rule, as Tortoise has it: a folder nobody has shown to be a game client."""
    (tmp_path / "c").mkdir()
    services = ControllerServices.for_entry(
        CATALOG.get("wow-centurion"), tmp_path / "s", tmp_path / "c"
    )

    assert services.client_addons is not None
    assert services.client_addons.applier.client_dir is None


def test_centurion_has_no_module_applier_but_an_add_on_only_one(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(CATALOG.get("wow-centurion"), tmp_path / "s")

    assert services.applier is None
    assert services.client_addons is not None
    assert type(services.client_addons.applier) is AddonOnlyApplier


def test_an_entry_whose_client_takes_no_add_ons_gets_no_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    entry = CATALOG.get("wow-tbc")
    entry = entry.model_copy(
        update={"client": entry.client.model_copy(update={"addon_interface": None})}
    )

    services = ControllerServices.for_entry(entry, tmp_path / "s")

    assert services.client_addons is None


@pytest.mark.parametrize("game", ["wow-tbc", "wow-centurion"])
def test_with_a_ready_to_play_client_the_route_writes_there_and_knows_the_original(
    game: str, tmp_path: Path
) -> None:
    """The client Play uses (owner, Q3), for the factory's applier and Centurion's alike."""
    original = tmp_path / "WoW"
    original.mkdir()
    ready = tmp_path / "WoW (Yu'lon)"
    (ready / "Interface").mkdir(parents=True)

    services = ControllerServices.for_entry(
        CATALOG.get(game), tmp_path / "s", original, play_client_dir=ready
    )

    assert services.client_addons is not None
    applier = services.client_addons.applier
    assert applier.client_dir == ready
    assert original in applier.client_origins
    assert applier.client_game == game


def test_the_route_knows_the_add_ons_the_game_ships_by_folder_name(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(CATALOG.get("wow-tortoise"), tmp_path / "s")

    assert services.client_addons is not None
    shipped = {name.casefold() for name in services.client_addons.shipped}
    assert {"tortoisebotsmanager", "tortoisegmmanager"} <= shipped
    assert "tortoise-bots-manager" in services.client_addons.shipped_ids


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-centurion"])
def test_a_game_with_no_record_seam_gets_the_routes_forget(game: str, tmp_path: Path) -> None:
    """So a Remove from the list drops an outside add-on's record, as on WotLK and Tortoise."""
    services = ControllerServices.for_entry(CATALOG.get(game), tmp_path / "s")

    assert services.client_addons is not None
    assert services.module_forget == services.client_addons.forget


def test_uninstall_on_centurion_takes_outside_add_on_files_back(tmp_path: Path) -> None:
    from yulon import purge

    services = ControllerServices.for_entry(CATALOG.get("wow-centurion"), tmp_path / "s")

    assert isinstance(services.uninstall, purge.Uninstaller)
    assert services.client_addons is not None
    assert (
        services.uninstall.take_back_client_files
        == services.client_addons.applier.take_back_everything
    )


def test_binding_the_other_servers_reaches_every_applier(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(CATALOG.get("wow-centurion"), tmp_path / "s")
    others = lambda: (tmp_path / "other",)  # noqa: E731

    services.bind_other_server_dirs(others)

    assert services.other_server_dirs is others
    assert services.client_addons is not None
    assert services.client_addons.applier.other_server_dirs is others


def test_main_binds_the_other_servers_through_the_one_method() -> None:
    """A relationship, read off the code: `main.py` does not set the attribute by hand."""
    text = (Path(__file__).parents[1] / "main.py").read_text(encoding="utf-8")
    assert "services.bind_other_server_dirs(_other_server_dirs(game, server_dir))" in text
    assert "services.other_server_dirs = " not in text


# ------------------------------------------------------------------ the user layer


def _record(game: str) -> Manifest:
    manifest = parse_manifest(
        {
            "id": "pfui",
            "name": "pfUI",
            "type": "mod",
            "game": game,
            "origin": {"kind": "folder", "path": "/x", "added": "2026-10-09"},
            "client": [{"src": ".", "dest": "addons", "name": "pfUI"}],
        }
    )
    module_source.persist(user_manifests_dir(), manifest, shipped_ids=())
    return manifest


@pytest.mark.parametrize(
    ("game", "store"),
    [("wow-tbc", tbc_modules.store), ("wow-vanilla", vanilla_modules.store)],
)
def test_tbc_and_vanilla_list_an_outside_add_on_beside_their_shipped_mods(
    game: str, store: object
) -> None:
    _record(game)

    listed = [m.id for m in store().load_all("mod")]  # type: ignore[operator]

    assert "pfui" in listed
    assert len(listed) > 1, "the shipped mods are still listed"


def test_centurion_lists_an_outside_add_on_with_no_shipped_tree() -> None:
    _record("wow-centurion")

    store = centurion_modules.store()

    assert [m.id for m in store.load_all("mod")] == ["pfui"]
    assert list(store.load_all("module")) == []


def test_a_record_of_another_game_is_not_listed() -> None:
    _record("wow-vanilla")

    assert list(centurion_modules.store().load_all("mod")) == []
    assert "pfui" not in [m.id for m in tbc_modules.store().load_all("mod")]


@pytest.mark.parametrize("game", ["wow-wotlk", "wow-tbc", "wow-centurion"])
def test_the_routes_completion_is_the_appliers_hook_where_it_had_none(
    game: str, tmp_path: Path
) -> None:
    services = ControllerServices.for_entry(CATALOG.get(game), tmp_path / "s")

    assert services.client_addons is not None
    assert services.client_addons.applier.recomplete == services.client_addons.completer


def test_tortoise_keeps_its_own_hook(tmp_path: Path) -> None:
    from yulon.controller_wow_tortoise.autoupdate import GuardedApplier

    services = ControllerServices.for_entry(CATALOG.get("wow-tortoise"), tmp_path / "s")

    assert isinstance(services.applier, GuardedApplier)
    assert services.applier.recomplete is not None
    assert services.client_addons is not None
    assert services.applier.recomplete != services.client_addons.completer
