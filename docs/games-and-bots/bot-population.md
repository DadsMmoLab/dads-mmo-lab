---
type: How-to
title: "Change how many bots your server runs"
description: "Use the Random bots box on the Bots tab to raise or lower the bot count, and learn when it takes effect."
tags: [bots, population, bots-tab, tuning, apply]
resource: pylauncher/yulon/bot_population.py
sources:
  - id: pop
    resource: pylauncher/yulon/bot_population.py
    title: "Reading and writing the bot count"
  - id: count
    resource: pylauncher/yulon/catalog/bot_count.py
    title: "Keeping the bot count across rewrites"
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: "The Random bots box and the delete/rebuild texts"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Change how many bots your server runs

The **Random bots** box on the Bots tab sets how many random bots the server keeps online. Yu'lon sets the lowest and the highest number to the same value, as the install does.[^pop]

## Steps

1. Open your server and go to the **Bots** tab.
2. In the **Random bots** group, type a number next to "Random bots:".
3. Press **Apply...** and read the question. It says which file gets the number, that a backup is made beside it first, and when the server will use it.
4. Confirm.

## What you'll see

A line says "Random bots set to N in <file>; the file as it was is beside it". If the number was already N, it says nothing was written.[^view]

The running server keeps its current bots until it is restarted. On WotLK, Unbound and Conquest of Azeroth, the bots use the server's container settings, so the server's containers must be recreated; Yu'lon offers the job when the write is done. After that the bots log in again, up to your number, which can take a few minutes.[^pop]

## Where the number lives

| Servers | Kept in |
|---|---|
| WotLK, Unbound, Conquest of Azeroth | The server's container settings (the server's Docker settings file). Only the two bot-count lines change. |
| TBC, Vanilla, Tortoise | `aiplayerbot.conf` in the server's etc folder. On the Tuning tab it appears as the "Playerbots (server settings)" card; **Revert** there puts the old file back. |
| Mists of Pandaria | `playerbots.conf` in the etc folder |
| Centurion | `playerbots.conf` in the etc folder, as "RandomPopulation" target lowest and highest |

## Limits

- On TBC and Vanilla, each bot account holds at most nine bot characters, so the box refuses numbers above the accounts times nine. Raise "Bot accounts" on the Tuning tab for more.[^pop]
- More bots than Yu'lon installed need more memory and processor time, and the question warns you.[^pop]
- A repair, an update or Update to latest keeps your count rather than putting the installed one back. Reset to default on the Tuning tab does put the installed number back.[^count]
- The box stays greyed until the files are read, while another job runs, and while its own write runs. A line under it says why.[^view]

## Starting over with the bots

- **Rebuild random bots...** (Tortoise only) deletes every random bot and makes them again from scratch. They lose level, gear, bags, bank, quests and professions. The bot accounts and your own characters stay. The server restarts, and anyone playing is disconnected.[^view]
- **Delete every random bot...** (WotLK, Unbound, Conquest of Azeroth) deletes the bots' accounts and characters from the databases. All or nothing, and the server must be stopped. The bots module makes its random bots again when the server starts.[^view]

## If something goes wrong

- "... is not on disk": Repair the server so the file is written again.
- "... were not made by Yu'lon": Yu'lon will not rewrite a Docker settings file it did not write; change the number in that file yourself.
- "The bot count was NOT changed": nothing was written; read the reason shown and press Apply again.[^pop][^view]

[^pop]: The bot population controls: routes, ceilings, questions and refusals.
[^count]: The bot count step: the count a rewrite carries over from the file on disk.
[^view]: Texts of the Random bots box, Rebuild and Delete dialogs.
