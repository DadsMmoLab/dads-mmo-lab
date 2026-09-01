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

import gzip
import threading
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path, PurePosixPath

import pytest

from tests.support_native import Recorder
from yulon import docker, platform, resources
from yulon.catalog import composegen, native
from yulon.catalog.catalog import CatalogEntry, SqlPlan, load_catalog
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


def test_the_typed_blocks_refuse_a_catalog_that_does_not_carry_them(tmp_path: Path) -> None:
    """`_native()`/`_data()` say "a bug in the app's catalog", never a machine's."""
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


def test_the_six_bound_stages_run_in_order_and_record_the_recorded_ones(tmp_path: Path) -> None:
    """End to end over the stages K.2 binds; K.3-K.7 insert the rest between them.

    Also the only test that drives `lay_sql`/`on_clone`, so the SQL fixtures the
    later tasks import are proved to land under the source that owns them.
    """
    rec = Recorder()
    server_dir = tmp_path / "srv"
    client = client_folder(tmp_path)
    said = install(rec, server_dir, client)
    assert [line for line in said if line.startswith("--- ")] == [
        "--- clone-sources",
        "--- generate-compose",
        "--- build",
        "--- start-db",
        "--- up",
        "--- ready",
    ]
    state = native.read_state(server_dir, valid=engine(rec).stage_names())
    assert state is not None
    assert state.completed == ("clone-sources", "generate-compose", "build")
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
