DROP TABLE IF EXISTS `ai_playerbot_help_texts`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_help_texts` (
  `id` smallint(20) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL COMMENT 'name - type:subject',
  `template_changed` tinyint(4) NOT NULL DEFAULT '0' COMMENT 'template_changed - Has the template text changed after text?',
  `template_text` text NOT NULL COMMENT 'generated text',
  `text` text NOT NULL COMMENT 'text',
  `text_loc1` text NOT NULL,
  `text_loc2` text NOT NULL,
  `text_loc3` text NOT NULL,
  `text_loc4` text NOT NULL,
  `text_loc5` text NOT NULL,
  `text_loc6` text NOT NULL,
  `text_loc7` text NOT NULL,
  `text_loc8` text NOT NULL,
  `locs_updated` tinyint(1) NOT NULL DEFAULT '0' COMMENT 'locs_updated - Have the loc texts been updated?',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=2424 DEFAULT CHARSET=utf8 ROW_FORMAT=DYNAMIC;

INSERT INTO `ai_playerbot_help_texts` (`id`, `name`, `template_changed`, `template_text`, `text`, `text_loc1`, `text_loc2`, `text_loc3`, `text_loc4`, `text_loc5`, `text_loc6`, `text_loc7`, `text_loc8`, `locs_updated`) VALUES
	(1, 'help:main', 0, '----This is the main help page----\r\nPlease copy a link (shift click to chat in a whisper to a bot) to get more information about a topic.\r\nObjects: [h:object:strategy|strategies][h:object:trigger|triggers][h:object:action|actions][h:object:value|values][h:object:chatfilter|chatfilters]', '', '', '', '', '', '', '', '', '', 0);
INSERT INTO `ai_playerbot_help_texts` (`id`, `name`, `template_changed`, `template_text`, `text`, `text_loc1`, `text_loc2`, `text_loc3`, `text_loc4`, `text_loc5`, `text_loc6`, `text_loc7`, `text_loc8`, `locs_updated`) VALUES
	(1240, 'action:priest feedback', 1, 'feedback [h:object|action] [c:do feedback|execute]\r\n\r\nCombat behavior:\r\nTriggers from: [h:trigger:priest feedback|feedback] with relevance (20.000) for [h:strategy:priest buff shadow pve|buff shadow pve]\r\nTriggers from: [h:trigger:priest feedback|feedback] with relevance (20.000) for [h:strategy:priest buff shadow pvp|buff shadow pvp]\r\nTriggers from: [h:trigger:priest feedback|feedback] with relevance (20.000) for [h:strategy:priest buff shadow raid|buff shadow raid]', '', '', '', '', '', '', '', '', '', 0);
INSERT INTO `ai_playerbot_help_texts` (`id`, `name`, `template_changed`, `template_text`, `text`, `text_loc1`, `text_loc2`, `text_loc3`, `text_loc4`, `text_loc5`, `text_loc6`, `text_loc7`, `text_loc8`, `locs_updated`) VALUES
	(2295, 'trigger:hunter stealthed nearby', 1, 'stealthed nearby [h:object|trigger] [c:stealthed nearby|trigger now]\r\n\r\nCombat behavior:\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter marksmanship pve|marksmanship pve]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter beast mastery pve|beast mastery pve]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter beast mastery pvp|beast mastery pvp]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter beast mastery raid|beast mastery raid]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter marksmanship pvp|marksmanship pvp]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter marksmanship raid|marksmanship raid]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter survival pve|survival pve]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter survival pvp|survival pvp]\r\nExecutes: [h:action:hunter flare|flare] (21.000) for [h:strategy:hunter survival raid|survival raid]\r\nNon combat behavior:\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter marksmanship pve|marksmanship pve]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter beast mastery pve|beast mastery pve]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter beast mastery pvp|beast mastery pvp]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter beast mastery raid|beast mastery raid]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter marksmanship pvp|marksmanship pvp]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter marksmanship raid|marksmanship raid]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter survival pve|survival pve]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter survival pvp|survival pvp]\r\nExecutes: [h:action:hunter flare|flare] (13.000) for [h:strategy:hunter survival raid|survival raid]', '', '', '', '', '', '', '', '', '', 0);

DROP TABLE IF EXISTS `ai_playerbot_texts`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_texts` (
  `id` smallint(20) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL COMMENT 'name - used in strategies/code as filter',
  `text` varchar(1024) NOT NULL COMMENT 'text',
  `say_type` tinyint(3) NOT NULL DEFAULT '0' COMMENT '0 - say, 1 - yell',
  `reply_type` tinyint(3) NOT NULL DEFAULT '0' COMMENT 'if > 0 then can be filtered as a response to chat',
  `text_loc1` varchar(1024) NOT NULL DEFAULT '',
  `text_loc2` varchar(1024) NOT NULL DEFAULT '',
  `text_loc3` varchar(1024) NOT NULL DEFAULT '',
  `text_loc4` varchar(1024) NOT NULL DEFAULT '',
  `text_loc5` varchar(1024) NOT NULL DEFAULT '',
  `text_loc6` varchar(1024) NOT NULL DEFAULT '',
  `text_loc7` varchar(1024) NOT NULL DEFAULT '',
  `text_loc8` varchar(1024) NOT NULL DEFAULT '',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=1 DEFAULT CHARSET=utf8 ROW_FORMAT=DYNAMIC;

INSERT INTO `ai_playerbot_texts` (`name`, `text`, `say_type`, `reply_type`, `text_loc1`, `text_loc2`, `text_loc3`, `text_loc4`, `text_loc5`, `text_loc6`, `text_loc7`, `text_loc8`) VALUES

-- strings
('string_unknown_area', 'the middle of nowhere', 0, 0, '', '', '', '', '', '', '', '');

DROP TABLE IF EXISTS `ai_playerbot_texts_chance`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_texts_chance` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `name` varchar(255) NOT NULL,
  `probability` bigint(20) NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=4 DEFAULT CHARSET=utf8 ROW_FORMAT=DYNAMIC;

INSERT INTO `ai_playerbot_texts_chance` (`id`, `name`, `probability`) VALUES
	(1, 'taunt', 30);
	
/*
-- The newly added content features a wealth of Middle-earth-style dialogue, including:
--
-- 1. Taunts (taunt): Shakespearean insults, battle provocations, boss-style one-liners
-- 2. Banter (reply): Internet slang, gaming culture memes, casual conversations
-- 3. Suggestions (suggest_something): Zone exploration, grouping up, PvP, daily chatter
-- 4. Instances (suggest_instance): LFM/LFG, achievement runs, gear farming
-- 5. Loot (loot): Chest-opening excitement, RNG prayers, inventory management
-- 6. AoE Scenarios (aoe): Panic, battle frenzy, classic quotes
*/

INSERT INTO `ai_playerbot_texts` (`name`,`text`,`say_type`,`reply_type`) VALUES 

('taunt','I’m sorry, I didn’t realize <target> were an expert in everything.',0,0);
