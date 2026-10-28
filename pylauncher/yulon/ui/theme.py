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

import dataclasses
import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QStyle,
    QStyleOptionSpinBox,
    QWidget,
)

from yulon import platform, resources
from yulon.log import get_logger

if TYPE_CHECKING:
    from PySide6.QtGui import QPalette
    from PySide6.QtWidgets import QApplication, QWidget

logger = get_logger(__name__)

DEFAULT_THEME = "arthas"
"""The default Battle.net / Frostmourne icy-blue theme."""

THEME_COMBO = "theme-selector"
"""objectName of the Settings dialog's theme dropdown."""

OPEN_THEMES_FOLDER_BUTTON = "open-themes-folder"
"""objectName of the Settings panel's button to open the user-accessible themes folder."""

# --- Dadcraft Minimal Surface Tokens ---
# Cool blue-black, not neutral gray, carries the app-wide icy treatment.
COLOR_BG_DARK = "#0B111B"  # window / app background
COLOR_BG_CONTAINER = "#101B29"  # tab pane, menus, popups
COLOR_BG_PANEL = "#162338"  # raised panel sheet
COLOR_BG_PARCHMENT = "#101B29"  # legacy alias kept for imported constants
COLOR_BG_PARCHMENT_LIGHT = "#1D3148"  # legacy alias: hover sheet
COLOR_BG_INPUT = "#080E17"  # inset input / list surface

# Historical constant names are retained for callers; these are the shared
# ice-blue accent tiers used by focus rings, active tabs, and primary actions.
COLOR_GOLD_BRIGHT = "#76D5FF"
COLOR_GOLD_LIGHT = "#BDEEFF"
COLOR_GOLD_BORDER = "#318BB4"
COLOR_GOLD_BRASS = "#225771"
COLOR_EMBER = "#183A53"
"""Deep frost-blue used for the header's low-light flame edge."""
COLOR_BRASS_DARK = "#2B4055"  # default hairline border
COLOR_BRASS_DEEP = "#1A2B3B"  # recessed / divider line

# Quality & state accents — desaturated from the old neon set so a status dot
# never shouts louder than the content beside it.
COLOR_COMMON = "#E6F0F8"
COLOR_UNCOMMON = "#5FB37A"  # muted fel / online green (was #1EFF00)
COLOR_RARE = "#5A8FBF"  # muted arcane / info blue (was #0070FF)
COLOR_EPIC = "#9B6FC4"  # muted nether purple (was #A335EE)
COLOR_LEGENDARY = "#D98F45"  # muted sunwell orange (was #FF8000)
COLOR_ARTIFACT = "#C9A96A"  # muted titan gold (was #E6CC80)
COLOR_DANGER = "#C4484C"  # muted crimson (was #C41E3A)
COLOR_TEXT_PRIMARY = "#E6F0F8"  # body text on dark sheets
COLOR_TEXT_MUTED = "#A9BBCB"  # secondary text (kept above WCAG AA on every sheet)
COLOR_TEXT_GOLD = "#9DDEFA"  # historical name; cool accent text
COLOR_TEXT_WARNING = "#E0A030"  # amber warning (unsupported platform, refusals)


@dataclass(frozen=True)
class ThemeSpec:
    """Complete token set for a Yu'lon UI theme (`tokens.css` & Arthas)."""

    id: str
    label: str
    is_light: bool
    bg_dark: str
    bg_container: str
    bg_panel: str
    bg_hover: str
    bg_input: str
    accent_bright: str
    accent_light: str
    accent_border: str
    accent_brass: str
    ember: str
    border_dark: str
    border_deep: str
    text_primary: str
    text_muted: str
    text_gold: str
    text_warning: str
    text_disabled: str
    tab_disabled: str
    primary_btn_text: str
    primary_btn_disabled_text: str
    uncommon: str
    rare: str
    danger: str
    danger_bg: str = "#2A1A1C"
    danger_hover_bg: str = "#3A2225"
    danger_pressed_bg: str = "#1A1012"
    danger_text: str = "#E8A0A4"
    danger_hover_text: str = "#FFC8CC"
    danger_border_pressed: str = "#6A3034"
    danger_border_focus: str = "#E06064"
    danger_disabled_bg: str = "#1E1618"
    danger_disabled_text: str = "#6A5A5C"
    tooltip_bg: str = "rgba(12, 20, 31, 0.97)"
    header_bg_start: str = "#06101D"
    header_bg_mid: str = "#0D2036"
    header_bg_end: str = "#071423"
    header_top_highlight: str = "#BDEEFF"
    tile_title: str = COLOR_TEXT_PRIMARY
    tile_subtitle: str = COLOR_TEXT_GOLD
    tile_desc: str = COLOR_TEXT_PRIMARY
    tile_meta: str = COLOR_TEXT_MUTED
    tile_warning: str = COLOR_TEXT_WARNING
    animation: str = "arthas"


THEMES: dict[str, ThemeSpec] = {
    "arthas": ThemeSpec(
        id="arthas",
        label="Arthas",
        is_light=False,
        bg_dark=COLOR_BG_DARK,
        bg_container=COLOR_BG_CONTAINER,
        bg_panel=COLOR_BG_PANEL,
        bg_hover=COLOR_BG_PARCHMENT_LIGHT,
        bg_input=COLOR_BG_INPUT,
        accent_bright=COLOR_GOLD_BRIGHT,
        accent_light=COLOR_GOLD_LIGHT,
        accent_border=COLOR_GOLD_BORDER,
        accent_brass=COLOR_GOLD_BRASS,
        ember=COLOR_EMBER,
        border_dark=COLOR_BRASS_DARK,
        border_deep=COLOR_BRASS_DEEP,
        text_primary=COLOR_TEXT_PRIMARY,
        text_muted=COLOR_TEXT_MUTED,
        text_gold=COLOR_TEXT_GOLD,
        text_warning=COLOR_TEXT_WARNING,
        text_disabled="#71869B",
        tab_disabled="#66798B",
        primary_btn_text=COLOR_BG_DARK,
        primary_btn_disabled_text="#7993A9",
        uncommon=COLOR_UNCOMMON,
        rare=COLOR_RARE,
        danger=COLOR_DANGER,
        tooltip_bg="rgba(12, 20, 31, 0.97)",
        header_bg_start="#06101D",
        header_bg_mid="#0D2036",
        header_bg_end="#071423",
        header_top_highlight="#BDEEFF",
    ),
    "gruvbox-dark": ThemeSpec(
        id="gruvbox-dark",
        label="Gruvbox Dark",
        is_light=False,
        bg_dark="#1D2021",
        bg_container="#282828",
        bg_panel="#3C3836",
        bg_hover="#504945",
        bg_input="#141617",
        accent_bright="#FE8019",
        accent_light="#FABD2F",
        accent_border="#D65D0E",
        accent_brass="#AF3A03",
        ember="#342014",
        border_dark="#504945",
        border_deep="#3C3836",
        text_primary="#EBDBB2",
        text_muted="#A89984",
        text_gold="#FABD2F",
        text_warning="#FE8019",
        text_disabled="#7C6F64",
        tab_disabled="#7C6F64",
        primary_btn_text="#1D2021",
        primary_btn_disabled_text="#928374",
        uncommon="#B8BB26",
        rare="#83A598",
        danger="#FB4934",
        danger_bg="#361816",
        danger_hover_bg="#4C1B18",
        danger_pressed_bg="#240F0E",
        danger_text="#FBF1C7",
        danger_hover_text="#FFFFFF",
        danger_border_pressed="#9D0006",
        danger_border_focus="#FB4934",
        danger_disabled_bg="#281D1C",
        danger_disabled_text="#7C6F64",
        tooltip_bg="rgba(29, 32, 33, 0.97)",
        header_bg_start="#141617",
        header_bg_mid="#28201B",
        header_bg_end="#1D2021",
        header_top_highlight="#FABD2F",
    ),
    "gruvbox-light": ThemeSpec(
        id="gruvbox-light",
        label="Gruvbox Light",
        is_light=True,
        bg_dark="#F2E5BC",
        bg_container="#F9F5D7",
        bg_panel="#EBDBB2",
        bg_hover="#FBF1C7",
        bg_input="#FBF1C7",
        accent_bright="#AF3A03",
        accent_light="#D65D0E",
        accent_border="#AF3A03",
        accent_brass="#B57614",
        ember="#D5C4A1",
        border_dark="#BDAE93",
        border_deep="#D5C4A1",
        text_primary="#282828",
        text_muted="#7C6F64",
        text_gold="#B57614",
        text_warning="#AF3A03",
        text_disabled="#928374",
        tab_disabled="#928374",
        primary_btn_text="#F9F5D7",
        primary_btn_disabled_text="#7C6F64",
        uncommon="#79740E",
        rare="#076678",
        danger="#9D0006",
        danger_bg="#F8D7DA",
        danger_hover_bg="#F1BFC4",
        danger_pressed_bg="#E8AEB4",
        danger_text="#9D0006",
        danger_hover_text="#7A0004",
        danger_border_pressed="#7A0004",
        danger_border_focus="#9D0006",
        danger_disabled_bg="#EFE4C8",
        danger_disabled_text="#928374",
        tooltip_bg="rgba(251, 241, 199, 0.98)",
        header_bg_start="#E5D5A7",
        header_bg_mid="#F2E5BC",
        header_bg_end="#D5C4A1",
        header_top_highlight="#AF3A03",
        tile_title="#282828",
        tile_subtitle="#AF3A03",
        tile_desc="#3C3836",
        tile_meta="#665C54",
        tile_warning="#AF3A03",
        animation="gruvbox",
    ),
    "98x-light": ThemeSpec(
        id="98x-light",
        label="98x Light",
        is_light=True,
        bg_dark="#B8B8B8",
        bg_container="#C0C0C0",
        bg_panel="#D4D0C8",
        bg_hover="#E4E2DC",
        bg_input="#FFFFFF",
        accent_bright="#000080",
        accent_light="#1E40AF",
        accent_border="#000080",
        accent_brass="#2563EB",
        ember="#122846",
        border_dark="#808080",
        border_deep="#9A9A9A",
        text_primary="#000000",
        text_muted="#444444",
        text_gold="#000080",
        text_warning="#C2410C",
        text_disabled="#666666",
        tab_disabled="#666666",
        primary_btn_text="#FFFFFF",
        primary_btn_disabled_text="#555555",
        uncommon="#15803D",
        rare="#2563EB",
        danger="#CC0000",
        danger_bg="#FEE2E2",
        danger_hover_bg="#FECACA",
        danger_pressed_bg="#FCA5A5",
        danger_text="#990000",
        danger_hover_text="#7F0000",
        danger_border_pressed="#800000",
        danger_border_focus="#CC0000",
        danger_disabled_bg="#CCCCCC",
        danger_disabled_text="#666666",
        tooltip_bg="rgba(255, 255, 225, 0.98)",
        header_bg_start="#000080",
        header_bg_mid="#0C3A88",
        header_bg_end="#1084D0",
        header_top_highlight="#FFFFFF",
        tile_title="#000000",
        tile_subtitle="#000080",
        tile_desc="#1F1F1F",
        tile_meta="#3A3A3A",
        tile_warning="#9A3412",
        animation="98x",
    ),
    "98x-dark": ThemeSpec(
        id="98x-dark",
        label="98x Dark",
        is_light=False,
        bg_dark="#1C1C1C",
        bg_container="#202020",
        bg_panel="#2B2B2B",
        bg_hover="#3A3A3A",
        bg_input="#181818",
        accent_bright="#3B82F6",
        accent_light="#93C5FD",
        accent_border="#1D4ED8",
        accent_brass="#2563EB",
        ember="#102034",
        border_dark="#3D3D3D",
        border_deep="#2B2B2B",
        text_primary="#E6E6E6",
        text_muted="#8A8A8A",
        text_gold="#60A5FA",
        text_warning="#F97316",
        text_disabled="#6B7280",
        tab_disabled="#6B7280",
        primary_btn_text="#FFFFFF",
        primary_btn_disabled_text="#8A8A8A",
        uncommon="#22C55E",
        rare="#38BDF8",
        danger="#EF4444",
        danger_bg="#380D0D",
        danger_hover_bg="#4C1414",
        danger_pressed_bg="#240808",
        danger_text="#FCA5A5",
        danger_hover_text="#FEE2E2",
        danger_border_pressed="#991B1B",
        danger_border_focus="#EF4444",
        danger_disabled_bg="#261B1B",
        danger_disabled_text="#6B7280",
        tooltip_bg="rgba(28, 28, 28, 0.97)",
        header_bg_start="#08101A",
        header_bg_mid="#122642",
        header_bg_end="#0A1422",
        header_top_highlight="#93C5FD",
    ),
    "ocean": ThemeSpec(
        id="ocean",
        label="Ocean",
        is_light=True,
        bg_dark="#D2E0EC",
        bg_container="#E5EDF5",
        bg_panel="#F4F8FC",
        bg_hover="#FFFFFF",
        bg_input="#F8FBFE",
        accent_bright="#2B7CD3",
        accent_light="#1852A8",
        accent_border="#2B7CD3",
        accent_brass="#3884FF",
        ember="#9BBCD4",
        border_dark="#7A9EBC",
        border_deep="#9BBCD4",
        text_primary="#102A43",
        text_muted="#486581",
        text_gold="#1D5799",
        text_warning="#EA580C",
        text_disabled="#627D98",
        tab_disabled="#627D98",
        primary_btn_text="#FFFFFF",
        primary_btn_disabled_text="#627D98",
        uncommon="#059669",
        rare="#2B7CD3",
        danger="#DC2626",
        danger_bg="#FEE2E2",
        danger_hover_bg="#FECACA",
        danger_pressed_bg="#FCA5A5",
        danger_text="#991B1B",
        danger_hover_text="#7F1D1D",
        danger_border_pressed="#991B1B",
        danger_border_focus="#DC2626",
        danger_disabled_bg="#DCE7F0",
        danger_disabled_text="#627D98",
        tooltip_bg="rgba(244, 248, 252, 0.98)",
        header_bg_start="#C6DCF0",
        header_bg_mid="#DFECF8",
        header_bg_end="#B8D2EA",
        header_top_highlight="#FFFFFF",
        tile_title="#102A43",
        tile_subtitle="#1D5799",
        tile_desc="#243B53",
        tile_meta="#486581",
        tile_warning="#C2410C",
        animation="ocean",
    ),
    "ocean-dark": ThemeSpec(
        id="ocean-dark",
        label="Ocean Dark",
        is_light=False,
        bg_dark="#070C1A",
        bg_container="#0E172E",
        bg_panel="#162444",
        bg_hover="#253E6D",
        bg_input="#050A14",
        accent_bright="#38BDF8",
        accent_light="#93C5FD",
        accent_border="#0284C7",
        accent_brass="#0369A1",
        ember="#0C4A6E",
        border_dark="#253E6D",
        border_deep="#1A2D52",
        text_primary="#E2E8F0",
        text_muted="#829AB1",
        text_gold="#67E8F9",
        text_warning="#FB923C",
        text_disabled="#627D98",
        tab_disabled="#627D98",
        primary_btn_text="#FFFFFF",
        primary_btn_disabled_text="#829AB1",
        uncommon="#34D399",
        rare="#60A5FA",
        danger="#F87171",
        danger_bg="#450A0A",
        danger_hover_bg="#5C1010",
        danger_pressed_bg="#2C0606",
        danger_text="#FECACA",
        danger_hover_text="#FEE2E2",
        danger_border_pressed="#991B1B",
        danger_border_focus="#F87171",
        danger_disabled_bg="#18182C",
        danger_disabled_text="#627D98",
        tooltip_bg="rgba(7, 12, 26, 0.97)",
        header_bg_start="#050A14",
        header_bg_mid="#0E2042",
        header_bg_end="#070C1A",
        header_top_highlight="#67E8F9",
    ),
    "jungle": ThemeSpec(
        id="jungle",
        label="Jungle",
        is_light=True,
        bg_dark="#D8E6DA",
        bg_container="#EAF2EB",
        bg_panel="#F4F9F5",
        bg_hover="#FFFFFF",
        bg_input="#FFFFFF",
        accent_bright="#15803D",
        accent_light="#14532D",
        accent_border="#15803D",
        accent_brass="#22C55E",
        ember="#9EC2A4",
        border_dark="#7EA885",
        border_deep="#9EC2A4",
        text_primary="#13381B",
        text_muted="#4A7050",
        text_gold="#166534",
        text_warning="#C2410C",
        text_disabled="#688A6E",
        tab_disabled="#688A6E",
        primary_btn_text="#FFFFFF",
        primary_btn_disabled_text="#688A6E",
        uncommon="#15803D",
        rare="#0F766E",
        danger="#DC2626",
        danger_bg="#FEE2E2",
        danger_hover_bg="#FECACA",
        danger_pressed_bg="#FCA5A5",
        danger_text="#991B1B",
        danger_hover_text="#7F1D1D",
        danger_border_pressed="#991B1B",
        danger_border_focus="#DC2626",
        danger_disabled_bg="#DCE8DE",
        danger_disabled_text="#688A6E",
        tooltip_bg="rgba(244, 249, 245, 0.98)",
        header_bg_start="#C8DFCC",
        header_bg_mid="#DFEFE2",
        header_bg_end="#B8D5BD",
        header_top_highlight="#FFFFFF",
        tile_title="#13381B",
        tile_subtitle="#166534",
        tile_desc="#1E4627",
        tile_meta="#3F6647",
        tile_warning="#B45309",
        animation="jungle",
    ),
    "jungle-dark": ThemeSpec(
        id="jungle-dark",
        label="Jungle Dark",
        is_light=False,
        bg_dark="#07120A",
        bg_container="#102417",
        bg_panel="#162E1D",
        bg_hover="#275233",
        bg_input="#050E08",
        accent_bright="#4ADE80",
        accent_light="#86EFAC",
        accent_border="#10B981",
        accent_brass="#15803D",
        ember="#0D401E",
        border_dark="#275233",
        border_deep="#1B3B24",
        text_primary="#E2F2E5",
        text_muted="#82A88A",
        text_gold="#5EEAD4",
        text_warning="#FB923C",
        text_disabled="#527458",
        tab_disabled="#527458",
        primary_btn_text="#050E08",
        primary_btn_disabled_text="#82A88A",
        uncommon="#4ADE80",
        rare="#2DD4BF",
        danger="#F87171",
        danger_bg="#450A0A",
        danger_hover_bg="#5C1010",
        danger_pressed_bg="#2C0606",
        danger_text="#FECACA",
        danger_hover_text="#FEE2E2",
        danger_border_pressed="#991B1B",
        danger_border_focus="#F87171",
        danger_disabled_bg="#142218",
        danger_disabled_text="#527458",
        tooltip_bg="rgba(7, 18, 10, 0.97)",
        header_bg_start="#050E08",
        header_bg_mid="#102C1A",
        header_bg_end="#07120A",
        header_top_highlight="#86EFAC",
    ),
    "floral": ThemeSpec(
        id="floral",
        label="Floral",
        is_light=True,
        bg_dark="#F8E1E7",
        bg_container="#FDF2F4",
        bg_panel="#FFF5F7",
        bg_hover="#FFFFFF",
        bg_input="#FFFBFD",
        accent_bright="#D94672",
        accent_light="#9F1239",
        accent_border="#D94672",
        accent_brass="#EC4899",
        ember="#E2B2BE",
        border_dark="#CC96A4",
        border_deep="#E2B2BE",
        text_primary="#4C1D28",
        text_muted="#884D5D",
        text_gold="#BE185D",
        text_warning="#C2410C",
        text_disabled="#9D7883",
        tab_disabled="#9D7883",
        primary_btn_text="#FFFFFF",
        primary_btn_disabled_text="#9D7883",
        uncommon="#059669",
        rare="#7E22CE",
        danger="#BE123C",
        danger_bg="#FFE4E6",
        danger_hover_bg="#FECDD3",
        danger_pressed_bg="#FDA4AF",
        danger_text="#9F1239",
        danger_hover_text="#881337",
        danger_border_pressed="#881337",
        danger_border_focus="#BE123C",
        danger_disabled_bg="#F5DCE3",
        danger_disabled_text="#9D7883",
        tooltip_bg="rgba(255, 245, 247, 0.98)",
        header_bg_start="#F5D4DC",
        header_bg_mid="#FCE7EC",
        header_bg_end="#EBC2CD",
        header_top_highlight="#FFFFFF",
        tile_title="#4C1D28",
        tile_subtitle="#BE185D",
        tile_desc="#5B2331",
        tile_meta="#884D5D",
        tile_warning="#B45309",
        animation="floral",
    ),
    "floral-dark": ThemeSpec(
        id="floral-dark",
        label="Floral Dark",
        is_light=False,
        bg_dark="#120910",
        bg_container="#1E111A",
        bg_panel="#2C1626",
        bg_hover="#522746",
        bg_input="#10060D",
        accent_bright="#F472B6",
        accent_light="#F9A8D4",
        accent_border="#EC4899",
        accent_brass="#9D174D",
        ember="#881337",
        border_dark="#522746",
        border_deep="#3B1B31",
        text_primary="#FCE7F3",
        text_muted="#C08497",
        text_gold="#FBCFE8",
        text_warning="#FB923C",
        text_disabled="#9D5D6F",
        tab_disabled="#9D5D6F",
        primary_btn_text="#FFFFFF",
        primary_btn_disabled_text="#C08497",
        uncommon="#34D399",
        rare="#E879F9",
        danger="#FB7185",
        danger_bg="#4C0519",
        danger_hover_bg="#660922",
        danger_pressed_bg="#310310",
        danger_text="#FECDD3",
        danger_hover_text="#FFE4E6",
        danger_border_pressed="#9F1239",
        danger_border_focus="#FB7185",
        danger_disabled_bg="#26121F",
        danger_disabled_text="#9D5D6F",
        tooltip_bg="rgba(18, 9, 16, 0.97)",
        header_bg_start="#10060D",
        header_bg_mid="#2C1626",
        header_bg_end="#180A14",
        header_top_highlight="#F9A8D4",
    ),
    "dracula-dark": ThemeSpec(
        id="dracula-dark",
        label="Dracula Dark",
        is_light=False,
        bg_dark="#21222C",
        bg_container="#282A36",
        bg_panel="#343746",
        bg_hover="#424450",
        bg_input="#191A21",
        accent_bright="#BD93F9",
        accent_light="#8BE9FD",
        accent_border="#BD93F9",
        accent_brass="#6272A4",
        ember="#343746",
        border_dark="#44475A",
        border_deep="#191A21",
        text_primary="#F8F8F2",
        text_muted="#97A7D9",
        text_gold="#F1FA8C",
        text_warning="#FFB86C",
        text_disabled="#6272A4",
        tab_disabled="#6272A4",
        primary_btn_text="#282A36",
        primary_btn_disabled_text="#97A7D9",
        uncommon="#50FA7B",
        rare="#8BE9FD",
        danger="#FF5555",
        danger_bg="#3B1C1C",
        danger_hover_bg="#4F2424",
        danger_pressed_bg="#261111",
        danger_text="#FFB8B8",
        danger_hover_text="#FFE0E0",
        danger_border_pressed="#992626",
        danger_border_focus="#FF5555",
        danger_disabled_bg="#28222B",
        danger_disabled_text="#6272A4",
        tooltip_bg="rgba(33, 34, 44, 0.97)",
        header_bg_start="#191A21",
        header_bg_mid="#282A36",
        header_bg_end="#21222C",
        header_top_highlight="#8BE9FD",
    ),
    "dracula-light": ThemeSpec(
        id="dracula-light",
        label="Dracula Light",
        is_light=True,
        bg_dark="#DEDCCF",
        bg_container="#FFFBEB",
        bg_panel="#EFEDDC",
        bg_hover="#ECE9DF",
        bg_input="#FFFFFF",
        accent_bright="#644AC9",
        accent_light="#036A96",
        accent_border="#644AC9",
        accent_brass="#A34D14",
        ember="#CECCC0",
        border_dark="#BCBAB3",
        border_deep="#CECCC0",
        text_primary="#1F1F1F",
        text_muted="#6C664B",
        text_gold="#846E15",
        text_warning="#A34D14",
        text_disabled="#8C866B",
        tab_disabled="#8C866B",
        primary_btn_text="#FFFBEB",
        primary_btn_disabled_text="#6C664B",
        uncommon="#14710A",
        rare="#644AC9",
        danger="#CB3A2A",
        danger_bg="#FDE8E6",
        danger_hover_bg="#FBD0CC",
        danger_pressed_bg="#F7B5AE",
        danger_text="#9E2416",
        danger_hover_text="#7D1B10",
        danger_border_pressed="#7D1B10",
        danger_border_focus="#CB3A2A",
        danger_disabled_bg="#E8E5D8",
        danger_disabled_text="#8C866B",
        tooltip_bg="rgba(255, 251, 235, 0.98)",
        header_bg_start="#DEDCCF",
        header_bg_mid="#EFEDDC",
        header_bg_end="#CECCC0",
        header_top_highlight="#FFFFFF",
        tile_title="#1F1F1F",
        tile_subtitle="#644AC9",
        tile_desc="#2E2D2A",
        tile_meta="#6C664B",
        tile_warning="#A34D14",
        animation="dracula",
    ),
}

BUNDLED_THEME_ORDER: tuple[str, ...] = (
    "arthas",
    "gruvbox-dark",
    "gruvbox-light",
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

THEME_ALIASES: dict[str, str] = {
    "98x": "98x-light",
}

_THEME_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_SAFE_CSS_VALUE_RE = re.compile(
    r"^(#[0-9A-Fa-f]{3,8}|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}"
    r"(?:\s*,\s*(?:0|1|0?\.\d+))?\s*\)|[A-Za-z0-9][A-Za-z0-9_-]{0,31})$"
)
_THEME_SPEC_FIELDS: frozenset[str] = frozenset(f.name for f in dataclasses.fields(ThemeSpec))


def user_themes_dir(config_dir: Path | None = None) -> Path:
    """`<config_dir>/themes` — user-accessible folder for custom `.json` theme files."""
    base = config_dir if config_dir is not None else platform.config_dir()
    return base / "themes"


def parse_theme_dict(raw: Mapping[str, Any], *, fallback_id: str | None = None) -> ThemeSpec:
    """Validate and merge a raw JSON theme mapping onto its base or `arthas` `ThemeSpec`."""
    theme_id = str(raw.get("id") or fallback_id or "").strip().lower()
    if not _THEME_ID_RE.match(theme_id):
        raise ValueError(f"invalid theme id {theme_id!r}")
    base = dataclasses.asdict(THEMES.get(theme_id, THEMES[DEFAULT_THEME]))
    base["id"] = theme_id
    label = raw.get("label")
    base["label"] = (
        str(label).strip()
        if isinstance(label, str) and label.strip()
        else theme_id.replace("-", " ").title()
    )
    for key, value in raw.items():
        if key in ("id", "label") or key not in _THEME_SPEC_FIELDS:
            continue
        if key == "is_light":
            if isinstance(value, bool):
                base[key] = value
        elif isinstance(value, str) and value.strip():
            cleaned_val = value.strip()
            if _SAFE_CSS_VALUE_RE.match(cleaned_val):
                base[key] = cleaned_val
            else:
                logger.warning(f"theme {theme_id!r}: ignoring unsafe token {key}={cleaned_val!r}")
    return ThemeSpec(**base)


def load_theme_file(path: Path) -> ThemeSpec | None:
    """Load one `.json` theme file; returns None if unreadable or invalid."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(raw, dict):
            return None
        return parse_theme_dict(raw, fallback_id=path.stem)
    except (OSError, ValueError, TypeError) as exc:
        logger.warning(f"theme: could not load {path}: {exc}")
        return None


def _load_bundled_themes() -> dict[str, ThemeSpec]:
    """Load bundled `.json` theme files from `resources.themes_dir()`, falling back to `THEMES`."""
    loaded: dict[str, ThemeSpec] = {}
    folder = resources.themes_dir()
    if folder.is_dir():
        for theme_id in BUNDLED_THEME_ORDER:
            candidate = folder / f"{theme_id}.json"
            if candidate.is_file():
                spec = load_theme_file(candidate)
                if spec is not None:
                    loaded[spec.id] = spec
        for extra in sorted(folder.glob("*.json")):
            if extra.stem.lower() not in loaded:
                spec = load_theme_file(extra)
                if spec is not None:
                    loaded[spec.id] = spec
    for theme_id, fallback in THEMES.items():
        loaded.setdefault(theme_id, fallback)
    return loaded


THEMES = _load_bundled_themes()

THEME_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (spec.id, spec.label) for spec in THEMES.values()
)


def sync_user_themes(user_dir: Path | None = None) -> Path:
    """Ensure the user-accessible `themes/` folder exists and holds bundled `.json` defaults."""
    target_dir = user_dir if user_dir is not None else user_themes_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        bundled = resources.themes_dir()
        if bundled.is_dir():
            for src in sorted(bundled.glob("*.json")):
                dest = target_dir / src.name
                content = src.read_text(encoding="utf-8")
                if not dest.exists():
                    dest.write_text(content, encoding="utf-8")
        else:
            for theme_id, spec in THEMES.items():
                dest = target_dir / f"{theme_id}.json"
                if not dest.exists():
                    dest.write_text(
                        json.dumps(dataclasses.asdict(spec), indent=2) + "\n",
                        encoding="utf-8",
                    )
    except OSError as exc:
        logger.debug(f"theme: could not seed user themes dir {target_dir}: {exc}")
    return target_dir


def load_available_themes(
    user_dir: Path | None = None,
    *,
    seed_user_dir: bool = False,
) -> dict[str, ThemeSpec]:
    """Return all available themes (bundled themes + any `.json` files in `user_dir`)."""
    catalog = dict(THEMES)
    if seed_user_dir:
        folder = sync_user_themes(user_dir)
    else:
        folder = user_dir if user_dir is not None else user_themes_dir()
    if folder.is_dir():
        try:
            paths = sorted(folder.glob("*.json"))
        except OSError:
            paths = []
        for path in paths:
            spec = load_theme_file(path)
            if spec is not None:
                catalog[spec.id] = spec
    # Keep canonical bundled order first, followed by custom user themes alphabetically.
    ordered: dict[str, ThemeSpec] = {}
    for theme_id in BUNDLED_THEME_ORDER:
        if theme_id in catalog:
            ordered[theme_id] = catalog.pop(theme_id)
    for theme_id in sorted(catalog):
        ordered[theme_id] = catalog[theme_id]
    return ordered


def available_theme_choices(
    user_dir: Path | None = None,
    *,
    seed_user_dir: bool = False,
) -> tuple[tuple[str, str], ...]:
    """Return `(id, label)` pairs for every bundled and user-defined theme."""
    themes = load_available_themes(user_dir, seed_user_dir=seed_user_dir)
    return tuple((spec.id, spec.label) for spec in themes.values())


def normalize_theme_name(name: object, user_dir: Path | None = None) -> str:
    """Return a canonical theme ID from bundled or user themes, defaulting to `DEFAULT_THEME`."""
    if not isinstance(name, str):
        return DEFAULT_THEME
    cleaned = name.strip().lower()
    cleaned = THEME_ALIASES.get(cleaned, cleaned)
    if cleaned in THEMES:
        return cleaned
    for spec in THEMES.values():
        if spec.label.lower() == cleaned:
            return spec.id
    custom = load_available_themes(user_dir)
    if cleaned in custom:
        return cleaned
    for spec in custom.values():
        if spec.label.lower() == cleaned:
            return spec.id
    return DEFAULT_THEME


def get_theme(name: object = None, user_dir: Path | None = None) -> ThemeSpec:
    """Return the `ThemeSpec` for `name` (checking user themes and bundled `THEMES`)."""
    custom = load_available_themes(user_dir)
    resolved = normalize_theme_name(name, user_dir=user_dir)
    return custom.get(resolved, THEMES[DEFAULT_THEME])


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
"""objectName of the Modules toolbar's "Server build ▾" menu button (T89).

Its label carries the triangle, so its QSS rule takes away the menu indicator the
style would otherwise draw -- and reserve width for -- beside it: two arrows.
"""


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


def _scroller_width(base: int, scale: float) -> str:
    """A tab bar's two scroll arrows side by side: each `_touch()` plus a 1 px border either side.

    Qt's style sheet gives each arrow half of `QTabBar::scroller`'s width.
    """
    return f"{2 * (int(_touch(base, scale).removesuffix('px')) + 2)}px"


def _px(base: int, scale: float) -> str:
    """A font size at `scale`, floored so text never becomes unreadable."""
    return f"{max(MIN_FONT_PX, round(base * scale))}px"


def _theme_images_dir_for(spec: ThemeSpec) -> Path:
    """Return the SVG directory for `spec`, generating tinted assets for non-default themes."""
    base_dir = resources.theme_images_dir()
    if spec.id == DEFAULT_THEME:
        return base_dir
    target_dir = Path(tempfile.gettempdir()) / "yulon-theme-images" / spec.id
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
        return target_dir
    except OSError:
        return base_dir


def _image(name: str, spec: ThemeSpec | None = None) -> str:
    """`url("<path>")` for one of the theme's SVGs (T193), read at build time.

    Quoted, because the path is the install's: `...\\Yu'lon\\...` carries an
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

/* Centered horizontal top menu bar below the header banner */
QWidget#{TOP_MENU_BAR} {{
    background-color: {spec.bg_panel};
    border-bottom: 1px solid {spec.border_dark};
}}

QToolButton#{TOP_MENU_BUTTON},
QToolButton#{SERVERS_MENU_BUTTON},
QToolButton#{SETTINGS_BUTTON} {{
    background-color: transparent;
    color: {spec.text_muted};
    border: 1px solid transparent;
    border-bottom: 2px solid transparent;
    border-radius: 3px;
    padding: 0px 8px;
    font-family: {FONT_TITLE_SINGLE};
    font-size: {_px(13, scale)};
    font-weight: bold;
    min-height: {_touch(28, scale)};
    min-width: 64px;
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
   paints its own backdrop, particles and hover lighting in `paintEvent`, and its
   own sheet makes the card itself transparent and borderless — a widget's own
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
    background-color: {spec.bg_panel if spec.is_light else "rgba(11, 17, 27, 0.86)"};
    color: {spec.text_primary if spec.is_light else "#E6F0F8"};
    border: 1px solid {spec.border_dark};
}}

QFrame[objectName^="catalog-tile-"] QPushButton:hover {{
    background-color: {spec.bg_hover if spec.is_light else "rgba(22, 35, 56, 0.94)"};
    color: {spec.accent_light if spec.is_light else spec.tile_subtitle};
    border: 1px solid {spec.accent_brass};
}}

QFrame[objectName^="catalog-tile-"] QPushButton:disabled {{
    background-color: {spec.bg_container if spec.is_light else "rgba(16, 27, 41, 0.78)"};
    color: {spec.text_disabled if spec.is_light else "#94A8BC"};
    border: 1px solid {spec.border_deep if spec.is_light else "rgba(43, 64, 85, 0.7)"};
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

QFrame[objectName^="catalog-tile-"] QPushButton[primary="true"]:disabled,
QFrame[objectName^="catalog-tile-"] QPushButton[objectName^="install-"]:disabled {{
    background-color: {spec.bg_hover if spec.is_light else "rgba(24, 38, 56, 0.85)"};
    color: {spec.primary_btn_disabled_text if spec.is_light else "#9AB0C4"};
    border: 1px solid {spec.border_deep if spec.is_light else "rgba(52, 76, 102, 0.8)"};
}}

/* ScrollAreas default to QPalette.Base unless styled; make them match the container sheet. */
QScrollArea {{
    background-color: transparent;
    border: none;
}}

QScrollArea > QWidget#qt_scrollarea_viewport {{
    background-color: transparent;
}}
"""


DADCRAFT_THEME_QSS = _build_qss(1.0)
"""The theme at scale 1.0 (the reference width). Tests assert against this;
`apply_dadcraft_theme` re-generates it per window size at runtime."""


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


_SPIN_NUMBER_GAP_PX = 6
"""Clearance (px) the spin-box number keeps from each of its −/+ buttons (T821)."""

_SPIN_GUARD_PROPERTY = "yulon_spin_guarded"
"""Dynamic property marking a spin box as carrying a `_SpinNumberGuard`."""


class _SpinNumberGuard(QObject):
    """Keep a spin box's number field clear of its −/+ buttons (T821).

    Qt 6.11.3's style sheet computes the number field as the spin rect minus
    exactly the two button rects, edge to edge, ignoring the sheet's
    horizontal padding (6.11.2 shrank the field by the 10 px padding plus the
    1 px border, so the number sat 11 px clear). Edge to edge means the
    number's last column IS the up button's first column, which the layout
    test reads as an overlap. The guard re-reads the buttons' real
    sub-control rects from the style on every resize and caps the line
    edit's maximum width so the number ends `_SPIN_NUMBER_GAP_PX` before the
    up button. The cap is a ceiling only: where the style already leaves
    room, the number is untouched.
    """

    def __init__(self, spin: QAbstractSpinBox) -> None:
        super().__init__(spin)
        self._spin = spin
        self._updating = False
        spin.installEventFilter(self)
        spin.lineEdit().installEventFilter(self)
        self._apply()

    def _apply(self) -> None:
        """Cap the number width from the buttons' live rects, when known."""
        spin = self._spin
        option = QStyleOptionSpinBox()
        spin.initStyleOption(option)
        control = QStyle.ComplexControl.CC_SpinBox
        up = spin.style().subControlRect(control, option, QStyle.SubControl.SC_SpinBoxUp, spin)
        edit = spin.lineEdit().geometry()
        if up.width() < TOUCH_TARGET_PX or edit.width() <= 0:
            return
        cap = max(0, up.x() - edit.x() - _SPIN_NUMBER_GAP_PX)
        field = spin.lineEdit()
        if cap < field.maximumWidth():
            self._updating = True
            try:
                field.setMaximumWidth(cap)
            finally:
                self._updating = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Re-cap the number after any resize settles the button rects."""
        if event.type() == QEvent.Type.Resize and not self._updating:
            self._apply()
        return False


def _guard_spin_numbers(root: QWidget) -> None:
    """Cap every spin box under `root` so its number clears its buttons."""
    for spin in root.findChildren(QAbstractSpinBox):
        if spin.property(_SPIN_GUARD_PROPERTY) == "1":
            guard = getattr(spin, "_yulon_spin_guard", None)
            if isinstance(guard, _SpinNumberGuard):
                guard._apply()
            continue
        spin.setProperty(_SPIN_GUARD_PROPERTY, "1")
        spin._yulon_spin_guard = _SpinNumberGuard(spin)  # type: ignore[attr-defined]


class _SpinBoxWatcher(QObject):
    """Guard spin boxes created after the theme went on (T821).

    Dialogs inherit the application sheet, so they never see their own
    `apply_dadcraft_theme` call. The watcher rides on the application and
    guards each spin box the first time it is shown, when its button rects
    are real.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Guard every spin box at its first show."""
        if event.type() == QEvent.Type.Polish:
            if isinstance(watched, QAbstractSpinBox):
                if watched.property(_SPIN_GUARD_PROPERTY) != "1":
                    watched.setProperty(_SPIN_GUARD_PROPERTY, "1")
                    guard = _SpinNumberGuard(watched)
                    watched._yulon_spin_guard = guard  # type: ignore[attr-defined]
        return False


def _ensure_spin_watcher(app: QApplication) -> None:
    """Install the late-spin watcher on `app`, once."""
    if app.property(_SPIN_GUARD_PROPERTY) == "1":
        return
    app.setProperty(_SPIN_GUARD_PROPERTY, "1")
    app.installEventFilter(_SpinBoxWatcher(app))


def resolve_widget_theme(widget: QWidget | None) -> str:
    """Return the theme ID recorded on `widget` or its top-level window, else `DEFAULT_THEME`."""
    node = widget
    while node is not None:
        prop = node.property("yulon_theme")
        if isinstance(prop, str) and prop.strip():
            return normalize_theme_name(prop)
        node = node.parentWidget()
    return DEFAULT_THEME


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

    palette = build_dadcraft_palette(resolved_theme)
    qss = _build_qss(
        scale_for_width(width) if width is not None else 1.0,
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
            header.set_theme(resolved_theme)
        for card in target.findChildren(QWidget):
            if card.objectName().startswith("catalog-tile-"):
                card.update()
