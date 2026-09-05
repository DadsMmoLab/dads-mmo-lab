# The `yulon-ubuntu` install logs, rescued 2026-09-04 01:26

These are the ONLY copies of the evidence behind several Phase 7 entries, and they lived in
`/home/pk/` on the `yulon-ubuntu` VM. 7.1's Ubuntu gate and 7.2's gate both begin by restoring
that VM to its `clean-ssh` checkpoint, which wipes the home directory — so they were copied
here first, before any `Restore-VMSnapshot` ran.

Original names are kept, because the checklist cites them by those names (`~/vanilla2.log`,
`~/tortoise.log`).

| file | what it is |
|---|---|
| `vanilla.log`, `vanilla2.log`, `vanilla3.log` | the WoW Vanilla installs behind 7.5 |
| `tortoise.log` … `tortoise8.log` | the Tortoise installs behind 7.6 |
| `killtest.log`, `killtest2.log`, `resume2.log` | the mid-build kill and the resume that followed it |
| `retry.log`, `retry2.log` | the 2026-09-03 attempt at 7.5's forced vmap retry (bug-checklist §37) |
| `tbc-7.4a.log` | the TBC run that established TBC cannot reach `build` without a client |
| `claude-activity.log` | the box's own record of what was announced on it |

`client-dl.log` (8.0 MB) was deliberately NOT copied: it is a download progress log, not a gate.

## The copy is faithful, checked rather than assumed

Three counts the checklist quotes were re-run against THESE files, not against the originals:

* `grep -c -i retry vanilla2.log` → **0** — the reading 7.5 rests on, that no retry path was
  ever entered.
* `grep -c '^core updates:' vanilla2.log` → **172**, and
  `grep -c "continuing because 'core updates' is on_error: warn" vanilla2.log` → **171**.
  That is 7.5's "172 attempted, exactly one applied, 171 warned past", reproduced here.

Note for anyone grepping these later: `grep -c 'ERROR 1054' vanilla2.log` answers **510**, not
171. It is not a contradiction and it is not a count of files — a single failing update file
prints the error more than once. The file-level question is the `on_error: warn` line above.
