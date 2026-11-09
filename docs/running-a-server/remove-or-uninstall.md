---
type: How-to
title: Remove or uninstall a server
description: Take a server off Yu'lon's list, uninstall it and keep or delete characters, or just remove its containers.
tags: [uninstall, remove, characters, danger-zone, backups]
resource: pylauncher/yulon/purge.py
sources:
  - id: purge
    resource: pylauncher/yulon/purge.py
    title: The uninstall steps and what they remove
  - id: dialog
    resource: pylauncher/yulon/ui/widgets/remove_dialog.py
    title: The Remove this server? dialog
  - id: forget
    resource: pylauncher/yulon/forgetting.py
    title: Remove from Yu'lon questions
  - id: kept
    resource: pylauncher/yulon/kept_backups.py
    title: Where backups are kept
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Remove or uninstall a server

Removing a server is in the **Danger zone** at the bottom of the **Server** tab. One button, **Remove this server...**, opens a dialog titled **Remove this server?** with the ways to do it.[^dialog]

## The choices

- **Only take it off this list**: Yu'lon stops listing the server and its tab closes. Nothing is deleted: the server folder, its database volume (your characters) and its Docker images stay. A running server is stopped first.[^forget]
- **Uninstall, keep my characters**: removes the folder, containers and images, but keeps the database volume so installing again to the same folder finds your characters.[^dialog]
- **Uninstall everything**: removes the folder, containers, images and every volume, including the characters.[^dialog]
- **Advanced**, then **Stop and remove containers...**: stops the server and deletes its containers. Characters are kept, and the next Start recreates them.[^dialog]

Some games have no Uninstall yet. The dialog then says so and offers only the list and container choices.[^dialog]

## Uninstall: steps

1. Press **Remove this server...** and pick one of the two Uninstall choices, then **Remove**.
2. Yu'lon reads and shows a plan of what would go. Nothing is removed yet.
3. Tick or untick **Keep my characters (the database volume is left alone)** if you want to change your mind. If the server has a ready-to-play client, **Also delete its ready-to-play client at ...** is ticked at first.
4. Press **Uninstall this server** (or **Cancel**).[^purge]

### What gets saved first

An uninstall deletes the server folder, and your backups live inside it. So Yu'lon first moves them to a folder beside it named like `<server folder> - kept backups`. If that name is taken it uses `(2)`, `(3)`. You can restore from there later; see [Back up your server](/running-a-server/backups.md).[^kept] Yu'lon also saves a copy of the server's log first.[^purge]

Uninstall only removes what Yu'lon created: the server folder, that install's Docker project and its record. It refuses folders it did not create, such as a server you adopted.[^purge]

## What you'll see

First the plan, then a result line. If it breaks off, the **Details** fold under the line says why.

## If something goes wrong

- "This server cannot be removed yet": a restore, backup, update or move is running. Wait for it. Removing is refused rather than cutting it off.
- Yu'lon could not keep your backups out of the folder: nothing was removed, and your backups are where they were.
- The uninstall did not finish: Details says why and gives a command to list what is left.
- To bring a listed-off server back, use **Use existing...** on its Catalog tile and point it at the same folder.[^forget]
- Docker space freed later: [Free Docker space](/running-a-server/free-docker-space.md).

[^dialog]: The three choices and the Advanced fold are in the remove dialog.
[^forget]: The remove-from-list question is in the forgetting module.
[^purge]: The uninstall order, ownership rules and steps are in the purge module.
[^kept]: The kept backups folder naming is in the kept backups module.
