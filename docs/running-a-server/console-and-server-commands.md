---
type: How-to
title: Console and server commands
description: Watch the world server's log live and type server commands from the Console tab.
tags: [console, commands, log, worldserver]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Console tab and its messages
  - id: commands
    resource: pylauncher/yulon/commands.py
    title: How a typed command is cleaned, and which shutdown commands are refused
  - id: redact
    resource: pylauncher/yulon/support/redact.py
    title: Hides passwords in the echo of typed commands
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Console and server commands

The **Console** tab shows what the world server is saying and lets you type commands to it, the way you would at the server's own console window.[^view]

## Follow the log

1. Open the **Console** tab.
2. Press **Follow worldserver log**. Lines appear as the server writes them.
3. Press the log panel's **Stop** button to stop following.

The log header reads "log ended - the world server is not running" if the server stops while you watch.[^view]

## Send a command

1. Click the box that says "console command, e.g. server info".
2. Type a command. A leading `.` or `!` is fine and is dropped; the command must be one line.[^commands]
3. Press **Send**.

### What you'll see

Your command is echoed as "> command", then the server's reply. Yu'lon waits about three seconds for a reply. If nothing comes back you see "(no reply inside the 3s window)", which is normal for commands that print nothing. If the server is still starting you may see a note saying what follows is not an answer.[^view]

Passwords in `account create` and password commands are hidden in that echo.[^redact]

## Shutdown commands are refused

Typing `server shutdown`, `server exit` or `server idleshutdown` (also shortened) does not go through. Yu'lon says: "Not sent: a shutdown typed here closes the world server, and Docker starts it again at once. To stop the server and keep it stopped, press Stop on the Server tab." `server restart` is allowed.[^commands]

## If you cannot type

On some computers **Send** is greyed with "This computer can't type at this server's console; the note below says why." Following the log still works there.[^view] Making accounts does not need the console; use the **Accounts** tab ([Game accounts](/running-a-server/game-accounts.md)).

## If something goes wrong

- Send is greyed while the last command is still waiting; it comes back by itself.
- A game with no command console says on the Server tab that there is nothing to turn on.
- Nothing appears when you follow the log: the server may be stopped. Start it first.

[^view]: Button labels, the placeholder and the messages are in the server page code.
[^commands]: The command cleaning and the shutdown check are in the commands module.
[^redact]: The echo of typed commands goes through a password mask.
