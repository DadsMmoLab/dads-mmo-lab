"""T566: a stuck world update whose file is gone from the checkout has a way out.

T545's "Apply database corrections…" listed such a row as not repeatable and stopped at it
for ever. Owner decisions of 2026-10-08:

1. The dialog offers "Skip this file": the row is recorded `skipped`, the updates behind it
   run, and the player is told that whatever the half-run file already changed stays as it is.
2. A "missing" file that is the same bytes under a new name is cleared by itself: the record
   moves to the new name and the file is retried there, with one line in the report.

Driven on T545's sqlite-backed fake, like test_retry_stuck_world_updates.py.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from tests.test_plan_corrections import _Route, an_engine, folder
from tests.test_retry_stuck_world_updates import (
    PLAN,
    U1,
    U2,
    U3,
    _Db,
    _lay,
    _ledger,
    _stuck_server,
)
from tests.test_world_content_updates import CONTENT, _run
from yulon.catalog import native
from yulon.catalog.families import sqlplan
from yulon.catalog.installer import InstallerError

RENAMED = "src/tbc-db/Updates/0002_renamed_upstream.sql"
PHASE = "content updates"


def _remove(server_dir: Path, rel: str = U2) -> None:
    (server_dir / rel).unlink()


def _rename(server_dir: Path, new: str = RENAMED, old: str = U2) -> None:
    body = (server_dir / old).read_text(encoding="utf-8")
    _lay(server_dir, new, body)
    (server_dir / old).unlink()


def _read(db: _Db, tmp_path: Path):  # type: ignore[no-untyped-def]
    engine = an_engine(PLAN, db)
    return engine, engine.correction_check(folder(tmp_path))


# -- the status ----------------------------------------------------------------------------


def test_skipped_is_a_state_the_ledger_writes_and_reads_back() -> None:
    row = sqlplan.FileRow(PHASE, U2, "1" * 64, "skipped")
    assert sqlplan.FILE_SKIPPED == "skipped"
    script = sqlplan.file_rows_sql("mangos", (row,), now=7)
    assert "'skipped'" in script
    assert sqlplan.parse_file_ledger(f"{PHASE}\t{U2}\t{'1' * 64}\tskipped\n") == {(PHASE, U2): row}


def test_every_reader_treats_a_skipped_row_as_neither_pending_nor_stuck(tmp_path: Path) -> None:
    """A skipped row: its phase is not held back, it is not named unsure/failed/changed, its
    file is not new, and a file that comes back under its name is named and never run."""
    rels = [f"src/tbc-db/Updates/000{n}.sql" for n in (1, 2, 3)]
    runs = [_run(rel, CONTENT, tmp_path) for rel in rels]
    for run in runs:
        _lay(tmp_path, run.rel, f"-- {run.rel}\n")
    sha = {rel: sqlplan.file_digest(tmp_path / rel) for rel in rels}
    ledger = {
        (PHASE, rels[0]): sqlplan.FileRow(PHASE, rels[0], sha[rels[0]], "seeded"),
        (PHASE, rels[1]): sqlplan.FileRow(PHASE, rels[1], "f" * 64, "skipped"),
    }
    owed = sqlplan.pending_files(runs, ledger)
    assert [run.rel for run in owed.new] == [rels[2]], "the file behind a skipped one runs"
    assert owed.withheld == () and owed.unsure == () and owed.failed == () and owed.changed == ()
    assert owed.skipped == (rels[1],), "a skipped file that is back is named, never run"

    # the same row with its file gone is silent: nothing of it is left to say
    gone = [run for run in runs if run.rel != rels[1]]
    quiet = sqlplan.pending_files(gone, ledger)
    assert quiet.skipped == () and quiet.unsure == () and quiet.failed == ()
    assert [run.rel for run in quiet.new] == [rels[2]]


# -- the reading and the dialog ---------------------------------------------------------------


def test_a_stuck_row_whose_file_is_gone_is_read_as_missing(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    ledger_sha = sqlplan.file_digest(server_dir / U2)
    _remove(server_dir)
    _, check = _read(db, tmp_path)
    assert check.state == "stale"
    (one,) = check.stuck
    assert (one.file, one.state, one.repeatable, one.behind) == (U2, "failed", False, 1)
    assert one.missing and one.renamed_from == ""
    assert one.sha256 == ledger_sha and one.at_unix > 0


def test_a_file_still_in_the_checkout_is_never_offered_a_skip(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    _, check = _read(db, tmp_path)
    assert not check.stuck[0].missing
    text = native.stuck_world_updates_text(check.stuck)
    assert native.SKIP_STUCK_LABEL not in text and "skip" not in text.lower()


def test_the_dialog_for_a_missing_file_offers_skip_and_says_what_it_leaves(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _remove(server_dir)
    engine, check = _read(db, tmp_path)
    text = engine.correction_confirmation(check, folder(tmp_path))
    assert U2 in text and "no longer in the sources" in text
    assert native.SKIP_STUCK_LABEL in text
    assert "stays as it is" in text, text
    assert "1 newer world update(s) waiting behind it" in text


def test_the_banner_names_a_missing_file_and_still_offers_the_press(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _remove(server_dir)
    _, check = _read(db, tmp_path)
    banner = native.corrections_banner_text(check)
    assert "0002.sql" in banner and native.CORRECTIONS_BUTTON_LABEL in banner


def test_the_skip_choice_is_not_part_of_what_the_press_compares(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _remove(server_dir)
    _, check = _read(db, tmp_path)
    assert replace(check, skip_missing=True) == check


# -- the press ------------------------------------------------------------------------------------


def test_without_a_skip_the_press_still_stops_at_a_missing_file(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _remove(server_dir)
    engine, check = _read(db, tmp_path)
    lines = list(engine.apply_corrections(check, folder(tmp_path)))
    assert any("no longer in the sources" in line and U2 in line for line in lines), lines
    assert _ledger(db) == {U1: "seeded", U2: "failed"}
    assert "t3" not in db.tables("mangos")


def test_skip_records_the_file_skipped_runs_the_rest_and_clears_the_banner(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    ledger_sha = sqlplan.file_digest(server_dir / U2)
    _remove(server_dir)
    engine, check = _read(db, tmp_path)
    lines = list(engine.apply_corrections(replace(check, skip_missing=True), folder(tmp_path)))
    assert _ledger(db) == {U1: "seeded", U2: "skipped", U3: "applied"}, lines
    assert "t3" in db.tables("mangos") and "t2" not in db.tables("mangos")
    skipped = [line for line in lines if U2 in line and "skipped" in line]
    assert skipped and "stays as it is" in skipped[0], lines
    stored = db.rows("mangos", "SELECT sha256 FROM yulon_install_file WHERE state = 'skipped'")
    assert stored == [(ledger_sha,)], "the record keeps the bytes it was tried with"
    assert engine.correction_check(folder(tmp_path)).state == "current"


def test_skip_leaves_a_stuck_file_that_is_still_there_to_be_run_again(tmp_path: Path) -> None:
    """Skip is for what is gone. A stuck file still in the checkout behind it is retried."""
    db, server_dir = _stuck_server(tmp_path)
    sqlplan.record_world_files(
        (sqlplan.FileRow(PHASE, U3, sqlplan.file_digest(server_dir / U3), "started"),),
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
    )
    _remove(server_dir)
    engine, check = _read(db, tmp_path)
    assert [(one.file, one.missing) for one in check.stuck] == [(U2, True), (U3, False)]
    list(engine.apply_corrections(replace(check, skip_missing=True), folder(tmp_path)))
    assert _ledger(db) == {U1: "seeded", U2: "skipped", U3: "applied"}
    assert "t3" in db.tables("mangos")


def test_a_skipped_file_that_comes_back_is_not_run_and_is_not_stuck(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    body = (server_dir / U2).read_text(encoding="utf-8")
    _remove(server_dir)
    engine, check = _read(db, tmp_path)
    list(engine.apply_corrections(replace(check, skip_missing=True), folder(tmp_path)))
    _lay(server_dir, U2, body)  # a later checkout brings it back
    assert engine.correction_check(folder(tmp_path)).state == "current"
    ledger = sqlplan.parse_file_ledger(
        "".join(
            f"{p}\t{f}\t{s}\t{st}\n"
            for p, f, s, st in db.rows(  # type: ignore[misc]
                "mangos", "SELECT phase, file, sha256, state FROM yulon_install_file"
            )
        )
    )
    runs = [_run(rel, CONTENT, server_dir) for rel in (U1, U2, U3)]
    owed = sqlplan.pending_files(runs, ledger)
    assert owed.new == () and owed.skipped == (U2,)
    assert "t2" not in db.tables("mangos"), "it was never run"


def test_a_file_that_came_back_since_the_dialog_is_not_skipped(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    body = (server_dir / U2).read_text(encoding="utf-8")
    _remove(server_dir)
    engine, check = _read(db, tmp_path)
    _lay(server_dir, U2, body)
    with pytest.raises(InstallerError, match="changed since the confirmation"):
        list(engine.apply_corrections(replace(check, skip_missing=True), folder(tmp_path)))
    assert _ledger(db)[U2] == "failed"


# -- a file upstream renamed (same bytes) ---------------------------------------------------------


def test_a_same_bytes_rename_is_offered_under_its_new_name_not_as_missing(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _rename(server_dir)
    _, check = _read(db, tmp_path)
    (one,) = check.stuck
    assert (one.file, one.renamed_from, one.missing) == (RENAMED, U2, False)
    assert one.state == "failed" and one.behind == 1 and one.at_unix > 0
    text = native.stuck_world_updates_text(check.stuck)
    assert RENAMED in text and "0002.sql" in text and "new name" in text
    assert native.SKIP_STUCK_LABEL not in text


def test_the_press_moves_the_record_to_the_new_name_and_retries_it_there(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _rename(server_dir)
    engine, check = _read(db, tmp_path)
    lines = list(engine.apply_corrections(check, folder(tmp_path)))  # no skip needed
    assert _ledger(db) == {U1: "seeded", RENAMED: "applied", U3: "applied"}, lines
    assert {"t2", "t3"} <= db.tables("mangos")
    said = [line for line in lines if U2 in line and RENAMED in line]
    assert len(said) == 1 and "same" in said[0], lines
    assert engine.correction_check(folder(tmp_path)).state == "current"


def test_a_rename_whose_retry_is_refused_stays_stuck_under_the_new_name(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _rename(server_dir)
    engine, check = _read(db, tmp_path)
    db.refuse = "t2"
    list(engine.apply_corrections(check, folder(tmp_path)))
    assert _ledger(db) == {U1: "seeded", RENAMED: "failed"}, "the old record is gone, not doubled"
    db.refuse = None
    again = engine.correction_check(folder(tmp_path))
    assert [(one.file, one.renamed_from) for one in again.stuck] == [(RENAMED, "")]


def test_two_files_with_the_same_bytes_are_not_a_rename(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    body = (server_dir / U2).read_text(encoding="utf-8")
    _remove(server_dir)
    _lay(server_dir, "src/tbc-db/Updates/0002_a.sql", body)
    _lay(server_dir, "src/tbc-db/Updates/0002_b.sql", body)
    engine, check = _read(db, tmp_path)
    (one,) = check.stuck
    assert one.missing and one.file == U2 and one.renamed_from == ""
    list(engine.apply_corrections(check, folder(tmp_path)))
    assert _ledger(db) == {U1: "seeded", U2: "failed"}, "an ambiguous match re-points nothing"


def test_a_file_renamed_and_edited_is_not_the_same_bytes(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    _remove(server_dir)
    _lay(server_dir, RENAMED, "CREATE TABLE IF NOT EXISTS t2 (id INT, extra INT);\n")
    _, check = _read(db, tmp_path)
    (one,) = check.stuck
    assert one.missing and one.file == U2


def test_a_copy_that_already_has_a_record_is_not_a_rename_target(tmp_path: Path) -> None:
    db, server_dir = _stuck_server(tmp_path)
    body = (server_dir / U2).read_text(encoding="utf-8")
    _lay(server_dir, RENAMED, body)
    sqlplan.record_world_files(
        (sqlplan.FileRow(PHASE, RENAMED, sqlplan.file_digest(server_dir / RENAMED), "applied"),),
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
    )
    _remove(server_dir)
    _, check = _read(db, tmp_path)
    (one,) = check.stuck
    assert one.missing and one.file == U2


def test_a_renamed_stuck_file_is_not_recorded_as_already_applied(tmp_path: Path) -> None:
    """The T531 "moved" rule marks a renamed file `seeded` -- right for a file that went in,
    wrong for one that failed or stopped: it never completed, so it must stay owed."""
    rels = [f"src/tbc-db/Updates/{n}.sql" for n in ("0001", "0002_renamed")]
    runs = [_run(rel, CONTENT, tmp_path) for rel in rels]
    _lay(tmp_path, rels[0], "-- the first\n")
    _lay(tmp_path, rels[1], "-- the second\n")
    old = "src/tbc-db/Updates/0002.sql"
    ledger = {
        (PHASE, rels[0]): sqlplan.FileRow(
            PHASE, rels[0], sqlplan.file_digest(tmp_path / rels[0]), "seeded"
        ),
        (PHASE, old): sqlplan.FileRow(
            PHASE, old, sqlplan.file_digest(tmp_path / rels[1]), "failed"
        ),
    }
    owed = sqlplan.pending_files(runs, ledger)
    assert owed.moved == () and owed.failed == (old,) and owed.withheld == (rels[1],)


# -- the dialog on the Server tab -----------------------------------------------------------------

SB = QMessageBox.StandardButton


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> object:
    from tests.test_controller_view import _Ps
    from yulon import runner
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


class _StuckRoute(_Route):
    """The corrections route of a server with one stuck world update, its file there or gone."""

    def __init__(self, *, missing: bool) -> None:
        super().__init__("stale")
        self.missing = missing

    def check(self) -> native.CorrectionCheck:
        self.checks += 1
        one = native.StuckWorldUpdate(
            PHASE, U2, "failed", False, behind=1, sha256="a" * 64, missing=self.missing
        )
        return native.CorrectionCheck("stale", stuck=(one,))

    def confirmation(self, check: native.CorrectionCheck) -> str:
        self.confirmed.append(check)
        return "run or skip the stuck update?"


def _box_answers(monkeypatch: pytest.MonkeyPatch, answer: QMessageBox.StandardButton) -> list[dict]:  # type: ignore[type-arg]
    seen: list[dict] = []  # type: ignore[type-arg]

    def record(box: QMessageBox) -> int:
        yes = box.button(SB.Yes)
        seen.append(
            {
                "buttons": box.standardButtons(),
                "yes": yes.text() if yes is not None else None,
                "default": box.standardButton(box.defaultButton()),
                "escape": box.standardButton(box.escapeButton()),
                "text": box.text(),
            }
        )
        return int(answer.value)

    monkeypatch.setattr(QMessageBox, "exec", record)
    return seen


def test_the_dialog_offers_skip_for_a_missing_file_and_the_press_carries_the_choice(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import pump_until
    from tests.test_plan_corrections import _database, _view

    route = _StuckRoute(missing=True)
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    seen = _box_answers(monkeypatch, SB.Yes)
    view.corrections_banner_button.click()  # type: ignore[attr-defined]
    pump_until(lambda: bool(route.pressed), "the skip press started")
    (box,) = seen
    assert box["buttons"] == SB.Yes | SB.Cancel
    assert box["yes"] == native.SKIP_STUCK_LABEL
    assert box["default"] == SB.Cancel and box["escape"] == SB.Cancel
    agreed, _cancel = route.pressed[0]
    assert agreed.skip_missing is True and agreed == route.confirmed[0]


def test_cancelling_the_skip_dialog_changes_nothing(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.test_plan_corrections import _database, _view

    route = _StuckRoute(missing=True)
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    seen = _box_answers(monkeypatch, SB.Cancel)
    view.corrections_banner_button.click()  # type: ignore[attr-defined]
    assert len(seen) == 1 and route.pressed == []


def test_a_stuck_file_that_is_still_there_gets_the_plain_dialog_and_no_skip(
    qapp: object, ps: object, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.conftest import pump_until
    from tests.test_plan_corrections import _database, _view

    route = _StuckRoute(missing=False)
    view = _view(ps, tmp_path, route)
    _database(ps, view, up=True)
    seen = _box_answers(monkeypatch, SB.Yes)
    view.corrections_banner_button.click()  # type: ignore[attr-defined]
    pump_until(lambda: bool(route.pressed), "the press started")
    (box,) = seen
    assert box["buttons"] == SB.Yes | SB.No and box["yes"] != native.SKIP_STUCK_LABEL
    assert route.pressed[0][0].skip_missing is False


# -- what Codex found: a claim that does not take the row it was shown ----------------------------


def _claim_rename(db: _Db, *, at: int) -> None:
    sqlplan.record_world_files(
        (sqlplan.FileRow(PHASE, RENAMED, "a" * 64, "started"),),
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
        reclaim_at=at,
        reclaim_state="failed",
        reclaim_file=U2,
    )


def test_a_rename_claim_takes_the_old_row_and_writes_the_new_one(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    seen = _at(db)
    _claim_rename(db, at=seen)
    assert _ledger(db) == {U1: "seeded", RENAMED: "started"}


def test_a_rename_claim_is_refused_when_the_old_row_changed_since_it_was_shown(
    tmp_path: Path,
) -> None:
    """The old and new names are different keys, so a `DELETE` that matched nothing used to
    leave the `INSERT` free to succeed: the press then ran a file it had not claimed."""
    db, _ = _stuck_server(tmp_path)
    seen = _at(db)
    with pytest.raises(InstallerError):
        _claim_rename(db, at=seen + 40)  # another press moved the row on: not the time shown
    assert _ledger(db) == {U1: "seeded", U2: "failed"}, "nothing was kept"


def test_a_rename_claim_is_refused_when_the_new_name_is_already_taken(tmp_path: Path) -> None:
    db, _ = _stuck_server(tmp_path)
    seen = _at(db)
    sqlplan.record_world_files(
        (sqlplan.FileRow(PHASE, RENAMED, "b" * 64, "started"),),
        marker_db="mangos",
        container="tbc-db",
        client="mariadb",
        password="x",
        exec_stdin=db.exec_stdin,
    )
    with pytest.raises(InstallerError):
        _claim_rename(db, at=seen)
    assert _ledger(db) == {U1: "seeded", U2: "failed", RENAMED: "started"}


def _at(db: _Db) -> int:
    (row,) = db.rows("mangos", f"SELECT at_unix FROM yulon_install_file WHERE file = '{U2}'")
    return int(row[0])  # type: ignore[arg-type]


def _agreed_then(
    tmp_path: Path, *, rename: bool
) -> tuple[_Db, Path, object, native.CorrectionCheck]:
    db, server_dir = _stuck_server(tmp_path)
    (_rename if rename else _remove)(server_dir)
    engine, check = _read(db, tmp_path)
    return db, server_dir, engine, replace(check, skip_missing=True)


def _loop(engine: object, tmp_path: Path, agreed: native.CorrectionCheck) -> list[str]:
    server_dir = folder(tmp_path).server_dir
    ctx = engine._update_context(server_dir, None)  # type: ignore[attr-defined]
    return list(engine._retry_stuck(ctx, agreed))  # type: ignore[attr-defined]


def test_a_file_shown_as_a_rename_that_is_gone_by_the_press_is_not_skipped(tmp_path: Path) -> None:
    """Skip is consent for the files the dialog showed as gone. A file it showed as there, even
    under a new name, that vanishes after the reading is not skipped on that consent."""
    db, server_dir, engine, agreed = _agreed_then(tmp_path, rename=True)
    (server_dir / RENAMED).unlink()
    lines = _loop(engine, tmp_path, agreed)
    assert any(RENAMED in line and "gone from the sources" in line for line in lines), lines
    assert _ledger(db) == {U1: "seeded", U2: "failed"}, "no skipped row, no half-moved record"


def test_a_file_shown_as_gone_that_is_back_by_the_press_is_not_run(tmp_path: Path) -> None:
    db, server_dir, engine, agreed = _agreed_then(tmp_path, rename=False)
    _lay(server_dir, U2, "CREATE TABLE IF NOT EXISTS t2 (id INT);\n")
    lines = _loop(engine, tmp_path, agreed)
    assert any(U2 in line and "back in the sources" in line for line in lines), lines
    assert _ledger(db) == {U1: "seeded", U2: "failed"} and "t2" not in db.tables("mangos")


# -- the cold review's two folds ------------------------------------------------------------------


def _twin_fixture(tmp_path: Path, twin_state: str | None) -> tuple[list[sqlplan.PhaseRun], dict]:  # type: ignore[type-arg]
    old = "src/tbc-db/Updates/0002.sql"
    new = "src/tbc-db/Updates/0002_renamed.sql"
    twin = "src/tbc-db/Updates/0001_gone.sql"
    runs = [_run(new, CONTENT, tmp_path)]
    _lay(tmp_path, new, "-- the second\n")
    sha = sqlplan.file_digest(tmp_path / new)
    ledger = {(PHASE, old): sqlplan.FileRow(PHASE, old, sha, "failed")}
    if twin_state is not None:
        ledger[(PHASE, twin)] = sqlplan.FileRow(PHASE, twin, sha, twin_state)
    return runs, ledger


def test_a_stuck_row_is_re_pointed_when_nothing_else_gone_holds_its_bytes(tmp_path: Path) -> None:
    runs, ledger = _twin_fixture(tmp_path, None)
    assert {k: r.rel for k, r in sqlplan.renamed_stuck(runs, ledger).items()} == {
        (PHASE, "src/tbc-db/Updates/0002.sql"): "src/tbc-db/Updates/0002_renamed.sql"
    }


@pytest.mark.parametrize("twin_state", ["seeded", "applied", "skipped"])
def test_a_stuck_row_whose_bytes_a_gone_row_also_holds_is_not_re_pointed(
    tmp_path: Path, twin_state: str
) -> None:
    """Two gone rows with one file's bytes: which of them the file is cannot be told."""
    runs, ledger = _twin_fixture(tmp_path, twin_state)
    assert sqlplan.renamed_stuck(runs, ledger) == {}


def test_a_file_moved_from_a_skipped_one_is_said_to_match_what_was_skipped(tmp_path: Path) -> None:
    runs, ledger = _twin_fixture(tmp_path, "skipped")
    del ledger[(PHASE, "src/tbc-db/Updates/0002.sql")]
    owed = sqlplan.pending_files(runs, ledger)
    assert [r.rel for r in owed.moved] == ["src/tbc-db/Updates/0002_renamed.sql"]
    assert owed.moved_from_skipped == (
        ("src/tbc-db/Updates/0002_renamed.sql", "src/tbc-db/Updates/0001_gone.sql"),
    )
    # bytes an applied row also holds were held by an update: not worded as a skip
    runs, ledger = _twin_fixture(tmp_path, "skipped")
    del ledger[(PHASE, "src/tbc-db/Updates/0002.sql")]
    other = "src/tbc-db/Updates/0003_gone.sql"
    ledger[(PHASE, other)] = sqlplan.FileRow(
        PHASE, other, ledger[(PHASE, "src/tbc-db/Updates/0001_gone.sql")].sha256, "applied"
    )
    assert sqlplan.pending_files(runs, ledger).moved_from_skipped == ()
