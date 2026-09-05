"""Cross-file invariants of `catalog.json` and the templates it names (roadmap 7.3, task G.7).

Every case is enumerated over the shipped catalog rather than over a list of ids, because
the point of one engine per lineage is that the same rule holds for every game it serves.
A red here names a drift in DATA or in a TEMPLATE, and the fix belongs in the file the
assertion names rather than in the number that made it red. One case is not like that:
the derived game-word set at the bottom has measured false positives, recorded in
`game_words()`, so a red from `test_family_modules_contain_no_game_literal` is read
before it is believed and may be answered by rewording prose. Nothing here starts a
container or reads a network.

**What this file deliberately does NOT assert.** `"cmangos" in families.FAMILIES` is a
declaration, and the 7.3 plan had this task assert it. The property it gestures at is owned
by two tests that cross the whole path instead:
`tests/test_families_cmangos.py::test_every_cmangos_entry_dispatches_to_an_engine_that_runs_this_familys_stages`
and `tests/test_spine.py::test_every_shipped_native_entry_reaches_the_class_its_family_id_names`.
Both go from a catalog entry through `installer_for()` to the object that comes back and ask
that object what stages it runs. A membership check beside them would be the weakest claim in
the file, and it would pass on the day either of those failed.

**Catalog data that outruns its engine is a defect, not a window.** Until F.3 an entry whose
family had no registered engine fell back to the bash `Installer`; F.3 deleted that script and
the branch that reached it, so `family_for()` now refuses such an entry with a sentence — and
`families.is_registered()`, the predicate whose docstring still called that state a supported
window, was deleted with this task because nothing had called it since.
"""

from __future__ import annotations

import ast
import re
from dataclasses import fields
from pathlib import Path

import pytest

from yulon import resources
from yulon.catalog import composegen, families, native
from yulon.catalog.catalog import CatalogEntry, NativeInstall, load_catalog
from yulon.catalog.families import FAMILIES
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, installer_for

ENTRIES: list[CatalogEntry] = list(load_catalog().games)
IDS = [entry.id for entry in ENTRIES]
CMANGOS_ENTRIES = [
    entry
    for entry in ENTRIES
    if entry.install.native is not None and entry.install.native.cmangos is not None
]
CMANGOS_IDS = [entry.id for entry in CMANGOS_ENTRIES]

assert ENTRIES, "the shipped catalog has no entries, so every case below is collected empty"
assert CMANGOS_ENTRIES, "no shipped entry carries a `cmangos` block; the CMaNGOS cases are empty"

TEMPLATES = resources.installers_dir()
CATALOG_PACKAGE = Path(composegen.__file__).resolve().parent
FAMILIES_DIR = Path(families.__file__).resolve().parent

COMPOSE_TEMPLATES = ("base.yml.tmpl", "override.yml.tmpl", "build.yml.tmpl")
DOCKERFILE_TEMPLATES = ("Dockerfile.tmpl", "dockerignore.tmpl")

# Every `./…:` line is a host bind; a named volume never starts with `./`.
_HOST_BIND = re.compile(r"^\s*-\s*\./")
_NAMED_VOLUME = re.compile(r"^\s*-\s*[A-Za-z][\w-]*:/")
_KEY = re.compile(r"^\s*(?:-\s*)?(?P<key>[A-Za-z_][\w.-]*)\s*:")
_SERVICE = re.compile(r"^  (?P<name>[A-Za-z0-9_-]+):\s*$")
_IMAGE = re.compile(r"^\s+image:\s*(?P<ref>\S+)\s*$")
_CONTAINER_NAME = re.compile(r"^\s+container_name:\s*(?P<name>\S+)\s*$")
_SERVICE_VOLUMES = re.compile(r"^(?P<indent>\s+)volumes:\s*$")
# Both spellings compose honours: `${VAR:-x}` defaults when unset OR empty,
# `${VAR-x}` when unset. Only the first was looked for until 2026-09-02.
_PASSWORD_DEFAULT = re.compile(r"\$\{DB_ROOT_PASSWORD:?-")

IMPORT_ROOT = Path(composegen.__file__).resolve().parents[2]
"""The directory the `yulon` package sits in, so a module path becomes a dotted name."""

CONTROLLER_MODULE = "yulon.controller"

TEST_PASSWORD = "generated-0123456789abcdef"
"""A stand-in for the per-install secret, in the shape `_refuse_unsafe()` accepts."""


# -- reading the rendered YAML without a YAML parser --------------------------
#
# Deliberately line-based. These cases are about what a template SPELLS and what
# `render()` writes, and a YAML round-trip answers about the document a parser
# reconstructed: a `ports:` key that compose would concatenate across two files
# is two lines and one key, and only the lines tell them apart.


def keys_in(text: str) -> set[str]:
    """Every mapping key the text spells, comments excluded."""
    found: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _KEY.match(line)
        if match:
            found.add(match.group("key"))
    return found


def service_lines(compose_text: str) -> list[str]:
    """The lines under `services:` and only there.

    `networks:` and `volumes:` declare keys at the same indent as a service name,
    so the scan stops at the next top-level key rather than reading a network as
    a service.
    """
    inside = False
    out: list[str] = []
    for line in compose_text.splitlines():
        if re.match(r"^services:\s*$", line):
            inside = True
            continue
        if inside and re.match(r"^\S", line):
            break
        if inside:
            out.append(line)
    return out


def service_names(compose_text: str) -> set[str]:
    return {
        match.group("name")
        for line in service_lines(compose_text)
        if (match := _SERVICE.match(line))
    }


def images_by_service(compose_text: str) -> dict[str, str]:
    """`{service: image ref}` for every service in the file that names an image."""
    found: dict[str, str] = {}
    service = ""
    for line in service_lines(compose_text):
        top = _SERVICE.match(line)
        if top:
            service = top.group("name")
            continue
        image = _IMAGE.match(line)
        if image and service:
            found[service] = image.group("ref")
    return found


def container_names_by_service(compose_text: str) -> dict[str, str]:
    """`{service: container_name}` — the PAIRING, and not two sets compared apart.

    Until 2026-09-02 the case below compared the set of service names to the
    entry's containers and then asked that each name appear as SOME
    `container_name:` line anywhere in the file. Swapping the two
    `container_name:` lines between the `realmd` and `mangosd` services leaves
    both of those true, and the whole suite stayed green with them swapped —
    while every command addressed by service name reaches the other container.
    The convention `docker.start_database()` relies on is service<->container
    IDENTITY, which is the half that was never asserted.
    """
    found: dict[str, str] = {}
    service = ""
    for line in service_lines(compose_text):
        top = _SERVICE.match(line)
        if top:
            service = top.group("name")
            continue
        named = _CONTAINER_NAME.match(line)
        if named and service:
            found[service] = named.group("name")
    return found


def volume_entries(text: str) -> list[str]:
    """Every list item under a SERVICE's `volumes:` key, in file order.

    The top-level `volumes:` block declares named volumes as a mapping and is
    skipped by requiring the key itself to be indented.
    """
    out: list[str] = []
    indent: int | None = None
    for line in text.splitlines():
        if indent is not None:
            if not line.strip():
                continue
            if len(line) - len(line.lstrip()) > indent:
                if not line.strip().startswith("#"):
                    out.append(line)
                continue
            indent = None
        match = _SERVICE_VOLUMES.match(line)
        if match:
            indent = len(match.group("indent"))
    return out


# -- building the real objects these invariants are asked of ------------------


def native_of(entry: CatalogEntry) -> NativeInstall:
    block = entry.install.native
    assert block is not None, f"{entry.id} has no `install.native` block"
    return block


def render(entry: CatalogEntry, server_dir: Path) -> composegen.ComposePlan:
    password = None if entry.install.password.mode == "fixed" else TEST_PASSWORD
    return composegen.render(
        entry,
        server_dir,
        templates_root=TEMPLATES,
        db_password=password,
        bind_label=":z",
        platform_id=lambda: "linux",
    )


def engine_for(entry: CatalogEntry) -> native.StagedInstaller:
    """The engine an Install press would reach, built the way `catalog_view` builds it."""
    engine = installer_for(entry, platform_id=lambda: "linux")
    assert isinstance(engine, native.StagedInstaller), entry.id
    return engine


def cmangos_engine(entry: CatalogEntry) -> CmangosInstaller:
    engine = engine_for(entry)
    assert isinstance(engine, CmangosInstaller), entry.id
    return engine


def sentinel_secrets() -> native.Secrets:
    """A `Secrets` whose every field holds a distinct marker, built from its own fields.

    From `dataclasses.fields()` and not from `db_password=…`, so a secret added to
    `native.Secrets` is carried into the mapping these cases spend on the day it is
    added rather than on the day somebody remembers this file.
    """
    named = fields(native.Secrets)
    assert named, "native.Secrets has no fields, so the token cases below prove nothing"
    return native.Secrets(**{field.name: f"SENTINEL-{field.name}" for field in named})


def fills_from(text: str, tokens: dict[str, str], where: str) -> None:
    """`composegen.fill()`, with the entry's own coordinates added to its refusal.

    The rule that fires on an unknown token is `fill()`'s own `ComposeGenError:
    unfilled compose placeholder {{…}}` — it RAISES rather than returning the
    text, so `assert "{{" not in composegen.fill(...)`, which is how the case
    below was spelled until 2026-09-02, is a line that cannot run. Red either
    way, but the assertion a reader was shown was not the rule that would fire,
    and `fill()`'s message names the token without naming WHERE in the entry it
    sits.
    """
    try:
        composegen.fill(text, tokens)
    except composegen.ComposeGenError as exc:
        raise AssertionError(f"{where}: {exc}") from None


def secret_tokens(entry: CatalogEntry, server_dir: Path) -> dict[str, str]:
    """The mapping the conf and import stages spend, asked of the engine itself.

    A copy typed out here would be the same mapping written twice, and the half
    that rots is the copy: a token added to `_public_tokens()` would leave this
    file's set narrower than the one an install spends, so a conf value naming the
    new token would pass here and fail at stage time.

    The ready-marker case further down DOES hand-type its two keys, against that
    rule, and deliberately — see its docstring. The rule holds where the mapping
    is WIDE and the test only spends it; it inverts where the mapping's width is
    itself the property under test, because then asking the engine for the
    mapping and filling with the same mapping asserts nothing at all.
    """
    engine = cmangos_engine(entry)
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family=native_of(entry).family,
            completed=(),
        ),
        cancel=None,
        secrets=sentinel_secrets(),
    )
    return engine._secret_tokens(ctx)


# -- the templates the catalog names ------------------------------------------


def test_the_named_template_dirs_and_the_shipped_ones_are_the_same_set() -> None:
    """Both directions, so neither a missing template nor an orphaned one can hide.

    One direction is what an install needs; the other is what a rename leaves
    behind. A directory of templates no entry names renders for no game and is
    still shipped in the bundle, and the day someone edits that copy of
    `base.yml.tmpl` is the day it costs something.
    """
    on_disk = {path.parent for path in TEMPLATES.rglob("*.tmpl")}
    assert on_disk, f"no templates are shipped under {TEMPLATES}"
    named: set[Path] = set()
    for entry in ENTRIES:
        block = native_of(entry)
        templates = TEMPLATES / block.templates
        named.add(templates)
        for name in COMPOSE_TEMPLATES:
            assert (templates / name).is_file(), f"{entry.id}: {templates / name}"
        if block.dockerfile_dir is not None:
            build_context = TEMPLATES / block.dockerfile_dir
            named.add(build_context)
            for name in DOCKERFILE_TEMPLATES:
                assert (build_context / name).is_file(), f"{entry.id}: {build_context / name}"
    orphans = sorted(str(path.relative_to(TEMPLATES)) for path in on_disk - named)
    assert not orphans, f"template directories no catalog entry names: {orphans}"


# -- what `render()` writes ---------------------------------------------------


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_ports_live_in_one_file_and_nothing_survives_unfilled(
    entry: CatalogEntry, tmp_path: Path
) -> None:
    plan = render(entry, tmp_path / entry.id)
    assert "ports" in keys_in(plan.base)
    assert "ports" not in keys_in(plan.override), "compose CONCATENATES ports lists across files"
    assert "ports" not in keys_in(plan.build)
    assert "build" not in keys_in(plan.base), "the build overlay is never auto-loaded"
    for name, text in (("base", plan.base), ("override", plan.override), ("build", plan.build)):
        assert "{{" not in text, f"{entry.id}: {name} kept a placeholder"
        assert text.startswith(composegen.GENERATED_MARKER), f"{entry.id}: {name}"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_the_build_overlay_builds_exactly_the_images_the_entry_declares(
    entry: CatalogEntry, tmp_path: Path
) -> None:
    """`install.native.images`, the base file's `image:` refs and the build overlay, crossed.

    `built_image_refs()` is what a resume asks "has this been built?" with, and it
    is computed from the entry alone. If it names a reference the base file does
    not give to a service the overlay builds, a finished build reports as unbuilt
    and the resume recompiles — the failure that function was added to end,
    arriving from the other end.
    """
    block = native_of(entry)
    server_dir = tmp_path / entry.id
    plan = render(entry, server_dir)
    built = service_names(plan.build)
    assert built, f"{entry.id}: the build overlay defines no service"
    image_of = images_by_service(plan.base)
    tag = composegen.image_tag(server_dir, platform_id=lambda: "linux")
    suffixes = set()
    for name in built:
        assert name in image_of, f"{entry.id}: the overlay builds {name}, which the base file omits"
        ref = image_of[name]
        assert ref.startswith(block.image_prefix), (entry.id, ref, block.image_prefix)
        assert ref.endswith(f":{tag}"), (entry.id, ref, tag)
        suffixes.add(ref[len(block.image_prefix) : -len(tag) - 1])
    assert suffixes == set(block.images), (entry.id, sorted(suffixes), sorted(block.images))
    refs = composegen.built_image_refs(entry, server_dir, platform_id=lambda: "linux")
    assert set(refs) == {image_of[name] for name in built}, entry.id


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_service_is_named_after_a_container_the_entry_declares(
    entry: CatalogEntry, tmp_path: Path
) -> None:
    """The AzerothCore convention `docker.start_database()` relies on, held for every game.

    That convention is an IDENTITY — the service key and its `container_name:`
    are the same string — so it is asserted as one mapping and not as two sets
    that happen to have the same members. `container_names_by_service()` says
    what the weaker form let through.
    """
    plan = render(entry, tmp_path / entry.id)
    containers = entry.containers
    expected = {containers.db, containers.auth, containers.world}
    if containers.db_import:
        expected.add(containers.db_import)
    if containers.client_data:
        expected.add(containers.client_data)
    assert service_names(plan.base) == expected, entry.id
    named = container_names_by_service(plan.base)
    assert named == {name: name for name in expected}, (entry.id, named)


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_a_generated_password_is_never_spelled_in_a_template(entry: CatalogEntry) -> None:
    """The secret reaches compose through `.env`, so no template may carry it or a default.

    Read off the template files rather than off a render, on purpose. `render()`
    has its own refusal for `{{DB_PASSWORD}}` in generated mode, so a rendering
    version of this case would go red from THAT rule and stay green if this one
    were deleted. `${DB_ROOT_PASSWORD:-…}` is the half no refusal sees at all: a
    default IS password text, and it is what makes compose start a database with
    a password nobody chose instead of refusing to start.

    BOTH default spellings, since 2026-09-02. Compose honours `${VAR:-x}` (unset
    or empty) and `${VAR-x}` (unset only), and this looked for the first as a
    literal substring: `${DB_ROOT_PASSWORD-mangos}` was added to the shared
    CMaNGOS base template and the entire suite stayed green. The paragraph above
    is the argument against the spelling it did not check, word for word.

    The base file's `:?` is deliberately not widened the same way. `${VAR?msg}`
    refuses an unset variable and accepts an empty one, and an empty root
    password is the state this rule exists to keep out of a running database.

    The neighbour of a default is a BARE `${DB_ROOT_PASSWORD}`, which is not a
    default and starts the database with an empty root password just the same —
    so every mention of the variable in a generated-password template has to be
    the refusing `:?` form. That last rule subsumes both spellings above; the
    substring check is kept in front of it because "defaults the secret" is the
    sentence that names what went wrong, and "spells ${DB_ROOT_PASSWORD}" is the
    one that catches what nobody thought of.
    """
    block = native_of(entry)
    if entry.install.password.mode == "fixed":
        assert entry.install.password.value, f"{entry.id} says fixed and names no value"
        return
    for name in COMPOSE_TEMPLATES:
        text = (TEMPLATES / block.templates / name).read_text(encoding="utf-8")
        assert "{{DB_PASSWORD}}" not in text, f"{entry.id}: {name}"
        assert not _PASSWORD_DEFAULT.search(text), f"{entry.id}: {name} defaults the secret"
        for spelled in re.findall(r"\$\{DB_ROOT_PASSWORD[^}]*\}", text):
            assert spelled.startswith("${DB_ROOT_PASSWORD:?"), f"{entry.id}: {name} {spelled}"
    base = (TEMPLATES / block.templates / "base.yml.tmpl").read_text(encoding="utf-8")
    assert "${DB_ROOT_PASSWORD:?" in base, f"{entry.id}: the base file must refuse an empty .env"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_mount_is_a_labelled_host_bind_or_an_unlabelled_named_volume(
    entry: CatalogEntry, tmp_path: Path
) -> None:
    """SELinux: `:z` on every `./…` bind, never on a named volume, and no third kind.

    The third kind is why this enumerates every item under a service's `volumes:`
    rather than counting the lines that match a host-bind pattern. A bind spelled
    another way — an absolute path, a `${VAR}` prefix — matches neither pattern,
    so a per-entry count of matches would still come out right while the new
    mount carried no label, and the report would be "Permission denied" from a
    container on Fedora, long after the build.

    ALL THREE compose templates, since 2026-09-02. The loop read `base` and
    `override`, and the single line that mentioned the third asserted `":z" not
    in plan.build` under the sentence "the build overlay mounts nothing" — so an
    UNLABELLED `- ./cache:{{CORE_DIR}}/cache` added to
    `shared/cmangos/build.yml.tmpl` satisfied it: the missing label was the pass
    condition, the whole suite stayed green, and the report would have been the
    Fedora denial this case exists to prevent. The build overlay's real claim is
    the stronger one — it declares no mount at all — so that is what is
    asserted, on the template and on the render, and `":z" not in` is gone with
    it. An unlabelled bind there now dies on the label rule below and a
    labelled one dies on the emptiness rule after it.
    """
    block = native_of(entry)
    plan = render(entry, tmp_path / entry.id)
    labelled = 0
    for name in COMPOSE_TEMPLATES:
        path = TEMPLATES / block.templates / name
        for line in volume_entries(path.read_text(encoding="utf-8")):
            item = line.strip()
            if _HOST_BIND.match(line):
                assert line.rstrip().endswith("{{BIND_LABEL}}"), f"{entry.id} {name}: {item}"
                if name != "build.yml.tmpl":
                    labelled += 1
            elif _NAMED_VOLUME.match(line):
                assert "{{BIND_LABEL}}" not in line, f"{entry.id} {name}: {item}"
            else:
                raise AssertionError(
                    f"{entry.id} {name}: {item} is neither a `./` host bind nor a named "
                    "volume, so nothing here decides whether it needs the SELinux label"
                )
    assert labelled, f"{entry.id} mounts no host directory at all"
    build_template = TEMPLATES / block.templates / "build.yml.tmpl"
    for source, text in (
        ("build.yml.tmpl", build_template.read_text(encoding="utf-8")),
        ("the rendered build overlay", plan.build),
    ):
        mounts = [line.strip() for line in volume_entries(text)]
        assert not mounts, f"{entry.id}: {source} declares a mount: {mounts}"
    rendered = [
        line
        for text in (plan.base, plan.override)
        for line in volume_entries(text)
        if _HOST_BIND.match(line)
    ]
    assert len(rendered) == labelled, (entry.id, len(rendered), labelled)
    assert all(line.rstrip().endswith(":z") for line in rendered), (entry.id, rendered)


# -- the CMaNGOS family blocks ------------------------------------------------


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=CMANGOS_IDS)
def test_every_sql_pattern_is_a_relative_path_under_a_source_the_entry_clones(
    entry: CatalogEntry,
) -> None:
    """A glob is expanded against the server dir, so it may only name what a clone laid down."""
    data = native_of(entry).cmangos
    assert data is not None
    dests = [source.dest.rstrip("/") + "/" for source in entry.emulator.sources]
    seen = 0
    for phase in data.sql.phases:
        for pattern in list(phase.files) + list((phase.into_each or {}).values()):
            assert not pattern.startswith("/"), (entry.id, phase.name, pattern)
            assert ".." not in pattern, (entry.id, phase.name, pattern)
            assert any(pattern.startswith(dest) for dest in dests), (
                entry.id,
                phase.name,
                pattern,
                dests,
            )
            seen += 1
    assert seen, f"{entry.id}'s SQL plan names no file at all"


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=CMANGOS_IDS)
def test_no_sql_pattern_carries_a_token_because_expand_never_fills_one(
    entry: CatalogEntry,
) -> None:
    """`sqlplan.expand()` fills STATEMENTS and leaves file patterns alone, by design.

    "a dump that happens to contain `{{` is a dump and not a template", so a
    `{{TOKEN}}` in a glob is matched literally against the filesystem and nothing
    matches it. In a `fail` phase that is a refused install and in a `warn` phase
    a silently skipped dump, and neither names the token as the cause.

    `assert seen` since 2026-09-02, matching its four siblings. `SqlPhase.files`
    defaults to `()` and is the one part of the SQL plan with no `min_length`,
    so an entry whose every phase runs STATEMENTS reaches this loop with nothing
    to examine — measured by turning wow-tbc's twelve phases into statement
    phases, which took the sibling above red on its own `assert seen` and left
    this case green.
    """
    data = native_of(entry).cmangos
    assert data is not None
    seen = 0
    for phase in data.sql.phases:
        for pattern in list(phase.files) + list((phase.into_each or {}).values()):
            assert "{{" not in pattern, (entry.id, phase.name, pattern)
            seen += 1
    assert seen, f"{entry.id}'s SQL plan names no file pattern, so nothing was examined"


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=CMANGOS_IDS)
def test_no_verify_query_carries_a_token_because_nothing_fills_one(entry: CatalogEntry) -> None:
    """`sqlplan.verify()` hands `rule.query` to the client exactly as written.

    The plan for this task had this case assert the queries FILL, which is a
    property of a mapping nobody spends on them: `verify()` takes the rule's query
    straight to `sql_query()`, so a `{{DB_PASSWORD}}` in one would reach the
    database as those characters and the rule would fail as a syntax error at the
    end of an import that had otherwise finished.
    """
    data = native_of(entry).cmangos
    assert data is not None
    assert data.sql.verify, f"{entry.id} verifies nothing after its import"
    for rule in data.sql.verify:
        assert "{{" not in rule.query, (entry.id, rule.db, rule.query)


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=CMANGOS_IDS)
def test_every_conf_value_and_sql_statement_fills_from_the_mapping_its_stage_spends(
    entry: CatalogEntry, tmp_path: Path
) -> None:
    """A10/A6: an unknown token is an `InstallerError` mid-install; refused here instead.

    Through `composegen.fill()` directly and not through `conf.apply_table()` or
    `sqlplan.expand()`, so the only rule that can fail this case is the unfilled-
    placeholder one. Both of those callers wrap the same refusal in a sentence of
    their own, and a case routed through them would also go red for a missing file
    or for a database name outside the entry — failures that say nothing about the
    tokens this case is about.

    The refusal is `fill()`'s, not this file's: see `fills_from()` for why the
    assertion this case used to spell could never have run.
    """
    data = native_of(entry).cmangos
    assert data is not None
    tokens = secret_tokens(entry, tmp_path / entry.id)
    values = 0
    for file_name, patch in data.conf.files.items():
        for key, value in patch.keys.items():
            fills_from(value, tokens, f"{entry.id} {file_name}[{key}]")
            values += 1
    statements = 0
    for phase in data.sql.phases:
        for statement in phase.statements:
            fills_from(statement, tokens, f"{entry.id} phase {phase.name!r}")
            statements += 1
    assert values, f"{entry.id} patches no conf value"
    assert statements, f"{entry.id}'s SQL plan runs no literal statement"


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=CMANGOS_IDS)
def test_no_conf_value_carries_a_line_break_that_would_write_a_second_key(
    entry: CatalogEntry,
) -> None:
    """`conf.apply_table()` writes one line per key, so a break in a value writes two.

    `assert values` since 2026-09-02, matching its siblings. `ConfPatchTable.files`
    and `ConfPatch.keys` both carry `min_length=1` today, so this loop cannot in
    fact run empty against the shipped models — the guard is what keeps the case
    self-contained if either constraint is ever relaxed, and it costs one line.
    """
    data = native_of(entry).cmangos
    assert data is not None
    values = 0
    for file_name, patch in data.conf.files.items():
        for key, value in patch.keys.items():
            assert "\n" not in value and "\r" not in value, (entry.id, file_name, key)
            values += 1
    assert values, f"{entry.id} patches no conf value, so nothing was examined"


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=CMANGOS_IDS)
def test_every_schema_the_sql_plan_names_is_a_database_the_entry_owns(
    entry: CatalogEntry,
) -> None:
    """`sqlplan.expand()` refuses a name outside this mapping before it lists a directory.

    Enumerated off the plan and answered by the ENGINE's `_schemas()`, which is
    the mapping `expand()` is handed — so the question asked here is the one that
    would stop the import, and it is asked for every CMaNGOS game rather than for
    the one the family tests happen to drive.
    """
    data = native_of(entry).cmangos
    assert data is not None
    schemas = cmangos_engine(entry)._schemas()
    named = {*data.sql.create, data.sql.marker_db}
    named.update(rule.db for rule in data.sql.verify)
    named.update(table.db for table in data.sql.player_data)
    for phase in data.sql.phases:
        if phase.into is not None:
            named.add(phase.into)
        named.update(phase.into_each or {})
    assert named, f"{entry.id}'s SQL plan names no database at all"
    assert named <= set(schemas), (entry.id, sorted(named - set(schemas)))


@pytest.mark.parametrize("entry", CMANGOS_ENTRIES, ids=CMANGOS_IDS)
def test_every_path_the_family_runs_inside_the_image_is_absolute(entry: CatalogEntry) -> None:
    """These are `docker run` argv and a copy source, resolved by the image and not by a cwd."""
    data = native_of(entry).cmangos
    assert data is not None
    assert data.extract.tools, f"{entry.id} extracts with no tool"
    for tool in data.extract.tools:
        assert tool.argv[0].startswith("/"), (entry.id, tool.name, tool.argv[0])
    assert data.mmaps.argv[0].startswith("/"), (entry.id, data.mmaps.argv[0])
    assert data.conf.source_dir.startswith("/"), (entry.id, data.conf.source_dir)


# -- the ready markers, which the SPINE fills ---------------------------------


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_every_ready_marker_fills_from_the_two_tokens_the_spine_gives_it(
    entry: CatalogEntry,
) -> None:
    """`_ready_spec()` fills `REALM_HOST` and `WORLD_PORT`, nothing else, then compiles.

    The plan for this task filled the markers from the FAMILY's mapping, which is
    a much wider set than the one they meet: `_ready_spec()` builds its own
    two-key mapping, so a marker naming `{{CORE_DIR}}` would pass a family-mapping
    check and then break the last stage of an otherwise finished install. Asked of
    the real engine, so the answer comes from the mapping the install spends.

    `given` is hand-typed, which `secret_tokens()` argues against for the family
    mapping — the opposite call here, and on purpose. The property under test IS
    the width of `_ready_spec()`'s mapping, so filling the marker from that same
    mapping and matching it against that same mapping's output would be a
    tautology. The fill side is written out so the marker is filled from
    OUTSIDE the engine and only the pattern side is asked of it; a key added to
    `_ready_spec()` therefore turns this case red, which is the right way round.
    """
    block = native_of(entry)
    spec = engine_for(entry)._ready_spec(block.ready)
    given = {"REALM_HOST": native.INSTALL_REALM_HOST, "WORLD_PORT": str(entry.ports.world)}
    markers = (
        ("world", block.ready.world, spec.world),
        ("auth", block.ready.auth, spec.auth),
        ("fatal", block.ready.fatal, spec.fatal),
    )
    for name, marker, pattern in markers:
        assert (marker is None) == (pattern is None), (entry.id, name)
        if marker is None or pattern is None:
            continue
        assert "{{" not in pattern, (entry.id, name, pattern)
        if not block.ready.regex:
            filled = composegen.fill(marker, given)
            assert re.search(pattern, filled), (entry.id, name, pattern, filled)


def test_a_ready_marker_naming_any_other_token_is_refused_before_the_install_starts() -> None:
    """The control for the case above: the two-key mapping really is the limit.

    `{{CORE_DIR}}` is a token the CMaNGOS family fills everywhere else, which is
    what makes it the right probe — the refusal has to come from the marker
    mapping being narrower, and not from the token being unknown to the app.

    Pinned to `_ready_spec()`'s unfilled-placeholder branch, with the neighbouring
    one asserted absent: a marker that fills but does not compile raises "is not a
    usable pattern" a few lines further down, and a test matching only "ready
    marker" would have gone green on either.
    """
    entry = CMANGOS_ENTRIES[0]
    broken = native_of(entry).ready.model_copy(update={"world": "{{CORE_DIR}} is up"})
    with pytest.raises(InstallerError) as raised:
        engine_for(entry)._ready_spec(broken)
    message = str(raised.value)
    assert "ready markers are broken" in message, message
    assert "CORE_DIR" in message, message
    assert "is not a usable pattern" not in message, message


# -- what a family module may not contain -------------------------------------


def literal_strings(source: str) -> list[tuple[int, str]]:
    """Every string constant that RUNS — docstrings dropped, attribute docstrings included.

    A docstring is prose about a game and is meant to be able to name one; a
    string constant is data. Comments never reach the AST at all.
    """
    tree = ast.parse(source)
    docs: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list):
            for statement in body:
                if (
                    isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant)
                    and isinstance(statement.value.value, str)
                ):
                    docs.add(id(statement.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docs
    ]


def per_game_values(entry: CatalogEntry) -> set[str]:
    """Every value of this entry that names its GAME, plus each path segment of one.

    Read off the entry rather than typed out. That is NOT the same as a new game
    widening the check: `game_words()` then deletes every value this entry
    shares with another, so a sibling entry mostly subtracts. What that costs is
    measured there. Segments are split on `/` only: `-` and `_` are how these
    names are BUILT (`tbc-db`, `tw_world`), and splitting on them would offer
    `db` and `world` as game words.
    """
    block = native_of(entry)
    values = {
        entry.id,
        entry.id.rsplit("-", 1)[-1],
        entry.name,
        entry.emulator.name,
        block.image_prefix,
        str(entry.client.build),
        entry.databases.auth,
        entry.databases.characters,
        entry.databases.world,
        entry.containers.db,
        entry.containers.auth,
        entry.containers.world,
        entry.containers.db_import,
        entry.containers.client_data,
        composegen._container_prefix(entry),
        block.ready.world,
        block.ready.auth,
        block.ready.fatal,
    }
    values.update(block.images)
    values.update(entry.databases.extra)
    values.update(source.dest for source in entry.emulator.sources)
    data = block.cmangos
    if data is not None:
        values.add(data.conf.source_dir)
        values.update(data.conf.files)
        values.add(data.mmaps.argv[0])
        for tool in data.extract.tools:
            values.update((tool.name, tool.argv[0]))
        for phase in data.sql.phases:
            values.update(phase.files)
            values.update((phase.into_each or {}).values())
    words: set[str] = set()
    for value in values:
        if isinstance(value, str):
            words.add(value)
            words.update(value.split("/"))
    return {word for word in words if len(word) >= 3}


def game_words() -> dict[str, set[str]]:
    """Per entry, the values naming THAT game and no other, minus the engines' own words.

    Two filters. A value more than one entry carries is a word of the lineage or
    of English — `server`, `characters`, `mangos` — and the first two appear in
    dozens of user-facing sentences in these modules. A stage name is Python's
    vocabulary: `client-data` is AzerothCore's fourth stage and also its third
    image, and the stage tuple is where that word belongs.

    **What the filters cost, MEASURED against the tree on 2026-09-02** (four
    entries, 126 words: wow-tbc 36, wow-vanilla 34, wow-tortoise 32, wow-wotlk
    24). None of the four below was reasoned about; each was run.

    * The set is not a superset of what an engine would plausibly hardcode.
      `Avg Diff:` is `install.native.ready.world` for BOTH wow-tbc and
      wow-vanilla, so the uniqueness filter deletes it — and a ready marker is
      the single most likely thing a family engine would hardcode.
    * Nothing about the CMaNGOS in-image layout is protected. `/opt/mangos/etc`,
      `mangosd.conf`, `Avg Diff:`, `/opt/mangos/bin/tools/MoveMapGen` and
      `vmap_extractor` were each put through the check as a literal in a family
      module and all five passed. wow-tbc and wow-vanilla are byte-identical in
      `conf.source_dir`, `conf.files`, `mmaps.argv` and two of three
      `extract.tools`, which is why.
    * It DECAYS as the catalog grows, which is the growth one engine per lineage
      exists for. Cloning `wow-wotlk` as a sibling `wow-wotlk-hd` took wow-wotlk
      from 24 words to 2 (`wotlk`, `wow-wotlk`) and gave the clone 1, and
      `test_the_catalog_words_catch_something` still passed — that control asks
      only whether each game contributes at least one word, and an entry's own
      id always survives.
    * It has false positives, and they are ordinary English. `dbc` (wow-tbc's)
      and `worldserver`, `authserver`, `12340` (wow-wotlk's) turn a family
      module red wherever they appear, in a sentence as readily as in data:
      "extract the dbc files before the maps" and "the worldserver container
      never came back" are both red. `native.py` already yields *"Waiting for
      the world server to finish loading"*, which is one respelling away.

    So a red from `test_family_modules_contain_no_game_literal` is read before
    it is believed, and the answer may be to reword prose rather than to move
    data. Redesigning the filter is a follow-up; this is the honest account of
    what it does today.
    """
    per_entry = {entry.id: per_game_values(entry) for entry in ENTRIES}
    seen: dict[str, int] = {}
    for values in per_entry.values():
        for value in values:
            seen[value] = seen.get(value, 0) + 1
    stage_words: set[str] = set()
    for cls in FAMILIES.values():
        stage_words.update(cls.STAGE_NAMES)
    return {
        game: {value for value in values if seen[value] == 1 and value not in stage_words}
        for game, values in per_entry.items()
    }


def game_literals_in(source: str) -> list[tuple[int, str, str]]:
    """`(line, game, value)` for every catalog value of one game spelled in this source."""
    found: list[tuple[int, str, str]] = []
    for game, words in game_words().items():
        for lineno, literal in literal_strings(source):
            for word in sorted(words):
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(word)}(?![A-Za-z0-9_])", literal):
                    found.append((lineno, game, word))
    return found


FAMILY_MODULES = sorted(FAMILIES_DIR.glob("*.py"))
assert FAMILY_MODULES, f"no family modules under {FAMILIES_DIR}"


@pytest.mark.parametrize("module", FAMILY_MODULES, ids=lambda path: path.stem)
def test_family_modules_contain_no_game_literal(module: Path) -> None:
    """A family names no game: a literal here is data that escaped `catalog.json`.

    `cmangos.py`'s module docstring names this test as the thing that will hold
    that claim; until this task the claim was held by a reading done by hand.

    The words are DERIVED from the catalog rather than listed, which is not the
    same as a new game widening the check: a new entry contributes its id and
    SUBTRACTS everything it shares with an existing one. What that costs, and
    the false positives it carries, are measured in `game_words()` and are worth
    reading before a red here is acted on.
    `test_the_catalog_words_catch_something` is the control that the derivation
    is not empty — and only that. It does not show the set is adequate; the
    measurements say it is not.
    """
    found = game_literals_in(module.read_text(encoding="utf-8"))
    assert not found, f"{module.name} spells catalog values: {found}"


def test_the_catalog_words_catch_something() -> None:
    """The control for the case above: green there must mean "clean", not "found nothing".

    A filter that emptied the word set — a catalog rename that made every value
    shared, a `STAGE_NAMES` that swallowed them — would leave every module green
    with nothing to report. That is the failure mode a derived check has and a
    hand-written list does not, so the derivation is exercised on a source this
    test writes.
    """
    words = game_words()
    assert set(words) == set(IDS)
    for game, found in words.items():
        assert found, f"{game} contributes no word, so nothing about it can be caught"
    probe = ENTRIES[0].id
    hits = game_literals_in(f"x = {probe!r}\ny = 'unrelated'\n")
    assert (ENTRIES[0].id, probe) in [(game, word) for _, game, word in hits], hits
    assert all(game == ENTRIES[0].id for _, game, _ in hits), hits
    assert not game_literals_in("x = 'unrelated'\n")
    prose = f'"""A docstring naming {probe}."""\nx = 1\n'
    assert not game_literals_in(prose), "prose about a game is not data that escaped it"


# -- what the catalog package may not import ----------------------------------


def imported_modules(module: Path, source: str) -> set[str]:
    """Every module `source` imports, resolved to an ABSOLUTE dotted name.

    Four spellings reach `yulon/controller.py` from inside `catalog/`, and until
    2026-09-02 this read two. `from yulon import controller` puts `"yulon"` in
    `node.module` and the module's own name in `node.names`, which was read for
    `ast.Import` and never for `ast.ImportFrom`; and every relative import
    (`from ..controller import X`, `level > 0`) carries a module name that
    starts with no package at all. Both were added to `composegen.py` and the
    whole suite stayed green. `yulon/controller.py` is a MODULE and not a
    package, so `from yulon import controller` is the spelling a person would
    write — the two that were caught are the two nobody uses.

    `node.names` is folded in for `ImportFrom` unconditionally, because
    `from yulon.catalog import composegen` and `from yulon import controller`
    are the same node shape and only the resolved name tells them apart. A
    class name imported the same way (`from x import Thing`) resolves to a
    dotted string that is no module, which costs nothing here.
    """
    package = ".".join(module.relative_to(IMPORT_ROOT).with_suffix("").parts[:-1])
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = ""
            if node.level:
                parts = package.split(".")
                base = ".".join(parts[: len(parts) - node.level + 1])
            head = ".".join(part for part in (base, node.module or "") if part)
            found.add(head)
            found.update(f"{head}.{alias.name}" if head else alias.name for alias in node.names)
    return {name for name in found if name}


def reaches_the_controller(name: str) -> bool:
    """`yulon.controller` itself or a dotted child of it — never a name that merely starts so.

    The dot is the point. `startswith("yulon.controller")`, which is what this
    rule used to spell, would also ban a future `yulon.controllers` that has
    nothing to do with the per-game probes.
    """
    return name == CONTROLLER_MODULE or name.startswith(f"{CONTROLLER_MODULE}.")


def test_the_catalog_package_imports_no_controller() -> None:
    """The per-game probes are injected by the caller; `catalog/` never reaches into one."""
    modules = sorted(CATALOG_PACKAGE.rglob("*.py"))
    assert modules, f"no modules under {CATALOG_PACKAGE}"
    seen = 0
    for module in modules:
        for name in imported_modules(module, module.read_text(encoding="utf-8")):
            assert not reaches_the_controller(name), f"{module}: imports {name}"
            seen += 1
    assert seen, f"nothing under {CATALOG_PACKAGE} imports anything, so nothing was read"


def test_the_import_reader_answers_every_spelling_of_the_controller() -> None:
    """The control for the case above: a ban is only as wide as the reader under it.

    Each line reaches `yulon/controller.py` from a module in `catalog/`, and the
    two marked below are the two that got past the ban while the whole suite
    stayed green. The last line is the neighbour on the other side — a rule that
    over-bans is also a broken rule.
    """
    module = CATALOG_PACKAGE / "composegen.py"
    reaches = (
        "import yulon.controller",
        "from yulon.controller import Controller",
        "from yulon import controller",  # green until 2026-09-02
        "from ..controller import Controller",  # green until 2026-09-02
        "from .. import controller",
    )
    for line in reaches:
        found = imported_modules(module, line)
        assert any(reaches_the_controller(name) for name in found), (line, sorted(found))
    for line in ("from yulon import platform", "from yulon.catalog import composegen"):
        found = imported_modules(module, line)
        assert not any(reaches_the_controller(name) for name in found), (line, sorted(found))
    assert not reaches_the_controller("yulon.controllers")
