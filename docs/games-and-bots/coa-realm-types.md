---
type: How-to
title: "Conquest of Azeroth realm types and Switch realm type"
description: "Pick one of the four Conquest of Azeroth rule sets and switch between them, each with its own characters."
tags: [coa, realm-type, switch, characters, server-tab]
resource: pylauncher/yulon/coa_switch.py
sources:
  - id: realm
    resource: pylauncher/yulon/coa_realm.py
    title: "Realm types: the engine that parks and swaps databases"
  - id: switch
    resource: pylauncher/yulon/coa_switch.py
    title: "The switch press under the per-server hold"
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: "The realm type list, the button and the Start question"
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: "The four realm types of wow-coa"
  - id: changelog
    resource: CHANGELOG.md
    title: "Release notes"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Conquest of Azeroth realm types and Switch realm type

A Conquest of Azeroth server can run under different rule sets, called realm types. Each realm type keeps its own characters, and **Switch realm type...** on the Server tab moves your server from one to another.[^view] The game itself is described in [WoW Conquest of Azeroth](/games-and-bots/wow-conquest-of-azeroth.md).

## The four realm types

| Choice in the list | Realm name | What it is |
|---|---|---|
| CoA classes | Conquest of Azeroth | The Conquest of Azeroth classes, as the Ascension client teaches them. This is the default. |
| Free Pick | CoA Free Pick | Free-pick advancement: any spell on any character. |
| Warcraft Reborn | CoA Warcraft Reborn | Warcraft Reborn rules on the Conquest of Azeroth world. |
| Wildcard | CoA Wildcard | Wildcard seasonal rules: free pick with the Wildcard draft. |

The list shows each choice as its label and realm name, and hovering over it shows the description.[^catalog][^view] The realm list in your game shows the realm type's name, from the next Start after a switch.[^changelog]

## Switch to another realm type

1. Open your Conquest of Azeroth server and go to the **Server** tab.
2. Find the line "Realm type - pick one, then press Switch realm type... (each keeps its own characters)."
3. Pick a type in the list. **Switch realm type...** lights up as soon as the picked type is not the current one.[^changelog]
4. Press **Switch realm type...** and read the question. It says the switch stops the server and keeps each type's characters apart.
5. Confirm. Yu'lon stops the server, switches, and starts it again.[^view]

### What you'll see

- The first time you switch to a type, Yu'lon sets up that type's databases. The confirm box says this takes a few minutes. It then copies your player accounts over, so you can log in the same way, and leaves the bot accounts behind.[^realm]
- Switching back later is quick: the type's characters are just where you left them.
- When it ends, the Server tab says what moved.

### Start with another type picked

If you press Start while the list is on a type the server is not running, a question appears: "Start as ...?" It offers **Switch and start**, **Start** with the current type's name, or Cancel. Switching first is no longer skipped by accident.[^view][^changelog]

## Good to know

- Each type has its own characters and its own bots. A backup records which realm type it holds.[^view]
- Switching away from Wildcard no longer carries Wildcard's rules into the next type.[^changelog]
- A switch cannot start while another job, such as an install, repair or second switch, holds the server.[^switch]

## If something goes wrong

- If the switch fails before anything is written, the server starts again on its current type. If it fails after that, the server stays stopped; press **Switch realm type...** again and it finishes the switch or puts things back.[^switch]
- A switch that was cut off leaves a note, and Start will refuse until the switch is finished. Press **Switch realm type...** again.[^realm]
- If the server's database password cannot be read, the new type's settings are not written. Press **Switch realm type...** again once it can be read.[^view]
- A switch refuses while a server container is still running. Stop the server first, then try again.[^realm]

See also [Backups](/running-a-server/backups.md) and [Start, stop and restart](/running-a-server/start-stop-and-restart.md).

[^view]: The Server tab's realm type list, the Switch realm type button and the Start question.
[^catalog]: The realm_types section of wow-coa in the game catalog.
[^realm]: The realm-type switch: parking, first-switch import, account copy and crash marker.
[^switch]: The realm-type switch: the confirm text and the hold taken around a switch.
[^changelog]: Release notes: realm list names, the button, the Start question, Wildcard fix.
