-- Adventurer2 — Classless-style UI (lazy-loaded, 3.3.5a safe)
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

Adv2.pendingAbilities = Adv2.pendingAbilities or {}
Adv2.pendingTalents = Adv2.pendingTalents or {}
Adv2.pendingRacials = Adv2.pendingRacials or {}

function Adv2.DestroyMainFrame()
    if Adv2.MainFrame then
        Adv2.MainFrame:Hide()
        Adv2.MainFrame = nil
    end
    if _G.Adventurer2MainFrame then
        _G.Adventurer2MainFrame:Hide()
        _G.Adventurer2MainFrame = nil
    end
    Adv2._mainFrameBuilding = false
end

function Adv2.CreateMainFrame()
    if Adv2.MainFrame then return Adv2.MainFrame end
    if Adv2._mainFrameBuilding then return nil end
    Adv2._mainFrameBuilding = true

    if _G.Adventurer2MainFrame then Adv2.DestroyMainFrame() end

    local ok, err = pcall(Adv2._CreateMainFrameImpl)
    Adv2._mainFrameBuilding = false

    if not ok then
        print("|cffff0000[Multiclass]|r UI error: " .. tostring(err))
        Adv2.DestroyMainFrame()
        return nil
    end
    return Adv2.MainFrame
end

function Adv2._CreateMainFrameImpl()
    local W = Adv2.UI.FRAME_WIDTH or 1040
    local H = Adv2.UI.FRAME_HEIGHT or 720

    local frame = CreateFrame("Frame", "Adventurer2MainFrame", UIParent)
    frame:Hide()
    frame:SetSize(W, H)
    frame:SetPoint("CENTER")
    frame:SetMovable(true)
    frame:EnableMouse(true)
    frame:RegisterForDrag("LeftButton")
    frame:SetScript("OnDragStart", frame.StartMoving)
    frame:SetScript("OnDragStop", frame.StopMovingOrSizing)
    frame:SetFrameStrata("DIALOG")
    frame:SetFrameLevel(10)

    Adv2.UI.ApplyDialogBackdrop(frame)

    -- Title
    local titleBar = CreateFrame("Frame", nil, frame)
    titleBar:SetHeight(Adv2.UI.TITLE_HEIGHT or 28)
    titleBar:SetPoint("TOPLEFT", 12, -10)
    titleBar:SetPoint("TOPRIGHT", -12, -10)
    frame.title = titleBar:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    frame.title:SetPoint("CENTER")
    frame.title:SetText("|cffffcc00Classless|r")

    frame.closeBtn = CreateFrame("Button", nil, titleBar, "UIPanelCloseButton")
    frame.closeBtn:SetPoint("TOPRIGHT", 2, 0)
    frame.closeBtn:SetFrameLevel(frame:GetFrameLevel() + 5)
    frame.closeBtn:SetScript("OnClick", function()
        if frame.rightCol and frame.rightCol.ReleaseSearchFocus then
            frame.rightCol:ReleaseSearchFocus()
        end
        frame:Hide()
    end)

    frame.selectedClassId = Adv2.GetPlayerClassId() or (Adv2.TopClassOrder and Adv2.TopClassOrder[1] or 4)
    frame.selectedSpecIndex = 1
    frame.viewMode = "class"
    frame.classLayoutReady = false

    -- Top class tabs (centered)
    frame.topTabBar = Adv2.UI.CreateTopTabBar(frame, function(tabId)
        frame:OnTopTabClick(tabId)
    end)
    frame.topTabBar:SetFrameLevel(frame:GetFrameLevel() + 50)

    -- Content host (panels created lazily on first class view)
    local contentTop = -(Adv2.UI.TITLE_HEIGHT + Adv2.UI.TOP_TAB_HEIGHT + 46)
    frame.contentHost = CreateFrame("Frame", nil, frame)
    frame.contentHost:EnableMouse(true)
    frame.contentHost:SetPoint("TOPLEFT", 14, contentTop)
    frame.contentHost:SetPoint("BOTTOMRIGHT", -14, (Adv2.UI.BOTTOM_BAR_HEIGHT or 36) + 14)
    frame.contentHost:SetFrameLevel(frame:GetFrameLevel() + 2)
    Adv2.UI.ApplyPanelBackground(frame.contentHost, "content")

    -- Bottom bar
    local bar = CreateFrame("Frame", nil, frame)
    bar:SetHeight(Adv2.UI.BOTTOM_BAR_HEIGHT or 36)
    bar:SetPoint("BOTTOMLEFT", 14, 12)
    bar:SetPoint("BOTTOMRIGHT", -14, 12)
    Adv2.UI.ApplyPanelBackground(bar, "footer")
    frame.pendingText = bar:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    frame.pendingText:SetPoint("LEFT", 4, 0)
    frame.statsText = bar:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
    frame.statsText:SetPoint("LEFT", 90, 0)

    local bw, bh, sp = 95, 24, 8
    frame.clearBtn = CreateFrame("Button", nil, bar, "UIPanelButtonTemplate")
    frame.clearBtn:SetSize(bw, bh)
    frame.clearBtn:SetPoint("RIGHT", -4, 0)
    frame.clearBtn:SetText("Clear Choices")
    frame.clearBtn:SetScript("OnClick", function() Adv2.ClearPending() end)

    frame.confirmBtn = CreateFrame("Button", nil, bar, "UIPanelButtonTemplate")
    frame.confirmBtn:SetSize(bw, bh)
    frame.confirmBtn:SetPoint("RIGHT", frame.clearBtn, "LEFT", -sp, 0)
    frame.confirmBtn:SetText("Confirm")
    frame.confirmBtn:SetScript("OnClick", function() Adv2.ConfirmPending() end)

    frame.resetBtn = CreateFrame("Button", nil, bar, "UIPanelButtonTemplate")
    frame.resetBtn:SetSize(bw, bh)
    frame.resetBtn:SetPoint("RIGHT", frame.confirmBtn, "LEFT", -sp, 0)
    frame.resetBtn:SetText("Respec")
    local resetLabel = frame.resetBtn:GetFontString()
    if resetLabel then
        resetLabel:SetTextColor(1, 0.15, 0.15)
    end
    frame.resetBtn:SetScript("OnClick", function()
        StaticPopup_Show("ADV2_CONFIRM_RESET")
    end)

    Adv2.UI.ApplyTiledBackground(
        frame, "tabStripBg",
        frame, "TOPLEFT", 16, -36,
        frame.contentHost, "TOPRIGHT", -16, 6,
        { 1, 1, 1, 1 }
    )

    function frame:EnsureClassLayout()
        if self.classLayoutReady and self.centerCol then return end
        if self.classLayoutReady and not self.centerCol then
            self.classLayoutReady = false
        end

        local ok, err = pcall(function()
            self.classLayout = CreateFrame("Frame", nil, self.contentHost)
            self.classLayout:SetAllPoints()
            self.classLayout:SetFrameLevel(self.contentHost:GetFrameLevel() + 1)
            Adv2.UI.ApplyRockBackground(self.classLayout)

            self.leftCol = Adv2.UI.CreateSpellGridPanel(self.classLayout)
            self.leftCol:SetPoint("TOPLEFT", 0, 0)
            self.leftCol:SetPoint("BOTTOMLEFT", 0, 0)

            self.rightCol = Adv2.UI.CreateSearchSidebar(self.classLayout)
            self.rightCol:SetPoint("TOPRIGHT", 0, 0)
            self.rightCol:SetPoint("BOTTOMRIGHT", 0, 0)

            self.centerCol = CreateFrame("Frame", nil, self.classLayout)
            self.centerCol:SetPoint("TOP", 0, 0)
            self.centerCol:SetPoint("BOTTOM", 0, 0)
            self.centerCol:SetPoint("LEFT", self.leftCol, "RIGHT", 6, 0)
            self.centerCol:SetPoint("RIGHT", self.rightCol, "LEFT", -6, 0)
            Adv2.UI.ApplySolidBackground(self.centerCol, 0.05, 0.04, 0.04, 1)

            local specBarH = Adv2.UI.SPEC_BAR_HEIGHT or 32
            self.centerSpecBar = Adv2.UI.CreateCenterSpecBar(self.centerCol, function(specIndex)
                frame.selectedSpecIndex = specIndex
                frame:EnsureSpecPanel()
            end)
            self.centerSpecBar:ClearAllPoints()
            self.centerSpecBar:SetPoint("TOPLEFT", self.centerCol, "TOPLEFT", 8, -8)
            self.centerSpecBar:SetPoint("TOPRIGHT", self.centerCol, "TOPRIGHT", -8, -8)
            self.centerSpecBar:SetHeight(specBarH)

            self.specPanel = nil
            self.specPanelTop = -(8 + specBarH + 4)
        end)

        if not ok then
            print("|cffff0000[Multiclass]|r Class layout build failed: " .. tostring(err))
            return
        end

        self.classLayoutReady = true
    end

    function frame:EnsureSpecPanel()
        self:EnsureClassLayout()
        local classData = Adv2.Classes[self.selectedClassId]
        local specData = classData and classData.specs and classData.specs[self.selectedSpecIndex]
        local centerW = self.centerCol:GetWidth() - 16
        if centerW <= 0 then
            centerW = (Adv2.UI.CENTER_COL_WIDTH or 420) - 16
        end

        self.centerSpecBar.activeSpec = self.selectedSpecIndex or 1
        self.centerSpecBar:BuildSpecs(self.selectedClassId)
        self.centerSpecBar:Show()

        if not self.specPanel then
            self.specPanel = Adv2.UI.CreateSpecPanel(self.centerCol, self.selectedClassId, self.selectedSpecIndex, specData, centerW)
            self.specPanel:SetPoint("TOPLEFT", 8, self.specPanelTop)
            self.specPanel:SetPoint("BOTTOMRIGHT", -8, 8)
        else
            self.specPanel:SetSpec(self.selectedClassId, self.selectedSpecIndex, specData)
            self.specPanel:Show()
        end

        if self.specPanel.Relayout then
            self.specPanel:Relayout()
        end
    end

    function frame:EnsureHeirloomsPanel()
        if self.heirloomsPanel then return end
        self.heirloomsPanel = Adv2.UI.CreateHeirloomsPanel(self.contentHost)
        self.heirloomsPanel:SetAllPoints()
        self.heirloomsPanel:SetFrameLevel(self.contentHost:GetFrameLevel() + 5)
        self.heirloomsPanel:Hide()
    end

    function frame:EnsureMorphPanel()
        if Adv2.IsClientOnly() then
            return
        end
        if self.morphPanel then return end
        self:EnsureClassLayout()
        if not Adv2.UI.CreateMorphPanel then
            print("|cffff0000[Multiclass]|r Morph panel unavailable (CreateMorphPanel missing).")
            return
        end
        self.morphPanel = Adv2.UI.CreateMorphPanel(self.classLayout)
        self.morphPanel:Hide()
    end

    function frame:EnsureKnownSpellsPanel()
        if self.knownSpellsPanel then return end
        self:EnsureClassLayout()
        if not Adv2.UI.CreateKnownSpellsPanel then
            print("|cffff0000[Multiclass]|r Known spells panel unavailable.")
            return
        end
        self.knownSpellsPanel = Adv2.UI.CreateKnownSpellsPanel(self.classLayout)
        self.knownSpellsPanel:Hide()
    end

    function frame:HideAllViews()
        if self.heirloomsPanel then self.heirloomsPanel:Hide() end
        if self.morphPanel then self.morphPanel:Hide() end
        if self.knownSpellsPanel then self.knownSpellsPanel:Hide() end
    end

    function frame:RefreshClassContent()
        Adv2.UI.DeferWhenSized(self.contentHost, function()
            local ok, err = pcall(function()
                self:EnsureClassLayout()
                if not self.centerCol or not self.leftCol or not self.rightCol then
                    error("class layout incomplete")
                end
                self:HideAllViews()
                self.classLayout:Show()
                self.viewMode = "class"

                self.leftCol:ClearAllPoints()
                self.leftCol:SetWidth(Adv2.UI.LEFT_COL_WIDTH or 320)
                self.leftCol:SetPoint("TOPLEFT", 0, 0)
                self.leftCol:SetPoint("BOTTOMLEFT", 0, 0)
                self.centerCol:Show()
                self.rightCol:Show()

                self.rightCol.onSpellSelect = function(entry)
                    if entry and entry.id then
                        frame:OnAbilityClick({ spellId = entry.id })
                    end
                end

                self.leftCol:SetClassSpells(self.selectedClassId, function(btn)
                    frame:OnAbilityClick(btn)
                end)

                self:EnsureSpecPanel()
                if self.rightCol.RefreshList then
                    self.rightCol:RefreshList()
                end
                self.topTabBar:SetSelection(self.selectedClassId, nil)
            end)
            if not ok then
                print("|cffff0000[Multiclass]|r Class layout error: " .. tostring(err))
            end
        end)
    end

    function frame:ShowRacialsView()
        local ok, err = pcall(function()
            self:EnsureClassLayout()
            if not self.leftCol then
                error("class layout incomplete")
            end
            self:HideAllViews()
            if self.heirloomsPanel then self.heirloomsPanel:Hide() end
            self.classLayout:Show()
            self.viewMode = "racials"

            self.leftCol:ClearAllPoints()
            self.leftCol:SetWidth(Adv2.UI.LEFT_COL_WIDTH or 320)
            self.leftCol:SetPoint("TOPLEFT", 0, 0)
            self.leftCol:SetPoint("BOTTOMLEFT", 0, 0)
            if self.centerCol then self.centerCol:Hide() end
            if self.rightCol then self.rightCol:Hide() end

            if Adv2.IsClientOnly() then
                self:EnsureKnownSpellsPanel()
                if not self.knownSpellsPanel then
                    error("known spells panel failed to create")
                end
                self.knownSpellsPanel:ClearAllPoints()
                self.knownSpellsPanel:SetPoint("TOPLEFT", self.leftCol, "TOPRIGHT", 8, 0)
                self.knownSpellsPanel:SetPoint("BOTTOMRIGHT", self.classLayout, "BOTTOMRIGHT", -4, 4)
                self.knownSpellsPanel:SetFrameLevel(self.classLayout:GetFrameLevel() + 10)
                self.knownSpellsPanel:Show()
                if self.knownSpellsPanel.Update then
                    self.knownSpellsPanel:Update()
                end
            else
                self:EnsureMorphPanel()
                if not self.morphPanel then
                    error("morph panel failed to create")
                end
                self.morphPanel:ClearAllPoints()
                self.morphPanel:SetPoint("TOPLEFT", self.leftCol, "TOPRIGHT", 8, 0)
                self.morphPanel:SetPoint("BOTTOMRIGHT", self.classLayout, "BOTTOMRIGHT", -4, 4)
                self.morphPanel:SetFrameLevel(self.classLayout:GetFrameLevel() + 10)
                self.morphPanel:Show()
                if self.morphPanel.RefreshLayout then
                    self.morphPanel:RefreshLayout()
                end
            end

            self.leftCol:SetRacialSpells(function(btn) frame:OnRacialClick(btn) end)
            self.topTabBar:SetSelection(nil, "racials")
        end)
        if not ok then
            print("|cffff0000[Multiclass]|r Racials view error: " .. tostring(err))
        end
    end

    function frame:RefreshRacialList()
        if self.viewMode == "racials" and self.leftCol then
            self.leftCol:SetRacialSpells(function(btn) frame:OnRacialClick(btn) end)
        end
    end

    function frame:ShowHeirloomsView()
        local ok, err = pcall(function()
            self:EnsureHeirloomsPanel()
            self:HideAllViews()
            if self.classLayout then self.classLayout:Hide() end
            self.viewMode = "heirlooms"
            self.heirloomsPanel:SetFrameLevel(self.contentHost:GetFrameLevel() + 5)
            self.heirloomsPanel:Show()
            self.topTabBar:SetSelection(nil, "heirlooms")
            Adv2.UI.DeferWhenSized(self.contentHost, function()
                if self.heirloomsPanel and self.heirloomsPanel.Update then
                    self.heirloomsPanel:Update()
                end
            end)
        end)
        if not ok then
            print("|cffff0000[Multiclass]|r Heirlooms panel error: " .. tostring(err))
        end
    end

    function frame:OnTopTabClick(tabId)
        if tabId == "heirlooms" then
            self:ShowHeirloomsView()
        elseif tabId == "racials" then
            self:ShowRacialsView()
        else
            self.selectedClassId = tabId
            self.selectedSpecIndex = 1
            self:RefreshClassContent()
        end
        self:UpdateHeader()
    end

    function frame:OnAbilityClick(btn)
        if not btn or not btn.spellId then return end
        if Adv2.IsClientOnly() then
            print("|cffffcc00[Multiclass]|r Abilities are learned from trainers on normal servers.")
            return
        end
        if Adv2.playerData.learnedAbilities[btn.spellId] then
            print("|cffffcc00[Multiclass]|r Already learned.")
            return
        end
        if Adv2.IsAbilityPending(btn.spellId) then
            for i, id in ipairs(Adv2.pendingAbilities) do
                if id == btn.spellId then table.remove(Adv2.pendingAbilities, i) break end
            end
            Adv2.UpdateUI()
            return
        end
        if Adv2.AddPendingAbility(btn.spellId) then
            local name = GetSpellInfo(btn.spellId) or ("Spell " .. btn.spellId)
            print("|cff00ff00[Multiclass]|r Queued: " .. name)
        end
    end

    function frame:OnRacialClick(btn)
        if Adv2.IsClientOnly() then
            print("|cffffcc00[Multiclass]|r Racials come from your race on normal servers.")
            return
        end
        if Adv2.playerData.learnedRacials[btn.spellId] then return end
        if Adv2.IsRacialPending(btn.spellId) then
            for i, id in ipairs(Adv2.pendingRacials) do
                if id == btn.spellId then table.remove(Adv2.pendingRacials, i) break end
            end
            Adv2.UpdateUI()
            return
        end
        Adv2.AddPendingRacial(btn.spellId)
    end

    function frame:UpdateHeader()
        if Adv2.IsClientOnly() then
            local staged = #Adv2.pendingTalents
            local free = math.max(0, Adv2.RealFreeTalentPoints() - staged)
            self.statsText:SetText(string.format(
                "|cffffcc00Talent Points|r  |cff00ff00%d|r free", free))
            if staged > 0 then
                self.pendingText:SetText("|cffffff00" .. staged .. " staged|r — Confirm to apply")
                self.confirmBtn:Enable()
                self.clearBtn:Enable()
            else
                self.pendingText:SetText("|cff888888none staged|r")
                self.confirmBtn:Disable()
                self.clearBtn:Disable()
            end
            return
        end

        local picks = Adv2.GetAvailablePicks()
        local pendingTalents = #Adv2.pendingTalents
        local pending = #Adv2.pendingAbilities + pendingTalents + #Adv2.pendingRacials
        self.statsText:SetText(string.format(
            "|cffffcc00Abilities|r %d+|cff00ff00%d|r/%d  |cffffcc00Talents|r %d+|cff00ff00%d|r/%d  |cffffcc00Racials|r %d+|cff00ff00%d|r/%d",
            picks.abilities.used, #Adv2.pendingAbilities, picks.abilities.total,
            picks.talents.used, pendingTalents, picks.talents.total,
            picks.racials.used, #Adv2.pendingRacials, picks.racials.total
        ))
        if pending > 0 then
            self.pendingText:SetText("|cff00ff00" .. pending .. " pending|r")
            self.confirmBtn:Enable()
            self.clearBtn:Enable()
        else
            self.pendingText:SetText("|cff8888880 pending|r")
            self.confirmBtn:Disable()
            self.clearBtn:Disable()
        end
    end

    function frame:UpdatePanels()
        if self.viewMode == "heirlooms" and self.heirloomsPanel and self.heirloomsPanel.Update then
            self.heirloomsPanel:Update()
        elseif self.viewMode == "racials" and self.leftCol then
            self:RefreshRacialList()
            self.leftCol:Update()
            if self.knownSpellsPanel and self.knownSpellsPanel.Update then
                self.knownSpellsPanel:Update()
            elseif self.morphPanel and self.morphPanel.UpdateSelection then
                self.morphPanel:UpdateSelection()
            end
        elseif self.viewMode == "class" then
            if self.leftCol then self.leftCol:Update() end
            if self.rightCol and self.rightCol.Update then self.rightCol:Update() end
            if self.specPanel then self.specPanel:Update() end
        end
    end

    frame:SetScript("OnShow", function(self)
        if self.rightCol and self.rightCol.ReleaseSearchFocus then
            self.rightCol:ReleaseSearchFocus()
        end
        Adv2.UI.DeferWhenSized(self.contentHost, function()
            if self.topTabBar and self.topTabBar.EnsurePlayerClassTab then
                self.topTabBar:EnsurePlayerClassTab()
            end

            local classId = Adv2.GetPlayerClassId()
            if classId then
                self.selectedClassId = classId
            end

            if not self._initialized then
                self._initialized = true
                self.viewMode = "class"
                self:OnTopTabClick(self.selectedClassId)
            elseif self.viewMode == "class" then
                self:RefreshClassContent()
            elseif self.viewMode == "heirlooms" and self.heirloomsPanel and self.heirloomsPanel.Update then
                self.heirloomsPanel:Update()
            elseif self.viewMode == "racials" then
                self:ShowRacialsView()
            end
            self:UpdateHeader()
        end)
    end)

    frame:SetScript("OnHide", function(self)
        if self.rightCol and self.rightCol.ReleaseSearchFocus then
            self.rightCol:ReleaseSearchFocus()
        end
    end)

    tinsert(UISpecialFrames, "Adventurer2MainFrame")
    Adv2.MainFrame = frame

    StaticPopupDialogs["ADV2_CONFIRM_RESET"] = {
        text = "Respec: unlearn ALL talents — your own class tree and every " ..
               "cross-class tree — and refund every point?",
        button1 = YES,
        button2 = NO,
        OnAccept = function()
            Adv2.ClearPending()
            if Adv2.IsClientOnly() then
                -- Real server-side respec: remove cross-class talents + refund.
                Adv2.RequestServerRespec()
                return
            end
            Adv2.playerData.learnedAbilities = {}
            Adv2.playerData.learnedTalents = {}
            Adv2.playerData.learnedRacials = {}
            Adv2.SaveData()
            Adv2.UpdateUI()
        end,
        timeout = 0,
        whileDead = 1,
        hideOnEscape = 1,
        preferredIndex = 3,
    }
end

-- Pending + heirlooms (unchanged logic)
function Adv2.AddPendingAbility(spellId)
    if Adv2.IsClientOnly() then
        print("|cffffcc00[Multiclass]|r Abilities are learned from trainers on normal servers.")
        return false
    end
    local picks = Adv2.GetAvailablePicks()
    if picks.abilities.available - #Adv2.pendingAbilities <= 0 then
        print("|cffff0000[Multiclass]|r No ability picks available!")
        return false
    end
    for _, id in ipairs(Adv2.pendingAbilities) do if id == spellId then return false end end
    table.insert(Adv2.pendingAbilities, spellId)
    Adv2.UpdateUI()
    return true
end

function Adv2.AddPendingTalent(classId, specIndex, talentId, spellId)
    if Adv2.IsClientOnly() then
        return Adv2.LearnTalent(classId, specIndex, talentId, spellId)
    end

    if Adv2.IsTalentClassAllowed and not Adv2.IsTalentClassAllowed(classId) then
        print("|cffff0000[Multiclass]|r That class is not registered as unlocked.")
        return false
    end

    local talentData = Adv2.FindTalentData(classId, specIndex, talentId)
    local maxRank = (talentData and talentData.maxRank) or 1
    local currentRank = Adv2.GetTalentPoints(classId, specIndex, talentId)
    local pendingRank = Adv2.CountPendingTalentRank(classId, specIndex, talentId)
    if currentRank + pendingRank >= maxRank then
        return false
    end

    local picks = Adv2.GetAvailablePicks()
    if picks.talents.available - #Adv2.pendingTalents <= 0 then
        print("|cffff0000[Multiclass]|r No talent points available!")
        return false
    end
    table.insert(Adv2.pendingTalents, { classId = classId, specIndex = specIndex, talentId = talentId, spellId = spellId })
    Adv2.UpdateUI()
    return true
end

function Adv2.AddPendingRacial(spellId)
    if Adv2.IsClientOnly() then
        print("|cffffcc00[Multiclass]|r Racials come from your race on normal servers.")
        return false
    end
    local picks = Adv2.GetAvailablePicks()
    if picks.racials.available - #Adv2.pendingRacials <= 0 then
        print("|cffff0000[Multiclass]|r No racial picks available!")
        return false
    end
    for _, id in ipairs(Adv2.pendingRacials) do if id == spellId then return false end end
    table.insert(Adv2.pendingRacials, spellId)
    Adv2.UpdateUI()
    return true
end

function Adv2.IsAbilityPending(spellId)
    for _, id in ipairs(Adv2.pendingAbilities) do if id == spellId then return true end end
    return false
end

function Adv2.IsTalentPending(classId, specIndex, talentId)
    for _, p in ipairs(Adv2.pendingTalents) do
        if p.classId == classId and p.specIndex == specIndex and p.talentId == talentId then return true end
    end
    return false
end

function Adv2.IsRacialPending(spellId)
    for _, id in ipairs(Adv2.pendingRacials) do if id == spellId then return true end end
    return false
end

function Adv2.ClearPending()
    Adv2.pendingAbilities = {}
    Adv2.pendingTalents = {}
    Adv2.pendingRacials = {}
    Adv2.UpdateUI()
end

function Adv2.ConfirmPending()
    if Adv2.IsClientOnly() then
        -- Send the staged cross-class picks to the server (throttled, in order).
        Adv2.ApplyPendingCrossTalents()
        return
    end

    for _, spellId in ipairs(Adv2.pendingAbilities) do Adv2.LearnAbility(spellId) end
    for _, p in ipairs(Adv2.pendingTalents) do Adv2.LearnTalent(p.classId, p.specIndex, p.talentId, p.spellId) end
    for _, spellId in ipairs(Adv2.pendingRacials) do Adv2.LearnRacial(spellId) end
    Adv2.pendingAbilities = {}
    Adv2.pendingTalents = {}
    Adv2.pendingRacials = {}
    Adv2.UpdateUI()
    if Adv2.MainFrame and Adv2.MainFrame.RefreshRacialList then
        Adv2.MainFrame:RefreshRacialList()
    end
    print("|cff00ff00[Multiclass]|r Selections confirmed!")
end
