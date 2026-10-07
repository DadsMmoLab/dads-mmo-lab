-- Adventurer2 layout + Grimfall-style theme colors
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

Adv2.UI.FRAME_WIDTH = 1040
Adv2.UI.FRAME_HEIGHT = 720
Adv2.UI.TITLE_HEIGHT = 28
Adv2.UI.TOP_TAB_HEIGHT = 78
Adv2.UI.CLASS_TAB_ICON_SIZE = 48
Adv2.UI.CLASS_TAB_SPACING = 14
Adv2.UI.CLASS_TAB_MAX_SPACING = 22
Adv2.UI.CLASS_TAB_SPREAD = 0.82
Adv2.UI.CLASS_TAB_TOP_OFFSET = 10
Adv2.UI.TOP_TAB_Y = -42
Adv2.UI.SPEC_BAR_HEIGHT = 38
Adv2.UI.BOTTOM_BAR_HEIGHT = 36
Adv2.UI.LEFT_COL_WIDTH = 320
Adv2.UI.CENTER_COL_WIDTH = 420
Adv2.UI.RIGHT_COL_WIDTH = 260
Adv2.UI.SEARCH_ROW_HEIGHT = 34
Adv2.UI.SEARCH_ROW_GAP = 5
Adv2.UI.SEARCH_ICON_SIZE = 24
Adv2.UI.RING_TAB_SIZE = 52
Adv2.UI.RING_TAB_ICON_INSET = 7
Adv2.UI.TALENT_BUTTON_SIZE = 32
Adv2.UI.SPELL_GRID_COLS = 1
Adv2.UI.SPELL_ROW_HEIGHT = 40
Adv2.UI.HEIRLOOM_ICON_SIZE = 52
Adv2.UI.HEIRLOOM_GRID_COLS = 8
Adv2.UI.HEIRLOOM_GRID_GAP = 12

-- Grimfall-inspired palette (light tints — texture must stay visible, not crushed to black)
Adv2.UI.Theme = {
    frameBg = { 0.78, 0.70, 0.55, 1.0 },
    frameBorder = { 0.72, 0.58, 0.32, 1 },
    contentFill = { 0.72, 0.64, 0.50, 1.0 },
    tabStripFill = { 0.75, 0.67, 0.52, 1.0 },
    footerFill = { 0.68, 0.60, 0.46, 1.0 },
    leftPanel = { 0.62, 0.52, 0.38, 1.0 },
    leftBorder = { 0.38, 0.30, 0.20, 1 },
    rightPanel = { 0.48, 0.42, 0.34, 1.0 },
    rightBorder = { 0.20, 0.17, 0.13, 1 },
    centerBorder = { 0.28, 0.22, 0.15, 0.85 },
    goldText = { 0.85, 0.75, 0.45 },
    mutedText = { 0.58, 0.54, 0.48 },
    specGlowDefault = { 0.50, 0.08, 0.06, 0.40 },
}

-- Per-spec center glow tint (matches Blizzard talent frame names)
Adv2.UI.SpecGlowTints = {
    DeathKnightBlood = { 0.55, 0.06, 0.06, 0.42 },
    DeathKnightFrost = { 0.08, 0.14, 0.42, 0.38 },
    DeathKnightUnholy = { 0.18, 0.42, 0.10, 0.36 },
    DruidBalance = { 0.35, 0.12, 0.50, 0.36 },
    DruidFeralCombat = { 0.40, 0.28, 0.06, 0.36 },
    DruidRestoration = { 0.08, 0.32, 0.14, 0.36 },
    HunterBeastMastery = { 0.42, 0.28, 0.06, 0.36 },
    HunterMarksmanship = { 0.12, 0.22, 0.40, 0.36 },
    HunterSurvival = { 0.10, 0.30, 0.12, 0.36 },
    MageArcane = { 0.30, 0.10, 0.45, 0.36 },
    MageFire = { 0.50, 0.14, 0.04, 0.40 },
    MageFrost = { 0.08, 0.20, 0.45, 0.38 },
    PaladinHoly = { 0.45, 0.38, 0.10, 0.36 },
    PaladinProtection = { 0.12, 0.18, 0.42, 0.36 },
    PaladinCombat = { 0.45, 0.12, 0.12, 0.38 },
    PriestDiscipline = { 0.35, 0.30, 0.08, 0.34 },
    PriestHoly = { 0.42, 0.36, 0.10, 0.36 },
    PriestShadow = { 0.28, 0.08, 0.38, 0.40 },
    RogueAssassination = { 0.12, 0.38, 0.10, 0.36 },
    RogueCombat = { 0.42, 0.30, 0.06, 0.36 },
    RogueSubtlety = { 0.18, 0.12, 0.38, 0.38 },
    ShamanElementalCombat = { 0.10, 0.22, 0.48, 0.38 },
    ShamanEnhancement = { 0.12, 0.30, 0.38, 0.36 },
    ShamanRestoration = { 0.08, 0.32, 0.18, 0.36 },
    WarlockCurses = { 0.32, 0.08, 0.38, 0.40 },
    WarlockSummoning = { 0.28, 0.14, 0.42, 0.38 },
    WarlockDestruction = { 0.48, 0.12, 0.04, 0.40 },
    WarriorArms = { 0.45, 0.10, 0.06, 0.38 },
    WarriorFury = { 0.48, 0.18, 0.04, 0.40 },
    WarriorProtection = { 0.12, 0.16, 0.38, 0.36 },
}
