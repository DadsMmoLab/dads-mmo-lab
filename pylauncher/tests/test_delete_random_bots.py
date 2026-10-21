"""T805: "Delete every random bot…" on the AzerothCore trees' Bots tab (WotLK, Unbound).

The same one-press, backup-first, confirmed action Tortoise's Bots tab has (T144), doing
the deleting itself: the bots' characters and their `rndbot` accounts, over the same
`DockerSql` the tab's reads go through, with 8.7a's world-stopped rule.
"""

from __future__ import annotations

import contextlib
from dataclasses import replace
from pathlib import Path

import pytest

from tests.conftest import pump_until
from yulon.catalog.catalog import load_catalog
from yulon.controller_wow_wotlk import rndbots
from yulon.manifest import Db
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import UpdateChoice
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
TORTOISE = load_catalog().get("wow-tortoise")

CONF = "env/dist/etc/modules/playerbots.conf"


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the view's background jobs synchronously, as every view test here does."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _conf(server_dir: Path, text: str = "AiPlayerbot.RandomBotAccountPrefix = rndbot\n") -> Path:
    path = server_dir / CONF
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


class _Sql:
    """`DockerSql`, recording every question and statement, answering counts in order.

    Each `COUNT(*)` read is answered from `counts` (characters' first, accounts'
    second, one pair per `_counts()` ask); the table-existence check answers
    `exists`.
    """

    def __init__(self, counts: list[int] | None = None, *, exists: str = "1") -> None:
        self.statements: list[tuple[str, str]] = []
        self.queries: list[tuple[str, str]] = []
        self._counts = list(counts or [])
        self.exists = exists

    def query(self, db: Db, statement: str) -> str:
        self.queries.append((db, statement))
        if "information_schema.TABLES" in statement:
            return self.exists
        if not self._counts:
            return "0"
        return str(self._counts.pop(0))

    def run_statement(self, db: Db, statement: str) -> None:
        self.statements.append((db, statement))

    def sqls(self) -> list[str]:
        return [statement for _db, statement in self.statements]


class _Alone:
    """The database-alone pair, recording what came up and what went down."""

    def __init__(self, started: bool = False) -> None:
        self.started = started
        self.because: list[str] = []
        self.taken_down = 0

    def bring_up(self, because: str) -> bool:
        self.because.append(because)
        return self.started

    def take_down(self) -> None:
        self.taken_down += 1


class _Holds:
    """The server hold, recording its press."""

    def __init__(self) -> None:
        self.presses: list[str] = []

    def __call__(self, press: str) -> contextlib.AbstractContextManager[object]:
        self.presses.append(press)
        return contextlib.nullcontext()


class _Backup:
    """The view's backup seam, raising when asked to."""

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def __call__(self) -> object:
        self.calls += 1
        if self.fail:
            raise RuntimeError("mysqldump said no")
        return None


def _seam(
    tmp_path: Path,
    sql: _Sql,
    *,
    running: object = False,
    alone: _Alone | None = None,
    holds: _Holds | None = None,
    entry: object = WOTLK,
) -> rndbots.RndbotDelete:
    _conf(tmp_path)
    return rndbots.RndbotDelete(
        entry=entry,  # type: ignore[arg-type]
        server_dir=tmp_path,
        sql=sql,
        world_running=lambda: running,  # type: ignore[arg-type,return-value]
        alone=alone if alone is not None else _Alone(),
        hold_server=holds if holds is not None else _Holds(),
    )


def _delete(seam: rndbots.RndbotDelete, **kw: object) -> list[str]:
    return list(seam.delete(**kw))  # type: ignore[arg-type]


def _lines(seam: rndbots.RndbotDelete, **kw: object) -> list[str]:
    lines: list[str] = []
    for line in seam.delete(**kw):  # type: ignore[arg-type]
        lines.append(line)
    return lines


# -- the statements -----------------------------------------------------------------


def test_the_characters_and_accounts_are_deleted_by_the_resolved_prefix(tmp_path: Path) -> None:
    """The delete names THIS install's schemas and the prefix its own conf sets."""
    sql = _Sql()
    seam = _seam(tmp_path, sql)
    _delete(seam)
    chars = [s for s in sql.sqls() if s.startswith("DELETE FROM `acore_characters`.")]
    accounts = [s for s in sql.sqls() if s.startswith("DELETE FROM `acore_auth`.`account` W")]
    assert len(chars) == 1 and "`characters`" in chars[0], chars
    assert len(accounts) == 1
    assert "UPPER(username) LIKE 'RNDBOT%'" in chars[0], "the prefix the conf sets, not a guess"
    assert "UPPER(username) LIKE 'RNDBOT%'" in accounts[0]
    # The GM rows go with the accounts, through the catalog's own level table.
    access = [s for s in sql.sqls() if "`account_access`" in s]
    assert len(access) == 1 and "IN (SELECT id FROM `acore_auth`.`account`" in access[0]


def test_the_registry_rows_of_the_deleted_accounts_go_too(tmp_path: Path) -> None:
    sql = _Sql()
    seam = _seam(tmp_path, sql)
    _delete(seam)
    registry = [s for s in sql.sqls() if "`playerbots_account_type`" in s and "DELETE FROM" in s]
    assert len(registry) == 1, registry
    assert "`account_id` IN (SELECT id FROM `acore_auth`.`account`" in registry[0]


def test_the_modules_random_bots_rows_go_first(tmp_path: Path) -> None:
    """`playerbots_random_bots` is deleted by subqueries over rows still standing."""
    sql = _Sql()
    seam = _seam(tmp_path, sql)
    _delete(seam)
    sqls = sql.sqls()
    random_rows = [i for i, s in enumerate(sqls) if "`playerbots_random_bots`" in s]
    chars = sqls.index(next(s for s in sqls if s.startswith("DELETE FROM `acore_characters`")))
    assert random_rows and max(random_rows) < chars, "before the characters delete"
    by_bot = [s for s in sqls if "WHERE `bot` IN" in s]
    by_owner = [s for s in sqls if "WHERE `owner` IN" in s]
    assert len(by_bot) == 1 and len(by_owner) == 1
    assert "`characters`" in by_bot[0], "the bot side is the character guid"


def test_only_the_random_bots_go_not_the_addclass_or_city_bots(tmp_path: Path) -> None:
    """The registry arm is narrowed to `account_type` 1: AddClass and city bots stay."""
    sql = _Sql()
    seam = _seam(tmp_path, sql)
    _delete(seam)
    clause = next(
        statement
        for _db, statement in sql.queries
        if "SELECT COUNT(*)" in statement and "characters" in statement
    )
    assert "account_type" in clause
    types = clause.split("account_type")[-1].split(")")[0].split("IN (")[-1]
    assert types.strip() == "1", f"only the RNDbot type: {clause}"


def test_a_missing_playerbots_table_is_skipped_not_a_failure(tmp_path: Path) -> None:
    """An install whose playerbots schema was never created loses only what it has."""
    sql = _Sql(exists="0")
    seam = _seam(tmp_path, sql)
    _delete(seam)
    assert not any("`playerbots_random_bots`" in s for s in sql.sqls())
    assert any("`playerbots_account_type`" in s for s in sql.sqls()), "the registry is separate"


# -- the order of the job ------------------------------------------------------------


def test_the_backup_runs_first_and_before_anything_is_written(tmp_path: Path) -> None:
    backup = _Backup()
    sql = _Sql()
    seam = _seam(tmp_path, sql)
    order: list[str] = []
    holds = _Holds()
    real_backup = backup

    def noted() -> object:
        order.append("backup")
        return real_backup()

    real_hold = holds.__call__

    def noted_hold(press: str) -> contextlib.AbstractContextManager[object]:
        order.append("hold")
        return real_hold(press)

    lines = _delete(
        replace(seam, hold_server=noted_hold),
        backup=noted,
    )
    assert order == ["backup", "hold"], "the backup, then the deletes' hold"
    assert backup.calls == 1
    assert any("Backing up" in line for line in lines)


def test_a_failing_backup_stops_before_anything_is_deleted(tmp_path: Path) -> None:
    sql = _Sql()
    seam = _seam(tmp_path, sql)
    with pytest.raises(RuntimeError, match="mysqldump said no"):
        _delete(seam, backup=_Backup(fail=True))
    assert sql.sqls() == [], "nothing was written over a failed backup"


def test_a_stopped_world_is_the_only_state_that_deletes(tmp_path: Path) -> None:
    sql = _Sql()
    seam = _seam(tmp_path, sql, running=True)
    with pytest.raises(rndbots.RndbotDeleteError, match="Press Stop on the Server tab"):
        _delete(seam)
    assert sql.statements == [], "nothing was deleted into a running world"


def test_an_unknown_world_refuses_too(tmp_path: Path) -> None:
    """Docker not answering is not "stopped" (8.7a's fails-closed reading)."""
    sql = _Sql()
    seam = _seam(tmp_path, sql, running=None)
    with pytest.raises(rndbots.RndbotDeleteError, match="Could not tell whether the server"):
        _delete(seam)
    assert sql.statements == []


def test_a_world_started_while_the_backup_ran_is_refused_before_the_writes(
    tmp_path: Path,
) -> None:
    """The guard is asked twice: before the backup and after it (the applier's rule)."""
    sql = _Sql()
    answers = [False, True]  # stopped before the backup, running after it
    seam = replace(_seam(tmp_path, sql), world_running=lambda: answers.pop(0))
    backup = _Backup()
    with pytest.raises(rndbots.RndbotDeleteError, match="Press Stop"):
        _delete(seam, backup=backup)
    assert backup.calls == 1 and sql.statements == []


def test_the_database_started_for_the_deletes_is_put_back_down(tmp_path: Path) -> None:
    alone = _Alone(started=True)
    sql = _Sql()
    seam = _seam(tmp_path, sql, alone=alone)
    _delete(seam)
    assert alone.because == ["no bot was deleted"], "the sentence names what was not done"
    assert alone.taken_down == 1


def test_a_database_that_was_already_up_is_not_stopped(tmp_path: Path) -> None:
    alone = _Alone(started=False)
    sql = _Sql()
    seam = _seam(tmp_path, sql, alone=alone)
    _delete(seam)
    assert alone.taken_down == 0


def test_the_deletes_run_under_the_servers_hold(tmp_path: Path) -> None:
    holds = _Holds()
    sql = _Sql()
    seam = _seam(tmp_path, sql, holds=holds)
    _delete(seam)
    assert holds.presses == [rndbots.PRESS_DELETE_BOTS]


def test_a_cancel_before_the_first_delete_deletes_nothing(tmp_path: Path) -> None:
    import threading

    sql = _Sql()
    seam = _seam(tmp_path, sql)
    cancel = threading.Event()
    cancel.set()
    lines = _delete(seam, cancel=cancel)  # an event ALREADY set
    assert sql.statements == []
    assert any("Stopped before anything was deleted" in line for line in lines)


def test_a_cancel_part_way_says_what_ran(tmp_path: Path) -> None:
    """A stop asked mid-job ends it after the statement in flight, and says so."""
    import threading

    sql = _Sql()
    # The second run_statement cancels the job before the account rows go.
    original = sql.run_statement

    def stop_on_the_characters(db: Db, statement: str) -> None:
        original(db, statement)
        if statement.startswith("DELETE FROM `acore_characters`"):
            cancel.set()

    cancel = threading.Event()
    sql.run_statement = stop_on_the_characters  # type: ignore[method-assign]
    seam = _seam(tmp_path, sql)
    lines = _delete(seam, cancel=cancel)
    assert cancel.is_set()
    assert any("Stopped." in line for line in lines)


def test_a_broken_prefix_is_a_refusal_not_a_write(tmp_path: Path) -> None:
    sql = _Sql()
    _conf(tmp_path, 'AiPlayerbot.RandomBotAccountPrefix = "rnd\'bot"\n')
    seam = rndbots.RndbotDelete(
        entry=WOTLK,
        server_dir=tmp_path,
        sql=sql,
        world_running=lambda: False,
        alone=_Alone(),
        hold_server=_Holds(),
    )
    with pytest.raises(rndbots.RndbotDeleteError, match="cannot be used in a query"):
        _delete(seam)
    assert sql.statements == []


def test_the_summary_names_what_went(tmp_path: Path) -> None:
    """Counts before and after: the summary reads off the server, not off the plan."""
    sql = _Sql(counts=[7, 5, 0, 0])
    seam = _seam(tmp_path, sql)
    lines = _delete(seam)
    assert any("7 random-bot characters" in line and "5 bot accounts" in line for line in lines)
    assert any(
        line.startswith("Deleted 7 random-bot characters and 5 bot accounts") for line in lines
    )


def test_the_counts_that_are_not_numbers_are_a_refusal(tmp_path: Path) -> None:
    sql = _Sql()
    original = sql.query

    def broken(db: Db, statement: str) -> str:
        if "information_schema.TABLES" not in statement:
            return "junk"
        return original(db, statement)

    sql.query = broken  # type: ignore[method-assign]
    seam = _seam(tmp_path, sql)
    with pytest.raises(rndbots.RndbotDeleteError, match="not a number"):
        _delete(seam)
    assert sql.statements == []


# -- the wiring ----------------------------------------------------------------------


def test_the_factory_returns_none_without_a_measured_marker() -> None:
    from types import SimpleNamespace

    assert (
        rndbots.for_entry(
            SimpleNamespace(observability=None),  # type: ignore[arg-type]
            Path("/nowhere"),
            sql=_Sql(),
            world_running=lambda: False,
            alone=_Alone(),
            hold_server=_Holds(),
        )
        is None
    )


def test_the_delete_is_wired_on_the_azerothcore_trees_not_the_cmangos_ones(
    qapp: object, tmp_path: Path
) -> None:
    """Both WotLK entries draw the press; the CMaNGOS trees still draw Tortoise's or none."""
    from tests.test_controller_view import _Ps, _services

    for entry, wants_delete, wants_rebuild in (
        (WOTLK, True, False),
        (TORTOISE, False, True),
    ):
        services = _services(_Ps(), tmp_path, [])
        if entry is TORTOISE:
            services = replace(services, bot_pool_rebuild=object())
        else:
            services = replace(
                services,
                bot_rndbot_delete=rndbots.for_entry(
                    entry,
                    tmp_path,
                    sql=_Sql(),
                    world_running=lambda: False,
                    alone=_Alone(),
                    hold_server=_Holds(),
                ),
            )
        view = controller_view_module.ControllerView(
            entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
        )
        label = view.bot_rebuild_button.text() if view.bot_rebuild_button else None
        if wants_delete:
            assert label == "Delete every random bot…", entry.id
        if wants_rebuild:
            assert label == "Rebuild random bots…", entry.id


def test_the_real_wotlk_factory_wires_the_delete_and_the_button(
    qapp: object, tmp_path: Path
) -> None:
    """The press rides the REAL `ControllerServices.for_entry()` wiring, not a test's replace.

    The factory's wiring is what a player gets; a seam only ever handed to the view by a
    test would draw a button on no real server.
    """
    from tests.test_characters_tab import _view as people_view

    view = people_view(tmp_path)
    assert view.services.bot_rndbot_delete is not None
    assert view.bot_rebuild_button is not None
    assert view.bot_rebuild_button.text() == "Delete every random bot…"


# -- the tab -------------------------------------------------------------------------


class _DeleteSeam:
    """The Bots tab's delete seam, recording what the view asked of it."""

    def __init__(self, fail: str | None = None) -> None:
        self.calls = 0
        self.backups: list[object] = []
        self.fail = fail

    def delete(self, *, backup: object = None, cancel: object = None) -> object:
        self.calls += 1
        self.backups.append(backup)
        if self.fail:
            raise rndbots.RndbotDeleteError(self.fail)
        yield "Deleted 2 random-bot characters and 2 bot accounts."


def _view(
    tmp_path: Path,
    seam: object | None,
    entry: object = WOTLK,
    **extra: object,
) -> controller_view_module.ControllerView:
    from tests.test_controller_view import _Ps, _services

    services = replace(
        _services(_Ps(), tmp_path, []),
        bot_rndbot_delete=seam,
        **extra,
    )
    return controller_view_module.ControllerView(
        entry, services, status_poll_ms=0, job_runner=run_inline  # type: ignore[arg-type]
    )


def _wait(view: object) -> None:
    """Until the panel's job has ended AND its finish handler has run (which unlocks `_busy`)."""
    log = view.bot_rebuild_log
    assert log is not None
    pump_until(lambda: not log.running and not view._busy, "the delete job finished")


def _answer(monkeypatch: pytest.MonkeyPatch, which: object) -> list[tuple[str, str]]:
    asked: list[tuple[str, str]] = []

    def question(_parent: object, title: str, text: str, **_kw: object) -> UpdateChoice:
        asked.append((title, text))
        return which  # type: ignore[return-value]

    monkeypatch.setattr(controller_view_module, "ask_backup_choice", question)
    return asked


def test_the_press_runs_the_job_with_or_without_the_backup(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for answer, backs_up in (
        (UpdateChoice.BACK_UP_FIRST, True),
        (UpdateChoice.WITHOUT_BACKUP, False),
    ):
        _answer(monkeypatch, answer)
        seam = _DeleteSeam()
        view = _view(tmp_path, seam)
        assert view.delete_random_bots() is True
        _wait(view)
        assert seam.calls == 1
        assert (seam.backups[0] is not None) is backs_up
        assert "Deleted 2 random-bot characters" in view.bot_rebuild_log.text()  # type: ignore[union-attr]


def test_cancel_escape_and_the_close_button_do_nothing(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seam = _DeleteSeam()
    view = _view(tmp_path, seam)
    for answer in (UpdateChoice.CANCEL, UpdateChoice.CANCEL, UpdateChoice.CANCEL):
        _answer(monkeypatch, answer)
        assert view.delete_random_bots() is False
    assert seam.calls == 0


def test_the_dialog_names_the_rule_and_the_default_is_cancel(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    asked = _answer(monkeypatch, UpdateChoice.CANCEL)
    view = _view(tmp_path, _DeleteSeam())
    assert view.delete_random_bots() is False
    assert len(asked) == 1
    title, text = asked[0]
    assert title == "Delete every random bot?"
    for said in ("rndbot account", "AddClass and city bots", "must be stopped"):
        assert said in text, said


def test_a_refusal_is_put_where_the_player_is_looking(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _answer(monkeypatch, UpdateChoice.WITHOUT_BACKUP)
    seam = _DeleteSeam(fail="The server is running: press Stop on the Server tab, then again.")
    view = _view(tmp_path, seam)
    failures: list[str] = []
    view.action_failed.connect(failures.append)
    view.delete_random_bots()
    _wait(view)
    assert "press Stop" in view.bot_rebuild_report.text()  # type: ignore[union-attr]
    assert failures and "press Stop" in failures[0]


def test_a_press_without_the_seam_is_false_and_draws_nothing(qapp: object, tmp_path: Path) -> None:
    view = _view(tmp_path, None)
    assert view.bot_rebuild_button is None
    assert view.delete_random_bots() is False


def test_the_press_is_refused_while_another_job_writes_this_server(
    qapp: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    boxes = _answer(monkeypatch, UpdateChoice.BACK_UP_FIRST)
    seam = _DeleteSeam()
    view = _view(tmp_path, seam)
    view._restore_running = True
    assert view.delete_random_bots() is False
    assert seam.calls == 0 and boxes == [], "refused before the question"
