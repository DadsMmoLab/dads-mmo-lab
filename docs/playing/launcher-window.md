---
type: Guide
title: "The Launcher… window for one server"
description: "Choose the realm address, the account name, the display and extras for a server's game, and press PLAY."
tags: [launcher, realm-address, account, display, extras]
resource: pylauncher/yulon/ui/launcher_window.py
sources:
  - id: window
    resource: pylauncher/yulon/ui/launcher_window.py
    title: The per-server launcher window
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Launcher… button and the Play pipeline
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# The Launcher… window for one server

Besides **Play**, the Server tab has a **Launcher…** button. It opens a window called "Play — *game name*" with one big **PLAY**, and the choices that go with it: where the game logs in, which account name is filled in, how the window opens, and the server's extras. Opening it starts nothing.[^window]

The button shows once the server has a ready-to-play client. Before that the window offers **Make a ready-to-play client…**: see [The ready-to-play client](/playing/ready-to-play-client.md).

Do not confuse it with the top bar's **Launcher** button, which is for a friend's server: see [Play on a friend's server](/playing/friend-launcher.md).

## What is in the window

- **Banner.** Says whether the realm is up, for example "Play starts the server first (about 1 minute)" or "The server is starting…". PLAY does the starting for you.[^window]
- **Client.** Where the ready-to-play client is, what it was made from, its status, and the buttons **Refresh from my client**, **Open folder** and **Delete…**.
- **Realm address.** The address the game logs in at: `127.0.0.1` on this computer, the server's LAN address at home, or its internet name. Type one or pick from the list. **Use this computer** puts `127.0.0.1` back. It is written to this client only.[^window]
- **Log in as.** Fills in your account name on the game's login screen. The password is never stored or written: you type it in the game.[^window]
- **Display.** **Window** and **Resolution**, set in the game's `Config.wtf` at your next Play.
- **Extras for this server.** Optional packs for the server, each with a checkbox, and an **Update** button where a newer version exists. If the server offers none, it says "This server offers no extras."
- **Addons in this client.** A list of the add-ons found, with **Open AddOns folder**. See [Default add-ons](/playing/default-addons.md) and [Add your own game add-ons](/playing/your-own-addons.md).
- **Server tab…** shows the server's tab in the main window; **Close** closes the window.

## Steps to log in at a different address

1. Open **Launcher…**.
2. Under **Realm address** type the address, for example this server's LAN address, and press Return.
3. Pick the account under **Log in as** if you want it filled in.
4. Press **PLAY**.

## What you'll see

Nothing is downloaded, patched or written into the client when you change a setting here: your choices are saved and applied at the next Play.[^window] The banner shows "Logs in at *address*, another computer" when the address is not this computer's.

## If something goes wrong

- PLAY and the other buttons follow the same rules as the Server tab: while a restore, backup or install runs they wait or say why.
- The window greys out if its server's tab goes away, for example after the server is removed.

See also [The Play button](/playing/the-play-button.md).

[^window]: The window's sections, labels and behaviour are in the launcher window code.
