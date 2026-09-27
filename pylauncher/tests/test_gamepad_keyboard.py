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
