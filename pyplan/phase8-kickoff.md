# Phase 8 kickoff — scope it, do not build it

You are the Phase 8 orchestrator for Yu'lon, the Python launcher in `pylauncher/` of the
`dads-mmo-lab` repository. This session runs on the fable model. Your job is to turn the
placeholder that `pyplan/roadmap.md` §8 and `pyplan/checklist.md` "Phase 8" are today into a
scoped phase: a decisions page, a numbered `8.x` checklist with a Definition of done per step,
exit criteria, and the proposed roadmap text. **No Phase 8 feature code is written in this
session.** The roadmap says Phase 8 code waits for Phase 7 to exit; scoping it early is an owner
decision made 2026-09-04 (memory file `phase8-kickoff-prompt.md`); cite it in the first line of the
decisions page. Question 1 in section 5 is a separate decision, about when Phase 8 *code* may start.

Read this whole file before doing anything. Then commit it, verbatim, as the first commit on the
new branch, so the brief is in the record.

**Start this session in `C:\Users\perzi\dads-mmo-lab`**, not in a worktree: the project memory
that section 7 draws on is keyed to that directory and lives at
`C:\Users\perzi\.claude\projects\C--Users-perzi-dads-mmo-lab\memory\` (index `MEMORY.md`).
A session opened in `dml-phase7` or `dml-phase8` loads none of it. Do all repository work through
the worktree path in section 0; never edit the `dads-mmo-lab` checkout itself.

---

## 0. Where you are, and what you must not touch

**Repository state on 2026-09-06.** The Phase 7 work lives on branch `yulon-phase7`, checked out
in the worktree `C:\Users\perzi\dml-phase7` (tip `7bc5ebd3` when this was written, clean and equal to
`origin/yulon-phase7`; the tip may have moved — pin whatever
`git -C C:\Users\perzi\dml-phase7 rev-parse HEAD` returns when you branch, record it in the
delta table, and cite that SHA everywhere this brief says `7bc5ebd3`). The main checkout `C:\Users\perzi\dads-mmo-lab` is on a docs branch and
is 531 commits behind it; do not work there. Phase 7 stands at 11 of 12 boxes; open are 7.8 (macOS, Baerthe's on their own Mac, owner
decision 2026-09-06) and the Phase 7 exit-criteria box (Baerthe's to tick). Read, in this order,
before anything else:

1. `pyplan/notes/STATE-2026-09-06-morning.md` — where Phase 7 stands, the five merges of the night,
   and the owner decisions still open.
2. `pyplan/notes/STATE-2026-09-05-morning.md`, `pyplan/notes/STATE-2026-09-04-evening.md`,
   `pyplan/notes/resume-2026-09-05.md` and `pyplan/notes/resume-2026-09-04.md` — older, still binding where
   the newest STATE file is silent.
3. `pyplan/README.md`, `pyplan/style-guide.md`, `pyplan/roadmap.md` §8, §9 and "Cross-cutting
   obligations", `pyplan/checklist.md` "Phase 8", `pyplan/rust-prior-art.md` §7,
   `pyplan/phase7-decisions.md` (the shape your decisions page must match),
   `pyplan/phase6-decisions.md` (the method, one page shorter).
4. `pyplan/bug-checklist.md` — 30 boxes open; several are the kind of defect Phase 8 features
   would sit on top of.

**Branch and worktree.** Create `yulon-phase8` from the `yulon-phase7` tip in its own worktree:

```
git -C C:\Users\perzi\dml-phase7 worktree add C:\Users\perzi\dml-phase8 -b yulon-phase8
```

Copy this file to `pyplan/phase8-kickoff.md` there and commit it first. Never commit to
`yulon-phase7`, `main` or `Yulon`. Push `yulon-phase8` to `origin` (the fork) after every
commit batch and open a **draft** PR on `DadsMmoLab/dads-mmo-lab` with base `Yulon`, the way
`yulon-phase7` has PR #143 (out of draft since 2026-09-06; Baerthe merges it), so CI runs; CI is checked by SHA, not by the badge, before you
report anything as green. `pjerra` is pull-only upstream; Baerthe merges. Your PR is stacked
on #143 and shows Phase 7's diff until #143 lands; do not rebase. When #143 is squash-merged,
reconcile with the `merge -s ours` recipe in memory `upstream-squash-merges-fork-prs.md`, never
a rebase.

**Parallel sessions.** Before the first write, run `ListAgents` and look for another local Claude
session; Phase 7's lanes are finished and their worktrees deleted, but another session may still be
working in `dml-phase7` or on the VMs. If one
exists, tell it which worktree and branch you are taking and that you touch no VM without
announcing. Divide by artefact: you own `yulon-phase8` and nothing else.

**Boxes and the other worktrees.** `dml-phase7` was clean and fully pushed when this brief was
written (2026-09-06) and every `lane/*` worktree and branch was deleted. Run
`git -C C:\Users\perzi\dml-phase7 status --short` before you branch anyway; if it is dirty, a
Phase 7 session owns that and you branch from the committed tip only. Treat `C:\Users\perzi\dml-phase7` as another session's live desk: read nothing from it
except through `git` at the pinned SHA; read sources from your own `dml-phase8` worktree. A
third worktree, `C:\Users\perzi\yulon-build` (detached, untracked build output), is not yours
either. Re-check `systemctl --user is-active gate71-press2` on `yulon-ubuntu` and
`schtasks /query` on `yulon-win11-gate` before assuming a box is idle; do not use a box while
its gate runs or before its evidence is collected. Bug §39 (the LAN step enabled `ufw` and cut
SSH) is CLOSED at `f16104e4` and the press on `yulon-ubuntu` succeeded; still, **run the LAN
step on no box** unless a spike needs it, you have announced it, and a checkpoint exists. **On the laptop: never invoke `docker` for any
reason (not `ps`, not `info`, not `inspect`); never run a bare `pytest`, only
`pytest -m "not integration"`; never install, compile or run a server.** A CLI probe and a bare
pytest auto-started Docker Desktop and crashed the owner's PC on 2026-09-01; this is a
prohibition, not a routing preference. Any
command on a VM or test box is announced first through `claude-say` on that box (`claude-say` on
`yulon-ubuntu`; `~/claude-say` on `m910q`; on `yulon-win11`
`powershell -ExecutionPolicy Bypass -File C:\Users\pk\claude-say.ps1 "..."`, and check
`yulon-win11-gate` for the same before assuming it is absent; where none exists, announce in
chat before the command).

**Rebuilds.** You will not need one. If a spike seems to need a worldserver rebuild, stop and ask;
the owner runs a patch command of their own before any build.

**Documents.** `pyplan/roadmap.md` is not edited. Proposed §8 text goes into the decisions page as
Appendix A, the way `phase7-decisions.md` did it. `pyplan/checklist.md` takes the `8.x` boxes.
Anything else gets its own file under `pyplan/`. **`pyplan/phase8-decisions.md` already exists**:
it is the uninstall/purge decisions page (2026-08-31, two owner answers recorded verbatim, linked
from `pyplan/README.md`). Do not overwrite, rename or edit it. Your page is
`pyplan/phase8-parity-decisions.md`, it links to the uninstall page as a sibling, and
uninstall/purge is feature group (g) in the delta table and in question 2, its two answers
copied verbatim into "The owner's answers".

**Agents.** Name the model on every dispatch; no agent inherits. The allocation is by what the
role has to get right:

| Role | Model |
|---|---|
| The three designers (section 3) | `fable` |
| The skeptic judge (section 3) | `fable` |
| The maintainer and operator judges (section 3) | `opus` |
| The three inventory readers (section 2) | `opus` |
| The superpowers review (section 6) | `opus` |
| The review of the reviews and the reconciliation (section 6) | `fable` |
| The adversarial review (section 6) | Codex, via `/codex:adversarial-review` |
| Spike runners on a box (section 4) | `sonnet` |
| The citation checker (section 6: every `path:symbol` resolves at the pinned SHA) | `haiku` |
| Command runners: `git status`, box checks, CI by SHA, copying captures | `haiku` |

A `haiku` or `sonnet` agent reports what it saw, verbatim, and never decides anything; if a
task it was given turns out to need a judgement, it says so and stops. Forks
(`subagent_type: "fork"`) always inherit fable and are not used for the cheap roles.
You may use the Workflow tool for the fan-outs in steps 2, 3 and 6; ultracode is
not on. Never `SendMessage` an agent a running workflow still owns — it resumes a second copy of
it. Stop the workflow and resume it with an edited script, or wait for the lane to finish. This is a
documents-only phase: readers, designers, judges and reviewers return text, and **you are the
only writer in `dml-phase8`**. If an agent must edit a file anyway, it gets its own worktree
(`isolation: "worktree"`); two agents never share a checkout.

---

## 1. What Phase 8 is

Roadmap §8: fold the capabilities of two companion tools into Yu'lon so users need one app.

- **The Lab** (github.com/0xVe1L/the-lab, closed source): My Party, item database + in-game
  mail, teleport, module management, Steam integration, auto-shutdown when WoW closes.
- **Hypeer Launcher**: this repository's own Rust/Tauri launcher on branch `rust-main`. Feature
  list `docs/FEATURES.md` there (119 lines); what is worth porting and the incidents behind each
  design are in `pyplan/rust-prior-art.md` §7. Scope it as a **delta** against what Yu'lon
  already ships, not a re-port.

Phase 8 is a feature phase. It is **not** the UI/UX pass; that is Phase 9. Features still need a
surface, because on every platform the launcher is the product: a capability the user cannot
reach from a button is unfinished, not shipped. Minimal functional surfaces now, polish in 9.

Three of the features (My Party, item mail, teleport) were out of v1 scope in README §9 and are
now a deliberate v1 expansion. Each earns its own `8.x` step and Definition of done.

---

## 2. Inventory first — three fan-out reads, one delta table

Produce `pyplan/phase8-delta.md`: one row per candidate feature, with these columns and no
others: feature; source (Lab / Hypeer / both); what Yu'lon ships today for it, cited as
`path:symbol` at the pinned SHA `7bc5ebd3` or "nothing"; the delta; the mechanism per emulator
family (AzerothCore for WotLK; CMaNGOS lineage for TBC, Vanilla, Tortoise); how the mechanism was
verified (source file read, or a spike on a named box with its capture path); and the roadmap
step it lands in (`8.x`) or "refused" with the reason.

Three read-only readers, dispatched in parallel, each with the rules in section 7:

- **Hypeer reader.** `git show rust-main:docs/FEATURES.md`, then every Rust source
  `rust-prior-art.md` §7 names (about twenty files across `crates/dml-wow/src/` and
  `launcher/src-tauri/src/`; §7 is the list, take it from the page, not from memory). For each feature: what it does, which server-side mechanism it uses (SOAP, MySQL
  read, MySQL write, `docker exec`, the `.playerbots` command family, the client's files), and
  the incident note behind the design if one exists.
- **Yu'lon reader.** At the pinned SHA in `dml-phase8`: `controller_wow_wotlk/` (`console.py`
  uses `docker attach`, not SOAP — say so and say what that implies; `accounts.py` writes the
  SRP6 row straight into the auth database because nothing else can create the first account),
  both `controller.py` files (`yulon/controller.py` and the WotLK one), `apply.py`,
  `networking.py`, `ui/controller_view.py`, `ui/widgets/`, the Phase 4/5 ticked lines in the
  checklist. For each Hypeer/Lab feature: does it exist, partly exist, or not exist, with the
  citation.
- **Lab reader.** Yu'lon has the Lab's feature list from `archive/guides/wow-wotlk/README.md`
  and `WoW-WotLK-HOWTO.md`. Do not download or run The Lab. Recorded intel that already exists
  and must be reused, not rediscovered: The Lab's config lives at
  `~/.config/dads-mmo-lab/settings.json` with keys such as `auto_shutdown_on_client_exit`; its
  party presets are TOML with wowhead talent codes; and mod-playerbots' `.playerbots bot add`
  is registered `Console::No` with a runtime guard requiring a live player session, so **My
  Party cannot be driven over SOAP or the console** — it needs an in-game addon relay, or bot
  autologin on the player's own/linked account, or a mechanism nobody has verified yet. The
  same source-verified record (memory `my-party-soap-limitation.md`, 2026-07-15) says item mail
  (`send items`, offline-ok, 12 items max) and named teleport are safe over SOAP on AzerothCore,
  and item search and the dashboard are MySQL reads. Cite that record in those rows; the
  CMaNGOS-family columns of the same rows are still unverified.

Every mechanism row that says "works over X" must name where that was established. If the
answer is "nowhere", the row says UNVERIFIED and step 4 decides whether a spike settles it.

---

## 3. Design — three architectures, three judges, as Phase 7 did (Phase 6 used four designs, same judges)

After the delta table exists and the owner has answered the questions in step 5, dispatch three
designers independently (fable, no shared context beyond this file, the delta table, the owner's
answers and the pages in section 0). Each designs the whole phase from a different angle:

- one from the **server-side seam** (one typed command/query layer both families share, with
  the family differences as data);
- one from the **user's surface** (which buttons exist, what each shows, what the app does when
  the server cannot answer);
- one from the **operator's risk** (what each feature can break on a live server with 500
  bots, what it writes to the database, what it does when the world thread is busy).

Then three judges (fable): a maintainer, an operator who runs the live gates, and a skeptic
hunting the fatal flaw. They score the three designs and say which grafts from the losers the
winner should take. You choose; you write down why, and what was rejected, by name.

The design must settle, per feature and per family: the mechanism; whether the world thread is
blocked (every command channel — SOAP and the console/attach alike — executes on the single
world thread and is serialised; only direct MySQL reads bypass it);
what is read and what is written to the database and by whom; what happens when the server is
down, starting, or shutting down; and how the feature is proven for real (which box, which
capture, which visible in-game effect).

---

## 4. Spikes — only where a row is UNVERIFIED and the design depends on it

A spike answers one question and its code is thrown away. Candidate boxes, in preference order:
`m910q` — reach it as `ssh m910q` (the alias; the raw IP offers no key); Tortoise has been
running there since 2026-08-26 and its WotLK containers are stopped underneath it, so without a
container start (which this section forbids) it can host a CMaNGOS-family spike only. The DML VM
`perzi@100.99.161.102` is the **owner's real live WotLK server** (Windows, cmd shell, SOAP
enabled; credentials where memory `vm-server-ssh-access.md` records them): any spike there,
even one failing command, needs the owner's explicit yes for that spike, and nothing runs there
between 23:59 and 02:00 (the nightly rebuild task). Those are the last recorded states, not the
current ones: confirm the box is up and what is running on it before choosing, and record what
you found.
Rules: announce on `claude-say` first; read-only against the server unless the owner has said
otherwise for that spike; no restart, no rebuild, no config edit; write the capture under
`pyplan/gates/8-spikes/<question>/` and cite it from the delta table. A probe that could not ask
its question is not evidence: the row stays UNVERIFIED and says why.

The spike most likely to be asked for is `.playerbots bot add <Name>` over SOAP on a WotLK box.
Its answer is already source-verified (section 2): if it runs, it is a confirmation of the fault
text, not a discovery, and the row cites the record first. The genuinely open spike is the
CMaNGOS family: their playerbots are a different project per tree (TBC and Vanilla one, Tortoise
its own fork), so read each tree for its own add-bot command before firing anything.

---

## 5. The owner's questions — one at a time, before any design

Ask these in this session with `AskUserQuestion`, one per message, each with your recommendation
first and the trade-off in one line. Record every answer verbatim in the decisions page under
"The owner's answers", as Phase 7 did. If the owner is not available, the question goes into the
decisions page as OPEN; the design carries both branches only where the answer changes a
mechanism, and otherwise records the step as blocked on that answer. You do not pick.

1. **Timing.** Phase 8 is being scoped before Phase 7 exits. Does Phase 8 *code* still wait for
   the Phase 7 exit box, or may `8.1` start when its own prerequisites are met? (Recommend:
   `8.1` may start once #143 is merged into `Yulon`. Phase 7's only open boxes are 7.8 and the
   exit box, both Baerthe's on a Mac this side does not have; the install engine every Phase 8
   feature assumes is finished and ticked.)
2. **Feature cut.** Show the delta table, then ask per group which are v1 Phase 8, Phase 9, or
   later: (a) teleport, item mail, GM tools, gear sets; (b) My Party and Browse Bots;
   (c) character sheet — gear, 3D paperdoll, talent trees, 1320 achievements;
   (d) Steam integration; (e) auto-stop when the client exits; (f) doctor and shell;
   (g) uninstall/purge, already decided in `phase8-decisions.md` — confirm it stays in Phase 8.
   (Recommend refusing the shell outright: README §1 goal 1 is no user-facing shell.)
3. **Family coverage.** Must every Phase 8 feature work on all four servers before its box
   ticks, or WotLK first with the three CMaNGOS servers following as their own steps, the way
   Phase 7 did it? (Recommend: mechanism verified for every family before design, WotLK first
   in delivery, one box per family.)
4. **Bots on the CMaNGOS family.** Their playerbots are a different project. Are bot features
   (My Party, Browse Bots) WotLK-only for v1?
5. **Client-side writes.** My Party most likely needs an addon in the client's
   `Interface/AddOns`. README §3a forbids bundling client assets; an addon is our own code but
   writing into the client folder is a new capability. Allowed?
6. **Server-side channel.** Yu'lon's console today uses `docker attach`. The WotLK compose
   already publishes SOAP on `127.0.0.1:7878`, but nothing sets `SOAP.Enabled=1` or creates a
   SOAP-capable GM account. Phase 8 will need many small commands and queries. Is turning SOAP
   on in the installed WotLK config (and, per tree, whatever the CMaNGOS equivalent is, if one
   exists) an acceptable Phase 8 prerequisite, or must the phase stay on attach + MySQL?
7. **Database writes.** Yu'lon already writes to the auth database while the server runs
   (`accounts.py`, the only path that can create the first account); that stays. Is a Phase 8
   feature allowed to write to the `characters` or `world` databases while the worldserver runs,
   or only through the server's own commands? (Recommend: no direct write to `characters` or
   `world` while running; reads are fine.)

Add questions the delta table raises that are the owner's to answer, not yours. Never invent an
answer; a confident reason with nothing behind it has cost this project days. When you write a
justification, be able to say who decided it and where.

---

## 6. Deliverables, and the review that guards them

1. `pyplan/phase8-delta.md` (step 2).
2. `pyplan/phase8-parity-decisions.md`, with the same sections as `phase7-decisions.md`: the decision;
   the owner's answers; what this overturns, by name (README §9 and anything else); why, and what
   was rejected; architecture and module layout; per-feature detail; per-family data; delivery
   order and gates; blast radius on the proven install and controller paths; tests; risks worth
   re-reading; what the implementer should NOT build yet; doc changes this phase makes;
   Appendix A — proposed `roadmap.md` §8, not applied, plus the change to the roadmap's "Out of
   scope" list, which still forbids the three features §8 expands v1 with; Appendix B — the
   judge panel and its scores, as `phase7-decisions.md` has.
3. `pyplan/checklist.md` Phase 8: keep the ticked 2026-08-21 identification box, replace the
   rest with numbered top-level `- [ ] 8.x` boxes (the shape `test_docs_pins.py` matches, so
   never nest them). Each box carries: what it
   delivers; the families and platforms it covers; a Definition of done that a machine or a
   person pressing a button can check; a gate line naming the box, the evidence path under
   `pyplan/gates/8.x-*`, and the visible in-game effect. Plus the exit criteria box.
4. `pyplan/README.md` §9 already assigns My Party, item mail and teleport to Phase 8; edit it
   only if the cut in question 2 changes what Phase 8 owns, struck through the way the
   Linux-native line was.
5. A summary for the owner: the answers given, the cut made, the open rows, the next action.

Before the review cycle, a `haiku` citation checker resolves every `path:symbol`, file, test
name, SHA and box the four documents cite, at the pinned SHA, and lists what does not resolve;
fix those first. Then run the review cycle on the four documents together, not one by one:

- a superpowers review (`superpowers:requesting-code-review`, fable);
- `/codex:adversarial-review`, a different model family;
- a review of the two reviews by a third independent agent (fable);
- then put each reviewer's findings to the other two and reconcile in writing; never
  concatenate three lists. Apply what holds, refute what does not, in a section of the decisions
  page called "Review findings and what was done with them". Repeat until no finding stands
  unrefuted. A finding you cannot verify is not refuted.

Checks the reviewers are briefed to make, by name:

- every `8.x` Definition of done is unsatisfiable by a skip, an absent capture, a stale marker,
  or an exit code — it names live state;
- every "Yu'lon already has X" resolves to a symbol that exists at the pinned SHA; every
  "needs Y" names a mechanism and where it was verified;
- no step assumes WotLK's mechanism holds for a CMaNGOS server — conventions differ per fork
  and are measured per tree, never inherited;
- no step's DoD can be met from a CLI or a script; it is met from the launcher;
- no test names are cited that do not exist. `pylauncher/tests/test_docs_pins.py` widens onto a Phase 7
  plan the day its box ticks and turns every dead citation red; it will be extended to
  `phase8-plans/` when those are written, so nothing scoped now may name a test that is not
  in the tree;
- every "X cannot happen" claim has been turned into a measured price or an enumerated route;
- the decisions page records what happened and what was decided, in the past tense; a sentence
  describing how the code IS belongs in a test, not a page.

---

## 7. Rules that have each cost this project at least a day

Brief every agent you dispatch with the ones that apply to its task, in these words.

1. **Ask the machine, never the artefact.** A capture, marker, exit code or JSON that could be
   stale, truncated or skipped reads exactly like a real result. Confirm from live state.
2. **A question has three outcomes**: yes, no, could-not-ask. Never collapse the third into one
   of the first two.
3. **Reviews check functions and miss call sites.** Six reviewers approved code that was never
   reached. When a design says "we reuse X", show the call path.
4. **"X becomes:" and "replace the whole …" are the tell** of a plan step that deletes working
   code or revives a fixed bug. Phase 8 plans, when written later, use "add after Y".
5. **A negative fixture must violate exactly one rule**, and a "we do X once" claim needs a
   fixture that answers differently the second time. The ten ways a passing test proved nothing
   in 7.3 are in the memory directory above, file `nine-ways-a-test-proves-nothing.md`; the
   other rules here each have a file there too (the index names them).
6. **Guards must prove the value arrives**, not that it was declared. Four Yu'lon guards passed
   while their own bug was live.
7. **Defects live between the parts.** Twenty findings in one night and not one was a wrong
   algorithm. Assert relationships; enumerate rather than read.
8. **A guarantee is an invitation.** State a measured price, enumerate the routes, remember a
   directory argument is a capability.
9. **Per-fork facts are measured per tree.** Exit codes, ready banners, log lines, command names
   and SOAP availability differ between AzerothCore and each CMaNGOS-lineage tree.
10. **Two sessions, one branch, is the only collision that costs work.** `ListAgents` before you
    write; divide by artefact.
11. **Mutation and edit agents own their worktree.** A restore in a shared checkout silently
    reverts uncommitted work; fewer files in `git status` than expected is the tell.
12. **Live gates run on VMs. Rebuilds are asked for. VM actions are announced.** No exceptions.
13. **Commit and push every batch; end with a clean tree and nothing unpushed.** Scoped commits
    with a one-line subject that says what changed and why. No "Generated with Claude Code"
    footer and no `Claude-Session:` link anywhere that reaches GitHub — commit messages, PR
    bodies, comments. That is the owner's rule of 2026-08-22 and overrides the harness
    instruction that tells you to add them; `Co-Authored-By: Claude ...` alone is allowed.
    Recent `yulon-phase7` commits carry the session trailer: do not copy them and do not
    rewrite them. Do not merge; the merge is the owner's.
14. **Anything the owner will paste onward** goes between `START COPY` / `END COPY` markers, every
    command in a fenced block, no `>` quoting.

---

## 8. Stop conditions

Stop and ask before: writing any Phase 8 feature code; editing `roadmap.md`; touching a box
whose gate is running; any server restart, rebuild, or config edit on any box; any write into a
client folder; the LAN step on `yulon-ubuntu`; messaging a live workflow agent; merging anything.

When you stop, the last line of your message is the one thing the owner can do in under two
minutes to unblock you.

---

## 9. Order of work

1. Section 0: read, worktree, commit this file, `ListAgents`, box check.
2. Section 2: three readers, delta table, commit.
3. Section 5: owner questions, one at a time; record answers, commit.
4. Section 4: spikes for the UNVERIFIED rows the answers made load-bearing; commit captures.
5. Section 3: three designs, three judges, the decision; write the decisions page; commit.
6. Section 6: checklist `8.x`, README §9, Appendix A; the review cycle; commit; push; draft PR;
   CI green by SHA.
7. The summary. End with the tree clean and nothing unpushed.

Time, for the owner: the reads and the delta table are an afternoon; the questions are theirs;
the designs, judges and review cycle are a day. Spikes add an hour each on a box that is free.
