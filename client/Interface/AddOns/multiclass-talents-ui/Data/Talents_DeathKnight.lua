-- Death Knight Talent Data (Class 6)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- BLOOD (Spec 1)
local Blood = {
    -- Tier 1
    { id = 48979, ranks = {48979, 49483}, name = "Butchery", icon = "Interface\\Icons\\INV_Axe_68", maxRank = 2, tier = 1, col = 1, desc = "+10 runic power on kill." },
    { id = 48997, ranks = {48997, 49490, 49491}, name = "Subversion", icon = "Interface\\Icons\\Spell_DeathKnight_DeathStrike", maxRank = 3, tier = 1, col = 2, desc = "+5% Blood Strike/Blood Boil damage." },
    { id = 49182, ranks = {49182, 49500, 49501, 55225, 55226}, name = "Blade Barrier", icon = "Interface\\Icons\\Spell_DeathKnight_DarkConviction", maxRank = 5, tier = 1, col = 3, desc = "+1% crit." },
    -- Tier 2
    { id = 48978, ranks = {48978, 49390, 49391, 49392, 49393}, name = "Bladed Armor", icon = "Interface\\Icons\\Ability_Backstab", maxRank = 5, tier = 2, col = 1, desc = "+3% Blood Strike/Scourge Strike crit." },
    { id = 49004, ranks = {49004, 49508, 49509}, name = "Scent of Blood", icon = "Interface\\Icons\\Ability_UpgradeMoonGlaive", maxRank = 3, tier = 2, col = 2, desc = "Blood Runes spent grant -2% damage." },
    { id = 55107, ranks = {55107, 55108}, name = "Two-Handed Weapon Specialization", icon = "Interface\\Icons\\Ability_Hunter_RapidKilling", maxRank = 2, tier = 2, col = 3, desc = "Target heals attacker when hit." },
    -- Tier 3
    { id = 48982, ranks = {48982}, name = "Rune Tap", icon = "Interface\\Icons\\Spell_DeathKnight_SpellDeflection", maxRank = 1, tier = 3, col = 1, desc = "Parry can deflect spell damage." },
    { id = 48987, ranks = {48987, 49477, 49478, 49479, 49480}, name = "Dark Conviction", icon = "Interface\\Icons\\Spell_DeathKnight_SpellDeflection", maxRank = 5, tier = 3, col = 2, desc = "Parry can deflect spell damage." },
    { id = 49467, ranks = {49467, 50033, 50034}, name = "Death Rune Mastery", icon = "Interface\\Icons\\Spell_DeathKnight_RuneTap", maxRank = 3, tier = 3, col = 3, desc = "+33% Rune Tap healing." },
    -- Tier 4
    { id = 48985, ranks = {48985, 49488, 49489}, name = "Improved Rune Tap", icon = "Interface\\Icons\\Spell_DeathKnight_RuneTap", maxRank = 3, tier = 4, col = 1, desc = "Convert Blood Rune to 10% health.", prereq = {3, 1} },
    { id = 49145, ranks = {49145, 49495, 49497}, name = "Spell Deflection", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 4, col = 3, desc = "" },
    { id = 49015, ranks = {49015, 50154, 55136}, name = "Vendetta", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 4, col = 4, desc = "" },
    -- Tier 5
    { id = 48977, ranks = {48977, 49394, 49395}, name = "Bloody Strikes", icon = "Interface\\Icons\\INV_Sword_62", maxRank = 3, tier = 5, col = 1, desc = "Death Strike/Obliterate convert to Death Runes." },
    { id = 49006, ranks = {49006, 49526, 50029}, name = "Veteran of the Third War", icon = "Interface\\Icons\\INV_Sword_98", maxRank = 3, tier = 5, col = 3, desc = "+2% 2H weapon damage." },
    { id = 49005, ranks = {49005}, name = "Mark of Blood", icon = "Interface\\Icons\\Ability_Backstab", maxRank = 1, tier = 5, col = 4, desc = "Crits stack +1% physical damage." },
    -- Tier 6
    { id = 48988, ranks = {48988, 49503, 49504}, name = "Bloody Vengeance", icon = "Interface\\Icons\\Ability_Backstab", maxRank = 3, tier = 6, col = 2, desc = "Crits stack +1% physical damage.", prereq = {3, 2} },
    { id = 53137, ranks = {53137, 53138}, name = "Abomination's Might", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 49027, ranks = {49027, 49542, 49543}, name = "Bloodworms", icon = "Interface\\Icons\\Ability_Warrior_IntensifyRage", maxRank = 3, tier = 7, col = 1, desc = "+2% Strength, +10% raid AP." },
    { id = 49016, ranks = {49016}, name = "Hysteria", icon = "Interface\\Icons\\Spell_DeathKnight_Bladedarmor", maxRank = 1, tier = 7, col = 2, desc = "+20% physical damage, drains health." },
    { id = 50365, ranks = {50365, 50371}, name = "Improved Blood Presence", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 7, col = 3, desc = "" },
    -- Tier 8
    { id = 62905, ranks = {62905, 62908}, name = "Improved Death Strike", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 1, desc = "" },
    { id = 49018, ranks = {49018, 49529, 49530}, name = "Sudden Doom", icon = "Interface\\Icons\\Spell_Misc_Warstomp", maxRank = 3, tier = 8, col = 2, desc = "+2% Strength and Stamina." },
    { id = 55233, ranks = {55233}, name = "Vampiric Blood", icon = "Interface\\Icons\\Spell_Shadow_LifeDrain", maxRank = 1, tier = 8, col = 3, desc = "+15% health and healing received." },
    -- Tier 9
    { id = 49189, ranks = {49189, 50149, 50150}, name = "Will of the Necropolis", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 1, desc = "" },
    { id = 55050, ranks = {55050}, name = "Heart Strike", icon = "Interface\\Icons\\Spell_Shadow_DeathScream", maxRank = 1, tier = 9, col = 2, desc = "Heals 2% on killing blow." },
    { id = 49023, ranks = {49023, 49533, 49534}, name = "Might of Mograine", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 3, desc = "" },
    -- Tier 10
    { id = 61154, ranks = {61154, 61155, 61156, 61157, 61158}, name = "Blood Gorged", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 10, col = 2, desc = "" },
    -- Tier 11
    { id = 49028, ranks = {49028}, name = "Dancing Rune Weapon", icon = "Interface\\Icons\\Ability_Creature_Cursed_03", maxRank = 1, tier = 11, col = 2, desc = "-5% damage when below 35%." },
}

-- FROST (Spec 2)
local Frost = {
    -- Tier 1
    { id = 49175, ranks = {49175, 50031, 51456}, name = "Improved Icy Touch", icon = "Interface\\Icons\\Spell_DeathKnight_IceTouch", maxRank = 3, tier = 1, col = 1, desc = "+5% Icy Touch damage." },
    { id = 49455, ranks = {49455, 50147}, name = "Runic Power Mastery", icon = "Interface\\Icons\\INV_Misc_Rune_10", maxRank = 2, tier = 1, col = 2, desc = "+15 max runic power." },
    { id = 49042, ranks = {49042, 49786, 49787, 49788, 49789}, name = "Toughness", icon = "Interface\\Icons\\Spell_Holy_Devotion", maxRank = 5, tier = 1, col = 3, desc = "+2% armor from items." },
    -- Tier 2
    { id = 55061, ranks = {55061, 55062}, name = "Icy Reach", icon = "Interface\\Icons\\INV_ChestPlate_05", maxRank = 2, tier = 2, col = 2, desc = "-2% hit chance vs you." },
    { id = 49140, ranks = {49140, 49661, 49662, 49663, 49664}, name = "Black Ice", icon = "Interface\\Icons\\Spell_Shadow_DarkRitual", maxRank = 5, tier = 2, col = 3, desc = "+10 yard Icy Touch/Chains range." },
    { id = 49226, ranks = {49226, 50137, 50138}, name = "Nerves of Cold Steel", icon = "Interface\\Icons\\Spell_Frost_ChillingArmor", maxRank = 3, tier = 2, col = 4, desc = "+2% Frost/Shadow damage." },
    -- Tier 3
    { id = 50880, ranks = {50880, 50884, 50885, 50886, 50887}, name = "Icy Talons", icon = "Interface\\Icons\\INV_Weapon_Halbard_14", maxRank = 5, tier = 3, col = 1, desc = "+6% damage vs targets below 35%.", prereq = {1, 1} },
    { id = 49039, ranks = {49039}, name = "Lichborne", icon = "Interface\\Icons\\INV_Weapon_ShortBlade_79", maxRank = 1, tier = 3, col = 2, desc = "+5% offhand damage, +8% 1H hit." },
    { id = 51468, ranks = {51468, 51472, 51473}, name = "Annihilation", icon = "Interface\\Icons\\INV_Sword_122", maxRank = 3, tier = 3, col = 3, desc = "Attacks can proc crit on Icy Touch/Frost Strike." },
    -- Tier 4
    { id = 51123, ranks = {51123, 51127, 51128, 51129, 51130}, name = "Killing Machine", icon = "Interface\\Icons\\Spell_Frost_FreezingBreath", maxRank = 5, tier = 4, col = 2, desc = "Stacks resist vs spell damage type." },
    { id = 49149, ranks = {49149, 50115}, name = "Chill of the Grave", icon = "Interface\\Icons\\Spell_DeathKnight_IcyTalons", maxRank = 2, tier = 4, col = 3, desc = "+4% melee attack speed." },
    { id = 49137, ranks = {49137, 49657}, name = "Endless Winter", icon = "Interface\\Icons\\Spell_Frost_ManaRecharge", maxRank = 2, tier = 4, col = 4, desc = "Mind Freeze costs no runic power." },
    -- Tier 5
    { id = 49186, ranks = {49186, 51108, 51109}, name = "Frigid Dreadplate", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 5, col = 2, desc = "" },
    { id = 49471, ranks = {49471, 49790, 49791}, name = "Glacier Rot", icon = "Interface\\Icons\\Spell_DeathKnight_DeathChill", maxRank = 3, tier = 5, col = 3, desc = "Next Icy Touch/Howling Blast/Frost Strike crits." },
    { id = 49796, ranks = {49796}, name = "Deathchill", icon = "Interface\\Icons\\INV_Weapon_Halbard_14", maxRank = 1, tier = 5, col = 4, desc = "+6% damage vs targets below 35%." },
    -- Tier 6
    { id = 55610, ranks = {55610}, name = "Improved Icy Talons", icon = "Interface\\Icons\\Spell_DeathKnight_DeathStrike", maxRank = 1, tier = 6, col = 1, desc = "+3% Blood Strike damage, converts runes.", prereq = {3, 1} },
    { id = 49024, ranks = {49024, 49538}, name = "Merciless Combat", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 6, col = 2, desc = "" },
    { id = 49188, ranks = {49188, 56822, 59057}, name = "Rime", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 50040, ranks = {50040, 50041, 50043}, name = "Chilblains", icon = "Interface\\Icons\\Spell_DeathKnight_ChillOfTheGrave", maxRank = 3, tier = 7, col = 1, desc = "Icy Touch/Obliterate generate runic power." },
    { id = 49203, ranks = {49203}, name = "Hungering Cold", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 7, col = 2, desc = "" },
    { id = 50384, ranks = {50384, 50385}, name = "Improved Frost Presence", icon = "Interface\\Icons\\Spell_DeathKnight_IcyTalons", maxRank = 2, tier = 7, col = 3, desc = "+20% raid melee haste." },
    -- Tier 8
    { id = 65661, ranks = {65661, 66191, 66192}, name = "Threat of Thassarian", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 8, col = 1, desc = "" },
    { id = 54639, ranks = {54639, 54638, 54637}, name = "Blood of the North", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 8, col = 2, desc = "" },
    { id = 51271, ranks = {51271}, name = "Unbreakable Armor", icon = "Interface\\Icons\\Spell_Frost_FreezingBreath", maxRank = 1, tier = 8, col = 3, desc = "Freezes all nearby enemies." },
    -- Tier 9
    { id = 49200, ranks = {49200, 50151, 50152}, name = "Acclimation", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 1, desc = "" },
    { id = 49143, ranks = {49143}, name = "Frost Strike", icon = "Interface\\Icons\\Spell_DeathKnight_EmpowerRuneBlade02", maxRank = 1, tier = 9, col = 2, desc = "Powerful Frost attack." },
    { id = 50187, ranks = {50187, 50190, 50191}, name = "Guile of Gorefiend", icon = "Interface\\Icons\\Spell_DeathKnight_FrostPresence", maxRank = 3, tier = 9, col = 3, desc = "-2% damage while in Frost Presence." },
    -- Tier 10
    { id = 49202, ranks = {49202, 50127, 50128, 50129, 50130}, name = "Tundra Stalker", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 10, col = 2, desc = "" },
    -- Tier 11
    { id = 49184, ranks = {49184}, name = "Howling Blast", icon = "Interface\\Icons\\Spell_Frost_ArcticWinds", maxRank = 1, tier = 11, col = 2, desc = "AoE Frost damage." },
}

-- UNHOLY (Spec 3)
local Unholy = {
    -- Tier 1
    { id = 51745, ranks = {51745, 51746}, name = "Vicious Strikes", icon = "Interface\\Icons\\Spell_DeathKnight_UnholyCommand", maxRank = 2, tier = 1, col = 1, desc = "-5 sec Death Grip cooldown." },
    { id = 48962, ranks = {48962, 49567, 49568}, name = "Virulence", icon = "Interface\\Icons\\INV_Weapon_ShortBlade_60", maxRank = 3, tier = 1, col = 2, desc = "+4% auto-attack shadow damage." },
    { id = 55129, ranks = {55129, 55130, 55131, 55132, 55133}, name = "Anticipation", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 1, col = 3, desc = "" },
    -- Tier 2
    { id = 49036, ranks = {49036, 49562}, name = "Epidemic", icon = "Interface\\Icons\\Ability_Creature_Disease_03", maxRank = 2, tier = 2, col = 1, desc = "+3% Plague Strike/Scourge Strike crit." },
    { id = 48963, ranks = {48963, 49564, 49565}, name = "Morbidity", icon = "Interface\\Icons\\Ability_Creature_Disease_03", maxRank = 3, tier = 2, col = 2, desc = "+3% Plague Strike/Scourge Strike crit." },
    { id = 49588, ranks = {49588, 49589}, name = "Unholy Command", icon = "Interface\\Icons\\Spell_Shadow_BurningSpirit", maxRank = 2, tier = 2, col = 3, desc = "+3% spell hit." },
    { id = 48965, ranks = {48965, 49571, 49572}, name = "Ravenous Dead", icon = "Interface\\Icons\\Spell_DeathKnight_ClassIcon", maxRank = 3, tier = 2, col = 4, desc = "+10% mount speed, -10% movement impair." },
    -- Tier 3
    { id = 49013, ranks = {49013, 55236, 55237}, name = "Outbreak", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordDominate", maxRank = 3, tier = 3, col = 1, desc = "+3 sec disease duration." },
    { id = 51459, ranks = {51459, 51462, 51463, 51464, 51465}, name = "Necrosis", icon = "Interface\\Icons\\Spell_Nature_MirrorImage", maxRank = 5, tier = 3, col = 2, desc = "+1% dodge." },
    { id = 49158, ranks = {49158}, name = "Corpse Explosion", icon = "Interface\\Icons\\Spell_Shadow_DeathCoil", maxRank = 1, tier = 3, col = 3, desc = "+5% Death Coil damage, -5 sec DnD cooldown." },
    -- Tier 4
    { id = 49146, ranks = {49146, 51267}, name = "On a Pale Horse", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 2, desc = "" },
    { id = 49219, ranks = {49219, 49627, 49628}, name = "Blood-Caked Blade", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 4, col = 3, desc = "" },
    { id = 55620, ranks = {55620, 55623}, name = "Night of the Dead", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 4, desc = "" },
    -- Tier 5
    { id = 49194, ranks = {49194}, name = "Unholy Blight", icon = "Interface\\Icons\\Spell_Shadow_ChillTouch", maxRank = 1, tier = 5, col = 1, desc = "+4% AP to spell damage." },
    { id = 49220, ranks = {49220, 49633, 49635, 49636, 49638}, name = "Impurity", icon = "Interface\\Icons\\Ability_Creature_Disease_01", maxRank = 5, tier = 5, col = 2, desc = "Diseases increase disease damage taken." },
    { id = 49223, ranks = {49223, 49599}, name = "Dirge", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 5, col = 3, desc = "" },
    -- Tier 6
    { id = 55666, ranks = {55666, 55667}, name = "Desecration", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 6, col = 1, desc = "" },
    { id = 49224, ranks = {49224, 49610, 49611}, name = "Magic Suppression", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 2, desc = "" },
    { id = 49208, ranks = {49208, 56834, 56835}, name = "Reaping", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 3, desc = "" },
    { id = 52143, ranks = {52143}, name = "Master of Ghouls", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 6, col = 4, desc = "", prereq = {4, 4} },
    -- Tier 7
    { id = 66799, ranks = {66799, 66814, 66815, 66816, 66817}, name = "Desolation", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 7, col = 1, desc = "" },
    { id = 51052, ranks = {51052}, name = "Anti-Magic Zone", icon = "Interface\\Icons\\Spell_DeathKnight_AntiMagicZone", maxRank = 1, tier = 7, col = 2, desc = "Create magic damage reduction zone.", prereq = {6, 2} },
    { id = 50391, ranks = {50391, 50392}, name = "Improved Unholy Presence", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 7, col = 3, desc = "" },
    { id = 63560, ranks = {63560}, name = "Ghoul Frenzy", icon = "Interface\\Icons\\Ability_Creature_Cursed_05", maxRank = 1, tier = 7, col = 4, desc = "Blood/Frost Strikes convert to Death Runes.", prereq = {6, 4} },
    -- Tier 8
    { id = 49032, ranks = {49032, 49631, 49632}, name = "Crypt Fever", icon = "Interface\\Icons\\Spell_Holy_Silence", maxRank = 3, tier = 8, col = 2, desc = "-2% magic damage taken." },
    { id = 49222, ranks = {49222}, name = "Bone Shield", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordDominate", maxRank = 1, tier = 8, col = 3, desc = "Plague Strike creates slowing AoE." },
    -- Tier 9
    { id = 49217, ranks = {49217, 49654, 49655}, name = "Wandering Plague", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 1, desc = "" },
    { id = 51099, ranks = {51099, 51160, 51161}, name = "Ebon Plaguebringer", icon = "Interface\\Icons\\Spell_Shadow_AnimateDead", maxRank = 3, tier = 9, col = 2, desc = "Ghoul becomes permanent pet.", prereq = {8, 2} },
    { id = 55090, ranks = {55090}, name = "Scourge Strike", icon = "Interface\\Icons\\Spell_Shadow_UnholyBlight", maxRank = 1, tier = 9, col = 3, desc = "Death Coil creates disease cloud." },
    -- Tier 10
    { id = 50117, ranks = {50117, 50118, 50119, 50120, 50121}, name = "Rage of Rivendare", icon = "Interface\\Icons\\Ability_Creature_Cursed_04", maxRank = 5, tier = 10, col = 2, desc = "Crypt Fever becomes Ebon Plague." },
    -- Tier 11
    { id = 49206, ranks = {49206}, name = "Summon Gargoyle", icon = "Interface\\Icons\\Ability_Hunter_Pet_Bat", maxRank = 1, tier = 11, col = 2, desc = "Summon gargoyle for 30 sec." },
}

Adv2.Data.Talents[6] = {
    [1] = Blood,
    [2] = Frost,
    [3] = Unholy,
}
