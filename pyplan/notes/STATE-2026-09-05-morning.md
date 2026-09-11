# Where things stand, 2026-09-05 09:45 — written before a compact

`yulon-phase7` was at **0cc637c7** when this note was first written (see `git log` for the tip), pushed, CI green (py3.11, py3.13, integration) on PR **#143 → Yulon**.
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
  clean-ssh restore** — both since put back. Clause 15 (the LAN step) waits on §39. **Superseded
  2026-09-05 23:06:57 UTC: the LAN step was pressed end to end on `yulon-ubuntu` through the real
  widgets, a client on `vmhost` logged in at 23:31:24 UTC with `last_ip 172.30.48.1`, and clause 15
  is graded MET on the 7.1 gate line; §39 is CLOSED — `pyplan/gates/bug39-lan-press-2026-09-05/`.**
* **7.2** — TICKED at `bdee23f5` (13:30 CEST): `lane/clause72` reworded the stale clause on what it
  meant, measured the deletion on both sides of `2fddaa0e`, and went through five review rounds
  before its record stopped carrying numbers written from memory. The previous lane refused to reword a criterion so it passes,
  which was right.
* **7.7** — Tortoise PASSED on `yulon-win11-gate` at 00:43:19 box-local (exit 0, banner `Loading time:
  59 minutes 18 seconds`, `RestartCount=0`). Widened to `["linux", "windows"]` and the ready budget
  moved 3600 → 10800 s at `eb5f3b3f` (the stage measured 3702 s wall; evidence in
  `pyplan/gates/7.7-win11-tortoise/`). TICKED the same afternoon: WotLK's fresh run exit 0 (2 h 34 min, `7.7-win11-wotlk/`)
  and TBC's second press exit 0 under the quiet budget (`7.7-win11-tbc-second-press/`), so all
  four games carry an exit-0 transcript on native Windows.

## The eight lane branches, and what each owes

| branch | tip | state |
|---|---|---|
| `lane/ticks` | 2e8ae66c | MERGED at 0cc637c7 |
| `lane/readybudget` | bbe6cdf4 | round 3 running — 2 blockers: a WSL cross-daemon read, and the `restarting` alive-status no test owns |
| `lane/cancelcopy` | 2a4f0cab | round 3 running — the copy ignores the state file the app itself wrote |
| `lane/headlesslog` | a20dae99 | round 3 running — the no-write rule is in-process only; the suite spawns children |
| `lane/bug39-r6` | 380ef4ed | MERGED at `0bdbc4e3` after round 10 — owner: "stop at 10" (2026-09-05). Guard ALLOWs on the production path; Docker's zone judged machine-made on the real Fedora listings; the LAN button itself is still never pressed end to end on a real remote box (§39 stays OPEN on that). **Superseded 2026-09-05 23:06:57 UTC by `lane/b39`: pressed end to end on `yulon-ubuntu`, client login from `vmhost` at 23:31:24 UTC, §39 CLOSED — `pyplan/gates/bug39-lan-press-2026-09-05/`** |
| `lane/doodad` | 851d0ca9 | MERGED at `5a57164d` after round 10 (same decision). The wedge is unreachable; the refused press launches no container; the issue draft's patch now applies (`doodad-2026-09-05/apply-check.txt`, doc-v2 exit 0 on both pinned revisions) |
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
2. **Whether to post the upstream CMaNGOS issue.** Its patch now applies on both pinned revisions
   (`pyplan/gates/doodad-2026-09-05/apply-check.txt`: doc-v2 exit 0, byte-identical to the shipped
   patch). Nothing has been posted; that is the owner's call.

## Bug checklist

Closed: §21, §27, §29 (both halves, `bdee23f5`), §30, §33, §40, §42 (headless log, `745307ad`, gate met on the Windows TBC second press). Filed: §43 (`keep_awake()` refuses the headless harness's own thread). Open: §39 (round 5 committed, two
measured lockout routes left — **the LAN button is not done**), §41 (loopback realm), §42 (a headless
install writes no log). **Superseded 2026-09-05 23:06:57 UTC: §39 is CLOSED — the LAN button was
pressed end to end on `yulon-ubuntu` and a client on `vmhost` logged in at 23:31:24 UTC
(`pyplan/gates/bug39-lan-press-2026-09-05/`), so the closed list gains §39 and the open list loses
it.**

## Handoff, 21:15 CEST — for the workflow that does "all the open ones"

Tree: `yulon-phase7` at `d911d3d5`, pushed, CI green, no lane branches or worktrees. 9 of 12 boxes.
Owner, 2026-09-05 evening: "stop at 10" (no lane gets more than ten review rounds; after ten the
merger closes line-level findings by hand and records the rest), then "we do all the open ones in
workflow".

**The open items, in the order that unblocks the most:**
1. **7.10 re-run** — the widget gate on `yulon-ubuntu` (the live 7.2 install; `pyplan/gates/7.10-gaps/`
   and `7.10-ubuntu-2026-09-04/` hold the drivers) on the merged engine. The two findings it filed on
   09-04 are what `lane/cancelcopy` fixed; the re-run is what ticks 7.10. Read-only elsewhere; this
   box IS the gate box.
2. **§41 loopback realm on purpose** — a third `networking.Mode`, intent persisted where a resume can
   read it, `ready` reading it before it overwrites; gate as written in the entry. `networking.py` is
   free now (§39's lane merged).
3. **§39 the LAN button end to end** — the guard is merged; what 7.1 clause 15 needs is the app's own
   LAN step pressed on a real remote Linux box with a console fallback (a throwaway VM reached through
   `ssh vmhost` + Hyper-V console, never a box the lane is logged into by ssh alone). Two measured
   lockouts in the record; the round-6 run on yulon-fedora applied and restored with a
   `systemd-run` failsafe — copy that shape. **DONE 2026-09-05 23:06:57 UTC by `lane/b39`, which did
   copy that shape: two `systemd-run` failsafes armed first, the console fallback proved on
   `yulon-ubuntu` before the firewall was touched, the press through the real `ControllerView`, and a
   3.3.5a client on `vmhost` logged in at 23:31:24 UTC. §39 CLOSED, clause 15 graded MET —
   `pyplan/gates/bug39-lan-press-2026-09-05/`. The box was the live 7.2 install rather than a
   throwaway VM; the lane's brief names that as the owner's decision that evening, quoting him as
   "it can also be used to finish phase 7", and the brief is the only place this lane saw it.**
4. **§43** — `keep_awake()` refuses the headless harness's main thread; small, Windows-measurable on
   `yulon-win11-gate` (Off; start it, host has the headroom).
5. **7.8 macOS** and **whether to post the CMaNGOS issue** — owner decisions, not lane work. The issue
   draft's patch applies (`pyplan/gates/doodad-2026-09-05/apply-check.txt`).

**Boxes for the lanes** (owner: "why are you not using the VMs?"): m910q (Py 3.11) and
`yulon-fedora` (Py 3.13, SELinux Enforcing; clone + venv at `~/dads-mmo-lab`, Off now — start with
`ssh vmhost 'Start-VM -Name yulon-fedora'`, check host free memory ≥ 12 GB after) are both
`run-tests-vm.sh` targets (`YULON_TEST_BOX=…`). `yulon-arch` (10/11) has no clone yet. One box per
lane; agents must never stop a VM. `yulon-ubuntu` is the 7.10 gate box, not a test box.

**Round discipline that finally held:** every number/SHA/line in a record from a command in the same
sitting, pinned to SHAs; a docstring naming a mutation as proof shows the red and which assertion;
every present-tense claim in the diff read against a command before commit; the reviewer re-derives
on the real shape; fable reviews the review. Rounds 6-10 of §39 were all "behaviour right, one
sentence false in one shape" — cap at 10 and close by hand.

