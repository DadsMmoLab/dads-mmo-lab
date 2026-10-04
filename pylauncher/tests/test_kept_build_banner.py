"""T224, owner D3: the Server tab shows a kept build, with a "Remove kept build…" press.

The banner reads only the record (`native.parked_build_note()` through the
route's `check`), so it is asked when the tab is built, again after every
rebuild press ends, and after the press. The press asks first, runs off the GUI
thread, and asks again afterwards so the banner goes when the build does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import _Ps, _services
from yulon import runner
from yulon.catalog import native
from yulon.catalog.catalog import load_catalog
from yulon.ui import controller_view as controller_view_module
from yulon.ui.controller_view import ControllerView
from yulon.ui.widgets.job import run_inline

WOTLK = load_catalog().get("wow-wotlk")
NOTE = "A finished build from 2026-10-04 21:05 is kept for the next rebuild."


class _Route:
    """A `KeptBuildRoute` double: a note while `kept`, and the presses it was asked for."""

    def __init__(self, kept: bool = True) -> None:
        self.kept = kept
        self.checks = 0
        self.removed = 0

    def check(self) -> str | None:
        self.checks += 1
        return NOTE if self.kept else None

    def remove(self) -> str:
        self.removed += 1
        self.kept = False
        return "The kept build was removed."

    def route(self) -> native.KeptBuildRoute:
        return native.KeptBuildRoute(check=self.check, remove=self.remove)


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _view(ps: _Ps, tmp_path: Path, route: _Route | None) -> ControllerView:
    services = _services(ps, tmp_path, [])
    services.kept_build = route.route() if route is not None else None
    return ControllerView(WOTLK, services, status_poll_ms=0)


def _answer(monkeypatch: pytest.MonkeyPatch, yes: bool) -> list[str]:
    from PySide6.QtWidgets import QMessageBox

    asked: list[str] = []

    def question(parent: object, title: str, text: str, *a: object, **k: object) -> int:
        asked.append(text)
        button = QMessageBox.StandardButton.Yes if yes else QMessageBox.StandardButton.No
        return int(button.value)

    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    return asked


def test_the_server_tab_banner_shows_while_a_build_is_kept(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route(kept=True)
    view = _view(ps, tmp_path, route)
    assert route.checks == 1, "the tab did not ask when it was built"
    assert not view.kept_build_banner.isHidden()
    assert view.kept_build_banner_label.text() == NOTE
    assert view.kept_build_banner_button.text() == native.REMOVE_KEPT_BUILD_LABEL


def test_no_kept_build_and_no_route_show_no_banner(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    assert _view(ps, tmp_path, _Route(kept=False)).kept_build_banner.isHidden()
    view = _view(ps, tmp_path, None)
    assert view.kept_build_banner.isHidden()
    assert view.remove_kept_build() is False


def test_a_rebuild_that_ends_asks_again_and_the_banner_follows(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    route = _Route(kept=False)
    view = _view(ps, tmp_path, route)
    assert view.kept_build_banner.isHidden()
    route.kept = True  # the press kept its build
    view._rebuild_finished(False, "Docker did not answer")
    assert route.checks == 2
    assert not view.kept_build_banner.isHidden()
    route.kept = False  # the next one used it
    view._rebuild_finished(True, "")
    assert route.checks == 3
    assert view.kept_build_banner.isHidden()


def test_declining_the_question_removes_nothing(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _Route()
    view = _view(ps, tmp_path, route)
    asked = _answer(monkeypatch, yes=False)
    view.kept_build_banner_button.click()
    assert asked == [native.REMOVE_KEPT_BUILD_QUESTION]
    assert route.removed == 0
    assert not view.kept_build_banner.isHidden()


def test_remove_kept_build_removes_it_and_the_banner_goes(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    route = _Route()
    view = _view(ps, tmp_path, route)
    _answer(monkeypatch, yes=True)
    view.kept_build_banner_button.click()
    assert route.removed == 1
    assert route.checks == 2, "the banner was not asked again after the press"
    assert view.kept_build_banner.isHidden()
    assert "The kept build was removed." in view.problem_label.text()
