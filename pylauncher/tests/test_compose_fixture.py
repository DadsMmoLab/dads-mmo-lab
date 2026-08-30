"""The generated WotLK stack against the proven yulon-ubuntu install (phase7-decisions, "The
compose-config reference").

Two sources, one vocabulary. `support_compose.shape_from_plan()` reads the three files
`composegen.render()` produces; `shape_from_config()` reads what `docker compose config
--format json` says about a real install. Both become the same `Service` records, and
`compare()` reports every difference that is not a documented design difference (image prefix
and tag, volume names, `stop_grace_period`, the playerbots DB on the importer, upstream's
build-time env). Nothing here runs a daemon.

The second half of this file is the bill for the first. Every rule the vocabulary applies is a
difference it will never report again, so each one is followed by a test that a MEANINGFUL
change in the same area is still caught, and — where a rule costs real sight — by a test that
pins exactly what it hides, so a reader can tell a paid-for blind spot from a missing feature.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from tests import support_compose as sc
from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog

ENTRY = load_catalog().get("wow-wotlk")
TEMPLATES = resources.installers_dir()


def render(server_dir: Path) -> composegen.ComposePlan:
    """Linux, no SELinux: `bind_label=""` is the byte-identical-to-before rendering."""
    return composegen.render(
        ENTRY, server_dir, templates_root=TEMPLATES, bind_label="", platform_id=lambda: "linux"
    )


def test_port_strings_resolve_compose_defaults() -> None:
    assert sc.port_from_string("127.0.0.1:${DOCKER_DB_EXTERNAL_PORT:-3306}:3306") == ("3306", 3306)
    assert sc.port_from_string("${DOCKER_AUTH_EXTERNAL_PORT:-3724}:3724") == ("3724", 3724)
    # The SOAP default carries its own host prefix; the regex must swallow the inner colon.
    assert sc.port_from_string("${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878") == (
        "7878",
        7878,
    )
    assert sc.port_from_config({"target": 3306, "published": "3306", "host_ip": "127.0.0.1"}) == (
        "3306",
        3306,
    )


def test_image_name_drops_registry_and_tag() -> None:
    assert (
        sc.image_name("yulon.local/ac-wotlk-worldserver:native-5c09ea72") == "ac-wotlk-worldserver"
    )
    assert sc.image_name("acore/ac-wotlk-worldserver:master") == "ac-wotlk-worldserver"
    assert sc.image_name("mysql:8.4") == "mysql"


def test_volumes_compare_by_kind_and_target_never_by_volume_name() -> None:
    assert sc.volume_from_string("./env/dist/etc:/azerothcore/env/dist/etc") == (
        "bind",
        "./env/dist/etc",
        "/azerothcore/env/dist/etc",
    )
    # A named volume's NAME is a recorded design difference (db-data vs ac-database), so it is
    # erased on both sides and only "a named volume at this path" survives.
    assert sc.volume_from_string("client-data:/azerothcore/env/dist/data/:ro") == (
        "volume",
        "<named>",
        "/azerothcore/env/dist/data",
    )
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv/modules", "target": "/azerothcore/modules"},
        root="/home/pk/srv",
    ) == ("bind", "./modules", "/azerothcore/modules")
    assert sc.volume_from_config(
        {"type": "volume", "source": "ac-database", "target": "/var/lib/mysql"}, root=None
    ) == ("volume", "<named>", "/var/lib/mysql")


def test_shape_from_plan_merges_the_three_files(tmp_path: Path) -> None:
    shape = sc.shape_from_plan(render(tmp_path))
    assert set(shape) == {
        "ac-database",
        "ac-db-import",
        "ac-client-data-init",
        "ac-authserver",
        "ac-worldserver",
    }
    world = shape["ac-worldserver"]
    # From the override: the modules mount and the bot population.
    assert ("bind", "./modules", "/azerothcore/modules") in world.volumes
    assert "AC_AI_PLAYERBOT_MAX_RANDOM_BOTS" in world.env_keys
    # From the build overlay.
    assert world.build == ("apps/docker/Dockerfile", "worldserver")
    assert shape["ac-database"].build is None
    assert world.ports == frozenset({("8085", 8085), ("7878", 7878)})
    assert world.depends_on == (
        ("ac-client-data-init", "service_completed_successfully"),
        ("ac-database", "service_healthy"),
        ("ac-db-import", "service_completed_successfully"),
    )


def test_compare_names_the_service_and_the_field() -> None:
    a = sc.Service(
        container_name="ac-authserver",
        image="ac-wotlk-authserver",
        ports=frozenset({("3724", 3724)}),
        volumes=frozenset(),
        env_keys=frozenset({"AC_LOGS_DIR"}),
        depends_on=(),
        build=None,
        restart="unless-stopped",
    )
    b = sc.Service(
        container_name="ac-authserver",
        image="ac-wotlk-authserver",
        ports=frozenset(),
        volumes=frozenset(),
        env_keys=frozenset({"AC_LOGS_DIR", "AC_CCACHE"}),
        depends_on=(),
        build=None,
        restart="unless-stopped",
    )
    problems = sc.compare({"ac-authserver": a}, {"ac-authserver": b})
    # AC_CCACHE is upstream build-time env the native stack deliberately drops; the port is real.
    # frozensets are shown as plain sets, so the line reads `{...} vs set()`, not `frozenset(...)`.
    assert problems == ["ac-authserver: ports {('3724', 3724)} vs set()"]
    assert sc.compare({"ac-authserver": a}, {}) == ["services: {'ac-authserver'} vs set()"]


# ---------------------------------------------------------------------------------------------
# What each normalisation rule still catches. One reference service, changed one field at a time.
# ---------------------------------------------------------------------------------------------

REFERENCE = sc.Service(
    container_name="ac-worldserver",
    image="ac-wotlk-worldserver",
    ports=frozenset({("8085", 8085), ("7878", 7878)}),
    volumes=frozenset(
        {
            ("bind", "./modules", "/azerothcore/modules"),
            ("volume", "<named>", "/azerothcore/env/dist/data"),
        }
    ),
    env_keys=frozenset({"AC_LOGS_DIR", "AC_DATA_DIR"}),
    depends_on=(("ac-database", "service_healthy"),),
    build=("apps/docker/Dockerfile", "worldserver"),
    restart="unless-stopped",
)


def theirs(**changes: object) -> list[str]:
    """`compare()` of REFERENCE against the same service with `changes` applied to it."""
    return sc.compare(
        {"ac-worldserver": REFERENCE}, {"ac-worldserver": replace(REFERENCE, **changes)}
    )


def test_an_unchanged_service_reports_nothing() -> None:
    """The baseline the rest of this section is measured against."""
    assert theirs() == []


# Rule: `${VAR:-default}` resolves to the default (compose with no `.env`).


def test_resolving_the_default_reads_it_rather_than_erasing_the_binding() -> None:
    """A different default is a different published port, not noise inside `${...}`."""
    assert sc.port_from_string("${DOCKER_DB_EXTERNAL_PORT:-13306}:3306") == ("13306", 3306)
    assert sc.resolve_defaults("a ${X:-1} b ${Y:-two} c") == "a 1 b two c"
    # A variable with no default is left alone rather than silently blanked: an unresolvable
    # reference must not compare equal to whatever the other side actually published.
    unresolvable = "${DOCKER_DB_EXTERNAL_PORT}:3306"
    assert sc.resolve_defaults(unresolvable) == unresolvable


def test_a_republished_port_is_reported() -> None:
    moved = frozenset({("13306", 8085), ("7878", 7878)})
    assert theirs(ports=moved) == [f"ac-worldserver: ports {set(REFERENCE.ports)} vs {set(moved)}"]


def test_a_dropped_port_is_reported() -> None:
    assert theirs(ports=frozenset({("8085", 8085)})) != []


# Rule: the image compares by its last path component — the registry prefix and the tag are the
# recorded design difference (`yulon.local/...:native-<id>` vs upstream's `acore/...:master`).


def test_a_changed_image_name_survives_the_prefix_and_tag_rule() -> None:
    assert sc.image_name("mysql:8.4") != sc.image_name("mariadb:11")
    assert theirs(image=sc.image_name("acore/ac-wotlk-authserver:master")) == [
        "ac-worldserver: image ac-wotlk-worldserver vs ac-wotlk-authserver"
    ]


def test_the_tag_rule_is_blind_to_a_base_image_version_which_is_its_cost() -> None:
    """PAID-FOR BLIND SPOT, recorded so nobody rediscovers it as a bug.

    Dropping the tag is what lets `yulon.local/ac-wotlk-worldserver:native-056ed20d` compare
    with `acore/ac-wotlk-worldserver:master`, and there is no version-shaped way to keep one
    and drop the other: the same rule therefore reads `mysql:8.4` and `mysql:5.7` as the same
    database. The database image is pinned in `catalog.json` and asserted by `test_catalog.py`;
    this vocabulary is not where that is defended.
    """
    assert sc.image_name("mysql:8.4") == sc.image_name("mysql:5.7")


# Rule: a named volume's NAME is erased (`db-data` here vs `ac-database` upstream); a bind's
# source is made relative to the install dir, because it is an absolute host path on the proven
# box and a pytest tmp dir here.


def test_a_changed_mount_target_is_reported() -> None:
    moved = frozenset(
        {
            ("bind", "./modules", "/azerothcore/mods"),
            ("volume", "<named>", "/azerothcore/env/dist/data"),
        }
    )
    assert theirs(volumes=moved) != []


def test_a_changed_bind_source_is_reported() -> None:
    moved = frozenset(
        {
            ("bind", "./env/dist/etc", "/azerothcore/modules"),
            ("volume", "<named>", "/azerothcore/env/dist/data"),
        }
    )
    assert theirs(volumes=moved) != []


def test_a_bind_where_a_named_volume_belongs_is_reported() -> None:
    """Erasing the NAME must not erase the KIND: client data on a host bind is a different
    install from client data in a managed volume."""
    swapped = frozenset(
        {
            ("bind", "./modules", "/azerothcore/modules"),
            ("bind", "./data", "/azerothcore/env/dist/data"),
        }
    )
    assert theirs(volumes=swapped) != []


def test_the_install_root_is_stripped_only_from_paths_actually_under_it() -> None:
    """A sibling directory that merely starts with the same characters stays absolute, so a
    mount of `/home/pk/srv-backup` can never be read as this install's own `./-backup`."""
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv-backup/modules", "target": "/azerothcore/modules"},
        root="/home/pk/srv",
    ) == ("bind", "/home/pk/srv-backup/modules", "/azerothcore/modules")
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv", "target": "/srv"}, root="/home/pk/srv"
    ) == ("bind", ".", "/srv")
    # A trailing slash on the install dir is the same install dir.
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv/modules", "target": "/azerothcore/modules"},
        root="/home/pk/srv/",
    ) == ("bind", "./modules", "/azerothcore/modules")


def test_volume_mode_is_not_compared_which_is_its_cost() -> None:
    """PAID-FOR BLIND SPOT. `compose config` reports read-only as its own `read_only` field
    while the short form spells it `:ro` in the mode column, and the two sources do not agree
    on the rest of that column (`z`, `Z`, `rw`) at all — the SELinux label this engine appends
    is a deliberate difference. So the mode is dropped, and a client-data mount that lost its
    `:ro` compares equal. `test_composegen.py` is where the rendered `:ro` is asserted."""
    assert sc.volume_from_string("client-data:/azerothcore/env/dist/data:ro") == (
        sc.volume_from_string("client-data:/azerothcore/env/dist/data")
    )


# Rule: environment compares by KEY. Values carry a per-install password and per-install paths,
# and the proven install's values are not this engine's to reproduce.


def test_a_key_only_the_native_stack_has_is_reported() -> None:
    assert theirs(env_keys=frozenset({"AC_LOGS_DIR"})) == [
        "ac-worldserver: env keys only in the native stack ['AC_DATA_DIR']"
    ]


def test_a_key_only_the_proven_install_has_is_reported() -> None:
    extra = REFERENCE.env_keys | {"AC_WORLD_DATABASE_INFO"}
    assert theirs(env_keys=extra) == [
        "ac-worldserver: env keys only in the proven install ['AC_WORLD_DATABASE_INFO']"
    ]


def test_the_build_time_allowance_forgives_only_the_proven_side() -> None:
    """The allow-list says "upstream sets these and the image reads none of them", which is a
    reason to stop reporting them as MISSING from ours. It is not a reason to accept them
    appearing in ours — that would mean the engine started writing build-time env."""
    ours = replace(REFERENCE, env_keys=REFERENCE.env_keys | {"AC_CCACHE", "AC_RESTARTER_URL"})
    problems = sc.compare({"ac-worldserver": ours}, {"ac-worldserver": REFERENCE})
    assert problems == [
        "ac-worldserver: env keys only in the native stack ['AC_CCACHE', 'AC_RESTARTER_URL']"
    ]
    assert sc.compare({"ac-worldserver": REFERENCE}, {"ac-worldserver": ours}) == []


def test_the_build_time_allowance_is_a_list_and_not_a_shape() -> None:
    """`AC_TEMP_DIR` looks as build-time-ish as `DATAPATH` and is not on the list, so it is
    still reported. The allowance is five names and one prefix that were checked in the image,
    not a guess about what a key sounds like."""
    assert theirs(env_keys=REFERENCE.env_keys | {"AC_TEMP_DIR"}) == [
        "ac-worldserver: env keys only in the proven install ['AC_TEMP_DIR']"
    ]
    assert "AC_TEMP_DIR" not in sc.BUILD_TIME_ENV
    assert not "AC_TEMP_DIR".startswith(sc.BUILD_TIME_ENV_PREFIXES)


def test_the_native_only_allowance_is_bound_to_one_service() -> None:
    """The playerbots DB belongs on the importer. The same key appearing on the worldserver
    when the proven install has no such key is a different fact and is still reported."""
    importer = replace(REFERENCE, env_keys=frozenset({"AC_PLAYERBOTS_DATABASE_INFO"}))
    bare = replace(REFERENCE, env_keys=frozenset())
    assert sc.compare({"ac-db-import": importer}, {"ac-db-import": bare}) == []
    assert sc.compare({"ac-worldserver": importer}, {"ac-worldserver": bare}) == [
        "ac-worldserver: env keys only in the native stack ['AC_PLAYERBOTS_DATABASE_INFO']"
    ]


def test_env_values_are_not_compared_which_is_its_cost() -> None:
    """PAID-FOR BLIND SPOT. Comparing values would compare a per-install DB password and the
    proven box's own paths, so only keys are compared — and a bot population of 0 against 500
    therefore reads as a match. `test_composegen.py` owns the bot-population value; this
    vocabulary owns "the key is there at all"."""
    five_hundred = _plan('environment:\n      AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "500"\n')
    none_at_all = _plan('environment:\n      AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "0"\n')
    assert sc.shape_from_plan(five_hundred) == sc.shape_from_plan(none_at_all)
    assert sc.shape_from_plan(five_hundred)["s"].env_keys == frozenset(
        {"AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"}
    )


# Rule: depends_on is sorted into (service, condition) pairs, because compose does not preserve
# the mapping's order and the short list form leaves the condition implicit.


def test_sorting_depends_on_does_not_erase_the_condition() -> None:
    assert theirs(depends_on=(("ac-database", "service_started"),)) == [
        "ac-worldserver: depends_on (('ac-database', 'service_healthy'),) vs "
        "(('ac-database', 'service_started'),)"
    ]


def test_a_missing_depends_on_edge_is_reported() -> None:
    both = (("ac-database", "service_healthy"), ("ac-db-import", "service_completed_successfully"))
    assert theirs(depends_on=both) != []


def test_the_short_list_form_means_service_started_not_no_condition() -> None:
    """Upstream's compose writes `depends_on: [ac-database]`, which is a weaker edge than
    `condition: service_healthy`. Reading the list form as the condition compose gives it is
    what makes that weakening visible instead of invisible."""
    assert sc.shape_from_plan(_plan("depends_on:\n      - ac-database\n"))["s"].depends_on == (
        ("ac-database", "service_started"),
    )


# Rule: `build` is (dockerfile, target); the context is not compared because it is the server
# dir on one side and the proven box's own path on the other.


def test_a_changed_build_target_is_reported() -> None:
    assert theirs(build=("apps/docker/Dockerfile", "authserver")) == [
        "ac-worldserver: build ('apps/docker/Dockerfile', 'worldserver') vs "
        "('apps/docker/Dockerfile', 'authserver')"
    ]


def test_a_changed_dockerfile_path_is_reported() -> None:
    assert theirs(build=("Dockerfile", "worldserver")) != []


def test_a_service_that_stopped_being_built_is_reported() -> None:
    assert theirs(build=None) != []


# Rule: nothing else on the service is compared. container_name and restart are, because both
# are contracts other code depends on.


def test_a_changed_container_name_is_reported() -> None:
    """The console attach, the repair probe and `docker exec ac-database mysqldump` all address
    containers by these names, so a rename is a break, not a cosmetic difference."""
    assert theirs(container_name="wotlk-worldserver") == [
        "ac-worldserver: container_name ac-worldserver vs wotlk-worldserver"
    ]


def test_a_changed_restart_policy_is_reported() -> None:
    assert theirs(restart="no") == ["ac-worldserver: restart unless-stopped vs no"]


def test_an_extra_service_is_reported() -> None:
    problems = sc.compare({}, {"ac-worldserver": REFERENCE})
    assert problems == ["services: set() vs {'ac-worldserver'}"]


def test_stop_grace_period_and_the_healthcheck_are_not_compared_which_is_its_cost() -> None:
    """PAID-FOR BLIND SPOT. `stop_grace_period: 5m` is this engine's own measured value and
    upstream has none, so comparing it would report a difference on every run forever; the
    healthcheck likewise differs by design (TCP by service name vs upstream's unix socket).
    Both were compared BY HAND on 2026-08-24 and are asserted in `test_composegen.py`. Neither
    reaches `Service`, so neither can ever be reported here — stated so that "the diff is
    clean" is never read as "the whole file matched"."""
    fields = set(sc.Service.__dataclass_fields__)
    assert "stop_grace_period" not in fields
    assert "healthcheck" not in fields
    plan = _plan('stop_grace_period: 5m\n    healthcheck:\n      test: "true"\n')
    assert sc.shape_from_plan(plan) == sc.shape_from_plan(_plan(""))


def _plan(body: str) -> composegen.ComposePlan:
    """A one-service ComposePlan whose base file carries `body` under `services: s:`."""
    return composegen.ComposePlan(
        base=f"services:\n  s:\n    image: x:1\n    restart: unless-stopped\n    {body}",
        override="services: {}\n",
        build="services: {}\n",
        dotenv={},
    )
