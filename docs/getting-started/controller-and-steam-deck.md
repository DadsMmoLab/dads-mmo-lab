---
type: Guide
title: "Playing on a controller or Steam Deck"
description: "Move around Yu'lon with a D-pad, A, B and the bumpers, and set up the game itself for a controller."
tags: [controller, steam-deck, gamepad, consoleport, steam]
resource: pylauncher/yulon/ui/gamepad.py
sources:
  - id: gamepad
    resource: pylauncher/yulon/ui/gamepad.py
    title: "Controller and keyboard navigation"
  - id: steamin
    resource: pylauncher/yulon/steam_input.py
    title: "The Steam Input layout for ConsolePort"
  - id: steam
    resource: pylauncher/yulon/steam.py
    title: "Add to Steam shortcuts and refusals"
  - id: catalogjson
    resource: pylauncher/yulon/catalog/catalog.json
    title: "ConsolePort pack and which games offer it"
  - id: changelog
    resource: CHANGELOG.md
    title: "How controller features are worded and their status"
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:25:00Z
status: draft
---

# Playing on a controller or Steam Deck

You can drive all of Yu'lon without a mouse. The Steam Deck's own buttons, an Xbox pad, or a Bluetooth controller all work the same way.[^gamepad]

## Moving around Yu'lon

| Button | What it does |
|---|---|
| D-pad (or left stick) | Moves between buttons, tabs and fields. Hold it to scroll a long list fast. |
| **A** | Presses the selected button. On a dropdown, opens it. In a text box, asks for the on-screen keyboard. |
| **B** | Goes back: closes an open dropdown or menu, cancels a prompt, closes a dialog, or presses **Back** in the Catalog. |
| **LB / RB** | Previous and next tab. |

Other buttons (X, Y, Start, Back, the triggers and the stick presses) do nothing in Yu'lon.[^gamepad]

## Dropdowns and menus

With a dropdown open, Up and Down move the highlight, **A** picks the row, and **B** closes it without choosing. A bumper also closes it without switching tab.[^gamepad]

## On a keyboard

The same moves work with the arrow keys, Enter or Space (confirm), Escape or Backspace (back), and L and R (tabs). In a text box those keys type instead.[^gamepad]

## Steam Deck: Docker and Gaming Mode

On a Steam Deck, Yu'lon sets up Docker as on any Linux. See [What Yu'lon installs](/getting-started/what-yulon-installs.md). On Linux and the Deck, a server's tab has an **Add to Steam...** button. It puts the server launcher and the game client into your Steam library so you can start them from Gaming Mode.[^steam]

Before you press it:

- **Quit Steam completely** (check the tray). Steam rewrites its shortcut list when it exits and would throw your entries away.
- Sign in to Steam once so it has a profile. If two accounts have profiles, Yu'lon will not guess which one.
- Have a Proton installed in Steam; the game client is a Windows program.
- If you run Yu'lon as an AppImage, start it by opening the `.AppImage` file itself.[^steam]

## Controller support in the game (ConsolePort)

For WoW WotLK, WoW Unbound and Centurion, the client's add-on packs offer **Controller support (ConsolePort)**. It installs the ConsolePortLK Enhanced add-on. On a Steam Deck, Steam Input sends its keys; in the game, type `/cp recalibrate` once.[^catalogjson] When you choose it, **Add to Steam...** also writes a ConsolePort controller layout for the game, unless that game already has one.[^steamin]

Conquest of Azeroth installs the add-on from its Modules tab instead.[^changelog]

This is new and has not been tested on a Steam Deck yet. The catalog asks you to report problems.[^catalogjson]

## If something goes wrong

- **Add to Steam... says Steam is running:** quit Steam completely, then press it again.
- **Docker is gone after a SteamOS update:** see [Common problems](/getting-started/common-problems.md).
- **Nothing reacts to the controller:** Yu'lon reads the pad through SDL. If that part is not available on your machine, the pad does nothing, but the keyboard keys above still work.[^gamepad]

[^gamepad]: gamepad.py: A, B, LB and RB are the only mapped buttons; the D-pad and left stick move focus; the keyboard fallback is built in.
[^steam]: steam.py: the refusal messages (Steam running, no profile, two profiles, no Proton, AppImage) and the Linux-only button.
[^steamin]: steam_input.py writes the ConsolePort layout only when the game chose the ConsolePort pack or its mod is installed.
[^catalogjson]: The consoleport pack's label and description in catalog.json; only WotLK, Centurion and Unbound entries carry it.
[^changelog]: CHANGELOG.md, v0.9.31-Public.
