"""Tests for the catalog (`yulon.catalog.catalog` + `catalog.json`, roadmap 3.1)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from yulon import resources
from yulon.catalog.catalog import (
    CATALOG_FILE,
    ClientSpec,
    DbFacts,
    EmulatorSource,
    ExtractPlan,
    NativeInstall,
    PasswordPlan,
    ReadyMarkers,
    SqlPhase,
    SqlPlan,
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


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ({"mode": "generated", "file": ".db_password", "value": "x"}, "must not carry a `value`"),
        ({"mode": "generated", "value": "x"}, "must not carry a `value`"),
        ({"mode": "fixed", "value": "x", "file": ".db_password"}, "must not name a `file`"),
    ],
)
def test_a_password_plan_may_not_name_the_other_mode_s_field(
    plan: dict[str, object], message: str
) -> None:
    """Each mode refuses the other's field, because `composegen` reads `.value` unconditionally.

    `{"mode": "generated", "value": "x"}` validated until this test existed
    (review, B.6): the entry says the password is minted per install and
    persisted at `file`, while `render()` would find a `.value` to splice into
    the compose TEXT — the exact leak B.6's refusal of `{{DB_PASSWORD}}` exists
    to prevent, arriving through the model instead of the template. A `file` on
    a fixed plan is the mirror: two sources of truth for one password, and
    nothing saying which wins.
    """
    with pytest.raises(ValidationError, match=message):
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


def test_wotlk_native_block_names_its_family_images_database_and_ready_markers() -> None:
    """The facts `native.py` and `docker.py` hard-coded, now data (phase7-decisions "wow-wotlk").

    The image prefix and the four service keys are `composegen`'s old
    `DEFAULT_IMAGE_PREFIX`/`BUILT_SERVICES` with the same values; the ready
    markers are `docker.py`'s `"ready..."` literal and `native._READY_REALM_HOST`
    spelled as tokens. Same strings, new home, so a resume still finds its images.
    The markers are literal (`regex` False): the spine `re.escape`s them, so the
    dots in `127.0.0.1` never become wildcards.

    The bot population is the owner's 500/500 (2026-08-28), the same number the
    five installer scripts ship; `test_composegen.py` sweeps them all against it.
    """
    native = load_catalog().get("wow-wotlk").install.native
    assert native is not None
    assert native.family == "azerothcore"
    assert native.images == ("worldserver", "authserver", "db-import", "client-data")
    assert native.image_prefix == "yulon.local/ac-wotlk-"
    assert native.dockerfile_dir is None
    assert native.db == DbFacts(image="mysql:8.4", client="mysql", user="root")
    assert native.db.charset == "utf8mb4"
    assert native.ready == ReadyMarkers(world="ready...", auth="{{REALM_HOST}}:{{WORLD_PORT}}")
    assert native.ready.fatal is None
    assert native.ready.regex is False
    assert native.ready.timeout_s == 600 and native.ready.restart_loop == 4
    assert native.azerothcore is not None
    assert native.azerothcore.world_env == {
        "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS": "500",
        "AC_AI_PLAYERBOT_MAX_RANDOM_BOTS": "500",
    }
    # `world_env` moved INTO the azerothcore block; a stale top-level key is an error.
    with pytest.raises(ValidationError, match="world_env"):
        type(native).model_validate({**native.model_dump(), "world_env": {}})


def test_the_three_cmangos_entries_have_no_native_block_yet() -> None:
    """7.1 dispatches only WotLK natively; the CMaNGOS blocks arrive with 7.3's models.

    F.4 changes the `platforms` assertion to `()` in 7.2; G.4 deletes this test
    when the three entries get their `native` blocks in 7.3.
    """
    catalog = load_catalog()
    for game_id in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        assert catalog.get(game_id).install.native is None, game_id
        assert catalog.get(game_id).install.platforms == ("linux",), game_id


@pytest.mark.parametrize("missing", ["family", "images", "image_prefix", "db", "ready"])
def test_a_native_block_without_its_facts_is_refused(missing: str) -> None:
    native = load_catalog().get("wow-wotlk").install.native
    assert native is not None
    data = native.model_dump()
    del data[missing]
    with pytest.raises(ValidationError, match=missing):
        type(native).model_validate(data)


def test_ready_markers_are_literal_unless_the_entry_says_regex() -> None:
    """A5: `regex` defaults to False; only Tortoise's alternations set it (7.3)."""
    assert ReadyMarkers(world="ready...").regex is False
    assert ReadyMarkers(world="World initialized|Avg Diff", regex=True).regex is True
    with pytest.raises(ValidationError):
        ReadyMarkers(world="")


def test_a_game_with_a_one_shot_import_service_must_carry_a_fixed_password() -> None:
    """The invariant that keeps the import gate and the Server tab on one password.



    `install_wiring.import_gate_for()` builds the probe pair from

    `fixed_db_password(entry)` - the CATALOGUE's value - because it has no

    `server_dir` to read a generated file from. `ControllerServices.for_wotlk()`

    builds everything else from `entry.install.db_password(server_dir)`, which

    does read that file. Today the two agree, and only because the one entry

    that names a `db_import` container is wow-wotlk, whose plan is `fixed`.



    An entry that named an import service AND generated its password would send

    the probe to the database as root with the literal "password" while every

    other control used the real one. The symptom is not a silent Repair offer:

    `repair.import_state()` cannot read the schemas, answers `unreadable`, and

    the spine turns that into a hard `InstallerError` - so the install fails at

    the import stage with a message about the database being unreadable, which

    is a confusing way to say "wrong password".



    If you are reading this because the test went red, you added such an entry.

    Either give it `"mode": "fixed"`, or teach `import_gate_for()` to be handed

    a password by a caller that holds a `server_dir` (a contract change - see

    the 7.1 interface contract's `install_wiring` block). Do NOT make

    `for_wotlk()` use the fixed password: that is the closed bug this pair of

    seams exists to keep closed.

    """

    generated = [
        entry.id
        for entry in load_catalog().games
        if entry.containers.db_import and entry.install.password.mode != "fixed"
    ]

    assert not generated, (
        "these name a one-shot import service but generate their password, so the "
        f"import gate would authenticate with the catalogue's fixed value: {generated}"
    )

    assert any(
        entry.containers.db_import for entry in load_catalog().games
    ), "no entry names an import service at all, so this test would pass on an empty catalog"


def test_wotlk_names_no_script_platform_but_still_ships_its_scripts_until_7_2() -> None:
    """One JSON key changes; the bash files and `script` field stay until 7.2 deletes this test."""
    wotlk = load_catalog().get("wow-wotlk")
    assert wotlk.install.script_platforms is None
    assert wotlk.install.platforms == ("linux", "macos", "windows")
    assert wotlk.install.native is not None
    assert wotlk.install.script == "wow-wotlk/install-wow-wotlk.sh"


# -- the CMaNGOS blocks (7.3, task G.2) ---------------------------------------

SQL_PLAN = {
    "create": ["mangos", "realmd"],
    "phases": [
        {"name": "realmd base", "into": "realmd", "files": ["src/core/sql/base/realmd.sql"]},
        {
            "name": "hotfix",
            "into": "mangos",
            "statements": ["UPDATE x SET y = 1"],
            "on_error": "warn",
        },
        {"name": "schema", "files": ["src/core/sql/create_databases.sql"]},
        {
            "name": "core updates",
            "into_each": {"mangos": "src/core/sql/updates/mangos/*.sql"},
            "sort": "natural",
            "on_error": "warn",
        },
    ],
    "verify": [{"db": "mangos", "query": "SELECT COUNT(*) FROM item_template", "min": 10000}],
    "player_data": [{"db": "characters", "table": "characters"}],
    "marker_db": "mangos",
}

CMANGOS = {
    "client": {"required_file": "Data/expansion.MPQ", "min_mpq": 6},
    "dockerfile": {"make_jobs": 2},
    "extract": {
        "image": "server",
        "tools": [
            {
                "name": "dbc and maps",
                "argv": ["/opt/mangos/bin/tools/ad", "-i", "/client"],
                "produces": {"dbc": 100},
            },
            {
                "name": "vmap extract",
                "argv": ["/opt/mangos/bin/tools/vmap_extractor"],
                "produces": {"Buildings": 100},
            },
        ],
        "retry": {"when_log_matches": "Segmentation fault|core dumped", "tools": ["vmap extract"]},
    },
    "mmaps": {"argv": ["/opt/mangos/bin/tools/MoveMapGen", "--silent"], "min_files": 500},
    "conf": {
        "source_dir": "/opt/mangos/etc",
        "files": {"mangosd.conf": {"keys": {"DataDir": '"/opt/mangos/data"'}}},
    },
    "sql": SQL_PLAN,
}

NATIVE_CMANGOS = {
    "family": "cmangos",
    "templates": "shared/cmangos",
    "dockerfile_dir": "wow-x/native",
    "image_prefix": "yulon.local/cmangos-x-",
    "images": ["server"],
    "db": {"image": "mariadb:11", "client": "mariadb", "user": "mangos"},
    "ready": {"world": "Avg Diff:"},
    "cmangos": CMANGOS,
}


def test_the_cmangos_block_validates_and_fills_its_defaults() -> None:
    native = NativeInstall.model_validate(NATIVE_CMANGOS)
    assert native.cmangos is not None
    assert native.cmangos.client.mpq_depth == "recursive"
    assert native.cmangos.client.locale_mpq_required is False
    assert native.cmangos.client.near_client_warn_gb == 8.0
    assert native.cmangos.extract.ulimit_stack_unlimited is False
    assert native.cmangos.extract.stage_client is False
    assert native.cmangos.mmaps.required is True
    assert native.cmangos.conf.files["mangosd.conf"].match_commented is False
    phases = native.cmangos.sql.phases
    assert phases[0].on_error == "fail" and phases[0].sort == "natural" and phases[0].gzip is False
    assert phases[2].into is None and phases[2].into_each is None, "schema-less is allowed"
    assert native.dockerfile_dir == "wow-x/native", "B.3's field, unchanged by 7.3"
    assert native.ready.regex is False, "A5: markers are literals unless the entry says regex"


def test_the_family_names_exactly_the_block_that_is_present() -> None:
    """`family` and the typed block must agree; one without the other is a typo, not a choice."""
    with pytest.raises(ValidationError, match="cmangos"):
        NativeInstall.model_validate({**NATIVE_CMANGOS, "cmangos": None})
    with pytest.raises(ValidationError, match="azerothcore"):
        NativeInstall.model_validate({**NATIVE_CMANGOS, "azerothcore": {"world_env": {}}})
    with pytest.raises(ValidationError, match="cmangos"):
        NativeInstall.model_validate(
            {**NATIVE_CMANGOS, "family": "azerothcore", "azerothcore": {"world_env": {}}}
        )
    ac = NativeInstall.model_validate(
        {
            "family": "azerothcore",
            "templates": "wow-wotlk/native",
            "image_prefix": "yulon.local/ac-wotlk-",
            "images": ["worldserver"],
            "db": {"image": "mysql:8.4", "client": "mysql", "user": "root"},
            "ready": {"world": "ready..."},
            "azerothcore": {"world_env": {}},
        }
    )
    assert ac.cmangos is None and ac.dockerfile_dir is None


def test_the_extract_plan_refuses_an_unknown_retry_tool_and_an_unbuilt_image() -> None:
    with pytest.raises(ValidationError, match="retry"):
        ExtractPlan.model_validate(
            {**CMANGOS["extract"], "retry": {"when_log_matches": "x", "tools": ["not a tool"]}}
        )
    with pytest.raises(ValidationError, match="images"):
        NativeInstall.model_validate(
            {
                **NATIVE_CMANGOS,
                "cmangos": {**CMANGOS, "extract": {**CMANGOS["extract"], "image": "tools"}},
            }
        )


def test_an_sql_phase_is_files_or_statements_into_one_place() -> None:
    with pytest.raises(ValidationError, match="files"):
        SqlPhase.model_validate({"name": "empty", "into": "mangos"})
    with pytest.raises(ValidationError, match="files"):
        SqlPhase.model_validate(
            {"name": "both", "into": "mangos", "files": ["a.sql"], "statements": ["SELECT 1"]}
        )
    with pytest.raises(ValidationError, match="into"):
        SqlPhase.model_validate(
            {
                "name": "two places",
                "into": "mangos",
                "into_each": {"realmd": "x/*.sql"},
                "files": ["a.sql"],
            }
        )
    with pytest.raises(ValidationError, match="into_each"):
        SqlPhase.model_validate(
            {
                "name": "statements per db",
                "into_each": {"realmd": "x/*.sql"},
                "statements": ["SELECT 1"],
            }
        )


def test_the_plan_hash_is_canonical_and_moves_with_the_plan() -> None:
    """The import marker records it; a reordered JSON file must not read as a new plan,
    a changed file list must."""
    plan = SqlPlan.model_validate(SQL_PLAN)
    again = SqlPlan.model_validate(
        {**SQL_PLAN, "marker_db": "mangos", "create": ["mangos", "realmd"]}
    )
    assert plan.plan_hash() == again.plan_hash()
    assert len(plan.plan_hash()) == 16 and int(plan.plan_hash(), 16) >= 0
    changed = SqlPlan.model_validate({**SQL_PLAN, "create": ["mangos"]})
    assert changed.plan_hash() != plan.plan_hash()


def test_mpq_depth_is_a_positive_int_or_recursive() -> None:
    assert ClientSpec(mpq_depth=1).mpq_depth == 1
    assert ClientSpec(mpq_depth="recursive").mpq_depth == "recursive"
    with pytest.raises(ValidationError):
        ClientSpec(mpq_depth=0)
    with pytest.raises(ValidationError):
        ClientSpec(mpq_depth="deep")  # type: ignore[arg-type]
