-- Unbound Wrath Edition — Paladin/Warlock mount purchase fix
-- Applied to: acore_world.unbound_class_catalog
--
-- Reported by Joshua: Summon Warhorse (Paladin) and Summon Felsteed (Warlock)
-- can be "bought" from the Mentor — gold is deducted and a success message is
-- shown — but the spell never appears in the spellbook, isn't selectable as a
-- mount, and the entry reappears in Browse as if never purchased.
--
-- Root cause (confirmed against AzerothCore source + Spell.dbc, 2026-06-13):
-- 34768 ("Summon Warhorse") and 1710 ("Summon Felsteed") are trainer TEACH
-- spells — their Effect array contains SPELL_EFFECT_LEARN_SPELL (36) twice,
-- meant to recursively grant the real mount spell + Apprentice Riding via the
-- temporary-learn trainer path. Player::_addSpell() (Player.cpp ~3192)
-- explicitly refuses any spell with SPELL_EFFECT_LEARN_SPELL when called via
-- the non-temporary player:LearnSpell() the Mentor uses — it adds the spell to
-- m_spells, immediately erases it, and returns false. The Lua never checks
-- that return value, so gold is taken and "Learned!" fires for a purchase that
-- silently did nothing.
--
-- Fix: point the catalog at the REAL castable mount spell each teach-spell was
-- meant to grant (same display name, same cost/req_level). Neither real mount
-- has a LEARN_SPELL effect, so player:LearnSpell() works normally — same code
-- path as Dreadsteed (23161), which already works correctly:
--   34768 "Summon Warhorse" (teach) -> 34769 "Summon Warhorse" (real mount)
--   1710  "Summon Felsteed" (teach) -> 5784  "Felsteed"        (real mount)
--
-- Note: both real mounts also require Apprentice Riding (skill 762 >= 75) to
-- be summonable once learned. Not modeled by the catalog, but
-- 06_universal_skill_access.sql already makes Riding (762) valid for every
-- class/race, and any character who trained a faction mount in the normal
-- 20-40 leveling range will already have it (confirmed live: Testmage has
-- Riding 150/150). Left out here to avoid scope creep into a riding-skill
-- purchase system — flag to Joshua if a player reports the mount is in their
-- spellbook but won't summon.
--
-- No worldserver restart required: the catalog is read live on every
-- Browse/Buy, and PREREQ_MAP doesn't reference these IDs (mounts have no rank
-- chain). Safe to re-run: each pair is a DELETE of the old spell_id followed
-- by INSERT IGNORE of the new one, so re-running never collides on the
-- (class_id, spell_id) primary key — even if an earlier INSERT IGNORE
-- migration re-creates the old row after this fix already ran once (e.g.
-- after an uninstall/reinstall where AzerothCore's update-tracking and the
-- catalog data fall out of sync). A plain UPDATE...SET spell_id=<new> would
-- collide with the primary key in that case since <new> already exists.

DELETE FROM unbound_class_catalog WHERE class_id = 2 AND spell_id = 34768;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (2, 34769, 3500, 20);

DELETE FROM unbound_class_catalog WHERE class_id = 9 AND spell_id = 1710;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (9, 5784, 10000, 20);
