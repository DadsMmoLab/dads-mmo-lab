"""Dadcraft Minimal — the slim, elegant Yu'lon UI theme (PySide6).

The brief is a *toned-down* reimagining of the classic Dadcraft motifs: heavy
stone, parchment and beveled gold filigree are abstracted into flat dark-slate
sheets with razor-thin amber trim and micro-borders. Nothing here is ornate; the
theme reads as a modern utility interface that merely remembers where it came
from. The tokens are:

- Warm charcoal sheets (`#14120E` window, `#201C16` panel, `#12100C` input),
  deliberately earth-tinted — not neutral gray — so the chrome harmonises with
  the gold/brass filigree the decorative widgets paint.
- A single muted amber accent (`#FFB000`) with two restrained derivatives
  (`#C98A0A` hairline, `#8A6510` deep) — no lemon-yellow, no neon quality hues.
- 1px borders and 3px radii everywhere. There is no four-sided bevel idiom and
  no multi-stop chrome gradient left in the sheet.
- State-driven pseudo-classes (`:hover`, `:pressed`, `:focus`, `:checked`,
  `:disabled`) carry the feedback, so the interface feels responsive without
  being loud.

Two hard-won constraints are baked into the generator below and must not be
reintroduced (see `pyplan/style-guide.md` §2/§8):

1. **Never use the `not()` pseudo-selector in QSS.** Qt's `QCss::Parser` has no
   negation combinator; a single occurrence makes `Parser::parse()` return false
   and Qt silently keeps only the rules *before* the failure point. The sidebar
   rail is therefore targeted by objectName (`QTabWidget#sidebar-tabs`), never
   by a negation.
2. **QSS cannot scale type relatively.** `em`, `%` and the CSS keywords all
   resolve to a fixed value, so every font size and touch-target floor is
   *generated* per window width by `_build_qss(scale)`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QWidget

# --- Dadcraft Minimal Surface Tokens ---
# Warm charcoal, not neutral gray: the "dark earth" the brief names, so the
# chrome harmonises with the gold/brass filigree the decorative widgets paint
# rather than reading as a separate, cooler interface sitting beside them.
COLOR_BG_DARK = "#14120E"  # window / app background
COLOR_BG_CONTAINER = "#1A1712"  # tab pane, menus, popups
COLOR_BG_PANEL = "#201C16"  # raised panel sheet
COLOR_BG_PARCHMENT = "#1A1712"  # legacy alias kept for imported constants
COLOR_BG_PARCHMENT_LIGHT = "#282218"  # legacy alias: hover sheet
COLOR_BG_INPUT = "#12100C"  # inset input / list surface

# Amber accent tiers — one hue, three weights. `#FFB000` is the requested accent.
COLOR_GOLD_BRIGHT = "#FFB000"  # primary accent: focus rings, active tab, primary btn
COLOR_GOLD_LIGHT = "#FFD980"  # lightened accent for hover text
COLOR_GOLD_BORDER = "#C98A0A"  # accent hairline (focus / primary border)
COLOR_GOLD_BRASS = "#8A6510"  # dim accent (pressed border, section headers)
COLOR_BRASS_DARK = "#3A3222"  # default hairline border — warm bronze, not neutral gray
COLOR_BRASS_DEEP = "#262218"  # recessed / divider line

# Quality & state accents — desaturated from the old neon set so a status dot
# never shouts louder than the content beside it.
COLOR_COMMON = "#E8E2D6"
COLOR_UNCOMMON = "#5FB37A"  # muted fel / online green (was #1EFF00)
COLOR_RARE = "#5A8FBF"  # muted arcane / info blue (was #0070FF)
COLOR_EPIC = "#9B6FC4"  # muted nether purple (was #A335EE)
COLOR_LEGENDARY = "#D98F45"  # muted sunwell orange (was #FF8000)
COLOR_ARTIFACT = "#C9A96A"  # muted titan gold (was #E6CC80)
COLOR_DANGER = "#C4484C"  # muted crimson (was #C41E3A)
COLOR_TEXT_PRIMARY = "#E8E2D6"  # body text on dark sheets
COLOR_TEXT_MUTED = "#A39A88"  # secondary text (kept above WCAG AA on every sheet)
COLOR_TEXT_GOLD = "#F2C55C"  # warm accent text
COLOR_TEXT_WARNING = "#E0A030"  # amber warning (unsupported platform, refusals)

# Font Hierarchies. The title face is deliberately the *same* sans stack as the
# body: the brief asks for modern utility, and the old ornate serif list
# ('Cinzel'/'Friz Quadrata'/'Georgia') names no face that ships on SteamOS, so
# it resolved non-deterministically through fontconfig. Every family below is
# either ubiquitous or has a generic fallback that exists everywhere.
FONT_FAMILY_TITLE = (
    "'Segoe UI', 'Noto Sans', 'DejaVu Sans', 'Liberation Sans', "
    "'Helvetica Neue', 'Arial', sans-serif"
)
FONT_FAMILY_BODY = FONT_FAMILY_TITLE
FONT_FAMILY_MONO = (
    "'JetBrains Mono', 'Fira Code', 'DejaVu Sans Mono', 'Liberation Mono', 'Consolas', monospace"
)

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
FONT_TITLE_SINGLE = "Segoe UI"
"""A single family name for the QTabBar widget rule. A comma list also parses on
this PySide6 build, but a single ubiquitous name keeps tab-label metrics stable
across platforms: missing faces fall through to the platform UI font, which is
exactly the modern look the brief asks for."""


FORGET_TAB_BUTTON = "tab-forget"
"""objectName of the × a server's sidebar tab carries (T95, `main.py`).

One spelling for the widget and its QSS rule. That rule is the one exemption
from the tab-bar tool-button touch floor.
"""


# --- Handheld geometry floors -------------------------------------------------
# The launcher ships on the Steam Deck (1280x800, 7"), driven by controller and
# touch. Every interactive widget gets a floor of at least `TOUCH_TARGET_PX` in
# its short dimension so a D-pad or thumb never has to aim, and every font has a
# hard rendered-pixel floor regardless of how far the window scale collapses.
TOUCH_TARGET_PX = 32
"""Minimum short-side size (px) for any interactive control.

The classic 44px figure is Apple's; on a 1280x800 handheld the whole viewport is
only 800px tall, so 44px on every row would fit eleven rows and nothing else. 32
is the Steam Deck's own on-screen-keyboard key floor and is the value Valve's
Game Mode UI uses for secondary controls — it stays comfortable under a thumb
while leaving the vertical budget the dense tabs need.
"""

MIN_FONT_PX = 12
"""Hard floor (px) for any rendered font size, applied after scaling.

Without it the width scale bottoms out around 10px for body text and 8px for
progress labels at the 960px window minimum — unreadable on a 215 PPI panel.
"""


def _touch(base: int, scale: float) -> str:
    """An interactive control's short-side floor at `scale`.

    Floors are scaled *gently* (a square root of the width scale) rather than
    linearly: a control that shrinks in direct proportion to the window ends up
    too small to hit, which is the failure mode the linear font scale has. The
    square root means a 25% narrower window only tightens targets ~13%.
    """
    eased = scale**0.5
    return f"{max(TOUCH_TARGET_PX, round(base * eased))}px"


def _px(base: int, scale: float) -> str:
    """A font size at `scale`, floored so text never becomes unreadable."""
    return f"{max(MIN_FONT_PX, round(base * scale))}px"


def _build_qss(scale: float) -> str:
    return f"""
/* ==========================================================================
   Yu'lon — "Dadcraft Minimal"
   Flat charcoal sheets, one muted amber accent, 1px hairlines, 3px radii.
   The sheet uses no negation pseudo-selector: Qt's parser has no such
   combinator and would discard every rule after the failure point.
   ========================================================================== */

/* --- Global Base Window & Central Widget --- */
QMainWindow, QDialog, QWidget#centralWidget {{
    background-color: {COLOR_BG_DARK};
    color: {COLOR_TEXT_PRIMARY};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(14, scale)};
}}

QWidget {{
    color: {COLOR_TEXT_PRIMARY};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(14, scale)};
}}

/* --- Tab Widget: flat sheet, hairline frame, slim tabs --- */
QTabWidget::pane {{
    border: 1px solid {COLOR_BRASS_DARK};
    background-color: {COLOR_BG_CONTAINER};
    border-radius: 3px;
    top: -1px;
    /* Restrained breathing room: 8px around page content keeps panels off the
       frame without spending height that the dense tabs need on an 800px screen. */
    padding: 8px;
}}

/* The tab text font must be set on the QTabBar WIDGET, not on `::tab`: the
   `::tab` sub-control does not honour `font-family`/`font-weight` (measured), so
   a rule there leaves tab text at the default font. */
QTabBar {{
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(13, scale)};
    font-weight: normal;
}}

/* Top tab bar (ControllerView's sub-tabs, and the catalog/console strip).
   A flat sheet with a hairline border and a transparent 1px top rail that the
   selected tab replaces with the amber accent — the "subtle bottom-line accent"
   the brief asks for, without a single gradient. */
QTabBar::tab, QTabBar::tab:top {{
    background-color: {COLOR_BG_PANEL};
    color: {COLOR_TEXT_MUTED};
    border: 1px solid {COLOR_BRASS_DARK};
    border-top: 2px solid transparent;
    border-radius: 0px;
    padding: 8px 16px;
    min-height: {_touch(26, scale)};
    min-width: 52px;
    margin-right: -1px;
}}

QTabBar::tab:hover, QTabBar::tab:top:hover {{
    background-color: #242424;
    color: {COLOR_GOLD_LIGHT};
    border: 1px solid {COLOR_GOLD_BRASS};
    border-top: 2px solid {COLOR_GOLD_BRASS};
}}

QTabBar::tab:selected, QTabBar::tab:top:selected {{
    background-color: {COLOR_BG_CONTAINER};
    color: {COLOR_GOLD_BRIGHT};
    border: 1px solid {COLOR_BRASS_DARK};
    border-top: 2px solid {COLOR_GOLD_BRIGHT};
    border-bottom: 1px solid {COLOR_BG_CONTAINER};
}}

/* Keyboard/D-pad focus on a tab is distinct from hover: an amber hairline ring
   on all four sides, so a controller user can see which tab will activate. */
QTabBar::tab:focus, QTabBar::tab:top:focus {{
    border: 1px solid {COLOR_GOLD_BRIGHT};
}}

/* West (left sidebar) tabs — the realm rail.
   Selected by objectName (`sidebar-tabs` in main.py), never by a negation and
   never by the `:west` pseudo-state: Qt does not reliably honour positional
   pseudo-states on `::tab` (measured). The right edge is left unbordered so the
   rail merges into the pane beside it. */
QTabWidget#sidebar-tabs QTabBar::tab {{
    background-color: {COLOR_BG_PANEL};
    color: {COLOR_TEXT_MUTED};
    border: 1px solid transparent;
    border-left: 3px solid transparent;
    border-radius: 0px;
    padding: 8px 6px;
    margin: 2px 0px;
    min-width: 48px;
    max-width: 64px;
    min-height: {_touch(44, scale)};
}}

QTabWidget#sidebar-tabs QTabBar::tab:hover {{
    background-color: #242424;
    color: {COLOR_GOLD_LIGHT};
    border: 1px solid {COLOR_GOLD_BRASS};
    border-left: 3px solid {COLOR_GOLD_BRASS};
}}

QTabWidget#sidebar-tabs QTabBar::tab:selected {{
    background-color: {COLOR_BG_CONTAINER};
    color: {COLOR_GOLD_BRIGHT};
    border: 1px solid {COLOR_BRASS_DARK};
    border-left: 3px solid {COLOR_GOLD_BRIGHT};
}}

QTabWidget#sidebar-tabs QTabBar::tab:focus {{
    border: 1px solid {COLOR_GOLD_BRIGHT};
    border-left: 3px solid {COLOR_GOLD_BRIGHT};
}}

QTabBar::tab:disabled {{
    background-color: {COLOR_BG_PANEL};
    color: #5A5A5A;
    border-color: {COLOR_BRASS_DEEP};
}}

/* Scroll arrows for tab bars with more tabs than fit. They are the only route
   to an off-screen tab, so they carry a real touch floor rather than collapsing
   to the icon's own size. */
QTabBar QToolButton {{
    background-color: {COLOR_BG_PANEL};
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
    min-width: {_touch(30, scale)};
    min-height: {_touch(30, scale)};
}}

QTabBar QToolButton:hover {{
    border-color: {COLOR_GOLD_BRIGHT};
    background-color: #242424;
}}

QTabBar QToolButton:focus {{
    border-color: {COLOR_GOLD_BRIGHT};
}}

QTabBar QToolButton:disabled {{
    border-color: {COLOR_BRASS_DEEP};
    color: #5A5A5A;
}}

/* T95: the × on a server's sidebar tab. Exempt, by objectName, from the scroll
   arrows' touch floor just above. At that floor it was a 37px square on a 64px
   rail and made every server tab 41px longer (measured offscreen 2026-09-23);
   at 18px it costs 22. The tab under it stays the touch target, and the tab's
   right-click entry and the Server tab's button open the same dialog. An ID
   selector outranks the type rule above, whatever the order. */
QTabBar QToolButton#{FORGET_TAB_BUTTON} {{
    background-color: transparent;
    border: none;
    color: {COLOR_TEXT_MUTED};
    padding: 0px;
    min-width: 16px;
    min-height: 16px;
    max-width: 18px;
    max-height: 18px;
}}

QTabBar QToolButton#{FORGET_TAB_BUTTON}:hover {{
    color: {COLOR_DANGER};
}}

/* --- Buttons: flat sheets with a hairline, amber on interaction --- */
QPushButton {{
    background-color: {COLOR_BG_PANEL};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
    padding: 8px 16px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
    font-weight: normal;
    min-height: {_touch(28, scale)};
    min-width: 64px;
}}

QPushButton:hover {{
    background-color: #2A2418;
    color: {COLOR_GOLD_LIGHT};
    border: 1px solid {COLOR_GOLD_BRASS};
}}

QPushButton:pressed {{
    /* The sunken look is fill + border only. Padding must stay identical to the
       base rule in every state: a padding shift here used to nudge the content
       without a matching change on the other sides, so a pressed button's box
       stopped lining up with its neighbours in the same row. */
    background-color: #12100A;
    color: {COLOR_GOLD_BRIGHT};
    border: 1px solid {COLOR_GOLD_BRASS};
}}

/* Controller/D-pad focus ring. Distinct from hover so a keyboard user always
   knows where the D-pad will land. */
QPushButton:focus {{
    border: 1px solid {COLOR_GOLD_BRIGHT};
    color: {COLOR_GOLD_LIGHT};
}}

QPushButton:disabled {{
    background-color: #1C1C1C;
    color: #6A6A6A;
    border: 1px solid {COLOR_BRASS_DEEP};
}}

/* Primary / prominent actions (Install, Start): the one place the amber is
   allowed to be a fill rather than a hairline. */
QPushButton[primary="true"],
QPushButton#start-server,
QPushButton#install-btn,
QPushButton[objectName^="install-"] {{
    background-color: {COLOR_GOLD_BORDER};
    color: #1A1206;
    border: 1px solid {COLOR_GOLD_BORDER};
    border-radius: 3px;
    padding: 8px 16px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(14, scale)};
    font-weight: bold;
    min-height: {_touch(30, scale)};
}}

QPushButton[primary="true"]:hover,
QPushButton#start-server:hover,
QPushButton#install-btn:hover,
QPushButton[objectName^="install-"]:hover {{
    background-color: {COLOR_GOLD_BRIGHT};
    color: #140D03;
    border: 1px solid {COLOR_GOLD_LIGHT};
}}

QPushButton[primary="true"]:pressed,
QPushButton#start-server:pressed,
QPushButton#install-btn:pressed,
QPushButton[objectName^="install-"]:pressed {{
    background-color: {COLOR_GOLD_BORDER};
    color: #140D03;
    border: 1px solid {COLOR_GOLD_BRASS};
}}

QPushButton[primary="true"]:focus,
QPushButton#start-server:focus,
QPushButton#install-btn:focus,
QPushButton[objectName^="install-"]:focus {{
    border: 1px solid {COLOR_GOLD_LIGHT};
}}

QPushButton[primary="true"]:disabled,
QPushButton#start-server:disabled,
QPushButton#install-btn:disabled,
QPushButton[objectName^="install-"]:disabled {{
    background-color: #2A2418;
    color: #7A6A48;
    border: 1px solid {COLOR_BRASS_DEEP};
}}

/* Destructive actions (Stop, Purge, Uninstall). */
QPushButton[danger="true"], QPushButton#stop-server, QPushButton#purge-btn {{
    background-color: #2A1A1C;
    color: #E8A0A4;
    border: 1px solid #6A3034;
    border-radius: 3px;
}}

QPushButton[danger="true"]:hover, QPushButton#stop-server:hover, QPushButton#purge-btn:hover {{
    background-color: #3A2225;
    color: #FFC8CC;
    border: 1px solid {COLOR_DANGER};
}}

QPushButton[danger="true"]:pressed,
QPushButton#stop-server:pressed,
QPushButton#purge-btn:pressed {{
    background-color: #1A1012;
    color: #E8A0A4;
    border: 1px solid #6A3034;
}}

QPushButton[danger="true"]:focus, QPushButton#stop-server:focus, QPushButton#purge-btn:focus {{
    border: 1px solid #E06064;
}}

QPushButton[danger="true"]:disabled,
QPushButton#stop-server:disabled,
QPushButton#purge-btn:disabled {{
    background-color: #1E1618;
    color: #6A5A5C;
    border: 1px solid {COLOR_BRASS_DEEP};
}}

/* --- Group Boxes & Panels: flat sheets with a hairline and an accent edge --- */
QGroupBox {{
    background-color: {COLOR_BG_PANEL};
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0px 6px;
    background-color: {COLOR_BG_PANEL};
    color: {COLOR_TEXT_MUTED};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(12, scale)};
    font-weight: bold;
    border: none;
}}

/* A reusable raised panel sheet, opted into with `setObjectName("panel")`.
   Keyed on objectName rather than `QFrame[frameShape="StyledPanel"]` for two
   reasons: the attribute form matches the enum's NUMERIC value ("6"), so the
   spelled-out name silently never matches, and nothing in this app ever set
   `StyledPanel` anyway — every frame is `NoFrame`. The single 2px amber left
   edge is the one recurring ornament in the theme, and the whole of it. */
QFrame#panel {{
    background-color: {COLOR_BG_PANEL};
    border: 1px solid {COLOR_BRASS_DARK};
    border-left: 2px solid {COLOR_GOLD_BRASS};
    border-radius: 3px;
}}

QFrame#panel:hover {{
    border: 1px solid {COLOR_GOLD_BRASS};
    border-left: 2px solid {COLOR_GOLD_BRIGHT};
}}

/* --- Campaign Tiles ---
   The four tile covers are NOT styled here on purpose. `DadcraftCampaignCard`
   paints its own backdrop, particles and hover lighting in `paintEvent`, and it
   sets a widget-level `background: transparent; border: none;` — a widget's own
   stylesheet always wins over the application sheet, so any rule written here
   for `QFrame#catalog-tile-*` would be dead text. The tile art has exactly one
   owner: the decorations module. */

/* --- Catalog Tile Text Hierarchy & Scrollable Box Cover Inset --- */
QScrollArea#catalog-shelf-scroll,
QScrollArea#tile-desc-box {{
    background-color: transparent;
    border: none;
}}

QScrollArea#catalog-shelf-scroll > QWidget > QWidget,
QScrollArea#tile-desc-box > QWidget > QWidget {{
    background-color: transparent;
}}

QScrollArea#tile-desc-box QWidget,
QLabel#tile-desc {{
    background-color: transparent;
}}

QLabel#tile-title {{
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(15, scale)};
    font-weight: bold;
    color: {COLOR_TEXT_PRIMARY};
}}

QLabel#tile-subtitle {{
    font-size: {_px(12, scale)};
    color: {COLOR_TEXT_GOLD};
    font-weight: bold;
}}

QLabel#tile-desc {{
    font-size: {_px(13, scale)};
    color: {COLOR_TEXT_PRIMARY};
    background-color: transparent;
}}

QLabel#tile-meta {{
    font-size: {_px(12, scale)};
    color: {COLOR_TEXT_MUTED};
    background-color: transparent;
}}

QLabel#tile-warning {{
    font-size: {_px(12, scale)};
    font-style: italic;
    color: {COLOR_TEXT_WARNING};
}}

/* A panel heading ("Select a Server Emulator", "In the party now"). Titled
   panels used to fall back to body size because no rule existed for the
   objectName the code sets. */
QLabel#section-title {{
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(16, scale)};
    font-weight: bold;
    color: {COLOR_TEXT_PRIMARY};
    padding: 2px 0px;
}}

/* The header shown above the ControllerView's sub-tabs: the full name of the
   panel currently open, because the tab strip itself is icon-only. */
QLabel#panel-title {{
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(16, scale)};
    font-weight: bold;
    color: {COLOR_TEXT_PRIMARY};
    padding: 2px 0px;
}}

/* --- Input Fields & Spinners ---
   Single-line controls get an explicit min-height so they never collapse to
   the text's own line-height (no room for the padding, so the caret and
   selection highlight clipped). Multi-line controls (log/report boxes) get a
   much taller floor so they read as a panel, not a stray line, but are left
   free to grow with their layout's stretch factor. */
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {COLOR_BG_INPUT};
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
    padding: 7px 10px;
    selection-background-color: {COLOR_GOLD_BRASS};
    selection-color: {COLOR_TEXT_PRIMARY};
}}

QLineEdit, QSpinBox, QComboBox {{
    min-height: {_touch(30, scale)};
    min-width: 60px;
}}

QTextEdit, QPlainTextEdit {{
    min-height: 90px;
}}

QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border: 1px solid #4A4A4A;
}}

/* The focus ring is the amber hairline: 1px, but against the near-black input
   sheet it reads at a glance, and it is the one signal a controller user needs. */
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {COLOR_GOLD_BRIGHT};
    background-color: #181818;
}}

QLineEdit:disabled,
QSpinBox:disabled,
QComboBox:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled {{
    background-color: #161616;
    color: #6A6A6A;
    border: 1px solid {COLOR_BRASS_DEEP};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: {_touch(28, scale)};
    border-left: 1px solid {COLOR_BRASS_DARK};
    background-color: #202020;
}}

QComboBox::drop-down:hover {{
    background-color: #2A2A2A;
    border-left: 1px solid {COLOR_GOLD_BRASS};
}}

QComboBox QAbstractItemView {{
    background-color: {COLOR_BG_CONTAINER};
    border: 1px solid {COLOR_GOLD_BRASS};
    color: {COLOR_TEXT_PRIMARY};
    selection-background-color: {COLOR_GOLD_BRASS};
    selection-color: {COLOR_TEXT_PRIMARY};
    padding: 4px;
    outline: none;
}}

/* --- Tables, Lists, and Tree Views --- */
QListWidget, QTableWidget, QTreeWidget, QTreeView, QTableView {{
    background-color: {COLOR_BG_INPUT};
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
    gridline-color: #262626;
    color: {COLOR_TEXT_PRIMARY};
    alternate-background-color: #181818;
    font-size: {_px(13, scale)};
    outline: none;
}}

QListWidget::item, QTableWidget::item, QTreeWidget::item {{
    padding: 8px 10px;
    border-bottom: 1px solid #202020;
    min-height: {_touch(30, scale)};
}}

QListWidget::item:hover, QTableWidget::item:hover, QTreeWidget::item:hover {{
    background-color: #262014;
    color: {COLOR_GOLD_LIGHT};
}}

QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{
    background-color: #2E2718;
    color: {COLOR_GOLD_BRIGHT};
    border-left: 2px solid {COLOR_GOLD_BRIGHT};
}}

/* A focused-but-unselected list is otherwise indistinguishable from an
   unfocused one, which leaves a D-pad user without a position. */
QListWidget::item:focus, QTableWidget::item:focus, QTreeWidget::item:focus {{
    background-color: #262014;
    border-left: 2px solid {COLOR_GOLD_BRASS};
}}

QListWidget::item:disabled, QTableWidget::item:disabled, QTreeWidget::item:disabled {{
    color: #6A6A6A;
}}

QHeaderView::section {{
    background-color: #202020;
    color: {COLOR_TEXT_MUTED};
    font-family: {FONT_FAMILY_BODY};
    font-weight: bold;
    padding: 6px;
    border: none;
    border-right: 1px solid {COLOR_BRASS_DEEP};
    border-bottom: 1px solid {COLOR_BRASS_DARK};
}}

QHeaderView::section:hover {{
    background-color: #262626;
    color: {COLOR_GOLD_LIGHT};
}}

/* --- Scroll Bars: slim trough, flat amber-neutral handle --- */
QScrollBar:vertical {{
    background-color: {COLOR_BG_DARK};
    width: 12px;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: #3A3A3A;
    min-height: {_touch(32, scale)};
    border: none;
    border-radius: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {COLOR_GOLD_BRASS};
}}

QScrollBar::handle:vertical:pressed {{
    background-color: {COLOR_GOLD_BORDER};
}}

QScrollBar:horizontal {{
    background-color: {COLOR_BG_DARK};
    height: 12px;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: #3A3A3A;
    min-width: {_touch(32, scale)};
    border: none;
    border-radius: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {COLOR_GOLD_BRASS};
}}

QScrollBar::handle:horizontal:pressed {{
    background-color: {COLOR_GOLD_BORDER};
}}

/* The arrow buttons are removed outright: on a touch/controller handheld a
   10px arrow is a target nobody can hit, and dragging/wheel covers the use. */
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0px;
    width: 0px;
    border: none;
    background: none;
}}

QScrollBar::add-page, QScrollBar::sub-page {{
    background: none;
}}

/* --- Splitter ---
   A 6px hairline is not a touch target, so it is widened to a real one and
   gives an amber feedback line on hover. */
QSplitter::handle {{
    background-color: {COLOR_BG_DARK};
    border: none;
}}

QSplitter::handle:horizontal {{
    width: 10px;
}}

QSplitter::handle:vertical {{
    height: 10px;
}}

QSplitter::handle:hover {{
    background-color: {COLOR_GOLD_BRASS};
}}

QSplitter::handle:pressed {{
    background-color: {COLOR_GOLD_BRIGHT};
}}

/* --- Progress Bars (XP & Health/Mana Style) --- */
QProgressBar {{
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
    background-color: {COLOR_BG_INPUT};
    text-align: center;
    color: {COLOR_TEXT_PRIMARY};
    font-weight: bold;
    font-size: {_px(12, scale)};
    min-height: {_touch(20, scale)};
}}

QProgressBar::chunk {{
    background-color: {COLOR_UNCOMMON};
    border-radius: 2px;
}}

QProgressBar:disabled {{
    color: #6A6A6A;
    border-color: {COLOR_BRASS_DEEP};
}}

QProgressBar::chunk:disabled {{
    background-color: #3A3A3A;
}}

/* --- Item Tooltip --- */
QToolTip {{
    background-color: rgba(20, 20, 20, 0.97);
    color: {COLOR_TEXT_PRIMARY};
    border: 1px solid {COLOR_GOLD_BRASS};
    padding: 8px 12px;
    border-radius: 3px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
}}

/* --- Checkboxes & Radio Buttons ---
   The indicator is a real touch target (30px), not a 16px speck: one of the
   app's checkboxes governs whether uninstall keeps the character database. */
QCheckBox, QRadioButton {{
    spacing: 8px;
    color: {COLOR_TEXT_PRIMARY};
    min-height: {_touch(30, scale)};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: {_touch(22, scale)};
    height: {_touch(22, scale)};
    border: 1px solid #6A6A6A;
    background-color: {COLOR_BG_INPUT};
    border-radius: 3px;
}}

QRadioButton::indicator {{
    border-radius: {max(6, round(11 * scale))}px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {COLOR_GOLD_BRIGHT};
}}

QCheckBox::indicator:focus, QRadioButton::indicator:focus {{
    border-color: {COLOR_GOLD_BRIGHT};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {COLOR_GOLD_BRIGHT};
    border-color: {COLOR_GOLD_LIGHT};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {COLOR_BRASS_DEEP};
    background-color: #161616;
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: #6A6A6A;
}}

/* --- Menus & Context Menus --- */
QMenuBar {{
    background-color: {COLOR_BG_DARK};
    border-bottom: 1px solid {COLOR_BRASS_DARK};
    color: {COLOR_TEXT_PRIMARY};
}}

QMenuBar::item:selected {{
    background-color: #2A2A2A;
    color: {COLOR_GOLD_BRIGHT};
}}

QMenu {{
    background-color: {COLOR_BG_CONTAINER};
    border: 1px solid {COLOR_GOLD_BRASS};
    border-radius: 3px;
    padding: 4px;
    color: {COLOR_TEXT_PRIMARY};
}}

QMenu::item {{
    padding: 8px 22px 8px 14px;
    border-radius: 2px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
    color: {COLOR_TEXT_PRIMARY};
    min-height: {_touch(28, scale)};
}}

QMenu::item:selected {{
    background-color: #2A2620;
    color: {COLOR_GOLD_BRIGHT};
}}

QMenu::item:disabled {{
    color: #6A6A6A;
    background-color: transparent;
}}

QMenu::separator {{
    height: 1px;
    background: {COLOR_BRASS_DEEP};
    margin: 4px 6px;
}}

/* --- Labels & Status Badges --- */
QLabel#updateBanner {{
    background-color: #2A2418;
    color: {COLOR_GOLD_LIGHT};
    border: 1px solid {COLOR_GOLD_BRIGHT};
    border-radius: 3px;
    padding: 8px 14px;
    font-weight: bold;
}}

/* --- Log & Console Panels ---
   The elapsed/progress fields are justified by fixed-width monospace digits,
   so the monospace stack has to actually reach the log body and the strip
   labels — it was defined and then never applied to anything. */
QPlainTextEdit#log-text, QTextEdit#log-text {{
    font-family: {FONT_FAMILY_MONO};
    font-size: {_px(12, scale)};
    background-color: #101010;
    border: 1px solid {COLOR_BRASS_DARK};
    border-radius: 3px;
}}

QLabel#log-strip-label {{
    font-family: {FONT_FAMILY_MONO};
    font-size: {_px(12, scale)};
    color: {COLOR_TEXT_MUTED};
}}

QLabel#sidebar-key-hint {{
    color: {COLOR_TEXT_MUTED};
    font-size: {_px(11, scale)};
    padding: 2px 6px;
}}

/* --- Catalog tile text roles ---
   `DadcraftCampaignCard` paints its own ornate backdrop in `paintEvent`, so
   these rules only carry the text hierarchy that sits on top of it. */
QLabel#tile-glyph {{
    font-size: {_px(20, scale)};
    background: transparent;
}}

QLabel#tile-subtitle {{
    font-size: {_px(12, scale)};
    color: {COLOR_TEXT_GOLD};
    font-weight: bold;
    background: transparent;
}}
"""


DADCRAFT_THEME_QSS = _build_qss(1.0)
"""The theme at scale 1.0 (the reference width). Tests assert against this;
`apply_dadcraft_theme` re-generates it per window size at runtime."""


def build_dadcraft_palette() -> QPalette:
    """Build a QPalette matching the dark Dadcraft Minimal charcoal/amber scheme."""
    from PySide6.QtGui import QColor, QPalette

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(COLOR_BG_DARK))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Base, QColor(COLOR_BG_INPUT))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(COLOR_BG_CONTAINER))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(COLOR_BG_CONTAINER))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Text, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.Button, QColor(COLOR_BG_PANEL))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(COLOR_TEXT_PRIMARY))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(COLOR_GOLD_BRIGHT))
    palette.setColor(QPalette.ColorRole.Link, QColor(COLOR_RARE))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(COLOR_GOLD_BRASS))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(COLOR_TEXT_PRIMARY))
    # Placeholder text (a QLineEdit hint) would otherwise fall back to a
    # near-black default that is invisible on the dark input background.
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(COLOR_TEXT_MUTED))
    return palette


def scale_for_width(width: int) -> float:
    """The font scale for a window of `width` px, against `REFERENCE_WIDTH`.

    Linear, so a window at half the reference width gets half-size text, but
    clamped so it never drops below a legible floor. The reference width is the
    default window size, so the default view renders at exactly scale 1.0.

    **The upper bound is 1.0: sizes scale DOWN for a small window and never up.**
    Every base here is authored at `REFERENCE_WIDTH`, so a wider window rendering
    them larger is rendering something nobody chose. The old 1.4 ceiling meant a
    maximised 1920 desktop -- `min(1.4, 1.5)` -- drew every label, button, tab and
    input 40% over its authored size at once, and Pass 11's base raises (`_px(13,
    ...)` -> `_px(14, ...)`) were multiplied by it: 14 became 20px. On a handheld
    the same raises are invisible, because `MIN_FONT_PX` has already floored that
    range -- so the bump bought nothing where it was aimed and cost 40% where it
    was not (T45, the owner on a 1920 desktop).

    Narrower windows are untouched: at and below `REFERENCE_WIDTH` the new bound
    is never the active one, the linear scale still applies, and `MIN_FONT_PX` /
    `TOUCH_TARGET_PX` still catch the bottom. The Steam Deck renders exactly as
    it does today.
    """
    return max(0.7, min(1.0, width / REFERENCE_WIDTH))


# The base QStyle is installed exactly once, before any style sheet exists.
# Re-checking `QApplication.style().objectName()` on later calls is useless:
# once a sheet is set, `style()` returns the QStyleSheetStyle proxy, whose
# objectName is always empty, so the guard looked "not fusion" on every resize
# and rebuilt the whole application style each time.
_fusion_installed = False


def _ensure_fusion_style() -> None:
    """Install the Fusion base style once, on the first theme application.

    On macOS Qt defaults to the native Aqua style, which overrides and distorts
    custom QSS (segmented-control tabs, Cocoa button bevels, nested group-box
    borders). Fusion gives one deterministic base on macOS, Windows and Linux.
    The installed QStyleSheetStyle proxy hides the base afterwards, so a module
    flag — not a style query — records that it is already done.
    """
    global _fusion_installed
    if _fusion_installed:
        return
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setStyle("Fusion")
    _fusion_installed = True


def apply_dadcraft_theme(target: QApplication | QWidget, *, width: int | None = None) -> None:
    """Apply the Dadcraft theme stylesheet and palette to a QApplication or QWidget.

    `width` re-generates the stylesheet at a font scale derived from the window
    width (`scale_for_width`), so text grows/shrinks with the window. Omit it
    (or pass None) for the reference scale 1.0.
    """
    from PySide6.QtWidgets import QApplication

    _ensure_fusion_style()

    qss = _build_qss(scale_for_width(width) if width is not None else 1.0)
    if isinstance(target, QApplication):
        target.setPalette(build_dadcraft_palette())
        target.setStyleSheet(qss)
    else:
        target.setStyleSheet(qss)
