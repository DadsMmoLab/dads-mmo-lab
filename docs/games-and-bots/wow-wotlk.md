---
type: Guide
title: "WoW WotLK"
description: "What the WotLK server is, which client you bring, and what is special about it."
tags: [wotlk, azerothcore, 3.3.5a, playerbots, catalog]
resource: pylauncher/yulon/controller_wow_wotlk/controller.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-wotlk entry"
  - id: party
    resource: pylauncher/yulon/party.py
    title: "My Party: which games have it"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# WoW WotLK

Wrath of the Lich King (3.3.5a) on AzerothCore, with bots that quest, group and fight alongside you. The Catalog calls it the most complete server here: you can add modules, scripts and mods from its Modules tab.[^catalog] It is the one server marked stable.

## The client you bring

You bring your own 3.3.5a client, build 12340. In the client, the realmlist line is `127.0.0.1` for yourself, or the host's LAN or public address for other players.[^catalog] Preparing the client is covered in [Playing](/playing/index.md).

## What is special

- **Bots.** It starts with 500 random bots. See [Playerbots](/games-and-bots/playerbots-overview.md) and [Bot population](/games-and-bots/bot-population.md).[^catalog]
- **My Party.** The Bots tab can build a bot party for your character. It works on WotLK and Unbound. See [My Party](/games-and-bots/my-party.md).[^party]
- **Modules.** Add and remove modules on the Modules tab: [Modules](/running-a-server/modules.md).
- **Graphics extra.** The Lumenveil graphics mod is offered as an optional client extra, and the ConsolePort controller add-on is available for Steam Deck and controller play.[^catalog]
- **Platforms.** Linux, macOS and Windows.[^catalog]

## Limits

- Game accounts you make get a GM level of at most 3.[^catalog]
- The server builds from the newest upstream version of the server and the bots module. The Catalog's own note says the newest pins were not live-tested at the time they were set.[^catalog]

## If something goes wrong

Bugs that also happen without the bots belong to AzerothCore; bugs in the bots belong to mod-playerbots. The app's Help places list shows where to report each one. For problems with Yu'lon itself, see [Common problems](/getting-started/common-problems.md).

[^catalog]: Description, client, ports, bot count and platforms from catalog.json.
[^party]: My Party is offered for wow-wotlk and wow-unbound only.
