-- Unbound Wrath Edition — Mentor NPC setup
-- Run once against acore_world AFTER the server has fully initialized.
-- Safe to re-run: INSERT IGNORE skips if entry already exists.
--
-- AzerothCore dropped `scale`, `mechanic_immune_mask`, and
-- `spell_school_immune_mask` from creature_template in migration
-- 2026_03_22_03.  This file uses the post-migration schema.
--
-- Apply:
--   docker exec -i <db-container> mysql -u root -p<pass> acore_world < npc_setup.sql
--
-- Then spawn in-game:
--   .npc add 900001

INSERT IGNORE INTO `creature_template`
    (`entry`, `name`, `subname`, `gossip_menu_id`,
     `minlevel`, `maxlevel`, `exp`, `faction`, `npcflag`,
     `speed_walk`, `speed_run`, `speed_swim`, `speed_flight`,
     `detection_range`, `rank`, `dmgschool`,
     `DamageModifier`, `BaseAttackTime`, `RangeAttackTime`,
     `BaseVariance`, `RangeVariance`,
     `unit_class`, `unit_flags`, `unit_flags2`, `dynamicflags`,
     `family`, `type`, `type_flags`,
     `lootid`, `pickpocketloot`, `skinloot`,
     `PetSpellDataId`, `VehicleId`, `mingold`, `maxgold`,
     `AIName`, `MovementType`, `HoverHeight`,
     `HealthModifier`, `ManaModifier`, `ArmorModifier`, `ExperienceModifier`,
     `RacialLeader`, `movementId`, `RegenHealth`,
     `flags_extra`, `ScriptName`, `VerifiedBuild`)
VALUES
    (900001, 'The Mentor', 'Unbound Class Trainer', 0,
     80, 80, 0, 35, 1,
     1.0, 1.14286, 1.0, 1.0,
     18, 0, 0,
     1.0, 1500, 2000,
     1.0, 1.0,
     1, 768, 2048, 0,
     0, 7, 0,
     0, 0, 0,
     0, 0, 0, 0,
     '', 0, 1.0,
     1.0, 1.0, 1.0, 1.0,
     0, 0, 1,
     2, '', 12340);

-- DisplayID 19097 = Ethereal Thief — final model, locked in by Joshua + Caitlin.
INSERT IGNORE INTO `creature_template_model`
    (`CreatureID`, `Idx`, `CreatureDisplayID`, `DisplayScale`, `Probability`, `VerifiedBuild`)
VALUES
    (900001, 0, 19097, 1.0, 1.0, 12340);
