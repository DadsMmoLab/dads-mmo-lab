---
type: How-to
title: Start, stop and restart your server
description: Start and stop the server from the Server tab, understand the stop choice, and know what the status words mean.
tags: [start, stop, restart, status, bots]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Start, Stop, Restart and the status line
  - id: stopchoice
    resource: pylauncher/yulon/stop_choice.py
    title: The "When stopping" choice and its wording
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Start, stop and restart your server

The **Server** tab has three buttons for the server's life: **Start**, **Stop** and **Restart**. A **Refresh** button asks Docker again how things are.[^view]

## Start the server

1. Open your server's page and the **Server** tab.
2. Press **Start**.
3. The status line says "Starting…" and the badge says STARTING / BUSY. A server with bots can take a few minutes to load its world.

### What you'll see

When the world is ready the badge turns to **REALM ONLINE** and the status line reads "Database running · Login server running · World server running".[^view]

## Stop the server

1. Press **Stop**.
2. The status line says "Stopping…". Players are always saved first.[^stopchoice]
3. When it finishes the badge reads **REALM OFFLINE**. Yu'lon also saves a copy of the server's log when you stop it.[^view]

If a world is still loading, a stop waits until the world can hear it. While it waits, a **Stop now anyway** button appears. Use it only if you accept losing anything since the last save.[^view]

## The "When stopping" choice

Some games show a drop-down labelled **When stopping:** under the buttons. It has two choices:[^stopchoice]

- **Wait for the bots to be saved (slower, safe)**: the default. Stop waits until every bot is saved.
- **Do not wait for the bots to be saved (faster; bots may lose their latest changes)**: Stop finishes sooner, but bots may lose what they did since their last save.

Backups, updates, rebuilds, realm switches, moves and removals always wait for every save, whatever you pick. The choice is kept per server, and only the Stop and Restart you press use it. It is greyed until the server's system is running, and it does not appear for games that offer no choice.[^stopchoice]

## Restart

**Restart** stops the server, saving every character, then starts it again. Everybody online is disconnected.[^view]

## Only one server at a time

If another server of yours already uses the same ports, Start says so and offers **Stop the other server and start this one**. Only one server can run at a time.[^view]

## If something goes wrong

- Start and Stop greyed with a banner above: Docker is the problem. See [Troubleshooting](/running-a-server/troubleshooting.md).
- Stop says another Yu'lon is working on this server: it names what it is doing and asks before stopping anyway.[^view]
- Badge says **CRASH LOOP**: the world keeps restarting. Open the **Console** tab and follow the log ([Console and server commands](/running-a-server/console-and-server-commands.md)).

[^view]: Button labels, status words and notes are in the server page code.
[^stopchoice]: The choice, its labels and its note are defined in the stop choice module.
