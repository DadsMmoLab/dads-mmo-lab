"""Tests for the CMaNGOS family engine (`yulon.catalog.families.cmangos`, phase 7.3).

Same shape as `test_families_azerothcore.py`: the `Recorder` machine double from
`tests/support_native.py`, the real `wow-tbc` catalog entry, the real shared
templates. Nothing here proves a CMaNGOS server installs — that is gate 7.4 —
it proves the family's control flow, its refusals, what a resume repeats, and
that every `docker run` it asks for is shaped the way the design says (client
`:ro`, `--user` on Linux, cwd `/out`), asserted by FIELD on `ContainerRun`.

The import gate is meant to be swapped for a `CallableGate` over
`Recorder.probe/reset`: `MarkerGate`'s five branches are `test_sqlplan.py`'s to
prove, and this file is to prove the family's reaction to each answer. That is
not wired yet and cannot be — K.6 is what binds the `import` stage and names
the method resolving the gate, and no such method exists in `yulon/` on this
branch. `engine()` already attaches the pair as `_test_gate`, and the `gated`
fixture patches `CmangosInstaller._gate` with `raising=False`, which today adds
an attribute nothing calls;
`test_the_import_gate_seam_has_not_landed_so_the_gated_fixture_is_inert` is
what says so out loud, and it goes red the day K.6 lands so the fixture is
re-pointed at the real method rather than staying quietly inert.
"""

from __future__ import annotations

import ast
import gzip
import inspect
import os
import subprocess
import threading
from collections.abc import Callable, Iterable, Iterator
from dataclasses import replace
from pathlib import Path, PurePosixPath

import pytest

import yulon
from tests.support_native import Recorder
from yulon import docker, platform, resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, PasswordPlan, SqlPlan, load_catalog
from yulon.catalog.families import dockerfile, extract, sqlplan
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

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


def client_folder(tmp_path: Path) -> Path:
    """A client tree passing the TBC ClientSpec: Data/, the required file, six MPQs, a locale."""
    client = tmp_path / "client"
    (client / "Data" / "enUS").mkdir(parents=True)
    for name in ("common", "expansion", "patch", "patch-2", "patch-3", "misc"):
        (client / "Data" / f"{name}.MPQ").write_bytes(b"MPQ\x1a")
    (client / "Data" / "enUS" / "locale-enUS.MPQ").write_bytes(b"MPQ\x1a")
    return client


def context(
    server_dir: Path, client_dir: Path | None = None, *, completed: Iterable[str] = ()
) -> native.StageContext:
    """A `StageContext` for calling one stage body directly."""
    return native.StageContext(
        server_dir=server_dir,
        client_dir=client_dir,
        state=native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family="cmangos",
            completed=tuple(completed),
        ),
        cancel=None,
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


GATE_METHOD_AT_IMPORT = hasattr(CmangosInstaller, "_gate")
"""Whether the engine carried a `_gate` BEFORE the `gated` fixture patched one on.

Read at import because `gated` is autouse: inside any test body
`hasattr(CmangosInstaller, "_gate")` is True whether K.6 landed or not, since
`monkeypatch.setattr(..., raising=False)` puts the attribute on the class
itself. The question only has an honest answer once.
"""


@pytest.fixture(autouse=True)
def gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every engine's import gate is to be the one `engine()` attached — once there is one.

    `raising=False` is kept rather than fixed, because there is nothing here
    for a strict patch to hit: `CmangosInstaller` has no `_gate` on this branch
    and `raising=True` would error in every test in this file. It is the
    inertness that is dangerous, not the keyword, so it is asserted instead —
    see `test_the_import_gate_seam_has_not_landed_so_the_gated_fixture_is_inert`.
    """

    def gate(self: CmangosInstaller, ctx: native.StageContext) -> native.ImportGate:
        attached = getattr(self, "_test_gate", None)
        assert attached is not None, "build engines with engine(rec) in this file"
        return attached

    monkeypatch.setattr(CmangosInstaller, "_gate", gate, raising=False)


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


def test_the_unbound_half_of_the_pinned_tuple_is_inert_until_its_stages_land() -> None:
    """K.3-K.7 bind the other six names; nothing in the app reads the tuple before then.

    `stage_names()` — not `STAGE_NAMES` — is what the spine validates a resume
    against (`_guard`, `_ownership`, `with_stage`), and it is derived from
    `stages()`. So the six names with no `Stage` behind them cannot be recorded,
    cannot be read back out of a state file, and cannot reorder one: a state
    file naming `extract` today has that name DROPPED on the way in, which is
    the same rule an unknown name has always had. The engine is also not in
    `FAMILIES` yet, so no user reaches it at all.
    """
    from yulon.catalog.families import FAMILIES

    assert "cmangos" not in FAMILIES
    bound = engine(Recorder()).stage_names()
    assert set(bound) < set(CmangosInstaller.STAGE_NAMES)
    smuggled = native.InstallState(game_id=ENTRY.id, install_id="x").with_stage("extract", bound)
    assert smuggled.completed == ()


def test_the_import_gate_seam_has_not_landed_so_the_gated_fixture_is_inert() -> None:
    """Red on the day K.6 lands, deliberately: the `gated` fixture must be re-pointed then.

    `gated` patches `CmangosInstaller._gate` with `raising=False`, and no
    `_gate` exists anywhere in `yulon/`. The hazard is not the missing method,
    which is only K.6 not having happened; it is a RENAME. If K.6 calls the
    gate resolver anything else, `monkeypatch.setattr` goes on quietly adding
    an unused attribute, the fixture silently stops overriding anything, and
    every end-to-end test here starts driving the real `MarkerGate` at a
    machine that is not there — a failure with no line in this file pointing
    at its cause.

    So the assertion is on what makes the inertness HARMLESS rather than on
    the name: while no `import` stage is bound, no body asks for a gate and
    nothing reads the attribute either way. The day a stage named `import`
    appears, this fails, and K.6 has to point `gated` at whatever it really
    called the method — with `raising=True`, which by then it can afford.

    The name is asked about too, but through `GATE_METHOD_AT_IMPORT`: `gated`
    is autouse and puts `_gate` on the class, so a `hasattr` in here would
    answer True on this branch and the check would pass for the wrong reason.
    """
    assert (
        "import" not in engine(Recorder()).stage_names()
    ), "K.6 bound the import stage: point `gated` at the real gate method, with raising=True"
    assert (
        not GATE_METHOD_AT_IMPORT
    ), "`_gate` exists now, so `gated` can and must patch it with raising=True"


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


def test_tokens_carry_the_family_set_from_catalog_data(tmp_path: Path) -> None:
    server_dir = tmp_path / "srv"
    tokens = engine(Recorder())._tokens(context(server_dir))
    native_block = ENTRY.install.native
    assert native_block is not None and CMANGOS is not None
    assert tokens["DB_HOST"] == ENTRY.containers.db
    assert tokens["DB_USER"] == native_block.db.user
    assert tokens["DB_IMAGE"] == native_block.db.image
    assert tokens["DB_PASSWORD"] == DB_PASSWORD
    assert tokens["AUTH_DB"] == ENTRY.databases.auth
    assert tokens["WORLD_DB"] == ENTRY.databases.world
    assert tokens["CHAR_DB"] == ENTRY.databases.characters
    assert tokens["LOGS_DB"] == ENTRY.databases.extra[0]
    assert tokens["CORE_DIR"] == str(PurePosixPath(CMANGOS.conf.source_dir).parent)
    assert tokens["CORE_DIR"] == "/opt/mangos", "the in-image install prefix, never a host path"
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


def test_tokens_omit_logs_db_when_the_entry_has_no_extra_schema(tmp_path: Path) -> None:
    bare = ENTRY.model_copy(update={"databases": ENTRY.databases.model_copy(update={"extra": ()})})
    eng = CmangosInstaller(
        bare,
        installers_root=resources.installers_dir(),
        seams=Recorder().seams(platform_id=lambda: "linux"),
    )
    assert "LOGS_DB" not in eng._tokens(context(tmp_path / "srv"))


def test_no_dockerfile_template_names_the_secret_this_one_mapping_carries() -> None:
    """`_tokens()` is handed to `dockerfile.render()` whole (K.4), and it holds `DB_PASSWORD`.

    A tripwire over the shipped templates, kept — but it is no longer what
    stands between the mapping and the secret. `dockerfile.SECRET_TOKEN` is:
    `render()` now refuses the token BY NAME, the way `composegen.generate()`
    refuses it in a compose template, and drops the key from the mapping it
    fills with. This test says the shipped six are clean; the refusal says the
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

    Rendered from the REAL `_tokens()` mapping, so what is refused is the object
    K.4 hands over rather than a convenient stand-in.
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
    tokens = engine(Recorder())._tokens(context(tmp_path / "srv"))
    assert tokens["DB_PASSWORD"] == DB_PASSWORD, "the whole mapping, secret included (K.4)"
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


def test_the_family_s_catalog_refusals_end_in_one_tail_and_not_two(tmp_path: Path) -> None:
    """Three refusals about a malformed entry, one sentence — they said it two ways.

    Until 2026-09-01 two ended "That is a catalog error in the app…" and the
    third "That is a bug in the app's catalog…", 65 lines apart. A second
    wording for one thing drifts further from the first every time either is
    edited, which is why `test_sqlplan.py` asserts two of its refusals are the
    SAME string rather than merely that both complain.

    `_native()` is deliberately not among them: its refusal ("has no
    `install.native` section") carries no tail at all, and whether it should is
    a separate question this does not settle.
    """
    from yulon.catalog.families import cmangos

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
    said: list[str] = []
    with pytest.raises(InstallerError) as from_block:
        engine_for(no_block, Recorder(), volume_exists=refuse_to_answer)._data()
    said.append(str(from_block.value))
    with pytest.raises(InstallerError) as from_image:
        engine(Recorder())._image_ref(context(server_dir), "no-such-service")
    said.append(str(from_image.value))
    with pytest.raises(InstallerError) as from_password:
        eng = engine_for(no_file, Recorder(), volume_exists=refuse_to_answer)
        list(eng._db_password(context(server_dir)))
    said.append(str(from_password.value))

    assert len(said) == 3
    for refusal in said:
        assert refusal.endswith(cmangos.CATALOG_ERROR_TAIL), refusal
        assert refusal != cmangos.CATALOG_ERROR_TAIL, "the tail is a tail, not the whole refusal"


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
    """End to end over the stages bound so far; K.5-K.7 insert the rest between them.

    Also drives `lay_sql`/`on_clone`, so the SQL fixtures the later tasks import
    are proved to land under the source that owns them — and it is the only
    test here that reaches `_write_dockerfile` through `run()` rather than by
    calling the body, so the `Stage` really is wired to the method.
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
        "--- start-db",
        "--- up",
        "--- ready",
    ]
    state = native.read_state(server_dir, valid=engine(rec).stage_names())
    assert state is not None
    assert state.completed == ("clone-sources", "write-dockerfile", "generate-compose", "build")
    # The pair really landed, from the whole install rather than from a direct
    # call to the body, and the password is in neither: `_tokens()` hands the
    # secret over and `dockerfile.render()` is what keeps it out of the context.
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


def test_write_dockerfile_hands_the_renderer_the_whole_mapping_and_no_file_carries_the_secret(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The K.4 decision, asserted as behaviour rather than defended in a docstring.

    Contract A6 is one mapping for the Dockerfile, the conf tables, the SQL and
    verify alike, and it carries `DB_PASSWORD` because the conf tables need it.
    The stage hands that mapping over WHOLE, and what keeps the password out of
    the build context is `dockerfile.render()` — which refuses a template
    naming the token and drops the key before it fills anything. So this test
    asserts both halves at once: what goes in still has the secret, and what
    lands on disk does not.

    Narrowing the mapping HERE was the alternative, and it was rejected: it
    would put a third copy of the same rule at a call site, which is the level
    the rule keeps being got wrong at. See `_write_dockerfile`'s docstring.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    seen: list[dict[str, str]] = []
    real = dockerfile.render

    def spy(template_dir: Path, tokens: dict[str, str]) -> tuple[str, str]:
        seen.append(dict(tokens))
        return real(template_dir, tokens)

    monkeypatch.setattr(dockerfile, "render", spy)
    eng = engine(Recorder())
    ctx = context(server_dir)
    list(eng._write_dockerfile(ctx))
    assert seen == [eng._tokens(ctx)], "the whole mapping, not a copy with keys taken out"
    assert seen[0]["DB_PASSWORD"] == DB_PASSWORD
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
