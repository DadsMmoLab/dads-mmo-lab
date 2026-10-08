-- Unbound Wrath Edition — universal skill access for Unbound characters
-- Applied to: acore_world.skillraceclassinfo_dbc
--
-- Problem: AzerothCore's _LoadSkills validates every skill against
-- GetSkillRaceClassInfo(skill, race, class). If no entry exists for that
-- skill+race+class combo, the skill is stripped from memory on every login.
-- This prevents Unbound characters from keeping cross-class skills (Staves
-- for a Paladin, Daggers for a Warrior, etc.).
--
-- Fix: insert rows with ClassMask=0, RaceMask=0 for every skill we need.
-- ClassMask=0 → all classes. RaceMask=0 → all races. This makes the check
-- always return a valid entry, allowing any character to keep any listed skill.
--
-- These rows are loaded at server start via storage.LoadFromDB("skillraceclassinfo_dbc")
-- called inside LoadDBC() in DBCStores.cpp.  Restart required after applying.
--
-- Safe to re-run: DELETE + re-INSERT on our ID range.

DELETE FROM skillraceclassinfo_dbc WHERE ID >= 10000;

-- Auto-generate one row per skill.
-- Fields: ID, SkillID, RaceMask, ClassMask, Flags, MinLevel, SkillTierID, SkillCostIndex
-- ClassMask=0 = all classes, RaceMask=0 = all races, SkillTierID=0 = level-scaled.

INSERT INTO skillraceclassinfo_dbc
  (ID, SkillID, RaceMask, ClassMask, Flags, MinLevel, SkillTierID, SkillCostIndex)
VALUES
-- ── Weapon skills ────────────────────────────────────────────────────────────
(10001,  43, 0, 0, 0, 0, 0, 0),   -- Swords
(10002,  44, 0, 0, 0, 0, 0, 0),   -- Axes
(10003,  45, 0, 0, 0, 0, 0, 0),   -- Bows
(10004,  46, 0, 0, 0, 0, 0, 0),   -- Guns
(10005,  54, 0, 0, 0, 0, 0, 0),   -- Maces
(10006,  55, 0, 0, 0, 0, 0, 0),   -- Two-Handed Swords
(10007, 118, 0, 0, 0, 0, 0, 0),   -- Dual Wield
(10008, 136, 0, 0, 0, 0, 0, 0),   -- Staves
(10009, 160, 0, 0, 0, 0, 0, 0),   -- Two-Handed Maces
(10010, 162, 0, 0, 0, 0, 0, 0),   -- Unarmed
(10011, 172, 0, 0, 0, 0, 0, 0),   -- Two-Handed Axes
(10012, 173, 0, 0, 0, 0, 0, 0),   -- Daggers
(10013, 176, 0, 0, 0, 0, 0, 0),   -- Thrown
(10014, 226, 0, 0, 0, 0, 0, 0),   -- Crossbows
(10015, 228, 0, 0, 0, 0, 0, 0),   -- Wands
(10016, 229, 0, 0, 0, 0, 0, 0),   -- Polearms
(10017, 433, 0, 0, 0, 0, 0, 0),   -- Shield
(10018, 473, 0, 0, 0, 0, 0, 0),   -- Fist Weapons
-- ── Armor skills ─────────────────────────────────────────────────────────────
(10019, 293, 0, 0, 0, 0, 0, 0),   -- Plate Mail
(10020, 413, 0, 0, 0, 0, 0, 0),   -- Mail
(10021, 414, 0, 0, 0, 0, 0, 0),   -- Leather
(10022, 415, 0, 0, 0, 0, 0, 0),   -- Cloth
-- ── Class spellbook tab skills (from playercreateinfo_skills classMask!=0) ───
-- These allow Unbound characters to keep spellbook tabs from unlocked classes.
(10030,   6, 0, 0, 0, 0, 0, 0),
(10031,   8, 0, 0, 0, 0, 0, 0),
(10032,  26, 0, 0, 0, 0, 0, 0),
(10033,  38, 0, 0, 0, 0, 0, 0),
(10034,  39, 0, 0, 0, 0, 0, 0),
(10035,  50, 0, 0, 0, 0, 0, 0),
(10036,  51, 0, 0, 0, 0, 0, 0),
(10037,  56, 0, 0, 0, 0, 0, 0),
(10038,  78, 0, 0, 0, 0, 0, 0),
(10039, 129, 0, 0, 0, 0, 0, 0),
(10040, 134, 0, 0, 0, 0, 0, 0),
(10041, 163, 0, 0, 0, 0, 0, 0),
(10042, 184, 0, 0, 0, 0, 0, 0),
(10043, 237, 0, 0, 0, 0, 0, 0),
(10044, 253, 0, 0, 0, 0, 0, 0),
(10045, 256, 0, 0, 0, 0, 0, 0),
(10046, 257, 0, 0, 0, 0, 0, 0),
(10047, 267, 0, 0, 0, 0, 0, 0),
(10048, 354, 0, 0, 0, 0, 0, 0),
(10049, 355, 0, 0, 0, 0, 0, 0),
(10050, 373, 0, 0, 0, 0, 0, 0),
(10051, 374, 0, 0, 0, 0, 0, 0),
(10052, 375, 0, 0, 0, 0, 0, 0),
(10053, 573, 0, 0, 0, 0, 0, 0),
(10054, 574, 0, 0, 0, 0, 0, 0),
(10055, 593, 0, 0, 0, 0, 0, 0),
(10056, 594, 0, 0, 0, 0, 0, 0),
(10057, 613, 0, 0, 0, 0, 0, 0),
(10058, 762, 0, 0, 0, 0, 0, 0),
(10059, 770, 0, 0, 0, 0, 0, 0),
(10060, 771, 0, 0, 0, 0, 0, 0),
(10061, 772, 0, 0, 0, 0, 0, 0);
