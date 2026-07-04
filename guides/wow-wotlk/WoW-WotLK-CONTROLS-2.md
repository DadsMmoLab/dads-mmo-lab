# 🖥️ Server Controls — Part 2
## GM Console, Commands, Troubleshooting & Terminal Basics

> **⬅️ [Back to Part 1 — Server Management & Account Creation](./WoW-WotLK-CONTROLS-1.md)**
>
> 📍 **Landing here directly?** Part 1 has your platform's terminal setup (Steam Deck, Ubuntu, Fedora, Windows/WSL2). [Check it out first.](./WoW-WotLK-CONTROLS-1.md#-what-platform-are-you-on)

---

## 🖥️ The GM Console — Full Guide

The GM Console is your direct line to the WoW worldserver.
Think of it like texting the game engine directly. You can
create accounts, run commands and check server status —
all without being logged into the game.

---

### Opening the GM Console

Make sure your server is running first, then run this in your terminal:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

If it worked, your prompt changes to `AC>`. That means you're talking directly to the server console.

> 💡 **To paste in your terminal:** right-click → Paste, or press **Ctrl+Shift+V**

---

### The Most Important Thing — How to Exit Safely

**NEVER press Ctrl+C inside the GM console.**

Ctrl+C kills the worldserver completely — everyone gets
disconnected and you will have to restart.

**The correct way to exit:**
1. Press **Ctrl+P**
2. Then immediately press **Ctrl+Q**

This detaches you from the console safely while leaving the
server running perfectly.

> Memory trick: P for Pause, Q for Quit the console.
> Two keystrokes, always in that order.

---

### Common GM Console Commands

```
account create USERNAME PASSWORD
account set gmlevel USERNAME 3 -1
account onlinelist
account delete USERNAME
reload config
server info
server shutdown 10
```

---

### If the Console Will Not Accept Input

Sometimes the console appears stuck or ignores typing.
This usually means it is printing log messages over your input.

Press Enter once to get a clean line then type your command.
The command still registers even if you cannot see it clearly.

---

## 🎮 Useful In-Game GM Commands

Type these in the WoW chat box while playing:

### Teleport Anywhere
```
.tele stormwind
.tele orgrimmar
.tele dalaran
.tele ironforge
```

### Level Up
```
.levelup
.levelup 10
```

### Modify Speed
```
.modify speed 3
```

1 is normal, 3 is fast, 10 is very fast.

### Give Yourself Gold
```
.modify money 999999
```

### Spawn Any Item
```
.additem ITEM_ID
```

Look up item IDs on wowhead.com

### Change Time of Day
```
.time set 12 0
```

Format is `HH MM` — for example `.time set 6 30` for 6:30 AM or `.time set 20 0` for 8 PM.

### See All Commands
```
.commands
```

---

## 🤖 NPCBot Commands

These work if you are running the **NPCBots** server version.

> **Not sure if you're on NPCBots?** Your server folder will be `wow-server-npcbots`, or your installer/launcher mentioned NPCBots.

### Spawn a Bot Near You
```
.npcbot spawn CLASS_ID
```

Class IDs:
```
1  Warrior    2  Paladin
3  Hunter     4  Rogue
5  Priest     6  Death Knight
7  Shaman     8  Mage
9  Warlock    11 Druid
```

### Add a Bot to Your Party
Target the bot in-game then type:
```
.npcbot add
```

### Remove a Bot
Target the bot then type:
```
.npcbot remove
```

### Set Bot Role
```
.npcbot set role tank
.npcbot set role heal
.npcbot set role dps
```

### Bot Movement
```
.npcbot set follow
.npcbot set standstill
```

### List Your Bots
```
.npcbot list
```

---

## 🔧 Troubleshooting Common Problems

### Cannot Connect to the Server

Check the server is running:
```bash
docker ps
```

If you do not see the containers start the server:
```bash
cd ~/wow-server && docker compose up -d
# Replace wow-server with wow-server-npcbots or wow-server-playerbots if that's your install
```

Check your `realmlist.wtf` — the value depends on your setup:
- **WoW is on the same machine as the server:** use `set realmlist 127.0.0.1`
- **WoW is on a different PC on the same network (LAN):** use your server's local IP address
- **Playing over the internet:** use your server's public IP address

See the [Networking Guide](./WoW-Wotlk-NETWORKING.md) for LAN and internet setup.

Give it time — first launch takes 5–15 minutes.

---

### Login Says Information Not Valid

Create the account manually via the GM console:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

Wait for the `AC>` prompt, then type (replace `USERNAME` and `PASSWORD` with your own):
```
account create USERNAME PASSWORD
account set gmlevel USERNAME 3 -1
```

Exit with Ctrl+P then Ctrl+Q.

---

### The Server Will Not Start

First, make sure you're in your server folder:
```bash
cd ~/wow-server          # or wow-server-npcbots / wow-server-playerbots
```

Check what is wrong:
```bash
docker compose logs --tail 50
```

Most common fix — remove old containers and restart:
```bash
docker compose down && docker compose up -d
```

---

### Docker Stopped Working After a System Update

**Steam Deck (SteamOS):** Run the fix script included in this repo. First, find where you cloned the repo (run `ls ~` to look for a `dads-mmo-lab` folder), then:
```bash
cd ~/dads-mmo-lab/guides/Steam-Update-Fix
chmod +x fix-after-update.sh && ./fix-after-update.sh
```
> If you don't have the repo cloned, download it: `git clone https://github.com/DadsMmoLab/dads-mmo-lab ~/dads-mmo-lab`

**Ubuntu / Debian:** Docker Engine is managed by apt — updates usually don't break it. If Docker won't start:
```bash
sudo systemctl enable --now docker
```

**Fedora:** Same as above but with dnf:
```bash
sudo systemctl enable --now docker
```

**Windows / WSL2:** If Docker Desktop updated itself and containers won't start, restart Docker Desktop from the system tray (right-click the whale icon → Restart). If WSL2 itself updated, run `wsl --shutdown` in PowerShell then reopen your WSL2 terminal.

---

### I Pressed Ctrl+C in the GM Console

The worldserver container may have stopped. Restart it using docker compose from your server folder:

```bash
cd ~/wow-server       # or wow-server-npcbots / wow-server-playerbots
docker compose up -d
```

Remember — always exit the GM console with Ctrl+P then Ctrl+Q.

---

## 📚 A Little Bit of Terminal

Here are a few terminal basics that will make everything easier.
Each one takes 30 seconds to learn.

> 🪟 **Windows users:** These commands run inside your **WSL2 terminal** (where you see `user@archlinux ~ $`). They are Linux commands — they don't work in PowerShell.

### Navigating Folders
```bash
cd ~/wow-server      # go into wow-server folder
cd ~                 # go back to home folder
cd ..                # go up one folder
ls                   # list what is in the current folder
pwd                  # show where you currently are
```

> In Linux (and WSL2), `~` means your home folder. On a Steam Deck that is `/home/deck`. On WSL2 Arch it is `/home/yourusername`.

### Reading Files
```bash
cat filename.txt     # print the whole file
tail -20 filename    # print the last 20 lines
```

### Stopping a Running Command

**Ctrl+C** stops whatever is running in the terminal.

Never press Ctrl+C inside the GM console though. Use
Ctrl+P then Ctrl+Q instead.

### Running a Script
```bash
chmod +x script.sh   # give it permission to run
./script.sh          # run it
```

### The Pipe Symbol

The `|` symbol sends the output of one command to another:

```bash
docker logs ac-worldserver | grep "error"
```

This gets the logs AND searches them for the word error.
Very useful for finding problems!

---

## 🎓 What You Have Learned

If you have read both parts of this guide you now know:

- What Docker containers are and why we use them
- How to start and stop your server safely
- How to create and manage accounts
- How to use the GM console safely
- How to use in-game GM commands
- How to manage NPCBots
- How to diagnose and fix common problems
- Basic terminal navigation

That is genuinely more than most people know.
And you learned it by setting up a WoW server. Not bad! 😄

---

## 📺 Video Guides

Full video tutorials at:
**[youtube.com/@DadsMmoLab](https://youtube.com/@DadsMmoLab)**

## 📦 More Guides

- [WoW-WotLK-HOWTO.md](./WoW-WotLK-HOWTO.md) — Full install guide (Steam Deck)
- [WoW-WotLK-CREATE-ACCOUNTS.md](./WoW-WotLK-CREATE-ACCOUNTS.md) — Quick account creation reference
- [WoW-Wotlk-NETWORKING.md](./WoW-Wotlk-NETWORKING.md) — Let friends connect (LAN & internet), Windows/WSL2 setup

---

*Part of the Dad's MMO Lab project — offline MMO servers
on Steam Deck, free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
