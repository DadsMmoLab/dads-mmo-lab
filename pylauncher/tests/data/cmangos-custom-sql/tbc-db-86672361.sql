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
-- CMaNGOS core does not properly handle the data these updates are fixing (yet)
-- Official/sniff data are from a different expansion/patch and were possibly changed since Classic
-- Official/sniff data from client are different from what they are server-side
-- Data or their parsing were wrong


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

-- ============================================================
-- TBC section
-- ============================================================

-- -------------------------------
-- Gameobject custom changes
-- -------------------------------

-- Temporary fix type as it is handled differently in tbc (Larva Spewer)
UPDATE gameobject_template SET type=10,faction=0,data3 =300*65536 WHERE entry=178559;

-- Hellfire Hot Spot Spreader
UPDATE gameobject_template SET data8=1 WHERE entry=183929; -- set serverside so that it's not visible to players
-- Sealed Tome traps
UPDATE gameobject_template SET data8=1 WHERE entry IN (181831,181838,181840,181842,181844);
-- Corki's Prison - make data match 182351
UPDATE gameobject_template SET data2=19660800 WHERE entry IN (182349,182350);
-- Consuming Flames Trap
UPDATE gameobject_template SET data3=0, data8=1 WHERE entry=178673; -- 21650, 0

-- Zul'Aman - Tanzar's Trunk - Is not subject to group loot per blizzlike data but all the other chests are. Confirmed data in sniff to be 0, however no such issues reported on retail. Confirmation that blizz sends unfixed GO template data???
UPDATE gameobject_template SET data15=1 WHERE entry IN(186648);

-- Make some trap GO only visible by GM
UPDATE gameobject_template SET data8=1 WHERE entry=184718; -- Cauldron Summoner
UPDATE gameobject_template SET data8=1 WHERE entry=184722; -- Cauldron Bug Summoner

-- Bogblossom 185497,185500 - consumable
UPDATE `gameobject_template` SET `data5` = 1 WHERE `entry` IN (185497,185500);

UPDATE gameobject_template SET data5=1 WHERE entry=184906; -- Power Converter (consumable)
UPDATE gameobject_template SET data8=1 WHERE entry=184910; -- Power Converter (serverside)

-- o.184958 'Nether Drake Egg'
-- trap makes it look like double spawn - removing its display fixes issue
UPDATE gameobject_template SET displayId = 0 WHERE entry = 184958;

-- -------------------------------
-- Item custom changes
-- -------------------------------

-- -----------------------------
-- Broadcast Text custom changes
-- -----------------------------

-- Hallow's End - Shade of the Horseman
-- these two texts were different before 3.0 changes were made to the event
-- the npc_text and broadcast_text data in tbc-db incorrectly had the 3.0 version
UPDATE `broadcast_text` SET `text1`='The Headless Horseman''s undead mind is obsessed!  During Hallow''s End, his attacks on this village are as rhythmic as his insane voice.  He will return in roughly $4676D.' WHERE `id`=22061; -- Alliance
UPDATE `broadcast_text` SET `text1`='The Headless Horseman''s mind is plagued with dementia!  During Hallow''s End, his attacks on this village are as rhythmic as his insane voice.$B$BHe will return in roughly $4676D.' WHERE `id`=22528; -- Horde
-- the first text of the jack-o-lantern was slightly different in TBC
UPDATE broadcast_text SET `text`="This large jack-o'-lantern rests in the middle of the village.  It eyes those who look up it, betraying a dark menace within." WHERE id=23650;

-- Sunwell Plateau Optional Progression Gating
-- NOTE: This is a custom modification to broadcast text
-- append onto ID 24247 since we have nothing else to go on (either this or make a custom ID)
-- source: https://warcraft.blizzplanet.com/blog/comments/wow_burning_crusade___patch_2_4___agamath_the_first_gate
UPDATE broadcast_text SET `text`="Should Kil'jaeden rise up through the Sunwell our world will be thrown into a war the likes of which has not been seen for 10,000 years!$B$BOur mages are $3253w percent through the defenses of the first gate, Agamath." WHERE id=24247;

-- q.10277 'The Caverns of Time' - Addition of "The Culling of Stratholme" Dungeon
UPDATE broadcast_text SET `Text1`="This timeway is currently collapsing. What that may hold for the past, present and future is currently unknown..." WHERE `Id` = 17669; -- This timeway collapsed and reformed. The result was Stratholme of yore. What could possibly be happening inside?

-- -------------------------------
-- Creature custom changes
-- -------------------------------

-- Shattered Halls Dummys add not_attackable and imun by npcs flags
-- Before: update creature_template set UnitFlags = '2304' where entry = 17578;
UPDATE creature_template SET UnitFlags = UnitFlags|640 WHERE entry = 17578;

-- mmap issues
UPDATE creature_template SET ExtraFlags = ExtraFlags|16384 WHERE `entry` IN (19608,21554); -- Frayer Wildling (Botanica)

-- https://github.com/cmangos/issues/wiki/CreatureStaticFlags
-- Blizzard (Shade of Aran) Entry: 17161 Counter: 5320734
UPDATE creature_template set StaticFlags1=StaticFlags1|0x02000000 where entry = 17161; -- IGNORE_COMBAT

-- -------------------------------

-- Displayid
UPDATE `creature_template` SET `DisplayIdProbability1` = 100, `DisplayIdProbability2` = 0 WHERE `entry` IN (
18095, -- Doomfire
18374, -- Focus Fire
20308, -- Focus Fire (1)
20978, -- Wrath-Scryer's Felfire
21625, -- Wrath-Scryer's Felfire (1)
25265, -- Demonic Vapor
25267, -- Demonic Vapor (Trail)
16995, -- Mouth of Kel'Thuzad
17459, -- Chess Waiting Room (DND)
18104, -- Doomfire Targeting
19028, -- The Overlook Capture Credit Marker
22356, -- [DND]Green Spot Grog Keg Credit
22383, -- [DND]Bloodmaul Chatter Credit
22434, -- [DND]Ogre Pike Planted Credit
22435, -- [DND]Rexxar's Wyvern Freed Credit
22798, -- [DND]Prophecy 1 Quest Credit
22799, -- [DND]Prophecy 2 Quest Credit
22800, -- [DND]Prophecy 3 Quest Credit
22801, -- [DND]Prophecy 4 Quest Credit
22925, -- Rain of Fire Bunny (Alliance)
25703); -- Brutallus Death Cloud

UPDATE `creature_template` SET `DisplayId2` = 17188, `DisplayIdProbability1` = 0, `DisplayIdProbability2` = 100 WHERE `entry` = 22332; -- Brood of Neltharaku

-- -------------------------------

UPDATE `creature_template` SET `name` = 'Redeemed Spirit of Earth' WHERE `entry` = 21739; -- Redeemed Spriit of Earth

-- -------------------------------
-- Quest custom changes
-- -------------------------------

-- quests fail upon death of a single person, but are meant to fail if whole group dies. TODO: Possibly change stay alive flag implementation for quests with QUEST_FLAGS_PARTY_ACCEPT
UPDATE `quest_template` SET `QuestFlags` = `QuestFlags`&~1 WHERE `entry` IN ( -- Remove QUEST_FLAGS_STAY_ALIVE
9962, -- The Ring of Blood: Brokentoe
9967, -- The Ring of Blood: The Blue Brothers
9970, -- The Ring of Blood: Rokdar the Sundered Lord
9972, -- The Ring of Blood: Skra'gath
9973, -- The Ring of Blood: The Warmaul Champion
9977); -- The Ring of Blood: The Final Challenge

-- -------------------------------
-- Spell custom changes
-- -------------------------------

-- s.14108 Venomhide Poison
-- was 4 (poison) - and source has immunity to it - so it prevents itself with applying aura - had to remove it.
UPDATE spell_template SET Dispel = 0 WHERE id = 14108;

-- Dance Vibe spell should not stack
UPDATE `spell_template` SET `StackAmount`='1' WHERE (`Id`='29521');


-- -------------------------------
-- CORE - remove after added in core
-- -------------------------------
-- spell FIX (unique - no stack)
UPDATE spell_template SET AttributesEx=AttributesEx|0x00000800 WHERE Id IN( -- SPELL_ATTR_EX_AURA_UNIQUE
-- Spell "Groups"
6533,12024,14030, -- "Net" Spells
13737,15708,16856,17547,19643,24573,29572,31911,32736,35054,37335,39171,40220,43441,43529,44268, -- "Mortal Strike" Spells
15971,32918, -- "Demoralizing" Spells
30931,32064,42247, -- "Battle Shout" Spells
5708,18144,23919,37012, -- "Swoop" Spells
30803,30804,30805,30806,30807, -- "Unleashed Rage" Spells
3589,7074,8281,23918,29904,39052,64140, -- Deafening Screech,Screams of the Past,Sonic Burst (AoE Silence)

-- Single Spells
6562, -- Heroic Presence (Racial Passive)
9612, -- Ink Spray
23060, -- Battle Squawk
34123, -- Tree of Life (Passive)
34410, -- Hellscream's Warsong
37695, -- Stanky
38775, -- Force of Neltharaku
40625 -- Apexis Emanations
);

