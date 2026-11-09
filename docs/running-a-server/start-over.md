---
type: How-to
title: Start over after an unfinished bring-in
description: Clear a folder where a bring-in from another computer stopped part-way and begin a plain install, with a copy kept first.
tags: [start-over, bring-in, reset, reinstall, move]
resource: pylauncher/yulon/start_over.py
sources:
  - id: startover
    resource: pylauncher/yulon/start_over.py
    title: What Start over does, step by step
  - id: catalog
    resource: pylauncher/yulon/ui/catalog_view.py
    title: Where the question appears
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Start over after an unfinished bring-in

If you brought a whole server in from another computer ([Move your server to another computer](/running-a-server/move-to-another-computer.md)) and it stopped part-way, that folder cannot take a normal install, because a plain install would boot a half-loaded server. **Start over with a plain install** clears the folder and installs fresh.[^startover]

## When you will see it

You press **Install** on the Catalog tile for that folder. Instead of installing, Yu'lon asks "Start over with a plain install?"[^catalog]

## Steps

1. Read the question. It says the unfinished bring-in and everything it loaded will be removed, then a plain install starts. Nothing from the other computer is kept. A copy of what is there is kept first.
2. Choose Yes to start over. Choose No to keep the bring-in; you can then press **Bring from another computer...** again with the same file and folder to carry it on.[^startover]

### What happens

In order: Yu'lon reserves the server, stops it, saves a backup of every database it has, removes the containers, volumes, built images, files modules put in the game client and everything in the folder (the backups move beside the folder first), checks that nothing is left, sets the old bring-in record aside, and runs the plain install.[^startover]

The server folder itself stays, with a few small record files.

## What you'll see

"The folder is clean: nothing of the bring-in is left in it or in Docker. The plain install starts now."[^startover]

## If it stops part-way

Each step is recorded. If Start over stops (a crash, a refusal, Stop), press **Install** on the tile again. Yu'lon asks "Finish starting over?" and carries on without repeating the copy. The tile may say "This folder is being reinstalled from scratch (Start over); press Install to finish it."[^startover]

## If something goes wrong

- "Yu'lon could not keep a copy of this server's databases first, so it removed nothing and the bring-in is as it was."
- "The bring-in's record in this folder cannot be read" or "changed after you were asked": nothing was changed; press Install again.
- "Yu'lon cannot tell which Docker project belongs to this folder": nothing was removed.
- To remove a server entirely, see [Remove or uninstall a server](/running-a-server/remove-or-uninstall.md).

[^startover]: The steps and every message are in the start over module.
[^catalog]: The question is raised by the Catalog tile's Install press.
