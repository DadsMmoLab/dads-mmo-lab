# T30 Half 1 — the Penqle core and the TortoiseBots module, measured on `yulon-arch`, 2026-09-11

The measuring half of T30. Nothing in the catalog is changed here: this is the ticket's six questions
answered against a real clone, a real Docker build and a real world run, with a file behind every
sentence. Every capture in this directory opens with `date -Is` from the box.

| | |
| --- | --- |
| box | `yulon-arch`, 20 GB, docker; all work under `~/t30/`, images `t30/…`, containers `t30-…` |
| core | `tortoise-wow/tortoise-wow` branch `bot-helpers` at **`9980181ce9aa5940b6125a5991cc754148ed9b82`** (2026-09-10, PR #475) — `01-revisions.txt` |
| module | `Sagiroth/TortoiseBots` main at **`fd7ec9ec7659035cfc3ea75d542c8683005525de`** (2026-09-11, PR #128), cloned to `modules/TortoiseBots` — `01-revisions.txt` |
| addons | `TortoiseBotsManager` `65ef0b2f`, `TortoiseGMManager` `ec11dd23`, read only — `01-revisions.txt`, `10-addons.txt` |
| build | `docker/Dockerfile.build` on `t30/base` (`docker/Dockerfile.base`), `make -j4` — **2044 s**, image **1.12 GB** — `04-build.log`, `12-image-contents.txt` |
| run | database up, confs written by hand, world up to its ready line, `.bot` probed on the console, down — `08a`, `08-world-run1.txt`, `08d-world-run2.txt`, `11-run-database.txt` |

## 1. The build, and the flags the new core does not know

The cmake line that worked, exactly as `docker/Dockerfile.build` runs it:

```
cmake .. \
    -DCMAKE_INSTALL_PREFIX=/opt/tortoise \
    -DMODULES=static -DMODULE_TORTOISEBOTS=static \
    -DUSE_EXTRACTORS=ON -DUSE_SCRIPTS=ON -DUSE_STD_MALLOC=ON \
    -DDEBUG_SYMBOLS=OFF -DUSE_ANTICHEAT=OFF -DALLOW_TURTLE_ADDONS=ON
```

Three of the flags `catalog/installers/wow-tortoise/native/Dockerfile.tmpl` passes are gone. The old
flag line was run against the new core on its own, configure only, and CMake named them
(`03-cmake-probe-old-flags.txt`, tail):

```
CMake Warning:
  Manually-specified variables were not used by the project:

    BUILD_PLAYERBOTS
    MODULE_MOD_DUNGEON_CLEAR
    MODULE_MOD_PLAYERBOTS
```

Everything else in that line is still a real option of the new `CMakeLists.txt`: `USE_STD_MALLOC`
(`:38`), `USE_ANTICHEAT` (`:40`), `USE_SCRIPTS` (`:42`), `MODULES` (`:43`), `USE_EXTRACTORS` (`:44`),
`ALLOW_TURTLE_ADDONS` (`:52`), `DEBUG_SYMBOLS` (`:57`) — `02-layout.txt` and `03-…` both read them.
`MODULES` still defaults to `disabled`, so the module needs naming either globally or per-module.

The effective module mode CMake prints, with `-DMODULES=static -DMODULE_TORTOISEBOTS=static`
(`04-build.log`):

```
-- Build modules         : static
--   TortoiseBots: static (MODULE_TORTOISEBOTS=static)
-- TortoiseBots source: /src/modules/TortoiseBots commit fd7ec9ec7659035cfc3ea75d542c8683005525de (clean)
```

and with the old line's `-DMODULES=disabled` plus the dead `MODULE_MOD_PLAYERBOTS`, it prints
`TortoiseBots: disabled (MODULE_TORTOISEBOTS=default)` — a bot-less server that builds and boots
and looks entirely healthy, the same trap the old template's comment describes for the old fork.

**Cost.** 2044 s wall clock at `make -j4` on this box, `EXIT=0` (`04-build.log`, last line).
Peak memory 4741 MB used of 19990 (`04b-memory-during-build.txt`, sampled every 30 s through the
build); the old fork's `make_jobs 2` is not needed here for memory's sake. The image is 1.12 GB,
`t30/base` (toolchain only) also 1.12 GB, and the two share the `ubuntu:24.04` and apt layers.

## 2. What the build installs

**The extractors all still exist by the names `catalog.json:1000-1071` runs** (`12-image-contents.txt`):
`mapextractor`, `vmapextractor`, `vmap_assembler`, `MoveMapGen`, beside `mangosd` and `realmd`. Their
argv are unchanged too — `mapextractor -i/-o/-e` (`tools/extractor/System.cpp:195-205`),
`MoveMapGen --silent --doNotFilterDeepWater --offMeshInput --settingsInput`
(`tools/mmap/src/generator.cpp:87-90`, `:214-252`).

**One path under them moved.** The whole tools tree is now top-level `tools/`, not `src/tools/`
(`02-layout.txt`), and nothing installs `offmesh.txt` / `mmapSettings.txt` into the prefix — so
`catalog.json:1058-1062`'s `/opt/tortoise/src/tools/mmap/offmesh.txt` does not exist in the image
(`12-image-contents.txt`: `ls: cannot access '/opt/tortoise/src/tools/mmap'`). The old template
copied them in by hand from `/src/tools/mmap`; the new one has to copy from `/src/tools/mmap` under
the new spelling.

### The conf keys the catalog writes, against this tree

Every key is from `05-conf-keys.txt`, which probes the shipped `.dist` files line by line.

| file | key (`catalog.json`) | on the new tree |
| --- | --- | --- |
| `mangosd.conf` | `LoginDatabase.Info`, `WorldDatabase.Info`, `CharacterDatabase.Info`, `LogsDatabase.Info` | **unchanged**, `:40-43` |
| | `Database.AutoUpdate.Path` | **unchanged**, `:48` |
| | `DataDir` | **unchanged**, `:12` |
| | `WorldServerPort` | **unchanged**, `:88` |
| | `GameType` | **unchanged**, `:604` (default is now `6`, not `0`) |
| | `GM.LoginState`, `GM.StartLevel` | **unchanged**, `:1577`, `:1609` |
| | `AutoHonorRestart` | **unchanged**, `:707`, still read at `src/game/World.cpp:1139`, still defaults `1` |
| | `AutoRestart.MaxServerUptime` | **unchanged**, `:148` |
| | `SOAP.Enabled`, `SOAP.IP`, `SOAP.Port` | **GONE — the key, the conf block and the whole subsystem** |
| `realmd.conf` | `LoginDatabaseInfo`, `ForcePinAccountRank` | **unchanged**, `:10`, `:143` |
| `aiplayerbot.conf` | `AiPlayerbot.MinRandomBots`, `AiPlayerbot.MaxRandomBots` | **unchanged**, `:46-47` |
| | `AiPlayerbot.RandomBotAccountCount` | **GONE** — the string does not occur anywhere in the module |
| | `AiPlayerbot.RandomBotAccountPrefix` (observability, `catalog.json:1289`) | **unchanged**, `:53`, default still `RNDBOT` |

New keys this stack needs that no catalog block writes today, all present in the shipped
`mangosd.conf.dist.in`: `Database.AutoUpdate.AllowedModules` (`:56`, `"all"`),
`Database.AutoUpdate.AuthUpdateName` / `CharUpdateName` / `WorldUpdateName` (`:49-51`,
`"auth"` / `"character"` / `"world"`), `Database.AutoUpdate.SortByName` (`:62`, `1`),
`Console.Enable` (`:1999`), `HttpApi.Enable` (`:96`).

New conf file: `tortoise_bots.conf`, from `modules/TortoiseBots/conf/tortoise_bots.conf.dist`. It is
a section-header conf (`[TortoiseBotsConf]`) of module toggles, three one-shot self-tests, a log
level and the three observability keys; nothing in it is load-bearing for a plain install
(`05-conf-keys.txt`).

**Where `RandomBotAccountCount` went.** The module discovers `RNDBOT*` accounts by prefix rather than
being told a count, and optionally creates the deficit itself: `AiPlayerbot.RandomBotAutoCreate`
(`:89`, default `0`), with `RandomBotAutologin` (`:22`) and `RandomBotLoginAtStartup` (`:25`) both
default `0` and `AiPlayerbot.Enabled` default `0` (`:12`). A conf block that writes only the old
three keys produces a module that loads and does nothing.

**Two things break the conf stage itself**, both read off the built image (`12-image-contents.txt`):

* `/opt/tortoise/etc/aiplayerbot.conf` is installed **without** a `.dist` suffix
  (`modules/TortoiseBots/TortoiseBots.cmake:40-46` `configure_file`s it to its live name).
  `conf.materialise()` (`families/conf.py:208-232`) copies `"<name>.dist"` out of `source_dir` and
  raises `InstallerError` when the image does not ship one — so `aiplayerbot.conf` in the catalog's
  conf table would refuse this image. The old fork's image shipped `aiplayerbot.conf.dist`; the same
  capture lists the old image's `etc/` beside the new one to show the change.
* `tortoise_bots.conf.dist` lands in `etc/modules/`, not `etc/`, so a table entry for it would need
  a different `source_dir` or a nested name.

The old image's `ahbot.conf.dist` is gone as well; AHBot is now a `.bot ah` subcommand (finding 5).

## 3. The databases

`06-sql-layout.txt` is the whole comparison; `07-db-up.txt`, `08b-…` and `11-run-database.txt` are it
pressed against a live MariaDB.

What is the same: `sql/create_databases.sql` still creates exactly `tw_char`, `tw_logon`, `tw_logs`,
`tw_world` (`:23`, `:2063`, `:3476`, `:3513`); `sql/base/` is still 190 per-table `tw_world_*.sql`
dumps; `sql/database_updates/` still holds `character/` and `world/`, and `Database.AutoUpdate.Path`
still points at it.

What is not:

| the plan's path (`catalog.json`) | on this tree |
| --- | --- |
| `sql/character_updates/*.sql` (`:1160`, phase `character updates`, `on_error: fail`) | **ABSENT** — the directory does not exist |
| `modules/mod-playerbots/sql/characters/*.sql` (`:1146`) | **ABSENT** |
| `modules/mod-playerbots/sql/world/*.sql`, `.../world/classic/*.sql` (`:1152-1153`) | **ABSENT** |
| the fork's 173 world migrations | 144, plus **1** character migration (`20260817151028_character.sql`) |

So three of the plan's six phases name directories that are gone, one of them `on_error: fail`.

**The self-colliding migration is still in the tree and no longer collides.** `20260903063722_world.sql`
is there, still with 14 plain `INSERT INTO` and no `INSERT IGNORE` (the `sed` in `Dockerfile.tmpl` was
not applied here), and its `spell_proc_event` row `44070` is still the one that cancelled the server
on m910q on 2026-09-08. On this tree it applies: the run imported `create_databases.sql` and all 190
base dumps, then the updater ran **144 world + 1 character migrations with no failure**
(`08b-autoupdater-run1.txt`: 148 `Attempting to execute update` lines, no `failed to apply`), and
`tw_world.migrations` holds 147 rows afterwards (`11-run-database.txt`). The base this tree ships does
not carry that row (`06-sql-layout.txt`), and the migration the old tree collided against is not here.
The `INSERT IGNORE` rewrite has nothing to fix on this pin.

### The module's SQL, and the one directory name that decides whether a fresh install starts

The module ships `data/sql/char/` (5 migrations) and `data/sql/world/` (3), which between them create
the `ai_playerbot_*` tables (`06-sql-layout.txt`). The core applies module SQL in
`AutoUpdater::ProcessModuleUpdates` (`src/shared/Database/AutoUpdater.cpp:245-283`), from

```
modulesPath / moduleName / "data" / "sql" / targetFolder
```

where `modulesPath` is **`TW_SOURCE_MODULES_DIR`, compiled in as `${CMAKE_SOURCE_DIR}/modules`**
(`CMakeLists.txt:548`, `AutoUpdater.cpp:503-506`) — the *source* tree, not the install prefix — and
`targetFolder` is the same `Database.AutoUpdate.CharUpdateName` / `WorldUpdateName` the core uses for
its own migrations, i.e. `"character"` and `"world"` from the shipped conf. A module folder that does
not exist is skipped silently (`AutoUpdater.cpp:268-270`).

`make install` knows this and renames on the way in: `TortoiseBots.cmake:61-69` installs
`data/sql/char/` to `${CMAKE_INSTALL_PREFIX}/modules/TortoiseBots/data/sql/**character**`, with a
comment saying exactly why. The image has that directory (`12-image-contents.txt`). **But nothing ever
reads it**, because the compiled-in path is the source tree, where the directory is still called
`char`.

That is not a theory. Run 1 was the image as built, with no source tree mounted beyond the one the
Dockerfile copied. The world applied every core migration, then

```
[DB Auto-Updater] Processing module TortoiseBots updates from /src/modules/TortoiseBots/data/sql/world.
[DB Auto-Updater] Found 3 possible migrations for world.
```

and nothing at all for `character` — and then died before its ready line (`08-world-run1.txt`):

```
SQL: select clazz, spec, lvl, slot, quality, item from ai_playerbot_equip_cache
[1146] Table 'tw_char.ai_playerbot_equip_cache' doesn't exist
Your database structure is not up to date. …
/src/src/shared/Database/DatabaseMysql.cpp:190: Error: Assertion in HandleMySQLError failed: false
  … RandomItemMgr::BuildEquipCache … PlayerbotAIConfig::Initialize … TortoiseBots::BotHostAdapter::OnStartup
terminate called after throwing an instance of 'std::runtime_error'
```

Run 2 changed one thing — `data/sql/char` copied to `data/sql/character` in the mounted source tree —
and the same image came up (`08e-autoupdater-run2.txt`, `08d-world-run2.txt`):

```
[DB Auto-Updater] Processing module TortoiseBots updates from /src/modules/TortoiseBots/data/sql/character.
[DB Auto-Updater] Found 5 possible migrations for character.
```

**So a Tortoise image built from this pin must keep a source `modules/` tree at the path the binary
was compiled with, and the module's char folder must be spelled the way the conf asks.** Either is a
Half 2 decision; neither is optional. After the fix the catalog's own verify query is satisfied:
11 `ai_playerbot%` tables in `tw_world` against a `min` of 10 (`catalog.json:1240-1244`,
`11-run-database.txt`), and 5 more in `tw_char`.

## 4. Eluna, the Lua bridge, and the command channel

**The new core has no Eluna and nothing that could load one** (`09-eluna-and-bot-surface.txt`): there
is no `src/modules` directory at all, nothing in the tree is named Eluna, no `CMakeLists.txt` or
`.cmake` mentions it, and no Lua engine is among the vendored deps. The old fork refused to configure
without the checkout (`pylauncher/tests/test_catalog.py:959-965`); this tree configured to completion
with none present (`03-cmake-probe-old-flags.txt`).

Where the catalog names it, and what happens to each:

| where | what it says | on the new stack |
| --- | --- | --- |
| `catalog.json:961-965` | source `ElunaLuaEngine/Eluna` pinned `1b06f28f`, cloned to `src/tortoise-wow/src/modules/Eluna` | **dead** — the destination path has no parent and no consumer |
| `pylauncher/tests/test_catalog.py:474`, `:942-980` | the guard that this source exists and is pinned | follows the source when it goes |
| `families/cmangos.py` | — | never mentions Eluna, ALE, `playerbots` or `rndbot` at all |

**And Tortoise never used a Lua bridge in the first place.** Yu'lon's bridge machinery is
AzerothCore-only by a hard guard: `party.InstallParty.for_entry_is_possible()` ends
`return entry.observability is not None and entry.id == "wow-wotlk"` (`pylauncher/yulon/party.py:3066`),
the Tortoise controller never passes `my_party=` (`ui/controller_view.py:1622-1757` against
`:1152`), and `party.deploy()` — the thing that would copy `pylauncher/lua/party/*.lua` anywhere —
has no non-test caller. Its constants are absolute AzerothCore container paths (`party.py:98`, `:102`,
`:104`).

What Tortoise's surfaces actually use is the **command channel**, and that is the thing this core
breaks. Every Play-tab write is a verb from the catalog sent over `operations.channel: "soap"`
(`catalog.json:1264-1274`): `rename` (`play.py:434-455`), `tele name` (`play.py:411-413`),
`send money` / `send items` (`play.py:459-476`), `revive` (`play.py:456-457`); set-level is declared
absent (`catalog.json:1253-1254`). Bot browsing is a direct SQL read by account prefix, no channel
(`dbreads.py:214-239`, `botlist.py:154-181`).

**There is no SOAP in this core.** The only occurrence of the string anywhere in `src/` is a comment,
`src/game/World.h:799` (`05-conf-keys.txt`); the conf ships no `SOAP.*` key, and no gsoap is vendored.
What the core offers instead is `Console.Enable` (`mangosd.conf.dist.in:1999`) and an `HttpApi.Enable`
(`:96`) that is not a channel kind Yu'lon knows. The catalog already has the answer for a core like
this: `operations.channel: "attach"`, whose validator docstring says in as many words *"this core
links neither gsoap nor RASocket, so its only way in is the console the Console tab already types at"*
(`catalog/catalog.py:1395-1416`) and whose transport, `AttachChannel`, was written for Tortoise on
2026-09-07 when its mangosd was in exactly this state (`yulon/channel.py:88-105`). Moving back to
`attach` means `port`, `namespace`, `gm_level` and `enable_conf` must all be dropped from the block
(`catalog.py:1395-1416`).

## 5. The run

Database up, confs written by hand the way the catalog's conf block would
(`08a-conf-and-start.txt` shows every key with the password masked), world up, console probed, down.

**The ready line is the first alternative of our regex, unchanged** (`08d-world-run2.txt`):

```
World server is up and running! Loading time: 1 minutes 13 seconds
```

emitted at `src/game/World.cpp:2447`. `ready.world`'s other three alternatives do not occur; the
`fatal` pattern's negative lookahead still earns its keep, because the module prints
`Could not open bot log file ../logs/bot_events.csv (No such file or directory). Logging to it is off
for this run.` on nearly every tick.

The module's own lines, in order (`08d-world-run2.txt`):

```
Bot configuration read from /opt/tortoise/etc/aiplayerbot.conf.
TortoiseBots: native random-bot pool loaded (0 candidates, target 5, startup 1, autoCreate 1)
TortoiseBots: native module loaded (AI enabled)
```

**`RNDBOT*` autologin.** With `AiPlayerbot.Enabled = 1`, `MinRandomBots = MaxRandomBots = 5`,
`RandomBotAutoCreate = 1`, `RandomBotAutologin = 1`, `RandomBotLoginAtStartup = 1`, the module found
no existing pool, created **one** account `RNDBOT111875` and **five** characters on it, and logged all
five in as headless sessions. Read out of the database while the world was still up, immediately
before the stop: 5 characters, all `online = 1` (`11-run-database.txt`; the shutdown then set them
back to 0, `08d-world-run2.txt`). The prefix the catalog's observability block reads is
the one the module writes, so `dbreads.bot_clause()`'s `UPPER(username) LIKE 'RNDBOT%'` finds them
unchanged.

**The client data question is answered yes.** The old fork's extraction at `~/tortoise-vm/data`
(3.6 GB, `Buildings dbc maps mmaps vmaps`) was mounted read-only at `/opt/tortoise/data` and the new
core took it: `VMap data directory: /opt/tortoise/data/vmaps.`, `VMap support included…`,
`MMap pathfinding enabled.`, `Using enUS DBC locale as default.` — and the bots pathfound on it
(`08d-world-run2.txt`). Same 1.18.1 / build 7272 client, no re-extraction needed for a measurement.

### The `.bot` surface on the console channel

`.bot` is intercepted for **every** `ChatHandler`, console included, before command-table lookup and
before any security check: `ChatHandler::ExecuteCommand` runs the `AllCommandScript` registry first
(`src/game/Chat/Chat.cpp:1585-1592`), and the module's `BotChatAdapter::CanExecuteCommand` consumes
the name `bot` (`host/BotChatAdapter.cpp:15-30`). Console commands are typed without the leading dot
(`src/mangosd/CliRunnable.cpp:563-588`, `SEC_CONSOLE`). Measured, verbatim (`08d-world-run2.txt`):

| typed at the console | answer |
| --- | --- |
| `bot` | `Usage: .bot add/remove/logout/roster/action/follow/invite/uninvite/stay/guard/free/ready/attack/interrupt/formation/list/stats/status/lease/pullback/summon/command/ah` |
| `bot help` | `TortoiseBots: Enabled` |
| `bot list` | `You must be in-game.` |
| `bot roster` | `TBM:ROSTER_ERROR\|not-in-game\|You must be in-game.` then `You must be in-game to request a bot roster.` |
| `bot stats` | `You must be in-game.` |
| `bot status` | `Usage: .bot status <online bot name> (same account only)` |
| `bot add Nobody` | `You must be in-game to add a bot.` |
| `bot summon Nobody` | `You must be in-game.` |
| `bot ah help` | the six-line AHBot command list (`status`, `reload`, `rebuild`, `item …`) |
| `bot nosuchthing` | `Unknown bot command 'nosuchthing'. Try .bot help` |

So the console reaches the module, is answered by it, and **cannot drive a single bot**: every command
that touches a bot resolves its requester as `handler->GetSession()->GetPlayer()`
(`commands/BotCommands.cpp:120`, `:633`) and refuses when there is none. The one surface that answers
usefully without a player is `bot ah`. Note `bot roster` emits the addon's `TBM:` protocol line on the
console too, so the protocol is not client-only.

## 6. The addons

`clientdir.py` is a pure validator: 364 lines of `Check` tuples, no `open(..., "w")`, no `mkdir`, no
`shutil` — it does not even import it — and it never mentions `Interface` or `AddOns`.

**Yu'lon does have a writer, and it is manifest-driven:** `_ApplyEngine._client()`
(`pylauncher/yulon/apply.py:2173-2192`) copies a manifest's `client` step into
`client_dir / "Interface" / "AddOns" / name` for `dest: "addons"`, via `shutil.copytree(...)`. It is
game-agnostic (`manifest.py:197-206` `ClientFile`, `manifest.schema.json:24-56`), but two things stand
between it and Tortoise today: **no `manifests/wow-tortoise/*.json` declares a `client` array** (only
`wow-wotlk` does, e.g. `kegs/bmah.json:54-57`), and the Tortoise controller does not pass `client_dir`
into its applier (`ui/controller_view.py:1717-1745`, against tbc `:1441` and vanilla `:1603`), so
`apply.py:2175-2177` would skip the step with *"no client dir configured"*. Both are small, named
changes; there is nothing new to build.

What the addons need (`10-addons.txt`):

* **TortoiseBotsManager** — `## Interface: 11200`, 7 Lua files plus a `.toc`, `SavedVariables:
  TortoiseBotsDB`, `/tbm`. **Requires the server-side module**: it sends `.bot` transport commands and
  parses `TBM:` responses; its README says *"No module → addon loads but every action replies
  'TortoiseBots module not loaded' from the server."*
* **TortoiseGMManager** — `## Interface: 11200`, 6 Lua files plus a `.toc`, `SavedVariables:
  TortoiseGMManagerDB`, `/tgmm`. **Needs nothing server-side**: *"The addon is client-side only. It
  does not connect directly to MariaDB and does not bypass server permissions."* It is a palette over
  the ordinary GM chat commands.

Both declare interface `11200` (Vanilla 1.12) for the 1.18.1 / build 7272 client, which is what this
client reports.

## Deviations

* **Two world runs, not one.** Run 1 died before its ready line on the module-SQL directory name
  (finding 3) — a crash, not a completed run — and the ticket's point 5 asks for the ready line, the
  module lines, the `RNDBOT*` count and the `.bot` answers, none of which exist without a world that
  starts. Run 2 is the same image and the same confs with one directory copied. Both are captured;
  nothing was left running.
* **`t30/base` is a separate image** rather than a first stage of one Dockerfile, so the cheap
  configure-only probe in finding 1 could be run in a container off the toolchain without rebuilding.
  Both files are in `docker/`.
* **`AiPlayerbot` settings for the run are not the catalog's.** The catalog writes 500/500/100; the run
  used 5/5 plus the three enable keys, because the point was to see the pool mechanism work, not to
  populate a world. `RandomBotAccountCount` could not be written at all — it no longer exists.
* **The 59 MB raw log of run 1 is not committed.** `08-world-run1.txt` keeps everything it printed
  except the auto-updater's per-statement `SQL:` echo, and `08b-autoupdater-run1.txt` keeps every
  updater line; `04-build.log` likewise drops only its `Building CXX object …` progress lines, with
  the count of what was dropped stated at its head.
* **The DB password** was generated on the box into `~/t30/.db_password` (mode 600), passed to
  MariaDB, to the conf writer and to every query through the environment, and masked in
  `08a-conf-and-start.txt`. Every file in this directory was grepped for its literal value before the
  commit: zero hits.

## The box as left

Every `t30-…` container removed; `docker ps -a` holds only what was there before (the three stopped
`tortoise-*`, the five stopped `ac-*`). The two images stay, as the ticket allows:
`t30/tortoise-core:latest` 1.12 GB and `t30/base:latest` 1.12 GB, sharing their base and apt layers.
`~/t30/` holds the clones, the Dockerfiles, the scripts and the evidence; `~/tortoise-vm`, `~/y8`,
`~/clients` and Steam were not written to — the old fork's `data/` was mounted `:ro` and its image was
read once, through a throwaway container that was removed. Final `free -m`: 1162 MB used of 19990
(`04b-memory-during-build.txt`, tail).
