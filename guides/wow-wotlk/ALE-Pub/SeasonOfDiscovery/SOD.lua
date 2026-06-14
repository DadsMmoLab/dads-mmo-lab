-- Season of Discovery Buff
-- ALE (AzerothCore Lua Engine) version
-- Forked from: https://github.com/notepadguyOfficial/acore_sod
-- Updated for ALE by: Dad's MMO Lab

-- ── Double-load guard ────────────────────────────────────────────────────────
-- Prevents duplicate event registration on .reload ale
if _G.SoDBuffLoaded then return end
_G.SoDBuffLoaded = true

-- ── Config ───────────────────────────────────────────────────────────────────
local CONFIG = {
    -- Set to false to disable the script entirely without removing it
    ENABLED = true,

    -- Maps maximum level (inclusive) → spell ID for that phase tier.
    -- Tiers are evaluated in order; the first range the player's level
    -- falls within is used. Players at or above MAX_LEVEL get no buff.
    MAX_LEVEL = 80,

    -- Spell IDs (custom spells — requires matching DB entries):
    --   80865 = +50%   80866 = +100%   80867 = +150%
    --   80868 = +200%  80869 = +250%   80870 = +300%
    TIERS = {
        { maxLevel = 10, spellId = 80870 }, -- levels  1-10 → +300%
        { maxLevel = 20, spellId = 80869 }, -- levels 11-20 → +250%
        { maxLevel = 40, spellId = 80868 }, -- levels 21-40 → +200%
        { maxLevel = 60, spellId = 80867 }, -- levels 41-60 → +150%
        { maxLevel = 70, spellId = 80866 }, -- levels 61-70 → +100%
        { maxLevel = 79, spellId = 80865 }, -- levels 71-79 →  +50%
        -- Level 80: no entry → no buff applied
    },
}

-- ── Helpers ───────────────────────────────────────────────────────────────────

-- Returns the spell ID for the player's current level, or nil at max level.
local function GetSoDSpell(level)
    if level >= CONFIG.MAX_LEVEL then return nil end
    for _, tier in ipairs(CONFIG.TIERS) do
        if level <= tier.maxLevel then
            return tier.spellId
        end
    end
    return nil
end

-- Removes every known SoD spell ID from the player (clean slate).
local function RemoveAllSoDBuffs(player)
    for _, tier in ipairs(CONFIG.TIERS) do
        player:RemoveAura(tier.spellId)
    end
end

-- Applies the correct tier buff for the player's current level.
-- Clears existing SoD buffs first so only one tier is ever active.
local function ApplySoDBuffForLevel(player)
    RemoveAllSoDBuffs(player)
    local spellId = GetSoDSpell(player:GetLevel())
    if spellId then
        player:CastSpell(player, spellId, true)
    end
end

-- ── Event handlers ────────────────────────────────────────────────────────────

-- PLAYER_EVENT_ON_LOGIN (3): Apply the correct tier buff on login.
local function OnLogin(event, player)
    ApplySoDBuffForLevel(player)
end

-- PLAYER_EVENT_ON_LOGOUT (4): Clean up all SoD buffs on logout.
local function OnLogout(event, player)
    RemoveAllSoDBuffs(player)
end

-- PLAYER_EVENT_ON_LEVEL_CHANGE (13): Refresh tier when the player levels up.
-- Signature: (event, player, oldLevel)
local function OnLevelChange(event, player, oldLevel)
    local newLevel = player:GetLevel()
    local newSpell = GetSoDSpell(newLevel)
    local oldSpell = GetSoDSpell(oldLevel)
    -- Reapply when the tier changes OR when the expected aura is missing
    -- (e.g. removed by a GM or another script mid-session).
    if newSpell ~= oldSpell or (newSpell and not player:HasAura(newSpell)) then
        ApplySoDBuffForLevel(player)
    end
end

-- ── Registration ──────────────────────────────────────────────────────────────
if CONFIG.ENABLED then
    RegisterPlayerEvent(3,  OnLogin)
    RegisterPlayerEvent(4,  OnLogout)
    RegisterPlayerEvent(13, OnLevelChange)
end
