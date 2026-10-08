"""T559: a Restart button on the Server tab, beside Start and Stop.

A player's suggestion: "how about a Restart button? Would be handy for when
tinkering so I don't have to wait for it to hit start again manually."

One press is the Tuning tab's own restart (`_do_restart()`): the Stop that
saves every character, then the Start, in ONE job, so nothing can take the
server between them. The realm badge says what is happening, in order:
STOPPING, then STARTING, then whatever the next reading finds. The press is
live only while something of the server runs, and greyed with the reason
otherwise and while any job of ours runs, as Stop is.

Docker is `_Ps`, the Server tab tests' fake CLI; the jobs run inline.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import Qt

from tests.test_controller_view import (
    ALL_UP,
    WOTLK,
    _at,
    _clipped,
    _controller_in_the_real_window,
    _Gate,
    _Ps,
    _services,
)
from yulon import runner
from yulon.ui.controller_view import (
    RESTART_LABEL,
    SERVER_NOT_RUNNING,
    ControllerView,
    wait_for,
)
from yulon.ui.widgets.job import run_inline
from yulon.ui.widgets.reasons import reason_of


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: _Ps, tmp_path: Path, **kw: Any) -> ControllerView:
    kw.setdefault("job_runner", run_inline)
    return ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0, **kw)


def _compose_verbs(calls: list[list[str]]) -> list[str]:
    """The compose stops and ups, in order: what a restart does to the containers."""
    return [c[2] for c in calls if c[:2] == ["docker", "compose"] and c[2] in ("stop", "up")]


def test_restart_sits_between_stop_and_refresh(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = _view(ps, tmp_path)
    bar = view.stop_button.parentWidget()
    assert view.restart_button.parentWidget() is bar
    layout = bar.layout()
    order = [layout.itemAt(i).widget() for i in range(layout.count())]
    order = [w for w in order if w is not None]
    assert order.index(view.restart_button) == order.index(view.stop_button) + 1
    assert order.index(view.refresh_button) == order.index(view.restart_button) + 1
    assert view.restart_button.text() == RESTART_LABEL == "Restart"
    assert "saving every character" in view.restart_button.toolTip()


def test_restart_is_live_only_while_the_server_is_up(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Greyed with Stop's own reason when nothing runs; live when up or partly up."""
    view = _view(ps, tmp_path)
    ps.names = ""
    view.refresh_status()
    assert not view.restart_button.isEnabled()
    assert reason_of(view.restart_button) == SERVER_NOT_RUNNING
    ps.names = ALL_UP
    view.refresh_status()
    assert view.restart_button.isEnabled()
    ps.names = "ac-database\n"  # partly up
    view.refresh_status()
    assert view.restart_button.isEnabled()


def test_restart_is_greyed_with_the_reason_while_a_job_runs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    gate = _Gate()
    view = _view(ps, tmp_path, job_runner=gate)
    ps.names = ALL_UP
    view.refresh_status()
    assert view.restart_button.isEnabled()
    gate.hold = True
    view.stop_server()
    assert not view.restart_button.isEnabled()
    assert reason_of(view.restart_button) == wait_for("Stop")
    view.restart_from_server_tab()  # pressed anyway (a tray menu left open): nothing happens
    assert len(gate.queued) == 1
    gate.release()


def test_one_press_stops_then_starts_in_one_job_and_the_badge_says_each(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    ran: list[list[str]] = []

    def runner(work: Any, on_done: Any, on_error: Any) -> None:
        before = len(ps.calls)
        run_inline(work, on_done, on_error)
        ran.append(_compose_verbs(ps.calls[before:]))

    view = _view(ps, tmp_path, job_runner=runner)
    ps.names = ALL_UP
    view.refresh_status()
    badges: list[str] = []
    view.realm_badge.status_changed.connect(badges.append)
    said: list[str] = []
    view.realm_badge.status_changed.connect(lambda _s: said.append(view.status_label.text()))
    ran.clear()

    view.restart_from_server_tab()

    assert ["stop", "up"] in ran, f"the stop and the start were not one job: {ran}"
    assert badges[:2] == ["stopping", "starting"]
    assert said[:2] == ["Restarting: stopping…", "Restarting: starting…"]
    assert view.realm_badge.status not in ("stopping", "starting"), "the hold never ended"
    assert view.restart_button.isEnabled() and view.stop_button.isEnabled()


def test_a_restart_covers_the_restart_the_tuning_tab_was_owed(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path)
    ps.names = ALL_UP
    view.refresh_status()
    view._tuning_owed.setdefault("restart", set()).add("worldserver.conf")
    view._refresh_tuning_owed()

    view.restart_from_server_tab()

    assert "restart" not in view._tuning_owed


def test_a_refused_stop_starts_nothing_and_says_why_as_stop_does(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Another install's project owns the containers: Stop refuses, so Restart must too."""
    view = _view(ps, tmp_path)
    ps.names = ALL_UP
    view.refresh_status()
    ps.label = "someone-elses-project"
    failures: list[str] = []
    view.action_failed.connect(failures.append)

    view.restart_from_server_tab()

    assert "up" not in _compose_verbs(ps.calls)
    assert "stop" not in _compose_verbs(ps.calls)
    # Stop's own words (the ownership refusal), not a Start's, and no Start offer.
    with pytest.raises(Exception) as stop_refusal:  # noqa: PT011 - any StartRefused-like refusal
        view.services.controller.stop()
    said = str(stop_refusal.value)
    assert "someone-elses-project" in said
    assert view.problem_label.text() == said
    assert failures and said in failures[0]
    assert not view.stop_other_button.isVisibleTo(view)
    assert not view.repair_database_button.isVisibleTo(view)
    assert view.realm_badge.status not in ("stopping", "starting")


def test_a_stop_that_breaks_says_the_server_did_not_stop(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Stop half's failure is said in Stop's words, not "The server did not start"."""
    from yulon.controller import Controller
    from yulon.ui.controller_view import STOP_FAILED_BROKE

    def broke(_self: Controller) -> bool:
        raise RuntimeError("compose exploded")

    monkeypatch.setattr(Controller, "stop", broke)
    view = _view(ps, tmp_path)
    ps.names = ALL_UP
    view.refresh_status()

    view.restart_from_server_tab()

    assert view.problem_label.text() == STOP_FAILED_BROKE
    assert "compose exploded" in view.problem_details.text()
    assert "up" not in _compose_verbs(ps.calls)


def test_a_missing_database_refuses_before_the_stop_and_offers_the_repair(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T377: refused before the Stop, so the world stays up; Start's own repair offer shows."""
    from yulon import database_presence
    from yulon.controller import Controller, DatabaseMissing

    def missing(_self: Controller) -> None:
        raise DatabaseMissing(database_presence.MISSING)

    monkeypatch.setattr(Controller, "refuse_a_missing_database", missing)
    view = _view(ps, tmp_path)
    view.services.repair_database = lambda _cancel=None: iter(())
    ps.names = ALL_UP
    view.refresh_status()

    view.restart_from_server_tab()

    assert "stop" not in _compose_verbs(ps.calls), "the world was stopped before the refusal"
    assert view.problem_label.text().startswith(database_presence.MISSING)
    assert view.repair_database_button.isVisibleTo(view)
    assert view.realm_badge.status not in ("stopping", "starting")


def test_a_start_that_meets_another_server_offers_what_start_offers(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Stopped, then the ports are taken: the Start half's own offer, not the Stop's words."""
    view = _view(ps, tmp_path)
    ps.names = ALL_UP
    view.refresh_status()
    ps.ports = "tbc-realmd\t0.0.0.0:3724->3724/tcp\n"

    view.restart_from_server_tab()

    assert _compose_verbs(ps.calls)[-1:] == ["stop"]
    assert view.stop_other_button.isVisibleTo(view)


def test_restart_is_reached_by_keyboard_and_pad(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = _view(ps, tmp_path)
    assert view.restart_button.focusPolicy() & Qt.FocusPolicy.TabFocus
    assert view.restart_button in view._server_presses()


@pytest.mark.parametrize("size", [(1280, 800), (960, 640)], ids=["1280x800", "960x640"])
def test_the_realm_bar_fits_with_restart_in_it(
    qapp: object, ps: _Ps, tmp_path: Path, size: tuple[int, int]
) -> None:
    view = _view(ps, tmp_path)
    window, _tab = _controller_in_the_real_window(view, "Server")
    try:
        _at(window, size)
        bar = view.restart_button.parentWidget()
        cut = [
            why
            for b in (view.start_button, view.stop_button, view.restart_button, view.refresh_button)
            if (why := _clipped(b)) is not None
        ]
        for b in (view.start_button, view.stop_button, view.restart_button, view.refresh_button):
            if b.x() + b.width() > bar.width():
                cut.append(f"{b.text()!r} ends at {b.x() + b.width()} of {bar.width()}")
        assert cut == [], cut
        assert view.restart_button.y() == view.stop_button.y(), "Restart wrapped off Stop's row"
    finally:
        window.close()


def test_a_finished_job_hands_back_restart_s_wait_before_the_next_reading(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """Its "Wait: Stop is running." goes when the job ends, as Start's and Stop's do."""
    gate = _Gate()
    view = _view(ps, tmp_path, job_runner=gate)
    ps.names = ALL_UP
    view.refresh_status()
    gate.hold = True
    view.stop_server()
    work, done, failed = gate.queued.pop(0)
    run_inline(work, done, failed)  # the Stop ends; the reading it asks for is held
    assert reason_of(view.restart_button) != wait_for("Stop")
    gate.release()


def test_docker_not_answering_greys_restart_as_it_greys_stop(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from yulon import docker

    view = _view(ps, tmp_path)
    ps.names = ALL_UP
    view.refresh_status()
    assert view.restart_button.isEnabled()

    def unanswered() -> None:
        raise docker.DockerUnansweredError("Cannot connect to the Docker daemon")

    monkeypatch.setattr(view.services.controller, "status", unanswered)
    view.refresh_status()
    assert not view.stop_button.isEnabled()
    assert not view.restart_button.isEnabled()
    assert reason_of(view.restart_button) == reason_of(view.stop_button) != ""


def test_a_late_word_from_a_finished_restart_moves_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = _view(ps, tmp_path)
    ps.names = ALL_UP
    view.refresh_status()
    view.restart_from_server_tab()
    shown = view.realm_badge.status
    view._restart_relay.emit_line("")
    assert view.realm_badge.status == shown != "starting"
