---
type: How-to
title: "The bot dashboard (Tortoise)"
description: "Switch on the TortoiseBots web dashboard to see a live map of your bots, stuck bots and an Armory."
tags: [bot-dashboard, tortoise, bots-tab, map, armory]
resource: pylauncher/yulon/catalog/bot_dashboard.py
sources:
  - id: dash
    resource: pylauncher/yulon/catalog/bot_dashboard.py
    title: "What the switch writes: three conf keys and one Docker settings block"
  - id: tort
    resource: pylauncher/yulon/controller_wow_tortoise/botdash.py
    title: "The Docker half: image, container, restart"
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: "The Bot dashboard group and its texts"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# The bot dashboard (Tortoise)

The bot dashboard is the bots module's own web page: a live map of every bot, their health and what they are doing, stuck bots, and an Armory that shows any bot's gear.[^view] It is offered on the [WoW Tortoise](/games-and-bots/wow-tortoise.md) server only. Other servers do not show the group at all.[^tort]

You sign in with one of your game accounts that has GM rank 2 or higher.[^view][^dash]

## Switch it on

1. Open your Tortoise server and go to the **Bots** tab.
2. In the **Bot dashboard** group, tick the box that says "Bot dashboard: Off". It then reads "Bot dashboard: On".
3. Leave **Allow other devices on my network** off unless you need it (see below).
4. Read the question. It lists every change, the restart, and who can reach the page. Confirm.
5. Watch the log panel in the group while it works.
6. When it is done, press **Open bot dashboard**. The page is at `http://localhost:8095`.[^dash]

## What you'll see

Yu'lon uses TortoiseBots' ready-made program when one fits your bots module, checked against its published checksum. If none fits, it builds the module's own, which takes a few minutes the first time.[^view][^tort] It turns on the module's telemetry in `tortoise_bots.conf`, keeping a copy, adds one service to the server's Docker settings file, starts it, and restarts the server if it was running so the bots module finds the dashboard. Anyone playing is disconnected for a few minutes.[^view]

Each install gets its own random session secret, and no password is ever written into the server's Docker settings file.[^dash]

## Other devices on your network

By default only this PC can open the dashboard. **Allow other devices on my network** opens it to every device on your network. The app warns that anyone there can reach the sign-in page and read the /metrics page (bot counts, bot problems) without signing in, and that signing in sends your GM password over plain, unencrypted HTTP. The choice is fixed while the dashboard is on: switch it off first to change it.[^view]

## Switch it off

Untick the box and confirm. The dashboard is stopped and removed from the server's Docker settings file, and the three telemetry settings go back to what they were before you switched it on. If you changed those three by hand since, your changes to them are replaced. The app offers a server restart; skipping it does no harm.[^view]

## After a server update

When an update moves the bots module, Yu'lon rebuilds the dashboard for it. If that fails, the dashboard stays on but stopped, a line explains why, and **Rebuild the bot dashboard** appears. Start also refuses to bring back the old one.[^dash]

## If something goes wrong

- **Open bot dashboard** is greyed until the dashboard is on; a line under the buttons says why a press is unavailable.[^view]
- If you cannot sign in, check that the account has GM rank 2 or higher: see [Game accounts](/running-a-server/game-accounts.md).
- Other trouble: [Common problems](/getting-started/common-problems.md).

[^dash]: The dashboard switch: port 8095, minimum GM level, the three conf keys, the Docker settings block and the rebuild-owed record.
[^tort]: The Tortoise dashboard switch: the on and off sequence; wired only for Tortoise.
[^view]: Texts and buttons of the Bot dashboard group.
