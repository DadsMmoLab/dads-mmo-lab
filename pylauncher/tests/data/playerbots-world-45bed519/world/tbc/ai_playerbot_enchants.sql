DROP TABLE IF EXISTS `ai_playerbot_enchants`;

CREATE TABLE `ai_playerbot_enchants` (
  `class` tinyint(2) NOT NULL,
  `spec` tinyint(2) NOT NULL,
  `spellid` bigint(6) NOT NULL,
  `slotid` tinyint(2) DEFAULT 1,
  `name` varchar(255) NOT NULL COMMENT 'name of the enchant',
  PRIMARY KEY (`class`,`spec`,`spellid`,`slotid`)
) ENGINE=InnoDB DEFAULT CHARSET=latin1;


INSERT INTO `ai_playerbot_enchants` (`class`, `spec`, `spellid`, `slotid`, `name`) VALUES
-- Arms Warrior (Spec ID: 10)
(1, 10, 35452, 0, 'Head: Glyph of Ferocity');
