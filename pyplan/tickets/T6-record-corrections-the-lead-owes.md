# T6 — Four record corrections the lead owes, each found by a reviewer on another ticket

**Status:** ACCEPTED and MERGED (lead, 2026-09-09 13:15) -- merge `9e959102`, lead's close beside it, suite green behind it
**Filed:** 2026-09-09 11:10 by the lead (Fable), from T1's eight reviews
**Hand:** Opus, worktree branched from `yulon-phase8b` (ff-merge `origin/yulon-phase8b` first; report the sha — expected `d70642c6` or later)
**File set (yours alone):** `pyplan/checklist.md` (line 2495 only), `pyplan/gates/8.6-panel-live-yulon-ubuntu2-2026-09-09/README.md`, `pyplan/gates/7.10-rerun-ubuntu2-2026-09-08/README.md`. No other file; no other line of the checklist.
**Box:** none.

## Why

T1's reviewers, across four rounds, named four things that are false or missing in the record and belonged to no ticket. Each is small. Together they are the same class of defect T1 existed to repair: a sentence that was true the hour it was written and is read as true today. This project's own lesson is that an incomplete artefact reads as fact.

## The four, with the fact behind each

1. **`pyplan/checklist.md:2495` — 8.2e's opening clause is false on both halves.** It says *"no SOAP exists on this core and none is added; the entry declares the attach console and the tab says so instead of offering a set-up button."* The fork re-added SOAP on 2026-09-07 (`src/mangosd/MaNGOSsoap.cpp`, `src/mangosd/soap/`, commit `3f9a062` — see `pyplan/archive/tortoise-upstream-switch.md`); the catalog pin moved to `3a8472e` on 2026-09-08 (`2da4c516`); the entry became a SOAP entry the same day (`c7e577c7`: `operations.channel = "soap"`, port 7878, rank 4). The set-up button now exists on this entry — `test_the_soap_trees_keep_their_button` (`pylauncher/tests/test_controller_view.py`, find its current line) asserts it — and `CONSOLE_CHANNEL_SENTENCE` (`controller_view.py`, find the line) is reached by no shipped entry at all. The 2026-09-07 press was true when made; the subject changed under it. **Do not untick 8.2e and do not delete its record.** Append a dated correction in the style T1's line 2508 now uses: what was true on 2026-09-07, what changed on 09-07/09-08 with the shas, where the SOAP press lives (`pyplan/gates/tortoise-soap-yulon-arch-2026-09-09/`, a fresh install; and the startup-rank fact that folder proves), and what the console sentence now describes (a core the catalog no longer ships). Read the exit review's correction 6 whole (`pyplan/phase8-exit-review-2026-09-09.md`, the "Present-tense sentences" section, item 6) — it has the wording and the citations; verify each line number on today's tree before you write it, because two of that review's other line numbers were already found stale.
2. **`8.6-panel-live-…/README.md` — the VM's clock.** The sibling page `8.6-wotlk-yulon-ubuntu2-2026-09-09/part-2-party.md:3` says *"the VM's clock is UTC"*. Measured by the lead on the box at 10:34 local on 2026-09-09: `date` → `CEST`, `timedatectl` → `Europe/Oslo (+0200)`, `docker exec ac-worldserver date` → `UTC`. So the VM shell is +0200 and every container is UTC; every `…Z` timestamp the record cites is a container's. Add that as a dated paragraph to the panel-live README's corrections (it already corrects `part-2-party.md:188`); do not edit the historical page.
3. **`8.6-panel-live-…/README.md` — no tree sha.** The folder records no commit sha for the code that was pressed (its lane was cut off). The nearest is `daeae0a5` (03:38 +0200, the surface's last commit before the 06:00 press; a dozen commits sit between, none touching `party_panel.py` or `controller_view.py` — verify with `git log --oneline daeae0a5..d70642c6 -- pylauncher/yulon/ui/widgets/party_panel.py pylauncher/yulon/ui/controller_view.py`). Say so in the README, as a record, not as the press's sha.
4. **`7.10-rerun-ubuntu2-2026-09-08/README.md` — a date inside a folder named for the day before.** Its `run.log:1` opens `2026-09-09T00:34:39+02:00` in a folder named `…-2026-09-08`. Add one dated sentence to that README saying the run started after midnight local on the 9th, the folder is named for the evening it was staged, and (read `run.log` and the README's own timeline first) which timestamps are local and which are UTC. Do not rename the folder — the checklist cites it by name.

## Rules

- Every sentence you add is dated and in the past tense where it records, present tense only where it states a still-true fact you verified today. Every citation resolves on your tree; where the exit review's line numbers are stale, cite the resolving line and name the symbol.
- Do not tick, untick or reflow any checklist line other than 2495's own sentence(s).
- Gate: `YULON_TEST_BOX=yulon-fedora bash /c/Users/perzi/run-tests-vm.sh --checks` from your worktree's `pylauncher/` (announce on `yulon-fedora` first); ALL GREEN.
- One commit, `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` only; no push; the ticket file is not yours to edit.

## Report format (final message)

`## Report` — sha, gate last line, diff stat, the four items each with the line you wrote and what you verified for it, deviations flagged, status DONE.

## Report (hand, 2026-09-09 11:34)

- sha `f04d8a9d` on `worktree-agent-aaf6e031fb23c16e4`, ff-merged from `528f219e`; gate ALL GREEN, 3769 passed; diff: `checklist.md | 2 +-`, `7.10-rerun-ubuntu2-2026-09-08/README.md | 20 +`, `8.6-panel-live-…/README.md | 21 +`.
- Item 1: dated correction appended to `:2495`; four of the exit review's line numbers were stale and are cited at the resolving line with the symbol (`test_the_soap_trees_keep_their_button` 4266→4372, `_attach_only` 4227→4333, `CONSOLE_CHANNEL_SENTENCE` 1491→1552, `_is_console_channel` 1506→1567); the tick, the 2026-09-07 evidence and the mutation reasoning stand; the rank-at-startup fact cited from the arch folder.
- Item 2: VM shell +0200, containers UTC, the bare `[HH:MM:SS]` panel stamps are VM-local, `client-agent.log` is a third clock (the host) not measured.
- Item 3: `daeae0a5` recorded as the nearest commit, `git log daeae0a5..528f219e -- party_panel.py controller_view.py` empty across 39 commits; not the press's sha.
- Item 4: dates stated on both clocks; the run and its staging commit (`d8c08cb5`, 00:13 +0200 = 22:13Z on the 8th) both fall on 2026-09-08 in UTC; no reason given for the name.
- Deviations: the ticket's "named for the evening it was staged" was unsupported (the staging commit is also after local midnight) -- the hand declined to give a reason; "a dozen commits" measured as 39; the host clock named rather than an offset invented.

## Review 1 (Codex adversarial, 2026-09-09 12:00) -- REWORK, three citations

- `8.6-panel-live-.../README.md:76-78` generalises one container measurement (`ac-worldserver`) to "every container"; narrow it to the measured one or measure the others.
- `.../README.md:79-80` cites `8.7a-...ubuntu2-.../README.md:8` for the offset quote; it is on line 9.
- `7.10-rerun-ubuntu2-2026-09-08/README.md:49-50` cites the panel-live README's "last bullet" for the clock, but this same commit appended the no-sha bullet after it; use a stable line citation, and do not repeat the universal.
Fable verdict pending; the rejection body carries both.

## Review 2 (cold Fable reviewer, 2026-09-09 12:06) -- REWORK, four

1. `7.10-rerun-ubuntu2-2026-09-08/README.md:36` "twenty-five minutes after local midnight" -- `run.log:1` is 00:34:39, thirty-four.
2. `.../README.md:50-51` cites the panel-live README's "last bullet" for the clock; after this same commit the last bullet is the no-sha paragraph.
3. `8.6-panel-live-.../README.md:79` cites `8.7a-...ubuntu2-.../README.md:8` for the offset quote; it is line 9.
4. `.../README.md:77-78` "every `...Z` timestamp the 8.6 record cites is a container's reading" is broader than measured: `8.6-wotlk-yulon-ubuntu-2026-09-08/` (the original box, never measured) has gate-script banners in Z. Narrow to the ubuntu2 pages' Docker `started=` stamps.
Notes: the 7.10 README's list of bare local times reads exhaustive and is not (`~00:26` at :61, `00:03:26` at :285); curly apostrophes in the quoted commit subject on 2495; "Owed separately ... premise dissolved" is a judgement and the exit line still carries the old framing (the exit line's owner); the hand's four deviations all sound.

## Rejection (lead, round 1) -- the union of both reviews, all in your three files

1. `7.10-rerun-ubuntu2-2026-09-08/README.md:36` -- thirty-four minutes, not twenty-five.
2. `.../README.md:50-51` -- replace "last bullet" with a stable citation to the clock bullet's line in the panel-live README; do not repeat the "every container" universal there either.
3. `8.6-panel-live-.../README.md:79` -- `8.7a-...ubuntu2-.../README.md:9`.
4. `.../README.md:76-78` -- narrow both universals: "`ac-worldserver` runs UTC" is what was measured (one container); and "every Z timestamp the 8.6 record cites" becomes the ubuntu2 pages' Docker `started=` stamps, naming `part-2-party.md:26`, `part-2-bridge-absent.md:29,33`; say the 2026-09-08 folder's Z banners are gate-script stamps on a box never measured.
5. Optional: the bare-local-times list at :46 says "among them" or lists :61 and :285 too; straight apostrophes in the quoted subject on 2495.
Amend the single commit, re-run the gate, report.

## Report, round 2 (hand, 2026-09-09 12:48)

- sha `256c52a6` (amended, base `528f219e`); gate ALL GREEN, 3769 passed; diff `checklist.md | 2 +-`, `7.10-… README | 24 +`, `panel-live README | 29 +`.
- 1: thirty-four minutes (verified `run.log:1`). 2: "last bullet" -> `README.md:74-94` anchored by the bullet's opening words; the universal removed there too. 3: `:9`. 4: both universals narrowed -- `ac-worldserver` the one container asked, no other measured; the Z stamps settled are the ubuntu2 pages' Docker `started=` readings (`part-2-party.md:26`, `part-2-bridge-absent.md:29,33`), and the 09-08 folder's Z banners are script stamps on the original, unmeasured box. 5: the bare-times list names `00:03:26`, `~00:26`, `00:31:32` too; six curly apostrophes on 2495 made straight, the quoted subject byte-identical to `2da4c516`'s.
- Deviation: "no longer exists" replaced with "OffCritical since its host disk left the bus" (self-caught); cross-file citations anchored by quoted words as well as numbers.

## Review 3 (Codex adversarial, round 2, 2026-09-09 13:05) -- REWORK, one classification

The universals, the :9 pointer, the stable clock-bullet citation, the checklist scope and the 8.2e tick are all corrected. One replacement sentence overclaims: panel-live README.md:83-86 calls the 2026-09-08 folder's Z stamps "the gate script's own banners", but README.md:3 there is authored prose and 4-console.txt:2 is a marker line, not a banner. Say "script-emitted and README-authored Z stamps on an unmeasured box", or restrict the phrase to the banner lines and classify the others separately. Fable round-2 verdict pending; if it is line-level too, the owner's rule (close by hand after two consecutive wording-only rounds) applies and the lead makes the edit at merge.

## Review 4 (cold Fable reviewer, round 2, 2026-09-09 13:08) -- ACCEPT

All five items verified on the committed tree, every citation resolved (the range 74-94 holds exactly the clock bullet; `:9`; the three Docker `started=` stamps; the 09-08 folder's Z lines), the self-caught "OffCritical" change confirmed in five other records, the checklist a pure append on 2495 with 8.2e still ticked. Notes: `T1-tick-8.6.md:125` still carried the universal the hand narrowed (the lead's file -- fixed at the merge); "gate script's own banners" read as fair for the marker line.

## Closed by hand (lead, 2026-09-09 13:15)

Fable ACCEPT, Codex REWORK on one phrase; two consecutive wording-only rounds is the owner's close-by-hand rule. Merged `256c52a6` (`9e959102`); the phrase made exact with Codex's wording ("script-emitted or README-authored, not container readings"); the same universal narrowed on T1's ticket. Suite behind the merge green. Hand retired, worktree removed, branch deleted.
