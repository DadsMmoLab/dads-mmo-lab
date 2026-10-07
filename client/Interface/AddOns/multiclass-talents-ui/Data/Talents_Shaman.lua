-- Shaman Talent Data (Class 7)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- ELEMENTAL (Spec 1)
local Elemental = {
    -- Tier 1
    { id = 16039, ranks = {16039, 16109, 16110, 16111, 16112}, name = "Convection", icon = "Interface\\Icons\\Spell_Nature_WispSplode", maxRank = 5, tier = 1, col = 2, desc = "-2% Shock/Lightning mana cost." },
    { id = 16035, ranks = {16035, 16105, 16106, 16107, 16108}, name = "Concussion", icon = "Interface\\Icons\\Spell_Fire_Fireball", maxRank = 5, tier = 1, col = 3, desc = "+1% Shock/Lightning damage." },
    -- Tier 2
    { id = 16038, ranks = {16038, 16160, 16161}, name = "Call of Flame", icon = "Interface\\Icons\\Spell_Fire_Immolation", maxRank = 3, tier = 2, col = 1, desc = "+5% Fire Totem damage." },
    { id = 28996, ranks = {28996, 28997, 28998}, name = "Elemental Warding", icon = "Interface\\Icons\\Spell_Nature_SkinofEarth", maxRank = 3, tier = 2, col = 2, desc = "-2% Fire/Frost/Nature damage taken." },
    { id = 30160, ranks = {30160, 29179, 29180}, name = "Elemental Devastation", icon = "Interface\\Icons\\Spell_Fire_SealOfFire", maxRank = 3, tier = 2, col = 3, desc = "+10% Fire Nova damage." },
    -- Tier 3
    { id = 16040, ranks = {16040, 16113, 16114, 16115, 16116}, name = "Reverberation", icon = "Interface\\Icons\\Spell_Fire_FlameShock", maxRank = 5, tier = 3, col = 1, desc = "Spell crits +3% melee crit." },
    { id = 16164, ranks = {16164}, name = "Elemental Focus", icon = "Interface\\Icons\\Spell_Frost_FrostWard", maxRank = 1, tier = 3, col = 2, desc = "-0.2 sec Shock cooldown." },
    { id = 16089, ranks = {16089, 60184, 60185, 60187, 60188}, name = "Elemental Fury", icon = "Interface\\Icons\\Spell_Shadow_ManaBurn", maxRank = 5, tier = 3, col = 3, desc = "Crits make next 2 spells -40% mana." },
    -- Tier 4
    { id = 16086, ranks = {16086, 16544}, name = "Improved Fire Nova", icon = "Interface\\Icons\\Spell_Fire_Volcano", maxRank = 2, tier = 4, col = 1, desc = "+20% crit damage bonus." },
    { id = 29062, ranks = {29062, 29064, 29065}, name = "Eye of the Storm", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 4, col = 4, desc = "" },
    -- Tier 5
    { id = 28999, ranks = {28999, 29000}, name = "Elemental Reach", icon = "Interface\\Icons\\Spell_Shadow_SoulLeech_2", maxRank = 2, tier = 5, col = 1, desc = "Crits reduce pushback." },
    { id = 16041, ranks = {16041}, name = "Call of Thunder", icon = "Interface\\Icons\\Spell_Nature_CallStorm", maxRank = 1, tier = 5, col = 2, desc = "+5% Lightning Bolt/Chain Lightning crit.", prereq = {3, 2} },
    { id = 30664, ranks = {30664, 30665, 30666}, name = "Unrelenting Storm", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 5, col = 4, desc = "" },
    -- Tier 6
    { id = 30672, ranks = {30672, 30673, 30674}, name = "Elemental Precision", icon = "Interface\\Icons\\Spell_Nature_UnrelentingStorm", maxRank = 3, tier = 6, col = 1, desc = "+4% mana regen from Intellect." },
    { id = 16578, ranks = {16578, 16579, 16580, 16581, 16582}, name = "Lightning Mastery", icon = "Interface\\Icons\\Spell_Nature_ElementalPrecision_1", maxRank = 5, tier = 6, col = 3, desc = "+1% Fire/Frost/Nature hit.", prereq = {3, 3} },
    -- Tier 7
    { id = 16166, ranks = {16166}, name = "Elemental Mastery", icon = "Interface\\Icons\\Spell_Nature_WispHeal", maxRank = 1, tier = 7, col = 2, desc = "Next spell instant and +15% crit.", prereq = {5, 2} },
    { id = 51483, ranks = {51483, 51485, 51486}, name = "Storm, Earth and Fire", icon = "Interface\\Icons\\Spell_Nature_EarthStorm", maxRank = 3, tier = 7, col = 3, desc = "-10% Shock cooldown, Flame Shock crit." },
    -- Tier 8
    { id = 63370, ranks = {63370, 63372}, name = "Booming Echoes", icon = "Interface\\Icons\\Spell_Fire_BlueFlameBolt", maxRank = 2, tier = 8, col = 1, desc = "+10% Flame/Frost Shock damage." },
    { id = 51466, ranks = {51466, 51470}, name = "Elemental Oath", icon = "Interface\\Icons\\Spell_Shaman_ElementalOath", maxRank = 2, tier = 8, col = 2, desc = "Crits grant +5% spell crit to raid.", prereq = {7, 2} },
    { id = 30675, ranks = {30675, 30678, 30679}, name = "Lightning Overload", icon = "Interface\\Icons\\Spell_Lightning_LightningBolt01", maxRank = 3, tier = 8, col = 3, desc = "-0.1 sec LB/CL/Lava Burst cast." },
    -- Tier 9
    { id = 51474, ranks = {51474, 51478, 51479}, name = "Astral Shift", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 1, desc = "" },
    { id = 30706, ranks = {30706}, name = "Totem of Wrath", icon = "Interface\\Icons\\Spell_Nature_LightningOverload", maxRank = 1, tier = 9, col = 2, desc = "LB/CL can cast duplicate." },
    { id = 51480, ranks = {51480, 51481, 51482}, name = "Lava Flows", icon = "Interface\\Icons\\Spell_Fire_TotemOfWrath", maxRank = 3, tier = 9, col = 3, desc = "+3% crit and hit totem." },
    -- Tier 10
    { id = 62097, ranks = {62097, 62098, 62099, 62100, 62101}, name = "Shamanism", icon = "Interface\\Icons\\Spell_Nature_AstralRecalGroup", maxRank = 5, tier = 10, col = 2, desc = "Stunned: -10% damage taken." },
    -- Tier 11
    { id = 51490, ranks = {51490}, name = "Thunderstorm", icon = "Interface\\Icons\\Spell_Nature_BloodLust", maxRank = 1, tier = 11, col = 2, desc = "+4% LB/CL/Lava Burst spell power." },
}

-- ENHANCEMENT (Spec 2)
local Enhancement = {
    -- Tier 1
    { id = 16259, ranks = {16259, 16295, 52456}, name = "Enhancing Totems", icon = "Interface\\Icons\\Ability_GhoulFrenzy", maxRank = 3, tier = 1, col = 1, desc = "+6% attack speed after crit." },
    { id = 16043, ranks = {16043, 16130}, name = "Earth's Grasp", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 1, col = 2, desc = "" },
    { id = 17485, ranks = {17485, 17486, 17487, 17488, 17489}, name = "Ancestral Knowledge", icon = "Interface\\Icons\\Spell_Nature_EarthBindTotem", maxRank = 5, tier = 1, col = 3, desc = "+5% Strength/Agility totems." },
    -- Tier 2
    { id = 16258, ranks = {16258, 16293}, name = "Guardian Totems", icon = "Interface\\Icons\\Spell_Shadow_GrimWard", maxRank = 2, tier = 2, col = 1, desc = "+2% Intellect." },
    { id = 16255, ranks = {16255, 16302, 16303, 16304, 16305}, name = "Thundering Strikes", icon = "Interface\\Icons\\Spell_Nature_StoneSkinTotem", maxRank = 5, tier = 2, col = 2, desc = "+10% Stoneskin/Windwall effect." },
    { id = 16262, ranks = {16262, 16287}, name = "Improved Ghost Wolf", icon = "Interface\\Icons\\Ability_ThunderBolt", maxRank = 2, tier = 2, col = 3, desc = "+1% melee crit." },
    { id = 16261, ranks = {16261, 16290, 51881}, name = "Improved Shields", icon = "Interface\\Icons\\Spell_Nature_SpiritWolf", maxRank = 3, tier = 2, col = 4, desc = "-1 sec Ghost Wolf cast." },
    -- Tier 3
    { id = 16266, ranks = {16266, 29079, 29080}, name = "Elemental Weapons", icon = "Interface\\Icons\\Spell_Nature_LightningShield", maxRank = 3, tier = 3, col = 1, desc = "+5% Lightning Shield damage." },
    { id = 43338, ranks = {43338}, name = "Shamanistic Focus", icon = "Interface\\Icons\\Spell_Fire_FlameTounge", maxRank = 1, tier = 3, col = 3, desc = "+13% Windfury/Flametongue effect." },
    { id = 16254, ranks = {16254, 16271, 16272}, name = "Anticipation", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 3, col = 4, desc = "" },
    -- Tier 4
    { id = 16256, ranks = {16256, 16281, 16282, 16283, 16284}, name = "Flurry", icon = "Interface\\Icons\\Spell_Nature_MirrorImage", maxRank = 5, tier = 4, col = 2, desc = "+1% dodge.", prereq = {2, 2} },
    { id = 16252, ranks = {16252, 16306, 16307, 16308, 16309}, name = "Toughness", icon = "Interface\\Icons\\Spell_Nature_ElementalAbsorption", maxRank = 5, tier = 4, col = 3, desc = "-45% Shock mana cost." },
    -- Tier 5
    { id = 29192, ranks = {29192, 29193}, name = "Improved Windfury Totem", icon = "Interface\\Icons\\Ability_Shaman_Stormstrike", maxRank = 2, tier = 5, col = 1, desc = "Stormstrike grants 20% mana." },
    { id = 16268, ranks = {16268}, name = "Spirit Weapons", icon = "Interface\\Icons\\Spell_Holy_Devotion", maxRank = 1, tier = 5, col = 2, desc = "+2% Stamina, -10% movement slow." },
    { id = 51883, ranks = {51883, 51884, 51885}, name = "Mental Dexterity", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 5, col = 3, desc = "" },
    -- Tier 6
    { id = 30802, ranks = {30802, 30808, 30809}, name = "Unleashed Rage", icon = "Interface\\Icons\\Spell_Nature_LightningShield", maxRank = 3, tier = 6, col = 1, desc = "Attacks can proc Lightning Shield." },
    { id = 29082, ranks = {29082, 29084, 29086}, name = "Weapon Mastery", icon = "Interface\\Icons\\Spell_Nature_EnchantArmor", maxRank = 3, tier = 6, col = 3, desc = "+33% Intellect as AP." },
    { id = 63373, ranks = {63373, 63374}, name = "Frozen Power", icon = "Interface\\Icons\\Spell_Frost_FreezingBreath", maxRank = 2, tier = 6, col = 4, desc = "Frost Shock roots." },
    -- Tier 7
    { id = 30816, ranks = {30816, 30818, 30819}, name = "Dual Wield Specialization", icon = "Interface\\Icons\\Spell_Nature_UnleashedRage", maxRank = 3, tier = 7, col = 1, desc = "Crits give +3% raid AP.", prereq = {7, 2} },
    { id = 30798, ranks = {30798}, name = "Dual Wield", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 7, col = 2, desc = "", prereq = {5, 2} },
    { id = 17364, ranks = {17364}, name = "Stormstrike", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 7, col = 3, desc = "" },
    -- Tier 8
    { id = 51525, ranks = {51525, 51526, 51527}, name = "Static Shock", icon = "Interface\\Icons\\Ability_Hunter_SwiftStrike", maxRank = 3, tier = 8, col = 1, desc = "+4% weapon damage." },
    { id = 60103, ranks = {60103}, name = "Lava Lash", icon = "Interface\\Icons\\Ability_Shaman_LavalLash", maxRank = 1, tier = 8, col = 2, desc = "Offhand attack with fire.", prereq = {7, 2} },
    { id = 51521, ranks = {51521, 51522}, name = "Improved Stormstrike", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 3, desc = "", prereq = {7, 3} },
    -- Tier 9
    { id = 30812, ranks = {30812, 30813, 30814}, name = "Mental Quickness", icon = "Interface\\Icons\\Spell_Nature_UnleashedRage", maxRank = 3, tier = 9, col = 1, desc = "Crits give +3% raid AP." },
    { id = 30823, ranks = {30823}, name = "Shamanistic Rage", icon = "Interface\\Icons\\Ability_Shaman_Stormstrike", maxRank = 1, tier = 9, col = 2, desc = "Dual attack, +20% Nature damage." },
    { id = 51523, ranks = {51523, 51524}, name = "Earthen Power", icon = "Interface\\Icons\\Ability_DualWield", maxRank = 2, tier = 9, col = 3, desc = "+2% hit while dual wielding." },
    -- Tier 10
    { id = 51528, ranks = {51528, 51529, 51530, 51531, 51532}, name = "Maelstrom Weapon", icon = "Interface\\Icons\\Ability_DualWieldSpecialization", maxRank = 5, tier = 10, col = 2, desc = "Can dual wield weapons." },
    -- Tier 11
    { id = 51533, ranks = {51533}, name = "Feral Spirit", icon = "Interface\\Icons\\Spell_Nature_EarthShock", maxRank = 1, tier = 11, col = 2, desc = "Earthbind removes roots." },
}

-- RESTORATION (Spec 3)
local Restoration = {
    -- Tier 1
    { id = 16182, ranks = {16182, 16226, 16227, 16228, 16229}, name = "Improved Healing Wave", icon = "Interface\\Icons\\Spell_Nature_MagicImmunity", maxRank = 5, tier = 1, col = 2, desc = "-0.1 sec Healing Wave cast." },
    { id = 16173, ranks = {16173, 16222, 16223, 16224, 16225}, name = "Totemic Focus", icon = "Interface\\Icons\\Spell_Shaman_TidalWaves", maxRank = 5, tier = 1, col = 3, desc = "Chain Heal/Riptide proc haste." },
    -- Tier 2
    { id = 16184, ranks = {16184, 16209}, name = "Improved Reincarnation", icon = "Interface\\Icons\\Spell_Nature_Reincarnation", maxRank = 2, tier = 2, col = 1, desc = "-7 min Reincarnation cooldown." },
    { id = 29187, ranks = {29187, 29189, 29191}, name = "Healing Grace", icon = "Interface\\Icons\\Spell_Nature_HealingTouch", maxRank = 3, tier = 2, col = 2, desc = "-5% healing threat." },
    { id = 16179, ranks = {16179, 16214, 16215, 16216, 16217}, name = "Tidal Focus", icon = "Interface\\Icons\\Spell_Nature_MooNKey", maxRank = 5, tier = 2, col = 3, desc = "-5% totem mana cost." },
    -- Tier 3
    { id = 16180, ranks = {16180, 16196, 16198}, name = "Improved Water Shield", icon = "Interface\\Icons\\Spell_Nature_ManaTide", maxRank = 3, tier = 3, col = 1, desc = "+5% Mana Spring/Healing Stream." },
    { id = 16181, ranks = {16181, 16230, 16232}, name = "Healing Focus", icon = "Interface\\Icons\\Ability_Shaman_WaterShield", maxRank = 3, tier = 3, col = 2, desc = "Water Shield procs more." },
    { id = 55198, ranks = {55198}, name = "Tidal Force", icon = "Interface\\Icons\\Spell_Shaman_TidalWaves", maxRank = 1, tier = 3, col = 3, desc = "Chain Heal/Riptide proc haste." },
    { id = 16176, ranks = {16176, 16235, 16240}, name = "Ancestral Healing", icon = "Interface\\Icons\\Spell_Frost_ManaRecharge", maxRank = 3, tier = 3, col = 4, desc = "-1% healing spell mana cost." },
    -- Tier 4
    { id = 16187, ranks = {16187, 16205, 16206}, name = "Restorative Totems", icon = "Interface\\Icons\\Spell_Frost_SummonWaterElemental", maxRank = 3, tier = 4, col = 2, desc = "+60% crit on next 3 heals." },
    { id = 16194, ranks = {16194, 16218, 16219, 16220, 16221}, name = "Tidal Mastery", icon = "Interface\\Icons\\Spell_Nature_UndyingStrength", maxRank = 5, tier = 4, col = 3, desc = "Crit heals +10% armor." },
    -- Tier 5
    { id = 29206, ranks = {29206, 29205, 29202}, name = "Healing Way", icon = "Interface\\Icons\\Spell_Nature_FocusedMind", maxRank = 3, tier = 5, col = 1, desc = "-5% silence/interrupt duration." },
    { id = 16188, ranks = {16188}, name = "Nature's Swiftness", icon = "Interface\\Icons\\Spell_Nature_HealingWay", maxRank = 1, tier = 5, col = 3, desc = "Healing Wave stacks +6% healing." },
    { id = 30864, ranks = {30864, 30865, 30866}, name = "Focused Mind", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 5, col = 4, desc = "" },
    -- Tier 6
    { id = 16178, ranks = {16178, 16210, 16211, 16212, 16213}, name = "Purification", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 30881, ranks = {30881, 30883, 30884, 30885, 30886}, name = "Nature's Guardian", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 7, col = 1, desc = "" },
    { id = 16190, ranks = {16190}, name = "Mana Tide Totem", icon = "Interface\\Icons\\Spell_Nature_RavenForm", maxRank = 1, tier = 7, col = 2, desc = "Next spell instant.", prereq = {4, 2} },
    { id = 51886, ranks = {51886}, name = "Cleanse Spirit", icon = "Interface\\Icons\\Spell_Shaman_TidalWaves", maxRank = 1, tier = 7, col = 3, desc = "Chain Heal/Riptide proc haste.", prereq = {6, 3} },
    -- Tier 8
    { id = 51554, ranks = {51554, 51555}, name = "Blessing of the Eternals", icon = "Interface\\Icons\\Ability_Shaman_CleanseSpirit", maxRank = 2, tier = 8, col = 1, desc = "Removes curses and poisons." },
    { id = 30872, ranks = {30872, 30873}, name = "Improved Chain Heal", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 2, desc = "" },
    { id = 30867, ranks = {30867, 30868, 30869}, name = "Nature's Blessing", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 8, col = 3, desc = "" },
    -- Tier 9
    { id = 51556, ranks = {51556, 51557, 51558}, name = "Ancestral Awakening", icon = "Interface\\Icons\\Spell_Shaman_BlessingOfTheEternals", maxRank = 3, tier = 9, col = 1, desc = "+2% crit, +20% Earthliving on low health." },
    { id = 974, ranks = {974}, name = "Earth Shield", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 9, col = 2, desc = "" },
    { id = 51560, ranks = {51560, 51561}, name = "Improved Earth Shield", icon = "Interface\\Icons\\Spell_Nature_HealingWaveGreater", maxRank = 2, tier = 9, col = 3, desc = "+10% Chain Heal healing.", prereq = {9, 2} },
    -- Tier 10
    { id = 51562, ranks = {51562, 51563, 51564, 51565, 51566}, name = "Tidal Waves", icon = "Interface\\Icons\\Spell_Nature_NaturesBlessing", maxRank = 5, tier = 10, col = 2, desc = "+5% Intellect as spell power." },
    -- Tier 11
    { id = 61295, ranks = {61295}, name = "Riptide", icon = "Interface\\Icons\\Spell_Nature_Riptide", maxRank = 1, tier = 11, col = 2, desc = "Instant heal + HoT." },
}

Adv2.Data.Talents[7] = {
    [1] = Elemental,
    [2] = Enhancement,
    [3] = Restoration,
}
