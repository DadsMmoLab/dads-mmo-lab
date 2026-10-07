UnboundSpellbook = UnboundSpellbook or {}
local USB = UnboundSpellbook

USB.BOOK_TYPE = BOOKTYPE_SPELL or "spell"
USB.entriesByClass = {}
USB.allEntries = {}
USB.refreshCallbacks = {}
USB.scanning = false
USB.scanProgress = 0
USB.scanTotal = 0
USB.lastKnownCount = 0

local function RankNumber(rankText)
    if not rankText then
        return 0
    end
    return tonumber(string.match(rankText, "(%d+)")) or 0
end

local function ShouldReplace(existing, candidate)
    if not existing then
        return true
    end

    if candidate.rankNumber > existing.rankNumber then
        return true
    end

    if candidate.rankNumber == existing.rankNumber
        and candidate.spellID > existing.spellID then
        return true
    end

    return false
end

function USB:Message(text)
    if DEFAULT_CHAT_FRAME then
        DEFAULT_CHAT_FRAME:AddMessage("|cff66ff66Unbound Spellbook:|r " .. text)
    end
end

function USB:Error(text)
    if UIErrorsFrame then
        UIErrorsFrame:AddMessage(text, 1, 0.25, 0.25, 1)
    end
    self:Message("|cffff6666" .. text .. "|r")
end

function USB:RegisterRefreshCallback(callback)
    table.insert(self.refreshCallbacks, callback)
end

function USB:NotifyRefresh()
    for _, callback in ipairs(self.refreshCallbacks) do
        callback()
    end
end

function USB:EnsureDB()
    if not UnboundSpellbookDB then
        UnboundSpellbookDB = {}
    end

    if not UnboundSpellbookDB.selectedTab then
        local _, playerClass = UnitClass("player")
        UnboundSpellbookDB.selectedTab = playerClass or "ALL"
    end

    return UnboundSpellbookDB
end

local scanJobs = {}
local scanIndex = 1
local classMaps = {}
local allMap = {}

local function FinalizeScan()
    USB.entriesByClass = {}

    for _, classKey in ipairs(USB.CLASS_ORDER) do
        local entries = {}
        for _, entry in pairs(classMaps[classKey]) do
            table.insert(entries, entry)
        end
        table.sort(entries, function(a, b)
            return a.name < b.name
        end)
        USB.entriesByClass[classKey] = entries
    end

    USB.allEntries = {}
    for _, entry in pairs(allMap) do
        table.insert(USB.allEntries, entry)
    end
    table.sort(USB.allEntries, function(a, b)
        return a.name < b.name
    end)

    USB.lastKnownCount = #USB.allEntries
    USB.scanning = false
    USB.scanProgress = USB.scanTotal
    USB:NotifyRefresh()
end

local scanFrame = CreateFrame("Frame")
scanFrame:Hide()

scanFrame:SetScript("OnUpdate", function(self)
    if not USB.scanning then
        self:Hide()
        return
    end

    -- Spread the direct checks over several frames so a huge multiclass
    -- character does not freeze the UI.
    local processed = 0
    local perFrame = 160

    while scanIndex <= #scanJobs and processed < perFrame do
        local job = scanJobs[scanIndex]
        scanIndex = scanIndex + 1
        processed = processed + 1
        USB.scanProgress = scanIndex - 1

        if IsSpellKnown(job.spellID) then
            local spellName, rankText, icon = GetSpellInfo(job.spellID)

            if spellName and spellName ~= "" then
                local entry = {
                    classKey = job.classKey,
                    spellID = job.spellID,
                    name = spellName,
                    rankText = rankText or "",
                    rankNumber = RankNumber(rankText),
                    icon = icon,
                }

                local currentClassEntry = classMaps[job.classKey][spellName]
                if ShouldReplace(currentClassEntry, entry) then
                    classMaps[job.classKey][spellName] = entry
                end

                local currentAllEntry = allMap[spellName]
                if ShouldReplace(currentAllEntry, entry) then
                    allMap[spellName] = entry
                end
            end
        end
    end

    USB:NotifyRefresh()

    if scanIndex > #scanJobs then
        FinalizeScan()
        self:Hide()
    end
end)

function USB:StartDirectScan()
    self:EnsureDB()

    scanJobs = {}
    classMaps = {}
    allMap = {}

    for _, classKey in ipairs(self.CLASS_ORDER) do
        classMaps[classKey] = {}

        for _, spellID in ipairs(self.CLASS_ACTIVE_SPELL_IDS[classKey]) do
            table.insert(scanJobs, {
                classKey = classKey,
                spellID = spellID,
            })
        end
    end

    scanIndex = 1
    self.scanTotal = #scanJobs
    self.scanProgress = 0
    self.scanning = true

    self:NotifyRefresh()
    scanFrame:Show()
end

function USB:GetEntries(tabKey)
    if tabKey == "ALL" then
        return self.allEntries
    end
    return self.entriesByClass[tabKey] or {}
end

function USB:FindNativeSlot(entry)
    if not entry or not GetNumSpellTabs or not GetSpellTabInfo then
        return nil
    end

    local tabCount = GetNumSpellTabs() or 0
    local nameFallback = nil

    for tabIndex = 1, tabCount do
        local _, _, offset, spellCount = GetSpellTabInfo(tabIndex)
        offset = offset or 0
        spellCount = spellCount or 0

        for slot = offset + 1, offset + spellCount do
            local bookName, bookRank = GetSpellName(slot, self.BOOK_TYPE)

            if bookName == entry.name then
                if not nameFallback then
                    nameFallback = slot
                end

                if GetSpellLink then
                    local link = GetSpellLink(slot, self.BOOK_TYPE)
                    local linkedID = link and tonumber(string.match(link, "spell:(%d+)"))
                    if linkedID == entry.spellID then
                        return slot
                    end
                end

                if entry.rankText == "" or bookRank == entry.rankText then
                    return slot
                end
            end
        end
    end

    return nameFallback
end

function USB:PickupEntry(entry)
    if not entry then
        return
    end

    if InCombatLockdown and InCombatLockdown() then
        self:Error("Leave combat before dragging a spell.")
        return
    end

    local slot = self:FindNativeSlot(entry)
    if not slot then
        self:Error(
            "The ability is known, but the native client did not expose its spellbook slot."
        )
        return
    end

    if PickupSpell then
        PickupSpell(slot, self.BOOK_TYPE)
    else
        self:Error("PickupSpell is unavailable on this client.")
    end
end

local scheduleFrame = CreateFrame("Frame")
scheduleFrame:Hide()
local countdown = 0

function USB:ScheduleScan(delay)
    countdown = delay or 0.25
    scheduleFrame:Show()
end

scheduleFrame:SetScript("OnUpdate", function(self, elapsed)
    countdown = countdown - elapsed
    if countdown <= 0 then
        self:Hide()
        USB:StartDirectScan()
    end
end)

local eventFrame = CreateFrame("Frame")
eventFrame:RegisterEvent("PLAYER_LOGIN")
eventFrame:RegisterEvent("PLAYER_ENTERING_WORLD")
eventFrame:RegisterEvent("SPELLS_CHANGED")
eventFrame:RegisterEvent("LEARNED_SPELL_IN_TAB")

eventFrame:SetScript("OnEvent", function(_, event)
    if event == "PLAYER_LOGIN" or event == "PLAYER_ENTERING_WORLD" then
        USB:ScheduleScan(0.75)
    else
        USB:ScheduleScan(0.25)
    end
end)

SLASH_UNBOUNDSPELLBOOKRESCAN1 = "/usbkrescan"
SlashCmdList["UNBOUNDSPELLBOOKRESCAN"] = function()
    USB:StartDirectScan()
    USB:Message("direct known-spell scan started.")
end
