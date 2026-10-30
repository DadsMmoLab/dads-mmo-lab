"""T692a: the Server version section lives on Maintenance, not in a Modules menu (RED)."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_controller_view import (
    T191_SIZES,
    WOTLK,
    _at,
    _clipped,
    _controller_in_the_real_window,
    _drawn_under_their_minimum,
    _latest,
    _past_the_pin,
    _Ps,
    _ran_a_job,
    _services,
)
from yulon import runner, server_build_presses
from yulon.server_build_presses import MAINTENANCE_TAB
from yulon.ui.controller_view import ControllerView, _drawn_title


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> _Ps:
    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


@pytest.fixture(autouse=True)
def _inline_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    from yulon.ui import controller_view as controller_view_module
    from yulon.ui.widgets.job import run_inline

    monkeypatch.setattr(controller_view_module, "threaded_job_runner", lambda _parent: run_inline)


def test_the_server_version_section_is_on_maintenance_and_modules_has_no_server_build(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view, _spy, _rebuilt = _past_the_pin(ps, tmp_path)
    assert not hasattr(view, "server_build_button"), "Modules still builds the menu"
    assert not hasattr(view, "server_build_menu"), "Modules still holds the menu"
    assert hasattr(view, "server_version_section"), "no Server version section"
    assert view.server_version_section.isAncestorOf(view.rebuild_button)
    upkeep = view._tabs.widget(
        next(
            i
            for i in range(view._tabs.count())
            if view._tabs.widget(i) is not None
            and view._tabs.widget(i).isAncestorOf(view.server_version_section)
        )
    )
    assert upkeep is not None
    assert view._tabs.tabText(view._tabs.indexOf(upkeep)) == _drawn_title(MAINTENANCE_TAB)


def test_a_hidden_action_hides_its_button(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert view.services.update_to_latest is None
    assert not view.update_to_latest_action.isVisible()
    assert view.update_to_latest_button.isHidden(), "route-less press still shown"


def test_on_the_pins_the_section_says_the_tested_pin(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    services, spy = _latest(ps, tmp_path)
    spy.revs = ()
    view = ControllerView(WOTLK, services, status_poll_ms=0)
    assert view.source_version_label.isHidden() is False, "on the pins the line is gone"
    assert "tested pin" in view.source_version_label.text(), view.source_version_label.text()


def test_put_the_folders_back_offered_opens_advanced(qapp: object, ps: _Ps, tmp_path: Path) -> None:
    from yulon.catalog import native

    view, _spy, _rebuilt = _past_the_pin(ps, tmp_path)
    view.services.put_the_folders_back = native.PutFoldersBackRoute(
        offered=lambda: True,
        confirmation=lambda: "put them back?",
        press=lambda cancel=None: iter([]),
    )
    view._refresh_put_the_folders_back()
    assert view.advanced_fold.isChecked(), "Advanced stayed folded while offered"
    assert not view.put_the_folders_back_button.isHidden()


def _on_modules_with_the_banner_up(view: ControllerView) -> None:
    view._rebuild_owed.add(("module", "mod-solocraft"))
    view._refresh_rebuild_banner()
    view._show_page_of(view.modules_panel)
    current = view._tabs.tabText(view._tabs.currentIndex())
    assert current == "Modules", current


def _answer_yes_no(monkeypatch: pytest.MonkeyPatch, yes: bool) -> list[object]:
    from yulon.ui import controller_view as controller_view_module

    asked: list[object] = []
    button = controller_view_module.QMessageBox.StandardButton

    def question(*a: object, **k: object) -> object:
        asked.append(a)
        return int(button.Yes if yes else button.No)

    monkeypatch.setattr(controller_view_module.QMessageBox, "question", question)
    return asked


def test_the_banner_rebuild_brings_maintenance_up(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, _spy, _rebuilt = _past_the_pin(ps, tmp_path)
    _on_modules_with_the_banner_up(view)
    asked = _answer_yes_no(monkeypatch, yes=True)
    # The panel's own signal, not `running`: the fake rebuild is one line long
    # and may already be over when the click returns.
    started: list[object] = []
    view.rebuild_log.run_started.connect(lambda *a: started.append(a))
    view.rebuild_banner_button.click()
    assert asked, "the banner's press skipped the rebuild question"
    assert started, "the rebuild did not start"
    assert view._tabs.tabText(view._tabs.currentIndex()) == _drawn_title(MAINTENANCE_TAB)


def test_a_declined_banner_rebuild_stays_on_modules(
    qapp: object, ps: _Ps, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    view, _spy, rebuilt = _past_the_pin(ps, tmp_path)
    _on_modules_with_the_banner_up(view)
    asked = _answer_yes_no(monkeypatch, yes=False)
    view.rebuild_banner_button.click()
    assert asked and not rebuilt
    assert view._tabs.tabText(view._tabs.currentIndex()) == "Modules"


def test_under_server_build_names_the_maintenance_tab() -> None:
    said = server_build_presses.under_server_build(server_build_presses.REBUILD)
    assert said == (
        "“Rebuild the server…” under “Server version” on the Backups & updates tab"
    ), said


def test_the_module_updates_button_checks_modules_not_the_server(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    assert (
        view.module_updates_button.text() == "Check module updates"
    ), view.module_updates_button.text()


def test_the_server_version_section_reads_whole_at_every_t191_window(
    qapp: object, ps: _Ps, tmp_path: Path
) -> None:
    """960x640 up: every press in the section is drawn whole, Advanced open, a job's log up.

    The section is the most the Maintenance tab gained, and its widest state is
    this one: past the pins (Return to the tested pin… shown), Advanced open, a
    rebuild's output in the log. A button cut through its label, or a widget the
    tab drew under its own minimum, is the defect the Modules bar had at 960
    before it wrapped (T83).
    """
    view, _spy, _rebuilt = _past_the_pin(ps, tmp_path)
    window, tab = _controller_in_the_real_window(view, MAINTENANCE_TAB)
    view.advanced_fold.setChecked(True)
    _ran_a_job(view)
    presses = (
        view.rebuild_button,
        view.update_to_latest_button,
        view.return_to_pin_button,
        view.adopt_button,
    )
    for size in T191_SIZES:
        _at(window, size)
        shown = [b for b in presses if b.isVisible()]
        assert len(shown) >= 3, f"at {size} only {[b.text() for b in shown]} are drawn"
        cut = [why for why in (_clipped(b) for b in shown) if why]
        assert cut == [], f"a Server version press is cut at {size}: {cut}"
        assert _drawn_under_their_minimum(tab) == [], (
            f"something on Maintenance is drawn under its minimum at {size}: "
            f"{_drawn_under_their_minimum(tab)}"
        )
        log = view.rebuild_log
        assert (
            log.isVisible() and log.height() >= log.minimumSizeHint().height()
        ), f"the rebuild log is cut at {size}: {log.height()} < {log.minimumSizeHint().height()}"
