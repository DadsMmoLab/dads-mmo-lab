---
type: How-to
title: "Point Yu'lon at your own game client"
description: "Choose, change or forget the game client folder for a server, and what Yu'lon checks before it accepts it."
tags: [client, folder, install, requirements, how-to]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Server tab's Client section and the folder checks
  - id: clientdir
    resource: pylauncher/yulon/catalog/families/clientdir.py
    title: The rules a client folder must pass
  - id: build
    resource: pylauncher/yulon/client_build.py
    title: How the game program's version is read
  - id: names
    resource: pylauncher/yulon/client_names.py
    title: Looking up client files in any letter case
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:36:29Z
status: draft
---

# Point Yu'lon at your own game client

Yu'lon never downloads or hands out a game client you don't already have. The only game files it fetches are ones you ask for: the Conquest of Azeroth files that **Update client** replaces in your own folder, and, for Centurion only, a stock `Wow.exe` when yours is not the unmodified one (one file, checked by hash first). You tell Yu'lon which folder your client is in. Play, add-ons and the ready-to-play client are all made from that folder.[^view] Which version each game needs is in [Which client each game needs](/playing/client-versions.md).

## Steps

1. Open the server's page and go to the **Server** tab.
2. Find the **Client** box. Its first line says "Client folder: none — addons and Play need one" until you set one.
3. Press **Set client folder…** (once a folder is set, the button reads **Change client folder…**).
4. In the window "Select your *version* client folder", choose the folder the game is installed in, the one that holds the `Data` folder.
5. If Yu'lon has a doubt, it asks "Use this client folder anyway?". Read the sentence, then answer Yes or No.

When a server is installed, Yu'lon may already have asked for the folder. WoW WotLK and WoW Unbound do not ask at install time, but you can still set the folder here, and Play needs it.[^view]

## What you'll see

The Client box shows "Client folder:" followed by the path. A **Forget client folder** button appears beside it; it clears the folder again. It asks nothing, and you can set the folder again with one press.[^view] The **Play** box says "Play needs your own client folder first" until a folder is set.

## What Yu'lon checks

Yu'lon refuses a folder and changes nothing when:

- the folder is gone, or is a file;
- it has no `Data` folder, so it is not a game client (pick the folder the game itself is installed in, not a launcher, an installer or a backup);[^clientdir]
- it is the server's own folder or inside it, because removing the server would delete it;
- the game program reports a different version than the server needs. The message names the version it found and the one needed;[^build]
- a file this server needs from the client is missing, or the folder is the wrong language for the server's patches;
- it holds no game archives at all.

It asks "Use this client folder anyway?" when the folder looks incomplete: fewer archives than expected, no locale folder, signs of a repacked client, or little free space.[^clientdir] A game program whose version cannot be read is accepted: only a version that is read and is wrong is refused.[^build]

On Linux and Steam Deck, file names are matched without regard to upper or lower case, so a client copied from Windows with lower-case names is still found, and Yu'lon never renames your files.[^names]

## If something goes wrong

- **"That game client is ..., and this server needs a ... client."** You picked another version. Choose a folder with the right one.
- **Buttons are greyed or the press is refused.** Yu'lon will not change the folder while a backup, restore, install or other job is running. Wait for it to finish.[^view]
- Still stuck? See [Common problems](/getting-started/common-problems.md).

[^view]: Set, change and forget client folder, and the Client section text, are in the Server tab code.
[^clientdir]: The refusals and warnings for a client folder are in the client folder rules.
[^build]: The game program's version is read from the program file itself; an unreadable one is never refused.
[^names]: Client files are looked up case-insensitively and never renamed.
