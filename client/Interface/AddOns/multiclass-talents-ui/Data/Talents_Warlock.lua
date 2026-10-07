-- Warlock Talent Data (Class 9)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- AFFLICTION (Spec 1)
local Affliction = {
    -- Tier 1
    { id = 18827, ranks = {18827, 18829}, name = "Improved Curse of Agony", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 1, col = 1, desc = "" },
    { id = 18174, ranks = {18174, 18175, 18176}, name = "Suppression", icon = "Interface\\Icons\\Spell_Shadow_CurseOfSargeras", maxRank = 3, tier = 1, col = 2, desc = "+5% Curse of Agony damage." },
    { id = 17810, ranks = {17810, 17811, 17812, 17813, 17814}, name = "Improved Corruption", icon = "Interface\\Icons\\Spell_Shadow_UnsummonBuilding", maxRank = 5, tier = 1, col = 3, desc = "+1% Affliction hit." },
    -- Tier 2
    { id = 18179, ranks = {18179, 18180}, name = "Improved Curse of Weakness", icon = "Interface\\Icons\\Spell_Shadow_AbominationExplosion", maxRank = 2, tier = 2, col = 1, desc = "-0.4 sec Corruption cast, +10% crit." },
    { id = 18213, ranks = {18213, 18372}, name = "Improved Drain Soul", icon = "Interface\\Icons\\Spell_Shadow_CurseOfMannoroth", maxRank = 2, tier = 2, col = 2, desc = "+10% Curse of Weakness effect." },
    { id = 18182, ranks = {18182, 18183}, name = "Improved Life Tap", icon = "Interface\\Icons\\Spell_Shadow_Haunting", maxRank = 2, tier = 2, col = 3, desc = "+100% mana regen during Drain Soul." },
    { id = 17804, ranks = {17804, 17805}, name = "Soul Siphon", icon = "Interface\\Icons\\Spell_Shadow_BurningSpirit", maxRank = 2, tier = 2, col = 4, desc = "+10% mana from Life Tap." },
    -- Tier 3
    { id = 53754, ranks = {53754, 53759}, name = "Improved Fear", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 3, col = 1, desc = "" },
    { id = 17783, ranks = {17783, 17784, 17785}, name = "Fel Concentration", icon = "Interface\\Icons\\Spell_Shadow_SoulLeech_3", maxRank = 3, tier = 3, col = 2, desc = "+3% Drain Life/Soul per Affliction." },
    { id = 18288, ranks = {18288}, name = "Amplify Curse", icon = "Interface\\Icons\\Spell_Shadow_Possession", maxRank = 1, tier = 3, col = 3, desc = "Fear causes no damage wake-up." },
    -- Tier 4
    { id = 18218, ranks = {18218, 18219}, name = "Grim Reach", icon = "Interface\\Icons\\Spell_Shadow_StrengthOfSpirit", maxRank = 2, tier = 4, col = 1, desc = "+35% pushback resist on channel." },
    { id = 18094, ranks = {18094, 18095}, name = "Nightfall", icon = "Interface\\Icons\\Spell_Shadow_Contagion", maxRank = 2, tier = 4, col = 2, desc = "Increases next Curse effect." },
    { id = 32381, ranks = {32381, 32382, 32383}, name = "Empowered Corruption", icon = "Interface\\Icons\\Spell_Shadow_CallofBone", maxRank = 3, tier = 4, col = 4, desc = "+10% Affliction range." },
    -- Tier 5
    { id = 32385, ranks = {32385, 32387, 32392, 32393, 32394}, name = "Shadow Embrace", icon = "Interface\\Icons\\Spell_Shadow_AbominationExplosion", maxRank = 5, tier = 5, col = 1, desc = "+12% spell power to Corruption." },
    { id = 63108, ranks = {63108}, name = "Siphon Life", icon = "Interface\\Icons\\Spell_Shadow_ShadowMastery", maxRank = 1, tier = 5, col = 2, desc = "+3% Shadow damage." },
    { id = 18223, ranks = {18223}, name = "Curse of Exhaustion", icon = "Interface\\Icons\\Spell_Shadow_Twilight", maxRank = 1, tier = 5, col = 3, desc = "Corruption/Drain can proc instant SB.", prereq = {3, 3} },
    -- Tier 6
    { id = 54037, ranks = {54037, 54038}, name = "Improved Felhunter", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 6, col = 1, desc = "" },
    { id = 18271, ranks = {18271, 18272, 18273, 18274, 18275}, name = "Shadow Mastery", icon = "Interface\\Icons\\Spell_Shadow_Twilight", maxRank = 5, tier = 6, col = 2, desc = "Corruption/Drain can proc instant SB.", prereq = {5, 2} },
    -- Tier 7
    { id = 47195, ranks = {47195, 47196, 47197}, name = "Eradication", icon = "Interface\\Icons\\Ability_Warlock_Eradication", maxRank = 3, tier = 7, col = 1, desc = "Corruption can proc +6% haste." },
    { id = 30060, ranks = {30060, 30061, 30062, 30063, 30064}, name = "Contagion", icon = "Interface\\Icons\\Spell_Shadow_Contagion", maxRank = 5, tier = 7, col = 2, desc = "+1% Curse of Agony/Corruption/Seed damage." },
    { id = 18220, ranks = {18220}, name = "Dark Pact", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 7, col = 3, desc = "" },
    -- Tier 8
    { id = 30054, ranks = {30054, 30057}, name = "Improved Howl of Terror", icon = "Interface\\Icons\\Spell_Shadow_CurseOfAchimonde", maxRank = 2, tier = 8, col = 1, desc = "+1% spell damage from Curse of Elements." },
    { id = 32477, ranks = {32477, 32483, 32484}, name = "Malediction", icon = "Interface\\Icons\\Spell_Shadow_GrimWard", maxRank = 3, tier = 8, col = 3, desc = "Curse that slows movement." },
    -- Tier 9
    { id = 47198, ranks = {47198, 47199, 47200}, name = "Death's Embrace", icon = "Interface\\Icons\\Spell_Shadow_DeathScream", maxRank = 3, tier = 9, col = 1, desc = "Instant Howl of Terror." },
    { id = 30108, ranks = {30108}, name = "Unstable Affliction", icon = "Interface\\Icons\\Spell_Shadow_UnstableAffliction_3", maxRank = 1, tier = 9, col = 2, desc = "Shadow DoT that damages dispeller.", prereq = {7, 2} },
    { id = 58435, ranks = {58435}, name = "Pandemic", icon = "Interface\\Icons\\Spell_Shadow_Pandemic", maxRank = 1, tier = 9, col = 3, desc = "Corruption/UA can crit.", prereq = {9, 2} },
    -- Tier 10
    { id = 47201, ranks = {47201, 47202, 47203, 47204, 47205}, name = "Everlasting Affliction", icon = "Interface\\Icons\\Spell_Shadow_DeathsEmbrace", maxRank = 5, tier = 10, col = 2, desc = "+4% Shadow damage on targets below 35%." },
    -- Tier 11
    { id = 48181, ranks = {48181}, name = "Haunt", icon = "Interface\\Icons\\Ability_Warlock_Haunt", maxRank = 1, tier = 11, col = 2, desc = "DoT that heals you and +20% Shadow DoTs." },
}

-- DEMONOLOGY (Spec 2)
local Demonology = {
    -- Tier 1
    { id = 18692, ranks = {18692, 18693}, name = "Improved Healthstone", icon = "Interface\\Icons\\INV_Stone_04", maxRank = 2, tier = 1, col = 1, desc = "+10% Healthstone healing." },
    { id = 18694, ranks = {18694, 18695, 18696}, name = "Improved Imp", icon = "Interface\\Icons\\Spell_Shadow_SummonImp", maxRank = 3, tier = 1, col = 2, desc = "+10% Imp Firebolt damage." },
    { id = 18697, ranks = {18697, 18698, 18699}, name = "Demonic Embrace", icon = "Interface\\Icons\\Spell_Shadow_Metamorphosis", maxRank = 3, tier = 1, col = 3, desc = "+4% Stamina." },
    { id = 47230, ranks = {47230, 47231}, name = "Fel Synergy", icon = "Interface\\Icons\\Spell_Shadow_FelSynergy", maxRank = 2, tier = 1, col = 4, desc = "Your damage heals pet 7.5%." },
    -- Tier 2
    { id = 18703, ranks = {18703, 18704}, name = "Improved Health Funnel", icon = "Interface\\Icons\\Spell_Shadow_LifeDrain", maxRank = 2, tier = 2, col = 1, desc = "+10% Health Funnel." },
    { id = 18705, ranks = {18705, 18706, 18707}, name = "Demonic Brutality", icon = "Interface\\Icons\\Spell_Shadow_SummonVoidWalker", maxRank = 3, tier = 2, col = 2, desc = "+10% Voidwalker abilities." },
    { id = 18731, ranks = {18731, 18743, 18744}, name = "Fel Vitality", icon = "Interface\\Icons\\Spell_Shadow_AntiMagicShell", maxRank = 3, tier = 2, col = 3, desc = "+3% Stamina/Intellect." },
    -- Tier 3
    { id = 18754, ranks = {18754, 18755, 18756}, name = "Improved Succubus", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 3, col = 1, desc = "" },
    { id = 19028, ranks = {19028}, name = "Soul Link", icon = "Interface\\Icons\\Spell_Shadow_GatherShadows", maxRank = 1, tier = 3, col = 2, desc = "Share 20% damage with pet." },
    { id = 18708, ranks = {18708}, name = "Fel Domination", icon = "Interface\\Icons\\Spell_Shadow_SummonSuccubus", maxRank = 1, tier = 3, col = 3, desc = "+10% Succubus abilities." },
    { id = 30143, ranks = {30143, 30144, 30145}, name = "Demonic Aegis", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordDominate", maxRank = 3, tier = 3, col = 4, desc = "+4% pet melee damage." },
    -- Tier 4
    { id = 18769, ranks = {18769, 18770, 18771, 18772, 18773}, name = "Unholy Power", icon = "Interface\\Icons\\Spell_Shadow_RagingScream", maxRank = 5, tier = 4, col = 2, desc = "+10% Demon Armor/Fel Armor.", prereq = {3, 2} },
    { id = 18709, ranks = {18709, 18710}, name = "Master Summoner", icon = "Interface\\Icons\\Spell_Nature_RemoveCurse", maxRank = 2, tier = 4, col = 3, desc = "Instant free pet summon.", prereq = {3, 3} },
    -- Tier 5
    { id = 30326, ranks = {30326}, name = "Mana Feed", icon = "Interface\\Icons\\INV_Ammo_FireTar", maxRank = 1, tier = 5, col = 1, desc = "+15% Firestone damage.", prereq = {4, 2} },
    { id = 18767, ranks = {18767, 18768}, name = "Master Conjuror", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 5, col = 3, desc = "" },
    -- Tier 6
    { id = 23785, ranks = {23785, 23822, 23823, 23824, 23825}, name = "Master Demonologist", icon = "Interface\\Icons\\Spell_Shadow_ShadowPact", maxRank = 5, tier = 6, col = 2, desc = "Pet gives bonus based on type.", prereq = {4, 2} },
    { id = 47245, ranks = {47245, 47246, 47247}, name = "Molten Core", icon = "Interface\\Icons\\Spell_Fire_Fireball02", maxRank = 3, tier = 6, col = 3, desc = "Soul Fire instant when target below 35%." },
    -- Tier 7
    { id = 30319, ranks = {30319, 30320, 30321}, name = "Demonic Resilience", icon = "Interface\\Icons\\Spell_Shadow_DemonicResilience", maxRank = 3, tier = 7, col = 1, desc = "-5% pet damage taken." },
    { id = 47193, ranks = {47193}, name = "Demonic Empowerment", icon = "Interface\\Icons\\Spell_Shadow_DemonicTactics", maxRank = 1, tier = 7, col = 2, desc = "Your crit procs pet +10% damage.", prereq = {6, 2} },
    { id = 35691, ranks = {35691, 35692, 35693}, name = "Demonic Knowledge", icon = "Interface\\Icons\\Ability_Warlock_MoltenCore", maxRank = 3, tier = 7, col = 3, desc = "Corruption can proc Incinerate +6% damage." },
    -- Tier 8
    { id = 30242, ranks = {30242, 30245, 30246, 30247, 30248}, name = "Demonic Tactics", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 8, col = 2, desc = "" },
    { id = 63156, ranks = {63156, 63158}, name = "Decimation", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 3, desc = "" },
    -- Tier 9
    { id = 54347, ranks = {54347, 54348, 54349}, name = "Improved Demonic Tactics", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 1, desc = "", prereq = {8, 2} },
    { id = 30146, ranks = {30146}, name = "Summon Felguard", icon = "Interface\\Icons\\Spell_Shadow_ManaFeed", maxRank = 1, tier = 9, col = 2, desc = "Pet restores mana when you crit." },
    { id = 63117, ranks = {63117, 63121, 63123}, name = "Nemesis", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 3, desc = "" },
    -- Tier 10
    { id = 47236, ranks = {47236, 47237, 47238, 47239, 47240}, name = "Demonic Pact", icon = "Interface\\Icons\\Spell_Shadow_DemonicTactics", maxRank = 5, tier = 10, col = 2, desc = "+2% pet and you crit." },
    -- Tier 11
    { id = 59672, ranks = {59672}, name = "Metamorphosis", icon = "Interface\\Icons\\Spell_Shadow_SummonFelGuard", maxRank = 1, tier = 11, col = 2, desc = "Summon Felguard pet." },
}

-- DESTRUCTION (Spec 3)
local Destruction = {
    -- Tier 1
    { id = 17793, ranks = {17793, 17796, 17801, 17802, 17803}, name = "Improved Shadow Bolt", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 1, col = 2, desc = "" },
    { id = 17788, ranks = {17788, 17789, 17790, 17791, 17792}, name = "Bane", icon = "Interface\\Icons\\Spell_Shadow_ShadowBolt", maxRank = 5, tier = 1, col = 3, desc = "Shadow Bolt crits apply +5% crit debuff." },
    -- Tier 2
    { id = 18119, ranks = {18119, 18120}, name = "Aftermath", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 2, col = 1, desc = "" },
    { id = 63349, ranks = {63349, 63350, 63351}, name = "Molten Skin", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 2, col = 2, desc = "" },
    { id = 17778, ranks = {17778, 17779, 17780}, name = "Cataclysm", icon = "Interface\\Icons\\Spell_Shadow_DeathPact", maxRank = 3, tier = 2, col = 3, desc = "-0.1 sec Shadow Bolt/Immolate cast." },
    -- Tier 3
    { id = 18126, ranks = {18126, 18127}, name = "Demonic Power", icon = "Interface\\Icons\\Ability_Mage_MoltenShields", maxRank = 2, tier = 3, col = 1, desc = "-2% damage taken." },
    { id = 17877, ranks = {17877}, name = "Shadowburn", icon = "Interface\\Icons\\Spell_Fire_Fire", maxRank = 1, tier = 3, col = 2, desc = "Destruction spells can daze." },
    { id = 17959, ranks = {17959, 59738, 59739, 59740, 59741}, name = "Ruin", icon = "Interface\\Icons\\Spell_Shadow_ShadowWordPain", maxRank = 5, tier = 3, col = 3, desc = "+20% Destruction crit damage." },
    -- Tier 4
    { id = 18135, ranks = {18135, 18136}, name = "Intensity", icon = "Interface\\Icons\\Spell_Fire_SoulBurn", maxRank = 2, tier = 4, col = 1, desc = "+4% Searing Pain crit." },
    { id = 17917, ranks = {17917, 17918}, name = "Destructive Reach", icon = "Interface\\Icons\\Spell_Fire_SelfDestruct", maxRank = 2, tier = 4, col = 2, desc = "+3% Fire damage." },
    { id = 17927, ranks = {17927, 17929, 17930}, name = "Improved Searing Pain", icon = "Interface\\Icons\\Spell_Shadow_CorpseExplode", maxRank = 3, tier = 4, col = 4, desc = "+10% Destruction range." },
    -- Tier 5
    { id = 34935, ranks = {34935, 34938, 34939}, name = "Backlash", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 5, col = 1, desc = "", prereq = {4, 1} },
    { id = 17815, ranks = {17815, 17833, 17834}, name = "Improved Immolate", icon = "Interface\\Icons\\Spell_Fire_Windsofwoe", maxRank = 3, tier = 5, col = 2, desc = "-4% Destruction mana cost." },
    { id = 18130, ranks = {18130}, name = "Devastation", icon = "Interface\\Icons\\Spell_Fire_Lavaspawn", maxRank = 1, tier = 5, col = 3, desc = "+35% Rain of Fire/Hellfire pushback resist.", prereq = {3, 3} },
    -- Tier 6
    { id = 30299, ranks = {30299, 30301, 30302}, name = "Nether Protection", icon = "Interface\\Icons\\Spell_Fire_PlayingWithFire", maxRank = 3, tier = 6, col = 1, desc = "+1% crit, attacks can proc instant Incinerate." },
    { id = 17954, ranks = {17954, 17955, 17956, 17957, 17958}, name = "Emberstorm", icon = "Interface\\Icons\\Spell_Shadow_SummonFelhunter", maxRank = 5, tier = 6, col = 3, desc = "+10% Succubus Lash damage." },
    -- Tier 7
    { id = 17962, ranks = {17962}, name = "Conflagrate", icon = "Interface\\Icons\\Spell_Fire_Immolation", maxRank = 1, tier = 7, col = 2, desc = "+10% Immolate damage.", prereq = {5, 2} },
    { id = 30293, ranks = {30293, 30295, 30296}, name = "Soul Leech", icon = "Interface\\Icons\\Spell_Fire_Volcano", maxRank = 3, tier = 7, col = 3, desc = "+3% Rain of Fire/Hellfire/Soul Fire damage." },
    { id = 18096, ranks = {18096, 18073, 63245}, name = "Pyroclasm", icon = "Interface\\Icons\\Spell_Shadow_NetherProtection", maxRank = 3, tier = 7, col = 4, desc = "Shadow/Fire damage can proc immunity." },
    -- Tier 8
    { id = 30288, ranks = {30288, 30289, 30290, 30291, 30292}, name = "Shadow and Flame", icon = "Interface\\Icons\\Spell_Shadow_SoulLeech_2", maxRank = 5, tier = 8, col = 2, desc = "Destruction crits restore 10% health/mana." },
    { id = 54117, ranks = {54117, 54118}, name = "Improved Soul Leech", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 3, desc = "", prereq = {7, 3} },
    -- Tier 9
    { id = 47258, ranks = {47258, 47259, 47260}, name = "Backdraft", icon = "Interface\\Icons\\Spell_Shadow_SoulLeech_3", maxRank = 3, tier = 9, col = 1, desc = "Soul Leech also restores pet, grants Replenishment.", prereq = {7, 2} },
    { id = 30283, ranks = {30283}, name = "Shadowfury", icon = "Interface\\Icons\\Spell_Shadow_Shadowfury", maxRank = 1, tier = 9, col = 2, desc = "AoE Shadow damage + stun." },
    { id = 47220, ranks = {47220, 47221, 47223}, name = "Empowered Imp", icon = "Interface\\Icons\\Ability_Warlock_Backdraft", maxRank = 3, tier = 9, col = 3, desc = "Conflagrate procs -30% cast time." },
    -- Tier 10
    { id = 47266, ranks = {47266, 47267, 47268, 47269, 47270}, name = "Fire and Brimstone", icon = "Interface\\Icons\\Spell_Shadow_SummonImp", maxRank = 5, tier = 10, col = 2, desc = "Imp crits give you 100% crit." },
    -- Tier 11
    { id = 50796, ranks = {50796}, name = "Chaos Bolt", icon = "Interface\\Icons\\Ability_Warlock_ChaosBolt", maxRank = 1, tier = 11, col = 2, desc = "Unresistable Fire damage." },
}

Adv2.Data.Talents[9] = {
    [1] = Affliction,
    [2] = Demonology,
    [3] = Destruction,
}
