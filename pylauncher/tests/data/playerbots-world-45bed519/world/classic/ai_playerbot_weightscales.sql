-- --------------------------------------------------------
-- Host:                         127.0.0.1
-- Server version:               5.7.26 - MySQL Community Server (GPL)
-- Server OS:                    Win32
-- HeidiSQL Version:             10.2.0.5599
-- --------------------------------------------------------

/*!40101 SET @OLD_CHARACTER_SET_CLIENT=@@CHARACTER_SET_CLIENT */;
/*!40101 SET NAMES utf8 */;
/*!50503 SET NAMES utf8mb4 */;
/*!40014 SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS, FOREIGN_KEY_CHECKS=0 */;
/*!40101 SET @OLD_SQL_MODE=@@SQL_MODE, SQL_MODE='NO_AUTO_VALUE_ON_ZERO' */;

-- Dumping structure for table classicplayerbots.ai_playerbot_weightscales
DROP TABLE IF EXISTS `ai_playerbot_weightscales`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_weightscales` (
  `id` int(32) NOT NULL AUTO_INCREMENT,
  `name` varchar(32) NOT NULL,
  `class` tinyint(3) unsigned NOT NULL DEFAULT '0',
  PRIMARY KEY (`id`)
) ENGINE=InnoDB AUTO_INCREMENT=33 DEFAULT CHARSET=utf8 ROW_FORMAT=COMPACT;

-- Dumping data for table classicplayerbots.ai_playerbot_weightscales: ~32 rows (approximately)
/*!40000 ALTER TABLE `ai_playerbot_weightscales` DISABLE KEYS */;
INSERT INTO `ai_playerbot_weightscales` (`id`, `name`, `class`) VALUES
	(1, 'arms', 1);
/*!40000 ALTER TABLE `ai_playerbot_weightscales` ENABLE KEYS */;

-- Dumping structure for table classicplayerbots.ai_playerbot_weightscale_data
DROP TABLE IF EXISTS `ai_playerbot_weightscale_data`;
CREATE TABLE IF NOT EXISTS `ai_playerbot_weightscale_data` (
  `id` int(32) NOT NULL,
  `field` varchar(18) NOT NULL,
  `val` smallint(6) unsigned NOT NULL,
  KEY `id` (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8;

-- Dumping data for table classicplayerbots.ai_playerbot_weightscale_data: ~272 rows (approximately)
/*!40000 ALTER TABLE `ai_playerbot_weightscale_data` DISABLE KEYS */;
INSERT INTO `ai_playerbot_weightscale_data` (`id`, `field`, `val`) VALUES
	(1, 'str', 4);
/*!40000 ALTER TABLE `ai_playerbot_weightscale_data` ENABLE KEYS */;

/*!40101 SET SQL_MODE=IFNULL(@OLD_SQL_MODE, '') */;
/*!40014 SET FOREIGN_KEY_CHECKS=IF(@OLD_FOREIGN_KEY_CHECKS IS NULL, 1, @OLD_FOREIGN_KEY_CHECKS) */;
/*!40101 SET CHARACTER_SET_CLIENT=@OLD_CHARACTER_SET_CLIENT */;
