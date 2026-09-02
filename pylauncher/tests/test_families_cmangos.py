"""Tests for the CMaNGOS family engine (`yulon.catalog.families.cmangos`, phase 7.3).

Same shape as `test_families_azerothcore.py`: the `Recorder` machine double from
`tests/support_native.py`, the real `wow-tbc` catalog entry, the real shared
templates. Nothing here proves a CMaNGOS server installs — that is gate 7.4 —
it proves the family's control flow, its refusals, what a resume repeats, and
that every `docker run` it asks for is shaped the way the design says (client
`:ro`, `--user` on Linux, cwd `/out`), asserted by FIELD on `ContainerRun`.

The import gate here is a `CallableGate` over `Recorder.probe`/`Recorder.reset`:
`MarkerGate`'s five branches belong to `test_sqlplan.py`, and this file proves
the family's reaction to each answer. K.7 bound the `import` stage and added
`CmangosInstaller._gate`; `engine()` attaches the pair as `_test_gate`, and the
autouse `gated` fixture patches `_gate` to hand it back, with `raising=True`.
What that keyword buys — and what stood in for it while no `_gate` existed — is
recorded in that fixture's own docstring rather than up here, because this
paragraph went on saying the seam "is not wired yet and cannot be" after K.7 had
wired it, and cited a test that has never existed. A module docstring is the
first thing read and the last thing re-checked.
"""

from __future__ import annotations

import ast
import gzip
import inspect
import os
import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, fields, is_dataclass, replace
from pathlib import Path, PurePosixPath
from typing import BinaryIO

import pytest

import yulon
from tests.support_native import ABSENT, IMPORTED, PARTIAL, POPULATED_HALF, Recorder
from yulon import docker, platform, resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, PasswordPlan, SqlPhase, SqlPlan, load_catalog
from yulon.catalog.families import cmangos, dockerfile, extract, family_for, sqlplan
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions, installer_for

DB_PASSWORD = "tbc-0123456789abcdef"


def installable(entry: CatalogEntry) -> CatalogEntry:
    """The entry with `platforms: ["linux"]`, because the family is what is under test.

    The 7.3 plan said `wow-tbc` carries `platforms: []` between 7.2 and gate
    7.4c and that this copy is what makes it installable. That is NOT true of
    the catalog on this branch: 7.2 has not landed, and all three CMaNGOS
    entries still ship `platforms: ["linux"]` and their bash `script`
    (`yulon/catalog/catalog.json`, verified 2026-09-01). So the copy is a no-op
    today and is kept only so that this file keeps testing the family rather
    than the dispatch refusal on whichever side of that gate the catalog is —
    `test_installer.py` is where the refusal is pinned. Whether the entries go
    back to `platforms: []` is undecided here; nothing in 7.3's landed code
    depends on it.
    """
    return entry.model_copy(
        update={"install": entry.install.model_copy(update={"platforms": ("linux",)})}
    )


ENTRY = installable(load_catalog().get("wow-tbc"))
CMANGOS = ENTRY.install.native.cmangos if ENTRY.install.native is not None else None
assert CMANGOS is not None, "wow-tbc must carry install.native.cmangos (7.3 catalog)"
SQL = CMANGOS.sql
DB_FACTS = ENTRY.install.native.db if ENTRY.install.native is not None else None
assert DB_FACTS is not None, "wow-tbc must carry install.native.db (the client, user and charset)"


def client_folder(tmp_path: Path) -> Path:
    """A client tree passing the TBC ClientSpec: Data/, the required file, six MPQs, a locale."""
    client = tmp_path / "client"
    (client / "Data" / "enUS").mkdir(parents=True)
    for name in ("common", "expansion", "patch", "patch-2", "patch-3", "misc"):
        (client / "Data" / f"{name}.MPQ").write_bytes(b"MPQ\x1a")
    (client / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"MPQ\x1a")
    return client


def context(
    server_dir: Path,
    client_dir: Path | None = None,
    *,
    completed: Iterable[str] = (),
    cancel: threading.Event | None = None,
) -> native.StageContext:
    """A `StageContext` for calling one stage body directly.

    `cancel` defaults to None — the spine hands a stage body an Event only
    while a run is cancellable, and None is what a body sees in every test that
    is not about being stopped.
    """
    return native.StageContext(
        server_dir=server_dir,
        client_dir=client_dir,
        state=native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="cmangos",
            completed=tuple(completed),
        ),
        cancel=cancel,
        secrets=native.Secrets(db_password=DB_PASSWORD),
    )


def lay_sql(server_dir: Path, plan: SqlPlan) -> Callable[[Path], None]:
    """An `on_clone` hook laying the files the real plan names under the source just cloned.

    Two files per glob so natural order is observable; gzip where the phase
    says so. A pattern under a NESTED source dest
    (`src/mangos-tbc/src/modules/Bots/...`) is laid only by that nested clone,
    or the spine's own guard would refuse the nested checkout as "has files".
    """
    dests = [source.dest for source in ENTRY.emulator.sources]

    def owner(pattern: str) -> str | None:
        return max((d for d in dests if pattern.startswith(d + "/")), key=len, default=None)

    def on_clone(dest: Path) -> None:
        rel = dest.relative_to(server_dir).as_posix()
        for phase in plan.phases:
            patterns = list(phase.files) + list((phase.into_each or {}).values())
            for pattern in patterns:
                if owner(pattern) != rel:
                    continue
                for n in (1, 2):
                    name = pattern.replace("*", f"{n:04d}")
                    path = server_dir / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    body = f"-- {name}\nSELECT {n};\n"
                    if phase.gzip:
                        path.write_bytes(gzip.compress(body.encode("utf-8")))
                    else:
                        path.write_text(body, encoding="utf-8")

    return on_clone


def engine(rec: Recorder, **overrides: object) -> CmangosInstaller:
    """An engine over `rec`, carrying the Recorder's probe/reset pair as its test gate."""
    eng = CmangosInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        seams=rec.seams(**{"platform_id": lambda: "linux", **overrides}),
    )
    eng._test_gate = native.CallableGate(rec.probe, rec.reset)  # type: ignore[attr-defined]
    return eng


def install(rec: Recorder, server_dir: Path, client_dir: Path, **overrides: object) -> list[str]:
    rec.on_clone = lay_sql(server_dir, SQL)
    return list(
        engine(rec, **overrides).run(InstallOptions(server_dir=server_dir, client_dir=client_dir))
    )


REAL_GATE = CmangosInstaller._gate
"""The unpatched `_gate`, bound at import — the only way to reach it in this file.

`gated` is autouse, so inside a test body `CmangosInstaller._gate` is the
double. Every test here wants that; exactly one wants the real thing, and it
has to have been taken before the first fixture ran.
"""


@pytest.fixture(autouse=True)
def gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every engine's import gate is the one `engine()` attached, not a real `MarkerGate`.

    `raising=True` since K.7 bound the import stage, and the keyword is what
    makes this patch mean anything: renaming `CmangosInstaller._gate` now
    errors here, where before it would have gone on quietly adding an unused
    attribute while every end-to-end test in this file drove the real gate at a
    machine that is not there. `GATE_METHOD_AT_IMPORT` and the test that read
    it were the stand-in for this keyword while no `_gate` existed; they went
    away with K.7.

    The real `_gate()` — the `MarkerGate` over the plan's marker table — is
    exercised in `test_sqlplan.py`, which points it at doubles with no engine
    in the way.
    """

    def gate(self: CmangosInstaller, ctx: native.StageContext) -> native.ImportGate:
        attached = getattr(self, "_test_gate", None)
        assert attached is not None, "build engines with engine(rec) in this file"
        return attached

    monkeypatch.setattr(CmangosInstaller, "_gate", gate, raising=True)


# -- identity ---------------------------------------------------------------


def test_family_and_stage_names_are_the_contract_tuple() -> None:
    assert CmangosInstaller.family == "cmangos"
    assert CmangosInstaller.STAGE_NAMES == (
        "clone-sources",
        "db-password",
        "write-dockerfile",
        "generate-compose",
        "build",
        "extract",
        "mmaps",
        "conf",
        "start-db",
        "import",
        "up",
        "ready",
    )


def test_stages_are_unique_and_a_subset_of_the_pinned_names_in_order() -> None:
    names = [stage.name for stage in engine(Recorder()).stages()]
    assert len(names) == len(set(names))
    assert "preflight" not in names and "guard" not in names
    pinned = [n for n in CmangosInstaller.STAGE_NAMES if n in names]
    assert names == pinned


def test_stage_names_is_the_pinned_tuple_now_that_every_stage_is_bound() -> None:
    """The equality K.2 deferred, K.7 could only check by hand, and K.8 owes.

    `STAGE_NAMES` is the class's DECLARATION of its order. `stage_names()` is
    what the spine actually validates a resume against, and it is derived from
    `stages()`. The two were allowed to disagree while the tuple was being
    built, because nothing in the app read `STAGE_NAMES` and this class was not
    in `FAMILIES`; K.8 changed the second half, so the agreement is held here
    instead of in a module docstring. The test above is the weaker
    subset-in-order form that let the gap exist — it still earns its place,
    because uniqueness and the reserved names are not this assertion's business.

    Read off the ENGINE and never off a state file. `read_state()` drops stage
    names the running binary does not know, silently (bug checklist §23), so a
    `completed` tuple can be made to agree with almost anything and would
    satisfy an equality written against it while the two declarations were in
    fact apart — which is precisely the mismatch this exists to catch.
    """
    assert engine(Recorder()).stage_names() == CmangosInstaller.STAGE_NAMES


# -- the registered family ----------------------------------------------------


def cmangos_entries() -> list[CatalogEntry]:
    """Every shipped entry whose `install.native` names this family.

    Derived from `catalog.json`, not listed, so a fourth CMaNGOS game is
    dispatch-tested by adding the entry and nothing else. WHICH games declare
    the family is `test_catalog.py`'s question
    (`test_the_cmangos_entries_carry_a_full_family_block`, parametrized over the
    three ids); what is asserted here is that every entry which declares it
    gets an engine. The emptiness guard lives here so that no caller can pass
    over an empty list and call it a result.
    """
    entries = [
        entry
        for entry in load_catalog().games
        if entry.install.native is not None and entry.install.native.family == "cmangos"
    ]
    assert entries, "no shipped catalog entry names the cmangos family"
    return entries


def test_every_cmangos_entry_dispatches_to_an_engine_that_runs_this_familys_stages() -> None:
    """The registration proved where it matters: on the entries, through the dispatcher.

    `"cmangos" in FAMILIES` is a declaration, and three guards in this project
    have passed while the bug they named was live. What the tasks downstream of
    K.8 need is that pressing Install on one of these games yields an engine of
    THIS class which knows THIS family's stages — so the registry lookup
    (`family_for`), the dispatcher that a button reaches (`installer_for`) and
    the object that comes back are crossed here, once for each shipped entry
    rather than once for `wow-tbc` with the other two assumed.

    `eng.stage_names()` and not `CmangosInstaller.STAGE_NAMES`: the second is a
    class attribute every instance shares, so reading it back off the result
    would say nothing about the object dispatch actually returned. The first is
    derived from that instance's own `stages()`, which the constructor has
    already run through `_check_stage_tuple()` — so a family whose stage tuple
    is broken for a game other than TBC is refused here, and not two hours into
    that game's build.
    """
    for entry in cmangos_entries():
        assert family_for(entry) is CmangosInstaller, entry.id
        eng = installer_for(entry, platform_id=lambda: "linux")
        assert isinstance(eng, CmangosInstaller), entry.id
        assert eng.stage_names() == CmangosInstaller.STAGE_NAMES, entry.id


def test_the_install_button_is_offered_for_every_cmangos_entry_on_linux() -> None:
    """`Install.supports()` is the gate the tile asks before the engine is ever built.

    An engine nothing can reach is not a shipped feature. `catalog_view.py` asks
    this one call twice — once to grey the tile and say which platform the
    installer needs, once in `start_install()` to refuse before the folder
    prompts — so a False here is an install that cannot be started however well
    the family dispatches. Measured 2026-09-02: all three CMaNGOS entries answer
    True for `linux`, which is the platform their `platforms` list carries and
    the only one this family's containers are built for.

    One-sided on purpose, and the mutation run says what that costs. A `supports()`
    rewritten to `return True` SURVIVES this test — an always-open gate does not
    break the claim made here. Measured 2026-09-02 at `f6ed1b9a`, whole suite:
    that mutation is killed SEVEN times over, in four files — three
    parametrisations of `test_the_cmangos_entries_carry_a_full_family_block`
    (`test_catalog.py`); `test_catalog_view.py`'s
    `test_unsupported_platform_is_said_on_the_tile_and_refused_before_any_prompt`
    and `test_unlocking_after_a_job_never_re_enables_a_gated_tile`;
    `test_installer_for_does_not_consult_the_platform_but_does_pass_it_on`
    (`test_installer.py`); and exactly ONE in `test_families_azerothcore.py`,
    `test_the_unsupported_platform_refusal_still_comes_first`. This paragraph
    credited `test_installer_refuses_a_platform_its_script_cannot_run` — deleted
    with the bash path in 7.2 — and "two in azerothcore". Folding
    the closure in here would put two rules in one fixture for coverage that
    already exists seven times over. What this test is not vacuous about was
    measured the same way: moving `wow-tortoise` to `platforms: ["macos"]` failed
    it by name, so it really does read each shipped entry rather than TBC alone.
    """
    for entry in cmangos_entries():
        assert entry.install.supports("linux") is True, entry.id


def test_the_module_constants_are_the_shared_template_s_own_spellings() -> None:
    """`data`, `etc` and `db-data` are read off `shared/cmangos/base.yml.tmpl`, not remembered."""
    from yulon.catalog.families import cmangos

    native_block = ENTRY.install.native
    assert native_block is not None
    base = (resources.installers_dir() / native_block.templates / "base.yml.tmpl").read_text(
        encoding="utf-8"
    )
    assert f"- ./{cmangos.DATA_DIR}:" in base
    assert f"- ./{cmangos.ETC_DIR}:" in base
    assert f"- {cmangos.DB_DATA_VOLUME}:/var/lib/mysql" in base


def test_both_token_mappings_carry_the_family_set_from_catalog_data(tmp_path: Path) -> None:
    """Every catalog- and install-derived key is in BOTH mappings; only one carries the secret.

    Asserted over both, not over the public one with a note about the other:
    the split's cost would be a conf table quietly losing `WORLD_PORT` because
    someone added a key to whichever mapping they had open.
    """
    server_dir = tmp_path / "srv"
    eng = engine(Recorder())
    public = eng._public_tokens(server_dir)
    secret = eng._secret_tokens(context(server_dir))
    native_block = ENTRY.install.native
    assert native_block is not None and CMANGOS is not None
    for tokens in (public, secret):
        assert tokens["DB_HOST"] == ENTRY.containers.db
        assert tokens["DB_USER"] == native_block.db.user
        assert tokens["DB_IMAGE"] == native_block.db.image
        assert tokens["AUTH_DB"] == ENTRY.databases.auth
        assert tokens["WORLD_DB"] == ENTRY.databases.world
        assert tokens["CHAR_DB"] == ENTRY.databases.characters
        assert tokens["LOGS_DB"] == ENTRY.databases.extra[0]
        assert tokens["CORE_DIR"] == str(PurePosixPath(CMANGOS.conf.source_dir).parent)
        assert tokens["CORE_DIR"] == "/opt/mangos", "the in-image prefix, never a host path"
        assert tokens["CLIENT_BUILD"] == str(ENTRY.client.build)
        assert tokens["MAKE_JOBS"] == str(CMANGOS.dockerfile.make_jobs)
        assert tokens["DB_PORT"] == str(ENTRY.ports.db)
        assert tokens["AUTH_PORT"] == str(ENTRY.ports.auth)
        assert tokens["WORLD_PORT"] == str(ENTRY.ports.world)
        assert tokens["REALM_HOST"] == native.INSTALL_REALM_HOST == "127.0.0.1"
        assert tokens["PROJECT_NAME"] == composegen.project_name(
            ENTRY.id, server_dir, platform_id=lambda: "linux"
        )
        assert tokens["IMAGE_TAG"] == composegen.image_tag(server_dir, platform_id=lambda: "linux")
        assert tokens["IMAGE_PREFIX"] == native_block.image_prefix
        assert "" not in tokens.values(), "an absent value is an absent key, never an empty fill"
    assert "DB_PASSWORD" not in public
    assert secret["DB_PASSWORD"] == DB_PASSWORD
    assert secret == {**public, "DB_PASSWORD": DB_PASSWORD}, "one set is the other plus the secrets"


def test_tokens_omit_logs_db_when_the_entry_has_no_extra_schema(tmp_path: Path) -> None:
    bare = ENTRY.model_copy(update={"databases": ENTRY.databases.model_copy(update={"extra": ()})})
    eng = CmangosInstaller(
        bare,
        installers_root=resources.installers_dir(),
        seams=Recorder().seams(platform_id=lambda: "linux"),
    )
    assert "LOGS_DB" not in eng._public_tokens(tmp_path / "srv")
    assert "LOGS_DB" not in eng._secret_tokens(context(tmp_path / "srv"))


def secrets_with_a_sentinel_per_field() -> tuple[native.Secrets, dict[str, str]]:
    """A `Secrets` whose every field holds a distinct, unmistakable string, and that mapping.

    Built from `dataclasses.fields()` rather than from `db_password=...`, so a
    secret added to `native.Secrets` is covered by every caller of this helper
    on the day it is added and not on the day somebody remembers. That is the
    difference the M15 mutation turned on: the protection K.4 shipped covered
    the NAME `DB_PASSWORD`, and a second secret walked past it.
    """
    named = fields(native.Secrets)
    assert named, "native.Secrets has no fields; every test built on this would pass vacuously"
    for field in named:
        assert field.type in ("str", str), (
            f"native.Secrets.{field.name} is {field.type!r}, not a string. The sentinel "
            "trick below assumes strings; re-read these tests before widening the type."
        )
    values = {field.name: f"SENTINEL-{field.name}-a4f19c7e" for field in named}
    return native.Secrets(**values), values


def test_the_build_context_mapping_needs_no_secret_and_still_fills_the_shipped_templates(
    tmp_path: Path,
) -> None:
    """`_public_tokens()` is complete on its own — no `Secrets` anywhere in the call.

    This is the by-construction half, and it is a behaviour and not a
    signature claim: the mapping the build context is rendered from is
    produced from a `server_dir` alone, and the SHIPPED Dockerfile pair fills
    from it with nothing left over. `composegen.fill()` refuses an unfilled
    `{{TOKEN}}`, so "the build context never needs a secret" is what a green
    render here means — not "we remembered to leave one out".
    """
    server_dir = tmp_path / "srv"
    public = engine(Recorder())._public_tokens(server_dir)
    native_block = ENTRY.install.native
    assert native_block is not None and native_block.dockerfile_dir is not None
    template_dir = resources.installers_dir() / native_block.dockerfile_dir
    text, ignore = dockerfile.render(template_dir, public)
    assert "{{" not in text and "{{" not in ignore
    assert text.startswith(composegen.GENERATED_MARKER)


def test_neither_the_context_secrets_nor_the_password_on_disk_reaches_the_rendered_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """M15 and M-R2, killed at the THREE roads listed below — and covering no others.

    Mutation M15 (2026-09-01) added a second secret key to the single
    `_tokens()` mapping — `"ROOT_PASSWORD": ctx.secrets.db_password` — and a
    planted template spelling `{{ROOT_PASSWORD}}` rendered
    `ENV ROOT_PASSWORD=tbc-0123456789abcdef` into the Dockerfile while all
    1872 tests passed — the whole suite as it stood that day, recorded at
    `9e198c05`; it is 1974 at `f6ed1b9a`, so read the number as a date and not
    as a size. `dockerfile.render()` drops one KEY BY NAME, so the
    second name walked past it, and no test in the suite looked at the mapping
    as a whole.

    So this one does, and it names no token: it puts a distinct sentinel in
    every field of `Secrets`, spies on the mapping the stage actually hands
    `render()`, and asserts no sentinel is anywhere in it — as a value or
    inside one.

    **The roads, and the mutation that put each one here.** `_public_tokens()`
    is handed a `server_dir`, so `ctx.secrets` is not the only place a password
    can be reached from: whatever the earlier stages have already written into
    that directory is in reach too. The sentinel therefore goes into each of
    these before the stage runs.

    * `ctx.secrets` itself — M15, above.
    * The file `install.password` names, which K.3's `db-password` stage writes
      two stages before the build. `resolve_secrets(server_dir)` is a public
      inherited method taking exactly the argument this body already holds, and
      by that point it READS that file rather than minting. A mutation taking
      that route walked past the `ctx.secrets`-only version of this test with
      the whole suite green (measured 2026-09-02).
    * `<server_dir>/.env` — mutation M-R2, measured 2026-09-02. K.4's
      `generate-compose` stage merges `DB_ROOT_PASSWORD=<the plaintext
      password>` into that file, and it runs at `STAGE_NAMES` index 3, ONE
      stage before `build` at index 4. A seven-line helper reading it, with no
      public method and no cache anywhere, put that value under
      `"ROOT_PASSWORD"`, rendered `ENV ROOT_PASSWORD=tbc-0123456789abcdef` into
      a Dockerfile, and left the suite at 1889 passed, 3 skipped (2026-09-02,
      recorded at `e176af17`) with mypy, ruff and black clean. The `.env` below
      is written by RUNNING the real
      `generate-compose` stage rather than by spelling its key out here, so a
      renamed key or file follows it; the assertion that the sentinel reached
      the file is what keeps that from going quietly vacuous.

    **What it still does not catch, said out loud so the name is not read as
    more than it is.** The set covered is one context and two files, not "the
    server dir". A mutation reading some OTHER file under `server_dir` that a
    stage writes a password into is uncovered. So is one that resolves the
    password ONCE and caches it — on the class, in a module global, anywhere
    outside this call — because a cached value can have come from an earlier
    `server_dir` that no sentinel here ever occupied; that variant was measured
    surviving on 2026-09-02. `_public_tokens()`'s docstring records why no test
    in this shape can close the set, and the guarantee against a NAMED secret
    remains `dockerfile.SECRET_TOKENS`' key-drop and refusal, which run
    whatever the mapping holds.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    secrets, sentinels = secrets_with_a_sentinel_per_field()
    eng = engine(Recorder())
    ctx = replace(context(server_dir), secrets=secrets)
    # The second road: what K.3's `db-password` stage leaves in the build
    # context, and therefore what `resolve_secrets(server_dir)` reads back.
    plan = ENTRY.install.password
    assert plan.file is not None, "wow-tbc's password plan names no file; the disk road is untested"
    assert "db_password" in sentinels, (
        "the password FILE holds `Secrets.db_password` specifically; that field is gone or "
        "renamed, so re-read which sentinel belongs on disk before trusting this test"
    )
    (server_dir / plan.file).write_text(sentinels["db_password"] + "\n", encoding="utf-8")
    # The third road: what K.4's `generate-compose` leaves in `.env` one stage
    # before the build. Run, not spelled out, so this follows a renamed key.
    list(eng.stage_generate_compose(ctx))
    dotenv = (server_dir / composegen.DOTENV_FILE).read_text(encoding="utf-8")
    assert sentinels["db_password"] in dotenv, (
        "`generate-compose` no longer writes the install password into `.env`, so the M-R2 "
        f"road is untested and a mutation reading it would survive again:\n{dotenv}"
    )
    seen: list[dict[str, str]] = []
    real = dockerfile.render

    def spy(template_dir: Path, tokens: Mapping[str, str]) -> tuple[str, str]:
        seen.append(dict(tokens))
        return real(template_dir, tokens)

    monkeypatch.setattr(dockerfile, "render", spy)
    list(eng._write_dockerfile(ctx))
    assert len(seen) == 1, "the renderer was not called once; this test would prove nothing"
    for name, value in sentinels.items():
        leaked = [key for key, held in seen[0].items() if value in held]
        assert not leaked, f"{leaked} in the build-context mapping carries Secrets.{name}"
    for name in ("Dockerfile", ".dockerignore"):
        built = (server_dir / name).read_text(encoding="utf-8")
        assert not [value for value in sentinels.values() if value in built], name


def test_the_secret_mapping_carries_a_second_secret_nobody_listed(tmp_path: Path) -> None:
    """A secret added to the secret TYPE arrives in `_secret_tokens()` with no list edited.

    The other half of M15's lesson. Covering the name `DB_PASSWORD` is what
    failed; the fix derives the secret token names from the fields of the
    `Secrets` instance, so the derivation has to be shown to answer for a
    field that did not exist when it was written. A subclass is how a second
    secret is added here without editing `yulon/catalog/native.py`, and
    `secret_token_map()` asks `fields()` of the INSTANCE for exactly that
    reason.

    **The line this test used to end on was not a guard.** It read
    `assert "API_TOKEN" not in eng._public_tokens(server_dir)`, and `API_TOKEN`
    exists only on the local subclass below — which `_public_tokens()` is never
    handed, since it takes a `server_dir` and no context. No edit to that
    method's body could fail it short of literally spelling `API_TOKEN`; undoing
    the split makes it raise `AttributeError`, which is a claim about a
    DECLARATION and not about a value. It is replaced by the enumeration below,
    which is this test's actual subject: the keys `_secret_tokens()` adds on top
    of the public half are exactly the ones the derivation predicts from the
    fields of the secret handed in — no fewer (a dropped field), no more (a
    listed name), and spelled the one way.

    Measured 2026-09-02, and corrected the same day. The first version of this
    note said an extra undeclared key was caught here but that
    `test_both_token_mappings_carry_the_family_set_from_catalog_data` caught it
    too, and that "no mutation was found that this line alone kills". A review
    found one, and it is exactly the shape this test exists for: a key added
    only when the secret carries more fields than `native.Secrets` declares —

        if len(fields(ctx.secrets)) > 1:
            secret = {**secret, "UNDECLARED_EXTRA": "x"}

    — is invisible to every test that builds a real `native.Secrets`, because
    for one the branch never runs. Reproduced 2026-09-02: with that edit in
    `_secret_tokens()` the suite came back `1 failed, 1888 passed, 3 skipped`
    (recorded at `e176af17`; the baseline was 1889 that day and is 1974 at
    `f6ed1b9a`) and the one failure was this test. So the enumeration below is not
    redundant with the other: it reaches a field that exists only on a
    subclass, and that reach is what caught this.

    Whether a secret VALUE can reach the public mapping is a different question
    with a different answer, and it belongs to
    `test_neither_the_context_secrets_nor_the_password_on_disk_reaches_the_rendered_mapping`
    — asserted over values, over the real `native.Secrets`, and over both the
    context and the on-disk roads, none of which a subclass-only token reaches.
    """

    @dataclass(frozen=True)
    class TwoSecrets(native.Secrets):
        api_token: str = "second-secret-6b2d0f11"

    server_dir = tmp_path / "srv"
    eng = engine(Recorder())
    ctx = replace(context(server_dir), secrets=TwoSecrets(db_password=DB_PASSWORD))
    assert cmangos.secret_token_map(native.Secrets(db_password=DB_PASSWORD)) == {
        "DB_PASSWORD": DB_PASSWORD
    }
    tokens = eng._secret_tokens(ctx)
    assert tokens["API_TOKEN"] == "second-secret-6b2d0f11", "derived, not listed"
    assert tokens["DB_PASSWORD"] == DB_PASSWORD
    added = set(tokens) - set(eng._public_tokens(server_dir))
    declared = {native.secret_token_name(f.name) for f in fields(TwoSecrets)}
    assert added == declared, "a declared field is missing, or a name nobody declared is present"


def test_a_secret_named_like_a_public_token_is_refused_instead_of_shadowing_it(
    tmp_path: Path,
) -> None:
    """`{**public, **secret}` lets the secret WIN, so a name collision is a silent swap.

    Not hypothetical arithmetic: the public half already spells `DB_HOST`,
    `DB_USER`, `DB_PORT` and `CORE_DIR`, and `native.Secrets` is a dataclass
    one field away from any of them. A field named `db_host` would put the
    PASSWORD wherever the conf tables, the SQL and (K.7) verify expect the
    database host — every value still filled, every file still well-formed,
    and `test_the_secret_mapping_spells_each_field_the_way_the_templates_do`
    still green, because `{{DB_PASSWORD}}` would keep coming out right.

    So the collision is refused rather than merged, and the refusal is asserted
    HERE rather than left to the docstring that describes it. The subclass
    names `db_host` because that is the collision this catalog actually
    affords; the assertion is over the token the engine itself produces, not
    over a literal restated in this file, so it follows a rename of the public
    key.
    """

    @dataclass(frozen=True)
    class ShadowingSecrets(native.Secrets):
        db_host: str = "not-a-host-but-a-password"

    server_dir = tmp_path / "srv"
    eng = engine(Recorder())
    collision = native.secret_token_name("db_host")
    assert collision in eng._public_tokens(server_dir), (
        "the public half no longer spells this token, so the fixture no longer collides "
        "with anything and this test would pass for the wrong reason"
    )
    ctx = replace(context(server_dir), secrets=ShadowingSecrets(db_password=DB_PASSWORD))
    with pytest.raises(InstallerError, match=collision):
        eng._secret_tokens(ctx)


def test_the_build_context_already_holds_the_plaintext_password_before_the_build_stage(
    tmp_path: Path,
) -> None:
    """The build context is NOT secret-free, and enumeration is what says which stages.

    `_public_tokens()`'s docstring once counted the stages writing into the
    server dir before the build and got the count wrong twice over: it said
    two, and the stage it left out was `generate-compose`, the one immediately
    before the build. A number in prose cannot go red, so the ordering is read
    off `STAGE_NAMES` here instead and the CONTENT is read off the disk each
    stage wrote.

    Two roads into the context, both asserted below as values on disk:

    * `db-password` runs before `build` and puts the plaintext password at the
      ROOT of the server dir, which is the directory `docker build` is pointed
      at.
    * `generate-compose` runs IMMEDIATELY before `build` and merges the same
      plaintext into `<server_dir>/.env` under `DB_ROOT_PASSWORD`. Nothing
      asserted that road until 2026-09-02, when mutation M-R2 read that file
      out of `_public_tokens()` and left the suite at 1889 passed, 3 skipped
      (2026-09-02, recorded at `e176af17`).

    So from stage 1 the secret is inside the build context as a file, and from
    stage 3 as two. What keeps them out of what the daemon receives is the
    leading `*` of the `.dockerignore`;
    `test_every_shipped_dockerignore_excludes_the_entrys_password_file` covers
    the password file and `test_composegen.py`'s
    `test_every_cmangos_dockerignore_admits_only_the_core_tree_it_copies`
    covers the shape, and no half of this means anything alone.

    What this test does NOT do is notice a stage being ADDED: it names the
    three it cares about and ignores the rest.
    `test_family_and_stage_names_are_the_contract_tuple` restates the whole
    tuple and is the one that goes red for that.
    """
    order = CmangosInstaller.STAGE_NAMES
    wanted = {"db-password", "generate-compose", "build"}
    assert wanted <= set(order), f"a stage was renamed; this test cannot find them all: {order}"
    assert order.index("db-password") < order.index("build")
    assert order.index("generate-compose") == order.index("build") - 1, (
        "`generate-compose` is no longer the stage immediately before the build, so re-read "
        "which stages leave a secret in the context before trusting `_public_tokens()`'s list"
    )

    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    eng = engine(Recorder())
    ctx = context(server_dir)
    list(eng._db_password(ctx))
    plan = ENTRY.install.password
    assert plan.file is not None
    written = server_dir / plan.file
    assert written.read_text(encoding="utf-8").strip() == DB_PASSWORD, "in plaintext, as it must be"
    assert written.parent == server_dir, (
        "the password no longer sits at the root of the build context; re-derive what the "
        "shipped `.dockerignore` has to exclude before trusting the guard that pairs with this"
    )

    list(eng.stage_generate_compose(ctx))
    dotenv = server_dir / composegen.DOTENV_FILE
    assert dotenv.parent == server_dir
    written_env = dotenv.read_text(encoding="utf-8")
    assert f"DB_ROOT_PASSWORD={DB_PASSWORD}" in written_env, (
        "`generate-compose` no longer leaves the plaintext password in the build context; "
        f"re-read what a build ships before trusting the note that says it does:\n{written_env}"
    )


def dockerignore_excludes(ignore_text: str, path: str) -> bool:
    """Would `docker build` withhold `path` from the daemon, given this `.dockerignore`?

    Enough of Docker's rule to answer for a name at the ROOT of the context,
    which is all this file asks about (`.db_password`): patterns are tried in
    order, the LAST one that matches decides, a leading `!` re-includes, and
    `*` does not cross a `/` (Go's `filepath.Match`, one segment at a time).
    Comments and blank lines are skipped.

    Deliberately NOT a general implementation: the parent-directory walk that
    decides a nested path like `etc/mangosd.conf` is where Docker's real
    algorithm gets interesting, and a half-right version of it here would be a
    test asserting this function's bugs. A root-level name needs none of it.
    """
    verdict = False
    for raw in ignore_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        negated = line.startswith("!")
        pattern = re.escape(line[1:] if negated else line)
        # `**` before `*`, or the first replacement eats the second star.
        pattern = pattern.replace(r"\*\*", ".*").replace(r"\*", "[^/]*").replace(r"\?", "[^/]")
        if re.fullmatch(pattern, path):
            verdict = not negated
    return verdict


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_every_shipped_dockerignore_excludes_the_entrys_password_file(
    game: str, tmp_path: Path
) -> None:
    """The leading `*` is the whole of the defence, and this covers the ROOT-level half.

    `db-password` writes the plaintext password into the server dir two stages
    before the build (the test above), so the file is inside the build context
    on disk. What keeps it out of the tarball `docker build` streams to the
    daemon is one character in each shipped `dockerignore.tmpl`: the `*` that
    excludes everything, before the `!src/...` line re-includes the core tree.

    Nothing tested that. A `!` re-include added below those lines — the shape
    of edit somebody makes to get one more file into a build — would ship the
    password into an image layer with the whole suite green, and an image layer
    is the expensive kind of leak: `docker history` prints it long after the
    file is deleted.

    Rendered through the real `dockerfile.render()` and the real
    `_public_tokens()`, not read as a template, because it is the RENDERED text
    the daemon reads; and asked about the file the ENTRY names rather than a
    literal `.db_password`, so a catalog that renamed it is answered about the
    file it actually writes.

    **What was measured about its overlap, and about its limit, 2026-09-02.**
    `test_composegen.py`'s
    `test_every_cmangos_dockerignore_admits_only_the_core_tree_it_copies`
    arrived with `yulon-phase7` and pins the SHAPE — first line `*`, exactly one
    `!` line, and it names the core tree. Three mutations were run against the
    pair:

    * the leading `*` deleted — both go red.
    * `!.db_password` appended — both go red.
    * `!etc` appended to `wow-tbc/native/dockerignore.tmpl` — the shape test
      goes red for `wow-tbc`; THIS test stays green on all three
      parametrisations.

    The third is the honest limit and is written down rather than left for a
    reader to infer coverage this test does not have. `dockerignore_excludes()`
    answers for a name at the ROOT of the context and has no parent-directory
    walk, so the password-bearing `.conf` files `conf` writes under `etc/` are
    outside what it can be asked about. Nothing on this branch asserts that
    `etc/*.conf` stays out of the build context; what stands there is the shape
    test's refusal of a second `!` line plus Docker's own rule that an excluded
    directory takes its children with it.

    The two are kept apart because they answer to different owners: that one is
    about which SOURCE TREE reaches the daemon — its whole docstring is about
    `src/tbc-db` and `.git` — so a legitimate build change needing a second `!`
    line will be argued with by editing its assertions, and after that edit
    nothing would be left saying the PASSWORD must not go. This test names the
    password, derives it from `install.password`, and has to be argued with
    separately.
    """
    entry = installable(load_catalog().get(game))
    plan = entry.install.password
    assert plan.mode == "generated" and plan.file is not None, (
        f"{game} keeps no password file, so there is nothing here to exclude and this "
        "parametrisation is vacuous for it"
    )
    native_block = entry.install.native
    assert native_block is not None and native_block.dockerfile_dir is not None
    eng = engine_for(entry, Recorder(), platform_id=lambda: "linux")
    template_dir = resources.installers_dir() / native_block.dockerfile_dir
    _text, ignore = dockerfile.render(template_dir, eng._public_tokens(tmp_path / "srv"))

    withheld = dockerignore_excludes(ignore, plan.file)
    assert withheld, f"{game} would send {plan.file} to the build daemon:\n{ignore}"
    # The matcher must be able to answer "no", or the assertion above is a
    # function that returns True. Two controls, because the obvious one covers
    # less than it used to say. The core tree is the one path these templates
    # deliberately let THROUGH; a comment here claimed it answered "no" only if
    # the `!` lines were read AND `*` was kept from crossing a `/`. Measured
    # 2026-09-02: only the second half was true. With the `!` branch deleted
    # (`negated = False`) all three parametrisations stayed green, because
    # `!src/mangos-tbc` then matches nothing at all and the verdict for a
    # nested path is left False by `*` alone.
    core = entry.emulator.sources[0].dest
    assert "/" in core, (
        f"{core} no longer has a path separator, so this control now depends on the `!` "
        "handling rather than on `*` not crossing a `/`; re-read what it proves"
    )
    assert not dockerignore_excludes(ignore, core), (
        f"the matcher withholds {core}, which every one of these templates re-includes, so "
        "it is letting `*` cross a `/` and the assertion above proves nothing"
    )
    # So the `!` branch gets a control of its own, on a fixture rather than on a
    # shipped template: a root-level name that only a re-include can rescue.
    # This one does go red when the branch is deleted.
    assert dockerignore_excludes("*", "keepme"), "the fixture control is not excluded to begin with"
    assert not dockerignore_excludes("*\n!keepme", "keepme"), (
        "the matcher ignores a `!` re-include, so it cannot be trusted to notice one added "
        "below the leading `*` — which is the edit this test exists to catch"
    )


def test_the_secret_mapping_spells_each_field_the_way_the_templates_do(tmp_path: Path) -> None:
    """`db_password` -> `DB_PASSWORD`: the derivation has to match what the catalog wrote.

    A derivation is only as good as its spelling rule, and the shipped conf
    tables and SQL statements name `{{DB_PASSWORD}}` literally — a rule that
    produced `DBPASSWORD` would leave `_secret_tokens()` looking correct and
    every conf value unfilled. So the assertion is against the catalog's own
    text, not against a constant restated here.
    """
    assert CMANGOS is not None
    written = "\n".join(
        f"{key} = {value}"
        for patch in CMANGOS.conf.files.values()
        for key, value in patch.keys.items()
    )
    assert "{{DB_PASSWORD}}" in written, "no shipped conf value names the secret; test is vacuous"
    tokens = engine(Recorder())._secret_tokens(context(tmp_path / "srv"))
    assert set(cmangos.secret_token_map(native.Secrets(db_password=DB_PASSWORD))) == {"DB_PASSWORD"}
    assert composegen.fill(written, tokens).count(DB_PASSWORD) == written.count("{{DB_PASSWORD}}")


def test_no_dockerfile_template_names_the_secret_the_conf_mapping_carries() -> None:
    """A tripwire over the shipped templates, third in a line of three protections.

    It is not what stands between the mapping and the secret, and since 7.3 it
    is not even second. `_write_dockerfile` renders from `_public_tokens()`,
    which has no secret in it; `dockerfile.SECRET_TOKENS` refuses the token BY
    NAME and drops the key, for any mapping any caller hands over; and this
    test says the shipped six templates are clean. The refusal says the
    seventh cannot happen wherever it is put.

    Which is the correction this test needed. A reviewer defeated the version
    that globbed `*/native/` by planting
    `catalog/installers/shared/cmangos/Dockerfile.tmpl` spelling
    `ENV DB_PASSWORD={{DB_PASSWORD}}`: the guard passed, the whole suite passed,
    and `render()` returned a Dockerfile with the password in it. That folder is
    not hypothetical — the compose templates for all three CMaNGOS games live
    there, and `NativeInstall.dockerfile_dir` is an unvalidated `str | None`. So
    the walk is now the whole installers tree rather than one directory shape;
    `test_a_dockerfile_template_the_glob_cannot_see_still_cannot_bake_the_secret`
    is the property version of the same sentence.
    """
    root = resources.installers_dir()
    templates = sorted(root.rglob("Dockerfile.tmpl")) + sorted(root.rglob("dockerignore.tmpl"))
    assert templates, "no Dockerfile templates found; this test would pass vacuously"
    assert len(templates) >= 6, "the three shipped CMaNGOS pairs, at least"
    for path in templates:
        assert "{{DB_PASSWORD}}" not in path.read_text(encoding="utf-8"), path


def test_a_dockerfile_template_the_glob_cannot_see_still_cannot_bake_the_secret(
    tmp_path: Path,
) -> None:
    """The reviewer's bypass of the test above, reproduced and now refused.

    The glob is a LOCATION; the refusal is a PROPERTY. The bypass planted
    `catalog/installers/shared/cmangos/Dockerfile.tmpl` spelling
    `ENV DB_PASSWORD={{DB_PASSWORD}}` — a folder the old `*/native/` glob never
    walked, and not a hypothetical one: the compose templates for all three
    CMaNGOS games already live at `shared/cmangos/`, and `dockerfile_dir` is an
    unvalidated `str | None`. With that file in place the guard test above and
    the whole suite passed clean while `render()` returned a Dockerfile with the
    password in it. The glob is widened to the whole tree as a tripwire; this is
    the guarantee.

    Rendered from the REAL `_secret_tokens()` mapping — the SECRET-bearing one,
    deliberately, and not the one `_write_dockerfile` now passes. 7.3 took the
    password out of the build-context mapping; if this test followed it there,
    `render()`'s own refusal would stop being proved by anything and could be
    deleted green. Defence in depth is only defence while something still
    attacks it.
    """
    folder = tmp_path / "shared" / "cmangos"
    folder.mkdir(parents=True)
    (folder / "Dockerfile.tmpl").write_text(
        f"{composegen.GENERATED_MARKER} - do not hand-edit.\n"
        "FROM debian:12\nENV DB_PASSWORD={{DB_PASSWORD}}\n",
        encoding="utf-8",
        newline="\n",
    )
    (folder / "dockerignore.tmpl").write_text(
        f"{composegen.GENERATED_MARKER} - do not hand-edit.\n*\n", encoding="utf-8", newline="\n"
    )
    tokens = engine(Recorder())._secret_tokens(context(tmp_path / "srv"))
    assert tokens["DB_PASSWORD"] == DB_PASSWORD, "the secret-bearing set, on purpose"
    with pytest.raises(dockerfile.DockerfileError, match="DB_PASSWORD") as caught:
        dockerfile.render(folder, tokens)
    assert DB_PASSWORD not in str(caught.value)
    # The refusal's own words, not the belt's. Dropping the key from the mapping ALSO
    # stops the render — as an unfilled placeholder — so without this line the test
    # would stay green with the by-name refusal deleted, which is the bug it is here for.
    assert "docker history" in str(caught.value)


def test_image_ref_names_the_built_server_image_and_refuses_an_unknown_service(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "srv"
    eng = engine(Recorder())
    refs = composegen.built_image_refs(ENTRY, server_dir, platform_id=lambda: "linux")
    assert CMANGOS is not None
    assert eng._image_ref(context(server_dir), CMANGOS.extract.image) in refs
    with pytest.raises(InstallerError, match="not one of the images"):
        eng._image_ref(context(server_dir), "no-such-service")


def functions_spending(tail_name: str) -> set[str]:
    """Every function in `cmangos.py` whose body interpolates the module constant `tail_name`.

    Read off the module's own AST rather than counted by hand, because a count
    in a docstring is what went stale here: this test said "Three refusals" and
    enumerated three, and the commit that wrote that sentence added a FOURTH
    user of `CATALOG_ERROR_TAIL` in the same diff (`_secret_tokens()`'s
    collision refusal, since moved to `DECLARATION_ERROR_TAIL`). A `Name` node
    is what is looked for, so the mentions inside docstrings — `_data()`'s, and
    the constants' own — are not counted; only code that spends it is.
    """
    tree = ast.parse(inspect.getsource(cmangos))
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        for inner in ast.walk(node)
        if isinstance(inner, ast.Name) and inner.id == tail_name
    }


def test_the_family_s_catalog_refusals_end_in_one_tail_and_not_two(tmp_path: Path) -> None:
    """Every refusal that spends the catalog tail, driven — the set derived, not restated.

    Until 2026-09-01 two ended "That is a catalog error in the app…" and a
    third "That is a bug in the app's catalog…", 65 lines apart. A second
    wording for one thing drifts further from the first every time either is
    edited, which is why `test_sqlplan.py` asserts two of its refusals are the
    SAME string rather than merely that both complain.

    The list of refusals is now derived from the module rather than counted
    here, and the reason is this test's own history: it opened "Three refusals
    …" and named three, in the same commit that gave `CATALOG_ERROR_TAIL` a
    fourth user. The prose was stale before it was pushed. `functions_spending()`
    asks the AST instead, so a fifth user turns this red with the name of the
    function that added it.

    `DECLARATION_ERROR_TAIL` is asserted here too, and against a different
    owner: `_secret_tokens()`'s collision refusal cannot be caused by any
    catalog file — both halves of the collision are Python declarations — so it
    ended in the wrong tail until 2026-09-02. Pinning WHICH functions spend
    WHICH tail is what keeps that from sliding back.

    `_native()` is deliberately in neither set: its refusal ("has no
    `install.native` section") carries no tail at all, and whether it should is
    a separate question this does not settle.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    no_block = ENTRY.model_copy(
        update={
            "install": ENTRY.install.model_copy(
                update={
                    "native": (
                        ENTRY.install.native.model_copy(update={"cmangos": None})
                        if ENTRY.install.native is not None
                        else None
                    )
                }
            )
        }
    )
    no_file = entry_with_password(
        PasswordPlan.model_construct(mode="generated", value=None, file=None, prefix="tbc-")
    )

    @dataclass(frozen=True)
    class ShadowingSecrets(native.Secrets):
        db_host: str = "not-a-host-but-a-password"

    said: dict[str, str] = {}
    with pytest.raises(InstallerError) as from_block:
        engine_for(no_block, Recorder(), volume_exists=refuse_to_answer)._data()
    said["_data"] = str(from_block.value)
    with pytest.raises(InstallerError) as from_image:
        engine(Recorder())._image_ref(context(server_dir), "no-such-service")
    said["_image_ref"] = str(from_image.value)
    with pytest.raises(InstallerError) as from_password:
        eng = engine_for(no_file, Recorder(), volume_exists=refuse_to_answer)
        list(eng._db_password(context(server_dir)))
    said["_db_password"] = str(from_password.value)

    assert set(said) == functions_spending("CATALOG_ERROR_TAIL"), (
        "a function spends the catalog tail that this test does not drive, or drives one "
        "that no longer spends it; add or remove the case rather than editing this line"
    )
    for where, refusal in said.items():
        assert refusal.endswith(cmangos.CATALOG_ERROR_TAIL), f"{where}: {refusal}"
        assert refusal != cmangos.CATALOG_ERROR_TAIL, "the tail is a tail, not the whole refusal"

    # The other tail, driven the same way: derived set, then the value.
    collided = replace(context(server_dir), secrets=ShadowingSecrets(db_password=DB_PASSWORD))
    declared: dict[str, str] = {}
    with pytest.raises(InstallerError) as from_collision:
        engine(Recorder())._secret_tokens(collided)
    declared["_secret_tokens"] = str(from_collision.value)
    assert set(declared) == functions_spending("DECLARATION_ERROR_TAIL"), (
        "a function spends the declaration tail that this test does not drive, or drives "
        "one that no longer spends it"
    )
    for where, refusal in declared.items():
        assert refusal.endswith(cmangos.DECLARATION_ERROR_TAIL), f"{where}: {refusal}"
        sent_to_catalog = refusal.endswith(cmangos.CATALOG_ERROR_TAIL)
        wrong_tail = f"{where} sends the reader to the catalog, which cannot cause this refusal"
        assert not sent_to_catalog, wrong_tail


def test_the_typed_blocks_refuse_a_catalog_that_does_not_carry_them(tmp_path: Path) -> None:
    """`_native()`/`_data()` name a catalog error in the app, never a machine's."""
    no_cmangos = ENTRY.model_copy(
        update={
            "install": ENTRY.install.model_copy(
                update={
                    "native": (
                        ENTRY.install.native.model_copy(update={"cmangos": None})
                        if ENTRY.install.native is not None
                        else None
                    )
                }
            )
        }
    )
    eng = CmangosInstaller(
        no_cmangos,
        installers_root=resources.installers_dir(),
        seams=Recorder().seams(platform_id=lambda: "linux"),
    )
    with pytest.raises(InstallerError, match="catalog"):
        eng._data()
    no_native = ENTRY.model_copy(
        update={"install": ENTRY.install.model_copy(update={"native": None})}
    )
    bare = CmangosInstaller(
        no_native,
        installers_root=resources.installers_dir(),
        seams=Recorder().seams(platform_id=lambda: "linux"),
    )
    with pytest.raises(InstallerError, match="install.native"):
        bare._native()


def test_user_args_ask_platform_py_through_the_seam_and_not_the_real_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`--user uid:gid` on Linux, nothing on Docker Desktop — and the SEAM decides which.

    `platform.container_user_args()`'s own docstring warns that its
    `platform_id` default is bound at import, so an engine that called it with
    no argument would ask the real host. On this Windows runner that mistake is
    invisible without the fake ids: `os.getuid` does not exist, so the wrong
    answer and the right one are both `()`. The ids are what make the two
    distinguishable, which is the only reason this test can fail.
    """
    monkeypatch.setattr(platform.os, "getuid", lambda: 1000, raising=False)
    monkeypatch.setattr(platform.os, "getgid", lambda: 1001, raising=False)
    assert engine(Recorder())._user_args() == ("--user", "1000:1001")
    assert engine(Recorder(), platform_id=lambda: "macos")._user_args() == ()
    assert engine(Recorder(), platform_id=lambda: "windows")._user_args() == ()


# -- _stream ----------------------------------------------------------------


def test_stream_interleaves_the_sink_and_the_generator_without_buffering() -> None:
    """Both halves arrive as they are produced, and the caller sees one stream."""

    def call(sink: docker.OutputSink) -> Iterator[str]:
        sink("container said one")
        yield "progress one"
        sink("container said two")
        yield "progress two"

    got = list(engine(Recorder())._stream(call))
    assert got == ["container said one", "progress one", "container said two", "progress two"]


def test_stream_yields_each_line_before_the_next_is_produced() -> None:
    """Not a list at the end: the consumer gets line one while the worker is still on line two."""
    released = threading.Event()

    def call(sink: docker.OutputSink) -> Iterator[str]:
        yield "first"
        assert released.wait(5), "the consumer had not been handed line one"
        yield "second"

    stream = engine(Recorder())._stream(call)
    assert next(stream) == "first"
    released.set()
    assert next(stream) == "second"
    with pytest.raises(StopIteration):
        next(stream)


def test_stream_re_raises_an_installer_error_untouched_and_wraps_anything_else() -> None:
    """The stage-kind modules' own refusals are the sentence the user reads; a bug is not.

    Asserted by EQUALITY, not by `match=`. The wrapper spells the original
    exception into its own message, so `match="Finished tiles are kept"` is
    satisfied by the wrapped copy just as well and a `_stream` that wrapped
    everything would pass it — the loose-assertion shape, caught by mutation.
    """

    def refuses(sink: docker.OutputSink) -> Iterator[str]:
        yield "starting"
        raise InstallerError("mmaps was stopped. Finished tiles are kept.")

    with pytest.raises(InstallerError) as raised:
        list(engine(Recorder())._stream(refuses))
    assert str(raised.value) == "mmaps was stopped. Finished tiles are kept."

    def breaks(sink: docker.OutputSink) -> Iterator[str]:
        raise ZeroDivisionError("division by zero")
        yield ""  # pragma: no cover - unreachable, keeps this a generator

    with pytest.raises(InstallerError, match="the step could not be run: division by zero"):
        list(engine(Recorder())._stream(breaks))


# -- the Recorder's new doubles ---------------------------------------------


def test_the_sql_doubles_take_the_distro_the_sql_plan_always_passes(tmp_path: Path) -> None:
    """`sqlplan.apply()` passes `wsl_distro=` UNCONDITIONALLY; a fake without it is a TypeError.

    Driven through the real `apply()` rather than called directly, because
    "does this double satisfy `ExecStdin`?" is a question only the caller can
    answer at runtime.
    """
    rec = Recorder()
    phase = SQL.phases[0]
    run = sqlplan.PhaseRun(
        phase=phase,
        schema="mangos",
        path=None,
        statement="SELECT 1;",
        gzip=False,
        rel="statement 1",
    )
    lines: list[str] = []
    said = list(
        sqlplan.apply(
            (run,),
            container="tbc-db",
            client="mariadb",
            password=DB_PASSWORD,
            exec_stdin=rec.exec_stdin,
            sink=lines.append,
            cancel=None,
            wsl_distro="Ubuntu-24.04",
        )
    )
    assert said
    assert rec.sql_calls == ["SELECT 1;"]
    assert rec.distros == ["Ubuntu-24.04"], "the double dropped the distro instead of recording it"


def test_the_query_double_answers_verbatim_so_one_empty_row_is_not_no_rows() -> None:
    """`docker.sql_query()` returns stdout WITH its trailing newline; the double must too.

    Under `--skip-column-names` one row holding the empty string prints `"\\n"`
    and no rows print `""`. A fake whose answer can never carry a newline
    cannot tell those two apart, and telling them apart is the whole of the
    marker gate's `absent`/`populated` split.
    """
    rec = Recorder()
    assert rec.query_answer == "20000\n"
    assert rec.sql_query("tbc-db", "mariadb", DB_PASSWORD, "mangos", "SELECT COUNT(*)") == "20000\n"
    assert rec.sql_query("tbc-db", "mariadb", DB_PASSWORD, None, "SELECT 1", wsl_distro="U") == (
        "20000\n"
    )
    assert rec.distros == [None, "U"]

    no_rows = Recorder(query_answer="")
    one_empty_row = Recorder(query_answer="\n")
    ask = ("tbc-db", "mariadb", DB_PASSWORD, "mangos", "SELECT marker FROM t")
    assert len(no_rows.sql_query(*ask).splitlines()) == 0
    assert len(one_empty_row.sql_query(*ask).splitlines()) == 1


def test_the_failing_sql_switch_answers_the_way_the_client_does() -> None:
    rec = Recorder(failing_sql="DROP")
    import io

    ok = rec.exec_stdin("tbc-db", ["mariadb"], io.BytesIO(b"SELECT 1;\n"), env={})
    bad = rec.exec_stdin("tbc-db", ["mariadb"], io.BytesIO(b"DROP TABLE t;\n"), env={})
    assert ok.returncode == 0
    assert bad.returncode == 1 and "ERROR 1064" in bad.stderr


def test_the_run_container_double_lays_exactly_what_the_real_plan_counts(tmp_path: Path) -> None:
    """The fixture's folder names are the catalog's, not a convenient invention.

    A `produce` naming a folder no tool produces is a test that cannot fail:
    `extract.shortfall()` counts the folders the plan's `produces` names, and
    the mmaps stage counts `mmaps` against `min_files`.
    """
    assert CMANGOS is not None
    wanted: dict[str, int] = {}
    for tool in CMANGOS.extract.tools:
        wanted.update(tool.produces)
    wanted["mmaps"] = CMANGOS.mmaps.min_files
    assert Recorder().produce == wanted

    rec = Recorder()
    out = tmp_path / "out"
    out.mkdir()
    spec = docker.ContainerRun(
        image="yulon.local/cmangos-tbc-server:t",
        argv=("/opt/mangos/bin/tools/ad",),
        mounts=(docker.Mount(out, "/out"),),
    )
    lines: list[str] = []
    result = rec.run_container(spec, sink=lines.append)
    assert result.returncode == 0
    assert rec.container_runs == [spec]
    assert lines == ["/opt/mangos/bin/tools/ad ran"]
    assert extract.file_count(out / "dbc") == 100
    assert extract.file_count(out / "mmaps") == 500


def test_the_run_container_double_leaves_nothing_when_the_run_failed(tmp_path: Path) -> None:
    """A tool that exited non-zero produced nothing; a double that still fills `/out` hides that."""
    rec = Recorder(run_result=docker.AttachedRun(1, ("segfault",)))
    out = tmp_path / "out"
    out.mkdir()
    rec.run_container(
        docker.ContainerRun(image="i", argv=("ad",), mounts=(docker.Mount(out, "/out"),)),
        sink=lambda line: None,
    )
    assert list(out.iterdir()) == []


def extract_run(client: Path, out: Path) -> docker.ContainerRun:
    """The shape K.5 will ask the double for: the client `:ro` FIRST, `/out` second.

    Two mounts in an order where "the first mount" and "the `/out` mount" are
    different directories, because that is the only arrangement in which the
    double's choice between them is observable. Every other `ContainerRun` in
    this file carries a single mount, so a `Recorder` that ignored `guest` and
    filled `spec.mounts[0]` would answer all of them correctly.
    """
    return docker.ContainerRun(
        image="yulon.local/cmangos-tbc-server:t",
        argv=("/opt/mangos/bin/tools/ad",),
        mounts=(docker.Mount(client, "/client", read_only=True), docker.Mount(out, "/out")),
        workdir="/out",
    )


def test_the_run_container_double_lays_its_output_under_out_and_not_under_the_first_mount(
    tmp_path: Path,
) -> None:
    """A double that cannot express the real run's shape cannot be asked about it.

    The real extraction run mounts two directories — the user's client
    read-only and the server's `data/` at `/out` — and `Recorder.run_container`
    picks the second by `guest == "/out"`. With one mount per fixture that
    selection is never exercised: replacing it with `spec.mounts[0].host`
    passed all 1774 tests (2026-09-01). The client half is what makes it fail,
    and it is also the half whose emptiness is the extraction stage's safety
    argument — an interrupted tool must leave nothing in the user's client.
    """
    rec = Recorder()
    client = client_folder(tmp_path)
    out = tmp_path / "srv" / "data"
    out.mkdir(parents=True)
    spec = extract_run(client, out)
    assert spec.mounts[0].guest != "/out", "the fixture must not be single-mount-shaped"

    before = sorted(p.name for p in (client / "Data").iterdir())
    assert rec.run_container(spec, sink=lambda line: None).returncode == 0
    assert rec.container_runs == [spec]
    assert extract.file_count(out / "dbc") == 100
    assert extract.file_count(out / "mmaps") == 500
    # Nothing landed on the read-only mount, and nothing beside it either.
    assert sorted(p.name for p in client.iterdir()) == ["Data"]
    assert sorted(p.name for p in (client / "Data").iterdir()) == before


def test_the_copy_double_answers_for_one_conf_file_and_for_the_whole_etc_directory(
    tmp_path: Path,
) -> None:
    rec = Recorder()
    one = tmp_path / "etc" / "mangosd.conf.dist"
    rec.copy_from_image("img", "/opt/mangos/etc/mangosd.conf.dist", one)
    assert 'LoginDatabaseInfo = "old"' in one.read_text(encoding="utf-8")
    whole = tmp_path / "all"
    rec.copy_from_image("img", "/opt/mangos/etc", whole)
    assert sorted(p.name for p in whole.iterdir()) == sorted(rec.conf_dist)
    assert rec.copied == [
        ("img", "/opt/mangos/etc/mangosd.conf.dist", one),
        ("img", "/opt/mangos/etc", whole),
    ]


def test_the_volume_double_answers_about_this_machine_and_not_about_the_name() -> None:
    rec = Recorder()
    assert not rec.volume_exists("tbc-abc_db-data")
    rec.volumes.add("tbc-abc_db-data")
    assert rec.volume_exists("tbc-abc_db-data")
    assert not rec.volume_exists("other_db-data")


def test_the_seams_carry_every_new_double_through_to_the_engine() -> None:
    """Additive fields, wired: an engine built from `seams()` reaches the Recorder, not docker."""
    rec = Recorder()
    seams = rec.seams(platform_id=lambda: "linux")
    assert seams.run_container == rec.run_container
    assert seams.copy_from_image == rec.copy_from_image
    assert seams.exec_stdin == rec.exec_stdin
    assert seams.sql_query == rec.sql_query
    assert seams.volume_exists == rec.volume_exists
    # Not `docker`'s: a seam that fell back to the default would reach a daemon.
    assert seams.run_container != docker.run_container
    assert seams.volume_exists != docker.volume_exists


# -- the stages that are bound today ----------------------------------------


def test_the_bound_stages_run_in_order_and_record_the_recorded_ones(tmp_path: Path) -> None:
    """End to end over the whole tuple; K.7 inserted `import` between them.

    Also drives `lay_sql`/`on_clone`, so the SQL fixtures the import spends are
    proved to land under the source that owns them — and it is the only test
    here that reaches `_write_dockerfile`, `_extract`, `_mmaps` and `_import`
    through `run()` rather than by calling the bodies, so each `Stage` really
    is wired to its method and the two long stages really do sit after the
    build.
    """
    rec = Recorder()
    server_dir = tmp_path / "srv"
    client = client_folder(tmp_path)
    said = install(rec, server_dir, client)
    assert [line for line in said if line.startswith("--- ")] == [
        "--- clone-sources",
        "--- db-password",
        "--- write-dockerfile",
        "--- generate-compose",
        "--- build",
        "--- extract",
        "--- mmaps",
        "--- conf",
        "--- start-db",
        "--- import",
        "--- up",
        "--- ready",
    ]
    state = native.read_state(server_dir, valid=engine(rec).stage_names())
    assert state is not None
    assert state.completed == (
        "clone-sources",
        "write-dockerfile",
        "generate-compose",
        "build",
        "extract",
        "mmaps",
        "conf",
        "import",
    )
    # The pair really landed, from the whole install rather than from a direct
    # call to the body, and the password is in neither: the stage renders from
    # `_public_tokens()`, which never held it, and `dockerfile.render()`'s own
    # by-name refusal stands behind that.
    for name in ("Dockerfile", ".dockerignore"):
        built = (server_dir / name).read_text(encoding="utf-8")
        assert built.startswith(composegen.GENERATED_MARKER)
        assert DB_PASSWORD not in built
    # `db-password` ran — the file is there — and is deliberately NOT in the
    # record. The FILE is the evidence a secret exists; a state file that
    # claimed one would survive the file being deleted, and the next run would
    # mint a second password over a live database.
    assert (server_dir / ".db_password").is_file()
    assert "db-password" not in state.completed
    assert state.family == "cmangos"
    assert [spec.dest for spec in rec.clones] == [
        server_dir / "src" / "mangos-tbc",
        server_dir / "src" / "mangos-tbc" / "src" / "modules" / "Bots",
        server_dir / "src" / "tbc-db",
    ]
    # `on_clone` laid the plan's own files, each under the source that owns it:
    # a literal path and a plain glob under the core, a gzipped one under the
    # content repo, and one under the NESTED playerbots dest. That the run
    # finished at all is the proof of the last one's timing — laid by the core
    # clone instead, `src/mangos-tbc/src/modules/Bots` would have had files in
    # it before its own clone and the spine would have refused it by name.
    assert (server_dir / "src/mangos-tbc/sql/base/realmd.sql").is_file()
    assert (server_dir / "src/mangos-tbc/sql/updates/mangos/0001.sql").is_file()
    dump = server_dir / "src/tbc-db/Full_DB/TBCDB_0002.sql.gz"
    assert gzip.decompress(dump.read_bytes()).endswith(b"SELECT 2;\n")
    assert (server_dir / "src/mangos-tbc/src/modules/Bots/sql/world/0001.sql").is_file()


def test_clone_sources_consults_the_record_under_its_own_stage_name(tmp_path: Path) -> None:
    """`stage_clone_sources` needs `recorded_as`; without the family's own name it re-clones.

    A resume that fetches and resets the user's checkout on every press is the
    incident this argument exists for (`stage_clone_sources`'s docstring).
    """
    rec = Recorder()
    server_dir = tmp_path / "srv"
    client = client_folder(tmp_path)
    install(rec, server_dir, client)
    first = len(rec.clones)
    again = Recorder()
    again.remotes = dict(rec.remotes)
    said = install(again, server_dir, client)
    assert first == 3
    assert again.clones == [], "\n".join(said)
    assert any("already in src/mangos-tbc" in line for line in said)


# -- the copy a Stop reads ---------------------------------------------------


def test_the_build_cancel_note_is_said_at_the_build_and_not_before_every_stage(
    tmp_path: Path,
) -> None:
    """It is copy about the BUILD, and the spine says it for whichever stage claims it.

    `stages()` is where this family claims it, and `cancel_note=` is one
    keyword: dropping it left all 23 tests in this file green (2026-09-01), so
    the sentence a user reads when they press Stop three hours into a compile
    was nobody's. Two properties, because the AzerothCore incident it mirrors
    (`test_families_azerothcore.py`, review 2026-08-23) was not a missing
    sentence but a misplaced one — said as the second line of every install, so
    a user who stopped during the 2.4 GB clone was told Docker was finishing a
    build step. `OPENING_NOTE` is the line that is true of every stage; this
    one belongs to the build alone, and the spine emits it directly under the
    stage banner.
    """
    rec = Recorder(images=False)
    said = install(rec, tmp_path / "srv", client_folder(tmp_path))
    assert native.OPENING_NOTE in said
    assert said.index(native.OPENING_NOTE) == 1
    build_at = said.index("--- build")
    # Said at the build: the banner, then the note, and nothing between them.
    assert said[build_at + 1] == native.BUILD_CANCEL_NOTE
    # Not before every stage: no earlier stage carries it, and no later one either.
    assert said.count(native.BUILD_CANCEL_NOTE) == 1
    assert native.BUILD_CANCEL_NOTE not in said[:build_at]


# -- db-password -------------------------------------------------------------


def db_volume(server_dir: Path) -> str:
    """`<compose project>_db-data` — recomputed here rather than asked of the engine.

    The engine's `_db_volume()` is the code under test, so these tests build the
    name from `composegen.project_name()` and the key the shared template
    declares. `test_the_module_constants_are_the_shared_template_s_own_spellings`
    is what ties `db-data` to `base.yml.tmpl`.
    """
    project = composegen.project_name(ENTRY.id, server_dir, platform_id=lambda: "linux")
    return f"{project}_db-data"


def entry_with_password(plan: PasswordPlan) -> CatalogEntry:
    """`ENTRY` carrying a different `install.password`, still installable on linux."""
    return ENTRY.model_copy(update={"install": ENTRY.install.model_copy(update={"password": plan})})


def engine_for(entry: CatalogEntry, rec: Recorder, **overrides: object) -> CmangosInstaller:
    return CmangosInstaller(
        entry,
        installers_root=resources.installers_dir(),
        seams=rec.seams(**{"platform_id": lambda: "linux", **overrides}),
    )


def refuse_to_answer(name: str) -> bool:
    """A `volume_exists` seam that fails the test if it is called at all.

    `AssertionError` and not `DockerCommandError`: the stage catches the latter
    and turns it into a refusal, which would hide the call being made.
    """
    raise AssertionError(f"the daemon was asked about {name}")


def test_db_password_writes_the_generated_secret_with_the_trailing_newline_the_spine_strips(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    said = list(engine(Recorder())._db_password(context(server_dir)))
    assert ENTRY.install.password.file is not None
    secret = server_dir / ENTRY.install.password.file
    assert secret.read_bytes() == (DB_PASSWORD + "\n").encode("utf-8")
    assert secret.read_text(encoding="utf-8").strip() == DB_PASSWORD
    assert any(ENTRY.install.password.file in line for line in said), said


def test_the_line_that_says_the_password_was_written_says_to_back_the_file_up(
    tmp_path: Path,
) -> None:
    """The advice is the warning that this file is the way back into the database.

    The stage writes the file once and says one sentence about it; the refusal
    that explains what the file was worth is only reached after it is already
    gone, which is too late to act on. So the advice is load-bearing where it
    stands, and it is pinned by its own words rather than left to the
    filename assertion above.

    Dropping "Back that file up" from the success line survived the whole
    suite before this test existed (mutation M16, 2026-09-01).
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    said = list(engine(Recorder())._db_password(context(server_dir)))
    assert len(said) == 1, said
    assert "Back that file up" in said[0]
    assert db_volume(server_dir) in said[0]


def test_db_password_turns_a_write_that_fails_into_a_sentence_and_not_a_traceback(
    tmp_path: Path,
) -> None:
    """A stage that raises `OSError` reaches the user as a traceback, not as a refusal.

    The failure is a real one rather than a patched call: `.db_password` is a
    DIRECTORY here, so `os.open()` raises before a byte is written
    (`IsADirectoryError` on POSIX, `PermissionError` on Windows — both
    `OSError`). `Path.is_file()` is False for a directory, so the stage takes
    the same write branch it takes for a missing file, which is the branch
    under test.

    `__cause__` is asserted, not just the type: the sentence is only useful
    while it still carries what the operating system said, and `raise ... from
    exc` is what keeps that.

    Removing the `except OSError` wrapper made nothing in the suite red before
    this test existed (mutation M14, 2026-09-01).
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    assert ENTRY.install.password.file is not None
    (server_dir / ENTRY.install.password.file).mkdir()
    with pytest.raises(InstallerError) as refusal:
        list(engine(Recorder())._db_password(context(server_dir)))
    assert isinstance(refusal.value.__cause__, OSError)
    assert ENTRY.install.password.file in str(refusal.value)
    assert "could not be written" in str(refusal.value)


def test_db_password_keeps_a_secret_file_that_is_already_there_and_never_asks_docker(
    tmp_path: Path,
) -> None:
    """A resume must not mint a second password, and must not need a daemon to know that.

    The second context carries a DIFFERENT secret on purpose: an mtime is a
    coarse oracle (an overwrite inside one filesystem timestamp tick moves
    nothing), while the bytes on disk answer differently the second time only
    if the stage really left them alone.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    ctx = context(server_dir)
    list(engine(Recorder())._db_password(ctx))
    assert ENTRY.install.password.file is not None
    secret = server_dir / ENTRY.install.password.file
    other = replace(ctx, secrets=native.Secrets(db_password="a-completely-different-secret"))
    again = list(engine(Recorder(), volume_exists=refuse_to_answer)._db_password(other))
    assert secret.read_text(encoding="utf-8").strip() == DB_PASSWORD
    assert any("already" in line for line in again), again


def file_aces(path: Path) -> list[str]:
    """The access-control entries `icacls` lists for `path`, one string per entry.

    The listing is the block before the first blank line; the first entry
    shares its line with the path, and a wrapped continuation of a long entry
    carries no `:(`, so the entries are the lines that do.

    Callers read these for the `(I)` inherited flag and never for a principal:
    the names are localised. Measured on PKGAME-LAPTOP (Windows 11 26200,
    Norwegian, CPython 3.13.14, 2026-09-01) the built-in groups printed as
    `NT-MYNDIGHET\\SYSTEM` and `BUILTIN\\Administratorer`, so a test matching
    `BUILTIN\\Users` there would fail for the language and not for the ACL.
    """
    listing = subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    ).stdout
    return [line.strip() for line in listing.split("\n\n")[0].splitlines() if ":(" in line]


def test_the_secret_file_is_owner_only_on_posix_and_only_inherits_the_folder_acl_on_windows(
    tmp_path: Path,
) -> None:
    """What the 0600 the writer asks for actually buys, per platform — measured, not assumed.

    Measured on PKGAME-LAPTOP, Windows 11 26200, CPython 3.13.14, 2026-09-01,
    and reproduced there on 2026-09-01 while this assertion was written:
    `os.open(path, O_WRONLY|O_CREAT|O_TRUNC, 0o600)` leaves `st_mode & 0o777`
    at `0o666`, byte-identical to a plain `open(path, "w")`, and every ACE
    `icacls` prints for the file carries `(I)` — the file has no entry of its
    own and grants exactly what its folder grants. Under a folder granting
    `BUILTIN\\Users:(RX)` the secret is readable by every local user. A
    following `os.chmod(path, 0o600)` changes neither.

    So the mode is a POSIX guarantee and nothing more, and both halves are
    asserted here rather than only described. The Windows half turns red the
    day `_write_secret` grows the explicit DACL its "Open: Windows ACLs" note
    weighs, because an entry granted that way is not inherited and carries no
    `(I)`. That red was produced rather than reasoned about, on PKGAME-LAPTOP
    on 2026-09-01: `icacls <file> /inheritance:r /grant:r <user>:(F)` over a
    file written exactly as this stage writes it left one entry,
    `PKGAME-LAPTOP\\perzi:(F)`, which this assertion rejects. It is recorded
    because Linux CI skips this branch and can never show it.

    The CONSTANT is asserted as well as the file, because on Windows it is the
    only thing that can be: the bytes on disk read `0o666` whatever the writer
    asked for, so a widened `SECRET_FILE_MODE` is invisible there. That half is
    an inspection rather than an observation, and it is here so the widening
    cannot land green on a Windows machine and be caught only by Linux CI.
    """
    from yulon.catalog.families import cmangos

    assert cmangos.SECRET_FILE_MODE == 0o600
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    list(engine(Recorder())._db_password(context(server_dir)))
    assert ENTRY.install.password.file is not None
    secret = server_dir / ENTRY.install.password.file
    mode = secret.stat().st_mode & 0o777
    if os.name == "nt":
        assert mode == 0o666, "Windows started honouring the mode: re-read _write_secret's note"
        aces = file_aces(secret)
        assert aces, f"icacls listed no entry for {secret}; the parse, not the ACL, is wrong"
        assert all("(I)" in ace for ace in aces), aces
    else:
        assert mode == cmangos.SECRET_FILE_MODE


def test_db_password_asks_about_the_volume_name_compose_gives_this_install(
    tmp_path: Path,
) -> None:
    """One name, and it is `<project>_db-data` — the string `docker volume ls` prints."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    asked: list[str] = []

    def watch(name: str) -> bool:
        asked.append(name)
        return False

    list(engine(Recorder(), volume_exists=watch)._db_password(context(server_dir)))
    assert asked == [db_volume(server_dir)]


def test_db_password_refuses_when_the_file_is_gone_but_the_volume_exists(tmp_path: Path) -> None:
    """The volume was initialised with the password the file held; a new one locks us out."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    rec.volumes.add(db_volume(server_dir))
    with pytest.raises(InstallerError) as refusal:
        list(engine(rec)._db_password(context(server_dir)))
    assert ".db_password" in str(refusal.value)
    assert db_volume(server_dir) in str(refusal.value)
    assert not (server_dir / ".db_password").exists()


VOLUME_DELETING_PAIRS = (("volume", "rm"), ("volume", "prune"))
"""Consecutive argv words that delete a named volume, whatever surrounds them."""


def volume_deleting_spellings(source: str) -> list[tuple[str, str]]:
    """Every place `source` spells a docker command that would delete a named volume.

    Two forms, tagged `"argv"` and `"text"`, because one module's argument list
    is another's shell line: a list or tuple of string constants running
    `volume rm`/`volume prune`, or a `down` carrying `-v`/`--volumes`; and any
    string constant containing those same words separated by single spaces.

    Both forms are searched because either alone is blind to the other —
    `["volume", "rm", name]` and `"docker volume rm"` are the same action, and
    a text search sees only the second. `-v` counts only beside `down`: on its
    own it is a bind mount, which every `docker run` in this app passes.

    Syntax, not behaviour: this reads source and cannot say whether the line
    runs. That is the point — it is meant to fire while the action is being
    written, before anyone wires it to a button.
    """
    found: list[tuple[str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.List | ast.Tuple):
            words = [
                el.value
                for el in node.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            ]
            pairs = list(zip(words, words[1:], strict=False))
            if any(pair in pairs for pair in VOLUME_DELETING_PAIRS) or (
                "down" in words and ("-v" in words or "--volumes" in words)
            ):
                found.append(("argv", " ".join(words)))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            said = " ".join(node.value.split()).lower()
            if any(
                phrase in said
                for phrase in ("volume rm", "volume prune", "down -v", "down --volumes")
            ):
                found.append(("text", " ".join(node.value.split())[:100]))
    return found


def app_modules() -> list[Path]:
    """Every `.py` file the shipped `yulon` package contains."""
    return sorted(Path(yulon.__file__).parent.rglob("*.py"))


def test_the_live_volume_refusal_names_a_way_to_delete_the_volume_the_server_tab_will_not(
    tmp_path: Path,
) -> None:
    """The refusal may not send the user to a button that keeps volumes by design.

    `controller_view`'s removal action is "Stop and remove containers…", and
    `docker.remove_staged()` passes no `-v` on purpose
    (`test_remove_staged_never_passes_a_flag_that_would_delete_a_volume`); its
    own armed warning says "the database lives in a Docker volume, which is
    kept". A refusal naming that button would send the user round a loop
    ending at this same message, so it names the command that does the job
    instead.

    The tripwire is the SCAN, not the wording check under it. Every module the
    `yulon` package ships is read for a docker command that deletes a named
    volume — in argv form anywhere, and in prose everywhere but this family's
    own file, where the refusal and the note above it say the words on
    purpose. It goes red the day any part of the app grows such an action, the
    Server tab included, at which point this refusal should point at it rather
    than at a terminal.

    The wording check is kept because it says which sentence the loop would
    have ended at, but it is not what fails:
    `test_the_scan_sees_a_volume_deleting_action_the_wording_check_is_blind_to`
    is the reproduction of it passing with the action present.
    """
    from yulon.ui import controller_view

    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    rec.volumes.add(db_volume(server_dir))
    with pytest.raises(InstallerError) as refusal:
        list(engine(rec)._db_password(context(server_dir)))
    said = str(refusal.value)
    assert f"docker volume rm {db_volume(server_dir)}" in said
    assert "Server tab" not in said
    assert "volume, which is kept" in inspect.getsource(controller_view)

    own_file = Path(inspect.getsourcefile(CmangosInstaller) or "").resolve()
    offenders = [
        f"{path.name}: {kind} {spelling}"
        for path in app_modules()
        for kind, spelling in volume_deleting_spellings(path.read_text(encoding="utf-8"))
        if not (kind == "text" and path.resolve() == own_file)
    ]
    assert offenders == [], offenders


A_NEW_SERVER_TAB_ACTION = '''

DELETE_VOLUME_IDLE = "Delete the database and start over…"


def delete_the_database_volume(name: str) -> None:
    """Exactly the action this refusal's premise says the app does not have."""
    docker._run(["volume", "rm", name])
'''
"""A plausible volume-deleting action, appended to `controller_view`'s real source.

It breaks one rule and one only: it spells a docker argv that deletes a named
volume. Its constant, its name and its docstring are the ordinary ones such an
action would carry, so nothing but the argv can be what a scan reacts to.
"""


def test_the_scan_sees_a_volume_deleting_action_the_wording_check_is_blind_to() -> None:
    """Why the wording check is not the tripwire, reproduced rather than argued.

    Run 2026-09-01 (K.3 review, and again here): with a real volume-deleting
    action present in `controller_view`, `"volume, which is kept" in source`
    is still true, because that substring belongs to the REMOVE_IDLE warning
    and an ADDED action removes nothing. A test whose name promised to go red
    the day the Server tab grew such an action was therefore green with the
    action sitting in the file. Only the scan reacts to it.
    """
    from yulon.ui import controller_view

    clean = inspect.getsource(controller_view)
    grown = clean + A_NEW_SERVER_TAB_ACTION

    assert volume_deleting_spellings(clean) == []
    # The old assertion, applied to the grown module: green, and it should not be.
    assert "volume, which is kept" in grown
    assert [kind for kind, _ in volume_deleting_spellings(grown)] == ["argv"]


def test_db_password_refuses_when_docker_will_not_say_whether_the_volume_exists(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()

    def unanswerable(name: str) -> bool:
        raise docker.DockerCommandError("Cannot connect to the Docker daemon")

    with pytest.raises(InstallerError, match="cannot prove") as refusal:
        list(engine(Recorder(), volume_exists=unanswerable)._db_password(context(server_dir)))
    assert "Cannot connect to the Docker daemon" in str(refusal.value)
    assert not (server_dir / ".db_password").exists()


def test_db_password_refuses_when_there_is_no_docker_cli_at_all(tmp_path: Path) -> None:
    """`DockerCliMissingError` subclasses `DockerCommandError` and lands in the same branch.

    Deliberate. Preflight already refuses a machine with no Docker, so reaching
    here means Docker went away mid-install; the outcome is the one that
    matters — nothing is written — and the sentence carries
    `DOCKER_CLI_MISSING_HELP` verbatim, which is the actionable half. A second
    branch would buy a second wording for an identical outcome and would drop
    the volume name from it.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()

    def no_cli(name: str) -> bool:
        raise docker.DockerCliMissingError(platform.DOCKER_CLI_MISSING_HELP)

    with pytest.raises(InstallerError) as refusal:
        list(engine(Recorder(), volume_exists=no_cli)._db_password(context(server_dir)))
    assert platform.DOCKER_CLI_MISSING_HELP in str(refusal.value)
    assert db_volume(server_dir) in str(refusal.value)
    assert not (server_dir / ".db_password").exists()


def test_db_password_writes_nothing_for_a_fixed_password(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    entry = entry_with_password(PasswordPlan(mode="fixed", value="password"))
    eng = engine_for(entry, Recorder(), volume_exists=refuse_to_answer)
    said = list(eng._db_password(context(server_dir)))
    assert list(server_dir.iterdir()) == []
    assert any("fixed" in line for line in said), said


def test_db_password_calls_a_generated_plan_with_no_file_a_catalog_error(tmp_path: Path) -> None:
    """Not "this server uses a fixed password" — that sends the user looking in the wrong place.

    Two fences already stand in front of this one: `PasswordPlan`'s validator
    refuses the shape, and `resolve_secrets()` refuses it again before stage 1.
    `model_construct` is what gets past them, and the branch is kept because
    `plan.file` is `str | None` and mypy is owed a narrowing. What it must not
    do is name the wrong defect on the way past.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    broken = PasswordPlan.model_construct(mode="generated", value=None, file=None, prefix="tbc-")
    eng = engine_for(entry_with_password(broken), Recorder(), volume_exists=refuse_to_answer)
    with pytest.raises(InstallerError) as refusal:
        list(eng._db_password(context(server_dir)))
    said = str(refusal.value)
    assert "catalog" in said
    assert "fixed" not in said
    assert list(server_dir.iterdir()) == []


def test_db_password_is_a_stage_that_is_never_recorded_and_follows_clone_sources() -> None:
    """Never recorded because the FILE is the evidence; a state file must not claim a secret."""
    stages = engine(Recorder()).stages()
    names = [stage.name for stage in stages]
    assert names.index("db-password") == names.index("clone-sources") + 1
    stage = next(s for s in stages if s.name == "db-password")
    assert stage.recorded is False


# -- write-dockerfile -------------------------------------------------------


def rooted(root: Path, rec: Recorder, entry: CatalogEntry | None = None) -> CmangosInstaller:
    """An engine reading its templates from `root` instead of the shipped installers tree.

    `engine()` pins `resources.installers_dir()`, which is what most of this
    file wants; the write-dockerfile tests that plant a template need the other
    root, and one that is writable.
    """
    return CmangosInstaller(
        entry if entry is not None else ENTRY,
        installers_root=root,
        seams=rec.seams(platform_id=lambda: "linux"),
    )


def plant_templates(root: Path, body: str, ignore: str = "*\n") -> Path:
    """Lay a marked `Dockerfile.tmpl`/`dockerignore.tmpl` pair where `wow-tbc`'s block points."""
    assert ENTRY.install.native is not None
    folder = root / str(ENTRY.install.native.dockerfile_dir)
    folder.mkdir(parents=True)
    marker = f"{composegen.GENERATED_MARKER} - do not hand-edit.\n"
    (folder / "Dockerfile.tmpl").write_text(marker + body, encoding="utf-8", newline="\n")
    (folder / "dockerignore.tmpl").write_text(marker + ignore, encoding="utf-8", newline="\n")
    return folder


def test_write_dockerfile_renders_the_marked_pair_from_the_entry_template(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    said = list(engine(Recorder())._write_dockerfile(context(server_dir)))
    built = (server_dir / "Dockerfile").read_text(encoding="utf-8")
    ignore = (server_dir / ".dockerignore").read_text(encoding="utf-8")
    assert built.startswith(composegen.GENERATED_MARKER)
    assert ignore.startswith(composegen.GENERATED_MARKER)
    assert "{{" not in built and "{{" not in ignore
    assert CMANGOS is not None
    assert f"-j{CMANGOS.dockerfile.make_jobs}" in built
    assert f"CMAKE_INSTALL_PREFIX={PurePosixPath(CMANGOS.conf.source_dir).parent}" in built
    assert ".git" in ignore
    assert said == ["Wrote Dockerfile", "Wrote .dockerignore"]


def test_write_dockerfile_hands_the_renderer_the_public_mapping_and_nothing_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 7.3 contract change (A6), asserted as behaviour rather than defended in a docstring.

    A6 specified ONE mapping for the Dockerfile, the conf tables, the SQL and
    verify alike, and K.4 handed it over whole with `DB_PASSWORD` in it. 7.3
    splits it by capability: this stage gets `_public_tokens()`, and the
    equality below is what says so — not "a mapping without `DB_PASSWORD`",
    which would still be satisfied by a mapping carrying a secret under some
    other name.

    The sentinel test above is the property version; this one pins the
    identity, so a stage that built its own nearly-right mapping instead of
    asking for the public one is caught here even if that mapping happens to
    be secret-free today.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    seen: list[dict[str, str]] = []
    real = dockerfile.render

    def spy(template_dir: Path, tokens: Mapping[str, str]) -> tuple[str, str]:
        seen.append(dict(tokens))
        return real(template_dir, tokens)

    monkeypatch.setattr(dockerfile, "render", spy)
    eng = engine(Recorder())
    ctx = context(server_dir)
    list(eng._write_dockerfile(ctx))
    assert seen == [eng._public_tokens(server_dir)], "the public mapping itself, not a near copy"
    assert "DB_PASSWORD" not in seen[0]
    for name in ("Dockerfile", ".dockerignore"):
        assert DB_PASSWORD not in (server_dir / name).read_text(encoding="utf-8")


def test_write_dockerfile_reports_a_template_that_names_the_secret_without_quoting_it(
    tmp_path: Path,
) -> None:
    """A planted template spelling `{{DB_PASSWORD}}` reaches the user as one sentence.

    The reviewer's bypass (`shared/cmangos/`) proved a location guard is not the
    protection; this is the same template through THIS stage, so the refusal is
    shown to survive the trip out to `run()`'s `InstallerError` — and to arrive
    without the password in it, since that string is what a failure dialog
    invites the user to paste into a bug report.
    """
    root = tmp_path / "installers"
    plant_templates(root, "FROM debian:12\nENV DB_PASSWORD={{DB_PASSWORD}}\n")
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    with pytest.raises(InstallerError) as refusal:
        list(rooted(root, Recorder())._write_dockerfile(context(server_dir)))
    said = str(refusal.value)
    assert "DB_PASSWORD" in said and "docker history" in said
    assert DB_PASSWORD not in said
    assert list(server_dir.iterdir()) == [], "nothing written on a refusal"


def test_write_dockerfile_skips_an_identical_rerun_that_no_state_file_recorded(
    tmp_path: Path,
) -> None:
    """The skip comes from the CONTENT on disk, not from the record — proved by withholding it.

    `dockerfile.write()` compares the text it is about to write with the text
    already there and returns only the paths it actually wrote; this stage
    consults `ctx.state` for nothing at all. So the re-run is given a context
    with an EMPTY `completed`, and it still skips: a test that passed
    `completed=["write-dockerfile"]` here would read as evidence of a
    state-driven skip that does not exist.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    eng = engine(Recorder())
    list(eng._write_dockerfile(context(server_dir)))
    before = {
        name: (server_dir / name).stat().st_mtime_ns for name in ("Dockerfile", ".dockerignore")
    }
    ctx = context(server_dir)
    assert ctx.state.completed == ()
    again = list(eng._write_dockerfile(ctx))
    assert {
        name: (server_dir / name).stat().st_mtime_ns for name in ("Dockerfile", ".dockerignore")
    } == before
    assert again == [
        "Dockerfile is already exactly what this install needs.",
        ".dockerignore is already exactly what this install needs.",
    ]


def test_write_dockerfile_says_what_happened_to_both_files_when_only_one_changed(
    tmp_path: Path,
) -> None:
    """Four combinations, two files: no line may be true of the pair while naming one.

    The stage speaks about each file by name every time, rather than collapsing
    "nothing changed" into one sentence the way `stage_generate_compose` does
    over three compose files. With two files the collapsed line is either a
    claim about the Dockerfile that is really about both, or silence about the
    file that did not move — and this test is the case that shows the
    difference: the `.dockerignore` is untouched here, and a reader who was
    told only "Wrote Dockerfile" would have no way to know that.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    eng = engine(Recorder())
    list(eng._write_dockerfile(context(server_dir)))
    marker = (server_dir / "Dockerfile").read_text(encoding="utf-8").splitlines()[0]
    (server_dir / "Dockerfile").write_text(f"{marker}\nFROM scratch\n", encoding="utf-8")
    ignore_before = (server_dir / ".dockerignore").stat().st_mtime_ns
    said = list(eng._write_dockerfile(context(server_dir)))
    assert said == [
        "Wrote Dockerfile",
        ".dockerignore is already exactly what this install needs.",
    ]
    assert (server_dir / ".dockerignore").stat().st_mtime_ns == ignore_before


def test_write_dockerfile_refuses_a_file_it_did_not_write_and_leaves_it_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    """One rule broken: the file in the way carries no generated-file marker."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    (server_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    with pytest.raises(InstallerError, match="Dockerfile") as refusal:
        list(engine(Recorder())._write_dockerfile(context(server_dir)))
    assert "not written by Yu'lon" in str(refusal.value)
    assert (server_dir / "Dockerfile").read_text(encoding="utf-8") == "FROM scratch\n"
    assert not (server_dir / ".dockerignore").exists(), "the pair is judged before either is laid"


def test_write_dockerfile_calls_an_entry_with_no_dockerfile_dir_a_catalog_error(
    tmp_path: Path,
) -> None:
    """`dockerfile_dir` is an unvalidated `str | None`; `wow-wotlk` really ships `None`."""
    assert ENTRY.install.native is not None
    blank = ENTRY.model_copy(
        update={
            "install": ENTRY.install.model_copy(
                update={"native": ENTRY.install.native.model_copy(update={"dockerfile_dir": None})}
            )
        }
    )
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    with pytest.raises(InstallerError, match="dockerfile_dir") as refusal:
        list(rooted(tmp_path, Recorder(), blank)._write_dockerfile(context(server_dir)))
    assert "catalog error in the app" in str(refusal.value)
    assert list(server_dir.iterdir()) == []


def test_write_dockerfile_passes_the_modules_sentence_through_and_names_the_class_of_anything_else(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two arms, two conventions, both deliberate.

    `DockerfileError` already carries the sentence a user reads, so it is
    passed through unchanged — `stage_generate_compose` treats
    `ComposeGenError` the same way. The broad arm is the one that cannot know
    what it caught, and there `str(exc)` alone is what J.4 rejected next door
    (`sqlplan._read_failure`): a bare `OSError` says `[Errno 13] ...` with no
    word for WHICH failure it was, so the class is named and the sentence says
    what was being attempted. The arm is defence in depth — `dockerfile.py`
    wraps every `OSError` it can raise today — and it exists because `run()`
    catches `InstallerError` and nothing else, so an escape is a traceback in
    the user's face rather than a dialog.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    eng = engine(Recorder())

    def refuse(*args: object, **kwargs: object) -> tuple[Path, ...]:
        raise dockerfile.DockerfileError("that file is not ours to replace")

    monkeypatch.setattr(dockerfile, "write", refuse)
    with pytest.raises(InstallerError) as passed_through:
        list(eng._write_dockerfile(context(server_dir)))
    assert str(passed_through.value) == "that file is not ours to replace"

    def blow_up(*args: object, **kwargs: object) -> tuple[Path, ...]:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(dockerfile, "write", blow_up)
    with pytest.raises(InstallerError) as wrapped:
        list(eng._write_dockerfile(context(server_dir)))
    assert "OSError" in str(wrapped.value)
    assert "No space left on device" in str(wrapped.value)
    assert ENTRY.name in str(wrapped.value)


def test_write_dockerfile_is_recorded_and_sits_between_db_password_and_generate_compose() -> None:
    """Recorded, though the record is not what skips it: the pinned order is the contract."""
    stages = engine(Recorder()).stages()
    names = [stage.name for stage in stages]
    assert names.index("db-password") + 1 == names.index("write-dockerfile")
    assert names.index("write-dockerfile") + 1 == names.index("generate-compose")
    stage = next(s for s in stages if s.name == "write-dockerfile")
    assert stage.recorded is True
    assert stage.cancel_note == "", "only `build` costs anything to stop (A4)"


# -- extract + mmaps ----------------------------------------------------------


def _expected_user_args() -> tuple[str, ...]:
    """What `platform.container_user_args()` answers for the Linux engine `engine()` builds.

    Asked of `platform.py` rather than spelled out, because the uid:gid policy
    is `test_platform.py`'s to prove and the question here is only whether the
    engine's answer reaches the container spec. Not a tautology: the seam says
    `linux`, so on a Linux runner this is a real `--user uid:gid` pair and an
    engine that handed `run_plan` nothing would be caught. On a Windows runner
    both sides are `()` and the assertion buys nothing there — which is why
    `test_user_args_ask_platform_py_through_the_seam_and_not_the_real_host` is
    the test that proves the policy, with faked ids.
    """
    return tuple(platform.container_user_args(platform_id=lambda: "linux"))


def _mmaps_runs(rec: Recorder) -> list[docker.ContainerRun]:
    """Every recorded run whose argv is the mmaps plan's — the generator, never a tool."""
    assert CMANGOS is not None
    return [run for run in rec.container_runs if run.argv == CMANGOS.mmaps.argv]


def _every_string_in(value: object) -> Iterator[str]:
    """Every string reachable from one `ContainerRun` field, however it is nested.

    Written to be exhaustive by CONSTRUCTION rather than by a list somebody
    keeps current: a dataclass is walked field by field (so `Mount.host` is
    reached inside `mounts`), a mapping gives up its keys as well as its values
    (so an `-e CLIENT=…` name is not a blind spot), any other iterable is
    walked, and anything else is rendered with `str()` rather than skipped.
    Nothing returns early on a type it does not recognise, which is the only way
    a field added after this was written is still audited.
    """
    if isinstance(value, str):
        yield value
    elif isinstance(value, Path):
        yield str(value)
    elif is_dataclass(value) and not isinstance(value, type):
        for member in fields(value):
            yield from _every_string_in(getattr(value, member.name))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            yield from _every_string_in(key)
            yield from _every_string_in(item)
    elif isinstance(value, Iterable):
        for item in value:
            yield from _every_string_in(item)
    else:
        yield str(value)


def _spoken_by_field(run: docker.ContainerRun) -> dict[str, tuple[str, ...]]:
    """What each of `ContainerRun`'s fields says, keyed by field name.

    `dataclasses.fields()` is the enumeration; the field NAMES are only carried
    so a failure says which field leaked, never to choose which ones to look at.
    """
    return {
        member.name: tuple(_every_string_in(getattr(run, member.name))) for member in fields(run)
    }


def test_extract_runs_every_tool_read_only_as_the_user_in_out(tmp_path: Path) -> None:
    """Audit by field: client `:ro` at /client, data rw at /out, cwd /out, `--user` on Linux."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = client_folder(tmp_path)
    rec = Recorder()
    eng = engine(rec)
    said = list(eng._extract(context(server_dir, client)))
    assert CMANGOS is not None
    tools = CMANGOS.extract.tools
    assert [run.argv for run in rec.container_runs] == [tool.argv for tool in tools]
    for run in rec.container_runs:
        mounts = {m.guest: m for m in run.mounts}
        assert mounts["/client"].host == client and mounts["/client"].read_only is True
        assert mounts["/out"].host == server_dir / "data" and mounts["/out"].read_only is False
        assert run.workdir == "/out"
        assert run.user_args == _expected_user_args()
        assert run.image == eng._image_ref(context(server_dir), CMANGOS.extract.image)
    evidence = extract.read_evidence(server_dir / "data")
    assert evidence is not None
    assert [record.name for record in evidence.tools] == [tool.name for tool in tools]
    assert evidence.client_path == str(client.resolve())
    assert evidence.required_file_size is not None, "the required file's size is the resume rule"
    assert any("Extraction finished" in line for line in said)


def test_extract_needs_the_client_folder(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    with pytest.raises(InstallerError, match="client"):
        list(engine(rec)._extract(context(server_dir, None)))
    assert rec.container_runs == [], "nothing was run against a client that was never given"


def test_extract_second_run_skips_finished_tools_and_redoes_only_the_lost_one(
    tmp_path: Path,
) -> None:
    """The data/ folders plus the completion records drive the gate — not the state file."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = client_folder(tmp_path)
    rec = Recorder()
    eng = engine(rec)
    list(eng._extract(context(server_dir, client)))
    first = len(rec.container_runs)
    assert first == 3, "a skip is only observable once something has actually run"
    list(eng._extract(context(server_dir, client, completed=["extract"])))
    assert len(rec.container_runs) == first, "every tool had a record and its counts; none re-ran"
    assert CMANGOS is not None
    lost = next(iter(CMANGOS.extract.tools[0].produces))
    for path in (server_dir / "data" / lost).iterdir():
        path.unlink()
    list(eng._extract(context(server_dir, client, completed=["extract"])))
    reran = rec.container_runs[first:]
    assert [run.argv for run in reran] == [CMANGOS.extract.tools[0].argv]


def test_extract_refuses_a_tool_that_exits_zero_with_a_shortfall(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = client_folder(tmp_path)
    rec = Recorder()
    rec.produce = {name: 3 for name in rec.produce}
    with pytest.raises(InstallerError) as refusal:
        list(engine(rec)._extract(context(server_dir, client)))
    assert str(ENTRY.client.build) in str(refusal.value), "the refusal names the client build"
    assert len(rec.container_runs) == 1, "the first tool fell short; nothing after it ran"
    evidence = extract.read_evidence(server_dir / "data")
    assert evidence is None or evidence.tools == (), "a record for a failed tool is never written"


def test_extract_refuses_a_failed_tool_naming_it(tmp_path: Path) -> None:
    """Named for the TOOL's name, so that is what is asserted — not the fixture's own echo.

    Until 2026-09-01 the match was `expansion.MPQ`, which is the tail this test
    itself put into `run_result` and which comes back in the refusal's "last
    words". That is the fixture answering itself: the refusal could stop naming
    the tool entirely and this test would still be green. The name comes off
    the plan rather than being typed, so a renamed tool cannot leave a literal
    behind that nothing produces.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    assert CMANGOS is not None
    failed = CMANGOS.extract.tools[0]
    rec = Recorder()
    rec.run_result = docker.AttachedRun(2, ("ad: cannot open /client/Data/expansion.MPQ",))
    with pytest.raises(InstallerError) as refusal:
        list(engine(rec)._extract(context(server_dir, client_folder(tmp_path))))
    message = str(refusal.value)
    assert failed.name in message, f"the refusal does not name the tool that failed: {message}"
    assert "expansion.MPQ" in message, "the tool's own last words are quoted too"
    assert len(rec.container_runs) == 1, "the first tool failed; nothing after it ran"


def test_mmaps_runs_the_generator_over_data_and_records_it(tmp_path: Path) -> None:
    """mmaps needs extraction evidence first (`run_mmaps` refuses without it), so extract runs."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = client_folder(tmp_path)
    rec = Recorder()
    eng = engine(rec)
    list(eng._extract(context(server_dir, client)))
    extracted = len(rec.container_runs)
    said = list(eng._mmaps(context(server_dir)))
    assert CMANGOS is not None
    mmaps_runs = rec.container_runs[extracted:]
    assert [run.argv for run in mmaps_runs] == [CMANGOS.mmaps.argv]
    run = mmaps_runs[0]
    assert {m.guest: m.host for m in run.mounts} == {"/out": server_dir / "data"}
    assert run.workdir == "/out"
    assert run.user_args == _expected_user_args()
    assert run.image == eng._image_ref(context(server_dir), CMANGOS.extract.image)
    assert len(list((server_dir / "data" / "mmaps").iterdir())) >= CMANGOS.mmaps.min_files
    assert any("Map generation finished" in line for line in said)
    list(eng._mmaps(context(server_dir, completed=["mmaps"])))
    assert len(rec.container_runs) == extracted + 1, "an evidenced mmaps run is not repeated"


def test_mmaps_refuses_before_any_extraction(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    (server_dir / "data").mkdir(parents=True)
    rec = Recorder()
    with pytest.raises(InstallerError, match="no extraction evidence"):
        list(engine(rec)._mmaps(context(server_dir)))
    assert rec.container_runs == []


def test_no_mmaps_container_is_handed_anything_that_names_the_users_client(tmp_path: Path) -> None:
    """`run_mmaps` deletes `data/mmaps`; this is the call site that keeps the client away from it.

    Three legs held the guarantee inside `extract.py` before anything called
    it: `run_mmaps`'s signature takes no client path, `MMAPS_DIR` is a single
    relative component, and `MmapPlan` carries no folder field. This engine is
    the first caller, and the leg it owns is the one that cannot be seen by
    reading the call — `data_dir=ctx.server_dir / DATA_DIR` and
    `data_dir=ctx.client_dir` are the same shape on the page, and the second
    would hand `shutil.rmtree` a folder inside somebody's game install.

    So the spec is audited by field rather than by eye — and the fields are
    ENUMERATED off `ContainerRun` rather than listed here. Until 2026-09-01
    they were listed: argv, the env VALUES, workdir, image, plus the mounts,
    five of the eight. This mutant, in `_mmaps`, passed that audit:

        user_args = (*self._user_args(), "-v", f"{ctx.client_dir}:/client:ro")

    It hands the mmaps container the user's client as a real bind. Measured
    that day on `yulon-ubuntu`: run alone, this test reported `1 passed`; the
    file went red only at `test_mmaps_runs_the_generator_over_data_and_records_it`,
    which pins `run.user_args` for its own unrelated reason. A guarantee a
    neighbour happens to hold is not held by the test named for it, so the
    walk now covers `user_args`, `ulimits`, `security_args` and env KEYS, and a
    ninth field on the day it is added.

    The `ctx` handed to `_mmaps` here DOES carry a client dir, deliberately — a
    body that reached for `ctx.client_dir` would find one rather than a `None`
    that fails for a different reason — and the client sits beside the server
    dir under `tmp_path`, so neither is an ancestor of the other.

    Both spellings of the client path are looked for, because a body that said
    `ctx.client_dir.resolve()` would otherwise slip through wherever `tmp_path`
    is reached by a link.

    An `is_relative_to(client)` leg over the mounts stood here until
    2026-09-01 and was REMOVED rather than repaired. It was lexical, so a `..`
    component or a symlinked `data/` walked past it; and made non-lexical with
    `.resolve()` it still could not fail under this fixture, because the exact
    mount equality on the line above already settles the `mounts` field and
    nothing here builds a link. It was catching nothing either way, which is
    worse than absent — a line that reads as a guard is counted as one. The
    resolved question is asked where a fixture actually builds the link:
    `test_a_data_folder_that_leads_out_of_the_install_is_refused_before_anything_runs`.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = client_folder(tmp_path)
    rec = Recorder()
    eng = engine(rec)
    list(eng._extract(context(server_dir, client)))
    list(eng._mmaps(context(server_dir, client)))
    runs = _mmaps_runs(rec)
    assert len(runs) == 1
    for run in runs:
        assert [(m.host, m.guest) for m in run.mounts] == [(server_dir / "data", "/out")]
        spoken = _spoken_by_field(run)
        seen = {text for texts in spoken.values() for text in texts}
        assert (
            str(server_dir / "data") in seen and run.image in seen
        ), "the field walk found neither the data mount nor the image, so it audited nothing"
        for field_name, texts in spoken.items():
            for text in texts:
                for spelling in (str(client), str(client.resolve())):
                    assert spelling not in text, f"{field_name} names the client: {text!r}"


def test_a_data_folder_that_leads_out_of_the_install_is_refused_before_anything_runs(
    tmp_path: Path,
) -> None:
    """`data/` may be a link, and a link is where `rmtree` goes. Both stages, both attempts.

    The fixture violates exactly one rule: `data/` under the server directory
    resolves outside it. Everything else is a valid install — a real server
    folder, a client that passes the TBC `ClientSpec`, a seam that answers
    `linux` — so a refusal here can only be about the link.

    What it cost while nothing refused, measured on this branch on 2026-09-01
    before `_data_dir()` existed: `mkdir(parents=True, exist_ok=True)` returns
    happily on a pre-existing symlink, `run_mmaps()` then hands
    `shutil.rmtree` `data/mmaps` down the link, and the client's own folder was
    gone afterwards. The sibling shape — `data/mmaps` itself being the link —
    survives, because `shutil` refuses with "Cannot call rmtree on a symbolic
    link"; that is `shutil`'s rule about its own last component and it says
    nothing about the parent.

    Asked TWICE, because a refusal that quietly repairs what it refuses would
    answer differently the second time: both calls must refuse the same way and
    the client must still be whole after both.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = client_folder(tmp_path)
    kept = client / "Data" / "common.MPQ"
    data = server_dir / "data"
    try:
        data.symlink_to(client, target_is_directory=True)
    except (OSError, NotImplementedError):  # pragma: no cover - needs privilege on Windows
        pytest.skip("cannot create a directory symlink on this machine")
    if data.resolve() == data:  # pragma: no cover - resolution disabled
        pytest.skip("symlinks are not resolved on this filesystem")
    rec = Recorder()
    eng = engine(rec)
    for attempt in (1, 2):
        for stage in (eng._extract, eng._mmaps):
            with pytest.raises(InstallerError) as refusal:
                list(stage(context(server_dir, client)))
            said = str(refusal.value)
            assert str(data) in said, f"attempt {attempt}: the refusal does not name the link"
            assert (
                str(client.resolve()) in said
            ), f"attempt {attempt}: the refusal does not say where the link leads"
        assert rec.container_runs == [], f"attempt {attempt}: a container ran over a linked data/"
        assert kept.is_file(), f"attempt {attempt}: the client lost content to a refused install"


def test_extract_and_mmaps_carry_the_stage_kinds_own_cancel_notes() -> None:
    notes = {s.name: s.cancel_note for s in engine(Recorder()).stages()}
    assert notes["extract"] == extract.EXTRACT_CANCEL_NOTE
    assert notes["mmaps"] == extract.MMAPS_CANCEL_NOTE
    assert "only the tool that was interrupted" in notes["extract"]
    assert "restarts from the beginning" in notes["mmaps"]


def test_the_selinux_answer_reaches_every_extraction_container_and_no_mmaps_one(
    tmp_path: Path,
) -> None:
    """`label:disable` where the client is mounted; never on the one container without one.

    Two facts in one test because they are one decision taken twice, in
    opposite directions, and neither is safe to read off the other:

    * The extraction containers hold the USER's client, which lives outside the
      server directory and which no `chcon` of ours ever reaches; on an
      enforcing box a confined container is denied it outright
      (`yulon-fedora-gate`, Fedora 44, Docker 29.7.2, 2026-09-01). They get the
      flag, and the ANSWER comes from the seam. **Not because `run_plan`'s
      parameter is import-bound** — this docstring said so until 2026-09-01 and
      it was false, twice over: that default is `None` and the module attribute
      is looked up inside the call (`extract.py:755`), so a `monkeypatch` of
      `platform.selinux_enforcing` would be seen either way. What the two
      engines below prove is the thing that IS true: the two host shapes differ
      only in what the SEAM answers, so an engine that asked the module instead
      would give both of them the runner's own answer and the two would agree
      instead of differing. `_extract`'s docstring carries the interpreter
      output that settled it.
    * The mmaps container binds `data/` under the server directory and nothing
      else, and `stage_generate_compose` has already relabelled that directory,
      so it is readable and writable while confined. The flag would turn a
      container's confinement off to buy nothing at all, and `run_mmaps` is
      right to omit it. What transfers from the extract stage is the evidence,
      not the decision.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    client = client_folder(tmp_path)
    disable = ("--security-opt", "label:disable")

    fedora = Recorder()
    eng = engine(fedora, selinux_enforcing=lambda: True)
    list(eng._extract(context(server_dir, client)))
    tools = list(fedora.container_runs)
    list(eng._mmaps(context(server_dir)))
    assert tools, "no extraction container ran, so this proves nothing about either half"
    for run in tools:
        assert run.security_args == (*extract.EXTRACT_HARDENING, *disable)
    for run in _mmaps_runs(fedora):
        assert run.security_args == extract.EXTRACT_HARDENING
        assert "label:disable" not in run.security_args

    ubuntu = Recorder()
    other = tmp_path / "srv2"
    other.mkdir()
    list(engine(ubuntu, selinux_enforcing=lambda: False)._extract(context(other, client)))
    for run in ubuntu.container_runs:
        assert run.security_args == extract.EXTRACT_HARDENING


def test_the_relabel_that_lets_mmaps_run_confined_happens_before_the_first_extraction(
    tmp_path: Path,
) -> None:
    """A guard whose correctness is its POSITION in a sequence; a reorder must fail here.

    `run_mmaps` carries no `label:disable` because its one bind, `data/` under
    the server directory, inherits `container_file_t` from the relabel
    `stage_generate_compose` does. That relabel was confirmed on
    `yulon-fedora-gate` (Fedora 44, Enforcing, Docker 29.7.2, 2026-09-01); what
    was NOT confirmed until these two stages were bound is that it happens
    FIRST, because until then there was no pipeline to ask.

    So both halves are asserted, and the live one is the point: a reorder of
    `stages()` that looked like housekeeping would move a confined container in
    front of the `chcon` that makes its bind readable, and the symptom would be
    a Fedora install that fails hours in with a permission error. It fails here
    instead.
    """
    rec = Recorder()

    def relabel(path: Path) -> bool:
        rec.calls.append("relabel")
        return rec.relabel(path)

    server_dir = tmp_path / "srv"
    install(
        rec,
        server_dir,
        client_folder(tmp_path),
        selinux_enforcing=lambda: True,
        relabel=relabel,
    )
    assert rec.relabelled == [server_dir], "the Fedora shape did not relabel; nothing was ordered"
    first_container = next(i for i, call in enumerate(rec.calls) if call.startswith("run:"))
    assert rec.calls.index("relabel") < first_container
    names = [stage.name for stage in engine(rec).stages()]
    assert names.index("generate-compose") < names.index("extract") < names.index("mmaps")
    pinned = CmangosInstaller.STAGE_NAMES
    assert pinned.index("generate-compose") < pinned.index("extract") < pinned.index("mmaps")


# -- conf ---------------------------------------------------------------------


def test_conf_copies_dist_files_out_of_the_server_image_once_and_patches_them(
    tmp_path: Path,
) -> None:
    """One round trip to the image, then every key in the table set from `_secret_tokens()`.

    Both streams are asserted against the catalog's OWN table rather than a
    list spelled here, so a file added to `conf.files` has to show up in each
    of them; a stage that copied one file and forgot another says so by name
    instead of by a count.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    eng = engine(rec)
    said = list(eng._conf(context(server_dir)))
    assert CMANGOS is not None
    image_ref = eng._image_ref(context(server_dir), CMANGOS.extract.image)
    # Exactly one copy, of the built server image: "once" is the assertion, and
    # `all(...)` over an empty list would have been true of a stage that copied
    # nothing at all.
    assert [image for image, _src, _dest in rec.copied] == [image_ref]
    assert all(src.startswith(CMANGOS.conf.source_dir) for _image, src, _dest in rec.copied)
    assert [line for line in said if line.startswith("Copied")] == [
        f"Copied {name} out of the server image." for name in CMANGOS.conf.files
    ]
    assert [line for line in said if line.startswith("Patched")] == [
        f"Patched {name}." for name in CMANGOS.conf.files
    ]
    etc = server_dir / "etc"
    mangosd = (etc / "mangosd.conf").read_text(encoding="utf-8")
    assert f'LoginDatabaseInfo = "{ENTRY.containers.db};3306;' in mangosd
    assert DB_PASSWORD in mangosd
    assert f"WorldServerPort = {ENTRY.ports.world}" in mangosd, "the per-install tokens too"
    assert "{{" not in mangosd
    assert "Other = 1" in mangosd, "keys the table does not name are left alone"
    # The password is in the FILE because the emulator reads files. It is in no
    # line this stage yields, and in no conf whose table never asks for it.
    assert not [line for line in said if DB_PASSWORD in line]
    assert DB_PASSWORD not in (etc / "ahbot.conf").read_text(encoding="utf-8")


def test_conf_second_run_never_recopies_and_keeps_the_users_own_edit(tmp_path: Path) -> None:
    """Copy-once is decided by the files on disk, and the copy double is not idempotent.

    A second `copy_from_image` would append to `rec.copied` AND overwrite the
    appended line, so neither half can cover for the other: the double answers
    differently the second time in both records.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    eng = engine(rec)
    list(eng._conf(context(server_dir)))
    copies = len(rec.copied)
    assert copies, "nothing was copied the first time, so a second run proves nothing"
    mangosd = server_dir / "etc" / "mangosd.conf"
    with mangosd.open("a", encoding="utf-8") as fh:
        fh.write("Rate.XP.Kill = 3\n")
    again = list(eng._conf(context(server_dir, completed=["conf"])))
    assert len(rec.copied) == copies, "an existing file is patched in place, never re-copied"
    assert "Rate.XP.Kill = 3" in mangosd.read_text(encoding="utf-8")
    assert not [line for line in again if line.startswith(("Copied", "Patched"))]
    assert any("already" in line for line in again), "every patched key read back equal"


def test_conf_asks_the_files_and_not_the_record_whether_to_copy(tmp_path: Path) -> None:
    """A state file saying `conf` is finished does not stop the first copy.

    The stage is recorded, but the record is not what skips it — the same rule
    `write-dockerfile` is written against. `materialise()` looks at `etc/`, so
    a state file that outlived the files it describes cannot leave an install
    with no confs at all.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    list(engine(rec)._conf(context(server_dir, completed=["conf"])))
    assert rec.copied
    assert (server_dir / "etc" / "mangosd.conf").is_file()


def test_conf_wraps_a_docker_failure_in_the_install_sentence(tmp_path: Path) -> None:
    """Docker's own words kept, inside a sentence saying which step they belong to."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()

    def broken(image: str, src: str, dest: Path) -> None:
        raise docker.DockerCommandError("docker create exited 125: no such image")

    with pytest.raises(InstallerError, match="no such image") as caught:
        list(engine(Recorder(), copy_from_image=broken)._conf(context(server_dir)))
    assert "could not be copied out of the server image" in str(caught.value)


def test_conf_passes_the_modules_own_refusal_through_and_leaves_no_half_built_etc(
    tmp_path: Path,
) -> None:
    """One `.dist` missing from the image: the module's sentence, not a second one round it.

    `InstallerError` subclasses `RuntimeError`, so an `except` broadened round
    `materialise()` would catch this refusal and wrap it inside "the
    configuration files could not be copied…", reading as a broken machine for
    what is the catalog and the image disagreeing.

    The empty `etc/` is `materialise()`'s all-or-nothing rule seen from the
    family, and it is the half that would be invisible later: a file that
    exists is never re-copied, so a partial copy would be sailed straight past
    by the next resume.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    del rec.conf_dist["ahbot.conf.dist"]
    with pytest.raises(InstallerError) as caught:
        list(engine(rec)._conf(context(server_dir)))
    message = str(caught.value)
    assert message.startswith("the built image ")
    assert "ahbot.conf.dist" in message
    assert "could not be copied out of the server image" not in message
    assert not list((server_dir / "etc").glob("*.conf"))


def test_conf_says_which_file_could_not_be_patched_and_does_not_say_it_twice(
    tmp_path: Path,
) -> None:
    """A conf that cannot be READ keeps `apply_table()`'s own sentence, naming the path.

    The one rule this fixture breaks is that `etc/mangosd.conf` is a directory
    rather than a file — it EXISTS, so `materialise()` leaves it alone (a file
    that is there is never re-copied), and `_read()` is what trips. That is the
    only way to reach the re-raise arm without editing the catalog, and without
    it the arm has no test: an `InstallerError` falling through to the broad
    clause below it would be wrapped in "could not be patched", which names the
    directory instead of the file and says the failure twice.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = Recorder()
    eng = engine(rec)
    list(eng._conf(context(server_dir)))
    mangosd = server_dir / "etc" / "mangosd.conf"
    mangosd.unlink()
    mangosd.mkdir()
    copies = len(rec.copied)
    with pytest.raises(InstallerError) as caught:
        list(eng._conf(context(server_dir)))
    message = str(caught.value)
    assert message.startswith(f"{mangosd} could not be read")
    assert "could not be patched" not in message, "the module's sentence was wrapped in a second"
    assert len(rec.copied) == copies, "a file that exists is not re-copied, directory or not"


def test_the_conf_this_stage_leaves_behind_carries_the_mode_the_module_asks_for(
    tmp_path: Path,
) -> None:
    """Owner-only on POSIX through both writers; a measured no-op on Windows (§24).

    `conf.CONF_MODE` and the two writers are `test_conf.py`'s to prove. What is
    asked here is the composition: that the file this STAGE leaves behind went
    through `materialise()` and `apply_table()` rather than being written
    beside them. `mangosd.conf` is copied and then patched on a first run, so
    it has been through both.

    Measured on PKGAME-LAPTOP, Windows 11 26200, CPython 3.13.14, 2026-09-01:
    a file created by `write_text`, moved with `shutil.move` and chmodded
    (`materialise`'s shape) and one written, chmodded and `os.replace`d
    (`_write`'s shape) both read back `st_mode & 0o777 == 0o666`. The mode does
    nothing there and the ACL is whatever the parent folder grants — that is
    `pyplan/bug-checklist.md` §24, open, and not fixed by this task. The
    assertion records what was measured rather than what the mode was for.
    """
    from yulon.catalog.families import conf as conf_kind

    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    list(engine(Recorder())._conf(context(server_dir)))
    mode = (server_dir / "etc" / "mangosd.conf").stat().st_mode & 0o777
    if os.name == "nt":
        assert mode == 0o666, "Windows started honouring the mode: re-read bug-checklist §24"
    else:
        assert mode == conf_kind.CONF_MODE


def test_conf_is_recorded_and_sits_between_mmaps_and_start_db() -> None:
    """Order and bookkeeping, because each is one keyword in `stages()`."""
    stages = engine(Recorder()).stages()
    names = [stage.name for stage in stages]
    at = names.index("mmaps")
    assert names[at + 1 : at + 3] == ["conf", "start-db"]
    conf_stage = next(stage for stage in stages if stage.name == "conf")
    assert conf_stage.recorded
    assert conf_stage.cancel_note == "", "a copy and a patch are seconds; a Stop costs nothing"


# -- import -------------------------------------------------------------------


IMPORTED_OLDER_PLAN = docker.ImportState(
    "imported",
    f"{sqlplan.MARKER_TABLE} holds plan 0000000000000000; this app's plan differs",
    complete=True,
)
"""A finished import recorded by a DIFFERENT plan than the one this app ships.

`MarkerGate` reads any marker row as `imported` whatever its hash, and the hash
lives in `detail`, which the family never parses. The fixture is the
interesting one for that reason: a mismatched hash is a finished import from an
older app, not a reason to import over it.
"""


def server_with_sql(tmp_path: Path) -> Path:
    """A server dir holding every file the shipped plan names, under the source that owns it."""
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    lay = lay_sql(server_dir, SQL)
    for source in ENTRY.emulator.sources:
        dest = server_dir / source.dest
        dest.mkdir(parents=True, exist_ok=True)
        lay(dest)
    return server_dir


def ready_to_import(*answers: docker.ImportState) -> Recorder:
    """A Recorder whose database container is up, answering the probe as told.

    `db_started` is what lets `Recorder.probe()` give any answer but
    `unreadable` — the real probe reaches the databases through `docker exec`,
    so with nothing running it cannot say `absent` either. The import stage
    runs after `start-db` for exactly that reason.
    """
    rec = Recorder()
    rec.db_started = True
    rec.probe_answers = list(answers)
    return rec


def plan_files_in_order() -> list[str]:
    """The first line of every dump the plan applies, in the order `expand()` must apply them.

    Derived from the plan rather than written out: phases in data order, then
    each pattern's own files, and `lay_sql` names each file after its own path
    so the line the client receives says which file it is. Statements are not
    here — they carry no `-- <path>` line — and that is what makes the caller's
    filter honest.
    """
    order: list[str] = []
    for phase in SQL.phases:
        for pattern in list(phase.files) + list((phase.into_each or {}).values()):
            if "*" in pattern:
                names = [pattern.replace("*", f"{n:04d}") for n in (1, 2)]
            else:
                names = [pattern]
            order += [f"-- {name}" for name in names]
    return order


def entry_with_sql(plan: SqlPlan) -> CatalogEntry:
    """`ENTRY` carrying a different `install.native.cmangos.sql`, still installable on linux."""
    assert ENTRY.install.native is not None and CMANGOS is not None
    data = CMANGOS.model_copy(update={"sql": plan})
    native_block = ENTRY.install.native.model_copy(update={"cmangos": data})
    return ENTRY.model_copy(
        update={"install": ENTRY.install.model_copy(update={"native": native_block})}
    )


def engine_with_sql(plan: SqlPlan, rec: Recorder, **overrides: object) -> CmangosInstaller:
    """An engine over a patched SQL plan, carrying the same test gate `engine()` attaches."""
    eng = engine_for(entry_with_sql(plan), rec, **overrides)
    eng._test_gate = native.CallableGate(rec.probe, rec.reset)  # type: ignore[attr-defined]
    return eng


def one_statement_plan(*statements: str) -> SqlPlan:
    """The shipped plan with its phases replaced by one statement-only phase.

    Statements and no files, so nothing has to be laid on disk and what is
    asserted is the text that reached the client rather than a glob's ordering.
    """
    return SQL.model_copy(
        update={
            "phases": (SqlPhase(name="tokens", into=ENTRY.databases.world, statements=statements),)
        }
    )


@pytest.mark.parametrize("game", ["wow-tbc", "wow-vanilla", "wow-tortoise"])
def test_schemas_answers_for_every_database_name_the_shipped_plan_spells(game: str) -> None:
    """Keyed by NAME and not by role (A10): `sqlplan` looks the plan's own spellings up here.

    Enumerated off the plan, so the question asked is the one
    `sqlplan._check_plan_schemas()` asks — a name it cannot find is a refusal
    raised before a single directory is listed, which would put the import's
    failure two stages away from the mapping that caused it.

    `Databases.schema_map()` is the role-keyed mapping this must not be: it
    answers `world` -> `mangos`, and every name below is on the wrong side of
    that arrow.

    Over all three CMaNGOS entries, because the plan and the `databases` block
    are two independently written halves of every entry and they agree only by
    hand. Tortoise's plan spells `tw_world` where TBC's and vanilla's spell
    `mangos`, and its `create` is empty where theirs lists four names — so the
    entry whose names differ most is the one a `wow-tbc`-only check said
    nothing about. The refusal half — a name the mapping does not carry — is
    `test_sqlplan.py`'s, and is not repeated here.
    """
    entry = load_catalog().get(game)
    native_block = entry.install.native
    assert native_block is not None and native_block.cmangos is not None, game
    plan = native_block.cmangos.sql
    schemas = engine_for(entry, Recorder())._schemas()
    named = {
        *plan.create,
        plan.marker_db,
        *(rule.db for rule in plan.verify),
        *(data.db for data in plan.player_data),
    }
    for phase in plan.phases:
        if phase.into is not None:
            named.add(phase.into)
        named.update(phase.into_each or {})
    assert named, f"{game}'s plan names no database at all; this fixture proves nothing"
    assert named <= set(schemas), sorted(named - set(schemas))
    assert all(schemas[name] == name for name in named)


def test_schemas_carries_the_entrys_databases_and_nothing_the_entry_does_not_own() -> None:
    """The other half: a name outside the entry is absent, so `sqlplan` can refuse it."""
    schemas = engine(Recorder())._schemas()
    db = ENTRY.databases
    assert set(schemas) == {db.auth, db.characters, db.world, *db.extra}
    assert "acore_world" not in schemas


def test_the_import_applies_the_plans_dumps_in_the_plans_own_order(tmp_path: Path) -> None:
    """Phases in data order; within a phase, each pattern's files in natural order."""
    rec = ready_to_import(ABSENT)
    list(engine(rec)._import(context(server_with_sql(tmp_path))))
    fed = [line for line in rec.sql_calls if line.startswith("-- ")]
    assert fed == plan_files_in_order()


def test_phase_zero_creates_the_plans_schemas_before_the_first_dump_is_streamed(
    tmp_path: Path,
) -> None:
    """`create_schemas()` first, and it creates the plan's OWN names.

    The first thing the client is handed names the first schema `create` lists,
    which is what a mapping keyed by role could not produce: it would raise on
    the lookup instead, and nothing would be created at all.
    """
    db = DB_FACTS
    rec = ready_to_import(ABSENT)
    said = list(engine(rec)._import(context(server_with_sql(tmp_path))))
    first = SQL.create[0]
    assert rec.sql_calls[0] == (
        f"CREATE DATABASE IF NOT EXISTS `{first}` CHARACTER SET {db.charset};"
    )
    dumps = [i for i, line in enumerate(rec.sql_calls) if line.startswith("-- ")]
    assert dumps and min(dumps) > 0
    # About the LOG and nothing else. The sentence is built from `db.user` itself, so
    # it reads the same whatever `create_schemas()` was handed — what the DATABASE was
    # told is asserted off the script in
    # `test_phase_zero_grants_to_the_user_the_entry_says_the_emulator_connects_as`.
    assert any(db.user in line for line in said), "the log does not say whose user it was"


def test_phase_zero_grants_to_the_user_the_entry_says_the_emulator_connects_as(
    tmp_path: Path,
) -> None:
    """The account named in the SCRIPT, not the account named in the log line.

    The stage yields "... and the <user> user." built from `db.user` itself, so
    an assertion over what it SAID is satisfied by the family repeating its own
    sentence whatever `create_schemas()` was handed. What the database was told
    is the script, and the account lines are line 2 onward of it — `CREATE
    DATABASE` is line 1 and was all this file could see until 2026-09-02.

    The account and its grants are read together because that is the defect
    that lives between them: the emulator connects as the entry's user while
    the privileges were granted to another, and the server then starts and
    cannot read its world. Every account the script names is enumerated rather
    than one line matched, so a second account added below reads as a failure
    instead of passing unseen.
    """
    rec = ready_to_import(ABSENT)
    list(engine(rec)._import(context(server_with_sql(tmp_path))))
    phase_zero = rec.sql_scripts[0]
    assert phase_zero.startswith("CREATE DATABASE "), phase_zero
    named = set(re.findall(r"'([^']*)'@'%'", phase_zero))
    assert named == {DB_FACTS.user}, phase_zero
    granted = [line for line in phase_zero.splitlines() if line.startswith("GRANT ")]
    assert granted, phase_zero
    assert all(f"TO '{DB_FACTS.user}'@'%'" in line for line in granted), granted


def test_every_database_the_import_speaks_to_is_asked_with_this_installs_password(
    tmp_path: Path,
) -> None:
    """The secret resolved once at the top of the stage, read off every seam it reaches.

    That one local is the `MYSQL_PWD` of phase 0, of every dump `apply()`
    streams and of the marker, the `IDENTIFIED BY` the app user is given, and
    `verify()`'s own connection secret. A password this install did not mint is
    refused by all of them — and the stage would still yield exactly the same
    sentences, because nothing it says carries the secret. Nothing here reads
    the log for that reason.

    `sql_secrets` is what ARRIVED at the double (`env["MYSQL_PWD"]` at
    `exec_stdin`, the `password` argument at `sql_query`), so a call carrying
    the wrong secret is visible even where the script it sent spells none. Both
    seams are asserted to have been reached, or the enumeration could narrow to
    one of them and still pass.
    """
    rec = ready_to_import(ABSENT)
    list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert {"sql", "query"} <= set(rec.calls), rec.calls
    assert len(rec.sql_secrets) == len(rec.sql_calls), rec.sql_calls
    assert set(rec.sql_secrets) == {DB_PASSWORD}
    identified = [
        line
        for script in rec.sql_scripts
        for line in script.splitlines()
        if "IDENTIFIED BY" in line
    ]
    assert identified, "the app user was never given a password"
    assert all(f"IDENTIFIED BY '{DB_PASSWORD}'" in line for line in identified), identified


def test_the_completion_marker_is_written_after_every_verify_rule_and_last_of_all(
    tmp_path: Path,
) -> None:
    """The ordering the next install press depends on.

    `MarkerGate` reads a marker row as `imported` whatever else is true, so a
    marker written before the checks — or before the last dump — would leave a
    hollow world that every later press skips. Both halves are asserted over
    the same run because they are one ordering, not two.
    """
    rec = ready_to_import(ABSENT)
    said = list(engine(rec)._import(context(server_with_sql(tmp_path))))
    marker_at = next(i for i, s in enumerate(rec.sql_calls) if sqlplan.MARKER_TABLE in s)
    asked = {rule.query for rule in SQL.verify}
    verify_ats = [i for i, s in enumerate(rec.sql_calls) if s in asked]
    assert len(verify_ats) == len(SQL.verify), rec.sql_calls
    assert marker_at > max(verify_ats)
    assert marker_at == len(rec.sql_calls) - 1
    assert said[-1] == "The databases are imported and marked complete."


def test_the_import_asks_the_databases_what_state_they_are_in_exactly_once(
    tmp_path: Path,
) -> None:
    """The spine probes; the family watches that answer rather than asking again.

    A second probe is a second question. Between the two the databases can have
    become something else, and the branch this stage then takes would not be
    the branch the spine's table took — the spine would have said "run" over a
    database this stage then treats as finished, or the reverse.
    """
    rec = ready_to_import(ABSENT)
    list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert rec.calls.count("probe") == 1, rec.calls


def test_the_remembering_gate_keeps_the_last_answer_and_has_none_before_the_first_probe() -> None:
    """What `_import` reads instead of asking the databases a second question.

    The family branches on `gate.last` once `stage_import()` has returned, so
    this wrapper has to answer four things: nothing at all before a probe, the
    inner gate's OWN answer passed through rather than replaced, the LAST of
    several answers rather than the first — a wrapper keeping the first would
    hand the family the state from before the reset instead of the state after
    it — and a `reset()` that reaches the inner gate and is not itself an
    answer about the databases.

    Driven through the real `CallableGate`, which is the gate an install
    actually wraps, and the inner pair records what it was asked, so a second
    probe smuggled in here reads as an extra entry rather than as the same
    answer twice.
    """
    remaining = [PARTIAL, IMPORTED]
    asked: list[str] = []

    def probe() -> docker.ImportState:
        asked.append("probe")
        return remaining.pop(0)

    def reset() -> tuple[str, ...]:
        asked.append("reset")
        return (ENTRY.databases.world,)

    gate = cmangos._Remembering(native.CallableGate(probe, reset))
    assert gate.last is None, "it answered about databases nobody had asked about"
    assert gate.probe() is PARTIAL
    assert gate.last is PARTIAL
    assert gate.reset() == (ENTRY.databases.world,)
    assert gate.last is PARTIAL, "a reset is not an answer about what the databases hold"
    assert gate.probe() is IMPORTED
    assert gate.last is IMPORTED
    assert asked == ["probe", "reset", "probe"]


def test_the_import_leaves_a_finished_one_alone_even_when_an_older_plan_wrote_it(
    tmp_path: Path,
) -> None:
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    said = list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert rec.sql_calls == [], "nothing was sent to the database"
    assert any("leaving them alone" in line for line in said), said


def test_the_import_leaves_a_populated_database_that_is_complete_alone(tmp_path: Path) -> None:
    """`populated` + `complete` is a finished import, and it is not the `imported` branch.

    It has to be read off the SAME answer the spine's table returned on: the
    spine returns for it without importing, so a family recognising only
    `imported` would go on and run the whole plan over a database with a
    person's characters in it.
    """
    full = docker.ImportState("populated", "every schema has tables and rows", complete=True)
    rec = ready_to_import(full)
    list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert rec.sql_calls == []


def test_the_import_clears_a_half_written_database_before_it_runs(tmp_path: Path) -> None:
    rec = ready_to_import(PARTIAL, IMPORTED)
    rec.reset_answer = (ENTRY.databases.world,)
    said = list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert rec.calls.index("reset") < rec.calls.index("sql")
    assert any(f"Cleared {ENTRY.databases.world}" in line for line in said), said
    assert any(sqlplan.MARKER_TABLE in s for s in rec.sql_calls), "and the import then ran"


def test_the_import_refuses_a_database_that_already_holds_somebodys_data(tmp_path: Path) -> None:
    rec = ready_to_import(POPULATED_HALF)
    with pytest.raises(InstallerError, match="already hold data"):
        list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert rec.sql_calls == []


def test_the_import_refuses_a_database_that_could_not_be_asked(tmp_path: Path) -> None:
    """No `db_started`, so the probe answers `unreadable` the way the real one does."""
    rec = Recorder()
    with pytest.raises(InstallerError, match="could not be asked"):
        list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert rec.sql_calls == []


def test_a_warn_phase_failure_names_the_file_by_its_server_relative_path_and_carries_on(
    tmp_path: Path,
) -> None:
    """`on_error: warn` is the scripts' `2>/dev/null` made visible, not made fatal."""
    phase = next(p for p in SQL.phases if p.on_error == "warn" and p.files)
    failing = phase.files[0].replace("*", "0001")
    rec = ready_to_import(ABSENT)
    rec.failing_sql = f"-- {failing}"
    said = list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert any(failing in line for line in said), said
    assert any(sqlplan.MARKER_TABLE in s for s in rec.sql_calls), "the import still finished"


def test_a_fail_phase_failure_stops_the_import_and_leaves_no_marker(tmp_path: Path) -> None:
    phase = next(p for p in SQL.phases if p.on_error == "fail" and p.files)
    failing = phase.files[0].replace("*", "0001")
    rec = ready_to_import(ABSENT)
    rec.failing_sql = f"-- {failing}"
    with pytest.raises(InstallerError, match=re.escape(failing)):
        list(engine(rec)._import(context(server_with_sql(tmp_path))))
    assert not any(sqlplan.MARKER_TABLE in s for s in rec.sql_calls)


def test_a_verify_shortfall_refuses_and_writes_no_marker(tmp_path: Path) -> None:
    """The count the database answered reaches the refusal, and no marker is written.

    `4242` fails the first rule, passes the second, and appears nowhere else in
    the sentence: the family's summary of the RULES quotes every `min` and every
    query, so a refusal that had lost `verify()`'s own answer would still name
    the rule. The number is the only part that can only have come from the
    database.
    """
    rec = ready_to_import(ABSENT)
    rec.query_answer = "4242\n"
    with pytest.raises(InstallerError) as refusal:
        list(engine(rec)._import(context(server_with_sql(tmp_path))))
    said = str(refusal.value)
    assert "4242" in said, said
    assert SQL.verify[0].query in said
    assert "No completion marker was written" in said
    assert not any(sqlplan.MARKER_TABLE in s for s in rec.sql_calls)


def test_a_verify_rule_that_could_not_be_answered_is_a_refusal_and_not_a_marker(
    tmp_path: Path,
) -> None:
    """A database that will not answer is not a database that answered zero."""

    def unanswerable(*args: object, **kwargs: object) -> str:
        raise docker.DockerCommandError("Error: No such container: yulon-tbc-db")

    rec = ready_to_import(ABSENT)
    with pytest.raises(InstallerError, match="No such container"):
        list(engine(rec, sql_query=unanswerable)._import(context(server_with_sql(tmp_path))))
    assert not any(sqlplan.MARKER_TABLE in s for s in rec.sql_calls)


def test_a_phase_statement_reaches_the_client_with_its_tokens_filled_in(tmp_path: Path) -> None:
    """Statements are filled through the one `composegen.fill`; dumps never are.

    The value asserted is one only the install knows — the entry's database
    user — arriving in the text the client was handed, rather than that a fill
    happened.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(ABSENT)
    plan = one_statement_plan("SELECT '{{DB_USER}}' AS who")
    list(engine_with_sql(plan, rec)._import(context(server_dir)))
    assert f"SELECT '{DB_FACTS.user}' AS who" in rec.sql_calls, rec.sql_calls


def test_a_phase_statement_naming_the_password_is_filled_from_the_secret_half(
    tmp_path: Path,
) -> None:
    """The shipped Tortoise plan writes `IDENTIFIED BY '{{DB_PASSWORD}}'` as a statement.

    So the import spends `_secret_tokens()` and not `_public_tokens()`. Handed
    the public half, `expand()` refuses the unknown token and nothing is
    applied at all — which is why the assertion is that the password ARRIVED,
    and not that some mapping was passed.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(ABSENT)
    plan = one_statement_plan("CREATE USER 'x'@'%' IDENTIFIED BY '{{DB_PASSWORD}}'")
    list(engine_with_sql(plan, rec)._import(context(server_dir)))
    assert f"CREATE USER 'x'@'%' IDENTIFIED BY '{DB_PASSWORD}'" in rec.sql_calls, rec.sql_calls


def test_a_stop_arriving_during_the_last_dump_is_caught_before_verify_and_the_marker(
    tmp_path: Path,
) -> None:
    """The window `sqlplan.apply()` cannot close, because it checks cancel BEFORE each run.

    A Stop pressed while the last file is streaming is seen by nothing inside
    `apply()` — there is no run left after it to check it — so `apply()`
    returns normally, and without the family's own check the stage would go on
    to verify the databases and write the completion marker for an import the
    user stopped. The marker is what makes that permanent: the next press reads
    a marker row as `imported` and leaves the half-loaded world alone.

    The Stop is set from inside the seam, on the way out of the last run, so
    the window is the real one rather than a cancel that was pending all along
    — and that run is asserted to have gone out, which is what says the refusal
    came from after `apply()` rather than from inside it. The two checks word
    it differently and that is what tells them apart: `sqlplan._check_cancel`
    says "The import was stopped", the spine's says "the install was stopped".

    Over a one-statement plan, because the trigger has to fire on the run
    `apply()` has no successor for, and only a plan short enough to name its
    own last run makes that identifiable without the test re-deriving
    `expand()`'s ordering. Written first against the shipped plan's last DUMP,
    which failed on 2026-09-02 with `sqlplan`'s wording: `expand()` puts a
    phase's statements before its files, so dumps are not where that plan ends
    and the Stop was caught one run early, inside `apply()`.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(ABSENT)
    stop = threading.Event()
    last = "SELECT 'the last run this plan has'"
    inner = rec.exec_stdin

    def exec_stdin(
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        proc = inner(container, argv, source, env=env, wsl_distro=wsl_distro)
        if rec.sql_calls[-1] == last:
            stop.set()
        return proc

    eng = engine_with_sql(one_statement_plan(last), rec, exec_stdin=exec_stdin)
    with pytest.raises(InstallerError, match="the install was stopped"):
        list(eng._import(context(server_dir, cancel=stop)))
    assert last in rec.sql_calls, "the Stop was meant to arrive after the last run went out"
    asked = {rule.query for rule in SQL.verify}
    assert not [s for s in rec.sql_calls if s in asked], "the databases were checked anyway"
    assert not [s for s in rec.sql_scripts if sqlplan.MARKER_TABLE in s], "a marker was written"


def test_a_marker_that_could_not_be_written_arrives_as_the_modules_own_sentence(
    tmp_path: Path,
) -> None:
    """`write_marker()` has already named the marker; wrapping it would name it twice.

    `sqlplan._run_sql()` turns both of its failures into an `InstallerError`
    that is already the sentence a user reads. The clause below it catches
    `RuntimeError` and `InstallerError` is one, so without the narrow arm ahead
    of it the refusal arrives folded inside a second sentence with a class name
    in the middle of it — "The import finished but its completion marker could
    not be written (InstallerError: The import stopped while writing the import
    marker: ERROR 1064 ...)".

    The switch is the marker table's own name, and the fixture asserts that
    exactly one script this install streamed contained it, so the refusal under
    test is the marker's and not some dump's.
    """
    rec = ready_to_import(ABSENT)
    rec.failing_sql = sqlplan.MARKER_TABLE
    with pytest.raises(InstallerError) as refusal:
        list(engine(rec)._import(context(server_with_sql(tmp_path))))
    hit = [s for s in rec.sql_scripts if rec.failing_sql in s]
    assert hit == [rec.sql_scripts[-1]], "the switch failed something other than the marker"
    said = str(refusal.value)
    assert said.startswith("The import stopped while writing the import marker: "), said
    assert "could not be written" not in said, said
    assert "InstallerError" not in said, said


def test_the_import_cancel_note_is_said_at_the_import_and_nowhere_else(tmp_path: Path) -> None:
    """A4: the spine says every stage's note, so the body yields none of its own.

    Read off a whole install rather than off `stages()`, because a
    `cancel_note=` keyword that is present but bound to the wrong stage reads
    identically in the tuple — the shape of the AzerothCore incident
    `test_the_build_cancel_note_is_said_at_the_build_and_not_before_every_stage`
    is written against.
    """
    rec = Recorder()
    said = install(rec, tmp_path / "srv", client_folder(tmp_path))
    at = said.index("--- import")
    assert said[at + 1] == native.IMPORT_CANCEL_NOTE
    assert said.count(native.IMPORT_CANCEL_NOTE) == 1


def test_import_is_recorded_and_sits_between_start_db_and_up() -> None:
    """Order and bookkeeping, because each is one keyword in `stages()`."""
    stages = engine(Recorder()).stages()
    names = [stage.name for stage in stages]
    at = names.index("start-db")
    assert names[at + 1 : at + 3] == ["import", "up"]
    stage = next(s for s in stages if s.name == "import")
    assert stage.recorded, "a finished import must not be re-run by a resume"


def test_the_real_gate_asks_this_installs_container_with_this_installs_password(
    tmp_path: Path,
) -> None:
    """The one run of the unpatched `_gate()`; `gated` replaces it everywhere else here.

    A `MarkerGate` built against another install's container answers `absent`
    for a database that is full, and the import then runs again over a working
    server — `MarkerGate`'s own docstring says so. Its wiring is therefore
    asserted by what reached the seam, not by the class of the object returned.

    The schema names in the answer are the second half: `self._names` is what
    `_plan_schemas(plan, schemas)` made of the plan through `_schemas()`, so a
    gate handed the wrong mapping could not have produced them.
    """
    seen: list[tuple[str, str, str, str | None, str]] = []

    def spy(
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        seen.append((container, client, password, schema, statement))
        return ""  # no databases at all on this server yet

    rec = Recorder()
    state = REAL_GATE(engine(rec, sql_query=spy), context(tmp_path)).probe()
    assert seen, "the gate asked the database nothing"
    # One question per plan schema, and every one of them to the SAME place: it is a
    # call going somewhere else that this asserts against, not the number of calls.
    assert set(seen) == {
        (ENTRY.container_spec().db, DB_FACTS.client, DB_PASSWORD, None, "SHOW DATABASES")
    }
    assert state.state == "absent"
    for name in {*SQL.create, SQL.marker_db, *(rule.db for rule in SQL.verify)}:
        assert name in state.detail, state.detail


# -- the one guard on a Tortoise password ------------------------------------


def test_the_compose_scalar_set_is_the_only_guard_on_the_tortoise_password(tmp_path: Path) -> None:
    """`wow-tortoise`'s `sql.create` is empty, so `create_schemas()`'s value checks never run.

    Measured 2026-09-02 against a mutant that dropped the single quote from
    `composegen._UNSAFE_SCALAR_CHARS`: a `.db_password` holding
    `tortoise-a'b--x` rendered into the compose files, into `.env` and into both
    conf files, and `IDENTIFIED BY 'tortoise-a'b--x'` reached the SQL stream.
    The whole suite stayed green through it. `resolve_secrets()` takes an
    existing file AS WRITTEN, so the value is the user's, not this app's.

    The neighbour is `sqlplan._refuse_unquotable()`, which holds the quote too.
    It is asserted unreached twice over: `create_schemas()` is handed the same
    bad password and an `exec_stdin` that fails the test if it is called at all,
    and the refusal that does arrive is pinned to composegen's words rather than
    to sqlplan's.
    """
    entry = installable(load_catalog().get("wow-tortoise"))
    native_block = entry.install.native
    assert native_block is not None and native_block.cmangos is not None
    plan = native_block.cmangos.sql
    assert plan.create == (), "the premise: with a `create` list this test would prove nothing"

    bad = "tortoise-a'b--x"
    server_dir = tmp_path / "server"
    server_dir.mkdir()
    password_file = entry.install.password.file
    assert password_file is not None, "a generated plan names the file it persists to"
    (server_dir / password_file).write_text(bad + "\n", encoding="utf-8")
    eng = engine_for(entry, Recorder())
    assert eng.resolve_secrets(server_dir).db_password == bad, "the file is read as written"

    def never_executed(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("create_schemas() ran a statement for a plan with no `create` list")

    sqlplan.create_schemas(
        plan,
        container=entry.containers.db,
        client="mysql",
        password=bad,
        # The identity map over the plan's own database NAMES, which is what
        # `sqlplan` looks every one of them up in; `entry.schema_map()` is keyed
        # by role and answers for none of them.
        schemas=eng._schemas(),
        user=native_block.db.user,
        charset=native_block.db.charset,
        exec_stdin=never_executed,
    )

    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=native.InstallState(
            game_id=entry.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="cmangos",
        ),
        cancel=None,
        secrets=native.Secrets(db_password=bad),
    )
    with pytest.raises(InstallerError) as caught:
        list(eng.stage_generate_compose(ctx))
    said = str(caught.value)
    assert "cannot be written into a compose file safely" in said
    assert "into SQL safely" not in said, "that is sqlplan's refusal, not the one under test"
    assert repr("'") in said, "the refusal must name the character it refused"
