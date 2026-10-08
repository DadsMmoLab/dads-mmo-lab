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

-- Dumping structure for table classicmangos.ai_playerbot_named_location
DROP TABLE IF EXISTS `ai_playerbot_named_location`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_named_location` (
  `name` char(128) NOT NULL,
  `map_id` smallint(5) NOT NULL,
  `position_x` decimal(40,20) NOT NULL,
  `position_y` decimal(40,20) NOT NULL,
  `position_z` decimal(40,20) NOT NULL,
  `orientation` decimal(40,20) NOT NULL,
  `description` varchar(255) NOT NULL,
  PRIMARY KEY (`name`)
) ENGINE=MyISAM DEFAULT CHARSET=utf8 ROW_FORMAT=FIXED COMMENT='PlayerbotAI Named Location';

-- Dumping data for table classicmangos.ai_playerbot_named_location: 54.055 rows
/*!40000 ALTER TABLE `ai_playerbot_named_location` DISABLE KEYS */;
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('AV_DUNBALDAR_NORTH', 30, 674.00061035156250000000, -143.12506103515625000000, 63.66151428222656250000, 0.99483770132064819300, 'AV - Dunbaldar North');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_0_140_47519', 0, -12199.47558600000000000000, -359.98629800000000000000, 10.18175000000000000000, 1.62316400000000000000, '140');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_0_344_41093', 0, -3306.88501000000000000000, -1035.41235400000000000000, 8.10471800000000000000, 2.93215500000000000000, '344');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_0_692_34682', 0, -13793.33300800000000000000, 691.66662600000000000000, 0.05845500000000000000, 0.00000000000000000000, '692');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_128_28242', 1, -3546.57983400000000000000, -2751.06372100000000000000, 31.68704600000000000000, 1.91985700000000000000, '128');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_422_21818', 1, 3926.00219700000000000000, -6279.75000000000000000000, 3.54137500000000000000, 3.50809900000000000000, '422');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_517_15364', 1, 7518.91601600000000000000, -2468.07690400000000000000, 453.26239000000000000000, 1.79769000000000000000, '517');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_611_8958', 1, 2943.40307600000000000000, -2709.84814500000000000000, 213.75904800000000000000, 1.74533700000000000000, '611');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_875_2513', 1, -4265.52636700000000000000, -4718.05322300000000000000, 0.52861300000000000000, 5.51522400000000000000, '875');
/*!40000 ALTER TABLE `ai_playerbot_named_location` ENABLE KEYS */;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
