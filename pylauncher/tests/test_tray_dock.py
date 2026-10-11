"""T691: on a Mac, a click on the Dock icon brings the window back from the tray.

What Qt does on that click (qtbase 6.11, read, not run here): Cocoa asks the app
delegate `applicationShouldHandleReopen:hasVisibleWindows:`, and Qt's delegate
answers with `QWindowSystemInterface::handleApplicationStateChanged(ApplicationActive,
forcePropagate=true)` (`qcocoaapplicationdelegate.mm`). `setApplicationState` then
sends `QEvent::ApplicationActivate` (then a `QApplicationStateChangeEvent`) to the
application object and emits `applicationStateChanged` EVEN WHEN the app already
was active (`qguiapplication.cpp`). Nothing else on macOS repeats the state: a
Cmd+Tab, the launch, or the tray's own flyout raising itself is one change from
inactive to active. So "active, again" is the Dock (LyX reads it the same way).

The tests send that `QEvent.ApplicationActivate` to the real application object,
through the tray's real application-wide event filter, with the platform said to
be macOS where the behaviour is meant to run.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator
from typing import Any

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QWidget

from tests.test_tray import FakeTrayIcon, FakeWindow, _close_by_hand
from yulon.ui import tray as tray_module
from yulon.ui.tray import YulonTray

ACTIVE = Qt.ApplicationState.ApplicationActive
INACTIVE = Qt.ApplicationState.ApplicationInactive


def _state(state: Qt.ApplicationState) -> None:
    """What Qt's `setApplicationState` sends to the application object, sent there.

    `QEvent.ApplicationActivate` or `ApplicationDeactivate`: the state-change event
    after it is not in PySide6 (no `QApplicationStateChangeEvent`), and on macOS
    Qt's Cocoa plugin only ever sets those two states.
    """
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    kind = QEvent.Type.ApplicationActivate if state == ACTIVE else QEvent.Type.ApplicationDeactivate
    QApplication.sendEvent(app, QEvent(kind))


def _dock_click_on_an_inactive_app() -> None:
    """The app becomes active (one change), then the Dock's reopen repeats it."""
    _state(ACTIVE)
    _state(ACTIVE)


@pytest.fixture
def mac(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")


@pytest.fixture
def window(qapp: Any) -> Iterator[FakeWindow]:
    win = FakeWindow()
    win.show()
    yield win
    win.hide()
    win.deleteLater()


@pytest.fixture
def raised() -> list[QWidget]:
    return []


@pytest.fixture
def tray(window: FakeWindow, raised: list[QWidget]) -> Iterator[YulonTray]:
    def bring_forward(widget: QWidget) -> None:
        raised.append(widget)
        tray_module._bring_forward(widget)

    made = YulonTray(
        window, icon_factory=FakeTrayIcon, available=lambda: True, bring_forward=bring_forward
    )
    made.install()
    made.note_seen = True
    yield made
    made.uninstall()


def _hide_into_the_tray(window: FakeWindow) -> None:
    _close_by_hand(window)
    assert window.isHidden()


def test_a_dock_click_brings_the_window_back_from_the_tray(
    mac: None,
    tray: YulonTray,
    window: FakeWindow,
    raised: list[QWidget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ticket: with Keep in tray on, the Dock re-activated Yu'lon and nothing showed.

    Mutation: drop the "active again" rule (any activation shows it): the Cmd+Tab
    step below shows the window. Mutation: drop the handler: the Dock step does not.
    Mutation: a deactivation not counted: Cmd+Tab back reads as the Dock.
    """
    # Closed with Yu'lon in front, so the "went to another app" step is what resets it.
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: ACTIVE))
    _hide_into_the_tray(window)
    _state(INACTIVE)  # the player went to another app
    _state(ACTIVE)  # Cmd+Tab back: one change, not the Dock
    assert window.isHidden(), "a plain activation (Cmd+Tab) pulled the window out of the tray"
    assert raised == []
    _state(ACTIVE)  # the Dock's reopen: active, again
    assert window.isVisible(), "the Dock click left the window in the tray"
    assert raised == [window], "not through the tray's own Open Yu'lon path"


def test_a_dock_click_while_yulon_is_already_active_brings_it_back(
    mac: None,
    tray: YulonTray,
    window: FakeWindow,
    raised: list[QWidget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed with the red button, Yu'lon stays the active app: one click is the Dock.

    Mutation: start the count from "inactive" instead of the app's state when the
    window went into the tray: the first click does nothing and the player clicks twice.
    """
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: ACTIVE))
    _hide_into_the_tray(window)
    _state(ACTIVE)
    assert window.isVisible()
    assert raised == [window]


@pytest.mark.parametrize("system", ["linux", "win32"])
def test_off_a_mac_a_repeated_activation_changes_nothing(
    system: str,
    tray: YulonTray,
    window: FakeWindow,
    raised: list[QWidget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no Dock: the tray icon and a second launch are the ways back there.

    Mutation: drop the macOS check: the window comes out of the tray here.
    """
    monkeypatch.setattr(sys, "platform", system)
    _hide_into_the_tray(window)
    _state(INACTIVE)
    _dock_click_on_an_inactive_app()
    assert window.isHidden()
    assert raised == []


def test_a_dock_click_with_the_window_up_is_left_to_macos(
    mac: None,
    tray: YulonTray,
    window: FakeWindow,
    raised: list[QWidget],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a window in the tray is brought back: a window that is up is not raised again.

    The window back some other way (a second launch, a dialog) lets go of the
    app on its own Show, so the Dock after it is macOS's alone. Mutation: keep
    watching the app once the window is back (the filter's first `_watch_app(False)`):
    the window up is raised again.
    """
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: ACTIVE))
    _hide_into_the_tray(window)
    window.show()  # back some other way
    _state(ACTIVE)  # the Dock, with Yu'lon already active
    assert raised == []
    assert not tray._watching_app


def test_with_the_window_up_the_app_is_not_watched_at_all(
    mac: None, tray: YulonTray, window: FakeWindow, raised: list[QWidget]
) -> None:
    """The ordinary case: Yu'lon's window is up, the player clicks the Dock. Nothing of ours."""
    _state(INACTIVE)
    _dock_click_on_an_inactive_app()
    assert raised == []
    assert not tray._watching_app


def test_a_sign_in_start_stays_in_the_tray_until_the_dock_is_clicked(
    mac: None, qapp: Any, raised: list[QWidget], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--tray`: the launch activates the app once, and that is not a reason to show.

    Qt's delegate activates a launched app (`applicationDidFinishLaunching`); that is
    one change from inactive. The Dock afterwards still works.
    """
    # A process just started is inactive (Qt's own starting value) until that activation.
    monkeypatch.setattr(QGuiApplication, "applicationState", staticmethod(lambda: INACTIVE))
    window = FakeWindow()

    def bring_forward(widget: QWidget) -> None:
        raised.append(widget)
        tray_module._bring_forward(widget)

    made = YulonTray(
        window, icon_factory=FakeTrayIcon, available=lambda: True, bring_forward=bring_forward
    )
    made.install()
    try:
        made.start_hidden()
        assert window.isHidden()
        _state(ACTIVE)  # the launch's activation
        assert window.isHidden(), "the sign-in start showed the window"
        assert raised == []
        _state(ACTIVE)  # the Dock
        assert window.isVisible()
    finally:
        made.uninstall()
        window.hide()
        window.deleteLater()
