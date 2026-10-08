local USB = UnboundSpellbook

local frame
local selectedTab = "ALL"
local currentPage = 1

local tabButtons = {}
local bookTabButtons = {}
local spellCells = {}

local UpdateBookTabButtons

local ENTRIES_PER_PAGE = 24
local COLUMNS = 6

local function ApplyBackdrop(target, alpha)
    target:SetBackdrop({
        bgFile = "Interface\\DialogFrame\\UI-DialogBox-Background",
        edgeFile = "Interface\\DialogFrame\\UI-DialogBox-Border",
        tile = true,
        tileSize = 32,
        edgeSize = 32,
        insets = {left = 11, right = 12, top = 12, bottom = 11},
    })

    if alpha then
        target:SetBackdropColor(0, 0, 0, alpha)
    end
end

local function MakeMovable(target)
    target:SetMovable(true)
    target:EnableMouse(true)
    target:RegisterForDrag("LeftButton")
    target:SetScript("OnDragStart", function(self)
        self:StartMoving()
    end)
    target:SetScript("OnDragStop", function(self)
        self:StopMovingOrSizing()
    end)
end

local function StandardButton(parent, text, width, height)
    local button = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
    button:SetWidth(width)
    button:SetHeight(height)
    button:SetText(text)
    return button
end

local titleText
local statusText
local pageText
local emptyText
local previousButton
local nextButton

local function TabLabel(tabKey)
    if tabKey == "ALL" then
        return "All Known"
    end

    local bookTab = USB.bookTabsByKey[tabKey]
    if bookTab then
        return bookTab.name
    end

    local info = USB.CLASS_INFO[tabKey]
    return info and info.name or tabKey
end

local function TabColor(tabKey)
    local info = USB.CLASS_INFO[tabKey]
    return info and info.color or "FFFFFF"
end

local function PageCount(entries)
    local count = math.ceil(#entries / ENTRIES_PER_PAGE)
    if count < 1 then
        count = 1
    end
    return count
end

local function FitToScreen()
    local widthScale = (UIParent:GetWidth() - 20) / 960
    local heightScale = (UIParent:GetHeight() - 20) / 670
    local scale = math.min(1, widthScale, heightScale)

    if scale < 0.65 then
        scale = 0.65
    end

    frame:SetScale(scale)
end

local function UpdateTabs()
    for _, item in ipairs(tabButtons) do
        local entries = USB:GetEntries(item.tabKey)
        local label = TabLabel(item.tabKey)

        if USB.CLASS_INFO[item.tabKey] then
            label = "|cff" .. TabColor(item.tabKey) .. label .. "|r"
        end

        -- The selected tab is marked with a locked highlight, never a
        -- text prefix -- uniform across class and book tabs.
        if item.tabKey == selectedTab then
            item.button:LockHighlight()
        else
            item.button:UnlockHighlight()
        end

        item.button:SetText(
            label .. "  |cffaaaaaa(" .. #entries .. ")|r"
        )
    end

    UpdateBookTabButtons()
end

local function ShowTooltip(cell)
    local entry = cell.entry
    if not entry then
        return
    end

    GameTooltip:SetOwner(cell, "ANCHOR_RIGHT")
    GameTooltip:ClearLines()

    if entry.spellID then
        GameTooltip:SetHyperlink("spell:" .. entry.spellID)
    elseif entry.slot then
        -- Slot-based tooltip for spellbook-tab entries without a link;
        -- pcall for the same invalid-slot throw the scanners guard against.
        local ok = pcall(GameTooltip.SetSpell, GameTooltip, entry.slot, USB.BOOK_TYPE)
        if not ok then
            GameTooltip:AddLine(entry.name)
        end
    else
        GameTooltip:AddLine(entry.name)
    end

    GameTooltip:AddLine(" ")

    local info = entry.classKey and USB.CLASS_INFO[entry.classKey]
    if info then
        GameTooltip:AddLine(info.name .. " ability", 0.65, 0.82, 1)
    elseif entry.bookTabName then
        GameTooltip:AddLine(
            entry.bookTabName .. " spellbook tab",
            0.65, 0.82, 1
        )
    end

    if entry.spellID then
        GameTooltip:AddLine("Spell ID: " .. entry.spellID, 0.65, 0.82, 1)
    end

    GameTooltip:AddLine(" ")
    if USB:HasReachableSlot(entry) then
        GameTooltip:AddLine("Drag to an action bar.", 0.3, 1, 0.3)
    else
        GameTooltip:AddLine(
            "Click: put a /cast macro on the cursor.",
            0.3, 1, 0.3
        )
        GameTooltip:AddLine(
            "Reuses a free pooled macro before taking a new slot.",
            0.7, 0.7, 0.7
        )
    end
    GameTooltip:Show()
end

local function RefreshUI()
    if not frame or not frame:IsShown() then
        return
    end

    -- A spellbook-driven tab disappears when a rescan finds it empty; fall
    -- back to the aggregate view instead of rendering a dead tab.
    if string.sub(selectedTab, 1, 5) == "BOOK:"
        and not USB.bookTabsByKey[selectedTab] then
        selectedTab = "ALL"
    end

    local entries = USB:GetEntries(selectedTab)
    local pageCount = PageCount(entries)

    if currentPage > pageCount then
        currentPage = pageCount
    elseif currentPage < 1 then
        currentPage = 1
    end

    titleText:SetText(
        "|cff" .. TabColor(selectedTab) .. TabLabel(selectedTab)
        .. "|r — Unbound Spellbook"
    )

    if USB.scanning then
        statusText:SetText(
            "Direct known-spell scan: "
            .. USB.scanProgress .. " / " .. USB.scanTotal
            .. " IDs checked"
        )
    else
        statusText:SetText(
            #entries .. " active known abilities shown"
            .. "     Total known active abilities: " .. USB.lastKnownCount
        )
    end

    pageText:SetText("Page " .. currentPage .. " / " .. pageCount)
    UpdateTabs()

    local startIndex = ((currentPage - 1) * ENTRIES_PER_PAGE) + 1

    for cellIndex, cell in ipairs(spellCells) do
        local entry = entries[startIndex + cellIndex - 1]

        if not entry then
            cell.entry = nil
            cell:Hide()
        else
            cell.entry = entry
            cell.icon:SetTexture(
                entry.icon or "Interface\\Icons\\INV_Misc_QuestionMark"
            )

            -- Passives render dimmed so the active abilities read first;
            -- cells are reused, so the bright state must be reset
            -- explicitly.
            if entry.passive then
                cell.icon:SetVertexColor(0.5, 0.5, 0.5)
                cell.nameText:SetTextColor(0.62, 0.62, 0.62)
            else
                cell.icon:SetVertexColor(1, 1, 1)
                cell.nameText:SetTextColor(1, 1, 1)
            end

            cell.nameText:SetText(entry.name)
            cell.rankText:SetText(entry.rankText or "")
            cell:Show()
        end
    end

    if #entries == 0 then
        if USB.scanning then
            emptyText:SetText("Checking known class abilities directly...")
        else
            emptyText:SetText("No known active abilities found in this tab.")
        end
        emptyText:Show()
    else
        emptyText:Hide()
    end

    if currentPage <= 1 then
        previousButton:Disable()
    else
        previousButton:Enable()
    end

    if currentPage >= pageCount then
        nextButton:Disable()
    else
        nextButton:Enable()
    end
end

frame = CreateFrame("Frame", "UnboundSpellbookFrame", UIParent)
frame:SetWidth(960)
frame:SetHeight(670)
frame:SetPoint("CENTER")
frame:SetFrameStrata("DIALOG")
frame:EnableMouseWheel(true)
ApplyBackdrop(frame, 0.98)
MakeMovable(frame)
frame:Hide()

frame:SetScript("OnShow", function()
    local db = USB:EnsureDB()
    selectedTab = db.selectedTab or "ALL"
    currentPage = 1
    FitToScreen()
    RefreshUI()

    if USB.lastKnownCount == 0 and not USB.scanning then
        USB:StartDirectScan()
    end
end)

frame:SetScript("OnMouseWheel", function(_, delta)
    local pageCount = PageCount(USB:GetEntries(selectedTab))

    if delta > 0 and currentPage > 1 then
        currentPage = currentPage - 1
        RefreshUI()
    elseif delta < 0 and currentPage < pageCount then
        currentPage = currentPage + 1
        RefreshUI()
    end
end)

local closeButton = CreateFrame("Button", nil, frame, "UIPanelCloseButton")
closeButton:SetPoint("TOPRIGHT", frame, "TOPRIGHT", -5, -5)

titleText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
titleText:SetPoint("TOP", frame, "TOP", 74, -18)
titleText:SetText("Unbound Spellbook")

statusText = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
statusText:SetPoint("TOP", titleText, "BOTTOM", 0, -6)

local classHeader = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
classHeader:SetPoint("TOPLEFT", frame, "TOPLEFT", 24, -26)
classHeader:SetText("Spell tabs")

local divider = frame:CreateTexture(nil, "ARTWORK")
divider:SetTexture(1, 1, 1, 0.13)
divider:SetPoint("TOPLEFT", frame, "TOPLEFT", 178, -58)
divider:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 178, 54)
divider:SetWidth(1)

local tabs = {"ALL"}
for _, classKey in ipairs(USB.CLASS_ORDER) do
    table.insert(tabs, classKey)
end

for index, tabKey in ipairs(tabs) do
    local button = StandardButton(frame, TabLabel(tabKey), 140, 29)
    button:SetPoint("TOPLEFT", frame, "TOPLEFT", 20, -58 - ((index - 1) * 43))
    button:SetScript("OnClick", function()
        selectedTab = tabKey
        currentPage = 1
        USB:EnsureDB().selectedTab = selectedTab
        RefreshUI()
    end)

    table.insert(tabButtons, {
        tabKey = tabKey,
        button = button,
    })
end

-- Spellbook-driven tabs (the real General tab plus any server-granted
-- extra, e.g. the GM tab). Buttons come from a small pool below the class
-- tabs and only show while their tab holds at least one spell.
function UpdateBookTabButtons()
    local shown = 0

    for _, bookTab in ipairs(USB.bookTabs) do
        if #bookTab.entries > 0 then
            shown = shown + 1
            local item = bookTabButtons[shown]

            if not item then
                local newItem = {}
                local button = StandardButton(frame, "", 140, 29)
                button:SetPoint(
                    "TOPLEFT",
                    frame,
                    "TOPLEFT",
                    20,
                    -58 - ((#tabs + shown - 1) * 43)
                )
                button:SetScript("OnClick", function()
                    if not newItem.tabKey then
                        return
                    end
                    selectedTab = newItem.tabKey
                    currentPage = 1
                    USB:EnsureDB().selectedTab = selectedTab
                    RefreshUI()
                end)
                newItem.button = button
                bookTabButtons[shown] = newItem
                item = newItem
            end

            item.tabKey = bookTab.key
            if bookTab.key == selectedTab then
                item.button:LockHighlight()
            else
                item.button:UnlockHighlight()
            end
            item.button:SetText(
                bookTab.name
                .. "  |cffaaaaaa(" .. #bookTab.entries .. ")|r"
            )
            item.button:Show()
        end
    end

    for index = shown + 1, #bookTabButtons do
        bookTabButtons[index].tabKey = nil
        bookTabButtons[index].button:UnlockHighlight()
        bookTabButtons[index].button:Hide()
    end
end

local gridStartX = 200
local gridStartY = -105
local cellWidth = 116
local cellHeight = 122
local columnGap = 7
local rowGap = 7

for index = 1, ENTRIES_PER_PAGE do
    local column = (index - 1) % COLUMNS
    local row = math.floor((index - 1) / COLUMNS)

    local cell = CreateFrame("Button", nil, frame)
    cell:SetWidth(cellWidth)
    cell:SetHeight(cellHeight)
    cell:SetPoint(
        "TOPLEFT",
        frame,
        "TOPLEFT",
        gridStartX + column * (cellWidth + columnGap),
        gridStartY - row * (cellHeight + rowGap)
    )
    cell:RegisterForDrag("LeftButton")

    local icon = cell:CreateTexture(nil, "BACKGROUND")
    icon:SetWidth(48)
    icon:SetHeight(48)
    icon:SetPoint("TOP", cell, "TOP", 0, -3)
    icon:SetTexCoord(0.07, 0.93, 0.07, 0.93)

    local border = cell:CreateTexture(nil, "OVERLAY")
    border:SetTexture("Interface\\Buttons\\UI-Quickslot2")
    border:SetWidth(68)
    border:SetHeight(68)
    border:SetPoint("CENTER", icon, "CENTER", 0, -1)

    local highlight = cell:CreateTexture(nil, "HIGHLIGHT")
    highlight:SetTexture("Interface\\Buttons\\ButtonHilight-Square")
    highlight:SetBlendMode("ADD")
    highlight:SetWidth(48)
    highlight:SetHeight(48)
    highlight:SetPoint("CENTER", icon, "CENTER")

    local nameText = cell:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    nameText:SetPoint("TOP", icon, "BOTTOM", 0, -7)
    nameText:SetWidth(112)
    nameText:SetHeight(38)
    nameText:SetJustifyH("CENTER")
    nameText:SetJustifyV("TOP")

    local rankText = cell:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    rankText:SetPoint("TOP", nameText, "BOTTOM", 0, -2)
    rankText:SetWidth(110)
    rankText:SetTextColor(0.75, 0.85, 1)

    cell.icon = icon
    cell.nameText = nameText
    cell.rankText = rankText

    cell:SetScript("OnEnter", ShowTooltip)
    cell:SetScript("OnLeave", function()
        GameTooltip:Hide()
    end)
    cell:SetScript("OnDragStart", function(self)
        USB:PickupEntry(self.entry)
    end)
    -- Entries with no reachable spellbook slot (past the client's
    -- 1024-slot cap -- GM entries always, most class spells too) route
    -- click -> macro pickup (see PickupViaMacro). Entries with a real
    -- slot keep drag-only, matching the native book.
    cell:SetScript("OnClick", function(self)
        local entry = self.entry
        if entry and not USB:HasReachableSlot(entry) then
            USB:PickupEntry(entry)
        end
    end)

    spellCells[index] = cell
    cell:Hide()
end

emptyText = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
emptyText:SetPoint("CENTER", frame, "CENTER", 85, -20)
emptyText:SetWidth(650)
emptyText:SetJustifyH("CENTER")
emptyText:SetText("Checking known class abilities directly...")
emptyText:Hide()

previousButton = StandardButton(frame, "< Previous", 100, 25)
previousButton:SetPoint("BOTTOM", frame, "BOTTOM", -70, 18)
previousButton:SetScript("OnClick", function()
    if currentPage > 1 then
        currentPage = currentPage - 1
        RefreshUI()
    end
end)

pageText = frame:CreateFontString(nil, "OVERLAY", "GameFontNormal")
pageText:SetPoint("BOTTOM", frame, "BOTTOM", 74, 24)
pageText:SetText("Page 1 / 1")

nextButton = StandardButton(frame, "Next >", 100, 25)
nextButton:SetPoint("BOTTOM", frame, "BOTTOM", 220, 18)
nextButton:SetScript("OnClick", function()
    local pageCount = PageCount(USB:GetEntries(selectedTab))
    if currentPage < pageCount then
        currentPage = currentPage + 1
        RefreshUI()
    end
end)

local rescanButton = StandardButton(frame, "Direct Rescan", 105, 25)
rescanButton:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", -20, 18)
rescanButton:SetScript("OnClick", function()
    USB:StartDirectScan()
    USB:Message("direct known-spell scan started.")
end)

local footer = frame:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
footer:SetPoint("BOTTOMLEFT", frame, "BOTTOMLEFT", 198, 24)
footer:SetText("Display bypasses Blizzard's General-tab page cap. Dragging still uses a native slot.")

USB:RegisterRefreshCallback(RefreshUI)

local function ToggleFrame()
    if frame:IsShown() then
        frame:Hide()
    else
        frame:Show()
    end
end

local function ReportMacros()
    local report = USB:MacroPoolReport()

    USB:Message(
        "macro slots in use: " .. report.character .. "/18 character, "
        .. report.account .. "/36 account."
    )
    USB:Message(
        "pooled by this addon: " .. report.live .. " ("
        .. report.placed .. " on action bars, "
        .. report.free .. " free to reuse)."
    )

    if report.free > 0 then
        USB:Message("reusable now: " .. table.concat(report.freeNames, ", "))
    end

    if report.stale > 0 then
        USB:Message(
            report.stale .. " pooled macro(s) were renamed, edited or"
            .. " deleted by hand -- left alone from now on."
        )
    end

    USB:Message(
        "macros made before pooling are not reused until you run"
        .. " /usbk adopt. Nothing is ever deleted."
    )
end

local function AdoptMacros(apply)
    local found = USB:AdoptMacros(apply)

    if #found == 0 then
        USB:Message("no pre-pool /cast macros found to adopt.")
        return
    end

    local names = {}
    for _, candidate in ipairs(found) do
        table.insert(names, candidate.name)
    end

    if apply then
        USB:Message("adopted " .. #found .. " macro(s): " .. table.concat(names, ", "))
        USB:Message(
            "unchanged and still on your bars -- they can now be reused"
            .. " once nothing points at them."
        )
        return
    end

    USB:Message("would adopt " .. #found .. " macro(s): " .. table.concat(names, ", "))
    USB:Message(
        "adoption only RECORDS them -- no rename, no edit, no delete."
        .. " Type: /usbk adopt confirm"
    )
end

SLASH_UNBOUNDSPELLBOOK1 = "/usbk"
SLASH_UNBOUNDSPELLBOOK2 = "/unboundspellbook"
SlashCmdList["UNBOUNDSPELLBOOK"] = function(input)
    local command = USB:NormalizeCommand(input or "")

    if command == "macros" then
        ReportMacros()
    elseif command == "adopt" then
        AdoptMacros(false)
    elseif command == "adopt confirm" then
        AdoptMacros(true)
    else
        ToggleFrame()
    end
end

-- Make the spellbook button / key open this window instead of Blizzard's
-- capped spellbook. Hold SHIFT to fall through to the original (pet book,
-- professions, and the skill tabs still live there). The pet book (bookType
-- BOOKTYPE_PET) always passes through untouched.
local origToggleSpellBook = ToggleSpellBook
if type(origToggleSpellBook) == "function" then
    ToggleSpellBook = function(bookType)
        if (bookType == nil or bookType == BOOKTYPE_SPELL)
            and not IsShiftKeyDown() then
            if SpellBookFrame and SpellBookFrame:IsShown() then
                HideUIPanel(SpellBookFrame)
            end
            ToggleFrame()
            return
        end
        return origToggleSpellBook(bookType)
    end
end

USB:Message("direct-scan edition loaded. /usbk to open, /usbk macros for macro slots.")
