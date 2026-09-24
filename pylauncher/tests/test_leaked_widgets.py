"""The suite destroys the top-level widgets a test leaves behind (T109).

A widget whose child's signal is connected to a lambda that captures the widget
is a cycle Python's collector cannot see: the lambda lives in the C++
connection, the connection lives on the child, the child lives in the widget.
`CatalogView`, `ControllerView` and most panels are built that way, so every
one a test built stayed alive for the rest of the run. Measured 2026-09-24 on
the laptop: 29,422 live widgets by the middle of `test_controller_view.py`.

Nothing noticed until an app-wide restyle. `QApplication.setStyleSheet` and
`setStyle` re-polish EVERY live widget, and `test_theme.py`'s two app-level
tests spent minutes there. The autouse fixture in `conftest.py` deletes what a
test orphaned; these tests hold it to that, and to leaving everything else be.
"""

from __future__ import annotations

import gc

import shiboken6
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QVBoxLayout, QWidget

from tests import conftest

LEFT_BEHIND = "t109-left-behind-by-the-test-before"


def _a_widget_only_its_own_signal_keeps_alive() -> QWidget:
    """The shape every leaking view has: a child whose slot is a lambda holding the parent."""
    widget = QWidget()
    button = QPushButton("press", widget)
    QVBoxLayout(widget).addWidget(button)
    button.clicked.connect(lambda: widget.setWindowTitle("pressed"))
    return widget


def _top_levels_named(name: str) -> list[QWidget]:
    return [w for w in QApplication.topLevelWidgets() if w.objectName() == name]


def test_the_collector_cannot_free_a_widget_its_own_lambda_holds(qapp: QApplication) -> None:
    """The premise: without the fixture, dropping the last reference frees nothing.

    If PySide ever learns to collect this cycle, the fixture is doing no work and
    this test says so first.
    """
    name = "t109-premise"
    before = conftest.top_level_widget_addresses()
    _a_widget_only_its_own_signal_keeps_alive().setObjectName(name)
    gc.collect()
    assert len(_top_levels_named(name)) == 1
    conftest.destroy_the_widgets_left_behind(before)
    assert _top_levels_named(name) == []


def test_only_the_orphans_made_since_the_snapshot_are_destroyed(qapp: QApplication) -> None:
    earlier = QWidget()
    before = conftest.top_level_widget_addresses()

    orphan = _a_widget_only_its_own_signal_keeps_alive()
    keeper = QWidget()
    owned = QDialog(keeper)  # a window, but its parent decides when it dies

    destroyed = conftest.destroy_the_widgets_left_behind(before | {_address(keeper)})

    assert destroyed == 1, "only the orphan was new and not exempted"
    assert not shiboken6.isValid(orphan)
    assert shiboken6.isValid(earlier), "a widget alive before the test is not the test's to delete"
    assert shiboken6.isValid(owned), "a parented window goes with its parent, not before it"
    assert shiboken6.isValid(keeper)


def test_this_test_leaves_a_widget_behind(qapp: QApplication) -> None:
    """Half of a pair: the next test checks the fixture removed this one."""
    _a_widget_only_its_own_signal_keeps_alive().setObjectName(LEFT_BEHIND)
    gc.collect()
    assert len(_top_levels_named(LEFT_BEHIND)) == 1


def test_the_widget_the_test_before_left_is_gone(qapp: QApplication) -> None:
    """The other half: the autouse fixture, not a helper nobody calls, did the work."""
    assert _top_levels_named(LEFT_BEHIND) == []


def _address(widget: QWidget) -> int:
    return int(shiboken6.getCppPointer(widget)[0])
