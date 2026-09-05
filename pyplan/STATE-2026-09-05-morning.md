# Where things stand, 2026-09-05 09:45 — written before a compact

`yulon-phase7` is at **0cc637c7**, pushed, CI green (py3.11, py3.13, integration) on PR **#143 → Yulon**.
The working tree is **clean**: every piece of in-flight work is on a `lane/*` branch, so nothing lives
only in a conversation.

## Phase 7: 7 of 12 ticked

Open: 7.1, 7.2, 7.7, 7.8 (hardware), 7.10.

* **7.1** — the realm clause is MET (measured: row `172.30.55.119`, and `ready`'s own line at
  `gate72-press3.log:3377`), and **clause 14 is MET as of 07:31:41 today**: a real 3.3.5a client
  authenticated against the 7.2 install — `COP_AUTHENTICATE AUTH_OK`, `COP_GET_CHARACTERS code=44
  result=TRUE`; server side `102 GATELOGIN last_login 2026-09-05 05:31:40 online 1 failed_logins 0`.
  That account was made through `ControllerServices`, so it is clause 13 evidence too. Route: an
  `ssh -L` tunnel with the realm temporarily at `127.0.0.1` — **Tailscale did not survive the
  clean-ssh restore** — both since put back. Clause 15 (the LAN step) waits on §39.
* **7.2** — TICKED at `bdee23f5` (13:30 CEST): `lane/clause72` reworded the stale clause on what it
  meant, measured the deletion on both sides of `2fddaa0e`, and went through five review rounds
  before its record stopped carrying numbers written from memory. The previous lane refused to reword a criterion so it passes,
  which was right.
* **7.7** — Tortoise PASSED on `yulon-win11-gate` at 00:43:19 box-local (exit 0, banner `Loading time:
  59 minutes 18 seconds`, `RestartCount=0`). Widened to `["linux", "windows"]` and the ready budget
  moved 3600 → 10800 s at `eb5f3b3f` (the stage measured 3702 s wall; evidence in
  `pyplan/gates/7.7-win11-tortoise/`). All four games are now proven on native Windows. Still owed:
  WotLK's transcript on that box.

## The eight lane branches, and what each owes

| branch | tip | state |
|---|---|---|
| `lane/ticks` | 2e8ae66c | MERGED at 0cc637c7 |
| `lane/readybudget` | bbe6cdf4 | round 3 running — 2 blockers: a WSL cross-daemon read, and the `restarting` alive-status no test owns |
| `lane/cancelcopy` | 2a4f0cab | round 3 running — the copy ignores the state file the app itself wrote |
| `lane/headlesslog` | a20dae99 | round 3 running — the no-write rule is in-process only; the suite spawns children |
| `lane/bug39-r6` | 9b0eb089 | needs another pass: a zone-breadth warning that fires on every ordinary Linux box (Docker makes a second firewalld zone) |
| `lane/doodad` | 961230e1 | needs another pass: BLOCKER — the new refusal's remedy dead-ends and points the user at deleting their characters |
| `lane/clause72` | ed3102b6 | MERGED into `yulon-phase7` at `bdee23f5`; worktree and branch deleted |
| `lane/dockerfile-value` | c1baabea | MERGED at `bdee23f5` — §29's value half closed; `render()` refuses a public value that carries the secret |

## Running in the background, and it survives a compact

* Workflow `wzizt9czj` (round 5 on readybudget / cancelcopy / headlesslog; round 4's verdicts and
  the meta-reviews are in the session scratchpad as `r4-*.json`). A fresh WotLK install is running
  on `yulon-win11-gate` into `D:\gate\wotlk-server77` from source `a0cc9dc0`, transcript
  `C:\gate\evidence\wotlk77.log` (started 01:46 box-local after a preflight refusal on Docker's
  disk; the VHDX was compacted 32.6 → 12.0 GB and the Tortoise stack stopped for the run).
* The Tortoise watcher is done (see 7.7 above). Every VM is at its baseline as of 10:15 CEST
  (`vmsize.ps1 -Show`: only `yulon-fedora` had drifted, down to 4/4, restored to 10/11 while Off).
  `C:\Users\PK\vmsize.ps1` on the host does the resizing and restoring, with write-once baselines;
  its headroom check now applies to Running VMs only, since an Off VM's configured memory costs nothing.

## Owner decisions still open

1. **7.8 macOS** — rent a Mac, or mark it deferred and ship at 11 of 12. `ci/macos-intel-dmg` is
   unmerged and has no PR; it was deliberately kept in the branch cleanup.
2. **The upstream CMaNGOS issue must NOT be posted yet** — the text on `lane/doodad` still carries a
   patch that `git apply --check` refuses on the commit it names.

## Bug checklist

Closed: §21, §27, §29 (both halves, `bdee23f5`), §30, §33, §40. Open: §39 (round 5 committed, two
measured lockout routes left — **the LAN button is not done**), §41 (loopback realm), §42 (a headless
install writes no log).
