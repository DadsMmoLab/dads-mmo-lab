-- ExchangeNpc_Up.sql
-- Dad's MMO Lab ALE-Pub / ExchangeNPC
-- Derived from 55Honey/Acore_ExchangeNpc (commit 6e99cfc)
--
-- Creates creature templates for the three Exchange NPCs and their gossip texts.
-- Apply to acore_world:
--   docker exec -i <db-container> mysql -u acore -pacore acore_world < ExchangeNpc_Up.sql
--
-- Note: The original upstream SQL also includes optional glyph vendor data (npc_vendor /
-- conditions rows for NPC 1116001 selling WotLK/TBC glyphs).  That data is NOT included
-- here to keep installs lean.  To enable glyph selling, run the full
-- ExchangeNpc_Up.sql from 55Honey/Acore_ExchangeNpc and then:
--   UPDATE `creature_template` SET `npcflag` = 129 WHERE `entry` = 1116001;

DELETE FROM `creature_template` WHERE `entry` IN (1116001, 1116002, 1116003);
INSERT INTO `creature_template`
  (`entry`,`difficulty_entry_1`,`difficulty_entry_2`,`difficulty_entry_3`,
   `KillCredit1`,`KillCredit2`,`modelid1`,`modelid2`,`modelid3`,`modelid4`,
   `name`,`subname`,`gossip_menu_id`,`minlevel`,`maxlevel`,`exp`,`faction`,
   `npcflag`,`speed_walk`,`speed_run`,`scale`,`rank`,`dmgschool`,`DamageModifier`,
   `BaseAttackTime`,`RangeAttackTime`,`BaseVariance`,`RangeVariance`,`unit_class`,
   `unit_flags`,`unit_flags2`,`dynamicflags`,`family`,`trainer_type`,`trainer_spell`,
   `trainer_class`,`trainer_race`,`type`,`type_flags`,`lootid`,`pickpocketloot`,
   `skinloot`,`PetSpellDataId`,`VehicleId`,`mingold`,`maxgold`,`AIName`,`MovementType`,
   `HoverHeight`,`HealthModifier`,`ManaModifier`,`ArmorModifier`,`RacialLeader`,
   `movementId`,`RegenHealth`,`mechanic_immune_mask`,`spell_school_immune_mask`,
   `flags_extra`,`ScriptName`,`VerifiedBuild`)
VALUES
(1116001,0,0,0,0,0,1097,0,0,0,'Roboto','Trusted Dealer',62001,63,63,0,35,1,1,1.14286,1,0,0,1,2000,2000,1,1,1,33536,2048,0,0,0,0,0,0,2,0,0,0,0,0,0,0,0,'',0,1,1.35,1,1,0,0,1,0,0,2,'',0),
(1116002,0,0,0,0,0,24207,0,0,0,'Shadow Priest Hacki','The Honor Melter',62001,63,63,0,35,1,1,1.14286,1,0,0,1,2000,2000,1,1,1,33536,2048,0,0,0,0,0,0,2,0,0,0,0,0,0,0,0,'',0,1,1.35,1,1,0,0,1,0,0,2,'',0),
(1116003,0,0,0,0,0,27645,0,0,0,'Construct','...has the good stuff',62001,63,63,0,35,1,1,1.14286,0.7,0,0,1,2000,2000,1,1,1,33536,2048,0,0,0,0,0,0,2,0,0,0,0,0,0,0,0,'',0,1,1.35,1,1,0,0,1,0,0,2,'',0);

DELETE FROM `npc_text` WHERE `ID` IN (92101,92102,92103,92104,92105);
INSERT INTO `npc_text`
  (`ID`,`text0_0`,`BroadcastTextID0`,`lang0`,`Probability0`,
   `em0_0`,`em0_1`,`em0_2`,`em0_3`,`em0_4`,`em0_5`,
   `BroadcastTextID1`,`lang1`,`Probability1`,`em1_0`,`em1_1`,`em1_2`,`em1_3`,`em1_4`,`em1_5`,
   `BroadcastTextID2`,`lang2`,`Probability2`,`em2_0`,`em2_1`,`em2_2`,`em2_3`,`em2_4`,`em2_5`,
   `BroadcastTextID3`,`lang3`,`Probability3`,`em3_0`,`em3_1`,`em3_2`,`em3_3`,`em3_4`,`em3_5`,
   `BroadcastTextID4`,`lang4`,`Probability4`,`em4_0`,`em4_1`,`em4_2`,`em4_3`,`em4_4`,`em4_5`,
   `BroadcastTextID5`,`lang5`,`Probability5`,`em5_0`,`em5_1`,`em5_2`,`em5_3`,`em5_4`,`em5_5`,
   `BroadcastTextID6`,`lang6`,`Probability6`,`em6_0`,`em6_1`,`em6_2`,`em6_3`,`em6_4`,`em6_5`,
   `BroadcastTextID7`,`lang7`,`Probability7`,`em7_0`,`em7_1`,`em7_2`,`em7_3`,`em7_4`,`em7_5`,
   `VerifiedBuild`)
VALUES
(92101,'Hello Time Traveler! Chromie has ordered me to provide you with proper tools on your journey, if you can show evidence of being worthy.',0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1),
(92102,'Keep in mind that these items won\'t last forever. They will turn back to what you brought here when we leave this timeline. Are you sure you wish to turn them in?',0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1),
(92103,'Hello Time Traveler! Chromie has ordered me to reward you for your efforts in faction wars.',0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1),
(92104,'Are you sure you wish to spend your acquired honor for money? There is no turning back!',0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1),
(92105,'I am offering Tokens for the most powerful gear, designed for combat against other heroes. You can currently obtain these tokens listed below.',0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,1);

DELETE FROM `creature_equip_template` WHERE `CreatureID` IN (1116001,1116002);
INSERT INTO `creature_equip_template` (`CreatureID`,`ID`,`ItemID1`,`ItemID2`,`ItemID3`,`VerifiedBuild`) VALUES
(1116002,1,18609,0,0,18019);
