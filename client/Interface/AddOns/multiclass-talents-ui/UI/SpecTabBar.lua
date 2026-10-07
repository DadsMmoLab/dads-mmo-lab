-- Center-column spec selector (Blood / Frost / Unholy etc.)
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

function Adv2.UI.CreateCenterSpecBar(parent, onSpecSelect)
    local bar = CreateFrame("Frame", nil, parent)
    bar:SetHeight(Adv2.UI.SPEC_BAR_HEIGHT or 32)

    bar.bg = bar:CreateTexture(nil, "BACKGROUND")
    bar.bg:SetAllPoints()
    bar.bg:SetTexture("Interface\\Buttons\\WHITE8X8")
    bar.bg:SetVertexColor(0, 0, 0, 0)

    bar.specTabs = {}
    bar.onSpecSelect = onSpecSelect
    bar.activeSpec = 1

    function bar:BuildSpecs(classId)
        for _, tab in pairs(self.specTabs) do
            tab:Hide()
        end

        local classData = Adv2.Classes and Adv2.Classes[classId]
        if not classData or not classData.specs then
            return
        end

        local visible = {}
        for specIndex = 1, 3 do
            local specData = classData.specs[specIndex]
            if specData then
                local tab = self.specTabs[specIndex]
                if not tab then
                    tab = Adv2.UI.CreateGoldTab(self, specData.name, 104)
                    tab:SetFrameLevel(self:GetFrameLevel() + 2)
                    tab:SetScript("OnClick", function()
                        self:SelectSpec(tab.specIndex)
                    end)
                    self.specTabs[specIndex] = tab
                end
                tab.text:SetText(specData.name)
                tab.specIndex = specIndex
                tab:Show()
                table.insert(visible, tab)
            elseif self.specTabs[specIndex] then
                self.specTabs[specIndex]:Hide()
            end
        end

        local tabW, spacing = 104, 8
        local totalW = #visible * tabW + math.max(0, #visible - 1) * spacing
        local x = -totalW / 2 + tabW / 2
        for _, tab in ipairs(visible) do
            tab:ClearAllPoints()
            tab:SetSize(tabW, 28)
            tab:SetPoint("TOP", self, "TOP", x, -2)
            x = x + tabW + spacing
        end

        self:SelectSpec(self.activeSpec or 1)
    end

    function bar:SelectSpec(specIndex)
        local changed = self.activeSpec ~= specIndex
        self.activeSpec = specIndex
        for _, tab in pairs(self.specTabs) do
            if tab:IsShown() then
                tab:SetSelected(tab.specIndex == specIndex)
            end
        end
        if changed and self.onSpecSelect then
            self.onSpecSelect(specIndex)
        end
    end

    bar:Hide()
    return bar
end
