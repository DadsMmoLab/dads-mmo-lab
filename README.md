# Wrath Unbound v1.4.0 ("Shadow Release")

Wrath Unbound is a multi-class mod for a Wrath of the Lich King (3.3.5a) AzerothCore
server with mod-playerbots. A character unlocks extra classes at an NPC, "The Mentor",
buys those classes' spells, and spends talent points across the trees of every class it
has unlocked. Death Knight can only be a character's base class; the other nine classes
can be unlocked. Playerbots stay single-class.

- **Author:** DaddyCool, Dad's MMO Lab.
- **Version:** v1.4.0, "Shadow Release", released 2026-07-25.
- **This branch:** an orphan branch of `DadsMmoLab/dads-mmo-lab` that holds only Unbound's
  files, laid out as an AzerothCore module so that a server installer can clone one commit of
  it straight into `modules/mod-unbound`.

## Where these files came from

The release was posted in the Dad's MMO Lab Discord, channel #wow-unbound, on 2026-07-25
as three attachments. It was never published on GitHub; the copy under `guides/unbound-wrath/`
on the `main` branch of this repository is the older v1.2.2.

| Original file | sha256 |
|---|---|
| `install-wrath-unbound-addon.sh` (installer, `WIZARD_VERSION="1.4.0"`) | `edb949814c6212ef620ffe0bb4ec11bf9b1d9e93275125c1838bd478396df7fd` |
| `WrathUnbound-Addons.zip` (client addons) | `eb36d2ebaad96910e05cdfbf8db11a9cbfad16c18078b3725226eff844ebc686` |
| `README-PLAYERS.txt` | `f567013cce4c8c423826f6bc5cc6a48846ce79718df4de3d3c5a5c4e93537f3b` |

The installer carries its whole server payload as quoted shell heredocs. The v1.4.0 files on
this branch were taken from those heredocs byte for byte and **moved** (not rewritten) into
the module layout described below; the move itself is a set of 100% renames in `git log`.
The installer itself is not on this branch. The client addons start as the zip's contents,
unzipped unchanged. Files written for this branch rather than taken from the release, and
changes made to release files afterwards, are listed under "Changes on top of v1.4.0".

## Layout

This branch is laid out the way AzerothCore reads a module: **the branch root is the `mod-unbound`
module**. Clone it to `modules/mod-unbound` in an AzerothCore tree. AzerothCore only builds a
module folder that has a `src/` directory, and only applies module SQL from
`data/sql/<db-world|db-characters>/`. The v1.4.0 release record (commit `d29fac97`) kept the
installer's own layout instead, which cannot be built from a plain clone. The release's files were moved
into this layout unchanged (100% renames); what was added or changed after that is listed
under "Changes on top of v1.4.0" below.

| Path | What it is | Where the installer put it |
|---|---|---|
| `src/UnboundSystem.cpp`, `src/UnboundSystem_loader.cpp` | The `mod-unbound` C++ module: `UnboundSystem.cpp` (rage/energy for unlocked power types, keeping a Lua-set mana pool, and on login building the class mask from `unbound_character_unlocks` and granting weapon and armour proficiency; bots are skipped; since v1.4.0 also the character-delete cleanup and the rogue/hunter class answers listed under "Changes on top of v1.4.0") and its loader (`Addmod_unboundScripts()`). The loader also calls `AddUnboundMulticlassBridge()`, `AddUnboundReagentFree()` and `Addmod_multiclass_summonsScripts()` (the last because AzerothCore only calls the loader of a folder that has the module's own name). The release ships no `CMakeLists.txt` and no module `.conf.dist`. | `modules/mod-unbound/src/` |
| `src/UnboundMulticlassBridge.cpp` | The multi-class talent bridge in C++, by ValentineSin (see Credits), with the rank handling added on the Dad's MMO Lab server (a repeated spell id is mapped to the talent's next rank, and the server's rank count is echoed back as `LEARNED:class:spell:rank`). It answers the client addon's `MCUB` messages (`SYNC`, `RESET`, `LEARN`) and hands each pick to the core's own `Player::LearnTalent`, so talents are stored natively in `character_talent`, prerequisites and tier points use the core's rules, and a respec is `resetTalents`. The Mentor stays the authority: a class that is not unlocked is refused. It needs a core change that lets `Player::LearnTalent` accept the unlocked classes' talents. That change is **not** in `core-patch/`: it ships as Yu'lon's own patch (the vendored `unbound-mentor-fix.patch`, Yu'lon's patch 02), so a server built from this branch alone needs it applied too. It replaces `unbound_addon_sync.lua` and `unbound_talent_data.lua`, which were in v1.4.0 and answer the same messages; never run both. | new in this branch |
| `src/UnboundReagentFree.cpp` | Two optional server house rules, each behind its own switch and each **off** unless the conf turns it on: free casting reagents (`Unbound.ReagentFree`; soul shards, candles, powders, symbols, seeds, runes, ankh, corpse dust and the like; crafting materials are untouched; bots included) and instant, no-mana, cooldown-less class summons (`Unbound.InstantSummons`; their reagents are still used unless `Unbound.ReagentFree` is also on; warlock demons and Inferno, Water Elemental, Raise Dead, Feral Spirit). Both rewrite the spell store at start-up. The world log prints `[UNBOUND] free reagents: off` / `on (...)` and `[UNBOUND] instant summons: off` / `on (...)`. | new in this branch |
| `conf/mod_unbound.conf.dist` | The switches `Unbound.ReagentFree`, `Unbound.InstantSummons` and `Unbound.AutoBuff`, all `0`. A missing conf also means off. | `env/dist/etc/modules/mod_unbound.conf` (copied from the `.dist`) |
| `src/mod-multiclass-summons/` | bdodroid's `mod-multiclass-summons` C++ sources, used with permission (see Credits). It fixes Warlock, Mage and Death Knight pet and mount conflicts for multi-class characters and lets them field several guardians at once; playerbots are excluded at runtime. The loader files are the release's copy, byte-identical to `bdodroid/mod-multiclass-summons@6001603bfe038204b73d0d5878ac3e1f24dda915` (that commit's `README.md` was not part of the release and is not here). `multiclass_pet_fix.cpp` is the one exception, the Dad's MMO Lab server's copy with a fix on top of bdodroid's file: a hunter's Call Pet works while a warlock demon holds the pet slot, and the demon steps aside as a side summon instead of being lost (`SummonManager::DemotePrimary`, and spell 883 added to the list that dismisses the pet first). The fix is meant to be offered to bdodroid for his module. The module's `CMakeLists.txt` (two `AC_ADD_SCRIPT` lines for files AzerothCore already collects from `src/`) is not carried over. | `modules/mod-multiclass-summons/src/` |
| `data/sql/db-world/base/multiclass_summons.sql` | bdodroid's SQL: registers the `spell_summon_pet_override` spell script on spells 688, 697, 712, 691, 30146, 70907, 70908, 46584 and 52150. | `modules/mod-multiclass-summons/data/sql/db-world/base/` |
| `lua_scripts/` | The ALE (Eluna) Lua scripts: `unbound_mentor.lua` (the Mentor and the Mentor Stone) and `dml_autobuff.lua` v3 (the `#buffs` auto-buff command from the Dad's MMO Lab server; it does nothing, and prints `[dml_autobuff] off (Unbound.AutoBuff = 0)`, unless `Unbound.AutoBuff = 1` in `mod_unbound.conf`). v1.4.0's `unbound_addon_sync.lua` and `unbound_talent_data.lua` (the Lua talent bridge and its data) are no longer here: `src/UnboundMulticlassBridge.cpp` does their job. On a successful start the world log prints `[UNBOUND] Prereq map built.` | `env/dist/etc/modules/lua_scripts/` |
| `data/sql/db-world/` | SQL for the world database (`acore_world`). `npc_setup.sql` is renamed `00_npc_setup.sql` so that AzerothCore's updater, which orders by file name, applies it first. `15_unbound_mentor_spawns.sql` places the Mentor in the eight capitals and Dalaran (see "Changes on top of v1.4.0"). | `modules/mod-unbound/data/sql/db-world/` and `modules/mod-unbound/npc_setup.sql` |
| `data/sql/db-characters/` | SQL for the characters database (`acore_characters`). | `modules/mod-unbound/data/sql/db-characters/` |
| `core-patch/unbound-core-access.patch` | The AzerothCore core patch: 6 files (`Player.h`, `Player.cpp`, `PlayerQuest.cpp`, `PlayerStorage.cpp`, `Trainer.cpp`, `ConditionMgr.cpp`). It adds `Player::m_unboundClassMask` with `Get`/`SetUnboundClassMask()` and ORs it into the class checks for trainers, spells, quests, items and `CONDITION_CLASS`. The worldserver does not compile with `mod-unbound` unless this patch is applied. | `modules/mod-unbound/unbound-core-access.patch`, then `git apply` at the server root |
| `core-patch/unbound-mana-regen.patch` | A second core patch, 1 file (`Player.cpp`): a class whose own power is not mana reads the priest's spirit-regen row, so a warrior, rogue or death knight who unlocks a mana class regenerates mana. Apply it after `unbound-core-access.patch`. | new in this branch |
| `conf/mod_ale.conf` | The `mod_ale.conf` the installer writes when the server has none (`ALE.Enabled = 1`, `ALE.ScriptPath = "/azerothcore/env/dist/etc/modules/lua_scripts"`). | `env/dist/etc/modules/mod_ale.conf` |
| `conf/worldserver.conf.unbound` | `ValidateSkillLearnedBySpells = 0`, which the installer sets in `worldserver.conf`. Without it AzerothCore removes cross-class spells from every character at login. | `env/dist/etc/worldserver.conf` |
| `client/Interface/AddOns/` | The three client addons: `multiclass-talents-ui` (2.9.27-unbound-gm; `/mc`), `multiclass-resources` (1.3; `/mcr`) and `UnboundSpellbook` (1.0; `/usbk`, `/usbkrescan`, `/usbk macros`, `/usbk adopt`). The Dad's MMO Lab server's later versions of `multiclass-talents-ui/Core.lua` (the talent window; matched to `src/UnboundMulticlassBridge.cpp`, which echoes the rank the server holds) and of `UnboundSpellbook` (three tabs, spells past the client's 1024-slot cap can go on bars) replace the v1.4.0 files; every other addon file is as released. They need "Load out of date AddOns". The client is otherwise a stock 3.3.5a client: no MPQ, DBC or map changes. | the player's `Interface/AddOns/` |
| `client/README-PLAYERS.txt` | The players' notes from the release. | |
| `MANIFEST.sha256` | sha256 of every other file on this branch (`sha256sum -c MANIFEST.sha256`). | |

There is no auth-database SQL in this release.

## Changes on top of v1.4.0

Everything below was added to or changed in the v1.4.0 release files after the move into this
layout. `git log` holds each change with its reason.

**Added or replaced from the Dad's MMO Lab server (not in the release):**

- `src/UnboundMulticlassBridge.cpp`: the C++ talent bridge by ValentineSin, with the server's
  rank handling. It replaces `unbound_addon_sync.lua` and `unbound_talent_data.lua`, which are removed.
- `src/UnboundReagentFree.cpp`: free casting reagents and instant summons, each off by default.
- `lua_scripts/dml_autobuff.lua` (gated by `Unbound.AutoBuff`) and
  `data/sql/db-characters/02_dml_autobuff_kv.sql` (its table).
- `src/mod-multiclass-summons/multiclass_pet_fix.cpp`: the Call Pet fix on top of bdodroid's file.
- Five client addon files replaced with the server's later versions:
  `multiclass-talents-ui/Core.lua` and `UnboundSpellbook/{ClassData.lua, Core.lua, UI.lua, UnboundSpellbook.toc}`.
- `data/sql/db-world/15_unbound_mentor_spawns.sql`: the Mentor stands beside the bank of each capital (Stormwind, Ironforge,
  Darnassus, Exodar, Orgrimmar, Undercity, Thunder Bluff, Silvermoon) and in Dalaran's Runeweaver Square, spawn ids
  9000101-9000109 only (later Unbound files must not ship spawn ids above 9000109: the core numbers a new
  `.npc add` spawn MAX(guid)+1, so those ids are taken). A server that already placed a Mentor by hand with
  `.npc add 900001` gets a second one in the same city (harmless; remove the hand-placed one with `.npc delete`).
  The Mentor Stone still works anywhere. It names the creature table's `id` column, as the core does
  since its 2026_06_16_00 update.
- `src/UnboundSystem_loader.cpp`: calls the three new registration functions.
- `conf/mod_unbound.conf.dist`: the three switches (`conf/mod_ale.conf` and `conf/worldserver.conf.unbound` are
  from the release).

**Fixes** for problems players reported in #wow-unbound and in this repository's issues:

| File | Change |
|---|---|
| `src/UnboundSystem.cpp` | **A deleted character's Unbound rows are deleted with it.** AzerothCore gives the next new character `MAX(guid)+1` after a restart, which is the deleted character's GUID when it was the newest, so a new level-1 character used to start with the deleted one's unlocked classes. `OnPlayerDeleteFromDB` deletes the character's rows from `unbound_character_unlocks`, `unbound_character_talents` and `dml_autobuff_kv` in the core's own delete transaction. At each start, before the world opens, a sweep removes rows whose GUID has no character and logs `[UNBOUND] Orphan sweep done: …`. Each table is checked for before it is touched (the core stops the worldserver on a query against a missing table), and the sweep does nothing when the `characters` table cannot be read or is empty. The tables are checked once per start, so a mass character delete (a bot reset) adds no extra queries. Rows of living, soft-deleted (restorable) and bot characters are never touched. |
| `src/UnboundSystem.cpp` | **An added rogue can pick pockets, and an added hunter can tame.** The core asks `Player::IsClass` before it hands out pickpocket loot, before Tame Beast tames and before a stable master offers the stable; the module now answers "rogue" for a character that knows Pick Pocket (921) and "hunter" (pet questions only) for one that knows Tame Beast (1515). Every other class question still gets the native class. |
| `src/UnboundSystem.cpp`, `src/mod-multiclass-summons/multiclass_pet_fix.cpp` | **Bots are recognised on the newest core.** The playerbots core renamed `WorldSession::IsBot()` to `IsHeadless()`, so a server built on it failed to compile here (and the summons guard quietly treated bots as players). Both files now ask whichever of the two the core has, and stop compiling if it has neither. |
| `lua_scripts/unbound_mentor.lua` | **Skills the Mentor grants are at their maximum on the newer mod-ale.** mod-ale #391 corrected the order in which `Player:SetSkill` passes its arguments, so the old call gave a new weapon, armour or class skill a value of 1 out of the level's maximum. The three grants now go through one helper that passes the same number as value and maximum, which grants the full value with either order. Skills a character already has are not touched. |
| `lua_scripts/unbound_mentor.lua` | **The Mentor re-checks a spell purchase on the server.** The browse menu only lists spells the character may buy, but the purchase handler never asked, so a gossip selection sent by a modified client bought any catalog spell, at any level, for the catalog price. The menu and the purchase handler now share one check (`SpellRefusal`: already known, below the spell's `req_level`, previous rank not learned), and a forged pick is refused with the Mentor's normal message (`You must reach level N to learn that ability.`) and charges nothing. |
| `data/sql/db-world/06_universal_skill_access.sql` | Row 10062 for Lockpicking (skill 633), like the weapon and armour rows: a character that adds rogue now gets the skill when it learns Pick Lock, keeps it across logins, and it rises to level × 5. |
| `data/sql/db-characters/01_unbound_characters.sql` | Its comment no longer says the unlock rows are never deleted. No schema change. |
| `core-patch/unbound-mana-regen.patch` | New: mana regeneration for a warrior, rogue or death knight who unlocks a mana class (see Layout). |

## SQL apply order

AzerothCore's updater applies module SQL from `data/sql/db-world/` and
`data/sql/db-characters/` at server start, in file-name order across the whole folder
(sub-folders included), so the order is by name:

1. World: `00_npc_setup.sql` (the Mentor's `creature_template` 900001). It must be in the
   world database before the worldserver starts, or the Mentor Lua fails at load.
2. World: `01_unbound_world.sql`, `02_fix_catalog_req_level.sql`, `03_creation_gift_spells.sql`,
   `04_catalog_druid_forms.sql`, `05_individual_purchase_prereqs.sql`,
   `06_universal_skill_access.sql`, `07_mentor_stone.sql`, `08_catalog_additions.sql`,
   `10_catalog_audit_fixes.sql`, `11_catalog_gap_additions.sql`, `12_mount_spell_fix.sql`,
   `13_flight_form_fix.sql`, `14_judgement_fix.sql`, `15_unbound_mentor_spawns.sql` (the Mentor's nine spawns;
   it deletes and re-inserts only spawn ids 9000101-9000109). There is no `09` in the release.
3. World: `base/multiclass_summons.sql` (safe to re-run: `DELETE` then `INSERT` of its own rows).
4. Characters: `01_unbound_characters.sql`, `02_dml_autobuff_kv.sql` (the auto-buff script's table, created here so it always exists).

The v1.4.0 installer instead piped each file into `mysql` itself, in this order: the summons
SQL, `01` to `14`, the characters file, and `npc_setup.sql` last.

The files are written to be re-run safely (`INSERT IGNORE`, `ON DUPLICATE KEY UPDATE`,
`CREATE TABLE IF NOT EXISTS`, an `information_schema` guard in `05`). Some of them rewrite
global world tables: `playercreateinfo_spell_custom`, `skillraceclassinfo_dbc` (rows with
ID 10000 and up) and `playercreateinfo_item` (the Mentor Stone, item 900100, for every new
character).

The Mentor already stands in the eight capitals and Dalaran (`15_unbound_mentor_spawns.sql`). A GM can add
another anywhere in game with `.npc add 900001`.

## What else a server needs

- **AzerothCore with mod-playerbots.** The release was tested against core `e98e7a97e3f2`
  (Playerbot branch, ACDB 335.16-dev, 2026-05-29). The core patch also applies cleanly, with
  line offsets only, to `mod-playerbots/azerothcore-wotlk@7f12e89ee5f467a50e62eba1d525eac7dc953d03`
  (`git apply --check` passes).
- **Do not also install bdodroid's standalone `mod-multiclass-summons`** on the same server. This
  branch already carries its sources, and a second copy defines the same loader symbols, so the
  worldserver fails to link.
- **Upgrading an old v1.4.0 install in place** (rather than installing fresh) leaves the old
  `unbound_character_talents` rows unmigrated: the C++ bridge stores talents in the core's
  `character_talent`. A fresh Yu'lon install is not affected.
- **mod-ale** (the Lua engine): `azerothcore/mod-ale` at
  `1cb86c9600260c3731c96dc3c98d25b4fc3f2153`, the commit the installer clones.

## Credits

Wrath Unbound is by DaddyCool, Dad's MMO Lab.

`src/mod-multiclass-summons/` and `data/sql/db-world/base/multiclass_summons.sql` are by bdodroid, used with permission:
<https://github.com/bdodroid/mod-multiclass-summons>, commit
[`6001603bfe038204b73d0d5878ac3e1f24dda915`](https://github.com/bdodroid/mod-multiclass-summons/commit/6001603bfe038204b73d0d5878ac3e1f24dda915).

The Call Pet fix in `src/mod-multiclass-summons/multiclass_pet_fix.cpp` is by pjerra / Dad's MMO Lab, on top of bdodroid's file.

`src/UnboundMulticlassBridge.cpp` is based on the multi-class talent bridge by ValentineSin
(Wrath-Unbound-Multiclass-Mentor-Fix, posted in #wow-unbound on 2026-08-11), with the rank
handling added by Dad's MMO Lab.

`core-patch/unbound-mana-regen.patch` follows Decon White's mana-regeneration fix (posted in
#wow-unbound on 2026-09-22 and 2026-10-07): read the spirit regeneration of a class without
mana from the priest's row.

`UnboundSpellbook/DATA_CREDITS.txt` credits
the trainer data to "What's Training? WotLK" by anhility (MIT) and the talent ranks to
Talented_WoTLK by bkader.
