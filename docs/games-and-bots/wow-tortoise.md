---
type: Guide
title: "WoW Tortoise"
description: "The Turtle WoW style server built from source, which custom client it needs, and what is different on it."
tags: [tortoise, turtle-wow, tortoisebots, 1.18.1, catalog]
resource: pylauncher/yulon/controller_wow_tortoise/controller.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-tortoise entry"
  - id: addons
    resource: pylauncher/yulon/default_addons.py
    title: "Default client add-ons"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# WoW Tortoise

Turtle WoW's world on the Tortoise core, the code the Turtle WoW V2 servers are built from, with the TortoiseBots bots built in. Yu'lon compiles it from source in Docker.[^catalog]

## The client you bring

Only the custom Turtle-WoW 1.18.1 client (build 7272) works. Any other build will not connect.[^catalog] Client setup is in [Playing](/playing/index.md).

## What is special

- **TortoiseBots.** 500 random bots at the start. The Bots tab can also turn on the bot dashboard ([Bot dashboard](/games-and-bots/bot-dashboard.md)) and rebuild the random bots ([Bot population](/games-and-bots/bot-population.md)).[^catalog]
- **Default add-ons.** TortoiseBots Manager and Tortoise GM Manager are put into your game client by themselves, and left alone if you remove them. **Update the server to latest...** also updates TortoiseBots Manager.[^addons]
- **GM ranks** go up to 4 on this server.[^catalog]
- **Offline rename.** A character has to be logged in before a rename can be flagged. Doing it offline would replace the name with a number, so Yu'lon does not send it.[^catalog]
- **No level box.** This server cannot move a character to a level you pick.[^catalog]
- Install is Linux or Windows.[^catalog]

## Limits

- Beta in the Catalog.
- My Party is not available here; browse the bots instead.
- The first install compiles the server, so it takes a while: see [Pre-install checks](/getting-started/pre-install-checks.md).

## If something goes wrong

Questions about the Tortoise server and the bots go to the Snapjaw Discord places listed in the app's Help places. For Yu'lon problems see [Common problems](/getting-started/common-problems.md).

[^catalog]: Client, ranks, play limits, bot count and platforms from the game catalog.
[^addons]: Default add-ons are put in at Play and at a fresh install, and a removed one stays removed.
