---
type: How-to
title: Free Docker space
description: Remove Docker's build cache and old builds that Yu'lon's servers left behind, without touching your databases.
tags: [docker, disk-space, cache, clean-up, maintenance]
resource: pylauncher/yulon/docker_space.py
sources:
  - id: space
    resource: pylauncher/yulon/docker_space.py
    title: What the dialog offers and its notes
  - id: dialog
    resource: pylauncher/yulon/ui/widgets/free_docker_space_dialog.py
    title: The Docker's disk space dialog
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The button and its messages
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Free Docker space

Building a server leaves behind a lot of cached data in Docker. **Free Docker space...** shows what is taking room and lets you remove parts of it.[^view]

## Steps

1. Open the **Backups & updates** tab.
2. In the **Backups** box press **Free Docker space...** The report reads "Reading what Docker holds..."
3. In the dialog titled **Docker's disk space**, tick what should go:[^dialog]
   - **Docker's build cache**, with its size. Ticked at first.
   - **Yu'lon's old untagged builds**, if there are any.
   - **This server's kept build**, if the server has one. Not ticked at first.
4. Press **Remove ticked**. If nothing is ticked the button is greyed.

### What each part is

- Build cache: the steps of every build, so the next build is faster. It belongs to every Docker project on the computer, not only Yu'lon's. After removing it, the next build of any server compiles from the start and takes as long as a first install.[^space]
- Old untagged builds: nameless images that earlier builds of Yu'lon's servers left behind, including servers you removed. One still used by a container is left alone.[^space]
- Kept build: the build your last rebuild kept. The next **Rebuild the server...** can use it instead of compiling, so it is not ticked.[^space]

The dialog says what is never removed: the images your servers run on, their databases, and anything in Docker that is not Yu'lon's, apart from the build cache.[^space]

## What you'll see

The report lists what was freed. On Windows, Docker Desktop keeps its data in one disk file, so the free space Windows shows may only grow once Docker Desktop compacts that file.[^space]

## Old database copies

Backups are not kept in Docker. To remove old ones, use **Clean up...** in the same box: [Back up your server](/running-a-server/backups.md).[^view]

## If something goes wrong

- Greyed or "wait" message: a server build is running. The button refuses while one runs. Try again afterwards.
- "Docker space was not freed. Details below says why." Open the Details fold.
- The space did not come back on Windows: see the note above.
- If Docker itself is not answering, see [Troubleshooting](/running-a-server/troubleshooting.md).

[^view]: The button, its pointer line and messages are in the server page code.
[^dialog]: The dialog's rows and button are in the dialog widget.
[^space]: Notes for each row are in the Docker space module.
