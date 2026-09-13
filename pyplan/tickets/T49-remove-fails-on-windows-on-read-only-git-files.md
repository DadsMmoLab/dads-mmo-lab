# T49 — removing a module fails on Windows, and the fix was already in the tree

**Status:** FILED — user-reported, reproduced from the report, fix located.
**Filed:** 2026-09-13 by the lead, from Boatmurdered's DM (read on the owner's instruction).
**Branch:** `fix/windows-remove-readonly-git`, cut from `upstream/Yulon` so it can be released
on its own rather than waiting behind PR 153.

## What the user saw

```
remove sod FAILED: [WinError 5] Access is denied:
'C:\wow-server-playerbots\ale_scripts\sod\.git\objects\pack\pack-26232faf5a65a80928d1c7f577d0dfe92bb364d9.idx'
```

He was trying to remove his modules to test a clean rebuild. He could not.

## The mechanism

Git writes pack files and loose objects **read-only** on Windows. `shutil.rmtree` has no
special handling for `FILE_ATTRIBUTE_READONLY`: it stops on the first one and raises
`PermissionError` (WinError 5). Every module clone has a `.git/objects/pack/*.idx`, so every
remove of a cloned module fails on Windows, at the first pack it reaches.

It is worse than a refusal: `rmtree` is **not atomic**. By the time it hits the pack it has
already deleted an unknown amount of the checkout, so the folder is left half-deleted — and
the next attempt starts from that.

## The fix already exists, and nothing outside its own file calls it

`purge.py:639` `remove_tree()` was written for exactly this, in these words:

> The retry is for Windows, where git writes packs and loose objects read-only:
> `shutil.rmtree` stops on the first of them and leaves a half-deleted checkout, which is
> worse than an undeleted one.

It does `rmtree` → `_clear_read_only()` → `_remove_unenterable()` → `rmtree` → assert gone.
`grep` says no module outside `purge.py` imports it. **Five** delete sites use a bare
`shutil.rmtree` on a path that can hold a `.git`:

| site | what it deletes |
|---|---|
| `apply.py:1305` | the module clone, on remove — **this is the reported failure** |
| `apply.py:1869` | an undeploy path |
| `git.py:723` | a non-git leftover at a clone destination |
| `git.py:1145` | a clone destination |
| `module_source.py:520` | the destination of a custom-module folder copy |

## Why it cannot simply import `purge`

`purge` transitively imports `apply` and `git` (measured, by walking the import graph), so
`apply -> purge` is a cycle. And `purge._undeletable()` is not reusable copy: it says
*"EVERYTHING ELSE IN THE PLAN HAS ALREADY BEEN REMOVED — the containers, the volumes it
named, and the images"*, which is true of an uninstall and false of removing one module.

So the **mechanism** moves to a leaf module and the **message** stays with each caller.

## Definition of done

1. **`yulon/rmtree.py`**, a leaf (`os`, `shutil`, `stat`, `pathlib`, `yulon.log` only):
   `remove_tree()` plus the two helpers, raising `TreeRemovalError` with a neutral sentence
   that names the path and the cause.
2. **`purge.remove_tree()` becomes a wrapper** that calls it and re-raises as `PurgeError`
   with `_undeletable()` unchanged — its message and its three tests must not move.
3. **The five sites call the leaf**, each converting the failure into its own surface's error.
4. **A test per site** that a read-only file inside a `.git` does not stop the delete, driven
   through the real caller, not through `remove_tree()`.

## Evidence the ticket owes

- The gate's last line and a named mutation per test.
- The POSIX shape of the stop is what CI can exercise (a cleared write bit); the Windows
  spelling is owed a press on `yulon-win11` against a real clone, which is also the only way
  to prove the reported error is gone.
