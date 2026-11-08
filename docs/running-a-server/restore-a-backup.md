---
type: How-to
title: Restore a backup
description: Put a backup back after reading a plan, and understand what a restore overwrites.
tags: [restore, backup, plan, databases, recovery]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Restore box, plan text and run
  - id: shelf
    resource: pylauncher/yulon/backup_shelf.py
    title: Backup list and kept sets
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Restore a backup

Restore puts the databases from a backup back on your server. It works as a plan first, then a second press, so you can read what will be overwritten before anything happens.[^view]

## Steps

1. Open the **Backups & updates** tab.
2. Click a backup in the **Backups** list. You can also pick one from an uninstall's kept backups.[^shelf]
3. Press **Show restore plan**. The tab says "Pick a backup in the list, then press Show restore plan." until you do.
4. Read the **Restore** box. It names the databases it would overwrite and the size.
5. If the plan is allowed, press **Restore**. If not, the box lists why under "This cannot go ahead:" and Restore stays greyed.

### What you'll see

The plan says "This overwrites:" and lists databases. A restore merges: tables the backup does not contain are left as they are. If the database has to be started on its own, the plan says so, and the game servers stay stopped.[^view]

After a restore: "Restored ... from ... The copy taken beforehand: ..." names the safety copy Yu'lon made of what you had. You can restore that copy later if you change your mind.[^view]

## Backups that do not say which game they are from

An older backup may not name its game. Yu'lon then asks "Restore a backup that does not say which game it is from?" with No as the default. Say yes only if you are sure it is from this game.[^view]

## A restore that was interrupted

If an earlier restore never finished, the plan says so and a notice with **Forget that record** may appear on the tab. Stopping the server then asks "A restore is still running" with a choice to wait or stop anyway; waiting is safer.[^view]

## If something goes wrong

- Select a backup first ("Select a backup first.").
- Restore is greyed after you change the selection; show the plan again.
- The restore did not finish: the Details fold says why. Your safety copy is in the list.
- Restoring does not need the world running; it stops being allowed if one is. Stop the server first ([Start, stop and restart](/running-a-server/start-stop-and-restart.md)).
- For a lost database, see [Repair damaged tables](/running-a-server/repair-damaged-tables.md).

Make backups first: [Back up your server](/running-a-server/backups.md).

[^view]: The plan text, questions and report are in the server page code.
[^shelf]: Kept backup sets from uninstalls appear in the list.
