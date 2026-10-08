-- Unbound Wrath Edition: the Mentor (creature 900001) in every capital and in Dalaran.
-- Applied to: acore_world
--
-- One Mentor stands beside each capital's bank (Stormwind, Ironforge, Darnassus, Exodar,
-- Orgrimmar, Undercity, Thunder Bluff, Silvermoon) and in Dalaran's Runeweaver Square, so a
-- player does not have to find a GM or use the Mentor Stone to meet it. The Mentor Stone
-- (07_mentor_stone.sql) still summons the Mentor anywhere.
--
-- Spawn ids 9000101-9000109 only. The core numbers a new spawn MAX(guid)+1, so the next
-- `.npc add` after this file gets 9000110 or higher: no later Unbound SQL file may ship
-- spawn ids above 9000109 (INSERT IGNORE would skip them without a word). AzerothCore
-- refuses spawn ids at or above 16777215. Each position is 4.5 yards in front of a banker
-- spawn of the core's own world database, on the banker's floor, facing the way the banker
-- faces (towards the walkway). The anchor banker's guid is in each row's Comment. Thunder
-- Bluff's bankers face each other across the bank, so its Mentor stands in the middle.
--
-- AzerothCore's update 2026_06_16_00 renamed the creature table's first entry column to `id`
-- and dropped the other two, so this file names only `id`.
--
-- Safe to re-run (AzerothCore applies a module file again when its bytes change): the DELETE
-- removes only our own ids that hold the Mentor, so a GM's own `.npc add 900001` placements
-- (fresh ids above this block) and a foreign row that sits on one of our ids are left alone.
-- If a foreign row does sit on one of the ids, INSERT IGNORE skips that Mentor and the
-- installer's count of creature id 900001 reports the shortfall.

DELETE FROM `creature` WHERE `guid` BETWEEN 9000101 AND 9000109 AND `id` = 900001;

INSERT IGNORE INTO `creature`
    (`guid`, `id`, `map`, `zoneId`, `areaId`, `spawnMask`, `phaseMask`, `equipment_id`,
     `position_x`, `position_y`, `position_z`, `orientation`,
     `spawntimesecs`, `wander_distance`, `currentwaypoint`, `curhealth`, `curmana`,
     `MovementType`, `npcflag`, `unit_flags`, `dynamicflags`,
     `ScriptName`, `VerifiedBuild`, `CreateObject`, `Comment`)
VALUES
    (9000101, 900001, 0, 0, 0, 1, 1, 0, -8931.23, 615.11, 99.61, 0.454, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Stormwind, Trade District bank, beside banker guid 79678'),
    (9000102, 900001, 0, 0, 0, 1, 1, 0, -4889.32, -994.04, 504.02, 2.234, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Ironforge, The Commons bank, beside banker guid 1754'),
    (9000103, 900001, 1, 0, 0, 1, 1, 0, 9940.34, 2515.07, 1317.66, 4.328, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Darnassus, Tradesmen''s Terrace bank, beside banker guid 46418'),
    (9000104, 900001, 530, 0, 0, 1, 1, 0, -3919.51, -11549.17, -150.04, 4.588, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Exodar, bank, beside banker guid 82943'),
    (9000105, 900001, 1, 0, 0, 1, 1, 0, 1623.02, -4377.38, 12.06, 3.438, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Orgrimmar, Valley of Strength bank, beside banker guid 6598'),
    (9000106, 900001, 0, 0, 0, 1, 1, 0, 1604.44, 241.04, -52.06, 0.087, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Undercity, Trade Quarter bank, beside banker guid 31858'),
    (9000107, 900001, 1, 0, 0, 1, 1, 0, -1257.32, 26.0, 128.27, 1.571, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Thunder Bluff, bank, between bankers guid 26560/26562/26616'),
    (9000108, 900001, 530, 0, 0, 1, 1, 0, 9804.04, -7488.25, 13.64, 3.159, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Silvermoon, Bazaar bank, beside banker guid 57612'),
    (9000109, 900001, 571, 0, 0, 1, 1, 0, 5628.15, 693.69, 652.68, 5.934, 300, 0, 0, 1, 0, 0, 0, 0, 0, '', NULL, 0, 'Unbound: The Mentor, Dalaran, Runeweaver Square bank, beside banker guid 108853');
