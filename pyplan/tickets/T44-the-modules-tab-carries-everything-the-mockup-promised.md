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
