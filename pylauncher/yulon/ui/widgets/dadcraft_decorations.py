"""Dadcraft & World of Dadcraft styled decorative UI components (PySide6).

Provides authentic Dadcraft-themed UI widgets:
- `DadcraftRealmBadge`: Realm status indicator with glowing runic gem & tooltip
- `DadcraftHeader`: Ornate header bar with golden filigree, emblem, and realm status
- `format_dadcraft_tooltip`: Helper for classic WoW item/spell style HTML tooltips
"""

from __future__ import annotations

import math
import random

import shiboken6
from PySide6.QtCore import QEvent, QPointF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFontMetricsF,
    QHideEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QRadialGradient,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from yulon.ui.theme import (
    COLOR_BRASS_DARK,
    COLOR_EPIC,
    COLOR_GOLD_BRASS,
    COLOR_GOLD_BRIGHT,
    COLOR_GOLD_LIGHT,
    COLOR_LEGENDARY,
    COLOR_RARE,
    COLOR_TEXT_GOLD,
    COLOR_TEXT_PRIMARY,
    COLOR_UNCOMMON,
    FONT_FAMILY_BODY,
    FONT_FAMILY_TITLE,
)


def format_dadcraft_tooltip(
    title: str,
    body: str | list[str],
    *,
    quality: str = "artifact",
    flavor_text: str | None = None,
    item_level: str | int | None = None,
) -> str:
    """Format an HTML tooltip styled like a World of Dadcraft item/spell tooltip.

    Quality tiers: 'common', 'uncommon', 'rare', 'epic', 'legendary', 'artifact'.
    """
    quality_colors = {
        "common": "#FFFFFF",
        "uncommon": COLOR_UNCOMMON,
        "rare": COLOR_RARE,
        "epic": COLOR_EPIC,
        "legendary": COLOR_LEGENDARY,
        "artifact": COLOR_GOLD_BRIGHT,
    }
    color = quality_colors.get(quality.lower(), COLOR_GOLD_BRIGHT)
    lines: list[str] = [
        f'<div style="font-family: {FONT_FAMILY_BODY}; font-size: 12px; '
        f'color: {COLOR_TEXT_PRIMARY}; min-width: 180px;">',
        f'  <div style="font-family: {FONT_FAMILY_TITLE}; font-size: 14px; '
        f'font-weight: bold; color: {color}; margin-bottom: 3px;">{title}</div>',
    ]
    if item_level is not None:
        lines.append(
            f'  <div style="color: {COLOR_GOLD_LIGHT}; font-size: 11px; '
            f'margin-bottom: 4px;">Server Tier / Build: {item_level}</div>'
        )
    lines.append(
        f'  <hr style="border: 0; border-top: 1px solid {COLOR_BRASS_DARK}; '
        'margin: 4px 0 6px 0;" />'
    )
    if isinstance(body, str):
        lines.append(f'  <div style="color: {COLOR_TEXT_PRIMARY}; line-height: 1.3;">{body}</div>')
    else:
        for b_line in body:
            lines.append(
                f'  <div style="color: {COLOR_TEXT_PRIMARY}; line-height: 1.3;">{b_line}</div>'
            )
    if flavor_text:
        lines.append(
            f'  <div style="color: {COLOR_TEXT_GOLD}; font-style: italic; '
            f'margin-top: 6px; font-size: 11px;">"{flavor_text}"</div>'
        )
    lines.append("</div>")
    return "\n".join(lines)


def realm_tone(status: str) -> str:
    """What a realm status says, in five words: "up", "between", "restarting", "down", "unknown".

    The badge's colour and the sidebar's status dot (T192) both read this, so
    the two cannot drift apart: a status the badge calls in between is one the
    dot draws amber. "unknown" is its own answer, not "down" -- Docker did not
    answer, and nothing is known about the realm (T188).
    """
    status = status.lower()
    if status in ("running", "online", "ready", "up"):
        return "up"
    if status in ("starting", "importing", "working", "building", "stopping", "partial"):
        # T188 C4/C5: "stopping" and "partial" used to fall through to OFFLINE
        # while the realm was still up. In between, like starting.
        return "between"
    if status in ("restarting", "loop"):
        return "restarting"
    if status == "unknown":
        return "unknown"
    return "down"


class DadcraftRealmBadge(QWidget):
    """A glowing realm status badge with classic Dadcraft gem styling."""

    status_changed = Signal(str)
    """The new status, each time `set_status()` changes it (T187).

    The client launcher's realm pill follows the Server tab's badge through
    this, so the two are one reading: whatever sets the tab's badge -- a poll,
    or Start's "starting" before a poll has seen it -- sets the launcher's.
    """

    def __init__(self, status: str = "stopped", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._status = status
        self._label = QLabel(self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            f"font-family: {FONT_FAMILY_TITLE}; font-size: 11px; font-weight: bold; "
            f"padding: 2px 8px; border-radius: 3px;"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        self.set_status(status)

    @property
    def status(self) -> str:
        """The status last set, lower-cased (`running`, `starting`, `stopped`, ...)."""
        return self._status

    def set_status(self, status: str) -> None:
        """Update the displayed status with appropriate gem lighting and text."""
        changed = status.lower() != self._status
        self._status = status.lower()
        tone = realm_tone(self._status)
        if tone == "up":
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1E824C, stop:1 #145A32)"
            border_color = COLOR_UNCOMMON
            text_color = "#E8F8F5"
            display_text = "● REALM ONLINE"
        elif tone == "between":
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #B7950B, stop:1 #7D6608)"
            border_color = COLOR_GOLD_BRIGHT
            text_color = COLOR_GOLD_LIGHT
            display_text = {
                "stopping": "◈ STOPPING",
                "partial": "◐ PARTLY UP",
            }.get(self._status, "◈ STARTING / BUSY")
        elif tone == "restarting":
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B4F72, stop:1 #154360)"
            border_color = COLOR_RARE
            text_color = "#EBF5FB"
            # T391: "loop" is the Server tab's verdict saying the world is
            # crash-looping under containers `docker ps` lists as up.
            display_text = "◆ CRASH LOOP" if self._status == "loop" else "◆ RESTARTING"
        elif tone == "unknown":
            # T188: Docker did not answer, so nothing is known about the realm.
            # The offline sheet's neutral colours, with words that do not claim it.
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2C2C34, stop:1 #1A1A20)"
            border_color = "#555560"
            text_color = "#A0A0AA"
            display_text = "? STATUS UNKNOWN"
        else:
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2C2C34, stop:1 #1A1A20)"
            border_color = "#555560"
            text_color = "#A0A0AA"
            # T608: "failed" is the Server tab's verdict naming a failed database update; the
            # world is not coming up, and the word says why instead of STARTING.
            display_text = "✖ UPDATE FAILED" if self._status == "failed" else "○ REALM OFFLINE"

        self._label.setText(display_text)
        self._label.setStyleSheet(
            f"background: {bg_color}; "
            f"border: 1.5px solid {border_color}; "
            f"color: {text_color}; "
            f"font-family: {FONT_FAMILY_TITLE}; "
            f"font-size: 11px; font-weight: bold; "
            f"padding: 3px 8px; border-radius: 3px;"
        )
        if changed:
            self.status_changed.emit(self._status)


REALM_TITLE_MIN_CHARS = 8
"""How much of the header's server name survives the narrowest window (T192):
enough to see which game it is, the whole of it one hover away."""


class _RealmTitle(QLabel):
    """The header's server name: one line, shortened to the room it is given (T192).

    It asks for the whole name and settles for `REALM_TITLE_MIN_CHARS`, so a
    narrow window shortens the name before it squeezes the update check or the
    badge. `full_text` is the name; `text()` is what is on screen.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.full_text = ""
        self.setWordWrap(False)

    def set_full_text(self, text: str) -> None:
        self.full_text = text
        self._relayout()
        self.updateGeometry()

    def _whole_width(self) -> int:
        # Rounded UP: `elidedText` cuts a 221.4px name in a 221px label.
        return math.ceil(QFontMetricsF(self.font()).horizontalAdvance(self.full_text))

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        """The WHOLE name's width, whatever is on screen, so a cut name asks for its room back."""
        margins = self.contentsMargins()
        return QSize(
            self._whole_width() + margins.left() + margins.right(), super().sizeHint().height()
        )

    def minimumSizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        return QSize(
            min(
                self.sizeHint().width(),
                self.fontMetrics().averageCharWidth() * REALM_TITLE_MIN_CHARS,
            ),
            super().minimumSizeHint().height(),
        )

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802  (Qt's own name)
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802  (Qt's own name)
        super().showEvent(event)
        self._relayout()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802  (Qt's own name)
        """A restyle changes the font, and with it what fits in the same width."""
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange, QEvent.Type.StyleChange):
            self._relayout()

    def _relayout(self) -> None:
        # The whole name until it is on screen: an unshown label's width is a
        # default nobody laid out.
        room = self.contentsRect().width()
        if not self.isVisible() or self._whole_width() <= room:
            super().setText(self.full_text)
            return
        super().setText(
            self.fontMetrics().elidedText(self.full_text, Qt.TextElideMode.ElideRight, room)
        )


class DadcraftHeader(QFrame):
    """Ornate Dadcraft header banner displaying title, filigree and realm status,
    with an animated warm firepit / hearth background glow and floating ember sparks.
    """

    def __init__(
        self,
        title: str = "Dad's MMO Lab",
        subtitle: str = "Yu'lon — Unified Server Launcher",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(56)
        # Scoped to the frame itself (T193 A20). Selector-less, the same two
        # declarations reached every child -- T188 C1's mechanism -- and took the
        # border and fill off "Check for updates", which then read as a caption.
        self.setObjectName("dadcraft-header")
        self.setStyleSheet("QFrame#dadcraft-header { background: transparent; border: none; }")
        self._time = 0.0

        # Pool of floating firepit embers
        random.seed(1337)
        self._embers: list[dict[str, float | str]] = []
        for _ in range(36):
            self._embers.append(
                {
                    "x": random.uniform(0.02, 0.98),
                    "y": random.uniform(0.0, 1.0),
                    "speed": random.uniform(0.006, 0.018),
                    "size": random.uniform(1.2, 2.6),
                    "sway_speed": random.uniform(1.8, 3.8),
                    "sway_amp": random.uniform(0.004, 0.015),
                    "phase": random.uniform(0, math.tau),
                    "tier": random.choice(["spark", "gold", "orange", "ember"]),
                }
            )

        self._row = QHBoxLayout(self)
        layout = self._row
        layout.setContentsMargins(14, 6, 14, 6)

        left_col = QVBoxLayout()
        title_label = QLabel(f"⚔ {title} ⚔", self)
        title_label.setStyleSheet(
            f"font-family: {FONT_FAMILY_TITLE}; font-size: 22px;"
            f"font-weight: bold; color: {COLOR_GOLD_BRIGHT}; background: transparent;"
        )
        sub_label = QLabel(subtitle, self)
        sub_label.setStyleSheet(
            f"font-family: {FONT_FAMILY_BODY}; font-size: 11px; color: {COLOR_TEXT_GOLD}; "
            "background: transparent;"
        )
        left_col.addWidget(title_label)
        left_col.addWidget(sub_label)
        layout.addLayout(left_col, 1)

        # T192's game strip: the whole name of the server the badge is about,
        # right beside it. The rail shows only "WotLK"; this says which one.
        self._realm_title = _RealmTitle(self)
        self._realm_title.setObjectName("realm-title")
        self._realm_title.setStyleSheet(
            f"font-family: {FONT_FAMILY_TITLE}; font-size: 13px; font-weight: bold; "
            f"color: {COLOR_GOLD_LIGHT}; background: transparent;"
        )
        self._realm_title.hide()
        layout.addWidget(self._realm_title, 0, Qt.AlignmentFlag.AlignVCenter)

        self._badge = DadcraftRealmBadge("stopped", self)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self._followed: DadcraftRealmBadge | None = None

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_realm_status(self, status: str) -> None:
        """Forward realm status to the embedded badge."""
        self._badge.set_status(status)

    def follow(
        self,
        badge: DadcraftRealmBadge | None,
        title: str | None = None,
        tooltip: str | None = None,
    ) -> None:
        """Show what `badge` shows from now on, or hide the header's badge (T188 C6).

        The window hands over the Server tab badge of the tab on screen, and
        None on the Catalog and Logs, where there is no one realm to name.
        `title` is that server's whole name ("WoW WotLK — DadsMmoLab", T192),
        shown left of the badge, shortened if the window is narrow, with
        `tooltip` (the full path) on hover; without a badge or a title it is
        hidden.
        The badge left behind is let go of first, unless it is already gone:
        a closed tab's badge is deleted while this still holds it, and Qt drops
        a deleted sender's connections by itself.
        """
        old = self._followed
        if old is not None and shiboken6.isValid(old):
            try:
                old.status_changed.disconnect(self._badge.set_status)
            except (RuntimeError, TypeError):  # pragma: no cover - never connected
                pass
        self._followed = badge
        if badge is not None:
            badge.status_changed.connect(self._badge.set_status)
            self._badge.set_status(badge.status)
        self._badge.setVisible(badge is not None)
        if badge is not None and title is not None:
            self._realm_title.set_full_text(title)
            self._realm_title.setToolTip(tooltip or title)
            self._realm_title.show()
        else:
            self._realm_title.hide()

    def add_action(self, widget: QWidget) -> None:
        """Place a small control left of the realm badge and its name (the update check, T90).

        There is no menu bar in this app, so the header is where a control that
        belongs to the whole window goes. Left of the badge rather than right:
        the badge is the rightmost thing in every screenshot of this app, and
        moving it would move the one element a user looks for by position.
        Left of the server's name too (T192), which belongs to the badge.
        """
        widget.setParent(self)
        self._row.insertWidget(
            self._row.indexOf(self._realm_title), widget, 0, Qt.AlignmentFlag.AlignVCenter
        )

    def event(self, event: QEvent) -> bool:
        """Re-fit the server's name whenever the row is laid out again (T192)."""
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize):
            self._fit_title()
        return super().event(event)

    def _fit_title(self) -> None:
        """Cap the server's name at the room the other header items leave it (T192).

        Asked for its whole width, a long name made the row short of room, and
        a box layout then takes the shortfall from EVERY item that can shrink,
        in equal parts: "Check for updates" (whose minimum is the theme's
        `min-width`) was cut to 94 of its 140px before the name gave up a pixel.
        Capped here, the name is the only thing short of room; the cap moves
        with every layout, so a wider window gives the whole name back.
        """
        row = self._row
        margins = row.contentsMargins()
        room = self.contentsRect().width() - margins.left() - margins.right()
        for index in range(row.count()):
            item = row.itemAt(index)
            if item is None or item.widget() is self._realm_title or item.isEmpty():
                continue
            room -= item.sizeHint().width() + max(0, row.spacing())
        floor = self._realm_title.minimumSizeHint().width()
        self._realm_title.setMaximumWidth(max(floor, room))

    def _tick(self) -> None:
        """Advance the firepit animation clock and rise the ember particles."""
        self._time += 0.033
        for e in self._embers:
            e["y"] = float(e["y"]) - float(e["speed"])
            if float(e["y"]) < 0:
                e["y"] = random.uniform(0.92, 1.0)
                e["x"] = random.uniform(0.02, 0.98)
        self.update()

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        if self._timer.isActive():
            self._timer.stop()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(33)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        t = self._time

        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, 6, 6)
        painter.setClipPath(path)

        # 1. Dark obsidian / charcoal foundation
        bg_grad = QLinearGradient(0, 0, w, h)
        bg_grad.setColorAt(0.0, QColor("#140E0A"))
        bg_grad.setColorAt(0.5, QColor("#1F140C"))
        bg_grad.setColorAt(1.0, QColor("#110B07"))
        painter.fillRect(0, 0, w, h, bg_grad)

        # 2. Pulsing hearth firepit glow (multi-frequency flame harmonics)
        pulse_center = 0.5 + 0.5 * math.sin(t * 3.1) * math.cos(t * 1.7)
        pulse_flame = 0.5 + 0.5 * math.sin(t * 4.8 + 0.8)

        # Main hearth firepit glow centered at bottom
        glow1 = QRadialGradient(w * 0.5 + 40 * math.sin(t * 1.2), h * 1.2, w * 0.6)
        glow1.setColorAt(0.0, QColor(255, 120, 20, int(80 + 35 * pulse_center)))
        glow1.setColorAt(0.3, QColor(200, 50, 10, int(50 + 25 * pulse_flame)))
        glow1.setColorAt(0.7, QColor(110, 25, 5, 25))
        glow1.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow1)

        # Ambient left campfire warmth
        glow2 = QRadialGradient(w * 0.2, h * 1.15, w * 0.35)
        glow2.setColorAt(0.0, QColor(255, 140, 25, int(45 + 20 * pulse_flame)))
        glow2.setColorAt(0.5, QColor(160, 35, 10, 20))
        glow2.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow2)

        # Ambient right campfire warmth
        glow3 = QRadialGradient(w * 0.8, h * 1.15, w * 0.35)
        glow3.setColorAt(0.0, QColor(255, 150, 30, int(50 + 20 * pulse_center)))
        glow3.setColorAt(0.5, QColor(160, 35, 10, 20))
        glow3.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow3)

        # 3. Firepit coals baseline (golden-red base glow)
        coals = QLinearGradient(0, h * 0.7, 0, h)
        coals.setColorAt(0.0, QColor(255, 100, 10, 0))
        coals.setColorAt(1.0, QColor(255, 80, 10, int(35 + 15 * pulse_flame)))
        painter.fillRect(0, int(h * 0.7), w, int(h * 0.3), coals)

        # 4. Floating glowing embers & sparks
        for e in self._embers:
            sway = math.sin(t * float(e["sway_speed"]) + float(e["phase"])) * float(e["sway_amp"])
            ex = (float(e["x"]) + sway) * w
            ey = float(e["y"]) * h
            alpha = int(255 * min(1.0, (1.0 - float(e["y"])) * 1.8) * float(e["y"]))
            if alpha <= 0:
                continue

            tier = e["tier"]
            if tier == "spark":
                col = QColor(255, 250, 220, alpha)
            elif tier == "gold":
                col = QColor(255, 210, 60, alpha)
            elif tier == "orange":
                col = QColor(255, 120, 20, alpha)
            else:
                col = QColor(230, 45, 10, alpha)

            rad = float(e["size"])
            p_grad = QRadialGradient(ex, ey, rad * 1.8)
            p_grad.setColorAt(0.0, col)
            p_grad.setColorAt(0.5, QColor(col.red(), col.green(), col.blue(), int(alpha * 0.6)))
            p_grad.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(p_grad)
            painter.drawEllipse(QPointF(ex, ey), rad * 1.8, rad * 1.8)

        # 5. Ornate Dadcraft 3 / WoW Brass Bevel Frame
        painter.setClipping(False)
        pen_gold = QPen(QColor(COLOR_GOLD_BRASS), 1.5)
        painter.setPen(pen_gold)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Top-light edge highlight
        pen_top = QPen(QColor("#FFE8A0"), 1.0)
        painter.setPen(pen_top)
        painter.drawLine(8, 1, w - 8, 1)


THEME_OF = {"wow-unbound": "wow-wotlk"}
"""A tile that borrows another campaign's backdrop, particles and frame colours (T554).

WoW Unbound is WotLK with more classes, so its card is the Frostmourne glacier too. The
object name and the particle seed stay the tile's own id.
"""


class DadcraftCampaignCard(QFrame):
    """Themed animated campaign card frame with expansion-specific backdrops,
    glowing particle physics, and reactive hover lighting.
    """

    def __init__(self, game_id: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.game_id = game_id
        self._theme = THEME_OF.get(game_id, game_id)
        self.setObjectName(f"catalog-tile-{game_id}")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(370)
        self.setMinimumWidth(235)
        # Focusable so the gamepad navigator can land on the tile as a single
        # node (and its Install / Use-existing buttons are reached one step in),
        # rather than the card being an invisible gap in the D-pad chain. The
        # card itself has no action; Confirm falls through to its children.
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        # Scoped to the card itself (T188 C1). A selector-less sheet here
        # cascaded to every child and beat the app sheet, so the Install button
        # lost its amber fill and kept its near-black text on the dark card.
        self.setStyleSheet(
            f"QFrame#catalog-tile-{game_id} {{ background: transparent; border: none; }}"
        )
        self._time = 0.0
        self._hovered = False
        self._hover_progress = 0.0

        # Unique particle pool seeded deterministically per campaign
        random.seed((hash(game_id) & 0xFFFFFFFF) ^ 0xA5A5A5A5)
        self._particles: list[dict[str, float | str]] = []
        for _ in range(28):
            self._particles.append(
                {
                    "x": random.uniform(0.02, 0.98),
                    "y": random.uniform(0.0, 1.0),
                    "speed": random.uniform(0.005, 0.016),
                    "size": random.uniform(1.2, 2.6),
                    "sway_speed": random.uniform(1.2, 3.2),
                    "sway_amp": random.uniform(0.006, 0.02),
                    "phase": random.uniform(0, math.tau),
                    "tier": random.choice(["bright", "mid", "soft"]),
                }
            )

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def enterEvent(self, event: QEnterEvent) -> None:
        super().enterEvent(event)
        self._hovered = True

    def leaveEvent(self, event: QEvent) -> None:
        super().leaveEvent(event)
        self._hovered = False

    def hideEvent(self, event: QHideEvent) -> None:
        super().hideEvent(event)
        if self._timer.isActive():
            self._timer.stop()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        if not self._timer.isActive():
            self._timer.start(33)

    def _tick(self) -> None:
        """Advance time harmonics, hover easing, and particle trajectories."""
        self._time += 0.033
        target_hover = 1.0 if self._hovered else 0.0
        self._hover_progress += (target_hover - self._hover_progress) * 0.15

        for p in self._particles:
            if self._theme == "wow-wotlk":
                # Snow drifts down & across
                p["y"] = float(p["y"]) + float(p["speed"]) * 0.8
                p["x"] = float(p["x"]) + float(p["speed"]) * 0.3
                if float(p["y"]) > 1.0:
                    p["y"] = random.uniform(0.0, 0.08)
                    p["x"] = random.uniform(0.0, 0.98)
                if float(p["x"]) > 1.0:
                    p["x"] = 0.0
            elif self._theme == "wow-vanilla":
                # Cinders & ashes fall slowly downward, drifting with the heat
                p["y"] = float(p["y"]) + float(p["speed"]) * 0.45
                p["x"] = float(p["x"]) + float(p["speed"]) * 0.12
                if float(p["y"]) > 1.0:
                    p["y"] = random.uniform(0.0, 0.06)
                    p["x"] = random.uniform(0.0, 0.98)
                if float(p["x"]) > 1.0:
                    p["x"] = 0.0
            else:
                # Embers / nature wisps float upwards
                p["y"] = float(p["y"]) - float(p["speed"])
                if float(p["y"]) < 0.0:
                    p["y"] = random.uniform(0.92, 1.0)
                    p["x"] = random.uniform(0.02, 0.98)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        t = self._time
        hp = self._hover_progress

        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, 7, 7)
        painter.setClipPath(path)

        # 1. Theme-specific background foundations & radial pulses
        if self._theme == "wow-wotlk":
            # Icy Frostmourne Glacier
            bg = QLinearGradient(0, 0, w, h)
            bg.setColorAt(0.0, QColor("#101824"))
            bg.setColorAt(0.5, QColor("#0D141E"))
            bg.setColorAt(1.0, QColor("#070B10"))
            painter.fillRect(0, 0, w, h, bg)

            aurora = 0.5 + 0.5 * math.sin(t * 2.2) * math.cos(t * 1.3)
            g1 = QRadialGradient(w * 0.5 + 30 * math.sin(t), h * 0.15, w * 0.7)
            g1.setColorAt(0.0, QColor(100, 200, 255, int(45 + 30 * aurora + 40 * hp)))
            g1.setColorAt(0.4, QColor(40, 120, 200, int(25 + 15 * aurora)))
            g1.setColorAt(0.8, QColor(15, 45, 80, 15))
            g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, g1)

            mist = QLinearGradient(0, h * 0.75, 0, h)
            mist.setColorAt(0.0, QColor(100, 180, 240, 0))
            mist.setColorAt(1.0, QColor(60, 140, 220, int(30 + 20 * hp)))
            painter.fillRect(0, int(h * 0.75), w, int(h * 0.25), mist)

        elif self._theme == "wow-tbc":
            # Fel Fire & Dark Portal Brimstone
            bg = QLinearGradient(0, 0, w, h)
            bg.setColorAt(0.0, QColor("#162214"))
            bg.setColorAt(0.5, QColor("#101A0E"))
            bg.setColorAt(1.0, QColor("#091007"))
            painter.fillRect(0, 0, w, h, bg)

            flame = 0.5 + 0.5 * math.sin(t * 3.4) * math.cos(t * 1.9)
            g1 = QRadialGradient(w * 0.5 + 25 * math.sin(t * 1.5), h * 1.15, w * 0.65)
            g1.setColorAt(0.0, QColor(80, 255, 40, int(65 + 35 * flame + 45 * hp)))
            g1.setColorAt(0.35, QColor(40, 180, 20, int(40 + 20 * flame)))
            g1.setColorAt(0.7, QColor(15, 75, 10, 20))
            g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, g1)

        elif self._theme == "wow-vanilla":
            # Dark Iron Forge & Polished Steel
            bg = QLinearGradient(0, 0, w, h)
            bg.setColorAt(0.0, QColor("#1C1E24"))
            bg.setColorAt(0.5, QColor("#14161C"))
            bg.setColorAt(1.0, QColor("#0D0E12"))
            painter.fillRect(0, 0, w, h, bg)

            forge = 0.5 + 0.5 * math.sin(t * 2.8) * math.cos(t * 1.4)
            g1 = QRadialGradient(w * 0.5, h * 1.2, w * 0.6)
            g1.setColorAt(0.0, QColor(255, 130, 30, int(60 + 30 * forge + 35 * hp)))
            g1.setColorAt(0.4, QColor(180, 60, 15, int(35 + 15 * forge)))
            g1.setColorAt(0.8, QColor(80, 25, 10, 15))
            g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, g1)

            sheen = QLinearGradient(0, 0, 0, h * 0.35)
            sheen.setColorAt(0.0, QColor(220, 235, 255, int(30 + 35 * hp)))
            sheen.setColorAt(1.0, QColor(200, 220, 245, 0))
            painter.fillRect(0, 0, w, int(h * 0.35), sheen)

        else:
            # Turtle WoW Mystic Jade & Forest
            bg = QLinearGradient(0, 0, w, h)
            bg.setColorAt(0.0, QColor("#141F18"))
            bg.setColorAt(0.5, QColor("#0F1813"))
            bg.setColorAt(1.0, QColor("#09100C"))
            painter.fillRect(0, 0, w, h, bg)

            aura = 0.5 + 0.5 * math.sin(t * 2.0)
            g1 = QRadialGradient(w * 0.5 + 20 * math.sin(t * 0.8), h * 0.55, w * 0.65)
            g1.setColorAt(0.0, QColor(70, 230, 140, int(45 + 25 * aura + 40 * hp)))
            g1.setColorAt(0.4, QColor(35, 150, 85, int(25 + 15 * aura)))
            g1.setColorAt(0.8, QColor(15, 60, 35, 15))
            g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, g1)

        # 2. Render particle pool
        for p in self._particles:
            sway = math.sin(t * float(p["sway_speed"]) + float(p["phase"])) * float(p["sway_amp"])
            px = (float(p["x"]) + sway) * w
            py = float(p["y"]) * h

            if self._theme == "wow-wotlk":
                alpha = int(
                    255 * min(1.0, (1.0 - float(p["y"])) * 2.0) * min(1.0, float(p["y"]) * 3.0)
                )
            elif self._theme == "wow-vanilla":
                # Ashes fade in as they fall from above, then settle out at the bottom
                alpha = int(
                    255 * min(1.0, float(p["y"]) * 4.0) * min(1.0, (1.0 - float(p["y"])) * 1.6)
                )
            else:
                alpha = int(255 * min(1.0, (1.0 - float(p["y"])) * 1.8) * float(p["y"]))

            if alpha <= 0:
                continue

            alpha = min(255, int(alpha * (1.0 + 0.4 * hp)))

            if self._theme == "wow-wotlk":
                col = (
                    QColor(210, 245, 255, alpha)
                    if p["tier"] == "bright"
                    else QColor(120, 200, 255, int(alpha * 0.8))
                )
            elif self._theme == "wow-tbc":
                col = (
                    QColor(190, 255, 150, alpha)
                    if p["tier"] == "bright"
                    else QColor(70, 240, 30, int(alpha * 0.85))
                )
            elif self._theme == "wow-vanilla":
                # Falling ashes: pale smoke-white motes, a few faint ember cinders
                col = (
                    QColor(205, 210, 218, alpha)
                    if p["tier"] == "bright"
                    else QColor(138, 143, 152, int(alpha * 0.8))
                )
            else:
                col = (
                    QColor(200, 255, 220, alpha)
                    if p["tier"] == "bright"
                    else QColor(60, 225, 130, int(alpha * 0.85))
                )

            rad = float(p["size"])
            pg = QRadialGradient(px, py, rad * 1.6)
            pg.setColorAt(0.0, col)
            pg.setColorAt(0.6, QColor(col.red(), col.green(), col.blue(), int(alpha * 0.5)))
            pg.setColorAt(1.0, QColor(col.red(), col.green(), col.blue(), 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(pg)
            painter.drawEllipse(QPointF(px, py), rad * 1.6, rad * 1.6)

        # 3. Outer Dadcraft Bevel & Filigree Frame
        painter.setClipping(False)

        if self._theme == "wow-wotlk":
            if hp > 0:
                border_base = QColor(int(43 + 37 * hp), int(82 + 86 * hp), int(120 + 135 * hp))
                border_hi = QColor(int(128 + 96 * hp), int(208 + 36 * hp), 255)
            else:
                border_base = QColor(43, 82, 120)
                border_hi = QColor(128, 208, 255)
        elif self._theme == "wow-tbc":
            if hp > 0:
                border_base = QColor(int(40 + 24 * hp), int(90 + 165 * hp), int(32 + 32 * hp))
                border_hi = QColor(int(96 + 120 * hp), 255, int(48 + 136 * hp))
            else:
                border_base = QColor(40, 90, 32)
                border_hi = QColor(96, 255, 48)
        elif self._theme == "wow-vanilla":
            if hp > 0:
                border_base = QColor(int(74 + 54 * hp), int(86 + 74 * hp), int(102 + 90 * hp))
                border_hi = QColor(255, 255, 255)
            else:
                border_base = QColor(74, 86, 102)
                border_hi = QColor(216, 228, 240)
        else:
            if hp > 0:
                border_base = QColor(int(46 + 18 * hp), int(96 + 128 * hp), int(68 + 68 * hp))
                border_hi = QColor(int(80 + 112 * hp), 255, int(152 + 68 * hp))
            else:
                border_base = QColor(46, 96, 68)
                border_hi = QColor(80, 232, 152)

        pen = QPen(border_base, 2.0)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 7, 7)

        # Top-lit golden/argent highlight
        pen_top = QPen(border_hi, 1.5)
        painter.setPen(pen_top)
        painter.drawLine(8, 1, w - 8, 1)
