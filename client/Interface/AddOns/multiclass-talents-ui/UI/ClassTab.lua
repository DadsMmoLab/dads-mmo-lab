-- Adventurer2 Class Tab Panel
-- Shows all 3 specs for a class side-by-side
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

local FRAME_WIDTH = 750  -- Match MainFrame width
local SPEC_PANEL_WIDTH = 220
local SPEC_HEADER_HEIGHT = 28
local PANEL_PADDING = 5

-- Create a full class tab with 3 spec panels
function Adv2.UI.CreateClassTab(parent, classId, classData)
    local frame = CreateFrame("Frame", nil, parent)
    frame.classId = classId
    frame.classData = classData
    
    -- Header showing class name and total points
    frame.header = CreateFrame("Frame", nil, frame)
    frame.header:SetHeight(30)
    frame.header:SetPoint("TOPLEFT", 0, 0)
    frame.header:SetPoint("TOPRIGHT", 0, 0)
    
    -- Class icon in header
    frame.classIcon = frame.header:CreateTexture(nil, "ARTWORK")
    frame.classIcon:SetSize(26, 26)
    frame.classIcon:SetPoint("LEFT", 5, 0)
    frame.classIcon:SetTexture(classData.icon)
    frame.classIcon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
    
    -- Class name
    frame.className = frame.header:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    frame.className:SetPoint("LEFT", frame.classIcon, "RIGHT", 8, 0)
    frame.className:SetText(classData.name)
    frame.className:SetTextColor(classData.color.r, classData.color.g, classData.color.b)
    
    -- Total points display
    frame.pointsText = frame.header:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    frame.pointsText:SetPoint("RIGHT", -10, 0)
    frame.pointsText:SetText(classData.name .. " (0)")
    
    -- Spec panels container
    frame.specsContainer = CreateFrame("Frame", nil, frame)
    frame.specsContainer:SetPoint("TOPLEFT", frame.header, "BOTTOMLEFT", 0, -5)
    frame.specsContainer:SetPoint("BOTTOMRIGHT", 0, 0)
    
    -- Create 3 spec panels side by side
    frame.specPanels = {}
    local numSpecs = 3
    local availableWidth = FRAME_WIDTH - 60  -- Account for tab bar
    local specWidth = (availableWidth - (PANEL_PADDING * (numSpecs + 1))) / numSpecs
    
    for specIndex = 1, numSpecs do
        local specData = classData.specs[specIndex]
        if specData then
            local specPanel = Adv2.UI.CreateSpecPanel(frame.specsContainer, classId, specIndex, specData, specWidth)
            specPanel:SetPoint("TOPLEFT", PANEL_PADDING + (specIndex - 1) * (specWidth + PANEL_PADDING), 0)
            specPanel:SetPoint("BOTTOM", 0, 5)
            specPanel:SetWidth(specWidth)
            
            frame.specPanels[specIndex] = specPanel
        end
    end
    
    -- Update function
    function frame:Update()
        local totalPoints = 0
        for specIndex, panel in ipairs(self.specPanels) do
            if panel.Update then
                panel:Update()
            end
            local specPoints = panel.totalPoints or 0
            totalPoints = totalPoints + specPoints
        end
        self.pointsText:SetText(self.classData.name .. " (" .. totalPoints .. ")")
    end
    
    return frame
end

-- Create the Racials tab
function Adv2.UI.CreateRacialsTab(parent)
    local frame = CreateFrame("Frame", nil, parent)
    
    -- Header
    frame.header = CreateFrame("Frame", nil, frame)
    frame.header:SetHeight(30)
    frame.header:SetPoint("TOPLEFT", 0, 0)
    frame.header:SetPoint("TOPRIGHT", 0, 0)
    
    frame.headerBg = Adv2.UI.CreateSolidTexture(frame.header, 0.1, 0.1, 0.1, 0.9)
    frame.headerBg:SetAllPoints()
    
    frame.headerText = frame.header:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    frame.headerText:SetPoint("LEFT", 10, 0)
    frame.headerText:SetText("|cffffcc00Racial Abilities|r")
    
    frame.racialsCount = frame.header:CreateFontString(nil, "OVERLAY", "GameFontNormal")
    frame.racialsCount:SetPoint("RIGHT", -10, 0)
    frame.racialsCount:SetText("Racials: 0/2")
    frame.racialsCount:SetTextColor(0, 1, 0)
    
    -- Scroll frame for racials
    frame.scrollFrame = CreateFrame("ScrollFrame", nil, frame)
    frame.scrollFrame:SetPoint("TOPLEFT", frame.header, "BOTTOMLEFT", 5, -5)
    frame.scrollFrame:SetPoint("BOTTOMRIGHT", -5, 5)
    
    frame.scrollChild = CreateFrame("Frame", nil, frame.scrollFrame)
    frame.scrollChild:SetWidth(FRAME_WIDTH - 80)
    frame.scrollChild:SetHeight(1)  -- Will be adjusted based on content
    frame.scrollFrame:SetScrollChild(frame.scrollChild)
    
    -- Enable mouse wheel scrolling
    frame.scrollFrame:EnableMouseWheel(true)
    frame.scrollFrame:SetScript("OnMouseWheel", function(self, delta)
        local current = self:GetVerticalScroll()
        local maxScroll = max(0, frame.scrollChild:GetHeight() - self:GetHeight())
        local newScroll = max(0, min(maxScroll, current - (delta * 40)))
        self:SetVerticalScroll(newScroll)
    end)
    
    frame.racialButtons = {}
    
    -- Create racial buttons by race
    local function CreateRacialButtons()
        local playerRace = select(3, UnitRace("player"))
        local yOffset = 0
        local buttonSize = 36
        local buttonsPerRow = 8
        local spacing = 5
        local sectionSpacing = 20
        
        -- Get available width for proper layout
        local contentWidth = FRAME_WIDTH - 100
        
        -- Iterate through each race (including 10 for Blood Elf)
        local raceOrder = {1, 2, 3, 4, 5, 6, 7, 8, 10, 11}
        for _, raceId in ipairs(raceOrder) do
            local raceData = Adv2.Data.Racials[raceId]
            if raceData and raceData.racials and #raceData.racials > 0 then
                -- Race header
                local raceHeader = frame.scrollChild:CreateFontString(nil, "OVERLAY", "GameFontNormal")
                raceHeader:SetPoint("TOPLEFT", 5, yOffset)
                
                local raceName = raceData.name or ("Race " .. raceId)
                if raceId == playerRace then
                    raceHeader:SetText("|cff00ff00" .. raceName .. " (Your Race)|r")
                else
                    raceHeader:SetText("|cffffffcc" .. raceName .. "|r")
                end
                
                yOffset = yOffset - 18
                
                -- Create buttons for this race
                local col = 0
                for i, racial in ipairs(raceData.racials) do
                    local btn = CreateFrame("Button", nil, frame.scrollChild)
                    btn:SetSize(buttonSize, buttonSize)
                    
                    local xPos = 5 + col * (buttonSize + spacing)
                    btn:SetPoint("TOPLEFT", xPos, yOffset)
                    
                    -- Icon
                    btn.icon = btn:CreateTexture(nil, "ARTWORK")
                    btn.icon:SetAllPoints()
                    btn.icon:SetTexture(racial.icon)
                    btn.icon:SetTexCoord(0.08, 0.92, 0.08, 0.92)
                    
                    -- Border for pending/learned status
                    btn.border = btn:CreateTexture(nil, "OVERLAY")
                    btn.border:SetPoint("TOPLEFT", -2, 2)
                    btn.border:SetPoint("BOTTOMRIGHT", 2, -2)
                    btn.border:SetTexture("Interface\\Buttons\\UI-ActionButton-Border")
                    btn.border:SetBlendMode("ADD")
                    btn.border:Hide()
                    
                    -- Highlight
                    btn:SetHighlightTexture("Interface\\Buttons\\ButtonHilight-Square")
                    btn:GetHighlightTexture():SetBlendMode("ADD")
                    
                    btn.racialId = racial.id
                    btn.racialData = racial
                    
                    -- Click handler
                    btn:SetScript("OnClick", function(self)
                        if Adv2.playerData.learnedRacials[self.racialId] then
                            print("|cffffcc00[Multiclass]|r Already learned: " .. self.racialData.name)
                            return
                        end
                        
                        if Adv2.IsRacialPending(self.racialId) then
                            -- Remove from pending (deselect)
                            for i, pending in ipairs(Adv2.pendingRacials) do
                                if pending == self.racialId then
                                    table.remove(Adv2.pendingRacials, i)
                                    break
                                end
                            end
                            Adv2.UpdateUI()
                            return
                        end
                        
                        if Adv2.AddPendingRacial(self.racialId) then
                            print("|cff00ff00[Multiclass]|r Selected: " .. self.racialData.name .. " (click Confirm to learn)")
                        else
                            print("|cffff0000[Multiclass]|r No racial picks available!")
                        end
                    end)
                    
                    -- Tooltip
                    btn:SetScript("OnEnter", function(self)
                        GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
                        
                        -- Use helper with fallback for 3.3.5a
                        Adv2.UI.SetSpellTooltip(self.racialId, self.racialData.name, self.racialData.desc)
                        
                        -- Add status
                        if Adv2.playerData.learnedRacials[self.racialId] then
                            GameTooltip:AddLine(" ")
                            GameTooltip:AddLine("|cff00ff00LEARNED|r", 1, 1, 1)
                        elseif Adv2.IsRacialPending(self.racialId) then
                            GameTooltip:AddLine(" ")
                            GameTooltip:AddLine("|cffffff00PENDING - Click Confirm to learn|r", 1, 1, 1)
                        else
                            GameTooltip:AddLine(" ")
                            GameTooltip:AddLine("Click to select", 0.7, 0.7, 0.7)
                        end
                        
                        GameTooltip:Show()
                    end)
                    btn:SetScript("OnLeave", function() GameTooltip:Hide() end)
                    
                    -- Update function for this button
                    function btn:UpdateStatus()
                        if Adv2.playerData.learnedRacials[self.racialId] then
                            self.border:SetVertexColor(0, 1, 0)  -- Green for learned
                            self.border:Show()
                            self.icon:SetDesaturated(false)
                        elseif Adv2.IsRacialPending(self.racialId) then
                            self.border:SetVertexColor(1, 1, 0)  -- Yellow for pending
                            self.border:Show()
                            self.icon:SetDesaturated(false)
                        else
                            self.border:Hide()
                            self.icon:SetDesaturated(false)
                        end
                    end
                    
                    table.insert(frame.racialButtons, btn)
                    
                    col = col + 1
                    if col >= buttonsPerRow then
                        col = 0
                        yOffset = yOffset - (buttonSize + spacing)
                    end
                end
                
                -- End row
                if col > 0 then
                    yOffset = yOffset - (buttonSize + spacing)
                end
                
                yOffset = yOffset - sectionSpacing
            end
        end
        
        -- Set scroll child height
        frame.scrollChild:SetHeight(math.abs(yOffset) + 50)
    end
    
    CreateRacialButtons()
    
    -- Update function
    function frame:Update()
        local usedRacialPicks = Adv2.CountLearnedRacials()
        local pendingRacialPicks = #Adv2.pendingRacials
        local totalRacialPicks = Adv2.Config.INITIAL_RACIAL_PICKS
        
        self.racialsCount:SetText("Racials: " .. usedRacialPicks .. "+" .. pendingRacialPicks .. "/" .. totalRacialPicks)
        
        -- Update all buttons
        for _, btn in ipairs(self.racialButtons) do
            if btn.UpdateStatus then
                btn:UpdateStatus()
            end
        end
    end
    
    return frame
end
