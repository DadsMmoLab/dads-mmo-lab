-- BMAH_Up.sql  (v4)
-- Dad's MMO Lab ALE-Kegs / BlackMarketAuctionHouse
-- Creates the Black Market Broker NPC (entry 2069430) in acore_world.
--
-- Apply to acore_world:
--   docker exec -i <db-container> mysql -u acore -pacore acore_world < BMAH_Up.sql
--
-- After applying: RESTART the worldserver, then:
--   .npc add 2069430        — spawn the broker
--   .npc set model <id>     — set appearance if desired
--
-- Safe to re-run: DELETE + INSERT uses ON DUPLICATE KEY; model/text are idempotent.

-- ── 1. NPC template ─────────────────────────────────────────────────────────────
DELETE FROM `creature_template` WHERE `entry` = 2069430;
INSERT INTO `creature_template`
  (`entry`, `name`, `subname`, `gossip_menu_id`,
   `minlevel`, `maxlevel`, `exp`, `faction`, `npcflag`,
   `speed_walk`, `speed_run`, `rank`,
   `dmgschool`, `DamageModifier`,
   `BaseAttackTime`, `RangeAttackTime`,
   `BaseVariance`, `RangeVariance`,
   `unit_class`, `unit_flags`, `unit_flags2`, `dynamicflags`,
   `type`, `AIName`, `MovementType`, `HoverHeight`,
   `HealthModifier`, `ManaModifier`, `ArmorModifier`,
   `RegenHealth`, `flags_extra`, `VerifiedBuild`)
VALUES
  (2069430, 'Black Market Broker', 'Rare Goods & Services', 0,
   80, 80, 0, 35, 1,
   1.0, 1.14286, 0,
   0, 1.0,
   2000, 2000,
   1.0, 1.0,
   1, 33536, 2048, 0,
   7, '', 0, 1.0,
   1.0, 1.0, 1.0,
   1, 2, 0);

-- ── 1b. Scale — schema-adaptive (column removed in some AC builds) ────────────────
SET @hasScale = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'creature_template' AND COLUMN_NAME = 'scale'
);
SET @sql = IF(@hasScale > 0,
  'UPDATE creature_template SET scale = 1.0 WHERE entry = 2069430',
  'SELECT ''Skipping scale — column not present in this AC build'' AS note'
);
PREPARE _bmah_stmt FROM @sql; EXECUTE _bmah_stmt; DEALLOCATE PREPARE _bmah_stmt;

-- ── 2. Display model — schema-adaptive ───────────────────────────────────────────
-- Picks a display ID already in the DB (prefers Krazek → Privateer Bloads → any
-- valid row), so the INSERT is guaranteed to reference a real CreatureDisplayInfo
-- entry regardless of which AC fork or DBC revision the server is running.

SET @hasModelTable = (
  SELECT COUNT(*) FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'creature_template_model'
);
-- Resolve a safe display ID dynamically from existing creatures
SET @bmah_display_id = 6557; -- absolute fallback if table is empty / doesn't exist
SET @sql = IF(@hasModelTable > 0,
  'SELECT COALESCE(MIN(CASE WHEN CreatureID=7164 THEN CreatureDisplayID ELSE NULL END), MIN(CASE WHEN CreatureID=2494 THEN CreatureDisplayID ELSE NULL END), MIN(CreatureDisplayID)) INTO @bmah_display_id FROM creature_template_model WHERE CreatureDisplayID > 0',
  'SELECT 6557');
PREPARE _bmah_stmt FROM @sql; EXECUTE _bmah_stmt; DEALLOCATE PREPARE _bmah_stmt;
SET @bmah_display_id = COALESCE(@bmah_display_id, 6557);
SELECT CONCAT('Using CreatureDisplayID = ', @bmah_display_id) AS model_note;

SET @sql = IF(@hasModelTable > 0,
  'DELETE FROM creature_template_model WHERE CreatureID = 2069430',
  'SELECT 1'
);
PREPARE _bmah_stmt FROM @sql; EXECUTE _bmah_stmt; DEALLOCATE PREPARE _bmah_stmt;
SET @sql = IF(@hasModelTable > 0,
  CONCAT('INSERT INTO creature_template_model (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability, VerifiedBuild) VALUES (2069430, 0, ', @bmah_display_id, ', 1.0, 1.0, 0)'),
  'SELECT ''Skipping creature_template_model — not present in this AC build'' AS note'
);
PREPARE _bmah_stmt FROM @sql; EXECUTE _bmah_stmt; DEALLOCATE PREPARE _bmah_stmt;

SET @hasModelid1 = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'creature_template' AND COLUMN_NAME = 'modelid1'
);
SET @sql = IF(@hasModelid1 > 0,
  CONCAT('UPDATE creature_template SET modelid1 = ', @bmah_display_id, ' WHERE entry = 2069430'),
  'SELECT ''Skipping modelid1 — column not present in this AC build'' AS note'
);
PREPARE _bmah_stmt FROM @sql; EXECUTE _bmah_stmt; DEALLOCATE PREPARE _bmah_stmt;

-- ── 3. Gossip text ────────────────────────────────────────────────────────────────
DELETE FROM `npc_text` WHERE `ID` = 2069430;
INSERT INTO `npc_text`
  (`ID`, `text0_0`, `text0_1`, `BroadcastTextID0`, `lang0`, `Probability0`,
   `em0_0`, `em0_1`, `em0_2`, `em0_3`, `em0_4`, `em0_5`)
VALUES
  (2069430,
   'Welcome to the Black Market.$B$BOnly the finest goods, procured at great risk.',
   '', 0, 0, 1, 0, 0, 0, 0, 0, 0);

-- ── Diagnostic: confirm what was inserted ────────────────────────────────────────
SELECT entry, name, faction, npcflag FROM creature_template WHERE entry = 2069430;
SET @hasModelTable2 = (
  SELECT COUNT(*) FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'creature_template_model'
);
SET @sql = IF(@hasModelTable2 > 0,
  'SELECT CreatureID, CreatureDisplayID, DisplayScale FROM creature_template_model WHERE CreatureID = 2069430',
  'SELECT ''creature_template_model table not present'' AS model_note'
);
PREPARE _bmah_diag FROM @sql; EXECUTE _bmah_diag; DEALLOCATE PREPARE _bmah_diag;
