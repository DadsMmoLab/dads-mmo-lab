# Yu'lon full VM run — 2026-08-28 — shared brief

You are testing the Yu'lon launcher (Python/PySide6, `pylauncher/`) on a real VM, hunting for
REAL bugs. Branch under test: `fix/installers-honour-chosen-folder` @ 80fb68a9
(github.com/pjerra/dads-mmo-lab, remote `origin`).

## Non-negotiables

1. **Announce every action before you do it** on the VM:
   `ssh <vm> '~/bin/claude-say "what you are about to do"'`
   The user is watching a terminal window on the VM desktop tailing `~/claude-activity.log`.
   No silent stretches longer than ~2 minutes. Announce start AND result of each step.
2. **The launcher GUI window must be visible on the VM desktop** while you test features.
   Launch GUI programs through `~/bin/on-desktop <cmd>` (it injects the graphical session env
   from the running window manager into an ssh shell). Example:
   `ssh <vm> 'cd ~/yulon-run/pylauncher && nohup ~/bin/on-desktop python3 -m yulon ... &'`
3. **Evidence or it did not happen.** Every claim needs a command + its output, quoted.
   Never report "works" from reading code. Never invent output.
4. **Do not destroy state you did not create.** No VM checkpoint apply, no `rm -rf` of the
   user's existing `~/wow-*` server dirs, no shutting the VM down.
5. Everything long-running goes in a log file under `~/yulon-run/logs/` and is polled, not
   blocked on. Do not let a single command block you for more than ~10 minutes.

## The recipe that is already proven to work

- `~/bin/claude-say "msg"` — appends to the on-screen activity log.
- `~/bin/on-desktop <cmd>` — runs a GUI command inside the logged-in desktop session.
- SSH: `ssh yulon-ubuntu` / `ssh yulon-fedora` / `ssh yulon-arch` (user `pk`, passwordless sudo).

## What to test (adapt to your distro; report what you could not reach and why)

**A. Code gates on this distro's Python**
   Clone to `~/yulon-run` (`git clone --branch fix/installers-honour-chosen-folder https://github.com/pjerra/dads-mmo-lab.git ~/yulon-run`),
   make a venv, `pip install -r pylauncher/requirements.txt -r pylauncher/requirements-dev.txt`,
   then from `pylauncher/`: `ruff check .`, `black --check .`, `mypy .`, `pytest -q`.
   Record the distro's Python version. Version-specific failures ARE findings.

**B. Launcher app, visibly**
   Start the GUI, confirm the window is on screen (screenshot it), then work the UI/underlying
   code paths: catalog list, install dialog (folder choice, free-space check, client-dir
   requirement), controller tabs for any existing install (console, accounts, backups,
   modules, networking, maintenance/repair, logs), self-update check.
   Where you cannot click (no automation), drive the same code path from Python directly and
   say so explicitly in the report.

**C. Install**
   Run the WotLK install for this distro through the launcher's own installer entry point,
   into a NEW folder you create (do not reuse an existing server dir), logging to
   `~/yulon-run/logs/install-<distro>.log`. Poll it. If it compiles, that is expected to take
   a long time — keep announcing progress. Report exactly where it got to.

## Report format (your final message)

For each finding: what you ran, what you expected, what happened, the exact output, and the
file:line in the repo you believe is responsible if you can identify it. Rank by severity.
Separately list what you could NOT test and why. Be blunt about gaps; a short honest report
beats a padded one.
