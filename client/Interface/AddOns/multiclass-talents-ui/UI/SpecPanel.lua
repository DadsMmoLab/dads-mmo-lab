-- Center column: spec talent tree (matches proven backup layout)
Adv2 = Adv2 or {}
Adv2.UI = Adv2.UI or {}

local BUTTON_SIZE = 32
local TIER_HEIGHT = 62
local COL_WIDTH = 54
local PANEL_PADDING = 10
local ROW_HEIGHT = (Adv2.UI and Adv2.UI.TALENT_ROW_HEIGHT) or 46
local BRANCH_TEX = "Interface\\Buttons\\WHITE8X8"

local function ApplySpecBackground(panel, specData)
    if not panel.bg then return end
    if specData and specData.background then
        panel.bg:SetTexture("Interface\\TalentFrame\\" .. specData.background .. "-TopLeft")
        panel.bg:SetTexCoord(0, 1, 0, 1)
        panel.bg:SetVertexColor(1, 1, 1, 1)
    else
        panel.bg:SetTexture(BRANCH_TEX)
        panel.bg:SetTexCoord(0, 1, 0, 1)
        panel.bg:SetVertexColor(0.08, 0.05, 0.1, 0.95)
    end
    panel.bg:Show()
end

function Adv2.UI.CreateSpecPanel(parent, classId, specIndex, specData, width)
    local panel = CreateFrame("Frame", nil, parent)
    panel.classId = classId
    panel.specIndex = specIndex
    panel.specData = specData or {}
    panel.talentPool = {}
    panel.branchPool = {}
    panel.totalPoints = 0

    panel.bgFrame = CreateFrame("Frame", nil, panel)
    panel.bgFrame:SetAllPoints()
    panel.bgFrame:SetFrameLevel(panel:GetFrameLevel())

    panel.bg = panel.bgFrame:CreateTexture(nil, "BACKGROUND")
    panel.bg:SetAllPoints()
    ApplySpecBackground(panel, specData)

    panel.scrollFrame = CreateFrame("ScrollFrame", nil, panel)
    panel.scrollFrame:SetPoint("TOPLEFT", 4, -4)
    panel.scrollFrame:SetPoint("BOTTOMRIGHT", -4, 4)
    panel.scrollFrame:SetFrameLevel(panel:GetFrameLevel() + 2)
    panel.scrollFrame:EnableMouse(true)
    panel.scrollFrame:EnableMouseWheel(true)

    panel.scrollChild = CreateFrame("Frame", nil, panel.scrollFrame)
    panel.scrollChild:SetWidth(math.max(200, (width or 380) - 12))
    panel.scrollChild:SetHeight(120)
    panel.scrollFrame:SetScrollChild(panel.scrollChild)

    Adv2.UI.SetupMouseWheelScroll(panel.scrollFrame, panel.scrollChild, 28)

    function panel:ApplyBackground(specData)
        ApplySpecBackground(self, specData)
    end

    function panel:ClearBranches()
        for _, line in ipairs(self.branchPool) do
            line:Hide()
            line:ClearAllPoints()
        end
    end

    function panel:AcquireBranch(index)
        local line = self.branchPool[index]
        if not line then
            line = self.scrollChild:CreateTexture(nil, "BACKGROUND", nil, -8)
            line:SetTexture(BRANCH_TEX)
            line:SetVertexColor(0.45, 0.45, 0.45, 0.85)
            self.branchPool[index] = line
        end
        line:Show()
        return line
    end

    function panel:ConnectTalents(fromBtn, toBtn, branchIndex)
        if not fromBtn or not toBtn then return branchIndex end

        local fromAnchor = fromBtn.iconHolder or fromBtn
        local toAnchor = toBtn.iconHolder or toBtn
        local fromCol = fromBtn.talentData.col or 1
        local toCol = toBtn.talentData.col or 1

        branchIndex = branchIndex + 1
        local down = self:AcquireBranch(branchIndex)
        down:SetWidth(2)
        down:SetHeight(14)
        down:SetPoint("TOP", fromAnchor, "BOTTOM", 0, 0)

        if fromCol == toCol then
            branchIndex = branchIndex + 1
            local vert = self:AcquireBranch(branchIndex)
            vert:SetWidth(2)
            vert:SetPoint("TOP", down, "BOTTOM", 0, 0)
            vert:SetPoint("BOTTOM", toAnchor, "TOP", 0, 0)
        else
            branchIndex = branchIndex + 1
            local mid = self:AcquireBranch(branchIndex)
            mid:SetHeight(2)
            mid:SetPoint("TOP", down, "BOTTOM", 0, 0)
            if fromCol < toCol then
                mid:SetPoint("LEFT", down, "CENTER", 0, 0)
                mid:SetPoint("RIGHT", toAnchor, "TOP", 0, 0)
            else
                mid:SetPoint("RIGHT", down, "CENTER", 0, 0)
                mid:SetPoint("LEFT", toAnchor, "TOP", 0, 0)
            end

            branchIndex = branchIndex + 1
            local up = self:AcquireBranch(branchIndex)
            up:SetWidth(2)
            up:SetPoint("TOP", mid, "BOTTOM", 0, 0)
            up:SetPoint("BOTTOM", toAnchor, "TOP", 0, 0)
        end

        return branchIndex
    end

    function panel:LayoutTalents()
        self:ClearBranches()

        local talents = Adv2.Data.GetTalents(self.classId, self.specIndex) or {}
        local maxTier = 0
        local minCol, maxCol = 4, 1
        local used = 0
        local btnMap = {}

        for _, talent in ipairs(talents) do
            local col = talent.col or 1
            minCol = math.min(minCol, col)
            maxCol = math.max(maxCol, col)
            if (talent.tier or 1) > maxTier then maxTier = talent.tier or 1 end
        end
        if #talents == 0 then
            minCol, maxCol = 1, 4
        end

        local childW = self.scrollChild:GetWidth()
        if not childW or childW <= 0 then
            childW = math.max(200, (self:GetWidth() or 380) - 12)
        end
        local treeWidth = (maxCol - minCol + 1) * COL_WIDTH
        local xOffset = math.max(PANEL_PADDING, math.floor((childW - treeWidth) / 2))

        for _, talent in ipairs(talents) do
            used = used + 1
            local btn = self.talentPool[used]
            if not btn then
                btn = Adv2.UI.CreateTalentButton(self.scrollChild, talent, self.classId, self.specIndex)
                btn:SetFrameLevel(self.scrollChild:GetFrameLevel() + 2)
                self.talentPool[used] = btn
            else
                btn.talentData = talent
                btn.classId = self.classId
                btn.specIndex = self.specIndex
                if btn.RefreshIcon then btn:RefreshIcon() end
                btn:Show()
            end

            local col = talent.col or 1
            local tier = talent.tier or 1
            btn:ClearAllPoints()
            btn:SetPoint("TOPLEFT", xOffset + ((col - minCol) * COL_WIDTH) + math.floor((COL_WIDTH - BUTTON_SIZE) / 2), -PANEL_PADDING - ((tier - 1) * TIER_HEIGHT))
            btnMap[tier .. "_" .. col] = btn
        end

        for i = used + 1, #self.talentPool do
            self.talentPool[i]:Hide()
        end

        local branchUsed = 0
        for _, talent in ipairs(talents) do
            if talent.prereq then
                local fromBtn = btnMap[talent.prereq[1] .. "_" .. talent.prereq[2]]
                local toBtn = btnMap[(talent.tier or 1) .. "_" .. (talent.col or 1)]
                branchUsed = self:ConnectTalents(fromBtn, toBtn, branchUsed)
            end
        end
        for i = branchUsed + 1, #self.branchPool do
            self.branchPool[i]:Hide()
        end

        self.scrollChild:SetHeight(math.max(120, (maxTier * TIER_HEIGHT) + PANEL_PADDING * 2 + ROW_HEIGHT))
        self.scrollFrame:SetVerticalScroll(0)
    end

    function panel:Relayout()
        local w = self:GetWidth()
        if w and w > 0 then
            self.scrollChild:SetWidth(math.max(200, w - 12))
        end
        self:LayoutTalents()
    end

    function panel:Update()
        local total = 0
        for _, btn in ipairs(self.talentPool) do
            if btn:IsShown() and btn.Update then
                total = total + (btn:Update() or 0)
            end
        end
        self.totalPoints = total
        return total
    end

    function panel:SetSpec(classId, specIndex, specData)
        self.classId = classId
        self.specIndex = specIndex
        self.specData = specData or {}
        self:ApplyBackground(specData)
        self:LayoutTalents()
        self:Update()
    end

    panel:LayoutTalents()
    panel:Update()
    return panel
end
