---
type: Guide
title: "WoW Conquest of Azeroth"
description: "The Conquest of Azeroth server: the Ascension client you bring, turning it into the CoA client, and what is special."
tags: [coa, conquest-of-azeroth, ascension, azerothcore, catalog]
resource: pylauncher/yulon/coa_client.py
sources:
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The wow-coa entry"
  - id: coaclient
    resource: pylauncher/yulon/coa_client.py
    title: "CoA client check and update"
  - id: changelog
    resource: CHANGELOG.md
    title: "Release notes"
  - id: update
    resource: pylauncher/yulon/ui/client_update.py
    title: "Update client button"
  - id: addons
    resource: pylauncher/yulon/default_addons.py
    title: "Default client add-ons"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# WoW Conquest of Azeroth

Conquest of Azeroth (3.3.5a) on the Ascension CoA fork, with bots that quest, group and fight alongside you.[^catalog] You can run it as one of four realm types: see [CoA realm types](/games-and-bots/coa-realm-types.md).

## The client you bring

Bring your own Ascension 3.3.5a client (`Ascension.exe` and its Data folder). Then use **Update client**, which sits beside Play, to turn your Ascension copy into the CoA client.[^catalog][^changelog]

This matters. The stock Ascension download logs in to a CoA server and is then disconnected when you pick the realm. The Server tab tells the two apart and warns you when your client is the stock one; **Update client** fixes it.[^coaclient] Files you have changed are kept unless you say otherwise (the button then reads **Update client and replace the changed files**).[^changelog]

Under Play there is also **Enable HD patches**, which turns the client's HD patches off or on as the official launcher does.[^changelog]

The realmlist line for the client is `127.0.0.1:3726`, or the host's LAN or public address with `:3726` for other players. CoA's login port is not WotLK's.[^catalog] More in [Playing](/playing/index.md).

## What is special

- **150 bots** at the start, with the bots' level kept in step with the players.[^catalog]
- **SquidBots Lite**, a bot add-on, is put into your game client by itself and updated with the server. If you remove it, it stays removed.[^addons]
- **Update the server to latest...** moves a CoA server to the newest CoA Bots release.[^changelog]
- **When stopping:** you can wait for the bots' saves, or stop once players are saved.[^changelog]
- **Map data.** The install extracts a few GB of map data from your client the first time. The server folder needs about 25 GB free (a warning below 40 GB); see [Pre-install checks](/getting-started/pre-install-checks.md).[^catalog]
- **mod-ale** can be added from the Modules tab; its Playerbots Lua functions are left out on this server.[^changelog]
- Linux and Windows.[^catalog]

## Limits

- Beta in the Catalog. My Party is not available here.
- The server needs at least 8 GB of memory (a warning below 12 GB).[^catalog]
- Update client checks every file of your client folder against the client the CoA project publishes and downloads only what differs. Each replaced file is kept in a backup, and **Undo the last client update...** puts them back.[^update]

## If something goes wrong

If you are disconnected right after choosing the realm, your client is probably the stock Ascension one: press **Update client**. If a realm type switch fails, see [CoA realm types](/games-and-bots/coa-realm-types.md). Other trouble: [Common problems](/getting-started/common-problems.md).

[^catalog]: Description, client notes, ports, bot keys and requirements from the game catalog.
[^coaclient]: The install checks tell the CoA client from the stock Ascension one by Extensions.dll and Ascension.exe against the CoA manifest.
[^changelog]: Release notes for Update client, HD patches, update to latest and the stop option.
[^update]: The tooltip and Undo button text in the client update code.
[^addons]: SquidBots Lite is a default add-on, installed at a fresh install and put back at Play unless removed.
