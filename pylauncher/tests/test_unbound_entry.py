"""T554: the WoW Unbound entry's own files.

Step Y2: the `wow-unbound` entry in `catalog.json`: its own server beside WotLK.

Step Y1: the five core patches the entry carries, pinned byte for byte. A patch that
drifts by one byte (an editor's CRLF, a re-diffed hunk, a swapped order) would change
what a player's core is built from, so each file's sha256 is written down here as a
constant and the test fails on any difference.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from tests.support_native import Recorder
from yulon import commands, resources, server_rates, useraccounts
from yulon.catalog.catalog import CATALOG_FILE, CatalogEntry, load_catalog, parse_catalog
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.install_wiring import fixed_db_password, import_gate_for
from yulon.party import InstallParty
from yulon.ui import catalog_view
from yulon.ui.widgets.dadcraft_decorations import DadcraftCampaignCard

PATCH_DIR = resources.installers_dir() / "wow-unbound" / "patches"

# name -> sha256, in the order the entry applies them (plan 2026-10-08 §2.2).
# 01 is the `mod-unbound` branch's core-patch/unbound-core-access.patch, whose sha256
# is the one in that branch's MANIFEST.sha256 at d29fac97.
UNBOUND_PATCHES: dict[str, str] = {
    "01-unbound-core-access.patch": "f20915389825e73dc6d4fbb4f420e30e6e27b37ddb57a1fcf399e39986449813",  # noqa: E501
    "02-player-learntalent.patch": "07cbaeeb7840aaff1658d86aec5967ca23bc2653be936b7aee7b1fa9b699f034",  # noqa: E501
    "03-feral-spirit-coexist.patch": "0b172ff6b18fbe2e6551a189781b26ab6f673930edce9c3ec1c3b0e544c7bec1",  # noqa: E501
    "04-pet-commands.patch": "7be5aed89bdfff62ac06d086198c41c7c7ea04be194e620ac6b2d83c93e5a4c6",
    # T556's mana regen fix; the module's core-patch/unbound-mana-regen.patch, which AzerothCore
    # never applies by itself, so the entry carries it (U7 cold review).
    "05-unbound-mana-regen.patch": "069278588dcd2f5d0a5c684fc4924f2bf710889768f31c220e9c06217787f099",  # noqa: E501
}


def test_the_patch_folder_holds_exactly_the_five_patches_in_order() -> None:
    assert sorted(p.name for p in PATCH_DIR.glob("*")) == list(UNBOUND_PATCHES)


@pytest.mark.parametrize(("name", "digest"), UNBOUND_PATCHES.items())
def test_every_unbound_patch_is_shipped_byte_exact(name: str, digest: str) -> None:
    data = (PATCH_DIR / name).read_bytes()
    assert b"\r" not in data, f"{name} must be LF-only"
    assert hashlib.sha256(data).hexdigest() == digest


# -- Y2: the entry --------------------------------------------------------------

UNBOUND_ID = "wow-unbound"
UNBOUND_REPO = "DadsMmoLab/dads-mmo-lab"
UNBOUND_BRANCH = "mod-unbound"
UNBOUND_REV = "456ce5b6df63b7d219905f6669ad4813516b5b4a"
"""The `mod-unbound` head that carries U1-U7 (the AzerothCore module layout, T556's fixes, the
mana-regen patch) and M1 (the Mentor spawns), as DadsMmoLab/dads-mmo-lab says it. The five patch
files here equal that head's `core-patch/` files byte for byte (checked when this was set)."""
MOD_ALE_REV = "1cb86c9600260c3731c96dc3c98d25b4fc3f2153"
DK_SENTENCE = "Death Knight can be your first class, not an added one."


def unbound() -> CatalogEntry:
    return load_catalog().get(UNBOUND_ID)


def unbound_json() -> dict:  # type: ignore[type-arg]
    with CATALOG_FILE.open(encoding="utf-8") as fh:
        return next(g for g in json.load(fh)["games"] if g["id"] == UNBOUND_ID)


def make_installer(entry: CatalogEntry) -> AzerothCoreInstaller:
    rec = Recorder()
    return AzerothCoreInstaller(
        entry,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(),
    )


def test_the_entry_is_in_the_catalog_as_a_beta_server_of_its_own() -> None:
    entry = unbound()
    assert (entry.name, entry.status) == ("WoW Unbound", "beta")
    assert entry.install.default_server_dir == "yulon-unbound"
    assert DK_SENTENCE in entry.description
    assert entry.manifest_game() == "wow-wotlk"
    assert entry.has_manifests


def test_it_has_its_own_containers_ports_and_soap() -> None:
    entry = unbound()
    containers = entry.containers
    assert (
        containers.db,
        containers.auth,
        containers.world,
        containers.db_import,
        containers.client_data,
    ) == ("ub-database", "ub-authserver", "ub-worldserver", "ub-db-import", "ub-client-data-init")
    assert (entry.ports.auth, entry.ports.world, entry.ports.db) == (3725, 8086, 3307)
    assert entry.install.native is not None
    assert entry.install.native.soap_port == 7879
    assert entry.operations is not None
    assert entry.operations.port == 7879
    assert entry.install.native.image_prefix == "yulon.local/ac-unbound-"


def test_its_database_password_plan_is_the_fixed_one_the_import_gate_reads() -> None:
    """`import_gate_for` reads `fixed_db_password`; a generated plan would give the check the
    wrong password (T552's cold review)."""
    entry = unbound()
    assert entry.install.password.mode == "fixed"
    assert fixed_db_password(entry) == fixed_db_password(load_catalog().get("wow-wotlk"))
    probe, reset = import_gate_for(entry)
    assert probe is not None and reset is not None


def test_it_builds_the_core_modules_ale_and_the_unbound_branch_at_pinned_commits() -> None:
    sources = {s.dest: s for s in unbound().emulator.sources}
    wotlk = {s.dest: s for s in load_catalog().get("wow-wotlk").emulator.sources}
    for dest, source in wotlk.items():
        assert (sources[dest].repo, sources[dest].branch, sources[dest].rev) == (
            source.repo,
            source.branch,
            source.rev,
        )
    ale = sources["modules/mod-ale"]
    assert (ale.repo, ale.rev) == ("azerothcore/mod-ale", MOD_ALE_REV)
    mine = sources["modules/mod-unbound"]
    assert (mine.repo, mine.branch, mine.rev) == (UNBOUND_REPO, UNBOUND_BRANCH, UNBOUND_REV)
    assert set(sources) == set(wotlk) | {"modules/mod-ale", "modules/mod-unbound"}


def test_the_five_core_patches_are_applied_in_order_to_the_core() -> None:
    patches = unbound().install.native.azerothcore.patches  # type: ignore[union-attr]
    assert [p.file for p in patches] == [f"wow-unbound/patches/{n}" for n in UNBOUND_PATCHES]
    assert {p.source for p in patches} == {"."}
    assert all(p.reason.endswith(".") for p in patches)


def test_the_world_env_locks_ale_auto_reload_off_and_names_no_unbound_switch() -> None:
    env = unbound().install.native.azerothcore.world_env  # type: ignore[union-attr]
    assert env["AC_VALIDATE_SKILL_LEARNED_BY_SPELLS"] == "0"
    assert env["AC_ALE_ENABLED"] == "1"
    assert env["AC_ALE_AUTO_RELOAD"] == "0"
    assert env["AC_ALE_SCRIPT_PATH"] == "/azerothcore/env/dist/etc/modules/lua_scripts"
    # Environment beats the conf, so a key here would freeze the switch the Tuning card sets.
    assert [key for key in env if key.startswith("AC_UNBOUND_")] == []
    assert env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == env["AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"] == "500"


def test_its_module_confs_are_made_from_their_dist_and_its_lua_goes_in_a_folder_of_its_own() -> (
    None
):
    block = unbound().install.native.azerothcore  # type: ignore[union-attr]
    assert block.confs_from_dist == (
        "env/dist/etc/modules/playerbots.conf",
        "env/dist/etc/modules/mod_ale.conf",
        "env/dist/etc/modules/mod_unbound.conf",
    )
    assert [(s.src, s.dest) for s in block.lua_scripts] == [
        (
            "modules/mod-unbound/lua_scripts",
            "env/dist/etc/modules/lua_scripts/unbound",
        )
    ]


def test_the_install_checks_what_the_unbound_sql_must_have_made() -> None:
    checks = unbound().install.native.azerothcore.sql_checks  # type: ignore[union-attr]
    assert [(c.db, c.table, c.where, c.at_least) for c in checks] == [
        ("world", "unbound_class_catalog", "", 1000),
        ("world", "unbound_milestones", "", 5),
        ("world", "creature", "id = 900001 AND guid >= 9000101 AND guid <= 9000109", 9),
        ("world", "creature_template", "entry = 900001", 1),
        ("world", "item_template", "entry = 900100", 1),
        ("world", "skillraceclassinfo_dbc", "ID >= 10000", 1),
    ]


# -- T2: the ready-to-play client's addons ------------------------------------------


def test_the_client_carries_the_modules_addon_folder_as_one_required_pack() -> None:
    client = unbound().client
    assert [p.id for p in client.packs] == ["unbound-addons"]
    pack = client.packs[0]
    assert pack.label == "Unbound addons"
    assert (pack.source.kind, pack.source.path) == (
        "checkout_folder",
        "modules/mod-unbound/client/Interface/AddOns",
    )
    assert pack.sha256_file == "modules/mod-unbound/MANIFEST.sha256"
    assert (pack.sha256, pack.md5, pack.md5_file) == (None, None, None)
    assert [(r.member, r.to_dir, r.to) for r in pack.install] == [("*", "Interface/AddOns", None)]
    assert not pack.optional, "required: every Play keeps the client half in step with the server"


def test_the_pack_reads_the_module_the_entry_builds() -> None:
    """The folder and the list sit in the checkout the entry pins as `modules/mod-unbound`."""
    pack = unbound().client.packs[0]
    dests = {s.dest for s in unbound().emulator.sources}
    for path in (pack.source.path, pack.sha256_file):
        assert path is not None and path.startswith("modules/mod-unbound/")
    assert "modules/mod-unbound" in dests


def test_load_out_of_date_addons_is_set_at_every_play_never_only_once() -> None:
    cfg = unbound().client.config_wtf
    assert cfg is not None
    assert {k.casefold(): v for k, v in cfg.always.items()} == {"checkaddonversion": "0"}
    assert cfg.seed == {}, "a seed is skipped on a client that already pressed Play once"
    assert not cfg.remove_locale_realmlists


def test_wotlks_client_has_no_packs_and_no_config_wtf() -> None:
    client = load_catalog().get("wow-wotlk").client
    assert client.packs == () and client.config_wtf is None and client.exe_patch is None


def test_the_installer_runs_the_patch_and_lua_stages_and_wotlks_tuple_is_unchanged() -> None:
    names = make_installer(unbound()).stage_names()
    assert "patch-sources" in names and "lua-and-sql" in names
    wotlk = make_installer(load_catalog().get("wow-wotlk")).stage_names()
    assert wotlk == AzerothCoreInstaller.STAGE_NAMES
    assert "patch-sources" not in wotlk and "lua-and-sql" not in wotlk


def test_help_leads_with_the_unbound_channel_then_wotlks_three() -> None:
    labels = [place.label for place in unbound().help_places]
    wotlk = [place.label for place in load_catalog().get("wow-wotlk").help_places]
    assert labels[0].endswith("#wow-unbound")
    assert labels[1:] == wotlk


def test_wotlk_itself_is_untouched_by_the_new_entry() -> None:
    wotlk = load_catalog().get("wow-wotlk")
    assert wotlk.ports.auth == 3724 and wotlk.containers.world == "ac-worldserver"
    assert wotlk.install.native.azerothcore.patches == ()  # type: ignore[union-attr]
    assert wotlk.install.native.azerothcore.lua_scripts == ()  # type: ignore[union-attr]


def test_the_entry_shares_no_host_port_with_any_other_entry() -> None:
    """T552's rule at catalog load; this fails first if a port is copied from WotLK."""
    raw = unbound_json()
    clone = copy.deepcopy(raw)
    clone["ports"]["db"] = 3306
    with pytest.raises(ValueError, match="3306"):
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        data["games"] = [g for g in data["games"] if g["id"] != UNBOUND_ID] + [clone]
        parse_catalog(data)


# -- Y3: where the rows keyed by id treat Unbound as WotLK -------------------------


def wotlk() -> CatalogEntry:
    return load_catalog().get("wow-wotlk")


def test_account_arguments_are_looked_up_by_name_as_on_wotlk() -> None:
    assert not useraccounts.digits_are_ids(unbound())
    assert not useraccounts.password_digits_are_ids(unbound())
    assert unbound().id in commands.NAME_LOOKUP_TREES


def test_my_party_is_possible_on_unbound_and_still_not_on_the_cmangos_games() -> None:
    assert InstallParty.for_entry_is_possible(unbound())
    assert InstallParty.for_entry_is_possible(wotlk())
    assert not InstallParty.for_entry_is_possible(load_catalog().get("wow-tbc"))


def test_the_server_rates_card_reads_the_same_world_conf_and_keys_as_wotlk(tmp_path: Path) -> None:
    entry = unbound()
    assert server_rates.read_at(entry) == server_rates.read_at(wotlk())
    assert server_rates.card_file(entry) == "env/dist/etc/worldserver.conf"
    assert list(server_rates.conf_keys(entry)) == list(server_rates.conf_keys(wotlk()))
    assert len(server_rates.conf_keys(entry)) == 11
    conf = tmp_path / "env/dist/etc/worldserver.conf"
    conf.parent.mkdir(parents=True)
    conf.write_text("Rate.XP.Kill = 2\n", encoding="utf-8")
    rows = server_rates.rows(entry, tmp_path)
    assert len(rows) == 11
    assert {row.key: row.current for row in rows}["Rate.XP.Kill"] == "2"


def test_the_catalog_tile_has_its_own_glyph_subtitle_and_software_words() -> None:
    entry = unbound()
    assert catalog_view._CAMPAIGN_GLYPHS[entry.id] != catalog_view._CAMPAIGN_GLYPHS["wow-wotlk"]
    assert catalog_view._CAMPAIGN_SUBTITLES[entry.id] == "Wrath of the Lich King, multi-class"
    assert (
        catalog_view._server_software(entry) == "AzerothCore with mod-playerbots and Wrath Unbound"
    )
    assert catalog_view.RECOMMENDED == "wow-wotlk"


def test_the_unbound_tile_draws_the_wotlk_backdrop_under_its_own_name(qapp: QApplication) -> None:
    card = DadcraftCampaignCard("wow-unbound")
    assert card.objectName() == "catalog-tile-wow-unbound"
    assert card._theme == "wow-wotlk"
    card._tick()
    assert DadcraftCampaignCard("wow-tbc")._theme == "wow-tbc"


def test_no_unbound_check_tells_the_player_to_rebuild_for_what_rebuild_cannot_do() -> None:
    """Live H5: Rebuild runs no module SQL again once the importer has ledgered the file."""
    checks = unbound().install.native.azerothcore.sql_checks  # type: ignore[union-attr]
    mentor = next(c for c in checks if c.table == "creature")
    assert "Press Rebuild" not in mentor.reason and "apply it again" not in mentor.reason
    assert "Rebuild does not" in mentor.reason
    assert "Where to get help" in mentor.reason
