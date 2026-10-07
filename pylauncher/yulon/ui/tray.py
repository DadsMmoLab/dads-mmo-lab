"""Yu'lon in the system tray (T540): it keeps running when its window is closed.

The owner's design (ticket T540): closing the window hides it, and the tray icon
says how the servers are -- plain, a green dot when a realm is online, an amber
ring while one starts or stops, a red dot when one needs attention (crash loop,
partly up). A left click opens the flyout, a right click the menu, and Quit
tray… is the one way out.

**One process, one status.** The tray is the same app with its window hidden,
not a second program, so the single-instance lock (T152) is still held and a
second launch still brings the window back (`main._bring_to_front` calls
`show()`). It reads what each Server tab already computed -- the realm badge's
word, which folds in T188's held "starting/stopping", T391's crash loop and
T451's "starting until ready" -- through the badge's own `status_changed`. No
timer here asks Docker anything.

**What the tray needs from the window** is a handful of attributes
`main.build_window()` puts on it: `yulon_controllers` (the live tabs),
`servers_changed` (a tab came or went), `yulon_open_launcher` (Play, T187),
`yulon_show_server_tab` and `yulon_show_logs`. The tray gives the window
`yulon_quit`, the one close that is never turned into a hide: the self-update and
the lost-lock exit call it.

**No tray** (`isSystemTrayAvailable()` False: GNOME without AppIndicator, a
Steam Deck in Game Mode, offscreen Qt): no icon, and closing quits exactly as it
did before this existed.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QApplication,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

from yulon import autostart, ui_settings
from yulon.log import get_logger
from yulon.ui.message_box import FittedMessageBox
from yulon.ui.tab_titles import controller_tab_titles
from yulon.ui.theme import COLOR_DANGER, COLOR_GOLD_BRIGHT, COLOR_UNCOMMON
from yulon.ui.tray_flyout import TrayFlyout
from yulon.ui.widgets.dadcraft_decorations import realm_tone

logger = get_logger(__name__)

OPEN_YULON = "Open Yu'lon"
PLAY = "Play"
LOGS = "Logs"
QUIT_TRAY = "Quit tray…"

STATE_COLOURS = {"up": COLOR_UNCOMMON, "between": COLOR_GOLD_BRIGHT, "attention": COLOR_DANGER}
"""The dot per icon state: the theme's online green, amber accent and danger red."""

NOTE_NEVER = "never"

FLYOUT_REFRESH_MS = 5000
"""How often an open flyout re-reads the tabs' counts: the Server tab's own poll."""

NOTIFY_MS = 10000
"""How long a notification asks to stay (the desktop may decide otherwise)."""

TRAY_WAIT_MS = 500
TRAY_WAIT_TRIES = 30
"""A sign-in start waits up to 15 s for the desktop's tray before it shows the window."""

REOPEN_GUARD_S = 0.3
"""A left click within this of the flyout closing (by losing focus to that same click)
leaves it closed: on Windows the click reaches the flyout first as a focus loss."""

ICON_SIZES = (16, 20, 24, 32, 48, 64)
"""Drawn at each size the shells ask for, so the dot is never a scaled blur."""


def status_words(status: str) -> str:
    """A badge word as the tray says it: the pill on a card and the menu's status row."""
    status = status.lower()
    if status == "stopping":
        return "Stopping"
    if status == "partial":
        return "Partly up"
    if status == "loop":
        return "Crash loop"
    tone = realm_tone(status)
    return {
        "up": "Realm online",
        "between": "Starting",
        "restarting": "Restarting",
        "unknown": "Status unknown",
    }.get(tone, "Stopped")


def is_online(status: str) -> bool:
    return realm_tone(status) == "up"


def tray_state(statuses: Sequence[str]) -> str:
    """The icon's state, worst first: "attention", "between", "up" or "plain".

    "unknown" (Docker did not answer) claims nothing and draws plain, as the
    sidebar dot draws nothing for it (T188).
    """
    words = [status.lower() for status in statuses]
    tones = [realm_tone(word) for word in words]
    if "loop" in words or "partial" in words:
        return "attention"
    # "restarting" is the hold of a Restart of ours (T188), in between like a Start.
    if "between" in tones or "restarting" in tones:
        return "between"
    if "up" in tones:
        return "up"
    return "plain"


def tray_tooltip(servers: Sequence[tuple[str, str]]) -> str:
    """ "Yu'lon: N servers online", which, and any in between or needing a look, with its state.

    Stopped servers are left out; a starting, stopping, partly up or looping one
    is named with its state (yulon-win11: the amber and red icons said only "no
    servers online").
    """
    online = [name for name, status in servers if is_online(status)]
    others = [
        f"{name} — {status_words(status)}"
        for name, status in servers
        if not is_online(status) and realm_tone(status) not in ("down", "unknown")
    ]
    noun = "server" if len(online) == 1 else "servers"
    head = f"Yu'lon: {len(online)} {noun} online" if online else "Yu'lon: no servers online"
    return "\n".join([head, *online, *others])


DASHBOARD = "Bot dashboard"
DASHBOARD_TURN_ON = "Turn on the bot dashboard…"
DASHBOARD_START_FIRST = "Start the server first to open its bot dashboard."


@dataclass(frozen=True)
class DashboardEntry:
    """What a Tortoise server's tray entry for the TortoiseBots dashboard says and does."""

    label: str
    menu_label: str
    enabled: bool
    reason: str
    kind: str
    """ "open" (the Bots tab's own Open), "switch" (the Bots tab, at the switch) or ""."""


def dashboard_entry(view: Any, status: str) -> DashboardEntry | None:
    """The bot dashboard's entry for one server, or None where it has none (not Tortoise).

    Three states, read off the Bots tab itself so the tray cannot disagree with it:
    the realm not up (greyed, start it first); up with the switch on (open it, or
    greyed with the Open button's own reason, e.g. a rebuild owed); up with the
    switch off (go to the switch).
    """
    if getattr(view.services, "bot_dashboard", None) is None:
        return None
    switch = getattr(view, "dashboard_switch", None)
    opener = getattr(view, "open_dashboard_button", None)
    if switch is None or opener is None:  # pragma: no cover - built with the seam
        return None
    if not is_online(status):
        return DashboardEntry(
            DASHBOARD, f"{DASHBOARD} (start the server first)", False, DASHBOARD_START_FIRST, ""
        )
    if not switch.isChecked():
        return DashboardEntry(DASHBOARD_TURN_ON, DASHBOARD_TURN_ON, True, "", "switch")
    if not opener.isEnabled():
        return DashboardEntry(DASHBOARD, DASHBOARD, False, opener.toolTip(), "")
    return DashboardEntry(DASHBOARD, DASHBOARD, True, "", "open")


def header_text(statuses: Sequence[str]) -> str:
    """ "Yu'lon — N of M servers online": the menu's first row and the flyout's title."""
    online = sum(1 for status in statuses if is_online(status))
    noun = "server" if len(statuses) == 1 else "servers"
    return f"Yu'lon — {online} of {len(statuses)} {noun} online"


def dot_geometry(size: int) -> tuple[QPointF, float, float]:
    """The state dot on a `size`-px icon: centre, radius, and the clear ring around it."""
    radius = size * 0.2
    centre = size - radius - 0.5
    return QPointF(centre, centre), radius, radius + size * 0.08


def state_icon(state: str, base: QIcon | None = None) -> QIcon:
    """The app icon with the state's dot drawn on it; plain is the app icon itself."""
    if base is None:
        from yulon.ui.icons import get_app_icon

        base = get_app_icon()
    colour = STATE_COLOURS.get(state)
    if colour is None:
        return base
    icon = QIcon()
    for size in ICON_SIZES:
        pixmap = base.pixmap(QSize(size, size))
        if pixmap.isNull() or pixmap.width() != size:
            pixmap = QPixmap(size, size)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            base.paint(painter, QRect(0, 0, size, size))
            painter.end()
        centre, radius, halo = dot_geometry(size)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.setBrush(QBrush(Qt.GlobalColor.black))
        painter.drawEllipse(centre, halo, halo)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        if state == "between":
            # A ring, not a dot: in between is not yet anything.
            ring = max(1.5, size * 0.09)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(colour), ring))
            painter.drawEllipse(centre, radius - ring / 2, radius - ring / 2)
        else:
            painter.setBrush(QColor(colour))
            painter.drawEllipse(centre, radius, radius)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class TrayIcon(Protocol):
    """What `YulonTray` uses of `QSystemTrayIcon`; a test hands in a recording fake."""

    activated: Any
    messageClicked: Any  # noqa: N815 - Qt's own name

    def setIcon(self, icon: QIcon) -> None: ...  # noqa: N802
    def setToolTip(self, text: str) -> None: ...  # noqa: N802
    def setContextMenu(self, menu: QMenu) -> None: ...  # noqa: N802
    def show(self) -> None: ...
    def hide(self) -> None: ...
    def isVisible(self) -> bool: ...  # noqa: N802
    def showMessage(self, title: str, text: str, *args: Any) -> None: ...  # noqa: N802
    def geometry(self) -> QRect: ...


def server_titles(views: Sequence[Any]) -> list[str]:
    """Each server's name, with its folder only where two servers share a name.

    The rail's own rule in short: one WotLK is "WotLK"; two are "WotLK — a" and
    "WotLK — b" (`controller_tab_titles`, the header's titles).
    """
    names = [view.entry.name for view in views]
    full = controller_tab_titles(
        [(view.entry.name, view.services.controller.server_dir) for view in views]
    )
    return [full[i] if names.count(name) > 1 else name for i, name in enumerate(names)]


def _bring_forward(window: QWidget) -> None:
    """Show, un-minimize, raise and activate: `main._bring_to_front` without a token."""
    window.setWindowState(
        (window.windowState() & ~Qt.WindowState.WindowMinimized) | Qt.WindowState.WindowActive
    )
    window.show()
    window.raise_()
    window.activateWindow()


class YulonTray(QObject):
    """The tray icon, its menu, and the window's close turned into a hide. See the module doc."""

    state_changed = Signal(str)

    def __init__(
        self,
        window: QWidget,
        *,
        icon_factory: Callable[[QObject], TrayIcon] | None = None,
        available: Callable[[], bool] = QSystemTrayIcon.isSystemTrayAvailable,
        bring_forward: Callable[[QWidget], None] = _bring_forward,
        keep_in_tray: bool | None = None,
    ) -> None:
        super().__init__(window)
        self.window = window
        self._icon_factory = icon_factory or (lambda parent: QSystemTrayIcon(parent))
        self._available = available
        self._bring_forward = bring_forward
        saved = ui_settings.load_ui_settings()
        self.keep_in_tray = saved.keep_in_tray if keep_in_tray is None else keep_in_tray
        self.icon: TrayIcon | None = None
        self.state = "plain"
        self.note_seen = saved.tray_note == NOTE_NEVER
        """Whether "Yu'lon stays in the tray" was said: this run, or "Don't show again"."""
        self._stopping: list[Any] = []
        """The tabs Stop-then-quit pressed Stop on and is waiting for (empty: not waiting)."""
        self._wait_tries = 0
        self._wait_timer: QTimer | None = None
        self._was_up: dict[int, bool] = {}
        """Each tab's realm was last settled up, not taken down by a Stop of ours."""
        self._looping: dict[int, bool] = {}
        """A crash loop was said for this tab and has not ended (up or down) since."""
        self._notified: Any = None
        self._quitting = False
        self._installed = False
        self._followed: list[Any] = []
        self._menu: QMenu | None = None
        self._was_quit_on_last = True
        self.flyout: TrayFlyout | None = None
        self._flyout_timer: QTimer | None = None
        self._flyout_hidden_at = 0.0

    # ---------------------------------------------------------------- set up

    @property
    def has_tray(self) -> bool:
        """A tray to sit in exists on this desktop."""
        try:
            return bool(self._available())
        except Exception as exc:  # noqa: BLE001 - a broken probe is no tray, not a crash
            logger.info(f"tray: could not ask whether a tray exists: {exc}")
            return False

    @property
    def keeping(self) -> bool:
        """Closing the window hides it now: a tray exists, the player wants it, not quitting."""
        return (
            self.icon is not None
            and self.keep_in_tray
            and self.icon.isVisible()
            and not self._quitting
        )

    def install(self) -> None:
        """Make the icon (where a tray exists), and take over the window's close."""
        if self._installed:
            return
        self._installed = True
        app = QApplication.instance()
        if isinstance(app, QApplication):
            self._was_quit_on_last = QApplication.quitOnLastWindowClosed()
            app.installEventFilter(self)
        self.window.installEventFilter(self)
        self.window.yulon_quit = self.quit  # type: ignore[attr-defined]
        changed = getattr(self.window, "servers_changed", None)
        if changed is not None:
            changed.connect(self.follow_servers)
        previous = getattr(self.window, "yulon_open_settings", None)
        self._previous_settings = previous
        self.window.yulon_open_settings = self.open_settings  # type: ignore[attr-defined]
        if self.has_tray:
            self._make_icon()
        else:
            logger.info("tray: this desktop has no system tray; closing Yu'lon quits it")
        self.follow_servers()
        self.set_keep_in_tray(self.keep_in_tray)

    def _make_icon(self) -> None:
        # Windows drops a notification from an unnamed explicit app ID (no-op elsewhere).
        autostart.register_app_id()
        icon = self._icon_factory(self)
        self.icon = icon
        self._menu = QMenu()
        self._menu.aboutToShow.connect(self._refill_menu)
        icon.setContextMenu(self._menu)
        icon.activated.connect(self._activated)
        icon.messageClicked.connect(self._message_clicked)

    def uninstall(self) -> None:
        """Undo `install()`: the close quits again, the icon goes. For a test's teardown."""
        if not self._installed:
            return
        self._installed = False
        app = QApplication.instance()
        if isinstance(app, QApplication):
            app.removeEventFilter(self)
            QApplication.setQuitOnLastWindowClosed(self._was_quit_on_last)
        if shiboken6.isValid(self.window):
            self.window.removeEventFilter(self)
            changed = getattr(self.window, "servers_changed", None)
            if changed is not None:
                try:
                    changed.disconnect(self.follow_servers)
                except (RuntimeError, TypeError):  # pragma: no cover - never connected
                    pass
            self.window.yulon_quit = self.window.close  # type: ignore[attr-defined]
            if self._previous_settings is not None:
                self.window.yulon_open_settings = self._previous_settings  # type: ignore[attr-defined]
        self._let_go_of_servers()
        self._stop_waiting_to_quit()
        if self._wait_timer is not None:
            self._wait_timer.stop()
        if self._flyout_timer is not None:
            self._flyout_timer.stop()
        if self.icon is not None:
            self.icon.hide()
        if self._menu is not None:
            self._menu.deleteLater()
            self._menu = None
        if self.flyout is not None:
            self.flyout.hide()
            self.flyout.deleteLater()
            self.flyout = None

    def set_keep_in_tray(self, keep: bool) -> None:
        """The setting: ON shows the icon and hides on close; OFF is the app as before."""
        self.keep_in_tray = keep
        if self.icon is None:
            QApplication.setQuitOnLastWindowClosed(True)
            return
        if keep:
            self.icon.show()
        else:
            self.icon.hide()
        # Off while the tray holds the app: with the window hidden, closing a
        # client launcher (or a parentless message box) would otherwise be the
        # last window closing, and Qt would end the app under the tray.
        QApplication.setQuitOnLastWindowClosed(not keep)

    # ------------------------------------------------------------ the servers

    def views(self) -> list[Any]:
        """The live Server tabs, read from the window each time (tabs are rebuilt)."""
        return [
            view
            for view in getattr(self.window, "yulon_controllers", [])
            if shiboken6.isValid(view)
        ]

    @Slot()
    def follow_servers(self) -> None:
        """Listen to the badge of every tab there is now, and to none that went."""
        self._let_go_of_servers()
        for view in self.views():
            view.realm_badge.status_changed.connect(self._server_changed)
            view.action_failed.connect(self._server_failed)
            self._followed.append(view)
            # What it says now is where a change is measured from: nothing is
            # said about a tab for the state it opened in.
            self._was_up.setdefault(id(view), realm_tone(view.realm_badge.status) == "up")
        live = {id(view) for view in self._followed}
        self._was_up = {k: v for k, v in self._was_up.items() if k in live}
        self._looping = {k: v for k, v in self._looping.items() if k in live}
        self.refresh()

    def _let_go_of_servers(self) -> None:
        for view in self._followed:
            if shiboken6.isValid(view) and shiboken6.isValid(view.realm_badge):
                try:
                    view.realm_badge.status_changed.disconnect(self._server_changed)
                    view.action_failed.disconnect(self._server_failed)
                except (RuntimeError, TypeError):  # pragma: no cover - already gone
                    pass
        self._followed = []

    @Slot(str)
    def _server_changed(self, status: str) -> None:
        badge = self.sender()
        view = next((v for v in self._followed if v.realm_badge is badge), None)
        if view is not None:
            self._notice_change(view, status)
        self.refresh()

    # -------------------------------------------------------- notifications

    def _looking(self) -> bool:
        """Whether the player can see the window, which already says what happened."""
        window = self.window
        return window.isVisible() and not window.isMinimized() and window.isActiveWindow()

    def _notice_change(self, view: Any, now: str) -> None:
        """A crash loop, or a realm that fell from up without a Stop of ours: said from the tray.

        Measured from the last SETTLED word, not the last word: on yulon-win11 a
        world stopped outside Yu'lon went running, starting, partial, because
        the verdict landed first and T451 reads a world that is not ready as
        "starting". So "starting" leaves "was up" as it was; a Stop or Restart
        of ours holds "stopping" or "restarting" first (T188), which clears it.
        """
        key = id(view)
        now = now.lower()
        title = self._title_of(view)
        tone = realm_tone(now)
        if now == "loop":
            if not self._looping.get(key):
                self._looping[key] = True
                self.notify(
                    view,
                    f"{title} is crash-looping",
                    "Its world keeps stopping. Click to see its Server tab.",
                )
            return
        if tone == "up":
            self._was_up[key] = True
            self._looping[key] = False
            return
        if now in ("stopping", "restarting"):
            self._was_up[key] = False  # ours (T188's hold)
            return
        was_up = self._was_up.get(key, False)
        if now == "partial" and was_up:
            self._was_up[key] = False
            self.notify(
                view,
                f"{title} is only partly up",
                "Part of it stopped, and Yu'lon did not stop it. Click to see its Server tab.",
            )
        elif tone == "down":
            self._looping[key] = False
            if was_up:
                self._was_up[key] = False
                self.notify(
                    view,
                    f"{title} went offline",
                    "Yu'lon did not stop it. Click to see its Server tab.",
                )

    @Slot(str)
    def _server_failed(self, message: str) -> None:
        sender = self.sender()
        view = next((v for v in self._followed if v is sender), None)
        if view is None or view in self._stopping:
            return  # Stop-then-quit shows that one itself
        self.notify(view, f"{self._title_of(view)}: something went wrong", message)

    def _title_of(self, view: Any) -> str:
        views = self.views()
        titles = dict(zip((id(v) for v in views), server_titles(views), strict=True))
        return titles.get(id(view)) or str(view.entry.name)

    def notify(self, view: Any, title: str, text: str) -> None:
        """A tray notification about one server, unless the window already shows it."""
        if self.icon is None or not self.keeping or self._looking():
            return
        logger.info(f"tray: notified: {title}")
        self._notified = view
        self.icon.showMessage(title, text, QSystemTrayIcon.MessageIcon.Warning, NOTIFY_MS)

    def servers(self) -> list[tuple[Any, str, str]]:
        """(view, title, badge word) for each live server tab, in rail order."""
        views = self.views()
        titles = server_titles(views)
        return [
            (view, title, view.realm_badge.status)
            for view, title in zip(views, titles, strict=True)
        ]

    def refresh(self) -> None:
        """Icon and tooltip from the badges as they are now."""
        servers = self.servers()
        state = tray_state([status for _, _, status in servers])
        changed = state != self.state
        self.state = state
        if self.icon is not None:
            self.icon.setIcon(state_icon(state))
            self.icon.setToolTip(tray_tooltip([(title, status) for _, title, status in servers]))
        if self.flyout is not None and self.flyout.isVisible():
            self._fill_flyout(servers)
        if self._menu is not None and not self._menu.isVisible():
            # Kept filled, not only on aboutToShow: GNOME's AppIndicator reads the
            # exported menu's items first, and with none it ignores every click
            # on the icon (yulon-ubuntu, 2026-10-07).
            self.build_menu(self._menu)
        if changed:
            self.state_changed.emit(state)

    # -------------------------------------------------------------- actions

    def open_window(self) -> None:
        self._bring_forward(self.window)

    def _current(self, view: Any) -> Any:
        """The server's tab as it is NOW, found by its (game, server folder), or None.

        An action can outlive the tab it was built for: a menu left open while
        the tab was rebuilt or removed (adversarial review). Pressing on that
        object would act on a superseded tab or a deleted one, so every action
        looks its server up again when it runs.
        """
        try:
            key = (view.entry.id, view.services.controller.server_dir)
        except (AttributeError, RuntimeError):
            return None
        for live in self.views():
            if (live.entry.id, live.services.controller.server_dir) == key:
                return live
        return None

    def start(self, view: Any) -> None:
        """Start one server through its own tab's Start: the same job, guards and badge."""
        live = self._current(view)
        if live is None:
            self.open_window()
            return
        live.start_server()

    def play(self, view: Any) -> None:
        """Open this server's client launcher window, the sidebar ▶'s way (T187)."""
        live = self._current(view)
        opener = getattr(self.window, "yulon_open_launcher", None)
        if live is None or opener is None:
            self.open_window()
            return
        opener(live.entry.id, live.services.controller.server_dir)

    def dashboard(self, view: Any, kind: str) -> None:
        """The bot dashboard entry: open it (the Bots tab's own Open), or go to its switch."""
        live = self._current(view)
        if live is None:
            self.open_window()
            return
        if kind == "open":
            live.open_bot_dashboard()
        elif kind == "switch":
            self.show_server(live)
            live.show_bot_dashboard_switch()

    def show_server(self, view: Any) -> None:
        live = self._current(view)
        shower = getattr(self.window, "yulon_show_server_tab", None)
        if live is not None and shower is not None:
            shower(live.entry.id, live.services.controller.server_dir)
        else:  # pragma: no cover - the real window always has one
            self.open_window()

    def show_logs(self) -> None:
        shower = getattr(self.window, "yulon_show_logs", None)
        if shower is not None:
            shower()
        self.open_window()

    def quit(self) -> bool:
        """Close the window for real and end the app. False when the close was refused.

        Refused is `main`'s busy guard: an import or a support save is running.
        It says why in a box; the window comes forward with it, and the next
        close by hand hides again.
        """
        self._quitting = True
        closed = self.window.close()
        if not closed:
            self._quitting = False
            self.open_window()
            return False
        if self.icon is not None:
            self.icon.hide()
        self.quit_app()
        return True

    def quit_app(self) -> None:
        """End the event loop. A seam: a test must not end its own process's loop."""
        QApplication.exit(0)

    def open_settings(self) -> None:
        """The tray's Settings: keep running in the tray, start at sign-in."""
        from yulon.ui.tray_settings import TraySettingsDialog

        dialog = getattr(self, "_settings_dialog", None)
        if dialog is None or not shiboken6.isValid(dialog):
            dialog = TraySettingsDialog(self)
            dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
            self._settings_dialog = dialog
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def remember_keep_in_tray(self, keep: bool) -> None:
        """The Settings switch: applied now, and kept in `ui.json` for the next start."""
        self.set_keep_in_tray(keep)
        ui_settings.remember_tray(keep_in_tray=keep)

    # ------------------------------------------------------------ quitting

    def running_servers(self) -> list[Any]:
        """The tabs whose realm is up, starting, stopping, partly up or looping."""
        return [
            view
            for view, _title, status in self.servers()
            if realm_tone(status) in ("up", "between", "restarting")
        ]

    def ask_to_quit(self) -> None:
        """Quit tray…: with servers running, ask whether to leave them or stop them first.

        Leaving them is the default and loses nothing: they are Docker
        containers, they keep running, and Yu'lon picks them up when it starts.
        """
        if self._stopping:
            if self.choose_while_stopping(len(self._stopping)) == "quit":
                logger.info("tray: quitting without waiting for the stops")
                self._stop_waiting_to_quit()
                self.quit()
            else:
                self.open_window()
            return
        running = self.running_servers()
        if not running:
            self.quit()
            return
        choice = self.choose_quit(len(running))
        if choice == "leave":
            self.quit()
        elif choice == "stop":
            self.stop_then_quit(running)

    def choose_quit(self, count: int) -> str:
        """The quit box, answered: "leave", "stop" or "cancel". A seam for the tests."""
        box, buttons = quit_box(count)
        try:
            box.exec()
            clicked = box.clickedButton()
            for name, button in buttons.items():
                if clicked is button:
                    return name
            return "cancel"
        finally:
            box.deleteLater()

    def choose_while_stopping(self, count: int) -> str:
        """Quit tray… while Stop-then-quit waits: "wait" or "quit". A seam for the tests."""
        box, buttons = while_stopping_box(count)
        try:
            box.exec()
            clicked = box.clickedButton()
            return "quit" if clicked is buttons["quit"] else "wait"
        finally:
            box.deleteLater()

    def tell(self, title: str, text: str) -> None:
        """A short box with OK (a seam for the tests)."""
        from yulon.ui.message_box import show_information

        show_information(self.window, title, text)

    def stop_then_quit(self, running: Sequence[Any]) -> None:
        """Press each running server's own Stop (it saves characters), quit when all are down.

        The window comes forward: a stop can take minutes, and the Server tab is
        where "Stop now anyway" and any failure are. A server whose Stop is
        greyed (another job of its tab is running) refuses the whole quit, with
        the tab's own reason, rather than quitting with it still up.
        """
        titles = dict(zip(self.views(), server_titles(self.views()), strict=True))
        blocked = [view for view in running if not view.stop_button.isEnabled()]
        if blocked:
            view = blocked[0]
            why = view.stop_button.toolTip() or "it is busy"
            self.tell(
                "Yu'lon cannot stop that server yet",
                f"{titles.get(view, view.entry.name)} cannot be stopped right now: {why}\n\n"
                "Yu'lon has not quit. Try again when it has finished.",
            )
            self.open_window()
            return
        self.open_window()
        self._stopping = list(running)
        for view in running:
            view.realm_badge.status_changed.connect(self._a_stop_moved)
            view.action_failed.connect(self._a_stop_failed)
        for view in running:
            view.stop_server()
        logger.info(f"tray: stopping {len(running)} server(s), then quitting")
        self._a_stop_moved("")

    @Slot(str)
    def _a_stop_moved(self, _status: str) -> None:
        """Quit once every Stop has ended with its server down; end the wait if one did not.

        A Stop of ours holds the badge at "stopping" until its job has ended and
        a reading after it has answered (T188), so a badge that has left
        "stopping" is a Stop that is over. Over and not down -- Docker went
        quiet ("unknown"), or the server is still up -- is not waited on for
        ever (adversarial review): Yu'lon stays open, says which, and the next
        Quit tray… asks again.
        """
        if not self._stopping:
            return
        live = [view for view in self._stopping if shiboken6.isValid(view)]
        if any(view.realm_badge.status.lower() == "stopping" for view in live):
            return
        left = [view for view in live if realm_tone(view.realm_badge.status) != "down"]
        self._stop_waiting_to_quit()
        if not left:
            logger.info("tray: every server Yu'lon stopped is down; quitting")
            self.quit()
            return
        names = ", ".join(self._title_of(view) for view in left)
        logger.warning(f"tray: a stop before quitting ended without its server down: {names}")
        self.show_server(left[0])
        self.tell(
            "Yu'lon did not quit",
            f"{names} did not read as stopped after its Stop "
            f"({status_words(left[0].realm_badge.status)}). Yu'lon stays open so you can "
            "see why; Quit tray… asks again.",
        )

    @Slot(str)
    def _a_stop_failed(self, message: str) -> None:
        sender = self.sender()
        failed = next((view for view in self._stopping if view is sender), None)
        self._stop_waiting_to_quit()
        logger.warning(f"tray: a stop before quitting failed ({message}); Yu'lon stays open")
        if failed is not None and shiboken6.isValid(failed):
            self.show_server(failed)
        else:  # pragma: no cover - only a stopping tab is connected
            self.open_window()

    def _stop_waiting_to_quit(self) -> None:
        for view in self._stopping:
            if not shiboken6.isValid(view):
                continue
            for signal, slot in (
                (view.realm_badge.status_changed, self._a_stop_moved),
                (view.action_failed, self._a_stop_failed),
            ):
                try:
                    signal.disconnect(slot)
                except (RuntimeError, TypeError):  # pragma: no cover - already gone
                    pass
        self._stopping = []

    # ------------------------------------------------- started at sign-in

    def start_hidden(self) -> None:
        """`--tray` (the sign-in entry): no window, the icon only.

        A desktop can bring its tray up after the programs it starts at sign-in,
        so a missing tray is waited for, a try every `TRAY_WAIT_MS`; if none
        comes, the window opens after all rather than Yu'lon running unseen.
        """
        if self.keeping:
            logger.info("tray: started at sign-in; Yu'lon is in the tray")
            return
        if not self.keep_in_tray:
            self.open_window()
            return
        self._wait_tries = 0
        timer = QTimer(self)
        timer.setInterval(TRAY_WAIT_MS)
        timer.timeout.connect(self._wait_for_the_tray)
        self._wait_timer = timer
        timer.start()

    def _wait_for_the_tray(self) -> None:
        self._wait_tries += 1
        if self.icon is None and self.has_tray:
            self._make_icon()
            self.follow_servers()
            self.set_keep_in_tray(self.keep_in_tray)
        if self.keeping:
            logger.info("tray: the tray came up; Yu'lon is in it")
            self._end_the_wait()
            return
        if self._wait_tries >= TRAY_WAIT_TRIES:
            logger.info("tray: no tray came up after sign-in; showing the window")
            self._end_the_wait()
            self.open_window()

    def _end_the_wait(self) -> None:
        if self._wait_timer is not None:
            self._wait_timer.stop()
            self._wait_timer.deleteLater()
            self._wait_timer = None

    @Slot()
    def _message_clicked(self) -> None:
        """A notification was clicked: the window, on the server it was about."""
        view = self._notified
        self._notified = None
        if view is not None and shiboken6.isValid(view) and view in self.views():
            self.show_server(view)
        else:
            self.open_window()

    # ----------------------------------------------------------------- menu

    @Slot()
    def _refill_menu(self) -> None:
        if self._menu is not None:
            self.build_menu(self._menu)

    def build_menu(self, menu: QMenu | None = None) -> QMenu:
        """Option A: counts, one row per server, Open Yu'lon, Play ▸, Logs, Quit tray…

        Built each time it opens, from the tabs as they are then; a test drives
        this, because `QMenu.exec` cannot be replaced.
        """
        if menu is None:
            menu = QMenu()
        menu.clear()
        servers = self.servers()
        header = menu.addAction(header_text([status for _, _, status in servers]))
        header.setEnabled(False)
        for view, title, status in servers:
            row = menu.addAction(f"{title} — {status_words(status)}")
            row.setEnabled(False)
            entry = dashboard_entry(view, status)
            if entry is not None:
                dash = menu.addAction(entry.menu_label)
                dash.setEnabled(entry.enabled)
                dash.setToolTip(entry.reason)
                dash.triggered.connect(
                    lambda _checked=False, v=view, k=entry.kind: self.dashboard(v, k)
                )
        menu.setToolTipsVisible(True)
        menu.addSeparator()
        open_action = menu.addAction(OPEN_YULON)
        font = QFont(open_action.font())
        font.setBold(True)
        open_action.setFont(font)
        open_action.triggered.connect(self.open_window)
        menu.setDefaultAction(open_action)
        play_menu = QMenu(PLAY, menu)
        for view, title, status in servers:
            if is_online(status):
                label = title
            else:
                label = f"{title} ({'starting' if realm_tone(status) == 'between' else 'stopped'})"
            action = play_menu.addAction(label)
            action.setEnabled(is_online(status))
            action.triggered.connect(lambda _checked=False, v=view: self.play(v))
        play_menu.setEnabled(bool(servers))
        menu.addMenu(play_menu)
        menu.addAction(LOGS).triggered.connect(self.show_logs)
        menu.addSeparator()
        menu.addAction(QUIT_TRAY).triggered.connect(self.ask_to_quit)
        return menu

    @Slot(object)
    def _activated(self, reason: object) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_flyout()
        elif reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.hide_flyout()
            self.open_window()

    # --------------------------------------------------------------- flyout

    def toggle_flyout(self) -> None:
        """A left click: the flyout opens, or closes if it is open."""
        flyout = self.flyout
        if flyout is not None and flyout.isVisible():
            self.hide_flyout()
            return
        if time.monotonic() - self._flyout_hidden_at < REOPEN_GUARD_S:
            # The click on the icon took the focus first, and that closed it:
            # this click meant "close", not "close and open again".
            return
        if flyout is None:
            flyout = TrayFlyout()
            flyout.play_requested.connect(self._flyout_play)
            flyout.start_requested.connect(self.start)
            flyout.open_server_requested.connect(self._flyout_show_server)
            flyout.open_requested.connect(self._flyout_open)
            flyout.quit_requested.connect(self._flyout_quit)
            flyout.settings_requested.connect(self._flyout_settings)
            flyout.dashboard_requested.connect(self._flyout_dashboard)
            flyout.dismissed.connect(self._flyout_dismissed)
            self.flyout = flyout
        self._fill_flyout(self.servers())
        flyout.pop_up(self.icon.geometry() if self.icon is not None else QRect())
        if self._flyout_timer is None:
            # The count line changes under a badge that stays "running" (normal
            # review): re-read the tabs' last verdicts while it shows. No Docker.
            self._flyout_timer = QTimer(self)
            self._flyout_timer.setInterval(FLYOUT_REFRESH_MS)
            self._flyout_timer.timeout.connect(self._refresh_open_flyout)
        self._flyout_timer.start()

    @Slot()
    def _refresh_open_flyout(self) -> None:
        if self.flyout is not None and self.flyout.isVisible():
            self._fill_flyout(self.servers())
        elif self._flyout_timer is not None:
            self._flyout_timer.stop()

    def _flyout_dismissed(self) -> None:
        self._flyout_hidden_at = time.monotonic()

    def hide_flyout(self) -> None:
        if self.flyout is not None and self.flyout.isVisible():
            self.flyout.hide()

    def _fill_flyout(self, servers: list[tuple[Any, str, str]]) -> None:
        if self.flyout is None:
            return
        self.flyout.show_servers(
            header_text([status for _, _, status in servers]),
            [
                (view, title, status, status_words(status), dashboard_entry(view, status))
                for view, title, status in servers
            ],
        )

    def _flyout_play(self, view: Any) -> None:
        self.hide_flyout()
        self.play(view)

    def _flyout_show_server(self, view: Any) -> None:
        self.hide_flyout()
        self.show_server(view)

    def _flyout_dashboard(self, view: Any, kind: str) -> None:
        self.hide_flyout()
        self.dashboard(view, kind)

    def _flyout_open(self) -> None:
        self.hide_flyout()
        self.open_window()

    def _flyout_quit(self) -> None:
        self.hide_flyout()
        self.ask_to_quit()

    def _flyout_settings(self) -> None:
        self.hide_flyout()
        self.open_settings()

    # --------------------------------------------------------------- events

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802 - Qt's name
        kind = event.type()
        if kind is QEvent.Type.Quit:
            # Qt 6 closes every window when the application is asked to quit
            # (macOS Cmd+Q, a session ending): that close is not a hide.
            self._quitting = True
            return False
        if watched is self.window and kind is QEvent.Type.Close:
            if not self.keeping:
                return False
            event.ignore()
            if not self.note_seen:
                self.note_seen = True
                choice = self.choose_note()
                if choice == NOTE_NEVER:
                    ui_settings.remember_tray(tray_note=NOTE_NEVER)
                elif choice == "quit":
                    self.ask_to_quit()
                    return True
            self.hide_window()
            return True
        if kind is QEvent.Type.Show and isinstance(watched, QWidget):
            self._a_window_showed(watched)
        return False

    def choose_note(self) -> str:
        """The first-close note, answered: "got_it", "quit" or "never". A seam for the tests."""
        box, buttons = note_box(self.window)
        try:
            box.exec()
            clicked = box.clickedButton()
            for name, button in buttons.items():
                if clicked is button:
                    return name
            return "got_it"
        finally:
            box.deleteLater()

    def hide_window(self) -> None:
        """Into the tray. The window's jobs, pollers and launchers carry on."""
        logger.info("tray: the window was closed; Yu'lon keeps running in the tray")
        self.window.hide()

    def _a_window_showed(self, widget: QWidget) -> None:
        """A modal box or dialog opening while the window is hidden brings the window back.

        An install finishing, a job's question parented to the hidden window: a
        modal nobody can see is a question nobody answers, and on Windows a
        dialog owned by a hidden window has no taskbar button to find it by.
        """
        if not widget.isWindow() or widget is self.window or not self.window.isHidden():
            return
        if widget.windowModality() is Qt.WindowModality.NonModal and not widget.isModal():
            return
        if widget.property(OWN_DIALOG) is True:
            return
        self.open_window()


OWN_DIALOG = "yulonTrayDialog"
"""A property on the tray's own boxes: asked from the tray, they need no window behind them."""


def quit_box(count: int) -> tuple[QMessageBox, dict[str, QAbstractButton]]:
    """Quit tray… with `count` servers running: leave them (default), stop them, or cancel."""
    box = FittedMessageBox(QMessageBox.Icon.Question, "Quit Yu'lon?", "")
    if count == 1:
        said = (
            "1 server is running. It keeps running after Yu'lon quits: it is a set of Docker "
            "containers, and Yu'lon picks it up again when it starts."
        )
    else:
        said = (
            f"{count} servers are running. They keep running after Yu'lon quits: they are "
            "Docker containers, and Yu'lon picks them up again when it starts."
        )
    box.setText(said)
    box.setProperty(OWN_DIALOG, True)
    plural = "server" if count == 1 else "servers"
    leave = box.addButton("Quit, leave servers running", QMessageBox.ButtonRole.AcceptRole)
    stop = box.addButton(f"Stop {count} {plural}, then quit", QMessageBox.ButtonRole.ActionRole)
    cancel = box.addButton(QMessageBox.StandardButton.Cancel)
    box.setDefaultButton(leave)
    box.setEscapeButton(cancel)
    return box, {"leave": leave, "stop": stop, "cancel": cancel}


def while_stopping_box(count: int) -> tuple[QMessageBox, dict[str, QAbstractButton]]:
    """Quit tray… again while Yu'lon waits for its Stops: keep waiting (default) or quit now."""
    noun = "server" if count == 1 else "servers"
    box = FittedMessageBox(QMessageBox.Icon.Question, "Yu'lon is stopping servers", "")
    box.setText(
        f"Yu'lon is stopping {count} {noun} and will quit when they are down. A stop that is "
        "already running carries on if Yu'lon quits now."
    )
    box.setProperty(OWN_DIALOG, True)
    wait = box.addButton("Keep waiting", QMessageBox.ButtonRole.RejectRole)
    now = box.addButton("Quit now", QMessageBox.ButtonRole.AcceptRole)
    box.setDefaultButton(wait)
    box.setEscapeButton(wait)
    return box, {"wait": wait, "quit": now}


def note_box(parent: QWidget | None = None) -> tuple[QMessageBox, dict[str, QAbstractButton]]:
    """The first close: "Yu'lon stays in the tray", over the window being closed."""
    box = FittedMessageBox(QMessageBox.Icon.Information, "Yu'lon stays in the tray", "")
    if parent is not None:
        # Over the window, not the screen's corner (yulon-ubuntu, GNOME).
        box.setParent(parent, box.windowFlags())
    text = (
        "Yu'lon keeps running in the system tray, so your servers' status and Play are one "
        "click away. Quit tray… in its menu closes it for good."
    )
    if sys.platform == "win32":
        # Windows 11 puts a new tray icon under the ^ beside the clock (seen on yulon-win11).
        text += "\n\nNo icon by the clock? Look under the ^ arrow, and drag it out to keep it."
    box.setText(text)
    got_it = box.addButton("Got it", QMessageBox.ButtonRole.AcceptRole)
    quit_instead = box.addButton("Quit Yu'lon instead", QMessageBox.ButtonRole.DestructiveRole)
    never = box.addButton("Don't show again", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(got_it)
    box.setEscapeButton(got_it)
    return box, {"got_it": got_it, "quit": quit_instead, NOTE_NEVER: never}
