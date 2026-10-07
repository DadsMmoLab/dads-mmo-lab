-- Adventurer2 Talent Button
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

local BUTTON_SIZE = (Adv2.UI and Adv2.UI.TALENT_BUTTON_SIZE) or 32
local RANK_HEIGHT = 14

function Adv2.UI.ResolveTalentIcon(talentData)
    if not talentData then
        return "Interface\\Icons\\INV_Misc_QuestionMark"
    end

    local spellId = talentData.id
    if talentData.ranks and talentData.ranks[1] then
        spellId = talentData.ranks[1]
    end

    if spellId then
        local _, _, icon = GetSpellInfo(spellId)
        if icon and icon ~= "" then
            return icon
        end
    end

    if talentData.icon and not string.find(talentData.icon, "QuestionMark", 1, true) then
        return talentData.icon
    end

    return "Interface\\Icons\\INV_Misc_QuestionMark"
end

function Adv2.UI.CreateTalentButton(parent, talentData, classId, specIndex)
    if not parent or not talentData then
        return nil
    end

    local btn = CreateFrame("Button", nil, parent)
    btn:SetSize(BUTTON_SIZE, BUTTON_SIZE + RANK_HEIGHT)

    btn.talentData = talentData
    btn.classId = classId
    btn.specIndex = specIndex
    btn.currentRank = 0
    btn.pendingRank = 0

    btn.iconHolder = CreateFrame("Frame", nil, btn)
    btn.iconHolder:SetSize(BUTTON_SIZE, BUTTON_SIZE)
    btn.iconHolder:SetPoint("TOP", 0, 0)

    btn.icon = btn.iconHolder:CreateTexture(nil, "ARTWORK")
    btn.icon:SetPoint("TOPLEFT", 1, -1)
    btn.icon:SetPoint("BOTTOMRIGHT", -1, 1)
    btn.icon:SetTexture(Adv2.UI.ResolveTalentIcon(talentData))
    btn.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)

    -- Subtle hover ring only (hidden until hover/select — no permanent white square)
    btn.border = btn.iconHolder:CreateTexture(nil, "OVERLAY")
    btn.border:SetPoint("TOPLEFT", -1, 1)
    btn.border:SetPoint("BOTTOMRIGHT", 1, -1)
    btn.border:SetTexture("Interface\\Buttons\\UI-ActionButton-Border")
    btn.border:SetBlendMode("ADD")
    btn.border:Hide()

    btn.check = btn.iconHolder:CreateTexture(nil, "OVERLAY")
    btn.check:SetSize(22, 22)
    btn.check:SetPoint("CENTER", 0, 0)
    btn.check:SetTexture("Interface\\Buttons\\UI-CheckBox-Check")
    btn.check:SetVertexColor(0.15, 1, 0.15)
    btn.check:Hide()

    btn.rankText = btn:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
    btn.rankText:SetPoint("TOP", btn.iconHolder, "BOTTOM", 0, -1)
    btn.rankText:SetText("0/1")

    btn:SetScript("OnClick", function(self, button)
        local maxRank = self.talentData.maxRank or 1
        local totalRank = self.currentRank + self.pendingRank

        if button == "RightButton" then
            if self.pendingRank > 0 then
                for i = #Adv2.pendingTalents, 1, -1 do
                    local pending = Adv2.pendingTalents[i]
                    if pending.classId == self.classId and
                       pending.specIndex == self.specIndex and
                       pending.talentId == self.talentData.id then
                        table.remove(Adv2.pendingTalents, i)
                        Adv2.UpdateUI()
                        return
                    end
                end
            end
            return
        end

        if totalRank >= maxRank then return end

        local nextRank = totalRank + 1
        local spellId = self.talentData.id
        if self.talentData.ranks and self.talentData.ranks[nextRank] then
            spellId = self.talentData.ranks[nextRank]
        end

        if Adv2.TryApplyTalent(self.classId, self.specIndex, self.talentData.id, spellId) then
            if not Adv2.IsClientOnly() then
                print("|cff00ff00[Multiclass]|r Selected: " .. (self.talentData.name or "Talent"))
            end
        end
    end)

    btn:SetScript("OnEnter", function(self)
        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
        local spellId = self.talentData.id
        if self.talentData.ranks and self.currentRank > 0 and self.talentData.ranks[self.currentRank] then
            spellId = self.talentData.ranks[self.currentRank]
        end
        Adv2.UI.SetSpellTooltip(spellId, self.talentData.name, self.talentData.desc)
        GameTooltip:AddLine(" ")
        local maxRank = self.talentData.maxRank or 1
        local totalRank = self.currentRank + self.pendingRank
        GameTooltip:AddLine("Rank: " .. totalRank .. "/" .. maxRank, 1, 1, 1)
        GameTooltip:Show()
        if (self.currentRank + self.pendingRank) == 0 then
            self.border:SetVertexColor(1, 0.82, 0)
            self.border:Show()
        end
    end)
    btn:SetScript("OnLeave", function()
        GameTooltip:Hide()
        if btn.Update then btn:Update() end
    end)

    function btn:RefreshIcon()
        self.icon:SetTexture(Adv2.UI.ResolveTalentIcon(self.talentData))
    end

    function btn:Update()
        local maxRank = self.talentData.maxRank or 1
        self.currentRank = Adv2.GetTalentPoints(self.classId, self.specIndex, self.talentData.id)
        self.pendingRank = 0
        for _, pending in ipairs(Adv2.pendingTalents) do
            if pending.classId == self.classId and pending.specIndex == self.specIndex and pending.talentId == self.talentData.id then
                self.pendingRank = self.pendingRank + 1
            end
        end
        local totalRank = self.currentRank + self.pendingRank
        self.rankText:SetText(totalRank .. "/" .. maxRank)

        if totalRank > 0 then
            self.check:Show()
            self.border:Hide()
            self.rankText:SetTextColor(0.2, 1, 0.2)
            if self.pendingRank > 0 then
                self.rankText:SetTextColor(1, 1, 0.4)
            elseif totalRank >= maxRank then
                self.rankText:SetTextColor(0.2, 1, 0.2)
            end
            if self.icon.SetDesaturated then
                self.icon:SetDesaturated(false)
            end
        else
            self.check:Hide()
            self.border:Hide()
            self.rankText:SetTextColor(0.75, 0.75, 0.75)
            if self.icon.SetDesaturated then
                self.icon:SetDesaturated(false)
            end
        end
        return totalRank
    end

    btn:RefreshIcon()
    btn:Update()
    return btn
end

Adv2.UI.TALENT_ROW_HEIGHT = BUTTON_SIZE + RANK_HEIGHT
