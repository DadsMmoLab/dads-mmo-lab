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
