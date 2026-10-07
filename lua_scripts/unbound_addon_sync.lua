-- Unbound <-> Multiclass Talents bridge (mod-ale / Eluna).
--   1. Class-unlock sync ("SYNC")  -> replies "CLASSES:<ids>".
--   2. Cross-class talent learn ("LEARN:<classId>:<spellId>") -> validated grant.
--
-- Cross-class talents are made to behave like REAL talents, enforced here:
--   * spell must be a real talent rank of that class (allowlist)
--   * you must own the class (unbound_character_unlocks) or it is your native
--   * rank order: you learn rank N only after rank N-1
--   * tier gating: (tier-1)*5 points already spent in THAT class tree
--   * prereq talent (if any) must be maxed
--   * budget: drawn from your REAL talent pool (GetFreeTalentPoints), shared
--     across your native tree and every cross-class tree
-- Spent cross-class ranks are tracked in unbound_character_talents so the tier
-- gate and rank order survive relog. The `.learn` GM command is never used.
--
-- Metadata comes from unbound_talent_data.lua (global UnboundTalentData).

local PREFIX = "MCUB"
local CHAT_MSG_WHISPER = 7
local ADDON_EVENT_ON_MESSAGE = 30
local PLAYER_EVENT_ON_LOGIN = 3
local PLAYER_EVENT_ON_LEVEL_CHANGE = 13
local POINTS_PER_TIER = 5

-- ---------------------------------------------------------------------------
-- Build lookup indices from UnboundTalentData (once, at load).
--   talentMeta[classId][talentId]      = { t,c,mr,r,p }
--   spellIndex[classId][spellId]       = { tid = talentId, ri = rankIndex }
--   tierColIndex[classId][tier][col]   = talentId       (for prereq lookup)
-- ---------------------------------------------------------------------------
local talentMeta, spellIndex, tierColIndex = {}, {}, {}
local indicesBuilt = false
local missingDataWarned = false

-- Built lazily on first use: ALE loads the lua_scripts dir alphabetically, so
-- unbound_talent_data.lua loads AFTER this file. By the time any addon message
-- arrives every script is loaded, so UnboundTalentData is guaranteed present.
local function EnsureIndices()
    if indicesBuilt then return true end
    if not UnboundTalentData then
        if not missingDataWarned then
            print("[UNBOUND] ERROR: unbound_talent_data.lua not loaded — " ..
                "cross-class talent learn will DENY. Check the data file exists " ..
                "in lua_scripts and `.reload ale`.")
            missingDataWarned = true
        end
        return false
    end
    for classId, talents in pairs(UnboundTalentData) do
        talentMeta[classId] = talents
        spellIndex[classId] = {}
        tierColIndex[classId] = {}
        for talentId, meta in pairs(talents) do
            tierColIndex[classId][meta.t] = tierColIndex[classId][meta.t] or {}
            tierColIndex[classId][meta.t][meta.c] = talentId
            for ri, rankSpell in ipairs(meta.r) do
                spellIndex[classId][rankSpell] = { tid = talentId, ri = ri }
            end
        end
    end
    indicesBuilt = true
    return true
end

-- ---------------------------------------------------------------------------
-- Persistent spent-rank tracking (self-creating table, acore_characters).
-- ---------------------------------------------------------------------------
local function EnsureTable()
    CharDBExecute(
        "CREATE TABLE IF NOT EXISTS `unbound_character_talents` (" ..
        "`char_guid` INT UNSIGNED NOT NULL, " ..
        "`class_id` TINYINT UNSIGNED NOT NULL, " ..
        "`talent_id` INT UNSIGNED NOT NULL, " ..
        "`rank` TINYINT UNSIGNED NOT NULL, " ..
        "PRIMARY KEY (`char_guid`, `talent_id`)) " ..
        "ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;")
end

local function GetStoredRank(guid, talentId)
    local Q = CharDBQuery(string.format(
        "SELECT `rank` FROM unbound_character_talents WHERE char_guid = %d AND talent_id = %d",
        guid, talentId))
    return Q and Q:GetUInt32(0) or 0
end

local function GetTreePoints(guid, classId)
    local Q = CharDBQuery(string.format(
        "SELECT COALESCE(SUM(`rank`),0) FROM unbound_character_talents " ..
        "WHERE char_guid = %d AND class_id = %d", guid, classId))
    return Q and Q:GetUInt32(0) or 0
end

-- Every cross-class rank this character has paid for, across all trees.
local function GetTotalSpent(guid)
    local Q = CharDBQuery(string.format(
        "SELECT COALESCE(SUM(`rank`),0) FROM unbound_character_talents " ..
        "WHERE char_guid = %d", guid))
    return Q and Q:GetUInt32(0) or 0
end

local function StoreRank(guid, classId, talentId, rank)
    CharDBExecute(string.format(
        "INSERT INTO unbound_character_talents (char_guid, class_id, talent_id, `rank`) " ..
        "VALUES (%d, %d, %d, %d) ON DUPLICATE KEY UPDATE `rank` = %d",
        guid, classId, talentId, rank, rank))
end

-- ---------------------------------------------------------------------------
-- Class-unlock helpers
-- ---------------------------------------------------------------------------
local function GetUnlockedSet(player)
    local set = {}
    local Q = CharDBQuery(string.format(
        "SELECT class_id FROM unbound_character_unlocks WHERE char_guid = %d",
        player:GetGUIDLow()))
    if Q then
        repeat set[Q:GetUInt32(0)] = true until not Q:NextRow()
    end
    return set
end

local function BuildClassList(player)
    local ids = {}
    for classId in pairs(GetUnlockedSet(player)) do ids[#ids + 1] = classId end
    table.sort(ids)
    for i = 1, #ids do ids[i] = tostring(ids[i]) end
    return table.concat(ids, ",")
end

local function Reply(player, text)
    player:SendAddonMessage(PREFIX, text, CHAT_MSG_WHISPER, player)
end

local function SendSync(player)
    if not player then return end
    Reply(player, "CLASSES:" .. BuildClassList(player))
end

-- ---------------------------------------------------------------------------
-- Cross-class talent learn
-- ---------------------------------------------------------------------------
local function HandleLearn(player, payload)
    local classId, spellId = payload:match("^(%d+):(%d+)$")
    classId = tonumber(classId)
    spellId = tonumber(spellId)
    if not classId or not spellId then return end

    local function deny(reason)
        Reply(player, string.format("DENY:%s:%d:%d", reason, classId, spellId))
    end

    -- 1. Class access: native class, or unlocked in Unbound.
    if classId ~= player:GetClass() then
        if not GetUnlockedSet(player)[classId] then
            return deny("LOCKED")
        end
    end

    -- 2. Must be a real talent rank of that class.
    if not EnsureIndices() then
        return deny("INVALID")
    end
    local idx = spellIndex[classId] and spellIndex[classId][spellId]
    if not idx then
        return deny("INVALID")
    end
    local meta = talentMeta[classId][idx.tid]
    local guid = player:GetGUIDLow()
    local stored = GetStoredRank(guid, idx.tid)

    -- 3. Idempotent: already have this rank (or higher) -> ack, no charge.
    if idx.ri <= stored then
        return Reply(player, string.format("LEARNED:%d:%d", classId, spellId))
    end

    -- 4. Rank order: only the next rank, never skip.
    if idx.ri ~= stored + 1 then
        return deny("RANK")
    end

    -- 5. Tier gating: (tier-1)*5 points already spent in this class tree.
    if GetTreePoints(guid, classId) < (meta.t - 1) * POINTS_PER_TIER then
        return deny("TIER")
    end

    -- 6. Prereq talent (if any) must be at max rank.
    if meta.p then
        local pTid = tierColIndex[classId][meta.p[1]] and
                     tierColIndex[classId][meta.p[1]][meta.p[2]]
        if pTid then
            local pMeta = talentMeta[classId][pTid]
            if GetStoredRank(guid, pTid) < pMeta.mr then
                return deny("PREREQ")
            end
        end
    end

    -- 7. Budget: shared real talent pool.
    local free = player:GetFreeTalentPoints()
    if free < 1 then
        return deny("NOPOINTS")
    end

    -- Grant: learn this rank, drop the superseded lower rank, debit a point,
    -- record the spend.
    player:LearnSpell(spellId)
    if idx.ri > 1 then
        player:RemoveSpell(meta.r[idx.ri - 1])
    end
    player:SetFreeTalentPoints(free - 1)
    StoreRank(guid, classId, idx.tid, idx.ri)
    Reply(player, string.format("LEARNED:%d:%d", classId, spellId))
end

-- ---------------------------------------------------------------------------
-- Respec: wipe EVERY talent this character has — the native class tree as well
-- as every cross-class tree — and hand back all of the points.
--
-- Two halves, and the order matters:
--   1. Cross-class ranks live only in unbound_character_talents / the spellbook,
--      so we unlearn them, credit the points back and drop the rows ourselves.
--   2. Native ranks are real talents, so Player::resetTalents() does the work.
--      It recomputes free points as CalculateTalentsPoints() — level total plus
--      any Mentor-purchased bonus points — which is exactly the right end state
--      once step 1 has zeroed the cross-class side. If the character had no
--      native talents spent, resetTalents() early-outs without touching the
--      counter, which is fine: step 1 already left it at the same value.
-- ---------------------------------------------------------------------------
local function HandleReset(player)
    if not EnsureIndices() then return end
    local guid = player:GetGUIDLow()
    local before = player:GetFreeTalentPoints()
    local crossRefund = 0

    local Q = CharDBQuery(string.format(
        "SELECT class_id, talent_id, `rank` FROM unbound_character_talents " ..
        "WHERE char_guid = %d", guid))
    if Q then
        repeat
            local classId = Q:GetUInt32(0)
            local talentId = Q:GetUInt32(1)
            local rank = Q:GetUInt32(2)
            local meta = talentMeta[classId] and talentMeta[classId][talentId]
            if meta then
                for i = 1, rank do
                    if meta.r[i] then player:RemoveSpell(meta.r[i]) end
                end
            end
            crossRefund = crossRefund + rank
        until not Q:NextRow()
    end

    if crossRefund > 0 then
        player:SetFreeTalentPoints(before + crossRefund)
    end
    CharDBExecute(string.format(
        "DELETE FROM unbound_character_talents WHERE char_guid = %d", guid))

    -- true = no reset cost; the cross-class half has always been free.
    player:ResetTalents(true)

    local after = player:GetFreeTalentPoints()
    Reply(player, "RESET:" .. (after > before and (after - before) or crossRefund))
end

-- ---------------------------------------------------------------------------
-- Free-point reconciliation.
--
-- Cross-class ranks are paid for out of the real talent pool, but the server
-- only knows about native talents: Player::InitTalentForLevel() recomputes free
-- points as (level total + purchased bonus - native spent) on login and after
-- every level-up. Without this pass that silently handed every cross-class
-- point back while the spells stayed learned — free respecs by relogging.
-- ---------------------------------------------------------------------------
local function ReconcileFreePoints(player)
    local spent = GetTotalSpent(player:GetGUIDLow())
    if spent <= 0 then return end
    local free = player:GetFreeTalentPoints()
    player:SetFreeTalentPoints(free > spent and (free - spent) or 0)
end

-- Runs on a short delay in both cases: the Player userdata captured here goes
-- stale across the login -> in-world transition, and on level-up GiveLevel()
-- must finish its own InitTalentForLevel() before we adjust the total.
local function ScheduleReconcile(player, delayMs)
    local guid = player:GetGUID()
    player:RegisterEvent(function()
        local live = GetPlayerByGUID(guid)
        if not live or not live:IsInWorld() then return end
        local ok, err = pcall(function() ReconcileFreePoints(live) end)
        if not ok then
            print("[UNBOUND] talent point reconcile ERROR: " .. tostring(err))
        end
    end, delayMs, 1)
end

-- ---------------------------------------------------------------------------
-- Router
-- ---------------------------------------------------------------------------
local function OnAddonMessage(event, sender, msgType, prefix, msg, target)
    if prefix ~= PREFIX or not sender then return end

    if msg == "SYNC" then
        SendSync(sender)
        return false
    end

    if msg == "RESET" then
        HandleReset(sender)
        return false
    end

    local learnPayload = msg:match("^LEARN:(.+)$")
    if learnPayload then
        HandleLearn(sender, learnPayload)
        return false
    end
end

RegisterServerEvent(ADDON_EVENT_ON_MESSAGE, OnAddonMessage)

RegisterPlayerEvent(PLAYER_EVENT_ON_LOGIN, function(event, player)
    ScheduleReconcile(player, 1000)
end)

RegisterPlayerEvent(PLAYER_EVENT_ON_LEVEL_CHANGE, function(event, player, oldLevel)
    ScheduleReconcile(player, 200)
end)

EnsureTable()
-- Indices bind lazily on the first cross-class pick (load-order safe).
print("[UNBOUND] Multiclass sync + cross-class talent bridge loaded " ..
    "(tier/prereq/budget enforced; talent metadata binds on first use).")
