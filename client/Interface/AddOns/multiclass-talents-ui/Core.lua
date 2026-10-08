-- Adventurer2 Core
-- Classless talent system for Adventurer class
Adv2 = Adv2 or {}

-- Saved variables
Adventurer2DB = Adventurer2DB or {}

-- Initialization state
Adv2.initialized = false

-- Player state
Adv2.playerData = {
    isAdventurer = false,
    level = 1,
    learnedAbilities = {},
    learnedTalents = {},
    learnedRacials = {},
}

-- Debug function
function Adv2.Debug(msg, ...)
    if Adv2.Config and Adv2.Config.DEBUG then
        local formatted = string.format(msg, ...)
        print("|cff00ff00[Adv2]|r " .. formatted)
    end
end

-- Check if player is Adventurer class
function Adv2.CheckIsAdventurer()
    local localizedName, englishClass, classId = UnitClass("player")
    
    Adv2.Debug("Class check: localized='%s', english='%s', id=%s", 
        tostring(localizedName), tostring(englishClass), tostring(classId))
    
    if classId == 12 then return true end
    if englishClass and englishClass:upper() == "ADVENTURER" then return true end
    if localizedName and localizedName:lower() == "adventurer" then return true end
    if classId == nil or classId == 0 then return false end

    
    return false
end

function Adv2.IsClientOnly()
    return Adv2.Config and Adv2.Config.CLIENT_ONLY == true
end

function Adv2.GetNativeTalentPointsTotal()
    local level = UnitLevel("player") or 1
    if level < 10 then
        return 0
    end
    return level - 9
end

function Adv2.GetNativeTalentPointsSpent()
    if type(GetNumTalents) ~= "function" or type(GetTalentInfo) ~= "function" then
        return 0
    end

    local spent = 0
    for tab = 1, 3 do
        local numTalents = GetNumTalents(tab)
        if numTalents and numTalents > 0 then
            for i = 1, numTalents do
                local _, _, _, _, rank = GetTalentInfo(tab, i)
                spent = spent + (rank or 0)
            end
        end
    end
    return spent
end

function Adv2.GetNativeUnspentTalentPoints()
    if type(GetUnspentTalentPoints) == "function" then
        return GetUnspentTalentPoints() or 0
    end
    return math.max(0, Adv2.GetNativeTalentPointsTotal() - Adv2.GetNativeTalentPointsSpent())
end

function Adv2.CountCrossClassTalentPoints()
    local native = Adv2.GetPlayerClassId()
    local total = 0
    for classId, specs in pairs(Adv2.playerData.learnedTalents or {}) do
        if classId ~= native then
            for _, talents in pairs(specs or {}) do
                for _, rank in pairs(talents or {}) do total = total + (rank or 0) end
            end
        end
    end
    return total
end

-- Calculate available picks
function Adv2.GetAvailablePicks()
    if Adv2.IsClientOnly() then
        local totalTalents = Adv2.GetNativeTalentPointsTotal()
        local spentTalents = Adv2.GetNativeTalentPointsSpent()
        local unspentTalents = math.max(0, Adv2.GetNativeUnspentTalentPoints() - Adv2.CountCrossClassTalentPoints())
        if totalTalents < spentTalents + unspentTalents then
            totalTalents = spentTalents + unspentTalents
        end
        local knownAbilities = Adv2.CountTable(Adv2.playerData.learnedAbilities)
        local knownRacials = Adv2.CountTable(Adv2.playerData.learnedRacials)

        return {
            abilities = { used = knownAbilities, total = knownAbilities, available = 0 },
            talents = { used = spentTalents, total = totalTalents, available = unspentTalents },
            racials = { used = knownRacials, total = knownRacials, available = 0 },
        }
    end

    local level = Adv2.playerData.level or UnitLevel("player")
    local cfg = Adv2.Config
    
    local totalAbilities = cfg.INITIAL_ABILITY_PICKS + math.max(0, level - 1) * cfg.ABILITY_PICKS_PER_LEVEL
    local totalTalentPoints = cfg.INITIAL_TALENT_POINTS + math.max(0, level - 1) * cfg.TALENT_POINTS_PER_LEVEL
    local totalRacials = cfg.INITIAL_RACIAL_PICKS
    
    local usedAbilities = Adv2.CountTable(Adv2.playerData.learnedAbilities)
    local usedTalentPoints = Adv2.CountTalentPoints()
    local usedRacials = Adv2.CountTable(Adv2.playerData.learnedRacials)
    
    return {
        abilities = { used = usedAbilities, total = totalAbilities, available = totalAbilities - usedAbilities },
        talents = { used = usedTalentPoints, total = totalTalentPoints, available = totalTalentPoints - usedTalentPoints },
        racials = { used = usedRacials, total = totalRacials, available = totalRacials - usedRacials },
    }
end

-- Count entries in a table
function Adv2.CountTable(t)
    local count = 0
    if t then
        for _ in pairs(t) do count = count + 1 end
    end
    return count
end

-- Count total talent points spent
function Adv2.CountTalentPoints()
    if Adv2.IsClientOnly() then
        return Adv2.GetNativeTalentPointsSpent()
    end

    local total = 0
    if Adv2.playerData.learnedTalents then
        for classId, specs in pairs(Adv2.playerData.learnedTalents) do
            for specIndex, talents in pairs(specs) do
                for talentId, rank in pairs(talents) do
                    total = total + rank
                end
            end
        end
    end
    return total
end

-- Get talent points for specific talent
function Adv2.GetTalentPoints(classId, specIndex, talentId)
    if Adv2.IsClientOnly() then
        if classId ~= Adv2.GetPlayerClassId() then
            if not Adv2.playerData.learnedTalents then return 0 end
            if not Adv2.playerData.learnedTalents[classId] then return 0 end
            if not Adv2.playerData.learnedTalents[classId][specIndex] then return 0 end
            return Adv2.playerData.learnedTalents[classId][specIndex][talentId] or 0
        end
        local talentData = Adv2.FindTalentData(classId, specIndex, talentId)
        local tab, index = Adv2.ResolveTalentTabIndex(classId, specIndex, talentId, talentData)
        if tab and index and type(GetTalentInfo) == "function" then
            local _, _, _, _, rank = GetTalentInfo(tab, index)
            return rank or 0
        end
        return 0
    end

    if not Adv2.playerData.learnedTalents then return 0 end
    if not Adv2.playerData.learnedTalents[classId] then return 0 end
    if not Adv2.playerData.learnedTalents[classId][specIndex] then return 0 end
    return Adv2.playerData.learnedTalents[classId][specIndex][talentId] or 0
end

-- Initialize addon
function Adv2.Init()
    if Adv2.initialized then
        Adv2.Debug("Already initialized, skipping")
        return
    end
    
    Adv2.Debug("Initializing Adventurer2...")
    
    Adv2.playerData.isAdventurer = Adv2.CheckIsAdventurer()
    Adv2.playerData.level = UnitLevel("player")
    
    Adv2.LoadSavedData()
    
    Adv2.initialized = true
    
    Adv2.RescanKnownSpells()
    if Adv2.UI and Adv2.UI.PrimeHeirloomItemCache then
        Adv2.UI.PrimeHeirloomItemCache()
    end
    if Adv2.HookClientUI then
        Adv2.HookClientUI()
    end
    
    print("|cff00ff00[Multiclass]|r Ready. /mc or the Talents button.")
end

-- Load saved data
function Adv2.LoadSavedData()
    if Adv2.IsClientOnly() then
        local guid = UnitGUID("player")
        local saved = guid and Adventurer2DB[guid] or nil
        Adv2.playerData.learnedAbilities = {}
        Adv2.playerData.learnedTalents = (saved and saved.learnedTalents) or {}
        Adv2.playerData.learnedRacials = {}
        Adv2.playerData.unboundClasses = (saved and saved.unboundClasses) or {}
        return
    end

    local guid = UnitGUID("player")
    if guid and Adventurer2DB[guid] then
        local saved = Adventurer2DB[guid]
        Adv2.playerData.learnedAbilities = saved.learnedAbilities or {}
        Adv2.playerData.learnedTalents = saved.learnedTalents or {}
        Adv2.playerData.learnedRacials = saved.learnedRacials or {}
        Adv2.Debug("Loaded saved data for player")
    end
end

-- Save data
function Adv2.SaveData()
    local guid = UnitGUID("player")
    if guid then
        Adventurer2DB[guid] = {
            learnedAbilities = Adv2.playerData.learnedAbilities,
            learnedTalents = Adv2.playerData.learnedTalents,
            learnedRacials = Adv2.playerData.learnedRacials,
            unboundClasses = Adv2.playerData.unboundClasses or {},
        }
        Adv2.Debug("Saved player data")
    end
end

-- =============================================================================
-- CLIENT LEARNING (Talented-style — no server commands)
-- =============================================================================

function Adv2.GetPlayerClassId()
    local _, classFile, classId = UnitClass("player")

    if type(classId) == "number" and classId > 0 and Adv2.Classes and Adv2.Classes[classId] then
        return classId
    end

    local fileKey = type(classFile) == "string" and classFile:upper()
        or (type(classId) == "string" and classId:upper())
    if fileKey and Adv2.ClassFileToId then
        return Adv2.ClassFileToId[fileKey]
    end

    return nil
end


-- Unbound class access -------------------------------------------------------
-- The server is authoritative. The addon requests the unlocked class list
-- from ALE/Eluna and replaces its local cache whenever the server replies.
local UNBOUND_PREFIX = "MCUB"

function Adv2.IsTalentClassAllowed(classId)
    if classId == Adv2.GetPlayerClassId() then return true end
    return Adv2.playerData.unboundClasses and Adv2.playerData.unboundClasses[classId] == true
end

function Adv2.PrintUnboundClasses()
    local names = {}
    for classId in pairs(Adv2.playerData.unboundClasses or {}) do
        local c = Adv2.Classes and Adv2.Classes[classId]
        table.insert(names, (c and c.name) or tostring(classId))
    end
    table.sort(names)
    print("|cff00ff00[Multiclass]|r Server-synced Unbound classes: " .. (#names > 0 and table.concat(names, ", ") or "none"))
end

function Adv2.ApplyUnboundClassSync(payload)
    local synced = {}
    for token in string.gmatch(payload or "", "(%d+)") do
        local classId = tonumber(token)
        -- The server is authoritative.  Do not discard a valid unlock just
        -- because the client-side class table has not finished initializing.
        if classId and classId >= 1 and classId <= 11 then synced[classId] = true end
    end
    -- Keep a previously verified list if this client receives a malformed
    -- empty reply.  A later non-empty server sync still replaces it normally.
    if not next(synced) and next(Adv2.playerData.unboundClasses or {}) then
        return
    end
    Adv2.playerData.unboundClasses = synced
    Adv2.SaveData()
    if Adv2.UpdateUI then Adv2.UpdateUI() end
end

function Adv2.RequestUnboundClassSync()
    if type(SendAddonMessage) ~= "function" then return end
    SendAddonMessage(UNBOUND_PREFIX, "SYNC", "WHISPER", UnitName("player"))
end

-- Mentor writes its unlock record asynchronously.  When it confirms a class
-- unlock in chat, retry the server sync briefly so the new class appears as
-- soon as that write reaches the character database.
local mentorSyncFrame = CreateFrame("Frame")
mentorSyncFrame:Hide()
mentorSyncFrame.elapsed = 0
mentorSyncFrame.attempts = 0
mentorSyncFrame:SetScript("OnUpdate", function(self, elapsed)
    self.elapsed = self.elapsed + elapsed
    if self.elapsed < 1 then return end
    self.elapsed = 0
    self.attempts = self.attempts + 1
    Adv2.RequestUnboundClassSync()
    if self.attempts >= 5 then self:Hide() end
end)

function Adv2.SyncAfterMentorUnlock()
    mentorSyncFrame.elapsed = 0
    mentorSyncFrame.attempts = 0
    Adv2.RequestUnboundClassSync()
    mentorSyncFrame:Show()
end

-- Cross-class talent learn over the MCUB bridge ------------------------------
-- We send LEARN:<classId>:<spellId> and commit the local rank only when the
-- server confirms with LEARNED:. Pending picks are keyed by "classId:spellId".
Adv2.pendingServerLearns = Adv2.pendingServerLearns or {}

function Adv2.RequestServerLearn(classId, specIndex, talentId, spellId)
    if type(SendAddonMessage) ~= "function" or not classId or not spellId then
        return false
    end
    Adv2.pendingServerLearns[classId .. ":" .. spellId] = {
        classId = classId, specIndex = specIndex, talentId = talentId, spellId = spellId,
    }
    SendAddonMessage(UNBOUND_PREFIX, string.format("LEARN:%d:%d", classId, spellId),
        "WHISPER", UnitName("player"))
    return true
end

function Adv2.OnServerLearned(classId, spellId, serverRank)
    local key = classId .. ":" .. spellId
    -- Do NOT consume the pending entry here: a multi-rank confirm sends the
    -- same spellId several times, and the server answers each with the
    -- AUTHORITATIVE rank it now holds (trailing field). Consuming the entry
    -- on the first reply dropped the rest, so the local rank trailed the
    -- server forever and every local rank/prereq check lied. The entry is
    -- cleared on deny/reset, and overwritten by the next send.
    local pending = Adv2.pendingServerLearns[key]
    if not pending then return end

    local specIndex, talentId = pending.specIndex, pending.talentId
    Adv2.playerData.unboundClasses = Adv2.playerData.unboundClasses or {}
    Adv2.playerData.unboundClasses[classId] = true
    Adv2.playerData.learnedTalents[classId] = Adv2.playerData.learnedTalents[classId] or {}
    Adv2.playerData.learnedTalents[classId][specIndex] = Adv2.playerData.learnedTalents[classId][specIndex] or {}
    local localRank = (Adv2.playerData.learnedTalents[classId][specIndex][talentId] or 0)
    local newRank = serverRank or (localRank + 1)
    if newRank < localRank then newRank = localRank end
    Adv2.playerData.learnedTalents[classId][specIndex][talentId] = newRank
    Adv2.SaveData()
    Adv2.UpdateUI()

    local talentData = Adv2.FindTalentData(classId, specIndex, talentId)
    print("|cff00ff00[Multiclass]|r Learned " ..
        ((talentData and talentData.name) or ("spell " .. spellId)) ..
        " rank " .. newRank .. ".")
end

function Adv2.OnServerLearnDenied(reason, classId, spellId)
    Adv2.pendingServerLearns[classId .. ":" .. spellId] = nil
    local msgByReason = {
        LOCKED   = "that class isn't unlocked. Try /mcunlock sync.",
        INVALID  = "that isn't a recognized cross-class talent.",
        RANK     = "that talent is already at its maximum rank.",
        TIER     = "you need more points spent lower in that tree first.",
        PREREQ   = "a required talent below it isn't maxed yet.",
        NOPOINTS = "no talent points available (shared with your own tree).",
    }
    print("|cffff0000[Multiclass]|r Server denied: " ..
        (msgByReason[reason] or "that talent."))
end

-- ===========================================================================
-- Staging model (client-only): cross-class picks are STAGED into
-- Adv2.pendingTalents and applied together on Confirm; own-class talents still
-- use the native LearnTalent API instantly. The server re-validates every pick.
-- ===========================================================================

-- Real shared talent pool (reflects the server's SetFreeTalentPoints debits).
function Adv2.RealFreeTalentPoints()
    if type(GetUnspentTalentPoints) == "function" then
        return GetUnspentTalentPoints() or 0
    end
    return Adv2.GetNativeUnspentTalentPoints() or 0
end

-- Points already applied + staged in a whole class tree (all specs).
function Adv2.ProjectedTreePoints(classId)
    local total = 0
    local applied = Adv2.playerData.learnedTalents[classId]
    if applied then
        for _, talents in pairs(applied) do
            for _, rank in pairs(talents) do total = total + (rank or 0) end
        end
    end
    for _, p in ipairs(Adv2.pendingTalents or {}) do
        if p.classId == classId then total = total + 1 end
    end
    return total
end

function Adv2.FindTalentByTierCol(classId, specIndex, tier, col)
    local talents = Adv2.Data and Adv2.Data.GetTalents and Adv2.Data.GetTalents(classId, specIndex)
    if not talents then return nil end
    for _, t in ipairs(talents) do
        if t.tier == tier and t.col == col then return t end
    end
    return nil
end

function Adv2.StageCrossTalent(classId, specIndex, talentId, spellId)
    if not Adv2.IsTalentClassAllowed(classId) then
        -- The 3.3.5 cache can miss a just-unlocked Mentor class.  Request a
        -- refresh, but let the server remain the final authority on Confirm.
        Adv2.RequestUnboundClassSync()
    end
    local talentData = Adv2.FindTalentData(classId, specIndex, talentId)
    if not talentData then return false end

    local maxRank = talentData.maxRank or 1
    local currentRank = Adv2.GetTalentPoints(classId, specIndex, talentId)
    local pendingRank = Adv2.CountPendingTalentRank(classId, specIndex, talentId)
    if currentRank + pendingRank >= maxRank then
        return false
    end

    if Adv2.RealFreeTalentPoints() - #Adv2.pendingTalents <= 0 then
        print("|cffff0000[Multiclass]|r No talent points available (shared with your own tree).")
        return false
    end

    if Adv2.ProjectedTreePoints(classId) < ((talentData.tier or 1) - 1) * 5 then
        print("|cffff0000[Multiclass]|r Spend more points lower in that tree first.")
        return false
    end

    if talentData.prereq then
        local pr = Adv2.FindTalentByTierCol(classId, specIndex, talentData.prereq[1], talentData.prereq[2])
        if pr then
            local have = Adv2.GetTalentPoints(classId, specIndex, pr.id)
                + Adv2.CountPendingTalentRank(classId, specIndex, pr.id)
            if have < (pr.maxRank or 1) then
                print("|cffff0000[Multiclass]|r Max the required talent below it first.")
                return false
            end
        end
    end

    table.insert(Adv2.pendingTalents, {
        classId = classId, specIndex = specIndex, talentId = talentId,
        spellId = spellId, tier = talentData.tier or 1,
        rank = currentRank + pendingRank + 1,
    })
    Adv2.UpdateUI()
    return true
end

-- Throttled sender: applying a whole build at once trips the server AntiDos
-- opcode limiter, so LEARN messages are dripped out one at a time.
Adv2.learnQueue = Adv2.learnQueue or {}
local learnSender = CreateFrame("Frame")
learnSender:Hide()
learnSender.acc = 0
learnSender:SetScript("OnUpdate", function(self, dt)
    self.acc = self.acc + dt
    if self.acc < 0.15 then return end
    self.acc = 0
    local job = table.remove(Adv2.learnQueue, 1)
    if not job then self:Hide(); return end
    Adv2.RequestServerLearn(job.classId, job.specIndex, job.talentId, job.spellId)
end)

function Adv2.ApplyPendingCrossTalents()
    if #Adv2.pendingTalents == 0 then
        print("|cffffcc00[Multiclass]|r No staged talents to confirm.")
        return
    end
    local jobs = {}
    for _, p in ipairs(Adv2.pendingTalents) do jobs[#jobs + 1] = p end
    -- Apply in tree order so the server's tier/rank gates always pass.
    table.sort(jobs, function(a, b)
        if a.classId ~= b.classId then return a.classId < b.classId end
        if (a.tier or 1) ~= (b.tier or 1) then return (a.tier or 1) < (b.tier or 1) end
        if a.talentId ~= b.talentId then return a.talentId < b.talentId end
        return (a.rank or 1) < (b.rank or 1)
    end)
    local n = #jobs
    for _, job in ipairs(jobs) do
        Adv2.learnQueue[#Adv2.learnQueue + 1] = job
    end
    Adv2.pendingTalents = {}
    learnSender:Show()
    Adv2.UpdateUI()
    print("|cff00ff00[Multiclass]|r Applying " .. n .. " talent(s)...")
end

function Adv2.RequestServerRespec()
    if type(SendAddonMessage) ~= "function" then return end
    Adv2.learnQueue = {}
    Adv2.pendingTalents = {}
    SendAddonMessage(UNBOUND_PREFIX, "RESET", "WHISPER", UnitName("player"))
    print("|cffffcc00[Multiclass]|r Requesting respec...")
end

function Adv2.OnServerReset(refund)
    -- The server respec unlearns the native tree as well, so drop every cached
    -- rank. Native ranks are re-read live from GetTalentInfo anyway.
    Adv2.playerData.learnedTalents = {}
    Adv2.pendingTalents = {}
    Adv2.SaveData()
    Adv2.UpdateUI()
    print("|cff00ff00[Multiclass]|r Respec complete — " .. refund ..
        " talent point(s) refunded.")
end

local syncFrame = CreateFrame("Frame")
syncFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
syncFrame:RegisterEvent("CHAT_MSG_ADDON")
syncFrame:RegisterEvent("CHAT_MSG_SYSTEM")
syncFrame:SetScript("OnEvent", function(self, event, prefix, message, channel, sender)
    if event == "PLAYER_ENTERING_WORLD" then
        if type(RegisterAddonMessagePrefix) == "function" then RegisterAddonMessagePrefix(UNBOUND_PREFIX) end
        local attempts = 0
        self:SetScript("OnUpdate", function(frame, elapsed)
            frame.elapsed = (frame.elapsed or 0) + elapsed
            if frame.elapsed >= 2 then
                frame.elapsed = 0
                attempts = attempts + 1
                Adv2.RequestUnboundClassSync()
                if attempts >= 5 then frame:SetScript("OnUpdate", nil) end
            end
        end)
    elseif event == "CHAT_MSG_SYSTEM" and prefix then
        local systemPayload = prefix:match("^MCUBSYNC:(.*)$")
        if systemPayload ~= nil then
            Adv2.ApplyUnboundClassSync(systemPayload)
        elseif string.find(prefix, "The path of the", 1, true) and
            string.find(prefix, "now open to you", 1, true) then
            Adv2.SyncAfterMentorUnlock()
        end
    elseif event == "CHAT_MSG_ADDON" and prefix == UNBOUND_PREFIX and message then
        local payload = message:match("^CLASSES:(.*)$")
        if payload ~= nil then
            Adv2.ApplyUnboundClassSync(payload)
            return
        end
        local lc, ls, lr = message:match("^LEARNED:(%d+):(%d+):?(%d*)$")
        if lc then
            Adv2.OnServerLearned(tonumber(lc), tonumber(ls), tonumber(lr))
            return
        end
        local reason, dc, ds = message:match("^DENY:(%u+):(%d+):(%d+)$")
        if reason then
            Adv2.OnServerLearnDenied(reason, tonumber(dc), tonumber(ds))
            return
        end
        local refund = message:match("^RESET:(%d+)$")
        if refund then
            Adv2.OnServerReset(tonumber(refund))
        end
    end
end)

SLASH_MCUNLOCK1 = "/mcunlock"
SlashCmdList.MCUNLOCK = function(msg)
    msg = (msg or ""):lower():gsub("^%s+", ""):gsub("%s+$", "")
    if msg == "sync" then
        Adv2.RequestUnboundClassSync()
        print("|cffffcc00[Multiclass]|r Requested Unbound class sync from server.")
    else
        Adv2.PrintUnboundClasses()
        print("|cffffcc00Usage:|r /mcunlock sync")
    end
end

function Adv2.FindTalentData(classId, specIndex, talentId)
    local talents = Adv2.Data and Adv2.Data.GetTalents and Adv2.Data.GetTalents(classId, specIndex)
    if not talents then
        return nil
    end
    for _, talent in ipairs(talents) do
        if talent.id == talentId then
            return talent
        end
    end
    return nil
end

function Adv2.ResolveTalentTabIndex(classId, specIndex, talentId, talentData)
    if classId ~= Adv2.GetPlayerClassId() then
        return nil
    end
    if type(GetNumTalents) ~= "function" or type(GetTalentInfo) ~= "function" then
        return nil
    end

    talentData = talentData or Adv2.FindTalentData(classId, specIndex, talentId)
    local numTalents = GetNumTalents(specIndex)
    if not numTalents or numTalents <= 0 then
        return nil
    end

    local wantName = talentData and talentData.name

    local function TierColMatches(tTier, tCol)
        if not talentData or not talentData.tier or not talentData.col then
            return false
        end
        if tTier == (talentData.tier - 1) and tCol == (talentData.col - 1) then
            return true
        end
        if tTier == talentData.tier and tCol == talentData.col then
            return true
        end
        return false
    end

    for i = 1, numTalents do
        local name, _, tier, column = GetTalentInfo(specIndex, i)
        if wantName and name and name == wantName then
            return specIndex, i
        end
    end

    for i = 1, numTalents do
        local name, _, tier, column = GetTalentInfo(specIndex, i)
        if TierColMatches(tier, column) then
            return specIndex, i
        end
    end

    if talentData and GetTalentLink then
        local targetSpell = (talentData.ranks and talentData.ranks[1]) or talentId
        for i = 1, numTalents do
            local link = GetTalentLink(specIndex, i)
            if link then
                local spellId = tonumber(link:match("spell:(%d+)"))
                if spellId == targetSpell then
                    return specIndex, i
                end
            end
        end
    end

    return nil
end

function Adv2.GetNativeTalentSlot(classId, specIndex, talentId)
    if classId ~= Adv2.GetPlayerClassId() then
        return nil
    end
    local talentData = Adv2.FindTalentData(classId, specIndex, talentId)
    local tab, index = Adv2.ResolveTalentTabIndex(classId, specIndex, talentId, talentData)
    if not tab or not index or type(GetTalentInfo) ~= "function" then
        return nil
    end
    local name, _, tier, column, rank, maxRank, _, meetsPrereq = GetTalentInfo(tab, index)
    return {
        tab = tab,
        index = index,
        name = name,
        tier = tier,
        column = column,
        rank = rank or 0,
        maxRank = maxRank or 1,
        meetsPrereq = meetsPrereq,
    }
end

function Adv2.CountPendingTalentRank(classId, specIndex, talentId)
    local count = 0
    for _, pending in ipairs(Adv2.pendingTalents or {}) do
        if pending.classId == classId and pending.specIndex == specIndex and pending.talentId == talentId then
            count = count + 1
        end
    end
    return count
end

function Adv2.TryApplyTalent(classId, specIndex, talentId, spellId)
    if Adv2.IsClientOnly() then
        return Adv2.LearnTalent(classId, specIndex, talentId, spellId)
    end
    return Adv2.AddPendingTalent(classId, specIndex, talentId, spellId)
end

function Adv2.TryLearnNativeTalent(classId, specIndex, talentId)
    local slot = Adv2.GetNativeTalentSlot(classId, specIndex, talentId)
    if slot and type(LearnTalent) == "function" then
        LearnTalent(slot.tab, slot.index)
        return true
    end
    return false
end

-- Learn an ability (Adventurer server mode only)
function Adv2.LearnAbility(spellId)
    if Adv2.IsClientOnly() then
        print("|cffffcc00[Multiclass]|r Abilities are learned from trainers on normal servers.")
        return false
    end

    if not spellId then return false end
    
    local picks = Adv2.GetAvailablePicks()
    if picks.abilities.available <= 0 then
        print("|cffff0000[Multiclass]|r No ability picks available!")
        return false
    end
    
    -- Track locally
    Adv2.playerData.learnedAbilities[spellId] = true
    Adv2.SaveData()
    
    Adv2.UpdateUI()
    return true
end

-- Learn a talent (native API on stock AC, local tracking on Adventurer servers)
function Adv2.LearnTalent(classId, specIndex, talentId, spellId)
    if Adv2.IsClientOnly() then
        if classId ~= Adv2.GetPlayerClassId() then
            if not spellId then return false end
            -- Cross-class: STAGE the pick (client-side tier/prereq/budget
            -- projection). It's sent to the server on Confirm, which is the
            -- final authority. Own-class talents fall through to the native
            -- LearnTalent API below.
            return Adv2.StageCrossTalent(classId, specIndex, talentId, spellId)
        end

        local slot = Adv2.GetNativeTalentSlot(classId, specIndex, talentId)
        if not slot then
            print("|cffff0000[Multiclass]|r Could not find that talent in your talent tree.")
            return false
        end
        if slot.rank >= slot.maxRank then
            print("|cffffcc00[Multiclass]|r That talent is already at max rank.")
            return false
        end
        if not slot.meetsPrereq then
            print("|cffff0000[Multiclass]|r Requirements not met (need more points in earlier tiers).")
            return false
        end
        if Adv2.GetNativeUnspentTalentPoints() <= 0 then
            print("|cffff0000[Multiclass]|r No talent points available!")
            return false
        end

        LearnTalent(slot.tab, slot.index)
        Adv2.UpdateUI()
        return true
    end

    if not spellId then return false end
    
    local picks = Adv2.GetAvailablePicks()
    if picks.talents.available <= 0 then
        print("|cffff0000[Multiclass]|r No talent points available!")
        return false
    end
    
    -- Track locally
    if not Adv2.playerData.learnedTalents then
        Adv2.playerData.learnedTalents = {}
    end
    if not Adv2.playerData.learnedTalents[classId] then
        Adv2.playerData.learnedTalents[classId] = {}
    end
    if not Adv2.playerData.learnedTalents[classId][specIndex] then
        Adv2.playerData.learnedTalents[classId][specIndex] = {}
    end
    
    local currentRank = Adv2.playerData.learnedTalents[classId][specIndex][talentId] or 0
    Adv2.playerData.learnedTalents[classId][specIndex][talentId] = currentRank + 1
    Adv2.SaveData()
    
    Adv2.TryLearnNativeTalent(classId, specIndex, talentId)
    
    Adv2.UpdateUI()
    return true
end

-- Learn a racial (Adventurer server mode only)
function Adv2.LearnRacial(spellId)
    if Adv2.IsClientOnly() then
        print("|cffffcc00[Multiclass]|r Racials come from your race on normal servers.")
        return false
    end

    if not spellId then return false end
    
    local picks = Adv2.GetAvailablePicks()
    if picks.racials.available <= 0 then
        print("|cffff0000[Multiclass]|r No racial picks available!")
        return false
    end
    
    -- Track locally
    Adv2.playerData.learnedRacials[spellId] = true
    Adv2.SaveData()
    
    Adv2.UpdateUI()
    return true
end

-- Execute a GM/server chat command (e.g. .additem for heirlooms on stock AzerothCore)
function Adv2.ExecuteServerCommand(cmd)
    if not cmd or cmd == "" then return end

    local editBox = ChatFrame1EditBox
    if editBox then
        local oldText = editBox:GetText() or ""
        editBox:SetText(cmd)
        ChatEdit_SendText(editBox)
        editBox:SetText(oldText)
        return
    end

    if DEFAULT_CHAT_FRAME and DEFAULT_CHAT_FRAME.editBox then
        DEFAULT_CHAT_FRAME.editBox:SetText(cmd)
        ChatEdit_SendText(DEFAULT_CHAT_FRAME.editBox)
        DEFAULT_CHAT_FRAME.editBox:SetText("")
    end
end

function Adv2.GiveHeirloom(itemId)
    if not itemId then return end

    Adv2.ExecuteServerCommand(string.format(".additem %d", itemId))

    local name = GetItemInfo(itemId) or ("Item " .. itemId)
    print("|cff00ff00[Multiclass]|r Requesting: " .. name)
end

Adv2.activeMorphId = Adv2.activeMorphId or nil

function Adv2.GetMorphName(morphId)
    for _, morph in ipairs(Adv2.Morphs or {}) do
        if morph.id == morphId then
            return morph.name
        end
    end
    return "Morph " .. tostring(morphId)
end

function Adv2.RunMorphCommand(morphId)
    print("|cff00ccff[Multiclass]|r Morph preview only in client-only mode.")
end

function Adv2.ApplyMorph(morphId)
    if not morphId then return false end
    Adv2.activeMorphId = morphId
    print("|cff00ccff[Multiclass]|r Morph selected (preview): " .. Adv2.GetMorphName(morphId))
    return true
end

function Adv2.ResetMorph()
    Adv2.activeMorphId = nil
    print("|cff00ccff[Multiclass]|r Morph selection cleared.")
end

function Adv2.ToggleMorph(morphId)
    if not morphId then return end
    if Adv2.activeMorphId == morphId then
        Adv2.ResetMorph()
    else
        Adv2.ApplyMorph(morphId)
    end
    if Adv2.MainFrame and Adv2.MainFrame.morphPanel and Adv2.MainFrame.morphPanel.UpdateSelection then
        Adv2.MainFrame.morphPanel:UpdateSelection()
    end
end

-- Check if player knows a spell
function Adv2.PlayerKnowsSpell(spellId)
    local name = GetSpellInfo(spellId)
    if not name then return false end
    
    local i = 1
    while true do
        local spellName = GetSpellName(i, BOOKTYPE_SPELL)
        if not spellName then break end
        
        local link = GetSpellLink(i, BOOKTYPE_SPELL)
        if link then
            local linkId = tonumber(link:match("spell:(%d+)"))
            if linkId == spellId then return true end
        end
        if spellName == name then
            return true
        end
        i = i + 1
    end
    
    return false
end

function Adv2.CollectSpellbookSpells()
    local spells = {}
    local seen = {}

    if type(GetNumSpellTabs) ~= "function" or type(GetSpellTabInfo) ~= "function" then
        return spells
    end

    for tab = 1, GetNumSpellTabs() do
        local _, _, offset, numSpells = GetSpellTabInfo(tab)
        if offset and numSpells and numSpells > 0 then
            for i = offset + 1, offset + numSpells do
                -- GM accounts report tab sizes that overshoot the real spell
                -- array, and this client THROWS on an invalid slot instead of
                -- returning nil. pcall + break: slots are contiguous, so the
                -- first invalid one ends the tab.
                local ok, name, rank = pcall(GetSpellName, i, BOOKTYPE_SPELL)
                if not ok then break end
                if name and name ~= "" then
                    local okLink, link = pcall(GetSpellLink or function() end, i, BOOKTYPE_SPELL)
                    if not okLink then link = nil end
                    local spellId = link and tonumber(link:match("spell:(%d+)"))
                    if spellId and not seen[spellId] then
                        seen[spellId] = true
                        local _, _, icon = GetSpellInfo(spellId)
                        spells[#spells + 1] = {
                            id = spellId,
                            name = name,
                            rank = rank,
                            icon = icon,
                        }
                    end
                end
            end
        end
    end

    table.sort(spells, function(a, b)
        return a.name:lower() < b.name:lower()
    end)

    return spells
end

-- Rescan learned abilities/racials from actual spellbook
function Adv2.RescanKnownSpells()
    Adv2.playerData.learnedAbilities = {}
    Adv2.playerData.learnedRacials = {}

    if Adv2.Data and Adv2.Data.CoreSpells then
        for _, spells in pairs(Adv2.Data.CoreSpells) do
            for _, spell in ipairs(spells) do
                if Adv2.PlayerKnowsSpell(spell.id) then
                    Adv2.playerData.learnedAbilities[spell.id] = true
                end
            end
        end
    end

    if Adv2.ProfessionCrafting then
        for _, prof in ipairs(Adv2.ProfessionCrafting) do
            if Adv2.PlayerKnowsSpell(prof.id) then
                Adv2.playerData.learnedAbilities[prof.id] = true
            end
        end
    end
    if Adv2.ProfessionGathering then
        for _, prof in ipairs(Adv2.ProfessionGathering) do
            if Adv2.PlayerKnowsSpell(prof.id) then
                Adv2.playerData.learnedAbilities[prof.id] = true
            end
        end
    end

    if Adv2.Data and Adv2.Data.GetAllRacials then
        for _, racial in ipairs(Adv2.Data.GetAllRacials()) do
            if Adv2.PlayerKnowsSpell(racial.id) then
                Adv2.playerData.learnedRacials[racial.id] = true
            end
        end
    end

    Adv2.SaveData()
end

-- Update the UI
function Adv2.UpdateUI()
    if Adv2.MainFrame and Adv2.MainFrame.UpdateHeader then
        Adv2.MainFrame:UpdateHeader()
    end
    if Adv2.MainFrame and Adv2.MainFrame.UpdatePanels then
        Adv2.MainFrame:UpdatePanels()
    elseif Adv2.MainFrame and Adv2.MainFrame.activeTab and Adv2.MainFrame.activeTab.contentPanel then
        local panel = Adv2.MainFrame.activeTab.contentPanel
        if panel.Update then panel:Update() end
    end
end

-- Toggle main frame
function Adv2.Toggle()
    if Adv2.OpenClasslessUI then
        Adv2.OpenClasslessUI()
        return
    end

    if Adv2.MainFrame then
        if Adv2.MainFrame:IsShown() then
            if Adv2.MainFrame.rightCol and Adv2.MainFrame.rightCol.ReleaseSearchFocus then
                Adv2.MainFrame.rightCol:ReleaseSearchFocus()
            end
            Adv2.MainFrame:Hide()
        else
            Adv2.MainFrame:Show()
            Adv2.UpdateUI()
        end
    else
        Adv2.Debug("MainFrame not created, attempting to create...")
        if Adv2.CreateMainFrame then
            local frame = Adv2.CreateMainFrame()
            if frame then
                frame:Show()
            else
                print("|cffff0000[Multiclass]|r Failed to open UI. Try /adv2 force or check for Lua errors.")
            end
        else
            print("|cffff0000[Multiclass]|r MainFrame module failed to load. Run /script ReloadUI()")
        end
    end
end

-- =============================================================================
-- LFG ROLE FIX
-- The client's GetAvailableRoles() does not recognize class 12 (Adventurer),
-- so the Dungeon Finder permanently disables the Tank/Healer/DPS buttons (no
-- checkboxes). Override GetAvailableRoles to report all three roles for class 12,
-- then refresh the role buttons. Non-Adventurers are unaffected (per-call gate).
-- =============================================================================
function Adv2.EnableLFGRoles()
    if Adv2._lfgRolesPatched then return end
    if type(GetAvailableRoles) ~= "function" then return end

    Adv2._lfgRolesPatched = true
    local origGetAvailableRoles = GetAvailableRoles
    GetAvailableRoles = function(...)
        -- NOTE: UnitClass() returns nil for the numeric class id of Adventurer on
        -- this client (class 12 is not in the client's class-id table), so we must
        -- match by class NAME like the rest of the addon does.
        local localizedName, englishClass, classId = UnitClass("player")
        if classId == 12
            or (englishClass and englishClass:upper() == "ADVENTURER")
            or (localizedName and localizedName:lower() == "adventurer") then
            return true, true, true
        end
        return origGetAvailableRoles(...)
    end

    -- Refresh the role buttons now that roles are available.
    if type(LFG_UpdateAvailableRoles) == "function" then
        LFG_UpdateAvailableRoles()
    end
end

-- =============================================================================
-- EVENT HANDLING
-- =============================================================================

local eventFrame = CreateFrame("Frame")
eventFrame:RegisterEvent("ADDON_LOADED")
eventFrame:RegisterEvent("PLAYER_LOGIN")
eventFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
eventFrame:RegisterEvent("PLAYER_LEVEL_UP")
eventFrame:RegisterEvent("SPELLS_CHANGED")
eventFrame:RegisterEvent("PLAYER_TALENT_UPDATE")
eventFrame:RegisterEvent("CHARACTER_POINTS_CHANGED")

eventFrame:SetScript("OnEvent", function(self, event, arg1, arg2, arg3, arg4)
    if event == "ADDON_LOADED" and (arg1 == "multiclass-talents-ui" or arg1 == "Adventurer2") then
        Adv2.Debug("Addon files loaded")
    elseif event == "PLAYER_LOGIN" then
        Adv2.Debug("PLAYER_LOGIN event")
        -- Delay init to ensure everything is ready
        Adv2.ScheduleInit(1.0)
    elseif event == "PLAYER_ENTERING_WORLD" then
        Adv2.Debug("PLAYER_ENTERING_WORLD event")
        -- Also try init here as backup
        Adv2.ScheduleInit(0.5)
    elseif event == "PLAYER_LEVEL_UP" then
        Adv2.playerData.level = arg1 or UnitLevel("player")
        Adv2.UpdateUI()
        print("|cff00ff00[Multiclass]|r Level up! You have new picks available.")
    elseif event == "SPELLS_CHANGED" or event == "PLAYER_TALENT_UPDATE" or event == "CHARACTER_POINTS_CHANGED" then
        if Adv2.initialized then
            if Adv2.IsClientOnly() then
                if Adv2.MainFrame and Adv2.MainFrame:IsShown() then
                    Adv2.UpdateUI()
                end
            else
                Adv2.RescanKnownSpells()
                if Adv2.MainFrame and Adv2.MainFrame:IsShown() then
                    Adv2.UpdateUI()
                end
            end
        end
    end
end)

-- Scheduled initialization (with debounce)
Adv2.initScheduled = false
function Adv2.ScheduleInit(delay)
    if Adv2.initialized or Adv2.initScheduled then
        return
    end
    
    Adv2.initScheduled = true
    Adv2.Debug("Scheduling init in %.1f seconds", delay)
    
    -- Use OnUpdate timer since C_Timer may not exist in 3.3.5a
    local timerFrame = CreateFrame("Frame")
    local elapsed = 0
    timerFrame:SetScript("OnUpdate", function(self, dt)
        elapsed = elapsed + dt
        if elapsed >= delay then
            self:SetScript("OnUpdate", nil)
            Adv2.initScheduled = false
            Adv2.Init()
        end
    end)
end

-- =============================================================================
-- SLASH COMMANDS
-- =============================================================================

SLASH_MULTICLASSTALENTS1 = "/mc"
SLASH_MULTICLASSTALENTS2 = "/adv2"
SlashCmdList["MULTICLASSTALENTS"] = function(msg)
    msg = msg and msg:lower() or ""
    
    if msg == "debug" then
        local localizedName, englishClass, classId = UnitClass("player")
        local picks = Adv2.GetAvailablePicks()
        print("|cff00ff00[Adv2 Debug]|r Class Info:")
        print("  Class: " .. tostring(localizedName) .. " (" .. tostring(classId) .. ")")
        print("  isAdventurer: " .. tostring(Adv2.playerData.isAdventurer))
        print("  initialized: " .. tostring(Adv2.initialized))
        print("  Level: " .. tostring(Adv2.playerData.level))
        print("  Abilities: " .. picks.abilities.used .. "/" .. picks.abilities.total)
        print("  Talents: " .. picks.talents.used .. "/" .. picks.talents.total)
        print("  Racials: " .. picks.racials.used .. "/" .. picks.racials.total)
        return
    end
    
    if msg == "lfg" then
        print("|cff00ff00[Adv2 LFG]|r diagnostics:")
        local _, _, classId = UnitClass("player")
        print("  UnitClass id: " .. tostring(classId))
        print("  _lfgRolesPatched: " .. tostring(Adv2._lfgRolesPatched))
        print("  GetAvailableRoles type: " .. type(GetAvailableRoles))
        if type(GetAvailableRoles) == "function" then
            local t, h, d = GetAvailableRoles()
            print("  GetAvailableRoles() -> tank=" .. tostring(t) .. " healer=" .. tostring(h) .. " dps=" .. tostring(d))
        end
        print("  LFG_UpdateAvailableRoles type: " .. type(LFG_UpdateAvailableRoles))
        print("  LFG_EnableRoleButton type: " .. type(LFG_EnableRoleButton))
        print("  LFDQueueFrameRoleButtonTank: " .. tostring(LFDQueueFrameRoleButtonTank))
        print("  LFDQueueFrameRoleButtonHealer: " .. tostring(LFDQueueFrameRoleButtonHealer))
        print("  LFDQueueFrameRoleButtonDPS: " .. tostring(LFDQueueFrameRoleButtonDPS))
        print("  LFDQueueFrameRoleButtonLeader: " .. tostring(LFDQueueFrameRoleButtonLeader))
        -- Attempt the fix live and refresh
        Adv2.EnableLFGRoles()
        if type(LFG_UpdateAvailableRoles) == "function" then
            LFG_UpdateAvailableRoles()
            print("  -> called LFG_UpdateAvailableRoles()")
        end
        -- Report checkbutton enabled states
        local function _cbState(b)
            if b and b.checkButton then
                return "enabled=" .. tostring(b.checkButton:IsEnabled()) .. " shown=" .. tostring(b.checkButton:IsShown())
            end
            return "no checkButton"
        end
        print("  Tank cb: " .. _cbState(LFDQueueFrameRoleButtonTank))
        print("  Healer cb: " .. _cbState(LFDQueueFrameRoleButtonHealer))
        print("  DPS cb: " .. _cbState(LFDQueueFrameRoleButtonDPS))
        -- Round-trip test: try to set DPS role via the real code path, read it back
        if type(GetLFGRoles) == "function" and type(SetLFGRoles) == "function" then
            local l0, t0, h0, d0 = GetLFGRoles()
            print(string.format("  GetLFGRoles BEFORE: leader=%s tank=%s healer=%s dps=%s",
                tostring(l0), tostring(t0), tostring(h0), tostring(d0)))
            SetLFGRoles(l0, true, false, true)
            local l1, t1, h1, d1 = GetLFGRoles()
            print(string.format("  GetLFGRoles AFTER SetLFGRoles(_,T,F,T): leader=%s tank=%s healer=%s dps=%s",
                tostring(l1), tostring(t1), tostring(h1), tostring(d1)))
        end
        return
    end
    
    if msg == "fixchat" or msg == "unfocus" then
        if Adv2.MainFrame and Adv2.MainFrame.rightCol and Adv2.MainFrame.rightCol.ReleaseSearchFocus then
            Adv2.MainFrame.rightCol:ReleaseSearchFocus()
        end
        if ChatFrame1EditBox then
            ChatFrame1EditBox:SetFocus()
        end
        print("|cff00ff00[Multiclass]|r Chat focus restored.")
        return
    end
    
    if msg == "force" then
        Adv2.playerData.isAdventurer = true
        print("|cff00ff00[Multiclass]|r Force enabled!")
        if Adv2.HookClientUI then Adv2.HookClientUI() end
        if not Adv2.MainFrame then
            Adv2.CreateMainFrame()
        end
        if Adv2.MainFrame then
            Adv2.Toggle()
        end
        return
    end
    
    if msg == "init" then
        Adv2.initialized = false
        Adv2.Init()
        return
    end
    
    if msg == "reset" then
        Adv2.playerData.learnedAbilities = {}
        Adv2.playerData.learnedTalents = {}
        Adv2.playerData.learnedRacials = {}
        Adv2.SaveData()
        print("|cff00ff00[Multiclass]|r Local data reset.")
        Adv2.UpdateUI()
        return
    end

    if msg == "resetall" then
        Adv2.playerData.learnedAbilities = {}
        Adv2.playerData.learnedTalents = {}
        Adv2.playerData.learnedRacials = {}
        Adv2.ClearPending()
        Adv2.SaveData()
        Adv2.UpdateUI()
        print("|cff00ff00[Multiclass]|r All saved picks cleared.")
        return
    end

    if msg == "sync" then
        Adv2.RescanKnownSpells()
        Adv2.UpdateUI()
        print("|cff00ff00[Multiclass]|r Refreshed from spellbook / talents.")
        return
    end
    
    if msg == "help" then
        print("|cff00ff00[Multiclass]|r Commands:")
        print("  /mc - Toggle panel (Talents button too)")
        print("  /mc debug - Show debug info")
        print("  /mc reset - Reset local saved picks")
        print("  /mc sync - Refresh from your spellbook")
        print("  /mc init - Re-initialize addon")
        return
    end
    
    -- Normal toggle
    if not Adv2.initialized then
        Adv2.Init()
    end
    
    if not Adv2.ShouldUseClasslessUI() then
        print("|cffff0000[Multiclass]|r UI not available for this character.")
        return
    end
    
    if not Adv2.MainFrame then
        Adv2.CreateMainFrame()
    end

    if not Adv2.MainFrame then
        print("|cffff0000[Multiclass]|r UI failed to load. Run /reload then /mc once.")
        return
    end

    Adv2.Toggle()
end

print("|cff00ff00[Multiclass]|r client-only build loaded. /mc or Talents button.")
