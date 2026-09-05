# 7.2 gate: the Ubuntu install re-run from `clean-ssh`, 2026-09-04/05 — lane gate-71-72

`pyplan/checklist.md`, 7.2 → *"Gate: full checks green; 7.1's Ubuntu gate re-run from the same
checkpoint with no other change."* This folder is the re-run, on `yulon-ubuntu` restored from
the `clean-ssh` checkpoint (2026-08-28) at 23:57:34 on 2026-09-04, driven through
`pyplan/gates/press-driver.py` as lingering `systemd --user` units, with the whole transcript of
every press kept. The same lane also produced the 7.10 widget-cancel run (`widget-cancel.log`)
in the window between press 1 and press 2, and, after the install was complete, a second
kill+resume cycle on a throwaway folder to earn 7.1's ccache clause after the first attempt's
measurement destroyed its own evidence (see "Cycle 2" below — that story is the most useful
thing in this folder).

Code under test: `pjerra/dads-mmo-lab` `yulon-phase7` at `4c959d70` cloned on the box, plus
the two files that separate it from `2f39a6d9` (`tests/test_catalog.py`,
`yulon/catalog/catalog.json`) copied in and md5-verified — `2f39a6d9` was not on the remote
(`git ls-remote` read `4c959d70`), so this is `2f39a6d9`'s `pylauncher/` content on a `4c959d70`
clone. Box: Ubuntu 24.04.4, kernel 7.0.0-30, 15 CPUs, 19 GB, Python 3.12.3; after press 1,
Docker 29.1.3 / Compose 2.40.3 / buildx 0.30.1 from Ubuntu's own packages.

## Timeline (CEST, 2026-09-05 unless marked)

| when | what | file |
|---|---|---|
| 09-04 23:55:45 | state of the box BEFORE the restore (the 7.1 lane's finished install, 500 bots, realm on Tailscale) | `state-before-restore.txt` |
| 23:57:34 | `Restore-VMSnapshot clean-ssh` + `Start-VM` from `vmhost` | — |
| 23:58:41 | as-restored probe: **no docker, no docker group, no `~/wowserver`, ufw inactive, 78 GB free**, up 0 min | `state-as-restored.txt` |
| 00:00:18 | box preparation: `apt-get install python3.12-venv` (the one package a venv needs and `clean-ssh` lacks), venv from `requirements.txt` only | `box-preparation.txt` |
| 00:01:56 | zero-bash sampler started (every 15 s until 01:58:54) | `zero-bash-sampler.log`, `.sh` |
| 00:01:59–00:03:02 | **press 1**: consent asked once, answered `y`; Docker installed; re-login refusal; exit 1 | `press1.log` |
| 00:07:09 | ccache probe T0 (v1, `--no-cache` — see cycle 2) | `ccache-stats.txt:1` |
| 00:07:41–00:13:29 | **7.10 widget cancel**: Install clicked, Stop clicked 20 s into clone-core, the cancel copy arrived; 15 OK / 0 FAIL | `widget-cancel.log`, `widget_cancel_driver.py`, `widget-cancel-folder-after.txt` |
| 00:15:37 | **press 2** under `sg docker -c`; clone 00:15:39, build 00:21:13 | `press2.log` |
| 00:30:18 | **SIGKILL** of the whole press-2 unit at ninja edge **1226/1834** (BuildKit stage clock 366.1 s); daemon compiler processes 36 → 0 within 4 s | `kill-record.txt`, `kill-watcher.sh`, `kill-watcher-as-run.sh` |
| 00:31:22 | ccache probe T1 (v1) — **this reset the engine's ccache mount**; found out at 01:19 | `ccache-stats.txt:17`, `cachemount-diag3.txt` |
| 00:32:19–01:09:56 | **press 3**: resume, `Already finished: clone-core, clone-modules, generate-compose`, build re-entered, 1834/1834, client-data, import, up, `--- ready`, exit 0 | `press3.log` |
| 01:14:33 | final state: 3 containers up, 22/111/315/30 tables, 500 bots online, ports as designed | `final-state.txt` |
| 01:15–01:19 | why press 3 recompiled everything: three busybox diagnostics | `cachemount-diag*.sh/.txt` |
| 01:20:46 | **cycle 2** prep: real stack stopped, `docker builder prune -af` (12.28 GB), v2 probe T0 = empty | `cycle2-prep.txt` |
| 01:21:51 | cycle-2 press A on the cancelled folder: **refused, container name owned by the other install** | `cycle2-pressA-refused-container-name.log` |
| 01:23:00 | real install's containers removed (`compose down`, volumes and images kept); press A2 on the same folder: **refused, "already a git checkout … no record here"** | `cycle2-compose-down.txt`, `cycle2-pressA2-refused-existing-checkout.log` |
| 01:24:05 | press A3 on a fresh folder `~/gate72-cycle2`; SIGKILL at edge **605/1834** (210.7 s) at 01:35:32; T1 = **590 misses, 340 MB** | `cycle2-pressA3.log`, `cycle2-kill-record.txt`, `ccache-stats.txt:63` |
| 01:36:39 | press B (resume): the first **590 edges replayed in ~5 s**, build done at 1078.1 s; unit killed at `The build finished`; T2 = **590 hits / 2400** | `cycle2-pressB.log`, `cycle2-edge-rate.txt`, `cycle2-finished-record.txt`, `ccache-stats.txt:79` |
| 01:56:37 | throwaway container/images/volumes/folders removed; real stack up through the engine's own `compose up -d --no-deps …`; `World Initialized In 1 Minutes 19 Seconds` | `cycle2-cleanup.txt` |
| 01:58:57 | account `YULON` (id 101, GM 3) through `ControllerServices.create_account`, second call converges | `account.log` |
| 01:58:59 | this run's own `docker compose config` (no `-f`) — md5 identical to the 7.1 clean run's | `compose-config.raw.json`, `.yml` |
| 01:58:59 | final state 2; sampler stopped; **box left running with the finished install** | `final-state-2.txt` |

## The 7.1 gate line's clauses, as this re-run answers them

| # | clause (as reworded 2026-09-04) | verdict here | where |
|---|---|---|---|
| 1 | clean checkpoint | **MET** — `docker: NOT INSTALLED`, no docker group, no `~/wowserver`, 78 GB, up 0 min, straight after the restore | `state-as-restored.txt`; `press1.log:3-14` (the driver's own before-probe) |
| 2 | starting state captured before press 1 | **MET** | `press1.log:3-14` |
| 3 | press 1: consent dialog | **MET** — asked once, `answered group x1` | `press1.log:29-31,44` |
| 4 | press 1: re-login report | **MET** — verbatim, and `state-after: id -Gn` still without `docker` | `press1.log:34-37,45-51` |
| 5 | re-login | **MET as the 7.1 lane did it**: press 2 under `sg docker -c`, whose before-probe shows `docker` in `id -Gn` | `press2.log:9` |
| 6 | a later press reaches `ready` | **MET** — press 3, `--- ready` at line 3372, `install of wow-wotlk finished` 01:09:56, exit 0 | `press3.log:3371-3380` |
| 7 | kill mid-build | **MET** — SIGKILL at edge 1226/1834, unit `Result=signal`, no install/buildx/inhibitor survivors, daemon compile stopped | `kill-record.txt` |
| 8 | resume recovers the finished objects from the ccache mount | **NOT observed on press 3** (see below: the probe reset the mount); **MET in cycle 2** on the same box: 590 objects, 340 MB, replayed in ~5 s, 590/2400 hits | `edge-rate.txt` (press 3: no replay); `cycle2-edge-rate.txt`, `ccache-stats.txt:63-91` |
| 9 | `docker compose config` matches a fixture from a different run | **MET** — the 09-04 clean capture: 59 passed, 0/0 differences; this run's capture is byte-identical (md5 `5ec739cc…`) | `../7.1-ubuntu-2026-09-04-clean/compose-diff.txt`, `COMPOSE-CAPTURE.md` |
| 10-12 | auth log `127.0.0.1:8085`, no `UPDATE`, from the container's log | **captured, not claimed** — an owner decision per the brief. `docker logs ac-authserver` line 42: `Added realm "AzerothCore" at 127.0.0.1:8085.`; a case-insensitive `UPDATE` grep hits 4 lines | `final-state.txt` |
| 13 | account | **MET** — `YULON` id 101 GM 3, read back by `docker exec mysql`, second call `created=False` | `account.log` |
| 14 | client login from the host | **NOT RUN** — needs the owner's laptop client; Tailscale is not on this restore | — |
| 15 | after the LAN step | **NOT RUN, deliberately** — bug-checklist §39 (the step enables ufw and cuts SSH); another lane owns it. The realm row reads `172.30.55.119` because the engine's `ready` stage set it, not the LAN step | `final-state-2.txt` |

## Zero bash on the path

The 7.2 line deletes the bash lineage; this run has to show none of it ran. Three independent
witnesses, none of them the engine's own claim:

* **The files are not there to run.** `find ~/gate72 … -name 'install-*.sh' -o -name dml-start.sh
  -o -name wow-manage.sh` outside `archive/` → none; the same over all of `$HOME` → none
  (`final-state.txt`). `~/dads-mmo-lab-install-*.log`, the bash installer's own log, → none at
  every probe, and `install-logs=0` on all 466 sampler lines.
* **Nothing shaped like them ran.** `zero-bash-sampler.sh` polled every 15 s from 00:01:56 to
  01:58:54: 466 samples, **465 with `lineage=[none]`**. The one exception (01:14:36, line 490)
  is the final-state probe's own shell (pid 78136), whose `bash -c` argv spells the three
  filenames it was about to look for and therefore matched the bracketed pattern — a `pgrep -f`
  matches any process whose argv contains the string, so a recorder that names the files it is
  looking for is seen once by another recorder. It is the "audit by argv" trap; the line is
  left in rather than cleaned.
* **What shell scripts DID run, each accounted for.** The sampler's second column caught every
  process with a `.sh` in its argv: this lane's own helpers (`kill-watcher.sh`,
  `finished-watcher.sh`, `ccache-probe.sh`, `cachemount-diag*.sh`, one `sed` on the watcher), and
  two inside containers — `docker-entrypoint.sh mysqld` in `ac-database`, and the client-data
  init's `bash -c … fns=/azerothcore/apps/installer/includes/functions.sh …`, which reads
  upstream's VERSION out of that file with `sed`. Nothing from `catalog/installers/`.
* **The transcripts say what ran**: press 1 had no stage at all (refused before the spine);
  press 2 `--- clone-core … --- build`; press 3 all nine. The only `.sh` strings in the three
  transcripts are `apps/docker/entrypoint.sh` — the AzerothCore image's own entrypoint.

## Press 3 recompiled everything, and it was the measurement's fault

`edge-rate.txt`: press 3 was *slower* than press 2 at every edge (edge 1226: 404 s against
366 s) and reported 1810 `Building` edges from a ccache that should have held ~1205 objects.
The v1 probe (`ccache-probe.sh`, `ccache-probe.Dockerfile`) read the `/ccache` mount with
`docker build --no-cache`. Three busybox experiments, ~2 minutes in all:

* `cachemount-diag.txt` — control FAILS: a RUN that completes writes a marker into a cache
  mount; the next build, same id, does not see it. Every build there ran with `--no-cache`.
* `cachemount-diag2.txt` — the same shapes without `--no-cache`: the control passes; **a writer
  SIGKILLed 15 s in keeps its 52.44 MB** in the mount (`/diag-a4`, usage count 2).
* `cachemount-diag3.txt` — the decisive one: normal build writes a marker → a `--no-cache` build
  lists the mount **empty** → a normal build afterwards finds the marker **gone**. So on this
  daemon (BuildKit v0.26.2 inside docker 29.1.3) **a `--no-cache` build that names a cache
  mount resets it**, it does not merely ignore it.

Therefore T1 at 00:31:22 — taken between the kill and the resume, with `--no-cache` — emptied
the 1226 objects press 2 had put there, and press 3 compiled from nothing. The kill itself did
not lose anything: diag2 shows a SIGKILLed writer's contents survive, and cycle 2 shows it at
full scale. Recorded for the 7.1 line beside the existing "a `docker builder prune` between the
kill and the resume costs a full compile": **so does any `--no-cache` build that names
`target=/ccache` on the same builder.** The v2 probe (`ccache-probe.v2.sh`, `.v2.Dockerfile`)
drops `--no-cache` and uses a `NONCE` build-arg to make its reading RUN execute.

## Cycle 2 — the ccache clause, earned

After press 3 had reached `ready`, with the real stack stopped and `docker builder prune -af`
so the throwaway build could not be served from BuildKit's layer cache:

* **press A3** on a fresh folder `~/gate72-cycle2` (install id `846544ff`), SIGKILL at edge
  **605/1834**, stage clock **210.7 s**; `cycle2-kill-record.txt`. (Its "survivors" section
  lists pid 107605 — that is the lane's own outer shell, whose argv quotes
  `yulon.install_wiring` in the press-B command it was about to run; the bracketed pattern
  protects a recorder from its own argv, not from another shell that spells the same string.
  The unit itself: `Result=signal`.)
* **T1 (v2 probe)**: `Cacheable calls 590 / 590, Misses 590, Cache size 0.2 GiB, 340M /ccache`.
* **press B**: `Using … (resuming)`, build re-entered from edge 1, `cycle2-edge-rate.txt`:

  | edge | A3 (cold) | B (resume) |
  |---|---|---|
  | 100 | 18.87 s | 8.94 s |
  | 500 | 168.2 s | 14.21 s |
  | 600 | 209.2 s | 29.69 s |
  | 1226 | — | 230.9 s |
  | 1834 | — | 1078.1 s |

  The first ~590 edges came back in ~5.4 s (8.79 → 14.21 s), the knee sits at the object count
  T1 reported, and the whole 1834-edge build took **1078.1 s** against **1189.7 s** cold on
  press 3. ccache still reports every edge (`1810 Building` both times) — the replay is a
  hit per object, not a skipped stage; the engine's `already built; skipping the compile` line
  is absent, as it should be on a resume.
* **T2 (v2 probe)**: `Cacheable calls 2400, Hits 590 (24.58%), Misses 1810, 584M /ccache`.
* The unit was SIGKILLed at `The build finished.` so the throwaway would not start containers
  under the real install's names; one `ac-client-data-init` (846544ff) had been *created* by
  then and was removed with the throwaway's four images, two volumes and network
  (`cycle2-cleanup.txt`).

## Two findings for the honest-cancel copy (7.10), from the widget run and cycle 2

`widget-cancel.log`: 15 OK / 0 FAIL. The tile's `Install` was clicked, the engine reached
clone-core in 7.1 s, `Stop` was clicked 20 s later, `panel.cancelled` went True at once, and
**320.4 s later** `install_finished(ok=False)` arrived with a modal titled *Install cancelled*
whose text **is** `cancelled_install_message()` for the folder as it was then — the copy at
`catalog_view.py:753`, arriving from a real cancelled install for the first time. Two things
it showed that the copy does not know:

1. **The copy's compose-file split was decided by upstream's file.** `compose_file()` found
   `docker-compose.yml` in the cancelled folder — git-tracked and unmodified, i.e. AzerothCore's
   own (`widget-cancel-folder-after.txt`). generate-compose had never run. So the user got the
   "the source is there … press *Use existing…*" branch after nothing but a clone, and
   `attach_existing()` would presumably accept that folder for the same reason.
2. **"Press Install again and choose <folder>: the installer carries on from the last stage
   recorded in `.yulon-install.json`" is not what happens.** No state file existed after the
   cancel (the spine raised before the first stage's completion was recorded), and a re-press
   on that folder was **refused**: *"already a git checkout of … and there is no record here of
   an install this app made … Install into an empty folder instead"*
   (`cycle2-pressA2-refused-existing-checkout.log:31`). The refusal is the right behaviour
   (refuse-not-delete); it is the copy that promises a carry-on the engine will not give.
   Also measured on the way: a Stop during clone-core cannot interrupt the containerized `git`
   (`docker ps` 5 s after Stop still showed the `alpine/git` container), so the 5-minute wait
   between Stop and the dialog is what a user pays.

And one more from cycle 2: **two installs cannot coexist on one box even to build**, because
AzerothCore's compose pins `container_name` and the engine refuses at the name
(`cycle2-pressA-refused-container-name.log:26`) — after preflight had passed the ports check,
since the other install's containers were merely stopped.

## Full checks at this code

`full-checks-m910q.txt`: `run-tests-vm.sh --checks` on m910q against `lane/gate-71-72`
(`2f39a6d9`): **2341 passed, 4 skipped**; mypy ×3 `Success: no issues found in 71 source
files`; ruff `All checks passed!`; black `136 files would be left unchanged`; **ALL GREEN**,
exit 0.

## Mistakes kept in the record

* `kill-watcher.sh` was written for 1829 ninja edges (the count every earlier record has); this
  core revision has **1834**, so the watcher never fired and the kill landed at edge 1226 by
  hand instead of ~900. `kill-watcher-as-run.sh` is the corrected pattern; both are here.
* The v1 ccache probe, above. Its three readings (T0, T1, T2 in `ccache-stats.txt:1-47`) are
  kept: they are the record of what a `--no-cache` probe reads, which is nothing, every time.
* Cycle 2 took three presses to start (container name, then existing checkout, then a fresh
  folder). Both refusals turned out to be findings and are kept as such.

## State the box was left in

`final-state-2.txt`: `yulon-ubuntu` RUNNING, `~/wowserver` complete (state file: six stages),
`ac-database` healthy, `ac-authserver` and `ac-worldserver` up, `World Initialized In 1
Minutes 19 Seconds`, 3724/8085 on all interfaces, 3306/7878 on loopback, schemas 22/111/315/30,
account `YULON` (101), realm `172.30.55.119:8085`, ufw **inactive**, **no Tailscale** on this
restore, 53 GB free. Checkout `~/gate72` (venv `~/gate72/pylauncher/.venv`), every transcript
still in `~/gate72-*.log/.txt`. The lingering units are left as they finished (`systemctl --user
list-units 'gate72*'`).
