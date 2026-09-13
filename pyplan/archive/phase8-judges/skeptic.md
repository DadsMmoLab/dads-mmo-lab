# Phase 8 judge panel — the skeptic

> Read 2026-09-06 against the three designs (`designs/seam.md` = **A**, `designs/surface.md` = **B**,
> `designs/risk.md` = **C**), the kickoff §3/§6/§7, the parity page, the delta, the five reads, the
> Phase 7 "rejected"/Appendix B and the Phase 6 repair section. Where a design's survival rested on a
> fact I could read, I read it: `pylauncher/` at 7bc5ebd3 (`C:\Users\perzi\dml-phase8`), the fetched
> trees under `scratchpad/{ac-trees,cmangos-trees}/`, and `origin/rust-main`. Nothing was run, no box
> was touched. Lines quoted are the lines at those trees.

---

## 0. Facts I checked, because the verdict turns on them

| # | Fact | Where | What it settles |
|---|---|---|---|
| F1 | **SOAP starts after the world is loaded, on all three SOAP cores.** AC `Main.cpp:315` `sWorld->SetInitialWorldSettings();` then `:337-339` `if (sConfigMgr->GetOption<bool>("SOAP.Enabled", false)) … soapThread.reset(…)`. TBC/VAN `Master.cpp:126` `sWorld.SetInitialWorldSettings();` then `:248` `if (sConfig.GetBoolDefault("SOAP.Enabled", false))`. | ac-trees/core, cmangos-trees | During map load a SOAP connect is **refused**, not accepted-and-blocked. A is right; B and C reason from the opposite (see §3). |
| F2 | **AC's SOAP loop serves one request at a time at this rev.** `ACSoap.cpp:56-63`: `struct soap* thread_soap = soap_copy(&soap); process_message(thread_soap);` — inline, no thread. `accept_timeout = 3` (`:34`). | ac-trees/core | C's "one thread per request" (§4.0) is wrong; a command stuck behind a long handler holds the only accept loop. Same shape as CMaNGOS (`MaNGOSsoap.cpp:51-63`). |
| F3 | **`ac-worldserver` depends on the one-shot import.** `base.yml.tmpl:273-279`: `depends_on: … ac-db-import: condition: service_completed_successfully`. `docker.py:702-731` (`start_staged`): `compose up -d --no-deps db auth world`, "never runs the one-shot import … recreates a service whose configuration changed", measured against Docker 29.1.3; the bare form "was killing the database". | pylauncher | A's recreate must go through `start_staged` (§3, A's fatal line). |
| F4 | **`urn:MaNGOS` is in the tree.** `MaNGOSsoap.cpp:156` `{ "ns1", "urn:MaNGOS" },` on both mangos-tbc and mangos-classic. | cmangos-trees | A's "UNVERIFIED" and C's "could-not-ask" are settled by a read; no gate needed for the namespace. |
| F5 | **`account set password` takes three args on TBC, Vanilla and Tortoise.** TBC `Level3.cpp:1132`+10-11, VAN `:1093`+10-11, TW `Commands.cpp` (handler +21-22): `szPassword1 = ExtractQuotedOrLiteralArg(&args); szPassword2 = …; if (!szPassword1 \|\| !szPassword2)`. | cmangos-trees | A's `arity_verified: false` → true today. |
| F6 | **Tortoise does not cache passwords.** `AccountMgr.cpp:299-311` `CheckPassword` → `SELECT 1 FROM account WHERE id='%u' AND sha_pass_hash='%s'`. Only `rank` is cached (`:250-256`). | cmangos-trees/tortoise-wow | C's S4 is settled without a spike; A8t (direct hash write) is safe. |
| F7 | **Addclass bots are logged out with their master.** `Playerbots.cpp:450-460` `OnPlayerbotLogout` → `playerbotMgr->LogoutAllBots()` when the player is not itself a bot. | ac-trees/mod-playerbots | C's S3 settled by a read. |
| F8 | **ALE's command hook hands Lua the handler.** `PlayerHooks.cpp:57-60` `START_HOOK_WITH_RETVAL(PLAYER_EVENT_ON_COMMAND, true); Push(player); Push(text); Push(&handler);`. `LuaFunctions.cpp:1710` `SendSysMessage`, `:755` `Player:RunCommand`, `:773` `Whisper`, `:122` `GetPlayerByName`, `:71` `RegisterPlayerEvent`. | ac-trees/mod-ale @ c3de7942 (unpinned) | A's `yulon_ping` via the handler works **at this HEAD**; the pin is still the owner's. |
| F9 | **The CMaNGOS/Tortoise column names A declared "unread" are the ones A guessed.** TBC `mangos.sql:2968-2978` `displayid, Quality, InventoryType, ItemLevel, RequiredLevel`; `characters.sql` has `race, class, gender, position_x, map, zone` (791+); `mail.receiver` `:1493`. TW `tw_world_item_template.sql` `quality, inventory_type, item_level, required_level`; `create_databases.sql` `characters.zone` (`:999`), `mail.receiver` (`:1820`). AC `group_member(guid, memberGuid)`. | cmangos-trees, ac-trees | A's `tables.*` are verified; B's "no zone column until read" is wrong. |
| F10 | **Name collation differs per tree.** AC `characters.name … COLLATE utf8mb4_bin` (`characters.sql:26`); TBC `characters` table `DEFAULT CHARSET=utf8` (`characters.sql:861`, case-insensitive default). | both | The canonicalisation rule (A §9, C `verbs.py`) is an AC fact; harmless elsewhere but not needed. |
| F11 | **`conf.apply_table` refuses a missing file.** `conf.py:257-259` "A missing file is an error, not a copy". | pylauncher | C's A1 (patch `env/dist/etc/worldserver.conf`) raises on an install the entrypoint has not yet populated. |
| F12 | **`write_plan` skips unchanged bytes; `start_staged` recreates on change.** `composegen.py:730-733`; `docker.py:729-731`. | pylauncher | A's `render_override` + recreate is sound **if** the recreate is `start_staged`. |
| F13 | **The app account name `YULON` already exists on m910q's TBC install.** `checklist.md:1452-1461`: 7.9 wrote `realmd.account` id 105 `YULON` gmlevel 3 and logged a client in through it — which also proves `mangos_srp6` live (the `catalog.py:931-943` docstring saying it is unmeasured is stale). | pyplan | B's fixed name `yulon` collides with the 7.9 gate's own row on the gate box; A/C's per-install name does not. |
| F14 | **A server-dir credentials file is inside the AC build context but not the image.** `build.yml.tmpl:24,28` context = the checkout; `apps/docker/Dockerfile:73-77` copies only `CMakeLists.txt conf deps src modules`; `.dockerignore` does not name a root json; `git.py:600,:833` `reset --hard FETCH_HEAD` leaves untracked files. | pylauncher, ac-trees | B's location is survivable, still the wrong shape (`platform.py:68-73`: app state, "not server data"). |
| F15 | **`published_bindings()` is global.** `docker.py:2302-2323` parses every container's `{{.Ports}}`; first IPv4 binding per port wins. | pylauncher | A's "7878 must read 127.0.0.1" guard answers for whichever container holds 7878. |
| F16 | **CMaNGOS `server info` text is a DB string.** `Level0.cpp:89-110` prints `LANG_CONNECTED_USERS`/`LANG_UPTIME` from `mangos_string` (tbc-db, not fetched). | cmangos-trees | A's `probe_expect: null` on TBC/VAN is honest; pin it from `tbc-db`'s `mangos_string` or at the gate. |
| F17 | **Tortoise's queue realm is conf-driven, default 1.** `Master.cpp:799` `realmID = sConfig.GetIntDefault("RealmID", 0)`; dist `:8` `RealmID = 1`; Yu'lon's Tortoise conf table patches no `RealmID` (`cmangos.md` Step 1). | cmangos-trees, catalog | A's `realm_id: 1` holds on a Yu'lon install. |

---

## 1. Scores

Seven criteria, 1–5: (1) style-guide fit; (2) DRY, one seam; (3) testability; (4) operator safety on a live server; (5) per-family correctness; (6) gate quality; (7) blast radius and incremental delivery.

| | A — seam | B — surface | C — risk |
|---|---|---|---|
| 1 style-guide fit | 4 | **5** | 3 |
| 2 DRY, one seam | **5** | 3 | 3 |
| 3 testability | **5** | 4 | **5** |
| 4 operator safety | 3 | 3 | **4** |
| 5 per-family correctness | **5** | 3 | 3 |
| 6 gate quality | 4 | **5** | 4 |
| 7 blast radius / incremental | **4** | **4** | 3 |
| **Total** | **30** | 27 | 25 |

Why the numbers: A is the only design whose server-state model matches the trees (F1) and whose per-tree data survives the reads (F4, F5, F9); it loses on safety for one line (F3) and two unowned seams (§4h). B has the best surface discipline and the best gates, and reasons about the channel from a transport behaviour that does not exist (F1). C has the best tests and the best ledger, pays the world thread on a timer, mis-describes AC's SOAP threading (F2), and spends four spikes on facts three reads settle (F4, F6, F7).

---

## 2. Ranked verdict

**A wins, on condition of the grafts in §5.** Ranking A > B > C.

A is the only design in which the three-outcome model is built from the right primitives: `starting` is decided by docker (running + no ready marker), not by a probe; the probe is never on the timer; the per-tree facts are typed data the catalog validator refuses; every write has a verify read; the Console tab stops spawning an attach client on the cores that have SOAP. B is the surface A lacks and must adopt nearly whole. C is the review A must pass before its first gate.

---

## 3. The fatal flaw in each design

### A — `docker compose up -d <world>` without `--no-deps`

- **Design line.** §4 8.1 "How the switch reaches a running install": "then `docker compose up -d <world>` recreates the container"; §2 `channel_setup.enable` = "`render_override` + recreate".
- **Fact it contradicts.** F3: `ac-worldserver` `depends_on: ac-db-import: condition: service_completed_successfully` (`base.yml.tmpl:276-277`). A bare `compose up -d ac-worldserver` evaluates that dependency; `start_staged`'s docstring records what that did: "Re-running the import … was killing the database" (`docker.py:707-711`). The safe, measured form is `compose up -d --no-deps db auth world` (`:766`), which "recreates a service whose configuration changed" (`:729-731`).
- **Fixable by graft**, not structural: `channel_setup.enable` calls `docker.start_staged()` (unchanged services are not recreated; the world is). The second half of the same line — one press recreates a running world after a sentence — takes B's two-press arm idiom (§5).
- Runner-up in A: `logsnap.snapshot` names no timeout (`_logs()` at `docker.py:1913` carries none either); "a snapshot failure is logged, never blocks the stop" covers a failure, not a hang — the 8-minute `docker ps` on yulon-win11 (`docker.py:925-935`) is the route. Graft C's 20 s bound.

### B — `starting` is derived from a connection that is never accepted

- **Design line.** §3.1 "the probe returns `starting` only while `container_state().settled` is true and the channel connects but does not answer within its reply timeout"; §3.4 "on SOAP it connects and blocks (`ACSoap.cpp:125-130`)"; §8 "SOAP connects and then waits … the seam's reply timeout decides `starting`".
- **Fact it contradicts.** F1: on AC, TBC and Vanilla the SOAP thread is created after `SetInitialWorldSettings()`; during the 46-minute load `controller.py:359-371` recorded, the connect is refused. B's table has no row for "container running, connect refused, credentials present", so the whole load reads `could-not-ask` (or, worse, `not-set-up` if refused is mapped to "no helper account"), the 15-s "while starting" cadence bounds a state never entered, and the 8.1a DoD "a Start shows 'waiting for the world to finish loading' before 'ready'" cannot be met as written.
- **Fixable by graft**: A's server-state table (`starting` = `docker.container_state().settled` and no ready marker in `_logs(this_run_only=True)`, the primitives `wait_ready()` already uses, `docker.py:2121-2219`); refused → `could-not-ask(starting)` only when docker says starting.
- Second, near-fatal in B: **two attach writers on Tortoise.** §2.4 "each tab's own pending flag … the seam's lock for the wire" and "The Console tab's Send stays on `docker attach` and keeps its own flag" — on Tortoise the Characters/Accounts buttons are also `send_command()` (§3.1 "Family cannot"), and B names no lock the Console tab shares with them. `console.py:57-73`: the prompt delimiter "is a single-writer property"; two clients put foreign prompts in each other's windows. Graft C's interlock, or A's rule that every attach goes through one per-install `AttachChannel` lock including the Console tab's.
- Third: the fixed account name `yulon` (§3.1, §8) collides with the 7.9 gate's `YULON` row on m910q (F13); B's remedy "the seam's next press finds it by name … and resets its password" would rewrite that account. Graft A/C's `yulon_<install_id>`.

### C — a world-thread command on a timer, forever, through a single-threaded accept loop

- **Design line.** §2 `dashboard.py` "one SOAP `server info`"; §4.2 "The polling budget … `server info` over SOAP every 30 s (one world-thread command, counters only)"; §4.0 "SOAP concurrency, AC: one thread per request".
- **Facts it contradicts.** F2: AC's loop is `soap_accept` → `process_message` inline; there is one request in flight per server, and a poll queued behind a `.saveall` (58–91 s of saves, `docker.py:938`) holds the accept loop, so the user's next teleport waits in the kernel backlog behind a dashboard tick. C's own rule "a poll refused while a poll is in flight" cannot see a poll that is blocked inside the server. And kickoff §3 asks each design to settle "whether the world thread is blocked"; C settles it by paying one slot every 30 s for the life of the server — the one thing A and B both refused.
- **Fixable by graft**: drop `server info` from the timer (A/B: reads + `docker inspect` only); `Update time diff` becomes a Refresh-button read and a gate capture, which is where C actually uses it (§4.5 "the gate captures … before and after each verb").
- Second C flaw: the conf route on AC (A1 `env/dist/etc/worldserver.conf`) is refused by `apply_table` on a file that is not there (F11) — the entrypoint writes it on first boot (`entrypoint.sh:46-50`); an install that was installed but never started is one press away from an `InstallerError`. Also two mechanisms for keys in one file (env for `AC_AI_PLAYERBOT_*`, conf for `SOAP.*`) — the six-places lesson of `bot-population-is-500`. Graft A's `render_override`.
- Third: S1, S3, S4 and the namespace are spikes or could-not-asks that three reads settle (F4, F6, F7). C's S1 wants a conf edit and a restart on the 7.2 install; §7 below names the read that replaces it.

---

## 4. The hunt, by rule

### (a) Load-bearing assumptions never verified

| Design | Assumption | Status |
|---|---|---|
| all | A docker port publish cannot reach a listener bound to the container's `127.0.0.1`, so `SOAP.IP = 0.0.0.0` behind a host `127.0.0.1:7878` publish is required | Correct for bridge networking (DNAT/docker-proxy target the container IP). Unmeasured on Yu'lon; the only live SOAP install is the owner's VM — §7 fact 1 |
| A | `compose up -d <world>` recreates safely | F3 — wrong unless `--no-deps`; graft |
| A | `published_bindings()` answers for this install | F15 — it is global; with another project on 7878 the guard lies. Filter by `container_project()` (`docker.py:2273-2299` already does this for ports) |
| A | `AttachChannel`'s lock covers the Console tab's attach on Tortoise | Not stated: §2 says the Console Send "uses `channel.ask` when the SOAP channel is verified and attach otherwise" — "attach otherwise" reads as today's `send_console`, outside the lock |
| A | the live bot prefix | `BotMarker.account_prefix` is catalog data; `prefix_conf` is "where the live prefix would be read from" — which wins is unsaid. B and C read the installed conf; a user who edits `AiPlayerbot.RandomBotAccountPrefix` gets an empty Bots tab from A with no warning |
| B | SOAP connects during map load | F1 — false |
| B | one attach at a time on Tortoise | §2.4 — false across tabs |
| B | `yulon` is free | F13 — false on m910q |
| B | 8.6 "within 30 s" (§5 DoD) vs 12 × 500 ms then *no* (§3.6) | internally inconsistent; pick one |
| C | AC spawns a thread per SOAP request | F2 — false |
| C | `ss` exists in the AC image | `apps/docker/Dockerfile` apt lists do not show `iproute2` on the lines read; use `cat /proc/net/tcp` and look for `:1EC6` |
| C | `worldserver.conf` exists when the press lands | F11 — not on a never-started install |
| C | Tortoise caches passwords (S4) | F6 — it does not; A8t is unblocked |

### (b) Guarantees, and the route that refutes them

- A §3: "a wrong column is a loud `DockerCommandError`, never an empty list" — a wrong *prefix* is the silent case (`LIKE 'X%'` on nothing); the validator's `min_length=1` guards the catalog value, not the conf value the design also names.
- A §4 8.1: "a second WotLK install cannot publish 7878" — it cannot *bind*, and the refusal is the daemon's, after `compose up` has already stopped the old container to recreate it (bug-checklist 227). Price: a recreate that leaves the world down. Route: `port_conflicts_for()` before the recreate, which A's §9 cites but the 8.1 table does not call.
- B §2.4: "attach and SOAP … never share a tty and cannot corrupt each other's reply" — true across the two transports, false for two attach clients (Tortoise).
- B §3.4: "a press is impossible — the button is disabled" while starting — the poll is 5 s; a world that crashes between polls accepts one press. B handles the fault as could-not-ask, so this is a price, not a hole; say so.
- C §3: "Q7 holds" — enumerated by the ledger; holds. C §4.2: "the host publish already exists at 127.0.0.1:7878 so enabling SOAP opens nothing new on the host" — true for WotLK; for CMaNGOS C's A6 *adds* a publish and says so.
- C §9: "the queue drains to empty before `Controller.stop()` … waits up to one command deadline, then stops anyway" — correctly a price (30 s), not a guarantee.
- C §4.2: "the same set that can already read `.env` and `.db_password`" — WotLK has no `.db_password` (`write_plan` docstring: "WotLK never comes here"); the root password is in `docker-compose.yml`. Same class, wrong citation.

### (c) Could-not-ask collapsed into yes or no

- A §4 server-state table, attach row "slow": "window elapses → `prompted=True, lines=()` (an honest empty)". On AC the prompt precedes the answer (`console.py:242`: "A command whose output outlives the window" is cut); an empty between prompts is "printed nothing" *or* "the answer came after the window". For a write that is `could_not_ask(timeout)`, not an empty yes.
- A `QueueChannel`: a server-refused row (bad name) is "queued, not yet seen" until the 2 × 60 s delete — A says so; the tab must say *could not tell*, never *no*.
- B §3.1: connect-refused-while-running has no row → falls through to `could-not-ask` by accident, not by design (F1).
- C §4.1 "Crash loop: `RestartCount` growing across two polls" — a single restart that has settled by the second poll reads as up; A/B also read `settled`. C uses `settled` only for Starting.

### (d) Guards that prove a declaration, not an arrival

- B §3.7: the Q7 refusal is a view gate (Install disabled); B admits it (§8) — the seam must refuse too (C's A11, A's applier refusal).
- A §3 `SoapEnable.arrives_by: "recreate"` is a declaration; A's arrival guard is `docker inspect … {{.Config.Env}}` after the recreate (§4 "Relationships 8.1 asserts") — good — but the gate line says "the three `AC_SOAP_*` values in the running container", which proves the env arrived, not that the listener did; the listener is proved by the probe. Both are needed and A has both; keep both in the DoD.
- C's `[ALE]: Searching scripts from` boot line proves the engine loaded; A's `yulon_ping` proves the script loaded. Neither alone; the winner takes both (C §4.7 precondition 1 + A's ping).
- A §4 8.3 TBC `arity_verified: false` → "the gate records the usage line" — F5 settles it today; leave the flag for what is still unread (`probe_expect`, F16).

### (e) Rust incident notes read as measurements on Yu'lon

- A: 5 s / 30 s timeouts (`soap.rs:246-248`) — A says 8.1a measures and re-sets them; acceptable. 12 × 500 ms (`party.rs:321-333`) — inherited by A and B; the 8.6 gate measures.
- C: `SNAPSHOT_TAIL_LINES = 2000`, 2 MiB, keep 10 (`logsnap.rs:52-117`) — reasonable defaults, labelled.
- All three: `botid.rs:4-17` (empty registry with 1000 bots) and `:96-99` (type-3 citizens) — these were live on the Rust side and the OR clause is safe whether or not they recur; fine.
- C §4.2: "The Rust launcher ran SOAP live on Ubuntu 2026-08-20 … which `SOAP.IP` they used is unrecorded" — the one honest use of a Rust note as *not* a measurement. `git log origin/rust-main` shows `af84556d fix(bridge): enforce ALE's absolute ScriptPath on deploy` in that window; the mod-ale SHA that ran is still unrecorded.

### (f) AzerothCore facts inherited onto TBC/Vanilla/Tortoise, or TBC onto Tortoise

- B and C: the SOAP "connects and blocks while starting" model — inherited from `ACSoap.cpp:125-130` and wrong there too (F1).
- C §4.3/§6: the snapshot "ends with the worldserver's `Halting process...`" — an AC string; "same" for CMaNGOS and Tortoise is an inherited exit banner (rule 9). B's wording ("contains the line the Console tab was showing") does not inherit.
- C §4.1: "Shutting down (foreign): connection reset (CMaNGOS)" — unread; the MaNGOS loop tests `IsStopped()` between requests, what an in-flight request sees is unread (A says so: `shutting_down_fault: null`).
- A §3 Vanilla: "Everything not listed is the TBC value" — declared inheritance with a separate gate per core; acceptable under rule 9 and now mostly read (F9).
- A/C: the name-canonicalisation rule (`utf8mb4_bin`) applied to every tree — harmless, but AC-only (F10).
- B §3.5: "no zone column until the `characters.zone` column is read" — it exists on TBC and TW (F9); B was being careful and was wrong in the safe direction.

### (g) Contradictions between the designs, and who is right

| Topic | A | B | C | Right, and why |
|---|---|---|---|---|
| SOAP during map load | refused → `could_not_ask(starting)` | connects and blocks | blocks the SOAP thread for the whole load | **A** — F1 |
| AC SOAP concurrency | "waits on a future"; per-install lock | — | one thread per request | **A** — F2 (single accept loop) |
| Where credentials live | `config_dir()/channels/<game>-<install_id>.json` 0600 | `<server_dir>/.yulon-commands.json` | `config_dir()/credentials/<game>-<install_id>.json` 0600 | **A/C** — `platform.py:68-73` (app state ≠ server data); F14 |
| App account name | `yulon_<install_id>` | `yulon` | `YULON_<install_id>` | **A/C** — F13; AC uppercases anyway (`cs_account.cpp:1058` `Utf8ToUpperOnlyLatin`), so A's lowercase and C's uppercase are the same row |
| Tortoise on native Windows | `pending_commands` queue + verify read | no channel; row routes only | refused | **Owner's call** (A §12 q2, B §11 q1). The route exists (F17); "one outcome" (B/C) is answered by the verify read; the "runs at next boot" hazard is A's refuse-unless-ready + stale-row delete |
| `urn:MaNGOS` | unverified | — | could-not-ask | **read** — F4 |
| `account set password` arity | `arity_verified: false` | three args on AC | — | **read** — F5, three on all |
| Tortoise password cache | attach/queue; sha fallback | row route | blocked on S4 | **read** — F6, not cached |
| Console tab transport | SOAP when verified, attach otherwise | attach, unchanged | attach, unchanged ("no level check" argument) | **A** — the attach console is also `IsConsole()` with no level check (`Chat.cpp:52-56`; CMaNGOS `CliRunnable.cpp:147` `SEC_CONSOLE`); C's reason has nothing behind it |
| Dashboard source | reads + inspect, never a command | reads + inspect; probe ≤ 1/15 s while starting, 0 once ready | reads + inspect + `server info` every 30 s | **A** (B acceptable once F1 is fixed) |
| 8.1 restart of a running world | one press + sentence | two-press arm idiom | Stop/Start through the existing buttons | **B/C** — the tab's own idiom (`:628-646`) and "ask before any restart" |
| Log snapshot bound | none named | "must drain" | 20 s, `.partial`, 2 MiB | **C** |
| Module SQL guard | applier refuses characters/world | view disables + says the seam should refuse | applier refuses characters/world/playerbots + names the step + `db-import` finding | **C** (widest and in the seam) |
| `playerbots` schema under Q7 | not named | not named | refused while up | Q7 names characters/world; C's widening is a design choice, not a fact — say so |

### (h) Seams no design assigns an owner to

| Seam | A | B | C |
|---|---|---|---|
| status poll ↔ command queue | owned: no probe on the timer | owned: cadence/meaning split | owned: budget rule, but the poll is a command |
| Console tab attach ↔ SOAP/attach from another tab, same pty (Tortoise) | **unowned** ("attach otherwise" outside the lock) | **unowned** (per-tab flags) | owned: two-way interlock — but from a UI flag read by the seam |
| `_set_busy` ↔ a job on another tab | Phase 9 | owned (§2.4) | half: `_busy` → "Shutting down (ours)" refuses commands; the other direction unsaid |
| log snapshot ↔ a stop that must not wait | **unowned** (no bound) | owned in words | owned with numbers |
| 8.1 recreate ↔ a running player | **unowned** (one press) | owned (two-press, sentence) | owned (existing Stop/Start) |
| a command in flight ↔ Stop pressed | **unowned** | **unowned** ("a teleport must not disable Stop") | owned (drain ≤ one deadline, then stop) |
| purge ↔ the credentials record | owned (deleted after the folder) | implicit (dies with the dir) | owned (A16) |

---

## 5. Grafts the winner must take, by name

**From B (surface):**
1. §3.1 two-press "Set up" when the world is up, the `REMOVE_IDLE`/`REMOVE_ARMED` arm idiom (`controller_view.py:628-646`) and the restart sentence; one press when down, banner "will be checked when the server starts".
2. §2.4 the ownership table: Server-tab actions lock every command button; a command button never locks Stop; the probe's cadence belongs to the poll (with A's state model underneath).
3. §2.1 `OutcomeLabel`/`ChannelBanner` — one widget for the three outcomes on every tab, so no tab can collapse one.
4. §3.2 `refresh_status()` once at construction (bug 552) and status words rendered from `settled`/`restart_count` (bug 499) on the Server tab itself; the DoD wording "the file contains the line the Console tab was showing" (not "the last line" — a bot server never stops logging).
5. §3.5 the zero-bots-with-characters warning; §3.6 "a timeout is reported as *no* with its cause, never ✓"; §3.7 selection gating (bug 394) and the view-side Q7 sentence; §3.9 `uninstalled` signal up, `drop_controller` stays the window's.
6. §4/§5 gate style: a press through the widget, a screenshot per state, a shell readback typed on the box, a client-visible effect; Windows presses split as their own lines where the mechanism differs (§11 q4).
7. §11 q1 wording for the Tortoise-on-Windows owner question, put beside A's §12 q2.

**From C (risk):**
1. §3 the write ledger and §8 `test_write_ledger.py` (AST over `run_statement`/`run_file`/`os.replace`/`open(..,"w")`, allow-list keyed to ledger rows, a deleted write turns a stale row red).
2. §2 `Applier.running` seam + `test_apply_guard.py` ("with the seam absent the behaviour is byte-for-byte today's"); §4.8 the `db-import` never-applied finding (`apply.py:1365-1367` vs `start_staged`) and §12 q3.
3. §4.2 `AiPlayerbot.CommandServerPort = 0` in the same press, arrival proved inside the container (`/proc/net/tcp`, not `ss`); RA asserted absent.
4. §2 the Console ↔ typed-command interlock — implemented as A's one per-install `AttachChannel` lock that the Console tab's attach also takes, not as a seam reading `_console_pending`.
5. §9 drain-before-stop: a command in flight delays `Controller.stop()` by at most one deadline, then the stop proceeds and says so.
6. §2 `snapshot_logs` bound 20 s, `.partial` → rename, 2 MiB after the line cap, prune excluding the just-written file.
7. §4.2 gate step: a deliberately wrong credentials file → 401 → the reset path → verified again; "evidence before" rows on every gate.
8. §4.6 `BotClause`: refuse on an unreadable or empty live prefix, `^[A-Za-z0-9_]+$` before SQL, `ESCAPE '!'`; one clause imported by 8.2/8.3/8.5/8.6 (`stats.rs:65-67` lesson) — A has this shape; take C's refusal semantics and the live-conf read.
9. §8 `test_credentials.py` asserting 0600 on the fd flags, and the state-machine fixture that answers "exists" the second time.
10. §12 q1 pin mod-ale before the first rebuild.

**Corrections to A from the reads (not grafts):** recreate via `start_staged()` (F3); `port_conflicts_for()` before the recreate; `published_bindings` filtered by project (F15); `arity_verified: true` with F5's citations; `urn:MaNGOS` cited (F4); `tables.*` on TBC/TW marked read (F9); the ALE handler argument cited (F8) with the log line as fallback; the attach "honest empty" re-labelled `could_not_ask(timeout)` for writes; which prefix wins (live conf) stated.

---

## 6. Kickoff §6 checklist, per design

| Check | A | B | C |
|---|---|---|---|
| DoD names live state; unsatisfiable by skip/capture/marker/exit code | **fail (1):** 8.2 "whose last line is the server's last logged line" — a 500-bot server logs between the two reads; the DoD is unsatisfiable on the box it names. Otherwise pass | **fail (2):** 8.1a "a Start shows 'waiting for the world to finish loading' before 'ready'" — never entered under B's model (F1); 8.6 DoD 30 s vs mechanism 6 s | **fail (1):** 8.1 `ss -ltn` inside the AC image (tool unverified); 8.2 "ends with the worldserver's halt line" for CMaNGOS/TW is inherited (rule 9) |
| Every "already has X" resolves at 7bc5ebd3; every "needs Y" names a mechanism and where verified | pass — every anchor I opened resolves (`composegen.py:692`, `docker.py:702/1883/2302`, `accounts.py:93-94/312`, `conf.py:243`, the view anchors) | pass — `:628-646`, `:1292-1304`, `state.py:82`, `main.py:189/216/275`, `catalog.py:931-943` resolve; the existing test names (`:1060`, `:664`, `:696`, `:378`, `:1873`, `:1240`) are for the haiku pass | pass — `apply.py:453` is the Protocol region, the ctor kwarg is `:784` (near miss); `maintenance.py:934-946`, `conf.py:384-392` resolve |
| No step assumes WotLK's mechanism holds for CMaNGOS | pass (Vanilla-from-TBC is declared and gated separately) | **fail:** the starting model (F1); everything else per tree | **fail:** the starting model (F1); "Halting process..." and "connection reset" for CMaNGOS unread |
| No DoD met from a CLI | **fail (1):** 8.1d "queue dry run — `INSERT` a `server info` row" reads as a SQL act; make it a press through `QueueChannel` | pass ("never a CLI") | pass (the blanked-prefix fixture is an edit, then a press) |
| No test names cited that do not exist | pass (all marked new; touched files exist) | pass, pending the citation checker on the six line-cited tests | pass (states the rule and follows it) |
| Every "cannot happen" turned into a price or an enumerated route | **fail (2):** "never an empty list"; "a second install cannot publish 7878" (§4b) | **fail (1):** "cannot corrupt each other's reply" (§4b) | pass — the ledger enumerates; the drain is priced |
| Past tense on the decisions page; "how the code IS" belongs in a test | n/a (a design); §3 present-tense catalog claims become `test_ops_catalog.py` | n/a; §2.4 rules become tests | n/a; §3 becomes `test_write_ledger.py` |

---

## 7. Facts the panel should settle before the decision is written

Each is a read, not a spike; each changes the shape.

1. **Which `SOAP.IP` and which network mode the one live SOAP install runs with.** The owner's live DML VM (reached over Tailscale; its address, key and SOAP credential file are recorded outside the repository) answers `server info` from the host today. Read: `grep -n "^SOAP\." <server>/env/dist/etc/worldserver.conf`, `docker inspect ac-worldserver --format "{{.HostConfig.NetworkMode}} {{.Config.Env}}"`, `docker port ac-worldserver 7878`. If it runs `SOAP.IP = 127.0.0.1` on a bridge network and still answers, every design's `0.0.0.0` premise is wrong; if it runs `0.0.0.0` (or an `AC_SOAP_IP` env), C's S1 spike on the 7.2 install — a conf edit plus a restart — is unnecessary. Read-only; announce it as a VM action all the same.
2. **Whether `docker compose up -d --no-deps` recreates the world on an override env change when db/auth are unchanged.** `docker.py:729-731` measured the three-service form on Docker 29.1.3; the decision should cite that measurement's journal (under `pyplan/gates/` for 2026-08-23) rather than re-measure — and the decision should say the recreate *is* `start_staged()`, so no new compose argv exists to measure.
3. **The CMaNGOS `server info` first line** (F16): read `mangos_string` entry for `LANG_CONNECTED_USERS`/the version line in `cmangos/tbc-db` and `classic-db` at the catalog's pinned SHAs (`5078439a`, `22b51464`) so `probe_expect` is pinned before 8.1c, not by it. Where: the DB repos' `mangos_string` inserts (not fetched; the catalog names the revs).
4. **Whether the AC image ships `ss` or only `/proc/net/tcp`** — `apps/docker/Dockerfile:34,66,122,229` apt lists at 413bea61; decides C's gate line and A's "listener present" capture. If absent, the capture is `docker exec ac-worldserver cat /proc/net/tcp | grep -i ':1EC6 '`.
5. **The mod-ale SHA that ran live on 2026-08-20.** `git log origin/rust-main --since=2026-08-19 --until=2026-08-22 -- cli/lua crates/dml-wow/src/bridge.rs` shows `af84556d` (the ScriptPath fix) but no lockfile; check `origin/rust-main` for a `modules.lock`/manifest rev in that window. If none exists, the pin is `c3de7942` by decision (C §12 q1) and F8 is the only read behind it — say so in the decisions page rather than implying a proven pair.

Two more that are already settled by this read and should be written into the decision as facts, not gates: `urn:MaNGOS` (F4), three-argument `account set password` on all three CMaNGOS-lineage trees (F5), Tortoise's uncached password (F6), addclass bots logging out with their master (F7), and the ALE hook's handler argument at c3de7942 (F8).
