---
type: Guide
title: "Default add-ons"
description: "Which game add-ons Yu'lon puts into your client by itself, and how to remove one so it stays removed."
tags: [addons, defaults, tortoise, coa, remove]
resource: pylauncher/yulon/default_addons.py
sources:
  - id: default
    resource: pylauncher/yulon/default_addons.py
    title: How default add-ons are put in and remembered as removed
  - id: tortoise
    resource: pylauncher/yulon/controller_wow_tortoise/modules.py
    title: The two Tortoise default add-ons
  - id: coa
    resource: pylauncher/yulon/controller_wow_coa/modules.py
    title: The Conquest of Azeroth default add-on
  - id: manifests
    resource: pylauncher/manifests/wow-coa/mods/yulon-squidbots-lite.json
    title: SquidBots Lite manifest
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Default add-ons

Two games come with add-ons that are put into your game client for you, so the in-game panels for the server's bots and tools are just there.[^default]

| Game | Add-ons Yu'lon puts in |
|---|---|
| WoW Tortoise | TortoiseBots Manager (`/tbm`, a panel for the bots) and Tortoise GM Manager (`/tgmm`, a GM command palette) |
| WoW Conquest of Azeroth | SquidBots Lite (bot orders, bot roles on party frames, bot offers, My alts) |


The add-ons are put in on a fresh install, and again at Play if the files are missing from the client you play with, for example after a ready-to-play client was made again or you deleted the folder by hand. At Play nothing is cloned or fetched: a slow network never holds Play. Updates are the Modules tab's job: **Check for updates** and **Update**.[^default]

Other games have their own extras, such as Unbound's add-ons in a client pack: see [Client updates, patches and extras](/playing/client-updates-and-patches.md).

## Remove one so it stays removed

1. Open the **Modules** tab of the server.
2. Find the add-on in the list.
3. Press **Remove** for it. Yu'lon takes back the files it put in your client. A file you changed stays, and your saved settings (`WTF`) are never touched.[^default]

Yu'lon remembers the removal for that server, in a small note file in the server's folder. Later Plays do not put the add-on back.[^default]

To get it back, install it again from the Modules tab by hand. That clears the note.[^default]

If Yu'lon cannot read its note, it puts in nothing rather than guess and put back something you removed.[^default]

## If you brought your own copy

If you added an add-on of your own that writes to the same folder as a default one, your copy wins and the default is left out. Yu'lon says so instead of writing over yours. See [Add your own game add-ons](/playing/your-own-addons.md).[^default]

## What you'll see

The add-on shows as installed on the Modules tab, and a Tortoise server's update also updates TortoiseBots Manager in your client.[^default]

## If something goes wrong

- A failed install at start-up is left alone for a day before Yu'lon tries again, so it does not retry at every start.[^default]
- Yu'lon writes into the client you play with: the ready-to-play client when there is one, else the folder you set. See [Point Yu'lon at your own game client](/playing/set-your-client-folder.md).
- The module side of add-ons is in [Install, remove and update modules](/running-a-server/modules.md).

[^default]: The defaults, when they are put in and how a removal is remembered are in the default add-ons module.
