---
type: Guide
title: "First run and the Catalog"
description: "A tour of the window and the Catalog, where you pick a game."
tags: [catalog, first-run, tiles, sidebar, servers]
resource: pylauncher/yulon/ui/catalog_view.py
sources:
  - id: catalogview
    resource: pylauncher/yulon/ui/catalog_view.py
    title: "The Catalog screen and its tiles"
  - id: sidebar
    resource: pylauncher/yulon/ui/sidebar.py
    title: "The top bar and server rail"
  - id: catalogjson
    resource: pylauncher/yulon/catalog/catalog.json
    title: "Games, statuses, descriptions"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# First run and the Catalog

The Catalog is where you pick a game and press **Install**. It is the first page in the window.

## The top bar

Across the top of the window are five buttons:[^sidebar]

- **Launcher** - play on a friend's server without installing one here.
- **Log** - Yu'lon's own logs, and a file to send when something goes wrong.
- **Catalog** - browse and install server emulators.
- **Servers** - choose an installed server. Until you have one it is greyed out, and its tooltip says "No servers installed yet - install one from the Catalog".
- **Settings** - change the colour theme, keep Yu'lon in the system tray, and start it at sign-in. See [Settings and themes](/getting-started/settings-and-themes.md).

Each server you install also gets a short name on the left rail, for example "WotLK".

## Picking a game

1. Open **Catalog**. The heading says **Choose a server to install**, and under it: "Yu'lon downloads and builds the server for you. Nothing changes on this computer until you press Install."[^catalogview]
2. You first see game cards. The WoW card says how many "Server Cores" it holds. Cards for other games say "1 Server", with "(Beta)" or "(Work in progress)" where that applies.[^catalogview]
3. Click a card to see its servers as tiles. Press **Back** to return to the cards.
4. Read a tile. It has a description, the client version it needs (for example "Client 3.3.5"), and a **Recommended** label on one of them.
5. Press **Install** on the tile you want. Next: [Install a server](/getting-started/install-a-server.md).

## Other buttons on a tile

- **Use existing...** manages a server that is already installed, by a script or before this app. It works on every system, and on every tile (World of Warcraft, DDON and DFO).[^catalogview]
- **Bring from another computer...** builds a server from a file packed on another computer. It is on World of Warcraft tiles only; DDON and DFO tiles show just **Install** and **Use existing...**.
- **Find in WSL...** is on World of Warcraft tiles only, and appears only on Windows when a Linux (WSL) distro exists. It adopts a server that already lives inside it, without moving it.
- Once a game is installed, its **Install** button becomes **Open server**, which takes you to that server's tab.[^catalogview]

## What you'll see

If a game does not support your system, its tile says "Installer needs ... - not available on this platform yet." and **Install** is greyed out. **Use existing...** stays available.[^catalogview]

## If something goes wrong

See [Common problems](/getting-started/common-problems.md).

[^sidebar]: Button names and tooltips are set in the sidebar.
[^catalogview]: Labels and tile layout are on the Catalog page; game list and descriptions in the game catalog.
