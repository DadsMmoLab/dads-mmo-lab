"""The TortoiseBots bot dashboard from the tray (T540, the owner's TortoiseBots note).

A Tortoise server's flyout card and its rows in the right-click menu carry the
dashboard in three states: up with the Bots tab's switch on (open it, through the
Bots tab's own Open), up with the switch off (go to the switch), and not up
(greyed: start the server first). Servers without a dashboard show nothing.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtWidgets import QCheckBox, QMenu, QPushButton

from tests.test_tray import FakeTrayIcon, FakeView, FakeWindow, _add
from yulon.ui import tray as tray_module
from yulon.ui.tray import YulonTray


class TortoiseView(FakeView):
    """A Tortoise tab: a dashboard seam, the Bots tab's switch and its Open button."""

    def __init__(self, status: str, *, switch_on: bool, openable: bool = True) -> None:
        super().__init__("Tortoise", "/srv/t", status)
        self.services.bot_dashboard = object()
        self.dashboard_switch = QCheckBox("Bot dashboard")
        self.dashboard_switch.setChecked(switch_on)
        self.open_dashboard_button = QPushButton("Open bot dashboard")
        self.open_dashboard_button.setEnabled(switch_on and openable)
        if switch_on and not openable:
            self.open_dashboard_button.setToolTip("Rebuild the bot dashboard first")
        self.opened_dashboard = 0
        self.shown_switch = 0

    def open_bot_dashboard(self) -> None:
        self.opened_dashboard += 1

    def show_bot_dashboard_switch(self) -> None:
        self.shown_switch += 1


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
    if made.flyout is not None:
        made.flyout.hide()
    made.uninstall()


def _dash_action(menu: QMenu) -> Any:
    found = [a for a in menu.actions() if "dashboard" in a.text().lower()]
    assert len(found) == 1, [a.text() for a in menu.actions()]
    return found[0]


def _card(tray: YulonTray) -> Any:
    tray.toggle_flyout()
    assert tray.flyout is not None
    return next(c for c in tray.flyout.cards() if c.title.text() == "Tortoise")


# ---------------------------------------------------------------- up, switch on


def test_up_with_the_switch_on_opens_the_dashboard_from_the_menu(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, TortoiseView("running", switch_on=True))
    menu = tray.build_menu()
    action = _dash_action(menu)
    assert action.text() == "Bot dashboard"
    assert action.isEnabled()
    rows = [a.text() for a in menu.actions()]
    assert rows.index("Bot dashboard") == rows.index("Tortoise — Realm online") + 1, rows
    action.trigger()
    assert view.opened_dashboard == 1


def test_up_with_the_switch_on_opens_the_dashboard_from_the_card(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, TortoiseView("running", switch_on=True))
    card = _card(tray)
    assert not card.dashboard.isHidden()
    assert card.dashboard.text() == "Bot dashboard"
    assert card.dashboard.isEnabled()
    card.dashboard.click()
    assert view.opened_dashboard == 1


# --------------------------------------------------------------- up, switch off


def test_up_with_the_switch_off_offers_to_turn_it_on_at_the_bots_tab(
    tray: YulonTray, window: FakeWindow
) -> None:
    view = _add(window, TortoiseView("running", switch_on=False))
    menu = tray.build_menu()
    action = _dash_action(menu)
    assert action.text() == "Turn on the bot dashboard…"
    assert action.isEnabled()
    action.trigger()
    assert window.shown_tabs == [("game-tortoise", Path("/srv/t"))]
    assert view.shown_switch == 1
    assert view.opened_dashboard == 0
    card = _card(tray)
    assert card.dashboard.text() == "Turn on the bot dashboard…"
    card.dashboard.click()
    assert view.shown_switch == 2


# -------------------------------------------------------------------- not up


@pytest.mark.parametrize("status", ["stopped", "starting", "partial"])
def test_a_server_that_is_not_up_greys_it_with_the_reason(
    tray: YulonTray, window: FakeWindow, status: str
) -> None:
    view = _add(window, TortoiseView(status, switch_on=True))
    menu = tray.build_menu()
    action = _dash_action(menu)
    assert not action.isEnabled()
    assert action.text() == "Bot dashboard (start the server first)"
    card = _card(tray)
    assert not card.dashboard.isEnabled()
    assert card.dashboard.toolTip() == tray_module.DASHBOARD_START_FIRST
    assert card.detail.text() == tray_module.DASHBOARD_START_FIRST
    card.dashboard.click()
    assert view.opened_dashboard == 0


def test_a_dashboard_owed_a_rebuild_is_greyed_with_the_bots_tabs_reason(
    tray: YulonTray, window: FakeWindow
) -> None:
    _add(window, TortoiseView("running", switch_on=True, openable=False))
    menu = tray.build_menu()
    action = _dash_action(menu)
    assert not action.isEnabled()
    assert action.toolTip() == "Rebuild the bot dashboard first"


def test_a_server_with_no_dashboard_shows_none(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    menu = tray.build_menu()
    assert not [a for a in menu.actions() if "dashboard" in a.text().lower()]
    tray.toggle_flyout()
    assert tray.flyout is not None
    assert tray.flyout.cards()[0].dashboard.isHidden()


def test_the_cards_dashboard_entry_is_never_cut_short(tray: YulonTray, window: FakeWindow) -> None:
    """yulon-ubuntu 2026-10-07: "Turn on the bot dashb" - the entry shared the name's column."""
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication

    from yulon.ui.theme import apply_dadcraft_theme

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    palette = QPalette(app.palette())
    apply_dadcraft_theme(app)
    try:
        view = _add(window, TortoiseView("running", switch_on=False))
        from datetime import timedelta

        from yulon import dashboard

        view.last_verdict = dashboard.Verdict(
            "up", players=0, bots=242, uptime=timedelta(minutes=3)
        )
        card = _card(tray)
        QApplication.processEvents()
        assert card.dashboard.width() >= card.dashboard.sizeHint().width(), (
            card.dashboard.width(),
            card.dashboard.sizeHint().width(),
        )
    finally:
        app.setStyleSheet("")
        app.setPalette(palette)


def test_an_open_menus_actions_reach_the_tab_there_is_now(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Adversarial review [high]: a menu left open while a tab is rebuilt kept the old tab
    and pressed on it; actions resolve the server's current tab when they run."""
    from PySide6.QtWidgets import QApplication

    old = _add(window, TortoiseView("running", switch_on=True))
    menu = tray.build_menu()  # open on screen, so not rebuilt under the player
    window.yulon_controllers.remove(old)
    new = TortoiseView("running", switch_on=True)
    _add(window, new)
    old.deleteLater()
    QApplication.processEvents()
    _dash_action(menu).trigger()
    assert new.opened_dashboard == 1


def test_an_action_for_a_server_that_is_gone_opens_yulon_instead(
    tray: YulonTray, window: FakeWindow
) -> None:
    gone = _add(window, TortoiseView("running", switch_on=True))
    menu = tray.build_menu()
    window.yulon_controllers.remove(gone)
    window.servers_changed.emit()
    window.hide()
    _dash_action(menu).trigger()
    assert gone.opened_dashboard == 0
    assert window.isVisible()
