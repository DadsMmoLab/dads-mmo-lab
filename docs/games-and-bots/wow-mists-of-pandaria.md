---
type: Guide
title: "WoW Mists of Pandaria"
description: "The 5.4.8 Pandaria server with 500 bots: where to get the client, how Yu'lon readies it, and what to expect."
tags: [mop, pandaria, 5.4.8, trinitycore, catalog]
resource: pylauncher/yulon/catalog/catalog.json
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-mop entry"
  - id: chars
    resource: pylauncher/yulon/controller_wow_centurion/characters.py
    title: "Characters tab actions per game"
  - id: changelog
    resource: CHANGELOG.md
    title: "Release notes"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# WoW Mists of Pandaria

Mists of Pandaria 5.4.8 (Legends of Azeroth Pandaria) with 500 playerbots that quest, travel, group and level from 1 to 90, Pandaria included. Yu'lon builds the server from LoA Pandaria's own source and makes your English 5.4.8 client ready to play it.[^catalog]

## The client you bring

Bring the Recall WoW client. The Catalog's note says to get it from Recall WoW, the Mists of Pandaria download (a file named RecallWoWHDmodels.zip, about 24 GB). It unpacks to one folder, `World of Warcraft 5.4.8`, and you launch `_Wow-64.exe`. Yu'lon never downloads it for you.[^catalog]

The build is 18414. The Recall client logs in to this server with no exe patch, so the login button is the normal one: no `@` name and no Battle.net.[^catalog] Yu'lon sets the realmlist and the locale (enUS) in the client's config for you. See [Playing](/playing/index.md).

## What is special

- Bots level from 1 to 90, Pandaria included.[^catalog]
- The first Mists of Pandaria install appeared in release v0.9.31.[^changelog]
- On Windows, the map data is built inside Docker rather than through the slow Windows folder share.[^changelog]
- It needs more memory than most: see [Pre-install checks](/getting-started/pre-install-checks.md).
- Linux and Windows.[^catalog]

## Limits

- It is a beta. The Characters tab offers none of its character actions here yet: each is withheld, with a note that it is offered once it has been checked against a live server.[^chars]
- My Party is not available.
- Bots live on accounts whose names start RNDBOT, which is how the Bots tab finds them.[^catalog]

## If something goes wrong

Bugs in the server and its bots go to the LoA Pandaria project (listed in the app's Help places). If the client check refuses, make sure you unpacked the Recall folder fully. More: [Common problems](/getting-started/common-problems.md).

[^catalog]: Description, client notes, build, bot count and platforms from catalog.json.
[^changelog]: v0.9.31 release notes.
[^chars]: The list of actions confirmed on a live server is empty for Mists of Pandaria.
