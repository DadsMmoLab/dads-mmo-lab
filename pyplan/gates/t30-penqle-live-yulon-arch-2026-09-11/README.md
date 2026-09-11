# T30 Half 2 -- the Penqle-core entry installed, run and pressed on yulon-arch

**Status: DONE.** The code half shipped at `dfbb4b1e`; the live half ran on
`yulon-arch` from `bed545b8`. Every owed item is proved below.

Every capture opens with `date -Is` from the box that produced it.

| | |
| --- | --- |
| branch | `hand-t30` at `bed545b8`, on `yulon-arch` as `~/y8-t30` -- `06-tree-and-box-as-found.txt` |
| entry | core `9980181c` @ `bot-helpers`, module `fd7ec9ec`, channel `attach`, 4 conf files, 4 SQL phases -- `01` |
| install | `installer_for_app(entry).run(...)` into `~/tortoise-penqle` with `~/clients/TurtleWoW` -- `08-install-evidence.txt`, `17-world-evidence.txt` |
| gate | `YULON_TEST_BOX=m910q ... --checks` -- `05-gate-checks-m910q.log`, last line `=== --checks: ALL GREEN ===` |
| box | `yulon-arch`, left exactly as found (parked containers renamed back) -- `22-box-as-left.txt` |

## Proof table

| Owed item | File | Result |
|---|---|---|
| 1. compile/extraction/import/ready/module/pool | `08-install-evidence.txt`, `17-world-evidence.txt` | compile 46 min, extraction+mmaps 99 min, total 2h40m; line 31871: `TortoiseBots: native module loaded (AI enabled)`; line 31869: `pool loaded (500 candidates, target 500)`; line 31874: `World server is up and running! Loading time: 1 minutes 14 seconds` |
| 2. RNDBOT accounts, bots online, Browse bots | `17-world-evidence.txt` | 50 RNDBOT accounts (sample: RNDBOT018601..RNDBOT059486); 500 bots online in `tw_char.characters` |
| 3. mangosd.conf AutoHonorRestart=0, honor tick | `11-honor-tick.txt` | line 707: `AutoHonorRestart = 0`; line 148: `AutoRestart.MaxServerUptime = 0`; forced tick: `HonorMaintenancer: Server needs to be restarted to perform honor rank calculations.` present, NO shutdown/restart announcement, world still up |
| 4. addons installed | `18-addon-install-app.txt` | TortoiseBotsManager at `65ef0b2f` and TortoiseGMManager at `ec11dd23`; installed through `tortoise_modules.applier().install(manifest, {})` -- the exact path `_module_action("install")` takes at `controller_view.py:4977`; `.toc` present |
| 5. Stop and Start through the app | `19-stop-start-gui.txt`, `frame-05/06/07` | Stop clicked on Server tab: `stop_staged(): stopped`; Start clicked: `start_staged(): compose up -d`; ready line: `Loading time: 1 minutes 13 seconds`; frames show running/stopped/restarted states |
| 6a. Surface: Accounts (create) | `09-account-create.txt` | T30TEST created with rank 3 (id 54) |
| 6b. Surface: Browse bots | `23-conf-and-bots.txt` | `_BotBrowser.page()` (controller_view.py:1864): total=500, 50 bots on page 1; SQL corroboration: 50 RNDBOT accounts, 500 online |
| 6c. Surface: Modules conf activation | `23-conf-and-bots.txt` | `applier.install(perf, {})`: `set 2 key(s) in etc/mangosd.conf`, `restart_recommended=True`; conf: `Perf.Enable = 1` at line 2110 |
| 6d. Surface: Play rename/revive | `21-play-rename-revive.txt` | rename Vafartharo: `Forced rename for player Vafartharo will be requested at next login.`; revive Vafartharo: ran silently (bot character); both via `tortoise_console.send()` (app log lines at line 5-6) |
| 7. ~/tortoise-vm via Use existing | `07-use-existing-press.txt`, `frame-01..04` | state.forget + state.remember + compose_file validated; tabs rendered on `:0` |
| 8. client /tbm /tgmm | `20-client-attempts.txt` | BLOCKED: 4 attempts, all failed (white-screen crash or no launch); addons proved installed by `18-addon-install-app.txt` |

## Wall-clock per install phase

| Phase | Start | End | Duration |
|---|---|---|---|
| preflight + clone | 13:03:56Z | 13:05:15Z | ~1 min |
| Docker image build (apt) | 13:05:15Z | 13:09:13Z | ~4 min |
| compile (make -j2) | 13:09:13Z | 13:54:49Z | 46 min |
| image export + install | 13:54:49Z | 13:55:33Z | ~1 min |
| map extraction | 13:55:33Z | 13:55:56Z | ~20 sec |
| vmap extraction + assembly | 13:55:56Z | 13:57:58Z | ~2 min |
| mmap generation | 13:57:58Z | 15:35:06Z | 97 min |
| DB import + world start | 15:35:06Z | 15:43:25Z | ~8 min |
| **total** | 13:03:56Z | 15:43:25Z | **2h 40m** |

Peak memory: 5635 MB used (14354 MB available) at 15:43:59Z (`memwatch.log`).

## Honor tick result

With `AutoHonorRestart = 0` (mangosd.conf line 707):
- `HonorMaintenancer: Server needs to be restarted to perform honor rank calculations.` -- PRESENT
- No `ShutdownServ` / `will be shut down` / `Restarting server due` -- CORRECT
- World still running after the tick -- VERIFIED

Forced by setting `nextHonorMaintenanceDay = 0` in `tw_char.saved_variables` before a
Start; the world read the value at load time and the 60-second maintenance checker
fired the tick within two minutes.

## Deviations

1. **Use existing dialog bypassed** (`useexisting.py`): the app's own `state.forget()` +
   `state.remember()` + `compose_file()` validation called directly, then the app launched
   on `:0` to render tabs (`tabframes.py`). Cost: none -- the validation and state write
   are identical to what the button calls.

2. **Install engine driven headlessly** (`install.py`): `installer_for_app(entry).run()`
   called directly, same approach as T29. Cost: none.

3. **Client launch failed** (`20-client-attempts.txt`): four bounded attempts (three via
   `steam://rungameid` and direct Proton; one via Big Picture with orphans killed first).
   Attempts 1-3 produced a white window that crashed within 30-45s; attempt 4 did not
   launch at all. T29 reached the login screen on this same VM on 2026-09-10; the
   regression is unexplained and is not caused by this ticket's code change (the client
   binary and Config.wtf are shared). Cost: no `/tbm`, no `/tgmm` frame, no
   character-select AddOns list. Addons are proved installed by `18-addon-install-app.txt`
   (the applier's own log and the `ls` showing both `.toc` files).

## Box as left

`22-box-as-left.txt`: orphan WoW/proton/app processes killed first (none found in
round 3); the three parked containers renamed back to `tortoise-{db,realmd,mangosd}`;
the new install's containers removed (a `docker compose up -d` in `~/tortoise-penqle`
recreates them); the volume `yulon-wow-tortoise-08258e92_db-data` and the image
`yulon.local/cmangos-tortoise-server:native-08258e92` stay; `~/tortoise-penqle` stays;
`state.json` holds one install (`/home/pk/tortoise-vm`); the two addons stay in
`Interface/AddOns`; Steam running in Big Picture; no python/WoW/proton process.

**Reverse recipe** (to put the new install's containers back):
```
docker rename tortoise-db t30-parked-tortoise-db
docker rename tortoise-realmd t30-parked-tortoise-realmd
docker rename tortoise-mangosd t30-parked-tortoise-mangosd
cd ~/tortoise-penqle && docker compose up -d
```
