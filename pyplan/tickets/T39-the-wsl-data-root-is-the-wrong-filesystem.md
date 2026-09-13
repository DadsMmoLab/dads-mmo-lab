# T39 — under Docker Desktop's WSL integration the free-space check measures the wrong filesystem

**Status:** OPEN — not started. Needs a box.
**Filed:** 2026-09-12 by the lead, from the Codex review of T37.
**Hand:** unassigned. Not unit-only: the whole question is which filesystem actually grows.

## The defect

`platform.docker_desktop_data_root()` (`platform.py:3531`) returns `/var/lib/docker` for
every `platform_id == "linux"`. `platform.detect()` returns `"linux"` inside WSL too — its
own docstring says so. So a launcher running in a WSL distro whose `docker` is Docker
Desktop's through WSL integration measures the distro's own `/var/lib/docker`, which that
daemon does not use: the images live in the `docker-desktop-data` VHDX on the Windows drive.

The free-space refusal is then computed from a filesystem Docker never writes to. It can
refuse an install that would have fitted, or pass one that will run the Windows drive out
of space mid-build.

T37 fixed the *sentence* under that refusal to name both daemons' settings, because this
module cannot tell them apart. This ticket is the *number*.

## What it needs

A box that can be put in both states and measured: a WSL distro with native Docker Engine,
and the same distro on Docker Desktop's WSL integration, with a build big enough to move
the needle, and `df` on both candidate filesystems before and after. The owner's own laptop
is in the second state — see [[yulon-moved-to-wsl-ubuntu]] — so this is reproducible without
building a new box, but never by running Docker on the laptop ([[ubuntu-test-box-m910q]]).

Likely shape of the fix: ask the daemon rather than assume. `docker info` reports
`Docker Root Dir` and, on Desktop, the operating system it is running under; that answer is
the one to measure, and "could not be established" must stay `unchecked` rather than
become a guess ([[a-guarantee-is-an-invitation]]).
