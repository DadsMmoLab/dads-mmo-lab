# doodad lane, second to fourth pass -- 2026-09-05, m910q

Raw captures behind five defects and three measurements. Nothing here is a summary;
each file is what the box printed.

| file | what it settles |
|---|---|
| `apply-check.txt` | `git apply --check` of the issue doc's fenced diff, before and after, against `mangos-classic 8ec338a1` and `mangos-tbc f82e7d67` -- the two commits the catalog pins and the doc names. The pre-lane fence exits 1 on both. |
| `fence-eol-apply-check.txt` | The third pass: the same fence in both line endings against four checkouts (the two pinned revs and each clone's newest `origin/master`). CRLF -- what a Windows checkout held before the `.gitattributes` pin -- exits 1 on all four; LF exits 0 on all four. |
| `insertion-only-presses.txt` | Three presses of an insertion-only hunk through `families/patch.py`, before and after the ordering fix. Before: three copies of the inserted line. |
| `state-files-m910q.txt` | Every `.yulon-install.json` on the box, and what each install's `data/.yulon-extract.json` vouches for. None records `patch-sources`; three record `build`. |
| `pre-lane-resume.txt` | A press on a state file in that shape, driven through `CmangosInstaller.run()`. Before: patched, then `skipping the compile`, then "installed and running". After: refused, nothing changed -- and the two neighbouring cases (images deleted, Docker silent) still patch. |
| `tortoise-doodadcheck.txt` | Why `wow-tortoise` carries no patch, run rather than read: its own extractor at `7c0fb278`, built and run against the Turtle client, `misspelt=0`. |
| `mutations-round4.txt` | Every mutation behind the fourth pass, as the script printed it: anchor occurrences asserted `== 1`, each substitution md5-checked on disk, `__pycache__` purged on both sides, the original restored and re-verified. `mutations-round4.py` is the script. M6 (the double stops refusing) survives alone -- the guard stops the tool before the double is asked -- and the M7 pair is what shows the double's refusal is load-bearing anyway: 7 RED with it, 3 without. |
| `extractor-dirty-output.txt` | The fourth pass, and the whole of what `extract.DIRTY_MARKERS` and `DIRTY_OUTPUT_TOOL` claim: `vmap_extractor`'s dirty-output `if` at both pinned CMaNGOS revs; a grep showing `ad`, `vmap_assembler` and `MoveMapGen` carry no such check at either; `TileAssembler` opening every output `"wb"`; the Tortoise extractor at the pinned `7c0fb278` having none; the two real installs on the box holding a `dir_bin` (50,938,121 and 31,072,203 bytes); and the issue fence re-checked -- LF exits 0 on both pinned trees, CRLF exits 1. `extractor-dirty-output.sh` is the script that printed it. |
