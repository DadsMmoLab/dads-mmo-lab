# 7.1 Ubuntu gate, re-run 2026-09-04 on `yulon-ubuntu`

Answering the fifteen clauses of `pyplan/checklist.md:282` **as reworded on 2026-09-04**,
after `AUDIT-2026-09-04.md` found 3 of 15 satisfied in the 08-31 set. Every count below was
taken on the box and redirected into a file as it was taken; the line numbers are into the
files in this directory, and every file's line count was compared on both machines after the
copy (23 files, all equal).

Code under test: `yulon-phase7` at `81d7311e803745c1659f27aea6610e663aadd891`, cloned fresh on
the box. Server: mod-playerbots/azerothcore-wotlk, core rev `47960183bb03+` (`gate-auth.log:5`).

## Clause by clause

| # | Clause | Verdict | Settled by |
|---|---|---|---|
| 1 | "yulon-ubuntu **clean checkpoint**" | **NOT MET, and not obtainable from this checkpoint** | `state-as-restored.txt` |
| 2 | **starting state captured** before press 1 | **MET** | `press1.log:3-14` |
| 3 | press 1: **consent dialog** | **not exercised** (expected) | `press1.log` has no `(y/n)` |
| 4 | press 1: **re-login report** | **not exercised** (expected) | same |
| 5 | **re-login** | **n/a on this checkpoint** | `press1.log:9` |
| 6 | **a later press reaches `ready`** | **MET** | `press3.log:3321,3325,3327,3329` |
| 7 | **kill mid-build** | **MET** | `press2.log:3911`, `kill-record.txt` |
| 8 | resume **recovers the finished objects from the ccache mount** | **MET, with ccache's own counters** | `ccache-stats.txt:12,15,27`, `ccache-recovery.txt` |
| 9 | `docker compose config` **matches a fixture minted from a DIFFERENT run** | **MET** (0 service differences) | `compose-compare.txt` |
| 10 | auth log **`127.0.0.1:8085`** | **MET** | `gate-auth.log:42` |
| 11 | **with no `UPDATE`** | **MET**, and the naive count is *not* zero | `auth-log-analysis.txt` |
| 12 | **read from the authserver container's log** | **MET** | `gate-auth.log`, `gate-auth-after-lan.log` |
| 13 | **account** | **MET** | `account-lan.log:7,11,27,29` |
| 14 | **client login from the host** | **NOT MET** — no WoW client on this box | `lan-reachability.txt` |
| 15 | **after the LAN step** | **MET**, and it found a defect | `account-lan.log:85,91,96`, `ufw-lockout.txt` |

**Tally: 10 met, 2 not met, 3 not exercised (already earned on 08-31).**

## The three things this run found that nobody was looking for

1. **`pre-7.2-gate-2026-09-02` is not a clean checkpoint.** It restores with a COMPLETE 08-31
   install at `~/wowserver` (state file `completed: [clone-core … import]`, install id
   `243c46e3`) and a RUNNING stack — `ac-worldserver` at 5.6 GB RSS, `ac-authserver`,
   `ac-database` — holding 3724/8085/3306, with 43 GB free. `state-as-restored.txt` records all
   of it before anything was touched. Press 1 refused on the spot: *"41 GB free, and the install
   needs 48 GB"* (`press1.log:21`). The box had to be cleaned by hand — stack down, `~/wowserver`
   moved aside, the 08-31 volumes/images/build cache removed (`box-preparation.txt`,
   `disk-reclaim.txt`) — before a fresh install could start at all. **7.2's "re-run from the same
   checkpoint with no other change" cannot be run from this checkpoint** until it is retaken from
   a genuinely clean box.

2. **The LAN step locks the operator out of a remote Linux box.** `networking.apply()` ran
   `ufw --force enable` with only 3724/tcp and 8085/tcp allowed and ufw's default-deny incoming
   policy, and SSH died mid-gate. `report.skipped` was empty, `plan.warnings` was empty, no manual
   step mentioned it. Recovery needed the Hyper-V synthetic keyboard driving the guest's GNOME
   session by hand. Full account, including why `Msvm_Keyboard.TypeText` was useless and virtual
   key codes were not, in `ufw-lockout.txt`.

3. **The committed compose fixture carries a strip its own brief does not document.** Four nested
   `name:` keys are present in the 08-31 raw capture (`~/gate-compose-config.yml:212,214,217,219`),
   present in mine at the same line numbers, and absent from
   `pylauncher/tests/data/wotlk-compose-config.json`. The E.2 brief documents only
   `raw.pop("name")` at the top level, which cannot reach them. Those four keys are the ONLY
   residue between the two runs once the capture route is matched. See `compose-compare.txt`.

## Files

| File | What it is |
|---|---|
| `state-as-restored.txt` | the checkpoint's real state, captured before any change |
| `box-preparation.txt` | stopping the 08-31 stack and moving `~/wowserver` aside |
| `disk-reclaim.txt` | removing the 08-31 volumes, images and build cache, and why the prune was deliberate |
| `press1.log` | press 1 — starting-state capture, then refused on free space |
| `press2.log` | press 2 — fresh install, SIGKILLed mid-build at edge 896/1829 |
| `press3.log` | press 3 — resume after the kill, reaches `ready`, exit 0 |
| `kill-record.txt` | the kill, the edge, the survivors, and the argv trap it fired |
| `ccache-stats.txt` | `ccache -s` at T0 (empty), T1 (881 misses), T2 (881 hits) |
| `ccache-recovery.txt` | the same edge in both presses, and the knee at edge 885 |
| `compose-compare.txt` | three comparisons against the committed fixture, plus the addendum |
| `gate-compose-config.yml` | this run's capture, the 08-31 route (219 lines) |
| `gate-compose-config.raw.json` / `.json` | the `--format json` capture, raw and transformed |
| `gate-compose-config-from-yaml.json` | the YAML capture put through the documented transform |
| `gate-compose-diff.txt` | the byte diff the `--format json` route produces |
| `gate-auth.log` | the authserver container's log at `ready` |
| `gate-auth-after-lan.log` | the same log after the LAN step and a restart |
| `auth-log-analysis.txt` | the two clause counts, what each grep matched, and the pair |
| `account-lan.log` | account + LAN, driven through `ControllerServices.for_entry()` |
| `ufw-lockout.txt` | the firewall defect, the lockout, and the console recovery |
| `lan-reachability.txt` | the realm address probed from the Hyper-V host |
| `press-driver-as-run.py` | the driver exactly as it ran (md5 `5eb42d2ce9f9448c5fe4b7009596bbb7`) |
| `claude-activity.log` | the box's own announcement trail |

## State the box was left in

Left RUNNING with the install complete and the stack up: `ac-worldserver`, `ac-authserver`,
`ac-database`, realm advertising `172.30.55.119:8085`, account `GATE0904` (id 101, GM 3). Not
shut down and not reverted deliberately — clause 14 is the only one a machine could still close,
and it needs this exact state. `ufw` is active with 3724, 8085 **and 22**; the 22 rule was added
by hand and is not the product's doing. `~/wowserver-0831` still holds the 08-31 install.
