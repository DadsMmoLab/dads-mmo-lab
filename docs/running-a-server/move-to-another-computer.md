---
type: How-to
title: Move your server to another computer
description: Pack accounts and characters, or the whole server, into one file and bring it into a server on another computer.
tags: [move, pack, bring-in, migration, backup]
resource: pylauncher/yulon/move_server.py
sources:
  - id: server
    resource: pylauncher/yulon/move_server.py
    title: Packing a whole server and building it again
  - id: flows
    resource: pylauncher/yulon/move_flows.py
    title: Packing and bringing in accounts and characters
  - id: panel
    resource: pylauncher/yulon/ui/widgets/move_panel.py
    title: The Move to another computer panel
  - id: catalog
    resource: pylauncher/yulon/ui/catalog_view.py
    title: Bring from another computer on the Catalog tile
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Move your server to another computer

There are two ways to move: only the accounts and characters, or the whole server. Both make one file you carry to the other computer. The panel is at the bottom of the **Backups & updates** tab, titled **Move to another computer**.[^panel]

The file holds every account's login data. Keep it private and delete it once the move is done.[^panel]

## Move accounts and characters

Use this when the other computer already has a server of the same game.[^flows]

On the old computer:

1. Open **Backups & updates** and find **Move to another computer**.
2. Press **Pack for another computer...**
3. Read the question and say yes, then choose where the file should go.
4. Wait: "Packing... this can take a few minutes with many bots."

On the new computer:

1. Open the same tab and press **Bring in accounts and characters...**
2. Choose the file. Read the question: logins, passwords, GM levels and bots come with it, and the server's own accounts, characters and bots are replaced. Its world, realm address and settings stay as they are.
3. Say yes. If the server was running it is stopped, and it stays stopped; press **Start** when ready.[^panel]

The world itself (custom items, NPCs) is not in this file. Both servers must be the same game, and at the same database version.[^flows]

## Move the whole server

Use this to build the same server again on a new computer, with the world, settings, module answers and the version of each module and of the server it was built from. The new computer builds it again at that version.[^server]

1. On the old computer press **Pack the whole server...**
2. If the server runs, it is stopped while packing and started again afterwards.
3. On the new computer open the **Catalog**, pick the game, and press **Bring from another computer...** on the tile. Choose the file and a folder.
4. Wait for the install and the data to load.

The database password and Yu'lon's command-channel login are not in the file. Built images, map data, logs, backups and the game client are not either.[^server] Bring your own client.

Afterwards the Server tab offers to update or return to the tested version. See [Update or rebuild the server](/running-a-server/update-and-rebuild.md).[^server]

## What you'll see

Before a load, Yu'lon takes a copy of the target's data. Those copies show in the backups list, so you can restore if you change your mind.[^server]

## If something goes wrong

- "This cannot go ahead:" lists each reason; nothing was changed.
- "Packing did not finish. Nothing was changed on this server."
- If a whole-server bring-in stops part way, pressing **Bring from another computer...** with the same file and folder carries on. To give up and start fresh, see [Start over after an unfinished bring-in](/running-a-server/start-over.md).[^catalog]
- A bring-in cannot run while another one runs on the same folder.

[^panel]: Buttons and questions are in the move panel.
[^flows]: Accounts-and-characters moves are in the move flows module.
[^server]: The whole-server pack and import are in the move server module.
[^catalog]: The Catalog tile message is in the catalog view.
