"""The button that applies an install plan's re-runnable phases to a server that exists.

T11 made the route: a phase declared `rerun_on_marked` is applied by `_import`
to an install the probe already reads as finished, before the world starts, with
no marker written. Its reviewer then found the route had no way in for a GUI
user — the catalog tile greys to "Installed" once the app knows the folder, and
`rebuild_stages()` excludes `import` on purpose — so for an established Tortoise
install the sentence that opened T11 was still true: no button in the app
applies those three files, and the only caller was the CLI harness.

This file is about the second route. `update_stages()` is the database started
alone and the import stage, and nothing else — no `up`, no compile, no compose
regeneration — and `update_databases()` refuses before any of it while the world
server is up or unreadable, because the route writes DDL into the character
database and a running worldserver holds that database in memory and saves it
back over whatever it finds (owner answer 7, checklist 8.7a).

The second precondition is the one round 1 did not have, and it is the larger
half. `import` is the family's own stage and the flagged-phase route is ONE arm
of a five-branch table: `absent` runs the whole plan and writes a completion
marker, `partial` drops every schema the plan names first. A press that consented
to a named list of files must reach neither, so `ctx.updates_only` is read before
`stage_import()` is called at all and the ordinary import is unreachable on this
route rather than guarded on it.

What is unit-tested only: no press was run on a box for this change. The live
half is `pyplan/gates/tortoise-updates-button-m910q-2026-09-09/`, and until it
exists the m910q evidence for this route is T11's, through the CLI harness.
"""

from __future__ import annotations

import re
import subprocess
import threading
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest

from tests.support_native import Recorder
from tests.test_families_cmangos import ENTRY as CM_ENTRY
from tests.test_families_cmangos import (
    EVERY_PRESS,
    IMPORTED_OLDER_PLAN,
    MARKED_ONLY,
    POPULATED_AND_COMPLETE,
    engine_with_sql,
    entry_with_sql,
    ready_to_import,
    rerun_plan,
)
from yulon import docker
from yulon.catalog import native
from yulon.catalog.catalog import SqlPhase, load_catalog
from yulon.catalog.families import sqlplan
from yulon.catalog.families.cmangos import CmangosInstaller
from yulon.catalog.installer import InstallerError, InstallOptions, installer_for

TORTOISE = load_catalog().get("wow-tortoise")
WOTLK = load_catalog().get("wow-wotlk")


@pytest.fixture(autouse=True)
def gated(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CmangosInstaller._gate` is the double `engine_with_sql(plan, rec)` attached.

    `test_families_cmangos.py`'s own `gated` is autouse THERE and reaches
    nothing here, so an engine driven from this file would take the real
    `MarkerGate` to a database that does not exist — and answer `absent`, which
    on the INSTALL route is the arm that imports the whole plan. On this route it
    is a refusal (`_only_the_rerunnable_phases`), and the fixture is still what
    makes these tests about the family rather than about a missing daemon. Copied
    rather than shared: `test_rebuild.py` keeps the same four lines under the
    name `cmangos_gate` and says so.
    """

    def gate(self: CmangosInstaller, ctx: native.StageContext) -> native.ImportGate:
        attached = getattr(self, "_test_gate", None)
        assert attached is not None, "build engines with engine_with_sql(plan, rec) in this file"
        return attached

    monkeypatch.setattr(CmangosInstaller, "_gate", gate, raising=True)


def a_world_that_is(answer: bool | None) -> object:
    """The `world_running` seam, answering the same thing however often it is asked."""

    def world_running(container: str) -> bool | None:
        return answer

    return world_running


def worlds(*answers: bool | None) -> tuple[object, list[str]]:
    """A `world_running` seam whose answers change between readings, and its record.

    A list and not a counter because the second reading is the point: the
    database start between the two waits for the container to report healthy,
    and a Server-tab Start inside that window puts a live world behind the very
    tables the next statement writes.
    """
    asked: list[str] = []
    remaining = list(answers)

    def world_running(container: str) -> bool | None:
        asked.append(container)
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return world_running, asked


def files_on_disk(server_dir: Path, *names: str) -> None:
    """Lay the files a phase's glob will match, with a line naming each."""
    for name in names:
        path = server_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"-- {name}\nSELECT 1;\n", encoding="utf-8")


def file_plan() -> object:
    """`rerun_plan()`'s pair, with the flagged phase reading files instead of statements.

    The confirmation's subject is the file list, and a statements-only plan has
    none. The unflagged phase stays a statement phase so the assertions about
    what did NOT move read the same as they do everywhere else in this file.
    """
    plan = rerun_plan()
    return plan.model_copy(
        update={
            "phases": (
                plan.phases[0],
                SqlPhase(
                    name="character updates",
                    into=CM_ENTRY.databases.characters,
                    files=("src/updates/*.sql",),
                    sort="name",
                    rerun_on_marked=True,
                ),
            )
        }
    )


SECOND_RERUN_STATEMENT = "SELECT 'a second step of a phase declared rerun_on_marked'"
"""The flagged phase's second statement in `two_statement_rerun_plan()`.

Named apart from `EVERY_PRESS` so a test can tell "the first run went out" from
"the second run never happened" by which of the two strings landed in
`rec.sql_calls`.
"""


def two_statement_rerun_plan() -> object:
    """`rerun_plan()`'s pair, with the flagged phase carrying two statements instead of one.

    `sqlplan.apply()` checks cancel BEFORE each run and never mid-file (A10), so
    a one-statement flagged phase has no run left to catch a Stop set on the way
    out of the first — the window this exists to test is between two runs of
    `_rerun_on_marked()`'s own `sqlplan.apply()` call, and a plan with only one
    statement in the flagged phase cannot open it.
    """
    plan = rerun_plan()
    return plan.model_copy(
        update={
            "phases": (
                plan.phases[0],
                SqlPhase(
                    name="character updates",
                    into=CM_ENTRY.databases.characters,
                    statements=(EVERY_PRESS, SECOND_RERUN_STATEMENT),
                    rerun_on_marked=True,
                ),
            )
        }
    )


# -- the tuple ---------------------------------------------------------------


def test_the_updates_tuple_is_the_database_and_the_import_and_nothing_else() -> None:
    """Two stages, named, in this order — and the four a press must never re-enter.

    `up` is the one that matters most and is why the list is spelled out rather
    than counted: this press is for a server somebody is playing on and has
    stopped, and starting the world at the end of it would put the world back
    up under a user who stopped it to let this run. The other three are the
    install's own — a compile, a compose regeneration and a Dockerfile
    re-render have nothing to do with three SQL files.

    Catches the tuple built from `stages()` with the finished stages skipped
    (which is what `rebuild_stages()`'s docstring says a rebuild deliberately
    is not, for the same reasons), and any stage appended to it.
    """
    engine = installer_for(TORTOISE)
    names = [stage.name for stage in engine.update_stages()]
    assert names == ["start-db", "import"]
    for forbidden in ("up", "build", "generate-compose", "write-dockerfile"):
        assert forbidden not in names, names


def test_the_import_stage_of_an_updates_press_is_never_written_into_the_state_file() -> None:
    """`recorded=False`, and it is the family's own stage with the record taken off.

    The install's `import` stage is recorded, and rightly: it imported. This
    press reaches the same body and the body does NOT import — on a marked
    install `_import` runs the probe's table, applies the flagged phases and
    returns — so a record written here would claim a stage that did not happen,
    and an install whose state file never recorded `import` (one made by the
    shell scripts, or one killed mid-install) would have its next resume skip
    the import on the strength of it.

    Catches `replace(..., recorded=False)` dropped from `update_stages()`.
    """
    engine = installer_for(TORTOISE)
    by_name = {stage.name: stage for stage in engine.update_stages()}
    assert by_name["import"].recorded is False
    assert by_name["start-db"].recorded is False
    assert engine.stage_named("import").recorded is True, "the install's own is still recorded"


def test_an_updates_press_says_the_truthful_note_once_and_never_the_installs(
    tmp_path: Path,
) -> None:
    """`IMPORT_CANCEL_NOTE` is a claim about `gate.reset()`, unreachable on this route.

    `stage_named("import")` carries `cancel_note=IMPORT_CANCEL_NOTE` ("Databases
    left half-written are detected and cleared before the import is run
    again…"), true of the install route because `stage_import()`'s `partial`
    arm calls `gate.reset()` before it re-imports. `_only_the_rerunnable_phases`
    never calls `stage_import()` — it is the second way IN to `_import`, past
    the branch that clears anything — so the sentence is false here.

    Round 1 cleared the stage's `cancel_note` to `""` and the reviewer rejected
    it: the spine says every stage's note once, up front, the way A4 says every
    other stage's is — a Stop that lands here gets no warning at all rather
    than a false one, which is not the fix. `update_stages()` now gives the
    stage `RERUN_CANCEL_NOTE`, the true sentence, in its place.

    Pinned on the panel's own lines rather than on the stage object: the two
    `--- <name>` markers the spine always yields for this route's two stages
    are asserted present, so a fix that also swallowed those would be caught
    here rather than passing this test by deleting too much.

    Catches `replace(stage_named("import"), recorded=False)` with no
    `cancel_note=` alongside it, and `cancel_note=""` back in its place.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    said = list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert not any(native.IMPORT_CANCEL_NOTE in line for line in said), said
    assert said.count(native.RERUN_CANCEL_NOTE) == 1, said
    at = said.index("--- import")
    assert said[at + 1] == native.RERUN_CANCEL_NOTE, said
    assert "--- start-db" in said, said


def test_a_stop_between_rerunnable_runs_says_nothing_is_cleared(tmp_path: Path) -> None:
    """`sqlplan.apply()`'s own between-run check, reached through the updates route.

    Round-1 rework's one high finding: the stage heading was fixed but
    `_rerun_on_marked()`'s call into `sqlplan.apply()` still defaulted to
    `IMPORT_CANCEL_NOTE`, so a Stop caught between two of the flagged phase's
    own statements raised the install route's clearing promise on a route that
    clears nothing — completed statements and a partially executed file stay,
    and the flagged phase is re-run whole on the next press.

    The event is set from inside the exec seam, on the way out of the FIRST
    statement, so the window under test is the real one between two runs
    rather than a cancel that was pending before the press started (the same
    discipline `test_a_stop_arriving_during_the_last_dump_is_caught_before_verify_and_the_marker`
    uses on the install route).

    Catches `cancel_note=RERUN_CANCEL_NOTE` dropped from `_rerun_on_marked()`'s
    `sqlplan.apply()` call, which reverts the parameter to `sqlplan.apply()`'s
    own default and brings `IMPORT_CANCEL_NOTE` back into the raised error.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    stop = threading.Event()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
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
        if rec.sql_calls[-1] == EVERY_PRESS:
            stop.set()
        return proc

    engine = engine_with_sql(
        two_statement_rerun_plan(),
        rec,
        world_running=a_world_that_is(False),
        exec_stdin=exec_stdin,
    )
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine.update_databases(InstallOptions(server_dir=server_dir), cancel=stop):
            said.append(line)
    assert "The import was stopped." in str(raised.value), raised.value
    assert native.RERUN_CANCEL_NOTE in str(raised.value), raised.value
    assert native.IMPORT_CANCEL_NOTE not in str(raised.value), raised.value
    assert not any(native.IMPORT_CANCEL_NOTE in line for line in said), said
    assert EVERY_PRESS in rec.sql_calls, "the first run went out before the stop was seen"
    assert SECOND_RERUN_STATEMENT not in rec.sql_calls, "the second run never happened"


def test_the_press_runs_only_the_phases_the_plan_declares_rerunnable(tmp_path: Path) -> None:
    """The whole point, at the engine: the flagged phase moved and nothing else did.

    The pair is what makes it readable — a plan carrying only the flagged phase
    could not tell "the flag was honoured" from "the whole plan ran again" —
    and it is T11's own fixture, so this asserts the button reaches that route
    rather than a second copy of it.

    Catches `update_stages()` returning the install's whole tuple, and the
    press reaching `_import`'s ordinary import path.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    said = list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert EVERY_PRESS in rec.sql_calls, rec.sql_calls
    assert MARKED_ONLY not in rec.sql_calls, rec.sql_calls
    assert any("The import marker is unchanged." in line for line in said), said


def test_the_press_starts_the_database_and_never_the_world(tmp_path: Path) -> None:
    """The one container this press may start, and the one it may not.

    `stage_start_db` is the primitive T7 wired for the applier's stopped-world
    path (`docker.start_database`), reused rather than re-derived; `up` is
    absent from the tuple, so nothing here can bring the world back.

    Catches `start-db` dropped from the tuple (the probe would then answer
    `unreadable` and the press would refuse instead of applying), and `up`
    added to it.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    rec.db_started = False
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert rec.db_started is True
    assert not any(call.startswith("start ") for call in rec.calls), rec.calls


# -- the refusals ------------------------------------------------------------


def test_a_press_while_the_world_is_up_applies_no_sql(tmp_path: Path) -> None:
    """8.7a's rule, on this route: the world holds these tables and writes them back.

    T11's reviewer (note 3) read the route as it then stood — reached only
    through `engine.run()`, where `stage_start_db` returns as soon as the
    database is up and nothing asks about the world — and recorded that it
    writes DDL into `tw_char` under a running world. This is that, refused.

    Catches the guard deleted, and the whole press starting before it is asked.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(True))
    with pytest.raises(InstallerError) as raised:
        list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert "world server is running" in str(raised.value)
    assert "Press Stop" in str(raised.value)
    assert rec.sql_calls == [], rec.sql_calls
    assert rec.calls == [], "not one seam was touched: the refusal is the first thing that runs"


def test_a_press_that_cannot_tell_whether_the_world_is_up_applies_no_sql(tmp_path: Path) -> None:
    """`None` is not "no". The three-valued seam, refused on its middle answer.

    `docker.container_state()` answers an empty state for a missing container
    and for a daemon that will not reply, and `.settled` turns both into
    `False` — which through this guard would be fail-OPEN. `docker.world_running`
    answers `None` there instead, and this is the branch that costs.

    **The remedy has to name Docker**, and that is not decoration: the most
    likely reason nobody can answer is that the daemon is not answering, and
    "Stop the server, then press again" is then an instruction the user cannot
    follow — the shape T7's ticket is titled after. Asked of the sentence,
    because a refusal a user cannot act on is the defect, not the branch.

    Catches the seam read through `container_state(...).settled`, the
    `None` branch folded into the `False` one, and the dead-daemon clause
    dropped from the remedy.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(None))
    with pytest.raises(InstallerError) as raised:
        list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert "could not tell whether" in str(raised.value)
    assert "check that Docker is running" in str(raised.value)
    assert rec.sql_calls == [], rec.sql_calls


def test_a_world_started_while_the_database_came_up_is_refused_before_the_first_statement(
    tmp_path: Path,
) -> None:
    """The window between the two readings, closed the way T7 closed its own.

    `start_database()` waits for the container to report healthy — up to
    `docker._DB_HEALTHY_TIMEOUT_SECONDS` — and the Server tab's Start is a
    button the same user can press meanwhile. The first reading is that old by
    the time the first statement would be sent, so the world is read again
    after the database is up and before the import stage runs, in the same
    words: it is the same fact and the same remedy.

    Catches the second reading deleted (the seam is then asked once and the
    statements go into a live world's tables).
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    rec.db_started = False
    seam, asked = worlds(False, True)
    engine = engine_with_sql(rerun_plan(), rec, world_running=seam)
    with pytest.raises(InstallerError) as raised:
        list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert "world server is running" in str(raised.value)
    assert rec.db_started is True, "the database really was started between the two readings"
    assert rec.sql_calls == [], rec.sql_calls
    assert asked == [CM_ENTRY.container_spec().world] * 2, asked


def test_a_seam_that_raises_is_read_as_could_not_tell(tmp_path: Path) -> None:
    """Fails closed on anything short of a clear "no", the seam's own failure included.

    The same discipline `apply.Applier`'s guard takes: *could not ask* is not
    *not running*, and a seam that raised would otherwise take the press's
    refusal out with it as a traceback in a dialog.

    Catches the `try`/`except` around the reading removed.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()

    def angry(container: str) -> bool | None:
        raise RuntimeError("the daemon is not there")

    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    engine = engine_with_sql(rerun_plan(), rec, world_running=angry)
    with pytest.raises(InstallerError) as raised:
        list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert "could not tell whether" in str(raised.value)
    assert "the daemon is not there" in str(raised.value)
    assert rec.sql_calls == [], rec.sql_calls


def test_a_clone_that_never_had_the_directory_refuses_naming_the_pattern(tmp_path: Path) -> None:
    """T11's reviewer, note 4: `expand()` refuses a `fail` phase whose glob matched nothing.

    A clone made before the fork's `sql/character_updates/` existed — or one
    whose fork moved it — has no file for this phase, and `on_error: fail` is
    set on it precisely so that a silent skip is impossible. The sentence is
    `sqlplan._matches()`'s own and is not wrapped in a second one: it already
    names the pattern, the folder it looked in, and that the sources may not
    have cloned completely.

    Catches the confirmation swallowing the refusal and offering an empty file
    list instead.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    engine = engine_with_sql(file_plan(), Recorder())
    with pytest.raises(InstallerError) as raised:
        engine.update_confirmation(InstallOptions(server_dir=server_dir))
    assert "src/updates/*.sql" in str(raised.value)
    assert "may not have cloned completely" in str(raised.value)


# -- the import state this press requires ------------------------------------


NOT_FINISHED = {
    "absent": docker.ImportState("absent", "no schema has tables"),
    "partial": docker.ImportState("partial", "tw_char has tables, tw_world has none"),
    "unreadable": docker.ImportState("unreadable", "the databases would not answer"),
    "populated but incomplete": docker.ImportState(
        "populated", "tw_logon holds rows, tw_world is empty", complete=False
    ),
}
"""Every probe answer that is NOT a finished import, by the name a reader uses.

Enumerated rather than sampled, because the danger is per ARM and the arms differ:
`absent` runs the whole plan and writes a completion marker, `partial` drops every
schema the plan names first (`gate.reset()`), and the two refusals are refusals
about an INSTALL and say install-shaped things. A press that consented to a named
list of files must reach none of them.
"""


@pytest.mark.parametrize("named", sorted(NOT_FINISHED))
def test_a_press_against_databases_that_are_not_a_finished_import_applies_nothing(
    tmp_path: Path, named: str
) -> None:
    """The precondition the round-1 tuple did not have, over all four answers it refuses.

    `import` is the family's own stage, and the flagged-phase route is ONE arm of
    a five-branch table. On `absent` that table imports the whole plan — hours,
    `CREATE USER … IDENTIFIED BY` with a password this tuple has no `db-password`
    stage to persist, and a completion marker — and on `partial` it first drops
    every schema the plan names. Both are reachable from a controller tab, which
    opens for any remembered folder and for "Use existing…", including a Tortoise
    install that failed at or after `import` (cold review of T14, round 1).

    Asserted on the three things that must not have happened: no SQL, no marker,
    and no drop.

    Catches `ctx.updates_only` never read (the press then falls into
    `stage_import()`'s table: `absent` and `partial` go green with SQL in the
    recorder, and the two refusals arrive in install-shaped words), and the
    precondition widened to `state != "unreadable"` or to any single arm.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(NOT_FINISHED[named])
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    with pytest.raises(InstallerError) as raised:
        list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert "do not read as a finished import" in str(raised.value)
    assert named.split()[0] in str(raised.value), "the refusal does not say what it read"
    assert "nothing was cleared" in str(raised.value)
    assert rec.sql_calls == [], rec.sql_calls
    assert not [s for s in rec.sql_scripts if sqlplan.MARKER_TABLE in s], rec.sql_scripts
    assert "reset" not in rec.calls, rec.calls


@pytest.mark.parametrize("finished", [IMPORTED_OLDER_PLAN, POPULATED_AND_COMPLETE])
def test_a_press_against_databases_that_read_as_finished_applies_the_flagged_phase(
    tmp_path: Path, finished: docker.ImportState
) -> None:
    """Both answers the route accepts, so the precondition is not merely "the marker".

    An install made by the shell scripts carries no marker row and reads
    `populated` with every schema complete — which is the install T11's route
    exists for, and a precondition narrowed to `imported` would lock it out of
    the button while leaving it exposed to the restart loop.

    Catches the predicate narrowed to `state == "imported"`.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(finished)
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    said = list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert EVERY_PRESS in rec.sql_calls, rec.sql_calls
    assert any(f"read as {finished.state}" in line for line in said), said


def test_the_press_asks_the_databases_exactly_once(tmp_path: Path) -> None:
    """One probe, and the reason it may not be two.

    A precondition checked after `stage_import()` had already branched would be a
    second question, and between two probes the answer can differ — so the arm
    that drops every schema the plan names would still be reachable on the first
    answer while the check passed on the second. `_Remembering` exists for this
    exact hazard on the install route; here the route probes once itself and
    hands the same answer to both the precondition and the re-run.

    Catches `_only_the_rerunnable_phases()` calling `stage_import()` (which
    probes again), and a precondition added in `_guard_then` on top of this one.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert rec.calls.count("probe") == 1, rec.calls


def test_a_game_whose_plan_has_no_rerunnable_phase_refuses_the_press_itself(
    tmp_path: Path,
) -> None:
    """The half no probe can see, refused by the spine before the tuple is built.

    The button is not offered for such a game, but `update_databases()` is a
    method and the greying is in the view — and a family that never learned to
    read `ctx.updates_only` would take this press straight into
    `stage_import()`'s table. AzerothCore is exactly that family: it imports
    through a compose one-shot and carries no phase list at all. So the refusal
    is here, where the plan is readable, rather than trusted to a family.

    Catches the refusal deleted, and `update_phases()` consulted only by the view.
    """
    engine = installer_for(WOTLK)
    with pytest.raises(InstallerError) as raised:
        list(engine.update_databases(InstallOptions(server_dir=tmp_path / "wotlk")))
    assert "no phase meant to be re-applied" in str(raised.value)
    assert "Nothing was started." in str(raised.value)


# -- the confirmation --------------------------------------------------------


def test_the_confirmation_names_every_file_the_press_will_stream(tmp_path: Path) -> None:
    """The list is expanded from the folder, not read off the catalog's glob.

    A dialog that said `src/updates/*.sql` while the press streamed two files
    would be a promise about a pattern. `expand()` is what produces both lists,
    so the dialog and the run cannot disagree about which files exist or about
    the order they go in.

    Catches the file list built by globbing the catalog patterns instead, and
    the unflagged phase's own files leaking into the list.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    files_on_disk(server_dir, "src/updates/0002_second.sql", "src/updates/0001_first.sql")
    engine = engine_with_sql(file_plan(), Recorder())
    said = engine.update_confirmation(InstallOptions(server_dir=server_dir))
    assert "src/updates/0001_first.sql" in said
    assert "src/updates/0002_second.sql" in said
    assert said.index("0001_first") < said.index("0002_second"), said
    assert "character updates" in said
    assert "world base" not in said, "the phases the marker rule still covers are not offered"


def test_the_confirmation_says_the_marker_is_unchanged_and_the_world_must_be_stopped(
    tmp_path: Path,
) -> None:
    """The two facts a user cannot see and would be right to fear, said before they agree.

    The marker clause is what separates this from a re-import: the databases
    keep the completion marker they have, no new one is written, and no
    `verify` rule is re-asked — because a marker row says the whole PLAN
    finished and this press is two phases of it.

    The stopped-world clause is stated here as well as in the refusal because a
    user meeting the refusal has already paid for the dialog.

    Catches either clause dropped from the confirmation.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    files_on_disk(server_dir, "src/updates/0001_first.sql")
    said = engine_with_sql(file_plan(), Recorder()).update_confirmation(
        InstallOptions(server_dir=server_dir)
    )
    assert "marker" in said
    assert "Stop" in said
    assert str(server_dir) in said


def test_the_confirmation_says_a_file_can_be_refused_and_the_press_stops_there(
    tmp_path: Path,
) -> None:
    """T11's reviewer, note 2: `MODIFY money INT(10) UNSIGNED` can fail on real data.

    A `guild_bank_money.money` that is negative cannot narrow to unsigned under
    MySQL's strict mode, and `on_error: fail` means the press stops on that file
    with the client's own last line. Stated, not fixed: this ticket puts a
    button on the route T11 built and does not change what the route applies.

    Catches the clause dropped, and an `on_error` claim that says a failing file
    is skipped.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    files_on_disk(server_dir, "src/updates/0001_first.sql")
    said = engine_with_sql(file_plan(), Recorder()).update_confirmation(
        InstallOptions(server_dir=server_dir)
    )
    assert "stops" in said
    assert "guild bank" in said.lower()


# -- who gets the button -----------------------------------------------------


def test_the_entries_that_offer_this_press_are_exactly_the_ones_with_a_flagged_phase() -> None:
    """The enabling rule, read off the catalog and bound to `SqlPhase.rerun_on_marked`.

    Two derivations of one fact — the button's own (`update_phases()`) and the
    route's (`rerunnable_phases()`, which `_rerun_on_marked` filters with) —
    over every shipped entry, so a button offered for a game whose plan has no
    such phase, or withheld from one that does, is red here rather than
    discovered on a server.

    NO SHIPPED ENTRY DECLARES ONE since T30, and the empty answer here is a
    real assertion rather than a vacuous one. `wow-tortoise` was the only entry
    that ever did: its `character updates` phase applied the retired fork's
    `sql/character_updates/`, which nothing on that tree read, and the Penqle
    core has no such directory -- what that phase's files fixed, this core's own
    updater applies at every world start. The flag is an exception to the marker
    rule (a phase run against a server somebody is playing on), so the catalog
    having none is the safe state and the one worth pinning: a flag that
    reappears, anywhere, is red here and has to be argued for.

    The other direction -- that the reader finds a flag when there IS one -- is
    `test_a_flag_added_to_another_entry_is_offered_without_the_reader_being_touched`
    below, over a synthesised plan, which is what keeps this pair from together
    passing on a reader that answers `()` to everything.

    Catches the reader hard-coding `wow-tortoise`, and a phase's flag moved to
    another phase or another entry.
    """
    offered = {
        entry.id: tuple(phase.name for phase in native.update_phases(entry))
        for entry in load_catalog().games
        if native.update_phases(entry)
    }
    assert offered == {}, (
        "a shipped plan declares a re-runnable phase again. That flag applies SQL to an "
        "install the marker rule says is finished -- somebody's server, mid-play -- so it "
        f"is argued for per phase, in the phase's own notes, before it lands here: {offered}"
    )
    assert native.update_phases(WOTLK) == (), "AzerothCore imports through a compose one-shot"


def test_a_flag_added_to_another_entry_is_offered_without_the_reader_being_touched() -> None:
    """The rule is data, and this is the proof it is not a name in an `if`.

    `test_the_entries_that_offer_this_press_are_exactly_the_ones_with_a_flagged_phase`
    pins today's catalog; this pins that the pin is about the catalog. The TBC
    entry ships no flagged phase at all, and one added to its plan reaches the
    button with no code change.

    Catches the reader written as `entry.id == "wow-tortoise"`, which passes
    the test above and fails this one.
    """
    assert native.update_phases(CM_ENTRY) == ()
    widened = entry_with_sql(rerun_plan())
    assert [phase.name for phase in native.update_phases(widened)] == ["character updates"]


def test_the_route_and_the_button_filter_phases_with_one_function() -> None:
    """One filter, two callers: the press's and the button's answers cannot drift.

    `_rerun_on_marked()` decides what runs and `update_phases()` decides whether
    the control is offered at all. Written twice they could disagree in the
    direction that matters — a button offered for a phase the route then skips,
    which reports success and applies nothing.

    Catches `rerunnable_phases()` inlined again in either caller.
    """
    plan = rerun_plan()
    assert [phase.name for phase in native.rerunnable_phases(plan)] == ["character updates"]
    assert native.rerunnable_phases(plan) == native.update_phases(entry_with_sql(plan))


def test_a_tuple_with_no_import_stage_to_guard_refuses_before_anything_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of `stage_named()`'s refusal: a tuple whose stage is under another name.

    `stage_named()` covers a family with no `import` at all. This covers the
    shape that is worse because it is quiet — a tuple this method stopped
    putting `import` into, or one carrying it under another name — because what
    that produces is not a missing stage but a second reading of the world that
    silently never happens. T8 built the same guard for the rebuild's rollback
    wrappers after finding they had bound positionally.

    Catches the `if not guarded:` block deleted.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    monkeypatch.setattr(type(engine), "update_stages", lambda self: (self.stage_named("start-db"),))
    with pytest.raises(InstallerError) as raised:
        list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert "no `import` stage to guard" in str(raised.value)
    assert "Nothing was started." in str(raised.value)
    assert rec.calls == [], rec.calls


def test_a_press_can_be_stopped_before_it_reaches_the_first_statement(tmp_path: Path) -> None:
    """The cancel every other staged run honours, honoured here too.

    **A guard, and honestly labelled as one**: deleting the explicit
    `_check_cancel()` from `update_databases()` leaves this green, because
    `_staged()` asks the same question before each stage. What the explicit one
    buys is not visible from here — it refuses before `_update_context()` reads
    the state file and resolves the install's database password — and `rebuild()`
    carries it in the same position for the same reason. Measured 2026-09-09;
    the mutation is equivalent, not uncaught.

    What this does catch is the cancel never reaching the stages at all: a press
    built with `cancel=None` on the context.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(IMPORTED_OLDER_PLAN)
    stop = threading.Event()
    stop.set()
    engine = engine_with_sql(rerun_plan(), rec, world_running=a_world_that_is(False))
    with pytest.raises(InstallerError):
        list(engine.update_databases(InstallOptions(server_dir=server_dir), cancel=stop))
    assert rec.sql_calls == [], rec.sql_calls


# ============================================================ the adopt press (T19)
#
# T14's button refuses an install with no marker row, and the install it was
# built for is exactly that: the owner's Tortoise server, made by the shell
# scripts, `populated` and complete in every way a person can see, with no
# `yulon_install` table in any schema. Three rounds then tried to teach the
# probe to prove an import finished without one -- per-schema table counts, the
# plan's own `verify` rules, the table set parsed out of every dump file -- and
# each round's reviewer found the next layer of inference underneath.
#
# The owner's answer was to stop inferring: an explicit press that writes the
# marker row with consent. So the tests below are about a press whose safety is
# NOT that it checked -- it cannot -- but that it says exactly what it will do,
# does exactly that and nothing else, and refuses everything that is plainly
# not the thing being claimed.

POPULATED_NO_MARKER = docker.ImportState(
    "populated", "903 rows in tw_char.characters, 110 rows in tw_logon.account"
)
"""The install the adopt press exists for, in the owner's own numbers.

`complete` at its default False, which is what `MarkerGate.probe()` answers on
this branch: the m910q Tortoise install reads exactly this, and it is why T14's
press refuses there (T19, finding 1).
"""

MARKER_PRESENT = docker.ImportState(
    "imported", f"tw_world.{sqlplan.MARKER_TABLE} records a finished import", complete=True
)
"""What the databases read as once this press has written its row."""

NOT_POPULATED = {
    "absent": docker.ImportState("absent", "none of tw_world, tw_char exists on this server yet"),
    "partial": docker.ImportState("partial", "tw_char exists but there is no import marker"),
    "unreadable": docker.ImportState("unreadable", "the databases would not answer"),
}
"""Every probe answer this press refuses, by the name a reader uses.

`imported` is not here: it has its own refusal, because "already done" and
"nothing to work with" are different sentences and a user meeting the wrong one
is told to do the wrong thing.
"""


def adoptable(
    plan: object, rec: Recorder, *, gaps: tuple[str, ...] = (), **overrides: object
) -> CmangosInstaller:
    """`engine_with_sql`, with a gate that also answers the presence question.

    `engine_with_sql` attaches `CallableGate(rec.probe, rec.reset)`, whose
    `adoption_gaps()` fails CLOSED — a gate built from the AzerothCore probe
    pair has no plan to read tables off and says so. That is the right default
    and the wrong fixture, so the gate is replaced here rather than the default
    widened: a test file that could not tell "the gate cannot look" from
    "nothing is missing" could not see the difference the press turns on.
    """
    engine = engine_with_sql(plan, rec, **overrides)
    engine._test_gate = native.CallableGate(rec.probe, rec.reset, lambda: gaps)  # type: ignore[attr-defined]
    return engine


def stopper() -> tuple[object, list[list[str]]]:
    """A `stop_db` seam and the record of what it was asked to stop."""
    stopped: list[list[str]] = []

    def stop_db(containers: list[str]) -> None:
        stopped.append(list(containers))

    return stop_db, stopped


def a_press(
    rec: Recorder,
    *,
    world: bool | None = False,
    db_up: bool | None = False,
    gaps: tuple[str, ...] = (),
    **overrides: object,
) -> tuple[CmangosInstaller, list[list[str]]]:
    """The engine an adopt press runs on, and the record of what it stopped.

    Every seam this press touches, given by name: the world it refuses on, the
    database container it may have to put back down, and the stop it puts it
    back down with. Defaulted to the state the press is FOR — world down,
    database down, nothing missing — so a test says only what it changes.
    """
    stop_db, stopped = stopper()
    engine = adoptable(
        rerun_plan(),
        rec,
        gaps=gaps,
        world_running=a_world_that_is(world),
        db_running=lambda container: db_up,
        stop_db=stop_db,
        **overrides,
    )
    return engine, stopped


def marker_scripts(rec: Recorder) -> list[str]:
    """Every script fed to the client that mentions the marker table."""
    return [script for script in rec.sql_scripts if sqlplan.MARKER_TABLE in script]


# -- the confirmation --------------------------------------------------------


def test_the_adopt_confirmation_names_the_row_it_will_write(tmp_path: Path) -> None:
    """The schema, the table and the plan hash, exactly as the writer spells them.

    The whole of this press is one row, so a dialog that described its EFFECT
    ("records the import as finished") rather than the act would be asking for
    consent to something the user cannot check afterwards. The hash is
    `plan.plan_hash()` — the string `sqlplan.write_marker()` puts in the row —
    asked of the plan rather than remembered, so an app upgrade between the
    dialog and the press cannot make the two disagree.

    Catches the hash dropped from the dialog, the schema named through the
    `schemas` mapping rather than as `plan.marker_db` (the writer's own
    spelling), and a dialog that names the table but not where it goes.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    plan = rerun_plan()
    engine = adoptable(plan, Recorder())
    said = engine.adopt_confirmation(InstallOptions(server_dir=server_dir))
    assert str(server_dir) in said
    assert plan.marker_db in said
    assert sqlplan.MARKER_TABLE in said
    assert plan.plan_hash() in said
    assert "ONE row" in said


def test_the_adopt_confirmation_says_the_consequence_in_the_owners_own_words(
    tmp_path: Path,
) -> None:
    """`ADOPT_CONSEQUENCE`, verbatim, and it is the sentence the feature turns on.

    Every other control in this package refuses on a reading it took itself.
    This is the one place a reading is replaced by a consent, and the sentence
    says so in those words: Yu'lon cannot check that the import finished, the
    person is saying it did, and if it did not the updates button will run the
    flagged files on an unfinished database.

    Pinned as one whole string rather than by keyword, because a paraphrase
    that kept the keywords would be a different promise.

    Catches the sentence reworded, softened, or dropped from the dialog.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    said = adoptable(rerun_plan(), Recorder()).adopt_confirmation(
        InstallOptions(server_dir=server_dir)
    )
    assert native.ADOPT_CONSEQUENCE in said
    assert "cannot check that the import finished; you are saying so" in said


def test_the_adopt_confirmation_lists_every_database_the_row_is_a_claim_about(
    tmp_path: Path,
) -> None:
    """The plan's own schemas, from the same reading `MarkerGate` builds its names from.

    A dialog listing `plan.create` would name none of them for Tortoise, whose
    `create` is empty and whose schemas are all reached through a phase's
    `into`.

    Catches the list read off `plan.create`.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    said = adoptable(rerun_plan(), Recorder()).adopt_confirmation(
        InstallOptions(server_dir=server_dir)
    )
    # The listing LINE and not the whole dialog: every one of these names also
    # appears elsewhere in the text — `mangos` is the marker schema, and
    # "characters" is an English word in the clause about what is read — so a
    # substring test over the paragraph would go green on an empty list.
    listed = next(line for line in said.splitlines() if line.startswith("These databases:"))
    for name in (CM_ENTRY.databases.world, CM_ENTRY.databases.characters, CM_ENTRY.databases.auth):
        assert name in listed, (name, listed)


def test_the_adopt_confirmation_says_the_world_must_be_stopped_and_the_database_put_back(
    tmp_path: Path,
) -> None:
    """Two facts a user meets afterwards, said before they agree.

    The stopped-world clause is here as well as in the refusal for the reason
    `updates_confirmation()`'s is: a user who meets the refusal has already
    paid for the dialog. The put-back clause is here because it is a state
    change this press makes that nothing else announces.

    Catches either clause dropped.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    said = adoptable(rerun_plan(), Recorder()).adopt_confirmation(
        InstallOptions(server_dir=server_dir)
    )
    assert "press Stop on the Server tab" in said
    assert "stopped again afterwards if this press was what started it" in said


# -- the press ---------------------------------------------------------------


def test_an_adopt_press_writes_the_marker_statement_and_nothing_else(tmp_path: Path) -> None:
    """The whole of what this press does, asserted at the seam the SQL leaves through.

    One script reaches the client and it is `sqlplan.write_marker()`'s own: the
    `CREATE TABLE IF NOT EXISTS` for the marker table and the `INSERT` of this
    plan's hash. Everything a press with this consent must never do is named
    rather than counted — no `DROP`, no `CREATE DATABASE`, no `CREATE USER`, no
    file streamed — because the danger here is per statement and a count would
    go green on the wrong one.

    Catches the write routed through anything but `write_marker()` (a second
    spelling of the row), the flagged phase applied as well, and
    `create_schemas()` reached.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, MARKER_PRESENT)
    rec.db_started = False
    engine, _ = a_press(rec)
    list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert len(rec.sql_scripts) == 1, rec.sql_scripts
    script = rec.sql_scripts[0]
    plan = rerun_plan()
    assert f"CREATE TABLE IF NOT EXISTS `{plan.marker_db}`.`{sqlplan.MARKER_TABLE}`" in script
    assert f"INSERT INTO `{plan.marker_db}`.`{sqlplan.MARKER_TABLE}`" in script
    assert plan.plan_hash() in script
    for forbidden in ("DROP", "CREATE DATABASE", "CREATE USER", "GRANT", "ALTER"):
        assert forbidden not in script, script
    assert EVERY_PRESS not in rec.sql_calls, rec.sql_calls
    assert MARKED_ONLY not in rec.sql_calls, rec.sql_calls
    assert "reset" not in rec.calls, rec.calls


def test_an_adopt_press_reads_the_databases_again_and_reports_them_as_imported(
    tmp_path: Path,
) -> None:
    """The only proof this press has that the row landed where the gate looks.

    The writer answers by not raising, which is the client's exit status and not
    a reading of the row. So the gate is asked once more afterwards, and the
    press says what it now reads — which is also the line that tells the user
    the updates button is live for this install.

    TWO probes on this route, deliberately, where T14's press takes exactly one:
    there the second question could take the destructive arm of a five-branch
    table, and here the second question is the receipt. T14's own one-probe test
    is about T14's route and stays as it is.

    Catches the re-probe dropped (the press then reports success on an exit
    status), and the report line that names the updates button removed.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, MARKER_PRESENT)
    rec.db_started = False
    engine, _ = a_press(rec)
    said = list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert rec.calls.count("probe") == 2, rec.calls
    assert any("now read as imported" in line for line in said), said
    assert any(native.UPDATES_BUTTON_LABEL in line for line in said), said


def test_an_adopt_press_refuses_when_the_row_does_not_read_back(tmp_path: Path) -> None:
    """A write that did not land where the gate looks is a failure, not a success.

    The alternative is a green press and a button that goes on offering itself,
    which is the shape this whole ticket exists to close.

    Catches the re-probe read and discarded.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, POPULATED_NO_MARKER)
    rec.db_started = False
    engine, _ = a_press(rec)
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "still read as populated" in str(raised.value)
    assert "do not treat this install as adopted" in str(raised.value)


def test_an_adopt_press_never_starts_the_world(tmp_path: Path) -> None:
    """The one container this press may start, and the one it may not.

    `up` is not in the tuple, so nothing here can bring the world back — the
    user stopped it in order to run this.

    Catches `up` added to `adopt_stages()`.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, MARKER_PRESENT)
    rec.db_started = False
    engine, _ = a_press(rec)
    list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert rec.db_started is True
    assert not any(call.startswith("start ") for call in rec.calls), rec.calls
    assert "start" not in rec.calls, rec.calls


def test_the_adopt_tuple_is_the_database_and_the_row_and_nothing_else() -> None:
    """Two stages, named, and neither of them the family's `import`.

    `update_stages()` selects `import` because that stage IS the re-run route.
    This tuple must not: `import` is a five-branch table whose `absent` arm runs
    the whole plan and whose `partial` arm drops every schema it names, and this
    press consented to one INSERT.

    Catches `import` selected into this tuple, and either stage recorded — a
    state file naming a stage no install tuple carries is a record the next
    resume has to interpret.
    """
    engine = installer_for(TORTOISE)
    stages = engine.adopt_stages()
    assert [stage.name for stage in stages] == ["start-db", "adopt"]
    for forbidden in ("import", "up", "build", "generate-compose", "write-dockerfile"):
        assert forbidden not in [stage.name for stage in stages]
    assert all(stage.recorded is False for stage in stages)


# -- the database this press may have started --------------------------------


def test_a_database_this_press_started_is_stopped_again(tmp_path: Path) -> None:
    """Put back what was found: the user stopped this server to run this.

    The reading is taken BEFORE the first stage, so what goes back down is what
    was down. Leaving the database up afterwards would be this app changing
    something nobody asked it to change, on a stack the user had stopped.

    Catches the stop dropped, and the stop reaching for the whole install
    (`stop_staged`) rather than the one container this press started.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, MARKER_PRESENT)
    rec.db_started = False
    engine, stopped = a_press(rec, db_up=False)
    said = list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert stopped == [[CM_ENTRY.container_spec().db]], stopped
    assert any("stopped again" in line for line in said), said


def test_a_database_that_was_already_up_is_left_up(tmp_path: Path) -> None:
    """The other half, and the one that costs if it is wrong.

    A database somebody else's press left up — a module import running on
    another tab, a maintenance dump — is not this press's to take down.

    Catches the stop made unconditional.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, MARKER_PRESENT)
    engine, stopped = a_press(rec, db_up=True)
    said = list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert stopped == [], stopped
    assert not any("stopped again" in line for line in said), said


def test_a_database_whose_state_could_not_be_read_is_left_exactly_as_it_was(
    tmp_path: Path,
) -> None:
    """`None` is not "it was down". Fail closed, pointed at a smaller question.

    `docker.world_running()` answers `None` for a missing container and for a
    daemon that will not reply, and reading either as "down" would have this
    press stop a container it never started.

    Catches the reading taken as a bool.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, MARKER_PRESENT)
    engine, stopped = a_press(rec, db_up=None)
    list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert stopped == [], stopped


def test_a_database_this_press_started_goes_back_down_after_a_refusal_too(
    tmp_path: Path,
) -> None:
    """Putting back what this press started is not conditional on the press working.

    The refusal is the one the user reads; the container going back down is not
    announced there (a generator whose consumer has gone may not yield again) and
    is logged instead.

    Catches the stop moved onto the success path only.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(NOT_POPULATED["partial"])
    rec.db_started = False
    engine, stopped = a_press(rec, db_up=False)
    with pytest.raises(InstallerError):
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert stopped == [[CM_ENTRY.container_spec().db]], stopped


# -- the refusals ------------------------------------------------------------


def test_an_adopt_press_while_the_world_is_up_writes_nothing(tmp_path: Path) -> None:
    """8.7a's rule on this route, and the refusal names THIS button.

    A refusal from the adopt button telling the user to press "Apply pending
    database updates…" again would be an instruction that does the wrong thing
    when followed — the defect T7's ticket is titled after — so the guard takes
    the label of the press it is refusing.

    Catches the guard's button label left hard-coded to the updates one, and the
    guard not reached from this press at all.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER)
    engine, _ = a_press(rec, world=True)
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "world server is running" in str(raised.value)
    assert native.ADOPT_BUTTON_LABEL in str(raised.value)
    assert native.UPDATES_BUTTON_LABEL not in str(raised.value)
    assert rec.sql_calls == [], rec.sql_calls
    assert rec.calls == [], "not one seam was touched: the refusal is the first thing that runs"


def test_an_adopt_press_that_cannot_tell_whether_the_world_is_up_writes_nothing(
    tmp_path: Path,
) -> None:
    """`None` is not "no", and the remedy names Docker first.

    The likeliest reason nobody can answer is that the daemon is not answering,
    and "Stop the server" is then an instruction the user cannot follow.

    Catches the `None` branch folded into the `False` one, and the dead-daemon
    clause dropped.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER)
    engine, _ = a_press(rec, world=None)
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "could not tell whether" in str(raised.value)
    assert "check that Docker is running" in str(raised.value)
    assert native.ADOPT_BUTTON_LABEL in str(raised.value)
    assert rec.sql_calls == [], rec.sql_calls


def test_a_world_started_while_the_database_came_up_is_refused_before_the_row(
    tmp_path: Path,
) -> None:
    """The window between the two readings, closed the way T14 closed its own.

    `start_database()` waits for the container to report healthy and the Server
    tab's Start is a button the same user can press meanwhile, so the first
    reading is that old by the time the row would be written.

    Catches the second reading deleted (`_guard_then` not wrapped round the
    adopt stage).
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER)
    rec.db_started = False
    seam, asked = worlds(False, True)
    stop_db, _ = stopper()
    engine = adoptable(
        rerun_plan(),
        rec,
        world_running=seam,
        db_running=lambda container: False,
        stop_db=stop_db,
    )
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "world server is running" in str(raised.value)
    assert rec.db_started is True, "the database really was started between the two readings"
    assert marker_scripts(rec) == [], rec.sql_scripts
    assert asked == [CM_ENTRY.container_spec().world] * 2, asked


def test_a_world_started_at_the_pre_write_line_is_refused_before_the_row(
    tmp_path: Path,
) -> None:
    """The window the reviewer of the adopt press found: the wrapper's two readings
    are older than the probe, the gap queries and the "Writing one row" line,
    and a consumer paused at that line leaves the world free to start before the
    statement goes out. The reading that decides is taken immediately before the
    write, and this run's world comes up exactly then: two readings say down,
    the third says up, and no marker statement is sent.

    Catches the third reading deleted (the write goes out on the stale readings).
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER)
    seam, asked = worlds(False, False, True)
    stop_db, _ = stopper()
    engine = adoptable(
        rerun_plan(),
        rec,
        world_running=seam,
        db_running=lambda container: True,
        stop_db=stop_db,
    )
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine.adopt_as_imported(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert any("Writing one row" in line for line in said), said
    assert "world server is running" in str(raised.value)
    assert marker_scripts(rec) == [], rec.sql_scripts
    assert len(asked) == 3, asked


def test_an_adopt_press_over_databases_that_already_carry_the_marker_refuses(
    tmp_path: Path,
) -> None:
    """ "Already done" and "nothing to work with" are different sentences.

    Refused rather than skipped silently: a press that reported success having
    written nothing would teach the user the button is decorative. The remedy
    names the updates button, because that is what somebody pressing this on a
    marked install was probably reaching for.

    Catches the `imported` arm folded into the not-populated one, and a press
    that writes a second row over a marker that is already there.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(MARKER_PRESENT)
    rec.db_started = False
    engine, _ = a_press(rec)
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "already carry Yu'lon's marker" in str(raised.value)
    assert native.UPDATES_BUTTON_LABEL in str(raised.value)
    assert marker_scripts(rec) == [], rec.sql_scripts


@pytest.mark.parametrize("named", sorted(NOT_POPULATED))
def test_an_adopt_press_over_databases_that_are_not_populated_refuses(
    tmp_path: Path, named: str
) -> None:
    """Adopting is a claim about an import somebody already made.

    Over `absent` and `partial` there is nothing to make the claim about, and
    over `unreadable` nobody could look. Enumerated rather than sampled: each
    of the three is a different thing to tell the user to do next, and the
    `unreadable` one has to name Docker because a database that is down and a
    daemon that is down read the same.

    Catches the precondition widened to "not imported", and the `unreadable`
    remedy telling the user to install.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(NOT_POPULATED[named])
    rec.db_started = False
    engine, _ = a_press(rec)
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "do not hold an import to adopt" in str(raised.value)
    assert named in str(raised.value), "the refusal does not say what it read"
    assert marker_scripts(rec) == [], rec.sql_scripts
    if named == "unreadable":
        assert "Docker is running" in str(raised.value)
    else:
        assert "Install this server" in str(raised.value)


def test_an_adopt_press_refuses_when_the_plan_names_something_that_is_not_there(
    tmp_path: Path,
) -> None:
    """`populated` short-circuits on the first row; the row this writes is about the plan.

    A `populated` answer says one `player_data` table has somebody's rows in it.
    It says nothing about the plan's other schemas or its other tables, and this
    press writes a row saying the whole plan finished — so the gate is asked
    once more, for presence only, and a gap is a refusal naming it.

    Catches the gap check dropped, and a gap reported without saying what it is.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER)
    rec.db_started = False
    engine, _ = a_press(rec, gaps=("tw_logon.account is not there",))
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "tw_logon.account is not there" in str(raised.value)
    assert "plainly did not" in str(raised.value)
    assert marker_scripts(rec) == [], rec.sql_scripts


def test_a_gate_that_cannot_be_asked_which_tables_are_there_refuses(tmp_path: Path) -> None:
    """A list of gaps has no way to say "I could not look", so the raise is the answer.

    `probe()` may not raise and turns this into `unreadable`; `adoption_gaps()`
    may, and must, because an empty tuple from a database that never answered
    would read as "everything is there" — the one answer that would let the row
    be written over a database nobody could see.

    Catches the reading wrapped in a `try` that answers `()`, and the raise
    escaping as a bare `DockerCommandError` rather than as a sentence.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER)
    rec.db_started = False

    def angry() -> tuple[str, ...]:
        raise docker.DockerCommandError("No such container: tw-db")

    stop_db, _ = stopper()
    engine = adoptable(
        rerun_plan(),
        rec,
        world_running=a_world_that_is(False),
        db_running=lambda container: False,
        stop_db=stop_db,
    )
    engine._test_gate = native.CallableGate(rec.probe, rec.reset, angry)  # type: ignore[attr-defined]
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert "could not be asked which of this plan's tables" in str(raised.value)
    assert "No such container" in str(raised.value)
    assert marker_scripts(rec) == [], rec.sql_scripts


def test_a_marker_the_client_refuses_is_the_sentence_the_user_reads(tmp_path: Path) -> None:
    """The writer failing, named, with nothing claimed about the install afterwards.

    `sqlplan._run_sql()` already turns both of its failures into an
    `InstallerError` naming the marker, so it is not wrapped in a second
    sentence here.

    Catches the write left outside anything that reports it, and a press that
    goes on to re-probe and report success after a failed write.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER, MARKER_PRESENT)
    rec.db_started = False
    rec.failing_sql = sqlplan.MARKER_TABLE
    engine, _ = a_press(rec)
    said: list[str] = []
    with pytest.raises(InstallerError) as raised:
        for line in engine.adopt_as_imported(InstallOptions(server_dir=server_dir)):
            said.append(line)
    assert "marker" in str(raised.value).lower()
    assert not any("now read as imported" in line for line in said), said


def test_a_game_whose_plan_has_no_rerunnable_phase_refuses_the_adopt_press_itself(
    tmp_path: Path,
) -> None:
    """Adopting buys nothing where no press would then do anything new.

    The button is not offered for such a game, but `adopt_as_imported()` is a
    method and the greying is in the view. AzerothCore imports through a compose
    one-shot and carries no phase list at all.

    Catches the refusal deleted.
    """
    engine = installer_for(WOTLK)
    with pytest.raises(InstallerError) as raised:
        list(engine.adopt_as_imported(InstallOptions(server_dir=tmp_path / "wotlk")))
    assert "no phase meant to be re-applied" in str(raised.value)
    assert "Nothing was started." in str(raised.value)


def test_a_family_with_no_marker_to_write_refuses_rather_than_writing_one(
    tmp_path: Path,
) -> None:
    """The spine's own answer, for a family that never learned to adopt.

    `adopt_gate()` and `marker_row()` are both `None` there, and a gate built
    out of the AzerothCore probe pair answers `adoption_gaps()` with the one gap
    it can honestly report — it has no plan to read tables off.

    Catches `CallableGate.adoption_gaps()` defaulted to `()`, which would have a
    gate that never looked report that nothing is missing.
    """
    assert native.CallableGate(lambda: MARKER_PRESENT, None).adoption_gaps() != ()
    server_dir = tmp_path / "wotlk"
    server_dir.mkdir()
    engine = installer_for(WOTLK)
    assert engine.marker_row() is None
    with pytest.raises(InstallerError) as raised:
        engine.adopt_confirmation(InstallOptions(server_dir=server_dir))
    assert "keeps no completion marker" in str(raised.value)
    with pytest.raises(InstallerError) as refused:
        list(engine.stage_adopt(engine._update_context(server_dir, None)))
    assert "keeps no completion marker" in str(refused.value)


def test_an_adopt_press_can_be_stopped_before_it_reaches_the_row(tmp_path: Path) -> None:
    """The cancel every other staged run honours, honoured here too.

    **A guard, and honestly labelled as one**, exactly as T14's counterpart is:
    deleting the explicit `_check_cancel()` from `adopt_as_imported()` leaves
    this green, because `_staged()` asks the same question before each stage.
    What the explicit one buys is not visible from here — it refuses before
    `_update_context()` reads the state file and resolves the install's database
    password, and before the database container is read. Measured 2026-09-10;
    that mutation is equivalent, not uncaught.

    What this does catch is the cancel never reaching the stages at all: a press
    that drops the event rather than putting it on the context.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    rec = ready_to_import(POPULATED_NO_MARKER)
    stop = threading.Event()
    stop.set()
    engine, _ = a_press(rec)
    with pytest.raises(InstallerError):
        list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir), cancel=stop))
    assert rec.sql_calls == [], rec.sql_calls


# -- the reading the button is offered on ------------------------------------


@pytest.mark.parametrize("answer", [POPULATED_NO_MARKER, MARKER_PRESENT, *NOT_POPULATED.values()])
def test_adopt_state_passes_the_gates_answer_through(
    tmp_path: Path, answer: docker.ImportState
) -> None:
    """The reading the tab greys the button on, and it is the gate's own.

    One question with one implementation: the answer that offers the button and
    the answer the press refuses on come out of the same `probe()`, so a button
    that appears cannot be a button whose press then says there is nothing to do.

    Catches a second probe written for the view.
    """
    rec = ready_to_import(answer)
    engine, _ = a_press(rec)
    assert engine.adopt_state(InstallOptions(server_dir=tmp_path / "srv")) == answer


def test_adopt_state_answers_unreadable_when_the_probe_raises(tmp_path: Path) -> None:
    """A status path has nowhere to put an exception, and a raise must not offer a control.

    `probe()` promises not to raise; this is the boundary that holds if some
    future gate forgets, because the one outcome that must never follow from a
    failed question is a button that writes a marker row appearing.

    Catches the `try` removed from `adopt_state()`.
    """

    def angry() -> docker.ImportState:
        raise RuntimeError("the daemon is not there")

    rec = ready_to_import(POPULATED_NO_MARKER)
    stop_db, _ = stopper()
    engine = adoptable(rerun_plan(), rec, world_running=a_world_that_is(False), stop_db=stop_db)
    engine._test_gate = native.CallableGate(angry, rec.reset, lambda: ())  # type: ignore[attr-defined]
    state = engine.adopt_state(InstallOptions(server_dir=tmp_path / "srv"))
    assert state.state == "unreadable"
    assert "the daemon is not there" in state.detail


# -- the round trip: adopt, then the button that could not reach this install --


class _MarkerlessServer:
    """One fake server that starts with no marker row and learns one when it is written.

    The doubles above answer the probe from a canned list, which is what makes
    the branch tests readable and is exactly what this one may not use: the
    claim here is that the row this press writes is the row the NEXT press
    reads, and a probe that answers from a list would go green whatever the
    write said. So this fake holds the server's state — which schemas exist,
    which tables are in them, how many rows, and the marker's hash once one has
    been inserted — and both a real `sqlplan.MarkerGate` and the marker writer
    are pointed at it.

    Modelled on `test_sqlplan.py`'s `_Server`, kept here rather than imported
    for `a_world_that_is()`'s reason: this file already imports fixtures out of
    `test_families_cmangos.py`, and a second cross-file import for one class
    would tie three test files together to save twenty lines. What it does that
    the other does not is APPLY the marker script — that is the whole point.
    """

    def __init__(self, plan: object, schemas: Mapping[str, str]) -> None:
        self.plan = plan
        self.names = list(sqlplan.plan_schemas(plan, schemas))  # type: ignore[arg-type]
        self.tables = {schemas[data.db]: {data.table} for data in plan.player_data}  # type: ignore[attr-defined]
        self.rows = {
            (schemas[data.db], data.table): 903 for data in plan.player_data  # type: ignore[attr-defined]
        }
        self.marker_hash: str | None = None
        self.statements: list[str] = []
        self.scripts: list[str] = []

    def sql_query(
        self,
        container: str,
        client: str,
        password: str,
        schema: str | None,
        statement: str,
        *,
        wsl_distro: str | None = None,
    ) -> str:
        self.statements.append(statement)
        if statement == "SHOW DATABASES":
            return "\n".join(["information_schema", *self.names]) + "\n"
        exists = re.search(r"table_schema='([^']+)' AND table_name='([^']+)'", statement)
        if exists:
            return "1\n" if exists.group(2) in self.tables.get(exists.group(1), set()) else "0\n"
        if statement.startswith("SELECT plan_hash"):
            return f"{self.marker_hash}\n" if self.marker_hash else ""
        count = re.search(r"SELECT COUNT\(\*\) FROM `([^`]+)`\.`([^`]+)`", statement)
        if count:
            return f"{self.rows.get((count.group(1), count.group(2)), 0)}\n"
        raise AssertionError(f"unexpected query: {statement}")

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        text = source.read().decode("utf-8")
        self.scripts.append(text)
        made = re.search(r"CREATE TABLE IF NOT EXISTS `([^`]+)`\.`([^`]+)`", text)
        if made:
            self.tables.setdefault(made.group(1), set()).add(made.group(2))
        inserted = re.search(
            r"INSERT INTO `[^`]+`\.`[^`]+` \(plan_hash[^)]*\) VALUES \('([^']+)'", text
        )
        if inserted:
            self.marker_hash = inserted.group(1)
        return subprocess.CompletedProcess(list(argv), 0, "", "")


def test_the_updates_button_reaches_an_install_this_press_adopted(tmp_path: Path) -> None:
    """T19's whole point, end to end, over a database that remembers what was written.

    The install is the owner's: every schema the plan names, characters and
    accounts in them, and no `yulon_install` table anywhere. T14's button
    refuses it — `populated`, and `import_reads_as_finished()` is False — which
    is the fact this ticket was filed on. The adopt press writes the row; the
    same databases then read `imported` through the SAME real `MarkerGate`; and
    the updates press applies the flagged phase, which is the thing no button in
    the app could do for this install before.

    The gate is a real `sqlplan.MarkerGate` over the fake server rather than a
    canned answer, because the claim is that the row WRITTEN is the row READ:
    a probe answering from a list would pass this test with the writer deleted.

    Catches the marker written with a hash the gate does not recognise, written
    into a schema the gate does not look in, and a `CREATE TABLE` the probe's
    `information_schema` reading cannot then see.
    """
    server_dir = tmp_path / "srv"
    server_dir.mkdir()
    plan = rerun_plan()
    rec = Recorder()
    rec.db_started = True
    stop_db, _ = stopper()
    engine = engine_with_sql(
        plan,
        rec,
        world_running=a_world_that_is(False),
        db_running=lambda container: True,
        stop_db=stop_db,
    )
    server = _MarkerlessServer(plan, engine._schemas())
    gate = sqlplan.MarkerGate(
        plan,
        container=CM_ENTRY.container_spec().db,
        client="mariadb",
        password="pw",
        schemas=engine._schemas(),
        sql_query=server.sql_query,
        exec_stdin=server.exec_stdin,
    )
    engine._test_gate = gate  # type: ignore[attr-defined]
    engine._seams.exec_stdin = server.exec_stdin  # type: ignore[assignment]

    before = gate.probe()
    assert before.state == "populated", before
    assert native.import_reads_as_finished(before) is False, "the install T14's button refuses"
    with pytest.raises(InstallerError) as refused:
        list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert "do not read as a finished import" in str(refused.value)

    said = list(engine.adopt_as_imported(InstallOptions(server_dir=server_dir)))
    assert any("now read as imported" in line for line in said), said
    assert server.marker_hash == plan.plan_hash(), server.marker_hash

    after = gate.probe()
    assert after.state == "imported", after
    assert native.import_reads_as_finished(after) is True
    applied = list(engine.update_databases(InstallOptions(server_dir=server_dir)))
    assert any(EVERY_PRESS in script for script in server.scripts), server.scripts
    assert not any(MARKED_ONLY in script for script in server.scripts), server.scripts
    assert any("The import marker is unchanged." in line for line in applied), applied
    assert server.marker_hash == plan.plan_hash(), "the updates press left the marker alone"
