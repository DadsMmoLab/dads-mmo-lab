---
type: How-to
title: "Play on a friend's server"
description: "Use Yu'lon as a launcher for a server you did not install: save the friend's realm address and play."
tags: [friend, launcher, realm-address, public-server, client]
resource: pylauncher/yulon/ui/friend_launcher.py
sources:
  - id: friend
    resource: pylauncher/yulon/ui/friend_launcher.py
    title: The friend-server launcher window
  - id: sidebar
    resource: pylauncher/yulon/ui/sidebar.py
    title: The top bar's Launcher button
  - id: client
    resource: pylauncher/yulon/play_client.py
    title: How the ready-to-play client is made
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# Play on a friend's server

Yu'lon can be only a launcher. If a friend hosts a World of Warcraft server (or you know a public one), you do not install anything: you save its address, make a ready-to-play client from your own client, and press Play.[^friend] To host a server for your friends instead, see [Play with friends](/running-a-server/play-with-friends.md).

## Open the launcher

Press the **Launcher** button, the play icon in the top bar. Its tooltip says "Launcher: play on a friend's server without installing one here". It works even when no server is installed.[^sidebar] The window is titled "Launcher — play on a friend's server".

## Steps

1. Under **Game:** choose the game your friend's server runs. The list shows each game with its client version, for example "WoW Vanilla (1.12.1)".
2. Under **Name:** type a name, for example "Mark's server".
3. Under **Realm address:** type the address as `host` or `host:port`, such as `192.168.1.20:3724`. With no port, 3724 is used. IPv6 addresses are not accepted.
4. Under **Your client folder:** press **Browse…** and choose your own client (you can also type the path). If you leave it empty, Yu'lon asks. See [Which client each game needs](/playing/client-versions.md).
5. Press **Make client**. Yu'lon checks your client, tells you how much is shared, and makes the copy. A full copy is made only after you agree to its size.
6. Select the server in the saved list and press **Play**.

## What you'll see

- The saved list shows each server as "name — address".
- While making the client, the line under the buttons says "Checking your client…", then "The ready-to-play client is at *folder*. Press Play to start the game."
- **Play** writes the address into the copy again and starts the game. The line says "The game is starting against *address*." The game keeps running if you close Yu'lon.[^friend]

## Remove a saved server

Select it and press **Remove**. It forgets the server and deletes the ready-to-play client Yu'lon made for it. Your own client folder is never touched; Yu'lon only deletes a folder marked as its own for that server.[^friend]

## Which games are offered

Only games whose ready-to-play client is complete without a server of their own: WoW WotLK, WoW TBC, WoW Vanilla, WoW Tortoise and WoW Conquest of Azeroth. Centurion and Unbound need their own server's files, so they are not offered; the window says "install the server to make their clients".[^friend] Mists of Pandaria is not offered either: its client needs settings that come with its own server.

## If something goes wrong

- **"Give this friend server a name first."** or **"Pick the game first."** Fill that in.
- **"That realm address is not one the game can reach."** Type it as `host` or `host:port`, for example `192.168.1.20`.
- **A name is refused next to another.** Names are saved as folder names, so "Mark's server" and "Mark_s server" would share one client. Choose a different name.
- **"The ready-to-play client ... is not this friend server's."** It is gone or was made for something else. Make it again, or Remove the entry.
- If Play cannot start the game, the message says what to install: see [The Play button](/playing/the-play-button.md).

[^friend]: The fields, messages, saved list and the Remove rules are in the friend launcher window code.
[^sidebar]: The Launcher button and its tooltip are in the top bar code.
