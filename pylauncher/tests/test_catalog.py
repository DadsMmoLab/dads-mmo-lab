"""Tests for the catalog (`yulon.catalog.catalog` + `catalog.json`, roadmap 3.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from yulon import resources
from yulon.catalog.catalog import (
    CATALOG_FILE,
    EmulatorSource,
    PasswordPlan,
    load_catalog,
    parse_catalog,
)
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
    assert wotlk.install.password == PasswordPlan(mode="fixed", value="password")
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
                "emulator": {"name": "e", "sources": [{"repo": "a/b", "dest": "."}]},
                "install": {
                    "script": "s.sh",
                    "default_server_dir": "d",
                    "password": {"mode": "fixed", "value": "x"},
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
                        "emulator": {
                            "name": "e",
                            "sources": [{"repo": "ftp://evil/x", "dest": "."}],
                        },
                        "install": {
                            "script": "s.sh",
                            "default_server_dir": "d",
                            "password": {"mode": "fixed", "value": "x"},
                        },
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

    The generated-password file was declared in the schema and read nowhere, so
    every caller fell back to the shared default - which for TBC and Vanilla is
    simply the wrong password, because their installers generate one. Start and
    Stop need no database, so it would have surfaced on Create account.
    """
    catalog = load_catalog()

    wotlk = catalog.get("wow-wotlk").install
    assert wotlk.db_password(tmp_path) == "password", "a fixed password wins outright"

    tbc = catalog.get("wow-tbc").install
    assert tbc.password.file, "wow-tbc is expected to generate its password"
    assert tbc.db_password(tmp_path) is None, "no file yet, so nothing is knowable"

    (tmp_path / tbc.password.file).write_text("tbcdeadbeef\n", encoding="utf-8")
    assert tbc.db_password(tmp_path) == "tbcdeadbeef", "read, and stripped of its newline"

    (tmp_path / tbc.password.file).write_text("   \n", encoding="utf-8")
    assert tbc.db_password(tmp_path) is None, "a blank file is not a password"


def test_db_password_is_none_when_the_file_cannot_be_read(tmp_path: Path) -> None:
    """A directory where the password file should be is unreadable, not empty.

    None rather than the default on purpose: the caller is then free to say the
    password is unknown instead of authenticating with a guess.
    """
    tbc = load_catalog().get("wow-tbc").install
    assert tbc.password.file
    (tmp_path / tbc.password.file).mkdir()
    assert tbc.db_password(tmp_path) is None


def test_every_entry_says_how_its_password_comes_to_exist() -> None:
    """One model, two modes (phase7-decisions "Password").

    WotLK's fixed `"password"` is a contract with backup, the console and every
    archived guide. The three CMaNGOS entries generate one per install and
    persist it at `.db_password`, prefixed so a value seen in `docker exec`
    output says which server it belongs to. The two old optional strings let an
    entry say both or neither; the model refuses that.
    """
    catalog = load_catalog()
    assert catalog.get("wow-wotlk").install.password == PasswordPlan(mode="fixed", value="password")
    generated = (("wow-tbc", "tbc-"), ("wow-vanilla", "vanilla-"), ("wow-tortoise", "tortoise-"))
    for game_id, prefix in generated:
        plan = catalog.get(game_id).install.password
        assert plan == PasswordPlan(mode="generated", file=".db_password", prefix=prefix), game_id


@pytest.mark.parametrize(
    "plan",
    [
        {"mode": "fixed"},
        {"mode": "fixed", "value": ""},
        {"mode": "generated"},
        {"mode": "generated", "prefix": "tbc-"},
        {"mode": "rolled", "value": "x"},
    ],
)
def test_a_password_plan_without_its_mode_s_field_is_refused(plan: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        PasswordPlan.model_validate(plan)


def test_the_old_password_fields_are_gone_not_ignored() -> None:
    """`extra="forbid"` turns a stale key into an error instead of a silent default."""
    with pytest.raises(ValidationError, match="db_root_password"):
        parse_catalog(
            {
                "games": [
                    {
                        "id": "x",
                        "name": "X",
                        "status": "wip",
                        "emulator": {"name": "e", "sources": [{"repo": "a/b", "dest": "."}]},
                        "install": {
                            "script": "s.sh",
                            "default_server_dir": "d",
                            "password": {"mode": "fixed", "value": "x"},
                            "db_root_password": "x",
                        },
                        "containers": {"db": "a", "auth": "b", "world": "c"},
                        "ports": {"auth": 1, "world": 2},
                        "databases": {"auth": "a", "characters": "c", "world": "w"},
                        "client": {"version": "1", "build": 1},
                    }
                ]
            }
        )


def _compose_services_declared(script: Path) -> dict[str, str]:
    """Map compose SERVICE key -> `container_name:` for every services block in a script.

    The installers write their `docker-compose.yml` from a heredoc, so the file the
    user ends up with is readable straight out of the script. A service with no
    `container_name:` maps to "" — compose then names the container itself.
    """
    services: dict[str, str] = {}
    in_services = False
    current: str | None = None
    for line in script.read_text(encoding="utf-8").splitlines():
        if line == "services:":
            in_services, current = True, None
            continue
        if not in_services or not line.strip():
            continue
        if not line.startswith(" "):  # `volumes:`, `networks:`, the heredoc terminator
            in_services, current = False, None
            continue
        key = re.match(r"^  ([a-z][a-z0-9_.-]*):\s*$", line)
        if key:
            current = key.group(1)
            services.setdefault(current, "")
            continue
        name = re.match(r"^\s+container_name:\s*(\S+)\s*$", line)
        if name and current:
            services[current] = name.group(1)
    return services


def test_cmangos_games_select_compose_services_not_container_names() -> None:
    """Every CMaNGOS installer names its services db/realmd/mangosd (Discord, 2026-08-26).

    Its containers are `<game>-db` and friends, and `docker compose up <container>`
    answers `no such service`, so the catalog must spell the services out. For
    AzerothCore the two names coincide and the container names are the answer.
    """
    catalog = load_catalog()
    for game_id in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        spec = catalog.get(game_id).container_spec()
        assert spec.compose_services() == ("db", "realmd", "mangosd"), game_id
    assert catalog.get("wow-wotlk").container_spec().compose_services() == (
        "ac-database",
        "ac-authserver",
        "ac-worldserver",
    )


def test_no_catalog_compose_service_is_really_a_container_name() -> None:
    """The invariant behind the bug: what `compose up` selects must be a service key.

    Only decided for compose files this repo writes; WotLK's base file comes from
    the AzerothCore checkout, so a service missing from the script proves nothing.
    """
    installers = resources.installers_dir()
    for game in load_catalog().games:
        declared = _compose_services_declared(installers / game.install.script)
        if not declared:
            continue
        container_names = {name for name in declared.values() if name}
        for service in game.container_spec().compose_services():
            assert service not in container_names or service in declared, (
                f"{game.id}: `docker compose up {service}` names a CONTAINER, not a service; "
                f"this compose file declares {sorted(declared)}"
            )


def test_every_source_says_where_it_lands() -> None:
    """`dest` replaces the index rule "sources[0] is the core, the rest go under modules/".

    That rule fit exactly one layout. CMaNGOS's playerbots checkout nests INSIDE
    the core (`src/mangos-tbc/src/modules/Bots`), which no index can express.
    WotLK's values are the paths `native._clone_core`/`_clone_modules` write
    today, so the move to data changes no directory on disk.
    """
    catalog = load_catalog()
    expected = {
        "wow-wotlk": (".", "modules/mod-playerbots"),
        "wow-tbc": ("src/mangos-tbc", "src/mangos-tbc/src/modules/Bots", "src/tbc-db"),
        "wow-vanilla": (
            "src/mangos-classic",
            "src/mangos-classic/src/modules/Bots",
            "src/classic-db",
        ),
        "wow-tortoise": ("src/tortoise-wow",),
    }
    for game_id, dests in expected.items():
        sources = catalog.get(game_id).emulator.sources
        assert all(isinstance(source, EmulatorSource) for source in sources)
        assert tuple(source.dest for source in sources) == dests, game_id


@pytest.mark.parametrize("dest", ["", "/srv/wow", "../elsewhere", "src/../../x", "src\\core"])
def test_a_dest_that_could_leave_the_server_dir_is_refused(dest: str) -> None:
    """A clone target is joined onto the server dir; nothing may point it outside."""
    with pytest.raises(ValidationError):
        EmulatorSource.model_validate({"repo": "a/b", "dest": dest})
