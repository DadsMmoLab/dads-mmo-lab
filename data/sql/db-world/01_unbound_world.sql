-- Unbound Wrath Edition — world DB schema
-- Run against: acore_world
-- Safe to re-run: all tables use CREATE TABLE IF NOT EXISTS; INSERTs use IGNORE / ON DUPLICATE KEY.

-- ============================================================
-- Milestone ladder (how many gold each class unlock costs)
-- ============================================================
CREATE TABLE IF NOT EXISTS `unbound_milestones` (
    `milestone_index`    TINYINT UNSIGNED NOT NULL,
    `required_level`     TINYINT UNSIGNED NOT NULL,
    `unlock_cost_copper` INT UNSIGNED     NOT NULL,
    PRIMARY KEY (`milestone_index`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO `unbound_milestones` (`milestone_index`, `required_level`, `unlock_cost_copper`) VALUES
(1,  5,        0),       -- 1st class: free at level 5
(2,  25,   30000),       -- 2nd class: 3g at level 25
(3,  50,  800000),       -- 3rd class: 80g at level 50
(4,  70, 3000000),       -- 4th class: 300g at level 70
(5,  80,15000000);       -- 5th+ class: 1500g each at level 80 (index 5 is reused for all subsequent unlocks)

-- ============================================================
-- Purchasable spell catalog, populated from Playerbots trainer
-- data (npc_trainer IDs 200002–200018).
--
-- class_id follows WoW class constants:
--   1=Warrior  2=Paladin  3=Hunter   4=Rogue   5=Priest
--   7=Shaman   8=Mage     9=Warlock  11=Druid
-- ============================================================
CREATE TABLE IF NOT EXISTS `unbound_class_catalog` (
    `class_id`         TINYINT UNSIGNED NOT NULL,
    `spell_id`         INT UNSIGNED     NOT NULL,
    `gold_cost_copper` INT UNSIGNED     NOT NULL DEFAULT 0,
    `req_level`        TINYINT UNSIGNED NOT NULL DEFAULT 1,
    PRIMARY KEY (`class_id`, `spell_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Populate from Playerbots synthetic trainer templates.
-- These are the same spells WotLK class trainers teach;
-- Playerbots loaded them into npc_trainer so bots can learn them.
-- Trainer template → class ID mapping (verified against creature subnames):
--   200002 = Warrior  200004 = Paladin  200006 = Druid
--   200008 = Mage     200010 = Warlock  200012 = Priest
--   200014 = Hunter   200016 = Rogue    200018 = Shaman
INSERT INTO `unbound_class_catalog` (`class_id`, `spell_id`, `gold_cost_copper`, `req_level`)
SELECT
    CASE `nt`.`ID`
        WHEN 200002 THEN 1
        WHEN 200004 THEN 2
        WHEN 200006 THEN 11
        WHEN 200008 THEN 8
        WHEN 200010 THEN 9
        WHEN 200012 THEN 5
        WHEN 200014 THEN 3
        WHEN 200016 THEN 4
        WHEN 200018 THEN 7
    END                         AS `class_id`,
    `nt`.`SpellID`              AS `spell_id`,
    `nt`.`MoneyCost`            AS `gold_cost_copper`,
    `nt`.`ReqLevel`             AS `req_level`
FROM `npc_trainer` `nt`
WHERE `nt`.`ID` IN (200002, 200004, 200006, 200008, 200010, 200012, 200014, 200016, 200018)
  AND `nt`.`SpellID` > 0
ON DUPLICATE KEY UPDATE
    `gold_cost_copper` = VALUES(`gold_cost_copper`),
    `req_level`        = VALUES(`req_level`);

-- ============================================================
-- Mentor NPC creature_template + model:
-- NOT applied here to avoid touching vanilla tables in the
-- auto-update path.  Run npc_setup.sql manually once, or use:
--   .npc add 900001   (after running npc_setup.sql)
-- ============================================================
