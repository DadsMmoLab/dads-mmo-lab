"""T739: no Rebuild, rollback or update starts the world while an import record stands.

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
        rec.calls.append("probe")
        if not rec.db_started:
            return docker.ImportState("unreadable", "no database container is running")
        return repair.import_state(server, server)  # type: ignore[arg-type]

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


# -- A: the rollback, and the new build's own start --------------------------------------------


def test_a_rollback_leaves_the_old_build_stopped_while_the_record_stands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuild pressed on a stopped server: the record cannot be asked yet, its rollback asks.

    T658's record over databases that read `imported` (an importer that was killed). The
    database is down at the press, so the probe answers `unreadable` and the press goes on,
    as Start would; the import fails; the copy goes back; the old build must NOT start.
    """
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    record.write_text(KILLED, encoding="utf-8")
    rec.one_shot_result = FAILED_IMPORT

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert fake.put_back_calls, "the ground: the import failed and the copy went back"
    _no_world_started(rec)
    assert "left STOPPED" in said, said
    assert native.ENDED_IMPORT_REFUSAL in said, said
    assert "Repair" in native.ENDED_IMPORT_REFUSAL
    assert record.read_text(encoding="utf-8") == KILLED, "the record stands, as it was"
    assert server.counts == {}, "nothing wrote into the unfinished databases"
    # Repair, which the sentence names, clears them and imports again.
    assert _repair(monkeypatch, server_dir, server) is True
    assert server.dropped == [AUTH, CHARACTERS, WORLD], server.dropped
    assert not record.exists()


def test_a_new_build_is_not_started_on_unfinished_databases_after_its_import_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same press with an import that exits 0: an update's run does not finish them.

    The Rebuild's importer applies only updates its `updates` tables lack; it never clears
    what an unfinished import wrote, nor lifts the record. So the new build's start is
    refused too, right before its containers are made, and the press rolls back stopped.
    """
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    record.write_text(KILLED, encoding="utf-8")

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert rec.calls.count(f"one-shot:{SERVICE}") == 1, "the ground: the import ran, exit 0"
    _no_world_started(rec)
    assert said.startswith(native.NEW_BUILD_NOT_STARTED_ON_AN_UNFINISHED_IMPORT), said
    assert "left STOPPED" in said and native.ENDED_IMPORT_REFUSAL in said, said
    assert fake.put_back_calls, "the copy went back before the old build was left stopped"
    assert record.read_text(encoding="utf-8") == KILLED
    assert server.counts == {}
    assert _repair(monkeypatch, server_dir, server) is True


def test_a_rollback_on_finished_databases_still_starts_the_old_build(tmp_path: Path) -> None:
    """The guard asks the record: with none, a rollback starts the old build as before."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    rec.one_shot_result = FAILED_IMPORT

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count("recreate") == 1, rec.calls
    assert "is running again" in str(raised.value), raised.value


def test_a_killed_record_over_a_players_rows_does_not_stop_the_rollback(tmp_path: Path) -> None:
    """T658's record over player data refuses no Start (`Controller.refuse_an_ended_import`).

    A rollback agrees with Start: it would otherwise leave a server stopped that Start then
    starts, and the Repair it named refuses player data.
    """
    server = _Server(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")},
        counts={f"{AUTH}.account": 3, f"{CHARACTERS}.characters": 7},
    )
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    docker.one_shot_ended_marker(server_dir, SERVICE).write_text(KILLED, encoding="utf-8")
    rec.one_shot_result = FAILED_IMPORT

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    assert rec.calls.count("recreate") == 1, rec.calls
    assert "is running again" in str(raised.value), raised.value


# -- B: a Rebuild or an update on a half-imported server refuses before anything --------------


def _crashed_first_import(server_dir: Path, server: _Server) -> None:
    """The live state: Repair from empty died on a module's SQL (City Bots' accounts in).

    `docker.mark_import_under_way()` is what `repair_import()` writes before its importer.
    """
    docker.mark_import_under_way(server_dir, SERVICE)
    server.tables.update(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: ["creature"]}
    )
    server.counts[f"{AUTH}.account"] = 400


def test_a_rebuild_on_a_half_imported_server_refuses_and_names_repair(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ticket's press. It died at the copy live (no acore_playerbots yet), and started
    the old build anyway; now it refuses before the first stage and names Repair."""
    server = _Server({})
    rec, server_dir, made, fake = _machine(tmp_path, server)
    _crashed_first_import(server_dir, server)
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    before = record.read_text(encoding="utf-8")
    rec.one_shot_result = FAILED_IMPORT

    with pytest.raises(InstallerError) as raised:
        list(made.rebuild(InstallOptions(server_dir=server_dir)))

    said = str(raised.value)
    assert said == f"{native.UNFINISHED_IMPORT_REFUSAL} Nothing was changed.", said
    assert "Repair" in native.UNFINISHED_IMPORT_REFUSAL
    _no_world_started(rec)
    assert not fake.taken, "no copy was attempted"
    assert f"one-shot:{SERVICE}" not in rec.calls and "build" not in rec.calls, rec.calls
    assert not [c for c in rec.calls if c.startswith("tag:")], "no rollback tags were made"
    assert record.read_text(encoding="utf-8") == before
    assert f"{CHARACTERS}.characters" not in server.counts, "no character was written"
    # Repair's own refusal ("characters were found") does not fire: it clears and imports.
    assert _repair(monkeypatch, server_dir, server) is True
    assert server.dropped == [AUTH, CHARACTERS, WORLD], server.dropped
    assert not record.exists()


def test_an_update_on_a_half_imported_server_refuses_before_it_fetches(tmp_path: Path) -> None:
    from tests.test_update_to_latest import NEW

    server = _Server({})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    for source in ENTRY.emulator.sources:
        rec.upstream[server_dir / source.dest] = NEW
    rec.clones.clear()
    _crashed_first_import(server_dir, server)

    with pytest.raises(InstallerError) as raised:
        list(made.update_to_latest(InstallOptions(server_dir=server_dir)))

    assert str(raised.value) == f"{native.UNFINISHED_IMPORT_REFUSAL} Nothing was started."
    assert rec.clones == [], "nothing was fetched"
    _no_world_started(rec)


def test_every_start_the_engine_makes_asks_the_record(tmp_path: Path) -> None:
    """`start_refusal()` is what the install's `stage_up()`, a Rebuild and a rollback ask."""
    server = _Server({})
    _rec, server_dir, made, _fake = _machine(tmp_path, server)
    docker.mark_import_under_way(server_dir, SERVICE)
    assert made.start_refusal(server_dir) == native.UNFINISHED_IMPORT_REFUSAL
    assert made.start_refusal(server_dir, rebuilding=True) == native.UNFINISHED_IMPORT_REFUSAL


def test_the_rebuild_question_is_refused_before_it_is_asked(tmp_path: Path) -> None:
    """The Server build menu asks `rebuild_refusal_before_asking()` before its question
    (T217 item 3), so the player is not asked about an hour's compile that then refuses."""
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    rec.db_started = True
    record = docker.one_shot_ended_marker(server_dir, SERVICE)
    record.write_text(KILLED, encoding="utf-8")
    assert made.rebuild_refusal_before_asking(server_dir) is None
    assert "probe" not in rec.calls, "the databases are not asked before the question"
    record.unlink()
    docker.mark_import_under_way(server_dir, SERVICE)
    assert made.rebuild_refusal_before_asking(server_dir) == native.UNFINISHED_IMPORT_REFUSAL


def test_no_record_refuses_nothing_and_asks_nothing(tmp_path: Path) -> None:
    server = _Server({AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("x")})
    rec, server_dir, made, _fake = _machine(tmp_path, server)
    rec.db_started = True
    assert made.start_refusal(server_dir) is None
    assert "probe" not in rec.calls, "the record alone decides whether the databases are asked"


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
    """A CMaNGOS update's own work may apply world updates (T531); the note claims neither."""
    from tests.test_update_rollback_database import TBC
    from tests.test_update_rollback_database import _press as update_press

    _rec, _server_dir, _made, _fake, said, raised = update_press(tmp_path, TBC)

    assert raised is None, raised
    assert native.UPDATE_CLOSING_NOTE in said, said[-3:]
    assert not [line for line in said if "ran no SQL" in line], said[-3:]
