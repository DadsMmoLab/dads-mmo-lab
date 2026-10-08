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

-- Dumping structure for table classicmangos.ai_playerbot_travelnode
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

-- Dumping data for table classicmangos.ai_playerbot_travelnode: 0 rows
/*!40000 ALTER TABLE `ai_playerbot_travelnode` DISABLE KEYS */;
INSERT INTO `ai_playerbot_travelnode` (`id`, `name`, `map_id`, `x`, `y`, `z`, `linked`) VALUES
	(0, 'Aerie Peak flightMaster', 0, 282.096, -2001.28, 194.127, 1);
/*!40000 ALTER TABLE `ai_playerbot_travelnode` ENABLE KEYS */;

-- Dumping structure for table classicmangos.ai_playerbot_travelnode_link
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

-- Dumping data for table classicmangos.ai_playerbot_travelnode_link: 6.190 rows
/*!40000 ALTER TABLE `ai_playerbot_travelnode_link` DISABLE KEYS */;
INSERT INTO `ai_playerbot_travelnode_link` (`node_id`, `to_node_id`, `type`, `object`, `distance`, `swim_distance`, `extra_cost`, `calculated`, `max_creature_0`, `max_creature_1`, `max_creature_2`) VALUES
	(0, 315, 4, 475, 0.1, 0, 0.441716, 1, 0, 0, 0);
/*!40000 ALTER TABLE `ai_playerbot_travelnode_link` ENABLE KEYS */;

-- Dumping structure for table classicmangos.ai_playerbot_travelnode_path
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

-- Dumping data for table classicmangos.ai_playerbot_travelnode_path: 414.126 rows
/*!40000 ALTER TABLE `ai_playerbot_travelnode_path` DISABLE KEYS */;
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(0, 315, 0, 0, 283.479, -2002.17, 195.21);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(54, 329, 28, 30, -1056.44, -362.883, 52.4847);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(117, 1473, 210, 531, -9165.93, 1575.95, -80.3229);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(142, 144, 16, 1, 2131.5, -3507.42, 58.0713);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(169, 189, 107, 1, 2773.34, -4074.68, 99.8033);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(192, 200, 29, 0, -6915.87, -3415.87, 243.879);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(237, 238, 2, 0, -7536.66, -1229.35, 286.5);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(280, 281, 4, 0, -7854.02, -2546.41, 131.461);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(334, 374, 14, 36, -79.3598, -943.425, 43.3241);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(383, 901, 25, 533, 2782.75, -3214.74, 286.341);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(415, 408, 171, 1, -1427.73, 1876.17, 64.0598);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(470, 473, 43, 0, -5617.13, -2200.27, 421.277);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(502, 500, 20, 1, 681.904, -4137.52, 18.6759);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(536, 1567, 75, 1, -3852, -4623.96, 9.81665);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(562, 559, 39, 0, 3082.39, -3415.16, 159.128);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(599, 1012, 17, 0, -8977, -256.642, 74.4009);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(640, 638, 31, 1, 4670.96, -640.096, 294.472);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(678, 805, 91, 230, 589.914, -140.536, -67.8314);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(743, 742, 5, 0, -919.749, -3499.65, 71.2202);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(795, 1020, 6, 429, -24.7767, -426.646, -58.3881);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(853, 1565, 36, 0, -5901.77, -2937.83, 366.303);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(904, 1735, 14, 0, -3857.14, -857.314, 7.97799);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(995, 989, 41, 1, -1890.49, -795.756, -6.12776);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1057, 1017, 32, 389, -225.789, -30.3889, -54.797);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1102, 1099, 1, 0, -9394.71, -2022.26, 59.0793);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1175, 1689, 72, 229, -26.3062, -477.182, 18.3096);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1240, 1244, 43, 1, -7251.85, 1024.06, 4.36881);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1271, 174, 287, 1, 2423.19, -6790.5, 133.863);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1310, 978, 25, 0, -8285.92, -2788.07, 212.284);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1352, 1348, 33, 0, -11808.6, -634.594, 32.6448);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1421, 1424, 96, 1, -8284.73, -3070.42, 9.99608);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1438, 692, 105, 1, -7233.31, -4225.55, 10.1693);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1472, 117, 0, 531, -8632.84, 2055.87, 108.86);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1508, 1496, 28, 1, -1834.01, -3167.96, 84.1563);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1531, 1499, 48, 1, -278.821, -2932.15, 119.279);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1567, 161, 25, 1, -2160.62, -2068.16, 123.965);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1604, 272, 21, 0, 2096.94, 482.945, 59.8424);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1643, 1435, 40, 1, -7664.69, -1981.79, -270.404);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1706, 570, 72, 0, 2591.63, -2478.72, 74.4641);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1733, 1734, 83, 0, -2839.79, -2237.34, 16.9984);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1761, 1764, 124, 1, 7114.97, -4010.31, 740.314);
INSERT INTO `ai_playerbot_travelnode_path` (`node_id`, `to_node_id`, `nr`, `map_id`, `x`, `y`, `z`) VALUES
	(1807, 752, 62, 309, -12048.4, -1737.04, 52.6545);
/*!40000 ALTER TABLE `ai_playerbot_travelnode_path` ENABLE KEYS */;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
