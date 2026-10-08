-- Unbound Wrath Edition — per-class character-creation gift spells
-- Applied to: acore_world.playercreateinfo_spell_custom
--
-- These are granted for FREE when a player unlocks a class via the Mentor NPC,
-- matching exactly what a freshly-created level-1 character of that class receives.
-- "Aelric opens the door; trainers fill the rooms."
--
-- classmask = 2^(classId-1):
--   Warrior=1  Paladin=2  Hunter=4  Rogue=8  Priest=16
--   Shaman=64  Mage=128   Warlock=256  Druid=1024
-- racemask = 0 means all races.
--
-- Apply: docker exec ac-database mysql -u root -ppassword acore_world < this_file.sql

-- Clear any previous entries so this file is safe to re-run
DELETE FROM playercreateinfo_spell_custom WHERE racemask = 0 AND classmask IN (1,2,4,8,16,64,128,256,1024);

-- ── Warrior (classmask=1) ────────────────────────────────────────────────────
-- All 3 stances + starting combat abilities
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 1, 2457, 'Warrior - Battle Stance'),
(0, 1, 71,   'Warrior - Defensive Stance'),
(0, 1, 2458, 'Warrior - Berserker Stance'),
(0, 1, 78,   'Warrior - Heroic Strike r1'),
(0, 1, 6673, 'Warrior - Battle Shout r1'),
(0, 1, 100,  'Warrior - Charge r1');

-- ── Paladin (classmask=2) ────────────────────────────────────────────────────
-- Judgement is the core rotation ability — without it, an Unbound Paladin's
-- Seal is permanently inert. The trainer-taught ID (10321) has a
-- SPELL_EFFECT_LEARN_SPELL effect, which Mentor-driven grants silently fail
-- (see 14_judgement_fix.sql). This row is inserted as 10321 and corrected to
-- 20271 ("Judgement of Light" — the real castable Judgement button, confirmed
-- working via Testpal) by 14_judgement_fix.sql, which must run after this file.
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 2, 635,   'Paladin - Holy Light r1'),
(0, 2, 20154, 'Paladin - Seal of Righteousness r1'),
(0, 2, 465,   'Paladin - Devotion Aura r1'),
(0, 2, 10321, 'Paladin - Judgement');

-- ── Hunter (classmask=4) ────────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 4, 75,    'Hunter - Auto Shot'),
(0, 4, 2973,  'Hunter - Raptor Strike r1'),
(0, 4, 13165, 'Hunter - Aspect of the Hawk r1');

-- ── Rogue (classmask=8) ─────────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 8, 1784, 'Rogue - Stealth r1'),
(0, 8, 1752, 'Rogue - Sinister Strike r1'),
(0, 8, 2098, 'Rogue - Eviscerate r1');

-- ── Priest (classmask=16) ────────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 16, 585,  'Priest - Smite r1'),
(0, 16, 2050, 'Priest - Lesser Heal r1');

-- ── Shaman (classmask=64) ────────────────────────────────────────────────────
-- All 4 starter totems are gifted so Shaman spells that require totems work
-- immediately. 2484=Earthbind Totem; totem items (5175-5178) are given by
-- GrantClassGiftItems in the Lua (CLASS_GIFT_ITEMS[7]).
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 64, 403,  'Shaman - Lightning Bolt r1'),
(0, 64, 331,  'Shaman - Healing Wave r1'),
(0, 64, 8071, 'Shaman - Stoneskin Totem r1 (Earth)'),
(0, 64, 8042, 'Shaman - Searing Totem r1 (Fire)'),
(0, 64, 5394, 'Shaman - Healing Stream Totem r1 (Water)'),
(0, 64, 8512, 'Shaman - Windfury Totem r1 (Air)'),
(0, 64, 2484, 'Shaman - Earthbind Totem');

-- ── Mage (classmask=128) ────────────────────────────────────────────────────
-- Arcane Intellect (1459) is a key Mage utility spell taught by trainer at level 1
-- but not included in Playerbots creation data — must be explicitly gifted.
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 128, 133,  'Mage - Fireball r1'),
(0, 128, 168,  'Mage - Frost Armor r1'),
(0, 128, 1459, 'Mage - Arcane Intellect r1');

-- ── Warlock (classmask=256) ─────────────────────────────────────────────────
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 256, 686, 'Warlock - Shadow Bolt r1'),
(0, 256, 687, 'Warlock - Demon Skin'),
(0, 256, 688, 'Warlock - Summon Imp');

-- ── Druid (classmask=1024) ───────────────────────────────────────────────────
-- Bear Form and Aquatic Form are sold via the Mentor catalog, not gifted free.
-- Bear Form: 5 silver (500 copper) — see 04_catalog_druid_forms.sql
-- Aquatic Form: already in catalog at 900 copper from Playerbots trainer data.
INSERT INTO playercreateinfo_spell_custom (racemask, classmask, Spell, Note) VALUES
(0, 1024, 5176, 'Druid - Wrath r1'),
(0, 1024, 5185, 'Druid - Healing Touch r1');
