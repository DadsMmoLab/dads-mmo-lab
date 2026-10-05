"""The window's left rail: short server names, and Catalog and Logs pinned above them (T192).

The rail is the sidebar `QTabWidget`'s own `QTabBar`, so every server tab is
still a tab and Catalog and Logs are still tabs 0 and 1: `indexOf()`, the
gamepad bumpers (`gamepad.py`'s `_cycle`) and `setCurrentIndex(0)` work exactly
as before. What changes is where the two are DRAWN. They are hidden in the bar
(`QTabBar.setTabVisible`) and `SidebarPins` draws them as two buttons above it,
so a rail with more servers than fit scrolls the servers and never the Catalog
away -- the Catalog used to be tab 0 of the scrolling bar.

Three things a plain `QTabBar` gets wrong once its current tab is hidden, and
`SidebarRail` puts right:

* With every tab hidden it asks for 0x0, and `QTabWidget` lays the page out
  under where the pins are. The rail keeps `RAIL_MIN_WIDTH` whatever it holds.
* It paints the current tab last, on top, without asking whether that tab is
  visible: a gold-rimmed "Catalog" box appeared over the first server tab.
* When the last visible tab goes it has no visible neighbour to move to and
  answers -1, an empty pane; the rail shows the Catalog instead.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, Slot
from PySide6.QtGui import QBrush, QColor, QIcon, QKeyEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import (
    QStyle,
    QStyleOptionTab,
    QStylePainter,
    QTabBar,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from yulon.ui.theme import (
    COLOR_GOLD_BRIGHT,
    COLOR_RARE,
    COLOR_TEXT_MUTED,
    COLOR_UNCOMMON,
    SIDEBAR_PIN_BUTTON,
    SIDEBAR_PIN_GAP,
    SIDEBAR_RAIL_WIDTH,
)
from yulon.ui.widgets.dadcraft_decorations import realm_tone

RAIL_MIN_WIDTH = SIDEBAR_RAIL_WIDTH
"""The rail's thickness floor (px): a server tab's styled width, so the rail is
as wide with no server tab as with five."""


def short_game_name(name: str) -> str:
    """The game's name on the rail: "WoW WotLK" -> "WotLK", "Centurion" unchanged.

    Every WoW title in the catalog shares the "WoW " prefix, and on a 64px rail
    it is the half of the name that says nothing. The full name is on the
    tab's tooltip.
    """
    return name.removeprefix("WoW ")


DOT_FILLS = {"up": COLOR_UNCOMMON, "between": COLOR_GOLD_BRIGHT, "restarting": COLOR_RARE}
"""The status dot's fill per `realm_tone()`: the realm badge's own border colours."""


def status_dot_geometry(size: int) -> tuple[QPointF, float, float]:
    """The dot on a `size`-px tab icon: its centre, its radius, and the clear ring's radius.

    Bottom-right, over the server glyph, and the clear ring around it is what
    keeps it a dot: the glyph is the same gold as "in between".
    """
    radius = size * 0.22
    centre = size - radius - 0.5
    return QPointF(centre, centre), radius, radius + size * 0.1


def server_tab_icon(status: str) -> QIcon:
    """A server tab's icon: the server glyph with a dot that says how its realm is (T192).

    Up is filled green, in between (starting, stopping, partly up) filled
    amber, restarting filled blue -- the badge's colours, through the same
    `realm_tone()` -- and stopped a hollow grey ring. Unknown draws no dot at
    all: Docker did not answer, and a ring would claim the realm is offline
    (T188).
    """
    from yulon.ui.icons import get_tab_icon

    glyph = get_tab_icon("server")
    tone = realm_tone(status)
    if tone == "unknown":
        return glyph
    base = glyph.availableSizes()[0] if glyph.availableSizes() else QSize(16, 16)
    pixmap = glyph.pixmap(base)
    size = pixmap.width()
    centre, radius, halo = status_dot_geometry(size)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
    painter.setBrush(QBrush(Qt.GlobalColor.black))
    painter.drawEllipse(centre, halo, halo)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
    fill = DOT_FILLS.get(tone)
    if fill is not None:
        painter.setBrush(QColor(fill))
        painter.drawEllipse(centre, radius, radius)
    else:
        ring = size * 0.1
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(COLOR_TEXT_MUTED), ring))
        painter.drawEllipse(centre, radius - ring / 2, radius - ring / 2)
    painter.end()
    return QIcon(pixmap)


class SidebarRail(QTabBar):
    """The sidebar's tab bar: a width floor, and no hidden tab drawn or left current."""

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        hint = super().sizeHint()
        return QSize(max(RAIL_MIN_WIDTH, hint.width()), hint.height())

    def minimumSizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        hint = super().minimumSizeHint()
        return QSize(max(RAIL_MIN_WIDTH, hint.width()), hint.height())

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802  (Qt's own name)
        current = self.currentIndex()
        if current < 0 or self.isTabVisible(current):
            super().paintEvent(event)
            return
        # The current tab is hidden (the Catalog or Logs): draw the visible tabs
        # and nothing selected. `tabRect()` already carries the scroll offset,
        # and the tab buttons (▶ ×) are child widgets that paint themselves.
        painter = QStylePainter(self)
        for index in range(self.count()):
            if not self.isTabVisible(index):
                continue
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            if not option.rect.intersects(event.rect()):
                continue
            painter.drawControl(QStyle.ControlElement.CE_TabBarTab, option)

    def tabRemoved(self, index: int) -> None:  # noqa: N802  (Qt's own name)
        super().tabRemoved(index)
        if self.currentIndex() < 0 and self.count() > 0:
            self.setCurrentIndex(0)


class SidebarPins(QWidget):
    """The pinned tabs, as checkable buttons above the rail.

    Each button shows its tab (`setCurrentIndex`), and is checked while that
    tab is current -- by hand on `currentChanged`, not by a button group: with
    a server tab current NEITHER is checked, which an exclusive group cannot
    say.

    The strip they sit in is made by pushing the bar down
    (`QTabWidget::tab-bar { top: ... }`), and the push is THIS widget's own
    size hint, set on the tab widget's sheet and re-read whenever a restyle
    changes the pins' font or style. Spelled in the theme it would be a guess
    at the platform font's height, and a guess too small puts the first server
    tab under the Logs pin. The widget follows the bar whenever the bar moves or
    resizes.
    """

    def __init__(self, tabs: QTabWidget, pinned: Sequence[tuple[int, QIcon, str]]) -> None:
        super().__init__(tabs)
        self.setObjectName("sidebar-pins")
        self._tabs = tabs
        self._bar = tabs.tabBar()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, SIDEBAR_PIN_GAP, 0, SIDEBAR_PIN_GAP)
        layout.setSpacing(SIDEBAR_PIN_GAP)
        self.buttons: dict[int, QToolButton] = {}
        for index, icon, text in pinned:
            button = QToolButton(self)
            button.setObjectName(SIDEBAR_PIN_BUTTON)
            button.setIcon(icon)
            button.setText(text)
            button.setToolTip(tabs.tabToolTip(index) or text)
            button.setCheckable(True)
            button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
            button.clicked.connect(lambda _checked=False, i=index: self._show(i))
            layout.addWidget(button)
            self.buttons[index] = button
        layout.addStretch(1)
        tabs.currentChanged.connect(self._sync)
        self._bar.installEventFilter(self)
        tabs.installEventFilter(self)  # Ctrl+Tab (`_ctrl_tab`)
        self._reserved = -1
        self._reserve()
        self._sync(tabs.currentIndex())
        self._place()

    def _reserve(self) -> None:
        """Push the bar down by what the pins ask for, when that changes."""
        for button in self.buttons.values():
            button.ensurePolished()
        height = self.sizeHint().height()
        if height == self._reserved:
            return
        self._reserved = height
        self._tabs.setStyleSheet(f"QTabWidget#sidebar-tabs::tab-bar {{ top: {height}px; }}")

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802  (Qt's own name)
        """A restyle at a new width re-polishes the pins: their height may have moved."""
        super().changeEvent(event)
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._reserve()

    def _show(self, index: int) -> None:
        self._tabs.setCurrentIndex(index)
        self._sync(self._tabs.currentIndex())

    @Slot(int)
    def _sync(self, current: int) -> None:
        for index, button in self.buttons.items():
            button.setChecked(index == current)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if watched is self._bar and event.type() in (QEvent.Type.Move, QEvent.Type.Resize):
            self._place()
        if (
            watched is self._tabs
            and event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
        ):
            return self._ctrl_tab(event)
        return False

    def _ctrl_tab(self, event: QKeyEvent) -> bool:
        """Ctrl+Tab and Ctrl+Shift+Tab walk every tab in a ring, the pinned two included.

        Qt's own Ctrl+Tab skips hidden tabs, and Catalog and Logs are hidden
        in the bar, so it never reached them while LB/RB do (`gamepad.py`'s
        `_cycle`). Same ring, same order. A tab widget inside a page (a
        server's own sub-tabs) takes the keys first, as it always did.
        """
        if not event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return False
        if event.key() == Qt.Key.Key_Backtab or (
            event.key() == Qt.Key.Key_Tab and event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            step = -1
        elif event.key() == Qt.Key.Key_Tab:
            step = 1
        else:
            return False
        count = self._tabs.count()
        if count < 2:
            return False
        self._tabs.setCurrentIndex((self._tabs.currentIndex() + step) % count)
        event.accept()
        return True

    def _place(self) -> None:
        bar = self._bar.geometry()
        self.setGeometry(bar.x(), 0, max(bar.width(), RAIL_MIN_WIDTH), max(0, bar.y()))
        self.raise_()
