# 7.7 — WoW WotLK on native Windows, `yulon-win11-gate`, 2026-09-05 — the transcript the box lacked

The install that had been running on this box since 2026-08-31 (`C:\gate\wotlk-server`) was
made after a failed attempt and nothing captured it (`7.7-win11-gate/20-install.log` ends with
the engine never answering). This folder is a fresh install from an empty folder, driven by
the branch tip of the day (`a0cc9dc0`, shipped to the box as `C:\gate\wotlk77-src\`), with the
whole transcript kept. Copied off the box 2026-09-05 at 13:59 CEST while the three containers
were up.

## What happened, in the box's own stamps (box shows PST; CEST = +9 h)

| stamp (box-local)  | event                                                                     | source |
|--------------------|---------------------------------------------------------------------------|--------|
| 01:46:55 → 01:47:08 | first start, **refused by preflight**: Docker's disk 22 GB free, 40 needed | `wotlk77-wrapper.log`, `wotlk77.log` (first run, overwritten — the refusal text is quoted below) |
| 01:56:11           | the install that ran (`==== install start … source=a0cc9dc0`)             | `wotlk77-wrapper.log` |
| 01:56:36 → 02:22:12 | `clone-core` + `clone-modules` (2.1 GB / 9,099 files at the 24-minute mark) | `wotlk77.log` |
| 02:23:44 → 04:02:31 | `build`, 1834 steps at 5 jobs (`ac-client-data-init` `StartedAt` 11:02:31Z marks the end) | `wotlk77.log`, `live-recheck.txt` |
| 04:02 → 04:09:59   | `client-data` (1140 MB download, resumed), `start-db`                     | `live-recheck.txt` (`ac-db-import` `StartedAt` 11:09:59Z) |
| 04:09:59 → 04:20:17 | `import` — `ac-db-import` **Exited (0)**                                  | `live-recheck.txt`, `db-import-tail.txt` |
| 04:20:35 (11:20:35Z) | `ac-worldserver` and `ac-authserver` started                             | `live-recheck.txt` |
| 11:30:24Z          | `WORLD: World Initialized In 4 Minutes 35 Seconds` then `AzerothCore rev. 413bea61a85e+ … ready...` — the banner the catalog's `ready.world` regex (`ready...`) waits for | `docker logs -t ac-worldserver`, quoted in this README |
| 04:30:26           | `install of wow-wotlk finished`, **errorlevel 0**                         | `wotlk77.log`, `wotlk77.exitcode` |
| 11:40:46Z          | `Random Bots Stats: 500 online`                                           | `docker logs -t ac-worldserver` |

Wall: 01:56:11 → 04:30:26 = **2 h 34 min 15 s** from an empty folder, exit 0. Ready stage
04:20:17 → 04:30:26 = 609 s.

## What the box said afterwards (`wotlk-final/live-recheck.txt`, 04:59 box-local)

* `ac-worldserver` Up, `0.0.0.0:8085` and `127.0.0.1:7878`; `ac-authserver` Up, `0.0.0.0:3724`;
  `ac-database` Up (healthy), `127.0.0.1:3306`; `RestartCount=0` on all three.
* `ac-db-import` Exited (0), `ac-client-data-init` Exited (0) — the 6.3 `ac-db-import` blocker,
  cleared on native Windows with its transcript this time.
* Schemas: `acore_auth` **22**, `acore_characters` **111**, `acore_playerbots` **30**,
  `acore_world` **315** — identical to the Ubuntu, Fedora, macOS and the 08-31 Windows records.
* Realm row: `AzerothCore 172.30.52.119 / 172.30.52.119 : 8085` (the VM's own address, set by the
  realm step; the authserver logged `Added realm "AzerothCore" at 127.0.0.1:8085` at 11:20:36Z,
  before that step ran, and re-reads the row on its own schedule).

## Two things a reader should know

* The first start was refused, correctly, by preflight: `free space on Docker's disk: 22 GB free,
  and the install needs 40 GB`. "Docker's disk" is the host drive holding
  `docker_data.vhdx`, which carried 20.6 GB of stale build cache from the three CMaNGOS builds.
  `docker builder prune -af` + `docker image prune -af`, then `wsl --shutdown` and
  `diskpart … compact vdisk` took the VHDX from 32.6 to 12.0 GB and `C:` from 22 to 42.8 GB
  free; the second start passed with `[warn] free space on Docker's disk: 43 GB free; 60 GB is
  the comfortable figure`. The five stopped `ac-*` containers of the 08-31 install were removed
  first (their volumes kept), because compose pins those names globally.
* `ac-worldserver` logged `Config::LoadFile: Failed open file
  '/azerothcore/env/dist/etc/modules/playerbots.conf'` at start and then took every playerbot
  setting from `AC_PLAYERBOTS_*` environment variables — 500 bots were online 20 minutes later.
  Recorded, not judged here: the same shape should be checked against the Linux transcripts
  before anyone calls it a Windows difference.

## Files

* `wotlk77.log` — the installer's transcript (6,647 lines, UTF-8, CRLF; the `build` stage's
  1834 `#25` lines are the bulk). `wotlk77.exitcode` — `0`. `wotlk77-wrapper.log` — both
  start/end pairs.
* `wotlk-final/live-recheck.txt` — `docker ps`, `docker inspect` (`StartedAt`, `RestartCount`,
  `ExitCode`) for all five containers, the first eight matching worldserver lines from
  `docker logs -t`, the schema counts and the realm row. Captured 04:59 box-local.
* `wotlk-final/worldserver-tail.txt` (last 200 lines), `authserver-tail.txt` (60),
  `db-import-tail.txt` (40) — container logs at capture time, ANSI escapes kept. The
  `ready...` banner had scrolled out of the worldserver tail under bot traffic; it is quoted
  above from a separate `docker logs -t | Select-String` run at 14:02 CEST.
