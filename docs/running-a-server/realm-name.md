---
type: How-to
title: Change the realm name
description: Set the name players see in the game's realm list.
tags: [realm, name, networking, realm-list]
resource: pylauncher/yulon/realm_name.py
sources:
  - id: realm
    resource: pylauncher/yulon/realm_name.py
    title: Rules and storage for the realm name
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Realm name field on the Networking tab
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Change the realm name

The realm name is the name your players see in the game's realm list. You set it on the **Networking** tab.[^view]

## Steps

1. Open the **Networking** tab.
2. Find **Realm name**. The box says "Up to 32 characters".
3. Type the new name.
4. Press **Save name**.

### What you'll see

The tab shows "Saving the realm name..." and then reports the result. The login server re-reads the name about every ten seconds, so players see it the next time their client opens the realm list.[^realm]

## Rules

- 1 to 32 characters.
- No line breaks or other unprintable characters.
- Characters outside the basic range, such as emoji, are refused.
- Quotes, apostrophes and similar are fine ("Jerra's Realm" works).
- The name must be unique on the server.[^realm]

Yu'lon remembers the name for this install and puts it back on every Start, so a rebuild does not undo it.[^realm]

## When the box is missing

- On Conquest of Azeroth the realm type decides the name, so there is no box. The tab says "The realm name follows the realm type."[^realm]
- Single-container servers have no realm row, so they have no realm name field.[^realm]

## If something goes wrong

- "The realm name could not be read from this server." The server's database may be down; start it and look again.
- "The realm name could not be saved. Nothing was changed." Try a different name. The name may already be in use or contain something refused.[^view]

For the address your friends connect to, see [Play with friends](/running-a-server/play-with-friends.md).

[^realm]: Limits, storage and exceptions are in the realm name module.
[^view]: Labels and messages are in the server page code.
