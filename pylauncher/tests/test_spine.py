"""Tests for the game-free install spine (`yulon.catalog.native.StagedInstaller`, roadmap 7.1).

What is asserted here is true of EVERY family: the state file and its hint
semantics, the guard, ask-forwarding, streaming, and what a stage tuple may
and may not contain. Anything AzerothCore-shaped lives in
`test_families_azerothcore.py`. The machine double is shared:
`tests/support_native.py`.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import traceback
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from tests.support_native import ENTRY, IMPORTED, PARTIAL, TBC, Recorder, install
from yulon import docker, resources
from yulon.catalog import composegen, native, preflight
from yulon.catalog.catalog import CatalogEntry, ReadyMarkers
from yulon.catalog.families import FAMILIES, family_for
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions

ORDER = ("clone-sources", "build", "import", "up")
HALF_WRITTEN = docker.ImportState("partial", "acore_world has 3 tables but no import record")
CANARY = "hunter2-a2-canary"


# -- the state file ---------------------------------------------------------


def test_the_state_file_round_trips_the_family(tmp_path: Path) -> None:
    """`family` is the 7.1 key: a folder installed as one family is never read as another."""
    native.write_state(
        tmp_path,
        native.InstallState(
            game_id="wow-wotlk", install_id="abcd1234", family="azerothcore", completed=("build",)
        ),
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == "azerothcore"
    assert state.completed == ("build",)
    assert state.game_id == "wow-wotlk"
    assert json.loads((tmp_path / native.STATE_FILE).read_text(encoding="utf-8"))["family"] == (
        "azerothcore"
    )


def test_a_state_file_written_before_family_existed_reads_as_an_empty_family(
    tmp_path: Path,
) -> None:
    """`version` stays 1: the key is additive, and an old file is not a refusal."""
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {"version": 1, "game_id": "wow-wotlk", "install_id": "abc", "completed": ["build"]}
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == ""
    assert state.version == 1


def test_a_family_that_is_not_a_string_reads_as_an_empty_family(tmp_path: Path) -> None:
    """A junk `family` must degrade to "unknown", never to a family name.

    `""` is the one value that means "this file does not say", and the guard is
    what turns that into "trust the entry". A non-string that survived as, say,
    `None` would type-lie to every reader downstream.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps({"version": 1, "game_id": "wow-wotlk", "install_id": "abc", "family": 7}),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.family == ""


def test_read_state_keeps_only_the_stage_names_the_entry_has(tmp_path: Path) -> None:
    """Per-entry validation replaces the global `STAGE_ORDER` filter (7.1).

    A file naming a stage this entry does not have must not become a skip —
    and the entry, not a module constant, is what says which names exist.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "clone-core", "invent-a-stage"],
            }
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.completed == ("build",)


def test_the_same_file_reads_differently_for_two_entries_stage_tuples(tmp_path: Path) -> None:
    """`valid` is per entry, so "known stage" is not a property of the module.

    The rejection above must be the `valid` argument doing the work and not a
    surviving module-level filter: `clone-core` is a real AzerothCore stage
    name, and it is dropped only because the tuple passed in has no such name.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "clone-core"],
            }
        ),
        encoding="utf-8",
    )
    narrow = native.read_state(tmp_path, valid=ORDER)
    wide = native.read_state(tmp_path, valid=("clone-core", "build"))
    assert narrow is not None and wide is not None
    assert narrow.completed == ("build",)
    # `valid` filters; it does not sort. The file's own order is kept, because
    # `with_stage` is what wrote that order and is what normalises it again.
    assert wide.completed == ("build", "clone-core")


def test_with_stage_orders_by_the_entry_tuple_and_never_records_twice() -> None:
    fresh = native.InstallState(game_id="wow-tbc", install_id="abc", family="cmangos")
    once = fresh.with_stage("import", ORDER).with_stage("clone-sources", ORDER)
    assert once.completed == ("clone-sources", "import")
    assert once.with_stage("import", ORDER) is once
    # A name outside the entry's tuple is dropped, the rule `read_state()` applies too.
    assert once.with_stage("invent-a-stage", ORDER).completed == ("clone-sources", "import")
    assert once.with_stage("build", ORDER).last_error == ""


def test_with_stage_keeps_the_family_it_was_given() -> None:
    """Recording progress must not quietly drop the ownership claim it carries."""
    state = native.InstallState(game_id="wow-tbc", install_id="abc", family="cmangos")
    assert state.with_stage("build", ORDER).family == "cmangos"


# -- the stage model --------------------------------------------------------


def _a_context(secret: native.Secrets) -> native.StageContext:
    """The context a stage body is handed, built once so every leak test uses the same one."""
    return native.StageContext(
        server_dir=Path("/srv"),
        client_dir=None,
        state=native.InstallState("wow-tbc", "abc", "cmangos"),
        cancel=threading.Event(),
        secrets=secret,
    )


def _a_stage_that_crashes(ctx: native.StageContext) -> Iterator[str]:
    """A stage body that dies mid-run, so its frame — holding `ctx` — lands in a traceback."""
    raise InstallerError("the build stopped")
    yield ""  # pragma: no cover - makes this a generator, which is what `Stage.run` is


def _crash_dump_with_locals() -> str:
    """A crash dump that `repr()`s every frame's locals, the way a crash reporter does.

    Done in a helper and not in the test so the ONLY frames in the traceback are
    ones holding a `StageContext`: a test frame would have the bare canary
    string in its own locals and the assertion would fail for the wrong reason.
    """
    ctx = _a_context(native.Secrets(db_password=CANARY))
    try:
        for _ in native.Stage("build", _a_stage_that_crashes).run(ctx):
            pass
    except InstallerError as exc:
        plain = "".join(traceback.format_exception(exc))
        with_locals = "".join(
            traceback.TracebackException.from_exception(exc, capture_locals=True).format()
        )
        return plain + with_locals
    raise AssertionError("the stage was supposed to crash")


def test_secrets_never_print_the_password() -> None:
    """A `StageContext` ends up in tracebacks and debug logs; the value must not."""
    secret = native.Secrets(db_password=CANARY)
    assert repr(secret) == "Secrets(db_password=***)"
    assert CANARY not in str(secret)
    assert CANARY not in repr(_a_context(secret))
    assert secret.db_password == CANARY


def test_the_password_never_reaches_a_log_line_or_a_crash_dump(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Where the value would actually escape: a debug log and a dump that repr()s locals.

    `repr()` masking is only worth having if it holds at the two places the
    object is rendered by something other than the author — a log line
    interpolating the context, and a crash reporter formatting every frame's
    locals. A default dataclass `repr()` passes the first and loses the
    password in the second, which is the failure this pins.
    """
    ctx = _a_context(native.Secrets(db_password=CANARY))
    with caplog.at_level(logging.DEBUG):
        native.logger.debug(f"stage context: {ctx}")
        native.logger.debug("secrets are %r / %s", ctx.secrets, ctx.secrets)

    rendered = [record.getMessage() for record in caplog.records]
    rendered.append(_crash_dump_with_locals())
    for text in rendered:
        assert CANARY not in text


def test_a_stage_is_recorded_by_default_and_carries_its_own_cancel_note() -> None:
    """The two-argument form must MEAN `recorded=True, cancel_note=""`, not merely default to it.

    Families write most stages as `Stage(name, body)`; the spine then reads
    `recorded` to decide what a resume may skip and `cancel_note` to decide what
    a Stop costs HERE. So the contract worth pinning is the equality — the short
    form and the spelled-out form are the same stage — plus the fact that both
    fields are actually overridable, which is what makes them data and not a
    module constant.
    """

    def body(ctx: native.StageContext) -> Iterator[str]:
        yield ctx.server_dir.name

    assert native.Stage("build", body) == native.Stage("build", body, recorded=True, cancel_note="")
    assert native.Stage("up", body, recorded=False).recorded is False
    assert native.Stage("build", body, cancel_note="the build restarts").cancel_note == (
        "the build restarts"
    )


def test_a_callable_gate_answers_the_probe_and_refuses_to_reset_without_a_seam() -> None:
    """The AzerothCore pair (probe, reset) behind the family-neutral `ImportGate`."""
    calls: list[str] = []

    def probe() -> docker.ImportState:
        calls.append("probe")
        return HALF_WRITTEN

    def reset() -> tuple[str, ...]:
        calls.append("reset")
        return ("acore_world",)

    gate: native.ImportGate = native.CallableGate(probe, reset)
    assert gate.probe() is HALF_WRITTEN
    assert gate.reset() == ("acore_world",)
    assert calls == ["probe", "reset"]
    with pytest.raises(InstallerError, match="no way to clear"):
        native.CallableGate(probe, None).reset()


# -- the spine's own machinery ----------------------------------------------


def _family(
    stages_of: Callable[[native.StagedInstaller], tuple[native.Stage, ...]],
) -> type[native.StagedInstaller]:
    """A throwaway family for one test: WotLK's entry and seams, any stage tuple."""

    class Family(native.StagedInstaller):
        family = "azerothcore"

        def stages(self) -> tuple[native.Stage, ...]:
            return stages_of(self)

    return Family


def _build(
    rec: Recorder,
    family: type[native.StagedInstaller],
    entry: CatalogEntry = ENTRY,
    **overrides: object,
) -> native.StagedInstaller:
    return family(
        entry,
        installers_root=resources.installers_dir(),
        import_probe=rec.probe,
        reset_unfinished=rec.reset,
        seams=rec.seams(**overrides),
    )


def _say(ctx: native.StageContext) -> Iterator[str]:
    yield f"in {ctx.server_dir.name}"


# -- the stage tuple --------------------------------------------------------


def test_families_maps_each_id_to_its_class_and_an_unknown_one_is_a_sentence() -> None:
    assert FAMILIES["azerothcore"] is AzerothCoreInstaller
    assert family_for(ENTRY) is AzerothCoreInstaller
    with pytest.raises(InstallerError, match="install.native"):
        family_for(TBC)  # no native block yet in 7.1


def test_a_family_must_agree_with_the_entry_about_its_family(tmp_path: Path) -> None:
    """`family` is asserted against catalog data before anything is written."""

    class Wrong(native.StagedInstaller):
        family = "cmangos"

        def stages(self) -> tuple[native.Stage, ...]:
            return (native.Stage("only", _say),)

    rec = Recorder()
    with pytest.raises(InstallerError, match="cmangos"):
        list(_build(rec, Wrong).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "clone" not in " ".join(rec.calls)


def test_a_stage_tuple_may_not_repeat_a_name_or_claim_preflight_or_guard() -> None:
    """A broken family is a `ValueError` at construction — a bug, not a user's sentence."""
    rec = Recorder()
    with pytest.raises(ValueError, match="twice"):
        _build(rec, _family(lambda me: (native.Stage("a", _say), native.Stage("a", _say))))
    with pytest.raises(ValueError, match="guard"):
        _build(rec, _family(lambda me: (native.Stage("guard", _say),)))
    with pytest.raises(ValueError, match="preflight"):
        _build(rec, _family(lambda me: (native.Stage("preflight", _say),)))


def test_an_unrecorded_stage_is_never_written_down(tmp_path: Path) -> None:
    rec = Recorder()
    family = _family(
        lambda me: (native.Stage("always", _say), native.Stage("never", _say, recorded=False))
    )
    server_dir = tmp_path / "wow"
    lines = list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))
    assert lines.count("in wow") == 2
    state = native.read_state(server_dir, valid=("always", "never"))
    assert state is not None
    assert state.completed == ("always",)
    assert state.family == "azerothcore"


def test_the_cancel_note_is_said_by_the_spine_right_after_the_stage_heading(
    tmp_path: Path,
) -> None:
    """A4: the spine yields `cancel_note` once, immediately after `--- <name>`."""
    rec = Recorder()
    family = _family(
        lambda me: (
            native.Stage("first", _say),
            native.Stage("second", _say, cancel_note="Stopping here costs nothing."),
        )
    )
    lines = list(_build(rec, family).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert lines.count("Stopping here costs nothing.") == 1
    assert lines.index("Stopping here costs nothing.") == lines.index("--- second") + 1
    assert lines.index("Stopping here costs nothing.") > lines.index("--- first")


# -- the guard: family --------------------------------------------------------


def test_a_folder_installed_as_another_family_is_refused_not_reinterpreted(
    tmp_path: Path,
) -> None:
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(server_dir, platform_id=lambda: "macos"),
            family="cmangos",
        ),
    )
    rec = Recorder()
    with pytest.raises(InstallerError, match="installed as `cmangos`"):
        install(rec, server_dir)
    assert "clone" not in " ".join(rec.calls)


def test_a_state_file_without_a_family_is_adopted_and_gains_the_current_one(
    tmp_path: Path,
) -> None:
    """Files from before 7.1 have no `family`; they are ours, and the next write says so."""
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    (server_dir / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": ENTRY.id,
                "install_id": composegen.install_id(server_dir, platform_id=lambda: "macos"),
                "completed": [],
            }
        ),
        encoding="utf-8",
    )
    install(Recorder(images=False), server_dir)
    state = native.read_state(server_dir, valid=AzerothCoreInstaller.STAGE_NAMES)
    assert state is not None
    assert state.family == "azerothcore"


# -- preflight ----------------------------------------------------------------


def test_preflight_hands_the_client_dir_to_gather(tmp_path: Path) -> None:
    """A9: `gather()` is told the client folder from 7.1 on; 7.3 starts checking it."""
    rec = Recorder(images=False)
    seen: dict[str, object] = {}

    def gather(entry: object, server_dir: Path, **kwargs: object) -> preflight.Facts:
        seen.update(kwargs)
        return rec.gather(entry, server_dir)

    installer = _build(rec, AzerothCoreInstaller, gather=gather)
    client = tmp_path / "client"
    installer.preflight(InstallOptions(server_dir=tmp_path / "wow", client_dir=client))
    assert seen["client_dir"] == client
    assert seen["platform_id"] is installer._seams.platform_id
    assert seen["docker_ready"] is installer._seams.docker_ready


def test_the_real_gather_takes_the_client_dir_and_ignores_it_until_7_3(tmp_path: Path) -> None:
    """The seam's default must accept what the spine passes it, or only the double does.

    `Seams.gather` is typed `Callable[..., Facts]`, so mypy cannot see this
    keyword at the seam; the call is covered here instead (A.2 review finding).
    """
    facts = preflight.gather(
        ENTRY,
        tmp_path,
        client_dir=tmp_path / "client",
        platform_id=lambda: "macos",
        docker_ready=lambda: False,
    )
    assert facts.platform_id == "macos"
    assert facts.docker_ready is False


# -- secrets ------------------------------------------------------------------


def test_a_fixed_password_is_the_catalog_value(tmp_path: Path) -> None:
    assert _build(Recorder(), AzerothCoreInstaller).resolve_secrets(tmp_path) == native.Secrets(
        ENTRY.install.password.value or ""
    )


def test_a_generated_password_is_read_from_its_file_or_minted_with_the_prefix(
    tmp_path: Path,
) -> None:
    """The spine resolves before stage 1; a family's `db-password` stage only persists (A8)."""
    plan = TBC.install.password
    assert plan.mode == "generated" and plan.file is not None
    installer = AzerothCoreInstaller(TBC, seams=Recorder().seams())
    minted = installer.resolve_secrets(tmp_path).db_password
    assert minted.startswith(plan.prefix) and len(minted) == len(plan.prefix) + 16
    assert re.fullmatch(r"[0-9a-f]{16}", minted[len(plan.prefix) :])
    (tmp_path / plan.file).write_text("tbc-kept\n", encoding="utf-8")
    assert installer.resolve_secrets(tmp_path).db_password == "tbc-kept"


def test_a_password_file_that_does_not_start_with_the_prefix_is_still_the_password(
    tmp_path: Path,
) -> None:
    """`prefix` decorates what this app MINTS; it is not a shape an existing file must have.

    The shipped bash installers minted these without the dash `catalog.json`
    carries, so a folder installed before 7.1 holds a password the prefix rule
    would reject — and rejecting it would mean the install could not talk to
    its own database (A.2 review finding).
    """
    plan = TBC.install.password
    assert plan.file is not None and plan.prefix
    installer = AzerothCoreInstaller(TBC, seams=Recorder().seams())
    (tmp_path / plan.file).write_text("no-prefix-here\n", encoding="utf-8")
    assert installer.resolve_secrets(tmp_path).db_password == "no-prefix-here"


# -- the spine's own stage bodies ---------------------------------------------


def test_stage_clone_sources_clones_every_source_at_its_dest_and_refuses_what_it_does_not_own(
    tmp_path: Path,
) -> None:
    """The CMaNGOS family's clone stage; AzerothCore keeps its two historical ones."""
    rec = Recorder()
    family = _family(
        lambda me: (
            native.Stage(
                "clone-sources",
                lambda ctx: me.stage_clone_sources(ctx, me.entry.emulator.sources),
            ),
        )
    )
    server_dir = tmp_path / "wow"
    list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))
    assert [spec.dest for spec in rec.clones] == [
        server_dir / source.dest for source in ENTRY.emulator.sources
    ]
    assert rec.clones[0].dest == server_dir  # dest "." IS the server dir

    hand_made = tmp_path / "wow2" / ENTRY.emulator.sources[1].dest
    hand_made.mkdir(parents=True)
    (hand_made / "my-own-patches.cpp").write_text("mine", encoding="utf-8")
    again = Recorder()
    (tmp_path / "wow2" / ".git").mkdir()
    again.remotes[tmp_path / "wow2"] = ENTRY.emulator.sources[0].url
    with pytest.raises(InstallerError, match="has files in it but is not a checkout"):
        list(_build(again, family).run(InstallOptions(server_dir=tmp_path / "wow2")))
    assert (hand_made / "my-own-patches.cpp").read_text(encoding="utf-8") == "mine"


def test_stage_start_db_starts_the_database_even_for_an_entry_with_no_import_service(
    tmp_path: Path,
) -> None:
    """A7: the spine's start-db is family-neutral — CMaNGOS has no `db_import` and needs it."""
    no_import = ENTRY.model_copy(
        update={"containers": ENTRY.containers.model_copy(update={"db_import": None})}
    )
    rec = Recorder()
    family = _family(lambda me: (native.Stage("start-db", me.stage_start_db, recorded=False),))
    list(_build(rec, family, no_import).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "start-db" in rec.calls
    assert rec.db_started is True


def _gated(rec: Recorder) -> type[native.StagedInstaller]:
    """start-db, then an import stage with NO service: the branch table, then the family's SQL."""

    def sql(me: native.StagedInstaller) -> Callable[[native.StageContext], Iterator[str]]:
        def body(ctx: native.StageContext) -> Iterator[str]:
            yield from me.stage_import(ctx, native.CallableGate(rec.probe, rec.reset), None)
            yield "applying sql"

        return body

    return _family(
        lambda me: (
            native.Stage("start-db", me.stage_start_db, recorded=False),
            native.Stage("sql", sql(me)),
        )
    )


def test_stage_import_without_a_service_runs_the_branch_table_then_returns(
    tmp_path: Path,
) -> None:
    """A7: the five-branch probe table always runs; only the one-shot + verify need a service."""
    rec = Recorder(probe_answers=[PARTIAL])
    lines = list(_build(rec, _gated(rec)).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert rec.calls.index("start-db") < rec.calls.index("probe") < rec.calls.index("reset")
    assert "verify" not in rec.calls
    assert not any(call.startswith("one-shot:") for call in rec.calls)
    assert "applying sql" in lines
    assert any(line.startswith("Cleared acore_world") for line in lines)


def test_stage_import_without_a_service_still_skips_an_imported_database(
    tmp_path: Path,
) -> None:
    rec = Recorder(probe_answers=[IMPORTED])
    lines = list(_build(rec, _gated(rec)).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "reset" not in rec.calls
    assert any("already imported" in line for line in lines)


def test_stage_import_without_a_service_still_refuses_an_unreadable_database(
    tmp_path: Path,
) -> None:
    rec = Recorder(db_start_error="")
    family = _family(
        lambda me: (
            native.Stage(
                "sql",
                lambda ctx: me.stage_import(ctx, native.CallableGate(rec.probe, rec.reset), None),
            ),
        )
    )
    # No start-db stage ran, so the Recorder's probe answers `unreadable`, as the real one does.
    with pytest.raises(InstallerError, match="could not be asked"):
        list(_build(rec, family).run(InstallOptions(server_dir=tmp_path / "wow")))
    assert "reset" not in rec.calls


def test_ready_markers_are_filled_and_escaped_unless_the_catalog_says_regex(
    tmp_path: Path,
) -> None:
    """A3/A5: `{{REALM_HOST}}:{{WORLD_PORT}}` is filled from `INSTALL_REALM_HOST`, then escaped."""
    assert native.INSTALL_REALM_HOST == "127.0.0.1"
    rec = Recorder(images=False)
    seen: list[docker.ReadySpec] = []

    def wait_ready(spec: docker.ContainerSpec, ready: docker.ReadySpec) -> bool:
        seen.append(ready)
        return True

    install(rec, tmp_path / "wow", wait_ready=wait_ready)
    assert ENTRY.install.native is not None
    markers = ENTRY.install.native.ready
    assert markers.regex is False
    tokens = {"REALM_HOST": native.INSTALL_REALM_HOST, "WORLD_PORT": str(ENTRY.ports.world)}
    assert seen[0].world == re.escape(composegen.fill(markers.world, tokens))
    assert markers.auth is not None
    filled_auth = composegen.fill(markers.auth, tokens)
    assert seen[0].auth == re.escape(filled_auth)
    assert re.search(seen[0].auth, filled_auth)
    assert not re.search(seen[0].auth, filled_auth.replace(".", "x"))
    assert seen[0].timeout == float(markers.timeout_s)
    assert seen[0].restart_loop == markers.restart_loop

    literal = _build(rec, AzerothCoreInstaller)._ready_spec(
        ReadyMarkers(world="World initialized|Ready", auth=None, regex=True)
    )
    assert literal.world == "World initialized|Ready"
    assert literal.auth is None


def test_the_catalogue_timeout_wins_over_the_docker_default() -> None:
    """Two contract-mandated numbers meet here: 600 from the data, 480 in `docker.py`.

    `ReadyMarkers.timeout_s` is 600 and `docker.ReadySpec`'s own default is
    480. A spec built from catalogue data uses the data (A.2 review finding),
    and the numbers must actually differ or this pins nothing.
    """
    spec = _build(Recorder(), AzerothCoreInstaller)._ready_spec(ReadyMarkers(world="ready..."))
    assert docker.ReadySpec(world="x").timeout == 480.0
    assert spec.timeout == 600.0


def test_a_broken_ready_pattern_is_refused_where_it_is_read_not_mid_poll() -> None:
    """A bad regex from `catalog.json` must not raise `re.error` 40 minutes into an install.

    `regex: true` hands the string to `re.search` as written, so an unbalanced
    group reaches the daemon poll and dies there — after the build, after the
    import, inside `wait_ready()`. Compiled here instead, and the sentence
    names the file to fix (A.2 review finding).
    """
    installer = _build(Recorder(), AzerothCoreInstaller)
    with pytest.raises(InstallerError, match="catalog.json"):
        installer._ready_spec(ReadyMarkers(world="World (initialized", regex=True))
    with pytest.raises(InstallerError, match="catalog.json"):
        installer._ready_spec(ReadyMarkers(world="ready...", fatal="*nope", regex=True))


def test_an_unfillable_ready_marker_is_a_sentence_not_a_traceback() -> None:
    """`fill()` raises `ComposeGenError` for a token nobody fills; the engine words it."""
    installer = _build(Recorder(), AzerothCoreInstaller)
    with pytest.raises(InstallerError, match="ready markers are broken"):
        installer._ready_spec(ReadyMarkers(world="{{NO_SUCH_TOKEN}}"))


def test_pumped_output_arrives_in_order_and_before_the_stage_ends(tmp_path: Path) -> None:
    """`_pump` streams a push-style docker call; nothing is collected into a list first."""
    rec = Recorder(images=False)
    lines = install(rec, tmp_path / "wow")
    assert lines.index("compiling") < lines.index("The build finished.")
    assert lines.index("--- build") < lines.index("compiling")
