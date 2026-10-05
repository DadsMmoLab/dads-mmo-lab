"""The sidebar rail and its pinned Catalog and Logs (T192).

The rail is a `QTabBar` that keeps Catalog and Logs as tabs 0 and 1 -- so
`indexOf`, the gamepad bumpers and `setCurrentIndex(0)` work as they did -- but
hides them, and `SidebarPins` draws the two above the scrolling server tabs.
These tests build a bare rail; the window-level half is in `test_main.py`.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from PySide6.QtCore import Qt
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


def _pinned_rail_with_servers(tabs: QTabWidget):  # noqa: ANN202  (a SidebarPins)
    """Catalog and Logs pinned, two servers after them, the last server current."""
    from PySide6.QtGui import QIcon

    from yulon.ui.sidebar import SidebarPins

    _pinned_pair(tabs)
    for title in ("WotLK", "TBC"):
        tabs.addTab(QLabel(title), title)
    pins = SidebarPins(tabs, [(0, QIcon(), "Catalog"), (1, QIcon(), "Logs")])
    tabs.setCurrentIndex(3)
    return pins


def test_ctrl_tab_walks_through_the_catalog_and_logs_like_the_bumpers(rail) -> None:
    """Qt's own Ctrl+Tab skips hidden tabs, so it never reached the Catalog or Logs (T192 review).

    LB/RB visit tabs 0..n in a ring (`gamepad.py` `_cycle`); Ctrl+Tab and
    Ctrl+Shift+Tab now walk the same ring, and the pins follow.
    """
    from PySide6.QtTest import QTest

    window, tabs = rail
    pins = _pinned_rail_with_servers(tabs)
    window.show()
    process_events(20)

    def press(key: Qt.Key, modifiers: Qt.KeyboardModifier) -> int:
        QTest.keyClick(tabs.currentWidget(), key, modifiers)
        process_events(5)
        return tabs.currentIndex()

    ctrl = Qt.KeyboardModifier.ControlModifier
    back = ctrl | Qt.KeyboardModifier.ShiftModifier
    assert press(Qt.Key.Key_Tab, ctrl) == 0
    assert pins.buttons[0].isChecked() and not pins.buttons[1].isChecked()
    assert press(Qt.Key.Key_Tab, ctrl) == 1
    assert pins.buttons[1].isChecked() and not pins.buttons[0].isChecked()
    assert press(Qt.Key.Key_Tab, ctrl) == 2
    assert not any(button.isChecked() for button in pins.buttons.values())

    assert press(Qt.Key.Key_Backtab, back) == 1
    assert pins.buttons[1].isChecked()
    assert press(Qt.Key.Key_Backtab, back) == 0
    assert pins.buttons[0].isChecked()
    assert press(Qt.Key.Key_Backtab, back) == 3, "Ctrl+Shift+Tab from the Catalog wraps"


def test_a_restyle_that_grows_the_pins_pushes_the_rail_down_with_them(rail) -> None:
    """The bar's offset is the pins' own height, re-read on a restyle, not a number in the theme.

    The theme's pins are the same height at all three window sizes, so this
    restyle makes them taller on purpose: the first server tab must still
    start below the Logs pin.

    Mutation: drop `SidebarPins.changeEvent` and the bar stays where the
    smaller pins put it, under the taller ones.
    """
    window, tabs = rail
    pins = _pinned_rail_with_servers(tabs)
    window.show()
    process_events(20)
    before = pins.sizeHint().height()

    window.setStyleSheet(
        window.styleSheet()
        + f"\nQToolButton#{theme.SIDEBAR_PIN_BUTTON} {{ padding: 30px 4px; font-size: 24px; }}"
    )
    process_events(30)

    assert pins.sizeHint().height() > before + 40, (before, pins.sizeHint())
    bar = tabs.tabBar()
    assert pins.geometry().bottom() < bar.geometry().top(), (pins.geometry(), bar.geometry())
    assert pins.height() >= pins.sizeHint().height(), (pins.size(), pins.sizeHint())


# -- T192: each server tab carries a status dot that follows its realm badge -------

ICON = 18
"""The rail's icon size (`main.py` `tabs.setIconSize`). The server glyph is drawn
at 16px, and the icon gives out the size it has, so the dot's geometry is asked
for at the image's own width."""


def _icon_image(status: str):  # noqa: ANN202  (a QImage)
    from yulon.ui.sidebar import server_tab_icon

    return server_tab_icon(status).pixmap(ICON, ICON).toImage()


def _close(colour: QColor, hex_colour: str, tolerance: int = 24) -> bool:
    want = QColor(hex_colour)
    return colour.alpha() > 200 and all(
        abs(a - b) <= tolerance
        for a, b in zip(
            (colour.red(), colour.green(), colour.blue()),
            (want.red(), want.green(), want.blue()),
            strict=True,
        )
    )


def _dot_pixels(image, hex_colour: str) -> int:  # noqa: ANN001  (a QImage)
    from yulon.ui.sidebar import status_dot_geometry

    centre, _radius, halo = status_dot_geometry(image.width())
    return sum(
        1
        for y in range(image.height())
        for x in range(image.width())
        if (x + 0.5 - centre.x()) ** 2 + (y + 0.5 - centre.y()) ** 2 <= halo**2
        and _close(image.pixelColor(x, y), hex_colour)
    )


@pytest.mark.parametrize(
    ("status", "colour"),
    [
        ("running", theme.COLOR_UNCOMMON),
        ("starting", theme.COLOR_GOLD_BRIGHT),
        ("stopping", theme.COLOR_GOLD_BRIGHT),
        ("partial", theme.COLOR_GOLD_BRIGHT),
        ("restarting", theme.COLOR_RARE),
    ],
)
def test_a_realm_that_is_up_or_on_its_way_shows_a_filled_dot_of_its_colour(
    qapp: object, status: str, colour: str
) -> None:
    """The badge's own colour, filled, in a clear ring that keeps it off the server glyph.

    The clear ring is what makes it a dot: the glyph is the same gold as
    "in between", so a centre pixel alone could be the glyph.
    """
    from yulon.ui.sidebar import status_dot_geometry

    image = _icon_image(status)
    centre, radius, halo = status_dot_geometry(image.width())
    middle = image.pixelColor(int(centre.x()), int(centre.y()))
    assert _close(middle, colour), (status, middle.name(), middle.alpha())
    assert _dot_pixels(image, colour) > 20, _dot_pixels(image, colour)
    gap = image.pixelColor(int(centre.x()), int(centre.y() - (radius + halo) / 2))
    assert gap.alpha() < 64, ("no clear ring around the dot", gap.name(), gap.alpha())


def test_a_stopped_realm_shows_a_hollow_muted_ring(qapp: object) -> None:
    from yulon.ui.sidebar import status_dot_geometry

    image = _icon_image("stopped")
    centre, _radius, _halo = status_dot_geometry(image.width())
    middle = image.pixelColor(int(centre.x()), int(centre.y()))
    assert middle.alpha() < 64, ("the ring is filled", middle.name(), middle.alpha())
    assert _dot_pixels(image, theme.COLOR_TEXT_MUTED) >= 8, _dot_pixels(
        image, theme.COLOR_TEXT_MUTED
    )
    assert _dot_pixels(image, theme.COLOR_UNCOMMON) == 0


def test_an_unknown_realm_shows_no_dot_at_all(qapp: object) -> None:
    """T188: Docker did not answer, so nothing is known -- not even "offline".

    A ring would say stopped. The tab carries the plain server icon, pixel for pixel.

    Mutation: draw "unknown" as the stopped ring and the icon differs.
    """
    from yulon.ui.icons import get_tab_icon

    plain = get_tab_icon("server").pixmap(ICON, ICON).toImage()
    unknown = _icon_image("unknown")
    assert unknown.size() == plain.size()
    assert unknown.convertToFormat(plain.format()) == plain, "an unknown realm draws a dot"
    assert _icon_image("stopped").convertToFormat(plain.format()) != plain, "the probe is blind"
