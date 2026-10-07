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

from collections.abc import Callable, Sequence
from typing import Any, Protocol

import shiboken6
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QSize, Qt, Signal, Slot
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QWidget

from yulon.log import get_logger
from yulon.ui.tab_titles import controller_tab_titles
from yulon.ui.theme import COLOR_DANGER, COLOR_GOLD_BRIGHT, COLOR_UNCOMMON
from yulon.ui.widgets.dadcraft_decorations import realm_tone

logger = get_logger(__name__)

OPEN_YULON = "Open Yu'lon"
PLAY = "Play"
LOGS = "Logs"
QUIT_TRAY = "Quit tray…"

STATE_COLOURS = {"up": COLOR_UNCOMMON, "between": COLOR_GOLD_BRIGHT, "attention": COLOR_DANGER}
"""The dot per icon state: the theme's online green, amber accent and danger red."""

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
    if "restarting" in tones or "partial" in words:
        return "attention"
    if "between" in tones:
        return "between"
    if "up" in tones:
        return "up"
    return "plain"


def tray_tooltip(servers: Sequence[tuple[str, str]]) -> str:
    """ "Yu'lon: N servers online" and, one per line, which."""
    online = [name for name, status in servers if is_online(status)]
    if not online:
        return "Yu'lon: no servers online"
    noun = "server" if len(online) == 1 else "servers"
    return "\n".join([f"Yu'lon: {len(online)} {noun} online", *online])


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
        keep_in_tray: bool = True,
    ) -> None:
        super().__init__(window)
        self.window = window
        self._icon_factory = icon_factory or (lambda parent: QSystemTrayIcon(parent))
        self._available = available
        self._bring_forward = bring_forward
        self.keep_in_tray = keep_in_tray
        self.icon: TrayIcon | None = None
        self.state = "plain"
        self.note_seen = False
        """Whether the "Yu'lon stays in the tray" note was shown this run (step 3)."""
        self._quitting = False
        self._installed = False
        self._followed: list[Any] = []
        self._menu: QMenu | None = None
        self._was_quit_on_last = True

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
        if self.has_tray:
            icon = self._icon_factory(self)
            self.icon = icon
            self._menu = QMenu()
            self._menu.aboutToShow.connect(self._refill_menu)
            icon.setContextMenu(self._menu)
            icon.activated.connect(self._activated)
        else:
            logger.info("tray: this desktop has no system tray; closing Yu'lon quits it")
        self.follow_servers()
        self.set_keep_in_tray(self.keep_in_tray)

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
        self._let_go_of_servers()
        if self.icon is not None:
            self.icon.hide()
        if self._menu is not None:
            self._menu.deleteLater()
            self._menu = None

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
            self._followed.append(view)
        self.refresh()

    def _let_go_of_servers(self) -> None:
        for view in self._followed:
            if shiboken6.isValid(view) and shiboken6.isValid(view.realm_badge):
                try:
                    view.realm_badge.status_changed.disconnect(self._server_changed)
                except (RuntimeError, TypeError):  # pragma: no cover - already gone
                    pass
        self._followed = []

    @Slot(str)
    def _server_changed(self, _status: str) -> None:
        self.refresh()

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
        if changed:
            self.state_changed.emit(state)

    # -------------------------------------------------------------- actions

    def open_window(self) -> None:
        self._bring_forward(self.window)

    def start(self, view: Any) -> None:
        """Start one server through its own tab's Start: the same job, guards and badge."""
        view.start_server()

    def play(self, view: Any) -> None:
        """Open this server's client launcher window, the sidebar ▶'s way (T187)."""
        opener = getattr(self.window, "yulon_open_launcher", None)
        if opener is not None:
            opener(view.entry.id, view.services.controller.server_dir)

    def show_server(self, view: Any) -> None:
        shower = getattr(self.window, "yulon_show_server_tab", None)
        if shower is not None:
            shower(view.entry.id, view.services.controller.server_dir)
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

    def ask_to_quit(self) -> None:
        """Quit tray… (step 3 asks about running servers first)."""
        self.quit()

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
        online = sum(1 for _, _, status in servers if is_online(status))
        noun = "server" if len(servers) == 1 else "servers"
        header = menu.addAction(f"Yu'lon — {online} of {len(servers)} {noun} online")
        header.setEnabled(False)
        for _view, title, status in servers:
            row = menu.addAction(f"{title} — {status_words(status)}")
            row.setEnabled(False)
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
        if reason in (
            QSystemTrayIcon.ActivationReason.DoubleClick,
            QSystemTrayIcon.ActivationReason.Trigger,
        ):
            # Step 2 puts the flyout on a single click.
            self.open_window()

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
            self.hide_window()
            return True
        if kind is QEvent.Type.Show and isinstance(watched, QWidget):
            self._a_window_showed(watched)
        return False

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
