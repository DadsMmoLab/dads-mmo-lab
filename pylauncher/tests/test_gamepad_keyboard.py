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
