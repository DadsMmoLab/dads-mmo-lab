---
type: Reference
title: "Client and Play buttons on the Server tab"
description: "Every button and menu entry in the Play and Client boxes of the Server tab, what it does and when it shows."
tags: [buttons, play, client, steam, reference]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Play and Client sections of the Server tab
  - id: update
    resource: pylauncher/yulon/ui/client_update.py
    title: Update client labels
  - id: hd
    resource: pylauncher/yulon/hd_patches.py
    title: Enable HD patches label
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# Client and Play buttons on the Server tab

The **Server** tab has a **Play** box and a **Client** box. This page lists what is in them.[^view] The other boxes (Realm, Command channel, Danger zone) are in [The server control panel and its tabs](/running-a-server/control-panel-and-tabs.md).

## The Client box

| Control | What it does |
|---|---|
| "Client folder: ..." line | Shows the folder, or says none is set, it is missing, or it has no `Interface` folder yet (start the game once). |
| Add-on pointer line | Points to **Game add-ons you bring** on the Modules tab: [Add your own game add-ons](/playing/your-own-addons.md). |
| **Set client folder…** / **Change client folder…** | Choose your own client. See [Point Yu'lon at your own game client](/playing/set-your-client-folder.md). |
| **Forget client folder** | Clears the folder; shows only when one is set. |

## The Play box

| Control | What it does |
|---|---|
| **Play** | Starts the game: [The Play button](/playing/the-play-button.md). Reads **Make a ready-to-play client…** until a copy exists. Hidden until a client folder is set or a copy exists. |
| **Launcher…** | Opens the per-server window: [The Launcher… window](/playing/launcher-window.md). |
| **▾** menu | **Client options…** (where the server has optional extras), **Refresh from your original client**, **Delete ready-to-play client…**, and **Undo the last client update…** where there is an update. See [The ready-to-play client](/playing/ready-to-play-client.md). |
| **Cancel** | Shows while Play gets the client ready; stops it. |
| **Add to Steam…** | Puts the server and its client in your Steam library for Gaming Mode. It is built only on Linux; Windows and macOS have no such button. Steps are in [Controller and Steam Deck](/getting-started/controller-and-steam-deck.md). |
| **Update client** | Conquest of Azeroth only: [Client updates](/playing/client-updates-and-patches.md). With **Update client and replace the changed files** when your client is the stock Ascension one. |
| **Enable HD patches** | Conquest of Azeroth only: [HD patches](/playing/hd-patches.md). |

A line under the buttons says what Play is doing or did. While Play or Make is running, buttons that would collide with it are greyed, and a reason line says why.[^view]

## If a button is missing

- **Play** is missing: set your client folder first. The Play box says "Play needs your own client folder first — set it under Client below."
- **Add to Steam…** is missing: you are not on Linux or Steam Deck.
- **Update client** or **Enable HD patches** are missing: your game is not Conquest of Azeroth.

[^view]: The Play and Client sections, their labels and tooltips are in the Server tab code.
