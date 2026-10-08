-- Unbound Wrath Edition — characters DB schema
-- Run against: acore_characters
-- Safe to re-run: CREATE TABLE IF NOT EXISTS.

-- ============================================================
-- Per-character class unlock records.
-- One row per (player, class) pair. Never deleted — additive only.
-- ============================================================
CREATE TABLE IF NOT EXISTS `unbound_character_unlocks` (
    `char_guid`        INT UNSIGNED     NOT NULL,
    `class_id`         TINYINT UNSIGNED NOT NULL,
    `unlocked_at_level` TINYINT UNSIGNED NOT NULL,
    `unlocked_ts`      TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (`char_guid`, `class_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
