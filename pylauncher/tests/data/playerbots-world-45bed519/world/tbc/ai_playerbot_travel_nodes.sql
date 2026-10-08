-- --------------------------------------------------------
-- Host:                         127.0.0.1
-- Server version:               5.7.26 - MySQL Community Server (GPL)
-- Server OS:                    Win32
-- HeidiSQL Version:             12.2.0.6576
-- --------------------------------------------------------

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8 */;
/*!50503 SET NAMES utf8mb4 */;
/*!40103 SET @OLD_TIME_ZONE=@@TIME_ZONE */;
/*!40103 SET TIME_ZONE='+00:00' */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;
/*!40111 SET @OLD_SQL_NOTES=@@SQL_NOTES, SQL_NOTES=0 */;

-- Dumping structure for table tbcmangos.ai_playerbot_travelnode
DROP TABLE IF EXISTS `ai_playerbot_travelnode`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_travelnode` (
  `id` mediumint(8) NOT NULL,
  `name` varchar(1024) NOT NULL,
  `map_id` mediumint(8) NOT NULL,
  `x` float NOT NULL,
  `y` float NOT NULL,
  `z` float NOT NULL,
  `linked` tinyint(2) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 ROW_FORMAT=FIXED COMMENT='PlayerbotAI Travel Node';

-- Dumping data for table tbcmangos.ai_playerbot_travelnode: 2.887 rows
/*!40000 ALTER TABLE `ai_playerbot_travelnode` DISABLE KEYS */;
INSERT INTO `ai_playerbot_travelnode` (`id`, `name`, `map_id`, `x`, `y`, `z`, `linked`) VALUES
	(0, 'Aerie Peak flightMaster', 0, 282.096, -2001.28, 194.127, 1);
/*!40000 ALTER TABLE `ai_playerbot_travelnode` ENABLE KEYS */;

-- Dumping structure for table tbcmangos.ai_playerbot_travelnode_link
DROP TABLE IF EXISTS `ai_playerbot_travelnode_link`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_travelnode_link` (
  `node_id` mediumint(8) NOT NULL,
  `to_node_id` mediumint(8) NOT NULL,
  `type` tinyint(3) NOT NULL,
  `object` mediumint(8) NOT NULL,
  `distance` float NOT NULL,
  `swim_distance` float NOT NULL,
  `extra_cost` float NOT NULL,
  `calculated` tinyint(1) NOT NULL,
  `max_creature_0` tinyint(2) NOT NULL,
  `max_creature_1` tinyint(2) NOT NULL,
  `max_creature_2` tinyint(2) NOT NULL,
  PRIMARY KEY (`node_id`,`to_node_id`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 ROW_FORMAT=FIXED COMMENT='PlayerbotAI Travel Node link';

-- Dumping data for table tbcmangos.ai_playerbot_travelnode_link: 9.556 rows
/*!40000 ALTER TABLE `ai_playerbot_travelnode_link` DISABLE KEYS */;
INSERT INTO `ai_playerbot_travelnode_link` (`node_id`, `to_node_id`, `type`, `object`, `distance`, `swim_distance`, `extra_cost`, `calculated`, `max_creature_0`, `max_creature_1`, `max_creature_2`) VALUES
	(0, 533, 4, 475, 0.1, 0, 0.441716, 1, 0, 0, 0);
/*!40000 ALTER TABLE `ai_playerbot_travelnode_link` ENABLE KEYS */;

-- Dumping structure for table tbcmangos.ai_playerbot_travelnode_path
DROP TABLE IF EXISTS `ai_playerbot_travelnode_path`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_travelnode_path` (
  `node_id` mediumint(8) NOT NULL,
  `to_node_id` mediumint(8) NOT NULL,
  `nr` mediumint(8) NOT NULL,
  `map_id` mediumint(8) NOT NULL,
  `x` float NOT NULL,
  `y` float NOT NULL,
  `z` float NOT NULL,
  PRIMARY KEY (`node_id`,`to_node_id`,`nr`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 ROW_FORMAT=FIXED COMMENT='PlayerbotAI Travel Node path';

-- Dumping data for table tbcmangos.ai_playerbot_travelnode_path: 627.446 rows
/*!40000 ALTER TABLE `ai_playerbot_travelnode_path` DISABLE KEYS */;
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(0, 533, 0, 0, 283.479, -2002.17, 195.21);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(57, 73, 8, 30, 818.886, -491.673, 101.125);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(123, 122, 97, 0, -870.929, -2169.04, 50.8914);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(160, 180, 4, 1, 2667.04, -2269.45, 200.905);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(183, 159, 64, 1, 2440.3, -602.215, 115.289);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(218, 234, 229, 1, 4042.7, -5366.67, 118.957);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(239, 220, 25, 1, 2725.7, -5024.19, 124.301);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(271, 1997, 49, 530, -4063.38, -11506.2, -18.2086);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(314, 2210, 46, 564, 298.228, 812.79, -22.4168);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(373, 896, 11, 530, 2960.68, 4910.84, 266.89);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(413, 411, 92, 0, -11335.5, -2648.27, 71.8761);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(454, 2666, 50, 530, -1884.8, -11221.3, 59.8694);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(495, 494, 23, 0, -7707.39, -2854.16, 135.288);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(552, 2682, 25, 545, -92.3973, -453.59, 7.9428);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(620, 624, 81, 0, -10828.6, -2168.04, 122.746);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(654, 1665, 28, 1, 114.735, 1732.98, 90.6836);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(719, 721, 114, 0, -5646.68, 485.087, 387.63);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(755, 764, 54, 1, 930.441, -4319.8, 25.2774);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(787, 796, 55, 1, -2892.46, -3324.4, 33.8354);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(821, 835, 90, 0, 2662.68, -5273.97, 143.442);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(865, 870, 9, 0, -9886.87, 654.228, 38.2856);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(911, 910, 82, 530, 8666.82, -5982.19, 40.602);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(941, 917, 21, 530, 8687.12, -6538.19, 75.6129);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(983, 993, 6, 1, -4384.46, 3271.43, 14.2846);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1012, 847, 8, 469, -7407.46, -973.352, 471.589);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1050, 1073, 19, 530, 7900.08, -7834.65, 171.041);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1074, 1054, 70, 530, 7557.44, -7711.55, 152.184);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1152, 1319, 49, 109, -514.675, -88.6297, -90.3514);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1209, 1186, 13, 530, 35.2287, 4316.31, 91.7902);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1259, 1254, 70, 0, -279.382, -407.215, 68.3462);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1312, 1311, 49, 530, 12729, -7022.64, 21.134);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1379, 1380, 10, 0, -5721.53, -3909.95, 322.834);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1441, 2075, 36, 349, 709.422, -359.479, -51.3522);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1545, 786, 8, 1, -4622.2, -3095.07, 37.5534);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1580, 2262, 13, 530, -2942.82, 6489.2, 84.7201);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1603, 2807, 106, 530, -123.626, 8751.27, 19.7659);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1643, 1628, 59, 530, 4476.36, 2448.93, 114.166);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1702, 751, 28, 1, 1294.13, -4579.47, 22.7065);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1755, 754, 49, 1, 230.42, -4410.47, 30.0327);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1803, 167, 57, 1, 3336.72, -4044.7, 38.3733);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1900, 2353, 120, 530, -2089.94, 4748.26, -2.68294);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1958, 1305, 17, 530, -2791.48, 2210.37, 93.873);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2003, 1100, 9, 530, 9982.81, -7075.78, 46.1561);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2047, 2038, 61, 0, -383.792, 1127.07, 84.2636);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2084, 2864, 110, 0, -10437.4, -3826.46, 18.584);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2132, 2155, 29, 0, -14428.2, 715.614, 0.540344);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2197, 2098, 86, 1, 707.89, 1379.81, 9.27556);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2234, 2241, 59, 1, -8124.35, -4006.2, 12.6339);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2250, 2239, 97, 1, -7052.32, -4594.58, 9.33235);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2292, 1538, 69, 564, 907.722, 142.693, 193.486);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2348, 207, 103, 530, -3497.62, 4936.97, -98.4261);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2375, 16, 7, 530, -3379.49, 4446.89, -10.9527);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2404, 339, 71, 1, -4167.85, -2017.52, 93.883);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2439, 2131, 91, 0, -14465.3, -149.939, 1.90701);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2488, 2021, 138, 1, -7368.79, 1710.36, -7.24599);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2529, 2534, 112, 1, -5286.04, -2000.77, -55.8524);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2574, 2570, 32, 0, 2734.54, 1428.4, 0.504318);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2637, 2638, 3, 0, 1587.93, 238.816, 60.7918);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2705, 2709, 35, 0, 2034.61, -2253.8, 66.7268);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2739, 2733, 57, 0, -3582.2, -3241.15, 23.9531);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2766, 2769, 113, 1, 7070.67, -3977.6, 746.162);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2795, 2809, 72, 530, -959.998, 5417.94, 23.6102);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(2838, 2850, 57, 309, -11605.4, -1569.16, 40.5695);
/*!40000 ALTER TABLE `ai_playerbot_travelnode_path` ENABLE KEYS */;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
