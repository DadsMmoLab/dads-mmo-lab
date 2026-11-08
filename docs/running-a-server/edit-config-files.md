---
type: How-to
title: Edit settings and config files
description: Change module settings on cards or edit a config file directly on the Tuning tab, with backups and the restart each change needs.
tags: [tuning, config, settings, backup, restart]
resource: pylauncher/yulon/tuning.py
sources:
  - id: tuning
    resource: pylauncher/yulon/tuning.py
    title: Reads and writes settings and says what applying them costs
  - id: panel
    resource: pylauncher/yulon/ui/widgets/tuning_panel.py
    title: The Tuning tab's cards and file editor
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Tuning tab buttons and messages
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Edit settings and config files

The **Tuning** tab holds your server's settings. You can change them on cards or open the config file itself. Every save keeps a backup of the file as it was.[^view]

## Change a setting on a card

1. Open the **Tuning** tab. Each installed module with settings has a card, and there is a **Server rates** card ([Server rates and time zone](/running-a-server/server-rates-and-time-zone.md)).
2. Change a value on the card.
3. Press the card's **Save**.

### What you'll see

The report says which keys changed, in which file, and where the backup of the old file is. It also says what applying the change costs. That is one of:[^tuning]

- Restart: the world server reads the file when it starts, so the change takes effect at the next restart.
- Recreate: the containers must be recreated, not just restarted.
- Rebuild: the server must be compiled again.
- Nothing to apply.

A card's **Revert** puts its controls back to what its file says.[^panel]

## Edit the file itself

1. Choose the file in the file side of the tab (a narrow window switches between **Settings** and **Edit file**).
2. Edit the text.
3. Press **Save file**.

If the text no longer looks like a settings file, Yu'lon asks "Save this file anyway?" with No as the default. Some core files lock lines Yu'lon sets itself; a save that touches one is refused with "Nothing was written. Yu'lon sets these lines, so they stay as they are:" and the keys follow. Core files show only the changed lines for you to confirm.[^view]

Other buttons in the editor: **Revert file** puts the file back from its backup, and **Default for this line** replaces the cursor's line with the shipped one (it writes nothing until you save).[^panel]

## The buttons across the top

- **Reload from disk** reads the files again.
- **Revert all changes** puts every control back to what its file says. It writes nothing.
- **Restart server...** and **Recreate containers...** take the server down to apply a change. Both ask first, and anyone playing is disconnected. A banner says "Waiting on a restart" or "Waiting on a recreate" and names the files.
- **Reset to default** puts the server's own files back: [Reset settings to default](/running-a-server/reset-to-defaults.md).[^view]

Your characters are not affected by Recreate: the database lives in a Docker volume that is kept.[^view]

## If something goes wrong

- "... was NOT written": the message names the file and the reason.
- "The file changed while it was open, so nothing was saved; reopen it to see what it says now." Press **Reload from disk** and redo the change.
- Settings are greyed while a job runs or the server's folder cannot be written; the reason shows under the buttons.
- A change you saved made the server misbehave: use **Revert file**, or **Reset to default**.

[^tuning]: What each change costs is defined by the settings module.
[^panel]: The cards and editor buttons are in the Tuning panel widget.
[^view]: Messages and top buttons are in the server page code.
