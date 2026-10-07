-- Adventurer2 - Complete Class Abilities
-- ALL trainable spells for every class in WoW 3.3.5a
-- Organized by class with highest rank only

Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}

-- Format: { id = spellId, name = "Spell Name", icon = "path" }
-- Icons will be fetched dynamically if not provided

Adv2.Data.AllSpells = {
    -- =========================================================================
    -- WARRIOR (Class 1)
    -- =========================================================================
    [1] = {
        name = "Warrior",
        spells = {
            -- Stances
            { id = 2457, name = "Battle Stance" },
            { id = 71, name = "Defensive Stance" },
            { id = 2458, name = "Berserker Stance" },
            
            -- Arms/Basic
            { id = 78, name = "Heroic Strike" },
            { id = 772, name = "Rend" },
            { id = 6343, name = "Thunder Clap" },
            { id = 57755, name = "Heroic Throw" },
            { id = 845, name = "Cleave" },
            { id = 1680, name = "Whirlwind" },
            { id = 5308, name = "Execute" },
            { id = 7384, name = "Overpower" },
            { id = 64382, name = "Shattering Throw" },
            { id = 1715, name = "Hamstring" },
            { id = 676, name = "Disarm" },
            { id = 694, name = "Mocking Blow" },
            { id = 6572, name = "Revenge" },
            { id = 1464, name = "Slam" },
            { id = 20252, name = "Intercept" },
            { id = 6552, name = "Pummel" },
            
            -- Shouts
            { id = 6673, name = "Battle Shout" },
            { id = 1160, name = "Demoralizing Shout" },
            { id = 469, name = "Commanding Shout" },
            { id = 5246, name = "Intimidating Shout" },
            { id = 18499, name = "Berserker Rage" },
            { id = 12323, name = "Piercing Howl" },
            { id = 1161, name = "Challenging Shout" },
            
            -- Charge/Movement
            { id = 100, name = "Charge" },
            { id = 3411, name = "Intervene" },
            
            -- Defensive
            { id = 871, name = "Shield Wall" },
            { id = 2565, name = "Shield Block" },
            { id = 23922, name = "Shield Slam" },
            { id = 72, name = "Shield Bash" },
            { id = 12975, name = "Last Stand" },
            { id = 355, name = "Taunt" },
            { id = 1719, name = "Recklessness" },
            { id = 18499, name = "Berserker Rage" },
            { id = 23920, name = "Spell Reflection" },
            { id = 55694, name = "Enraged Regeneration" },
            { id = 20230, name = "Retaliation" },
            
            -- Talents (top picks)
            { id = 12294, name = "Mortal Strike" },
            { id = 23881, name = "Bloodthirst" },
            { id = 20243, name = "Devastate" },
            { id = 46924, name = "Bladestorm" },
            { id = 46968, name = "Shockwave" },
            { id = 12809, name = "Concussion Blow" },
            { id = 12292, name = "Death Wish" },
            { id = 12328, name = "Sweeping Strikes" },
            { id = 60970, name = "Heroic Fury" },
            { id = 46917, name = "Titan's Grip" },
            { id = 29801, name = "Rampage" },
            { id = 50720, name = "Vigilance" },
            { id = 12294, name = "Mortal Strike (Rank 1)" },
        },
    },

    -- =========================================================================
    -- PALADIN (Class 2)
    -- =========================================================================
    [2] = {
        name = "Paladin",
        spells = {
            -- Holy
            { id = 635, name = "Holy Light" },
            { id = 19750, name = "Flash of Light" },
            { id = 879, name = "Exorcism" },
            { id = 2812, name = "Holy Wrath" },
            { id = 20473, name = "Holy Shock" },
            { id = 53563, name = "Beacon of Light" },
            { id = 54428, name = "Divine Plea" },
            { id = 31842, name = "Divine Illumination" },
            { id = 20216, name = "Divine Favor" },
            
            -- Protection
            { id = 31935, name = "Avenger's Shield" },
            { id = 24275, name = "Hammer of Wrath" },
            { id = 26573, name = "Consecration" },
            { id = 53595, name = "Hammer of the Righteous" },
            { id = 20925, name = "Holy Shield" },
            { id = 465, name = "Devotion Aura" },
            { id = 19876, name = "Shadow Resistance Aura" },
            { id = 7294, name = "Retribution Aura" },
            { id = 19746, name = "Concentration Aura" },
            { id = 32223, name = "Crusader Aura" },
            { id = 62124, name = "Hand of Reckoning" },
            { id = 31789, name = "Righteous Defense" },
            { id = 20164, name = "Seal of Justice" },
            { id = 20165, name = "Seal of Light" },
            { id = 53736, name = "Seal of Corruption" },
            { id = 20375, name = "Seal of Command" },
            { id = 20166, name = "Seal of Wisdom" },
            { id = 31801, name = "Seal of Vengeance" },
            { id = 21084, name = "Seal of Righteousness" },
            
            -- Blessings/Hands
            { id = 25782, name = "Greater Blessing of Might" },
            { id = 25898, name = "Greater Blessing of Kings" },
            { id = 19740, name = "Blessing of Might" },
            { id = 20217, name = "Blessing of Kings" },
            { id = 19742, name = "Blessing of Wisdom" },
            { id = 1038, name = "Hand of Salvation" },
            { id = 1022, name = "Hand of Protection" },
            { id = 1044, name = "Hand of Freedom" },
            { id = 6940, name = "Hand of Sacrifice" },
            
            -- Judgements
            { id = 20271, name = "Judgement of Light" },
            { id = 53408, name = "Judgement of Wisdom" },
            { id = 53407, name = "Judgement of Justice" },
            
            -- Retribution
            { id = 35395, name = "Crusader Strike" },
            { id = 53385, name = "Divine Storm" },
            { id = 20066, name = "Repentance" },
            { id = 31884, name = "Avenging Wrath" },
            
            -- Utility
            { id = 853, name = "Hammer of Justice" },
            { id = 853, name = "Hammer of Justice (Rank 1)" },
            { id = 642, name = "Divine Shield" },
            { id = 633, name = "Lay on Hands" },
            { id = 53385, name = "Divine Storm" },
            { id = 7328, name = "Redemption" },
            { id = 4987, name = "Cleanse" },
            { id = 1152, name = "Purify" },
            { id = 19752, name = "Divine Intervention" },
            { id = 25780, name = "Righteous Fury" },
            { id = 66233, name = "Ardent Defender" },
            { id = 20178, name = "Seal of Martyrdom" },
        },
    },

    -- =========================================================================
    -- HUNTER (Class 3)
    -- =========================================================================
    [3] = {
        name = "Hunter",
        spells = {
            -- Shots
            { id = 75, name = "Auto Shot" },
            { id = 19434, name = "Aimed Shot" },
            { id = 2643, name = "Multi-Shot" },
            { id = 56641, name = "Steady Shot" },
            { id = 53301, name = "Explosive Shot" },
            { id = 53209, name = "Chimera Shot" },
            { id = 1978, name = "Serpent Sting" },
            { id = 3043, name = "Scorpid Sting" },
            { id = 3034, name = "Viper Sting" },
            { id = 53351, name = "Kill Shot" },
            { id = 3044, name = "Arcane Shot" },
            { id = 1510, name = "Volley" },
            { id = 19503, name = "Scatter Shot" },
            { id = 34490, name = "Silencing Shot" },
            { id = 19434, name = "Aimed Shot (Rank 1)" },
            { id = 5116, name = "Concussive Shot" },
            { id = 20736, name = "Distracting Shot" },
            { id = 2974, name = "Wing Clip" },
            
            -- Traps
            { id = 13813, name = "Explosive Trap" },
            { id = 1499, name = "Freezing Trap" },
            { id = 13809, name = "Frost Trap" },
            { id = 13795, name = "Immolation Trap" },
            { id = 34600, name = "Snake Trap" },
            { id = 3674, name = "Black Arrow" },
            
            -- Pet Management
            { id = 883, name = "Call Pet" },
            { id = 2641, name = "Dismiss Pet" },
            { id = 982, name = "Revive Pet" },
            { id = 6991, name = "Feed Pet" },
            { id = 136, name = "Mend Pet" },
            { id = 1515, name = "Tame Beast" },
            { id = 1002, name = "Eyes of the Beast" },
            { id = 34026, name = "Kill Command" },
            { id = 19577, name = "Intimidation" },
            { id = 19574, name = "Bestial Wrath" },
            
            -- Utility
            { id = 1494, name = "Track Beasts" },
            { id = 19878, name = "Track Demons" },
            { id = 19879, name = "Track Dragonkin" },
            { id = 19880, name = "Track Elementals" },
            { id = 19882, name = "Track Giants" },
            { id = 19885, name = "Track Hidden" },
            { id = 19883, name = "Track Humanoids" },
            { id = 19884, name = "Track Undead" },
            { id = 5384, name = "Feign Death" },
            { id = 3045, name = "Rapid Fire" },
            { id = 781, name = "Disengage" },
            { id = 19263, name = "Deterrence" },
            { id = 34477, name = "Misdirection" },
            { id = 1543, name = "Flare" },
            { id = 60192, name = "Freezing Arrow" },
            { id = 13159, name = "Aspect of the Pack" },
            { id = 61846, name = "Aspect of the Dragonhawk" },
            { id = 13165, name = "Aspect of the Hawk" },
            { id = 5118, name = "Aspect of the Cheetah" },
            { id = 13163, name = "Aspect of the Monkey" },
            { id = 34074, name = "Aspect of the Viper" },
            { id = 13161, name = "Aspect of the Beast" },
            { id = 20043, name = "Aspect of the Wild" },
            { id = 1513, name = "Scare Beast" },
            { id = 19306, name = "Counterattack" },
            { id = 19386, name = "Wyvern Sting" },
            { id = 23989, name = "Readiness" },
        },
    },

    -- =========================================================================
    -- ROGUE (Class 4)
    -- =========================================================================
    [4] = {
        name = "Rogue",
        spells = {
            -- Combo Builders
            { id = 1752, name = "Sinister Strike" },
            { id = 53, name = "Backstab" },
            { id = 1329, name = "Mutilate" },
            { id = 16511, name = "Hemorrhage" },
            { id = 6770, name = "Sap" },
            { id = 5938, name = "Shiv" },
            { id = 5938, name = "Shiv" },
            { id = 1776, name = "Gouge" },
            { id = 1776, name = "Gouge" },
            { id = 1833, name = "Cheap Shot" },
            { id = 1966, name = "Feint" },
            { id = 1966, name = "Feint" },
            { id = 703, name = "Garrote" },
            { id = 703, name = "Garrote" },
            
            -- Finishers
            { id = 2098, name = "Eviscerate" },
            { id = 51723, name = "Fan of Knives" },
            { id = 26679, name = "Deadly Throw" },
            { id = 8647, name = "Expose Armor" },
            { id = 6770, name = "Sap" },
            { id = 1943, name = "Rupture" },
            { id = 1943, name = "Rupture" },
            { id = 32645, name = "Envenom" },
            { id = 408, name = "Kidney Shot" },
            { id = 51690, name = "Killing Spree" },
            { id = 5171, name = "Slice and Dice" },
            
            -- Poisons
            { id = 2818, name = "Deadly Poison" },
            { id = 8679, name = "Instant Poison" },
            { id = 3408, name = "Crippling Poison" },
            { id = 13218, name = "Wound Poison" },
            { id = 5761, name = "Mind-numbing Poison" },
            { id = 26688, name = "Anesthetic Poison" },
            
            -- Utility
            { id = 1784, name = "Stealth" },
            { id = 1856, name = "Vanish" },
            { id = 57934, name = "Tricks of the Trade" },
            { id = 2094, name = "Blind" },
            { id = 1766, name = "Kick" },
            { id = 26679, name = "Deadly Throw" },
            { id = 31224, name = "Cloak of Shadows" },
            { id = 5277, name = "Evasion" },
            { id = 2983, name = "Sprint" },
            { id = 36554, name = "Shadowstep" },
            { id = 14185, name = "Preparation" },
            { id = 13877, name = "Blade Flurry" },
            { id = 13750, name = "Adrenaline Rush" },
            { id = 14177, name = "Cold Blood" },
            { id = 51713, name = "Shadow Dance" },
            { id = 2836, name = "Detect Traps" },
            { id = 1804, name = "Pick Lock" },
            { id = 1860, name = "Safe Fall" },
            { id = 921, name = "Pick Pocket" },
            { id = 1842, name = "Disarm Trap" },
        },
    },

    -- =========================================================================
    -- PRIEST (Class 5)
    -- =========================================================================
    [5] = {
        name = "Priest",
        spells = {
            -- Holy Healing
            { id = 2060, name = "Greater Heal" },
            { id = 2061, name = "Flash Heal" },
            { id = 139, name = "Renew" },
            { id = 596, name = "Prayer of Healing" },
            { id = 33076, name = "Prayer of Mending" },
            { id = 47788, name = "Guardian Spirit" },
            { id = 34861, name = "Circle of Healing" },
            { id = 724, name = "Lightwell" },
            { id = 19236, name = "Desperate Prayer" },
            { id = 64843, name = "Divine Hymn" },
            { id = 64844, name = "Divine Hymn" },
            { id = 32546, name = "Binding Heal" },
            { id = 596, name = "Prayer of Healing" },
            
            -- Discipline
            { id = 17, name = "Power Word: Shield" },
            { id = 588, name = "Inner Fire" },
            { id = 33206, name = "Pain Suppression" },
            { id = 47540, name = "Penance" },
            { id = 14752, name = "Divine Spirit" },
            { id = 27681, name = "Prayer of Spirit" },
            { id = 14751, name = "Inner Focus" },
            { id = 10060, name = "Power Infusion" },
            { id = 6346, name = "Fear Ward" },
            { id = 64901, name = "Hymn of Hope" },
            { id = 64904, name = "Hymn of Hope" },
            { id = 1243, name = "Power Word: Fortitude" },
            { id = 21562, name = "Prayer of Fortitude" },
            
            -- Shadow
            { id = 32379, name = "Shadow Word: Death" },
            { id = 589, name = "Shadow Word: Pain" },
            { id = 34914, name = "Vampiric Touch" },
            { id = 8092, name = "Mind Blast" },
            { id = 15407, name = "Mind Flay" },
            { id = 2944, name = "Devouring Plague" },
            { id = 15473, name = "Shadowform" },
            { id = 47585, name = "Dispersion" },
            { id = 15487, name = "Silence" },
            { id = 64044, name = "Psychic Horror" },
            { id = 34914, name = "Vampiric Touch" },
            { id = 15286, name = "Vampiric Embrace" },
            
            -- Utility
            { id = 586, name = "Fade" },
            { id = 605, name = "Mind Control" },
            { id = 32375, name = "Mass Dispel" },
            { id = 8122, name = "Psychic Scream" },
            { id = 9484, name = "Shackle Undead" },
            { id = 527, name = "Dispel Magic" },
            { id = 527, name = "Dispel Magic" },
            { id = 453, name = "Mind Soothe" },
            { id = 2006, name = "Resurrection" },
            { id = 2006, name = "Resurrection" },
            { id = 1706, name = "Levitate" },
            { id = 9484, name = "Shackle Undead" },
            { id = 15237, name = "Holy Nova" },
            { id = 585, name = "Smite" },
            { id = 8129, name = "Mana Burn" },
            { id = 2096, name = "Mind Vision" },
        },
    },

    -- =========================================================================
    -- DEATH KNIGHT (Class 6)
    -- =========================================================================
    [6] = {
        name = "Death Knight",
        spells = {
            -- Presences
            { id = 48266, name = "Blood Presence" },
            { id = 48263, name = "Frost Presence" },
            { id = 48265, name = "Unholy Presence" },
            
            -- Blood
            { id = 45902, name = "Blood Strike" },
            { id = 55050, name = "Heart Strike" },
            { id = 55233, name = "Vampiric Blood" },
            { id = 55050, name = "Heart Strike" },
            { id = 48982, name = "Rune Tap" },
            { id = 49028, name = "Dancing Rune Weapon" },
            { id = 49016, name = "Hysteria" },
            { id = 56815, name = "Rune Strike" },
            
            -- Frost
            { id = 49143, name = "Frost Strike" },
            { id = 49184, name = "Howling Blast" },
            { id = 49143, name = "Frost Strike" },
            { id = 49203, name = "Hungering Cold" },
            { id = 51271, name = "Unbreakable Armor" },
            { id = 45524, name = "Chains of Ice" },
            { id = 49020, name = "Obliterate" },
            { id = 57330, name = "Horn of Winter" },
            { id = 49184, name = "Howling Blast" },
            
            -- Unholy
            { id = 48721, name = "Blood Boil" },
            { id = 55090, name = "Scourge Strike" },
            { id = 55090, name = "Scourge Strike" },
            { id = 43265, name = "Death and Decay" },
            { id = 49206, name = "Summon Gargoyle" },
            { id = 51052, name = "Anti-Magic Zone" },
            { id = 63560, name = "Ghoul Frenzy" },
            { id = 70895, name = "Dark Transformation" },
            { id = 49222, name = "Bone Shield" },
            
            -- Core
            { id = 45462, name = "Plague Strike" },
            { id = 45477, name = "Icy Touch" },
            { id = 56222, name = "Dark Command" },
            { id = 47528, name = "Mind Freeze" },
            { id = 48707, name = "Anti-Magic Shell" },
            { id = 49576, name = "Death Grip" },
            { id = 47568, name = "Empower Rune Weapon" },
            { id = 48743, name = "Death Pact" },
            { id = 50977, name = "Death Gate" },
            { id = 46584, name = "Raise Dead" },
            { id = 49998, name = "Death Strike" },
            { id = 42650, name = "Army of the Dead" },
            { id = 43265, name = "Death and Decay" },
            { id = 3714, name = "Path of Frost" },
            { id = 61999, name = "Raise Ally" },
            { id = 48792, name = "Icebound Fortitude" },
            { id = 49039, name = "Lichborne" },
            { id = 49158, name = "Corpse Explosion" },
            { id = 47476, name = "Strangulate" },
        },
    },

    -- =========================================================================
    -- SHAMAN (Class 7)
    -- =========================================================================
    [7] = {
        name = "Shaman",
        spells = {
            -- Elemental
            { id = 403, name = "Lightning Bolt" },
            { id = 421, name = "Chain Lightning" },
            { id = 8050, name = "Flame Shock" },
            { id = 8042, name = "Earth Shock" },
            { id = 8056, name = "Frost Shock" },
            { id = 51505, name = "Lava Burst" },
            { id = 51490, name = "Thunderstorm" },
            { id = 16166, name = "Elemental Mastery" },
            
            -- Enhancement
            { id = 17364, name = "Stormstrike" },
            { id = 60103, name = "Lava Lash" },
            { id = 51533, name = "Feral Spirit" },
            { id = 30823, name = "Shamanistic Rage" },
            { id = 58875, name = "Spirit Walk" },
            { id = 58875, name = "Spirit Walk" },
            { id = 8232, name = "Windfury Weapon" },
            { id = 8024, name = "Flametongue Weapon" },
            { id = 8033, name = "Frostbrand Weapon" },
            { id = 8017, name = "Rockbiter Weapon" },
            { id = 51730, name = "Earthliving Weapon" },
            
            -- Restoration
            { id = 8004, name = "Lesser Healing Wave" },
            { id = 331, name = "Healing Wave" },
            { id = 1064, name = "Chain Heal" },
            { id = 974, name = "Earth Shield" },
            { id = 61295, name = "Riptide" },
            { id = 16190, name = "Mana Tide Totem" },
            { id = 51886, name = "Cleanse Spirit" },
            { id = 55198, name = "Tidal Force" },
            { id = 16188, name = "Nature's Swiftness" },
            
            -- Totems
            { id = 8190, name = "Magma Totem" },
            { id = 5730, name = "Stoneclaw Totem" },
            { id = 2484, name = "Earthbind Totem" },
            { id = 32062, name = "Fire Nova Totem" },
            { id = 8227, name = "Flametongue Totem" },
            { id = 57994, name = "Wind Shear" },
            { id = 8177, name = "Grounding Totem" },
            { id = 5730, name = "Stoneclaw Totem" },
            { id = 8190, name = "Magma Totem" },
            { id = 5675, name = "Mana Spring Totem" },
            { id = 5394, name = "Healing Stream Totem" },
            { id = 8075, name = "Strength of Earth Totem" },
            { id = 8512, name = "Windfury Totem" },
            { id = 3738, name = "Wrath of Air Totem" },
            { id = 8143, name = "Tremor Totem" },
            { id = 3599, name = "Searing Totem" },
            { id = 2894, name = "Fire Elemental Totem" },
            { id = 2062, name = "Earth Elemental Totem" },
            
            -- Utility
            { id = 51514, name = "Hex" },
            { id = 2645, name = "Ghost Wolf" },
            { id = 546, name = "Water Walking" },
            { id = 131, name = "Water Breathing" },
            { id = 556, name = "Astral Recall" },
            { id = 2825, name = "Bloodlust" },
            { id = 32182, name = "Heroism" },
            { id = 20608, name = "Reincarnation" },
            { id = 370, name = "Purge" },
            { id = 2008, name = "Ancestral Spirit" },
            { id = 526, name = "Cure Toxins" },
        },
    },

    -- =========================================================================
    -- MAGE (Class 8)
    -- =========================================================================
    [8] = {
        name = "Mage",
        spells = {
            -- Fire
            { id = 133, name = "Fireball" },
            { id = 11366, name = "Pyroblast" },
            { id = 2948, name = "Scorch" },
            { id = 11113, name = "Blast Wave" },
            { id = 31661, name = "Dragon's Breath" },
            { id = 44457, name = "Living Bomb" },
            { id = 2136, name = "Fire Blast" },
            { id = 2120, name = "Flamestrike" },
            { id = 11129, name = "Combustion" },
            
            -- Frost
            { id = 116, name = "Frostbolt" },
            { id = 122, name = "Frost Nova" },
            { id = 120, name = "Cone of Cold" },
            { id = 10, name = "Blizzard" },
            { id = 44572, name = "Deep Freeze" },
            { id = 45438, name = "Ice Block" },
            { id = 12472, name = "Icy Veins" },
            { id = 31687, name = "Summon Water Elemental" },
            { id = 44614, name = "Frostfire Bolt" },
            { id = 11426, name = "Ice Barrier" },
            { id = 55342, name = "Mirror Image" },
            { id = 31661, name = "Dragon's Breath" },
            
            -- Arcane
            { id = 30451, name = "Arcane Blast" },
            { id = 5143, name = "Arcane Missiles" },
            { id = 1449, name = "Arcane Explosion" },
            { id = 44425, name = "Arcane Barrage" },
            { id = 12042, name = "Arcane Power" },
            { id = 12043, name = "Presence of Mind" },
            { id = 31589, name = "Slow" },
            { id = 44572, name = "Deep Freeze" },
            { id = 12051, name = "Evocation" },
            { id = 66, name = "Invisibility" },
            
            -- Utility
            { id = 118, name = "Polymorph" },
            { id = 28272, name = "Polymorph: Pig" },
            { id = 28271, name = "Polymorph: Turtle" },
            { id = 61305, name = "Polymorph: Cat" },
            { id = 61721, name = "Polymorph: Rabbit" },
            { id = 2139, name = "Counterspell" },
            { id = 1953, name = "Blink" },
            { id = 587, name = "Conjure Food" },
            { id = 759, name = "Conjure Mana Gem" },
            { id = 5504, name = "Conjure Water" },
            { id = 604, name = "Dampen Magic" },
            { id = 1008, name = "Amplify Magic" },
            { id = 7302, name = "Ice Armor" },
            { id = 6117, name = "Mage Armor" },
            { id = 30482, name = "Molten Armor" },
            { id = 1459, name = "Arcane Intellect" },
            { id = 23028, name = "Arcane Brilliance" },
            { id = 43987, name = "Ritual of Refreshment" },
            { id = 54646, name = "Focus Magic" },
            { id = 30449, name = "Spellsteal" },
            { id = 475, name = "Remove Curse" },
            { id = 130, name = "Slow Fall" },
            { id = 11416, name = "Portal: Ironforge" },
            { id = 11419, name = "Portal: Darnassus" },
            { id = 32266, name = "Portal: Exodar" },
            { id = 10059, name = "Portal: Stormwind" },
            { id = 49360, name = "Portal: Theramore" },
            { id = 11417, name = "Portal: Orgrimmar" },
            { id = 11418, name = "Portal: Undercity" },
            { id = 11420, name = "Portal: Thunder Bluff" },
            { id = 32267, name = "Portal: Silvermoon" },
            { id = 49361, name = "Portal: Stonard" },
            { id = 53142, name = "Portal: Dalaran" },
            { id = 35715, name = "Portal: Shattrath (Alliance)" },
            { id = 35717, name = "Portal: Shattrath (Horde)" },
        },
    },

    -- =========================================================================
    -- WARLOCK (Class 9)
    -- =========================================================================
    [9] = {
        name = "Warlock",
        spells = {
            -- Affliction
            { id = 172, name = "Corruption" },
            { id = 980, name = "Curse of Agony" },
            { id = 603, name = "Curse of Doom" },
            { id = 6789, name = "Death Coil" },
            { id = 30108, name = "Unstable Affliction" },
            { id = 48181, name = "Haunt" },
            { id = 27243, name = "Seed of Corruption" },
            { id = 755, name = "Health Funnel" },
            { id = 689, name = "Drain Life" },
            { id = 1120, name = "Drain Soul" },
            { id = 18223, name = "Curse of Exhaustion" },
            { id = 18223, name = "Curse of Exhaustion" },
            { id = 1714, name = "Curse of Tongues" },
            { id = 17937, name = "Curse of Shadow" },
            { id = 16231, name = "Curse of Recklessness" },
            { id = 1714, name = "Curse of Tongues" },
            { id = 702, name = "Curse of Weakness" },
            { id = 17862, name = "Curse of Shadow" },
            { id = 1490, name = "Curse of the Elements" },
            { id = 35195, name = "Siphon Life" },
            { id = 35195, name = "Siphon Life" },
            
            -- Demonology
            { id = 688, name = "Summon Imp" },
            { id = 697, name = "Summon Voidwalker" },
            { id = 712, name = "Summon Succubus" },
            { id = 691, name = "Summon Felhunter" },
            { id = 37277, name = "Summon Infernal" },
            { id = 22865, name = "Summon Doomguard" },
            { id = 30146, name = "Summon Felguard" },
            { id = 47241, name = "Metamorphosis" },
            { id = 47193, name = "Demonic Empowerment" },
            { id = 18708, name = "Fel Domination" },
            { id = 6229, name = "Shadow Ward" },
            { id = 706, name = "Demon Armor" },
            { id = 28176, name = "Fel Armor" },
            { id = 48018, name = "Demonic Circle: Summon" },
            { id = 48020, name = "Demonic Circle: Teleport" },
            { id = 50589, name = "Immolation Aura" },
            { id = 50581, name = "Shadow Cleave" },
            
            -- Destruction
            { id = 686, name = "Shadow Bolt" },
            { id = 29722, name = "Incinerate" },
            { id = 6353, name = "Soul Fire" },
            { id = 50796, name = "Chaos Bolt" },
            { id = 30283, name = "Shadowfury" },
            { id = 17962, name = "Conflagrate" },
            { id = 5740, name = "Rain of Fire" },
            { id = 348, name = "Immolate" },
            { id = 5676, name = "Searing Pain" },
            { id = 1949, name = "Hellfire" },
            { id = 47897, name = "Shadowflame" },
            { id = 17962, name = "Conflagrate" },
            
            -- Utility
            { id = 5782, name = "Fear" },
            { id = 5484, name = "Howl of Terror" },
            { id = 710, name = "Banish" },
            { id = 6201, name = "Create Healthstone" },
            { id = 29893, name = "Ritual of Souls" },
            { id = 698, name = "Ritual of Summoning" },
            { id = 29858, name = "Soulshatter" },
            { id = 6203, name = "Soulstone" },
            { id = 132, name = "Detect Invisibility" },
            { id = 5697, name = "Unending Breath" },
            { id = 126, name = "Eye of Kilrogg" },
            { id = 17877, name = "Shadowburn" },
            { id = 6353, name = "Soul Fire" },
        },
    },

    -- =========================================================================
    -- DRUID (Class 11)
    -- =========================================================================
    [11] = {
        name = "Druid",
        spells = {
            -- Forms
            { id = 5487, name = "Bear Form" },
            { id = 9634, name = "Dire Bear Form" },
            { id = 768, name = "Cat Form" },
            { id = 783, name = "Travel Form" },
            { id = 1066, name = "Aquatic Form" },
            { id = 24858, name = "Moonkin Form" },
            { id = 33891, name = "Tree of Life" },
            { id = 40120, name = "Swift Flight Form" },
            
            -- Balance
            { id = 5176, name = "Wrath" },
            { id = 2912, name = "Starfire" },
            { id = 8921, name = "Moonfire" },
            { id = 5570, name = "Insect Swarm" },
            { id = 48505, name = "Starfall" },
            { id = 16914, name = "Hurricane" },
            { id = 33831, name = "Force of Nature" },
            { id = 50516, name = "Typhoon" },
            { id = 50516, name = "Typhoon" },
            { id = 48505, name = "Starfall" },
            { id = 33876, name = "Mangle (Cat)" },
            
            -- Feral Cat
            { id = 1082, name = "Claw" },
            { id = 52610, name = "Savage Roar" },
            { id = 1079, name = "Rip" },
            { id = 22568, name = "Ferocious Bite" },
            { id = 5221, name = "Shred" },
            { id = 9005, name = "Pounce" },
            { id = 5217, name = "Tiger's Fury" },
            { id = 62078, name = "Swipe (Cat)" },
            { id = 1822, name = "Rake" },
            { id = 33876, name = "Mangle (Cat)" },
            
            -- Feral Bear
            { id = 6795, name = "Growl" },
            { id = 6807, name = "Maul" },
            { id = 99, name = "Demoralizing Roar" },
            { id = 5209, name = "Challenging Roar" },
            { id = 5211, name = "Bash" },
            { id = 33745, name = "Lacerate" },
            { id = 779, name = "Swipe (Bear)" },
            { id = 33878, name = "Mangle (Bear)" },
            { id = 22842, name = "Frenzied Regeneration" },
            { id = 5229, name = "Enrage" },
            { id = 61336, name = "Survival Instincts" },
            { id = 50334, name = "Berserk" },
            { id = 62606, name = "Savage Defense" },
            
            -- Restoration
            { id = 5185, name = "Healing Touch" },
            { id = 774, name = "Rejuvenation" },
            { id = 8936, name = "Regrowth" },
            { id = 48438, name = "Wild Growth" },
            { id = 48438, name = "Wild Growth" },
            { id = 33763, name = "Lifebloom" },
            { id = 18562, name = "Swiftmend" },
            { id = 740, name = "Tranquility" },
            { id = 17116, name = "Nature's Swiftness" },
            { id = 33763, name = "Lifebloom" },
            { id = 50464, name = "Nourish" },
            { id = 21849, name = "Gift of the Wild" },
            { id = 1126, name = "Mark of the Wild" },
            { id = 467, name = "Thorns" },
            { id = 467, name = "Thorns" },
            
            -- Utility
            { id = 50769, name = "Revive" },
            { id = 20484, name = "Rebirth" },
            { id = 29166, name = "Innervate" },
            { id = 22812, name = "Barkskin" },
            { id = 16689, name = "Nature's Grasp" },
            { id = 339, name = "Entangling Roots" },
            { id = 33786, name = "Cyclone" },
            { id = 2637, name = "Hibernate" },
            { id = 8946, name = "Cure Poison" },
            { id = 2782, name = "Remove Curse" },
            { id = 2893, name = "Abolish Poison" },
            { id = 16857, name = "Faerie Fire (Feral)" },
            { id = 770, name = "Faerie Fire" },
            { id = 5225, name = "Track Humanoids" },
            { id = 62600, name = "Savage Defense" },
            { id = 52610, name = "Savage Roar" },
        },
    },
}

-- Get icon for a spell (dynamically)
function Adv2.GetSpellIcon(spellId)
    local name, rank, icon = GetSpellInfo(spellId)
    return icon
end

-- Override CoreSpells with our complete data
-- This makes the Abilities panel show all spells
function Adv2.InitCoreSpells()
    if not Adv2.Data then Adv2.Data = {} end

    Adv2.Data.CoreSpells = {}
    Adv2.Data.PickSpells = {}

    for classId, classData in pairs(Adv2.Data.AllSpells) do
        Adv2.Data.CoreSpells[classId] = {}
        Adv2.Data.PickSpells[classId] = {}
        local seen = {}

        for _, spell in ipairs(classData.spells or {}) do
            local icon = spell.icon
            if not icon then
                local _, _, spellIcon = GetSpellInfo(spell.id)
                icon = spellIcon or "Interface\\Icons\\INV_Misc_QuestionMark"
            end

            local entry = {
                id = spell.id,
                name = spell.name,
                icon = icon,
                level = spell.level or 1,
            }

            table.insert(Adv2.Data.CoreSpells[classId], entry)

            if not seen[spell.id] then
                seen[spell.id] = true
                table.insert(Adv2.Data.PickSpells[classId], entry)
            end
        end
    end

    print("|cff00ff00[Multiclass]|r CoreSpells initialized with " .. Adv2.CountAllSpells() .. " total spells")
end

function Adv2.Data.GetPickSpells(classId)
    if Adv2.Data.PickSpells and Adv2.Data.PickSpells[classId] then
        return Adv2.Data.PickSpells[classId]
    end
    return Adv2.Data.CoreSpells and Adv2.Data.CoreSpells[classId] or {}
end

function Adv2.Data.GetAllPickSpellsFlat()
    if Adv2.Data._allSpellsFlat then
        return Adv2.Data._allSpellsFlat
    end

    local seen = {}
    local list = {}
    for _, classId in ipairs(Adv2.ClassOrder or {}) do
        local spells = Adv2.Data.GetPickSpells(classId)
        for _, spell in ipairs(spells) do
            if not seen[spell.id] then
                seen[spell.id] = true
                table.insert(list, spell)
            end
        end
    end

    table.sort(list, function(a, b)
        return (a.name or ""):lower() < (b.name or ""):lower()
    end)

    Adv2.Data._allSpellsFlat = list
    return list
end

-- Count total spells
function Adv2.CountAllSpells()
    local total = 0
    if Adv2.Data and Adv2.Data.CoreSpells then
        for classId, spells in pairs(Adv2.Data.CoreSpells) do
            total = total + #spells
        end
    end
    return total
end

-- Auto-initialize when file loads
Adv2.InitCoreSpells()

print("|cff00ff00[Multiclass]|r AllSpells.lua loaded - Complete class ability database")
