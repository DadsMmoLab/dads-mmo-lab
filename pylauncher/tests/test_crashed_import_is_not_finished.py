"""T678: an AzerothCore database import whose importer dies is never recorded as finished.

Two facts made a crashed first import read as a finished one. AzerothCore's importer
creates every schema's `updates` tables before it applies the updates, so one that
dies part-way leaves schemas the probe reads as `imported` (T658, m910q 2026-10-10).
And the install's import and Repair never looked at the importer's exit: they ran it
as attached `compose up --no-deps <importer>`, which exits 0 whatever the one-shot
exits (measured on yulon-ubuntu, Compose 2.40.3, 2026-10-11: a one-shot that printed
`ERROR 1146` and exited 1 left `compose up` at 0, and `--exit-code-from` at 1).

Now the one-shot's own status comes back, a non-zero one is a failure on both routes
whatever the probe reads, and the folder records an import as unfinished from before
it starts until it is verified, so a run cut off with nothing of Yu'lon left to record
it (power lost, Docker or WSL gone) is not trusted either.
"""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from tests.support_native import ABSENT, IMPORTED, Recorder, engine, install
from yulon import docker
from yulon.catalog.installer import InstallerError, InstallOptions, InstallStopped
from yulon.controller import ENDED_IMPORT_DETAIL, Controller

IMPORTER = "ac-db-import"
IMPORT_UP_ARGV = ["docker", "compose", "up", "--no-deps", "--exit-code-from", IMPORTER, IMPORTER]
POPULATED_DONE = docker.ImportState("populated", "400 accounts", complete=True)

# The importer's own last lines, from the T706 live run on yulon-ubuntu (2026-10-11).
BROKEN_FILE = "/azerothcore/modules/mod-city-bots/data/sql/db-world/updates/zz_t706_broken.sql"
DIED = (
    ">> Applying update \"zz_t706_broken.sql\" 'AD3D0CA'...",
    "ERROR 1146 (42S02) at line 3: Table 'acore_world.t706_no_such_table' doesn't exist",
    f"Applying of file '{BROKEN_FILE}' to database 'acore_world' failed! If you are a user, "
    "please pull the latest revision from the repository.",
    "Could not update the World database, see log for details.",
)


def _marker(server: Path) -> Path:
    return docker.one_shot_ended_marker(server, IMPORTER)


def _press(
    rec: Recorder, server: Path, importer: docker.AttachedRun | None = None, **overrides: object
) -> list[str]:
    """One Install press. `importer`: what the import one-shot alone comes back with.

    The real post-check, not the Recorder's always-imported double: what it reads is
    what the probe answers, here `imported`, as a dead importer leaves it (T658).
    """
    if importer is not None:

        def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
            rec.calls.append(f"one-shot:{service}")
            return importer if service == IMPORTER else docker.AttachedRun(0, ("ran",))

        overrides["one_shot"] = one_shot
    return install(rec, server, verify_import=docker.verify_import, **overrides)


# ------------------------------------------------------------------ the install


def test_an_install_whose_importer_exits_non_zero_is_not_finished(tmp_path: Path) -> None:
    """THE bug: exit 1, probe `imported` afterwards, and the install went on to start."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError) as raised:
        _press(rec, server, docker.AttachedRun(1, DIED))
    said = str(raised.value)
    assert "zz_t706_broken.sql" in said and "mod-city-bots" in said, said
    assert "acore_world" in said, said
    assert "ERROR 1146" not in said, "the importer's own words are under Details (T214)"
    assert "unfinished" in said and "nothing was started" in said, said
    assert "Details" in said and "ERROR 1146" in raised.value.detail
    assert "start" not in rec.calls, f"the server was started on it: {rec.calls}"
    assert _marker(server).is_file(), "nothing records the import as unfinished"


def test_an_importer_that_exits_with_no_file_named_still_fails(tmp_path: Path) -> None:
    """A database that restarted, a full disk: no update named, its last words instead."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError) as raised:
        _press(rec, server, docker.AttachedRun(1, ("Lost connection to MySQL server",)))
    assert "exit 1" in str(raised.value) and "Lost connection" in raised.value.detail
    assert _marker(server).is_file()


def test_the_next_press_clears_what_the_crashed_import_left_and_imports_again(
    tmp_path: Path,
) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError):
        _press(rec, server, docker.AttachedRun(1, DIED))
    rec.calls.clear()
    rec.probe_answers = [IMPORTED, IMPORTED]
    lines = _press(rec, server, docker.AttachedRun(0, ("World database is up-to-date!",)))
    assert "reset-everything" in rec.calls, rec.calls
    assert rec.calls.index("reset-everything") < rec.calls.index(f"one-shot:{IMPORTER}")
    assert not any("leaving them alone" in line for line in lines), lines
    assert not _marker(server).exists(), "a verified import keeps the record"


def test_the_record_stands_while_the_importer_runs(tmp_path: Path) -> None:
    """Power lost or Yu'lon killed mid-import: nothing of ours runs after, so write it first."""
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    seen: list[bool] = []

    def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        if service == IMPORTER:
            seen.append(_marker(server_dir).is_file())
        return docker.AttachedRun(0, ("ran",))

    _press(rec, server, one_shot=one_shot)
    assert seen == [True], seen
    assert not _marker(server).exists()


def test_a_crashed_import_that_left_rows_is_refused_not_called_finished(tmp_path: Path) -> None:
    """A module's own updates seed accounts (City Bots: 400) before the world schema is done.

    Those rows read as player data, which is never cleared; the press says so and stops
    rather than reading the databases as imported and starting the server on them.
    """
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError):
        _press(rec, server, docker.AttachedRun(1, DIED))
    rec.calls.clear()
    rec.probe_answers = [POPULATED_DONE]
    with pytest.raises(InstallerError) as raised:
        _press(rec, server)
    said = str(raised.value)
    assert "did not finish" in said and "400 accounts" in said, said
    assert not any(call.startswith("reset") for call in rec.calls), rec.calls
    assert f"one-shot:{IMPORTER}" not in rec.calls
    assert "start" not in rec.calls, rec.calls


def test_a_stop_whose_importer_comes_back_killed_is_still_the_stop(tmp_path: Path) -> None:
    """With the one-shot's own status back, a Stop's kill can surface as 137: that is the Stop."""
    rec = Recorder()
    cancel = threading.Event()

    def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        if service != IMPORTER:
            return docker.AttachedRun(0, ("ran",))
        cancel.set()
        return docker.AttachedRun(137, ("importing acore_world",))

    with pytest.raises(InstallStopped):
        list(
            engine(rec, one_shot=one_shot, verify_import=docker.verify_import).run(
                InstallOptions(server_dir=tmp_path / "s"), cancel=cancel
            )
        )


# ---------------------------------------------------------------------- Repair


def _repair_with_exit(
    monkeypatch: pytest.MonkeyPatch, calls: list[list[str]], exit_code: int
) -> None:
    from tests.test_docker import SPEC, _repair_doubles

    _repair_doubles(
        monkeypatch, calls, running={SPEC.db}, import_exit=exit_code, import_output=lambda: DIED
    )


def test_a_repair_whose_importer_exits_non_zero_fails_and_stays_on_offer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, UNIMPORTED, _probe

    calls: list[list[str]] = []
    _repair_with_exit(monkeypatch, calls, 1)
    with pytest.raises(docker.DockerCommandError) as raised:
        docker.repair_import(SPEC, tmp_path, _probe(UNIMPORTED, IMPORTED))
    said = str(raised.value)
    assert "zz_t706_broken.sql" in said and "mod-city-bots" in said, said
    assert "ERROR 1146" in raised.value.detail, raised.value.detail
    assert _marker(tmp_path).is_file()
    # The Server tab reads it as unfinished, so Repair is offered again and Start refused.
    ctl = Controller(SPEC, tmp_path, import_probe=_probe(IMPORTED))
    state = ctl.import_state()
    assert state.repairable and state.detail == ENDED_IMPORT_DETAIL, state


def test_a_repair_after_a_crashed_one_clears_and_imports_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, UNIMPORTED, _probe

    calls: list[list[str]] = []
    _repair_with_exit(monkeypatch, calls, 1)
    with pytest.raises(docker.DockerCommandError):
        docker.repair_import(SPEC, tmp_path, _probe(UNIMPORTED, IMPORTED))
    _repair_with_exit(monkeypatch, calls, 0)
    resets: list[bool] = []

    def reset(*, everything: bool = False) -> tuple[str, ...]:
        resets.append(everything)
        return ("acore_auth", "acore_characters", "acore_world")

    assert docker.repair_import(SPEC, tmp_path, _probe(IMPORTED, IMPORTED), reset=reset) is True
    assert resets == [True]
    assert not _marker(tmp_path).exists()


def test_a_repair_records_the_import_before_it_runs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, UNIMPORTED, _probe, _repair_doubles

    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    real_stream = docker.runner.stream
    seen: list[bool] = []

    def stream(argv: list[str], **kw: object) -> Iterator[str]:
        if "--exit-code-from" in argv or argv[-1] == SPEC.import_service:
            seen.append(_marker(tmp_path).is_file())
        return real_stream(argv, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(docker.runner, "stream", stream)
    assert docker.repair_import(SPEC, tmp_path, _probe(UNIMPORTED, IMPORTED)) is True
    assert seen == [True], seen
    assert not _marker(tmp_path).exists()


# ------------------------------------------------------------- the one-shot's status


def test_the_import_one_shot_asks_compose_for_its_own_exit_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attached `compose up` exits 0 whatever the one-shot does; `--exit-code-from` does not."""
    seen: list[list[str]] = []

    def stream(argv: list[str], **_kw: object) -> Iterator[str]:
        seen.append(argv)
        yield "applying"
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(docker.runner, "stream", stream)
    run = docker.run_one_shot(IMPORTER, tmp_path)
    assert run.returncode == 1
    assert seen[0][-5:] == ["up", "--no-deps", "--exit-code-from", IMPORTER, IMPORTER], seen


# The end of a real `compose up --exit-code-from ac-db-import` run with a failing update
# (yulon-ubuntu, 2026-10-11): every importer line carries compose's prefix, and compose's
# own closing lines come after them.
COMPOSE_UP_SAID = (
    "Attaching to ac-db-import",
    "ac-db-import  | >> Applying update \"zz_t678_broken.sql\" '5B1F6C2'...",
    "ac-db-import  | ERROR 1146 (42S02) at line 1: Table 'acore_world.t678_no_such_table' "
    "doesn't exist",
    f"ac-db-import  | Applying of file '{BROKEN_FILE}' to database 'acore_world' failed!",
    "ac-db-import  | Could not update the World database, see log for details.",
    "ac-db-import  |",
    "Aborting on container exit...",
    "Container ac-db-import  Stopping",
    "Container ac-db-import  Stopped",
)


def test_the_importers_words_are_read_through_composes_prefix_and_closing_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Details and the sentence show mysql's error, not "Container ac-db-import Stopped"."""

    def stream(argv: list[str], **_kw: object) -> Iterator[str]:
        yield from COMPOSE_UP_SAID
        raise subprocess.CalledProcessError(1, argv)

    monkeypatch.setattr(docker.runner, "stream", stream)
    run = docker.run_one_shot(IMPORTER, tmp_path)
    assert run.returncode == 1
    assert "Stopped" not in run.tail[-1] and "Could not update" in run.tail[-2], run.tail
    words = docker.importer_last_words(run.tail)
    assert any(line.startswith("ERROR 1146") for line in words), words
    assert "" not in words, "a bare `ac-db-import  |` line read as a line of its own"
    rebuild = docker.importer_failure(run, "Applying the database updates")
    assert "(ERROR 1146 (42S02)" in rebuild, rebuild
    assert "zz_t678_broken.sql" in docker.import_failed(IMPORTER, tmp_path, run).detail


# ------------------------------------------------- review round 2: refuse + a clean redo
#
# Owner decision (2026-10-11): an import that died after it wrote rows (City Bots' 400
# accounts land in acore_auth before acore_world fails) is refused by Start, and Repair
# clears what it wrote and imports again -- but ONLY under the record written before that
# import began, which stands only over databases that read empty or were cleared. Rows
# without that record are somebody's, and nothing clears them.

AUTH, CHARACTERS, WORLD = "acore_auth", "acore_characters", "acore_world"


def _city_bots_crashed_server():  # type: ignore[no-untyped-def]
    """acore_auth finished with City Bots' 400 accounts; acore_world died part-way."""
    from tests.test_ended_import_is_not_trusted import _done, _Server

    return _Server(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: ["creature"]},
        counts={f"{AUTH}.account": 400},
    )


def _real_seams(server):  # type: ignore[no-untyped-def]
    """The WotLK probe and reset, the real functions, on the in-memory database."""
    from yulon.controller_wow_wotlk import repair

    def probe() -> docker.ImportState:
        return repair.import_state(server, server)

    def reset(**kw: bool) -> tuple[str, ...]:
        return repair.reset_unfinished(server, server, **kw)

    return probe, reset


def _the_import_fills(server) -> None:  # type: ignore[no-untyped-def]
    from tests.test_ended_import_is_not_trusted import _done

    server.tables.update(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("creature")}
    )


def _crashed_repair(monkeypatch: pytest.MonkeyPatch, server_dir: Path) -> None:
    """A real Repair from empty whose importer died: the record it leaves."""
    from tests.test_docker import SPEC, UNIMPORTED, _probe

    _repair_with_exit(monkeypatch, [], 1)
    with pytest.raises(docker.DockerCommandError):
        docker.repair_import(SPEC, server_dir, _probe(UNIMPORTED, IMPORTED))
    assert docker.import_record_is_from_empty(server_dir, SPEC.import_service)


def test_start_refuses_rows_a_crashed_first_import_left_and_points_at_repair(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC
    from yulon.controller import ImportEnded

    _crashed_repair(monkeypatch, tmp_path)
    probe, reset = _real_seams(_city_bots_crashed_server())
    assert probe().state == "populated"
    ctl = Controller(SPEC, tmp_path, import_probe=probe, reset_unfinished=reset)
    with pytest.raises(ImportEnded) as refused:
        ctl.refuse_an_ended_import()
    said = str(refused.value)
    assert "did not finish" in said and "Repair" in said, said
    assert ctl.import_state().repairable, "the Server tab must offer the Repair it names"


def test_repair_clears_what_the_crashed_import_wrote_and_imports_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, _repair_doubles

    _crashed_repair(monkeypatch, tmp_path)
    server = _city_bots_crashed_server()
    probe, reset = _real_seams(server)
    calls: list[list[str]] = []
    _repair_doubles(
        monkeypatch,
        calls,
        running={SPEC.db},
        import_output=lambda: (_the_import_fills(server), ("World database is up-to-date!",))[1],
    )
    said: list[str] = []
    assert (
        docker.repair_import(
            SPEC, tmp_path, probe, reset=reset, output=said.append, safety_copy=lambda: "COPY"
        )
        is True
    )
    assert server.dropped == [AUTH, CHARACTERS, WORLD], server.dropped
    assert "COPY" in said, "the safety copy was not named"
    assert IMPORT_UP_ARGV in calls
    assert not _marker(tmp_path).exists(), "a verified import keeps the record"
    after = probe()  # City Bots seeds its accounts again: a finished import with rows
    assert after.state == "populated" and after.complete, after


@pytest.mark.parametrize("record", [None, "ended"], ids=["no-record", "only-a-kill-record"])
def test_rows_without_the_record_from_empty_are_never_cleared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, record: str | None
) -> None:
    """Player data: no record, or only T658's kill record (written over any databases)."""
    from tests.test_docker import SPEC, _repair_doubles
    from yulon.controller import ImportEnded

    if record is not None:
        _marker(tmp_path).write_text(f"{SPEC.import_service} was ended before it finished\n")
    server = _city_bots_crashed_server()
    probe, reset = _real_seams(server)
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    with pytest.raises(docker.DockerCommandError, match="player data"):
        docker.repair_import(SPEC, tmp_path, probe, reset=reset)
    assert server.dropped == []
    assert IMPORT_UP_ARGV not in calls
    ctl = Controller(SPEC, tmp_path, import_probe=probe, reset_unfinished=reset)
    try:
        ctl.refuse_an_ended_import()
    except ImportEnded as exc:  # pragma: no cover - the assertion below names it
        pytest.fail(f"Start refused a server with player data: {exc}")


def test_the_reset_itself_refuses_rows_unless_told_they_are_the_imports() -> None:
    """The guard twice (`reset_unfinished`'s own rule): `everything` alone clears no rows."""
    server = _city_bots_crashed_server()
    _probe, reset = _real_seams(server)
    with pytest.raises(RuntimeError, match="player data"):
        reset(everything=True)
    assert server.dropped == []


def test_the_record_from_empty_is_never_written_over_databases_with_rows(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The invariant the clearing rests on, on both routes that write the record."""
    from tests.test_docker import SPEC, _repair_doubles

    server = _city_bots_crashed_server()
    probe, reset = _real_seams(server)
    _repair_doubles(monkeypatch, [], running={SPEC.db})
    with pytest.raises(docker.DockerCommandError):
        docker.repair_import(SPEC, tmp_path, probe, reset=reset)
    assert not _marker(tmp_path).exists()

    rec = Recorder(probe_answers=[POPULATED_DONE])
    _press(rec, tmp_path / "s")  # a finished import with rows: left alone
    assert f"one-shot:{IMPORTER}" not in rec.calls
    assert not _marker(tmp_path / "s").exists()


def test_a_stop_ending_our_own_import_keeps_the_record_from_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`end_one_shot()`'s kill record must not replace it, or the redo is lost."""
    from tests.test_ended_import_is_not_trusted import _Daemon

    monkeypatch.setattr(docker, "pinned_project_name", lambda server_dir: "wow-server")
    monkeypatch.setattr(docker.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(docker, "_docker", _Daemon([IMPORTER], tmp_path))
    docker.mark_import_under_way(tmp_path, IMPORTER)
    assert docker.end_one_shot(IMPORTER, tmp_path, record_ended=True) is None
    assert docker.import_record_is_from_empty(tmp_path, IMPORTER)


def test_the_install_press_points_rows_from_its_crashed_import_at_repair(tmp_path: Path) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError):
        _press(rec, server, docker.AttachedRun(1, DIED))
    rec.calls.clear()
    rec.probe_answers = [POPULATED_DONE]
    with pytest.raises(InstallerError) as raised:
        _press(rec, server)
    assert "Repair" in str(raised.value), str(raised.value)
    assert "empty folder" not in str(raised.value)
    assert not any(call.startswith("reset") for call in rec.calls), rec.calls


def test_the_failure_sentence_names_repair_and_no_way_that_does_not_work() -> None:
    run = docker.AttachedRun(1, DIED)
    said = docker.importer_failure(run, "The database import", first_import=True)
    assert "Repair" in said and "mod-city-bots" in said, said
    assert "without it" not in said, said


def test_composes_exit_line_takes_none_of_the_importers_last_words() -> None:
    tail = (
        *COMPOSE_UP_SAID[:5],
        "ac-db-import exited with code 1",
        "ac-db-import  | one more line of its own",
    )
    words = docker.importer_last_words(tail)
    assert not any("exited with code" in line for line in words), words
    assert words[-1] == "one more line of its own", words


# ------------------------------------------------- review round 3: never a player's rows
#
# Two ways the record outlived the unfinished import and Repair's clean redo would have
# deleted real players: a restored backup, and a server started by hand whose own updater
# finished the world and that people then played on. Belt and braces: a finished restore
# removes the record, a character anywhere refuses the clear, and a safety copy of the
# three databases is taken before any of it is cleared.


def _played_on_server(characters: int = 3):  # type: ignore[no-untyped-def]
    from tests.test_ended_import_is_not_trusted import _done, _Server

    return _Server(
        {AUTH: _done("account"), CHARACTERS: _done("characters"), WORLD: _done("creature")},
        counts={f"{AUTH}.account": 400, f"{CHARACTERS}.characters": characters},
    )


def test_the_probe_counts_the_characters() -> None:
    probe, _reset = _real_seams(_played_on_server(3))
    assert probe().characters == 3
    probe, _reset = _real_seams(_city_bots_crashed_server())
    assert probe().characters == 0


def test_a_finished_restore_removes_the_record_so_repair_clears_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, _repair_doubles
    from tests.test_maintenance import (
        AT,
        DB,
        FakeMysql,
        a_backup_of,
        plan_restore,
        restore,
        running,
    )

    docker.mark_import_under_way(tmp_path, SPEC.import_service)
    mysql = FakeMysql(("acore_auth", "acore_characters", "acore_world"))
    plan = plan_restore(a_backup_of(tmp_path, "acore_characters"), tmp_path, running=running(DB))
    restore(plan, mysql, confirm=plan.token, running=running(DB), now=AT)
    assert not _marker(tmp_path).exists(), "the restore left the unfinished-import record"

    server = _played_on_server()
    probe, reset = _real_seams(server)
    _repair_doubles(monkeypatch, [], running={SPEC.db})
    copies: list[str] = []
    with pytest.raises(docker.DockerCommandError, match="player data"):
        docker.repair_import(
            SPEC, tmp_path, probe, reset=reset, safety_copy=lambda: copies.append("x") or "COPY"
        )
    assert server.dropped == [] and copies == []


def test_characters_under_the_record_are_never_cleared(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Started by hand after the crash: the world finished, people played, the record stands."""
    from tests.test_docker import SPEC, _repair_doubles

    _crashed_repair(monkeypatch, tmp_path)
    server = _played_on_server()
    probe, reset = _real_seams(server)
    calls: list[list[str]] = []
    _repair_doubles(monkeypatch, calls, running={SPEC.db})
    copies: list[str] = []
    with pytest.raises(docker.DockerRefusal) as refused:
        docker.repair_import(
            SPEC, tmp_path, probe, reset=reset, safety_copy=lambda: copies.append("x") or "COPY"
        )
    said = str(refused.value)
    assert "characters" in said and "Maintenance" in said, said
    assert server.dropped == [] and copies == []
    assert IMPORT_UP_ARGV not in calls


def test_the_reset_itself_refuses_characters_even_when_told_the_rows_are_seeded() -> None:
    server = _played_on_server()
    _probe, reset = _real_seams(server)
    with pytest.raises(RuntimeError, match="characters"):
        reset(everything=True, seeded=True)
    assert server.dropped == []


def test_the_seeded_clear_takes_the_safety_copy_first(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_docker import SPEC, _repair_doubles

    _crashed_repair(monkeypatch, tmp_path)
    server = _city_bots_crashed_server()
    probe, real_reset = _real_seams(server)
    order: list[str] = []

    def reset(**kw: bool) -> tuple[str, ...]:
        order.append("clear")
        return real_reset(**kw)

    def safety_copy() -> str:
        order.append("copy")
        assert server.dropped == [], "copied after the clear"
        return "A copy of the databases was saved first, in backups: x.sql."

    _repair_doubles(
        monkeypatch,
        [],
        running={SPEC.db},
        import_output=lambda: (_the_import_fills(server), ("done",))[1],
    )
    said: list[str] = []
    docker.repair_import(
        SPEC, tmp_path, probe, reset=reset, output=said.append, safety_copy=safety_copy
    )
    assert order == ["copy", "clear"], order
    assert any("saved first" in line for line in said), said


@pytest.mark.parametrize("copy", ["none", "fails"])
def test_no_safety_copy_no_clear(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, copy: str
) -> None:
    from tests.test_docker import SPEC, _repair_doubles

    _crashed_repair(monkeypatch, tmp_path)
    server = _city_bots_crashed_server()
    probe, reset = _real_seams(server)
    _repair_doubles(monkeypatch, [], running={SPEC.db})

    def failing() -> str:
        raise OSError("disk full")

    with pytest.raises(docker.DockerCommandError):
        docker.repair_import(
            SPEC, tmp_path, probe, reset=reset, safety_copy=None if copy == "none" else failing
        )
    assert server.dropped == []


def test_the_repair_confirmation_says_what_is_cleared_and_that_a_copy_comes_first(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from tests.test_controller_view import WOTLK, _Ps, _services
    from yulon import runner
    from yulon.controller import UNFINISHED_IMPORT_WROTE_DETAIL
    from yulon.ui.controller_view import ControllerView
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(runner, "run", _Ps())
    view = ControllerView(
        WOTLK, _services(_Ps(), tmp_path, []), status_poll_ms=0, job_runner=run_inline
    )
    view._import_state = docker.ImportState(
        "partial", f"{UNFINISHED_IMPORT_WROTE_DETAIL} (400 rows in acore_auth.account)"
    )
    view.repair_import()  # the first press only arms it
    said = view.danger_label.text()
    assert "400 rows in acore_auth.account" in said, said
    assert "copy" in said and "DELETED" in said, said
    assert "hold no accounts" not in said, said


# ------------------------------- T757: a failed first install has no tab to point at yet
#
# A tab exists only once an install has succeeded (`ui/catalog_view.py`), so the install's
# own failure sentences name the way to one ("Use existing…" on the folder) or Install
# again; Repair's route, run from the Server tab, keeps naming that tab.


def test_a_failed_install_on_a_modules_file_says_how_to_reach_the_tabs_it_names(
    tmp_path: Path,
) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError) as raised:
        _press(rec, server, docker.AttachedRun(1, DIED))
    said = str(raised.value)
    assert "Use existing" in said and str(server) in said, said
    assert said.index("Use existing") < said.index("Modules tab"), said
    assert said.index("Use existing") < said.index("Server tab"), said


def test_a_failed_install_with_no_file_named_says_install_again_and_names_no_tab(
    tmp_path: Path,
) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError) as raised:
        _press(rec, server, docker.AttachedRun(1, ("Lost connection to MySQL server",)))
    said = str(raised.value)
    assert "Install again" in said, said
    assert "Server tab" not in said and "Modules tab" not in said, said


def test_the_install_press_on_rows_its_crashed_import_left_says_how_to_reach_repair(
    tmp_path: Path,
) -> None:
    server = tmp_path / "s"
    rec = Recorder(probe_answers=[ABSENT, IMPORTED])
    with pytest.raises(InstallerError):
        _press(rec, server, docker.AttachedRun(1, DIED))
    rec.calls.clear()
    rec.probe_answers = [POPULATED_DONE]
    with pytest.raises(InstallerError) as raised:
        _press(rec, server)
    said = str(raised.value)
    assert "Use existing" in said and str(server) in said, said
    assert said.index("Use existing") < said.index("Server tab"), said


def test_repair_the_database_on_its_tab_keeps_naming_the_server_tab(tmp_path: Path) -> None:
    """The same import failing under "Repair the database…", which IS on the Server tab."""
    from yulon.database_presence import Reading

    server = tmp_path / "s"
    rec = Recorder(images=True)
    install(rec, server)
    rec.calls.clear()
    rec.db_started = False
    rec.probe_answers = [ABSENT, IMPORTED]

    def one_shot(service: str, server_dir: Path, **_kw: object) -> docker.AttachedRun:
        rec.calls.append(f"one-shot:{service}")
        return docker.AttachedRun(1, DIED) if service == IMPORTER else docker.AttachedRun(0, ())

    repair = engine(
        rec,
        one_shot=one_shot,
        verify_import=docker.verify_import,
        read_database=lambda entry, d: Reading("missing"),
    ).repair_database(InstallOptions(server_dir=server))
    with pytest.raises(InstallerError) as raised:
        list(repair)
    said = str(raised.value)
    assert "on the Server tab" in said and "mod-city-bots" in said, said
    assert "Use existing" not in said, said
