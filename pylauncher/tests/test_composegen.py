"""Tests for compose generation (`yulon.catalog.composegen`, roadmap 6.2).

Pure functions over template data, so everything here is byte-level and needs
no daemon. The load-bearing test is `test_ports_appear_in_exactly_one_file`:
compose CONCATENATES `ports:` across files, so a port added to the override
publishes a second binding instead of replacing the first, and nothing else in
this project would notice until two containers fought over 3724.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog import composegen
from yulon.catalog.catalog import load_catalog

ENTRY = load_catalog().get("wow-wotlk")
TEMPLATES = resources.installers_dir()

BOT_POPULATION = "500"
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


@pytest.mark.parametrize("password", ['pass"word', "pass$word", "pass;word", "pass#word"])
def test_a_password_that_cannot_be_spliced_is_refused(tmp_path: Path, password: str) -> None:
    """Refused rather than escaped: the same scalar lands in two contexts at once.

    A bare YAML value and a quoted, semicolon-separated one, with compose's own
    interpolation running on top of both — no single escaping survives all of
    it, and this is an install-time option the user can simply spell
    differently.
    """
    with pytest.raises(composegen.ComposeGenError, match="database root password"):
        composegen.render(
            ENTRY,
            tmp_path / "wow",
            templates_root=TEMPLATES,
            db_password=password,
            platform_id=lambda: "macos",
        )


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
    """
    written: dict[str, list[str]] = {}
    for path in sorted(TEMPLATES.rglob("*")):
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        found = [m.group("ac") or m.group("cmangos") for m in _BOT_POPULATION_WRITE.finditer(text)]
        if found:
            written[path.relative_to(TEMPLATES).as_posix()] = found

    assert written == {
        "wow-tbc/install-wow-tbc.sh": [BOT_POPULATION, BOT_POPULATION],
        "wow-vanilla/install-wow-vanilla.sh": [BOT_POPULATION, BOT_POPULATION],
        "wow-wotlk/install-wow-wotlk-fedora.sh": [BOT_POPULATION, BOT_POPULATION],
        "wow-wotlk/install-wow-wotlk-ubuntu.sh": [BOT_POPULATION, BOT_POPULATION],
        "wow-wotlk/install-wow-wotlk.sh": [BOT_POPULATION, BOT_POPULATION],
    }, "an installer ships a bot population that is not the one that was decided"

    # The sixth place is the native path's data, which the scan above cannot
    # see: it lives in `catalog.json`, not under `catalog/installers/`.
    assert ENTRY.install.native is not None
    assert ENTRY.install.native.azerothcore is not None
    min_bots = ENTRY.install.native.azerothcore.world_env["AC_AI_PLAYERBOT_MIN_RANDOM_BOTS"]
    assert min_bots == BOT_POPULATION


def test_the_image_refs_match_the_services_the_build_overlay_actually_builds(
    tmp_path: Path,
) -> None:
    """`BUILT_SERVICES` is a third copy of four strings, and nothing cross-checked it.

    The same four names live in `build.yml.tmpl`'s `target:` lines, in
    `base.yml.tmpl`'s `image:` refs, and in `BUILT_SERVICES`. They agree today.
    Nothing enforced it: rename a `target:` without touching the tuple and
    `images_built()` asks the daemon for a reference that will never exist, so
    it answers False forever and every resume re-runs the multi-hour build —
    permanently, silently, with a green suite. That is precisely the bug the
    per-reference rewrite was written to fix, reintroduced through the one seam
    nothing checked (review, 2026-08-24).

    Derived from the rendered files rather than restated, so the assertion
    cannot drift the way the tuple did.
    """
    plan = render(tmp_path / "wow")
    refs = composegen.built_image_refs(tmp_path / "wow", platform_id=lambda: "linux")

    built_targets = set(re.findall(r"^\s*target:\s*(\S+)\s*$", plan.build, re.MULTILINE))
    assert built_targets == set(composegen.BUILT_SERVICES), (
        built_targets,
        composegen.BUILT_SERVICES,
    )

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
