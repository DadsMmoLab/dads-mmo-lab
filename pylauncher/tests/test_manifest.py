"""Tests for the manifest schema (`yulon.manifest`, roadmap 2.1).

Pins the contract rather than pydantic: a README §6-shaped manifest validates,
a feature-complete ALE manifest validates, and every refusal the schema exists
for (unknown keys, non-allow-listed repo, non-acronym ids, a keg without a
sparse path, ...) actually refuses. The checked-in JSON Schema must match what
the models generate, so the language-neutral copy can't drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from yulon import manifest
from yulon.manifest import ALLOWED_REPO_HOSTS, Manifest, parse_index, parse_manifest

MANIFESTS_DIR = Path(__file__).resolve().parents[1] / "manifests"
SCHEMA_FILE = MANIFESTS_DIR / "schema" / "manifest.schema.json"

README_EXAMPLE: dict[str, Any] = {
    "schema_version": 1,
    "id": "mod-ah-bot",
    "name": "Auction House Bot",
    "type": "module",
    "game": "wow-wotlk",
    "description": "Populates the auction house with bot-posted items.",
    "source": {"repo": "azerothcore/mod-ah-bot"},
    "build": {"rebuild": True},
    "sql": [{"db": "world", "path": "data/sql/db-world/*.sql", "applied_by": "db-import"}],
    "conf": [
        {
            "file": "env/dist/etc/modules/mod_ahbot.conf",
            "template": "conf/mod_ahbot.conf.dist",
            "keys": [
                {"key": "AuctionHouseBot.Account", "default": "{bot_account}"},
                {"key": "AuctionHouseBot.GUID", "default": "{bot_guid}"},
            ],
        }
    ],
    "prompts": [
        {"key": "bot_guid", "question": "GUID of the bot character", "kind": "int"},
        {"key": "bot_account", "question": "Account id of the bot character", "kind": "int"},
    ],
    "conflicts_with": ["mod-ah-bot-plus"],
}

ALE_EXAMPLE: dict[str, Any] = {
    "schema_version": 1,
    "id": "sitmeanrest",
    "name": "Sit Means Rest",
    "type": "ale",
    "game": "wow-wotlk",
    "source": {"repo": "Brytenwally/SitMeansRest"},
    "requires": ["mod-ale"],
    "deploy": [{"src": "SitMeansRest.lua", "dest": "env/dist/etc/modules/lua_scripts/"}],
    "patches": [
        {
            "file": "env/dist/etc/modules/lua_scripts/SitMeansRest.lua",
            "find": r"(DURATION\s*=\s*)\d+",
            "replace": r"\g<1>{duration}",
            "regex": True,
            "when": "configure",
        }
    ],
    "prompts": [
        {"key": "duration", "question": "Rest duration in seconds", "kind": "int", "default": "20"}
    ],
}

KEG_EXAMPLE: dict[str, Any] = {
    "id": "bmah",
    "name": "Black Market Auction House",
    "type": "keg",
    "game": "wow-wotlk",
    "source": {
        "repo": "DadsMmoLab/dads-mmo-lab",
        "sparse_path": "guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse",
    },
    "requires": ["mod-ale"],
    "sql": [
        {
            "db": "world",
            "path": "guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/sql/BMAH_Up.sql",
        }
    ],
    "client": [
        {
            "src": "guides/wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/Client Files/AddOns/"
            "BlackMarketUI",
            "dest": "addons",
            "name": "BlackMarketUI",
        }
    ],
    "npcs": [{"entry": 2069430, "name": "Black Market Broker"}],
}


def test_readme_example_validates() -> None:
    """The README §6-shaped module manifest is accepted and typed."""
    m = parse_manifest(README_EXAMPLE)
    assert m.id == "mod-ah-bot"
    assert m.source is not None and m.source.url == "https://github.com/azerothcore/mod-ah-bot.git"
    assert m.build.rebuild is True
    assert m.sql[0].applied_by == "db-import"
    assert m.conf[0].keys[1].key == "AuctionHouseBot.GUID"
    assert m.conflicts_with == ("mod-ah-bot-plus",)


def test_ale_and_keg_examples_validate() -> None:
    """Deploy/patch/prompt and sparse-checkout/client/npc primitives all parse."""
    ale = parse_manifest(ALE_EXAMPLE)
    assert ale.patches[0].regex is True and ale.patches[0].when == "configure"
    assert ale.prompts[0].kind == "int"
    keg = parse_manifest(KEG_EXAMPLE)
    assert keg.source is not None and keg.source.sparse_path is not None
    assert keg.build.rebuild is False  # default for non-modules
    assert keg.client[0].dest == "addons"


def test_unknown_field_is_rejected() -> None:
    """A typo'd key must fail loudly, never silently no-op."""
    bad = {**README_EXAMPLE, "build_target": ["MODULES=mod-ah-bot"]}
    with pytest.raises(ValidationError, match="build_target"):
        parse_manifest(bad)


@pytest.mark.parametrize(
    "repo",
    [
        "azerothcore/mod-ah-bot.git",
        "not a slug",
        "https://evil-warez.example/wow/mod.git",
        "https://github.com.evil.example/x/y",
        "ftp://github.com/x/y",
    ],
)
def test_repo_outside_allow_list_is_rejected(repo: str) -> None:
    """README §3a: only allow-listed forges (or a GitHub slug) pass."""
    bad = {**README_EXAMPLE, "source": {"repo": repo}}
    with pytest.raises(ValidationError, match="repo"):
        parse_manifest(bad)


@pytest.mark.parametrize("host", ALLOWED_REPO_HOSTS)
def test_repo_url_on_allowed_host_is_accepted(host: str) -> None:
    m = parse_manifest({**README_EXAMPLE, "source": {"repo": f"https://{host}/owner/name.git"}})
    assert m.source is not None and m.source.url == f"https://{host}/owner/name.git"


@pytest.mark.parametrize("field", ["id", "game"])
@pytest.mark.parametrize("value", ["Mod-AH-Bot", "world of warcraft", "-lead", "double--dash", ""])
def test_ids_must_be_lowercase_kebab_slugs(field: str, value: str) -> None:
    """Style-guide §6/§6a: acronym-only, lowercase, kebab-case ids and game ids."""
    with pytest.raises(ValidationError, match=field):
        parse_manifest({**README_EXAMPLE, field: value})


def test_module_ale_keg_require_a_source_and_keg_requires_sparse_path() -> None:
    for kind in ("module", "ale", "keg"):
        with pytest.raises(ValidationError, match="source"):
            parse_manifest({**README_EXAMPLE, "type": kind, "source": None})
    with pytest.raises(ValidationError, match="sparse_path"):
        parse_manifest({**KEG_EXAMPLE, "source": {"repo": "DadsMmoLab/dads-mmo-lab"}})


def test_sql_mod_needs_no_source() -> None:
    """Pure-SQL mods (mob tweaks, XP rates) are inline and have nothing to clone."""
    m = parse_manifest(
        {
            "id": "baby-mobs",
            "name": "Baby Mobs",
            "type": "mod",
            "game": "wow-wotlk",
            "sql": [
                {
                    "db": "world",
                    "statement": "UPDATE creature_template SET HealthModifier=HealthModifier*{hp}",
                },
                {
                    "db": "world",
                    "statement": "UPDATE creature_template SET HealthModifier=HealthModifier/{hp}",
                    "when": "remove",
                },
            ],
            "prompts": [
                {"key": "hp", "question": "HP multiplier", "kind": "float", "default": "0.25"}
            ],
        }
    )
    assert m.source is None and m.sql[1].when == "remove"


def test_sql_step_needs_exactly_one_body() -> None:
    for body in ({}, {"path": "a.sql", "statement": "SELECT 1"}):
        with pytest.raises(ValidationError, match="exactly one"):
            parse_manifest({**README_EXAMPLE, "sql": [{"db": "world", **body}]})


def test_prompt_choice_rules() -> None:
    with pytest.raises(ValidationError, match="choices"):
        parse_manifest(
            {**README_EXAMPLE, "prompts": [{"key": "x", "question": "?", "kind": "choice"}]}
        )
    with pytest.raises(ValidationError, match="choices"):
        parse_manifest(
            {**README_EXAMPLE, "prompts": [{"key": "x", "question": "?", "choices": ["a"]}]}
        )


def test_item_cannot_require_or_conflict_with_itself() -> None:
    with pytest.raises(ValidationError, match="itself"):
        parse_manifest({**README_EXAMPLE, "requires": ["mod-ah-bot"]})


def test_index_parses() -> None:
    idx = parse_index(
        {"schema_version": 1, "game": "wow-wotlk", "type": "module", "items": ["mod-ah-bot"]}
    )
    assert idx.items == ("mod-ah-bot",)
    with pytest.raises(ValidationError):
        parse_index({"schema_version": 1, "game": "wow-wotlk", "type": "module", "modules": []})


def test_checked_in_json_schema_is_current() -> None:
    """`manifests/schema/manifest.schema.json` must equal `--dump-schema` output."""
    on_disk = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    assert (
        on_disk == manifest.json_schema()
    ), "regenerate: python -m yulon.manifest --dump-schema > manifests/schema/manifest.schema.json"


def test_manifest_models_are_frozen() -> None:
    m = parse_manifest(README_EXAMPLE)
    with pytest.raises(ValidationError):
        m.name = "changed"  # type: ignore[misc]
    assert isinstance(m, Manifest)


def _index_files() -> list[Path]:
    return sorted(p for p in MANIFESTS_DIR.glob("*/*.json") if p.parent.name != "schema")


@pytest.mark.parametrize("index_file", _index_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_checked_in_index_and_its_items_validate(index_file: Path) -> None:
    """Every `manifests/<game>/<family>.json` index and each listed item file parses.

    This is the "no code change needed to add a module" guarantee in test form:
    a new JSON file that doesn't fit the schema fails CI here, before any
    controller ever loads it.
    """
    index = parse_index(json.loads(index_file.read_text(encoding="utf-8")))
    assert index.game == index_file.parent.name
    item_dir = index_file.with_suffix("")
    for item_id in index.items:
        item_file = item_dir / f"{item_id}.json"
        assert item_file.is_file(), f"{index_file.name} lists {item_id} but {item_file} is missing"
        item = parse_manifest(json.loads(item_file.read_text(encoding="utf-8")))
        assert item.id == item_id and item.type == index.type and item.game == index.game
    # Nothing on disk that the index forgot.
    if item_dir.is_dir():
        orphans = sorted(p.stem for p in item_dir.glob("*.json"))
        assert set(orphans) <= set(index.items), f"not in {index_file.name}: {orphans}"
