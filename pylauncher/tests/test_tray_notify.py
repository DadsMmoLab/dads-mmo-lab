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
    # A loop that reads "starting" for a poll between two crashes is still one crash loop.
    view.realm_badge.set_status("starting")
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


def test_a_world_that_stops_under_a_running_database_is_said(
    tray: YulonTray, window: FakeWindow
) -> None:
    """The world crashed, the database and login are still up: the badge reads PARTLY UP."""
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    window.hide()
    view.realm_badge.set_status("partial")
    assert _messages(tray) == [
        (
            "WotLK is only partly up",
            "Part of it stopped, and Yu'lon did not stop it. Click to see its Server tab.",
        )
    ]


def test_a_world_that_drops_through_starting_is_still_said(
    tray: YulonTray, window: FakeWindow
) -> None:
    """yulon-win11 2026-10-07: the verdict lands first and T451 reads the dying world as
    "starting" for one poll, so the badge goes running, starting, partial."""
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    window.hide()
    view.realm_badge.set_status("starting")
    view.realm_badge.set_status("partial")
    assert [title for title, _ in _messages(tray)] == ["WotLK is only partly up"]


def test_a_restart_of_ours_is_neither_a_crash_nor_red(tray: YulonTray, window: FakeWindow) -> None:
    """ "restarting" is the hold of the Tuning tab's Restart (T188), not a crash."""
    view = _add(window, FakeView("WotLK", "/srv/a", "running"))
    window.hide()
    view.realm_badge.set_status("restarting")
    assert tray.state == "between"
    view.realm_badge.set_status("running")
    assert _messages(tray) == []


def test_a_realm_that_came_up_while_followed_is_watched_from_then(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, FakeView("WotLK", "/srv/a", "stopped"))
    window.hide()
    tray.start(view)
    view.realm_badge.set_status("running")
    view.realm_badge.set_status("stopped")
    assert [title for title, _ in _messages(tray)] == ["WotLK went offline"]


def test_making_the_icon_names_the_app_id_for_windows(
    window: FakeWindow, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the name Windows drops every notification (yulon-win11, 2026-10-07)."""
    from yulon import autostart

    asked: list[int] = []
    monkeypatch.setattr(autostart, "register_app_id", lambda **k: asked.append(1) or True)
    made = YulonTray(window, icon_factory=FakeTrayIcon, available=lambda: True)
    made.install()
    try:
        assert asked == [1]
    finally:
        made.uninstall()


def test_a_click_after_two_servers_said_something_opens_yulon_not_the_wrong_tab(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Adversarial review [medium]: the click carries no identity, so after A then B it
    opened B even when the player clicked A's notification."""
    a = _add(window, FakeView("WotLK", "/srv/a", "running"))
    b = _add(window, FakeView("TBC", "/srv/b", "running"))
    window.hide()
    a.realm_badge.set_status("loop")
    b.realm_badge.set_status("loop")
    assert isinstance(tray.icon, FakeTrayIcon)
    tray.icon.messageClicked.emit()
    assert window.shown_tabs == [], "a click opened one server's tab for two notifications"
    assert window.isVisible()
    # Said again for one server only: its click goes to its tab.
    window.hide()
    a.realm_badge.set_status("running")
    a.realm_badge.set_status("loop")
    tray.icon.messageClicked.emit()
    assert window.shown_tabs == [("game-wotlk", Path("/srv/a"))]
