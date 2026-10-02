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
from yulon.catalog.installer import InstallerError

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


def test_the_shipped_catalog_loads_unchanged_and_no_entry_is_trinitycore_yet() -> None:
    """Task 1 is the model only: the real `wow-centurion` entry is Task 7's."""
    catalog = load_catalog()
    assert [entry.id for entry in catalog.games] == [
        "wow-wotlk",
        "wow-tbc",
        "wow-vanilla",
        "wow-tortoise",
    ]
    for entry in catalog.games:
        native = entry.install.native
        assert native is not None
        assert native.family != "trinitycore", entry.id
        assert native.trinitycore is None, entry.id
        assert entry.accounts.scheme != "trinitycore", entry.id


def test_an_entry_of_the_family_is_refused_until_its_engine_is_registered() -> None:
    """The model lands before the engine (Task 3): until then dispatch refuses, never falls back.

    When `FAMILIES` gains `trinitycore` this test is the one to turn around.
    """
    entry = parse_catalog({"schema_version": 1, "games": [_entry()]}).get("wow-example")
    with pytest.raises(InstallerError, match="install family this app does not have"):
        family_for(entry)


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


@pytest.mark.parametrize("path", ["/centurion/dbc", "../dbc", "centurion/../../dbc", "."])
def test_a_dbc_overlay_outside_the_checkout_is_refused(path: str) -> None:
    with pytest.raises(ValidationError, match="dbc_overlay_from must be a relative POSIX path"):
        NativeInstall.model_validate(_native(extract__dbc_overlay_from=path))


@pytest.mark.parametrize("checkout", ["/src/core", "../core", "src\\core"])
def test_a_checkout_outside_the_server_dir_is_refused(checkout: str) -> None:
    with pytest.raises(ValidationError, match="checkout must be a relative POSIX path"):
        NativeInstall.model_validate(_native(checkout=checkout))


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


def test_a_table_that_does_not_name_the_updater_is_not_refused_for_it() -> None:
    """authserver.conf ships 0 already (facts §4), so the rule binds only where it is named."""
    files = copy.deepcopy(TRINITYCORE["conf"]["files"])
    del files["worldserver.conf"]["keys"]["Updates.EnableDatabases"]
    assert NativeInstall.model_validate(_native(conf__files=files)).trinitycore is not None


@pytest.mark.parametrize("name", ["modules/playerbots.conf", "../playerbots.conf", ""])
def test_the_playerbots_conf_must_be_a_bare_file_name(name: str) -> None:
    """It is read only beside worldserver.conf (facts §4, worldserver/Main.cpp:242-250)."""
    with pytest.raises(ValidationError, match="playerbots_conf is a file name beside"):
        NativeInstall.model_validate(_native(conf__playerbots_conf=name))


def test_the_playerbots_conf_must_be_a_file_the_table_writes() -> None:
    with pytest.raises(ValidationError, match="not one of the conf table's files"):
        NativeInstall.model_validate(_native(conf__playerbots_conf="aiplayerbot.conf"))


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


def test_a_rename_must_land_on_one_of_the_entrys_own_schemas() -> None:
    native = _native(sql__renames=[["legionnaireauth", "auth"]])
    with pytest.raises(ValidationError, match="'auth', which is not one of this entry's"):
        parse_catalog({"schema_version": 1, "games": [_entry(native)]})
