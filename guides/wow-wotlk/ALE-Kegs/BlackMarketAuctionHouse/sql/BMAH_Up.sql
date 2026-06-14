-- BMAH_Up.sql
-- Dad's MMO Lab ALE-Kegs / BlackMarketAuctionHouse
-- Creates the default Black Market Broker NPC (entry 2069430) in acore_world.
-- Apply to acore_world:
--   docker exec -i <db-container> mysql -u acore -pacore acore_world < BMAH_Up.sql
--
-- To use a different NPC entry:
--   1. Change the entry number below (and in BMAH.lua BMAH_VENDOR_NPCs)
--   2. OR run: UPDATE creature_template SET npcflag = npcflag | 1 WHERE entry = <your_entry>;

DELETE FROM `creature_template` WHERE `entry` = 2069430;
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
  (2069430,0,0,0,0,0,20572,0,0,0,'Black Market Broker','Rare Goods & Services',0,63,63,0,35,
   1,1,1.14286,1,0,0,1,2000,2000,1,1,1,33536,2048,0,0,0,0,0,0,7,0,0,0,0,0,0,0,0,'',0,1,1,1,1,0,0,1,0,0,2,'',0);
