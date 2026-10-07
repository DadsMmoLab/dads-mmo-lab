-- Druid Talent Data (Class 11)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- BALANCE (Spec 1)
local Balance = {
    -- Tier 1
    { id = 16814, ranks = {16814, 16815, 16816, 16817, 16818}, name = "Starlight Wrath", icon = "Interface\\Icons\\Spell_Nature_AbolishMagic", maxRank = 5, tier = 1, col = 2, desc = "-0.1 sec Wrath/Starfire cast." },
    { id = 57810, ranks = {57810, 57811, 57812, 57813, 57814}, name = "Genesis", icon = "Interface\\Icons\\Spell_Nature_StarFall", maxRank = 5, tier = 1, col = 3, desc = "+5% Moonfire damage and crit." },
    -- Tier 2
    { id = 16845, ranks = {16845, 16846, 16847}, name = "Moonglow", icon = "Interface\\Icons\\Spell_Arcane_ArcaneTorrent", maxRank = 3, tier = 2, col = 1, desc = "+1% periodic damage/healing." },
    { id = 35363, ranks = {35363, 35364}, name = "Nature's Majesty", icon = "Interface\\Icons\\Spell_Nature_Sentinal", maxRank = 2, tier = 2, col = 2, desc = "-3% Moonfire/Starfire/Wrath mana." },
    { id = 16821, ranks = {16821, 16822}, name = "Improved Moonfire", icon = "Interface\\Icons\\INV_Staff_01", maxRank = 2, tier = 2, col = 4, desc = "+2% Wrath/Starfire/etc crit." },
    -- Tier 3
    { id = 16836, ranks = {16836, 16839, 16840}, name = "Brambles", icon = "Interface\\Icons\\Spell_Nature_Thorns", maxRank = 3, tier = 3, col = 1, desc = "+25% Thorns damage, Treants root." },
    { id = 16880, ranks = {16880, 61345, 61346}, name = "Nature's Grace", icon = "Interface\\Icons\\Spell_Nature_NaturesBlessing", maxRank = 3, tier = 3, col = 2, desc = "Spell crits proc +20% haste.", prereq = {2, 2} },
    { id = 57865, ranks = {57865}, name = "Nature's Splendor", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 3, col = 3, desc = "", prereq = {2, 2} },
    { id = 16819, ranks = {16819, 16820}, name = "Nature's Reach", icon = "Interface\\Icons\\Spell_Nature_NaturesSplendor", maxRank = 2, tier = 3, col = 4, desc = "+3 sec Moonfire/Rejuv duration." },
    -- Tier 4
    { id = 16909, ranks = {16909, 16910, 16911, 16912, 16913}, name = "Vengeance", icon = "Interface\\Icons\\Spell_Nature_NaturesReach", maxRank = 5, tier = 4, col = 2, desc = "+10% Balance range, -15% threat." },
    { id = 16850, ranks = {16850, 16923, 16924}, name = "Celestial Focus", icon = "Interface\\Icons\\Spell_Nature_Purge", maxRank = 3, tier = 4, col = 3, desc = "+20% Starfire/Wrath crit damage." },
    -- Tier 5
    { id = 33589, ranks = {33589, 33590, 33591}, name = "Lunar Guidance", icon = "Interface\\Icons\\Spell_Arcane_StarFire", maxRank = 3, tier = 5, col = 1, desc = "+1% spell haste, Starfire stun chance." },
    { id = 5570, ranks = {5570}, name = "Insect Swarm", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 5, col = 2, desc = "" },
    { id = 57849, ranks = {57849, 57850, 57851}, name = "Improved Insect Swarm", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 5, col = 3, desc = "", prereq = {5, 2} },
    -- Tier 6
    { id = 33597, ranks = {33597, 33599, 33956}, name = "Dreamstate", icon = "Interface\\Icons\\Ability_Druid_LunarGuidance", maxRank = 3, tier = 6, col = 1, desc = "+4% Intellect as spell power." },
    { id = 16896, ranks = {16896, 16897, 16899}, name = "Moonfury", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 2, desc = "" },
    { id = 33592, ranks = {33592, 33596}, name = "Balance of Power", icon = "Interface\\Icons\\Spell_Nature_InsectSwarm", maxRank = 2, tier = 6, col = 3, desc = "+1% Wrath damage vs swarmed." },
    -- Tier 7
    { id = 24858, ranks = {24858}, name = "Moonkin Form", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 7, col = 2, desc = "" },
    { id = 48384, ranks = {48384, 48395, 48396}, name = "Improved Moonkin Form", icon = "Interface\\Icons\\Ability_Druid_BalanceOfPower", maxRank = 3, tier = 7, col = 3, desc = "+2% hit, +6% Spirit as spell power.", prereq = {7, 2} },
    { id = 33600, ranks = {33600, 33601, 33602}, name = "Improved Faerie Fire", icon = "Interface\\Icons\\Spell_Nature_Lightning", maxRank = 3, tier = 7, col = 4, desc = "+4% Intellect as mana regen." },
    -- Tier 8
    { id = 48389, ranks = {48389, 48392, 48393}, name = "Owlkin Frenzy", icon = "Interface\\Icons\\Ability_Druid_ImprovedMoonkinForm", maxRank = 3, tier = 8, col = 1, desc = "+3% spell haste aura.", prereq = {7, 2} },
    { id = 33603, ranks = {33603, 33604, 33605, 33606, 33607}, name = "Wrath of Cenarius", icon = "Interface\\Icons\\Spell_Nature_MoonGlow", maxRank = 5, tier = 8, col = 3, desc = "+3% Starfire/Moonfire/Wrath damage." },
    -- Tier 9
    { id = 48516, ranks = {48516, 48521, 48525}, name = "Eclipse", icon = "Interface\\Icons\\Spell_Nature_FaerieFire", maxRank = 3, tier = 9, col = 1, desc = "+1% crit vs FF targets." },
    { id = 50516, ranks = {50516}, name = "Typhoon", icon = "Interface\\Icons\\Ability_Druid_Typhoon", maxRank = 1, tier = 9, col = 2, desc = "Frontal cone knockback.", prereq = {7, 2} },
    { id = 33831, ranks = {33831}, name = "Force of Nature", icon = "Interface\\Icons\\Ability_Druid_ForceOfNature", maxRank = 1, tier = 9, col = 3, desc = "Summon 3 Treants." },
    { id = 48488, ranks = {48488, 48514}, name = "Gale Winds", icon = "Interface\\Icons\\Ability_Druid_OwlkinFrenzy", maxRank = 2, tier = 9, col = 4, desc = "Attacks proc +10% damage." },
    -- Tier 10
    { id = 48506, ranks = {48506, 48510, 48511}, name = "Earth and Moon", icon = "Interface\\Icons\\Ability_Druid_TwilightsWrath", maxRank = 3, tier = 10, col = 2, desc = "+4% Starfire spell power, +2% Wrath." },
    -- Tier 11
    { id = 48505, ranks = {48505}, name = "Starfall", icon = "Interface\\Icons\\Ability_Druid_GaleWinds", maxRank = 1, tier = 11, col = 2, desc = "+15% Hurricane/Typhoon damage." },
}

-- FERAL COMBAT (Spec 2)
local FeralCombat = {
    -- Tier 1
    { id = 16934, ranks = {16934, 16935, 16936, 16937, 16938}, name = "Ferocity", icon = "Interface\\Icons\\Ability_Hunter_Pet_Hyena", maxRank = 5, tier = 1, col = 2, desc = "-1 energy/rage on abilities." },
    { id = 16858, ranks = {16858, 16859, 16860, 16861, 16862}, name = "Feral Aggression", icon = "Interface\\Icons\\Ability_Druid_DemoralizingRoar", maxRank = 5, tier = 1, col = 3, desc = "+8% Ferocious Bite, +8% Demo Roar." },
    -- Tier 2
    { id = 16947, ranks = {16947, 16948, 16949}, name = "Feral Instinct", icon = "Interface\\Icons\\Ability_Ambush", maxRank = 3, tier = 2, col = 1, desc = "+5% Swipe damage, stealth detection." },
    { id = 16998, ranks = {16998, 16999}, name = "Savage Fury", icon = "Interface\\Icons\\Ability_Druid_Rake", maxRank = 2, tier = 2, col = 2, desc = "+10% Claw/Rake/Mangle damage." },
    { id = 16929, ranks = {16929, 16930, 16931}, name = "Thick Hide", icon = "Interface\\Icons\\INV_Misc_Pelt_Bear_03", maxRank = 3, tier = 2, col = 3, desc = "+4% armor from items." },
    -- Tier 3
    { id = 17002, ranks = {17002, 24866}, name = "Feral Swiftness", icon = "Interface\\Icons\\Spell_Nature_SpiritWolf", maxRank = 2, tier = 3, col = 1, desc = "+4% Cat dodge, +15% outdoor speed." },
    { id = 61336, ranks = {61336}, name = "Survival Instincts", icon = "Interface\\Icons\\Ability_Druid_TigersRoar", maxRank = 1, tier = 3, col = 2, desc = "+30% health for 20 sec." },
    { id = 16942, ranks = {16942, 16943, 16944}, name = "Sharpened Claws", icon = "Interface\\Icons\\Spell_Shadow_VampiricAura", maxRank = 3, tier = 3, col = 3, desc = "-9 energy on Shred, -1 rage on Lacerate." },
    -- Tier 4
    { id = 16966, ranks = {16966, 16968}, name = "Shredding Attacks", icon = "Interface\\Icons\\Ability_Hunter_Pet_Cat", maxRank = 2, tier = 4, col = 1, desc = "+50% level AP, finishing moves proc free spell." },
    { id = 16972, ranks = {16972, 16974, 16975}, name = "Predatory Strikes", icon = "Interface\\Icons\\Ability_Racial_Cannibalize", maxRank = 3, tier = 4, col = 2, desc = "Crits give combo point or rage." },
    { id = 37116, ranks = {37116, 37117}, name = "Primal Fury", icon = "Interface\\Icons\\Ability_Druid_PrimalPrecision", maxRank = 2, tier = 4, col = 3, desc = "+5 expertise, refund on miss.", prereq = {3, 3} },
    { id = 48409, ranks = {48409, 48410}, name = "Primal Precision", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 4, desc = "", prereq = {3, 3} },
    -- Tier 5
    { id = 16940, ranks = {16940, 16941}, name = "Brutal Impact", icon = "Interface\\Icons\\Ability_Druid_Bash", maxRank = 2, tier = 5, col = 1, desc = "+1 sec Bash stun, -30 sec Pounce CD." },
    { id = 49377, ranks = {49377}, name = "Feral Charge", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 5, col = 3, desc = "" },
    { id = 33872, ranks = {33872, 33873}, name = "Nurturing Instinct", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 5, col = 4, desc = "" },
    -- Tier 6
    { id = 57878, ranks = {57878, 57880, 57881}, name = "Natural Reaction", icon = "Interface\\Icons\\Ability_Druid_NurturingInstinct", maxRank = 3, tier = 6, col = 1, desc = "+35% Agility as healing." },
    { id = 17003, ranks = {17003, 17004, 17005, 17006, 24894}, name = "Heart of the Wild", icon = "Interface\\Icons\\Ability_Hunter_Pet_Bear", maxRank = 5, tier = 6, col = 2, desc = "Charge in Cat/Bear form.", prereq = {4, 2} },
    { id = 33853, ranks = {33853, 33855, 33856}, name = "Survival of the Fittest", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 17007, ranks = {17007}, name = "Leader of the Pack", icon = "Interface\\Icons\\Spell_Holy_BlessingOfAgility", maxRank = 1, tier = 7, col = 2, desc = "+4% Intellect, +2% Stamina Bear, +4% AP Cat." },
    { id = 34297, ranks = {34297, 34300}, name = "Improved Leader of the Pack", icon = "Interface\\Icons\\Ability_Druid_EnragedRegeneration", maxRank = 2, tier = 7, col = 3, desc = "+2% all stats, -2% crit chance vs you.", prereq = {7, 2} },
    { id = 33851, ranks = {33851, 33852, 33957}, name = "Primal Tenacity", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 4, desc = "" },
    -- Tier 8
    { id = 57873, ranks = {57873, 57876, 57877}, name = "Protector of the Pack", icon = "Interface\\Icons\\Ability_Druid_NurturingInstinct", maxRank = 3, tier = 8, col = 1, desc = "+35% Agility as healing.", prereq = {7, 2} },
    { id = 33859, ranks = {33859, 33866, 33867}, name = "Predatory Instincts", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 8, col = 3, desc = "" },
    { id = 48483, ranks = {48483, 48484, 48485}, name = "Infected Wounds", icon = "Interface\\Icons\\Ability_Druid_ProtectorOfThePack", maxRank = 3, tier = 8, col = 4, desc = "+2% damage, -4% damage taken in Bear." },
    -- Tier 9
    { id = 48492, ranks = {48492, 48494, 48495}, name = "King of the Jungle", icon = "Interface\\Icons\\Ability_Druid_PredatoryInstincts", maxRank = 3, tier = 9, col = 1, desc = "+5% crit damage, -10% AoE damage in Cat." },
    { id = 33917, ranks = {33917}, name = "Mangle", icon = "Interface\\Icons\\Ability_Druid_Mangle2", maxRank = 1, tier = 9, col = 2, desc = "Bleed damage debuff attack.", prereq = {7, 2} },
    { id = 48532, ranks = {48532, 48489, 48491}, name = "Improved Mangle", icon = "Interface\\Icons\\Ability_Druid_Mangle2", maxRank = 3, tier = 9, col = 3, desc = "-1.5 sec Bear Mangle CD, -3 energy Cat Mangle.", prereq = {9, 2} },
    -- Tier 10
    { id = 48432, ranks = {48432, 48433, 48434, 51268, 51269}, name = "Rend and Tear", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 10, col = 2, desc = "" },
    { id = 63503, ranks = {63503}, name = "Primal Gore", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 10, col = 3, desc = "", prereq = {10, 2} },
    -- Tier 11
    { id = 50334, ranks = {50334}, name = "Berserk", icon = "Interface\\Icons\\Ability_Druid_PrimalGore", maxRank = 1, tier = 11, col = 2, desc = "Lacerate and Rip can crit." },
}

-- RESTORATION (Spec 3)
local Restoration = {
    -- Tier 1
    { id = 17050, ranks = {17050, 17051}, name = "Improved Mark of the Wild", icon = "Interface\\Icons\\Spell_Nature_Regeneration", maxRank = 2, tier = 1, col = 1, desc = "+20% MotW effect." },
    { id = 17063, ranks = {17063, 17065, 17066}, name = "Nature's Focus", icon = "Interface\\Icons\\Spell_Nature_HealingWaveGreater", maxRank = 3, tier = 1, col = 2, desc = "+23% pushback resist on healing." },
    { id = 17056, ranks = {17056, 17058, 17059, 17060, 17061}, name = "Furor", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 1, col = 3, desc = "" },
    -- Tier 2
    { id = 17069, ranks = {17069, 17070, 17071, 17072, 17073}, name = "Naturalist", icon = "Interface\\Icons\\Spell_Holy_BlessingOfStamina", maxRank = 5, tier = 2, col = 1, desc = "Energy/Rage on shapeshift." },
    { id = 17118, ranks = {17118, 17119, 17120}, name = "Subtlety", icon = "Interface\\Icons\\Spell_Nature_Regeneration", maxRank = 3, tier = 2, col = 2, desc = "-0.1 sec Healing Touch cast, +2% physical damage." },
    { id = 16833, ranks = {16833, 16834, 16835}, name = "Natural Shapeshifter", icon = "Interface\\Icons\\Spell_Nature_CrystalBall", maxRank = 3, tier = 2, col = 3, desc = "Attacks/spells can proc Clearcasting." },
    -- Tier 3
    { id = 17106, ranks = {17106, 17107, 17108}, name = "Intensity", icon = "Interface\\Icons\\Spell_Fire_LavaSpawn", maxRank = 3, tier = 3, col = 1, desc = "+17% mana regen while casting." },
    { id = 16864, ranks = {16864}, name = "Omen of Clarity", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 3, col = 2, desc = "" },
    { id = 48411, ranks = {48411, 48412}, name = "Master Shapeshifter", icon = "Interface\\Icons\\Ability_Druid_EmpoweredRejuvenation", maxRank = 2, tier = 3, col = 3, desc = "+4% spell power to HoTs.", prereq = {2, 3} },
    -- Tier 4
    { id = 24968, ranks = {24968, 24969, 24970, 24971, 24972}, name = "Tranquil Spirit", icon = "Interface\\Icons\\Spell_Nature_Rejuvenation", maxRank = 5, tier = 4, col = 2, desc = "+5% Rejuvenation healing." },
    { id = 17111, ranks = {17111, 17112, 17113}, name = "Improved Rejuvenation", icon = "Interface\\Icons\\Ability_Druid_MasterShapeShifter", maxRank = 3, tier = 4, col = 3, desc = "+2% damage/healing/crit in forms." },
    -- Tier 5
    { id = 17116, ranks = {17116}, name = "Nature's Swiftness", icon = "Interface\\Icons\\Spell_Holy_ElunesGrace", maxRank = 1, tier = 5, col = 1, desc = "-2% Healing Touch/Nourish mana.", prereq = {3, 1} },
    { id = 17104, ranks = {17104, 24943, 24944, 24945, 24946}, name = "Gift of Nature", icon = "Interface\\Icons\\Ability_Eyeoftheowl", maxRank = 5, tier = 5, col = 2, desc = "-10% threat." },
    { id = 17123, ranks = {17123, 17124}, name = "Improved Tranquility", icon = "Interface\\Icons\\Spell_Nature_WispSplode", maxRank = 2, tier = 5, col = 4, desc = "-10% shapeshift mana." },
    -- Tier 6
    { id = 33879, ranks = {33879, 33880}, name = "Empowered Touch", icon = "Interface\\Icons\\Ability_Druid_EmpoweredTouch", maxRank = 2, tier = 6, col = 1, desc = "+20% spell power to HT/Nourish." },
    { id = 17074, ranks = {17074, 17075, 17076, 17077, 17078}, name = "Nature's Bounty", icon = "Interface\\Icons\\Spell_Nature_RavenForm", maxRank = 5, tier = 6, col = 3, desc = "Next Nature spell instant.", prereq = {4, 3} },
    -- Tier 7
    { id = 34151, ranks = {34151, 34152, 34153}, name = "Living Spirit", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 1, desc = "" },
    { id = 18562, ranks = {18562}, name = "Swiftmend", icon = "Interface\\Icons\\INV_Relics_IdolofRejuvenation", maxRank = 1, tier = 7, col = 2, desc = "Consume HoT for instant heal.", prereq = {5, 2} },
    { id = 33881, ranks = {33881, 33882, 33883}, name = "Natural Perfection", icon = "Interface\\Icons\\Spell_Nature_NaturesBounty", maxRank = 3, tier = 7, col = 3, desc = "+5% Regrowth/Nourish crit." },
    -- Tier 8
    { id = 33886, ranks = {33886, 33887, 33888, 33889, 33890}, name = "Empowered Rejuvenation", icon = "Interface\\Icons\\Spell_Nature_GiftOfTheWaterSpirit", maxRank = 5, tier = 8, col = 2, desc = "+5% Spirit." },
    { id = 48496, ranks = {48496, 48499, 48500}, name = "Living Seed", icon = "Interface\\Icons\\Ability_Druid_GiftOfTheEarthMother", maxRank = 3, tier = 8, col = 3, desc = "Crit heals leave Living Seed." },
    -- Tier 9
    { id = 48539, ranks = {48539, 48544, 48545}, name = "Revitalize", icon = "Interface\\Icons\\Ability_Druid_ImprovedTreeForm", maxRank = 3, tier = 9, col = 1, desc = "+5% spell power in Tree, +15% armor." },
    { id = 65139, ranks = {65139}, name = "Tree of Life", icon = "Interface\\Icons\\Spell_Nature_StoneClawTotem", maxRank = 1, tier = 9, col = 2, desc = "+5% Barkskin reduction, grants to Tree.", prereq = {8, 2} },
    { id = 48535, ranks = {48535, 48536, 48537}, name = "Improved Tree of Life", icon = "Interface\\Icons\\Ability_Druid_Revitalize", maxRank = 3, tier = 9, col = 3, desc = "Rejuv/WG can proc mana/energy/rage.", prereq = {9, 2} },
    -- Tier 10
    { id = 63410, ranks = {63410, 63411}, name = "Improved Barkskin", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 10, col = 1, desc = "" },
    { id = 51179, ranks = {51179, 51180, 51181, 51182, 51183}, name = "Gift of the Earthmother", icon = "Interface\\Icons\\Ability_Druid_GiftOfTheEarthMother", maxRank = 5, tier = 10, col = 3, desc = "-4% Lifebloom GCD, +2% instant heal." },
    -- Tier 11
    { id = 48438, ranks = {48438}, name = "Wild Growth", icon = "Interface\\Icons\\Ability_Druid_Flourish", maxRank = 1, tier = 11, col = 2, desc = "Smart HoT on up to 5 targets.", prereq = {9, 2} },
}

Adv2.Data.Talents[11] = {
    [1] = Balance,
    [2] = FeralCombat,
    [3] = Restoration,
}
