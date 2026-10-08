-- Unbound Wrath Edition — Unbounding Mentor Stone
-- Applied to: acore_world
--
-- Creates a permanent use-item (entry 900100) given to every character at login.
-- Right-clicking summons the Mentor NPC (entry 900001) for 3 minutes.
--
-- Safe to re-run: uses INSERT IGNORE / ON DUPLICATE KEY UPDATE.
--
-- IMPORTANT — root cause writeup (see ~/wow-server-playerbots/CLAUDE.md "RESOLVED BUGS"):
-- The item's spellid_1 MUST point at a real, client-known spell ID (Blizzard IDs
-- top out around ~71000). A custom server-only ID like 900200 is invisible to the
-- client's binary Spell.dbc — the client silently refuses to recognize the item as
-- usable and never even sends CMSG_USE_ITEM. spellid_1 = 433 ("Food") was chosen as
-- a harmless defense-in-depth fallback: its only effect is a heal-over-time that
-- fizzles unless seated, so even if the Lua cancellation in unbound_mentor.lua were
-- ever bypassed, nothing disruptive happens (unlike the Hearthstone teleport that
-- was tried first during diagnosis). The Lua ITEM_EVENT_ON_USE handler unconditionally
-- returns true to cancel the real cast — the Lua-side STONE_LAST_USE 180s cooldown
-- fully replaces spellcooldown_1 as the gameplay cooldown.
--
-- displayid = 6418 (INV_Misc_Rune_01) — a Vanilla-era rune-stone icon guaranteed
-- present in any 3.3.5a client; newer WotLK icons (e.g. 58413) can render as "?"
-- on clients whose MPQ data is missing those textures.

-- ── Item 900100: Unbounding Mentor Stone ─────────────────────────────────────
-- class=15 (Miscellaneous), InventoryType=0 (non-equippable bag item).
-- maxcount=1 ensures only one copy can be held at a time.
-- spellid_1=433 (Food — real client-known spell, cancelled by Lua) +
-- spellcooldown_1=180000 ms (3 min, superseded by the Lua-side cooldown guard).
INSERT INTO item_template
    (entry, class, subclass, SoundOverrideSubclass, name,
     displayid, Quality, Flags, FlagsExtra,
     BuyCount, BuyPrice, SellPrice,
     InventoryType, AllowableClass, AllowableRace,
     ItemLevel, RequiredLevel,
     maxcount, stackable,
     spellid_1, spelltrigger_1, spellcharges_1, spellppmRate_1,
     spellcooldown_1, spellcategory_1, spellcategorycooldown_1,
     description, ScriptName)
VALUES
    (900100, 15, 0, -1, 'Unbounding Mentor Stone',
     6418, 3, 0, 0,
     1, 0, 0,
     0, -1, -1,
     1, 0,
     1, 1,
     433, 0, 0, 0,
     180000, 0, -1,
     'Summons your Unbounding Mentor for 3 minutes. (3 min cooldown)', '')
ON DUPLICATE KEY UPDATE
    name              = VALUES(name),
    displayid         = VALUES(displayid),
    Quality           = VALUES(Quality),
    spellid_1         = VALUES(spellid_1),
    spelltrigger_1    = VALUES(spelltrigger_1),
    spellcooldown_1   = VALUES(spellcooldown_1),
    description       = VALUES(description);

-- ── Give stone to all new characters at creation ─────────────────────────────
-- race=0 means any race; class entries cover all WotLK playable classes.
-- The Lua login hook in unbound_mentor.lua also gives it to existing characters.
INSERT IGNORE INTO playercreateinfo_item (race, class, itemid, amount) VALUES
(0,  1, 900100, 1),   -- Warrior
(0,  2, 900100, 1),   -- Paladin
(0,  3, 900100, 1),   -- Hunter
(0,  4, 900100, 1),   -- Rogue
(0,  5, 900100, 1),   -- Priest
(0,  6, 900100, 1),   -- Death Knight
(0,  7, 900100, 1),   -- Shaman
(0,  8, 900100, 1),   -- Mage
(0,  9, 900100, 1),   -- Warlock
(0, 11, 900100, 1);   -- Druid
