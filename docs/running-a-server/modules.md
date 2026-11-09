---
type: How-to
title: Install, remove and update modules
description: Add or remove server modules on the Modules tab, check for updates, and finish the rebuild or database step a module may need.
tags: [modules, install, remove, update, rebuild]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Modules tab, its buttons and messages
  - id: panel
    resource: pylauncher/yulon/ui/widgets/modules_panel.py
    title: Module rows and their chips
  - id: presses
    resource: pylauncher/yulon/server_build_presses.py
    title: Rebuild and update button labels
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Install, remove and update modules

Modules add features to your server, such as extra rules or bots. The **Modules** tab lists the modules Yu'lon knows for your game and shows which ones your server already has.[^view]

## Install a module

1. Open the **Modules** tab.
2. Find the module's row and press its **Install**. If it is greyed, the row says why.[^panel]
3. If the module asks questions (for example a rate or a choice), answer them in the box that opens. Cancel stops with nothing changed.
4. Wait for the report box to finish.[^view]

### What you'll see

The report box tells you what was done. Many modules are code that needs a compile: the row then shows a rebuild chip, and a banner appears above the list with a **Rebuild the server...** button.[^presses] Until that has run, the module sits on disk and does nothing.[^view] See [Update or rebuild the server](/running-a-server/update-and-rebuild.md).

If the module has database changes it could not apply, the report says to press **Apply module SQL** with the server stopped.[^view]

## Remove a module

Press **Remove** on the row. Some modules ask first: a module that only changed settings tells you each setting and the value it goes back to, and a Remove that changes the database asks you to confirm. Answer No to cancel with nothing changed.[^view]

## Update a module

1. Press **Check module updates** at the top. It only asks each installed module's source how far behind it is, and changes nothing on your server.[^view]
2. On a row that says it has an update, press **Update** in its chip.

Yu'lon checks again by itself in the background after the tab opens, and about once an hour while it stays open.[^view]

## The buttons at the top

- **Check module updates**: read-only, as above.
- **Refresh**: reads the module folders again. It is quick and does not use the network.
- **Apply module SQL**: applies the pending database steps of the installed modules.
- **Bring your own...**: opens the options for modules and add-ons Yu'lon does not ship. See [Bring your own modules and add-ons](/running-a-server/bring-your-own-modules.md).[^view]

## If something goes wrong

- "Something else is running": wait for the current job, then press again. Nothing was started.
- A row that says the module is installed but not in the catalog has no steps Yu'lon can use; it is still compiled into your server.[^view]
- A module that will not build after a server update may need a newer or older server; the report tells you which button to try.
- Greyed buttons with a database reason: see [Troubleshooting](/running-a-server/troubleshooting.md).

[^view]: Labels, banners and messages are in the server page code.
[^panel]: Each row carries its own Install and Remove, plus chips for owed jobs.
[^presses]: The rebuild button label is defined once and reused.
