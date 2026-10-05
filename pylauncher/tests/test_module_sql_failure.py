"""A module's SQL that fails, through the real route (T214, PR 305's live check, 2026-10-05).

The live check put a file of not-SQL into mod-npc-beastmaster on yulon-ubuntu and
pressed Apply module SQL. `tests/data/importer-module-sql-failed.txt` is what the
Modules tab's Last action box then held, colour codes and all, from the importer's
first line to its last. It showed four defects: the colour codes on screen and in
the log, a Details that began mid-sentence without the mysql error, a sentence
carrying `--rm` and `docker compose logs`, and a per-file report that said the
failed file was "applied" and every file applied on an earlier day "refused".

Everything from `ControllerView.apply_module_sql()` down to `docker.run_attached()`
is real here. The doubles are the docker CLI (the streamed importer, and the three
questions `docker.apply_module_sql()` asks before it runs it) and the database's
`updates` ledger, which is what says whether a file is in the database.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterator, Mapping
from pathlib import Path

import pytest

from tests.test_controller_view import WOTLK, _Ps, _services
from yulon import docker, runner
from yulon import platform as yulon_platform
from yulon.apply import ApplyError
from yulon.controller_wow_wotlk import docker_ctl, modules
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

CAPTURED = (
    (Path(__file__).parent / "data" / "importer-module-sql-failed.txt")
    .read_text(encoding="utf-8")
    .splitlines()
)
"""The importer's lines as the live check's Last action box held them (79 lines)."""

PLAIN = [runner.strip_ansi(line).strip() for line in CAPTURED]
"""The same lines as a person reads them."""

MYSQL_ERROR = (
    "ERROR 1064 (42000) at line 2: You have an error in your SQL syntax; check the manual "
    "that corresponds to your MySQL server version for the right syntax to use near "
    "'THIS IS NOT SQL AT ALL' at line 1"
)

BEASTMASTER = "modules/mod-npc-beastmaster/data/sql"
WORLD = (
    "beastmaster_tames.sql",
    "beastmaster_tames_inserts.sql",
    "npc_beastmaster.sql",
    "zz_gate305_broken.sql",
)
CHARACTERS = ("track_tamed_pets.sql",)
EARLIER: dict[str, frozenset[str]] = {
    "world": frozenset(WORLD[:3]),
    "characters": frozenset(CHARACTERS),
}
"""What `updates` held on the live box: every beastmaster file but the broken one."""


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view's jobs run inline, as in `test_controller_view`."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    """`test_controller_view`'s Docker-free `runner.run`."""
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


class _Ledger:
    """The `updates` ledger: `SELECT name FROM updates ...` answered from `rows`."""

    def __init__(self, rows: Mapping[str, frozenset[str]], *, fails: bool = False) -> None:
        self.rows = rows
        self.fails = fails
        self.asked: list[tuple[str, str]] = []

    def query(self, db: str, statement: str) -> str:
        self.asked.append((db, statement))
        if self.fails:
            raise ApplyError("query -> acore_world failed: ERROR 2002 (HY000): Can't connect")
        assert statement.startswith("SELECT name FROM updates WHERE name IN ("), statement
        return "".join(f"{n}\n" for n in sorted(self.rows.get(db, ())) if f"'{n}'" in statement)


def _beastmaster_on_disk(server_dir: Path, extra: tuple[str, ...] = ()) -> None:
    for name in (*WORLD, *extra):
        path = server_dir / BEASTMASTER / "db-world" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-- x\n", encoding="utf-8")
    for name in CHARACTERS:
        path = server_dir / BEASTMASTER / "db-characters" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("-- x\n", encoding="utf-8")


def _importer(monkeypatch: pytest.MonkeyPatch, said: list[str], exit_code: int) -> None:
    """The docker CLI under `docker.apply_module_sql()`: its three questions, then the importer.

    The importer's lines are yielded one at a time, as `runner.stream()` does, and a
    non-zero exit arrives as its `CalledProcessError` at the end.
    """
    monkeypatch.setattr(yulon_platform, "_resolved_docker_cli", "docker")
    monkeypatch.setattr(docker, "install_project", lambda *a, **k: "t-project")
    monkeypatch.setattr(
        docker, "_running", lambda *a, **k: docker.Running(ours=(docker_ctl.SPEC.db,))
    )
    monkeypatch.setattr(docker, "importer_sees_modules", lambda *a, **k: True)
    monkeypatch.setattr(docker, "start_database", lambda *a, **k: None)

    def stream(
        cmd: list[str], cwd: Path | None = None, *, merge_stderr: bool = False
    ) -> Iterator[str]:
        assert cmd[:3] == ["docker", "compose", "run"], cmd
        yield from said
        if exit_code:
            raise subprocess.CalledProcessError(exit_code, cmd)

    monkeypatch.setattr(runner, "stream", stream)


def _view(ps: _Ps, tmp_path: Path, ledger: _Ledger) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.module_sql = lambda output: modules.apply_module_sql(
        tmp_path, output=output, ledger=ledger
    )
    return ControllerView(WOTLK, services, status_poll_ms=0)


def _press_the_live_check(
    ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ledger: _Ledger
) -> ControllerView:
    _beastmaster_on_disk(tmp_path)
    _importer(monkeypatch, CAPTURED, 1)
    view = _view(ps, tmp_path, ledger)
    view.apply_module_sql()
    return view


def _report(view: ControllerView) -> list[str]:
    return view.module_report.toPlainText().splitlines()


def _verdict(view: ControllerView, db_dir: str, name: str, db: str) -> str:
    where = f"sql data/sql/{db_dir}/{name} -> {db}: "
    found = [line for line in _report(view) if line.startswith(where)]
    assert len(found) == 1, (where, _report(view))
    return found[0][len(where) :]


def test_no_colour_code_reaches_the_box_details_or_the_log(
    qapp: object,
    ps: _Ps,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Item 1: the captured lines start with ESC[0m ESC[36m, and the box showed "[0m[36m".

    Mutation: stop stripping in `docker.run_attached()`, and the box, Details and the
    log each carry the codes again.
    """
    caplog.set_level(logging.INFO)
    view = _press_the_live_check(ps, tmp_path, monkeypatch, _Ledger(EARLIER))

    box = view.module_report.toPlainText()
    details = view.module_details.text()
    logged = "\n".join(record.getMessage() for record in caplog.records)
    for where, text in (("box", box), ("Details", details), ("log", logged)):
        assert "\x1b" not in text, where
        assert "[0m" not in text and "[36m" not in text and "[31;1m" not in text, where
    assert "Updating World database..." in box, "the run log keeps the importer's stream"


def test_details_start_at_a_whole_line_and_hold_the_mysql_error(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 2: Details began "…_world' failed!" and the ERROR 1064 line was not in it.

    Mutation: give Details the old `last_words()`, and the first line is cut and the
    error line is gone.
    """
    view = _press_the_live_check(ps, tmp_path, monkeypatch, _Ledger(EARLIER))

    details = view.module_details.text().splitlines()
    assert details, "no Details at all"
    for line in details:
        assert line in PLAIN, f"not a whole line of what the importer printed: {line!r}"
    assert MYSQL_ERROR in details
    assert any("zz_gate305_broken.sql' to database 'acore_world' failed!" in d for d in details)
    assert not details[0].startswith("…")


def test_the_sentence_says_what_happened_in_plain_words(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 3: "The container was removed when it exited (`--rm`) ... `docker compose logs`".

    Mutation: put the old sentence back, and the backticks and `--rm` are on the line.
    """
    view = _press_the_live_check(ps, tmp_path, monkeypatch, _Ledger(EARLIER))

    said = [line for line in _report(view) if line.startswith("The SQL run did not finish")]
    assert len(said) == 1, _report(view)
    for word in ("`", "--rm", "compose", "ac-db-import", "exited"):
        assert word not in said[0], (word, said[0])
    assert "Details" in said[0]


def test_each_file_says_what_the_database_holds(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 4: the failed file read "applied", and the files of earlier days "refused: <sentence>".

    The importer's own failure line names the file it could not apply, and the
    `updates` ledger names every file the database holds; the order the files were
    listed in decides nothing.

    Mutation: read "applied" from `Applying update` alone, and the broken file is
    applied again; drop the ledger, and the earlier files are not "already applied".
    """
    ledger = _Ledger(EARLIER)
    view = _press_the_live_check(ps, tmp_path, monkeypatch, ledger)

    assert _verdict(view, "db-world", "zz_gate305_broken.sql", "world") == f"failed: {MYSQL_ERROR}"
    for name in WORLD[:3]:
        assert _verdict(view, "db-world", name, "world") == "already applied", name
    assert _verdict(view, "db-characters", "track_tamed_pets.sql", "characters") == (
        "already applied"
    )
    text = view.module_report.toPlainText()
    assert "refused:" not in text
    assert not [line for line in _report(view) if line.endswith(": applied")]
    assert {db for db, _ in ledger.asked} == {"world", "characters"}


APPLIED_NOW = ">> Applying update \"beastmaster_new.sql\" '1A2B3C4'..."


def test_a_file_applied_now_reads_applied_only_when_the_database_has_it(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`Applying update` is the importer starting a file, not the database holding it."""
    _beastmaster_on_disk(tmp_path, extra=("beastmaster_new.sql", "beastmaster_lost.sql"))
    _importer(
        monkeypatch,
        [
            "Updating World database...",
            APPLIED_NOW,
            ">> Applying update \"beastmaster_lost.sql\" '5D6E7F8'...",
            ">> World database is up-to-date! Containing 2 new and 0 archived updates.",
        ],
        0,
    )
    rows = {**EARLIER, "world": EARLIER["world"] | {"beastmaster_new.sql"}}
    view = _view(ps, tmp_path, _Ledger(rows))
    view.apply_module_sql()

    assert _verdict(view, "db-world", "beastmaster_new.sql", "world") == "applied"
    lost = _verdict(view, "db-world", "beastmaster_lost.sql", "world")
    assert lost.startswith("not applied: "), lost
    assert _verdict(view, "db-world", "npc_beastmaster.sql", "world") == "already applied"


def test_an_unreadable_ledger_claims_nothing_the_database_was_not_asked_about(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No ledger, no "applied" and no "already applied": what the importer said, and that."""
    view = _press_the_live_check(ps, tmp_path, monkeypatch, _Ledger(EARLIER, fails=True))

    assert _verdict(view, "db-world", "zz_gate305_broken.sql", "world") == f"failed: {MYSQL_ERROR}"
    for name in WORLD[:3]:
        verdict = _verdict(view, "db-world", name, "world")
        assert verdict.startswith("not known"), verdict
        assert "already applied" not in verdict
    assert not [line for line in _report(view) if line.endswith(": applied")]


def test_the_log_file_and_details_draw_no_colour_code(qapp: object) -> None:
    """Item 1's other two places: whatever reaches them, the codes do not.

    Mutation: format with a plain `logging.Formatter`, or set Details' text as it
    comes, and the code is in the file or the fold.
    """
    from yulon import log
    from yulon.ui.widgets.details import Details

    record = logging.LogRecord(
        "yulon.docker", logging.WARNING, __file__, 1, f"\x1b[31;1m{MYSQL_ERROR}\x1b[0m", None, None
    )
    assert log.PlainFormatter("%(message)s").format(record) == MYSQL_ERROR
    handler = log._stderr_handler
    assert handler is not None and isinstance(handler.formatter, log.PlainFormatter)

    details = Details()
    details.set_text(f"\x1b[0m\x1b[36m{MYSQL_ERROR}\x1b[0m")
    assert details.text() == MYSQL_ERROR
