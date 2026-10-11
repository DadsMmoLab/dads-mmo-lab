"""T681: a Restore is its server's only writer, and a restore that did not finish blocks starts.

Lifecycle audit L7, L8/L19, L9 and L10, through the real Maintenance tab and the real
`controller_wow_wotlk.maintenance` planner and restore (the harness of
`test_restore_starts_the_database.py`: only the census and the database client are doubles).

* L7: the cross-process reservation is re-entrant in one process, so a Modules job's SQL (or
  any other writer's hold) joined the restore's and ran during its load.
* L8/L19: the restore's marker was read by Move only; Start, Update, a Rebuild and a carried-on
  Bring-in started the server on half-written databases.
* L9: a dump that writes the `mysql` schema was restored, with no safety copy of it.
* L10: a restore or backup that worked was reported as failed when the database it started
  could not be stopped again.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import IO

import pytest

from tests.test_controller import _FakeRunner
from tests.test_controller_view import _last, _put_back_view
from tests.test_maintenance import good_dump
from tests.test_restore_starts_the_database import (
    SPEC,
    WOTLK,
    _failures,
    _Mysql,
    _Ps,
    _real_maintenance,
    _select_backup,
    _Stack,
    _view,
)
from yulon import docker, forgetting, runner
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.controller import Controller, StartRefused
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import MaintenanceError
from yulon.ui import controller_view as controller_view_module

INSTALL_PRESS = "Install mod-transmog"
GAME = maintenance.game_of(load_catalog().get("wow-wotlk"))


@pytest.fixture(autouse=True)
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    """Every `docker` command any press runs, recorded; nothing reaches a daemon."""
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _during_the_load(mysql: _Mysql, act: Callable[[], object]) -> list[object]:
    """Run `act` while the restore is loading (inside `load_from`), keeping what it answered."""
    seen: list[object] = []
    load = mysql.load_from

    def load_and_act(source: IO[bytes]) -> None:
        try:
            seen.append(act())
        except Exception as exc:  # noqa: BLE001 - the refusal IS the answer under test
            seen.append(exc)
        load(source)

    mysql.load_from = load_and_act  # type: ignore[method-assign]
    return seen


def _hold(server_dir: Path) -> object:
    """The seam every writer of a tab is handed (`_server_hold_for`), taken and let go."""
    with docker.server_hold(server_dir, INSTALL_PRESS, spec=None):
        return "held"


def _claim(server_dir: Path) -> object:
    """The applier's own seam (`hold_server=lambda press: docker.server_claim(...)`)."""
    with docker.server_claim(server_dir, press=INSTALL_PRESS):
        return "claimed"


# -- L7: nothing else of this Yu'lon writes during the load ------------------------------------


@pytest.mark.parametrize("seam", [_hold, _claim], ids=["server_hold", "server_claim"])
def test_every_other_writer_of_the_server_is_refused_while_a_restore_loads(
    qapp: object, tmp_path: Path, seam: Callable[[Path], object]
) -> None:
    """The applier's SQL, an account, a network write: each takes one of these two seams.

    Before T681 the take simply joined the restore's own reservation (re-entrant in one
    process) and the write ran in the middle of the load. Mutation: drop `alone=` from the
    restore's hold, or skip `_press_on()` in either seam.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    path = _select_backup(view, tmp_path)
    seen = _during_the_load(mysql, lambda: seam(tmp_path))

    view.show_restore_plan()
    view.run_restore()

    assert mysql.loaded == [path.read_bytes()], "the restore itself did not finish"
    assert len(seen) == 1 and isinstance(seen[0], docker.ServerHeldError), seen
    assert str(seen[0]) == forgetting.RESTORE_WRITES_ALONE
    # The restore's alone-ness ends with it: the same take works afterwards.
    assert seam(tmp_path) in ("held", "claimed")


def test_a_restore_pressed_while_another_job_writes_to_the_server_is_refused(
    qapp: object, tmp_path: Path
) -> None:
    """The other order: a Modules job holds the server, then Restore is pressed. Nothing loads.

    Mutation: drop the `inside` check from `hold_the_server(alone=...)`.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)
    view.show_restore_plan()
    assert view.restore_button.isEnabled(), view.maintenance_report.toPlainText()

    with docker.server_hold(tmp_path, INSTALL_PRESS, spec=None):
        view.run_restore()

    assert mysql.loaded == []
    assert stack.starts == 0 and stack.running == set()
    assert failures == [f"Nothing was restored: {forgetting.another_press_writes(INSTALL_PRESS)}"]
    assert maintenance.interrupted_restore(tmp_path) is None, "a marker for a restore never begun"


def test_another_server_is_not_held_by_a_restore(qapp: object, tmp_path: Path) -> None:
    """The alone-ness is per server folder: another server's Modules job is not refused."""
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    other = tmp_path / "another-server"
    seen = _during_the_load(mysql, lambda: _hold(other))
    view.show_restore_plan()
    view.run_restore()
    assert seen == ["held"]


@pytest.mark.parametrize("which", ["install", "update", "remove", "put back"])
def test_a_modules_press_says_wait_for_the_restore_and_asks_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, which: str
) -> None:
    """The Modules tab says so itself, before any question (the row menu is not greyed).

    `_restore_running` is the flag `run_restore()` sets for the length of the job. Mutation:
    drop the `_restore_running` branch from `_build_is_running()`.
    """
    view, applier, asked = _put_back_view(ps, tmp_path, monkeypatch, _last())
    view._restore_running = True

    if which == "put back":
        view._put_back_last_update()
    else:
        view._module_action(which)

    assert view.module_report.toPlainText() == (
        f"{which.capitalize()} mod-transmog was not started. Wait: the restore is running. "
        "Try again when it has finished. Nothing was changed."
    )
    assert applier.put_back_calls == [] and applier.installed == [] and applier.removed == []
    assert asked == [] and view._module_pending is None
    view._restore_running = False
    view.shutdown()


# -- L9: a dump that writes MySQL's own schema ----------------------------------------------


@pytest.mark.parametrize("schema", ["mysql", "sys"])
def test_a_dump_that_writes_a_system_schema_is_refused_and_loads_nothing(
    tmp_path: Path, schema: str
) -> None:
    """An `--all-databases` dump: the plan refuses it, and `restore()` re-plans and refuses too.

    Mutation: drop the `system` refusal from `plan_restore()`.
    """
    dump = tmp_path / "all-databases.sql"
    dump.write_bytes(good_dump("acore_characters", schema))
    stack = _Stack(SPEC.db)
    mysql = _Mysql(stack)
    plan = maintenance.plan_restore(dump, tmp_path, game=GAME, running=stack.census)

    assert not plan.allowed
    assert plan.refusals == (maintenance.system_schema_refusal(dump.name, [schema]),)
    assert f"MySQL's own system database ({schema})" in plan.refusals[0]
    with pytest.raises(MaintenanceError, match="system database"):
        maintenance.restore(plan, mysql, game=GAME, confirm=plan.token, running=stack.census)
    assert mysql.loaded == []
    assert maintenance.interrupted_restore(tmp_path) is None


def test_a_dump_of_only_the_game_databases_is_still_restored(tmp_path: Path) -> None:
    """The guard names the system schemas only; the three game databases plan as before."""
    dump = tmp_path / "game.sql"
    dump.write_bytes(good_dump("acore_auth", "acore_characters", "acore_world"))
    stack = _Stack(SPEC.db)
    plan = maintenance.plan_restore(dump, tmp_path, game=GAME, running=stack.census)
    assert plan.allowed, plan.refusals


# -- L8/L19: a restore that did not finish refuses every start --------------------------------


def _half_restored(tmp_path: Path) -> maintenance.InterruptedRestore:
    """A restore that failed part-way, through the real Maintenance tab: its marker is left."""
    stack = _Stack()
    mysql = _Mysql(stack, fails_load="ERROR 2013 (HY000): Lost connection")
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    view.show_restore_plan()
    view.run_restore()
    record = maintenance.interrupted_restore(tmp_path)
    assert record is not None and record.readable, "the failed restore left no marker"
    return record


def test_start_is_refused_on_a_restore_that_did_not_finish_and_runs_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Controller.start()` (Start, Restart, Start and play, PLAY) asks the folder first.

    Mutation: drop `unfinished_restore_refusal()` from `native.folder_start_refusal()`.
    """
    record = _half_restored(tmp_path)
    fake = _FakeRunner()
    monkeypatch.setattr(runner, "run", fake)

    with pytest.raises(StartRefused) as refused:
        Controller(SPEC, tmp_path).start()

    said = str(refused.value)
    assert said == maintenance.unfinished_restore_sentence(record)
    assert "acore_characters did not finish" in said and "restore a backup again" in said
    assert any(str(p) in said for p in record.safety_backup), "the way back is not named"
    assert not [c for c in fake.calls if c[:3] == ["docker", "compose", "up"]], fake.calls


def test_a_rebuild_and_the_engine_start_are_refused_too(qapp: object, tmp_path: Path) -> None:
    """A Rebuild is the way out of the folder's other start refusals, not out of this one.

    The engine's `start_refusal()` (a Rebuild, the install's `up` stage a Bring-in runs,
    Repair) asks `folder_start_refusal(rebuilding=...)`. Mutation: ask it after the
    `rebuilding` early return.
    """
    _half_restored(tmp_path)
    for rebuilding in (False, True):
        said = native.folder_start_refusal(tmp_path, lambda ref: None, rebuilding=rebuilding)
        assert said is not None and "did not finish" in said, rebuilding


def test_start_works_again_once_the_record_is_put_down(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The way out the sentence names: Forget that record (or a restore that finishes)."""
    _half_restored(tmp_path)
    assert maintenance.forget_interrupted_restore(tmp_path)
    fake = _FakeRunner()
    monkeypatch.setattr(runner, "run", fake)
    Controller(SPEC, tmp_path).start()
    assert any(c[:3] == ["docker", "compose", "up"] for c in fake.calls), fake.calls


def test_a_restore_that_finishes_clears_the_refusal(qapp: object, tmp_path: Path) -> None:
    """Restoring again is the other way out: the marker goes, and the start is allowed."""
    _half_restored(tmp_path)
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    view.show_restore_plan()
    view.run_restore()
    assert len(mysql.loaded) == 1, view.maintenance_report.toPlainText()
    assert native.folder_start_refusal(tmp_path, lambda ref: None) is None


def test_a_load_still_running_from_the_last_session_says_wait(tmp_path: Path) -> None:
    """T690's live load (a real live process, this one): not "restore again", a second writer."""
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True)
    token, name = maintenance.process_identity(os.getpid())
    record = maintenance.InterruptedRestore(
        marker=marker,
        backup=tmp_path / "b.sql",
        databases=("acore_characters",),
        safety_backup=(),
        started_at="",
        load_pid=os.getpid(),
        load_host=maintenance.this_host(),
        load_token=token,
        load_name=name,
    )
    marker.write_text(record.as_json(), encoding="utf-8")
    assert maintenance.interrupted_restore(tmp_path).load_running  # type: ignore[union-attr]
    said = native.folder_start_refusal(tmp_path, lambda ref: None)
    assert said is not None and "still writing" in said and "restore a backup again" not in said


def test_update_is_refused_before_its_question_and_its_backup(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Server tab's Update says no first; the backup is not taken of half-written databases.

    Mutation: drop `_refused_over_an_unfinished_restore()` from `update_to_latest()`.
    """
    record = _half_restored(tmp_path)
    stack = _Stack()
    services = _real_maintenance(tmp_path, stack, _Mysql(stack))
    asked: list[str] = []
    backups: list[int] = []

    class Route:
        upstream_news = None

        def confirmation(self) -> str:
            asked.append("confirmation")
            return "Update?"

        def press(self, cancel: object) -> object:
            asked.append("press")
            return iter(())

        def source_version(self) -> native.SourceVersion:
            return native.SourceVersion(line="", past_the_pin=False)

    services = replace(services, update_to_latest=Route(), backup=lambda: backups.append(1))
    view = _view(services)
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        controller_view_module, "show_warning", lambda _p, title, text: shown.append((title, text))
    )
    monkeypatch.setattr(
        controller_view_module,
        "ask_update_choice",
        lambda *a, **k: asked.append("choice") or controller_view_module.UpdateChoice.BACK_UP_FIRST,
    )

    assert view.update_to_latest() is False

    said = maintenance.unfinished_restore_sentence(record)
    assert shown == [(f"Update {WOTLK.name}", said)]
    assert asked == [] and backups == []


def test_the_engine_update_refuses_before_its_first_fetch(qapp: object, tmp_path: Path) -> None:
    """Past the tab (Update without a backup, headless): the engine's update asks too.

    A real WotLK install (`support_native`), then a restore of it that failed part-way.
    Mutation: drop `unfinished_restore_refusal()` from the update's `owed` line.
    """
    from tests.support_native import Recorder, engine, install
    from yulon.catalog.installer import InstallerError, InstallOptions

    rec = Recorder()
    server_dir = tmp_path / "server"
    install(rec, server_dir)
    _half_restored(server_dir)
    rec.clones.clear()
    rec.calls.clear()

    with pytest.raises(InstallerError) as refused:
        list(engine(rec).update_to_latest(InstallOptions(server_dir=server_dir)))

    assert "acore_characters did not finish" in str(refused.value), str(refused.value)
    assert str(refused.value).endswith("Nothing was started.")
    assert rec.clones == [], "the update fetched before it refused"


# -- L10: the report is the job's, not the stop's -------------------------------------------


class _StopFails(_Stack):
    def take_down(self) -> None:
        self.stops += 1
        raise docker.DockerCommandError("docker stop exited 1: daemon busy")


def test_a_restore_that_worked_is_reported_as_restored_when_the_stop_fails(
    qapp: object, tmp_path: Path
) -> None:
    """Mutation: put `alone.take_down()` back in a `finally` of its own."""
    stack = _StopFails()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    path = _select_backup(view, tmp_path)
    failures = _failures(view)

    view.show_restore_plan()
    view.run_restore()

    assert mysql.loaded == [path.read_bytes()]
    assert failures == [], failures
    text = view.maintenance_report.toPlainText()
    assert text.startswith("Restored acore_characters"), text
    assert "could not be stopped again (docker stop exited 1: daemon busy)" in text
    assert stack.stops == 1


def test_a_backup_that_worked_is_reported_and_kept_when_the_stop_fails(
    qapp: object, tmp_path: Path
) -> None:
    stack = _StopFails()
    mysql = _Mysql(stack)
    services = _real_maintenance(tmp_path, stack, mysql)
    report = maintenance.BackupReport(directory=tmp_path, dumps=(), missing_core=())
    services = replace(services, backup=lambda: report)
    view = _view(services)
    failures = _failures(view)

    view.back_up()

    assert failures == [], failures
    text = view.maintenance_report.toPlainText()
    assert text.startswith(f"Backed up to {tmp_path}"), text
    assert "could not be stopped again" in text


def test_a_failed_restore_keeps_its_own_failure_when_the_stop_fails_too(
    qapp: object, tmp_path: Path
) -> None:
    """The stop's error used to replace the load's; the load's is what the player needs."""
    stack = _StopFails()
    mysql = _Mysql(stack, fails_load="ERROR 2013 (HY000): Lost connection")
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    failures = _failures(view)
    view.show_restore_plan()
    view.run_restore()
    assert failures and "failed part-way" in failures[-1], failures
    assert "daemon busy" not in failures[-1]
    assert stack.stops == 1


# -- review round 1 (cold review of 5db1c78b) ------------------------------------------------


def test_update_during_this_tabs_own_restore_says_the_restore_is_running(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live load's own marker (T690's pid, this process) is not "the last session".

    Mutation: drop the `_restore_running` / `held_here` early return from
    `_refused_over_an_unfinished_restore()`.
    """
    stack = _Stack()
    services = _real_maintenance(tmp_path, stack, _Mysql(stack))
    asked: list[str] = []

    class Route:
        upstream_news = None

        def confirmation(self) -> str:
            asked.append("confirmation")
            return "Update?"

        def press(self, cancel: object) -> object:
            asked.append("press")
            return iter(())

        def source_version(self) -> native.SourceVersion:
            return native.SourceVersion(line="", past_the_pin=False)

    view = _view(replace(services, update_to_latest=Route()))
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True, exist_ok=True)
    token, name = maintenance.process_identity(os.getpid())
    record = maintenance.InterruptedRestore(
        marker=marker,
        backup=tmp_path / "b.sql",
        databases=("acore_characters",),
        safety_backup=(),
        started_at="",
        load_pid=os.getpid(),
        load_host=maintenance.this_host(),
        load_token=token,
        load_name=name,
    )
    marker.write_text(record.as_json(), encoding="utf-8")
    shown: list[tuple[str, str]] = []
    for seam in ("show_warning", "show_information"):
        monkeypatch.setattr(
            controller_view_module, seam, lambda _p, title, text: shown.append((title, text))
        )
    monkeypatch.setattr(
        controller_view_module,
        "ask_update_choice",
        lambda *a, **k: asked.append("choice") or controller_view_module.UpdateChoice.CANCEL,
    )
    view._restore_running = True

    assert view.update_to_latest() is False

    said = " ".join(text for _t, text in shown)
    assert "last session" not in said, shown
    assert forgetting.RESTORE_HOLDS_THE_DATABASES in said, shown
    assert asked == []
    view._restore_running = False

    # Another window's restore of the same server: the hold of this process says so.
    shown.clear()
    with docker.hold_the_server(tmp_path, forgetting.RESTORE_HOLDS_THE_SERVER):
        assert view.update_to_latest() is False
    said = " ".join(text for _t, text in shown)
    assert "last session" not in said and forgetting.RESTORE_HOLDS_THE_SERVER in said, shown
    assert asked == []


def test_a_load_whose_session_ended_says_yulon_cannot_tell_whether_it_finished(
    tmp_path: Path,
) -> None:
    """Quit anyway over a load that then finished: the marker keeps a dead pid.

    Mutation: drop the `load_pid` branch from `unfinished_restore_sentence()`.
    """
    marker = maintenance.marker_path(tmp_path)
    marker.parent.mkdir(parents=True)
    record = maintenance.InterruptedRestore(
        marker=marker,
        backup=tmp_path / "b.sql",
        databases=("acore_characters",),
        safety_backup=(tmp_path / "pre-restore.sql",),
        started_at="",
        load_pid=2**22 + 12345,  # no such process
        load_host=maintenance.this_host(),
        load_token="gone",
    )
    marker.write_text(record.as_json(), encoding="utf-8")

    said = native.folder_start_refusal(tmp_path, lambda ref: None)

    assert said is not None
    assert "cannot tell whether it finished" in said, said
    assert "restore a backup again" in said and "Forget that record" in said
    assert str(tmp_path / "pre-restore.sql") in said


@pytest.mark.parametrize(
    "statement",
    [
        b"INSERT INTO `mysql`.`user` VALUES ('root','%');\n",
        b"INSERT INTO mysql.user VALUES ('root','%');\n",
        b"CREATE USER 'x'@'%' IDENTIFIED BY 'y';\n",
        b"GRANT ALL PRIVILEGES ON *.* TO 'x'@'%';\n",
        b"ALTER USER 'root'@'localhost' IDENTIFIED BY 'z';\n",
        b"DROP USER 'root'@'%';\n",
        b"SET PASSWORD FOR 'root'@'%' = 'z';\n",
        b"  grant select on acore_world.* to 'x'@'%';\n",
    ],
    ids=[
        "insert-backquoted",
        "insert-plain",
        "create-user",
        "grant",
        "alter-user",
        "drop-user",
        "set-password",
        "indented-lowercase",
    ],
)
def test_a_dump_that_changes_mysql_accounts_is_refused(tmp_path: Path, statement: bytes) -> None:
    """Without a `USE mysql`, a statement can still write MySQL's own accounts.

    Mutation: drop the account-statement scan from `plan_restore()`.
    """
    body = good_dump("acore_characters").replace(
        b"-- Dump completed", statement + b"-- Dump completed"
    )
    dump = tmp_path / "accounts.sql"
    dump.write_bytes(body)
    stack = _Stack(SPEC.db)
    plan = maintenance.plan_restore(dump, tmp_path, game=GAME, running=stack.census)
    assert not plan.allowed, statement
    assert "MySQL's own" in plan.refusals[0], plan.refusals


def test_account_words_inside_data_are_not_refused(tmp_path: Path) -> None:
    """mysqldump escapes a newline inside a string, so a value never starts a line."""
    rows = (
        b"INSERT INTO `t` VALUES ('he said\\nGRANT ALL to me'),('CREATE USER x');\n"
        b"INSERT INTO `mysql_log` VALUES (1);\n"
        b"-- GRANT nothing, a comment\n"
    )
    body = good_dump("acore_characters").replace(b"-- Dump completed", rows + b"-- Dump completed")
    dump = tmp_path / "data.sql"
    dump.write_bytes(body)
    stack = _Stack(SPEC.db)
    plan = maintenance.plan_restore(dump, tmp_path, game=GAME, running=stack.census)
    assert plan.allowed, plan.refusals


def test_a_start_during_this_processs_own_restore_keeps_the_holds_sentence(
    qapp: object, tmp_path: Path
) -> None:
    """The marker of a load in flight here is not "did not finish": the hold speaks.

    Mutation: drop the `docker.held_here()` skip from `native.unfinished_restore_refusal()`.
    """
    stack = _Stack()
    mysql = _Mysql(stack)
    view = _view(_real_maintenance(tmp_path, stack, mysql))
    _select_backup(view, tmp_path)
    seen = _during_the_load(mysql, lambda: native.folder_start_refusal(tmp_path, lambda r: None))
    view.show_restore_plan()
    view.run_restore()
    assert seen == [None], seen
    # And the restore that held it finished, so its marker is gone.
    assert maintenance.interrupted_restore(tmp_path) is None


def test_a_backup_that_answers_nothing_still_says_the_database_was_left_running(
    qapp: object, tmp_path: Path
) -> None:
    """Mutation: return the odd result without its note in `_back_up_under_the_lease()`."""
    stack = _StopFails()
    services = replace(_real_maintenance(tmp_path, stack, _Mysql(stack)), backup=lambda: None)
    view = _view(services)
    view.back_up()
    assert "could not be stopped again" in view.maintenance_report.toPlainText()
