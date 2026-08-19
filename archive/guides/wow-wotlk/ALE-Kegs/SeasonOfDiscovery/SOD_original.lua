-- FORKED FROM: https://github.com/notepadguyOfficial/acore_sod
-- Original author: notepadguyOfficial
-- Forked unmodified for comparison. See SOD.lua for the updated ALE version.

local PLAYER_EVENT_ON_LOGIN = 3
local PLAYER_EVENT_ON_LOGOUT = 4

    -- 80865 +50%
    -- 80866 +100%
    -- 80867 +150%
    -- 80867 +150%  <-- BUG in original: duplicate entry, should be 80868 +200%
    -- 80868 +200%
    -- 80869 +250%
    -- 80870 +300%

-- Player login event
local function OnLogin(event, player)
	player:CastSpell(player, 80870, true)
end

local function OnLogout(event, player)
	player:RemoveAura(80870)
end

RegisterPlayerEvent(PLAYER_EVENT_ON_LOGIN, OnLogin)
RegisterPlayerEvent(PLAYER_EVENT_ON_LOGOUT, OnLogout)
