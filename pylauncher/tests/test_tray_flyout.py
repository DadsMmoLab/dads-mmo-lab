"""The tray's left-click flyout (T540, Option B): one card per server, Play and Start.

Uses `test_tray.py`'s stand-ins: a window carrying what `main.build_window()`
puts on the real one, and tabs that are badges and two buttons.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtWidgets import QApplication, QPushButton, QSystemTrayIcon

from tests.test_tray import FakeTrayIcon, FakeView, FakeWindow, _add
from yulon import dashboard
from yulon.ui.tray import YulonTray
from yulon.ui.tray_flyout import TrayFlyout, card_detail, flyout_position


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
    flyout = made.flyout
    if flyout is not None:
        flyout.hide()
    made.uninstall()


def _open(tray: YulonTray) -> TrayFlyout:
    tray.toggle_flyout()
    flyout = tray.flyout
    assert flyout is not None and flyout.isVisible()
    return flyout


def _card(flyout: TrayFlyout, title: str) -> Any:
    for card in flyout.cards():
        if card.title.text() == title:
            return card
    raise AssertionError(f"no card {title!r} in {[c.title.text() for c in flyout.cards()]}")


def _visible_buttons(card: Any) -> dict[str, bool]:
    return {
        button.text(): button.isEnabled()
        for button in card.findChildren(QPushButton)
        if not button.isHidden()
    }


# --------------------------------------------------------------- pure parts


def test_the_detail_line_is_players_bots_and_uptime_when_known() -> None:
    up = dashboard.Verdict("up", players=3, bots=500, uptime=timedelta(hours=2, minutes=5))
    assert card_detail(up) == "3 players · 500 bots · up 2h 5m"
    assert card_detail(dashboard.Verdict("up", players=1, bots=1)) == "1 player · 1 bot"
    assert card_detail(dashboard.Verdict("up")) == "", "an unread count must not read as zero"
    assert card_detail(None) == ""


def test_the_flyout_sits_by_the_icon_and_inside_the_screen() -> None:
    screen = QRect(0, 0, 1920, 1040)  # a taskbar below 1040
    size = (360, 400)
    # A Windows taskbar icon at the bottom right: above it, right edges lined up.
    pos = flyout_position(QRect(1800, 1045, 24, 24), QPoint(0, 0), screen, size)
    assert pos == QPoint(1824 - 360, 1045 - 400 - 8)
    # A top panel (GNOME/KDE): below it.
    pos = flyout_position(QRect(1700, 2, 24, 24), QPoint(0, 0), screen, size)
    assert pos == QPoint(1724 - 360, 2 + 24 + 8)
    # No icon geometry (StatusNotifierItem): by the cursor, kept on the screen.
    pos = flyout_position(QRect(), QPoint(1910, 1030), screen, size)
    assert pos.x() + 360 <= 1920 and pos.y() + 400 <= 1040
    assert pos.x() >= 0 and pos.y() >= 0


# ----------------------------------------------------------------- the cards


def test_one_card_per_server_with_play_only_when_online(
    tray: YulonTray, window: FakeWindow
) -> None:
    up = _add(window, FakeView("WotLK", "/srv/a", "running"))
    up.last_verdict = dashboard.Verdict("up", players=2, bots=10, uptime=timedelta(minutes=7))
    _add(window, FakeView("TBC", "/srv/b", "starting"))
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    flyout = _open(tray)
    assert flyout.header.text() == "Yu'lon — 1 of 3 servers online"
    wotlk = _card(flyout, "WotLK")
    assert wotlk.pill.text() == "Realm online"
    assert wotlk.detail.text() == "2 players · 10 bots · up 7m"
    assert _visible_buttons(wotlk) == {"Play": True}
    tbc = _card(flyout, "TBC")
    assert tbc.pill.text() == "Starting"
    assert tbc.detail.isHidden()
    assert _visible_buttons(tbc) == {"Start": False}
    assert _visible_buttons(_card(flyout, "Vanilla")) == {"Start": True}


def test_play_opens_that_servers_client_launcher(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    flyout = _open(tray)
    _card(flyout, "WotLK").action.click()
    assert window.opened == [("game-wotlk", Path("/srv/a"))]
    assert not flyout.isVisible(), "the flyout stayed over the launcher it opened"


def test_start_presses_the_tabs_start_and_the_card_says_starting_at_once(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    flyout = _open(tray)
    _card(flyout, "Vanilla").action.click()
    assert view.starts == 1
    card = _card(flyout, "Vanilla")
    assert card.pill.text() == "Starting"
    assert _visible_buttons(card) == {"Start": False}
    assert tray.state == "between"


def test_start_is_greyed_with_the_tabs_own_reason(tray: YulonTray, window: FakeWindow) -> None:
    view = _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    view.start_button.setEnabled(False)
    view.start_button.setToolTip("Waiting for the module install to finish")
    card = _card(_open(tray), "Vanilla")
    assert _visible_buttons(card) == {"Start": False}
    assert card.action.toolTip() == "Waiting for the module install to finish"


def test_a_server_needing_attention_opens_its_tab(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "loop"))
    flyout = _open(tray)
    card = _card(flyout, "WotLK")
    assert card.pill.text() == "Crash loop"
    assert _visible_buttons(card) == {"Open": True}
    card.action.click()
    assert window.shown_tabs == [("game-wotlk", Path("/srv/a"))]


def test_the_footer_opens_yulon_and_quits(tray: YulonTray, window: FakeWindow) -> None:
    window.hide()
    flyout = _open(tray)
    flyout.open_button.click()
    assert window.isVisible()
    assert not flyout.isVisible()
    asked: list[int] = []
    tray.ask_to_quit = lambda: asked.append(1)  # type: ignore[method-assign]
    _open(tray).quit_button.click()
    assert asked == [1]


def test_the_settings_button_asks_for_the_settings(tray: YulonTray) -> None:
    asked: list[int] = []
    tray.open_settings = lambda: asked.append(1)  # type: ignore[method-assign]
    _open(tray).settings_button.click()
    assert asked == [1]


def test_a_click_on_the_icon_opens_the_flyout_and_a_second_closes_it(
    tray: YulonTray, window: FakeWindow
) -> None:
    assert isinstance(tray.icon, FakeTrayIcon)
    tray.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert tray.flyout is not None and tray.flyout.isVisible()
    tray.icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)
    assert not tray.flyout.isVisible()


def test_a_server_added_while_the_flyout_is_open_gets_a_card(
    tray: YulonTray, window: FakeWindow
) -> None:
    flyout = _open(tray)
    assert flyout.cards() == []
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    assert [card.title.text() for card in flyout.cards()] == ["WotLK"]


# ------------------------------------------------------------------- the pad


def test_the_flyout_is_driven_by_the_pad(tray: YulonTray, window: FakeWindow) -> None:
    """D-pad moves between the flyout's buttons, A presses, B closes it (`gamepad.py`)."""
    from yulon.ui.gamepad import Action, Direction, Navigator

    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    navigator = Navigator()
    flyout = _open(tray)
    QApplication.processEvents()
    assert QApplication.activeWindow() is flyout
    first = QApplication.focusWidget()
    assert first is _card(flyout, "WotLK").action, "the pad has nowhere to start from"
    assert navigator.navigate(Direction.DOWN)
    assert QApplication.focusWidget() is _card(flyout, "Vanilla").action
    navigator.perform(Action.CONFIRM)
    assert window.yulon_controllers[1].starts == 1
    assert navigator.perform(Action.BACK)
    assert not flyout.isVisible()
    navigator.deleteLater()


# ------------------------------------------- the count the cards read (no new read)


def test_a_tab_keeps_its_last_verdict_for_the_tray_and_drops_it_with_the_line(
    qapp: object, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`last_verdict` is the line's own reading: kept when shown, gone when the line goes."""
    from tests.test_controller_view import WOTLK, _Ps, _services
    from yulon import runner
    from yulon.ui.controller_view import ControllerView
    from yulon.ui.widgets.job import run_inline

    ps = _Ps()
    monkeypatch.setattr(runner, "run", ps)
    ps.names = "ac-database\nac-authserver\nac-worldserver\n"
    up = dashboard.Verdict("up", players=4, bots=500)
    services = _services(ps, tmp_path, [])
    services.dashboard = lambda: up
    view = ControllerView(WOTLK, services, status_poll_ms=0, job_runner=run_inline)
    view.refresh_status()
    view.refresh_verdict()
    assert view.last_verdict is up
    view._clear_the_verdict()
    assert view.last_verdict is None


def test_the_click_that_took_the_focus_away_does_not_open_it_again(tray: YulonTray) -> None:
    """Windows: clicking the icon to close the flyout first takes its focus, which closes it."""
    flyout = _open(tray)
    flyout.hide()
    flyout.dismissed.emit()
    tray.toggle_flyout()
    assert not flyout.isVisible(), "the closing click opened it again"
