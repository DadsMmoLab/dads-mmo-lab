---
type: Concept
title: "What Yu'lon is"
description: "A plain-words explanation of what Yu'lon does and what you need to bring."
tags: [overview, concept, catalog, servers, docker]
resource: README.md
sources:
  - id: readme
    resource: README.md
    title: "Project README"
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The list of games and their descriptions"
  - id: pyreadme
    resource: pylauncher/README.md
    title: "Launcher README: what works where"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:20:00Z
status: draft
---

# What Yu'lon is

Yu'lon is one app that installs and runs your own private game server, using buttons instead of a terminal. You pick a game from the Catalog, press **Install**, and the app fetches the open-source server software, builds it, starts it and gives you a control panel.[^readme]

## What you get

After an install you get a tab for that server with:

- Start and Stop
- a live console
- accounts
- modules
- networking, so friends can join[^readme]

## Which games are in the Catalog

The Catalog lists WoW servers and two other games:[^catalog]

- **WoW WotLK** (the stable one), plus **WoW TBC**, **WoW Vanilla**, **WoW Tortoise**, **Centurion**, **WoW Unbound**, **WoW Conquest of Azeroth** and **WoW Mists of Pandaria**, which are marked beta.
- **DDON** and **DFO**, two other games, shown under "Other games".

Each tile has a short description of what that server is. Read it before you install.

## What you need to bring

- **Your own game client.** Yu'lon never downloads, bundles or hands out game clients or game files. You point it at a client you already own.[^readme] Some servers ask for the folder during Install; for others the client is only needed when you press Play.
- **Disk space and memory.** An install builds the server from source, so it needs room. See [Pre-install checks](/getting-started/pre-install-checks.md).
- **Your computer's password once or twice**, when Yu'lon has to install system software. See [What Yu'lon installs](/getting-started/what-yulon-installs.md).

## What it is not

Yu'lon is open-source server software that you run yourself. Read the project's disclaimer before you run a server.[^pyreadme]

## Next steps

1. [What Yu'lon installs on your computer](/getting-started/what-yulon-installs.md)
2. [Download and start the app](/getting-started/download-and-start-the-app.md)
3. [First run and the Catalog](/getting-started/first-run-and-the-catalog.md)

[^readme]: README.md, "Project Yu'lon" and "What the app installs for you".
[^catalog]: Names, status and descriptions come from catalog.json (eight games, two others under "Other games").
[^pyreadme]: pylauncher/README.md says to read DISCLAIMER.md before running a server.
