-- ────────────────────────────────────────────────────────────────────────────────
-- ────────────────────────────────────────────────────────────────────────────────
-- BLACK MARKET AUCTION HOUSE 3.3.5 BACKPORT 
-- ────────────────────────────────────────────────────────────────────────────────
-- ────────────────────────────────────────────────────────────────────────────────

-- ─── Vendor NPC Configuration ───────────────────────────────────────────────
-- List the NPC IDs that will serve as Black Market Auction House vendors.
-- Interacting with any of these IDs will open the BMAH UI for players.
-- Add or remove IDs here as your server requires.

local BMAH_VENDOR_NPCs = {
  2069430, --Test Subject
  --#######, --Add More Here
}
-- ─────────────────────────────────────────────────────────────────────────────
-- ─── Fill-rarity configuration ───────────────────────────────────────────────────
-- Tweak these three values to adjust your loot rarity probabilities.
-- They represent the *cumulative* thresholds for a random roll r = math.random():
--
--   0.00 ≤ r < FillRateCommon   → pick from commonItems
--   FillRateCommon ≤ r < FillRateRare     → pick from rareItems
--   FillRateRare ≤ r ≤ FillRateUltra      → pick from ultraRareItems
--
-- Requirements:
--  1) 0.0  ≤ FillRateCommon
--  2) FillRateCommon ≤ FillRateRare
--  3) FillRateRare   ≤ FillRateUltra
--  4) FillRateUltra ≤ 1.0
--
-- Example distributions:
--   FillRateCommon = 0.70   → 70% common
--   FillRateRare   = 0.90   → 20% rare  (0.90 - 0.70)
--   FillRateUltra  = 1.00   → 10% ultra (1.00 - 0.90)
--
-- Implementation note:
--   local r = math.random()  -- returns a float 0 <= r < 1 (or ≤1 depending on build)
--   if r < FillRateCommon then
--       -- common
--   elseif r < FillRateRare then
--       -- rare
--   else
--       -- ultra
--   end
--
local FillRateCommon   = 0.85   -- e.g. 85% chance for commonItems
local FillRateRare     = 0.95   -- next 10% (95% - 85%) for rareItems
local FillRateUltra    = 1.00   -- final 5%  (100% - 95%) for ultraRareItems
-- ──────────────────────────────────────────────────────────────────────────────

-- ─── Fill-count configuration ─────────────────────────────────────────────────
-- These three thresholds (cumulative) map a random roll r = math.random() to
-- one of four possible row-counts. Adjust the numbers below to change how many
-- auctions get spawned most often.
--
--    0.00 ≤ r < FillCountThreshold1 → insert FillCount1 rows
--    FillCountThreshold1 ≤ r < FillCountThreshold2 → insert FillCount2 rows
--    FillCountThreshold2 ≤ r < FillCountThreshold3 → insert FillCount3 rows
--    FillCountThreshold3 ≤ r ≤ FillCountThreshold4 → insert FillCount4 rows
--
-- Requirements:
--  0.0 ≤ FillCountThreshold1
--  FillCountThreshold1 ≤ FillCountThreshold2
--  FillCountThreshold2 ≤ FillCountThreshold3
--  FillCountThreshold3 ≤ FillCountThreshold4 (must be 1.0)
--
local FillCountThreshold1 = 0.64   -- e.g. 64% chance to insert FillCount1 rows
local FillCountThreshold2 = 0.76   -- next 12% (76%-64%) → FillCount2
local FillCountThreshold3 = 0.88   -- next 12% (88%-76%) → FillCount3
local FillCountThreshold4 = 1.00   -- final 12% (100%-88%) → FillCount4

local FillCount1 = 3               -- rows if r < 0.64
local FillCount2 = 2               -- rows if r < 0.76
local FillCount3 = 4               -- rows if r < 0.88
local FillCount4 = 5               -- rows otherwise
-- ───────────────────────────────────────────────────────────────────────────────

-- ─── Bidding rules ────────────────────────────────────────────────────────────
local MinBidIncrementG   = 10       -- how many gold above last_bid is required
-- ──────────────────────────────────────────────────────────────────────────────
-- ─── General timing & chance ──────────────────────────────────────────────────
local AutoFillChance     = 0.50        -- chance to auto-fill when table is empty
local PotentialDurations = {720, 1440} -- possible “time left” values (in minutes)
-- ───────────────────────────────────────────────────────────────────────────────
-- ─── Refund‐mail configuration ─────────────────────────────────────────────────
local RefundMailSender     = 0         
local RefundStationery     = 41     
local RefundMailSubject    = "[BMAH] Outbid Refund"
local RefundMailBody       = "You were outbid on The Black Market Auction Hosue. Your bid of %dg has been returned."
-- ────────────────────────────────────────────────────────────────────────────────

-- ─── Flush‐notify configuration ────────────────────────────────────────────────
local FlushMailSender      = 0  
local FlushMailStationery  = 62 
local FlushMailSubject     = "[BMAH] You’ve won your auction!"
local FlushMailBody        = [[
Congratulations! You have successfully won an item off the Black Market Auction House.
After spending %dg, “%s” is now yours! Enjoy.

- The Black Market AH
]]
-- ───────────────────────────────────────────────────────────────────────────────

-- ─── Item Pricing Configuration ──────────────────────────────────────────────
-- Define the gold cost for each item category and rarity tier.
--   common_*_price   → cost for common items
--   rare_*_price     → cost for rare items
--   ultraRare_*_price → cost for ultra-rare items
-- Adjust these values to fit your server’s economy.

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
-- ─────────────────────────────────────────────────────────────────────────────

local commonItems = {
  { itemId = 8485,   seller = "Breanni",         cost = common_pets_price  },
  { itemId = 8490,   seller = "Breanni",         cost = common_pets_price  },
  { itemId = 8491,   seller = "Breanni",         cost = common_pets_price  },
  { itemId = 8492,   seller = "Breanni",         cost = common_pets_price  },
  { itemId = 20768,  seller = "Yuppl",           cost = common_pets_price  },
  { itemId = 20769,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 22799,  seller = "Zunji the Knife", cost = common_gear_price  },
  { itemId = 29960,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 34499,  seller = "Landro Longshot", cost = common_tcg_price   },
  { itemId = 34535,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 38309,  seller = "Landro Longshot", cost = common_tcg_price   },
  { itemId = 38310,  seller = "Landro Longshot", cost = common_tcg_price   },
  { itemId = 38313,  seller = "Landro Longshot", cost = common_tcg_price   },
  { itemId = 38578,  seller = "Landro Longshot", cost = common_tcg_price   },
  { itemId = 39883,  seller = "Yuppl",           cost = common_pets_price  },
  { itemId = 43698,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44178,  seller = "Mei Francis",     cost = common_mount_price },
  { itemId = 44707,  seller = "Mei Francis",     cost = common_mount_price },
  { itemId = 44721,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44751,  seller = "Yuppl",           cost = common_pets_price  },
  { itemId = 44965,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44970,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44971,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44973,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44974,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44980,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 44982,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 45002,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 45606,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 46780,  seller = "Landro Longshot", cost = common_tcg_price   },
  { itemId = 48112,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 48114,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 48116,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 48118,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 48124,  seller = "Breanni",         cost = common_pets_price  },
  { itemId = 48126,  seller = "Breanni",         cost = common_pets_price  },
}

local rareItems = {
  { itemId = 8494,   seller = "Breanni",             cost = rare_pets_price     },
  { itemId = 8498,   seller = "Breanni",             cost = rare_pets_price     },
  { itemId = 8499,   seller = "Breanni",             cost = rare_pets_price     },
  { itemId = 10822,  seller = "Breanni",             cost = rare_pets_price     },
  { itemId = 13335,  seller = "Mei Francis",         cost = rare_mount_price    },
  { itemId = 14617,  seller = "Yuppl",               cost = rare_misc_price     },
  { itemId = 23705,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 23709,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 23713,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 23720,  seller = "Mei Francis",         cost = rare_mount_price    },
  { itemId = 29271,  seller = "Zunji the Knife",     cost = rare_gear_price     },
  { itemId = 30380,  seller = "Caladis Brightspear", cost = battered_hilt_price },
  { itemId = 32542,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 32566,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 32588,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 33219,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 33223,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 34492,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 35504,  seller = "Breanni",             cost = rare_pets_price     },
  { itemId = 35513,  seller = "Mei Francis",         cost = rare_mount_price    },
  { itemId = 38050,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 38311,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 39769,  seller = "Bergrisst",           cost = rare_instrument_price },
  { itemId = 43952,  seller = "Mei Francis",         cost = rare_mount_price    },
  { itemId = 43953,  seller = "Mei Francis",         cost = rare_mount_price    },
  { itemId = 44151,  seller = "Mei Francis",         cost = rare_mount_price    },
  { itemId = 44924,  seller = "Bergrisst",           cost = rare_instrument_price },
  { itemId = 45037,  seller = "Yuppl",               cost = rare_misc_price     },
  { itemId = 45063,  seller = "Landro Longshot",     cost = rare_tcg_price      },
  { itemId = 50379,  seller = "Caladis Brightspear", cost = battered_hilt_price },
}

local ultraRareItems = {
  { itemId = 19872,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 19902,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 30480,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 32458,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 34493,  seller = "Landro Longshot",   cost = ultraRare_tcg_price   },
  { itemId = 35227,  seller = "Landro Longshot",   cost = ultraRare_tcg_price   },
  { itemId = 38312,  seller = "Landro Longshot",   cost = ultraRare_tcg_price   },
  { itemId = 38314,  seller = "Landro Longshot",   cost = ultraRare_tcg_price   },
  { itemId = 40491,  seller = "Zunji the Knife",   cost = ultraRare_tcg_price   },
  { itemId = 44083,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 44175,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 45693,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 45802,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 49286,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 49287,  seller = "Breanni",           cost = ultraRare_pets_price  },
  { itemId = 49343,  seller = "Breanni",           cost = ultraRare_pets_price  },
  { itemId = 49636,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 50046,  seller = "Zunji the Knife",   cost = ultraRare_gear_price  },
  { itemId = 50047,  seller = "Zunji the Knife",   cost = ultraRare_gear_price  },
  { itemId = 50048,  seller = "Zunji the Knife",   cost = ultraRare_gear_price  },
  { itemId = 50049,  seller = "Zunji the Knife",   cost = ultraRare_gear_price  },
  { itemId = 50050,  seller = "Zunji the Knife",   cost = ultraRare_gear_price  },
  { itemId = 50051,  seller = "Zunji the Knife",   cost = ultraRare_gear_price  },
  { itemId = 50052,  seller = "Zunji the Knife",   cost = ultraRare_gear_price  },
  { itemId = 50818,  seller = "Mei Francis",       cost = ultraRare_mount_price },
  { itemId = 54068,  seller = "Mei Francis",       cost = ultraRare_mount_price },
}
--Set the Table
CharDBExecute([[
CREATE TABLE IF NOT EXISTS `blackmarketauctionhouse` (
  `id` INT UNSIGNED NOT NULL AUTO_INCREMENT,
  `item_id` INT UNSIGNED NOT NULL DEFAULT 0,
  `item_owner` VARCHAR(32) NOT NULL DEFAULT '',
  `time` INT NOT NULL DEFAULT 0,
  `last_bid` INT UNSIGNED NOT NULL DEFAULT 0,
  `start_bid` INT UNSIGNED NOT NULL DEFAULT 0,
  `buyer_id` INT UNSIGNED NOT NULL DEFAULT 0,
  `total_bids` INT NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
]])



local function OnBMAHVendorGossip(event, player, creature)
    -- prefix “BMAHUI” / message “OPEN” is arbitrary but must match client
    player:SendAddonMessage("BMAHUI", "OPEN", 0, player)
    player:GossipComplete()    -- close the gossip window
end

for _, entry in ipairs(BMAH_VENDOR_NPCs) do
    RegisterCreatureGossipEvent(entry, 1, OnBMAHVendorGossip)
end

local REQ  = "BMAH_REQ"
local DATA = "BMAH_DATA"
local DONE = "BMAH_DONE"
local COPPER_PER_SILVER = 100
local SILVER_PER_GOLD   = 100
math.randomseed(os.time())

local SUBCLASS = {
    ["0_0"]="Consumable",["0_1"]="Potion",["0_2"]="Elixir",["0_3"]="Flask",["0_4"]="Scroll",["0_5"]="Food & Drink",["0_6"]="Item Enhancement",["0_7"]="Bandage",["0_8"]="Other",
    ["1_0"]="Bag",["1_1"]="Soul Bag",["1_2"]="Herb Bag",["1_3"]="Enchanting Bag",["1_4"]="Engineering Bag",["1_5"]="Gem Bag",["1_6"]="Mining Bag",["1_7"]="Leatherworking Bag",["1_8"]="Inscription Bag",
    ["2_0"]="One-Handed Axe",["2_1"]="Two-Handed Axe",["2_2"]="Bow",["2_3"]="Gun",["2_4"]="One-Handed Mace",["2_5"]="Two-Handed Mace",["2_6"]="Polearm",["2_7"]="One-Handed Sword",["2_8"]=" Two-Handed Sword",["2_9"]="Obsolete",["2_10"]="Staff",["2_11"]="Exotic",["2_12"]="Exotic",["2_13"]="Fist Weapon",["2_14"]="Miscellaneous",["2_15"]="Dagger",["2_16"]="Thrown",["2_17"]="Spear",["2_18"]="Crossbow",["2_19"]="Wand",["2_20"]="Fishing Pole",
    ["3_0"]="Red",["3_1"]="Blue",["3_2"]="Yellow",["3_3"]="Purple",["3_4"]="Green",["3_5"]="Orange",["3_6"]="Meta",["3_7"]="Simple",["3_8"]="Prismatic",
    ["4_0"]="Miscellaneous",["4_1"]="Cloth",["4_2"]="Leather",["4_3"]="Mail",["4_4"]="Plate",["4_5"]="Buckler",["4_6"]="Shield",["4_7"]="Libram",["4_8"]="Idol",["4_9"]="Totem",["4_10"]="Sigil",
    ["5_0"]="Reagent",
    ["6_0"]="Wand",["6_1"]="Bolt",["6_2"]="Arrow",["6_3"]="Bullet",["6_4"]="Thrown",
    ["7_0"]="Trade Goods",["7_1"]="Parts",["7_2"]="Explosives",["7_3"]="Devices",["7_4"]="Jewelcrafting",["7_5"]="Cloth",["7_6"]="Leather",["7_7"]="Metal & Stone",["7_8"]="Meat",["7_9"]="Herb",["7_10"]="Elemental",["7_11"]="Other",["7_12"]="Enchanting",["7_13"]="Materials",["7_14"]="Armor Enchantment",["7_15"]="Weapon Enchantment",
    ["8_0"]="Generic",
    ["9_0"]="Book",["9_1"]="Leatherworking",["9_2"]="Tailoring",["9_3"]="Engineering",["9_4"]="Blacksmithing",["9_5"]="Cooking",["9_6"]="Alchemy",["9_7"]="First Aid",["9_8"]="Enchanting",["9_9"]="Fishing",["9_10"]="Jewelcrafting",
    ["10_0"]="Money",
    ["11_0"]="Quiver",["11_1"]="Quiver",["11_2"]="Quiver",["11_3"]="Ammo Pouch",
    ["12_0"]="Quest",
    ["13_0"]="Key",["13_1"]="Lockpick",
    ["14_0"]="Permanent",
    ["15_0"]="Junk",["15_1"]="Reagent",["15_2"]="Pet",["15_3"]="Holiday",["15_4"]="Other",["15_5"]="Mount",
    ["16_1"]="Warrior",["16_2"]="Paladin",["16_3"]="Hunter",["16_4"]="Rogue",["16_5"]="Priest",["16_6"]="Death Knight",["16_7"]="Shaman",["16_8"]="Mage",["16_9"]="Warlock",["16_11"]="Druid",
}

-- helper: bucket minutes into a word
local function ClassifyTime(mins)
    if mins < 30 then
        return "Short"
    elseif mins < 120 then
        return "Medium"
    elseif mins < 720 then
        return "Long"
    else
        return "Very Long"
    end
end

RegisterPlayerEvent(19, function(_, player, msg, _, _, receiver)
    if msg ~= REQ then
        return
    end
    local target = receiver or player

    -- ◼ 0) find row with the most total_bids
    local maxQ = CharDBQuery([[
        SELECT id
        FROM blackmarketauctionhouse
        ORDER BY total_bids DESC
        LIMIT 1
    ]])
    local maxRowId = maxQ and maxQ:GetUInt32(0) or 0

    -- 1) Fetch all blackmarket rows from CharDB
    local rowsQ = CharDBQuery([[
        SELECT id, item_id, time, item_owner, last_bid
        FROM blackmarketauctionhouse
        ORDER BY id ASC
    ]])
    if not rowsQ then
        player:SendAddonMessage(DONE, "0", 0, target)
        return
    end

    local sent = 0
    repeat
        -- pull from CharDB
        local itemId   = rowsQ:GetUInt32(1)
        local minsLeft = rowsQ:GetUInt32(2)
        local owner    = rowsQ:GetString(3)
        local lastBid  = rowsQ:GetUInt32(4)

        -- lookup name, level, class, subclass in WorldDB
        local tplQ = WorldDBQuery(string.format([[
            SELECT name, RequiredLevel, class, subclass
            FROM item_template
            WHERE entry = %d
        ]], itemId))

        local itemName, reqLevel, classId, subClassId
        if tplQ then
            itemName   = tplQ:GetString(0)
            reqLevel   = tplQ:GetUInt32(1)
            classId    = tplQ:GetUInt32(2)
            subClassId = tplQ:GetUInt32(3)
        else
            itemName   = "Item#" .. itemId
            reqLevel   = 0
            classId    = 0
            subClassId = 0
        end

        -- map class_subclass → text via your SUBCLASS table
        local key      = classId .."_".. subClassId
        local itemType = SUBCLASS[key] or ""

        -- bucket minutes into Short/Medium/Long/Very Long
        local timeWord = ClassifyTime(minsLeft)

        local tpl = GetItemTemplate(itemId)
        local iconName = tpl and tpl:GetIcon() or "INV_Misc_QuestionMark"

        -- build exactly 9 fields: name;level;type;time;owner;bid;icon;maxRowId;itemId
        local payload = string.format(
            "%s;%d;%s;%s;%s;%d;%s;%d;%d",
            itemName:gsub(";", ""),
            reqLevel,
            itemType,
            timeWord,
            owner:gsub(";", ""),
            lastBid,
            iconName,
            maxRowId,
            itemId
        )

        player:SendAddonMessage(DATA, payload, 0, target)
        sent = sent + 1
    until not rowsQ:NextRow()

    player:SendAddonMessage(DONE, tostring(sent), 0, target)
end)

RegisterPlayerEvent(19, function(_, player, msg, _, _, _)
    if msg:lower() ~= "bmah_flush" then
        return
    end

    if not player:IsGM() then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r You do not have permission to flush the BlackMarketAH table.")
        return false
    end

    -- 1) query every sold row
    local q = CharDBQuery([[
        SELECT id, item_id, buyer_id, last_bid
        FROM blackmarketauctionhouse
        WHERE buyer_id <> 0
    ]])

    if q then
        repeat
            local rowId       = q:GetUInt32(0)
            local itemEntry   = q:GetUInt32(1)
            local receiver    = q:GetUInt32(2)
            local bidCopper   = q:GetUInt32(3)
            -- fetch item name from world DB
            local wq = WorldDBQuery(string.format(
                "SELECT name FROM item_template WHERE entry = %d", itemEntry
            ))
            local itemName = wq and wq:GetString(0) or ("Item#"..itemEntry)
            -- compute gold spent
            local bidGold = math.floor(bidCopper / (COPPER_PER_SILVER * SILVER_PER_GOLD))

            -- format the body
            local body = string.format(FlushMailBody, bidGold, itemName)

            -- send the mail: no money, no COD, attach exactly one of the item
            SendMail(
                FlushMailSubject,
                body,
                receiver,
                FlushMailSender,
                FlushMailStationery,
                0,        -- immediate delivery
                0,        -- money attached
                0,        -- COD
                itemEntry,
                1         -- quantity
            )
        until not q:NextRow()
    end

    -- 2) now wipe the auction table
    CharDBExecute([[TRUNCATE TABLE blackmarketauctionhouse]])

    -- 3) feedback
    player:SendBroadcastMessage("|cff69ccf0[BMAH]|r BlackMarketAH has been flushed. All won items have been mailed out!")
    return false
end)

RegisterPlayerEvent(19, function(_, player, msg, _, _, _)
    if msg:lower() ~= "bmah_fill" then
        return
    end

    if not player:IsGM() then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r You do not have permission to refill the BlackMarketAH table.")
        return false
    end

    -- ── do not fill if any auctions still exist ───────────
    local countQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse")
    local count  = countQ and countQ:GetUInt32(0) or 0
    if count > 0 then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r BlackMarketAH already has active auctions. Please flush first.")
        return false
    end

    -- now safe to truncate & refill
    CharDBExecute([[TRUNCATE TABLE blackmarketauctionhouse]])

    -- roll how many rows to insert
    local r = math.random()
    local count
    if r < FillCountThreshold1 then
        count = FillCount1
    elseif r < FillCountThreshold2 then
        count = FillCount2
    elseif r < FillCountThreshold3 then
        count = FillCount3
    else
        count = FillCount4
    end

    -- helper to pick a random entry from a table
    local function pick(t)
        return t[ math.random(1, #t) ]
    end

    -- now loop and insert
    for i = 1, count do
        -- roll rarity
        local r = math.random()
        local entry
        if r < FillRateCommon then
            entry = pick(commonItems)
        elseif r < FillRateRare then
            entry = pick(rareItems)
        else
            entry = pick(ultraRareItems)
        end

        -- sanitize owner name
        local owner = entry.seller:gsub("'", "''")
        -- cost is multiplied by 10000
        local bid = entry.cost * 10000
        -- timeLeft (in minutes) — adjust as you like
        local durations = PotentialDurations
        local timeLeft  = durations[ math.random(#durations) ]

        -- insert into DB
        CharDBExecute(string.format([[
            INSERT INTO blackmarketauctionhouse
              (item_id, time, item_owner, start_bid, last_bid)
            VALUES
              (%d, %d, '%s', %d, %d)
        ]],
            entry.itemId,
            timeLeft,
            owner,
            bid,
            bid
        ))
    end

    player:SendBroadcastMessage(
        string.format(
            "|cff69ccf0[BMAH]|r Filled BlackMarketAH with %d rows.",
            count
        )
    )
    return false
end)

-- client will whisper: "BMAH_BID;<itemId>;<goldAmount>"
local BID_REQ    = "BMAH_BID"
-- same for hot-item if you want a different command
local HOTBID_REQ = "BMAH_HOTBID"

RegisterPlayerEvent(19, function(_, player, msg, _, _, _)
    -- only handle our bid commands
    local cmd, payload = msg:match("^([A-Z_]+);(.+)$")
    if cmd ~= BID_REQ and cmd ~= HOTBID_REQ then
        return
    end

    -- parse params
    local idStr, bidG = payload:match("^(%d+);(%d+)$")
    local id     = tonumber(idStr)
    local bidAmt = tonumber(bidG)
    if not id or not bidAmt then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r Invalid bid format.")
        return false
    end

    -- look up the auction row (now also fetch buyer_id)
    local q = CharDBQuery(string.format(
        "SELECT id, last_bid, buyer_id FROM blackmarketauctionhouse WHERE %s = %d",
        (cmd == HOTBID_REQ) and "id" or "item_id",
        id
    ))
    if not q then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r Auction entry not found.")
        return false
    end

    local rowId          = q:GetUInt32(0)
    local lastBid        = q:GetUInt32(1)
    local currentBidder  = q:GetUInt32(2)
    -- convert to copper
    local bidCopper      = bidAmt * COPPER_PER_SILVER * SILVER_PER_GOLD
    local minRequired    = lastBid + (MinBidIncrementG * COPPER_PER_SILVER * SILVER_PER_GOLD)
    local playerCopper   = player:GetCoinage()

    -- 0) If they’re already the highest bidder, bail out
    if currentBidder == player:GetGUIDLow() then
        player:SendBroadcastMessage(
            "|cffff0000[BMAH]|r You already hold the highest bid on that auction."
        )
        return false
    end

    if playerCopper < bidCopper then
        player:SendBroadcastMessage("|cffff0000[BMAH]|r You lack the funds for that bid.")
    elseif bidCopper < minRequired then
        local requiredG = minRequired / (COPPER_PER_SILVER * SILVER_PER_GOLD)
        player:SendBroadcastMessage(
            ("|cffff0000[BMAH]|r Your bid must be at least %dg."):format(requiredG)
        )
    else
        -- deduct
        player:ModifyMoney(-bidCopper)
        -- refund setup (as before)
        local refundCopper = lastBid
        local refundGold   = math.floor(refundCopper / (COPPER_PER_SILVER * SILVER_PER_GOLD))
        if currentBidder ~= 0 then
            -- escape strings
            local subj = RefundMailSubject:gsub("'", "''")
            local body = string.format(RefundMailBody, refundGold):gsub("'", "''")

            SendMail(
                RefundMailSubject,
                body,
                currentBidder,
                RefundMailSender,
                RefundStationery,
                0,               -- no delay
                refundCopper     -- refund amount in copper
            )
        end
        -- update DB
        CharDBExecute(string.format([[
            UPDATE blackmarketauctionhouse
               SET last_bid   = %d,
                   buyer_id   = %d,
                   total_bids = total_bids + 1
             WHERE id = %d
        ]], bidCopper, player:GetGUIDLow(), rowId))
        player:SendBroadcastMessage(
            ("|cff69ccf0[BMAH]|r Your bid of %dg has been accepted!"):format(bidAmt)
        )
    end

    return false    -- swallow the whisper so it doesn’t spam the client
end)

-- 1) Define two helper functions using your existing code

local function BMAH_FlushLogic()
    print("[BMAH][Timer] → flushing expired auctions…")
    -- 1) grab every expired auction
    local q = CharDBQuery([[
        SELECT id, item_id, buyer_id, last_bid
        FROM blackmarketauctionhouse
        WHERE time <= 0
    ]])
    if q then
        local expiredIds = {}
        repeat
            local rowId     = q:GetUInt32(0)
            local itemEntry = q:GetUInt32(1)
            local buyerId   = q:GetUInt32(2)
            local bidCopper = q:GetUInt32(3)

            -- mail only if someone actually bid
            if buyerId ~= 0 then
                local wq = WorldDBQuery(string.format(
                    "SELECT name FROM item_template WHERE entry = %d",
                    itemEntry
                ))
                local itemName = wq and wq:GetString(0) or ("Item#"..itemEntry)
                local bidGold  = math.floor(bidCopper / (COPPER_PER_SILVER * SILVER_PER_GOLD))
                local body     = string.format(FlushMailBody, bidGold, itemName)

                SendMail(
                    FlushMailSubject,
                    body,
                    buyerId,
                    FlushMailSender,
                    FlushMailStationery,
                    0, 0, 0,
                    itemEntry,
                    1
                )
            end

            table.insert(expiredIds, rowId)
        until not q:NextRow()

        -- 2) delete only those expired rows
        CharDBExecute(( "DELETE FROM blackmarketauctionhouse WHERE id IN (%s)" )
            :format(table.concat(expiredIds, ",")))
    end

    print("[BMAH][Timer] → flush complete.")
end

local function BMAH_FillLogic()
    print("[BMAH][Timer] → auto‐filling market…")
    -- copy exactly the body of your fill handler, minus the GM‐check and player:SendBroadcastMessage
    CharDBExecute("TRUNCATE TABLE blackmarketauctionhouse")

    local r = math.random()
    local count
    if r < FillCountThreshold1 then
        count = FillCount1
    elseif r < FillCountThreshold2 then
        count = FillCount2
    elseif r < FillCountThreshold3 then
        count = FillCount3
    else
        count = FillCount4
    end

    local function pick(t) return t[ math.random(1, #t) ] end
    for i = 1, count do
        local r = math.random()
        local entry
        if r < 0.85 then entry = pick(commonItems)
        elseif r < 0.95 then entry = pick(rareItems)
        else entry = pick(ultraRareItems) end

        local owner    = entry.seller:gsub("'", "''")
        local bid      = entry.cost * 10000
        local durations = {720, 1440}
        local timeLeft = durations[ math.random(#durations) ]

        CharDBExecute(string.format([[
            INSERT INTO blackmarketauctionhouse
              (item_id, time, item_owner, start_bid, last_bid)
            VALUES (%d, %d, '%s', %d, %d)
        ]],
            entry.itemId,
            timeLeft,
            owner,
            bid,
            bid
        ))
    end

    print(string.format("[BMAH][Setter] has filled %d rows.", count))
end

-- track seconds since last 5-minute tick
local tick_position = 0

-- Every 5 minutes: age, flush expired, maybe fill
CreateLuaEvent(function()
    -- 1) count how many auctions we have right now
    local totalQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse")
    local total  = totalQ and totalQ:GetUInt32(0) or 0

    if total == 0 then
        print("[BMAH][Cleaner] table empty at tick now skipping time decrement & flush")
    else
        -- 2) decrement time on all rows
        print("[BMAH][Timer] tick down 5 minutes on all auctions")
        CharDBExecute("UPDATE blackmarketauctionhouse SET time = time - 5")

        -- 3) check for expired only if we had rows
        local expQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse WHERE time <= 0")
        if expQ and expQ:GetUInt32(0) > 0 then
            print("[BMAH][Cleaner] found expired auctions → calling flush")
            BMAH_FlushLogic()
        end
    end

    -- 4) after potential flush, see if the table is now empty
    local remQ = CharDBQuery("SELECT COUNT(*) FROM blackmarketauctionhouse")
    local rem  = remQ and remQ:GetUInt32(0) or 0
    if rem == 0 then
        if math.random() < AutoFillChance then
            print("[BMAH][Setter] no rows left now calling fill (50%)")
            BMAH_FillLogic()
        else
            print("[BMAH][Setter] no rows left now skipping fill (50%)")
        end
    end
end, 300000, 0)
