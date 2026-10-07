-- Adventurer2 Talent Data - Base Setup
-- Individual class talent trees are in Talents_*.lua files
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}

-- Initialize talents table (class files will populate this)
Adv2.Data.Talents = Adv2.Data.Talents or {}

--[[
    Talent structure:
    {
        id = spellId,           -- The spell ID to learn
        name = "Talent Name",   -- Display name
        icon = "path",          -- Icon path
        maxRank = 5,           -- Max points (1-5)
        ranks = {id1, id2...}, -- Spell IDs for each rank (if ranked)
        tier = 1,              -- Row (1-11)
        col = 1,               -- Column (1-4)
        prereq = nil,          -- {tier, col} of prerequisite talent or nil
        desc = "...",          -- Tooltip description
    }
]]

-- Core class spells (non-talent abilities) organized by class
-- These are the baseline spells that should be selectable
Adv2.Data.CoreSpells = {
    [1] = {  -- Warrior
        { id = 100, name = "Charge", icon = "Interface\\Icons\\Ability_Warrior_Charge", level = 1 },
        { id = 78, name = "Heroic Strike", icon = "Interface\\Icons\\Ability_Rogue_Ambush", level = 1 },
        { id = 772, name = "Rend", icon = "Interface\\Icons\\Ability_Gouge", level = 4 },
        { id = 6673, name = "Battle Shout", icon = "Interface\\Icons\\Ability_Warrior_BattleShout", level = 1 },
        { id = 1715, name = "Hamstring", icon = "Interface\\Icons\\Ability_ShockWave", level = 8 },
        { id = 7384, name = "Overpower", icon = "Interface\\Icons\\Ability_MeleeDamage", level = 12 },
        { id = 355, name = "Taunt", icon = "Interface\\Icons\\Spell_Nature_Reincarnation", level = 10 },
        { id = 1160, name = "Demoralizing Shout", icon = "Interface\\Icons\\Ability_Warrior_WarCry", level = 14 },
        { id = 6552, name = "Pummel", icon = "Interface\\Icons\\INV_Gauntlets_04", level = 38 },
        { id = 5246, name = "Intimidating Shout", icon = "Interface\\Icons\\Ability_GolemThunderClap", level = 22 },
        { id = 20230, name = "Retaliation", icon = "Interface\\Icons\\Ability_Warrior_Challange", level = 20 },
        { id = 1719, name = "Recklessness", icon = "Interface\\Icons\\Ability_CriticalStrike", level = 50 },
        { id = 871, name = "Shield Wall", icon = "Interface\\Icons\\Ability_Warrior_ShieldWall", level = 28 },
        { id = 2565, name = "Shield Block", icon = "Interface\\Icons\\Ability_Defend", level = 16 },
        { id = 676, name = "Disarm", icon = "Interface\\Icons\\Ability_Warrior_Disarm", level = 18 },
        { id = 1680, name = "Whirlwind", icon = "Interface\\Icons\\Ability_Whirlwind", level = 36 },
        { id = 5308, name = "Execute", icon = "Interface\\Icons\\INV_Sword_48", level = 24 },
        { id = 20252, name = "Intercept", icon = "Interface\\Icons\\Ability_Rogue_Sprint", level = 30 },
        { id = 3411, name = "Intervene", icon = "Interface\\Icons\\Ability_Warrior_Intervene", level = 70 },
        { id = 57755, name = "Heroic Throw", icon = "Interface\\Icons\\INV_Axe_66", level = 80 },
        { id = 1464, name = "Slam", icon = "Interface\\Icons\\Ability_Warrior_DecisiveStrike", level = 30 },
        { id = 2457, name = "Battle Stance", icon = "Interface\\Icons\\Ability_Warrior_OffensiveStance", level = 1 },
        { id = 71, name = "Defensive Stance", icon = "Interface\\Icons\\Ability_Warrior_DefensiveStance", level = 10 },
        { id = 2458, name = "Berserker Stance", icon = "Interface\\Icons\\Ability_Racial_Avatar", level = 30 },
    },
    [2] = {  -- Paladin
        { id = 635, name = "Holy Light", icon = "Interface\\Icons\\Spell_Holy_HolyBolt", level = 1 },
        { id = 20154, name = "Seal of Righteousness", icon = "Interface\\Icons\\Ability_ThunderBolt", level = 1 },
        { id = 19740, name = "Blessing of Might", icon = "Interface\\Icons\\Spell_Holy_FistOfJustice", level = 4 },
        { id = 853, name = "Hammer of Justice", icon = "Interface\\Icons\\Spell_Holy_SealOfMight", level = 8 },
        { id = 19750, name = "Flash of Light", icon = "Interface\\Icons\\Spell_Holy_FlashHeal", level = 20 },
        { id = 498, name = "Divine Protection", icon = "Interface\\Icons\\Spell_Holy_Restoration", level = 6 },
        { id = 642, name = "Divine Shield", icon = "Interface\\Icons\\Spell_Holy_DivineIntervention", level = 34 },
        { id = 31884, name = "Avenging Wrath", icon = "Interface\\Icons\\Spell_Holy_AvengeWrath", level = 70 },
    },
    [3] = {  -- Hunter
        { id = 75, name = "Auto Shot", icon = "Interface\\Icons\\Ability_Whirlwind", level = 1 },
        { id = 3044, name = "Arcane Shot", icon = "Interface\\Icons\\Ability_ImpalingBolt", level = 6 },
        { id = 1978, name = "Serpent Sting", icon = "Interface\\Icons\\Ability_Hunter_Quickshot", level = 4 },
        { id = 5116, name = "Concussive Shot", icon = "Interface\\Icons\\Spell_Frost_Stun", level = 8 },
        { id = 1513, name = "Scare Beast", icon = "Interface\\Icons\\Ability_Druid_Cower", level = 14 },
        { id = 781, name = "Disengage", icon = "Interface\\Icons\\Ability_Rogue_Feint", level = 20 },
        { id = 5384, name = "Feign Death", icon = "Interface\\Icons\\Ability_Rogue_FeignDeath", level = 30 },
        { id = 34026, name = "Kill Command", icon = "Interface\\Icons\\Ability_Hunter_KillCommand", level = 66 },
    },
    [4] = {  -- Rogue
        { id = 1752, name = "Sinister Strike", icon = "Interface\\Icons\\Spell_Shadow_RitualOfSacrifice", level = 1 },
        { id = 1784, name = "Stealth", icon = "Interface\\Icons\\Ability_Stealth", level = 1 },
        { id = 2098, name = "Eviscerate", icon = "Interface\\Icons\\Ability_Rogue_Eviscerate", level = 1 },
        { id = 1766, name = "Kick", icon = "Interface\\Icons\\Ability_Kick", level = 12 },
        { id = 1856, name = "Vanish", icon = "Interface\\Icons\\Ability_Vanish", level = 22 },
        { id = 2094, name = "Blind", icon = "Interface\\Icons\\Spell_Shadow_MindSteal", level = 34 },
        { id = 1725, name = "Distract", icon = "Interface\\Icons\\Ability_Rogue_Distract", level = 22 },
        { id = 8647, name = "Expose Armor", icon = "Interface\\Icons\\Ability_Warrior_Riposte", level = 14 },
    },
    [5] = {  -- Priest
        { id = 585, name = "Smite", icon = "Interface\\Icons\\Spell_Holy_HolySmite", level = 1 },
        { id = 589, name = "Shadow Word: Pain", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordPain", level = 4 },
        { id = 2061, name = "Flash Heal", icon = "Interface\\Icons\\Spell_Holy_FlashHeal", level = 20 },
        { id = 17, name = "Power Word: Shield", icon = "Interface\\Icons\\Spell_Holy_PowerWordShield", level = 6 },
        { id = 139, name = "Renew", icon = "Interface\\Icons\\Spell_Holy_Renew", level = 8 },
        { id = 2050, name = "Lesser Heal", icon = "Interface\\Icons\\Spell_Holy_LesserHeal", level = 1 },
        { id = 8092, name = "Mind Blast", icon = "Interface\\Icons\\Spell_Shadow_UnholyFrenzy", level = 10 },
        { id = 586, name = "Fade", icon = "Interface\\Icons\\Spell_Magic_LesserInvisibilty", level = 24 },
    },
    [6] = {  -- Death Knight
        { id = 45477, name = "Icy Touch", icon = "Interface\\Icons\\Spell_DeathKnight_IceTouch", level = 55 },
        { id = 45462, name = "Plague Strike", icon = "Interface\\Icons\\Spell_DeathKnight_EmpowerRuneBlade", level = 55 },
        { id = 49998, name = "Death Strike", icon = "Interface\\Icons\\Spell_DeathKnight_Butcher2", level = 56 },
        { id = 47528, name = "Mind Freeze", icon = "Interface\\Icons\\Spell_DeathKnight_MindFreeze", level = 57 },
        { id = 48265, name = "Unholy Presence", icon = "Interface\\Icons\\Spell_DeathKnight_UnholyPresence", level = 70 },
        { id = 48263, name = "Frost Presence", icon = "Interface\\Icons\\Spell_DeathKnight_FrostPresence", level = 61 },
        { id = 48266, name = "Blood Presence", icon = "Interface\\Icons\\Spell_DeathKnight_BloodPresence", level = 64 },
        { id = 49576, name = "Death Grip", icon = "Interface\\Icons\\Spell_DeathKnight_Strangulate", level = 55 },
    },
    [7] = {  -- Shaman
        { id = 403, name = "Lightning Bolt", icon = "Interface\\Icons\\Spell_Nature_Lightning", level = 1 },
        { id = 8042, name = "Earth Shock", icon = "Interface\\Icons\\Spell_Nature_EarthShock", level = 4 },
        { id = 331, name = "Healing Wave", icon = "Interface\\Icons\\Spell_Nature_MagicImmunity", level = 1 },
        { id = 8017, name = "Rockbiter Weapon", icon = "Interface\\Icons\\Spell_Nature_RockBiter", level = 1 },
        { id = 2484, name = "Earthbind Totem", icon = "Interface\\Icons\\Spell_Nature_StrengthOfEarthTotem02", level = 6 },
        { id = 370, name = "Purge", icon = "Interface\\Icons\\Spell_Nature_Purge", level = 12 },
        { id = 2008, name = "Ancestral Spirit", icon = "Interface\\Icons\\Spell_Nature_Regenerate", level = 12 },
        { id = 57994, name = "Wind Shear", icon = "Interface\\Icons\\Spell_Nature_Cyclone", level = 16 },
    },
    [8] = {  -- Mage
        { id = 133, name = "Fireball", icon = "Interface\\Icons\\Spell_Fire_FlameBolt", level = 1 },
        { id = 116, name = "Frostbolt", icon = "Interface\\Icons\\Spell_Frost_FrostBolt02", level = 4 },
        { id = 5143, name = "Arcane Missiles", icon = "Interface\\Icons\\Spell_Nature_Starfall", level = 8 },
        { id = 118, name = "Polymorph", icon = "Interface\\Icons\\Spell_Nature_Polymorph", level = 8 },
        { id = 122, name = "Frost Nova", icon = "Interface\\Icons\\Spell_Frost_FrostNova", level = 10 },
        { id = 1953, name = "Blink", icon = "Interface\\Icons\\Spell_Arcane_Blink", level = 20 },
        { id = 12051, name = "Evocation", icon = "Interface\\Icons\\Spell_Nature_Purge", level = 20 },
        { id = 45438, name = "Ice Block", icon = "Interface\\Icons\\Spell_Frost_Frost", level = 30 },
    },
    [9] = {  -- Warlock
        { id = 686, name = "Shadow Bolt", icon = "Interface\\Icons\\Spell_Shadow_ShadowBolt", level = 1 },
        { id = 172, name = "Corruption", icon = "Interface\\Icons\\Spell_Shadow_AbominationExplosion", level = 4 },
        { id = 687, name = "Demon Skin", icon = "Interface\\Icons\\Spell_Shadow_RagingScream", level = 1 },
        { id = 688, name = "Summon Imp", icon = "Interface\\Icons\\Spell_Shadow_SummonImp", level = 1 },
        { id = 5782, name = "Fear", icon = "Interface\\Icons\\Spell_Shadow_Possession", level = 8 },
        { id = 6201, name = "Create Healthstone", icon = "Interface\\Icons\\INV_Stone_04", level = 10 },
        { id = 1120, name = "Drain Soul", icon = "Interface\\Icons\\Spell_Shadow_Haunting", level = 14 },
        { id = 5697, name = "Unending Breath", icon = "Interface\\Icons\\Spell_Shadow_DemonBreath", level = 16 },
    },
    [11] = {  -- Druid
        { id = 5176, name = "Wrath", icon = "Interface\\Icons\\Spell_Nature_AbolishMagic", level = 1 },
        { id = 8921, name = "Moonfire", icon = "Interface\\Icons\\Spell_Nature_StarFall", level = 4 },
        { id = 5185, name = "Healing Touch", icon = "Interface\\Icons\\Spell_Nature_HealingTouch", level = 1 },
        { id = 774, name = "Rejuvenation", icon = "Interface\\Icons\\Spell_Nature_Rejuvenation", level = 4 },
        { id = 768, name = "Cat Form", icon = "Interface\\Icons\\Ability_Druid_CatForm", level = 20 },
        { id = 5487, name = "Bear Form", icon = "Interface\\Icons\\Ability_Racial_BearForm", level = 10 },
        { id = 783, name = "Travel Form", icon = "Interface\\Icons\\Ability_Druid_TravelForm", level = 16 },
        { id = 29166, name = "Innervate", icon = "Interface\\Icons\\Spell_Nature_Lightning", level = 40 },
    },
}

-- Function to get talent data for a class/spec
function Adv2.Data.GetTalents(classId, specIndex)
    if Adv2.Data.Talents[classId] and Adv2.Data.Talents[classId][specIndex] then
        return Adv2.Data.Talents[classId][specIndex]
    end
    return {}
end

-- Function to get all core spells for a class
function Adv2.Data.GetCoreSpells(classId)
    return Adv2.Data.CoreSpells[classId] or {}
end

-- Function to count talents in a spec
function Adv2.Data.CountTalentsInSpec(classId, specIndex)
    local talents = Adv2.Data.GetTalents(classId, specIndex)
    return #talents
end

-- Function to get max tier in a spec
function Adv2.Data.GetMaxTier(classId, specIndex)
    local talents = Adv2.Data.GetTalents(classId, specIndex)
    local maxTier = 0
    for _, talent in ipairs(talents) do
        if talent.tier > maxTier then
            maxTier = talent.tier
        end
    end
    return maxTier
end
