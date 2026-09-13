# T59 — the Loot Pet manifest on `Yulon` cannot install

**Status:** FIXED on the branch; gate and review below.
**Filed:** 2026-09-14 by the lead, from our own review of the T53 follow-up.
**Branch:** `fix/lootpet-install-is-broken-in-upstream`, from `upstream/Yulon` 936beda5.

## What happens

#160 merged a `lootpet` manifest that deploys a single file and renames it:

```json
"deploy": [{ "src": "LootPet2.lua", "dest": "env/dist/etc/modules/lua_scripts/",
             "rename": [["LootPet2.lua", "LootPet.lua"]] }]
```

Driven through the real `Applier.install()` on pure `upstream/Yulon` 936beda5, fake clone:

```
install: NotADirectoryError [Errno 20] Not a directory:
  '<server>/env/dist/etc/modules/lua_scripts/LootPet2.lua/LootPet2.lua'
  -> '<server>/env/dist/etc/modules/lua_scripts/LootPet2.lua/LootPet.lua'
after install: ['LootPet2.lua']
after remove : []
```

## The mechanism

For a file `src` and a `dest` ending in `/`, `_deploy_target()` returns the full destination
FILENAME. `_deploy()` then runs `(target / old).replace(target / new)`, which builds a path
inside that file. The copy has already happened, so the install fails with the script in place
under its old name. Remove with the same manifest does take it away (measured above).

`rename` was only ever written for a directory `src`, and the one test of it uses a directory.
No test had driven a shipped manifest through `install()`.

## Exposure

No release carries it. `v0.8.65-Public`, `v0.8.66/67/68-fixtest` all ship the earlier
`Brytenwally/Lootpet` entry (`LootPet.lua`, no rename), checked by `git show <tag>:...`.
It is on `upstream/Yulon` from #160 (28ce0de8, 2026-09-13 22:16 UTC) onward.

## The fix

1. **Repin at the source.** `pjerra/LootPet2` 4b1c3ed renames the file to `LootPet.lua` in the
   repository. Same blob (184464ab) as `LootPet2.lua` at 56d15536, so the script is byte-identical
   and an install over the earlier `Brytenwally` entry overwrites rather than doubles.
2. **Refuse the shape at load.** `Deploy` rejects `rename` on a single-file `src`, so a manifest
   like this fails in CI instead of part way through a user's install.
3. **A catalog test** walks every single-file deploy in `manifests/wow-wotlk/ale/`, as shipped,
   through `install()` and `remove()`.
4. **Restores the empty-directory narrowing** from the closed #157: an empty leftover folder no
   longer counts as an installed conflicting module; a folder with content still does.

## Decided out of scope — owner, 2026-09-14

An `obsolete` manifest field (delete files a previous version deployed) was drafted and cut. Its
only beneficiary is a machine that ran `Yulon` tip after #160 and pressed Install on Loot Pet.
That machine keeps `lua_scripts/LootPet2.lua` beside the new `LootPet.lua`, and ALE loads both.
Manual step, stated in the PR: delete `env/dist/etc/modules/lua_scripts/LootPet2.lua`.

The draft commit (8d8ae002) also said the field was "wired into both paths". It was not: no
code in `apply.py` read it.

## Definition of done

1. The shipped `lootpet` manifest installs and removes through the real applier.
2. A single-file `rename` fails to load.
3. A named mutation per test, each killed.
4. Gate: full narrow suite, black, ruff, mypy on linux/win32/darwin.
5. `/adv-review` on the committed branch BEFORE the PR.
