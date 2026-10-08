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

-- Dumping structure for table tbcmangos.ai_playerbot_named_location
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

-- Dumping data for table tbcmangos.ai_playerbot_named_location: 88.158 rows
/*!40000 ALTER TABLE `ai_playerbot_named_location` DISABLE KEYS */;
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('AV_DUNBALDAR_NORTH', 30, 674.00061035156250000000, -143.12506103515625000000, 63.66151428222656250000, 0.99483770132064819300, 'AV - Dunbaldar North');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_0_140_81698', 0, -11747.58007800000000000000, -306.96368400000000000000, 11.61539600000000000000, 4.46807000000000000000, '140');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_0_344_75272', 0, -3286.36718800000000000000, -1291.81384300000000000000, 7.41131800000000000000, 2.12931300000000000000, '344');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_0_692_68861', 0, -13667.80566400000000000000, 459.72006200000000000000, 25.42331100000000000000, 2.37364400000000000000, '692');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_128_62421', 1, -3556.76391600000000000000, -3040.27490200000000000000, 30.43737000000000000000, 3.28123200000000000000, '128');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_423_55995', 1, 3900.67285200000000000000, -5989.76123000000000000000, 0.66624000000000000000, 0.69812400000000000000, '423');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_522_49544', 1, -2807.15625000000000000000, -2930.24316400000000000000, 30.53469800000000000000, 3.45574600000000000000, '522');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_614_43145', 1, -3911.91699200000000000000, -3676.00219700000000000000, 34.16117100000000000000, 1.20426800000000000000, '614');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_1_891_36743', 1, 7811.97168000000000000000, -2482.83691400000000000000, 487.64691200000000000000, 5.20108600000000000000, '891');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_530_1143_30488', 530, -2399.82519500000000000000, 4198.33447300000000000000, -0.97147800000000000000, 1.58825300000000000000, '1143');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_530_1145_24263', 530, 243.11480700000000000000, 8185.41210900000000000000, 19.37647600000000000000, 3.35103100000000000000, '1145');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_530_1194_18080', 530, -4875.14160200000000000000, -12169.26367200000000000000, 0.58088400000000000000, 3.75243100000000000000, '1194');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_530_1277_11901', 530, 724.21374500000000000000, 7301.77246100000000000000, 20.05000900000000000000, 2.42601300000000000000, '1277');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_530_1528_5675', 530, 429.91311600000000000000, 6851.06347700000000000000, -13.54505300000000000000, 5.06145300000000000000, '1528');
INSERT INTO `ai_playerbot_named_location` (`name`, `map_id`, `position_x`, `position_y`, `position_z`, `orientation`, `description`) VALUES
	('FISH_LOCATION_530_1584_2629', 530, 8032.26904300000000000000, -6420.08691400000000000000, 55.30083500000000000000, 0.34908400000000000000, '1584');
/*!40000 ALTER TABLE `ai_playerbot_named_location` ENABLE KEYS */;

/*!40103 SET TIME_ZONE=IFNULL(@OLD_TIME_ZONE, 'system') */;
/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IFNULL(@OLD_FOREIGN_KEY_CHECKS, 1) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
/*!40111 SET SQL_NOTES=IFNULL(@OLD_SQL_NOTES, 1) */;
