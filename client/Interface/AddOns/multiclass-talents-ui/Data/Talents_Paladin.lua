-- Paladin Talent Data (Class 2)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- HOLY (Spec 1)
local Holy = {
    -- Tier 1
    { id = 20205, ranks = {20205, 20206, 20207, 20209, 20208}, name = "Spiritual Focus", icon = "Interface\\Icons\\Spell_Arcane_Blink", maxRank = 5, tier = 1, col = 2, desc = "70% reduced pushback on Holy spells." },
    { id = 20224, ranks = {20224, 20225, 20330, 20331, 20332}, name = "Seals of the Pure", icon = "Interface\\Icons\\Ability_ThunderBolt", maxRank = 5, tier = 1, col = 3, desc = "+3% seal damage." },
    -- Tier 2
    { id = 20237, ranks = {20237, 20238, 20239}, name = "Healing Light", icon = "Interface\\Icons\\Spell_Holy_HolyBolt", maxRank = 3, tier = 2, col = 1, desc = "+4% Holy Light/Holy Shock healing." },
    { id = 20257, ranks = {20257, 20258, 20259, 20260, 20261}, name = "Divine Intellect", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 2, col = 2, desc = "" },
    { id = 9453, ranks = {9453, 25836}, name = "Unyielding Faith", icon = "Interface\\Icons\\Spell_Holy_UnyieldingFaith", maxRank = 2, tier = 2, col = 3, desc = "+10% Fear/Disorient resist." },
    -- Tier 3
    { id = 31821, ranks = {31821}, name = "Aura Mastery", icon = "Interface\\Icons\\Spell_Holy_AuraMastery", maxRank = 1, tier = 3, col = 1, desc = "Increases aura radius and effectiveness." },
    { id = 20210, ranks = {20210, 20212, 20213, 20214, 20215}, name = "Illumination", icon = "Interface\\Icons\\Spell_Holy_GreaterHeal", maxRank = 5, tier = 3, col = 2, desc = "Holy crit returns 30% mana." },
    { id = 20234, ranks = {20234, 20235}, name = "Improved Lay on Hands", icon = "Interface\\Icons\\Spell_Nature_Sleep", maxRank = 2, tier = 3, col = 3, desc = "+2% Intellect." },
    -- Tier 4
    { id = 20254, ranks = {20254, 20255, 20256}, name = "Improved Concentration Aura", icon = "Interface\\Icons\\Spell_Holy_MindSooth", maxRank = 3, tier = 4, col = 1, desc = "+5% silence/interrupt resist." },
    { id = 20244, ranks = {20244, 20245}, name = "Improved Blessing of Wisdom", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 3, desc = "" },
    { id = 53660, ranks = {53660, 53661}, name = "Blessed Hands", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 4, desc = "" },
    -- Tier 5
    { id = 31822, ranks = {31822, 31823}, name = "Pure of Heart", icon = "Interface\\Icons\\Spell_Holy_SealOfWisdom", maxRank = 2, tier = 5, col = 1, desc = "+10% BoW effect." },
    { id = 20216, ranks = {20216}, name = "Divine Favor", icon = "Interface\\Icons\\Spell_Holy_Heal", maxRank = 1, tier = 5, col = 2, desc = "Next Holy spell is guaranteed crit.", prereq = {3, 2} },
    { id = 20359, ranks = {20359, 20360, 20361}, name = "Sanctified Light", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 5, col = 3, desc = "" },
    -- Tier 6
    { id = 31825, ranks = {31825, 31826}, name = "Purifying Power", icon = "Interface\\Icons\\Spell_Holy_PureOfHeart", maxRank = 2, tier = 6, col = 1, desc = "+15% Cleanse effect." },
    { id = 5923, ranks = {5923, 5924, 5925, 5926, 25829}, name = "Holy Power", icon = "Interface\\Icons\\Spell_Holy_SealOfProtection", maxRank = 5, tier = 6, col = 3, desc = "Reduces Hand spell cooldowns." },
    -- Tier 7
    { id = 31833, ranks = {31833, 31835, 31836}, name = "Light's Grace", icon = "Interface\\Icons\\Spell_Holy_HealingAura", maxRank = 3, tier = 7, col = 1, desc = "+3% Holy Light/Holy Shock crit." },
    { id = 20473, ranks = {20473}, name = "Holy Shock", icon = "Interface\\Icons\\Spell_Holy_SearingLight", maxRank = 1, tier = 7, col = 2, desc = "Instant damage or heal.", prereq = {5, 2} },
    { id = 31828, ranks = {31828, 31829, 31830}, name = "Blessed Life", icon = "Interface\\Icons\\Spell_Holy_PurifyingPower", maxRank = 3, tier = 7, col = 3, desc = "+10% Exorcism/Holy Wrath damage." },
    -- Tier 8
    { id = 53551, ranks = {53551, 53552, 53553}, name = "Sacred Cleansing", icon = "Interface\\Icons\\Spell_Holy_SacredCleansing", maxRank = 3, tier = 8, col = 1, desc = "Cleanse can remove magic." },
    { id = 31837, ranks = {31837, 31838, 31839, 31840, 31841}, name = "Holy Guidance", icon = "Interface\\Icons\\Spell_Holy_BlessedLife", maxRank = 5, tier = 8, col = 3, desc = "Chance to prevent damage." },
    -- Tier 9
    { id = 31842, ranks = {31842}, name = "Divine Illumination", icon = "Interface\\Icons\\Spell_Holy_HolyGuidance", maxRank = 1, tier = 9, col = 1, desc = "+5% Intellect as spell power." },
    { id = 53671, ranks = {53671, 53673, 54151, 54154, 54155}, name = "Judgements of the Pure", icon = "Interface\\Icons\\Spell_Holy_DivineIllumination", maxRank = 5, tier = 9, col = 3, desc = "50% mana cost reduction for 15 sec." },
    -- Tier 10
    { id = 53569, ranks = {53569, 53576}, name = "Infusion of Light", icon = "Interface\\Icons\\Spell_Holy_JudgementOfThePure", maxRank = 2, tier = 10, col = 2, desc = "+3% haste after judging.", prereq = {7, 2} },
    { id = 53556, ranks = {53556, 53557}, name = "Enlightened Judgements", icon = "Interface\\Icons\\Spell_Holy_InfusionOfLight", maxRank = 2, tier = 10, col = 3, desc = "Holy Shock crit reduces Flash heal time." },
    -- Tier 11
    { id = 53563, ranks = {53563}, name = "Beacon of Light", icon = "Interface\\Icons\\Ability_Paladin_BeaconOfLight", maxRank = 1, tier = 11, col = 2, desc = "All heals also heal beacon target." },
}

-- PROTECTION (Spec 2)
local Protection = {
    -- Tier 1
    { id = 63646, ranks = {63646, 63647, 63648, 63649, 63650}, name = "Divinity", icon = "Interface\\Icons\\Spell_Holy_BlindingLight", maxRank = 5, tier = 1, col = 2, desc = "+1% healing done and received." },
    { id = 20262, ranks = {20262, 20263, 20264, 20265, 20266}, name = "Divine Strength", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 1, col = 3, desc = "" },
    -- Tier 2
    { id = 31844, ranks = {31844, 31845, 53519}, name = "Stoicism", icon = "Interface\\Icons\\Spell_Holy_DivineSacrifice", maxRank = 3, tier = 2, col = 1, desc = "Transfer 30% damage to you." },
    { id = 20174, ranks = {20174, 20175}, name = "Guardian's Favor", icon = "Interface\\Icons\\Spell_Holy_Devotion", maxRank = 2, tier = 2, col = 2, desc = "+2% armor from items." },
    { id = 20096, ranks = {20096, 20097, 20098, 20099, 20100}, name = "Anticipation", icon = "Interface\\Icons\\Spell_Holy_DivineGuardian", maxRank = 5, tier = 2, col = 3, desc = "Divine Sacrifice reduces raid damage." },
    -- Tier 3
    { id = 64205, ranks = {64205}, name = "Divine Sacrifice", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 3, col = 1, desc = "" },
    { id = 20468, ranks = {20468, 20469, 20470}, name = "Improved Righteous Fury", icon = "Interface\\Icons\\Spell_Holy_SealOfMight", maxRank = 3, tier = 3, col = 2, desc = "-10 sec HoJ cooldown." },
    { id = 20143, ranks = {20143, 20144, 20145, 20146, 20147}, name = "Toughness", icon = "Interface\\Icons\\Spell_Magic_LesserInvisibilty", maxRank = 5, tier = 3, col = 3, desc = "+1% dodge." },
    -- Tier 4
    { id = 53527, ranks = {53527, 53530}, name = "Divine Guardian", icon = "Interface\\Icons\\Spell_Holy_DevotionAura", maxRank = 2, tier = 4, col = 1, desc = "+17% armor, +2% healing.", prereq = {3, 1} },
    { id = 20487, ranks = {20487, 20488}, name = "Improved Hammer of Justice", icon = "Interface\\Icons\\Spell_Nature_LightningShield", maxRank = 2, tier = 4, col = 2, desc = "-3% damage taken, restores resources." },
    { id = 20138, ranks = {20138, 20139, 20140}, name = "Improved Devotion Aura", icon = "Interface\\Icons\\Ability_GolemStoneSkin", maxRank = 3, tier = 4, col = 3, desc = "+3% Strength." },
    -- Tier 5
    { id = 20911, ranks = {20911}, name = "Blessing of Sanctuary", icon = "Interface\\Icons\\Spell_Holy_Reckoning", maxRank = 1, tier = 5, col = 2, desc = "Chance to gain extra attack when hit." },
    { id = 20177, ranks = {20177, 20179, 20181, 20180, 20182}, name = "Reckoning", icon = "Interface\\Icons\\Spell_Holy_SacredDuty", maxRank = 5, tier = 5, col = 3, desc = "+4% Stamina." },
    -- Tier 6
    { id = 31848, ranks = {31848, 31849}, name = "Sacred Duty", icon = "Interface\\Icons\\INV_Sword_20", maxRank = 2, tier = 6, col = 1, desc = "+4% damage with 1H weapons." },
    { id = 20196, ranks = {20196, 20197, 20198}, name = "One-Handed Weapon Specialization", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 31785, ranks = {31785, 33776}, name = "Spiritual Attunement", icon = "Interface\\Icons\\Spell_Holy_ArdentDefender", maxRank = 2, tier = 7, col = 1, desc = "Reduces damage when low health." },
    { id = 20925, ranks = {20925}, name = "Holy Shield", icon = "Interface\\Icons\\Spell_Holy_BlessingOfProtection", maxRank = 1, tier = 7, col = 2, desc = "Increases block chance, deals holy damage.", prereq = {5, 2} },
    { id = 31850, ranks = {31850, 31851, 31852}, name = "Ardent Defender", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 3, desc = "" },
    -- Tier 8
    { id = 20127, ranks = {20127, 20130, 20135}, name = "Redoubt", icon = "Interface\\Icons\\Spell_Holy_Stoicism", maxRank = 3, tier = 8, col = 1, desc = "-10% stun duration." },
    { id = 31858, ranks = {31858, 31859, 31860}, name = "Combat Expertise", icon = "Interface\\Icons\\Spell_Holy_CombatExpertise", maxRank = 3, tier = 8, col = 3, desc = "+2% expertise and crit." },
    -- Tier 9
    { id = 53590, ranks = {53590, 53591, 53592}, name = "Touched by the Light", icon = "Interface\\Icons\\Ability_Defend", maxRank = 3, tier = 9, col = 1, desc = "+10% block chance after being crit." },
    { id = 31935, ranks = {31935}, name = "Avenger's Shield", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 9, col = 2, desc = "", prereq = {7, 2} },
    { id = 53583, ranks = {53583, 53585}, name = "Guarded by the Light", icon = "Interface\\Icons\\Ability_Paladin_TouchedByTheLight", maxRank = 2, tier = 9, col = 3, desc = "+20% Stamina as spell power." },
    -- Tier 10
    { id = 53709, ranks = {53709, 53710, 53711}, name = "Shield of the Templar", icon = "Interface\\Icons\\Ability_Paladin_GuardedByTheLight", maxRank = 3, tier = 10, col = 2, desc = "-6% spell damage, Divine Plea uninterruptible.", prereq = {9, 2} },
    { id = 53695, ranks = {53695, 53696}, name = "Judgements of the Just", icon = "Interface\\Icons\\Ability_Paladin_ShieldOfTheTemplar", maxRank = 2, tier = 10, col = 3, desc = "+10% Holy Shield/Avenger's Shield damage." },
    -- Tier 11
    { id = 53595, ranks = {53595}, name = "Hammer of the Righteous", icon = "Interface\\Icons\\Ability_Paladin_ShieldOfTheRighteous", maxRank = 1, tier = 11, col = 2, desc = "Slam shield, dealing holy damage based on block value." },
}

-- RETRIBUTION (Spec 3)
local Retribution = {
    -- Tier 1
    { id = 20060, ranks = {20060, 20061, 20062, 20063, 20064}, name = "Deflection", icon = "Interface\\Icons\\Ability_Parry", maxRank = 5, tier = 1, col = 2, desc = "+1% Parry." },
    { id = 20101, ranks = {20101, 20102, 20103, 20104, 20105}, name = "Benediction", icon = "Interface\\Icons\\Spell_Frost_WindWalkOn", maxRank = 5, tier = 1, col = 3, desc = "-2% mana cost of instant spells." },
    -- Tier 2
    { id = 25956, ranks = {25956, 25957}, name = "Improved Judgements", icon = "Interface\\Icons\\Spell_Holy_RighteousFury", maxRank = 2, tier = 2, col = 1, desc = "-1 sec Judgement cooldown." },
    { id = 20335, ranks = {20335, 20336, 20337}, name = "Heart of the Crusader", icon = "Interface\\Icons\\Spell_Holy_HolySmite", maxRank = 3, tier = 2, col = 2, desc = "+3% crit against judged targets." },
    { id = 20042, ranks = {20042, 20045}, name = "Improved Blessing of Might", icon = "Interface\\Icons\\Spell_Holy_FistOfJustice", maxRank = 2, tier = 2, col = 3, desc = "+12.5% BoM effect." },
    -- Tier 3
    { id = 9452, ranks = {9452, 26016}, name = "Vindication", icon = "Interface\\Icons\\Spell_Holy_Vindication", maxRank = 2, tier = 3, col = 1, desc = "Attacks reduce target stats." },
    { id = 20117, ranks = {20117, 20118, 20119, 20120, 20121}, name = "Conviction", icon = "Interface\\Icons\\Spell_Holy_Retributionaura", maxRank = 5, tier = 3, col = 2, desc = "+1% melee crit." },
    { id = 20375, ranks = {20375}, name = "Seal of Command", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 3, col = 3, desc = "" },
    { id = 26022, ranks = {26022, 26023}, name = "Pursuit of Justice", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 3, col = 4, desc = "" },
    -- Tier 4
    { id = 9799, ranks = {9799, 25988}, name = "Eye for an Eye", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 1, desc = "" },
    { id = 32043, ranks = {32043, 35396, 35397}, name = "Sanctity of Battle", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 4, col = 3, desc = "" },
    { id = 31866, ranks = {31866, 31867, 31868}, name = "Crusade", icon = "Interface\\Icons\\Ability_Warrior_InnerRage", maxRank = 3, tier = 4, col = 4, desc = "Melee attacks deal additional Holy damage." },
    -- Tier 5
    { id = 20111, ranks = {20111, 20112, 20113}, name = "Two-Handed Weapon Specialization", icon = "Interface\\Icons\\Spell_Holy_PursuitOfJustice", maxRank = 3, tier = 5, col = 1, desc = "+8% movement speed, reduces Disarm duration." },
    { id = 31869, ranks = {31869}, name = "Sanctified Retribution", icon = "Interface\\Icons\\Spell_Holy_PowerInfusion", maxRank = 1, tier = 5, col = 3, desc = "+5% damage to Exorcism/Crusader Strike." },
    -- Tier 6
    { id = 20049, ranks = {20049, 20056, 20057}, name = "Vengeance", icon = "Interface\\Icons\\Spell_Holy_Crusade", maxRank = 3, tier = 6, col = 2, desc = "+1% damage, +1% vs Humanoids/Demons/Undead.", prereq = {3, 2} },
    { id = 31871, ranks = {31871, 31872}, name = "Divine Purpose", icon = "Interface\\Icons\\INV_Hammer_04", maxRank = 2, tier = 6, col = 3, desc = "+2% damage with 2H weapons." },
    -- Tier 7
    { id = 53486, ranks = {53486, 53488}, name = "The Art of War", icon = "Interface\\Icons\\Spell_Holy_EyeforanEye", maxRank = 2, tier = 7, col = 1, desc = "Reflect damage when crit by spells." },
    { id = 20066, ranks = {20066}, name = "Repentance", icon = "Interface\\Icons\\Spell_Holy_PrayerOfHealing", maxRank = 1, tier = 7, col = 2, desc = "Incapacitates target for 1 min." },
    { id = 31876, ranks = {31876, 31877, 31878}, name = "Judgements of the Wise", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 3, desc = "" },
    -- Tier 8
    { id = 31879, ranks = {31879, 31880, 31881}, name = "Fanaticism", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 8, col = 2, desc = "", prereq = {7, 2} },
    { id = 53375, ranks = {53375, 53376}, name = "Sanctified Wrath", icon = "Interface\\Icons\\Spell_Holy_SanctifiedRetribution", maxRank = 2, tier = 8, col = 3, desc = "+3% damage while in aura." },
    -- Tier 9
    { id = 53379, ranks = {53379, 53484, 53648}, name = "Swift Retribution", icon = "Interface\\Icons\\Ability_Paladin_TheArtOfWar", maxRank = 3, tier = 9, col = 1, desc = "Crits make Exorcism instant cast." },
    { id = 35395, ranks = {35395}, name = "Crusader Strike", icon = "Interface\\Icons\\Spell_Holy_CrusaderStrike", maxRank = 1, tier = 9, col = 2, desc = "Instant weapon attack, refreshes Judgements." },
    { id = 53501, ranks = {53501, 53502, 53503}, name = "Sheath of Light", icon = "Interface\\Icons\\Ability_Paladin_RighteousVengeance", maxRank = 3, tier = 9, col = 3, desc = "Crits leave DoT for 40% damage." },
    -- Tier 10
    { id = 53380, ranks = {53380, 53381, 53382}, name = "Righteous Vengeance", icon = "Interface\\Icons\\Ability_Paladin_SanctifiedWrath", maxRank = 3, tier = 10, col = 2, desc = "+25% Hammer of Wrath crit, reduces Avenging Wrath cooldown." },
    -- Tier 11
    { id = 53385, ranks = {53385}, name = "Divine Storm", icon = "Interface\\Icons\\Ability_Paladin_SwiftRetribution", maxRank = 1, tier = 11, col = 2, desc = "+3% haste from Retribution Aura." },
}

Adv2.Data.Talents[2] = {
    [1] = Holy,
    [2] = Protection,
    [3] = Retribution,
}
