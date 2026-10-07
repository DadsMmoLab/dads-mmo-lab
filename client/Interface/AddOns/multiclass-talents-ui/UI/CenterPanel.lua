-- Center column panels: workspace (spells), character preview (specs)

Adv2 = Adv2 or {}

Adv2.UI = Adv2.UI or {}



function Adv2.UI.CreateWorkspacePanel(parent)

    local panel = CreateFrame("Frame", nil, parent)



    panel.bg = panel:CreateTexture(nil, "BACKGROUND")

    panel.bg:SetAllPoints()

    panel.bg:SetTexture("Interface\\Buttons\\WHITE8X8")

    panel.bg:SetVertexColor(0.04, 0.03, 0.06, 0.98)



    panel.specArt = panel:CreateTexture(nil, "ARTWORK")

    panel.specArt:SetAllPoints()

    panel.specArt:SetAlpha(0.85)



    panel.overlay = panel:CreateTexture(nil, "BORDER")

    panel.overlay:SetAllPoints()

    panel.overlay:SetTexture("Interface\\Buttons\\WHITE8X8")

    panel.overlay:SetVertexColor(0, 0, 0, 0.4)



    function panel:SetSpec(classId, specIndex)

        local classData = Adv2.Classes and Adv2.Classes[classId]

        local specData = classData and classData.specs and classData.specs[specIndex]

        if specData and specData.background then

            self.specArt:SetTexture("Interface\\TalentFrame\\" .. specData.background .. "-TopLeft")

            self.specArt:SetTexCoord(0, 1, 0, 1)

            self.specArt:Show()

        else

            self.specArt:Hide()

        end

    end



    return panel

end



function Adv2.UI.CreateSpecPreviewPanel(parent)

    local panel = CreateFrame("Frame", nil, parent)



    panel.bg = panel:CreateTexture(nil, "BACKGROUND")

    panel.bg:SetAllPoints()

    panel.bg:SetTexture("Interface\\Buttons\\WHITE8X8")

    panel.bg:SetVertexColor(0.04, 0.03, 0.06, 0.95)



    panel.model = CreateFrame("PlayerModel", nil, panel)

    panel.model:SetPoint("TOPLEFT", 12, -52)

    panel.model:SetPoint("BOTTOMRIGHT", -12, 12)

    panel.model:SetRotation(0.35)



    panel.nameText = panel:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")

    panel.nameText:SetPoint("TOP", 0, -16)

    panel.nameText:SetTextColor(1, 0.82, 0)



    panel.subText = panel:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")

    panel.subText:SetPoint("TOP", panel.nameText, "BOTTOM", 0, -2)

    panel.subText:SetTextColor(0.7, 0.7, 0.7)



    function panel:Update(classId, specIndex)

        local classData = Adv2.Classes and Adv2.Classes[classId]

        local specData = classData and classData.specs and classData.specs[specIndex]



        self.nameText:SetText(UnitName("player") or "")

        local guild = GetGuildInfo("player")

        local specName = specData and specData.name or ""

        if guild and guild ~= "" then

            self.subText:SetText("<" .. guild .. ">  " .. specName)

        else

            self.subText:SetText(specName)

        end



        self.model:SetUnit("player")

    end



    return panel

end


