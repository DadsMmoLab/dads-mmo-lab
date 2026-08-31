"""The generated WotLK stack against the proven yulon-ubuntu install (phase7-decisions, "The
compose-config reference").

Two sources, one vocabulary. `support_compose.shape_from_plan()` reads the files
`composegen.render()` produces; `shape_from_config()` reads what `docker compose config
--format json` says about a real install. Both become the same `Service` records, and
`compare()` reports every difference that is not a documented design difference (image prefix
and tag, a named volume's name at the mount, `stop_grace_period`, the playerbots DB on the
importer, upstream's build-time env). Nothing here runs a daemon.

TWO REAL INSTALLS, and they answer different questions.

* `tests/data/wotlk-compose-config.json` — the live gate's own NATIVE install on a clean Ubuntu
  box, captured after it reached `ready` (2026-08-31). Asks "does today's render still resolve to
  the stack that worked". Its pairing carries no rename registry: the engine named both volumes.
* `tests/data/wotlk-compose-config-script.json` — a server the BASH INSTALLER built, captured on
  Fedora the same day (project `wow-server-playerbots`). Asks "where do the two engines really
  differ", which is the question the whole vocabulary was built for and the one a
  native-against-native diff cannot ask: every documented design allowance fires against this
  fixture and none of them fires against the other. Three differences stand, all pinned in
  `SCRIPT_INSTALL_DIVERGENCES` with reasons.

Both have the project `name:` and the absolute install path stripped so neither names a machine.
`pyplan/checklist.md` carries what each first run reported.

The second half of this file is the bill for the first. Every rule the vocabulary applies is a
difference it will never report again, so each one is followed by a test that a MEANINGFUL
change in the same area is still caught, and — where a rule costs real sight — by a test that
pins exactly what it hides, so a reader can tell a paid-for blind spot from a missing feature.

One fact about the rendered stack is asserted HERE rather than deferred: `ac-client-data-init`'s
inline downloader, the largest deliberate divergence from upstream in the whole stack. It is
invisible to `Service`, and the only other thing carrying that text is the byte snapshot under
`tests/data/wotlk-rendered/` — a change-detector whose documented remedy is regeneration, which
reports that the file moved but never that the script has to be able to resume.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
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

FIXTURE = Path(__file__).parent / "data" / "wotlk-compose-config.json"
SCRIPT_FIXTURE = Path(__file__).parent / "data" / "wotlk-compose-config-script.json"


def render(server_dir: Path) -> composegen.ComposePlan:
    """Linux, no SELinux: `bind_label=""` is the byte-identical-to-before rendering."""
    return composegen.render(
        ENTRY, server_dir, templates_root=TEMPLATES, bind_label="", platform_id=lambda: "linux"
    )


def auto_loaded(plan: composegen.ComposePlan) -> composegen.ComposePlan:
    """The plan MINUS the build overlay — the two files compose loads on its own.

    The fixture is `docker compose config` run in the install directory, so it resolved
    `docker-compose.yml` plus `docker-compose.override.yml` and nothing else: the build overlay
    is deliberately never auto-loaded (its own header says so), which is what stops a
    post-install `up` from starting a multi-hour rebuild. Comparing a three-file render against
    that capture reported `build ... vs None` on all four built services — a difference in which
    documents were captured, not in the stack — so the same two documents are compared on both
    sides instead of forgiving a missing `build`, which would have hidden the engine losing the
    overlay. `test_dropping_the_build_overlay_drops_build_and_nothing_else` is the receipt.
    """
    return replace(plan, build="services: {}\n")


def test_the_rendered_stack_matches_the_proven_install(tmp_path: Path) -> None:
    """Off SELinux the rendered files are byte-identical to 6.2's, and this is the proof that
    they still RESOLVE to the proven install's stack — service by service, not by eye.

    Both halves of the vocabulary run: `compare()` sees the services, `compare_stack()` sees the
    top-level `volumes:`/`networks:` blocks that no per-service record can reach. Wiring only one
    would leave the other reading like coverage while comparing nothing — and the top-level block
    is where a `driver_opts.device` can move 1.1 GB of client data to another disk in silence.
    """
    plan = auto_loaded(render(tmp_path))
    proven = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert sc.compare(sc.shape_from_plan(plan), sc.shape_from_config(proven)) == []
    assert sc.compare_stack(sc.stack_from_plan(plan), sc.stack_from_config(proven)) == []


SCRIPT_INSTALL_DIVERGENCES = [
    # The bash install publishes MySQL on EVERY interface. Ours pins it to loopback, and the
    # launcher's own maintenance path talks to the database through `docker exec` rather than
    # over the network, so nothing needs the binding. This is the largest divergence either
    # capture contains and it was invisible until the host IP started being compared.
    "ac-database: ports {('127.0.0.1', '3306', 3306, 'tcp')} vs {('', '3306', 3306, 'tcp')}",
    # We mount the modules tree into the IMPORTER; the script install mounts it only into the
    # worldserver. The import applies each module's own db-auth/db-characters SQL as well as
    # AzerothCore's, which it cannot do without the tree — the base template says so where the
    # mount is written. Deliberate, and the reason the native stack has the playerbots schema
    # where the script install's repair gate found it missing.
    "ac-db-import: volumes "
    "{('bind', './env/dist/etc', '/azerothcore/env/dist/etc', 'rw'), "
    "('bind', './env/dist/logs', '/azerothcore/env/dist/logs', 'rw'), "
    "('bind', './modules', '/azerothcore/modules', 'rw')} vs "
    "{('bind', './env/dist/etc', '/azerothcore/env/dist/etc', 'rw'), "
    "('bind', './env/dist/logs', '/azerothcore/env/dist/logs', 'rw')}",
    # Same as the first line, for the SOAP admin port: theirs is on every interface, ours on
    # loopback. SOAP is an unauthenticated-by-design remote console in front of a GM account.
    "ac-worldserver: ports {('', '8085', 8085, 'tcp'), ('127.0.0.1', '7878', 7878, 'tcp')} vs "
    "{('', '7878', 7878, 'tcp'), ('', '8085', 8085, 'tcp')}",
]
"""Every difference between the rendered stack and the bash script install, PINNED rather than
forgiven. Each is a finding with a reason, and each reason is about the two installs genuinely
doing different things — never about making this test pass. A new line appearing here is a new
divergence to explain; a line disappearing is a change to one of the two."""


def test_the_rendered_stack_against_the_bash_script_install(tmp_path: Path) -> None:
    """The comparison this project set out to make, and the one the native fixture cannot make.

    `tests/data/wotlk-compose-config-script.json` is `docker compose config` from a server the
    BASH INSTALLER built (Fedora, 2026-08-31, project `wow-server-playerbots`) — upstream's own
    compose file, upstream's published `acore/*:master` images, a different distro and a different
    install path from the native capture. It is committed for one reason the native fixture cannot
    serve: every documented design difference in `support_compose.py` — the image registry and
    tag, the two volume names, upstream's build-time env, the playerbots DB the importer only has
    here — is exercised by NOTHING in a native-against-native comparison. Against this fixture
    each one fires, so an allow-list that stopped describing reality would be caught instead of
    reading like coverage.

    What it is NOT is a regression net for upstream. Upstream's file is a third party's and will
    change; when it does, the difference is a finding to read, not a red to silence. Three
    differences stand today and all three are pinned above with their reasons.
    """
    plan = render(tmp_path)
    script = json.loads(SCRIPT_FIXTURE.read_text(encoding="utf-8"))
    aliases = sc.SCRIPT_INSTALL_VOLUME_NAMES
    assert sc.compare(sc.shape_from_plan(plan), sc.shape_from_config(script)) == (
        SCRIPT_INSTALL_DIVERGENCES
    )
    assert (
        sc.compare_stack(
            sc.stack_from_plan(plan, volume_aliases=aliases),
            sc.stack_from_config(script, volume_aliases=aliases),
        )
        == []
    )


def test_the_script_install_exercises_every_allowance_the_native_fixture_cannot(
    tmp_path: Path,
) -> None:
    """The receipt for committing a second fixture: each design difference the vocabulary forgives
    is REAL in this pairing and absent from the other, so none of them is an allowance for a thing
    that never happens.

    Take any one away and the comparison above stops being clean — which is what a rule earning
    its place looks like.
    """
    script = json.loads(SCRIPT_FIXTURE.read_text(encoding="utf-8"))
    native = json.loads(FIXTURE.read_text(encoding="utf-8"))
    services = script["services"]
    # The image registry and tag rule: upstream's published images against our locally built ones.
    assert services["ac-worldserver"]["image"] == "acore/ac-wotlk-worldserver:master"
    assert native["services"]["ac-worldserver"]["image"].startswith("yulon.local/")
    assert sc.image_name(services["ac-worldserver"]["image"]) == "ac-wotlk-worldserver"
    # The volume rename: confirmed by capture, not by a note in a checklist.
    assert sorted(script["volumes"]) == ["ac-client-data", "ac-database"]
    assert sorted(native["volumes"]) == ["client-data", "db-data"]
    # Upstream's build-time env, on the runtime services, exactly as BUILD_TIME_ENV describes it.
    theirs = set(services["ac-worldserver"]["environment"])
    ours = set(sc.shape_from_plan(render(tmp_path))["ac-worldserver"].env_keys)
    assert theirs - ours == sc.BUILD_TIME_ENV | {
        key for key in theirs if key.startswith(sc.BUILD_TIME_ENV_PREFIXES)
    }
    # And the playerbots schema on the importer, which only the native stack supplies.
    assert "AC_PLAYERBOTS_DATABASE_INFO" not in services["ac-db-import"]["environment"]
    assert ("ac-db-import", "AC_PLAYERBOTS_DATABASE_INFO") in sc.NATIVE_ONLY_ENV


def test_what_the_script_capture_shows_and_this_vocabulary_still_cannot_see() -> None:
    """DECLARED SILENCES, each with a real example now rather than a hypothetical one. Stated so
    that "three differences" is never read as "three differences exist".

    * `build.args` — upstream passes `USER_ID`/`GROUP_ID`/`DOCKER_USER` into the image build and
      the native stack passes none. `_build()` compares the dockerfile and the target only.
    * `build.context` — absolute on the script side (C1's predicted surprise, in the flesh, but
      on `context` and not on `dockerfile`, which is relative on both). Not compared, by design:
      it is the install dir either way.
    * the SELinux label — the script install carries `bind: {selinux: Z}` on exactly ONE mount,
      the worldserver's modules tree, and nothing on its other five binds, while this engine
      labels every `./` bind with `z` when SELinux is on. `volume_from_config()` does not read
      `bind.selinux` and `_mount_mode()` drops `z`/`Z` on the other side, so the two schemes are
      invisible to each other. Recorded in `pyplan/checklist.md`.
    * the healthcheck — theirs addresses MySQL over the local socket, ours over TCP by service
      name from the compose network. Owned by `test_composegen.py`.
    """
    script = json.loads(SCRIPT_FIXTURE.read_text(encoding="utf-8"))
    world = script["services"]["ac-worldserver"]
    assert set(world["build"]["args"]) == {"DOCKER_USER", "GROUP_ID", "USER_ID"}
    assert world["build"]["context"] == "." and world["build"]["dockerfile"] == (
        "apps/docker/Dockerfile"
    )
    assert sc.Service.__dataclass_fields__.keys().isdisjoint({"build_args", "healthcheck"})
    labelled = [v for v in world["volumes"] if (v.get("bind") or {}).get("selinux")]
    assert [v["target"] for v in labelled] == ["/azerothcore/modules"]
    assert sc.volume_from_config(labelled[0], root=None) == (
        "bind",
        "./modules",
        "/azerothcore/modules",
        "rw",
    )


def test_dropping_the_build_overlay_drops_build_and_nothing_else(tmp_path: Path) -> None:
    """PAID-FOR BLIND SPOT, and the fence around it. The fixture pins the RUNTIME stack, so it
    can never report a change to `docker-compose.build.yml`; that file is owned by
    `test_shape_from_plan_merges_the_three_files` (its dockerfile and all four targets), by
    `test_composegen.py` and by the byte snapshot. What is asserted here is that `auto_loaded()`
    removes exactly `build` and no other field, so it cannot quietly hide a second difference.
    """
    full = sc.shape_from_plan(render(tmp_path))
    runtime = sc.shape_from_plan(auto_loaded(render(tmp_path)))
    assert set(full) == set(runtime)
    assert full["ac-worldserver"].build == ("apps/docker/Dockerfile", "worldserver")
    assert all(svc.build is None for svc in runtime.values())
    assert {name: replace(svc, build=None) for name, svc in full.items()} == runtime


def test_the_fixture_carries_no_machine_identity() -> None:
    """A fixture that names /home/pk or the folder's install id would drift the first time
    somebody captured it from another box; the capture step strips both.

    The install id appears in THREE places in a raw `compose config`: the top-level `name:`, the
    `name:` compose fills into every top-level volume and network declaration (`<project>_db-data`),
    and the image tag the engine builds (`:native-<id>`). The first two are stripped, because the
    comparison reads them — `_options()` drops a declaration's `name`, so nothing is lost. The
    third is left as captured, and is asserted here to be the only survivor: `image_name()` drops
    the tag by a documented rule, so it cannot drift a result, and leaving it keeps the fixture
    recognisably a capture rather than an edited document.
    """
    text = FIXTURE.read_text(encoding="utf-8")
    data = json.loads(text)
    install_id = "243c46e3"
    assert "/home/" not in text
    assert "name" not in data
    assert all("name" not in body for body in data["volumes"].values())
    assert all("name" not in body for body in data["networks"].values())
    assert install_id not in json.dumps({k: v for k, v in data.items() if k != "services"})
    for name, svc in data["services"].items():
        assert install_id not in json.dumps({k: v for k, v in svc.items() if k != "image"}), name
    assert set(data["services"]) == {
        "ac-database",
        "ac-db-import",
        "ac-client-data-init",
        "ac-authserver",
        "ac-worldserver",
    }


def test_a_captured_compose_config_matches_the_fixture() -> None:
    """The live gate's diff: point YULON_COMPOSE_CONFIG at `docker compose config --format json`
    captured from a NATIVE install and YULON_COMPOSE_ROOT at that install's absolute server dir.
    Skips, loudly, when not asked for.

    Capture it the way the fixture was captured: in the install directory, with NO `-f` flags, so
    compose resolves the two files it auto-loads and not the build overlay. Hand it the RAW
    output — the `name:` is not stripped from a live capture, because `compare_stack()` refuses a
    stack that has none and that refusal is half of what makes erasing volume names safe.
    """
    captured = os.environ.get("YULON_COMPOSE_CONFIG")
    if not captured:
        pytest.skip("set YULON_COMPOSE_CONFIG=<compose config json> to diff a live install")
    root = os.environ.get("YULON_COMPOSE_ROOT")
    raw = json.loads(Path(captured).read_text(encoding="utf-8"))
    proven = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert sc.compare(sc.shape_from_config(raw, root=root), sc.shape_from_config(proven)) == []
    assert (
        sc.compare_stack(sc.stack_from_config(raw, root=root), sc.stack_from_config(proven)) == []
    )


def test_port_strings_resolve_compose_defaults() -> None:
    assert sc.port_from_string("127.0.0.1:${DOCKER_DB_EXTERNAL_PORT:-3306}:3306") == (
        "127.0.0.1",
        "3306",
        3306,
        "tcp",
    )
    assert sc.port_from_string("${DOCKER_AUTH_EXTERNAL_PORT:-3724}:3724") == (
        "",
        "3724",
        3724,
        "tcp",
    )
    # The SOAP default carries its own host prefix; the regex must swallow the inner colon, and
    # the prefix survives as the host IP rather than being thrown away with it.
    assert sc.port_from_string("${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878") == (
        "127.0.0.1",
        "7878",
        7878,
        "tcp",
    )
    assert sc.port_from_config({"target": 3306, "published": "3306", "host_ip": "127.0.0.1"}) == (
        "127.0.0.1",
        "3306",
        3306,
        "tcp",
    )


def test_a_port_published_on_every_interface_is_not_the_same_as_a_loopback_one() -> None:
    """The rule that pays for reading the host IP, and the divergence it found. A published port
    with no host prefix is bound on EVERY interface — which is how the bash script install
    publishes MySQL and the SOAP console, and how this stack publishes neither. Both sources
    spell the absence the same way (`""`), so the two meet without an allowance.
    """
    assert sc.port_from_string("${DOCKER_DB_EXTERNAL_PORT:-3306}:3306") == ("", "3306", 3306, "tcp")
    assert sc.port_from_config({"target": 3306, "published": "3306"}) == ("", "3306", 3306, "tcp")
    assert sc.port_from_string("127.0.0.1:3306:3306") != sc.port_from_string("3306:3306")
    exposed = frozenset({("", "8085", 8085, "tcp"), ("", "7878", 7878, "tcp")})
    assert theirs(ports=exposed) == [
        "ac-worldserver: ports {('', '8085', 8085, 'tcp'), ('127.0.0.1', '7878', 7878, 'tcp')} "
        "vs {('', '7878', 7878, 'tcp'), ('', '8085', 8085, 'tcp')}"
    ]


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
    # From the build overlay: the dockerfile AND every one of the four service-to-target pairs.
    # Nothing else pins the pairing — `test_composegen.py` counts four targets without binding
    # any of them to a service, and the byte snapshot's remedy for a mismatch is regeneration —
    # so a worldserver built from the authserver stage would otherwise pass everything but this.
    assert {name: svc.build for name, svc in shape.items()} == {
        "ac-worldserver": ("apps/docker/Dockerfile", "worldserver"),
        "ac-authserver": ("apps/docker/Dockerfile", "authserver"),
        "ac-db-import": ("apps/docker/Dockerfile", "db-import"),
        "ac-client-data-init": ("apps/docker/Dockerfile", "client-data"),
        "ac-database": None,
    }
    assert world.ports == frozenset({("", "8085", 8085, "tcp"), ("127.0.0.1", "7878", 7878, "tcp")})
    assert world.depends_on == (
        ("ac-client-data-init", "service_completed_successfully"),
        ("ac-database", "service_healthy"),
        ("ac-db-import", "service_completed_successfully"),
    )


def test_compare_names_the_service_and_the_field() -> None:
    a = sc.Service(
        container_name="ac-authserver",
        image="ac-wotlk-authserver",
        ports=frozenset({("", "3724", 3724, "tcp")}),
        volumes=frozenset(),
        networks=frozenset({"ac-network"}),
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
        networks=frozenset({"ac-network"}),
        env_keys=frozenset({"AC_LOGS_DIR", "AC_CCACHE"}),
        depends_on=(),
        build=None,
        restart="unless-stopped",
    )
    problems = sc.compare({"ac-authserver": a}, {"ac-authserver": b})
    # AC_CCACHE is upstream build-time env the native stack deliberately drops; the port is real.
    # frozensets are shown as plain sets, so the line reads `{...} vs set()`, not `frozenset(...)`.
    assert problems == ["ac-authserver: ports {('', '3724', 3724, 'tcp')} vs set()"]
    assert sc.compare({"ac-authserver": a}, {}) == ["services: {'ac-authserver'} vs set()"]


# ---------------------------------------------------------------------------------------------
# What each normalisation rule still catches. One reference service, changed one field at a time.
# ---------------------------------------------------------------------------------------------

REFERENCE = sc.Service(
    container_name="ac-worldserver",
    image="ac-wotlk-worldserver",
    ports=frozenset({("", "8085", 8085, "tcp"), ("127.0.0.1", "7878", 7878, "tcp")}),
    volumes=frozenset(
        {
            ("bind", "./modules", "/azerothcore/modules", "rw"),
            ("volume", "<named>", "/azerothcore/env/dist/data", "ro"),
        }
    ),
    networks=frozenset({"ac-network"}),
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
    assert sc.port_from_string("${DOCKER_DB_EXTERNAL_PORT:-13306}:3306") == (
        "",
        "13306",
        3306,
        "tcp",
    )
    assert sc.resolve_defaults("a ${X:-1} b ${Y:-two} c") == "a 1 b two c"
    # A variable with no default is left alone rather than silently blanked: an unresolvable
    # reference must not compare equal to whatever the other side actually published.
    unresolvable = "${DOCKER_DB_EXTERNAL_PORT}:3306"
    assert sc.resolve_defaults(unresolvable) == unresolvable


def test_a_republished_port_is_reported() -> None:
    moved = frozenset({("", "13306", 8085, "tcp"), ("127.0.0.1", "7878", 7878, "tcp")})
    assert theirs(ports=moved) == [
        "ac-worldserver: ports {('', '8085', 8085, 'tcp'), ('127.0.0.1', '7878', 7878, 'tcp')} "
        "vs {('', '13306', 8085, 'tcp'), ('127.0.0.1', '7878', 7878, 'tcp')}"
    ]


def test_a_dropped_port_is_reported() -> None:
    assert theirs(ports=frozenset({("", "8085", 8085, "tcp")})) != []


def test_a_port_that_changed_protocol_is_reported() -> None:
    """The protocol is the one thing in a port spec that varies with nothing — not the machine,
    not the install, not the project name — so it is read rather than dropped. A realm published
    on UDP answers no client, and both sources spell it: `/udp` in the short form, a `protocol`
    field from `compose config`."""
    assert sc.port_from_string("${DOCKER_AUTH_EXTERNAL_PORT:-3724}:3724/udp") == (
        "",
        "3724",
        3724,
        "udp",
    )
    assert sc.port_from_config({"target": 3724, "published": "3724", "protocol": "udp"}) == (
        "",
        "3724",
        3724,
        "udp",
    )
    udp = frozenset({("", "8085", 8085, "udp"), ("127.0.0.1", "7878", 7878, "tcp")})
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


# Rule: a service naming no `networks:` is read as compose's implicit `default`, and the
# rendered top level grows a `default:` declaration when some service names none. `compose
# config` materialises both; the file can spell neither.


def test_a_service_that_left_the_stack_network_is_reported() -> None:
    """The reason the absence is read out rather than left empty. A worldserver that lost
    `networks: [ac-network]` lands on the implicit default bridge with every other container on
    the machine — and would have compared equal to one that was never attached, had both spelled
    "no networks" as the empty set."""
    assert theirs(networks=frozenset({"default"})) == [
        "ac-worldserver: networks {'ac-network'} vs {'default'}"
    ]
    assert theirs(networks=frozenset({"ac-network", "extra"})) != []


def test_the_two_spellings_of_a_service_network_meet(tmp_path: Path) -> None:
    """The rendered file writes `networks: [ac-network]`; `compose config` writes a mapping of
    name to per-network aliases. Only the names are read, and a service that names none is read
    as `default` on both sides — which is exactly what the capture shows for
    `ac-client-data-init`, the one service in the stack that is not on `ac-network`."""
    shape = sc.shape_from_plan(render(tmp_path))
    assert shape["ac-worldserver"].networks == frozenset({"ac-network"})
    assert shape["ac-client-data-init"].networks == frozenset({"default"})
    captured = json.loads(FIXTURE.read_text(encoding="utf-8"))["services"]
    assert sc.shape_from_config({"services": captured})["ac-client-data-init"].networks == (
        frozenset({"default"})
    )
    assert sc.shape_from_plan(_plan("networks: [ac-network]\n"))["s"].networks == frozenset(
        {"ac-network"}
    )


def test_the_synthesised_default_declaration_is_modelled_and_not_erased(tmp_path: Path) -> None:
    """The `default:` block is added to the rendered top level with NO options, so it models
    what compose does without forgiving what a declaration says. A captured `default:` that grew
    a driver or a `driver_opts.device` is still a reported difference — and a plan where every
    service names a network grows no `default:` at all, so the synthesis cannot become an
    unconditional blank cheque."""
    stack = sc.stack_from_plan(render(tmp_path))
    redriven = _config({"db-data": {}, "client-data": {}}, networks={"default": {"driver": "host"}})
    assert sc.compare_stack(stack, redriven) == ["networks: default [] vs ['driver=host']"]
    attached = composegen.ComposePlan(
        base="networks:\n  ac-network:\nservices:\n  s:\n    networks: [ac-network]\n",
        override="services: {}\n",
        build="services: {}\n",
        dotenv={},
    )
    assert sc.stack_from_plan(attached).networks == (("ac-network", ()),)


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
    """`ac-client-data-init`'s `entrypoint` + `command` are a ~45-line bash script that REPLACES
    upstream's `curl -L <url> > data.zip`, which cannot resume and so never finishes on a flaky
    link (it died at 66 MB of 1140 MB on the Ubuntu box, 2026-08-19). It is the largest
    deliberate divergence in the stack and it is invisible to `Service`, so a silent revert to
    upstream's downloader would pass every other test in this file.

    Its text IS in the byte snapshot at `tests/data/wotlk-rendered/docker-compose.yml`, so this
    is not the only thing that would go red. But that snapshot is a CHANGE-DETECTOR whose own
    docstring says to regenerate it "when a WotLK template change is the point of the commit" —
    it reports that the file moved, and the documented remedy makes the new text the expected
    text. It cannot say the script has to be able to resume. That is what the assertions below
    say, one per property the replacement exists for: bash (without the `entrypoint` this stops
    being a script at all and becomes argv for the image's own entrypoint), resume, retry, a
    version-keyed partial that can never be spliced across two releases, a refusal to extract a
    truncated archive — and the fallback to upstream's own function when the version cannot be
    read, which is what keeps this never worse than what it replaced.
    """
    service = yaml.safe_load(render(tmp_path).base)["services"]["ac-client-data-init"]
    assert service["entrypoint"] == ["/bin/bash", "-c"]
    command = "\n".join(service["command"])
    assert "--continue-at -" in command
    assert "--retry 30" in command
    assert 'zip="$$data/data-$$ver.zip"' in command
    assert command.count("unzip -t") == 2
    assert "inst_download_client_data" in command


def test_a_stack_without_a_project_name_is_refused(tmp_path: Path) -> None:
    """Erasing volume NAMES is only sound because the project `name:` keys them: `db-data` in
    project `yulon-wow-wotlk-<id>` is a different volume from `db-data` in another install's
    project. Without a `name:` compose falls back to the directory basename, two installs in
    similarly named folders share one database, and this vocabulary's central erasure stops
    being safe.

    `test_composegen.py` owns the rendered name — that it is per-directory and travels in the
    file (`test_the_project_name_is_per_directory_and_travels_in_the_file`) and its exact value
    in the byte snapshot. What is asserted here is the other half: that the COMPARISON refuses a
    stack that lost it, rather than quietly reporting a clean diff built on an unsound erasure.
    """
    stack = sc.stack_from_plan(render(tmp_path))
    assert stack.project is not None and stack.project.startswith("yulon-wow-wotlk-")
    assert sc.compare_stack(stack, _proven()) == []
    nameless = replace(stack, project=None)
    assert sc.compare_stack(nameless, _proven()) == [
        "project: the native stack has no `name:`, so its named volumes are keyed by the "
        "install directory and two installs can share one database"
    ]


def test_the_rendered_top_level_volumes_and_networks_are_plain(tmp_path: Path) -> None:
    """The two named volumes and the two networks are declared with NO options, which is what
    makes them ordinary managed volumes on the docker root. That is the fact the next tests
    protect. The volumes appear under the names the file writes, because the rename registry is
    empty; `default` is the implicit network compose materialises for `ac-client-data-init`."""
    stack = sc.stack_from_plan(render(tmp_path))
    assert stack.volumes == (("client-data", ()), ("db-data", ()))
    assert stack.networks == (("ac-network", ()), ("default", ()))
    # What the file itself says, which is what the translation registry is checked against.
    assert stack.declared_volumes == ("client-data", "db-data")


def _config(
    volumes: dict[str, object],
    networks: Mapping[str, Mapping[str, object]] | None = None,
    aliases: Mapping[str, str] = sc.NO_VOLUME_ALIASES,
) -> sc.Stack:
    """A `compose config` top level with the project-prefixed `name:` compose writes on each.

    `default` is declared alongside `ac-network` because that is what the real capture shows:
    `ac-client-data-init` names no network, so compose materialises the implicit one.
    """
    project = "yulon-wow-wotlk-056ed20d"
    declared: Mapping[str, Mapping[str, object]] = {
        "ac-network": {},
        "default": {},
        **(networks or {}),
    }
    return sc.stack_from_config(
        {
            "services": {},
            "name": project,
            "volumes": {
                name: {"name": f"{project}_{name}", **body}  # type: ignore[dict-item]
                for name, body in volumes.items()
            },
            "networks": {
                name: {"name": f"{project}_{name}", **body} for name, body in declared.items()
            },
        },
        volume_aliases=aliases,
    )


def _proven(options: dict[str, object] | None = None) -> sc.Stack:
    """The proven install's top level, spelled the way the captured fixture spells it.

    Its two volumes carry OUR names, `db-data` and `client-data`, because the reference is a
    native install and the engine rendered them — see `DESIGN_VOLUME_NAMES`, which the capture
    emptied. `options` attaches a body to one of them.
    """
    volumes: dict[str, object] = {"db-data": {}, "client-data": {}}
    volumes.update(options or {})
    return _config(volumes)


RELOCATION = {"type": "none", "device": "/somewhere/else", "o": "bind"}


def test_a_relocated_named_volume_is_reported_by_the_stack_comparison(tmp_path: Path) -> None:
    """No per-service record can see this: a top-level `client-data:` that grows
    `driver_opts: {type: none, device: /somewhere/else, o: bind}` puts all 1.1 GB of client data
    on another disk, and both sides still reduce to
    `('volume', '<named>', '/azerothcore/env/dist/data', 'ro')`. Only compose's per-project
    `name:` is dropped from a declaration; the rest of it is compared, under its own key."""
    proven = _proven()
    relocated = _config({"db-data": {}, "client-data": {"driver_opts": RELOCATION}})
    # The per-service view cannot tell these apart at all.
    assert sc.shape_from_config({"services": {}}) == {}
    assert sc.compare_stack(relocated, proven) == [
        "volumes: client-data ['driver_opts.device=/somewhere/else', 'driver_opts.o=bind', "
        "'driver_opts.type=none'] vs []"
    ]


def test_the_same_options_on_the_other_store_is_a_different_stack() -> None:
    """The reason the declaration's KEY is kept. These two differ only in WHICH of the two 1.1 GB
    stores was moved off the docker root — one is the client data, the other is the database —
    and a comparison that erased the names would call them identical."""
    client_data_moved = _config({"db-data": {}, "client-data": {"driver_opts": RELOCATION}})
    database_moved = _proven({"db-data": {"driver_opts": RELOCATION}})
    assert sc.compare_stack(client_data_moved, database_moved) == [
        "volumes: client-data ['driver_opts.device=/somewhere/else', 'driver_opts.o=bind', "
        "'driver_opts.type=none'] vs []",
        "volumes: db-data [] vs ['driver_opts.device=/somewhere/else', "
        "'driver_opts.o=bind', 'driver_opts.type=none']",
    ]


def test_each_registry_describes_the_pairing_it_belongs_to_and_only_that_one() -> None:
    """C9/C10, settled by two captures rather than by a note.

    The recorded rename `db-data` -> `ac-database` / `client-data` -> `ac-client-data` came from
    a `checklist.md` line and a gate log, both describing UPSTREAM's script install. Against the
    script capture it is CONFIRMED, name for name. Against the native capture it describes
    nothing — the engine rendered both volumes itself — so that pairing carries no registry and
    compares the names as written.

    The registry therefore belongs to a pairing, not to the module: one table applied to both
    fixtures would have been wrong for one of them whichever way it was set.
    """
    assert dict(sc.SCRIPT_INSTALL_VOLUME_NAMES) == {
        "db-data": "ac-database",
        "client-data": "ac-client-data",
    }
    assert dict(sc.NO_VOLUME_ALIASES) == {} and sc.DESIGN_VOLUME_NAMES is sc.NO_VOLUME_ALIASES
    script = json.loads(SCRIPT_FIXTURE.read_text(encoding="utf-8"))
    native = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert sc.stack_from_config(script).declared_volumes == ("ac-client-data", "ac-database")
    assert sc.stack_from_config(native).declared_volumes == ("client-data", "db-data")
    assert sc.stack_from_config(native).volumes == (("client-data", ()), ("db-data", ()))


def test_two_sides_reduced_with_different_registries_are_refused() -> None:
    """The reason a `Stack` carries the registry it was built with. Comparing a side translated by
    the script-install table against one that was not produces names from two vocabularies, and
    every line below it would be built on that. Reporting the mismatch is the only honest answer;
    a clean-looking diff would be the dangerous one.
    """
    ours = _config({"db-data": {}, "client-data": {}}, aliases=sc.SCRIPT_INSTALL_VOLUME_NAMES)
    theirs = _config({"ac-database": {}, "ac-client-data": {}})
    assert sc.compare_stack(ours, theirs) == [
        "volume aliases: the two sides were reduced with different rename registries, "
        "[['client-data', 'ac-client-data'], ['db-data', 'ac-database']] vs []"
    ]


def test_a_renamed_named_volume_is_caught_at_the_top_level_not_at_the_mount() -> None:
    """The mount rule's cost, and what pays for it. A named volume's NAME is erased at the mount
    (`<named>`), so the per-service view cannot tell `db-data` mounted at the data path from
    `client-data` mounted there. The DECLARATION is compared by name, exactly, and that is where
    a renamed or swapped store is reported — which is only true because the rename registry is
    empty, so no name is translated on its way through.
    """
    assert sc.volume_from_string("db-data:/azerothcore/env/dist/data") == sc.volume_from_string(
        "client-data:/azerothcore/env/dist/data"
    )
    renamed = _config({"mysql-data": {}, "client-data": {}})
    assert sc.compare_stack(renamed, _proven()) == [
        "volumes: only in the native stack ['mysql-data']",
        "volumes: only in the proven install ['db-data']",
    ]


def test_the_registered_rename_still_has_to_stay_true(tmp_path: Path) -> None:
    """The condition on holding a registry at all: an entry that quietly stops matching is worse
    than no entry. While it describes both sides the rename is translated and reported as nothing
    — the script fixture's whole `compare_stack()` is clean on that basis. The moment either side
    stops declaring its own half AS WRITTEN, the guard names both spellings and what that side
    really declares, instead of letting the diff read as one volume ADDED and another REMOVED.

    Provenance is what the guard asks, never the translated result: our side renaming `db-data`
    to `mysql-data` while some unrelated volume happens to be called `ac-database` is a dead
    entry, and asking "is the target present?" answered yes for the wrong reason.
    """
    table = sc.SCRIPT_INSTALL_VOLUME_NAMES
    script = json.loads(SCRIPT_FIXTURE.read_text(encoding="utf-8"))
    live = sc.stack_from_config(script, volume_aliases=table)
    assert sc.compare_stack(sc.stack_from_plan(render(tmp_path), volume_aliases=table), live) == []
    # Our side stopped declaring the source name; a coincidental `ac-database` does not save it.
    coincidence = _config({"mysql-data": {}, "client-data": {}, "ac-database": {}}, aliases=table)
    assert sc.compare_stack(coincidence, live) == [
        "volumes: the recorded rename `db-data` -> `ac-database` no longer describes the native "
        "stack, which declares ['ac-database', 'client-data', 'mysql-data']",
        "volumes: only in the native stack ['mysql-data']",
    ]
    # And the other half: the reference renamed the target, so the entry stops describing it and
    # the untranslated name arrives as one volume ADDED and another REMOVED.
    ours = _config({"db-data": {}, "client-data": {}}, aliases=table)
    renamed = _config({"acore-database": {}, "ac-client-data": {}}, aliases=table)
    assert sc.compare_stack(ours, renamed) == [
        "volumes: the recorded rename `db-data` -> `ac-database` no longer describes the proven "
        "install, which declares ['ac-client-data', 'acore-database']",
        "volumes: only in the native stack ['ac-database']",
        "volumes: only in the proven install ['acore-database']",
    ]


def test_a_stack_record_cannot_be_built_without_its_provenance() -> None:
    """`declared_volumes` is not optional, so the staleness rule can never be asked of a record
    that has no answer."""
    with pytest.raises(TypeError):
        sc.Stack(project="p", volumes=(), networks=())  # type: ignore[call-arg]


def test_an_external_or_redriven_declaration_is_reported(tmp_path: Path) -> None:
    """`external: true` hands the stack a volume somebody else made — with somebody else's data
    in it — and a changed network driver changes what the containers can reach. Neither touches
    a service."""
    proven = _proven()
    external = _config({"db-data": {}, "client-data": {"external": True}})
    assert sc.compare_stack(external, proven) == ["volumes: client-data ['external=True'] vs []"]
    redriven = _config(
        {"db-data": {}, "client-data": {}}, networks={"ac-network": {"driver": "macvlan"}}
    )
    assert sc.compare_stack(redriven, proven) == ["networks: ac-network ['driver=macvlan'] vs []"]


def test_a_problem_line_reads_the_same_way_twice() -> None:
    """The E.3/E.4 gates paste these lines into a record. `set` repr order varies between
    processes, so the members are sorted — the braces stay because a set is what they are."""
    ours = replace(REFERENCE, ports=frozenset())
    line = sc.compare({"ac-worldserver": REFERENCE}, {"ac-worldserver": ours})[0]
    assert line == (
        "ac-worldserver: ports "
        "{('', '8085', 8085, 'tcp'), ('127.0.0.1', '7878', 7878, 'tcp')} vs set()"
    )
    assert sc.compare({"b": REFERENCE, "a": REFERENCE}, {}) == ["services: {'a', 'b'} vs set()"]


def _plan(body: str) -> composegen.ComposePlan:
    """A one-service ComposePlan whose base file carries `body` under `services: s:`."""
    return composegen.ComposePlan(
        base=f"services:\n  s:\n    image: x:1\n    restart: unless-stopped\n    {body}",
        override="services: {}\n",
        build="services: {}\n",
        dotenv={},
    )
