-- BlackMarketUI.lua (client)
local bmahUIInitTime
SLASH_BMAH1 = "/bmah"
SLASH_BMAH2 = "/blackmarket"
SlashCmdList["BMAH"] = function(msg)
  msg = msg:lower():trim()
  local me = UnitName("player")

  if msg == "flush" then
    SendChatMessage("bmah_flush", "WHISPER", nil, me)
  elseif msg == "fill" then
    SendChatMessage("bmah_fill",  "WHISPER", nil, me)
  else
    -- show usage
    DEFAULT_CHAT_FRAME:AddMessage("|cff69ccf0[BMAH]|r Usage: /bmah [fill|flush] (GM ONLY)")
  end
end
StaticPopupDialogs["BMAH_CONFIRM_BID"] = {
  text         = "Are you sure you want to bid %dg on |cff00ff00%s|r?",
  button1      = ACCEPT,
  button2      = CANCEL,
  hideOnEscape = 1,
  timeout      = 0,
  whileDead    = 1,
  exclusive    = 1,

  OnShow = function(self)
    -- put it above everything
    self:SetFrameStrata("DIALOG")
    -- move it up 100px
    self:ClearAllPoints()
    self:SetPoint("CENTER", UIParent, "CENTER", 0, 340)
  end,

  OnAccept = function(self, data)
    SendChatMessage(
      ("BMAH_BID;%d;%d"):format(data.itemId, data.amount),
      "WHISPER", nil, UnitName("player")
    )
    MoneyInputFrame_SetCopper(BlackMarketBidPrice, 0)
  end,
}
-- DEBUG: print every record we got from the server, including the raw payload
local function BMAH_PrintDebug(tbl)
  DEFAULT_CHAT_FRAME:AddMessage("|cff69ccf0[BMAH DEBUG]|r got "..#tbl.." rows:")
  for i, rec in ipairs(tbl) do
    -- 1) show the raw string
    DEFAULT_CHAT_FRAME:AddMessage(
      string.format("  RAW #%d → %s", i, rec._raw or "<missing>")
    )
    -- 2) show the parsed fields
    DEFAULT_CHAT_FRAME:AddMessage(
      string.format(
        "     → Name=%s, Level=%s, Type=%s, TimeLeft=%s, Seller=%s, Bid=%s, Icon=%s, HotRow=%d, WowEntry=%s, RowId=%s",
        tostring(rec.itemName),
        tostring(rec.reqLevel),
        tostring(rec.itemType),
        tostring(rec.timeLeft),
        tostring(rec.owner),
        tostring(rec.last_bid),
        tostring(rec.icon or "<none>"),
        rec.maxRowId or 0,
        tostring(rec.wowEntry),
        tostring(rec.itemId)
      )
    )
  end
end

local selectedIndex
local selectedButton
local expectingNewBatch = true

local ADDON_PREFIX    = "BMAH"
local UI_OPEN_PREFIX  = "BMAHUI"
local REQ             = "BMAH_REQ"
local DATA            = "BMAH_DATA"
local DONE            = "BMAH_DONE"

local rows = {}
local serverMaxRowId

local f = CreateFrame("Frame")
f:RegisterEvent("CHAT_MSG_SYSTEM")
f:RegisterEvent("PLAYER_LOGIN")
f:RegisterEvent("CHAT_MSG_ADDON")

local f = CreateFrame("Frame")
f:RegisterEvent("CHAT_MSG_SYSTEM")
f:RegisterEvent("PLAYER_LOGIN")
f:RegisterEvent("CHAT_MSG_ADDON")
f:SetScript("OnEvent", function(self, event, ...)
  if event == "CHAT_MSG_SYSTEM" then
    local msg = ...
    if msg:match("%[BMAH%]|r Your bid of %d+g has been accepted") then
      local scroll = BlackMarketFrameScrollFrame
      for _, btn in ipairs(scroll.buttons) do btn:Hide() end
      scroll.scrollBar:SetValue(0)
      wipe(rows)
      BlackMarketUI_UpdateList(rows)
      SendChatMessage(REQ, "WHISPER", nil, UnitName("player"))
    end

  elseif event == "PLAYER_LOGIN" then
    -- if you still want an auto‐fetch on login:
    -- SendChatMessage(REQ, "WHISPER", nil, UnitName("player"))

  elseif event == "CHAT_MSG_ADDON" then
    local prefix, msg, _, sender = ...

    -- only handle whispers *to* yourself
    if sender ~= UnitName("player") then return end

    -- 1) “OPEN” from your server-side NPC
    if prefix == UI_OPEN_PREFIX and msg == "OPEN" then
      BlackMarketFrame:Show()
      return
    end

    -- 2) our data channel?
    if prefix == ADDON_PREFIX then
      local cmd, payload = msg:match("^([A-Z_]+);?(.*)$")
      if cmd == "DATA" then
        -- parse a row (10 fields: name;level;type;time;owner;bid;icon;hotIdx;wowEntry;rowId)
        local itemName, reqLevel, itemType, timeLeft, owner, last_bid, iconName, maxRowIdStr, wowEntryStr, itemIdStr
          = strsplit(";", payload)
        serverMaxRowId = tonumber(maxRowIdStr)
        table.insert(rows, {
          itemName = itemName,
          reqLevel = tonumber(reqLevel),
          itemType = itemType,
          timeLeft = timeLeft,
          owner    = owner,
          last_bid = tonumber(last_bid),
          icon     = iconName,
          wowEntry = tonumber(wowEntryStr),
          itemId   = tonumber(itemIdStr),
          _raw     = payload,
        })

      elseif cmd == "DONE" then
        -- all rows are in → redraw
        BlackMarketUI_UpdateList(rows)

        -- update the “hot” item on the right-hand panel
        local hotRow = rows[serverMaxRowId]
        if hotRow then
          local iconPath = hotRow.icon ~= "" and "Interface\\Icons\\"..hotRow.icon
                           or "Interface\\Icons\\INV_Misc_QuestionMark"
          BlackMarketFrameHotItemIcon.texture:SetTexture(iconPath)
          BlackMarketFrameHotItemName:SetText(hotRow.itemName)
          BlackMarketFrameHotItemSubclass:SetText(hotRow.itemType)
          BlackMarketFrameSellerLine:SetText("Seller:\n"..hotRow.owner)
          BlackMarketFrameTimeLeftLine:SetText("Time Left: "..hotRow.timeLeft)
        end
      end
    end
  end

  -- CHAT_MSG_ADDON → args: prefix, message, channel, sender
  local prefix, message, _, sender = ...

  -- ignore everyone but yourself
  if sender ~= UnitName("player") then return end

  if prefix == DATA then
    -- 10 fields: name;level;type;time;owner;bid;icon;hotIdx;wowEntry;rowId
    local itemName,
      reqLevel,
      itemType,
      timeLeft,
      owner,
      last_bid,
      iconName,
      maxRowIdStr,
      wowEntryStr,
      itemIdStr = strsplit(";", message)
    local maxRowId = tonumber(maxRowIdStr) or 0
    serverMaxRowId = maxRowId
    table.insert(rows, {
      itemName = itemName,
      reqLevel = tonumber(reqLevel),
      itemType = itemType,
      timeLeft = timeLeft,
      owner    = owner,
      last_bid = tonumber(last_bid),
      icon     = iconName,
      maxRowId = maxRowId,
      wowEntry = tonumber(wowEntryStr),
      itemId   = tonumber(itemIdStr),
      _raw     = message,
    })

  elseif prefix == DONE then
    -- all rows arrived—rebuild your scroll frame
    BlackMarketUI_UpdateList(rows)
    hotItemIndex = serverMaxRowId
    local hotRow = rows[serverMaxRowId]

    if hotRow then
      -- Icon
      local iconPath = hotRow.icon and hotRow.icon ~= "" and "Interface\\Icons\\" .. hotRow.icon or "Interface\\Icons\\INV_Misc_QuestionMark"
      if BlackMarketFrameHotItemIcon and BlackMarketFrameHotItemIcon.texture then
        BlackMarketFrameHotItemIcon.texture:SetTexture(iconPath)
      end

      -- Item name
      if BlackMarketFrameHotItemName then
        BlackMarketFrameHotItemName:SetText(hotRow.itemName or "Unknown Item")
      end

      -- Subclass
      if BlackMarketFrameHotItemSubclass then
        BlackMarketFrameHotItemSubclass:SetText((hotRow.itemType or "Unknown Type"))
      end

      -- Seller
      if BlackMarketFrameSellerLine then
        BlackMarketFrameSellerLine:SetText("Seller:\n" .. (hotRow.owner or "unknown"))
      end

      -- Time left
      if BlackMarketFrameTimeLeftLine then
        BlackMarketFrameTimeLeftLine:SetText("Time Left: " .. (hotRow.timeLeft or "very long"))
      end
    end

    if event == "PLAYER_LOGIN" then
      wipe(rows)
      SendChatMessage(REQ, "WHISPER", nil, UnitName("player"))
      return
    end
  end
end)

-- this gets called once you receive BMAH_DONE
local ROW_HEIGHT = 37

function BlackMarketUI_UpdateList(data)
  local scroll = _G["BlackMarketFrameScrollFrame"]
  local offset = HybridScrollFrame_GetOffset(scroll)
  local buttons = scroll.buttons

  for i = 1, #buttons do
    buttons[i]:SetID(i)
    local btn    = buttons[i]
    local record = data[i + offset]

    if record then
      if btn.Icon then
        local iconPath = (record.icon and record.icon ~= "")
                         and "Interface\\Icons\\"..record.icon
                         or "Interface\\Icons\\INV_Misc_QuestionMark"
        btn.Icon:SetTexture(iconPath)
        btn.Icon:SetTexCoord(0, 1, 0, 1)
      end

      -- Name field
      if btn.Name then
        btn.Name:SetText(record.itemName)
      end

      -- Level
      if btn.Level then
        btn.Level:SetText(tostring(record.reqLevel))
      end

      -- Type / subType
      if btn.Type then
        btn.Type:SetText(record.itemType)
      end

      -- Seller & bid
      if btn.Seller then
        btn.Seller:SetText(record.owner)
      end
      if btn.YourBid then
        local gold = math.floor(record.last_bid / (COPPER_PER_SILVER * SILVER_PER_GOLD))
        btn.YourBid:SetText(gold .. " |TInterface\\MoneyFrame\\UI-GoldIcon:0:0:2:0|t")
      end
      if btn.TimeLeft then
        btn.TimeLeft:SetText(record.timeLeft)
      end
      btn:Show()

    else
      btn:Hide()
    end
  end

  local totalHeight = #data * ROW_HEIGHT
  HybridScrollFrame_Update(scroll, totalHeight, scroll:GetHeight())

  if #data == 0 then
    BlackMarketFrame.noItemsText:Show()
  else
    BlackMarketFrame.noItemsText:Hide()
  end
end

-- In your PLAYER_LOGIN / ADDON_LOADED handler:

-- 1) Define the filter
local function BMAHWhisperFilter(self, event, msg, author, ...)
  -- Only care about our BMAH_ messages
  if not msg:match("^BMAH_") then
    return false
  end

  if event == "CHAT_MSG_WHISPER_INFORM" then
    -- Outgoing whisper (the REQ)
    -- msg == "BMAH_REQ"
    -- return true to hide the raw "/w" line from the player
    return true

  elseif event == "CHAT_MSG_WHISPER" then
    -- Incoming whisper from server: parse tag & payload
    local tag, payload = msg:match("^(BMAH_%u+);?(.*)$")
    if tag == "BMAH_DATA" then
      -- payload is "id;itemEntry;suffix;min;max;closed;premium"
      -- e.g. split and handle it:
      local id, entry, suffix, minC, maxC, closed, premium = strsplit(";", payload)
      -- TODO: add this row to your UI’s list

    elseif tag == "BMAH_DONE" then
      -- all rows received
      DEFAULT_CHAT_FRAME:AddMessage("  → BMAH transmission complete.")
      -- TODO: refresh or show the UI now
    end
    -- hide the raw whisper from the main chat
    return true
  end

  return false
end

-- 2) Register it on both incoming & outgoing whispers
ChatFrame_AddMessageEventFilter("CHAT_MSG_WHISPER",        BMAHWhisperFilter)
ChatFrame_AddMessageEventFilter("CHAT_MSG_WHISPER_INFORM", BMAHWhisperFilter)

local function MakeStretchyHeader(parent, text, width, xOffset)
  local hdr = CreateFrame("Frame", nil, parent)
  hdr:SetSize(width, 24)
  hdr:SetPoint("TOPLEFT", parent, "TOPLEFT", xOffset, -10)

  -- left cap
  hdr.left = hdr:CreateTexture(nil, "ARTWORK")
  hdr.left:SetTexture("Interface\\FrameGeneral\\UI-Panel-Header")
  hdr.left:SetTexCoord(0, .125, 0, 1)      -- adjust based on your texture
  hdr.left:SetSize(16, 24)
  hdr.left:SetPoint("LEFT", hdr, "LEFT", 0, 0)

  -- right cap
  hdr.right = hdr:CreateTexture(nil, "ARTWORK")
  hdr.right:SetTexture("Interface\\FrameGeneral\\UI-Panel-Header")
  hdr.right:SetTexCoord(.875, 1, 0, 1)
  hdr.right:SetSize(16, 24)
  hdr.right:SetPoint("RIGHT", hdr, "RIGHT", 0, 0)

  -- middle, tiled
  hdr.mid = hdr:CreateTexture(nil, "ARTWORK")
  hdr.mid:SetTexture("Interface\\FrameGeneral\\UI-Panel-Header")
  hdr.mid:SetTexCoord(.125, .875, 0, 1)
  hdr.mid:SetHorizTile(true)
  -- anchor it to fill between the caps
  hdr.mid:SetPoint("LEFT", hdr.left, "RIGHT", 0, 0)
  hdr.mid:SetPoint("RIGHT", hdr.right, "LEFT", 0, 0)
  hdr.mid:SetHeight(24)

  -- label
  hdr.label = hdr:CreateFontString(nil, "OVERLAY", "GameFontHighlightSmall")
  hdr.label:SetPoint("CENTER", hdr, "CENTER", 0, 0)
  hdr.label:SetText(text)

  return hdr
end

-- ── 1) YOUR ADDON_LOADED: just build columns, close-button, scroll frame & scrollbar ──
local init = CreateFrame("Frame")
init:RegisterEvent("ADDON_LOADED")
init:SetScript("OnEvent", function(self, event, addon)
  if addon ~= "BlackMarketUI" then return end
  self:UnregisterEvent("ADDON_LOADED")

  -- grab the XML-defined scroll frame (with backdrop & border)
  local scroll = BlackMarketFrameScrollFrame

  -- tell it to hook up its scrollChild and scrollBar (defined in XML)
  HybridScrollFrame_OnLoad(scroll)

  -- create the pool of buttons using your XML template
  local ROW_HEIGHT    = 37
  local VISIBLE_ROWS  = math.floor(scroll:GetHeight() / ROW_HEIGHT)
  HybridScrollFrame_CreateButtons(scroll,
                                  "BlackMarketItemTemplate",
                                  ROW_HEIGHT,
                                  VISIBLE_ROWS)

  -- ensure each row button truly catches and shows clicks
  for i, btn in ipairs(scroll.buttons) do
    btn:EnableMouse(true)                       -- allow mouse events
    btn:RegisterForClicks("AnyUp")              -- actually receive OnClick
    btn:SetFrameStrata("DIALOG")                -- same as scroll backdrop
    -- +3 to be safely above both scrollFrame & scrollChild backdrops
    btn:SetFrameLevel(scroll:GetFrameLevel() + 3)
  end

  local f = _G["BlackMarketFrame"]
  if not f then
    DEFAULT_CHAT_FRAME:AddMessage("BlackMarketFrame not found on ADDON_LOADED.")
    return
  end

  local scroll = _G["BlackMarketFrameScrollFrame"]

  -- 1) Item name column
  local colName = CreateFrame("Frame", f:GetName().."ColumnName", f, "BlackMarketColumnButtonTemplate")
  colName:SetSize(214, 27)
  colName:SetPoint("TOPLEFT", f, "TOPLEFT", 29, -55)
  colName.Name:ClearAllPoints()
  colName.Name:SetPoint("LEFT", colName.Left, "RIGHT", 1, 3)
  colName.Name:SetJustifyH("LEFT")
  colName.Name:SetText(NAME)

  -- 2) Level column
  local colLevel = CreateFrame("Frame", f:GetName().."ColumnLevel", f, "BlackMarketColumnButtonTemplate")
  colLevel:SetSize(30, 27)
  colLevel:SetPoint("LEFT", colName, "RIGHT", 0, 0)
  colLevel.Name:ClearAllPoints()
  colLevel.Name:SetPoint("CENTER", colLevel, 1, 3)
  colLevel.Name:SetJustifyH("CENTER")
  colLevel.Name:SetText("Lvl")

  -- 3) Type column
  local colType = CreateFrame("Frame", f:GetName().."ColumnType", f, "BlackMarketColumnButtonTemplate")
  colType:SetSize(91, 27)
  colType:SetPoint("LEFT", colLevel, "RIGHT", 0, 0)
  colType.Name:ClearAllPoints()
  colType.Name:SetPoint("CENTER", colType, 1, 3)
  colType.Name:SetJustifyH("CENTER")
  colType.Name:SetText(TYPE)

  -- 4) Time-left column
  local colDuration = CreateFrame("Frame", f:GetName().."ColumnDuration", f, "BlackMarketColumnButtonTemplate")
  colDuration:SetSize(91, 27)
  colDuration:SetPoint("LEFT", colType, "RIGHT", 0, 0)
  colDuration.Name:ClearAllPoints()
  colDuration.Name:SetPoint("CENTER", colDuration, 1, 3)
  colDuration.Name:SetJustifyH("CENTER")
  colDuration.Name:SetText(CLOSES_IN)

  -- 5) Seller column
  local colSeller = CreateFrame("Frame", f:GetName().."ColumnHighBidder", f, "BlackMarketColumnButtonTemplate")
  colSeller:SetSize(76, 27)
  colSeller:SetPoint("LEFT", colDuration, "RIGHT", 0, 0)
  colSeller.Name:ClearAllPoints()
  colSeller.Name:SetPoint("CENTER", colSeller, 1, 3)
  colSeller.Name:SetJustifyH("CENTER")
  colSeller.Name:SetText(AUCTION_CREATOR)

  -- 6) Current bid column
  local colBid = CreateFrame("Frame", f:GetName().."ColumnCurrentBid", f, "BlackMarketColumnButtonTemplate")
  colBid:SetSize(81, 27)
  -- anchor to the scroll-frame’s right so it never overlaps the bar:
  colBid:SetPoint("LEFT", colSeller, "RIGHT", 0, 0)
  colBid.Name:ClearAllPoints()
  colBid.Name:SetPoint("CENTER", colBid, 1, 3)
  colBid.Name:SetJustifyH("CENTER")
  colBid.Name:SetText(CURRENT_BID)

  local cols = {
    colName,
    colLevel,
    colType,
    colDuration,
    colSeller,
    colBid,
  }

  for _, col in ipairs(cols) do
    -- these fields exist on the BlackMarketColumnButtonTemplate
    local left  = col.Left     or col:FindTexture("Left")
    local mid   = col.Middle   or col:FindTexture("Middle")
    local right = col.Right    or col:FindTexture("Right")

    if mid then
      -- enable tiling
      mid:SetHorizTile(true)

      -- re-anchor to span exactly from left→right
      mid:ClearAllPoints()
      mid:SetPoint("LEFT",  left,  "RIGHT",  0, 0)
      mid:SetPoint("RIGHT", right, "LEFT",   0, 0)
      mid:SetHeight(col:GetHeight())

      -- adjust texcoords if your caps aren’t exactly at .125/.875
      mid:SetTexCoord(.125, .875, 0, 1)
    end
  end

  -- Close button (reuse if exists)
  local closeName = f:GetName() .. "CloseButton"
  local close = _G[closeName]
  if not close then
    close = CreateFrame("Button", closeName, f, "UIPanelCloseButton")
    close:SetPoint("TOPRIGHT", f, "TOPRIGHT", 4, 4)
  end

  -- after your XML has created BlackMarketFrameListBackdrop:
  local lb = _G["BlackMarketFrameListBackdrop"]
  if lb then
    -- Give it a 1px gray edge + tileable black fill
    lb:SetBackdrop({
      bgFile   = "Interface\\Buttons\\WHITE8X8",   -- 1×1 white
      edgeFile = "Interface\\Buttons\\WHITE8X8",   -- same 1×1 for border
      tile     = true,
      tileSize = 1,
      edgeSize = 1,
      insets   = { left=0, right=0, top=0, bottom=0 },
    })
    lb:SetBackdropColor(0, 0, 0, 0.6)               -- black @ 60%
    lb:SetBackdropBorderColor(0.5, 0.5, 0.5, 1)     -- gray @ 100%
  end

  local texture = BlackMarketFrame:CreateTexture(nil, "ARTWORK")
  texture:SetTexture("Interface\\AddOns\\BlackMarketUI\\Media\\HotItemText.blp")
  texture:SetSize(128, 32)  -- Half of 256x64
  texture:SetPoint("TOP", 306, -128) -- Adjust position as needed
  texture:Show()

  local function StartRangeWatcher(frame)
    -- Record the NPC’s GUID at the moment the UI is shown
    local npcUnit, npcGUID = "target", UnitGUID("target")
    if not npcGUID then return end

    frame._npcGUID = npcGUID

    -- On every frame, check if we’re still within interaction range
    frame:SetScript("OnUpdate", function(self, elapsed)
      -- If you no longer have that NPC targeted, or you're out of  trade range, hide:
      if UnitGUID(npcUnit) ~= self._npcGUID or not CheckInteractDistance(npcUnit, 2) then
        self:Hide()
        -- stop checking
        self:SetScript("OnUpdate", nil)
      end
    end)
  end

  -- Hook the frame’s Show method so the watcher starts whenever you open it
  hooksecurefunc(BlackMarketFrame, "Show", function(self)
    StartRangeWatcher(self)
  end)

  -- ------------------------------------------------------------------
  -- Let Escape close the frame
  -- ------------------------------------------------------------------
  -- Add this frame to the global UISpecialFrames list.
  -- Any frame in that list with a valid "Close" button will automatically
  -- hide itself when the player presses Escape.
  tinsert(UISpecialFrames, "BlackMarketFrame")
end)

-- ── ONE-TIME INITIALIZER ON LOGIN ─────────────────────────────────────────
local init = CreateFrame("Frame")
init:RegisterEvent("PLAYER_LOGIN")
init:SetScript("OnEvent", function(self, event)
  -- Grab the (hidden) BlackMarketFrame
  local f = _G["BlackMarketFrame"]
  if not f then return end

  -- ── Spawn a second Bid button ───────────────────────────────────────────────
  local secondBid = CreateFrame("Button", "BlackMarketFrameSecondBidButton", f, "UIPanelButtonTemplate")
  secondBid:SetSize(80, 20)
  -- adjust these offsets to wherever you want it
  secondBid:SetPoint("BOTTOMRIGHT", f, "BOTTOMRIGHT", -100, 134)
  secondBid:SetText("Bid")
  secondBid:SetScript("OnClick", function(self)
    -- reuse Blizzard’s bid handler
    BlackMarketBid_OnClick(self)
  end)
  secondBid:SetScript("OnClick", BlackMarketHotBid_OnClick)

  local goldOnly = CreateFrame("Frame", "BlackMarketFrameGoldEntry", BlackMarketFrame, "MoneyInputFrameTemplate")
  goldOnly:SetSize(120, 22)
  goldOnly:SetPoint("BOTTOMRIGHT", BlackMarketFrame, "BOTTOMRIGHT", -96, 164)

  -- Combined seller line
  local sellerLine = BlackMarketFrame:CreateFontString("BlackMarketFrameSellerLine", "OVERLAY", "GameFontNormalLarge")
  sellerLine:SetPoint("CENTER", BlackMarketFrame, "BOTTOMRIGHT", -138, 237)
  sellerLine:SetText("Market Currently Closed")  -- default

  -- Combined time left line
  local timeLeftLine = BlackMarketFrame:CreateFontString("BlackMarketFrameTimeLeftLine", "OVERLAY", "GameFontHighlightSmall")
  timeLeftLine:SetPoint("CENTER", BlackMarketFrame, "BOTTOMRIGHT", -138, 198)
  timeLeftLine:SetText("Market Currently Closed")  -- default

  local parent = BlackMarketFrame

  -- Container frame
  local itemFrame = CreateFrame("Frame", "BlackMarketFrameHotItemDisplay", parent)
  itemFrame:SetSize(155, 36)
  itemFrame:SetPoint("BOTTOMRIGHT", parent, "BOTTOMRIGHT", -46, 290)

  -- Left cap
  local left = itemFrame:CreateTexture(nil, "BACKGROUND")
  left:SetTexture("Interface\\AuctionFrame\\UI-AuctionItemNameFrame")
  left:SetTexCoord(0, 0.078125, 0, 1)
  left:SetSize(10, 32)
  left:SetPoint("TOPLEFT", itemFrame, "TOPLEFT", 0, 0)

  -- Right cap
  local right = itemFrame:CreateTexture(nil, "BACKGROUND")
  right:SetTexture("Interface\\AuctionFrame\\UI-AuctionItemNameFrame")
  right:SetTexCoord(0.75, 0.828125, 0, 1)
  right:SetSize(10, 32)
  right:SetPoint("TOPRIGHT", itemFrame, "TOPRIGHT", 0, 0)

  -- Middle stretch
  local middle = itemFrame:CreateTexture(nil, "BACKGROUND")
  middle:SetTexture("Interface\\AuctionFrame\\UI-AuctionItemNameFrame", true, false)  -- horizontal tiling on
  middle:SetTexCoord(0.078125, 0.75, 0, 1)
  middle:SetHeight(32)
  middle:SetPoint("LEFT", left, "RIGHT", 0, 0)
  middle:SetPoint("RIGHT", right, "LEFT", 0, 0)

  BlackMarketMoneyFrame:SetFrameStrata("DIALOG")
  BlackMarketMoneyFrame:SetFrameLevel(BlackMarketFrame:GetFrameLevel() + 15)

  if BlackMarketFrame.MoneyFrameBorder then
    BlackMarketFrame.MoneyFrameBorder:SetFrameStrata("DIALOG")
    BlackMarketFrame.MoneyFrameBorder:SetFrameLevel(BlackMarketFrame:GetFrameLevel() + 14)
  end

  -- Container frame for layering
  local goldBgFrame = CreateFrame("Frame", nil, BlackMarketFrame)
  goldBgFrame:SetSize(149, 19)
  goldBgFrame:SetPoint("BOTTOMLEFT", BlackMarketFrame, "BOTTOMLEFT", 52, 1)
  goldBgFrame:SetFrameStrata("DIALOG")
  goldBgFrame:SetFrameLevel(BlackMarketFrame:GetFrameLevel() + 10)

  -- Actual texture
  local goldBg = goldBgFrame:CreateTexture(nil, "OVERLAY")
  goldBg:SetTexture("Interface\\AddOns\\BlackMarketUI\\Media\\GoldBack.blp")
  goldBg:SetAllPoints()

  -- Icon Button
  local icon = CreateFrame("Button", "BlackMarketFrameHotItemIcon", itemFrame)
  icon:SetSize(32, 32)
  icon:SetPoint("LEFT", itemFrame, "LEFT", -32, 2)

  icon.texture = icon:CreateTexture(nil, "ARTWORK")
  icon.texture:SetAllPoints()
  icon.texture:SetTexture("Interface\\Icons\\INV_Misc_QuestionMark") -- default placeholder


  icon:EnableMouse(true)
  icon:SetHitRectInsets(0, -159, 0, 0)
  icon:SetScript("OnEnter", function(self)
    GameTooltip:SetOwner(self, "ANCHOR_RIGHT")
    local rec = rows[hotItemIndex]
    if not rec then return end
    GameTooltip:SetHyperlink(("item:%d:0:0:0:0:0:0"):format(rec.wowEntry or rec.itemId))
    GameTooltip:Show()
  end)
  icon:SetScript("OnLeave", GameTooltip_Hide)

  -- Item name
  local nameText = itemFrame:CreateFontString("BlackMarketFrameHotItemName", "OVERLAY", "GameFontNormal")
  nameText:SetPoint("LEFT", icon, "RIGHT", 6, 0)
  nameText:SetPoint("RIGHT", itemFrame, "RIGHT", -6, 0)
  nameText:SetJustifyH("LEFT")
  nameText:SetText("Market Currently Closed")
  nameText:SetWordWrap(false)

  -- Subclass label (e.g. Companion Pet)
  local subclassText = itemFrame:CreateFontString("BlackMarketFrameHotItemSubclass", "OVERLAY", "GameFontHighlightSmall")
  subclassText:SetPoint("CENTER", nameText, "BOTTOMLEFT", 55, -20)
  subclassText:SetText("Market Currently Closed")

  do
    local txt = parent:CreateFontString(nil, "OVERLAY", "GameFontNormalLarge")
    -- place it dead‐center in the scroll area:
    txt:SetPoint("CENTER", parent.ScrollFrame, "CENTER", -130, 0)
    txt:SetText("There are no items currently on market. Check back later!")
    txt:Hide()
    parent.noItemsText = txt
  end

  do
    local frame = _G["BlackMarketFrame"]
    if frame then
      frame:HookScript("OnShow", function(self)
        -- 0) clear out any previous selection
        if selectedButton then
          selectedButton:UnlockHighlight()
          selectedButton = nil
          selectedIndex  = nil
        end
        -- clear out old rows
        wipe(rows)
        local scroll = _G["BlackMarketFrameScrollFrame"]
        if scroll and scroll.scrollBar then
          scroll.scrollBar:SetValue(0)
        end
        -- send a fresh request to the server
        SendChatMessage(REQ, "WHISPER", nil, UnitName("player"))
      end)
    else
      DEFAULT_CHAT_FRAME:AddMessage("|cffff0000[BMAH UI]|r BlackMarketFrame not found for OnShow hook!")
    end
  end

  bmahUIInitTime = GetTime()
end)

-- Called by each row’s OnClick
function BlackMarketUI_Row_OnClick(self)
  local scroll = BlackMarketFrameScrollFrame
  -- how many rows have been scrolled off-screen
  local offset = HybridScrollFrame_GetOffset(scroll)
  -- self:GetID() is 1..#visibleButtons
  local index = self:GetID() + offset
  BlackMarketUI_SelectRow(index)
end

-- visually mark one row as "selected" and store its index
function BlackMarketUI_SelectRow(index)
  local scroll   = BlackMarketFrameScrollFrame
  local offset   = HybridScrollFrame_GetOffset(scroll)
  local onScreen = index - offset
  local btn      = scroll.buttons[onScreen]

  -- 1) un-highlight the previous selection, if any
  if selectedButton then
    selectedButton:UnlockHighlight()
  end

  -- 2) if the new button exists, highlight it and store it
  if btn then
    btn:LockHighlight()
    selectedButton = btn
    selectedIndex  = index
  end
end

-- optional tooltip on hover
function BlackMarketUI_Row_OnEnter(self)
  self:LockHighlight()
  -- determine which record this button represents
  local scroll = BlackMarketFrameScrollFrame
  local offset = HybridScrollFrame_GetOffset(scroll)
  local rec    = rows[self:GetID() + offset]
  if not rec then return end

  GameTooltip:SetOwner(self,     "ANCHOR_RIGHT")
  GameTooltip:SetHyperlink(("item:%d:0:0:0:0:0:0"):format(rec.wowEntry or rec.itemId))
  GameTooltip:Show()
end

function BlackMarketUI_Row_OnLeave(self)
  if self ~= selectedButton then
    self:UnlockHighlight()
  end
  GameTooltip:Hide()
end

local function ClearBidInputsAndRefresh()
  -- clear both boxes
  MoneyInputFrame_SetCopper(BlackMarketBidPrice,       0)
  MoneyInputFrame_SetCopper(BlackMarketFrameGoldEntry, 0)

  -- redraw the scroll list
  BlackMarketUI_UpdateList(rows)
  -- re-highlight the selected row if any
  if selectedIndex then
    BlackMarketUI_SelectRow(selectedIndex)
  end

  -- re-draw the hot item panel if we have one
  if hotItemIndex then
    local hotRow = rows[hotItemIndex]
    if hotRow then
      -- build the icon path just like you do in OnEvent
      local iconPath = hotRow.icon and hotRow.icon ~= ""
                       and "Interface\\Icons\\" .. hotRow.icon
                       or "Interface\\Icons\\INV_Misc_QuestionMark"
      BlackMarketFrameHotItemIcon.texture:SetTexture(iconPath)

      BlackMarketFrameHotItemName:SetText(hotRow.itemName)
      BlackMarketFrameHotItemSubclass:SetText(hotRow.itemType)
      BlackMarketFrameSellerLine:SetText("Seller:\n" .. hotRow.owner)
      BlackMarketFrameTimeLeftLine:SetText("Time Left: " .. hotRow.timeLeft)
    end
  end
end

-- Called by your XML OnClick on the “BID” button
function BlackMarketBid_OnClick(self)
  -- 1) Make sure we have a selection
  if not selectedIndex then
    DEFAULT_CHAT_FRAME:AddMessage("|cff69ccf0[BMAH]|r Please select an item to bid on first.")
    return
  end

  -- 2) Fetch the record for that row
  local record = rows[selectedIndex]
  if not record then
    DEFAULT_CHAT_FRAME:AddMessage("|cff69ccf0[BMAH]|r Invalid selection.")
    return
  end

  -- 3) Read the copper amount from the MoneyInputFrame
  --    MoneyInputFrame_GetCopper returns total copper in the frame
  local bidCopper = MoneyInputFrame_GetCopper(BlackMarketBidPrice)
  --    Convert copper → gold (flooring)
  local bidGold = math.floor(bidCopper / (COPPER_PER_SILVER * SILVER_PER_GOLD))

  
  -- trigger confirmation popup:
  StaticPopup_Show(
    "BMAH_CONFIRM_BID",
    bidGold,
    record.itemName,
    { itemId = record.itemId, amount = bidGold }
  )
end

function BlackMarketHotBid_OnClick(self)
  if not hotItemIndex then
    DEFAULT_CHAT_FRAME:AddMessage("|cff69ccf0[BMAH]|r No hot item available to bid on right now.")
    return
  end
  local record = rows[hotItemIndex]
  if not record then
    DEFAULT_CHAT_FRAME:AddMessage("|cff69ccf0[BMAH]|r Hot item data lost—please reopen the UI.")
    return
  end

  -- read copper from the blue MoneyInputFrame we created (named "BlackMarketFrameGoldEntry")
  local bidCopper = MoneyInputFrame_GetCopper(BlackMarketFrameGoldEntry)
  local bidGold   = math.floor(bidCopper / (COPPER_PER_SILVER * SILVER_PER_GOLD))

  
  -- trigger confirmation popup:
  StaticPopup_Show(
    "BMAH_CONFIRM_BID",
    bidGold,
    record.itemName,
    { itemId = record.itemId, amount = bidGold }
  )
end
