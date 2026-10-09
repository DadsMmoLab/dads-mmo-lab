"""T621: the Modules tab's background update refresh, at the tab.

It borrows nothing from the Check for updates press except the chips it fills, and it shows
nothing of its own: the module report is not written, no button changes, no new label.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from tests.test_controller_view import (
    TORTOISE,
    WOTLK,
    _FakeClientDir,
    _Ps,
    _services,
    _tortoise_services,
)
from yulon import apply as apply_module
from yulon import runner
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerServices, ControllerView
from yulon.ui.widgets.job import run_inline


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    """The view's own jobs run on the spot, as `test_controller_view` runs them."""
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


class _Deferred:
    """A job runner that keeps each job until the test lets it run: a job 'in flight'.

    The tab starts jobs of its own when it is built, so the refresh's are told apart by the
    slot that takes their answer.
    """

    def __init__(self) -> None:
        self.jobs: list[tuple[Any, Any, Any]] = []

    def __call__(self, work: Any, on_done: Any, on_error: Any) -> None:
        if getattr(on_done, "__name__", "") == "_refresh_done":
            self.jobs.append((work, on_done, on_error))
        else:
            work_then_deliver(work, on_done, on_error)

    def finish(self) -> None:
        work_then_deliver(*self.jobs.pop(0))


def work_then_deliver(work: Any, on_done: Any, on_error: Any) -> None:
    run_inline(work, on_done, on_error)


def _row(key: str, behind: Any, family: str = "mod") -> apply_module.ModuleUpdate:
    return apply_module.ModuleUpdate(
        key=key, path=Path(key), is_checkout=True, behind=behind, family=family
    )


class _Route:
    def __init__(self, rows: tuple[apply_module.ModuleUpdate, ...] = ()) -> None:
        self.rows = rows
        self.calls: list[threading.Event] = []
        self.error: Exception | None = None

    def __call__(self, cancel: threading.Event) -> tuple[apply_module.ModuleUpdate, ...]:
        self.calls.append(cancel)
        if self.error is not None:
            raise self.error
        return self.rows


def _view(
    ps: _Ps, tmp_path: Path, route: _Route | None, *, deferred: bool = False
) -> tuple[ControllerView, _Deferred | None]:
    services = _services(ps, tmp_path, [])
    services.module_refresh = route
    runner = _Deferred() if deferred else None
    view = ControllerView(TORTOISE, services, status_poll_ms=0, job_runner=runner)
    return view, runner


def test_the_refresh_fills_the_update_chips_and_writes_nothing_in_the_report(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route((_row("tortoise-gm-manager", 3), _row("tortoise-bots-manager", 0)))
    view, _ = _view(ps, tmp_path, route)
    before = view.module_report.toPlainText()
    view.refresh_updates()
    assert len(route.calls) == 1
    assert view._behind == {("mod", "tortoise-gm-manager"): 3}
    assert view.module_report.toPlainText() == before
    assert not view._module_job_running() and not view._busy


def test_a_game_with_no_refresh_route_does_nothing(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view, runner = _view(ps, tmp_path, None, deferred=True)
    view.refresh_updates()
    assert runner is not None and runner.jobs == []


@pytest.mark.parametrize(
    "block",
    ["_busy", "_module_pending", "_play_pending"],
)
def test_the_refresh_does_not_start_while_a_server_job_a_module_job_or_play_is_on(
    qapp: object, ps: _Ps, tmp_path: Path, block: str
) -> None:
    route = _Route((_row("tortoise-gm-manager", 3),))
    view, runner = _view(ps, tmp_path, route, deferred=True)
    setattr(view, block, True if block != "_module_pending" else "install x")
    view.refresh_updates()
    assert runner is not None and runner.jobs == [] and route.calls == []
    setattr(view, block, False if block != "_module_pending" else None)
    view.refresh_updates()
    assert len(runner.jobs) == 1, "the block was lifted and the refresh still did not start"


def test_a_running_refresh_is_the_only_one(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    route = _Route()
    view, runner = _view(ps, tmp_path, route, deferred=True)
    view.refresh_updates()
    view.refresh_updates()
    assert runner is not None and len(runner.jobs) == 1
    runner.finish()
    view.refresh_updates()
    assert len(runner.jobs) == 1, "a finished refresh must allow the next one"


def test_the_refresh_never_runs_on_the_gui_thread(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    route = _Route()
    view, runner = _view(ps, tmp_path, route, deferred=True)
    view.refresh_updates()
    assert runner is not None and route.calls == [], "the refresh work ran where it was asked"
    runner.finish()
    assert len(route.calls) == 1


def test_a_refresh_that_fails_or_finds_nothing_leaves_the_old_counts(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route((_row("tortoise-gm-manager", 3),))
    view, _ = _view(ps, tmp_path, route)
    view.refresh_updates()
    assert view._behind == {("mod", "tortoise-gm-manager"): 3}
    route.rows = ()
    view.refresh_updates()
    assert view._behind == {("mod", "tortoise-gm-manager"): 3}, "an empty answer cleared the chips"
    route.error = OSError("network is unreachable")
    before = view.module_report.toPlainText()
    view.refresh_updates()
    assert view._behind == {("mod", "tortoise-gm-manager"): 3}
    assert view.module_report.toPlainText() == before, "a background failure was shown"
    assert not view._refresh_running, "a failed refresh left the next one blocked"


def test_a_press_or_a_server_job_that_starts_cancels_the_refresh_and_its_answer_is_dropped(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route((_row("tortoise-gm-manager", 3),))
    view, runner = _view(ps, tmp_path, route, deferred=True)
    view.refresh_updates()
    assert runner is not None
    view._set_busy(True, "Start")
    runner.finish()
    assert route.calls[0].is_set(), "a server job started and the refresh was left running"
    assert view._behind == {}, "an answer that was cancelled still filled the chips"
    assert not view._refresh_running


def test_a_module_job_that_starts_cancels_the_refresh(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route()
    view, runner = _view(ps, tmp_path, route, deferred=True)
    view.refresh_updates()
    view._run_module_job(lambda: None, lambda _r: None, lambda _e: None)
    assert route.calls == [] and view._refresh_cancel.is_set()


def test_play_cancels_a_running_refresh(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    route = _Route()
    view, runner = _view(ps, tmp_path, route, deferred=True)
    view.services.set_play_client_dir = _FakeClientDir()
    view.refresh_updates()
    view.play()
    assert view._refresh_cancel.is_set()


def test_closing_the_tab_cancels_the_refresh_before_it_joins_the_jobs(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    seen_at_join: list[bool] = []
    route = _Route()
    view, _ = _view(ps, tmp_path, route, deferred=True)
    view.refresh_updates()
    view._jobs.wait = lambda *_a: seen_at_join.append(view._refresh_cancel.is_set())  # type: ignore[attr-defined]
    view.shutdown()
    assert seen_at_join == [True], "the join waited on a fetch that nobody had told to stop"


def test_a_refresh_answering_after_the_tab_closed_touches_nothing(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route((_row("tortoise-gm-manager", 3),))
    view, runner = _view(ps, tmp_path, route, deferred=True)
    view.refresh_updates()
    assert runner is not None
    view.shutdown()
    runner.finish()
    assert view._behind == {}


def test_a_tab_opened_at_start_up_refreshes_a_little_later_and_not_on_the_spot(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route()
    view, _ = _view(ps, tmp_path, route)
    view.refresh_updates_later(0)
    assert route.calls == [], "never on the spot: the tab is still being built"
    for _ in range(200):
        QApplication.processEvents()
        if route.calls:
            break
    assert len(route.calls) == 1


def test_a_closed_tab_does_not_come_back_for_its_refresh(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route()
    view, _ = _view(ps, tmp_path, route)
    view.refresh_updates_later(0)
    view.shutdown()
    for _ in range(50):
        QApplication.processEvents()
    assert route.calls == []


def test_a_long_session_asks_again_by_itself(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """The refresh re-arms itself, and the cache (not the timer) is what makes it once a day."""
    route = _Route()
    view, _ = _view(ps, tmp_path, route)
    view.refresh_updates_later(0)
    view._refresh_again_ms = 0
    for _ in range(400):
        QApplication.processEvents()
        if len(route.calls) >= 2:
            break
    view.shutdown()
    assert len(route.calls) >= 2


# --------------------------------------------------------------------------- the wiring


def test_tortoise_and_wotlk_get_a_refresh_route_and_the_other_games_none(tmp_path: Path) -> None:
    from tests.test_controller_view import TBC

    assert _tortoise_services(tmp_path / "t", None).module_refresh is not None
    assert ControllerServices.for_entry(WOTLK, tmp_path / "w").module_refresh is not None
    assert ControllerServices.for_entry(TBC, tmp_path / "b").module_refresh is None


def test_main_asks_each_opened_tab_for_its_refresh() -> None:
    source = (Path(__file__).parent.parent / "main.py").read_text(encoding="utf-8")
    assert "refresh_updates_later()" in source


def test_play_preparation_never_reaches_the_refresh(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    """Play is the path T612 made network-free; nothing under it may call the refresh route."""
    source = (Path(__file__).parent.parent / "yulon" / "ui" / "controller_view.py").read_text(
        encoding="utf-8"
    )
    prepare = source.split("def _prepare_client", 1)[1].split("\n    def ", 1)[0]
    assert "module_refresh" not in prepare and "refresh_updates" not in prepare
