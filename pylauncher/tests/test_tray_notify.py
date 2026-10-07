"""Tray notifications (T540): a crash loop, a realm that went down unasked, a failed Start.

Said only while the player is not looking at the window, once per change, and a
click on one opens that server's tab.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QApplication

from tests.test_tray import FakeTrayIcon, FakeView, FakeWindow, _add
from yulon.ui.tray import YulonTray


@pytest.fixture
def window(qapp: Any) -> Iterator[FakeWindow]:
    win = FakeWindow()
    win.show()
    yield win
    win.hide()
    win.deleteLater()


@pytest.fixture
def tray(window: FakeWindow) -> Iterator[YulonTray]:
    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: True)
    made.install()
    made.note_seen = True
    yield made
    made.uninstall()


def _messages(tray: YulonTray) -> list[tuple[str, str]]:
    assert isinstance(tray.icon, FakeTrayIcon)
    return tray.icon.messages


def test_a_crash_loop_is_said_while_yulon_is_in_the_tray(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    window.hide()
    view.realm_badge.set_status("loop")
    assert _messages(tray) == [
        ("WotLK is crash-looping", "Its world keeps stopping. Click to see its Server tab.")
    ]
    # A loop that flips to restarting and back between polls is still one crash loop.
    view.realm_badge.set_status("restarting")
    view.realm_badge.set_status("loop")
    assert len(_messages(tray)) == 1, "one crash loop was said more than once"


def test_a_realm_that_goes_down_unasked_is_said_and_a_stop_of_ours_is_not(
    tray: YulonTray, window: FakeWindow
) -> None:
    crashed = _add(window, FakeView("WotLK", "/srv/a", "running"))
    stopped = _add(window, FakeView("TBC", "/srv/b", "running"))
    window.hide()
    stopped.stop_server()  # the badge says "stopping" first: ours
    stopped.realm_badge.set_status("stopped")
    assert _messages(tray) == []
    crashed.realm_badge.set_status("stopped")
    assert _messages(tray) == [
        ("WotLK went offline", "Yu'lon did not stop it. Click to see its Server tab.")
    ]


def test_a_failed_start_from_the_tray_is_said(tray: YulonTray, window: FakeWindow) -> None:
    view = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    window.hide()
    tray.start(view)
    view.action_failed.emit("This server needs port(s) 3724, which another install is using.")
    assert _messages(tray) == [
        (
            "Vanilla: something went wrong",
            "This server needs port(s) 3724, which another install is using.",
        )
    ]


def test_clicking_a_notification_opens_that_servers_tab(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    window.hide()
    view.realm_badge.set_status("loop")
    assert isinstance(tray.icon, FakeTrayIcon)
    tray.icon.messageClicked.emit()
    assert window.shown_tabs == [("game-wotlk", Path("/srv/a"))]


def test_nothing_is_said_while_the_player_looks_at_the_window(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    window.activateWindow()
    QApplication.processEvents()
    assert window.isActiveWindow()
    view.realm_badge.set_status("loop")
    assert _messages(tray) == [], "the window already says it"


def test_starting_and_stopping_say_nothing(tray: YulonTray, window: FakeWindow) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "stopped"))
    window.hide()
    for status in ("starting", "running", "stopping", "stopped", "unknown", "running"):
        view.realm_badge.set_status(status)
    assert _messages(tray) == []


def test_a_tray_turned_off_says_nothing(tray: YulonTray, window: FakeWindow) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    tray.set_keep_in_tray(False)
    window.hide()
    view.realm_badge.set_status("loop")
    assert _messages(tray) == []
