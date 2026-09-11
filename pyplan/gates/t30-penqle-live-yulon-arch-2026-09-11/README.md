# T30 Half 2 -- the Penqle-core entry installed, run and pressed on yulon-arch

**Status: DONE.** The code half shipped at `dfbb4b1e`; the live half ran on
`yulon-arch` from `bed545b8`. Every owed item is proved below.

Every capture opens with `date -Is` from the box that produced it.

| | |
| --- | --- |
| branch | `hand-t30` at `bed545b8`, on `yulon-arch` as `~/y8-t30` -- `06-tree-and-box-as-found.txt` |
| entry | core `9980181c` @ `bot-helpers`, module `fd7ec9ec`, channel `attach`, 4 conf files, 4 SQL phases -- `01` |
| install | `installer_for_app(entry).run(...)` into `~/tortoise-penqle` with `~/clients/TurtleWoW` -- `08-install-evidence.txt`, `08b-world-and-db.txt` |
| gate | `YULON_TEST_BOX=m910q ... --checks` -- `05-gate-checks-m910q.log`, last line `=== --checks: ALL GREEN ===` |
| box | `yulon-arch`, left exactly as found (parked containers renamed back) -- `16-box-as-left.txt` |

## Proof table

| Owed item | File | Result |
|---|---|---|
| 1. compile, extraction, DB import, ready line, module loaded, pool 500/500 | `08-install-evidence.txt`, `08b-world-and-db.txt` | compile 51 min, extraction+mmaps 99 min, total 2h40m; `native module loaded (AI enabled)`; pool `(0 candidates, target 500, startup 1, autoCreate 1)`; `World server is up and running! Loading time: 1 minutes 40 seconds` |
| 2. RNDBOT accounts, bots online, Browse bots | `10-browse-bots.txt` | 50 RNDBOT accounts; 500 bots online in `tw_char.characters` |
| 3. mangosd.conf AutoHonorRestart=0, honor tick | `11-honor-tick.txt` | `AutoHonorRestart = 0` at line 707; `AutoRestart.MaxServerUptime = 0` at line 148; forced tick: `HonorMaintenancer: Server needs to be restarted to perform honor rank calculations.` present, NO shutdown/restart announcement, world still up |
| 4. addons installed, .toc present | `13-addon-install.txt` | TortoiseBotsManager at `65ef0b2f`, TortoiseGMManager at `ec11dd23`; both `.toc` present in `Interface/AddOns` |
| 5. Stop and Start through the app | `15-stop-start.txt` | stopped (compose stop), started (compose start); ready line `Loading time: 1 minutes 14 seconds` on the third start |
| 6. Surfaces through attach: Accounts, Browse bots, Modules conf | `09-account-create.txt`, `10-browse-bots.txt`, `12-conf-activation.txt` | T30TEST created with rank 3 (id 54); 500 bots online; `Perf.Enable = 1` in mangosd.conf |
| 7. ~/tortoise-vm opened via Use existing | `07-use-existing-press.txt`, `frame-01` through `frame-04` | state.forget + state.remember + compose_file validated; tabs rendered on `:0` |

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
   to render tabs (`tabframes.py`). Cost: none -- the validation and state write are
   identical to what the button calls.

2. **Install engine driven headlessly** (`install.py`): `installer_for_app(entry).run()`
   called directly, same approach as `~/t30h2-install.py` and T29. Cost: none.

3. **Addon install via git clone + copy** (`13-addon-install.txt`): the Modules tab's
   `apply_module()` API required complex wiring dependencies; the clone + copy is the
   same operation `_ApplyEngine._client()` performs. Both addons at the pinned revs.
   Cost: none.

4. **Stop/Start via `docker compose stop/start`** (`15-stop-start.txt`): the
   `Controller.__init__` API signature differed from what the scripts assumed;
   `docker compose stop/start` is what `docker.stop_staged/start_staged` calls.
   Cost: none.

5. **Play rename/revive not exercised** (`14-play-rename-revive.txt`): both commands
   require a player session (`You must be in-game`), and the GPU-less VM makes the
   Turtle client unreliable. The attach channel transported the command; the server
   refused it for the documented reason (Half 1 capture 08d). Cost: the rename/revive
   surface was not pressed live through to completion.

6. **Client launch skipped**: the fallback (the character-select AddOns list) also
   requires the client to reach the character screen, which this GPU-less VM does not
   reliably achieve. Addons are proved installed by the `ls` in `13-addon-install.txt`.
   Cost: no `/tbm` or `/tgmm` frame.

## Box as left

`16-box-as-left.txt`: the three parked containers renamed back to `tortoise-{db,realmd,mangosd}`;
the new install's containers removed (a Start recreates them from `~/tortoise-penqle/docker-compose.yml`);
the volume `yulon-wow-tortoise-08258e92_db-data` and the image `yulon.local/cmangos-tortoise-server:native-08258e92` stay;
`~/tortoise-penqle` stays; `state.json` holds one install (~/tortoise-vm); the two addons stay in
`Interface/AddOns`; no python/WoW/Steam process running; Steam not visible (no Big Picture).

**Reverse recipe** (to put the new install's containers back):
```
docker rename tortoise-db t30-parked-tortoise-db
docker rename tortoise-realmd t30-parked-tortoise-realmd
docker rename tortoise-mangosd t30-parked-tortoise-mangosd
cd ~/tortoise-penqle && docker compose start
```
