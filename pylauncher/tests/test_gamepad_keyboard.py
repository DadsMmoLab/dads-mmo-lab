"""T139: the pad's keyboard filter must not eat the keys a text field is typed with.

`KeyboardSource` filters every key press in the application. A player reported that
R switched tab and Space and Backspace did nothing in the console and on the Accounts
tab, so no name or command with an r in it could be typed. These tests press real
keys at the window, the way the platform delivers them, with a field focused.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from tests.conftest import process_events

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QComboBox,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from yulon.ui.gamepad import install_gamepad_navigation  # noqa: E402


@pytest.fixture
def window(qapp: object) -> Iterator[QWidget]:
    win = QWidget()
    tabs = QTabWidget(win)
    tabs.setObjectName("sidebar-tabs")
    first = QWidget()
    layout = QVBoxLayout(first)
    for name, widget in (
        ("field", QLineEdit(first)),
        ("read_only", QLineEdit(first)),
        ("editor", QPlainTextEdit(first)),
        ("spin", QSpinBox(first)),
        ("rich", QTextEdit(first)),
        ("combo", QComboBox(first)),
        ("button", QPushButton("OK", first)),
    ):
        widget.setObjectName(name)
        layout.addWidget(widget)
    first.findChild(QLineEdit, "read_only").setReadOnly(True)
    first.findChild(QComboBox, "combo").setEditable(True)
    tabs.addTab(first, "one")
    tabs.addTab(QWidget(), "two")
    QVBoxLayout(win).addWidget(tabs)
    _navigator, keyboard, gamepad = install_gamepad_navigation(win)
    win.show()
    win.activateWindow()
    process_events()
    yield win
    keyboard.stop()
    gamepad.stop()
    win.close()
    win.deleteLater()
    process_events()


def _focus(win: QWidget, name: str) -> QWidget:
    widget = win.findChild(QWidget, name)
    widget.setFocus()
    process_events()
    assert widget.hasFocus(), f"{name} did not take focus"
    return widget


def _press(win: QWidget, key: Qt.Key) -> None:
    # At the window, not the widget: the platform hands a key to the QWindow first,
    # and the application filter sees it there before any widget does.
    QTest.keyClick(win.windowHandle(), key)
    process_events()


def _tabs(win: QWidget) -> QTabWidget:
    return win.findChild(QTabWidget, "sidebar-tabs")


def test_r_l_and_space_type_into_a_line_edit_and_do_not_switch_tab(window: QWidget) -> None:
    field = _focus(window, "field")
    for key in (Qt.Key.Key_R, Qt.Key.Key_L, Qt.Key.Key_Space, Qt.Key.Key_R):
        _press(window, key)
    assert field.text().lower() == "rl r"
    assert _tabs(window).currentIndex() == 0


def test_backspace_deletes_in_a_line_edit(window: QWidget) -> None:
    field = _focus(window, "field")
    field.setText("gm")
    field.setCursorPosition(2)
    _press(window, Qt.Key.Key_Backspace)
    assert field.text() == "g"


def test_the_typing_keys_reach_a_multi_line_editor_and_a_spinbox(window: QWidget) -> None:
    editor = _focus(window, "editor")
    for key in (Qt.Key.Key_R, Qt.Key.Key_Space, Qt.Key.Key_L, Qt.Key.Key_Backspace):
        _press(window, key)
    assert editor.toPlainText().lower() == "r "
    spin = _focus(window, "spin")
    spin.setValue(42)
    spin.selectAll()
    QTest.keyClick(spin, Qt.Key.Key_End)
    _press(window, Qt.Key.Key_Backspace)
    assert spin.cleanText() == "4"
    assert _tabs(window).currentIndex() == 0


def test_the_typing_keys_reach_a_rich_text_editor_and_an_editable_combo(window: QWidget) -> None:
    for name in ("rich", "combo"):
        widget = _focus(window, name)
        for key in (Qt.Key.Key_R, Qt.Key.Key_Space, Qt.Key.Key_L, Qt.Key.Key_Backspace):
            _press(window, key)
        text = widget.toPlainText() if isinstance(widget, QTextEdit) else widget.currentText()
        assert text.lower() == "r ", name
    assert _tabs(window).currentIndex() == 0


def test_off_a_field_r_still_cycles_the_tabs(window: QWidget) -> None:
    """The pad mapping stands everywhere a key cannot be typed."""
    _focus(window, "button")
    _press(window, Qt.Key.Key_R)
    assert _tabs(window).currentIndex() == 1


def test_a_read_only_field_is_not_a_place_to_type(window: QWidget) -> None:
    _focus(window, "read_only")
    _press(window, Qt.Key.Key_R)
    assert _tabs(window).currentIndex() == 1


def test_down_still_leaves_a_field(window: QWidget) -> None:
    """Up/Down are the pad's way out of a field; they keep that meaning."""
    field = _focus(window, "field")
    _press(window, Qt.Key.Key_Down)
    assert not field.hasFocus()


# --- the SDL parity guard: it must catch a remap and DEGRADE, not raise. ----


def test_sdl_parity_passes_against_real_pygame(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mirrored constants agree with the pygame this process imports."""
    import pygame  # noqa: F401  # pyinstaller-visible; present in the venv

    import yulon.ui.gamepad as gamepad_module

    monkeypatch.setattr(gamepad_module, "_parity_result", None)
    assert gamepad_module._assert_sdl_parity() is True


def test_sdl_parity_detects_drift_and_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A remapped SDL constant is caught as a verdict, not raised as an abort."""
    import pygame

    import yulon.ui.gamepad as gamepad_module

    monkeypatch.setattr(gamepad_module, "_parity_result", None)
    monkeypatch.setattr(pygame, "CONTROLLER_BUTTON_A", 999)
    # A bad mapping must report False (poll disabled) without throwing.
    assert gamepad_module._assert_sdl_parity() is False
    # And the verdict is memoized: a second call does not re-raise or re-drift.
    assert gamepad_module._assert_sdl_parity() is False


# --- T89: a button that opens a menu, driven without a mouse. ---------------
#
# The Modules tab's three server-build presses are one "Server build ▾" button
# with a menu since T89. A menu's items are `QAction`s and not widgets, so the
# D-pad walk has nothing to move between once one is open, and the keyboard
# filter used to eat the arrows and Return the menu reads its own items with.


class _MenuWindow:
    """A window holding a button whose menu has a live, a dead and a live item."""

    def __init__(self) -> None:
        from PySide6.QtWidgets import QMenu

        self.win = QWidget()
        tabs = QTabWidget(self.win)
        tabs.setObjectName("sidebar-tabs")
        first = QWidget()
        layout = QVBoxLayout(first)
        self.other = QPushButton("Other", first)
        self.button = QPushButton("Choose ▾", first)
        layout.addWidget(self.other)
        layout.addWidget(self.button)
        self.menu = QMenu(self.button)
        self.fired: list[str] = []
        for text in ("one", "two", "three"):
            action = self.menu.addAction(text)
            action.triggered.connect(lambda _checked=False, said=text: self.fired.append(said))
        # The middle one is dead, so "Down twice lands on three" is a claim that
        # the menu's OWN rule ran -- it skips a disabled item -- and not merely
        # that two key presses arrived.
        self.menu.actions()[1].setEnabled(False)
        self.button.setMenu(self.menu)
        tabs.addTab(first, "one")
        tabs.addTab(QWidget(), "two")
        self.tabs = tabs
        QVBoxLayout(self.win).addWidget(tabs)
        self.navigator, self.keyboard, self.gamepad = install_gamepad_navigation(self.win)
        self.win.show()
        self.win.activateWindow()
        process_events()

    def open_popup(self) -> object:
        from PySide6.QtWidgets import QApplication

        return QApplication.activePopupWidget()

    def close(self) -> None:
        self.menu.close()
        self.keyboard.stop()
        self.gamepad.stop()
        self.win.close()
        self.win.deleteLater()
        process_events()


@pytest.fixture
def menu_window(qapp: object) -> Iterator[_MenuWindow]:
    made = _MenuWindow()
    yield made
    made.close()


def test_the_keyboard_opens_a_menu_button_moves_in_the_menu_and_chooses(
    menu_window: _MenuWindow,
) -> None:
    """Return opens it, Down walks it past the dead item, Return picks (T89)."""
    menu_window.button.setFocus()
    process_events()
    _press(menu_window.win, Qt.Key.Key_Return)
    assert menu_window.open_popup() is menu_window.menu, "Return did not open the menu"

    _press(menu_window.win, Qt.Key.Key_Down)
    active = menu_window.menu.activeAction()
    assert active is not None and active.text() == "one", "Down did not move into the menu"
    _press(menu_window.win, Qt.Key.Key_Down)
    active = menu_window.menu.activeAction()
    assert active is not None and active.text() == "three", "Down did not skip the dead item"
    _press(menu_window.win, Qt.Key.Key_Return)

    assert menu_window.fired == ["three"]
    assert menu_window.open_popup() is None, "choosing an item left the menu open"


def test_the_pad_opens_a_menu_button_moves_in_the_menu_and_chooses(
    menu_window: _MenuWindow,
) -> None:
    """The same walk through `Navigator`, which is all `GamepadSource` calls (T89).

    Not a key press: a real pad reaches the navigator as `navigate()` and
    `perform()` from the SDL poller, with no `QKeyEvent` anywhere, so passing
    keys through the filter is not enough on its own.
    """
    from yulon.ui.gamepad import Action, Direction

    nav = menu_window.navigator
    menu_window.button.setFocus()
    process_events()
    nav.perform(Action.CONFIRM)
    process_events()
    assert menu_window.open_popup() is menu_window.menu, "A did not open the menu"

    assert nav.navigate(Direction.DOWN) is True
    assert nav.navigate(Direction.DOWN) is True
    active = menu_window.menu.activeAction()
    assert active is not None and active.text() == "three", "the D-pad did not walk the menu"
    nav.perform(Action.CONFIRM)
    process_events()

    assert menu_window.fired == ["three"]
    assert menu_window.open_popup() is None


def test_back_closes_an_open_menu_and_a_bumper_does_not_switch_tab_under_it(
    menu_window: _MenuWindow,
) -> None:
    """B leaves the menu with nothing chosen; LB/RB wait until it is closed (T89)."""
    from yulon.ui.gamepad import Action, Direction

    nav = menu_window.navigator
    menu_window.button.setFocus()
    process_events()
    nav.perform(Action.CONFIRM)
    process_events()
    assert menu_window.open_popup() is menu_window.menu
    nav.navigate(Direction.DOWN)

    nav.perform(Action.CYCLE_NEXT)
    process_events()
    assert menu_window.tabs.currentIndex() == 0, "a bumper switched tab under an open menu"
    nav.perform(Action.BACK)
    process_events()

    assert menu_window.open_popup() is None, "B did not close the menu"
    assert menu_window.fired == []
    # And once it is closed the bumper is the tab switch again.
    nav.perform(Action.CYCLE_NEXT)
    assert menu_window.tabs.currentIndex() == 1


def test_backspace_closes_an_open_menu_as_b_does(menu_window: _MenuWindow) -> None:
    """Backspace is the pad's B under Steam Input's keyboard emulation (T89 round 2).

    A `QMenu` closes on Escape and ignores Backspace, so handing an open menu
    EVERY key left B dead inside one: the menu stayed open over nothing chosen.
    """
    menu_window.button.setFocus()
    process_events()
    _press(menu_window.win, Qt.Key.Key_Return)
    assert menu_window.open_popup() is menu_window.menu
    _press(menu_window.win, Qt.Key.Key_Down)

    _press(menu_window.win, Qt.Key.Key_Backspace)

    assert menu_window.open_popup() is None, "Backspace did not close the menu"
    assert menu_window.fired == []


def test_the_bumper_and_confirm_keys_keep_their_pad_meaning_in_an_open_menu(
    menu_window: _MenuWindow,
) -> None:
    """R and L switch nothing under an open menu; Space is A and chooses (T89 round 2)."""
    menu_window.button.setFocus()
    process_events()
    _press(menu_window.win, Qt.Key.Key_Return)
    _press(menu_window.win, Qt.Key.Key_Down)
    for key in (Qt.Key.Key_R, Qt.Key.Key_L):
        _press(menu_window.win, key)
        assert menu_window.open_popup() is menu_window.menu, f"{key} closed the menu"
        assert menu_window.tabs.currentIndex() == 0, f"{key} switched tab under the menu"
    assert menu_window.fired == []

    _press(menu_window.win, Qt.Key.Key_Space)

    assert menu_window.fired == ["one"]
    assert menu_window.open_popup() is None


def _reached_from(navigator: object, start: QWidget) -> set[QWidget]:
    """Every widget the D-pad reaches from `start`, through the navigator's own list.

    A breadth-first walk of presses in all four directions. The navigator is
    asked rather than the widget tree, because what is under test is what the
    navigator believes is there -- its cache -- and a fresh walk of the tree
    would not see a stale one.
    """
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction, Navigator

    assert isinstance(navigator, Navigator)
    seen = {start}
    frontier = [start]
    while frontier:
        here = frontier.pop()
        for direction in Direction:
            here.setFocus()
            navigator.navigate(direction)
            landed = QApplication.focusWidget()
            if landed is not None and landed not in seen:
                seen.add(landed)
                frontier.append(landed)
    return seen


def test_the_pad_follows_a_widget_shown_hidden_greyed_or_ungreyed_and_a_bumper(
    qapp: object,
) -> None:
    """The navigator's cached focus chain is redrawn when the tree it lists changes (T153).

    Found on the Modules tab, whose custom-module card swaps for a one-line form
    at small windows: a navigator primed before the swap kept the hidden card's
    buttons and never found the new ones, because the list is filtered on
    visible and enabled once, when it is walked, and nothing called
    `invalidate()`. The same held for a tab switched with a bumper -- the old
    tab's buttons stayed in the list and the new tab's were out of reach.

    Each case is ONE change on a freshly primed navigator, because one change of
    any kind would clear the cache for the others: a button SHOWN, a button
    UNGREYED, a button HIDDEN -- which is seen as a dead press rather than a
    missing target, since the navigator aims at the hidden button and the focus
    cannot land on it -- and a tab switched by RB. And the widget still gets its
    own `Show` event: the navigator watches the application's events and must
    not eat them.
    """
    from PySide6.QtCore import QEvent, QObject
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Action, Direction

    win = QWidget()
    tabs = QTabWidget(win)
    tabs.setObjectName("sidebar-tabs")
    first = QWidget()
    first_box = QVBoxLayout(first)
    start = QPushButton("Start", first)
    shown_later = QPushButton("Shown later", first)
    ungreyed_later = QPushButton("Ungreyed later", first)
    hidden_later = QPushButton("Hidden later", first)
    bottom = QPushButton("Bottom", first)
    for button in (start, shown_later, ungreyed_later, hidden_later, bottom):
        first_box.addWidget(button)
    second = QWidget()
    second_box = QVBoxLayout(second)
    other_tab = [QPushButton("Other one", second), QPushButton("Other two", second)]
    for button in other_tab:
        second_box.addWidget(button)
    tabs.addTab(first, "one")
    tabs.addTab(second, "two")
    QVBoxLayout(win).addWidget(tabs)
    navigator, keyboard, gamepad = install_gamepad_navigation(win)
    shown_later.hide()
    ungreyed_later.setEnabled(False)
    win.show()
    win.activateWindow()
    process_events()

    shows: list[object] = []

    class _Shows(QObject):
        def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
            if event.type() == QEvent.Type.Show:
                shows.append(watched)
            return False

    shown_later.installEventFilter(_Shows(shown_later))
    try:
        primed = _reached_from(navigator, start)
        assert shown_later not in primed and ungreyed_later not in primed

        shown_later.show()
        process_events()
        assert shows == [shown_later], "the navigator ate the button's own Show event"
        assert shown_later in _reached_from(
            navigator, start
        ), "a button shown after the walk is out of the pad's reach"

        ungreyed_later.setEnabled(True)
        process_events()
        assert ungreyed_later in _reached_from(
            navigator, start
        ), "a button ungreyed after the walk is out of the pad's reach"

        hidden_later.hide()
        process_events()
        ungreyed_later.setFocus()
        navigator.navigate(Direction.DOWN)
        assert (
            QApplication.focusWidget() is bottom
        ), "Down aimed at a button hidden since the walk and the focus went nowhere"

        _reached_from(navigator, start)
        start.setFocus()
        navigator.perform(Action.CYCLE_NEXT)
        process_events()
        assert tabs.currentIndex() == 1
        reached = _reached_from(navigator, other_tab[0])
        assert set(other_tab) <= reached, "the tab a bumper opened is out of the pad's reach"
        assert not reached & {
            start,
            shown_later,
            ungreyed_later,
            bottom,
        }, f"the pad lands on the tab it left: {[w.text() for w in reached]}"
    finally:
        keyboard.stop()
        gamepad.stop()
        win.close()
        win.deleteLater()
        process_events()
