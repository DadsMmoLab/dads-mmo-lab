"""The tray's left-click panel: design C, the compact status list (T551; was T540's cards, B).

"Servers" and "N online" at the top. One 44 px row per server: a status dot
(green online, amber ring starting or stopping, grey stopped, red when it needs
attention), the name, a second line (players · bots when up, else its state),
and small square icon buttons: `server_actions()`, the one list the tray's
right-click menu shows too (T697). Online: **Play**, **Restart**, **Stop**.
Stopped: **Play** (the tab's PLAY starts it, waits for the realm and plays,
T694) and **Start**. Partly up: **Open** (its Server tab says what), **Restart**,
**Stop**; crash-looping: **Open** and **Stop**. None while it starts or stops.
Each press is greyed, with the tab's own reason, whenever the tab's own button
is. A Tortoise row has a further icon for its bot dashboard, in the three states
T540 gave it. Then the tray's two switches, live (`TraySwitches`, the Settings
dialog's own), and **Open Yu'lon** | **Quit Yu'lon…**.

Every button goes through `YulonTray`, which goes through the window and the
tab: Play is the tab's `play()`, Start/Stop/Restart its own presses behind
T690's busy guard. The rows are refilled from the tabs' badges each time the
tray hears one change, so a Start reads "Starting" the moment it is pressed
(T188's held badge).

**A controller reaches it** with no gamepad code here: it is an ordinary active
window, so `gamepad.Navigator` finds it as its context, the D-pad moves between
its buttons, A presses, and B closes it (`BACK_CLOSES`). Focus starts on the
first row's button. Nothing opens it from the pad while Yu'lon is hidden: a
global pad button would fire in the middle of a game.
"""

from __future__ import annotations

import functools
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from yulon import dashboard
from yulon.ui.gamepad import BACK_CLOSES
from yulon.ui.icons import dadcraft_icon
from yulon.ui.theme import (
    COLOR_DANGER,
    COLOR_GOLD_BRIGHT,
    COLOR_TEXT_MUTED,
    COLOR_UNCOMMON,
    DEFAULT_THEME,
    get_theme,
    normalize_theme_name,
)
from yulon.ui.widgets.dadcraft_decorations import realm_tone
from yulon.ui.widgets.reasons import reason_of

FLYOUT_WIDTH = 340
"""Design C's narrow panel: a name and its second line beside one or two icons."""
FLYOUT_MAX_HEIGHT = 560
"""Past this the rows scroll (eight fit); under the 800 px of a Steam Deck."""
ROW_HEIGHT = 44
"""One server, one row: design C's 44 px."""
ICON_BUTTON = 28
"""The row's square icon buttons."""
GAP = 8
"""Between the icon (or the cursor) and the flyout's edge."""

DOT_COLOURS = {
    "up": COLOR_UNCOMMON,
    "between": COLOR_GOLD_BRIGHT,
    "attention": COLOR_DANGER,
    "down": COLOR_TEXT_MUTED,
}

PLAY = "Play"
START = "Start"
STOP = "Stop"
RESTART = "Restart"
OPEN = "Open"
OPEN_YULON = "Open Yu'lon"
QUIT_YULON = "Quit Yu'lon…"
"""The one way out (T697): the menu's, the flyout's and the first-close note's."""

SERVER_PRESSES = (START, STOP, RESTART)
"""The actions that are the tab's own Server presses, each greyed when the tab's is."""


@dataclass(frozen=True)
class ServerAction:
    """One thing the tray offers to do to one server: its verb, live or not, and why not."""

    name: str
    enabled: bool
    reason: str = ""


def _gate(press: Any) -> tuple[bool, str]:
    """The tab's button as the tray must show it: live, or greyed with the tab's reason."""
    if press is None or press.isEnabled():
        return True, ""
    return False, reason_of(press) or press.toolTip()


def server_actions(view: Any, status: str) -> tuple[ServerAction, ...]:
    """What the tray offers for one server, in order: the menu's and the flyout's one list (T697).

    Online: Play, Restart, Stop. Stopped: Play (the tab's PLAY starts it first, T694, so it is
    greyed when Start is) and Start. Partly up: Open, Restart, Stop. A crash loop: Open, Stop.
    "Update failed" or unknown: Open. Starting, stopping or restarting: nothing to press.
    """
    word = status.lower()
    tone = dot_tone(status)
    actions: list[ServerAction] = []
    if tone == "up":
        actions.append(ServerAction(PLAY, True))
    elif tone == "down" and realm_tone(status) != "unknown":
        enabled, why = _gate(getattr(view, "start_button", None))
        actions += [ServerAction(PLAY, enabled, why), ServerAction(START, enabled, why)]
    elif tone == "attention" or realm_tone(status) == "unknown":
        actions.append(ServerAction(OPEN, True))
    if tone == "up" or word == "partial":
        actions.append(ServerAction(RESTART, *_gate(getattr(view, "restart_button", None))))
    if tone == "up" or word in ("partial", "loop"):
        actions.append(ServerAction(STOP, *_gate(getattr(view, "stop_button", None))))
    return tuple(actions)


def card_detail(verdict: Any) -> str:
    """ "3 players · 500 bots · up 2h 5m": only what the verdict really read (None is unread)."""
    if not isinstance(verdict, dashboard.Verdict):
        return ""
    parts: list[str] = []
    if verdict.players is not None:
        parts.append(f"{verdict.players} player{'' if verdict.players == 1 else 's'}")
    if verdict.bots is not None:
        parts.append(f"{verdict.bots} bot{'' if verdict.bots == 1 else 's'}")
    if parts and verdict.uptime is not None:
        parts.append(dashboard.uptime_text(verdict.uptime))
    return " · ".join(parts)


def row_detail(verdict: Any) -> str:
    """ "3 players · 500 bots": design C's second line for a realm that is up (None is unread)."""
    if not isinstance(verdict, dashboard.Verdict):
        return ""
    parts: list[str] = []
    if verdict.players is not None:
        parts.append(f"{verdict.players} player{'' if verdict.players == 1 else 's'}")
    if verdict.bots is not None:
        parts.append(f"{verdict.bots} bot{'' if verdict.bots == 1 else 's'}")
    return " · ".join(parts)


def dot_tone(status: str) -> str:
    """The row's dot: "up", "between", "attention" or "down" (unknown claims nothing: grey)."""
    word = status.lower()
    if word in ("loop", "partial", "failed"):
        return "attention"
    tone = realm_tone(word)
    if tone == "up":
        return "up"
    if tone in ("between", "restarting"):
        return "between"
    return "down"


def flyout_position(
    anchor: QRect, cursor: QPoint, available: QRect, size: tuple[int, int]
) -> QPoint:
    """Where the flyout's top-left goes: by the icon, else by the cursor, always on screen.

    Above an icon in the lower half of the screen (a Windows taskbar), below one
    in the upper half (a GNOME or KDE top panel), right edges lined up. A
    StatusNotifierItem tray gives no geometry, so then the cursor -- which is on
    the icon it just clicked -- stands in for it.
    """
    width, height = size
    box = anchor if anchor.isValid() and not anchor.isEmpty() else QRect(cursor, cursor)
    right = box.right() + 1 if box.width() > 1 else box.left()
    x = right - width
    if box.center().y() > available.center().y():
        y = box.top() - height - GAP
    else:
        y = box.bottom() + 1 + GAP if box.height() > 1 else box.top() + GAP
    x = max(available.left(), min(x, available.right() + 1 - width))
    y = max(available.top(), min(y, available.bottom() + 1 - height))
    return QPoint(x, y)


class StatusDot(QWidget):
    """The row's status dot: filled green, an amber ring, filled grey, or filled red."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tone = "down"
        self.setFixedSize(14, 14)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self.update()

    def paintEvent(self, _event: object) -> None:  # noqa: N802 - Qt's own name
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colour = QColor(DOT_COLOURS.get(self.tone, COLOR_TEXT_MUTED))
        if self.tone == "between":
            painter.setPen(QPen(colour, 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(3, 3, 8, 8))
        else:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(colour)
            painter.drawEllipse(QRectF(2, 2, 10, 10))
        painter.end()


def _icon_button(parent: QWidget) -> QToolButton:
    button = QToolButton(parent)
    button.setFixedSize(ICON_BUTTON, ICON_BUTTON)
    button.setIconSize(QSize(14, 14))
    button.setObjectName("tray-row-button")
    return button


class ServerCard(QFrame):
    """One server, one 44 px row: dot, name over a second line, and its icon buttons.

    `action` is the row's first button (Play, or Open), rightmost, where the pad's focus
    starts; `start`, `stop` and `restart` sit to its left, each shown only when
    `server_actions()` offers it.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tray-row")
        self.setFixedHeight(ROW_HEIGHT)
        self.view: Any = None
        self.which = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 6, 2)
        row.setSpacing(8)
        self.dot = StatusDot(self)
        row.addWidget(self.dot, 0, Qt.AlignmentFlag.AlignVCenter)
        words = QVBoxLayout()
        words.setSpacing(0)
        self.title = QLabel(self)
        self.title.setObjectName("tray-row-title")
        self.detail = QLabel(self)
        self.detail.setObjectName("tray-row-detail")
        for label in (self.title, self.detail):
            # The words give way, never the buttons (T540: Play was pushed off the edge).
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        words.addWidget(self.title)
        words.addWidget(self.detail)
        row.addLayout(words, 1)
        # A Tortoise server's TortoiseBots dashboard (T540), a second icon.
        self.dashboard = _icon_button(self)
        self.dashboard.setIcon(dadcraft_icon("robot"))
        self.dashboard.setVisible(False)
        self.dashboard_kind = ""
        row.addWidget(self.dashboard, 0, Qt.AlignmentFlag.AlignVCenter)
        # T697: the tab's own Server presses, the same ones the tray's menu offers.
        self.stop = _icon_button(self)
        self.restart = _icon_button(self)
        self.start = _icon_button(self)
        self.presses = {STOP: self.stop, RESTART: self.restart, START: self.start}
        for press in self.presses.values():
            press.setVisible(False)
            row.addWidget(press, 0, Qt.AlignmentFlag.AlignVCenter)
        self.stop.setIcon(dadcraft_icon("stop", "#FFB8B8"))
        self.restart.setIcon(dadcraft_icon("restart"))
        self.start.setIcon(power_icon())
        self.action = _icon_button(self)
        row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self.presses_armed: dict[str, Any] = {}
        """Per press, the server it was pressed on (the same check as `armed`)."""

    armed: tuple[Any, str] | None = None
    """(server, action) as they were when the icon button was pressed."""

    dashboard_armed: tuple[Any, str] | None = None
    """(server, dashboard kind) as they were when the dashboard icon was pressed."""

    def arm(self) -> None:
        self.armed = (self.view, self.which)

    def arm_dashboard(self) -> None:
        self.dashboard_armed = (self.view, self.dashboard_kind)

    def arm_press(self, name: str) -> None:
        self.presses_armed[name] = self.view

    def action_buttons(self) -> list[QToolButton]:
        """Every button that acts on this server (not the dashboard), shown or not."""
        return [self.action, *self.presses.values()]

    def show_server(self, view: Any, title: str, status: str, words: str, entry: Any = None) -> str:
        """Fill the row; answers which first button it shows (PLAY, OPEN or "")."""
        self.view = view
        self.title.setText(title)
        self.title.setToolTip(f"{title}\n{view.services.controller.server_dir}")
        tone = dot_tone(status)
        self.dot.set_tone(tone)
        self.dot.setToolTip(words)
        # A dot is colour; a screen reader gets the words (cold review).
        self.dot.setAccessibleName(
            words
            if realm_tone(status) == "unknown"  # grey like stopped, but not known to be (Codex)
            else {"up": "Online", "attention": "Needs attention", "down": "Stopped"}.get(
                tone, words
            )
        )
        verdict = getattr(view, "last_verdict", None)
        counts = row_detail(verdict) if tone == "up" else ""
        self.detail.setText(counts or words)
        self.detail.setToolTip(card_detail(verdict) if tone == "up" else words)
        offered = {action.name: action for action in server_actions(view, status)}
        first = offered.get(PLAY) or offered.get(OPEN)
        which = first.name if first is not None else ""
        self.which = which
        self._fill(self.action, first, title)
        self.action.setIcon(dadcraft_icon(_ICONS[which]) if which else QIcon())
        for name, press in self.presses.items():
            self._fill(press, offered.get(name), title)
        self.dashboard.setVisible(entry is not None)
        self.dashboard_kind = ""
        if entry is not None:
            self.dashboard.setAccessibleName(f"{entry.label} ({title})")
            self.dashboard.setEnabled(entry.enabled)
            self.dashboard.setToolTip(entry.reason or entry.label)
            self.dashboard_kind = entry.kind
        return which

    @staticmethod
    def _fill(button: QToolButton, action: ServerAction | None, title: str) -> None:
        """Show `action` on `button` (greyed with the tab's reason, T195), or hide it."""
        button.setVisible(action is not None)
        if action is None:
            button.setAccessibleName("")
            return
        button.setEnabled(action.enabled)
        button.setToolTip(f"{action.name} {title}" if action.enabled else action.reason)
        # Which server, not only the verb (cold review): "Play WoW WotLK".
        button.setAccessibleName(f"{action.name} {title}")


_ICONS = {PLAY: "play", OPEN: "wrench"}
"""Play is the sidebar ▶'s icon and Open a wrench; Start is `power_icon()`, Stop and
Restart the Server tab's own icons."""


@functools.cache
def power_icon() -> QIcon:
    """Start's icon: the power symbol, an open ring with a line through its top (lead,
    T551: the server glyph's two bars did not read as Start, and ▶ is Play's)."""
    icon = QIcon()
    for size in (16, 24, 32, 48):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(COLOR_GOLD_BRIGHT), max(1.5, size * 0.11))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        inset = size * 0.14
        ring = QRectF(inset, inset + size * 0.04, size - 2 * inset, size - 2 * inset)
        # Qt angles are in 1/16 degree, counter-clockwise from 3 o'clock: leave
        # a 60° gap at the top for the line.
        painter.drawArc(ring, 120 * 16, 300 * 16)
        painter.drawLine(QPointF(size / 2, size * 0.1), QPointF(size / 2, size * 0.48))
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class TrayFlyout(QWidget):
    """The panel window. `YulonTray` fills it (`show_servers`) and acts on its signals."""

    play_requested = Signal(object)
    start_requested = Signal(object)
    stop_requested = Signal(object)
    restart_requested = Signal(object)
    open_server_requested = Signal(object)
    open_requested = Signal()
    quit_requested = Signal()
    dashboard_requested = Signal(object, str)
    """(view, kind): a row's bot dashboard icon was pressed."""
    dismissed = Signal()
    """It closed itself because something else took the focus (a click elsewhere)."""

    def __init__(self, switches: QWidget | None = None) -> None:
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setObjectName("tray-flyout")
        self.setWindowTitle("Yu'lon")
        self.setProperty(BACK_CLOSES, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        self.setFixedWidth(FLYOUT_WIDTH)
        self._theme_name = DEFAULT_THEME
        self.set_theme(DEFAULT_THEME)
        column = QVBoxLayout(self)
        column.setContentsMargins(10, 8, 10, 10)
        column.setSpacing(6)
        top = QHBoxLayout()
        self.header = QLabel("Servers", self)
        self.header.setObjectName("tray-header")
        self.count = QLabel(self)
        self.count.setObjectName("tray-count")
        top.addWidget(self.header, 1)
        top.addWidget(self.count, 0, Qt.AlignmentFlag.AlignRight)
        column.addLayout(top)
        self._list = QWidget()
        self._cards_box = QVBoxLayout(self._list)
        self._cards_box.setContentsMargins(0, 0, 0, 0)
        self._cards_box.setSpacing(4)
        self._cards_box.addStretch(1)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._list)
        column.addWidget(self._scroll, 1)
        self.empty = QLabel("No servers yet: install one from the Catalog.", self)
        self.empty.setObjectName("tray-row-detail")
        self.empty.setWordWrap(True)
        column.addWidget(self.empty)
        self.switches = switches
        if switches is not None:
            switches.setParent(self)
            column.addWidget(switches)
        foot = QHBoxLayout()
        self.open_button = QPushButton(OPEN_YULON, self)
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(self.open_requested)
        self.quit_button = QPushButton(QUIT_YULON, self)
        self.quit_button.clicked.connect(self.quit_requested)
        # Split in two, as design C draws it.
        foot.addWidget(self.open_button, 1)
        foot.addWidget(self.quit_button, 1)
        column.addLayout(foot)
        self._cards: list[ServerCard] = []

    def set_theme(self, theme_name: str | None = None) -> None:
        """Restyle the flyout panel for `theme_name`."""
        self._theme_name = normalize_theme_name(theme_name)
        spec = get_theme(self._theme_name)
        self.setStyleSheet(f"""
            QWidget#tray-flyout {{
                background: {spec.bg_panel};
                border: 1px solid {spec.accent_brass};
            }}
            QFrame#tray-row {{
                background: {spec.bg_container};
                border: 1px solid {spec.border_dark};
                border-radius: 4px;
            }}
            QLabel#tray-header {{ color: {spec.text_primary}; font-weight: bold; }}
            QLabel#tray-count {{ color: {spec.text_muted}; }}
            QLabel#tray-row-title {{ color: {spec.text_primary}; font-weight: bold; }}
            QLabel#tray-row-detail {{ color: {spec.text_muted}; font-size: 11px; }}
            QToolButton#tray-row-button {{
                border: 2px solid {spec.border_dark};
                border-radius: 4px;
                padding: 0;
                min-width: {ICON_BUTTON}px;
                max-width: {ICON_BUTTON}px;
                min-height: {ICON_BUTTON}px;
                max-height: {ICON_BUTTON}px;
            }}
            /* Where the pad or the keyboard is: the theme's focus edge. */
            QToolButton#tray-row-button:focus {{
                border: 2px solid {spec.accent_bright};
            }}
            """)

    def cards(self) -> list[ServerCard]:
        return [card for card in self._cards if not card.isHidden()]

    def show_servers(self, count: str, servers: Sequence[tuple[Any, str, str, str, Any]]) -> None:
        """("N online", then (view, title, badge word, state words, dashboard entry) per server).

        Rows are reused, so a row the pad's focus is on keeps it across a refresh.
        """
        self.count.setText(count)
        while len(self._cards) < len(servers):
            card = ServerCard(self._list)
            card.action.pressed.connect(card.arm)
            card.action.clicked.connect(lambda _c=False, c=card: self._pressed(c))
            card.dashboard.pressed.connect(card.arm_dashboard)
            card.dashboard.clicked.connect(lambda _c=False, c=card: self._dashboard_pressed(c))
            for name, press in card.presses.items():
                press.pressed.connect(lambda c=card, n=name: c.arm_press(n))
                press.clicked.connect(lambda _c=False, c=card, n=name: self._press_pressed(c, n))
            self._cards_box.insertWidget(len(self._cards), card)
            self._cards.append(card)
        for card, (view, title, status, words, entry) in zip(self._cards, servers, strict=False):
            card.show_server(view, title, status, words, entry)
            card.setVisible(True)
        for card in self._cards[len(servers) :]:
            card.setVisible(False)
            card.view = None
        self.empty.setVisible(not servers)
        self._fit()

    def _fit(self) -> None:
        rows = len(self.cards())
        wanted = rows * ROW_HEIGHT + max(0, rows - 1) * self._cards_box.spacing() + 2
        room = FLYOUT_MAX_HEIGHT - 200  # header, switches and footer
        self._scroll.setFixedHeight(max(0, min(wanted, room)))
        self.adjustSize()

    def _dashboard_pressed(self, card: ServerCard) -> None:
        # The same press-time check as the row's own button (Codex on 4e3549e9).
        if card.dashboard_armed != (card.view, card.dashboard_kind):
            return
        if card.view is not None and card.dashboard_kind:
            self.dashboard_requested.emit(card.view, card.dashboard_kind)

    def _press_pressed(self, card: ServerCard, name: str) -> None:
        """Start, Stop or Restart: only on the server the press began on (T551's check)."""
        view = card.view
        armed = card.presses_armed.pop(name, None)
        if view is None or armed is not view or card.presses[name].isHidden():
            return
        signal = {
            START: self.start_requested,
            STOP: self.stop_requested,
            RESTART: self.restart_requested,
        }[name]
        signal.emit(view)

    def _pressed(self, card: ServerCard) -> None:
        view = card.view
        if view is None or card.armed != (view, card.which):
            # Rows are reused by position: a refresh between the press and the
            # release made this row another server's (T551 adversarial). The
            # click goes nowhere rather than to a server it was not begun on.
            return
        if card.which == PLAY:
            self.play_requested.emit(view)
        elif card.which == OPEN:
            self.open_server_requested.emit(view)

    def first_button(self) -> QWidget:
        """Where the pad's focus starts: the first row's button, else Open Yu'lon."""
        for card in self.cards():
            if not card.action.isHidden() and card.action.isEnabled():
                return card.action
        return self.open_button

    def pop_up(self, anchor: QRect) -> None:
        """Show by the icon (or the cursor), activated, with focus on the first button."""
        reread = getattr(self.switches, "read", None)
        if callable(reread):
            reread()
        self.adjustSize()
        cursor = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor) or QGuiApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else QRect(0, 0, 1280, 800)
        self.move(
            flyout_position(anchor, cursor, available, (self.width(), self.sizeHint().height()))
        )
        self.show()
        self.raise_()
        self.activateWindow()
        self.first_button().setFocus(Qt.FocusReason.OtherFocusReason)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt's own name
        """Gone as soon as something else is clicked, like any tray flyout."""
        super().changeEvent(event)
        if (
            event.type() is QEvent.Type.ActivationChange
            and self.isVisible()
            and not self.isActiveWindow()
            and self._was_active
        ):
            self.hide()
            self.dismissed.emit()
        if event.type() is QEvent.Type.ActivationChange:
            self._was_active = self.isActiveWindow()

    _was_active = False
