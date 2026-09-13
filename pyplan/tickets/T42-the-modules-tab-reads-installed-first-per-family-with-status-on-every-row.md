# T42 — the Modules tab reads installed first, per family, with status on every row

**Status:** FILED — spec ready; the hand starts in another session
**Filed:** 2026-09-12 22:30 CEST by the lead (Fable), from the owner: "can you check how my dml launcher module section is made? I want to make it look more like that on yulon also." The DML launcher's page is `launcher/src/lib/pages/ModuleManager.svelte` on `rust-main` (its design: `docs/superpowers/specs/2026-08-11-modules-page-round-design.md` on `feat/modules-page-round`). The owner approved the mockup at https://claude.ai/code/artifact/a2e50955-de56-4ffe-99fb-aa9eb5f82419 (source: the lead's scratchpad, `modules-tab-mock.html`).
**Hand:** Opus 5. Worktree `.claude/worktrees/t42`, branch `hand-t42` from `yulon-phase8b` at or after `ec76ec16` (T41's review round — this ticket builds on T41 and must keep its behaviour). Reviewer: Codex adversarial (or a cold Opus), two-round cap, then the lead closes by hand. Unit; the live press on `yulon-win11` is the lead's. **Do not edit `yulon/ui/theme.py`, `icons.py`, `widgets/*_decorations.py` or `widgets/log_panel.py`, and import nothing from the decorations modules: upstream `Yulon` carries Baerthe's Pass 8–12 on those files (not yet merged into `yulon-phase8b`), and `warcraft_decorations.py` is deleted there. Colours come from the `COLOR_*` constants `theme.py` already exports.**

## What is true today

- `ControllerView._build_modules_tab()` (`ui/controller_view.py`, search the name) is one `QListWidget` of every manifest in catalog order, rows `✓ [module] Name — description` since T41 (`INSTALLED_MARK`, `NOT_IN_CATALOG` rows for a clone the catalog has never heard of, `UNCATALOGUED_PRESS` when one is pressed), two toolbars of nine buttons, a `module_report` box and the `rebuild_log` panel on the right. Baerthe's passes only wrapped the list in a "Modules" group box.
- T41 added the cheap reading this ticket keys everything on: `ControllerServices.installed_modules` → `apply.installed_clones(server_dir)` → ids per family from each family's own clone directory (`apply.CLONE_DIRS`: module→`modules/`, ale and keg→`ale_scripts/`, mod→`sql_scripts/clones/`). Its per-FOLDER accounting of uncatalogued rows (a keg's clone is read into both the ale and keg sets) is in `reload_modules()` and must survive.
- Every row's data is already in reach: family = `manifest.type` (`FAMILY_FILES` order: module, ale, keg, mod); GitHub link = `manifest.source.url` (slugs resolve to GitHub; `None` when there is no source); conf and script paths = `manifest.conf[*].file`; a question at install = `required_prompts(manifest, "install")` with a `default is None`; dependencies = `manifest.requires`; a rebuild owed = `ApplyReport.rebuild_required`; SQL owed = `ApplyReport.pending_sql`; behind upstream = `ModuleUpdate.behind` from `check_module_updates()`.
- What is NOT in reach and stays out: a per-module pull (`module_updates()` only counts; there is no Update button to give a row), a persisted "rebuild pending" marker (the report box forgets it on restart today, and so will the chip), a per-row version line (a `git log` per row on every reload is the cost T41 refused).

## The shape (the mockup, in Yu'lon's own theme)

Top to bottom inside the tab: an **action bar** (Check for module updates, Refresh, stretch, Adopt as imported…, Apply database updates…, Apply module SQL, Rebuild server…); an **amber banner** when a rebuild is owed, with its own Rebuild server… button; **four family cards** in `FAMILY_FILES` order, titled `C++ modules`, `ALE Lua scripts`, `Kegs`, `SQL & config mods`, each with an **Installed (N)** section first and an **Available N — not installed** toggle below it, collapsed when the family has at least one installed module and open when it has none; a **custom-module card** ("Install from a link…", "Install from a folder…" with one sentence); then the `module_report` line and the `rebuild_log` panel, both unchanged. One row skeleton for every module: name and GitHub link, one-line description, conf/script path in mono, then one status badge and chips, then the row's own Install or Remove button right-aligned in a column of shared width.

## Definition of done

1. **A panel widget, not a list.** `yulon/ui/widgets/modules_panel.py`: `ModulesPanel(QWidget)` inside a `QScrollArea`, built from a sequence of `ModuleRow` (frozen dataclass: `id`, `family`, `name`, `description`, `url: str | None`, `installed: bool`, `catalogued: bool`, `paths: tuple[str, ...]`, `chips: tuple[Chip, ...]`, `removable: bool`, `remove_reason: str | None`; `Chip` = `kind` in {"owed", "fact"}, `label`, `detail`). Signals: `install_pressed(str)`, `remove_pressed(str)`, `chip_pressed(str, str)` (id, label), `row_selected(str)`. Methods: `set_rows(rows)` (rebuilds the cards, keeps each family's open/closed state and the selection where the id still exists), `select(id)`, `selected_id() -> str | None`, `row(id) -> RowWidget` (exposes `install_button`/`remove_button` for tests), `set_enabled_actions(bool)`. Family titles in a `FAMILY_TITLES` mapping beside `FAMILY_FILES`. The open/closed rule: collapsed iff the family has an installed row; a toggle press flips it; `set_rows()` does not reset it. Clicking anywhere on a row selects it (a visible highlight through `COLOR_*` constants); right-click keeps `_show_module_context_menu` (moved to take the row's id).
2. **A pure builder** `build_module_rows(manifests, installed, session, client_dir) -> tuple[ModuleRow, ...]` in `modules_panel.py` (or a sibling `modules_rows.py`), tested without widgets. Installed rows first within each family, catalog order inside each half; T41's uncatalogued rows appended to their family as `installed=True, catalogued=False, description=NOT_IN_CATALOG`, with T41's per-folder accounting **moved** here from `reload_modules()`, not duplicated. Status badge text: `Installed` (green) / `Not installed` (grey). Chips, and nothing beyond these six: owed `Rebuild pending` (the id is in the session's `rebuild_owed` set); owed `SQL pending` (in `sql_owed`; detail names the files); owed `Update available — N behind` (in `behind`, N > 0); fact `asks a question` (uninstalled, a prompt with no default); fact `needs the client folder` (uninstalled, the manifest has a `client` step, `client_dir is None`); fact `required by <names>` (installed, another INSTALLED manifest lists it in `requires`) — and that row is `removable=False` with `remove_reason` naming them.
3. **Session state the chips read**, held on the view and documented as not persisted: `_rebuild_owed: set[str]`, `_sql_owed: dict[str, tuple[str, ...]]`, `_behind: dict[str, int]`. Filled from `_module_done()` (the report's `rebuild_required` / `pending_sql`) and `_module_updates_done()`; `_rebuild_owed` cleared when `_rebuild_finished` reports success, `_sql_owed` cleared when `apply_module_sql` finishes, a removed id dropped from all three. The **banner** shows iff `_rebuild_owed` is non-empty and names the modules; its button is the same `rebuild_server` slot. A press on an owed chip writes its `detail` into `module_report`; a fact chip does nothing but carries its detail as a tooltip.
4. **The existing surface keeps its names.** `reload_modules()` builds rows and calls `set_rows()`; `selected_manifest()` reads `selected_id()`; `_module_action(action)` stays the one handler for both row buttons (T41's `UNCATALOGUED_PRESS` path stays reachable through the context menu on an uncatalogued row, which has no buttons); `install_module_button` and `remove_module_button` are removed from the toolbar and from the class (tests move to `modules_panel.row(id).install_button`); the link/folder buttons keep their names and `_set_custom_module_buttons()` gating; a new `refresh_modules_button` calls `reload_modules()`. `_set_busy()` also calls `set_enabled_actions(False)`/`(True)`, and the `store is None or applier is None` gate that greyed the two toolbar buttons now greys every row button. The T41 tests are **re-seeded** to the new surface, not deleted: every assertion they made (the mark, the uncatalogued row, its press sentence, one row per folder) must still be made by a test.
5. **Tests**, each with its mutation named in the report: builder — installed first, counts per family, the per-folder accounting (T41's four cases), each of the six chips present and absent, `removable=False` only with an installed dependant; panel — the collapse rule both ways and its survival across `set_rows()`, a row's Install emits with that id, `set_enabled_actions(False)` disables every row button, selection survives a rebuild, a click on a row selects it; view — `_module_done()` with `rebuild_required=True` shows the banner and the chip, a successful rebuild clears both, `check_module_updates` with `behind=3` shows the chip, busy greys the rows, the context menu on an uncatalogued row prints `UNCATALOGUED_PRESS`. Offscreen `QT_QPA_PLATFORM` as the rest of `test_controller_view.py`.

**Not in scope:** an Update button (no per-module pull exists — file it); a persisted rebuild marker; version lines; the mockup's explanation panel under a row (the report line carries the detail in v1); needs-setup metadata on manifests; the Catalog tab; any change to `apply.py` beyond moving nothing — the builder consumes what T41 exposes.

**Gate:** `yt` green in the worktree; `--checks` ALL GREEN on m910q is the lead's (one gate at a time on that box — the T37/T38 branch may be using it); black/ruff; every test with its named mutation; CHANGELOG line is the lead's. Commits on `hand-t42` (one per point is fine), trailer `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>` alone, no other attribution line, no push. Report appended below: sha(s), the file and class names, the six chip rules as implemented, how the T41 tests were re-seeded (old name → new), each test with its mutation, the gate's last line, deviations, Status.

**Live half (lead, after the merge):** `yulon-win11`, the owner's WotLK box with the BMAH keg and `mod-transmog` installed: the tab read against `pyplan/gates/t41-modules-tab-2026-09-12/modules-tab-v0.8.5-before.png`; a Solocraft install through a row's button → the banner and the chip; the rebuild → both gone; frames in `pyplan/gates/t42-modules-tab-yulon-win11-<date>/`.

## How to start the hand (any session)

```
cd ~/dads-mmo-lab && git pull --ff-only
git worktree add .claude/worktrees/t42 -b hand-t42 yulon-phase8b
```

Then a general-purpose agent on Opus 5 with: "You are the hand for T42. Read `pyplan/tickets/T42-*.md` in full and `pyplan/tickets/T41-*.md` for what you build on. Work only in `.claude/worktrees/t42` on `hand-t42`. Implement the definition of done in order, TDD: write each test, watch it fail, make it pass, name the mutation. Run `yt` from the worktree (never bare pytest, never docker). Do not touch the files the ticket forbids. Commit with the trailer the ticket names and nothing else. Append your report to the ticket under `## Report (hand, Opus 5, <date from the clock>)`. Do not push." The lead then runs the reviewer, the m910q gate, the merge and the live half.

## Report (hand, Opus 5, 2026-09-12)

**Status:** SUPERSEDED by the round-2 section below (Codex REJECT, addressed 2026-09-13). Unit-gated in the worktree, not pushed. Reviewer, the m910q
`--checks` gate, the CHANGELOG line, the merge and the live half on `yulon-win11` are the
lead's.

### Commits (`hand-t42`, from `eb30e6c3`)

| sha | what |
| --- | --- |
| `e11fcc9b` | point 2 — the pure builder |
| `c1bc34d1` | point 1 — the panel of family cards |
| `2a77ae6c` | points 3 and 4 — the view reads the panel and remembers what is owed |

### Files and classes

**New: `pylauncher/yulon/ui/widgets/modules_panel.py`.** No Qt in its first half, no import
of `controller_view`, none of the decorations modules; colours are `theme.py`'s `COLOR_*`
constants.

* `Chip(kind, label, detail)`, `ModuleRow(...)` and `SessionState(rebuild_owed, sql_owed,
  behind)` — frozen dataclasses.
* `build_module_rows(manifests, installed, session, client_dir) -> tuple[ModuleRow, ...]`.
  `manifests` is a flat sequence in the store's own order (the view concatenates
  `store.load_all(kind)` per family), which keeps the "a family would not load" branch in
  the view where the report box is.
* `FAMILY_TITLES` (module → `C++ modules`, ale → `ALE Lua scripts`, keg → `Kegs`, mod →
  `SQL & config mods`), `NOT_IN_CATALOG`, `BADGE_INSTALLED`/`BADGE_NOT_INSTALLED`, the four
  fixed chip labels, `chip_update_label(n)`, `chip_required_by_label(names)`,
  `NO_MODULES_NOTE`, `BUTTON_COLUMN_WIDTH`.
* `_clone_dir_of()` **moved here** from `controller_view.py` with T41's per-folder
  accounting; the view no longer has a copy.
* `RowWidget(QFrame)` — `.data`, `.name_label`, `.link_label`, `.description_label`,
  `.paths_label`, `.badge_label`, `.chip_buttons`, `.install_button`, `.remove_button`
  (`None` where that press does not exist), `.set_enabled_actions()`, `.set_selected()`.
* `_FamilyCard(QGroupBox)` — `installed_header`, `installed_box`, `toggle`,
  `available_box`.
* `ModulesPanel(QWidget)` — a `QScrollArea` of cards. `set_rows`, `select`, `selected_id`,
  `row`, `rows`, `set_enabled_actions`, `available_open`, `available_toggle`,
  `installed_header`, `empty_label`. Signals `install_pressed`, `remove_pressed`,
  `chip_pressed(id, label)`, `row_selected`, **and** `context_menu_requested(id, QPoint)`
  (see Deviations).

**Changed: `pylauncher/yulon/ui/controller_view.py`.** `INSTALLED_MARK`, `NOT_IN_CATALOG`
and `_clone_dir_of` removed (the last two moved); `REBUILD_BANNER`, `MODULE_LOAD_FAILED`
and `_pending_sql_names()` added. New/changed members: `modules_panel`,
`refresh_modules_button`, `rebuild_banner`/`rebuild_banner_label`/`rebuild_banner_button`,
`_rebuild_owed`/`_sql_owed`/`_behind`/`_rebuild_is_compile`, `_session_state()`,
`_module_actions_allowed()`, `_refresh_rebuild_banner()`, `_row_install`, `_row_remove`,
`_chip_pressed`, `_note_session_facts`. Gone: `module_list`, `install_module_button`,
`remove_module_button`, `_custom_install_pending`.

**Changed: `pylauncher/yulon/module_source.py`** — `_refuse_shipped` said "select it in the
list and press Install selected", naming a control this ticket removes. It now says "find
it on the Modules tab and press Install on its row".

### The six chip rules as implemented

All in `modules_panel._chips_for()`, owed first then facts, and nothing beyond these six.

| # | kind | label | when | detail |
| --- | --- | --- | --- | --- |
| 1 | owed | `Rebuild pending` | `id in session.rebuild_owed` | names the id and says to press Rebuild server… |
| 2 | owed | `SQL pending` | `session.sql_owed[id]` is non-empty | names every file (`_pending_sql_names` resolves `PendingSql.files`, falling back to the glob when `files` is `()` or `None`) and says to press Apply module SQL |
| 3 | owed | `Update available — N commit(s) behind` | `session.behind[id] > 0` | says Yu'lon has no per-module pull, so git in the clone folder |
| 4 | fact | `asks a question` | **not installed**, catalogued, and `required_prompts(manifest, "install")` has a prompt with `default is None` | lists the questions |
| 5 | fact | `needs the client folder` | **not installed**, catalogued, `manifest.client` non-empty, `client_dir is None` | says to set one on the Server tab |
| 6 | fact | `required by <names>` | **installed** and another **INSTALLED** manifest names it in `requires` | names them and says to remove them first — and the row is `removable=False` with `remove_reason` naming them |

Uncatalogued rows (`catalogued=False`) take chips 1–3 and 6 — the session and the
dependency graph know about an id whether or not a manifest does — and never 4 or 5, which
are facts about installing something that is already installed. They carry no buttons at
all.

### How the T41 tests were re-seeded

Not deleted. Four kept their names and were re-pointed at the panel; two were renamed
because the surface they name changed. Every assertion T41 made is still made — the mark is
now the `Installed` badge read off `RowWidget.badge_label`, which is a stronger reading than
the old leading-glyph substring.

| old (`tests/test_controller_view.py`) | new |
| --- | --- |
| `test_the_modules_list_says_which_modules_are_installed` | `test_the_modules_tab_says_which_modules_are_installed` |
| `test_pressing_install_on_an_uncatalogued_row_says_why_nothing_happened` | `test_the_context_menu_on_an_uncatalogued_row_says_why_nothing_happened` |
| `test_a_module_on_disk_the_catalog_never_heard_of_still_gets_a_row` | same name, asserts `catalogued is False`, the badge, and that the row has no buttons |
| `test_a_game_with_no_installed_modules_seam_lists_the_catalog_unchanged` | same name, asserts every badge reads `Not installed` (the old test only asserted the absence of a glyph) |
| `test_a_family_is_marked_from_its_own_clone_folder_not_from_modules` | same name, against the panel |
| `test_a_clone_matched_in_one_family_is_not_listed_again_as_unknown_in_another` | same name, against the panel |
| helper `_marked(view)` | reads `badge_label.text() == BADGE_INSTALLED` |
| helper `_select_module` / `_row_for` / `_rows_for` / `_listed` | read `modules_panel` instead of `module_list` |

The same four facts are also asserted at builder level in the new file, with synthetic
manifests, so a change to the shipped WotLK catalog cannot quietly stop exercising them:
`test_the_rows_say_which_modules_are_installed`,
`test_a_module_on_disk_the_catalog_never_heard_of_still_gets_a_row`,
`test_a_family_is_marked_from_its_own_clone_folder_not_from_modules`,
`test_a_clone_matched_in_one_family_is_not_listed_again_as_unknown_in_another`.

One more rename, outside T41: `tests/test_module_source.py`'s
`test_a_link_to_a_shipped_module_says_to_use_the_list_instead` →
`..._says_to_use_the_tab_instead`.

### Tests and their mutations

Every mutation below was applied to a clean tree with `__pycache__` purged on both sides,
run, and seen to FAIL; the tree was restored afterwards. 35 mutations in all.

**Builder — `tests/test_modules_panel.py` (19)**

| test | mutation that fails it |
| --- | --- |
| `test_installed_rows_come_first_inside_each_family` | one `rows += [_row(m) for m in family]` instead of the two halves |
| `test_catalog_order_is_kept_inside_each_half` | sort each half by id |
| `test_the_four_families_come_in_family_files_order` | `for kind in sorted(FAMILY_FILES)` |
| `test_every_family_has_a_title` | drop the `keg` key from `FAMILY_TITLES` |
| `test_the_rows_say_which_modules_are_installed` | `here = True` for every row |
| `test_a_module_on_disk_the_catalog_never_heard_of_still_gets_a_row` | `continue` before the uncatalogued row is appended |
| `test_a_game_with_no_installed_reading_marks_nothing` | default a missing family to `frozenset({manifest.id})` |
| `test_a_family_is_marked_from_its_own_clone_folder_not_from_modules` | `installed.get("module", …)` for every family |
| `test_a_clone_matched_in_one_family_is_not_listed_again_as_unknown_in_another` | account per family instead of per clone folder |
| `test_one_uncatalogued_clone_in_a_shared_folder_gets_one_row_not_two` | same mutation (both fail together) |
| `test_the_rebuild_pending_chip_is_on_exactly_the_owed_module` | `if False:` on `session.rebuild_owed` |
| `test_the_sql_pending_chip_names_the_files_it_is_waiting_on` | `detail = CHIP_SQL_PENDING` |
| `test_the_update_chip_counts_and_is_absent_at_zero` | `if behind >= 0:` |
| `test_the_asks_a_question_chip_is_only_on_an_uninstalled_manifest_with_no_default` | drop the `default is None` clause |
| `test_the_client_folder_chip_appears_only_while_no_folder_is_set` | drop `and client_dir is None` |
| `test_required_by_names_only_installed_dependants_and_locks_remove` | count dependants from `catalog` instead of `installed_ids` |
| `test_an_installed_module_nothing_needs_is_removable_and_carries_no_chips` | `removable=False` always |
| `test_a_row_carries_its_github_link_and_its_conf_paths` | `url=manifest.source.repo` instead of `.url` |
| `test_an_uncatalogued_row_still_takes_the_owed_chips` | `chips=()` on uncatalogued rows |

**Panel — `tests/test_modules_panel.py` (15)**

| test | mutation |
| --- | --- |
| `test_a_family_with_something_installed_starts_collapsed` | `self._open[kind] = True` |
| `test_a_toggle_press_flips_the_family_and_set_rows_keeps_it` | recompute the open state on every `set_rows()` |
| `test_a_rows_install_button_emits_that_rows_id` | emit `""` instead of `self.data.id` |
| `test_an_installed_row_has_no_install_button_and_the_reverse` | build both buttons on every catalogued row |
| `test_an_uncatalogued_row_has_no_buttons_at_all` | `else:` instead of `elif data.catalogued:` (an uncatalogued row gets a Remove) |
| `test_a_row_another_installed_module_needs_cannot_be_removed` | `setEnabled(enabled)` without `and self.data.removable` |
| `test_set_enabled_actions_false_disables_every_row_button` | drop the install half of `RowWidget.set_enabled_actions` |
| `test_set_enabled_actions_true_does_not_unlock_an_unremovable_row` | the same `setEnabled(enabled)` mutation |
| `test_the_selection_survives_a_rebuild_and_a_gone_id_does_not` | `self._selected = None` in `set_rows()` |
| `test_a_click_anywhere_on_a_row_selects_it` | drop `self.clicked.emit()` from `mousePressEvent` |
| `test_an_owed_chip_press_names_its_row_and_its_label` | emit the id with an empty label |
| `test_a_fact_chip_is_not_a_press_and_carries_its_detail_as_a_tooltip` | `if True:` instead of `if chip.kind == "owed":` |
| `test_a_panel_with_no_rows_says_so_instead_of_showing_nothing` | `empty_label.setVisible(False)` unconditionally |
| `test_the_installed_header_counts_that_family_only` | `Installed ({len(installed) + 1})` |
| `test_the_panel_reports_rows_in_the_order_it_was_handed_them` | fill the card by building the installed half first |

**View — `tests/test_controller_view.py` (11 new + the re-seeded T41 six)**

| test | mutation |
| --- | --- |
| `test_the_installed_row_is_drawn_above_the_ones_that_are_not` | drop the builder's installed-first split |
| `test_an_install_that_needs_a_rebuild_raises_the_banner_and_the_chip` | drop `_note_session_facts()` from `_module_done` |
| `test_only_a_compile_that_succeeds_clears_the_banner_and_the_chip` | `if ok:` (drops the compile flag) — and `if True:` |
| `test_a_database_update_through_the_same_panel_clears_no_rebuild` | the same two; this is the only test that sees the first |
| `test_a_report_with_pending_sql_puts_the_files_on_the_chip` | drop `_note_session_facts()` |
| `test_the_importer_finishing_clears_every_sql_chip` | drop `self._sql_owed.clear()` from `_module_sql_done` |
| `test_an_update_check_puts_the_count_on_the_row_it_counted` | treat `behind=None` as `1`; and drop the `reload_modules()` after the check |
| `test_busy_greys_every_row_button_and_gives_them_back` | drop `set_enabled_actions(False)` from `_set_busy` |
| `test_a_game_with_no_applier_has_no_live_row_button` | `set_enabled_actions(True)` at build time |
| `test_a_rows_install_button_installs_that_row` | make `_row_install` a no-op |
| `test_the_context_menu_on_an_uncatalogued_row_says_why_nothing_happened` | `_selected_row_is_uncatalogued` returns `False` |
| the five other re-seeded T41 tests | `installed = {}` in `reload_modules()` fails four of them; `badge = BADGE_INSTALLED` always fails two |

### Two defects found while wiring it, each now held by a test

1. **`_rebuild_finished` is not the rebuild's slot alone.** Three actions run through
   `rebuild_log` — `rebuild_server()`, `apply_database_updates()` and `adopt_as_imported()`
   — and all three reach `_rebuild_finished`. The ticket says "`_rebuild_owed` cleared when
   `_rebuild_finished` reports success", which taken literally makes a successful SQL run
   clear the banner and tell the user their module is live. `_rebuild_is_compile` is set at
   the press that starts a compile and cleared at each of the other two;
   `test_a_database_update_through_the_same_panel_clears_no_rebuild` is the only test that
   sees it, because the compile path is green either way.
2. **A test that agreed by accident.** `ModulesPanel.rows()` first reported the dict
   insertion order, which was the FILL order (installed half built first) rather than the
   drawn order — so `test_the_installed_row_is_drawn_above_the_ones_that_are_not` stayed
   green with the builder's sort deleted. The cards are now built in the order handed over
   and only then split into halves, and `test_the_panel_reports_rows_in_the_order_it_was
   _handed_them` hands the panel an order the builder would never produce.

### Deviations

* **A fifth signal on `ModulesPanel`.** The ticket names four; point 1 also says
  "right-click keeps `_show_module_context_menu` (moved to take the row's id)", and the menu
  it builds is about the applier and the clipboard, which the widget must not know. So
  `context_menu_requested(str, QPoint)` carries the row's id and a global position.
* **`FAMILY_TITLES` lives in `modules_panel.py`, not in `manifest_store.py`.** "Beside
  `FAMILY_FILES`" is honoured by importing it next to the mapping and by
  `test_every_family_has_a_title`, which fails the moment the two disagree. A card's label
  in the store would give the store, the fetcher and the catalog tests a dependency on how
  a tab titles a card.
* **Three reading methods the ticket does not name** — `available_open(family)`,
  `available_toggle(family)`, `installed_header(family)` — plus `rows()` and
  `empty_label`. Without them the collapse rule and the per-family count are not assertable
  except through pixel geometry.
* **`_module_done` now reloads after EVERY report**, where it used to reload only for a
  custom install or a dropped record. The chips and the banner come from the report just
  filed, and the reason the old rule was narrow (a reload lost the list's selection) is
  gone: `set_rows()` keeps the selection. Its only reader gone, `_custom_install_pending`
  is retired.
* **A remove drops the id from all three sets, per the ticket, including
  `_rebuild_owed`.** So removing a C++ module raises no banner even though the removal's own
  report says `rebuild_required`. Written down rather than quietly changed: the module is
  off the list, but the running server still has it compiled in. Worth a follow-up ticket.
* **A family whose manifests will not parse** used to be a row in the list
  (`!! could not load <kind>s: …`). There is no list; it is now `appendPlainText`ed into
  `module_report` (`MODULE_LOAD_FAILED`) — appended, not set, so it cannot take the report
  of the action the user just pressed with it.
* **`module_source.py` touched**, which the ticket does not list. Its refusal named
  "Install selected", a control this ticket removes — FACT 4's exact fault. One sentence and
  its test.
* **Not done, and in the ticket's "Not in scope":** no Update button (no per-module pull
  exists below this tab — still worth filing), no persisted rebuild marker, no version
  lines, no explanation panel under a row (the report line carries the chip's detail).

### Gate

`yt` in the worktree, last line:

```
4405 passed, 8 skipped, 23 deselected, 2 warnings in 136.69s (0:02:16)
```

`black --check yulon/ tests/` — 208 files unchanged. `ruff check yulon/ tests/` — all checks
passed. `mypy yulon/` — no issues in 100 source files. Nothing pushed; `--checks` on m910q
and the `yulon-win11` frames are the lead's.

## Report — round 2 (hand, Opus 5, 2026-09-13)

Codex returned REJECT on `hand-t42`. All four MUST FIX findings are fixed, every test it
asked for is written, and the two tests it called out for agreeing by accident are fixed
rather than re-worded. One of its sentences is contradicted below, with the line that
contradicts it; the finding it belongs to is real and is fixed anyway.

**Status:** ROUND 2 DONE — unit-gated in the worktree, not pushed.

### 1. A cancelled rebuild no longer clears the debt

`_rebuild_finished()` asked two questions and needed three. `LogPanel` reports a STOPPED job
as `ok=True, message="stopped"` **on purpose** — its worker's `except` branch says so in as
many words, because `request_stop()` kills the job's children and a terminated child exits
non-zero, and reporting that as a failure would put a refusal on screen for a button the
user pressed. So `ok` cannot mean "the compile finished".

The third question is `LogPanel.cancelled`, a property that exists for exactly this and
whose docstring says "the panel is the only thing that knows the Stop button was pressed".
`if ok and compiled and not self.rebuild_log.cancelled:`.

The message is deliberately NOT read: `message == "stopped"` would be the same defect in a
new place — an English string standing in for a fact the object already carries.

**Anything else keying off `run_finished`'s `ok` in the same wrong way: no.** There are two
consumers. `catalog_view._on_run_finished()` (`ui/catalog_view.py:864-880`) already reads
`self._log.cancelled` first, with a comment recording the install gate of 2026-08-23 where
a cancelled install was written into `state.json` as finished. `_rebuild_finished()`'s other
effects are `_set_busy(False)`, `_import_asked = False`, `_forget_the_adopt_reading()` and
`if not ok: action_failed.emit(...)` — the three resets are conservative in both directions
and the emit cannot fire on a stop, since a stop arrives as `ok=True`.

### 2. A shared manifest id no longer corrupts four things

Four keys were widened; none of them is a public name.

* `build_module_rows`: `installed_ids: dict[str, Manifest]` became `installed_keys:
  set[tuple[str, str]]` **and** `installed_manifests: list[Manifest]`, because the one dict
  was making two different mistakes — as a membership test it read the wrong FOLDER
  (`modules/` vs `sql_scripts/clones/`), and as the source of the dependency graph it
  DROPPED a manifest, so only the last-loaded one under an id contributed its `requires`.
* `ModulesPanel._rows`: keyed by `(family, id)`. Both widgets were always drawn, so the user
  saw two rows and the panel knew one.
* `ControllerView._manifests`: keyed by `(family, id)`, and `selected_manifest()` reads the
  family off the selected ROW. That object goes straight to `applier.install()`.
* Selection: a click now carries its widget (`_select_widget`), so it selects the row it
  happened on. `row()`, `select()` and every signal keep their bare-id shape — T43 reads
  them — and the ambiguity is settled once, in `_key_for()`, in `FAMILY_FILES` order.

`dependants` stays keyed by the required **id**, because `Manifest.requires` names an id and
never a family. That is the schema's own precision; inventing a family there would be a
guess. The consequence is deliberate and conservative: where an id really is in two
families, both rows are held by whatever requires it.

**One thing the fix deliberately does NOT change.** An id in both `ale` and `keg` reading
installed in both is correct, not a collision: `apply.CLONE_DIRS` gives those two families
the same directory and `apply.installed_clones()` returns the same set under both keys, in
as many words ("`ale` and `keg` share one folder, so they share its answer"). The
`(family, id)` keying preserves that, because `installed` carries the name under both keys.
T41's `bmah` case still passes unchanged.

The collision is latent, as Codex said: nothing in the store, the schema or the catalog
tests makes an id unique across families, and grepping the shipped `wow-wotlk` tree finds
none today. That is why "state why it is unique and guard it" was not taken — it is not
unique by construction, a guard that raised would blank the tab on a user manifest that
merely shares a shipped keg's name, and the widening cost four lines.

### 3. The owed chips reconcile against the disk

Chosen: **reconcile the three session sets in `reload_modules()`**, not gate the chips in
`build_module_rows()`. The reason is that the two readers disagree about their source — the
chips are built from the rows, the banner is built from `_rebuild_owed` directly — so gating
only the chips would have left the banner naming a module that is gone. Reconciling the sets
makes the two agree by construction, in one place.

`_forget_what_is_no_longer_installed()` runs only where the `installed_modules` seam
ANSWERED. A game without one reads as "nothing is installed anywhere", and treating that as
evidence would throw away everything the session has learned on the first reload after an
install — which is a test of its own.

Known limit, written down rather than discovered later: a manifest that leaves no clone can
never be reconciled as installed, so its owed facts are dropped on the next reload and only
the report line carries them. It cannot bite a shipped manifest today — every one of the 15
`wow-wotlk` manifests with `applied_by="db-import"` SQL is a `module` with a `source`, so
all of them leave a clone in `modules/` (counted through the manifest files, round 2) — and
`build.rebuild` is true only for `module`s, which always clone.

### 4. The uncatalogued row's menu offers the answer, not two dead actions

An uncatalogued row's menu now carries one entry, `WHY_UNCATALOGUED` ("Why is there no
Install or Remove?"), whose whole effect is `UNCATALOGUED_PRESS`, plus Copy Module ID. A
catalogued row keeps Install/Remove behind `_module_actions_allowed()`.

**One correction to the finding's wording, which does not change the fix.** It says "Round 1
gave those rows `UNCATALOGUED_PRESS` through the buttons". Round 1 gave them no buttons at
all — `RowWidget.__init__` builds one only `if data.catalogued`, and the round-1 test
`test_an_uncatalogued_row_has_no_buttons_at_all` asserts both are `None`. The menu was the
only place the sentence was reachable, which is what the ticket's point 4 asked for. The
substantive half of the finding is right and is what changed: two entries that name an
action and then explain that the action does not exist are two controls that look live.

**And a refactor the test forced, which the code is better for.** `QMenu.exec` is a Shiboken
slot: `QMenu.exec = <lambda>` is accepted and then ignored, and the real popup runs (measured
in round 2 — a test that patched it sat on a real nested event loop until it was killed, and
printed Qt's own `This plugin does not support grabbing the keyboard`). So the building and
the showing are now two methods: `_module_menu(module_id) -> QMenu` decides, and
`_show_module_context_menu()` selects and shows. Both branches are testable without a popup,
and the two lines only the real entry point runs are covered by a test that stubs
`_module_menu` to an empty `QMenu`, whose `exec` returns at once (measured).

### The two tests that agreed by accident

Both are fixed, not re-worded — and the fix uncovered a third.

* `ModulesPanel.rows()` really is insertion order, as Codex says. It is now documented as
  "the order `set_rows()` was handed them", and a new `drawn_rows()` walks the cards'
  layouts through `_FamilyCard.laid_out_rows()`.
* `test_the_installed_row_is_drawn_above_the_ones_that_are_not` now asserts both the drawn
  order and the built order, and its docstring says which mutation reaches which.
* `test_the_cards_draw_the_installed_half_above_the_available_half` hands the panel rows in
  an order the builder would never produce. This is the third accident: written first
  against builder-sorted rows, it stayed GREEN under `card.fill(widgets, [])` — because when
  the rows arrive already sorted the card's split and the builder's split are
  indistinguishable. Measured, then rewritten.
* A helper agreed by accident too, and the round-2 keying change exposed it: `_marked(view)`
  returned marked **ids**, and `test_a_family_is_marked_from_its_own_clone_folder...` then
  asked `modules_panel.row(id)` which family that was — a bare-id lookup, on the one case
  (`mod-ale`) where two families hold the id. It returns the widgets now.

### Not required: "clicking anywhere on a row selects it"

Both halves of Codex's option, because the two children differ.

* **A CHIP now selects its row** (`RowWidget._chip`). A chip press is a statement about that
  row and writes that row's sentence into the report; leaving the row unselected was the
  same gap `_row_install`/`_row_remove` already close by selecting first.
* **The GITHUB LINK deliberately does not.** It opens a browser at somebody else's website,
  and highlighting a row on this tab is not part of that. The claim is narrowed to say so,
  in `RowWidget.mousePressEvent`'s docstring and in the ticket's shape section above: a
  click on the row's BODY — name, description, paths, badge — selects it.

### Public names (T43 reads this module)

**Nothing public moved.** `ModuleRow`, `Chip`, `SessionState`, `build_module_rows`,
`FAMILY_TITLES`, `NOT_IN_CATALOG`, `BADGE_INSTALLED`/`BADGE_NOT_INSTALLED`, the chip label
constants, `chip_update_label`, `chip_required_by_label`, `NO_MODULES_NOTE`, `RowWidget` and
its attributes, and `ModulesPanel`'s `set_rows`, `row`, `rows`, `select`, `selected_id`,
`set_enabled_actions`, `available_open`, `available_toggle`, `installed_header`,
`empty_label` and all five signals keep their names and signatures.

Added: `ModulesPanel.drawn_rows()`, `ModulesPanel.selected_row()`,
`_FamilyCard.laid_out_rows()`, `ControllerView._module_menu()`,
`ControllerView._forget_what_is_no_longer_installed()`, `controller_view.WHY_UNCATALOGUED`.

Changed, both private: `ModulesPanel._rows` and `ControllerView._manifests` are keyed by
`(family, id)` instead of `id`. If T43 reads either dict directly it needs the tuple key;
nothing in `ModulesPanel`'s public surface exposes it. `ControllerView._custom_install_pending`
was already retired in round 1.

Test-only: the helper `_opened_menu` became `_row_menu`, and `_marked` returns widgets.

### Round-2 tests, each with its mutation

Every mutation was applied to a clean tree with `__pycache__` purged on both sides, run, and
seen to FAIL; the tree was restored afterwards. 19 mutations.

**Builder — `tests/test_modules_panel.py`**

| test | mutation |
| --- | --- |
| `test_an_id_in_two_families_is_installed_only_where_its_own_folder_says_so` | add every family's key to `installed_keys` (an id-keyed membership test) |
| `test_both_families_of_a_shared_id_contribute_their_own_requires` | build the graph from `{m.id: m for m in installed_manifests}.values()` |
| `test_the_sql_chip_is_absent_when_the_report_listed_no_files` | `if item_id in session.sql_owed:` instead of reading the value |

**Panel — `tests/test_modules_panel.py`**

| test | mutation |
| --- | --- |
| `test_an_id_in_two_families_gets_two_addressable_rows` | key `_rows` by the id alone |
| `test_clicking_the_second_family_of_a_shared_id_selects_that_row` | `widget.clicked.connect(self.select)` (the bare-id route) |
| `test_a_chip_press_selects_its_row_too` | drop the `clicked.emit` from `RowWidget._chip` |
| `test_the_cards_draw_the_installed_half_above_the_available_half` | `card.fill(widgets, [])` |
| `test_a_family_that_disappears_and_comes_back_keeps_its_toggle` | `self._open.clear()` at the top of `set_rows()` |
| `test_a_family_that_gains_its_first_installed_row_stays_open` | recompute the collapse rule on every `set_rows()` |

**View — `tests/test_controller_view.py`**

| test | mutation |
| --- | --- |
| `test_a_stopped_rebuild_keeps_the_debt_it_started_with` | drop `and not self.rebuild_log.cancelled` |
| `test_an_adopt_through_the_same_panel_clears_no_rebuild` | `_rebuild_is_compile = True` in `adopt_as_imported()` |
| `test_a_successful_removal_forgets_everything_owed_about_that_module` | drop `self._behind.pop(...)` from the remove branch |
| `test_a_clone_deleted_outside_the_app_takes_its_chips_and_the_banner_with_it` | drop the `_forget_what_is_no_longer_installed()` call |
| `test_a_game_with_no_installed_reader_keeps_what_this_session_learned` | reconcile unconditionally (drop `if reader is not None`) |
| `test_the_menu_on_an_uncatalogued_row_offers_the_answer_and_not_two_dead_actions` | `if False:` on the uncatalogued branch (round 1's shape) |
| `test_the_menu_on_a_catalogued_row_still_installs_and_removes` | connect the Install entry to `_module_action("remove")` |
| `test_a_right_click_selects_the_row_and_builds_its_own_menu` | drop the `select()` from `_show_module_context_menu` |
| `test_the_selected_manifest_of_a_shared_id_is_the_one_whose_row_is_selected` | look the manifest up by id alone, newest entry first (what `_manifests[manifest.id] = manifest` gives) |
| `test_the_installed_row_is_drawn_above_the_ones_that_are_not` | drop the installed-first split in `build_module_rows` |

One of these took two attempts to make bite and the first attempt is worth recording. The
shared-id manifest test first asserted only the `mod` row's answer — and an id-keyed dict is
last-write-wins with `mod` loaded after `module`, so it would have answered that one
correctly and the test would have passed over the live defect. It asserts both directions
now. (Ten-ways-a-test-proves-nothing, case 1: which single rule does this fixture violate?)

### Gate

`yt` in the worktree, last line:

```
4423 passed, 8 skipped, 23 deselected, 2 warnings in 151.06s (0:02:31)
```

`black --check yulon/ tests/` — 208 files unchanged. `ruff check yulon/ tests/` — all checks
passed. `mypy yulon/` — no issues in 100 source files. Nothing pushed; `--checks` on m910q
and the `yulon-win11` frames are still the lead's.

Round 1's own list of "not in scope" and the follow-up it names (a remove that reports
`rebuild_required` raises no banner, because the ticket's rule drops a removed id from all
three sets) are unchanged by this round.
