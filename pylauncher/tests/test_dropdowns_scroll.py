"""T875: long dropdowns show ten rows at most and scroll.

Without `combobox-popup: 0` in the app stylesheet, Linux/Fusion uses the
menu-like combo popup that shows EVERY item, centred on the current one, and
ignores `maxVisibleItems` -- the Logs tab's log list ran off the screen with no
way to scroll. With the rule, Qt uses the list popup that honours
`maxVisibleItems` (10 by default; the Logs tab's picker asks for 8), and the
D-pad's Down/Up reach the popup's rows through the gamepad Navigator.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QVBoxLayout, QWidget

from tests.conftest import process_events
from yulon.catalog.catalog import load_catalog
from yulon.support import bundle
from yulon.support.sources import LiveLog
from yulon.ui.gamepad import Action, Direction, install_gamepad_navigation
from yulon.ui.logs_view import LogsView
from yulon.ui.theme import THEMES, apply_dadcraft_theme
from yulon.ui.widgets.job import run_inline

CATALOG = load_catalog()


def _window_with_combo(count: int = 40) -> tuple[QWidget, QComboBox]:
    win = QWidget()
    box = QComboBox(win)
    for index in range(count):
        box.addItem(f"log {index}")
    layout = QVBoxLayout(win)
    layout.addWidget(box)
    return win, box


def _logs_view() -> LogsView:
    def no_docker(install: object, silent: object) -> list[LiveLog]:
        return [LiveLog("w", "up\n")]

    return LogsView(
        lambda: [],
        CATALOG,
        jobs=run_inline,
        bundle_seams=bundle.Seams(live_logs=no_docker, docker_version=lambda distro: None),
    )


@pytest.mark.parametrize("theme_id", list(THEMES))
def test_every_theme_stylesheet_uses_the_list_popup(qapp: object, theme_id: str) -> None:
    """Every theme's applied sheet carries `combobox-popup: 0` (T875)."""
    host = QWidget()
    try:
        apply_dadcraft_theme(host, width=1280, theme_name=theme_id)
        assert "combobox-popup: 0" in host.styleSheet(), theme_id
    finally:
        host.deleteLater()
        process_events()


def test_open_dropdown_shows_ten_rows_at_most_and_scrolls(qapp: object) -> None:
    """A 40-item combo under the app sheet: visible rows <= 10, scrollbar live (T875)."""
    win, box = _window_with_combo()
    try:
        apply_dadcraft_theme(win, width=1280, theme_name="arthas")
        win.show()
        process_events()
        box.showPopup()
        process_events()
        try:
            view = box.view()
            assert box.count() == 40
            assert box.maxVisibleItems() <= 10
            assert view.verticalScrollBar().maximum() > 0
            visible = sum(
                1
                for row in range(view.model().rowCount())
                if view.visualRect(view.model().index(row, 0)).intersects(view.viewport().rect())
            )
            assert visible <= box.maxVisibleItems()
        finally:
            box.hidePopup()
            process_events()
    finally:
        win.close()
        win.deleteLater()
        process_events()


def test_logs_source_picker_shows_eight_rows(qapp: object) -> None:
    """The Logs tab's picker fits a Steam Deck 800 px screen with room (T875)."""
    view = _logs_view()
    try:
        assert view.source_picker.maxVisibleItems() == 8
    finally:
        view.deleteLater()
        process_events()


def test_pad_down_past_the_visible_rows_moves_and_scrolls(qapp: object) -> None:
    """15 pad Downs through the Navigator: index 15, and the row shows (T875)."""
    win, box = _window_with_combo()
    navigator, keyboard, gamepad = install_gamepad_navigation(win)
    try:
        apply_dadcraft_theme(win, width=1280, theme_name="arthas")
        win.show()
        win.activateWindow()
        process_events()
        box.setFocus()
        process_events()
        assert navigator.perform(Action.CONFIRM) is True
        process_events()
        assert QApplication.activePopupWidget() is not None
        box.setCurrentIndex(0)
        process_events()
        view = box.view()
        for _ in range(15):
            assert navigator.navigate(Direction.DOWN) is True
            process_events()
        try:
            assert box.currentIndex() == 15
            assert view.currentIndex().row() == 15
            assert view.verticalScrollBar().value() > 0
            row = view.visualRect(view.model().index(15, 0))
            assert row.intersects(view.viewport().rect())
        finally:
            box.hidePopup()
            process_events()
    finally:
        keyboard.stop()
        gamepad.stop()
        win.close()
        win.deleteLater()
        process_events()


def test_pad_keys_down_past_the_visible_rows_moves_and_scrolls(qapp: object) -> None:
    """15 pad Downs as key presses: index 15, and the row shows (T875)."""
    win, box = _window_with_combo()
    _navigator, keyboard, gamepad = install_gamepad_navigation(win)
    try:
        apply_dadcraft_theme(win, width=1280, theme_name="arthas")
        win.show()
        win.activateWindow()
        process_events()
        box.showPopup()
        process_events()
        box.setCurrentIndex(0)
        process_events()
        view = box.view()
        try:
            for _ in range(15):
                QTest.keyClick(win.windowHandle(), Qt.Key.Key_Down)
                process_events()
            assert box.currentIndex() == 15
            assert view.currentIndex().row() == 15
            row = view.visualRect(view.model().index(15, 0))
            assert row.intersects(view.viewport().rect())
        finally:
            box.hidePopup()
            process_events()
    finally:
        keyboard.stop()
        gamepad.stop()
        win.close()
        win.deleteLater()
        process_events()
