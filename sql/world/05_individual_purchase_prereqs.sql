-- Unbound Wrath Edition — individual spell purchase with rank prerequisites
-- Applied to: acore_world
--
-- 1. Add prereq_spell column to unbound_class_catalog
--    Populated from npc_trainer.ReqSpell (the prerequisite rank).
-- 2. Update shaman creation gifts: add missing starter totems.

-- ── 1. prereq_spell column ────────────────────────────────────────────────
-- MySQL 8 on this server doesn't support ADD COLUMN IF NOT EXISTS; use stored proc pattern
SET @col_exists = (
    SELECT COUNT(*) FROM information_schema.COLUMNS
    WHERE TABLE_SCHEMA = 'acore_world'
      AND TABLE_NAME   = 'unbound_class_catalog'
      AND COLUMN_NAME  = 'prereq_spell');
SET @sql = IF(@col_exists = 0,
    'ALTER TABLE unbound_class_catalog ADD COLUMN prereq_spell INT UNSIGNED NOT NULL DEFAULT 0',
    'SELECT ''prereq_spell column already exists''');
PREPARE stmt FROM @sql; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- Populate prereq_spell from npc_trainer.ReqSpell for each catalog entry.
-- Trainer ID → class mapping:
--   200002=Warrior 200004=Paladin 200006=Druid  200008=Mage    200010=Warlock
--   200012=Priest  200014=Hunter  200016=Rogue   200018=Shaman
UPDATE unbound_class_catalog uc
INNER JOIN npc_trainer nt
    ON nt.SpellID = uc.spell_id
    AND nt.ID IN (200002,200004,200006,200008,200010,200012,200014,200016,200018)
SET uc.prereq_spell = nt.ReqSpell
WHERE nt.ReqSpell > 0 AND uc.prereq_spell = 0;

-- ── 2. Shaman starter totems (missing from Playerbots trainer template) ───
-- Each element's basic rank-1 totem, gifted free at class unlock.
-- Already in playercreateinfo_spell_custom for classmask=64.
DELETE FROM playercreateinfo_spell_custom WHERE classmask = 64 AND racemask = 0;
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 64, 403,  'Shaman - Lightning Bolt r1'),
(0, 64, 331,  'Shaman - Healing Wave r1'),
(0, 64, 8071, 'Shaman - Stoneskin Totem r1 (Earth)'),
(0, 64, 8042, 'Shaman - Searing Totem r1 (Fire)'),
(0, 64, 5394, 'Shaman - Healing Stream Totem r1 (Water)'),
(0, 64, 8512, 'Shaman - Windfury Totem r1 (Air)'),
(0, 64, 2484, 'Shaman - Earthbind Totem');
