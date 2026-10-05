"""The `trinitycore` family's catalog block (T179 Task 1): what loads, and what is refused.

Centurion (thomasjteachey/TrinityCore112, branch CENTURION) is the first tree of
this family. The facts each field describes are in
`.notes/tickets/T179-centurion-facts.md` (read at faac5fc9); the values below
are a minimal block shaped like that tree, not the shipped entry, which lands
in Task 7. Each refusal test changes ONE value of a block that otherwise loads,
so a red here names exactly the rule that let it through.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from yulon.catalog.catalog import (
    CATALOG_FILE,
    Accounts,
    NativeInstall,
    TrinityCoreData,
    load_catalog,
    parse_catalog,
)
from yulon.catalog.families import family_for
from yulon.catalog.families.trinitycore import TrinityCoreInstaller

SQL: dict[str, Any] = {
    "create": ["c_auth", "c_characters", "c_world"],
    "phases": [
        {
            "name": "auth schema",
            "into": "c_auth",
            "files": ["src/core/centurion/sql/auth/auth_schema.sql"],
        },
        {
            "name": "world routines",
            "into": "c_world",
            "files": ["src/core/centurion/sql/world/_routines.sql"],
        },
        {
            "name": "world tables",
            "into": "c_world",
            "files": ["src/core/centurion/sql/world/*.sql"],
        },
    ],
    "marker_db": "c_auth",
    "renames": [["legionnaireauth", "c_auth"], ["centurionworld", "c_world"]],
    "rename_files": [
        "src/core/centurion/sql/auth/auth_schema.sql",
        "src/core/centurion/sql/world/_routines.sql",
    ],
}

TRINITYCORE: dict[str, Any] = {
    "checkout": "src/core",
    "client": {"required_file": "Data/lichking.MPQ", "min_mpq": 6},
    "sparse_exclude": ["playerbot reference", "centurion/launcher"],
    "dockerfile": {
        "make_jobs": 2,
        "cmake_options": ["-DPLAYERBOT=ON", "-DTOOLS=ON", "-DSCRIPTS=static"],
    },
    "extract": {
        "image": "server",
        "tools": [
            {
                "name": "maps",
                "argv": ["/opt/trinitycore/bin/mapextractor", "-i", "/client", "-o", "/out"],
                "produces": {"maps": 100},
            }
        ],
        "dbc_overlay_from": "centurion/dbc",
        "client_archives": ["common.MPQ", "{locale}/locale-{locale}.MPQ"],
    },
    "mmaps": {"argv": ["/opt/trinitycore/bin/mmaps_generator"], "background": True},
    "conf": {
        "source_dir": "/opt/trinitycore/etc",
        "files": {
            "worldserver.conf": {
                "keys": {"Updates.EnableDatabases": "0", "DataDir": '"/opt/trinitycore/data"'}
            },
            "authserver.conf": {"keys": {"LoginDatabaseInfo": '"db;3306;root;x;c_auth"'}},
            "playerbots.conf": {"keys": {"Playerbot.Enable": "1"}},
        },
        "playerbots_conf": "playerbots.conf",
    },
    "sql": SQL,
    "required_maps": [0, 1, 530],
}

NATIVE: dict[str, Any] = {
    "family": "trinitycore",
    "templates": "shared/trinitycore",
    "dockerfile_dir": "wow-example/native",
    "image_prefix": "yulon.local/trinitycore-example-",
    "images": ["server"],
    "db": {"image": "mysql:8.4", "client": "mysql", "user": "root"},
    "ready": {"world": "World initialized"},
    "trinitycore": TRINITYCORE,
}


def _native(**changes: Any) -> dict[str, Any]:
    """`NATIVE` with dotted-path changes inside its `trinitycore` block, e.g. `extract.image`."""
    native = copy.deepcopy(NATIVE)
    for dotted, value in changes.items():
        *parents, last = dotted.split("__")
        at = native["trinitycore"]
        for key in parents:
            at = at[key]
        at[last] = value
    return native


def _entry(native: dict[str, Any] | None = None, **top: Any) -> dict[str, Any]:
    """A whole catalog entry around a trinitycore block, built from a shipped entry."""
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    entry: dict[str, Any] = copy.deepcopy(
        next(game for game in data["games"] if game["id"] == "wow-tbc")
    )
    entry["id"] = "wow-example"
    entry["name"] = "Example"
    entry["emulator"] = {
        "name": "An example TrinityCore fork",
        "sources": [{"repo": "example/TrinityCore", "dest": "src/core", "branch": "EXAMPLE"}],
    }
    entry["databases"] = {"auth": "c_auth", "characters": "c_characters", "world": "c_world"}
    entry["accounts"] = {"scheme": "trinitycore"}
    entry["install"]["native"] = copy.deepcopy(NATIVE) if native is None else native
    entry.update(top)
    return entry


# -- what loads -----------------------------------------------------------------


def test_a_minimal_trinitycore_block_loads_and_reads_back() -> None:
    native = NativeInstall.model_validate(NATIVE)
    block = native.trinitycore
    assert isinstance(block, TrinityCoreData)
    assert native.family == "trinitycore"
    assert native.cmangos is None and native.azerothcore is None
    assert block.checkout == "src/core"
    assert block.client.required_file == "Data/lichking.MPQ"
    assert block.sparse_exclude == ("playerbot reference", "centurion/launcher")
    assert block.dockerfile.cmake_options == ("-DPLAYERBOT=ON", "-DTOOLS=ON", "-DSCRIPTS=static")
    assert block.dockerfile.make_jobs == 2
    assert block.extract.dbc_overlay_from == "centurion/dbc"
    assert block.mmaps.background is True
    assert block.conf.playerbots_conf == "playerbots.conf"
    assert block.sql.renames == (("legionnaireauth", "c_auth"), ("centurionworld", "c_world"))
    assert block.sql.rename_files == tuple(SQL["rename_files"])
    assert block.required_maps == (0, 1, 530)


def test_the_mmaps_run_in_the_background_unless_the_entry_says_otherwise() -> None:
    """Owner decision 4 (spec): mmaps after the server is up, in the background."""
    del_background = _native()
    del del_background["trinitycore"]["mmaps"]["background"]
    block = NativeInstall.model_validate(del_background).trinitycore
    assert block is not None and block.mmaps.background is True


def test_a_whole_entry_with_a_trinitycore_block_loads() -> None:
    catalog = parse_catalog({"schema_version": 1, "games": [_entry()]})
    entry = catalog.get("wow-example")
    assert entry.install.native is not None
    assert entry.install.native.family == "trinitycore"
    assert entry.accounts.scheme == "trinitycore"


def test_trinitycore_is_a_family_and_an_account_scheme_the_models_name() -> None:
    assert "trinitycore" in get_args(NativeInstall.model_fields["family"].annotation)
    assert Accounts(scheme="trinitycore").scheme == "trinitycore"


def test_the_shipped_catalog_has_one_trinitycore_entry_and_it_is_centurion() -> None:
    """Task 1 was the model only; the real `wow-centurion` entry landed in Task 7."""
    catalog = load_catalog()
    assert [entry.id for entry in catalog.games] == [
        "wow-wotlk",
        "wow-tbc",
        "wow-vanilla",
        "wow-tortoise",
        "wow-centurion",
    ]
    for entry in catalog.games:
        native = entry.install.native
        assert native is not None
        is_tc = entry.id == "wow-centurion"
        assert (native.family == "trinitycore") is is_tc, entry.id
        assert (native.trinitycore is not None) is is_tc, entry.id
        assert (entry.accounts.scheme == "trinitycore") is is_tc, entry.id


def test_an_entry_of_the_family_dispatches_to_the_trinitycore_engine() -> None:
    """Task 3 registered the engine: a trinitycore entry gets it, never another family's.

    Until Task 3 this test said the opposite -- dispatch refused the entry -- and
    Task 1 named it as the one to turn around when `FAMILIES` gained the family.
    """
    entry = parse_catalog({"schema_version": 1, "games": [_entry()]}).get("wow-example")
    assert family_for(entry) is TrinityCoreInstaller
    assert TrinityCoreInstaller.family == "trinitycore"


# -- the family names exactly its block -------------------------------------------


def test_a_trinitycore_family_without_its_block_is_refused() -> None:
    with pytest.raises(ValidationError, match="exactly the `trinitycore` block"):
        NativeInstall.model_validate({**NATIVE, "trinitycore": None})


def test_a_trinitycore_block_on_another_family_is_refused() -> None:
    with pytest.raises(
        ValidationError, match="blocks present are \\['azerothcore', 'trinitycore'\\]"
    ):
        NativeInstall.model_validate(
            {**NATIVE, "family": "azerothcore", "azerothcore": {"world_env": {}}}
        )


def test_the_extract_image_must_be_one_the_build_makes() -> None:
    with pytest.raises(ValidationError, match="trinitycore.extract.image 'tools'"):
        NativeInstall.model_validate(_native(extract__image="tools"))


# -- one refusal per rule ------------------------------------------------------


@pytest.mark.parametrize("path", ["/playerbot reference", "../elsewhere", "a/../../b", "a\\b"])
def test_a_sparse_exclusion_outside_the_checkout_is_refused(path: str) -> None:
    with pytest.raises(ValidationError, match="sparse_exclude must be a relative POSIX path"):
        NativeInstall.model_validate(_native(sparse_exclude=[path]))


@pytest.mark.parametrize("path", ["!keep", "#note", "tools/*", "a?b", "[ab]"])
def test_a_sparse_exclusion_holding_a_pattern_character_is_refused(path: str) -> None:
    """Git reads these as sparse-checkout pattern syntax, so they are refused, not escaped.

    A space stays allowed: `playerbot reference` is the very path Centurion
    leaves out.
    """
    with pytest.raises(ValidationError, match="sparse_exclude holds a git pattern character"):
        NativeInstall.model_validate(_native(sparse_exclude=[path]))


@pytest.mark.parametrize("path", ["/centurion/dbc", "../dbc", "centurion/../../dbc", "."])
def test_a_dbc_overlay_outside_the_checkout_is_refused(path: str) -> None:
    with pytest.raises(ValidationError, match="dbc_overlay_from must be a relative POSIX path"):
        NativeInstall.model_validate(_native(extract__dbc_overlay_from=path))


@pytest.mark.parametrize("checkout", ["/src/core", "../core", "src\\core", "."])
def test_a_checkout_outside_the_server_dir_is_refused(checkout: str) -> None:
    """`.` is the server dir itself: `!.` in the .dockerignore re-includes all of it."""
    with pytest.raises(ValidationError, match="checkout must be a relative POSIX path"):
        NativeInstall.model_validate(_native(checkout=checkout))


def test_an_empty_checkout_is_refused() -> None:
    with pytest.raises(ValidationError, match="at least 1 character"):
        NativeInstall.model_validate(_native(checkout=""))


@pytest.mark.parametrize(
    "checkout", ["src/core*", "src/c?re", "src/[core", "src/core]", "!src/core", 'src/"core']
)
def test_a_checkout_the_build_files_read_as_syntax_is_refused(checkout: str) -> None:
    """Spliced into `!{{CHECKOUT}}` and `COPY ["{{CHECKOUT}}", ...]` (Task 2's templates)."""
    with pytest.raises(ValidationError, match="reads as syntax; name a plain folder"):
        NativeInstall.model_validate(_native(checkout=checkout))


def test_a_checkout_starting_like_a_comment_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"holds \['#'\]"):
        NativeInstall.model_validate(_native(checkout="#core"))


def test_a_checkout_with_a_space_is_a_plain_folder() -> None:
    """A space is not syntax in either file; Centurion's own excluded path has one."""
    block = NativeInstall.model_validate(_native(checkout="src/my core")).trinitycore
    assert block is not None and block.checkout == "src/my core"


def test_the_client_rules_are_required() -> None:
    native = _native()
    del native["trinitycore"]["client"]
    with pytest.raises(ValidationError, match="trinitycore.client\n  Field required"):
        NativeInstall.model_validate(native)


@pytest.mark.parametrize(
    "pair",
    [
        ["legionnaire-auth", "c_auth"],
        ["legionnaireauth", "c_auth; DROP"],
        ["", "c_auth"],
        ["centurionworld", "c`world"],
    ],
)
def test_a_rename_whose_names_are_not_plain_identifiers_is_refused(pair: list[str]) -> None:
    """import.sh refuses any DB name outside `^[A-Za-z0-9_]+$` (facts §2, import.sh:21-26).

    The names are spliced into SQL text by a substitution, so anything else is
    an injection, not a name.
    """
    with pytest.raises(ValidationError, match="a rename names databases matching"):
        NativeInstall.model_validate(_native(sql__renames=[pair]))


def test_a_rename_file_outside_the_server_dir_is_refused() -> None:
    with pytest.raises(ValidationError, match="rename_files must be a relative POSIX path"):
        NativeInstall.model_validate(_native(sql__rename_files=["../auth_schema.sql"]))


def test_a_rename_file_no_phase_imports_is_refused() -> None:
    """Renames apply to listed files only; a listed file nothing imports is dead text."""
    with pytest.raises(ValidationError, match="no phase imports"):
        NativeInstall.model_validate(
            _native(sql__rename_files=["src/core/centurion/sql/characters/characters_schema.sql"])
        )


@pytest.mark.parametrize("value", ["7", "1", " 7 "])
def test_the_database_updater_must_be_off_when_the_table_names_it(value: str) -> None:
    """`Updates.EnableDatabases` ships 7 and must be 0 (facts §2, README.md:213-214)."""
    files = copy.deepcopy(TRINITYCORE["conf"]["files"])
    files["worldserver.conf"]["keys"]["Updates.EnableDatabases"] = value
    with pytest.raises(ValidationError, match="Updates.EnableDatabases must be 0"):
        NativeInstall.model_validate(_native(conf__files=files))


def test_the_world_conf_must_switch_the_updater_off() -> None:
    """Absent is not off: the world conf's shipped value is 7 (worldserver.conf.dist:1470).

    So the table that writes the world conf must say 0 itself; leaving the key
    out leaves the `.dist`'s 7, and the worldserver replays stock updates over
    the snapshot or shuts down (facts §2).
    """
    files = copy.deepcopy(TRINITYCORE["conf"]["files"])
    del files["worldserver.conf"]["keys"]["Updates.EnableDatabases"]
    with pytest.raises(ValidationError, match="must set Updates.EnableDatabases to 0"):
        NativeInstall.model_validate(_native(conf__files=files))


def test_the_world_conf_is_the_one_the_table_names() -> None:
    with pytest.raises(ValidationError, match="world_conf 'world.conf' is not one of"):
        NativeInstall.model_validate(_native(conf__world_conf="world.conf"))


def test_the_auth_conf_may_leave_the_updater_out() -> None:
    """authserver.conf.dist:258 already ships 0 (facts §4), so only a value there is checked."""
    files = copy.deepcopy(TRINITYCORE["conf"]["files"])
    assert "Updates.EnableDatabases" not in files["authserver.conf"]["keys"]
    block = NativeInstall.model_validate(_native(conf__files=files)).trinitycore
    assert block is not None and block.conf.world_conf == "worldserver.conf"
    files["authserver.conf"]["keys"]["Updates.EnableDatabases"] = "7"
    with pytest.raises(ValidationError, match="authserver.conf: Updates.EnableDatabases must be 0"):
        NativeInstall.model_validate(_native(conf__files=files))


@pytest.mark.parametrize("name", ["modules/playerbots.conf", "../playerbots.conf", ""])
def test_the_playerbots_conf_must_be_a_bare_file_name(name: str) -> None:
    """It is read only beside worldserver.conf (facts §4, worldserver/Main.cpp:242-250)."""
    with pytest.raises(ValidationError, match="playerbots_conf is a file name beside"):
        NativeInstall.model_validate(_native(conf__playerbots_conf=name))


def test_the_playerbots_conf_must_be_a_file_the_table_writes() -> None:
    with pytest.raises(ValidationError, match="not one of the conf table's files"):
        NativeInstall.model_validate(_native(conf__playerbots_conf="aiplayerbot.conf"))


def test_a_conf_copied_from_the_checkout_reads_back() -> None:
    """T179 Task 8: Centurion's AutoBalance.conf, the live realm's, copied whole."""
    block = NativeInstall.model_validate(
        _native(conf__from_checkout={"AutoBalance.conf": "centurion/conf/AutoBalance.conf"})
    ).trinitycore
    assert block is not None
    assert block.conf.from_checkout == {"AutoBalance.conf": "centurion/conf/AutoBalance.conf"}
    bare = copy.deepcopy(NATIVE)
    bare["trinitycore"]["conf"].pop("from_checkout", None)
    plain = NativeInstall.model_validate(bare).trinitycore
    assert plain is not None and plain.conf.from_checkout == {}, "none by default"


@pytest.mark.parametrize("name", ["conf/AutoBalance.conf", "..", ""])
def test_a_conf_copied_from_the_checkout_lands_beside_worldserver_conf(name: str) -> None:
    with pytest.raises(ValidationError, match="from_checkout names a file beside"):
        NativeInstall.model_validate(_native(conf__from_checkout={name: "centurion/conf/x.conf"}))


@pytest.mark.parametrize("source", ["/etc/AutoBalance.conf", "../AutoBalance.conf", "a\\b", ""])
def test_a_conf_copied_from_the_checkout_comes_from_inside_it(source: str) -> None:
    with pytest.raises(ValidationError, match="from_checkout must be a relative POSIX path"):
        NativeInstall.model_validate(_native(conf__from_checkout={"AutoBalance.conf": source}))


def test_a_conf_is_either_copied_from_the_checkout_or_patched_never_both() -> None:
    with pytest.raises(ValidationError, match="one file has one source"):
        NativeInstall.model_validate(
            _native(conf__from_checkout={"playerbots.conf": "centurion/conf/playerbots.conf"})
        )


def test_the_shipped_centurion_places_the_live_autobalance_conf() -> None:
    """README.md:203-204: copy the live realm's centurion/conf/AutoBalance.conf."""
    native = load_catalog().get("wow-centurion").install.native
    assert native is not None and native.trinitycore is not None
    assert native.trinitycore.conf.from_checkout == {
        "AutoBalance.conf": "centurion/conf/AutoBalance.conf"
    }


@pytest.mark.parametrize(
    "option", ["PLAYERBOT=ON", "-DPLAYERBOT", "-DPLAYERBOT=ON && rm -rf /", "-DX=$(id)"]
)
def test_a_cmake_option_that_is_not_one_define_is_refused(option: str) -> None:
    with pytest.raises(ValidationError, match="cmake_options must each be one"):
        NativeInstall.model_validate(_native(dockerfile__cmake_options=[option]))


def test_the_start_check_needs_at_least_one_map() -> None:
    with pytest.raises(ValidationError, match="required_maps\n  Tuple should have at least 1"):
        NativeInstall.model_validate(_native(required_maps=[]))


def test_a_negative_map_id_is_refused() -> None:
    with pytest.raises(ValidationError, match="required_maps.1\n  Input should be greater than"):
        NativeInstall.model_validate(_native(required_maps=[0, -1]))


# -- what the entry around the block must agree with ------------------------------


def test_the_checkout_must_be_a_source_the_entry_clones() -> None:
    native = _native(checkout="src/other")
    with pytest.raises(ValidationError, match="'src/other', which is not a dest"):
        parse_catalog({"schema_version": 1, "games": [_entry(native)]})


@pytest.mark.parametrize("scheme", ["azerothcore", "mangos_srp6", "mangos_sha"])
def test_a_trinitycore_entry_cannot_take_another_cores_account_scheme(scheme: str) -> None:
    """`Accounts.scheme` defaults to azerothcore, which a Centurion entry must not inherit.

    AzerothCore's `account_access(id, gmlevel)` is not TrinityCore's
    `account_access(AccountID, SecurityLevel)` (facts §5): an entry that forgot
    its `accounts` block would write rows that look right and grant nothing.
    """
    with pytest.raises(ValidationError, match="a trinitycore entry's accounts.scheme"):
        parse_catalog({"schema_version": 1, "games": [_entry(accounts={"scheme": scheme})]})


def test_a_trinitycore_entry_may_declare_no_scheme() -> None:
    entry = parse_catalog({"schema_version": 1, "games": [_entry(accounts={"scheme": None})]})
    assert entry.get("wow-example").accounts.scheme is None


def test_a_rename_must_land_on_one_of_the_entrys_own_schemas() -> None:
    native = _native(sql__renames=[["legionnaireauth", "auth"]])
    with pytest.raises(ValidationError, match="'auth', which is not one of this entry's"):
        parse_catalog({"schema_version": 1, "games": [_entry(native)]})


def test_the_dbc_overlay_lands_in_data_dbc_unless_the_entry_says_otherwise() -> None:
    """TrinityCore reads `<DataDir>/dbc/` (README.md:174-180, facts §3)."""
    block = NativeInstall.model_validate(NATIVE).trinitycore
    assert block is not None and block.extract.dbc_overlay_to == "dbc"


@pytest.mark.parametrize("path", ["/dbc", "../dbc", "dbc/../../x", ".", "a\\b"])
def test_a_dbc_overlay_target_outside_the_data_folder_is_refused(path: str) -> None:
    with pytest.raises(ValidationError, match="dbc_overlay_to must be a relative POSIX path"):
        NativeInstall.model_validate(_native(extract__dbc_overlay_to=path))


def test_the_extraction_client_keeps_only_the_archives_the_entry_names() -> None:
    block = NativeInstall.model_validate(NATIVE).trinitycore
    assert block is not None
    assert block.extract.client_archives == ("common.MPQ", "{locale}/locale-{locale}.MPQ")
    native = _native()
    del native["trinitycore"]["extract"]["client_archives"]
    with pytest.raises(ValidationError, match="client_archives\n  Field required"):
        NativeInstall.model_validate(native)


@pytest.mark.parametrize(
    ("name", "rule"),
    [
        ("../common.MPQ", "must be a relative POSIX path"),
        ("/Data/common.MPQ", "must be a relative POSIX path"),
        ("Wow.exe", "names game archives"),
        ("patch-*.MPQ", "plain names and the `{locale}` token only"),
        ("{lang}/locale.MPQ", "plain names and the `{locale}` token only"),
    ],
)
def test_a_client_archive_that_is_not_a_plain_archive_name_is_refused(name: str, rule: str) -> None:
    with pytest.raises(ValidationError, match=re.escape(rule)):
        NativeInstall.model_validate(_native(extract__client_archives=[name]))


# -- T209: the tile header a resume checks, and the retry's threads ------------------------


def test_centurions_tile_header_is_the_pinned_structs() -> None:
    """`struct MmapTileHeader` at CENTURION faac5fc9 (MapDefines.h:24-41), as the tests spell it
    from the C source: magic first, `size` the fourth uint32, 20 bytes in all."""
    from tests.support_trinitycore import MMAP_MAGIC, TILE_HEADER, mmtile
    from yulon.catalog.catalog import load_catalog

    block = load_catalog().get("wow-centurion").install.native.trinitycore  # type: ignore[union-attr]
    assert block is not None
    header = block.mmaps.tile_header
    assert header is not None
    assert (header.length, header.magic) == (TILE_HEADER.size, MMAP_MAGIC)
    tile = mmtile(1234)
    assert int.from_bytes(tile[header.size_offset : header.size_offset + 4], "little") == 1234


def test_without_a_tile_header_nothing_is_ever_kept() -> None:
    block = NativeInstall.model_validate(_native()).trinitycore
    assert block is not None
    assert block.mmaps.tile_header is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tile_header", {"length": 20, "magic": 0x4D4D4150, "size_offset": 17}),
        ("tile_header", {"length": 20, "magic": 1 << 32, "size_offset": 12}),
        ("tile_header", {"length": 20, "magic": 0x4D4D4150, "size_offset": 2}),
        ("retry_threads", 1),
    ],
    ids=(
        "size-past-the-header",
        "magic-past-uint32",
        "size-over-the-magic",
        "retry-threads-is-gone",
    ),
)
def test_the_tile_header_refuses_nonsense_and_retry_threads_is_no_longer_a_field(
    field: str, value: object
) -> None:
    raw = _native()
    raw["trinitycore"]["mmaps"][field] = value
    with pytest.raises(ValidationError):
        NativeInstall.model_validate(raw)


# -- T219: the folders a Windows world server reads from its volume ---------------------------


def test_no_world_data_folders_is_the_default_and_keeps_the_bind() -> None:
    assert NativeInstall.model_validate(_native()).trinitycore.world_data_dirs == ()  # type: ignore[union-attr]


def test_centurion_copies_the_five_folders_its_world_server_opens() -> None:
    """Read off the pin's source in T219 Task 0 (`GetDataPath()` + each name); never
    `Buildings`, the extractor's own, and never `.yulon-previous` (T241)."""
    block = load_catalog().get("wow-centurion").install.native.trinitycore  # type: ignore[union-attr]
    assert block.world_data_dirs == ("dbc", "maps", "vmaps", "mmaps", "Cameras")


def test_a_world_data_folder_named_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match=r"world_data_dirs names \['maps'\] more than once"):
        NativeInstall.model_validate(
            _native(world_data_dirs=["maps", "dbc", "maps"], world_data_gb=4)
        )


@pytest.mark.parametrize("name", ["../maps", "maps/x", ".yulon-previous", "a b", ""])
def test_a_world_data_folder_must_be_a_plain_name(name: str) -> None:
    """Spliced into the script's folder list and a `sed` pattern: letters, digits and `_`."""
    with pytest.raises(ValidationError, match="world_data_dirs"):
        NativeInstall.model_validate(_native(world_data_dirs=[name], world_data_gb=4))


def test_folders_copied_into_a_volume_must_say_the_room_they_take() -> None:
    """Preflight adds the number to Docker's disk on Windows; without it the floor is short."""
    with pytest.raises(ValidationError, match="world_data_gb must say how much room"):
        NativeInstall.model_validate(_native(world_data_dirs=["maps"]))
    block = NativeInstall.model_validate(_native(world_data_dirs=["maps"], world_data_gb=4))
    assert block.trinitycore.world_data_gb == 4  # type: ignore[union-attr]
