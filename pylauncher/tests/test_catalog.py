"""Tests for the catalog (`yulon.catalog.catalog` + `catalog.json`, roadmap 3.1)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

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
    repo_root = Path(__file__).resolve().parents[2]
    for game in load_catalog().games:
        assert (repo_root / game.install.script).is_file(), game.install.script


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
