# 🖥️ Server Controls — Part 1
## Server Management & Account Creation

> **➡️ [Part 2 — GM Console, Bot Commands, Troubleshooting & Terminal Basics](./WoW-WotLK-CONTROLS-2.md)**

---

## 📍 What Platform Are You On?

These guides work on all of the following. Follow the terminal instructions for your system:

| Platform | How to open a terminal |
|---|---|
| **Steam Deck (SteamOS)** | Press Steam → Power → **Switch to Desktop** → open **Konsole** from the taskbar |
| **Ubuntu / Debian / Linux Mint** | Press `Ctrl+Alt+T` or search **Terminal** in your app menu |
| **Fedora** | Press `Ctrl+Alt+T` or open **Activities** → search **Terminal** |
| **Windows 10/11 (WSL2)** | Start → search **Windows Terminal** → open it → type `wsl` → Enter |

> 🪟 **Windows users:** If `wsl` says "not found" or "not recognized", WSL2 is not installed. [Follow Microsoft's WSL2 install guide](https://learn.microsoft.com/en-us/windows/wsl/install) before continuing. All `docker` and server commands must be run inside the **WSL2 terminal** (where you see `user@archlinux ~ $`), not in regular PowerShell. See the [Networking Guide](./WoW-Wotlk-NETWORKING.md) for WSL2-specific setup.

> 💡 **To paste in your terminal:** right-click → Paste, or press **Ctrl+Shift+V** (works on Linux and Windows Terminal).

---

## ⚡ The Only Commands You Need

Copy paste. That's it.

**Base WoW:**
```bash
# Start
cd ~/wow-server && docker compose up -d

# Stop
cd ~/wow-server && docker compose down
```

**NPCBots:**
```bash
# Start
cd ~/wow-server-npcbots && docker compose up -d

# Stop
cd ~/wow-server-npcbots && docker compose down
```

**Playerbots:**
```bash
# Start
cd ~/wow-server-playerbots && docker compose up -d

# Stop
cd ~/wow-server-playerbots && docker compose down
```

> ⚠️ Only run ONE server at a time — they share the same
> ports. Stop one before starting another.

> 💡 **Not sure which folder name to use?** Run `ls ~` in your terminal and use the folder name you actually see — `wow-server`, `wow-server-npcbots`, or `wow-server-playerbots`.

> 💡 **Steam Deck only:** Gaming Mode handles start and stop automatically.
> The commands above are only needed when managing the server manually.

---

## 🧠 Understanding What's Actually Happening

Before diving in — here's what your WoW server actually is.
This will make everything click.

### What is Docker?

Think of Docker like a lunchbox. Inside is everything your
WoW server needs — the database, the game server, all the
settings. Docker keeps it all contained and neat so it doesn't
interfere with the rest of your system.

When you run `docker compose up` you are opening the lunchbox.
When you run `docker compose down` you are closing it safely.

> **Don't have Docker yet?**
> - **Ubuntu / Debian:** `sudo apt install docker.io docker-compose-plugin && sudo systemctl enable --now docker`
> - **Fedora:** `sudo dnf install docker docker-compose-plugin && sudo systemctl enable --now docker`
> - **Windows (WSL2):** Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) and enable WSL2 integration in its settings
> - **Steam Deck:** Follow the [WoW WotLK HOWTO guide](./WoW-WotLK-HOWTO.md) — it installs Docker and the server for you
> - **Ubuntu / Debian / Fedora users:** After installing Docker, run `sudo usermod -aG docker $USER` then log out and back in so you can run `docker` without `sudo`.

### What are Containers?

Your WoW server is actually THREE separate programs running
at the same time:

| Container | What it does |
|-----------|-------------|
| Database | Stores everything — characters, items, quests |
| Authserver | Handles login — checks username and password |
| Worldserver | The actual game world — NPCs, quests, combat |

They work together. If the database is not running nothing
else works. That is why we always use `docker compose` — it
starts all three in the right order automatically.

### Which Container is Which?

All server versions use the same container names:

| Container | Name |
|--------|----------------------|
| Worldserver | `ac-worldserver` |
| Authserver | `ac-authserver` |
| Database | `ac-database` |

The universal way to always find the worldserver:

```bash
docker ps --format '{{.Names}}' | grep worldserver
```

Whatever it returns — that is your container.

---

## 📋 Quick Reference — Commands You Will Use Most

### Start the Server
```bash
cd ~/wow-server && docker compose up -d
```

Change `wow-server` to `wow-server-npcbots` or
`wow-server-playerbots` for other versions.

### Stop the Server Safely
```bash
cd ~/wow-server && docker compose down
```

### Check if Server is Running
```bash
docker ps
```

### Watch the Server Start Up
```bash
docker logs -f $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

*(This command auto-finds the worldserver container for you. You'll see live log output. When you see `AZEROTH IS READY!`, the server is fully loaded.)*

Press Ctrl+C to stop watching. The server keeps running.

### Open the GM Console (the worldserver/server console)
```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

*(This command auto-finds the worldserver container for you. If it worked, your prompt changes to `AC>`.)*

Exit with **Ctrl+P then Ctrl+Q** — never Ctrl+C!

---

## ⚡ Everyday Server Management

### Starting Your Server

```bash
cd ~/wow-server && docker compose up -d
```

The `-d` means run in the background. You can close Konsole
and the server keeps running!

For other server versions:
```bash
cd ~/wow-server-npcbots && docker compose up -d
cd ~/wow-server-playerbots && docker compose up -d
```

---

### Stopping Your Server Safely

```bash
cd ~/wow-server && docker compose down
```

Always use this to stop — never just shut down your machine
while the server is running. Docker needs to save the database
properly first.

---

### Checking Server Status

```bash
docker ps
```

If you see your worldserver, authserver and database containers
listed — your server is running. If the table is empty — it
is stopped.

---

### Restarting Just the Worldserver

Sometimes you change a setting and just need to restart the
game world without touching the database:

```bash
docker restart $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

---

### Checking Server Logs

```bash
docker logs $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

To watch live as it happens add `-f`:

```bash
docker logs -f $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

Press Ctrl+C to stop following. The server keeps running.

---

## 👤 Account Management

### Creating a New Account

Open the GM console:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

If it worked, your prompt changes to `AC>`. Then type:

```
account create USERNAME PASSWORD
account set gmlevel USERNAME 3 -1
```

Exit safely with **Ctrl+P then Ctrl+Q**

> **To paste in Konsole:** right-click → Paste, or press **Ctrl+Shift+V**

---

### Creating Multiple Accounts

Just repeat the process for each account:

```
account create caitlin mypassword
account set gmlevel caitlin 3 -1

account create kiddo simplepass
account set gmlevel kiddo 3 -1
```

---

### GM Level Explained

| Level | Role | Can do |
|-------|------|--------|
| 0 | Regular player | Nothing special |
| 1 | Moderator | Basic commands |
| 2 | Game Master | Most commands |
| 3 | Administrator | Full control |

---

### Changing a Password

```
account set password USERNAME NEWPASSWORD NEWPASSWORD
```

> Type the new password **twice** — that's how AzerothCore confirms it. You do not need the old password when you have GM console access.

---

### List Online Accounts

```
account onlinelist
```

---

## 🔀 Running Multiple Server Versions

If you have Base WoW AND NPCBots AND Playerbots installed
you can only run one at a time — they share the same ports.

**Switch from Base WoW to NPCBots:**

```bash
cd ~/wow-server && docker compose down
cd ~/wow-server-npcbots && docker compose up -d
```

**Switch from NPCBots to Playerbots:**

```bash
cd ~/wow-server-npcbots && docker compose down
cd ~/wow-server-playerbots && docker compose up -d
```

---

## 💾 Backing Up Your Characters

Before doing anything major — back up first. Run these two commands one at a time:

**Step 1 — Find the database container:**
```bash
DB=$(docker ps --format '{{.Names}}' | grep -iE "database" | head -1)
```

**Step 2 — Export everything to a file:**
```bash
docker exec $DB mysqldump -uroot -ppassword --databases acore_characters acore_auth acore_world > ~/wow-backup-$(date +%Y%m%d).sql
```

Your backup is saved to your home folder (`~`) as a file like `wow-backup-20260704.sql`.

**Verify the backup was created:**
```bash
ls -lh ~/wow-backup-*.sql
```
You should see the file listed with a size greater than 0. If the file is missing or 0 bytes, something went wrong — check that the server is running and try again.

**To restore a backup:**

> ⚠️ **WARNING — restoring a backup replaces your current characters and data.** Only do this if you intentionally want to roll back.

```bash
DB=$(docker ps --format '{{.Names}}' | grep -iE "database" | head -1)
docker exec -i $DB mysql -uroot -ppassword < ~/wow-backup-20260506.sql
```

Replace `20260506` with the date in your backup filename.

---

## ➡️ Continue to Part 2

**[Part 2 — GM Console, Bot Commands, Troubleshooting & Terminal Basics](./WoW-WotLK-CONTROLS-2.md)**

---

*Part of the Dad's MMO Lab project — offline MMO servers on Steam Deck, free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
