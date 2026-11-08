---
type: Guide
title: "WoW Vanilla"
description: "What the Vanilla 1.12.1 server is, which client builds work, and what to know before installing."
tags: [vanilla, cmangos, 1.12.1, playerbots, catalog]
resource: pylauncher/yulon/controller_wow_vanilla/controller.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-vanilla entry"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# WoW Vanilla

Vanilla World of Warcraft (1.12.1) on the CMaNGOS server with the CMaNGOS playerbots fork.[^catalog]

## The client you bring

You bring your own 1.12.1 client. The server accepts build 5875, and also builds 6005 and 6141.[^catalog] Before it extracts anything from your client, Yu'lon checks the client's archives: the number of MPQ files and that `dbc.MPQ` is there.[^catalog] See [Pre-install checks](/getting-started/pre-install-checks.md) for what those checks say, and [Playing](/playing/index.md) for client setup.

## What is special

- 500 random bots at the start. See [Playerbots](/games-and-bots/playerbots-overview.md) and [Bot population](/games-and-bots/bot-population.md).[^catalog]
- An offline character can be revived, and mail carries one item at a time on this server.[^catalog]
- Level sync for bots is on: bots follow the levels of the players, staying at most 5 levels above.[^catalog]
- Install is Linux or Windows.[^catalog]

## Limits

- It is a beta in the Catalog.
- My Party is not available here; it needs modules that only the WotLK and Unbound servers have. The Bots tab still lets you browse the bots.[^catalog]
- The Vanilla server uses a Docker build from source, so the first install takes a while.

## If something goes wrong

Bugs in the Vanilla server and in the bots are reported to the cMaNGOS project; the app's Help places list has the link. For install trouble, see [Common problems](/getting-started/common-problems.md).

[^catalog]: Client builds, bot keys, play limits and platforms from catalog.json.
