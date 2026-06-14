-- ExchangeNpc.lua  —  ALE-compatible rewrite
-- ALE (AzerothCore Lua Engine) version
-- Forked from: 55Honey/Acore_ExchangeNpc (commit 6e99cfc)
-- Updated for ALE by: Dad's MMO Lab
--
-- ADMIN GUIDE:
--   1. Apply database/world/ExchangeNpc_Up.sql to acore_world
--   2. Copy this file to your lua_scripts/ directory
--   3. Reload: .reload ale  (or restart worldserver)
--   4. Spawn NPCs with .npc add <entry> — see coordinates in config below
--
-- GM GUIDE:     Nothing to do.
-- Player guide: Talk to any of the spawned NPCs to exchange items/honor/tokens.
------------------------------------------------------------------------------------------------

-- Double-load guard (safe across .reload ale cycles)
if _G.ExchangeNpcLoaded then return end
_G.ExchangeNpcLoaded = true

local Config = {}

Config.TurnInItemEntry        = {}
Config.TurnInItemAmount       = {}
Config.GainItemEntry          = {}
Config.GainItemAmount         = {}
Config.ItemGossipOptionTextA  = {}
Config.ItemGossipOptionTextB  = {}
Config.SendAsOneMail          = {}
Config.TurnInHonorAmount      = {}
Config.GainGoldAmount         = {}
Config.TokenNpcMapId          = {}
Config.TokenNpcX              = {}
Config.TokenNpcY              = {}
Config.TokenNpcZ              = {}
Config.TokenNpcO              = {}
Config.MarkEntry              = {}
Config.MarkCount              = {}
Config.GainTokenEntry         = {}
Config.Requirement            = {}
Config.TokenGossipOptionText  = {}
Config.ItemNpcMapId           = {}
Config.ItemNpcX               = {}
Config.ItemNpcY               = {}
Config.ItemNpcZ               = {}
Config.ItemNpcO               = {}
Config.HonorNpcMapId          = {}
Config.HonorNpcX              = {}
Config.HonorNpcY              = {}
Config.HonorNpcZ              = {}
Config.HonorNpcO              = {}

local GOSSIP_EVENT_ON_HELLO  = 1
local GOSSIP_EVENT_ON_SELECT = 2
local OPTION_ICON_CHAT       = 0
local GOSSIP_ICON_VENDOR     = 1
-- ALE: server event 16 = lua state closing (clean up spawned NPCs)
local SERVER_EVENT_ON_LUA_STATE_CLOSE = 16

Config.ItemNpcOn  = 1   -- 1 = spawn item-exchange NPC
Config.HonorNpcOn = 1   -- 1 = spawn honor-to-gold NPC
Config.TokenNpcOn = 0   -- 1 = spawn PvP token NPC

------------------------------------------------------------------------------------------------
-- Item Exchange NPC
------------------------------------------------------------------------------------------------
Config.ItemNpcEntry      = 1116001
Config.ItemNpcInstanceId = 0

Config.ItemNpcMapId[1] = 530
Config.ItemNpcX[1]     = -1814.3
Config.ItemNpcY[1]     = 5292.34
Config.ItemNpcZ[1]     = -12.42
Config.ItemNpcO[1]     = 2.014

Config.ItemNpcMapId[2] = 1
Config.ItemNpcX[2]     = -7153
Config.ItemNpcY[2]     = -3740
Config.ItemNpcZ[2]     = 8.4
Config.ItemNpcO[2]     = 5.06

Config.ItemGossipText              = 92101
Config.ItemGossipConfirmationText  = 92102
Config.NotEnoughItemsMessage       = 'You do not have the required items at hand.'
Config.ItemExchangeSuccessfulMessage = 'Thank you! The exchange will be sent to you in a mail by my assistants as soon as possible.'
Config.ItemMailSubject             = 'Item Exchange'
Config.ItemMailMessage             = 'Greetings, Time Traveler! Here are the requested substitutes for the provided items.'

--[==[  Example item exchange pairs — uncomment and adjust to taste:
Config.TurnInItemEntry[1]        = 14344  -- Large Brilliant Shard
Config.TurnInItemAmount[1]       = 1
Config.GainItemEntry[1]          = 22447  -- Lesser Planar Essence
Config.GainItemAmount[1]         = 1
Config.ItemGossipOptionTextA[1]  = ' of my Large Brilliant Shards and ask Chromie to send me '
Config.ItemGossipOptionTextB[1]  = ' of her Lesser Planar Essence by mail.'
Config.SendAsOneMail[1]          = false
--]==]

------------------------------------------------------------------------------------------------
-- Honor Exchange NPC
------------------------------------------------------------------------------------------------
Config.HonorNpcEntry      = 1116002
Config.HonorNpcInstanceId = 0

Config.HonorNpcMapId[1] = 530
Config.HonorNpcX[1]     = -1802.67
Config.HonorNpcY[1]     = 5296.19
Config.HonorNpcZ[1]     = -12.42
Config.HonorNpcO[1]     = 2.15

Config.HonorNpcMapId[2] = 0
Config.HonorNpcX[2]     = -14288.9
Config.HonorNpcY[2]     = 533.9
Config.HonorNpcZ[2]     = 8.8
Config.HonorNpcO[2]     = 3.64

Config.HonorGossipText             = 92103
Config.HonorGossipConfirmationText = 92104
Config.NotEnoughHonorMessage       = 'You do not have the required amount of Honor.'
Config.HonorExchangeSuccessfulMessage = 'Thank you! Your Honor was converted to Gold.'

Config.TurnInHonorAmount[1] = 1000;   Config.GainGoldAmount[1] = 4
Config.TurnInHonorAmount[2] = 5000;   Config.GainGoldAmount[2] = 20
Config.TurnInHonorAmount[3] = 10000;  Config.GainGoldAmount[3] = 40
Config.TurnInHonorAmount[4] = 50000;  Config.GainGoldAmount[4] = 200

------------------------------------------------------------------------------------------------
-- Token Exchange NPC  (disabled by default — set Config.TokenNpcOn = 1 to enable)
------------------------------------------------------------------------------------------------
Config.TokenNpcEntry      = 1116003
Config.ShowAllTokens      = 0   -- 1 = show all tiers regardless of requirements
Config.TokenNpcInstanceId = 0
Config.TokenGossipText    = 92105

Config.TokenNpcMapId[1] = 1
Config.TokenNpcX[1]     = 1649.44
Config.TokenNpcY[1]     = -4221.64
Config.TokenNpcZ[1]     = 56.38
Config.TokenNpcO[1]     = 1.16

Config.TokenNpcMapId[2] = 0
Config.TokenNpcX[2]     = -8776.29
Config.TokenNpcY[2]     = 427.62
Config.TokenNpcZ[2]     = 105.23
Config.TokenNpcO[2]     = 4.57

Config.MissingTokenConditionsMessage = 'You do not meet all conditions to obtain this.'
Config.TokenExchangeSuccessfulMessage = 'Thank you! The token was added to your inventory.'
Config.TokenGossipRefundText = 'I made a mistake and want to refund a token in my inventory. I\'m aware of the caps for marks and honor and the refund will not exceed them.'

-- HonorPrice[slot]: 1=Feet 2=Hands 3=Legs 4=Chest 5=Head 6=Shoulders 7=2H-Weapon 8=1H-Weapon 9=Offhand
Config.HonorPrice = { 30000, 30000, 35000, 50000, 40000, 35000, 75000, 50000, 25000 }

-- 20558=Warsong Marks  20559=Arathi Marks  20560=Alterac Valley Marks
Config.MarkEntry[1] = {20558,20559,20560}; Config.MarkCount[1] = {10,10,5}
Config.GainTokenEntry[1] = 34858; Config.Requirement[1] = 0
Config.TokenGossipOptionText[1] = 'It costs 30000 honor, 10 Warsong marks, 10 Arathi marks and 5 Alterac marks to buy a token for Boots.'

Config.MarkEntry[2] = {20558,20559,20560}; Config.MarkCount[2] = {10,10,5}
Config.GainTokenEntry[2] = 31093; Config.Requirement[2] = 0
Config.TokenGossipOptionText[2] = 'It costs 30000 honor, 10 Warsong marks, 10 Arathi marks and 5 Alterac marks to buy a token for Gloves.'

Config.MarkEntry[3] = {20558,20559,20560}; Config.MarkCount[3] = {15,10,10}
Config.GainTokenEntry[3] = 31099; Config.Requirement[3] = 0
Config.TokenGossipOptionText[3] = 'It costs 35000 honor, 15 Warsong marks, 10 Arathi marks and 10 Alterac marks to buy a token for Leg Armor.'

Config.MarkEntry[4] = {20558,20559,20560}; Config.MarkCount[4] = {10,15,10}
Config.GainTokenEntry[4] = 31090; Config.Requirement[4] = 0
Config.TokenGossipOptionText[4] = 'It costs 50000 honor, 10 Warsong marks, 15 Arathi marks and 10 Alterac marks to buy a token for Chest Armor.'

Config.MarkEntry[5] = {20558,20559,20560}; Config.MarkCount[5] = {15,15,10}
Config.GainTokenEntry[5] = 31096; Config.Requirement[5] = 0
Config.TokenGossipOptionText[5] = 'It costs 40000 honor, 15 Warsong marks, 15 Arathi marks and 10 Alterac marks to buy a token for Head Armor.'

Config.MarkEntry[6] = {20558,20559,20560}; Config.MarkCount[6] = {15,15,15}
Config.GainTokenEntry[6] = 31102; Config.Requirement[6] = 0
Config.TokenGossipOptionText[6] = 'It costs 35000 honor, 15 Warsong marks, 15 Arathi marks and 15 Alterac marks to buy a token for Shoulderpads.'

Config.MarkEntry[7] = {20558,20559,20560}; Config.MarkCount[7] = {40,40,20}
Config.GainTokenEntry[7] = 34855; Config.Requirement[7] = 0
Config.TokenGossipOptionText[7] = 'It costs 75000 honor, 40 Warsong marks, 40 Arathi marks and 20 Alterac marks to buy a token for a two-handed Weapon.'

Config.MarkEntry[8] = {20558,20559,20560}; Config.MarkCount[8] = {25,25,10}
Config.GainTokenEntry[8] = 34852; Config.Requirement[8] = 0
Config.TokenGossipOptionText[8] = 'It costs 50000 honor, 25 Warsong marks, 25 Arathi marks and 10 Alterac marks to buy a token for a one-handed Weapon.'

Config.MarkEntry[9] = {20558,20559,20560}; Config.MarkCount[9] = {15,15,10}
Config.GainTokenEntry[9] = 34853; Config.Requirement[9] = 0
Config.TokenGossipOptionText[9] = 'It costs 25000 honor, 15 Warsong marks, 15 Arathi marks and 10 Alterac marks to buy a token for an Offhand Weapon.'

------------------------------------------
-- NO ADJUSTMENTS REQUIRED BELOW THIS LINE
------------------------------------------

local npcItemObjectGuid  = {}
local npcHonorObjectGuid = {}
local npcTokenObjectGuid = {}

-- Item NPC logic -------------------------------------------------------------------------

local function eI_BuildExchangeString(id, amount)
    return 'Take ' .. Config.TurnInItemAmount[id] * amount
        .. Config.ItemGossipOptionTextA[id]
        .. Config.GainItemAmount[id] * amount
        .. Config.ItemGossipOptionTextB[id]
end

local function eI_ItemOnHello(event, player, creature)
    if not player then return end
    if Config.TurnInItemEntry and Config.TurnInItemEntry[1] then
        for n = 1, #Config.TurnInItemEntry do
            player:GossipMenuAddItem(OPTION_ICON_CHAT, eI_BuildExchangeString(n, 1), Config.ItemNpcEntry, n - 1)
        end
    end
    player:GossipMenuAddItem(GOSSIP_ICON_VENDOR, "Let's trade", Config.ItemNpcEntry, 10000)
    player:GossipSendMenu(Config.ItemGossipText, creature, 0)
end

local function eI_ItemOnGossipSelect(event, player, object, sender, intid, code, menu_id)
    if not player then return end
    if intid < 1000 then
        player:GossipComplete()
        local ExchangeId = intid + 1
        -- FIX: original used undefined 'id' here; corrected to ExchangeId
        if Config.TurnInItemAmount[ExchangeId] == nil then
            PrintError('ExchangeNpc: ExchangeId ' .. ExchangeId .. ' not found in config.')
            return
        end
        player:GossipMenuAddItem(OPTION_ICON_CHAT, eI_BuildExchangeString(ExchangeId, 1),  Config.ItemNpcEntry, intid + 1000)
        player:GossipMenuAddItem(OPTION_ICON_CHAT, eI_BuildExchangeString(ExchangeId, 5),  Config.ItemNpcEntry, intid + 2000)
        player:GossipMenuAddItem(OPTION_ICON_CHAT, eI_BuildExchangeString(ExchangeId, 10), Config.ItemNpcEntry, intid + 3000)
        player:GossipMenuAddItem(OPTION_ICON_CHAT, eI_BuildExchangeString(ExchangeId, 20), Config.ItemNpcEntry, intid + 4000)
        player:GossipSendMenu(Config.ItemGossipConfirmationText, object, 0)
    elseif intid == 10000 then
        player:SendListInventory(object)
    else
        local ExchangeId, Amount, GiveAmount
        if intid >= 4000 then
            ExchangeId = intid - 3999; Amount = 20
        elseif intid >= 3000 then
            ExchangeId = intid - 2999; Amount = 10
        elseif intid >= 2000 then
            ExchangeId = intid - 1999; Amount = 5
        else
            ExchangeId = intid - 999;  Amount = 1
        end
        GiveAmount = Config.TurnInItemAmount[ExchangeId] * Amount
        local playerGuid = tonumber(tostring(player:GetGUID()))
        if player:HasItem(Config.TurnInItemEntry[ExchangeId], GiveAmount, false) then
            player:RemoveItem(Config.TurnInItemEntry[ExchangeId], GiveAmount)
            if Config.SendAsOneMail[ExchangeId] == true then
                -- FIX: reward amount is GainItemAmount * Amount, not GiveAmount (turn-in qty)
                SendMail(Config.ItemMailSubject, Config.ItemMailMessage, playerGuid, 0, 61, 5, 0, 0,
                    Config.GainItemEntry[ExchangeId], Config.GainItemAmount[ExchangeId] * Amount)
            else
                for n = 1, Amount do
                    SendMail(Config.ItemMailSubject, Config.ItemMailMessage, playerGuid, 0, 61, 5, 0, 0,
                        Config.GainItemEntry[ExchangeId], Config.GainItemAmount[ExchangeId])
                end
            end
            player:SendBroadcastMessage(Config.ItemExchangeSuccessfulMessage)
        else
            player:SendBroadcastMessage(Config.NotEnoughItemsMessage)
        end
        player:GossipComplete()
    end
end

-- Honor NPC logic ------------------------------------------------------------------------

local function eI_HonorOnHello(event, player, creature)
    if not player then return end
    for n = 1, #Config.TurnInHonorAmount do
        local txt = 'Turn in ' .. Config.TurnInHonorAmount[n] .. ' honor to gain ' .. Config.GainGoldAmount[n] .. ' gold.'
        player:GossipMenuAddItem(OPTION_ICON_CHAT, txt, Config.HonorNpcEntry, n - 1)
    end
    player:GossipSendMenu(Config.HonorGossipText, creature, 0)
end

local function eI_HonorOnGossipSelect(event, player, object, sender, intid, code, menu_id)
    if not player then return end
    if intid < 1000 then
        player:GossipComplete()
        local ExchangeId = intid + 1
        local txt = 'Yes! Turn in ' .. Config.TurnInHonorAmount[ExchangeId] .. ' honor to gain ' .. Config.GainGoldAmount[ExchangeId] .. ' gold.'
        player:GossipMenuAddItem(OPTION_ICON_CHAT, txt, Config.HonorNpcEntry, intid + 1000)
        player:GossipSendMenu(Config.HonorGossipConfirmationText, object, 0)
    else
        local ExchangeId   = intid - 999
        local playerHonor  = player:GetHonorPoints()
        if playerHonor >= Config.TurnInHonorAmount[ExchangeId] then
            player:SetHonorPoints(playerHonor - Config.TurnInHonorAmount[ExchangeId])
            player:ModifyMoney(Config.GainGoldAmount[ExchangeId] * 10000)
            player:SendBroadcastMessage(Config.HonorExchangeSuccessfulMessage)
        else
            player:SendBroadcastMessage(Config.NotEnoughHonorMessage)
        end
        player:GossipComplete()
    end
end

-- Token NPC logic ------------------------------------------------------------------------

local function eI_HasPreviousToken(player, intid)
    if Config.Requirement[intid] == 0 then return true end
    for n = 1, #Config.Requirement[intid] do
        if player:HasItem(Config.Requirement[intid][n], 1, true) then return true end
    end
    return false
end

local function eI_HasHonorAndMarksAndRequiredItems(player, intid)
    if not Config.MarkEntry[intid] then
        PrintError('ExchangeNpc: Config.MarkEntry[' .. intid .. '] missing')
        return false
    end
    for n = 1, #Config.MarkEntry[intid] do
        if not player:HasItem(Config.MarkEntry[intid][n], Config.MarkCount[intid][n], false) then
            return false
        end
    end
    if player:GetHonorPoints() < Config.HonorPrice[intid] then return false end
    return eI_HasPreviousToken(player, intid)
end

local function RemoveTheHonorAndMarks(player, intid)
    for n = 1, #Config.MarkEntry[intid] do
        player:RemoveItem(Config.MarkEntry[intid][n], Config.MarkCount[intid][n])
    end
    player:ModifyHonorPoints(-1 * Config.HonorPrice[intid])
end

local function GiveTheToken(player, intid)
    return player:AddItem(Config.GainTokenEntry[intid], 1) ~= nil
end

local function eI_TokenOnHello(event, player, creature)
    if not player then return end
    player:GossipMenuAddItem(OPTION_ICON_CHAT, Config.TokenGossipRefundText, Config.TokenNpcEntry, 1000)
    if not player:HasAchieved(452) and not player:HasAchieved(440) then
        player:SendBroadcastMessage('You need at least 10k honorable kills to buy epic PvP items.')
        player:GossipSendMenu(Config.TokenGossipText, creature, 0)
        return
    end
    for n = 1, #Config.GainTokenEntry do
        if Config.ShowAllTokens == 1 or eI_HasPreviousToken(player, n) then
            player:GossipMenuAddItem(OPTION_ICON_CHAT, Config.TokenGossipOptionText[n], Config.TokenNpcEntry, n)
        end
    end
    player:GossipSendMenu(Config.TokenGossipText, creature, 0)
end

local function eI_TokenOnGossipSelect(event, player, object, sender, intid, code, menu_id)
    if not player then
        PrintError('ExchangeNpc: nil player in eI_TokenOnGossipSelect')
        return
    end
    if intid == 1000 then
        for n = 1, #Config.GainTokenEntry do
            if player:HasItem(Config.GainTokenEntry[n], 1, false) then
                player:RemoveItem(Config.GainTokenEntry[n], 1)
                player:ModifyHonorPoints(Config.HonorPrice[n])
                for m = 1, #Config.MarkEntry[n] do
                    player:AddItem(Config.MarkEntry[n][m], Config.MarkCount[n][m])
                end
                player:GossipComplete()
                return
            end
        end
        return
    end
    if intid > 6 and not player:HasAchieved(439) and not player:HasAchieved(451) then
        player:SendBroadcastMessage('You need at least 20k honorable kills to buy epic PvP weapons.')
        return
    end
    if eI_HasHonorAndMarksAndRequiredItems(player, intid) then
        if GiveTheToken(player, intid) then
            RemoveTheHonorAndMarks(player, intid)
        else
            player:SendBroadcastMessage('You need at least one empty inventory slot.')
        end
    else
        player:SendBroadcastMessage(Config.MissingTokenConditionsMessage)
    end
    player:GossipComplete()
end

-- Cleanup on .reload ale / server shutdown -----------------------------------------------
-- Despawn all Lua-spawned NPCs before the state closes so they don't linger as ghost copies.
local function eI_OnLuaStateClose(event)
    local function despawnGuids(guidTable, mapIdTable)
        -- Use pairs() — integer keys may be sparse if any spawn failed
        for k, guid in pairs(guidTable) do
            if guid then
                local map = GetMapById(mapIdTable[k])
                if map then
                    local npc = map:GetWorldObject(guid)
                    if npc then npc:DespawnOrUnsummon(0) end
                end
            end
        end
    end
    despawnGuids(npcItemObjectGuid,  Config.ItemNpcMapId)
    despawnGuids(npcHonorObjectGuid, Config.HonorNpcMapId)
    despawnGuids(npcTokenObjectGuid, Config.TokenNpcMapId)
end

-- Startup: spawn NPCs and register events ------------------------------------------------
-- FIX: nil check now happens BEFORE calling :GetGUID() to avoid a crash if spawn fails.
local function spawnAndTrack(guidTable, entry, instanceId, mapIdTable, xTable, yTable, zTable, oTable, spells)
    for k in pairs(mapIdTable) do
        local npc = PerformIngameSpawn(1, entry, mapIdTable[k], instanceId,
            xTable[k], yTable[k], zTable[k], oTable[k])
        if npc then
            guidTable[k] = npc:GetGUID()
            if spells then
                for _, sid in ipairs(spells) do npc:CastSpell(npc, sid, true) end
            end
        else
            PrintError('ExchangeNpc: PerformIngameSpawn failed for entry ' .. entry .. ' on map ' .. mapIdTable[k])
        end
    end
end

if Config.ItemNpcOn == 1 then
    spawnAndTrack(npcItemObjectGuid, Config.ItemNpcEntry, Config.ItemNpcInstanceId,
        Config.ItemNpcMapId, Config.ItemNpcX, Config.ItemNpcY, Config.ItemNpcZ, Config.ItemNpcO,
        {65712, 48200})
    RegisterCreatureGossipEvent(Config.ItemNpcEntry, GOSSIP_EVENT_ON_HELLO,  eI_ItemOnHello)
    RegisterCreatureGossipEvent(Config.ItemNpcEntry, GOSSIP_EVENT_ON_SELECT, eI_ItemOnGossipSelect)
end

if Config.HonorNpcOn == 1 then
    spawnAndTrack(npcHonorObjectGuid, Config.HonorNpcEntry, Config.HonorNpcInstanceId,
        Config.HonorNpcMapId, Config.HonorNpcX, Config.HonorNpcY, Config.HonorNpcZ, Config.HonorNpcO,
        {65712})
    RegisterCreatureGossipEvent(Config.HonorNpcEntry, GOSSIP_EVENT_ON_HELLO,  eI_HonorOnHello)
    RegisterCreatureGossipEvent(Config.HonorNpcEntry, GOSSIP_EVENT_ON_SELECT, eI_HonorOnGossipSelect)
end

if Config.TokenNpcOn == 1 then
    spawnAndTrack(npcTokenObjectGuid, Config.TokenNpcEntry, Config.TokenNpcInstanceId,
        Config.TokenNpcMapId, Config.TokenNpcX, Config.TokenNpcY, Config.TokenNpcZ, Config.TokenNpcO,
        nil)
    RegisterCreatureGossipEvent(Config.TokenNpcEntry, GOSSIP_EVENT_ON_HELLO,  eI_TokenOnHello)
    RegisterCreatureGossipEvent(Config.TokenNpcEntry, GOSSIP_EVENT_ON_SELECT, eI_TokenOnGossipSelect)
end

if Config.ItemNpcOn == 1 or Config.HonorNpcOn == 1 or Config.TokenNpcOn == 1 then
    RegisterServerEvent(SERVER_EVENT_ON_LUA_STATE_CLOSE, eI_OnLuaStateClose)
end
