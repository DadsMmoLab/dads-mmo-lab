"""Tests for the game-free install spine (`yulon.catalog.native.StagedInstaller`, roadmap 7.1).

What is asserted here is true of EVERY family: the state file and its hint
semantics, the guard, ask-forwarding, streaming, and what a stage tuple may
and may not contain. Anything AzerothCore-shaped lives in
`test_families_azerothcore.py`. The machine double is shared:
`tests/support_native.py`.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import shutil
import subprocess
import threading
import traceback
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from tests.support_native import ENTRY, IMPORTED, PARTIAL, TBC, Recorder, install
from yulon import docker, install_wiring, platform, resources
from yulon.apply import ApplyError
from yulon.catalog import composegen, native, preflight
from yulon.catalog.catalog import CatalogEntry, ReadyMarkers, load_catalog
from yulon.catalog.families import FAMILIES, azerothcore, family_for
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import (
    DockerUnavailableError,
    InstallerError,
    InstallOptions,
    installer_for,
)
from yulon.controller_wow_wotlk.maintenance import MaintenanceError

ORDER = ("clone-sources", "build", "import", "up")
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


def test_an_unknown_stage_name_survives_a_read_and_a_write(tmp_path: Path) -> None:
    """A downgrade must not strip the newer build's progress off disk.

    `read_state` filtered unknown names out and `write_state` then persisted the
    filtered tuple, so an older Yu'lon opening a newer install PERMANENTLY removed
    the names it did not recognise — and the "Already finished: ..." line printed
    the filtered list, so nothing said so. Resuming on the newer build afterwards
    redid whatever those stages were, which for this family includes a multi-hour
    compile. Bug-checklist section 23; fixed 2026-09-02.

    Asserts the round trip through the FILE rather than the object, because the
    file is what the other build reads. The two halves stay separate on the way
    in — this build must not act on a stage it cannot interpret — and are rejoined
    on the way out.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "a-stage-from-the-future"],
            }
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    assert state.completed == ("build",), "an uninterpretable stage must not become a skip"
    assert state.unknown == ("a-stage-from-the-future",)

    native.write_state(tmp_path, state)
    on_disk = json.loads((tmp_path / native.STATE_FILE).read_text(encoding="utf-8"))["completed"]
    assert (
        "a-stage-from-the-future" in on_disk
    ), "the older build wrote the newer build's progress out of existence"


def test_recording_a_stage_does_not_drop_the_names_this_build_cannot_read(
    tmp_path: Path,
) -> None:
    """The lossy path was a WRITE, so the write that happens every stage is the one to pin.

    `with_stage()` rebuilds `completed` from `order`, and a future name is by
    definition not in `order`. If `unknown` did not ride along beside it, the very
    first stage an older build completed would erase the newer one's record — the
    same defect as the read-side filter, reached by the commoner route.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["clone-core", "a-stage-from-the-future"],
            }
        ),
        encoding="utf-8",
    )
    state = native.read_state(tmp_path, valid=ORDER)
    assert state is not None
    native.write_state(tmp_path, state.with_stage("build", ORDER))

    on_disk = json.loads((tmp_path / native.STATE_FILE).read_text(encoding="utf-8"))["completed"]
    assert "build" in on_disk and "clone-core" in on_disk, "the ordinary record still works"
    assert "a-stage-from-the-future" in on_disk, "recording a stage erased the future one"


def test_a_stage_this_build_cannot_read_is_reported_rather_than_dropped_in_silence(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Keeping the name is not enough if nobody is told why a resume looks short.

    The user-facing "Already finished" line prints `completed`, which by design
    excludes these. Without a log line the only visible symptom is an install that
    appears to have done less than it did, with nothing naming the cause.
    """
    (tmp_path / native.STATE_FILE).write_text(
        json.dumps(
            {
                "version": 1,
                "game_id": "wow-tbc",
                "install_id": "abc",
                "completed": ["build", "a-stage-from-the-future"],
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="yulon.catalog.native"):
        native.read_state(tmp_path, valid=ORDER)
    said = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "a-stage-from-the-future" in m for m in said
    ), f"the dropped name was never reported: {said}"


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


def test_ownership_is_three_answers_and_a_file_nobody_can_read_is_never_the_middle_one(
    tmp_path: Path,
) -> None:
    """`read_claim()` is the ONE place a folder becomes an ownership answer.

    It exists because two places answered and they disagreed on exactly one
    input. `read_state()` said `None` for a file it could not parse — correct
    for "which stages may I skip?", since a hint that cannot be read is no hint
    — and `claimed_this_folder()` said `is_file()`, which is `True` for the same
    input. A bool has nowhere to put "the file is there and I cannot read it",
    so the two questions shared one answer and the corrupt case fell out of the
    gap with `git reset --hard` behind it.

    Absent is `UNCLAIMED`, parsed is `OWNED`, present-and-unreadable is
    `UNKNOWN`, and `UNKNOWN` must never be worth more than `UNCLAIMED`.
    """
    good = tmp_path / "ours"
    good.mkdir()
    native.write_state(good, native.InstallState("wow-tbc", "abc", "cmangos"))
    empty = tmp_path / "empty"
    empty.mkdir()

    assert native.read_claim(empty, valid=ORDER).ownership is native.Ownership.UNCLAIMED
    assert native.read_claim(empty, valid=ORDER).state is None
    owned = native.read_claim(good, valid=ORDER)
    assert owned.ownership is native.Ownership.OWNED
    assert owned.state is not None and owned.state.install_id == "abc"

    damages = ("", "{not json", "[]", '{"version": 1}', "\x00\x00")
    for number, damage in enumerate(damages):
        broken = tmp_path / f"broken{number}"
        broken.mkdir()
        (broken / native.STATE_FILE).write_text(damage, encoding="utf-8")
        claim = native.read_claim(broken, valid=ORDER)
        assert claim.ownership is native.Ownership.UNKNOWN, damage
        assert claim.state is None, damage
        # And the hint reader keeps its own contract on the same input, which is
        # what made the two answers look interchangeable in the first place.
        assert native.read_state(broken, valid=ORDER) is None, damage


def _a_newer_builds_state_file(server_dir: Path) -> dict[str, object]:
    """What a Yu'lon one version ahead of this one would have written here.

    Additive keys and an additive stage name, because `STATE_VERSION`'s own
    docstring names additive keys as the evolution path - and they are exactly
    what a rewrite from this build destroyed.
    """
    return {
        "version": native.STATE_VERSION + 1,
        "game_id": ENTRY.id,
        "family": AzerothCoreInstaller.family,
        "install_id": composegen.install_id(server_dir, platform_id=lambda: "macos"),
        "completed": ["clone-core", "rotate-secrets"],
        "last_error": "",
        "updated_unix": 1756000000,
        "client_dir": "/home/somebody/wow-client",
        "secrets_rotated_unix": 1756800000,
    }


def test_a_state_file_from_a_newer_build_is_the_unknown_ownership_nothing_produced(
    tmp_path: Path,
) -> None:
    """`Ownership.UNKNOWN` names "written by a version this one cannot read" as one of its cases.

    Until 2026-09-02 no code path produced it: `_parse_state()` read `version`
    and never compared it to anything, so a file from a newer build parsed as
    `OWNED` and was resumed. This is that case's producer.

    Its neighbour is the version this build DOES understand, and the boundary
    is `>` rather than `>=`: a file at `STATE_VERSION` must still be `OWNED`,
    which is every install anyone has today.
    """
    newer = tmp_path / "newer"
    newer.mkdir()
    (newer / native.STATE_FILE).write_text(
        json.dumps(_a_newer_builds_state_file(newer)), encoding="utf-8"
    )
    claim = native.read_claim(newer, valid=AzerothCoreInstaller.STAGE_NAMES)
    assert claim.ownership is native.Ownership.UNKNOWN
    assert claim.state is None
    assert native.read_state(newer, valid=AzerothCoreInstaller.STAGE_NAMES) is None

    current = tmp_path / "current"
    current.mkdir()
    at_this_version = _a_newer_builds_state_file(current) | {"version": native.STATE_VERSION}
    (current / native.STATE_FILE).write_text(json.dumps(at_this_version), encoding="utf-8")
    same = native.read_claim(current, valid=AzerothCoreInstaller.STAGE_NAMES)
    assert same.ownership is native.Ownership.OWNED, "the boundary is `>`, not `>=`"


def test_a_newer_builds_state_file_is_refused_and_left_byte_for_byte(tmp_path: Path) -> None:
    """A record this build cannot interpret is not rewritten, and says so in its own words.

    Measured on `f6ed1b9a` with a v2 file resumed by this v1 build:

        keys lost            : ['client_dir', 'secrets_rotated_unix']
        version still claims : 2
        unknown stage kept   : True

    `write_state()` rebuilds the payload from a fixed set of keys, so a key it
    does not know is dropped - and it wrote `version` back UNCHANGED, which made
    the loss undetectable to the newer build afterwards: the file still claimed
    to be a v2 record while no longer being one. Section 23 preserved unknown
    stage NAMES and left the keys open; this closes it by not rewriting the file
    at all.

    The neighbour is the generic unreadable-file refusal, which tells the user
    to DELETE the file - the worst possible advice about a working newer
    install. It is pinned out by name, not merely hoped past.
    """
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    path = server_dir / native.STATE_FILE
    path.write_bytes(
        (json.dumps(_a_newer_builds_state_file(server_dir), indent=2) + "\n").encode("utf-8")
    )
    before = path.read_bytes()
    rec = Recorder(images=False)
    with pytest.raises(InstallerError, match="newer version of Yu'lon") as caught:
        install(rec, server_dir)
    said = str(caught.value)
    assert path.read_bytes() == before, "the newer build's record was rewritten"
    assert rec.calls == [], "the machine was measured before the record was read"
    assert "delete it and try again" not in said, (
        "that is the generic unreadable-file refusal, and deleting a newer "
        "install's record is exactly what must not be advised here"
    )
    kept = json.loads(path.read_text(encoding="utf-8"))
    assert kept["client_dir"] == "/home/somebody/wow-client"
    assert kept["secrets_rotated_unix"] == 1756800000
    assert kept["version"] == native.STATE_VERSION + 1
    assert "rotate-secrets" in kept["completed"]


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
        return PARTIAL

    def reset() -> tuple[str, ...]:
        calls.append("reset")
        return ("acore_world",)

    gate: native.ImportGate = native.CallableGate(probe, reset)
    assert gate.probe() is PARTIAL
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
    # TBC stood in for this refusal from G.4 until K.8, when `cmangos` was
    # registered and TBC became a family this build DOES have. The refusal is
    # about a family id with no class behind it, so the stand-in is now an entry
    # carrying one. `model_copy` does not re-validate, which is the only way past
    # `Literal["azerothcore", "cmangos"]` — and the reason no catalog file can
    # produce this state, which is why the branch is defence and not a code path
    # a user reaches.
    assert TBC.install.native is not None
    stranger = TBC.model_copy(
        update={
            "install": TBC.install.model_copy(
                update={"native": TBC.install.native.model_copy(update={"family": "nosuchlineage"})}
            )
        }
    )
    with pytest.raises(InstallerError, match="install family this app does not have"):
        family_for(stranger)
    bare = TBC.model_copy(update={"install": TBC.install.model_copy(update={"native": None})})
    with pytest.raises(InstallerError, match="install.native"):
        family_for(bare)


def test_every_shipped_native_entry_reaches_the_class_its_family_id_names() -> None:
    """Catalog, registry and engine joined to each other, instead of each to a literal.

    Three facts have to agree before an Install press reaches an engine at all:
    the entry's `install.native.family`, the class `FAMILIES` maps that id to,
    and that class's own `family` attribute — which the spine asserts against
    the entry before it writes anything (see
    `test_a_family_must_agree_with_the_entry_about_its_family`). Nothing joined
    them. On 2026-09-02 K.8 added one line to `FAMILIES` and five tests went
    red, and every one of them had written one side of that join down by hand.

    Enumerated over `load_catalog().games`, not over a list of ids: an entry
    added to `catalog.json` whose family has no engine is exactly the
    disagreement this exists for, and a hand-kept list is the one thing
    guaranteed not to notice it.

    Through `installer_for()` rather than `family_for()`, so what is proved is
    dispatch and not a dictionary lookup. Until F.3 the fallback branch handed
    an entry whose family was unregistered back to the bash `Installer`, which
    is not a `StagedInstaller` and had no `family` at all; F.3 deleted that
    branch, and the isinstance below is what would have caught it.
    """
    entries = [entry for entry in load_catalog().games if entry.install.native is not None]
    assert entries, "the shipped catalog has no native entry left to check"
    for entry in entries:
        block = entry.install.native
        assert block is not None
        engine = installer_for(entry, platform_id=lambda: "linux")
        assert isinstance(engine, native.StagedInstaller), f"{entry.id} fell back to the script"
        assert engine.family == block.family, entry.id
        assert engine.entry is entry, entry.id


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


def test_a_finished_install_says_so_last_and_says_where(tmp_path: Path) -> None:
    """The sentence a user reads when the install ends, asserted by anything at all.

    Nothing in the suite read it before 2026-09-02: `grep "is installed and
    running"` over `tests/` found no hit, so the last line of every successful
    install was free to change or disappear silently. That is not hypothetical
    here — the line exists BECAUSE it went missing once. The comment above it
    records why: this path used to log nothing at the end, and a tester on
    yulon-win11 (2026-08-28) read a seven-minute readiness wait as "the install
    was not remembered", because the only sign a run had ended was the
    compose-project pin.

    Asserts three things a support question depends on: that it is there, that it
    names the SERVER DIRECTORY (the one fact that distinguishes two installs of
    the same game), and that it is LAST — a success line with stage output after
    it reads as a run that carried on and then stopped for an unsaid reason.
    """
    rec = Recorder()
    family = _family(lambda me: (native.Stage("only", _say),))
    server_dir = tmp_path / "wow"
    lines = list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))

    ending = [line for line in lines if "is installed and running" in line]
    assert len(ending) == 1, f"expected exactly one closing line, got {ending}"
    assert str(server_dir) in ending[0], (
        "the closing line does not say which folder, so two installs of the same "
        f"game are indistinguishable in a log: {ending[0]!r}"
    )
    assert (
        lines[-1] == ending[0]
    ), f"something is said after the install claims to be finished: {lines[-1]!r}"


def test_a_failed_install_never_says_it_finished(tmp_path: Path) -> None:
    """The other half, which is the half that would actually hurt.

    A closing line that survives a raising stage would tell a user their server is
    installed and running when it is not, and send them looking for a working
    install rather than at the error. Pinned separately from the success case
    because the two can fail independently: moving the yield above the `try` would
    keep this green while breaking the ordering, and moving it inside the `try`
    would keep the ordering while breaking this.
    """
    rec = Recorder()

    def _boom(ctx: native.StageContext) -> Iterator[str]:
        yield "starting"
        raise InstallerError("the build died")

    family = _family(lambda me: (native.Stage("build", _boom),))
    engine = _build(rec, family)
    said: list[str] = []
    with pytest.raises(InstallerError):
        for line in engine.run(InstallOptions(server_dir=tmp_path / "wow")):
            said.append(line)

    assert not [
        line for line in said if "is installed and running" in line
    ], f"a failed install announced success: {said}"


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


def test_a_state_file_the_guard_cannot_read_stops_the_install_instead_of_starting_fresh(
    tmp_path: Path,
) -> None:
    """`Ownership.UNKNOWN` fails closed, and this is where it does it first.

    Ignoring it made this the FRESH-install path — every `install_id`,
    `game_id`, `family` and not-empty refusal in `_guard()` is written `if
    existing is not None and …` — while `claimed_this_folder()` was
    simultaneously calling the same folder ours. Two answers, one input, and
    `git reset --hard` on the far side of them.

    The failure modes are not exotic enough to gamble on: a crash mid-write, a
    file damaged by hand or by a backup tool, a version this build cannot read,
    and an unrelated file that happens to sit at the reserved name all arrive
    here identically. An atomic writer covers exactly one of the four.
    """
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    (server_dir / native.STATE_FILE).write_text("{not json", encoding="utf-8")
    rec = Recorder(images=False)
    with pytest.raises(InstallerError, match="cannot read"):
        install(rec, server_dir)
    # Nothing ran at all - no machine check, no clone, no build, no container.
    # A flag set to the right value would not be evidence of that; an empty call
    # log is. It read `["gather"]` until 2026-09-02, which recorded the defect as
    # if it were the contract: the machine was measured, and Docker provisioned
    # first, before this folder was judged - and the refusal then says `Nothing
    # was written` about a machine that had just had packages installed on it.
    assert rec.calls == [], rec.calls
    # Never deleted or rewritten: a file this engine cannot read may not be its
    # own, and the message asks the user to decide.
    assert (server_dir / native.STATE_FILE).read_text(encoding="utf-8") == "{not json"


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
    # The folder rule as well, since C1. The engine now refuses a bad folder
    # before it provisions anything, and `evaluate()` still judges the same
    # folder from `Facts`; handing the seam down is what stops those two
    # asking different functions - an engine could otherwise refuse a folder
    # that its own report went on to pass.
    assert seen["dir_problem"] is installer._seams.dir_problem


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


def test_the_real_gather_takes_the_folder_rule_the_engine_hands_it(tmp_path: Path) -> None:
    """The seam the engine threads must reach `Facts`, not a default underneath it.

    The same blind spot as the client-dir test above: `Seams.gather` is typed
    `Callable[..., Facts]`, so mypy sees no keyword at the seam and the double
    in `support_native.py` swallows every kwarg. A `dir_problem=` the real
    `gather()` silently ignored would leave the engine refusing a folder its own
    report then passed, and only this call would notice.
    """
    facts = preflight.gather(
        ENTRY,
        tmp_path,
        platform_id=lambda: "macos",
        docker_ready=lambda: False,
        dir_problem=lambda path: f"{path} is the canary folder",
    )
    assert facts.dir_problem == f"{tmp_path} is the canary folder"


def test_preflight_refuses_a_reserved_folder_before_it_provisions_anything() -> None:
    """The folder rule fires BEFORE `ensure_docker()`, not from the report after it.

    Both halves of preflight know this rule and until C1 only the late one ran:
    `evaluate()` reads `Facts.dir_problem`, `Facts` come from `gather()`, and
    `gather()` runs after provisioning. Picking the home folder on a clean Linux
    box therefore bought the docker-group consent dialog, a sudo password typed
    into Yu'lon's own dialog and a package install, and only then "Cannot use
    '/home/pk' as the install location" - measured on Fedora 44, 2026-08-25,
    against the shell installer this engine replaced. The native engine
    inherited that order in 7.1, and F.3 deleted the last test that named it.

    `provisioned == []` is the assertion that matters, and the doubles are
    arranged so that it is the one that fails. The `gather` double reads the
    folder rule through the seam it is handed, exactly as the real one does, so
    an engine with the early check removed STILL refuses with these words and
    still matches `home folder itself`; the only thing that changes is that it
    refuses second. A refusal test that stopped at `pytest.raises` would pass
    against that engine, and would be a test of the union of every rule on this
    path rather than of one of them.
    """
    rec = Recorder(images=False)
    provisioned: list[str] = []
    gathered: list[Path] = []

    def ensure_docker(**_kwargs: object) -> platform.ProvisionReport:
        provisioned.append("ensure_docker")
        # The generous answer - a provisioner that SUCCEEDED - so that nothing
        # further down refuses for a reason of its own and the order is the only
        # thing this test can fail on.
        return platform.ProvisionReport(platform="linux", docker_ready=True)

    def gather(entry: object, server_dir: Path, **kwargs: object) -> preflight.Facts:
        gathered.append(server_dir)
        rule = kwargs["dir_problem"]
        assert callable(rule)
        return replace(rec.gather(entry, server_dir), dir_problem=rule(server_dir))

    installer = _build(
        rec,
        AzerothCoreInstaller,
        # No daemon answering, so provisioning is the very next thing this
        # engine would do - which is what makes an empty list evidence.
        docker_ready=lambda: False,
        ensure_docker=ensure_docker,
        gather=gather,
    )
    home = Path.home()
    assert platform.server_dir_problem(home) is not None, "the rule under test must hold here"
    with pytest.raises(InstallerError, match="home folder itself") as caught:
        installer.preflight(InstallOptions(server_dir=home))
    assert provisioned == [], "a sudo/consent dialog was reached before the folder was judged"
    assert gathered == [], "the machine was measured before the folder was judged"
    # preflight's own words, so the early refusal and the late one cannot drift.
    assert "Pick a different folder and try again. Nothing was written." in str(caught.value)


# -- the guard rules that need no daemon -------------------------------------
#
# `_guard()` ran in one piece AFTER `_preflight_lines()` had finished - which
# means after `ensure_docker()` (a sudo password, the docker-group consent and
# a package install) and after `gather()` (docker ps, a port scan, and a
# bind-mount probe that pulls an image). Most of its rules are pure filesystem
# reads. Recorded seam order on a real run, 2026-09-02:
#
#     dir_problem -> None | "Checking Docker." | docker_ready -> False
#     ensure_docker   <- sudo password + docker-group consent + package install
#     preflight.gather
#     REFUSED: ...\server is not empty and was not created by this app.
#              Nothing was written.
#
# "Nothing was written" was untrue of the machine by the time it was said.
# `7cb3bf17` hoisted exactly one sibling rule, `dir_problem`, and left these
# below it; the suite's only ordering assertion covered that one rule.
#
# `_refuse_foreign_containers()` is deliberately NOT among them and stays late:
# it asks the daemon which compose project owns a container wearing our names,
# so it cannot be answered before there is a daemon. Every case below asserts
# it was not reached either, which is what keeps this parametrisation from
# quietly growing a rule that has to stay where it is.


def _state_file_at(server_dir: Path, **fields: object) -> None:
    """A state file whose `install_id` is the one this folder really has, plus `fields`."""
    server_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {
        "version": native.STATE_VERSION,
        "game_id": ENTRY.id,
        "family": AzerothCoreInstaller.family,
        "install_id": composegen.install_id(server_dir, platform_id=lambda: "macos"),
        "completed": [],
    }
    payload.update(fields)
    (server_dir / native.STATE_FILE).write_text(json.dumps(payload), encoding="utf-8")


def _an_unreadable_state_file(server_dir: Path) -> None:
    server_dir.mkdir(parents=True)
    (server_dir / native.STATE_FILE).write_text("{ not json at all", encoding="utf-8")


def _a_record_made_for_another_folder(server_dir: Path) -> None:
    _state_file_at(server_dir, install_id="made-somewhere-else")


def _another_games_install(server_dir: Path) -> None:
    _state_file_at(server_dir, game_id=TBC.id)


def _an_install_of_another_family(server_dir: Path) -> None:
    _state_file_at(server_dir, family="cmangos")


def _a_folder_with_somebody_elses_files(server_dir: Path) -> None:
    server_dir.mkdir(parents=True)
    (server_dir / "somebody-elses-work.txt").write_text("mine", encoding="utf-8")


DAEMONLESS_GUARD_RULES = (
    pytest.param(_an_unreadable_state_file, "cannot read", id="unreadable-state-file"),
    pytest.param(
        _a_record_made_for_another_folder, "made for a different folder", id="copied-folder"
    ),
    pytest.param(_another_games_install, "already holds an install of", id="another-game"),
    pytest.param(_an_install_of_another_family, "was installed as", id="another-family"),
    pytest.param(
        _a_folder_with_somebody_elses_files,
        "is not empty and was not created by this app",
        id="not-empty",
    ),
)
"""Every `_guard()` rule that is a pure filesystem read, with the words unique to it.

The words matter as much as the arrangement: five times in this phase a test
passed because a NEIGHBOURING rule refused first, and every rule here has four
neighbours that would also refuse this folder if the arrangement were sloppy.
"""


@pytest.mark.parametrize(("arrange", "words"), DAEMONLESS_GUARD_RULES)
def test_a_guard_rule_that_needs_no_daemon_refuses_before_docker_is_provisioned(
    arrange: Callable[[Path], None], words: str, tmp_path: Path
) -> None:
    """A refusal that says "Nothing was written" must be true of the machine when it is said.

    The generous answers throughout - a provisioner that SUCCEEDS, a folder the
    hoisted `dir_problem` rule is happy with - so that nothing further down can
    refuse for a reason of its own and the ORDER is the only thing this can fail
    on. An empty `provisioned` is evidence because there is no daemon answering,
    which makes provisioning the very next thing this engine would do.
    """
    provisioned: list[str] = []
    gathered: list[Path] = []
    asked_about: list[str] = []
    rec = Recorder(images=False)

    def ensure_docker(**_kwargs: object) -> platform.ProvisionReport:
        provisioned.append("ensure_docker")
        return platform.ProvisionReport(platform="linux", docker_ready=True)

    def gather(entry: object, server_dir: Path, **_kwargs: object) -> preflight.Facts:
        gathered.append(server_dir)
        return rec.gather(entry, server_dir)

    def container_exists(name: str) -> bool:
        asked_about.append(name)
        return False

    server_dir = tmp_path / "wow"
    arrange(server_dir)
    assert platform.server_dir_problem(server_dir) is None, (
        "the arrangement must not trip `dir_problem`, the sibling rule already hoisted - "
        "it would refuse first and this would prove nothing about the rule under test"
    )
    installer = _build(
        rec,
        AzerothCoreInstaller,
        docker_ready=lambda: False,
        ensure_docker=ensure_docker,
        gather=gather,
        container_exists=container_exists,
    )
    with pytest.raises(InstallerError, match=words):
        installer.preflight(InstallOptions(server_dir=server_dir))
    assert provisioned == [], "a sudo/consent dialog was reached before the folder was judged"
    assert gathered == [], "the machine was measured before the folder was judged"
    assert asked_about == [], (
        "the daemon was asked which project owns our container names - that is "
        "`_refuse_foreign_containers()`, which is not this rule and cannot be hoisted"
    )


def test_the_guard_rule_that_needs_a_daemon_still_runs_after_provisioning(tmp_path: Path) -> None:
    """The other half of the split: hoisting all of `_guard()` would break this one.

    `_refuse_foreign_containers()` asks `container_project()` which compose
    project owns a container wearing this entry's names. There is no answer to
    that before there is a daemon, so it stays below provisioning - and an
    empty folder, which every daemonless rule above is happy with, is exactly
    the case that reaches it.
    """
    rec = Recorder(containers={ENTRY.containers.world: "somebody-elses-project"})
    provisioned: list[str] = []

    def ensure_docker(**_kwargs: object) -> platform.ProvisionReport:
        provisioned.append("ensure_docker")
        return platform.ProvisionReport(platform="linux", docker_ready=True)

    installer = _build(
        rec, AzerothCoreInstaller, docker_ready=lambda: False, ensure_docker=ensure_docker
    )
    with pytest.raises(InstallerError, match="belongs to another install"):
        list(installer.run(InstallOptions(server_dir=tmp_path / "wow")))
    assert provisioned == ["ensure_docker"], "this rule needs the daemon it was provisioned for"


# -- the forwarded prompter, all the way down to the sudo dialog ---------------
#
# The wire (`preflight(ask=...)` → `ensure_docker(ask=...)`, A4) and the thing
# it feeds (`platform.SudoSession`, C7) were built on branches that never saw
# each other, so a green suite on either side was no evidence about the join.
# Everything below drives the REAL `platform.ensure_docker()` and
# `_ensure_docker_linux()`; only the machine underneath them is a double.

SUDO_CANARY = "hunter2-sudo-canary"


class _LinuxBox:
    """A `RunCmd` for a Linux box with no Docker and a package manager to install one with.

    Answers what `ensure_docker()` really asks a machine: `docker info` fails,
    `id -nG` says the user is not in the docker group, and every `sudo -n`
    refuses with sudo's own words — the state a clean Fedora/Arch box is in,
    and the only stderr `_needs_password()` accepts.

    `needs_password=False` is the root/NOPASSWD box, where no step ever reaches
    the session and the outcome stays `unasked`.
    """

    def __init__(self, needs_password: bool = True) -> None:
        self.calls: list[list[str]] = []
        self.needs_password = needs_password

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[:2] == ["docker", "info"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "id":
            return subprocess.CompletedProcess(argv, 0, "pk wheel", "")
        if argv[:2] == ["sudo", "-n"] and self.needs_password:
            return subprocess.CompletedProcess(argv, 1, "", "sudo: a password is required")
        return subprocess.CompletedProcess(argv, 0, "", "")


def _provisions_a_linux_box(
    run: _LinuxBox, run_input: platform.RunWithInput
) -> Callable[..., platform.ProvisionReport]:
    """The `ensure_docker` seam, wired to the real function with a fake machine under it.

    The spine hands this seam `cancel` and `ask` and nothing else, which is the
    point: the fakes are bound here, so `ask` has to travel the real parameter
    rather than one the test handed in.
    """

    def provision(**kwargs: object) -> platform.ProvisionReport:
        return platform.ensure_docker(
            run=run,
            which=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None,
            user="pk",
            wait_seconds=0.0,
            run_input=run_input,
            **kwargs,  # type: ignore[arg-type]
        )

    return provision


def test_the_prompter_the_spine_forwards_is_the_one_the_sudo_dialog_asks_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The join, by identity: `ask` leaves `preflight()` and arrives inside `SudoSession`.

    Not "a prompter was called" — THE prompter. `SudoSession` is spied on at
    construction, so what is asserted is the object the engine forwarded, and
    both of the questions the module docstring promises ("both via the
    forwarded `ask`") are put to it. The password it answers with then has to
    be seen reaching `sudo` on STDIN, or the wire reaches the dialog and drops
    what the dialog says.
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: False)
    built: list[object] = []
    real_session = platform.SudoSession

    def spy(ask: object, run_input: object, **kwargs: object) -> platform.SudoSession:
        built.append(ask)
        return real_session(ask, run_input, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(platform, "SudoSession", spy)

    asked: list[str] = []

    def prompter(question: str) -> str:
        asked.append(question)
        return SUDO_CANARY if question == platform.SUDO_PASSWORD_QUESTION else "n"

    fed: list[tuple[list[str], str]] = []

    def feed(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
        fed.append((argv, text))
        return subprocess.CompletedProcess(argv, 0 if text == SUDO_CANARY + "\n" else 1, "", "")

    rec = Recorder(images=False)
    installer = _build(
        rec,
        AzerothCoreInstaller,
        docker_ready=lambda: False,
        ensure_docker=_provisions_a_linux_box(_LinuxBox(), feed),
    )
    with pytest.raises(DockerUnavailableError):
        installer.preflight(InstallOptions(server_dir=tmp_path / "wow"), ask=prompter)

    assert built == [prompter], "the session was built with something other than the forwarded ask"
    assert asked[0] == platform.DOCKER_GROUP_QUESTION.format(user="pk")
    assert platform.SUDO_PASSWORD_QUESTION in asked
    assert asked.count(platform.SUDO_PASSWORD_QUESTION) == 1  # one dialog per run, still
    # The answer went to sudo, on stdin, and never as an argv element.
    assert fed[0][0] == ["sudo", "-S", "-p", "", "-v"] and fed[0][1] == SUDO_CANARY + "\n"
    assert [argv for argv, _ in fed if any(SUDO_CANARY in part for part in argv)] == []
    # ...and every later privileged step was fed it rather than re-asking.
    assert ["sudo", "-S", "-p", "", "apt-get", "update"] in [argv for argv, _ in fed]


class _NoSudoBinary:
    """A `RunWithInput` for a box with no `sudo` at all: the exec itself fails."""

    def __call__(self, argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError(2, "No such file or directory", "sudo")


def _refuses_every_password(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(argv, 1, "", "Sorry, try again.")


def _accepts(password: str) -> platform.RunWithInput:
    def feed(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 0 if text == password + "\n" else 1, "", "")

    return feed


def _never_feeds(argv: list[str], text: str) -> subprocess.CompletedProcess[str]:
    raise AssertionError(f"a password was fed to {argv} when no step needed one")


def test_every_sudo_outcome_survives_the_trip_up_to_the_sentence_the_user_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`SudoOutcome`'s five values, driven from the Install button to the failure dialog.

    `SudoOutcome` exists because a `bool` loses the difference between "they
    said no", "the machine said no" and "nobody could be asked" — and carrying
    it is only worth anything if it is still legible after `ensure_docker()`,
    `_preflight_lines()` and `DockerUnavailableError` have each rewritten it. A
    distinction that exists in the type and dies in the message is the exact
    failure this asserts against.

    Five values, THREE sentences, and that is the right count: `unasked` and
    `verified` are the two where the privileged steps actually ran, so the user
    is told nothing about sudo at all — the same silence, for the same reason.

    `unavailable` is the one with a wrong answer waiting for it: "run them in a
    terminal with sudo" is useless advice on a box whose `sudo` is what could
    not be run, so it must not appear there (measured in a container on
    yulon-ubuntu, 2026-08-24).
    """
    monkeypatch.setattr(platform.sys, "platform", "linux")
    monkeypatch.setattr(platform, "is_steamos", lambda: False)

    def message(run: _LinuxBox, run_input: platform.RunWithInput, password: str | None) -> str:
        def prompter(question: str) -> str | None:
            return password if question == platform.SUDO_PASSWORD_QUESTION else "n"

        rec = Recorder(images=False)
        installer = _build(
            rec,
            AzerothCoreInstaller,
            docker_ready=lambda: False,
            ensure_docker=_provisions_a_linux_box(run, run_input),
        )
        with pytest.raises(DockerUnavailableError) as caught:
            installer.preflight(InstallOptions(server_dir=tmp_path / "wow"), ask=prompter)
        return str(caught.value)

    unasked = message(_LinuxBox(needs_password=False), _never_feeds, None)
    verified = message(_LinuxBox(), _accepts(SUDO_CANARY), SUDO_CANARY)
    declined = message(_LinuxBox(), _accepts(SUDO_CANARY), "")
    refused = message(_LinuxBox(), _refuses_every_password, "wrong")
    unavailable = message(_LinuxBox(), _NoSudoBinary(), SUDO_CANARY)

    # The two whose steps ran carry no sudo complaint at all. (Both still carry
    # the docker-group advice, which names `sudo usermod` for a reason of its
    # own — so this asserts the absence of the SKIP sentences, not of the word.)
    for quiet in (unasked, verified):
        assert "password" not in quiet.lower(), quiet
        assert "Some steps" not in quiet, quiet
        assert "sudo could not be run" not in quiet, quiet

    # The three that did not are three different sentences, not one house one.
    # `declined` and `refused` were the same sentence until this merge review:
    # the reason is written into `skipped` and was then thrown away by the line
    # that renders `manual_steps`, which kept only the command names.
    assert "Some steps needed a password; run them in a terminal with sudo" in declined
    assert "Some steps needed a password and sudo refused the one given" in refused
    assert "sudo could not be run, so nothing was elevated" in unavailable
    assert len({declined, refused, unavailable}) == 3

    # ...and the box with no `sudo` is never sent to a terminal to run one.
    assert "terminal with sudo" not in unavailable
    assert "needed a password" not in unavailable
    assert "Some steps did not run" in unavailable


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
                lambda ctx: me.stage_clone_sources(
                    ctx, me.entry.emulator.sources, recorded_as="clone-sources"
                ),
            ),
        )
    )
    server_dir = tmp_path / "wow"
    list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))
    assert [spec.dest for spec in rec.clones] == [
        server_dir / source.dest for source in ENTRY.emulator.sources
    ]
    assert rec.clones[0].dest == server_dir  # dest "." IS the server dir

    # The spine's own stage obeys the same rule the family's two do: a second
    # run over a recorded, present clone touches nothing (D5).
    settled = Recorder()
    for source in ENTRY.emulator.sources:
        settled.remotes[server_dir / source.dest] = source.url
    list(_build(settled, family).run(InstallOptions(server_dir=server_dir)))
    assert settled.clones == []

    hand_made = tmp_path / "wow2" / ENTRY.emulator.sources[1].dest
    hand_made.mkdir(parents=True)
    (hand_made / "my-own-patches.cpp").write_text("mine", encoding="utf-8")
    again = Recorder()
    (tmp_path / "wow2" / ".git").mkdir()
    again.remotes[tmp_path / "wow2"] = ENTRY.emulator.sources[0].url
    # This install owns wow2 and got part-way through its clone: without the
    # record, the core checkout is somebody's own repository and
    # `refuse_unowned_checkout()` stops the run one source before the module
    # this test is about.
    native.write_state(
        tmp_path / "wow2",
        native.InstallState(
            game_id=ENTRY.id,
            install_id=composegen.install_id(tmp_path / "wow2", platform_id=lambda: "macos"),
            family="azerothcore",
        ),
    )
    with pytest.raises(InstallerError, match="has files in it but is not a checkout"):
        list(_build(again, family).run(InstallOptions(server_dir=tmp_path / "wow2")))
    assert (hand_made / "my-own-patches.cpp").read_text(encoding="utf-8") == "mine"


def _owned_context(server_dir: Path) -> tuple[native.StagedInstaller, native.StageContext]:
    """An install that has claimed `server_dir`, and the context its stages are handed.

    The state file and `ctx.state` are the SAME record, which is the arrangement
    `_guard()` produces: it validated the file's `install_id`, `game_id` and
    `family` and handed the parsed object on.
    """
    state = native.InstallState(game_id=ENTRY.id, install_id="an-install-id", family="azerothcore")
    server_dir.mkdir(parents=True, exist_ok=True)
    native.write_state(server_dir, state)
    installer = _build(Recorder(), _family(lambda _me: ()))
    ctx = native.StageContext(
        server_dir=server_dir,
        client_dir=None,
        state=state,
        cancel=threading.Event(),
        secrets=native.Secrets("pw"),
    )
    return installer, ctx


def test_the_one_path_that_could_destroy_a_users_work_no_longer_needs_three_copied_guards(
    tmp_path: Path,
) -> None:
    """`refuse_unowned_checkout()` has to be safe alone, and it was not.

    Its `if remote is None: return` was safe only because
    `if has_git and existing is None: raise` was copy-pasted into
    `families/azerothcore.py` twice and `stage_clone_sources()` once — so the
    method whose own docstring calls itself "the one path in this engine that
    could still destroy a user's work" was relying on three call sites to have
    stopped first (review, 2026-08-31). `None` from `remote_url()` is not "there
    is no checkout": it is also no docker CLI, a daemon that would not answer, a
    failed pull, and — until `5c6c655c` — an SELinux denial on every enforcing
    box. A `.git` the HOST can see is the evidence that the answer is wrong.

    Called here with nothing else in the picture, which is the point: no family
    body, no stage, no guard above it.
    """
    installer, ctx = _owned_context(tmp_path / "wow")
    checkout = tmp_path / "wow"
    (checkout / ".git").mkdir(parents=True, exist_ok=True)
    with pytest.raises(InstallerError, match="would not say what it is a checkout of"):
        installer.refuse_unowned_checkout(ctx, checkout, "https://example/core.git", None)

    # And it still stands down where `None` really does mean "no checkout here",
    # which is every fresh clone: a directory that does not exist yet.
    fresh = tmp_path / "not-there-yet"
    assert installer.refuse_unowned_checkout(ctx, fresh, "https://example/core.git", None) is None


def test_a_state_file_damaged_during_the_install_is_not_ownership_either(tmp_path: Path) -> None:
    """`_guard()` refuses `UNKNOWN` before stage 1, so reaching it later means mid-run damage.

    That is precisely why `claimed_this_folder()` re-reads the folder instead of
    trusting a decision taken minutes and several stages ago — and why it
    answers three ways. The old bool could only say "the file is there", which
    for a file nobody can read is the most confident thing it could possibly
    have said.
    """
    server_dir = tmp_path / "wow"
    installer, ctx = _owned_context(server_dir)
    checkout = server_dir
    (checkout / ".git").mkdir(parents=True, exist_ok=True)
    url = "https://example/core.git"
    assert installer.claimed_this_folder(ctx) is native.Ownership.OWNED
    assert installer.refuse_unowned_checkout(ctx, checkout, url, url) is None

    (server_dir / native.STATE_FILE).write_text("", encoding="utf-8")
    assert installer.claimed_this_folder(ctx) is native.Ownership.UNKNOWN
    with pytest.raises(InstallerError, match="cannot be read"):
        installer.refuse_unowned_checkout(ctx, checkout, url, url)

    # A file that parses but names a DIFFERENT install is not ownership either:
    # something replaced it while this run was going, and the identity
    # `_guard()` validated is the only thing worth comparing it to.
    native.write_state(
        server_dir, native.InstallState(game_id=ENTRY.id, install_id="someone-else", family="")
    )
    assert installer.claimed_this_folder(ctx) is native.Ownership.UNKNOWN

    # And with no file at all it is UNCLAIMED, which refuses with the sentence
    # about a checkout this app never made — not the one about a damaged file.
    (server_dir / native.STATE_FILE).unlink()
    assert installer.claimed_this_folder(ctx) is native.Ownership.UNCLAIMED
    with pytest.raises(InstallerError, match="no record here of an install this app made"):
        installer.refuse_unowned_checkout(ctx, checkout, url, url)


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
    """Two contract-mandated numbers meet here: the data's, and 480 in `docker.py`.

    A spec built from catalogue data uses the DATA (A.2 review finding), not
    `docker.ReadySpec`'s own 480. The data's number is read from
    `ReadyMarkers` rather than restated: it was written as the literal 600 and
    that is what the field defaulted to, so raising the default to 1800 after a
    measurement disproved 600 failed this test for a reason it is not about.
    The guard below keeps it meaningful -- the two numbers must actually differ
    or this pins nothing (review, 2026-09-02).
    """
    markers = ReadyMarkers(world="ready...")
    spec = _build(Recorder(), AzerothCoreInstaller)._ready_spec(markers)
    assert docker.ReadySpec(world="x").timeout == 480.0
    assert markers.timeout_s != 480, "the data must differ from docker's default"
    assert spec.timeout == float(markers.timeout_s)


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


# -- SELinux ----------------------------------------------------------------
#
# Three facts meet in `generate-compose`: is SELinux enforcing, can this
# filesystem hold a label, and did the one-off relabel work. The engine asks
# the first two through seams and lets `platform.bind_label()` combine them, so
# the policy lives in one function and this file asserts the WIRING.

_MOUNT_MODES = frozenset(
    {
        "ro",
        "rw",
        "z",
        "Z",
        "shared",
        "rshared",
        "slave",
        "rslave",
        "private",
        "rprivate",
        "nocopy",
        "consistent",
        "cached",
        "delegated",
    }
)
"""Every mount option a compose short-syntax bind may already carry before the label."""


def _compose_text(server_dir: Path) -> str:
    """Both auto-loaded files, because `{{BIND_LABEL}}` is in the override too."""
    base = (server_dir / composegen.BASE_FILE).read_text(encoding="utf-8")
    override = (server_dir / composegen.OVERRIDE_FILE).read_text(encoding="utf-8")
    return f"{base}\n{override}"


def test_bind_mounts_are_labelled_only_when_selinux_enforces_on_a_labelable_fs(
    tmp_path: Path,
) -> None:
    """`:z` on every host bind line under enforcing SELinux; byte-identical files elsewhere.

    The two negatives carry as much weight as the positive. A `:z` written on
    Ubuntu would be harmless noise, but a `:z` written on the exFAT drive a
    user keeps their servers on makes the daemon refuse to create the
    container at all — which is why `fs_type` is asked separately from
    `selinux_enforcing` rather than assumed.
    """
    off = tmp_path / "off"
    rec_off = Recorder(images=False)
    install(rec_off, off)
    assert ":z" not in _compose_text(off)
    assert rec_off.relabelled == []

    on = tmp_path / "on"
    rec = Recorder(images=False)
    install(rec, on, selinux_enforcing=lambda: True, fs_type=lambda path: "ext4")
    assert ":z" in (on / composegen.BASE_FILE).read_text(encoding="utf-8")
    assert ":z" in (on / composegen.OVERRIDE_FILE).read_text(encoding="utf-8")
    assert rec.relabelled == [on]

    ntfs = tmp_path / "ntfs"
    rec_ntfs = Recorder(images=False)
    install(rec_ntfs, ntfs, selinux_enforcing=lambda: True, fs_type=lambda p: "ntfs")
    assert ":z" not in _compose_text(ntfs)
    # No label written, so nothing to relabel: `chcon` on a filesystem with no
    # xattrs fails anyway, and the warning line would be noise on a machine
    # whose install is fine.
    assert rec_ntfs.relabelled == []


def test_selinux_that_could_not_be_asked_arrives_as_none_and_not_as_no(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`selinux_enforcing()` has THREE answers and the middle one must survive the trip.

    `True`, `False`, `None` — and `None` means the question went unanswered,
    never "no". Both `None` and `False` end in an empty label today, so no
    rendered file can tell them apart; what this pins is that the engine hands
    `bind_label()` what the seam actually said, so a `bool(...)` or an
    `is True` at the call site is a failing test rather than a silent collapse
    that a later change to the policy would turn into a wrong install. The
    shell lineage collapsed exactly this pair and relabelled nothing on an
    enforcing Fedora while its own test passed.
    """
    asked: list[tuple[bool | None, str | None]] = []
    real = platform.bind_label

    def spy(*, enforcing: bool | None, fs_type: str | None) -> str:
        asked.append((enforcing, fs_type))
        return real(enforcing=enforcing, fs_type=fs_type)

    monkeypatch.setattr(native.platform, "bind_label", spy)
    rec = Recorder(images=False)
    install(rec, tmp_path / "wow", selinux_enforcing=lambda: None, fs_type=lambda p: "ext4")
    assert asked == [(None, "ext4")]
    assert ":z" not in _compose_text(tmp_path / "wow")
    assert rec.relabelled == []


def test_the_filesystem_is_probed_for_the_folder_the_binds_actually_live_in(
    tmp_path: Path,
) -> None:
    """`fs_type` is asked about the SERVER dir — the thing every `./` bind is relative to.

    A compose bind is resolved against the file's own directory, so the only
    filesystem whose label support matters is the one holding the server
    folder. Asking about anything else (the config dir, the home dir, cwd)
    answers a different machine's question on any box with more than one drive
    — which is the box this check exists for.
    """
    server_dir = tmp_path / "wow"
    probed: list[Path] = []

    def fs_type(path: Path) -> str | None:
        probed.append(path)
        return "btrfs"

    install(Recorder(images=False), server_dir, selinux_enforcing=lambda: True, fs_type=fs_type)
    assert probed == [server_dir]


def test_a_label_is_never_appended_to_a_bind_that_already_carries_a_mode(tmp_path: Path) -> None:
    """`:ro:z` is not a mount spec; the legal spelling is `:ro,z`, and Docker refuses the first.

    The label is spliced onto the end of a bind line as a bare suffix, so a
    host bind that already carries a mode renders `./x:/y:ro:z` and the daemon
    rejects the whole file at `up` — after the build, on the one platform that
    needs the label. No WotLK host bind carries a mode, which is exactly why
    nothing else catches it: the templates and the token convention agree by
    luck rather than by rule. The CMaNGOS games (7.3) DO mount the client
    read-only, so the first template that puts `{{BIND_LABEL}}` after a mode
    steps straight onto it. This is the tripwire that makes that a red test
    here rather than a Fedora bug report; closing it properly means teaching
    `composegen.render()` the comma form, which is group B's contract and not
    this task's file.
    """
    server_dir = tmp_path / "wow"
    install(
        Recorder(images=False), server_dir, selinux_enforcing=lambda: True, fs_type=lambda p: "ext4"
    )
    labelled = [
        line.strip()
        for line in _compose_text(server_dir).splitlines()
        if line.rstrip().endswith(":z")
    ]
    assert labelled, "the enforcing render wrote no labelled bind at all"
    for line in labelled:
        already = line.rstrip()[: -len(":z")].rsplit(":", 1)[-1]
        assert already not in _MOUNT_MODES, f"{line!r} renders an illegal `:mode:z`; use `:mode,z`"


def test_a_relabel_that_fails_is_a_warning_line_not_a_refusal(tmp_path: Path) -> None:
    """`chcon` is belt to the `:z` braces: it can fail and the install still has to finish.

    `:z` relabels the mount source when the container starts, which is the
    mechanism that actually carries a Fedora install; the one-off `chcon`
    covers the files that exist before compose ever runs. Failing the install
    on it would refuse a server that works, so the line is said and the stages
    keep going — `start` is in the calls, and the sentence names the command
    to run by hand.
    """
    rec = Recorder(images=False)
    lines = install(
        rec,
        tmp_path / "wow",
        selinux_enforcing=lambda: True,
        fs_type=lambda path: "ext4",
        relabel=lambda path: False,
    )
    assert any("could not be relabelled" in line for line in lines)
    assert any("chcon -Rt container_file_t" in line for line in lines)
    assert "start" in rec.calls


# -- a refusal that is not an InstallerError ---------------------------------
#
# `run()` catches `InstallerError` and nothing else, and composegen's
# `ComposeGenError` is not one: both subclass `RuntimeError` independently, so
# neither `except` clause can ever see the other's refusal. Everything below
# drives the real `install_wiring.main()`, because the two things that break
# when such a refusal escapes a stage body are both invisible from inside one --
# the traceback the caller gets instead of a sentence, and the `last_error` that
# is then never recorded.
#
# `generate-compose` is bound to the SPINE's own body by every family, so this
# is not one game's defect. It was reproduced through the CLI on `wow-wotlk`,
# the only game with green live gates, as well as on the CMaNGOS side.

UNFILLED_TOKEN = "YULON_UNFILLED_TOKEN"
"""A placeholder no `entry_tokens()` mapping will ever hold, so `fill()` refuses on it."""

UNFILLED_PLACEHOLDER = f"{{{{{UNFILLED_TOKEN}}}}}"
"""The token as a template spells it - derived, so template and assertion cannot drift."""


def _installers_with_an_unfilled_token(entry: CatalogEntry, tmp_path: Path) -> Path:
    """A copy of the shipped installers tree whose compose template names a token nobody fills.

    `fill()`'s refusal and not `_refuse_unsafe()`'s, because this one is
    reachable on BOTH families: `wow-wotlk`'s database password is fixed in the
    catalog and safe, so the password refusal can never fire for it. It is also
    the honest shape of the real failure - an `--installers-root` pointed at an
    incomplete checkout, or a bundle that shipped short.

    Only the compose template is touched. A wholesale broken tree would be
    refused one stage earlier by CMaNGOS's `write-dockerfile`, whose
    `DockerfileError` IS translated, and the test would then go green on the
    neighbour while proving nothing about this stage.
    """
    native_block = entry.install.native
    assert native_block is not None, f"{entry.id} has no native block to render"
    root = tmp_path / "installers"
    shutil.copytree(resources.installers_dir(), root)
    template = root / native_block.templates / "base.yml.tmpl"
    template.write_text(
        template.read_text(encoding="utf-8") + f"\n# {UNFILLED_PLACEHOLDER}\n",
        encoding="utf-8",
    )
    return root


def _cli_engine_over(rec: Recorder, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point `install_wiring.installer_for_app` at the real family engine over `rec`.

    `main()`, the entry, the family, the spine and the templates are all real;
    only the machine underneath is a double. `installers_root` is taken from the
    keyword `main()` passes down, which is what keeps `--installers-root` the
    trigger rather than a fixture reaching around the CLI.
    """

    def build(entry: CatalogEntry, **kwargs: object) -> native.StagedInstaller:
        root = kwargs["installers_root"]
        assert isinstance(root, Path)
        linux = entry.model_copy(
            update={"install": entry.install.model_copy(update={"platforms": ("linux",)})}
        )
        return family_for(linux)(
            linux,
            installers_root=root,
            import_probe=rec.probe,
            reset_unfinished=rec.reset,
            seams=rec.seams(platform_id=lambda: "linux"),
        )

    monkeypatch.setattr(install_wiring, "installer_for_app", build)


@pytest.mark.parametrize("game_id", ["wow-wotlk", "wow-tbc"])
def test_a_compose_refusal_reaches_the_cli_as_a_sentence_and_is_recorded(
    game_id: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A `ComposeGenError` out of `render()` is a refusal, and every refusal is an `InstallerError`.

    Measured on `f6ed1b9a` before the fix, for both games: `main()` raised
    `ComposeGenError` from `native.py:1309`, the harness printed a traceback
    where its own docstring promises "the sentence written for a person", and
    because `run()`'s `except InstallerError` never fired, `_record_error` did
    not run - the state file kept `"last_error": ""` where every other stage
    failure records its sentence.

    The rule under test is `fill()`'s. Its neighbour on this path is
    `write_plan()`'s refusal, which was the one `ComposeGenError` already
    translated, so the message is pinned to the first and the compose files are
    asserted absent: a run that reached the neighbour would fail here rather
    than pass on the neighbour's words.
    """
    entry = load_catalog().get(game_id)
    server_dir = tmp_path / "server"
    root = _installers_with_an_unfilled_token(entry, tmp_path)
    _cli_engine_over(Recorder(images=False), monkeypatch)
    try:
        code = install_wiring.main(
            [game_id, "--server-dir", str(server_dir), "--installers-root", str(root)]
        )
    except Exception as exc:
        pytest.fail(
            f"a compose refusal escaped install_wiring.main() as {type(exc).__name__}: "
            + "".join(traceback.format_exception(exc)).strip()
        )
    streamed = capsys.readouterr()
    refusal = f"unfilled compose placeholder {UNFILLED_PLACEHOLDER}"
    assert code == 1
    assert f"install failed: {refusal}" in streamed.err
    assert "--- generate-compose" in streamed.out, "the run never reached the stage under test"
    assert "was not written by Yu'lon" not in streamed.err, "that is write_plan()'s refusal"
    for name in composegen.COMPOSE_FILES:
        path = server_dir / name
        assert not path.exists() or not composegen.is_ours(path), (
            f"{name} carries the generated marker, so write_plan() ran - the refusal under "
            "test is render()'s, one call earlier"
        )
    recorded = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert recorded["last_error"] == refusal, (
        "the refusal was not recorded, so `run()`'s `except InstallerError` never saw it "
        "and `_record_error` did not run"
    )


# -- a folder that will not list ---------------------------------------------
#
# Every "may this install write here?" rule in the engine is decided by listing
# a directory, and `Path.iterdir()` raises `OSError` for reasons that have
# nothing to do with what the folder holds: a permission change, an unreadable
# mount, a vanished drive, a stale UNC path into a WSL distro. The app really
# does reach folders that way - `Identification.UNVERIFIED` exists in the UI for
# exactly that - so this is not an exotic input.
#
# Untranslated it is the blocker's shape again: a traceback where the harness
# promises a sentence, and no `last_error` at the stage sites. The sites are
# ENUMERATED rather than described, because they are what the fix is about; a
# fifth one added later without the helper is caught by
# `test_no_folder_in_the_install_spine_is_listed_outside_the_helper`.


@dataclass(frozen=True)
class _ListingSite:
    """One place the engine lists a folder to decide whether it may write into it.

    `neighbour` is the refusal that site makes when the listing SUCCEEDS and
    finds files. It is asserted absent from the run under test and present from
    the paired readable run, so a fixture that never reached the site cannot
    pass on the neighbour's words - and a fixture that reached nothing at all
    cannot pass either.
    """

    site: str
    game_id: str
    folder: str
    """Relative to the server dir; empty is the server dir itself."""

    stage: str | None
    """The stage the refusal comes from, or None for the guard, which precedes them all."""

    neighbour: str


_LISTING_SITES = (
    _ListingSite(
        "_claim_folder", "wow-wotlk", "", None, "is not empty and was not created by this app"
    ),
    _ListingSite(
        "_clone_core", "wow-wotlk", "", "clone-core", "has files in it but is not a checkout"
    ),
    _ListingSite(
        "_clone_modules",
        "wow-wotlk",
        "modules/mod-playerbots",
        "clone-modules",
        "has files in it but is not a checkout",
    ),
    _ListingSite(
        "stage_clone_sources",
        "wow-tbc",
        "src/mangos-tbc",
        "clone-sources",
        "has files in it but is not a checkout",
    ),
)
"""Every folder listing that decides whether this engine may write somewhere.

Two are the spine's own - `_claim_folder`, which all four shipped games reach
through preflight and `_guard()`, and `stage_clone_sources`, which the three
CMaNGOS games bind - and two are AzerothCore's, the game with green live gates.
Written out rather than derived from the code: these ARE the list under test,
and a parametrisation computed from it would delete its own case the moment a
site went missing.
"""


def _claimable(server_dir: Path, entry: CatalogEntry, **fields: object) -> None:
    """Write the state file that makes `server_dir` this install's own, resuming.

    Without it `_claim_folder()` refuses a non-empty, non-git folder during
    preflight and no stage runs at all - that is the FIRST site below, and it
    would otherwise hide the three under it.
    """
    native_block = entry.install.native
    assert native_block is not None, f"{entry.id} has no native block"
    server_dir.mkdir(parents=True, exist_ok=True)
    native.write_state(
        server_dir,
        native.InstallState(
            game_id=entry.id,
            # `_cli_engine_over()` pins the engine to linux and the id is
            # derived from the path THROUGH that seam; a mismatch is refused as
            # a copied install, one rule before anything here.
            install_id=composegen.install_id(server_dir, platform_id=lambda: "linux"),
            family=native_block.family,
            **fields,  # type: ignore[arg-type]
        ),
    )


def _prepare_listing_site(site: _ListingSite, rec: Recorder, server_dir: Path) -> Path:
    """Build the tree that puts a run at `site`, and return the folder that run will list."""
    entry = load_catalog().get(site.game_id)
    folder = server_dir / site.folder if site.folder else server_dir
    if site.site == "_claim_folder":
        server_dir.mkdir(parents=True, exist_ok=True)
        return folder
    if site.site == "_clone_modules":
        _claimable(server_dir, entry, completed=("clone-core",))
        (server_dir / ".git").mkdir(exist_ok=True)
        rec.remotes[server_dir] = entry.emulator.sources[0].url
    else:
        _claimable(server_dir, entry)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _unlistable(monkeypatch: pytest.MonkeyPatch, folder: Path, error: OSError) -> None:
    """Make ONE directory refuse to list itself, the way a real unreadable one does.

    Scoped to a single path rather than replacing `Path.iterdir` wholesale: the
    run lists other folders on its way past this one, and a blanket refusal
    would stop it somewhere else entirely and prove nothing about the site.
    """
    listing = Path.iterdir

    def refuse(self: Path) -> Iterator[Path]:
        if self == folder:
            raise error
        return listing(self)

    monkeypatch.setattr(Path, "iterdir", refuse)


def _refusal_from(captured: str) -> str:
    """The sentence `install_wiring.main()` wrote for a person, without its prefix."""
    for line in captured.splitlines():
        if line.startswith("install failed: "):
            return line[len("install failed: ") :]
    raise AssertionError(f"main() wrote no refusal at all; it wrote {captured!r}")


@pytest.mark.parametrize("site", _LISTING_SITES, ids=lambda site: site.site)
def test_a_folder_that_will_not_list_is_a_refusal_and_not_a_traceback(
    site: _ListingSite,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`iterdir()` raises `OSError`, which is not an `InstallerError` and never was one.

    Measured on `697adca6` at all four sites: `install_wiring.main()` raised
    `PermissionError` out of the list comprehension, the harness printed a
    traceback where its own docstring promises "the sentence written for a
    person", and at the three stage sites `run()`'s `except InstallerError`
    never fired, so the state file kept `"last_error": ""`.

    The guard site's second half is deliberately the opposite assertion.
    `_claim_folder()` runs from preflight and from `_guard()`, both OUTSIDE
    `run()`'s `try`, because its refusals concern a state file that may be
    somebody else's - so nothing may be written there, and this asserts the
    folder was left exactly as empty as it was found.

    The paired readable run is what proves the fixture reached the site at all:
    the same tree with one thing changed answers with the site's NEIGHBOURING
    rule, which the first half asserts absent.
    """
    server_dir = tmp_path / "unreadable" / "server"
    rec = Recorder(images=False)
    folder = _prepare_listing_site(site, rec, server_dir)
    _cli_engine_over(rec, monkeypatch)
    _unlistable(monkeypatch, folder, PermissionError(13, "Permission denied"))
    try:
        code = install_wiring.main([site.game_id, "--server-dir", str(server_dir)])
    except Exception as exc:
        pytest.fail(
            f"an unreadable folder escaped install_wiring.main() as {type(exc).__name__}: "
            + "".join(traceback.format_exception(exc)).strip()
        )
    streamed = capsys.readouterr()
    refusal = _refusal_from(streamed.err)
    assert code == 1
    assert refusal.startswith(f"{folder} could not be listed"), refusal
    assert "Permission denied" in refusal, "the refusal drops what the machine actually said"
    assert site.neighbour not in refusal, "that is the sentence for a folder that COULD be read"
    if site.stage is None:
        assert "--- " not in streamed.out, "the guard refused, so no stage may have started"
        assert not (server_dir / native.STATE_FILE).exists(), (
            "a state file was written into a folder the guard had just refused; the guard sits "
            "outside `run()`'s `try` precisely so that cannot happen"
        )
    else:
        assert f"--- {site.stage}" in streamed.out, "the run never reached the stage under test"
        recorded = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
        assert recorded["last_error"] == refusal, (
            "the refusal was not recorded, so `run()`'s `except InstallerError` never saw it "
            "and `_record_error` did not run"
        )

    readable = tmp_path / "readable" / "server"
    other = Recorder(images=False)
    theirs = _prepare_listing_site(site, other, readable)
    (theirs / "somebody-elses-work.txt").write_text("mine", encoding="utf-8")
    _cli_engine_over(other, monkeypatch)
    assert install_wiring.main([site.game_id, "--server-dir", str(readable)]) == 1
    assert site.neighbour in _refusal_from(capsys.readouterr().err), (
        "the same tree with that folder READABLE never reached this site's own rule, so the "
        "half above proved nothing about this site"
    )


def test_no_folder_in_the_install_spine_is_listed_outside_the_helper() -> None:
    """One translation means one place that lists a folder, so the next site cannot miss it.

    Four sites carried the same untranslated listing and were found one at a
    time. Asked of the syntax tree rather than of the text, because the comments
    explaining the fix name `iterdir()` too and a grep would match those.
    """
    callers = {
        module.__name__: {
            enclosing.name
            for enclosing in ast.walk(ast.parse(Path(module.__file__ or "").read_text("utf-8")))
            if isinstance(enclosing, ast.FunctionDef)
            for node in ast.walk(enclosing)
            if isinstance(node, ast.Attribute) and node.attr == "iterdir"
        }
        for module in (native, azerothcore)
    }
    assert callers == {native.__name__: {native._listing.__name__}, azerothcore.__name__: set()}


# -- a reset that will not finish --------------------------------------------


@pytest.mark.parametrize(
    "error",
    [
        MaintenanceError("the schemas could not be listed"),
        ApplyError("DROP DATABASE acore_world was refused"),
        RuntimeError("this install holds player data, so nothing was dropped."),
    ],
    ids=["MaintenanceError", "ApplyError", "RuntimeError"],
)
def test_a_reset_that_fails_reaches_the_cli_as_a_sentence_and_is_recorded(
    error: Exception,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`gate.reset()` on the `partial` branch, whose seam raises three things and none is ours.

    The three are `reset_unfinished()`'s own `Raises:` list, enumerated because
    that list is the thing under test: `MaintenanceError` and `ApplyError` are
    independent `RuntimeError` subclasses and the bare `RuntimeError` is
    deliberately neither, so no `except` clause in the spine could ever have
    seen them. Measured on `697adca6`: `install_wiring.main()` raised each one
    through, the harness printed a traceback, and the state file kept
    `"last_error": ""`.

    Driven on `wow-wotlk` - AzerothCore, the game with green live gates, and the
    only family whose import gate is the injected `acore_*` pair this seam
    belongs to.

    The neighbours are the other four answers in the same five-branch table,
    and the `partial` branch's own second refusal. All are asserted absent, and
    the probe's own line is asserted present, so a run that took another branch
    fails here rather than passing on that branch's words. The one-shot is
    asserted never to have run, because re-importing over a half-written schema
    is the destruction this branch exists to prevent.
    """
    server_dir = tmp_path / "server"
    rec = Recorder(images=False, probe_answers=[PARTIAL, IMPORTED], reset_error=error)
    _cli_engine_over(rec, monkeypatch)
    try:
        code = install_wiring.main(["wow-wotlk", "--server-dir", str(server_dir)])
    except Exception as exc:
        pytest.fail(
            f"a failed reset escaped install_wiring.main() as {type(exc).__name__}: "
            + "".join(traceback.format_exception(exc)).strip()
        )
    streamed = capsys.readouterr()
    refusal = _refusal_from(streamed.err)
    assert code == 1
    assert "reset" in rec.calls, "the run never reached the call under test"
    assert f"The databases read as {PARTIAL.state}: {PARTIAL.detail}" in streamed.out
    expected = "The half-written databases could not be cleared, so the import was not run: "
    assert refusal == expected + str(error)
    assert "nothing was found to clear" not in refusal, "that is the empty-tuple refusal below it"
    neighbours = ("already hold data", "could not be asked")
    assert not any(
        words in refusal for words in neighbours
    ), "those are the `populated` and `unreadable` branches, which this run never took"
    assert "one-shot:ac-db-import" not in rec.calls, (
        "the import ran over databases that were never cleared, which is the 28-second "
        "success that leaves `acore_world` permanently unimportable"
    )
    recorded = json.loads((server_dir / native.STATE_FILE).read_text(encoding="utf-8"))
    assert recorded["last_error"] == refusal, (
        "the refusal was not recorded, so `run()`'s `except InstallerError` never saw it "
        "and `_record_error` did not run"
    )


def test_a_finished_install_drops_the_previous_runs_failure(tmp_path: Path) -> None:
    """A success must not leave the last failure's sentence in the state file.

    `record()` clears `last_error`, and until 2026-09-02 that was the only thing
    that did -- so it cleared nothing on the run where it matters most. It
    returns `self` untouched when the stage is already in `completed`, and an
    unrecorded stage never reaches it, so a RESUME that finishes every remaining
    stage records nothing new and clears nothing.

    Seen on m910q the same day: WoW TBC printed "WoW TBC is installed and
    running", three containers up, and `.yulon-install.json` still said
    `"last_error": "The server started but never reported ready..."` with
    `updated_unix` freshly bumped -- a working install describing itself as a
    failed one, on exactly the retry-after-failure path where someone is most
    likely to believe it.

    Shaped as that resume rather than as a fresh install: the recorded stage is
    ALREADY done before this run starts, so `record()`'s early return is taken
    and the only remaining stage is unrecorded. A test that drove a first run
    would pass against the broken code, because there `record()` clears the
    field on the way past.
    """
    rec = Recorder()
    family = _family(
        lambda me: (native.Stage("always", _say), native.Stage("never", _say, recorded=False))
    )
    server_dir = tmp_path / "wow"

    # Run once so "always" is recorded, then plant a failure the way
    # `_record_error` does.
    list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))
    state = native.read_state(server_dir, valid=("always", "never"))
    assert state is not None
    native.write_state(server_dir, replace(state, last_error="the server never reported ready"))
    assert native.read_state(server_dir, valid=("always", "never")).last_error  # type: ignore[union-attr]

    list(_build(rec, family).run(InstallOptions(server_dir=server_dir)))

    after = native.read_state(server_dir, valid=("always", "never"))
    assert after is not None
    assert after.last_error == "", (
        "a finished install left the previous failure in the state file: " f"{after.last_error!r}"
    )
    assert after.completed == ("always",), "clearing the error must not disturb what was done"
