-- Unbound Wrath Edition — catalog req_level self-heal vs real WotLK trainers
-- Applied to: acore_world.unbound_class_catalog
--
-- 02_fix_catalog_req_level.sql blanket-lowered every entry with req_level <= 8
-- to req_level = 1 so a level-5 class unlock always had something buyable.
-- That also dragged down ~30 legitimate rank-2/utility spells (Heroic Strike
-- r2, Hammer of Justice, Aspect of the Hawk r2, etc.) that real trainers gate
-- at level 8, plus a separate batch of level 60/70 spells that were a tier
-- below their real 61/71 requirement.
--
-- Fix: pull req_level straight from the real class trainers (trainer +
-- trainer_spell, Type=0, Requirement=class_id) and apply it wherever the
-- catalog disagrees. Verified live (2026-06-13): this only ever RAISES
-- req_level — level-5 unlocks stay buyable because 08_catalog_additions.sql
-- already seeds req_level 1/2/4/6 entries per class.
--
-- Requires a worldserver restart afterward so PREREQ_MAP (built once at Eluna
-- load from catalog req_level order) re-sorts rank chains with the corrected
-- levels.
--
-- Safe to re-run: WHERE clause only touches rows that still disagree.

UPDATE unbound_class_catalog c
JOIN (
    SELECT t.Requirement AS class_id, ts.SpellID AS spell_id, MIN(ts.ReqLevel) AS req_level
    FROM trainer t
    JOIN trainer_spell ts ON ts.TrainerId = t.Id
    WHERE t.Type = 0 AND t.Requirement IN (1,2,3,4,5,7,8,9,11)
    GROUP BY t.Requirement, ts.SpellID
) rts ON rts.class_id = c.class_id AND rts.spell_id = c.spell_id
SET c.req_level = rts.req_level
WHERE c.req_level <> rts.req_level;

-- ============================================================
-- Hunter (class_id=3) gap fill: Aspect of the Monkey
-- ============================================================
-- 13163 = Aspect of the Monkey, a real Hunter trainer spell (req_level 4,
-- 100c) missing from the catalog. It was stuck in limbo because
-- 03_creation_gift_spells.sql's Hunter gift used to point at 13163 by mistake
-- (intending Aspect of the Hawk r1 = 13165, now corrected) — so 13163 was
-- neither gifted nor purchasable. prereq_spell defaults to 0: Aspect of the
-- Monkey has no rank chain.
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES
(3, 13163, 100, 4);
