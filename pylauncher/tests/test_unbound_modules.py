"""T554 Y4: on WoW Unbound a folder the server install cloned is never a removable module.

Unbound builds `modules/mod-ale` as a part of the server (ALE is how its Lua runs). The
Modules tab shares WotLK's manifests, and WotLK's `mod-ale` manifest clones into the same
folder, so without this the tab offered ALE as a module of Unbound and **Remove** deleted
the folder the compile needs. The filter is by the folder an entry's emulator sources
clone into, which is data, not by game id: WotLK is unchanged.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from yulon import apply as apply_module
from yulon import resources
from yulon.catalog import native
from yulon.catalog.catalog import CatalogEntry, load_catalog
from yulon.manifest import Manifest
from yulon.manifest_store import ManifestStore
from yulon.ui.controller_view import ControllerServices, ControllerView


def entry(game: str) -> CatalogEntry:
    return load_catalog().get(game)


def module_manifest(item: str) -> Manifest:
    store = ManifestStore(resources.manifests_dir(), "wow-wotlk")
    return next(m for m in store.load_all("module") if m.id == item)


def test_the_folders_an_entrys_sources_clone_into_exclude_the_core_itself() -> None:
    assert native.server_source_folders(entry("wow-unbound")) == frozenset(
        PurePosixPath(p)
        for p in ("modules/mod-playerbots", "modules/mod-ale", "modules/mod-unbound")
    )
    assert native.server_source_folders(entry("wow-wotlk")) == frozenset(
        {PurePosixPath("modules/mod-playerbots")}
    )


def test_a_manifest_is_a_server_source_when_its_clone_folder_is_one() -> None:
    folders = native.server_source_folders(entry("wow-unbound"))
    assert apply_module.is_server_source(module_manifest("mod-ale"), folders)
    assert not apply_module.is_server_source(module_manifest("mod-junk-to-gold"), folders)
    wotlk = native.server_source_folders(entry("wow-wotlk"))
    assert not apply_module.is_server_source(module_manifest("mod-ale"), wotlk)


def test_remove_install_and_update_refuse_a_server_source_and_leave_the_folder(
    tmp_path: Path,
) -> None:
    ale = tmp_path / "modules" / "mod-ale"
    ale.mkdir(parents=True)
    (ale / "keep.txt").write_text("the compile needs me", encoding="utf-8")
    applier = apply_module.Applier(tmp_path)
    applier.server_sources = native.server_source_folders(entry("wow-unbound"))
    applier.server_name = "WoW Unbound"
    manifest = module_manifest("mod-ale")
    for press in (applier.remove, applier.install, applier.update):
        with pytest.raises(apply_module.ApplyRefusal, match="part of the WoW Unbound server"):
            press(manifest)
    assert (ale / "keep.txt").read_text(encoding="utf-8") == "the compile needs me"


def test_the_factory_hands_every_applier_its_entrys_server_sources(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(entry("wow-unbound"), tmp_path)
    assert services.applier is not None
    assert services.applier.server_sources == native.server_source_folders(entry("wow-unbound"))
    assert services.applier.server_name == "WoW Unbound"


def test_wotlks_applier_still_installs_and_removes_mod_ale(tmp_path: Path) -> None:
    services = ControllerServices.for_entry(entry("wow-wotlk"), tmp_path)
    assert services.applier is not None
    assert not apply_module.is_server_source(
        module_manifest("mod-ale"), services.applier.server_sources
    )


def _offered(view: ControllerView) -> set[str]:
    return {row.data.id for row in view.modules_panel.rows() if row.data.catalogued}


def test_the_modules_tab_does_not_offer_ale_on_unbound_but_shows_its_folder(
    qapp: object, tmp_path: Path
) -> None:
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    unbound = entry("wow-unbound")
    view = ControllerView(
        unbound, ControllerServices.for_entry(unbound, tmp_path), status_poll_ms=0
    )
    view.reload_modules()
    assert "mod-ale" not in _offered(view)
    shown = {row.data.id: row.data for row in view.modules_panel.rows()}
    assert "mod-ale" in shown and not shown["mod-ale"].catalogued
    assert "mod-junk-to-gold" in _offered(view)


def test_an_ale_script_that_requires_mod_ale_is_still_installable_on_unbound(
    qapp: object, tmp_path: Path
) -> None:
    (tmp_path / "modules" / "mod-ale").mkdir(parents=True)
    ale_script = next(
        m
        for m in ManifestStore(resources.manifests_dir(), "wow-wotlk").load_all("ale")
        if "mod-ale" in m.requires
    )
    installed = apply_module.installed_clones(tmp_path)
    assert apply_module.missing_requirements(ale_script, installed) == ()


def test_wotlks_modules_tab_still_offers_ale(qapp: object, tmp_path: Path) -> None:
    wotlk = entry("wow-wotlk")
    view = ControllerView(wotlk, ControllerServices.for_entry(wotlk, tmp_path), status_poll_ms=0)
    view.reload_modules()
    assert "mod-ale" in _offered(view)
