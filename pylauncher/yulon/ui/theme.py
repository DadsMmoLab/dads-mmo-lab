"""World of Warcraft & Warcraft III inspired UI Theme for Yu'lon (PySide6).

Provides the visual theme, color palette, custom font hierarchies, and Qt Style
Sheets (QSS) for replicating the classic Warcraft aesthetic:
- Deep obsidian / dark iron stone backdrops with parchment textured panels
- Beveled brass and radiant gold filigree borders
- Spellbook / character sheet style navigation tabs
- Heavy action buttons with dual-tone lighting and glowing gold hover states
- Ornate group boxes, runic input boxes, and classic WoW-styled item tooltips
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QWidget

# --- Warcraft & WoW Color Palette Constants ---
COLOR_BG_DARK = "#0B0D12"
COLOR_BG_CONTAINER = "#12161F"
COLOR_BG_PANEL = "#161A24"
COLOR_BG_PARCHMENT = "#1C1712"
COLOR_BG_PARCHMENT_LIGHT = "#241E17"
COLOR_BG_INPUT = "#0D0F14"

# Gold & Brass Accent Tiers
COLOR_GOLD_BRIGHT = "#FFD100"
COLOR_GOLD_LIGHT = "#FFF1A8"
COLOR_GOLD_BORDER = "#D4AF37"
COLOR_GOLD_BRASS = "#C89B3C"
COLOR_BRASS_DARK = "#785A28"
COLOR_BRASS_DEEP = "#3C2D14"

# Quality & State Accents
COLOR_COMMON = "#FFFFFF"
COLOR_UNCOMMON = "#1EFF00"  # Fel / Green
COLOR_RARE = "#0070FF"  # Arcane / Blue
COLOR_EPIC = "#A335EE"  # Nether / Purple
COLOR_LEGENDARY = "#FF8000"  # Sunwell / Orange
COLOR_ARTIFACT = "#E6CC80"  # Gold
COLOR_DANGER = "#C41E3A"  # Crimson Red
COLOR_TEXT_PRIMARY = "#F0E6D2"  # Parchment White
COLOR_TEXT_MUTED = "#A69C88"  # Aged Text (lightened from #8A8275 for legibility)
COLOR_TEXT_GOLD = "#F0C050"  # Warm Gold
COLOR_TEXT_WARNING = "#FFB86B"  # Amber warning (unsupported platform, refusals)

# Font Hierarchies
FONT_FAMILY_TITLE = (
    "'Cinzel', 'Beaufort for LOL', 'Friz Quadrata', 'FrizQuadrata BT', "
    "'Palatino Linotype', 'Book Antiqua', 'Georgia', serif"
)
FONT_FAMILY_BODY = "'Segoe UI', 'Ubuntu', 'Helvetica Neue', 'Arial', sans-serif"
FONT_FAMILY_MONO = "'Consolas', 'Fira Code', 'JetBrains Mono', 'DejaVu Sans Mono', monospace"

# Qt's QSS cannot scale font sizes relatively (`em`, `%` and the CSS keywords
# all resolve to a fixed value — measured on PySide6 6.11.2). So the theme is
# *generated*: every font size is a base size at the reference width, multiplied
# by a scale the window recomputes on resize. The reference width is the default
# window (`DEFAULT_WINDOW_SIZE` in main.py), so the default view renders exactly
# the base sizes and a narrower/wider window scales every label, button, tab and
# input proportionally.
REFERENCE_WIDTH = 1280
"""The width (px) the base font sizes are authored against."""

# The QTabBar widget honours `font-family`/`font-weight`, but the `::tab`
# sub-control does not (measured: `QTabBar::tab { font-family: ... }` leaves the
# tab text at the default sans-serif). The title font is therefore set on the
# `QTabBar` widget itself, and the `::tab` rule only carries colour and layout.
FONT_TITLE_SINGLE = "Georgia"
"""A single family name for QSS: a comma-separated fallback list is accepted by
the `QWidget`/`QMainWindow` rules but breaks on the `QTabBar` rule, so tabs fell
back to a thin default font. Georgia is the last-resort serif in
`FONT_FAMILY_TITLE` and is present on every supported platform."""


def _px(base: int, scale: float) -> str:
    """A font size at `scale`, clamped so text never becomes unreadable."""
    return f"{max(8, round(base * scale))}px"


def _build_qss(scale: float) -> str:
    return f"""
/* ==========================================================================
   Yu'lon Warcraft & World of Warcraft Desktop Theme
   ========================================================================== */

/* --- Global Base Window & Central Widget --- */
QMainWindow, QDialog, QWidget#centralWidget {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT_PRIMARY};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
}}

QWidget {{
    color: {COLOR_TEXT_PRIMARY};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
}}

/* --- Tab Widget & Spellbook Style Tab Bar --- */
QTabWidget::pane {{
    border: 2px solid {COLOR_BRASS_DARK};
    background-color: {COLOR_BG_CONTAINER};
    border-radius: 4px;
    top: -2px;
    /* Breathing room around the page content, so panels and lists do not sit
       flush against the pane border on any tab, at any window size. */
    padding: 10px;
}}

/* The tab text font is set on the QTabBar WIDGET, not on `::tab`: the `::tab`
   sub-control does not honour `font-family`/`font-weight` (measured), so the
   title font belongs here where it reaches the text actually painted. */
QTabBar {{
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(13, scale)};
    font-weight: bold;
}}

/* Top (North) Tab Bar — used in ControllerView and general tabs.
   Full 4-sided bevel on every tab, with the right-hand border fully visible.
   Square top corners keep the strip continuous when tabs expand in document
   mode (rounded corners leave a 1px seam between expanding tabs). */
QTabBar::tab, QTabBar::tab:top, QTabBar::tab:north, QTabWidget:not(#sidebar-tabs) QTabBar::tab {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #25201A, stop:0.5 #1A1612, stop:1 #100D0A
    );
    color: {COLOR_TEXT_MUTED};
    border: 2px solid {COLOR_BRASS_DARK};
    border-top: 2px solid {COLOR_GOLD_BORDER};
    border-left: 2px solid {COLOR_GOLD_BORDER};
    border-right: 2px solid {COLOR_BRASS_DARK};
    border-bottom: 2px solid {COLOR_BRASS_DARK};
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
    border-bottom-left-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 7px 16px;
    margin-right: 0px;
}}

QTabBar::tab:hover,
QTabBar::tab:top:hover,
QTabBar::tab:north:hover,
QTabWidget:not(#sidebar-tabs) QTabBar::tab:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #3A3026, stop:0.5 #282018, stop:1 #1A1410
    );
    color: {COLOR_GOLD_LIGHT};
    border: 2px solid {COLOR_GOLD_BRIGHT};
    border-top: 2px solid #FFF8D0;
    border-left: 2px solid #FFF8D0;
    border-right: 2px solid {COLOR_GOLD_BRASS};
    border-bottom: 2px solid {COLOR_GOLD_BRASS};
}}

QTabBar::tab:selected,
QTabBar::tab:top:selected,
QTabBar::tab:north:selected,
QTabWidget:not(#sidebar-tabs) QTabBar::tab:selected {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #42321D, stop:0.4 #2A1F13, stop:1 #17100A
    );
    color: {COLOR_GOLD_BRIGHT};
    border: 2px solid {COLOR_GOLD_BRASS};
    border-top: 3px solid {COLOR_GOLD_BRIGHT};
    border-left: 2px solid {COLOR_GOLD_BRASS};
    border-right: 2px solid {COLOR_GOLD_BRASS};
    border-bottom: 2px solid {COLOR_BG_CONTAINER};
    padding-bottom: 8px;
    margin-bottom: -2px;
}}

/* West (Left Sidebar) Tabs — distinct navigation rail on the left side of the
   window. Missing its right-hand border so it seamlessly connects and merges
   into the central container pane on its right. Targets both the :west selector
   and the #sidebar-tabs objectName for reliable matching. */
QTabBar::tab:west,
QTabBar::tab:left,
QTabWidget#sidebar-tabs QTabBar::tab {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #25201A, stop:0.5 #1A1612, stop:1 #100D0A
    );
    color: {COLOR_TEXT_MUTED};
    border-top-left-radius: 5px;
    border-bottom-left-radius: 5px;
    border-top-right-radius: 0px;
    border-bottom-right-radius: 0px;
    padding: 6px 8px;
    margin-bottom: 4px;
    margin-right: 0px;
    border: 2px solid {COLOR_BRASS_DARK};
    border-top: 2px solid {COLOR_GOLD_BORDER};
    border-left: 2px solid {COLOR_GOLD_BORDER};
    border-bottom: 2px solid {COLOR_BRASS_DARK};
    border-right: none;
    min-height: 20px;
    min-width: 48px;
    max-width: 60px;
    text-align: left;
}}

QTabBar::tab:west:hover,
QTabBar::tab:left:hover,
QTabWidget#sidebar-tabs QTabBar::tab:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #3A3026, stop:0.5 #282018, stop:1 #1A1410
    );
    color: {COLOR_GOLD_LIGHT};
    border: 2px solid {COLOR_GOLD_BRIGHT};
    border-top: 2px solid #FFF8D0;
    border-left: 2px solid #FFF8D0;
    border-bottom: 2px solid {COLOR_GOLD_BRASS};
    border-right: none;
}}

QTabWidget#sidebar-tabs QTabBar::tab:selected,
QTabBar::tab:west:selected,
QTabBar::tab:left:selected {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #42321D, stop:0.4 #2A1F13, stop:1 #17100A
    );
    color: {COLOR_GOLD_BRIGHT};
    border: 2px solid {COLOR_GOLD_BRASS};
    border-top: 2px solid {COLOR_GOLD_BRIGHT};
    border-left: 3px solid {COLOR_GOLD_BRIGHT};
    border-bottom: 2px solid {COLOR_GOLD_BRASS};
    border-right: none;
    padding-left: 7px;
}}
    border-left: 2px solid #FFF8D0;
    border-bottom: 2px solid {COLOR_GOLD_BRASS};
    border-right: none;
}}

QTabBar::tab:west:selected, QTabBar::tab:left:selected {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #42321D, stop:0.4 #2A1F13, stop:1 #17100A
    );
    color: {COLOR_GOLD_BRIGHT};
    border: 2px solid {COLOR_GOLD_BRASS};
    border-top: 2px solid {COLOR_GOLD_BRIGHT};
    border-left: 3px solid {COLOR_GOLD_BRIGHT};
    border-bottom: 2px solid {COLOR_GOLD_BRASS};
    border-right: none;
    padding-left: 7px;
    margin-right: -2px;
}}

QTabBar::tab:disabled {{
    background-color: #12100E;
    color: #4A443C;
    border-color: #24201A;
}}

/* Scroll buttons for tab bars with more tabs than fit — the sidebar once a
   few servers are added, and ControllerView's top tab strip on a narrow
   window (`setUsesScrollButtons(True)` in main.py / controller_view.py). */
QTabBar QToolButton {{
    background-color: {COLOR_BG_PARCHMENT};
    border: 1.5px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
}}

QTabBar QToolButton:hover {{
    border-color: {COLOR_GOLD_BRIGHT};
    background-color: {COLOR_BG_PARCHMENT_LIGHT};
}}

QTabBar QToolButton:disabled {{
    border-color: #24201A;
}}

/* --- Classic WoW Action Buttons --- */
QPushButton {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #3A2B18, stop:0.45 #261A0C, stop:0.55 #1C1208, stop:1 #100A04
    );
    color: {COLOR_TEXT_GOLD};
    border: 2px solid {COLOR_BRASS_DARK};
    border-top: 2px solid {COLOR_GOLD_BORDER};
    border-left: 2px solid {COLOR_GOLD_BORDER};
    border-right: 2px solid {COLOR_BRASS_DARK};
    border-bottom: 2px solid {COLOR_BRASS_DARK};
    border-radius: 4px;
    padding: 8px 16px;
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(13, scale)};
    font-weight: bold;
    min-height: 22px;
}}

QPushButton:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #543E23, stop:0.45 #382613, stop:0.55 #2B1C0D, stop:1 #191007
    );
    color: {COLOR_GOLD_LIGHT};
    border: 2px solid {COLOR_GOLD_BRIGHT};
    border-top: 2px solid #FFF8D0;
    border-left: 2px solid #FFF8D0;
    border-right: 2px solid {COLOR_GOLD_BRASS};
    border-bottom: 2px solid {COLOR_GOLD_BRASS};
}}

QPushButton:pressed {{
    /* The "sunken" look comes from the darker fill and flipped border
       shading only — NOT from a padding shift. A padding shift here used to
       nudge the button's content 1px down-and-right without a matching
       change on the other two sides, so a pressed button's box did not line
       up with its un-pressed neighbours in the same row (their shared
       border edge stopped being one straight line). Padding stays identical
       to the base QPushButton rule in every state. */
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #120A04, stop:0.5 #1C1208, stop:1 #2E1F10
    );
    color: {COLOR_GOLD_BRASS};
    border: 2px solid {COLOR_BRASS_DARK};
    border-top: 2px solid #1A1208;
    border-left: 2px solid #1A1208;
    border-right: 2px solid {COLOR_GOLD_BORDER};
    border-bottom: 2px solid {COLOR_GOLD_BORDER};
}}

QPushButton:disabled {{
    background-color: #17181C;
    color: #7A7A7A;
    border: 1.5px solid #2C2C32;
}}

/* Special Primary / Prominent Buttons (e.g. Install, Start) */
QPushButton[primary="true"], QPushButton#start-server, QPushButton#install-btn {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #5C4119, stop:0.45 #3D2B11, stop:0.55 #2B1E0C, stop:1 #171006
    );
    color: {COLOR_GOLD_LIGHT};
    border: 2px solid {COLOR_GOLD_BRASS};
    border-top: 2px solid #FFF8D0;
    border-left: 2px solid #FFF8D0;
    border-right: 2px solid {COLOR_GOLD_BRASS};
    border-bottom: 2px solid {COLOR_GOLD_BRASS};
}}

QPushButton[primary="true"]:hover, QPushButton#start-server:hover, QPushButton#install-btn:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #75521E, stop:0.45 #4D3716, stop:0.55 #38280F, stop:1 #1F1508
    );
    color: #FFFFFF;
    border: 2px solid {COLOR_GOLD_BRIGHT};
    border-top: 2px solid #FFFFFF;
    border-left: 2px solid #FFFFFF;
    border-right: 2px solid {COLOR_GOLD_BRIGHT};
    border-bottom: 2px solid {COLOR_GOLD_BRIGHT};
}}

QPushButton[primary="true"]:pressed {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1F1508, stop:0.5 #38280F, stop:1 #4D3716
    );
    color: {COLOR_GOLD_BRASS};
    border: 2px solid {COLOR_GOLD_BRASS};
    border-top: 2px solid #1F1508;
    border-left: 2px solid #1F1508;
    border-right: 2px solid {COLOR_GOLD_BRIGHT};
    border-bottom: 2px solid {COLOR_GOLD_BRIGHT};
}}

QPushButton[danger="true"], QPushButton#stop-server, QPushButton#purge-btn {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #4A1212, stop:0.5 #2E0B0B, stop:1 #1A0606
    );
    color: #FFB8B8;
    border: 2px solid #A82828;
    border-top: 2px solid #E04848;
    border-left: 2px solid #E04848;
    border-right: 2px solid #A82828;
    border-bottom: 2px solid #A82828;
}}

QPushButton[danger="true"]:hover, QPushButton#stop-server:hover, QPushButton#purge-btn:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #661A1A, stop:0.5 #421010, stop:1 #260909
    );
    color: #FFFFFF;
    border: 2px solid #E84141;
    border-top: 2px solid #FF8080;
    border-left: 2px solid #FF8080;
    border-right: 2px solid #E84141;
    border-bottom: 2px solid #E84141;
}}

QPushButton[danger="true"]:pressed {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #1A0606, stop:0.5 #2E0B0B, stop:1 #4A1212
    );
    color: #FF8080;
    border: 2px solid #A82828;
    border-top: 2px solid #1A0606;
    border-left: 2px solid #1A0606;
    border-right: 2px solid #E04848;
    border-bottom: 2px solid #E04848;
}}

/* --- Group Boxes & Frames --- */
QGroupBox {{
    background-color: {COLOR_BG_PARCHMENT};
    border: 1.5px solid {COLOR_BRASS_DARK};
    border-radius: 6px;
    margin-top: 22px;
    padding: 18px 14px 14px 14px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top center;
    padding: 3px 16px;
    background-color: {COLOR_BG_DARK};
    color: {COLOR_GOLD_BRIGHT};
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(13, scale)};
    font-weight: bold;
    border: 1.5px solid {COLOR_BRASS_DARK};
    border-radius: 4px;
}}

QFrame[frameShape="5"], QFrame[frameShape="StyledPanel"] {{
    background-color: {COLOR_BG_PARCHMENT};
    border: 1.5px solid {COLOR_BRASS_DARK};
    border-radius: 5px;
}}

/* --- Catalog Tile Text Hierarchy --- */
QLabel#tile-title {{
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(15, scale)};
    font-weight: bold;
    color: {COLOR_GOLD_BRIGHT};
}}

QLabel#tile-desc {{
    font-size: {_px(13, scale)};
    color: {COLOR_TEXT_PRIMARY};
}}

QLabel#tile-meta {{
    font-size: {_px(12, scale)};
    color: {COLOR_TEXT_MUTED};
}}

QLabel#tile-warning {{
    font-size: {_px(12, scale)};
    font-style: italic;
    color: {COLOR_TEXT_WARNING};
}}

/* The header shown above the ControllerView's sub-tabs: the full name of the
   panel currently open, because the tab strip itself is icon-only. */
QLabel#panel-title {{
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(16, scale)};
    font-weight: bold;
    color: {COLOR_GOLD_BRIGHT};
    padding: 2px 0px;
}}

/* --- Input Fields & Spinners ---
   Single-line controls get an explicit min-height so they never collapse to
   the text's own line-height (no room for the padding, so the caret and
   selection highlight clipped) — the defect "every textbox is properly
   sized (currently not)" describes. Multi-line controls (log/report boxes)
   get a much taller floor so they read as a panel, not a stray line, but
   are left free to grow with their layout's stretch factor. */
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {COLOR_BG_INPUT};
    color: {COLOR_TEXT_PRIMARY};
    border: 1.5px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
    padding: 7px 10px;
    selection-background-color: #523E1E;
    selection-color: {COLOR_GOLD_LIGHT};
}}

QLineEdit, QSpinBox, QComboBox {{
    min-height: 20px;
    min-width: 60px;
}}

QTextEdit, QPlainTextEdit {{
    min-height: 90px;
}}

QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1.5px solid {COLOR_GOLD_BRIGHT};
    background-color: #0E121A;
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid {COLOR_BRASS_DARK};
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #322514, stop:1 #181109
    );
}}

QComboBox QAbstractItemView {{
    background-color: {COLOR_BG_CONTAINER};
    border: 1.5px solid {COLOR_GOLD_BRASS};
    color: {COLOR_TEXT_PRIMARY};
    selection-background-color: #3D2D16;
    selection-color: {COLOR_GOLD_BRIGHT};
    padding: 4px;
}}

/* --- Tables, Lists, and Tree Views --- */
QListWidget, QTableWidget, QTreeWidget, QTreeView, QTableView {{
    background-color: {COLOR_BG_INPUT};
    border: 1.5px solid {COLOR_BRASS_DARK};
    border-radius: 4px;
    gridline-color: #26211A;
    color: {COLOR_TEXT_PRIMARY};
    alternate-background-color: #11141B;
    font-size: {_px(13, scale)};
}}

QListWidget::item, QTableWidget::item, QTreeWidget::item {{
    padding: 7px 10px;
    border-bottom: 1px solid #1E1A14;
}}

QListWidget::item:hover, QTableWidget::item:hover, QTreeWidget::item:hover {{
    background-color: #241D14;
    color: {COLOR_GOLD_LIGHT};
}}

QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #4A3519, stop:1 #241A0C
    );
    color: {COLOR_GOLD_BRIGHT};
    border-left: 3px solid {COLOR_GOLD_BRIGHT};
}}

QHeaderView::section {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #2C2318, stop:1 #17120B
    );
    color: {COLOR_GOLD_BRASS};
    font-family: {FONT_TITLE_SINGLE};
    font-weight: bold;
    padding: 5px;
    border: 1px solid {COLOR_BRASS_DEEP};
    border-bottom: 2px solid {COLOR_BRASS_DARK};
}}

/* --- Scroll Bars (Carved Stone & Brass Trough) --- */
QScrollBar:vertical {{
    background-color: {COLOR_BG_DARK};
    width: 14px;
    margin: 14px 0 14px 0;
    border: 1px solid #1E1914;
}}

QScrollBar::handle:vertical {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #3D2F1D, stop:0.5 #574328, stop:1 #3D2F1D
    );
    min-height: 24px;
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #574328, stop:0.5 #785C36, stop:1 #574328
    );
    border-color: {COLOR_GOLD_BRASS};
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    background: {COLOR_BG_PARCHMENT};
    height: 14px;
    subcontrol-origin: margin;
    border: 1px solid {COLOR_BRASS_DEEP};
}}

QScrollBar:horizontal {{
    background-color: {COLOR_BG_DARK};
    height: 14px;
    margin: 0 14px 0 14px;
    border: 1px solid #1E1914;
}}

QScrollBar::handle:horizontal {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #3D2F1D, stop:0.5 #574328, stop:1 #3D2F1D
    );
    min-width: 24px;
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 2px;
}}

/* --- Splitter --- */
QSplitter::handle {{
    background-color: #1A1612;
    border: 1px solid #2B2218;
}}

QSplitter::handle:horizontal {{
    width: 6px;
}}

QSplitter::handle:vertical {{
    height: 6px;
}}

QSplitter::handle:hover {{
    background-color: {COLOR_GOLD_BRASS};
}}

/* --- Progress Bars (XP & Health/Mana Style) --- */
QProgressBar {{
    border: 1.5px solid {COLOR_BRASS_DARK};
    border-radius: 4px;
    background-color: #0A0D12;
    text-align: center;
    color: {COLOR_TEXT_PRIMARY};
    font-weight: bold;
    font-size: {_px(11, scale)};
}}

QProgressBar::chunk {{
    background: qlineargradient(
        x1:0, y1:0, x2:0, y2:1,
        stop:0 #27AE60, stop:0.5 #1E824C, stop:1 #145A32
    );
    border-radius: 2px;
}}

/* --- Classic World of Warcraft Item Tooltip Style --- */
QToolTip {{
    background-color: rgba(11, 13, 18, 0.96);
    color: {COLOR_TEXT_PRIMARY};
    border: 1.5px solid {COLOR_GOLD_BORDER};
    padding: 8px 12px;
    border-radius: 4px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
}}

/* --- Checkboxes & Radio Buttons --- */
QCheckBox, QRadioButton {{
    spacing: 7px;
    color: {COLOR_TEXT_PRIMARY};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: 16px;
    height: 16px;
    border: 1.5px solid {COLOR_BRASS_DARK};
    background-color: {COLOR_BG_INPUT};
    border-radius: 3px;
}}

QRadioButton::indicator {{
    border-radius: 8px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {COLOR_GOLD_BRIGHT};
}}

QCheckBox::indicator:checked {{
    background-color: {COLOR_GOLD_BRASS};
    border-color: {COLOR_GOLD_LIGHT};
}}

QRadioButton::indicator:checked {{
    background-color: {COLOR_GOLD_BRASS};
    border-color: {COLOR_GOLD_LIGHT};
}}

/* --- Menus & Context Menus --- */
QMenuBar {{
    background-color: {COLOR_BG_DARK};
    border-bottom: 1.5px solid {COLOR_BRASS_DARK};
    color: {COLOR_TEXT_PRIMARY};
}}

QMenuBar::item:selected {{
    background-color: #2C2216;
    color: {COLOR_GOLD_BRIGHT};
}}

QMenu {{
    background-color: {COLOR_BG_CONTAINER};
    border: 1.5px solid {COLOR_GOLD_BRASS};
    border-radius: 4px;
    padding: 4px;
    color: {COLOR_TEXT_PRIMARY};
}}

QMenu::item {{
    padding: 6px 18px 6px 12px;
    border-radius: 2px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
}}

QMenu::item:selected {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #4A3519, stop:1 #241A0C
    );
    color: {COLOR_GOLD_BRIGHT};
}}

QMenu::item:disabled {{
    color: #666666;
}}

QMenu::separator {{
    height: 1px;
    background: {COLOR_BRASS_DARK};
    margin: 4px 6px;
}}

/* --- Labels & Status Badges --- */
QLabel#updateBanner {{
    background: qlineargradient(
        x1:0, y1:0, x2:1, y2:0,
        stop:0 #4A3311, stop:0.5 #6E4D19, stop:1 #4A3311
    );
    color: {COLOR_GOLD_LIGHT};
    border: 1.5px solid {COLOR_GOLD_BRIGHT};
    border-radius: 4px;
    padding: 8px 14px;
    font-weight: bold;
}}
"""


WARCRAFT_THEME_QSS = _build_qss(1.0)
"""The theme at scale 1.0 (the reference width). Tests assert against this;
`apply_warcraft_theme` re-generates it per window size at runtime."""


def build_warcraft_palette() -> QPalette:
    """Build a QPalette matching the dark Warcraft obsidian and gold color scheme."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLOR_BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLOR_BG_INPUT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLOR_BG_CONTAINER))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLOR_BG_DARK))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLOR_BG_PARCHMENT))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXT_GOLD))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(COLOR_GOLD_BRIGHT))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLOR_RARE))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLOR_GOLD_BRASS))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLOR_BG_DARK))
    # Placeholder text (QLineEdit hint like "name begins with…") would
    # otherwise fall back to a near-black default that is invisible on the
    # dark input background. Muted-but-legible, matching the aged-text tone.
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLOR_TEXT_MUTED))
    return palette


def scale_for_width(width: int) -> float:
    """The font scale for a window of `width` px, against `REFERENCE_WIDTH`.

    Linear, so a window at half the reference width gets half-size text, but
    clamped so it never drops below a legible floor or grows absurdly. The
    reference width is the default window size, so the default view renders at
    exactly scale 1.0.
    """
    return max(0.7, min(1.4, width / REFERENCE_WIDTH))


def apply_warcraft_theme(target: QApplication | QWidget, *, width: int | None = None) -> None:
    """Apply the Warcraft theme stylesheet and palette to a QApplication or QWidget.

    `width` re-generates the stylesheet at a font scale derived from the window
    width (`scale_for_width`), so text grows/shrinks with the window. Omit it
    (or pass None) for the reference scale 1.0.
    """
    from PySide6.QtWidgets import QApplication

    qss = _build_qss(scale_for_width(width) if width is not None else 1.0)
    if isinstance(target, QApplication):
        target.setPalette(build_warcraft_palette())
        target.setStyleSheet(qss)
    else:
        target.setStyleSheet(qss)
