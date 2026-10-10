"""T661: a failed or half-run `cmangos custom` file is not only a log line.

T659's `reapply_changed` phase applies the db repo's `utilities/cmangos_custom.sql`. When the
database refused it, or an update stopped while it ran, the Server tab's banner names it like
any other unfinished world update; a fresh install that carried on past a refusal says so in
the install's own closing lines.
"""

from __future__ import annotations

import re
from pathlib import Path

from tests.support_native import Recorder
from tests.test_families_cmangos import install
from tests.test_plan_corrections import BASE, an_engine, folder, imported_with, plan
from tests.test_retry_stuck_world_updates import _Db, _lay, _ledger
from yulon.catalog import native
from yulon.catalog.catalog import SqlPhase, load_catalog
from yulon.catalog.families import sqlplan

CUSTOM = SqlPhase(
    name="cmangos custom",
    into="mangos",
    files=("src/tbc-db/utilities/cmangos_custom.sql",),
    on_error="warn",
    on_update="reapply_changed",
)
PLAN = plan(BASE, CUSTOM)
FILE = "src/tbc-db/utilities/cmangos_custom.sql"
SAFE = "UPDATE t SET a=1 WHERE id=1;\n"


def _server(tmp_path: Path, state: str, body: str = SAFE) -> tuple[_Db, Path]:
    db = _Db(tmp_path)
    server_dir = folder(tmp_path).server_dir
    assert server_dir is not None
    imported_with(db, PLAN, server_dir)
    path = _lay(server_dir, FILE, body)
    sqlplan.record_world_files(
        (sqlplan.FileRow("cmangos custom", FILE, sqlplan.file_digest(path), state),),
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
    )
    return db, server_dir


def test_a_refused_custom_file_is_in_the_banner_with_what_to_do(tmp_path: Path) -> None:
    db, _ = _server(tmp_path, "failed")
    check = an_engine(PLAN, db).correction_check(folder(tmp_path))
    assert check.state == "stale", check
    (one,) = check.stuck
    assert (one.phase, one.file, one.state, one.repeatable) == (
        "cmangos custom",
        FILE,
        "failed",
        True,
    )
    banner = native.corrections_banner_text(check)
    assert "cmangos_custom.sql" in banner and native.CORRECTIONS_BUTTON_LABEL in banner


def test_a_half_run_custom_file_is_in_the_banner(tmp_path: Path) -> None:
    db, _ = _server(tmp_path, "started")
    check = an_engine(PLAN, db).correction_check(folder(tmp_path))
    (one,) = check.stuck
    assert one.state == "started"


def test_the_dialog_says_it_is_safe_to_repeat_and_what_the_press_does(tmp_path: Path) -> None:
    db, _ = _server(tmp_path, "failed")
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    text = engine.correction_confirmation(check, folder(tmp_path))
    assert FILE in text and "the database refused it" in text
    assert "data corrections" in text and "safe to repeat" in text
    assert "cannot show that running it again is safe" not in text


def test_a_custom_file_of_an_unsafe_shape_is_not_offered_as_safe(tmp_path: Path) -> None:
    db, _ = _server(tmp_path, "failed", "UPDATE t SET a=a+1 WHERE id=1;\n")
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    (one,) = check.stuck
    assert one.repeatable is False
    assert "cannot show that running it again is safe" in engine.correction_confirmation(
        check, folder(tmp_path)
    )


def test_an_applied_custom_file_is_not_stuck(tmp_path: Path) -> None:
    db, _ = _server(tmp_path, "applied")
    assert an_engine(PLAN, db).correction_check(folder(tmp_path)).state == "current"
    assert _ledger(db) == {FILE: "applied"}


def _with_table(db: _Db) -> None:
    import io

    db.exec_stdin(
        "tbc-db",
        ["mariadb", "-u", "x", "mangos"],
        io.BytesIO(b"CREATE TABLE t (id INT, a INT); INSERT INTO t VALUES (1, 0);"),
        env={},
    )


def test_the_press_runs_a_refused_custom_file_again_and_records_it(tmp_path: Path) -> None:
    db, _ = _server(tmp_path, "failed")
    _with_table(db)
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    lines = list(engine.apply_corrections(check, folder(tmp_path)))
    assert _ledger(db) == {FILE: "applied"}, lines
    assert list(db.rows("mangos", "SELECT a FROM t")) == [(1,)]
    assert engine.correction_check(folder(tmp_path)).state == "current"


def test_the_press_will_not_run_a_custom_file_edited_to_an_unsafe_shape(tmp_path: Path) -> None:
    db, _ = _server(tmp_path, "failed", "UPDATE t SET a=a+1 WHERE id=1;\n")
    _with_table(db)
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    lines = list(engine.apply_corrections(check, folder(tmp_path)))
    assert any("was not run" in line and "not safe to repeat" in line for line in lines), lines
    assert _ledger(db) == {FILE: "failed"}


# -- a fresh install ----------------------------------------------------------------------


class _Answering(Recorder):
    """The recorder, answering the file ledger the way a database that took the writes would."""

    def sql_query(self, container, client, password, schema, statement, *, wsl_distro=None):  # type: ignore[no-untyped-def]
        written = [s for s in self.sql_scripts if "yulon_install_file" in s and "'failed'" in s]
        if "information_schema.tables" in statement and "yulon_install_file" in statement:
            return "1\n" if written else "0\n"
        if statement.startswith("SELECT phase, file, sha256, state FROM"):
            rows = []
            for script in written:
                for m in re.finditer(
                    r"VALUES \('([^']*)', '([^']*)', '([0-9a-f]{64})', 'failed'", script
                ):
                    rows.append("\t".join((*m.groups(), "failed")))
            return "\n".join(rows) + "\n"
        return super().sql_query(
            container, client, password, schema, statement, wsl_distro=wsl_distro
        )


def _fresh(tmp_path: Path, refuse: bool) -> tuple[list[str], str]:
    entry = load_catalog().get("wow-tbc")
    rec = _Answering()
    db = next(s.dest for s in entry.emulator.sources if s.repo.endswith("-db"))
    rel = f"{db}/utilities/cmangos_custom.sql"
    if refuse:
        rec.failing_sql = f"-- {rel}"
    return install(rec, tmp_path / "srv", tmp_path / "client", entry=entry), rel


def test_a_fresh_install_that_carried_on_past_the_custom_file_says_so_at_the_end(
    tmp_path: Path,
) -> None:
    lines, rel = _fresh(tmp_path, refuse=True)
    said = [
        line
        for line in lines
        if line.startswith("warning:") and rel in line and "did not finish" in line
    ]
    closing = [i for i, line in enumerate(lines) if "is installed and running" in line]
    assert len(said) == 1, lines
    assert native.CORRECTIONS_BUTTON_LABEL in said[0]
    assert lines.index(said[0]) < closing[-1], "before the closing line"
    assert closing[-1] == len(lines) - 1


def test_a_clean_fresh_install_says_nothing_about_it(tmp_path: Path) -> None:
    lines, rel = _fresh(tmp_path, refuse=False)
    assert not [line for line in lines if rel in line and "did not finish" in line], lines
