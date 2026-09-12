"""Warcraft & World of Warcraft styled decorative UI components (PySide6).

Provides authentic Warcraft-themed UI widgets:
- `WarcraftRealmBadge`: Realm status indicator with glowing runic gem & tooltip
- `WarcraftHeader`: Ornate header bar with golden filigree, emblem, and realm status
- `format_warcraft_tooltip`: Helper for classic WoW item/spell style HTML tooltips
"""

from __future__ import annotations

from PySide6.QtCore import Qt
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
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1E824C, stop:1 #145A32)"
            border_color = COLOR_UNCOMMON
            text_color = "#E8F8F5"
            display_text = "● REALM ONLINE"
        elif self._status in ("starting", "importing", "working", "building"):
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #B7950B, stop:1 #7D6608)"
            border_color = COLOR_GOLD_BRIGHT
            text_color = COLOR_GOLD_LIGHT
            display_text = "◈ STARTING / BUSY"
        elif self._status in ("restarting", "loop"):
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B4F72, stop:1 #154360)"
            border_color = COLOR_RARE
            text_color = "#EBF5FB"
            display_text = "◆ RESTARTING"
        else:
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #2C2C34, stop:1 #1A1A20)"
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
    """Ornate Warcraft III / WoW header banner displaying title, filigree and realm status."""

    def __init__(
        self,
        title: str = "Dad's MMO Lab",
        subtitle: str = "Yu'lon — Unified Server Launcher",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            f"WarcraftHeader {{ "
            f"  background: qlineargradient(x1:0, y1:0, x2:1, y2:0, "
            f"  stop:0 #1E1710, stop:0.5 #2C2216, stop:1 #1E1710); "
            f"  border: 1.5px solid {COLOR_GOLD_BRASS}; "
            f"  border-radius: 5px; "
            f"  padding: 6px; "
            f"}}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)

        left_col = QVBoxLayout()
        title_label = QLabel(f"⚔ {title} ⚔", self)
        title_label.setStyleSheet(
            f"font-family: {FONT_FAMILY_TITLE}; font-size: 16px; "
            f"font-weight: bold; color: {COLOR_GOLD_BRIGHT};"
        )
        sub_label = QLabel(subtitle, self)
        sub_label.setStyleSheet(
            f"font-family: {FONT_FAMILY_BODY}; font-size: 11px; color: {COLOR_TEXT_GOLD};"
        )
        left_col.addWidget(title_label)
        left_col.addWidget(sub_label)
        layout.addLayout(left_col, 1)

        self._badge = WarcraftRealmBadge("stopped", self)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_realm_status(self, status: str) -> None:
        """Forward realm status to the embedded badge."""
        self._badge.set_status(status)
