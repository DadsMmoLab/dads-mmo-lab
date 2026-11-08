---
type: Concept
title: "Playerbots: what they are"
description: "What the bots on your server are, which games have them, and where to find the Bots tab tools."
tags: [playerbots, bots, bots-tab, overview, city-bots]
resource: pylauncher/yulon/botlist.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "Bot counts and bot markers per game"
  - id: botlist
    resource: pylauncher/yulon/botlist.py
    title: "Browsing the bots"
  - id: citybots
    resource: pylauncher/yulon/city_bots_guard.py
    title: "City Bots account check"
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: "The Bots tab"
  - id: addons
    resource: pylauncher/yulon/default_addons.py
    title: "Default client add-ons"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Playerbots: what they are

Playerbots are computer-controlled characters that log in to your server like players. They quest, travel, join groups and fight, so a private server feels populated even when only your family plays. Every World of Warcraft server in the Catalog has them.[^catalog]

## Random bots and party bots

- **Random bots** log in by themselves and roam the world. How many there are is a setting; see [Bot population](/games-and-bots/bot-population.md).
- **Party bots** are bots you ask to join your group. On WotLK and Unbound the launcher can build the party for you: see [My Party](/games-and-bots/my-party.md).[^view]

## The Bots tab

Each World of Warcraft server has a **Bots** tab. What is on it depends on the game:

| Tool | Where |
|---|---|
| **Random bots** box with **Apply...** | All eight servers |
| **Browse the bots** (a filter, **Find**, **Previous**, **Next**) | The games whose bots Yu'lon can recognise |
| **My Party** | WotLK and Unbound |
| **Rebuild random bots...** | Tortoise |
| **Delete every random bot...** | WotLK, Unbound and Conquest of Azeroth |
| **Bot dashboard** | Tortoise |

Browse shows each bot's name, level, and whether it is online. On WotLK-style servers it also says whether Yu'lon found the bot by the bots module's own list or by its account name, because the two can disagree if you changed the bot account prefix.[^botlist][^view]

## How many bots each game starts with

500 on WotLK, Unbound, TBC, Vanilla, Tortoise and Mists of Pandaria; 150 on Centurion and Conquest of Azeroth.[^catalog] Centurion's bots are pre-made characters on three fixed accounts, which is why its number is smaller.

## City Bots

On WotLK, the City Bots module keeps 400 bots on account numbers 12001 to 12400, named CITYBOT followed by the number. Before Yu'lon applies that module's database changes, it checks that none of those account numbers belongs to a real account. If one does, Yu'lon refuses and names the accounts, because a bot logging in would kick that player.[^citybots]

## Bot add-ons for your game client

Tortoise and Conquest of Azeroth put their own bot add-ons into your game client by themselves (TortoiseBots Manager, Tortoise GM Manager, and SquidBots Lite). They are never put back once you remove them. How to manage client add-ons is in [Playing](/playing/index.md).[^addons]

## More

- [Playerbots settings and playerbots.conf](/games-and-bots/playerbots-conf.md)
- [Games at a glance](/games-and-bots/games-overview.md)

[^catalog]: Bot counts and bot markers per game from the game catalog.
[^botlist]: The bot list: the two signals (registry and account prefix) only exist on WotLK-style servers.
[^view]: The Bots tab and its groups in the server view.
[^citybots]: The City bots safeguard: the range, the name prefix and the refusal.
[^addons]: The default add-ons step: Tortoise and Conquest of Azeroth default add-ons.
