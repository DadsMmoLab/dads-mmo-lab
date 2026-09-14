# T59 — the Loot Pet manifest on `Yulon` cannot install

**Status:** FIXED on the branch; gated, reviewed twice, PR pending.
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

## Gate — 2026-09-14

`1 failed, 4662 passed, 8 skipped, 23 deselected` on 3.13 and on 3.11. The one failure is
`test_the_forget_button_appears_even_when_the_status_poll_cannot_reach_docker`, which fails on
pure `upstream/Yulon` too and is fixed by #162. ruff, black clean; mypy clean on linux, win32 and
darwin. Lines in `pyplan/gates/t59-lootpet-broken-in-upstream-2026-09-14/gate-py3*.txt`.

The first gate run failed a second test, and it was ours: the port from #157 dropped the
`_conflict_refusal` entry in `test_spine._ACCOUNTED_LISTINGS`. Restored from f2c9e8ee. That run
is the entry's mutation.

## Review round 1 — one HIGH, declined on the owner's decision

Codex adversarial, on the committed branch, before any PR. *"Upgrade leaves the previously
deployed script behind"* — the stranded `LootPet2.lua`. True, and the case cut above.

## Review round 2 — one MEDIUM, taken

*"Catalog test fabricates the repinned source tree."* The fake clone is built from the manifest,
so a pin that does not exist, or lacks the named `src`, still passes. Also: the test's name said
every ALE manifest while it skips directory deploys.

- Renamed to `test_every_shipped_single_file_ale_deploy_installs_and_removes`; its docstring
  now names what it does not prove.
- The pin read from the repository by hand: `pin-provenance.txt` in the gate folder.
  `LootPet.lua` at 4b1c3ed is blob 184464ab, the same blob as `LootPet2.lua` at 56d15536.
- A test that asks the repository whether every pinned `rev` holds its `src` belongs to the
  whole catalog (`sitmeanrest` is pinned the same way), so it is the pinning ticket, not this one.
