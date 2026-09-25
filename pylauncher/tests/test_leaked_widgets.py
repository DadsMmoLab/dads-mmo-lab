"""The suite destroys the top-level widgets a test leaves behind (T109).

A widget whose child's signal is connected to a lambda that captures the widget
is a cycle Python's collector cannot see: the lambda lives in the C++
connection, the connection lives on the child, the child lives in the widget.
`CatalogView`, `ControllerView` and most panels are built that way, so every
one a test built stayed alive for the rest of the run. Measured 2026-09-24 on
the laptop: 29,422 live widgets by the middle of `test_controller_view.py`.

Nothing noticed until an app-wide restyle. `QApplication.setStyleSheet` and
`setStyle` re-polish EVERY live widget, and `test_theme.py`'s two app-level
tests spent minutes there. The autouse fixtures in `conftest.py` delete what a
test or module orphaned; these tests hold them to that, and to leaving
everything else be.
"""

from __future__ import annotations

import gc
import weakref

import pytest
import shiboken6
from PySide6.QtWidgets import (
    QApplication,
    QCompleter,
    QDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from tests import conftest

LEFT_BEHIND = "t109-left-behind-by-the-test-before"
_the_first_half_ran: list[bool] = []


def _a_widget_only_its_own_signal_keeps_alive() -> QWidget:
    """The shape every leaking view has: a child whose slot is a lambda holding the parent."""
    widget = QWidget()
    button = QPushButton("press", widget)
    QVBoxLayout(widget).addWidget(button)
    button.clicked.connect(lambda: widget.setWindowTitle("pressed"))
    return widget


def _top_levels_named(name: str) -> list[QWidget]:
    return [w for w in QApplication.topLevelWidgets() if w.objectName() == name]


def _address(widget: QWidget) -> int:
    return int(shiboken6.getCppPointer(widget)[0])


def test_the_collector_cannot_free_a_widget_its_own_lambda_holds(qapp: QApplication) -> None:
    """The premise: without the fixture, dropping the last reference frees nothing.

    If PySide ever learns to collect this cycle, the fixture is doing no work and
    this test says so first.
    """
    name = "t109-premise"
    before = conftest.top_level_widget_snapshot()
    _a_widget_only_its_own_signal_keeps_alive().setObjectName(name)
    gc.collect()
    assert len(_top_levels_named(name)) == 1
    conftest.destroy_the_widgets_left_behind(before)
    assert _top_levels_named(name) == []


def test_only_the_orphans_made_since_the_snapshot_are_destroyed(qapp: QApplication) -> None:
    earlier = QWidget()
    before = conftest.top_level_widget_snapshot()

    orphan = _a_widget_only_its_own_signal_keeps_alive()
    keeper = QWidget()
    owned = QDialog(keeper)  # a window, but its parent decides when it dies

    destroyed = conftest.destroy_the_widgets_left_behind(
        {**before, _address(keeper): weakref.ref(keeper)}
    )

    assert destroyed == 1, "only the orphan was new and not exempted"
    assert not shiboken6.isValid(orphan)
    assert shiboken6.isValid(earlier), "a widget alive before the test is not the test's to delete"
    assert shiboken6.isValid(owned), "a parented window goes with its parent, not before it"
    assert shiboken6.isValid(keeper)


def test_a_new_widget_at_a_dead_widgets_address_is_not_mistaken_for_it(
    qapp: QApplication,
) -> None:
    """Qt reuses a freed widget's address; the snapshot must not spare the newcomer.

    Reuse is up to the allocator, so it is staged: the snapshot is written by
    hand with the new widget's address against the weak reference of a widget
    that has since been freed.
    """
    gone = QWidget()
    dead = weakref.ref(gone)
    del gone
    gc.collect()
    assert dead() is None

    newcomer = _a_widget_only_its_own_signal_keeps_alive()
    assert conftest.destroy_the_widgets_left_behind({_address(newcomer): dead}) == 1
    assert not shiboken6.isValid(newcomer)


def test_an_address_whose_wrapper_is_another_widget_is_not_a_match(qapp: QApplication) -> None:
    """The other shape of reuse: the old wrapper is still referenced, so the weakref is live.

    A wrapper outlives its C++ object while anything holds it; the entry must
    return THIS wrapper, not merely some wrapper.
    """
    someone_else = QWidget()
    newcomer = _a_widget_only_its_own_signal_keeps_alive()
    snapshot = {
        _address(newcomer): weakref.ref(someone_else),
        _address(someone_else): weakref.ref(someone_else),
    }

    assert conftest.destroy_the_widgets_left_behind(snapshot) == 1
    assert not shiboken6.isValid(newcomer)
    assert shiboken6.isValid(someone_else), "in the snapshot at its own address, so spared"


def test_a_parentless_widget_qt_made_for_itself_is_left_to_qt(qapp: QApplication) -> None:
    """A `QCompleter`'s popup: a top-level with no parent, which the completer deletes.

    Deleting it here would leave the completer holding a freed pointer. Without
    the `createdByPython` filter it is exactly what the teardown would pick.
    """
    before = conftest.top_level_widget_snapshot()
    completer = QCompleter(["alpha", "beta"])
    popup = completer.popup()
    assert popup.parentWidget() is None
    assert any(w is popup for w in QApplication.topLevelWidgets())
    assert not shiboken6.createdByPython(popup)

    conftest.destroy_the_widgets_left_behind(before)

    assert shiboken6.isValid(popup)
    assert completer.popup() is popup


def test_the_fixture_body_deletes_what_its_test_left(qapp: QApplication) -> None:
    """The very generator both autouse fixtures run, driven inside one test."""
    name = "t109-inside-one-test"
    body = conftest.destroying_what_is_left_behind()
    next(body)
    _a_widget_only_its_own_signal_keeps_alive().setObjectName(name)
    gc.collect()
    assert len(_top_levels_named(name)) == 1
    with pytest.raises(StopIteration):
        next(body)
    assert _top_levels_named(name) == []


def test_both_fixtures_run_for_a_test_that_did_not_ask_for_them(
    request: pytest.FixtureRequest,
) -> None:
    assert "_widgets_a_test_leaves_behind_are_destroyed" in request.fixturenames
    assert "_widgets_a_module_leaves_behind_are_destroyed" in request.fixturenames


def test_this_test_leaves_a_widget_behind(qapp: QApplication) -> None:
    """Half of an end-to-end pair: the next test checks the fixture removed this one."""
    _a_widget_only_its_own_signal_keeps_alive().setObjectName(LEFT_BEHIND)
    gc.collect()
    assert len(_top_levels_named(LEFT_BEHIND)) == 1
    _the_first_half_ran.append(True)


def test_the_widget_the_test_before_left_is_gone(qapp: QApplication) -> None:
    """The other half: across a real test boundary, pytest's own teardown did the work."""
    if not _the_first_half_ran:
        pytest.skip("test_this_test_leaves_a_widget_behind did not run first (-k or reordering)")
    assert _top_levels_named(LEFT_BEHIND) == []
