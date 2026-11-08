---
type: Guide
title: "DDON and DFO servers"
description: "Start a Dragon's Dogma Online or Dungeon Fighter Online server, choose your own client, and play."
tags: [ddon, dfo, single-server, play, client]
resource: pylauncher/yulon/ui/single_server_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/single_server_view.py
    title: The DDON and DFO server tab
  - id: play
    resource: pylauncher/yulon/single_play.py
    title: How Play starts these clients
  - id: launcher
    resource: pylauncher/yulon/single_launcher.py
    title: Add to DDON-Launcher
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: The Catalog's single-server entries
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# DDON and DFO servers

The Catalog's **Other games** row has two servers that are not World of Warcraft: **DDON** (Dragon's Dogma Online) and **DFO** (Dungeon Fighter Online). Their page is simpler than a WoW server's page. You bring your own client; Yu'lon never downloads it.[^view] Installing is in [Install a server](/getting-started/install-a-server.md); the games are summarized in [Games and servers at a glance](/games-and-bots/games-overview.md).

| Game | Client you bring | Program |
|---|---|---|
| DDON | version 03.04.003, the folder with DDO.exe in it | DDO.exe |
| DFO | US 2.38.3.25, the folder with DFO.exe, Script.pvf and sk.dat in it | DFO.exe |

## The page

At the top: the server's name, a status badge, **Open folder**, and the server folder and ports. Then **Start**, **Stop**, **Where to get help…** and **Remove from Yu'lon…** (which takes the server off the list; nothing on disk or in Docker is deleted).[^view]

## Steps to play

1. Press **Start** and wait for the server to be up.
2. Press **Choose client folder…** and pick the folder named above. The line shows "Client: not chosen yet" until you do.
3. Type an **Account:** name. It must be 3 to 20 letters, digits or underscores, with no spaces. The account is made the first time you log in with a new name.[^play]
4. Press **Play**. Yu'lon starts your client already pointed at this server through this computer, and nothing is written into your client folder.[^play]

Play refuses, and says why, if there is no client folder, the program is missing from it, or the name is not allowed.

On a DFO server, Start runs the server for that one account, and the game logs in as it. If the server is running for another name, Play says "The server is running for *name*. Play as *name*, or Stop the server and Start it again to play as *new name*."[^view]

## Who can connect

The **Who can connect** choice sets the address clients are told to use. Pick **Another address…** to type one, then press **Apply**. For another computer, such as a Steam Deck, choose a network here and point that computer's launcher at the address shown. A DFO server takes players on this computer only: the tab says so and there is no network to choose.[^view] For hosting beyond this computer see [Play with friends](/running-a-server/play-with-friends.md).

## Other launchers

- **Add to DDON-Launcher** (DDON only): lists the server in your DDON-Launcher's settings file as one section, keeping every other line. Open the launcher, choose the section, then Register the first time and log in. It refuses if the launcher has not made its file yet.[^launcher]
- **Add to Steam Deck (Gaming Mode)…**: adds a Server, a game and a Server Stop shortcut to Steam. Quit Steam first. The account you typed beside Play is the one used. See [Controller and Steam Deck](/getting-started/controller-and-steam-deck.md).[^view]

## How the game starts

On Windows the client runs directly; if it asks for administrator rights, Windows shows its own question and you may say no. On Linux and Steam Deck it runs through Proton or Wine, the same as in [The Play button](/playing/the-play-button.md). On Linux a DFO client is started by the Gaming Mode client script so it can join the server's private network.[^play]

## If something goes wrong

- A message "*name* was not started" tells what is missing. Press Play again after fixing it.
- "Nothing on this machine can run ..." means Proton or Wine is missing: install one, then Play again.

[^view]: The tab's buttons, texts and the account check are in the single-server view.
[^play]: How the client is started and what the account name may be are in the single-server play module.
[^launcher]: What Add to DDON-Launcher writes and refuses is in the single-server launcher module.
