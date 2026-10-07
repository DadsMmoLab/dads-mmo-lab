-- Left column: Grimfall-style spell list (single column, row pooling)
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

local ROW_H = 40

function Adv2.UI.CreateSpellGridPanel(parent)
    local panel = CreateFrame("Frame", nil, parent)
    panel:SetWidth(Adv2.UI.LEFT_COL_WIDTH or 340)

    Adv2.UI.CreateThemedInset(panel, "left")

    panel.scrollFrame = CreateFrame("ScrollFrame", nil, panel)
    panel.scrollFrame:SetPoint("TOPLEFT", 8, -8)
    panel.scrollFrame:SetPoint("BOTTOMRIGHT", -8, 8)

    panel.scrollChild = CreateFrame("Frame", nil, panel.scrollFrame)
    panel.scrollChild:SetWidth((Adv2.UI.LEFT_COL_WIDTH or 340) - 24)
    panel.scrollChild:SetHeight(1)
    panel.scrollFrame:SetScrollChild(panel.scrollChild)

    Adv2.UI.SetupMouseWheelScroll(panel.scrollFrame, panel.scrollChild, 36)
    panel.rowPool = {}
    panel.spellData = {}
    panel.classId = nil
    panel.mode = "class"
    panel.clickHandler = nil

    local rowWidth = panel.scrollChild:GetWidth()

    local function SyncRowWidth()
        local w = panel.scrollFrame:GetWidth()
        if not w or w <= 0 then
            w = panel:GetWidth() - 24
        end
        if w and w > 0 then
            rowWidth = w
            panel.scrollChild:SetWidth(w)
            for _, row in ipairs(panel.rowPool) do
                row:SetWidth(rowWidth)
                if row.nameText then
                    row.nameText:SetWidth(rowWidth - 52)
                end
            end
        end
    end

    local function AcquireRow(index)
        local row = panel.rowPool[index]
        if not row then
            row = CreateFrame("Button", nil, panel.scrollChild)
            row:SetSize(rowWidth, ROW_H - 2)
            row.icon = row:CreateTexture(nil, "ARTWORK")
            row.icon:SetSize(32, 32)
            row.icon:SetPoint("LEFT", 4, 0)
            row.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
            row.nameText = row:CreateFontString(nil, "OVERLAY", "GameFontNormal")
            row.nameText:SetPoint("TOPLEFT", row.icon, "TOPRIGHT", 8, -2)
            row.nameText:SetWidth(rowWidth - 52)
            row.nameText:SetJustifyH("LEFT")
            row.levelText = row:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
            row.levelText:SetPoint("TOPLEFT", row.nameText, "BOTTOMLEFT", 0, -1)
            row.levelText:SetTextColor(0.55, 0.55, 0.55)
            Adv2.UI.StyleListRow(row, ROW_H - 2)
            row:SetScript("OnEnter", function(self)
                GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
                Adv2.UI.SetSpellTooltip(self.spellId, self.spellData and self.spellData.name, self.spellData and self.spellData.desc)
                GameTooltip:Show()
            end)
            row:SetScript("OnLeave", function() GameTooltip:Hide() end)
            panel.rowPool[index] = row
        end
        row:Show()
        return row
    end

    local function HideExtraRows(used)
        for i = used + 1, #panel.rowPool do
            panel.rowPool[i]:Hide()
        end
    end

    function panel:LayoutSpells(spells, clickHandler)
        SyncRowWidth()
        self.clickHandler = clickHandler
        self.spellData = spells or {}

        local y = 0
        local used = 0

        for _, spell in ipairs(self.spellData) do
            used = used + 1
            local row = AcquireRow(used)
            row:ClearAllPoints()
            row:SetPoint("TOPLEFT", 0, y)
            y = y - ROW_H
            row.spellId = spell.id
            row.spellData = spell
            row.icon:SetTexture(spell.icon or "Interface\\Icons\\INV_Misc_QuestionMark")
            row.nameText:SetText(spell.name or ("Spell " .. spell.id))
            row.levelText:SetText("Level " .. (spell.level or 1))
            row:SetScript("OnClick", function()
                if clickHandler then clickHandler(row) end
            end)
        end

        HideExtraRows(used)
        self.scrollChild:SetHeight(math.max(1, math.abs(y) + 8))
        self.scrollFrame:SetVerticalScroll(0)
        self:Update()
    end

    function panel:SetClassSpells(classId, clickHandler)
        self.classId = classId
        self.mode = "class"
        local spells = Adv2.Data.GetPickSpells and Adv2.Data.GetPickSpells(classId)
            or (Adv2.Data.CoreSpells and Adv2.Data.CoreSpells[classId])
            or {}
        local sorted = {}
        for _, spell in ipairs(spells) do
            table.insert(sorted, spell)
        end
        table.sort(sorted, function(a, b)
            return (a.name or ""):lower() < (b.name or ""):lower()
        end)
        self:LayoutSpells(sorted, clickHandler)
    end

    function panel:SetRacialSpells(clickHandler)
        self.mode = "racials"
        self.clickHandler = clickHandler
        local entries = {}
        for _, raceId in ipairs({1, 2, 3, 4, 5, 6, 7, 8, 10, 11}) do
            local raceData = Adv2.Data.Racials and Adv2.Data.Racials[raceId]
            if raceData and raceData.racials then
                for _, racial in ipairs(raceData.racials) do
                    table.insert(entries, {
                        id = racial.id,
                        name = racial.name,
                        icon = racial.icon,
                        level = 1,
                        desc = racial.desc,
                    })
                end
            end
        end

        table.sort(entries, function(a, b)
            local aLearned = Adv2.playerData.learnedRacials[a.id] and 1 or 0
            local bLearned = Adv2.playerData.learnedRacials[b.id] and 1 or 0
            if aLearned ~= bLearned then
                return aLearned > bLearned
            end
            local aPending = Adv2.IsRacialPending and Adv2.IsRacialPending(a.id) and 1 or 0
            local bPending = Adv2.IsRacialPending and Adv2.IsRacialPending(b.id) and 1 or 0
            if aPending ~= bPending then
                return aPending > bPending
            end
            return (a.name or ""):lower() < (b.name or ""):lower()
        end)

        self:LayoutSpells(entries, clickHandler)
    end

    function panel:Update()
        for _, row in ipairs(self.rowPool) do
            if row:IsShown() then
                if self.mode == "racials" then
                    local learned = Adv2.playerData.learnedRacials[row.spellId]
                    local pending = Adv2.IsRacialPending and Adv2.IsRacialPending(row.spellId)
                    Adv2.UI.UpdateSpellRowState(row, learned, pending)
                else
                    local learned = Adv2.playerData.learnedAbilities[row.spellId]
                    local pending = Adv2.IsAbilityPending and Adv2.IsAbilityPending(row.spellId)
                    Adv2.UI.UpdateSpellRowState(row, learned, pending)
                end
            end
        end
    end

    panel:SetScript("OnShow", function()
        Adv2.UI.DeferWhenSized(panel, function()
            SyncRowWidth()
            panel:Update()
        end)
    end)

    return panel
end
