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
   `speed_walk`, `speed_run`, `scale`, `rank`,
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
   1.0, 1.14286, 1.0, 0,
   0, 1.0,
   2000, 2000,
   1.0, 1.0,
   1, 33536, 2048, 0,
   7, '', 0, 1.0,
   1.0, 1.0, 1.0,
   1, 2, 0);

-- ── 2. Display model — schema-adaptive ───────────────────────────────────────────
-- Newer AC (no modelid columns): insert into creature_template_model.
-- Older AC (has modelid1): update modelid1 directly.
-- Uses MySQL dynamic SQL (PREPARE/EXECUTE) so neither branch errors out.

SET @hasModelTable = (
  SELECT COUNT(*) FROM information_schema.TABLES
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'creature_template_model'
);
SET @sql = IF(@hasModelTable > 0,
  'INSERT IGNORE INTO creature_template_model (CreatureID, Idx, CreatureDisplayID, DisplayScale, Probability, VerifiedBuild) VALUES (2069430, 0, 6557, 1.0, 1.0, 0)',
  'SELECT ''Skipping creature_template_model — not present in this AC build'' AS note'
);
PREPARE _bmah_stmt FROM @sql; EXECUTE _bmah_stmt; DEALLOCATE PREPARE _bmah_stmt;

SET @hasModelid1 = (
  SELECT COUNT(*) FROM information_schema.COLUMNS
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'creature_template' AND COLUMN_NAME = 'modelid1'
);
SET @sql = IF(@hasModelid1 > 0,
  'UPDATE creature_template SET modelid1 = 6557 WHERE entry = 2069430',
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
