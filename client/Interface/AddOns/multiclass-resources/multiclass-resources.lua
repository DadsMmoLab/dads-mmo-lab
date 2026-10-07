-- multiclass-resources (WotLK 3.3.5a)
-- Shows multiple power types + combo points at once.
-- Slash commands:
--   /mcr unlock | /mcr lock
--   /mcr scale 1.2
--   /mcr reset
--   /mcr debug

local ADDON = "Multiclass Resources"
local ADV2_PREFIX = "ADV2"

local serverCombo = {
	cp = 0,
	max = 5,
	targetLow = 0,
	updated = false,
}

local POWER_TYPES = {
	{ id = 0, token = "MANA",        label = "Mana",  force = true  },
	{ id = 1, token = "RAGE",        label = "Rage",  force = true  },
	{ id = 2, token = "FOCUS",       label = "Focus", force = false },
	{ id = 3, token = "ENERGY",      label = "Energy",force = true  },
	{ id = 6, token = "RUNIC_POWER", label = "Runic", force = false },
	{ id = 4, token = "COMBO_POINTS",label = "Combo", force = true,  isCombo = true },
}

local function GetPowerColor(token)
	if PowerBarColor and PowerBarColor[token] then
		local c = PowerBarColor[token]
		if c and c.r then return c.r, c.g, c.b end
	end
	return 0.25, 0.50, 1.00
end

local function Clamp(n, lo, hi)
	if n < lo then return lo end
	if n > hi then return hi end
	return n
end

local function IsAdventurerClass()
	local _, englishClass, classId = UnitClass("player")
	if classId == 12 then return true end
	if englishClass and englishClass:upper() == "ADVENTURER" then return true end
	return false
end

if type(AdventurerResourcesDB) ~= "table" then AdventurerResourcesDB = {} end
local DB = AdventurerResourcesDB

local DEFAULTS = {
	locked = false,
	scale  = 1.0,
	point    = "CENTER",
	relPoint = "CENTER",
	x = 0,
	y = -160,
}

for k, v in pairs(DEFAULTS) do
	if DB[k] == nil then DB[k] = v end
end

local UI = CreateFrame("Frame", "MulticlassResourcesFrame", UIParent)
UI:SetSize(260, 18)
UI:ClearAllPoints()
UI:SetPoint(DB.point, UIParent, DB.relPoint, DB.x, DB.y)
UI:SetScale(DB.scale)
UI:SetFrameStrata("HIGH")
UI:EnableMouse(true)
UI:SetClampedToScreen(true)

if UI.SetBackdrop then
	UI:SetBackdrop({
		bgFile = "Interface\\Tooltips\\UI-Tooltip-Background",
		edgeFile = "Interface\\Tooltips\\UI-Tooltip-Border",
		tile = true, tileSize = 16, edgeSize = 16,
		insets = { left = 4, right = 4, top = 4, bottom = 4 }
	})
	UI:SetBackdropColor(0, 0, 0, 0.65)
else
	UI.bg = UI:CreateTexture(nil, "BACKGROUND")
	UI.bg:SetAllPoints()
	UI.bg:SetTexture("Interface\\Tooltips\\UI-Tooltip-Background")
	UI.bg:SetVertexColor(0, 0, 0, 0.65)
end

local title = UI:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
title:SetPoint("BOTTOMLEFT", UI, "TOPLEFT", 4, 2)
title:SetText("Multiclass Resources")

local bars = {}

local function CreateBar(parent)
	local f = CreateFrame("StatusBar", nil, parent)
	f:SetHeight(14)
	f:SetMinMaxValues(0, 1)
	f:SetValue(0)

	f.bg = f:CreateTexture(nil, "BACKGROUND")
	f.bg:SetAllPoints(true)
	f.bg:SetTexture("Interface\\TargetingFrame\\UI-StatusBar")

	f.text = f:CreateFontString(nil, "OVERLAY", "GameFontNormalSmall")
	f.text:SetPoint("CENTER", f, "CENTER", 0, 0)

	f:SetStatusBarTexture("Interface\\TargetingFrame\\UI-StatusBar")

	return f
end

for i = 1, #POWER_TYPES do
	local b = CreateBar(UI)
	b.pt = POWER_TYPES[i]
	bars[i] = b
end

local function SetLocked(locked)
	DB.locked = locked
	if locked then
		UI:SetMovable(false)
		UI:SetScript("OnDragStart", nil)
		UI:SetScript("OnDragStop", nil)
	else
		UI:RegisterForDrag("LeftButton")
		UI:SetMovable(true)
		UI:SetScript("OnDragStart", function(self) self:StartMoving() end)
		UI:SetScript("OnDragStop", function(self)
			self:StopMovingOrSizing()
			local point, _, relPoint, x, y = self:GetPoint(1)
			DB.point, DB.relPoint = point, relPoint
			DB.x, DB.y = x, y
		end)
	end
end

local function LayoutBars()
	local visible = {}

	for i = 1, #bars do
		if bars[i]:IsShown() then
			visible[#visible + 1] = bars[i]
		end
	end

	local count = #visible
	if count == 0 then
		UI:SetHeight(18)
		return
	end

	local pad = 6
	local barH = 14
	local gap = 4
	UI:SetHeight(pad + (count * barH) + ((count - 1) * gap) + pad)

	for idx = 1, count do
		local b = visible[idx]
		b:ClearAllPoints()
		if idx == 1 then
			b:SetPoint("TOPLEFT", UI, "TOPLEFT", 8, -8)
			b:SetPoint("TOPRIGHT", UI, "TOPRIGHT", -8, -8)
		else
			b:SetPoint("TOPLEFT", visible[idx - 1], "BOTTOMLEFT", 0, -gap)
			b:SetPoint("TOPRIGHT", visible[idx - 1], "BOTTOMRIGHT", 0, -gap)
		end
	end
end

local function ReadClientComboPoints()
	local unit = (UnitHasVehicleUI and UnitHasVehicleUI("player")) and "vehicle" or "player"
	local cp = 0

	if GetComboPoints then
		if UnitExists("target") then
			cp = GetComboPoints(unit, "target") or 0
		end
		if cp == 0 then
			cp = GetComboPoints(unit) or 0
		end
		if cp == 0 then
			cp = GetComboPoints(unit, unit) or 0
		end
	end

	return cp
end

local function ReadComboPoints()
	local maxCP = MAX_COMBO_POINTS or 5
	local cp = ReadClientComboPoints()

	if cp > 0 then
		return cp, maxCP
	end

	if serverCombo.updated then
		cp = serverCombo.cp
		maxCP = serverCombo.max > 0 and serverCombo.max or maxCP
	end

	return cp, maxCP
end

local function HandleAdv2ComboMessage(msg)
	local cp, tgtLow, max = msg:match("^CP:(%d+):(%d+):(%d+)$")
	if not cp then
		return
	end
	serverCombo.cp = tonumber(cp) or 0
	serverCombo.targetLow = tonumber(tgtLow) or 0
	serverCombo.max = tonumber(max) or 5
	serverCombo.updated = true
end

local function UpdateBar(b)
	local pt = b.pt
	local val, maxv

	if pt.isCombo then
		val, maxv = ReadComboPoints()
	else
		val = UnitPower("player", pt.id) or 0
		maxv = UnitPowerMax("player", pt.id) or 0
		if maxv == 0 then maxv = 1 end
	end

	val = Clamp(val, 0, maxv)

	local shouldShow = pt.force or val > 0 or (maxv and maxv > 1)
	if shouldShow then
		local r, g, bcol = GetPowerColor(pt.token)
		b:SetStatusBarColor(r, g, bcol)
		b.bg:SetVertexColor(0.15, 0.15, 0.15, 0.85)
		b:SetMinMaxValues(0, maxv)
		b:SetValue(val)
		b.text:SetText(string.format("%s: %d / %d", pt.label, val, maxv))
		b:Show()
	else
		b:Hide()
	end
end

local function UpdateAll()
	for i = 1, #bars do
		UpdateBar(bars[i])
	end
	LayoutBars()
end

if RegisterAddonMessagePrefix then
	RegisterAddonMessagePrefix(ADV2_PREFIX)
end

UI:RegisterEvent("PLAYER_ENTERING_WORLD")
UI:RegisterEvent("UNIT_POWER")
UI:RegisterEvent("UNIT_MAXPOWER")
UI:RegisterEvent("UNIT_DISPLAYPOWER")
UI:RegisterEvent("PLAYER_TARGET_CHANGED")
UI:RegisterEvent("UPDATE_SHAPESHIFT_FORM")
UI:RegisterEvent("UNIT_COMBO_POINTS")
UI:RegisterEvent("CHAT_MSG_ADDON")

UI:SetScript("OnEvent", function(_, event, ...)
	if event == "CHAT_MSG_ADDON" then
		local prefix, msg = ...
		if prefix == ADV2_PREFIX and msg then
			HandleAdv2ComboMessage(msg)
			UpdateAll()
		end
		return
	end

	local unit = ...
	if event == "UNIT_COMBO_POINTS" or event == "PLAYER_TARGET_CHANGED"
		or event == "PLAYER_ENTERING_WORLD" or event == "UPDATE_SHAPESHIFT_FORM" then
		UpdateAll()
		return
	end
	if unit and unit ~= "player" and unit ~= "target" and unit ~= "vehicle" then
		return
	end
	UpdateAll()
end)

local elapsed = 0
UI:SetScript("OnUpdate", function(_, dt)
	elapsed = elapsed + dt
	if elapsed >= 0.10 then
		elapsed = 0
		UpdateAll()
	end
end)

local function HandleSlash(msg)
	msg = (msg or ""):lower()

	if msg == "unlock" then
		SetLocked(false)
		print(ADDON .. ": unlocked (drag to move).")
	elseif msg == "lock" then
		SetLocked(true)
		print(ADDON .. ": locked.")
	elseif msg == "debug" then
		local unit = (UnitHasVehicleUI and UnitHasVehicleUI("player")) and "vehicle" or "player"
		local cpTarget = UnitExists("target") and (GetComboPoints(unit, "target") or 0) or "n/a"
		local cpOne = GetComboPoints and (GetComboPoints(unit) or 0) or "n/a"
		local cpSelf = GetComboPoints and (GetComboPoints(unit, unit) or 0) or "n/a"
		print(string.format(
			"%s debug: adventurer=%s clientCP=%s serverCP=%s serverTarget=%s target=%s GetCP(target)=%s GetCP()=%s GetCP(self)=%s",
			ADDON,
			tostring(IsAdventurerClass()),
			tostring(ReadClientComboPoints()),
			tostring(serverCombo.updated and serverCombo.cp or "none"),
			tostring(serverCombo.updated and serverCombo.targetLow or "none"),
			tostring(UnitExists("target")),
			tostring(cpTarget),
			tostring(cpOne),
			tostring(cpSelf)
		))
	elseif msg:match("^scale") then
		local s = tonumber(msg:match("scale%s+([%d%.]+)"))
		if s and s > 0.3 and s < 3.0 then
			DB.scale = s
			UI:SetScale(DB.scale)
			print(ADDON .. ": scale set to " .. s)
		else
			print(ADDON .. ": usage: /mcr scale 1.2")
		end
	elseif msg == "reset" then
		DB.point, DB.relPoint = "CENTER", "CENTER"
		DB.x, DB.y = 0, -160
		UI:ClearAllPoints()
		UI:SetPoint(DB.point, UIParent, DB.relPoint, DB.x, DB.y)
		print(ADDON .. ": position reset.")
	else
		print(ADDON .. " commands:")
		print("  /mcr unlock | /mcr lock")
		print("  /mcr scale 1.2")
		print("  /mcr reset")
		print("  /mcr debug")
	end
end

SLASH_MULTICLASSRES1 = "/mcr"
SLASH_MULTICLASSRES2 = "/ar"
SlashCmdList["MULTICLASSRES"] = HandleSlash

SetLocked(DB.locked)
UpdateAll()
UI:Show()

print("|cff00ff00[Multiclass Resources]|r loaded. /mcr for options.")
