# Wrath Unbound v1.4.0 ("Shadow Release")

Wrath Unbound is a multi-class mod for a Wrath of the Lich King (3.3.5a) AzerothCore
server with mod-playerbots. A character unlocks extra classes at an NPC, "The Mentor",
buys those classes' spells, and spends talent points across the trees of every class it
has unlocked. Death Knight can only be a character's base class; the other nine classes
can be unlocked. Playerbots stay single-class.

- **Author:** DaddyCool, Dad's MMO Lab.
- **Version:** v1.4.0, "Shadow Release", released 2026-07-25.
- **This branch:** an orphan branch of `DadsMmoLab/dads-mmo-lab` that holds only Unbound's
  files, so that a server installer can pin one commit of it.

## Where these files came from

The release was posted in the Dad's MMO Lab Discord, channel #wow-unbound, on 2026-07-25
as three attachments. It was never published on GitHub; the copy under `guides/unbound-wrath/`
on the `main` branch of this repository is the older v1.2.2.

| Original file | sha256 |
|---|---|
| `install-wrath-unbound-addon.sh` (installer, `WIZARD_VERSION="1.4.0"`) | `edb949814c6212ef620ffe0bb4ec11bf9b1d9e93275125c1838bd478396df7fd` |
| `WrathUnbound-Addons.zip` (client addons) | `eb36d2ebaad96910e05cdfbf8db11a9cbfad16c18078b3725226eff844ebc686` |
| `README-PLAYERS.txt` | `f567013cce4c8c423826f6bc5cc6a48846ce79718df4de3d3c5a5c4e93537f3b` |

The installer carries its whole server payload as quoted shell heredocs. Every server file
on this branch is one of those heredocs, byte for byte, laid out by what it is rather than
where the installer wrote it. The installer itself is not on this branch. The client
addons are the zip's contents, unzipped unchanged. The only files written for this branch
are this README, `.gitattributes`, `MANIFEST.sha256` and the two files in `conf/`, which
spell out the settings the installer writes into existing config files.

## Layout

| Path | What it is | Where the installer put it |
|---|---|---|
| `core-patch/unbound-core-access.patch` | The AzerothCore core patch: 6 files (`Player.h`, `Player.cpp`, `PlayerQuest.cpp`, `PlayerStorage.cpp`, `Trainer.cpp`, `ConditionMgr.cpp`). It adds `Player::m_unboundClassMask` with `Get`/`SetUnboundClassMask()` and ORs it into the class checks for trainers, spells, quests, items and `CONDITION_CLASS`. The worldserver does not compile with `mod-unbound` unless this patch is applied. | `modules/mod-unbound/unbound-core-access.patch`, then `git apply` at the server root |
| `modules/mod-unbound/src/` | The `mod-unbound` C++ module: `UnboundSystem.cpp` (rage/energy for unlocked power types, keeping a Lua-set mana pool, and on login building the class mask from `unbound_character_unlocks` and granting weapon and armour proficiency; bots are skipped) and its loader `UnboundSystem_loader.cpp` (`Addmod_unboundScripts()`). AzerothCore picks a module up from its `src/` folder; the release ships no `CMakeLists.txt` and no module `.conf.dist`. | `modules/mod-unbound/src/` |
| `modules/mod-multiclass-summons/` | bdodroid's `mod-multiclass-summons` C++ module, used with permission (see Credits). It fixes Warlock, Mage and Death Knight pet and mount conflicts for multi-class characters and lets them field several guardians at once; playerbots are excluded at runtime. Its `data/sql/db-world/base/multiclass_summons.sql` registers the `spell_summon_pet_override` spell script on spells 688, 697, 712, 691, 30146, 70907, 70908, 46584 and 52150. The five files are the release's copy, byte-identical to `bdodroid/mod-multiclass-summons@6001603bfe038204b73d0d5878ac3e1f24dda915` (that commit's `README.md` was not part of the release and is not here). | `modules/mod-multiclass-summons/` |
| `lua_scripts/` | The ALE (Eluna) Lua scripts: `unbound_mentor.lua` (the Mentor and the Mentor Stone), `unbound_addon_sync.lua` (the bridge to the client talent addon; it creates `unbound_character_talents` itself) and `unbound_talent_data.lua` (talent data). On a successful start the world log prints `[UNBOUND] Prereq map built.` | `env/dist/etc/modules/lua_scripts/` |
| `sql/world/` | SQL for the world database (`acore_world`). | `modules/mod-unbound/data/sql/db-world/` and `modules/mod-unbound/npc_setup.sql` |
| `sql/characters/` | SQL for the characters database (`acore_characters`). | `modules/mod-unbound/data/sql/db-characters/` |
| `conf/mod_ale.conf` | The `mod_ale.conf` the installer writes when the server has none (`ALE.Enabled = 1`, `ALE.ScriptPath = "/azerothcore/env/dist/etc/modules/lua_scripts"`). | `env/dist/etc/modules/mod_ale.conf` |
| `conf/worldserver.conf.unbound` | `ValidateSkillLearnedBySpells = 0`, which the installer sets in `worldserver.conf`. Without it AzerothCore removes cross-class spells from every character at login. | `env/dist/etc/worldserver.conf` |
| `client/Interface/AddOns/` | The three client addons: `multiclass-talents-ui` (2.9.27-unbound-gm; `/mc`), `multiclass-resources` (1.3; `/mcr`) and `UnboundSpellbook` (0.3; `/usbk`, `/usbkrescan`). They need "Load out of date AddOns". The client is otherwise a stock 3.3.5a client: no MPQ, DBC or map changes. | the player's `Interface/AddOns/` |
| `client/README-PLAYERS.txt` | The players' notes from the release. | |
| `MANIFEST.sha256` | sha256 of every other file on this branch (`sha256sum -c MANIFEST.sha256`). | |

There is no auth-database SQL in this release.

## SQL apply order

The installer pipes each file into `mysql` in this order (it does not rely on AzerothCore's
own updater for module SQL). Within each folder, file-name order is the installer's order.

0. World: `modules/mod-multiclass-summons/data/sql/db-world/base/multiclass_summons.sql`,
   applied when the installer stages that module. It sits where AzerothCore's updater also
   applies module SQL at start-up, and it is safe to re-run (`DELETE` then `INSERT` of its
   own rows).
1. World: `01_unbound_world.sql`, `02_fix_catalog_req_level.sql`, `03_creation_gift_spells.sql`,
   `04_catalog_druid_forms.sql`, `05_individual_purchase_prereqs.sql`,
   `06_universal_skill_access.sql`, `07_mentor_stone.sql`, `08_catalog_additions.sql`,
   `10_catalog_audit_fixes.sql`, `11_catalog_gap_additions.sql`, `12_mount_spell_fix.sql`,
   `13_flight_form_fix.sql`, `14_judgement_fix.sql`. There is no `09` in the release.
2. Characters: `01_unbound_characters.sql`.
3. World: `npc_setup.sql` (the Mentor's `creature_template` 900001). It must be in the world
   database before the worldserver starts, or the Mentor Lua fails at load.

The files are written to be re-run safely (`INSERT IGNORE`, `ON DUPLICATE KEY UPDATE`,
`CREATE TABLE IF NOT EXISTS`, an `information_schema` guard in `05`). Some of them rewrite
global world tables: `playercreateinfo_spell_custom`, `skillraceclassinfo_dbc` (rows with
ID 10000 and up) and `playercreateinfo_item` (the Mentor Stone, item 900100, for every new
character).

After the first start, a GM spawns the Mentor once in game with `.npc add 900001`.

## What else a server needs

- **AzerothCore with mod-playerbots.** The release was tested against core `e98e7a97e3f2`
  (Playerbot branch, ACDB 335.16-dev, 2026-05-29). The core patch also applies cleanly, with
  line offsets only, to `mod-playerbots/azerothcore-wotlk@7f12e89ee5f467a50e62eba1d525eac7dc953d03`
  (`git apply --check` passes).
- **mod-ale** (the Lua engine): `azerothcore/mod-ale` at
  `1cb86c9600260c3731c96dc3c98d25b4fc3f2153`, the commit the installer clones.

## Credits

Wrath Unbound is by DaddyCool, Dad's MMO Lab.

`modules/mod-multiclass-summons/` is by bdodroid, used with permission:
<https://github.com/bdodroid/mod-multiclass-summons>, commit
[`6001603bfe038204b73d0d5878ac3e1f24dda915`](https://github.com/bdodroid/mod-multiclass-summons/commit/6001603bfe038204b73d0d5878ac3e1f24dda915).

`UnboundSpellbook/DATA_CREDITS.txt` credits
the trainer data to "What's Training? WotLK" by anhility (MIT) and the talent ranks to
Talented_WoTLK by bkader.
