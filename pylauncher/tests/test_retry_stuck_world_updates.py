"""T545: a world update an update to latest left refused or unfinished is run again from the app.

Since T531 such a file stays `failed`/`started` in `mangos.yulon_install_file`, is never run
again on Yu'lon's own word, and holds back every newer world update of its phase. This is the
player's way forward: the Server tab's "Apply database corrections…" (T129) reads the stuck
files, its dialog says for each whether running it again is shown to be safe (T534's
whole-table guard) and asks, and the press runs each again and then the ones held back.

Driven on T129's sqlite-backed fake, which runs the SQL it is handed: nothing between the
engine and the databases is a double.
"""

from __future__ import annotations

import io
import re
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

import pytest

from tests.test_plan_corrections import BASE, _Mariadb, an_engine, folder, imported_with, plan
from yulon.catalog import native
from yulon.catalog.catalog import SqlPhase
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError

CONTENT = SqlPhase(
    name="content updates",
    into="mangos",
    files=("src/tbc-db/Updates/*.sql",),
    on_error="warn",
    on_update="apply_new",
)
PLAN = plan(BASE, CONTENT)
U1, U2, U3 = (f"src/tbc-db/Updates/000{n}.sql" for n in (1, 2, 3))


class _Db(_Mariadb):
    """T129's fake, reading MariaDB's table options as sqlite does not: by leaving them out."""

    @staticmethod
    def _default(text: str, schema: str | None) -> str:
        text = re.sub(r"\)\s*ENGINE=InnoDB DEFAULT CHARSET=utf8mb4", ")", text)
        return _Mariadb._default(text, schema)

    def exec_stdin(
        self,
        container: str,
        argv: Sequence[str],
        source: BinaryIO,
        *,
        env: Mapping[str, str],
        wsl_distro: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return super().exec_stdin(container, argv, source, env=env, wsl_distro=wsl_distro)


def _lay(server_dir: Path, rel: str, body: str) -> Path:
    path = server_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def _stuck_server(tmp_path: Path, state: str = "failed") -> tuple[_Db, Path]:
    """An imported server whose update ran 0001, was refused (or stopped) on 0002, and held 0003."""
    db = _Db(tmp_path)
    server_dir = folder(tmp_path).server_dir
    assert server_dir is not None
    _lay(server_dir, U1, "CREATE TABLE IF NOT EXISTS t1 (id INT);\n")
    imported_with(db, PLAN, server_dir)
    _lay(server_dir, U2, "CREATE TABLE IF NOT EXISTS t2 (id INT);\n")
    _lay(server_dir, U3, "CREATE TABLE IF NOT EXISTS t3 (id INT);\n")
    rows = (
        sqlplan.FileRow("content updates", U1, sqlplan.file_digest(server_dir / U1), "seeded"),
        sqlplan.FileRow("content updates", U2, sqlplan.file_digest(server_dir / U2), state),
    )
    sqlplan.record_world_files(
        rows,
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
    )
    return db, server_dir


def _ledger(db: _Db) -> dict[str, str]:
    return dict(db.rows("mangos", "SELECT file, state FROM yulon_install_file"))  # type: ignore[arg-type]


def test_a_refused_world_update_is_offered_with_what_waits_behind_it(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    check = an_engine(PLAN, db).correction_check(folder(tmp_path))
    assert check.state == "stale", check
    assert check.offered == ()
    (one,) = check.stuck
    assert (one.phase, one.file, one.state, one.repeatable, one.behind) == (
        "content updates",
        U2,
        "failed",
        False,
        1,
    )
    assert len(one.sha256) == 64 and one.at_unix > 0
    banner = native.corrections_banner_text(check)
    assert "0002.sql" in banner and native.CORRECTIONS_BUTTON_LABEL in banner


def test_a_server_with_nothing_stuck_and_no_ledger_is_current(tmp_path: Path) -> None:
    db = _Db(tmp_path)
    server_dir = folder(tmp_path).server_dir
    assert server_dir is not None
    _lay(server_dir, U1, "CREATE TABLE IF NOT EXISTS t1 (id INT);\n")
    imported_with(db, PLAN, server_dir)
    assert an_engine(PLAN, db).correction_check(folder(tmp_path)).state == "current"


def test_the_dialog_asks_before_a_file_that_cannot_be_shown_safe_to_run_again(
    tmp_path: Path,
) -> None:
    db, _ = _stuck_server(tmp_path)
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    text = engine.correction_confirmation(check, folder(tmp_path))
    assert U2 in text and "the database refused it" in text
    assert "cannot show that running it again is safe" in text
    assert "1 newer world update(s) waiting behind it" in text
    assert "STOPS it first" in text


def test_a_whole_table_file_is_said_to_be_safe_to_run_again(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path, state="started")
    _lay(
        server_dir,
        U2,
        "DROP TABLE IF EXISTS t2;\nCREATE TABLE t2 (id INT);\nINSERT INTO t2 VALUES (1);\n",
    )
    # the ledger's sha is of the old bytes; T531 would call it changed, the press still runs it
    check = an_engine(PLAN, db).correction_check(folder(tmp_path))
    assert check.stuck[0].repeatable and check.stuck[0].state == "started"
    text = native.stuck_world_updates_text(check.stuck)
    assert "an update stopped while it ran" in text and "running it again is safe" in text


def test_the_press_runs_the_stuck_file_then_the_ones_held_behind_it(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    lines = list(engine.apply_corrections(check, folder(tmp_path)))
    assert {"t2", "t3"} <= db.tables("mangos"), lines
    assert _ledger(db) == {U1: "seeded", U2: "applied", U3: "applied"}
    assert engine.correction_check(folder(tmp_path)).state == "current"


def test_a_file_the_database_refuses_again_stays_offered_and_nothing_after_it_runs(
    tmp_path: Path,
) -> None:
    db, _ = _stuck_server(tmp_path)
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    db.refuse = "t2"
    lines = list(engine.apply_corrections(check, folder(tmp_path)))
    assert any("refused" in line and U2 in line for line in lines), lines
    assert "t3" not in db.tables("mangos")
    assert _ledger(db)[U2] == "failed" and U3 not in _ledger(db)
    db.refuse = None
    assert engine.correction_check(folder(tmp_path)).state == "stale"


def test_the_press_refuses_when_the_stuck_files_changed_since_the_dialog(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    forged = native.CorrectionCheck(
        "stale",
        marker=check.marker,
        stuck=(native.StuckWorldUpdate("content updates", U3, "failed", False),),
    )
    with pytest.raises(InstallerError, match="changed since the confirmation"):
        list(engine.apply_corrections(forged, folder(tmp_path)))
    assert "t2" not in db.tables("mangos") and "t3" not in db.tables("mangos")


def test_the_update_s_sentence_now_points_at_the_press() -> None:
    from tests.support_native import Recorder
    from tests.test_families_cmangos import ENTRY
    from tests.test_families_cmangos import engine as tbc_engine

    said = tbc_engine(Recorder(), entry=ENTRY)._say_so(U2)
    assert native.CORRECTIONS_BUTTON_LABEL in said and "DELETE" not in said


def test_a_refusal_on_the_first_of_two_stuck_files_stops_before_the_second(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    sqlplan.record_world_files(
        (sqlplan.FileRow("content updates", U3, sqlplan.file_digest(server_dir / U3), "started"),),
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
    )
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    assert [s.file for s in check.stuck] == [U2, U3]
    db.refuse = "t2"
    lines = list(engine.apply_corrections(check, folder(tmp_path)))
    assert any("refused" in line and U2 in line for line in lines), lines
    assert "t3" not in db.tables("mangos")
    assert _ledger(db)[U2] == "failed" and _ledger(db)[U3] == "started"


def test_a_stuck_file_edited_after_the_dialog_is_not_run(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    engine = an_engine(PLAN, db)
    check = engine.correction_check(folder(tmp_path))
    _lay(server_dir, U2, "CREATE TABLE IF NOT EXISTS t2 (id INT, extra INT);\n")
    with pytest.raises(InstallerError, match="changed since the confirmation"):
        list(engine.apply_corrections(check, folder(tmp_path)))
    assert "t2" not in db.tables("mangos")


def test_only_one_of_two_presses_can_take_a_stuck_row(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    # the stuck row is old, as it is when a player reads the dialog and then presses
    db.exec_stdin(
        "tbc-db",
        ["mariadb", "-u", "root", "mangos"],
        io.BytesIO(b"UPDATE yulon_install_file SET at_unix = 1000 WHERE state = 'failed';"),
        env={},
    )
    seen = _times(db)[("content updates", U2)]
    assert seen == 1000
    row = sqlplan.FileRow("content updates", U2, "a" * 64, "started")

    def take() -> None:
        sqlplan.record_world_files(
            (row,),
            marker_db="mangos",
            container="tbc-db",
            client="mariadb",
            password="x",
            exec_stdin=db.exec_stdin,
            reclaim_at=seen,
        )

    take()
    with pytest.raises(InstallerError):
        take()
    assert _ledger(db)[U2] == "started"


def test_two_presses_in_one_second_still_cannot_both_take_the_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The claim moves the row's time on, so a press holding the old time never matches again."""
    db, _ = _stuck_server(tmp_path)
    monkeypatch.setattr(sqlplan.time, "time", lambda: 5000.0)
    db.exec_stdin(
        "tbc-db",
        ["mariadb", "-u", "root", "mangos"],
        io.BytesIO(b"UPDATE yulon_install_file SET at_unix = 5000 WHERE state = 'failed';"),
        env={},
    )
    row = sqlplan.FileRow("content updates", U2, "a" * 64, "started")

    def take() -> None:
        sqlplan.record_world_files(
            (row,),
            marker_db="mangos",
            container="tbc-db",
            client="mariadb",
            password="x",
            exec_stdin=db.exec_stdin,
            reclaim_at=5000,
        )

    take()
    assert _times(db)[("content updates", U2)] > 5000
    with pytest.raises(InstallerError):
        take()


def test_a_claim_that_cannot_be_written_leaves_the_stuck_row_where_it_was(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    seen = _times(db)[("content updates", U2)]
    db.exec_stdin(
        "tbc-db",
        ["mariadb", "-u", "root", "mangos"],
        io.BytesIO(
            b"CREATE TRIGGER refuse_claim BEFORE INSERT ON yulon_install_file "
            b"BEGIN SELECT RAISE(ABORT, 'refused'); END;"
        ),
        env={},
    )
    with pytest.raises(InstallerError):
        sqlplan.record_world_files(
            (sqlplan.FileRow("content updates", U2, "a" * 64, "started"),),
            marker_db="mangos",
            container="tbc-db",
            client="mariadb",
            password="x",
            exec_stdin=db.exec_stdin,
            reclaim_at=seen,
        )
    assert _ledger(db)[U2] == "failed"


def test_a_retrys_own_record_is_never_older_than_the_time_it_took_the_row_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, _ = _stuck_server(tmp_path)
    monkeypatch.setattr(sqlplan.time, "time", lambda: 5000.0)
    sqlplan.record_world_files(
        (sqlplan.FileRow("content updates", U2, "a" * 64, "applied"),),
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
        not_before=5001,
    )
    assert _times(db)[("content updates", U2)] == 5001


def _times(db: _Db) -> dict[tuple[str, str], int]:
    answer = "\n".join(
        f"{p}\t{f}\t{t}"
        for p, f, t in db.rows("mangos", "SELECT phase, file, at_unix FROM yulon_install_file")  # type: ignore[misc]
    )
    return sqlplan.parse_file_times(answer)
