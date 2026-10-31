"""T812 C2: the WoW Conquest of Azeroth (wow-coa) entry's own files.

The `wow-coa` entry in `catalog.json`: its own server beside WotLK and Unbound.
The carried vmap patch equals the `origin/tools/extract-original` side of the
pinned core, byte for byte of the two hunks.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from yulon import commands, server_rates, useraccounts
from yulon.catalog import composegen
from yulon.catalog.catalog import CATALOG_FILE, CatalogEntry, load_catalog, parse_catalog
from yulon.party import InstallParty
from yulon.ui import catalog_view
from yulon.ui.widgets.dadcraft_decorations import DadcraftCampaignCard

PATCH_DIR = (
    Path(__file__).resolve().parent.parent / "catalog" / "installers" / "wow-coa" / "patches"
)

COA_ID = "wow-coa"
CORE_REPO = "pjerra/azerothcore-wotlk-coa"
CORE_BRANCH = "coa-bots-2.8"
CORE_REV = "98570f248b6095a23ef14bbbfac6731ac4c20d66"
BOTS_REPO = "pjerra/mod-playerbots-coa"
BOTS_BRANCH = "coa"
BOTS_REV = "2b7a4f6547b2e4b35a4ec3a89374e41e5c6e5272"
"""The two pins. The core branch `coa-bots-2.8` is `98570f24…` and the bots tag
`v2.8` is `2b7a4f65…`, both verified with `git ls-remote` 2026-10-27; the bots
commit sits on branch `coa` (mirror clone, `branch -r --contains`)."""

COA_PATCH = "01-vmap4-extractor-name-lists.patch"


def coa() -> CatalogEntry:
    return load_catalog().get(COA_ID)


def coa_json() -> dict:  # type: ignore[type-arg]
    with CATALOG_FILE.open(encoding="utf-8") as fh:
        return next(g for g in json.load(fh)["games"] if g["id"] == COA_ID)


def test_the_entry_is_in_the_catalog_as_a_beta_server_of_its_own() -> None:
    entry = coa()
    assert (entry.name, entry.status) == ("WoW Conquest of Azeroth", "beta")
    assert entry.install.default_server_dir == "yulon-coa"
    assert "Ascension 3.3.5a client" in entry.description
    assert "Update client makes your Ascension copy one" in entry.description
    assert entry.manifest_game() == "wow-wotlk"
    assert entry.has_manifests


def test_it_builds_the_coa_core_and_bots_at_their_pins() -> None:
    sources = {s.dest: s for s in coa().emulator.sources}
    assert set(sources) == {".", "modules/mod-playerbots"}
    core, bots = sources["."], sources["modules/mod-playerbots"]
    assert (core.repo, core.branch, core.rev) == (CORE_REPO, CORE_BRANCH, CORE_REV)
    assert core.depth is None
    assert (bots.repo, bots.branch, bots.rev) == (BOTS_REPO, BOTS_BRANCH, BOTS_REV)


def test_it_has_its_own_containers_ports_and_soap() -> None:
    entry = coa()
    containers = entry.containers
    assert (
        containers.db,
        containers.auth,
        containers.world,
        containers.db_import,
        containers.client_data,
    ) == ("coa-database", "coa-authserver", "coa-worldserver", "coa-db-import", None)
    assert (entry.ports.auth, entry.ports.world, entry.ports.db) == (3726, 8087, 3308)
    assert entry.install.native is not None
    assert entry.install.native.soap_port == 7880
    assert entry.operations is not None
    assert entry.operations.port == 7880
    assert entry.install.native.image_prefix == "yulon.local/ac-coa-"


def test_coa_ports_collide_with_no_other_entry() -> None:
    """T552's rule at catalog load; this fails first if a port is copied from WotLK."""
    raw = coa_json()
    clone = copy.deepcopy(raw)
    clone["ports"]["db"] = 3307
    with pytest.raises(ValueError, match="3307"):
        data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
        data["games"] = [g for g in data["games"] if g["id"] != COA_ID] + [clone]
        parse_catalog(data)


def test_it_installs_on_linux_and_windows_with_the_coa_floors() -> None:
    entry = coa()
    assert entry.install.platforms == ("linux", "windows")
    native = entry.install.native
    assert native is not None
    assert (native.min_ram_gb, native.warn_ram_gb) == (8, 12)
    assert (native.min_server_dir_gb, native.warn_server_dir_gb) == (25, 40)
    assert native.update_to_latest is False
    assert native.templates == "wow-coa/native"
    assert list(native.images) == ["worldserver", "authserver", "db-import", "tools"]
    assert entry.install.requires_client_dir is True


def test_its_database_password_plan_is_generated_with_a_coa_prefix() -> None:
    plan = coa().install.password
    assert (plan.mode, plan.file, plan.prefix) == ("generated", ".db_password", "coa-")
    assert plan.legacy == "password"


def test_the_carried_vmap_patch_applies_to_the_pin() -> None:
    data = (PATCH_DIR / COA_PATCH).read_bytes()
    assert b"\r" not in data, "the patch must be LF-only"
    files = [line for line in data.decode().splitlines() if line.startswith("diff --git")]
    assert files == [
        "diff --git a/src/tools/vmap4_extractor/adtfile.cpp "
        "b/src/tools/vmap4_extractor/adtfile.cpp",
        "diff --git a/src/tools/vmap4_extractor/wdtfile.cpp "
        "b/src/tools/vmap4_extractor/wdtfile.cpp",
    ], "the patch touches only the two vmap extractor name-list files"
    assert data.count(b"new char[size + 1]") == 3
    assert data.count(b"buf[size] = 0;") == 3


def test_the_vmap_patch_is_applied_to_the_core() -> None:
    patches = coa().install.native.azerothcore.patches  # type: ignore[union-attr]
    assert [p.file for p in patches] == [f"wow-coa/patches/{COA_PATCH}"]
    assert {p.source for p in patches} == {"."}
    assert all(p.reason.endswith(".") for p in patches)


COA_ENV_KEYS = {
    "CoA.AllowRemoteClients": "AC_CO_A_ALLOW_REMOTE_CLIENTS",
    "Updates.EnableDatabases": "AC_UPDATES_ENABLE_DATABASES",
    "Playerbots.Updates.EnableDatabases": "AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES",
    "CoA.BossProbe.Enable": "AC_CO_A_BOSS_PROBE_ENABLE",
    "CoAGameplayTest.Enable": "AC_CO_AGAMEPLAY_TEST_ENABLE",
    "UpdateUptimeInterval": "AC_UPDATE_UPTIME_INTERVAL",
    "AiPlayerbot.MinRandomBots": "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS",
    "AiPlayerbot.MaxRandomBots": "AC_AI_PLAYERBOT_MAX_RANDOM_BOTS",
    "AiPlayerbot.RandomBotAutologin": "AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN",
    "AiPlayerbot.RandomBotJoinBG": "AC_AI_PLAYERBOT_RANDOM_BOT_JOIN_BG",
    "AiPlayerbot.RandomBotAutoJoinBG": "AC_AI_PLAYERBOT_RANDOM_BOT_AUTO_JOIN_BG",
    "AiPlayerbot.SyncLevelWithPlayers": "AC_AI_PLAYERBOT_SYNC_LEVEL_WITH_PLAYERS",
    "AiPlayerbot.RandomBotConcentrateInPlayerZone": (
        "AC_AI_PLAYERBOT_RANDOM_BOT_CONCENTRATE_IN_PLAYER_ZONE"
    ),
    "CoA.ClassModel": "AC_CO_A_CLASS_MODEL",
    "CoA.MapClass10ToWarrior": "AC_CO_A_MAP_CLASS_10_TO_WARRIOR",
    "CoAChallenges.GameModes.Realm": "AC_CO_ACHALLENGES_GAME_MODES_REALM",
    "CoA.GameModeMask": "AC_CO_A_GAME_MODE_MASK",
    "CoA.ClientBooleanConfigs": "AC_CO_A_CLIENT_BOOLEAN_CONFIGS",
    "CoA.RealmType": "AC_CO_A_REALM_TYPE",
    "PlayerStart.CustomSpells": "AC_PLAYER_START_CUSTOM_SPELLS",
    "AlwaysMaxSkillForLevel": "AC_ALWAYS_MAX_SKILL_FOR_LEVEL",
}
"""Every conf key the CoA entry sets, with the env name `composegen.env_name_for`
derives from it. The realm-type rows are the block's plus each type's own."""


def test_every_coa_env_name_is_env_name_for_its_key() -> None:
    for key, name in COA_ENV_KEYS.items():
        assert composegen.env_name_for(key) == name, key
    env = coa().install.native.azerothcore.world_env  # type: ignore[union-attr]
    block_keys = {
        "AC_CO_A_ALLOW_REMOTE_CLIENTS": "1",
        "AC_UPDATES_ENABLE_DATABASES": "0",
        "AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES": "1",
        "AC_CO_A_BOSS_PROBE_ENABLE": "0",
        "AC_CO_AGAMEPLAY_TEST_ENABLE": "0",
        "AC_UPDATE_UPTIME_INTERVAL": "1",
        "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS": "150",
        "AC_AI_PLAYERBOT_MAX_RANDOM_BOTS": "150",
        "AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN": "1",
        "AC_AI_PLAYERBOT_RANDOM_BOT_JOIN_BG": "0",
        "AC_AI_PLAYERBOT_RANDOM_BOT_AUTO_JOIN_BG": "0",
        "AC_AI_PLAYERBOT_SYNC_LEVEL_WITH_PLAYERS": "1",
        "AC_AI_PLAYERBOT_RANDOM_BOT_CONCENTRATE_IN_PLAYER_ZONE": "0",
    }
    assert dict(env) == block_keys
    # Default bots from the setting (150), not a constant: the entry carries them.
    assert env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == env["AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"] == "150"


def test_the_realm_types_are_the_four_db_suffixes_with_coa_default() -> None:
    block = coa().install.native.azerothcore  # type: ignore[union-attr]
    assert [(r.id, r.realm_name) for r in block.realm_types] == [
        ("coa", "Conquest of Azeroth"),
        ("hero", "CoA Free Pick"),
        ("wcr", "CoA Warcraft Reborn"),
        ("wc", "CoA Wildcard"),
    ]
    assert block.default_realm_type == "coa"
    by_id = {r.id: r for r in block.realm_types}
    assert by_id["coa"].world_env == {
        "AC_CO_A_CLASS_MODEL": "coa",
        "AC_CO_A_MAP_CLASS_10_TO_WARRIOR": "1",
    }
    assert by_id["wc"].world_env["AC_CO_ACHALLENGES_GAME_MODES_REALM"] == "WildCard"
    assert by_id["wc"].world_env["AC_CO_A_GAME_MODE_MASK"] == "64"
    assert block.auth_after_world_ready is True
    assert block.world_bootstrap_service == "coa-world-bootstrap"


def test_the_install_checks_the_two_custom_dungeons() -> None:
    checks = coa().install.native.azerothcore.sql_checks  # type: ignore[union-attr]
    assert [(c.db, c.table, c.where, c.at_least) for c in checks] == [
        ("world", "instance_template", "map >= 936 AND map <= 937", 2),
    ]
    assert "two custom dungeons" in checks[0].reason


def test_the_health_block_names_the_coa_startup_lines() -> None:
    health = coa().install.native.azerothcore.health  # type: ignore[union-attr]
    assert health is not None
    assert health.log_markers == (
        "Ascension compatibility enabled; consuming extension opcodes",
        "CoA dungeon boss kit rows for",
    )


def test_coa_compose_has_no_client_data_volume_and_binds_data_ro(tmp_path: Path) -> None:
    from yulon import resources
    from yulon.catalog import composegen as gen

    plan = gen.render(
        coa(),
        tmp_path / "coa",
        templates_root=resources.installers_dir(),
        platform_id=lambda: "linux",
    )
    base = plan.base
    assert "client-data-init" not in base
    assert "client-data:" not in base
    assert "./data:/azerothcore/env/dist/data:ro" in base
    assert "coa-tools" in base
    assert "coa-world-bootstrap" in base


def test_coa_tools_service_never_starts_a_server(tmp_path: Path) -> None:
    from yulon import resources
    from yulon.catalog import composegen as gen

    plan = gen.render(
        coa(),
        tmp_path / "coa",
        templates_root=resources.installers_dir(),
        platform_id=lambda: "linux",
    )
    import yaml

    tools = yaml.safe_load(plan.base)["services"]["coa-tools"]
    assert tools["command"] == ["true"]
    assert tools["restart"] == "no"
    assert "ports" not in tools
    build = yaml.safe_load(plan.build)["services"]["coa-tools"]["build"]
    assert build["target"] == "tools"


def test_the_client_extract_names_wb2_and_the_surgical_936() -> None:
    block = coa().install.native.azerothcore  # type: ignore[union-attr]
    assert block.client_extract is not None
    extract = block.client_extract
    assert extract.client.required_file == "Data/patch-WB2.MPQ"
    assert extract.client.min_mpq == 40
    assert extract.client.locales == ("enUS",)
    assert [(t.name, t.argv[0]) for t in extract.extract.tools] == [
        ("maps, dbc and cameras", "/azerothcore/env/dist/bin/map_extractor"),
        ("vmap extract", "/azerothcore/env/dist/bin/vmap4_extractor"),
        ("vmap assemble", "/azerothcore/env/dist/bin/vmap4_assembler"),
    ]
    assert extract.mmaps.argv[1:] == ("--silent", "--threads", "2")
    assert extract.mmaps_config == "src/tools/mmaps_generator/mmaps-config.yaml"
    assert extract.required_maps == (0, 1, 530, 571, 936, 937)
    assert len(extract.surgical_maps) == 1
    surgical = extract.surgical_maps[0]
    assert (surgical.map_id, surgical.archive, surgical.link_as) == (
        936,
        "Data/patch-WB1.MPQ.DISABLED",
        "Data/patch-WB1.MPQ",
    )
    assert surgical.extra_vmaps == ("Ns_Inquisitorial_Dungeon.wmo.vmo",)
    assert surgical.min_maps == 9


def test_the_client_is_the_ascension_one_with_the_dll_override() -> None:
    client = coa().client
    assert (client.version, client.build, client.required_build) == ("3.3.5a", 12340, 12340)
    assert client.addon_interface == 30300
    assert client.exe_names == ("ascension.exe",)
    assert client.wine_dll_overrides == "divxtac=d"
    assert client.packs == ()
    assert any("Ascension 3.3.5a client" in note for note in client.notes)


def test_play_accounts_and_operations_are_wotlks() -> None:
    entry, wotlk = coa(), load_catalog().get("wow-wotlk")
    assert entry.play == wotlk.play
    assert entry.accounts == wotlk.accounts
    assert entry.observability == wotlk.observability
    assert entry.operations is not None and wotlk.operations is not None
    assert entry.operations.namespace == wotlk.operations.namespace == "urn:AC"
    assert entry.operations.port == 7880 and wotlk.operations.port == 7878


def test_help_names_the_coa_forks_then_yulon() -> None:
    assert [(p.label, p.url) for p in coa().help_places] == [
        (
            "Zyth45/azerothcore-wotlk-coa",
            "https://github.com/Zyth45/azerothcore-wotlk-coa/issues",
        ),
        ("Zyth45/mod-playerbots", "https://github.com/Zyth45/mod-playerbots/issues"),
        ("Yu'lon", "https://github.com/DadsMmoLab/dads-mmo-lab/issues"),
    ]


def test_account_arguments_are_looked_up_by_name_as_on_wotlk() -> None:
    assert not useraccounts.digits_are_ids(coa())
    assert not useraccounts.password_digits_are_ids(coa())
    assert coa().id in commands.NAME_LOOKUP_TREES


def test_my_party_stays_off_on_coa_without_mod_ale() -> None:
    # Plan C2: NOT party.py (CoA has no mod-ale), so the gate stays shut.
    assert not InstallParty.for_entry_is_possible(coa())


def test_the_server_rates_card_reads_the_coa_world_conf_at_its_pin(tmp_path: Path) -> None:
    entry = coa()
    assert server_rates.read_at(entry) == (CORE_REPO, CORE_REV)
    assert server_rates.card_file(entry) == "env/dist/etc/worldserver.conf"
    assert list(server_rates.conf_keys(entry)) == list(
        server_rates.conf_keys(load_catalog().get("wow-wotlk"))
    )


def test_the_catalog_tile_has_its_own_glyph_subtitle_and_software_words() -> None:
    entry = coa()
    assert catalog_view._CAMPAIGN_GLYPHS[entry.id] == "⚔"
    assert (
        catalog_view._CAMPAIGN_SUBTITLES[entry.id]
        == "Wrath of the Lich King, Conquest of Azeroth classes"
    )
    assert catalog_view._server_software(entry) == "AzerothCore CoA fork with SquidBots playerbots"


def test_the_coa_tile_draws_the_wotlk_backdrop_under_its_own_name(qapp: QApplication) -> None:
    card = DadcraftCampaignCard("wow-coa")
    assert card.objectName() == "catalog-tile-wow-coa"
    assert card._theme == "wow-wotlk"
