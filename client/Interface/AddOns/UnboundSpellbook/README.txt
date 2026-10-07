UNBOUND SPELLBOOK v0.2 — DIRECT KNOWN-SPELL EDITION
Standalone WotLK 3.3.5a addon

WHY THIS VERSION EXISTS
-----------------------
A character that learns every class and every spell can exceed the assumptions
inside Blizzard's original 3.3.5 spellbook. Its General tab may stop changing
pages around the Mage portal section.

This addon no longer uses those native pages to decide what to display.

HOW IT WORKS
------------
- Checks the built-in class ability database directly with IsSpellKnown.
- Shows active abilities only; passive talents and proficiencies are excluded.
- Keeps only the highest known rank of an ability.
- Sorts results into all ten class tabs.
- Scans incrementally across several frames to avoid a large UI freeze.
- Supports far more than 150 visible known abilities.

OPEN
----
/usbk
or
/unboundspellbook

UPDATE
------
Replace the entire existing folder:
World of Warcraft\Interface\AddOns\UnboundSpellbook

Then type:
/reload

DRAGGING
--------
The display no longer depends on Blizzard's pages.

Dragging still needs a native spellbook slot because the WotLK client API
PickupSpell uses a spellbook slot. The addon searches the complete native tab
ranges when you drag. If the client refuses to expose a particular slot, that
ability will still be visible here but may not be draggable to an action bar.

RESCAN
------
Use the Direct Rescan button or:
/usbkrescan

DATA SCOPE
----------
The database contains standard WotLK trainer abilities, rank chains and active
talent-granted abilities for:
Warrior, Paladin, Hunter, Rogue, Priest, Death Knight,
Shaman, Mage, Warlock and Druid.
