# doodad lane, second pass -- 2026-09-05, m910q

Raw captures behind three defects and one measurement. Nothing here is a summary;
each file is what the box printed.

| file | what it settles |
|---|---|
| `apply-check.txt` | `git apply --check` of the issue doc's fenced diff, before and after, against `mangos-classic 8ec338a1` and `mangos-tbc f82e7d67` -- the two commits the catalog pins and the doc names. The pre-lane fence exits 1 on both. |
| `insertion-only-presses.txt` | Three presses of an insertion-only hunk through `families/patch.py`, before and after the ordering fix. Before: three copies of the inserted line. |
| `state-files-m910q.txt` | Every `.yulon-install.json` on the box, and what each install's `data/.yulon-extract.json` vouches for. None records `patch-sources`; three record `build`. |
| `pre-lane-resume.txt` | A press on a state file in that shape, driven through `CmangosInstaller.run()`. Before: patched, then `skipping the compile`, then "installed and running". After: refused, nothing changed -- and the two neighbouring cases (images deleted, Docker silent) still patch. |
| `tortoise-doodadcheck.txt` | Why `wow-tortoise` carries no patch, run rather than read: its own extractor at `7c0fb278`, built and run against the Turtle client, `misspelt=0`. |
