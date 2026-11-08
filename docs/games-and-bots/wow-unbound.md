---
type: Guide
title: "WoW Unbound"
description: "The multi-class WotLK server: its own server beside WotLK, its own login port, and what to set in the client."
tags: [unbound, wotlk, azerothcore, classes, catalog]
resource: pylauncher/yulon/unbound_settings.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-unbound entry"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# WoW Unbound

Wrath of the Lich King with Wrath Unbound: you unlock more classes at the Mentor and mix their spells and talents. A Death Knight can be your first class, not an added one.[^catalog]

## Its own server

Unbound has its own folder, containers, images, databases and ports, so it runs beside your WotLK server.[^catalog] Its login port is 3725 and its world port 8086, not WotLK's.

## The client you bring

You bring a 3.3.5a client, build 12340. Because the login port is different, the realmlist line must include it: `127.0.0.1:3725` for yourself, or the host's LAN or public address with `:3725` for other players.[^catalog] Unbound also installs a set of its own client add-ons, and the Lumenveil graphics mod and ConsolePort are offered as extras. See [Playing](/playing/index.md).

## What is special

- 500 random bots at the start, like WotLK. See [Playerbots](/games-and-bots/playerbots-overview.md).
- My Party works here too: [My Party](/games-and-bots/my-party.md).
- Linux, macOS and Windows are supported.[^catalog]
- The Modules tab works as on WotLK: [Modules](/running-a-server/modules.md).

## Limits

- Game accounts get a GM level of at most 3.
- The server is a beta in the Catalog, and its newest pins were not live-tested when set.[^catalog]

## If something goes wrong

If the client cannot reach the realm, check first that the realmlist line ends in `:3725`. Bugs in the multi-class mod go to the Dad's MMO Lab Discord channel named in the app's Help places. Other trouble: [Common problems](/getting-started/common-problems.md).

[^catalog]: Description, ports, client notes and platforms from the game catalog.
