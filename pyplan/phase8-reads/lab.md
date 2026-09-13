# Lab reader — Phase 8 scoping, The Lab feature parity

Read-only. Nothing downloaded, nothing run but `git`/`grep`/`sed`/`awk`/`cat`/`ls`. The Lab was
neither downloaded nor executed.

## 0. Provenance and what "verified" means here

**Tree.** Worktree `C:\Users\perzi\dml-phase8`. Its HEAD is **`1a332a8d`** ("Phase 8 kickoff brief,
verbatim, so the scoping session is in the record"), **not** the pinned `7bc5ebd3` named in my task.
`git merge-base --is-ancestor 7bc5ebd3 HEAD` → yes: `7bc5ebd3` is the parent of HEAD, and the only
delta is the kickoff-brief commit. Every guide/manifest citation below is unchanged between the two
(the extra commit adds `pyplan/phase8-kickoff.md` only). Flagging it rather than silently reading a
different tree.

**Evidence labels used throughout, per rule 1:**

| Label | Meaning |
|---|---|
| RECORD (source-verified) | A memory record that names the source files it was read from and its date |
| RECORD (observed) | A memory record of something observed/measured, dated, but not source-read |
| GUIDE CLAIM | A line in `archive/guides/` — a claim *about* a server, not a measurement of one |
| REPO FACT | Something I read directly in this tree just now (a manifest, a file's existence) |
| INFERENCE | Mine. Marked inline. |
| UNVERIFIED | Nobody asked the machine. Distinct from "no". |

**The guides are archived and partly stale.** `archive/guides/wow-wotlk/README.md:3-18` says so
itself: *"**Archived — this is not the launcher's documentation.** … It is **not** documentation for
Yu'lon"*. It also says (`README.md:14-18`) the installer scripts and `wow-manage.sh` "now live in
`pylauncher/catalog/installers/wow-wotlk/`". REPO FACT: they do not. `ls
pylauncher/catalog/installers/wow-wotlk/` returns only `native/` (`base.yml.tmpl`,
`build.yml.tmpl`, `override.yml.tmpl`), and a repo-wide `find` for `wow-manage.sh`,
`*playerbots-launcher*`, `install-wow-wotlk*.sh` returns **nothing** — the Phase 7 bash deletion took
them. So the guides' own cross-references to the shell tooling are dead links, and the Gaming-Mode
launcher script that implemented the guides' auto-shutdown no longer exists in this tree to read.

---

## 1. What the guides say about The Lab — verbatim

Two places only. Repo-wide `grep -rn -i 'the lab\|0xVe1L'` over `*.md`/`*.py`/`*.sh`/`*.toml`/`*.json`
outside `pylauncher/` and `pyplan/` returns exactly the twelve lines below (plus one binary false
positive in `ALE-Kegs/SeasonOfDiscovery/Server Files/dbc/Spell.dbc`, and
`WoW-Wotlk-NETWORKING.md:311` which matches "the label on your router").

### `archive/guides/wow-wotlk/README.md`

```
  6	> installers, `wow-manage.sh`, and the third-party The Lab GUI. It is kept for reference and for
 52	- Includes the **Managing Your Server** section covering both `wow-manage.sh` and The Lab GUI
183	### The Lab — GUI App
184	**Platform: Steam Deck / Linux (AppImage)**
185	
186	A full graphical app for managing your server without a terminal — built for Gaming Mode and couch play.
187	
188	**Download:** [github.com/0xVe1L/the-lab](https://github.com/0xVe1L/the-lab) — grab `TheLab.AppImage` from the latest release.
189	
190	**Features:**
191	- Start / stop / restart with a live, readable server console
192	- **My Party** — build a 5-man bot group: pick class, role, spec, and level
193	- **Item database** — search any item and mail it to your character
194	- **Teleport** to any location or map coordinates
195	- **Module management** — toggle AzerothCore modules and tune settings in-app
196	- **Steam integration** — adds the server and WoW client to your Steam library automatically
197	- Auto-shuts down when you close WoW
198	
199	> **Already installed from this guide?** The Lab detects existing Dad's MMO Lab servers and migrates them in — your characters and data stay untouched.
262	- [The Lab GUI](https://github.com/0xVe1L/the-lab) — graphical server manager
```

### `archive/guides/wow-wotlk/WoW-WotLK-HOWTO.md`

```
277	### Option B — The Lab (GUI App — No Terminal Required)
278	
279	**The Lab** is a full graphical app for managing your server with clicks, not commands. It's built for Steam Deck Gaming Mode and works great from the couch.
280	
281	**Download:** [github.com/0xVe1L/the-lab](https://github.com/0xVe1L/the-lab) — grab `TheLab.AppImage` from the latest release.
282	
283	Features include:
284	- **Start / stop / restart** the server with a live, readable console
285	- **My Party** — build a 5-man bot group: pick role, class, spec, and level — The Lab spawns and gears them for you
286	- **Item database & in-game mail** — search any item and send it to your character instantly
287	- **Teleport** to any named location or map coordinates
288	- **Module management** — toggle AzerothCore modules on/off; tune their settings in-app
289	- **Steam integration** — adds the server and WoW client to Steam with artwork
290	- Auto-shutdown when you close WoW
291	
292	> **Already have a server from this guide?** The Lab detects existing Dad's MMO Lab installs and migrates them in — your characters and data are untouched.
```

That is the **entire** documented feature set in this repository. The guides give no mechanism for a
single one of them: no command text, no config path, no port. Everything mechanical below comes from
the memory records or is UNVERIFIED.

---

## 2. Feature entries

### F1 — Start / stop / restart with a live, readable console

- **What The Lab does.** GUIDE CLAIM `README.md:191`, `HOWTO.md:284`: *"Start / stop / restart with a
  live, readable server console"*. No further detail anywhere.
- **Mechanism on AzerothCore (WotLK).** UNVERIFIED for The Lab specifically. What the *guides* teach
  the user is `docker compose up -d` / `down` (`HOWTO.md:305-311`) and `docker attach $(docker ps
  --format '{{.Names}}' | grep worldserver | head -1)` for the console
  (`HOWTO.md:317`, `CONTROLS-1.md:240`, `CONTROLS-2.md:24`, `CREATE-ACCOUNTS.md:38`), with the
  detach warning *"Exit: Ctrl+P then Ctrl+Q — DO NOT press Ctrl+C (that stops the server)"*
  (`HOWTO.md:318`; `CONTROLS-2.md:33-48` is a whole section on it). RECORD (observed),
  `dml-launcher-windows-project.md` 2026-07-14: a "~140-command Tauri IPC surface recovered via
  `strings`" exists but the record does not name which of those commands drives the console, and
  the record lists `active_soap_url` among the settings keys — so The Lab has a SOAP channel too.
  INFERENCE (mine, unproven): "readable console" implies it does not use raw `docker attach`
  interactive mode, because that channel is a single-attacher PTY. Nothing establishes this.
- **Mechanism on the CMaNGOS lineage.** UNVERIFIED. The Lab is not documented against CMaNGOS at
  all. The guides' own CMaNGOS console is `docker attach tbc-mangosd` /
  `docker attach vanilla-mangosd` with a `mangos>` prompt (`wow-tbc/WoW-TBC-HOWTO.md:107,110`;
  `wow-vanilla/HOWTO-WOW-VANILLA.md:102,105`) — a different container name, a different prompt, and
  no `AC>` banner.
- **Client-side requirement.** None.
- **What the user sees.** GUIDE CLAIM: the ready banner. Steam-Deck Gaming Mode's is
  **`AZEROTH IS READY!`** (`HOWTO.md:233`, `CREATE-ACCOUNTS.md:29`); the console prompt is
  **`AC>`** (`CONTROLS-2.md:27`, `CREATE-ACCOUNTS.md:41`, `WoW-Playerbots-Windows-HOWTO.md:118`)
  and **`mangos>`** on the CMaNGOS games. Per memory `upstream-conventions-differ-per-fork`, treat
  each as a per-tree fact.

### F2 — My Party (5-man bot group: role, class, spec, level; spawned and geared)

- **What The Lab does.** GUIDE CLAIM `HOWTO.md:285`: *"**My Party** — build a 5-man bot group: pick
  role, class, spec, and level — The Lab spawns and gears them for you"*; `README.md:192` says
  *"pick class, role, spec, and level"* (same list, different order). RECORD (observed),
  `dml-launcher-windows-project.md` (2026-07-14, from `strings` on `TheLab.AppImage` v0.0.7):
  *"party presets are TOML (schema_version=1, wowhead talent codes)"* — so spec is stored as a
  wowhead talent code, and presets are a file format, not just UI state.
- **Mechanism on AzerothCore (WotLK).** **Not SOAP, not the console.** RECORD (source-verified),
  `my-party-soap-limitation.md`, researched **2026-07-15** against mod-playerbots master
  (`PlayerbotMgr.cpp` / `PlayerbotCommandScript.cpp`):

  > the `.playerbots bot add <Name>` command that adds a bot to your party is registered
  > `SEC_PLAYER, Console::No` and has a runtime guard `if (!m_session) { "You may only add bots from
  > an active session"; return false; }` — the master is always the calling session player, no
  > argument can target another/offline player. **So SOAP (console context) cannot build a party.**

  The same record names the constraints and the candidate mechanisms:

  > **Why:** … the real mechanism is one of: (1) in-game addon relay — app writes roster to an
  > addon's SavedVariables .lua, addon fires the commands on login (most likely;
  > whipowill/azcguy PlayerbotsPanel pattern); (2) `AiPlayerbot.BotAutologin=1` with bots on the
  > player's own/linked/guild account; (3) NOT the TCP CommandServer :8888 (telemetry/random-pool
  > only, no party concept). Bots must live on own/linked (`.playerbots account link`)/guild account
  > or `bot add` rejects them; cap `MaxAddedBots=40`.

  So: **which** mechanism The Lab uses is **UNVERIFIED**; that SOAP/console **cannot** be it is
  source-verified. A spike would have to ask: (a) confirm the fault text by firing `.playerbots bot
  add <Name>` over SOAP on a live WotLK box — a confirmation, not a discovery; (b) determine whether
  the account-linking route (`.playerbots account link`) is itself console-legal, which nobody has
  read the registration for; (c) whether the "gears them" half is a separate mechanism (a
  `PlayerbotFactory` call, an `.additem`-style path, or a MySQL write) — nothing in any record
  addresses gearing at all.
- **Mechanism on the CMaNGOS lineage (TBC, Vanilla, Tortoise).** **UNVERIFIED**, and explicitly a
  different project per tree — `pyplan/archive/phase8-kickoff.md:234-236`: *"The genuinely open spike is the
  CMaNGOS family: their playerbots are a different project per tree (TBC and Vanilla one, Tortoise
  its own fork), so read each tree for its own add-bot command before firing anything."* No CMaNGOS
  guide in `archive/guides/` shows any bot command; `grep -rn '\.playerbots'` over the guides returns
  **zero hits** anywhere in `archive/`. The only bot-party commands any guide teaches are
  **NPCBots**, a different module for a different server variant (see the command table, and F2a).
- **Client-side requirement.** UNVERIFIED, and it is the crux: candidate (1) above **requires an
  in-game addon** written into the client's `Interface/AddOns` plus its SavedVariables, candidate (2)
  requires none. Recorded in `my-party-soap-limitation.md` as candidates, not as a finding.
- **What the user sees.** Five named bots in the player's party frame, of the chosen classes/specs,
  at the chosen level, wearing gear. A gate could read the party roster in-game, or check
  `acore_characters` for the bot characters' equipment rows. UNVERIFIED which is authoritative.

### F2a — NPCBots party commands (a *different* feature the guides teach, not The Lab's)

REPO FACT: `WoW-WotLK-CONTROLS-2.md:127-175` documents a party-of-bots flow that is **not**
mod-playerbots and is gated on the server variant — `CONTROLS-2.md:129`: *"These work if you are
running the **NPCBots** server version"*, `:131`: *"Your server folder will be `wow-server-npcbots`"*.
Commands are in the table below. Relevance to Phase 8: it is the only bot-party command family any
guide in this repo shows the user typing, and it is in-game-chat-only with a **targeting**
precondition (`CONTROLS-2.md:148`: *"Target the bot in-game then type:"*) — which no desktop app can
supply. INFERENCE (mine): that targeting requirement is the same class of obstacle as the
`m_session` guard, and would need the same kind of relay.

### F3 — Item database + in-game mail

- **What The Lab does.** GUIDE CLAIM `README.md:193`: *"**Item database** — search any item and mail
  it to your character"*; `HOWTO.md:286`: *"**Item database & in-game mail** — search any item and
  send it to your character instantly"*.
- **Mechanism on AzerothCore (WotLK).** RECORD (source-verified), `my-party-soap-limitation.md`
  2026-07-15: *"**Safe over SOAP (verified):** item mail (`send items "<char>" "<subj>" "<body>"
  id:count`, offline-ok, max 12)"* and *"**Direct MySQL (read):** item search
  (`acore_world.item_template`)"*. So: **search = MySQL read of `acore_world.item_template`;
  delivery = SOAP command `send items`**, works against an offline character, capped at 12 items per
  mail. The same record's constraint applies to every SOAP feature here: *"SOAP is synchronous on the
  single world thread — the CLI must serialize calls."* Note the record's caveat about the install
  at that date: *"DML install does NOT enable SOAP yet (no `SOAP.Enabled` in worldserver.conf; port
  table wrongly says 8086, AC default is 7878)"*; RECORD (observed) `vm-server-ssh-access.md`
  (2026-08-09) says the owner's live VM does have it: *"SOAP: enabled, `127.0.0.1:7878` on the VM;
  the SOAP account is a GM-3 account whose credentials are recorded outside the repository"*.
- **Mechanism on the CMaNGOS lineage.** **UNVERIFIED.** No record and no guide line. The database
  names differ (`mangos`, per `wow-tbc/WoW-TBC-HOWTO.md:259`: `docker exec -it tbc-db mariadb -u root
  -p mangos -e "SHOW TABLES LIKE 'ai_playerbot%'"`), so even the search half does not transfer:
  neither the schema name nor the table name is established for those trees. A spike would have to
  ask: does `send items` exist in that tree's command table at all, is it console-legal there, and
  what is the item table's name and schema.
- **Client-side requirement.** None. The mail arrives through the game's own mailbox.
- **What the user sees.** A red envelope / new-mail notification in-game and the item in the mailbox;
  offline delivery means it is waiting at next login. Gate-checkable server-side by a `mail` /
  `mail_items` row in `acore_characters`.

### F4 — Teleport (any named location or map coordinates)

- **What The Lab does.** GUIDE CLAIM `README.md:194`: *"**Teleport** to any location or map
  coordinates"*; `HOWTO.md:287`: *"**Teleport** to any named location or map coordinates"*. Two
  addressing modes: named and raw coordinates.
- **Mechanism on AzerothCore (WotLK).** RECORD (source-verified), `my-party-soap-limitation.md`
  2026-07-15: *"**Safe over SOAP (verified):** … named teleport (`teleport name <char> <loc>`)"*.
  That covers the **named** mode only. The **map-coordinates** mode is **UNVERIFIED** — no record
  names a command for it, and the guides only ever show the named in-game form (`.tele stormwind`
  etc., `CONTROLS-2.md:82-85`). A spike would have to ask which console-legal command takes
  map/x/y/z for an arbitrary (possibly offline) character, and whether it is offline-capable the way
  `teleport name` is. RECORD (observed), `citybots-vm-open-items.md`: teleporting a **bot** is a
  different problem — *"`.tele name Mokmak` answered 'is already being teleported' 40 min after
  login"*, a pending-teleport state that only clears when the bot's AI acks it. That is a bot
  concern, not a player-character one, but it is the only recorded observation of the teleport
  command's failure mode on this project's servers.
- **Mechanism on the CMaNGOS lineage.** **UNVERIFIED.** No guide for TBC/Vanilla/Tortoise shows any
  `.tele` command (`grep -rn '\.tele' wow-tbc/ wow-vanilla/` → no hits), and no record covers them.
- **Client-side requirement.** None.
- **What the user sees.** The character loading-screens into the destination. Gate-checkable via the
  character's `position_x/y/z`/`map` in `acore_characters` (the method memory
  `citybots-vm-open-items.md` used, with its recorded caveat that a `.saveall`+position-diff
  under-reported movement on Kalimdor/Orgrimmar and cost three rounds — *"cross-check a metric
  against a second independent source"*).

### F5 — Module management (toggle modules, tune settings in-app)

- **What The Lab does.** GUIDE CLAIM `README.md:195`: *"**Module management** — toggle AzerothCore
  modules and tune settings in-app"*; `HOWTO.md:288`: *"toggle AzerothCore modules on/off; tune their
  settings in-app"*. No list of which modules, no mechanism.
- **Parity denominator — REPO FACT** (`pylauncher/manifests/wow-wotlk/`, counted at this tree):

  | Index | Type | Items in index | `.json` files in dir |
  |---|---|---|---|
  | `modules.json` | module (C++ AzerothCore module) | 21 | 21 |
  | `ale.json` | ale (ALE/Eluna Lua) | 7 | 7 |
  | `mods.json` | mod (SQL mod) | 11 | 11 |
  | `kegs.json` | keg (ALE-Keg) | 2 | 2 |
  | **Total** | | **41** | **41** |

  Index and directory agree in every case. The 21 modules: `mod-1v1-arena`, `mod-aoe-loot`,
  `mod-ah-bot`, `mod-ah-bot-plus`, `mod-autobalance`, `mod-ale`, `mod-player-bot-level-brackets`,
  `mod-challenge-modes`, `mod-individual-progression`, `mod-junk-to-gold`, `mod-learn-spells`,
  `mod-npc-beastmaster`, `mod-quest-loot-party`, `mod-arac`, `mod-dungeon-master`, `mod-solocraft`,
  `mod-talentbutton`, `mod-transmog`, `mod-profession-progression`, `mod-mount-scaling`,
  `mod-custom-login`. So "module management parity" is a claim about **41 manifests**, of which 21
  are modules proper.

- **Manifests whose `client` primitive writes into the client folder — REPO FACT, 5 of 41:**

  | Manifest | `client` entry | Writes |
  |---|---|---|
  | `modules/mod-arac.json:27-32` | `{"src": "Patch-A.MPQ", "dest": "data"}` | an MPQ into the client's `Data/` |
  | `ale/battlepass.json:49-55` | `{"src": "BattlePass", "dest": "addons", "name": "BattlePass"}` | an AddOn |
  | `ale/paragon.json:56-61` | `{"src": "clientside/Interface", "dest": "interface"}` | a whole `Interface/` subtree, merged — its own note: *"Ships a full Interface/ subtree (merged into the client's Interface/), not a self-contained AddOn."* |
  | `kegs/bmah.json:54-60` | `{"src": ".../Client Files/AddOns/BlackMarketUI", "dest": "addons", "name": "BlackMarketUI"}` | an AddOn |
  | `kegs/sod.json:26-35` | two entries: `.../Client Files/data` → `data`, `.../Client Files/Interface` → `interface` | client DBCs **and** an Interface subtree |

  Two more touch the client without a `client` primitive, so a search for the primitive alone misses
  them (rule: enumerate, don't read one field): `modules/mod-mount-scaling.json:52` — *"Players must
  delete the client `Cache/WDB/<locale>/itemcache.wdb` after the SQL applies"*; and
  `modules/mod-talentbutton.json:27` — *"Needs an UNPATCHED 3.3.5a client — RCEPatcher blocks the
  server-side script injection."*

  `mod-arac.json:33-36` also records a removal asymmetry worth carrying into any toggle design:
  *"Lives under `modules/` and is detected like a C++ module, but the install flow skips the rebuild.
  Removing the clone does NOT revert the SQL/DBC/MPQ."*

- **Mechanism on AzerothCore (WotLK).** For The Lab: **UNVERIFIED**. For the operation itself, the
  manifests state it per module: a `conf` file with named keys and a `.conf.dist` template (e.g.
  `modules/mod-transmog.json` → `env/dist/etc/modules/transmog.conf`, five keys, template
  `conf/transmog.conf.dist`), plus `sql` blocks `applied_by: db-import`, plus `build.rebuild: true`
  for C++ modules. RECORD (observed), `citybots-vm-open-items.md` (2026-08-12): conf changes for at
  least one module can be applied hot — *"Conf flips done HOT via `docker exec sed` + SOAP `reload
  config` (the module's `OnAfterConfigLoad` supports it — no restart needed…)"* — and the same entry
  records the trap: *"the VM's module-dir conf still has the old 0s — any conf re-copy to the
  container reverts them"*. GUIDE CLAIM `CONTROLS-2.md:59` teaches `reload config` at the console.
  RECORD (observed), `WoW-Playerbots-Windows-HOWTO.md:393`: for playerbots specifically the compose
  env vars win — *"these environment variables are the source of truth — they override anything in
  the module's config file (`env/dist/etc/modules/playerbots.conf.dist`). Edit the override file, not
  the conf."* (That is a GUIDE CLAIM; memory `bot-population-is-500` records the same class of trap —
  the number lives in six files and a running container keeps its old value until recreated.)
- **Mechanism on the CMaNGOS lineage.** **UNVERIFIED**, and structurally different by the guides'
  own account: CMaNGOS configuration is flat `.conf` files in `~/wow-tbc-server/etc/`
  (`mangosd.conf`, `realmd.conf`, `aiplayerbot.conf`, `ahbot.conf` — `wow-tbc/WoW-TBC-HOWTO.md:214`;
  `wow-vanilla/HOWTO-WOW-VANILLA.md:200,227`), with no module directory and no manifest set in this
  repo. There are **no** manifests for `wow-tbc`, `wow-vanilla` or Tortoise under
  `pylauncher/manifests/` mirroring the 41 above (only `wow-wotlk/` was inspected per my brief;
  I did not enumerate the other games' manifest dirs — see §4).
- **Client-side requirement.** Yes, for the five manifests above — a module toggle is not purely
  server-side, and one of them (`kegs/sod.json:38`) even records that the client-data volume is
  mounted read-only: *"The `ac-client-data` volume is mounted `:ro` on the worldserver, so DBCs are
  copied through a throwaway rw helper container."*
- **What the user sees.** Per-module: a new NPC to talk to (several manifests carry `npcs` entries
  with a `.npc add <entry>` note — `mod-transmog` 190010, `battlepass` 90100, `bmah` 2069430), a new
  UI element, or nothing visible until a restart (`mod-arac.json:35`: *"Restart the worldserver after
  the DBC copy."*).

### F6 — Steam integration (adds server + WoW client to the Steam library, with artwork)

- **What The Lab does.** GUIDE CLAIM `README.md:196`: *"**Steam integration** — adds the server and
  WoW client to your Steam library automatically"*; `HOWTO.md:289` adds the artwork:
  *"adds the server and WoW client to Steam with artwork"*. Two shortcuts, and images.
- **Mechanism.** For The Lab: **UNVERIFIED** — no record names a file or an API. What the guides
  teach the user to do **by hand** is the shape of what it automates
  (`WoW-WotLK-HOWTO.md:202-226`, verbatim):

  ```
  202	## Step 4 — Add to Steam (Gaming Mode)
  204	You need two Steam shortcuts. A **Non-Steam Game** is just a shortcut in your Steam library to a program Steam didn't install …
  206	### Shortcut 1: Server Launcher
  208	1. Steam → **Add a Non-Steam Game** → browse to `/usr/bin/konsole`
  212	2. Rename to: `WoW Playerbots Server`
  213	3. Right-click → **Properties** → Launch Options:
  215	   --hold -e bash ~/wow-playerbots-launcher.sh
  218	4. Compatibility tab: **disable Proton** (this is a Linux script, not a Windows program)
  220	### Shortcut 2: WoW Client
  222	1. Steam → **Add a Non-Steam Game** → browse to `WoW.exe` in your client folder
  223	2. Rename to: `World of Warcraft: Wrath of the Lich King`
  224	   - The name helps steam find controller layouts.
  225	3. Compatibility tab: **Force a specific Steam Play compatibility tool** → select **GE-Proton** (latest)
  ```

  Prerequisite recorded at `HOWTO.md:60`: *"**GE-Proton** installed in Steam (for the WoW client
  shortcut in Step 4). Install it via **ProtonUp-Qt** from the Discover app store."* INFERENCE
  (mine, unproven): automating this means writing Steam's `shortcuts.vdf` and dropping artwork into
  the userdata grid folder, and setting the per-app compat tool — none of that is recorded anywhere
  in this repo or the memory directory, and I did not verify it.
- **Platform note — REPO FACT.** Both citations are in the **Steam Deck / Linux** guide; The Lab is
  listed as *"**Platform: Steam Deck / Linux (AppImage)**"* (`README.md:184`). Nothing records a
  Windows Steam integration. Per memory `launcher-is-the-product` and `fix-one-distro-at-a-time`,
  whether this feature has a Windows meaning at all is an owner question, not a reader's — flagging
  and stopping on it per my brief.
- **Client-side requirement.** A Steam shortcut entry pointing at the client's `WoW.exe`, plus a
  Proton compat-tool assignment. Recorded only as the manual procedure above.
- **What the user sees.** Two entries in the Steam library, launchable from Gaming Mode, with
  artwork. `HOWTO.md:230-238` is the daily-use flow they enable.

### F7 — Auto-shutdown when WoW closes

- **What The Lab does.** GUIDE CLAIM `README.md:197`: *"Auto-shuts down when you close WoW"*;
  `HOWTO.md:290`: *"Auto-shutdown when you close WoW"*.
- **Config key — RECORD (observed).** `dml-launcher-windows-project.md`, 2026-07-14, from
  `strings` on `TheLab.AppImage` v0.0.7: *"config at `~/.config/dads-mmo-lab/settings.json` (shared
  namespace with DML) with keys like `active_soap_url`, `safe_graphics`,
  `auto_shutdown_on_client_exit`"*. So the feature is a **boolean setting in a JSON file at a shared
  path**, and the path is shared with DML itself. The record does not say what watches the client or
  what it does when it fires.
- **Mechanism.** **UNVERIFIED** for The Lab. The guides record the *DML shell launcher's* behaviour,
  which is a different implementation with published numbers — `WoW-WotLK-HOWTO.md:240` verbatim:

  > When you close WoW, the launcher shuts the server down automatically. If WoW isn't detected
  > within 5 minutes, the server stays alive for 3 hours as a fallback.

  REPO FACT: the script that implemented it (`~/wow-playerbots-launcher.sh`, generated by
  `install-wow-wotlk.sh`) **no longer exists in this tree** — deleted with the bash installers — so
  those two numbers cannot be checked against code here; they are a guide claim only.
  A distinct Windows-side behaviour is recorded and is **not** the same feature:
  `WoW-Playerbots-Windows-HOWTO.md:163` — *"Windows shuts down the WSL2 environment (and your server
  with it) about a minute after the last connection to it closes. The DML Launcher keeps the
  connection alive"* — i.e. on Windows the tray app existed to *prevent* shutdown, not cause it; and
  `DML-Windows/DML-Windows-HOWTO.md:244` — *"Exit | Closes the tray app — **running servers shut down
  seconds later** (the launcher warns you first)"*.
- **Mechanism on the CMaNGOS lineage.** UNVERIFIED, but INFERENCE (mine): process-watching a client
  executable is emulator-independent; only the *stop* half is per-family (compose project names and
  container names differ — `tbc-mangosd`, `vanilla-mangosd` vs `ac-worldserver`). Not verified.
- **Client-side requirement.** None on disk; it needs to observe the client **process**.
- **What the user sees.** The server stops on its own within some interval of closing WoW. A gate
  could check the containers are gone after the client process exits.

### F8 — Detect and migrate an existing Dad's MMO Lab install (revealed by the guides, not on my six)

- **What The Lab does.** GUIDE CLAIM, twice: `README.md:199` — *"**Already installed from this
  guide?** The Lab detects existing Dad's MMO Lab servers and migrates them in — your characters and
  data stay untouched"*; `HOWTO.md:292` — *"The Lab detects existing Dad's MMO Lab installs and
  migrates them in — your characters and data are untouched."* Both promise data preservation
  explicitly.
- **Mechanism.** **UNVERIFIED**. INFERENCE (mine, unproven): the shared config namespace
  `~/.config/dads-mmo-lab/settings.json` recorded in `dml-launcher-windows-project.md` is the likely
  discovery surface, since that record says the namespace is *"shared … with DML"* — but nothing
  establishes that, and the guides' server roots are conventional paths (`~/wow-server-playerbots`,
  `HOWTO.md:333`; `~/games/wow-server-playerbots` on the Windows/WSL layout,
  `WoW-Playerbots-Windows-HOWTO.md:293`) which a scan could equally find.
- **Relevance.** `pyplan/rust-prior-art.md:203-204` records that the Rust launcher's equivalent is
  explicitly **not** worth porting for Yu'lon — *"Explicitly **not** worth porting: the WSL-distro
  provisioning lineage, `migrate.rs`'s import path (Yu'lon has no WSL-era servers to import)"*. That
  is about the Rust code's migration, not about The Lab's; whether Yu'lon owes a Lab-install
  migration is a judgement, and I stop on it.
- **What the user sees.** The existing server appearing in the app's library with its characters
  intact.

### F9 — "Play Together" (recorded in memory, absent from every guide)

- **What The Lab does.** RECORD (observed), `dml-launcher-windows-project.md`, 2026-07-14, from the
  AppImage dissection: *"Play Together = Tailscale + alembic.gg auth (Bearer + X-Lab-Account) +
  character snapshot merge"*. Not mentioned in any guide in this repo (`grep -rn -i 'play together'`
  over `archive/` → no hits).
- **Mechanism.** A third-party hosted service (alembic.gg) plus Tailscale plus a character-snapshot
  merge. Nothing about the merge's server-side mechanism is recorded. **UNVERIFIED** beyond the
  strings-level observation.
- **Note.** The same record says The Lab is *"the official closed-source Steam Deck GUI evolution of
  DML by Veil (0xVe1L) + alembic.gg"*, so this feature is tied to a service Yu'lon does not have.
  Whether it is in scope is a judgement — I stop on it.

### F10 — Other settings keys recovered (no feature name attached)

RECORD (observed), same 2026-07-14 record: `active_soap_url` and `safe_graphics` sit in the same
`settings.json`. `active_soap_url` implies The Lab talks SOAP and can point at more than one server;
`safe_graphics` implies it launches the client with a graphics fallback. Neither has a guide line, a
mechanism, or a feature entry — recorded here so they are not rediscovered. Also: *"a ~140-command
Tauri IPC surface recovered via `strings`"* — the list itself is **not** in the memory record, only
the count. If the command names are wanted, the record says where the extraction lived
(`/home/labtest/squashfs-root`) and `my-party-soap-limitation.md` says to *"re-extract"* if gone.

### F11 — Character dashboard / gear / stats (adjacent, not in the Lab feature list)

`my-party-soap-limitation.md` (RECORD, source-verified, 2026-07-15) names *"**Direct MySQL (read):**
… the dashboard (`acore_characters`)"* alongside item search. The word "dashboard" is not a Lab
feature name from any guide — the guides' Lab list has no dashboard entry. `pyplan/rust-prior-art.md:
198-201` puts the character/gear/stats reads under *"The Lab features themselves"*
(`pages.rs`/`paperdoll.rs`/`stats.rs`, `iteminfo.rs`, `botid.rs`), which is the Rust launcher's own
framing. **Whether The Lab has this feature is a claim from our side of the fence, not a guide
record — I could not establish it and do not assert it.** Mechanism, if wanted: MySQL read of
`acore_characters` (AzerothCore); **UNVERIFIED** on the CMaNGOS lineage (schema is `mangos`,
`WoW-TBC-HOWTO.md:259`).

---

## 3. Commands the guides teach today, and their context

Every command any guide in `archive/guides/` shows the user typing at a server. Verbatim, with
file:line relative to `archive/guides/`. Context column: **console** = typed at the attached
worldserver/mangosd console (`AC>` / `mangos>`); **chat** = typed in the WoW chat box in-game.

| Command (verbatim) | file:line | Context | Account level shown |
|---|---|---|---|
| `account create USERNAME PASSWORD` | `wow-wotlk/WoW-WotLK-CONTROLS-1.md:246`, `wow-wotlk/WoW-WotLK-CONTROLS-2.md:55`, `wow-wotlk/WoW-WotLK-CONTROLS-2.md:215`, `wow-wotlk/WoW-WotLK-CREATE-ACCOUNTS.md:52`, `wow-wotlk/WoW-WotLK-CREATE-ACCOUNTS.md:107`, `wow-wotlk/WoW-WotLK-HOWTO.md:322` (commented) | console (`AC>`) | n/a — console has no account level |
| `account create YOURNAME YOURPASSWORD` | `wow-wotlk/WoW-WotLK-HOWTO.md:143` | console (`AC>`) | n/a |
| `account create john mypassword` | `wow-wotlk/WoW-WotLK-HOWTO.md:149` | console | n/a |
| `account create dad mypassword` | `wow-wotlk/WoW-WotLK-CREATE-ACCOUNTS.md:65` | console | n/a |
| `account create caitlin mypassword` / `account create kiddo simplepass` | `wow-wotlk/WoW-WotLK-CONTROLS-1.md:261,264` | console | n/a |
| `account create player player` | `wow-wotlk/WoW-Playerbots-Windows-HOWTO.md:121`, `wow-tbc/WoW-TBC-HOWTO.md:113`, `wow-vanilla/HOWTO-WOW-VANILLA.md:108` | console — `AC>` for WotLK (`:118`), **`mangos>`** for TBC (`:110`) and Vanilla (`:105`) | n/a |
| `account create USERNAME PASSWORD PASSWORD` *(three args — password twice)* | `DML-Windows/HOWTO-WINDOWS-WSL2.md:301`, and `:307` as `account create dad mypassword mypassword` | console; that guide attaches with `grep -i "worldserver\|mangosd"` (`:295`), i.e. it covers both families with one form | n/a |
| `account set gmlevel USERNAME 3 -1` | `wow-wotlk/WoW-WotLK-CONTROLS-1.md:247`, `CONTROLS-2.md:56`, `CONTROLS-2.md:216`, `CREATE-ACCOUNTS.md:58`, `CREATE-ACCOUNTS.md:108`, `WoW-WotLK-HOWTO.md:144`, `WoW-WotLK-HOWTO.md:323` (commented), `WoW-Playerbots-Windows-HOWTO.md:284` (commented), `DML-Windows/HOWTO-WINDOWS-WSL2.md:302` | console | grants level **3** ("Administrator", `CONTROLS-1.md:277`); realm `-1` = all realms |
| `account set gmlevel player 3 -1` | `wow-wotlk/WoW-Playerbots-Windows-HOWTO.md:122`, `wow-tbc/WoW-TBC-HOWTO.md:114`, `wow-vanilla/HOWTO-WOW-VANILLA.md:109` | console (`AC>` / `mangos>`) | 3 |
| `account set password USERNAME NEWPASSWORD NEWPASSWORD` | `wow-wotlk/WoW-WotLK-CONTROLS-1.md:284`, `wow-wotlk/WoW-WotLK-CREATE-ACCOUNTS.md:130` | console | *"You do not need the old password when you have GM console access."* (`CONTROLS-1.md:287`) |
| `account onlinelist` | `wow-wotlk/WoW-WotLK-CONTROLS-1.md:294`, `CONTROLS-2.md:57` | console | n/a |
| `account delete USERNAME` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:58` | console | n/a |
| `reload config` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:59` | console | n/a |
| `server info` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:60` | console | n/a |
| `server shutdown 10` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:61` | console | n/a |
| `.tele stormwind` / `.tele orgrimmar` / `.tele dalaran` / `.tele ironforge` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:82,83,84,85` | **chat** — *"Type these in the WoW chat box while playing"* (`:78`) | GM 3 implied (`CREATE-ACCOUNTS.md:61`: gmlevel 3 *"gives your account administrator ('GM') powers in-game — teleport, summon items, and more"*) |
| `.levelup` / `.levelup 10` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:90,91` | chat | GM 3 implied |
| `.modify speed 3` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:96` | chat | GM 3 implied |
| `.modify money 999999` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:103` | chat | GM 3 implied |
| `.additem ITEM_ID` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:108` | chat | GM 3 implied |
| `.time set 12 0` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:115` | chat | GM 3 implied |
| `.commands` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:122` | chat | GM 3 implied |
| `.npcbot spawn CLASS_ID` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:135` | chat | **NPCBots server variant only** (`:129,131`) |
| `.npcbot add` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:150` | chat, **after targeting the bot** (`:148`) | NPCBots variant only |
| `.npcbot remove` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:156` | chat, after targeting (`:154`) | NPCBots variant only |
| `.npcbot set role tank` / `heal` / `dps` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:161,162,163` | chat | NPCBots variant only |
| `.npcbot set follow` / `.npcbot set standstill` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:168,169` | chat | NPCBots variant only |
| `.npcbot list` | `wow-wotlk/WoW-WotLK-CONTROLS-2.md:174` | chat | NPCBots variant only |
| `.npc add 900001` | `unbound-wrath/Wrath-Unbound-Addon-HOWTO.md:80`, and `:239` in its summary table | chat, standing where the NPC should go (`:77`) | *"requires GM level 3"* (`:83`), stated explicitly |
| `.npc add 2069430` | `wow-wotlk/ALE-Kegs/BlackMarketAuctionHouse/BMAH.lua:12,51` (a comment/`print` in the mod, not a guide page) | chat | GM implied |

**Two facts about this table that matter for Phase 8:**

1. **No guide anywhere teaches a `.playerbots` command.** `grep -rn '\.playerbots'` across the whole
   repo returns only `pylauncher/yulon/catalog/catalog.py:913-914` (a catalog field, not a command)
   and three lines of `pyplan/archive/phase8-kickoff.md`. Zero hits in `archive/`. Everything the guides
   teach about a bot party is **NPCBots**, a different module.
2. **Two command channels, one of them stateful.** Every console command above is reached through
   `docker attach`, whose safe exit is Ctrl+P Ctrl+Q and whose unsafe exit kills the server
   (`CONTROLS-2.md:35-45`). RECORD (observed), memory `wsl-console-pty-via-script` (2026-08-27):
   *"kill-teardown stops the container through wsl.exe; Ctrl+P Ctrl+Q does not"* — so the detach
   sequence is not reliable through every host wrapper. `pyplan/archive/phase8-kickoff.md:207-208` states the
   constraint that binds both channels: *"every command channel — SOAP and the console/attach alike —
   executes on the single world thread and is serialised; only direct MySQL reads bypass it"*.

---

## 4. Facts I could not establish

Each is a "could-not-ask", not a "no".

1. **Which mechanism The Lab's My Party actually uses.** Source-verified that SOAP/console cannot be
   it (`my-party-soap-limitation.md`, 2026-07-15); the three candidates in that record were never
   narrowed, and the record's own next step (*"grep The Lab AppImage … for `.playerbots bot add`,
   `:8888`, `urn:AC`, and AddOns SavedVariables writes"*) has not been done — and my brief forbids
   downloading or running The Lab, so I could not do it.
2. **How My Party "gears" the bots.** No record addresses gearing at all. Not even a candidate.
3. **The map-coordinates half of teleport.** Only `teleport name <char> <loc>` is verified over SOAP.
   No command for map/x/y/z is recorded, offline-capable or otherwise.
4. **Every CMaNGOS-lineage mechanism, for every feature.** No record in this repo or the memory
   directory establishes any of them; per memory `upstream-conventions-differ-per-fork` and
   `archive/phase8-kickoff.md:234-236`, three separate trees (TBC/Vanilla share a playerbots project,
   Tortoise has its own fork). The guides for those games teach only `account create` /
   `account set gmlevel` at a `mangos>` prompt — no `.tele`, no mail, no bot commands.
5. **How Steam integration is implemented.** Only the manual procedure is recorded
   (`HOWTO.md:202-226`). The file format(s) an automated version would write are not recorded
   anywhere I read. My `shortcuts.vdf` guess is INFERENCE and I did not verify it.
6. **What The Lab's auto-shutdown watches, and its timings.** Only the boolean key
   `auto_shutdown_on_client_exit` is recorded. The 5-minute-detect / 3-hour-fallback numbers at
   `HOWTO.md:240` belong to the **DML shell launcher**, and the script that implemented them has been
   deleted from this tree, so they cannot be checked here.
7. **How The Lab detects and migrates an existing DML install** (F8) — mechanism unrecorded.
8. **The ~140 Tauri IPC command names.** The count is recorded; the list is not. Recovering it means
   re-extracting the AppImage, which my brief forbids.
9. **Whether The Lab has a character dashboard at all** (F11). Our records name the *mechanism* for a
   dashboard; no guide line attributes the feature to The Lab.
10. **Which The Lab version the guides describe.** The memory record dissected **v0.0.7** (2026-07-14);
    the guides say only *"the latest release"* (`README.md:188`, `HOWTO.md:281`). The feature lists in
    `README.md:190-197` and `HOWTO.md:283-290` differ from each other in wording (README says "any
    location", HOWTO "any *named* location"; HOWTO adds "with artwork" and "spawns and gears them"),
    which may be drift between two edits of the same list rather than two versions. Undetermined.
11. **A guide inconsistency in the account-create arity.** `DML-Windows/HOWTO-WINDOWS-WSL2.md:301,307`
    teaches `account create USERNAME PASSWORD PASSWORD` (three args) while every WotLK page and both
    CMaNGOS pages teach two. That guide attaches to `worldserver\|mangosd` alike (`:295`), so it may
    be the mangosd form generalised, or an error. Not resolved — it needs the machine.
12. **Whether the other games have module manifests.** I enumerated `pylauncher/manifests/wow-wotlk/`
    only, as briefed (41 manifests). I did not check whether `wow-tbc`, `wow-vanilla` or Tortoise have
    manifest sets, so I cannot state a cross-game module-management denominator.

**Items where the next step is a judgement, so I stopped** (per my brief): whether Steam integration
has a Windows meaning at all (F6); whether Yu'lon owes a Lab-install migration path (F8); whether
"Play Together" is in scope given it depends on the alembic.gg service (F9).
