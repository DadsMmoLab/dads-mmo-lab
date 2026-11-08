---
type: How-to
title: "Install a server"
description: "Press Install on a Catalog tile, choose a folder, and follow the install until the server runs."
tags: [install, catalog, folder, client, progress]
resource: pylauncher/yulon/ui/catalog_view.py
sources:
  - id: catalogview
    resource: pylauncher/yulon/ui/catalog_view.py
    title: "Install button, folder question, install log"
  - id: native
    resource: pylauncher/yulon/catalog/native.py
    title: "The install engine and its opening note"
  - id: catalogjson
    resource: pylauncher/yulon/catalog/catalog.json
    title: "Default folders, client-folder requirement"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:25:00Z
status: draft
---

# Install a server

Pressing **Install** checks your computer, builds the server from source in Docker, and starts it. It can take a long time, and you can stop it at any time.[^native]

## Before you start

- Have enough free disk space and memory: [Pre-install checks](/getting-started/pre-install-checks.md).
- For **WoW TBC, WoW Vanilla, WoW Tortoise, Centurion, WoW Conquest of Azeroth and WoW Mists of Pandaria**, have your own game client ready. Yu'lon asks for its folder. **WoW WotLK** and **WoW Unbound** do not ask for one at install time.[^catalogjson]
- Close any other install that is still running. Yu'lon says "Another job is still running." if one is.[^catalogview]

## Steps

1. Open **Catalog**, open the game card, and press **Install** on the tile.
2. Yu'lon asks "Install *game* into this new folder?" and names a folder in your home folder. The default is `yulon-` plus the game, for example `yulon-wotlk`.[^catalogjson] Choose one:
   - **Use this folder** takes the suggestion. Yu'lon creates the folder when the install starts.
   - **Choose another folder...** opens a folder picker.
   - **Cancel** stops here and changes nothing.[^catalogview]
3. If the game needs a client, Yu'lon asks "Select your *version* client folder". The window says the app never downloads one. Pick the folder where the game is installed.[^catalogview]
4. The install log opens. Yu'lon prints the checks first (see [Pre-install checks](/getting-started/pre-install-checks.md)), then builds. A **Stop** button is there while it runs, and a line shows "step N of M" with the step name. You can hide the log with **Hide install log** and bring it back with **Show install log**.[^catalogview]
5. Wait for the last line: "*game* is installed and running in *folder*".[^native]

## What you'll see

The tile's **Install** button turns into **Open server**. A new tab for the server appears, with Start and Stop, the console, accounts and the rest.

## If you stop or it fails

- Starting the install again continues where it stopped. Work already finished, like source that was already cloned, is not repeated.[^native]
- If you cancel, Yu'lon shows "Install cancelled" and does not add a server tab.[^catalogview]
- If a check refuses, a box titled "Cannot install yet" says what to change. Nothing was written.[^catalogview]
- Other failures show "Install stopped" with the reason. See [Common problems](/getting-started/common-problems.md).

## What the install writes outside your folder

The images it builds and the volumes holding the database and server data live in Docker's own storage. Docker itself is installed if it is missing. And Yu'lon's own settings and log go in its config folder. See [Where Yu'lon keeps its files](/getting-started/where-files-live.md).[^native]

[^native]: The opening note and the closing line are in catalog/native.py.
[^catalogjson]: default_server_dir and requires_client_dir per game in catalog.json.
[^catalogview]: Dialog titles, button labels and messages are in ui/catalog_view.py.
