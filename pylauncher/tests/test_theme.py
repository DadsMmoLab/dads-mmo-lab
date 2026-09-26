"""Tests for the Dadcraft & WoW UI theme and custom decorative widgets."""

from __future__ import annotations

from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QMainWindow, QTabWidget, QWidget

import main
from yulon.catalog.catalog import load_catalog
from yulon.ui.catalog_view import CatalogView
from yulon.ui.icons import dadcraft_icon, get_app_icon, get_tab_icon
from yulon.ui.theme import (
    COLOR_BG_DARK,
    COLOR_BG_PANEL,
    COLOR_GOLD_BRIGHT,
    COLOR_TEXT_MUTED,
    DADCRAFT_THEME_QSS,
    apply_dadcraft_theme,
    build_dadcraft_palette,
)
from yulon.ui.widgets.dadcraft_decorations import (
    DadcraftCampaignCard,
    DadcraftHeader,
    DadcraftRealmBadge,
    format_dadcraft_tooltip,
)
from yulon.ui.widgets.log_panel import LogPanel


def test_dadcraft_palette_construction() -> None:
    palette = build_dadcraft_palette()
    assert isinstance(palette, QPalette)
    assert palette.color(QPalette.ColorRole.Window).name().lower() == COLOR_BG_DARK.lower()
    assert palette.color(QPalette.ColorRole.Button).name().lower() == COLOR_BG_PANEL.lower()


def test_dadcraft_theme_qss_covers_essential_controls() -> None:
    assert "QMainWindow" in DADCRAFT_THEME_QSS
    assert "QTabWidget" in DADCRAFT_THEME_QSS
    assert "QTabBar::tab" in DADCRAFT_THEME_QSS
    assert "QTabWidget#sidebar-tabs QTabBar::tab" in DADCRAFT_THEME_QSS
    assert "QMenu" in DADCRAFT_THEME_QSS
    assert "QMenu::item" in DADCRAFT_THEME_QSS
    assert "QPushButton" in DADCRAFT_THEME_QSS
    assert "QGroupBox" in DADCRAFT_THEME_QSS
    assert "QLineEdit" in DADCRAFT_THEME_QSS
    assert "QListWidget" in DADCRAFT_THEME_QSS
    assert "QProgressBar" in DADCRAFT_THEME_QSS
    assert "QToolTip" in DADCRAFT_THEME_QSS
    assert "QLabel#tile-title" in DADCRAFT_THEME_QSS
    assert "QLabel#tile-desc" in DADCRAFT_THEME_QSS
    assert "QLabel#tile-meta" in DADCRAFT_THEME_QSS
    assert "QLabel#tile-warning" in DADCRAFT_THEME_QSS
    assert "QTabBar QToolButton" in DADCRAFT_THEME_QSS


def test_input_controls_carry_explicit_minimum_sizes() -> None:
    # The shared input block must give single-line controls a real height floor
    # (so the caret/selection are never clipped by the padding, and a thumb can
    # hit them) and multi-line panels a taller one (so a log/report box reads as
    # a panel, not a stray line). Guarded as QSS text because the floor is set in
    # the theme, not in per-widget Python.
    assert "min-height: 90px" in DADCRAFT_THEME_QSS
    from yulon.ui.theme import TOUCH_TARGET_PX, _touch

    smallest = int(_touch(30, 1.0).rstrip("px"))
    assert smallest >= TOUCH_TARGET_PX


def test_the_pressed_button_state_does_not_shift_padding() -> None:
    # A pressed button's sunken look must come from the fill and border shading
    # only — an asymmetric `padding-top`/`padding-left` nudges the content and
    # breaks the shared border edge with its neighbours. The pressed rule must
    # declare no padding property at all (a prose "padding" in the explanatory
    # comment is fine; a `padding-` declaration is not).
    pressed = DADCRAFT_THEME_QSS.split("QPushButton:pressed")[1].split("}")[0]
    assert "padding-top:" not in pressed
    assert "padding-left:" not in pressed
    assert "padding-bottom:" not in pressed
    assert "padding-right:" not in pressed
    assert "padding:" not in pressed


def test_interactive_controls_declare_a_touch_target_floor() -> None:
    # Every interactive primitive must carry a short-side floor at or above
    # TOUCH_TARGET_PX, because the launcher ships on a 1280x800 handheld driven
    # by thumb and D-pad. This is the regression guard for the 16px checkbox and
    # the icon-sized tab scroll arrows that made the destructive uninstall
    # checkbox effectively unhittable.
    from yulon.ui.theme import MIN_FONT_PX, TOUCH_TARGET_PX, _build_qss

    qss = _build_qss(1.0)

    import re

    def floor_of(rule_start: str) -> int:
        body = qss.split(rule_start)[1].split("}")[0]
        match = re.search(r"min-(?:height|width):\s*(\d+)px", body)
        if match is None:
            raise AssertionError(f"no min-height/min-width in rule {rule_start!r}")
        return int(match.group(1))

    assert floor_of("QPushButton {") >= TOUCH_TARGET_PX
    assert floor_of("QLineEdit, QSpinBox, QComboBox {") >= TOUCH_TARGET_PX
    assert floor_of("QListWidget::item, QTableWidget::item, QTreeWidget::item {") >= TOUCH_TARGET_PX
    assert floor_of("QTabBar QToolButton {") >= TOUCH_TARGET_PX
    # The font floor keeps body text legible even where the width scale clamps.
    assert MIN_FONT_PX >= 12


def test_the_tab_x_is_exempt_by_id_and_the_floor_rule_is_untouched() -> None:
    """T95: the × is the one tab-bar tool button below the floor.

    Exempt by objectName, never by negation.
    """
    from yulon.ui.theme import FORGET_TAB_BUTTON, _build_qss, _touch

    qss = _build_qss(1.0)
    rule = qss.split(f"QTabBar QToolButton#{FORGET_TAB_BUTTON} {{")[1].split("}")[0]
    assert "max-width: 18px" in rule and "max-height: 18px" in rule
    floor = qss.split("QTabBar QToolButton {")[1].split("}")[0]
    assert f"min-width: {_touch(30, 1.0)};" in floor, "the scroll arrows lost their floor"


def test_the_button_base_state_draws_a_visible_hairline() -> None:
    # The base QPushButton rule must pair its panel fill with a hairline that is
    # visibly distinct from the fill — the near-black `#3C2D14` it once used on
    # two edges read as a missing border. Asserted against the constant so the
    # check follows the palette instead of pinning one hex value by hand.
    from yulon.ui.theme import COLOR_BRASS_DARK

    base = DADCRAFT_THEME_QSS.split("QPushButton {")[1].split("}")[0]
    assert f"border: 1px solid {COLOR_BRASS_DARK};" in base
    assert "#3C2D14" not in base


def test_the_tab_base_state_draws_a_visible_hairline() -> None:
    from yulon.ui.theme import COLOR_BRASS_DARK

    tab_rule = DADCRAFT_THEME_QSS.split("QTabBar::tab, QTabBar::tab:top {")[1].split("}")[0]
    assert f"border: 1px solid {COLOR_BRASS_DARK};" in tab_rule


def test_the_sidebar_tab_is_bounded_to_a_narrow_rail() -> None:
    # The West sidebar (objectName "sidebar-tabs") reads as an icon-first rail:
    # `max-width` caps it near the icon-plus-padding width, `min-width` keeps it
    # from collapsing, and it carries a real touch-target height.
    west = DADCRAFT_THEME_QSS.split("QTabWidget#sidebar-tabs QTabBar::tab {")[1].split("}")[0]
    assert "max-width: 64px" in west
    assert "min-width: 48px" in west
    assert "min-height: 44px" in west


def test_the_tab_text_font_is_on_the_widget_not_the_subcontrol() -> None:
    # `QTabBar::tab { font-family: ... }` does not reach the painted tab text
    # (the `::tab` sub-control ignores font properties — measured), so the title
    # font lives on the `QTabBar` widget rule, where it does reach the text.
    assert "QTabBar {" in DADCRAFT_THEME_QSS
    from yulon.ui.theme import FONT_TITLE_SINGLE

    assert "," not in FONT_TITLE_SINGLE


def test_the_theme_scales_font_sizes_with_window_width() -> None:
    # The theme is generated per window width: a narrower window shrinks the
    # base font sizes so text stays in proportion to the controls instead of
    # clipping. The rendered-pixel floor (MIN_FONT_PX) means the narrow end no
    # longer collapses into 10px text.
    #
    # T45: it scales DOWN and never up. This line used to read
    # `scale_for_width(960) < 1.0 < scale_for_width(1600)` and asserted the
    # opposite — that a wider window grows the text. Every base in this module is
    # authored at REFERENCE_WIDTH, so growing past it renders a size nobody
    # chose: a maximised 1920 desktop sat at min(1.4, 1.5) and drew every label,
    # button, tab and input 40% over its authored size at once.
    from yulon.ui.theme import MIN_FONT_PX, REFERENCE_WIDTH, _build_qss, scale_for_width

    assert "font-size: 14px" in _build_qss(1.0)
    assert scale_for_width(REFERENCE_WIDTH) == 1.0
    assert scale_for_width(960) < 1.0
    for wider in (1600, 1920, 2560, 3840):
        assert scale_for_width(wider) == 1.0, f"{wider}px grew past the authored size"

    # No generated font-size may fall below the legibility floor, at any scale
    # the app can actually be resized to (960 is the window's minimum width).
    import re

    for width in (960, 1024, 1280, 1600, 2560):
        qss = _build_qss(scale_for_width(width))
        sizes = [int(m) for m in re.findall(r"font-size:\s*(\d+)px", qss)]
        assert sizes, "no font sizes in generated sheet"
        assert min(sizes) >= MIN_FONT_PX, f"font fell below floor at width {width}"


def test_the_generated_sheet_contains_no_negation_selector() -> None:
    # Qt's QCss parser has no negating pseudo-selector. A single occurrence makes
    # `Parser::parse()` return false and Qt silently keeps only the rules BEFORE
    # the failure point — which is how the whole interactive vocabulary (buttons,
    # inputs, lists, tabs, menus) once vanished and left the app looking "bland".
    # Text assertions cannot catch that, so the raw string is guarded here and
    # the parse itself is checked in the test below.
    from yulon.ui.theme import _build_qss, scale_for_width

    for width in (960, 1280, 1920):
        qss = _build_qss(scale_for_width(width))
        assert ":not(" not in qss
        assert "not(" not in qss


LIVE_WIDGETS_A_RESTYLE_MAY_REPOLISH = 2000
"""How many live widgets the app-wide restyles below may find. T109.

`QApplication.setStyleSheet` and `setStyle` re-polish every live widget, so the
two app-level tests here cost whatever earlier modules left alive. Measured on
the full suite, 2026-09-24: 22,506 at this point before the fix (the parse test
16 s on the laptop, 38 s on m910q), and 12,732 with only the per-test teardown
(one module-scoped `main._Window` from `test_main.py` was never deleted). Run
alone, this module starts at 0.
"""


def test_no_earlier_module_left_widgets_for_the_restyle_to_repolish(qapp: QApplication) -> None:
    # Guards the two tests below from what OTHER modules do, and says so by
    # name rather than as a slow run nobody reads: a leak shows up here first.
    live = len(QApplication.allWidgets())
    tops = {type(w).__name__ for w in QApplication.topLevelWidgets() if w.parentWidget() is None}
    assert live <= LIVE_WIDGETS_A_RESTYLE_MAY_REPOLISH, (
        f"{live} widgets are alive before an app-wide restyle; orphan windows: {sorted(tops)}. "
        "Something outlived the test or module that made it (conftest.py tears these down)."
    )


def test_qt_actually_parses_the_generated_stylesheet(qapp: QApplication) -> None:
    # The only test that proves the sheet is VALID, not merely present. Qt emits
    # `Could not parse application stylesheet` (via qWarning) when the parser
    # rejects input; this installs a message handler, forces widget polish, and
    # asserts that warning never appeared. Every other theme test asserts on QSS
    # *text* and would keep passing with a sheet Qt refuses to load.
    from PySide6.QtCore import qInstallMessageHandler

    from yulon.ui.theme import apply_dadcraft_theme

    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _t, _c, msg: messages.append(msg))
    try:
        apply_dadcraft_theme(qapp)
        window = QMainWindow()
        tabs = QTabWidget(window)
        tabs.setObjectName("sidebar-tabs")
        page = QWidget()
        tabs.addTab(page, "Catalog")
        window.setCentralWidget(tabs)
        window.resize(1280, 800)
        window.show()
        qapp.processEvents()
    finally:
        qInstallMessageHandler(previous)

    parse_errors = [m for m in messages if "parse" in m.lower() and "stylesheet" in m.lower()]
    assert parse_errors == [], f"Qt rejected the stylesheet: {parse_errors}"


def test_muted_text_color_is_lightened_for_legibility() -> None:
    # Regression guard: the original #8A8275 measured too low-contrast against
    # the tab/list backgrounds it sits on; it must stay lighter than that.
    assert COLOR_TEXT_MUTED.upper() != "#8A8275"


def test_apply_dadcraft_theme_on_widget(qapp: QApplication) -> None:
    widget = QWidget()
    apply_dadcraft_theme(widget)
    assert widget.styleSheet() == DADCRAFT_THEME_QSS


def test_apply_dadcraft_theme_on_qapp(qapp: QApplication) -> None:
    apply_dadcraft_theme(qapp)
    assert qapp.styleSheet() == DADCRAFT_THEME_QSS


def test_dadcraft_realm_badge(qapp: QApplication) -> None:
    badge = DadcraftRealmBadge("online")
    assert "ONLINE" in badge._label.text()
    badge.set_status("starting")
    assert "STARTING" in badge._label.text()
    badge.set_status("restarting")
    assert "RESTARTING" in badge._label.text()
    badge.set_status("stopped")
    assert "OFFLINE" in badge._label.text()


def test_dadcraft_header(qapp: QApplication) -> None:
    header = DadcraftHeader("TEST REALM", "Subtitle")
    assert header is not None
    header.set_realm_status("running")
    assert "ONLINE" in header._badge._label.text()


def test_dadcraft_campaign_card(qapp: QApplication) -> None:
    for game_id in ("wow-wotlk", "wow-tbc", "wow-vanilla", "wow-tortoise"):
        card = DadcraftCampaignCard(game_id)
        assert card.objectName() == f"catalog-tile-{game_id}"
        assert card.height() == 370
        card._tick()
        assert card._time > 0.0


def test_format_dadcraft_tooltip() -> None:
    tooltip_html = format_dadcraft_tooltip(
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


def test_dadcraft_icon_renders_a_non_null_pixmap(qapp: QApplication) -> None:
    icon = dadcraft_icon("server", color=COLOR_GOLD_BRIGHT, size=16)
    assert not icon.isNull()
    pixmap = icon.pixmap(16, 16)
    assert pixmap.width() == 16
    assert pixmap.height() == 16


def test_dadcraft_icon_returns_empty_icon_for_unknown_name(qapp: QApplication) -> None:
    icon = dadcraft_icon("no-such-icon-name")
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


def test_get_app_icon_renders_a_valid_non_null_icon(qapp: QApplication) -> None:
    icon = get_app_icon()
    assert not icon.isNull()
    for size in (16, 32, 64, 128, 256):
        pixmap = icon.pixmap(size, size)
        assert not pixmap.isNull()
        assert pixmap.width() == size
        assert pixmap.height() == size
