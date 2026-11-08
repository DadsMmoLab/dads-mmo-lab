---
type: How-to
title: "HD patches"
description: "Turn Conquest of Azeroth's HD patches on or off, and add Centurion's optional HD packs."
tags: [hd, patches, textures, coa, centurion]
resource: pylauncher/yulon/hd_patches.py
sources:
  - id: hd
    resource: pylauncher/yulon/hd_patches.py
    title: The per-install HD patches choice
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: Centurion's optional HD packs
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# HD patches

Some servers can show higher resolution art. How you switch it depends on the game.

## Conquest of Azeroth: Enable HD patches

The Conquest of Azeroth client carries its own HD texture and model patches. Under **Play** on the Server tab there is a checkbox **Enable HD patches**. It is on by default, as in the official launcher.[^hd]

1. Open the **Server** tab of your Conquest of Azeroth server.
2. Under **Play**, untick **Enable HD patches** for a lighter, original look, or tick it to turn them back on.
3. Start the game with **Play**.

The choice is saved the moment you change it, and applies the next time you start the game. Yu'lon shows "HD patches are off from the next time you start the game." (or "on").[^hd] It is kept per server, so it survives a deleted or remade ready-to-play client. If the saved choice cannot be read, HD stays on.

If you made a Steam shortcut earlier with **Add to Steam…**, it keeps the old choice until you use Add to Steam again.[^hd] See [Controller and Steam Deck](/getting-started/controller-and-steam-deck.md).

## Centurion: optional HD packs

Centurion offers optional packs, all off by default: **HD Creatures**, **HD Textures**, **HD Spells**, **HD Battlegrounds**, **HD Interface** (a Shadowlands style interface) and **Alt World**.[^catalog] To add or remove them:

1. Open the **▾** menu beside Play and choose **Client options…**.
2. Tick the packs you want.
3. Press **Save**, then **Play**. The packs download then; they are large, so the first Play takes a while.

See [Client updates, patches and extras](/playing/client-updates-and-patches.md) for how packs are fetched.

## If something goes wrong

- A save that fails (for example a folder you cannot write to) puts the box back and says why.
- If a pack's download is cancelled, what arrived is kept for next time.

[^hd]: The checkbox, its text and the saved choice are in the HD patches module and the Server tab.
[^catalog]: The pack names and their optional, off-by-default state are in the Centurion entry of the Catalog.
