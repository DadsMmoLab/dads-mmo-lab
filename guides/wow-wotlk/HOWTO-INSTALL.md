# ⚔️ How to Install — WoW Server Setup Wizard

> **Zero Linux knowledge required.**
> The wizard handles everything — you just answer a few questions.

---

## 📋 Before You Start

Make sure you have:
- ✅ A Steam Deck (or any Linux machine)
- ✅ At least **15GB** free storage (30GB+ for Playerbots)
- ✅ A WoW 3.3.5a client installed and working in Steam
- ✅ Internet connection for the initial download
- ✅ Your Steam Deck **plugged in** if choosing Playerbots

---

## 🚀 Quick Start — One Command

Open **Konsole** in Desktop Mode and run:

```bash
cd ~/Downloads && chmod +x install-wow.sh && ./install-wow.sh
```

That's it. The wizard takes over from here.

---

## 🗺️ What the Wizard Does

The wizard walks you through **6 steps**:

---

### Step 1 — Choose Your Experience

```
1) Base WoW
   Clean server, just you and the world
   Great for Solocraft! Scale dungeons to 1 player
   Fastest install (~30 minutes)

2) NPCBots
   Hire AI companions to join your party
   Perfect for dungeons, raids and leveling
   Fast install (~10 mins) OR compile (~2-4 hours)

3) Playerbots
   Hundreds of AI players roam the world freely
   Quest, dungeon, raid, Azeroth feels truly alive
   Requires compilation (2-4 hours)
```

**Which should I choose?**

| I want... | Choose |
|-----------|--------|
| A clean solo experience | Base WoW |
| A healer or tank for my dungeons | NPCBots |
| A living world full of players | Playerbots |
| To scale dungeons solo with no bots | Base WoW + Solocraft |

---

### Step 2 — Choose Your Modules

After choosing your server the wizard offers compatible add-ons:

| Module | What it does | Available for |
|--------|-------------|---------------|
| AH Bot | Populates all 3 Auction Houses | All |
| Individual Progression | Vanilla then TBC then WotLK | All |
| Dungeon Master | Procedural roguelike challenges | All |
| Solocraft | Scales dungeons to 1 player | Base WoW |
| Wandering Bots | 500 bots roam the open world | NPCBots |

Just answer y or n for each one!

---

### Step 3 — Summary and Confirm

Before anything downloads the wizard shows you exactly what it will build and how long it will take. You confirm before it starts.

---

### Step 4 — Install

Sit back. The wizard:
- Installs Docker automatically if not already installed
- Downloads or compiles your server
- Sets up all your chosen modules
- Starts everything up

**Base WoW:** About 30 minutes

**NPCBots (pre-built):** About 10 minutes

**NPCBots or Playerbots (compile):** 2-4 hours. Leave your Steam Deck plugged in!

> Check compile progress in another Konsole window:
> ```bash
> tail -f ~/playerbots-build.log
> ```

---

### Step 5 — Create Your Account

After the server starts the wizard shows you exactly how to create your account via the GM console. It takes about 60 seconds.

Open a **new Konsole window** and run:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

Then type these two commands — replace USERNAME and PASSWORD with whatever you want:

```
account create USERNAME PASSWORD PASSWORD
account set gmlevel USERNAME 3 -1
```

Then exit safely with **Ctrl+P then Ctrl+Q** — never Ctrl+C!

> You can create as many accounts as you need — one per family
> member, one for testing, whatever you like. Just repeat the
> two commands above for each one.
>
> See HOWTO-CREATE-ACCOUNTS.md for the full guide.

---

### Step 6 — Gaming Mode Setup

The wizard creates a launcher for your server type and shows you the exact Steam setup instructions including the full path to your launcher file.

---

## 🎮 Setting Up Gaming Mode

After the wizard finishes it shows you the exact launcher path. Here is the setup:

### Add to Steam

1. Open Steam in Desktop Mode
2. Click Games then Add a Non-Steam Game
3. Click Browse and navigate to `/usr/bin/`
4. Select `konsole` and click Add Selected Programs
5. Find konsole in your library, right-click, Properties
6. Rename it to match your server, for example `WoW Server`
7. In Launch Options paste the command the wizard showed you

**Base WoW:**
```
--hold -e bash /home/deck/wow-gaming-mode.sh
```

**NPCBots:**
```
--hold -e bash /home/deck/wow-npcbots-launcher.sh
```

**Playerbots:**
```
--hold -e bash /home/deck/wow-playerbots-launcher.sh
```

8. Under Compatibility, do NOT enable Proton

> Proton causes a blank screen on shell scripts. Leave it OFF.

---

### How Gaming Mode Works

1. Launch your server from the Steam library
2. Watch the dots and wait for AZEROTH IS READY
3. Press Steam button and launch WoW
4. Login with your username and password
5. Play your session
6. Close WoW and the server shuts down automatically

Start to playing: Under 1 minute after first launch. Exit: Automatic.

---

## 🔧 Set Your WoW Realmlist

Before logging in for the first time:

1. Open your WoW client folder
2. Find and open `realmlist.wtf`
3. Make sure it contains exactly:
```
set realmlist 127.0.0.1
```
4. Save and close

---

## Frequently Asked Questions

**Can I have multiple server versions installed at once?**

Yes! Each installs to its own folder and they never conflict. Just run one at a time since they share the same ports.

```
Base WoW     ~/wow-server
NPCBots      ~/wow-server-npcbots
Playerbots   ~/wow-server-playerbots
```

---

**The server takes forever on first launch. Is that normal?**

Yes! First launch builds the entire database which takes 5-15 minutes. After the first time it starts in about 30 seconds. The dots mean it is working.

---

**Login says the information is not valid**

Create the account manually. Open Konsole and run:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

Then type:
```
account create admin admin admin
account set gmlevel admin 3 -1
```

Then press Ctrl+P then Ctrl+Q to exit.

---

**Docker says Unsupported distribution SteamOS**

You have an old version of the installer. Re-download `install-wow.sh` from GitHub and run it fresh. The new wizard handles SteamOS automatically.

---

**SteamOS updated and Docker is broken**

Run the fix script:
```bash
chmod +x fix-after-update.sh && ./fix-after-update.sh
```

See HOWTO-FIX-AFTER-UPDATE.md for details.

---

## What Gets Installed Where

```
~/wow-server/                  Base WoW server files
~/wow-server-npcbots/          NPCBots server files
~/wow-server-playerbots/       Playerbots server files

~/wow-gaming-mode.sh           Base WoW launcher
~/wow-npcbots-launcher.sh      NPCBots launcher
~/wow-playerbots-launcher.sh   Playerbots launcher
```

Each server folder contains MY_ACCOUNTS.txt with your login details and full Gaming Mode setup instructions.

---

## What is Next?

- Want to create more accounts? See HOWTO-CREATE-ACCOUNTS.md
- Want to uninstall? See HOWTO-UNINSTALL.md
- Want to manage your server? See HOWTO-DESKTOP-CONTROLS-1.md
- Docker broken after an update? See HOWTO-FIX-AFTER-UPDATE.md

---

## Video Guide

Full video walkthroughs at:
**youtube.com/@DadsMmoLab**

## GitHub

Everything is free at:
**github.com/DadsMmoLab/dads-mmo-lab**

---

*Part of the Dad's MMO Lab project — offline MMO servers on Steam Deck, free forever.*
