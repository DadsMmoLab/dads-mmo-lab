-- Unbound Wrath Edition — Paladin Judgement purchase/gift fix
-- Applied to: acore_world.unbound_class_catalog, acore_world.playercreateinfo_spell_custom
--
-- Same bug class as 12_mount_spell_fix.sql / 13_flight_form_fix.sql.
--
-- Confirmed live (2026-06-13) with Testpal (Rogue, second class Paladin via
-- Mentor at level 5): the Paladin-unlock creation gifts 635 (Holy Light r1),
-- 20154 (Seal of Righteousness r1) and 465 (Devotion Aura r1) were granted
-- correctly, but 10321 ("Judgement") was not — and buying "Judgement" from
-- the Mentor (req4/100c) takes gold, grants nothing, and the entry never
-- disappears from Browse ("keeps buying over and over").
--
-- 10321 "Judgement" is a trainer TEACH spell: Effects=[36,36,0],
-- TriggerSpells=[20271 "Judgement of Light", 21084 "Seal of Righteousness"].
-- Both player:LearnSpell() (Mentor purchase) and the Mentor's class-unlock
-- gift-granting code call learnSpell() non-temporary, which Player::_addSpell
-- erases-and-rejects for any SPELL_EFFECT_LEARN_SPELL spell. A *native*
-- character creation grants 10321 via AzerothCore's temporary=true path
-- (which DOES honor LEARN_SPELL), so freshly-rolled Paladins are unaffected —
-- only Mentor-driven unlocks and Mentor purchases hit the broken path.
--
-- Fix part 1 (catalog, live immediately, no restart): point the catalog entry
-- at 20271 "Judgement of Light" — the actual SCRIPT_EFFECT spell WotLK
-- Paladins use as their "Judgement" button (it judges using whichever Seal is
-- currently active, regardless of the "of Light" name). Same cost/req_level.
-- This is also the remediation path for Testpal and anyone else already
-- missing Judgement from a Mentor unlock.
--
-- Fix part 2 (creation-gift table, requires worldserver restart):
-- playercreateinfo_spell_custom is loaded into PlayerInfo at startup, so this
-- only affects FUTURE Mentor class-unlocks until restarted.
--
-- Not touched: 21084 "Seal of Righteousness" (10321's other trigger). Testpal
-- already has 20154 "Seal of Righteousness r1" as a creation gift and both
-- DBC entries share the same name with no rank text to distinguish them —
-- granting 21084 too risks an unverified duplicate/rank conflict. Flag for a
-- follow-up if Seal of Righteousness turns out not to rank up correctly.
--
-- Safe to re-run: DELETE the old spell_id/Spell row then INSERT IGNORE the
-- new one in each table, so re-running never collides on the primary key
-- (see 12_mount_spell_fix.sql for why a plain UPDATE isn't safe here).

DELETE FROM unbound_class_catalog WHERE class_id = 2 AND spell_id = 10321;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (2, 20271, 100, 4);

DELETE FROM playercreateinfo_spell_custom WHERE racemask = 0 AND classmask = 2 AND Spell = 10321;
INSERT IGNORE INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES (0, 2, 20271, 'Paladin - Judgement of Light');
