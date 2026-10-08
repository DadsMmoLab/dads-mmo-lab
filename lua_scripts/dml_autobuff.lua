-- dml_autobuff.lua v3 — ALE server-side auto-buffer for dad's server.
--
-- #buffs on              enable   (per character, persisted)
-- #buffs off             disable
-- #buffs                 status: every toggle + every group choice
-- #buffs <buff> on|off   toggle one buff line (fort, motw, thorns, ...)
-- #buffs <group> <pick>  choose in an exclusive group (shout, blessing, seal,
--                        aura, aspect, magearmor, lockarmor, shield, magic,
--                        mh, oh) — every group accepts "none"
-- #buffs reagents on|off keeper reagent kit (see below)
--
-- Buffs are applied as DIRECT AURAS every 10s to the player and every summon
-- they own within 45y: free, no reagents, auto-renew. Weapon imbues are
-- applied as temp enchants (ids extracted from this server's Spell.dbc).
--
-- KEEPER REAGENT KIT: the CLIENT checks reagents from its own Spell.dbc
-- before it sends a cast, so server-side reagent stripping alone cannot free
-- manual casts (voidwalker, Greater Blessings, prayers...). While enabled we
-- keep exactly ONE of each casting reagent in the bags — the client sees it
-- and allows the cast; the server (reagent-strip patch) never consumes it.
--
-- Deliberately NOT auto-applied: stances, presences, druid forms.
-- Seals ARE offered (group 'seal', default none) per user request 2026-08-16.
--
-- Opt-in doubles as the bot filter; persisted in acore_characters
-- .dml_autobuff_kv (sanctioned write surface, user-approved 2026-08-15).
-- Values written to SQL come only from the controlled vocabularies below.

-- ----------------------------------------------------------- server switch
-- Unbound.AutoBuff (mod_unbound.conf) must be 1 for this script to do anything.
-- It is checked before the table is created or any event is registered, so a
-- server with the switch off carries no trace of this script. ALE hands a
-- config value back as a number ("1" -> 1), a boolean ("true") or a string, so
-- compare the text form.
do
  local setting = tostring(GetConfigValue("Unbound.AutoBuff"))
  if setting ~= "1" and setting ~= "true" then
    print("[dml_autobuff] off (Unbound.AutoBuff = 0)")
    return
  end
end

local UPDATE_MS  = 10000
local SUMMON_YDS = 45

-- ------------------------------------------------------------- toggle lines
-- ids: TOP RANK FIRST, first HasSpell hit wins. shared=false -> player only.

local TOGGLES = {
  { key="fort",       name="PW: Fortitude",     shared=true,  def=true,  ids={48161,25389,10938,10937,2791,1245,1244,1243} },
  { key="spirit",     name="Divine Spirit",     shared=true,  def=true,  ids={48073,25312,27841,14819,14818,14752} },
  { key="shadowprot", name="Shadow Protection", shared=true,  def=true,  ids={48169,25433,10958,10957,976} },
  { key="motw",       name="Mark of the Wild",  shared=true,  def=true,  ids={48469,26990,9885,9884,8907,5234,6756,5232,1126} },
  { key="thorns",     name="Thorns",            shared=true,  def=true,  ids={53307,26992,9910,9756,8914,1075,782,467} },
  { key="intellect",  name="Arcane Intellect",  shared=true,  def=true,  ids={42995,27126,10157,10156,1461,1460,1459} },
  { key="horn",       name="Horn of Winter",    shared=true,  def=true,  ids={57623,57330} },
  { key="trueshot",   name="Trueshot Aura",     shared=true,  def=true,  ids={19506} },
  { key="innerfire",  name="Inner Fire",        shared=false, def=true,  ids={48168,48040,25431,10952,10951,1006,602,7128,588} },
  { key="fearward",   name="Fear Ward",         shared=false, def=true,  ids={6346} },
  { key="rfury",      name="Righteous Fury",    shared=false, def=false, ids={25780} },
}

-- --------------------------------------------------------- exclusive groups
-- Spell groups apply the pick to the player; mh/oh apply a weapon enchant.

local GROUPS = {
  shout     = { label="Warrior shout", def="battle",
                choices={ battle=47436, commanding=47440 } },
  -- Same-caster blessings are mutually exclusive in 3.3.5 (Might knocks off
  -- Kings, Wisdom knocks off Might...), so blessings are a GROUP, not toggles
  -- — the old kings/might/wisdom toggles fought each other every tick and
  -- only the last one applied survived. Chains TOP RANK FIRST like TOGGLES.
  -- NB Kings (20217) is a Protection TALENT on 3.3.5 — a paladin without the
  -- talent point silently gets nothing; pick might/wisdom there.
  blessing  = { label="Paladin blessing", def="might", shared=true,
                choices={ kings={20217},
                          might={48932,48931,27140,25291,19838,19837,19836,19835,19834,19740},
                          wisdom={48936,48935,27142,25290,19854,19853,19852,19850,19742},
                          sanctuary={20911} } },
  seal      = { label="Paladin seal", def="none",
                choices={ righteousness=21084, command=20375, vengeance=31801,
                          corruption=53736, justice=20164 } },
  aura      = { label="Paladin aura", def="retribution",
                choices={ devotion=48942, retribution=54043, concentration=19746,
                          crusader=32223, shadowresist=48943, frostresist=48945,
                          fireresist=48947 } },
  aspect    = { label="Hunter aspect", def="dragonhawk",
                choices={ dragonhawk=61847, hawk=27044, monkey=13163, cheetah=5118,
                          pack=13159, viper=34074, wild=49071 } },
  magearmor = { label="Mage armor", def="molten",
                choices={ molten=43046, mage=43024, ice=43008, frost=168 } },
  lockarmor = { label="Warlock armor", def="fel",
                choices={ fel=47893, demon=47889 } },
  shield    = { label="Shaman shield", def="lightning",
                choices={ lightning=49281, water=57960, earth=49284 } },
  magic     = { label="Amplify/Dampen Magic", def="none",
                choices={ amplify=43017, dampen=43015 } },
  mh        = { label="Main-hand imbue", def="windfury", enchant=true,
                choices={ windfury={spell=58804,ench=3787}, flametongue={spell=58790,ench=3781},
                          frostbrand={spell=58796,ench=3784}, earthliving={spell=51994,ench=3350} } },
  oh        = { label="Off-hand imbue", def="flametongue", enchant=true,
                choices={ windfury={spell=58804,ench=3787}, flametongue={spell=58790,ench=3781},
                          frostbrand={spell=58796,ench=3784}, earthliving={spell=51994,ench=3350} } },
}

local GROUP_ORDER = { "shout","blessing","seal","aura","aspect","magearmor","lockarmor","shield","magic","mh","oh" }

-- One of each casting reagent, kept in the bags so the CLIENT allows the
-- cast; the server-side reagent strip means they are never consumed.
local KEEPER_ITEMS = {
  6265,  -- Soul Shard
  21177, -- Symbol of Kings
  17029, -- Sacred Candle
  17028, -- Holy Candle
  44615, -- Devout Candle
  17020, -- Arcane Powder
  17031, -- Rune of Teleportation
  17032, -- Rune of Portals
  17030, -- Ankh
  22147, -- Flintweed Seed
  44614, -- Starleaf Seed
  22148, -- Wild Quillvine
  44605, -- Wild Spineleaf
  37201, -- Corpse Dust
  5565,  -- Infernal Stone
  16583, -- Demonic Figurine
}

local EQ_MAINHAND, EQ_OFFHAND = 15, 16
local TEMP_ENCHANT_SLOT = 1

-- ---------------------------------------------------------------- state + db

local state = {} -- [guidLow] = { enabled, reagents, t={key=bool}, g={key=choice} }

local function defaultState()
  local s = { enabled=false, reagents=true, t={}, g={} }
  for _, line in ipairs(TOGGLES) do s.t[line.key] = line.def end
  for key, grp in pairs(GROUPS) do s.g[key] = grp.def end
  return s
end

CharDBExecute([[
CREATE TABLE IF NOT EXISTS `dml_autobuff_kv` (
  `guid` INT UNSIGNED NOT NULL,
  `k`    VARCHAR(32)  NOT NULL,
  `v`    VARCHAR(16)  NOT NULL,
  PRIMARY KEY (`guid`, `k`)
)
]])

local function loadState(guid)
  local s = defaultState()
  local q = CharDBQuery("SELECT k, v FROM dml_autobuff_kv WHERE guid = " .. guid)
  if q then
    repeat
      local k, v = q:GetString(0), q:GetString(1)
      if k == "enabled" then s.enabled = v == "1"
      elseif k == "reagents" then s.reagents = v == "1"
      elseif k:sub(1,2) == "t_" then
        local key = k:sub(3)
        if s.t[key] ~= nil then s.t[key] = v == "1" end
      elseif k:sub(1,2) == "g_" then
        local key = k:sub(3)
        if GROUPS[key] and (v == "none" or GROUPS[key].choices[v]) then s.g[key] = v end
      end
    until not q:NextRow()
  end
  state[guid] = s
  return s
end

local function saveKV(guid, k, v)
  -- k and v come only from the controlled vocabularies above
  CharDBExecute(string.format(
    "REPLACE INTO dml_autobuff_kv (guid, k, v) VALUES (%d, '%s', '%s')", guid, k, v))
end

local function getState(player)
  local guid = player:GetGUIDLow()
  return state[guid] or loadState(guid), guid
end

-- ---------------------------------------------------------------- buffing

local function applyAura(caster, target, spellId)
  if target:HasAura(spellId) then return end
  local ok = pcall(function() caster:AddAura(spellId, target) end)
  if not ok then
    pcall(function() caster:CastSpell(target, spellId, true) end)
  end
end

local function highestKnown(player, ids)
  for _, id in ipairs(ids) do
    if player:HasSpell(id) then return id end
  end
end

local function ownedSummons(player)
  local out = {}
  local ok, list = pcall(function() return player:GetCreaturesInRange(SUMMON_YDS) end)
  if not ok or type(list) ~= "table" then return out end
  local myGuid = player:GetGUID()
  for _, c in ipairs(list) do
    local okOwn, owned = pcall(function() return c:GetOwnerGUID() == myGuid end)
    if okOwn and owned and c:IsAlive() then out[#out + 1] = c end
  end
  return out
end

local function keepReagents(player)
  for _, item in ipairs(KEEPER_ITEMS) do
    local ok, n = pcall(function() return player:GetItemCount(item) end)
    if ok and n == 0 then
      pcall(function() player:AddItem(item, 1) end)
    end
  end
end

local function applyImbue(player, grpKey, slot)
  local s = getState(player)
  local choice = s.g[grpKey]
  if choice == "none" then return end
  local pick = GROUPS[grpKey].choices[choice]
  if not pick or not player:HasSpell(pick.spell) then return end
  pcall(function()
    local item = player:GetItemByPos(255, slot)
    if item and item:GetEnchantmentId(TEMP_ENCHANT_SLOT) ~= pick.ench then
      item:SetEnchantment(pick.ench, TEMP_ENCHANT_SLOT)
    end
  end)
end

local function buffPlayer(player)
  if player:IsDead() then return end
  local s = getState(player)

  if s.reagents then keepReagents(player) end

  local targets = { player }
  for _, c in ipairs(ownedSummons(player)) do targets[#targets + 1] = c end

  for _, line in ipairs(TOGGLES) do
    if s.t[line.key] then
      local id = highestKnown(player, line.ids)
      if id then
        if line.shared then
          for _, t in ipairs(targets) do applyAura(player, t, id) end
        else
          applyAura(player, player, id)
        end
      end
    end
  end

  for key, grp in pairs(GROUPS) do
    if not grp.enchant then
      local id = grp.choices[s.g[key]]
      if type(id) == "table" then id = highestKnown(player, id) end -- rank chain
      if id and player:HasSpell(id) then
        if grp.shared then
          for _, t in ipairs(targets) do applyAura(player, t, id) end
        else
          applyAura(player, player, id)
        end
      end
    end
  end

  applyImbue(player, "mh", EQ_MAINHAND)
  applyImbue(player, "oh", EQ_OFFHAND)
end

CreateLuaEvent(function()
  local ok, players = pcall(GetPlayersInWorld)
  if not ok or type(players) ~= "table" then return end
  for _, p in ipairs(players) do
    local st = state[p:GetGUIDLow()]
    if st and st.enabled then
      pcall(buffPlayer, p)
    end
  end
end, UPDATE_MS, 0)

-- ---------------------------------------------------------------- chat command

local function msgTo(player, text)
  player:SendBroadcastMessage("|cff33ff99[autobuff]|r " .. text)
end

local function sortedKeys(t)
  local out = {}
  for k in pairs(t) do out[#out + 1] = k end
  table.sort(out)
  return out
end

local function showStatus(player)
  local s = getState(player)
  msgTo(player, "auto-buff " .. (s.enabled and "|cff00ff00ON|r" or "|cffff0000OFF|r")
    .. ", keeper reagents " .. (s.reagents and "on" or "off")
    .. "  (#buffs on/off, #buffs reagents on/off)")
  local on, off = {}, {}
  for _, line in ipairs(TOGGLES) do
    if s.t[line.key] then on[#on + 1] = line.key else off[#off + 1] = line.key end
  end
  msgTo(player, "buffs ON: |cff00ff00" .. table.concat(on, " ") .. "|r")
  msgTo(player, "buffs OFF: |cff888888" .. table.concat(off, " ") .. "|r  (#buffs <name> on/off)")
  for _, key in ipairs(GROUP_ORDER) do
    local grp = GROUPS[key]
    msgTo(player, string.format("%s = |cffffff00%s|r  (#buffs %s <%s|none>)",
      grp.label, s.g[key], key, table.concat(sortedKeys(grp.choices), "|")))
  end
end

local function onChat(event, player, msg)
  local cmd = msg:match("^#buffs%s*(.*)$")
  if not cmd then return end
  cmd = cmd:lower()

  local s, guid = getState(player)

  if cmd == "" or cmd == "list" or cmd == "status" then
    showStatus(player)
  elseif cmd == "on" then
    s.enabled = true
    saveKV(guid, "enabled", "1")
    msgTo(player, "ON — buffing you and your summons every " .. (UPDATE_MS / 1000) .. "s.")
    pcall(buffPlayer, player)
  elseif cmd == "off" then
    s.enabled = false
    saveKV(guid, "enabled", "0")
    msgTo(player, "OFF.")
  elseif cmd == "reagents on" then
    s.reagents = true
    saveKV(guid, "reagents", "1")
    msgTo(player, "keeper reagent kit ON — one of each casting reagent stays in your bags.")
    pcall(keepReagents, player)
  elseif cmd == "reagents off" then
    s.reagents = false
    saveKV(guid, "reagents", "0")
    msgTo(player, "keeper reagent kit OFF (items left in bags are yours to delete).")
  else
    local word, arg = cmd:match("^(%S+)%s+(%S+)$")
    if not word then
      msgTo(player, "usage: #buffs | #buffs on/off | #buffs <buff> on/off | #buffs <group> <choice|none> | #buffs reagents on/off")
      return false
    end
    local isToggle = false
    for _, line in ipairs(TOGGLES) do
      if line.key == word then isToggle = true break end
    end
    if isToggle and (arg == "on" or arg == "off") then
      s.t[word] = arg == "on"
      saveKV(guid, "t_" .. word, arg == "on" and "1" or "0")
      msgTo(player, word .. " -> " .. arg)
      if s.enabled then pcall(buffPlayer, player) end
    elseif GROUPS[word] then
      if arg == "none" or GROUPS[word].choices[arg] then
        s.g[word] = arg
        saveKV(guid, "g_" .. word, arg)
        msgTo(player, GROUPS[word].label .. " -> " .. arg)
        if s.enabled then pcall(buffPlayer, player) end
      else
        msgTo(player, "options: " .. table.concat(sortedKeys(GROUPS[word].choices), ", ") .. ", none")
      end
    else
      msgTo(player, "unknown buff/group '" .. word .. "' — #buffs for the list")
    end
  end
  return false -- suppress the chat line
end

local PLAYER_EVENT_ON_CHAT   = 18
local PLAYER_EVENT_ON_LOGIN  = 3
local PLAYER_EVENT_ON_LOGOUT = 4

RegisterPlayerEvent(PLAYER_EVENT_ON_CHAT, onChat)
RegisterPlayerEvent(PLAYER_EVENT_ON_LOGIN, function(event, player)
  loadState(player:GetGUIDLow())
end)
RegisterPlayerEvent(PLAYER_EVENT_ON_LOGOUT, function(event, player)
  state[player:GetGUIDLow()] = nil
end)

print("[dml_autobuff] v3 loaded — opt in with #buffs on")
