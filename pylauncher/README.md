# Yu'lon — the Dad's MMO Lab launcher

One app that installs and runs your own private MMO server with buttons instead of terminals.
You pick a game from the Catalog, click **Install**, and the app does the rest: it fetches the
open-source server software, builds it, starts it, and gives you a control panel (start/stop,
live console, accounts, modules, networking for friends).

## What the app installs for you — and what that means under the hood

The game servers run as **Docker** containers, and Docker needs a Linux kernel. Yu'lon hides
that for you, but it does not remove it — here is exactly what happens on each system:

| You are on | What Yu'lon sets up silently | What that really is |
|---|---|---|
| **Linux** (Steam Deck, Ubuntu, Fedora, Arch, …) | Docker Engine via your distro's package manager (SteamOS is briefly unlocked with `steamos-readonly disable` and relocked) | Native containers — no virtual machine |
| **Windows 10/11** | WSL2, then Docker Desktop (WSL2 backend) | A small Linux VM managed by Windows. **WSL is hidden, not removed.** A reboot is needed once after WSL2 is enabled, and Yu'lon tells you so. |
| **macOS** | Docker Desktop | Docker Desktop runs the containers in its own Linux VM |

Things the app **cannot** do silently and will ask you for, in plain words:

- **A password** when your system needs one to install software (Linux `sudo`, Windows UAC, macOS).
- **A reboot** after enabling WSL2 on Windows.
- **Router settings** for internet play (a DHCP reservation and TCP port forwarding) — the app
  detects and explains them; it cannot log in to your router.
- **Your own game client.** Yu'lon never downloads, bundles or distributes game clients or game
  files (see the project README: open-source emulators only). You point the app at a client you
  already own.

## Where things live

- **App state and logs:** `~/.local/share/yulon/` (Linux), `%APPDATA%\yulon\` (Windows),
  `~/Library/Application Support/yulon/` (macOS).
- **Your server files:** wherever you chose at install time (default `~/wow-server-playerbots`
  for WotLK). Never inside the app folder.

## Unsigned builds

The downloads are not code-signed yet. Windows SmartScreen and macOS Gatekeeper will warn the
first time; choose "Run anyway" / right-click → Open. This is a known v1 trade-off.

## Updates

On launch, Yu'lon checks GitHub Releases and shows a banner with a download link if a newer
version exists. It never replaces itself; you download the new file.

## Developers

See [`development.md`](development.md) and the design docs in [`../pyplan/`](../pyplan/).
