-- Rogue Talent Data (Class 4)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- ASSASSINATION (Spec 1)
local Assassination = {
    -- Tier 1
    { id = 14162, ranks = {14162, 14163, 14164}, name = "Improved Eviscerate", icon = "Interface\\Icons\\Ability_Rogue_Eviscerate", maxRank = 3, tier = 1, col = 1, desc = "+5% Eviscerate damage." },
    { id = 14144, ranks = {14144, 14148}, name = "Remorseless Attacks", icon = "Interface\\Icons\\Ability_FiegnDead", maxRank = 2, tier = 1, col = 2, desc = "+20% crit after kill." },
    { id = 14138, ranks = {14138, 14139, 14140, 14141, 14142}, name = "Malice", icon = "Interface\\Icons\\Ability_Racial_BeastSlaying", maxRank = 5, tier = 1, col = 3, desc = "+1% crit." },
    -- Tier 2
    { id = 14156, ranks = {14156, 14160, 14161}, name = "Ruthlessness", icon = "Interface\\Icons\\Ability_Druid_Disembowel", maxRank = 3, tier = 2, col = 1, desc = "20% chance to add combo point on finisher." },
    { id = 51632, ranks = {51632, 51633}, name = "Blood Spatter", icon = "Interface\\Icons\\Ability_Rogue_Murder", maxRank = 2, tier = 2, col = 2, desc = "+2% damage." },
    { id = 13733, ranks = {13733, 13865, 13866}, name = "Puncturing Wounds", icon = "Interface\\Icons\\Ability_Rogue_BloodSpatter", maxRank = 3, tier = 2, col = 4, desc = "+15% Garrote/Rupture damage." },
    -- Tier 3
    { id = 14983, ranks = {14983}, name = "Vigor", icon = "Interface\\Icons\\Spell_Ice_Lament", maxRank = 1, tier = 3, col = 1, desc = "Next attack is guaranteed crit." },
    { id = 14168, ranks = {14168, 14169}, name = "Improved Expose Armor", icon = "Interface\\Icons\\Ability_CriticalStrike", maxRank = 2, tier = 3, col = 2, desc = "+6% crit damage bonus." },
    { id = 14128, ranks = {14128, 14132, 14135, 14136, 14137}, name = "Lethality", icon = "Interface\\Icons\\Ability_Rogue_FeignDeath", maxRank = 5, tier = 3, col = 3, desc = "+7% poison damage.", prereq = {1, 3} },
    -- Tier 4
    { id = 16513, ranks = {16513, 16514, 16515}, name = "Vile Poisons", icon = "Interface\\Icons\\Ability_Poisons", maxRank = 3, tier = 4, col = 2, desc = "+4% poison apply chance." },
    { id = 14113, ranks = {14113, 14114, 14115, 14116, 14117}, name = "Improved Poisons", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 4, col = 3, desc = "" },
    -- Tier 5
    { id = 31208, ranks = {31208, 31209}, name = "Fleet Footed", icon = "Interface\\Icons\\Ability_Rogue_KidneyShot", maxRank = 2, tier = 5, col = 1, desc = "+3% damage vs Kidney Shot targets." },
    { id = 14177, ranks = {14177}, name = "Cold Blood", icon = "Interface\\Icons\\Ability_Rogue_MasterOfSubtlety", maxRank = 1, tier = 5, col = 2, desc = "+30% damage from stealth for 6 sec." },
    { id = 14174, ranks = {14174, 14175, 14176}, name = "Improved Kidney Shot", icon = "Interface\\Icons\\Ability_Rogue_QuickRecovery", maxRank = 3, tier = 5, col = 3, desc = "50% energy refund on failed finisher." },
    { id = 31244, ranks = {31244, 31245}, name = "Quick Recovery", icon = "Interface\\Icons\\Spell_Shadow_ChillTouch", maxRank = 2, tier = 5, col = 4, desc = "Crits add combo point." },
    -- Tier 6
    { id = 14186, ranks = {14186, 14190, 14193, 14194, 14195}, name = "Seal Fate", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 6, col = 2, desc = "", prereq = {5, 2} },
    { id = 14158, ranks = {14158, 14159}, name = "Murder", icon = "Interface\\Icons\\Ability_Rogue_Sprint", maxRank = 2, tier = 6, col = 3, desc = "+8% movement, -15% fall damage." },
    -- Tier 7
    { id = 51625, ranks = {51625, 51626}, name = "Deadly Brew", icon = "Interface\\Icons\\Ability_Rogue_DeadenedNerves", maxRank = 2, tier = 7, col = 1, desc = "-2% damage taken." },
    { id = 58426, ranks = {58426}, name = "Overkill", icon = "Interface\\Icons\\Ability_Creature_Poison_03", maxRank = 1, tier = 7, col = 2, desc = "Deadly Poison applies Crippling." },
    { id = 31380, ranks = {31380, 31382, 31383}, name = "Deadened Nerves", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 3, desc = "" },
    -- Tier 8
    { id = 51634, ranks = {51634, 51635, 51636}, name = "Focused Attacks", icon = "Interface\\Icons\\Ability_Rogue_Murder", maxRank = 3, tier = 8, col = 1, desc = "+2% damage." },
    { id = 31234, ranks = {31234, 31235, 31236}, name = "Find Weakness", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 8, col = 3, desc = "" },
    -- Tier 9
    { id = 31226, ranks = {31226, 31227, 58410}, name = "Master Poisoner", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 1, desc = "" },
    { id = 1329, ranks = {1329}, name = "Mutilate", icon = "Interface\\Icons\\Ability_Rogue_ShadowStrikes", maxRank = 1, tier = 9, col = 2, desc = "Dual-wield attack, extra damage vs poisoned.", prereq = {7, 2} },
    { id = 51627, ranks = {51627, 51628, 51629}, name = "Turn the Tables", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 3, desc = "" },
    -- Tier 10
    { id = 51664, ranks = {51664, 51665, 51667, 51668, 51669}, name = "Cut to the Chase", icon = "Interface\\Icons\\Ability_Rogue_FocusedAttacks", maxRank = 5, tier = 10, col = 2, desc = "Crits restore 2 energy." },
    -- Tier 11
    { id = 51662, ranks = {51662}, name = "Hunger For Blood", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 11, col = 2, desc = "" },
}

-- COMBAT (Spec 2)
local Combat = {
    -- Tier 1
    { id = 13741, ranks = {13741, 13793, 13792}, name = "Improved Gouge", icon = "Interface\\Icons\\Ability_Gouge", maxRank = 3, tier = 1, col = 1, desc = "+0.5 sec Gouge duration." },
    { id = 13732, ranks = {13732, 13863}, name = "Improved Sinister Strike", icon = "Interface\\Icons\\Spell_Shadow_RitualOfSacrifice", maxRank = 2, tier = 1, col = 2, desc = "-3 energy cost." },
    { id = 13715, ranks = {13715, 13848, 13849, 13851, 13852}, name = "Dual Wield Specialization", icon = "Interface\\Icons\\Ability_DualWield", maxRank = 5, tier = 1, col = 3, desc = "+10% offhand damage." },
    -- Tier 2
    { id = 14165, ranks = {14165, 14166}, name = "Improved Slice and Dice", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 2, col = 1, desc = "" },
    { id = 13713, ranks = {13713, 13853, 13854}, name = "Deflection", icon = "Interface\\Icons\\Ability_Rogue_SliceDice", maxRank = 3, tier = 2, col = 2, desc = "+25% S&D duration." },
    { id = 13705, ranks = {13705, 13832, 13843, 13844, 13845}, name = "Precision", icon = "Interface\\Icons\\Ability_Parry", maxRank = 5, tier = 2, col = 4, desc = "+1% Parry." },
    -- Tier 3
    { id = 13742, ranks = {13742, 13872}, name = "Endurance", icon = "Interface\\Icons\\Ability_Marksmanship", maxRank = 2, tier = 3, col = 1, desc = "+1% hit." },
    { id = 14251, ranks = {14251}, name = "Riposte", icon = "Interface\\Icons\\Spell_Shadow_ShadowWard", maxRank = 1, tier = 3, col = 2, desc = "-25% Sprint/Evasion cooldown.", prereq = {2, 2} },
    { id = 13706, ranks = {13706, 13804, 13805, 13806, 13807}, name = "Close Quarters Combat", icon = "Interface\\Icons\\Spell_Nature_Invisibilty", maxRank = 5, tier = 3, col = 3, desc = "+2% dodge.", prereq = {1, 3} },
    -- Tier 4
    { id = 13754, ranks = {13754, 13867}, name = "Improved Kick", icon = "Interface\\Icons\\Ability_Rogue_Sprint", maxRank = 2, tier = 4, col = 1, desc = "Sprint removes movement impairment." },
    { id = 13743, ranks = {13743, 13875}, name = "Improved Sprint", icon = "Interface\\Icons\\Ability_Warrior_Challange", maxRank = 2, tier = 4, col = 2, desc = "Counter attack after parry." },
    { id = 13712, ranks = {13712, 13788, 13789}, name = "Lightning Reflexes", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 4, col = 3, desc = "" },
    { id = 18427, ranks = {18427, 18428, 18429, 61330, 61331}, name = "Aggression", icon = "Interface\\Icons\\INV_Mace_01", maxRank = 5, tier = 4, col = 4, desc = "Maces ignore 3% armor." },
    -- Tier 5
    { id = 13709, ranks = {13709, 13800, 13801, 13802, 13803}, name = "Mace Specialization", icon = "Interface\\Icons\\Ability_Racial_Avatar", maxRank = 5, tier = 5, col = 1, desc = "+3% SS/BS/Evis damage." },
    { id = 13877, ranks = {13877}, name = "Blade Flurry", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 5, col = 2, desc = "" },
    { id = 13960, ranks = {13960, 13961, 13962, 13963, 13964}, name = "Hack and Slash", icon = "Interface\\Icons\\Ability_Warrior_PunishingBlow", maxRank = 5, tier = 5, col = 3, desc = "+20% attack speed, hits 2 targets." },
    -- Tier 6
    { id = 30919, ranks = {30919, 30920}, name = "Weapon Expertise", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 6, col = 2, desc = "", prereq = {5, 2} },
    { id = 31124, ranks = {31124, 31126}, name = "Blade Twisting", icon = "Interface\\Icons\\Spell_Holy_BlessingOfStrength", maxRank = 2, tier = 6, col = 3, desc = "+5 expertise." },
    -- Tier 7
    { id = 31122, ranks = {31122, 31123, 61329}, name = "Vitality", icon = "Interface\\Icons\\Ability_Rogue_BladeTwisting", maxRank = 3, tier = 7, col = 1, desc = "Attacks can daze target." },
    { id = 13750, ranks = {13750}, name = "Adrenaline Rush", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordDominate", maxRank = 1, tier = 7, col = 2, desc = "+100% energy regen for 15 sec." },
    { id = 31130, ranks = {31130, 31131}, name = "Nerves of Steel", icon = "Interface\\Icons\\Ability_Rogue_NervesOfSteel", maxRank = 2, tier = 7, col = 3, desc = "-15% damage while stunned." },
    -- Tier 8
    { id = 5952, ranks = {5952, 51679}, name = "Throwing Specialization", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 1, desc = "" },
    { id = 35541, ranks = {35541, 35550, 35551, 35552, 35553}, name = "Combat Potency", icon = "Interface\\Icons\\INV_Gauntlets_05", maxRank = 5, tier = 8, col = 3, desc = "+1% crit with fist weapons." },
    -- Tier 9
    { id = 51672, ranks = {51672, 51674}, name = "Unfair Advantage", icon = "Interface\\Icons\\INV_ThrowingKnife_06", maxRank = 2, tier = 9, col = 1, desc = "Throwing can interrupt." },
    { id = 32601, ranks = {32601}, name = "Surprise Attacks", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 9, col = 2, desc = "", prereq = {7, 2} },
    { id = 51682, ranks = {51682, 58413}, name = "Savage Combat", icon = "Interface\\Icons\\Ability_Rogue_SurpriseAttack", maxRank = 2, tier = 9, col = 3, desc = "Finishing moves can't be dodged." },
    -- Tier 10
    { id = 51685, ranks = {51685, 51686, 51687, 51688, 51689}, name = "Prey on the Weak", icon = "Interface\\Icons\\Ability_Rogue_UnfairAdvantage", maxRank = 5, tier = 10, col = 2, desc = "Dodge procs free SS." },
    -- Tier 11
    { id = 51690, ranks = {51690}, name = "Killing Spree", icon = "Interface\\Icons\\Ability_Creature_Disease_03", maxRank = 1, tier = 11, col = 2, desc = "+2% damage vs poisoned." },
}

-- SUBTLETY (Spec 3)
local Subtlety = {
    -- Tier 1
    { id = 14179, ranks = {14179, 58422, 58423, 58424, 58425}, name = "Relentless Strikes", icon = "Interface\\Icons\\Ability_Warrior_DecisiveStrike", maxRank = 5, tier = 1, col = 1, desc = "Finishers restore 5 energy per combo point." },
    { id = 13958, ranks = {13958, 13970, 13971}, name = "Master of Deception", icon = "Interface\\Icons\\Ability_Rogue_SleightofHand", maxRank = 3, tier = 1, col = 2, desc = "-2% hit chance vs you, +10% Tricks range." },
    { id = 14057, ranks = {14057, 14072}, name = "Opportunity", icon = "Interface\\Icons\\Ability_Rogue_DirtyTricks", maxRank = 2, tier = 1, col = 3, desc = "-50% Sap/Blind energy cost." },
    -- Tier 2
    { id = 30892, ranks = {30892, 30893}, name = "Sleight of Hand", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 2, col = 1, desc = "" },
    { id = 14076, ranks = {14076, 14094}, name = "Dirty Tricks", icon = "Interface\\Icons\\Spell_Shadow_Curse", maxRank = 2, tier = 2, col = 2, desc = "Deals damage and +15% dodge." },
    { id = 13975, ranks = {13975, 14062, 14063}, name = "Camouflage", icon = "Interface\\Icons\\Ability_Stealth", maxRank = 3, tier = 2, col = 3, desc = "+5% speed in stealth, -1 sec Stealth cooldown." },
    -- Tier 3
    { id = 13981, ranks = {13981, 14066}, name = "Elusiveness", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 3, col = 1, desc = "" },
    { id = 14278, ranks = {14278}, name = "Ghostly Strike", icon = "Interface\\Icons\\Spell_Nature_MirrorImage", maxRank = 1, tier = 3, col = 2, desc = "Dodge/parry can add combo point." },
    { id = 14171, ranks = {14171, 14172, 14173}, name = "Serrated Blades", icon = "Interface\\Icons\\Spell_Shadow_Fumble", maxRank = 3, tier = 3, col = 3, desc = "+25% chance for extra CP from stealth." },
    -- Tier 4
    { id = 13983, ranks = {13983, 14070, 14071}, name = "Setup", icon = "Interface\\Icons\\INV_Sword_17", maxRank = 3, tier = 4, col = 1, desc = "Rupture ignores 3% armor." },
    { id = 13976, ranks = {13976, 13979, 13980}, name = "Initiative", icon = "Interface\\Icons\\Spell_Magic_LesserInvisibilty", maxRank = 3, tier = 4, col = 2, desc = "-1.5 min Vanish/Blind/Cloak cooldown." },
    { id = 14079, ranks = {14079, 14080}, name = "Improved Ambush", icon = "Interface\\Icons\\Spell_Nature_MirrorImage", maxRank = 2, tier = 4, col = 3, desc = "Dodge/parry can add combo point." },
    -- Tier 5
    { id = 30894, ranks = {30894, 30895}, name = "Heightened Senses", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 5, col = 1, desc = "" },
    { id = 14185, ranks = {14185}, name = "Preparation", icon = "Interface\\Icons\\Spell_Shadow_AntiShadow", maxRank = 1, tier = 5, col = 2, desc = "Resets ability cooldowns." },
    { id = 14082, ranks = {14082, 14083}, name = "Dirty Deeds", icon = "Interface\\Icons\\Spell_Shadow_PlagueCloud", maxRank = 2, tier = 5, col = 3, desc = "+10% damage vs targets below 35%." },
    { id = 16511, ranks = {16511}, name = "Hemorrhage", icon = "Interface\\Icons\\Spell_Shadow_LifeDrain", maxRank = 1, tier = 5, col = 4, desc = "Attack that increases physical damage taken.", prereq = {3, 3} },
    -- Tier 6
    { id = 31221, ranks = {31221, 31222, 31223}, name = "Master of Subtlety", icon = "Interface\\Icons\\INV_Weapon_Crossbow_11", maxRank = 3, tier = 6, col = 1, desc = "+2% AP." },
    { id = 30902, ranks = {30902, 30903, 30904, 30905, 30906}, name = "Deadliness", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 31211, ranks = {31211, 31212, 31213}, name = "Enveloping Shadows", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 1, desc = "" },
    { id = 14183, ranks = {14183}, name = "Premeditation", icon = "Interface\\Icons\\Spell_Shadow_Possession", maxRank = 1, tier = 7, col = 2, desc = "Add 2 combo points from stealth.", prereq = {5, 2} },
    { id = 31228, ranks = {31228, 31229, 31230}, name = "Cheat Death", icon = "Interface\\Icons\\Ability_Rogue_CheatDeath", maxRank = 3, tier = 7, col = 3, desc = "Chance to survive fatal blow." },
    -- Tier 8
    { id = 31216, ranks = {31216, 31217, 31218, 31219, 31220}, name = "Sinister Calling", icon = "Interface\\Icons\\Ability_Rogue_SinisterCalling", maxRank = 5, tier = 8, col = 2, desc = "+1% Agility, +2% Backstab/Hemorrhage damage.", prereq = {7, 2} },
    { id = 51692, ranks = {51692, 51696}, name = "Waylay", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 3, desc = "" },
    -- Tier 9
    { id = 51698, ranks = {51698, 51700, 51701}, name = "Honor Among Thieves", icon = "Interface\\Icons\\Ability_Rogue_Filthytricks", maxRank = 3, tier = 9, col = 1, desc = "-5 sec Tricks/Distract cooldown." },
    { id = 36554, ranks = {36554}, name = "Shadowstep", icon = "Interface\\Icons\\Ability_Rogue_Shadowstep", maxRank = 1, tier = 9, col = 2, desc = "Teleport behind target." },
    { id = 58414, ranks = {58414, 58415}, name = "Filthy Tricks", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 9, col = 3, desc = "" },
    -- Tier 10
    { id = 51708, ranks = {51708, 51709, 51710, 51711, 51712}, name = "Slaughter from the Shadows", icon = "Interface\\Icons\\Ability_Rogue_Waylay", maxRank = 5, tier = 10, col = 2, desc = "Ambush/Backstab reduce movement speed." },
    -- Tier 11
    { id = 51713, ranks = {51713}, name = "Shadow Dance", icon = "Interface\\Icons\\Ability_Rogue_HonorAmongstThieves", maxRank = 1, tier = 11, col = 2, desc = "Party crits give you combo points." },
}

Adv2.Data.Talents[4] = {
    [1] = Assassination,
    [2] = Combat,
    [3] = Subtlety,
}
