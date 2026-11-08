---
type: How-to
title: Back up your server and manage kept backups
description: Make a backup, read the list, delete old copies, clean up automatically, and find backups an uninstall kept.
tags: [backup, backups, clean-up, maintenance, databases]
resource: pylauncher/yulon/backup_shelf.py
sources:
  - id: shelf
    resource: pylauncher/yulon/backup_shelf.py
    title: Delete and Clean up rules and what is never removed
  - id: kept
    resource: pylauncher/yulon/kept_backups.py
    title: Where an uninstall keeps backups
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Backups box on the Backups & updates tab
  - id: dialog
    resource: pylauncher/yulon/ui/widgets/clean_up_dialog.py
    title: The Clean up old backups dialog
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Back up your server and manage kept backups

A backup is a copy of your server's databases: accounts, characters and the rest. Make one before you update, test a module or do anything you cannot easily undo. You find backups in the **Backups** box on the **Backups & updates** tab.[^view]

## Make a backup

1. Open **Backups & updates**.
2. In **Backups**, press **Back up now**.
3. Wait. The report says "Backing up... this can take minutes on a full world."

### What you'll see

The report says "Backed up to" a folder and lists each database with its size. If the server was running, it adds "note: the server was running, so this is a hot copy." The new files show up in the list. Backups live in the server folder under `sql_scripts/backups`.[^view][^shelf]

**Refresh** reads the list again. If there are none, the report says "No backups yet" and names the folder.[^view]

## Other backups Yu'lon makes

Yu'lon also takes copies for you before some actions: an update, a restore (a safety copy), a move, and before some module installs. They land in the same list.[^shelf]

## Delete a backup

1. Click it in the list.
2. Press **Delete...**
3. Read the question. The file is deleted for good; it does not go to the trash. If it cannot be restored, or Yu'lon would normally keep it, the question says so.

If nothing is picked the report says "Pick a backup to delete."[^view]

## Clean up old backups

**Clean up...** opens a dialog titled **Clean up old backups**.[^dialog]

1. Choose **Keep the newest** a number of copies of each database, or **Delete copies older than** a number of days.
2. Optionally tick **Also delete cut-short copies and files Yu'lon cannot restore**.
3. Read the list and the line that says how much it frees. The OK button says how many files it will delete.
4. Optionally tick **After every Back up now, keep only that many copies of each database**. This is off unless you turn it on and only applies to **Back up now**.

Yu'lon always keeps the newest good copy of every database, the copy the last update took, anything an unfinished restore needs, and the copy taken before an installed mod. Read-only files, files with another name and another game's backups are left for you to delete one by one.[^dialog]

## Backups kept after an uninstall

When you uninstall a server and keep your backups, they go in a folder beside the server folder named like `<server folder> - kept backups` (then `(2)`, `(3)` for later ones). The Backups list shows them for restoring, and Yu'lon never deletes from that folder.[^kept][^shelf]

## If something goes wrong

- "The backup did not finish": the Details fold says why. If a damaged table is the cause, the message names it and offers a repair: [Repair damaged tables](/running-a-server/repair-damaged-tables.md).
- Backup is greyed while another job runs or the database password is unknown; the reason shows under the buttons.
- Backups need disk space. Clean up here and see [Free Docker space](/running-a-server/free-docker-space.md).

Next: [Restore a backup](/running-a-server/restore-a-backup.md).

[^view]: Labels and messages are in the server page code.
[^shelf]: The backups folder and the keep rules are in the backup shelf module.
[^dialog]: The Clean up dialog text is in the dialog widget.
[^kept]: The kept-backups folder name is built by the kept backups module.
