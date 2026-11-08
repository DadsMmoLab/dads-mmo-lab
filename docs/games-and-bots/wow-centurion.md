---
type: Guide
title: "Centurion"
description: "The level-60 player-versus-player server: what it is, how your client is made ready, and its limits."
tags: [centurion, pvp, trinitycore, 3.3.5a, catalog]
resource: pylauncher/yulon/controller_wow_centurion/controller.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-centurion entry"
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: "The Modules tab text for games without add-on modules"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Centurion

Centurion is level-60 player-versus-player WoW on the 3.3.5a client, with hardcore world characters, tournament battlegrounds and arenas, and 150 bots that quest, roam and fill the battlegrounds. Yu'lon builds the server from Centurion's own source and makes your English 3.3.5a client ready to play it, with Centurion's patches and addons.[^catalog]

## The client you bring

Bring your own English (enUS) 3.3.5a client, build 12340. Yu'lon makes a ready-to-play copy of it, with Centurion's patches and a Wow.exe that reports build 12342, and leaves your own client as it was.[^catalog] The patches it adds include Centurion's art, game data and addons, client fixes, and optional HD packs (creatures, textures, spells, battlegrounds, interface) and the ConsolePort controller add-on. The ready-made client has a borderless full screen option. How to do this step is in [Playing](/playing/index.md).

## What is special

- **150 bots.** The bots are 232 pre-made characters on fixed accounts, and they fill the battlegrounds. The Bots tab's Random bots box works here, but bot accounts are fixed, so no ceiling is stated. See [Bot population](/games-and-bots/bot-population.md).[^catalog]
- **No add-on modules.** Its bots and its own features are built into the server, so the Modules tab has nothing to add or remove.[^view]
- **Install space.** The server folder needs at least 12 GB free (a warning below 20 GB); see [Pre-install checks](/getting-started/pre-install-checks.md).
- Linux and Windows.[^catalog]

## Limits

- It is a beta in the Catalog.
- My Party is not available on Centurion.
- The server's settings are in `playerbots.conf`; see [playerbots.conf](/games-and-bots/playerbots-conf.md).

## If something goes wrong

Questions about Centurion and its bots go to the Centurion Discord listed in the app's Help places. If the client step fails, see [Common problems](/getting-started/common-problems.md).

[^catalog]: Description, client notes, bot counts and packs from the game catalog.
[^view]: The Modules tab says the game has no add-on modules.
