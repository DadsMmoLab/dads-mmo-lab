---
type: How-to
title: "Logs: read them and save a file for support"
description: Open Yu'lon's logs, copy recent lines, and save one zip with passwords taken out to send when something is wrong.
tags: [logs, support, zip, diagnostics, redaction]
resource: pylauncher/yulon/ui/logs_view.py
sources:
  - id: logs
    resource: pylauncher/yulon/ui/logs_view.py
    title: The Logs tab
  - id: settings
    resource: pylauncher/yulon/ui/tray_settings.py
    title: The Diagnostic logs button in Settings
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Console tab's live log
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Logs: read them and save a file for support

The **Logs** page shows what Yu'lon and your servers have written, and builds one file you can send when something is not working.[^logs]

## Open it

Press **Diagnostic logs...** in **Settings**. The button's tip says "Open Yu'lon's diagnostic logs and save a support file when something goes wrong".[^settings]

## Read a log

1. Use the drop-down to pick a log.
2. The viewer shows it; it says "Reading..." while it loads.

Passwords, login keys and your home folder are taken out of everything shown, copied or saved. The files on disk stay as written.[^logs]

## Save logs for support

1. Press **Save logs for support...**
2. Choose where to put the zip.
3. Wait for "Saving the support file...".
4. Send the file it made.

### What you'll see

"Saved ... Send this file; the passwords are already taken out." If the file is larger than the size Yu'lon aims for, the message warns it may not fit a free chat upload limit and that the list of contents inside the zip says what was trimmed. A line also says how many logs were left out and why.[^logs]

A very short password in ordinary log lines cannot be taken out reliably. If that applies, the page says so and tells you where the password is set.[^logs]

## Other buttons

- **Open log folder** opens the folder of run logs and server snapshots. Passwords are not taken out of these, so send the support file instead.
- **Copy last 200 lines** copies the end of the shown log. It says "Nothing to copy yet." when empty.
- **Refresh** reads again.[^logs]

## Live server log

For the world server's log as it happens, use the **Console** tab: [Console and server commands](/running-a-server/console-and-server-commands.md).[^view]

## If something goes wrong

- "Nothing has been logged yet." There is nothing to read for that source.
- Saving fails: the status line says why. Try another folder.
- Yu'lon will not let the window close while it is saving the support file; wait for it to finish.[^logs]

[^logs]: Buttons, notes and messages are in the Logs tab code.
[^settings]: The Diagnostic logs button is on the Settings page.
[^view]: The live log is the Console tab's Follow worldserver log.
