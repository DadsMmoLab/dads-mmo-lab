---
type: How-to
title: "Game accounts: create, GM level, password"
description: Make game accounts for yourself and your players, change a password or GM level, and delete an account.
tags: [accounts, gm-level, password, players]
resource: pylauncher/yulon/ui/controller_view.py
sources:
  - id: view
    resource: pylauncher/yulon/ui/controller_view.py
    title: The Accounts tab
  - id: rules
    resource: pylauncher/yulon/account_rules.py
    title: Name and password rules
  - id: users
    resource: pylauncher/yulon/useraccounts.py
    title: Which accounts are listed
generated:
  by: claude-sonnet-5-5/docs-okf
  at: 2026-11-08T21:00:00Z
status: draft
---

# Game accounts: create, GM level, password

A game account is the login you and your players type at the WoW login screen. You manage them on the **Accounts** tab, which works without the console.[^view]

## Create an account

1. Open the **Accounts** tab.
2. In **Create account**, fill in **Username**, **Password** and **GM level**.
3. Press **Create**.

### The rules

- Username: letters A-Z, digits and `_`. It must be at least 3 characters; the longest is 16 or 17 depending on the game.[^rules]
- Password: 4 to 16 characters from letters, digits and `_ @ # % + = ! -`.[^rules]
- Names starting with `YULON_` are Yu'lon's own and cannot be created.[^rules]
- GM level runs from 0 (a normal player) up to the game's highest level. Most games stop at 3; Tortoise accepts 4.[^view]

The password box hides what you type and is cleared after you press Create.[^view]

## See the accounts on the server

**Accounts on this server** lists each account like "name - id 5 - GM level 3". The list leaves out Yu'lon's own helper account and the bots' accounts. Press **Refresh the list** to read it again.[^users]

## Change a password or GM level

1. Click the account in the list. Until you do, the change buttons are greyed with "Choose an account first."
2. For a password, type it in **New password** and press **Set password**.
3. For a GM level, set the number and press **Set GM level**.

The report line says what happened, and the list is read again so you can check.[^view]

## Delete an account

Select the account and press **Delete account...**. Yu'lon first reads which characters it has and asks, naming them. Deleting also deletes its characters, and "only a backup made before now can bring it back".[^view] Take a backup first: [Back up your server](/running-a-server/backups.md).

## If something goes wrong

- A rule message tells you which part of the name or password to change.
- Some games keep accounts in a form Yu'lon does not write yet. The tab then greys Create and tells you the console command to use instead ([Console and server commands](/running-a-server/console-and-server-commands.md)).[^view]
- If a database password is unknown, the buttons are greyed with that reason. See [Troubleshooting](/running-a-server/troubleshooting.md).
- Set password and Set GM level go through Yu'lon's command channel, so the channel must be set up. If it is not, the report line says "Turn it on from the Server tab" (look for **Turn on the command channel** in the **Command channel** section).[^users]
- On some games a new account's GM level only takes effect when the server next starts. The report line says "GM level N is saved on the account and takes effect when the server next starts" or "GM level N is live now".[^users]

[^view]: Labels, ranges and messages are in the server page code.
[^rules]: The rules are shared by Create, Set password and the other account buttons.
[^users]: The listing excludes the app's own account prefix and the bot accounts.
