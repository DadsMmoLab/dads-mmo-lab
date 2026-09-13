# T44 — the Modules and Tuning tabs carry everything the mockups promised, and look like them

**Status:** FILED — spec ready; the hand starts next
**Filed:** 2026-09-13 by the lead, from the owner after the live press on `yulon-win11`: "I want all the info that was on this one" (the approved mockup) "and same look", then "same with tuning".
**Mockups:** Modules https://claude.ai/code/artifact/a2e50955-de56-4ffe-99fb-aa9eb5f82419 (the owner's own, approved before T42) · Tuning https://claude.ai/code/artifact/21ebf51e-a3f5-4f6b-90c9-dbac665c9bdf (painted in `theme.py`'s own constants)
**Hand:** Opus 5. Worktree `.claude/worktrees/t44`, branch `hand-t44` **from `feat/modules-and-tuning-tabs`** (the PR 153 branch, which is T42 + T43 cut onto `upstream/Yulon`). Reviewer: Codex adversarial, two-round cap; a third round is the owner's to grant.
**Do not edit** `yulon/ui/theme.py`, `icons.py`, `widgets/dadcraft_decorations.py` or `widgets/log_panel.py`. Colours come from `theme.py`'s `COLOR_*` constants. **Note upstream renamed the theme**: `warcraft_icon` → `dadcraft_icon`, `warcraft_decorations` → `dadcraft_decorations`, `WarcraftRealmBadge` → `DadcraftRealmBadge`.

## Why this is a ticket and not a T42 amendment

T42 shipped and passed review. Three of the gaps below it **excluded on purpose**, and the
exclusion is quoted in its own definition of done:

> What is NOT in reach and stays out: a per-module pull (`module_updates()` only counts;
> there is no Update button to give a row), a persisted "rebuild pending" marker (the report
> box forgets it on restart today, and so will the chip), a per-row version line (a `git log`
> per row on every reload is the cost T41 refused).

The owner has now asked for them anyway, having seen the tab running. That is a decision he
is entitled to make, and it reverses a documented one — so it gets its own ticket with its
own evidence, rather than being smuggled into a merged one.

## The gap, measured against the live press

Screenshot of the tab as it runs today: `pyplan/gates/t44-modules-gap-2026-09-13/`.

| # | the mockup has | the app has | note |
|---|---|---|---|
| 1 | a version line per row, `7c02b1d · 2026-09-01` | nothing | **the expensive one — see below** |
| 2 | an **Update** button on a row that is behind | nothing | needs a per-module pull that does not exist |
| 3 | a section hint beside `Installed (N)` — "compiled into the worldserver", "no rebuild — the world reloads them on restart", "Dad's MMO Lab bundles: server side plus a client addon" | nothing | pure copy, per family |
| 4 | an expanding subpanel under an owed row, with its own action button | the chip writes to the report line | |
| 5 | badges `Cloned, SQL not applied` and `Not for this game` | only `Installed` / `Not installed` | |
| 6 | beveled buttons, the card title notched centre-top, tabs drawn as tabs | flatter, notch at the left | QSS, within `theme.py`'s constants |

## Item 1 is the one with a price, and it is not to be paid per reload

`git log -1` per installed module on **every** `reload_modules()` is what T41 refused and
T42 restated. Reads happen on every install, remove, refresh and tab rebuild. Do not simply
add the call.

Implement it so the cost is paid once and then cached:

- read it lazily, for **installed rows only**, off the clone's `.git` — a `git log -1
  --format=%h %cs` per module, not a fetch;
- cache per `(server_dir, module_id)` and invalidate on anything that can change the clone:
  a successful install, remove, or update, and the Refresh press;
- never block the first paint — if the read has not happened yet the row shows nothing where
  the version goes, and fills in. A row that renders late is fine; a tab that takes a second
  per module to open is not;
- a clone with no `.git`, an unreadable one, or a `git` that is not on PATH answers nothing
  and says nothing. It must never guess a sha, and it must never turn a reload into a
  failure.

Measure it and put the number on the ticket: the tab's reload time with 2 installed modules
and with 20, before and after.

## Item 2 needs a real pull, or it must not exist

A button that appears to update and does not is worse than no button. `module_updates()`
counts commits behind; nothing below this tab pulls. Either:

- implement the pull on the same seam the install uses (`apply`/`git`), with the same
  refusals — a dirty clone is not fast-forwarded, and the module's rebuild/SQL consequences
  are reported exactly as an install reports them; or
- **leave the button out** and say so on the ticket.

The hand decides and defends the choice. If it is implemented, a module whose update needs a
rebuild must raise the same amber banner an install does.

## The Tuning tab's gap, measured against the same press

Screenshot beside the Modules one in the same gate folder. The tab works — the cards are
real, the explanations are each module's own words, the values are read off disk and the
apply sentence is computed — but it is the plainest possible rendering of that.

| # | the mockup has | the app has |
|---|---|---|
| 7 | an action bar: **Reload from disk**, **Revert all changes**, and the two that cost something — *Recreate containers…*, *Restart server…*, greyed until one is owed | only **Refresh** |
| 8 | a banner naming what is waiting on a restart, with its own button | nothing |
| 9 | a chip per row: `Restart pending`, `free text`, `read-only in this version` | nothing |
| 10 | the old value on a changed row — `beastmaster.min_level · was 10` — and a rail down its left edge | nothing |
| 11 | an int's real limits beside the control, `0–80` | nothing |
| 12 | `Settings N` and a per-card hint: "read at world start", "patched into a deployed Lua script" | the apply sentence only |
| 13 | the file picker as buttons, with `worldserver.conf · read-only` among them | a plain path field |
| 14 | the lint verdict live — "✓ every line reads as Key = Value" | nothing |
| 15 | the backup's name after a save, and a **Revert** beside **Save file** | Save file only |
| 16 | the subpanel warning that a raw rewrite of a mounted conf needs the containers **recreated**, not a world restart | nothing |

Items 9, 10, 11 and 14 are all facts T43 already computes and then throws away —
`TuningRow` carries the type, the bounds and the starting value, `lint()` returns its
verdict, and `apply_rule()` returns the cost. This is mostly a rendering ticket, not a new
engine. Item 16 is the one that is a real safety message: `ModuleFiles.svelte:124-128` on
`rust-main` says promising the fast restart there "would be a promise we cannot keep".

Item 15's Revert must restore from the backup `tuning.write()` already takes, and say which
file it restored — not re-read the form.

## Definition of done, in order

One commit per item, TDD, each test with its mutation named.

1. **Section hints** (item 3) — per family, from a mapping beside `FAMILY_TITLES`, asserted
   by a test that the four families each have one and that it names what the family costs.
2. **The two badges** (item 5). `Cloned, SQL not applied` is `installed and sql_owed` —
   the state T43's probe produced on a real install. `Not for this game` is a manifest whose
   `game` is not this entry's; today those rows are simply absent, and showing them greyed is
   what the mockup does.
3. **The subpanel** (item 4) — an owed chip expands its row instead of only writing to the
   report. Keep the report line: it is what a user copies into a bug report.
4. **The version line** (item 1), to the rules above, with the measurement.
5. **The Update button** (item 2), or its refusal, argued.
6. **The Tuning tab's rendering** (items 9-12, 14) — the facts it already has, shown.
7. **The Tuning tab's controls** (items 7, 8, 13, 15, 16) — the action bar, the banner, the
   picker, Revert-from-backup, and the recreate warning.
8. **The look** (item 6), both tabs — QSS only, `theme.py`'s constants only, no new colours
   and no edits to the forbidden files.

## Evidence the ticket owes

- The gate's last line, and a named mutation per test.
- The reload-time measurement from item 1, both row counts.
- One screenshot of EACH finished tab on `yulon-win11` against the real WotLK install,
  beside its mockup, for the owner to compare.

## Report (hand, Opus 5, 2026-09-13)

Branch `hand-t44` in `.claude/worktrees/t44`, off `d20997bc`. Eleven commits —
one per definition-of-done item, plus one for the busy gate the new bar needed,
one for the two reds the full gate turned up, and one that is not T44's work at
all. Nothing pushed.

| sha | what |
|---|---|
| `30c5d025` | item 3 — a section hint per family |
| `47154d15` | item 5 — `Cloned, SQL not applied` and `Not for this game` |
| `a44c6551` | (not T44) `black` on `controller_probe.py` |
| `bb746180` | item 4 — the owed chip's subpanel and its press |
| `e6c2aa03` | item 1 — the version line, read once and cached |
| `08c07bb6` | item 2 — Update, through the install seam |
| `bdad9a7f` | items 9-12, 14 — the Tuning tab's rendering |
| `f401894a` | items 7, 8, 13, 15, 16 — the Tuning tab's controls |
| `8c06a7e8` | item 6 — the bevel and the centre notch |
| `3741ce64` | item 7 — the busy gate reaches the new bar |
| `7df8ce81` | the gate's two reds — one mine, one upstream's |

### What moved, by file

`yulon/ui/widgets/modules_panel.py` — `FAMILY_HINTS`; `BADGE_SQL_NOT_APPLIED`,
`BADGE_NOT_FOR_THIS_GAME`, `_badge_for()`; `ChipAction`,
`CHIP_ACTION_LABELS`, `Chip.action`; `ModuleRow.for_this_game`,
`.badge`, `.version`; `class VersionCache`; `build_module_rows(..., game=,
versions=)`; `RowWidget.version_label`/`.detail`/`.detail_button`/
`.detail_visible()`/`.set_version()`; `_FamilyCard.hint_label`/
`.laid_out_hint()`; `ModulesPanel.chip_action_pressed`/`.family_hint()`/
`.set_version()`.

`yulon/ui/widgets/tuning_panel.py` — `CHIP_READ_ONLY`, `CHIP_FREE_TEXT`,
`PENDING_CHIPS`, `HINT_CONF`/`HINT_LUA`/`HINT_OTHER`, `LINT_OK`,
`READ_ONLY_SUFFIX`, `RAW_REWRITE_NEEDS_RECREATE`; `row_chips()`,
`bounds_note()`, `card_hint()`, `picker_label()`; `CHANGED_FROM` reshaped to
`{key} · was {old}`; `RowEditor.chips`/`.bounds_label`/`._draw_chips()`/
`._draw_rail()`; `CardWidget.count_label`/`.hint_label`/`.edited`, object name
`tuningCard`; `TuningPanel.recreate_warning`/`.backup_label`/
`.file_revert_button`/`.file_revert_pressed`/`.edited`/`.file_buttons()`/
`.current_file()`/`.set_backup()`/`.has_edits()`, and `set_files(...,
read_only=)` — the picker is now buttons, not a `QComboBox`.

`yulon/ui/widgets/panel_style.py` — NEW. `panel_qss()`, `CARD_OBJECT_NAMES`.

`yulon/git.py` — `VersionReader` Protocol, `VERSION_FORMAT`,
`VERSION_SEPARATOR`, `RunnerGit.head_version()`.

`yulon/ui/controller_view.py` — `ControllerServices.module_version`;
`_versions`/`_filling_versions`, `refresh_modules()`, `_known_versions()`,
`_start_filling_versions()`, `_fill_next_version()`; `_chip_action_pressed()`,
`_module_action(..., called=)`; `_confirm()`; `TUNING_*` labels and sentences,
`_tuning_owed`, `_note_tuning_owed()`, `_refresh_tuning_owed()`,
`_set_tuning_revert_all()`, `revert_all_tuning_edits()`, `restart_server()`,
`recreate_containers()`, `_do_restart()`, `_do_recreate()`,
`_tuning_job_done()`, `_tuning_job_failed()`, `revert_tuning_file()`.

64 new tests across `test_modules_panel.py` (26), `test_tuning_panel.py` (16),
`test_controller_view.py` (14), `test_git.py` (3) and `test_panel_style.py`
(5, new file). 60 mutations run, each against the tree with `__pycache__`
purged on both sides; every one is listed below and every one failed its test.

### Item 1 — the measurement

`pyplan/gates/t44-modules-gap-2026-09-13/reload-times.txt`, with the harness
(`measure-reload.py`) beside it. Median of 5, WSL2 Ubuntu-24.04 on the laptop,
native ext4, Python 3.13.15, offscreen Qt. BEFORE is this same tab with the
naive line in it — `VersionCache.known` patched to read, so every reload does a
`git log -1` per installed row.

| installed | N raw `git log -1` | BEFORE, per reload | AFTER, first paint | AFTER, fill after the paint | AFTER, later reload |
|---|---|---|---|---|---|
| 2 | 4.5 ms | 73.1 ms | 68.2 ms | 42.4 ms | 68.4 ms |
| 20 | 32.4 ms | 100.0 ms | 68.1 ms | 84.3 ms | 68.9 ms |

The read costs ~1.6 ms per installed clone on this box. The naive line pays
that on every reload — and the tab reloads after every install, remove, update
check and Refresh — while the shipped one pays it once per clone and never on
the paint. The "fill" column is dominated by the event-loop turn between reads
(one module per `singleShot(0)`), not by git; that is the point, since none of
it is on the first paint.

Two honest limits on the number. It is measured on ext4 through WSL — a
subprocess on the owner's `/mnt/c` or on Windows is dearer, which makes the
BEFORE column worse and leaves the AFTER column alone. And the ~68 ms floor is
the panel rebuilding 41 rows, which is T42's cost and unchanged.

### Item 2 — the decision, and the reasoning

**Implemented, not refused.** The pull already exists and is already guarded:
`Applier.install()` over a clone that is on disk IS a pull, because
`RunnerGit.clone()` on a folder with a `.git` in it runs `_update()` — `git
fetch origin <ref>` then `git reset --hard FETCH_HEAD` — and re-applies the
pin. Everything after it (deploy, patches, SQL, conf activation, client files)
is exactly what has to be redone once the source has moved.

Routing Update there buys three things and costs nothing:

* **the same refusals.** `_require_own_clone()` runs first; on a checkout with
  no claim `_adoption_refusal()` refuses an EDITED tree and one carrying the
  user's own commits — the dirty clone is not fast-forwarded, it is refused
  outright. Already covered by
  `test_a_checkout_with_local_work_in_it_is_not_adopted` and
  `test_a_checkout_carrying_the_user_s_own_commits_is_not_adopted`.
* **the same consequences, reported the same way.** It returns an
  `ApplyReport`, so a module whose update needs a rebuild raises the same
  amber banner and the same chip an install does, through
  `_note_session_facts()` unchanged.
* **no second route** with a second copy of that guard to keep in step.

**The limit, stated rather than hidden.** It adds no refusal of its own: a
clone this app's own claim vouches for is reset WITHOUT asking `unmodified`,
so local edits inside a module Yu'lon installed are discarded. That is
precisely what the Install button does to the same folder today — Update adds
no exposure, it makes an existing one reachable from a row that invites it.
So the chip's sentence, which the subpanel puts directly above the button,
says it will discard anything changed inside the clone and may ask the
module's install questions again. A refusal for Update alone would make it
stricter than Install and give the tab two rules for one `reset --hard`; if
the lead wants that guard it belongs on both presses, as its own ticket.

`_module_action(..., called="update")` changes the SENTENCE and never the
route: a user who pressed a button marked Update must not read "install mod-x"
back from it. A finished report drops that module's "N commits behind" — the
checkout has moved off the commit that figure was about.

### Every test, with the mutation run against it

**Item 3 (2)**
- `test_every_family_has_a_hint_that_says_what_that_family_costs` — drop the `keg` key → FAILED.
- `test_a_family_card_draws_its_hint_beside_the_installed_header` — build the label and never `addWidget` it → FAILED. **The first version of this test SURVIVED that mutation**: a `QLabel` parented to the card but laid out nowhere is still `isVisibleTo()` it and still carries its text. `family_hint()` now reads the label off the card's LAYOUT, which is `laid_out_rows()`'s rule from T42.

**Item 5 (6)**
- `test_an_installed_module_whose_sql_is_waiting_says_so_in_its_badge` — drop the `sql_owed` clause from `_badge_for()` → FAILED.
- `test_a_manifest_for_another_game_is_drawn_greyed_and_offers_no_install` — ignore the `game` argument → FAILED.
- `test_no_game_given_means_every_manifest_belongs_here` — default `game` to `""` and compare with `==` → FAILED.
- `test_an_uncatalogued_clone_is_installed_and_belongs_here` — build T41's rows with `for_this_game=False` → FAILED.
- `test_the_row_widget_draws_the_badge_the_builder_decided` — restore the widget's own `BADGE_INSTALLED if data.installed else …` → FAILED.
- `test_a_row_for_another_game_gets_no_install_button` — drop `for_this_game` from the button condition → FAILED.

**Item 4 (7)**
- `test_each_owed_chip_names_the_action_that_answers_it_and_a_fact_names_none` — give the `asks a question` chip an action → FAILED.
- `test_every_chip_action_has_a_button_label` — drop the `sql` key → FAILED.
- `test_an_owed_chip_press_opens_a_subpanel_under_its_own_row` — never `setVisible(True)` → FAILED.
- `test_a_second_press_on_the_same_chip_closes_the_subpanel` — drop the toggle-shut branch → FAILED.
- `test_a_chip_press_still_writes_the_report_line` — replace the `pressed_chip` emit with the expansion → FAILED.
- `test_the_subpanels_button_emits_the_rows_id_and_the_action_key` — emit `CHIP_ACTION_LABELS[action]` instead of the key → FAILED.
- `test_a_fact_chip_opens_nothing` — expand on every chip → FAILED.

**Item 1 (14 tests, 10 mutations)**
- `test_head_version_reads_the_clone_and_never_fetches`, `test_head_version_answers_nothing_rather_than_guessing`, `test_the_version_line_equals_what_git_log_prints_by_hand` (real git, skipped where none) — covered by the argv, the four cannot-answer cases and the by-hand comparison.
- V1 `_known_versions()` calls `fill()` → FAILED (`test_the_first_paint_of_the_modules_tab_reads_no_clone_at_all`).
- V2 drop `_start_filling_versions()` from `reload_modules()` → FAILED.
- V3 `VersionCache.fill()` never caches → FAILED (7 tests).
- V4 Refresh bound back to `reload_modules` → FAILED.
- V5 a report forgets no version → FAILED.
- V6 the builder looks up a version for every row, not just installed ones → FAILED **after strengthening**: the first version of `test_the_builder_carries_the_version_it_was_handed_and_nothing_else` offered no version for the uninstalled row, so it could not see the mutation. It now offers one and asserts it is not shown.
- V7 `known()` reads → FAILED.
- V8 `forget()` is a no-op → FAILED.
- V9 the cache path drops the family → FAILED **after** re-running it over the whole file (the `-k` filter had not selected `test_two_families_that_share_an_id_are_two_clones_and_two_entries`).
- V10 `set_version()` indexes `self._rows[key]` directly → FAILED.

**Item 2 (3 tests, 5 mutations)**
- U1 drop the chip's `"update"` action → FAILED.
- U2 route Update to `applier.remove` → FAILED.
- U3 Update does nothing → FAILED.
- U4 `_behind` survives the pull → FAILED.
- U5 keep T42's "Yu'lon has no per-module pull" detail → FAILED.

**Items 9-12, 14 (10)**
- T1 a read-only row gets no chip → FAILED.
- T2 every text box is chipped `free text` → FAILED.
- T3 the pending chip hard-coded to `Restart pending` → FAILED.
- T4 bounds shown for a one-bound int → FAILED.
- T5 the card hint keyed off the family → FAILED.
- T6 `Settings N` counts files → FAILED.
- T7 the changed line drops the key → FAILED.
- T8 the rail set once in `__init__` → FAILED.
- T9 no verdict on a clean file → FAILED.
- T10 read-only files linted too → FAILED.

**Items 7, 8, 13, 15, 16, and the busy gate (14)**
- C1 the two expensive buttons start enabled → FAILED.
- C2 a save arms both jobs → FAILED.
- C3 the banner names the cheaper job → FAILED.
- C4 `revert_all_tuning_edits()` calls `reload_tuning()` → FAILED **after strengthening**: the first version asserted only "edits cleared, nothing written", which both spellings satisfy. It now changes the file on disk behind the tab and proves Revert all changes does NOT pick it up while Reload from disk does.
- C5 the raw Revert re-opens instead of restoring → FAILED.
- C6 the picker gets no `read_only` list → FAILED.
- C7 no confirm on restart → FAILED.
- C8 full paths on every picker button → FAILED.
- C9 basenames even when they collide → FAILED.
- C10 the recreate warning shows on read-only files → FAILED.
- C11 the backup name survives the next file → FAILED.
- C12 raw Revert armed with no backup → FAILED.
- C13 the busy gate skips the tuning bar → FAILED.
- C14 the busy gate unlocks the bar unconditionally → FAILED.

**Item 6 (5 tests, 6 mutations)**
- L1 a hand-typed hex in `panel_qss()` → FAILED (`test_the_panel_qss_invents_no_colour` names it).
- L2 the notch selector unscoped to `QGroupBox` → FAILED.
- L3 the pressed bevel grows the box → FAILED.
- L4 no bevel at all → FAILED.
- L5 only the Modules panel wears the sheet → FAILED.
- L6 `CardWidget`'s object name renamed → FAILED.

### Deviations, and three things the lead should decide

1. **`Not for this game` has no live producer today.** `manifest_store._load_at()`
   RAISES on a manifest whose `game` is not the store's, and the raise takes
   the whole family down through `_load_manifests()`'s `!!` line. So nothing on
   disk can reach `build_module_rows()` with a foreign-game manifest, and the
   badge is the rule stated where the rows are decided rather than a row anybody
   has seen. The ticket's own wording ("today those rows are simply absent")
   anticipates this. Making it live means changing the store's validation, which
   is a real guard and out of this ticket's scope — worth its own ticket if the
   owner wants the greyed row on screen.
2. **`a44c6551` is not T44's file.** `yulon/ui/controller_probe.py` was failing
   `black --check .` — a CI step — on `hand-t44`'s parent, so every push from
   this branch would have been red for a reason that is not this ticket's. Two
   lines, produced by black itself, in their own commit so they can be dropped
   whole if another lane owns that file. One further line in
   `modules_panel.py`'s own docstring was at 108 characters and failing
   `ruff check .` for the same reason (upstream's `warcraft_` → `dadcraft_`
   rename); it is reflowed inside `30c5d025`.
3. **Item 6's third clause was already true.** "tabs drawn as tabs" is
   `theme.py`'s own `QTabWidget`/`QTabBar` block, and the as-built screenshot in
   this gate folder shows real tabs. `theme.py` is forbidden here anyway, so
   item 6 is the bevel and the notch, both scoped to the two panels.
4. **New file.** `yulon/ui/widgets/panel_style.py` — not in the forbidden list,
   and the honest home for a sheet both tabs must share: a bevel spelled twice
   is a bevel that can differ.
5. **`ControllerServices` gained `module_version`** and is wired to
   `RunnerGit().head_version` for the one game with clones. `ContainerGit` does
   NOT get it: a `docker run` per module to print a sha costs more than the line
   is worth, and a machine with no host git answers nothing, which the ticket
   permits explicitly.
6. **T43 tests updated where the shape changed, never deleted.** Four for
   `CHANGED_FROM`'s new `{key} · was {old}`, one for the clean-file lint verdict,
   three for the picker being buttons rather than a `QComboBox`. Each edit is in
   the commit that caused it and carries the reason.
7. **Four view-level version tests were written after the implementation**, not
   before it: the unit tests in `test_modules_panel.py` and `test_git.py` drove
   item 1 red-first, and the view tests were added to pin the wiring. Their
   evidence is the V1-V5 mutation runs above, not a RED-first run.
8. **"Revert all changes" is the undo for the FORM**, not a restore from
   backups. Reload from disk re-reads the files; Revert all changes rebuilds the
   cards from rows already read and writes nothing; the per-card Revert is the
   one that restores a file from its backup. The gap table does not say which
   the mockup meant, and this is the reading that cannot destroy anything.
9. **`restart_server()` and `recreate_containers()` are new public slots on the
   view.** Restart is `controller.stop()` + `controller.start()` in one worker
   job; Recreate is `controller.remove()` + `controller.start()`, which is the
   Server tab's own sentence for that pair ("Containers removed; volumes kept.
   The next Start will recreate them").
10. **The full gate turned up two reds, and only one was mine.**
    `test_every_game_offers_the_whole_controller_surface_wotlk_does` failed on
    `module_version` — the new seam is wired for the one game with clones, so it
    joins `module_updates` and `installed_modules` in that test's `uncounted`
    allowance, which is the third size of one fact.
    `test_every_folder_listing_in_the_package_is_accounted_for` fails on
    `d20997bc` too — I checked out the parent and ran it. `ui/gamepad.py`
    arrived with upstream's UX passes carrying a local generator named `walk`
    over Qt's `QWidget.children()`; the audit reads `walk` as one of its eight
    filesystem spellings and so sees two sites that touch no path at all. Both
    are now in `_ACCOUNTED_LISTINGS` with that reason rather than narrowed out
    of `_LISTING_CALLS` — the audit is an equality, and shrinking what it
    matches on to dodge a false positive is exactly how it twice stopped seeing
    the class it enumerates. Both fixes are in `7df8ce81`, which can be split if
    the lead wants the upstream half separate.
11. **The screenshots the ticket owes are NOT taken.** "One screenshot of EACH
    finished tab on `yulon-win11` against the real WotLK install, beside its
    mockup" is still owed — this hand ran only on the WSL tree and started no
    VM. That is the last piece of this ticket's evidence.

### The gate

`YULON_REPO=/home/perzi/dads-mmo-lab/.claude/worktrees/t44 yt`, last line:

```
4628 passed, 8 skipped, 23 deselected, 1 warning in 184.76s (0:03:04)
```

`black --check .`, `ruff check .` and `mypy` over `yulon` are all clean on the
finished tree.

**Status:** the eight definition-of-done items are implemented, tested and
committed on `hand-t44` (nothing pushed). Owed before this can close: the two
`yulon-win11` screenshots, and the Codex adversarial review the ticket names.
