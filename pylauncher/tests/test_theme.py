"""Tests for the Warcraft & WoW UI theme and custom decorative widgets."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMainWindow, QTabWidget, QWidget

import main
from yulon.catalog.catalog import load_catalog
from yulon.ui.catalog_view import CatalogView
from yulon.ui.icons import get_tab_icon, warcraft_icon
from yulon.ui.theme import (
    COLOR_BG_DARK,
    COLOR_BG_PARCHMENT,
    COLOR_GOLD_BRIGHT,
    COLOR_TEXT_MUTED,
    WARCRAFT_THEME_QSS,
    apply_warcraft_theme,
    build_warcraft_palette,
)
from yulon.ui.widgets.log_panel import LogPanel
from yulon.ui.widgets.warcraft_decorations import (
    WarcraftHeader,
    WarcraftRealmBadge,
    format_warcraft_tooltip,
)


def test_warcraft_palette_construction() -> None:
    palette = build_warcraft_palette()
    assert isinstance(palette, QPalette)
    assert palette.color(QPalette.ColorRole.Window).name().lower() == COLOR_BG_DARK.lower()
    assert palette.color(QPalette.ColorRole.Button).name().lower() == COLOR_BG_PARCHMENT.lower()


def test_warcraft_theme_qss_covers_essential_controls() -> None:
    assert "QMainWindow" in WARCRAFT_THEME_QSS
    assert "QTabWidget" in WARCRAFT_THEME_QSS
    assert "QTabBar::tab" in WARCRAFT_THEME_QSS
    assert "QTabWidget#sidebar-tabs QTabBar::tab" in WARCRAFT_THEME_QSS
    assert "QMenu" in WARCRAFT_THEME_QSS
    assert "QMenu::item" in WARCRAFT_THEME_QSS
    assert "QPushButton" in WARCRAFT_THEME_QSS
    assert "QGroupBox" in WARCRAFT_THEME_QSS
    assert "QLineEdit" in WARCRAFT_THEME_QSS
    assert "QListWidget" in WARCRAFT_THEME_QSS
    assert "QProgressBar" in WARCRAFT_THEME_QSS
    assert "QToolTip" in WARCRAFT_THEME_QSS
    assert "QLabel#tile-title" in WARCRAFT_THEME_QSS
    assert "QLabel#tile-desc" in WARCRAFT_THEME_QSS
    assert "QLabel#tile-meta" in WARCRAFT_THEME_QSS
    assert "QLabel#tile-warning" in WARCRAFT_THEME_QSS
    assert "QTabBar QToolButton" in WARCRAFT_THEME_QSS


def test_input_controls_carry_explicit_minimum_sizes() -> None:
    # The shared input block must give single-line controls a real height floor
    # (so the caret/selection are never clipped by the padding) and multi-line
    # panels a taller one (so a log/report box reads as a panel, not a stray
    # line). Guarded as QSS text because the floor is set in the theme, not in
    # per-widget Python.
    assert "min-height: 20px" in WARCRAFT_THEME_QSS
    assert "min-width: 60px" in WARCRAFT_THEME_QSS
    assert "min-height: 90px" in WARCRAFT_THEME_QSS


def test_the_pressed_button_state_does_not_shift_padding() -> None:
    # A pressed button's sunken look must come from the fill and border shading
    # only — the old asymmetric `padding-top: 9px; padding-left: 17px` nudged
    # the content and broke the shared border edge with its neighbours. The
    # pressed rule must declare no padding property at all (a prose "padding"
    # in the explanatory comment is fine; a `padding-` declaration is not).
    pressed = WARCRAFT_THEME_QSS.split("QPushButton:pressed")[1].split("}")[0]
    assert "padding-top:" not in pressed
    assert "padding-left:" not in pressed
    assert "padding-bottom:" not in pressed
    assert "padding-right:" not in pressed
    assert "padding:" not in pressed


def test_the_button_base_state_draws_a_visible_right_and_bottom_border() -> None:
    # The base QPushButton rule used COLOR_BRASS_DEEP (#3C2D14) for the right
    # and bottom edges — a dark brown nearly identical to the button's own
    # fill, so those two borders read as MISSING. All four edges must use a
    # colour that is visibly distinct from the fill (the brass, and the gold
    # on the lit top/left).
    base = WARCRAFT_THEME_QSS.split("QPushButton {")[1].split("}")[0]
    # The f-string has already interpolated the color constants, so assert the
    # resolved hex: right/bottom must be the visible brass, not the near-black
    # deep brown.
    assert "border-right: 2px solid #785A28;" in base
    assert "border-bottom: 2px solid #785A28;" in base
    assert "border-right: 2px solid #3C2D14;" not in base
    assert "border-bottom: 2px solid #3C2D14;" not in base


def test_the_tab_base_state_draws_a_visible_right_and_bottom_border() -> None:
    # The base `QTabBar::tab` (top bar) must draw visible brass borders on
    # right and bottom, not the near-black #3C2D14 (which blended into the dark
    # background and read as missing right-hand borders).
    tab_rule = WARCRAFT_THEME_QSS.split("QTabBar::tab {")[1].split("}")[0]
    assert "border-right: 2px solid #785A28;" in tab_rule
    assert "border-bottom: 2px solid #785A28;" in tab_rule
    assert "#3C2D14" not in tab_rule


def test_the_sidebar_tab_is_bounded_to_a_narrow_rail() -> None:
    # The West sidebar (objectName "sidebar-tabs") reads as an icon-first rail:
    # `max-width` caps it near the icon-plus-padding width, `min-width` keeps it
    # from collapsing, and `border-right: none` lets it merge into the pane.
    west = WARCRAFT_THEME_QSS.split("QTabWidget#sidebar-tabs QTabBar::tab {")[1].split("}")[0]
    assert "max-width: 60px" in west
    assert "min-width: 48px" in west
    assert "border-right: none;" in west


def test_the_tab_text_font_is_on_the_widget_not_the_subcontrol() -> None:
    # `QTabBar::tab { font-family: ... }` does not reach the painted tab text
    # (the `::tab` sub-control ignores font properties — measured), so the title
    # font lives on the `QTabBar` widget rule, where it does reach the text.
    assert "QTabBar {" in WARCRAFT_THEME_QSS
    # The single-family fallback is what makes the serif actually apply; the
    # comma-separated stack broke on the tab rule and left a thin default font.
    from yulon.ui.theme import FONT_TITLE_SINGLE

    assert "," not in FONT_TITLE_SINGLE


def test_the_theme_scales_font_sizes_with_window_width() -> None:
    # The theme is generated per window width: a narrower window shrinks the
    # base font sizes and a wider one grows them, so text stays in proportion
    # to the controls instead of clipping or sprawling.
    from yulon.ui.theme import REFERENCE_WIDTH, _build_qss, scale_for_width

    assert "font-size: 13px" in _build_qss(1.0)
    assert "font-size: 10px" in _build_qss(scale_for_width(960))
    assert scale_for_width(REFERENCE_WIDTH) == 1.0
    assert scale_for_width(960) < 1.0 < scale_for_width(1600)


def test_muted_text_color_is_lightened_for_legibility() -> None:
    # Regression guard: the original #8A8275 measured too low-contrast against
    # the tab/list backgrounds it sits on; it must stay lighter than that.
    assert COLOR_TEXT_MUTED.upper() != "#8A8275"


def test_apply_warcraft_theme_on_widget(qapp: QApplication) -> None:
    widget = QWidget()
    apply_warcraft_theme(widget)
    assert widget.styleSheet() == WARCRAFT_THEME_QSS


def test_apply_warcraft_theme_on_qapp(qapp: QApplication) -> None:
    apply_warcraft_theme(qapp)
    assert qapp.styleSheet() == WARCRAFT_THEME_QSS


def test_warcraft_realm_badge(qapp: QApplication) -> None:
    badge = WarcraftRealmBadge("online")
    assert "ONLINE" in badge._label.text()
    badge.set_status("starting")
    assert "STARTING" in badge._label.text()
    badge.set_status("restarting")
    assert "RESTARTING" in badge._label.text()
    badge.set_status("stopped")
    assert "OFFLINE" in badge._label.text()


def test_warcraft_header(qapp: QApplication) -> None:
    header = WarcraftHeader("TEST REALM", "Subtitle")
    assert header is not None
    header.set_realm_status("running")
    assert "ONLINE" in header._badge._label.text()


def test_format_warcraft_tooltip() -> None:
    tooltip_html = format_warcraft_tooltip(
        "Thunderfury, Blessed Blade",
        ["Binds when picked up", "Speed 1.90"],
        quality="legendary",
        flavor_text="Did someone say...?",
        item_level=80,
    )
    assert "Thunderfury" in tooltip_html
    assert "Did someone say" in tooltip_html
    assert "Server Tier / Build: 80" in tooltip_html


def test_build_catalog_tab_sets_west_tab_position(qapp: QApplication) -> None:
    window = QMainWindow()
    catalog = load_catalog()
    panel = LogPanel()
    view = CatalogView(catalog, lambda _e: cast(Any, None), panel)
    tabs, _banner, _splitter = main.build_catalog_tab(window, view, panel)
    assert tabs.tabPosition() == QTabWidget.TabPosition.West


def test_catalog_tile_has_custom_context_menu_policy(qapp: QApplication) -> None:
    catalog = load_catalog()
    panel = LogPanel()
    view = CatalogView(catalog, lambda _e: cast(Any, None), panel)
    frames = view.findChildren(QFrame)
    custom_frames = [
        f for f in frames if f.contextMenuPolicy() == Qt.ContextMenuPolicy.CustomContextMenu
    ]
    assert len(custom_frames) >= len(catalog.games)


def test_catalog_tile_text_uses_a_readable_role_hierarchy(qapp: QApplication) -> None:
    catalog = load_catalog()
    panel = LogPanel()
    view = CatalogView(catalog, lambda _e: cast(Any, None), panel)
    labels = view.findChildren(QLabel)
    roles = {label.objectName() for label in labels if label.objectName()}
    assert "tile-title" in roles
    assert "tile-desc" in roles
    assert "tile-meta" in roles


def test_warcraft_icon_renders_a_non_null_pixmap(qapp: QApplication) -> None:
    icon = warcraft_icon("server", color=COLOR_GOLD_BRIGHT, size=16)
    assert not icon.isNull()
    pixmap = icon.pixmap(16, 16)
    assert pixmap.width() == 16
    assert pixmap.height() == 16


def test_warcraft_icon_returns_empty_icon_for_unknown_name(qapp: QApplication) -> None:
    icon = warcraft_icon("no-such-icon-name")
    assert icon.isNull()


def test_get_tab_icon_matches_known_tab_names(qapp: QApplication) -> None:
    for name in (
        "Catalog",
        "Server",
        "Console",
        "Accounts",
        "Characters",
        "Bots",
        "Maintenance",
        "Modules",
        "Networking",
    ):
        icon = get_tab_icon(name)
        assert not icon.isNull(), f"{name} produced a null icon"


def test_get_tab_icon_falls_back_to_server_for_unknown_names(qapp: QApplication) -> None:
    icon = get_tab_icon("something-unrelated")
    assert not icon.isNull()
