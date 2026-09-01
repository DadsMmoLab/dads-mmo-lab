# Yu'lon

The launcher application: a desktop app that installs and runs a self-hosted WoW server for you,
with buttons instead of a terminal.

It is not finished. This file says what works today and on which operating system, so nobody has
to guess. The plan for the rest is in [`../pyplan/`](../pyplan/README.md).

## What runs where, today

| | Linux | Windows 10/11 | macOS |
|---|---|---|---|
| Install a server from the Catalog | run live | no | no |
| Attach to a server that already exists | run live | run live | never run |
| Start and stop it | run live | run live | never run |
| Follow the worldserver log | run live | run live | never run |
| GM console (type a command at the server) | built | no — no `os.openpty` | never run |
| The packaged download | AppImage, opens | .exe, opens | .dmg, never opened |

Three values, deliberately:

- **run live** — somebody did this against a real server and wrote down what happened. The list
  further down says when.
- **built** — the code exists and has tests, and nobody has driven it by hand. It may well work.
  That is not the same claim and this file will not make it.
- **never run** — nobody has started the app on this operating system at all.

There is no Mac on this side of the project, so nothing in the macOS column is a claim. The code
is real and has tests; nobody has started the app on a Mac.

"Opens" is also the weaker word it looks like. The evidence for the AppImage and the `.exe` is a
`YULON_SMOKE_TEST=1` run that builds the window and exits; a person has used the app on Linux and
on Windows, but not from those artifacts.

## Installing a server

**Linux only.** Every entry in `yulon/catalog/catalog.json` declares `install.platforms:
["linux"]`, and
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

Three honest gaps:

- **LAN setup is only partly automatic, and on Arch it is not automatic at all.** The Networking
  tab opens the ports a friend on your LAN needs by driving the host's own firewall tool: `netsh`
  on Windows, and `ufw` or `firewalld` on Linux. A stock Arch desktop has neither —
  `detect_firewall()` answers `"none"` — so the plan carries the step as something for you to run
  by hand instead of running it. That is the intended behaviour and nothing about it is silent,
  but it is worth saying plainly, because a table that says Linux "run live" does not: on that
  distro "networking works" means the app told you what to type, not that it did it. (macOS is a
  third case again — its firewall is per-application and cannot express a port at all, so that one
  is always manual.)
- The **GM console** attaches over a pseudo-terminal, and the test for one is `os.openpty`,
  which CPython only provides on POSIX. (Windows 10 does have a pty of its own in ConPTY; nothing
  here uses it, so this is a limit of the code and not of the operating system.) On Windows the
  command box and Send button are disabled with the reason shown on the tab. Following the log
  needs no terminal and stays enabled.
- **Creating an account** does not go through the console at all. It writes the SRP6 registration
  row itself. That is the only transport that *can* work everywhere — and the only way at all to
  make the very first account, since the server's own SOAP interface needs an account before it
  will answer. It has been run live on Linux only.

## What has been run against a real server, rather than only tested

All of the below on Linux; none of it yet on Windows or macOS.

- **Installing a server** — Ubuntu 24.04 VM, 2026-08-21. Worth being exact: the install itself
  was driven by the CLI harness (then `python -m yulon.catalog.installer wow-wotlk --server-dir …`,
  today `python -m yulon.install_wiring wow-wotlk --server-dir …`),
  which built AzerothCore with playerbots from source and ended with all three containers up. The
  Catalog's Install button reaches the same `Installer`, but the button has not itself been the
  thing pressed on a fresh machine.
- **A human click-through of the management UI** — same VM, same day, against that running
  server.
- **Database backup and restore** — 2026-08-23, full round trip against a live AzerothCore install:
  four schemas found rather than the three the old guide hardcodes, a 292 MB world dump, the value
  in the dump read back after the restore, and a wrong confirmation token refused. Restore is the
  one action here that can destroy a server, and it is worth knowing exactly what it does: it loads
  a backup file over the live databases, table by table. Anything the backup contains replaces what
  is live. Anything it does NOT contain is left alone — so a restore is a merge, not a return to
  the state the backup was taken from, and a table added since (by a module, or by SQL pasted from
  a guide) is still there afterwards. Measured on Windows, 2026-08-23. It refuses while the
  worldserver is running, it shows you which databases it will touch and will not act until you
  confirm with a token that only that plan can produce, and it copies what it is about to
  overwrite first.
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
- **You need your own copy of the game.** Yu'lon never bundles, sells or distributes a game client,
  and it cannot get you one. Two details worth stating plainly, because the short version of this
  sentence is misleading: WoW TBC, Vanilla and Tortoise ask you to point at a client folder you
  already have, and **WotLK does not** — its installer never asks, because the server fetches
  AzerothCore's own client-data archive (maps, vmaps, mmaps, DBC) into a Docker volume. That is
  server-side data the server cannot run without. It is not the client you log in with, and you
  still have to supply that yourself.
- Read [`../DISCLAIMER.md`](../DISCLAIMER.md) before running a server.
- Releases, including the packaged builds: <https://github.com/DadsMmoLab/dads-mmo-lab/releases>.
- On launch it checks GitHub Releases and shows a banner if a newer version exists. It never
  replaces itself.

## Developers

Design docs, roadmap and checklist: [`../pyplan/`](../pyplan/README.md). Setup and conventions:
[`../pyplan/contribution.md`](../pyplan/contribution.md) and
[`../pyplan/style-guide.md`](../pyplan/style-guide.md).

### Linux prerequisites for running from source

`pip install -r requirements-dev.txt` gets you PySide6, and PySide6 does not bring the X libraries
its `xcb` platform plugin loads at runtime — the distro supplies those. On a stock Arch desktop six
of them are missing and the app aborts before it draws anything:

```bash
sudo pacman -S --needed xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image \
  xcb-util-renderutil libxkbcommon-x11
```

The Debian family (Ubuntu, Mint, Raspberry Pi OS) spells the same six:

```bash
sudo apt-get install libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-image0 \
  libxcb-render-util0 libxkbcommon-x11-0
```

**Qt names only one of the six, and it is usually the wrong one.** Whatever is actually missing,
the message reads `From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed to load the Qt xcb platform
plugin`. Installing that one package and getting the identical error again is the normal
experience. `QT_DEBUG_PLUGINS=1` prints the real unresolved soname, and `ldd` on the plugin
(`.venv/lib/python3*/site-packages/PySide6/Qt/plugins/platforms/libqxcb.so`) lists them all.

`libxkbcommon-x11` is the same library [`build/check-bundle-closure.sh`](build/check-bundle-closure.sh)
already catches missing from the shipped tarball. That gate reads the bundle, and a source checkout
bundles nothing — so it is one defect on two paths, and only one of them has a gate.
