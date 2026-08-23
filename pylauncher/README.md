# Yu'lon

The launcher application: a desktop app that installs and runs a self-hosted WoW server for you,
with buttons instead of a terminal.

It is not finished. This file says what works today and on which operating system, so nobody has
to guess. The plan for the rest is in [`../pyplan/`](../pyplan/README.md).

## What runs where, today

| | Linux | Windows 10/11 | macOS |
|---|---|---|---|
| Install a server from the Catalog | yes | no | no |
| Attach to a server that already exists | yes | yes | never run |
| Start and stop it | yes | yes | never run |
| Follow the worldserver log | yes | yes | never run |
| GM console (type a command at the server) | yes | no — needs a pseudo-terminal | never run |
| The packaged download | AppImage, launched | .exe, launched | .dmg, never launched |

"Never run" means exactly that: there is no Mac on this project. The macOS work is real code with
tests, but no one has yet started the app on a Mac, so nothing in the macOS column is a claim.

## Installing a server

**Linux only.** Every entry in `yulon/catalog/catalog.json` declares `platforms: ["linux"]`, and
the app says so on the tile rather than letting you press Install and fail: the Install button is
disabled with the reason next to it. Proven on a fresh Ubuntu 24.04 VM (2026-08-21), which built
AzerothCore with playerbots from source and ended with all three containers up.

**Windows** cannot install a server through the app yet. What is proven is the layer underneath it:
on a Windows 11 box that had never had Docker, `yulon.exe --provision` installed WSL2 and Docker
Desktop and reached a working daemon (2026-08-23), asking for nothing but a reboot and the prompts
Windows and Docker Desktop put in front of a person themselves. The install path that would sit on
top of that is not built, and `--provision` is a diagnostic flag, not a button in the app.

**macOS** has neither. The `.dmg` is built by CI and has never been launched by anyone, so whether
it even opens past Gatekeeper is unknown.

## Managing a server you already have

This is the part that works most widely. Point the app at an existing install with "Use existing…"
— that button is deliberately enabled on every platform — and you get the Server, Console,
Accounts, Maintenance, Modules and Networking tabs. On Windows, start, stop, following the log and
the database work behind a module apply have each been run against a real install; the rest of
those tabs are built and unit-tested there but have not been driven by hand.

Two honest gaps:

- The **GM console** needs a pseudo-terminal, which Windows does not have. There, the command box
  and Send button are disabled with the reason shown on the tab. Following the log needs no
  terminal and stays enabled.
- **Creating an account** does not go through the console at all. It writes the SRP6 registration
  row itself, which is the only way that works on all three platforms, and the only way at all to
  make the very first account.

## What has been run against a real server, rather than only tested

All of the below on Linux; none of it yet on Windows or macOS.

- **Install and a human click-through of the UI** — Ubuntu 24.04 VM, 2026-08-21.
- **Database backup and restore** — 2026-08-23, full round trip against a live AzerothCore install:
  four schemas found rather than the three the old guide hardcodes, a 292 MB world dump, the value
  in the dump read back after the restore, and a wrong confirmation token refused.
- **Account creation** — 2026-08-23. The salt and verifier we write are byte-for-byte what the
  worldserver itself wrote for accounts it created at its own console, non-ASCII passwords
  included.
- **Stop and remove containers** — 2026-08-23, on an install with 650 accounts: every container
  removed, both volumes kept, the stack started again from nothing, and all 650 accounts read back
  intact.

## Which servers

The catalog lists four: WoW WotLK (stable), WoW TBC and WoW Vanilla (beta), WoW Tortoise (work in
progress). Only WotLK has had its features exercised against a live server; the other three are a
later phase and are listed, not vouched for.

## Odds and ends

- The builds are not code-signed. Windows SmartScreen and macOS Gatekeeper will warn on first run.
- Yu'lon never downloads or bundles game client files. You point it at a client you already own.
- On launch it checks GitHub Releases and shows a banner if a newer version exists. It never
  replaces itself.

## Developers

Design docs, roadmap and checklist: [`../pyplan/`](../pyplan/README.md). Setup and conventions:
[`../pyplan/contribution.md`](../pyplan/contribution.md) and
[`../pyplan/style-guide.md`](../pyplan/style-guide.md).
