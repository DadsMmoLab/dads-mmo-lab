"""T878: a first realm switch's db-import output no longer floods the Server tab.

The import's output went onto the Server tab's problem line, thousands of lines tall, and
pushed Play far down the page. It now goes under that line's Details fold (the pattern a
failed Start's last lines use), folded; the switch's own sentences stay on the line. A
failed import's last lines stay open under the line.

The switch is the product's own (`coa_switch.switch_import` over fake docker seams), as in
`test_coa_first_switch`; only the one-shot's output is made long.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_coa_first_switch import _Docker, _switch
from tests.test_coa_realm import FakeSql, coa_schemas, write_file
from tests.test_controller_view import _FakeClientDir, _Ps
from yulon import docker, runner
from yulon.catalog.catalog import load_catalog
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline

FLOOD = 2000


class _Chatty(_Docker):
    """The one-shot prints `lines` lines before it ends, as a first import does."""

    def __init__(self, sql: FakeSql, lines: int, *, returncode: int = 0) -> None:
        super().__init__(sql, returncode=returncode)
        self.lines = lines

    def one_shot(self, service: str, folder: Path, sink: docker.OutputSink) -> docker.AttachedRun:
        for number in range(self.lines):
            sink(f"import line {number}")
        ran = super().one_shot(service, folder, sink)
        if ran.returncode == 0:
            return ran
        return docker.AttachedRun(
            ran.returncode, ("import line 1998", "import line 1999", "ERROR 1146: no such table")
        )


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fake: _Docker, sql: FakeSql) -> Any:
    """A CoA Server tab, shown, whose realm switch is the product's own over `fake`."""
    write_file(tmp_path, "coa")
    entry = load_catalog().get("wow-coa")
    services = ControllerServices.for_wotlk(entry, tmp_path, None)
    services.set_play_client_dir = _FakeClientDir()  # gives the tab its Play button
    view = ControllerView(entry, services, status_poll_ms=0, job_runner=run_inline)
    services.realm_switch = _switch(tmp_path, sql, fake)  # type: ignore[assignment]
    monkeypatch.setattr(view, "_confirm", lambda title, question: True)
    monkeypatch.setattr(view, "refresh_status", lambda: None)
    view.realm_type_choice.setCurrentIndex(1)
    assert view.realm_type_choice.currentData() == "hero"
    view.resize(900, 1400)
    view.show()
    return view


def _switched(
    qapp: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: int, returncode: int = 0
) -> tuple[Any, int, int]:
    """(view, Play's y before, Play's y after) for a first switch whose import prints `lines`."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    sql = FakeSql()
    coa_schemas(sql)
    view = _view(tmp_path, monkeypatch, _Chatty(sql, lines, returncode=returncode), sql)
    qapp.processEvents()
    assert view.play_button is not None
    before = view.play_button.mapTo(view, view.play_button.rect().topLeft()).y()
    view.switch_realm_type()
    qapp.processEvents()
    after = view.play_button.mapTo(view, view.play_button.rect().topLeft()).y()
    return view, before, after


def test_a_first_switch_importing_thousands_of_lines_does_not_push_play_down(
    qapp: Any, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: send the import's lines to the problem line again -> Play moves far down."""
    quiet, quiet_before, quiet_after = _switched(qapp, tmp_path / "quiet", monkeypatch, 0)
    view, before, after = _switched(qapp, tmp_path / "loud", monkeypatch, FLOOD)

    said = view.problem_label.text()
    assert "import line" not in said
    # The switch's own six sentences, as a switch with a silent import has; no more.
    assert len(said.splitlines()) == len(quiet.problem_label.text().splitlines()), said
    assert after - before <= quiet_after - quiet_before, (before, after, quiet_after)
    # The whole output is kept, folded, under Details.
    assert view.problem_details.text().count("import line") == FLOOD
    assert view.problem_details.collapsed


def test_a_failed_import_shows_its_last_lines_open_under_the_line(
    qapp: Any, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal's `detail` (the importer's last lines) is shown, not left folded away."""
    view, _before, _after = _switched(qapp, tmp_path, monkeypatch, FLOOD, returncode=1)

    details = view.problem_details
    assert "ERROR 1146: no such table" in details.text()
    assert "import line 1999" in details.text()
    assert not details.isHidden() and not details.collapsed
    assert "import line" not in view.problem_label.text()
    assert len(view.problem_label.text().splitlines()) <= 6
