-- Fix: lower tier-1 spell req_level from 8 → 1 so newly unlocked classes
-- have buyable abilities immediately (first milestone unlocks at level 5).
-- All classes had min req_level=8 from Playerbots trainer data, causing a
-- level 5-7 player to see "no abilities available" after unlocking a class.
UPDATE `unbound_class_catalog` SET `req_level` = 1 WHERE `req_level` <= 8;
