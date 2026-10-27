"""The Conquest of Azeroth catalog model (T812 C1): what loads, and what is refused.

C1 is the model only: the new `AcRealmType`/`AcSurgicalMap`/`AcClientExtract`
classes, the five fields they add to `AzerothCoreData`, the `client_extract`
arm of `NativeInstall._exactly_the_family_block`, the two `CatalogEntry`
cross-checks, and `Client.exe_names`/`wine_dll_overrides`. No entry, no
behaviour. Each refusal test changes ONE value of a fixture that otherwise
loads, so a red names exactly the rule that let it through.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest
from pydantic import ValidationError

from yulon.catalog.catalog import (
    CATALOG_FILE,
    AcClientExtract,
    AcRealmType,
    AcSurgicalMap,
    AzerothCoreData,
    Client,
    NativeInstall,
    parse_catalog,
)

REALM: dict[str, Any] = {
    "id": "coa",
    "label": "CoA classes",
    "description": "The Conquest of Azeroth class model.",
    "realm_name": "Conquest of Azeroth",
    "world_env": {"AC_CO_A_CLASS_MODEL": "coa"},
}

SURGICAL: dict[str, Any] = {
    "map_id": 936,
    "archive": "Data/patch-WB1.MPQ.DISABLED",
    "link_as": "Data/patch-WB1.MPQ",
    "extra_vmaps": ["Ns_Inquisitorial_Dungeon.wmo.vmo"],
    "min_maps": 9,
}

EXTRACT: dict[str, Any] = {
    "client": {
        "required_file": "Data/patch-WB2.MPQ",
        "min_mpq": 40,
        "locale_mpq_required": True,
        "locales": ["enUS"],
    },
    "extract": {
        "image": "tools",
        "tools": [
            {
                "name": "maps",
                "argv": ["/azerothcore/env/dist/bin/map_extractor", "-i", "/client", "-o", "/out"],
                "produces": {"maps": 9000},
            }
        ],
    },
    "mmaps": {"argv": ["/azerothcore/env/dist/bin/mmaps_generator", "--silent"]},
    "mmaps_config": "src/tools/mmaps_generator/mmaps-config.yaml",
    "required_maps": [0, 1, 530, 571, 936, 937],
    "surgical_maps": [SURGICAL],
}

NATIVE: dict[str, Any] = {
    "family": "azerothcore",
    "templates": "wow-wotlk/native",
    "images": ["worldserver", "authserver", "db-import", "tools"],
    "image_prefix": "yulon.local/ac-coa-",
    "db": {"image": "mysql:8.4", "client": "mysql", "user": "root"},
    "ready": {"world": "ready...", "auth": "{{REALM_HOST}}:{{WORLD_PORT}}"},
    "azerothcore": {
        "world_env": {"AC_AI_PLAYERBOT_MIN_RANDOM_BOTS": "150"},
        "client_extract": EXTRACT,
        "realm_types": [REALM],
        "default_realm_type": "coa",
    },
}


def _native(**changes: Any) -> dict[str, Any]:
    """`NATIVE` with dotted-path changes inside its `azerothcore` block."""
    native = copy.deepcopy(NATIVE)
    for dotted, value in changes.items():
        *parents, last = dotted.split("__")
        at = native["azerothcore"]
        for key in parents:
            at = at[key]
        at[last] = value
    return native


def _entry(native: dict[str, Any] | None = None, **top: Any) -> dict[str, Any]:
    """A whole entry around an azerothcore block with client_extract, from wow-wotlk."""
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    entry: dict[str, Any] = copy.deepcopy(
        next(game for game in data["games"] if game["id"] == "wow-wotlk")
    )
    entry["id"] = "wow-coa-shape"
    entry["name"] = "Shape"
    entry["install"]["requires_client_dir"] = True
    entry["install"]["native"] = copy.deepcopy(NATIVE) if native is None else native
    entry["containers"] = {
        "db": "ac-database",
        "auth": "ac-authserver",
        "world": "ac-worldserver",
        "db_import": "ac-db-import",
    }
    entry.update(top)
    return entry


# -- what loads -----------------------------------------------------------------


def test_a_minimal_azerothcore_block_with_client_extract_loads_and_reads_back() -> None:
    native = NativeInstall.model_validate(NATIVE)
    block = native.azerothcore
    assert isinstance(block, AzerothCoreData)
    assert isinstance(block.client_extract, AcClientExtract)
    assert isinstance(block.client_extract.surgical_maps[0], AcSurgicalMap)
    assert block.client_extract.required_maps == (0, 1, 530, 571, 936, 937)
    assert block.realm_types[0].id == "coa"
    assert block.default_realm_type == "coa"
    assert block.world_bootstrap_service is None
    assert block.auth_after_world_ready is False


def test_a_whole_entry_with_client_extract_loads() -> None:
    entry = parse_catalog({"schema_version": 1, "games": [_entry()]}).get("wow-coa-shape")
    native = entry.install.native
    assert native is not None and native.family == "azerothcore"
    assert native.azerothcore is not None and native.azerothcore.client_extract is not None


def test_client_defaults_keep_every_shipped_entry_loading() -> None:
    assert Client(version="3.3.5a", build=12340).exe_names == ("wow.exe",)
    assert Client(version="3.3.5a", build=12340).wine_dll_overrides is None


# -- realm types ----------------------------------------------------------------


def test_realm_type_ids_must_be_unique() -> None:
    with pytest.raises(ValidationError, match="realm type ids must be unique"):
        AzerothCoreData.model_validate(
            {"realm_types": [REALM, REALM], "default_realm_type": "coa"}
        )


def test_a_default_realm_type_that_is_not_listed_is_refused() -> None:
    with pytest.raises(ValidationError, match="not one of this.*realm types"):
        AzerothCoreData.model_validate({"realm_types": [REALM], "default_realm_type": "hero"})


def test_a_default_realm_type_without_realm_types_is_refused() -> None:
    with pytest.raises(ValidationError, match="names no realm type"):
        AzerothCoreData.model_validate({"default_realm_type": "coa"})


def test_realm_types_without_a_default_are_refused() -> None:
    with pytest.raises(ValidationError, match="names its default realm type"):
        AzerothCoreData.model_validate({"realm_types": [REALM]})


def test_a_realm_type_repeating_a_world_env_key_is_refused() -> None:
    with pytest.raises(ValidationError, match="one owner"):
        AzerothCoreData.model_validate(
            {
                "world_env": {"AC_AI_PLAYERBOT_MIN_RANDOM_BOTS": "150"},
                "realm_types": [
                    {**REALM, "world_env": {"AC_AI_PLAYERBOT_MIN_RANDOM_BOTS": "1"}}
                ],
                "default_realm_type": "coa",
            }
        )


@pytest.mark.parametrize("realm_id", ["Coa", "c", "coa_classes!", "12345678901", "-coa"])
def test_a_bad_realm_type_id_is_refused(realm_id: str) -> None:
    with pytest.raises(ValidationError, match="String should match pattern"):
        AcRealmType.model_validate({**REALM, "id": realm_id})


@pytest.mark.parametrize("name", ["Conquest's Azeroth", 'Conquest "Azeroth"', "Conquest\\Azeroth"])
def test_a_quote_or_backslash_in_a_realm_name_is_refused(name: str) -> None:
    with pytest.raises(ValidationError, match="quote or backslash"):
        AcRealmType.model_validate({**REALM, "realm_name": name})


def test_a_realm_name_beyond_32_chars_is_refused() -> None:
    with pytest.raises(ValidationError, match="realm_name"):
        AcRealmType.model_validate({**REALM, "realm_name": "C" * 33})


def test_a_realm_world_env_key_outside_the_ac_shape_is_refused() -> None:
    with pytest.raises(ValidationError, match="world_env"):
        AcRealmType.model_validate({**REALM, "world_env": {"COA_CLASS_MODEL": "coa"}})


# -- surgical maps --------------------------------------------------------------


def test_a_surgical_map_loads_and_reads_back() -> None:
    surgical = AcSurgicalMap.model_validate(SURGICAL)
    assert surgical.map_id == 936
    assert surgical.min_maps == 9


def test_link_as_outside_data_is_refused() -> None:
    with pytest.raises(ValidationError, match="link_as.*inside the client's Data"):
        AcSurgicalMap.model_validate({**SURGICAL, "link_as": "../patch-WB1.MPQ"})


def test_an_archive_outside_data_is_refused() -> None:
    with pytest.raises(ValidationError, match="archive.*inside the client's Data"):
        AcSurgicalMap.model_validate({**SURGICAL, "archive": "/client/patch-WB1.MPQ.DISABLED"})


def test_a_link_as_that_is_not_an_archive_is_refused() -> None:
    with pytest.raises(ValidationError, match="link_as names the archive"):
        AcSurgicalMap.model_validate({**SURGICAL, "link_as": "Data/patch-WB1.DBC"})


def test_a_vmo_with_a_directory_is_refused() -> None:
    with pytest.raises(ValidationError, match="bare file names"):
        AcSurgicalMap.model_validate({**SURGICAL, "extra_vmaps": ["vmaps/Ns.vmo"]})


def test_a_surgical_map_with_no_maps_is_refused() -> None:
    with pytest.raises(ValidationError, match="min_maps"):
        AcSurgicalMap.model_validate({**SURGICAL, "min_maps": 0})


def test_a_mmaps_config_outside_the_checkout_is_refused() -> None:
    with pytest.raises(ValidationError, match="mmaps_config must be a relative POSIX path"):
        AcClientExtract.model_validate({**EXTRACT, "mmaps_config": "../mmaps-config.yaml"})


def test_client_extract_with_a_client_data_service_is_refused() -> None:
    entry = _entry()
    entry["containers"] = {
        "db": "ac-database",
        "auth": "ac-authserver",
        "world": "ac-worldserver",
        "db_import": "ac-db-import",
        "client_data": "ac-client-data-init",
    }
    with pytest.raises(ValidationError, match="containers.client_data must be absent"):
        parse_catalog({"schema_version": 1, "games": [entry]})


def test_an_extract_image_that_is_not_built_is_refused() -> None:
    native = _native()
    native["azerothcore"]["client_extract"]["extract"]["image"] = "client-data"
    with pytest.raises(ValidationError, match="azerothcore.extract.image 'client-data'"):
        NativeInstall.model_validate(native)



def test_client_extract_without_requires_client_dir_is_refused() -> None:
    native = _native()
    entry = _entry(native)
    entry["install"] = {**entry["install"], "requires_client_dir": False}
    with pytest.raises(ValidationError, match="requires_client_dir must be true"):
        parse_catalog({"schema_version": 1, "games": [entry]})


def test_auth_after_world_without_an_auth_marker_is_refused() -> None:
    native = _native(auth_after_world_ready=True)
    native["ready"] = {"world": "ready..."}
    with pytest.raises(ValidationError, match="ready.auth must be set"):
        parse_catalog({"schema_version": 1, "games": [_entry(native)]})


# -- the client the player brings -----------------------------------------------


def test_an_exe_name_in_upper_case_is_refused() -> None:
    with pytest.raises(ValidationError, match="exe_names are lower case"):
        Client(version="3.3.5a", build=12340, exe_names=["Ascension.exe"])


def test_a_repeated_exe_name_is_refused() -> None:
    with pytest.raises(ValidationError, match="exe_names must be distinct"):
        Client(
            version="3.3.5a",
            build=12340,
            exe_names=["ascension.exe", "ascension.exe"],
        )


def test_a_quote_in_wine_dll_overrides_is_refused() -> None:
    with pytest.raises(ValidationError, match="wine_dll_overrides"):
        Client(version="3.3.5a", build=12340, wine_dll_overrides='divxtac="d"')


def test_an_ascension_client_loads() -> None:
    client = Client(
        version="3.3.5a",
        build=12340,
        exe_names=["ascension.exe"],
        wine_dll_overrides="divxtac=d",
    )
    assert client.exe_names == ("ascension.exe",)
