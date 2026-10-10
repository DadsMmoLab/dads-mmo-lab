-- -------------------------------
-- CMaNGOS custom DB updates
-- -------------------------------

-- This file holds SQL updates to DB contents that are known to be different from official/sniff data
-- These updates are applied to ensure expected functionning from a gameplay point of view
-- (aka "Blizzlike") when using CMaNGOS core.
--
-- These updates SHOULD NOT be part of a full DB dump (like for DB release) or inserted into a regular 
-- SQL update file as we need to keep DB data as "pure" as possible to analyse them and
-- deduce proper core behaviour from them.
-- 
-- Possible but non-exhaustive reasons for these updates to be needed:
--	- CMaNGOS core does not properly handle the data these updates are fixing (yet)
--	- Official/sniff data are from a different expansion/patch and were possibly changed since Classic
--	- Official/sniff data from client are different from what they are server-side
--	- Data or their parsing were wrong


-- ============================================================
-- Classic section
-- ============================================================

-- -------------------------------
-- Gameobject custom changes
-- -------------------------------

-- Make Moonwell GO server-side (visible by GM only)
UPDATE gameobject_template SET data3=1 WHERE entry IN (177272, 177273, 177274, 177275, 177276, 177277, 177278, 177279, 177280, 177281);

-- Witherbark Totem Bundle 174764 - only visible by GM
UPDATE gameobject_template SET data3=1 WHERE entry=174764;

-- Make Incantion of Celebras Trap only visible by GM
UPDATE gameobject_template SET data8=1 WHERE entry=178963;

-- Make some trap GO only visible by GM
UPDATE gameobject_template SET data8=1 WHERE entry=103575; -- Containment Coffer TRAP
UPDATE gameobject_template SET data8=1 WHERE entry=128972; -- Shallow Grave TRAP
UPDATE gameobject_template SET data8=1 WHERE entry=176592; -- Shellfish Trap
UPDATE gameobject_template SET data8=1 WHERE entry=176750; -- Kodo Bones
UPDATE gameobject_template SET data8=1 WHERE entry=177493; -- Fire of Elune (Trap
UPDATE gameobject_template SET data8=1 WHERE entry=177529; -- Altar of Elune (Trap
UPDATE gameobject_template SET data8=1 WHERE entry=178124; -- Resonite Crystal (Trap
UPDATE gameobject_template SET data8=1 WHERE entry=178248; -- Naga Brazier (trap
UPDATE gameobject_template SET data8=1 WHERE entry=171941; -- Blackrock Keg Trap
UPDATE gameobject_template SET data8=1 WHERE entry=180391; -- Heart of Hakkar Spell Emitter
UPDATE gameobject_template SET data8=1 WHERE entry=181214; -- Necropolis critter spawner
UPDATE gameobject_template SET data8=1 WHERE entry=179324; -- Frostwolf Landmine
UPDATE gameobject_template SET data8=1 WHERE entry=179325; -- Stormpike Landmine
UPDATE gameobject_template SET data8=1 WHERE entry=160842; -- Gor'tesh's Lopped Off Head

-- Make Supply Crate trap GO only visible by GM
 UPDATE gameobject_template SET data8=1 WHERE entry IN (175534, 175535, 175536, 175537);

-- Make Dawn's Gambit Trap only visible by GM
UPDATE gameobject_template SET data2=5, data3=0, data8=0 WHERE entry=176110;
-- Dawn's Gambit: add data from GO trap 176110 until we are able to trigger trap GOs without spells
UPDATE gameobject_template SET data2=5, data3=18110, data4=1, data8=1 WHERE entry=177304;

-- The Demon Seed GO: make it despawnable on use with short autoclose value
UPDATE gameobject_template SET data3=65536, data5=1 WHERE entry=3524;

-- Add missing GO template 175622 spawn by GO Rookery Egg 175124 (guessed data)
DELETE FROM gameobject_template WHERE entry=175622;
INSERT INTO `gameobject_template` (`entry`, `type`, `displayId`, `name`, `size`, `Data0`, `Data1`, `Data2`, `Data3`, `Data4`, `Data5`, `Data6`, `Data7`, `Data8`, `Data9`, `Data10`, `Data11`, `Data12`, `Data13`, `Data14`, `Data15`, `Data16`, `Data17`, `Data18`, `Data19`, `Data20`, `Data21`, `Data22`, `Data23`) VALUES
(175622, 6, 477, 'Rookery Egg Spawner', 1, 0, 0, 2, 15745, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0);

-- Set 10 yards radius to Night Elven Bear Trap GO because it needs to be triggered by SD2 script otherwise (and said script do not exist yet)
UPDATE gameobject_template SET data2=10 WHERE entry=109515;

-- Set a 70 yards diameter for Ragnaros GO because because it needs to be triggered by SD2 script otherwise (and said script do not exist yet)
UPDATE gameobject_template SET data2=70 WHERE entry=178088;

-- Set 20 yards radius to Suppression Device GO and cooldown because it needs to be triggered by SD2 script otherwise (and said script does not exist yet because GO spell casting in not implemented)
UPDATE gameobject_template SET data2=20 WHERE entry=179784;


-- Currently the core cannot make a GO usable for a specific quest if the items it holds are not objectives of the quest

-- Link Hive'Ashi Pod GO to quest 1126 so it is usable by players on the quest
UPDATE gameobject_template SET data8=1126 WHERE entry=178553;

-- Link Azsharite GOs to quest 3602 so they are usable by players on the quest
UPDATE gameobject_template SET data8=3602 WHERE entry IN (152620, 152621, 152622, 152631);

-- Link Crate with Holes, Snufflenose Command Stick and Snufflenose Owner's Manual GOs to quest 1221 so they are usable by players on the quest
UPDATE gameobject_template SET data8=1221 WHERE entry IN (68865, 21530, 21277);

-- Thaurissan Relic classic-db (and classic sniff) value 3702 - the quest which involves actually using this object is 3701
UPDATE gameobject_template SET data1=3701 WHERE entry=153556;

-- Make object 181444 (Kel'Thuzad Trigger) despawnable on usage
UPDATE gameobject_template SET data4=1 WHERE entry=181444;

-- Ritual Candle Aura
UPDATE gameobject_template SET `data8`=1 WHERE entry=179688; -- add serverside attribute so that it's not visible to players
UPDATE gameobject_template SET `faction`=1375 WHERE entry=179688; -- "Treasure" faction 1375 here is guessed based on when patch 1.4 was released, and the fact that it's hostile to Demon faction 90

-- Make PX-238 Winter Wondervolt TRAP GO server-side (visible by GM only)
UPDATE gameobject_template SET `data8`=1 WHERE entry=180797;

-- Andorhal Tower
-- SpellFocus radius reduced to 5 (sync with eff1 from spell:17016 that activates target there = 5y)
UPDATE gameobject_template SET data1 = 5 WHERE entry BETWEEN 176094 AND 176097;

-- Musty Tome 176150/176151
-- must despawn after used (has that flag set up.. but with restock timer it will never happen)
UPDATE gameobject_template SET data2=0 WHERE entry IN (176150,176151);

-- Cannonball: set radius to zero so it is only triggered through spell
UPDATE gameobject_template SET data2=0 WHERE entry=176211;

-- Verigan's Fist: must despawn during scripted quest
UPDATE gameobject_template SET `data3`=0, `data5`=1 WHERE entry=102413;

-- Temporary fix data6 for GO 178559 (Larva Spewer) in prevision of coming core update
UPDATE gameobject_template SET data6=0 WHERE entry=178559;

-- Inconspicuous Landmark 142189 - consumable, despawn on s.11462 expire
UPDATE `gameobject_template` SET `data5` = 1 WHERE `entry` = 142189; -- 19660800 / 65536 = 300sec

-- hack - this bypasses despawn prevention due to GO casting a hidden 6 second spell the GO should cast - note will be put down even in core
UPDATE gameobject_template SET data3=65536*6 WHERE entry IN(180619);

-- Plague Fissure: use wotlk sniff data for Eruption s.29371 spell dmg - might require go script for classic and tbc
UPDATE gameobject_template SET `data3` = 29371 WHERE entry IN (181510,181511,181512,181513,181514,181515,181516,181517,181518,181519,181520,181521,181522,181523,181524,181525,181526,181527,181528,181529,181530,181531,181532,181533,181534,181535,181536,181537,181538,181539,181540,181541,181542,181543,181544,181545,181546,181547,181548,181549,181550,181551,181552,181676,181677,181678,181695);

-- -------------------------------
-- Item custom changes
-- -------------------------------

-- Mana Agate should have class=0 and subclass=3 like all other mana gems
UPDATE item_template SET class=0, subclass=3 WHERE entry=5514;

-- -------------------------------
-- Creature custom changes
-- -------------------------------

-- Fix a few model IDs found in sniffs from patch 1.13 but not present in patch 1.12 DBCs
UPDATE creature_template SET DisplayId2=0 WHERE Entry IN (3651, 16979); -- [UNUSED] Kolkar Observer ; Midsummer Merchant
UPDATE creature_template SET DisplayId3=13132, DisplayId4=0 WHERE Entry=11446;  -- Gordok Spirit

-- -------------------------------
-- Quest custom changes
-- -------------------------------

-- None
