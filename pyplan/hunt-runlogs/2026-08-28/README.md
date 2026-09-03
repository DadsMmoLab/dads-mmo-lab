# The 2026-08-28 cross-OS feature sweep — the raw records

Recovered 2026-09-04. These files existed only as **untracked** files in three different
worktrees (`dml-vmrun/runlogs/`, `dml-modules/`, and the main checkout), which meant that
removing any of those worktrees would have deleted them with no trace in git. Nothing here is
new work; it is the primary evidence behind entries that were already distilled into
[`../../bug-checklist.md`](../../bug-checklist.md) and [`../../hunt-rounds.md`](../../hunt-rounds.md).

The distilled entry is the one to read. This directory exists because a distilled entry is a
claim and these are what it was read off.

## What is here

| file | what it records |
|---|---|
| `BRIEF.md` | the brief the sweep was run from |
| `findings-ubuntu.md`, `findings-arch.md`, `findings-fedora.md`, `findings-win11.md`, `findings-m910q.md` | per-OS findings, round 3 |
| `findings-arch-round3-features.md`, `findings-fedora-round3-features.md` | the feature half of the same round |
| `findings-win11-round3-and-a-refutation.md` | includes a claim that was refuted rather than confirmed |
| `finding-*.md` | six findings written up singly, including the GUI-test segfault diagnosis |
| `FIX-PLAN.md` | what the sweep proposed doing about all of it |
| `UBUNTU-OPERATIONAL-RUN.md` | the operational run on Ubuntu |
| `modules-ownership-report.md` | the review behind `fix/modules-tab-ownership` (that branch is still open) |
| `drivers/` | the harnesses that produced the above |

## The drivers, and why they are worth keeping

`sweep_driver*.py` drive the **production wiring** — `ControllerServices.for_wotlk`, the same
seam the GUI's `controller_view.py` buttons call — non-interactively. They are not a
reimplementation of the GUI; they are the GUI's own seam called from a script.

They exist because of a measured obstacle: the native `xdg-desktop-portal` folder picker has no
AT-SPI content over an SSH-driven session, so the dialog cannot be driven by accessibility at
all. `atspi_dialog2.py` and `atspi_use_existing_wotlk.py` are the AT-SPI tree dumps that
established that. Anyone who next needs to drive this app's GUI headlessly on Linux should read
those two before assuming AT-SPI will work.

Paths inside the drivers point at `~/yulon-run/pylauncher` and `~/wow-server-playerbots` on the
box they were run on. They are a record, not a fixture — nothing in the test suite imports them.
