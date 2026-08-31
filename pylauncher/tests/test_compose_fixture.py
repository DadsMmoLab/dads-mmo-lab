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

Two facts about the rendered stack are asserted HERE rather than deferred to another file,
because nothing else in `tests/` asserts them and both are invisible to `Service`:
`ac-client-data-init`'s inline downloader (the largest deliberate divergence from upstream in
the whole stack) and the per-install project `name:` (what keys the named volumes, and so the
only reason erasing volume names is safe).
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

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
    assert sc.port_from_string("127.0.0.1:${DOCKER_DB_EXTERNAL_PORT:-3306}:3306") == (
        "3306",
        3306,
        "tcp",
    )
    assert sc.port_from_string("${DOCKER_AUTH_EXTERNAL_PORT:-3724}:3724") == ("3724", 3724, "tcp")
    # The SOAP default carries its own host prefix; the regex must swallow the inner colon.
    assert sc.port_from_string("${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878") == (
        "7878",
        7878,
        "tcp",
    )
    assert sc.port_from_config({"target": 3306, "published": "3306", "host_ip": "127.0.0.1"}) == (
        "3306",
        3306,
        "tcp",
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
        "rw",
    )
    # A named volume's NAME is a recorded design difference (db-data vs ac-database), so it is
    # erased on both sides and only "a named volume at this path" survives. Its READ-ONLY does
    # not: both sources can say it, so both are made to say it the same way.
    assert sc.volume_from_string("client-data:/azerothcore/env/dist/data/:ro") == (
        "volume",
        "<named>",
        "/azerothcore/env/dist/data",
        "ro",
    )
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv/modules", "target": "/azerothcore/modules"},
        root="/home/pk/srv",
    ) == ("bind", "./modules", "/azerothcore/modules", "rw")
    assert sc.volume_from_config(
        {"type": "volume", "source": "ac-database", "target": "/var/lib/mysql"}, root=None
    ) == ("volume", "<named>", "/var/lib/mysql", "rw")
    assert sc.volume_from_config(
        {
            "type": "volume",
            "source": "client-data",
            "target": "/azerothcore/env/dist/data",
            "read_only": True,
        },
        root=None,
    ) == ("volume", "<named>", "/azerothcore/env/dist/data", "ro")


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
    assert ("bind", "./modules", "/azerothcore/modules", "rw") in world.volumes
    # And from the base: client data is mounted READ-ONLY into the worldserver.
    assert ("volume", "<named>", "/azerothcore/env/dist/data", "ro") in world.volumes
    assert "AC_AI_PLAYERBOT_MAX_RANDOM_BOTS" in world.env_keys
    # From the build overlay.
    assert world.build == ("apps/docker/Dockerfile", "worldserver")
    assert shape["ac-database"].build is None
    assert world.ports == frozenset({("8085", 8085, "tcp"), ("7878", 7878, "tcp")})
    assert world.depends_on == (
        ("ac-client-data-init", "service_completed_successfully"),
        ("ac-database", "service_healthy"),
        ("ac-db-import", "service_completed_successfully"),
    )


def test_compare_names_the_service_and_the_field() -> None:
    a = sc.Service(
        container_name="ac-authserver",
        image="ac-wotlk-authserver",
        ports=frozenset({("3724", 3724, "tcp")}),
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
    assert problems == ["ac-authserver: ports {('3724', 3724, 'tcp')} vs set()"]
    assert sc.compare({"ac-authserver": a}, {}) == ["services: {'ac-authserver'} vs set()"]


# ---------------------------------------------------------------------------------------------
# What each normalisation rule still catches. One reference service, changed one field at a time.
# ---------------------------------------------------------------------------------------------

REFERENCE = sc.Service(
    container_name="ac-worldserver",
    image="ac-wotlk-worldserver",
    ports=frozenset({("8085", 8085, "tcp"), ("7878", 7878, "tcp")}),
    volumes=frozenset(
        {
            ("bind", "./modules", "/azerothcore/modules", "rw"),
            ("volume", "<named>", "/azerothcore/env/dist/data", "ro"),
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
    assert sc.port_from_string("${DOCKER_DB_EXTERNAL_PORT:-13306}:3306") == ("13306", 3306, "tcp")
    assert sc.resolve_defaults("a ${X:-1} b ${Y:-two} c") == "a 1 b two c"
    # A variable with no default is left alone rather than silently blanked: an unresolvable
    # reference must not compare equal to whatever the other side actually published.
    unresolvable = "${DOCKER_DB_EXTERNAL_PORT}:3306"
    assert sc.resolve_defaults(unresolvable) == unresolvable


def test_a_republished_port_is_reported() -> None:
    moved = frozenset({("13306", 8085, "tcp"), ("7878", 7878, "tcp")})
    assert theirs(ports=moved) == [
        "ac-worldserver: ports {('7878', 7878, 'tcp'), ('8085', 8085, 'tcp')} vs "
        "{('13306', 8085, 'tcp'), ('7878', 7878, 'tcp')}"
    ]


def test_a_dropped_port_is_reported() -> None:
    assert theirs(ports=frozenset({("8085", 8085, "tcp")})) != []


def test_a_port_that_changed_protocol_is_reported() -> None:
    """The protocol is the one thing in a port spec that varies with nothing — not the machine,
    not the install, not the project name — so it is read rather than dropped. A realm published
    on UDP answers no client, and both sources spell it: `/udp` in the short form, a `protocol`
    field from `compose config`."""
    assert sc.port_from_string("${DOCKER_AUTH_EXTERNAL_PORT:-3724}:3724/udp") == (
        "3724",
        3724,
        "udp",
    )
    assert sc.port_from_config({"target": 3724, "published": "3724", "protocol": "udp"}) == (
        "3724",
        3724,
        "udp",
    )
    udp = frozenset({("8085", 8085, "udp"), ("7878", 7878, "tcp")})
    assert theirs(ports=udp) != []


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
            ("bind", "./modules", "/azerothcore/mods", "rw"),
            ("volume", "<named>", "/azerothcore/env/dist/data", "ro"),
        }
    )
    assert theirs(volumes=moved) != []


def test_a_changed_bind_source_is_reported() -> None:
    moved = frozenset(
        {
            ("bind", "./env/dist/etc", "/azerothcore/modules", "rw"),
            ("volume", "<named>", "/azerothcore/env/dist/data", "ro"),
        }
    )
    assert theirs(volumes=moved) != []


def test_a_bind_where_a_named_volume_belongs_is_reported() -> None:
    """Erasing the NAME must not erase the KIND: client data on a host bind is a different
    install from client data in a managed volume."""
    swapped = frozenset(
        {
            ("bind", "./modules", "/azerothcore/modules", "rw"),
            ("bind", "./data", "/azerothcore/env/dist/data", "ro"),
        }
    )
    assert theirs(volumes=swapped) != []


def test_a_mount_that_lost_read_only_is_reported() -> None:
    """1.1 GB of client data is mounted `:ro` into the worldserver so a server that goes wrong
    cannot write into the map data every other install shares. Losing that flag is a regression,
    not a spelling difference, and both sources can say it — so the mode rule keeps `ro` and
    drops only the SELinux label characters it was introduced for."""
    writable = frozenset(
        {
            ("bind", "./modules", "/azerothcore/modules", "rw"),
            ("volume", "<named>", "/azerothcore/env/dist/data", "rw"),
        }
    )
    assert theirs(volumes=writable) == [
        "ac-worldserver: volumes "
        "{('bind', './modules', '/azerothcore/modules', 'rw'), "
        "('volume', '<named>', '/azerothcore/env/dist/data', 'ro')} vs "
        "{('bind', './modules', '/azerothcore/modules', 'rw'), "
        "('volume', '<named>', '/azerothcore/env/dist/data', 'rw')}"
    ]


def test_the_install_root_is_stripped_only_from_paths_actually_under_it() -> None:
    """A sibling directory that merely starts with the same characters stays absolute, so a
    mount of `/home/pk/srv-backup` can never be read as this install's own `./-backup`."""
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv-backup/modules", "target": "/azerothcore/modules"},
        root="/home/pk/srv",
    ) == ("bind", "/home/pk/srv-backup/modules", "/azerothcore/modules", "rw")
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv", "target": "/srv"}, root="/home/pk/srv"
    ) == ("bind", ".", "/srv", "rw")
    # A trailing slash on the install dir is the same install dir.
    assert sc.volume_from_config(
        {"type": "bind", "source": "/home/pk/srv/modules", "target": "/azerothcore/modules"},
        root="/home/pk/srv/",
    ) == ("bind", "./modules", "/azerothcore/modules", "rw")


def test_only_the_selinux_label_is_dropped_from_the_mode_column() -> None:
    """The mode rule exists to hide `{{BIND_LABEL}}`'s `:z`, which the proven install has no
    equivalent for. That justifies dropping `z` and `Z` and nothing else, so `ro` survives and a
    flag that is neither raises rather than being erased by a rule nobody wrote down."""
    labelled = sc.volume_from_string("./env/dist/etc:/azerothcore/env/dist/etc:z")
    assert labelled == sc.volume_from_string("./env/dist/etc:/azerothcore/env/dist/etc")
    assert labelled == sc.volume_from_string("./env/dist/etc:/azerothcore/env/dist/etc:Z")
    assert sc.volume_from_string("client-data:/data:ro,z") == (
        "volume",
        "<named>",
        "/data",
        "ro",
    )
    with pytest.raises(ValueError, match="unrecognised mount mode"):
        sc.volume_from_string("./modules:/azerothcore/modules:cached")


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


def test_the_fields_a_service_record_does_not_carry_are_owned_elsewhere() -> None:
    """PAID-FOR BLIND SPOT, and the full list of it. `stop_grace_period: 5m` is this engine's own
    measured value and upstream has none, so comparing it would report a difference on every run
    forever; the healthcheck likewise differs by design (TCP by service name vs upstream's unix
    socket); `user:` is `"0:0"` on Windows and a comment on Linux, where running as root would
    make every file the container writes root-owned ON THE HOST. All three are asserted on the
    rendered side by `test_composegen.py`, and none of them reaches `Service` — stated so that
    "the diff is clean" is never read as "the whole file matched"."""
    fields = set(sc.Service.__dataclass_fields__)
    assert not fields & {"stop_grace_period", "healthcheck", "user", "tty", "stdin_open"}
    body = 'stop_grace_period: 5m\n    user: "0:0"\n    healthcheck:\n      test: "true"\n'
    assert sc.shape_from_plan(_plan(body)) == sc.shape_from_plan(_plan(""))


def test_the_replaced_client_data_downloader_is_asserted_here_because_nobody_else_does(
    tmp_path: Path,
) -> None:
    """`ac-client-data-init`'s `command` is a ~45-line shell script that REPLACES upstream's
    `curl -L <url> > data.zip`, which cannot resume and so never finishes on a flaky link (it
    died at 66 MB of 1140 MB on the Ubuntu box, 2026-08-19). It is the largest deliberate
    divergence in the stack and it is invisible to `Service`, so a silent revert to upstream's
    downloader would pass every other test in this file. Nothing in `tests/` asserted it before
    this test: `grep -rl "inst_download_client_data" tests/` found nothing.

    The four properties below are the reason the replacement exists — resume, retry, a
    version-keyed partial that can never be spliced across two releases, and a refusal to
    extract a truncated archive — plus the fallback to upstream's own function when the version
    cannot be read, which is what keeps this never worse than what it replaced.
    """
    doc = yaml.safe_load(render(tmp_path).base)
    command = "\n".join(doc["services"]["ac-client-data-init"]["command"])
    assert "--continue-at -" in command
    assert "--retry 30" in command
    assert 'zip="$$data/data-$$ver.zip"' in command
    assert command.count("unzip -t") == 2
    assert "inst_download_client_data" in command


def test_the_rendered_stack_names_a_per_install_project(tmp_path: Path) -> None:
    """Erasing volume NAMES is only safe because the project `name:` keys them: `db-data` in
    project `yulon-wow-wotlk-<id>` is a different volume from `db-data` in another install's
    project. Without a `name:` compose falls back to the directory basename, two installs in
    similarly named folders share one database, and this whole vocabulary's central erasure
    becomes unsound. Nothing else in `tests/` checks it, so `compare_stack()` does."""
    stack = sc.stack_from_plan(render(tmp_path))
    assert stack.project is not None and stack.project.startswith("yulon-wow-wotlk-")
    assert sc.compare_stack(stack, stack) == []
    nameless = replace(stack, project=None)
    assert sc.compare_stack(nameless, stack) == [
        "project: the native stack has no `name:`, so its named volumes are keyed by the "
        "install directory and two installs can share one database"
    ]


def test_the_rendered_top_level_volumes_and_networks_are_plain(tmp_path: Path) -> None:
    """The two named volumes and the one network are declared with NO options, which is what
    makes them ordinary managed volumes on the docker root. That is the fact the next test
    protects."""
    stack = sc.stack_from_plan(render(tmp_path))
    assert stack.volumes == ((), ())
    assert stack.networks == ((),)


def test_a_relocated_named_volume_is_reported_by_the_stack_comparison(tmp_path: Path) -> None:
    """No per-service record can see this: a top-level `client-data:` that grows
    `driver_opts: {type: none, device: /somewhere/else, o: bind}` puts all 1.1 GB of client data
    on another disk, and both sides still reduce to
    `('volume', '<named>', '/azerothcore/env/dist/data', 'ro')`. The declaration's own name and
    compose's per-project `name:` are erased the same way the mounts' are; everything else about
    it is compared."""
    proven = sc.stack_from_plan(render(tmp_path))
    relocated = sc.stack_from_config(
        {
            "services": {},
            "name": "yulon-wow-wotlk-056ed20d",
            "volumes": {
                "db-data": {"name": "yulon-wow-wotlk-056ed20d_db-data"},
                "client-data": {
                    "name": "yulon-wow-wotlk-056ed20d_client-data",
                    "driver_opts": {"type": "none", "device": "/somewhere/else", "o": "bind"},
                },
            },
            "networks": {"ac-network": {"name": "yulon-wow-wotlk-056ed20d_ac-network"}},
        }
    )
    # The per-service view cannot tell these apart at all.
    assert sc.shape_from_config({"services": {}}) == {}
    assert sc.compare_stack(relocated, proven) == [
        "volumes: [(), ('driver_opts.device=/somewhere/else', 'driver_opts.o=bind', "
        "'driver_opts.type=none')] vs [(), ()]"
    ]


def test_an_external_or_redriven_declaration_is_reported(tmp_path: Path) -> None:
    """`external: true` hands the stack a volume somebody else made — with somebody else's data
    in it — and a changed network driver changes what the containers can reach. Neither touches
    a service."""
    proven = sc.stack_from_plan(render(tmp_path))
    external = replace(proven, volumes=((), ("external=True",)))
    assert sc.compare_stack(external, proven) == ["volumes: [(), ('external=True',)] vs [(), ()]"]
    redriven = replace(proven, networks=(("driver=macvlan",),))
    assert sc.compare_stack(redriven, proven) == ["networks: [('driver=macvlan',)] vs [()]"]


def test_a_problem_line_reads_the_same_way_twice() -> None:
    """The E.3/E.4 gates paste these lines into a record. `set` repr order varies between
    processes, so the members are sorted — the braces stay because a set is what they are."""
    ours = replace(REFERENCE, ports=frozenset())
    line = sc.compare({"ac-worldserver": REFERENCE}, {"ac-worldserver": ours})[0]
    assert line == ("ac-worldserver: ports {('7878', 7878, 'tcp'), ('8085', 8085, 'tcp')} vs set()")
    assert sc.compare({"b": REFERENCE, "a": REFERENCE}, {}) == ["services: {'a', 'b'} vs set()"]


def _plan(body: str) -> composegen.ComposePlan:
    """A one-service ComposePlan whose base file carries `body` under `services: s:`."""
    return composegen.ComposePlan(
        base=f"services:\n  s:\n    image: x:1\n    restart: unless-stopped\n    {body}",
        override="services: {}\n",
        build="services: {}\n",
        dotenv={},
    )
