-- Client integration — Adventurer class only hooks the talent micro-button

Adv2 = Adv2 or {}



function Adv2.ShouldUseClasslessUI()
    if Adv2.Config and Adv2.Config.CLIENT_ONLY then
        return true
    end
    if Adv2.playerData and Adv2.playerData.isAdventurer then
        return true
    end
    return Adv2.CheckIsAdventurer and Adv2.CheckIsAdventurer()
end



function Adv2.CloseBlizzardTalentUI()

    if PlayerTalentFrame and PlayerTalentFrame:IsShown() then

        HideUIPanel(PlayerTalentFrame)

    end

    if GlyphFrame and GlyphFrame:IsShown() then

        HideUIPanel(GlyphFrame)

    end

end



function Adv2.OpenClasslessUI()

    if not Adv2.initialized then

        Adv2.Init()

    end

    if not Adv2.ShouldUseClasslessUI() then

        return false

    end



    Adv2.CloseBlizzardTalentUI()



    local frame = Adv2.CreateMainFrame()

    if not frame then

        return false

    end



    if frame:IsShown() then

        if frame.rightCol and frame.rightCol.ReleaseSearchFocus then

            frame.rightCol:ReleaseSearchFocus()

        end

        frame:Hide()

    else

        frame:Show()

        Adv2.UpdateUI()

    end



    if type(UpdateMicroButtons) == "function" then

        UpdateMicroButtons()

    end

    return true

end



function ClasslessFrame_Freepick_LoadUI()

    if not Adv2.initialized then Adv2.Init() end

    Adv2.CreateMainFrame()

end



function ClasslessFrame_Freepick_Toggle()

    Adv2.OpenClasslessUI()

end



function Adv2.HandleTalentButtonClick()

    if not Adv2.ShouldUseClasslessUI() then

        return false

    end

    ClasslessFrame_Freepick_LoadUI()

    ClasslessFrame_Freepick_Toggle()

    return true

end



function Adv2.HookToggleTalentFrame()

    if type(ToggleTalentFrame) ~= "function" then return end

    if ToggleTalentFrame == Adv2._hookedToggleTalentFrame then return end



    local orig = ToggleTalentFrame

    Adv2._origToggleTalentFrame = orig



    _G.ToggleTalentFrame = function()

        if Adv2.HandleTalentButtonClick() then return end

        return orig()

    end

    Adv2._hookedToggleTalentFrame = _G.ToggleTalentFrame

end



function Adv2.HookPlayerTalentFrameToggle()

    if type(PlayerTalentFrame_Toggle) ~= "function" then return end

    if PlayerTalentFrame_Toggle == Adv2._hookedPlayerTalentFrameToggle then return end



    local orig = PlayerTalentFrame_Toggle

    Adv2._origPlayerTalentFrameToggle = orig



    _G.PlayerTalentFrame_Toggle = function(pet, suggestedTalentGroup)

        if not pet and Adv2.HandleTalentButtonClick() then return end

        return orig(pet, suggestedTalentGroup)

    end

    Adv2._hookedPlayerTalentFrameToggle = _G.PlayerTalentFrame_Toggle

end



function Adv2.HookTalentMicroButton()

    if not TalentMicroButton or TalentMicroButton._adv2ClickHooked then return end

    TalentMicroButton._adv2ClickHooked = true



    if TalentMicroButton.minLevel then

        TalentMicroButton._adv2OrigMinLevel = TalentMicroButton.minLevel

    end

    TalentMicroButton.minLevel = 1



    TalentMicroButton:SetScript("OnClick", function()

        if Adv2.HandleTalentButtonClick() then return end

        if Adv2._origToggleTalentFrame then

            Adv2._origToggleTalentFrame()

        elseif type(ToggleTalentFrame) == "function" then

            ToggleTalentFrame()

        end

    end)

end



function Adv2.HookUpdateMicroButtons()

    if type(UpdateMicroButtons) ~= "function" then return end

    if UpdateMicroButtons == Adv2._hookedUpdateMicroButtons then return end



    local orig = UpdateMicroButtons

    _G.UpdateMicroButtons = function()

        orig()

        if TalentMicroButton and Adv2.MainFrame and Adv2.MainFrame:IsShown() then

            TalentMicroButton:SetButtonState("PUSHED", 1)

        end

    end

    Adv2._hookedUpdateMicroButtons = _G.UpdateMicroButtons

end



function Adv2.HookClientUI()

    if not Adv2.ShouldUseClasslessUI() then

        return

    end

    Adv2.HookToggleTalentFrame()

    Adv2.HookPlayerTalentFrameToggle()

    Adv2.HookTalentMicroButton()

    Adv2.HookUpdateMicroButtons()

end



local hookFrame = CreateFrame("Frame")

hookFrame:RegisterEvent("PLAYER_LOGIN")

hookFrame:RegisterEvent("PLAYER_ENTERING_WORLD")

hookFrame:SetScript("OnEvent", function()
    Adv2.playerData.isAdventurer = Adv2.CheckIsAdventurer()
    if not Adv2.ShouldUseClasslessUI() then
        return
    end
    Adv2.HookClientUI()



    local retry = CreateFrame("Frame")

    local elapsed = 0

    retry:SetScript("OnUpdate", function(self, dt)

        elapsed = elapsed + dt

        if elapsed >= 0.5 then

            Adv2.HookClientUI()

            self:SetScript("OnUpdate", nil)

        end

    end)

end)


