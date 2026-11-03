"""Tests for the Dadcraft & WoW UI theme and custom decorative widgets."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
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


def test_shared_theme_accent_and_focus_ring_are_icy_blue() -> None:
    accent = QColor(COLOR_GOLD_BRIGHT)
    assert accent.blue() > accent.red()
    assert accent.green() > accent.red()
    assert f"border: 1px solid {COLOR_GOLD_BRIGHT};" in DADCRAFT_THEME_QSS
    assert QColor(COLOR_BG_DARK).blue() > QColor(COLOR_BG_DARK).red()


def test_header_renders_a_cool_glow_and_frost_particles(qapp: QApplication) -> None:
    header = DadcraftHeader()
    header.resize(720, 56)
    header.show()
    header._tick()
    image = header.grab().toImage()

    cool_pixels = 0
    for y in range(image.height()):
        for x in range(image.width()):
            colour = image.pixelColor(x, y)
            if colour.blue() > colour.red() + 20 and colour.green() > colour.red():
                cool_pixels += 1

    assert cool_pixels > image.width() * 10
    assert any(mote.crystal for mote in header._frost_motes)
    header.close()
    header.deleteLater()


def test_all_css_themes_and_arthas_build_valid_palettes_and_stylesheets(
    qapp: QApplication,
) -> None:
    from PySide6.QtCore import qInstallMessageHandler

    from yulon.ui.theme import THEME_CHOICES, THEMES, get_theme, normalize_theme_name

    expected = (
        "arthas",
        "organic-dark",
        "organic-light",
        "gruvbox-dark-soft",
        "gruvbox-dark-medium",
        "gruvbox-dark-hard",
        "gruvbox-light-soft",
        "gruvbox-light-medium",
        "gruvbox-light-hard",
        "98x-light",
        "98x-dark",
        "ocean",
        "ocean-dark",
        "jungle",
        "jungle-dark",
        "floral",
        "floral-dark",
        "dracula-dark",
        "dracula-light",
    )
    assert tuple(THEMES) == expected
    assert tuple(k for k, _ in THEME_CHOICES) == expected
    assert normalize_theme_name("98x") == "98x-light"
    assert normalize_theme_name("organic") == "organic-dark"
    assert normalize_theme_name("gruvbox-dark") == "gruvbox-dark-medium"
    assert normalize_theme_name("gruvbox-light") == "gruvbox-light-medium"
    assert normalize_theme_name("Arthas") == "arthas"

    messages: list[str] = []
    previous = qInstallMessageHandler(lambda _t, _c, msg: messages.append(msg))
    widget = QWidget()
    try:
        for theme_id in expected:
            spec = get_theme(theme_id)
            palette = build_dadcraft_palette(theme_id)
            assert palette.color(QPalette.ColorRole.Window).name().lower() == spec.bg_dark.lower()
            apply_dadcraft_theme(widget, width=1280, theme_name=theme_id)
            assert spec.bg_dark in widget.styleSheet()
            assert spec.accent_bright in widget.styleSheet()
    finally:
        qInstallMessageHandler(previous)
        widget.deleteLater()

    parse_errors = [m for m in messages if "parse" in m.lower() and "stylesheet" in m.lower()]
    assert parse_errors == [], parse_errors


def test_gruvbox_uses_orange_accent_and_header_renders_theme_specific_animations(
    qapp: QApplication,
) -> None:
    from yulon.ui.theme import THEMES, get_theme

    assert get_theme("organic-dark").label == "Organic Dark"
    assert get_theme("organic-light").label == "Organic Light"
    assert get_theme("organic-dark").animation == "organic"
    assert get_theme("organic-light").animation == "organic"
    assert get_theme("organic-dark").accent_bright.upper() == "#F26430"
    assert get_theme("organic-light").accent_bright.upper() == "#A63218"
    assert (
        QColor(get_theme("organic-dark").bg_panel).red()
        > QColor(get_theme("organic-dark").bg_panel).green()
    )
    assert (
        QColor(get_theme("organic-light").bg_panel).red()
        > QColor(get_theme("organic-light").bg_panel).green()
    )
    assert get_theme("gruvbox-dark-soft").bg_dark.upper() == "#32302F"
    assert get_theme("gruvbox-dark-medium").bg_dark.upper() == "#282828"
    assert get_theme("gruvbox-dark-hard").bg_dark.upper() == "#1D2021"
    assert get_theme("gruvbox-light-soft").bg_dark.upper() == "#F2E5BC"
    assert get_theme("gruvbox-light-medium").bg_dark.upper() == "#FBF1C7"
    assert get_theme("gruvbox-light-hard").bg_dark.upper() == "#F9F5D7"
    assert get_theme("gruvbox-dark-medium").accent_bright.upper() == "#FE8019"
    assert get_theme("gruvbox-light-medium").accent_bright.upper() == "#AF3A03"
    assert get_theme("gruvbox-dark-medium").animation == "gruvbox"
    assert get_theme("gruvbox-light-medium").animation == "gruvbox"
    assert get_theme("98x-light").animation == "98x"
    assert get_theme("98x-dark").animation == "98x"

    header = DadcraftHeader()
    header.resize(720, 50)
    assert header.height() == 50  # 10% reduced from 56px
    header.show()
    try:
        rendered: dict[str, QColor] = {}
        for theme_id in THEMES:
            header.set_theme(theme_id)
            header._tick()
            img = header.grab().toImage()
            assert not img.isNull()
            rendered[theme_id] = img.pixelColor(img.width() // 2, img.height() - 8)
        assert rendered["98x-light"] != rendered["arthas"]
        assert rendered["organic-dark"] != rendered["arthas"]
        assert rendered["gruvbox-dark-medium"] != rendered["arthas"]
        assert rendered["gruvbox-dark-medium"] != rendered["organic-dark"]
        assert rendered["gruvbox-light-medium"] != rendered["gruvbox-dark-medium"]
        assert rendered["floral"] != rendered["ocean"]
    finally:
        header.close()
        header.deleteLater()

    # In a light theme, campaign cards render a light surface so dark text has high contrast.
    host = QWidget()
    card = DadcraftCampaignCard("wow-wotlk", host)
    card.resize(300, 370)
    apply_dadcraft_theme(host, theme_name="gruvbox-light-medium")
    host.show()
    try:
        card_img = card.grab().toImage()
        center_px = card_img.pixelColor(card_img.width() // 2, card_img.height() // 2)
        assert center_px.lightness() > 160, f"light theme card stayed dark: {center_px.name()}"
    finally:
        host.close()
        host.deleteLater()


def test_themes_load_from_json_files_and_support_user_accessible_custom_themes(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    from yulon import platform, resources, ui_settings
    from yulon.ui.theme import (
        BUNDLED_THEME_ORDER,
        available_theme_choices,
        get_theme,
        sync_user_themes,
        user_themes_dir,
    )

    monkeypatch.setattr(platform, "config_dir", lambda: tmp_path)
    assert user_themes_dir() == tmp_path / "themes"

    # Bundled themes exist as individual JSON files under resources.themes_dir().
    bundled_files = {p.stem for p in resources.themes_dir().glob("*.json")}
    assert set(BUNDLED_THEME_ORDER) <= bundled_files

    # Seeding the user-accessible themes folder copies the bundled JSON files there
    # and prunes legacy alias files (`gruvbox-dark.json` -> `gruvbox-dark-medium.json`).
    legacy = tmp_path / "themes" / "gruvbox-dark.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"id": "gruvbox-dark", "label": "Gruvbox Dark"}), encoding="utf-8")
    seeded = sync_user_themes()
    assert seeded == tmp_path / "themes"
    assert (seeded / "arthas.json").is_file()
    assert not legacy.exists()

    # Dropping a custom JSON theme into the user folder makes it selectable and loadable.
    custom_path = seeded / "frostfire-custom.json"
    custom_path.write_text(
        json.dumps(
            {
                "id": "frostfire-custom",
                "label": "Frostfire Custom",
                "is_light": False,
                "bg_dark": "#11091C",
                "accent_bright": "#FF55AA",
                "animation": "floral",
            }
        ),
        encoding="utf-8",
    )

    choices = dict(available_theme_choices())
    assert choices.get("frostfire-custom") == "Frostfire Custom"
    spec = get_theme("frostfire-custom")
    assert spec.bg_dark == "#11091C"
    assert spec.accent_bright == "#FF55AA"
    assert spec.animation == "floral"

    # And `ui.json` accepts and remembers the custom user theme ID (even when
    # the filename differs from the inner `"id"` field, and unsafe CSS tokens
    # are rejected without breaking the rest of the theme).
    assert ui_settings.remember_theme("frostfire-custom")
    assert ui_settings.load_ui_settings().theme == "frostfire-custom"

    mismatched_path = seeded / "my-file.json"
    mismatched_path.write_text(
        json.dumps(
            {
                "id": "custom-inner-id",
                "label": "Custom Inner",
                "bg_dark": "#0F172A",
                "accent_bright": "#38BDF8; } * { background: red",
            }
        ),
        encoding="utf-8",
    )
    inner_spec = get_theme("custom-inner-id")
    assert inner_spec.bg_dark == "#0F172A"
    # Unsafe token with CSS syntax is ignored in favor of the default token.
    assert inner_spec.accent_bright == COLOR_GOLD_BRIGHT
    assert ui_settings.remember_theme("custom-inner-id")
    assert ui_settings.load_ui_settings().theme == "custom-inner-id"


def test_dadcraft_theme_qss_covers_essential_controls() -> None:
    assert "QMainWindow" in DADCRAFT_THEME_QSS
    assert "QTabWidget" in DADCRAFT_THEME_QSS
    assert "QTabBar::tab" in DADCRAFT_THEME_QSS
    assert "QTabWidget#sidebar-tabs > QTabBar::tab" in DADCRAFT_THEME_QSS
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


def test_the_tab_play_is_exempt_by_id_like_the_x() -> None:
    """T187: the ▶ beside the × has the ×'s size, so the two fit across the rail."""
    from yulon.ui.theme import LAUNCH_TAB_BUTTON, _build_qss

    qss = _build_qss(1.0)
    rule = qss.split(f"QTabBar QToolButton#{LAUNCH_TAB_BUTTON} {{")[1].split("}")[0]
    assert "max-width: 18px" in rule and "max-height: 18px" in rule
    assert "border: none" in rule
    # Gold at rest (fix round 1), so it reads apart from the muted ×.
    from yulon.ui.theme import COLOR_GOLD_LIGHT

    assert f"color: {COLOR_GOLD_LIGHT};" in rule


def test_the_play_menu_button_draws_one_arrow_not_two() -> None:
    """Its label is the ▾; the style's own menu indicator was a second arrow (T181, fixed T187)."""
    from yulon.ui.theme import PLAY_MENU_BUTTON, _build_qss

    qss = _build_qss(1.0)
    assert f"QPushButton#{PLAY_MENU_BUTTON}::menu-indicator" in qss
    rule = qss.split(f"QPushButton#{PLAY_MENU_BUTTON}::menu-indicator")[1].split("}")[0]
    assert "image: none" in rule and "width: 0px" in rule


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
    west = DADCRAFT_THEME_QSS.split("QTabWidget#sidebar-tabs > QTabBar::tab {")[1].split("}")[0]
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


@pytest.fixture
def _application_theme_is_restored(qapp: QApplication) -> Iterator[QApplication]:
    """The app's style sheet and palette, put back after a test that themes the app (T212).

    `apply_dadcraft_theme(qapp)` changes the one `QApplication` every later test
    shares. Left in place, the theme's touch-target floor grew the fixed-size
    buttons of `test_gamepad_keyboard.py`'s `placed` fixture from 60x20 to
    98x50, and four navigation tests failed whenever this file ran first.
    `conftest.py`'s `_no_test_leaves_the_application_restyled` fails any test
    that forgets this.
    """
    sheet, palette = qapp.styleSheet(), QPalette(qapp.palette())
    yield qapp
    qapp.setStyleSheet(sheet)
    qapp.setPalette(palette)


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


def test_qt_actually_parses_the_generated_stylesheet(
    _application_theme_is_restored: QApplication,
) -> None:
    # The only test that proves the sheet is VALID, not merely present. Qt emits
    # `Could not parse application stylesheet` (via qWarning) when the parser
    # rejects input; this installs a message handler, forces widget polish, and
    # asserts that warning never appeared. Every other theme test asserts on QSS
    # *text* and would keep passing with a sheet Qt refuses to load.
    from PySide6.QtCore import qInstallMessageHandler

    from yulon.ui.theme import apply_dadcraft_theme

    qapp = _application_theme_is_restored
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


def test_apply_dadcraft_theme_on_qapp(_application_theme_is_restored: QApplication) -> None:
    qapp = _application_theme_is_restored
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


@pytest.mark.parametrize(
    ("status", "says"),
    [("stopping", "STOPPING"), ("partial", "PARTLY UP"), ("unknown", "STATUS UNKNOWN")],
)
def test_the_realm_badge_names_stopping_and_partly_up(
    qapp: QApplication, status: str, says: str
) -> None:
    """T188 C4/C5: both fell through to "REALM OFFLINE" while the realm was up."""
    badge = DadcraftRealmBadge("running")
    badge.set_status(status)
    text = badge._label.text()
    assert says in text, text
    assert "OFFLINE" not in text and "STARTING" not in text, text


def test_the_header_badge_follows_the_badge_it_is_given(qapp: QApplication) -> None:
    """T188 C6: the header said REALM OFFLINE whatever the server was doing."""
    header = DadcraftHeader("TEST REALM", "Subtitle")
    first, second = DadcraftRealmBadge("running"), DadcraftRealmBadge("stopped")

    header.follow(first)
    assert header._badge.status == "running"
    assert header._badge.isHidden() is False

    header.follow(second)
    assert header._badge.status == "stopped"
    first.set_status("starting")
    assert header._badge.status == "stopped", "it still listens to the badge it left"
    second.set_status("stopping")
    assert header._badge.status == "stopping"


def test_the_header_badge_hides_when_there_is_nothing_to_follow(qapp: QApplication) -> None:
    header = DadcraftHeader("TEST REALM", "Subtitle")
    badge = DadcraftRealmBadge("running")
    header.follow(badge)

    header.follow(None)

    assert header._badge.isHidden() is True
    badge.set_status("stopped")
    assert header._badge.status == "running", "a hidden badge kept following"


def test_the_header_lets_go_of_a_badge_that_was_destroyed(qapp: QApplication) -> None:
    """A closed tab's badge is deleted while the header still holds it."""
    import shiboken6

    header = DadcraftHeader("TEST REALM", "Subtitle")
    gone = DadcraftRealmBadge("running")
    header.follow(gone)
    shiboken6.delete(gone)

    after = DadcraftRealmBadge("stopping")
    header.follow(after)

    assert header._badge.status == "stopping"
    assert header._badge.isHidden() is False


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
    assert tabs.tabPosition() == QTabWidget.TabPosition.North
    assert window.property("top_menu") is not None


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


# -- T192: one reading of a realm status for the badge and the sidebar's dot -------


@pytest.mark.parametrize(
    ("status", "tone", "says"),
    [
        ("running", "up", "ONLINE"),
        ("online", "up", "ONLINE"),
        ("ready", "up", "ONLINE"),
        ("starting", "between", "STARTING"),
        ("importing", "between", "STARTING"),
        ("building", "between", "STARTING"),
        ("stopping", "between", "STOPPING"),
        ("partial", "between", "PARTLY UP"),
        ("restarting", "restarting", "RESTARTING"),
        ("loop", "restarting", "CRASH LOOP"),  # T391: same tone, its own words
        ("unknown", "unknown", "UNKNOWN"),
        ("stopped", "down", "OFFLINE"),
        ("exited", "down", "OFFLINE"),
    ],
)
def test_realm_tone_is_what_the_badge_says(
    qapp: QApplication, status: str, tone: str, says: str
) -> None:
    """The sidebar's dot reads `realm_tone()`; the badge must read it too, or they drift."""
    from yulon.ui.widgets.dadcraft_decorations import realm_tone

    badge = DadcraftRealmBadge("running" if status != "running" else "stopped")
    badge.set_status(status)
    assert realm_tone(status) == tone
    assert realm_tone(status.upper()) == tone
    assert says in badge._label.text(), badge._label.text()


def test_the_header_names_the_server_it_follows(qapp: QApplication) -> None:
    """T192: the header's game strip -- the server's whole name beside its badge.

    Hidden with the badge, on the Catalog and Logs (`follow(None)`), and when a
    caller hands a badge with no title.
    """
    header = DadcraftHeader("TEST REALM", "Subtitle")
    header.resize(1200, 56)
    header.show()
    badge = DadcraftRealmBadge("running")

    header.follow(badge, title="WoW WotLK — DadsMmoLab", tooltip="WoW WotLK — D:/DadsMmoLab")

    assert header._realm_title.isHidden() is False
    assert header._realm_title.text() == "WoW WotLK — DadsMmoLab"
    assert header._realm_title.toolTip() == "WoW WotLK — D:/DadsMmoLab"

    header.follow(None)
    assert header._realm_title.isHidden() is True
    header.follow(badge)
    assert header._realm_title.isHidden() is True
    header.close()


def test_bundled_themes_match_json_files_and_meet_wcag_aa_contrast() -> None:
    """K1: every `.json` file in `yulon/ui/themes/` matches its `BUNDLED_THEMES` baseline."""
    import dataclasses
    import json

    from yulon import resources
    from yulon.ui.theme_spec import BUNDLED_THEME_ORDER, BUNDLED_THEMES

    def rel_lum(hex_str: str) -> float:
        c = QColor(hex_str)

        def ch(v: int) -> float:
            x = v / 255.0
            return float(x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4)

        return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())

    def contrast(h1: str, h2: str) -> float:
        l1, l2 = rel_lum(h1), rel_lum(h2)
        return (max(l1, l2) + 0.05) / (min(l1, l2) + 0.05)

    assert tuple(BUNDLED_THEMES) == BUNDLED_THEME_ORDER
    json_files = sorted(resources.themes_dir().glob("*.json"))
    assert {p.stem for p in json_files} == set(BUNDLED_THEMES)
    for path in json_files:
        raw = json.loads(path.read_text(encoding="utf-8"))
        spec = BUNDLED_THEMES[path.stem]
        spec_dict = dataclasses.asdict(spec)
        assert raw == spec_dict, f"{path.name} drifted from BUNDLED_THEMES[{path.stem!r}]"
        assert contrast(spec.text_primary, spec.bg_panel) >= 4.5, path.stem
        assert contrast(spec.text_muted, spec.bg_panel) >= 4.0, path.stem
        assert contrast(spec.text_muted, spec.bg_container) >= 4.5, path.stem
        if path.stem.startswith(("gruvbox-", "organic-")):
            assert contrast(spec.text_muted, spec.bg_panel) >= 4.5, path.stem
            assert contrast(spec.primary_btn_text, spec.accent_border) >= 4.5, path.stem


def test_dark_theme_cards_and_tile_buttons_use_spec_tokens_and_tooltips_respect_theme(
    qapp: QApplication,
) -> None:
    """K1: dark themes paint `spec` surfaces on cards/buttons; light tooltips use dark text."""
    from yulon.ui.theme import _build_qss, get_theme

    for theme_id in ("gruvbox-dark", "dracula-dark", "floral-dark"):
        spec = get_theme(theme_id)
        qss = _build_qss(1.0, theme_name=theme_id)
        tile_btn = qss.split('QFrame[objectName^="catalog-tile-"] QPushButton {')[1].split("}")[0]
        assert f"background-color: {spec.bg_panel};" in tile_btn
        assert f"color: {spec.text_primary};" in tile_btn
        assert "rgba(11, 17, 27, 0.86)" not in tile_btn

    host = QWidget()
    card = DadcraftCampaignCard("wow-wotlk", host)
    card.resize(300, 370)
    host.show()
    try:
        apply_dadcraft_theme(host, theme_name="arthas")
        arthas_px = card.grab().toImage().pixelColor(150, 260)
        apply_dadcraft_theme(host, theme_name="gruvbox-dark")
        gruvbox_px = card.grab().toImage().pixelColor(150, 260)
        assert arthas_px != gruvbox_px
        assert gruvbox_px.red() >= gruvbox_px.blue()
    finally:
        host.close()
        host.deleteLater()

    light_tip = format_dadcraft_tooltip(
        "Realm Status",
        "Online",
        quality="common",
        flavor_text="Ready",
        theme_name="gruvbox-light",
    )
    light_spec = get_theme("gruvbox-light")
    assert light_spec.text_primary in light_tip
    assert "#FFFFFF" not in light_tip


def test_k4_header_and_badge_typography_scale_and_focus_highlights_across_themes(
    qapp: QApplication,
) -> None:
    """Phase K4.1: `DadcraftHeader` and `DadcraftRealmBadge` scale via `_px(..., scale)`."""
    from yulon.ui.theme import BUNDLED_THEME_ORDER, _build_qss, get_theme, scale_for_width

    window = QMainWindow()
    header = DadcraftHeader("Dad's MMO Lab", "Yu'lon", window)
    window.setProperty("header", header)
    badge = DadcraftRealmBadge("running", window)
    try:
        apply_dadcraft_theme(window, width=960, theme_name="arthas")
        assert window.property("yulon_scale") == scale_for_width(960)
        assert "font-size: 15px;" in header._title_label.styleSheet()
        assert "font-size: 12px;" in header._sub_label.styleSheet()
        assert "font-size: 12px;" in header._realm_title.styleSheet()
        assert "font-size: 12px;" in badge._label.styleSheet()

        apply_dadcraft_theme(window, width=1280, theme_name="arthas")
        assert window.property("yulon_scale") == 1.0
        assert "font-size: 20px;" in header._title_label.styleSheet()
        assert "font-size: 12px;" in header._sub_label.styleSheet()
        assert "font-size: 13px;" in header._realm_title.styleSheet()
        assert "font-size: 12px;" in badge._label.styleSheet()

        apply_dadcraft_theme(window, width=1920, theme_name="arthas")
        assert window.property("yulon_scale") == 1.0
        assert "font-size: 20px;" in header._title_label.styleSheet()
    finally:
        window.close()
        window.deleteLater()

    for theme_id in BUNDLED_THEME_ORDER:
        spec = get_theme(theme_id)
        qss = _build_qss(1.0, theme_name=theme_id)
        tile_focus = qss.split('QFrame[objectName^="catalog-tile-"] QPushButton:focus {')[1].split(
            "}"
        )[0]
        assert f"border: 1px solid {spec.accent_bright};" in tile_focus
        back_focus = qss.split("QPushButton#catalog-back-button:focus {")[1].split("}")[0]
        assert f"border: 2px solid {spec.accent_bright};" in back_focus


def test_apply_dadcraft_theme_propagates_to_modules_and_tuning_rows(
    qapp: QApplication,
) -> None:
    """`apply_dadcraft_theme` calls `apply_theme` on descendant `ModulesPanel` and `RowWidget`."""
    from yulon import tuning
    from yulon.ui.theme import get_theme
    from yulon.ui.widgets.modules_panel import BADGE_INSTALLED, ModuleRow, ModulesPanel
    from yulon.ui.widgets.tuning_panel import TuningPanel, build_tuning_cards

    host = QWidget()
    apply_dadcraft_theme(host, theme_name="gruvbox-dark")
    panel = ModulesPanel(host)
    panel.set_rows(
        [
            ModuleRow(
                "mod-aoe-loot",
                "module",
                "AoE Loot",
                "Loot nearby corpses at once.",
                "https://github.com/azerothcore/mod-aoe-loot",
                True,
                True,
                (),
                (),
                True,
                None,
                badge=BADGE_INSTALLED,
            )
        ]
    )
    spec = get_theme("gruvbox-dark")
    row = panel.row("mod-aoe-loot")
    # Even on initial `set_rows()` under a themed parent, `RowWidget` and `_FamilyCard` inherit it
    assert spec.text_gold in row.name_label.styleSheet()
    assert spec.uncommon in row.badge_label.styleSheet()
    assert spec.uncommon in panel._cards["module"].installed_header.styleSheet()

    # And switching the parent's theme updates `ModulesPanel`, `TuningPanel`, and `file_title`
    tpanel = TuningPanel(host)
    trow = tuning.TuningRow(
        module_id="mod-aoe-loot",
        module_name="AoE Loot",
        family="module",
        file="env/dist/etc/modules/aoe_loot.conf",
        key="AoELoot.Enable",
        label="Enable AoE Loot",
        explain="Loot nearby corpses.",
        type="bool",
        min=None,
        max=None,
        default="1",
        current="1",
        installed=True,
        backend="conf",
        read_only_reason=None,
    )
    tpanel.set_cards(build_tuning_cards((trow,)))
    tpanel.set_files(["env/dist/etc/modules/aoe_loot.conf"])
    assert spec.text_primary in tpanel.file_title.text()

    apply_dadcraft_theme(host, theme_name="dracula-light")
    light_spec = get_theme("dracula-light")
    assert light_spec.text_gold in row.name_label.styleSheet()
    assert light_spec.uncommon in row.badge_label.styleSheet()
    assert light_spec.text_primary in tpanel.file_title.text()
    assert light_spec.text_primary in tpanel.card("mod-aoe-loot").title_label.styleSheet()
    host.deleteLater()


def test_friend_launcher_logs_view_and_tray_flyout_follow_theme_changes(
    qapp: QApplication,
) -> None:
    """`FriendLauncher`, `LogsView`, `LogPanel`, and `TrayFlyout` re-theme all child elements."""
    from yulon.ui.friend_launcher import FriendLauncher
    from yulon.ui.logs_view import LogsView
    from yulon.ui.theme import get_theme
    from yulon.ui.tray_flyout import ServerCard, TrayFlyout

    catalog = load_catalog()
    launcher = FriendLauncher(catalog)
    logs_view = LogsView(lambda: (), catalog)
    log_panel = LogPanel()
    flyout_card = ServerCard()
    try:
        for theme_id in ("gruvbox-dark-medium", "dracula-light"):
            spec = get_theme(theme_id)
            launcher.apply_theme(theme_id)
            assert launcher.property("yulon_theme") == theme_id
            assert spec.text_muted in launcher.note_label.styleSheet()
            assert spec.bg_dark in launcher.styleSheet()
            assert not launcher.make_button.icon().isNull()
            assert not launcher.play_button.icon().isNull()
            assert not launcher.remove_button.icon().isNull()

            logs_view.apply_theme(theme_id)
            assert logs_view.property("yulon_theme") == theme_id
            assert (
                logs_view.viewer.palette().color(QPalette.ColorRole.Base).name().lower()
                == spec.bg_input.lower()
            )
            assert not logs_view.save_button.icon().isNull()

            log_panel.apply_theme(theme_id)
            assert log_panel.property("yulon_theme") == theme_id
            assert (
                log_panel._text.palette().color(QPalette.ColorRole.Base).name().lower()
                == spec.bg_input.lower()
            )

            flyout_card.apply_theme(theme_id)
            assert flyout_card.property("yulon_theme") == theme_id
            assert not flyout_card.start.icon().isNull()
            assert not flyout_card.stop.icon().isNull()
    finally:
        for w in (launcher, logs_view, log_panel, flyout_card):
            w.close()
            w.deleteLater()
        _ = TrayFlyout


def test_dialogs_and_embedded_scroll_areas_render_light_surface_on_light_themes(
    qapp: QApplication,
) -> None:
    """In light themes, dialog scroll areas and quit modals render light surfaces and fit."""
    from PySide6.QtWidgets import QMessageBox

    from yulon import ui_settings
    from yulon.manifest import Manifest, Prompt
    from yulon.ui.message_box import FittedMessageBox
    from yulon.ui.tray import note_box, quit_box, while_stopping_box
    from yulon.ui.widgets.manifest_prompt import ManifestPromptDialog

    window = QMainWindow()
    apply_dadcraft_theme(window, theme_name="organic-light")
    window.resize(640, 480)
    window.show()

    box = FittedMessageBox(
        QMessageBox.Icon.Information,
        "Light Dialog Check",
        "First paragraph of notice.\n\nSecond paragraph of notice.",
        QMessageBox.StandardButton.Ok,
        window,
    )
    box.show()

    manifest = Manifest(
        id="mod-xp",
        game="wow-wotlk",
        type="mod",
        name="XP Rate",
        description="Set XP rate.",
    )
    prompt = Prompt(key="rate", question="XP Rate?", kind="int", default="1")
    prompt_dlg = ManifestPromptDialog(window, manifest, [prompt])
    prompt_dlg.show()

    qbox, qbtns = quit_box(1, "A server is being stopped...", window)
    qbox.show()

    wbox, wbtns = while_stopping_box(1, parent=window)
    wbox.show()

    nbox, nbtns = note_box(window)
    nbox.show()
    qapp.processEvents()

    try:
        assert box._scroll is not None
        box_img = box.grab().toImage()
        pt = box._scroll.mapTo(box, box._scroll.rect().topLeft())
        scroll_px = box_img.pixelColor(pt.x() + 4, pt.y() + 4)
        assert (
            scroll_px.lightness() > 180
        ), f"FittedMessageBox scroll stayed dark: {scroll_px.name()}"

        prompt_img = prompt_dlg.grab().toImage()
        mpt = prompt_dlg._scroll.mapTo(prompt_dlg, prompt_dlg._scroll.rect().topLeft())
        m_px = prompt_img.pixelColor(mpt.x() + 4, mpt.y() + 4)
        assert m_px.lightness() > 180, f"ManifestPromptDialog scroll stayed dark: {m_px.name()}"

        # Verify quit modals keep their scroll area inside the grid and buttons separated
        for modal, btns in ((qbox, qbtns), (wbox, wbtns), (nbox, nbtns)):
            assert modal._scroll is not None
            assert modal.layout().indexOf(modal._scroll) >= 0
            assert modal._scroll.geometry().x() > 0
            assert modal._scroll.geometry().width() < modal.width()
            btn_list = list(btns.values())
            for i, btn in enumerate(btn_list):
                r1 = btn.geometry()
                assert r1.width() >= btn.sizeHint().width()
                for other in btn_list[i + 1 :]:
                    assert not r1.intersects(
                        other.geometry()
                    ), f"Buttons overlapped in {modal.windowTitle()}"

        # Verify Gruvbox dark headers stay dark and Gruvbox light headers stay bright & vibrant
        header = DadcraftHeader("Dad's MMO Lab", "Yu'lon")
        header.follow(None)
        header.resize(720, 50)
        header.show()
        try:
            header.set_theme("gruvbox-dark-hard")
            header._tick()
            dark_hdr = header.grab().toImage().pixelColor(520, 24)
            header.set_theme("gruvbox-light-hard")
            header._tick()
            light_hdr = header.grab().toImage().pixelColor(520, 24)
            assert dark_hdr.lightness() < 75, f"Gruvbox dark header too bright: {dark_hdr.name()}"
            assert light_hdr.lightness() > 190, f"Gruvbox light header too dark: {light_hdr.name()}"
        finally:
            header.close()
            header.deleteLater()
    finally:
        for w in (box, prompt_dlg, qbox, wbox, nbox, window):
            w.close()
            w.deleteLater()
        _ = ui_settings
