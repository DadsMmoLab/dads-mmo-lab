-- Hunter Talent Data (Class 3)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- BEAST MASTERY (Spec 1)
local BeastMastery = {
    -- Tier 1
    { id = 19552, ranks = {19552, 19553, 19554, 19555, 19556}, name = "Improved Aspect of the Hawk", icon = "Interface\\Icons\\Spell_Nature_RavenForm", maxRank = 5, tier = 1, col = 2, desc = "Hawk procs 10% haste." },
    { id = 19583, ranks = {19583, 19584, 19585, 19586, 19587}, name = "Endurance Training", icon = "Interface\\Icons\\Spell_Nature_Reincarnation", maxRank = 5, tier = 1, col = 3, desc = "+2% pet and +1% hunter health." },
    -- Tier 2
    { id = 35029, ranks = {35029, 35030}, name = "Focused Fire", icon = "Interface\\Icons\\Ability_Hunter_AspectOfTheMonkey", maxRank = 2, tier = 2, col = 1, desc = "+2% dodge in Monkey." },
    { id = 19549, ranks = {19549, 19550, 19551}, name = "Improved Aspect of the Monkey", icon = "Interface\\Icons\\Ability_Hunter_FocusedFire", maxRank = 3, tier = 2, col = 2, desc = "+2% damage when pet is active." },
    { id = 19609, ranks = {19609, 19610, 19612}, name = "Thick Hide", icon = "Interface\\Icons\\INV_Misc_Pelt_Bear_03", maxRank = 3, tier = 2, col = 3, desc = "+7% pet armor." },
    { id = 24443, ranks = {24443, 19575}, name = "Improved Revive Pet", icon = "Interface\\Icons\\Ability_Hunter_Pet_Hyena", maxRank = 2, tier = 2, col = 4, desc = "+2% pet crit." },
    -- Tier 3
    { id = 19559, ranks = {19559, 19560}, name = "Pathfinding", icon = "Interface\\Icons\\Ability_Hunter_BeastSoothe", maxRank = 2, tier = 3, col = 1, desc = "-3 sec Revive cast, +15% health." },
    { id = 53265, ranks = {53265}, name = "Aspect Mastery", icon = "Interface\\Icons\\Ability_Hunter_CobraStrikes", maxRank = 1, tier = 3, col = 2, desc = "Your crits give pet 2 guaranteed crits." },
    { id = 19616, ranks = {19616, 19617, 19618, 19619, 19620}, name = "Unleashed Fury", icon = "Interface\\Icons\\Ability_BullRush", maxRank = 5, tier = 3, col = 3, desc = "+4% pet damage." },
    -- Tier 4
    { id = 19572, ranks = {19572, 19573}, name = "Improved Mend Pet", icon = "Interface\\Icons\\Ability_Hunter_MendPet", maxRank = 2, tier = 4, col = 2, desc = "Mend removes debuffs." },
    { id = 19598, ranks = {19598, 19599, 19600, 19601, 19602}, name = "Ferocity", icon = "Interface\\Icons\\Ability_Mount_JungleTiger", maxRank = 5, tier = 4, col = 3, desc = "+4% Aspect of Cheetah/Pack speed." },
    -- Tier 5
    { id = 19578, ranks = {19578, 20895}, name = "Spirit Bond", icon = "Interface\\Icons\\Ability_Hunter_Pet_Hyena", maxRank = 2, tier = 5, col = 1, desc = "+2% pet crit." },
    { id = 19577, ranks = {19577}, name = "Intimidation", icon = "Interface\\Icons\\Ability_Druid_FerociousBite", maxRank = 1, tier = 5, col = 2, desc = "Pet enrages, +50% damage, immune to CC." },
    { id = 19590, ranks = {19590, 19592}, name = "Bestial Discipline", icon = "Interface\\Icons\\Ability_Druid_DemoralizingRoar", maxRank = 2, tier = 5, col = 4, desc = "2% health regen for you and pet." },
    -- Tier 6
    { id = 34453, ranks = {34453, 34454}, name = "Animal Handler", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 6, col = 1, desc = "" },
    { id = 19621, ranks = {19621, 19622, 19623, 19624, 19625}, name = "Frenzy", icon = "Interface\\Icons\\Ability_Devour", maxRank = 5, tier = 6, col = 3, desc = "Pet stuns target for 3 sec.", prereq = {4, 3} },
    -- Tier 7
    { id = 34455, ranks = {34455, 34459, 34460}, name = "Ferocious Inspiration", icon = "Interface\\Icons\\Ability_Mount_WhiteTiger", maxRank = 3, tier = 7, col = 1, desc = "+4% pet AP, +4% pet hit." },
    { id = 19574, ranks = {19574}, name = "Bestial Wrath", icon = "Interface\\Icons\\Ability_Hunter_Pet_Hyena", maxRank = 1, tier = 7, col = 2, desc = "+2% pet crit.", prereq = {5, 2} },
    { id = 34462, ranks = {34462, 34464, 34465}, name = "Catlike Reflexes", icon = "Interface\\Icons\\Ability_Hunter_Invigoration", maxRank = 3, tier = 7, col = 3, desc = "Pet crits restore 1% mana." },
    -- Tier 8
    { id = 53252, ranks = {53252, 53253}, name = "Invigoration", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 1, desc = "", prereq = {7, 1} },
    { id = 34466, ranks = {34466, 34467, 34468, 34469, 34470}, name = "Serpent's Swiftness", icon = "Interface\\Icons\\Ability_Hunter_SerpentsSwiftness", maxRank = 5, tier = 8, col = 3, desc = "+4% ranged and pet attack speed." },
    -- Tier 9
    { id = 53262, ranks = {53262, 53263, 53264}, name = "Longevity", icon = "Interface\\Icons\\Ability_Hunter_Longevity", maxRank = 3, tier = 9, col = 1, desc = "-10% pet ability cooldowns." },
    { id = 34692, ranks = {34692}, name = "The Beast Within", icon = "Interface\\Icons\\Ability_GhoulFrenzy", maxRank = 1, tier = 9, col = 2, desc = "Pet crits proc 30% attack speed.", prereq = {7, 2} },
    { id = 53256, ranks = {53256, 53259, 53260}, name = "Cobra Strikes", icon = "Interface\\Icons\\Ability_Hunter_KindredSpirits", maxRank = 3, tier = 9, col = 3, desc = "+4% pet damage, +20% pet movement.", prereq = {8, 3} },
    -- Tier 10
    { id = 56314, ranks = {56314, 56315, 56316, 56317, 56318}, name = "Kindred Spirits", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 10, col = 2, desc = "" },
    -- Tier 11
    { id = 53270, ranks = {53270}, name = "Beast Mastery", icon = "Interface\\Icons\\Ability_Hunter_BeastMastery", maxRank = 1, tier = 11, col = 2, desc = "Tame exotic beasts, +4 pet skill points." },
}

-- MARKSMANSHIP (Spec 2)
local Marksmanship = {
    -- Tier 1
    { id = 19407, ranks = {19407, 19412}, name = "Improved Concussive Shot", icon = "Interface\\Icons\\Spell_Frost_Stun", maxRank = 2, tier = 1, col = 1, desc = "+4 sec daze." },
    { id = 53620, ranks = {53620, 53621, 53622}, name = "Focused Aim", icon = "Interface\\Icons\\Ability_Hunter_CarefulAim", maxRank = 3, tier = 1, col = 2, desc = "+33% Intellect as ranged AP." },
    { id = 19426, ranks = {19426, 19427, 19429, 19430, 19431}, name = "Lethal Shots", icon = "Interface\\Icons\\Ability_SearingArrow", maxRank = 5, tier = 1, col = 3, desc = "+1% ranged crit." },
    -- Tier 2
    { id = 34482, ranks = {34482, 34483, 34484}, name = "Careful Aim", icon = "Interface\\Icons\\Ability_Hunter_FocusedAim", maxRank = 3, tier = 2, col = 1, desc = "+1% hit, reduces Steady Shot pushback." },
    { id = 19421, ranks = {19421, 19422, 19423}, name = "Improved Hunter's Mark", icon = "Interface\\Icons\\Ability_PierceDamage", maxRank = 3, tier = 2, col = 2, desc = "+6% ranged crit damage." },
    { id = 19485, ranks = {19485, 19487, 19488, 19489, 19490}, name = "Mortal Shots", icon = "Interface\\Icons\\Ability_ImpalingBolt", maxRank = 5, tier = 2, col = 3, desc = "+5% Arcane Shot damage." },
    -- Tier 3
    { id = 34950, ranks = {34950, 34954}, name = "Go for the Throat", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 3, col = 1, desc = "" },
    { id = 19454, ranks = {19454, 19455, 19456}, name = "Improved Arcane Shot", icon = "Interface\\Icons\\Ability_Hunter_RapidKilling", maxRank = 3, tier = 3, col = 2, desc = "Reduces Rapid Fire cooldown, 20% damage after kill." },
    { id = 19434, ranks = {19434}, name = "Aimed Shot", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 3, col = 3, desc = "", prereq = {2, 3} },
    { id = 34948, ranks = {34948, 34949}, name = "Rapid Killing", icon = "Interface\\Icons\\Ability_Hunter_GoForTheThroat", maxRank = 2, tier = 3, col = 4, desc = "Your crits give pet 25 focus." },
    -- Tier 4
    { id = 19464, ranks = {19464, 19465, 19466}, name = "Improved Stings", icon = "Interface\\Icons\\INV_Spear_07", maxRank = 3, tier = 4, col = 2, desc = "Powerful shot, reduces healing by 50%." },
    { id = 19416, ranks = {19416, 19417, 19418, 19419, 19420}, name = "Efficiency", icon = "Interface\\Icons\\Ability_Hunter_SniperShot", maxRank = 5, tier = 4, col = 3, desc = "+30% Hunter's Mark AP bonus." },
    -- Tier 5
    { id = 35100, ranks = {35100, 35102}, name = "Concussive Barrage", icon = "Interface\\Icons\\Ability_UpgradeMoonGlaive", maxRank = 2, tier = 5, col = 1, desc = "+12% Aimed/Multi crit." },
    { id = 23989, ranks = {23989}, name = "Readiness", icon = "Interface\\Icons\\Ability_Hunter_Readiness", maxRank = 1, tier = 5, col = 2, desc = "Resets all Hunter ability cooldowns." },
    { id = 19461, ranks = {19461, 19462, 24691}, name = "Barrage", icon = "Interface\\Icons\\Ability_Hunter_Quickshot", maxRank = 3, tier = 5, col = 3, desc = "+10% Sting damage." },
    -- Tier 6
    { id = 34475, ranks = {34475, 34476}, name = "Combat Experience", icon = "Interface\\Icons\\INV_Weapon_Rifle_06", maxRank = 2, tier = 6, col = 1, desc = "+2% ranged damage." },
    { id = 19507, ranks = {19507, 19508, 19509}, name = "Ranged Weapon Specialization", icon = "Interface\\Icons\\Ability_Hunter_CombatExperience", maxRank = 3, tier = 6, col = 4, desc = "+2% Agility and Intellect." },
    -- Tier 7
    { id = 53234, ranks = {53234, 53237, 53238}, name = "Piercing Shots", icon = "Interface\\Icons\\Ability_Hunter_PiercingShots", maxRank = 3, tier = 7, col = 1, desc = "Crits bleed for 10% damage." },
    { id = 19506, ranks = {19506}, name = "Trueshot Aura", icon = "Interface\\Icons\\Ability_TrueShot", maxRank = 1, tier = 7, col = 2, desc = "+10% AP aura.", prereq = {5, 2} },
    { id = 35104, ranks = {35104, 35110, 35111}, name = "Improved Barrage", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 3, desc = "", prereq = {5, 3} },
    -- Tier 8
    { id = 34485, ranks = {34485, 34486, 34487, 34488, 34489}, name = "Master Marksman", icon = "Interface\\Icons\\Ability_Hunter_RapidRecuperation", maxRank = 5, tier = 8, col = 2, desc = "Rapid Fire restores mana." },
    { id = 53228, ranks = {53228, 53232}, name = "Rapid Recuperation", icon = "Interface\\Icons\\Ability_Hunter_MasterMarksman", maxRank = 2, tier = 8, col = 3, desc = "+2% crit, reduces Steady Shot mana." },
    -- Tier 9
    { id = 53215, ranks = {53215, 53216, 53217}, name = "Wild Quiver", icon = "Interface\\Icons\\Ability_Hunter_ImprovedSteadyShot", maxRank = 3, tier = 9, col = 1, desc = "Steady Shot can proc +15% damage." },
    { id = 34490, ranks = {34490}, name = "Silencing Shot", icon = "Interface\\Icons\\Ability_TheBlackArrow", maxRank = 1, tier = 9, col = 2, desc = "Silences target for 3 sec.", prereq = {8, 2} },
    { id = 53221, ranks = {53221, 53222, 53224}, name = "Improved Steady Shot", icon = "Interface\\Icons\\Ability_Hunter_MarkedForDeath", maxRank = 3, tier = 9, col = 3, desc = "+2% damage vs marked targets." },
    -- Tier 10
    { id = 53241, ranks = {53241, 53243, 53244, 53245, 53246}, name = "Marked for Death", icon = "Interface\\Icons\\Ability_Hunter_WildQuiver", maxRank = 5, tier = 10, col = 2, desc = "Chance for auto-shots to fire additional arrow." },
    -- Tier 11
    { id = 53209, ranks = {53209}, name = "Chimera Shot", icon = "Interface\\Icons\\Ability_Hunter_ChimeraShot2", maxRank = 1, tier = 11, col = 2, desc = "Refreshes Sting and deals bonus damage." },
}

-- SURVIVAL (Spec 3)
local Survival = {
    -- Tier 1
    { id = 52783, ranks = {52783, 52785, 52786, 52787, 52788}, name = "Improved Tracking", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 1, col = 1, desc = "" },
    { id = 19498, ranks = {19498, 19499, 19500}, name = "Hawk Eye", icon = "Interface\\Icons\\Ability_Hunter_SniperShot", maxRank = 3, tier = 1, col = 2, desc = "+2 yard range." },
    { id = 19159, ranks = {19159, 19160}, name = "Savage Strikes", icon = "Interface\\Icons\\Ability_Racial_BloodRage", maxRank = 2, tier = 1, col = 3, desc = "+10% Raptor Strike/Mongoose Bite crit." },
    -- Tier 2
    { id = 19290, ranks = {19290, 19294, 24283}, name = "Surefooted", icon = "Interface\\Icons\\Ability_Kick", maxRank = 3, tier = 2, col = 1, desc = "+4% hit, -10% movement impair." },
    { id = 19184, ranks = {19184, 19387, 19388}, name = "Entrapment", icon = "Interface\\Icons\\Spell_Nature_StrangleVines", maxRank = 3, tier = 2, col = 2, desc = "Traps can root targets." },
    { id = 19376, ranks = {19376, 63457, 63458}, name = "Trap Mastery", icon = "Interface\\Icons\\Ability_Ensnare", maxRank = 3, tier = 2, col = 3, desc = "+10% trap damage and duration." },
    { id = 34494, ranks = {34494, 34496}, name = "Survival Instincts", icon = "Interface\\Icons\\Ability_Hunter_SurvivalTactics", maxRank = 2, tier = 2, col = 4, desc = "-2% Disengage cooldown, +4% trap resist." },
    -- Tier 3
    { id = 19255, ranks = {19255, 19256, 19257, 19258, 19259}, name = "Survivalist", icon = "Interface\\Icons\\Ability_Hunter_SurvivalInstincts", maxRank = 5, tier = 3, col = 1, desc = "+2% crit and -2% damage taken." },
    { id = 19503, ranks = {19503}, name = "Scatter Shot", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 3, col = 2, desc = "" },
    { id = 19295, ranks = {19295, 19297, 19298}, name = "Deflection", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 3, col = 3, desc = "" },
    { id = 19286, ranks = {19286, 19287}, name = "Survival Tactics", icon = "Interface\\Icons\\Ability_Warrior_Challange", maxRank = 2, tier = 3, col = 4, desc = "Counter parried attack." },
    -- Tier 4
    { id = 56333, ranks = {56333, 56336, 56337}, name = "T.N.T.", icon = "Interface\\Icons\\Ability_Hunter_ExplosiveShot", maxRank = 3, tier = 4, col = 2, desc = "Fire shot, deals fire damage over 2 sec." },
    { id = 56342, ranks = {56342, 56343, 56344}, name = "Lock and Load", icon = "Interface\\Icons\\Ability_Hunter_HuntingParty", maxRank = 3, tier = 4, col = 4, desc = "Agility +1%, crits regen mana for party." },
    -- Tier 5
    { id = 56339, ranks = {56339, 56340, 56341}, name = "Hunter vs. Wild", icon = "Interface\\Icons\\Ability_Hunter_SniperTraining", maxRank = 3, tier = 5, col = 1, desc = "+2% crit from 30+ yards.", prereq = {3, 1} },
    { id = 19370, ranks = {19370, 19371, 19373}, name = "Killer Instinct", icon = "Interface\\Icons\\Ability_Marksmanship", maxRank = 3, tier = 5, col = 2, desc = "+1% crit." },
    { id = 19306, ranks = {19306}, name = "Counterattack", icon = "Interface\\Icons\\INV_Spear_02", maxRank = 1, tier = 5, col = 3, desc = "Puts target to sleep.", prereq = {3, 3} },
    -- Tier 6
    { id = 19168, ranks = {19168, 19180, 19181, 24296, 24297}, name = "Lightning Reflexes", icon = "Interface\\Icons\\Spell_Nature_Invisibilty", maxRank = 5, tier = 6, col = 1, desc = "+3% Agility." },
    { id = 34491, ranks = {34491, 34492, 34493}, name = "Resourcefulness", icon = "Interface\\Icons\\Ability_Hunter_Resourcefulness", maxRank = 3, tier = 6, col = 3, desc = "-2 sec trap cooldowns, -10% melee cost." },
    -- Tier 7
    { id = 34500, ranks = {34500, 34502, 34503}, name = "Expose Weakness", icon = "Interface\\Icons\\Ability_Hunter_LockAndLoad", maxRank = 3, tier = 7, col = 1, desc = "Traps can proc instant Explosive/Arcane Shot.", prereq = {6, 1} },
    { id = 19386, ranks = {19386}, name = "Wyvern Sting", icon = "Interface\\Icons\\Ability_Hunter_SerpentSting", maxRank = 1, tier = 7, col = 2, desc = "+3% Wyvern Sting damage, dispel penalty.", prereq = {5, 2} },
    { id = 34497, ranks = {34497, 34498, 34499}, name = "Thrill of the Hunt", icon = "Interface\\Icons\\Ability_Hunter_SurvivalTactics", maxRank = 3, tier = 7, col = 3, desc = "-2% Disengage cooldown, +4% trap resist." },
    -- Tier 8
    { id = 34506, ranks = {34506, 34507, 34508, 34838, 34839}, name = "Master Tactician", icon = "Interface\\Icons\\INV_Misc_Bomb_04", maxRank = 5, tier = 8, col = 1, desc = "+3% Explosive Shot/Trap crit, stun chance." },
    { id = 53295, ranks = {53295, 53296, 53297}, name = "Noxious Stings", icon = "Interface\\Icons\\Ability_Hunter_MasterTactitian", maxRank = 3, tier = 8, col = 2, desc = "Ranged attacks can proc +10% crit.", prereq = {7, 2} },
    -- Tier 9
    { id = 53298, ranks = {53298, 53299}, name = "Point of No Escape", icon = "Interface\\Icons\\Ability_Hunter_MasterTactitian", maxRank = 2, tier = 9, col = 1, desc = "Ranged attacks can proc +10% crit." },
    { id = 3674, ranks = {3674}, name = "Black Arrow", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 9, col = 2, desc = "" },
    { id = 53302, ranks = {53302, 53303, 53304}, name = "Sniper Training", icon = "Interface\\Icons\\Ability_Hunter_HuntingParty", maxRank = 3, tier = 9, col = 4, desc = "+1% Agility, ranged crits give mana." },
    -- Tier 10
    { id = 53290, ranks = {53290, 53291, 53292}, name = "Hunting Party", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 10, col = 3, desc = "", prereq = {7, 3} },
    -- Tier 11
    { id = 53301, ranks = {53301}, name = "Explosive Shot", icon = "Interface\\Icons\\Ability_Hunter_ExplosiveShot", maxRank = 1, tier = 11, col = 2, desc = "Fire damage in area over time.", prereq = {9, 2} },
}

Adv2.Data.Talents[3] = {
    [1] = BeastMastery,
    [2] = Marksmanship,
    [3] = Survival,
}
