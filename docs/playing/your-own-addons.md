---
type: How-to
title: "Add your own game add-ons"
description: "Put a game add-on into your client from a link, a folder or a zip, and update or remove it later."
tags: [addons, interface, zip, github, bring-your-own]
resource: pylauncher/yulon/client_addons.py
sources:
  - id: addons
    resource: pylauncher/yulon/client_addons.py
    title: How an outside add-on is installed
  - id: layout
    resource: pylauncher/yulon/addon_layout.py
    title: How add-ons are found and which game versions they fit
  - id: archive
    resource: pylauncher/yulon/addon_archive.py
    title: How zips and links are checked
  - id: box
    resource: pylauncher/yulon/ui/widgets/client_addons_box.py
    title: The add-on box
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The add-on dialogs and messages
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T20:30:00Z
status: draft
---

# Add your own game add-ons

You can put any game add-on into the client you play with. It works on every game Yu'lon runs, from the **Modules** tab. The short steps are also in [Bring your own modules and add-ons](/running-a-server/bring-your-own-modules.md); this page has the rules and the problems.[^box]

The Server tab reminds you of it: under the client folder it says "Game add-ons from a link, a folder or a zip: Modules tab, “Game add-ons you bring”."[^view]

## Steps

1. Open the **Modules** tab and find the box **Game add-ons you bring**.
2. Press one of:
   - **Add-on from link…** and paste a GitHub, GitLab or Codeberg repository, or a link to a `.zip` there;
   - **Add-on from folder…** and choose a folder holding the add-on (or a folder of add-ons);
   - **Add-on from zip…** and choose a `.zip` on your computer.
3. If you already have an add-on of that name, Yu'lon asks "Replace *name*?". Answer Yes only if you want yours replaced.
4. The add-on appears in the list **Added by Yu'lon:**. Select it and use **Update** or **Remove**.

On a narrow window the buttons read **Add from link…**, **Add from folder…** and **Add from zip…**.[^box]

## Where it goes

It goes into the game client you play with: the ready-to-play client when there is one. Play puts back the add-ons you brought into a ready-to-play client that was made again.[^addons]

## How Yu'lon decides it is an add-on

The game loads `Interface/AddOns/<Name>/<Name>.toc`, so Yu'lon names the add-on after its `.toc` file. A download called `pfUI-master` containing `pfUI.toc` becomes `pfUI`. Several add-ons in one source are all added.[^layout]

It also checks the add-on is made for your client version. A very new add-on, from a later game version, is refused. A slightly older one is installed with an "out of date" note.[^layout]

## What Yu'lon refuses

- A zip with program files in it (`.exe`, libraries and the like), a password, files that would land outside the add-on folder, or an unreasonable size.[^archive]
- A link on another site. Only GitHub, GitLab and Codeberg are allowed.
- A client with no `Interface` folder: start the game once, or choose the right folder with **Set client folder…**.[^view]

Each refusal says "Nothing on this machine was changed."

## Remove

**Remove** asks "Remove *name*?" and explains: Yu'lon takes back the files it put in your client. A file you changed stays, and your settings (`WTF`) are never touched. If you had your own add-on with that name first, it is put back.[^view]

## If something goes wrong

- Buttons grey out while another job runs on the Modules tab. Wait, then press again.
- For add-ons Yu'lon installs by itself, see [Default add-ons](/playing/default-addons.md).

[^box]: Labels and the note of the add-on box.
[^view]: The dialogs, the pointer line and the refusals are in the Server tab code.
[^addons]: Where an add-on is written and how it is recorded is in the outside add-ons module.
[^layout]: How names and version ranges are decided is in the add-on layout module.
[^archive]: What a zip may and may not contain is in the add-on archive module.
