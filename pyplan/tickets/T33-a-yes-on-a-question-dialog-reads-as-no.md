# T33 — a Yes on a question dialog reads as No: the static `QMessageBox.question()` returns an `int`

**Status:** CLOSED 2026-09-11 22:54 CEST — merged `8dc5204a`, gate green on m910q, pushed
**Filed:** 2026-09-11 22:23 CEST by the lead (Fable), from the owner's report on Windows (pressing Install: "Install WoW WotLK into this new folder? … Yes/No" opens the folder picker whichever button is pressed) and the macOS user's "server rebuild does nothing". Both are one bug.
**Hand:** Sonnet. Worktree `.claude/worktrees/t33`, branch `hand-t33` from `yulon-phase8b`. Reviewer: Codex adversarial (or a cold Opus). Unit only; no box.

## Root cause (measured)

With PySide6 6.11.2 on Python 3.11 and 3.13 (the two CI legs), the **static** `QMessageBox.question(...)` returns a plain `int` — `16384` for Yes, `65536` for No — not a `QMessageBox.StandardButton` member. An instance's `standardButton(clickedButton())` still returns the member. Probe (`scratchpad/T33/static_probe.py`, a `QTimer` clicking the active modal's button under `QT_QPA_PLATFORM=offscreen`):

```
static question(), clicked Yes: returned 16384 type=int is_Yes=False is_not_Yes=True eq_Yes=True
static question(), clicked No:  returned 65536 type=int is_Yes=False is_not_Yes=True eq_Yes=False
```

So `answer is QMessageBox.StandardButton.Yes` is always False and `is not Yes` always True, on every platform running this PySide6. Six sites compare that way:

| Site | Effect of a Yes today |
|---|---|
| `catalog_view.py:136` `_qt_suggestion_asker` | the suggested folder is never taken; the picker opens on Yes and on No (the owner's report) |
| `controller_view.py:5311` `rebuild_server` | logged "declined at the confirmation", nothing starts (the macOS "rebuild does nothing") |
| `controller_view.py:5385` `apply_database_updates` | same |
| `controller_view.py:5452` `adopt_as_imported` | same |
| `catalog_view.py:677` "Adopt without checking?" | a Yes refuses |
| `catalog_view.py:900` "Restart Yu'lon now?" | a Yes never restarts |

Why nothing caught it: every test of these presses monkeypatches `QMessageBox.question` to return the **enum** member (`tests/test_catalog_view.py:1437…1628`, `tests/test_regroup.py:345…679`), and the live gates drove the presses directly through the seams. `==` holds for both the `int` and the member (`StandardButton` is an int-backed flag), which is the fix.

## Definition of done

1. One helper, `yulon/ui/answers.py` (or beside an existing dialog helper if one exists — say which): `def said_yes(answer: object) -> bool` returning `answer == QMessageBox.StandardButton.Yes`, with a docstring recording the measurement above in one paragraph (past tense: what was measured, on which versions). All six sites call it; no `is`/`is not` against a `StandardButton` remains in `yulon/` (a test greps for it: `test_no_identity_comparison_against_a_standard_button`).
2. Tests for the helper: `int(Yes)`, the `Yes` member, `int(No)`, `NoButton`, `0` — with the mutation `==` → `is`.
3. **A real-dialog test** for `_qt_suggestion_asker` (the one site that is a seam with a clean signature): under offscreen, a `QTimer` clicks Yes on the active modal `QMessageBox` and the asker must return True; clicks No and it must return False. This is the test that fails on today's code and would have caught the regression; keep it in `tests/test_catalog_view.py`. Use the probe's technique (`app.activeModalWidget()`, re-arm the timer until it is a `QMessageBox`); bound the wait so a broken run fails rather than hangs.
4. For the three controller confirmations and the two other catalog dialogs: one existing fake per site changed (or added) to return `int(QMessageBox.StandardButton.Yes)` — what the real static returns — and the press must proceed; the existing enum-returning fakes stay, so both shapes are covered. Mutation per site: revert the site to `is` — the int-returning test fails.
5. `--checks` ALL GREEN on m910q; black/ruff; every test with its named mutation; CHANGELOG line is the lead's; `write-ledger.md` untouched. Commit on `hand-t33`, trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>` alone, no push.

Report on the final message: sha, the helper's location, the six sites, each test with its mutation, the real-dialog test's timing, the gate's last line, deviations, Status.

## Reproduced on two boxes (lead, 2026-09-11 22:35 CEST)

The app's own `_qt_suggestion_asker()` with a `QTimer` pressing the real button: `yulon-arch` (display `:0`, xcb, Python 3.13, `~/y8` at `124c9a7`) and `m910q` (offscreen, the gate venv, Python 3.11, `~/dads-mmo-lab` at `a13a5def`) — both PySide6 6.11.2, both `False` on Yes and `False` on No. Evidence `pyplan/gates/t33-yes-reads-as-no-2026-09-11/` (`01-`, `02-`, `live_probe.py`, README).

## Report (hand, Sonnet, 2026-09-11 22:48 CEST, `hand-t33` at `9229baf3`)

`yulon/ui/answers.py::said_yes()` (`answer == StandardButton.Yes`); the six sites switched (`catalog_view.py:137`, `:666`, `:890`; `controller_view.py:5304`, `:5377`, `:5443`); `tests/test_answers.py` (seven tests: `int(Yes)`, the member, `int(No)`, `No`, `NoButton`, `0`, and a repo-wide AST test that no `is`/`is not` against a `StandardButton` remains — it listed all six before the switch); the real-dialog tests `test_a_real_yes_on_the_suggestion_dialog_reads_as_yes` (FAILED on the old code: `assert False is True`; passes now) and `…_no_…`; one int-returning fake per remaining site (`…still_adopts_an_unverified_folder`, `…still_offers_and_takes_the_restart` — the first tests ever on `_offer_a_restart_instead`, `…still_starts_the_rebuild`, `…the_database_updates`, `…the_adopt_press`), each mutation (`==`→`is`, or the site back to `is`) watched failing. Stale prose naming the old spelling updated in three places. Gate `=== --checks: ALL GREEN ===` (4205 passed, 6 skipped; mypy ×3, ruff, black clean) — the real-dialog test ran on m910q inside it. Status DONE → Codex adversarial review.

## Codex review (2026-09-11 22:50 CEST): ACCEPT

Non-blocking: the AST guard scans the shipped package only (as the ticket asked); `True`, `1`, `NoButton`, `0` and `Yes | No` none equal `Yes` — explicit tests for `True`, `1` and the combination would add coverage; the real-dialog test's poller stops at its deadline and its timer closes an open box, so a broken run cannot hang or leak a modal; all five per-site fakes return `int(...)`; one section comment could go; Codex could not run the suite in its sandbox and reviewed by reading. Lead's hand before the merge: the probe is cited at `pyplan/gates/t33-yes-reads-as-no-2026-09-11/static_probe.py` instead of the session scratchpad (five docstrings).

## Closed (lead, 2026-09-11 22:54 CEST)

Merged `--no-ff` as `8dc5204a` (+ `be09d1ec`, four docstring lines wrapped after the lead's citation fix took ruff over the limit — the first gate behind the merge was RED on that alone); gate `=== --checks: ALL GREEN ===` (4211 passed, 6 skipped); pushed. Evidence `pyplan/gates/t33-yes-reads-as-no-2026-09-11/` committed with the close. Worktree and `hand-t33` removed. CHANGELOG: one line under Fixed. To upstream with T32 and T34 in one PR; `v0.8.0-Public` carries the bug on every platform.
