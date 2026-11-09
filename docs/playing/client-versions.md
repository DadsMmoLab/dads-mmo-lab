---
type: Reference
title: "Which client each game needs"
description: "A table of the game client you bring for each game, and the program file Yu'lon looks for."
tags: [client, versions, catalog, games, reference]
resource: pylauncher/yulon/catalog/catalog.json
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: Client version, build and program name per game
  - id: build
    resource: pylauncher/yulon/client_build.py
    title: How the program's build is checked
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Which client each game needs

Yu'lon never downloads or hands out a game client you don't already have. The only game files it fetches are ones you ask for: the Conquest of Azeroth files that **Update client** replaces in your own folder, and, for Centurion only, a stock `Wow.exe` when yours is not the unmodified one (one file, checked by hash first). You bring a client that matches the game. Pick its folder as described in [Point Yu'lon at your own game client](/playing/set-your-client-folder.md).

| Game | Client you bring | Program Yu'lon starts |
|---|---|---|
| WoW WotLK | 3.3.5a, build 12340 | Wow.exe |
| WoW Unbound | 3.3.5a, build 12340 | Wow.exe |
| WoW Conquest of Azeroth | Ascension 3.3.5a (the CoA client files, see below) | Ascension.exe |
| Centurion | English 3.3.5a, build 12340 | Wow.exe |
| WoW Tortoise | Turtle WoW 1.18.1 | Wow.exe |
| WoW Vanilla | 1.12.1, build 5875 (builds 6005 and 6141 are also accepted) | Wow.exe |
| WoW TBC | 2.4.3, build 8606 | Wow.exe |
| WoW Mists of Pandaria | 5.4.8, build 18414, from Recall WoW | _wow-64.exe |
| DDON | 03.04.003 | DDO.exe |
| DFO | US 2.38.3.25 | DFO.exe |

The versions and program names come from the Catalog.[^catalog] Per-game notes, such as ports and bots, are in [Games and servers at a glance](/games-and-bots/games-overview.md) and the other pages under [Games and bots](/games-and-bots/index.md).

## How the version is checked

When you pick a folder, and again at Play, Yu'lon reads the version out of the game program file. If it reads a different build than the server needs, it says so and refuses. If it cannot read a version (a launcher stub, for example), it lets the folder through.[^build]

## Notes that matter

- **Conquest of Azeroth.** Use the CoA client files. A stock Ascension download is disconnected when you pick the realm. The **Update client** button turns your Ascension copy into the CoA client: see [Client updates](/playing/client-updates-and-patches.md).[^catalog]
- **Centurion.** Yu'lon makes a ready-to-play copy with Centurion's patches and a program that reports build 12342. Your own client stays as it was.[^catalog]
- **WoW Mists of Pandaria.** Get the client from Recall WoW. It unpacks to one folder called World of Warcraft 5.4.8; the program is `_Wow-64.exe`.[^catalog]
- **WoW Tortoise.** Only the custom Turtle WoW client works; other builds will not connect.[^catalog]
- **DDON and DFO** are not World of Warcraft: see [DDON and DFO servers](/playing/single-container-servers.md).

[^catalog]: Client version, build, program name and notes per game are in the client block of each Catalog entry.
[^build]: A build that is read and wrong is refused; an unreadable one is accepted.
