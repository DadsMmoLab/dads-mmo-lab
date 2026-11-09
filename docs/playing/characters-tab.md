---
type: Guide
title: "The Characters tab"
description: "See every character on your server and fix one with Teleport, Set level, Revive, Send gold and more."
tags: [characters, gm, teleport, level, gold]
resource: pylauncher/yulon/play.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Characters tab
  - id: reads
    resource: pylauncher/yulon/play.py
    title: What the list reads and what each action sends
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: Per-game rules for offline revive, rename and set level
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# The Characters tab

The **Characters** tab lists the characters on your server and lets you act on the one you pick: teleport it, set its level, revive it, rename it, or mail it gold or its worn gear. It is for you, the server owner.[^view]

## Steps

1. Make sure the server is running, then open the **Characters** tab.
2. Press **Refresh the list**. The box **Characters on this server** shows one line per character: name, level, online or offline, the account it belongs to, and its GUID.[^view]
3. Type in **Filter by name…** to narrow the list. The filter only hides rows; it matches the name, in any letter case.
4. Click a character. Every button in **What to do** now names it, for example "Revive *name*".
5. Press the action you want.

You can also right-click a character for a menu: copy the name, and the same actions.[^view]

## The actions

- **Teleport**: type a place the server knows, such as Stormwind, in **Teleport to**.
- **Set level**: pick the number in **Level** (it starts at the character's own level). Lowering a level asks first, because it resets the character's experience and raising it again does not give that back.
- **Rename at next login**: marks the character so the game asks for a new name at login.
- **Revive**: brings the character back to life.
- **Send gold**: mails the amount in **Gold** with the subject "A gift".
- **Send everything worn**: mails the character the items it is wearing, in as many mails as needed.[^view]

After an action, the line under the tab says what happened and the list refreshes itself.

## What differs by game

Not every game offers every action.

- **WoW Tortoise** has no Set level; the tab says what it can do instead.
- On **WoW Tortoise**, a character has to be logged in to be renamed.
- Revive of a character who is not logged in works on **WoW Vanilla** and **WoW Tortoise**. On the other games the button says the character "has to be logged in to be revived".[^catalog]
- A game whose actions have not been measured shows a sentence instead of the buttons.[^view]

Bots are listed with the other characters here; the Playerbots pages are in [Games and bots](/games-and-bots/index.md). For accounts see [Game accounts](/running-a-server/game-accounts.md). A list of all the tabs is in [The server control panel and its tabs](/running-a-server/control-panel-and-tabs.md).

## If something goes wrong

- **"There are no characters on this server yet."** Make one in the game, logged in with an account from the **Accounts** tab, then press **Refresh the list**.
- **"Could not read this server's characters: ..."** The database is not up. Start the server and refresh.
- Buttons are grey with a reason: usually "Choose a character in the list first."
- If two characters share a name, Yu'lon refuses to guess which you mean and says so.[^reads]

[^view]: The tab, its labels, the context menu and the messages are in the Server page code.
[^catalog]: Offline revive, offline rename and the missing Set level are per-game facts in the Catalog.
[^reads]: The character list read and the duplicate-name refusal are in the character reads module.
