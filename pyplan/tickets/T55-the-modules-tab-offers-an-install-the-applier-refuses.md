# T55 — the Modules tab offers an Install the applier will refuse

**Status:** IN PROGRESS.
**Filed:** 2026-09-14 by the lead. The WIP (93b381f8, kept as `backup/t55-wip-before-restack`)
predates this file; the rule is ticket first, and this is the catch-up.
**Branch:** `feat/conflicts-in-the-modules-tab`, stacked on T59's
`fix/lootpet-install-is-broken-in-upstream` (#164), because both change `_conflict_refusal()`.

## What happens

T53 made `Applier.install()` refuse a module whose declared `conflicts_with` partner is already
on disk (`mod-ah-bot` against `mod-ah-bot-plus`). The safety is right and lives in the applier.
The tab was left offering the press: Install is enabled, and for `mod-ah-bot-plus` the press
first opens its prompt dialog (a prompt with no default), and only after the user answers does
the applier say no.

## The fix

1. **One reading of the rule.** `apply.conflicting_installed(manifest, installed)` answers every
   `(id, family)` listed as installed that the manifest conflicts with. The applier refuses on
   the first whose folder holds anything; the tab greys on the first at all.
2. **The row.** `ModuleRow` gains `installable` / `install_reason`. A not-installed catalogued
   row whose conflict is installed carries a `conflicts with <name>` fact chip, and its Install
   button is disabled with the reason as tooltip -- the same shape as `required by` on Remove.
3. **The other route.** The context menu's Install goes through `_module_action("install")`.
   It refuses a non-installable row with the reason in the report BEFORE the prompt dialog.

## Decided by the lead, for the owner to check

**The tab and the applier may differ in one direction.** T59 taught the applier that an EMPTY
leftover folder is not an install. The tab's `installed` comes from `installed_clones()`, which
counts every directory, so for that one case the tab greys a row the applier would let through.

Kept deliberately. The tab is then stricter, never looser; the conflicting row it points at is
drawn Installed with a Remove, and removing an empty folder is allowed, so the way out is on
screen. Closing the gap means opening every clone folder on each reload and changing what
"Installed" means on every badge -- a different ticket if it is ever wanted.

## Definition of done

1. Row tests: conflict installed → not installable with a reason naming it; conflict merely
   declared → installable (the buff/nerf/baby-mobs four).
2. Widget test: the Install button is disabled for that row, and stays disabled after a job
   re-enables actions.
3. View test: the menu route refuses before any prompt is asked.
4. A test pinning the one-direction difference.
5. A named mutation per test; full gate on 3.13 and 3.11; `/adv-review` before the PR.
