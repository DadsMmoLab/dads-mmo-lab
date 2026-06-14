-- ExchangeNpc_Down.sql  —  rollback / uninstall
-- Dad's MMO Lab ALE-Pub / ExchangeNPC
DELETE FROM `creature_template`      WHERE `entry`  IN (1116001, 1116002, 1116003);
DELETE FROM `creature_equip_template` WHERE `CreatureID` IN (1116001, 1116002);
DELETE FROM `npc_text`               WHERE `ID`     IN (92101, 92102, 92103, 92104, 92105);
DELETE FROM `npc_vendor`             WHERE `entry`  = 1116001;
DELETE FROM `conditions`
  WHERE (`SourceTypeOrReferenceId` = 23)
    AND (`SourceGroup` = 1116001)
    AND (`SourceId`    = 0)
    AND (`ElseGroup`   = 0)
    AND (`ConditionTypeOrReference` = 15)
    AND (`ConditionTarget` = 0)
    AND (`ConditionValue2` = 0)
    AND (`ConditionValue3` = 0);
