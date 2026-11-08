---
type: How-to
title: "Client updates, patches and extras"
description: "Keep a Conquest of Azeroth client current with Update client, and choose optional client packs for other servers."
tags: [client, update, patches, packs, refresh]
resource: pylauncher/yulon/ui/client_update.py
sources:
  - id: update
    resource: pylauncher/yulon/ui/client_update.py
    title: The Update client press and its questions
  - id: coa
    resource: pylauncher/yulon/coa_client.py
    title: How the CoA client is compared and updated
  - id: packs
    resource: pylauncher/yulon/client_packs.py
    title: How client packs are fetched and checked
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Client options and the Play pipeline
  - id: catalog
    resource: pylauncher/yulon/catalog/catalog.json
    title: Which games have packs and updates
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# Client updates, patches and extras

Yu'lon never downloads the game itself. It does fetch small extras for the client: patches a server needs, optional packs, and, for Conquest of Azeroth, updates to your own client.[^packs]

## Update client (Conquest of Azeroth)

On a WoW Conquest of Azeroth server, **Update client** sits beside **Play**. It compares every file of your client folder with the client the Conquest of Azeroth project publishes, and downloads only what differs.[^coa]

1. Press **Update client**. Yu'lon checks first: it shows "Your client is up to date" or "A client update is available".
2. Read the question. It says how many files and how much will download, and that your client folder itself changes.
3. Choose **Update**. If you changed some files, choose **Update, keep my changed files** or **Update, replace my changed files**.
4. Wait. **Cancel** stops between reads; what arrived is kept and the next press continues.

Every file it replaces is kept in a backup folder in your client, and your settings (`WTF`), Yu'lon's own add-on folders and the logs are never written. Your own graphics wrapper files and `.ini`, `.conf` and `.cfg` files are only created if missing, never replaced.[^coa]

To go back, use the **▾** menu beside Play: **Undo the last client update…**.[^update]

**The stock Ascension download.** The Server tab warns "This client is the stock Ascension download…". Press **Update client and replace the changed files** to turn it into the CoA client. Without that you are disconnected when you pick the realm.[^coa] Play is never held up by an update.

## Client options (packs)

Where a server has optional extras, the **▾** menu beside Play has **Client options…**. That is WoW WotLK, WoW Unbound and Centurion. The window lists the optional packs as checkboxes: for example the Lumenveil graphics mod (Windows only), ConsolePort controller support, and Centurion's HD packs. Centurion also has a Wow.exe option, borderless full screen. The window says how much the required download is. Required packs are not a choice: they are fetched at Play. Tick what you want and press **Save**. The change is applied at your next Play: packs are downloaded and installed, or removed, then.[^catalog][^view] Optional packs that are off by default stay off until you tick them.

Downloads go over https to the host the server names, and a file is checked before it is used. A pack whose address no longer answers is shown as unavailable.[^packs] A pack with a newer version shows an **Update** button.

## After your own client is patched

When a patcher changes files in your own client, use **Refresh from your original client** (see [The ready-to-play client](/playing/ready-to-play-client.md)).

HD options are covered in [HD patches](/playing/hd-patches.md).

## If something goes wrong

- **"Choose your client folder first; then Update client can check it."** Set the folder: [Point Yu'lon at your own game client](/playing/set-your-client-folder.md).
- **"Could not check for a client update."** The check could not reach the project. Try again later; Play still works.
- A pack that cannot be fetched stops Play with a question. For an optional pack choose **Retry** or **Play without** that pack this time.[^view]

[^packs]: Packs are fetched over https from the hosts the server's Catalog entry names and verified.
[^coa]: What Update client compares, keeps and never writes is described in the CoA client module.
[^update]: The button labels and questions are in the Update client control.
[^catalog]: Which games list which packs comes from the Catalog.
[^view]: The Client options window and the Play pipeline are in the Server tab code.
