-- Mage Talent Data (Class 8)
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}
Adv2.Data.Talents = Adv2.Data.Talents or {}

-- ARCANE (Spec 1)
local Arcane = {
    -- Tier 1
    { id = 11210, ranks = {11210, 12592}, name = "Arcane Subtlety", icon = "Interface\\Icons\\Spell_Holy_DispelMagic", maxRank = 2, tier = 1, col = 1, desc = "-20% Arcane threat, resist dispel." },
    { id = 11222, ranks = {11222, 12839, 12840}, name = "Arcane Focus", icon = "Interface\\Icons\\Spell_Holy_Devotion", maxRank = 3, tier = 1, col = 2, desc = "+1% Arcane hit." },
    { id = 11237, ranks = {11237, 12463, 12464, 16769, 16770}, name = "Arcane Stability", icon = "Interface\\Icons\\Spell_Nature_Invisibilty", maxRank = 5, tier = 1, col = 3, desc = "-2% Arcane pushback." },
    -- Tier 2
    { id = 28574, ranks = {28574, 54658, 54659}, name = "Arcane Fortitude", icon = "Interface\\Icons\\Spell_Arcane_ArcaneTorrent", maxRank = 3, tier = 2, col = 1, desc = "+50% Intellect as armor." },
    { id = 29441, ranks = {29441, 29444}, name = "Magic Absorption", icon = "Interface\\Icons\\Spell_Nature_AstralRecalGroup", maxRank = 2, tier = 2, col = 2, desc = "+1% resist, -1% damage taken." },
    { id = 11213, ranks = {11213, 12574, 12575, 12576, 12577}, name = "Arcane Concentration", icon = "Interface\\Icons\\Spell_Shadow_ManaBurn", maxRank = 5, tier = 2, col = 3, desc = "10% Clearcasting proc." },
    -- Tier 3
    { id = 11247, ranks = {11247, 12606}, name = "Magic Attunement", icon = "Interface\\Icons\\Spell_Nature_AbolishMagic", maxRank = 2, tier = 3, col = 1, desc = "+50% Amplify/Dampen Magic." },
    { id = 11242, ranks = {11242, 12467, 12469}, name = "Spell Impact", icon = "Interface\\Icons\\Spell_Arcane_StudentOfMagic", maxRank = 3, tier = 3, col = 2, desc = "+3% crit buff." },
    { id = 44397, ranks = {44397, 44398, 44399}, name = "Student of the Mind", icon = "Interface\\Icons\\Spell_Arcane_MassDispel", maxRank = 3, tier = 3, col = 3, desc = "Absorbed damage becomes spell power." },
    { id = 54646, ranks = {54646}, name = "Focus Magic", icon = "Interface\\Icons\\Spell_Holy_MindSooth", maxRank = 1, tier = 3, col = 4, desc = "+4% Spirit." },
    -- Tier 4
    { id = 11252, ranks = {11252, 12605}, name = "Arcane Shielding", icon = "Interface\\Icons\\Spell_Shadow_DetectLesserInvisibility", maxRank = 2, tier = 4, col = 1, desc = "-17% Mana Shield mana drain." },
    { id = 11255, ranks = {11255, 12598}, name = "Improved Counterspell", icon = "Interface\\Icons\\Spell_Frost_IceShock", maxRank = 2, tier = 4, col = 2, desc = "Counterspell silences." },
    { id = 18462, ranks = {18462, 18463, 18464}, name = "Arcane Meditation", icon = "Interface\\Icons\\Spell_Shadow_SiphonMana", maxRank = 3, tier = 4, col = 3, desc = "+17% mana regen while casting." },
    { id = 29447, ranks = {29447, 55339, 55340}, name = "Torment the Weak", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 4, col = 4, desc = "" },
    -- Tier 5
    { id = 31569, ranks = {31569, 31570}, name = "Improved Blink", icon = "Interface\\Icons\\Spell_Shadow_Teleport", maxRank = 2, tier = 5, col = 1, desc = "+4% damage vs slowed." },
    { id = 12043, ranks = {12043}, name = "Presence of Mind", icon = "Interface\\Icons\\Spell_Nature_Lightning", maxRank = 1, tier = 5, col = 2, desc = "+20% damage, +20% mana cost." },
    { id = 11232, ranks = {11232, 12500, 12501, 12502, 12503}, name = "Arcane Mind", icon = "Interface\\Icons\\Spell_Arcane_Blink", maxRank = 5, tier = 5, col = 4, desc = "-1.5 sec Blink snare removal." },
    -- Tier 6
    { id = 31574, ranks = {31574, 31575, 54354}, name = "Prismatic Cloak", icon = "Interface\\Icons\\Spell_Shadow_Teleport", maxRank = 3, tier = 6, col = 1, desc = "+1% spell damage and crit." },
    { id = 15058, ranks = {15058, 15059, 15060}, name = "Arcane Instability", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 2, desc = "", prereq = {5, 2} },
    { id = 31571, ranks = {31571, 31572}, name = "Arcane Potency", icon = "Interface\\Icons\\Spell_Arcane_ArcanePotency", maxRank = 2, tier = 6, col = 3, desc = "+15% crit after Presence/Clearcasting.", prereq = {5, 2} },
    -- Tier 7
    { id = 31579, ranks = {31579, 31582, 31583}, name = "Arcane Empowerment", icon = "Interface\\Icons\\Spell_Arcane_StarFire", maxRank = 3, tier = 7, col = 1, desc = "+1% raid damage." },
    { id = 12042, ranks = {12042}, name = "Arcane Power", icon = "Interface\\Icons\\Spell_Nature_EnchantArmor", maxRank = 1, tier = 7, col = 2, desc = "Next spell instant.", prereq = {6, 2} },
    { id = 44394, ranks = {44394, 44395, 44396}, name = "Incanter's Absorption", icon = "Interface\\Icons\\Spell_Arcane_PrismaticCloak", maxRank = 3, tier = 7, col = 3, desc = "-2% damage taken." },
    -- Tier 8
    { id = 44378, ranks = {44378, 44379}, name = "Arcane Flows", icon = "Interface\\Icons\\Spell_Nature_WispSplode", maxRank = 2, tier = 8, col = 2, desc = "+2% Arcane Blast/Barrage/Explosion damage.", prereq = {7, 2} },
    { id = 31584, ranks = {31584, 31585, 31586, 31587, 31588}, name = "Mind Mastery", icon = "Interface\\Icons\\Spell_Arcane_SpellPower", maxRank = 5, tier = 8, col = 3, desc = "+25% crit damage bonus." },
    -- Tier 9
    { id = 31589, ranks = {31589}, name = "Slow", icon = "Interface\\Icons\\Spell_Arcane_SpellPower", maxRank = 1, tier = 9, col = 2, desc = "+25% crit damage bonus." },
    { id = 44404, ranks = {44404, 54486, 54488, 54489, 54490}, name = "Missile Barrage", icon = "Interface\\Icons\\Spell_Arcane_ArcaneFlows", maxRank = 5, tier = 9, col = 3, desc = "-15% Evocation/PoM cooldown." },
    -- Tier 10
    { id = 44400, ranks = {44400, 44402, 44403}, name = "Netherwind Presence", icon = "Interface\\Icons\\Spell_Arcane_NetherwindPresence", maxRank = 3, tier = 10, col = 2, desc = "+2% spell haste." },
    { id = 35578, ranks = {35578, 35581}, name = "Spell Power", icon = "Interface\\Icons\\Spell_Nature_MindBomb", maxRank = 2, tier = 10, col = 3, desc = "+3% Intellect as spell power." },
    -- Tier 11
    { id = 44425, ranks = {44425}, name = "Arcane Barrage", icon = "Interface\\Icons\\Ability_Mage_ArcaneBarrage", maxRank = 1, tier = 11, col = 2, desc = "Instant Arcane damage." },
}

-- FIRE (Spec 2)
local Fire = {
    -- Tier 1
    { id = 11078, ranks = {11078, 11080}, name = "Improved Fire Blast", icon = "Interface\\Icons\\Spell_Fire_BurningDetermination", maxRank = 2, tier = 1, col = 1, desc = "Interrupt can proc immunity." },
    { id = 18459, ranks = {18459, 18460, 54734}, name = "Incineration", icon = "Interface\\Icons\\Spell_Fire_Fireball02", maxRank = 3, tier = 1, col = 2, desc = "+2% Flamestrike/Pyroblast/Blastwave/Dragon crit." },
    { id = 11069, ranks = {11069, 12338, 12339, 12340, 12341}, name = "Improved Fireball", icon = "Interface\\Icons\\Spell_Fire_Fireball", maxRank = 5, tier = 1, col = 3, desc = "-1 sec Fire Blast cooldown." },
    -- Tier 2
    { id = 11119, ranks = {11119, 11120, 12846, 12847, 12848}, name = "Ignite", icon = "Interface\\Icons\\Spell_Fire_FlameBolt", maxRank = 5, tier = 2, col = 1, desc = "-0.1 sec Fireball cast." },
    { id = 54747, ranks = {54747, 54749}, name = "Burning Determination", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 2, col = 2, desc = "" },
    { id = 11108, ranks = {11108, 12349, 12350}, name = "World in Flames", icon = "Interface\\Icons\\Spell_Fire_MeteorStorm", maxRank = 3, tier = 2, col = 3, desc = "Fire damage can stun." },
    -- Tier 3
    { id = 11100, ranks = {11100, 12353}, name = "Flame Throwing", icon = "Interface\\Icons\\Spell_Fire_Incinerate", maxRank = 2, tier = 3, col = 1, desc = "Crits leave DoT for 40% damage." },
    { id = 11103, ranks = {11103, 12357, 12358}, name = "Impact", icon = "Interface\\Icons\\Spell_Fire_Flameshock", maxRank = 3, tier = 3, col = 2, desc = "+2% Fire Blast/Scorch/Cone crit." },
    { id = 11366, ranks = {11366}, name = "Pyroblast", icon = "Interface\\Icons\\Spell_Fire_Fireball02", maxRank = 1, tier = 3, col = 3, desc = "Massive Fire damage." },
    { id = 11083, ranks = {11083, 12351}, name = "Burning Soul", icon = "Interface\\Icons\\Spell_Fire_Fire", maxRank = 2, tier = 3, col = 4, desc = "-35% Fire pushback, -5% threat." },
    -- Tier 4
    { id = 11095, ranks = {11095, 12872, 12873}, name = "Improved Scorch", icon = "Interface\\Icons\\Spell_Holy_Excorcism_02", maxRank = 3, tier = 4, col = 1, desc = "AoE Fire damage + knockback." },
    { id = 11094, ranks = {11094, 13043}, name = "Molten Shields", icon = "Interface\\Icons\\Spell_Fire_SoulBurn", maxRank = 2, tier = 4, col = 2, desc = "Scorch has chance to increase Fire crit." },
    { id = 29074, ranks = {29074, 29075, 29076}, name = "Master of Elements", icon = "Interface\\Icons\\Spell_Fire_MoltenShields", maxRank = 3, tier = 4, col = 4, desc = "+15% Fire Ward/Fire Shield damage reflect." },
    -- Tier 5
    { id = 31638, ranks = {31638, 31639, 31640}, name = "Playing with Fire", icon = "Interface\\Icons\\Spell_Fire_MasterOfElements", maxRank = 3, tier = 5, col = 1, desc = "Crits refund 10% mana." },
    { id = 11115, ranks = {11115, 11367, 11368}, name = "Critical Mass", icon = "Interface\\Icons\\Spell_Fire_Flare", maxRank = 3, tier = 5, col = 2, desc = "+3 yard Fire spell range." },
    { id = 11113, ranks = {11113}, name = "Blast Wave", icon = "Interface\\Icons\\Spell_Fire_PlayingWithFire", maxRank = 1, tier = 5, col = 3, desc = "+1% spell damage, +1% Fire damage taken.", prereq = {3, 3} },
    -- Tier 6
    { id = 31641, ranks = {31641, 31642}, name = "Blazing Speed", icon = "Interface\\Icons\\Spell_Fire_Immolation", maxRank = 2, tier = 6, col = 1, desc = "+2% Fire damage." },
    { id = 11124, ranks = {11124, 12378, 12398, 12399, 12400}, name = "Fire Power", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 5, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 34293, ranks = {34293, 34295, 34296}, name = "Pyromaniac", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 7, col = 1, desc = "" },
    { id = 11129, ranks = {11129}, name = "Combustion", icon = "Interface\\Icons\\Spell_Nature_WispHeal", maxRank = 1, tier = 7, col = 2, desc = "+2% Fire crit.", prereq = {5, 2} },
    { id = 31679, ranks = {31679, 31680}, name = "Molten Fury", icon = "Interface\\Icons\\Spell_Fire_EmpoweredFire", maxRank = 2, tier = 7, col = 3, desc = "+5% Ignite damage, Intellect as spell power." },
    -- Tier 8
    { id = 64353, ranks = {64353, 64357}, name = "Fiery Payback", icon = "Interface\\Icons\\Spell_Fire_MoltenFury", maxRank = 2, tier = 8, col = 1, desc = "+6% damage vs targets below 35%." },
    { id = 31656, ranks = {31656, 31657, 31658}, name = "Empowered Fire", icon = "Interface\\Icons\\Spell_Fire_Burnout", maxRank = 3, tier = 8, col = 3, desc = "+1% crit and -5% mana when 2+ Fire DoTs." },
    -- Tier 9
    { id = 44442, ranks = {44442, 44443}, name = "Firestarter", icon = "Interface\\Icons\\Ability_Mage_Firestarter", maxRank = 2, tier = 9, col = 1, desc = "Blastwave/Dragon's Breath proc instant Flamestrike.", prereq = {9, 2} },
    { id = 31661, ranks = {31661}, name = "Dragon's Breath", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 9, col = 2, desc = "", prereq = {7, 2} },
    { id = 44445, ranks = {44445, 44446, 44448}, name = "Hot Streak", icon = "Interface\\Icons\\INV_Misc_Head_Dragon_01", maxRank = 3, tier = 9, col = 3, desc = "Cone Fire damage + disorient." },
    -- Tier 10
    { id = 44449, ranks = {44449, 44469, 44470, 44471, 44472}, name = "Burnout", icon = "Interface\\Icons\\Ability_Mage_HotStreak", maxRank = 5, tier = 10, col = 2, desc = "2 crits in a row proc instant Pyroblast." },
    -- Tier 11
    { id = 44457, ranks = {44457}, name = "Living Bomb", icon = "Interface\\Icons\\Spell_Fire_Burnout", maxRank = 1, tier = 11, col = 2, desc = "+10% spell crit damage, extra mana cost." },
}

-- FROST (Spec 3)
local Frost = {
    -- Tier 1
    { id = 11071, ranks = {11071, 12496, 12497}, name = "Frostbite", icon = "Interface\\Icons\\Spell_Frost_IceFloes", maxRank = 3, tier = 1, col = 1, desc = "-7% Frost Nova/CoC/Ice Block cooldown." },
    { id = 11070, ranks = {11070, 12473, 16763, 16765, 16766}, name = "Improved Frostbolt", icon = "Interface\\Icons\\Spell_Frost_FrostArmor", maxRank = 5, tier = 1, col = 2, desc = "Chill effects can freeze." },
    { id = 31670, ranks = {31670, 31672, 55094}, name = "Ice Floes", icon = "Interface\\Icons\\Spell_Frost_ChillingBlast", maxRank = 3, tier = 1, col = 3, desc = "Frost damage applies +1% Frost crit debuff." },
    -- Tier 2
    { id = 11207, ranks = {11207, 12672, 15047}, name = "Ice Shards", icon = "Interface\\Icons\\Spell_Frost_FrostBolt02", maxRank = 3, tier = 2, col = 1, desc = "-0.1 sec Frostbolt cast." },
    { id = 11189, ranks = {11189, 28332}, name = "Frost Warding", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 2, col = 2, desc = "" },
    { id = 29438, ranks = {29438, 29439, 29440}, name = "Precision", icon = "Interface\\Icons\\Spell_Frost_FrostWard", maxRank = 3, tier = 2, col = 3, desc = "+15% Frost Ward/Ice Barrier effect." },
    { id = 11175, ranks = {11175, 12569, 12571}, name = "Permafrost", icon = "Interface\\Icons\\Spell_Ice_MagicDamage", maxRank = 3, tier = 2, col = 4, desc = "+1% Frost hit, -1% mana cost." },
    -- Tier 3
    { id = 11151, ranks = {11151, 12952, 12953}, name = "Piercing Ice", icon = "Interface\\Icons\\Spell_Frost_Frostbolt", maxRank = 3, tier = 3, col = 1, desc = "+2% Frost damage." },
    { id = 12472, ranks = {12472}, name = "Icy Veins", icon = "Interface\\Icons\\Spell_Frost_ColdHearted", maxRank = 1, tier = 3, col = 2, desc = "+20% spell haste for 20 sec." },
    { id = 11185, ranks = {11185, 12487, 12488}, name = "Improved Blizzard", icon = "Interface\\Icons\\Spell_Frost_IceStorm", maxRank = 3, tier = 3, col = 3, desc = "Blizzard chills targets." },
    -- Tier 4
    { id = 16757, ranks = {16757, 16758}, name = "Arctic Reach", icon = "Interface\\Icons\\Spell_Frost_Wisp", maxRank = 2, tier = 4, col = 1, desc = "+4% Chill slow, +50% Snare duration." },
    { id = 11160, ranks = {11160, 12518, 12519}, name = "Frost Channeling", icon = "Interface\\Icons\\Spell_Frost_Stun", maxRank = 3, tier = 4, col = 2, desc = "-4% Frost mana cost, -4% threat." },
    { id = 11170, ranks = {11170, 12982, 12983}, name = "Shatter", icon = "Interface\\Icons\\Spell_Frost_FrostShock", maxRank = 3, tier = 4, col = 3, desc = "+17% crit vs frozen." },
    -- Tier 5
    { id = 11958, ranks = {11958}, name = "Cold Snap", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 5, col = 2, desc = "" },
    { id = 11190, ranks = {11190, 12489, 12490}, name = "Improved Cone of Cold", icon = "Interface\\Icons\\Spell_Frost_WizardMark", maxRank = 3, tier = 5, col = 3, desc = "Resets Frost ability cooldowns." },
    { id = 31667, ranks = {31667, 31668, 31669}, name = "Frozen Core", icon = "Interface\\Icons\\Spell_Frost_ColdAsIce", maxRank = 3, tier = 5, col = 4, desc = "-10% Cold Snap/Ice Barrier cooldown." },
    -- Tier 6
    { id = 55091, ranks = {55091, 55092}, name = "Cold as Ice", icon = "Interface\\Icons\\Spell_Shadow_DarkRitual", maxRank = 2, tier = 6, col = 1, desc = "+10% Frostbolt/Ice Lance/Blizzard range.", prereq = {5, 2} },
    { id = 11180, ranks = {11180, 28592, 28593}, name = "Winter's Chill", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 6, col = 3, desc = "" },
    -- Tier 7
    { id = 44745, ranks = {44745, 54787}, name = "Shattered Barrier", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 7, col = 1, desc = "", prereq = {7, 2} },
    { id = 11426, ranks = {11426}, name = "Ice Barrier", icon = "Interface\\Icons\\Spell_Ice_Lament", maxRank = 1, tier = 7, col = 2, desc = "Absorb damage shield.", prereq = {5, 2} },
    { id = 31674, ranks = {31674, 31675, 31676, 31677, 31678}, name = "Arctic Winds", icon = "Interface\\Icons\\Spell_Frost_SummonWaterElemental_2", maxRank = 5, tier = 7, col = 3, desc = "Summon Water Elemental." },
    -- Tier 8
    { id = 31682, ranks = {31682, 31683}, name = "Empowered Frostbolt", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 2, tier = 8, col = 2, desc = "" },
    { id = 44543, ranks = {44543, 44545}, name = "Fingers of Frost", icon = "Interface\\Icons\\Spell_Frost_ShatteredBarrier", maxRank = 2, tier = 8, col = 3, desc = "Ice Barrier break procs Frost Nova." },
    -- Tier 9
    { id = 44546, ranks = {44546, 44548, 44549}, name = "Brain Freeze", icon = "Interface\\Icons\\Spell_Frost_ShatteredBarrier", maxRank = 3, tier = 9, col = 1, desc = "Ice Barrier break procs Frost Nova." },
    { id = 31687, ranks = {31687}, name = "Summon Water Elemental", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 1, tier = 9, col = 2, desc = "" },
    { id = 44557, ranks = {44557, 44560, 44561}, name = "Enduring Winter", icon = "Interface\\Icons\\INV_Misc_QuestionMark", maxRank = 3, tier = 9, col = 3, desc = "", prereq = {9, 2} },
    -- Tier 10
    { id = 44566, ranks = {44566, 44567, 44568, 44570, 44571}, name = "Chilled to the Bone", icon = "Interface\\Icons\\Spell_Frost_ArcticWinds", maxRank = 5, tier = 10, col = 2, desc = "+1% Frost damage, -1% hit vs you." },
    -- Tier 11
    { id = 44572, ranks = {44572}, name = "Deep Freeze", icon = "Interface\\Icons\\Ability_Mage_FingersOfFrost", maxRank = 1, tier = 11, col = 2, desc = "Chill effects can proc Shatter." },
}

Adv2.Data.Talents[8] = {
    [1] = Arcane,
    [2] = Fire,
    [3] = Frost,
}
