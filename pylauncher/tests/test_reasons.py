"""`ReasonLine` and `set_enabled_why`: a greyed press says why, on screen and on hover (T195).

A Steam Deck has no hover and a disabled button takes no focus, so a reason
only in a tooltip is a reason nobody on a Deck reads. The line under the
presses shows it; the tooltip keeps it for a mouse.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture
def row(qapp: object) -> Any:
    """A widget holding two presses, each with a tooltip of its own, and a line watching both."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from yulon.ui.widgets.reasons import ReasonLine

    root = QWidget()
    layout = QVBoxLayout(root)
    first = QPushButton("Apply", root)
    first.setToolTip("Apply the plan")
    second = QPushButton("Restore", root)
    second.setToolTip("Restore the backup")
    line = ReasonLine(root)
    for widget in (first, second, line):
        layout.addWidget(widget)
    line.watch(first)
    line.watch(second)
    yield root, first, second, line
    root.deleteLater()


def test_a_line_with_nothing_to_explain_is_hidden(row: Any) -> None:
    _root, first, second, line = row
    assert first.isEnabled() and second.isEnabled()
    assert line.isHidden()
    assert line.text() == ""


def test_disabling_one_press_with_a_reason_shows_it_on_the_line_and_the_tooltip(row: Any) -> None:
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, second, line = row

    set_enabled_why(first, "Press Show plan first.", line)

    assert not first.isEnabled()
    assert first.toolTip() == "Press Show plan first."
    assert not line.isHidden()
    assert line.text() == "Press Show plan first."
    assert second.isEnabled() and second.toolTip() == "Restore the backup"


def test_two_reasons_are_one_per_line_in_the_order_the_presses_were_watched(row: Any) -> None:
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, second, line = row

    set_enabled_why(second, "Pick a backup first.", line)
    set_enabled_why(first, "Press Show plan first.", line)

    assert line.text() == "Press Show plan first.\nPick a backup first."


def test_the_same_reason_on_two_presses_is_said_once(row: Any) -> None:
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, second, line = row

    set_enabled_why(first, "Choose a character in the list first.", line)
    set_enabled_why(second, "Choose a character in the list first.", line)

    assert line.text() == "Choose a character in the list first."


def test_enabling_hides_the_line_and_gives_the_press_its_own_tooltip_back(row: Any) -> None:
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, second, line = row
    set_enabled_why(first, "Press Show plan first.", line)
    set_enabled_why(second, "Pick a backup first.", line)

    set_enabled_why(first, None, line)

    assert first.isEnabled()
    assert first.toolTip() == "Apply the plan"
    assert line.text() == "Pick a backup first."

    set_enabled_why(second, None)

    assert second.toolTip() == "Restore the backup"
    assert line.isHidden()


def test_a_press_enabled_by_plain_set_enabled_drops_its_reason(row: Any) -> None:
    """Most of the view re-enables with `setEnabled(True)`; a reason must not outlive it."""
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, _second, line = row
    set_enabled_why(first, "Wait: Start is running.", line)

    first.setEnabled(True)

    assert first.toolTip() == "Apply the plan"
    assert line.isHidden()
    first.setEnabled(False)
    assert line.isHidden(), "a reason came back that nobody gave again"


def test_a_hidden_press_has_no_reason_on_the_line(row: Any) -> None:
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, second, line = row
    second.setVisible(False)

    set_enabled_why(second, "Pick a backup first.", line)

    assert line.isHidden(), line.text()

    set_enabled_why(first, "Press Show plan first.", line)
    assert line.text() == "Press Show plan first."

    second.setVisible(True)
    assert line.text() == "Press Show plan first.\nPick a backup first."


def test_a_standing_reason_is_given_whenever_the_press_is_greyed(row: Any) -> None:
    """For a press another widget greys by itself (the log panel's Stop)."""
    from PySide6.QtWidgets import QPushButton

    root, _first, _second, line = row
    stop = QPushButton("Stop", root)
    stop.setToolTip("Stop following")
    stop.setEnabled(False)

    line.watch(stop, standing="Nothing is being followed.")

    assert stop.toolTip() == "Nothing is being followed."
    assert line.text() == "Nothing is being followed."
    stop.setEnabled(True)
    assert stop.toolTip() == "Stop following"
    assert line.isHidden()
    stop.setEnabled(False)
    assert stop.toolTip() == "Nothing is being followed."
    assert line.text() == "Nothing is being followed."


def test_a_tooltip_someone_else_set_while_greyed_is_left_alone(row: Any) -> None:
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, _second, line = row
    set_enabled_why(first, "Press Show plan first.", line)
    first.setToolTip("A newer tooltip")

    set_enabled_why(first, None, line)

    assert first.toolTip() == "A newer tooltip"


def test_the_line_is_muted_wrapped_selectable_and_styled_by_its_own_name(row: Any) -> None:
    from PySide6.QtCore import Qt

    from yulon.ui.theme import COLOR_TEXT_MUTED

    _root, _first, _second, line = row
    assert line.wordWrap()
    assert line.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    sheet = line.styleSheet()
    assert sheet.startswith(f"QLabel#{line.objectName()}"), sheet
    assert COLOR_TEXT_MUTED in sheet


def test_hiding_the_box_around_a_press_takes_its_reason_off_the_line(row: Any) -> None:
    """Fix round 1, F3: the Docker banner hides its reinstall press without telling it."""
    from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

    from yulon.ui.widgets.reasons import set_enabled_why

    root, _first, _second, line = row
    box = QWidget(root)
    QVBoxLayout(box)
    root.layout().addWidget(box)
    inside = QPushButton("Reinstall", root)
    set_enabled_why(inside, "Wait: Start is running.", line)
    # Moved into the box after it was watched, as a layout moves a press.
    box.layout().addWidget(inside)
    assert line.text() == "Wait: Start is running."

    box.setVisible(False)
    assert line.isHidden(), line.text()

    box.setVisible(True)
    assert line.text() == "Wait: Start is running."


def test_an_empty_reason_is_refused_and_changes_nothing(row: Any) -> None:
    """Fix round 1, F4: `""` greyed the press with nothing to say."""
    from yulon.ui.widgets.reasons import set_enabled_why

    _root, first, _second, line = row

    with pytest.raises(ValueError):
        set_enabled_why(first, "", line)
    with pytest.raises(ValueError):
        set_enabled_why(first, "   ", line)

    assert first.isEnabled()
    assert first.toolTip() == "Apply the plan"
    assert line.isHidden()
