---
type: Guide
title: "WoW TBC"
description: "What the Burning Crusade 2.4.3 server is, why a Vanilla client will not do, and what to know before installing."
tags: [tbc, cmangos, 2.4.3, playerbots, catalog]
resource: pylauncher/yulon/controller_wow_tbc/controller.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-tbc entry"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# WoW TBC

The Burning Crusade (2.4.3) on the CMaNGOS server with the CMaNGOS playerbots fork.[^catalog]

## The client you bring

You bring your own 2.4.3 client, build 8606. A Vanilla client is the wrong version, and the installer checks for this.[^catalog] Client setup is in [Playing](/playing/index.md).

## What is special

- 500 random bots at the start, made on 100 bot accounts. See [Playerbots](/games-and-bots/playerbots-overview.md) and [Bot population](/games-and-bots/bot-population.md).[^catalog]
- Install is Linux or Windows.[^catalog]
- It has the same tabs as the other servers; see [Control panel and tabs](/running-a-server/control-panel-and-tabs.md). Repairs are buttons on the Server tab.

## Limits

- It is a beta in the Catalog, and its newest pins were not live-tested when set.[^catalog]
- My Party is not available here (WotLK and Unbound only); you can still browse the bots.
- Accounts get a GM level of at most 3.[^catalog]

## If something goes wrong

If the install refuses your client, check the version first: it must be 2.4.3. More in [Pre-install checks](/getting-started/pre-install-checks.md) and [Common problems](/getting-started/common-problems.md).

[^catalog]: Client build, notes, bot keys, platforms and account levels from the game catalog.
