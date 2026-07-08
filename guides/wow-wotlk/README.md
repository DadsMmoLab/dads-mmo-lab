# 🧙 WoW WotLK — Dad's MMO Lab

Run a fully offline **World of Warcraft: Wrath of the Lich King (3.3.5a)** private server on your own hardware — no subscription, no internet required after setup.

**Supported platforms:** Steam Deck (SteamOS) · Ubuntu / Debian / Linux Mint · Fedora · Windows 10/11 (WSL2)

All servers run in **Docker** via [AzerothCore's official Docker compose setup](https://www.azerothcore.org/wiki/install-with-docker). We do not manage nor are we affiliated with AzerothCore — we simply provide a fully automated installer and management scripts for the AzerothCore WotLK + Playerbots server branch.

---

## 🚀 Where Do I Start?

| My platform | Start here |
|---|---|
| **Steam Deck and Machine** | [WoW-WotLK-HOWTO.md](./WoW-WotLK-HOWTO.md) — full install walkthrough with automated installer |
| **Linux (Debian, Fedora, Bazzite, Ubuntu, PopOS!, Arch Linux, Mint)** | [WoW-WotLK-HOWTO.md](./WoW-WotLK-HOWTO.md) — server setup, account creation, daily use commands |
| **Windows 10/11** | [DML-Windows-HOWTO.md](../DML-Windows/DML-Windows-HOWTO.md) — full Windows installer walkthrough |
| **Want multiplayer (LAN or internet)?** | [WoW-Wotlk-NETWORKING.md](./WoW-Wotlk-NETWORKING.md) — firewall, port forwarding, connecting friends |

---

## 📚 Guides

### 🧰 Install & First-Time Setup

#### [WoW-WotLK-HOWTO.md](./WoW-WotLK-HOWTO.md)
**Steam Deck / Arch Linux**

The full walkthrough for installing AzerothCore WotLK + Playerbots and managing your server. Steam Deck users get the fully automated installer script — Linux users will find the server management, account creation, and networking sections all apply directly.

- **Steam Deck:** Runs the `install-wow-wotlk.sh` script — fully automated, hands-off build
- Installs **AzerothCore WotLK** + **mod-playerbots** (1,600–2,000 AI players), compiled from source
- Sets up a one-button **Gaming Mode launcher** in your Steam library (Steam Deck)
- Walks through account creation, realmlist setup, and daily use
- Includes the **Managing Your Server** section covering both `wow-manage.sh` and The Lab GUI
- Covers troubleshooting, bot settings, and force-rebuild

**Fedora / Bazzite**

Go through the full install guide, but skip the Steam Deck-specific sections. The server management, account creation, and networking sections all apply directly. Use `install-wow-wotlk-fedora.sh` to install AzerothCore WotLK + Playerbots on Fedora systems.

**Ubuntu / Debian / PopOS! / Linux Mint**

Go through the full install guide, but skip the Steam Deck-specific sections. The server management, account creation, and networking sections all apply directly. Use `install-wow-wotlk-ubuntu.sh` to install AzerothCore WotLK + Playerbots on Ubuntu/Debian/PopOS!/Linux Mint systems.

**Windows users:** See [DML-Windows-HOWTO.md](../DML-Windows/DML-Windows-HOWTO.md) for the Windows installer walkthrough.

---

#### [WoW-Wotlk-NETWORKING.md](./WoW-Wotlk-NETWORKING.md)
**Platform: All (Steam Deck · Linux · Windows WSL2)**

Everything you need to let other players connect to your server — on the same Wi-Fi (LAN) or over the internet.

- **Part 1–2:** Open a terminal and start your server
- **Part 3:** Find your local and public IP addresses
- **Part 4:** Open firewall ports — platform-specific blocks for SteamOS, Fedora, Ubuntu/Debian, and Windows
- **WSL2 portproxy:** How to forward ports from Windows into WSL2 (only needed if Docker binds to 127.0.0.1)
- **6A — LAN setup:** Same Wi-Fi, no router changes needed
- **6B — Internet play:** Port forwarding, static IP reservation, DuckDNS for dynamic IPs
- **Troubleshooting:** Common LAN/internet connection problems and fixes

---

### 🖥️ Daily Use — Server Controls

#### [WoW-WotLK-CONTROLS-1.md](./WoW-WotLK-CONTROLS-1.md)
**Platform: All (Steam Deck · Ubuntu/Debian · Fedora · Windows WSL2)**

Part 1 of the server management reference. If you need to start, stop, or manage accounts — this is the guide.

- Platform table — how to open a terminal on each OS
- Start / stop / restart commands for all three server variants (Base WoW, NPCBots, Playerbots)
- What Docker is and how it works (plain English)
- Container names and how to find them
- Checking server status and reading live logs
- Account creation, GM levels, and password management
- Switching between server versions
- Database backup and restore

➡️ Continue to Part 2 after reading this.

---

#### [WoW-WotLK-CONTROLS-2.md](./WoW-WotLK-CONTROLS-2.md)
**Platform: All (Steam Deck · Ubuntu/Debian · Fedora · Windows WSL2)**

Part 2 of the server management reference. The GM console, in-game commands, and troubleshooting.

- Full GM console guide — how to open it, how to exit safely, why Ctrl+C is dangerous
- Common GM console commands (account create, delete, gmlevel)
- In-game GM commands: teleport, level up, speed, gold, spawn items, time of day
- **NPCBots commands** — spawn, party, roles, movement (NPCBots server variant only)
- Troubleshooting: can't connect, won't start, Docker stopped after update, accidentally pressed Ctrl+C
- **Terminal basics** — a short primer on navigating folders, reading files, running scripts

---

#### [WoW-WotLK-CREATE-ACCOUNTS.md](./WoW-WotLK-CREATE-ACCOUNTS.md)
**Platform: All (Steam Deck · Ubuntu/Debian · Fedora · Windows WSL2)**

A quick one-page reference for creating player accounts. Useful to bookmark and share with family members who need their own login.

- Platform table for opening a terminal
- Open the GM console, create an account, set GM level, exit safely
- Copy-paste ready command block
- Troubleshooting: account already exists, login not valid, password reset, server not found

---

## 🛠️ Scripts & Tools

### `install-wow-wotlk.sh` — Automated Installer
**Platform: Steam Deck**

The installer script that `WoW-WotLK-HOWTO.md` walks you through. It automates the entire AzerothCore + Playerbots server build.

**Version:** 1.2.0 | **What it does:**
1. Installs Docker and Git if not already present
2. Shows a build summary and asks you to confirm
3. Clones and compiles AzerothCore WotLK + mod-playerbots (~2–4 hours)
4. Waits for the worldserver to initialize, then guides you through account creation
5. Creates the Gaming Mode launcher script and reference card

Download it from this repo and run it on your Steam Deck — full instructions in [WoW-WotLK-HOWTO.md](./WoW-WotLK-HOWTO.md).

```bash
chmod +x ~/Downloads/install-wow-wotlk.sh
~/Downloads/install-wow-wotlk.sh
```

---

### `wow-manage.sh` — Interactive Server Manager
**Platform: Steam Deck / Linux**

A full-screen, menu-driven terminal script for managing your server after it's installed — no need to remember Docker commands.

**Version:** 2.2.1 "ALE House Edition"

**Download and run:**
```bash
curl -o ~/Downloads/wow-manage.sh https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/wow-manage.sh
chmod +x ~/Downloads/wow-manage.sh
~/Downloads/wow-manage.sh
```

**Menus:**

| Menu | What it does |
|---|---|
| **Server Controls** | Start, stop, restart, status, live logs, attach to console |
| **Server Modifications** | Add/remove AzerothCore modules, ALE Lua mods, SQL mods |
| **Configurations** | Set WoW client folder, configure AH Bot and ALE, rebuild the worldserver |

Navigate with the numbers shown on screen. Press Enter with no input to go back.

---

### The Lab — GUI App
**Platform: Steam Deck / Linux (AppImage)**

A full graphical app for managing your server without a terminal — built for Gaming Mode and couch play.

**Download:** [github.com/0xVe1L/the-lab](https://github.com/0xVe1L/the-lab) — grab `TheLab.AppImage` from the latest release.

**Features:**
- Start / stop / restart with a live, readable server console
- **My Party** — build a 5-man bot group: pick class, role, spec, and level
- **Item database** — search any item and mail it to your character
- **Teleport** to any location or map coordinates
- **Module management** — toggle AzerothCore modules and tune settings in-app
- **Steam integration** — adds the server and WoW client to your Steam library automatically
- Auto-shuts down when you close WoW

> **Already installed from this guide?** The Lab detects existing Dad's MMO Lab servers and migrates them in — your characters and data stay untouched.

---

## 🧪 ALE-Kegs — Lua Mods

**Folder:** [`ALE-Kegs/`](./ALE-Kegs/)

ALE-Kegs are Eluna Lua mods adapted by Baerthe specifically for this WotLK installation, using the [AzerothCore Lua Engine (ALE)](https://github.com/azerothcore/mod-eluna). They are included in the repo for use with `wow-manage.sh`'s mod installer, but can be used independently.

> These mods are tuned for this specific install. They may need adjustments to work in other AzerothCore setups.

### BlackMarketAuctionHouse
**Folder:** [`ALE-Kegs/BlackMarketAuctionHouse/`](./ALE-Kegs/BlackMarketAuctionHouse/)

Adds a Black Market Auction House to WotLK — a feature originally introduced in Mists of Pandaria. Rare items and gear appear for sale at steep prices, creating a gold sink and rare gear economy on your server.

Contents:
- `BMAH.lua` — the mod (ALE version)
- `BMAH_original.lua` — the unmodified original (reference/fallback)
- `Client Files/` — client-side AddOn files (`BlackMarketUI`)
- `sql/` — database SQL files required for the mod to function

---

### SeasonOfDiscovery
**Folder:** [`ALE-Kegs/SeasonOfDiscovery/`](./ALE-Kegs/SeasonOfDiscovery/)

Adds Season of Discovery-inspired content and mechanics to your WotLK server — bringing SoD's discovery experience to Wrath of the Lich King.

Contents:
- `SOD.lua` — the mod (ALE version)
- `SOD_original.lua` — the unmodified original (reference/fallback)
- `Client Files/` — client-side files (Interface icons and locale data)
- `Server Files/` — server-side DBC files

---

## 📦 legacy/

**Folder:** [`legacy/`](./legacy/)

This folder contains the **old installer scripts** that were used before `install-wow-wotlk.sh` replaced them. They are kept for reference only — **do not use these for a fresh install.**

| File | What it was |
|---|---|
| `install.sh` | Original combined installer |
| `install-wow.sh` | Base WoW (no bots) installer |
| `install-npcbots.sh` | NPCBots server installer |
| `wow-gaming-mode.sh` | Old Gaming Mode setup script |
| `wow-playerbots-launcher.sh` | Old Playerbots launcher |
| `wow-npcbots-launcher.sh` | Old NPCBots launcher |
| `docker-compose.yml` | Old compose file (uses pre-built images, not compiled) |

> **The legacy guides used pre-built AzerothCore Docker images** (pulled from Docker Hub) rather than compiling from source. The current installer compiles from source for full mod support including Playerbots.

---

## 🔗 External Links

- [AzerothCore](https://github.com/azerothcore/azerothcore-wotlk) — the open-source WoW WotLK server
- [AzerothCore Docker Install Guide](https://www.azerothcore.org/wiki/install-with-docker) — official Docker setup docs
- [mod-playerbots](https://github.com/mod-playerbots/mod-playerbots) — AI player bots module
- [The Lab GUI](https://github.com/0xVe1L/the-lab) — graphical server manager
- [DuckDNS](https://www.duckdns.org/) — free dynamic DNS (for stable public hosting)
- [portforward.com](https://portforward.com/) — router port forwarding help by model

---

*Part of the Dad's MMO Lab project — free forever.*

**[youtube.com/@DadsMmoLab](https://youtube.com/@DadsMmoLab)**
**[github.com/DadsMmoLab/dads-mmo-lab](https://github.com/DadsMmoLab/dads-mmo-lab)**