# 7.7 — WoW TBC on native Windows, the second press that turned exit 1 into exit 0 — 2026-09-05

The 09-04 TBC install on `yulon-win11-gate` (`7.7-win11-tbc/`) completed every stage, and the
engine reported it failed: the world server's first `Avg Diff:` line came 46.0 min after the
container started, against a fixed 1800 s budget. This run is a **second Install press on that
same finished folder** (`C:\gate\tbc-server`, client `C:\gate\client\WoW-Client-2.4.3`) under
the engine merged that afternoon — source `745307ad`, which carries `lane/readybudget`'s quiet
budget — shipped to the box as `C:\gate\tbc77b-src\` and driven by `C:\gate\run-tbc77b.cmd`
as task `dml-tbc77b`. Copied off the box 2026-09-05 at 15:08 CEST with the three containers up.

## What happened, in the box's own stamps (box shows PST; CEST = +9 h)

| stamp (box-local) | event | source |
|---|---|---|
| 05:04:10 → 05:04:53 | first start, **refused by preflight**: `free space on Docker's disk and the server folder: 25 GB free, and the install needs 40 GB` — `C:` had refilled during the WotLK build | `tbc77b-wrapper.log`; the refusal text was read live and is quoted here (the log was overwritten by the second start) |
| 05:11:45 | the press that ran (`source=745307ad second press on the finished 09-04 folder`) | `tbc77b-wrapper.log` |
| 05:11:4x → 05:12:34 | steps 1–10 all found done: `Already finished: clone-sources, write-dockerfile, generate-compose, build, extract, mmaps, conf, import`; sources left "exactly as it is", Dockerfile "already exactly what this install needs", `mangos.yulon_install records a finished import` | `tbc77b.log` |
| 12:12:34Z / 12:12:50Z | `tbc-db`, then `tbc-realmd` + `tbc-mangosd` started (`up`) | `tbc77b-final/live-recheck.txt` |
| 05:12:51 | `ready`: *"Waiting for the world server. A first boot loads the whole world and can take many minutes; this waits as long as the server keeps printing, and calls it stuck after 30 minutes with nothing new."* — the quiet budget's own sentence, first seen on a gate box | `tbc77b.log` |
| 12:22:24Z | `CMANGOS: World initialized` — 9 min 34 s after the container started | `docker logs -t tbc-mangosd`, quoted below |
| 12:22:33Z | first `Avg Diff: 138. Sessions online: 0.` — the marker the catalog waits for | same |
| 05:22:33 | `The server is up.`; 05:22:34 `install of wow-tbc finished`, **errorlevel 0** | `tbc77b.log`, `tbc77b.exitcode` |

Wall: 05:11:45 → 05:22:34 = **10 min 49 s**. Ready stage 05:12:51 → 05:22:33 = 582 s. The boot
is a warm one (the world data was already on disk from 09-04), so 9 min 34 s says nothing about
the 46-minute first boot it replaces; what it says is that the engine now reports a complete
install as complete.

## What the box said afterwards (`tbc77b-final/live-recheck.txt`, 06:08 box-local)

* `tbc-realmd` Up, `0.0.0.0:3724`; `tbc-mangosd` Up, `0.0.0.0:8085`; `tbc-db` Up (healthy),
  `127.0.0.1:3306`; `RestartCount=0` on all three. Realm advertising `172.30.52.119`.
* The `docker logs` lines in that capture are dated 2026-09-04: the containers were restarted,
  not recreated, so their logs begin with the 09-04 boot. Today's lines were pulled separately:

```
2026-09-05T12:22:24.922419170Z       CMANGOS: World initialized
2026-09-05T12:22:33.231232273Z mangos>Avg Diff: 138. Sessions online: 0.
2026-09-05T12:24:32.322998130Z Avg Diff: 1061. Sessions online: 0.
```

## The headless log (bug-checklist §42)

`C:\Users\pk\AppData\Roaming\Yulon\yulon.log` on the box was written by this run — the first
headless install through `install_wiring` to leave one, because `745307ad` carries
`lane/headlesslog`'s `configure(config_dir=…)` call. `tbc77b-final/yulon-log-excerpt-headless-tbc.txt`
is every line of it stamped between 05:1x and 06:0x box-local: 85 lines, the twelve `Step N of
12` markers, `start_staged()`, `The server is up.` and `install of wow-tbc finished` among them.
That is §42's third box — *run the CLI installer headlessly, then find the log and the stage
lines in it* — met on the box it was filed for.

One line in it is new and is filed as its own entry (§43): at 05:12:16, `WARNING
[yulon.catalog.native] not holding this machine awake: keep_awake() must run on the worker
thread doing the install: Windows scopes the assertion to the thread that set it, so holding
it on the GUI thread would claim a guarantee the install does not have.` The headless harness
has no GUI thread; its main thread IS the thread doing the install, and the guard refused it
anyway. The install was not affected (the box did not sleep), but a laptop running a scripted
install would not be held awake.

## Files

* `tbc77b.log` — the transcript (80 lines; a second press over a finished folder is short).
  `tbc77b.exitcode` — `0`. `tbc77b-wrapper.log` — both start/end pairs.
* `tbc77b-final/live-recheck.txt` — `docker ps`, `docker inspect` (`StartedAt`, `RestartCount`),
  the first three matching world-server lines (09-04's, see above).
* `tbc77b-final/yulon-log-excerpt-headless-tbc.txt` — the headless `yulon.log` lines for this run.
