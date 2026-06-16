# ⚙️ Dad's MMO Lab — Steam Deck Offline MMO Server Project

> *"The games we grew up with deserve to live forever. This project makes that possible on a single handheld device."*

**By [u/Kingspoken](https://reddit.com/u/Kingspoken)**

---

## 🎯 What Is This?

This is a collection of **step-by-step guides, Docker scripts, and automated installers** for running classic MMO private servers **completely offline** on a Steam Deck (or any Linux machine).

No subscription. No internet required. No servers getting shut down. Just you and the games you love — forever.

Every guide here is built around:
- ✅ **Open source emulators only** — no copyrighted assets, no game files distributed
- ✅ **Docker-based** — clean, repeatable, easy to remove
- ✅ **Steam Deck tested** — every setup verified on SteamOS
- ✅ **Dad-friendly** — written for people who love games, not just developers
- ✅ **One command install** — automated installers handle everything

---

## 🌍 The Story

I'm a dad who grew up on MMOs. Like a lot of you, I watched the servers for games I loved get shut down one by one. Nostalrius. Felmyst. Turtle WoW. Games that meant something — gone.

Then I got a Steam Deck.

And I started wondering: *what if I could bring them back? Offline. On a handheld. Forever.*

Turns out — for a lot of classic MMOs — you can. The emulator community has done incredible work over the years. This project is about packaging that work into something any dad (or mom, or kid) can actually use.

**This is not piracy.** We use open source server emulators. You supply your own legally obtained game clients. We just help you run them.

---

## 📺 Videos

**▶️ [Dad's MMO Lab — YouTube Channel](https://youtube.com/@DadsMmoLab)**

| Video | Description |
|-------|-------------|
| [It Still Lives](https://youtu.be/0XwLmaz3tao) | The proof of concept — WoW running offline on Steam Deck |
| [Full Install Guide](https://youtu.be/GVUVnngY93I) | Complete walkthrough from scratch using the auto-installer |

---

## ✅ Currently Working

| Game | Emulator | Bot Support | Status | Guide |
|------|----------|-------------|--------|-------|
| ⚔️ WoW Vanilla 1.12.1 | CMaNGOS Classic + Playerbots | Playerbots | ✅ Complete | [View Guide](./guides/wow-vanilla/HOWTO-WOW-VANILLA.md) |
| ⚔️ WoW The Burning Crusade 2.4.3 | CMaNGOS TBC + Playerbots | Playerbots | ✅ Complete | [View Guide](./guides/wow-tbc/WoW-TBC-HOWTO.md) |
| ⚔️ WoW Wrath of the Lich King 3.3.5a | AzerothCore + Playerbots | Playerbots | ✅ Complete | [View Guide](./guides/wow-wotlk/WoW-WotLK-HOWTO.md) |
| 🏃 RuneScape 2009 (SD) | 2009scape Singleplayer | Bots | ✅ Complete | [View Guide](./guides/runescape/HOWTO-RUNESCAPE.md) |
| 🏃 RuneScape 2009 (HD) | 2009scape + Saradomin Launcher | Bots | ✅ Complete | [View Guide](./guides/runescape/RuneScape-HD-HOWTO.md) |

---

## 🔥 In Progress

| Game | Emulator | Status |
|------|----------|--------|
| 🐉 Monster Hunter Frontier Z | Erupe CE | 🔨 In Progress |
| 🌿 Ragnarok Online | rAthena | 🔨 In Progress |
| 💎 Mu Online | OpenMU | 🔨 In Progress |
| 🏰 Dark Age of Camelot | OpenDAoC | 🔨 In Progress |

---

## 📋 Planned

| Game | Emulator | Notes |
|------|----------|-------|
| 🍄 MapleStory (v83 Pre-Big Bang) | Cosmic | Wife's pick 👩 |
| ⚡ PSO Blue Burst | newserv / Archon | Steam Deck proven |
| 🌌 Phantasy Star Universe | Clementine | Community server |
| 🧱 LEGO Universe | Darkflame Universe | For the kids |
| 🏨 Habbo Hotel | Havana | Browser client |
| ⚔️ Tibia | The Forgotten Server | |
| 🗡️ Cabal Online | Freya | |
| 🌟 Final Fantasy XI | LandSandBoat | High demand |
| 🌟 Final Fantasy XIV | Sapphire | High demand |
| 🏰 EverQuest 1 | EQEmu | |
| 🚀 Star Wars Galaxies | SWGEmu | |
| ⚔️ Lineage 2 | L2J / Mobius | |
| 🌐 Ultima Online | ModernUO + ClassicUO | Linux native client — no Proton needed |
| 🦸 City of Heroes | Homecoming | |
| 🏹 Asheron's Call | ACEmulator | |
| 🗝️ RuneScape Classic | OpenRSC | The original 2001 era |

---

## 📦 What's In This Repo

### WoW Vanilla 1.12 (`guides/wow-vanilla/`)

| File | What it does |
|------|-------------|
| `install-wow-vanilla.sh` | Full automated installer — one command does everything |
| `HOWTO-WOW-VANILLA.md` | Beginner install guide — zero Linux knowledge needed |

### WoW The Burning Crusade (`guides/wow-tbc/`)

| File | What it does |
|------|-------------|
| `install-wow-tbc.sh` | Full automated installer — one command does everything |
| `WoW-TBC-HOWTO.md` | Beginner install guide — zero Linux knowledge needed |

### WoW Wrath of the Lich King (`guides/wow-wotlk/`)

| File | What it does |
|------|-------------|
| `install-wow-wotlk.sh` | Full automated installer — one command does everything |
| `WoW-WotLK-HOWTO.md` | Beginner install guide — zero Linux knowledge needed |
| `manage-wow-modules.sh` | Enable / disable optional server modules in-place |

### RuneScape 2009 (`guides/runescape/`)

| File | What it does |
|------|-------------|
| `install-runescape.sh` | Full automated installer — one command does everything |
| `upgrade-runescape-hd.sh` | Adds the Saradomin HD client on top of an existing install |
| `HOWTO-RUNESCAPE.md` | Beginner install guide — zero Linux knowledge needed |
| `RuneScape-HD-HOWTO.md` | HD upgrade guide — Saradomin Launcher setup and first run |

---

## 🚀 Quick Start

Pick your game and run the installer. Each one handles everything automatically.

**WoW Vanilla 1.12:**
```bash
chmod +x install-wow-vanilla.sh && ./install-wow-vanilla.sh
```

**WoW The Burning Crusade:**
```bash
chmod +x install-wow-tbc.sh && ./install-wow-tbc.sh
```

**WoW Wrath of the Lich King:**
```bash
chmod +x install-wow-wotlk.sh && ./install-wow-wotlk.sh
```

**RuneScape 2009:**
```bash
chmod +x install-runescape.sh && ./install-runescape.sh
```

**RuneScape 2009 HD upgrade** *(run after the base installer):*
```bash
chmod +x upgrade-runescape-hd.sh && ./upgrade-runescape-hd.sh
```

Every WoW installer:
- ✅ Detects SteamOS and fixes the pacman keyring
- ✅ Installs Docker if needed
- ✅ Downloads and compiles the server
- ✅ Creates a default **admin / admin** account with GM Level 3
- ✅ Populates the world with bots
- ✅ Builds a Gaming Mode launcher

**New to Linux?** Read the HOWTO guide for your game first — every step explained in plain English, zero assumed knowledge.

---

## 🤖 Bot Options (WoW)

All three WoW installers ship with **Playerbots** pre-configured — bots that roam the world, fill dungeons and raids, and keep the economy alive so the game feels like a real server, not an empty shell.

| | Vanilla | TBC | WotLK |
|---|---|---|---|
| **Bot engine** | CMaNGOS Playerbots | CMaNGOS Playerbots | AzerothCore Playerbots |
| **Bot count** | 600–800 active | 600–800 active | 600–800 active |
| **Level range** | Endgame 50–60 | Outlands 57–70 | Full 1–80 (syncs to player) |
| **Auction House** | Stocked | Stocked | Stocked |
| **Install time** | ~30 min | ~30 min | ~30 min |

---

## 🎮 Gaming Mode Setup

Play entirely from Steam Gaming Mode — no Desktop Mode needed after setup:

1. Each installer creates a **Gaming Mode launcher** in your home folder
2. Add it to Steam as a Non-Steam game via Konsole:
   - **Target:** `/usr/bin/konsole`
   - **Launch Options:** `--hold -e bash ~/[game]-launcher.sh`
   - **Proton:** OFF (WoW uses Proton for the game client, not the server)
3. Launch from your Steam library
4. Wait for **"[GAME] IS READY!"**
5. Launch the game client from your library
6. Close the client → **server auto-shuts down**

Full setup instructions in each game's HOWTO guide.

---

## 🔧 After a SteamOS Update

If Docker stops working after a Steam Deck update:

```bash
chmod +x guides/Steam-Update-Fix/fix-after-update.sh
./guides/Steam-Update-Fix/fix-after-update.sh
```

Rebuilds the pacman keyring and reinstalls Docker automatically.

---

## 🛠️ How It Works

```
Steam Deck Gaming Mode
        │
        ▼
   Docker Container      ← Runs silently in background
   (Server Emulator)
        │
        ▼
  MySQL / MariaDB
   (Game Database)
        │
        ▼
Game Client via Proton
   → connects to localhost
   → completely offline
```

*(RuneScape runs without Docker — pure Java on Linux. No Proton needed.)*

---

## ⚠️ Legal & Ethical Notes

Dad's MMO Lab and WoW Unbound are not affiliated with, endorsed by, 
or associated with Blizzard Entertainment or any of its products.

We do not operate, host, or endorse any public or third-party servers 
— including any servers at unboundwow.com or similar domains. 
WoW Unbound is an open source educational project built on AzerothCore 
(GPL licensed), intended for personal use cases wholly dependant on end user choices

We do not claim ownership of any Blizzard intellectual property. 
No Blizzard game files are distributed by this project. Users must 
supply their own legally obtained game client.

Any public server operating under the "WoW Unbound" or "Unbound" 
name is doing so without our knowledge, authorization, or support.

This project:
- ✅ Uses **only open source server emulators**
- ✅ Does **not** distribute game assets, client files, or copyrighted content
- ✅ Requires you to **supply your own game client**
- ✅ Is intended for **personal, offline, single-player use**
- ❌ Does **not** help run public servers
- ❌ Does **not** support monetization of private servers

Huge credit to the communities that make this possible:
- **[AzerothCore](https://github.com/azerothcore/azerothcore-wotlk)** — the incredible open source WoW WotLK emulator
- **[CMaNGOS](https://github.com/cmangos)** — the Vanilla and TBC foundation
- **[2009scape](https://github.com/2009scape/2009scape)** — the RuneScape 2009 emulator
- **[Saradomin Launcher](https://flathub.org/apps/org._2009scape.Launcher)** — the HD experimental client
- Every emulator project linked in our guides

Go give them a star. They deserve it.

> *"This is preservation, not piracy."*

---

## 🤝 Contributing

Found a bug? Got a game working that's not listed? PRs are welcome!

Please read [CONTRIBUTING.md](./CONTRIBUTING.md) before submitting.

Special thanks to the community testers who have helped improve these installers through real-world bug reports. You know who you are. 🙏

---

## 💬 Community

- **Reddit:** [u/Kingspoken](https://reddit.com/u/Kingspoken)
- **Reddit Thread:** [The post that started it all](https://www.reddit.com/r/SteamDeck/s/A8SvXK0eOc)
- **YouTube:** [youtube.com/@DadsMmoLab](https://youtube.com/@DadsMmoLab)

---

## ☕ Support the Project

This project is free and always will be.

If it helped you relive something you thought was gone forever — a coffee goes a long way toward keeping this going and eventually making it a full time mission:

**[☕ ko-fi.com/dadsmmolab](https://ko-fi.com/dadsmmolab)**

Or just:
- ⭐ **Star this repo** — helps more people find it
- 📢 **Share it** with other dads who miss their old games
- 💬 **Comment** on the YouTube videos

---

## 📜 License

Installer scripts and guides in this repo are released under the 
[MIT License](./LICENSE).

WoW Unbound server modifications are released under the 
[GNU Affero General Public License v3.0](./LICENSE-AGPL), 
consistent with AzerothCore's licensing.

Game emulators linked here are subject to their own licenses. 
Game assets belong to their respective owners and are NOT included here.

---

*Built with love by a dad who just wanted to play WoW on the couch without a subscription.*

*And then things got out of hand.* 😄

*We're just getting started.* ⚔️
