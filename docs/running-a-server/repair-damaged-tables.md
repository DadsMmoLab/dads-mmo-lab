---
type: How-to
title: Repair damaged tables or a lost database
description: Fix a database table a crash damaged, finish an import that never completed, or rebuild a database Docker lost.
tags: [repair, database, tables, crash, docker]
resource: pylauncher/yulon/table_repair.py
sources:
  - id: repair
    resource: pylauncher/yulon/table_repair.py
    title: Damaged-table repair and its question
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Repair buttons and messages on the Server tab
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Repair damaged tables or a lost database

Yu'lon has three different repairs on the **Server** tab. Pick the one that matches the message you see.

## Repair the damaged table

When the computer stops suddenly, a database table can be marked damaged. The world then cannot read characters. Yu'lon shows a banner and a **Repair the damaged table...** (or **Repair the damaged tables...**) button, also after a backup that failed on one.[^repair]

1. Press **Repair the damaged table...**
2. Read the question. It names the table and database, and says Yu'lon first saves a backup of that database and a copy of the table's files, then repairs only those tables. Players can stay connected; restart the server afterwards.
3. Press Yes.

### What you'll see

A sentence reports the result. If the table cannot be backed up or its files copied, Yu'lon asks again before repairing and warns that rows can be lost. A repair can lose rows it cannot read; the copy keeps them for a specialist.[^repair]

While a repair runs nothing may start, stop or recreate the server.[^repair]

Only certain tables are covered. If a world table is damaged the message points you to a backup instead.[^view]

## Repair: finish the database import

If an install's first import never finished, the Server tab offers **Repair: finish the database import...** It works only when the databases hold no accounts or characters to lose (or only ones from the unfinished import).[^view]

1. Press the button once. It does not run yet. It changes to **Press again to overwrite the databases** and a **Cancel** appears.
2. Read the text under the buttons: everything in the auth, characters and world databases is overwritten, the server must be stopped, and a full import takes 10 to 30 minutes and cannot be stopped or the window closed once it starts.
3. Press it again to go ahead, or **Cancel**.

## Repair the database

If Docker no longer has the server's database, the Server tab offers **Repair the database...** and **Restore a backup...**. Repair makes the database again from the server files and starts the server. Your accounts and characters are not in the server files, so they do not come back. If you have a backup, restore it on the **Backups & updates** tab once the repair finished. This can take several minutes.[^view]

**Restore a backup...** takes you to that tab. See [Restore a backup](/running-a-server/restore-a-backup.md).

## If something goes wrong

- The repair did not finish: "The repair did not finish. The backup is kept." Nothing else was changed.
- Buttons are greyed while the server is busy; wait for the other job.
- See [Troubleshooting](/running-a-server/troubleshooting.md).

[^repair]: The question, results and hold are in the table repair module.
[^view]: Button labels and messages are in the server page code.
