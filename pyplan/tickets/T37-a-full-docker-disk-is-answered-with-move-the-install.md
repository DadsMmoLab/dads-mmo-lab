# T37 — a full Docker disk is answered with "install to a drive that has room", which the user already did

**Status:** OPEN — reviewed (Codex: REJECT, both findings fixed in one round) and green on the lead's checkout; owes a gate
**Filed:** 2026-09-12 by the lead, from Doc's report in `#-yulon` (Discord, 2026-09-12 06:04 and 08:05 CEST) with his install log DM'd the same morning.
**Hand:** the lead. Unit only; no box — the refusal is a pure function of `Facts` and every threshold in it is already testable without a daemon or a disk.

## The report

Doc, in `#-yulon` at 08:05 CEST:

> So, is there a way to make this app point to anything but the C: drive? I had this issue with the DML stuff as well.

His log (`yulong install log.txt`, DM'd 06:05 CEST), the whole preflight block:

```
[23:59:31] Installing WoW WotLK into E:\wow wotlk
[23:59:31] Checking Docker.
[23:59:36] [pass]   Docker: the daemon answered
[23:59:36] [pass]   memory: Docker's VM has 15.5 GB
[23:59:36] [warn]   compiler jobs vs memory: this build compiles with 21 parallel jobs at about 2…
[23:59:36] [refuse] free space on Docker's disk: 16 GB free, and the install needs 40 GB Free so…
[23:59:36] [pass]   free space on the server folder: 1336 GB free
[23:59:36] [pass]   the server folder: E:\wow wotlk looks usable
[23:59:36] [pass]   sharing the folder with Docker: a container can read E:\wow wotlk
[23:59:36] [pass]   SELinux: not a Linux host, so it does not apply
[23:59:36] [pass]   the server's ports: nothing else is using them
```

## Root cause

**The refusal is correct. The sentence under it is not.**

Docker Desktop keeps its images, layers and build cache in its own disk image, which on Windows lives under `C:` whatever folder the user picks for the server. 16 GB free there against a 40 GB floor is a true refusal, and letting a 40-minute compile start into it would fail later and worse.

What was wrong is the remedy. `_space_check()` (`preflight.py:671` before this change) handed **one** string to **both** free-space rows:

```python
"Free some space, or install to a drive that has room, then try again."
```

For the `the server folder` row that is exactly right. For the `Docker's disk` row it names the one action that cannot possibly help — and Doc had already taken it, which his own log says twice in the two rows directly beneath the refusal (`1336 GB free`, `E:\wow wotlk looks usable`). He read it, found it described what he had done, and concluded the app could only be pointed at `C:`.

The macOS-host branch, `_space_check_macos_bounded()`, carried the same sentence for the same row and the same reason: `Docker.raw` does not move because the install folder does.

This is one more of [[defects-live-between-the-parts]] — neither check is wrong on its own; the defect is the string they share, which has no owner and no test asserting it belongs to either.

## The fix

`_space_remedy(what, facts)` picks the advice per row, because which drive is short decides which action is true:

| row | remedy |
|---|---|
| `the server folder` | `FOLDER_SPACE_REMEDY` — free space, or install to a drive that has room. Unchanged. |
| `Docker's disk` | `_docker_disk_remedy(facts)` — names Docker Desktop → Settings → Resources → Advanced → **Disk image location** on Windows and macOS; on Linux names *both* `data-root` in `/etc/docker/daemon.json` and Docker Desktop's setting, because `detect()` reports `"linux"` inside WSL too (see the review below). Says outright that moving the install will not help. |
| `ONE_VOLUME_SPACE` | one drive holds both, so all three actions are offered. Matched explicitly; an unknown row gets a logged, claim-free default. |

`_space_check_macos_bounded()`'s refusal calls `_docker_disk_remedy()` too.

**No threshold moved.** The 40/60 pair is `rust-prior-art.md` §3 verbatim and this module's own docstring says it is not where they could be changed; they are data in `catalog.json`. Whether 40 GB is the right floor is a separate question and wants a measurement, not an edit here.

## Evidence

`tests/test_preflight.py::test_a_full_docker_disk_is_not_answered_with_move_the_install`, built from Doc's exact numbers (`data_root_free=16 GiB`, `server_dir_free=1336 GiB`, `same_volume=False`, `platform_id="windows"`). It asserts the refusal is the Docker row alone, that its remedy does not contain "install to a drive that has room", and that it names "Disk image location"; then the mirror case — a short server folder on a roomy data root — to hold the folder row's own advice in place.

RED before the change, on the right line:

```
E       AssertionError: Free some space, or install to a drive that has room, then try again.
E       assert 'install to ...hat has room' not in 'Free some s...n try again.'
```

Green after: `tests/test_preflight.py` 56 passed; full suite 4340 passed, 31 skipped.

## Codex adversarial review (2026-09-12): REJECT, then fixed

Two findings taken, both real; the round closed in one pass.

**1. The Linux remedy was wrong for a reachable configuration.** `detect()` returns
`"linux"` inside WSL — its own docstring says so — so a launcher running in a WSL
distro whose `docker` is Docker Desktop's through WSL integration reached the Linux
branch and was told to edit `/etc/docker/daemon.json`, which that daemon never
reads. The same dead end the ticket exists to remove, one platform over. The branch
now names both routes, because this module cannot tell the two daemons apart:
`platform.docker_desktop_data_root()` answers `/var/lib/docker` for either
(`platform.py:3531`), which means under WSL integration the *measurement* is of the
wrong filesystem too. **That is a separate defect and is not fixed here** — it moves
a number, not a sentence, and wants a box that can prove which filesystem Docker
Desktop actually grew. Filed below.

Codex also named Docker rootless (`~/.config/docker/daemon.json`) and the containerd
image store as cases `data-root` does not cover. Not verified against a live daemon
by this project, so the sentence does not claim to be exhaustive — it names where to
look rather than asserting a single file is the only answer.

**2. `_space_remedy()`'s final `return` was unguarded**, so any row name added later
would have been handed the one-volume sentence — an assertion that two paths share a
drive, false under every row but `ONE_VOLUME_SPACE`. Now matched explicitly, with a
logged, claim-free default for a row with no remedy written for it.

Not taken: finding 3 (the `warn`/`unchecked` branches) was NOT FOUND by the reviewer
and confirmed sound. Finding 5 confirmed the Windows/macOS Docker Desktop path is
right.

**Tests added for the three rows the first pass left untested** —
`test_every_free_space_row_gets_the_remedy_that_can_actually_move_its_bytes` (Linux,
macOS, one-volume) and `test_an_unnamed_free_space_row_does_not_claim_two_paths_share_a_drive`.
Three mutations, `__pycache__` purged on both sides of each, all three caught by the
right test with the right message: the Linux branch losing the Docker Desktop route,
the fallthrough going back to claiming one drive, and the macOS refusal keeping the
old shared sentence.

`tests/test_preflight.py` 58 passed; suite 4342 passed, 31 skipped; ruff, black, mypy clean.

## Follow-ups this review opened (not T37's scope)

- **The WSL measurement.** `docker_desktop_data_root()` returns `/var/lib/docker` for
  any `platform_id == "linux"`, including a WSL distro on Docker Desktop's WSL
  integration, where Docker's bytes are in the `docker-desktop-data` VHDX on the
  Windows drive. The refusal there is computed from the wrong filesystem. Wants a box.
- **The same defect class, three more rows, all pre-existing** (Codex finding 6): a
  stopped native Linux Docker Engine is told to open Docker Desktop and wait for the
  whale icon (`preflight.py:457`); a native Linux box short on memory is told to raise
  Docker Desktop's Resources limit, which native Engine has no pane for
  (`preflight.py:482`); and the client-folder bind check always prescribes Docker
  Desktop's file-sharing settings (`preflight.py:843`) where the server folder's twin
  already handles it through `_bind_remedy()`. Each names an action the user cannot
  take on that platform. One ticket, not four.

## Still owed

- The gate on a box, and `--checks`.
- A reply to Doc and to Boatmurdered — see below.

## The two reports this closes out

- **Doc** (the C: drive): fixed here. He is waiting on an answer and should get the
  Disk image location steps directly, since his refusal is real until he moves
  Docker's disk or frees space on `C:`.
- **Boatmurdered** (the rebuild doing nothing): **not a new bug.** That is T33, the
  static `QMessageBox.question()` returning an `int` so `is not Yes` was always true
  and every rebuild read as declined. Fixed in `82a1f01b` and shipped in
  `v0.8.4-Public`, tagged 2026-09-12 00:54 PDT — about three hours after his 08:08
  CEST report, which is why he and Baerthe both saw it live that morning. He needs
  the update, not a fix.
