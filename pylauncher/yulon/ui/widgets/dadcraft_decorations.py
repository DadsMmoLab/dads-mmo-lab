"""Dadcraft & World of Dadcraft styled decorative UI components (PySide6).

Provides authentic Dadcraft-themed UI widgets:
- `DadcraftRealmBadge`: Realm status indicator with glowing runic gem & tooltip
- `DadcraftHeader`: Icy animated header with frost details and realm status
- `format_dadcraft_tooltip`: Helper for classic WoW item/spell style HTML tooltips
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

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
    COLOR_GOLD_BRIGHT,
    COLOR_GOLD_LIGHT,
    COLOR_LEGENDARY,
    COLOR_RARE,
    COLOR_TEXT_GOLD,
    COLOR_TEXT_PRIMARY,
    COLOR_UNCOMMON,
    DEFAULT_THEME,
    FONT_FAMILY_BODY,
    FONT_FAMILY_TITLE,
    get_theme,
    normalize_theme_name,
    resolve_widget_theme,
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
        self._theme_name = DEFAULT_THEME
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

    def set_theme(self, theme_name: str | None = None) -> None:
        """Restyle the badge for `theme_name` without emitting a status change."""
        self._theme_name = normalize_theme_name(theme_name)
        self._apply_status_style()

    def set_status(self, status: str) -> None:
        """Update the displayed status with appropriate gem lighting and text."""
        changed = status.lower() != self._status
        self._status = status.lower()
        self._apply_status_style()
        if changed:
            self.status_changed.emit(self._status)

    def _apply_status_style(self) -> None:
        spec = get_theme(self._theme_name)
        tone = realm_tone(self._status)
        if tone == "up":
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1E824C, stop:1 #145A32)"
            border_color = spec.uncommon
            text_color = "#E8F8F5"
            display_text = "● REALM ONLINE"
        elif tone == "between":
            bg_color = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                f"stop:0 {spec.accent_brass}, stop:1 {spec.bg_panel})"
            )
            border_color = spec.accent_bright
            text_color = spec.accent_light if not spec.is_light else spec.text_primary
            display_text = {
                "stopping": "◈ STOPPING",
                "partial": "◐ PARTLY UP",
            }.get(self._status, "◈ STARTING / BUSY")
        elif tone == "restarting":
            bg_color = "qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1B4F72, stop:1 #154360)"
            border_color = spec.rare
            text_color = "#EBF5FB"
            # T391: "loop" is the Server tab's verdict saying the world is
            # crash-looping under containers `docker ps` lists as up.
            display_text = "◆ CRASH LOOP" if self._status == "loop" else "◆ RESTARTING"
        elif tone == "unknown":
            # T188: Docker did not answer, so nothing is known about the realm.
            # The offline sheet's neutral colours, with words that do not claim it.
            bg_color = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                f"stop:0 {spec.bg_panel}, stop:1 {spec.bg_container})"
            )
            border_color = spec.border_dark
            text_color = spec.text_muted
            display_text = "? STATUS UNKNOWN"
        else:
            bg_color = (
                "qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                f"stop:0 {spec.bg_panel}, stop:1 {spec.bg_container})"
            )
            border_color = spec.border_dark
            text_color = spec.text_muted
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


REALM_TITLE_SLOT_PX = 280
"""The width the header keeps for the server's name, whether or not a server page is open.

The header row is laid out around it: the top menu sits where this slot leaves it, so the
menu stays put when a server page shows the name and badge and when the Catalog hides them.
Roomy enough for "WoW WotLK — parent/folder"; a longer name is elided (the whole of it on
hover), never given a wider slot."""

BADGE_STATUSES = (
    "running",
    "starting",
    "stopping",
    "partial",
    "restarting",
    "loop",
    "unknown",
    "stopped",
    "failed",
)
"""Every status word the realm badge shows, to find the widest text it ever has."""

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
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

    def set_full_text(self, text: str) -> None:
        self.full_text = text
        self._relayout()
        self.updateGeometry()

    def _whole_width(self) -> int:
        # Rounded UP: `elidedText` cuts a 221.4px name in a 221px label.
        return math.ceil(QFontMetricsF(self.font()).horizontalAdvance(self.full_text))

    def sizeHint(self) -> QSize:  # noqa: N802  (Qt's own name)
        """The header's fixed name slot, whatever the name is and whether or not it shows.

        The same width with a server page open and without one (the label then sits empty and
        hidden, its place kept), so what shares the row with it does not move. A name longer
        than the slot is shortened to it, with the whole of it on hover.
        """
        return QSize(REALM_TITLE_SLOT_PX, super().sizeHint().height())

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


@dataclass
class _FrostMote:
    """One deterministically seeded mote in the header's frost animation."""

    x: float
    y: float
    speed: float
    size: float
    sway_speed: float
    sway_amp: float
    phase: float
    tint: tuple[int, int, int]
    crystal: bool


class DadcraftHeader(QFrame):
    """Icy animated header with drifting frost, blue flames, and realm status."""

    def __init__(
        self,
        title: str = "Dad's MMO Lab",
        subtitle: str = "Yu'lon — Unified Server Launcher",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFixedHeight(50)
        # Scoped to the frame itself (T193 A20). Selector-less, the same two
        # declarations reached every child -- T188 C1's mechanism -- and took the
        # border and fill off "Check for updates", which then read as a caption.
        self.setObjectName("dadcraft-header")
        self.setStyleSheet("QFrame#dadcraft-header { background: transparent; border: none; }")
        self._time = 0.0
        self._theme_name = DEFAULT_THEME

        rng = random.Random(1337)
        self._rng: random.Random = rng
        self._frost_motes: list[_FrostMote] = []
        for _ in range(36):
            tint = rng.choice(((191, 239, 255), (116, 213, 255), (224, 249, 255), (87, 166, 213)))
            self._frost_motes.append(
                _FrostMote(
                    x=rng.uniform(0.02, 0.98),
                    y=rng.uniform(0.0, 1.0),
                    speed=rng.uniform(0.004, 0.012),
                    size=rng.uniform(1.0, 2.4),
                    sway_speed=rng.uniform(1.8, 3.8),
                    sway_amp=rng.uniform(0.004, 0.015),
                    phase=rng.uniform(0, math.tau),
                    tint=tint,
                    crystal=rng.random() < 0.28,
                )
            )

        self._row = QHBoxLayout(self)
        layout = self._row
        layout.setContentsMargins(14, 4, 14, 4)

        left_col = QVBoxLayout()
        left_col.setSpacing(0)
        self._title_label = QLabel(f"⚔ {title} ⚔", self)
        self._sub_label = QLabel(subtitle, self)
        left_col.addWidget(self._title_label)
        left_col.addWidget(self._sub_label)
        layout.addLayout(left_col, 1)

        # T192's game strip: the whole name of the server the badge is about,
        # right beside it. The rail shows only "WotLK"; this says which one.
        self._realm_title = _RealmTitle(self)
        self._realm_title.setObjectName("realm-title")
        self._realm_title.hide()
        layout.addWidget(self._realm_title, 0, Qt.AlignmentFlag.AlignVCenter)

        self._badge = DadcraftRealmBadge("stopped", self)
        layout.addWidget(self._badge, 0, Qt.AlignmentFlag.AlignVCenter)
        # The name and the badge keep their places when hidden (the Catalog, the Logs tab):
        # hiding them would hand their room to the stretch and move the top menu sideways
        # each time a server page opened or closed.
        for kept in (self._realm_title, self._badge):
            policy = kept.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            kept.setSizePolicy(policy)
        badge_layout = self._badge.layout()
        if badge_layout is not None:
            badge_layout.setAlignment(
                self._badge._label,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        self._followed: DadcraftRealmBadge | None = None
        self.set_theme(DEFAULT_THEME)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(33)

    def set_theme(self, theme_name: str | None = None) -> None:
        """Apply `theme_name` to the header's labels, embedded badge, and backdrop."""
        self._theme_name = normalize_theme_name(theme_name)
        spec = get_theme(self._theme_name)
        title_color = (
            "#FFFFFF"
            if spec.id == "98x-light"
            else (spec.text_primary if spec.is_light else spec.accent_bright)
        )
        sub_color = (
            "#E0F2FE"
            if spec.id == "98x-light"
            else (spec.text_muted if spec.is_light else spec.text_gold)
        )
        realm_color = (
            "#FFFFFF"
            if spec.id == "98x-light"
            else (spec.accent_bright if spec.is_light else spec.accent_light)
        )
        self._title_label.setStyleSheet(
            f"font-family: {FONT_FAMILY_TITLE}; font-size: 20px;"
            f"font-weight: bold; color: {title_color}; background: transparent;"
        )
        self._sub_label.setStyleSheet(
            f"font-family: {FONT_FAMILY_BODY}; font-size: 11px; color: {sub_color}; "
            "background: transparent;"
        )
        self._realm_title.setStyleSheet(
            f"font-family: {FONT_FAMILY_TITLE}; font-size: 13px; font-weight: bold; "
            f"color: {realm_color}; background: transparent;"
        )
        self._badge.set_theme(self._theme_name)
        self._reserve_badge_width()
        self.update()

    def set_realm_status(self, status: str) -> None:
        """Forward realm status to the embedded badge."""
        self._badge.set_status(status)

    def _reserve_badge_width(self) -> None:
        """Keep room for the widest status text, so the badge's width never moves the row.

        A badge that said "STOPPING" and then "STATUS UNKNOWN" would push the top menu sideways
        between two server pages, or between a server page and the Catalog, as the retained
        width of a hidden badge is the one it last had. Its text stays right-aligned in the room.
        """
        probe = DadcraftRealmBadge("stopped")
        probe.set_theme(self._theme_name)
        widest = 0
        for status in BADGE_STATUSES:
            probe.set_status(status)
            widest = max(widest, probe.sizeHint().width())
        probe.deleteLater()
        self._badge.setMinimumWidth(widest)

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
        """Advance the banner's theme-specific animation clock and particles."""
        self._time += 0.033
        downward = self._theme_name in ("floral", "floral-dark")
        for mote in self._frost_motes:
            if downward:
                mote.y += mote.speed * 0.9
                mote.x = (mote.x + mote.speed * 0.25) % 1.0
                if mote.y > 1.0:
                    mote.y = self._rng.uniform(0.0, 0.08)
                    mote.x = self._rng.uniform(0.02, 0.98)
            else:
                mote.y -= mote.speed
                if mote.y < 0:
                    mote.y = self._rng.uniform(0.92, 1.0)
                    mote.x = self._rng.uniform(0.02, 0.98)
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
        spec = get_theme(self._theme_name)

        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, 6, 6)
        painter.setClipPath(path)

        # 1. Theme-aware foundation gradient
        bg_grad = QLinearGradient(0, 0, w, h)
        if spec.id == "98x-light":
            bg_grad.setColorAt(0.0, QColor("#1D4ED8"))
            bg_grad.setColorAt(0.55, QColor("#38BDF8"))
            bg_grad.setColorAt(1.0, QColor("#7DD3FC"))
        elif spec.id == "98x-dark":
            bg_grad.setColorAt(0.0, QColor("#061024"))
            bg_grad.setColorAt(0.55, QColor("#0F294E"))
            bg_grad.setColorAt(1.0, QColor("#153A66"))
        else:
            bg_grad.setColorAt(0.0, QColor(spec.header_bg_start))
            bg_grad.setColorAt(0.5, QColor(spec.header_bg_mid))
            bg_grad.setColorAt(1.0, QColor(spec.header_bg_end))
        painter.fillRect(0, 0, w, h, bg_grad)

        pulse_center = 0.5 + 0.5 * math.sin(t * 3.1) * math.cos(t * 1.7)
        pulse_flame = 0.5 + 0.5 * math.sin(t * 4.8 + 0.8)
        anim = spec.animation.lower()

        if anim == "98x" or spec.id in ("98x-light", "98x-dark"):
            self._paint_98x_hills(painter, w, h, t, dark=(not spec.is_light))
            rime_rgb = (147, 197, 253) if not spec.is_light else (255, 255, 255)
        elif anim == "ocean" or spec.id in ("ocean", "ocean-dark"):
            self._paint_ocean_bubbles(painter, w, h, t, dark=(not spec.is_light))
            rime_rgb = (56, 189, 248) if not spec.is_light else (43, 124, 211)
        elif anim == "floral" or spec.id in ("floral", "floral-dark"):
            self._paint_floral_sakura(painter, w, h, t, dark=(not spec.is_light))
            rime_rgb = (244, 114, 182) if not spec.is_light else (217, 70, 114)
        elif anim == "jungle" or spec.id in ("jungle", "jungle-dark"):
            self._paint_jungle_canopy(painter, w, h, t, dark=(not spec.is_light))
            rime_rgb = (74, 222, 128) if not spec.is_light else (21, 128, 61)
        elif anim == "dracula" or spec.id in ("dracula-dark", "dracula-light"):
            self._paint_dracula_night(painter, w, h, t, light=spec.is_light)
            rime_rgb = (100, 74, 201) if spec.is_light else (189, 147, 249)
        elif anim == "gruvbox" or spec.id in ("gruvbox-dark", "gruvbox-light"):
            self._paint_gruvbox_flames(painter, w, h, t, light=spec.is_light)
            rime_rgb = (175, 58, 3) if spec.is_light else (254, 128, 25)
        else:
            self._paint_arthas_frost(painter, w, h, t, pulse_center, pulse_flame)
            rime_rgb = (118, 213, 255)

        # Corner frame & accent trim
        painter.setClipping(False)
        pen_ice = QPen(QColor(spec.accent_brass), 1.2)
        painter.setPen(pen_ice)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(1, 1, w - 2, h - 2, 6, 6)

        rime_pen = QPen(QColor(*rime_rgb, 145), 1.0)
        painter.setPen(rime_pen)
        for x, direction in ((7, 1), (w - 7, -1)):
            for y, vertical in ((5, 1), (h - 5, -1)):
                crystal = QPainterPath()
                crystal.moveTo(x, y)
                crystal.lineTo(x + direction * 11, y)
                crystal.moveTo(x + direction * 6, y)
                crystal.lineTo(x + direction * 8, y + vertical * 2.5)
                crystal.moveTo(x, y)
                crystal.lineTo(x, y + vertical * 8)
                crystal.moveTo(x, y)
                crystal.lineTo(x + direction * 6, y + vertical * 5)
                painter.drawPath(crystal)

        pen_top = QPen(QColor(spec.header_top_highlight), 1.0)
        painter.setPen(pen_top)
        painter.drawLine(8, 1, w - 8, 1)

    def _paint_arthas_frost(
        self,
        painter: QPainter,
        w: int,
        h: int,
        t: float,
        pulse_center: float,
        pulse_flame: float,
    ) -> None:
        """Arthas: glacial icy-blue flames with frost mist and 4-point ice crystals."""
        glow_core = QColor(72, 196, 255, int(68 + 28 * pulse_center))
        glow_mid = QColor(32, 128, 212, int(44 + 22 * pulse_flame))
        glow_outer = QColor(16, 64, 124, 24)
        side_left = QColor(118, 213, 255, int(42 + 18 * pulse_flame))
        side_right = QColor(96, 192, 248, int(45 + 18 * pulse_center))
        flame_outer_rgb = (36, 148, 235)
        flame_mid_rgb = (118, 213, 255)
        flame_core_rgb = (218, 248, 255)

        glow1 = QRadialGradient(w * 0.5 + 32 * math.sin(t * 1.2), h * 1.18, w * 0.62)
        glow1.setColorAt(0.0, glow_core)
        glow1.setColorAt(0.35, glow_mid)
        glow1.setColorAt(0.72, glow_outer)
        glow1.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow1)

        glow2 = QRadialGradient(w * 0.2, h * 1.15, w * 0.36)
        glow2.setColorAt(0.0, side_left)
        glow2.setColorAt(0.55, glow_outer)
        glow2.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow2)

        glow3 = QRadialGradient(w * 0.8, h * 1.15, w * 0.36)
        glow3.setColorAt(0.0, side_right)
        glow3.setColorAt(0.55, glow_outer)
        glow3.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow3)

        flame_bed = QLinearGradient(0, h * 0.58, 0, h)
        flame_bed.setColorAt(0.0, QColor(*flame_outer_rgb, 0))
        flame_bed.setColorAt(0.65, QColor(*flame_outer_rgb, int(22 + 12 * pulse_flame)))
        flame_bed.setColorAt(1.0, QColor(*flame_mid_rgb, int(44 + 18 * pulse_center)))
        painter.fillRect(0, int(h * 0.58), w, int(h * 0.42) + 1, flame_bed)

        self._draw_flame_ribbons(
            painter,
            w,
            h,
            t,
            pulse_center,
            pulse_flame,
            flame_outer_rgb,
            flame_mid_rgb,
            flame_core_rgb,
        )

        frost_veil = QLinearGradient(0, 0, 0, h * 0.36)
        frost_veil.setColorAt(0.0, QColor(*flame_core_rgb, 26))
        frost_veil.setColorAt(1.0, QColor(*flame_core_rgb, 0))
        painter.fillRect(0, 0, w, int(h * 0.36), frost_veil)

        for mote in self._frost_motes:
            sway = math.sin(t * mote.sway_speed + mote.phase) * mote.sway_amp
            mx = (mote.x + sway) * w
            my = mote.y * h
            twinkle = 0.72 + 0.28 * math.sin(t * 3.8 + mote.phase)
            alpha = int(220 * min(1.0, (1.0 - mote.y) * 1.8) * mote.y * twinkle)
            if alpha <= 0:
                continue
            red, green, blue = mote.tint
            if mote.crystal:
                arm = mote.size * 2.0
                waist = mote.size * 0.65
                crystal = QPainterPath()
                crystal.moveTo(mx, my - arm)
                crystal.lineTo(mx + waist, my)
                crystal.lineTo(mx, my + arm)
                crystal.lineTo(mx - waist, my)
                crystal.closeSubpath()
                painter.setPen(QPen(QColor(red, green, blue, alpha), 0.9))
                painter.setBrush(QColor(red, green, blue, int(alpha * 0.38)))
                painter.drawPath(crystal)
                painter.drawLine(QPointF(mx - arm * 0.72, my), QPointF(mx + arm * 0.72, my))
            else:
                mote_glow = QRadialGradient(mx, my, mote.size * 1.9)
                mote_glow.setColorAt(0.0, QColor(red, green, blue, alpha))
                mote_glow.setColorAt(0.5, QColor(red, green, blue, int(alpha * 0.58)))
                mote_glow.setColorAt(1.0, QColor(red, green, blue, 0))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(mote_glow)
                painter.drawEllipse(QPointF(mx, my), mote.size * 1.9, mote.size * 1.9)

    def _draw_flame_ribbons(
        self,
        painter: QPainter,
        w: int,
        h: int,
        t: float,
        pulse_center: float,
        pulse_flame: float,
        outer_rgb: tuple[int, int, int],
        mid_rgb: tuple[int, int, int],
        core_rgb: tuple[int, int, int],
    ) -> None:
        painter.setPen(Qt.PenStyle.NoPen)
        flame_count = 18
        for index in range(flame_count):
            phase = t * (2.2 + (index % 5) * 0.28) + index * 1.17
            sway_tip = math.sin(phase) * 9.0 + math.cos(phase * 1.65) * 4.5
            sway_mid = math.cos(phase * 1.25 + 0.7) * 5.5
            frac = (index + 0.5) / flame_count
            center_x = w * (0.03 + frac * 0.94) + math.sin(phase * 0.7) * 5.0
            wave = 0.5 + 0.5 * math.sin(phase * 1.35) * math.cos(phase * 0.85)
            height = h * (0.28 + 0.34 * wave + (0.08 if index % 3 == 0 else 0.0))
            half_width = (w / flame_count) * (0.58 + 0.16 * (index % 3))
            base_y = float(h + 2)
            tip_x = center_x + sway_tip
            tip_y = base_y - height

            outer = QPainterPath()
            outer.moveTo(center_x - half_width, base_y)
            outer.cubicTo(
                center_x - half_width * 0.65 + sway_mid * 0.5,
                base_y - height * 0.38,
                tip_x - half_width * 0.28,
                tip_y + height * 0.26,
                tip_x,
                tip_y,
            )
            outer.cubicTo(
                tip_x + half_width * 0.28,
                tip_y + height * 0.30,
                center_x + half_width * 0.72 + sway_mid * 0.5,
                base_y - height * 0.34,
                center_x + half_width,
                base_y,
            )
            outer.closeSubpath()
            outer_grad = QLinearGradient(center_x, base_y, tip_x, tip_y)
            outer_grad.setColorAt(0.0, QColor(*outer_rgb, int(52 + 22 * wave)))
            outer_grad.setColorAt(0.55, QColor(*mid_rgb, int(38 + 16 * pulse_flame)))
            outer_grad.setColorAt(1.0, QColor(*core_rgb, 0))
            painter.setBrush(outer_grad)
            painter.drawPath(outer)

            core_hw = half_width * 0.46
            core_h = height * 0.68
            core_tip_x = center_x + sway_tip * 0.65
            core_tip_y = base_y - core_h
            core = QPainterPath()
            core.moveTo(center_x - core_hw, base_y)
            core.cubicTo(
                center_x - core_hw * 0.5 + sway_mid * 0.35,
                base_y - core_h * 0.4,
                core_tip_x - core_hw * 0.2,
                core_tip_y + core_h * 0.25,
                core_tip_x,
                core_tip_y,
            )
            core.cubicTo(
                core_tip_x + core_hw * 0.2,
                core_tip_y + core_h * 0.28,
                center_x + core_hw * 0.55 + sway_mid * 0.35,
                base_y - core_h * 0.35,
                center_x + core_hw,
                base_y,
            )
            core.closeSubpath()
            core_grad = QLinearGradient(center_x, base_y, core_tip_x, core_tip_y)
            core_grad.setColorAt(0.0, QColor(*core_rgb, int(58 + 24 * pulse_center)))
            core_grad.setColorAt(0.6, QColor(*mid_rgb, int(30 + 14 * wave)))
            core_grad.setColorAt(1.0, QColor(*core_rgb, 0))
            painter.setBrush(core_grad)
            painter.drawPath(core)

    def _paint_gruvbox_flames(
        self, painter: QPainter, w: int, h: int, t: float, *, light: bool
    ) -> None:
        """Gruvbox: roaring furnace inferno with multi-tier orange/yellow flames & ember sparks."""
        roar_a = 0.5 + 0.5 * math.sin(t * 6.4) * math.cos(t * 2.9)
        roar_b = 0.5 + 0.5 * math.sin(t * 8.2 + 1.1)
        crimson_rgb = (157, 0, 6) if light else (204, 36, 29)
        orange_rgb = (175, 58, 3) if light else (254, 128, 25)
        gold_rgb = (215, 153, 33) if light else (250, 189, 47)
        white_hot_rgb = (251, 241, 199)

        # Intense furnace glow across the entire bottom & center
        glow_main = QRadialGradient(w * 0.5 + 36 * math.sin(t * 2.1), h * 1.12, w * 0.68)
        glow_main.setColorAt(0.0, QColor(*gold_rgb, int(92 + 38 * roar_a)))
        glow_main.setColorAt(0.38, QColor(*orange_rgb, int(68 + 28 * roar_b)))
        glow_main.setColorAt(0.75, QColor(*crimson_rgb, int(36 + 16 * roar_a)))
        glow_main.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow_main)

        for side_x, phase_shift in ((w * 0.22, 0.4), (w * 0.78, 2.2)):
            side_pulse = 0.5 + 0.5 * math.sin(t * 5.8 + phase_shift)
            side_glow = QRadialGradient(side_x, h * 1.1, w * 0.42)
            side_glow.setColorAt(0.0, QColor(*orange_rgb, int(64 + 26 * side_pulse)))
            side_glow.setColorAt(0.55, QColor(*crimson_rgb, int(28 + 14 * side_pulse)))
            side_glow.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, side_glow)

        # Glowing coal bed at the base
        coal_bed = QLinearGradient(0, h * 0.48, 0, h)
        coal_bed.setColorAt(0.0, QColor(*crimson_rgb, 0))
        coal_bed.setColorAt(0.55, QColor(*orange_rgb, int(45 + 22 * roar_b)))
        coal_bed.setColorAt(1.0, QColor(*gold_rgb, int(88 + 32 * roar_a)))
        painter.fillRect(0, int(h * 0.48), w, int(h * 0.52) + 1, coal_bed)

        # Dense, tall roaring flame tongues (28 overlapping multi-harmonic tongues)
        painter.setPen(Qt.PenStyle.NoPen)
        flame_count = 28
        for index in range(flame_count):
            phase = t * (4.8 + (index % 6) * 0.55) + index * 0.93
            sway_tip = math.sin(phase) * 14.0 + math.cos(phase * 1.9) * 7.0
            sway_mid = math.cos(phase * 1.45 + 0.5) * 9.0
            frac = (index + 0.5) / flame_count
            center_x = w * (0.01 + frac * 0.98) + math.sin(phase * 0.9) * 6.0
            surge = 0.5 + 0.5 * math.sin(phase * 1.55) * math.cos(phase * 0.95)
            height = h * (0.42 + 0.46 * surge + (0.10 if index % 4 == 0 else 0.0))
            half_width = (w / flame_count) * (0.78 + 0.24 * (index % 3))
            base_y = float(h + 3)
            tip_x = center_x + sway_tip
            tip_y = base_y - height

            # Outer roaring crimson/orange tongue
            outer = QPainterPath()
            outer.moveTo(center_x - half_width, base_y)
            outer.cubicTo(
                center_x - half_width * 0.72 + sway_mid * 0.55,
                base_y - height * 0.36,
                tip_x - half_width * 0.32,
                tip_y + height * 0.24,
                tip_x,
                tip_y,
            )
            outer.cubicTo(
                tip_x + half_width * 0.32,
                tip_y + height * 0.28,
                center_x + half_width * 0.76 + sway_mid * 0.55,
                base_y - height * 0.32,
                center_x + half_width,
                base_y,
            )
            outer.closeSubpath()
            outer_grad = QLinearGradient(center_x, base_y, tip_x, tip_y)
            outer_grad.setColorAt(0.0, QColor(*crimson_rgb, int(96 + 34 * surge)))
            outer_grad.setColorAt(0.48, QColor(*orange_rgb, int(78 + 28 * roar_b)))
            outer_grad.setColorAt(0.85, QColor(*gold_rgb, int(36 + 18 * surge)))
            outer_grad.setColorAt(1.0, QColor(*gold_rgb, 0))
            painter.setBrush(outer_grad)
            painter.drawPath(outer)

            # Inner white-hot / golden-yellow core tongue
            core_hw = half_width * 0.52
            core_h = height * 0.74
            core_tip_x = center_x + sway_tip * 0.7
            core_tip_y = base_y - core_h
            core = QPainterPath()
            core.moveTo(center_x - core_hw, base_y)
            core.cubicTo(
                center_x - core_hw * 0.52 + sway_mid * 0.4,
                base_y - core_h * 0.4,
                core_tip_x - core_hw * 0.22,
                core_tip_y + core_h * 0.24,
                core_tip_x,
                core_tip_y,
            )
            core.cubicTo(
                core_tip_x + core_hw * 0.22,
                core_tip_y + core_h * 0.26,
                center_x + core_hw * 0.56 + sway_mid * 0.4,
                base_y - core_h * 0.34,
                center_x + core_hw,
                base_y,
            )
            core.closeSubpath()
            core_grad = QLinearGradient(center_x, base_y, core_tip_x, core_tip_y)
            core_grad.setColorAt(0.0, QColor(*white_hot_rgb, int(92 + 36 * roar_a)))
            core_grad.setColorAt(0.55, QColor(*gold_rgb, int(64 + 24 * surge)))
            core_grad.setColorAt(1.0, QColor(*orange_rgb, 0))
            painter.setBrush(core_grad)
            painter.drawPath(core)

        # Fast-rising streaking fire sparks & embers
        for mote in self._frost_motes:
            ry = (mote.y - t * mote.speed * 18.0) % 1.0
            sway = math.sin(t * (mote.sway_speed * 1.9) + mote.phase) * (mote.sway_amp * 1.8)
            mx = (mote.x + sway) * w
            my = ry * h
            alpha = int(245 * min(1.0, (1.0 - ry) * 2.0) * min(1.0, ry * 2.2))
            if alpha <= 0:
                continue
            rgb = white_hot_rgb if mote.crystal else gold_rgb
            mote_glow = QRadialGradient(mx, my, mote.size * 2.2)
            mote_glow.setColorAt(0.0, QColor(*rgb, alpha))
            mote_glow.setColorAt(0.45, QColor(*orange_rgb, int(alpha * 0.72)))
            mote_glow.setColorAt(1.0, QColor(*crimson_rgb, 0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(mote_glow)
            painter.drawEllipse(QPointF(mx, my), mote.size * 1.5, mote.size * 2.4)

    def _paint_98x_hills(self, painter: QPainter, w: int, h: int, t: float, *, dark: bool) -> None:
        """98x: rolling green bliss hills with drifting sky clouds / stars."""
        painter.setPen(Qt.PenStyle.NoPen)
        # Soft drifting clouds / sky glints in the upper sky
        for index in range(6):
            cx = ((index * 0.19 + t * 0.015 * (1 + index % 2)) % 1.15 - 0.08) * w
            cy = h * (0.20 + (index % 3) * 0.11) + math.sin(t + index) * 1.5
            cloud_col = QColor(147, 197, 253, 38) if dark else QColor(255, 255, 255, 95)
            painter.setBrush(cloud_col)
            painter.drawEllipse(QPointF(cx, cy), 22, 6)
            painter.drawEllipse(QPointF(cx + 10, cy - 2), 16, 5)

        # Three layered rolling green hills along the bottom
        hill_layers = (
            (0.62, 6.5, 0.7, "#14532D" if dark else "#15803D", "#064E3B" if dark else "#166534"),
            (0.72, 5.0, 2.1, "#16A34A" if dark else "#22C55E", "#14532D" if dark else "#15803D"),
            (0.81, 3.8, 3.8, "#22C55E" if dark else "#4ADE80", "#15803D" if dark else "#16A34A"),
        )
        steps = 32
        for base_frac, amp, phase_off, top_hex, bot_hex in hill_layers:
            hill = QPainterPath()
            hill.moveTo(0, h + 2)
            for step in range(steps + 1):
                x = (step / steps) * w
                nx = step / steps
                y = (
                    h * base_frac
                    + math.sin(nx * math.tau * 1.35 + t * 0.65 + phase_off) * amp
                    + math.cos(nx * math.tau * 2.4 - t * 0.4 + phase_off) * (amp * 0.45)
                )
                hill.lineTo(x, y)
            hill.lineTo(w, h + 2)
            hill.closeSubpath()
            grad = QLinearGradient(0, h * (base_frac - 0.15), 0, h)
            grad.setColorAt(0.0, QColor(top_hex))
            grad.setColorAt(1.0, QColor(bot_hex))
            painter.setBrush(grad)
            painter.drawPath(hill)

    def _paint_ocean_bubbles(
        self, painter: QPainter, w: int, h: int, t: float, *, dark: bool
    ) -> None:
        """Ocean: undulating aqua wave ribbons and rising translucent water bubbles."""
        painter.setPen(Qt.PenStyle.NoPen)
        wave_colors = (
            (QColor(2, 132, 199, 65), QColor(56, 189, 248, 45))
            if dark
            else (QColor(43, 124, 211, 55), QColor(56, 189, 248, 45))
        )
        for idx, col in enumerate(wave_colors):
            wave = QPainterPath()
            wave.moveTo(0, h + 2)
            for step in range(36):
                x = (step / 35) * w
                y = (
                    h * (0.62 + idx * 0.12)
                    + math.sin((step / 35) * math.tau * 2.0 + t * (1.3 + idx * 0.4)) * 5.0
                )
                wave.lineTo(x, y)
            wave.lineTo(w, h + 2)
            wave.closeSubpath()
            painter.setBrush(col)
            painter.drawPath(wave)

        for mote in self._frost_motes:
            sway = math.sin(t * mote.sway_speed + mote.phase) * (mote.sway_amp * 1.4)
            bx = (mote.x + sway) * w
            by = mote.y * h
            alpha = int(200 * min(1.0, (1.0 - mote.y) * 1.8) * mote.y)
            if alpha <= 0:
                continue
            rad = mote.size * 2.1
            ring_col = QColor(125, 211, 252, alpha) if dark else QColor(24, 82, 168, alpha)
            fill_col = (
                QColor(186, 230, 253, int(alpha * 0.28))
                if dark
                else QColor(255, 255, 255, int(alpha * 0.55))
            )
            painter.setPen(QPen(ring_col, 1.0))
            painter.setBrush(fill_col)
            painter.drawEllipse(QPointF(bx, by), rad, rad)
            # Specular bubble highlight dot
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(alpha * 0.8)))
            painter.drawEllipse(QPointF(bx - rad * 0.32, by - rad * 0.32), rad * 0.25, rad * 0.25)

    def _paint_floral_sakura(
        self, painter: QPainter, w: int, h: int, t: float, *, dark: bool
    ) -> None:
        """Floral: soft rose breeze and drifting cherry-blossom sakura petals."""
        glow = QRadialGradient(w * 0.5 + 30 * math.sin(t * 0.9), h * 0.2, w * 0.55)
        glow_col = QColor(244, 114, 182, 55) if dark else QColor(244, 114, 182, 65)
        glow.setColorAt(0.0, glow_col)
        glow.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(0, 0, w, h, glow)

        for mote in self._frost_motes:
            sway = math.sin(t * mote.sway_speed + mote.phase) * (mote.sway_amp * 2.0)
            px = (mote.x + sway) * w
            py = mote.y * h
            alpha = int(215 * min(1.0, (1.0 - mote.y) * 1.8) * min(1.0, mote.y * 2.4))
            if alpha <= 0:
                continue
            petal_col = (
                QColor(249, 168, 212, alpha) if dark else QColor(219, 39, 119, int(alpha * 0.82))
            )
            edge_col = (
                QColor(251, 207, 232, alpha) if dark else QColor(159, 18, 57, int(alpha * 0.9))
            )
            angle = t * 85.0 + mote.phase * 57.3
            size = mote.size * 2.1
            painter.save()
            painter.translate(px, py)
            painter.rotate(angle)
            petal = QPainterPath()
            petal.moveTo(0, -size)
            petal.cubicTo(size * 0.85, -size * 0.4, size * 0.75, size * 0.65, 0, size)
            petal.cubicTo(-size * 0.75, size * 0.65, -size * 0.85, -size * 0.4, 0, -size)
            petal.closeSubpath()
            painter.setPen(QPen(edge_col, 0.7))
            painter.setBrush(petal_col)
            painter.drawPath(petal)
            painter.restore()

    def _paint_jungle_canopy(
        self, painter: QPainter, w: int, h: int, t: float, *, dark: bool
    ) -> None:
        """Jungle: swaying botanical canopy silhouettes and bioluminescent firefly spores."""
        painter.setPen(Qt.PenStyle.NoPen)
        leaf_col = QColor(16, 185, 129, 48) if dark else QColor(21, 128, 61, 45)
        for index in range(9):
            lx = w * (0.06 + index * 0.11) + math.sin(t * 1.2 + index) * 6.0
            ly = h + 2
            lh = h * (0.42 + (index % 3) * 0.1)
            sway = math.sin(t * 1.6 + index * 0.9) * 8.0
            blade = QPainterPath()
            blade.moveTo(lx - 7, ly)
            blade.quadTo(lx - 3 + sway * 0.5, ly - lh * 0.6, lx + sway, ly - lh)
            blade.quadTo(lx + 5 + sway * 0.5, ly - lh * 0.5, lx + 7, ly)
            blade.closeSubpath()
            painter.setBrush(leaf_col)
            painter.drawPath(blade)

        for mote in self._frost_motes:
            sway = math.sin(t * mote.sway_speed + mote.phase) * mote.sway_amp
            mx = (mote.x + sway) * w
            my = mote.y * h
            pulse = 0.55 + 0.45 * math.sin(t * 4.2 + mote.phase)
            alpha = int(220 * min(1.0, (1.0 - mote.y) * 1.8) * mote.y * pulse)
            if alpha <= 0:
                continue
            core = QColor(163, 230, 53, alpha) if dark else QColor(21, 128, 61, alpha)
            halo = (
                QColor(74, 222, 128, int(alpha * 0.5))
                if dark
                else QColor(34, 197, 94, int(alpha * 0.45))
            )
            spore = QRadialGradient(mx, my, mote.size * 2.0)
            spore.setColorAt(0.0, core)
            spore.setColorAt(0.5, halo)
            spore.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.setBrush(spore)
            painter.drawEllipse(QPointF(mx, my), mote.size * 2.0, mote.size * 2.0)

    def _paint_dracula_night(
        self, painter: QPainter, w: int, h: int, t: float, *, light: bool
    ) -> None:
        """Dracula: spooky Gothic castle silhouette, blood/harvest moon, mist, and flying bats."""
        pulse = 0.5 + 0.5 * math.sin(t * 2.2)
        glow_rgb = (100, 74, 201) if light else (189, 147, 249)
        moon_rgb = (203, 58, 42) if light else (255, 85, 85)
        mist_rgb = (100, 74, 201) if light else (255, 121, 198)

        # 1. Blood / Gothic Moon & eerie halo in the upper sky
        moon_x = w * 0.62
        moon_y = h * 0.34
        moon_r = h * 0.26
        halo = QRadialGradient(moon_x, moon_y, moon_r * 3.8)
        halo.setColorAt(0.0, QColor(*moon_rgb, int(95 + 30 * pulse)))
        halo.setColorAt(0.38, QColor(*glow_rgb, int(55 + 20 * pulse)))
        halo.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.fillRect(0, 0, w, h, halo)

        moon_fill = QRadialGradient(moon_x - moon_r * 0.2, moon_y - moon_r * 0.2, moon_r * 1.2)
        if light:
            moon_fill.setColorAt(0.0, QColor(255, 228, 230, 230))
            moon_fill.setColorAt(0.7, QColor(225, 29, 72, 195))
            moon_fill.setColorAt(1.0, QColor(159, 18, 57, 175))
        else:
            moon_fill.setColorAt(0.0, QColor(255, 241, 242, 235))
            moon_fill.setColorAt(0.65, QColor(255, 121, 198, 210))
            moon_fill.setColorAt(1.0, QColor(189, 147, 249, 185))
        painter.setBrush(moon_fill)
        painter.drawEllipse(QPointF(moon_x, moon_y), moon_r, moon_r)

        # 2. Drifting eerie mist bands across the moon and horizon
        for m_idx in range(3):
            drift = math.sin(t * (0.8 + m_idx * 0.3) + m_idx * 1.9) * 28.0
            mx = w * (0.25 + m_idx * 0.26) + drift
            my = h * (0.48 + (m_idx % 2) * 0.16)
            mist = QRadialGradient(mx, my, w * 0.22)
            mist.setColorAt(0.0, QColor(*mist_rgb, 36 if light else 42))
            mist.setColorAt(1.0, QColor(*mist_rgb, 0))
            painter.setBrush(mist)
            painter.drawEllipse(QPointF(mx, my), w * 0.22, h * 0.22)

        # 3. Spooky Gothic Castle & Craggy Cliff Silhouette along the horizon
        castle_col = QColor(42, 30, 68, 235) if light else QColor(13, 12, 20, 248)
        rim_col = QColor(100, 74, 201, 140) if light else QColor(189, 147, 249, 125)
        cx = w * 0.56
        by = float(h + 2)
        castle = QPainterPath()
        castle.moveTo(cx - 115, by)
        # Left craggy hill slope up to outer bastion
        castle.lineTo(cx - 92, h * 0.78)
        castle.lineTo(cx - 74, h * 0.72)
        # Left turret with crenellations & pointed spire
        castle.lineTo(cx - 74, h * 0.44)
        castle.lineTo(cx - 65, h * 0.22)
        castle.lineTo(cx - 56, h * 0.44)
        castle.lineTo(cx - 56, h * 0.62)
        # Curtain wall battlements
        castle.lineTo(cx - 42, h * 0.62)
        castle.lineTo(cx - 42, h * 0.54)
        castle.lineTo(cx - 34, h * 0.54)
        castle.lineTo(cx - 34, h * 0.62)
        castle.lineTo(cx - 24, h * 0.62)
        # Central tall Dracula keep & needle spire
        castle.lineTo(cx - 24, h * 0.30)
        castle.lineTo(cx - 18, h * 0.30)
        castle.lineTo(cx - 10, h * 0.07)
        castle.lineTo(cx - 2, h * 0.30)
        castle.lineTo(cx + 4, h * 0.30)
        castle.lineTo(cx + 4, h * 0.56)
        # Right bridge & crooked belfry tower
        castle.lineTo(cx + 20, h * 0.56)
        castle.lineTo(cx + 20, h * 0.36)
        castle.lineTo(cx + 29, h * 0.16)
        castle.lineTo(cx + 38, h * 0.36)
        castle.lineTo(cx + 38, h * 0.68)
        # Right cliff descent
        castle.lineTo(cx + 62, h * 0.74)
        castle.lineTo(cx + 95, by)
        castle.closeSubpath()

        painter.setPen(QPen(rim_col, 1.0))
        painter.setBrush(castle_col)
        painter.drawPath(castle)

        # Glowing Gothic arched windows inside the keep and turrets
        win_alpha = int(175 + 65 * math.sin(t * 3.7))
        win_col = QColor(255, 184, 108, win_alpha) if light else QColor(241, 250, 140, win_alpha)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(win_col)
        for wx, wy, ww, wh in (
            (cx - 11, h * 0.38, 3.2, 6.5),
            (cx - 11, h * 0.50, 3.2, 5.5),
            (cx - 65, h * 0.50, 2.6, 5.0),
            (cx + 29, h * 0.44, 2.6, 5.0),
        ):
            painter.drawRoundedRect(int(wx - ww / 2), int(wy), int(ww), int(wh), 1, 1)

        # 4. Flock of flapping bats flying across the sky & moon
        bat_color = QColor(31, 20, 48, 230) if light else QColor(18, 14, 28, 245)
        eye_color = QColor(255, 85, 85, 220)
        bat_count = 9
        for b_idx in range(bat_count):
            speed = 0.045 + (b_idx % 4) * 0.014
            direction = -1.0 if b_idx % 2 == 1 else 1.0
            raw_x = (b_idx * 0.13 + direction * t * speed) % 1.16 - 0.08
            bx = raw_x * w
            by_bat = (
                h * (0.18 + (b_idx % 4) * 0.15)
                + math.sin(t * (2.6 + b_idx * 0.3) + b_idx * 1.4) * 5.5
            )
            span = 6.5 + (b_idx % 3) * 2.2
            flap = math.sin(t * (13.5 + (b_idx % 3) * 2.2) + b_idx * 1.1)
            wing_tip_y = by_bat - flap * span * 0.58
            wing_arch_y = by_bat - span * 0.38 - flap * span * 0.25

            bat = QPainterPath()
            # Head / ears center
            bat.moveTo(bx, by_bat - 1.5)
            # Left wing top edge to tip
            bat.quadTo(bx - span * 0.48, wing_arch_y, bx - span, wing_tip_y)
            # Left scalloped bat-wing membrane (two arches back to body)
            bat.quadTo(
                bx - span * 0.65,
                by_bat + span * 0.12,
                bx - span * 0.42,
                by_bat + span * 0.24,
            )
            bat.quadTo(bx - span * 0.20, by_bat + span * 0.06, bx, by_bat + 2.0)
            # Right scalloped bat-wing membrane
            bat.quadTo(
                bx + span * 0.20,
                by_bat + span * 0.06,
                bx + span * 0.42,
                by_bat + span * 0.24,
            )
            bat.quadTo(bx + span * 0.65, by_bat + span * 0.12, bx + span, wing_tip_y)
            # Right wing top edge back to head
            bat.quadTo(bx + span * 0.48, wing_arch_y, bx, by_bat - 1.5)
            bat.closeSubpath()

            painter.setPen(QPen(QColor(*glow_rgb, 90), 0.6))
            painter.setBrush(bat_color)
            painter.drawPath(bat)
            # Tiny crimson glint on the largest bats
            if b_idx % 3 == 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(eye_color)
                painter.drawEllipse(QPointF(bx, by_bat - 0.5), 0.9, 0.9)


THEME_OF = {"wow-unbound": "wow-wotlk", "wow-coa": "wow-wotlk"}
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
        # lost its ice-blue fill and kept its near-black text on the dark card.
        self.setStyleSheet(
            f"QFrame#catalog-tile-{game_id} {{ background: transparent; border: none; }}"
        )
        self._time = 0.0
        self._hovered = False
        self._hover_progress = 0.0

        # Unique particle pool seeded deterministically per campaign
        rng = random.Random((hash(game_id) & 0xFFFFFFFF) ^ 0xA5A5A5A5)
        self._rng: random.Random = rng
        self._particles: list[dict[str, float | str]] = []
        for _ in range(28):
            self._particles.append(
                {
                    "x": rng.uniform(0.02, 0.98),
                    "y": rng.uniform(0.0, 1.0),
                    "speed": rng.uniform(0.005, 0.016),
                    "size": rng.uniform(1.2, 2.6),
                    "sway_speed": rng.uniform(1.2, 3.2),
                    "sway_amp": rng.uniform(0.006, 0.02),
                    "phase": rng.uniform(0, math.tau),
                    "tier": rng.choice(["bright", "mid", "soft"]),
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
                    p["y"] = self._rng.uniform(0.0, 0.08)
                    p["x"] = self._rng.uniform(0.0, 0.98)
                if float(p["x"]) > 1.0:
                    p["x"] = 0.0
            elif self._theme == "wow-vanilla":
                # Cinders & ashes fall slowly downward, drifting with the heat
                p["y"] = float(p["y"]) + float(p["speed"]) * 0.45
                p["x"] = float(p["x"]) + float(p["speed"]) * 0.12
                if float(p["y"]) > 1.0:
                    p["y"] = self._rng.uniform(0.0, 0.06)
                    p["x"] = self._rng.uniform(0.0, 0.98)
                if float(p["x"]) > 1.0:
                    p["x"] = 0.0
            else:
                # Embers / nature wisps float upwards
                p["y"] = float(p["y"]) - float(p["speed"])
                if float(p["y"]) < 0.0:
                    p["y"] = self._rng.uniform(0.92, 1.0)
                    p["x"] = self._rng.uniform(0.02, 0.98)
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        t = self._time
        hp = self._hover_progress
        spec = get_theme(resolve_widget_theme(self))

        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, 7, 7)
        painter.setClipPath(path)

        # 1. Theme-specific background foundations & radial pulses
        if spec.is_light:
            bg = QLinearGradient(0, 0, w, h)
            bg.setColorAt(0.0, QColor(spec.bg_panel))
            bg.setColorAt(0.55, QColor(spec.bg_container))
            bg.setColorAt(1.0, QColor(spec.bg_dark))
            painter.fillRect(0, 0, w, h, bg)

            if self._theme == "wow-wotlk":
                pulse = 0.5 + 0.5 * math.sin(t * 2.2) * math.cos(t * 1.3)
                g1 = QRadialGradient(w * 0.5 + 30 * math.sin(t), h * 0.15, w * 0.7)
                g1.setColorAt(0.0, QColor(56, 189, 248, int(42 + 24 * pulse + 30 * hp)))
                g1.setColorAt(0.5, QColor(14, 116, 144, int(18 + 12 * pulse)))
                g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            elif self._theme == "wow-tbc":
                pulse = 0.5 + 0.5 * math.sin(t * 3.4) * math.cos(t * 1.9)
                g1 = QRadialGradient(w * 0.5 + 25 * math.sin(t * 1.5), h * 1.15, w * 0.65)
                g1.setColorAt(0.0, QColor(34, 197, 94, int(42 + 24 * pulse + 30 * hp)))
                g1.setColorAt(0.5, QColor(21, 128, 61, int(18 + 12 * pulse)))
                g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            elif self._theme == "wow-vanilla":
                pulse = 0.5 + 0.5 * math.sin(t * 2.8) * math.cos(t * 1.4)
                g1 = QRadialGradient(w * 0.5, h * 1.2, w * 0.6)
                g1.setColorAt(0.0, QColor(234, 88, 12, int(40 + 22 * pulse + 28 * hp)))
                g1.setColorAt(0.5, QColor(180, 83, 9, int(18 + 10 * pulse)))
                g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            else:
                pulse = 0.5 + 0.5 * math.sin(t * 2.0)
                g1 = QRadialGradient(w * 0.5 + 20 * math.sin(t * 0.8), h * 0.55, w * 0.65)
                g1.setColorAt(0.0, QColor(16, 185, 129, int(38 + 20 * pulse + 28 * hp)))
                g1.setColorAt(0.5, QColor(5, 150, 105, int(16 + 10 * pulse)))
                g1.setColorAt(1.0, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, g1)
        elif self._theme == "wow-wotlk":
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

            if spec.is_light:
                accent_q = QColor(spec.accent_bright)
                col = QColor(accent_q.red(), accent_q.green(), accent_q.blue(), int(alpha * 0.55))
            elif self._theme == "wow-wotlk":
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

        if spec.is_light:
            border_base = QColor(spec.accent_bright if hp > 0.2 else spec.border_dark)
            border_hi = QColor(spec.header_top_highlight)
        elif self._theme == "wow-wotlk":
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
