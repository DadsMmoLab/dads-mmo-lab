-- dml_autobuff.lua keeps each player's #buffs choices here. The script creates
-- this table too, but it only does so when the Unbound.AutoBuff switch is on;
-- creating it here means it always exists, so the character-delete clean-up can
-- name it whether or not the script ran.
CREATE TABLE IF NOT EXISTS `dml_autobuff_kv` (
  `guid` INT UNSIGNED NOT NULL,
  `k`    VARCHAR(32)  NOT NULL,
  `v`    VARCHAR(16)  NOT NULL,
  PRIMARY KEY (`guid`, `k`)
);
