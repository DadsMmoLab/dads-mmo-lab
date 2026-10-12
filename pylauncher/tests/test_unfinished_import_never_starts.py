"""T739: no Rebuild, rollback or update starts the world while an import record stands.

Round 2 (cold review of 7855e1c7, the lead's decision): under the record a Rebuild
compiles and tags as usual, applies no database update and never starts the world; it
ends stopped and names Repair. Refusing it (round 1) left a server whose build is gone
(T627) with no button: Start says Rebuild, Rebuild said Repair, and Repair's importer
image is one only a Rebuild makes.

Live on yulon-ubuntu2 (2026-10-12, T712's proof): a Repair import died on a module's SQL
and left `.yulon-ac-db-import-ended`, so Start was refused. Rebuild was not: its import
failed again, its rollback put the copy back and then STARTED the old build on the
unfinished databases ("The server is up."). The world wrote 100 bot accounts and 1000
bot characters into them, and the Repair the Server tab offered then refused ("characters
were found"). Start refused, Repair refused: stuck.

Every test drives the real `rebuild()` / `update_to_latest()` through
`tests.support_native.Recorder`, with the real WotLK probe (`repair.import_state()`) over
an in-memory database whose world WRITES the live run's rows whenever it is started, and
then the real `docker.repair_import()` on the same database. The probe answers
`unreadable` while no database container runs, as the real one does (`docker exec`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support_native import ENTRY, FakeSnapshot, Recorder, install
from tests.test_crashed_import_is_not_finished import DIED
from tests.test_ended_import_is_not_trusted import _done, _Server
from tests.test_update_to_latest import _gated  # noqa: F401 - autouse: CMaNGOS engines' gate
from yulon import docker, resources
from yulon.catalog import native
from yulon.catalog.families.azerothcore import AzerothCoreInstaller
from yulon.catalog.installer import InstallerError, InstallOptions
from yulon.controller_wow_wotlk import repair

SERVICE = ENTRY.containers.db_import or ""
AUTH, CHARACTERS, WORLD = "acore_auth", "acore_characters", "acore_world"
KILLED = f"{SERVICE} was ended before it finished\n"
"""T658's record (`end_one_shot()`), written over whatever databases there are."""
FAILED_IMPORT = docker.AttachedRun(1, DIED)


def _world_writes_its_bots(server: _Server) -> None:
    """What the world the rollback started wrote on yulon-ubuntu2 (live.log 02:31Z)."""
    server.counts[f"{AUTH}.account"] = 100
    server.counts[f"{CHARACTERS}.characters"] = 1000


def _machine(
    tmp_path: Path, server: _Server
) -> tuple[Recorder, Path, AzerothCoreInstaller, FakeSnapshot]:
    """A finished WotLK install, stopped, whose databases are `server`; its Rebuild engine."""
    rec = Recorder(images=True)
    server_dir = tmp_path / "wow"
    install(rec, server_dir)
    rec.calls.clear()
    rec.db_started = False  # stopped: no database container to `docker exec` into
    # A world started on these databases writes into them, whichever press starts it.
    rec.on_recreate = lambda control: _world_writes_its_bots(server)

    def probe() -> docker.ImportState:
        state = (
            repair.import_state(server, server)  # type: ignore[arg-type]
            if rec.db_started
            else docker.ImportState("unreadable", "no database container is running")
        )
        rec.calls.append(f"probe:{state.state}")
        return state

    made = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=probe,
        reset_unfinished=lambda **kw: repair.reset_unfinished(server, server, **kw),  # type: ignore[arg-type]
        seams=rec.seams(),
    )
    fake = FakeSnapshot(rec)
    made._snapshot = fake
    return rec, server_dir, made, fake


def _repair(monkeypatch: pytest.MonkeyPatch, server_dir: Path, server: _Server) -> bool:
    """The Server tab's Repair, the real `docker.repair_import()` on the same databases."""
    from tests.test_docker import SPEC, _repair_doubles

    def import_output() -> tuple[str, ...]:
        server.tables.update(
            {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("creature")}
        )
        return ("World database is up-to-date!",)

    _repair_doubles(monkeypatch, [], running={SPEC.db}, import_output=import_output)
    return docker.repair_import(
        SPEC,
        server_dir,
        lambda: repair.import_state(server, server),  # type: ignore[arg-type]
        reset=lambda **kw: repair.reset_unfinished(server, server, **kw),  # type: ignore[arg-type]
        safety_copy=lambda: "COPY",
    )


def _no_world_started(rec: Recorder) -> None:
    assert "recreate" not in rec.calls, rec.calls
    assert "start" not in rec.calls, rec.calls


def _press(made: AzerothCoreInstaller, server_dir: Path, **kw: object) -> list[str]:
    return list(made.rebuild(InstallOptions(server_dir=server_dir), **kw))  # type: ignore[arg-type]


# -- the Rebuild under the record: built, stopped, Repair next --------------------------------


def _crashed_first_import(server_dir: Path, server: _Server) -> None:
    """The live state: Repair from empty died on a module's SQL (City Bots' accounts in).

    `docker.mark_import_under_way()` is what `repair_import()` writes before its importer.
    """
    docker.mark_import_under_way(server_dir, SERVICE)
    server.tables.update(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: ["creature"]}
    )
    server.counts[f"{AUTH}.account"] = 400


@pytest.mark.parametrize("gone", [False, True], ids=["build-there", "build-gone"])
def test_a_rebuild_on_a_half_imported_server_builds_stays_stopped_then_repair_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gone: bool
) -> None:
    """The ticket's press, and the review's dead end: the build gone (T627) as well.

    Start says Rebuild for a gone build; the Rebuild compiles every image, the importer's
    included (Repair runs it from the runtime compose, which can only name the image), takes
    the old servers down and starts nothing; Repair then clears and imports.
    """
    server = _Server({})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    before = record.read_text(encoding="utf-8")
    if gone:
        rec.images = False  # Docker no longer has the build: no rollback to keep (T170)
    rec.ready = False  # no world runs, so none ever reports ready
    rec.ready_specs.clear()  # the install's own wait

    said = _press(made, server_dir, missing_images_ok=gone)

    assert rec.ready_specs == [], "nothing was started, so nothing is waited for"

    assert [
        r for r in made.image_refs_at(server_dir) if SERVICE.split("-", 1)[1] in r
    ], "the compile makes the importer's image too"
    assert native.held_rebuild_ending(ENTRY.name, server_dir) == said[-1], said[-3:]
    _no_world_started(rec)
    assert "build" in rec.calls and f"one-shot:{SERVICE}" not in rec.calls, rec.calls
    assert not fake.taken, "no update is applied, so no copy is taken"
    spec = ENTRY.container_spec()
    servers = [f"remove_service:{spec.service_for(c)}" for c in (spec.auth, spec.world)]
    assert all(c in rec.calls for c in servers), rec.calls
    assert rec.calls.index("stop_servers") < min(rec.calls.index(c) for c in servers)
    assert record.read_text(encoding="utf-8") == before, "the record stands"
    assert f"{CHARACTERS}.characters" not in server.counts, "no character was written"
    assert made.start_refusal(server_dir) == native.UNFINISHED_IMPORT_REFUSAL
    # Repair's own refusal ("characters were found") does not fire: it clears and imports.
    assert _repair(monkeypatch, server_dir, server) is True
    assert server.dropped == [AUTH, CHARACTERS, WORLD], server.dropped
    assert not record.exists()


def test_a_rebuild_over_a_killed_import_asks_the_database_it_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T658's record on a stopped server: the database is started to be asked, it reads
    `imported`, so the Rebuild holds as under the other record."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    record.write_text(KILLED, encoding="utf-8")

    said = _press(made, server_dir)

    assert said[-1] == native.held_rebuild_ending(ENTRY.name, server_dir), said[-3:]
    assert rec.calls.index("start-db") < rec.calls.index("build"), "asked before the compile"
    _no_world_started(rec)
    assert f"one-shot:{SERVICE}" not in rec.calls and not fake.taken, rec.calls
    assert record.read_text(encoding="utf-8") == KILLED
    assert server.counts == {}
    assert _repair(monkeypatch, server_dir, server) is True
    assert not record.exists()


def test_a_killed_record_over_a_players_rows_rebuilds_and_starts_as_before(tmp_path: Path) -> None:
    """T658's record over player data refuses no Start (`Controller.refuse_an_ended_import`),
    so a Rebuild there imports and starts as it always did."""
    server = _Server(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")},
        counts={f"{AUTH}.account": 3, f"{CHARACTERS}.characters": 7},
    )
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")

    said = _press(made, server_dir)

    assert rec.calls.count("recreate") == 1, rec.calls
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_no_record_rebuilds_as_before(tmp_path: Path) -> None:
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)

    said = _press(made, server_dir)

    assert rec.calls.count("recreate") == 1 and f"one-shot:{SERVICE}" in rec.calls, rec.calls
    assert not [c for c in rec.calls if c.startswith("remove_service:")], rec.calls
    assert said[-1] == f"{ENTRY.name} was rebuilt and is running in {server_dir}"


def test_the_rebuild_question_is_asked_under_the_record(tmp_path: Path) -> None:
    """Round 1 refused before the question; the Rebuild is now the way past a gone build."""
    server = _Server({})
    _rec, server_dir, made, _fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    assert made.rebuild_refusal_before_asking(server_dir) is None


# -- the update route: refused up front; its rollback and its new build never start ---------


def _updatable(rec: Recorder, server_dir: Path) -> None:
    from tests.test_update_to_latest import NEW

    for source in ENTRY.emulator.sources:
        rec.upstream[server_dir / source.dest] = NEW
    rec.clones.clear()


def test_an_update_on_a_half_imported_server_refuses_before_it_fetches(tmp_path: Path) -> None:
    """Kept from round 1: Update and Return cannot meet a gone build (they refuse one, T170,
    and Rebuild is that way out), so the up-front refusal leaves no dead end."""
    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _updatable(rec, server_dir)
    _crashed_first_import(server_dir, server)

    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert str(raised.value) == f"{native.UNFINISHED_IMPORT_REFUSAL} Nothing was started."
    assert rec.clones == [], "nothing was fetched"
    _no_world_started(rec)


def test_an_update_rollback_stays_stopped_when_the_record_cannot_be_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review (a): T658's record, the database never readable (the probe answers
    `unreadable` throughout), the update's import fails: the rollback leaves it stopped."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    _updatable(rec, server_dir)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    record.write_text(KILLED, encoding="utf-8")
    rec.one_shot_result = FAILED_IMPORT

    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert fake.put_back_calls, "the ground: the import failed and the copy went back"
    assert "unreadable" in rec_probe_answers(rec), "the ground: the record could not be asked"
    _no_world_started(rec)
    assert "left STOPPED" in said and native.ENDED_IMPORT_REFUSAL in said, said
    assert record.read_text(encoding="utf-8") == KILLED
    assert server.counts == {}
    assert _repair(monkeypatch, server_dir, server) is True


def test_an_updates_new_build_is_not_started_when_the_record_cannot_be_asked(
    tmp_path: Path,
) -> None:
    """The same with an import that exits 0: an update's run does not finish an import."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _updatable(rec, server_dir)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    record.write_text(KILLED, encoding="utf-8")

    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert rec.calls.count(f"one-shot:{SERVICE}") == 1, "the ground: the import ran, exit 0"
    _no_world_started(rec)
    assert said.startswith(native.NEW_BUILD_NOT_STARTED_ON_AN_UNFINISHED_IMPORT), said
    assert "left STOPPED" in said and native.ENDED_IMPORT_REFUSAL in said, said
    assert record.read_text(encoding="utf-8") == KILLED


def test_an_update_rollback_on_finished_databases_still_starts_the_old_build(
    tmp_path: Path,
) -> None:
    """The guard asks the record: with none, a rollback starts the old build as before."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    _updatable(rec, server_dir)
    rec.one_shot_result = FAILED_IMPORT

    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count("recreate") == 1, rec.calls
    assert "is running again" in str(raised.value), raised.value


def rec_probe_answers(rec: Recorder) -> list[str]:
    return [c.split(":", 1)[1] for c in rec.calls if c.startswith("probe:")]


# -- parity with Start (`Controller.refuse_an_ended_import`) ----------------------------------

NONE, EMPTY, KILL = "no-record", "record-from-empty", "kill-record"
STATES = {
    "absent": docker.ImportState("absent", "no schemas"),
    "partial": docker.ImportState("partial", "acore_world is half there"),
    "imported": docker.ImportState("imported", "every schema is full", complete=True),
    "rows": docker.ImportState("populated", "400 accounts", complete=False, characters=0),
    "players": docker.ImportState("populated", "3 accounts", complete=True, characters=7),
    "unreadable": docker.ImportState("unreadable", "the database would not answer"),
}
START_REFUSES = {
    (EMPTY, "partial"),
    (EMPTY, "imported"),
    (EMPTY, "rows"),
    (EMPTY, "players"),
    (KILL, "partial"),
    (KILL, "imported"),
}
"""Where `Controller.refuse_an_ended_import()` refuses a Start (T658, T678)."""
STRICTER_FOR_THE_ENGINE = {
    (EMPTY, "absent"),
    (EMPTY, "unreadable"),
}
"""Where the engine refuses and Start does not, on purpose (T739): the record from empty
is never asked of the databases (the Controller leaves "absent" to its missing-database
refusal and lets "unreadable" go)."""
STRICTER_STILL_TO_START_ITS_OWN = {(KILL, "absent"), (KILL, "unreadable")}
"""Where only the engine's own start (`strict`: a rollback, the new build's start, the
install's `up`) refuses too (review (a)): one it cannot ask is left stopped."""


@pytest.mark.parametrize("record", [NONE, EMPTY, KILL])
@pytest.mark.parametrize("reads", list(STATES))
def test_the_engine_and_start_agree_on_the_record_except_where_said(
    tmp_path: Path, record: str, reads: str
) -> None:
    from tests.test_docker import SPEC
    from yulon.controller import Controller, ImportEnded

    assert SPEC.import_service == SERVICE
    server_dir = tmp_path / "wow"
    server_dir.mkdir()
    if record == EMPTY:
        docker.mark_import_under_way(server_dir, SERVICE)
    elif record == KILL:
        docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")
    state = STATES[reads]
    controller = Controller(SPEC, server_dir, import_probe=lambda: state)
    try:
        controller.refuse_an_ended_import()
        start = False
    except ImportEnded:
        start = True
    engine_ = AzerothCoreInstaller(
        ENTRY,
        installers_root=resources.installers_dir(),
        import_probe=lambda: state,
        seams=Recorder().seams(),
    )
    lenient = engine_.unfinished_import_refusal(server_dir) is not None
    strict = engine_.unfinished_import_refusal(server_dir, strict=True) is not None

    cell = (record, reads)
    assert start == (cell in START_REFUSES), cell
    assert lenient == (cell in START_REFUSES | STRICTER_FOR_THE_ENGINE), cell
    assert strict == (
        cell in START_REFUSES | STRICTER_FOR_THE_ENGINE | STRICTER_STILL_TO_START_ITS_OWN
    ), cell


def test_start_refusal_is_strict_and_a_rebuilds_own_ask_holds_instead(tmp_path: Path) -> None:
    """`start_refusal()` (the install's `up`, a rollback) names the record; a Rebuild's own
    first ask does not refuse on it: the press holds instead (above)."""
    server = _Server({})
    _rec, server_dir, made, _fake = _machine(tmp_path, server)
    docker.mark_import_under_way(server_dir, SERVICE)
    assert made.start_refusal(server_dir) == native.UNFINISHED_IMPORT_REFUSAL
    assert made.start_refusal(server_dir, rebuilding=True) is None


def test_no_record_refuses_nothing_and_asks_nothing(tmp_path: Path) -> None:
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    rec.db_started = True
    assert made.start_refusal(server_dir) is None
    assert not [c for c in rec.calls if c.startswith("probe")], "the record decides the ask"


# -- C: the closing note says what the press did ------------------------------------------------


def test_an_azerothcore_update_does_not_close_saying_it_ran_no_sql(tmp_path: Path) -> None:
    """Live (T707, yulon-ubuntu2): the update's importer applied 91 queries, then the panel
    said "This rebuild ran no SQL"."""
    from tests.test_update_rollback_database import WOTLK
    from tests.test_update_rollback_database import _press as update_press

    rec, _server_dir, _made, _fake, said, raised = update_press(tmp_path, WOTLK)

    assert raised is None, raised
    assert rec.calls.count(f"one-shot:{SERVICE}") == 1, "the ground: the update imported"
    assert native.REBUILD_CLOSING_NOTE_IMPORTED in said, said[-3:]
    assert not [line for line in said if "ran no SQL" in line], said[-3:]


def test_another_familys_update_does_not_claim_no_sql_either(tmp_path: Path) -> None:
    """A CMaNGOS update's own work names each file it applies (T531); the server's own
    updater's work is in its log, which the note says, rather than "named above"."""
    from tests.test_update_rollback_database import TBC
    from tests.test_update_rollback_database import _press as update_press

    _rec, _server_dir, _made, _fake, said, raised = update_press(tmp_path, TBC)

    assert raised is None, raised
    assert native.UPDATE_CLOSING_NOTE in said, said[-3:]
    assert not [line for line in said if "ran no SQL" in line], said[-3:]
    assert "worldserver log" in native.UPDATE_CLOSING_NOTE


def test_the_rebuild_question_says_what_the_press_will_do_under_the_record(
    tmp_path: Path,
) -> None:
    """The question promised a copy, the updates and a start; under the record it says it
    compiles, leaves the server stopped and that Repair comes next."""
    from yulon.catalog.installer import rebuild_confirmation

    server = _Server({})
    _rec, server_dir, _made, _fake = _machine(tmp_path, server)
    asked = rebuild_confirmation(ENTRY, server_dir)
    assert "copies your databases" in asked and native.HELD_CONFIRMATION not in asked
    _crashed_first_import(server_dir, server)
    asked = rebuild_confirmation(ENTRY, server_dir)
    assert native.HELD_CONFIRMATION in asked, asked
    assert "copies your databases" not in asked and "reports ready" not in asked, asked
    assert native.HELD_CONFIRMATION not in rebuild_confirmation(ENTRY, server_dir, kept_build=False)
