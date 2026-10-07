-- Priest Talent Data (Class 5)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- DISCIPLINE (Spec 1)
local Discipline = {
    -- Tier 1
    { id = 14522, ranks = {14522, 14788, 14789, 14790, 14791}, name = "Unbreakable Will", icon = "Interface\\Icons\\Spell_Magic_MageArmor", maxRank = 5, tier = 1, col = 2, desc = "+3% Fear/Stun/Silence resist." },
    { id = 47586, ranks = {47586, 47587, 47588, 52802, 52803}, name = "Twin Disciplines", icon = "Interface\\Icons\\Spell_Holy_InnerFire", maxRank = 5, tier = 1, col = 3, desc = "+15% Inner Fire armor, +6 charges." },
    -- Tier 2
    { id = 14523, ranks = {14523, 14784, 14785}, name = "Silent Resolve", icon = "Interface\\Icons\\Spell_Nature_ManaRegenTotem", maxRank = 3, tier = 2, col = 1, desc = "-7% threat." },
    { id = 14747, ranks = {14747, 14770, 14771}, name = "Improved Inner Fire", icon = "Interface\\Icons\\Spell_Holy_WordFortitude", maxRank = 3, tier = 2, col = 2, desc = "+15% Fortitude effect." },
    { id = 14749, ranks = {14749, 14767}, name = "Improved Power Word: Fortitude", icon = "Interface\\Icons\\Spell_Nature_Tranquility", maxRank = 2, tier = 2, col = 3, desc = "Crits reduce pushback on next spell." },
    { id = 14531, ranks = {14531, 14774}, name = "Martyrdom", icon = "Interface\\Icons\\Spell_Nature_Sleep", maxRank = 2, tier = 2, col = 4, desc = "+17% mana regen while casting." },
    -- Tier 3
    { id = 14521, ranks = {14521, 14776, 14777}, name = "Meditation", icon = "Interface\\Icons\\Spell_Frost_WindWalkOn", maxRank = 3, tier = 3, col = 1, desc = "Next spell is free and +25% crit." },
    { id = 14751, ranks = {14751}, name = "Inner Focus", icon = "Interface\\Icons\\Spell_Shadow_ManaBurn", maxRank = 1, tier = 3, col = 2, desc = "-0.5 sec Mana Burn cast time." },
    { id = 14748, ranks = {14748, 14768, 14769}, name = "Improved Power Word: Shield", icon = "Interface\\Icons\\Spell_Holy_PowerWordShield", maxRank = 3, tier = 3, col = 3, desc = "+5% PW:S absorption." },
    -- Tier 4
    { id = 33167, ranks = {33167, 33171, 33172}, name = "Absolution", icon = "Interface\\Icons\\Spell_Holy_Absolution", maxRank = 3, tier = 4, col = 1, desc = "-5% Dispel/Cure/Abolish mana cost." },
    { id = 14520, ranks = {14520, 14780, 14781}, name = "Mental Agility", icon = "Interface\\Icons\\Ability_Hibernation", maxRank = 3, tier = 4, col = 2, desc = "-4% instant spell mana cost." },
    { id = 14750, ranks = {14750, 14772}, name = "Improved Mana Burn", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 4, desc = "" },
    -- Tier 5
    { id = 33201, ranks = {33201, 33202}, name = "Reflective Shield", icon = "Interface\\Icons\\Spell_Holy_PowerWordShield", maxRank = 2, tier = 5, col = 1, desc = "PW:S reflects 22% damage." },
    { id = 18551, ranks = {18551, 18552, 18553, 18554, 18555}, name = "Mental Strength", icon = "Interface\\Icons\\Spell_Nature_EnchantArmor", maxRank = 5, tier = 5, col = 2, desc = "+3% Intellect." },
    { id = 63574, ranks = {63574}, name = "Soul Warding", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 5, col = 3, desc = "", prereq = {3, 3} },
    -- Tier 6
    { id = 33186, ranks = {33186, 33190}, name = "Focused Power", icon = "Interface\\Icons\\Spell_Holy_PowerInfusion", maxRank = 2, tier = 6, col = 1, desc = "+2% damage and Mass Dispel cast time." },
    { id = 34908, ranks = {34908, 34909, 34910}, name = "Enlightenment", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 45234, ranks = {45234, 45243, 45244}, name = "Focused Will", icon = "Interface\\Icons\\Spell_Holy_SealOfProtection", maxRank = 3, tier = 7, col = 1, desc = "Removes PW:S cooldown in Disc." },
    { id = 10060, ranks = {10060}, name = "Power Infusion", icon = "Interface\\Icons\\Spell_Holy_PowerInfusion", maxRank = 1, tier = 7, col = 2, desc = "+20% spell haste, -20% mana cost.", prereq = {5, 2} },
    { id = 63504, ranks = {63504, 63505, 63506}, name = "Improved Flash Heal", icon = "Interface\\Icons\\Spell_Holy_HolyProtection", maxRank = 3, tier = 7, col = 3, desc = "Crit heals reduce damage on target." },
    -- Tier 8
    { id = 57470, ranks = {57470, 57472}, name = "Renewed Hope", icon = "Interface\\Icons\\Spell_Holy_MindSooth", maxRank = 2, tier = 8, col = 1, desc = "+2% haste and Spirit." },
    { id = 47535, ranks = {47535, 47536, 47537}, name = "Rapture", icon = "Interface\\Icons\\Spell_Holy_Rapture", maxRank = 3, tier = 8, col = 2, desc = "PW:S restores mana." },
    { id = 47507, ranks = {47507, 47508}, name = "Aspiration", icon = "Interface\\Icons\\Spell_Holy_Aspiration", maxRank = 2, tier = 8, col = 3, desc = "-10% Inner Focus/Penance cooldown." },
    -- Tier 9
    { id = 47509, ranks = {47509, 47511, 47515}, name = "Divine Aegis", icon = "Interface\\Icons\\Spell_Holy_DevineAegis", maxRank = 3, tier = 9, col = 1, desc = "Crit heals create absorption shield." },
    { id = 33206, ranks = {33206}, name = "Pain Suppression", icon = "Interface\\Icons\\Spell_Holy_PainSupression", maxRank = 1, tier = 9, col = 2, desc = "-40% damage taken for 8 sec." },
    { id = 47516, ranks = {47516, 47517}, name = "Grace", icon = "Interface\\Icons\\Spell_Holy_HopeAndGrace", maxRank = 2, tier = 9, col = 3, desc = "Heals increase healing on target." },
    -- Tier 10
    { id = 52795, ranks = {52795, 52797, 52798, 52799, 52800}, name = "Borrowed Time", icon = "Interface\\Icons\\Spell_Holy_BorrowedTime", maxRank = 5, tier = 10, col = 2, desc = "PW:S grants haste." },
    -- Tier 11
    { id = 47540, ranks = {47540}, name = "Penance", icon = "Interface\\Icons\\Spell_Holy_Penance", maxRank = 1, tier = 11, col = 2, desc = "Channeled heal or damage." },
}

-- HOLY (Spec 2)
local Holy = {
    -- Tier 1
    { id = 14913, ranks = {14913, 15012}, name = "Healing Focus", icon = "Interface\\Icons\\Spell_Holy_HealingFocus", maxRank = 2, tier = 1, col = 1, desc = "+35% pushback resist on heals." },
    { id = 14908, ranks = {14908, 15020, 17191}, name = "Improved Renew", icon = "Interface\\Icons\\Spell_Holy_Renew", maxRank = 3, tier = 1, col = 2, desc = "+5% Renew effect." },
    { id = 14889, ranks = {14889, 15008, 15009, 15010, 15011}, name = "Holy Specialization", icon = "Interface\\Icons\\Spell_Holy_SealOfSalvation", maxRank = 5, tier = 1, col = 3, desc = "+1% Holy crit." },
    -- Tier 2
    { id = 27900, ranks = {27900, 27901, 27902, 27903, 27904}, name = "Spell Warding", icon = "Interface\\Icons\\Spell_Holy_SpellWarding", maxRank = 5, tier = 2, col = 2, desc = "-2% spell damage taken." },
    { id = 18530, ranks = {18530, 18531, 18533, 18534, 18535}, name = "Divine Fury", icon = "Interface\\Icons\\Spell_Holy_SearingLightPriest", maxRank = 5, tier = 2, col = 3, desc = "-0.1 sec Smite/HF/Heal/GH cast time." },
    -- Tier 3
    { id = 19236, ranks = {19236}, name = "Desperate Prayer", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 3, col = 1, desc = "" },
    { id = 27811, ranks = {27811, 27815, 27816}, name = "Blessed Recovery", icon = "Interface\\Icons\\Spell_Holy_Restoration", maxRank = 3, tier = 3, col = 2, desc = "Instant self-heal." },
    { id = 14892, ranks = {14892, 15362, 15363}, name = "Inspiration", icon = "Interface\\Icons\\Spell_Holy_BlessedRecovery", maxRank = 3, tier = 3, col = 4, desc = "Crits heal you over time." },
    -- Tier 4
    { id = 27789, ranks = {27789, 27790}, name = "Holy Reach", icon = "Interface\\Icons\\Spell_Holy_LayOnHands", maxRank = 2, tier = 4, col = 1, desc = "Crit heals increase target armor." },
    { id = 14912, ranks = {14912, 15013, 15014}, name = "Improved Healing", icon = "Interface\\Icons\\Spell_Holy_HolyNova", maxRank = 3, tier = 4, col = 2, desc = "+20% Holy Nova/PoH/CoH range." },
    { id = 14909, ranks = {14909, 15017}, name = "Searing Light", icon = "Interface\\Icons\\Spell_Holy_Heal02", maxRank = 2, tier = 4, col = 3, desc = "-5% Heal/GH/Binding Heal mana cost.", prereq = {2, 3} },
    -- Tier 5
    { id = 14911, ranks = {14911, 15018}, name = "Healing Prayers", icon = "Interface\\Icons\\Spell_Holy_SearingLight", maxRank = 2, tier = 5, col = 1, desc = "+5% Smite/Holy Fire damage." },
    { id = 20711, ranks = {20711}, name = "Spirit of Redemption", icon = "Interface\\Icons\\INV_Enchant_EssenceAstralSmall", maxRank = 1, tier = 5, col = 2, desc = "+5% Spirit, become angel on death." },
    { id = 14901, ranks = {14901, 15028, 15029, 15030, 15031}, name = "Spiritual Guidance", icon = "Interface\\Icons\\Spell_Holy_PrayerOfHealing02", maxRank = 5, tier = 5, col = 3, desc = "-10% PoH/Binding Heal mana cost." },
    -- Tier 6
    { id = 33150, ranks = {33150, 33154}, name = "Surge of Light", icon = "Interface\\Icons\\Spell_Nature_MoonGlow", maxRank = 2, tier = 6, col = 1, desc = "+2% healing." },
    { id = 14898, ranks = {14898, 15349, 15354, 15355, 15356}, name = "Spiritual Healing", icon = "Interface\\Icons\\Spell_Holy_SpiritualGuidence", maxRank = 5, tier = 6, col = 3, desc = "+5% Spirit as spell power." },
    -- Tier 7
    { id = 34753, ranks = {34753, 34859, 34860}, name = "Holy Concentration", icon = "Interface\\Icons\\Spell_Holy_SurgeOfLight", maxRank = 3, tier = 7, col = 1, desc = "Crits can make Smite/Flash Heal free." },
    { id = 724, ranks = {724}, name = "Lightwell", icon = "Interface\\Icons\\Spell_Holy_SummonLightwell", maxRank = 1, tier = 7, col = 2, desc = "Creates healing Lightwell.", prereq = {5, 2} },
    { id = 33142, ranks = {33142, 33145, 33146}, name = "Blessed Resilience", icon = "Interface\\Icons\\Spell_Holy_BlessedResillience", maxRank = 3, tier = 7, col = 3, desc = "+1% crit, heals more when crit." },
    -- Tier 8
    { id = 64127, ranks = {64127, 64129}, name = "Body and Soul", icon = "Interface\\Icons\\Spell_Holy_BodyAndSoul", maxRank = 2, tier = 8, col = 1, desc = "PW:S increases movement speed." },
    { id = 33158, ranks = {33158, 33159, 33160, 33161, 33162}, name = "Empowered Healing", icon = "Interface\\Icons\\Spell_Holy_GreaterHeal", maxRank = 5, tier = 8, col = 2, desc = "+8% GH/FL/Binding bonus coeff." },
    { id = 63730, ranks = {63730, 63733, 63737}, name = "Serendipity", icon = "Interface\\Icons\\Spell_Holy_Serendipity", maxRank = 3, tier = 8, col = 3, desc = "FH/BH reduces GH/PoH cast time." },
    -- Tier 9
    { id = 63534, ranks = {63534, 63542, 63543}, name = "Empowered Renew", icon = "Interface\\Icons\\Spell_Holy_HolyConcentration", maxRank = 3, tier = 9, col = 1, desc = "Crit heals proc mana regen." },
    { id = 34861, ranks = {34861}, name = "Circle of Healing", icon = "Interface\\Icons\\Spell_Holy_CircleOfRenewal", maxRank = 1, tier = 9, col = 2, desc = "AoE heal around target." },
    { id = 47558, ranks = {47558, 47559, 47560}, name = "Test of Faith", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 3, desc = "" },
    -- Tier 10
    { id = 47562, ranks = {47562, 47564, 47565, 47566, 47567}, name = "Divine Providence", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 10, col = 2, desc = "" },
    -- Tier 11
    { id = 47788, ranks = {47788}, name = "Guardian Spirit", icon = "Interface\\Icons\\Spell_Holy_GuardianSpirit", maxRank = 1, tier = 11, col = 2, desc = "Prevents death, heals to 50%." },
}

-- SHADOW (Spec 3)
local Shadow = {
    -- Tier 1
    { id = 15270, ranks = {15270, 15335, 15336}, name = "Spirit Tap", icon = "Interface\\Icons\\Spell_Shadow_Requiem", maxRank = 3, tier = 1, col = 1, desc = "Killing blow restores mana." },
    { id = 15337, ranks = {15337, 15338}, name = "Improved Spirit Tap", icon = "Interface\\Icons\\Spell_Shadow_Requiem", maxRank = 2, tier = 1, col = 2, desc = "SW:D crits can proc Spirit Tap.", prereq = {1, 1} },
    { id = 15259, ranks = {15259, 15307, 15308, 15309, 15310}, name = "Darkness", icon = "Interface\\Icons\\Spell_Shadow_Twilight", maxRank = 5, tier = 1, col = 3, desc = "+2% Shadow damage." },
    -- Tier 2
    { id = 15318, ranks = {15318, 15272, 15320}, name = "Shadow Affinity", icon = "Interface\\Icons\\Spell_Shadow_ShadowWard", maxRank = 3, tier = 2, col = 1, desc = "-8% Shadow threat." },
    { id = 15275, ranks = {15275, 15317}, name = "Improved Shadow Word: Pain", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordPain", maxRank = 2, tier = 2, col = 2, desc = "+3% SW:P damage." },
    { id = 15260, ranks = {15260, 15327, 15328}, name = "Shadow Focus", icon = "Interface\\Icons\\Spell_Shadow_BurningSpirit", maxRank = 3, tier = 2, col = 3, desc = "+1% Shadow spell hit." },
    -- Tier 3
    { id = 15392, ranks = {15392, 15448}, name = "Improved Psychic Scream", icon = "Interface\\Icons\\Spell_Shadow_PsychicScream", maxRank = 2, tier = 3, col = 1, desc = "-2 sec Psychic Scream cooldown." },
    { id = 15273, ranks = {15273, 15312, 15313, 15314, 15316}, name = "Improved Mind Blast", icon = "Interface\\Icons\\Spell_Shadow_UnholyFrenzy", maxRank = 5, tier = 3, col = 2, desc = "-0.5 sec Mind Blast cooldown." },
    { id = 15407, ranks = {15407}, name = "Mind Flay", icon = "Interface\\Icons\\Spell_Shadow_SiphonMana", maxRank = 1, tier = 3, col = 3, desc = "Channeled damage and slow." },
    -- Tier 4
    { id = 15274, ranks = {15274, 15311}, name = "Veiled Shadows", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 2, desc = "" },
    { id = 17322, ranks = {17322, 17323}, name = "Shadow Reach", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 4, col = 3, desc = "" },
    { id = 15257, ranks = {15257, 15331, 15332}, name = "Shadow Weaving", icon = "Interface\\Icons\\Spell_Shadow_ChillTouch", maxRank = 3, tier = 4, col = 4, desc = "+10% Shadow spell range." },
    -- Tier 5
    { id = 15487, ranks = {15487}, name = "Silence", icon = "Interface\\Icons\\Spell_Shadow_ImprovedVampiricEmbrace", maxRank = 1, tier = 5, col = 1, desc = "+33% VE healing.", prereq = {3, 1} },
    { id = 15286, ranks = {15286}, name = "Vampiric Embrace", icon = "Interface\\Icons\\Spell_Magic_LesserInvisibilty", maxRank = 1, tier = 5, col = 2, desc = "-30 sec Fade cooldown." },
    { id = 27839, ranks = {27839, 27840}, name = "Improved Vampiric Embrace", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 5, col = 3, desc = "", prereq = {5, 2} },
    { id = 33213, ranks = {33213, 33214, 33215}, name = "Focused Mind", icon = "Interface\\Icons\\Spell_Nature_FocusedMind", maxRank = 3, tier = 5, col = 4, desc = "-5% Mind spells mana cost." },
    -- Tier 6
    { id = 14910, ranks = {14910, 33371}, name = "Mind Melt", icon = "Interface\\Icons\\Spell_Shadow_ImpPhaseShift", maxRank = 2, tier = 6, col = 1, desc = "Silence target for 5 sec." },
    { id = 63625, ranks = {63625, 63626, 63627}, name = "Improved Devouring Plague", icon = "Interface\\Icons\\Spell_Shadow_DevouringPlague", maxRank = 3, tier = 6, col = 3, desc = "DP deals instant damage." },
    -- Tier 7
    { id = 15473, ranks = {15473}, name = "Shadowform", icon = "Interface\\Icons\\Spell_Shadow_Shadowform", maxRank = 1, tier = 7, col = 2, desc = "+15% Shadow damage, -15% physical.", prereq = {5, 2} },
    { id = 33221, ranks = {33221, 33222, 33223, 33224, 33225}, name = "Shadow Power", icon = "Interface\\Icons\\Spell_Shadow_Blackplague", maxRank = 5, tier = 7, col = 3, desc = "Shadow damage stacks +1% damage." },
    -- Tier 8
    { id = 47569, ranks = {47569, 47570}, name = "Improved Shadowform", icon = "Interface\\Icons\\Spell_Shadow_Misery", maxRank = 2, tier = 8, col = 1, desc = "+1% spell hit from SW:P.", prereq = {7, 2} },
    { id = 33191, ranks = {33191, 33192, 33193}, name = "Misery", icon = "Interface\\Icons\\Spell_Shadow_Shadowform", maxRank = 3, tier = 8, col = 3, desc = "Fade removes snares in Shadowform." },
    -- Tier 9
    { id = 64044, ranks = {64044}, name = "Psychic Horror", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 9, col = 1, desc = "" },
    { id = 34914, ranks = {34914}, name = "Vampiric Touch", icon = "Interface\\Icons\\Spell_Holy_Stoicism", maxRank = 1, tier = 9, col = 2, desc = "DoT that restores mana.", prereq = {7, 2} },
    { id = 47580, ranks = {47580, 47581, 47582}, name = "Pain and Suffering", icon = "Interface\\Icons\\Spell_Shadow_ShadowPower", maxRank = 3, tier = 9, col = 3, desc = "+20% MB/SW:D crit damage." },
    -- Tier 10
    { id = 47573, ranks = {47573, 47577, 47578, 51166, 51167}, name = "Twisted Faith", icon = "Interface\\Icons\\Spell_Shadow_PainAndSuffering", maxRank = 5, tier = 10, col = 3, desc = "Mind Flay refreshes SW:P." },
    -- Tier 11
    { id = 47585, ranks = {47585}, name = "Dispersion", icon = "Interface\\Icons\\Spell_Shadow_Dispersion", maxRank = 1, tier = 11, col = 2, desc = "-90% damage, restores mana.", prereq = {9, 2} },
}

Adv2.Data.Talents[5] = {
    [1] = Discipline,
    [2] = Holy,
    [3] = Shadow,
}
