-- Classless-style top tab bar — classes + racials + heirlooms (moderate spread)
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

function Adv2.UI.CreateTopTabBar(parent, onSelect)
    local bar = CreateFrame("Frame", nil, parent)
    bar:SetHeight(Adv2.UI.TOP_TAB_HEIGHT or 78)
    bar:SetPoint("TOPLEFT", 12, Adv2.UI.TOP_TAB_Y or -42)
    bar:SetPoint("TOPRIGHT", -36, Adv2.UI.TOP_TAB_Y or -42)
    bar:EnableMouse(false)
    bar.tabs = {}
    bar.onSelect = onSelect

    local function AddTab(id, icon, label, iconCoords)
        local tab = Adv2.UI.CreateClassTab(bar, id, icon, label, iconCoords)
        table.insert(bar.tabs, tab)
        tab.tabId = id
        tab:SetScript("OnClick", function()
            if bar.onSelect then
                bar.onSelect(id)
            end
        end)
        return tab
    end

    for _, classId in ipairs(Adv2.TopClassOrder or Adv2.ClassOrder or {}) do
        local classData = Adv2.Classes and Adv2.Classes[classId]
        if classData then
            AddTab(classId, classData.icon, classData.name)
        end
    end

    bar.racialsTab = AddTab("racials", "Interface\\Icons\\Achievement_Character_Human_Male", "Racials")
    bar.heirloomsTab = AddTab("heirlooms", "Interface\\Icons\\INV_Sword_43", "Heirlooms")

    function bar:EnsurePlayerClassTab()
        local classId = Adv2.GetPlayerClassId()
        if not classId then
            return
        end

        for _, tab in ipairs(self.tabs) do
            if tab.tabId == classId then
                return
            end
        end

        local classData = Adv2.Classes and Adv2.Classes[classId]
        if not classData then
            return
        end

        local tab = Adv2.UI.CreateClassTab(self, classId, classData.icon, classData.name)
        tab.tabId = classId
        tab:SetScript("OnClick", function()
            if self.onSelect then
                self.onSelect(classId)
            end
        end)

        local insertAt = 1
        for index, existing in ipairs(self.tabs) do
            if existing.tabId == "racials" or existing.tabId == "heirlooms" then
                insertAt = index
                break
            end
        end
        table.insert(self.tabs, insertAt, tab)
        self:LayoutTabs()
    end

    function bar:LayoutTabs()
        local iconSize = Adv2.UI.CLASS_TAB_ICON_SIZE or 48
        local minSpacing = Adv2.UI.CLASS_TAB_SPACING or 14
        local maxSpacing = Adv2.UI.CLASS_TAB_MAX_SPACING or 22
        local spread = Adv2.UI.CLASS_TAB_SPREAD or 0.82
        local topOffset = -(Adv2.UI.CLASS_TAB_TOP_OFFSET or 10)
        local count = #self.tabs
        if count == 0 then return end

        for _, tab in ipairs(self.tabs) do
            if tab.UpdateIconLayout then
                tab:UpdateIconLayout(iconSize)
            end
        end

        local barWidth = self:GetWidth()
        if barWidth <= 0 then
            barWidth = (Adv2.UI.FRAME_WIDTH or 1040) - 48
        end

        local maxW = barWidth - 4
        local sumWidths = 0
        for _, tab in ipairs(self.tabs) do
            sumWidths = sumWidths + tab:GetWidth()
        end

        local spacing = minSpacing
        if count > 1 then
            local minTotal = sumWidths + (count - 1) * minSpacing
            if minTotal > maxW then
                spacing = math.max(2, (maxW - sumWidths) / (count - 1))
            else
                local targetW = math.min(maxW, barWidth * spread)
                local expanded = (targetW - sumWidths) / (count - 1)
                spacing = math.min(math.max(expanded, minSpacing), maxSpacing)
            end
        end

        local totalW = sumWidths + math.max(0, count - 1) * spacing
        local x = -totalW / 2
        local tabLevel = self:GetFrameLevel() + 5

        for _, tab in ipairs(self.tabs) do
            local w = tab:GetWidth()
            tab:ClearAllPoints()
            tab:SetPoint("TOP", self, "TOP", x + w / 2, topOffset)
            tab:EnableMouse(true)
            tab:SetFrameLevel(tabLevel)
            x = x + w + spacing
        end
    end

    function bar:SetSelection(classId, specialTab)
        for _, tab in ipairs(self.tabs) do
            local selected = false
            if specialTab then
                selected = tab.id == specialTab
            elseif classId then
                selected = tab.id == classId
            end
            tab:SetSelected(selected)
        end
    end

    bar:SetScript("OnSizeChanged", function()
        bar:LayoutTabs()
    end)

    bar:LayoutTabs()
    return bar
end
