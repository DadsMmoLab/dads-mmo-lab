"""T613 PR-3: the add-ons a client pack puts in the client are shipped, so the box refuses them."""

from __future__ import annotations

from pathlib import Path

import pytest

from yulon import client_packs
from yulon.addon_archive import AddonRefusal
from yulon.catalog.catalog import load_catalog
from yulon.ui.controller_view import ControllerServices

CATALOG = load_catalog()
PACK = "modules/mod-unbound/client/Interface/AddOns"


def _unbound(tmp_path: Path, *, folders: tuple[str, ...] = ("UnboundTalents", "UnboundBar")):
    client = tmp_path / "client"
    (client / "Interface").mkdir(parents=True)
    server = tmp_path / "server"
    for name in folders:
        (server / PACK / name).mkdir(parents=True)
        (server / PACK / name / f"{name}.toc").write_text("## Interface: 30300\n")
    return ControllerServices.for_entry(CATALOG.get("wow-unbound"), server, client)


def _folder(tmp_path: Path, name: str, interface: int = 30300) -> Path:
    root = tmp_path / "dl" / name
    root.mkdir(parents=True)
    (root / f"{name}.toc").write_text(f"## Interface: {interface}\n## Title: {name}\n{name}.lua\n")
    (root / f"{name}.lua").write_text("x = 1\n")
    return root


def test_an_add_on_the_unbound_pack_installs_is_refused_by_name(tmp_path: Path) -> None:
    services = _unbound(tmp_path)
    route = services.client_addons
    assert route is not None
    with pytest.raises(AddonRefusal) as caught:
        route.from_folder(_folder(tmp_path, "UnboundBar"))
    assert "UnboundBar" in str(caught.value) and "Unbound addons" in str(caught.value)


def test_the_name_is_matched_in_any_case(tmp_path: Path) -> None:
    route = _unbound(tmp_path).client_addons
    assert route is not None
    with pytest.raises(AddonRefusal):
        route.from_folder(_folder(tmp_path, "unboundtalents"))


def test_an_add_on_the_pack_record_lists_in_the_client_is_refused_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _unbound(tmp_path, folders=()).client_addons
    assert route is not None
    monkeypatch.setattr(
        client_packs,
        "pack_files",
        lambda _dir: frozenset({Path("Interface/AddOns/Spellbook/a.lua")}),
    )
    with pytest.raises(AddonRefusal):
        route.from_folder(_folder(tmp_path, "Spellbook"))


def test_an_unrelated_add_on_still_installs_its_read(tmp_path: Path) -> None:
    route = _unbound(tmp_path).client_addons
    assert route is not None
    assert route.from_folder(_folder(tmp_path, "pfUI")).manifest.id == "pfui"


def test_a_game_with_no_pack_add_ons_is_as_it_was(tmp_path: Path) -> None:
    client = tmp_path / "client"
    (client / "Interface").mkdir(parents=True)
    route = ControllerServices.for_entry(
        CATALOG.get("wow-wotlk"), tmp_path / "s", client
    ).client_addons
    assert route is not None
    assert route.from_folder(_folder(tmp_path, "UnboundBar")).manifest.id == "unboundbar"


def test_the_default_add_ons_of_tortoise_are_refused_through_the_box(tmp_path: Path) -> None:
    client = tmp_path / "client"
    (client / "Interface").mkdir(parents=True)
    route = ControllerServices.for_entry(
        CATALOG.get("wow-tortoise"), tmp_path / "s", client
    ).client_addons
    assert route is not None
    with pytest.raises(AddonRefusal):
        route.from_folder(_folder(tmp_path, "TortoiseBotsManager", 11200))
