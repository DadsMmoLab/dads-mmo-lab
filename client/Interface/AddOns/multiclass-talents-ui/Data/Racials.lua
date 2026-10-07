-- Adventurer2 Racials Data - Complete list of ALL racial abilities
Adv2 = Adv2 or {}
Adv2.Data = Adv2.Data or {}

-- All racials organized by race
-- Each racial has: spellId, name, icon, isActive (vs passive), description
Adv2.Data.Racials = {
    -- =========================================================================
    -- HUMAN (Race 1)
    -- =========================================================================
    [1] = {
        name = "Human",
        icon = "Interface\\Icons\\Achievement_Character_Human_Male",
        racials = {
            { id = 59752, name = "Every Man for Himself", icon = "Interface\\Icons\\Spell_Shadow_Charm", active = true, desc = "Removes all movement impairing effects and all effects which cause loss of control of your character." },
            { id = 20599, name = "Diplomacy", icon = "Interface\\Icons\\INV_Misc_Note_02", active = false, desc = "Reputation gains increased by 10%." },
            { id = 20864, name = "Mace Specialization", icon = "Interface\\Icons\\INV_Mace_01", active = false, desc = "Expertise with Maces and Two-Handed Maces increased by 3." },
            { id = 20597, name = "Sword Specialization", icon = "Interface\\Icons\\INV_Sword_27", active = false, desc = "Expertise with Swords and Two-Handed Swords increased by 3." },
            { id = 20598, name = "The Human Spirit", icon = "Interface\\Icons\\Spell_Holy_HolyGuidance", active = false, desc = "Spirit increased by 3%." },
            { id = 58985, name = "Perception", icon = "Interface\\Icons\\Spell_Nature_Invisibilty", active = false, desc = "Increases your Stealth detection." },
        }
    },
    -- =========================================================================
    -- ORC (Race 2)
    -- =========================================================================
    [2] = {
        name = "Orc",
        icon = "Interface\\Icons\\Achievement_Character_Orc_Male",
        racials = {
            { id = 33697, name = "Blood Fury", icon = "Interface\\Icons\\Racial_Orc_BerserkerStrength", active = true, desc = "Increases attack power and spell power for 15 sec." },
            { id = 20573, name = "Hardiness", icon = "Interface\\Icons\\Spell_Shadow_ShadowPact", active = false, desc = "Duration of Stun effects reduced by 15%." },
            { id = 20574, name = "Axe Specialization", icon = "Interface\\Icons\\INV_Axe_02", active = false, desc = "Expertise with Fist Weapons, Axes, and Two-Handed Axes increased by 5." },
            { id = 21563, name = "Command", icon = "Interface\\Icons\\Spell_Nature_UndyingStrength", active = false, desc = "Damage dealt by Death Knight, Hunter and Warlock pets increased by 5%." },
        }
    },
    -- =========================================================================
    -- DWARF (Race 3)
    -- =========================================================================
    [3] = {
        name = "Dwarf",
        icon = "Interface\\Icons\\Achievement_Character_Dwarf_Male",
        racials = {
            { id = 20594, name = "Stoneform", icon = "Interface\\Icons\\Spell_Shadow_UnholyStrength", active = true, desc = "Removes all poison, disease, and bleed effects and increases armor by 10% for 8 sec." },
            { id = 20595, name = "Gun Specialization", icon = "Interface\\Icons\\INV_Weapon_Rifle_01", active = false, desc = "Your chance to critically hit with Guns is increased by 1%." },
            { id = 20596, name = "Frost Resistance", icon = "Interface\\Icons\\Spell_Frost_WizardMark", active = false, desc = "Reduces the chance you will be hit by Frost spells by 2%." },
            { id = 2481, name = "Find Treasure", icon = "Interface\\Icons\\Racial_Dwarf_FindTreasure", active = true, desc = "Allows the dwarf to sense nearby treasure, making it appear on the minimap." },
            { id = 59224, name = "Mace Specialization", icon = "Interface\\Icons\\INV_Mace_01", active = false, desc = "Expertise with Maces and Two-Handed Maces increased by 5." },
        }
    },
    -- =========================================================================
    -- NIGHT ELF (Race 4)
    -- =========================================================================
    [4] = {
        name = "Night Elf",
        icon = "Interface\\Icons\\Achievement_Character_Nightelf_Male",
        racials = {
            { id = 58984, name = "Shadowmeld", icon = "Interface\\Icons\\Ability_Ambush", active = true, desc = "Activate to slip into the shadows, reducing the chance for enemies to detect your presence. Lasts until cancelled or upon moving." },
            { id = 20583, name = "Nature Resistance", icon = "Interface\\Icons\\Spell_Nature_ProtectionformNature", active = false, desc = "Reduces the chance you will be hit by Nature spells by 2%." },
            { id = 20585, name = "Wisp Spirit", icon = "Interface\\Icons\\Spell_Nature_WispSplode", active = false, desc = "Transform into a wisp upon death, increasing speed by 75%." },
            { id = 20582, name = "Quickness", icon = "Interface\\Icons\\Ability_Rogue_QuickRecovery", active = false, desc = "Reduces the chance that melee and ranged attackers will hit you by 2%." },
            { id = 21009, name = "Elusiveness", icon = "Interface\\Icons\\Ability_Rogue_FeignDeath", active = false, desc = "Reduces the chance enemies have to detect you while Shadowmelded or Stealthed." },
        }
    },
    -- =========================================================================
    -- UNDEAD (Race 5)
    -- =========================================================================
    [5] = {
        name = "Undead",
        icon = "Interface\\Icons\\Achievement_Character_Undead_Male",
        racials = {
            { id = 7744, name = "Will of the Forsaken", icon = "Interface\\Icons\\Spell_Shadow_RaiseDead", active = true, desc = "Removes any Charm, Fear and Sleep effect." },
            { id = 20577, name = "Cannibalize", icon = "Interface\\Icons\\Ability_Racial_Cannibalize", active = true, desc = "When activated, regenerates 7% of total health every 2 sec for 10 sec. Only works on Humanoid or Undead corpses within 5 yds." },
            { id = 20579, name = "Shadow Resistance", icon = "Interface\\Icons\\Spell_Shadow_AntiShadow", active = false, desc = "Reduces the chance you will be hit by Shadow spells by 2%." },
            { id = 5227, name = "Underwater Breathing", icon = "Interface\\Icons\\Spell_Shadow_DemonBreath", active = false, desc = "Underwater breath lasts 233% longer than normal." },
        }
    },
    -- =========================================================================
    -- TAUREN (Race 6)
    -- =========================================================================
    [6] = {
        name = "Tauren",
        icon = "Interface\\Icons\\Achievement_Character_Tauren_Male",
        racials = {
            { id = 20549, name = "War Stomp", icon = "Interface\\Icons\\Ability_WarStomp", active = true, desc = "Stuns up to 5 enemies within 8 yds for 2 sec." },
            { id = 20550, name = "Endurance", icon = "Interface\\Icons\\Spell_Nature_UnyeildingStamina", active = false, desc = "Base Health increased by 5%." },
            { id = 20551, name = "Nature Resistance", icon = "Interface\\Icons\\Spell_Nature_ProtectionformNature", active = false, desc = "Reduces the chance you will be hit by Nature spells by 2%." },
            { id = 20552, name = "Cultivation", icon = "Interface\\Icons\\Trade_Herbalism", active = false, desc = "Herbalism skill increased by 15." },
        }
    },
    -- =========================================================================
    -- GNOME (Race 7)
    -- =========================================================================
    [7] = {
        name = "Gnome",
        icon = "Interface\\Icons\\Achievement_Character_Gnome_Male",
        racials = {
            { id = 20589, name = "Escape Artist", icon = "Interface\\Icons\\Ability_Rogue_Trip", active = true, desc = "Escape the effects of any immobilization or movement speed reduction effect." },
            { id = 20592, name = "Arcane Resistance", icon = "Interface\\Icons\\Spell_Arcane_Blink", active = false, desc = "Reduces the chance you will be hit by Arcane spells by 2%." },
            { id = 20591, name = "Expansive Mind", icon = "Interface\\Icons\\INV_Enchant_EssenceEternalLarge", active = false, desc = "Intellect increased by 5%." },
            { id = 20593, name = "Engineering Specialization", icon = "Interface\\Icons\\Trade_Engineering", active = false, desc = "Engineering skill increased by 15." },
        }
    },
    -- =========================================================================
    -- TROLL (Race 8)
    -- =========================================================================
    [8] = {
        name = "Troll",
        icon = "Interface\\Icons\\Achievement_Character_Troll_Male",
        racials = {
            { id = 26297, name = "Berserking", icon = "Interface\\Icons\\Racial_Troll_Berserk", active = true, desc = "Increases your casting and attack speed by 20% for 10 sec." },
            { id = 20555, name = "Regeneration", icon = "Interface\\Icons\\Spell_Nature_Regeneration", active = false, desc = "Health regeneration rate increased by 10%. 10% of total Health regeneration may continue during combat." },
            { id = 20558, name = "Throwing Specialization", icon = "Interface\\Icons\\INV_ThrowingKnife_02", active = false, desc = "Your chance to critically hit with Throwing Weapons is increased by 1%." },
            { id = 20557, name = "Beast Slaying", icon = "Interface\\Icons\\Spell_Holy_PrayerOfHealing", active = false, desc = "Damage dealt versus Beasts increased by 5%." },
            { id = 26290, name = "Bow Specialization", icon = "Interface\\Icons\\INV_Weapon_Bow_07", active = false, desc = "Your chance to critically hit with Bows is increased by 1%." },
            { id = 58943, name = "Da Voodoo Shuffle", icon = "Interface\\Icons\\Ability_Creature_Poison_05", active = false, desc = "Reduces the duration of all movement impairing effects by 15%." },
        }
    },
    -- =========================================================================
    -- BLOOD ELF (Race 10)
    -- =========================================================================
    [10] = {
        name = "Blood Elf",
        icon = "Interface\\Icons\\Achievement_Character_BloodElf_Male",
        racials = {
            { id = 28730, name = "Arcane Torrent", icon = "Interface\\Icons\\Spell_Shadow_ManaFeed", active = true, desc = "Silence all enemies within 8 yards for 2 sec and restores resources." },
            { id = 822, name = "Magic Resistance", icon = "Interface\\Icons\\Spell_Arcane_ArcaneTorrent", active = false, desc = "Reduces the chance you will be hit by spells by 2%." },
            { id = 28877, name = "Arcane Affinity", icon = "Interface\\Icons\\INV_Enchant_ShardBrilliantSmall", active = false, desc = "Enchanting skill increased by 10." },
        }
    },
    -- =========================================================================
    -- DRAENEI (Race 11)
    -- =========================================================================
    [11] = {
        name = "Draenei",
        icon = "Interface\\Icons\\Achievement_Character_Draenei_Male",
        racials = {
            { id = 28880, name = "Gift of the Naaru", icon = "Interface\\Icons\\Spell_Holy_HolyProtection", active = true, desc = "Heals the target for 20% of the caster's total health over 15 sec." },
            { id = 6562, name = "Heroic Presence", icon = "Interface\\Icons\\INV_Helmet_21", active = false, desc = "Increases chance to hit with all spells and attacks by 1% for you and all party members within 30 yards." },
            { id = 59221, name = "Shadow Resistance", icon = "Interface\\Icons\\Spell_Shadow_AntiShadow", active = false, desc = "Reduces the chance you will be hit by Shadow spells by 2%." },
            { id = 28875, name = "Gemcutting", icon = "Interface\\Icons\\INV_Misc_Gem_01", active = false, desc = "Jewelcrafting skill increased by 5." },
        }
    },
}

-- Helper to get all racials as a flat list for display
function Adv2.Data.GetAllRacials()
    local all = {}
    for raceId, raceData in pairs(Adv2.Data.Racials) do
        for _, racial in ipairs(raceData.racials) do
            table.insert(all, {
                id = racial.id,
                name = racial.name,
                icon = racial.icon,
                active = racial.active,
                desc = racial.desc,
                raceName = raceData.name,
                raceId = raceId,
                raceIcon = raceData.icon,
            })
        end
    end
    return all
end
