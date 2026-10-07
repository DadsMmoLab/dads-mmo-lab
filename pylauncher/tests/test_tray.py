"""Yu'lon in the system tray (T540): hide on close, the icon's four states, the menu.

Offscreen Qt has no system tray (`isSystemTrayAvailable()` is False there), so
every test hands `YulonTray` a `FakeTrayIcon` and says a tray exists. The window
is a small stand-in carrying the attributes `main.build_window()` puts on the real
one (`yulon_controllers`, `yulon_open_launcher`, ...): the tray reads nothing
else, and `test_main.py` holds the wiring into the real window.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PySide6.QtCore import QEvent, QObject, QRect, Signal
from PySide6.QtGui import QCloseEvent, QColor, QIcon
from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QPushButton, QWidget

from yulon.ui import tray as tray_module
from yulon.ui.tray import YulonTray, tray_state, tray_tooltip


class FakeTrayIcon(QObject):
    """What `YulonTray` uses of `QSystemTrayIcon`, recorded."""

    activated = Signal(object)
    messageClicked = Signal()  # noqa: N815 - Qt's own name

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.icon: QIcon | None = None
        self.tooltip = ""
        self.menu: QMenu | None = None
        self.visible = False
        self.messages: list[tuple[str, str]] = []
        self.rect = QRect()

    def setIcon(self, icon: QIcon) -> None:  # noqa: N802
        self.icon = icon

    def setToolTip(self, text: str) -> None:  # noqa: N802
        self.tooltip = text

    def setContextMenu(self, menu: QMenu) -> None:  # noqa: N802
        self.menu = menu

    def show(self) -> None:
        self.visible = True

    def hide(self) -> None:
        self.visible = False

    def isVisible(self) -> bool:  # noqa: N802
        return self.visible

    def showMessage(self, title: str, text: str, *args: Any) -> None:  # noqa: N802
        self.messages.append((title, text))

    def geometry(self) -> QRect:
        return self.rect


class FakeBadge(QObject):
    status_changed = Signal(str)

    def __init__(self, status: str) -> None:
        super().__init__()
        self.status = status

    def set_status(self, status: str) -> None:
        changed = status != self.status
        self.status = status
        if changed:
            self.status_changed.emit(status)


class FakeView(QObject):
    """A server tab as far as the tray can see one."""

    action_failed = Signal(str)

    def __init__(self, name: str, server_dir: str, status: str = "stopped") -> None:
        super().__init__()
        self.entry = SimpleNamespace(id=f"game-{name.lower()}", name=name)
        self.services = SimpleNamespace(controller=SimpleNamespace(server_dir=Path(server_dir)))
        self.realm_badge = FakeBadge(status)
        self.start_button = QPushButton("Start")
        self.stop_button = QPushButton("Stop")
        self.last_verdict: Any = None
        self.starts = 0
        self.stops = 0

    def start_server(self) -> None:
        # The real one holds the badge at "starting" before its job runs (T188).
        self.starts += 1
        self.realm_badge.set_status("starting")

    def stop_server(self) -> None:
        self.stops += 1
        self.realm_badge.set_status("stopping")


class FakeWindow(QMainWindow):
    servers_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.yulon_controllers: list[Any] = []
        self.opened: list[tuple[str, Path]] = []
        self.shown_tabs: list[tuple[str, Path]] = []
        self.logs_shown = 0
        self.yulon_open_launcher = lambda game, sd: self.opened.append((game, Path(str(sd))))
        self.yulon_show_server_tab = lambda game, sd: self.shown_tabs.append((game, Path(str(sd))))

        def show_logs() -> None:
            self.logs_shown += 1

        self.yulon_show_logs = show_logs


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
    yield made
    made.uninstall()


def _add(window: FakeWindow, view: FakeView) -> FakeView:
    window.yulon_controllers.append(view)
    window.servers_changed.emit()
    return view


def _close_by_hand(window: QWidget) -> bool:
    """A Close event, as the title bar's × sends one. True if the window accepted it."""
    event = QCloseEvent()
    QApplication.sendEvent(window, event)
    return event.isAccepted()


def _dot_colour(icon: QIcon) -> QColor:
    """The colour at the centre of the state dot (bottom right), as drawn at 32 px."""
    image = icon.pixmap(32, 32).toImage()
    centre, _radius, _halo = tray_module.dot_geometry(image.width())
    return image.pixelColor(int(centre.x()), int(centre.y()))


# ----------------------------------------------------------------- pure parts


def test_the_icon_state_is_the_worst_server_first() -> None:
    assert tray_state([]) == "plain"
    assert tray_state(["stopped", "unknown"]) == "plain"
    assert tray_state(["stopped", "running"]) == "up"
    assert tray_state(["running", "starting"]) == "between"
    assert tray_state(["running", "stopping"]) == "between"
    assert tray_state(["running", "starting", "loop"]) == "attention"
    assert tray_state(["restarting"]) == "between", "a Restart of ours holds this word"
    assert tray_state(["running", "partial"]) == "attention"


def test_the_tooltip_counts_and_names_the_servers_online() -> None:
    assert tray_tooltip([]) == "Yu'lon: no servers online"
    assert tray_tooltip([("WotLK", "running"), ("TBC", "stopped")]) == (
        "Yu'lon: 1 server online\nWotLK"
    )
    assert tray_tooltip([("WotLK", "running"), ("TBC", "online")]) == (
        "Yu'lon: 2 servers online\nWotLK\nTBC"
    )


def test_the_tooltip_says_which_server_is_starting_or_needs_a_look() -> None:
    """yulon-win11: the amber and red icons read only "no servers online"."""
    assert tray_tooltip([("WotLK", "starting"), ("TBC", "stopped")]) == (
        "Yu'lon: no servers online\nWotLK — Starting"
    )
    assert tray_tooltip([("WotLK", "running"), ("TBC", "partial")]) == (
        "Yu'lon: 1 server online\nWotLK\nTBC — Partly up"
    )


def test_each_state_draws_its_own_colour_on_the_icon(qapp: Any) -> None:
    from yulon.ui.theme import COLOR_DANGER, COLOR_UNCOMMON

    assert _dot_colour(tray_module.state_icon("up")).name().upper() == COLOR_UNCOMMON.upper()
    assert _dot_colour(tray_module.state_icon("attention")).name().upper() == COLOR_DANGER.upper()
    # Between is a ring: amber at the rim, not filled.
    image = tray_module.state_icon("between").pixmap(32, 32).toImage()
    centre, radius, _halo = tray_module.dot_geometry(image.width())
    rim = image.pixelColor(int(centre.x() + radius - 1), int(centre.y()))
    assert rim.alpha() > 0
    assert abs(rim.red() - 0xFF) < 40 and abs(rim.green() - 0xB0) < 40 and rim.blue() < 60
    assert _dot_colour(tray_module.state_icon("between")).alpha() == 0


# ---------------------------------------------------------- following the tabs


def test_the_icon_and_tooltip_follow_each_tabs_badge(tray: YulonTray, window: FakeWindow) -> None:
    icon = tray.icon
    assert isinstance(icon, FakeTrayIcon)
    assert icon.visible
    view = _add(window, FakeView("WotLK", "/srv/a"))
    assert tray.state == "plain"
    view.realm_badge.set_status("running")
    assert tray.state == "up"
    assert "1 server online" in icon.tooltip
    view.realm_badge.set_status("loop")
    assert tray.state == "attention"


def test_a_start_from_the_tray_shows_starting_at_once(tray: YulonTray, window: FakeWindow) -> None:
    """Lead, 2026-10-07: a Start from the tray must never look like nothing happened."""
    view = _add(window, FakeView("WotLK", "/srv/a"))
    tray.start(view)
    assert view.starts == 1
    assert tray.state == "between"


def test_a_tab_rebuilt_is_followed_and_the_old_one_let_go(
    tray: YulonTray, window: FakeWindow
) -> None:
    old = _add(window, FakeView("WotLK", "/srv/a", "running"))
    assert tray.state == "up"
    window.yulon_controllers.remove(old)
    new = FakeView("WotLK", "/srv/a", "stopped")
    _add(window, new)
    assert tray.state == "plain"
    old.realm_badge.set_status("loop")
    assert tray.state == "plain", "a dropped tab's badge still reached the tray"
    new.realm_badge.set_status("running")
    assert tray.state == "up"


# ---------------------------------------------------------------- hide on close


def test_closing_the_window_by_hand_hides_it_and_keeps_the_app(
    tray: YulonTray, window: FakeWindow
) -> None:
    tray.note_seen = True
    accepted = _close_by_hand(window)
    assert not accepted
    assert window.isHidden()
    assert not QApplication.quitOnLastWindowClosed()


def test_an_application_quit_is_not_turned_into_a_hide(tray: YulonTray, window: FakeWindow) -> None:
    """Qt 6 closes every window on `QEvent.Quit` (macOS Cmd+Q, a session ending): let it."""
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    tray.note_seen = True
    tray._application_quitting()  # what `YulonApplication.quit_requested` reaches
    assert _close_by_hand(window), "the close was swallowed into a hide"
    assert window.isVisible()


def test_the_window_gets_a_real_quit_to_call(tray: YulonTray, window: FakeWindow) -> None:
    """`window.yulon_quit` is what the self-update and the lost-lock exit call instead of close."""
    quits: list[int] = []
    tray.quit_app = lambda: quits.append(1)  # type: ignore[method-assign]
    assert window.yulon_quit() is True  # type: ignore[attr-defined]
    assert quits == [1]
    assert window.isHidden()


def test_quit_closes_the_window_for_real(tray: YulonTray, window: FakeWindow) -> None:
    quits: list[int] = []
    tray.quit_app = lambda: quits.append(1)  # type: ignore[method-assign]
    assert tray.quit()
    assert quits == [1]
    assert window.isHidden()


def test_a_refused_quit_leaves_the_window_up_and_hides_again_next_time(
    tray: YulonTray, window: FakeWindow
) -> None:
    class Refuse(QObject):
        def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
            if event.type() is QEvent.Type.Close:
                event.ignore()
                return True
            return False

    # Installed after the tray's filter, so it runs first; in `main()` the busy
    # guard runs second. A refusal is a refusal in either order.
    refuse = Refuse(window)
    window.installEventFilter(refuse)
    window.hide()
    quits: list[int] = []
    tray.quit_app = lambda: quits.append(1)  # type: ignore[method-assign]
    assert not tray.quit()
    assert quits == []
    assert window.isVisible(), "a refused quit must leave the window where it can be read"
    window.removeEventFilter(refuse)
    tray.note_seen = True
    assert not _close_by_hand(window)
    assert window.isHidden(), "the quit flag outlived the refused quit"


def test_closing_a_launcher_window_while_hidden_keeps_the_app_running(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Lead, 2026-10-07: the client launcher was the one visible window left."""
    from PySide6.QtCore import QTimer

    tray.note_seen = True
    _close_by_hand(window)
    launcher = QWidget()
    launcher.show()
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    ended_by: list[str] = []

    def close_launcher() -> None:
        launcher.close()

    def finish() -> None:
        ended_by.append("test")
        app.exit(7)

    QTimer.singleShot(0, close_launcher)
    QTimer.singleShot(300, finish)
    code = app.exec()
    launcher.deleteLater()
    assert ended_by == ["test"], "closing the launcher ended the app"
    assert code == 7


def test_without_a_tray_closing_quits_as_before(qapp: Any) -> None:
    win = FakeWindow()
    win.show()
    made = YulonTray(win, icon_factory=FakeTrayIcon, available=lambda: False)
    made.install()
    try:
        assert made.icon is None
        assert QApplication.quitOnLastWindowClosed()
        assert _close_by_hand(win)
        assert win.isVisible(), "an accepted close is the window's to carry out, not a hide"
    finally:
        made.uninstall()
        win.deleteLater()


def test_turning_keep_in_tray_off_puts_today_back(tray: YulonTray, window: FakeWindow) -> None:
    tray.set_keep_in_tray(False)
    assert tray.icon is not None and not tray.icon.isVisible()
    assert QApplication.quitOnLastWindowClosed()
    assert _close_by_hand(window)
    tray.set_keep_in_tray(True)
    assert tray.icon.isVisible()
    assert not QApplication.quitOnLastWindowClosed()


def test_a_dialog_that_opens_while_hidden_brings_the_window_back(
    tray: YulonTray, window: FakeWindow
) -> None:
    from PySide6.QtWidgets import QDialog

    tray.note_seen = True
    _close_by_hand(window)
    assert window.isHidden()
    dialog = QDialog(window)
    dialog.setModal(True)
    dialog.show()
    QApplication.processEvents()
    assert window.isVisible(), "a question was asked behind a hidden window"
    dialog.reject()
    dialog.deleteLater()


def test_a_second_launch_brings_a_hidden_window_back(tray: YulonTray, window: FakeWindow) -> None:
    """`main._bring_to_front` answers `raise_requested`; it must un-hide, not just raise."""
    import main

    tray.note_seen = True
    _close_by_hand(window)
    main._bring_to_front(window)
    assert window.isVisible()


# ------------------------------------------------------------------- the menu


def _texts(menu: QMenu) -> list[str]:
    return [action.text() for action in menu.actions() if not action.isSeparator()]


def test_the_menu_lists_the_servers_and_plays_only_an_online_one(
    tray: YulonTray, window: FakeWindow
) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("TBC", "/srv/b", "starting"))
    _add(window, FakeView("Vanilla", "/srv/c", "stopped"))
    menu = tray.build_menu()
    texts = _texts(menu)
    assert texts[0] == "Yu'lon — 1 of 3 servers online"
    assert "Open Yu'lon" in texts
    assert "Logs" in texts
    assert "Quit tray…" in texts
    open_action = next(a for a in menu.actions() if a.text() == "Open Yu'lon")
    assert open_action.font().bold()
    assert menu.defaultAction() is open_action
    play = next(a for a in menu.actions() if a.text() == "Play")
    sub = play.menu()
    assert sub is not None
    rows = {a.text(): a.isEnabled() for a in sub.actions()}
    assert rows == {"WotLK": True, "TBC (starting)": False, "Vanilla (stopped)": False}
    next(a for a in sub.actions() if a.isEnabled()).trigger()
    assert window.opened == [("game-wotlk", Path("/srv/a"))]


def test_the_menu_status_rows_say_each_servers_state(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("TBC", "/srv/b", "loop"))
    texts = _texts(tray.build_menu())
    assert "WotLK — Realm online" in texts
    assert "TBC — Crash loop" in texts


def test_open_yulon_and_logs_bring_the_window_back(tray: YulonTray, window: FakeWindow) -> None:
    tray.note_seen = True
    _close_by_hand(window)
    menu = tray.build_menu()
    next(a for a in menu.actions() if a.text() == "Logs").trigger()
    assert window.logs_shown == 1
    _close_by_hand(window)
    next(a for a in menu.actions() if a.text() == "Open Yu'lon").trigger()
    assert window.isVisible()


def test_a_double_click_on_the_icon_opens_the_window(tray: YulonTray, window: FakeWindow) -> None:
    from PySide6.QtWidgets import QSystemTrayIcon

    tray.note_seen = True
    _close_by_hand(window)
    assert isinstance(tray.icon, FakeTrayIcon)
    tray.icon.activated.emit(QSystemTrayIcon.ActivationReason.DoubleClick)
    assert window.isVisible()


def test_uninstall_gives_back_the_close_and_the_quit(qapp: Any) -> None:
    win = FakeWindow()
    win.show()
    made = YulonTray(win, icon_factory=FakeTrayIcon, available=lambda: True)
    made.install()
    made.uninstall()
    assert QApplication.quitOnLastWindowClosed()
    assert _close_by_hand(win)
    win.deleteLater()


def test_two_servers_of_one_game_are_told_apart(tray: YulonTray, window: FakeWindow) -> None:
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    _add(window, FakeView("WotLK", "/srv/b", "stopped"))
    texts = _texts(tray.build_menu())
    assert "WotLK — a — Realm online" in texts
    assert "WotLK — b — Stopped" in texts


def test_the_menu_is_filled_before_anyone_asks_to_show_it(
    tray: YulonTray, window: FakeWindow
) -> None:
    """yulon-ubuntu 2026-10-07: GNOME's AppIndicator reads the exported menu's items before
    it ever shows it, and with none it ignored every click on the icon. The menu was filled
    only in aboutToShow, which never came."""
    _add(window, FakeView("WotLK", "/srv/a", "running"))
    assert isinstance(tray.icon, FakeTrayIcon) and tray.icon.menu is not None
    texts = _texts(tray.icon.menu)
    assert "Open Yu'lon" in texts and "WotLK — Realm online" in texts
    window.yulon_controllers[0].realm_badge.set_status("stopped")
    assert "WotLK — Stopped" in _texts(tray.icon.menu), "the filled menu went stale"


def test_rebuilding_the_menu_leaves_one_play_submenu(tray: YulonTray, window: FakeWindow) -> None:
    """Cold review SHOULD: every refresh left the old Play submenu behind (200 -> 201)."""
    from PySide6.QtCore import QCoreApplication

    _add(window, FakeView("WotLK", "/srv/a", "running"))
    assert tray._menu is not None
    for _ in range(50):
        tray.refresh()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert len(tray._menu.findChildren(QMenu)) == 1


def test_the_application_is_watched_only_while_the_window_is_hidden(
    tray: YulonTray, window: FakeWindow
) -> None:
    """Cold review SHOULD: an application-wide filter puts every event of the app through
    Python. It is needed only for a dialog that opens while the window is hidden."""
    assert not tray._watching_app
    tray.note_seen = True
    _close_by_hand(window)
    assert tray._watching_app
    tray.open_window()
    QApplication.processEvents()
    assert not tray._watching_app, "still watching the whole app with the window back"
