---
type: How-to
title: Update or rebuild the server
description: Move the server to the newest code, rebuild it after adding modules, go back to the tested version, and apply database updates.
tags: [update, rebuild, maintenance, server-version, modules]
resource: pylauncher/yulon/server_build_presses.py
sources:
  - id: presses
    resource: pylauncher/yulon/server_build_presses.py
    title: The Server version button labels
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: Rebuild, update, return-to-pin and database update flows
  - id: installer
    resource: pylauncher/yulon/catalog/installer.py
    title: The rebuild question and measured build times
  - id: native
    resource: pylauncher/yulon/catalog/native.py
    title: Database update button label and notes
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Update or rebuild the server

The **Server version** section of the **Backups & updates** tab holds the buttons that change which server code you run. Which buttons show depends on the game.[^presses]

This page is about the server itself. The Yu'lon app updates separately; see **Check for Yu'lon updates** in Settings.

## Rebuild the server

Use this after you installed modules that need compiling. A banner above the Modules list also carries the same button.

1. Open **Backups & updates**.
2. Press **Rebuild the server...**
3. Read the question. It says how long the compile takes (about 15 minutes on a fast Mac and 35 to 72 minutes on a typical Linux PC, about 68 minutes on a Windows PC that gives Docker little memory), that the server goes down while the containers are replaced, and that if the new build is broken the old one is put back by itself. Saying No changes nothing.[^installer]
4. Press Yes. A log panel under the buttons shows the compile.

### What you'll see

The server stops, builds, and comes back when it is ready. If a build is under way you cannot start a second one, and a module job running at the same time makes the press refuse with "Wait for it to finish".[^view]

## Update the server to latest

This fetches the newest code from the repositories your server was built from and compiles it. It is code nobody has tested with this app, so Yu'lon asks first and recommends a backup.[^view]

1. Press **Update the server to latest...**
2. Choose **Back up first, then update**, **Update without a backup**, or **Cancel**. Cancel is the default.
3. With a backup, Yu'lon takes it, tells you where it was saved and asks "Update now?" once more. A failed backup stops the update.[^view]

## Return to the tested pin

After an update the button **Return to the tested pin...** appears under **Advanced**. It compiles the server again from the commit this app was tested against. It does not undo anything a newer server already wrote into your databases, which is why no backup is offered at this step.[^view]

## Advanced buttons

Press **Advanced** in the section to show:[^view]

- **Return to the tested pin...** as above.
- **Put the folders back...** shows while Yu'lon offers it. When Yu'lon's own files were not all written back into the source folders, a rebuild is refused until this has run, and the message names the button.[^native]
- **Adopt as imported...** says that the databases of a server Yu'lon did not install are a finished import. It asks first and refuses while the server runs. Yu'lon cannot check the import finished; you are saying so.

## Apply pending database updates

**Apply pending database updates...** applies database updates a server version carries. It asks first. If the server is running it refuses and tells you to press **Stop**, then press the button again.[^native]

## If something goes wrong

- A greyed button explains why in the line under the section.
- A rebuild that fails keeps your old build when it can. Read the log panel; **Stop now anyway** appears if a stop is waiting on a loading world.
- If the server will not start afterwards, see [Troubleshooting](/running-a-server/troubleshooting.md). A backup you took first can be restored: [Restore a backup](/running-a-server/restore-a-backup.md).
- Rebuilds and builds leave Docker data behind: [Free Docker space](/running-a-server/free-docker-space.md).

[^presses]: Labels are defined once so every message reads them from there.
[^view]: The questions and the flows are in the server page code.
[^installer]: The rebuild question and times come from the installer module.
[^native]: Notes and labels for these presses are in the native install module.
