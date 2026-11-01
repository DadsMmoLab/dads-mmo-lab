"""Dadcraft Frost — the slim, elegant Yu'lon UI theme (PySide6).

The brief is a *toned-down* reimagining of the classic Dadcraft motifs: heavy
stone, parchment and beveled filigree are abstracted into flat blue-slate sheets
with ice-blue trim and micro-borders. Nothing here is ornate; the theme reads as
a modern utility interface that merely remembers where it came from. The tokens
are:

- Deep blue-black sheets (`#0B111B` window, `#162338` panel, `#080E17` input),
  deliberately cool-tinted — not neutral gray — so the chrome harmonises with
  the icy header and frost-blue details.
- A single clear ice-blue accent (`#76D5FF`) with two restrained derivatives
  (`#318BB4` hairline, `#225771` deep).
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

from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QWidget,
)

from yulon.log import get_logger
from yulon.ui.theme_qss import (
    CHECK_UPDATES_BUTTON,
    DADCRAFT_THEME_QSS,
    FONT_FAMILY_BODY,
    FONT_FAMILY_MONO,
    FONT_FAMILY_TITLE,
    FONT_TITLE_SINGLE,
    FORGET_TAB_BUTTON,
    LAUNCH_TAB_BUTTON,
    LAUNCHER_PLAY_BUTTON,
    MIN_FONT_PX,
    PLAY_MENU_BUTTON,
    QUESTION_PARAGRAPH,
    REFERENCE_WIDTH,
    SERVER_BUILD_BUTTON,
    SERVERS_MENU_BUTTON,
    SETTINGS_BUTTON,
    SIDEBAR_PIN_BUTTON,
    SIDEBAR_PIN_GAP,
    SIDEBAR_RAIL_WIDTH,
    TAB_BUTTONS,
    TOP_MENU_BAR,
    TOP_MENU_BUTTON,
    _build_qss,
    _image,
    _indicator_px,
    _px,
    _scroller_width,
    _spin_button_px,
    _theme_images_dir_for,
    _touch,
    scale_for_width,
)
from yulon.ui.theme_spec import (
    BUNDLED_THEME_ORDER,
    BUNDLED_THEMES,
    COLOR_ARTIFACT,
    COLOR_BG_CONTAINER,
    COLOR_BG_DARK,
    COLOR_BG_INPUT,
    COLOR_BG_PANEL,
    COLOR_BG_PARCHMENT,
    COLOR_BG_PARCHMENT_LIGHT,
    COLOR_BRASS_DARK,
    COLOR_BRASS_DEEP,
    COLOR_COMMON,
    COLOR_DANGER,
    COLOR_EMBER,
    COLOR_EPIC,
    COLOR_GOLD_BORDER,
    COLOR_GOLD_BRASS,
    COLOR_GOLD_BRIGHT,
    COLOR_GOLD_LIGHT,
    COLOR_LEGENDARY,
    COLOR_RARE,
    COLOR_TEXT_GOLD,
    COLOR_TEXT_MUTED,
    COLOR_TEXT_PRIMARY,
    COLOR_TEXT_WARNING,
    COLOR_UNCOMMON,
    DEFAULT_THEME,
    OPEN_THEMES_FOLDER_BUTTON,
    THEME_ALIASES,
    THEME_CHOICES,
    THEME_COMBO,
    THEMES,
    ThemeSpec,
    _load_bundled_themes,
    available_theme_choices,
    get_theme,
    load_available_themes,
    load_theme_file,
    normalize_theme_name,
    parse_theme_dict,
    sync_user_themes,
    user_themes_dir,
)
from yulon.ui.theme_widgets import (
    _SPIN_GUARD_PROPERTY,
    _SPIN_NUMBER_GAP_PX,
    TOUCH_TARGET_PX,
    _ensure_spin_watcher,
    _guard_spin_numbers,
    _SpinBoxWatcher,
    _SpinNumberGuard,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QPalette

__all__ = [
    "BUNDLED_THEMES",
    "BUNDLED_THEME_ORDER",
    "CHECK_UPDATES_BUTTON",
    "COLOR_ARTIFACT",
    "COLOR_BG_CONTAINER",
    "COLOR_BG_DARK",
    "COLOR_BG_INPUT",
    "COLOR_BG_PANEL",
    "COLOR_BG_PARCHMENT",
    "COLOR_BG_PARCHMENT_LIGHT",
    "COLOR_BRASS_DARK",
    "COLOR_BRASS_DEEP",
    "COLOR_COMMON",
    "COLOR_DANGER",
    "COLOR_EMBER",
    "COLOR_EPIC",
    "COLOR_GOLD_BORDER",
    "COLOR_GOLD_BRASS",
    "COLOR_GOLD_BRIGHT",
    "COLOR_GOLD_LIGHT",
    "COLOR_LEGENDARY",
    "COLOR_RARE",
    "COLOR_TEXT_GOLD",
    "COLOR_TEXT_MUTED",
    "COLOR_TEXT_PRIMARY",
    "COLOR_TEXT_WARNING",
    "COLOR_UNCOMMON",
    "DADCRAFT_THEME_QSS",
    "DEFAULT_THEME",
    "FONT_FAMILY_BODY",
    "FONT_FAMILY_MONO",
    "FONT_FAMILY_TITLE",
    "FONT_TITLE_SINGLE",
    "FORGET_TAB_BUTTON",
    "LAUNCHER_PLAY_BUTTON",
    "LAUNCH_TAB_BUTTON",
    "MIN_FONT_PX",
    "OPEN_THEMES_FOLDER_BUTTON",
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
    "THEMES",
    "THEME_ALIASES",
    "THEME_CHOICES",
    "THEME_COMBO",
    "TOP_MENU_BAR",
    "TOP_MENU_BUTTON",
    "TOUCH_TARGET_PX",
    "ThemeSpec",
    "_SPIN_GUARD_PROPERTY",
    "_SPIN_NUMBER_GAP_PX",
    "_SpinBoxWatcher",
    "_SpinNumberGuard",
    "_build_qss",
    "_ensure_spin_watcher",
    "_guard_spin_numbers",
    "_image",
    "_indicator_px",
    "_load_bundled_themes",
    "_px",
    "_scroller_width",
    "_spin_button_px",
    "_theme_images_dir_for",
    "_touch",
    "apply_dadcraft_theme",
    "available_theme_choices",
    "build_dadcraft_palette",
    "get_theme",
    "load_available_themes",
    "load_theme_file",
    "normalize_theme_name",
    "parse_theme_dict",
    "resolve_widget_scale",
    "resolve_widget_theme",
    "scale_for_width",
    "sync_user_themes",
    "user_themes_dir",
]

logger = get_logger(__name__)


def build_dadcraft_palette(theme_name: str | None = None) -> QPalette:
    """Build a QPalette matching the given theme (defaults to Arthas)."""
    from PySide6.QtGui import QColor, QPalette

    spec = get_theme(theme_name)
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(spec.bg_dark))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(spec.text_primary))
    palette.setColor(QPalette.ColorRole.Base, QColor(spec.bg_input))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(spec.bg_container))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(spec.bg_container))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(spec.text_primary))
    palette.setColor(QPalette.ColorRole.Text, QColor(spec.text_primary))
    palette.setColor(QPalette.ColorRole.Button, QColor(spec.bg_panel))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(spec.text_primary))
    palette.setColor(QPalette.ColorRole.BrightText, QColor(spec.accent_bright))
    # The ice-blue accent (T193): a link is the theme's accent like every
    # other thing that can be pressed, and visited is the same, since a GitHub
    # page opened once is no less worth opening again.
    palette.setColor(QPalette.ColorRole.Link, QColor(spec.accent_light))
    palette.setColor(QPalette.ColorRole.LinkVisited, QColor(spec.accent_light))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(spec.accent_brass))
    palette.setColor(
        QPalette.ColorRole.HighlightedText,
        QColor(spec.primary_btn_text if spec.is_light else spec.text_primary),
    )
    # Placeholder text (a QLineEdit hint) would otherwise fall back to a
    # near-black default that is invisible on the dark input background.
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(spec.text_muted))
    return palette


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


def resolve_widget_theme(widget: QWidget | None) -> str:
    """Return the theme ID recorded on `widget` or its top-level window, else `DEFAULT_THEME`."""
    node = widget
    while node is not None:
        prop = node.property("yulon_theme")
        if isinstance(prop, str) and prop.strip():
            return normalize_theme_name(prop)
        node = node.parentWidget()
    return DEFAULT_THEME


def resolve_widget_scale(widget: QWidget | None) -> float:
    """Return the responsive font scale recorded on `widget` or its window, else `1.0`."""
    node = widget
    while node is not None:
        prop = node.property("yulon_scale")
        if isinstance(prop, (int, float)) and not isinstance(prop, bool) and prop > 0:
            return float(prop)
        node = node.parentWidget()
    return 1.0


def apply_dadcraft_theme(
    target: QApplication | QWidget,
    *,
    width: int | None = None,
    theme_name: str | None = None,
) -> None:
    """Apply the Dadcraft theme stylesheet and palette to a QApplication or QWidget.

    `width` re-generates the stylesheet at a font scale derived from the window
    width (`scale_for_width`), so text grows/shrinks with the window. Omit it
    (or pass None) for the reference scale 1.0.
    `theme_name` selects one of `THEMES` (defaults to the target's recorded
    theme or `"arthas"`).

    Every spin box under `target` also gets a number guard (T821): the guard
    caps the number field from the buttons' live rects so the number can
    never slide under a button, whatever the Qt build's font metrics are.
    """
    _ensure_fusion_style()

    resolved_theme: str
    if theme_name is not None:
        resolved_theme = normalize_theme_name(theme_name)
        if isinstance(target, QWidget):
            target.setProperty("yulon_theme", resolved_theme)
    elif isinstance(target, QWidget):
        resolved_theme = resolve_widget_theme(target)
    else:
        resolved_theme = DEFAULT_THEME

    scale = scale_for_width(width) if width is not None else 1.0
    if isinstance(target, QWidget):
        target.setProperty("yulon_scale", scale)

    palette = build_dadcraft_palette(resolved_theme)
    qss = _build_qss(
        scale,
        theme_name=resolved_theme,
    )
    if isinstance(target, QApplication):
        target.setPalette(palette)
        target.setStyleSheet(qss)
        _ensure_spin_watcher(target)
        for widget in target.allWidgets():
            if isinstance(widget, QAbstractSpinBox):
                _guard_spin_numbers(widget)
    else:
        target.setPalette(palette)
        target.setStyleSheet(qss)
        _guard_spin_numbers(target)
        header = target.property("header")
        if header is not None and hasattr(header, "set_theme"):
            header.set_theme(resolved_theme, scale=scale)
        if type(target).__name__ != "LauncherWindow" and hasattr(target, "apply_theme"):
            target.apply_theme(resolved_theme)
        from yulon.ui.widgets.panel_style import panel_qss

        for card in target.findChildren(QWidget):
            if card is target:
                continue
            card_type = type(card).__name__
            if hasattr(card, "set_theme") and card_type == "DadcraftRealmBadge":
                card.set_theme(resolved_theme, scale=scale)
            elif card_type in ("ModulesPanel", "TuningPanel"):
                card.setStyleSheet(panel_qss(resolved_theme))
            elif card_type in ("CatalogView", "TopMenuBar", "SingleServerView", "SettingsView"):
                if hasattr(card, "apply_theme"):
                    card.apply_theme(resolved_theme)
            elif card.objectName().startswith(("catalog-tile-", "boxart-card-")):
                if hasattr(card, "apply_theme"):
                    card.apply_theme(resolved_theme)
                else:
                    card.update()
