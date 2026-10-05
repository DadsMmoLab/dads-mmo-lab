"""T217 (a), the owner's decision of 2026-10-05: a folder off its build refuses every start.

When a failed update could not put a source folder back (case B), the old world
server would apply the database updates it finds in that folder -- the live proof
showed it migrating the playerbots database and crash-looping. Until 2026-10-05
Start warned and started; now Start, Start and play, the launcher's PLAY, Restart
and Recreate refuse through `Controller.refuse_start()`, and touch nothing. Each
press is driven here over a folder in that state, as `test_start_refused_record`
does for T197's record.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import (
    WOTLK,
    _answer,
    _built,
    _game_client,
    _play_view,
    _Ps,
    _services,
)
from tests.test_launcher_window import _launcher
from tests.test_start_refused_record import _touched, launched, ps  # noqa: F401 - fixtures
from yulon.catalog import native
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

OLD = "a" * 40
NEW = "b" * 40
MODULE = "modules/mod-playerbots"


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def _off(server_dir: Path) -> str:
    """Leave the module off its build, as case B does; return the refusal the player reads."""
    module = server_dir / MODULE
    (module / ".git").mkdir(parents=True, exist_ok=True)
    (module / ".git" / "HEAD").write_text(f"{NEW}\n", encoding="utf-8")
    native.remember_sources_off(server_dir, [("mod-playerbots/mod-playerbots", module, OLD)])
    refused = native.sources_off_refusal(server_dir)
    assert refused is not None and "Return to the tested pin" in refused
    return refused


def test_the_start_button_starts_nothing_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path  # noqa: F811
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    refused = _off(tmp_path)
    view.start_server()
    assert view.problem_label.text() == refused
    assert _touched(ps) == []


def test_start_and_play_starts_neither_the_server_nor_the_game(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    launched: list[object],  # noqa: F811
) -> None:
    _answer(monkeypatch, controller_view_module.QMessageBox.StandardButton.Yes)
    original = _game_client(tmp_path / "clients" / "WoW")
    view, _ = _play_view(ps, tmp_path, original=original, play=_built(original, tmp_path))
    refused = _off(tmp_path)
    ps.names = ""
    view.play()
    assert _touched(ps) == []
    assert launched == []
    assert view.problem_label.text() == refused


@pytest.mark.parametrize("press", ["restart", "recreate"])
def test_restart_and_recreate_stop_and_remove_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, press: str  # noqa: F811
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    monkeypatch.setattr(view, "_confirm", lambda title, question: True)
    spec = WOTLK.container_spec()
    ps.names = "".join(f"{n}\n" for n in (spec.db, spec.auth, spec.world))
    refused = _off(tmp_path)
    since = len(ps.calls)
    view.restart_server() if press == "restart" else view.recreate_containers()
    assert view.tuning_report.toPlainText() == refused, "Yu'lon's refusal, as written (T214)"
    assert _touched(ps, since) == []


def test_the_launchers_play_starts_nothing(
    qapp: object,
    ps: _Ps,  # noqa: F811
    tmp_path: Path,
    launched: list[object],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "exec",
        lambda self: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    window, view, _play = _launcher(ps, tmp_path)
    refused = _off(tmp_path)
    ps.names = ""
    window.play_button.click()
    assert launched == []
    assert _touched(ps) == []
    assert view.problem_label.text() == refused
