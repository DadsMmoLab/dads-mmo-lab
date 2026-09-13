# Phase 8 exit line — judged, 2026-09-09

The line is `pyplan/checklist.md:2508`. This page reads every clause of it against the state of the
tree, says MET / NOT MET / MET WITH A NAMED GAP with the file and line that proves each, and ends
with either the tick text or the shortest list of what blocks it.

**No machine was driven and no code was changed for this review.** It is a reading of the tree, the
gate folders and `origin/rust-main`. The one command it ran is its own `--checks` gate, below.

---

## The ground, read before anything was judged

Three separate things get called "the tip" in tonight's material, and the difference decides which
evidence counts. Read rather than assumed:

| | commit | what it holds |
| --- | --- | --- |
| `origin/yulon-phase8b` | **`d8ad7275`** | this review's own worktree, fast-forwarded onto it |
| local `yulon-phase8b` | **`069db9dc`** | the same, plus this run's four lane merges — **not pushed** |
| the sha the 7.9/7.10 lanes cloned | `96a129dc` | an ancestor of both |

`git merge-base --is-ancestor 96a129dc d8ad7275` → 0, so the regression lanes measured a tree that
`origin/yulon-phase8b` contains. **`5a7e8baa` — the rebuild lane's ready-wait fix — is NOT an
ancestor of `d8ad7275`** (`--is-ancestor` → 1). It exists only on local `yulon-phase8b` and on
`worktree-wf_9fadba02-fe6-1`. Every clause below that depends on that fix is graded against the
local branch and says so, because a fix nobody has pushed is not on the tip anyone else can read.

`git diff --stat d8ad7275 yulon-phase8b` is **150 files, +13647/−14**, and `pyplan/checklist.md` is
**not one of them** — the four lanes wrote evidence and code and left the ticking alone, as they
were told to. So the tick state is identical on both branches: **8.6, 8.7a and 8.8 unticked; the
exit line unticked.**

### This review's own gate

From this worktree's `pylauncher/`, at `d8ad7275`:

```
YULON_TEST_BOX=yulon-fedora bash /c/Users/perzi/run-tests-vm.sh --checks
  3679 passed, 6 skipped in 26.18s
  mypy (this platform / as Windows / as macOS): no issues found in 93 source files
  ruff: All checks passed!   black: 193 files would be left unchanged
=== --checks: ALL GREEN ===
```

Announced on `yulon-fedora` first. That reading is what clause 5 rests on.

---

## The box inventory

| box | tick | evidence folder | note |
| --- | --- | --- | --- |
| 8.1a–d | `[x]` | `8.1a-wotlk-yulon-ubuntu-2026-09-06/` (+ this run's `2026-09-09-wotlk-three-strike-press/`), and the b/c/d folders | 8.1a's owed WotLK three-strike press ran tonight |
| 8.2a–e | `[x]` | five folders | 8.2e's subject has changed under it — see the corrections |
| 8.3a–d | `[x]` | four folders | 8.3d ticked with the fork's defect recorded; that defect is now fixed upstream |
| 8.4a–d | `[x]` | four folders | |
| 8.5a–d | `[x]` | four folders | |
| **8.6** | `[ ]` | `8.6-wotlk-yulon-ubuntu2-2026-09-09/` + `rebuild-live-yulon-ubuntu2-2026-09-09/` (local branch only) | all four questions answered; **no widget** |
| **8.7a** | `[ ]` | `8.7a-wotlk-yulon-ubuntu-2026-09-08/` (4 of 5) + `8.7a-wotlk-yulon-ubuntu2-2026-09-09/` (the 5th, local branch only) | five of five now have a press |
| 8.7b–d | `[x]` | three folders | |
| **8.8** | `[ ]` | none | an assignment, not a gate — neither Deck is reachable from this side |
| 8.9a–b | `[x]` | two folders | both tick with the last hop of a definition-of-done clause unproved, and both say so |

---

## Clause by clause

### 1. "every `8.x` box above ticked with the evidence its gate line names" — **NOT MET**

Three boxes are unticked at `pyplan/checklist.md:2500` (8.6), `:2501` (8.7a) and `:2505` (8.8).

* **8.6** and **8.7a** now have a press for every clause they were missing, on the local branch.
  8.6's four questions are answered in `pyplan/gates/8.6-wotlk-yulon-ubuntu2-2026-09-09/part-1-rebuild.md`
  and `part-2-party.md`; 8.7a's fifth clause in
  `pyplan/gates/8.7a-wotlk-yulon-ubuntu2-2026-09-09/README.md`. Ticking them is the owner's
  session's job and this review does not do it. Whoever does must know that **the evidence is not
  on `origin/yulon-phase8b`** — a tick citing those folders on the pushed branch cites nothing.
* **8.8** has no evidence and no box that can produce it. Its own line already says why: the gate is
  "a Steam Deck running SteamOS — Baerthe's or DaddyCool's (owner answer 13, 2026-09-06)", and
  neither is reachable from this side. That is an assignment the owner makes, not a lane's work.
  See the correction to its prerequisite sentence below — the format it says is unrecorded is
  recorded, in full, on `origin/rust-main`.
* **"the evidence its gate line names"** is the half of this clause that will not be literally
  satisfiable for 8.6 and 8.7a. Their gate lines name `yulon-ubuntu` and folders
  `8.6-wotlk-yulon-ubuntu-<date>/` and `8.7a-wotlk-yulon-ubuntu-<date>/`; the presses happened on
  `yulon-ubuntu2`, in folders named for it. Either the gate lines get corrected as part of the tick
  or the tick has to name the deviation. It is a naming gap, not a substantive one, and it is listed
  under the corrections.

### 2. "no definition of done satisfied by a skip, an absent capture, a stale marker or an exit code" — **MET WITH A NAMED GAP** (three gaps)

The audit rule this clause exists for held up well tonight: every lane read its ground first, and two
of them refused a step whose result was its start state. `pyplan/gates/8.7a-wotlk-yulon-ubuntu2-2026-09-09/README.md`
lists "WHICH ASSERTIONS ARE ALREADY TRUE HERE (so that pressing them would prove nothing)" and then
made the value **wrong** before pressing it. `pyplan/gates/7.10-rerun-ubuntu2-2026-09-08/README.md`
reports `network_apply('lan')`'s realmlist half as *"what the app said it did and not as evidence
that the row was written"*, because the row already held the value. Both are the rule applied, not
recited.

The three gaps:

**(a) One of the two SQL routes owner answer 7 is about is guarded by a sentence, not a press.**
8.7a's clause *"a module whose SQL targets the world database is refused while the server runs"*
passed on 2026-09-08 (`pyplan/gates/8.7a-wotlk-yulon-ubuntu-2026-09-08/11-cycle2-up.log:41-56`,
`3-refused-world-up.png`) — and the refusal it caught reads *"Running the importer over the modules
installed here… ac-worldserver, ac-authserver are running"*. That is the `applied_by: "db-import"`
route. The other route is `applied_by: "direct"`, which reaches `Applier._run_sql`
(`pylauncher/yulon/apply.py:1855-1868`), and `pyplan/write-ledger.md:67-68` says of both of its write
sites: **"yes, and unguarded"**. Enumerated rather than assumed: across all four games' shipped
manifests there is **exactly one** `direct` SQL step, `pylauncher/manifests/wow-wotlk/modules/mod-arac.json`,
and its `db` is `world`. So the one shipped module that writes the world database out from under a
running worldserver was never pressed against a guard, and the ledger's own promise —
`pyplan/write-ledger.md:49-50`, *"8.7 is where the applier's guard lands"* — is half kept.

**(b) 8.9a and 8.9b both tick with the last hop of a definition-of-done clause unproved.** Their
shared clause is *"with the box ticked, a reinstall to the same folder finds the characters on the
login screen"*, and both lines say the last hop is not proved (`checklist.md:2506`: *"the last hop of
the ticked clause is a rebuilt server drawing those characters on a real login screen; what was
proved is the volume, its password and the rows"*; `:2507` repeats it). Honestly recorded on the
boxes and **not carried by the exit line**, which is what makes it a gap here rather than there.

**(c) The rollback's RESTORE arm has never been pressed.** `pyplan/gates/rebuild-live-yulon-ubuntu2-2026-09-09/README.md`
says so plainly: *"The rollback's **restore** path was not pressed: nothing this lane did produced a
build that failed to come up."* `_keep_rollback` and `_let_go` were pressed live, three times, and
read by a second process from outside (`press2-rollback-watch.log`, absent → 4 during the compile →
0 after). The restore half is unit-tested through the machine double only. Owner answer 2's second
sentence — *"restore it automatically if the world does not come up"* — is therefore the half of
that feature nothing has demonstrated.

### 3. "no capability reachable only from a command line or a script" — **NOT MET**

**My Party has no widget.** `pyplan/gates/8.6-wotlk-yulon-ubuntu2-2026-09-09/part-2-party.md` names
it under *"What was NOT proved here"*: *"**The Qt widget for My Party does not exist.** What was
built is the seam: `party.InstallParty`, the `MyPartySeam` protocol, and `ControllerServices.my_party`
wired in `_for_wotlk` … Drawing the tab is not in this lane and is not claimed."*

Verified against the merged branch rather than taken on trust: `MyPartySeam` is declared at
`pylauncher/yulon/ui/controller_view.py:133` and `ControllerServices.my_party` at `:441`, wired at
`:923` — and no `QPushButton` or `addTab` in that file mentions party. **Those line numbers are on
local `yulon-phase8b`; at `origin/yulon-phase8b` the strings `my_party` and `MyPartySeam` do not
appear in `pylauncher/yulon/` at all.** So on the pushed tip My Party has neither a widget nor a
seam, and on the merged one it has a seam and no widget. Every press in the 8.6
evidence went through `gate86b.py`, a gate script. That is the exact shape this clause forbids, on
the one box whose whole subject is a new capability.

The lesser sibling, named so it is not confused with the blocker: the **Rebuild** button exists and
is wired (`controller_view.py:1587` `REBUILD_BUTTON_LABEL`, `:4220-4221` `clicked.connect(self.rebuild_server)`),
and 8.7a's UI half clicked a real button for the module-SQL route
(`pyplan/gates/8.7a-ui-yulon-ubuntu-2026-09-08/README.md`) — but the three live rebuilds were driven
at the seam by `rebuild_gate.py`, not by the button. The capability is reachable from the surface;
this run did not press it there.

### 4. "every value still marked unverified in the catalog's operations block replaced by a measured one, and that set enumerated by name in the catalog test rather than left to memory" — **MET WITH A NAMED GAP**, and the clause's own premise is refuted

Three findings, and they need to be read in order.

**The marker this clause names does not exist and never did.**
`pyplan/phase8-designs/d-catalog-provenance.md:32-36`, measured at `dcc64543`: *"the string
`unverified` appears **nowhere** in `yulon/catalog/` — not in the data, not in a model, not in a
description. So the exit line's 'still marked unverified' names a marker **that does not exist**, and
the set it asks to be enumerated could not be computed by anything."* Re-checked here at `d8ad7275`:
`grep -c unverified pylauncher/yulon/catalog/catalog.json` → **0**. So the clause as written cannot
be met by anyone, and its first half — "replaced by a measured one" — has no set to range over.

**The enumeration half was built, and it is green.** `pylauncher/tests/catalog_provenance.py` holds
the table (`PROVENANCE` at `:105`, `OWED` at `:333`) and
`pylauncher/tests/test_catalog_invariants.py` holds five tests over it — the rule at `:1067`, the
exit line's own clause at `:1110` (`test_the_provenance_debts_are_exactly_these_four`), the citation
resolver at `:1134`, the shape check at `:1154` and the inherited-kind rule at `:1173`. All five are
inside tonight's 3679 passed. The rule is anti-vacuous by construction: the walk is asserted
non-empty and asserted to reach all four games **before** any rule runs, and the exact count of 39 is
asserted **last** on purpose (`test_catalog_invariants.py:1096-1107`).

**But it covers two blocks, and neither is the one the clause names.**
`catalog_provenance.py:65` — `BLOCKS = ("play", "accounts")`, with the reason at `:65-73`:
*"`operations` and `observability` are the other two and are NOT marked."* The clause says
*"the catalog's **operations** block"*. The design counted the cost of closing that:
`d-catalog-provenance.md:163-168` — `operations` + `observability` hold **64** written-down leaves
between them, so all four blocks is **103** against tonight's 39.

**And four values are still owed, not replaced.** `test_the_provenance_debts_are_exactly_these_four`
pins them by name: `wow-wotlk:play.mail_item_cap`, `wow-wotlk:accounts.level.max_level`,
`wow-tbc:accounts.level.max_level`, `wow-tortoise:accounts.scheme`. Each row says what would settle
it, and none of the four is settled. Three are ceilings nothing asked the tree to refuse; the fourth
is a real 2026-08-26 measurement whose reading predates `pyplan/gates/` and so cannot be cited.

**The gap, stated as one sentence:** the clause asks for a set that its own marker could not produce,
and what exists in its place enumerates a *different* set of blocks with four debts open. Which of
those to accept is an owner decision, and the design page already lists it as such
(`d-catalog-provenance.md:240-248`, four open questions). It is not a lane's to decide and this
review does not decide it.

### 5. "the write-ledger test green with every write site in the package enumerated" — **MET**

Green in this review's own gate run, at `d8ad7275`, on `yulon-fedora`. The test fails in both
directions, which is what makes "every" mean something: `test_every_write_in_the_package_is_in_the_ledger`
(`pylauncher/tests/test_write_ledger.py:242`) and
`test_every_ledger_row_still_resolves_to_a_write_in_the_package` (`:254`), plus
`test_every_ledger_row_says_what_it_writes_and_whether_the_world_may_be_up` (`:264`) so a row cannot
be filled in with a blank. `pyplan/write-ledger.md` carries 100 rows.

Checked for the one way this could be green on the pushed branch and red on the merged one: the four
lanes touched `party.py` (+681), `apply.py` (+15), `catalog/native.py` (+42) and `controller_view.py`
(+43), and `git diff d8ad7275 yulon-phase8b` over those four files adds **zero** write-call
spellings the walker recognises. My Party's one write site is already on the table —
`party.py::deploy::shutil.copy2`, `pyplan/write-ledger.md:149`, marked "**new (8.6)**". So the clause
holds on the merged tip too, on the evidence available without merging.

The two "**yes, and unguarded**" rows are gap (a) of clause 2, not a defect in this clause. The test
is green because the ledger is honest about them.

### 6. "Phase 7's controller-surface gate and cross-server regression pass re-run green on the merged tip" — **NOT MET**, and the failure is not a Phase 8 regression

**The controller-surface half is MET.** `pyplan/gates/7.9-rerun-m910q-2026-09-08/README.md`:
`gate-79-controller-surface.py` at `96a129dc`, run unmodified against all three CMaNGOS games on
`m910q` — **33 checks, 33 OK, 0 FAIL**, plus 18 more from the photograph pass. The ground was read
before the first game was touched (`ground-start.txt`: every game container `Exited`, none of
3724/3443/8085/8888/8129 listening), so each game's "all three up" is `start_staged()`'s work and not
the state the run began in. It also found something the gate cannot see:
`tbc-mangosd` exits **139** on a clean `stop_staged()` — a CMaNGOS-TBC object-teardown assertion,
and `79-tbc-stop-abort.log` separates it from the 2026-09-04 mechanism by timestamp (`tbc-db`
finished 0.3 s *after* the worldserver, so the database was not taken away).

**The regression half is NOT MET.** `pyplan/gates/7.10-rerun-ubuntu2-2026-09-08/README.md`: 53
checks, **52 OK, 1 FAIL**, exit 134.

#### I must correct the orchestrator's framing before weighing it

The brief handed to this review says the regression lane's two FAILED clauses *"both trace to the
realm address on `yulon-ubuntu2`"*. **Only one does.** The lane's own evidence says otherwise, and I
verified the mechanism in this worktree rather than reading it off the README:

* **The gate-79 ready-wait clause is the address one, and the brief is right about it.**
  `docker.azerothcore_ready()` builds the auth marker as `f"{realm_host}:{realm_port}"`; the
  authserver prints that pair only in `Added realm "<name>" at <address>:<port>.`, and both halves
  come out of `acore_auth.realmlist`. `ready-marker-probe.txt` is A-against-B on one server:
  `wait_server_ready("127.0.0.1", 3724)` still waiting after **8 m 19 s**, killed;
  `wait_server_ready("172.30.48.189", 8085)` **ready in 0.3 s**. That is a ready-wait defect exposed
  by an address the box does advertise, exactly as the brief frames it.
* **The 7.10 GREEN clause failed on something else entirely.** The single FAIL is
  `widget-run.log:109` — `[FAIL] the follow stopped after the click -- 11 lines had arrived first`.
  It is the LogPanel Stop defect, and it has nothing to do with the realm address.

#### The 7.10 failure, verified independently here

Three readings taken in this worktree, not copied from the lane:

1. `git diff cfb4c04f d8ad7275 -- pylauncher/yulon/ui/widgets/log_panel.py` is **empty**. The file
   has not changed between the 2026-09-05 run's tree and this one, so the defect is **not** a Phase 8
   regression. It is a live defect on the merged tip that eleven weeks of runs never asked the right
   question of, because the check's verdict was decided by how chatty the server happened to be.
2. `pylauncher/yulon/ui/widgets/log_panel.py:81` — `for text in self._source():` — can only notice
   `self._stop` **between** lines, so a follow blocked on a quiet worldserver stays blocked. That is
   the mechanism, and the probe measured both arms on the same panel seconds apart: a synthetic
   source stopped **0.02 s** after the click; `docker logs -f ac-worldserver` never stopped in 120 s.
3. The one log-following call site passes no cancel event:
   `pylauncher/yulon/ui/controller_view.py:3064` —
   `self.console_log.run(self.services.logs_source, title="worldserver log")`.

**And a correction to the lane, in the same spirit it corrected the brief.** Its README says
*"`LogPanel.run()` has the parameter that fixes this"*. The parameter is necessary and **not
sufficient**. `LogPanel.stop()` sets the event (`log_panel.py:355-356`), but the source has to be
able to see it, and `runner.stream()` (`pylauncher/yulon/runner.py:256-258`) takes no cancel argument
and blocks in a readline over the child's stdout. Passing `cancel=` at `controller_view.py:3064`
changes nothing until something can interrupt or kill that child. **The shortest honest fix is not
one line**, and a tick that promises one would be wrong.

**Corroboration the lane did not have, found while judging.** The same abort is already on the
record, inside a *ticked-clause* gate log, unremarked: the last two lines of
`pyplan/gates/8.7a-wotlk-yulon-ubuntu-2026-09-08/11-cycle2-up.log` are
`QThread: Destroyed while thread '' is still running` and `timeout: the monitored command dumped
core`. So the defect has been photographed twice before tonight and named once.

**What it costs a user, which is why this blocks rather than annotates:** press Stop on the Console
tab's log follow while the world is quiet and the button appears dead — the header keeps counting —
until the server prints again; close the app in that window and it aborts on the way out. The Console
tab is a Phase 8 surface (8.2a–e), so this is not a Phase 7 artefact sitting in a corner.

### Carve-out 1 — Tortoise's reason on native Windows (owner answer 10's second half) — **NOT MET, the owner's to close or waive — and its subject may have dissolved**

The line already says this one has no box, because no gate box runs Tortoise on Windows. That is
unchanged: no such press exists.

**What has changed is whether the carve-out still describes anything.** It is about *"the reason
shown on native Windows where Tortoise has no pseudo-terminal"* — a reason that exists only because
Tortoise's command channel was the attach console. It is not. `pylauncher/yulon/catalog/catalog.json`
now gives `wow-tortoise.operations` `channel: "soap"`, port 7878, `gm_level: 4` and an `enable_conf`
setting `SOAP.Enabled = 1` — since `c7e577c7` (2026-09-08, *"Tortoise joins the SOAP trees"*). A tree
whose channel is SOAP needs no pseudo-terminal, so there may be no reason left to show. **Whether
this carve-out is closed, waived or re-scoped is an owner question**, and this review does not answer
it — but it must not be carried forward as though its premise still held.

### Carve-out 2 — the macOS halves of 8.1d, 8.2e, 8.3d (and 8.4d, 8.5d, 8.7d when they tick) — **NOT MET, correctly carved out**

No macOS machine exists on this side; owner answer 10 allows Tortoise there and nothing can press it.
The 2026-09-08 audit's correction to this line — that three ticked boxes were delegating to a
container the line did not have — is in place and accurate. Unchanged tonight, and nothing in this
run moves it.

---

## Present-tense sentences in the ticked `8.x` lines that today's facts have made false

The rule this section exists for: *a comment about how the code IS goes stale; one recording what
happened or was measured stays true forever.* Every entry below is a present-tense claim in a line
somebody will read as current fact. The corrections are offered for the owner's session to apply;
this review does not edit `checklist.md`.

**1. `checklist.md:2470` — the WotLK box.** *"`yulon-ubuntu` (the Hyper-V VM holding the finished 7.2
WotLK install, Off at baseline) is the WotLK box."*
**False.** That VM's host disk left the Hyper-V host's SATA bus on 2026-09-08; it is `OffCritical`
and its checkpoints went with the disk.
**Correction:** the WotLK box is **`yulon-ubuntu2`** (12 vCPU, 16 GB), carrying a fresh AzerothCore
WotLK install made through the app's own engine on 2026-09-08 and rebuilt with `mod-ale` compiled in.
`yulon-ubuntu` is `OffCritical` and was not touched. **Its replacement kept the hostname
`yulon-ubuntu`**, which has already misled one artefact: `pyplan/gates/7.10-rerun-ubuntu2-2026-09-08/run.log`
reads `box : yulon-ubuntu` and its README has to warn about it. Anything reading a hostname to
identify the box will get this wrong.

**2. `checklist.md:2500` and `:2501` — the gate boxes and folder names for 8.6 and 8.7a.**
*"**Gate:** `yulon-ubuntu` … evidence `pyplan/gates/8.6-wotlk-yulon-ubuntu-<date>/`"* and
*"**Gate:** `yulon-ubuntu`; evidence `pyplan/gates/8.7a-wotlk-yulon-ubuntu-<date>/`"*.
**Unfillable as written.**
**Correction:** the presses ran on `yulon-ubuntu2`, and the folders are
`pyplan/gates/8.6-wotlk-yulon-ubuntu2-2026-09-09/`,
`pyplan/gates/8.7a-wotlk-yulon-ubuntu2-2026-09-09/` and
`pyplan/gates/rebuild-live-yulon-ubuntu2-2026-09-09/`. 8.7a's first four clauses keep their original
folder, `pyplan/gates/8.7a-wotlk-yulon-ubuntu-2026-09-08/`, on the box that no longer exists.

**3. `checklist.md:2500` — My Party's route.** *"**This route has never been recorded working**:
the one live note about it records it failing on 2026-08-20."*
**False as of 2026-09-09.**
**Correction:** the route works on this tree. After the rebuild, `dml_bridge_ping` answered
`DML-BRIDGE-READY` (`rebuild-live-yulon-ubuntu2-2026-09-09/7-ping.log`), and a mage bot named
`Jilsur` entered a real 3.3.5a client's party frame under `Pakka`, geared and specced, and was
dismissed (`8.6-wotlk-yulon-ubuntu2-2026-09-09/2-party-frame-with-bot.png`,
`3-party-frame-after-dismiss.png`, `part-2-party.md`). What changed the answer was the rebuild — the
2026-08-20 attempt and 8.6's repeat of it both failed for the reason 8.6 named, the engine not being
in the image. The sentence should become the past-tense record it already is: the route failed on
2026-08-20 and on 2026-09-08 for a measured reason, and answered on 2026-09-09 once the engine was
compiled in.

**4. `checklist.md:2500` — who runs the rebuild.** *"the applier does not act on a manifest's rebuild
flag today, so that rebuild is the owner's and is not automatic."*
**Half false.**
**Correction:** the applier still does not act on the flag — that part stands — but the app has its
own Rebuild control (`controller_view.py:1587`, `:4220-4221`) with the rollback owner answer 2 asked
for, and it was pressed live three times on 2026-09-08/09. The rebuild is no longer only the owner's
hand. What is still the owner's is the **decision** to press it, and the HARD RULE that no agent
starts one.

**5. `checklist.md:2501` — the rollback's status.** *"Unit-tested through the machine double only; no
live rebuild pressed it yet, and the database-dump half is a morning question."*
**False.**
**Correction:** three live presses on `yulon-ubuntu2`. `-rollback` tags were read **absent before →
four during the compile → none after**, by `watch_rollback.sh` polling `docker images` from **outside
the press**, so the press's own claim and the daemon's state are two independent readings
(`press2-rollback-watch.log`, `press2-rebuild.log`, `3-rollback-tags-exist-during-the-compile.png`,
`5-press2-rollback-kept-then-let-go.png`). Two things the presses added that the line should carry:
the **RESTORE arm is still unpressed**, and a measured limit on `_let_go` — a `-rollback` tag on the
two one-shot services (`ac-db-import`, `ac-client-data-init`) can survive it, because their **exited**
containers still reference the old image and `docker rmi` refuses ("must be forced"). "The rollback
names are gone afterwards" is true of the two long-running services and **not guaranteed** of the
one-shot ones.

**6. `checklist.md:2487` — 8.2e's opening clause.** *"no SOAP exists on this core and none is added;
the entry declares the attach console and the tab says so instead of offering a set-up button."*
**False on both halves, and this is the largest of the corrections.**
**Correction:** the fork re-added SOAP on 2026-09-07 — `src/mangosd/MaNGOSsoap.cpp`, `src/mangosd/soap/`,
commit `3f9a062` (`pyplan/archive/tortoise-upstream-switch.md`). **Tortoise's catalog pin then moved to
`3a8472e` on 2026-09-08** (`2da4c516`, *"Tortoise's pin moves to 3a8472e: the SOAP interface and the
account-lockout fix"*), and the entry became a SOAP entry the same day (`c7e577c7`):
`wow-tortoise.operations.channel = "soap"`, port 7878, rank 4, `SOAP.Enabled = 1`. The consequences
the line still asserts the opposite of:
* **The set-up button exists on this entry**, and the app's own test says so by name —
  `test_the_soap_trees_keep_their_button` (`pylauncher/tests/test_controller_view.py:4266`) asserts
  `enable_channel_button.isVisibleTo(...) is True` for `wow-tortoise`.
* **`CONSOLE_CHANNEL_SENTENCE`** (`controller_view.py:1491`) — *"This core has no remote command
  listener to turn on — it is built with neither SOAP nor the telnet console"* — is now reached by
  **no shipped entry at all**: all four games' `operations.channel` is `soap`, so
  `_is_console_channel()` (`:1506`) answers `False` for every one of them. The sentence is honest
  about a core that the catalog no longer describes, and the test that covers it now drives a
  synthetic `_attach_only()` fixture (`test_controller_view.py:4227`) rather than the real entry. The
  code and the tests were updated correctly; the checklist line was not.
* 8.2e's **"Visible effect: the console reply on the Server tab, and no set-up button anywhere on
  this entry"** describes an app that no longer ships.
* And its own **"Owed separately"** note — the native-Windows pseudo-terminal reason — is the
  carve-out whose premise dissolved with the same commit. See carve-out 1.

**7. `checklist.md:2491` — 8.3d's closing conditional.** *"they shipped a SOAP interface hours after
the owner's earlier message, so a fix may follow — and when it does, this box's password clause can
be re-run without changing a line of this app."*
**The condition has been satisfied**, so the sentence has stopped being a forecast and become an owed
action.
**Correction:** the account-lockout fix landed and **the catalog pin moved onto it** — `3a8472e` is
that fix (`2da4c516`; `pyplan/archive/tortoise-upstream-switch.md` names it *"the account-lockout fix
(`3a8472e`)"*). 8.3d's password clause is now owed a re-run against an install built at the new pin,
by the box's own words. Two further sentences in the same line go with it:
* *"correctly NEVER CREATED on a core whose channel is its console"* — that reason is gone; the
  entry's channel is SOAP. **Whether the app's own account should now be created on Tortoise is an
  open question, not a settled one**, and nothing in `phase8-decisions.md` or
  `phase8-parity-decisions.md` answers it. Named rather than decided.
* Every ticked Tortoise box (8.1d, 8.2e, 8.3d, 8.4d, 8.5d, 8.7d) was gated on `m910q` against
  `~/tortoise-server`, an install built **before** the pin moved — so their evidence is against a
  tree the catalog no longer names. **Stated precisely, because half of it was already answered and
  crediting the wrong half would be its own error.** `pylauncher/tests/test_tortoise_boot_facts.py:431-441`
  asserts the new pin **by value** with exactly the right argument — *"moving the pin means taking
  those measurements again, and this line is where that decision has to be written down rather than
  discovered on a four-hour install"* — and its message lists which measurements were re-taken at
  `3a8472e`: the ready banner, the migrations path, the SQL globs and the harmless bot-log line on a
  copy of `m910q`'s install (`pyplan/gates/tortoise-reimport-rehearsal-m910q-2026-09-08/`), and the
  exit status and bot conf on a fresh install on `yulon-arch`
  (`pyplan/gates/tortoise-fresh-yulon-arch-2026-09-08/`). Those are **boot** facts. The six ticked
  `8.x` boxes' facts — the account list and its marker, the level ceiling and rank column, the mail
  cap, the rename refusal, the module set — were **not** re-measured, and the guard that would ask
  cannot: `GATE_PINS` (`pylauncher/tests/test_catalog.py:220-235`) names three games and
  `wow-tortoise` is not one of them, by design (`:255-256`, *"pinned by `test_tortoise_boot_facts.py`,
  by value, with its own argument"*). So the question is open per-fact rather than wholesale, and
  owner answer 1's `tw_world` reimport at the baseline is the action that touches it. It has not run.

**8. `checklist.md:2479` — 8.1a's owed re-press.** *"the crash-loop stage of this gate is owed a
re-press … it ran on 2026-09-08 — on `m910q`'s TBC tree, not on this box."*
**Now discharged**, and the line should say so.
**Correction:** the WotLK press ran on 2026-09-09 on `yulon-ubuntu2` —
`pyplan/gates/8.1a-wotlk-yulon-ubuntu-2026-09-06/2026-09-09-wotlk-three-strike-press/`. One strike
reads `up`, three read `restart loop — 3 restarts` on the **first** probe after the third kill (zero
polls later, inside the "within two polls" bound), and `stage_d2`'s own recipe — the database taken
away, six polls at ten seconds — crossed at poll 1. The two frames are the same tab, the same
container and the same 22 s of uptime, forty-seven seconds apart, so nothing but the strike count
differs between them. It also carries its own correction of a false reading inside its transcript:
`QWidget.isVisible()` is `False` for every widget in an offscreen grab, so the gate's
`visible=False` lines say nothing about what was painted — `isEnabled()` and the picture are what
settle the interlock.

**9. `checklist.md:2505` — 8.8's prerequisite.** *"the shortcuts file format is recorded nowhere in
this repository and must be read from a real Steam profile before the writer is built."*
**False.** It is recorded, completely, on this project's own `origin/rust-main`, in
`docs/reference/dmlpack/dmlpack.py`:

| what | where |
| --- | --- |
| the type table and the fatal two-`END` terminator (*"A file missing that second byte makes Steam drop EVERY non-Steam shortcut"*) | `:243-251` |
| parser / serialiser / document dump | `:259-276`, `:279-290`, `:292-293` |
| `gen_appid` — crc32(exe+appname) with the high bit set, which is also the grid-artwork filename key, with *"Never rename"* | `:296-301` |
| finding the account: the profile that has a `shortcuts.vdf`, then most-recently-used | `:1463-1509` |
| `shortcuts.vdf` + `grid` paths from a userdata id | `:1511-1517` |
| **Steam-is-running refusal, re-checked at write time and not only in preflight** | `:1572-1580` |
| timestamped backup before the write | `:1631-1633` |
| round-trip re-parse **and** terminator check, refusing the write on either | `:1636-1643` |
| the same refusal as a preflight problem, in the user's words | `:1056-1062` |

**What the prerequisite is still right about**, so the correction does not overreach: a **real profile
read** is still needed for the userdata id on a Deck and for the **compatibility tool** mapping,
which `dmlpack.py` does not write — it treats Proton only as a `runtime_deps` presence check
(`:1048-1054`). And one behavioural difference the box must know before lifting this: `dmlpack.py` is
**add-only** (`:1592-1594`, *"already present -- left alone (add-only)"*), which satisfies 8.8's
*"a second press changes nothing"* but **not** its *"keyed so a second press replaces rather than
appends"*. The keying itself is free — `gen_appid` regenerates the identical appid for the same
name+exe, which is exactly the key 8.8 asks for.

---

## Where the plan is silent, and this review did not decide for it

Read before writing any verdict above, and none of these pages answers:

* **What "the catalog's operations block" means now that no `unverified` marker exists.**
  `phase8-decisions.md`, `phase8-parity-decisions.md` and the 2026-09-08 owner answers are silent;
  `d-catalog-provenance.md:240-248` puts the four questions to the owner and this review leaves them
  there. Clause 4 cannot be graded MET or NOT MET without one of those answers, which is why it is
  graded MET WITH A NAMED GAP and the gap is spelled out rather than resolved.
* **Whether carve-out 1 survives Tortoise gaining SOAP.** Nothing anywhere says what the
  native-Windows reason should read once the channel is not a pseudo-terminal. Named, not answered.
* **Whether the app's own account should be created on Tortoise now.** 8.3d's refusal rested on
  "a core whose channel is its console". That premise is gone and no page replaces it.
* **What "the server came back up" means for a rebuild.** The rebuild lane found this silence and
  answered it one way (the realm line, on this install's world port, `5a7e8baa`), naming it as a
  choice made in the silence. It stays a choice until a page carries it — and the fix is not on
  `origin/yulon-phase8b`.

---

## Verdict

**The Phase 8 exit line CANNOT be ticked.** Of its six clauses, **three are NOT MET** (1, 3 and 6),
two are MET WITH A NAMED GAP (2 and 4) and one is MET (5); both carve-outs stand open, and one of
them has lost its premise. No wording of the tick would be true tonight.

| clause | verdict |
| --- | --- |
| 1 every `8.x` box ticked with the evidence its gate line names | **NOT MET** |
| 2 no definition of done satisfied by a skip, an absent capture, a stale marker or an exit code | MET WITH A NAMED GAP (×3) |
| 3 no capability reachable only from a command line or a script | **NOT MET** |
| 4 the catalog's unverified operations values measured, and that set enumerated in the catalog test | MET WITH A NAMED GAP — premise refuted |
| 5 the write-ledger test green with every write site enumerated | **MET** |
| 6 Phase 7's controller-surface gate and cross-server regression pass re-run green on the merged tip | **NOT MET** (7.9 green, 7.10 52/53) |
| carve-out 1 Tortoise's reason on native Windows | NOT MET, owner's — and its subject may have dissolved |
| carve-out 2 the macOS halves | NOT MET, correctly carved out |

### The shortest list of what blocks it

Ordered so that each item is a thing somebody can do, with what settles it:

1. **8.8 has no evidence and no reachable box.** *(clause 1)* An owner assignment: name whose Deck
   runs it. `origin/rust-main:docs/reference/dmlpack/dmlpack.py` supplies the file format, so the
   prerequisite read shrinks to the userdata id and the compatibility-tool mapping.
2. **My Party is reachable only from a script.** *(clause 3)* A Qt surface over the
   `MyPartySeam`/`ControllerServices.my_party` that already exist, and a press through it. Until then
   8.6 cannot tick without violating the clause above it, whatever its gate folder holds.
3. **7.10's regression pass is 52 of 53, and the FAIL is live on the tip.** *(clause 6)* The
   LogPanel Stop defect: `log_panel.py:81` cannot see `_stop` while blocked, the one log-following
   call site (`controller_view.py:3064`) passes no cancel, **and** `runner.stream()`
   (`runner.py:256`) cannot honour one — so the fix has to reach the source, not just the call site.
   Then re-run 7.10 on the merged tip.
4. **`gate-79`'s ready wait cannot answer for an AzerothCore install.** *(clause 6)* Read the pair
   from `acore_auth.realmlist` instead of typing it; `pyplan/gates/7.10-rerun-ubuntu2-2026-09-08/ready_marker_probe.py`
   is that query already written. 7.9's own re-run is green **because** its three CMaNGOS rows take
   no realm arguments — the `wow-wotlk` row has never been exercised.
5. **8.6 and 8.7a are unticked, and their new evidence is not on `origin/yulon-phase8b`.**
   *(clause 1)* Merge and push the four lane branches, then tick with a citation that resolves.
6. **Clause 4's own premise needs an owner answer** before it can be graded at all: the marker it
   names does not exist, what was built covers `play`/`accounts` rather than `operations`, and four
   debts stand.
7. **The `applied_by: "direct"` SQL route is unguarded and unpressed.** *(clause 2, gap a)* One
   shipped manifest uses it (`mod-arac` → `world`). Either guard `Applier._run_sql` and press it
   while the world is up, or the exit line must carry the exception the way it carries its other two.
8. **The rollback's RESTORE arm has never been pressed.** *(clause 2, gap c)* Owner answer 2's second
   half. A rebuild deliberately made not to come up settles it; that is a rebuild, and per the HARD
   RULES it is the owner's to start.

### What is NOT blocking, and should not be argued again

* **The 7.10 failure is not a Phase 8 regression.** `git diff cfb4c04f d8ad7275 -- pylauncher/yulon/ui/widgets/log_panel.py`
  is empty; the call site is the same call at both commits. Nothing the base controller's stop path
  or the service assembly changed caused it. It is a defect the tip has always had, which an
  eleven-week run of chatty worldservers hid, and it appears in a 2026-09-08 gate log's last two
  lines unremarked. It blocks clause 6 because the clause says *green*, not because Phase 8 broke it.
* **The gate-79 ready-wait failure is an install-time marker defect exposed by an owner-side address
  change**, precisely as the orchestrator framed it — the harness types `127.0.0.1:3724` while the
  authserver advertises the machine's reachable address on the world port. The rebuild side of the
  same defect is already fixed at `5a7e8baa`.
* **The two carve-outs are the owner's, and neither has moved.** Carve-out 1's *subject* has moved,
  which is a question, not a blocker.

### If those close, the tick text to write

Offered so it does not have to be composed under pressure, and phrased in the past tense so it
cannot rot:

> **[x] Phase 8 exit criteria met** — closed 2026-__-__ at `<sha>`. Every `8.x` box ticked on its own
> gate folder, with 8.6 and 8.7a pressed on **`yulon-ubuntu2`** after `yulon-ubuntu`'s host disk
> failed on 2026-09-08 — the gate lines' box name is corrected in place, and the folders are
> `8.6-wotlk-yulon-ubuntu2-2026-09-09/`, `8.7a-wotlk-yulon-ubuntu2-2026-09-09/` and
> `rebuild-live-yulon-ubuntu2-2026-09-09/`. My Party reached a real surface and was pressed there;
> no Phase 8 capability is reachable only from a script. The catalog's per-tree values are
> enumerated by name in `pylauncher/tests/catalog_provenance.py` and guarded by five tests in
> `test_catalog_invariants.py` — over `play` and `accounts`, which is **not** the `operations` block
> this line originally named, because the `unverified` marker it named never existed (owner answer
> ____, and `pyplan/phase8-designs/d-catalog-provenance.md`); `OWED` stood at ____ debts on the day.
> The write-ledger test was green with all ____ write sites in `pyplan/write-ledger.md`, both
> directions asserted. Phase 7's controller-surface gate re-ran 33 of 33 on all three CMaNGOS games
> (`7.9-rerun-m910q-2026-09-08/`) and the cross-server regression pass re-ran ____ of ____
> (`7.10-rerun-.../`) once the LogPanel Stop defect and `gate-79`'s AzerothCore ready marker were
> fixed — the first was never a Phase 8 regression (`log_panel.py` byte-identical across
> `cfb4c04f..96a129dc`) and is recorded as a defect the tip had carried since before Phase 8.
> **Carve-outs, still named rather than hidden:** owner answer 10's second half on native Windows —
> re-scoped or waived on ____, Tortoise's channel having become SOAP at `c7e577c7`; and the macOS
> halves of 8.1d, 8.2e, 8.3d, 8.4d, 8.5d and 8.7d, where no macOS machine exists on this side.
> **A third, added by this review:** the `applied_by: "direct"` SQL route was ____.

Every `____` is a reading somebody must take on the day. A tick with one of them guessed is the shape
this whole round exists to prevent.

---

## What this review did not do

* **No screenshot was taken and none is owed.** Judging has no live clause; the deliverable is this
  page. Every picture cited above belongs to a lane's own folder, and where a lane's liveness record
  mattered — 8.6's four frames each logging `Wow.exe alive pid=19760`, the rebuild's
  `ac-worldserver running pid=… restarts=…` under every capture, the three-strike press's six
  `.State.Running true` — it is quoted rather than trusted.
* **`pyplan/checklist.md` was not edited.** Ticking, and applying the nine corrections above, is the
  owner's session's job. This page is written so each correction can be lifted verbatim.
* **No box was driven and no rebuild started.** The one remote action was this review's own
  `--checks` gate on `yulon-fedora`, announced first.
* **The four lane branches were not merged into this worktree.** Their content was read with
  `git show yulon-phase8b:<path>`, which is why every clause above says which branch its evidence
  is on.

---

## Errata (lead, 2026-09-09 10:35)

* **Correction 3 carried a wrong date.** It says the bridge "answered on 2026-09-09". The folder it rests on says otherwise: `pyplan/gates/rebuild-live-yulon-ubuntu2-2026-09-09/7-ping.log` is `DML-BRIDGE-READY` against a world started `2026-09-08T20:39:08Z`, and `8-ping-after-press2.log` against `2026-09-08T21:15:35Z` — 22:42 and 23:18 on **2026-09-08** in +0200. The folder is dated 09-09 because it was written after midnight. Found by the cold reviewer on ticket T1's third round, after two hands and one lead had copied the sentence forward. A folder's name is not a timestamp.
* **Correction 3 also said the cause was measured "both times".** The 2026-08-20 attempt's only record (`pyplan/phase8-reads/hypeer.md:619-623`) attributes it to a relative `ALE.ScriptPath` and measured nothing; the engine's absence was measured once, on 2026-09-08 (`8.6-wotlk-yulon-ubuntu-2026-09-08/README.md:40-52`).
* **Correction 4's line numbers** (`controller_view.py:1587`, `:4220-4221`) did not resolve on the tree they were written against; the control is `REBUILD_BUTTON_LABEL` at `:1648`, the button at `:4320-4321`.
