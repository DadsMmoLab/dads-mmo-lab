-- Right column: searchable global spell list (Grimfall-style rows)

Adv2 = Adv2 or {}

Adv2.UI = Adv2.UI or {}



local MAX_LIST_ROWS = 120

local ROW_H = Adv2.UI.SEARCH_ROW_HEIGHT or 34

local ROW_GAP = Adv2.UI.SEARCH_ROW_GAP or 5

local ICON_SIZE = Adv2.UI.SEARCH_ICON_SIZE or 24

local ROW_INSET = 2



function Adv2.UI.CreateSearchSidebar(parent)

    local panel = CreateFrame("Frame", nil, parent)

    panel:SetWidth(Adv2.UI.RIGHT_COL_WIDTH or 260)



    Adv2.UI.CreateThemedInset(panel, "right")



    panel.header = CreateFrame("Frame", nil, panel)

    panel.header:SetPoint("TOPLEFT", 8, -8)

    panel.header:SetPoint("TOPRIGHT", -8, -8)

    panel.header:SetHeight(30)



    panel.searchBox = CreateFrame("EditBox", nil, panel.header)

    panel.searchBox:SetPoint("TOPLEFT", 0, 0)

    panel.searchBox:SetPoint("TOPRIGHT", 0, 0)

    panel.searchBox:SetHeight(24)

    panel.searchBox:SetFontObject(ChatFontNormal)

    panel.searchBox:SetTextInsets(8, 8, 0, 0)

    panel.searchBox:EnableKeyboard(false)

    panel.searchBox:SetText("")

    panel.searchBg = panel.searchBox:CreateTexture(nil, "BACKGROUND")

    panel.searchBg:SetAllPoints()

    panel.searchBg:SetTexture("Interface\\Buttons\\WHITE8X8")

    panel.searchBg:SetVertexColor(0.06, 0.05, 0.04, 1)

    panel.searchBorder = CreateFrame("Frame", nil, panel.searchBox)

    panel.searchBorder:SetPoint("TOPLEFT", -1, 1)

    panel.searchBorder:SetPoint("BOTTOMRIGHT", 1, -1)

    if panel.searchBorder.SetBackdrop then
        panel.searchBorder:SetBackdrop({
            edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
            edgeSize = 12,
            insets = { left = 2, right = 2, top = 2, bottom = 2 },
        })
        panel.searchBorder:SetBackdropBorderColor(0.28, 0.24, 0.18, 0.9)
    end

    panel.searchBox:SetScript("OnTextChanged", function() panel:RefreshList() end)

    panel.searchBox:SetScript("OnEscapePressed", function(self) self:ClearFocus(); self:EnableKeyboard(false) end)

    panel.searchBox:SetScript("OnEnterPressed", function(self) self:ClearFocus(); self:EnableKeyboard(false) end)

    panel.searchBox:SetScript("OnEditFocusLost", function(self)

        self:EnableKeyboard(false)

        if self:GetText() == "" and panel.searchPlaceholder then panel.searchPlaceholder:Show() end

    end)

    panel.searchBox:SetScript("OnEditFocusGained", function() if panel.searchPlaceholder then panel.searchPlaceholder:Hide() end end)

    panel.searchBox:SetScript("OnMouseDown", function(self) self:EnableKeyboard(true); self:SetFocus() end)



    panel.searchPlaceholder = panel.header:CreateFontString(nil, "OVERLAY", "GameFontDisableSmall")

    panel.searchPlaceholder:SetPoint("LEFT", panel.searchBox, "LEFT", 10, 0)

    panel.searchPlaceholder:SetText("Search")

    panel.searchPlaceholder:SetTextColor(0.45, 0.42, 0.38)



    panel.scrollFrame = CreateFrame("ScrollFrame", nil, panel)

    panel.scrollFrame:SetPoint("TOPLEFT", panel.header, "BOTTOMLEFT", 0, -8)

    panel.scrollFrame:SetPoint("BOTTOMRIGHT", -8, 8)



    panel.listBgFrame = CreateFrame("Frame", nil, panel)

    panel.listBgFrame:SetPoint("TOPLEFT", panel.scrollFrame, "TOPLEFT", 0, 0)

    panel.listBgFrame:SetPoint("BOTTOMRIGHT", panel.scrollFrame, "BOTTOMRIGHT", 0, 0)

    panel.listBgFrame:SetFrameLevel(panel.scrollFrame:GetFrameLevel() - 1)

    Adv2.UI.ApplyMarbleBackground(panel.listBgFrame, 0, 0, 0, 0, { 0.42, 0.36, 0.28, 1 })



    panel.scrollChild = CreateFrame("Frame", nil, panel.scrollFrame)

    panel.scrollChild:SetWidth((Adv2.UI.RIGHT_COL_WIDTH or 260) - 28)

    panel.scrollChild:SetHeight(1)

    panel.scrollFrame:SetScrollChild(panel.scrollChild)



    Adv2.UI.SetupMouseWheelScroll(panel.scrollFrame, panel.scrollChild, ROW_H + ROW_GAP)

    panel.rowPool = {}



    function panel:ReleaseSearchFocus()

        if self.searchBox then self.searchBox:ClearFocus(); self.searchBox:EnableKeyboard(false) end

    end



    function panel:GetRowWidth()

        return self.scrollChild:GetWidth() - (ROW_INSET * 2)

    end



    function panel:AcquireRow(index)

        local row = self.rowPool[index]

        local rowWidth = self:GetRowWidth()

        if not row then

            row = CreateFrame("Button", nil, self.scrollChild)

            Adv2.UI.StyleListRow(row, ROW_H, { bordered = true })

            row.icon = row:CreateTexture(nil, "ARTWORK")

            row.icon:SetSize(ICON_SIZE, ICON_SIZE)

            row.icon:SetPoint("LEFT", 6, 0)

            row.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)

            row.text = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")

            row.text:SetPoint("LEFT", row.icon, "RIGHT", 8, 0)

            row.text:SetJustifyH("LEFT")

            row.text:SetTextColor(0.85, 0.75, 0.45)

            row:SetScript("OnEnter", function(self)

                GameTooltip:SetOwner(self, "ANCHOR_LEFT")

                Adv2.UI.SetSpellTooltip(self.spellId, self.spellData and self.spellData.name, self.spellData and self.spellData.desc)

                GameTooltip:Show()

            end)

            row:SetScript("OnLeave", function() GameTooltip:Hide() end)

            self.rowPool[index] = row

        end

        row:SetSize(rowWidth, ROW_H)

        row.text:SetWidth(rowWidth - ICON_SIZE - 20)

        row:Show()

        return row

    end



    function panel:RefreshList()

        local sidebar = self

        local query = string.lower(self.searchBox:GetText() or "")

        local y = 0

        local shown = 0

        local spells = Adv2.Data.GetAllPickSpellsFlat and Adv2.Data.GetAllPickSpellsFlat() or {}



        for _, spell in ipairs(spells) do

            local entryName = spell.name or ""

            if query == "" or string.find(string.lower(entryName), query, 1, true) then

                shown = shown + 1

                if shown > MAX_LIST_ROWS then break end

                local row = self:AcquireRow(shown)

                row:ClearAllPoints()

                row:SetPoint("TOPLEFT", ROW_INSET, y)

                y = y - (ROW_H + ROW_GAP)

                row.icon:SetTexture(spell.icon or "Interface\\Icons\\INV_Misc_QuestionMark")

                row.text:SetText(entryName)

                row.spellId = spell.id

                row.spellData = spell

                row:EnableMouse(true)

                row:RegisterForClicks("LeftButtonUp", "RightButtonUp")

                row:SetScript("OnClick", function()

                    if sidebar.onSpellSelect and spell.id then

                        sidebar.onSpellSelect(spell)

                    end

                end)

            end

        end



        for i = shown + 1, #self.rowPool do self.rowPool[i]:Hide() end

        self.scrollChild:SetHeight(math.max(1, math.abs(y) + 8))

        self:Update()

    end



    function panel:Update()

        for _, row in ipairs(self.rowPool) do

            if row:IsShown() and row.spellId then

                local learned = Adv2.playerData.learnedAbilities[row.spellId]

                local pending = Adv2.IsAbilityPending and Adv2.IsAbilityPending(row.spellId)

                Adv2.UI.UpdateSpellRowState(row, learned, pending)

            end

        end

    end



    panel:RefreshList()

    return panel

end

