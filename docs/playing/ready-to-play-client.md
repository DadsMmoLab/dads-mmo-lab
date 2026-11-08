---
type: Concept
title: "The ready-to-play client"
description: "What the ready-to-play client is, how to make it, refresh it from your own client, and delete it."
tags: [client, ready-to-play, hard-links, refresh, disk-space]
resource: pylauncher/yulon/play_client.py
sources:
  - id: client
    resource: pylauncher/yulon/play_client.py
    title: How the per-server client folder is built and kept in step
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Make, Refresh and Delete on the Server tab
  - id: launcher
    resource: pylauncher/yulon/ui/launcher_window.py
    title: The Client box of the launcher window
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# The ready-to-play client

Yu'lon does not point your own client at a server. It makes a copy of your client for each server, set up for that server, and Play starts the copy. Your own client is never changed for this server.[^client]

## Why a copy

Pointing a game at a server means changing its realm address, and sometimes its program and patches. A copy per server keeps your original exactly as it was, so it still reaches whatever it reached before.[^client]

The big game archives (`.mpq` and `.dll` files) are shared with your own client instead of copied, so the copy takes very little extra room. Files the game writes to, such as your settings in `WTF` and your add-ons in `Interface`, are real copies.[^client]

## Make it

1. Set your client folder first: [Point Yu'lon at your own game client](/playing/set-your-client-folder.md).
2. On the **Server** tab press **Make a ready-to-play client…**. This button is the Play button until a copy exists.
3. The window explains the copy and shows where it will go, next to your own client. Press **Change…** to put it elsewhere.
4. Read the size line. On the same drive it says how many gigabytes are shared and take no extra space.
5. Press **Make it**.

If you pick another drive, game files cannot be shared, so it would be a full copy. Press OK only after ticking "Make a full copy" with its size shown. If your drive cannot share files at all, the same box is offered as "If this drive cannot share files, make a full copy instead".[^view]

On Windows, if your client is inside OneDrive, Yu'lon picks a folder outside it by itself.[^client]

## Keep it in step

When a patch changes files in your own client, press the **▾** menu beside Play and choose **Refresh from your original client**. It brings the game files and the program back in step. Your settings (`WTF`) and add-ons (`Interface`) are never touched.[^view] Play also notices when your client was patched and offers "Refresh and play".

The launcher window's Client box has the same button as **Refresh from my client**, plus **Open folder** and **Delete…**.[^launcher]

## Delete it

In the **▾** menu choose **Delete ready-to-play client…**. Yu'lon asks first. Your own client keeps all its files, and the game files the two shared stay too. Yu'lon only deletes a folder it made for this server.[^view] You can make a new one at any time.

## If something goes wrong

- **"There is no Wow.exe in the ready-to-play client."** Use **Refresh from your original client** to copy it back, then press Play again.[^view]
- A make that fails or is cancelled leaves nothing half-made: Yu'lon builds in a temporary folder and renames it at the end.[^client]
- Other jobs hold the buttons. Wait for them to end.

Next: [The Play button](/playing/the-play-button.md).

[^client]: The design, the shared file types and the clean-up on failure are described in the client builder.
[^view]: Labels and texts of the Make dialog, the menu and the confirmations are in the Server tab code.
[^launcher]: The launcher window's Client box labels.
