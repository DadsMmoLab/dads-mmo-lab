-- Unbound Wrath Edition — catalog gap fill: Mage teleports/portals + Paladin mount
-- Applied to: acore_world.unbound_class_catalog
--
-- Remaining real-trainer spells identified by the level 1-80 catalog audit
-- (2026-06-13) that were missing entirely. req_level/cost taken directly from
-- trainer_spell (Type=0, Requirement=class_id).
--
-- Deliberately NOT added (see audit notes):
--   - Paladin Summon Charger (34767, req40/3500c): trainer_spell gates it on
--     ReqAbility1=33391 ("Journeyman Riding", itself a 1000g Riding-trainer
--     spell at req60) and ReqAbility2=34769 (a second, untaught "Summon
--     Warhorse" companion spell). That prereq chain reaches into the Riding
--     skill system, which Unbound doesn't model — locked until a proper
--     prereq/talent system exists, per Joshua's call on Seal of Corruption.
--   - Paladin Seal of Corruption (53736): per Wowhead, this is the
--     Horde-faction name for the same "Holy Vengeance" seal as Seal of
--     Vengeance (31801, already in the catalog via 08_catalog_additions.sql)
--     — Alliance/Horde naming variants of one ability, not a talent rank or
--     an upgrade. Adding it would just duplicate 31801 under another name.
--
-- prereq_spell defaults to 0; PREREQ_MAP (built from catalog req_level order
-- at script load) infers same-named rank chains automatically.
-- Safe to re-run: uses INSERT IGNORE.

INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES

-- ── Paladin (class_id=2) ──────────────────────────────────────────────────────
-- Summon Warhorse (34768) — basic Paladin mount, no prereqs.
(2, 34768, 3500, 20),

-- ── Mage (class_id=8) — Teleport/Portal lines ─────────────────────────────────
-- req 20, 2000c: Teleport: Stormwind/Ironforge/Undercity/Orgrimmar/Exodar/Silvermoon/Stonard/Theramore
(8,  3561, 2000, 20),
(8,  3562, 2000, 20),
(8,  3563, 2000, 20),
(8,  3567, 2000, 20),
(8, 32271, 2000, 20),
(8, 32272, 2000, 20),
(8, 49358, 2000, 20),
(8, 49359, 2000, 20),

-- req 30, 8000c: Teleport: Darnassus/Thunder Bluff
(8,  3565, 8000, 30),
(8,  3566, 8000, 30),

-- req 35, 15000c: Portal: Theramore/Stonard
(8, 49360, 15000, 35),
(8, 49361, 15000, 35),

-- req 40, 15000c: Portal: Stormwind/Ironforge/Orgrimmar/Undercity/Exodar/Silvermoon
(8, 10059, 15000, 40),
(8, 11416, 15000, 40),
(8, 11417, 15000, 40),
(8, 11418, 15000, 40),
(8, 32266, 15000, 40),
(8, 32267, 15000, 40),

-- req 50, 32000c: Portal: Darnassus/Thunder Bluff
(8, 11419, 32000, 50),
(8, 11420, 32000, 50),

-- req 60, 20000c: Teleport: Shattrath (Aldor/Scryer faction-name variants)
(8, 33690, 20000, 60),
(8, 35715, 20000, 60),

-- req 65, 150000c: Portal: Shattrath (Aldor/Scryer faction-name variants)
(8, 33691, 150000, 65),
(8, 35717, 150000, 65),

-- req 71/74: Teleport/Portal: Dalaran
(8, 53140, 100000, 71),
(8, 53142, 100000, 74);
