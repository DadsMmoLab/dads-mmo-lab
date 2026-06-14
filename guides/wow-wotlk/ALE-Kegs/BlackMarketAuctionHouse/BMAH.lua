-- BMAH.lua  —  ALE-compatible rewrite
-- ALE (AzerothCore Lua Engine) version
-- Forked from: https://github.com/Youpeoples/Black-Market-Auction-House
-- Updated for ALE by: Dad's MMO Lab
--
-- ADMIN GUIDE:
--   1. Run sql/BMAH_Up.sql against acore_world to create the vendor NPC
--   2. Copy this file to your lua_scripts/ directory
--   3. Reload: .reload ale  (or restart worldserver)
--   4. The DB table is created automatically on first load (acore_characters)
--   5. Reload NPC templates: .reload creature_template  (in-game GM command)
--   6. Spawn the NPC: .npc add 2069430  (or add your own entry to BMAH_VENDOR_NPCs)
--   7. Client addon: copy Client Files/AddOns/BlackMarketUI → <WoW>/Interface/AddOns/
--
-- USING A CUSTOM NPC:
--   Add its entry ID to BMAH_VENDOR_NPCs below.
--   The creature_template row must have npcflag with bit 0 set (gossip = 1):
--     UPDATE creature_template SET npcflag = npcflag | 1 WHERE entry = <your_entry>;
--
-- GM COMMANDS (whisper the NPC or self):
--   bmah_fill   — manually fill the auction table (only when empty)
--   bmah_flush  — award won items via mail and wipe the table
--   bmah_diag   — print system state to GM chat (use when NPC/gossip won't work)
-- ---------------------------------------------------------------------------

-- ── Double-load guard ────────────────────────────────────────────────────────
if _G.BMAHLoaded then return end
_G.BMAHLoaded = true
-- Reset guard on full Lua state restart so re-require works cleanly
local ALE_EVENT_ON_LUA_STATE_CLOSE = 16
RegisterServerEvent(ALE_EVENT_ON_LUA_STATE_CLOSE, function()
    _G.BMAHLoaded = nil
end)

-- ── Load-time NPC template check ─────────────────────────────────────────────
-- Prints to worldserver console on every ALE (re)load.
-- If the NPC template is missing or misconfigured, the error is shown here.
do
    local q = WorldDBQuery("SELECT faction, npcflag FROM creature_template WHERE entry = 2069430")
    if q then
        local faction = q:GetUInt32(0)
        local npcflag = q:GetUInt32(1)
        print(string.format("[BMAH] NPC 2069430 loaded — faction=%d npcflag=0x%X", faction, npcflag))
        if (npcflag & 1) == 0 then
            print("[BMAH] WARNING: gossip npcflag bit missing — NPC won't respond to right-click!")
        end
        if faction ~= 35 then
            print("[BMAH] WARNING: faction=" .. faction .. " (expected 35/Friendly) — NPC may not be interactable!")
        end
    else
        print("[BMAH] ERROR: entry 2069430 NOT FOUND in creature_template!")
        print("[BMAH] Run sql/BMAH_Up.sql against acore_world, then RESTART the worldserver.")
        print("[BMAH] After restart: .npc add 2069430  to spawn the broker.")
    end
end

-- ── Config ───────────────────────────────────────────────────────────────────
-- NPC entry IDs that open the BMAH UI when interacted with
local BMAH_VENDOR_NPCs = {
    2069430, -- Test Subject
    -- add more entry IDs here as needed
}

-- Fill-rarity thresholds (cumulative roll 0–1):
--   0.00 ≤ r < FillRateCommon              → common
--   FillRateCommon ≤ r < FillRateRare      → rare
--   FillRateRare   ≤ r ≤ 1.0              → ultraRare
local FillRateCommon = 0.85
local FillRateRare   = 0.95
-- FillRateUltra = 1.00 (implicit)

-- Row-count thresholds (cumulative roll 0–1):
local FillCountThreshold1 = 0.64
local FillCountThreshold2 = 0.76
local FillCountThreshold3 = 0.88
local FillCount1 = 3
local FillCount2 = 2
local FillCount3 = 4
local FillCount4 = 5

-- Bidding
local MinBidIncrementG = 10        -- minimum gold above last bid

-- Timing
local AutoFillChance     = 0.50    -- chance to auto-fill when table empties
local PotentialDurations = {720, 1440} -- auction duration options (minutes)

-- Mail — refund (outbid)
local RefundMailSender  = 0
local RefundStationery  = 41
local RefundMailSubject = "[BMAH] Outbid Refund"
local RefundMailBody    = "You were outbid on The Black Market Auction House. Your bid of %dg has been returned."

-- Mail — flush (auction won)
local FlushMailSender     = 0
local FlushMailStationery = 62
local FlushMailSubject    = "[BMAH] You've won your auction!"
local FlushMailBody       = [[
Congratulations! You have successfully won an item off the Black Market Auction House.
After spending %dg, "%s" is now yours! Enjoy.

– The Black Market AH
]]

-- ── Pricing ───────────────────────────────────────────────────────────────────
local common_pets_price     = 100
local rare_pets_price       = 400
local ultraRare_pets_price  = 1000
local common_mount_price    = 5000
local rare_mount_price      = 10000
local ultraRare_mount_price = 20000
local common_tcg_price      = 1000
local rare_tcg_price        = 2000
local ultraRare_tcg_price   = 5000
local common_misc_price     = 500
local rare_misc_price       = 600
local battered_hilt_price   = 10000
local common_gear_price     = 500
local rare_gear_price       = 1800
local ultraRare_gear_price  = 5000
local rare_instrument_price = 25000

-- ── Item Pools ────────────────────────────────────────────────────────────────
local commonItems = {
    { itemId = 8485,  seller = "Breanni",         cost = common_pets_price  },
    { itemId = 8490,  seller = "Breanni",         cost = common_pets_price  },
    { itemId = 8491,  seller = "Breanni",         cost = common_pets_price  },
    { itemId = 8492,  seller = "Breanni",         cost = common_pets_price  },
    { itemId = 20768, seller = "Yuppl",           cost = common_pets_price  },
    { itemId = 20769, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 22799, seller = "Zunji the Knife", cost = common_gear_price  },
    { itemId = 29960, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 34499, seller = "Landro Longshot", cost = common_tcg_price   },
    { itemId = 34535, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 38309, seller = "Landro Longshot", cost = common_tcg_price   },
    { itemId = 38310, seller = "Landro Longshot", cost = common_tcg_price   },
    { itemId = 38313, seller = "Landro Longshot", cost = common_tcg_price   },
    { itemId = 38578, seller = "Landro Longshot", cost = common_tcg_price   },
    { itemId = 39883, seller = "Yuppl",           cost = common_pets_price  },
    { itemId = 43698, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44178, seller = "Mei Francis",     cost = common_mount_price },
    { itemId = 44707, seller = "Mei Francis",     cost = common_mount_price },
    { itemId = 44721, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44751, seller = "Yuppl",           cost = common_pets_price  },
    { itemId = 44965, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44970, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44971, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44973, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44974, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44980, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 44982, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 45002, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 45606, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 46780, seller = "Landro Longshot", cost = common_tcg_price   },
    { itemId = 48112, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 48114, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 48116, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 48118, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 48124, seller = "Breanni",         cost = common_pets_price  },
    { itemId = 48126, seller = "Breanni",         cost = common_pets_price  },
}

local rareItems = {
    { itemId = 8494,  seller = "Breanni",             cost = rare_pets_price       },
    { itemId = 8498,  seller = "Breanni",             cost = rare_pets_price       },
    { itemId = 8499,  seller = "Breanni",             cost = rare_pets_price       },
    { itemId = 10822, seller = "Breanni",             cost = rare_pets_price       },
    { itemId = 13335, seller = "Mei Francis",         cost = rare_mount_price      },
    { itemId = 14617, seller = "Yuppl",               cost = rare_misc_price       },
    { itemId = 23705, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 23709, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 23713, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 23720, seller = "Mei Francis",         cost = rare_mount_price      },
    { itemId = 29271, seller = "Zunji the Knife",     cost = rare_gear_price       },
    { itemId = 30380, seller = "Caladis Brightspear", cost = battered_hilt_price   },
    { itemId = 32542, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 32566, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 32588, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 33219, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 33223, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 34492, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 35504, seller = "Breanni",             cost = rare_pets_price       },
    { itemId = 35513, seller = "Mei Francis",         cost = rare_mount_price      },
    { itemId = 38050, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 38311, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 39769, seller = "Bergrisst",           cost = rare_instrument_price },
    { itemId = 43952, seller = "Mei Francis",         cost = rare_mount_price      },
    { itemId = 43953, seller = "Mei Francis",         cost = rare_mount_price      },
    { itemId = 44151, seller = "Mei Francis",         cost = rare_mount_price      },
    { itemId = 44924, seller = "Bergrisst",           cost = rare_instrument_price },
    { itemId = 45037, seller = "Yuppl",               cost = rare_misc_price       },
    { itemId = 45063, seller = "Landro Longshot",     cost = rare_tcg_price        },
    { itemId = 50379, seller = "Caladis Brightspear", cost = battered_hilt_price   },
}

local ultraRareItems = {
    { itemId = 19872, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 19902, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 30480, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 32458, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 34493, seller = "Landro Longshot", cost = ultraRare_tcg_price   },
    { itemId = 35227, seller = "Landro Longshot", cost = ultraRare_tcg_price   },
    { itemId = 38312, seller = "Landro Longshot", cost = ultraRare_tcg_price   },
    { itemId = 38314, seller = "Landro Longshot", cost = ultraRare_tcg_price   },
    { itemId = 40491, seller = "Zunji the Knife", cost = ultraRare_tcg_price   },
    { itemId = 44083, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 44175, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 45693, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 45802, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 49286, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 49287, seller = "Breanni",         cost = ultraRare_pets_price  },
    { itemId = 49343, seller = "Breanni",         cost = ultraRare_pets_price  },
    { itemId = 49636, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 50046, seller = "Zunji the Knife", cost = ultraRare_gear_price  },
    { itemId = 50047, seller = "Zunji the Knife", cost = ultraRare_gear_price  },
    { itemId = 50048, seller = "Zunji the Knife", cost = ultraRare_gear_price  },
    { itemId = 50049, seller = "Zunji the Knife", cost = ultraRare_gear_price  },
    { itemId = 50050, seller = "Zunji the Knife", cost = ultraRare_gear_price  },
    { itemId = 50051, seller = "Zunji the Knife", cost = ultraRare_gear_price  },
    { itemId = 50052, seller = "Zunji the Knife", cost = ultraRare_gear_price  },
    { itemId = 50818, seller = "Mei Francis",     cost = ultraRare_mount_price },
    { itemId = 54068, seller = "Mei Francis",     cost = ultraRare_mount_price },
}

-- ── Database setup ────────────────────────────────────────────────────────────
-- Table is created in acore_characters on first load; safe to run every time.
CharDBExecute([[
CREATE TABLE IF NOT EXISTS `blackmarketauctionhouse` (
  `id`         INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `item_id`    INT UNSIGNED NOT NULL DEFAULT 0,
  `item_owner` VARCHAR(32)  NOT NULL DEFAULT '',
  `time`       INT          NOT NULL DEFAULT 0,
  `last_bid`   INT UNSIGNED NOT NULL DEFAULT 0,
  `start_bid`  INT UNSIGNED NOT NULL DEFAULT 0,
  `buyer_id`   INT UNSIGNED NOT NULL DEFAULT 0,
  `total_bids` INT          NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
]])

-- ── Constants ─────────────────────────────────────────────────────────────────
local COPPER_PER_SILVER = 100
local SILVER_PER_GOLD   = 100
local REQ     = "BMAH_REQ"
local DATA    = "BMAH_DATA"
local DONE    = "BMAH_DONE"
local BID_REQ    = "BMAH_BID"
local HOTBID_REQ = "BMAH_HOTBID"
math.randomseed(os.time())

-- ── Helpers ───────────────────────────────────────────────────────────────────
local SUBCLASS = {
    ["0_0"]="Consumable", ["0_1"]="Potion",        ["0_2"]="Elixir",
    ["0_3"]="Flask",      ["0_4"]="Scroll",         ["0_5"]="Food & Drink",
    ["0_6"]="Item Enhancement", ["0_7"]="Bandage",  ["0_8"]="Other",
    ["1_0"]="Bag",        ["1_1"]="Soul Bag",       ["1_2"]="Herb Bag",
    ["1_3"]="Enchanting Bag", ["1_4"]="Engineering Bag", ["1_5"]="Gem Bag",
    ["1_6"]="Mining Bag", ["1_7"]="Leatherworking Bag", ["1_8"]="Inscription Bag",
    ["2_0"]="One-Handed Axe",  ["2_1"]="Two-Handed Axe", ["2_2"]="Bow",
    ["2_3"]="Gun",        ["2_4"]="One-Handed Mace",["2_5"]="Two-Handed Mace",
    ["2_6"]="Polearm",    ["2_7"]="One-Handed Sword",["2_8"]="Two-Handed Sword",
    ["2_10"]="Staff",     ["2_13"]="Fist Weapon",   ["2_14"]="Miscellaneous",
    ["2_15"]="Dagger",    ["2_16"]="Thrown",        ["2_18"]="Crossbow",
    ["2_19"]="Wand",      ["2_20"]="Fishing Pole",
    ["3_0"]="Red",        ["3_1"]="Blue",           ["3_2"]="Yellow",
    ["3_3"]="Purple",     ["3_4"]="Green",          ["3_5"]="Orange",
    ["3_6"]="Meta",       ["3_7"]="Simple",         ["3_8"]="Prismatic",
    ["4_0"]="Miscellaneous", ["4_1"]="Cloth",       ["4_2"]="Leather",
    ["4_3"]="Mail",       ["4_4"]="Plate",          ["4_5"]="Buckler",
    ["4_6"]="Shield",     ["4_7"]="Libram",         ["4_8"]="Idol",
    ["4_9"]="Totem",      ["4_10"]="Sigil",
    ["5_0"]="Reagent",
    ["6_0"]="Wand",       ["6_1"]="Bolt",           ["6_2"]="Arrow",
    ["6_3"]="Bullet",     ["6_4"]="Thrown",
    ["7_0"]="Trade Goods",["7_1"]="Parts",          ["7_2"]="Explosives",
    ["7_3"]="Devices",    ["7_4"]="Jewelcrafting",  ["7_5"]="Cloth",
    ["7_6"]="Leather",    ["7_7"]="Metal & Stone",  ["7_8"]="Meat",
    ["7_9"]="Herb",       ["7_10"]="Elemental",     ["7_11"]="Other",
    ["7_12"]="Enchanting",["7_13"]="Materials",
    ["9_0"]="Book",       ["9_1"]="Leatherworking", ["9_2"]="Tailoring",
    ["9_3"]="Engineering",["9_4"]="Blacksmithing",  ["9_5"]="Cooking",
    ["9_6"]="Alchemy",    ["9_7"]="First Aid",      ["9_8"]="Enchanting",
    ["9_9"]="Fishing",    ["9_10"]="Jewelcrafting",
    ["12_0"]="Quest",     ["13_0"]="Key",           ["13_1"]="Lockpick",
    ["14_0"]="Permanent",
    ["15_0"]="Junk",      ["15_1"]="Reagent",       ["15_2"]="Pet",
    ["15_3"]="Holiday",   ["15_4"]="Other",         ["15_5"]="Mount",
    ["16_1"]="Warrior",   ["16_2"]="Paladin",       ["16_3"]="Hunter",
    ["16_4"]="Rogue",     ["16_5"]="Priest",        ["16_6"]="Death Knight",
    ["16_7"]="Shaman",    ["16_8"]="Mage",          ["16_9"]="Warlock",
    ["16_11"]="Druid",
}

local function ClassifyTime(mins)
    if mins < 30  then return "Short"
    elseif mins < 120 then return "Medium"
    elseif mins < 720 then return "Long"
    else return "Very Long" end
end

local function pick(t) return t[math.random(1, #t)] end

local function rollCount()
    local r = math.random()
    if r < FillCountThreshold1 then return FillCount1
    elseif r < FillCountThreshold2 then return FillCount2
    elseif r < FillCountThreshold3 then return FillCount3
    else return FillCount4 end
end

local function rollItem()
    local r = math.random()
    if r < FillRateCommon then return pick(commonItems)
    elseif r < FillRateRare then return pick(rareItems)
    else return pick(ultraRareItems) end
end

-- ── Gossip: open BMAH UI ──────────────────────────────────────────────────────
-- ALE: RegisterCreatureGossipEvent with GOSSIP_EVENT_ON_HELLO=1, ON_SELECT=2.
-- GossipSendMenu takes 2 args for creature senders (npcTextId, creature).
-- SendAddonMessage channel 255 = CHAT_MSG_ADDON (uint8 0xFF).
-- The npc_text ID (2069430) is created by sql/BMAH_Up.sql.
local GOSSIP_EVENT_ON_HELLO  = 1
local GOSSIP_EVENT_ON_SELECT = 2
local function OnBMAHGossipHello(event, player, creature)
    player:GossipClearMenu()
    player:GossipMenuAddItem(4, "Browse the Black Market", 0, 1)
    player:GossipSendMenu(2069430, creature)
end
local function OnBMAHGossipSelect(event, player, creature, sender, intid, code, menu_id)
    if intid == 1 then
        player:SendAddonMessage("BMAHUI", "OPEN", 255, player)
        player:GossipComplete()
    end
end
for _, entry in ipairs(BMAH_VENDOR_NPCs) do
    RegisterCreatureGossipEvent(entry, GOSSIP_EVENT_ON_HELLO,  OnBMAHGossipHello)
    RegisterCreatureGossipEvent(entry, GOSSIP_EVENT_ON_SELECT, OnBMAHGossipSelect)
end

-- ── Listing request (client whispers BMAH_REQ) ────────────────────────────────
RegisterPlayerEvent(19, function(_, player, msg, _, _, receiver)
    if msg ~= REQ then return end
    local target = receiver or player
    -- find the auction with the most bids (used as "hot item" indicator)
    local maxQ   = CharDBQuery("SELECT id FROM blackmarketauctionhouse ORDER BY total_bids DESC LIMIT 1")
    local hotId  = maxQ and maxQ:GetUInt32(0) or 0
    local rowsQ  = CharDBQuery("SELECT id, item_id, time, item_owner, last_bid FROM blackmarketauctionhouse ORDER BY id ASC")
    if not rowsQ then
        player:SendAddonMessage(DONE, "0", 255, target)
        return
    end
    local sent = 0
    repeat
        local rowId    = rowsQ:GetUInt32(0)
        local itemId   = rowsQ:GetUInt32(1)
        local minsLeft = rowsQ:GetUInt32(2)
        local owner    = rowsQ:GetString(3)
        local lastBid  = rowsQ:GetUInt32(4)
        local tplQ = WorldDBQuery(string.format(
            "SELECT name, RequiredLevel, class, subclass FROM item_template WHERE entry = %d", itemId))
        local itemName, reqLevel, classId, subClassId
        if tplQ then
            itemName   = tplQ:GetString(0)
            reqLevel   = tplQ:GetUInt32(1)
            classId    = tplQ:GetUInt32(2)
            subClassId = tplQ:GetUInt32(3)
        else
            itemName, reqLevel, classId, subClassId = "Item#"..itemId, 0, 0, 0
        end
        local itemType = SUBCLASS[classId.."_"..subClassId] or ""
        local tpl      = GetItemTemplate(itemId)
        local iconName = tpl and tpl:GetIcon() or "INV_Misc_QuestionMark"
        -- payload: name;level;type;time;owner;bid;icon;hotRowId;rowId
        local payload = string.format("%s;%d;%s;%s;%s;%d;%s;%d;%d",
            itemName:gsub(";", ""), reqLevel, itemType,
            ClassifyTime(minsLeft), owner:gsub(";", ""),
            lastBid, iconName, hotId, rowId)
        player:SendAddonMessage(DATA, payload, 255, target)
        sent = sent + 1
    until not rowsQ:NextRow()
    player:SendAddonMessage(DONE, tostring(sent), 255, target)
end)

-- ── Flush command (GM whispers bmah_flush) ────────────────────────────────────
RegisterPlayerEvent(19, function(_, player, msg, _, _, _)
    if msg:lower() ~= "bmah_flush" then return end
    if not player:IsGM() then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r You do not have permission to flush the BMAH table.")
        return false
    end
    local q = CharDBQuery("SELECT id, item_id, buyer_id, last_bid FROM blackmarketauctionhouse WHERE buyer_id <> 0")
    if q then
        repeat
            local itemEntry = q:GetUInt32(1)
            local receiver  = q:GetUInt32(2)
            local bidCopper = q:GetUInt32(3)
            local wq = WorldDBQuery(string.format("SELECT name FROM item_template WHERE entry = %d", itemEntry))
            local itemName = wq and wq:GetString(0) or ("Item#"..itemEntry)
            local bidGold  = math.floor(bidCopper / (COPPER_PER_SILVER * SILVER_PER_GOLD))
            SendMail(FlushMailSubject, string.format(FlushMailBody, bidGold, itemName),
                receiver, FlushMailSender, FlushMailStationery, 0, 0, 0, itemEntry, 1)
        until not q:NextRow()
    end
    CharDBExecute("TRUNCATE TABLE blackmarketauctionhouse")
    player:SendBroadcastMessage("|cff69ccf0[BMAH]|r BMAH has been flushed. Won items mailed out!")
    return false
end)

-- ── Fill command (GM whispers bmah_fill) ─────────────────────────────────────
RegisterPlayerEvent(19, function(_, player, msg, _, _, _)
    if msg:lower() ~= "bmah_fill" then return end
    if not player:IsGM() then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r You do not have permission to fill the BMAH table.")
        return false
    end
    local countQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse")
    if countQ and countQ:GetUInt32(0) > 0 then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r Active auctions exist. Please flush first.")
        return false
    end
    CharDBExecute("TRUNCATE TABLE blackmarketauctionhouse")
    local count = rollCount()
    for _ = 1, count do
        local entry    = rollItem()
        local owner    = entry.seller:gsub("'", "''")
        local bid      = entry.cost * 10000
        local timeLeft = PotentialDurations[math.random(#PotentialDurations)]
        CharDBExecute(string.format(
            "INSERT INTO blackmarketauctionhouse (item_id, time, item_owner, start_bid, last_bid) VALUES (%d, %d, '%s', %d, %d)",
            entry.itemId, timeLeft, owner, bid, bid))
    end
    player:SendBroadcastMessage(string.format("|cff69ccf0[BMAH]|r Filled BMAH with %d auctions.", count))
    return false
end)

-- ── Bid handler (client whispers BMAH_BID;<rowId>;<gold> or BMAH_HOTBID;...) ─
RegisterPlayerEvent(19, function(_, player, msg, _, _, _)
    local cmd, payload = msg:match("^([A-Z_]+);(.+)$")
    if cmd ~= BID_REQ and cmd ~= HOTBID_REQ then return end
    local idStr, bidG = payload:match("^(%d+);(%d+)$")
    local id     = tonumber(idStr)
    local bidAmt = tonumber(bidG)
    if not id or not bidAmt then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r Invalid bid format.")
        return false
    end
    -- always resolve by row id (avoids ambiguity when the same item appears twice)
    local q = CharDBQuery(string.format(
        "SELECT id, last_bid, buyer_id FROM blackmarketauctionhouse WHERE id = %d", id))
    if not q then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r Auction entry not found.")
        return false
    end
    local rowId         = q:GetUInt32(0)
    local lastBid       = q:GetUInt32(1)
    local currentBidder = q:GetUInt32(2)
    local bidCopper     = bidAmt * COPPER_PER_SILVER * SILVER_PER_GOLD
    local minRequired   = lastBid + (MinBidIncrementG * COPPER_PER_SILVER * SILVER_PER_GOLD)
    if currentBidder == player:GetGUIDLow() then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r You already hold the highest bid.")
        return false
    end
    if player:GetCoinage() < bidCopper then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r You lack the funds for that bid.")
    elseif bidCopper < minRequired then
        player:SendBroadcastMessage(string.format("|cffff0000[BMAH]|r Your bid must be at least %dg.",
            minRequired / (COPPER_PER_SILVER * SILVER_PER_GOLD)))
    else
        player:ModifyMoney(-bidCopper)
        if currentBidder ~= 0 then
            local refundGold = math.floor(lastBid / (COPPER_PER_SILVER * SILVER_PER_GOLD))
            SendMail(RefundMailSubject, string.format(RefundMailBody, refundGold):gsub("'","''"),
                currentBidder, RefundMailSender, RefundStationery, 0, lastBid)
        end
        CharDBExecute(string.format(
            "UPDATE blackmarketauctionhouse SET last_bid = %d, buyer_id = %d, total_bids = total_bids + 1 WHERE id = %d",
            bidCopper, player:GetGUIDLow(), rowId))
        player:SendBroadcastMessage(string.format("|cff69ccf0[BMAH]|r Your bid of %dg has been accepted!", bidAmt))
    end
    return false
end)

-- ── Internal flush logic (used by timer) ──────────────────────────────────────
local function BMAH_FlushLogic()
    print("[BMAH][Timer] flushing expired auctions...")
    local q = CharDBQuery("SELECT id, item_id, buyer_id, last_bid FROM blackmarketauctionhouse WHERE time <= 0")
    if q then
        local expiredIds = {}
        repeat
            local rowId     = q:GetUInt32(0)
            local itemEntry = q:GetUInt32(1)
            local buyerId   = q:GetUInt32(2)
            local bidCopper = q:GetUInt32(3)
            if buyerId ~= 0 then
                local wq = WorldDBQuery(string.format("SELECT name FROM item_template WHERE entry = %d", itemEntry))
                local itemName = wq and wq:GetString(0) or ("Item#"..itemEntry)
                local bidGold  = math.floor(bidCopper / (COPPER_PER_SILVER * SILVER_PER_GOLD))
                SendMail(FlushMailSubject, string.format(FlushMailBody, bidGold, itemName),
                    buyerId, FlushMailSender, FlushMailStationery, 0, 0, 0, itemEntry, 1)
            end
            table.insert(expiredIds, rowId)
        until not q:NextRow()
        CharDBExecute("DELETE FROM blackmarketauctionhouse WHERE id IN ("..table.concat(expiredIds, ",")..")")
    end
    print("[BMAH][Timer] flush complete.")
end

-- ── Internal fill logic (used by timer) ───────────────────────────────────────
local function BMAH_FillLogic()
    print("[BMAH][Timer] auto-filling market...")
    CharDBExecute("TRUNCATE TABLE blackmarketauctionhouse")
    local count = rollCount()
    for _ = 1, count do
        local entry    = rollItem()
        local owner    = entry.seller:gsub("'", "''")
        local bid      = entry.cost * 10000
        local timeLeft = PotentialDurations[math.random(#PotentialDurations)]
        CharDBExecute(string.format(
            "INSERT INTO blackmarketauctionhouse (item_id, time, item_owner, start_bid, last_bid) VALUES (%d, %d, '%s', %d, %d)",
            entry.itemId, timeLeft, owner, bid, bid))
    end
    print(string.format("[BMAH][Timer] filled %d auctions.", count))
end

-- ── 5-minute tick: age → flush → maybe fill ───────────────────────────────────
CreateLuaEvent(function()
    local totalQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse")
    local total  = totalQ and totalQ:GetUInt32(0) or 0
    if total == 0 then
        print("[BMAH][Timer] table empty, skipping decrement")
    else
        print("[BMAH][Timer] decrementing 5 minutes on all auctions")
        CharDBExecute("UPDATE blackmarketauctionhouse SET time = time - 5")
        local expQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse WHERE time <= 0")
        if expQ and expQ:GetUInt32(0) > 0 then BMAH_FlushLogic() end
    end
    local remQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse")
    local rem  = remQ and remQ:GetUInt32(0) or 0
    if rem == 0 then
        if math.random() < AutoFillChance then
            print("[BMAH][Timer] no auctions left, auto-filling (50% chance hit)")
            BMAH_FillLogic()
        else
            print("[BMAH][Timer] no auctions left, skipping auto-fill (50% chance miss)")
        end
    end
end, 300000, 0)

-- ── GM diagnostic command: whisper 'bmah_diag' ───────────────────────────────
-- Reports full system state to the GM in chat. Use when gossip/spawn isn't working.
RegisterPlayerEvent(19, function(_, player, msg, _, _, _)
    if msg:lower() ~= "bmah_diag" then return end
    if not player:IsGM() then return false end
    local function report(color, text)
        player:SendBroadcastMessage("|cff" .. color .. "[BMAH Diag]|r " .. text)
    end
    -- DB table
    local tblQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse")
    if tblQ then
        report("69ccf0", "Characters DB table: OK (" .. tblQ:GetUInt32(0) .. " auction rows)")
    else
        report("ff0000", "Characters DB table: MISSING — re-run BMAH.lua (table auto-creates on load)")
    end
    -- NPC template
    local npcQ = WorldDBQuery("SELECT faction, npcflag FROM creature_template WHERE entry = 2069430")
    if npcQ then
        local faction = npcQ:GetUInt32(0)
        local npcflag = npcQ:GetUInt32(1)
        report("69ccf0", string.format("NPC 2069430: faction=%d npcflag=0x%X", faction, npcflag))
        if (npcflag & 1) == 0 then
            report("ff8000", "WARN: gossip bit not set — run BMAH_Up.sql, then RESTART worldserver")
        end
        if faction ~= 35 then
            report("ff8000", "WARN: faction=" .. faction .. " (need 35/Friendly) — run BMAH_Up.sql, then RESTART")
        end
        if npcflag == 1 and faction == 35 then
            report("69ccf0", "NPC template OK — if .npc add fails, the worldserver needs a RESTART to load templates")
        end
    else
        report("ff0000", "NPC 2069430 NOT IN DB — run sql/BMAH_Up.sql against acore_world")
        report("ff0000", "Then RESTART the worldserver — .reload creature_template alone is not reliable")
        report("ffffff", "After restart: .npc add 2069430")
    end
    -- Registered NPC entries
    local entries = {}
    for _, e in ipairs(BMAH_VENDOR_NPCs) do entries[#entries+1] = tostring(e) end
    report("69ccf0", "Lua-registered entries: " .. table.concat(entries, ", "))
    return false
end)
