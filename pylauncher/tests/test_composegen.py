"""Tests for compose generation (`yulon.catalog.composegen`, roadmap 6.2).

Pure functions over template data, so everything here is byte-level and needs
no daemon. The load-bearing test is `test_ports_appear_in_exactly_one_file`:
compose CONCATENATES `ports:` across files, so a port added to the override
publishes a second binding instead of replacing the first, and nothing else in
this project would notice until two containers fought over 3724.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path, PurePosixPath

import pytest

from yulon import platform, resources
from yulon.catalog import composegen
from yulon.catalog.catalog import (
    CatalogEntry,
    CmangosData,
    DbFacts,
    NativeInstall,
    ReadyMarkers,
    load_catalog,
)

ENTRY = load_catalog().get("wow-wotlk")
TEMPLATES = resources.installers_dir()

BOT_POPULATION = "500"
# How many accounts those bots are spread across. A second number, decided with
# the first and written by every installer, so it needs its own name here: the
# 7.3 plan supplied 400 for TBC and 200 for Vanilla against the 100 that ships.
BOT_ACCOUNTS = "100"
"""How many random playerbots an install is supposed to come up with.

Owner decision, 2026-08-28, for the test runs and the default alike. It is a
constant here because the number is written down in six places — `catalog.json`
for the native path, and one copy per installer script in two spellings — and
the failure this project actually had was those six disagreeing with the
decision rather than with each other's syntax.
"""

# Anything that WRITES a random-bot population, in either spelling: AzerothCore
# takes it as compose environment, CMaNGOS as a `sed` replacement into
# aiplayerbot.conf. The `= <digits>` is what makes this a write — `wow-manage.sh`
# mentions `MaxRandomBots + 400` when it talks about PlayerLimit, and advice
# about the number is not a decision about it.
_BOT_POPULATION_WRITE = re.compile(
    r'AC_AI_PLAYERBOT_(?:MIN|MAX)_RANDOM_BOTS:\s*"(?P<ac>\d+)"'
    r"|AiPlayerbot\.(?:Min|Max)RandomBots\s*=\s*(?P<cmangos>\d+)"
)

# A YAML key at any depth, ignoring comments. There is no YAML parser in this
# project's dependencies and adding one for a test would ship a dependency the
# app does not use, so the scan is textual — over files this module wrote
# itself, whose shape is known.
_KEY = re.compile(r"^\s*(?:-\s*)?(?P<key>[A-Za-z_][\w.-]*)\s*:")


def keys_in(text: str) -> set[str]:
    """Every mapping key in a YAML document, at any depth, comments excluded."""
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _KEY.match(line)
        if match:
            found.add(match.group("key"))
    return found


def service_names(compose_text: str) -> set[str]:
    """Keys directly under `services:` — and ONLY there.

    `networks:` and `volumes:` keys sit at the same two-space indent, so a whole-file
    regex would count `tbc-net` and `db-data` as services; the scan stops at the next
    top-level key.

    Module-level rather than beside the CMaNGOS tests it was written for: the G.3
    cross-check reads the service keys of EVERY shipped entry, WotLK included.

    The key pattern excludes `_` deliberately, and the direction of that mistake is
    the reason it is left alone (bug-checklist §30, noted 2026-09-02): a service key
    spelled with an underscore would read as UNDEFINED, so the cross-check would
    fail loudly on a file that is fine, rather than pass quietly on one that is not.
    Widening it would be the permissive direction, and no shipped template writes an
    underscored key — every service name here is `<prefix>-<role>`.
    """
    names: set[str] = set()
    inside = False
    for line in compose_text.splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            break
        if inside:
            match = re.match(r"^  ([A-Za-z0-9-]+):\s*$", line)
            if match:
                names.add(match.group(1))
    return names


def render(server_dir: Path) -> composegen.ComposePlan:
    return composegen.render(
        ENTRY, server_dir, templates_root=TEMPLATES, platform_id=lambda: "macos"
    )


def _services_with_user_key(base: str) -> set[str]:
    """Which services carry a service-level `user:`, by indentation.

    Asserted structurally rather than by counting the string: a `user:` that has
    drifted inside `build:` or `environment:` would satisfy a substring count
    while doing nothing, and the point of the key is which container it applies
    to.
    """
    services: set[str] = set()
    service = None
    for raw in base.split("\n"):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent == 2 and raw.rstrip().endswith(":"):
            service = raw.strip()[:-1]
        elif indent == 4 and service and raw.strip().startswith("user:"):
            services.add(service)
    return services


WRITES_TO_BOUND_ETC = {"ac-db-import", "ac-authserver", "ac-worldserver"}


def test_windows_runs_the_env_dist_writers_as_root(tmp_path: Path) -> None:
    """Without this, the native Windows install cannot finish.

    Docker Desktop mounts a Windows drive into the WSL2 VM over 9p/drvfs with
    `uid=0;gid=0` and mode 0755, and the images run as `acore` (uid 1000). Every
    container that must write the bound-out `env/dist` is refused, and
    `ac-db-import` dies with "cp: cannot create regular file ...: Permission
    denied" on files whose Windows ACLs give the user full control. Measured on
    a clean Windows 11 box (2026-08-25): the identical import exits 1 without
    this key and 0 with it.

    Exactly the three services that mount `env/dist/etc` - a `user:` on the
    database or the client-data init would be scope nobody asked for.
    """
    plan = composegen.render(
        ENTRY, tmp_path, templates_root=TEMPLATES, platform_id=lambda: "windows"
    )
    assert _services_with_user_key(plan.base) == WRITES_TO_BOUND_ETC
    for service in WRITES_TO_BOUND_ETC:
        assert f"{service}:" in plan.base
    assert 'user: "0:0"' in plan.base


@pytest.mark.parametrize("platform_id", ["linux", "macos"])
def test_only_windows_gets_the_root_user(tmp_path: Path, platform_id: str) -> None:
    """Everywhere else the key would cost more than it buys.

    On Linux, running as root makes every file the container creates root-owned
    ON THE HOST - and `env/dist/etc` is bound out precisely so the module system
    and the user can edit `worldserver.conf` and every module conf. Windows pays
    nothing for it because 9p maps ownership back to the Windows account
    whatever uid wrote the file, which is why this is gated rather than global.
    """
    plan = composegen.render(
        ENTRY, tmp_path, templates_root=TEMPLATES, platform_id=lambda: platform_id
    )
    assert _services_with_user_key(plan.base) == set()
    assert "0:0" not in plan.base


def test_the_container_user_line_is_explained_where_it_is_absent(tmp_path: Path) -> None:
    """A line that renders to nothing teaches the next reader nothing.

    On the platforms that do not get the key, the template still emits a comment
    pointing at the function that decided - otherwise the only trace of a real,
    measured platform difference is an absence, and absences do not survive
    refactors.
    """
    plan = composegen.render(ENTRY, tmp_path, templates_root=TEMPLATES, platform_id=lambda: "linux")
    assert plan.base.count("# user: left to the image") == len(WRITES_TO_BOUND_ETC)


def test_ports_appear_in_exactly_one_file(tmp_path: Path) -> None:
    """`ports:` lives in the base file and NOWHERE else.

    Compose does not replace a `ports:` list from a later file, it appends to
    it. So an "override" of the auth port publishes both 3724 and its
    replacement, and the second container to start fails to bind — after the
    build, not before it.
    """
    plan = render(tmp_path / "wow")
    assert "ports" in keys_in(plan.base)
    assert "ports" not in keys_in(plan.override)
    assert "ports" not in keys_in(plan.build)


def test_the_build_overlay_is_only_build_blocks_and_names_its_dockerfile(tmp_path: Path) -> None:
    """Every build block spells `dockerfile:` explicitly, and nothing else lives here.

    AzerothCore keeps its Dockerfile at `apps/docker/Dockerfile`, so omitting
    the key makes compose look for `<checkout>/Dockerfile` — which is how the
    Rust launcher's first real native build died after cloning 600 MB, with
    five green stages of unit tests behind it.
    """
    plan = render(tmp_path / "wow")
    assert plan.build.count("dockerfile: apps/docker/Dockerfile") == 4
    assert plan.build.count("target:") == 4
    # Nothing structural: no image refs, no env, no volumes, no ports.
    assert keys_in(plan.build) <= {"services", "build", "context", "dockerfile", "target"} | {
        "ac-worldserver",
        "ac-authserver",
        "ac-db-import",
        "ac-client-data-init",
    }
    # And the base file, which IS auto-loaded, carries no build block at all —
    # or a bare `docker compose up` would start a multi-hour rebuild.
    assert "build" not in keys_in(plan.base)


def test_the_base_file_gives_the_import_its_playerbots_database(tmp_path: Path) -> None:
    """The gap this generator was written to close.

    The one-shot creates only the databases it is told about. An import that
    never heard of `acore_playerbots` leaves a worldserver whose bots cannot
    start, and the catalog says that schema exists (`databases.extra`).
    """
    plan = render(tmp_path / "wow")
    assert "AC_PLAYERBOTS_DATABASE_INFO" in plan.base
    for schema in ENTRY.databases.extra:
        assert schema in plan.base


def test_the_worldserver_keeps_its_console_and_its_shutdown_grace(tmp_path: Path) -> None:
    """`stdin_open`/`tty` (the Console tab attaches) and a 5m stop grace (measured)."""
    plan = render(tmp_path / "wow")
    assert "stdin_open: true" in plan.base
    assert "stop_grace_period: 5m" in plan.base


def test_the_database_is_published_on_loopback_only(tmp_path: Path) -> None:
    plan = render(tmp_path / "wow")
    assert f'"127.0.0.1:${{DOCKER_DB_EXTERNAL_PORT:-{ENTRY.ports.db}}}:3306"' in plan.base
    # SOAP is loopback-pinned through the DEFAULT VALUE, never an IP prefix on
    # the mapping — a prefix would render 127.0.0.1:127.0.0.1:7878:7878 once
    # something writes the whole binding into .env.
    assert "${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878" in plan.base


def test_the_project_name_is_per_directory_and_travels_in_the_file(tmp_path: Path) -> None:
    """Two folders are two stacks — compose keys named volumes by project too."""
    one = render(tmp_path / "one").base
    two = render(tmp_path / "two").base
    name_one = _project_name_of(one)
    name_two = _project_name_of(two)
    assert name_one.startswith("yulon-wow-wotlk-")
    assert name_one != name_two


def _project_name_of(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("the generated base file has no name: — the identity would be the folder")


def test_install_id_is_stable_and_absolute(tmp_path: Path) -> None:
    """Two spellings of one directory are one install, and a trailing slash is not a second."""
    linux = {"platform_id": lambda: "linux"}
    here = composegen.install_id(tmp_path / "wow", **linux)  # type: ignore[arg-type]
    assert here == composegen.install_id(Path(str(tmp_path / "wow") + "/"), **linux)  # type: ignore[arg-type]
    assert here != composegen.install_id(tmp_path / "other", **linux)  # type: ignore[arg-type]
    assert len(here) == composegen.INSTALL_ID_LENGTH


def test_install_id_follows_the_filesystem_on_case(tmp_path: Path) -> None:
    """NTFS is case-insensitive, so `C:\\Games` and `c:\\games` must be ONE install.

    Answered through the platform seam and never through `sys.platform`:
    faking that mutates the real module for the whole process, which is how
    this suite once went red on every Python 3.12+ Linux box while CI stayed
    green (checklist, "CI was green while the suite was red").
    """
    upper = Path("/Games/WoW")
    lower = Path("/games/wow")
    assert composegen.install_id(upper, platform_id=lambda: "windows") == composegen.install_id(
        lower, platform_id=lambda: "windows"
    )
    assert composegen.install_id(upper, platform_id=lambda: "linux") != composegen.install_id(
        lower, platform_id=lambda: "linux"
    )


def entry_with_templates(name: str) -> object:
    """`ENTRY`, but reading its templates from `name` under whatever root is passed."""
    native = ENTRY.install.native
    assert native is not None
    return ENTRY.model_copy(
        update={
            "install": ENTRY.install.model_copy(
                update={"native": native.model_copy(update={"templates": name})}
            )
        }
    )


def test_an_unfilled_placeholder_is_an_error_not_a_compose_file(tmp_path: Path) -> None:
    """A template edit that adds a token nobody fills must fail loudly, here.

    The alternative is a compose file containing a literal `{{...}}`, which
    compose accepts as a string and which then fails somewhere unrelated.
    """
    templates = tmp_path / "native"
    templates.mkdir()
    (templates / "base.yml.tmpl").write_text(
        "name: {{PROJECT_NAME}}\nx: {{NOBODY_FILLS_THIS}}\n", encoding="utf-8"
    )
    (templates / "override.yml.tmpl").write_text("services:\n{{ENVIRONMENT}}\n", encoding="utf-8")
    (templates / "build.yml.tmpl").write_text("services:\n", encoding="utf-8")
    with pytest.raises(composegen.ComposeGenError, match="NOBODY_FILLS_THIS"):
        composegen.render(
            entry_with_templates("native"),  # type: ignore[arg-type]
            tmp_path / "wow",
            templates_root=tmp_path,
            platform_id=lambda: "macos",
        )


def test_a_missing_template_says_which_file(tmp_path: Path) -> None:
    with pytest.raises(composegen.ComposeGenError, match="base.yml.tmpl"):
        composegen.render(
            entry_with_templates("not-there"),  # type: ignore[arg-type]
            tmp_path / "wow",
            templates_root=tmp_path,
            platform_id=lambda: "macos",
        )


UNSAFE_SCALAR_REASONS = {
    "$": "opens another compose interpolation",
    '"': "ends the quoted, semicolon-separated database field list",
    "\\": "escapes the next character inside that quoted string",
    ";": "splits the database field list",
    "#": "truncates a bare YAML value at a comment",
    "{": "opens a `${...}` interpolation",
    "}": "ends a `${VAR:-default}` early",
    "\r": "a line end that YAML and the SQL client both act on, and that a paste hides",
    "\n": "splices arbitrary YAML, and closes a joined SQL line to write the next one",
    "\t": "is not legal YAML indentation",
    "'": "closes `IDENTIFIED BY '<pw>'` in the SQL a plan with no `create` list writes",
}
"""Why each character is in `_UNSAFE_SCALAR_CHARS`, restated here ON PURPOSE.

A parametrisation derived from the constant shrinks with the constant, so it
can never see a narrowing. Measured 2026-09-02 on `f6ed1b9a`: deleting the
single quote — one character — from `_UNSAFE_SCALAR_CHARS` left the whole suite
passing, byte-identical to baseline, because the four cases the old
parametrisation restated did not include it. Deleting the `_refuse_unsafe()`
CALL was killed by four tests; narrowing the set it reads was silent. Something
outside the constant has to hold its membership, and a reason per character is
what makes that a review rather than a magic literal.

`test_every_unsafe_scalar_character_is_refused_*` then drives every member
through the real refusal, so this table cannot claim a character the code does
not enforce either.
"""


def test_the_unsafe_scalar_set_is_exactly_the_characters_with_a_reason() -> None:
    """The membership pin. Narrowing the set is loud here and nowhere else."""
    assert composegen._UNSAFE_SCALAR_CHARS == frozenset(UNSAFE_SCALAR_REASONS)


@pytest.mark.parametrize("char", sorted(composegen._UNSAFE_SCALAR_CHARS))
def test_every_unsafe_scalar_character_is_refused_in_the_database_password(
    tmp_path: Path, char: str
) -> None:
    """Refused rather than escaped: the same scalar lands in two contexts at once.

    A bare YAML value and a quoted, semicolon-separated one, with compose's own
    interpolation running on top of both — no single escaping survives all of
    it, and this is an install-time option the user can simply spell
    differently. Derived from the constant, so a character ADDED to the set
    arrives here with a case of its own; the membership pin above is what
    covers a character taken out.
    """
    with pytest.raises(composegen.ComposeGenError, match="database root password"):
        composegen.render(
            ENTRY,
            tmp_path / "wow",
            templates_root=TEMPLATES,
            db_password=f"pass{char}word",
            platform_id=lambda: "macos",
        )


@pytest.mark.parametrize("char", sorted(composegen._UNSAFE_SCALAR_CHARS))
def test_every_unsafe_scalar_character_is_refused_in_a_world_env_value(
    tmp_path: Path, char: str
) -> None:
    """`_env_block()` reads the same constant, and its values are catalog data, not a secret.

    The second call site, driven through `render()` rather than through the
    private function, because what is under test is that the same set guards
    both — the password refusal one line earlier must not be what answers, so
    the message names the variable and not the password.
    """
    with pytest.raises(composegen.ComposeGenError, match="the value of AC_A_TEST_SETTING") as bad:
        composegen.render(
            ENTRY,
            tmp_path / "wow",
            templates_root=TEMPLATES,
            world_env={"AC_A_TEST_SETTING": f"on{char}off"},
            platform_id=lambda: "macos",
        )
    assert "database root password" not in str(bad.value), "the password refusal answered instead"


def test_write_plan_refuses_a_compose_file_it_did_not_write(tmp_path: Path) -> None:
    """The rule that stops a dropped-in state file orphaning somebody's volumes."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    theirs = "services:\n  ac-worldserver:\n    image: mine\n"
    (server_dir / composegen.BASE_FILE).write_text(theirs, encoding="utf-8")
    with pytest.raises(composegen.ComposeGenError, match="not written by Yu'lon"):
        composegen.write_plan(render(server_dir), server_dir)
    assert (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8") == theirs


def test_write_plan_replaces_only_the_files_the_caller_proved_replaceable(
    tmp_path: Path,
) -> None:
    """The narrow exception the emulator repository's own compose file needs.

    The server directory IS the checkout and that repo ships a
    `docker-compose.yml` at its root, so the marker rule alone refused every
    install (review, 2026-08-23). `write_plan()` still refuses to guess which
    file that is — `native._generate_compose()` asks git and names it here.
    """
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    upstream = "services:\n  ac-database:\n    image: mysql:8.4\n"
    theirs = "services:\n  ac-worldserver:\n    environment: {MY_SETTING: 1}\n"
    (server_dir / composegen.BASE_FILE).write_text(upstream, encoding="utf-8")
    written = composegen.write_plan(
        render(server_dir), server_dir, replaceable=(composegen.BASE_FILE,)
    )
    assert len(written) == 3
    assert (
        (server_dir / composegen.BASE_FILE)
        .read_text(encoding="utf-8")
        .startswith(composegen.GENERATED_MARKER)
    )
    # Nothing is replaceable by default: the same directory refuses again once
    # the base file is somebody else's rather than ours.
    (server_dir / composegen.BASE_FILE).write_text(upstream, encoding="utf-8")
    with pytest.raises(composegen.ComposeGenError, match="not written by Yu'lon"):
        composegen.write_plan(render(server_dir), server_dir)
    # And naming the base file does not widen the rule to its neighbours.
    (server_dir / composegen.OVERRIDE_FILE).write_text(theirs, encoding="utf-8")
    with pytest.raises(composegen.ComposeGenError, match="not written by Yu'lon"):
        composegen.write_plan(render(server_dir), server_dir, replaceable=(composegen.BASE_FILE,))
    assert (server_dir / composegen.OVERRIDE_FILE).read_text(encoding="utf-8") == theirs


def test_write_plan_rewrites_its_own_files_and_leaves_identical_ones_alone(tmp_path: Path) -> None:
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    plan = render(server_dir)
    written = composegen.write_plan(plan, server_dir)
    assert {path.name for path in written} == {
        composegen.BASE_FILE,
        composegen.OVERRIDE_FILE,
        composegen.BUILD_FILE,
    }
    assert composegen.write_plan(plan, server_dir) == ()  # idempotent by content
    # Every generated file carries the marker, which is the whole rewrite rule.
    for name in (composegen.BASE_FILE, composegen.OVERRIDE_FILE, composegen.BUILD_FILE):
        assert (
            (server_dir / name).read_text(encoding="utf-8").startswith(composegen.GENERATED_MARKER)
        )


def test_merge_dotenv_replaces_in_place_and_appends_the_rest() -> None:
    """A merge, not a rewrite: this file is shared with SOAP setup and the port remedy."""
    existing = "# theirs\nDOCKER_DB_EXTERNAL_PORT=13306\nSOMETHING_ELSE=keep me\n"
    merged = composegen.merge_dotenv(
        existing, {"DOCKER_DB_EXTERNAL_PORT": "23306", "DB_ROOT_PASSWORD": "hunter2"}
    )
    lines = merged.splitlines()
    assert "SOMETHING_ELSE=keep me" in lines
    assert "DOCKER_DB_EXTERNAL_PORT=23306" in lines
    assert "DOCKER_DB_EXTERNAL_PORT=13306" not in lines
    # Replaced in PLACE, because compose takes the last assignment.
    assert lines.index("DOCKER_DB_EXTERNAL_PORT=23306") < lines.index("SOMETHING_ELSE=keep me")
    assert "DB_ROOT_PASSWORD=hunter2" in lines


def test_write_dotenv_is_atomic_and_leaves_no_temporary_file(tmp_path: Path) -> None:
    path = composegen.write_dotenv(tmp_path, {"DB_ROOT_PASSWORD": "hunter2"})
    assert path.read_text(encoding="utf-8").endswith("DB_ROOT_PASSWORD=hunter2\n")
    assert [p.name for p in tmp_path.iterdir()] == [composegen.DOTENV_FILE]


def test_the_database_healthcheck_asserts_the_thing_its_waiters_need(tmp_path: Path) -> None:
    """Health must mean "reachable over TCP", because that is how it is reached.

    Upstream's healthcheck has no `-h`, so it rides the unix socket and proves
    only that a client INSIDE the container can log in. Every waiter on
    `condition: service_healthy` — the import one-shot, both servers — connects
    to `ac-database:3306` from a different container.

    Pinned rather than left to review because it is one word in a string in a
    template, and its absence is invisible until a first-ever install.
    """
    plan = render(tmp_path / "wow")
    healthcheck = next(line for line in plan.base.splitlines() if line.strip().startswith("test:"))
    assert "--protocol=TCP" in healthcheck
    # By SERVICE NAME, not loopback: 127.0.0.1 inside the database container is
    # not the interface consumers arrive on, so a probe against it does not
    # establish what this test is for (adversarial review, 2026-08-24).
    assert "-h ac-database" in healthcheck
    assert "127.0.0.1" not in healthcheck


def test_the_generated_stack_configures_the_bot_population(tmp_path: Path) -> None:
    """A native install must not quietly give a different world than a script one.

    The Linux installer script sets the playerbot population; the engine's
    defaults did not, so a macOS install would have taken mod-playerbots' own.
    Found by diffing `docker compose config` on the proven yulon-ubuntu install
    against what `render()` produces — the check `phase6-decisions.md` asks for
    and which had never been run.

    The numbers live in `catalog.json`, not in a constant in this package. They
    started in `DEFAULT_WORLD_ENV` and an adversarial review moved them: a
    per-game value in a module constant is what style-guide §3 forbids, and one
    machine's bot population is not a default for every machine — a preflight-
    passing laptop can install successfully and then be unusable. Making them
    data is what lets a capacity-aware default, or a user setting, exist later.

    Min and Max are the SAME number, not a band, so the world comes up with the
    population that was asked for rather than somewhere under it.
    """
    plan = render(tmp_path / "wow")
    assert f'AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "{BOT_POPULATION}"' in plan.override
    assert f'AC_AI_PLAYERBOT_MAX_RANDOM_BOTS: "{BOT_POPULATION}"' in plan.override

    # Data, not code — and the structural flags stay behind in the constant.
    assert "AC_AI_PLAYERBOT_MIN_RANDOM_BOTS" not in composegen.DEFAULT_WORLD_ENV
    assert ENTRY.install.native is not None
    assert ENTRY.install.native.azerothcore is not None
    world_env = ENTRY.install.native.azerothcore.world_env
    assert world_env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"] == BOT_POPULATION
    assert world_env["AC_AI_PLAYERBOT_MAX_RANDOM_BOTS"] == BOT_POPULATION
    assert 'AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES: "1"' in plan.override


def test_every_installer_writes_the_bot_population_that_was_decided() -> None:
    """The decision and what ships have to be the same number, in all six places.

    The way this went wrong was not a typo. 500/500 was decided and written
    down, the change landed on one test branch, and every other branch went on
    shipping 1600-2000 — a fresh Fedora install came up `1633/1633 Bot Reyna
    logged in` while the number of record said 500. The pin above would not
    have caught that: it watches the native path, and the five installer
    scripts each carry their own copy of the number in their own spelling.

    So this scans every installer file for anything that WRITES a random-bot
    population and holds all of them to `BOT_POPULATION`. It is deliberately a
    sweep rather than a list of five paths: a sixth installer, or a seventh
    spelling in an existing one, is caught the day it is added rather than the
    day someone counts the bots that logged in.

    Until 7.2 the sweep matched the five bash installers, twice each, and held
    those ten numbers to `BOT_POPULATION`. Those files are gone and nothing
    under `catalog/installers/` writes the number any more, so the sweep now
    expects nothing and exists to catch a NEW shipped file that hard-codes one.
    The `catalog.json` assertions below it are the ones with teeth today.
    """
    scanned = 0
    written: dict[str, list[str]] = {}
    for path in sorted(TEMPLATES.rglob("*")):
        if not path.is_file():
            continue
        scanned += 1
        text = path.read_text(encoding="utf-8", errors="replace")
        found = [m.group("ac") or m.group("cmangos") for m in _BOT_POPULATION_WRITE.finditer(text)]
        if found:
            written[path.relative_to(TEMPLATES).as_posix()] = found

    # An empty expectation is satisfied by scanning nothing at all, which is
    # what a moved or renamed installers root would produce.
    assert scanned >= 12, f"{TEMPLATES} holds {scanned} files; this sweep is looking nowhere"
    assert written == {}, (
        "a file under catalog/installers/ hard-codes a bot population; the number "
        f"belongs in catalog.json, which says {BOT_POPULATION}"
    )

    # The sixth place is the native path's data, which the scan above cannot
    # see: it lives in `catalog.json`, not under `catalog/installers/`.
    assert ENTRY.install.native is not None
    assert ENTRY.install.native.azerothcore is not None
    min_bots = ENTRY.install.native.azerothcore.world_env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"]
    assert min_bots == BOT_POPULATION

    # And from G.4 there are three more of it: the CMaNGOS entries write the
    # same number as an `aiplayerbot.conf` value instead of compose
    # environment. The plan for that task supplied 1600/2000 for TBC and
    # 600/800 for Vanilla — the pre-2026-08-28 numbers, copied out of the
    # scripts as they were before the decision reached them. Writing those
    # would have re-opened this exact bug on the side of it no scan of
    # `catalog/installers/` can see.
    for game_id in ("wow-tbc", "wow-vanilla", "wow-tortoise"):
        cmangos = load_catalog().get(game_id).install.native
        assert cmangos is not None and cmangos.cmangos is not None
        bots = cmangos.cmangos.conf.files.get("aiplayerbot.conf")
        if bots is None:
            continue
        assert bots.keys["AiPlayerbot.MinRandomBots"] == BOT_POPULATION, game_id
        assert bots.keys["AiPlayerbot.MaxRandomBots"] == BOT_POPULATION, game_id
        # The account count is the third number the plan got wrong (400 and 200
        # against the 100 both scripts write), and it is a separate key: an edit
        # that broke only this one would pass every assertion above it.
        assert bots.keys["AiPlayerbot.RandomBotAccountCount"] == BOT_ACCOUNTS, game_id


def test_the_image_refs_match_the_services_the_build_overlay_actually_builds(
    tmp_path: Path,
) -> None:
    """`native.images` and the build overlay's `target:` lines must agree, and nothing else did.

    The same four names live in `build.yml.tmpl`'s `target:` lines, in
    `base.yml.tmpl`'s `image:` refs, and now in `catalog.json`'s `images`
    (they were a module constant, `BUILT_SERVICES`). Rename a `target:` without
    touching the data and `images_built()` asks the daemon for a reference that
    will never exist, so it answers False forever and every resume re-runs the
    multi-hour build — permanently, silently, with a green suite (review,
    2026-08-24). Derived from the rendered files rather than restated, so the
    assertion cannot drift the way the tuple did.
    """
    plan = render(tmp_path / "wow")
    refs = composegen.built_image_refs(ENTRY, tmp_path / "wow", platform_id=lambda: "linux")
    native = ENTRY.install.native
    assert native is not None

    built_targets = set(re.findall(r"^\s*target:\s*(\S+)\s*$", plan.build, re.MULTILINE))
    assert built_targets == set(native.images), (built_targets, native.images)
    assert all(ref.startswith(native.image_prefix) for ref in refs)

    # And every reference the engine will ask the daemon about is exactly an
    # image the base file names, so a rename in either place fails here.
    #
    # The tag used to be spelled as compose interpolation
    # (`${IMAGE_TAG:-native-abc}`), and this test resolved it before comparing —
    # which DOCUMENTED a coupling rather than closing it. An `.env` that set
    # `IMAGE_TAG` would retag the build while `built_image_refs()` went on
    # asking the daemon about the derived default, so `images_built()` would
    # answer "not built" forever and every resume would re-run a multi-hour
    # compile: the same defect that had just been measured and fixed from the
    # other direction. All five reviewers refused "documented in a test" as the
    # disposition. Nothing ever wrote that key — `composegen.py` substitutes
    # `{{IMAGE_TAG}}` at generation time and no code path puts it in `.env` — so
    # the wrapper was indirection with no writer, and it is gone. The
    # comparison is literal now, and the second assertion keeps it gone.
    base_images = set(re.findall(r"^\s*image:\s*(\S+)\s*$", plan.base, re.MULTILINE))
    assert set(refs) <= base_images, (set(refs) - base_images, base_images)
    assert "${IMAGE_TAG" not in plan.base, "the interpolation wrapper came back"


def test_the_image_prefix_is_the_entrys_not_a_constant(tmp_path: Path) -> None:
    """Two games must not tag their builds into one namespace; the prefix is catalog data."""
    native = ENTRY.install.native
    assert native is not None
    other = ENTRY.model_copy(
        update={
            "install": ENTRY.install.model_copy(
                update={"native": native.model_copy(update={"image_prefix": "yulon.local/x-"})}
            )
        }
    )
    refs = composegen.built_image_refs(other, tmp_path / "wow", platform_id=lambda: "linux")
    tag = composegen.image_tag(tmp_path / "wow", platform_id=lambda: "linux")
    assert refs == tuple(f"yulon.local/x-{name}:{tag}" for name in native.images)
    plan = composegen.render(
        other, tmp_path / "wow", templates_root=TEMPLATES, platform_id=lambda: "linux"
    )
    assert "yulon.local/x-worldserver:" in plan.base
    assert "yulon.local/ac-wotlk-" not in plan.base
    assert not hasattr(composegen, "DEFAULT_IMAGE_PREFIX")
    assert not hasattr(composegen, "BUILT_SERVICES")


def test_built_image_refs_refuses_an_entry_with_no_native_block(tmp_path: Path) -> None:
    """Built from WotLK with `native` stripped, not from a CMaNGOS entry: G.4 gives those
    a native block in 7.3 and this test must not care."""
    scriptless = ENTRY.model_copy(
        update={"install": ENTRY.install.model_copy(update={"native": None})}
    )
    assert scriptless.install.native is None
    with pytest.raises(composegen.ComposeGenError, match="install.native"):
        composegen.built_image_refs(scriptless, tmp_path / "wow", platform_id=lambda: "linux")


HOST_BIND = re.compile(r"^\s*-\s*\./")
"""A HOST bind line in a compose template: a list item whose source is a `./` path.

Named volumes (`db-data:`, `client-data:`) deliberately do not match. A relabel
suffix on a named volume is a bug rather than a no-op, so the two are told apart
by what the mount SOURCE is and not by which service they sit under.
"""


def test_every_host_bind_line_carries_the_label_and_no_named_volume_does(tmp_path: Path) -> None:
    """SELinux: `:z` on every `./...:` bind, never on `client-data:`/`db-data:` (phase7-decisions).

    Eight lines today - seven in the base file, one in the override - and the
    count is asserted so a new bind line added without the token fails here
    rather than as "Permission denied" from a container on Fedora. A uniform
    `:z`, not the Fedora script's `:Z` on `./modules`: that mount is shared by
    `ac-db-import` and `ac-worldserver`, and `:Z` on a shared mount locks the
    other service out. The token may appear on nothing else - not even a
    comment, which `fill()` would otherwise happily rewrite.
    """
    base_tmpl = (TEMPLATES / "wow-wotlk/native/base.yml.tmpl").read_text(encoding="utf-8")
    override_tmpl = (TEMPLATES / "wow-wotlk/native/override.yml.tmpl").read_text(encoding="utf-8")
    assert base_tmpl.count("{{BIND_LABEL}}") == 7
    assert override_tmpl.count("{{BIND_LABEL}}") == 1
    for text in (base_tmpl, override_tmpl):
        for line in text.splitlines():
            if HOST_BIND.match(line):
                assert line.endswith("{{BIND_LABEL}}"), line
            elif "{{BIND_LABEL}}" in line:
                raise AssertionError(f"the label is on something that is not a host bind: {line}")

    labelled = composegen.render(
        ENTRY,
        tmp_path / "wow",
        templates_root=TEMPLATES,
        bind_label=":z",
        platform_id=lambda: "linux",
    )
    binds = [
        line
        for text in (labelled.base, labelled.override)
        for line in text.splitlines()
        if HOST_BIND.match(line)
    ]
    assert len(binds) == 8
    assert all(line.endswith(":z") for line in binds), binds
    assert "client-data:/azerothcore/env/dist/data/:ro" in labelled.base
    assert "db-data:/var/lib/mysql\n" in labelled.base
    assert labelled.build.count(":z") == 0


def test_off_selinux_the_label_renders_to_nothing(tmp_path: Path) -> None:
    """The default is the empty string, so every existing install renders byte-identically."""
    plain = composegen.render(
        ENTRY, tmp_path / "wow", templates_root=TEMPLATES, platform_id=lambda: "linux"
    )
    explicit = composegen.render(
        ENTRY,
        tmp_path / "wow",
        templates_root=TEMPLATES,
        bind_label="",
        platform_id=lambda: "linux",
    )
    assert plain == explicit
    assert ":z" not in plain.base and ":z" not in plain.override
    assert "- ./modules:/azerothcore/modules\n" in plain.override
    assert "{{" not in plain.base and "{{" not in plain.override


def test_render_accepts_every_label_platform_bind_label_can_produce(tmp_path: Path) -> None:
    """The allow-list above and `platform.bind_label()` have to be the same two strings.

    A.5 is what finally wires the two together; until then the only thing
    holding the ends in agreement is this test and its twin in
    `test_platform.py`. A third answer out of `bind_label()` would not fail
    there — it would fail here, as a `ComposeGenError` raised in the middle of
    a real install, after the sources are cloned.
    """
    for enforcing in (True, False, None):
        for fs_type in (None, "ext2/ext3", "xfs", "9p", "NTFS3"):
            label = platform.bind_label(enforcing=enforcing, fs_type=fs_type)
            plan = composegen.render(
                ENTRY,
                tmp_path / "wow",
                templates_root=TEMPLATES,
                bind_label=label,
                platform_id=lambda: "linux",
            )
            assert "{{" not in plan.base, (enforcing, fs_type)


def test_a_bind_label_that_is_not_a_mount_option_is_refused(tmp_path: Path) -> None:
    """Anything but `:z` is refused, because the token lands unquoted inside a YAML list item."""
    with pytest.raises(composegen.ComposeGenError, match="bind label"):
        composegen.render(
            ENTRY,
            tmp_path / "wow",
            templates_root=TEMPLATES,
            bind_label=":z\n  - /etc:/etc",
            platform_id=lambda: "linux",
        )


def test_fill_is_the_one_public_substitution_and_refuses_a_leftover_token() -> None:
    """A6: conf values, SQL statements and ready markers all go through this one function."""
    assert composegen.fill("a {{X}} b {{Y}}", {"X": "1", "Y": "2"}) == "a 1 b 2"
    assert composegen.fill("no tokens", {"X": "unused"}) == "no tokens"
    with pytest.raises(composegen.ComposeGenError, match="unfilled compose placeholder"):
        composegen.fill("a {{X}} {{Z}}", {"X": "1"})
    # The message NAMES the offending token, which G.3's callers depend on: a conf
    # value, a SQL statement and a ready marker all reach this one refusal from a
    # file the traceback never mentions, so "something is unfilled" is not enough.
    with pytest.raises(composegen.ComposeGenError, match="NOPE"):
        composegen.fill("{{A}} {{NOPE}}", {"A": "1"})
    assert composegen._fill is composegen.fill, "the old private name is an alias, not a copy"


def test_an_unfilled_placeholder_is_named_and_never_quotes_what_was_substituted() -> None:
    """The message says WHICH token, and quotes no filled text — because filled text is
    where the secret is.

    `fill()` substitutes every token first and only then looks for a leftover `{{`, so a
    window of the RESULT can hold a value that was filled in. On the shipped Tortoise
    statement that leaked eight characters of a constant prefix; one shape over — a token
    the template comments out next to an unfilled one — it is the whole password, in a
    user-facing error whose tail invites the user to report it.

    The name is also simply the better message: every caller (`conf`, `sqlplan`,
    `native`'s ready markers) re-raises this text about a file the traceback never names,
    and "unfilled placeholder `{{NOPE}}`" beats forty characters of context.
    """
    secret = "tortoise-0123456789abcdef"
    with pytest.raises(composegen.ComposeGenError) as caught:
        composegen.fill("SELECT {{NOPE}} /*{{DB_PASSWORD}}*/", {"DB_PASSWORD": secret})
    assert "{{NOPE}}" in str(caught.value)
    assert secret not in str(caught.value)
    with pytest.raises(composegen.ComposeGenError) as caught:
        composegen.fill(
            "CREATE USER '{{DB_USER}}'@'%' IDENTIFIED BY '{{DB_PASSWORD}}'",
            {"DB_PASSWORD": secret},
        )
    assert "{{DB_USER}}" in str(caught.value)
    assert secret not in str(caught.value)


def test_a_leftover_brace_with_no_token_to_name_says_which_of_the_two_it_is() -> None:
    """Two leftovers have no token name to report, and they have different remedies.

    A `{{` the template spells but never closes as a `{{TOKEN}}` is a template bug, and
    the template is quoted for it — template text is data in this repo, never a secret.
    A `{{` a filled VALUE carried in is not the template's fault at all, and quoting it
    is exactly the leak above, so it is described rather than shown.
    """
    with pytest.raises(composegen.ComposeGenError, match="not a closed"):
        composegen.fill("root: {{ DB_HOST }}\n", {"DB_HOST": "db"})
    with pytest.raises(composegen.ComposeGenError, match="a filled value") as caught:
        composegen.fill("root: {{X}}\n", {"X": "{{oops"})
    assert "oops" not in str(caught.value)


# -- generated-password mode: the secret lives in .env and nowhere else --------


MINIMAL_CMANGOS = {
    "client": {},
    "dockerfile": {},
    "extract": {
        "image": "server",
        "tools": [{"name": "dbc", "argv": ["/opt/mangos/bin/tools/ad"], "produces": {"dbc": 1}}],
    },
    "mmaps": {"argv": ["/opt/mangos/bin/tools/MoveMapGen"]},
    "conf": {
        "source_dir": "/opt/mangos/etc",
        "files": {"mangosd.conf": {"keys": {"DataDir": '"/opt/mangos/data"'}}},
    },
    "sql": {
        "phases": [{"name": "base", "into": "mangos", "files": ["src/core/sql/base/mangos.sql"]}],
        "marker_db": "mangos",
    },
}
"""The smallest `cmangos` block the family-block validator accepts (G.2): B.6's
`generated_entry()` says `family="cmangos"`, so it must carry the block the family names."""


def generated_entry(tmp_path: Path, base: str) -> CatalogEntry:
    """wow-tbc (a generated-password entry) with a throwaway native block and templates.

    G.2's exactly-one-family-block validator makes this block need a minimal
    `cmangos=CmangosData(...)`; G.2 adds it here (A14).
    """
    templates = tmp_path / "gen"
    templates.mkdir(exist_ok=True)
    (templates / "base.yml.tmpl").write_text(base, encoding="utf-8")
    (templates / "override.yml.tmpl").write_text("services:\n{{ENVIRONMENT}}\n", encoding="utf-8")
    (templates / "build.yml.tmpl").write_text("services:\n", encoding="utf-8")
    tbc = load_catalog().get("wow-tbc")
    native = NativeInstall(
        family="cmangos",
        templates="gen",
        images=("server",),
        image_prefix="yulon.local/cmangos-tbc-",
        db=DbFacts(image="mariadb:11", client="mariadb", user="mangos"),
        ready=ReadyMarkers(world="Avg Diff"),
        cmangos=CmangosData.model_validate(MINIMAL_CMANGOS),
    )
    return tbc.model_copy(update={"install": tbc.install.model_copy(update={"native": native})})


SAFE_BASE = "name: {{PROJECT_NAME}}\nroot: ${DB_ROOT_PASSWORD:?Yu'lon .env is missing}\n"


def test_a_generated_password_reaches_env_and_never_the_compose_text(tmp_path: Path) -> None:
    """The secret travels in `.env` (phase7-decisions "Password"), which has exactly one writer."""
    entry = generated_entry(tmp_path, SAFE_BASE)
    plan = composegen.render(
        entry,
        tmp_path / "wow",
        templates_root=tmp_path,
        db_password="tbc-0a1b2c3d",
        platform_id=lambda: "linux",
    )
    assert plan.dotenv == {"DB_ROOT_PASSWORD": "tbc-0a1b2c3d"}
    for text in (plan.base, plan.override, plan.build):
        assert "tbc-0a1b2c3d" not in text
    assert "${DB_ROOT_PASSWORD:?" in plan.base


def test_generated_mode_without_a_password_is_an_error_not_a_blank(tmp_path: Path) -> None:
    entry = generated_entry(tmp_path, SAFE_BASE)
    with pytest.raises(composegen.ComposeGenError, match="generates its database password"):
        composegen.render(
            entry, tmp_path / "wow", templates_root=tmp_path, platform_id=lambda: "linux"
        )


def test_a_generated_password_template_may_not_mention_the_password_token(tmp_path: Path) -> None:
    """`{{DB_PASSWORD}}` would bake the secret into a file git can see; refused by name."""
    entry = generated_entry(tmp_path, "name: {{PROJECT_NAME}}\nroot: {{DB_PASSWORD}}\n")
    with pytest.raises(composegen.ComposeGenError, match=r"DB_PASSWORD.*base\.yml\.tmpl"):
        composegen.render(
            entry,
            tmp_path / "wow",
            templates_root=tmp_path,
            db_password="tbc-0a1b2c3d",
            platform_id=lambda: "linux",
        )


def test_a_fixed_password_still_lands_in_the_base_file_and_not_in_env(tmp_path: Path) -> None:
    """WotLK is unchanged: the fixed value is the interpolation default, `.env` stays untouched."""
    plan = render(tmp_path / "wow")
    assert plan.dotenv == {}
    assert "${DB_ROOT_PASSWORD:-password}" in plan.base


# -- A16: the committed byte snapshot ------------------------------------------

LINUX_SERVER_DIR = Path("/home/pk/wow-server-playerbots")
LINUX_INSTALL_ID = hashlib.sha256(b"/home/pk/wow-server-playerbots").hexdigest()[
    : composegen.INSTALL_ID_LENGTH
]
SNAPSHOT_DIR = Path(__file__).resolve().parent / "data" / "wotlk-rendered"


def rendered_as_on_linux() -> dict[str, str]:
    """WotLK rendered for the yulon-ubuntu path, platform linux, no SELinux label.

    `install_id()` hashes `os.path.abspath()`, and on Windows `/home/pk/…` grows
    a drive letter, so the id is the ONE thing normalised to its Linux value —
    on Linux itself the replace is a no-op and the comparison is literal.
    """
    plan = composegen.render(
        ENTRY,
        LINUX_SERVER_DIR,
        templates_root=TEMPLATES,
        bind_label="",
        platform_id=lambda: "linux",
    )
    actual_id = composegen.install_id(LINUX_SERVER_DIR, platform_id=lambda: "linux")
    return {
        composegen.BASE_FILE: plan.base.replace(actual_id, LINUX_INSTALL_ID),
        composegen.OVERRIDE_FILE: plan.override.replace(actual_id, LINUX_INSTALL_ID),
        composegen.BUILD_FILE: plan.build.replace(actual_id, LINUX_INSTALL_ID),
    }


def test_the_wotlk_render_reproduces_the_committed_snapshot_byte_for_byte() -> None:
    """A16: the three files under `tests/data/wotlk-rendered/` ARE what `render()` writes.

    Committed in 7.1 and kept green through 7.3 (G.5/G.7), which is how "the
    WotLK templates render byte-identical" stops being a sentence in the
    checklist and becomes something the suite refuses to let drift. Regenerate
    them only by the command in B.6 Step 3, and only when a WotLK template
    change is the point of the commit.
    """
    texts = rendered_as_on_linux()
    assert set(texts) == {p.name for p in SNAPSHOT_DIR.iterdir() if p.is_file()}
    for name, text in texts.items():
        expected = (SNAPSHOT_DIR / name).read_text(encoding="utf-8")
        assert text == expected, name
    if sys.platform.startswith("linux"):
        assert (
            composegen.install_id(LINUX_SERVER_DIR, platform_id=lambda: "linux") == LINUX_INSTALL_ID
        )
    assert f"name: yulon-wow-wotlk-{LINUX_INSTALL_ID}\n" in texts[composegen.BASE_FILE]
    assert "${DB_ROOT_PASSWORD:-password}" in texts[composegen.BASE_FILE]
    assert ":z" not in texts[composegen.BASE_FILE]


# -- G.3: the per-entry token set ----------------------------------------------


def test_entry_tokens_spell_the_entry_once(tmp_path: Path) -> None:
    """`CORE_DIR` is the in-image install prefix (parent of `conf.source_dir`), never a
    host path; `MAKE_JOBS` is the dockerfile's number; `LOGS_DB` is the first extra schema."""
    entry = generated_entry(tmp_path, SAFE_BASE)
    tokens = composegen.entry_tokens(entry)
    assert tokens["CORE_DIR"] == "/opt/mangos"
    assert tokens["MAKE_JOBS"] == "2"
    assert tokens["DB_HOST"] == entry.containers.db
    assert tokens["CONTAINER_PREFIX"] == "tbc-"
    assert tokens["LOGS_DB"] == entry.databases.extra[0]
    assert set(tokens) == {
        "DB_IMAGE",
        "DB_HOST",
        "DB_USER",
        "AUTH_DB",
        "WORLD_DB",
        "CHAR_DB",
        "LOGS_DB",
        "CONTAINER_PREFIX",
        "CORE_DIR",
        "CLIENT_BUILD",
        "MAKE_JOBS",
    }


def test_logs_db_is_omitted_not_blanked_without_an_extra_schema(tmp_path: Path) -> None:
    entry = generated_entry(tmp_path, SAFE_BASE)
    no_logs = entry.model_copy(
        update={"databases": entry.databases.model_copy(update={"extra": ()})}
    )
    tokens = composegen.entry_tokens(no_logs)
    assert "LOGS_DB" not in tokens
    with pytest.raises(composegen.ComposeGenError, match="LOGS_DB"):
        composegen.fill("{{LOGS_DB}}", tokens)


def test_the_family_tokens_come_from_the_entry(tmp_path: Path) -> None:
    """DB host, user, schema names, container prefix and client build are catalog facts."""
    templates = tmp_path / "native"
    templates.mkdir()
    (templates / "base.yml.tmpl").write_text(
        "name: {{PROJECT_NAME}}\n"
        "db: {{DB_IMAGE}} {{DB_HOST}} {{DB_USER}}\n"
        "schemas: {{AUTH_DB}} {{WORLD_DB}} {{CHAR_DB}} {{LOGS_DB}}\n"
        "prefix: {{CONTAINER_PREFIX}}db\n"
        "build: {{CLIENT_BUILD}}\n",
        encoding="utf-8",
    )
    (templates / "override.yml.tmpl").write_text("services: {}\n", encoding="utf-8")
    (templates / "build.yml.tmpl").write_text("services:\n", encoding="utf-8")
    plan = composegen.render(
        entry_with_templates("native"),  # type: ignore[arg-type]
        tmp_path / "wow",
        templates_root=tmp_path,
        platform_id=lambda: "linux",
    )
    native = ENTRY.install.native
    assert native is not None
    assert f"db: {native.db.image} {ENTRY.containers.db} {native.db.user}" in plan.base
    assert "schemas: acore_auth acore_world acore_characters acore_playerbots" in plan.base
    # `ac-db` is this synthetic template's own `db` behind the real prefix, NOT a
    # container WotLK has (its three are ac-database/-authserver/-worldserver). What
    # the prefix must produce for a real entry is cross-checked over the shipped catalog
    # by `test_every_service_the_catalog_selects_is_defined_in_the_rendered_compose_file`,
    # which compares the names an entry SELECTS against the service keys its rendered base
    # DEFINES — for the three CMaNGOS entries those keys are `{{CONTAINER_PREFIX}}<service>`,
    # so a wrong prefix is what makes the two sides fail to meet. It is not the check this
    # comment cited until 2026-09-02, which was
    # `test_the_container_prefix_rebuilds_the_container_names_of_every_shipped_entry`:
    # that one rebuilt names from `containers.services` instead of reading what was
    # written, and was replaced for exactly that reason (bug-checklist §26, §30).
    assert "prefix: ac-db" in plan.base, "the common prefix of the three container names"
    assert f"build: {ENTRY.client.build}" in plan.base


def test_tokens_a_family_does_not_have_stay_unfilled_and_therefore_loud(tmp_path: Path) -> None:
    """WotLK has no `cmangos` block, so `MAKE_JOBS`/`CORE_DIR` do not exist for it.

    Not filled with "" — a blank `make -j` or a blank mount path is a silent literal — but
    left for `fill()` to refuse.

    The `render()` half of this alone was vacuous: before `entry_tokens()` existed,
    `{{MAKE_JOBS}}` was already unfilled and `fill()` already refused it, so a green
    result proved the catch-all fires and said nothing about the family guard. The
    first two assertions are the real check — they read the dict `entry_tokens()`
    returns for an AzerothCore entry and pin its own `if native.cmangos is not None`.
    """
    assert ENTRY.install.native is not None
    assert ENTRY.install.native.cmangos is None, "WotLK is the AzerothCore entry here"
    tokens = composegen.entry_tokens(ENTRY)
    assert "MAKE_JOBS" not in tokens
    assert "CORE_DIR" not in tokens

    templates = tmp_path / "native"
    templates.mkdir()
    (templates / "base.yml.tmpl").write_text("jobs: {{MAKE_JOBS}}\n", encoding="utf-8")
    (templates / "override.yml.tmpl").write_text("services: {}\n", encoding="utf-8")
    (templates / "build.yml.tmpl").write_text("services:\n", encoding="utf-8")
    with pytest.raises(composegen.ComposeGenError, match="MAKE_JOBS"):
        composegen.render(
            entry_with_templates("native"),  # type: ignore[arg-type]
            tmp_path / "wow",
            templates_root=tmp_path,
            platform_id=lambda: "linux",
        )


# -- G.3: every service the catalog selects is a service the file defines ------


def test_every_service_the_catalog_selects_is_defined_in_the_rendered_compose_file(
    tmp_path: Path,
) -> None:
    """The two sides compared against each other, instead of each against a literal.

    `docker.start_staged()` runs `compose up -d --no-deps <compose_services()...>`,
    `repair_import()` selects `import_service`, and the native engine selects
    `containers.client_data`; compose answers `no such service` and fails the install
    for a name its file does not define. Nothing joined the two sides, and on
    2026-09-01 they disagreed for three of the four shipped entries: `catalog.json`
    declared `containers.services` as `db`/`realmd`/`mangosd` for TBC, Vanilla and
    Tortoise, while `shared/cmangos/base.yml.tmpl` renders those services as
    `{{CONTAINER_PREFIX}}db` and friends — `tbc-db`, `tbc-realmd`, `tbc-mangosd`.
    Not live only because `FAMILIES` registered `azerothcore` alone; K.8 registers
    `CmangosInstaller`, and that install would have died at the first `compose up`.

    Two tests had covered this ground and neither could fail for it. The one that
    read a compose file at all read the *bash* installer's, not the generated one;
    its replacement asserted the catalog's literals in one half and the rendered
    names in the other, and passed with both, because it never compared them. This
    one renders through `composegen.render()` and reads the service keys back out —
    the only form of the check that can see a disagreement.

    Replaces `test_the_container_prefix_rebuilds_the_container_names_of_every_shipped_
    entry`, which reconstructed what a template *would* write from
    `containers.services` rather than reading what it did write. Every entry with a
    native block, so WotLK — whose services and containers coincide, and whose spec
    therefore keeps the default — guards the convention it established.
    """
    catalog = load_catalog()
    examined = 0
    checked = 0
    # Collected, not asserted inside the loop: an `assert` on the first bad entry
    # stops the sweep, and the three CMaNGOS entries were wrong together — a run
    # that named only TBC would have been read as one entry's typo.
    problems: list[str] = []
    for entry in catalog.games:
        if entry.install.native is None:
            continue
        plan = composegen.render(
            entry,
            tmp_path / entry.id,
            templates_root=TEMPLATES,
            db_password="0123456789abcdef",
            platform_id=lambda: "linux",
        )
        defined = service_names(plan.base)
        spec = entry.container_spec()
        selected = list(spec.compose_services())
        if spec.import_service:
            selected.append(spec.import_service)
        if entry.containers.client_data:
            selected.append(entry.containers.client_data)
        # Assert the entry SELECTED something before checking what it selected.
        # Without this the sweep is vacuous in the one direction that matters:
        # the reviewer mutated `ContainerSpec.compose_services()` to
        # `return self.services` -- empty for every shipped entry now that none
        # declares one -- and `selected`, `missing` and `problems` were all empty
        # while this test passed. The entry count below could not see it, because
        # four entries really had been rendered. This is the standing rule landing
        # on the test that inherited three deleted tests' weight: assert the value
        # ARRIVES, never that the loop ran.
        assert selected, (
            f"{entry.id} selected no compose services at all, so this entry "
            "compared nothing; `ContainerSpec.compose_services()` is answering empty"
        )
        checked += len(selected)
        missing = [name for name in selected if name not in defined]
        if missing:
            problems.append(
                f"{entry.id} selects {missing}, but its rendered docker-compose.yml "
                f"defines {sorted(defined)}"
            )
        examined += 1
    assert not problems, (
        "`docker compose up` answers `no such service` and the install stops there:\n  "
        + "\n  ".join(problems)
    )
    # A sweep that renders nothing passes just as quietly as one that renders four
    # correct files. Every shipped entry has a native block today, so the count is
    # the entry count and not merely "more than none".
    #
    # Deliberately NOT `== 4`. A hard-coded number here rots on the fifth game
    # while catching nothing extra: a broken fifth entry is reported by the
    # `assert not problems` above, which fires first -- verified by mutation,
    # 2026-09-02, by narrowing the sweep to one entry AND breaking that entry, and
    # watching the failure come from `not problems` rather than from this line.
    assert examined == len(catalog.games) > 0, (
        f"the cross-check examined {examined} of {len(catalog.games)} shipped entries; "
        "an entry without a native block renders nothing and is silently uncovered"
    )
    assert checked >= 3 * examined, (
        f"only {checked} service names were compared across {examined} entries; "
        "each shipped entry names at least a db, an auth and a world container, so "
        "a lower number means something answered with an empty selection"
    )


def with_containers(db: str, auth: str, world: str) -> CatalogEntry:
    """A copy of the shipped TBC entry wearing three other container names.

    Module-level because two tests need it and they need it to agree: one pins what
    `_container_prefix()` answers for a shape, the other renders the same shape and
    reads the service keys back. Written apart, they could have drifted onto
    different names and each gone on passing.
    """
    tbc = load_catalog().get("wow-tbc")
    return tbc.model_copy(
        update={
            "containers": tbc.containers.model_copy(update={"db": db, "auth": auth, "world": world})
        }
    )


def test_a_container_prefix_that_cannot_be_derived_is_refused_not_guessed() -> None:
    """`os.path.commonprefix` answers a plausible wrong string; `_container_prefix()` must not.

    The three shapes the reviewer produced by running it: names sharing no first
    character (`""`), one name a literal prefix of the others (`"db"`, so
    `{{CONTAINER_PREFIX}}db` renders `dbdb`), and an accidental character-wise prefix
    that eats the separator (`"abc"`). None of them raise on their own.

    Only the first is refused here, and the last two are asserted to be ANSWERED
    rather than left unmentioned. Until 2026-09-04 they raised too, but only
    because every fixture below also carried `services=("db","realmd","mangosd")`
    and the rebuild branch compared against that — a declaration no shipped entry
    has had since 2026-09-01 and one this module now refuses outright
    (bug-checklist §30). Nothing in the entry names the suffix a template writes,
    so `dbdb` cannot be told from `tbc-db` at this level; the shape that catches
    the other two is
    `test_a_commonprefix_the_template_does_not_share_is_caught_by_the_rendered_file`,
    which renders them and reads the service keys back.

    Characterisation, not a discriminator of the §30 fix: measured 2026-09-04 on
    m910q with the pre-§30 `composegen.py` (the rebuild branch restored, md5
    86ffd5ba3e8f21ab10a66c33864a40b8) this test passed unchanged — with no
    `services` on the fixtures the old branch never ran either. The test that
    fails on that file is
    `test_an_entry_this_module_renders_may_not_declare_compose_services`.
    """
    with pytest.raises(composegen.ComposeGenError, match="share no common prefix"):
        composegen._container_prefix(with_containers("mysql", "authserver", "worldserver"))
    assert composegen._container_prefix(with_containers("db", "dbauth", "dbworld")) == "db"
    eaten = with_containers("abc-db", "abcd-realmd", "abcx-mangosd")
    assert composegen._container_prefix(eaten) == "abc"
    accepted = with_containers("x-db", "x-realmd", "x-mangosd")
    # Twice: the fixture is rebuilt per call and the function is pure, so a second
    # ask must answer the same thing. A helper that mutated the entry it copied
    # from would pass once and diverge here.
    assert composegen._container_prefix(accepted) == "x-"
    assert composegen._container_prefix(accepted) == "x-"


@pytest.mark.parametrize(
    ("containers", "prefix"),
    [
        (("db", "dbauth", "dbworld"), "db"),
        (("abc-db", "abcd-realmd", "abcx-mangosd"), "abc"),
    ],
    ids=["one-name-is-the-prefix", "the-separator-is-eaten"],
)
def test_a_commonprefix_the_template_does_not_share_is_caught_by_the_rendered_file(
    tmp_path: Path, containers: tuple[str, str, str], prefix: str
) -> None:
    """Where the two shapes `_container_prefix()` cannot judge DO get caught, measured.

    §30 recommended deleting the rebuild branch on the grounds that "the cross-check
    now covers the separator-eaten case". That was a claim about a test, and this is
    the measurement behind it: both shapes are rendered through the real shared
    CMaNGOS templates and the service keys are read back out of the text. They come
    out as the prefix in front of the template's own literal suffixes — `dbdb`,
    `abcdb` and friends — and share not one name with what the entry would hand
    `compose up`, which is the disagreement
    `test_every_service_the_catalog_selects_is_defined_in_the_rendered_compose_file`
    reports for every shipped entry.

    Asserting `defined` in full rather than "the container names are missing from
    it": a helper that answered the empty set would satisfy the disjointness on its
    own, and the point is that the file really does define three services under
    names nobody selects.

    Characterisation, not a discriminator of the §30 fix: measured 2026-09-04 on
    m910q with the pre-§30 `composegen.py` (the rebuild branch restored, md5
    86ffd5ba3e8f21ab10a66c33864a40b8) both parametrisations passed unchanged —
    nothing here declares `services`, so neither the old branch nor the new
    refusal is reached. It pins what the templates render for these shapes; the
    test that fails on the old file is
    `test_an_entry_this_module_renders_may_not_declare_compose_services`.
    """
    entry = with_containers(*containers)
    # Not refused at generation time -- that is the whole subject of the test.
    assert composegen._container_prefix(entry) == prefix
    plan = render_generated(entry, tmp_path / "wow")
    defined = service_names(plan.base)
    assert defined == {prefix + suffix for suffix in ("db", "realmd", "mangosd")}
    selected = set(entry.container_spec().compose_services())
    assert selected == set(containers), "with no declaration, the selection IS the container names"
    assert not selected & defined, (
        f"{sorted(selected)} would be handed to `compose up`, and the rendered file "
        f"defines {sorted(defined)}: every one of them answers `no such service`"
    )


def test_an_entry_this_module_renders_may_not_declare_compose_services(tmp_path: Path) -> None:
    """RED for bug-checklist §30: the rule accepted the declaration that breaks `compose up`.

    Probed on the shipped `wow-tbc` entry, the rebuild branch was satisfiable only
    by the defect §26 fixed. `("tbc-db","tbc-realmd","tbc-mangosd")` — the names
    `compose up` has to be given, because the template's service keys ARE the
    container names — was refused, since `tbc-` in front of them rebuilds
    `tbc-tbc-db`. `("db","realmd","mangosd")` — the declaration that would have
    killed three installs at `no such service` — was accepted, and
    `compose_services()` then answered those three bare suffixes.

    Both shapes are refused now, and for the same reason: for an entry rendered
    from these templates the field has no correct value at all. Asserted through
    `render()` as well as against the function, because a refusal nothing reaches
    is not a refusal — `entry_tokens()` is the only caller in the app.

    This is the one test of the three added for §30 that discriminates: measured
    2026-09-04 on m910q with the pre-§30 `composegen.py` restored (md5
    86ffd5ba3e8f21ab10a66c33864a40b8) it failed at the first `raises` — `correct`
    was refused with the rebuild text, which does not name `containers.services`.
    """
    tbc = load_catalog().get("wow-tbc")

    def declaring(services: tuple[str, str, str]) -> CatalogEntry:
        return tbc.model_copy(
            update={"containers": tbc.containers.model_copy(update={"services": services})}
        )

    correct = declaring((tbc.containers.db, tbc.containers.auth, tbc.containers.world))
    the_bug = declaring(("db", "realmd", "mangosd"))
    for entry in (correct, the_bug):
        with pytest.raises(composegen.ComposeGenError, match=r"containers\.services") as raised:
            composegen._container_prefix(entry)
        message = str(raised.value)
        # The old refusal text was a set of instructions for reproducing §26 —
        # "Name every container after its service with one shared prefix in front
        # of it" — handed to whoever adds the fifth game. Pinned so the sentence
        # cannot come back.
        assert "after its service" not in message
        assert "Delete containers.services" in message
        with pytest.raises(composegen.ComposeGenError, match=r"containers\.services"):
            composegen.entry_tokens(entry)
        with pytest.raises(composegen.ComposeGenError, match=r"containers\.services"):
            render_generated(entry, tmp_path / "wow")


@pytest.mark.parametrize(
    "containers",
    [("db", "dbauth", "dbworld"), ("abc-db", "abcd-realmd", "abcx-mangosd")],
    ids=["one-name-is-the-prefix", "the-separator-is-eaten"],
)
def test_the_refusal_names_no_service_the_rendered_file_does_not_define(
    tmp_path: Path, containers: tuple[str, str, str]
) -> None:
    """RED 2026-09-04 on m910q (§30, round 2): the replacement refusal taught the bug again.

    §30's headline complaint was that the refusal text read as instructions for
    reproducing §26. The first rewrite refused the declaration and then, in the same
    sentence, named the entry's three container names as "the names compose must be
    given". Reproduced against the real `shared/cmangos` templates with containers
    `db`/`dbauth`/`dbworld` and a declaration on top:

        REFUSAL: wow-tbc declares containers.services (db, realmd, mangosd), but ...
                 The names compose must be given are db, dbauth, dbworld ...
        service keys the shared/cmangos template would write: ['dbdb', 'dbmangosd', 'dbrealmd']

    Three names in the advice, three different services in the file it had just
    described; every name in the advice answers `no such service`. The suffixes the
    template writes are not known to `_container_prefix()`, so the only service names
    a refusal could print honestly are ones it has seen rendered, and it has seen
    none — so it prints none. Held to that the only way it can be: the same shape is
    rendered here, the service keys read back, and every container name the file does
    NOT define must be absent from the message.

    The declared services are three words that are neither container names nor
    service keys, so the echo of the declaration cannot collide with the check: with
    `("db","realmd","mangosd")` declared, the word `db` would be in the message
    legitimately, as the thing the entry said.
    """
    shape = with_containers(*containers)
    declaring = shape.model_copy(
        update={
            "containers": shape.containers.model_copy(update={"services": ("one", "two", "three")})
        }
    )
    with pytest.raises(composegen.ComposeGenError, match=r"containers\.services") as raised:
        composegen._container_prefix(declaring)
    message = str(raised.value)
    defined = service_names(render_generated(shape, tmp_path / "wow").base)
    undefined = set(containers) - defined
    assert undefined == set(containers), "the shapes were chosen so no container name is a key"
    for name in sorted(undefined):
        assert not re.search(rf"(?<![\w-]){re.escape(name)}(?![\w-])", message), (
            f"the refusal names {name!r}, and the rendered file defines {sorted(defined)}: "
            "following the advice answers `no such service`"
        )


# -- G.5: the shared CMaNGOS compose templates ---------------------------------

CMANGOS_ENTRIES = [load_catalog().get(g) for g in ("wow-tbc", "wow-vanilla", "wow-tortoise")]


def render_generated(entry: CatalogEntry, server_dir: Path) -> composegen.ComposePlan:
    """A generated-password entry needs the secret handed in; it never reaches the file."""
    return composegen.render(
        entry,
        server_dir,
        templates_root=TEMPLATES,
        db_password="tbc-0123456789abcdef",
        bind_label=":z",
        platform_id=lambda: "linux",
    )


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=lambda e: e.id)
def test_the_shared_cmangos_templates_render_for_every_family_entry(
    tmp_path: Path, entry: CatalogEntry
) -> None:
    plan = render_generated(entry, tmp_path / "wow")
    for text in (plan.base, plan.override, plan.build):
        assert text.startswith(composegen.GENERATED_MARKER)
        assert "{{" not in text
    assert "ports" in keys_in(plan.base)
    assert "ports" not in keys_in(plan.override)
    assert "ports" not in keys_in(plan.build)
    assert "build" not in keys_in(plan.base)


def test_the_cmangos_services_are_named_after_their_containers(tmp_path: Path) -> None:
    """The rendered file's service keys ARE the entry's container names.

    This is the template's side of the AzerothCore convention, asserted against the
    entry's own three names rather than against literals. `compose up -d --no-deps
    <db>` then works with `ContainerSpec.services` left at its default — which is what
    the entry does today, having stopped declaring `containers.services` on 2026-09-01;
    the docstring here used to state that as a fact while the catalog was overriding it
    with `db`/`realmd`/`mangosd`, and neither this test nor the catalog's own could see
    the disagreement. `test_every_service_the_catalog_selects_is_defined_in_the_rendered_
    compose_file` is the one that now compares the two sides.
    """
    entry = load_catalog().get("wow-tbc")
    plan = render_generated(entry, tmp_path / "wow")
    services = service_names(plan.base)
    assert services == {entry.containers.db, entry.containers.auth, entry.containers.world}
    for name in services:
        assert f"container_name: {name}" in plan.base


def test_the_cmangos_database_is_loopback_only_with_the_mariadb_healthcheck(tmp_path: Path) -> None:
    """Never the scripts' `3306:3306`; and the healthcheck is mariadb's own script."""
    entry = load_catalog().get("wow-tortoise")
    plan = render_generated(entry, tmp_path / "wow")
    assert f'"127.0.0.1:${{DOCKER_DB_EXTERNAL_PORT:-{entry.ports.db}}}:3306"' in plan.base
    assert '["CMD", "healthcheck.sh", "--connect", "--innodb_initialized"]' in plan.base
    assert "image: mariadb:10.6" in plan.base


def test_a_generated_password_never_reaches_the_compose_files(tmp_path: Path) -> None:
    plan = render_generated(load_catalog().get("wow-tbc"), tmp_path / "wow")
    for text in (plan.base, plan.override, plan.build):
        assert "tbc-0123456789abcdef" not in text
    assert "${DB_ROOT_PASSWORD:?Yu'lon .env is missing}" in plan.base
    assert plan.dotenv == {"DB_ROOT_PASSWORD": "tbc-0123456789abcdef"}


def test_the_cmangos_world_server_keeps_its_console_and_shutdown_grace(tmp_path: Path) -> None:
    plan = render_generated(load_catalog().get("wow-vanilla"), tmp_path / "wow")
    assert "stdin_open: true" in plan.base
    assert "stop_grace_period: 5m" in plan.base


def test_the_cmangos_build_overlay_builds_exactly_the_server_image(tmp_path: Path) -> None:
    entry = load_catalog().get("wow-tbc")
    plan = render_generated(entry, tmp_path / "wow")
    assert plan.build.count("dockerfile: Dockerfile") == 1
    assert "context: ." in plan.build
    refs = composegen.built_image_refs(entry, tmp_path / "wow", platform_id=lambda: "linux")
    base_images = set(re.findall(r"^\s*image:\s*(\S+)\s*$", plan.base, re.MULTILINE))
    assert set(refs) <= base_images
    assert len(refs) == 1 and refs[0].startswith("yulon.local/cmangos-tbc-server:native-")


def test_the_cmangos_build_overlay_names_a_service_the_base_file_defines(tmp_path: Path) -> None:
    """The build block's service key is the game's, not a literal.

    One template serves three games whose containers are `tbc-`, `vanilla-` and
    `tortoise-` prefixed, so the overlay can only name its service through
    `{{CONTAINER_PREFIX}}` — and compose merges by service NAME. A build block
    under a key the base file does not define is not an error compose reports:
    it silently adds a fourth, image-less service, and `build_staged()` builds
    nothing the stack then runs. The plan's own build template spelled that
    token while `render()` filled only `BUILD_CONTEXT`, which would have raised
    `unfilled compose placeholder` for every CMaNGOS install.
    """
    for entry in CMANGOS_ENTRIES:
        plan = render_generated(entry, tmp_path / entry.id)
        assert service_names(plan.build) <= service_names(plan.base)
        assert service_names(plan.build) == {entry.containers.world}


def test_the_cmangos_host_binds_carry_the_label_and_the_volume_does_not(tmp_path: Path) -> None:
    plan = render_generated(load_catalog().get("wow-tbc"), tmp_path / "wow")
    binds = [line for line in plan.base.splitlines() if line.strip().startswith("- ./")]
    assert len(binds) == 3
    assert all(line.endswith(":z") for line in binds)
    assert "- db-data:/var/lib/mysql" in plan.base
    assert "/var/lib/mysql:z" not in plan.base
    assert "./etc:/opt/mangos/etc:z" in plan.base
    assert "./data:/opt/mangos/data:z" in plan.base


# -- G.6: the per-game Dockerfile / dockerignore pair --------------------------


def cmangos_native(entry: CatalogEntry) -> NativeInstall:
    """The entry's native block, proved present so the tests below can index it."""
    native = entry.install.native
    assert native is not None and native.cmangos is not None
    assert native.dockerfile_dir is not None
    return native


def dockerfile_text(entry: CatalogEntry) -> str:
    native = cmangos_native(entry)
    path = TEMPLATES / str(native.dockerfile_dir) / "Dockerfile.tmpl"
    return composegen.fill(path.read_text(encoding="utf-8"), composegen.entry_tokens(entry))


def dockerignore_text(entry: CatalogEntry) -> str:
    native = cmangos_native(entry)
    path = TEMPLATES / str(native.dockerfile_dir) / "dockerignore.tmpl"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=lambda e: e.id)
def test_every_cmangos_entry_has_a_dockerfile_pair_that_fills_from_its_tokens(
    entry: CatalogEntry,
) -> None:
    """Rendered through the one `fill()`; the marker is a `#` first line so the
    generated-file rule (`dockerfile.write()` refuses an unmarked file) has something to read.

    The pair fills from `entry_tokens(entry)` ALONE — not from the installer's
    wider mapping — because that is all a build-time render has: the image is
    built before any per-install secret or port is in hand. A template reaching
    for `DB_PASSWORD` therefore raises here, not in front of a user.

    That last sentence is load-bearing and fragile, so: it holds only because
    `entry_tokens()` never contains `DB_PASSWORD`, which makes a template that
    spelled the token fail here as UNFILLED. Filling from the installer's real
    mapping instead — the plausible "make this realistic" edit — would delete
    the check silently. Note that since 7.3 the realistic mapping is
    `CmangosInstaller._public_tokens()`, which has no secret in it either, so
    the edit would look harmless AND still delete the check. Do not; and if you
    do, the guarantee is still `dockerfile.SECRET_TOKENS`' by-name refusal,
    covered in `test_dockerfile.py` and `test_families_cmangos.py`.
    """
    native = cmangos_native(entry)
    template_dir = TEMPLATES / str(native.dockerfile_dir)
    tokens = composegen.entry_tokens(entry)
    for name in ("Dockerfile.tmpl", "dockerignore.tmpl"):
        text = composegen.fill((template_dir / name).read_text(encoding="utf-8"), tokens)
        assert text.startswith(composegen.GENERATED_MARKER), name
        assert "{{" not in text
    dockerfile = dockerfile_text(entry)
    assert native.cmangos is not None
    assert f"make -j{native.cmangos.dockerfile.make_jobs}" in dockerfile
    assert "git clone" not in dockerfile, "sources are COPY'd from the clone stage, never fetched"
    core = entry.emulator.sources[0].dest
    assert f"COPY {core} " in dockerfile
    assert f"CMAKE_INSTALL_PREFIX={tokens['CORE_DIR']}" in dockerfile
    assert tokens["CORE_DIR"].startswith("/opt/"), "the in-image prefix, never the host src dir"
    ignore = dockerignore_text(entry)
    assert ".git" in ignore, "the .git tree is not build context"


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=lambda e: e.id)
def test_every_cmangos_dockerignore_admits_only_the_core_tree_it_copies(
    entry: CatalogEntry,
) -> None:
    """`*` then one re-include, and the `.git` of every tree that re-include pulls in.

    The build context is the server dir, which by build time also holds the
    world-database checkout (hundreds of MB), the extracted client data and the
    `.git` of every clone. Only the tree the Dockerfile COPYs may reach the
    daemon, and the re-include has to name exactly that tree: a bare `!src`
    would hand over `src/tbc-db` as well, which the SQL import streams from the
    host and the image has no use for.
    """
    ignore = dockerignore_text(entry)
    lines = [line for line in ignore.splitlines() if line and not line.startswith("#")]
    assert lines[0] == "*"
    core = entry.emulator.sources[0].dest
    assert [line for line in lines if line.startswith("!")] == [f"!{core}"]
    assert f"{core}/.git" in lines
    for source in entry.emulator.sources[1:]:
        nested = source.dest.startswith(f"{core}/")
        assert nested == (f"{source.dest}/.git" in lines), source.dest
        assert nested or source.dest not in ignore, f"{source.dest} is host-only, not context"


def test_the_tortoise_dockerfile_keeps_every_flag_and_library_its_script_proved() -> None:
    """The Tortoise image is transcribed from `install-tortoise-wow-wsl.sh`, which ran.

    Three things in that script are load-bearing and were missing from the
    plan's transcription of it:

    * `-DBUILD_PLAYERBOTS=ON`. The entry clones `Shyalya/tortoise-wow` on branch
      `playerbots-integration-gh` and its description sells the bots; without
      the flag the fork compiles into a bot-less server that boots and looks
      fine. It is also what emits `aiplayerbot.conf.dist` into `etc/`, which the
      conf stage copies out of the image.
    * `libboost-{thread,filesystem,system}-dev`. The script installed them in
      the ONE image that both compiled and ran, so here they belong to both
      stages — a runtime stage without them is a `mangosd` that cannot link.
    * the `sql/` and `tools/mmap/` trees under `CORE_DIR`, named by this entry's
      own `Database.AutoUpdate.Path` conf value and by `mmaps.argv`. If the
      image does not carry them, those two settings point at nothing.
    """
    entry = load_catalog().get("wow-tortoise")
    native = cmangos_native(entry)
    assert native.cmangos is not None
    dockerfile = dockerfile_text(entry)
    for flag in (
        "-DUSE_EXTRACTORS=ON",
        "-DUSE_SCRIPTS=ON",
        "-DUSE_STD_MALLOC=ON",
        "-DDEBUG_SYMBOLS=OFF",
        "-DUSE_ANTICHEAT=OFF",
        "-DALLOW_TURTLE_ADDONS=ON",
        "-DBUILD_PLAYERBOTS=ON",
    ):
        assert flag in dockerfile, flag
    for package in ("libboost-thread-dev", "libboost-filesystem-dev", "libboost-system-dev"):
        assert dockerfile.count(package) == 2, f"{package} is a build AND a runtime dependency"
    core_dir = composegen.entry_tokens(entry)["CORE_DIR"]
    autoupdate = native.cmangos.conf.files["mangosd.conf"].keys["Database.AutoUpdate.Path"]
    assert f"{core_dir}/sql" in dockerfile and f"{core_dir}/sql" in autoupdate
    for argument in native.cmangos.mmaps.argv:
        if argument.startswith(f"{core_dir}/src/"):
            assert str(PurePosixPath(argument).parent) in dockerfile, argument


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=lambda e: e.id)
def test_the_cmangos_runtime_stage_carries_the_tools_the_extract_stage_runs(
    entry: CatalogEntry,
) -> None:
    """One image serves the server AND the extractors, as in every script it replaces.

    `extract.image` is `server` for all three games, so the argv in the catalog
    are paths inside THIS image: the builder's whole install prefix has to land
    in the runtime stage, and the cmake run has to have asked for the tools.
    """
    native = cmangos_native(entry)
    assert native.cmangos is not None
    assert native.cmangos.extract.image == "server"
    dockerfile = dockerfile_text(entry)
    core_dir = composegen.entry_tokens(entry)["CORE_DIR"]
    assert f"COPY --from=builder {core_dir} {core_dir}" in dockerfile
    assert dockerfile.count("FROM ubuntu:22.04") == 2, "a builder stage and a slim runtime"
    for tool in native.cmangos.extract.tools:
        assert tool.argv[0].startswith(f"{core_dir}/bin/"), tool.argv[0]
