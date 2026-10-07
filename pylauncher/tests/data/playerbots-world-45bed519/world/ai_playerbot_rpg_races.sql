-- delete original GOSSIP_MENU_BOT
delete FROM gossip_menu_option where option_id = 99;
  
  DROP TABLE IF EXISTS `ai_playerbot_rpg_races`;
  
  CREATE TABLE `ai_playerbot_rpg_races` (
    `id` bigint(20) NOT NULL AUTO_INCREMENT,
    `entry` bigint(20),
    `race` bigint(20),
    `minl` bigint(20),
    `maxl` bigint(20),
    PRIMARY KEY (`id`),
    KEY `entry` (`entry`)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8;
  
  DELETE FROM `ai_playerbot_rpg_races`;
  
  -- say
  
  INSERT INTO `ai_playerbot_rpg_races` VALUES
  --
  --       DRAENEI
  --
  -- Draenei Azumeryst Isle
  (NULL, 16553, 11, 1, 10);
