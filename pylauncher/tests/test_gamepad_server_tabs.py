"""T874: the D-pad reaches a server's sub-tabs and Left/Right walk them.

Reproduction-first contract for the fix. The real launcher window is built
offscreen (the same `_controller_in_the_real_window` harness
`test_controller_view.py` uses): focus the first button on the Server
sub-tab page, press UP until focus stops changing, and the sub-tab bar
(`server-sub-tabs`) must be focused on the way, before anything of the top bar.

Decision coverage (the lead's five):
1. Focus on the sub-tab bar: LEFT/RIGHT change its current tab (focus stays).
2. UP from inside a page goes to the bar first (the bar is the first stop above
   its own page); a further UP then goes on as today.
3. A focused QTabBar is visibly marked in every theme.
4. Nothing server-specific in gamepad.py (Qt classes only) — pinned by the
   left rail test below.
5. CHANGELOG line (checked by style tests elsewhere).
"""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from tests.conftest import process_events
from tests.test_controller_view import (
    WOTLK,
    _controller_in_the_real_window,
    _pad_press,
    _Ps,
    _services,
)
from yulon.ui.controller_view import ControllerView


@pytest.fixture
def ps(monkeypatch: pytest.MonkeyPatch) -> Any:
    """`test_controller_view`'s Docker-free `runner.run`."""
    from yulon import runner

    fake = _Ps()
    monkeypatch.setattr(runner, "run", fake)
    return fake


def _top_buttons(window: Any) -> list[QWidget]:
    """The focusable buttons of the window's top chrome (menu bar, pins)."""
    from PySide6.QtWidgets import QAbstractButton

    from yulon.ui.sidebar import TOP_MENU_BAR

    menu = window.findChild(QWidget, TOP_MENU_BAR)
    assert menu is not None, "the real window has no top menu"
    return [b for b in menu.findChildren(QAbstractButton) if b.isVisibleTo(window)]


def _first_page_button(view: ControllerView, window: Any) -> QWidget:
    from yulon.ui.gamepad import _iter_focusable

    page = view._tabs.currentWidget()
    stops = [w for w in _iter_focusable(window) if page.isAncestorOf(w)]
    assert stops, "the Server page has no focusable widget"
    return stops[0]


def test_up_from_a_wide_page_button_reaches_the_narrow_centred_bar(qapp: object) -> None:
    """Review round: UP always stops at a page's own tab row, wherever the focus stands.

    A full-width top row whose LEFT button sits above-left, a QTabWidget with a
    NARROW centred bar (the sub-tabs sheet), a wide button at the page's left
    edge: UP from that button's geometric target is the top-left button, above
    and outside the tab widget, so the ruling's rule sends the press to the bar
    instead. No x-overlap test: neither the focus nor the target stands under
    the bar.
    """
    from PySide6.QtWidgets import QApplication, QPushButton, QTabWidget

    from yulon.ui.controller_view import SUB_TABS_NAME
    from yulon.ui.gamepad import Direction, install_gamepad_navigation

    win = QWidget()
    win.resize(800, 500)
    top_left = QPushButton("TopLeft", win)
    top_left.setGeometry(0, 8, 140, 70)
    top_mid = QPushButton("TopMid", win)
    top_mid.setGeometry(330, 8, 140, 70)
    top_right = QPushButton("TopRight", win)
    top_right.setGeometry(660, 8, 140, 70)
    tabs = QTabWidget(win)
    tabs.setGeometry(0, 60, 800, 400)
    tabs.setObjectName(SUB_TABS_NAME)
    tabs.setStyleSheet(
        f"QTabWidget#{SUB_TABS_NAME}::tab-bar {{ alignment: center; }} "
        f"QTabWidget#{SUB_TABS_NAME} > QTabBar::tab {{ max-width: 16777215px; }}"
    )
    tabs.tabBar().setExpanding(False)
    page = QWidget()
    wide = QPushButton("Wide", page)
    wide.setGeometry(0, 8, 300, 40)
    tabs.addTab(page, "One")
    tabs.addTab(QWidget(), "Two")
    nav, keyboard, gamepad = install_gamepad_navigation(win)
    win.show()
    win.activateWindow()
    process_events()
    try:
        bar = tabs.tabBar()
        assert bar.isVisibleTo(win), "the bar is not a visible stop in the hand-built layout"
        wide.setFocus()
        process_events()
        nav.navigate(Direction.UP)
        process_events()
        assert QApplication.focusWidget() is bar, "UP from the wide button missed the bar"
    finally:
        keyboard.stop()
        gamepad.stop()
        win.close()
        win.deleteLater()
        process_events()


def test_up_from_the_server_page_reaches_the_sub_tab_bar_before_the_top_menu(
    qapp: object, ps: Any, tmp_path: Any
) -> None:
    """Step 1 reproduction: UP from the Server page must stop at the sub-tab bar first."""
    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Server")
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    process_events()
    from PySide6.QtWidgets import QApplication

    from yulon.ui.controller_view import SUB_TABS_NAME

    bar = window.findChild(QWidget, SUB_TABS_NAME)
    assert bar is not None
    bar = view._tabs.tabBar()
    top = set(_top_buttons(window))
    try:
        start = _first_page_button(view, window)
        start.setFocus()
        process_events()
        seen: list[Any] = []
        for _press in range(10):
            landed = _pad_press(window, Qt.Key.Key_Up)
            if landed in seen[-1:] and seen:
                break
            seen.append(landed)
            if landed is bar:
                break
        assert bar in seen, "UP from the Server page never reached the sub-tab bar: " + ", ".join(
            type(w).__name__ for w in seen
        )
        assert not any(
            w in top for w in seen[: seen.index(bar)]
        ), "UP reached the top menu before the sub-tab bar: " + ", ".join(
            type(w).__name__ for w in seen[: seen.index(bar)]
        )
    finally:
        window.close()
        window.deleteLater()
        process_events()
        QApplication.instance().setStyleSheet("")


def test_left_right_on_the_sub_tab_bar_walk_the_tabs_and_keep_focus(
    qapp: object, ps: Any, tmp_path: Any
) -> None:
    """Decision 1: focus on the bar at tab 0: RIGHT x3 -> index 3, focus stays; LEFT -> 2."""
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction, install_gamepad_navigation

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Server")
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    process_events()
    nav, keyboard, gamepad = install_gamepad_navigation(window)
    try:
        bar = view._tabs.tabBar()
        count = view._tabs.count()
        assert count >= 4, f"only {count} sub-tabs: the walk proves nothing"
        view._tabs.setCurrentIndex(0)
        bar.setFocus()
        process_events()
        for _press in range(3):
            nav.navigate(Direction.RIGHT)
            process_events()
        assert (
            view._tabs.currentIndex() == 3
        ), f"RIGHT x3 from tab 0 is at {view._tabs.currentIndex()}, not 3"
        assert QApplication.focusWidget() is bar, "RIGHT left the tab bar"
        nav.navigate(Direction.LEFT)
        process_events()
        assert (
            view._tabs.currentIndex() == 2
        ), f"LEFT from tab 3 is at {view._tabs.currentIndex()}, not 2"
        assert QApplication.focusWidget() is bar, "LEFT left the tab bar"
    finally:
        keyboard.stop()
        gamepad.stop()
        window.close()
        window.deleteLater()
        process_events()


def test_right_skips_a_disabled_and_a_hidden_tab_and_falls_through_at_the_last(
    qapp: object, ps: Any, tmp_path: Any
) -> None:
    """Review round: RIGHT skips disabled AND hidden tabs; at the last tab it leaves the bar."""
    from PySide6.QtWidgets import QApplication, QPushButton

    from yulon.ui.gamepad import Direction, install_gamepad_navigation

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Server")
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    process_events()
    nav, keyboard, gamepad = install_gamepad_navigation(window)
    try:
        bar = view._tabs.tabBar()
        count = view._tabs.count()
        assert count >= 4, f"only {count} sub-tabs: the walk proves nothing"
        # Disabled and hidden tabs are skipped.
        view._tabs.setTabEnabled(2, False)
        view._tabs.setTabVisible(3, False)
        nav.invalidate()
        view._tabs.setCurrentIndex(0)
        bar.setFocus()
        process_events()
        nav.navigate(Direction.RIGHT)
        process_events()
        assert (
            view._tabs.currentIndex() == 1
        ), f"RIGHT from tab 0 is at {view._tabs.currentIndex()}, not 1"
        nav.navigate(Direction.RIGHT)
        process_events()
        assert (
            view._tabs.currentIndex() == 4
        ), f"RIGHT past disabled tab 2 and hidden tab 3 is at {view._tabs.currentIndex()}, not 4"
        view._tabs.setTabVisible(3, True)
        # At the last tab, RIGHT leaves the bar geometrically (old behaviour).
        view._tabs.setTabEnabled(2, True)
        nav.invalidate()
        view._tabs.setCurrentIndex(count - 1)
        bar.setFocus()
        process_events()
        nav.navigate(Direction.RIGHT)
        process_events()
        assert QApplication.focusWidget() is not bar, "RIGHT at the last tab stayed on the bar"
        assert isinstance(QApplication.focusWidget(), QPushButton) or isinstance(
            QApplication.focusWidget(), QWidget
        )
    finally:
        keyboard.stop()
        gamepad.stop()
        window.close()
        window.deleteLater()
        process_events()


def test_left_at_the_first_tab_falls_through(qapp: object, ps: Any, tmp_path: Any) -> None:
    """Decision 1 mirror: at the first tab LEFT leaves geometrically instead of wrapping."""
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction, install_gamepad_navigation

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Server")
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    process_events()
    nav, keyboard, gamepad = install_gamepad_navigation(window)
    try:
        bar = view._tabs.tabBar()
        view._tabs.setCurrentIndex(0)
        bar.setFocus()
        process_events()
        nav.navigate(Direction.LEFT)
        process_events()
        assert QApplication.focusWidget() is not bar, "LEFT at the first tab stayed on the bar"
    finally:
        keyboard.stop()
        gamepad.stop()
        window.close()
        window.deleteLater()
        process_events()


def test_up_from_the_modules_pages_last_focusable_reaches_the_sub_tab_bar(
    qapp: object, ps: Any, tmp_path: Any
) -> None:
    """T874-B: UP from the Modules page's last stop reaches the sub-tab bar within 40 presses.

    The reported second gap was repeated UP from inside the Modules list
    orbiting list rows and never reaching the bar. Driven with the Navigator
    installed -- the production configuration (`build_window()` installs it):
    without a Navigator Qt's own arrow handling eats the presses in the scroll
    list (a row self-loop / Install <-> chip bounce that never leaves), which is
    a harness without navigation, not a navigation defect. With it, every UP
    strictly advances (each press lands on a new widget above the last) and the
    bar is reached; the no-revisit assertion below is the orbit pin, and the
    40-press bound is the ruling's "within the number of rows above it".
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Modules")
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    process_events()
    from yulon.ui.gamepad import _iter_focusable, install_gamepad_navigation

    nav, keyboard, gamepad = install_gamepad_navigation(window)
    try:
        bar = view._tabs.tabBar()
        page = view._tabs.currentWidget()
        stops = [w for w in _iter_focusable(window) if page.isAncestorOf(w)]
        assert stops, "the Modules page has no focusable widget"
        start = stops[-1]
        start.setFocus()
        process_events()
        seen: set[int] = {id(start)}
        for press in range(40):
            landed = _pad_press(window, Qt.Key.Key_Up)
            if landed is bar:
                break
            assert id(landed) not in seen, (
                f"UP orbits: press {press + 1} revisited "
                f"{type(landed).__name__} instead of advancing to the bar"
            )
            seen.add(id(landed))
        else:
            pytest.fail("UP from the Modules page's last stop never reached the bar in 40 presses")
        assert QApplication.focusWidget() is bar
    finally:
        keyboard.stop()
        gamepad.stop()
        window.close()
        window.deleteLater()
        process_events()
        QApplication.instance().setStyleSheet("")


def test_down_from_the_sub_tab_bar_enters_the_current_page(
    qapp: object, ps: Any, tmp_path: Any
) -> None:
    """UP/DOWN unchanged: Down from the bar enters the page (its top-left focusable)."""
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction, install_gamepad_navigation

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Server")
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    process_events()
    nav, keyboard, gamepad = install_gamepad_navigation(window)
    try:
        bar = view._tabs.tabBar()
        bar.setFocus()
        process_events()
        nav.navigate(Direction.DOWN)
        process_events()
        landed = QApplication.focusWidget()
        page = view._tabs.currentWidget()
        assert page.isAncestorOf(
            landed
        ), f"DOWN from the bar left the page: {type(landed).__name__}"
    finally:
        keyboard.stop()
        gamepad.stop()
        window.close()
        window.deleteLater()
        process_events()


def test_left_right_on_the_left_rail_walk_its_tabs(qapp: object, ps: Any, tmp_path: Any) -> None:
    """Decision 4: the same rules hold for the left rail (nothing server-specific in gamepad.py)."""
    from PySide6.QtWidgets import QApplication, QTabWidget

    from yulon.ui.gamepad import Direction, install_gamepad_navigation

    view = ControllerView(WOTLK, _services(ps, tmp_path, []), status_poll_ms=0)
    window, _tab = _controller_in_the_real_window(view, "Server")
    window.resize(1280, 800)
    window.show()
    window.activateWindow()
    process_events()
    nav, keyboard, gamepad = install_gamepad_navigation(window)
    try:
        rail = window.findChild(QTabWidget, "sidebar-tabs")
        assert rail is not None and rail.count() >= 2
        rail.tabBar().show()
        rail.setCurrentIndex(0)
        rail.tabBar().setFocus()
        process_events()
        nav.navigate(Direction.RIGHT)
        process_events()
        assert rail.currentIndex() == 1, f"RIGHT on the rail is at {rail.currentIndex()}, not 1"
        assert QApplication.focusWidget() is rail.tabBar(), "RIGHT left the rail bar"
        # Review round: a hidden rail tab is skipped too. With the rail's only
        # other tab hidden there is nowhere to walk to, so RIGHT leaves the bar
        # instead of landing on the hidden tab.
        rail.setTabVisible(1, False)
        nav.invalidate()
        rail.setCurrentIndex(0)
        rail.tabBar().setFocus()
        process_events()
        nav.navigate(Direction.RIGHT)
        process_events()
        assert rail.currentIndex() == 0, "RIGHT walked the rail onto a hidden tab"
        assert (
            QApplication.focusWidget() is not rail.tabBar()
        ), "RIGHT stayed on the rail bar with only a hidden tab ahead"
        rail.setTabVisible(1, True)
        nav.invalidate()
    finally:
        keyboard.stop()
        gamepad.stop()
        window.close()
        window.deleteLater()
        process_events()


def _bundled_theme_ids() -> list[str]:
    """Every bundled theme id, so a new theme cannot miss the focus-ring rule."""
    from yulon.ui.theme_spec import BUNDLED_THEME_ORDER

    return list(BUNDLED_THEME_ORDER)


@pytest.mark.parametrize("theme_id", _bundled_theme_ids())
def test_every_theme_marks_the_focused_server_sub_tab(qapp: object, theme_id: str) -> None:
    """Review round: every theme marks the focused SERVER sub-tab with the focus ring.

    The D-pad's first stop above a server page is the sub-tabs bar, so the
    `QTabWidget#server-sub-tabs > QTabBar::tab:focus` rule is what the player
    sees when Left/Right will switch tabs. The list comes from the theme
    registry, not a hard-coded copy, so it cannot drift out of date.
    """
    from yulon.ui.theme import get_theme
    from yulon.ui.theme_qss import _build_qss

    spec = get_theme(theme_id)
    qss = _build_qss(1.0, theme_name=theme_id)
    assert (
        "QTabWidget#server-sub-tabs > QTabBar::tab:focus" in qss
    ), f"{theme_id}: no server-sub-tabs focus rule"
    assert spec.accent_bright in qss, f"{theme_id}: focus rule is not the focus-ring token"
