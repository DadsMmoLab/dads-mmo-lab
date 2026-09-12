"""Warcraft & World of Warcraft styled decorative UI components (PySide6).

Provides authentic Warcraft-themed UI widgets:
- `WarcraftRealmBadge`: Realm status indicator with glowing runic gem & tooltip
- `WarcraftHeader`: Ornate header bar with golden filigree, emblem, and realm status
- `format_warcraft_tooltip`: Helper for classic WoW item/spell style HTML tooltips
"""

from __future__ import annotations

import math
import random

from PySide6.QtCore import QPointF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QHideEvent,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QRadialGradient,
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


def format_warcraft_tooltip(
    title: str,
    body: str | list[str],
    *,
    quality: str = "artifact",
    flavor_text: str | None = None,
    item_level: str | int | None = None,
) -> str:
    """Format an HTML tooltip styled like a World of Warcraft item/spell tooltip.

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


class WarcraftRealmBadge(QWidget):
    """A glowing realm status badge with classic Warcraft gem styling."""

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

    def set_status(self, status: str) -> None:
        """Update the displayed status with appropriate gem lighting and text."""
        self._status = status.lower()
        if self._status in ("running", "online", "ready", "up"):
            bg_color = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1E824C, stop:1 #145A32)"
            )
            border_color = COLOR_UNCOMMON
            text_color = "#E8F8F5"
            display_text = "● REALM ONLINE"
        elif self._status in ("starting", "importing", "working", "building"):
            bg_color = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #B7950B, stop:1 #7D6608)"
            )
            border_color = COLOR_GOLD_BRIGHT
            text_color = COLOR_GOLD_LIGHT
            display_text = "◈ STARTING / BUSY"
        elif self._status in ("restarting", "loop"):
            bg_color = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B4F72, stop:1 #154360)"
            )
            border_color = COLOR_RARE
            text_color = "#EBF5FB"
            display_text = "◆ RESTARTING"
        else:
            bg_color = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2C2C34, stop:1 #1A1A20)"
            )
            border_color = "#555560"
            text_color = "#A0A0AA"
            display_text = "○ REALM OFFLINE"

        self._label.setText(display_text)
        self._label.setStyleSheet(
            f"background: {bg_color}; "
            f"border: 1.5px solid {border_color}; "
            f"color: {text_color}; "
            f"font-family: {FONT_FAMILY_TITLE}; "
            f"font-size: 11px; font-weight: bold; "
            f"padding: 3px 8px; border-radius: 3px;"
        )


class WarcraftHeader(QFrame):
    """Ornate Warcraft III / WoW header banner displaying title, filigree and realm status,
    with an animated warm firepit / hearth background glow and floating ember sparks.
    """

    def __init__(
        self,
        title: str = "Dad's MMO Lab",
        subtitle: str = "Yu'lon — Unified Server Launcher",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFixedHeight(56)
        self.setStyleSheet("WarcraftHeader { background: transparent; border: none; }")
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

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 14, 6)

        left_col = QVBoxLayout()
        title_label = QLabel(f"⚔ {title} ⚔", self)
        title_label.setStyleSheet(
            f"font-family: {FONT_FAMILY_TITLE}; font-size: 17px; "
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

        self._badge = WarcraftRealmBadge("stopped", self)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_realm_status(self, status: str) -> None:
        """Forward realm status to the embedded badge."""
        self._badge.set_status(status)

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

        # 5. Ornate Warcraft 3 / WoW Brass Bevel Frame
        painter.setClipping(False)
        pen_gold = QPen(QColor(COLOR_GOLD_BRASS), 1.5)
        painter.setPen(pen_gold)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        # Top-light edge highlight
        pen_top = QPen(QColor("#FFE8A0"), 1.0)
        painter.setPen(pen_top)
        painter.drawLine(8, 1, w - 8, 1)
