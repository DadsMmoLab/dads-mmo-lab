"""T822: the Server card's Repair for a table a crash left damaged.

The reported error is the first test. Every refusal has its own test — a hold another
Yu'lon took, a missing table, an InnoDB table, a failed backup, a backup that names a
second crashed table, a copy that failed with the question answered No and Yes, a
CHECK that is still bad after the one EXTENDED repair, a press while busy and Cancel
at the confirm — because a refusal that is only reachable through another refusal's
test is a refusal nobody sees break.

The engine tests use fakes that record every call, so the order (hold, engines,
backup, copy, REPAIR, CHECK) and the one table written are asserted rather than
assumed.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support_fake_docker import end_fake_containers, lay_fake_docker
from tests.support_player_text import command_faults, text_faults
from tests.test_controller_view import _Ps
from tests.test_plan_corrections import _view
from yulon import dashboard, dbreads, docker, platform, runner, table_repair
from yulon.catalog.catalog import load_catalog
from yulon.table_repair import Crashed, CrashedTable, TableRepair
from yulon.ui import controller_view as controller_view_module

WOTLK = load_catalog().get("wow-wotlk")
TORTISE_SCHEMAS = ("tw_char", "tw_logon")

verbatim = (
    "SQL failed (query → tw_char): ERROR 1194 (HY000) at line 1: "
    "Table 'characters' is marked as crashed and should be repaired"
)


# -- the parser ------------------------------------------------------------


def test_the_reported_tortoise_error_names_tw_char_characters() -> None:
    """Mutation: ignore `default_db` for a bare name."""
    crashed = table_repair.crashed_tables(verbatim, TORTISE_SCHEMAS)
    assert crashed.tables == (CrashedTable("tw_char", "characters"),)
    assert not crashed.unnamed
    assert crashed.tables[0].qualified == "tw_char.characters"


def test_the_last_automatic_repair_failed_wording_names_the_table_too() -> None:
    text = (
        "ERROR 1195 (HY000): Table 'tw_char.characters' is marked as crashed and last "
        "(automatic?) repair failed"
    )
    assert table_repair.crashed_tables(text, TORTISE_SCHEMAS).tables == (
        CrashedTable("tw_char", "characters"),
    )


def test_the_mysqldump_dot_slash_form_is_read_as_a_schema_and_a_table() -> None:
    text = (
        "Got error: 145: \"Table './tw_char/characters' is marked as crashed and should be "
        'repaired" when using LOCK TABLES'
    )
    assert table_repair.crashed_tables(text, TORTISE_SCHEMAS).tables == (
        CrashedTable("tw_char", "characters"),
    )


def test_a_myi_path_and_the_incorrect_key_file_wording_are_read() -> None:
    text = "Incorrect key file for table '/var/lib/mysql/tw_char/characters.MYI'; try to repair it"
    assert table_repair.crashed_tables(text, TORTISE_SCHEMAS).tables == (
        CrashedTable("tw_char", "characters"),
    )


def test_a_dotted_name_is_read_as_it_is_written() -> None:
    text = "Table 'tw_char.characters' is marked as crashed and should be repaired"
    assert table_repair.crashed_tables(text, TORTISE_SCHEMAS).tables == (
        CrashedTable("tw_char", "characters"),
    )


def test_a_storage_engine_error_that_names_no_table_is_unnamed() -> None:
    text = "Got error 145 from storage engine"
    crashed = table_repair.crashed_tables(text, TORTISE_SCHEMAS)
    assert crashed.tables == ()
    assert crashed.unnamed


def test_two_named_tables_are_kept_once_each_in_the_order_they_appear() -> None:
    text = (
        "Table 'tw_char.characters' is marked as crashed and should be repaired; "
        "Table 'tw_logon.account' is marked as crashed and should be repaired; "
        "Table 'tw_char.characters' is marked as crashed and should be repaired"
    )
    assert table_repair.crashed_tables(text, TORTISE_SCHEMAS).tables == (
        CrashedTable("tw_char", "characters"),
        CrashedTable("tw_logon", "account"),
    )


def test_a_qualified_name_outside_these_schemas_is_dropped_and_unnamed() -> None:
    text = "Table 'somebody_else.characters' is marked as crashed and should be repaired"
    crashed = table_repair.crashed_tables(text, TORTISE_SCHEMAS)
    assert crashed.tables == ()
    assert crashed.unnamed


def test_a_name_with_a_quote_or_backtick_is_dropped_and_no_sql_is_sent() -> None:
    """The identifier fullmatch is the whole injection defence.

    Mutation: drop it. The name below then reaches `REPAIR TABLE`, and the fake
    database that records nothing is the proof it must not.
    """
    text = (
        "Table 'tw_char.charac\\`ters' is marked as crashed and should be repaired; "
        "Table 'tw_char.characters`; DROP TABLE tw_char.account' is marked as crashed and "
        "should be repaired"
    )
    crashed = table_repair.crashed_tables(text, TORTISE_SCHEMAS)
    assert crashed.tables == ()
    assert crashed.unnamed
    mysql = _Mysql()
    report = _repair(crashed.tables, mysql=mysql, schemas=TORTISE_SCHEMAS)
    assert report.refused
    assert mysql.calls == []


# -- the card --------------------------------------------------------------


class _BrokenSql:
    def query(self, db: str, statement: str) -> str:
        raise RuntimeError(verbatim)


def test_the_card_line_is_plain_words_and_the_raw_sql_goes_to_the_log(tmp_path: Path) -> None:
    answer = dbreads.resolve_marker(WOTLK, _install(tmp_path))
    assert answer.marker is not None
    counts = dbreads.population(_BrokenSql(), WOTLK, answer.marker)
    assert counts.crashed == (CrashedTable(WOTLK.databases.characters, "characters"),)
    assert "ERROR 1194" not in counts.problem
    assert "SQL failed" not in counts.problem
    line = dashboard.line(
        dashboard.Verdict(
            "up",
            problem=counts.problem,
            crashed=counts.crashed,
            crash_unnamed=counts.crash_unnamed,
            database_unreachable=True,
        )
    )
    assert line.startswith("up — a database table is damaged (")
    assert "stopping suddenly" in line
    assert "characters cannot be read" in line


def _install(tmp_path: Path) -> Path:
    conf = tmp_path / WOTLK.observability.bots.prefix_conf_file  # type: ignore[union-attr]
    conf.parent.mkdir(parents=True, exist_ok=True)
    conf.write_text("AiPlayerbot.RandomBotAccountPrefix = rndbot\n", encoding="utf-8")
    return tmp_path


# -- fakes -----------------------------------------------------------------


class _Mysql:
    """Records every call; answers engines and CHECK from canned rows (T822)."""

    def __init__(
        self,
        engines: str = "tw_char\tcharacters\tMyISAM\n",
        *,
        datadir: str = "/var/lib/mysql/",
        checked: str = "OK",
    ) -> None:
        self.engines = engines
        self.datadir = datadir
        self.checked = checked
        self.calls: list[object] = []

    def query(self, sql: str) -> str:
        if "@@datadir" in sql:
            self.calls.append("datadir")
            return self.datadir + "\n"
        if sql.startswith("CHECK TABLE"):
            self.calls.append(("check", sql))
            table = sql.split("`")[1] + "." + sql.split("`")[3]
            return f"{table}\tcheck\t{self.checked}\n"
        self.calls.append(("engines", sql))
        return self.engines

    def execute(self, sql: str) -> None:
        self.calls.append(("execute", sql))

    def writes(self) -> list[str]:
        return [call[1] for call in self.calls if isinstance(call, tuple) and call[0] == "execute"]


@dataclass
class _Backup:
    calls: list[Mapping[str, object]] | None = None
    failure: Exception | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def __call__(
        self, *, only: Sequence[str], label: str, ignore_tables: Mapping[str, Sequence[str]]
    ) -> object:
        assert self.calls is not None
        self.calls.append({"only": list(only), "label": label, "ignore_tables": ignore_tables})
        if self.failure is not None:
            raise self.failure
        return object()


class _Copy:
    def __init__(self, folder: Path, failure: Exception | None = None) -> None:
        self.folder = folder
        self.failure = failure
        self.calls: list[tuple[Sequence[CrashedTable], str, Mapping[str, str]]] = []

    def __call__(
        self,
        tables: Sequence[CrashedTable],
        datadir: str,
        engines: Mapping[str, str],
    ) -> Path:
        self.calls.append((tables, datadir, engines))
        if self.failure is not None:
            raise self.failure
        return self.folder


def _tables() -> tuple[CrashedTable, ...]:
    return (CrashedTable("tw_char", "characters"),)


def _repair(
    tables: Sequence[CrashedTable],
    *,
    mysql: _Mysql,
    schemas: Sequence[str] = TORTISE_SCHEMAS,
    backup: _Backup | None = None,
    copy: _Copy | None = None,
    confirm: object = None,
    hold: object = None,
) -> table_repair.RepairReport:
    backup = backup if backup is not None else _Backup()
    copy = copy if copy is not None else _Copy(Path("/tmp/copy"))
    asked: list[str] = []

    def ask(sentence: str) -> bool:
        asked.append(sentence)
        return bool(confirm(sentence)) if callable(confirm) else True

    held = hold if hold is not None else contextlib.nullcontext
    return table_repair.repair(
        tables,
        schemas=schemas,
        mysql=mysql,
        hold=held,  # type: ignore[arg-type]
        backup=backup,
        copy_files=copy,
        confirm_without_copy=ask,
    )


# -- the press order and its one table -------------------------------------


def test_backup_copy_repair_and_check_happen_in_that_order_on_only_that_table(
    tmp_path: Path,
) -> None:
    """Mutations: move the backup after REPAIR; repair every table of the schema."""
    events: list[str] = []
    mysql = _Mysql(
        engines=(
            "tw_char\tcharacters\tMyISAM\n"
            "tw_char\tbugreport\tMyISAM\n"
            "tw_logon\taccount\tMyISAM\n"
        )
    )
    original_query = mysql.query

    def query(sql: str) -> str:
        if sql.startswith("CHECK TABLE"):
            events.append("check")
        elif sql.startswith("SELECT TABLE_SCHEMA"):
            events.append("engines")
        else:
            events.append("datadir")
        return original_query(sql)

    mysql.query = query  # type: ignore[method-assign]
    original_execute = mysql.execute

    def execute(sql: str) -> None:
        events.append("repair")
        original_execute(sql)

    mysql.execute = execute  # type: ignore[method-assign]

    class _BackupNow(_Backup):
        def __call__(self, **kwargs: object) -> object:
            events.append("backup")
            return super().__call__(**kwargs)  # type: ignore[arg-type]

    class _CopyNow(_Copy):
        def __call__(self, tables, datadir, engines):  # type: ignore[no-untyped-def]
            events.append("copy")
            return super().__call__(tables, datadir, engines)

    @contextlib.contextmanager
    def hold() -> Iterator[None]:
        events.append("hold")
        yield

    report = _repair(
        _tables(),
        mysql=mysql,
        backup=_BackupNow(),
        copy=_CopyNow(tmp_path),
        hold=hold,
    )
    assert report.ok
    assert events == ["hold", "engines", "backup", "datadir", "copy", "repair", "check"]
    assert mysql.writes() == ["REPAIR TABLE `tw_char`.`characters`;"]
    assert "tw_logon" not in mysql.writes()[0] and "bugreport" not in mysql.writes()[0]


# -- every refusal ---------------------------------------------------------


def test_a_repair_while_another_yulon_holds_the_server_dumps_and_repairs_nothing(
    fake_docker: Path, tmp_path: Path
) -> None:
    """Mutation: take the hold off. SEAMS names this test for `table_repair`."""
    from tests.test_server_reservation import _another_yulon_holds

    server = tmp_path / "server"
    server.mkdir()
    theirs = _another_yulon_holds(
        fake_docker, docker.SERVER_CLAIM_PREFIX + str(docker.folder_id(server))
    )
    mysql = _Mysql()
    backup = _Backup()
    copy = _Copy(tmp_path)
    route = TableRepair(
        schemas=TORTISE_SCHEMAS,
        mysql=mysql,
        hold=lambda: docker.hold_the_server(
            server,
            table_repair.REPAIR_HOLD_REASON,
            press=table_repair.REPAIR_PRESS,
            spec=docker_ctl_spec(),
        ),
        backup=backup,
        copy_files=copy,
    )
    try:
        report = route.repair(_tables(), confirm_without_copy=lambda _s: True)
    finally:
        theirs.kill()
    assert report.refused
    assert "Another Yu'lon" in report.sentence
    assert mysql.calls == []
    assert backup.calls == []
    assert copy.calls == []


def docker_ctl_spec() -> docker.ContainerSpec:
    from yulon.controller_wow_tortoise import docker_ctl

    return docker_ctl.SPEC


def test_a_missing_table_refuses_before_the_backup(tmp_path: Path) -> None:
    mysql = _Mysql(engines="")
    backup = _Backup()
    report = _repair(_tables(), mysql=mysql, backup=backup)
    assert report.refused
    assert "cannot find the table tw_char.characters" in report.sentence
    assert backup.calls == []


def test_an_innodb_table_is_refused_with_its_engine_named(tmp_path: Path) -> None:
    """Mutation: drop the engine check."""
    mysql = _Mysql(engines="tw_char\tcharacters\tInnoDB\n")
    report = _repair(_tables(), mysql=mysql)
    assert report.refused
    assert "InnoDB" in report.sentence
    assert "cannot be repaired here" in report.sentence
    assert mysql.writes() == []


def test_a_backup_that_fails_refuses_and_repairs_nothing(tmp_path: Path) -> None:
    mysql = _Mysql()
    backup = _Backup(failure=RuntimeError("the backup of tw_char did not finish."))
    report = _repair(_tables(), mysql=mysql, backup=backup)
    assert report.refused
    assert report.sentence == table_repair.BACKUP_FAILED
    assert mysql.writes() == []


def test_a_backup_that_names_another_crashed_table_keeps_it_for_the_next_press(
    tmp_path: Path,
) -> None:
    other = (
        "the backup of tw_char did not finish. "
        "Table 'tw_logon.account' is marked as crashed and should be repaired"
    )
    backup = _Backup(failure=RuntimeError(other))
    mysql = _Mysql()
    report = _repair(_tables(), mysql=mysql, backup=backup)
    assert report.refused
    assert "Another table is damaged too: tw_logon.account" in report.sentence
    assert report.found_more == (CrashedTable("tw_logon", "account"),)
    assert mysql.writes() == []


def test_a_copy_that_fails_and_the_question_answered_no_leaves_the_backup_only(
    tmp_path: Path,
) -> None:
    asked: list[str] = []

    def no(sentence: str) -> bool:
        asked.append(sentence)
        return False

    mysql = _Mysql()
    backup = _Backup()
    report = _repair(
        _tables(),
        mysql=mysql,
        backup=backup,
        copy=_Copy(tmp_path, failure=RuntimeError("no such file")),
        confirm=no,
    )
    assert report.refused
    assert report.sentence == table_repair.NOTHING_REPAIRED_BACKUP_KEPT
    assert asked == [table_repair.COPY_FAILED_QUESTION]
    assert backup.calls
    assert mysql.writes() == []


def test_a_copy_that_fails_and_the_question_answered_yes_repairs_and_says_no_copy(
    tmp_path: Path,
) -> None:
    mysql = _Mysql()
    report = _repair(
        _tables(),
        mysql=mysql,
        copy=_Copy(tmp_path, failure=RuntimeError("no such file")),
        confirm=lambda _sentence: True,
    )
    assert report.ok
    assert report.copy_dir is None
    assert "could not be made" in report.sentence
    assert mysql.writes() == ["REPAIR TABLE `tw_char`.`characters`;"]


def test_a_check_still_bad_after_one_extended_repair_says_so(tmp_path: Path) -> None:
    """Mutation: skip CHECK and call it repaired."""
    mysql = _Mysql(checked="error")
    report = _repair(_tables(), mysql=mysql)
    assert not report.ok
    assert report.still_damaged == _tables()
    assert mysql.writes() == [
        "REPAIR TABLE `tw_char`.`characters`;",
        "REPAIR TABLE `tw_char`.`characters` EXTENDED;",
    ]
    checks = [call for call in mysql.calls if isinstance(call, tuple) and call[0] == "check"]
    assert len(checks) == 2
    assert "did not fix tw_char.characters" in report.sentence
    assert "backups folder" in report.sentence


def test_a_bare_account_is_resolved_to_the_schema_that_holds_it_at_press_time(
    tmp_path: Path,
) -> None:
    """TBC/Vanilla: the bot clause makes `realmd.account` the table a crash can name."""
    text = "Table 'account' is marked as crashed and should be repaired"
    tables = table_repair.crashed_tables(text, ("characters", "realmd")).tables
    assert tables == (CrashedTable("characters", "account"),)
    mysql = _Mysql(engines="realmd\taccount\tMyISAM\n")
    backup = _Backup()
    report = _repair(tables, mysql=mysql, schemas=("characters", "realmd"), backup=backup)
    assert report.ok
    assert backup.calls == [
        {"only": ["realmd"], "label": "before-repair", "ignore_tables": {"realmd": ["account"]}}
    ]
    assert mysql.writes() == ["REPAIR TABLE `realmd`.`account`;"]


def test_a_bare_name_two_schemas_hold_is_refused(tmp_path: Path) -> None:
    text = "Table 'account' is marked as crashed and should be repaired"
    schemas = ("characters", "realmd", "logs")
    tables = table_repair.crashed_tables(text, schemas).tables
    mysql = _Mysql(engines="realmd\taccount\tMyISAM\nlogs\taccount\tMyISAM\n")
    report = _repair(tables, mysql=mysql, schemas=schemas)
    assert report.refused
    assert "More than one database has a table called account" in report.sentence
    assert mysql.writes() == []


# -- the wiring ------------------------------------------------------------


@pytest.mark.parametrize(
    ("game", "factory", "wired"),
    [
        ("wow-tortoise", "_for_tortoise", True),
        ("wow-tbc", "_for_tbc", True),
        ("wow-vanilla", "_for_vanilla", True),
        ("wow-wotlk", "_for_wotlk", False),
        ("wow-centurion", "_for_centurion", False),
    ],
)
def test_only_the_cmangos_factories_wire_the_repair(
    tmp_path: Path, game: str, factory: str, wired: bool
) -> None:
    """Mutation: wire WotLK, or drop one CMaNGOS factory."""
    folder = tmp_path / "server"
    folder.mkdir()
    services = getattr(controller_view_module, factory)(
        load_catalog().get(game), folder, None, None
    )
    assert (services.table_repair is not None) is wired


# -- the sentences a player reads ------------------------------------------


def test_no_player_sentence_names_a_command_or_looks_like_a_note() -> None:
    tables = _tables()
    texts = [
        table_repair.damaged_sentence(Crashed(tables)),
        table_repair.damaged_sentence(Crashed(unnamed=True)),
        table_repair.repair_banner_text(tables),
        table_repair.repair_button_label(tables),
        table_repair.confirmation(tables),
        table_repair.COPY_FAILED_QUESTION,
        table_repair.NOTHING_REPAIRED_BACKUP_KEPT,
        table_repair.BACKUP_FAILED,
        table_repair.REPAIR_FAILED,
        table_repair.REPAIR_HOLD_REASON,
        controller_view_module.REPAIR_BROKE,
    ]
    for text in texts:
        assert command_faults(text) == [], text
        assert text_faults(text) == [], text


# -- the tab ---------------------------------------------------------------


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture
def fake_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cli, state = lay_fake_docker(tmp_path)
    monkeypatch.setattr(platform, "docker_program", lambda: str(cli))
    monkeypatch.setattr(docker, "RESERVATIONS_ON", True)
    (state / "images-listed").write_text("yulon.local/wotlk-server:native\n", encoding="utf-8")
    yield state
    end_fake_containers(state)


def _route(
    tmp_path: Path, *, mysql: _Mysql | None = None, copy: _Copy | None = None
) -> TableRepair:
    return TableRepair(
        schemas=("tw_char", "tw_logon"),
        mysql=mysql if mysql is not None else _Mysql(),
        hold=contextlib.nullcontext,
        backup=_Backup(),
        copy_files=copy if copy is not None else _Copy(tmp_path),
    )


def _shown(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    shown: list[str] = []
    monkeypatch.setattr(
        controller_view_module,
        "show_information",
        lambda _parent, _title, text: shown.append(text),
    )
    monkeypatch.setattr(controller_view_module, "show_warning", lambda _p, _t, _x: None)
    return shown


def _verdict(tables: Sequence[CrashedTable] = _tables()) -> dashboard.Verdict:
    return dashboard.Verdict("up", crashed=tuple(tables), database_unreachable=True)


def test_the_banner_is_shown_for_a_named_table_and_hidden_for_an_unnamed_one(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = _shown(monkeypatch)
    view = _view(ps, tmp_path, None)
    view.services.table_repair = _route(tmp_path)  # type: ignore[attr-defined]
    view.last_verdict = _verdict()  # type: ignore[attr-defined]
    view._refresh_repair_banner()  # type: ignore[attr-defined]
    assert not view.repair_banner.isHidden()  # type: ignore[attr-defined]
    assert "can be repaired" in view.repair_banner_label.text()  # type: ignore[attr-defined]
    assert view.repair_banner_button.text() == "Repair the damaged table…"  # type: ignore[attr-defined]

    view.last_verdict = dashboard.Verdict("up", crash_unnamed=True)  # type: ignore[attr-defined]
    view._refresh_repair_banner()  # type: ignore[attr-defined]
    assert view.repair_banner.isHidden()  # type: ignore[attr-defined]
    assert shown == []


def test_the_banner_goes_with_the_verdict_and_is_absent_without_a_seam(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shown(monkeypatch)
    view = _view(ps, tmp_path, None)
    view.last_verdict = _verdict()  # type: ignore[attr-defined]
    view._refresh_repair_banner()  # type: ignore[attr-defined]
    assert view.repair_banner.isHidden()  # type: ignore[attr-defined]
    view.services.table_repair = _route(tmp_path)  # type: ignore[attr-defined]
    view._refresh_repair_banner()  # type: ignore[attr-defined]
    assert not view.repair_banner.isHidden()  # type: ignore[attr-defined]
    view._clear_the_verdict()  # type: ignore[attr-defined]
    assert view.repair_banner.isHidden()  # type: ignore[attr-defined]


def test_the_banner_button_waits_while_another_job_runs(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shown(monkeypatch)
    view = _view(ps, tmp_path, None)
    view.services.table_repair = _route(tmp_path)  # type: ignore[attr-defined]
    view.last_verdict = _verdict()  # type: ignore[attr-defined]
    view._refresh_repair_banner()  # type: ignore[attr-defined]
    view._set_busy(True, "Start")  # type: ignore[attr-defined]
    assert not view.repair_banner_button.isEnabled()  # type: ignore[attr-defined]
    view._set_busy(False)  # type: ignore[attr-defined]
    assert view.repair_banner_button.isEnabled()  # type: ignore[attr-defined]


def test_cancel_at_the_confirmation_repairs_nothing(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _shown(monkeypatch)
    mysql = _Mysql()
    seam = _route(tmp_path, mysql=mysql)
    view = _view(ps, tmp_path, None)
    view.services.table_repair = seam  # type: ignore[attr-defined]
    view.last_verdict = _verdict()  # type: ignore[attr-defined]
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *_a, **_k: False)
    assert view.repair_damaged_tables() is False  # type: ignore[attr-defined]
    assert mysql.calls == []


def test_a_press_while_busy_says_so_and_starts_nothing(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = _shown(monkeypatch)
    mysql = _Mysql()
    view = _view(ps, tmp_path, None)
    view.services.table_repair = _route(tmp_path, mysql=mysql)  # type: ignore[attr-defined]
    view.last_verdict = _verdict()  # type: ignore[attr-defined]
    view._set_busy(True, "Start")  # type: ignore[attr-defined]
    assert view.repair_damaged_tables() is False  # type: ignore[attr-defined]
    assert mysql.calls == []
    assert "busy with another action" in shown[0]


def test_the_press_says_the_engine_sentence_and_repairs_the_one_table(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = _shown(monkeypatch)
    mysql = _Mysql()
    view = _view(ps, tmp_path, None)
    view.services.table_repair = _route(tmp_path, mysql=mysql)  # type: ignore[attr-defined]
    view.last_verdict = _verdict()  # type: ignore[attr-defined]
    monkeypatch.setattr(controller_view_module, "ask_yes_no", lambda *_a, **_k: True)
    assert view.repair_damaged_tables() is True  # type: ignore[attr-defined]
    assert mysql.writes() == ["REPAIR TABLE `tw_char`.`characters`;"]
    assert shown and shown[0].startswith("Repaired tw_char.characters.")


def test_the_worker_asks_the_copy_question_on_the_gui_thread(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shown = _shown(monkeypatch)
    mysql = _Mysql()
    answers = iter([True, False])
    asked: list[str] = []

    def answer(_parent: object, _title: str, text: str) -> bool:
        asked.append(text)
        return next(answers)

    monkeypatch.setattr(controller_view_module, "ask_yes_no", answer)
    view = _view(ps, tmp_path, None)
    view.services.table_repair = _route(  # type: ignore[attr-defined]
        tmp_path, mysql=mysql, copy=_Copy(tmp_path, failure=RuntimeError("no such file"))
    )
    view.last_verdict = _verdict()  # type: ignore[attr-defined]
    assert view.repair_damaged_tables() is True  # type: ignore[attr-defined]
    assert len(asked) == 2
    assert table_repair.COPY_FAILED_QUESTION in asked
    assert mysql.writes() == []
    assert shown == [table_repair.NOTHING_REPAIRED_BACKUP_KEPT]
