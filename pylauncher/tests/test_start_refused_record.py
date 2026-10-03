"""T197 fix round 2: `native.START_REFUSED_FILE` refuses every start, on every family.

A rollback that left the image tags mixed leaves no build a start may run: in that
geometry `compose up -d` would start the NEW import image beside the OLD world server.
The record is in the server folder, so it outlives the app, and `Controller.refuse_start()`
-- the one door every Start, Start and play, the launcher's PLAY, Restart and Recreate
goes through -- reads it before the family's own `start_guard`. Each press is driven
here over a folder holding the record, and none may start, stop or remove anything.
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
from yulon import runner
from yulon.catalog import native
from yulon.controller import Controller, StartRefused
from yulon.controller_wow_tortoise import botdash
from yulon.controller_wow_tortoise.controller import TortoiseController
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

REFUSED = native.REBUILD_OWED_REFUSAL


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


@pytest.fixture
def launched(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every `LaunchSpec` Play hands to `play_launch.launch()`; nothing is started (Windows)."""
    from yulon import play_launch

    monkeypatch.setattr(controller_view_module.platform, "detect", lambda: "windows")
    specs: list[object] = []
    monkeypatch.setattr(play_launch, "launch", lambda spec, **_k: specs.append(spec))
    return specs


def _owed(server_dir: Path) -> None:
    assert native.owe_start(server_dir, native.OWED_REBUILD) == ""


def _touched(ps: _Ps, since: int = 0) -> list[list[str]]:
    """Every compose up, stop, rm or down, and every plain `docker stop`, since `since`."""
    return [
        c
        for c in ps.calls[since:]
        if c[:3]
        in (
            ["docker", "compose", "up"],
            ["docker", "compose", "stop"],
            ["docker", "compose", "rm"],
            ["docker", "compose", "down"],
        )
        or c[:2] == ["docker", "stop"]
    ]


def test_the_record_refuses_a_new_controller_and_goes_once_removed(tmp_path: Path) -> None:
    """The app restarted: a controller made fresh over the folder reads it from disk."""
    _owed(tmp_path)
    with pytest.raises(StartRefused) as refused:
        Controller(WOTLK.container_spec(), tmp_path).refuse_start()
    assert str(refused.value) == REFUSED
    (tmp_path / native.START_REFUSED_FILE).unlink()
    Controller(WOTLK.container_spec(), tmp_path).refuse_start()


def test_the_start_button_starts_nothing_and_says_why(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _owed(tmp_path)
    view.start_server()
    assert view.problem_label.text() == REFUSED
    assert _touched(ps) == []


def test_start_and_play_starts_neither_the_server_nor_the_game(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, launched: list[object]
) -> None:
    _answer(monkeypatch, controller_view_module.QMessageBox.StandardButton.Yes)
    original = _game_client(tmp_path / "clients" / "WoW")
    view, _ = _play_view(ps, tmp_path, original=original, play=_built(original, tmp_path))
    _owed(tmp_path)
    ps.names = ""
    view.play()
    assert _touched(ps) == []
    assert launched == []
    assert view.problem_label.text() == REFUSED


@pytest.mark.parametrize("press", ["restart", "recreate"])
def test_restart_and_recreate_stop_and_remove_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, press: str
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    monkeypatch.setattr(view, "_confirm", lambda title, question: True)
    spec = WOTLK.container_spec()
    ps.names = "".join(f"{n}\n" for n in (spec.db, spec.auth, spec.world))
    _owed(tmp_path)
    since = len(ps.calls)
    view.restart_server() if press == "restart" else view.recreate_containers()
    assert view.tuning_report.toPlainText() == f"FAILED: {REFUSED}"
    assert _touched(ps, since) == []


def test_stopping_the_other_server_to_start_this_one_stops_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    _owed(tmp_path)
    view.stop_other_and_start()
    assert REFUSED in view.problem_label.text()
    assert _touched(ps) == []


def test_the_launchers_play_starts_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, launched: list[object], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        controller_view_module.QMessageBox,
        "exec",
        lambda self: controller_view_module.QMessageBox.StandardButton.Yes,
    )
    window, view, _play = _launcher(ps, tmp_path)
    _owed(tmp_path)
    ps.names = ""
    window.play_button.click()
    assert launched == []
    assert _touched(ps) == []
    assert view.problem_label.text() == REFUSED


def test_a_tortoise_start_is_refused_before_its_bot_dashboard_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tortoise's own `start()` brings the dashboard up first; a refused start must not."""
    asked: list[str] = []
    monkeypatch.setattr(botdash, "start_if_on", lambda *a, **_k: asked.append("dashboard"))
    _owed(tmp_path)
    with pytest.raises(StartRefused, match="must be rebuilt"):
        TortoiseController(tmp_path).start()
    assert asked == []
