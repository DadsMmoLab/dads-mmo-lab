"""The shipped `wow-centurion` entry (T179 Task 7): the values, against the facts they came from.

Every value here was read at CENTURION @ faac5fc9 (github.com/thomasjteachey/TrinityCore112),
the commit `git ls-remote` still answered for the branch on 2026-10-02:

* the server: `.notes/tickets/T179-centurion-facts.md` (path:line in each comment below);
* the client: the T181 b/c spec's facts, `centurion/patches/` (8 zips, `patches.md5`), and
  Centurion's launcher (`centurion/launcher/src/common/constants.ts` FileMap,
  `main/modules/patcher.ts`), which is where the install map, the HD packs and the Wow.exe
  table are written down;
* the HD packs: an HTTPS HEAD on each zip at centurionpvp.com on 2026-10-02 (Content-Length),
  the zip's own central directory (read by a range request) for its members.

T500 (2026-10-06) moved the pin to 56fe34fa. None of the 14 commits after faac5fc9 touches
`centurion/patches/`, `centurion/launcher/`, a `sql/` file or CMake, and `worldserver.conf.dist`
only gained keys, so every value read at faac5fc9 still holds at 56fe34fa. They do change five
files in `centurion/dbc/` (Item, ItemDisplayInfo, ItemSet, Spell, SpellShapeshiftForm), the
folder `dbc_overlay_from` lays over the map data, so an install moved onto 56fe34fa is asked to
"Re-extract map data". The gate was the existing `yulon-ubuntu2` install, moved onto it by the
"Return to the tested pin…" route, then re-extracted, started and logged into again.

T524 (2026-10-07) moved the pin to 6c6472c3, one commit after 56fe34fa: Battleground.cpp,
PathGenerator.cpp/.h and MoveSplineInit.cpp. No conf, SQL, CMake, dbc, World.cpp or
World.h change, so every reading above still holds; the `World.cpp` lines cited in
this file and in the catalog were re-read at 6c6472c3 on that date.

The entry's family blocks were tested before this entry existed against the fixture in
`tests/support_trinitycore.py`; the last test here holds the two together, so a template
proved on the fixture is proved on what ships.
"""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter

from tests.support_trinitycore import centurion_like
from yulon import client_exe
from yulon.catalog import composegen
from yulon.catalog.catalog import CatalogEntry, ClientPack, load_catalog
from yulon.catalog.families import conf as conf_files

ENTRY: CatalogEntry = load_catalog().get("wow-centurion")
NATIVE = ENTRY.install.native
assert NATIVE is not None and NATIVE.trinitycore is not None
TC = NATIVE.trinitycore
REV = "6c6472c3b6aeb89169d7d49c45af7f7eab326743"
CHECKOUT = "src/centurion"
PATCHES = f"{CHECKOUT}/centurion/patches"
CORE = "/opt/trinitycore"
TEMPLATES = Path(__file__).resolve().parents[1] / "catalog" / "installers"


def test_the_source_is_the_centurion_branch_at_the_tested_commit() -> None:
    """The repo's default branch is `master`, an old snapshot (README.md:22-23): always named."""
    (source,) = ENTRY.emulator.sources
    assert source.repo == "thomasjteachey/TrinityCore112"
    assert source.branch == "CENTURION"
    assert source.rev == REV
    assert source.dest == CHECKOUT == TC.checkout
    assert TC.sparse_exclude == ("playerbot reference", "centurion/launcher")


def test_it_installs_on_windows_and_linux_and_not_yet_on_macos() -> None:
    """Owner decision 6: Windows + Linux first (the Steam Deck is Linux)."""
    assert ENTRY.install.platforms == ("linux", "windows")
    assert ENTRY.install.requires_client_dir is True
    assert NATIVE.family == "trinitycore"
    assert NATIVE.update_to_latest is True


def test_the_description_is_two_sentences_a_player_reads() -> None:
    sentences = [part for part in ENTRY.description.split(". ") if part]
    assert len(sentences) == 2, ENTRY.description
    assert ENTRY.description.endswith(".")
    for internal in ("TrinityCore", "faac5fc9", "trinitycore", "catalog"):
        assert internal not in ENTRY.description


def test_ports_databases_and_the_realm_row() -> None:
    """auth 3724, world 8085 (auth_data.sql:73), SOAP 7878 on loopback (facts §4)."""
    assert (ENTRY.ports.auth, ENTRY.ports.world, NATIVE.soap_port) == (3724, 8085, 7878)
    assert ENTRY.operations is not None
    assert ENTRY.operations.port == 7878
    assert ENTRY.operations.namespace == "urn:TC"
    assert ENTRY.operations.gm_level == 3  # SEC_ADMINISTRATOR, TCSoap.cpp:83-103
    assert ENTRY.databases.auth == "centurion_auth"
    assert ENTRY.databases.world == "centurion_world"
    assert ENTRY.databases.characters == "centurion_characters"
    assert ENTRY.realmlist.local_address_column == "localAddress"  # auth_schema.sql:399


def test_the_world_is_ready_when_it_says_so_after_its_network_is_up() -> None:
    """`(worldserver-daemon) ready...` (worldserver/Main.cpp:446) is printed after the SOAP
    thread, the world socket and the realm's online flag (:383-425); `World initialized`
    (World.cpp:2492 at 6c6472c3, read 2026-10-07) comes before all three. The fatal line is
    World.cpp:1833, whole."""
    assert NATIVE.ready.world == "(worldserver-daemon) ready..."
    assert NATIVE.ready.regex is False
    assert NATIVE.ready.fatal == "Unable to load critical files - server shutting down !!!"
    # The signal handler is installed before the world loads (Main.cpp:304-308, load at
    # :364), as on AzerothCore, so a stop needs no wait for the load.
    assert NATIVE.stop_waits_for_world_load is False


def test_the_client_folder_must_be_an_english_wotlk_client() -> None:
    """Centurion's locale patches are enUS only (patch-enUS-6/7/A): another locale is refused."""
    assert TC.client.required_file == "Data/lichking.MPQ"
    assert TC.client.locales == ("enUS",)
    assert TC.client.locale_mpq_required is True


def test_the_map_data_takes_maps_cameras_and_vmaps_and_the_trees_own_dbcs() -> None:
    """`-e 5` = maps + cameras (map_extractor System.cpp:66-68): the DBCs are the checkout's
    (`centurion/dbc`, README.md:174-180), and `Cameras/` goes into DataDir beside maps."""
    maps, extract_v, assemble = TC.extract.tools
    assert maps.argv == (f"{CORE}/bin/mapextractor", "-i", "/client", "-o", "/out", "-e", "5")
    assert set(maps.produces) == {"maps", "Cameras"}
    assert extract_v.argv == (f"{CORE}/bin/vmap4extractor", "-d", "/client/Data/")
    assert assemble.argv == (f"{CORE}/bin/vmap4assembler", "Buildings", "vmaps")
    assert TC.extract.dbc_overlay_from == "centurion/dbc"
    assert TC.required_maps == (0, 1, 530)
    assert TC.mmaps.argv == (f"{CORE}/bin/mmaps_generator", "--threads", "{{THREADS}}")
    assert TC.mmaps.background is True
    assert TC.mmaps.min_files == 500


def test_the_world_conf_keeps_the_updater_off_and_plays_like_the_live_realm() -> None:
    keys = TC.conf.files["worldserver.conf"].keys
    assert keys["Updates.EnableDatabases"] == "0"  # README.md:213-214, .dist ships 7 (:1470)
    assert keys["mmap.enablePathFinding"] == "0"  # on once the background job finished
    assert keys["SOAP.Enabled"] == "1" and keys["SOAP.Port"] == "7878"
    # README.md:215-217 "To play like the live realm"; the .dist has 80, 0, 0 and 0.
    assert keys["MaxPlayerLevel"] == "60"
    assert keys["GameType"] == "1"
    assert keys["Centurion.Hardcore.Enable"] == "1"
    assert keys["Centurion.Tournament.Enable"] == "1"
    # README.md:363-366: the bot cost reducers the live realm runs; the bounty hunters the
    # live playerbots.conf turns on need Centurion.Bounty.Enable (README.md:353-354).
    assert keys["Centurion.Bots.SkipClientPackets"] == "1"
    assert keys["Centurion.Bots.GridActivationRange"] == "90"
    assert keys["Centurion.Bounty.Enable"] == "1"


def test_the_freeze_detector_waits_out_the_first_bot_rebalance() -> None:
    """T205: the .dist's `MaxCoreStuckTime = 60` (worldserver.conf.dist:459-464 at
    5e732762) killed fresh boots while the world thread built the first 150 bots; the
    one boot that lived spent 59.1 s in that step. 300 s, not 0: 0 switches the detector
    off (Main.cpp:438), and a realm that really hangs would then never come back."""
    keys = TC.conf.files["worldserver.conf"].keys
    assert keys["MaxCoreStuckTime"] == "300"


def _centurion_etc(tmp_path: Path, world_conf: str) -> Path:
    """An `etc/` with Centurion's three table files, the world one as given."""
    etc = tmp_path / "etc"
    etc.mkdir()
    (etc / "worldserver.conf").write_text(world_conf, encoding="utf-8")
    (etc / "authserver.conf").write_text('LogsDir = ""\n', encoding="utf-8")
    (etc / "playerbots.conf").write_text("Playerbot.Enable = 0\n", encoding="utf-8")
    return etc


def test_the_conf_table_writes_the_freeze_detectors_wait_over_a_60(tmp_path: Path) -> None:
    """`apply_table()` over Centurion's own table: the .dist's 60 becomes 300, and a conf
    that says 60 again is rewritten and reported changed. The conf stage makes exactly
    this call; no press that reaches it on a remembered server is claimed here."""
    tokens = {**composegen.entry_tokens(ENTRY), "DB_PASSWORD": "tc-secret", "WORLD_PORT": "8085"}
    etc = _centurion_etc(tmp_path, "[worldserver]\nMaxCoreStuckTime = 60\n")
    conf_files.apply_table(TC.conf, etc, tokens)
    world = (etc / "worldserver.conf").read_text(encoding="utf-8")
    assert "\nMaxCoreStuckTime = 300\n" in world
    assert "MaxCoreStuckTime = 60" not in world
    (etc / "worldserver.conf").write_text(
        world.replace("MaxCoreStuckTime = 300", "MaxCoreStuckTime = 60"), encoding="utf-8"
    )
    assert conf_files.apply_table(TC.conf, etc, tokens) == (etc / "worldserver.conf",)
    assert "\nMaxCoreStuckTime = 300\n" in (etc / "worldserver.conf").read_text(encoding="utf-8")


def test_the_bots_conf_is_the_live_realms_over_the_dist() -> None:
    """`centurion/conf/playerbots.conf`'s every key that differs from the `.dist` (68 at
    faac5fc9): with the `.dist` alone every bot feature is off (playerbots.conf.dist:27)."""
    keys = TC.conf.files["playerbots.conf"].keys
    assert TC.conf.playerbots_conf == "playerbots.conf"
    assert len(keys) == 68
    assert keys["Playerbot.Enable"] == "1"  # live :27
    assert keys["Playerbot.PvpCore.Enable"] == "1"  # live :35
    assert keys["Playerbot.PvpClassSpells.Enable"] == "1"  # live :66, needed even for PvE
    assert keys["Playerbot.RandomPopulation.TargetMin"] == "150"  # live :83
    assert keys["Playerbot.RandomPopulation.TargetMax"] == "150"  # live :90
    assert keys["Playerbot.RandomPopulation.BotAccountIds"] == "76,77,78"  # live :104
    assert keys["Playerbot.Pve.PvpOnlyAccountIds"] == '"79"'  # live :220
    assert keys["Playerbot.BgFill.Tier.Easy.Characters"] == '"100955-100963"'  # live :529


def test_the_logs_dir_of_each_server_is_a_folder_the_compose_binds(tmp_path: Path) -> None:
    """LogsDir "../logs" from `{CORE}/bin` is `{CORE}/logs`, bound from `./logs` on both."""
    plan = composegen.render(
        ENTRY,
        tmp_path / "wow-centurion-server",
        templates_root=TEMPLATES,
        db_password="tc-0123456789abcdef",
        bind_label="",
        platform_id=lambda: "linux",
    )
    services = yaml.safe_load(plan.base)["services"]
    for conf, service in (
        ("worldserver.conf", "centurion-worldserver"),
        ("authserver.conf", "centurion-authserver"),
    ):
        assert TC.conf.files[conf].keys["LogsDir"] == '"../logs"'
        assert services[service]["working_dir"] == f"{CORE}/bin"
        assert f"./logs:{CORE}/logs" in services[service]["volumes"], service
    assert f"./data:{CORE}/data" in services["centurion-worldserver"]["volumes"]


# -- the ready-to-play client ----------------------------------------------------------------

REQUIRED = {
    # pack id: (zip in centurion/patches, member, target, joined size in bytes)
    "art-base": ("patch-Y.zip", "patch-Y.MPQ", "Data/patch-X.MPQ", 1_232_561_101),
    "art-update": ("patch-Z.zip", "patch-Z.MPQ", "Data/patch-Z.MPQ", 889),
    "dungeon-maps": ("patch-dungeon-maps.zip", "patch-M.MPQ", "Data/patch-M.MPQ", 48_792_731),
    "locale-6": ("patch-enUS-6.zip", "patch-enUS-6.MPQ", "Data/enUS/patch-enUS-6.MPQ", 122_785_262),
    "locale-7": ("patch-enUS-7.zip", "patch-enUS-7.MPQ", "Data/enUS/patch-enUS-7.MPQ", 4_974_354),
    "locale-a": ("patch-enUS-A.zip", "patch-enUS-A.MPQ", "Data/enUS/patch-enUS-A.MPQ", 60_709_870),
    "addons": ("addons.zip", "*", "Interface/AddOns", 214_001),
    "client-tweaks": ("client-tweaks.zip", "dinput8.dll", "dinput8.dll", 100_566),
}
"""The launcher's FileMap (constants.ts:155-243, :317-319) and each zip's central directory.

patch-Y.zip's one member is `patch-Y.MPQ`, written as `patch-X.MPQ` (constants.ts:220-225:
the art base moved down a letter on 2026-09-17 to free patch-Y for the Alt World pack).
Sizes from the GitHub tree at faac5fc9: patch-Y is 23 x 51,380,224 + 50,815,949 in 24
parts, patch-enUS-6 2 x 51,380,224 + 20,024,814, patch-enUS-A 51,380,224 + 9,329,646.
"""

HD = {
    # pack id: (members, Content-Length on 2026-10-02, version that day)
    "hd-creatures": (("patch-F.MPQ",), 1_454_949_079, "1.00005"),
    "hd-textures": (("patch-T.MPQ",), 418_008_264, "1.00000"),
    "hd-spells": (("patch-G.MPQ", "patch-H.MPQ"), 83_185_849, "1.00003"),
    "hd-bgs": (("patch-U.MPQ",), 232_975_066, "1.00000"),
    "hd-misc": (("patch-L.MPQ",), 121_927_714, "1.00000"),
    "world-terrain": (("patch-Y.MPQ",), 146_670_268, "1.00006"),
}
"""constants.ts:244-302, each at https://centurionpvp.com/downloads/patches/<id>.zip.

The version is not in the catalog (the site's `.version` is read at each Play); it is
written here only so this record says what was there when the sizes were taken.
"""


def _packs() -> dict[str, ClientPack]:
    return {pack.id: pack for pack in ENTRY.client.packs}


def test_the_client_is_3_3_5a_reporting_build_12342() -> None:
    """The realm row's `gamebuild` is 12342 (auth_data.sql:73); a 12340 client sees it offline
    (AuthSession.cpp:755-765), which is what the exe patch's build string is for."""
    assert (ENTRY.client.version, ENTRY.client.build) == ("3.3.5a", 12342)
    assert list(_packs()) == [*REQUIRED, *HD]


def test_the_required_packs_come_from_the_checkout_checked_by_its_own_patches_md5() -> None:
    for pack_id, (zip_name, member, target, size) in REQUIRED.items():
        pack = _packs()[pack_id]
        assert pack.source.kind == "checkout"
        assert pack.source.path == f"{PATCHES}/{zip_name}"
        assert pack.md5_file == f"{PATCHES}/patches.md5", pack_id
        assert pack.md5 is None and pack.sha256 is None, "the checkout says, not a pin"
        assert pack.optional is False
        assert pack.size_hint == size
        (rule,) = pack.install
        assert rule.member == member
        assert (rule.to or rule.to_dir) == target


def test_the_hd_packs_are_optional_off_by_default_downloads_from_centurions_site() -> None:
    for pack_id, (members, size, _version) in HD.items():
        pack = _packs()[pack_id]
        base = f"https://centurionpvp.com/downloads/patches/{pack_id}"
        assert pack.source.kind == "url"
        assert (pack.source.url, pack.source.version_url) == (f"{base}.zip", f"{base}.version")
        assert pack.optional is True and pack.default is False, "opt-in (T181 decision 1)"
        assert pack.size_hint == size
        assert [(rule.member, rule.to) for rule in pack.install] == [
            (member, f"Data/{member}") for member in members
        ]
    # The launcher deletes any patch-Y.MPQ while Alt World is off (constants.ts:293-302):
    # before the art base moved to patch-X, every client carried it under that name.
    assert _packs()["world-terrain"].remove_when_off == ("Data/patch-Y.MPQ",)
    assert all(
        not _packs()[pack_id].remove_when_off for pack_id in HD if pack_id != "world-terrain"
    )


def test_the_extraction_client_gets_the_required_packs_only() -> None:
    """Review Focus 1: the extractors read every lettered archive, so no HD pack may be
    required -- the extraction client is made from the required packs alone."""
    required = {pack.id for pack in ENTRY.client.packs if not pack.optional}
    assert required == set(REQUIRED)


def test_config_wtf_points_at_the_realm_named_centurion() -> None:
    """patcher.ts writes realmList/patchList/realmName always and these seeds once; the realm
    is picked by name, which is why `Centurion` must match the realm row (README.md:267)."""
    cfg = ENTRY.client.config_wtf
    assert cfg is not None
    assert cfg.always == {
        "realmList": "127.0.0.1",
        "patchList": "127.0.0.1",
        "realmName": "Centurion",
        "hwDetect": "0",
    }
    assert cfg.seed == {"gxWindow": "1", "gxMaximize": "1", "checkAddonVersion": "0"}
    assert cfg.remove_locale_realmlists is True


# -- Wow.exe ---------------------------------------------------------------------------------

STOCK_SIZE = 7_704_216
PE_AT = 0x100


def _stand_in() -> bytes:
    """A synthetic stock exe: never Blizzard's (T181 spec §4). A PE header at 0x100."""
    data = bytearray((bytes(range(256)) * (STOCK_SIZE // 256 + 1))[:STOCK_SIZE])
    struct.pack_into("<I", data, 0x3C, PE_AT)
    data[PE_AT : PE_AT + 4] = b"PE\0\0"
    struct.pack_into("<H", data, PE_AT + 0x16, 0x010F)
    return bytes(data)


def _centurion_launcher(stock: bytes, *, borderless: bool) -> bytes:
    """`patchConfig()`'s Wow.exe half (centurion/launcher/src/main/modules/patcher.ts:62-306),
    statement by statement in its own order and shape -- the 0-fill then the build string, the
    uint16, the byte list, the PE flag read through `e_lfanew` -- so the catalog's table, folded
    into non-overlapping writes, is checked against the launcher's own sequence.
    """
    buf = bytearray(stock)
    buf[0x5F3A00 : 0x5F3A00 + 6] = b"\0" * 6
    buf[0x5F3A00 : 0x5F3A00 + 5] = b"12342"
    struct.pack_into("<H", buf, 0x4C99F0, 12342)
    for offset, value in (
        (0x1F41BF, [0xEB]),
        (0x415A25, [0xEB]),
        (0x415A3F, [0x03]),
        (0x415A95, [0x03]),
        (0x415B46, [0xEB]),
        (0x415B5F, [0xB8, 0x03, 0x00, 0x00, 0x00, 0xEB, 0xED]),
    ):
        buf[offset : offset + len(value)] = bytes(value)
    buf[0x0E94] = 0xEB if borderless else 0x74
    buf[0x2E1C67 : 0x2E1C67 + 11] = b"\x90" * 11
    buf[0x33D7C9] = 0xEB
    buf[0x0355BF] = 0xEB
    buf[0x33E0D6 : 0x33E0D6 + 22] = b"\x90" * 22
    buf[0x469A2C : 0x469A2C + 12] = bytes.fromhex("e971f00b00f813d4008b1dfc")
    buf[0x528AA2 : 0x528AA2 + 25] = bytes.fromhex(
        "8d4df05157ff15dcf59d008b45f08b15f813d400e97a0ff4ff"
    )
    mouse = (
        "89e58b05fc13d4008b0df813d400ebc27d0383c10183c03283c1323b0decbcca007e0383e9013b05f0"
        "bcca007e0383e80183e93283e832890df813d4008905fc13d40089ec5de9b4f7ffffec5dc3c3"
    )
    buf[0x4691B1 : 0x4691B1 + len(mouse) // 2] = bytes.fromhex(mouse)
    buf[0x469183 : 0x469183 + 13] = bytes.fromhex("83f8327d0383c00183f932eb31")
    buf[0x1DDC5D] = 0xEB
    buf[0x16D899 : 0x16D899 + 5] = bytes([0x05, 0x01, 0x00, 0x00, 0x00])
    buf[0x2DB241] = 50
    buf[0x5CFBC0 : 0x5CFBC0 + 11] = bytes.fromhex("c705748ed300ffffffffc3")
    buf[0x10CA41] = 0xEB
    buf[0x6404F] = 0x14
    pe = struct.unpack_from("<I", buf, 0x3C)[0]
    struct.pack_into("<H", buf, pe + 0x16, struct.unpack_from("<H", buf, pe + 0x16)[0] | 0x20)
    return bytes(buf)


def test_the_exe_patch_is_the_stock_3_3_5a_exe_from_the_owners_clean_client() -> None:
    patch = ENTRY.client.exe_patch
    assert patch is not None
    assert patch.expect_size == STOCK_SIZE
    assert patch.expect_sha256 == (
        "aa63a5750d60ef16746c686b3d5e26876d98953eab08b1c026cd0faf78e88cb8"
    )
    assert [(s.url, s.member) for s in patch.clean_sources] == [
        ("https://wow.baerthe.com/WoW-Client-3.3.5a.zip", "WoW-3.3.5a/Wow.exe")
    ]
    assert patch.fallback_page == "https://chromiecraft.com/en/downloads/"
    assert patch.build == 12342
    assert patch.pe_large_address_aware is True
    assert list(patch.options) == ["borderless"]
    # Off by default, unlike the launcher's (schemas.ts:56): the lead's ruling, because the
    # borderless path blanks other monitors and grabs the mouse under Wine or Proton, and
    # Linux is a shipped platform. The option stays for a player who wants it.
    assert patch.options["borderless"].default is False


def test_the_exe_patch_writes_exactly_what_centurions_launcher_writes_both_ways() -> None:
    patch = ENTRY.client.exe_patch
    assert patch is not None
    stock = _stand_in()
    for borderless in (True, False):
        ours = client_exe.patched(stock, patch, {"borderless": borderless})
        theirs = _centurion_launcher(stock, borderless=borderless)
        assert hashlib.sha256(ours).hexdigest() == hashlib.sha256(theirs).hexdigest(), borderless
    assert client_exe.patched(stock, patch, {}) == _centurion_launcher(stock, borderless=False)


def test_the_hosts_a_download_may_reach_are_centurions_site_and_the_clean_client() -> None:
    assert ENTRY.client.hosts() == frozenset({"centurionpvp.com", "wow.baerthe.com"})


# -- the shipped entry is the one the family was tested with ---------------------------------


def _dump(value: Any) -> Any:
    """A model, a tuple of models or a plain value, as JSON-shaped data to compare."""
    return TypeAdapter(type(value)).dump_python(value, mode="json")


def test_the_family_blocks_the_templates_were_tested_with_are_the_shipped_ones() -> None:
    """Tasks 2-6 tested the templates and the engine on `support_trinitycore.centurion_like()`.

    Where the shipped entry differs from it, it differs on purpose: the extraction also
    takes the cameras, the confs carry the live realm's settings, and the client folder must
    be enUS. Everything else is the same, so a test of the fixture is a test of this entry.
    """
    fixture = centurion_like()
    f_native, s_native = fixture.install.native, ENTRY.install.native
    assert f_native is not None and s_native is not None
    f_tc, s_tc = f_native.trinitycore, s_native.trinitycore
    assert f_tc is not None and s_tc is not None
    for name in ("checkout", "sparse_exclude", "dockerfile", "sql", "required_maps", "updates"):
        assert _dump(getattr(s_tc, name)) == _dump(getattr(f_tc, name)), name
    assert _dump(s_tc.extract.client_archives) == _dump(f_tc.extract.client_archives)
    assert s_tc.extract.dbc_overlay_from == f_tc.extract.dbc_overlay_from
    assert [tool.name for tool in s_tc.extract.tools][1:] == [
        tool.name for tool in f_tc.extract.tools
    ][1:]
    for conf, patch in f_tc.conf.files.items():
        assert patch.keys.items() <= s_tc.conf.files[conf].keys.items(), conf
    for name in ("family", "templates", "dockerfile_dir", "image_prefix", "images", "db"):
        assert _dump(getattr(s_native, name)) == _dump(getattr(f_native, name)), name
    for name in ("containers", "databases", "realmlist", "accounts", "console", "play"):
        assert _dump(getattr(ENTRY, name)) == _dump(getattr(fixture, name)), name
    assert ENTRY.has_manifests is fixture.has_manifests is False
    assert ENTRY.observability is not None and fixture.observability is not None
    assert _dump(ENTRY.observability.bots) == _dump(fixture.observability.bots)
    assert ENTRY.operations is not None and fixture.operations is not None
    for name in ("channel", "namespace", "port", "enable_conf", "must_not_listen", "publish"):
        assert _dump(getattr(ENTRY.operations, name)) == _dump(
            getattr(fixture.operations, name)
        ), name
    assert ENTRY.install.password == fixture.install.password
    assert ENTRY.install.default_server_dir == fixture.install.default_server_dir
