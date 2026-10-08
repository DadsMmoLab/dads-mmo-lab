-- Unbound Wrath Edition — Druid Flight Form purchase fix
-- Applied to: acore_world.unbound_class_catalog
--
-- Same bug class as 12_mount_spell_fix.sql, found by auditing every catalog
-- spell_id against Spell.dbc for SPELL_EFFECT_LEARN_SPELL (36).
--
-- 33950 "Flight Form" (Druid, req68/34000c) is a trainer TEACH spell —
-- Effects=[36,36,44], TriggerSpells=[33943 "Flight Form", 34090 "Expert
-- Riding"]. Player::_addSpell() erases any spell with SPELL_EFFECT_LEARN_SPELL
-- when learned via the non-temporary player:LearnSpell() the Mentor uses, so
-- buying 33950 took gold and granted nothing — identical symptom to the
-- mount bug (reappears in Browse, not in spellbook, not usable).
--
-- Fix: point the catalog at 33943, the real castable "Flight Form" shapeshift
-- spell (same name, same cost/req_level, Effects=[6,6,6] — no LEARN_SPELL,
-- learns normally).
--
-- Note: 34090 "Expert Riding" (skill 762 -> 225, needed to actually fly) is
-- not granted by this fix, same rationale as 12_mount_spell_fix.sql — Riding
-- skill is already universally accessible (06_universal_skill_access.sql) and
-- most level-68+ characters will already have at least Artisan Riding (300)
-- from normal flying-mount training, which exceeds the 225 Expert requirement.
--
-- No worldserver restart required. Safe to re-run: DELETE the old spell_id
-- then INSERT IGNORE the new one, so re-running never collides on the
-- (class_id, spell_id) primary key (see 12_mount_spell_fix.sql for why a
-- plain UPDATE isn't safe here).

DELETE FROM unbound_class_catalog WHERE class_id = 11 AND spell_id = 33950;
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES (11, 33943, 34000, 68);
