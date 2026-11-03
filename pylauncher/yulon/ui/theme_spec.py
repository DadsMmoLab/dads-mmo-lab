"""Theme token models, bundled baselines, and JSON theme loader for Yu'lon (`style-guide.md` §3).

Houses `ThemeSpec`, the 19 canonical `BUNDLED_THEMES` baselines, user-theme JSON
parsing/validation (`parse_theme_dict`, `load_theme_file`), and user theme folder
synchronization (`sync_user_themes`). Kept free of Qt imports so theme metadata and
JSON validation remain pure data operations.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from yulon import platform, resources
from yulon.log import get_logger

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


BUNDLED_THEMES: dict[str, ThemeSpec] = {
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
        animation="arthas",
    ),
    "organic-dark": ThemeSpec(
        id="organic-dark",
        label="Organic Dark",
        is_light=False,
        bg_dark="#1C1311",
        bg_container="#261916",
        bg_panel="#382420",
        bg_hover="#4D312B",
        bg_input="#140D0B",
        accent_bright="#F26430",
        accent_light="#F7A05B",
        accent_border="#E05A2B",
        accent_brass="#9E3416",
        ember="#3B1D16",
        border_dark="#593831",
        border_deep="#3D2621",
        text_primary="#F4E2D4",
        text_muted="#C9AFA0",
        text_gold="#F7A05B",
        text_warning="#F26430",
        text_disabled="#8C6E63",
        tab_disabled="#8C6E63",
        primary_btn_text="#140D0B",
        primary_btn_disabled_text="#A6887C",
        uncommon="#B8BB26",
        rare="#D3869B",
        danger="#FB4934",
        danger_bg="#3A1614",
        danger_hover_bg="#521C19",
        danger_pressed_bg="#260E0D",
        danger_text="#FBF1E8",
        danger_hover_text="#FFFFFF",
        danger_border_pressed="#9D0006",
        danger_border_focus="#FB4934",
        danger_disabled_bg="#2B1B19",
        danger_disabled_text="#8C6E63",
        tooltip_bg="rgba(28, 19, 17, 0.97)",
        header_bg_start="#160E0C",
        header_bg_mid="#331B15",
        header_bg_end="#1C1311",
        header_top_highlight="#F7A05B",
        tile_title="#F4E2D4",
        tile_subtitle="#F7A05B",
        tile_desc="#F4E2D4",
        tile_meta="#C9AFA0",
        tile_warning="#F26430",
        animation="organic",
    ),
    "organic-light": ThemeSpec(
        id="organic-light",
        label="Organic Light",
        is_light=True,
        bg_dark="#F2DCD0",
        bg_container="#F9ECE4",
        bg_panel="#EBD0C0",
        bg_hover="#FDF4EE",
        bg_input="#FDF6F0",
        accent_bright="#A63218",
        accent_light="#C84B24",
        accent_border="#A63218",
        accent_brass="#B85226",
        ember="#DFBFA8",
        border_dark="#C9A38F",
        border_deep="#DFBFA8",
        text_primary="#2D1814",
        text_muted="#6E473F",
        text_gold="#9E3416",
        text_warning="#A63218",
        text_disabled="#946E64",
        tab_disabled="#946E64",
        primary_btn_text="#FDF6F0",
        primary_btn_disabled_text="#6E473F",
        uncommon="#79740E",
        rare="#8F3F71",
        danger="#9D0006",
        danger_bg="#F8D7DA",
        danger_hover_bg="#F1BFC4",
        danger_pressed_bg="#E8AEB4",
        danger_text="#9D0006",
        danger_hover_text="#7A0004",
        danger_border_pressed="#7A0004",
        danger_border_focus="#9D0006",
        danger_disabled_bg="#EDD5C7",
        danger_disabled_text="#946E64",
        tooltip_bg="rgba(249, 236, 228, 0.98)",
        header_bg_start="#E4C1AD",
        header_bg_mid="#F2DCD0",
        header_bg_end="#D8B29C",
        header_top_highlight="#A63218",
        tile_title="#2D1814",
        tile_subtitle="#A63218",
        tile_desc="#3B211C",
        tile_meta="#6E473F",
        tile_warning="#A63218",
        animation="organic",
    ),
    "gruvbox-dark-soft": ThemeSpec(
        id="gruvbox-dark-soft",
        label="Gruvbox Dark Soft",
        is_light=False,
        bg_dark="#32302F",
        bg_container="#3C3836",
        bg_panel="#504945",
        bg_hover="#665C54",
        bg_input="#282828",
        accent_bright="#FE8019",
        accent_light="#FABD2F",
        accent_border="#D65D0E",
        accent_brass="#AF3A03",
        ember="#3C3836",
        border_dark="#665C54",
        border_deep="#504945",
        text_primary="#EBDBB2",
        text_muted="#D5C4A1",
        text_gold="#FABD2F",
        text_warning="#FE8019",
        text_disabled="#928374",
        tab_disabled="#928374",
        primary_btn_text="#141617",
        primary_btn_disabled_text="#A89984",
        uncommon="#B8BB26",
        rare="#83A598",
        danger="#FB4934",
        danger_bg="#3F211F",
        danger_hover_bg="#542623",
        danger_pressed_bg="#2B1615",
        danger_text="#FBF1C7",
        danger_hover_text="#FFFFFF",
        danger_border_pressed="#9D0006",
        danger_border_focus="#FB4934",
        danger_disabled_bg="#362A29",
        danger_disabled_text="#928374",
        tooltip_bg="rgba(50, 48, 47, 0.97)",
        header_bg_start="#1D2021",
        header_bg_mid="#282828",
        header_bg_end="#1D2021",
        header_top_highlight="#FABD2F",
        tile_title="#EBDBB2",
        tile_subtitle="#FABD2F",
        tile_desc="#EBDBB2",
        tile_meta="#D5C4A1",
        tile_warning="#FE8019",
        animation="gruvbox",
    ),
    "gruvbox-dark-medium": ThemeSpec(
        id="gruvbox-dark-medium",
        label="Gruvbox Dark Medium",
        is_light=False,
        bg_dark="#282828",
        bg_container="#32302F",
        bg_panel="#3C3836",
        bg_hover="#504945",
        bg_input="#1D2021",
        accent_bright="#FE8019",
        accent_light="#FABD2F",
        accent_border="#D65D0E",
        accent_brass="#AF3A03",
        ember="#342014",
        border_dark="#504945",
        border_deep="#3C3836",
        text_primary="#EBDBB2",
        text_muted="#BDAE93",
        text_gold="#FABD2F",
        text_warning="#FE8019",
        text_disabled="#7C6F64",
        tab_disabled="#7C6F64",
        primary_btn_text="#141617",
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
        tooltip_bg="rgba(40, 40, 40, 0.97)",
        header_bg_start="#141617",
        header_bg_mid="#1D2021",
        header_bg_end="#181A1B",
        header_top_highlight="#FABD2F",
        tile_title="#EBDBB2",
        tile_subtitle="#FABD2F",
        tile_desc="#EBDBB2",
        tile_meta="#BDAE93",
        tile_warning="#FE8019",
        animation="gruvbox",
    ),
    "gruvbox-dark-hard": ThemeSpec(
        id="gruvbox-dark-hard",
        label="Gruvbox Dark Hard",
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
        text_muted="#BDAE93",
        text_gold="#FABD2F",
        text_warning="#FE8019",
        text_disabled="#7C6F64",
        tab_disabled="#7C6F64",
        primary_btn_text="#141617",
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
        header_bg_start="#101213",
        header_bg_mid="#181A1B",
        header_bg_end="#141617",
        header_top_highlight="#FABD2F",
        tile_title="#EBDBB2",
        tile_subtitle="#FABD2F",
        tile_desc="#EBDBB2",
        tile_meta="#BDAE93",
        tile_warning="#FE8019",
        animation="gruvbox",
    ),
    "gruvbox-light-soft": ThemeSpec(
        id="gruvbox-light-soft",
        label="Gruvbox Light Soft",
        is_light=True,
        bg_dark="#F2E5BC",
        bg_container="#EBDBB2",
        bg_panel="#D5C4A1",
        bg_hover="#FBF1C7",
        bg_input="#F9F5D7",
        accent_bright="#AF3A03",
        accent_light="#D65D0E",
        accent_border="#AF3A03",
        accent_brass="#B57614",
        ember="#BDAE93",
        border_dark="#A89984",
        border_deep="#BDAE93",
        text_primary="#3C3836",
        text_muted="#504945",
        text_gold="#B57614",
        text_warning="#AF3A03",
        text_disabled="#7C6F64",
        tab_disabled="#7C6F64",
        primary_btn_text="#F9F5D7",
        primary_btn_disabled_text="#504945",
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
        danger_disabled_bg="#E2D3B1",
        danger_disabled_text="#7C6F64",
        tooltip_bg="rgba(242, 229, 188, 0.98)",
        header_bg_start="#F2E5BC",
        header_bg_mid="#F9F5D7",
        header_bg_end="#FBF1C7",
        header_top_highlight="#AF3A03",
        tile_title="#3C3836",
        tile_subtitle="#AF3A03",
        tile_desc="#3C3836",
        tile_meta="#504945",
        tile_warning="#AF3A03",
        animation="gruvbox",
    ),
    "gruvbox-light-medium": ThemeSpec(
        id="gruvbox-light-medium",
        label="Gruvbox Light Medium",
        is_light=True,
        bg_dark="#FBF1C7",
        bg_container="#F2E5BC",
        bg_panel="#EBDBB2",
        bg_hover="#F9F5D7",
        bg_input="#F9F5D7",
        accent_bright="#AF3A03",
        accent_light="#D65D0E",
        accent_border="#AF3A03",
        accent_brass="#B57614",
        ember="#D5C4A1",
        border_dark="#BDAE93",
        border_deep="#D5C4A1",
        text_primary="#3C3836",
        text_muted="#665C54",
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
        header_bg_start="#FBF1C7",
        header_bg_mid="#F9F5D7",
        header_bg_end="#F2E5BC",
        header_top_highlight="#AF3A03",
        tile_title="#3C3836",
        tile_subtitle="#AF3A03",
        tile_desc="#3C3836",
        tile_meta="#665C54",
        tile_warning="#AF3A03",
        animation="gruvbox",
    ),
    "gruvbox-light-hard": ThemeSpec(
        id="gruvbox-light-hard",
        label="Gruvbox Light Hard",
        is_light=True,
        bg_dark="#F9F5D7",
        bg_container="#FBF1C7",
        bg_panel="#EBDBB2",
        bg_hover="#FFFFFF",
        bg_input="#FFFFFF",
        accent_bright="#AF3A03",
        accent_light="#D65D0E",
        accent_border="#AF3A03",
        accent_brass="#B57614",
        ember="#D5C4A1",
        border_dark="#BDAE93",
        border_deep="#D5C4A1",
        text_primary="#282828",
        text_muted="#665C54",
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
        tooltip_bg="rgba(249, 245, 215, 0.98)",
        header_bg_start="#F9F5D7",
        header_bg_mid="#FFFDF0",
        header_bg_end="#FBF1C7",
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
        animation="98x",
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
        animation="ocean",
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
        animation="jungle",
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
        animation="floral",
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
        animation="dracula",
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

THEME_ALIASES: dict[str, str] = {
    "98x": "98x-light",
    "organic": "organic-dark",
    "gruvbox": "gruvbox-dark-medium",
    "gruvbox-dark": "gruvbox-dark-medium",
    "gruvbox-light": "gruvbox-light-medium",
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


def parse_theme_dict(raw: Mapping[str, object], *, fallback_id: str | None = None) -> ThemeSpec:
    """Validate and merge a raw JSON theme mapping onto its base or `arthas` `ThemeSpec`."""
    theme_id = str(raw.get("id") or fallback_id or "").strip().lower()
    if not _THEME_ID_RE.match(theme_id):
        raise ValueError(f"invalid theme id {theme_id!r}")
    base_id = THEME_ALIASES.get(theme_id, theme_id)
    base = dataclasses.asdict(BUNDLED_THEMES.get(base_id, BUNDLED_THEMES[DEFAULT_THEME]))
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
    """Load bundled `.json` files from `resources.themes_dir()`, with `BUNDLED_THEMES` fallback."""
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
    for theme_id, fallback in BUNDLED_THEMES.items():
        loaded.setdefault(theme_id, fallback)
    return loaded


THEMES: dict[str, ThemeSpec] = _load_bundled_themes()

THEME_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (spec.id, spec.label) for spec in THEMES.values()
)


def sync_user_themes(user_dir: Path | None = None) -> Path:
    """Ensure the user-accessible `themes/` folder exists and holds bundled `.json` defaults."""
    target_dir = user_dir if user_dir is not None else user_themes_dir()
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        for legacy_alias in THEME_ALIASES:
            legacy_file = target_dir / f"{legacy_alias}.json"
            if legacy_file.is_file():
                try:
                    raw = json.loads(legacy_file.read_text(encoding="utf-8-sig"))
                    if isinstance(raw, dict) and raw.get("id") == legacy_alias:
                        legacy_file.unlink(missing_ok=True)
                except (OSError, ValueError):
                    pass
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


_ThemeDirSignature = tuple[tuple[str, int, int], ...]
_AVAILABLE_THEMES_CACHE: dict[Path, tuple[_ThemeDirSignature, dict[str, ThemeSpec]]] = {}


def load_available_themes(
    user_dir: Path | None = None,
    *,
    seed_user_dir: bool = False,
) -> dict[str, ThemeSpec]:
    """Return all available themes (bundled themes + any `.json` files in `user_dir`)."""
    if seed_user_dir:
        folder = sync_user_themes(user_dir)
    else:
        folder = user_dir if user_dir is not None else user_themes_dir()
    paths: list[Path] = []
    signature: list[tuple[str, int, int]] = []
    if folder.is_dir():
        try:
            for path in sorted(folder.glob("*.json")):
                st = path.stat()
                paths.append(path)
                signature.append((path.name, st.st_mtime_ns, st.st_size))
        except OSError:
            paths = []
            signature = []
    sig_tuple = tuple(signature)
    cached = _AVAILABLE_THEMES_CACHE.get(folder)
    if cached is not None and cached[0] == sig_tuple:
        return dict(cached[1])

    catalog = dict(THEMES)
    for path in paths:
        spec = load_theme_file(path)
        if spec is not None and spec.id not in THEME_ALIASES:
            catalog[spec.id] = spec
    ordered: dict[str, ThemeSpec] = {}
    for theme_id in BUNDLED_THEME_ORDER:
        if theme_id in catalog:
            ordered[theme_id] = catalog.pop(theme_id)
    for theme_id in sorted(catalog):
        ordered[theme_id] = catalog[theme_id]
    _AVAILABLE_THEMES_CACHE[folder] = (sig_tuple, dict(ordered))
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
