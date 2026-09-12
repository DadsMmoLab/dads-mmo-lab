# T43 — a Tuning tab: every module setting, with the file behind it editable

**Status:** FILED — spec ready; the hand starts next
**Filed:** 2026-09-12 by the lead, from the owner: "I also want a tuning tab for the modules like in my dml launcher" and "should be able to open .conf file for the modules inside yulon to edit there also."
**Mockup (owner-approved):** https://claude.ai/code/artifact/21ebf51e-a3f5-4f6b-90c9-dbac665c9bdf
**Hand:** Opus 5. Worktree `.claude/worktrees/t43`, branch `hand-t43` **from `hand-t42`**, not from `yulon-phase8b`: T42 rewrote the Modules tab and moved T41's row building into `ui/widgets/modules_panel.py`, and a Tuning tab built off the old shape would conflict with every line of it. Reviewer: Codex adversarial, two-round cap, then the lead closes by hand. Unit only; the live press on `yulon-win11` is the lead's.
**Do not edit** `yulon/ui/theme.py`, `icons.py`, `widgets/*_decorations.py` or `widgets/log_panel.py`, and import nothing from the decorations modules — upstream `Yulon` carries Baerthe's later passes on those files. Colours come from the `COLOR_*` constants `theme.py` already exports.

## Why

Two reports and one measurement.

Installing `mod-transmog` through Yu'lon's own `apply_module()` on the live WotLK
install (`yulon-win11`, 2026-09-12) reported:

```
SKIPPED: conf env/dist/etc/modules/transmog.conf: no value in the catalog for
  Transmogrification.Enable, Transmogrification.ShowSetDisclaimer,
  Transmogrification.UseCollectionSystem, Transmogrification.UseVendorInterface,
  Transmogrification.AllowHiddenTransmog — not written
```

That is not a transmog problem. Counted across `manifests/wow-wotlk`: **24 manifests
declare conf keys, 107 keys in total, and 73 of them carry no `default`.** Every one of
those is a setting the catalog names, the applier declines to write, and a user has no
way to reach from inside the app. `apply.py:2141` is deliberate about it — "A key the
catalog names with no `default` is a step nobody takes… which value belongs in a user's
core configuration is the catalog's sentence to write" — and this ticket is where the
catalog finally writes it.

## Prior art — read before designing anything

The DML launcher already ships this, and the owner's rule is to read `rust-main` first.

| file on `origin/rust-main` | what it settles |
|---|---|
| `crates/dml-wow/data/tuning-registry.json` | 13 entries, 4 modules. Per entry: `key, backend, module, label, explain, type, min, max, value, default, installed, file`. Types `bool` (6), `int` (6, with min/max), `list` (1); backends `conf` (8), `lua` (5) |
| `launcher/src/lib/ModuleTuning.svelte` | the guided half, 723 lines: the restart banner, the "applied live" note, per-write apply semantics |
| `launcher/src/lib/ModuleFiles.svelte` | the raw half, 382 lines: file picker, readonly files, a live "does this still look like a .conf?" lint gating Save behind a one-off confirm, a backup per save |
| `crates/dml-wow/src/tuning.rs`, `tests/tuning_parity.rs`, `tests/tuning_write_parity.rs` | the read/write parity the Rust side holds itself to |

`ModuleFiles.svelte:124-128` carries the warning this ticket must not lose: a raw rewrite
of a **bind-mounted** conf needs the containers `recreate`d, not a world-only restart, and
promising the fast restart there "would be a promise we cannot keep."

## Decisions (owner, 2026-09-12)

1. **Its own tab**, beside Modules — not a section inside T42's redesign.
2. **The manifests are the source of truth.** No second registry: the tuning metadata goes
   on the `ConfKey` entries the manifests already declare (`manifest.py:146`). One file
   describes a module's install AND its tuning.
3. **Scope v1: every manifest that already declares conf keys** — all 24, all 107 keys.
4. **The raw `.conf` editor ships in v1**, in the same tab.
5. **Out of v1: the `lua` backend.** Five of DML's 13 keys patch a deployed `.lua` in
   place; Yu'lon's ALE story differs enough to want its own ticket. A `lua`-backed key is
   listed and shown read-only, with a line saying why, never silently dropped.

## The schema change

`ConfKey` (`yulon/manifest.py:146`) gains four optional fields. Optional, because 107 keys
cannot all be authored in one ticket and a key with none of them must still work exactly
as it does today.

```python
class ConfKey(_Strict):
    key: str
    default: str | None = None
    note: str | None = None
    # T43:
    label: str | None = None      # "Enable the Beastmaster NPC"
    explain: str | None = None    # the author's own sentence
    type: Literal["bool", "int", "list", "text"] | None = None
    min: int | None = None        # int only
    max: int | None = None        # int only
```

**A key with no `type` renders as a text box.** Never a switch, never a spinner. This is
the safety rule of the whole ticket: a wrong `type` writes a wrong value into somebody's
live server, and "unknown" must degrade to the control that can express anything. The
same goes for `min`/`max` absent on an `int` — no clamping is invented.

`label` absent falls back to the key itself; `explain` absent shows nothing rather than
inventing prose.

## Definition of done, in order

Each numbered item is a commit with its own tests. TDD: write the test, watch it fail,
make it pass, name the mutation you ran against it.

1. **The model.** `ConfKey` gains the four fields; `_Strict` still rejects unknown ones.
   A manifest with none of them parses byte-identically to today. A `min` or `max` on a
   non-`int` key is a parse error — it is meaningless and would mislead a reader.
2. **The reader.** A pure function, no Qt: `tuning.rows_for(entry, server_dir)` →
   ordered rows carrying `(module_id, module_name, family, file, key, label, explain,
   type, min, max, default, current, installed)`. `current` is read from the deployed
   conf on disk; a file that is missing or unreadable gives `current=None`, never a
   fabricated value, and the row still lists with its default. Rows come only from
   INSTALLED modules — T41/T42's `apply.installed_clones()` decides that, and an
   uninstalled module has no file to tune.
3. **The writer.** `tuning.write(file, {key: value})`: rewrites only the named keys in
   place, preserving comments, blank lines, key order and the file's existing line
   endings. A key the file does not contain is appended under a comment naming the app
   and the date. **A backup is taken before every write** and its path is returned.
   A value that fails its own type (a non-number for `int`, out of `min`/`max`) is
   refused before a byte is written, naming the key.
4. **The raw editor's guard.** `tuning.lint(text)` → the "does this still look like a
   `.conf`?" verdict: every non-blank, non-comment line matches `Key = Value`. Reports
   the first offending line number and its text. It gates Save behind one confirm; it
   never blocks.
5. **The apply rule per row.** `conf` on a bind-mounted file → containers recreated;
   `conf` on a file read at world start → restart; a compiled module whose source
   changed → rebuild owed. The rule is computed, not typed into the view, and is the
   chip the mockup shows.
6. **The tab.** `_build_tuning_tab()` in `controller_view.py`, the panel itself in a new
   `ui/widgets/tuning_panel.py` on T42's pattern (a pure builder plus widgets, so the
   rows are testable without Qt). Guided cards grouped by module on the left, the file
   picker and editor on the right, stacking on a narrow window. Changed rows are marked
   and say what they changed from. Save is per module card; Revert restores from the
   backup.
7. **The enrichment.** `label`, `explain`, `type`, `min`, `max` written onto the keys of
   all 24 manifests, sourced from each module's own `.conf.dist` comments and README —
   the author's words, not invented ones. Where DML's registry already covers a key
   (Beastmaster, Learn Spells, Sit Means Rest, Unlimited Ammo) its `label`/`explain` are
   carried over verbatim, so the two launchers say the same thing. A key whose meaning
   the source does not state gets `type` omitted and no `explain` — a text box and
   silence, which is honest.

## Evidence the ticket owes

- The gate's last line, and the mutation named for every test.
- One screenshot of the tab on `yulon-win11` against the real WotLK install, showing a
  real module's real settings — the lead takes it.
- A before/after of one key: the conf on disk, the value changed through the tab, the
  conf on disk again, and the backup beside it.

## Known follow-ups, not this ticket

- The `lua` backend (decision 5).
- `worldserver.conf` and `playerbots.conf` are core files, not module files. The raw
  editor can list them read-only in v1; making them tunable is a bigger question about
  who owns core configuration.
- T42's own follow-up stands: removing a compiled module raises no rebuild banner.

## Report (hand, Opus 5, 2026-09-13)

**Status:** IMPLEMENTED — all seven points done, unit-gated in the worktree, not
pushed. Reviewer, the m910q `--checks` gate, the CHANGELOG line, the merge, the
`yulon-win11` screenshot and the before/after of one key are the lead's.

### Commits (`hand-t43`, from `9faaf5d1`)

| sha | what |
| --- | --- |
| `57167682` | point 1 — the model |
| `eb794608` | point 2 — the reader |
| `24bc6af0` | point 3 — the writer |
| `a04b42be` | point 4 — the raw editor's guard |
| `217a7b1a` | point 5 — the apply rule |
| `7e11ea4e` | point 6 — the tab |
| `ebcddf15` | two defects found while reading the sources for point 7 |
| `28ab0919` | point 7 — the enrichment |
| `f39b5cae` | a defect the full gate found after point 7, plus the two tree-wide ledgers |

### Files and classes

**`pylauncher/yulon/manifest.py`** — `ConfKey` gains `label`, `explain`, `type`
(`Literal["bool","int","list","text"] | None`), `min`, `max`, all optional, plus
`_bounds_belong_to_an_int` (a bound off an `int` is a parse error, and so is
`min` above `max`). `manifests/schema/manifest.schema.json` regenerated.

**New: `pylauncher/yulon/tuning.py`** — no Qt, no applier, no subprocess.
`Backend` / `backend_of()`, `TuningRow` (frozen; `.editable`), `rows_for()`,
`conf_value()`; `TuningError`, `check()`, `backup()`, `write()`, `restore()`,
`backups_of()`; `LintIssue`, `lint()`, `lint_sentence()`; `ApplyRule`,
`BOUND_INTO_THE_CONTAINERS`, `APPLY_SENTENCES`, `file_rule()`, `apply_rule()`,
`apply_sentence()`, `worst()`. Sentences: `LUA_IS_NOT_IN_V1`,
`NOT_A_CONF_FILE`, `MORE_THAN_ONE_FILE`, `NOT_ONE_KEY`, `LINT_SENTENCE`.

**New: `pylauncher/yulon/ui/widgets/tuning_panel.py`** — pure half `TuningCard`,
`build_tuning_cards()`, `control_kind()`, `starting_value()`, `value_note()`,
`bool_words()`, and the constants `CHANGED_FROM`, `NOTHING`, `NOT_IN_THE_FILE`,
`NOTHING_TO_TUNE`, `NARROW_WIDTH`, `BOOL_WORDS`; widget half `RowEditor`,
`CardWidget`, `TuningPanel`. Nothing imported from the decorations modules;
colours are `theme.py`'s `COLOR_*` constants. `theme.py`, `icons.py`,
`*_decorations.py` and `log_panel.py` are untouched.

**`pylauncher/yulon/ui/controller_view.py`** — `_build_tuning_tab()`,
`reload_tuning()`, `_tuning_files()`, `_tuning_spec()`, `save_tuning()`,
`revert_tuning()`, `open_tuning_file()`, `reload_tuning_file()`,
`save_tuning_file()`; members `tuning_panel`, `tuning_report`,
`refresh_tuning_button`, `_tuning_rows`, `_tuning_newline`; constants
`TUNING_SAVED`, `TUNING_NOTHING_CHANGED`, `TUNING_REFUSED`, `TUNING_REVERTED`,
`TUNING_NO_BACKUP`, `TUNING_FILE_SAVED`, `TUNING_FILE_FAILED`,
`TUNING_LINT_CONFIRM_TITLE`, `TUNING_CORE_FILE`, `TUNING_CORE_FILES`.
`reload_modules()`'s two readings were **factored out**, not copied, into
`_installed_clones()` and `_load_manifests()`, so the two tabs cannot disagree
about what is installed. `_set_busy()` and `_module_done()` now drive both tabs.

### The apply-rule computation as implemented

`tuning.apply_rule(row, *, in_clone=False)`, and `tuning.file_rule(file)` for a
raw file with no row behind it (`apply_rule` delegates to it, so the chip on a
card and the note under the editor cannot drift):

1. `row.read_only_reason is not None` → **`read-only`**. Four ways to get one: a
   deployed `.lua` (decision 5), a `file` that is not a file (`acore_ale.
   paragon_config (DB table)`), a glob, and a `key` that is not one key.
2. `in_clone` → **`rebuild`** — the setting is in the module's own source tree,
   so the running worldserver has the old value compiled into it.
3. the file is under a prefix in `BOUND_INTO_THE_CONTAINERS` (`env/dist/etc/`)
   → **`restart`**: compose binds that directory, so the containers read it off
   the user's own disk at world start.
4. otherwise → **`recreate`**: the running container is using the image's copy,
   so saving changes the disk and not the server.

`BOUND_INTO_THE_CONTAINERS` is pinned to
`catalog/installers/wow-wotlk/native/base.yml.tmpl` by
`test_the_bound_directory_is_the_one_this_apps_compose_actually_binds`, so
moving the mount fails a test instead of quietly turning every "restart" on the
tab into `ModuleFiles.svelte:124-128`'s refused promise. `worst()` prices a
card's Save at its most expensive row.

### Tests and their mutations

79 mutations. Each was applied to a clean tree with `__pycache__` purged on both
sides, run, seen to FAIL, and the tree restored. The runner asserts the
mutation actually changed the file — two early ones silently did not match and
reported a green run that meant nothing.

**Point 1 — `tests/test_manifest.py` (6 tests, 4 mutations)**

| test | mutation |
| --- | --- |
| `test_a_conf_key_carries_its_label_explain_type_and_bounds` | (M4) `label` made required |
| `test_a_conf_key_with_none_of_them_still_parses_and_answers_none` | (M4) the same |
| `test_a_bound_on_a_key_that_is_not_an_int_is_a_parse_error` (4 cases) | (M1) `if False:` on the validator's first clause; (M2) `self.type not in ("int", None)` — only the `None` case sees this one |
| `test_an_int_key_may_carry_one_bound_or_none_at_all` | (M4) |
| `test_a_type_the_schema_does_not_name_is_refused` | pydantic's own `Literal` |
| `test_an_int_key_whose_min_is_above_its_max_is_a_parse_error` | (M3) `if False:` on the satisfiability clause |

**Point 2 — `tests/test_tuning.py` (11 tests, 10 mutations)**

| test | mutation |
| --- | --- |
| `test_only_an_installed_module_contributes_rows` | (M6) every catalogued id counts as installed |
| `test_a_row_carries_every_field_the_tab_draws` | (M5) `label=key.key` — the fallback never applies |
| `test_a_key_with_no_label_is_drawn_under_its_own_key` | (M5) |
| `test_a_conf_that_is_not_there_gives_no_current_value_and_still_lists` | (M10) `_read` without its `except OSError` |
| `test_a_conf_that_cannot_be_read_gives_no_current_value` | (M10) |
| `test_the_last_active_assignment_wins_and_a_commented_one_is_not_read` | (M8) `return` on the first match; (M9) the column-0 guard dropped |
| `test_a_lua_backed_key_is_listed_read_only_and_says_why` | (M12) `backend_of` never answers `lua` |
| `test_a_key_whose_file_is_not_a_file_is_read_only_and_never_touches_the_disk` | (M14) the `other`/glob guard dropped, so the reader opens `acore_ale.paragon_config (DB table)` |
| `test_a_key_whose_file_is_a_glob_is_read_only_because_there_is_no_one_file` | (M11) the glob branch removed; (M14) |
| `test_rows_are_ordered_by_family_then_catalog_then_file_then_key` | (M13) rows sorted by key |
| `test_a_family_is_read_from_its_own_clone_folder` | (M6); (M7) a family matched against any folder's set |

**Point 3 — `tests/test_tuning.py` (13 tests, 14 mutations)**

| test | mutation |
| --- | --- |
| `test_only_the_named_keys_move_and_every_other_line_is_untouched` | (M22) the quote-preserving branch removed (via the quoted test); covered end-to-end by M18/M19 too |
| `test_the_last_active_assignment_is_rewritten_and_a_commented_copy_is_left` | (M18) `_last_active` returns the first match; (M19) its column-0 guard weakened to `!= "#"` |
| `test_a_key_the_file_does_not_carry_is_appended_under_a_dated_comment` | (M21) the dated comment dropped; (M28) an unfound key skipped instead of appended |
| `test_a_crlf_file_comes_back_crlf_and_an_lf_file_stays_lf` | (M20) every file written LF |
| `test_a_key_appended_to_a_crlf_file_is_appended_with_crlf` | (M20); (M28) |
| `test_a_backup_is_taken_before_the_write_and_its_path_is_returned` | (M16) the backup taken after the write |
| `test_a_value_that_fails_its_own_type_is_refused_before_a_byte_is_written` | (M15) the check loop removed; (M17) the backup taken before the checks; (M23) the upper bound unchecked; (M24) the lower bound unchecked |
| `test_one_bad_value_in_a_batch_writes_none_of_the_batch` | (M15) |
| `test_no_bound_is_invented_where_the_catalog_states_none` | (M24) |
| `test_a_key_with_no_type_accepts_anything_because_it_is_a_text_box` | (M25) a key with no `type` refused |
| `test_a_bool_key_takes_both_spellings_its_files_use_and_nothing_wider` | (M26) `yes` added to `_BOOL_WORDS` |
| `test_a_quoted_value_keeps_its_quotes` | (M22) |
| `test_a_backup_can_be_put_back` | (M16); (M27) `restore` moves instead of copying |

**Point 4 — `tests/test_tuning.py` (9 tests, 7 mutations).** DML's
`conf-lint.test.ts` carried over case for case.

| test | mutation |
| --- | --- |
| `test_a_clean_conf_has_nothing_to_say` | (M29) section headers flagged |
| `test_a_line_with_no_assignment_is_reported_with_its_number_and_text` | (M32) zero-indexed line numbers |
| `test_a_line_whose_key_is_empty_is_reported` | (M31) `find("=") < 0` |
| `test_an_empty_value_and_a_value_holding_an_equals_sign_are_both_fine` | (M31) |
| `test_line_numbers_are_one_indexed_across_crlf_and_lf` | (M32); (M33) the `\r` left on the reported text |
| `test_trailing_whitespace_does_not_make_a_line_a_problem` | (M33) |
| `test_an_ini_section_header_is_valid_conf_and_a_broken_one_is_not` | (M29); (M30) the header pattern unanchored |
| `test_the_confirm_names_the_first_offending_line_and_nothing_else` | (M34) the LAST issue named |
| `test_a_clean_text_asks_nothing` | (M35) a clean text still asks |

**Point 5 — `tests/test_tuning.py` (7 tests, 6 mutations)**

| test | mutation |
| --- | --- |
| `test_a_setting_this_app_does_not_write_owes_nothing` | (M36) the read-only clause removed |
| `test_a_conf_the_containers_read_off_the_users_disk_needs_a_restart` | (M39) the bound prefix renamed to a directory nothing binds |
| `test_a_conf_outside_every_bind_needs_the_containers_recreated` | (M38) every conf called a restart |
| `test_a_setting_in_the_modules_own_source_tree_needs_a_rebuild` | (M37) the clone clause removed |
| `test_the_bound_directory_is_the_one_this_apps_compose_actually_binds` | (M39) |
| `test_every_rule_has_a_sentence_and_no_sentence_has_no_rule` | (M41) the `read-only` sentence deleted |
| `test_a_card_is_priced_at_its_most_expensive_row` | (M40) `min` instead of `max` |

**Point 6, panel — `tests/test_tuning_panel.py` (38 tests, 22 mutations)**

| test | mutation |
| --- | --- |
| `test_rows_are_grouped_into_one_card_per_module_in_the_order_they_arrived` | (M42) cards sorted by id |
| `test_a_card_names_every_file_its_rows_live_in_once_each` | (M43) a file listed once per row |
| `test_a_card_is_priced_at_its_most_expensive_row` | (M44) the card priced at its first row |
| `test_a_card_of_nothing_but_read_only_rows_owes_nothing` | (M44) |
| `test_a_key_gets_the_control_its_own_type_earns_and_never_a_wider_one` (9 cases) | (M45) a key with no `type` gets a switch; (M46) one bound is enough for a spinner |
| `test_a_row_starts_at_what_the_file_says_and_falls_back_to_the_default` | (M47) the default outranks the file |
| `test_a_key_the_file_does_not_carry_says_so_instead_of_looking_set` | (M48) the note never shows |
| `test_a_bool_key_draws_a_switch_and_reads_back_the_files_own_spelling` | (M49) every switch writes `1`/`0` |
| `test_a_bool_key_in_a_zero_one_file_comes_back_as_zero_or_one` | (M49) |
| `test_a_bounded_int_draws_a_spinner_inside_its_own_bounds` | (M55) the spinner keeps Qt's own 0–99 range |
| `test_an_unbounded_int_draws_a_text_box_so_nothing_clamps_it` | (M46) |
| `test_a_key_with_no_type_draws_a_text_box` | (M45) |
| `test_a_read_only_row_has_no_control_and_says_why` | (M50) a read-only row still gets a control |
| `test_a_changed_row_is_marked_and_says_what_it_changed_from` | (M51) the mark never hides; (M79) `changed` is only the moved flag |
| `test_a_row_that_was_not_in_the_file_says_it_changed_from_nothing` | (M45) |
| `test_a_switch_over_a_key_the_file_never_carried_is_not_changed_until_it_is_moved` | (M78) `changed` ignores whether the control was moved |
| `test_a_spinner_over_a_key_the_file_never_carried_is_not_changed_until_it_is_moved` | (M78) |
| `test_a_text_box_typed_into_and_cleared_again_is_not_changed` | (M79) |
| `test_a_card_offers_only_the_keys_that_changed` | (M52) every key on the card offered |
| `test_a_card_whose_rows_are_all_read_only_offers_no_save` | (M53) `if True:` on the editable gate |
| `test_a_cards_save_and_revert_name_their_own_module` | (M52) |
| `test_the_panel_draws_a_card_per_module_and_finds_one_by_id` | (M42) |
| `test_a_panel_with_nothing_to_tune_says_so` | (M60) the empty note never hides |
| `test_the_panel_keeps_the_users_edits_out_of_a_redraw` | (M45) |
| `test_busy_disables_every_card_and_gives_them_back` | (M54) `set_enabled_actions` leaves the buttons live |
| `test_the_file_box_lists_the_files_it_was_handed_and_names_the_one_picked` | (M61) the picker emits nothing |
| `test_a_read_only_file_is_shown_and_cannot_be_saved` | (M56) `read_only` ignored |
| `test_an_editable_file_lints_live_and_never_blocks` | (M57) the live lint unwired |
| `test_the_file_save_hands_up_the_text_that_is_in_the_box` | (M58) the file NAME emitted instead of the text |
| `test_a_narrow_window_stacks_the_two_halves` | (M59) the split never stacks |

**Point 6, view — `tests/test_controller_view.py` (15 new tests, 9 mutations)**

| test | mutation |
| --- | --- |
| `test_the_tuning_tab_lists_the_settings_of_installed_modules_only` | (M69) every catalogued module gets a card |
| `test_a_setting_shows_what_the_deployed_conf_says_and_never_invents_one` | (M78) |
| `test_a_game_with_nothing_installed_says_so_instead_of_an_empty_tab` | (M60) |
| `test_saving_a_card_writes_what_changed_and_names_the_backup` | (M16), (M52) |
| `test_a_save_that_changed_nothing_writes_nothing_and_says_so` | (M52), (M78) |
| `test_a_key_the_conf_never_carried_is_written_for_the_first_time` | (M28) |
| `test_a_value_that_fails_its_type_is_refused_and_the_file_is_untouched` | (M62) `write()` called with no `spec`, so nothing is type-checked |
| `test_revert_puts_the_conf_back_from_the_backup` | (M27) |
| `test_a_revert_with_no_backup_says_so_rather_than_doing_nothing` | (M67) the empty-backup branch says nothing |
| `test_the_file_picker_lists_the_deployed_confs_and_opens_the_first` | (M63) the core files listed whether or not they exist |
| `test_the_servers_own_conf_is_listed_read_only_and_says_why` | (M64) a core file opens editable |
| `test_a_raw_save_that_still_looks_like_a_conf_asks_nothing` | (M66) `answer is not StandardButton.Yes` (T33's trap) |
| `test_a_raw_save_that_stopped_looking_like_a_conf_asks_once_and_a_no_writes_nothing` | (M65) the lint never asked; (M66) |
| `test_busy_greys_the_tuning_saves_and_gives_them_back` | (M68) `_set_busy` leaves the tuning saves live |
| `test_a_card_says_what_applying_its_change_costs` | (M38), (M39) |

**The shorthand rule — `tests/test_tuning.py` (2 tests, 4 mutations)**

| test | mutation |
| --- | --- |
| `test_a_key_that_names_a_family_of_keys_is_listed_read_only` (6 cases) | (M70) the clause removed; (M71) `*` allowed in the key pattern; (M73) the reason computed per FILE instead of per key |
| `test_an_ordinary_key_is_still_writable` | (M72) the pattern narrowed so `BeastMaster.Enable` is refused |

**Point 7 — `tests/test_manifest.py` (4 tests, 4 mutations)**

| test | mutation |
| --- | --- |
| `test_a_bool_key_the_catalog_also_writes_agrees_with_its_own_default` | (M74) `mod-ah-bot`'s `EnableSeller` default set to `maybe` |
| `test_an_int_key_the_catalog_also_writes_parses_and_sits_in_its_own_bounds` | (M75) `LearnSpells.MaxLevel` default set to `800`, above its own max |
| `test_every_key_the_tuning_tab_can_write_says_what_kind_of_setting_it_is` | (M76) `mod-talentbutton`'s only key loses its `type` |
| `test_a_label_is_a_name_and_not_the_authors_whole_sentence` | (M77) a transmog label replaced with a sentence |

### Four tests that agreed by accident, caught by their own mutation

1. **`test_the_last_active_assignment_wins_and_a_commented_one_is_not_read`** had
   the commented and indented copies ABOVE the real one, where "last active
   wins" masks them — it passed with the column-0 rule deleted. They are below
   it now.
2. **`test_a_changed_row_is_marked_and_says_what_it_changed_from`** asserted the
   `changed` property and not the MARK, so `setVisible(True)` unconditional
   stayed green. It now asserts the label is hidden again.
3. **`test_a_key_whose_file_is_not_a_file_is_read_only…`** asserted
   `current is None`, which is also what a failed open answers — it could not
   tell "never opened" from "opened and failed". `_read` is monkeypatched to
   raise now, so opening the path at all fails the test.
4. **`test_a_narrow_window_stacks_the_two_halves`** resized a HIDDEN widget,
   which is sent no resize event, so it passed with the whole rule deleted. The
   panel is shown first.

### The enrichment: 22 of the 24 manifests, 95 of the 107 keys

Sourced by fetching each module's own `conf/*.conf.dist` (and, for
`accountwide`, its scripts and README) from the repository its manifest names,
on 2026-09-13. Every `explain` is the author's sentence. DML's registry wording
is carried **verbatim** for the four modules it covers.

**Enriched (22):** `ale/accountwide`, `ale/sitmeanrest` (DML), `ale/unlimitedammo`
(DML), `kegs/bmah`, `mods/xp-rates`, `modules/mod-1v1-arena`,
`mod-ah-bot`, `mod-ah-bot-plus`, `mod-ale`, `mod-aoe-loot`, `mod-challenge-modes`,
`mod-dungeon-master`, `mod-individual-progression`, `mod-learn-spells` (DML),
`mod-mount-scaling`, `mod-npc-beastmaster` (DML), `mod-player-bot-level-brackets`,
`mod-profession-progression`, `mod-quest-loot-party`, `mod-solocraft`,
`mod-talentbutton`, `mod-transmog`.

**Not enriched (2), each for a stated reason:** `ale/paragon` — its five values
live in `acore_ale.paragon_config`, a database table, and the repository's
README describes the system but says nothing about the five keys; the author's
silence is kept. `modules/mod-autobalance` — its one declared "key" is
`AutoBalance.Enable.*`, which is eleven real keys, so there is nothing single to
label or type.

**The twelve keys left with no `type`** are pinned BY NAME in
`test_every_key_the_tuning_tab_can_write_says_what_kind_of_setting_it_is` (a
count would pass on the wrong twelve): `paragon`'s five, and the seven catalog
shorthands. Every one of the twelve is a key the tab refuses to write anyway.

**Type decisions worth reading:** every multiplier and rate is `text` and not
`int` — `Rate.XP.*`, `SoloCraft.Spellpower.Mult`, `SoloCraft.Stats.Mult`,
`IndividualProgression.Vanilla*Adjustment`, `AOELoot.Range`,
`MountScaling.Ground.SpeedPerLevel`, `Hardcore.XPMultiplier`,
`AutoFillChance` — because a spinner cannot hold `2.5` and there is no `float`
in the schema. `AuctionHouseBot.MinutesBetweenSellCycle` is `text` because its
author accepts `3:15` as well as `3`. `min`/`max` appear on eight keys only,
and every one of those ranges is stated by its own source (four from DML's
registry, `ProfessionProgression.ExpansionStage` from the author's own
enumeration of 0/1/2, and `LearnSpells.MaxLevel` from DML).

**`AOELoot.Message` gets a label and no `explain`.** Its `.conf.dist` comment is
a copy of `AOELoot.Group`'s — "Enables area loot if the player is in a group" —
which is not what the key does. Carrying it verbatim would have put a wrong
sentence in front of a user; silence is the honest reading.

### Three defects found on the way, each now held by a test

1. **Six of the 107 "keys" are shorthand for a GROUP of keys** —
   `AutoBalance.Enable.*` (eleven real keys), `MountScaling.Ground.Journeyman.*`,
   `MountScaling.Flying.Expert.*`, `MountScaling.Flying.Artisan.*`,
   `AuctionHouseBot.ListProportion.*`, `common/rare/ultraRare_*_price`,
   `FillRateCommon / FillRateRare / FillRateUltra` (seven in all). Harmless
   until this ticket, because nothing ever WROTE a key with no default; with the
   tab, saving one appends a line the module never reads under a comment saying
   Yu'lon put it there. Now read-only with `tuning.NOT_ONE_KEY`.
2. **`mod-dungeon-master` named three keys the module does not have.** Its own
   `conf/mod_dungeon_master.conf.dist` spells them `DungeonMaster.Scaling.
   LevelBand` (line 58), `DungeonMaster.Rewards.BaseGold` (116) and
   `DungeonMaster.Roguelike.Enable` (265); the manifest had all three without
   the prefix. Fixed.
3. **An untouched switch reported a change nobody made.** A checkbox cannot draw
   "no value": after point 7 typed them, all five of `mod-transmog`'s `bool`
   keys — none of them in the conf, none with a default — opened as unchecked
   boxes whose value read `0` against a start of `""`, so `value() != start`
   called every one of them changed and a Save on an untouched card wrote five
   keys. A spinner had the same hole one control along. `RowEditor.changed` is
   now "moved AND different". **The full gate is what found this** — the view
   tests were green against the pre-enrichment catalog and only the whole suite
   ran both halves together.

### Deviations

* **`rows_for(manifests, installed, server_dir)`, not `rows_for(entry, server_dir)`.**
  A `CatalogEntry` carries no manifests (`catalog.py:1491` — it has
  `has_manifests: bool` and nothing else), so an `entry` signature would have to
  build a `ManifestStore` inside what the ticket asks to be a pure function.
  This is T42's `build_module_rows(manifests, installed, session, client_dir)`
  shape exactly, which the hand was told to follow.
* **`TuningRow` carries two fields the ticket's tuple does not name**,
  `backend` and `read_only_reason`. Decision 5 requires a `lua` key to be
  "listed and shown read-only, with a line saying why", and without these the
  panel cannot obey it. `installed` is on the row as the ticket names it and is
  `True` on every row v1 produces, because the filter and the field are the same
  reading; `test_only_an_installed_module_contributes_rows` asserts both halves.
* **`lint()` returns every issue, not only the first.** `lint_sentence()` is what
  names the first, which is what the confirm shows. Returning the tuple lets the
  test carry DML's own multi-issue case over unchanged.
* **An `int` with fewer than both bounds draws a TEXT BOX, not a spinner.**
  `QSpinBox` always clamps — its own default range is 0–99 — so a spinner over an
  unbounded `int` would be this app inventing the bound the ticket forbids.
  Five of the eleven `int` keys are drawn this way.
* **The Tuning tab borrows the Modules tab's puzzle icon.** `icons.py` is a file
  this ticket must not edit and it has no `tuning` key; the fallback for an
  unknown name is the SERVER icon, which would collide with the Server tab.
* **`file_rule()` was added to `tuning.py` in point 6**, after point 5's commit,
  so the raw editor's note and the card's chip come from one computation.
* **`reload_modules()` was refactored**, which no point asks for: its two
  readings became `_installed_clones()` and `_load_manifests()` so both tabs
  share them. Behaviour is unchanged and T42's tests all still pass.
* **`test_spine.py` and `pyplan/write-ledger.md` were edited** — both are
  tree-wide invariants that fail on any new folder listing or write, and this
  ticket adds one listing (`tuning.backups_of`) and four writes.
* **TDD order, honestly:** points 1–5 and the panel half of point 6 were written
  test-first and seen RED. The VIEW half of point 6 was wired first and its 15
  tests written immediately after; each is backed by a named mutation run and
  seen to fail, which is the evidence, but it was not red-first.
* **Not done, and named in the ticket as out of scope:** the `lua` backend
  (decision 5), `worldserver.conf`/`authserver.conf`/`playerbots.conf` as
  anything but read-only, and the live half on `yulon-win11`.

### Gate

`yt` in the worktree, last line:

```
4518 passed, 8 skipped, 23 deselected, 2 warnings in 145.55s (0:02:25)
```

`black --check yulon/ tests/` — 212 files unchanged. `ruff check yulon/ tests/` —
all checks passed. `mypy yulon/` — no issues in 102 source files. Nothing pushed.

## Report — round 2 (hand, Opus 5, 2026-09-13)

**Status:** all six MUST-FIX findings addressed, plus the NOT-required one
decided and written down. `33586d57` on `hand-t43`. Not pushed.

Finding 1 sent me to **AzerothCore's own config parser**
(`src/common/Configuration/Config.cpp:305-331`, fetched from `master`
2026-09-13), which settles more than the finding asked and settles one part of
it the other way. It is worth reading before the table below:

```cpp
line = Acore::String::Trim(line, in.getloc());        // 307
if (line.empty()) continue;                           // 310
if (line[0] == '#' || line[0] == '[') continue;       // 313
auto const equal_pos = line.find('=');                // 316
auto entry = Trim(line.substr(0, equal_pos));         // 324
auto value = Trim(line.substr(equal_pos + 1, npos));  // 325
value.erase(remove(value.begin(), value.end(), '"')); // 327
if (IsDuplicateOption(entry)) continue;               // 330  <- FIRST wins
```

### What changed

| # | finding | what was done |
| --- | --- | --- |
| 1 | the whole line was rebuilt as `key = value` | `_rewrite()` keeps the leading whitespace, the key as the file spells it, the separator exactly as written (`K    =    `), the quotes and the trailing `\r`, and replaces only the value |
| 2 | `errors="replace"` in, UTF-8 out | a strict decode in `write()` (raises `TuningError`, before the backup, so a refusal leaves the directory untouched), in `_read()` (`current` is `None`, never U+FFFD) and in `open_tuning_file()` (empty, read-only, Save dead) |
| 3 | no atomic write | `_atomic_write()`: a `.yulon-tmp` sibling and `os.replace`, with the temp removed if anything raises |
| 4 | two backups in one second collide | the stamp carries microseconds in a fixed-width field and steps to the next free one; a name sort is still a time sort, which `backups_of()` depends on |
| 5 | `worst()` priced a mixed card too cheaply | `worst()` is gone. `owed()` returns EVERY job, most expensive first; `owed_sentence()` says all of them; `TuningCard.rule` → `rules` |
| 6 | the refusal was per file, not per card | `save_tuning()` checks every value of every file before opening any of them; `write()`'s own check stays, because it is a public seam |

### Two things the finding did not ask for, found by following it

**`conf_value()` and the writer were reading the wrong line, twice over.** The
module started with `party.read_conf()`'s rule — column 0, last-wins — which is
Lua's rule applied to a `.conf`. Against `Config.cpp`:

* line 307 trims BEFORE anything is decided, so an **indented** assignment is
  live. The old rule answered `None` for one;
* line 330 skips every later copy, so the **first** occurrence wins. The tab
  showed a value the running server does not use, and `_last_active()` rewrote
  a line the server ignores — the value on screen would not have moved and Save
  would have looked broken. `apply._set_conf_key()`'s `subn(..., count=1)` is
  the first match and had it right all along.

`conf_value()` is now the core's rule and `lua_value()` keeps Lua's, with
`value_in(text, key, backend)` picking. `party.py` is **not** changed: its rule
is right for the file it reads there and this ticket does not own it — worth its
own ticket that `party.read_conf`'s docstring generalises a Lua measurement.

### Where I disagree, with evidence

**Finding 1's trailing-comment half is wrong, and implementing it would write a
value the user never asked for.** `K = old # keep this`:

* `Config.cpp:316` takes the FIRST `=`, and :325 takes **the rest of the line**
  as the value. There is no trailing-comment syntax — a `#` is a comment only
  when it is the first character of the trimmed line (:313). So the running
  server's value for that key is literally `old # keep this`.
* Preserving `# keep this` while replacing `old` therefore writes the value
  `new # keep this` into a live conf. The person who moved the control asked
  for `new`.

`test_a_hash_after_the_value_is_part_of_the_value_and_goes_with_it` asserts
both halves — that `conf_value()` reads the whole thing as the value, and that
the write replaces the whole thing. The spacing half of the finding is
implemented exactly as asked, and the `K = old = fallback` case now loses its
second half **for the same reason**, not by accident: that half is the value.

For what it is worth, the prior art agrees: DML's own `.conf` writer
(`crates/dml-core/src/conf.rs:171-230`) rewrites the whole line to a canonical
`{key} = {value}`, rewrites EVERY duplicate rather than one, and drops the
`\r` on the lines it touches. Yu'lon now preserves strictly more than DML does.

### The NOT-required one, decided

`build_tuning_cards()` still calls `apply_rule(row)` with no `in_clone`, and
the rebuild branch still has no production caller. Reason, in the code and in a
test: `ConfFile.file` is relative to the SERVER dir and `ConfFile` has no
`in_clone` field — only `Patch` does — so no manifest can point a tuning row at
a file inside a module's source tree. The parameter stays because the schema
carries the concept one model along, and
`test_a_conf_file_can_never_be_a_clone_file_so_the_rebuild_branch_has_no_caller`
fails the day `ConfFile` gains the field, which is the day the branch acquires
a caller.

### Tests and their mutations (round 2)

Numbering continues from round 1. Same method: applied to a clean tree,
`__pycache__` purged both sides, run, seen to FAIL, restored — and the runner
asserts the mutation actually changed the file.

| test | mutation |
| --- | --- |
| `test_the_first_assignment_wins_in_a_conf_and_a_commented_one_is_not_read` | (M80) `conf_value` takes the last assignment again; (M81) the column-0 rule put back — the indented key reads `None` |
| `test_a_comment_and_a_section_header_are_both_lines_that_say_nothing` | (M82) the `[` arm of `_is_conf_comment` removed; (M84) the `#` arm removed |
| `test_a_lua_script_keeps_luas_own_rule_and_not_the_cores` | (M83) `value_in` sends every backend to `conf_value` |
| `test_the_line_keeps_its_indentation_and_its_spacing_around_the_equals` | (M85) the line rebuilt canonically; (M86) the separator normalised to `" = "` while the indent is kept |
| `test_the_whole_value_is_replaced_including_a_second_equals` | (M85) |
| `test_a_hash_after_the_value_is_part_of_the_value_and_goes_with_it` | (M87) a trailing `#…` kept as a "comment" — which is Codex's request, and this is the test that refuses it |
| `test_the_first_assignment_is_rewritten_and_the_duplicate_below_it_is_left` | (M88) the writer targets the last assignment; (M85) |
| `test_a_file_with_no_trailing_newline_keeps_none` | (M85) |
| `test_a_crlf_line_keeps_its_spacing_and_its_carriage_return` | (M85); (M86) |
| `test_a_conf_that_is_not_utf8_is_refused_rather_than_rewritten` | (M90) the writer reads with `errors="replace"` again |
| `test_a_conf_that_is_not_utf8_has_no_current_value_rather_than_a_replaced_one` | (M91) `_read` replaces undecodable bytes |
| `test_a_conf_that_is_not_utf8_opens_empty_and_read_only` (view) | (M99) the `UnicodeDecodeError` arm of `open_tuning_file` never catches |
| `test_a_write_that_fails_leaves_the_file_whole` | (M92) a plain truncating `open(path, "w")`; (M93) the temp file left behind on failure |
| `test_two_saves_inside_one_second_are_two_backups` | (M94) the stamp back to whole seconds; (M95) a taken name reused |
| `test_a_card_names_every_job_its_rows_owe` | (M96) `owed` returns only the dearest; (M97) `read-only` counted as a job |
| `test_a_card_that_needs_a_rebuild_and_a_recreate_names_both` | (M96) |
| `test_owed_lists_each_job_once_most_expensive_first_and_drops_read_only` | (M96); (M97) |
| `test_a_card_whose_rows_cost_two_different_things_says_both` (panel) | (M100) the card keeps only its first row's cost; (M101) the sentence names only the dearest job |
| `test_a_card_names_every_job_its_rows_owe_and_a_read_only_row_owes_none` (panel) | (M100) |
| `test_a_multi_file_card_writes_nothing_when_the_second_files_value_is_bad` (view) | (M98) the card validated file by file again — the first file lands |
| `test_a_key_that_names_a_family_of_keys_is_listed_read_only` | now 8 cases, not 6: `MountScaling.Flying.Expert.*` and `.Artisan.*` were untested and the docstring said six. Mutations M70–M73 as in round 1 |
| `test_a_conf_file_can_never_be_a_clone_file_so_the_rebuild_branch_has_no_caller` | (M102) `in_clone` added to `ConfFile` — the day the branch gets a caller |

**One test that agreed by accident, again.** The first version of
`test_a_section_header_is_not_read_as_a_setting` routed the `[` arm through
`rows_for` and passed with that arm deleted: a `[` binds to the first token, so
`[worldserver]` strips to `[worldserver` and the exact key match excludes it
anyway. The same is true of the `#` arm. Both are now asserted directly on
`_is_conf_comment`, and both docstrings say the call is redundant where it sits
and why it is kept — rather than claiming to guard something it does not.

**Three prose-coupled assertions de-coupled** (`test_a_save_that_changed_nothing…`,
`test_a_revert_with_no_backup…`, `test_a_raw_save_that_stopped_looking_like_a_conf…`):
each now compares against the constant or the function that produces the
sentence, so a reworded message is not a test failure and a message that stops
being produced at all still is.

### Ledger and gate

`pyplan/write-ledger.md`: `tuning.py::write::open(w)` became
`tuning.py::_atomic_write::open(w)`, and two rows were added
(`_atomic_write::os.replace`, `_atomic_write::unlink`); the backup row now
records the microsecond stamp and the conf row the strict decode.

`yt` in the worktree, last line:

```
4537 passed, 8 skipped, 23 deselected, 2 warnings in 146.75s (0:02:26)
```

`black --check yulon/ tests/` — 212 files unchanged. `ruff check` — all checks
passed. `mypy yulon/` — no issues in 102 source files. Nothing pushed.
