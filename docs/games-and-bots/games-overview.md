---
type: Reference
title: "Games and servers at a glance"
description: "A table of every game Yu'lon can install, which client you must bring, and how many bots each starts with."
tags: [catalog, games, clients, overview, bots]
resource: pylauncher/yulon/catalog/catalog.json
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The Catalog: every game, its client version, ports and bot counts"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# Games and servers at a glance

The Catalog lists these games. Pick one to see its servers, then press Install. The steps are in [Install a server](/getting-started/install-a-server.md). Yu'lon never ships a game client: you bring your own, and the table says which version.[^catalog]

## The World of Warcraft servers

| Game | Status | Client you bring | Bots it starts with |
|---|---|---|---|
| [WoW WotLK](/games-and-bots/wow-wotlk.md) | stable | 3.3.5a (build 12340) | 500 |
| [WoW Unbound](/games-and-bots/wow-unbound.md) | beta | 3.3.5a (build 12340) | 500 |
| [WoW Conquest of Azeroth](/games-and-bots/wow-conquest-of-azeroth.md) | beta | Ascension 3.3.5a, made into the CoA client | 150 |
| [Centurion](/games-and-bots/wow-centurion.md) | beta | English 3.3.5a (build 12340) | 150 |
| [WoW Tortoise](/games-and-bots/wow-tortoise.md) | beta | Turtle WoW 1.18.1 (build 7272) | 500 |
| [WoW Vanilla](/games-and-bots/wow-vanilla.md) | beta | 1.12.1 (build 5875) | 500 |
| [WoW TBC](/games-and-bots/wow-tbc.md) | beta | 2.4.3 (build 8606) | 500 |
| [WoW Mists of Pandaria](/games-and-bots/wow-mists-of-pandaria.md) | beta | 5.4.8 (build 18414), from Recall WoW | 500 |

"Beta" is the status the Catalog gives the server. WotLK is the only one marked stable.[^catalog]

## Where each one runs

WotLK and Unbound can be installed on Linux, macOS and Windows. Every other World of Warcraft server here lists Linux and Windows.[^catalog] How much memory and disk each one needs is checked before the install; the numbers are in [Pre-install checks](/getting-started/pre-install-checks.md).

## Ports

Most of these servers use the usual login port 3724. Unbound (3725) and Conquest of Azeroth (3726) use their own, so they can sit beside a WotLK server. For those two, the realmlist line also carries the port, for example `127.0.0.1:3725`.[^catalog]

## Two other games

The Catalog also has servers that are not World of Warcraft:

- **DDON** - Dragon's Dogma Online on the Arrowgene server. You play with your own 03.04.003 client, and your account is made the first time you log in.
- **DFO** - Dungeon Fighter Online on the 115US community server, with your own US 2.38.3.25 client. The server is early: the towns and first dungeons work, many later ones do not yet.[^catalog]

## Next

- Bots on these servers: [Playerbots](/games-and-bots/playerbots-overview.md).
- Getting your client ready and patched: [Playing](/playing/index.md).
- Running the server day to day: [Running a server](/running-a-server/index.md).

[^catalog]: Names, status, client version, ports, platforms and bot counts come from the games section of catalog.json.
