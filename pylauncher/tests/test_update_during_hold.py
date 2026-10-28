"""T738 parts 1 and 2: an Update pressed during a hold names the holder, once.

Part 1: an Update pressed during a hold showed "...Nothing was started or
stopped. Nothing was started." Part 2: the dialog was always titled "A restore
is running", even for a backup or move hold. The title now comes from the
holder's press, and " Nothing was started." is added only when the held
sentence has no "nothing was".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import WOTLK, _latest, _Ps
from yulon import docker, forgetting, move_flows, runner
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline


def _view(ps: _Ps, tmp_path: Path) -> tuple[ControllerView, object]:
    services, spy = _latest(ps, tmp_path)
    return ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline), spy


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    """Every `docker` command any press runs, recorded; nothing reaches a daemon."""
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _shown(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    shown: list[tuple[str, str]] = []
    monkeypatch.setattr(
        controller_view_module,
        "show_information",
        lambda _p, title, text: shown.append((title, text)),
    )
    return shown


def test_update_during_a_restore_hold_says_nothing_was_started_once(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 1: the held sentence already ends "Nothing was started or stopped.".

    Mutation: always add " Nothing was started." and the body doubles it.
    """
    view, spy = _view(ps, tmp_path)
    shown = _shown(monkeypatch)
    with docker.hold_the_server(
        tmp_path, forgetting.RESTORE_HOLDS_THE_SERVER, press=forgetting.PRESS_RESTORE
    ):
        assert view.update_to_latest() is False
    assert spy.presses == [], "the update started despite the hold"
    assert shown == [("A restore is running", forgetting.RESTORE_HOLDS_THE_SERVER)], shown


def test_update_during_a_backup_hold_is_titled_a_backup(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 2: the title comes from the holder's press, not always the restore's.

    Mutation: title fixed to "A restore is running".
    """
    view, spy = _view(ps, tmp_path)
    shown = _shown(monkeypatch)
    with docker.hold_the_server(
        tmp_path, forgetting.BACKUP_HOLDS_THE_SERVER, press=forgetting.PRESS_BACKUP
    ):
        assert view.update_to_latest() is False
    assert spy.presses == [], "the update started despite the hold"
    assert shown == [("A backup is running", forgetting.BACKUP_HOLDS_THE_SERVER)], shown


def test_update_during_a_move_hold_is_titled_a_move(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Part 2's other side: a Pack/Bring in hold titles the dialog "A move is running"."""
    view, spy = _view(ps, tmp_path)
    shown = _shown(monkeypatch)
    with docker.hold_the_server(
        tmp_path, forgetting.MOVE_HOLDS_THE_SERVER, press=move_flows.PRESS_BRING_IN
    ):
        assert view.update_to_latest() is False
    assert spy.presses == [], "the update started despite the hold"
    assert shown == [("A move is running", forgetting.MOVE_HOLDS_THE_SERVER)], shown


def test_update_while_this_tabs_restore_runs_adds_nothing_was_started_once(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No hold yet (the lease only): the fallback sentence has no "nothing was".

    Mutation: never add the suffix and the body leaves out what was not started.
    """
    view, spy = _view(ps, tmp_path)
    shown = _shown(monkeypatch)
    view._restore_running = True
    assert view.update_to_latest() is False
    assert spy.presses == [], "the update started despite the running restore"
    assert shown == [
        (
            "A restore is running",
            f"{forgetting.RESTORE_HOLDS_THE_DATABASES} Nothing was started.",
        )
    ], shown


def test_the_hold_names_the_press_that_took_it(tmp_path: Path) -> None:
    """Part 2's mechanism: the hold stores the holder's press for the dialog title."""
    assert docker.holder(tmp_path) is None
    with docker.hold_the_server(tmp_path, "a reason", press="Backup"):
        hold = docker.holder(tmp_path)
        assert hold is not None
        assert (hold.reason, hold.press) == ("a reason", "Backup")
        assert docker.held_reason(tmp_path) == "a reason"
    assert docker.holder(tmp_path) is None


def test_two_holds_with_the_same_reason_let_go_one_at_a_time(tmp_path: Path) -> None:
    """The finally drops the one hold that was exited, not every equal hold.

    Mutation: drop every equal hold and the outer hold stops refusing too early.
    """
    with docker.hold_the_server(tmp_path, "same", press="Restore"):
        with docker.hold_the_server(tmp_path, "same", press="Restore"):
            assert docker.holder(tmp_path) is not None
        assert docker.held_here(tmp_path), "the inner exit dropped the outer hold too"
        assert docker.holder(tmp_path) is not None
        assert docker.held_reason(tmp_path) == "same"
    assert docker.holder(tmp_path) is None
    assert not docker.held_here(tmp_path)
