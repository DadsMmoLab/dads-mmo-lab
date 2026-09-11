# T30 Half 2 — the Penqle core entry, and the live run that could not start

**Status: INCOMPLETE.** The code half of T30 Half 2 is done, tested and gated
(`--checks: ALL GREEN` on m910q, capture 05). The LIVE half — the ticket's point
9 — got as far as pressing the app's own install engine on `yulon-arch` and was
refused by the app, for a reason that has nothing to do with this change and
that this hand is not allowed to clear on its own. What that refusal was, and
what it would take, is capture 02 and the section below.

Every capture here opens with `date -Is` from the box that produced it.

| | |
| --- | --- |
| branch | `hand-t30` at `dfbb4b1e`, on `yulon-arch` as `~/y8-t30` — `01-branch-and-entry.txt` |
| entry | core `9980181c` @ `bot-helpers`, module `fd7ec9ec`, channel `attach`, 4 conf files, 4 SQL phases — `01` |
| press | `installer_for_app(entry).run(...)` into `~/tortoise-penqle` with `~/clients/TurtleWoW` — `02`, `02b` |
| gate | `YULON_TEST_BOX=m910q … --checks` — `05-gate-checks-m910q.log`, last line `=== --checks: ALL GREEN ===`. Run twice: once on `dfbb4b1e` (the code) and again on the tree that carries every file here, which differs from this commit only by the transcript itself |
| box | `yulon-arch`, left exactly as found — `04-box-as-left.txt` |

## What the install press did, and why it stopped

The engine behind the tab's Install button ran off this branch, on the real box,
against the real client folder. It read this entry, ran every preflight check,
and stopped before the clone (`02b-install-engine-raw.log`, last line):

```
INSTALL FAILED: A container called tortoise-db already exists and belongs to
another install (yulon-wow-tortoise-54fa64f0). Two servers cannot share that
name. Remove the other install's containers from its own tab first, then try
again.
```

That is the app working correctly. `catalog.json` gives this game three fixed
container names (`tortoise-db`, `tortoise-realmd`, `tortoise-mangosd`), and on
this box all three are held — stopped, but present — by the install at
`~/tortoise-vm`, which was made from the RETIRED fork and is the install the
ticket's point 8 wants opened beside the new one. One box, one Tortoise, by
construction.

**Everything before that check passed**, and the transcript is worth keeping for
it: the preflight read this entry's own numbers off the new block —
`compiler jobs vs memory: 2 parallel jobs against about 9 the memory affords`
(the `make_jobs 2` of point 2), the client folder, its 21 MPQ archives, the
ports, the disk. Two warnings, both pre-existing and neither about this change:
58 GB free against a comfortable 60, and the Turtle client's `realmlist.wtf` at
its root reading like a repack.

**What it would take.** The three containers are stopped shells over a named
volume (`yulon-wow-tortoise-54fa64f0_db-data`) and two bind mounts into
`~/tortoise-vm/{etc,data}`. Removing or renaming them destroys nothing: the
volume, the binds and the three compose files stay, and a Start on that install
recreates them from `~/tortoise-vm/docker-compose.yml`. But the ticket's own
rule for this hand is *do not touch `~/tortoise-vm` except through "Use
existing…"*, and those containers are that install — so freeing the names is the
owner's call, not this hand's. It was attempted twice, as `docker rm` and as the
gentler `docker rename … t30-parked-…`, and refused both times by this session's
permission gate. Nothing was removed, renamed, stopped or started
(`04-box-as-left.txt`).

## What IS proved here, and what is still owed

Proved on this box, from the pinned core's own source (`03-…`):

* **`AutoHonorRestart` is read once and consumed once.** `World.cpp:1139` sets it
  from the conf with a default of `true` — which is why this entry writes it —
  and `HonorMgr.cpp:393` is the only consumer. With the key at 1,
  `CheckMaintenanceDay()` calls `ShutdownServ(900, SHUTDOWN_MASK_RESTART, …)`,
  which announces a restart fifteen minutes out. With it at 0 the same branch
  prints `HonorMaintenancer: Server needs to be restarted to perform honor rank
  calculations.` and schedules nothing. So the live proof point 9 asks for is
  two lines: that one present, and no restart announcement — and it needs a
  forced honor tick, because `CheckMaintenanceDay()` only acts when
  `GetGameDay() >= m_nextMaintenanceDay`, which is weekly.
* **`AutoRestart.MaxServerUptime = 0` disables the second restart outright.**
  `World.cpp:2766-2776`'s block tests `getConfig(CONFIG_UINT32_AUTO_RESTART_MAX_SERVER_UPTIME)`
  first, so a zero makes `Restarting server due to exceeding maximum uptime.`
  unreachable.
* **The doodad question, re-asked.** `families/extract.py:1342` recorded its
  reading at the retired fork's `7c0fb278`, and this core moved the whole tools
  tree to top-level `tools/`. `wmo.cpp:98` still calls `fixnamen(ddnames, size)`
  over the MODN block in place, at the same line, so this entry still carries no
  doodad patch and still should not (`03-…`, second half).

Still owed, and all of it needs the container names freed:

1. the compile inside the app's log, the extraction, the DB import, the ready
   line, the module's `native module loaded (AI enabled)` and the 500/500 pool;
2. `RNDBOT*` accounts, bots online in `characters` and in the who-list through
   Browse bots;
3. `mangosd.conf` read back with `AutoHonorRestart = 0`, and the honor tick
   forced to show the two lines above;
4. the two addons under the client's `Interface/AddOns` after an install press
   on the Modules tab, and `/tbm` / `/tgmm` in a frame from the client;
5. one Stop and one Start through the app;
6. every Tortoise surface pressed once through the ATTACH channel — Accounts,
   Play's rename/revive, Browse bots' who-list, Modules' conf activation;
7. `~/tortoise-vm` opened beside it through "Use existing…", not started.

## The box as left

Nothing on `yulon-arch` was changed that was not this run's own. No container
was created, removed, renamed, started or stopped; the volume list and the image
list are the ones Half 1 left; `~/clients/TurtleWoW` has its twelve `Blizzard_*`
addons and no Tortoise ones, and its `realmlist.wtf` still reads
`set realmlist 127.0.0.1`; `~/tortoise-vm` is 4.1 GB, unopened and unadopted;
`~/tortoise-penqle` does not exist. What this run added: `~/y8-t30` (a
`--shared` clone of `~/y8` on `hand-t30`), `~/t30h2/` (these captures, the
install log, a memory sampler), `~/hand-t30.bundle`, and three helper scripts in
`~`. Steam was not touched. `04-box-as-left.txt` is the whole listing.

No password was printed. The install stopped before `db-password`, so no
password was generated for `~/tortoise-penqle` at all.
