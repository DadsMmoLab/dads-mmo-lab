-- Unbound Wrath Edition — catalog gap fill
-- Applied to: acore_world.unbound_class_catalog
--
-- These spells appear in WotLK class trainers (trainer_spell IDs 1-34) but were
-- absent from the catalog, which was originally populated from Playerbots
-- synthetic trainer data (npc_trainer IDs 200002–200018). The Playerbots
-- templates omit some low-level rank-1 spells, particularly those that native
-- characters receive at creation.
--
-- Spells already in creation gifts (playercreateinfo_spell_custom) are excluded
-- because Unbound players receive them for free at unlock.
-- Prices match WotLK trainer MoneyCost values.
--
-- Safe to re-run: uses INSERT IGNORE.
-- prereq_spell defaults to 0; PREREQ_MAP (built from catalog req_level order at
-- script load) will infer rank chains automatically.

-- prereq_spell is omitted; it defaults to 0 (added by 05_individual_purchase_prereqs.sql).
-- PREREQ_MAP in the Lua infers rank chains from req_level ordering at script load.
INSERT IGNORE INTO unbound_class_catalog (class_id, spell_id, gold_cost_copper, req_level) VALUES

-- ── Warrior (class_id=1) ──────────────────────────────────────────────────────
-- Rend r1 (772), Parry (3127), Thunder Clap r1 (6343), Victory Rush (34428)
(1,   772,   100, 4),
(1,  3127,   100, 6),
(1,  6343,   100, 6),
(1, 34428,   100, 6),

-- ── Paladin (class_id=2) ──────────────────────────────────────────────────────
-- Judgement (10321), Blessing of Might r1 (19740),
-- Divine Protection r1 (498), Holy Light r2 (639),
-- Seal of Vengeance (31801 — high-level Retribution seal)
(2, 10321,   100, 4),
(2, 19740,   100, 4),
(2,   498,   100, 6),
(2,   639,   100, 6),
(2, 31801, 67000,64),

-- ── Hunter (class_id=3) ───────────────────────────────────────────────────────
-- Track Beasts (1494), Serpent Sting r1 (1978),
-- Hunter's Mark r1 (1130), Arcane Shot r1 (3044)
(3, 1494,    10, 2),
(3, 1978,   100, 4),
(3, 1130,   100, 6),
(3, 3044,   100, 6),

-- ── Rogue (class_id=4) ────────────────────────────────────────────────────────
-- Backstab r1 (53), Pickpocket (921),
-- Gouge r1 (1757), Ambush r1 (1776)
(4,   53,   100, 4),
(4,  921,   100, 4),
(4, 1757,   100, 6),
(4, 1776,   100, 6),

-- ── Priest (class_id=5) ───────────────────────────────────────────────────────
-- Power Word: Fortitude r1 (1243), Shadow Word: Pain r1 (589),
-- Lesser Heal r2 (2052 — rank 2 of creation gift 2050), Power Word: Shield r1 (17),
-- Smite r2 (591 — rank 2 of creation gift 585)
(5, 1243,    10, 1),
(5,  589,   100, 4),
(5, 2052,   100, 4),
(5,   17,   100, 6),
(5,  591,   100, 6),

-- ── Shaman (class_id=7) ───────────────────────────────────────────────────────
-- Rockbiter Weapon r1 (8017), Earth Shock r1 (8042 in gifts — skip),
-- Healing Wave r2 (332 — rank 2 of creation gift 331), Earthbind Totem (2484 in gifts — skip)
(7, 8017,    10, 1),
(7,  332,   100, 6),

-- ── Mage (class_id=8) ─────────────────────────────────────────────────────────
-- Arcane Intellect r1 (1459 — also in creation gifts; added here so higher ranks'
-- prereq chain resolves correctly and re-purchase is possible if lost)
-- Frostbolt r1 (116), Conjure Food r1 (587→5504),
-- Conjure Water r1 (143), Conjure Food r1 (587),
-- Fire Blast r1 (2136), Detect Magic (2855)
(8, 1459,    10, 1),
(8,  116,   100, 4),
(8, 5504,   100, 4),
(8,  143,   100, 6),
(8,  587,   100, 6),
(8, 2136,   100, 6),
(8, 2855,  2000,16),

-- ── Warlock (class_id=9) ──────────────────────────────────────────────────────
-- Immolate r1 (348), Corruption r1 (172), Curse of Weakness r1 (702),
-- Shadow Bolt r2 (695 — rank 2 of creation gift 686), Life Tap r1 (1454)
(9,  348,    10, 3),
(9,  172,   100, 4),
(9,  702,   100, 4),
(9,  695,   100, 6),
(9, 1454,   100, 6),

-- ── Druid (class_id=11) ───────────────────────────────────────────────────────
-- Mark of the Wild r1 (1126), Rejuvenation r1 (774), Moonfire r1 (8921),
-- Thorns r1 (467), Wrath r2 (5177 — rank 2 of creation gift 5176)
(11, 1126,    10, 1),
(11,  774,   100, 4),
(11, 8921,   100, 4),
(11,  467,   100, 6),
(11, 5177,   100,  6);
