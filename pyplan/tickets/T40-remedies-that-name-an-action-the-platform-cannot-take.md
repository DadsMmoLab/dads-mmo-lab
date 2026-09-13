# T40 — four preflight rows tell a Linux user to open Docker Desktop

**Status:** OPEN — not started. Unit-only; every row is a pure function of `Facts`.
**Filed:** 2026-09-12 by the lead, from the Codex review of T37 (its finding 6).
**Hand:** unassigned.

## The defect

The same class T37 fixed for the free-space rows, in four more places: a remedy naming an
action the user cannot take on the platform they are on. Each was found by reading, not by
a report, but T37's row reached a real user and cost him a morning.

| row | says | on native Linux |
|---|---|---|
| `preflight.py:457` | open Docker Desktop and wait for the whale icon | there is no Docker Desktop; the daemon is a service |
| `preflight.py:482` | raise Docker Desktop's Resources memory limit | native Engine has no Resources pane and no VM to size |
| `preflight.py:843` | set Docker Desktop's file sharing for the client folder | native Engine shares the host filesystem directly |
| (the same row, `unchecked`) | as above | as above |

The server folder's bind check already does this correctly through `_bind_remedy()`, which
is the working example to copy — this is [[defects-live-between-the-parts]] again, and the
pattern for the fix already exists in the same file.

## Scope

One ticket, not four: they are one rule applied in four places, and splitting them would
four times the review for one decision. Not folded into T37 because T37's diff is the
free-space rows and a fix is reviewed against what it claims to be.
