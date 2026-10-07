local USB = UnboundSpellbook

local frame
local selectedTab = "ALL"
local currentPage = 1

local tabButtons = {}
local spellCells = {}

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
        local prefix = item.tabKey == selectedTab and "> " or ""
        local label = TabLabel(item.tabKey)

        if USB.CLASS_INFO[item.tabKey] then
            label = "|cff" .. TabColor(item.tabKey) .. label .. "|r"
        end

        item.button:SetText(
            prefix .. label .. "  |cffaaaaaa(" .. #entries .. ")|r"
        )
    end
end

local function ShowTooltip(cell)
    local entry = cell.entry
    if not entry then
        return
    end

    GameTooltip:SetOwner(cell, "ANCHOR_RIGHT")
    GameTooltip:ClearLines()
    GameTooltip:SetHyperlink("spell:" .. entry.spellID)
    GameTooltip:AddLine(" ")
    GameTooltip:AddLine(
        USB.CLASS_INFO[entry.classKey].name .. " ability",
        0.65, 0.82, 1
    )
    GameTooltip:AddLine("Spell ID: " .. entry.spellID, 0.65, 0.82, 1)
    GameTooltip:AddLine(" ")
    GameTooltip:AddLine("Drag to an action bar.", 0.3, 1, 0.3)
    GameTooltip:Show()
end

local function RefreshUI()
    if not frame or not frame:IsShown() then
        return
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
classHeader:SetText("Class tabs")

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

SLASH_UNBOUNDSPELLBOOK1 = "/usbk"
SLASH_UNBOUNDSPELLBOOK2 = "/unboundspellbook"
SlashCmdList["UNBOUNDSPELLBOOK"] = ToggleFrame

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

USB:Message("direct-scan edition loaded. Type /usbk")
