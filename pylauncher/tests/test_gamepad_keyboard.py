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


def test_down_from_a_tab_pages_top_row_enters_the_page_and_up_still_leaves_it(
    qapp: object,
) -> None:
    """A tab widget is not a place the pad goes: its tab bar and its page are (T172).

    A `QTabWidget` takes `TabFocus`, so it was a candidate, scored at the centre
    of the whole widget -- the middle of its page -- while `setFocus()` on it
    lands on its tab bar, which Qt makes its focus proxy. From a page's top row
    that centre is straight below, so Down aimed into the page and the focus
    went up to the tab bar: seen live at 960x640 after RB to the Tuning tab.

    Built so the two answers differ: the top button is centred over the tab
    widget's middle, and the only button below it is far to the left. And Up
    from the top row still reaches the tab bar, which is walked in its own
    right, at its own place.
    """
    from PySide6.QtWidgets import QApplication, QHBoxLayout

    win = QWidget()
    tabs = QTabWidget(win)
    first = QWidget()
    column = QVBoxLayout(first)
    top_row = QHBoxLayout()
    top = QPushButton("Top", first)
    top_row.addStretch(1)
    top_row.addWidget(top)
    top_row.addStretch(1)
    bottom_row = QHBoxLayout()
    bottom = QPushButton("Bottom", first)
    bottom_row.addWidget(bottom)
    bottom_row.addStretch(1)
    column.addLayout(top_row)
    column.addStretch(1)
    column.addLayout(bottom_row)
    tabs.addTab(first, "one")
    tabs.addTab(QWidget(), "two")
    QVBoxLayout(win).addWidget(tabs)
    win.resize(600, 400)
    _navigator, keyboard, gamepad = install_gamepad_navigation(win)
    win.show()
    win.activateWindow()
    process_events()
    try:
        top.setFocus()
        process_events()
        _press(win, Qt.Key.Key_Down)
        landed = QApplication.focusWidget()
        assert (
            landed is bottom
        ), f"Down from the page's top row went to {type(landed).__name__}, not into the page"

        top.setFocus()
        process_events()
        _press(win, Qt.Key.Key_Up)
        assert QApplication.focusWidget() is tabs.tabBar(), "Up from the top row lost the tab bar"
    finally:
        keyboard.stop()
        gamepad.stop()
        win.close()
        win.deleteLater()
        process_events()


def test_left_from_a_spinbox_reaches_the_button_beside_it(qapp: object) -> None:
    """A spinbox's own editor is not a separate stop for the pad (T172).

    The editor inside a `QSpinBox` has the spinbox as its focus proxy, and its
    centre is left of the spinbox's -- so Left from the spinbox aimed at its own
    editor, the focus stayed where it was, and the press was dead. Through
    `navigate()`, the call a pad's D-pad makes (`GamepadSource`); the keyboard's
    Left is the spinbox's caret key (T139) and never gets this far.
    """
    from PySide6.QtWidgets import QApplication, QHBoxLayout

    from yulon.ui.gamepad import Direction

    win = QWidget()
    row = QHBoxLayout(win)
    before = QPushButton("Before", win)
    spin = QSpinBox(win)
    row.addWidget(before)
    row.addWidget(spin)
    navigator, keyboard, gamepad = install_gamepad_navigation(win)
    win.show()
    win.activateWindow()
    process_events()
    try:
        spin.setFocus()
        process_events()
        assert QApplication.focusWidget() is spin
        navigator.navigate(Direction.LEFT)
        landed = QApplication.focusWidget()
        assert landed is before, f"Left from the spinbox stayed on {type(landed).__name__}"
    finally:
        keyboard.stop()
        gamepad.stop()
        win.close()
        win.deleteLater()
        process_events()


def _placed(parent: QWidget, text: str, x: int, y: int, w: int, h: int) -> QPushButton:
    """A button at a fixed place, so the navigator's arithmetic is the test's own."""
    button = QPushButton(text, parent)
    button.setGeometry(x, y, w, h)
    return button


@pytest.fixture
def placed(qapp: object) -> Iterator[QWidget]:
    """A 600x400 window with no layout: every widget a test adds stays where it is put."""
    win = QWidget()
    win.resize(600, 400)
    yield win
    win.close()
    win.deleteLater()
    process_events()


def _pressed_from(win: QWidget, start: QWidget, direction: object) -> QWidget | None:
    """Show `win`, put the focus on `start`, and make one D-pad press through the navigator."""
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction

    assert isinstance(direction, Direction)
    navigator, keyboard, gamepad = install_gamepad_navigation(win)
    win.show()
    win.activateWindow()
    process_events()
    try:
        start.setFocus()
        process_events()
        navigator.navigate(direction)
        return QApplication.focusWidget()
    finally:
        keyboard.stop()
        gamepad.stop()


def test_down_takes_the_nearest_row_under_it_not_a_centred_one_further_down(
    placed: QWidget,
) -> None:
    """Down lands on the row just below, even when a row further down is better centred (T175).

    Seen live at 960x640: Down from the Tuning tab's conf list went to the
    full-width "Last action" strip at the bottom, past the three conf buttons
    straight under it, because the navigator took the candidate most nearly in
    line first -- the strip was 0 px off-centre, `authserver.conf` 2 px. Built
    so the two rules answer differently: the button under the start is 2 px
    off its centre, and a strip at the bottom is exactly under it.
    """
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 200, 20, 200, 40)
    _placed(placed, "Left", 0, 80, 200, 40)
    under = _placed(placed, "Under", 202, 80, 200, 40)
    _placed(placed, "Right", 404, 80, 196, 40)
    _placed(placed, "Strip", 0, 340, 600, 20)

    landed = _pressed_from(placed, start, Direction.DOWN)
    assert landed is under, f"Down skipped the row below and went to {landed.text()!r}"


def test_up_takes_the_nearest_row_over_it_not_a_centred_one_further_up(placed: QWidget) -> None:
    """The same rule upward: Up from a bottom strip stops at the row just above it (T175).

    Up from the strip went to the widget most nearly over its centre, at the top
    of the window -- on the Tuning tab that was the sub-tab bar, past the whole
    page.
    """
    from yulon.ui.gamepad import Direction

    _placed(placed, "Top", 200, 20, 200, 40)
    _placed(placed, "Left", 0, 280, 200, 40)
    over = _placed(placed, "Over", 202, 280, 200, 40)
    _placed(placed, "Right", 404, 280, 196, 40)
    strip = _placed(placed, "Strip", 0, 340, 600, 20)

    landed = _pressed_from(placed, strip, Direction.UP)
    assert landed is over, f"Up skipped the row above and went to {landed.text()!r}"


def test_right_takes_the_nearest_column_beside_it_not_a_level_one_further_on(
    placed: QWidget,
) -> None:
    """Left and Right are the same rule turned on its side (T175).

    A tall button on the left with a column of three beside it, and far to the
    right a button exactly level with the tall one's middle: Right goes to the
    column beside it, whose middle button is 13 px off level.
    """
    from yulon.ui.gamepad import Direction

    tall = _placed(placed, "Tall", 20, 20, 40, 360)
    _placed(placed, "Upper", 100, 20, 80, 110)
    beside = _placed(placed, "Beside", 100, 132, 80, 110)
    _placed(placed, "Lower", 100, 244, 80, 110)
    _placed(placed, "Far", 500, 180, 80, 40)

    landed = _pressed_from(placed, tall, Direction.RIGHT)
    assert landed is beside, f"Right skipped the column beside it and went to {landed.text()!r}"


def test_left_takes_the_nearest_column_beside_it_not_a_level_one_further_on(
    placed: QWidget,
) -> None:
    """And Left, from the far button back: the column beside it, not the tall one level with it."""
    from yulon.ui.gamepad import Direction

    _placed(placed, "Tall", 20, 20, 40, 360)
    _placed(placed, "Upper", 100, 20, 80, 110)
    beside = _placed(placed, "Beside", 100, 132, 80, 110)
    _placed(placed, "Lower", 100, 244, 80, 110)
    far = _placed(placed, "Far", 500, 180, 80, 40)

    landed = _pressed_from(placed, far, Direction.LEFT)
    assert landed is beside, f"Left skipped the column beside it and went to {landed.text()!r}"


def test_down_from_inside_a_focusable_box_leaves_it_for_the_row_below(placed: QWidget) -> None:
    """A box the focus can stop on is not the "row below" a widget inside it (T175).

    The Modules tab's list is such a box: a scroll area the pad stops on, with
    the modules' own buttons inside it. From a button in the box's top half the
    box's centre is below, and the box spans the button's column, so a rule
    that counted it as a row at no distance would send Down out to the box
    itself. The box is exactly as centred as the row below, and nearer by
    centre, so the older rule went there too.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame

    from yulon.ui.gamepad import Direction

    box = QFrame(placed)
    box.setGeometry(0, 0, 600, 300)
    box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    inside = _placed(box, "Inside", 250, 10, 100, 30)
    below = _placed(placed, "Below", 250, 320, 100, 30)

    landed = _pressed_from(placed, inside, Direction.DOWN)
    assert landed is below, f"Down from inside the box went to {type(landed).__name__}"


def test_a_focused_box_is_entered_before_it_is_left(placed: QWidget) -> None:
    """From a focusable box, a stop inside it that lies ahead comes before one outside (T175).

    The other half of the box rule above. A box's own buttons overlap it on
    both axes, like the box overlaps a button inside it, so a rule that only
    counted rows wholly beyond the focused widget never went INTO a box: from
    the Modules tab's list at 960x640 the pad went round its "Available"
    header, which nothing else lines up with, and the header was out of reach.
    Built so the rules differ: a button outside, level with the box's middle
    and nearer its edge, and the button inside well off that middle.
    """
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame

    from yulon.ui.gamepad import Direction

    placed.resize(700, 400)
    _placed(placed, "Outside", 0, 0, 40, 300)
    box = QFrame(placed)
    box.setGeometry(60, 0, 600, 300)
    box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    inside = _placed(box, "Inside", 20, 20, 100, 30)

    landed = _pressed_from(placed, box, Direction.LEFT)
    assert landed is inside, f"Left from the box went to {type(landed).__name__}, not into it"


def test_down_takes_a_much_nearer_row_beside_its_column_over_one_far_below_in_it(
    placed: QWidget,
) -> None:
    """The nearest row wins even when nothing in it is in the widget's own column (T175).

    Found in review of the first cut, which went to whatever shared the column
    however far away: Up from a chip in the Modules list went to the sub-tab bar
    past the whole toolbar, and Down from the list's "Available" header went to
    the strip below the list past its rows. A small button just below and off
    to the side, and one in the column further down: Down takes the near one,
    and the one further down is the next press.
    """
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 200, 20, 200, 40)
    aside = _placed(placed, "Aside", 0, 80, 60, 40)
    _placed(placed, "Under", 250, 300, 100, 40)

    landed = _pressed_from(placed, start, Direction.DOWN)
    assert landed is aside, f"Down went past the row just below, to {landed.text()!r}"


def test_in_the_nearest_row_the_one_in_its_own_column_beats_one_nearer_its_middle(
    placed: QWidget,
) -> None:
    """Within the row, the column comes before the centring (T175).

    A wide button that shares part of the start's column, its middle far to the
    left, and a narrow one just past the start's right edge, nearer its middle
    but in no part of its column: Down takes the one in its column.
    """
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 200, 20, 200, 40)
    shares = _placed(placed, "Shares", 0, 80, 220, 40)
    _placed(placed, "Beside", 410, 90, 20, 30)

    landed = _pressed_from(placed, start, Direction.DOWN)
    assert landed is shares, f"Down went to {landed.text()!r}, outside its own column"


def test_sideways_in_the_nearest_column_the_one_in_its_own_row_beats_one_nearer_its_middle(
    placed: QWidget,
) -> None:
    """And turned on its side: Right takes the column's button that shares its row (T175)."""
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 20, 150, 100, 40)
    shares = _placed(placed, "Shares", 140, 0, 60, 160)
    _placed(placed, "Below", 150, 200, 40, 20)

    landed = _pressed_from(placed, start, Direction.RIGHT)
    assert landed is shares, f"Right went to {landed.text()!r}, outside its own row"


def test_right_does_not_take_a_nearer_column_off_to_the_side_of_its_row(placed: QWidget) -> None:
    """Sideways keeps to the row: Right goes along it, not to a nearer button above it (T175).

    Up and Down take the nearest row wherever its buttons stand; Left and
    Right do not take the nearest column the same way, because a column
    counted across the whole height of the Modules list sent Left from one
    module's Install to a chip 900 px further down it.
    """
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 20, 150, 100, 40)
    _placed(placed, "Aside", 140, 0, 40, 40)
    level = _placed(placed, "Level", 400, 160, 100, 20)

    landed = _pressed_from(placed, start, Direction.RIGHT)
    assert landed is level, f"Right went to {landed.text()!r}, off to the side of its row"


def test_up_does_not_take_a_button_beside_it_that_ends_below_its_top(placed: QWidget) -> None:
    """A button beside the start that reaches a little above it is on its row, not above (T175).

    Its middle is above the start's, so it is "ahead" by the middle, but its
    bottom is below the start's TOP edge, the one Up leaves by: the space
    between the two is negative, and Up goes to the row wholly above. Measured
    from the start's bottom edge instead, the button beside would be 30 px
    past it and nearer than the row above.
    """
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 200, 200, 100, 60)
    _placed(placed, "Beside", 320, 170, 100, 60)
    above = _placed(placed, "Above", 200, 100, 100, 50)

    landed = _pressed_from(placed, start, Direction.UP)
    assert landed is above, f"Up went to {landed.text()!r}, beside it on its own row"


def test_down_does_not_take_a_button_beside_it_that_starts_above_its_bottom(
    placed: QWidget,
) -> None:
    """The same turned over: Down measures from the start's bottom edge (T175)."""
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 200, 100, 100, 60)
    _placed(placed, "Beside", 320, 130, 100, 60)
    below = _placed(placed, "Below", 200, 250, 100, 50)

    landed = _pressed_from(placed, start, Direction.DOWN)
    assert landed is below, f"Down went to {landed.text()!r}, beside it on its own row"


def _scrolled_box(parent: QWidget, x: int, y: int, w: int, h: int, content_height: int) -> object:
    """A scroll area the pad cannot stop on, with a content widget taller than its box."""
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QFrame, QScrollArea

    area = QScrollArea(parent)
    area.setGeometry(x, y, w, h)
    area.setFrameShape(QFrame.Shape.NoFrame)
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    content = QWidget()
    content.resize(w, content_height)
    area.setWidget(content)
    return area


def test_a_button_scrolled_out_of_its_box_is_not_one_press_away_from_outside_it(
    placed: QWidget,
) -> None:
    """Nothing the player cannot see is a target (T175).

    A module's buttons scrolled out of the Modules list are still visible to
    Qt, at the place they would be drawn, and the pad went to them: the focus
    left the screen. A button above a box, one scrolled out of sight inside it
    right under the start, and one below the box further down: Down goes to
    the one below the box.
    """
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 250, 10, 100, 30)
    area = _scrolled_box(placed, 0, 60, 600, 200, 1000)
    hidden = _placed(area.widget(), "Hidden", 250, 250, 100, 30)
    below = _placed(placed, "Below", 250, 330, 100, 30)

    landed = _pressed_from(placed, start, Direction.DOWN)
    assert landed is not hidden, "Down went to a button scrolled out of sight"
    assert landed is below, f"Down went to {type(landed).__name__}"


def test_a_button_half_out_of_its_box_is_measured_by_the_half_that_shows(placed: QWidget) -> None:
    """A button half scrolled out of sight is as near as its visible half (T175).

    From below the box: the button's lower half is cut off by the box's
    bottom edge, so it looks further up than it is, and a button beside the
    box that is nearer than the visible half is the one Up goes to -- while
    by the button's whole rectangle it would be the other way round.
    """
    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 250, 350, 100, 30)
    area = _scrolled_box(placed, 0, 60, 300, 200, 1000)
    _placed(area.widget(), "Half", 250, 150, 100, 100)
    nearer = _placed(placed, "Nearer", 320, 270, 100, 30)

    landed = _pressed_from(placed, start, Direction.UP)
    assert landed is nearer, f"Up went to {landed.text()!r}, measured by what is out of sight"


def test_a_press_onto_a_button_half_out_of_its_box_scrolls_it_into_view(placed: QWidget) -> None:
    """The pad's focus is never left out of sight: the box scrolls to it (T175).

    `setFocus()` scrolls nothing, so a button the pad moved to at the bottom
    edge of the Modules list stayed half hidden, and one further down was
    wholly so. The press scrolls the box until the button shows.
    """
    from PySide6.QtCore import QPoint

    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 250, 10, 100, 30)
    area = _scrolled_box(placed, 0, 60, 600, 200, 1000)
    half = _placed(area.widget(), "Half", 250, 150, 100, 100)

    landed = _pressed_from(placed, start, Direction.DOWN)
    assert landed is half, f"Down went to {type(landed).__name__}"
    viewport = area.viewport()
    top = half.mapTo(viewport, QPoint(0, 0)).y()
    assert area.verticalScrollBar().value() > 0, "the box did not scroll"
    assert (
        0 <= top and top + half.height() <= viewport.height()
    ), f"the button is still cut off: {top}..{top + half.height()} in a {viewport.height()} box"


def test_a_list_the_pad_stops_on_is_entered_at_the_list_from_outside(placed: QWidget) -> None:
    """From outside a scroll area the pad can stop on, the press goes to the area first (T175).

    The Catalog's shelf is such a list, and whatever shows of a tile in it
    starts no nearer than the shelf's own edge: taken at the row, the tile
    best in line won every tie and the shelf was nobody's next stop at
    1280x800. A button inside the box straight under the start, and the box's
    own middle far off to the side: Down goes to the box.
    """
    from PySide6.QtCore import Qt

    from yulon.ui.gamepad import Direction

    start = _placed(placed, "Start", 20, 10, 100, 30)
    area = _scrolled_box(placed, 0, 60, 600, 200, 1000)
    area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    _placed(area.widget(), "Inside", 20, 5, 100, 30)

    landed = _pressed_from(placed, start, Direction.DOWN)
    assert landed is area, f"Down went past the list to {type(landed).__name__}"


def _focusable_list_of_rows(win: QWidget, scrolled: int = 0) -> tuple[object, list[QPushButton]]:
    """A list the pad stops on, 200 px tall, of 20 rows 40 px apart: R0..R4 show, scrolled 0."""
    from PySide6.QtCore import Qt

    area = _scrolled_box(win, 0, 100, 600, 200, 800)
    area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    rows = [_placed(area.widget(), f"R{i}", 250, 10 + 40 * i, 100, 30) for i in range(20)]
    area.verticalScrollBar().setValue(scrolled)
    return area, rows


def test_down_from_a_focused_list_lands_on_its_first_row_that_shows(placed: QWidget) -> None:
    """A list the pad stops on is entered at its leading edge: Down goes to its top row (T175).

    Measured from the list's middle, as the older rule measured, Down went to
    the first row below the middle, R3, over R0 to R2; on the Modules tab at
    960x640 that was a chip 100 px down, past the "Available" header and two
    rows of Install.
    """
    from yulon.ui.gamepad import Direction

    area, rows = _focusable_list_of_rows(placed)
    landed = _pressed_from(placed, area, Direction.DOWN)
    assert landed is rows[0], f"Down from the list went to {landed.text()!r}, not R0"


def test_up_from_a_focused_list_lands_on_its_last_row_that_shows(placed: QWidget) -> None:
    """And Up enters at the bottom edge: the last row that shows, not one scrolled out below."""
    from yulon.ui.gamepad import Direction

    area, rows = _focusable_list_of_rows(placed)
    landed = _pressed_from(placed, area, Direction.UP)
    assert landed is rows[4], f"Up from the list went to {landed.text()!r}, not R4"


def test_the_first_press_on_a_window_with_nothing_focused_lands_on_what_shows(
    placed: QWidget,
) -> None:
    """With nothing focused yet, the first press puts the focus on a stop that shows (T175).

    The first press seeds the focus at the top-left stop. A list scrolled part
    way has its rows above the box still at their places above it, and the
    top-left of those is out of sight: the seed is the top-left of what shows,
    here R4 with its top 20 px cut off, and the list scrolls until all of it
    shows. The list itself is not a stop.
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction

    area, rows = _focusable_list_of_rows(placed)
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    navigator, keyboard, gamepad = install_gamepad_navigation(placed)
    placed.show()
    placed.activateWindow()
    process_events()
    try:
        area.verticalScrollBar().setValue(190)
        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()
        process_events()
        assert QApplication.focusWidget() is None
        navigator.navigate(Direction.DOWN)
        landed = QApplication.focusWidget()
        assert landed is rows[4], f"the first press went to {_name(landed)}, not R4"
        top = landed.mapTo(area.viewport(), QPoint(0, 0)).y()
        assert (
            0 <= top and top + landed.height() <= area.viewport().height()
        ), f"R4 is still cut off at {top}..{top + landed.height()}"
    finally:
        keyboard.stop()
        gamepad.stop()


def test_a_press_never_goes_out_to_the_box_that_holds_the_focus(placed: QWidget) -> None:
    """Sideways from a row of a list with nothing beside it goes past the list, not to it (T175).

    The list holds the row, so it is no place to go from the row: taken as
    one, Left from the Modules list at 960x640 entered it at a module's
    Install, Left from there went out to the list -- the candidate most level
    with the Install -- and the next Left went in again, for ever. Here the
    list's middle is exactly level with the row, and a button beside the list
    shares some of the list's height but none of the row's: Left goes on
    past the list to it.
    """
    from PySide6.QtCore import Qt

    from yulon.ui.gamepad import Direction

    area = _scrolled_box(placed, 100, 100, 500, 200, 800)
    area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    level = _placed(area.widget(), "Level", 350, 85, 100, 30)
    _placed(area.widget(), "Lower", 100, 150, 100, 30)
    beside = _placed(placed, "Beside", 0, 0, 60, 105)

    landed = _pressed_from(placed, level, Direction.LEFT)
    assert landed is not area, "Left went out to the list that holds the row"
    assert landed is beside, f"Left went to {_name(landed)}, not past the list"


def test_with_nothing_past_the_list_either_a_press_still_never_goes_out_to_it(
    placed: QWidget,
) -> None:
    """The same list with nothing beside it: the older rule decides, without the list (T175).

    With no row that way from the button or from the list, the candidate most
    nearly level is taken -- which would be the list, exactly level with the
    row. The list holds the button, so it is not a candidate: Left goes to the
    list's other button, down and to the left.
    """
    from PySide6.QtCore import Qt

    from yulon.ui.gamepad import Direction

    area = _scrolled_box(placed, 100, 100, 500, 200, 800)
    area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    level = _placed(area.widget(), "Level", 350, 85, 100, 30)
    lower = _placed(area.widget(), "Lower", 100, 150, 100, 30)

    landed = _pressed_from(placed, level, Direction.LEFT)
    assert landed is not area, "Left went out to the list that holds the button"
    assert landed is lower, f"Left went to {_name(landed)}"


def test_the_press_back_from_a_list_just_entered_goes_past_it_not_back_out_to_it(
    placed: QWidget,
) -> None:
    """Down into a list and straight back Up leaves the list: the way back is not to a box (T175).

    The pad goes back where its last move came from, but not to a box that
    holds the focus: Down from the Modules list to its top row and Up went
    back to the list, and the next Up entered the list again at its bottom
    row. Here Up from the list's top row goes on to the button over the list.
    """
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction

    area, rows = _focusable_list_of_rows(placed)
    above = _placed(placed, "Above", 250, 20, 100, 40)
    navigator, keyboard, gamepad = install_gamepad_navigation(placed)
    placed.show()
    placed.activateWindow()
    process_events()
    try:
        area.setFocus()
        process_events()
        navigator.navigate(Direction.DOWN)
        assert QApplication.focusWidget() is rows[0], "Down did not enter the list at R0"
        navigator.navigate(Direction.UP)
        landed = QApplication.focusWidget()
        assert landed is not area, "Up went back out to the list that holds R0"
        assert landed is above, f"Up from R0 went to {_name(landed)}"
    finally:
        keyboard.stop()
        gamepad.stop()


def test_the_first_press_with_nothing_showing_focuses_a_stop_and_scrolls_it_into_view(
    placed: QWidget,
) -> None:
    """With nothing focused and no stop showing, the first press still gives the focus a place.

    Every stop is a row of a list scrolled past all of them: the seed falls
    back to the top-left of them all, and the list scrolls until it shows.
    Doing nothing instead would leave every later press with nowhere to
    start from.
    """
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction

    area = _scrolled_box(placed, 0, 100, 600, 200, 800)
    area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    rows = [_placed(area.widget(), f"R{i}", 250, 10 + 40 * i, 100, 30) for i in range(3)]
    navigator, keyboard, gamepad = install_gamepad_navigation(placed)
    placed.show()
    placed.activateWindow()
    process_events()
    try:
        area.verticalScrollBar().setValue(300)
        assert all(_showing(row, placed) is None for row in rows), "a row still shows"
        focused = QApplication.focusWidget()
        if focused is not None:
            focused.clearFocus()
        process_events()
        assert QApplication.focusWidget() is None
        navigator.navigate(Direction.DOWN)
        landed = QApplication.focusWidget()
        assert landed is rows[0], f"the first press went to {_name(landed)}"
        top = landed.mapTo(area.viewport(), QPoint(0, 0)).y()
        assert (
            0 <= top and top + landed.height() <= area.viewport().height()
        ), f"R0 is still out of sight at {top}"
    finally:
        keyboard.stop()
        gamepad.stop()


def _narrow_pair_over_a_wide_one(win: QWidget) -> tuple[QPushButton, QPushButton, QPushButton]:
    """Two buttons side by side over one as wide as both: the wide one's Up picks the centred one.

    The left one is the start, and the right one sits over the wide one's
    middle, so by the rule alone Down from the left one and then Up comes back
    to the right one.
    """
    left = _placed(win, "Left", 0, 20, 100, 40)
    right = _placed(win, "Right", 110, 20, 180, 40)
    wide = _placed(win, "Wide", 0, 100, 400, 40)
    return left, right, wide


def test_up_right_after_down_goes_back_to_where_down_came_from(placed: QWidget) -> None:
    """The opposite press undoes a move, where the rule alone would go elsewhere (T175).

    The nearest row is not symmetric: Down from a narrow button onto a wide
    one, and Up from the wide one goes to the button best centred over it.
    Measured in the real window, about 460 of 1004 presses were not undone by
    the opposite one; the Maintenance tab's "Back up now", Down to the backups
    list, then Up went to "Refresh".
    """
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction

    left, _right, wide = _narrow_pair_over_a_wide_one(placed)
    navigator, keyboard, gamepad = install_gamepad_navigation(placed)
    placed.show()
    placed.activateWindow()
    process_events()
    try:
        left.setFocus()
        process_events()
        navigator.navigate(Direction.DOWN)
        assert QApplication.focusWidget() is wide
        navigator.navigate(Direction.UP)
        landed = QApplication.focusWidget()
        assert landed is left, f"Up after Down went to {landed.text()!r}, not back"
    finally:
        keyboard.stop()
        gamepad.stop()


def test_the_way_back_is_only_the_pads_own_last_move(placed: QWidget) -> None:
    """Once the focus has moved by anything but that press, the rule decides again (T175).

    The same three buttons: the pad goes Down from the left one, then the focus
    is put on the wide one by other means -- a click -- after being elsewhere;
    Up is the rule's own answer, the button over the wide one's middle.
    """
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction

    left, right, wide = _narrow_pair_over_a_wide_one(placed)
    navigator, keyboard, gamepad = install_gamepad_navigation(placed)
    placed.show()
    placed.activateWindow()
    process_events()
    try:
        left.setFocus()
        process_events()
        navigator.navigate(Direction.DOWN)
        right.setFocus()
        wide.setFocus()
        process_events()
        navigator.navigate(Direction.UP)
        assert QApplication.focusWidget() is right, "an old move was undone after a click"
    finally:
        keyboard.stop()
        gamepad.stop()


def _edges_on(widget: QWidget, win: QWidget) -> tuple[int, int, int, int]:
    """`widget`'s left, top, right and bottom in `win`, all of it, shown or not."""
    from PySide6.QtCore import QPoint

    p = widget.mapTo(win, QPoint(0, 0))
    return p.x(), p.y(), p.x() + widget.width(), p.y() + widget.height()


def _showing(widget: QWidget, win: QWidget) -> tuple[int, int, int, int] | None:
    """The part of `widget` that shows in `win`, cut by every scroll area it is in; None if none."""
    from PySide6.QtWidgets import QAbstractScrollArea

    left, top, right, bottom = _edges_on(widget, win)
    parent = widget.parentWidget()
    while parent is not None and parent is not win:
        area = parent.parentWidget()
        if isinstance(area, QAbstractScrollArea) and area.viewport() is parent:
            v_left, v_top, v_right, v_bottom = _edges_on(parent, win)
            left, top = max(left, v_left), max(top, v_top)
            right, bottom = min(right, v_right), min(bottom, v_bottom)
        parent = parent.parentWidget()
    return (left, top, right, bottom) if left < right and top < bottom else None


def _name(widget: QWidget) -> str:
    """A button's text, else its class: how a failure names a stop."""
    return widget.text() if isinstance(widget, QPushButton) else type(widget).__name__


def _rows_passed_over(
    here: tuple[int, int, int, int],
    landed: tuple[int, int, int, int],
    others: list[tuple[bool, tuple[int, int, int, int]]],
    down: bool,
) -> list[tuple[int, int, int, int]]:
    """Which of `others` lie wholly between `here` and `landed`, across the window's whole width.

    Each of `others` comes with whether it is inside `here`: a stop inside a
    box the press starts from is passed over from the box's leading edge, the
    top for Down and the bottom for Up, not from its far one.
    """
    over = []
    for inside, o in others:
        if down:
            start = here[1] if inside else here[3]
            if start <= o[1] and o[3] <= landed[1]:
                over.append(o)
        else:
            start = here[3] if inside else here[1]
            if o[3] <= start and landed[3] <= o[1]:
                over.append(o)
    return over


def _free_spot(
    rng: object, taken: list[tuple[int, int, int, int]], width: int, height: int, sizes: object
) -> tuple[int, int, int, int] | None:
    """A rectangle of a random size from `sizes` that overlaps none of `taken`, or None."""
    import random

    assert isinstance(rng, random.Random)
    for _attempt in range(60):
        w, h = sizes(rng)  # type: ignore[operator]
        if w > width or h > height:
            continue
        x, y = rng.randint(0, width - w), rng.randint(0, height - h)
        r = (x, y, x + w, y + h)
        if not any(r[0] < o[2] and o[0] < r[2] and r[1] < o[3] and o[1] < r[3] for o in taken):
            return r
    return None


def test_no_up_or_down_press_passes_over_a_row_in_any_layout(placed: QWidget) -> None:
    """Up and Down never go wholly past a stop to one beyond it, however the stops stand (T175).

    The property the ticket is about, over many layouts rather than one: in
    each of 60 made-up windows of 5 to 11 buttons of every size -- narrow ones,
    wide ones, strips the whole width -- and in two of every three a scrolled
    list of buttons, taller inside than its box and scrolled part way, that
    the pad itself stops on in most of them. One press Up and one Down from
    each stop that shows, and wherever the press lands: it shows, and no stop
    that showed lies wholly between the two, in any column. A press from the
    list counts the list's own rows from the list's leading edge: Down from it
    past its first showing row is a row passed over. A press that wraps at an
    edge lands behind and passes over nothing; one that lands on a row
    scrolled in from out of sight is measured where that row was before the
    press, so skipping a row that showed for one that did not is caught too.
    Left and Right are not held to it across the window: they keep to the
    start's own row (see
    `test_right_does_not_take_a_nearer_column_off_to_the_side_of_its_row`).

    The layouts are drawn from a fixed seed, so a failure is the same failure
    on every run. The count of presses where a jump was possible at all -- a
    stop wholly past the start with another wholly past that -- and the count
    of those made from a list the pad stops on keep the test from passing
    over layouts that could not show the defect. From one of a list's rows to
    another, only the list's own rows count: the list keeps the focus while
    anything in it lies that way, as a web page's scroll container does.
    """
    import random

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from yulon.ui.gamepad import Direction

    rng = random.Random(175)
    navigator, keyboard, gamepad = install_gamepad_navigation(placed)
    placed.show()
    placed.activateWindow()
    process_events()
    passed: list[str] = []
    unseen: list[str] = []
    presses = could_jump = from_a_list = 0
    try:
        for layout in range(60):
            board = QWidget(placed)
            board.setGeometry(0, 0, 600, 400)
            taken: list[tuple[int, int, int, int]] = []
            stops: list[QWidget] = []
            area = None
            if layout % 3:
                box = _free_spot(
                    rng, taken, 600, 400, lambda r: (r.randint(200, 600), r.randint(100, 240))
                )
                assert box is not None
                taken.append(box)
                width, height = box[2] - box[0], box[3] - box[1]
                content_height = height * rng.randint(2, 4)
                area = _scrolled_box(board, box[0], box[1], width, height, content_height)
                if rng.random() < 0.75:
                    area.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
                    stops.append(area)
                rows: list[tuple[int, int, int, int]] = []
                for i in range(rng.randint(3, 9)):
                    r = _free_spot(
                        rng,
                        rows,
                        width,
                        content_height,
                        lambda r: (r.randint(20, 300), r.randint(14, 40)),
                    )
                    if r is not None:
                        rows.append(r)
                        stops.append(
                            _placed(area.widget(), f"L{i}", r[0], r[1], r[2] - r[0], r[3] - r[1])
                        )
            for i in range(rng.randint(5, 11)):
                r = _free_spot(
                    rng,
                    taken,
                    600,
                    400,
                    lambda r: (600 if r.random() < 0.15 else r.randint(20, 320), r.randint(14, 60)),
                )
                if r is not None:
                    taken.append(r)
                    stops.append(_placed(board, f"B{i}", r[0], r[1], r[2] - r[0], r[3] - r[1]))
            board.show()
            process_events()
            scrolled = 0
            if area is not None:
                bar = area.verticalScrollBar()
                scrolled = rng.randint(0, bar.maximum())
                bar.setValue(scrolled)
            showing = {id(w): e for w in stops if (e := _showing(w, placed)) is not None}
            placed_at = {id(w): _edges_on(w, placed) for w in stops}
            for here in stops:
                if id(here) not in showing:
                    continue
                h = showing[id(here)]
                for direction in (Direction.UP, Direction.DOWN):
                    down = direction is Direction.DOWN
                    if area is not None:
                        area.verticalScrollBar().setValue(scrolled)
                    here.setFocus()
                    navigator._last_move = None
                    navigator.navigate(direction)
                    landed = QApplication.focusWidget()
                    presses += 1
                    others = [
                        (here.isAncestorOf(w), showing[id(w)])
                        for w in stops
                        if w is not here and id(w) in showing
                    ]
                    # Any stop at all past the start could be a landing that jumps.
                    if any(_rows_passed_over(h, o, others, down) for _i, o in others):
                        could_jump += 1
                        from_a_list += here is area
                    if landed is None or landed is here:
                        continue
                    what = f"layout {layout}: {direction.name} from {_name(here)} {h}"
                    if _showing(landed, placed) is None:
                        unseen.append(f"{what} to a stop out of sight")
                    # A row scrolled in from out of sight is measured where it
                    # was before the press: skipping a row that showed for one
                    # that did not is passing over it too.
                    to = showing.get(id(landed), placed_at.get(id(landed)))
                    if to is None:
                        continue
                    rest = [(i, o) for i, o in others if o != to]
                    if area is not None and area.isAncestorOf(here) and area.isAncestorOf(landed):
                        # From one of a list's rows to another the list keeps
                        # the focus, as a web page's scroll container does: it
                        # is its own column of rows, and what stands beside it
                        # is not between two of them.
                        rest = [
                            (here.isAncestorOf(w), showing[id(w)])
                            for w in stops
                            if area.isAncestorOf(w) and id(w) in showing and w not in (here, landed)
                        ]
                    over = _rows_passed_over(h, to, rest, down)
                    if over:
                        names = [_name(w) for w in stops if showing.get(id(w)) == over[0]]
                        passed.append(f"{what} to {_name(landed)} {to} over {names} {over[0]}")
            board.hide()
            board.deleteLater()
            process_events()
    finally:
        keyboard.stop()
        gamepad.stop()
    assert presses >= 900, f"only {presses} presses were made"
    assert could_jump >= 400, f"only {could_jump} presses had a row to jump over"
    assert from_a_list >= 20, f"only {from_a_list} presses from a list had a row to jump over"
    assert unseen == [], f"{len(unseen)} presses left the focus out of sight: {unseen[:5]}"
    assert passed == [], f"{len(passed)} presses passed over a row: " + "; ".join(passed[:5])
