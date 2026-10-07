"""The tray's left-click flyout (T540, the owner's Option B).

A small charcoal sheet by the tray icon: "Yu'lon — N of M servers online" and a
gear at the top, one card per server, **Open Yu'lon** and **Quit tray…** at the
foot. A card is the server's name, a status pill in the badge's colours, the
players, bots and uptime when the realm is up and they are known, and one
button: **Play** when the realm is online, **Start** when it is stopped (greyed,
with the tab's own reason, whenever the tab's Start is), **Open** when it needs
attention (a crash loop, partly up) -- its Server tab says what and offers the fix.

Every button goes through `YulonTray`, which goes through the window: Play is the
sidebar ▶'s `open_launcher`, Start is the tab's `start_server`. The cards are
rebuilt from the tabs' badges each time the tray hears one change, so a Start
reads "Starting" the moment it is pressed (T188's held badge).

**A controller reaches it** with no gamepad code here: it is an ordinary active
window, so `gamepad.Navigator` finds it as its context, the D-pad moves between
its buttons, A presses, and B closes it (`BACK_CLOSES`). Focus starts on the
first card's button. Nothing opens it from the pad while Yu'lon is hidden: a
global pad button would fire in the middle of a game.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from yulon import dashboard
from yulon.ui.gamepad import BACK_CLOSES
from yulon.ui.theme import (
    COLOR_BG_CONTAINER,
    COLOR_BG_PANEL,
    COLOR_BRASS_DARK,
    COLOR_DANGER,
    COLOR_GOLD_BRASS,
    COLOR_GOLD_BRIGHT,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_UNCOMMON,
)
from yulon.ui.widgets.dadcraft_decorations import realm_tone

FLYOUT_WIDTH = 360
"""Wide enough for "WoW WotLK — DadsMmoLab" and a pill on one line."""
FLYOUT_MAX_HEIGHT = 560
"""Past this the cards scroll: six servers fit, a seventh scrolls."""
GAP = 8
"""Between the icon (or the cursor) and the flyout's edge."""

PILL_COLOURS = {
    "up": COLOR_UNCOMMON,
    "between": COLOR_GOLD_BRIGHT,
    "restarting": COLOR_DANGER,
    "unknown": COLOR_TEXT_MUTED,
    "down": COLOR_TEXT_MUTED,
}

PLAY = "Play"
START = "Start"
OPEN = "Open"
OPEN_YULON = "Open Yu'lon"
QUIT_TRAY = "Quit tray…"


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


class ServerCard(QFrame):
    """One server: title, status pill, the count line, one button."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("tray-card")
        self.view: Any = None
        # One row: the name (with the count line under it), the pill, the button.
        # A second row only for the count, so a stopped server's card is one line.
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 8, 6)
        row.setSpacing(8)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.title = QLabel(self)
        self.title.setObjectName("tray-card-title")
        self.detail = QLabel(self)
        self.detail.setObjectName("tray-card-detail")
        words.addWidget(self.title)
        words.addWidget(self.detail)
        row.addLayout(words, 1)
        self.pill = QLabel(self)
        self.pill.setObjectName("tray-pill")
        row.addWidget(self.pill, 0, Qt.AlignmentFlag.AlignVCenter)
        self.action = QPushButton(self)
        self.action.setMinimumWidth(72)
        row.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)

    def show_server(self, view: Any, title: str, status: str, words: str) -> str:
        """Fill the card; answers which button it shows (PLAY, START, OPEN or "")."""
        self.view = view
        self.title.setText(title)
        self.title.setToolTip(str(view.services.controller.server_dir))
        tone = realm_tone(status)
        colour = PILL_COLOURS.get(tone, COLOR_TEXT_MUTED)
        if status.lower() == "partial":
            colour = COLOR_DANGER
        self.pill.setText(words)
        self.pill.setStyleSheet(
            f"color: {colour}; border: 1px solid {colour}; border-radius: 8px; padding: 1px 8px;"
        )
        detail = card_detail(getattr(view, "last_verdict", None)) if tone == "up" else ""
        self.detail.setText(detail)
        self.detail.setVisible(bool(detail))
        if tone == "up":
            which = PLAY
            enabled, why = True, f"Open the game launcher for {title}"
        elif tone in ("restarting",) or status.lower() == "partial":
            which = OPEN
            enabled, why = True, f"Open Yu'lon on {title}'s Server tab"
        elif tone == "unknown":
            which = OPEN
            enabled, why = True, f"Open Yu'lon on {title}'s Server tab"
        else:
            # Down or in between: the tab's own Start, greyed whenever the tab's
            # is, with the tab's own reason (T195).
            which = START
            gate = view.start_button
            enabled = tone == "down" and gate.isEnabled()
            why = gate.toolTip() if tone == "down" and not gate.isEnabled() else ""
            if tone == "between":
                why = f"{title} is {words.lower()}"
        self.action.setText(which)
        self.action.setEnabled(enabled)
        self.action.setToolTip(why)
        self.action.setProperty("primary", which == PLAY)
        self.action.style().unpolish(self.action)
        self.action.style().polish(self.action)
        return which


class TrayFlyout(QWidget):
    """The flyout window. `YulonTray` fills it (`show_servers`) and acts on its signals."""

    play_requested = Signal(object)
    start_requested = Signal(object)
    open_server_requested = Signal(object)
    open_requested = Signal()
    quit_requested = Signal()
    settings_requested = Signal()
    dismissed = Signal()
    """It closed itself because something else took the focus (a click elsewhere)."""

    def __init__(self) -> None:
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
        self.setStyleSheet(f"""
            QWidget#tray-flyout {{
                background: {COLOR_BG_PANEL};
                border: 1px solid {COLOR_GOLD_BRASS};
            }}
            QFrame#tray-card {{
                background: {COLOR_BG_CONTAINER};
                border: 1px solid {COLOR_BRASS_DARK};
                border-radius: 4px;
            }}
            QLabel#tray-header {{ color: {COLOR_TEXT_PRIMARY}; font-weight: bold; }}
            QLabel#tray-card-title {{ color: {COLOR_TEXT_PRIMARY}; font-weight: bold; }}
            QLabel#tray-card-detail {{ color: {COLOR_TEXT_MUTED}; }}
            QToolButton {{ color: {COLOR_GOLD_BRIGHT}; font-size: 16px; }}
            """)
        column = QVBoxLayout(self)
        column.setContentsMargins(12, 10, 12, 12)
        column.setSpacing(8)
        top = QHBoxLayout()
        self.header = QLabel(self)
        self.header.setObjectName("tray-header")
        self.settings_button = QToolButton(self)
        # U+FE0E asks for the text glyph, so it takes the amber like the rest.
        self.settings_button.setText("⚙\ufe0e")
        self.settings_button.setToolTip("Settings")
        self.settings_button.setAccessibleName("Settings")
        self.settings_button.setAutoRaise(True)
        self.settings_button.clicked.connect(self.settings_requested)
        top.addWidget(self.header, 1)
        top.addWidget(self.settings_button)
        column.addLayout(top)
        self._list = QWidget()
        self._cards_box = QVBoxLayout(self._list)
        self._cards_box.setContentsMargins(0, 0, 0, 0)
        self._cards_box.setSpacing(6)
        self._cards_box.addStretch(1)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll.setWidget(self._list)
        column.addWidget(self._scroll, 1)
        self.empty = QLabel("No servers yet: install one from the Catalog.", self)
        self.empty.setObjectName("tray-card-detail")
        self.empty.setWordWrap(True)
        column.addWidget(self.empty)
        foot = QHBoxLayout()
        self.open_button = QPushButton(OPEN_YULON, self)
        self.open_button.setProperty("primary", True)
        self.open_button.clicked.connect(self.open_requested)
        self.quit_button = QPushButton(QUIT_TRAY, self)
        self.quit_button.clicked.connect(self.quit_requested)
        foot.addWidget(self.open_button, 1)
        foot.addWidget(self.quit_button)
        column.addLayout(foot)
        self._cards: list[ServerCard] = []

    def cards(self) -> list[ServerCard]:
        return [card for card in self._cards if not card.isHidden()]

    def show_servers(self, header: str, servers: Sequence[tuple[Any, str, str, str]]) -> None:
        """(view, title, badge word, pill words) per server. Cards are reused, not rebuilt.

        Reused so a card the pad's focus is on keeps it across a refresh.
        """
        self.header.setText(header)
        while len(self._cards) < len(servers):
            card = ServerCard(self._list)
            card.action.clicked.connect(lambda _c=False, c=card: self._pressed(c))
            self._cards_box.insertWidget(len(self._cards), card)
            self._cards.append(card)
        for card, (view, title, status, words) in zip(self._cards, servers, strict=False):
            card.show_server(view, title, status, words)
            card.setVisible(True)
        for card in self._cards[len(servers) :]:
            card.setVisible(False)
            card.view = None
        self.empty.setVisible(not servers)
        self._fit()

    def _fit(self) -> None:
        self._list.adjustSize()
        wanted = self._list.sizeHint().height() + 4
        self._scroll.setFixedHeight(min(wanted, FLYOUT_MAX_HEIGHT - 120))
        self.adjustSize()

    def _pressed(self, card: ServerCard) -> None:
        view = card.view
        if view is None:
            return
        which = card.action.text()
        if which == PLAY:
            self.play_requested.emit(view)
        elif which == START:
            self.start_requested.emit(view)
        elif which == OPEN:
            self.open_server_requested.emit(view)

    def first_button(self) -> QWidget:
        """Where the pad's focus starts: the first card's button, else Open Yu'lon."""
        for card in self.cards():
            if card.action.isEnabled():
                return card.action
        return self.open_button

    def pop_up(self, anchor: QRect) -> None:
        """Show by the icon (or the cursor), activated, with focus on the first button."""
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
