# Ubuntu "100% operational" run — the bar PR 1 must clear

Run on yulon-ubuntu from a KNOWN checkpoint, against the final PR-1 tree (post-review), with the
activity window announcing every step. Nothing below may be taken from an earlier round: the point
is that the fixed scripts and the fixed app do it, end to end, on one box, in one sitting.

## Preconditions
- Ubuntu resized (8 vCPU / 23 GB). Fedora/Arch/Win11 off — this box gets the budget.
- `docker system prune -af` first: Ubuntu was at 82% disk after round 2.
- Stop and REMOVE any old `ac-*` containers (`docker rm`, no `-v`) so the foreign-container
  refusal is exercised on purpose, not tripped by accident — see step 4.

## The checklist (each item: command + output recorded)
1. Clean clone of the PR-1 branch; `ruff`, `black --check`, `mypy .` AND `mypy yulon main.py`
   agree (both 0 issues, same file count).
2. `pytest -q --ignore=tests/integration` green; `pytest -q` (with live Docker) green **and under
   5 minutes** — the SIGTERM fixture fix is what makes that possible; time it.
3. Six GUI files × 20 plain runs, 0 crashes; × 10 under gdb, 0 crashes.
4. **Foreign-container refusal, deliberately**: with the OLD install's stopped `ac-*` containers
   present, start a fresh install into a new folder through the GUI Install button. Expect the
   script to refuse BEFORE any build, naming the owning folder. Then `docker rm` those containers.
5. Fresh WotLK install through the GUI into a chosen folder with a SPACE in its path; completes;
   `install of wow-wotlk finished` in the app log; a tab appears without "Use existing…".
6. Server boots; `docker inspect ac-worldserver` env says 500/500; log reaches `500/500 Bot …
   logged in`; console `server info` says `Characters in world: 500`.
7. **Interrupted-install re-run, deliberately**: `docker compose stop`, delete the images
   (`docker rmi acore/ac-wotlk-*`), press Install again on the same folder. Expect
   "Nothing was installed" + a non-zero exit, folder intact, NO tab pinned. Then press Install
   on the same folder with the images present: expect the fast path ("Compiled images already
   found"), no rebuild.
8. Feature sweep, through the GUI where clickable, direct otherwise (say which): console; account
   create + the live SRP6 gate (`tests/integration/test_accounts_live.py`); backup → verify →
   restore (with the safety backup); one module apply + remove round trip; networking LAN plan +
   apply; maintenance `import_state`; log follow; self-update (expect the documented 404).
9. Status-poll wedge, deliberately: `sudo systemctl stop docker` while a tab is open; the status
   label must read "Docker not reachable" within ~35 s, and recover within 5 s of
   `systemctl start docker`. (The old code stayed on "unknown" forever.)
10. Close the app mid-"Follow worldserver log": no abort, exit 0, nothing left running.
11. **Tortoise install, for real, on m910q** (the one box with a client: `~/TurtleWoW`). Stop the
    live `tortoise-*` stack first (ports 3724/3306), install into a NEW folder through the launcher
    with `~/TurtleWoW` as the client dir, boot to `Ready to login`, create an account, confirm
    `mangos_sha` in `tw_logon`. Then restore the original stack. This is the first-ever run of
    `install-tortoise-wow-wsl.sh` by the hunt.
12. **TBC and Vanilla installs, for real, on m910q** — the clients are there now
    (`~/clients/WoW-Client-1.12.1`, `~/clients/WoW-Client-2.4.3`, both from the owner's URLs).
    Each: stop whatever holds 3724/3306, install into a NEW folder through the launcher with the
    client dir given, watch the extract/mmap stages (first-ever runs of `install-wow-vanilla.sh`
    and `install-wow-tbc.sh` by this effort), boot to the realmd/mangosd ready lines, create an
    account (`mangos_srp6` — verify `s`/`v` populated, not `sha_pass_hash`), confirm the bot count
    is **500** (`AiPlayerbot.MinRandomBots = 500` in `aiplayerbot.conf`, then live). One at a
    time. **Disk on m910q is the constraint**: 27 GB free after extraction, and
    `install-wow-vanilla.sh:265,275` REFUSES under 20 GB on both the target disk and Docker's data
    root (same disk there). A TBC build cache will breach that, so `docker builder prune -af` and
    `docker image prune -f` between installs; the stopped old `ac-*` stack and its images on m910q
    (from an earlier session) are the next candidate if that is not enough - owner's say.
    **Prerequisite:** the 500-bot commit (`c5c7d20a` on `test/full-vm-run-2026-08-28`) must be on
    the branch under test — it is not on `fix/ubuntu-to-green` yet.
13. Take a Hyper-V checkpoint `ubuntu-green-<date>` as the new baseline.

## Out of the bar, by decision
- Self-update returning 404 (upstream releases are all `prerelease: true`; not fixable in code).
- Two installs of the same game coexisting (container names are a design decision for the owner).
- A refusal floor on `/var/lib/docker` free space (host-side dynamic-disk exhaustion is invisible
  from the guest; the warning exists).
