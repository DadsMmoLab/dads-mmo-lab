-- mod-multiclass-summons: Register the summon-override spell script.
--
-- This file lives under modules/<module>/data/sql/db-world/, which is the only
-- module SQL location AzerothCore's DBUpdater auto-applies (UpdateFetcher.cpp).
-- It runs during database loading at startup, BEFORE the world calls
-- LoadSpellScriptNames(), so these rows are present when the spell system caches
-- the table. Do NOT register these from C++ at runtime: that executes AFTER
-- LoadSpellScriptNames() and is only picked up on the next restart.

DELETE FROM `spell_script_names` WHERE `ScriptName` = 'spell_summon_pet_override';
INSERT INTO `spell_script_names` (`spell_id`, `ScriptName`) VALUES
(688, 'spell_summon_pet_override'),     -- Summon Imp
(697, 'spell_summon_pet_override'),     -- Summon Voidwalker
(712, 'spell_summon_pet_override'),     -- Summon Succubus
(691, 'spell_summon_pet_override'),     -- Summon Felhunter
(30146, 'spell_summon_pet_override'),   -- Summon Felguard
(70907, 'spell_summon_pet_override'),   -- Summon Water Elemental (Temp)
(70908, 'spell_summon_pet_override'),   -- Summon Water Elemental (Perm)
(46584, 'spell_summon_pet_override'),   -- Raise Dead (Temp Ghoul)
(52150, 'spell_summon_pet_override');   -- Raise Dead (Perm Ghoul)
