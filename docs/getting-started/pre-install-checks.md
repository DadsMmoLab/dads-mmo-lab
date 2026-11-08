---
type: Reference
title: "Pre-install checks"
description: "What Yu'lon checks before it builds a server, what each result means, and how to fix a refusal."
tags: [preflight, disk-space, memory, ports, client-folder]
resource: pylauncher/yulon/catalog/preflight.py
sources:
  - id: preflight
    resource: pylauncher/yulon/catalog/preflight.py
    title: "The checks and their wording"
  - id: clientdir
    resource: pylauncher/yulon/catalog/families/clientdir.py
    title: "The client folder rules"
  - id: catalogjson
    resource: pylauncher/yulon/catalog/catalog.json
    title: "Per-game memory and disk numbers"
  - id: native
    resource: pylauncher/yulon/catalog/native.py
    title: "Prints the checks into the install log"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Pre-install checks

Before Yu'lon writes anything or builds, it asks your computer a list of questions and prints each answer in the install log. A refusal stops the install early, so you do not lose hours to a build that was never going to finish.[^native]

## What the results mean

Each line starts with one of four words:[^preflight]

- **pass** - fine.
- **warn** - it should work, but it is tight. The install goes on.
- **unchecked** - Yu'lon could not measure it. This is not a pass, and the install goes on.
- **refuse** - the install will not start. The "Cannot install yet" box tells you what to change.

## The checks

| Check | What it asks |
|---|---|
| Docker | Does Docker answer? If not, Yu'lon tries to set it up first. |
| Docker Compose | Is Compose present and new enough? The log says to test with `docker compose version`, with a space; the old hyphenated one is not used. |
| memory | How much memory Docker has. |
| parallel jobs | Whether a build that compiles many pieces at once fits in that memory. This one only warns. |
| free space on Docker's disk | Room for the images and build. |
| free space on the server folder | Room for the server files. |
| the server folder | Can Yu'lon use the folder you chose? |
| sharing the folder with Docker | Can a container see that folder? |
| the server's ports | Is something else using the ports the server needs? |
| the client folder | Only for games that need your client (see below). |

## Memory and disk numbers

The numbers differ per game and are set in the catalog. For example, WoW TBC, Vanilla and Tortoise refuse below 6 GB of memory and warn below 8 GB, and want 20 GB free (warning below 30 GB) on each disk. Conquest of Azeroth refuses below 8 GB and warns below 12 GB. Mists of Pandaria refuses below 8 GB and warns below 16 GB.[^catalogjson] If the server folder and Docker's disk are on the same drive, the needs add up, and the log says so.[^preflight]

On Windows, the memory Docker gets comes from Windows' WSL settings (`%UserProfile%\.wslconfig`), not from a slider in Docker Desktop.[^preflight] On a Mac, Docker's virtual disk has its own size limit, so plenty of free space on the Mac is reported as "unchecked", not a pass.[^preflight]

## Ports

If another container already uses the server's ports, the check refuses: stop that server first or remove its containers. If something else is merely listening, it only warns.[^preflight]

## The client folder

For games that need your own client, Yu'lon checks the folder you picked:[^clientdir]

- The folder must exist and be a folder, not a file.
- It must have a `Data` directory. Pick the folder the game is installed in, not a launcher, an installer or a backup.
- A required game file must be there. If it is missing, the client is the wrong one or incomplete.
- The game program's version must match what the server expects.

Softer problems, such as few archives or an unexpected locale, are warnings. Yu'lon also checks free space next to the client.

## If something goes wrong

Fix what the refusal says, then press **Install** again. See [Common problems](/getting-started/common-problems.md).

[^native]: The install prints every check as one line in the install log.
[^preflight]: Verdicts, check names and remedies come from the pre-install checks.
[^catalogjson]: min and warn numbers are in each game's native section.
[^clientdir]: The client folder rules come from the install checks.
