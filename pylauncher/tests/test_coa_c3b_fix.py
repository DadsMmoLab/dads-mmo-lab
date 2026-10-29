"""T812 C3b FIX FIRST round 1: headerless dumps refuse; a failed switch never strands.

Tests first: every test below fails before the fix and passes after it.
Run one file at a time from pylauncher (YULON_REPO REQUIRED): see the brief.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.test_backup_knows_its_game import FakeMysql
from tests.test_coa_realm import (
    FakeSql,
    _killed_after_rename,
    coa,
    coa_schemas,
    file_type,
    fresh_import,
    parked_set,
    seed_accounts,
    write_crash,
    write_file,
)
from tests.test_coa_switch_hold import dump_of, line_for, realm_line_for
from tests.test_coa_switch_hold import write as write_dump
from yulon import coa_realm, coa_switch
from yulon.controller_wow_wotlk import maintenance
from yulon.controller_wow_wotlk.maintenance import MaintenanceError

COA_GAME = maintenance.Game("wow-coa", "WoW CoA")


def _plan(path: Path, tmp_path: Path, realm: str | None):
    return maintenance.plan_restore(
        path,
        tmp_path,
        game=COA_GAME,
        spec=coa().container_spec(),
        running=lambda: ["coa-database"],
        realm_type=realm,
    )


def _restore(
    plan: maintenance.RestorePlan, mysql: FakeMysql, realm: str | None
) -> maintenance.RestoreReport:
    return maintenance.restore(
        plan,
        mysql,
        game=COA_GAME,
        confirm=plan.token,
        spec=coa().container_spec(),
        running=lambda: ["coa-database"],
        realm_type=realm,
    )


@pytest.mark.parametrize("active", ["coa", "hero", "wcr", "wc"])
def test_a_headerless_characters_dump_is_refused_while_any_type_is_active(
    tmp_path: Path, active: str
) -> None:
    """Pre-C3b dumps carry no realm record: restoring one would load another type's rows."""
    path = write_dump(tmp_path, line_for("wow-coa") + dump_of("acore_characters"))
    plan = _plan(path, tmp_path, active)
    assert not plan.allowed
    assert any(
        "does not say which realm type" in refusal for refusal in plan.refusals
    ), plan.refusals


@pytest.mark.parametrize("database", ["acore_auth", "acore_characters", "acore_playerbots"])
def test_a_headerless_dump_of_each_realm_database_is_refused(tmp_path: Path, database: str) -> None:
    path = write_dump(tmp_path, line_for("wow-coa") + dump_of(database))
    plan = _plan(path, tmp_path, "coa")
    assert not plan.allowed
    assert any("does not say which realm type" in refusal for refusal in plan.refusals)


def test_a_headerless_world_dump_is_allowed(tmp_path: Path) -> None:
    path = write_dump(tmp_path, line_for("wow-coa") + dump_of("acore_world"))
    plan = _plan(path, tmp_path, "coa")
    assert not any("realm type" in refusal for refusal in plan.refusals), plan.refusals
    assert plan.allowed


def test_a_headerless_world_and_characters_dump_is_refused(tmp_path: Path) -> None:
    path = write_dump(tmp_path, line_for("wow-coa") + dump_of("acore_world", "acore_characters"))
    plan = _plan(path, tmp_path, "coa")
    assert not plan.allowed
    assert any("does not say which realm type" in refusal for refusal in plan.refusals)


def test_a_headerless_restore_loads_nothing_and_dumps_nothing(tmp_path: Path) -> None:
    """The plan path: a headerless file planned without a type is refused once one is set."""
    path = write_dump(tmp_path, line_for("wow-coa") + dump_of("acore_characters"))
    plan = _plan(path, tmp_path, None)
    assert plan.allowed
    mysql = FakeMysql(("acore_characters",))
    with pytest.raises(MaintenanceError, match="does not say which realm type"):
        _restore(plan, mysql, "coa")
    assert mysql.loaded == []
    assert list((tmp_path / "sql_scripts").glob("**/*pre-restore*")) == []


def test_the_open_handle_recheck_refuses_a_headerless_dump(tmp_path: Path) -> None:
    """The read-before-load: the handle about to be loaded is judged, not only the plan."""
    path = write_dump(tmp_path, line_for("wow-coa") + dump_of("acore_characters"))
    plan = _plan(path, tmp_path, None)
    fresh = _plan(path, tmp_path, None)
    assert plan.allowed and fresh.allowed
    mysql = FakeMysql(("acore_characters",))
    with path.open("rb") as source:
        with pytest.raises(MaintenanceError, match="does not say which realm type"):
            maintenance._restore_from(  # type: ignore[attr-defined]
                source,
                plan,
                fresh,
                mysql,
                game=COA_GAME,
                spec=coa().container_spec(),
                core_databases=coa().core_databases(),
                running=lambda: ["coa-database"],
                wsl_distro=None,
                now=None,
                safety_label="pre-restore",
                before_load=None,
                last_of_a_copy=True,
                realm_type="coa",
            )
    assert mysql.loaded == []


def test_a_headered_dump_of_the_active_type_loads(tmp_path: Path) -> None:
    path = write_dump(
        tmp_path,
        line_for("wow-coa") + realm_line_for("coa") + dump_of("acore_characters"),
    )
    plan = _plan(path, tmp_path, "coa")
    assert plan.allowed
    mysql = FakeMysql(("acore_characters",))
    report = _restore(plan, mysql, "coa")
    assert report.databases == ("acore_characters",)
    assert len(mysql.loaded) == 1


def test_a_headered_dump_of_another_type_names_both(tmp_path: Path) -> None:
    path = write_dump(
        tmp_path, line_for("wow-coa") + realm_line_for("wc") + dump_of("acore_characters")
    )
    plan = _plan(path, tmp_path, "coa")
    assert not plan.allowed
    said = " ".join(plan.refusals)
    assert "wc" in said and "coa" in said


# -- the switch never strands the server -------------------------------------

_HOLD = "hold:Switch realm type"


class _TestHold:
    """The hold seam, recording takes like the C3b hold test's."""

    def __init__(self, events: list[str]) -> None:
        self.events = events

    @contextmanager
    def __call__(self, press: str, budget: float | None = None) -> Iterator[None]:
        self.events.append(f"hold:{press}")
        try:
            yield
        finally:
            self.events.append("release")


def _run_switch(
    tmp_path: Path,
    sql: FakeSql,
    target: str,
    hold: _TestHold,
    *,
    stopped: list[str],
    started: list[str],
    servers_running: bool | Callable[[], bool] = False,
    run_import: Callable[[coa_realm.SwitchImport], Iterator[str]] | None = None,
    set_realm_row: Callable[[str, str], None] | None = None,
    write_override: Callable[[dict[str, str]], None] | None = None,
    stop: Callable[[], bool] | None = None,
    start: Callable[[], None] | None = None,
    bot_prefix: str = "rndbot",
) -> coa_switch.RealmSwitch:
    wrote: list[dict[str, str]] = []
    return coa_switch.RealmSwitch(
        coa(),
        tmp_path,
        hold_server=hold,
        sql=sql,
        servers_running=(
            servers_running if callable(servers_running) else (lambda: servers_running)
        ),
        run_import=run_import or (lambda context: fresh_import(sql, context)),
        set_realm_row=set_realm_row or (lambda name, port: None),
        write_override=write_override or (lambda env: wrote.append(dict(env))),
        check_import=lambda: None,
        stop=stop or (lambda: stopped.append("stop") or not sql.stop_database()),
        start_db=sql.start_database,
        start=start or (lambda: started.append("start")),
        bot_prefix=bot_prefix,
    )


def _fresh(tmp_path: Path, sql: FakeSql, active: str = "coa") -> None:
    coa_schemas(sql)
    write_file(tmp_path, active)


def test_an_unknown_target_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []
    events: list[str] = []
    switch = _run_switch(tmp_path, sql, "nope", _TestHold(events), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="'nope' is not one of"):
        list(switch.switch_to("nope"))
    assert stopped == [] and started == []
    assert events == [_HOLD, "release"]
    assert sql.executed == []


def test_an_unreadable_crash_note_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    (tmp_path / coa_realm.SWITCH_MARKER_FILE).write_text("not json\n", encoding="utf-8")
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="cannot be read"):
        list(switch.switch_to("hero"))
    assert stopped == [] and started == []


def test_a_file_marker_split_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, marker="hero")
    write_file(tmp_path, "coa")
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "wc", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="Switch realm type to hero"):
        list(switch.switch_to("wc"))
    assert stopped == [] and started == []
    assert sql.executed == []


def test_a_non_empty_park_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql, parks=("coa",))
    sql.tables["acore_auth_coa"].append("orphan")
    write_file(tmp_path, "coa")
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="not empty"):
        list(switch.switch_to("hero"))
    assert stopped == [] and started == []


def test_a_target_park_of_another_type_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    coa_schemas(sql)
    parked_set(sql, "hero", marker="wc")
    write_file(tmp_path, "coa")
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="do not read as the hero"):
        list(switch.switch_to("hero"))
    assert stopped == [] and started == []


def test_a_set_with_no_characters_table_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    sql.tables.update({"acore_auth": ["account"], "acore_characters": [], "acore_playerbots": []})
    for schema in sql.tables:
        sql.schemata[schema] = ("utf8mb4", "utf8mb4_unicode_ci")
    write_file(tmp_path, "coa")
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="no characters table"):
        list(switch.switch_to("hero"))
    assert stopped == [] and started == []


def test_an_unreadable_database_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    sql.fail_query_on = "information_schema.TABLES"
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="nothing was switched"):
        list(switch.switch_to("hero"))
    assert stopped == [] and started == []


def test_an_unusable_bot_prefix_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(
        tmp_path,
        sql,
        "hero",
        _TestHold([]),
        stopped=stopped,
        started=started,
        bot_prefix="has a space!",
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="bot account prefix"):
        list(switch.switch_to("hero"))
    assert stopped == [] and started == []


def test_an_unfinished_switch_for_another_type_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    _killed_after_rename(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "wcr", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="did not finish"):
        list(switch.switch_to("wcr"))
    assert stopped == [] and started == []


def test_switching_to_the_active_type_stops_nothing(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []
    events: list[str] = []
    switch = _run_switch(tmp_path, sql, "coa", _TestHold(events), stopped=stopped, started=started)
    lines = list(switch.switch_to("coa"))
    assert lines == ["The server is already on the coa realm type; nothing was switched."]
    assert events == [_HOLD, "release"]
    assert sql.executed == []


def test_a_park_that_will_not_list_stops_nothing(tmp_path: Path) -> None:
    """A read failure before any write: the server never stops, said once."""
    sql = FakeSql()
    _fresh(tmp_path, sql)
    sql.fail_query_on = "information_schema.TABLES"
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError, match="nothing was switched"):
        list(switch.switch_to("hero"))
    assert stopped == [] and started == []


def test_a_park_that_will_not_create_restarts_the_original_type(tmp_path: Path) -> None:
    """Before any database write: the server comes back up on its own type, said once."""
    sql = FakeSql()
    _fresh(tmp_path, sql)
    sql.fail_on = "CREATE DATABASE"
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(RuntimeError, match="started again on the coa realm type") as caught:
        list(switch.switch_to("hero"))
    assert "the database refused" in str(caught.value)
    assert stopped == ["stop"] and started == ["start"]
    assert file_type(tmp_path) == "coa"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_server_still_running_after_the_stop_is_restarted_on_the_original_type(
    tmp_path: Path,
) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(
        tmp_path,
        sql,
        "hero",
        _TestHold([]),
        stopped=stopped,
        started=started,
        servers_running=True,
    )
    with pytest.raises(RuntimeError, match="Stop the server first") as caught:
        list(switch.switch_to("hero"))
    assert "started again on the coa realm type" in str(caught.value)
    assert stopped == ["stop"] and started == ["start"]
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_a_failed_rename_leaves_the_server_stopped(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    sql.fail_on = "RENAME TABLE"
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError) as caught:
        list(switch.switch_to("hero"))
    said = str(caught.value)
    assert "could not be renamed" in said
    assert "The server is stopped." in said
    assert "Switch realm type again" in said
    assert stopped == ["stop"] and started == []
    assert file_type(tmp_path) == "coa"
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_a_failed_first_import_leaves_the_server_stopped(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []

    def _blow_up(_context: coa_realm.SwitchImport) -> Iterator[str]:
        raise RuntimeError("the import blew up")
        yield "unreached"

    switch = _run_switch(
        tmp_path,
        sql,
        "hero",
        _TestHold([]),
        stopped=stopped,
        started=started,
        run_import=_blow_up,
    )
    with pytest.raises(RuntimeError, match="the import blew up") as caught:
        list(switch.switch_to("hero"))
    said = str(caught.value)
    assert "The server is stopped." in said
    assert "Switch realm type again" in said
    assert started == []
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_a_failed_realm_row_leaves_the_server_stopped(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []

    def _row(name: str, port: str) -> None:
        raise RuntimeError("the realm row would not write")

    switch = _run_switch(
        tmp_path,
        sql,
        "hero",
        _TestHold([]),
        stopped=stopped,
        started=started,
        set_realm_row=_row,
    )
    with pytest.raises(RuntimeError, match="the realm row would not write") as caught:
        list(switch.switch_to("hero"))
    assert "The server is stopped." in str(caught.value)
    assert started == []


def test_a_colliding_account_leaves_the_server_stopped(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []

    def _colliding(context: coa_realm.SwitchImport) -> Iterator[str]:
        yield from fresh_import(sql, context)
        seed_accounts(sql, "acore_auth", {"Player2": None})

    switch = _run_switch(
        tmp_path,
        sql,
        "hero",
        _TestHold([]),
        stopped=stopped,
        started=started,
        run_import=_colliding,
    )
    with pytest.raises(coa_realm.RealmSwitchError, match="already hold an account named Player2"):
        list(switch.switch_to("hero"))
    assert started == []
    assert (tmp_path / coa_realm.SWITCH_MARKER_FILE).is_file()


def test_a_marker_that_will_not_write_leaves_the_server_stopped(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    sql.fail_on = "INSERT IGNORE INTO"
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(RuntimeError, match="INSERT IGNORE INTO") as caught:
        list(switch.switch_to("hero"))
    assert "The server is stopped." in str(caught.value)
    assert "Switch realm type again" in str(caught.value)
    assert started == []


def test_a_failed_verification_leaves_the_server_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)

    def _refuse(sql: coa_realm.SqlRunner, entry: object, current: str, target: str) -> None:
        raise coa_realm.RealmSwitchError(
            "the plain databases do not read back; nothing further was written."
        )

    monkeypatch.setattr(coa_realm, "_verify", _refuse)
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(coa_realm.RealmSwitchError) as caught:
        list(switch.switch_to("hero"))
    assert "do not read back" in str(caught.value)
    assert "The server is stopped." in str(caught.value)
    assert started == []


def test_an_override_that_will_not_write_leaves_the_server_stopped(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []

    def _refuse(env: dict[str, str]) -> None:
        raise OSError("the disk would not take it")

    switch = _run_switch(
        tmp_path,
        sql,
        "hero",
        _TestHold([]),
        stopped=stopped,
        started=started,
        write_override=_refuse,
    )
    with pytest.raises(RuntimeError, match="would not take it") as caught:
        list(switch.switch_to("hero"))
    assert "The server is stopped." in str(caught.value)
    assert started == []


def test_a_file_that_will_not_publish_leaves_the_server_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)

    def _refuse(server_dir: Path, type_id: str) -> None:
        raise OSError("the file would not publish")

    monkeypatch.setattr(coa_realm, "write_active", _refuse)
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(RuntimeError, match="would not publish") as caught:
        list(switch.switch_to("hero"))
    assert "would not publish" in str(caught.value)
    assert "The server is stopped." in str(caught.value)
    assert started == []


def test_a_crash_note_that_will_not_write_leaves_the_server_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)

    def _refuse(server_dir: Path, target_id: str, *, imported: bool, source: str) -> None:
        raise OSError("the note would not write")

    monkeypatch.setattr(coa_realm, "_write_crash", _refuse)
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(RuntimeError, match="would not write") as caught:
        list(switch.switch_to("hero"))
    assert "would not write" in str(caught.value)
    assert "The server is stopped." in str(caught.value)
    assert started == []


def test_a_park_shell_that_will_not_drop_leaves_the_server_stopped(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    sql.fail_on = "DROP DATABASE"
    stopped: list[str] = []
    started: list[str] = []
    switch = _run_switch(tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started)
    with pytest.raises(RuntimeError, match="DROP DATABASE") as caught:
        list(switch.switch_to("hero"))
    assert "The server is stopped." in str(caught.value)
    assert "Switch realm type again" in str(caught.value)
    assert started == []


def test_a_restart_that_fails_says_stopped_not_started(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    sql.fail_on = "CREATE DATABASE"
    stopped: list[str] = []
    started: list[str] = []

    def _refuse() -> None:
        started.append("start")
        raise RuntimeError("the start blew up too")

    switch = _run_switch(
        tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started, start=_refuse
    )
    with pytest.raises(RuntimeError, match="blew up too") as caught:
        list(switch.switch_to("hero"))
    said = str(caught.value)
    assert "blew up too" in said
    assert "The server is stopped." in said
    assert "started again on the coa" not in said


def test_a_final_start_that_fails_keeps_the_finished_switch(tmp_path: Path) -> None:
    sql = FakeSql()
    _fresh(tmp_path, sql)
    stopped: list[str] = []
    started: list[str] = []

    def _refuse() -> None:
        started.append("start")
        raise RuntimeError("the start blew up after the switch")

    switch = _run_switch(
        tmp_path, sql, "hero", _TestHold([]), stopped=stopped, started=started, start=_refuse
    )
    with pytest.raises(RuntimeError, match="blew up after the switch"):
        list(switch.switch_to("hero"))
    assert file_type(tmp_path) == "hero"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_an_interrupted_switch_is_undone_under_the_hold(tmp_path: Path) -> None:
    sql = FakeSql()
    _killed_after_rename(sql)
    write_file(tmp_path, "coa")
    write_crash(tmp_path, "hero", imported=False)
    stopped: list[str] = []
    started: list[str] = []
    events: list[str] = []
    switch = _run_switch(tmp_path, sql, "coa", _TestHold(events), stopped=stopped, started=started)
    lines = list(switch.switch_to("coa"))
    assert any("put back" in line for line in lines)
    assert stopped == ["stop"] and started == ["start"]
    assert events == [_HOLD, "release"]
    assert file_type(tmp_path) == "coa"
    assert not (tmp_path / coa_realm.SWITCH_MARKER_FILE).exists()


def test_the_confirm_names_both_outcomes() -> None:
    said = coa_switch.confirm_text("Hero", "CoA Free Pick")
    assert "starts again on its current type" in said
    assert "stays stopped" in said
    assert "finishes it or puts it back" in said
