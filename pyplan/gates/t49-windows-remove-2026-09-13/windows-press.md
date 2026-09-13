# T49 — the Windows press

**Box:** `yulon-win11` (Hyper-V, already running when this was taken), Python 3.11.9,
repo at `6956e0b2`. Script: `winpress.py`, beside this file.
**Taken:** 2026-09-13 09:10 (box clock).

Not a simulation: the fixture is a real checkout made by real `git init` + `commit` +
`gc --aggressive`, so the read-only files are the ones git itself writes.

```
scratch: C:\Users\pk\AppData\Local\Temp\t49-jkqwhru3
read-only files git left: 4
   .git\objects\info\commit-graph
   .git\objects\pack\pack-d9126bd5ba7477bf6a4768670246cf96e28aeccd.idx
   .git\objects\pack\pack-d9126bd5ba7477bf6a4768670246cf96e28aeccd.pack
   .git\objects\pack\pack-d9126bd5ba7477bf6a4768670246cf96e28aeccd.rev

--- shutil.rmtree (what shipped before T49) ---
RESULT: FAILED -> PermissionError: [WinError 5] Access is denied:
        '...\with-shutil\.git\objects\info\commit-graph'
        still on disk: True
        entries left behind: 14  <-- half-deleted

--- yulon.rmtree.remove_tree (T49) ---
RESULT: OK, gone = True
```

Three things this settles that the POSIX test could not:

1. **The reported error is reproduced on Windows**, `[WinError 5] Access is denied`, on a
   `.git` tree — the same shape as the user's
   `...\ale_scripts\sod\.git\objects\pack\pack-2623....idx`.
2. **`rmtree` really is non-atomic here.** 14 entries were left behind. The ticket asserted
   the half-deleted checkout; this counts it.
3. **`remove_tree()` finishes**, and the log line shows it taking the retry path
   (`would not delete; clearing read-only flags and retrying`) rather than succeeding by luck
   on the first `rmtree`.

Note `commit-graph` is read-only too, not only the pack — so the first stop is not always
inside `objects\pack`. Any code that special-cased the pack directory would still fail.
