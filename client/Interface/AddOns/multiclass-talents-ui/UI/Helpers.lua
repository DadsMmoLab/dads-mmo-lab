-- Adventurer2 UI Helpers
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

-- Run fn once parent has a usable width (ScrollFrames need this on first open).
function Adv2.UI.DeferWhenSized(parent, fn, minWidth, maxAttempts)
    if not parent or not fn then return end
    minWidth = minWidth or 80
    maxAttempts = maxAttempts or 15
    local attempts = 0
    local ticker = CreateFrame("Frame")
    ticker:SetScript("OnUpdate", function(self, elapsed)
        attempts = attempts + 1
        if attempts % 2 ~= 0 and attempts < maxAttempts then
            return
        end
        local w = parent:GetWidth() or 0
        if w >= minWidth or attempts >= maxAttempts then
            self:SetScript("OnUpdate", nil)
            fn()
        end
    end)
end

function Adv2.UI.Debounce(parent, key, delay, fn)
    if not parent or not fn then return end
    parent._debouncers = parent._debouncers or {}
    local ticker = parent._debouncers[key]
    if not ticker then
        ticker = CreateFrame("Frame", nil, parent)
        parent._debouncers[key] = ticker
    end
    local wait = 0
    ticker:SetScript("OnUpdate", function(self, elapsed)
        wait = wait + elapsed
        if wait >= (delay or 0.12) then
            self:SetScript("OnUpdate", nil)
            fn()
        end
    end)
end

local TEX_ROCK = "Interface\\AddOns\\multiclass-talents-ui\\Art\\UI-Background-Rock.blp"
local TEX_MARBLE = "Interface\\AddOns\\multiclass-talents-ui\\Art\\UI-Background-Marble.blp"
local TEX_UNDERLAY = "Interface\\Buttons\\WHITE8X8"

local function ApplySolidFill(frame, r, g, b, a, left, top, right, bottom)
    if not frame._fillUnder then
        frame._fillUnder = frame:CreateTexture(nil, "BACKGROUND", nil, -1)
        frame._fillUnder:SetTexture(TEX_UNDERLAY)
    end
    frame._fillUnder:ClearAllPoints()
    frame._fillUnder:SetPoint("TOPLEFT", frame, "TOPLEFT", left or 0, top or 0)
    frame._fillUnder:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", right or 0, bottom or 0)
    frame._fillUnder:SetVertexColor(r or 0.08, g or 0.07, b or 0.06, a or 1)
    frame._fillUnder:Show()
    if frame._fillTile then
        frame._fillTile:Hide()
    end
end

local function ApplyTiledTextureFill(frame, path, tint, left, top, right, bottom)
    if not frame._fillUnder then
        frame._fillUnder = frame:CreateTexture(nil, "BACKGROUND")
        frame._fillUnder:SetTexture(TEX_UNDERLAY)
    end
    frame._fillUnder:ClearAllPoints()
    frame._fillUnder:SetPoint("TOPLEFT", frame, "TOPLEFT", left or 0, top or 0)
    frame._fillUnder:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", right or 0, bottom or 0)
    frame._fillUnder:SetVertexColor(0.08, 0.07, 0.06, 1)
    frame._fillUnder:Show()

    if not frame._fillTile then
        frame._fillTile = frame:CreateTexture(nil, "BACKGROUND")
    end
    frame._fillTile:SetHorizTile(true)
    frame._fillTile:SetVertTile(true)
    frame._fillTile:SetTexture(path or TEX_ROCK)
    local c = tint or { 1, 1, 1, 1 }
    frame._fillTile:SetVertexColor(c[1], c[2], c[3], c[4] or 1)
    frame._fillTile:ClearAllPoints()
    frame._fillTile:SetPoint("TOPLEFT", frame, "TOPLEFT", left or 0, top or 0)
    frame._fillTile:SetPoint("BOTTOMRIGHT", frame, "BOTTOMRIGHT", right or 0, bottom or 0)
    frame._fillTile:Show()
end

local function ApplyEdgeBorder(frame, edgeFile, borderColor, insets)
    if not frame.SetBackdrop then return end
    insets = insets or { left = 11, right = 12, top = 12, bottom = 11 }
    frame:SetBackdrop({
        edgeFile = edgeFile,
        tile = true,
        tileSize = 32,
        edgeSize = 32,
        insets = insets,
    })
    local bc = borderColor or { 0.75, 0.65, 0.40, 1 }
    frame:SetBackdropBorderColor(bc[1], bc[2], bc[3], bc[4] or 1)
end

local function ApplyTiledBackdrop(frame, bgFile, borderFile, bgColor, borderColor, insets)
    insets = insets or { left = 4, right = 4, top = 4, bottom = 4 }
    local backdrop = {
        bgFile = bgFile,
        tile = true,
        tileSize = 256,
        insets = insets,
    }
    if borderFile then
        backdrop.edgeFile = borderFile
        backdrop.edgeSize = 32
    end
    frame:SetBackdrop(backdrop)
    if bgColor then
        frame:SetBackdropColor(bgColor[1], bgColor[2], bgColor[3], bgColor[4] or 1)
    end
    if borderFile and borderColor then
        frame:SetBackdropBorderColor(borderColor[1], borderColor[2], borderColor[3], borderColor[4] or 1)
    end
end

-- Debug helper
local function DebugPrint(msg)
    if Adv2.Config and Adv2.Config.DEBUG then
        print("|cff00ffff[Adv2 UI]|r " .. msg)
    end
end

-- Create a texture with solid color (3.3.5a compatible)
function Adv2.UI.CreateSolidTexture(parent, r, g, b, a)
    if not parent then 
        DebugPrint("CreateSolidTexture: no parent!")
        return nil 
    end
    local tex = parent:CreateTexture(nil, "BACKGROUND")
    -- Use a simple 1x1 white texture approach for 3.3.5a
    tex:SetTexture("Interface\\Buttons\\WHITE8X8")
    tex:SetVertexColor(r or 0, g or 0, b or 0, a or 1)
    return tex
end

-- Create backdrop (3.3.5a style)
function Adv2.UI.CreateBackdrop(frame, bgColor, borderColor)
    bgColor = bgColor or { 1, 1, 1, 1 }
    borderColor = borderColor or { 0.30, 0.24, 0.16, 1 }
    ApplySolidFill(frame, 0.09, 0.08, 0.07, 1, 0, 0, 0, 0)
    if not frame._insetBorder then
        frame._insetBorder = CreateFrame("Frame", nil, frame)
        frame._insetBorder:SetAllPoints()
    end
    ApplyEdgeBorder(frame._insetBorder, "Interface\\Tooltips\\UI-Tooltip-Border", borderColor,
        { left = 2, right = 2, top = 2, bottom = 2 })
    frame._insetBorder:Show()
end

-- Create a standard button
function Adv2.UI.CreateButton(parent, text, width, height)
    local btn = CreateFrame("Button", nil, parent, "UIPanelButtonTemplate")
    btn:SetSize(width or 100, height or 24)
    btn:SetText(text or "Button")
    return btn
end

-- Create icon button (for talents)
function Adv2.UI.CreateIconButton(parent, size)
    size = size or 40
    
    local btn = CreateFrame("Button", nil, parent)
    btn:SetSize(size, size)
    
    -- Icon texture
    btn.icon = btn:CreateTexture(nil, "ARTWORK")
    btn.icon:SetPoint("TOPLEFT", 2, -2)
    btn.icon:SetPoint("BOTTOMRIGHT", -2, 2)
    btn.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    
    -- Border
    btn.border = btn:CreateTexture(nil, "OVERLAY")
    btn.border:SetAllPoints()
    btn.border:SetTexture("Interface\\Buttons\\UI-ActionButton-Border")
    btn.border:SetBlendMode("ADD")
    btn.border:SetAlpha(0.7)
    
    -- Highlight
    btn.highlight = btn:CreateTexture(nil, "HIGHLIGHT")
    btn.highlight:SetAllPoints()
    btn.highlight:SetTexture("Interface\\Buttons\\ButtonHilight-Square")
    btn.highlight:SetBlendMode("ADD")
    
    -- Rank text
    btn.rankText = btn:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    btn.rankText:SetPoint("BOTTOMRIGHT", -2, 2)
    btn.rankText:SetTextColor(1, 0.82, 0)
    
    -- Level req text
    btn.levelText = btn:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    btn.levelText:SetPoint("TOPRIGHT", -2, -2)
    btn.levelText:SetTextColor(0.5, 0.5, 0.5)
    
    -- Checkmark for learned
    btn.check = btn:CreateTexture(nil, "OVERLAY")
    btn.check:SetSize(16, 16)
    btn.check:SetPoint("BOTTOMLEFT", 0, 0)
    btn.check:SetTexture("Interface\\RaidFrame\\ReadyCheck-Ready")
    btn.check:Hide()
    
    -- Lock overlay for unavailable
    btn.lock = btn:CreateTexture(nil, "OVERLAY")
    btn.lock:SetAllPoints()
    btn.lock:SetTexture("Interface\\Buttons\\WHITE8X8")
    btn.lock:SetVertexColor(0.1, 0.1, 0.1, 0.7)
    btn.lock:Hide()
    
    return btn
end

-- Create a tab button
function Adv2.UI.CreateTab(parent, id, icon, tooltip, iconCoords)
    if not parent then
        DebugPrint("CreateTab: no parent!")
        return nil
    end
    
    local tab = CreateFrame("Button", nil, parent)
    tab:SetSize(42, 42)
    tab.id = id
    
    -- Background using proper 3.3.5a texture
    tab.bg = tab:CreateTexture(nil, "BACKGROUND")
    tab.bg:SetAllPoints()
    tab.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.bg:SetVertexColor(0.2, 0.2, 0.2, 0.8)
    
    -- Icon
    tab.icon = tab:CreateTexture(nil, "ARTWORK")
    tab.icon:SetPoint("TOPLEFT", 3, -3)
    tab.icon:SetPoint("BOTTOMRIGHT", -3, 3)
    tab.icon:SetTexture(icon)
    
    -- Use custom coords if provided (for class icon atlas)
    if iconCoords then
        tab.icon:SetTexCoord(iconCoords[1], iconCoords[2], iconCoords[3], iconCoords[4])
    else
        tab.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    end
    
    -- Selected indicator
    tab.selected = tab:CreateTexture(nil, "OVERLAY")
    tab.selected:SetPoint("TOPLEFT", -2, 2)
    tab.selected:SetPoint("BOTTOMRIGHT", 2, -2)
    tab.selected:SetTexture("Interface\\Buttons\\UI-ActionButton-Border")
    tab.selected:SetBlendMode("ADD")
    tab.selected:SetVertexColor(1, 0.82, 0)
    tab.selected:Hide()
    
    -- Highlight
    tab:SetHighlightTexture("Interface\\Buttons\\ButtonHilight-Square")
    tab:GetHighlightTexture():SetBlendMode("ADD")
    
    -- Tooltip
    if tooltip then
        tab:SetScript("OnEnter", function(self)
            GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
            GameTooltip:SetText(tooltip, 1, 1, 1)
            GameTooltip:Show()
        end)
        tab:SetScript("OnLeave", function()
            GameTooltip:Hide()
        end)
    end
    
    return tab
end

-- Create scrollable frame
function Adv2.UI.CreateScrollFrame(parent, name)
    local scrollFrame = CreateFrame("ScrollFrame", name, parent)
    
    local scrollChild = CreateFrame("Frame", nil, scrollFrame)
    scrollFrame:SetScrollChild(scrollChild)
    scrollChild:SetWidth(scrollFrame:GetWidth())
    scrollChild:SetHeight(1)  -- Will grow with content
    
    scrollFrame.scrollChild = scrollChild
    
    return scrollFrame
end

-- Create talent tier connector lines
function Adv2.UI.CreateConnectorLine(parent, fromBtn, toBtn, direction)
    if not parent or not fromBtn or not toBtn then return nil end
    
    local line = parent:CreateTexture(nil, "BACKGROUND")
    line:SetTexture("Interface\\Buttons\\WHITE8X8")
    line:SetVertexColor(0.5, 0.5, 0.5, 0.8)
    
    if direction == "down" then
        line:SetWidth(2)
        line:SetPoint("TOP", fromBtn, "BOTTOM", 0, 0)
        line:SetPoint("BOTTOM", toBtn, "TOP", 0, 0)
    elseif direction == "right" then
        line:SetHeight(2)
        line:SetPoint("LEFT", fromBtn, "RIGHT", 0, 0)
        line:SetPoint("RIGHT", toBtn, "LEFT", 0, 0)
    end
    
    return line
end

-- Format large numbers
function Adv2.UI.FormatNumber(n)
    if n >= 1000000 then
        return string.format("%.1fM", n / 1000000)
    elseif n >= 1000 then
        return string.format("%.1fK", n / 1000)
    end
    return tostring(n)
end

-- Set spell tooltip — prefer native hyperlink scan (full description, cost, range)
function Adv2.UI.SetSpellTooltip(spellId, name, desc)
    GameTooltip:ClearLines()

    if spellId then
        local link = GetSpellLink and GetSpellLink(spellId)
        if not link then
            link = "spell:" .. spellId
        end
        GameTooltip:SetHyperlink(link)
        if GameTooltip:NumLines() > 0 then
            return
        end
    end

    local spellName, rank = GetSpellInfo(spellId)
    local displayName = spellName or name or ("Spell #" .. tostring(spellId))
    GameTooltip:AddLine(displayName, 1, 1, 1)
    if rank and rank ~= "" then
        GameTooltip:AddLine(rank, 1, 1, 0.5)
    end

    local description = desc
    if (not description or description == "") and GetSpellDescription then
        description = GetSpellDescription(spellId)
    end
    if description and description ~= "" then
        GameTooltip:AddLine(" ")
        GameTooltip:AddLine(description, 1, 0.82, 0, true)
    end

    if Adv2.Config and Adv2.Config.DEBUG and spellId then
        GameTooltip:AddLine("ID: " .. tostring(spellId), 0.5, 0.5, 0.5)
    end
end

-- Use client item cache first; Constants icons are generic fallbacks only.
local function ResolveItemTexture(tex)
    if not tex then
        return nil
    end
    if type(tex) == "number" then
        return tex
    end
    if type(tex) == "string" and tex ~= "" then
        return tex
    end
    return nil
end

local function GetHeirloomItemIcon(item)
    if item and item.id and GetItemInfo then
        local _, _, _, _, _, _, _, _, _, tex = GetItemInfo(item.id)
        tex = ResolveItemTexture(tex)
        if tex then
            return tex
        end
        _, _, _, _, _, _, _, _, _, tex = GetItemInfo("item:" .. item.id)
        tex = ResolveItemTexture(tex)
        if tex then
            return tex
        end
    end
    if item and item.icon then
        return item.icon
    end
    return "Interface\\Icons\\INV_Misc_QuestionMark"
end

-- Set item tooltip — prefer native item hyperlink
function Adv2.UI.SetItemTooltip(itemId, name, slot, stats)
    GameTooltip:ClearLines()

    if itemId then
        local link = GetItemLink and GetItemLink(itemId)
        if not link then
            link = "item:" .. itemId
        end
        GameTooltip:SetHyperlink(link)
        if GameTooltip:NumLines() > 0 then
            return
        end
    end

    GameTooltip:AddLine(name or ("Item #" .. tostring(itemId)), 1, 0.82, 0)
    if slot and slot ~= "" then
        GameTooltip:AddLine(slot, 1, 1, 1)
    end
    if stats and stats ~= "" then
        GameTooltip:AddLine(stats, 0.7, 0.7, 0.7)
    end
    GameTooltip:AddLine(" ")
    GameTooltip:AddLine("Click to receive", 0.5, 0.8, 0.5)
end

function Adv2.UI.CreateHeirloomItemButton(parent, item)
    local size = Adv2.UI.HEIRLOOM_ICON_SIZE or 52
    local btn = CreateFrame("Button", nil, parent)
    btn:SetSize(size, size)
    btn.itemId = item.id
    btn.itemData = item

    btn.iconFrame = CreateFrame("Frame", nil, btn)
    btn.iconFrame:SetAllPoints()
    if btn.iconFrame.SetBackdrop then
        btn.iconFrame:SetBackdrop({
            edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
            edgeSize = 16,
            insets = { left = 4, right = 4, top = 4, bottom = 4 },
        })
        btn.iconFrame:SetBackdropBorderColor(0.52, 0.42, 0.28, 1)
    end

    btn.borderOuter = btn:CreateTexture(nil, "BACKGROUND", nil, 1)
    btn.borderOuter:SetPoint("TOPLEFT", btn.iconFrame, "TOPLEFT", -1, 1)
    btn.borderOuter:SetPoint("BOTTOMRIGHT", btn.iconFrame, "BOTTOMRIGHT", 1, -1)
    btn.borderOuter:SetTexture("Interface\\Buttons\\WHITE8X8")
    btn.borderOuter:SetVertexColor(0.18, 0.14, 0.10, 1)

    btn.bg = btn.iconFrame:CreateTexture(nil, "BACKGROUND")
    btn.bg:SetPoint("TOPLEFT", 4, -4)
    btn.bg:SetPoint("BOTTOMRIGHT", -4, 4)
    btn.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    btn.bg:SetVertexColor(0.10, 0.08, 0.06, 1)

    btn.icon = btn.iconFrame:CreateTexture(nil, "ARTWORK")
    btn.icon:SetPoint("TOPLEFT", 5, -5)
    btn.icon:SetPoint("BOTTOMRIGHT", -5, 5)
    btn.icon:SetTexture(GetHeirloomItemIcon(item))
    btn.icon:SetTexCoord(0.06, 0.94, 0.06, 0.94)

    btn.highlight = btn:CreateTexture(nil, "HIGHLIGHT")
    btn.highlight:SetAllPoints(btn.iconFrame)
    btn.highlight:SetTexture("Interface\\Buttons\\WHITE8X8")
    btn.highlight:SetVertexColor(0.55, 0.42, 0.18, 0.20)
    btn.highlight:SetBlendMode("ADD")

    btn:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        Adv2.UI.SetItemTooltip(self.itemId, self.itemData.name, self.itemData.slot, self.itemData.stats)
        GameTooltip:Show()
    end)
    btn:SetScript("OnLeave", function() GameTooltip:Hide() end)
    btn:SetScript("OnClick", function(self)
        if Adv2.GiveHeirloom then
            Adv2.GiveHeirloom(self.itemId)
        end
    end)
    btn:RegisterForClicks("LeftButtonUp", "RightButtonUp")

    return btn
end

-- Grimfall-style list row chrome (warm hover, dark row strip)
function Adv2.UI.StyleListRow(row, rowHeight, opts)
    opts = opts or {}
    row:SetHighlightTexture("Interface\\Buttons\\WHITE8X8")
    local highlight = row:GetHighlightTexture()
    if highlight then
        highlight:SetVertexColor(0.45, 0.35, 0.12, 0.35)
        highlight:SetBlendMode("ADD")
    end

    if not row.rowBg then
        row.rowBg = row:CreateTexture(nil, "BACKGROUND", nil, -1)
        row.rowBg:SetAllPoints()
        row.rowBg:SetTexture("Interface\\Buttons\\WHITE8X8")
    end
    row.rowBg:SetVertexColor(0.12, 0.10, 0.08, 0.96)

    if not row.selectBg then
        row.selectBg = row:CreateTexture(nil, "BACKGROUND", nil, 0)
        row.selectBg:SetAllPoints()
        row.selectBg:SetTexture("Interface\\Buttons\\WHITE8X8")
        row.selectBg:Hide()
    end

    if opts.bordered and not row.rowBorder then
        row.rowBorder = CreateFrame("Frame", nil, row)
        row.rowBorder:SetAllPoints()
        if row.rowBorder.SetBackdrop then
            row.rowBorder:SetBackdrop({
                edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
                edgeSize = 12,
                insets = { left = 2, right = 2, top = 2, bottom = 2 },
            })
            row.rowBorder:SetBackdropBorderColor(0.32, 0.26, 0.16, 0.75)
        end
    end
    if row.rowBorder then
        if opts.bordered then
            row.rowBorder:Show()
        else
            row.rowBorder:Hide()
        end
    end

    if rowHeight then
        row:SetHeight(rowHeight)
    end
end

-- Get spell name with fallback
function Adv2.UI.GetSpellName(spellId, fallbackName)
    local name = GetSpellInfo(spellId)
    if name and name ~= "" then
        return name
    end
    return fallbackName or ("Spell #" .. spellId)
end

-- Get spell icon with fallback
function Adv2.UI.GetSpellIcon(spellId, fallbackIcon)
    local _, _, icon = GetSpellInfo(spellId)
    if icon then
        return icon
    end
    return fallbackIcon or "Interface\\Icons\\INV_Misc_QuestionMark"
end

-- =========================================================================
-- Classless-style chrome (Grimfall UI-Background-Rock)
-- =========================================================================

local PARTS = "Interface\\TalentFrame\\TalentFrame-Parts"

local RING_ICON_COVERS = {
    { "TOPLEFT", "TOPLEFT", 0.26171875, 0.29296875, 0.85742188, 0.87304688 },
    { "TOPRIGHT", "TOPRIGHT", 0.22265625, 0.25390625, 0.85742188, 0.87304688 },
    { "BOTTOMLEFT", "BOTTOMLEFT", 0.91406250, 0.94531250, 0.59179688, 0.60742188 },
    { "BOTTOMRIGHT", "BOTTOMRIGHT", 0.95312500, 0.98437500, 0.59179688, 0.60742188 },
}

local function ApplyRingTabCornerCovers(tab, icon, innerSize)
    local coverSize = math.max(8, math.floor(innerSize * 0.20 + 0.5))
    if not tab.cornerCovers then
        tab.cornerCovers = {}
        for i, spec in ipairs(RING_ICON_COVERS) do
            local cover = tab:CreateTexture(nil, "ARTWORK", nil, -1)
            cover:SetTexture(PARTS)
            tab.cornerCovers[i] = { tex = cover, point = spec[1], relPoint = spec[2], coords = spec }
        end
    end
    for _, entry in ipairs(tab.cornerCovers) do
        local cover = entry.tex
        local c = entry.coords
        cover:SetSize(coverSize, coverSize)
        cover:SetPoint(entry.point, icon, entry.relPoint, 0, 0)
        cover:SetTexCoord(c[3], c[4], c[5], c[6])
        cover:Show()
    end
end

Adv2.UI.Art = Adv2.UI.Art or {
    Rock = TEX_ROCK,
    Marble = TEX_MARBLE,
}

function Adv2.UI.ApplyTiledTextureFill(frame, path, tint, left, top, right, bottom)
    ApplyTiledTextureFill(frame, path, tint, left, top, right, bottom)
end

function Adv2.UI.ApplySolidBackground(frame, r, g, b, a, left, top, right, bottom)
    ApplySolidFill(frame, r, g, b, a, left, top, right, bottom)
end

function Adv2.UI.ApplyRockBackground(frame, left, top, right, bottom, tint)
    ApplySolidFill(frame, 0.08, 0.07, 0.06, 1, left, top, right, bottom)
end

function Adv2.UI.ApplyMarbleBackground(frame, left, top, right, bottom, tint)
    ApplySolidFill(frame, 0.10, 0.09, 0.08, 1, left, top, right, bottom)
end

function Adv2.UI.ApplyClasslessFrameChrome(frame)
    ApplySolidFill(frame, 0.07, 0.06, 0.05, 1, 6, -6, -6, 6)
    ApplyEdgeBorder(frame, "Interface\\DialogFrame\\UI-DialogBox-Gold-Border")
end

function Adv2.UI.ApplyDialogBackdrop(frame)
    Adv2.UI.ApplyClasslessFrameChrome(frame)
end

function Adv2.UI.ApplyPanelBackground(frame, preset)
    if preset == "left" or preset == "right" then
        ApplySolidFill(frame, 0.10, 0.09, 0.08, 1, 0, 0, 0, 0)
    else
        ApplySolidFill(frame, 0.08, 0.07, 0.06, 1, 0, 0, 0, 0)
    end
end

function Adv2.UI.ApplyTiledBackground(parent, key, topAnchor, topPoint, topX, topY, bottomAnchor, bottomPoint, bottomX, bottomY, tint)
    local hostKey = key .. "Host"
    local host = parent[hostKey]
    if not host then
        host = CreateFrame("Frame", nil, parent)
        host:SetFrameLevel((parent.GetFrameLevel and parent:GetFrameLevel() or 0) + 1)
        host:EnableMouse(false)
        parent[hostKey] = host
    end
    host:ClearAllPoints()
    host:SetPoint("TOPLEFT", topAnchor, topPoint or "TOPLEFT", topX or 0, topY or 0)
    host:SetPoint("BOTTOMRIGHT", bottomAnchor, bottomPoint or "BOTTOMRIGHT", bottomX or 0, bottomY or 0)
    host:Show()
    ApplySolidFill(host, 0.08, 0.07, 0.06, 1, 0, 0, 0, 0)
end

function Adv2.UI.CreatePanelInset(parent, r, g, b, a, br, bg2, bb2, ba2)
    local inset = CreateFrame("Frame", nil, parent)
    inset:SetPoint("TOPLEFT", 4, -4)
    inset:SetPoint("BOTTOMRIGHT", -4, 4)
    local border = {
        br or 0.28, bg2 or 0.22, bb2 or 0.15, ba2 or 0.9,
    }
    Adv2.UI.CreateBackdrop(inset, { 1, 1, 1, 1 }, border)
    return inset
end

function Adv2.UI.CreateThemedInset(parent, preset)
    local t = Adv2.UI.Theme or {}
    if preset == "left" then
        local c = t.leftPanel or { 1, 1, 1, 1 }
        local b = t.leftBorder or { 0.30, 0.24, 0.16, 1 }
        return Adv2.UI.CreatePanelInset(parent, c[1], c[2], c[3], c[4], b[1], b[2], b[3], b[4])
    elseif preset == "right" then
        local c = t.rightPanel or { 1, 1, 1, 1 }
        local b = t.rightBorder or { 0.20, 0.17, 0.13, 1 }
        return Adv2.UI.CreatePanelInset(parent, c[1], c[2], c[3], c[4], b[1], b[2], b[3], b[4])
    elseif preset == "center" then
        local b = t.centerBorder or { 0.28, 0.22, 0.15, 0.85 }
        return Adv2.UI.CreatePanelInset(parent, 0, 0, 0, 0, b[1], b[2], b[3], b[4])
    end
    return Adv2.UI.CreatePanelInset(parent, 0.06, 0.05, 0.04, 0.9)
end

function Adv2.UI.CreateClassTab(parent, id, icon, label, iconCoords)
    local iconSize = Adv2.UI.CLASS_TAB_ICON_SIZE or 48
    local iconPad = 3
    local labelSpace = 16
    local tab = CreateFrame("Button", nil, parent)
    tab.id = id

    tab.iconFrame = CreateFrame("Frame", nil, tab)
    tab.iconFrame:EnableMouse(false)
    tab.iconFrame:SetSize(iconSize, iconSize)
    tab.iconFrame:SetPoint("TOP", 0, 0)
    if tab.iconFrame.SetBackdrop then
        tab.iconFrame:SetBackdrop({
            edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
            edgeSize = 16,
            insets = { left = 4, right = 4, top = 4, bottom = 4 },
        })
    end

    tab.borderOuter = tab:CreateTexture(nil, "BACKGROUND", nil, 0)
    tab.borderOuter:SetPoint("TOPLEFT", tab.iconFrame, "TOPLEFT", -1, 1)
    tab.borderOuter:SetPoint("BOTTOMRIGHT", tab.iconFrame, "BOTTOMRIGHT", 1, -1)
    tab.borderOuter:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.borderOuter:SetVertexColor(0.18, 0.14, 0.10, 1)

    tab.bg = tab.iconFrame:CreateTexture(nil, "BACKGROUND")
    tab.bg:SetPoint("TOPLEFT", 4, -4)
    tab.bg:SetPoint("BOTTOMRIGHT", -4, 4)
    tab.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.bg:SetVertexColor(0.10, 0.08, 0.06, 1)

    tab.icon = tab.iconFrame:CreateTexture(nil, "ARTWORK")
    tab.icon:SetPoint("TOPLEFT", iconPad + 1, -(iconPad + 1))
    tab.icon:SetPoint("BOTTOMRIGHT", -(iconPad + 1), iconPad + 1)
    tab.icon:SetTexture(icon)
    if iconCoords then
        tab.icon:SetTexCoord(iconCoords[1], iconCoords[2], iconCoords[3], iconCoords[4])
    else
        tab.icon:SetTexCoord(0.06, 0.94, 0.06, 0.94)
    end

    tab.selectGlow = tab.iconFrame:CreateTexture(nil, "OVERLAY")
    tab.selectGlow:SetAllPoints()
    tab.selectGlow:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.selectGlow:SetVertexColor(0.75, 0.58, 0.15, 0.30)
    tab.selectGlow:SetBlendMode("ADD")
    tab.selectGlow:Hide()

    tab.highlight = tab:CreateTexture(nil, "HIGHLIGHT")
    tab.highlight:SetPoint("TOPLEFT", tab.iconFrame, "TOPLEFT", 0, 0)
    tab.highlight:SetPoint("BOTTOMRIGHT", tab.iconFrame, "BOTTOMRIGHT", 0, 0)
    tab.highlight:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.highlight:SetVertexColor(0.55, 0.42, 0.18, 0.15)
    tab.highlight:SetBlendMode("ADD")

    tab.label = tab:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    tab.label:SetPoint("TOP", tab.iconFrame, "BOTTOM", 0, -2)
    tab.label:SetText(label or "")
    tab.label:SetTextColor(0.78, 0.68, 0.48)

    local function ApplyTabWidth(iconW)
        iconW = iconW or iconSize
        local labelW = tab.label:GetStringWidth() or 0
        local tabW = math.max(iconW, labelW + 8)
        tab:SetSize(tabW, iconW + labelSpace)
        tab.iconFrame:ClearAllPoints()
        tab.iconFrame:SetSize(iconW, iconW)
        tab.iconFrame:SetPoint("TOP", tab, "TOP", (tabW - iconW) / 2, 0)
        return tabW
    end

    ApplyTabWidth(iconSize)

    local function ApplyBorder(selected)
        if selected then
            tab.iconFrame:SetBackdropBorderColor(0.92, 0.74, 0.30, 1)
            tab.borderOuter:SetVertexColor(0.28, 0.22, 0.14, 1)
            tab.bg:SetVertexColor(0.20, 0.16, 0.10, 1)
            tab.selectGlow:Show()
            tab.label:SetTextColor(1, 0.82, 0)
        else
            tab.iconFrame:SetBackdropBorderColor(0.52, 0.42, 0.28, 1)
            tab.borderOuter:SetVertexColor(0.18, 0.14, 0.10, 1)
            tab.bg:SetVertexColor(0.10, 0.08, 0.06, 1)
            tab.selectGlow:Hide()
            tab.label:SetTextColor(0.78, 0.68, 0.48)
        end
    end

    ApplyBorder(false)

    function tab:SetSelected(selected)
        ApplyBorder(selected == true)
    end

    function tab:UpdateIconLayout(newSize)
        newSize = newSize or iconSize
        self.iconFrame:SetSize(newSize, newSize)
        ApplyTabWidth(newSize)
    end

    tab:RegisterForClicks("LeftButtonUp")
    tab:EnableMouse(true)

    return tab
end

Adv2.UI.CreateRingTab = Adv2.UI.CreateClassTab

function Adv2.UI.CreateGoldTab(parent, text, width)
    local tab = CreateFrame("Button", nil, parent)
    tab:SetSize(width or 88, 28)
    tab.text = tab:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    tab.text:SetPoint("CENTER", 0, 0)
    tab.text:SetText(text or "")

    tab.bg = tab:CreateTexture(nil, "BACKGROUND")
    tab.bg:SetAllPoints()
    tab.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.bg:SetVertexColor(0.16, 0.13, 0.10, 0.98)

    tab.border = tab:CreateTexture(nil, "OVERLAY", nil, -1)
    tab.border:SetPoint("TOPLEFT", -1, 1)
    tab.border:SetPoint("BOTTOMRIGHT", 1, -1)
    tab.border:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.border:SetVertexColor(0.38, 0.30, 0.20, 0.90)

    tab.highlight = tab:CreateTexture(nil, "HIGHLIGHT")
    tab.highlight:SetAllPoints()
    tab.highlight:SetTexture("Interface\\Buttons\\WHITE8X8")
    tab.highlight:SetVertexColor(0.55, 0.42, 0.18, 0.35)
    tab.highlight:SetBlendMode("ADD")

    function tab:SetSelected(selected)
        if selected then
            self.bg:SetVertexColor(0.68, 0.52, 0.24, 1)
            self.border:SetVertexColor(0.88, 0.70, 0.28, 1)
            self.text:SetTextColor(0.12, 0.08, 0.04)
        else
            self.bg:SetVertexColor(0.16, 0.13, 0.10, 0.98)
            self.border:SetVertexColor(0.38, 0.30, 0.20, 0.90)
            self.text:SetTextColor(0.78, 0.68, 0.48)
        end
    end

    tab:RegisterForClicks("LeftButtonUp")
    tab:EnableMouse(true)

    return tab
end

function Adv2.UI.CreateRoleButton(parent, role)
    local btn = CreateFrame("Button", nil, parent)
    btn:SetSize(26, 26)
    btn.role = role

    btn.bg = btn:CreateTexture(nil, "BACKGROUND")
    btn.bg:SetAllPoints()
    btn.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    btn.bg:SetVertexColor(0.08, 0.08, 0.1, 0.9)

    btn.icon = btn:CreateTexture(nil, "ARTWORK")
    btn.icon:SetSize(18, 18)
    btn.icon:SetPoint("CENTER")
    btn.icon:SetTexture("Interface\\LFGFrame\\UI-LFG-Icon-Roles")
    if role == "TANK" then
        btn.icon:SetTexCoord(0, 0.26171875, 0.26171875, 0.5234375)
    elseif role == "HEALER" then
        btn.icon:SetTexCoord(0.26171875, 0.5234375, 0, 0.26171875)
    else
        btn.icon:SetTexCoord(0.26171875, 0.5234375, 0.26171875, 0.5234375)
    end

    btn.border = btn:CreateTexture(nil, "OVERLAY")
    btn.border:SetPoint("TOPLEFT", -1, 1)
    btn.border:SetPoint("BOTTOMRIGHT", 1, -1)
    btn.border:SetTexture("Interface\\Buttons\\WHITE8X8")
    btn.border:SetVertexColor(0.35, 0.3, 0.22, 1)

    function btn:SetSelected(selected)
        if selected then
            self.border:SetVertexColor(0.95, 0.75, 0.15, 1)
            self.bg:SetVertexColor(0.18, 0.14, 0.06, 1)
        else
            self.border:SetVertexColor(0.35, 0.3, 0.22, 1)
            self.bg:SetVertexColor(0.08, 0.08, 0.1, 0.9)
        end
    end

    return btn
end

function Adv2.UI.UpdateSpellRowState(row, isLearned, isPending)
    if row.indicator then row.indicator:Hide() end
    if row.check then row.check:Hide() end

    local label = row.nameText or row.text
    local icon = row.icon

    if isLearned then
        if icon then
            icon:SetDesaturated(false)
            icon:SetVertexColor(0.35, 1, 0.35)
        end
        if label then label:SetTextColor(0.2, 1, 0.2) end
        if row.selectBg then
            row.selectBg:SetVertexColor(0.06, 0.20, 0.06, 0.75)
            row.selectBg:Show()
        end
    elseif isPending then
        if icon then
            icon:SetDesaturated(false)
            icon:SetVertexColor(1, 1, 0.45)
        end
        if label then label:SetTextColor(1, 1, 0.4) end
        if row.selectBg then
            row.selectBg:SetVertexColor(0.28, 0.24, 0.05, 0.85)
            row.selectBg:Show()
        end
    else
        if icon then
            icon:SetVertexColor(1, 1, 1)
            if icon.SetDesaturated then icon:SetDesaturated(true) end
        end
        if label then
            local gold = Adv2.UI.Theme and Adv2.UI.Theme.goldText or { 0.85, 0.75, 0.45 }
            label:SetTextColor(gold[1], gold[2], gold[3])
        end
        if row.selectBg then row.selectBg:Hide() end
        if row.rowBg then row.rowBg:SetVertexColor(0.12, 0.10, 0.08, 0.96) end
    end
end

-- Grimfall-style talent tree background (4-corner art + crimson glow + vignette)
function Adv2.UI.ApplyGrimfallTalentBackground(host, bgName)
    if not host.grimfallLayers then
        host.grimfallLayers = true
        host.bgBase = host:CreateTexture(nil, "BACKGROUND", nil, -3)
        host.bgBase:SetAllPoints()
        host.bgBase:SetTexture("Interface\\Buttons\\WHITE8X8")

        host.bgGlow = host:CreateTexture(nil, "BACKGROUND", nil, -2)
        host.bgGlow:SetTexture("Interface\\Buttons\\WHITE8X8")
        host.bgGlow:SetBlendMode("ADD")
        host.bgGlow:SetPoint("CENTER", 0, 0)

        host.bgVignette = host:CreateTexture(nil, "BORDER", nil, 2)
        host.bgVignette:SetAllPoints()
        host.bgVignette:SetTexture("Interface\\Buttons\\WHITE8X8")
        host.bgVignette:SetVertexColor(0, 0, 0, 0.30)
    end

    host.bgBase:SetVertexColor(0.025, 0.015, 0.015, 1)

    local glow = (Adv2.UI.SpecGlowTints and bgName and Adv2.UI.SpecGlowTints[bgName])
        or (Adv2.UI.Theme and Adv2.UI.Theme.specGlowDefault)
        or { 0.50, 0.08, 0.06, 0.40 }
    host.bgGlow:SetVertexColor(glow[1], glow[2], glow[3], glow[4] or 0.40)

    local w = host:GetWidth()
    local h = host:GetHeight()
    if w and w > 0 and h and h > 0 then
        host.bgGlow:SetSize(math.max(120, w * 0.72), math.max(160, h * 0.85))
    else
        host.bgGlow:SetSize(280, 420)
    end

    Adv2.UI.ApplyTalentFrameBackground(host, bgName, 0.82, 0.68, 0.68)
end

-- Four-corner talent tree background (Blizzard TalentFrame art)
function Adv2.UI.ApplyTalentFrameBackground(frame, bgName, tr, tg, tb)
    if not frame.bgTiles then
        frame.bgTiles = {}
        local function MakeTile(corner, anchorFn)
            local tex = frame:CreateTexture(nil, "BACKGROUND", nil, 0)
            frame.bgTiles[corner] = tex
            anchorFn(tex, frame)
        end

        MakeTile("TopLeft", function(tex, host)
            tex:SetPoint("TOPLEFT", host, "TOPLEFT", 0, 0)
            tex:SetPoint("RIGHT", host, "CENTER", 0, 0)
            tex:SetPoint("BOTTOM", host, "CENTER", 0, 0)
        end)
        MakeTile("TopRight", function(tex, host)
            tex:SetPoint("TOPRIGHT", host, "TOPRIGHT", 0, 0)
            tex:SetPoint("LEFT", host, "CENTER", 0, 0)
            tex:SetPoint("BOTTOM", host, "CENTER", 0, 0)
        end)
        MakeTile("BottomLeft", function(tex, host)
            tex:SetPoint("BOTTOMLEFT", host, "BOTTOMLEFT", 0, 0)
            tex:SetPoint("RIGHT", host, "CENTER", 0, 0)
            tex:SetPoint("TOP", host, "CENTER", 0, 0)
        end)
        MakeTile("BottomRight", function(tex, host)
            tex:SetPoint("BOTTOMRIGHT", host, "BOTTOMRIGHT", 0, 0)
            tex:SetPoint("LEFT", host, "CENTER", 0, 0)
            tex:SetPoint("TOP", host, "CENTER", 0, 0)
        end)
    end

    if not bgName then
        for _, tex in pairs(frame.bgTiles) do
            tex:Hide()
        end
        return
    end

    local prefix = "Interface\\TalentFrame\\" .. bgName
    frame.bgTiles.TopLeft:SetTexture(prefix .. "-TopLeft")
    frame.bgTiles.TopRight:SetTexture(prefix .. "-TopRight")
    frame.bgTiles.BottomLeft:SetTexture(prefix .. "-BottomLeft")
    frame.bgTiles.BottomRight:SetTexture(prefix .. "-BottomRight")
    tr, tg, tb = tr or 1, tg or 1, tb or 1
    for _, tex in pairs(frame.bgTiles) do
        tex:SetTexCoord(0, 1, 0, 1)
        tex:SetVertexColor(tr, tg, tb, 1)
        tex:Show()
    end
end

function Adv2.UI.SetupMouseWheelScroll(scrollFrame, scrollChild, step)
    step = step or 36
    scrollFrame:EnableMouseWheel(true)
    scrollFrame:SetScript("OnMouseWheel", function(self, delta)
        local current = self:GetVerticalScroll() or 0
        local maxScroll = math.max(0, (scrollChild:GetHeight() or 0) - (self:GetHeight() or 0))
        self:SetVerticalScroll(math.max(0, math.min(maxScroll, current - (delta * step))))
    end)
end

-- Heirlooms tab panel (kept in Helpers.lua so it always loads with the UI)
local HEIRLOOM_SECTIONS = { "Weapons", "Armor", "Trinkets" }

local function GetHeirloomCategory(item)
    if item.category then
        return item.category
    end
    local slot = item.slot or ""
    if slot == "Trinket" then
        return "Trinkets"
    end
    if slot:find("Shoulder") or slot:find("Chest") then
        return "Armor"
    end
    return "Weapons"
end

local function GroupHeirlooms(items)
    local grouped = { Weapons = {}, Armor = {}, Trinkets = {} }
    for _, item in ipairs(items or {}) do
        local cat = GetHeirloomCategory(item)
        if grouped[cat] then
            table.insert(grouped[cat], item)
        end
    end
    return grouped
end

function Adv2.UI.CreateHeirloomsPanel(parent)
    local panel = CreateFrame("Frame", nil, parent)
    Adv2.UI.CreateThemedInset(panel, "left")

    panel.header = CreateFrame("Frame", nil, panel)
    panel.header:SetPoint("TOPLEFT", 12, -12)
    panel.header:SetPoint("TOPRIGHT", -12, -12)
    panel.header:SetHeight(48)

    panel.title = panel.header:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    panel.title:SetPoint("TOPLEFT", 4, -2)
    panel.title:SetText("|cffa335eeHeirloom Equipment|r")

    panel.subtitle = panel.header:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    panel.subtitle:SetPoint("TOPLEFT", panel.title, "BOTTOMLEFT", 0, -4)
    panel.subtitle:SetText("Scales with your level. Click an icon to receive the item.")
    panel.subtitle:SetTextColor(0.65, 0.60, 0.52)

    panel.scrollFrame = CreateFrame("ScrollFrame", nil, panel)
    panel.scrollFrame:SetPoint("TOPLEFT", panel.header, "BOTTOMLEFT", 0, -8)
    panel.scrollFrame:SetPoint("BOTTOMRIGHT", -12, 12)

    panel.scrollChild = CreateFrame("Frame", nil, panel.scrollFrame)
    panel.scrollChild:SetWidth(math.max(400, (Adv2.UI.FRAME_WIDTH or 1040) - 80))
    panel.scrollChild:SetHeight(1)
    panel.scrollFrame:SetScrollChild(panel.scrollChild)

    Adv2.UI.SetupMouseWheelScroll(panel.scrollFrame, panel.scrollChild, 64)
    panel.itemButtons = {}
    panel.sectionLabels = {}

    function panel:RefreshIcons()
        for _, btn in ipairs(self.itemButtons) do
            if btn:IsShown() and btn.itemData then
                btn.icon:SetTexture(GetHeirloomItemIcon(btn.itemData))
            end
        end
    end

    function panel:LayoutItems(preserveScroll)
        local previousScroll = preserveScroll and (self.scrollFrame:GetVerticalScroll() or 0) or 0

        for _, btn in ipairs(self.itemButtons) do
            btn:Hide()
        end
        for _, label in ipairs(self.sectionLabels) do
            label:Hide()
        end

        local iconSize = Adv2.UI.HEIRLOOM_ICON_SIZE or 52
        local cols = Adv2.UI.HEIRLOOM_GRID_COLS or 8
        local gap = Adv2.UI.HEIRLOOM_GRID_GAP or 12
        local childWidth = self.scrollChild:GetWidth()
        if childWidth <= 0 then
            childWidth = (Adv2.UI.FRAME_WIDTH or 1040) - 52
        end
        self.scrollChild:SetWidth(childWidth)
        local gridWidth = cols * iconSize + math.max(0, cols - 1) * gap
        local gridLeft = math.max(0, (childWidth - gridWidth) / 2)

        local grouped = GroupHeirlooms(Adv2.Heirlooms)
        local y = 0
        local btnIndex = 0
        local sectionIndex = 0

        for _, sectionName in ipairs(HEIRLOOM_SECTIONS) do
            local items = grouped[sectionName]
            if items and #items > 0 then
                sectionIndex = sectionIndex + 1
                local sectionLabel = self.sectionLabels[sectionIndex]
                if not sectionLabel then
                    sectionLabel = self.scrollChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
                    self.sectionLabels[sectionIndex] = sectionLabel
                end
                sectionLabel:ClearAllPoints()
                sectionLabel:SetPoint("TOPLEFT", gridLeft, y)
                sectionLabel:SetText(sectionName)
                sectionLabel:SetTextColor(0.85, 0.75, 0.45)
                sectionLabel:Show()
                y = y - 22

                local col = 0
                for _, item in ipairs(items) do
                    btnIndex = btnIndex + 1
                    local btn = self.itemButtons[btnIndex]
                    if not btn then
                        btn = Adv2.UI.CreateHeirloomItemButton(self.scrollChild, item)
                        self.itemButtons[btnIndex] = btn
                    end
                    btn.itemId = item.id
                    btn.itemData = item
                    btn.icon:SetTexture(GetHeirloomItemIcon(item))
                    btn:ClearAllPoints()
                    btn:SetPoint("TOPLEFT", gridLeft + col * (iconSize + gap), y)
                    btn:Show()

                    col = col + 1
                    if col >= cols then
                        col = 0
                        y = y - (iconSize + gap)
                    end
                end

                if col > 0 then
                    y = y - (iconSize + gap)
                end
                y = y - 16
            end
        end

        self.scrollChild:SetHeight(math.max(1, math.abs(y) + 12))
        local maxScroll = math.max(0, self.scrollChild:GetHeight() - self.scrollFrame:GetHeight())
        if preserveScroll then
            if previousScroll > maxScroll then
                previousScroll = maxScroll
            end
            self.scrollFrame:SetVerticalScroll(previousScroll)
        else
            self.scrollFrame:SetVerticalScroll(0)
        end
    end

    local function PrimeHeirloomCache()
        for _, item in ipairs(Adv2.Heirlooms or {}) do
            if item.id then
                GetItemInfo(item.id)
            end
        end
    end

    function Adv2.UI.PrimeHeirloomItemCache()
        PrimeHeirloomCache()
    end

    local function RefreshHeirloomLayout(preserveScroll)
        local w = panel.scrollFrame:GetWidth()
        if not w or w <= 0 then
            w = panel:GetWidth() - 24
        end
        if w and w > 0 and panel._layoutWidth ~= w then
            panel._layoutWidth = w
            panel.scrollChild:SetWidth(w)
            preserveScroll = false
        end
        PrimeHeirloomCache()
        panel:LayoutItems(preserveScroll)
    end

    panel:SetScript("OnShow", function()
        panel._layoutWidth = nil
        Adv2.UI.DeferWhenSized(panel, function()
            RefreshHeirloomLayout(false)
        end)
        if not panel.iconRefreshFrame then
            panel.iconRefreshFrame = CreateFrame("Frame")
        end
        local elapsed = 0
        panel.iconRefreshFrame:SetScript("OnUpdate", function(self, dt)
            elapsed = elapsed + dt
            if elapsed >= 0.5 then
                self:SetScript("OnUpdate", nil)
                PrimeHeirloomCache()
                panel:RefreshIcons()
            end
        end)
    end)

    panel:SetScript("OnSizeChanged", function()
        if panel:IsShown() then
            Adv2.UI.Debounce(panel, "layout", 0.15, function()
                RefreshHeirloomLayout(true)
            end)
        end
    end)

    function panel:Update()
        RefreshHeirloomLayout(true)
    end

    return panel
end

-- Racials tab — known spellbook (stock AC) or morph gallery (custom servers)
local KNOWN_SPELL_ROW_H = Adv2.UI.SEARCH_ROW_HEIGHT or 34
local KNOWN_SPELL_ROW_GAP = Adv2.UI.SEARCH_ROW_GAP or 5
local KNOWN_SPELL_ICON = Adv2.UI.SEARCH_ICON_SIZE or 24
local KNOWN_SPELL_MAX_ROWS = 250

function Adv2.UI.CreateKnownSpellsPanel(parent)
    local panel = CreateFrame("Frame", nil, parent)
    panel:SetFrameLevel((parent.GetFrameLevel and parent:GetFrameLevel() or 0) + 5)

    panel.bg = panel:CreateTexture(nil, "BACKGROUND", nil, -2)
    panel.bg:SetAllPoints()
    panel.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    panel.bg:SetVertexColor(0.07, 0.06, 0.05, 1)

    if panel.SetBackdrop then
        panel:SetBackdrop({
            edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
            edgeSize = 16,
            insets = { left = 4, right = 4, top = 4, bottom = 4 },
        })
        panel:SetBackdropBorderColor(0.20, 0.17, 0.13, 1)
    end

    panel.header = panel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    panel.header:SetPoint("TOPLEFT", 12, -10)
    panel.header:SetText("|cffa335eeKnown Spells|r")

    panel.hint = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    panel.hint:SetPoint("TOPLEFT", panel.header, "BOTTOMLEFT", 0, -2)
    panel.hint:SetText("Spells currently in your spellbook.")
    panel.hint:SetTextColor(0.58, 0.54, 0.48)

    panel.countText = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    panel.countText:SetPoint("TOPRIGHT", -12, -10)
    panel.countText:SetTextColor(0.75, 0.70, 0.62)

    panel.scrollFrame = CreateFrame("ScrollFrame", nil, panel)
    panel.scrollFrame:SetPoint("TOPLEFT", 8, -42)
    panel.scrollFrame:SetPoint("BOTTOMRIGHT", -8, 8)

    panel.scrollChild = CreateFrame("Frame", nil, panel.scrollFrame)
    panel.scrollChild:SetWidth(400)
    panel.scrollChild:SetHeight(1)
    panel.scrollFrame:SetScrollChild(panel.scrollChild)

    Adv2.UI.SetupMouseWheelScroll(panel.scrollFrame, panel.scrollChild, KNOWN_SPELL_ROW_H + KNOWN_SPELL_ROW_GAP)
    panel.rowPool = {}

    function panel:AcquireRow(index, rowWidth)
        local row = self.rowPool[index]
        if not row then
            row = CreateFrame("Button", nil, self.scrollChild)
            Adv2.UI.StyleListRow(row, KNOWN_SPELL_ROW_H, { bordered = true })
            row.icon = row:CreateTexture(nil, "ARTWORK")
            row.icon:SetSize(KNOWN_SPELL_ICON, KNOWN_SPELL_ICON)
            row.icon:SetPoint("LEFT", 6, 0)
            row.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
            row.text = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
            row.text:SetPoint("LEFT", row.icon, "RIGHT", 8, 0)
            row.text:SetJustifyH("LEFT")
            row.text:SetTextColor(0.85, 0.75, 0.45)
            row:SetScript("OnEnter", function(self)
                GameTooltip:SetOwner(self, "ANCHOR_LEFT")
                Adv2.UI.SetSpellTooltip(self.spellId, self.spellName)
                GameTooltip:Show()
            end)
            row:SetScript("OnLeave", function() GameTooltip:Hide() end)
            self.rowPool[index] = row
        end
        row:SetSize(rowWidth, KNOWN_SPELL_ROW_H)
        row.text:SetWidth(rowWidth - KNOWN_SPELL_ICON - 20)
        row:Show()
        return row
    end

    function panel:RefreshList()
        local spells = Adv2.CollectSpellbookSpells and Adv2.CollectSpellbookSpells() or {}
        local rowWidth = math.max(200, (self:GetWidth() or 420) - 36)
        self.scrollChild:SetWidth(rowWidth + 16)

        local y = 0
        local shown = 0
        for _, spell in ipairs(spells) do
            shown = shown + 1
            if shown > KNOWN_SPELL_MAX_ROWS then
                break
            end
            local row = self:AcquireRow(shown, rowWidth)
            row:ClearAllPoints()
            row:SetPoint("TOPLEFT", 2, y)
            row.icon:SetTexture(spell.icon or "Interface\\Icons\\INV_Misc_QuestionMark")
            local label = spell.name or ("Spell " .. tostring(spell.id))
            if spell.rank and spell.rank ~= "" then
                label = label .. " (" .. spell.rank .. ")"
            end
            row.text:SetText(label)
            row.spellId = spell.id
            row.spellName = spell.name
            y = y - (KNOWN_SPELL_ROW_H + KNOWN_SPELL_ROW_GAP)
        end

        for i = shown + 1, #self.rowPool do
            self.rowPool[i]:Hide()
        end

        self.scrollChild:SetHeight(math.max(1, math.abs(y) + 8))
        self.scrollFrame:SetVerticalScroll(0)
        self.countText:SetText(tostring(#spells) .. " spells")
    end

    panel:SetScript("OnShow", function()
        Adv2.UI.DeferWhenSized(panel, function()
            panel:RefreshList()
        end)
    end)

    panel:SetScript("OnSizeChanged", function()
        if panel:IsShown() then
            Adv2.UI.Debounce(panel, "layout", 0.15, function()
                panel:RefreshList()
            end)
        end
    end)

    function panel:Update()
        self:RefreshList()
    end

    return panel
end

-- Racials tab — morph gallery (custom servers only)
local MORPH_CARD_W = 148
local MORPH_CARD_H = 158
local MORPH_CARD_GAP = 8
local MORPH_COLS = 3
local MORPH_ROWS = 3
local MORPH_PAGE_SIZE = MORPH_COLS * MORPH_ROWS
local MORPH_MODEL_H = 108
local MORPH_GRID_H = MORPH_ROWS * MORPH_CARD_H + (MORPH_ROWS - 1) * MORPH_CARD_GAP
local MORPH_MID_ROW_Y = -(MORPH_CARD_H + MORPH_CARD_GAP) - (MORPH_CARD_H / 2)

local function ClearPreviewModel(model)
    if not model then return end
    if model.ClearModel then
        pcall(function() model:ClearModel() end)
    end
end

local function SetMorphPreviewModel(model, morph)
    if not model or not morph then return end
    ClearPreviewModel(model)
    local ok = false
    if morph.model and model.SetModel then
        ok = pcall(function()
            model:SetModel(morph.model)
        end)
    end
    if not ok and morph.id and model.SetCreature then
        pcall(function()
            model:SetCreature(morph.id)
        end)
    end
    if model.SetRotation then
        model:SetRotation(0.45)
    end
    if model.SetPosition then
        pcall(function() model:SetPosition(0, 0, 0) end)
    end
end

local function UpdateMorphCardState(card, activeId)
    if not card then return end
    local selected = activeId and card.morphId == activeId
    if card.border then
        if selected then
            card.border:SetBackdropBorderColor(0.95, 0.78, 0.22, 1)
        else
            card.border:SetBackdropBorderColor(0.42, 0.34, 0.24, 1)
        end
    end
    if card.nameText then
        if selected then
            card.nameText:SetTextColor(0.95, 0.82, 0.35)
        else
            card.nameText:SetTextColor(0.88, 0.84, 0.76)
        end
    end
    if card.RefreshMacro then
        card:RefreshMacro()
    end
end

function Adv2.UI.CreateMorphCard(parent, morph)
    local card = CreateFrame("Button", nil, parent, "SecureActionButtonTemplate")
    card:SetSize(MORPH_CARD_W, MORPH_CARD_H)
    card.morphId = morph.id
    card.morphData = morph
    card:RegisterForClicks("LeftButtonUp")
    card:SetAttribute("type", "macro")

    function card:RefreshMacro()
        local id = self.morphId
        if not id then return end
        if Adv2.activeMorphId == id then
            self:SetAttribute("macrotext", ".morph reset")
        else
            self:SetAttribute("macrotext", "/target player\n.morph target " .. tostring(id))
        end
    end
    card:RefreshMacro()

    card.bg = card:CreateTexture(nil, "BACKGROUND")
    card.bg:SetAllPoints()
    card.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    card.bg:SetVertexColor(0.08, 0.06, 0.05, 0.92)

    card.border = CreateFrame("Frame", nil, card)
    card.border:SetAllPoints()
    card.border:EnableMouse(false)
    if card.border.SetBackdrop then
        card.border:SetBackdrop({
            edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
            edgeSize = 12,
            insets = { left = 3, right = 3, top = 3, bottom = 3 },
        })
        card.border:SetBackdropBorderColor(0.42, 0.34, 0.24, 1)
    end

    card.modelFrame = CreateFrame("Frame", nil, card)
    card.modelFrame:SetPoint("TOPLEFT", 8, -8)
    card.modelFrame:SetPoint("TOPRIGHT", -8, -8)
    card.modelFrame:SetHeight(MORPH_MODEL_H)
    card.modelFrame:EnableMouse(false)
    card.modelBg = card.modelFrame:CreateTexture(nil, "BACKGROUND")
    card.modelBg:SetAllPoints()
    card.modelBg:SetTexture("Interface\\Buttons\\WHITE8X8")
    card.modelBg:SetVertexColor(0.04, 0.03, 0.02, 1)

    card.model = CreateFrame("PlayerModel", nil, card.modelFrame)
    card.model:SetAllPoints()
    card.model:EnableMouse(false)
    SetMorphPreviewModel(card.model, morph)

    card.nameText = card:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    card.nameText:SetPoint("TOP", card.modelFrame, "BOTTOM", 0, -6)
    card.nameText:SetWidth(MORPH_CARD_W - 16)
    card.nameText:SetJustifyH("CENTER")
    card.nameText:SetText(morph.name or ("Morph " .. tostring(morph.id)))

    card:SetScript("OnEnter", function()
        GameTooltip:SetOwner(card, "ANCHOR_RIGHT")
        GameTooltip:SetText(morph.name or "Morph", 1, 0.82, 0)
        GameTooltip:AddLine("Targets you, then morphs", 0.7, 0.7, 0.7)
        GameTooltip:AddLine("Click again to reset", 0.7, 0.7, 0.7)
        GameTooltip:Show()
    end)
    card:SetScript("OnLeave", function() GameTooltip:Hide() end)
    card:SetScript("PostClick", function(self)
        local id = self.morphId
        if not id then return end
        if Adv2.activeMorphId == id then
            Adv2.activeMorphId = nil
            print("|cff00ff00[Multiclass]|r Morph reset.")
        else
            Adv2.activeMorphId = id
            print("|cff00ff00[Multiclass]|r Morph: " .. (Adv2.GetMorphName and Adv2.GetMorphName(id) or tostring(id)))
        end
        if Adv2.MainFrame and Adv2.MainFrame.morphPanel and Adv2.MainFrame.morphPanel.UpdateSelection then
            Adv2.MainFrame.morphPanel:UpdateSelection()
        end
    end)

    return card
end

function Adv2.UI.CreateMorphPanel(parent)
    local panel = CreateFrame("Frame", nil, parent)
    panel:SetFrameLevel((parent.GetFrameLevel and parent:GetFrameLevel() or 0) + 5)
    panel.currentPage = 1

    panel.bg = panel:CreateTexture(nil, "BACKGROUND", nil, -2)
    panel.bg:SetAllPoints()
    panel.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    panel.bg:SetVertexColor(0.07, 0.06, 0.05, 1)

    if panel.SetBackdrop then
        panel:SetBackdrop({
            edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
            edgeSize = 16,
            insets = { left = 4, right = 4, top = 4, bottom = 4 },
        })
        panel:SetBackdropBorderColor(0.20, 0.17, 0.13, 1)
    end

    panel.header = panel:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    panel.header:SetPoint("TOPLEFT", 12, -10)
    panel.header:SetText("|cffa335eeCharacter Morphs|r")

    panel.hint = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    panel.hint:SetPoint("TOPLEFT", panel.header, "BOTTOMLEFT", 0, -2)
    panel.hint:SetText("Click a preview to morph. Click again to reset.")
    panel.hint:SetTextColor(0.58, 0.54, 0.48)

    panel.emptyText = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlight")
    panel.emptyText:SetPoint("CENTER")
    panel.emptyText:SetText("No morph data loaded.")
    panel.emptyText:SetTextColor(0.7, 0.65, 0.55)
    panel.emptyText:Hide()

    panel.gridHost = CreateFrame("Frame", nil, panel)
    panel.gridHost:SetPoint("TOPLEFT", 8, -42)
    panel.gridHost:SetPoint("TOPRIGHT", -8, -42)
    panel.gridHost:SetHeight(MORPH_GRID_H)
    panel.gridHost:SetFrameLevel(panel:GetFrameLevel() + 1)

    panel.gridBg = panel.gridHost:CreateTexture(nil, "BACKGROUND")
    panel.gridBg:SetAllPoints()
    panel.gridBg:SetTexture("Interface\\Buttons\\WHITE8X8")
    panel.gridBg:SetVertexColor(0.06, 0.05, 0.04, 1)

    panel.prevBtn = CreateFrame("Button", nil, panel, "UIPanelButtonTemplate")
    panel.prevBtn:SetSize(56, 24)
    panel.prevBtn:SetFrameLevel(panel:GetFrameLevel() + 20)
    panel.prevBtn:SetText("Prev")

    panel.nextBtn = CreateFrame("Button", nil, panel, "UIPanelButtonTemplate")
    panel.nextBtn:SetSize(56, 24)
    panel.nextBtn:SetFrameLevel(panel:GetFrameLevel() + 20)
    panel.nextBtn:SetText("Next")

    panel.pageText = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    panel.pageText:SetPoint("TOP", panel.gridHost, "BOTTOM", 0, -8)
    panel.pageText:SetTextColor(0.75, 0.70, 0.62)

    panel.cards = {}

    function panel:LayoutNavButtons(gridLeft, gridWidth)
        self.prevBtn:ClearAllPoints()
        self.prevBtn:SetPoint("RIGHT", self.gridHost, "TOPLEFT", gridLeft - 6, MORPH_MID_ROW_Y)
        self.nextBtn:ClearAllPoints()
        self.nextBtn:SetPoint("LEFT", self.gridHost, "TOPLEFT", gridLeft + gridWidth + 6, MORPH_MID_ROW_Y)
    end

    function panel:LayoutCards()
        local morphs = Adv2.Morphs or {}
        local total = #morphs
        if total == 0 then
            for _, card in ipairs(self.cards) do
                card:Hide()
            end
            self.emptyText:Show()
            self.pageText:SetText("")
            self.prevBtn:Disable()
            self.nextBtn:Disable()
            return
        end
        self.emptyText:Hide()

        local totalPages = math.max(1, math.ceil(total / MORPH_PAGE_SIZE))
        if self.currentPage > totalPages then
            self.currentPage = totalPages
        end
        if self.currentPage < 1 then
            self.currentPage = 1
        end

        local startIdx = (self.currentPage - 1) * MORPH_PAGE_SIZE + 1
        local hostWidth = self.gridHost:GetWidth()
        if not hostWidth or hostWidth <= 0 then
            hostWidth = 480
        end

        local gridWidth = MORPH_COLS * MORPH_CARD_W + math.max(0, MORPH_COLS - 1) * MORPH_CARD_GAP
        local gridLeft = math.max(0, (hostWidth - gridWidth) / 2)
        self:LayoutNavButtons(gridLeft, gridWidth)

        for slot = 1, MORPH_PAGE_SIZE do
            local morphIndex = startIdx + slot - 1
            local morph = morphs[morphIndex]
            local card = self.cards[slot]
            if morph then
                if not card then
                    local ok, built = pcall(Adv2.UI.CreateMorphCard, self.gridHost, morph)
                    if ok and built then
                        card = built
                        self.cards[slot] = card
                    end
                end
                if card then
                    card.morphId = morph.id
                    card.morphData = morph
                    if card.nameText then
                        card.nameText:SetText(morph.name or ("Morph " .. tostring(morph.id)))
                    end
                    SetMorphPreviewModel(card.model, morph)
                    card:RefreshMacro()
                    local col = (slot - 1) % MORPH_COLS
                    local row = math.floor((slot - 1) / MORPH_COLS)
                    card:ClearAllPoints()
                    card:SetPoint("TOPLEFT", gridLeft + col * (MORPH_CARD_W + MORPH_CARD_GAP), -(row * (MORPH_CARD_H + MORPH_CARD_GAP)))
                    card:Show()
                    UpdateMorphCardState(card, Adv2.activeMorphId)
                end
            elseif card then
                card:Hide()
            end
        end

        self.pageText:SetText(string.format("Page %d / %d", self.currentPage, totalPages))
        if self.currentPage <= 1 then
            self.prevBtn:Disable()
        else
            self.prevBtn:Enable()
        end
        if self.currentPage >= totalPages then
            self.nextBtn:Disable()
        else
            self.nextBtn:Enable()
        end
    end

    panel.prevBtn:SetScript("OnClick", function()
        if panel.currentPage > 1 then
            panel.currentPage = panel.currentPage - 1
            panel:LayoutCards()
        end
    end)

    panel.nextBtn:SetScript("OnClick", function()
        local morphs = Adv2.Morphs or {}
        local totalPages = math.max(1, math.ceil(#morphs / MORPH_PAGE_SIZE))
        if panel.currentPage < totalPages then
            panel.currentPage = panel.currentPage + 1
            panel:LayoutCards()
        end
    end)

    function panel:UpdateSelection()
        for _, card in ipairs(self.cards) do
            if card:IsShown() then
                UpdateMorphCardState(card, Adv2.activeMorphId)
            end
        end
    end

    function panel:RefreshLayout()
        self:LayoutCards()
    end

    panel:SetScript("OnShow", function()
        if not panel.refreshFrame then
            panel.refreshFrame = CreateFrame("Frame")
        end
        panel.refreshFrame:SetScript("OnUpdate", function(self)
            self:SetScript("OnUpdate", nil)
            panel:RefreshLayout()
        end)
    end)

    function panel:Update()
        self:UpdateSelection()
    end

    return panel
end
