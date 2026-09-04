# 7.10 cross-server regression sweep, 2026-09-04, `yulon-ubuntu`

Answering `pyplan/checklist.md:1333` — *"re-run WotLK's 6.5 coverage gate after 7.1–7.9 land to
confirm shared layers (`docker.py`, base `Controller`, `runner.py`, `platform.py`, `networking.py`)
weren't regressed"*.

**Target.** The WotLK server the 7.1 lane installed and left running at `/home/pk/wowserver`
(`pyplan/gates/7.1-ubuntu-2026-09-04/`). Nothing was reinstalled: this sweep drives the app's own
wiring against that live install.

**Code under test.** `/home/pk/gate0904/checkout` at `81d7311e803745c1659f27aea6610e663aadd891` —
which is the tip's code. `git diff 81d7311e badee625 -- pylauncher` is **empty**; the three commits
between them add gate evidence and docs only. So the sweep ran the same `pylauncher` bytes as
`badee625`, and the same bytes the 7.1 install was built by.

**Drivers.** The four `sweep_driver*.py` from `pyplan/hunt-runlogs/2026-08-28/drivers/`, adjusted
and not rewritten: **two constants per file** (three in driver 4). `drivers.diff` in this directory
is the complete diff against the committed originals — 5 changed lines across 4 files, all of them
paths.

| Driver | changed lines |
|---|---|
| `sweep_driver.py` | `sys.path` root, `SERVER_DIR` |
| `sweep_driver2.py` | `sys.path` root, `SERVER_DIR` |
| `sweep_driver3.py` | `sys.path` root, `SERVER_DIR` |
| `sweep_driver4.py` | `sys.path` root, `SERVER_DIR`, fake-client dir |

## Result: 20 checks, 20 OK, 0 FAIL

| Driver | checks | exit | elapsed |
|---|---|---|---|
| `sweep_driver.py` | 3 OK | 0 | 1 s |
| `sweep_driver2.py` | 7 OK | **134** (see below) | 8 s |
| `sweep_driver3.py` | 7 OK | 0 | 75 s |
| `sweep_driver4.py` | 3 OK | 0 | 2 s |

`run.log` carries the same table as the driver wrote it.

### What each shared layer answered

* **base `Controller`** — `status()` `db=True auth=True world=True`; `import_state()` `populated`,
  *"101 rows in acore_auth.account, 1000 rows in acore_characters.characters"*, `complete=True`;
  `port_conflicts()` `[]` (it excuses our own three containers); `repair_import()` **refused** on a
  populated DB with the running-server sentence; `start()` brought auth+world back after the
  restore (`sweep1.log`, `sweep2.log`, `sweep3.log`).
* **`docker.py`** — `follow_logs()` yielded 5 live worldserver lines; `docker stop` of two of three
  containers, then a start, both through the app; the console round-trip
  `send_console('server info')` returned `AzerothCore rev. 47960183bb03+ (Playerbot branch)`,
  `Characters in world: 500`, `Update time diff: 3ms`, mean 35 ms, p95 118 ms, max 162 ms.
* **`runner.py`** — every subprocess above, plus the log stream.
* **`platform.py`** — reached through the controller's docker calls and `check_for_update()`
  (`current=0.6.59 latest=v0.6.59Public available=False error=None`).
* **`networking.py`** — `network_plan('lan')` and `('internet')` both `ready=True`;
  `network_apply('lan')` **4 done, 0 skipped**; `write_client_realmlist()` round-tripped
  `set realmlist 192.168.1.50`.
* **maintenance / backup-restore** — `backup()` of the live server produced 4 dumps
  (`acore_auth` 79,574 B, `acore_characters` 9,349,240 B, `acore_playerbots` 61,979,183 B,
  `acore_world` 306,374,348 B) in 26 s while it was running; `verify_dump()` passed on all four;
  `plan_restore()` refused while auth+world were up and allowed it once they were down;
  `restore()` completed with a pre-restore safety backup;`interrupted_restore()` `None`.

### The server is intact afterwards

`state-before.txt` / `state-after-restore.txt` / `state-after.txt` are the same 152-line probe taken
three times. All three report:

* schema table counts **22 / 111 / 315 / 30** (auth / characters / world / playerbots) — the figure
  every platform's install gate has produced;
* the 7.1 gate account row `101 GATE0904` still present alongside the 100 `RNDBOT*` rows;
* realmlist `1 AzerothCore 172.30.55.119 172.30.55.119 8085`;
* 3724 / 8085 listening, `ac-database` healthy.

So the backup→stop→restore→start cycle round-tripped without losing the 7.1 lane's state, and
clause 14 of the 7.1 gate (a client login, still open) can still be closed against this box.

## The one thing this sweep found

**`sweep_driver2.py` exits 134 (SIGABRT).** All seven of its checks pass, then the interpreter dies
on the way out:

```
Fatal Python error: _enter_buffered_busy: could not acquire lock for
<_io.BufferedReader name=5> at interpreter shutdown, possibly due to daemon threads
  Garbage-collecting
  File ".../yulon/runner.py", line 236 in stream
  File ".../yulon/docker.py", line 2353 in follow_logs
```

The driver takes five lines off `logs_source()` and `break`s without closing the iterator. The
generator is then finalised by GC **at interpreter shutdown**, so `stream()`'s `finally` runs when
the daemon `_drain_stderr` thread can no longer be joined; `proc.stderr.close()` on a pipe that
thread is still blocked in makes CPython abort.

**It is not a Phase 7 regression, and that is checkable rather than asserted.** Against
`a6a86320` (the 0.6.58 tag of 2026-08-28, the code the original sweep ran):

* `runner.py::stream()` — **byte-identical**, 103 lines, `git show a6a86320:… | diff`;
* `docker.py::follow_logs()` — **byte-identical**, 35 lines.

The two functions in the traceback did not change. What did change around them is substantial —
2,133 inserted lines across the five shared files between `a6a86320` and `badee625` — which is why
the sweep was worth running; it is also why the absence of any other failure is worth something.

The 08-28 run never recorded a driver's exit status, only its `[OK]` lines, so nobody could have
seen this then. It is a latent hazard in a shared layer (any caller that abandons `logs_source()`
without closing it), not a fault the sweep introduced, and it needs a defect number rather than a
7.10 blocker.

## Corroboration of a 7.1 finding

`sweep4.log` independently reproduces the LAN defect the 7.1 lane reported: the plan contains
`('ufw', '--force', 'enable')` while `warnings=()` and `manual_steps=()`, and the report comes back
`skipped=()`. This run did not lock itself out only because port 22 was allowed by hand after the
7.1 lockout — that hand-added rule is visible in every `state-*.txt` at `[ 3] 22 ALLOW IN`.

## Files

| File | What it is |
|---|---|
| `run-710.sh` | the runner, exactly as it executed under `systemd-run --user --unit=dml-710-sweep` |
| `drivers/` | the four adjusted drivers, as run |
| `drivers.diff` | their complete diff against the 2026-08-28 committed originals |
| `run.log` | provenance header + per-driver exit code and elapsed time |
| `sweep1.log` … `sweep4.log` | each driver's full transcript, with its own exit code appended |
| `state-before.txt`, `state-after-restore.txt`, `state-after.txt` | the same 152-line probe, three times |

14 files, 1,133 lines; `wc -l` compared on `yulon-ubuntu` and on the laptop, every count equal.
