---
type: Reference
title: "Playerbots settings and playerbots.conf"
description: "Which bot settings Yu'lon itself sets on each game, where they live, and how that affects your own edits."
tags: [playerbots, playerbots-conf, tuning, battlegrounds, settings]
resource: pylauncher/yulon/playerbots_keys.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The bot settings each install writes"
  - id: keys
    resource: pylauncher/yulon/playerbots_keys.py
    title: "Which key names the bots module reads"
  - id: realm
    resource: pylauncher/yulon/coa_realm.py
    title: "Conquest of Azeroth's retired battleground lines"
  - id: pop
    resource: pylauncher/yulon/bot_population.py
    title: "Where the bot count is kept"
  - id: changelog
    resource: CHANGELOG.md
    title: "Release notes"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Playerbots settings and playerbots.conf

The bots module reads its settings from a file called `playerbots.conf` (`aiplayerbot.conf` on the older CMaNGOS servers). Yu'lon sets a few of them when it installs. This page lists which ones, so you know what is yours to edit and what Yu'lon will keep writing.

## Where you change settings

- The **Tuning** tab lists the server's settings, including the bot card, and keeps a backup so you can revert. On TBC, Vanilla and Tortoise it shows "Playerbots (server settings)".[^pop]
- The **Random bots** box on the Bots tab changes the bot count: [Bot population](/games-and-bots/bot-population.md).

## Servers whose settings live in the container (WotLK, Unbound, Conquest of Azeroth)

On these AzerothCore servers Yu'lon passes some settings as container environment lines. The environment wins over `playerbots.conf`, so editing one of these keys in the file does nothing while the environment line is there.[^pop] Use the Bots tab for the count instead.

| Game | What Yu'lon sets this way |
|---|---|
| WotLK | Lowest and highest random bots: 500 |
| Unbound | The same two, plus the Lua engine (mod-ale) switched on with its script path and no auto reload |
| Conquest of Azeroth | Lowest and highest random bots: 150; bots log in by themselves; bot level follows the players; bots are not packed into the player's zone |

Any other bot setting in `playerbots.conf` is yours to change.[^catalog]

## Battlegrounds on Conquest of Azeroth

Earlier versions of Yu'lon forced two battleground settings (bots joining battlegrounds) to 0 on Conquest of Azeroth. That was removed: bots join battlegrounds again, and `playerbots.conf`'s battleground settings take effect from the next Start. On an existing server, Yu'lon removes those old lines on Start, but only when their value is exactly 0. A line you set to something else is left alone.[^realm][^changelog]

## CMaNGOS servers (TBC, Vanilla, Tortoise)

Yu'lon writes these into `aiplayerbot.conf`:

- TBC: the lowest and highest random bots (500) and the number of bot accounts (100).
- Vanilla: the same, plus level sync with the players, at most 5 levels above, and level sync with no player.
- Tortoise: the bots module switched on, bots created, logged in at startup, and the count (500). Its `tortoise_bots.conf` also holds the dashboard switch.[^catalog]

## TrinityCore servers

- **Mists of Pandaria:** the bots module on, 500 random bots on 200 accounts, a maximum bot level of 90, the list of maps the bots may visit, and the bots' command server turned off.[^catalog]
- **Centurion:** a long list, mainly the player-versus-player and battleground-filling features, a random population of 150 between level 1 and 60, and the bots' fixed account numbers. Change these only if you know what they do.[^catalog]

## Newer bots modules and renamed settings

The bots module renamed its settings from `AiPlayerbot.` to `Playerbots.`. Yu'lon reads which names the server's own copy of the module uses and writes the matching ones, so you do not need to do anything.[^keys]

## If something goes wrong

If you change a setting and nothing happens, the container environment may be overriding it, or the server needs a restart. Reset to default on the Tuning tab puts back the installed values. See [Update and rebuild](/running-a-server/update-and-rebuild.md).

[^catalog]: The conf tables and world environment of each game in the game catalog.
[^pop]: The bot population controls: environment wins over playerbots.conf; the Tuning card.
[^realm]: The realm-type switch: the retired battleground lines are dropped only when their value is 0.
[^keys]: The playerbots settings step: the prefix rename and how it is read from the checkout.
[^changelog]: Release notes: battleground settings take effect from the next Start.
