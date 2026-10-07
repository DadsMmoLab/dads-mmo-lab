-- Adventurer2 Constants
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}

-- Class IDs matching WoW
Adv2.CLASS_WARRIOR = 1
Adv2.CLASS_PALADIN = 2
Adv2.CLASS_HUNTER = 3
Adv2.CLASS_ROGUE = 4
Adv2.CLASS_PRIEST = 5
Adv2.CLASS_DEATHKNIGHT = 6
Adv2.CLASS_SHAMAN = 7
Adv2.CLASS_MAGE = 8
Adv2.CLASS_WARLOCK = 9
Adv2.CLASS_DRUID = 11
Adv2.CLASS_ADVENTURER = 12

Adv2.ClassFileToId = {
    WARRIOR = 1,
    PALADIN = 2,
    HUNTER = 3,
    ROGUE = 4,
    PRIEST = 5,
    DEATHKNIGHT = 6,
    SHAMAN = 7,
    MAGE = 8,
    WARLOCK = 9,
    DRUID = 11,
    ADVENTURER = 12,
}

-- Race IDs
Adv2.RACE_HUMAN = 1
Adv2.RACE_ORC = 2
Adv2.RACE_DWARF = 3
Adv2.RACE_NIGHTELF = 4
Adv2.RACE_UNDEAD = 5
Adv2.RACE_TAUREN = 6
Adv2.RACE_GNOME = 7
Adv2.RACE_TROLL = 8
Adv2.RACE_BLOODELF = 10
Adv2.RACE_DRAENEI = 11

-- Class definitions with specs
-- Using spec icons as class icons (more reliable in 3.3.5a)
Adv2.Classes = {
    [1] = {
        name = "Warrior",
        icon = "Interface\\Icons\\Ability_Warrior_SavageBlow",
        color = { r = 0.78, g = 0.61, b = 0.43 },
        specs = {
            [1] = { name = "Arms", icon = "Interface\\Icons\\Ability_Warrior_SavageBlow", background = "WarriorArms" },
            [2] = { name = "Fury", icon = "Interface\\Icons\\Ability_Warrior_InnerRage", background = "WarriorFury" },
            [3] = { name = "Protection", icon = "Interface\\Icons\\Ability_Warrior_DefensiveStance", background = "WarriorProtection" },
        }
    },
    [2] = {
        name = "Paladin",
        icon = "Interface\\Icons\\Spell_Holy_HolyBolt",
        color = { r = 0.96, g = 0.55, b = 0.73 },
        specs = {
            [1] = { name = "Holy", icon = "Interface\\Icons\\Spell_Holy_HolyBolt", background = "PaladinHoly" },
            [2] = { name = "Protection", icon = "Interface\\Icons\\Spell_Holy_DevotionAura", background = "PaladinProtection" },
            [3] = { name = "Retribution", icon = "Interface\\Icons\\Spell_Holy_AuraOfLight", background = "PaladinCombat" },
        }
    },
    [3] = {
        name = "Hunter",
        icon = "Interface\\Icons\\Ability_Hunter_BeastTaming",
        color = { r = 0.67, g = 0.83, b = 0.45 },
        specs = {
            [1] = { name = "Beast Mastery", icon = "Interface\\Icons\\Ability_Hunter_BeastTaming", background = "HunterBeastMastery" },
            [2] = { name = "Marksmanship", icon = "Interface\\Icons\\Ability_Marksmanship", background = "HunterMarksmanship" },
            [3] = { name = "Survival", icon = "Interface\\Icons\\Ability_Hunter_SwiftStrike", background = "HunterSurvival" },
        }
    },
    [4] = {
        name = "Rogue",
        icon = "Interface\\Icons\\Ability_Rogue_Eviscerate",
        color = { r = 1.0, g = 0.96, b = 0.41 },
        specs = {
            [1] = { name = "Assassination", icon = "Interface\\Icons\\Ability_Rogue_Eviscerate", background = "RogueAssassination" },
            [2] = { name = "Combat", icon = "Interface\\Icons\\Ability_BackStab", background = "RogueCombat" },
            [3] = { name = "Subtlety", icon = "Interface\\Icons\\Ability_Stealth", background = "RogueSubtlety" },
        }
    },
    [5] = {
        name = "Priest",
        icon = "Interface\\Icons\\Spell_Holy_WordFortitude",
        color = { r = 1.0, g = 1.0, b = 1.0 },
        specs = {
            [1] = { name = "Discipline", icon = "Interface\\Icons\\Spell_Holy_WordFortitude", background = "PriestDiscipline" },
            [2] = { name = "Holy", icon = "Interface\\Icons\\Spell_Holy_GuardianSpirit", background = "PriestHoly" },
            [3] = { name = "Shadow", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordPain", background = "PriestShadow" },
        }
    },
    [6] = {
        name = "Death Knight",
        icon = "Interface\\Icons\\Spell_Deathknight_BloodPresence",
        color = { r = 0.77, g = 0.12, b = 0.23 },
        specs = {
            [1] = { name = "Blood", icon = "Interface\\Icons\\Spell_Deathknight_BloodPresence", background = "DeathKnightBlood" },
            [2] = { name = "Frost", icon = "Interface\\Icons\\Spell_Deathknight_FrostPresence", background = "DeathKnightFrost" },
            [3] = { name = "Unholy", icon = "Interface\\Icons\\Spell_Deathknight_UnholyPresence", background = "DeathKnightUnholy" },
        }
    },
    [7] = {
        name = "Shaman",
        icon = "Interface\\Icons\\Spell_Nature_Lightning",
        color = { r = 0.0, g = 0.44, b = 0.87 },
        specs = {
            [1] = { name = "Elemental", icon = "Interface\\Icons\\Spell_Nature_Lightning", background = "ShamanElementalCombat" },
            [2] = { name = "Enhancement", icon = "Interface\\Icons\\Spell_Nature_LightningShield", background = "ShamanEnhancement" },
            [3] = { name = "Restoration", icon = "Interface\\Icons\\Spell_Nature_MagicImmunity", background = "ShamanRestoration" },
        }
    },
    [8] = {
        name = "Mage",
        icon = "Interface\\Icons\\Spell_Holy_MagicalSentry",
        color = { r = 0.41, g = 0.80, b = 0.94 },
        specs = {
            [1] = { name = "Arcane", icon = "Interface\\Icons\\Spell_Holy_MagicalSentry", background = "MageArcane" },
            [2] = { name = "Fire", icon = "Interface\\Icons\\Spell_Fire_FireBolt02", background = "MageFire" },
            [3] = { name = "Frost", icon = "Interface\\Icons\\Spell_Frost_FrostBolt02", background = "MageFrost" },
        }
    },
    [9] = {
        name = "Warlock",
        icon = "Interface\\Icons\\Spell_Shadow_DeathCoil",
        color = { r = 0.58, g = 0.51, b = 0.79 },
        specs = {
            [1] = { name = "Affliction", icon = "Interface\\Icons\\Spell_Shadow_DeathCoil", background = "WarlockCurses" },
            [2] = { name = "Demonology", icon = "Interface\\Icons\\Spell_Shadow_Metamorphosis", background = "WarlockSummoning" },
            [3] = { name = "Destruction", icon = "Interface\\Icons\\Spell_Shadow_RainOfFire", background = "WarlockDestruction" },
        }
    },
    [11] = {
        name = "Druid",
        icon = "Interface\\Icons\\Spell_Nature_StarFall",
        color = { r = 1.0, g = 0.49, b = 0.04 },
        specs = {
            [1] = { name = "Balance", icon = "Interface\\Icons\\Spell_Nature_StarFall", background = "DruidBalance" },
            [2] = { name = "Feral Combat", icon = "Interface\\Icons\\Ability_Racial_BearForm", background = "DruidFeralCombat" },
            [3] = { name = "Restoration", icon = "Interface\\Icons\\Spell_Nature_HealingTouch", background = "DruidRestoration" },
        }
    },
}

-- Class order for tabs (matching talent calculator)
Adv2.ClassOrder = { 1, 2, 3, 4, 5, 6, 7, 8, 9, 11 }

-- Top tab bar order (Classless-style: Abilities then classes then Heirlooms)
Adv2.TopClassOrder = { 6, 11, 3, 8, 2, 5, 4, 7, 9, 1 }

-- Race data
Adv2.Races = {
    [1] = { name = "Human", faction = "Alliance" },
    [2] = { name = "Orc", faction = "Horde" },
    [3] = { name = "Dwarf", faction = "Alliance" },
    [4] = { name = "Night Elf", faction = "Alliance" },
    [5] = { name = "Undead", faction = "Horde" },
    [6] = { name = "Tauren", faction = "Horde" },
    [7] = { name = "Gnome", faction = "Alliance" },
    [8] = { name = "Troll", faction = "Horde" },
    [10] = { name = "Blood Elf", faction = "Horde" },
    [11] = { name = "Draenei", faction = "Alliance" },
}

-- Simple race name lookup
Adv2.RaceNames = {
    [1] = "Human",
    [2] = "Orc",
    [3] = "Dwarf",
    [4] = "Night Elf",
    [5] = "Undead",
    [6] = "Tauren",
    [7] = "Gnome",
    [8] = "Troll",
    [10] = "Blood Elf",
    [11] = "Draenei",
}

-- UI Constants (merged into Adv2.UI, don't overwrite functions from Helpers.lua)
Adv2.UI = Adv2.UI or {}
Adv2.UI.TALENT_BUTTON_SIZE = 32
Adv2.UI.TALENT_BUTTON_SPACING = 8
Adv2.UI.SPEC_PANEL_WIDTH = 200
Adv2.UI.SPEC_PANEL_HEIGHT = 500
Adv2.UI.TIER_COUNT = 11  -- Max tiers in a talent tree
Adv2.UI.COLS_PER_TIER = 4  -- Max columns per tier

-- Configuration
Adv2.Config = {
    -- Core Abilities (Level 1: 5, then +1 per level, no max)
    INITIAL_ABILITY_PICKS = 5,
    ABILITY_PICKS_PER_LEVEL = 1,
    
    -- Specialization Points (Level 1: 3, then +2 per level, no max)
    INITIAL_TALENT_POINTS = 3,
    TALENT_POINTS_PER_LEVEL = 2,
    
    -- Racial Picks (Level 1: 3, no additional, max 3)
    INITIAL_RACIAL_PICKS = 3,
    MAX_RACIAL_PICKS = 3,
    
    ADDON_PREFIX = "ADV2",
    DEBUG = false,

    -- Multiclass / stock AzerothCore package: client-only (Talented-style)
    CLIENT_ONLY = true,
}

-- Heirloom weapons and gear (3.3.5a)
-- These are the actual heirloom item IDs from WotLK
Adv2.Heirlooms = {
    -- ====== WEAPONS ======
    -- One-Handed
    { id = 42944, name = "Balanced Heartseeker", icon = "Interface\\Icons\\INV_Weapon_ShortBlade_37", slot = "1H Dagger", stats = "Agility" },
    { id = 42945, name = "Venerable Dal'Rend's Sacred Charge", icon = "Interface\\Icons\\INV_Sword_04", slot = "1H Sword", stats = "Strength" },
    { id = 42948, name = "Devout Aurastone Hammer", icon = "Interface\\Icons\\INV_Hammer_30", slot = "1H Mace", stats = "Intellect" },
    { id = 48716, name = "Venerable Mass of McGowan", icon = "Interface\\Icons\\INV_Mace_118", slot = "1H Mace", stats = "Strength" },
    
    -- Two-Handed
    { id = 42943, name = "Bloodied Arcanite Reaper", icon = "Interface\\Icons\\INV_Axe_86", slot = "2H Axe", stats = "Strength" },
    { id = 42947, name = "Dignified Headmaster's Charge", icon = "Interface\\Icons\\INV_Staff_30", slot = "2H Staff", stats = "Intellect/Spirit" },
    { id = 48718, name = "Repurposed Lava Dredger", icon = "Interface\\Icons\\INV_Mace_119", slot = "2H Mace", stats = "Strength" },
    { id = 44095, name = "Grand Staff of Jordan", icon = "Interface\\Icons\\INV_Staff_33", slot = "2H Staff", stats = "Intellect" },
    
    -- Ranged
    { id = 42946, name = "Charmed Ancient Bone Bow", icon = "Interface\\Icons\\INV_Weapon_Bow_07", slot = "Bow", stats = "Agility" },
    { id = 44093, name = "Upgraded Dwarven Hand Cannon", icon = "Interface\\Icons\\INV_Weapon_Rifle_20", slot = "Gun", stats = "Agility" },
    
    -- ====== ARMOR ======
    -- Cloth
    { id = 42984, name = "Preened Ironfeather Shoulders", icon = "Interface\\Icons\\INV_Shoulder_25", slot = "Shoulder", stats = "Intellect/Spirit" },
    { id = 42985, name = "Tattered Dreadmist Mantle", icon = "Interface\\Icons\\INV_Shoulder_02", slot = "Shoulder", stats = "Intellect/Spell Power" },
    { id = 48691, name = "Tattered Dreadmist Robe", icon = "Interface\\Icons\\INV_Chest_Cloth_43", slot = "Chest", stats = "Intellect/Spell Power" },
    { id = 48687, name = "Preened Ironfeather Breastplate", icon = "Interface\\Icons\\INV_Chest_Leather_09", slot = "Chest", stats = "Intellect/Spirit" },
    
    -- Leather
    { id = 42952, name = "Stained Shadowcraft Spaulders", icon = "Interface\\Icons\\INV_Shoulder_05", slot = "Shoulder", stats = "Agility" },
    { id = 44105, name = "Lasting Feralheart Spaulders", icon = "Interface\\Icons\\INV_Shoulder_25", slot = "Shoulder", stats = "Intellect/Spirit" },
    { id = 48689, name = "Stained Shadowcraft Tunic", icon = "Interface\\Icons\\INV_Chest_Leather_08", slot = "Chest", stats = "Agility" },
    
    -- Mail
    { id = 42950, name = "Champion Herod's Shoulder", icon = "Interface\\Icons\\INV_Shoulder_09", slot = "Shoulder", stats = "Strength" },
    { id = 42951, name = "Mystical Pauldrons of Elements", icon = "Interface\\Icons\\INV_Shoulder_14", slot = "Shoulder", stats = "Intellect" },
    { id = 48677, name = "Champion's Deathdealer Breastplate", icon = "Interface\\Icons\\INV_Chest_Chain_05", slot = "Chest", stats = "Agility" },
    
    -- Plate
    { id = 42949, name = "Polished Spaulders of Valor", icon = "Interface\\Icons\\INV_Shoulder_22", slot = "Shoulder", stats = "Strength" },
    { id = 48685, name = "Polished Breastplate of Valor", icon = "Interface\\Icons\\INV_Chest_Plate01", slot = "Chest", stats = "Strength" },
    
    -- ====== TRINKETS ======
    { id = 42991, name = "Swift Hand of Justice", icon = "Interface\\Icons\\INV_Jewelry_Talisman_01", slot = "Trinket", stats = "Haste + Heal on Kill" },
    { id = 42992, name = "Discerning Eye of the Beast", icon = "Interface\\Icons\\INV_Jewelry_Talisman_08", slot = "Trinket", stats = "Intellect + Mana on Kill" },
}

-- Professions (exclude cooking, first aid, fishing)
Adv2.ProfessionCrafting = {
    { id = 2259,  name = "Alchemy",        icon = "Interface\\Icons\\Trade_Alchemy" },
    { id = 2018,  name = "Blacksmithing",  icon = "Interface\\Icons\\Trade_BlackSmithing" },
    { id = 7411,  name = "Enchanting",     icon = "Interface\\Icons\\Trade_Engraving" },
    { id = 4036,  name = "Engineering",    icon = "Interface\\Icons\\Trade_Engineering" },
    { id = 45357, name = "Inscription",    icon = "Interface\\Icons\\INV_Inscription_Tradeskill01" },
    { id = 25229, name = "Jewelcrafting",  icon = "Interface\\Icons\\INV_Misc_Gem_01" },
    { id = 2108,  name = "Leatherworking", icon = "Interface\\Icons\\Trade_LeatherWorking" },
    { id = 3908,  name = "Tailoring",      icon = "Interface\\Icons\\Trade_Tailoring" },
}

Adv2.ProfessionGathering = {
    { id = 2366, name = "Herbalism", icon = "Interface\\Icons\\Trade_Herbalism" },
    { id = 2575, name = "Mining",    icon = "Interface\\Icons\\Trade_Mining" },
    { id = 8613, name = "Skinning",  icon = "Interface\\Icons\\INV_Misc_Pelt_Wolf_01" },
}
