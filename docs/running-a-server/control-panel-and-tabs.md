---
type: Guide
title: The server control panel and its tabs
description: A tour of the tabs on a server's page, so you know where each job lives.
tags: [control-panel, tabs, server, overview]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Builds the server page and every tab on it
  - id: presses
    resource: pylauncher/yulon/server_build_presses.py
    title: Spells the tab name "Backups & updates" and the Server version buttons
  - id: badge
    resource: pylauncher/yulon/ui/widgets/dadcraft_decorations.py
    title: The realm badge words
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# The server control panel and its tabs

Every server you install gets its own page in Yu'lon, with a row of tabs across the top. This page tells you what each tab is for.[^view]

## Opening it

Pick the server's short name on the left rail (for example "WotLK"), or press **Open server** on its Catalog tile. If you run two servers of the same game, the rail adds the folder name so you can tell them apart.[^view]

## The tabs

| Tab | Use it to |
| --- | --- |
| **Server** | Start, stop and restart the server, see its status, set the game client folder, and reach the Danger zone for removing it. |
| **Console** | Follow the world server's log and type server commands. See [Console and server commands](/running-a-server/console-and-server-commands.md). |
| **Accounts** | Create accounts, set GM levels and passwords. See [Game accounts](/running-a-server/game-accounts.md). |
| **Characters** | Pick a character and act on it (level, teleport, revive, and more). |
| **Bots** | Settings for the bot population, on games that have bots. |
| **Backups & updates** | Back up, restore, update or rebuild the server, and free Docker space. |
| **Modules** | Install, remove and update modules. See [Install, remove and update modules](/running-a-server/modules.md). |
| **Tuning** | Change server settings, rates and the time zone. See [Edit settings and config files](/running-a-server/edit-config-files.md). |
| **Networking** | Choose who can connect and set the realm name. See [Play with friends](/running-a-server/play-with-friends.md). |

What a tab offers depends on the game. A button that does not apply to your game is hidden, and a button that is greyed explains why in a line under the buttons.[^view]

## The Server tab, section by section

- **Realm** shows the status line and the Start, Stop, Restart and Refresh buttons. The status line reads like "Database running · Login server running · World server running".[^view]
- **Play** and **Client** hold the game-client buttons. Yu'lon never ships a game client; you bring your own.
- **Command channel** is how Yu'lon talks to the running server. It has a **Test the console** button where the game supports it.[^view]
- **Help** opens help pages for the game, where there are any.
- **Danger zone** removes the server. See [Remove or uninstall a server](/running-a-server/remove-or-uninstall.md).

## The realm badge

A badge at the top of the page tells you the state at a glance:[^badge]

- **REALM ONLINE**: the server is up.
- **STARTING / BUSY**, **STOPPING** or **PARTLY UP**: it is changing state or only some parts run.
- **CRASH LOOP** or **UPDATE FAILED**: something is wrong; see [Troubleshooting](/running-a-server/troubleshooting.md).
- **REALM OFFLINE**: it is stopped.
- **STATUS UNKNOWN**: Yu'lon cannot ask Docker right now.

## The "Backups & updates" name

Some messages say "the Maintenance tab". That is the same tab as **Backups & updates**.[^presses]

## If something goes wrong

If a tab looks empty or greyed, read the grey line under its buttons first. For anything else, see [Troubleshooting](/running-a-server/troubleshooting.md).

[^view]: Tab names, section titles and button labels come from the server page code.
[^presses]: The tab title is stored once and every message reads it from there.
[^badge]: The badge words are drawn by the realm badge widget.
