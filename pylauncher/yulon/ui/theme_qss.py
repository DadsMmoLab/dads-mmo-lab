"""QSS stylesheet generation and responsive width/touch scaling (Project Kael Phase K4).

Extracted from `yulon/ui/theme.py` so token definitions (`theme_spec.py`),
stylesheet generation (`theme_qss.py`), and Qt widget event filters
(`theme_widgets.py`) each live in a single-responsibility module behind
`theme.py`'s public facade (`style-guide.md` §1.1 & §3).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from yulon import resources
from yulon.ui.theme_spec import (
    DEFAULT_THEME,
    ThemeSpec,
    get_theme,
)
from yulon.ui.theme_widgets import TOUCH_TARGET_PX

__all__ = [
    "CHECK_UPDATES_BUTTON",
    "DADCRAFT_THEME_QSS",
    "FONT_FAMILY_BODY",
    "FONT_FAMILY_MONO",
    "FONT_FAMILY_TITLE",
    "FONT_TITLE_SINGLE",
    "FORGET_TAB_BUTTON",
    "LAUNCHER_PLAY_BUTTON",
    "LAUNCH_TAB_BUTTON",
    "MIN_FONT_PX",
    "PLAY_MENU_BUTTON",
    "QUESTION_PARAGRAPH",
    "REFERENCE_WIDTH",
    "SERVERS_MENU_BUTTON",
    "SERVER_BUILD_BUTTON",
    "SETTINGS_BUTTON",
    "SIDEBAR_PIN_BUTTON",
    "SIDEBAR_PIN_GAP",
    "SIDEBAR_RAIL_WIDTH",
    "TAB_BUTTONS",
    "TOP_MENU_BAR",
    "TOP_MENU_BUTTON",
    "_build_qss",
    "_image",
    "_indicator_px",
    "_px",
    "_scroller_width",
    "_spin_button_px",
    "_theme_images_dir_for",
    "_touch",
    "scale_for_width",
]

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


LAUNCH_TAB_BUTTON = "tab-play"
"""objectName of the ▶ beside the × on a server's sidebar tab: opens its launcher (T187).

Sized and exempted from the touch floor exactly as the × is, for the same reason;
the Server tab's Play is the pad's and the thumb's way to the same window.
"""

TAB_BUTTONS = "tab-buttons"
"""objectName of the strip that holds a server tab's ▶ and × side by side (T187)."""


QUESTION_PARAGRAPH = "question-paragraph"
"""objectName of one paragraph of a long question (T243, `message_box.FittedMessageBox`).

When the question is longer than the screen shows, each paragraph is a place the
pad stops, and this ring is how a player sees which one it stopped on."""


PLAY_MENU_BUTTON = "play-menu"
"""objectName of the Server tab's ▾ (T181): one arrow, as on `SERVER_BUILD_BUTTON` (T187)."""


SERVER_BUILD_BUTTON = "server-build"
"""objectName of the former Modules toolbar's menu button (T89), kept for T187's rule."""


LAUNCHER_PLAY_BUTTON = "launcher-play"
"""objectName of the client launcher window's big PLAY (T187).

It is a primary button (`primary="true"`, the ice-blue fill); this rule only makes
it the size the launcher is built around, in the theme's colours.
"""


SIDEBAR_PIN_BUTTON = "sidebar-pin"
"""objectName of the Catalog and Logs buttons pinned above the sidebar rail (T192, `sidebar.py`)."""

TOP_MENU_BAR = "top-menu-bar"
"""objectName of the centered horizontal navigation bar below the banner."""

TOP_MENU_BUTTON = "top-menu-button"
"""objectName of the centered menu items (Catalog, Servers, Log, Settings) in `TOP_MENU_BAR`."""

SERVERS_MENU_BUTTON = "top-menu-servers"
"""objectName of the Servers dropdown button in `TOP_MENU_BAR`."""

SIDEBAR_PIN_GAP = 4
"""The gap (px) above, between and below the two pinned buttons (`sidebar.SidebarPins`)."""

SIDEBAR_RAIL_WIDTH = 64
"""The rail's thickness (px): what a server tab measures across it under the
`QTabWidget#sidebar-tabs > QTabBar::tab` rule (measured offscreen 2026-10-03), and
the floor `sidebar.SidebarRail` keeps when every tab in it is hidden."""


CHECK_UPDATES_BUTTON = "check-for-updates"
SETTINGS_BUTTON = "yulon-settings"
"""The header's Settings… (T540): the tray's two switches."""
"""objectName of the header's "Check for updates" (T90; a real button since T193).

The header is 56px tall with 6px margins, so a button with the theme's 8px
vertical padding (50px) stood out over its bottom edge. Its rule keeps the
touch floor and trims only the padding.
"""


# --- Handheld geometry floors -------------------------------------------------
# The launcher ships on the Steam Deck (1280x800, 7"), driven by controller and
# touch. Every interactive widget gets a floor of at least `TOUCH_TARGET_PX` in
# its short dimension so a D-pad or thumb never has to aim, and every font has a
# hard rendered-pixel floor regardless of how far the window scale collapses.
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


def _scroller_width(base: int, scale: float) -> str:
    """A tab bar's two scroll arrows side by side: each `_touch()` plus a 1 px border either side.

    Qt's style sheet gives each arrow half of `QTabBar::scroller`'s width.
    """
    return f"{2 * (int(_touch(base, scale).removesuffix('px')) + 2)}px"


def _px(base: int, scale: float) -> str:
    """A font size at `scale`, floored so text never becomes unreadable."""
    return f"{max(MIN_FONT_PX, round(base * scale))}px"


_TINTED_SVG_CACHE: dict[tuple[str, str, str, str, str], Path] = {}


def _theme_images_dir_for(spec: ThemeSpec) -> Path:
    """Return the SVG directory for `spec`, generating tinted assets for non-default themes."""
    base_dir = resources.theme_images_dir()
    if spec.id == DEFAULT_THEME:
        return base_dir
    target_dir = Path(tempfile.gettempdir()) / "yulon-theme-images" / spec.id
    cache_key = (
        spec.id,
        spec.primary_btn_text,
        spec.text_muted,
        spec.accent_bright,
        spec.text_primary,
    )
    if _TINTED_SVG_CACHE.get(cache_key) == target_dir and (target_dir / "check.svg").is_file():
        return target_dir
    svgs: dict[str, str] = {
        "check.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" '
            f'stroke="{spec.primary_btn_text}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "check-disabled.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M5 12.5l4.5 4.5L19 7.5" fill="none" '
            f'stroke="{spec.text_muted}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "radio-dot.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<circle cx="12" cy="12" r="6" fill="{spec.accent_bright}"/></svg>'
        ),
        "radio-dot-disabled.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            f'<circle cx="12" cy="12" r="6" fill="{spec.text_muted}"/></svg>'
        ),
        "arrow-down.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M5 8.5l7 7 7-7" fill="none" '
            f'stroke="{spec.text_primary}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow-down-disabled.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M5 8.5l7 7 7-7" fill="none" '
            f'stroke="{spec.text_muted}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow-left.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M15.5 5l-7 7 7 7" fill="none" '
            f'stroke="{spec.text_primary}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow-left-disabled.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M15.5 5l-7 7 7 7" fill="none" '
            f'stroke="{spec.text_muted}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow-right.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M8.5 5l7 7-7 7" fill="none" '
            f'stroke="{spec.text_primary}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "arrow-right-disabled.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M8.5 5l7 7-7 7" fill="none" '
            f'stroke="{spec.text_muted}" stroke-width="3" '
            'stroke-linecap="round" stroke-linejoin="round"/></svg>'
        ),
        "plus.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M12 4v16M4 12h16" fill="none" '
            f'stroke="{spec.text_primary}" stroke-width="4" stroke-linecap="round"/></svg>'
        ),
        "plus-disabled.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M12 4v16M4 12h16" fill="none" '
            f'stroke="{spec.text_muted}" stroke-width="4" stroke-linecap="round"/></svg>'
        ),
        "minus.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M4 12h16" fill="none" '
            f'stroke="{spec.text_primary}" stroke-width="4" stroke-linecap="round"/></svg>'
        ),
        "minus-disabled.svg": (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
            '<path d="M4 12h16" fill="none" '
            f'stroke="{spec.text_muted}" stroke-width="4" stroke-linecap="round"/></svg>'
        ),
    }
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename, content in svgs.items():
            svg_path = target_dir / filename
            if not svg_path.exists() or svg_path.read_text(encoding="utf-8") != content:
                svg_path.write_text(content + "\n", encoding="utf-8")
        _TINTED_SVG_CACHE[cache_key] = target_dir
        return target_dir
    except OSError:
        return base_dir


def _image(name: str, spec: ThemeSpec | None = None) -> str:
    """`url("<path>")` for one of the theme's SVGs (T193), read at build time.

    Quoted, because the path is inside Yu'lon's own install, which has an
    apostrophe and often spaces, and an unquoted `url()` would end at either.
    Forward slashes on every platform, which is what Qt's URL parser expects.
    Looked up through the module on each call, so a sheet built for a moved
    bundle (or a test's copy) names the files that are really there.
    """
    folder = resources.theme_images_dir() if spec is None else _theme_images_dir_for(spec)
    return f'url("{(folder / name).as_posix()}")'


def _indicator_px(scale: float) -> int:
    """The checkbox / radio indicator's side (px) at `scale`, without its 1px border."""
    return int(_touch(22, scale).removesuffix("px"))


def _spin_button_px(scale: float) -> int:
    """The side (px) of each of a spin box's square − and + buttons at `scale`."""
    return int(_touch(28, scale).removesuffix("px"))


def _build_qss(scale: float, theme_name: str | None = None) -> str:
    spec = get_theme(theme_name)
    return f"""
/* ==========================================================================
   Yu'lon — "Dadcraft Minimal" ({spec.label})
   Flat blue-slate sheets, one ice-blue accent, 1px hairlines, 3px radii.
   The sheet uses no negation pseudo-selector: Qt's parser has no such
   combinator and would discard every rule after the failure point.
   ========================================================================== */

/* --- Global Base Window & Central Widget --- */
QMainWindow, QDialog, QWidget#centralWidget {{
    background-color: {spec.bg_dark};
    color: {spec.text_primary};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(14, scale)};
}}

QWidget {{
    color: {spec.text_primary};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(14, scale)};
}}

/* --- Header & Realm Badge Typography (Project Kael Phase K4) --- */
QLabel#header-title {{
    font-family: {FONT_FAMILY_TITLE};
    font-size: {_px(20, scale)};
    font-weight: bold;
    background: transparent;
}}

QLabel#header-subtitle {{
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(12, scale)};
    background: transparent;
}}

QLabel#realm-title {{
    font-family: {FONT_FAMILY_TITLE};
    font-size: {_px(13, scale)};
    font-weight: bold;
    background: transparent;
}}

QLabel#realm-badge-label {{
    font-family: {FONT_FAMILY_TITLE};
    font-size: {_px(12, scale)};
    font-weight: bold;
}}

/* --- Tab Widget: flat sheet, hairline frame, slim tabs --- */
QTabWidget::pane {{
    border: 1px solid {spec.border_dark};
    background-color: {spec.bg_container};
    border-radius: 3px;
    top: -1px;
    /* Restrained breathing room: 8px around page content keeps panels off the
       frame without spending height that the dense tabs need on an 800px screen. */
    padding: 8px;
}}

/* The main window tab pane descends directly from `DadcraftHeader` with zero top gap,
   so a server panel's centered sub-tab bar hangs flush beneath the top banner bar. */
QTabWidget#sidebar-tabs::pane {{
    top: 0px;
    border-top: none;
    border-top-left-radius: 0px;
    border-top-right-radius: 0px;
    padding: 0px 8px 8px 8px;
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
   selected tab replaces with the ice-blue accent — the "subtle bottom-line accent"
   the brief asks for, without a single gradient. */
QTabBar::tab, QTabBar::tab:top {{
    background-color: {spec.bg_panel};
    color: {spec.text_muted};
    border: 1px solid {spec.border_dark};
    border-top: 2px solid transparent;
    border-radius: 0px;
    padding: 8px 16px;
    min-height: {_touch(26, scale)};
    min-width: 52px;
    margin-right: -1px;
}}

QTabBar::tab:hover, QTabBar::tab:top:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
    border: 1px solid {spec.accent_brass};
    border-top: 2px solid {spec.accent_brass};
}}

QTabBar::tab:selected, QTabBar::tab:top:selected {{
    background-color: {spec.bg_container};
    color: {spec.accent_bright};
    border: 1px solid {spec.border_dark};
    border-top: 2px solid {spec.accent_bright};
    border-bottom: 1px solid {spec.bg_container};
}}

/* Keyboard/D-pad focus on a tab is distinct from hover: an ice-blue hairline ring
   on all four sides, so a controller user can see which tab will activate. */
QTabBar::tab:focus, QTabBar::tab:top:focus {{
    border: 1px solid {spec.accent_bright};
}}

/* Server sub-tabs (Server, Console, Accounts, Characters, Bots, Modules, Tuning,
   Backups & updates, Networking): centered horizontally and descending from the top banner. */
QTabWidget#server-sub-tabs::tab-bar {{
    alignment: center;
}}

QTabWidget#server-sub-tabs::pane {{
    top: 0px;
}}

QTabWidget#server-sub-tabs > QTabBar::tab {{
    border-top: none;
    border-bottom-left-radius: 4px;
    border-bottom-right-radius: 4px;
}}

QTabWidget#server-sub-tabs > QTabBar::tab:hover {{
    border-top: none;
    border-bottom: 2px solid {spec.accent_brass};
}}

QTabWidget#server-sub-tabs > QTabBar::tab:selected {{
    border-top: none;
    border-bottom: 2px solid {spec.accent_bright};
}}

/* A focused sub-tab bar is the D-pad's stop above its page (T874): the same
   ice-blue focus ring the buttons use, so the player sees which tab the
   Left/Right presses will switch. */
QTabWidget#server-sub-tabs > QTabBar::tab:focus {{
    border: 1px solid {spec.accent_bright};
    border-bottom: 2px solid {spec.accent_bright};
}}

/* West (left sidebar) tabs — the realm rail.
   Selected by objectName (`sidebar-tabs` in main.py), never by a negation and
   never by the `:west` pseudo-state: Qt does not reliably honour positional
   pseudo-states on `::tab` (measured). The right edge is left unbordered so the
   rail merges into the pane beside it. A CHILD selector (T193, found by T189):
   with a space it reached every tab bar below the rail too, and capped a
   server page's own sub-tabs at the rail's 64px. */
QTabWidget#sidebar-tabs > QTabBar::tab {{
    background-color: {spec.bg_panel};
    color: {spec.text_muted};
    border: 1px solid transparent;
    border-left: 3px solid transparent;
    border-radius: 0px;
    padding: 8px 6px;
    margin: 2px 0px;
    min-width: 48px;
    max-width: 64px;
    min-height: {_touch(44, scale)};
}}

QTabWidget#sidebar-tabs > QTabBar::tab:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
    border: 1px solid {spec.accent_brass};
    border-left: 3px solid {spec.accent_brass};
}}

QTabWidget#sidebar-tabs > QTabBar::tab:selected {{
    background-color: {spec.bg_container};
    color: {spec.accent_bright};
    border: 1px solid {spec.border_dark};
    border-left: 3px solid {spec.accent_bright};
}}

QTabWidget#sidebar-tabs > QTabBar::tab:focus {{
    border: 1px solid {spec.accent_bright};
    border-left: 3px solid {spec.accent_bright};
}}

/* T192: Catalog and Logs, pinned above the rail, each drawn as a rail tab is:
   muted at rest, the blue left edge while its page is on screen. The bar is
   pushed down by the strip they fill, and that offset is set by `SidebarPins`
   from the pins' own size hint, not here: their height is the platform font's
   (an icon over a label), and an offset spelled in this sheet would have to
   guess it. No horizontal padding: "Catalog" plus the 3px edge just fits the
   64px rail, and with 2px a side it asked for 66 (measured). */
QToolButton#{SIDEBAR_PIN_BUTTON} {{
    background-color: {spec.bg_panel};
    color: {spec.text_muted};
    border: 1px solid transparent;
    border-left: 3px solid transparent;
    border-radius: 0px;
    padding: 2px 0px;
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(12, scale)};
    min-height: {_touch(30, scale)};
}}

QToolButton#{SIDEBAR_PIN_BUTTON}:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
    border: 1px solid {spec.accent_brass};
    border-left: 3px solid {spec.accent_brass};
}}

QToolButton#{SIDEBAR_PIN_BUTTON}:checked {{
    background-color: {spec.bg_container};
    color: {spec.accent_bright};
    border: 1px solid {spec.border_dark};
    border-left: 3px solid {spec.accent_bright};
}}

QToolButton#{SIDEBAR_PIN_BUTTON}:focus {{
    border: 1px solid {spec.accent_bright};
    border-left: 3px solid {spec.accent_bright};
}}

/* Right-aligned horizontal module topbar & secondary utility cluster */
QWidget#{TOP_MENU_BAR} {{
    background-color: {spec.bg_panel};
    border-bottom: 1px solid {spec.border_dark};
}}

QFrame#top-secondary-toolbar {{
    background-color: transparent;
    border: none;
}}

QFrame#top-menu-separator {{
    background-color: {spec.border_dark};
    border: none;
    max-width: 1px;
}}

QToolButton#{TOP_MENU_BUTTON},
QToolButton#{SERVERS_MENU_BUTTON},
QToolButton#{SETTINGS_BUTTON} {{
    background-color: transparent;
    color: {spec.text_muted};
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    border-radius: 3px;
    padding: 0px 6px;
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(13, scale)};
    font-weight: bold;
    min-height: {_touch(28, scale)};
    min-width: 52px;
}}

QToolButton#{TOP_MENU_BUTTON}:hover,
QToolButton#{SERVERS_MENU_BUTTON}:hover,
QToolButton#{SETTINGS_BUTTON}:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
    border: 1px solid {spec.accent_brass};
    border-bottom: 2px solid {spec.accent_brass};
}}

QToolButton#{TOP_MENU_BUTTON}:checked,
QToolButton#{SERVERS_MENU_BUTTON}:checked,
QToolButton#{SETTINGS_BUTTON}:checked {{
    background-color: {spec.bg_container};
    color: {spec.accent_bright};
    border: 1px solid {spec.border_dark};
    border-bottom: 2px solid {spec.accent_bright};
}}

QToolButton#{TOP_MENU_BUTTON}:focus,
QToolButton#{SERVERS_MENU_BUTTON}:focus,
QToolButton#{SETTINGS_BUTTON}:focus {{
    border: 1px solid {spec.accent_bright};
    border-bottom: 2px solid {spec.accent_bright};
    color: {spec.accent_light};
}}

QToolButton#{TOP_MENU_BUTTON}:disabled,
QToolButton#{SERVERS_MENU_BUTTON}:disabled,
QToolButton#{SETTINGS_BUTTON}:disabled {{
    background-color: transparent;
    color: {spec.tab_disabled};
    border-color: transparent;
}}

QToolButton#{SERVERS_MENU_BUTTON}::menu-indicator {{
    image: none;
    width: 0px;
}}

QTabBar::tab:disabled {{
    background-color: {spec.bg_panel};
    color: {spec.tab_disabled};
    border-color: {spec.border_deep};
}}

/* Scroll arrows for tab bars with more tabs than fit. They are the only route
   to an off-screen tab, so they carry a real touch floor rather than collapsing
   to the icon's own size. */
QTabBar QToolButton {{
    background-color: {spec.bg_panel};
    border: 1px solid {spec.border_dark};
    border-radius: 3px;
    min-width: {_touch(30, scale)};
    min-height: {_touch(30, scale)};
}}

/* Where QTabBar PUTS its two arrows: it places them by this width, halved for
   each (PM_TabBarScrollButtonWidth, 16 px each unset), not by their min-width
   above, so at 960x640 each arrow was drawn 34 px wide in a 16 px slot and the
   right one ran off the bar's end (PR 291 Windows live test). Two arrows, each
   the floor plus its 1 px border either side. */
QTabBar::scroller {{
    width: {_scroller_width(30, scale)};
}}

QTabBar QToolButton:hover {{
    border-color: {spec.accent_bright};
    background-color: {spec.bg_hover};
}}

QTabBar QToolButton:focus {{
    border-color: {spec.accent_bright};
}}

QTabBar QToolButton:disabled {{
    border-color: {spec.border_deep};
    color: {spec.tab_disabled};
}}

/* Their glyphs. Styling the button replaces the base style's arrow, and a sheet
   draws only what it names: at 960x640 the two were dark boxes with nothing in
   them (PR 291 Linux live test). */
QTabBar QToolButton::left-arrow {{
    image: {_image("arrow-left.svg", spec)};
    width: 14px;
    height: 14px;
}}

QTabBar QToolButton::left-arrow:disabled {{
    image: {_image("arrow-left-disabled.svg", spec)};
}}

QTabBar QToolButton::right-arrow {{
    image: {_image("arrow-right.svg", spec)};
    width: 14px;
    height: 14px;
}}

QTabBar QToolButton::right-arrow:disabled {{
    image: {_image("arrow-right-disabled.svg", spec)};
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
    color: {spec.text_muted};
    padding: 0px;
    min-width: 16px;
    min-height: 16px;
    max-width: 18px;
    max-height: 18px;
}}

QTabBar QToolButton#{FORGET_TAB_BUTTON}:hover {{
    color: {spec.danger};
}}

/* T187: the ▶ beside it opens the server's launcher. The same exemption and the
   same size, so the two sit side by side across the 64px rail and the tab is no
   longer than the × alone made it. Gold at rest, so it reads apart from the muted
   ×, and brighter on hover, where the × goes red. */
QTabBar QToolButton#{LAUNCH_TAB_BUTTON} {{
    background-color: transparent;
    border: none;
    color: {spec.accent_light};
    font-size: 15px;
    padding: 0px;
    min-width: 16px;
    min-height: 16px;
    max-width: 18px;
    max-height: 18px;
}}

QTabBar QToolButton#{LAUNCH_TAB_BUTTON}:hover {{
    color: {spec.accent_bright};
}}

QTabBar QWidget#{TAB_BUTTONS} {{
    background: transparent;
}}

/* --- Buttons: flat sheets with a hairline, ice-blue on interaction --- */
QPushButton {{
    background-color: {spec.bg_panel};
    color: {spec.text_primary};
    border: 1px solid {spec.border_dark};
    border-radius: 3px;
    padding: 8px 16px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
    font-weight: normal;
    min-height: {_touch(28, scale)};
    min-width: 64px;
}}

QPushButton:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
    border: 1px solid {spec.accent_brass};
}}

QPushButton:pressed {{
    /* The sunken look is fill + border only. Padding must stay identical to the
       base rule in every state: a padding shift here used to nudge the content
       without a matching change on the other sides, so a pressed button's box
       stopped lining up with its neighbours in the same row. */
    background-color: {spec.bg_input};
    color: {spec.accent_bright};
    border: 1px solid {spec.accent_brass};
}}

/* Controller/D-pad focus ring. Distinct from hover so a keyboard user always
   knows where the D-pad will land. */
QPushButton:focus {{
    border: 1px solid {spec.accent_bright};
    color: {spec.accent_light};
}}

/* T243: the paragraph of a long question the pad has stopped on. The border is
   always there, clear, so a focused paragraph does not re-wrap when it lights.
   `border: 0px` first: with only `border-left` set, Qt sizes the border and
   never draws it (measured offscreen, PySide6 6.11). */
QLabel#{QUESTION_PARAGRAPH} {{
    border: 0px solid transparent;
    border-left: 2px solid transparent;
    padding-left: 6px;
}}
QLabel#{QUESTION_PARAGRAPH}:focus {{
    border-left: 2px solid {spec.accent_bright};
}}

QPushButton:disabled {{
    background-color: {spec.bg_container};
    color: {spec.text_disabled};
    border: 1px solid {spec.border_deep};
}}

/* T89: the menu button says it opens a menu in its own label (its triangle), so the
   style's indicator would be a second arrow, and it reserves width for one too. */
QPushButton#{SERVER_BUILD_BUTTON}::menu-indicator,
QPushButton#{PLAY_MENU_BUTTON}::menu-indicator {{
    image: none;
    width: 0px;
}}

/* Primary / prominent actions (Install, Start): the one place the accent is
   allowed to be a fill rather than a hairline. */
QPushButton[primary="true"],
QPushButton#start-server,
QPushButton#install-btn,
QPushButton[objectName^="install-"] {{
    background-color: {spec.accent_border};
    color: {spec.primary_btn_text};
    border: 1px solid {spec.accent_border};
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
    background-color: {spec.accent_bright};
    color: {spec.primary_btn_text};
    border: 1px solid {spec.accent_light};
}}

QPushButton[primary="true"]:pressed,
QPushButton#start-server:pressed,
QPushButton#install-btn:pressed,
QPushButton[objectName^="install-"]:pressed {{
    background-color: {spec.accent_border};
    color: {spec.primary_btn_text};
    border: 1px solid {spec.accent_brass};
}}

QPushButton[primary="true"]:focus,
QPushButton#start-server:focus,
QPushButton#install-btn:focus,
QPushButton[objectName^="install-"]:focus {{
    border: 1px solid {spec.accent_light};
}}

QPushButton[primary="true"]:disabled,
QPushButton#start-server:disabled,
QPushButton#install-btn:disabled,
QPushButton[objectName^="install-"]:disabled {{
    background-color: {spec.bg_hover};
    color: {spec.primary_btn_disabled_text};
    border: 1px solid {spec.border_deep};
}}

/* T187: the client launcher's PLAY -- a primary button, the size of the window's purpose. */
QPushButton#{LAUNCHER_PLAY_BUTTON} {{
    font-size: {_px(26, scale)};
    font-weight: bold;
    letter-spacing: 3px;
    min-height: {_touch(58, scale)};
    border-radius: 4px;
}}

/* T193 A20: the header's update check, a real button that fits the header.
   T540: Settings… beside it, the same size. */
QPushButton#{CHECK_UPDATES_BUTTON}, QPushButton#{SETTINGS_BUTTON} {{
    padding: 4px 14px;
}}

/* Destructive actions (Stop, Purge, Uninstall). The edge is `COLOR_DANGER` at
   rest (T193): the old `#6A3034` was 1.8:1 against the pane, under the 3:1 a
   control's edge needs, and read exactly like the `:disabled` look below. */
QPushButton[danger="true"], QPushButton#stop-server, QPushButton#purge-btn {{
    background-color: {spec.danger_bg};
    color: {spec.danger_text};
    border: 1px solid {spec.danger};
    border-radius: 3px;
}}

QPushButton[danger="true"]:hover, QPushButton#stop-server:hover, QPushButton#purge-btn:hover {{
    background-color: {spec.danger_hover_bg};
    color: {spec.danger_hover_text};
    border: 1px solid {spec.danger};
}}

QPushButton[danger="true"]:pressed,
QPushButton#stop-server:pressed,
QPushButton#purge-btn:pressed {{
    background-color: {spec.danger_pressed_bg};
    color: {spec.danger_text};
    border: 1px solid {spec.danger_border_pressed};
}}

QPushButton[danger="true"]:focus, QPushButton#stop-server:focus, QPushButton#purge-btn:focus {{
    border: 1px solid {spec.danger_border_focus};
}}

QPushButton[danger="true"]:disabled,
QPushButton#stop-server:disabled,
QPushButton#purge-btn:disabled {{
    background-color: {spec.danger_disabled_bg};
    color: {spec.danger_disabled_text};
    border: 1px solid {spec.border_deep};
}}

/* --- Group Boxes & Panels: flat sheets with a hairline and an accent edge --- */
QGroupBox {{
    background-color: {spec.bg_panel};
    border: 1px solid {spec.border_dark};
    border-radius: 3px;
    margin-top: 10px;
    padding: 12px 10px 10px 10px;
}}

QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0px 6px;
    background-color: {spec.bg_panel};
    color: {spec.text_muted};
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(12, scale)};
    font-weight: bold;
    border: none;
}}

/* A reusable raised panel sheet, opted into with `setObjectName("panel")`.
   Keyed on objectName rather than `QFrame[frameShape="StyledPanel"]` for two
   reasons: the attribute form matches the enum's NUMERIC value ("6"), so the
   spelled-out name silently never matches, and nothing in this app ever set
   `StyledPanel` anyway — every frame is `NoFrame`. The single 2px blue left
   edge is the one recurring ornament in the theme, and the whole of it. */
QFrame#panel {{
    background-color: {spec.bg_panel};
    border: 1px solid {spec.border_dark};
    border-left: 2px solid {spec.accent_brass};
    border-radius: 3px;
}}

QFrame#panel:hover {{
    border: 1px solid {spec.accent_brass};
    border-left: 2px solid {spec.accent_bright};
}}

/* --- Campaign Tiles ---
   The four tile covers are NOT styled here on purpose. `DadcraftCampaignCard`
   paints its own backdrop, particles and header-lit frame in `paintEvent`, and
   its own sheet makes the card itself transparent and borderless — a widget's own
   stylesheet wins over the application sheet, so any rule written here for
   `QFrame#catalog-tile-*` would be dead text. The tile art has exactly one
   owner: the decorations module. That sheet names the card by its id
   (T188 C1), so the buttons and labels inside it are styled from here. */

/* --- The window's scroll area (T388): see-through, so the window's own backdrop shows --- */
QScrollArea#window-scroll {{
    background-color: transparent;
    border: none;
}}

QScrollArea#window-scroll > QWidget > QWidget#window-scroll-content {{
    background-color: transparent;
}}

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
    color: {spec.tile_title};
}}

QLabel#tile-subtitle {{
    font-size: {_px(12, scale)};
    color: {spec.tile_subtitle};
    font-weight: bold;
}}

QLabel#tile-desc {{
    font-size: {_px(13, scale)};
    color: {spec.tile_desc};
    background-color: transparent;
}}

QLabel#tile-meta {{
    font-size: {_px(12, scale)};
    color: {spec.tile_meta};
    background-color: transparent;
}}

QLabel#tile-warning {{
    font-size: {_px(12, scale)};
    font-style: italic;
    color: {spec.tile_warning};
}}

/* A panel heading ("Select a Server Emulator", "In the party now"). Titled
   panels used to fall back to body size because no rule existed for the
   objectName the code sets. */
QLabel#section-title {{
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(16, scale)};
    font-weight: bold;
    color: {spec.text_primary};
    padding: 2px 0px;
}}

/* The header shown above the ControllerView's sub-tabs: the full name of the
   panel currently open, because the tab strip itself is icon-only. */
QLabel#panel-title {{
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(16, scale)};
    font-weight: bold;
    color: {spec.text_primary};
    padding: 2px 0px;
}}

/* --- Input Fields & Spinners ---
   Single-line controls get an explicit min-height so they never collapse to
   the text's own line-height (no room for the padding, so the caret and
   selection highlight clipped). Multi-line controls (log/report boxes) get a
   much taller floor so they read as a panel, not a stray line, but are left
   free to grow with their layout's stretch factor. */
QLineEdit, QSpinBox, QComboBox, QTextEdit, QPlainTextEdit {{
    background-color: {spec.bg_input};
    color: {spec.text_primary};
    border: 1px solid {spec.border_dark};
    border-radius: 3px;
    padding: 7px 10px;
    selection-background-color: {spec.accent_brass};
    selection-color: {spec.text_primary};
}}

QLineEdit, QSpinBox, QComboBox {{
    min-height: {_touch(30, scale)};
    min-width: 60px;
}}

QTextEdit, QPlainTextEdit {{
    min-height: 90px;
}}

QLineEdit:hover, QSpinBox:hover, QComboBox:hover, QTextEdit:hover, QPlainTextEdit:hover {{
    border: 1px solid {spec.border_dark};
}}

/* The focus ring is the ice-blue hairline: 1px, but against the near-black input
   sheet it reads at a glance, and it is the one signal a controller user needs. */
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QTextEdit:focus, QPlainTextEdit:focus {{
    border: 1px solid {spec.accent_bright};
    background-color: {spec.bg_container};
}}

QLineEdit:disabled,
QSpinBox:disabled,
QComboBox:disabled,
QTextEdit:disabled,
QPlainTextEdit:disabled {{
    background-color: {spec.bg_container};
    color: {spec.text_disabled};
    border: 1px solid {spec.border_deep};
}}

QComboBox::drop-down {{
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: {_touch(28, scale)};
    border-left: 1px solid {spec.border_dark};
    background-color: {spec.bg_panel};
}}

QComboBox::drop-down:hover {{
    background-color: {spec.bg_hover};
    border-left: 1px solid {spec.accent_brass};
}}

/* T193: the combo's arrow. `::drop-down` alone drew an empty well -- the
   style sheet replaces the base style's arrow and draws only what it names. */
QComboBox::down-arrow {{
    image: {_image("arrow-down.svg", spec)};
    width: 14px;
    height: 14px;
}}

QComboBox::down-arrow:disabled {{
    image: {_image("arrow-down-disabled.svg", spec)};
}}

/* T193: a spin box's − and + are two touch-sized squares either side of the
   number, not the 16-px stacked halves that drew a 1-px dash. The style sheet
   takes each button's width out of the edit field itself, so the shared
   padding above is kept as the gap between a button and the number. */
QSpinBox, QDoubleSpinBox {{
    min-width: {2 * _spin_button_px(scale) + 48}px;
}}

QSpinBox::up-button, QDoubleSpinBox::up-button {{
    subcontrol-origin: border;
    subcontrol-position: center right;
    width: {_spin_button_px(scale)}px;
    height: {_spin_button_px(scale)}px;
    border: none;
    border-left: 1px solid {spec.border_dark};
    background-color: {spec.bg_panel};
}}

QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border;
    subcontrol-position: center left;
    width: {_spin_button_px(scale)}px;
    height: {_spin_button_px(scale)}px;
    border: none;
    border-right: 1px solid {spec.border_dark};
    background-color: {spec.bg_panel};
}}

QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover {{
    background-color: {spec.bg_hover};
    border-color: {spec.accent_brass};
}}

QSpinBox::up-button:pressed, QDoubleSpinBox::up-button:pressed,
QSpinBox::down-button:pressed, QDoubleSpinBox::down-button:pressed {{
    background-color: {spec.bg_input};
}}

QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: {_image("plus.svg", spec)};
    width: 14px;
    height: 14px;
}}

QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: {_image("minus.svg", spec)};
    width: 14px;
    height: 14px;
}}

QSpinBox::up-arrow:disabled, QDoubleSpinBox::up-arrow:disabled {{
    image: {_image("plus-disabled.svg", spec)};
}}

QSpinBox::down-arrow:disabled, QDoubleSpinBox::down-arrow:disabled {{
    image: {_image("minus-disabled.svg", spec)};
}}

QComboBox QAbstractItemView {{
    background-color: {spec.bg_container};
    border: 1px solid {spec.accent_brass};
    color: {spec.text_primary};
    selection-background-color: {spec.accent_brass};
    selection-color: {spec.primary_btn_text if spec.is_light else spec.text_primary};
    padding: 4px;
    outline: none;
}}

/* --- Tables, Lists, and Tree Views --- */
QListWidget, QTableWidget, QTreeWidget, QTreeView, QTableView {{
    background-color: {spec.bg_input};
    border: 1px solid {spec.border_dark};
    border-radius: 3px;
    gridline-color: {spec.border_deep};
    color: {spec.text_primary};
    alternate-background-color: {spec.bg_container};
    font-size: {_px(13, scale)};
    outline: none;
}}

QListWidget::item, QTableWidget::item, QTreeWidget::item {{
    padding: 8px 10px;
    border-bottom: 1px solid {spec.border_deep};
    min-height: {_touch(30, scale)};
}}

QListWidget::item:hover, QTableWidget::item:hover, QTreeWidget::item:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
}}

/* A focused-but-unselected list is otherwise indistinguishable from an
   unfocused one, which leaves a D-pad user without a position. */
QListWidget::item:focus, QTableWidget::item:focus, QTreeWidget::item:focus {{
    background-color: {spec.bg_hover};
    border-left: 2px solid {spec.accent_brass};
}}

QListWidget::item:selected, QTableWidget::item:selected, QTreeWidget::item:selected {{
    background-color: {spec.accent_brass};
    color: {spec.primary_btn_text if spec.is_light else spec.accent_bright};
    border-left: 2px solid {spec.accent_bright};
}}

QListWidget::item:disabled, QTableWidget::item:disabled, QTreeWidget::item:disabled {{
    color: {spec.text_disabled};
}}

QHeaderView::section {{
    background-color: {spec.bg_container};
    color: {spec.text_muted};
    font-family: {FONT_FAMILY_BODY};
    font-weight: bold;
    padding: 6px;
    border: none;
    border-right: 1px solid {spec.border_deep};
    border-bottom: 1px solid {spec.border_dark};
}}

QHeaderView::section:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
}}

/* --- Scroll Bars: slim trough, flat ice-blue handle --- */
QScrollBar:vertical {{
    background-color: {spec.bg_dark};
    width: 12px;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:vertical {{
    background-color: {spec.border_dark};
    min-height: {_touch(32, scale)};
    border: none;
    border-radius: 2px;
}}

QScrollBar::handle:vertical:hover {{
    background-color: {spec.accent_brass};
}}

QScrollBar::handle:vertical:pressed {{
    background-color: {spec.accent_border};
}}

QScrollBar:horizontal {{
    background-color: {spec.bg_dark};
    height: 12px;
    margin: 0px;
    border: none;
}}

QScrollBar::handle:horizontal {{
    background-color: {spec.border_dark};
    min-width: {_touch(32, scale)};
    border: none;
    border-radius: 2px;
}}

QScrollBar::handle:horizontal:hover {{
    background-color: {spec.accent_brass};
}}

QScrollBar::handle:horizontal:pressed {{
    background-color: {spec.accent_border};
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
   gives an ice-blue feedback line on hover. */
QSplitter::handle {{
    background-color: {spec.bg_dark};
    border: none;
}}

QSplitter::handle:horizontal {{
    width: 10px;
}}

QSplitter::handle:vertical {{
    height: 10px;
}}

QSplitter::handle:hover {{
    background-color: {spec.accent_brass};
}}

QSplitter::handle:pressed {{
    background-color: {spec.accent_bright};
}}

/* --- Progress Bars (XP & Health/Mana Style) --- */
QProgressBar {{
    border: 1px solid {spec.border_dark};
    border-radius: 3px;
    background-color: {spec.bg_input};
    text-align: center;
    color: {spec.text_primary};
    font-weight: bold;
    font-size: {_px(12, scale)};
    min-height: {_touch(20, scale)};
}}

QProgressBar::chunk {{
    background-color: {spec.uncommon};
    border-radius: 2px;
}}

QProgressBar:disabled {{
    color: {spec.text_disabled};
    border-color: {spec.border_deep};
}}

QProgressBar::chunk:disabled {{
    background-color: {spec.border_deep};
}}

/* --- Item Tooltip --- */
QToolTip {{
    background-color: {spec.tooltip_bg};
    color: {spec.text_primary};
    border: 1px solid {spec.accent_brass};
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
    color: {spec.text_primary};
    min-height: {_touch(30, scale)};
}}

QCheckBox::indicator, QRadioButton::indicator {{
    width: {_indicator_px(scale)}px;
    height: {_indicator_px(scale)}px;
    border: 1px solid {spec.text_muted};
    background-color: {spec.bg_input};
    border-radius: 3px;
}}

/* Round (T193): the radius is half the box the border is drawn on -- the
   indicator plus its two 1px borders -- so the radio is a disc, never the
   rounded square `11 * scale` made of it. */
QRadioButton::indicator {{
    border-radius: {(_indicator_px(scale) + 2) // 2}px;
}}

QCheckBox::indicator:hover, QRadioButton::indicator:hover {{
    border-color: {spec.accent_bright};
}}

QCheckBox::indicator:focus, QRadioButton::indicator:focus {{
    border-color: {spec.accent_bright};
}}

QCheckBox::indicator:checked, QRadioButton::indicator:checked {{
    background-color: {spec.accent_bright};
    border-color: {spec.accent_light};
}}

/* T193: a ticked box shows a tick, not only an ice-blue fill. */
QCheckBox::indicator:checked {{
    image: {_image("check.svg", spec)};
}}

/* A checked radio is a blue dot in the dark well, so it never reads as a
   ticked checkbox beside it. Its blue rim alone still tells it from an
   unchecked one (muted rim) if the dot's file cannot load. */
QRadioButton::indicator:checked {{
    background-color: {spec.bg_input};
    border-color: {spec.accent_bright};
    image: {_image("radio-dot.svg", spec)};
}}

QCheckBox::indicator:disabled, QRadioButton::indicator:disabled {{
    border-color: {spec.border_deep};
    background-color: {spec.bg_container};
}}

/* A disabled ticked box kept looking unticked -- the uninstall's "Keep my
   characters" while a job runs (T193): `:disabled` and `:checked` weigh the
   same, so the later `:disabled` wiped the checked look. These two pseudo-
   classes outweigh either alone, so they win wherever they sit in the sheet.
   A muted tick or dot on the disabled well, and a muted rim against the
   unticked box's deep one, so the two still differ if the image cannot load. */
QCheckBox::indicator:checked:disabled {{
    border-color: {spec.text_muted};
    image: {_image("check-disabled.svg", spec)};
}}

QRadioButton::indicator:checked:disabled {{
    border-color: {spec.text_muted};
    image: {_image("radio-dot-disabled.svg", spec)};
}}

QCheckBox:disabled, QRadioButton:disabled {{
    color: {spec.text_disabled};
}}

/* --- Menus & Context Menus --- */
QMenuBar {{
    background-color: {spec.bg_dark};
    border-bottom: 1px solid {spec.border_dark};
    color: {spec.text_primary};
}}

QMenuBar::item:selected {{
    background-color: {spec.bg_hover};
    color: {spec.accent_bright};
}}

QMenu {{
    background-color: {spec.bg_container};
    border: 1px solid {spec.accent_brass};
    border-radius: 3px;
    padding: 4px;
    color: {spec.text_primary};
}}

QMenu::item {{
    padding: 8px 22px 8px 14px;
    border-radius: 2px;
    font-family: {FONT_FAMILY_BODY};
    font-size: {_px(13, scale)};
    color: {spec.text_primary};
    min-height: {_touch(28, scale)};
}}

QMenu::item:selected {{
    background-color: {spec.bg_hover};
    color: {spec.accent_bright};
}}

QMenu::item:disabled {{
    color: {spec.text_disabled};
    background-color: transparent;
}}

QMenu::separator {{
    height: 1px;
    background: {spec.border_deep};
    margin: 4px 6px;
}}

/* --- Labels & Status Badges --- */
QLabel#updateBanner {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light};
    border: 1px solid {spec.accent_bright};
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
    background-color: {spec.bg_input};
    border: 1px solid {spec.border_dark};
    border-radius: 3px;
}}

QLabel#log-strip-label {{
    font-family: {FONT_FAMILY_MONO};
    font-size: {_px(12, scale)};
    color: {spec.text_muted};
}}

QLabel#sidebar-key-hint {{
    color: {spec.text_muted};
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
    color: {spec.tile_subtitle};
    font-weight: bold;
    background: transparent;
}}

QFrame[objectName^="catalog-tile-"] QPushButton {{
    background-color: {spec.bg_panel};
    color: {spec.text_primary};
    border: 1px solid {spec.border_dark};
}}

QFrame[objectName^="catalog-tile-"] QPushButton:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_light if spec.is_light else spec.tile_subtitle};
    border: 1px solid {spec.accent_brass};
}}

QFrame[objectName^="catalog-tile-"] QPushButton:focus {{
    border: 1px solid {spec.accent_bright};
    color: {spec.accent_light};
}}

QFrame[objectName^="catalog-tile-"] QPushButton:disabled {{
    background-color: {spec.bg_container};
    color: {spec.text_disabled};
    border: 1px solid {spec.border_deep};
}}

QFrame[objectName^="catalog-tile-"] QPushButton[primary="true"],
QFrame[objectName^="catalog-tile-"] QPushButton[objectName^="install-"] {{
    background-color: {spec.accent_border};
    color: {spec.primary_btn_text};
    border: 1px solid {spec.accent_border};
}}

QFrame[objectName^="catalog-tile-"] QPushButton[primary="true"]:hover,
QFrame[objectName^="catalog-tile-"] QPushButton[objectName^="install-"]:hover {{
    background-color: {spec.accent_bright};
    color: {spec.primary_btn_text};
    border: 1px solid {spec.accent_light};
}}

QFrame[objectName^="catalog-tile-"] QPushButton[primary="true"]:focus,
QFrame[objectName^="catalog-tile-"] QPushButton[objectName^="install-"]:focus {{
    border: 1px solid {spec.accent_light};
}}

QFrame[objectName^="catalog-tile-"] QPushButton[primary="true"]:disabled,
QFrame[objectName^="catalog-tile-"] QPushButton[objectName^="install-"]:disabled {{
    background-color: {spec.bg_hover};
    color: {spec.primary_btn_disabled_text};
    border: 1px solid {spec.border_deep};
}}

/* --- Stage 1 Boxart Cards & Stage 2 Back Button (Project Kael Phase K2) --- */
QPushButton[objectName^="boxart-card-"] {{
    background: transparent;
    border: none;
    padding: 0px;
}}

QPushButton#catalog-back-button {{
    background-color: {spec.bg_panel};
    color: {spec.text_primary};
    border: 1px solid {spec.accent_brass};
    border-radius: 3px;
    min-height: {_touch(34, scale)};
    padding: 6px 14px;
    font-weight: bold;
    font-size: {_px(13, scale)};
}}

QPushButton#catalog-back-button:hover {{
    background-color: {spec.bg_hover};
    color: {spec.accent_bright};
    border: 1px solid {spec.accent_bright};
}}

QPushButton#catalog-back-button:focus {{
    border: 2px solid {spec.accent_bright};
}}

QLabel#catalog-group-title {{
    color: {spec.accent_light};
    font-family: {FONT_FAMILY_TITLE};
    font-size: {_px(16, scale)};
    font-weight: bold;
    padding: 0px 6px;
    background: transparent;
}}

/* ScrollAreas default to QPalette.Base unless styled; make them and their inner body
   match the surrounding container/dialog sheet so embedded scroll areas in dialogs
   (`FittedMessageBox`, `ManifestPromptDialog`) never paint a dark `QPalette.Window`
   rectangle over a light theme. */
QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollArea > QWidget#qt_scrollarea_viewport {{
    background-color: transparent;
}}

QScrollArea > QWidget#qt_scrollarea_viewport > QWidget {{
    background-color: transparent;
}}
"""


DADCRAFT_THEME_QSS = _build_qss(1.0)
"""The theme at scale 1.0 (the reference width). Tests assert against this;
`apply_dadcraft_theme` re-generates it per window size at runtime."""


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
