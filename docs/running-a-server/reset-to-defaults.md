---
type: How-to
title: Reset settings to default
description: Put the server's own settings files back to how Yu'lon installed them, keep your modules, and undo it if needed.
tags: [reset, defaults, config, tuning, undo]
resource: pylauncher/yulon/reset_defaults.py
sources:
  - id: reset
    resource: pylauncher/yulon/reset_defaults.py
    title: What default means and what a reset keeps
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Reset to default menu and its messages
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Reset settings to default

If your server's settings files are in a mess, **Reset to default** on the **Tuning** tab puts the server's own files back to how Yu'lon installed them. Module files are kept, and a backup of each file is made first.[^view]

## Steps

1. Open the **Tuning** tab.
2. Press **Reset to default**. A menu opens.
3. Choose **All server settings...** to reset every server file, or one file from the list.
4. Read the question and confirm.

### What "default" means

It is what a fresh install writes, not a bare copy of the upstream sample. A fresh install already fills in your database logins, the port, the command channel, the bot population and similar, and a plain sample file would lock the server out of its own database. So the reset rebuilds the file the way the install did.[^reset]

Three things survive a reset:[^reset]

- Module config files are never touched.
- Inside a server file, keys that an installed module declares keep their current value.
- The time zone is kept.

## Undo the last reset

Choose **Undo the last reset...** in the same menu. It copies each file back from the backup named beside it. Anything you changed since the reset is replaced, and the file as it is now is first kept beside it as an `.undo-....bak` file.[^view] If there is nothing to undo it says "Nothing to undo".[^view]

## What you'll see

The report names each file reset and its backup. Apply rules still hold: a restart may be needed before the server uses the file; see [Edit settings and config files](/running-a-server/edit-config-files.md).

## If something goes wrong

- "Yu'lon couldn't read the settings files. Details below says why."
- "Reset to default did not finish. Details below says why."
- It is refused while another job runs. The window can be closed once the reset is done ("this window closes normally once it is done").[^view]
- For a damaged server rather than a bad settings file, see [Troubleshooting](/running-a-server/troubleshooting.md).

[^reset]: What a reset writes and keeps is described in the reset module.
[^view]: The menu labels and messages are in the server page code.
