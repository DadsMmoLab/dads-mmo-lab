-- Warrior Talent Data (Class 1)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- ARMS (Spec 1) - Fixed and complete
local Arms = {
    -- Tier 1
    { id = 12282, ranks = {12282, 12663, 12664}, name = "Improved Heroic Strike", icon = "Interface\\Icons\\Ability_Rogue_Ambush", maxRank = 3, tier = 1, col = 1, desc = "Reduces the cost of Heroic Strike by 1 rage." },
    { id = 16462, ranks = {16462, 16463, 16464, 16465, 16466}, name = "Deflection", icon = "Interface\\Icons\\Ability_Parry", maxRank = 5, tier = 1, col = 2, desc = "Increases Parry chance by 1%." },
    { id = 12286, ranks = {12286, 12658}, name = "Improved Rend", icon = "Interface\\Icons\\Ability_Gouge", maxRank = 2, tier = 1, col = 3, desc = "Increases bleed damage of Rend by 10%." },
    -- Tier 2
    { id = 12285, ranks = {12285, 12697}, name = "Improved Charge", icon = "Interface\\Icons\\Ability_Warrior_Charge", maxRank = 2, tier = 2, col = 1, desc = "Increases rage from Charge by 5." },
    { id = 12300, ranks = {12300, 12959, 12960}, name = "Iron Will", icon = "Interface\\Icons\\Spell_Magic_MageArmor", maxRank = 3, tier = 2, col = 2, desc = "Reduces Stun/Charm duration by 10%." },
    { id = 12295, ranks = {12295, 12676, 12677}, name = "Tactical Mastery", icon = "Interface\\Icons\\Spell_Nature_EnchantArmor", maxRank = 3, tier = 2, col = 3, desc = "Retain up to 10 rage when changing stances." },
    -- Tier 3
    { id = 12290, ranks = {12290, 12963}, name = "Improved Overpower", icon = "Interface\\Icons\\Ability_Warrior_Overpower", maxRank = 2, tier = 3, col = 1, desc = "Increases rage from Charge by 5." },
    { id = 12296, ranks = {12296}, name = "Anger Management", icon = "Interface\\Icons\\Spell_Holy_BlessingOfStamina", maxRank = 1, tier = 3, col = 2, desc = "Increases rage decay time by 30%." },
    { id = 16493, ranks = {16493, 16494}, name = "Impale", icon = "Interface\\Icons\\Ability_Searingarrow", maxRank = 2, tier = 3, col = 3, desc = "Increases crit damage bonus by 10%." },
    { id = 12834, ranks = {12834, 12849, 12867}, name = "Deep Wounds", icon = "Interface\\Icons\\Ability_BackStab", maxRank = 3, tier = 3, col = 4, desc = "Critical strikes cause bleeding.", prereq = {3, 3} },
    -- Tier 4
    { id = 12163, ranks = {12163, 12711, 12712}, name = "Two-Handed Weapon Specialization", icon = "Interface\\Icons\\INV_Axe_09", maxRank = 3, tier = 4, col = 2, desc = "Increases 2H weapon damage by 2%." },
    { id = 56636, ranks = {56636, 56637, 56638}, name = "Taste for Blood", icon = "Interface\\Icons\\Ability_Rogue_HungerforBlood", maxRank = 3, tier = 4, col = 3, desc = "Rend allows Overpower to proc." },
    -- Tier 5
    { id = 12700, ranks = {12700, 12781, 12783, 12784, 12785}, name = "Poleaxe Specialization", icon = "Interface\\Icons\\INV_Weapon_Halbard_01", maxRank = 5, tier = 5, col = 1, desc = "Increases crit chance with Axes/Polearms by 1%." },
    { id = 12328, ranks = {12328}, name = "Sweeping Strikes", icon = "Interface\\Icons\\Ability_Rogue_SliceDice", maxRank = 1, tier = 5, col = 2, desc = "Next 5 attacks hit an additional target." },
    { id = 12284, ranks = {12284, 12701, 12702, 12703, 12704}, name = "Mace Specialization", icon = "Interface\\Icons\\INV_Mace_01", maxRank = 5, tier = 5, col = 3, desc = "Increases Mace expertise by 1." },
    { id = 12281, ranks = {12281, 12812, 12813, 12814, 12815}, name = "Sword Specialization", icon = "Interface\\Icons\\INV_Sword_27", maxRank = 5, tier = 5, col = 4, desc = "1% chance for extra attack per point." },
    -- Tier 6
    { id = 20504, ranks = {20504, 20505}, name = "Weapon Mastery", icon = "Interface\\Icons\\Ability_Warrior_WeaponMastery", maxRank = 2, tier = 6, col = 1, desc = "Reduces dodge chance of attacks by 2%." },
    { id = 12289, ranks = {12289, 12668, 23695}, name = "Improved Hamstring", icon = "Interface\\Icons\\Ability_Warrior_Hamstring", maxRank = 3, tier = 6, col = 3, desc = "Gives your Hamstring ability a 5% chance to immobilize the target for 5 sec." },
    { id = 46854, ranks = {46854, 46855}, name = "Trauma", icon = "Interface\\Icons\\Ability_Warrior_Trauma", maxRank = 2, tier = 6, col = 4, desc = "Crits cause 15% more bleed damage." },
    -- Tier 7
    { id = 29834, ranks = {29834, 29838}, name = "Second Wind", icon = "Interface\\Icons\\Ability_Warrior_SecondWind", maxRank = 2, tier = 7, col = 1, desc = "Whenever you are struck by a Stun or Immobilize effect you will generate 10 rage and 5% of your total health over 10 sec." },
    { id = 12294, ranks = {12294}, name = "Mortal Strike", icon = "Interface\\Icons\\Ability_Warrior_SavageBlow", maxRank = 1, tier = 7, col = 2, desc = "Deals weapon damage +380, reduces healing by 50%.", prereq = {5, 2} },
    { id = 46865, ranks = {46865, 46866}, name = "Strength of Arms", icon = "Interface\\Icons\\Ability_Warrior_StrengthOfArms", maxRank = 2, tier = 7, col = 3, desc = "Increases Strength by 2% and health by 4%." },
    { id = 12862, ranks = {12862, 12330}, name = "Improved Slam", icon = "Interface\\Icons\\Ability_Warrior_DecisiveStrike", maxRank = 2, tier = 7, col = 4, desc = "Reduces Slam swing time by 0.5 sec." },
    -- Tier 8
    { id = 64976, ranks = {64976}, name = "Juggernaut", icon = "Interface\\Icons\\Ability_Juggernaut", maxRank = 1, tier = 8, col = 1, desc = "Your Charge ability is now usable while in combat, but the cooldown on Charge is increased by 5 sec. Following a Charge, your next Slam or Mortal Strike has an additional 25% chance to critically hit if used within 10 sec." },
    { id = 35446, ranks = {35446, 35448, 35449}, name = "Improved Mortal Strike", icon = "Interface\\Icons\\Ability_Warrior_SavageBlow", maxRank = 3, tier = 8, col = 2, desc = "Increases Mortal Strike damage by 3%.", prereq = {7, 2} },
    { id = 46859, ranks = {46859, 46860}, name = "Unrelenting Assault", icon = "Interface\\Icons\\Ability_Warrior_UnrelentingAssault", maxRank = 2, tier = 8, col = 3, desc = "Reduces Overpower/Revenge cooldown by 2 sec." },
    -- Tier 9
    { id = 29723, ranks = {29723, 29725, 29724}, name = "Sudden Death", icon = "Interface\\Icons\\Ability_Warrior_SuddenDeath", maxRank = 3, tier = 9, col = 1, desc = "Attacks have chance to allow Execute anytime." },
    { id = 29623, ranks = {29623}, name = "Endless Rage", icon = "Interface\\Icons\\Ability_Warrior_EndlessRage", maxRank = 1, tier = 9, col = 2, desc = "Generates 25% more rage from damage." },
    { id = 29836, ranks = {29836, 29859}, name = "Blood Frenzy", icon = "Interface\\Icons\\Ability_Warrior_BloodFrenzy", maxRank = 2, tier = 9, col = 3, desc = "Increases physical damage taken by target by 2%." },
    -- Tier 10
    { id = 46867, ranks = {46867, 56611, 56612, 56613, 56614}, name = "Wrecking Crew", icon = "Interface\\Icons\\Ability_Warrior_WreckingCrew", maxRank = 5, tier = 10, col = 2, desc = "Your melee critical hits Enrage you, increasing all damage caused by 10% for 12 sec. This effect does not stack with Enrage." },
    -- Tier 11
    { id = 46924, ranks = {46924}, name = "Bladestorm", icon = "Interface\\Icons\\Ability_Warrior_Bladestorm", maxRank = 1, tier = 11, col = 2, desc = "Whirlwind of destruction for 6 sec." },
}

-- FURY (Spec 2)
local Fury = {
    -- Tier 1
    { id = 61216, ranks = {61216, 61221, 61222}, name = "Armored to the Teeth", icon = "Interface\\Icons\\Ability_Warrior_ArmoredToTheTeeth", maxRank = 3, tier = 1, col = 1, desc = "+1 AP per 108 armor." },
    { id = 12321, ranks = {12321, 12835}, name = "Booming Voice", icon = "Interface\\Icons\\Ability_Warrior_BoomingVoice", maxRank = 2, tier = 1, col = 2, desc = "Increases shout range and duration by 25%." },
    { id = 12320, ranks = {12320, 12852, 12853, 12855, 12856}, name = "Cruelty", icon = "Interface\\Icons\\Ability_Rogue_Eviscerate", maxRank = 5, tier = 1, col = 3, desc = "Increases melee crit chance by 1%." },
    -- Tier 2
    { id = 12324, ranks = {12324, 12876, 12877, 12878, 12879}, name = "Improved Demoralizing Shout", icon = "Interface\\Icons\\Ability_Warrior_DemoralizingShout", maxRank = 5, tier = 2, col = 2, desc = "Increases the melee attack power reduction of your Demoralizing Shout by 8%." },
    { id = 12322, ranks = {12322, 12999, 13000, 13001, 13002}, name = "Unbridled Wrath", icon = "Interface\\Icons\\Ability_Warrior_UnbridledWrath", maxRank = 5, tier = 2, col = 3, desc = "Chance to generate extra rage on hit." },
    -- Tier 3
    { id = 12329, ranks = {12329, 12950, 20496}, name = "Improved Cleave", icon = "Interface\\Icons\\Ability_Warrior_Cleave", maxRank = 3, tier = 3, col = 1, desc = "Increases Cleave bonus damage by 40%." },
    { id = 12323, ranks = {12323}, name = "Piercing Howl", icon = "Interface\\Icons\\Ability_Warrior_PiercingHowl", maxRank = 1, tier = 3, col = 2, desc = "Dazes nearby enemies, reducing movement by 50%." },
    { id = 16487, ranks = {16487, 16489, 16492}, name = "Blood Craze", icon = "Interface\\Icons\\Ability_Warrior_BloodCraze", maxRank = 3, tier = 3, col = 3, desc = "Regenerate 3% health over 6 sec after being crit." },
    { id = 12318, ranks = {12318, 12857, 12858, 12860, 12861}, name = "Commanding Presence", icon = "Interface\\Icons\\Ability_Warrior_CommandingPresence", maxRank = 5, tier = 3, col = 4, desc = "Increases shout benefits by 5%." },
    -- Tier 4
    { id = 23584, ranks = {23584, 23585, 23586, 23587, 23588}, name = "Dual Wield Specialization", icon = "Interface\\Icons\\Ability_DualWield", maxRank = 5, tier = 4, col = 1, desc = "Increases offhand damage by 5%." },
    { id = 20502, ranks = {20502, 20503}, name = "Improved Execute", icon = "Interface\\Icons\\INV_Sword_48", maxRank = 2, tier = 4, col = 2, desc = "Reduces Execute rage cost by 2." },
    { id = 12317, ranks = {12317, 13045, 13046, 13047, 13048}, name = "Enrage", icon = "Interface\\Icons\\Ability_Warrior_Enrage", maxRank = 5, tier = 4, col = 3, desc = "+5% damage for 12 sec after being crit." },
    -- Tier 5
    { id = 29590, ranks = {29590, 29591, 29592}, name = "Precision", icon = "Interface\\Icons\\Spell_Nature_AncestralGuardian", maxRank = 3, tier = 5, col = 1, desc = "Berserker Rage generates 10 rage." },
    { id = 12292, ranks = {12292}, name = "Death Wish", icon = "Interface\\Icons\\Spell_Shadow_DeathPact", maxRank = 1, tier = 5, col = 2, desc = "+20% physical damage for 30 sec." },
    { id = 29888, ranks = {29888, 29889}, name = "Improved Intercept", icon = "Interface\\Icons\\Ability_Warrior_Revenge", maxRank = 2, tier = 5, col = 3, desc = "+1% melee hit chance." },
    -- Tier 6
    { id = 20500, ranks = {20500, 20501}, name = "Improved Berserker Rage", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 6, col = 1, desc = "" },
    { id = 12319, ranks = {12319, 12971, 12972, 12973, 12974}, name = "Flurry", icon = "Interface\\Icons\\Ability_GhoulFrenzy", maxRank = 5, tier = 6, col = 3, desc = "+5% attack speed for 3 swings after crit." },
    -- Tier 7
    { id = 46908, ranks = {46908, 46909, 56924}, name = "Intensify Rage", icon = "Interface\\Icons\\Ability_Warrior_FuriousResolve", maxRank = 3, tier = 7, col = 1, desc = "Attacks can reduce healing on target by 25%." },
    { id = 23881, ranks = {23881}, name = "Bloodthirst", icon = "Interface\\Icons\\Spell_Nature_BloodLust", maxRank = 1, tier = 7, col = 2, desc = "Instant attack for 50% AP as damage.", prereq = {5, 2} },
    { id = 29721, ranks = {29721, 29776}, name = "Improved Whirlwind", icon = "Interface\\Icons\\Ability_Whirlwind", maxRank = 2, tier = 7, col = 4, desc = "Reduces Whirlwind cooldown by 1 sec." },
    -- Tier 8
    { id = 46910, ranks = {46910, 46911}, name = "Furious Attacks", icon = "Interface\\Icons\\Ability_Warrior_Bloodsurge", maxRank = 2, tier = 8, col = 1, desc = "Heroic Strike/Bloodthirst can make Slam instant." },
    { id = 29759, ranks = {29759, 29760, 29761, 29762, 29763}, name = "Improved Berserker Stance", icon = "Interface\\Icons\\Ability_Racial_Avatar", maxRank = 5, tier = 8, col = 4, desc = "+4% Strength in Berserker Stance." },
    -- Tier 9
    { id = 60970, ranks = {60970}, name = "Heroic Fury", icon = "Interface\\Icons\\Ability_HeroicLeap", maxRank = 1, tier = 9, col = 1, desc = "Removes immobilization, resets Intercept cooldown." },
    { id = 29801, ranks = {29801}, name = "Rampage", icon = "Interface\\Icons\\Ability_Warrior_Rampage", maxRank = 1, tier = 9, col = 2, desc = "Crits cause rampage, +2% AP for 10 sec.", prereq = {7, 2} },
    { id = 46913, ranks = {46913, 46914, 46915}, name = "Bloodsurge", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 3, desc = "", prereq = {7, 2} },
    -- Tier 10
    { id = 56927, ranks = {56927, 56929, 56930, 56931, 56932}, name = "Unending Fury", icon = "Interface\\Icons\\Ability_Warrior_IntensifyRage", maxRank = 5, tier = 10, col = 2, desc = "+2% damage to Slam/Whirlwind/Bloodthirst." },
    -- Tier 11
    { id = 46917, ranks = {46917}, name = "Titan's Grip", icon = "Interface\\Icons\\Ability_Warrior_TitansGrip", maxRank = 1, tier = 11, col = 2, desc = "Equip 2H weapons in one hand." },
}

-- PROTECTION (Spec 3)
local Protection = {
    -- Tier 1
    { id = 12301, ranks = {12301, 12818}, name = "Improved Bloodrage", icon = "Interface\\Icons\\Ability_Racial_BloodRage", maxRank = 2, tier = 1, col = 1, desc = "+25% instant rage from Bloodrage." },
    { id = 12298, ranks = {12298, 12724, 12725, 12726, 12727}, name = "Shield Specialization", icon = "Interface\\Icons\\INV_Shield_06", maxRank = 5, tier = 1, col = 2, desc = "+1% block chance, generates rage on block." },
    { id = 12287, ranks = {12287, 12665, 12666}, name = "Improved Thunder Clap", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 1, col = 3, desc = "" },
    -- Tier 2
    { id = 50685, ranks = {50685, 50686, 50687}, name = "Incite", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 2, col = 2, desc = "" },
    { id = 12297, ranks = {12297, 12750, 12751, 12752, 12753}, name = "Anticipation", icon = "Interface\\Icons\\Spell_Nature_ThunderClap", maxRank = 5, tier = 2, col = 3, desc = "Reduces Thunder Clap cost by 1 rage." },
    -- Tier 3
    { id = 12975, ranks = {12975}, name = "Last Stand", icon = "Interface\\Icons\\Spell_Holy_AshesToAshes", maxRank = 1, tier = 3, col = 1, desc = "+30% max health for 20 sec." },
    { id = 12797, ranks = {12797, 12799}, name = "Improved Revenge", icon = "Interface\\Icons\\Ability_Warrior_Revenge", maxRank = 2, tier = 3, col = 2, desc = "25% chance Revenge stuns for 3 sec." },
    { id = 29598, ranks = {29598, 29599}, name = "Shield Mastery", icon = "Interface\\Icons\\Ability_ThunderBolt", maxRank = 2, tier = 3, col = 3, desc = "Stuns target for 5 sec." },
    { id = 12299, ranks = {12299, 12761, 12762, 12763, 12764}, name = "Toughness", icon = "Interface\\Icons\\Ability_Warrior_Incite", maxRank = 5, tier = 3, col = 4, desc = "+5% crit chance for Heroic Strike/Thunder Clap/Cleave." },
    -- Tier 4
    { id = 59088, ranks = {59088, 59089}, name = "Improved Spell Reflection", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 1, desc = "" },
    { id = 12313, ranks = {12313, 12804}, name = "Improved Disarm", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 2, desc = "" },
    { id = 12308, ranks = {12308, 12810, 12811}, name = "Puncture", icon = "Interface\\Icons\\Ability_Warrior_ShieldReflection", maxRank = 3, tier = 4, col = 3, desc = "Spell Reflection affects party." },
    -- Tier 5
    { id = 12312, ranks = {12312, 12803}, name = "Improved Disciplines", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 5, col = 1, desc = "" },
    { id = 12809, ranks = {12809}, name = "Concussion Blow", icon = "Interface\\Icons\\Ability_Warrior_ShieldReflection", maxRank = 1, tier = 5, col = 2, desc = "Spell Reflection affects party." },
    { id = 12311, ranks = {12311, 12958}, name = "Gag Order", icon = "Interface\\Icons\\INV_Shield_05", maxRank = 2, tier = 5, col = 3, desc = "+15% block value, reduces Shield Wall cooldown." },
    -- Tier 6
    { id = 16538, ranks = {16538, 16539, 16540, 16541, 16542}, name = "One-Handed Weapon Specialization", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 29593, ranks = {29593, 29594}, name = "Improved Defensive Stance", icon = "Interface\\Icons\\Ability_Warrior_ShieldBash", maxRank = 2, tier = 7, col = 1, desc = "Shield Bash silences for 3 sec, +5% Heroic Throw damage." },
    { id = 50720, ranks = {50720}, name = "Vigilance", icon = "Interface\\Icons\\Spell_Holy_Devotion", maxRank = 1, tier = 7, col = 2, desc = "+2% armor from items, reduces slow effects by 6%.", prereq = {5, 2} },
    { id = 29787, ranks = {29787, 29790, 29792}, name = "Focused Rage", icon = "Interface\\Icons\\Ability_Warrior_DefensiveStance", maxRank = 3, tier = 7, col = 3, desc = "+2% spell damage reduction in Defensive Stance." },
    -- Tier 8
    { id = 29140, ranks = {29140, 29143, 29144}, name = "Vitality", icon = "Interface\\Icons\\Ability_Warrior_CriticalBlock", maxRank = 3, tier = 8, col = 2, desc = "Block can become critical, blocking double." },
    { id = 46945, ranks = {46945, 46949}, name = "Safeguard", icon = "Interface\\Icons\\Ability_Warrior_FocusedRage", maxRank = 2, tier = 8, col = 3, desc = "Reduces rage cost of abilities by 1." },
    -- Tier 9
    { id = 57499, ranks = {57499}, name = "Warbringer", icon = "Interface\\Icons\\Ability_Warrior_SwordAndBoard", maxRank = 1, tier = 9, col = 1, desc = "Devastate/Revenge can reset Shield Slam cooldown." },
    { id = 20243, ranks = {20243}, name = "Devastate", icon = "Interface\\Icons\\INV_Sword_11", maxRank = 1, tier = 9, col = 2, desc = "Sunder Armor + weapon damage." },
    { id = 47294, ranks = {47294, 47295, 47296}, name = "Critical Block", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 3, desc = "" },
    -- Tier 10
    { id = 46951, ranks = {46951, 46952, 46953}, name = "Sword and Board", icon = "Interface\\Icons\\Ability_Warrior_Warbringer", maxRank = 3, tier = 10, col = 2, desc = "Charge/Intercept/Intervene usable in any stance.", prereq = {9, 2} },
    { id = 58872, ranks = {58872, 58874}, name = "Damage Shield", icon = "Interface\\Icons\\Ability_Warrior_Safeguard", maxRank = 2, tier = 10, col = 3, desc = "Intervene reduces target damage taken by 15%." },
    -- Tier 11
    { id = 46968, ranks = {46968}, name = "Shockwave", icon = "Interface\\Icons\\Ability_Warrior_Shockwave", maxRank = 1, tier = 11, col = 2, desc = "Cone stun for 4 sec." },
}

Adv2.Data.Talents[1] = {
    [1] = Arms,
    [2] = Fury,
    [3] = Protection,
}
