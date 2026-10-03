"""The sidebar rail and its pinned Catalog and Logs (T192).

The rail is a `QTabBar` that keeps Catalog and Logs as tabs 0 and 1 -- so
`indexOf`, the gamepad bumpers and `setCurrentIndex(0)` work as they did -- but
hides them, and `SidebarPins` draws the two above the scrolling server tabs.
These tests build a bare rail; the window-level half is in `test_main.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLabel, QMainWindow, QTabWidget

from tests.conftest import process_events
from yulon.ui import theme
from yulon.ui.sidebar import RAIL_MIN_WIDTH, SidebarRail


@pytest.fixture
def rail(qapp: object) -> Iterator[tuple[QMainWindow, QTabWidget]]:
    """A themed window holding a sidebar `QTabWidget` built on `SidebarRail`."""
    window = QMainWindow()
    tabs = QTabWidget()
    tabs.setObjectName("sidebar-tabs")
    tabs.setTabBar(SidebarRail(tabs))
    tabs.setTabPosition(QTabWidget.TabPosition.West)
    tabs.setUsesScrollButtons(True)
    window.setCentralWidget(tabs)
    theme.apply_dadcraft_theme(window, width=1280)
    window.resize(960, 640)
    yield window, tabs
    window.close()
    window.deleteLater()
    process_events(10)


def _pinned_pair(tabs: QTabWidget) -> None:
    """Catalog and Logs as tabs 0 and 1, hidden from the bar, Catalog current."""
    for title in ("Catalog", "Logs"):
        tabs.addTab(QLabel(title), title)
    tabs.tabBar().setTabVisible(0, False)
    tabs.tabBar().setTabVisible(1, False)
    tabs.setCurrentIndex(0)


def test_a_rail_with_only_hidden_tabs_keeps_its_width(rail) -> None:
    """With every tab hidden a plain `QTabBar` asks for 0x0, and the page slides under the pins.

    Mutation: drop the width floor from `SidebarRail`'s hints and the page
    starts at x 0.
    """
    window, tabs = rail
    _pinned_pair(tabs)
    window.show()
    process_events(30)

    page = tabs.currentWidget()
    assert page.mapTo(tabs, page.rect().topLeft()).x() >= RAIL_MIN_WIDTH
    assert tabs.tabBar().width() >= RAIL_MIN_WIDTH


def test_a_hidden_current_tab_is_not_drawn_over_a_visible_one(rail) -> None:
    """QTabBar paints the current tab last without asking whether it is visible.

    With Catalog current and hidden, a plain bar drew a gold-rimmed "Catalog"
    box over the first server tab. Nothing in the bar is selected then, so no
    pixel of it is the selected tab's gold.
    """
    window, tabs = rail
    _pinned_pair(tabs)
    for title in ("WotLK", "TBC"):
        tabs.addTab(QLabel(title), title)
    tabs.setCurrentIndex(0)
    window.show()
    process_events(30)

    image = tabs.tabBar().grab().toImage()
    gold = QColor(theme.COLOR_GOLD_BRIGHT).name()
    lit = sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if image.pixelColor(x, y).name() == gold
    )
    assert lit == 0, f"{lit} gold pixels: a hidden tab is drawn as the selected one"

    tabs.setCurrentIndex(2)
    process_events(30)
    image = tabs.tabBar().grab().toImage()
    assert any(
        image.pixelColor(x, y).name() == gold
        for y in range(image.height())
        for x in range(image.width())
    ), "the probe is blind: a visible current tab shows no gold either"


def test_closing_the_last_server_tab_shows_the_catalog_not_nothing(rail) -> None:
    """With Catalog and Logs hidden there is no visible neighbour to fall back on.

    `QTabBar` then answers -1 and the window shows an empty pane; the rail goes
    to the Catalog instead.
    """
    window, tabs = rail
    _pinned_pair(tabs)
    tabs.addTab(QLabel("WotLK"), "WotLK")
    tabs.setCurrentIndex(2)
    window.show()
    process_events(30)

    tabs.removeTab(2)

    assert tabs.currentIndex() == 0
    assert tabs.currentWidget().isVisible()


def test_a_tab_widget_inside_the_rail_keeps_its_own_tab_width(rail) -> None:
    """The rail's tab rules caught every tab BELOW the rail, not only its own (T189).

    `QTabWidget#sidebar-tabs QTabBar::tab` is a descendant selector, so a server
    page's own sub-tabs -- a QTabWidget inside a rail page -- were capped at the
    rail's 64px too. The rail's rules are child selectors (`> QTabBar::tab`).

    Mutation: put the space back for the `>` and the nested tab is 64px wide.
    """
    window, tabs = rail
    page = QTabWidget()
    page.addTab(QLabel("one"), "Characters and their gear")
    tabs.addTab(page, "WotLK")
    window.show()
    process_events(30)

    nested = page.tabBar()
    words = nested.fontMetrics().horizontalAdvance("Characters and their gear")
    assert nested.tabRect(0).width() > words, (nested.tabRect(0), words)
    assert nested.tabRect(0).width() > RAIL_MIN_WIDTH
