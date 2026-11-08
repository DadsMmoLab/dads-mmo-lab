---
type: Troubleshooting
title: Troubleshooting a server that is running
description: Fixes for the problems you can meet once a server is installed, from Docker not answering to a world that keeps restarting.
tags: [troubleshooting, docker, ports, crash, repair]
resource: pylauncher/yulon/docker_advice.py
sources:
  - id: advice
    resource: pylauncher/yulon/docker_advice.py
    title: What the banner says per system and failure
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Server tab messages, banners and repair buttons
  - id: badge
    resource: pylauncher/yulon/ui/widgets/dadcraft_decorations.py
    title: The realm badge words
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Troubleshooting a server that is running

Find the message or badge you see, then follow the line beside it. Whenever something is unclear, save your logs and send them: [Logs](/running-a-server/logs.md).

## "Yu'lon can't ask Docker about this server right now" / STATUS UNKNOWN

Yu'lon cannot reach Docker. A banner on the Server tab says what to do for your system, and **Start** and **Stop** come back when Docker answers.[^advice]

- **Windows or macOS**: open Docker Desktop and wait until it says Engine running. Yu'lon checks again every few seconds. If Docker Desktop did not let Yu'lon in, sign out and in again (Windows: your account must be in the docker-users group) or restart Docker Desktop (macOS).
- **Linux**: Docker is installed but not running. Restart the computer, or run `sudo systemctl start docker` (or `restart`) in a terminal. If Docker is running but "your account isn't allowed to use it yet", log out and back in so your account joins the docker group.
- **Steam Deck**: the same, from Desktop Mode, and restart the Deck if needed.
- **Server inside WSL on Windows**: the Docker inside that distro is not answering; restart the computer or start it there.

Press **Try again** on the banner when done.[^advice]

## Docker's Compose is out of date

If the Server tab shows **Update Docker Compose**, press it. It is offered when your Compose is too old for the server.[^view]

## "Only one server can run at a time"

Another server holds the same ports. The Server tab names it and offers **Stop the other server and start this one**. If the port is held by something that is not a Yu'lon server, free the port and start again.[^view]

## "Another Yu'lon is working on this server"

Another Yu'lon of yours holds the server. A Stop names what it is doing and asks. A leftover reservation from an earlier run can be removed with **Clear it**; it is only offered when no other Yu'lon of yours is running.[^view]

## CRASH LOOP or UPDATE FAILED

The world keeps restarting, or a database update failed. Open the **Console** tab and press **Follow worldserver log** ([Console and server commands](/running-a-server/console-and-server-commands.md)). If it started after you added a module, remove the module and rebuild ([Install, remove and update modules](/running-a-server/modules.md)). If it started after an update, use **Return to the tested pin...** ([Update or rebuild the server](/running-a-server/update-and-rebuild.md)). If your databases are already ahead of the tested version, Yu'lon will not go back and names the backup from before the update to restore instead.[^badge]

## "Table ... is marked as crashed"

A table was damaged by a sudden stop. Use **Repair the damaged table...** ([Repair damaged tables](/running-a-server/repair-damaged-tables.md)).

## "Docker no longer has this server's database"

Use **Repair the database...**, then restore a backup ([Repair damaged tables](/running-a-server/repair-damaged-tables.md), [Restore a backup](/running-a-server/restore-a-backup.md)).[^view]

## Docker files differ from this Yu'lon

A banner says the server's Docker settings file differs from what this version writes (another version wrote it, or it was edited by hand). **Repair server files...** writes it again the way this version does and keeps your current file as a backup. Nothing changes until you press it. Applying it may need **Recreate containers...**.[^view]

## Friends cannot connect

Check the plan on the Networking tab, your firewall and your router port forwarding: [Play with friends](/running-a-server/play-with-friends.md).

## The server is slow to stop

A world still loading cannot stop until it can hear the stop. **Stop now anyway** forces it, losing anything since the last save. The "When stopping" choice lets Stop skip waiting for bots ([Start, stop and restart](/running-a-server/start-stop-and-restart.md)).[^view]

## A button is greyed

The reason is on the grey line under the buttons, or on the button's tip. Common ones: another job is running ("Wait: ... is running"), or the database password is unknown.

## Out of disk space

Delete old backups ([Back up your server](/running-a-server/backups.md)) and free Docker space ([Free Docker space](/running-a-server/free-docker-space.md)).

[^advice]: The banner words per system are in the Docker advice module.
[^view]: Messages and buttons are in the server page code.
[^badge]: Badge words are drawn by the realm badge widget.
