# T53 — `conflicts_with` was declared, parsed, tested, and never enforced

**Status:** FIXED 2026-09-13, on the owner's choice between this and dropping an entry.
**Filed:** by the lead, from Boatmurdered's 21-minute link failure.
**Branch:** `report/t52-challenge-modes-unbuildable` (rides with T52 — same catalog, same user).

## What the user paid

His rebuild reached the **link** step — every one of ~1900 objects compiled — and then:

```
/usr/bin/ld: modules/libmodules.a(ah_bot_loader.cpp.o): in function `Addmod_ah_botScripts()':
mod-ah-bot-plus/src/ah_bot_loader.cpp:18: multiple definition of `Addmod_ah_botScripts()';
mod-ah-bot/src/ah_bot_loader.cpp:12: first defined here
  ... multiple definition of `AuctionHouseBot::~AuctionHouseBot()'
  ... multiple definition of `AuctionHouseBot::Update()'
```

`mod-ah-bot-plus` is a FORK of `mod-ah-bot`: same class, same loader, same symbols. Both were
installed. 21 minutes to find that out.

## The part that makes it ours

The catalog already knew:

```json
mod-ah-bot        "conflicts_with": ["mod-ah-bot-plus"]
mod-ah-bot-plus   "conflicts_with": ["mod-ah-bot"]
```

- the field is in `manifest.py` and validated (it refuses self-reference),
- `tests/test_manifest.py` asserts it round-trips,
- **no code anywhere read it.** `grep conflicts_with yulon/` outside `manifest.py` returned one
  docstring mention and nothing else.

A declaration nothing acts on is worse than no declaration: to anyone auditing the catalog it
reads as a guarantee that was being kept. This is the third mechanism tonight that existed and
was never called — `purge.remove_tree()` (T49), `source.rev` (T52), and now this.

**And it is not one pair.** Four SQL mods name each other: `buff-mobs`, `xbuff-mobs`,
`nerf-mobs`, `baby-mobs`. Six entries, two groups, one unenforced field.

## What was considered and not done

Dropping `mod-ah-bot` from the catalog was the owner's first instruction. It was carried out,
and the cost surfaced: **11 tests** across 5 files use it as fixture data, and it is
AzerothCore's *official* AH bot while `-plus` is a third-party fork — dropping the official one
is a product decision rather than a bug fix. It also would have left the mobs foursome
unguarded. Put back to the owner with that evidence; he chose enforcement instead.

## The fix

`Applier._conflict_refusal()`, called from `install()` **before anything is written**, beside
the existing "one source, not two" refusal.

Two things it gets right, each with a test and a mutation:

1. **It asks the DISK, not the declaration.** Four mods naming each other would be permanently
   uninstallable if the check refused on *having* a conflict rather than on the conflicting
   clone being *present*.
2. **It searches every family's folder.** `CLONE_DIRS` puts a module in `modules/`, an ale in
   `ale_scripts/`, a mod in `sql_scripts/clones/`. A same-family check reads perfectly and
   silently misses a conflict across families. The docstring claimed this before a test did;
   the test came second, which is the wrong order and is recorded here for that reason.

What the user sees now, instead of 21 minutes:

```
mod-ah-bot-plus and mod-ah-bot cannot both be installed: they are alternatives to each
other, and the catalog records the conflict. mod-ah-bot is already here, at
modules/mod-ah-bot. Remove it first, or keep it and leave mod-ah-bot-plus out.
Nothing was changed.
```

## Evidence

Three tests, three named mutations, all killed
(`pyplan/gates/t53-conflicts-enforced-2026-09-13/mutations/`):

| | | |
|---|---|---|
| M1 | refuses on the declaration instead of the disk | the no-conflict-installed control fails |
| M2 | searches only the manifest's own family | the cross-family test fails |
| M3 | never refuses | the refusal test fails |

**The mutations were taken twice.** The first set passed against code mypy then rejected —
`installed_clones()` returns `dict[str, ...]` and `CLONE_DIRS` is keyed by a `Literal`, so the
first version indexed one with the other's keys. It worked at runtime, so every test and every
mutation agreed with it. Fixing it changed the loop M2 targets, so the evidence was re-taken
rather than reused.

## Still open

**Nothing greys the row in the Modules tab.** The refusal is correct and instant, but a user
still presses Install to discover it. The panel has the manifests and `installed_clones()`
already — showing a conflicting row as unavailable, with the reason, is a UI change that
belongs with the Modules tab rather than with the applier.

## Review round 1 — two findings, both real

Adversarial Codex review, 2026-09-13, on the pushed branch. Verdict **needs-attention**.

**HIGH — the LootPet rename was broken on install AND remove.** The manifest deployed a single
file with `rename: [["LootPet2.lua", "LootPet.lua"]]`. For a single-file deploy
`_deploy_target()` already returns the full destination FILENAME, so the applier's
`(target / old).replace(target / new)` built a path INSIDE the copied file:

```
NotADirectoryError: '.../lua_scripts/LootPet2.lua/LootPet2.lua'
                 -> '.../lua_scripts/LootPet2.lua/LootPet.lua'
```

Install raised after already copying, and never created `LootPet.lua`. Remove was independently
wrong: `_undeploy()` applies renames only on its directory branch, so the file survived it.

The rename existed to stop ALE loading two scripts. It would have stopped the install instead.
It was never run end to end; the one existing rename test uses a DIRECTORY src, which is the
shape that works.

Fixed at the source rather than in the applier: the script is now named `LootPet.lua` in
`pjerra/LootPet2`, and the manifest has no rename at all. `lootpet` was the only single-file
rename in the catalog — `activechat`, the other user of `rename`, deploys a directory. Adding a
half-working feature to core deploy logic for one consumer that does not need it was the wrong
trade.

**Guarded so it cannot recur:** `Deploy` now refuses a `rename` on a single-file `src` at load.
A wrong manifest fails to parse instead of failing part way through an install that has already
written to the server folder.

**MEDIUM — an empty leftover directory caused a false refusal.** `clone_names()` counts every
non-hidden directory as installed: no `.git`, no claim, no content. So an abandoned or empty
`modules/mod-ah-bot` blocked `mod-ah-bot-plus` forever, naming a module that is not really
there. Narrowed to directories that HOLD something — content, not `.git`, because a module
copied in from a folder has neither a checkout nor a claim and is exactly as present to the
linker. `_require_own_clone()` already drew this line.

Unreadable answers "occupied": a false refusal costs a message, a false pass costs a build that
dies at the linker an hour in.

Gate after: `4373 passed, 8 skipped`. ruff/black clean, mypy clean on all three.
