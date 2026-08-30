# Hunt rounds — one game at a time, every OS before the next game

The order is deliberate. A game is finished on **all four operating systems** before the next
game starts, because a defect found on one OS is usually present on the others and it is
cheaper to fix it once, across the board, than four times a month apart.

Every bug from every round lands in [`bug-checklist.md`](bug-checklist.md). That file is the
single list; this one is only the running order.

---

## Round 1 — WotLK, all four OSes

The AzerothCore WotLK installer, which is the one the launcher leads with.

| OS | Installer | State |
| --- | --- | --- |
| **Ubuntu 24.04** | `install-wow-wotlk-ubuntu.sh` (apt) | **done** — 21 defects, fixed, [PR #126](https://github.com/DadsMmoLab/dads-mmo-lab/pull/126) green |
| **Arch** | `install-wow-wotlk.sh` (base script, pacman fallback) | round-4 hunt running |
| **Windows 11** | native engine (no bash script) | round-4 hunt running |
| **Fedora 44** | `install-wow-wotlk-fedora.sh` (dnf) | **open** — see below |

**Fedora is the hole in this round.** The dnf variant has never completed a fresh compile: it
reached 83% at 8 GB of RAM and swap-thrashed the box unreachable. Everything Fedora has
contributed so far — the SELinux `:z`/`:Z` findings — came from an *adopted* install, not one
the script built. Owed: a fresh compile at 23 GB on yulon-fedora.

## Round 2 — Tortoise

`install-tortoise-wow-wsl.sh`. **Never run once.**

**It is a LINUX installer, despite the filename.** `catalog.json` declares
`"platforms": ["linux"]` for `wow-tortoise` — Linux and nothing else. The `-wsl` in the name is
a leftover: the script detects WSL2 at line 186 and branches on it, and it carries an explicit
NON-WSL branch as well (the Steam Deck / Proton path, which tells the user the client needs the
Deck's LAN IP rather than 127.0.0.1). On native Linux it simply takes that branch. So this round
runs on **Arch or Ubuntu**, like the others.

The Windows side is the opposite problem, and it is already on the checklist as a Windows
finding: the script HAS WSL2 support, and the catalog's platform list refuses to offer it there,
so a Windows user can never install Tortoise even though the code to do it exists.

Turtle's own servers shut down in May 2026, so the install is judged on whether it completes and
starts, not on whether a character can log into a live realm.

The client blocker is **cleared**: the owner supplied
`1.18.1-7272-Hotfix-2026-04-12.zip` (9.86 GB), now staged on the host share alongside the other
two. Nothing else stands in this round's way.

## Round 3 — Vanilla

`install-wow-vanilla.sh`. **Correction: it HAS been run, on Fedora, and it failed.**
`/home/pk/wow-vanilla-server` exists on yulon-fedora and `vanilla-install.log` ends with

    Bind for 0.0.0.0:8085 failed: port is already allocated
    — Failed to start mangosd/realmd.

— a port collision with the WotLK stack already running on that box. So this round starts from a
known failure rather than from nothing, and "two games on one machine" is now a hunting lens in
its own right. The script also refuses under **20 GB free on the target disk AND on Docker's data
root**, so a prune is required between installs on the same box.

## Round 4 — TBC

`install-wow-tbc.sh`. **Never run once.** Needs the 2.4.3 client, staged on the host share.

---

## What every round does, in the same order

1. **Hunt only.** Four lenses per box, run as a workflow, each finding carrying the command
   and the output that produced it. No speculation, and nothing already on the checklist.
2. **Verify adversarially.** Every finding goes to a second agent whose job is to *refute* it,
   defaulting to refuted when uncertain. Only what survives reaches the checklist. Round 3 on
   Windows retracted a HIGH finding this way, and round 2 on Arch retracted another.
3. **Land it on the checklist**, with severity and evidence.
4. **Stop. Ask before fixing.** Fixes start on an explicit go-ahead, not automatically.
5. **Fix → verify on a real box → three-way review → CI green → one PR per OS.**

## What constrains the schedule

The Hyper-V host has **63.8 GB** and each test VM takes **23 GB**, so **two VMs at a time** is
the hard ceiling. A third means saving one first. yulon-fedora is therefore blocked behind
whichever of arch/win11 finishes first.

A WotLK compile is 35–72 minutes depending on the box; the clients are 1–10 GB each. Neither is
parallelisable on one machine, so rounds 2–4 are sequential by nature.


---

## Where the clients live

All three sit once on the Hyper-V host and are shared read-only as `\\<host>\clients`
(`U:\clients`, on the volume Ubuntu's VM vacated):

| Client | Size | For |
| --- | --- | --- |
| `WoW-Client-1.12.1.zip` | 4.97 GB | Vanilla |
| `WoW-Client-2.4.3.zip` | 7.84 GB | TBC |
| `TurtleWoW-1.18.1-7272-Hotfix-2026-04-12.zip` | 9.86 GB | Tortoise |

The VMs are on the **Default Switch**, which is an *Internal* switch — the host is one virtual
NIC hop away, so a Linux guest mounts it with
`mount -t cifs //<gateway>/clients /mnt/clients -o ro,guest` and Windows reads the UNC path
directly. That matters: a laptop-to-VM copy over Tailscale is DERP-relayed at 0.65 MB/s, which is
why nothing should ever be pushed to these boxes that way again. Each VM also has its OWN 119 GB
volume (U/Y/X/W for ubuntu/arch/fedora/win11), so staging large files anywhere else steals a
build's headroom.
