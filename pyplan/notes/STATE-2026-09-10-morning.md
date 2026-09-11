# State, 2026-09-10 morning — handoff for a fresh session

Written by the lead (Fable) at 10:45 CEST after the 2026-09-09 exit pass stalled on the monthly spend limit at ~14:30 the day before. A new session (WSL + rtk, or anywhere) resumes from this page, the tickets under `pyplan/tickets/`, the memory folder, and the worktrees named below. Nothing here needs the old transcript.

## The process in force (owner's "runes", 2026-09-09)

Lead writes the spec and files the ticket → starts it (branch, worktree, seat) → an ephemeral hand implements in its worktree, runs the suite there, commits to its branch, marks DONE with a report → a cold reviewer with the diff and the spec writes ACCEPT or REWORK → REWORK: the lead rejects the ticket with the must-fixes, the same hand fixes → ACCEPT: merge into `yulon-phase8b` ("master"; mainline `Yulon` is Baerthe's), the full suite behind the merge, red reverts → the lead closes the ticket, retires the hand, removes the worktree → step 8: title the CHANGELOG's Unreleased, version bump, tag `v…Public`, push, GitHub release (ask the owner for the version and the branch first).

**Budget rules (owner, 2026-09-09 13:00-13:05):** one reviewer per ticket — Codex adversarial (its own OpenAI limit; it hit its cooldown at 12:10 on the 9th), or a cold **Opus** reviewer while Codex is on cooldown, Fable only for live-box or >300-line tickets; **two-round cap**, after round 2 the lead fixes the remaining must-fixes by hand and merges; **Sonnet hands** for small tickets. Lanes ≤3 worktrees. Hands write scratch under `<scratchpad>/<ticket>/`. No "Generated with Claude Code" footer and no `Claude-Session:` line anywhere reaching GitHub — `Co-Authored-By: Claude <model> <noreply@anthropic.com>` alone. Every merge: `--checks` on `yulon-fedora` ALL GREEN behind it, then push. Stamps come from the clock in the script that writes them (`date +"%H:%M CEST"`), never typed.

**Gate:** from a worktree's `pylauncher/`: `YULON_TEST_BOX=yulon-fedora bash /c/Users/perzi/run-tests-vm.sh --checks` → must end `=== --checks: ALL GREEN ===`. Announce on a box first (`ssh <box> claude-say "..."`; on ubuntu2 and m910q it is `~/claude-say`). Never `docker` on the laptop; never bare `pytest` (`-m "not integration"`); never a server on the laptop; ask before any rebuild; one server at a time on m910q, stopped when done.

## Board

| Ticket | State | Where |
|---|---|---|
| T1 T2 T3 T4 T5 T6 T7 T8 T9 T10 T11 T12 T15 | CLOSED, merged, worktrees removed | tip `788136e2` on `origin/yulon-phase8b` (suite 3973 green at `55e0c894`, on m910q) |
| **T13** (dml_uninvite verifies the master) | CLOSED: code half `1a446392`, live half `99269c8b` (Opus hand on `yulon-ubuntu2`, one cold evidence review, two sentences closed by the lead's hand) | folder `8.6-uninvite-contract-yulon-ubuntu2-2026-09-10/`; the free guard in the Lua; parties staged by a harness since no human master exists on the box |
| **T14 / T19** (Apply pending database updates, Adopt as imported) | CLOSED; the owner's m910q install adopted on the 10th and the updates applied through the button: code half `ed035e41`, live half merged `1f44a995` (one Opus evidence review, REWORK on five README sentences, closed by the lead's hand in `9fc5bc54`); worktree and branch removed | the press refused on the owner's marker-less install; T19 carries both findings (the dead `populated`+complete arm, the cancel note on this route) |
| T16 (chosen level does not hold at join) | OPEN, waits for T13 to merge (shares `party.py`) and the box | spec on the ticket; live measurement first |
| T17 (8.8 Steam on a fullscreen-Steam stand-in) | BLOCKED on the owner: a Steam login for `yulon-arch`, or a captured `shortcuts.vdf` | spec on the ticket |
| T18 (deploy `playerbots.conf` so a spec can take effect) | CLOSED, merged `c7c3f8d4` (Opus hand, live only, no code; one evidence review, closed by the lead's hand) | `8.6-spec-takes-effect-yulon-ubuntu2-2026-09-10/`; the deployed conf left on the box; T27 filed (a conf activation never recommends a restart) |

**Owed Codex passes: all six run on 2026-09-10** (T7 r2, T12, T3 r4, T11, T8 r3, T14 code), each recorded on its ticket. Findings became T20 (paused world passes the direct-SQL guard), T22 (WotLK password repair defaults the scheme), T23 (clause-35 runner's two fail-open paths, blocked on the box), T24 (the engine route re-runs DDL under a running world), T25 (rebuild marker prefix; touched before recreate); T14 code's finding was already closed at the tip. T21 (a failed group read after an uninvite reads as removed) came from T13's review. Every merged hand branch is deleted.

**Afternoon of the 10th:** T20, T22, T21, T24 worked by Sonnet hands and MERGED (Codex reviewer each; T21 two rounds, T24 one, both with a last sentence closed by the lead's hand); T25 MERGED (two rounds, the boundary closed by the lead's hand); T19's second half MERGED `e2a305c8` (two rounds, the last finding closed by the lead's hand); T19's first half (teach the probe), T23 and T26 open; T13's live half MERGED `99269c8b`; T23 MERGED `eb7c8932` (one round, evidence review ACCEPT); T19 redirected to an adopt press on the owner's word (three probe rounds kept unmerged as `record/t19-probe-unmerged`), its unit half MERGED `a62dd763` and the m910q press MERGED `bfe353e6` (adopted and updated, T19 CLOSED); T26 measured, specced, its unit half MERGED `235ed010`; Steam installed on `yulon-arch` for T17, at the sign-in screen (`bot add` needs a real non-bot master session; a bridge script plus a client seat; the friends route is the module's account-links table); the index on m910q dropped; `yulon-win11-gate` could not be moved off D: (its 149 GB chain fits no healthy volume). The Hyper-V host came back at 12:36 (a clean shutdown at 17:39 on the 9th, not a crash); `yulon-ubuntu2` is up with the world running, `yulon-fedora` up. The host's disks: D: (all VMs' disk files) retried I/O on 9/2, 9/7, 9/8 and is quiet since the boot; the NVMe boot SSD throws a 17-event bad-block burst after each boot (9/4, 9/10) — memory `vm-host-disk-health`.

## Checklist and records

Phase 8: **27 of 29** boxes. 8.7a ticked 2026-09-09 (`8dad8eda`). 8.6 open with an honest record (dismiss-all proved through the panel; chosen spec unreachable until the app deploys `playerbots.conf` — T18; chosen level sent at join is accepted and not written — T16). 8.8 open (T17). `CHANGELOG.md` Unreleased carries every merged feature with its gate folder; the lead titles it at release. PR **DadsMmoLab #146** (`yulon-phase8b` → `Yulon`) is a **draft** by the owner's order until the exit pass closes; its body is regenerated from `<scratchpad>/lead/pr146-new-body.md` and refreshed after each merge (`gh pr edit 146 --repo DadsMmoLab/dads-mmo-lab --body-file …`).

## Boxes, as last left

- `yulon-ubuntu2` (WotLK, 100.99.204.5): world up pid 538052 (500/500 bots), auth up, realm row `100.99.204.5|100.99.204.5|255.255.255.0`; accounts `101 YULON_243C46E3, 102 PERZI, 103 YULONADMIN` (YULONPANEL removed by the lead 2026-09-09 ~14:05 via SOAP); `~/t7` worktree and `~/t7-gate-out/world-six-tables-before.sql` (7 MB) left for a reviewer, removable. Owner's things untouched (PERZI/Pakka, `LootPet2.lua` under `~/wowserver/env/dist/etc/modules/lua_scripts/`, `Logger.ALE` at `worldserver.conf:706`). SOAP admin account YULONADMIN on loopback 7878 (the owner knows the password; it is not written here).
- `m910q` (Tortoise, the owner's install): **every container exited** (as the owner left it before the 9th); `guild_bank_money.money` still `int(11)`; `idx_owner_bot_event` present. No press writes there without the owner's word.
- `yulon-fedora`: the gate box. `yulon-arch`: healthy, candidate for T17.

## The morning report

Artifact "Phase 8 Closing Night": https://claude.ai/code/artifact/85d5b01e-a039-4160-8fcb-7c845cf8778f — still the night-before version; the 2026-09-09 daytime section is NOT in it yet. How to build it and what to add: `<scratchpad 5c3f610a…>/lead/REPORT-HOWTO.md` (template `closing-night.tmpl.html` + `shots-morning/*.png` in the old scratchpad `e6db1900…`, `{{IMG:name}}` → data URI, publish with the Artifact tool to the URL above). Frames to add: T5's `panel-5/6/9`, `client-4`; T7's three renderings; T3's `failrestore/restore.log:14,17,21`; T14's three widget grabs.

## Open questions for the owner -- ANSWERED 2026-09-10 18:26 CEST (through the question tool)

1. m910q marker: **teach the probe** (T19's first half goes ahead: completeness on a populated, unmarked install from per-schema table counts; no write to the owner's database).
2. 8.6: **not ticked**. New requirement: My Party must let the player choose an existing character to join as a bot -- their own alts, characters on another account, friends' and family's characters (the AzerothCore playerbots alt-bot route; WotLK only). Filed as **T26**. 8.6 waits on T16, T18 and T26.
3. 8.8: the owner gives a **throwaway Steam account later**; T17 stays blocked until then.
4. Release: **upstream chooses the version when Baerthe merges the PR**; no tag or release is cut here. Step 8 becomes: mark PR 146 ready when the exit pass closes.
5. `idx_owner_bot_event` on m910q: **drop it now** (the lead, world stopped, announced, recorded under `pyplan/gates/m910q-index-drop-2026-09-10/`).
6. DML VM: no action pending; still needs an explicit yes per action.
Also: the report goes out **without the login card**; `yulon-win11-gate`'s disk files move from D: to W: (the lead, on the host); T13's live half and T23 run on `yulon-ubuntu2` autonomously, frames saved for the report.

## Scratch paths (Windows; `/mnt/c/...` from WSL)

- This session's lead scripts: `C:\Users\perzi\AppData\Local\Temp\claude\C--Users-perzi-dads-mmo-lab\5c3f610a-f81d-40c7-8610-23867cdf66fb\scratchpad\lead\` — `gate-merge.sh <merge-sha> "<label>"` (suite behind a merge, push on green), `t*_merge.py` (the pattern for ticket bookkeeping), `pr146-new-body.md`, `REPORT-HOWTO.md`.
- The old scratchpad `…\e6db1900-f1cd-4099-995e-e1ef6bec1004\scratchpad\`: the report template, images, `morning-questions-2026-09-09.md`.
- Memory: `C:\Users\perzi\.claude\projects\C--Users-perzi-dads-mmo-lab\memory\` (copy the folder to the WSL project dir `~/.claude/projects/-mnt-c-Users-perzi-dads-mmo-lab/memory/`).
