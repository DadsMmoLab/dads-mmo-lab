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
@pytest.mark.parametrize("value", ["Mod-AH-Bot", "world of dadcraft", "-lead", "double--dash", ""])
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


_ROSTER_PRECONDITION: dict[str, Any] = {
    "db": "playerbots",
    "query": "SELECT 1 FROM information_schema.TABLES WHERE TABLE_SCHEMA = DATABASE()",
    "missing": "mod-playerbots has not created its tables yet",
}


def test_a_sql_step_check_belongs_only_to_a_direct_step() -> None:
    """T63's two fields are refused on the route this app does not run itself.

    A `db-import` step is handed to AzerothCore's own updater on a later boot.
    Nothing here opens the file, and nothing here is watching when it lands, so
    a `precondition` on one would gate a write this engine never makes and a
    `verify` would read a database before the program that wrote it had run. A
    field nobody reads is how `conflicts_with` came to be in the schema, in the
    catalog and in a passing test while the app installed both modules anyway
    (T53), and the cheapest place to stop the next one is the parser.
    """
    for extra in (
        {"precondition": _ROSTER_PRECONDITION},
        {"verify": [_ROSTER_PRECONDITION]},
    ):
        with pytest.raises(ValidationError, match="applied_by='direct'"):
            parse_manifest(
                {
                    **README_EXAMPLE,
                    "sql": [
                        {
                            "db": "world",
                            "path": "a.sql",
                            "applied_by": "db-import",
                            **extra,
                        }
                    ],
                }
            )
    # The same step on the direct route parses, so the refusal above is about
    # `applied_by` and not about the field being malformed.
    ok = parse_manifest(
        {
            **README_EXAMPLE,
            "sql": [
                {
                    "db": "world",
                    "path": "a.sql",
                    "applied_by": "direct",
                    "precondition": _ROSTER_PRECONDITION,
                    "verify": [_ROSTER_PRECONDITION],
                }
            ],
        }
    )
    assert ok.sql[0].precondition is not None and len(ok.sql[0].verify) == 1


def test_a_sql_step_check_query_must_be_one_select() -> None:
    """The same fence `Prompt.exists` has, asserted through the new field.

    `ExistsCheck` is one model with three users now, and its `_one_select`
    validator is the reason a manifest -- content, not code -- cannot reach the
    write half of the SQL seam through a field whose whole job is to read.
    Pinned here as well as on the prompt, because a shared validator that stops
    being reached by one of its users fails silently.
    """
    for query in ("DELETE FROM citizen_roster", "SELECT 1; DROP TABLE citizen_roster"):
        with pytest.raises(ValidationError, match="ExistsCheck.query"):
            parse_manifest(
                {
                    **README_EXAMPLE,
                    "sql": [
                        {
                            "db": "world",
                            "path": "a.sql",
                            "precondition": {**_ROSTER_PRECONDITION, "query": query},
                        }
                    ],
                }
            )


def test_no_shipped_sql_step_check_carries_a_template_field() -> None:
    """A `SqlStep` check is never rendered, so a `{key}` in one would be sent to MySQL raw.

    `Prompt.exists` templates over the user's answers and `Applier._check_exists`
    renders it. The two `SqlStep` checks are the CATALOG's own sentence about
    the module's own tables, asked before any value is in hand -- and
    `Applier._ask_db()` deliberately does not render, so that a `{` in somebody's
    SQL cannot raise inside a check whose whole job is to answer a question.
    That decision is only safe while no shipped check carries one.

    Not vacuous: the count of checks it actually read is asserted, so a glob
    that stopped finding files would fail here rather than pass over nothing.
    """
    seen: list[str] = []
    offenders: list[str] = []
    for path in sorted(MANIFESTS_DIR.glob("*/*/*.json")):
        if path.parent.parent.name == "schema":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        for step in raw.get("sql", []):
            checks = [step["precondition"]] if step.get("precondition") else []
            checks += list(step.get("verify", []))
            for check in checks:
                seen.append(f"{path.name}:{check['query'][:30]}")
                if "{" in check["query"] or "{" in check["missing"]:
                    offenders.append(f"{path.relative_to(MANIFESTS_DIR)}: {check['query']!r}")
    assert offenders == [], offenders
    assert len(seen) == 3, seen  # mod-city-bots' precondition and its two verify entries


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


def test_a_source_may_pin_a_full_commit_sha_and_nothing_else() -> None:
    """A pin is a SHA, not a ref: a tag can be moved, and GitHub serves a fetch by hash
    only for the full 40-hex object id (`uploadpack.allowReachableSHA1InWant`)."""
    from yulon.manifest import Source

    assert Source(repo="a/b").rev is None
    assert Source(repo="a/b", rev="0123456789abcdef0123456789abcdef01234567").rev is not None
    for bad in ("v1.0", "main", "0123456", "0123456789ABCDEF0123456789ABCDEF01234567"):
        with pytest.raises(ValidationError):
            Source(repo="a/b", rev=bad)


def test_a_folder_origin_module_needs_no_source_and_a_link_one_still_does() -> None:
    """The `source`-required rule relaxes by exactly one clause, for exactly one shape.

    A module derived from a folder on the user's own disk has nothing to clone
    from — a path is not a clone URL, and `Source.url` feeds `git.CloneSpec`. A
    module derived from a LINK has a source and must still carry it, so the
    relaxation cannot be reached by omitting the field and claiming an origin.
    """
    sourceless = {k: v for k, v in README_EXAMPLE.items() if k != "source"}

    folder = parse_manifest(
        {
            **sourceless,
            "origin": {"kind": "folder", "path": "/home/pk/mod-x", "added": "2026-09-08"},
        }
    )
    assert folder.source is None
    assert folder.origin is not None and folder.origin.kind == "folder"

    with pytest.raises(ValidationError, match="source"):
        parse_manifest({**sourceless, "origin": {"kind": "link", "added": "2026-09-08"}})
    for kind in ("ale", "keg"):
        with pytest.raises(ValidationError, match="source"):
            parse_manifest(
                {
                    **sourceless,
                    "type": kind,
                    "origin": {"kind": "folder", "path": "/x", "added": "2026-09-08"},
                }
            )


def test_origin_is_optional_and_every_shipped_manifest_has_none() -> None:
    """`origin` says "this app derived me"; a manifest the project ships never did.

    Asserted over the tree rather than trusted: a shipped file that grew an
    `origin` would be a custom module sitting in the bundled index, which the
    store's shadow rule would then have to reason about for no reason.
    """
    assert parse_manifest(README_EXAMPLE).origin is None
    for index_file in _index_files():
        item_dir = index_file.with_suffix("")
        for item_file in sorted(item_dir.glob("*.json")) if item_dir.is_dir() else []:
            assert parse_manifest(json.loads(item_file.read_text(encoding="utf-8"))).origin is None


def test_no_shipped_module_manifest_pins_a_revision() -> None:
    """Every module tracks its repository's latest: the owner's decision, 2026-09-15 (T60).

    Five manifests were pinned (`lootpet`, `sitmeanrest`, `mod-ale`,
    `tortoise-bots-manager`, `tortoise-gm-manager`), each with a written reason,
    and the owner removed all five knowing them. So a `rev` in a shipped module
    manifest is a decision being reversed, and it fails here rather than in
    review. `rev: null` is refused too: the decision is that the field is not
    there.

    The files are enumerated from the DISK, every game and every family, and
    not through the indexes, so an item file an index forgot is still read.
    `Source.rev` stays in the schema: the catalog's SERVER sources are pinned
    (`test_catalog.py::GATE_PINS`), and derived user manifests are not shipped.
    """
    items = sorted(p for p in MANIFESTS_DIR.glob("*/*/*.json") if p.parent.parent.name != "schema")
    pinned: list[str] = []
    cloned: set[str] = set()
    for path in items:
        raw = json.loads(path.read_text(encoding="utf-8"))
        source = raw.get("source")
        if source is None:
            continue
        cloned.add(f"{raw['game']}/{raw['id']}")
        if "rev" in source:
            pinned.append(f"{path.relative_to(MANIFESTS_DIR)} rev={source['rev']!r}")
    assert not pinned, (
        "the owner decided on 2026-09-15 (T60) that no module manifest carries a rev -- every "
        f"module tracks its repository's latest. Pinned: {pinned}"
    )
    # Not vacuous: the glob reached every game, and the five that were pinned.
    games = {p.name for p in MANIFESTS_DIR.iterdir() if p.is_dir() and p.name != "schema"}
    assert games == {p.parent.parent.name for p in items}, games
    assert {
        "wow-wotlk/lootpet",
        "wow-wotlk/sitmeanrest",
        "wow-wotlk/mod-ale",
        "wow-tortoise/tortoise-bots-manager",
        "wow-tortoise/tortoise-gm-manager",
    } <= cloned, sorted(cloned)


NOT_MANIFESTS: dict[str, str] = {
    "mod-playerbots": (
        "cloned by the SERVER install, not by the Modules tab: "
        "`catalog.json` lists `mod-playerbots/mod-playerbots` among wow-wotlk's "
        "emulator sources with `dest: modules/mod-playerbots`, so it is present "
        "on every Playerbots install and has no manifest of its own"
    ),
}
"""`requires` targets that are real clone directories but not catalog items, and why.

EXACT, not a floor. Written when `requires` was a declaration nothing read
([[the-mechanism-exists-and-nothing-calls-it]]), against the day something
enforced it by looking the target up as a catalog id -- which would have
refused `mod-city-bots` forever, silently, on installs where its requirement is
in fact present.

T69 is that day, and it took the other route: `apply.missing_requirements()`
asks the DISK, so a folder under `modules/` answers for a server-cloned module
exactly as it does for one the Modules tab cloned, and this dict is not
consulted at runtime at all. It stays a TEST fixture, and the test below now
checks the excuse rather than taking it -- an entry here has to name something
`catalog.json` really clones.
"""


def test_every_requires_target_is_a_shipped_item_or_one_of_these() -> None:
    """A dangling `requires` ships uncaught today; a typo in one would ship too.

    Until T63 every `requires` named a sibling manifest in the same game, which
    made the invariant true by accident. `mod-city-bots` is the first that names
    something the catalog does not carry, so the invariant is written down
    before the exception is added rather than after it is discovered.
    """
    ids: dict[str, set[str]] = {}
    targets: list[tuple[str, str, str]] = []
    for path in sorted(MANIFESTS_DIR.glob("*/*/*.json")):
        if path.parent.parent.name == "schema":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        ids.setdefault(raw["game"], set()).add(raw["id"])
        targets += [(raw["game"], raw["id"], want) for want in raw.get("requires", ())]
        targets += [(raw["game"], raw["id"], want) for want in raw.get("conflicts_with", ())]

    dangling = [
        f"{game}/{item} needs {want}"
        for game, item, want in targets
        if want not in ids[game] and want not in NOT_MANIFESTS
    ]
    assert dangling == [], dangling
    # Not vacuous, and the exception is USED: a `NOT_MANIFESTS` entry nothing
    # names is a note about nothing, and would outlive the manifest it excused.
    assert len(targets) >= 10, targets
    assert {want for _game, _item, want in targets} >= set(NOT_MANIFESTS)


def test_every_excused_requires_target_is_really_cloned_by_a_server_install() -> None:
    """The exception has to EARN itself against `catalog.json` (T69).

    `NOT_MANIFESTS` used to be a sentence a person wrote. Now that
    `apply.missing_requirements()` enforces `requires` against the disk, an
    entry here is a claim that some game's install puts the folder there, and a
    wrong one is a module permanently uninstallable with the reason *needs X,
    not installed* on a machine that can never get X.

    So the claim is read back out of the catalog: every excused id must be the
    last segment of some emulator source's `dest`. The sibling test above
    cannot catch this -- it only asks whether the name is spelled in this dict.
    """
    catalog = json.loads(
        (Path(__file__).resolve().parents[1] / "yulon" / "catalog" / "catalog.json").read_text(
            encoding="utf-8"
        )
    )
    cloned = {
        source["dest"].rstrip("/").rsplit("/", 1)[-1]
        for game in catalog["games"]
        for source in game.get("emulator", {}).get("sources", ())
        # `dest: "."` is the emulator core itself, unpacked over the server
        # root. It is not a clone DIRECTORY anything can require, and letting
        # it in would excuse the name `.` against a folder that is always
        # present -- so it is dropped before the comparison, not after.
        if source.get("dest") and source["dest"].rstrip("/") not in ("", ".")
    }
    assert set(NOT_MANIFESTS) <= cloned, sorted(set(NOT_MANIFESTS) - cloned)
    # Not vacuous, in both directions: the set is really read (it has the one
    # entry that matters) and the drop above really drops.
    assert "mod-playerbots" in cloned and "." not in cloned


# -- T43: the tuning fields on a conf key ----------------------------------


def _with_keys(keys: list[dict[str, Any]]) -> Manifest:
    return parse_manifest(
        {
            **README_EXAMPLE,
            "conf": [{"file": "env/dist/etc/modules/mod_ahbot.conf", "keys": keys}],
        }
    )


def test_a_conf_key_carries_its_label_explain_type_and_bounds() -> None:
    """T43's four optional fields parse and arrive on the model."""
    key = (
        _with_keys(
            [
                {
                    "key": "AuctionHouseBot.MinItems",
                    "default": "500",
                    "label": "Items on sale, minimum",
                    "explain": "The bot tops the house back up to this many lots.",
                    "type": "int",
                    "min": 0,
                    "max": 200000,
                }
            ]
        )
        .conf[0]
        .keys[0]
    )
    assert key.label == "Items on sale, minimum"
    assert key.explain == "The bot tops the house back up to this many lots."
    assert key.type == "int" and key.min == 0 and key.max == 200000


def test_a_conf_key_with_none_of_them_still_parses_and_answers_none() -> None:
    """The 107 keys authored before T43 must be byte-identical in meaning."""
    key = _with_keys([{"key": "AuctionHouseBot.Account"}]).conf[0].keys[0]
    assert (key.label, key.explain, key.type, key.min, key.max) == (None, None, None, None, None)


@pytest.mark.parametrize("kind", ["bool", "list", "text", None])
def test_a_bound_on_a_key_that_is_not_an_int_is_a_parse_error(kind: str | None) -> None:
    """`min`/`max` mean nothing off an `int` and would mislead whoever read them.

    `None` included on purpose: a key with no `type` renders as a text box
    (T43's safety rule), and a bound on a text box is the same lie as a bound
    on a switch.
    """
    for bound in ({"min": 1}, {"max": 9}, {"min": 1, "max": 9}):
        entry: dict[str, Any] = {"key": "AuctionHouseBot.Account", **bound}
        if kind is not None:
            entry["type"] = kind
        with pytest.raises(ValidationError, match="int"):
            _with_keys([entry])


def test_an_int_key_may_carry_one_bound_or_none_at_all() -> None:
    """No clamping is invented where a bound is absent (T43's safety rule)."""
    only_min = _with_keys([{"key": "A.B", "type": "int", "min": 0}]).conf[0].keys[0]
    assert only_min.min == 0 and only_min.max is None
    neither = _with_keys([{"key": "A.B", "type": "int"}]).conf[0].keys[0]
    assert neither.min is None and neither.max is None


def test_a_type_the_schema_does_not_name_is_refused() -> None:
    with pytest.raises(ValidationError):
        _with_keys([{"key": "A.B", "type": "switch"}])


def test_an_int_key_whose_min_is_above_its_max_is_a_parse_error() -> None:
    """A range no value can satisfy, caught where a reader would trust it."""
    with pytest.raises(ValidationError, match="min"):
        _with_keys([{"key": "A.B", "type": "int", "min": 10, "max": 9}])


# -- T43 point 7: what the enriched catalog must keep true ------------------

WOTLK_KEYS = [
    (path, conf.file, key)
    for path in sorted((MANIFESTS_DIR / "wow-wotlk").glob("*/*.json"))
    for conf in manifest.parse_manifest(json.loads(path.read_text(encoding="utf-8"))).conf
    for key in conf.keys
]


def test_a_bool_key_the_catalog_also_writes_agrees_with_its_own_default() -> None:
    """A `type` that disagrees with the `default` beside it would write a wrong value.

    The relationship has no owner otherwise: `ConfKey` can check a bound against
    a bound, but only a pass over the shipped catalog can check that the TYPE a
    key was given matches the VALUE the same key tells the installer to write.
    """
    for path, file, key in WOTLK_KEYS:
        if key.type != "bool" or key.default is None or "{" in key.default:
            continue
        assert key.default.strip().lower() in (
            "0",
            "1",
            "true",
            "false",
        ), f"{path.name}:{file}:{key.key} is typed `bool` but its default is {key.default!r}"


def test_an_int_key_the_catalog_also_writes_parses_and_sits_in_its_own_bounds() -> None:
    for path, file, key in WOTLK_KEYS:
        if key.type != "int" or key.default is None or "{" in key.default:
            continue
        where = f"{path.name}:{file}:{key.key}"
        number = int(key.default.strip())  # raises here rather than at save time
        assert key.min is None or number >= key.min, f"{where}: default below its own min"
        assert key.max is None or number <= key.max, f"{where}: default above its own max"


def test_every_key_the_tuning_tab_can_write_says_what_kind_of_setting_it_is() -> None:
    """T43 point 7, pinned by NAME rather than by a count.

    A count would pass on the wrong twelve. These are the twelve keys deliberately
    left with no `type`, and every one of them is a key the tab refuses to write
    anyway: seven are catalog shorthand for a GROUP of keys (`tuning.NOT_ONE_KEY`)
    and five are `paragon`'s, whose values live in a database table and whose
    source states nothing about them — the author's silence, kept.
    """
    bare = {
        f"{path.parent.name}/{path.name}:{key.key}"
        for path, _file, key in WOTLK_KEYS
        if key.type is None
    }
    assert bare == {
        "ale/paragon.json:LEVEL_LINKED_TO_ACCOUNT",
        "ale/paragon.json:PARAGON_LEVEL_CAP",
        "ale/paragon.json:BASE_MAX_EXPERIENCE",
        "ale/paragon.json:POINTS_PER_LEVEL",
        "ale/paragon.json:UNIVERSAL_CREATURE_EXPERIENCE",
        "kegs/bmah.json:common/rare/ultraRare_*_price",
        "kegs/bmah.json:FillRateCommon / FillRateRare / FillRateUltra",
        "modules/mod-ah-bot-plus.json:AuctionHouseBot.ListProportion.*",
        "modules/mod-autobalance.json:AutoBalance.Enable.*",
        "modules/mod-mount-scaling.json:MountScaling.Ground.Journeyman.*",
        "modules/mod-mount-scaling.json:MountScaling.Flying.Expert.*",
        "modules/mod-mount-scaling.json:MountScaling.Flying.Artisan.*",
    }


def test_a_label_is_a_name_and_not_the_authors_whole_sentence() -> None:
    """The label goes beside a control; the sentence goes under it."""
    for path, file, key in WOTLK_KEYS:
        if key.label is None:
            continue
        where = f"{path.name}:{file}:{key.key}"
        assert len(key.label) <= 40, f"{where}: label is a sentence, not a name"
        assert not key.label.endswith("."), f"{where}: label ends in a full stop"


def test_a_rename_on_a_single_file_deploy_is_refused_at_load() -> None:
    """`rename` only means anything for a DIRECTORY deploy (T53 review).

    `_deploy_target()` returns the full filename for a single-file deploy, so
    `(target / old).replace(target / new)` builds a path INSIDE the copied file:
    `.../LootPet2.lua/LootPet.lua`. Install raised `NotADirectoryError` after
    already copying, leaving the file under its old name.

    That was merged in a manifest of ours (#160) and no test caught it: the existing
    rename test uses a directory src, which is the shape that works. A runtime
    `NotADirectoryError` deep in an install is the wrong place to learn this —
    the manifest is wrong, and a wrong manifest should not load.
    """
    base = {
        "id": "mod-x",
        "name": "X",
        "type": "module",
        "game": "wow-wotlk",
        "description": "x",
        "source": {"repo": "acme/mod-x"},
    }
    with pytest.raises(ValidationError, match="rename"):
        parse_manifest(
            {
                **base,
                "deploy": [{"src": "One.lua", "dest": "d/", "rename": [["One.lua", "Two.lua"]]}],
            }
        )

    # A directory src is the shape it is for, and stays valid.
    ok = parse_manifest(
        {**base, "deploy": [{"src": "tree/", "dest": "d/", "rename": [["a.lua", "b.lua"]]}]}
    )
    assert ok.deploy[0].rename == (("a.lua", "b.lua"),)
    # And a single-file deploy with no rename is the ordinary case.
    assert parse_manifest({**base, "deploy": [{"src": "One.lua", "dest": "d/"}]}).deploy[0].src
