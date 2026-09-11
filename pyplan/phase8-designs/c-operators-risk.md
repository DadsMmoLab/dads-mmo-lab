# Phase 8 from the operator's risk — what each feature can break on a live server

> One of three independent Phase 8 designs (`pyplan/phase8-kickoff.md` §3), written 2026-09-06
> against `yulon-phase7` tip **7bc5ebd3** (paths relative to `pylauncher/`), the delta table
> `pyplan/phase8-delta.md`, the five reads under `pyplan/phase8-reads/`, and the owner's eight
> answers in `pyplan/phase8-parity-decisions.md`, which this page does not re-litigate. The angle
> is the server that is already running with 500 bots: what a button can break, what it writes,
> and what it does when the world thread is busy. Citations: `AC` = azerothcore-wotlk 413bea61,
> `PB` = mod-playerbots b949b50b, `ALE` = mod-ale c3de7942 (unpinned), `TBC`/`VAN` = mangos-tbc
> f82e7d67 / mangos-classic 8ec338a1, `PBC` = cmangos/playerbots 993f1809, `TW` = tortoise-wow
> 7c0fb278, `RUST` = rust-main 21cfdf14. No code was written and no box was touched.

---

## 1. The decision

**Every Phase 8 feature treats the running worldserver as a single-threaded machine it may
interrupt but never race, and the databases as the server's to write.** Concretely: one command
in flight per install, through one queue whose depth is on screen; every command bounded by a
timeout and refused outright when the server is down, still loading, shutting down or
crash-looping (three outcomes, never two); no direct write to `characters`, `world` or the
playerbots schema while `spec.world` is up — the server performs those through its own commands
(Q7), and the module applier that writes them today gains the guard it lacks; every new secret
lives under `config_dir()` at 0600 and never in argv, env or a log; every gate captures the
live rows it is about to change into a file before the change and the same rows after; bots
are counted by both markers and an empty marker refuses to answer rather than answering "all"
or "none"; and the worldserver's log is copied to disk before every stop, so the next
2026-07-21 leaves evidence. Tortoise, which has no SOAP, gets reads and fire-then-verify-by-read
through the attach console, never a `pending_commands` row.

---

## 2. Architecture and module layout

The enforcement lives in five new modules and three additions to existing ones. Nothing in
`controller_wow_wotlk/console.py`, `accounts.py`, `networking.py` or the Phase 7 engine is
replaced; every change is "add after Y".

| Module (new unless said) | Owns | Must never |
|---|---|---|
| `yulon/commands/queue.py` — `CommandQueue` | One FIFO per install (keyed by `server_dir`); at most one command executing; `depth` and `in_flight` as observable properties; per-command deadline; the refusal table by server state (§4 matrix); the three-outcome `Outcome` type (`ok`, `refused(reason)`, `could_not_ask(reason)`) | Run two commands at once against one install; collapse a timeout into a "no"; know a command's text (that is `verbs.py`) |
| `yulon/commands/soap.py` — `SoapChannel` | The HTTP/1.1 Basic-auth POST of one `executeCommand` envelope to `http://127.0.0.1:<soap_port>/` with stdlib `http.client`; connect 5 s / read `deadline`; maps the wire into `ok(text)`, `fault(text)`, `shutting_down`, `unauthorised` (401/403), `unreachable`, `timeout`; the per-tree envelope namespace as data (`urn:AC` for AC per `RUST soap.rs:127-132`; the MaNGOS namespace is **could-not-ask** at this read — measured on the first TBC gate, not inherited) | Put the password in argv, env, a log line, a repr or an exception; retry a command that may have run (a timeout after send is "unknown", not "failed") |
| `yulon/commands/attach.py` — `AttachChannel` | Wraps the existing `console.send_command()` (`console.py:228-336`) for Tortoise as a fire-only channel whose reply is advisory; the verify step is always a `DockerSql.query` the verb declares | Parse a bot-command reply on any CMaNGOS tree (`PBC RandomPlayerbotMgr.cpp:3396-3401`: account id 0 never receives it); write to `tw_logon.pending_commands` |
| `yulon/commands/verbs.py` | The closed set of command texts per tree as data: verb → (text template, min tree level, console allowed, offline allowed, item cap, the DB read that proves it ran); the AC #2695 sanitiser (strip `"`, CR/LF → space, `RUST soap_cmds.rs:249-252`); character-name canonicalisation for the `utf8mb4_bin` column (`AC characters.sql:26`; `RUST soap_cmds.rs:598-600`) | Accept free text (the Console tab keeps that, on attach, unchanged); hold a per-game literal outside the table |
| `yulon/commands/credentials.py` | The app account's name (`YULON_<install_id>`, 14 chars ≤ `MAX_USERNAME` 17, `accounts.py:93`) and 16-char password (`MAX_PASSWORD` 16, `:94`); the file `config_dir()/credentials/<game>-<install_id>.json` written with `os.open(..., 0o600)` the way `families/conf.py:_write` does (`conf.py:384-392`); the state machine `absent → row-written → verified → refused`; the verifier-rotation write scoped by name pattern (§3 A4) | Persist before a SOAP round-trip has succeeded; rotate a verifier for any name not matching `YULON_%`; create a second account when the first exists (`accounts.create_account` is keyed on the name, `accounts.py:543`) |
| `yulon/bots/markers.py` — `BotClause` | The two-arm SQL (`playerbots_account_type IN (1,2,3)` OR `UPPER(username) LIKE '<PREFIX>%'`) on AC; prefix-only on TBC/VAN/TW; reads the live prefix from the installed conf (`env/dist/etc/modules/playerbots.conf` on AC, `etc/aiplayerbot.conf` on the CMaNGOS family — both host-bound); refuses on an empty prefix (`RUST botid.rs:33-38`) | Emit `LIKE '%'`; answer 0 or "all" when it could not read the prefix; derive "human" as NOT-bot for any destructive action |
| `yulon/docker.py` — **add** `snapshot_logs(spec, server_dir, dest_dir, *, wsl_distro)` after `_logs()` (`docker.py:1913`) | `compose ps -a -q <world service>` in the install's own dir, then `logs --tail 2000 <id>` bounded 20 s; `.partial` → rename; 2 MiB cap; retention 10 excluding the file just written | Resolve the container by name (`RUST logsnap.rs:77-84`); block a stop on its own failure |
| `yulon/controller.py` — **add** a pre-stop hook in `stop()` and `remove()` (`:272`, `:294`) before `docker.stop_staged`/`remove_staged` | Calling `snapshot_logs` once per stop; reporting a snapshot failure in the returned message rather than raising | Skip the stop because the snapshot failed |
| `yulon/apply.py` — **add** a `running: Callable[[], InstallStatus] \| None` seam on `Applier` (after `sql`, `:453`-region) checked in `_sql()` (`:1359`) | Refusing a `sql` step whose `db` is `characters`, `world` or `playerbots` while `spec.world` is up; naming the step in `log.skipped` | Refuse an `auth` step (same class as the three inherited writes); stop the server itself |
| `yulon/dashboard.py` | Composing `docker.container_state()` (`docker.py:1883`), the `BotClause` counts and one SOAP `server info` into a `Dashboard` dataclass with three-valued fields (`int \| None` + a reason); the poll budget (§4.2) | Poll faster than the budget; call `docker.status()` itself (the tab already does, `ui/controller_view.py:729-732`) |

**Call paths that exist today, and where the new pieces sit on them.**

- Status: `QTimer(5000)` → `ControllerView.refresh_status` → `Controller.status()` (`controller.py:127`) → `docker.status()` (`docker.py:1788`). The dashboard is a **second** job on a slower timer, not a change to this one (bug line 552 stays a bug line; 8.2 does not fix it by side effect).
- Stop: `ControllerView` → `Controller.stop()` (`:272`) → `docker.stop_staged()` (`:1648`) → `compose stop -t 300` (`:1748`). The snapshot hook goes between the first two.
- Console: `send_console_command` → `_send` (guarded by `_console_pending`, `ui/controller_view.py:1323-1347`) → `services.send_console` → `console.send_command`. Untouched. The `CommandQueue` is a sibling guard for typed commands; the two are joined by one rule: **a typed command is refused while `_console_pending` is true and vice versa**, because both reach the same world queue (`AC World.h:221`) and, on Tortoise, the same pty.
- Jobs: `threaded_job_runner` runs **each call on its own `QThread`** (`ui/widgets/job.py:160-165`, `:189`). Nothing serialises two service calls today except the per-kind pending flags; that is why the queue is a controller-side object and not a UI flag.
- SQL reads: `apply.DockerSql.query` (`apply.py:504-528`), password in `MYSQL_PWD` env (`:410-433`), never argv. Every 8.2/8.3/8.5 read uses it unchanged.
- Accounts: `accounts.create_account(sql, name, pw, gm_level=3)` (`accounts.py:312`) is the write 8.1 reuses for the app account, through `_for_wotlk`'s existing `sql` (`ui/controller_view.py:341-346`).
- Conf: `families/conf.patch()` (`conf.py:130`) and `apply_table()` (`:243`, write-on-change, temp+rename at `:362-`) are the seams 8.1 uses to flip `SOAP.Enabled` on the host-bound conf.

---

## 3. The write ledger

Every write Phase 8 adds (A) or inherits (W). "Running?" is whether the worldserver may be up
when the write lands. "Who asserts it" names the test that enumerates the write and fails on
one it does not know (§8, `test_write_ledger.py`, new).

| # | Write | Database / file | Writer | Running? | Crash mid-write leaves | Second press | Who asserts |
|---|---|---|---|---|---|---|---|
| W1 | `create_account` | auth: `account`, `realmcharacters` (server-wide, `accounts.py:382-393`), `account_access` / `account.gmlevel` / `account.rank` | app, `docker exec mysql` | yes, no guard (`yulon.md` §8) | row without counters/GM; converges on retry (`accounts.py:327-337`) | finds the row, keeps its password, raises level only (`:339-343`) | `test_accounts.py`, `integration/test_accounts_live.py` |
| W2 | realmlist `UPDATE … WHERE id=1` | auth `realmlist` | app | yes, no guard (`networking.py:3617-3632`) | one statement; nothing torn | same value (`bug41` gate: second press leaves the row alone) | `test_networking.py` |
| W3 | module SQL, conf, deploy, patches, client, dbc | any of auth/characters/world/playerbots (`apply.py:60`, `:1359-1387`); server dir; client dir | app | yes, **no guard** — 8.7 adds one (A11) | a half-applied `.sql` file; no marker | re-runs every step (`apply.py:907-912`) | `test_apply.py` + new `test_write_ledger.py` |
| W4 | restore | every DB the dump names | app | **refused** while world/auth up (`maintenance.py:934-946`) | the interrupted-restore marker | plan first, then restore | `test_maintenance.py` |
| W5 | backup (hot) | files under `backups_dir(server_dir)` | app | yes, allowed (`:544` records it) | `.partial` never renamed (`:551`) | new files | `test_maintenance.py` |
| W6 | `reset_unfinished` `DROP DATABASE` | auth/characters/world | app | guarded on content, not state (`repair.py:257-266`) | a dropped schema (by design) | refuses `populated` | `test_repair.py` |
| W7 | `.env` merge, project pin | server dir `.env` | app | install time | atomic replace (`composegen.py:772-790`) | replaces in place | `test_composegen.py` |
| A1 | `SOAP.Enabled = 1`, `SOAP.IP = 0.0.0.0` | AC `env/dist/etc/worldserver.conf`; TBC/VAN `etc/mangosd.conf` (both host-bound: `base.yml.tmpl:278`, `shared/cmangos/base.yml.tmpl:110`) | app via `conf.patch` | yes (file); **value arrives only at the next worldserver start** (`AC Main.cpp:337`; `TBC Master.cpp:248`) | temp+rename: old or new, never torn | `apply_table` writes nothing when unchanged | `test_conf.py` + new `test_soap_setup.py` |
| A2 | `AiPlayerbot.CommandServerPort = 0` | AC `env/dist/etc/modules/playerbots.conf` | app via `conf.patch` | yes; arrives at next start | as A1 | as A1 | new `test_soap_setup.py` |
| A3 | the app account row, GM 3 | auth (via W1 with `gm_level=3`) | app | yes | as W1 | as W1 — no second account ever (`RUST soap_autosetup.rs:116-120` is the hazard; W1's name key is the defence) | new `test_credentials.py` |
| A4 | verifier rotation for the app account | auth `account.salt/verifier` (AC), `v,s` (mangos_srp6) — `WHERE username = 'YULON_<id>'` | app, new seam | yes | one statement | rotates again; the file is rewritten after the round-trip | new `test_credentials.py` asserts the WHERE by field |
| A5 | credential file | `config_dir()/credentials/<game>-<install_id>.json`, 0600 | app | n/a | temp+rename | overwritten only after a successful round-trip | new `test_credentials.py` (mode, never in repr/log) |
| A6 | CMaNGOS SOAP port publish | `shared/cmangos/base.yml.tmpl` `ports:` gains `${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:{{SOAP_PORT}}}:7878` | install engine (render) | install time; an existing install needs the base file regenerated and its containers **recreated** (a running container keeps its old ports) | `write_plan` compares bytes, writes whole file | unchanged bytes → no write | `test_catalog_invariants.py` ("ports in exactly one file") |
| A7 | log snapshot | `config_dir()/logs/<game>-<install_id>-<stamp>.log` | app | yes (pre-stop) | `.partial` | a new file per stop; prune keeps 10, never the one just written | new `test_logsnap.py` |
| A8 | set password | auth, **by the server** (`.account set password`, AC `cs_account.cpp:1044-1055`; TBC/VAN `SEC_CONSOLE` reachable over SOAP, `cmangos.md` A) | worldserver | yes | server's own statement | same value | new `test_gm_verbs.py` |
| A8t | set password, Tortoise | auth `account.sha_pass_hash` direct (`accounts.py:430` has the hash) — **could-not-ask** whether the server caches passwords (`TW AccountMgr.cpp:250-256` caches *rank*) | app | yes | one statement | same value | blocked on the m910q read (§9) |
| A9 | GM level | auth by the server (AC `.account set gmlevel <a> <n> -1`; TBC/VAN `UPDATE account SET gmlevel`, `Level3.cpp:1126`; TW `account.rank` + cache, `Commands.cpp:321`) | worldserver | yes | server's | same value | new `test_gm_verbs.py` |
| A10 | teleport / revive / level / rename / mail / money | **characters, by the server**: `SavePositionInDB` (`AC cs_tele.cpp:156`), `OfflineResurrect` (`cs_misc.cpp:1217-1221`), `at_login`, `mail`/`mail_items`/`item_instance` | worldserver | yes — this is Q7's sanctioned route | the server's transaction | teleport/level/rename/revive: same end state; **mail: one more mail per press** (§4.4) | new `test_gm_verbs.py` (text by field); live gate rows |
| A11 | module SQL guard | as W3 | app | **refused** for characters/world/playerbots while `spec.world` up; auth allowed; conf/deploy allowed with "restart required" | as W3 | as W3 | new `test_apply_guard.py` |
| A12 | manifest refresh cache | `config_dir()` (`manifest_store.py:35`) | app | n/a | ETag file | revalidates | `test_manifest_store.py` |
| A13 | bridge scripts + `mod_ale.conf` | server dir `env/dist/etc/modules/lua_scripts/`, `mod_ale.conf` | app via Applier deploy/conf | yes (file); ALE reads scripts at boot or `reload ale` (`ALE PlayerHooks.cpp:44-54`) | a half-copied dir → `reload ale` loads what is there | overwrite | `test_apply.py` |
| A14 | bots made by `dml_addclass` | characters + `playerbots_account_type` type 2, **by the server** (`PB PlayerbotMgr.cpp:84`, `:131-136`) | worldserver | yes | server's | **one more bot per press** up to `MaxAddedBots` 40 (`playerbots.conf.dist:136`) | live gate rows |
| A15 | Steam shortcuts | `<steam>/userdata/<id>/config/shortcuts.vdf`, `grid/*.png`, `config.vdf` compat mapping — the **client machine**, not the client folder | app | Steam **must be closed** | temp+rename + `.yulon-bak`; a torn binary VDF loses every non-Steam shortcut | keyed by app id: replace, not append | new `test_steam_shortcuts.py` |
| A16 | purge | containers, volumes (unless kept), images, server folder, `state.json` record | app | **refused** while running | folder half-deleted, record intact (`state.forget()` last) | `plan()` shows what remains; deleting the absent is a no-op | new `test_purge.py` |
| A17 | party presets | `config_dir()/party/*.toml` | app | n/a | temp+rename | overwrite | new `test_party.py` |

**Q7 holds**: every `characters`/`world`/`playerbots` row Phase 8 changes is written by the
worldserver through a command (A8–A10, A14), and the one app-side path that could write them
(W3) is guarded by A11. The two app-side auth writes added (A3, A4) are the same class as W1.
No Phase 8 write goes to `world`. A test enumerates every `run_statement`/`run_file` call site
and every `open(..., "w")`/`os.replace` under `yulon/` via `ast` and fails on one absent from this
table (§8).

---

## 4. Per-feature risk, per step, per tree

### 4.0 Facts that price every row

| Fact | Number | Source |
|---|---|---|
| World tick with 500 bots and mod-ale | lag-tail floor ~155 ms, from `BotActiveAlone` rotation, not bot count; `MapUpdate.Threads` stays 1 | memory `mapupdate-threads-1-with-mod-ale` |
| Where a command runs | queued on `_cliCmdQueue`, drained once per `World::Update`, synchronously | `AC World.cpp:1341`, `:1641-1657`; `TBC World.cpp:1748`; `TW World.cpp:3079` |
| Latency floor of any command | wait for the next tick (0–155 ms) + its own run; the run **stalls every bot for its duration** | derived from the two rows above |
| The instrument | `.server info` prints `Update time diff` and mean/median/p95/p99/max of the last N diffs | `AC cs_server.cpp:275-294` |
| SOAP concurrency, AC | one thread per request, all queue on the same world queue and block | `ACSoap.cpp:120-130` |
| SOAP concurrency, TBC/VAN | **one request at a time**, accept → serve → destroy; 50 ms poll | `MaNGOSsoap.cpp:51-63`, `:127-128` |
| Shutdown | AC returns receiver fault "Server is shutting down" within 1 s once `World::IsStopped()` | `ACSoap.cpp:125-130` |
| Stop grace | 300 s; measured drains 90.7 / 73.4 / 58.3 s at ~1980 characters, 7400–7700 queued saves | `docker.py:938-980` |
| `docker inspect` cost | ~0.3 s CLI start; 7 invocations overran a 2 s poll | `docker.py:1883-1897` |
| `docker ps` worst case | hung 8+ min on yulon-win11 under memory pressure; 30 s timeout | `docker.py:925-935` |
| Attach console cost | ≥ 3.6 s per command, one writer, reply mixed with 6–44 bot-login lines | `console.py:45`, `:76`, `:485-486` |
| Worldserver memory | ~4–5 GB RSS | memory `server-and-build-memory-budget` |
| Tortoise remote channel | none returns output; `pending_commands` polled every 60 s | `TW World.cpp:2724`, `:845`, `:3946-3948` |

### 4.1 The server-state matrix (applies to every step)

| State | How the seam knows | What the app shows | What it never does |
|---|---|---|---|
| Down | `docker.status()` lacks `spec.world` | "Server is down" on every command button, disabled | fire anything; count bots as 0 (shows "—") |
| Starting | `container_state().settled` and this-run log lacks `ReadySpec.world` (`docker.py:2121-2219`, `_logs(this_run_only=True)`) | "Loading maps — N s since start" | fire a SOAP command (it would block the SOAP thread until the first tick, up to the 480 s ready budget) |
| Up | ready marker seen this run | dashboard live, buttons armed | — |
| Shutting down (ours) | `ControllerView._busy` | "Stopping — saves draining, up to 300 s" | fire; poll the dashboard |
| Shutting down (foreign `docker stop`) | receiver fault text "Server is shutting down" (AC) / connection reset (CMaNGOS) | the fault, verbatim | retry |
| Crash loop | `RestartCount` growing across two polls (`ContainerState.restart_count`, `docker.py:1862-1897`) — bug line 499 | "restarted N times in the last M s" in red; buttons disabled | report "up" (the current tab does) |
| Foreign server on 7878 | `server info` first line names the core: `AzerothCore rev.` (`RUST status.rs:88`) vs the MaNGOS revision line (`TBC Level0.cpp:104-107`) | "another server is answering on 7878" | send a second command |
| Docker unreachable | `DockerCommandError` from `status()` | "could not ask Docker" | show "down" |
| SOAP 401/403 | `credentials` state → `refused` | "the app's account no longer authenticates — Repair" | create a second account |
| WSL-resident (`wsl_distro`) | `KnownInstall.wsl_distro` set | commands only after `wsl.is_running` (`controller.py:174-181`) | start the distro from a poll |

### 4.2 8.1 — the command channel

| | AC (WotLK) | TBC / VAN | TW |
|---|---|---|---|
| What can break | a wrong command with no level check (`AC Chat.cpp:52-56`) — `server shutdown`, `account delete`, `character erase` are all one string away; a SOAP thread blocked for a whole map load if fired while starting; the host publish already exists at `127.0.0.1:7878` (`base.yml.tmpl:264`) so enabling SOAP opens nothing new on the host | same, at `SEC_CONSOLE` regardless of caller (`MaNGOSsoap.cpp:113`); the template publishes **no** SOAP port — A6 adds one, loopback-pinned by default value the way WotLK's is | nothing to enable; the risk is a design that writes `pending_commands` (an auth write that runs a console command 60 s later with no reply) — **refused** |
| World-thread cost | the verification round-trip is one `server info` (counters only) | same | none |
| Loopback inside the container | **load-bearing, unverified**: `SOAP.IP` defaults to `127.0.0.1` inside the container (`worldserver.conf.dist:460`; `mangosd.conf.dist.in:1859`), where a docker port publish cannot reach it. The design sets `SOAP.IP = 0.0.0.0` and keeps the loopback pin on the **host** binding, which is what `base.yml.tmpl:258-263` says the pin is. Price: SOAP is reachable from every container on the compose network — enumerated: `ac-database`, `ac-authserver`, `ac-db-import`, `ac-client-data-init`; none is user-reachable. The Rust launcher ran SOAP live on Ubuntu 2026-08-20 (`RUST bridge.rs:189-195`) and the owner's VM has it on; which `SOAP.IP` they used is unrecorded — spike S1, §9 | same; compose network `{{CONTAINER_PREFIX}}net` | — |
| Fail-safe direction | connection refused / 401 / timeout are three different outcomes; only `ok("...AzerothCore rev...")` marks the channel verified | same | attach only; `prompted=False` is ambiguous by construction (`console.py:98-102`) and is reported as could-not-ask |
| Double press | `create_account` keyed on name → same row; the file is written only after verification; a second press with the file present re-verifies and writes nothing | same | — |
| Restart needed | yes: the conf is read at start (`Main.cpp:337`). 8.1 asks for it as a Stop/Start through the existing buttons (the log snapshot of 8.2 fires on that stop) — never a `docker restart` behind the user's back | yes (`Master.cpp:248`) | — |
| Playerbot Command Server | on by default at 8888 inside the network (`PB conf:2517`), unauthenticated, off the world thread, reaches any loaded bot's AI (`PlayerbotCommandServer.cpp:47-67`). Price of leaving it on: any process in the four containers can drive any bot; not host-published (`base.yml.tmpl:256-264`). 8.1 sets `AiPlayerbot.CommandServerPort = 0` (A2) in the same press, and the gate asserts the value arrived by `ss -ltn` inside the container showing no 8888 | already 0 (`PBC PlayerbotAIConfig.cpp:277`) — asserted, not assumed | already 0 (`TW PlayerbotAIConfig.cpp:358`) — asserted |
| RA | stays `Ra.Enable = 0` (`worldserver.conf.dist:419-445`); the gate asserts no listener on 3443 | `Ra.Enable = 0` (`mangosd.conf.dist.in:1851`) | absent (`TW src/mangosd/CMakeLists.txt:19-30`) |
| Credentials | `config_dir()/credentials/…json` 0600 via `os.open` (Linux/macOS); Windows inherits the per-user `%APPDATA%` ACL. Who can read it: the same user, root, anything running as the user — **the same set that can already read `.env` and `.db_password`** (`cmangos.py:161`, `conf.py:75-83`), which hold DB root; the GM-3 SOAP account grants nothing DB root does not. The marginal exposure is zero for a local reader; the new hazard is the app's own misuse, which the closed verb table bounds | same | none |
| Password changed in-game / account deleted | both arrive as 401 (`ACSoap.cpp:84-101`) → state `refused` → "Repair" = A4 (rotate the verifier, name-scoped) or A3 (recreate) — the user is told which; never silent | same (`MaNGOSsoap.cpp:78-97`) | — |
| Second install on the box | different path → different `install_id` (`composegen.py:135`) → different name and file; README §12 says one server at a time on 7878, and a mismatched server answers 401 (its auth DB lacks the account) or a different `server info` first line | same | — |
| Evidence before | `docker port <world> 7878`, `ss -ltnp` on the host for 7878/8888/3443, the conf keys' current lines, `SELECT COUNT(*) FROM account`, `SELECT id,gmlevel,RealmID FROM account_access`, `.server info` text | same tables under the CMaNGOS names | — |
| Proven for real | the app's tab shows "Command channel: verified <time>", the `account` row exists with `account_access(id,3,-1)`, and `server info` text from the round-trip is in the capture; then a deliberately wrong password file → 401 → the Repair path → verified again | same | the tab says "Tortoise: attach console + reads only", and a `server info` through attach appears in the Console tab |

**The polling budget.** The existing 5 s `docker ps` stays. The dashboard adds: `container_state`
for `spec.world` every 15 s (one inspect, 0.3 s); the two bot-clause `COUNT(*)` reads every 15 s
(one `docker exec`, indexed `online` column, `AC characters.sql:106`); `server info` over SOAP
every 30 s (one world-thread command, counters only). Total ≈ 1 command per 30 s on a thread
that ticks ~6 times a second — 1 in ~190 ticks. Budget rule: a poll that overruns its interval
skips the next (the queue refuses a poll while a poll is in flight), and the tab shows "dashboard
stale N s" rather than the last number as if current. On TBC/VAN the SOAP server serves one
request at a time (`MaNGOSsoap.cpp:51-63`), which is a second reason the queue is single-flight.

### 4.3 8.2 — dashboard and the log snapshot

| | AC | TBC / VAN | TW |
|---|---|---|---|
| What can break | a poll queued behind a slow command; a `docker ps` hang (30 s timeout, `docker.py:935`); a snapshot that holds the stop | same | attach-only `server info` costs 3.6 s and a pty; the dashboard uses **reads only** on TW (`characters.online`, `tw_logon.account` prefix, `container_state`) — no console poll |
| Bot count | both arms (registry types 1,2,3 OR prefix); empty registry with 1000 bots playing (incident 2026-08-01, `RUST botid.rs:4-17`) and 400 type-3 citizens (2026-08-10) are both handled by the OR | prefix only (`PBC PlayerbotAIConfig.cpp:939`; dist `RNDBOT`, `aiplayerbot.conf.dist.in:57`); Yu'lon patches the count keys but not the prefix, so the dist value is live — **read, not assumed** | prefix only in `tw_logon.account` (`aiplayerbot.conf.dist.in:63`) |
| Crash loop | `RestartCount` delta between two polls → red; commands refused | same (bug line 499 is exactly this on TBC) | same |
| Snapshot | `docker compose ps -a -q ac-worldserver` in `server_dir`, then `logs --tail 2000` (`RUST logsnap.rs:277-327`); 20 s bound; written before `compose stop` | service `{{CONTAINER_PREFIX}}mangosd` | same |
| Snapshot failure | reported in the stop message ("log not saved: <reason>"); the stop proceeds — a hung `docker logs` must not hold a server the user asked to stop | same | same |
| Evidence before | `RestartCount`, `StartedAt`, both counts, `server info` text | same | reads only |
| Proven for real | with the WotLK server up and bots logged in: the tab shows players/bots/uptime/update-diff; `docker kill` the world once → the tab shows "restarted 1 time"; press Stop → a file appears under `config_dir()/logs/` whose last line is the worldserver's `Halting process...` and whose size is ≤ 2 MiB, captured beside the `compose stop` transcript | the same on one CMaNGOS box; the dashboard's bot number equals `SELECT COUNT(*) … LIKE 'RNDBOT%'` run by hand | TW: counts and the snapshot; no uptime line |

### 4.4 8.3 — accounts

| | AC | TBC / VAN | TW |
|---|---|---|---|
| What can break | lowering the **app's own** account below 3 kills the channel; deleting is not offered; `account set password` echoed anywhere; the `LAST_INSERT_ID` hazard (`RUST account_write.rs:173-175`) — Yu'lon reads the id back by name (`accounts.py:543`) | same at `SEC_CONSOLE` over SOAP; `gmlevel` 0–3 and the caller must outrank (`TBC Level3.cpp:1098`, `:1112`) — SOAP at `SEC_CONSOLE` may grant 3 | `.account set gmlevel` `SEC_ADMINISTRATOR`(4) on TW's enum (`Commands.cpp:293`, `:307`), reaches `account.rank` and the in-memory cache through the server; via attach the password would be **echoed into the pty window** — refused; A8t is a direct hash write (Q7-legal, auth) pending the cache read |
| Relationship asserted | target `username != YULON_<id>` for password and level; the list marks the app account and greys it | same | same |
| Bot accounts in the list | excluded by the same `BotClause`, both arms (the 403-row picker incident, `RUST pages.rs:281-287`) | prefix arm | prefix arm |
| Double press | same value → same row | same | same |
| Evidence before | `account`, `account_access` rows for the target; `.server info` | `account.gmlevel` | `account.rank`, `sha_pass_hash` |
| Proven for real | a human account's password set from the tab and the **client on vmhost logs in with it**; its level set to 1 and the row reads 1; the app account's own row cannot be edited from the tab | same on one CMaNGOS box | TW: level via attach and the row reads it; password blocked until §9 S4 |

### 4.5 8.4 — teleport, item search, mail, revive, level, rename, money, gear sets

| | AC | TBC / VAN | TW |
|---|---|---|---|
| World-thread cost, per verb | unmeasured; each is one queued command that runs synchronously; `.send items` builds a mail in one transaction; `.teleport name` on an **online** player runs a live teleport (map change) — the gate captures `Update time diff … max` before and after each verb, from `server info`, into the capture file | same | attach: 3.6 s wall each, cost on the thread the same |
| What can break | teleporting an online player out of an instance or mid-combat (no refusal exists server-side); mailing to an offline name creates items nobody asked for; `.send items` newline injection (AC #2695) → sanitiser; name mismatch on the `utf8mb4_bin` column → pre-checks canonicalise; item cap 12 (`Mail.h:33`) | TBC 12, **VAN 1 item per mail** (`VAN Mail.h:49`); `.send items` `SEC_ADMINISTRATOR` — fine at `SEC_CONSOLE` | 1 per mail (`TW Mail/Mail.h:51`); **no set-level** (`Chat.cpp:151-165`); rename is top-level `.rename` (`:850`) |
| Online vs offline | offline handled by the server (`ChatCommandTags.cpp:122-129`; the offline branches cited in §3 A10); the app shows which it is from `characters.online` **before** firing and says "will apply now" / "will apply at next login" | same via `ExtractPlayerTarget` (`TBC Chat.cpp:3337-3388`) | same |
| Double press | teleport/level/rename/revive converge; **mail does not** — the button disables while any mail for that target is queued or in flight, and the tab lists sent mails with their `mail.id` read back; the price of a press after an app restart is one extra mail the player can delete: named, accepted | same | same |
| Gear sets | read `character_inventory ⋈ item_instance ⋈ item_template`, bag 0, slot < 19 (derived from `Player.h:681`, `azerothcore.md` note 6); mail in chunks of 12; a 19-piece set = 2 commands | TBC 2 commands; **VAN 19 commands** — the queue shows "19 queued", and the send is one job that can be cancelled between mails | 19; inventory schema **could-not-ask** (`delta` row) — gear sets on TW wait for the read |
| Evidence before | target's `characters` row (`position_x/y/z, map, level, at_login, online`), `SELECT COUNT(*) FROM mail WHERE receiver = <guid>`, `server info` diff summary | same, under CMaNGOS names | same under `tw_char` |
| Proven for real | each verb once on an **offline** character and once **online** with the client on vmhost: the row changes as expected and the in-game effect is on screen (the mail in the mailbox; the character at the location) | same on one CMaNGOS box | TW: fire via attach, prove by the row |

### 4.6 8.5 — Browse Bots

Reads only, off the world thread. `COUNT(*)` plus one page (`LIMIT` clamped to 200, `RUST
pages.rs:143-145`) with the `BotClause`. What can break: an empty prefix compiling to `LIKE '%'`
(every account is a bot) or an empty registry (no account is) — the fail-safe is **refuse to
answer**: the tab shows "could not read the bot marker from `<conf path>`" and no list. Asserted
relationship: when `characters` has rows on accounts the clause matches, the count is ≥ 1; a
prefix read from the conf must be non-empty and match `^[A-Za-z0-9_]+$` before it enters SQL;
`%`/`_` in a user's name filter are escaped with `ESCAPE '!'` (the `sql_mode` trap,
`RUST account_write.rs:64-73`). Proven for real: the count on the tab equals the hand-run query in
the capture, on all four boxes (Q4), with the WotLK number split "random / addclass / citizen".

### 4.7 8.6 — My Party (WotLK only, mod-ale bridge over SOAP)

| Question | Answer, and what is asserted live |
|---|---|
| What must be true before the button is drawn | (1) the worldserver **was compiled** with mod-ale — a clone under `modules/` is not that; the proof is the ALE boot line `[ALE]: Searching scripts from` in **this run's** log (`ALE LuaEngine.cpp:135`, read through `_logs(this_run_only=True)`); (2) `ALE.Enabled = true` is in effect — the compiled default is `false` (`ALEConfig.cpp:20`), so the key must be present and the boot line is the guard that the value arrived; (3) `ALE.ScriptPath` is the absolute container path (`mod-ale.json:17-19`) and the six scripts plus a new `dml_ping.lua` exist on the host side of the bind; (4) the bridge answers: `dml_ping` over SOAP returns the script's reply, not "Command does not exist" (the silent-bridge bug, `RUST bridge.rs:189-195`); (5) the channel is verified (8.1); (6) the player is online by canonical name; (7) party size and `MaxAddedBots` (40, `PB PlayerbotMgr.cpp:131-136`) have room |
| What the app does when one is false | draws the reason instead of the button: "mod-ale is not compiled into this server (rebuild, 2–4 h, from the Modules tab — asks first)" / "ALE is disabled" / "scripts missing" / "bridge not answering" / "player offline" / "party full". Never a greyed button with no sentence |
| World-thread cost | `dml_addclass` → `AddPlayerBot` → a full bot login (character load) synchronously; then two whispers per bot (spec, autogear) — `autogear` equips from the item store; **unmeasured**. The gate captures `server info`'s diff summary before, after one add, after four |
| Double press | a second `dml_addclass` adds a second bot; the app snapshots `group_member` (`RUST party.rs:267-268`) before firing, disables the button until the new member appears (12 × 500 ms poll, `party.rs:321-333`) or the poll expires with "could not confirm — check the party frame", and shows "N of 40" |
| Bot left in the group after logout | whether `PlayerbotMgr` logs out addclass bots on master logout is **could-not-ask** at this read; the cost if not: a bot session on the world thread and in `characters.online` until server restart. `dismiss-all` reads the party's bots with **both** marker arms — addclass accounts are type 2 in the registry and carry **no** prefix, so a prefix-only read is the "party of bots read as zero" incident (`party.rs:271-276`) inverted |
| mod-ale is unpinned | the manifest names no rev (`mod-ale.json:9-11`); a rebuild takes whatever HEAD is that day. The design pins `c3de7942` in the manifest before the first 8.6 rebuild — the owner starts the rebuild, so the pin is question 1 in §12 |
| Rebuild | 2–4 h, the owner's to start; never from a gate script. `build.rebuild` is data nothing acts on today (`manifest.py:103-106`) — 8.6 wires it to a compose build with the explicit `-f` set (`RUST destructive.rs:26-37`), with the world **stopped** and the snapshot taken |
| Evidence before | `group_member` rows for the player; `playerbots_account_type` counts by type; `characters.online` count; `server info` summary; `mod_ale.conf` bytes; the ALE boot line |
| Proven for real | on yulon-ubuntu after the owner's rebuild: the client on vmhost is in-game; the tab adds a warrior; the party frame shows it (vmshot from the host); `group_member` gained one row on a type-2 account; `dismiss-all` removes it and the row is gone; the diff summary before/after is in the capture |

### 4.8 8.7 — module update checks and CMaNGOS manifests

What can break: module SQL into a live `characters`/`world` (W3 → A11 refuses it while
`spec.world` is up, names the step, and the Modules tab shows "stop the server to apply N SQL
steps"); a conf write whose value never arrives (the memory `bot-population-is-500`: a running
container keeps its old value) — the report says "restart required" and the gate proves arrival
by the server's own log or `.reload config` (`AC cs_reload.cpp:351-354`, ADMINISTRATOR, console
yes); an update check that touches nothing but the clone (`git fetch`, network only). One
finding to name: `applied_by: db-import` steps are logged "left to ac-db-import on next start"
(`apply.py:1365-1367`) and **`start_staged` never runs the one-shot** (`controller.py:272-292`
docstring), so on an installed server those steps are never applied. 8.7 either routes them
through `docker.run_one_shot` (`docker.py:1357`) with the world stopped, or says so on the tab;
which is §12 question 3. On the CMaNGOS family a "module" is a conf patch table or a SQL mod
against `mangos`/`tw_world` (no module dir, `lab.md` F5) — the same guard, the same `conf.patch`
seam, the same "value arrives" proof from `mangosd`'s own boot log. Tortoise: `Database.AutoUpdate`
is a world write the server performs at boot (`cmangos.md` Step 1); a manifest must never run it
while up.

### 4.9 8.8 — Steam (Linux / Steam Deck)

No server contact. What can break: a torn `shortcuts.vdf` loses every non-Steam shortcut the
user has; writing while Steam runs is overwritten on Steam's exit. The design: refuse while Steam
is running (three outcomes: running / not running / could not tell, from the `steam.pid` file and
`pgrep`), temp+rename, keep `shortcuts.vdf.yulon-bak`, key entries by app id so a second press
replaces. Whether Steam's userdata tree falls under Q5's client-folder prohibition is the
owner's (§12 question 2). Format and compat-tool mapping are UNVERIFIED (`delta` could-not-ask 6):
a read of a real Steam userdata folder is the spike, on no server box.

### 4.10 8.9 — uninstall / purge (mechanism decided; the risk section)

| Risk | What the design does |
|---|---|
| Running server | refused; "Stop first" — the stop takes the 8.2 snapshot, so the last log survives the purge |
| Ownership unproved | `remove_staged`'s census refuses (`phase8-decisions.md` "Ownership is proved") |
| Order | containers → volumes (unless kept) → images → folder → `state.forget()`; the folder last because the compose files are what resolve the project; a crash after the folder leaves a record pointing at nothing, which the tab can still forget by hand |
| Second press | `plan()` is a fresh census; absent things are not errors |
| Keep-characters | the volume survives; `install_id` is path-derived so a reinstall to the same path finds it — owed a test (that page's open item 2) |
| Backups | never removed; the dialog offers "Back up now" first (W5, hot, allowed) |
| Windows read-only git objects; WSL folder | `onerror` chmod retry; `wsl.exe` removal inside the distro |
| Evidence before | `docker ps -a`, `volume ls`, `image ls` filtered by project; folder size; the state record |

---

## 5. Delivery order and gates

WotLK first on every step (Q3), one box per family, every gate from a checkpoint, announced on
`claude-say`, captured **before** it changes anything. Boxes and why they are safe: **yulon-ubuntu**
(Hyper-V, checkpoint `pre-7.2-gate-2026-09-02` under `clean-ssh`; the 7.2 WotLK install intact,
Off at baseline; the 3.3.5a client on vmhost at `C:\clients\WoW-WotLK-3.3.5a-min` logs in after the
LAN step — `notes/STATE-2026-09-06-morning.md`) for WotLK; **m910q** (Tortoise running since 2026-08-26;
all four clients under `~/clients`; stale `r5`/`r6`/`fw5` containers and `http.server` processes
to be removed first) for Tortoise; **yulon-fedora** (Py 3.13, SELinux Enforcing, Off at baseline)
for TBC and Vanilla — never two servers on one box at once (README §12), never a compile beside a
running server. The owner's DML VM is never a gate box. Windows repeats on **yulon-win11-gate**
after each family's Linux gate.

| Step | What lands | Gate (box; capture path; visible effect) |
|---|---|---|
| **8.1** | `commands/{queue,soap,attach,verbs,credentials}.py`; A1/A2 conf patches; A6 template line; the Command-channel panel on the Server tab | yulon-ubuntu, WotLK: capture ports/conf/rows/`server info` → press "Enable command channel" → Stop → Start → "verified" on the tab; `ss -ltn` inside the container shows 7878 and no 8888; wrong-password file → 401 → Repair → verified. `pyplan/gates/8.1-wotlk-ubuntu-<date>/`. Then yulon-fedora TBC (namespace measured), Vanilla; m910q Tortoise shows the attach-only sentence |
| **8.2** | `dashboard.py`; `docker.snapshot_logs`; the `Controller.stop()` hook | yulon-ubuntu: the dashboard numbers equal hand-run queries in the capture; `docker kill ac-worldserver` → "restarted 1 time"; Stop → snapshot file ending in `Halting process...`. `8.2-wotlk-ubuntu-<date>/`; one CMaNGOS box; m910q TW |
| **8.3** | Accounts list / set password / GM level | yulon-ubuntu: password set → client on vmhost logs in; level 1 → row 1; app account uneditable. `8.3-…`; one CMaNGOS box; TW level only |
| **8.5** | Browse Bots | all four boxes, each count equals the hand query; WotLK split by type. `8.5-…` |
| **8.4** | the eight verbs, gear sets | yulon-ubuntu: every verb offline and online, rows before/after, mail in the mailbox on screen, diff summary before/after. `8.4-…`; one CMaNGOS box (VAN 1-item mail proven); TW by rows |
| **8.7** | `Applier.running` guard; update checks; three CMaNGOS manifest sets | yulon-ubuntu: a SQL manifest refused while up with the step named; applied stopped; conf value proven arrived from the boot log. `8.7-…`; one CMaNGOS box |
| **8.9** | purge | yulon-ubuntu **from a throwaway install**, never the 7.2 one: install → purge keep → reinstall same path → characters present; install → purge → project census empty. `8.9-…`; one CMaNGOS box |
| **8.6** | mod-ale pin; rebuild wiring; the bridge; My Party | yulon-ubuntu after the owner's rebuild: §4.7 "proven for real". `8.6-wotlk-ubuntu-<date>/` |
| **8.8** | Steam | a Steam Deck or a Linux box with Steam installed — none this side; the owner's |

8.1 → 8.2 → {8.3, 8.5} → 8.4 → 8.7 → 8.9 → 8.6 → 8.8. 8.6 waits on spike S1–S3 and the rebuild;
8.8 waits on §12 question 2.

---

## 6. Proposed `8.x` checklist lines

- [ ] 8.1 Command channel — SOAP on loopback enabled in the installed conf (`SOAP.Enabled`, `SOAP.IP = 0.0.0.0` behind the host's `127.0.0.1` publish), `AiPlayerbot.CommandServerPort = 0`, an app-owned GM-3 account `YULON_<install_id>` written through `accounts.create_account` and verified by a real `server info` round-trip before its credential is saved (0600 under `config_dir()`), a per-install single-flight command queue with visible depth, a closed verb table per tree, three-outcome results. Families: WotLK, TBC, Vanilla over SOAP; Tortoise attach + reads. Platforms: Linux first, then native Windows. **DoD (live state):** the Server tab shows "Command channel: verified <timestamp>"; `account_access` holds `(<id>, 3, -1)` (or `account.gmlevel = 3`); inside the world container `ss -ltn` lists 7878 and not 8888 nor 3443; a deliberately wrong credential file shows "refused — Repair" and Repair returns to verified. **Gate:** yulon-ubuntu (WotLK), yulon-fedora (TBC, Vanilla), m910q (Tortoise); `pyplan/gates/8.1-<family>-<box>-<date>/` holding the before/after conf lines, port listings, `account`/`account_access` rows and the `server info` text; visible effect: the tab's verified line and the worldserver log's SOAP bind line for this run.
- [ ] 8.2 Live dashboard and pre-stop log snapshot — players (human by NOT-bot), bots (both marker arms), uptime, `Update time diff` summary, `RestartCount` crash-loop line; `docker.snapshot_logs()` called from `Controller.stop()`/`remove()` before `compose stop`. Families: all four (Tortoise reads only). **DoD:** with bots logged in the tab's numbers equal `SELECT COUNT(*)` run by hand in the same minute; after `docker kill` of the world the tab shows "restarted 1 time" within two polls and disables commands; after Stop a file under `config_dir()/logs/` ends with the worldserver's halt line and is ≤ 2 MiB. **Gate:** yulon-ubuntu, one CMaNGOS box, m910q; `pyplan/gates/8.2-…/`; visible effect: the dashboard panel and the snapshot file.
- [ ] 8.3 Accounts: list, set password, GM level — list by MySQL read excluding bot accounts and marking the app account; password and level through the server's own commands (SOAP; Tortoise level through attach, password blocked on S4). **DoD:** a human account's password set from the tab logs the client in; its level set to 1 reads 1 in the row; the app account's row cannot be edited from the tab. **Gate:** yulon-ubuntu with the vmhost client, one CMaNGOS box, m910q; `pyplan/gates/8.3-…/` with the rows before/after; visible effect: the client login.
- [ ] 8.4 Named teleport, item search, item mail, revive, set level, rename, mailed money, gear sets — every write performed by the server; per-tree caps (12/12/1/1 items per mail; no set-level on Tortoise); online/offline shown before firing; mail non-idempotence bounded by the queue. **DoD:** each verb once offline and once online changes the named row (`position_x/map`, `level`, `at_login`, `mail` count) and the in-game effect is on screen; `Update time diff … max` before/after each verb is in the capture. **Gate:** yulon-ubuntu, one CMaNGOS box (Vanilla for the 1-item mail), m910q by rows; `pyplan/gates/8.4-…/`.
- [ ] 8.5 Browse Bots — read-only list with count and page, both marker arms on WotLK, prefix on the CMaNGOS family; refuses to answer on an unreadable or empty prefix. **DoD:** the count on the tab equals the hand query on all four boxes; on WotLK the split random/addclass/citizen is shown; with the prefix line blanked in a copy of the conf the tab shows the refusal sentence, not 0. **Gate:** all four boxes; `pyplan/gates/8.5-…/`.
- [ ] 8.6 My Party (WotLK only) — mod-ale pinned in the manifest, rebuild wired to the explicit `-f` set with the world stopped and snapshotted, the bridge scripts plus `dml_ping`, the seven live preconditions each with its own sentence, add/spec/gear/dismiss-all with both marker arms. **DoD:** with the client in-game, "Add warrior" puts a bot in the party frame and one row in `group_member` on a type-2 account; "Dismiss all" removes it; the diff summary before/after one and four adds is in the capture; every precondition failure draws its sentence instead of the button. **Gate:** yulon-ubuntu after the owner's rebuild; `pyplan/gates/8.6-wotlk-ubuntu-<date>/`; visible effect: the party frame via vmshot.
- [ ] 8.7 Module update checks and manifests for TBC, Vanilla, Tortoise — `Applier.running` refuses characters/world/playerbots SQL while the world is up and names the step; conf writes report "restart required" and the value's arrival is proven by the server's boot log. **DoD:** a SQL manifest pressed on a running server shows the refusal with the step named and writes nothing (row count unchanged); the same manifest applied stopped writes its rows; a conf key's new value appears in the worldserver's own boot output. **Gate:** yulon-ubuntu, one CMaNGOS box; `pyplan/gates/8.7-…/`.
- [ ] 8.8 Steam integration (Linux / Steam Deck only) — two shortcuts and artwork written with Steam closed, temp+rename, `.yulon-bak`, keyed by app id. **DoD:** the two entries appear in the Steam library after a Steam restart and the client entry carries the compat tool; a second press changes nothing; with Steam running the button shows the refusal. **Gate:** a Steam Deck or Linux box with Steam — owner's; `pyplan/gates/8.8-…/`.
- [ ] 8.9 Uninstall / purge — as `phase8-decisions.md`; refused while running; order containers → volumes → images → folder → record. **DoD:** install → purge with Keep → reinstall to the same path → the characters are there; install → purge → `docker ps -a`/`volume ls`/`image ls` filtered by the project are empty and the folder is gone; a purge of a running server shows "Stop first". **Gate:** yulon-ubuntu on a throwaway install, one CMaNGOS box; `pyplan/gates/8.9-…/`.
- [ ] Phase 8 exit criteria met — every box above ticked with its gate directory present; no `8.x` DoD satisfied by a skip, an absent capture or an exit code; `test_write_ledger.py` green with every write in §3 enumerated; 7.9's controller-surface gate and 7.10's regression re-run green on the merged tip.

---

## 7. Blast radius on the proven install and controller paths

| Change | What it touches | Gates invalidated, to re-run |
|---|---|---|
| A1/A2 conf patches on a live install | `env/dist/etc/worldserver.conf`, `modules/playerbots.conf` (host-bound) | none of 7.x (the engine does not write them); 8.1's own gate proves the value arrives |
| A6: SOAP port line in `shared/cmangos/base.yml.tmpl` | the rendered TBC/Vanilla/Tortoise compose files; `test_catalog_invariants.py` "ports in exactly one file" still holds | **7.4c, 7.5, 7.6** compose-config captures are stale for the `ports` block; 7.9's controller gate on each CMaNGOS box; **7.10** regression |
| `Controller.stop()`/`remove()` pre-stop hook | `controller.py:272-306`; every game's stop path | **7.9** (`gate-79-controller-surface.py`) on all four; **7.10** |
| `Applier.running` seam | `apply.py` `Applier` constructor and `_sql()`; `ui/controller_view.py:341-346` passes `controller.status` | `test_apply.py`; 7.10's Modules-tab checks |
| `ControllerServices` gains `commands`, `dashboard`, `bots` | `ui/controller_view.py:300-400` and the three CMaNGOS `_for_*` builders | **7.9** controller surface on all four; `test_controller_packages_agree.py` |
| WotLK templates | **unchanged** — the SOAP publish already exists (`base.yml.tmpl:264`); `tests/data/wotlk-rendered/` byte-identical | none |
| `DEFAULT_WORLD_ENV` | **unchanged** — 8888 is closed by the conf patch, not by env, so the rendered override fixture stays | none |
| Not touched | `console.py` (all four), `accounts.py` (create path), `networking.py`, `maintenance.py`, `repair.py`, `native.py`, `families/*`, `start_staged`/`stop_staged`/`remove_staged` bodies | — |

---

## 8. Tests

Unit, no daemon, in the shapes `test_native.py`/`test_families_cmangos.py` use. **New** files
are marked; nothing here names a test that does not exist at 7bc5ebd3 except those.

- **`tests/test_command_queue.py` (new)** — one in flight per install; a second submission queues and `depth` reads 1; a timeout returns `could_not_ask`, never `refused`; the refusal table by server state (each of §4.1's rows as a fixture violating exactly one rule); a poll refused while a poll is in flight; the console-pending interlock in both directions.
- **`tests/test_soap_client.py` (new)** — the envelope by field (namespace from tree data, command text verbatim, Basic header present, password absent from `repr`, `str`, log records and every exception message); a stub HTTP server that answers each of: OK result, sender fault, receiver fault "Server is shutting down", 401, 403, connection refused, hang → the six typed outcomes. Gate the tool, not the payload: no worldserver.
- **`tests/test_verbs.py` (new)** — every verb's text by field per tree; the sanitiser strips `"` and replaces CR/LF with one space each (AC #2695); per-tree item caps (12/12/1/1) split a 19-item set into 2/2/19/19 commands; Tortoise has no `set level` verb and `rename` is top-level; the name canonicaliser; a verb whose tree says `Console::No` cannot be constructed.
- **`tests/test_credentials.py` (new)** — the file is created with mode 0600 via `os.open` (asserted on the fd flags, not on a later `chmod`); the state machine: row-written-then-verify-fails leaves state `pending` and a second tick creates **no** second account (a `Recorder` seam that answers "exists" the second time — the fixture that answers differently on the second press); the rotation statement's `WHERE` names `YULON_%` by field and refuses any other name.
- **`tests/test_bot_markers.py` (new)** — both arms on AC, prefix-only on the three others; an empty prefix raises before SQL; `%`/`_` escaped with `ESCAPE '!'`; the relationship assertion (rows on matched accounts ⇒ count ≥ 1) with a fixture whose registry is empty and prefix populated (the 2026-08-01 shape) and one with 400 type-3 rows and no prefix (2026-08-10).
- **`tests/test_dashboard.py` (new)** — three-valued fields; `RestartCount` growing between two polls flips the crash-loop line; a stale poll is labelled stale, not current.
- **`tests/test_logsnap.py` (new)** — argv by field (`compose ps -a -q <service>` in `server_dir`, then `logs --tail 2000 <id>`); `.partial` rename; the 2 MiB cap after the line cap; prune excludes the file just written under a backwards clock (the `RUST logsnap.rs:183-190` trap); a failure returns a message and never raises into `stop()`.
- **`tests/test_apply_guard.py` (new)** — a `sql` step to `characters`/`world`/`playerbots` is skipped with the step named while `spec.world` is up; `auth` steps and conf/deploy still run; with the seam absent the behaviour is byte-for-byte today's (no regression for callers that pass nothing).
- **`tests/test_write_ledger.py` (new)** — walks `yulon/` with `ast`, enumerates every call to `run_statement`, `run_file`, `query`-less write seams, `os.replace`, `Path.write_text`/`write_bytes` and `open(..., "w")`, and fails on any site not in a committed allow-list keyed to §3's row ids. A relationship test: every allow-list row must resolve to a site, so a deleted write cannot leave a stale ledger line.
- **`tests/test_steam_shortcuts.py` (new)**, **`tests/test_purge.py` (new)**, **`tests/test_party.py` (new)** — the refusals (Steam running / server running / ownership unproved) each from a fixture violating one rule; app-id keyed replace; the purge order by recorded argv.
- **Existing, extended**: `test_controller.py` (stop calls the snapshot hook once, before `stop_staged`, and still stops when it fails); `test_controller_packages_agree.py` (the three CMaNGOS packages expose the same new services); `test_catalog_invariants.py` (the SOAP port line renders once per CMaNGOS game); `test_docs_pins.py` (widens onto `phase8-plans/` when written).
- **Integration (daemon, throwaway containers, never a real server)**: `exec_stdin` into a throwaway mariadb:11 with the `BotClause` SQL against seeded rows (the four marker shapes); the snapshot against a busybox container that prints 3000 lines and exits; the stub SOAP server in a container to prove the client reaches `127.0.0.1:<port>` through a docker publish (S1's mechanism, without a worldserver).
- **Mutation discipline**: purge `__pycache__` on both sides of every mutation; N/N is a claim, not a result.

---

## 9. Risks worth re-reading

| Risk | Measured price or enumerated routes | Spike needed? |
|---|---|---|
| **`SOAP.IP` inside the container.** With the dist default `127.0.0.1` the docker publish reaches nothing; with `0.0.0.0` the listener is on the compose network | routes: (a) `0.0.0.0` + host `127.0.0.1` publish — reachable by four containers, none user-facing; (b) the container's own IP — brittle; (c) leave `127.0.0.1` and run the client inside the container via `docker exec` — costs a CLI spawn per command and puts the password in the exec's env | **S1**: on yulon-ubuntu's 7.2 install, set the two keys, restart the world, `curl` `server info` from the host; a conf edit + restart, so the owner's yes for that spike (§12 q4) |
| **A command fired during map load blocks the SOAP thread for the whole load** | AC: the request queues and waits (`ACSoap.cpp:125`); ready budget 480 s (`docker.py:39`) | no — the starting-state refusal is the defence; the gate fires one command 5 s after Start and captures the refusal |
| **`saveall`/shutdown vs a queued command** | AC returns receiver fault within 1 s of `IsStopped()`; a command queued *before* SIGTERM may run during the drain and hold the world thread while 7400 saves queue | no — the queue drains to empty before `Controller.stop()` is allowed to begin (the hook waits up to one command deadline, then stops anyway and says so) |
| **CMaNGOS SOAP serves one request at a time** | a hung command blocks the next in the kernel backlog until the client timeout | no — single-flight queue; the 30 s deadline is the price |
| **The world-thread cost of `dml_addclass` + `autogear`** | unmeasured | **S2**: measured by the 8.6 gate itself via `server info` before/after — not a separate spike |
| **Addclass bots after master logout** | could-not-ask (`PB PlayerbotMgr.cpp` logout path unread) | **S3**: a source read of `PlayerbotMgr::LogoutAllBots`/`OnPlayerLogout` at b949b50b; no box |
| **Tortoise password cache** | `TW AccountMgr.cpp:250-256` caches rank; whether `CheckPassword` reads the DB is unread | **S4**: a read of `TW AccountMgr::CheckPassword`; if cached, A8t is refused and Tortoise has no set-password |
| **The MaNGOS SOAP namespace and fault wire shape** | `soapC.cpp` is generated and unread (`cmangos.md` could-not-establish 2) | measured on 8.1's TBC gate, from the first reply, and recorded as tree data |
| **mod-ale unpinned** | a rebuild takes today's HEAD; E in `azerothcore.md` is "as of 2026-09-06" | no — pin `c3de7942` (§12 q1) |
| **Existing installs need a container recreate for A6** | a running CMaNGOS container keeps its old ports; `compose up -d` recreates on config change, which is a stop (300 s grace) | no — 8.1 says so on the tab and runs it through the existing Stop/Start |
| **`db-import` module SQL is never applied on an installed server** | `apply.py:1365-1367` defers to a one-shot `start_staged` never runs | §12 q3 |
| **Two installs, one 7878** | README §12; the wrong server answers 401 or a different `server info` head | no — asserted per session |
| **Mail is not idempotent and the app can forget a press across a restart** | one extra mail per forgotten press; deletable in-game | no — named, accepted |
| **`docker ps` can hang for minutes** | 8+ min measured; 30 s timeout (`docker.py:925-935`) | no — the dashboard job skips, labels stale |
| **The GM-3 account is a remote shell in front of the server** | equal to what `.env`'s DB root already grants a local reader; new exposure is app misuse, bounded by the closed verb table and the four-container network | no |
| **A conf value that never arrives** (env-vs-conf duplication, `bot-population-is-500`) | Yu'lon sets no `AC_SOAP_*`/`AC_AI_PLAYERBOT_COMMAND_SERVER_PORT` env, so the conf is the only source; asserted by the boot log and `ss` | no |

---

## 10. What the implementer should NOT build yet

- **No SOAP Console tab.** The Console stays on `docker attach`; SOAP carries the closed verb table only. Moving free text onto a channel with no level check is a surface decision, not this design's.
- **No `pending_commands` writer for Tortoise**, ever in this phase.
- **No YAML writer for `docker-compose.override.yml`.** 8888 and SOAP are conf keys; the settings surface that rewrites the override is Phase 9.
- **No credential in `state.json`.** `KnownInstall` (`state.py:24-40`) stays as it is; the credential file is its own, 0600.
- **No coordinate teleport** (refused by Q7), **no `.additem`** (online-only), **no heal/summon** unless the 8.6 bridge is proven and the owner extends Q2a.
- **No automatic pre-stop backup.** Q8iv put automatic dumps later; the snapshot is a log, not a database.
- **No My Party on the CMaNGOS family** (Q4), and no `.rndbot spoof` experiments outside a named spike with the owner's yes.
- **No rebuild wiring before 8.6**, and no rebuild started by a gate script.
- **No second bot-identity implementation** anywhere — one `BotClause`, imported by 8.2, 8.3, 8.5, 8.6 (`RUST stats.rs:65-67` is the lesson).

---

## 11. Doc changes this phase makes

- `pyplan/style-guide.md` §3 — rows for `commands/queue.py`, `commands/soap.py`, `commands/attach.py`, `commands/verbs.py`, `commands/credentials.py`, `bots/markers.py`, `dashboard.py`, applied when each module exists; the `controller.py` row gains "takes the pre-stop snapshot once"; the `apply.py` row gains "refuses characters/world/playerbots SQL while the world is up through the `running` seam".
- `pyplan/README.md` §9 — the three struck lines (per the decisions page); §11 — the credential file named under local data; §12 — the sentence that two servers cannot share 7878 either.
- `pyplan/checklist.md` — the `8.x` lines of §6; the write ledger (§3) linked from 8.1's line as the table `test_write_ledger.py` enforces.
- `pyplan/bug-checklist.md` — line 239 (accounts create-only) closes with 8.3; line 499 (crash loop reads as up) closes with 8.2; line 795 is about the deleted scripts and is re-read against the native engine's `127.0.0.1` pins, then closed or re-worded; a new box for the `db-import` finding in §4.8 if §12 q3 says "later".
- `pylauncher/README.md` — the capability table per step as each gate records what ran live.
- `pyplan/phase8-parity-decisions.md` — this page's §3 ledger and §4.1 matrix, if chosen, under "Per-feature detail"; `roadmap.md` is not edited (Appendix A carries §8).

---

## 12. Open questions for the owner

1. **Pin mod-ale.** The manifest names no rev (`mod-ale.json:9-11`) and a rebuild is 2–4 h of yours. Pin `c3de7942` (read 2026-09-06) before the first 8.6 rebuild, or take HEAD that day? (Recommend: pin.)
2. **Steam's userdata tree.** 8.8 writes `shortcuts.vdf`, grid artwork and the compat mapping under `~/.steam/…/userdata/<id>/` — not the WoW client folder. Is that inside Q5's prohibition? (Recommend: allowed; it is Steam's file, written with Steam closed, backed up first.)
3. **`db-import` module SQL on an installed server.** Today it is deferred to a one-shot that a staged start never runs, so it is never applied. May 8.7 run `docker.run_one_shot` with the world stopped for those steps, or does the tab say "not applied on an installed server" and the box goes to later? (Recommend: run the one-shot, stopped, with the snapshot first.)
4. **Spike S1 on yulon-ubuntu.** It needs two conf keys set and one worldserver restart on the 7.2 install — a config edit and a restart, which the spike rules forbid without your yes. Yes for that one spike, from the checkpoint, announced?
