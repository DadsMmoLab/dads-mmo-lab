---
type: Guide
title: "The Play button"
description: "What Play does step by step, how it starts the game on each system, and what to do when it refuses."
tags: [play, launch, realmlist, proton, wine]
resource: pylauncher/yulon/play_launch.py
sources:
  - id: launch
    resource: pylauncher/yulon/play_launch.py
    title: How the game is started on each system
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Play pipeline on the Server tab
  - id: config
    resource: pylauncher/yulon/client_config.py
    title: How Config.wtf is kept
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# The Play button

**Play** on the **Server** tab starts the game for this server: it starts the server if needed, points the ready-to-play client at it and launches the game. You do not edit any game file yourself.[^view]

## Steps

1. Set your client folder and make the ready-to-play client if you have not: [Point Yu'lon at your own game client](/playing/set-your-client-folder.md), [The ready-to-play client](/playing/ready-to-play-client.md). Until then the button says **Make a ready-to-play client…**.
2. Press **Play**.
3. If the server is stopped, Yu'lon asks "Start the server?". Answer **Start and play**.
4. If your own client was patched since the copy was made, Yu'lon asks. Choose **Refresh and play** or **Play without refreshing**.
5. If the server has client extras to get ready (patches or optional packs), Yu'lon does that first, with **Cancel** beside Play to stop it.
6. The game window opens.

## What Yu'lon writes for you

Before it starts the game, Play writes the realm address into the ready-to-play client, in its realmlist or, for games that set it in the game's settings file, in `WTF/Config.wtf`. By default the address is `127.0.0.1`, this computer. You can change it in the launcher window ([The Launcher… window](/playing/launcher-window.md)).[^view]

For games that need settings in the game's own file, Play sets the ones the server needs each time, and sets a first-run preference (such as windowed mode) only where you have no line for it, so your choices in the game's options are kept.[^config] Your own client's files are not touched.

If the address is another computer's, Play does not start or check the server on this computer: it says "Starting World of Warcraft at ... The server on this computer is not started or checked for it."[^view]

## How the game is started

- **Windows:** the game program runs directly.
- **Linux and Steam Deck:** it runs through Proton (the one Steam would use for Add to Steam), with no Steam window; else through Wine if installed. Each server has its own Proton folder under Yu'lon's data folder, so servers do not share settings.[^launch]
- **macOS:** it runs through Wine if installed.[^launch]

The game keeps running if you close Yu'lon. After it starts you see "World of Warcraft was started at *time*. Closing Yu'lon does not close it."[^view]

## If something goes wrong

- **Nothing can run the program.** On Linux install Proton in Steam (Library, filter by Tools, Proton, Install) or install Wine; on macOS install Wine. Then press Play again.[^launch]
- **The wrong client version.** Play refuses with the version found and the one needed. Pick the right client folder.
- **The ready-to-play folder is gone or not this server's.** Yu'lon offers to make it again.
- **The server does not come up.** Play waits for the realm, up to ten minutes, and then says to check the **Server** tab and press Play again.[^view]
- **"Play stopped ... Nothing was started."** Press Play again; if it repeats, use **Refresh from your original client** in the **▾** menu first.

Related: [Add to Steam](/getting-started/controller-and-steam-deck.md), [Play with friends](/running-a-server/play-with-friends.md) for hosting, and [Games and bots](/games-and-bots/index.md).

[^view]: The steps, questions, messages and the realm address handling are in the Play code of the Server tab.
[^launch]: The per-system start, the Proton folder per server and the missing-runner messages are in the launcher module.
[^config]: Settings the server needs are set each Play; first-run ones only when you have no line.
