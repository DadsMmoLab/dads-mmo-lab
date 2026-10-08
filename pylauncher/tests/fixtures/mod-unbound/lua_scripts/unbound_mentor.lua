--[[ ============================================================
  unbound_mentor.lua — Unbound Wrath Edition
  --------------------------------------------------------------
  Handles:
    • Mentor NPC gossip (class unlocks + individual spell purchase)
    • Multi-resource pool initialization on unlock and login
    • Weapon/armor proficiency (all skills) for every Unbound character
    • Persistent unlock state in unbound_character_unlocks (acore_characters)

  C++ side (mod-unbound/UnboundSystem.cpp) hooks
  Player::HasActivePowerType so rage/energy generation fires
  for any power type whose GetMaxPower() > 0.

  NPC entry 900001 = The Mentor (model = Ethereal Thief, displayid 19097).
  Spawn in-game via: .npc add 900001
============================================================ --]]

local MENTOR_ENTRY      = 900001
local MENTOR_STONE_ENTRY = 900100
local PAGE_SIZE          = 10   -- spells shown per gossip page

-- ── Talent points for sale ────────────────────────────────────────────────
-- Flat price, no scaling with level or with how many you already own.
local TALENT_POINT_COST    = 750000            -- 75g in copper
local TALENT_POINT_BUNDLES = { 1, 5, 10 }      -- purchase sizes offered
-- A bought point becomes a permanent bonus talent point: AddBonusTalent feeds
-- Player::CalculateTalentsPoints(), and the running total is saved in
-- characters.extraBonusTalentCount, so it survives relog, level-up and respec.
-- AzerothCore reads that column back as a uint8, so the total must stay <= 255.
local MAX_BONUS_TALENT_POINTS = 255

-- Per-player timestamp (os.time seconds) of last Mentor Stone use.
-- Used to enforce the 3-minute cooldown on the Lua side as a guard.
local STONE_LAST_USE = {}
local STONE_COOLDOWN_SEC = 180

-- AzerothCore power type constants
local POWER_MANA   = 0
local POWER_RAGE   = 1
local POWER_ENERGY = 3

local RAGE_MAX    = 1000
local ENERGY_MAX  = 100

local RAGE_NATIVE   = { [1]=true, [11]=true }
local ENERGY_NATIVE = { [4]=true, [11]=true }
local MANA_NATIVE   = { [2]=true, [3]=true, [5]=true, [7]=true, [8]=true, [9]=true, [11]=true }

local CLASS_NAMES = {
    [1]="Warrior", [2]="Paladin", [3]="Hunter",  [4]="Rogue",
    [5]="Priest",  [7]="Shaman",  [8]="Mage",    [9]="Warlock", [11]="Druid"
}

-- ── Weapon and armor skill IDs (from SharedDefines.h) ─────────────────────
-- These skills govern item equip eligibility (CanEquipItem checks GetSkillValue).
-- Granting all of them makes every Unbound character weapon/armor-agnostic.
local WEAPON_SKILLS = {
    43,   -- Swords
    44,   -- Axes
    45,   -- Bows
    46,   -- Guns
    54,   -- Maces
    55,   -- Two-Handed Swords
    118,  -- Dual Wield
    136,  -- Staves
    160,  -- Two-Handed Maces
    162,  -- Unarmed
    172,  -- Two-Handed Axes
    173,  -- Daggers
    176,  -- Thrown
    226,  -- Crossbows
    228,  -- Wands
    229,  -- Polearms
    433,  -- Shield
    473,  -- Fist Weapons
}
local ARMOR_SKILLS = {
    293,  -- Plate Mail
    413,  -- Mail
    414,  -- Leather
    415,  -- Cloth
}

-- ── Rank prerequisite map ─────────────────────────────────────────────────
-- PREREQ_MAP[classId][spellId] = prereqSpellId (0 if none)
-- Built at script-load from GetSpellInfo data; avoids any DB dependency.
local PREREQ_MAP = {}

-- GetSpellInfo returns a SpellInfo object.  Its name method is :GetName(locale)
-- which returns a plain string.  Rank info isn't on SpellInfo, so we order
-- same-named spells by catalog req_level to determine the rank chain.
local function BuildPrereqMap()
    for classId = 1, 11 do
        if CLASS_NAMES[classId] then
            PREREQ_MAP[classId] = {}
            -- Include req_level so we can sort chains without rank text
            local Q = WorldDBQuery(string.format(
                "SELECT spell_id, req_level FROM unbound_class_catalog " ..
                "WHERE class_id = %d ORDER BY req_level, spell_id", classId))
            if Q then
                local byName = {}  -- spellName → [{reqLevel, id}]
                repeat
                    local spellId   = Q:GetUInt32(0)
                    local reqLevel  = Q:GetUInt32(1)
                    local info      = GetSpellInfo(spellId)
                    if info then
                        local ok, name = pcall(function() return info:GetName(0) end)
                        if ok and name and name ~= "" then
                            if not byName[name] then byName[name] = {} end
                            table.insert(byName[name], { lv=reqLevel, id=spellId })
                        end
                    end
                until not Q:NextRow()
                for _, group in pairs(byName) do
                    if #group > 1 then
                        table.sort(group, function(a, b)
                            return a.lv < b.lv or (a.lv == b.lv and a.id < b.id)
                        end)
                        for i = 2, #group do
                            PREREQ_MAP[classId][group[i].id] = group[i-1].id
                        end
                    end
                end
            end
        end
    end
    print("[UNBOUND] Prereq map built.")
end

pcall(BuildPrereqMap)

-- ============================================================
-- Helpers
-- ============================================================

local function FormatCopper(copper)
    if copper == 0 then return "Free" end
    local g = math.floor(copper / 10000)
    local s = math.floor((copper % 10000) / 100)
    local c = copper % 100
    if g > 0 then
        return s > 0 and string.format("%dg %ds", g, s) or string.format("%dg", g)
    end
    if s > 0 then return string.format("%ds", s) end
    return string.format("%dc", c)
end

local function GetSpellDisplayName(spellId)
    local info = GetSpellInfo(spellId)
    if not info then return "Spell #" .. spellId end
    local ok, name = pcall(function() return info:GetName(0) end)
    if ok and name and name ~= "" then return name end
    return "Spell #" .. spellId
end

-- A handful of spell IDs (e.g. Parry=3127, Plate Mail=750, Mail=8737) appear
-- in unbound_class_catalog under MULTIPLE class_ids. Gossip intid only carries
-- one number, so the browse/buy path encodes both class and spell into it —
-- otherwise a bare "WHERE spell_id = ... LIMIT 1" recovery would resolve to
-- whichever class_id happens to sort first, not the class the player actually
-- browsed from, and reject the purchase with "You have not unlocked that class."
-- Max spell_id in the catalog is well under 1,000,000 and class_id <= 11, so
-- this never collides and stays inside Lua's 32-bit intid range.
local SPELL_ID_MULT = 1000000
local function EncodeClassSpell(classId, spellId)
    return classId * SPELL_ID_MULT + spellId
end
local function DecodeClassSpell(intid)
    return math.floor(intid / SPELL_ID_MULT), intid % SPELL_ID_MULT
end

local function GetUnlockedClasses(player)
    local unlocked = {}
    local Q = CharDBQuery(string.format(
        "SELECT class_id FROM unbound_character_unlocks WHERE char_guid = %d",
        player:GetGUIDLow()))
    if Q then
        repeat unlocked[Q:GetUInt32(0)] = true until not Q:NextRow()
    end
    return unlocked
end

local function GetUnlockedCount(player)
    local Q = CharDBQuery(string.format(
        "SELECT COUNT(*) FROM unbound_character_unlocks WHERE char_guid = %d",
        player:GetGUIDLow()))
    return Q and Q:GetUInt32(0) or 0
end

local function GetMilestone(index)
    local capped = math.min(5, index)
    local Q = WorldDBQuery(string.format(
        "SELECT required_level, unlock_cost_copper FROM unbound_milestones WHERE milestone_index = %d",
        capped))
    if not Q then return nil end
    return { level = Q:GetUInt32(0), cost = Q:GetUInt32(1) }
end

-- ============================================================
-- Power pools
-- ============================================================

local function ApplyUnboundPools(player)
    local native  = player:GetClass()
    local level   = player:GetLevel()
    local unlocked = GetUnlockedClasses(player)

    local needRage, needEnergy, needMana = false, false, false
    for classId in pairs(unlocked) do
        if classId == 1  then needRage   = true end
        if classId == 4  then needEnergy = true end
        if classId ~= 1 and classId ~= 4 then needMana = true end
    end
    if RAGE_NATIVE[native]   then needRage   = false end
    if ENERGY_NATIVE[native] then needEnergy = false end
    if MANA_NATIVE[native]   then needMana   = false end

    if needRage and player:GetMaxPower(POWER_RAGE) == 0 then
        player:SetMaxPower(POWER_RAGE, RAGE_MAX)
    end
    if needEnergy and player:GetMaxPower(POWER_ENERGY) == 0 then
        player:SetMaxPower(POWER_ENERGY, ENERGY_MAX)
        player:SetPower(ENERGY_MAX, POWER_ENERGY)
    end
    if needMana then
        -- 80% of mage base mana at the player's level.
        -- Warriors (and other non-mana classes) have basemana=0 in player_class_stats,
        -- so we look up the mage value explicitly rather than trusting GetMaxPower.
        -- The == 0 guard is intentionally removed: server-side UpdateMaxPower for
        -- non-mana classes always recalculates to 0, which would silently undo the set.
        local mQ = WorldDBQuery(string.format(
            "SELECT basemana FROM player_class_stats WHERE class = 8 AND level = %d", level))
        local pool = mQ and math.floor(mQ:GetUInt32(0) * 0.8) or (level * 30)
        if pool < 100 then pool = 100 end
        player:SetMaxPower(POWER_MANA, pool)
        player:SetPower(pool, POWER_MANA)

        -- AzerothCore computes percentage-based spell costs (ManaCostPercentage —
        -- e.g. Arcane Intellect, Frost Armor) as a % of GetCreateMana(), which reads
        -- UNIT_FIELD_BASE_MANA — the player's NATIVE class's base mana (0 for
        -- Rogue/Warrior/Hunter/etc). That makes those spells cost 0 mana for
        -- cross-class casters, so mana never visibly depletes. SetCreateMana() isn't
        -- Lua-exposed, so set the raw field directly: UNIT_FIELD_BASE_MANA =
        -- OBJECT_END(6) + 0x72 = 120. SetCreateMana() is only called from the
        -- level-change paths GiveLevel/InitStatsForLevel, both of which complete
        -- before ApplyUnboundPools runs (1s after login, 200ms after level-up), so
        -- this sticks until the next level change re-applies it.
        player:SetUInt32Value(120, pool)
    end
end

-- ============================================================
-- Skill grants
-- ============================================================

-- Class-specific skill tabs (Arms/Fury/Protection for warrior, etc.)
-- so the WotLK spellbook renders the correct ability tabs.
local function ApplyUnboundSkills(player, classId)
    local mask = math.floor(2 ^ (classId - 1))
    local Q = WorldDBQuery(string.format(
        "SELECT skill FROM playercreateinfo_skills WHERE classMask = %d", mask))
    if not Q then return end
    local maxSkill = math.max(1, player:GetLevel() * 5)
    repeat
        local skillId = Q:GetUInt32(0)
        if player:GetSkillValue(skillId) == 0 then
            player:SetSkill(skillId, 1, 1, maxSkill)
        end
    until not Q:NextRow()
end

local function ApplyAllUnboundSkills(player)
    for classId in pairs(GetUnlockedClasses(player)) do
        ApplyUnboundSkills(player, classId)
    end
end

-- Universal weapon + armor proficiency for all Unbound characters.
-- SetSkill grants the skill entry so the server-side equip check passes
-- (CanEquipItem checks GetSkillValue(itemSkill) > 0). The client-side
-- "weapon shows as equippable" half is handled separately by the C++
-- OnPlayerLogin hook in UnboundSystem.cpp (AddWeaponProficiency + SendProficiency),
-- which bypasses learnSkillRewardedSpells()'s ClassMask filter directly —
-- no custom proficiency spells needed.
--
-- Pure SetSkill approach — no CharDBExecute.
-- With the skillraceclassinfo_dbc fix (06_universal_skill_access.sql), _LoadSkills
-- no longer strips cross-class skills (ClassMask=0 entries pass validation).
-- SetSkill marks skill SKILL_NEW → _SaveSkills INSERTs on logout → persists.
-- Using CharDBExecute alongside SetSkill caused duplicate-key aborts that
-- reverted the entire character save transaction.
local function ApplyUnboundWeaponArmorSkills(player)
    local maxSkill = math.max(1, player:GetLevel() * 5)
    for _, skillId in ipairs(WEAPON_SKILLS) do
        if player:GetSkillValue(skillId) == 0 then
            player:SetSkill(skillId, 1, 1, maxSkill)
        end
    end
    for _, skillId in ipairs(ARMOR_SKILLS) do
        if player:GetSkillValue(skillId) == 0 then
            player:SetSkill(skillId, 1, 1, maxSkill)
        end
    end
    -- Dual Wield off-hand spell
    if not player:HasSpell(674) then
        player:LearnSpell(674)
    end
end

-- ============================================================
-- Creation gift spells
-- ============================================================
-- Sourced from playercreateinfo_spell_custom (same table AzerothCore
-- reads for LearnCustomSpells at character creation).

local function GrantClassGiftSpells(player, classId)
    local mask = math.floor(2 ^ (classId - 1))
    local Q = WorldDBQuery(string.format(
        "SELECT Spell FROM playercreateinfo_spell_custom WHERE classmask = %d AND racemask = 0",
        mask))
    if not Q then return end
    repeat
        local spellId = Q:GetUInt32(0)
        if not player:HasSpell(spellId) then player:LearnSpell(spellId) end
    until not Q:NextRow()
end

local function GrantAllClassGiftSpells(player)
    for classId in pairs(GetUnlockedClasses(player)) do
        GrantClassGiftSpells(player, classId)
    end
end

-- ============================================================
-- Creation gift items
-- ============================================================
-- Physical reagent items required to cast certain abilities.
-- Shaman needs the four totem items in inventory to cast totem spells.
-- item 5175=Earth Totem  5176=Fire Totem  5177=Water Totem  5178=Air Totem
local CLASS_GIFT_ITEMS = {
    [7] = { 5175, 5176, 5177, 5178 },
}

local function GrantClassGiftItems(player, classId)
    local items = CLASS_GIFT_ITEMS[classId]
    if not items then return end
    for _, itemId in ipairs(items) do
        if player:GetItemCount(itemId) == 0 then
            player:AddItem(itemId, 1)
        end
    end
end

local function GrantAllClassGiftItems(player)
    for classId in pairs(GetUnlockedClasses(player)) do
        GrantClassGiftItems(player, classId)
    end
end

-- ============================================================
-- Individual spell purchase helpers
-- ============================================================

-- Returns an ordered list of {id, cost} for spells the player can buy
-- right now: level met, prereq met (or none), not already known.
local function GetBuyableSpells(player, classId)
    local level = player:GetLevel()
    local Q = WorldDBQuery(string.format(
        "SELECT spell_id, gold_cost_copper FROM unbound_class_catalog " ..
        "WHERE class_id = %d AND req_level <= %d ORDER BY req_level, spell_id",
        classId, level))
    if not Q then return {} end

    local classPrereqs = PREREQ_MAP[classId] or {}
    local list = {}
    repeat
        local spellId = Q:GetUInt32(0)
        local cost    = Q:GetUInt32(1)
        if not player:HasSpell(spellId) then
            local prereq = classPrereqs[spellId] or 0
            if prereq == 0 or player:HasSpell(prereq) then
                table.insert(list, { id=spellId, cost=cost })
            end
        end
    until not Q:NextRow()
    return list
end

-- Build (or rebuild) the browse gossip page for a class.
local function ShowBrowsePage(player, creature, classId, page)
    player:GossipClearMenu()

    local list = GetBuyableSpells(player, classId)
    local total = #list
    local startIdx = page * PAGE_SIZE + 1
    local endIdx   = math.min(startIdx + PAGE_SIZE - 1, total)

    if total == 0 then
        local nextQ = WorldDBQuery(string.format(
            "SELECT MIN(req_level) FROM unbound_class_catalog " ..
            "WHERE class_id = %d AND req_level > %d", classId, player:GetLevel()))
        local nextLvl = nextQ and nextQ:GetUInt32(0) or 0
        if nextLvl > 0 then
            player:GossipMenuAddItem(0, string.format(
                "No %s abilities available yet — come back at level %d.",
                CLASS_NAMES[classId], nextLvl), 0, 99, false)
        else
            player:GossipMenuAddItem(0,
                "You already know all available " .. CLASS_NAMES[classId] .. " abilities.",
                0, 99, false)
        end
        player:GossipMenuAddItem(0, "← Back", 99, 0, false)
        player:GossipSendMenu(100, creature)
        return
    end

    -- Page header (non-clickable)
    player:GossipMenuAddItem(0, string.format(
        "── %s abilities (page %d/%d) ──",
        CLASS_NAMES[classId], page+1, math.ceil(total/PAGE_SIZE)),
        0, 99, false)

    -- Buy-all shortcut: sender=27, intid=classId
    if page == 0 then
        local totalCost = 0
        for _, sp in ipairs(list) do totalCost = totalCost + sp.cost end
        player:GossipMenuAddItem(0, string.format(
            "|cffffd700* Buy ALL available abilities (%s) *|r", FormatCopper(totalCost)),
            27, classId, false)
    end

    -- Individual spell items: sender=25, intid=EncodeClassSpell(classId, spellId)
    for i = startIdx, endIdx do
        local sp = list[i]
        local label = string.format("%s  [%s]",
            GetSpellDisplayName(sp.id), FormatCopper(sp.cost))
        player:GossipMenuAddItem(0, label, 25, EncodeClassSpell(classId, sp.id), false)
    end

    -- Pagination: sender=24, intid encodes (classId*1000 + page)
    if page > 0 then
        player:GossipMenuAddItem(0, "← Prev page", 24, classId*1000+(page-1), false)
    end
    if endIdx < total then
        player:GossipMenuAddItem(0, "Next page →", 24, classId*1000+(page+1), false)
    end

    player:GossipMenuAddItem(0, "← Back to menu", 99, 0, false)
    player:GossipSendMenu(100, creature)
end

-- ============================================================
-- Talent point purchase
-- ============================================================

local function TalentPointMenuLabel(player)
    return string.format("Buy talent points  [%s each]  (%d unspent)",
        FormatCopper(TALENT_POINT_COST), player:GetFreeTalentPoints())
end

local function ShowTalentPointMenu(player, creature)
    player:GossipClearMenu()

    local purchased = player:GetBonusTalentCount()
    player:GossipMenuAddItem(0, string.format(
        "── %d unspent talent point(s), %d purchased ──",
        player:GetFreeTalentPoints(), purchased), 0, 99, false)

    local offered = 0
    for _, amount in ipairs(TALENT_POINT_BUNDLES) do
        if purchased + amount <= MAX_BONUS_TALENT_POINTS then
            player:GossipMenuAddItem(0, string.format(
                "Buy %d talent point%s  [%s]",
                amount, amount == 1 and "" or "s",
                FormatCopper(TALENT_POINT_COST * amount)), 31, amount, false)
            offered = offered + 1
        end
    end
    if offered == 0 then
        player:GossipMenuAddItem(0,
            "You have bought every talent point I can grant.", 0, 99, false)
    end

    player:GossipMenuAddItem(0, "← Back", 32, 0, false)
    player:GossipSendMenu(100, creature)
end

-- ============================================================
-- Gossip: Hello
-- ============================================================

local function OnGossipHello(event, player, creature)
    player:GossipClearMenu()

    local unlockedCnt = GetUnlockedCount(player)
    local unlocked    = GetUnlockedClasses(player)
    local native      = player:GetClass()

    if unlockedCnt == 0 then
        local ms = GetMilestone(1)
        if ms and player:GetLevel() >= ms.level then
            player:GossipMenuAddItem(0, "I wish to walk the Unbound path.", 1, 0, false)
        else
            local lvl = ms and ms.level or 5
            player:GossipMenuAddItem(0, string.format(
                "(Reach level %d to begin the Unbound path.)", lvl), 0, 99, false)
        end
        player:GossipMenuAddItem(0, TalentPointMenuLabel(player), 30, 0, false)
        player:GossipMenuAddItem(0, "Farewell.", 99, 0, false)
        player:GossipSendMenu(100, creature)
        return
    end

    local nextMs = GetMilestone(unlockedCnt + 1)
    if nextMs then
        if player:GetLevel() >= nextMs.level then
            player:GossipMenuAddItem(0, string.format(
                "Unlock another class  [%s | Requires level %d]",
                FormatCopper(nextMs.cost), nextMs.level), 1, 0, false)
        else
            player:GossipMenuAddItem(0, string.format(
                "(Next unlock: level %d, %s)", nextMs.level, FormatCopper(nextMs.cost)),
                0, 99, false)
        end
    end

    for classId in pairs(unlocked) do
        player:GossipMenuAddItem(0,
            "Browse " .. CLASS_NAMES[classId] .. " abilities", 2, classId, false)
    end

    player:GossipMenuAddItem(0, TalentPointMenuLabel(player), 30, 0, false)
    player:GossipMenuAddItem(0, "Farewell.", 99, 0, false)
    player:GossipSendMenu(100, creature)
end

-- ============================================================
-- Gossip: Select
-- sender=1   → show class picker for next unlock
-- sender=10  → execute class unlock (classId = intid)
-- sender=2   → browse spells for class (classId = intid, page 0)
-- sender=24  → paginate (intid = classId*1000 + page)
-- sender=25  → buy individual spell directly, then refresh Browse (intid = encoded classId+spellId)
-- sender=27  → buy every currently-available spell for the class (intid = classId)
-- sender=30  → open the talent point shop
-- sender=31  → buy talent points (intid = how many)
-- sender=32  → back to the main menu
-- sender=99  → close
-- ============================================================

local function OnGossipSelect(event, player, creature, sender, intid, code, menuId)
    player:GossipClearMenu()

    if sender == 99 or intid == 99 then
        player:GossipComplete()
        return
    end

    local native      = player:GetClass()
    local unlockedCnt = GetUnlockedCount(player)
    local unlocked    = GetUnlockedClasses(player)

    -- ---- sender=1: show class picker for unlock ----
    if sender == 1 then
        local nextMs = GetMilestone(unlockedCnt + 1)
        if not nextMs then
            player:SendBroadcastMessage("Error: milestone data missing.")
            player:GossipComplete()
            return
        end
        if player:GetLevel() < nextMs.level then
            player:SendBroadcastMessage(string.format(
                "You must reach level %d before unlocking another class.", nextMs.level))
            player:GossipComplete()
            return
        end
        local costStr = FormatCopper(nextMs.cost)
        for classId, name in pairs(CLASS_NAMES) do
            if classId ~= native and not unlocked[classId] then
                player:GossipMenuAddItem(0,
                    string.format("%s  [%s]", name, costStr), 10, classId, false)
            end
        end
        player:GossipMenuAddItem(0, "Never mind.", 99, 0, false)
        player:GossipSendMenu(100, creature)
        return
    end

    -- ---- sender=10: execute class unlock ----
    if sender == 10 then
        local classId = intid
        local nextMs  = GetMilestone(unlockedCnt + 1)
        if not nextMs then
            player:SendBroadcastMessage("Error: milestone data missing.")
            player:GossipComplete()
            return
        end
        if player:GetLevel() < nextMs.level then
            player:SendBroadcastMessage(string.format(
                "You must be level %d to unlock another class.", nextMs.level))
            player:GossipComplete()
            return
        end
        if unlocked[classId] then
            player:SendBroadcastMessage("You have already unlocked that class.")
            player:GossipComplete()
            return
        end
        if not CLASS_NAMES[classId] then
            player:SendBroadcastMessage("Unknown class.")
            player:GossipComplete()
            return
        end
        if nextMs.cost > 0 and player:GetCoinage() < nextMs.cost then
            player:SendBroadcastMessage(string.format(
                "You need %s to unlock this class.", FormatCopper(nextMs.cost)))
            player:GossipComplete()
            return
        end

        if nextMs.cost > 0 then player:ModifyMoney(-nextMs.cost) end

        CharDBExecute(string.format(
            "INSERT IGNORE INTO unbound_character_unlocks (char_guid, class_id, unlocked_at_level) " ..
            "VALUES (%d, %d, %d)",
            player:GetGUIDLow(), classId, player:GetLevel()))

        ApplyUnboundPools(player)
        ApplyUnboundSkills(player, classId)
        ApplyUnboundWeaponArmorSkills(player)
        GrantClassGiftSpells(player, classId)
        GrantClassGiftItems(player, classId)

        player:SendBroadcastMessage(string.format(
            "|cff00ff00The path of the %s is now open to you!|r " ..
            "Relog once to see the ability tabs in your spellbook.",
            CLASS_NAMES[classId]))
        player:GossipComplete()
        return
    end

    -- ---- sender=2: open browse for class (page 0) ----
    if sender == 2 then
        local classId = intid
        if not unlocked[classId] then
            player:GossipComplete()
            return
        end
        ShowBrowsePage(player, creature, classId, 0)
        return
    end

    -- ---- sender=24: paginate ----
    if sender == 24 then
        local classId = math.floor(intid / 1000)
        local page    = intid % 1000
        if not unlocked[classId] then
            player:GossipComplete()
            return
        end
        ShowBrowsePage(player, creature, classId, page)
        return
    end

    -- ---- sender=25: buy individual spell, then refresh the Browse page ----
    if sender == 25 then
        local classId, spellId = DecodeClassSpell(intid)
        local Q = WorldDBQuery(string.format(
            "SELECT gold_cost_copper FROM unbound_class_catalog WHERE class_id = %d AND spell_id = %d",
            classId, spellId))
        if not Q then
            player:SendBroadcastMessage("Spell not found in catalog.")
            player:GossipComplete()
            return
        end
        local cost = Q:GetUInt32(0)

        if not unlocked[classId] then
            player:SendBroadcastMessage("You have not unlocked that class.")
            player:GossipComplete()
            return
        end
        if player:HasSpell(spellId) then
            player:SendBroadcastMessage("You already know that ability.")
            ShowBrowsePage(player, creature, classId, 0)
            return
        end
        local prereq = PREREQ_MAP[classId] and PREREQ_MAP[classId][spellId] or 0
        if prereq > 0 and not player:HasSpell(prereq) then
            player:SendBroadcastMessage(string.format(
                "You must learn %s first.", GetSpellDisplayName(prereq)))
            ShowBrowsePage(player, creature, classId, 0)
            return
        end
        if player:GetCoinage() < cost then
            player:SendBroadcastMessage(string.format(
                "You need %s to buy that ability.", FormatCopper(cost)))
            ShowBrowsePage(player, creature, classId, 0)
            return
        end

        player:ModifyMoney(-cost)
        player:LearnSpell(spellId)
        player:SendBroadcastMessage(string.format(
            "|cff00ff00Learned %s!|r", GetSpellDisplayName(spellId)))
        ShowBrowsePage(player, creature, classId, 0)
        return
    end

    -- ---- sender=27: buy every available spell for the class ----
    if sender == 27 then
        local classId = intid
        if not unlocked[classId] then
            player:GossipComplete()
            return
        end

        if #GetBuyableSpells(player, classId) == 0 then
            player:SendBroadcastMessage("You already know everything currently available.")
            ShowBrowsePage(player, creature, classId, 0)
            return
        end

        -- Re-query each pass: buying a spell can satisfy another's prereq,
        -- which only shows up once GetBuyableSpells re-checks HasSpell().
        local learned = 0
        while true do
            local list = GetBuyableSpells(player, classId)
            if #list == 0 then break end
            local boughtAny = false
            for _, sp in ipairs(list) do
                if player:GetCoinage() >= sp.cost then
                    player:ModifyMoney(-sp.cost)
                    player:LearnSpell(sp.id)
                    learned = learned + 1
                    boughtAny = true
                end
            end
            if not boughtAny then break end
        end

        if learned == 0 then
            player:SendBroadcastMessage("You can't afford any available abilities right now.")
        else
            player:SendBroadcastMessage(string.format(
                "|cff00ff00Learned %d %s abilities!|r", learned, CLASS_NAMES[classId]))
        end
        ShowBrowsePage(player, creature, classId, 0)
        return
    end

    -- ---- sender=30: open the talent point shop ----
    if sender == 30 then
        ShowTalentPointMenu(player, creature)
        return
    end

    -- ---- sender=31: buy talent points ----
    if sender == 31 then
        local amount = 0
        for _, bundle in ipairs(TALENT_POINT_BUNDLES) do
            if intid == bundle then amount = bundle end
        end
        if amount == 0 then
            player:GossipComplete()
            return
        end

        if player:GetBonusTalentCount() + amount > MAX_BONUS_TALENT_POINTS then
            player:SendBroadcastMessage(
                "You have bought every talent point I can grant.")
            ShowTalentPointMenu(player, creature)
            return
        end

        local cost = TALENT_POINT_COST * amount
        if player:GetCoinage() < cost then
            player:SendBroadcastMessage(string.format(
                "You need %s for %d talent point%s.",
                FormatCopper(cost), amount, amount == 1 and "" or "s"))
            ShowTalentPointMenu(player, creature)
            return
        end

        player:ModifyMoney(-cost)
        -- AddBonusTalent makes the point permanent (saved in
        -- characters.extraBonusTalentCount, re-added by CalculateTalentsPoints
        -- on every login/level-up/respec); SetFreeTalentPoints makes it
        -- spendable right now, without waiting for that recalculation.
        player:AddBonusTalent(amount)
        player:SetFreeTalentPoints(player:GetFreeTalentPoints() + amount)
        player:SendBroadcastMessage(string.format(
            "|cff00ff00Gained %d talent point%s for %s.|r",
            amount, amount == 1 and "" or "s", FormatCopper(cost)))
        ShowTalentPointMenu(player, creature)
        return
    end

    -- ---- sender=32: back to the main menu ----
    if sender == 32 then
        OnGossipHello(event, player, creature)
        return
    end

    player:GossipComplete()
end

-- ============================================================
-- Mentor Stone item use handler
-- ============================================================
-- Fires when a player right-clicks the Unbounding Mentor Stone (entry 900100).
-- Summons the Mentor NPC 3 yards in front of the player for 3 minutes.
--
-- Uses ITEM_EVENT_ON_USE (event=2), which fires before the item spell is cast.
-- Returning false lets AzerothCore proceed with CastItemUseSpell, which applies
-- the 3-minute cooldown from item_template.spellcooldown_1.
-- Returning true (or nil) tells Eluna "handled it" — skips the spell cast.
-- The Lua-side STONE_LAST_USE table guards against the server-restart case
-- where spellcooldown_1 state is lost but the Lua table has been reset too.
RegisterItemEvent(MENTOR_STONE_ENTRY, 2, function(event, player, item, target)
    local guid = player:GetGUIDLow()
    local now  = os.time()
    local last = STONE_LAST_USE[guid] or 0

    if (now - last) < STONE_COOLDOWN_SEC then
        local remaining = STONE_COOLDOWN_SEC - (now - last)
        player:SendBroadcastMessage(string.format(
            "|cffff4444Unbounding Mentor Stone is on cooldown (%ds remaining).|r", remaining))
        return true  -- Eluna "handled" this use; skip the spell cast
    end

    STONE_LAST_USE[guid] = now

    -- Summon 3 yards ahead of the player, facing back toward the player.
    local angle = player:GetO()
    local x = player:GetX() + math.cos(angle) * 3
    local y = player:GetY() + math.sin(angle) * 3
    local z = player:GetZ()
    local face = angle + math.pi  -- face the player

    -- TEMPSUMMON_TIMED_DESPAWN (3): despawn after 180 000 ms regardless of state.
    local mentor = player:SpawnCreature(MENTOR_ENTRY, x, y, z, face, 3, 180000)
    if not mentor then
        player:SendBroadcastMessage(
            "|cffff4444Could not summon the Mentor here. Try again in the open world.|r")
        STONE_LAST_USE[guid] = 0  -- refund cooldown on failure
        return true
    end

    player:SendBroadcastMessage(
        "|cff00ff00Your Unbounding Mentor has arrived. (3 min)|r")
    -- Return true: cancel the item's spell cast. spellid_1 only exists so the
    -- 3.3.5a client recognizes this as a usable item and sends CMSG_USE_ITEM —
    -- a custom server-only spell ID (900200, absent from the client's Spell.dbc)
    -- left the client unable to resolve the item, so right-click did nothing.
    -- The Lua-side STONE_LAST_USE cooldown (180s, matching spellcooldown_1)
    -- fully replaces the need for the real spell cast to apply a cooldown.
    return true
end)

-- ============================================================
-- Event registration
-- ============================================================

RegisterCreatureGossipEvent(MENTOR_ENTRY, 1, OnGossipHello)
RegisterCreatureGossipEvent(MENTOR_ENTRY, 2, OnGossipSelect)

-- On level-up: re-apply pools so the mana pool grows with the player's level.
-- The C++ OnAfterUpdateMaxPower hook (UnboundSystem.cpp) preserves the pool across
-- stat recalculations, but it locks in the OLD value — so we must re-calculate
-- after each level change.  Short 200 ms delay lets GiveLevel() finish its own
-- UpdateAllStats() pass before we write the new value.
RegisterPlayerEvent(13, function(event, player, oldLevel)
    local Q = CharDBQuery(string.format(
        "SELECT 1 FROM unbound_character_unlocks WHERE char_guid = %d LIMIT 1",
        player:GetGUIDLow()))
    if Q then
        player:RegisterEvent(function()
            pcall(function() ApplyUnboundPools(player) end)
        end, 200, 1)
    end
end)

-- On login: restore pools, skills, weapon/armor proficiency, gift spells,
-- and ensure the player has their Mentor Stone.
-- Delayed 1s: calling SetMaxPower during PLAYER_EVENT_ON_LOGIN crashes
-- AzerothCore before the character is fully in-world.
RegisterPlayerEvent(3, function(event, player)
    -- Give the Mentor Stone to any character that doesn't have it.
    -- Runs unconditionally so existing characters and anyone who deleted
    -- theirs get it back automatically.
    if player:GetItemCount(MENTOR_STONE_ENTRY) == 0 then
        player:AddItem(MENTOR_STONE_ENTRY, 1)
    end

    local Q = CharDBQuery(string.format(
        "SELECT 1 FROM unbound_character_unlocks WHERE char_guid = %d LIMIT 1",
        player:GetGUIDLow()))
    if Q then
        -- The `player` userdata captured directly in this closure goes stale by the
        -- time the 1s timer fires (Eluna invalidates it during the login->world
        -- transition: "pointer to nonexisting (invalidated) object"). Capture the
        -- GUID instead and re-fetch a live Player reference inside the callback.
        local guid = player:GetGUID()
        local guidLow = player:GetGUIDLow()
        player:RegisterEvent(function()
            local livePlayer = GetPlayerByGUID(guid)
            if not livePlayer or not livePlayer:IsInWorld() then
                return
            end
            local ok, err = pcall(function()
                ApplyUnboundPools(livePlayer)
                ApplyAllUnboundSkills(livePlayer)
                ApplyUnboundWeaponArmorSkills(livePlayer)
                GrantAllClassGiftSpells(livePlayer)
                GrantAllClassGiftItems(livePlayer)
            end)
            if not ok then
                print(string.format("[UNBOUND] OnLogin post-login setup ERROR for guidLow=%d: %s",
                    guidLow, tostring(err)))
            end
        end, 1000, 1)
    end
end)
