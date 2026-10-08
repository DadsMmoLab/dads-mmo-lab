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


def test_the_real_tab_wiring_with_a_ready_to_play_client_also_knows_its_server_sources(
    tmp_path: Path,
) -> None:
    """T554 rework item 3. `main.py` builds every tab with `play_client_dir`, so the
    tabs players see come out of `for_entry`'s SECOND branch; the test above only
    drove the first. Both must hand the applier Unbound's server sources."""
    play = tmp_path / "play"
    play.mkdir()
    services = ControllerServices.for_entry(
        entry("wow-unbound"),
        tmp_path / "server",
        client_dir=tmp_path / "own-client",
        play_client_dir=play,
    )
    assert services.play_client_dir == play
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


# -- T554 rework item 2: user-added manifests are per server, shipped ones shared --


def _hand_made_folder(where: Path, setting: str) -> Path:
    source = where / "mod-hand-made"
    (source / "src").mkdir(parents=True)
    (source / "conf").mkdir()
    (source / "conf" / "mod_hand_made.conf.dist").write_text(
        f"[worldserver]\nHandMade.Enable = {setting}\n", encoding="utf-8"
    )
    return source


def _services_for(game: str, server_dir: Path) -> ControllerServices:
    server_dir.mkdir()
    password_file = entry(game).install.password.file
    if password_file:
        (server_dir / password_file).write_text("hunter2", encoding="utf-8")
    return ControllerServices.for_entry(entry(game), server_dir)


def _added(services: ControllerServices, source: Path) -> Manifest:
    assert services.module_from_folder is not None
    assert services.module_install_custom is not None
    manifest = services.module_from_folder(source)
    services.module_install_custom(manifest, source)
    return manifest


def _listed(services: ControllerServices) -> dict[str, Manifest]:
    assert services.store is not None
    return {m.id: m for m in services.store.load_all("module")}


def test_a_module_added_on_unbound_is_its_own_and_never_touches_wotlks_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Lead decision 2026-10-08: `manifests_from` shares WotLK's SHIPPED manifests
    only. A module the user added from a link or a folder is a record of one
    server, kept under `<config>/manifests/user/<that server's game id>/`.

    Before: Unbound read and wrote `user/wow-wotlk/`, so adding the same module
    on Unbound overwrote WotLK's saved manifest and a Remove on Unbound deleted
    it, leaving the module still installed on WotLK as an uncatalogued folder
    with no Remove."""
    from yulon import docker
    from yulon.controller_wow_wotlk import modules

    monkeypatch.setattr(docker, "world_running", lambda *a, **k: None)
    wotlk = _services_for("wow-wotlk", tmp_path / "wotlk")
    unbound = _services_for("wow-unbound", tmp_path / "unbound")
    user_dir = modules.user_manifests_dir()

    on_wotlk = _added(wotlk, _hand_made_folder(tmp_path / "a", "1"))
    wotlk_record = user_dir / "wow-wotlk" / "modules" / "mod-hand-made.json"
    wotlk_bytes = wotlk_record.read_bytes()
    assert "mod-hand-made" not in _listed(unbound), "WotLK's own module is not offered on Unbound"

    on_unbound = _added(unbound, _hand_made_folder(tmp_path / "b", "0"))
    unbound_record = user_dir / "wow-unbound" / "modules" / "mod-hand-made.json"
    assert unbound_record.is_file()
    assert wotlk_record.read_bytes() == wotlk_bytes, "Unbound's add overwrote WotLK's record"
    assert "mod-hand-made" in _listed(unbound)

    assert unbound.module_forget is not None
    assert unbound.module_forget(on_unbound) is True
    assert not unbound_record.exists()
    assert wotlk_record.read_bytes() == wotlk_bytes, "Unbound's remove dropped WotLK's record"
    assert "mod-hand-made" in _listed(wotlk)
    assert "mod-hand-made" not in _listed(unbound)
    # The manifest the old shared tab handed Unbound's Remove: WotLK's own.
    assert unbound.module_forget(on_wotlk) is False
    assert wotlk_record.read_bytes() == wotlk_bytes


def test_unbound_still_offers_wotlks_shipped_modules(tmp_path: Path) -> None:
    shipped = set(ManifestStore(resources.manifests_dir(), "wow-wotlk").load_index("module").items)
    unbound = ControllerServices.for_entry(entry("wow-unbound"), tmp_path)
    assert shipped <= set(_listed(unbound))


def test_the_update_count_and_the_sql_run_read_unbounds_own_user_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two other readers of the merged list: the update count's branch table and
    the importer's plan. Both are handed the server's own game id."""
    from yulon.controller_wow_wotlk import modules

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        modules, "module_updates", lambda server_dir, **kw: seen.update(updates=kw) or ()
    )
    monkeypatch.setattr(
        modules, "apply_module_sql", lambda server_dir, **kw: seen.update(sql=kw) or None
    )
    services = ControllerServices.for_entry(entry("wow-unbound"), tmp_path)
    assert services.module_updates is not None and services.module_sql is not None
    services.module_updates()
    services.module_sql(lambda _line: None)
    assert seen["updates"] == {"user_game": "wow-unbound"}
    assert seen["sql"]["user_game"] == "wow-unbound"  # type: ignore[index]


def test_the_merged_list_reads_the_named_servers_user_layer_only(tmp_path: Path) -> None:
    from yulon import module_source
    from yulon.controller_wow_wotlk import modules

    source = _hand_made_folder(tmp_path, "1")
    made = modules.derive_folder(source, "wow-unbound")
    module_source.persist(modules.user_manifests_dir(), made, shipped_ids=())
    assert "mod-hand-made" in {m.id for m in modules._module_manifests("wow-unbound")}
    assert "mod-hand-made" not in {m.id for m in modules._module_manifests()}


def test_the_update_counts_branch_table_reads_the_named_servers_user_layer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import module_source
    from yulon.controller_wow_wotlk import modules

    made = modules.derive_link("https://github.com/you/mod-linked", "wow-unbound")
    module_source.persist(modules.user_manifests_dir(), made, shipped_ids=())
    asked: list[set[str]] = []
    monkeypatch.setattr(
        modules, "apply_updates", lambda _dir, **kw: asked.append(set(kw["branches"])) or ()
    )
    modules.module_updates(tmp_path, git=object(), user_game="wow-unbound")  # type: ignore[arg-type]
    modules.module_updates(tmp_path, git=object())  # type: ignore[arg-type]
    assert "mod-linked" in asked[0]
    assert "mod-linked" not in asked[1]


def test_a_module_added_from_a_link_on_unbound_is_unbounds_and_lands_in_its_layer(
    tmp_path: Path,
) -> None:
    """The link route of item 2 (re-review note 1): the manifest Unbound's tab derives
    from a link names `wow-unbound`, so `complete()` keeps it under Unbound's own
    user layer and never under WotLK's."""
    from yulon.controller_wow_wotlk import modules

    unbound = ControllerServices.for_entry(entry("wow-unbound"), tmp_path / "unbound")
    assert unbound.module_from_link is not None
    linked = unbound.module_from_link("https://github.com/you/mod-linked")
    assert linked.game == "wow-unbound"
    clone = tmp_path / "clone"
    (clone / "src").mkdir(parents=True)
    modules.complete(linked, clone)
    user_dir = modules.user_manifests_dir()
    assert (user_dir / "wow-unbound" / "modules" / "mod-linked.json").is_file()
    assert not (user_dir / "wow-wotlk" / "modules" / "mod-linked.json").exists()
    assert "mod-linked" in _listed(unbound)
