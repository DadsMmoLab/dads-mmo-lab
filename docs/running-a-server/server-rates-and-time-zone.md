---
type: How-to
title: Server rates and time zone
description: Change XP, gold, drop, reputation and honor rates, and choose the server's time zone, from the Tuning tab.
tags: [rates, xp, time-zone, tuning, drops]
resource: pylauncher/yulon/server_rates.py
sources:
  - id: rates
    resource: pylauncher/yulon/server_rates.py
    title: The Server rates card and its limits
  - id: zone
    resource: pylauncher/yulon/server_time_zone.py
    title: Reading and setting the server's time zone
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The time zone box and its labels
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Server rates and time zone

Two server-wide settings have their own spot at the top of the **Tuning** tab: the **Server rates** card and **Server time zone**.[^view]

## Server rates

The card has built-in rows for every game:[^rates]

- **XP from kills**, **XP from quests**, **XP from exploring**
- **Gold dropped**
- **Grey item drops**, **White item drops**, **Green item drops**, **Blue item drops**, **Purple item drops**
- **Reputation gained**
- **Honor gained**

For every row, 1 is the normal amount, 2 is double and 0.5 is half. Values from 0 to 100 are accepted. The top end is Yu'lon's own safety limit that keeps a typo like 1000 from reaching your realm; it is not a rule of the game server.[^rates]

### Steps

1. Open the **Tuning** tab and find **Server rates**.
2. Change a number.
3. Press **Save** on the card.
4. Restart the server so the world re-reads the file. The report names what is needed; see [Edit settings and config files](/running-a-server/edit-config-files.md).

### Good to know

- Some games add small notes on a row. For example, on TBC the old-world and Outland versions of a rate multiply on top, and on some games gold also multiplies the money quests reward.[^rates]
- If you install the XP Rate Customization module, its XP rows become read-only and say the Server rates card sets them. While it is installed, **Reset to default** keeps that value, and removing the module sets it back to 1.[^rates]

## Server time zone

The server's clock decides when daily quests reset, when calendar events start and the times in its logs.[^zone]

1. In **Server time zone**, use the first drop-down to pick **Same as this computer** (it shows the zone), **UTC**, or a region. A value you wrote by hand shows as "As written: ...".
2. If you chose a region, pick the place in the second drop-down.
3. Press **Apply...** and answer the question.

You pick from lists; you do not type a zone. Yu'lon changes only the time zone setting in the server's the server's Docker settings file and makes a backup of it first.[^zone]

### What you'll see

The question explains what changes. The running server keeps its time zone until its containers are recreated, and Yu'lon offers the recreate when the change is done.[^zone]

## If something goes wrong

- "... is not on disk, so there is no time zone to read or change. Repair the server to write it again."
- If the server's Docker settings files were not made by Yu'lon, Yu'lon does not rewrite them and tells you to set TZ yourself.[^zone]
- If TZ appears more than once, remove the extra line and press Reload.
- Box missing: games whose Docker settings files Yu'lon does not make have no time zone box.[^zone]
- See [Troubleshooting](/running-a-server/troubleshooting.md).

[^rates]: The rows, limits and notes are in the server rates module.
[^zone]: The question, file and failure messages are in the time zone module.
[^view]: The box labels (Server time zone, Apply...) are in the server page code.
