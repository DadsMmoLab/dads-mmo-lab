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
import threading
import traceback
from collections.abc import Iterator
from pathlib import Path

import pytest

from yulon import docker
from yulon.catalog import native
from yulon.catalog.installer import InstallerError

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
