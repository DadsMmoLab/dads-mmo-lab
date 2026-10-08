UnboundSpellbook = UnboundSpellbook or {}
local USB = UnboundSpellbook

USB.BOOK_TYPE = BOOKTYPE_SPELL or "spell"
USB.entriesByClass = {}
USB.allEntries = {}
USB.bookTabs = {}
USB.bookTabsByKey = {}
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

local PROFESSION_ID_SET = {}
for _, spellID in ipairs(USB.PROFESSION_SPELL_IDS or {}) do
    PROFESSION_ID_SET[spellID] = true
end

local function IsProfessionSpell(spellID, spellName)
    if spellID and PROFESSION_ID_SET[spellID] then
        return true
    end
    if spellName and USB.PROFESSION_NAMES and USB.PROFESSION_NAMES[spellName] then
        return true
    end
    return false
end

-- Every class-list ID in one set: on this server tab 1 is stuffed with
-- whole class spell sets at all ranks, and extra school-tab sets appear
-- per unlocked class; both are recognised (and hidden) through this.
local CLASS_ID_SET = {}
for _, classSpellIDs in pairs(USB.CLASS_ACTIVE_SPELL_IDS or {}) do
    for _, spellID in ipairs(classSpellIDs) do
        CLASS_ID_SET[spellID] = true
    end
end

-- ShouldReplace for book entries, which may lack a spell ID.
local function BetterBookRank(existing, candidate)
    if not existing then
        return true
    end
    if candidate.rankNumber ~= existing.rankNumber then
        return candidate.rankNumber > existing.rankNumber
    end
    return (candidate.spellID or 0) > (existing.spellID or 0)
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
local classConfirmed = {}
local allMap = {}

-- An UNLOCKED class lands dozens of confirmations across its ~200-ID list,
-- because only part of a multiclass character's spells fall inside the
-- client's reachable slot array. Measured live 2026-08-14 on a Blood Elf
-- Paladin with eight extra classes unlocked:
--
--   Mage 228/301  Shaman 203/292  Warlock 165/256  Priest 131/257
--   Hunter 120/187  Druid 98/317  Warrior 27/153  Rogue 22/130
--   PALADIN 0/195   DeathKnight 0/102
--
-- So one confirmation keeps a class -- but zero does NOT mean locked, and
-- that mistake shipped once: the character's OWN class scored zero, exactly
-- like the one class they do not have, so gating on confirmations alone
-- emptied their main spellbook. Nothing in IsSpellKnown separates those two
-- cases. UnitClass does, and it cannot be capped out, so the native class is
-- always confirmed. Every other unlocked class is far from the boundary
-- (Rogue, the thinnest, still scores 22).
-- Spell IDs that exist ONLY as a talent reward, harvested from the sibling
-- multiclass-talents-ui addon's own tables rather than from memory: its
-- talent `id` and `ranks` ARE spell IDs (Adrenaline Rush is
-- {id=13750, ranks={13750}}) and this addon's class lists carry the same
-- numbers. Shape: Adv2.Data.Talents[classID][specIndex] = { talent, ... }.
-- If that addon is not loaded the set is empty and nothing is hidden.
local function TalentSpellSet()
    local set = {}
    local byClass = Adv2 and Adv2.Data and Adv2.Data.Talents
    if type(byClass) ~= "table" then
        return set
    end

    for _, specs in pairs(byClass) do
        for _, talents in pairs(specs or {}) do
            for _, talent in pairs(talents or {}) do
                if type(talent) == "table" then
                    if talent.id then
                        set[talent.id] = true
                    end
                    for _, rankID in ipairs(talent.ranks or {}) do
                        set[rankID] = true
                    end
                end
            end
        end
    end

    return set
end

local CLASS_KEY_TO_ID = {
    WARRIOR = 1, PALADIN = 2, HUNTER = 3, ROGUE = 4, PRIEST = 5,
    DEATHKNIGHT = 6, SHAMAN = 7, MAGE = 8, WARLOCK = 9, DRUID = 11,
}

-- Has the character actually spent points in this class? The native class
-- goes through Blizzard's own talent UI, the unlocked ones through
-- multiclass-talents-ui, which records every purchase in its saved
-- variables. NEITHER reads the capped spell array, so both stay reliable
-- exactly where IsSpellKnown does not.
local function ClassHasAnyTalents(classKey, nativeClass)
    if classKey == nativeClass then
        if type(GetNumTalents) == "function" and type(GetTalentInfo) == "function" then
            for tab = 1, 3 do
                for i = 1, (GetNumTalents(tab) or 0) do
                    local _, _, _, _, rank = GetTalentInfo(tab, i)
                    if (rank or 0) > 0 then
                        return true
                    end
                end
            end
        end
        return false
    end

    local classID = CLASS_KEY_TO_ID[classKey]
    local learned = Adv2 and Adv2.playerData and Adv2.playerData.learnedTalents
    if not classID or type(learned) ~= "table" or type(learned[classID]) ~= "table" then
        return false
    end

    for _, talents in pairs(learned[classID]) do
        for _, rank in pairs(talents or {}) do
            if (rank or 0) > 0 then
                return true
            end
        end
    end

    return false
end

local function FinalizeScan()
    USB.entriesByClass = {}
    allMap = {}

    local _, nativeClass = UnitClass("player")
    local talentSpells = TalentSpellSet()

    for _, classKey in ipairs(USB.CLASS_ORDER) do
        local entries = {}

        if classConfirmed[classKey] or classKey == nativeClass then
            -- A class with ZERO points spent cannot have earned ANY of its
            -- talent-granted abilities, so those are dropped -- that is the
            -- one over-report fail-open otherwise produces (Adrenaline Rush
            -- listed for a character who never specced Rogue). Deliberately
            -- class-level rather than per-talent: it can only ever hide
            -- spells belonging to a class the character has spent nothing
            -- in, so it can never hide a rotation ability like Bloodthirst
            -- or Crusader Strike, which come from classes that DO have
            -- points. A positively confirmed spell is never hidden either.
            local hideTalents = not ClassHasAnyTalents(classKey, nativeClass)

            for _, entry in pairs(classMaps[classKey]) do
                local isUnearnedTalent = hideTalents
                    and entry.spellID
                    and talentSpells[entry.spellID]
                    and not entry.confirmed

                if not isUnearnedTalent then
                    table.insert(entries, entry)

                    -- The ALL tab is built HERE, from surviving classes only,
                    -- so a locked class never leaks its spells into it.
                    if ShouldReplace(allMap[entry.name], entry) then
                        allMap[entry.name] = entry
                    end
                end
            end
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
    USB:ScanBookTabs()
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

        -- IsSpellKnown is a POSITIVE-ONLY signal here, and treating its false
        -- as "not known" is what hid whole classes. It reads the client's
        -- spell array, which is hard-capped around 1024 slots while a
        -- multiclass character knows thousands (5721 on the character this
        -- was found on). Measured live 2026-08-14: Power Word: Fortitude
        -- rank 8 (48161) casts fine yet answers FALSE, while Battle Stance
        -- answers true purely because it landed inside the cap. The two
        -- obvious alternatives were tested and are worse -- GetSpellInfo(name)
        -- returns nil past the cap, and GetSpellLink answers for spells the
        -- character does NOT own (it is a DBC lookup, verified against a
        -- Death Knight spell on a character with Death Knight locked). So no
        -- client API can answer per spell: false means "cannot tell", every
        -- listed spell is kept, and the class is judged as a whole above.
        local okKnown, isKnown = pcall(IsSpellKnown, job.spellID)
        if okKnown and isKnown then
            classConfirmed[job.classKey] = true
        end

        local spellName, rankText, icon = GetSpellInfo(job.spellID)

        if spellName and spellName ~= "" then
            local entry = {
                classKey = job.classKey,
                spellID = job.spellID,
                name = spellName,
                rankText = rankText or "",
                rankNumber = RankNumber(rankText),
                icon = icon,
                -- Kept per entry so the talent filter below can never hide a
                -- spell the client positively confirmed.
                confirmed = (okKnown and isKnown) and true or false,
            }

            local currentClassEntry = classMaps[job.classKey][spellName]
            if ShouldReplace(currentClassEntry, entry) then
                classMaps[job.classKey][spellName] = entry
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
    classConfirmed = {}
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

    local bookTab = self.bookTabsByKey[tabKey]
    if bookTab then
        return bookTab.entries
    end

    return self.entriesByClass[tabKey] or {}
end

-- Spellbook-driven tabs -- always at most three, fixed: General,
-- Professions, GM. This server grants entire class sets (all ranks)
-- into spellbook tab 1, so a raw mirror is unusable noise: General
-- keeps only tab 1's spells that are neither professions nor in any
-- class ID list, collapsed to their highest known rank, and Professions
-- is split out of tab 1. The GM tab is a static ID list (ClassData's
-- GM_SPELL_IDS) checked with IsSpellKnown, exactly like the class tabs:
-- the client's spellbook slot array is hard-capped at 1024 and General
-- alone reports 1013, so the server's "Internal" tab sits entirely past
-- the cap and slot access can never reach it. Runs from FinalizeScan,
-- so the existing SPELLS_CHANGED wiring keeps these live; a tab with
-- nothing to show is simply not kept.

-- On this client an invalid slot makes GetSpellName THROW instead of
-- returning nil (the slot array is hard-capped at 1024, and individual
-- slots can be unreadable mid-tab as well as past the end -- found
-- live: a first-failure break hid everything sorted after them,
-- professions included). So a failed slot is SKIPPED, and only a long
-- unbroken run of failures is treated as the end of the array.
local BOOK_SLOT_MISS_LIMIT = 50

function USB:ScanBookTabs()
    self.bookTabs = {}
    self.bookTabsByKey = {}

    if type(GetNumSpellTabs) ~= "function"
        or type(GetSpellTabInfo) ~= "function" then
        return
    end

    local function KeepBookTab(name, entries)
        if #entries == 0 then
            return
        end

        table.sort(entries, function(a, b)
            if a.passive ~= b.passive then
                return not a.passive        -- actives read before passives
            end
            if a.name == b.name then
                return a.rankNumber < b.rankNumber
            end
            return a.name < b.name
        end)

        local bookTab = {
            key = "BOOK:" .. name,
            name = name,
            entries = entries,
        }
        table.insert(self.bookTabs, bookTab)
        self.bookTabsByKey[bookTab.key] = bookTab
    end

    local generalName = "General"
    local generalByName = {}
    local professions = {}

    -- Only tab 1 is slot-scanned: General and Professions come from it,
    -- and the GM tab is built by ID below. Extra tabs (school-tab sets,
    -- the server's own "Internal") are never slot-walked any more.
    if (GetNumSpellTabs() or 0) >= 1 then
        local tabName, _, offset, spellCount = GetSpellTabInfo(1)
        offset = offset or 0
        spellCount = spellCount or 0

        if tabName and tabName ~= "" then
            generalName = tabName
        end

        if spellCount > 0 then
            local misses = 0

            for slot = offset + 1, offset + spellCount do
                local ok, spellName, rankText = pcall(GetSpellName, slot, self.BOOK_TYPE)

                if not ok or not spellName or spellName == "" then
                    -- Unreadable slot: skip it (see BOOK_SLOT_MISS_LIMIT).
                    misses = misses + 1
                    if misses >= BOOK_SLOT_MISS_LIMIT then
                        break
                    end
                else
                    misses = 0

                    local spellID = nil
                    if GetSpellLink then
                        local okLink, link = pcall(GetSpellLink, slot, self.BOOK_TYPE)
                        if okLink and link then
                            spellID = tonumber(string.match(link, "spell:(%d+)"))
                        end
                    end

                    local icon = nil
                    if spellID then
                        local _, _, spellIcon = GetSpellInfo(spellID)
                        icon = spellIcon
                    end
                    if not icon and GetSpellTexture then
                        local okTexture, texture = pcall(GetSpellTexture, slot, self.BOOK_TYPE)
                        if okTexture then
                            icon = texture
                        end
                    end

                    -- Degenerate slot: a non-empty name with no ID, no icon
                    -- and no rank is this client's noise, not a spell.
                    if spellID or icon or (rankText or "") ~= "" then
                        local passive = false
                        if IsPassiveSpell then
                            local okPassive, isPassive = pcall(IsPassiveSpell, slot, self.BOOK_TYPE)
                            if okPassive then
                                passive = not not isPassive
                            end
                        end

                        local entry = {
                            bookTabName = tabName,
                            slot = slot,
                            spellID = spellID,
                            name = spellName,
                            rankText = rankText or "",
                            rankNumber = RankNumber(rankText),
                            icon = icon,
                            passive = passive,
                        }

                        if IsProfessionSpell(spellID, spellName) then
                            entry.bookTabName = "Professions"
                            table.insert(professions, entry)
                        elseif spellID and CLASS_ID_SET[spellID] then
                            -- Class ability: the class tabs already list
                            -- it. This is the bulk of the General-tab
                            -- noise on a server that grants whole class
                            -- sets there.
                        elseif BetterBookRank(generalByName[spellName], entry) then
                            -- Collapse to the highest known rank, the
                            -- same way the class tabs do.
                            generalByName[spellName] = entry
                        end
                    end
                end
            end

        end
    end

    -- GM tab: the static ID list from ClassData (skill line 769
    -- "Internal"), resolved BY ID exactly like the class tabs -- slot
    -- access can never reach these spells, they sit past the 1024-slot
    -- book cap.
    local gmEntries = {}
    for _, spellID in ipairs(USB.GM_SPELL_IDS or {}) do
        local okKnown, isKnown = pcall(IsSpellKnown, spellID)
        if okKnown and isKnown then
            local spellName, rankText, icon = GetSpellInfo(spellID)
            if spellName and spellName ~= "" then
                table.insert(gmEntries, {
                    bookTabName = "GM",
                    spellID = spellID,
                    name = spellName,
                    rankText = rankText or "",
                    rankNumber = RankNumber(rankText),
                    icon = icon,
                    -- On 3.3.5 IsPassiveSpell takes a BOOK INDEX, not a
                    -- spell ID, and these spells have no reachable book
                    -- slot -- asking it would silently read some other
                    -- slot's answer. No guessing: GM entries never dim.
                    passive = false,
                    gmMacro = true,
                })
            end
        end
    end

    local generalEntries = {}
    for _, entry in pairs(generalByName) do
        table.insert(generalEntries, entry)
    end

    -- Exactly three book tabs, at most: General, Professions, GM. A tab
    -- with nothing to show is dropped by KeepBookTab.
    KeepBookTab(generalName, generalEntries)
    KeepBookTab("Professions", professions)
    KeepBookTab("GM", gmEntries)
end

-- strict = accept ONLY an ID-verified slot. Used for entries we resolved
-- by spell ID rather than by walking the book (the GM tab): there, a
-- same-named spell in a reachable slot is not the spell the user clicked,
-- and picking it up would put the WRONG ability on the bar with nothing in
-- the tooltip to say so.
function USB:FindNativeSlot(entry, strict)
    if not entry or not GetNumSpellTabs or not GetSpellTabInfo then
        return nil
    end

    local tabCount = GetNumSpellTabs() or 0
    local nameFallback = nil

    for tabIndex = 1, tabCount do
        local _, _, offset, spellCount = GetSpellTabInfo(tabIndex)
        offset = offset or 0
        spellCount = spellCount or 0

        local misses = 0

        for slot = offset + 1, offset + spellCount do
            local ok, bookName, bookRank = pcall(GetSpellName, slot, self.BOOK_TYPE)

            if not ok then
                -- Unreadable slot: skip it (see BOOK_SLOT_MISS_LIMIT).
                bookName = nil
                misses = misses + 1
                if misses >= BOOK_SLOT_MISS_LIMIT then
                    break
                end
            else
                misses = 0
            end

            if bookName == entry.name then
                if not nameFallback then
                    nameFallback = slot
                end

                if GetSpellLink then
                    local okLink, link = pcall(GetSpellLink, slot, self.BOOK_TYPE)
                    if not okLink then
                        link = nil
                    end
                    local linkedID = link and tonumber(string.match(link, "spell:(%d+)"))
                    if linkedID == entry.spellID then
                        return slot
                    end
                end

                if not strict
                    and (entry.rankText == "" or bookRank == entry.rankText) then
                    return slot
                end
            end
        end
    end

    if strict then
        return nil
    end

    return nameFallback
end

-- Whether a native book slot can hold this entry. Entries scanned out of
-- tab 1 carry their slot; anything else needs a book walk, so the answer
-- is computed once per entry and cached (entries are rebuilt every scan,
-- which keeps the cache honest across SPELLS_CHANGED).
function USB:HasReachableSlot(entry)
    if entry.slot then
        return true
    end
    if entry.reachable == nil then
        -- GM entries were assumed unreachable and hardcoded to the macro
        -- route. That assumption was measured on ONE book size and can
        -- only ever get more wrong: ask, do not assume -- and ask
        -- strictly, since a GM entry has no book walk behind it.
        entry.reachable = self:FindNativeSlot(entry, entry.gmMacro) ~= nil
    end
    return entry.reachable
end

-- ---------------------------------------------------------------------
-- The macro pool
--
-- A known spell with no reachable book slot cannot be held by
-- PickupSpell, and on this server MOST spells sit past the client's
-- 1024-slot cap (measured 2026-08-17: the book reports 1738 slots). The
-- only action-bar route for those is a "/cast <name>" macro -- and 3.3.5
-- gives a character 18 per-character + 36 account macro OBJECTS, total,
-- forever. The old code created one per spell and reclaimed none, so
-- dragging eighteen capped spells ended the feature and the user had to
-- start deleting macros by hand.
--
-- So macros are POOLED: reclaimed and rewritten in place instead of
-- accumulating. Three rules, and every one of them exists to protect
-- macros the addon did not write:
--
--   1. NEVER DeleteMacro. Reclaiming is EditMacro on the SAME index, so
--      every action-bar button pointing at it keeps working. Deleting
--      would blank that button AND shift every later macro's index
--      underneath the bars -- the one failure that loses real work.
--   2. Only macros this addon recorded in its saved variables are
--      touchable, and only while the recorded name still resolves to a
--      macro whose body is byte-for-byte the one we wrote. A rename, a
--      hand edit, or a macro we never made is the user's, permanently.
--   3. A pool macro is reclaimable only when NO action slot references
--      it. A macro sitting on a bar is in use by definition.
--
-- Rule 2 has a deliberate cost: macros created before this pool existed
-- carry no record, so they are never reclaimed. `/usbk adopt` is the
-- opt-in that hands them over, and it shows the list before it acts.
-- ---------------------------------------------------------------------

local MACRO_NAME_MAX = 16
local MAX_ACCOUNT_MACROS = 36
local MAX_CHARACTER_MACROS = 18
local ACTION_SLOTS = 120

local function Trim(text)
    if type(text) ~= "string" then
        return ""
    end
    if type(strtrim) == "function" then
        return strtrim(text)
    end
    return (string.gsub(text, "^%s*(.-)%s*$", "%1"))
end

-- Trim + collapse runs of whitespace, so "adopt  confirm" is the same
-- command as "adopt confirm".
function USB:NormalizeCommand(text)
    return string.lower((string.gsub(Trim(text), "%s+", " ")))
end

function USB:MacroBody(spellName)
    -- #showtooltip makes the action-bar button borrow the spell's own
    -- icon and tooltip; macro icon index 1 (question mark) is only the
    -- macro-frame fallback.
    return "#showtooltip\n/cast " .. spellName
end

-- Per CHARACTER, because 18 of the 54 macro objects are: a name recorded
-- on one character resolves to a different macro (or none) on another,
-- and the saved variables are account-wide.
function USB:MacroPool()
    local db = self:EnsureDB()

    if type(db.macroPool) ~= "table" then
        db.macroPool = {}
    end

    local key = (UnitName("player") or "?") .. "-" .. (GetRealmName() or "?")
    if type(db.macroPool[key]) ~= "table" then
        db.macroPool[key] = {}
    end

    return db.macroPool[key]
end

-- Resolve a pool record to a live macro index, or nil. Both halves are
-- load-bearing: the name may now belong to something else, and the body
-- may have been edited -- in either case the macro is no longer ours.
function USB:ResolvePoolMacro(name, record)
    if not (GetMacroIndexByName and GetMacroBody) or type(record) ~= "table" then
        return nil
    end

    local okIndex, index = pcall(GetMacroIndexByName, name)
    if not okIndex or not index or index == 0 then
        return nil
    end

    local okBody, body = pcall(GetMacroBody, index)
    if not okBody or not body then
        return nil
    end

    if Trim(body) ~= Trim(record.body or "") then
        return nil
    end

    return index
end

-- Which macro indices an action bar is currently pointing at. A macro in
-- here is in use, whatever the pool thinks.
function USB:MacroIndicesOnBars()
    local used = {}

    if type(GetActionInfo) ~= "function" then
        return used
    end

    for slot = 1, ACTION_SLOTS do
        local ok, actionType, id = pcall(GetActionInfo, slot)
        if ok and actionType == "macro" and id then
            used[id] = true
        end
    end

    return used
end

-- A free macro name, 16 chars or fewer. `allowName` is the name we are
-- already holding (a reclaim keeps its own name rather than colliding
-- with itself).
local function UniqueMacroName(spellName, allowName)
    local base = string.sub(spellName, 1, MACRO_NAME_MAX)
    local candidate = base

    for n = 2, 99 do
        if candidate == allowName then
            return candidate
        end

        local ok, index = pcall(GetMacroIndexByName, candidate)
        if not ok or not index or index == 0 then
            return candidate
        end

        local suffix = tostring(n)
        candidate = string.sub(base, 1, MACRO_NAME_MAX - string.len(suffix)) .. suffix
    end

    return candidate
end

function USB:MacroCounts()
    if type(GetNumMacros) ~= "function" then
        return 0, 0
    end

    local ok, account, character = pcall(GetNumMacros)
    if not ok then
        return 0, 0
    end

    return account or 0, character or 0
end

function USB:PickupViaMacro(entry)
    if not (CreateMacro and PickupMacro and GetMacroIndexByName) then
        self:Error("The macro API is unavailable on this client.")
        return
    end

    local pool = self:MacroPool()
    local body = self:MacroBody(entry.name)

    -- 1. This spell already has a live pool macro. Body, not spell ID, is
    --    the identity: book-tab entries can carry no ID at all, and the
    --    body is what actually gets cast.
    for name, record in pairs(pool) do
        if record.body == body then
            local index = self:ResolvePoolMacro(name, record)
            if index then
                if pcall(PickupMacro, index) then
                    self:Message(
                        "picked up '" .. name .. "' -- drop it on an action bar."
                    )
                else
                    self:Error("Could not pick up the existing '" .. name .. "' macro.")
                end
                return
            end
        end
    end

    -- 2. Reclaim a pool macro no action bar is using. Same index, so
    --    nothing on any bar moves or changes.
    local onBars = self:MacroIndicesOnBars()
    for name, record in pairs(pool) do
        local index = self:ResolvePoolMacro(name, record)
        if index and not onBars[index] then
            local newName = UniqueMacroName(entry.name, name)
            if pcall(EditMacro, index, newName, 1, body) then
                pool[name] = nil
                pool[newName] = { spellID = entry.spellID, body = body }

                if pcall(PickupMacro, index) then
                    self:Message(
                        "reused free macro slot as '" .. newName
                        .. "' -- drop it on an action bar."
                    )
                else
                    self:Message(
                        "reused free macro slot as '" .. newName
                        .. "'. Open /macro to place it on a bar."
                    )
                end
                return
            end
        end
    end

    -- 3. Nothing to reclaim: take a fresh slot if the client has one.
    local account, character = self:MacroCounts()
    if character >= MAX_CHARACTER_MACROS and account >= MAX_ACCOUNT_MACROS then
        self:Error(
            "All " .. (MAX_CHARACTER_MACROS + MAX_ACCOUNT_MACROS)
            .. " macro slots are full and every pooled one is on an action bar."
        )
        self:Error(
            "Clear a spell off a bar and drag again, or see /usbk macros."
        )
        return
    end

    local macroName = UniqueMacroName(entry.name)
    -- Per-character first (18 of them), account tab as the overflow (36).
    local perCharacter = nil
    if character < MAX_CHARACTER_MACROS then
        perCharacter = 1
    end

    -- 3.3.5 builds disagree on whether the per-character flag is
    -- CreateMacro's 4th or 5th argument, and getting it wrong silently
    -- spends the OTHER pool's slots -- then fails outright once that pool
    -- is full while eighteen slots sit unused. So try the documented
    -- shape, and on failure try the other one before giving up.
    local okCreate, macroID = pcall(CreateMacro, macroName, 1, body, nil, perCharacter)
    if not okCreate or not macroID then
        okCreate, macroID = pcall(CreateMacro, macroName, 1, body, perCharacter, nil)
    end
    if not okCreate or not macroID then
        self:Error("Could not create the macro. Free a slot in /macro, or see /usbk macros.")
        return
    end

    pool[macroName] = { spellID = entry.spellID, body = body }

    if pcall(PickupMacro, macroID) then
        self:Message(
            "created macro '" .. macroName
            .. "' -- drop it on an action bar."
        )
    else
        self:Message(
            "created macro '" .. macroName
            .. "'. Open /macro to place it on a bar."
        )
    end
end

-- What the pool holds right now, as counts plus the reclaimable names.
-- Read-only: it never edits, creates or forgets anything.
function USB:MacroPoolReport()
    local pool = self:MacroPool()
    local onBars = self:MacroIndicesOnBars()

    local live, placed, free, stale = 0, 0, 0, 0
    local freeNames = {}

    for name, record in pairs(pool) do
        local index = self:ResolvePoolMacro(name, record)
        if index then
            live = live + 1
            if onBars[index] then
                placed = placed + 1
            else
                free = free + 1
                table.insert(freeNames, name)
            end
        else
            stale = stale + 1
        end
    end

    table.sort(freeNames)

    local account, character = self:MacroCounts()

    return {
        live = live,
        placed = placed,
        free = free,
        stale = stale,
        freeNames = freeNames,
        account = account,
        character = character,
    }
end

-- Hand pre-pool macros over to the pool. Adoption RECORDS, it never
-- edits: the macro keeps its name, its index, its body and its place on
-- your bars, and all that changes is that a later drag may reclaim it
-- once it is on no bar. Candidates must match a body this addon would
-- itself have written for a spell it can see -- anything else is a macro
-- you wrote, and it is left alone.
function USB:AdoptMacros(apply)
    local pool = self:MacroPool()

    -- `or false`, never nil: a book-tab entry can have no spell ID, and a
    -- nil value would drop the spell out of its own lookup table.
    local knownSpells = {}
    for _, entry in ipairs(self.allEntries or {}) do
        if entry.name then
            knownSpells[entry.name] = entry.spellID or false
        end
    end

    local found = {}

    if type(GetMacroInfo) == "function" then
        for index = 1, MAX_ACCOUNT_MACROS + MAX_CHARACTER_MACROS do
            local ok, name, _, body = pcall(GetMacroInfo, index)
            if ok and name and body and not pool[name] then
                -- Match the exact shape this addon writes, then require
                -- the spell to be one it can actually see. A macro with
                -- any other line in it is not ours, however similar.
                local spellName = string.match(
                    Trim(body), "^#showtooltip%s+/cast%s+(.+)$"
                )
                if spellName and knownSpells[spellName] ~= nil then
                    table.insert(found, {
                        name = name,
                        spellName = spellName,
                        spellID = knownSpells[spellName],
                        body = self:MacroBody(spellName),
                    })
                end
            end
        end
    end

    if apply then
        for _, candidate in ipairs(found) do
            pool[candidate.name] = {
                spellID = candidate.spellID,
                body = candidate.body,
            }
        end
    end

    return found
end

function USB:PickupEntry(entry)
    if not entry then
        return
    end

    if InCombatLockdown and InCombatLockdown() then
        self:Error("Leave combat before dragging a spell.")
        return
    end

    if entry.gmMacro then
        -- Resolved by ID with no book walk behind it, so only an
        -- ID-verified slot counts. Usually there is none and this falls
        -- through to a macro -- but spending a macro slot on a spell the
        -- client can hold directly is the waste this round exists to end.
        local gmSlot = self:FindNativeSlot(entry, true)
        if gmSlot and PickupSpell then
            PickupSpell(gmSlot, self.BOOK_TYPE)
        else
            self:PickupViaMacro(entry)
        end
        return
    end

    -- Spellbook-tab entries carry their own slot; re-check it still holds
    -- the same spell before trusting it, then fall back to the name search.
    local slot
    if entry.slot then
        local ok, bookName = pcall(GetSpellName, entry.slot, self.BOOK_TYPE)
        if ok and bookName == entry.name then
            slot = entry.slot
        end
    end
    slot = slot or self:FindNativeSlot(entry)
    if not slot then
        -- No reachable native slot (past the 1024-slot cap): fall back
        -- to the macro route, which works for any known spell.
        entry.reachable = false
        self:PickupViaMacro(entry)
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
