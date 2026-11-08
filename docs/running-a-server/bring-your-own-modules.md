---
type: How-to
title: Bring your own modules and add-ons
description: Install a server module from a link or a folder, and add game add-ons from a link, folder or zip.
tags: [modules, custom, addons, github, bring-your-own]
resource: pylauncher/yulon/module_source.py
sources:
  - id: source
    resource: pylauncher/yulon/module_source.py
    title: How a module is derived from a link or folder
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Bring your own... and the custom module card
  - id: addons
    resource: pylauncher/yulon/ui/widgets/client_addons_box.py
    title: The add-on box
  - id: manifest
    resource: pylauncher/yulon/manifest.py
    title: Allowed repository sites
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Bring your own modules and add-ons

If you want a module Yu'lon does not ship, you can point Yu'lon at its repository or at a folder on your computer. You can also add game add-ons to the client you play with.[^view]

## Open the options

On the **Modules** tab press **Bring your own...**. It opens a card titled **A module this app does not ship**, with the line "Paste a repository link, or point at a folder on this computer. Yu'lon reads it and installs it the same way as any row above."[^view]

## Install a module from a link

1. Press **Install from link...**
2. Paste the repository link. Yu'lon accepts links on github.com, gitlab.com and codeberg.org.[^manifest]
3. Confirm any questions it asks.

## Install a module from a folder

1. Press **Install from folder...**
2. Choose the folder on this computer that holds the module.

### What Yu'lon works out

Yu'lon reads what the module has and treats it as a C++ module named for its repository. It uses the module's own `conf/*.conf.dist` files and `data/sql` folders if it has them. It does not invent anything else, so a module that needs extra setup may need more steps by hand.[^source] The folder or repository name may use capitals.[^view]

After the install, the module needs a rebuild like any other C++ module: see [Update or rebuild the server](/running-a-server/update-and-rebuild.md). Once installed it appears in the module list, and Remove and update checks work as for other modules.[^source]

## Add a game add-on

The **Game add-ons you bring** box adds add-ons to the game client you play with.[^addons]

- **Add-on from link...**: a GitHub, GitLab or Codeberg repository, or a .zip link there.
- **Add-on from folder...**: a folder holding an add-on, or a folder of add-ons.
- **Add-on from zip...**: a .zip file on this computer.

An add-on of yours with the same name is replaced only if you say yes. The list **Added by Yu'lon:** has **Update** and **Remove** for each one, and Remove puts yours back.[^addons]

## If something goes wrong

- A link on another site is refused. Use a repository on one of the three sites.
- Some games have no route for custom modules; the card then says so in a sentence instead of showing the buttons.[^view]
- Buttons are greyed while another job runs; wait for it.
- If a custom module will not build, remove it and rebuild; see [Troubleshooting](/running-a-server/troubleshooting.md).

[^view]: The card, its wording and the sub-menu are in the server page code.
[^manifest]: The allowed repository sites are github.com, gitlab.com and codeberg.org.
[^source]: What is derived and what is not is described in the module source module.
[^addons]: The add-on box labels and note are in the add-on box widget.
