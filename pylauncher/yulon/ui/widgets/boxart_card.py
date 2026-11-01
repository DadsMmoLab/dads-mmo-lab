"""Stage 1 game franchise boxart card for `CatalogView` (Project Kael Phase K2).

Renders a keyboard-, controller-, and mouse-focusable 2:3 vertical poster card
for each supported game franchise (`WoW`, `DDON`, `DFO`) using self-contained
procedural/vector art and active `ThemeSpec` tokens (`style-guide.md` §3, §5, §6).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFocusEvent,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
    QPixmap,
    QRadialGradient,
)
from PySide6.QtWidgets import QPushButton, QSizePolicy, QWidget

from yulon.ui.icons import dadcraft_icon
from yulon.ui.theme import (
    FONT_TITLE_SINGLE,
    get_theme,
    normalize_theme_name,
    resolve_widget_theme,
)

_MOTIF_TINTS: dict[str, tuple[tuple[int, int, int], tuple[int, int, int]]] = {
    "wow": ((90, 195, 255), (35, 115, 195)),
    "ddon": ((85, 230, 145), (30, 145, 85)),
    "dfo": ((255, 145, 55), (185, 65, 25)),
}


def _blend(a: QColor, b: QColor, ratio: float) -> QColor:
    """Linearly interpolate between two `QColor` values (`0.0` = `a`, `1.0` = `b`)."""
    t = max(0.0, min(1.0, ratio))
    inv = 1.0 - t
    return QColor(
        int(a.red() * inv + b.red() * t),
        int(a.green() * inv + b.green() * t),
        int(a.blue() * inv + b.blue() * t),
        int(a.alpha() * inv + b.alpha() * t),
    )


class GameBoxartCard(QPushButton):
    """Focusable 2:3 vertical boxart card for Stage 1 of `CatalogView` (`boxart-card-<id>`)."""

    group_selected = Signal(str)

    def __init__(
        self,
        group_id: str,
        title: str,
        subtitle: str,
        glyph: str,
        *,
        total_servers: int = 1,
        installed_servers: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(title, parent)
        self.group_id: str = group_id
        self.group_title: str = title
        self.group_subtitle: str = subtitle
        self.glyph: str = glyph
        self._total_servers: int = max(0, total_servers)
        self._installed_servers: int = max(0, installed_servers)
        self._hovered: bool = False
        self._theme_name: str | None = None
        self._crest_cache: tuple[str, str, int, QPixmap] | None = None

        self.setObjectName(f"boxart-card-{group_id}")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(180, 240)
        self.setMaximumSize(260, 340)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        self.clicked.connect(self._on_clicked)
        self._sync_tooltip()

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt's own name
        """Return the reference 2:3 vertical poster size (`220×300`)."""
        return QSize(220, 300)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt's own name
        """Return the minimum 2:3 vertical poster size (`180×240`) for 960×640 windows."""
        return QSize(180, 240)

    @property
    def total_servers(self) -> int:
        """Total number of catalog server entries in this game group."""
        return self._total_servers

    @property
    def installed_servers(self) -> int:
        """Number of currently installed servers in this game group."""
        return self._installed_servers

    def set_server_counts(self, total: int, installed: int) -> None:
        """Update the total and installed server counts shown on the boxart footer."""
        self._total_servers = max(0, total)
        self._installed_servers = max(0, installed)
        self._sync_tooltip()
        self.update()

    def apply_theme(self, theme_name: str) -> None:
        """Record the active theme and schedule a repaint."""
        self._theme_name = normalize_theme_name(theme_name)
        self.setProperty("yulon_theme", self._theme_name)
        self.update()

    def _on_clicked(self) -> None:
        self.group_selected.emit(self.group_id)

    def _sync_tooltip(self) -> None:
        summary = self._count_label()
        if self._installed_servers > 0:
            summary = f"{summary} ({self._installed_servers} installed)"
        self.setToolTip(f"Browse {self.group_title} servers — {summary}")

    def _count_label(self) -> str:
        if self.group_subtitle:
            return self.group_subtitle
        word = "Server" if self._total_servers == 1 else "Servers"
        return f"{self._total_servers} {word}"

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt's own name
        super().enterEvent(event)
        self._hovered = True
        self.update()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt's own name
        super().leaveEvent(event)
        self._hovered = False
        self.update()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt's own name
        super().focusInEvent(event)
        self.update()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802 - Qt's own name
        super().focusOutEvent(event)
        self.update()

    def _crest_pixmap(self, color: str, size: int) -> QPixmap:
        cached = self._crest_cache
        if cached is not None and cached[:3] == (self.glyph, color, size):
            return cached[3]
        pixmap = dadcraft_icon(self.glyph, color, size).pixmap(size, size)
        self._crest_cache = (self.glyph, color, size, pixmap)
        return pixmap

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 - Qt's own name
        """Paint the theme-aware vector cover art, crest emblem, and footer metadata."""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        w = float(self.width())
        h = float(self.height())
        active = self._hovered or self.hasFocus()
        spec = get_theme(self._theme_name or resolve_widget_theme(self))

        clip_path = QPainterPath()
        clip_path.addRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), 8.0, 8.0)
        painter.setClipPath(clip_path)

        # 1. Base poster surface gradient from ThemeSpec tokens (both dark and light themes)
        bg = QLinearGradient(0.0, 0.0, 0.0, h)
        top_fill = QColor(spec.bg_hover) if active else QColor(spec.bg_panel)
        bg.setColorAt(0.0, top_fill)
        bg.setColorAt(0.66, QColor(spec.bg_container))
        bg.setColorAt(1.0, QColor(spec.bg_dark))
        painter.fillRect(QRectF(0.0, 0.0, w, h), bg)

        # 2. Atmospheric radial cover glow blended with game motif and active theme accent
        hi_rgb, mid_rgb = _MOTIF_TINTS.get(self.group_id, _MOTIF_TINTS["wow"])
        accent_bright = QColor(spec.accent_bright)
        accent_mid = QColor(spec.accent_border)
        motif_hi = _blend(QColor(*hi_rgb), accent_bright, 0.55)
        motif_mid = _blend(QColor(*mid_rgb), accent_mid, 0.55)

        art_h = min(h * 0.66, h - 86.0)
        cx = w * 0.5
        cy = art_h * 0.52
        aura_radius = min(w, art_h) * 0.62
        aura = QRadialGradient(QPointF(cx, cy), aura_radius)
        hi_alpha = (70 if active else 48) if not spec.is_light else (55 if active else 38)
        mid_alpha = (36 if active else 22) if not spec.is_light else (28 if active else 16)
        aura.setColorAt(0.0, QColor(motif_hi.red(), motif_hi.green(), motif_hi.blue(), hi_alpha))
        aura.setColorAt(
            0.55,
            QColor(motif_mid.red(), motif_mid.green(), motif_mid.blue(), mid_alpha),
        )
        aura.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.fillRect(QRectF(0.0, 0.0, w, art_h), aura)

        # 3. Vector geometric medallion & filigree rings in the cover art region
        ring_color = QColor(spec.accent_bright if active else spec.accent_brass)
        ring_color.setAlpha(135 if active else 80)
        painter.setPen(QPen(ring_color, 1.4))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        outer_r = min(w, art_h) * 0.34
        inner_r = outer_r * 0.76
        painter.drawEllipse(QPointF(cx, cy), outer_r, outer_r)

        sub_ring = QColor(spec.border_dark)
        sub_ring.setAlpha(140)
        painter.setPen(QPen(sub_ring, 1.0, Qt.PenStyle.DotLine))
        painter.drawEllipse(QPointF(cx, cy), inner_r, inner_r)

        # Diamond corner accents around the medallion
        diamond_r = outer_r * 1.12
        diamond_path = QPainterPath()
        diamond_path.moveTo(cx, cy - diamond_r)
        diamond_path.lineTo(cx + diamond_r, cy)
        diamond_path.lineTo(cx, cy + diamond_r)
        diamond_path.lineTo(cx - diamond_r, cy)
        diamond_path.closeSubpath()
        diamond_pen = QColor(spec.accent_brass)
        diamond_pen.setAlpha(70 if active else 38)
        painter.setPen(QPen(diamond_pen, 1.0))
        painter.drawPath(diamond_path)

        # 4. Crisp vector crest icon centered in the medallion
        icon_px = max(36, min(56, int(inner_r * 1.05)))
        crest_hex = spec.accent_bright if active else spec.text_gold
        pixmap = self._crest_pixmap(crest_hex, icon_px)
        painter.drawPixmap(int(cx - icon_px * 0.5), int(cy - icon_px * 0.5), pixmap)

        # 5. Footer panel separator & metadata
        footer_top = art_h
        sep_pen = QColor(spec.accent_brass if active else spec.border_dark)
        sep_pen.setAlpha(180 if active else 120)
        painter.setPen(QPen(sep_pen, 1.0))
        painter.drawLine(QPointF(14.0, footer_top), QPointF(w - 14.0, footer_top))

        # Acronym title ("WoW", "DDON", "DFO")
        title_font = QFont(FONT_TITLE_SINGLE)
        title_font.setPixelSize(max(16, int(h * 0.068)))
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.setPen(QColor(spec.accent_light if active else spec.tile_title))
        title_rect = QRectF(14.0, footer_top + 8.0, w - 28.0, 26.0)
        painter.drawText(
            title_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self.group_title,
        )

        # Server count / edition subtitle
        sub_font = QFont(self.font())
        sub_font.setPixelSize(max(11, int(h * 0.043)))
        sub_font.setBold(False)
        painter.setFont(sub_font)
        painter.setPen(QColor(spec.tile_subtitle))
        sub_rect = QRectF(12.0, footer_top + 34.0, w - 24.0, 20.0)
        painter.drawText(
            sub_rect,
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
            self._count_label(),
        )

        # Installed indicator pill (when 1+ servers in this group are installed)
        if self._installed_servers > 0:
            pill_text = f"● {self._installed_servers} Installed"
            pill_w = min(w - 32.0, 118.0)
            pill_h = 20.0
            pill_rect = QRectF((w - pill_w) * 0.5, footer_top + 56.0, pill_w, pill_h)
            pill_bg = QColor(spec.uncommon)
            pill_bg.setAlpha(38 if not spec.is_light else 32)
            pill_border = QColor(spec.uncommon)
            pill_border.setAlpha(170)
            painter.setPen(QPen(pill_border, 1.0))
            painter.setBrush(pill_bg)
            painter.drawRoundedRect(pill_rect, 10.0, 10.0)

            pill_font = QFont(self.font())
            pill_font.setPixelSize(11)
            pill_font.setBold(True)
            painter.setFont(pill_font)
            painter.setPen(QColor(spec.uncommon))
            painter.drawText(
                pill_rect,
                Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter,
                pill_text,
            )

        # 6. Outer card frame & focus/hover ring
        painter.setClipping(False)
        if self.hasFocus():
            border_pen = QPen(QColor(spec.accent_bright), 2.0)
        elif self._hovered:
            border_pen = QPen(QColor(spec.accent_brass), 1.5)
        else:
            border_pen = QPen(QColor(spec.border_dark), 1.0)
        painter.setPen(border_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(QRectF(1.0, 1.0, w - 2.0, h - 2.0), 8.0, 8.0)
