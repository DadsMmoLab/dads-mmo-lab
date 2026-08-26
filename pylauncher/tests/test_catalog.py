"""Tests for the catalog (`yulon.catalog.catalog` + `catalog.json`, roadmap 3.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from yulon import resources
from yulon.catalog.catalog import CATALOG_FILE, load_catalog, parse_catalog
from yulon.controller_wow_wotlk import docker_ctl

V1_GAMES = ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise")


def test_bundled_catalog_describes_exactly_the_four_v1_servers() -> None:
    """README §1: v1 scope is WoW WotLK / TBC / Vanilla / Tortoise, acronyms only."""
    catalog = load_catalog()
    assert tuple(g.id for g in catalog.games) == V1_GAMES
    for game in catalog.games:
        assert "Warcraft" not in game.name and "Warcraft" not in game.id
        assert game.ports.auth == 3724  # shared by every v1 server (README §12)
        assert game.client.build > 0


def test_wotlk_entry_matches_the_controller_spec() -> None:
    """The catalog's WotLK containers/ports are the same facts `docker_ctl.SPEC` pins."""
    wotlk = load_catalog().get("wow-wotlk")
    assert wotlk.container_spec() == docker_ctl.SPEC
    assert wotlk.has_manifests is True
    assert wotlk.install.db_root_password == "password"
    assert wotlk.emulator.sources[0].url == (
        "https://github.com/mod-playerbots/azerothcore-wotlk.git"
    )


def test_install_scripts_exist_in_the_repo() -> None:
    """Phase 3a wraps the existing scripts — every referenced path must be real."""
    installers = resources.installers_dir()
    for game in load_catalog().games:
        assert (installers / game.install.script).is_file(), game.install.script
        for pm, variant in game.install.script_variants.items():
            assert (installers / variant).is_file(), f"{game.id} {pm}: {variant}"
            assert game.install.script_for(pm) == variant
        assert game.install.script_for(None) == game.install.script
        assert game.install.script_for("zypper") == game.install.script


def test_script_variant_keys_must_be_known_package_managers() -> None:
    """A typo like "ubuntu" would silently fall back to the pacman script — refuse it."""
    bad = {
        "schema_version": 1,
        "games": [
            {
                "id": "x-y",
                "name": "X",
                "status": "wip",
                "emulator": {"name": "e", "sources": [{"repo": "a/b"}]},
                "install": {
                    "script": "s.sh",
                    "default_server_dir": "d",
                    "script_variants": {"ubuntu": "s-ubuntu.sh"},
                },
                "containers": {"db": "d", "auth": "a", "world": "w"},
                "ports": {"auth": 1, "world": 2, "db": 3},
                "databases": {"auth": "a", "characters": "c", "world": "w"},
                "client": {"version": "1", "build": 1},
            }
        ],
    }
    with pytest.raises(ValidationError):
        parse_catalog(bad)


def test_only_one_server_runs_at_a_time_is_visible_in_the_data() -> None:
    """Every v1 server publishes the same auth port, so the §12 guard will engage."""
    ports = {g.ports.auth for g in load_catalog().games}
    assert ports == {3724}


def test_unknown_game_and_bad_entries_are_rejected() -> None:
    catalog = load_catalog()
    with pytest.raises(KeyError):
        catalog.get("wow-cata")
    with pytest.raises(ValidationError, match="repo"):
        parse_catalog(
            {
                "games": [
                    {
                        "id": "x",
                        "name": "X",
                        "status": "wip",
                        "emulator": {"name": "e", "sources": [{"repo": "ftp://evil/x"}]},
                        "install": {"script": "s.sh", "default_server_dir": "d"},
                        "containers": {"db": "a", "auth": "b", "world": "c"},
                        "ports": {"auth": 1, "world": 2},
                        "databases": {"auth": "a", "characters": "c", "world": "w"},
                        "client": {"version": "1", "build": 1},
                    }
                ]
            }
        )
    assert CATALOG_FILE.name == "catalog.json"


def test_db_password_prefers_a_fixed_one_then_the_generated_file(tmp_path: Path) -> None:
    """Where the root password comes from, for a game that does not have a fixed one.

    `db_root_password_file` was declared in the schema and read nowhere, so
    every caller fell back to the shared default - which for TBC and Vanilla is
    simply the wrong password, because their installers generate one. Start and
    Stop need no database, so it would have surfaced on Create account.
    """
    catalog = load_catalog()

    wotlk = catalog.get("wow-wotlk").install
    assert wotlk.db_password(tmp_path) == "password", "a fixed password wins outright"

    tbc = catalog.get("wow-tbc").install
    assert tbc.db_root_password_file, "wow-tbc is expected to generate its password"
    assert tbc.db_password(tmp_path) is None, "no file yet, so nothing is knowable"

    (tmp_path / tbc.db_root_password_file).write_text("tbcdeadbeef\n", encoding="utf-8")
    assert tbc.db_password(tmp_path) == "tbcdeadbeef", "read, and stripped of its newline"

    (tmp_path / tbc.db_root_password_file).write_text("   \n", encoding="utf-8")
    assert tbc.db_password(tmp_path) is None, "a blank file is not a password"


def test_db_password_is_none_when_the_file_cannot_be_read(tmp_path: Path) -> None:
    """A directory where the password file should be is unreadable, not empty.

    None rather than the default on purpose: the caller is then free to say the
    password is unknown instead of authenticating with a guess.
    """
    tbc = load_catalog().get("wow-tbc").install
    assert tbc.db_root_password_file
    (tmp_path / tbc.db_root_password_file).mkdir()
    assert tbc.db_password(tmp_path) is None


def test_every_game_says_how_its_db_password_can_be_known() -> None:
    """A game that declares neither is a controller that cannot use its database.

    The app needs the root password for every SQL-backed control - accounts,
    backup, restore, the repair probes. An entry that names no fixed password
    AND no generated-password file silently resolves to the shared default,
    which is right only by accident. This is the invariant that caught
    wow-tortoise: its installer generates `tortoise$(date +%s | tail -c 6)` and
    the catalog declared nothing at all.
    """
    for entry in load_catalog().games:
        install = entry.install
        assert install.db_root_password or install.db_root_password_file, (
            f"{entry.id} declares neither db_root_password nor db_root_password_file, "
            f"so the app would authenticate to its database with the shared default"
        )


def test_every_game_maps_its_own_schema_names() -> None:
    """The schema names SQL connects to come from the entry, not from a constant."""
    catalog = load_catalog()
    assert catalog.get("wow-tortoise").schema_map() == {
        "auth": "tw_logon",
        "characters": "tw_char",
        "world": "tw_world",
    }
    for game_id in ("wow-tbc", "wow-vanilla"):
        assert catalog.get(game_id).schema_map() == {
            "auth": "realmd",
            "characters": "characters",
            "world": "mangos",
        }, game_id


def test_wotlk_s_schema_map_is_the_applier_s_default() -> None:
    """`apply.DB_NAMES` is the AzerothCore map; wow-wotlk must not drift from it."""
    from yulon.apply import DB_NAMES

    assert load_catalog().get("wow-wotlk").schema_map() == dict(DB_NAMES)


def test_the_cmangos_cores_declare_the_mangos_console_prompt() -> None:
    """`AC>` is AzerothCore's delimiter; mangosd prints `mangos>` (archive/guides)."""
    catalog = load_catalog()
    for game_id in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        assert catalog.get(game_id).console.prompt == "mangos>", game_id
    assert catalog.get("wow-wotlk").console.prompt == "AC>"
