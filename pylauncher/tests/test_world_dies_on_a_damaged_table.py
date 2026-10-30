"""T823: a world that crash-loops on a damaged table names it and offers Repair.

After a PC crash a MyISAM table is marked crashed; the world reads it while loading, dies,
Docker restarts it, and the Server tab used to say only "restart loop — N restarts". The
world's own log names the table, so the realm line says which one, the Repair banner is
offered for a table the press covers, and a world table is pointed at a backup instead.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import _Ps
from tests.test_dashboard import _FakeSql, _loop_states, _loop_watch
from tests.test_plan_corrections import _view
from tests.test_table_repair import TORTISE_SCHEMAS, _route, _shown
from yulon import dashboard, runner, table_repair
from yulon.catalog.catalog import load_catalog
from yulon.server_build_presses import MAINTENANCE_TAB
from yulon.table_repair import Crashed, CrashedTable, damaged_at_start
from yulon.ui import controller_view as controller_view_module

CRASHED_AT_LOAD = (
    "Loading Creature Data...\n"
    "SQL: SELECT guid, id, map FROM creature\n"
    "[1194] Table 'characters' is marked as crashed and should be repaired\n"
    "/src/src/shared/Database/DatabaseMysql.cpp:190: Error: Assertion in HandleMySQLError "
    "failed: false\n"
)

CRASHED_WORLD_AT_LOAD = CRASHED_AT_LOAD.replace("'characters'", "'creature'")

STORAGE_ENGINE_ERROR = (
    'Got error 145 "Table was marked as crashed and should be repaired" from storage engine\n'
)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


class _BrokenSql(_FakeSql):
    """A read that fails: the card must say it cannot tell, never crash the tab."""

    def query(self, db: str, statement: str) -> str:
        self.statements.append(statement)
        raise RuntimeError("no route to the database")


# -- the loop's own log names the table ------------------------------------


def test_a_loop_that_died_on_a_damaged_characters_table_names_it_and_offers_the_repair(
    tmp_path: Path,
) -> None:
    """The reported case: the world dies on `characters`, and the realm line says so."""
    sql = _FakeSql("tw_char\tcharacters\n")
    tick = _loop_watch(
        tmp_path,
        {"r1": CRASHED_AT_LOAD},
        _loop_states(("restarting", "r1")),
        sql=sql,
    )

    verdict = tick()

    assert verdict.state == "restart_loop"
    assert verdict.crashed == (CrashedTable("tw_char", "characters"),)
    text = dashboard.line(verdict)
    assert "(tw_char.characters)" in text
    assert "Repair the damaged table… on the server card" in text
    assert "Maintenance" not in text


def test_a_bare_name_is_resolved_by_the_database_not_guessed(tmp_path: Path) -> None:
    """The log says `characters`; the world schema does not hold it, tw_char does.

    Mutation: skip `resolve_bare`, and the table reads as the first schema's provisional
    guess (`tw_world.characters`), which is the schema the world would be loading.
    """
    sql = _FakeSql("tw_world\tcreature\n")
    tick = _loop_watch(
        tmp_path,
        {"r1": CRASHED_WORLD_AT_LOAD},
        _loop_states(("restarting", "r1")),
        sql=sql,
    )

    verdict = tick()

    assert verdict.crashed == (CrashedTable("tw_world", "creature"),)
    text = dashboard.line(verdict)
    assert "Repair on the server card cannot fix tw_world" in text
    assert f"{MAINTENANCE_TAB} tab" in text
    assert len(sql.statements) == 1
    assert "information_schema" in sql.statements[0]


def test_a_name_two_schemas_hold_is_unnamed(tmp_path: Path) -> None:
    sql = _FakeSql("tw_char\tcharacters\ntw_world\tcharacters\n")
    tick = _loop_watch(
        tmp_path,
        {"r1": CRASHED_AT_LOAD},
        _loop_states(("restarting", "r1")),
        sql=sql,
    )

    verdict = tick()

    assert verdict.crashed == ()
    assert verdict.crash_unnamed
    assert "cannot tell which one" in dashboard.line(verdict)


def test_a_name_no_schema_holds_is_unnamed(tmp_path: Path) -> None:
    sql = _FakeSql("")
    tick = _loop_watch(
        tmp_path,
        {"r1": CRASHED_AT_LOAD},
        _loop_states(("restarting", "r1")),
        sql=sql,
    )

    verdict = tick()

    assert verdict.crashed == ()
    assert verdict.crash_unnamed
    assert "cannot tell which one" in dashboard.line(verdict)


def test_a_resolve_that_fails_is_unnamed_not_a_crash(tmp_path: Path) -> None:
    tick = _loop_watch(
        tmp_path,
        {"r1": CRASHED_AT_LOAD},
        _loop_states(("restarting", "r1")),
        sql=_BrokenSql(),
    )

    verdict = tick()

    assert verdict.state == "restart_loop"
    assert verdict.crash_unnamed
    assert verdict.crashed == ()


def test_the_storage_engine_error_with_no_table_is_unnamed(tmp_path: Path) -> None:
    sql = _FakeSql("tw_char\tcharacters\n")
    tick = _loop_watch(
        tmp_path,
        {"r1": STORAGE_ENGINE_ERROR},
        _loop_states(("restarting", "r1")),
        sql=sql,
    )

    verdict = tick()

    assert verdict.crash_unnamed
    assert verdict.crashed == ()
    assert sql.statements == [], "nothing bare was read, so nothing was asked"


# -- kept across the loop, dropped when it ends -----------------------------


def test_a_running_run_between_two_crashes_keeps_the_answer(tmp_path: Path) -> None:
    """Mutation: drop the `status == "running"` guard, and the second read loses the table."""
    logs = {"r1": CRASHED_AT_LOAD, "r2": "Loading...\n"}
    tick = _loop_watch(
        tmp_path,
        logs,
        _loop_states(("restarting", "r1"), ("restarting", "r1"), ("running", "r2")),
        sql=_FakeSql("tw_char\tcharacters\n"),
    )

    tick()
    dead, alive = tick(), tick()

    assert dead.crashed == (CrashedTable("tw_char", "characters"),)
    assert alive.state == "restart_loop"
    assert alive.crashed == (CrashedTable("tw_char", "characters"),)


def test_a_later_dead_run_without_the_error_drops_the_table(tmp_path: Path) -> None:
    """Mutation: keep the first answer (`self._crash = self._crash or found`)."""
    logs = {"r1": CRASHED_AT_LOAD, "r2": "Loading spell chains...\n"}
    tick = _loop_watch(
        tmp_path,
        logs,
        _loop_states(
            ("restarting", "r1"), ("restarting", "r1"), ("running", "r2"), ("restarting", "r2")
        ),
        sql=_FakeSql("tw_char\tcharacters\n"),
    )

    tick()
    assert tick().crashed == (CrashedTable("tw_char", "characters"),)
    tick()
    last = tick()

    assert last.state == "restart_loop"
    assert last.crashed == ()
    assert last.damaged_line == ""


def test_a_loop_that_ended_takes_the_table_with_it(tmp_path: Path) -> None:
    """Mutation: leave out the reset line on the non-loop path in `Dashboard._tick()`.

    The loop ends at `exited`; the run Docker starts next is still a loop (the strikes have
    not aged out), and its own log has not been read yet. It must not inherit the ended
    loop's table.
    """
    tick = _loop_watch(
        tmp_path,
        {"r1": CRASHED_AT_LOAD, "r2": "Loading...\n"},
        _loop_states(
            ("restarting", "r1"),
            ("restarting", "r1"),
            ("exited", "r1"),
            ("running", "r2"),
        ),
        sql=_FakeSql("tw_char\tcharacters\n"),
    )

    tick()
    assert tick().crashed == (CrashedTable("tw_char", "characters"),)
    stopped = tick()
    again = tick()

    assert stopped.state == "stopped"
    assert stopped.crashed == () and stopped.damaged_line == ""
    assert again.state == "restart_loop"
    assert again.crashed == () and again.damaged_line == ""


def test_an_azerothcore_loop_reads_nothing_new(tmp_path: Path) -> None:
    """Mutation: drop the family check, and this reads and names a table T822 never would."""
    sql = _FakeSql("acore_characters\tcharacters\n")
    tick = _loop_watch(
        tmp_path,
        {"r1": CRASHED_AT_LOAD},
        _loop_states(("restarting", "r1")),
        sql=sql,
        entry=load_catalog().get("wow-wotlk"),
    )

    verdict = tick()

    assert verdict.state == "restart_loop"
    assert verdict.crashed == ()
    assert not verdict.crash_unnamed
    assert sql.statements == []


# -- the banner and the press only take the press's own tables --------------


def test_a_world_table_on_the_verdict_never_shows_the_banner(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: drop `repairable_only` in `_refresh_repair_banner` or `repair_damaged_tables`."""
    _shown(monkeypatch)
    asked: list[str] = []
    monkeypatch.setattr(
        controller_view_module,
        "ask_yes_no",
        lambda _parent, _title, text: asked.append(text) or True,
    )
    view = _view(ps, tmp_path, None)
    view.services.table_repair = _route(tmp_path)  # type: ignore[attr-defined]
    view.last_verdict = dashboard.Verdict(  # type: ignore[attr-defined]
        "restart_loop", restarts=9, crashed=(CrashedTable("tw_world", "creature"),)
    )

    view._refresh_repair_banner()  # type: ignore[attr-defined]

    assert view.repair_banner.isHidden()  # type: ignore[attr-defined]
    assert view.repair_damaged_tables() is False  # type: ignore[attr-defined]
    assert asked == []


def test_a_characters_table_from_a_loop_shows_the_banner(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shown(monkeypatch)
    view = _view(ps, tmp_path, None)
    view.services.table_repair = _route(tmp_path)  # type: ignore[attr-defined]
    view.last_verdict = dashboard.Verdict(  # type: ignore[attr-defined]
        "restart_loop",
        restarts=9,
        crashed=(CrashedTable("tw_char", "characters"), CrashedTable("tw_world", "creature")),
    )

    view._refresh_repair_banner()  # type: ignore[attr-defined]

    assert not view.repair_banner.isHidden()  # type: ignore[attr-defined]
    assert view.repair_banner_button.text() == "Repair the damaged table…"  # type: ignore[attr-defined]


# -- the two functions on their own ----------------------------------------


def test_resolve_bare_keeps_the_order_and_leaves_named_tables_untouched() -> None:
    found = Crashed(
        (
            CrashedTable("tw_logon", "account", bare=True),
            CrashedTable("tw_char", "characters"),
            CrashedTable("tw_logon", "account"),
        )
    )
    asked: list[str] = []

    def query(statement: str) -> str:
        asked.append(statement)
        return "tw_logon\taccount\n"

    resolved = table_repair.resolve_bare(found, ("tw_world", "tw_char", "tw_logon"), query)

    # The resolved bare name lands where it stood, and its duplicate is merged away.
    assert resolved.tables == (
        CrashedTable("tw_logon", "account"),
        CrashedTable("tw_char", "characters"),
    )
    assert not resolved.unnamed
    assert "'account'" in asked[0] and "'tw_world', 'tw_char', 'tw_logon'" in asked[0]


def test_resolve_bare_asks_nothing_when_no_name_is_bare() -> None:
    found = Crashed((CrashedTable("tw_char", "characters"),))

    def query(_statement: str) -> str:
        raise AssertionError("a fully named table needs no resolution")

    assert table_repair.resolve_bare(found, TORTISE_SCHEMAS + ("tw_world",), query) == found


def test_damaged_at_start_offers_the_press_when_the_press_covers_the_table() -> None:
    said = damaged_at_start(
        Crashed((CrashedTable("tw_char", "characters"),)), repairable=TORTISE_SCHEMAS
    )

    assert said.startswith("the world server stops while it starts because")
    assert "Repair the damaged table… on the server card" in said
    assert MAINTENANCE_TAB not in said
