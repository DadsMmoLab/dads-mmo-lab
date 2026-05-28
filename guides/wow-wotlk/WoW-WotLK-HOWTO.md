# Dad's MMO Lab — Wrath of the Lich King Server: How-To Guide

**Expansion:** World of Warcraft: Wrath of the Lich King (patch 3.3.5a)
**Server:** AzerothCore WotLK + mod-playerbots, compiled from source
**Platform:** Steam Deck (SteamOS), Desktop Mode + Gaming Mode

---

## What This Installs

A fully offline, single-player-friendly Wrath of the Lich King server running on your Steam Deck. No internet required after install. Includes:

- **AzerothCore WotLK** — the open-source WoW WotLK server core
- **mod-playerbots** — 1,600–2,000 AI players that roam Azeroth and Northrend, group up, and run dungeons
- **Gaming Mode launcher** — one-button start from your Steam library

This installer uses AzerothCore's own Docker compose build system, which handles map data download automatically. No WoW client path is required.

---

## Requirements

| Requirement | Details |
|---|---|
| Disk space | **15 GB free** minimum |
| RAM | 16 GB (standard Steam Deck spec) |
| Time | 2–4 hours compile (hands-off) + ~15 min first-boot DB import |
| Power | Deck plugged in; flat hard surface for airflow |

---

## Step 1 — Run the Installer

Open Konsole (Desktop Mode) and run:

```bash
chmod +x ~/Downloads/install-wow-wotlk.sh
~/Downloads/install-wow-wotlk.sh
```

The script walks you through everything interactively. Answer the prompts at the start, then walk away.

---

## What Happens During Install

### Step 1: Summary & Confirm (~1 min)
Confirms what will be built and asks you to start.

### Step 2: Compile AzerothCore + Playerbots (2–4 hours)
- Clones [mod-playerbots/azerothcore-wotlk](https://github.com/mod-playerbots/azerothcore-wotlk) with the Playerbot branch
- Clones [mod-playerbots/mod-playerbots](https://github.com/mod-playerbots/mod-playerbots)
- Builds Docker images: worldserver, authserver, db-import, client-data
- On first boot, AzerothCore downloads and imports its map data automatically

The fan will be loud during compile — that's normal.

> **If it fails:** Re-run the installer. It detects existing compiled images and skips the compile automatically.

### Step 3: Wait for Server Ready (~5–15 min first boot)
The installer waits for the world server to print `ready...` before continuing. First launch after compilation includes a full database import pass, which takes 10–15 minutes. Subsequent starts take ~30 seconds.

### Step 4: Create Your Account
The installer pauses here and shows you the exact commands. See Step 2 below.

### Step 5: Gaming Mode Setup
Creates `~/wow-playerbots-launcher.sh` and saves a reference card to `~/wow-server-playerbots/MY_SERVER.txt`.

---

## Step 2 — Create Your Account (Required)

When the installer pauses at account creation, open a **new Konsole window** and run:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

At the `AC>` prompt, type:

```
account create player player
account set gmlevel player 3 -1
```

Exit safely: **Ctrl+P then Ctrl+Q** (sequential — do NOT press Ctrl+C, that kills the server).

Return to the installer window and press **Enter** to continue.

---

## Step 3 — Set Your Realmlist

In your WoW WotLK client folder, find `realmlist.wtf` and make sure it contains:

```
set realmlist 127.0.0.1
```

Common locations:
- `[client]/realmlist.wtf`
- `[client]/Data/enUS/realmlist.wtf`

Lock it after editing so the client doesn't overwrite it:
```bash
chmod 444 "[path]/realmlist.wtf"
```

---

## Step 4 — Add to Steam (Gaming Mode)

You need two Steam shortcuts.

### Shortcut 1: Server Launcher

1. Steam → **Add a Non-Steam Game** → browse to `/usr/bin/konsole`
2. Rename to: `WoW Playerbots Server`
3. Right-click → **Properties** → Launch Options:
   ```
   --hold -e bash ~/wow-playerbots-launcher.sh
   ```
4. Compatibility: **Proton OFF** (this is a Linux bash script)

### Shortcut 2: WoW Client

1. Steam → **Add a Non-Steam Game** → browse to `WoW.exe` in your client folder
2. Rename to: `Wrath of the Lich King`
3. Compatibility: **Force GE-Proton** (latest)

---

## Daily Use — Gaming Mode

1. Launch **WoW Playerbots Server** from your library
2. Wait for: **`AZEROTH IS READY!`**
3. Press the Steam button → switch to your library
4. Launch **Wrath of the Lich King**
5. Log in: your chosen username / password — realmlist: **127.0.0.1**
6. **Bots take 5–10 minutes after server start to populate** — be patient on first login

When you close WoW, the launcher shuts the server down automatically. If WoW isn't detected within 5 minutes, the server stays alive for 3 hours as a fallback.

---

## Useful Commands (Desktop Mode)

```bash
# Start server manually
cd ~/wow-server-playerbots && docker compose up -d

# Stop server
cd ~/wow-server-playerbots && docker compose down

# Watch live logs
cd ~/wow-server-playerbots && docker compose logs -f

# Check running containers
docker ps | grep -iE "worldserver|authserver"

# Attach to server console
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
# Exit: Ctrl+P then Ctrl+Q

# Create additional accounts
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
# account create USERNAME PASSWORD
# account set gmlevel USERNAME 3 -1
# Ctrl+P then Ctrl+Q to exit
```

---

## Files and Paths

| Path | What it is |
|---|---|
| `~/wow-server-playerbots/` | Server root |
| `~/wow-server-playerbots/modules/mod-playerbots/` | Playerbots module source |
| `~/wow-server-playerbots/docker-compose.override.yml` | Bot settings and build targets |
| `~/wow-server-playerbots/MY_SERVER.txt` | Quick reference card |
| `~/wow-playerbots-launcher.sh` | Gaming Mode launcher |
| `~/playerbots-build.log` | Compile log |

**Server ports:**

| Port | Service |
|---|---|
| 3724 | Auth server |
| 8085 | World server |

---

## Bot Settings

Bots are tuned for a solo player. Settings in `docker-compose.override.yml`:

| Setting | Value |
|---|---|
| `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS` | 1600 |
| `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS` | 2000 |
| `AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN` | 1 (enabled) |

To change these, edit `~/wow-server-playerbots/docker-compose.override.yml` and restart:
```bash
cd ~/wow-server-playerbots && docker compose down && docker compose up -d
```

---

## Troubleshooting

### Server won't start / worldserver keeps restarting

```bash
docker compose -f ~/wow-server-playerbots/docker-compose.yml logs ac-worldserver --tail 50
```

### "ready..." never appears

AzerothCore's first boot includes a full database import. This can take 10–15 minutes on a fresh compile. Watch the logs:
```bash
cd ~/wow-server-playerbots && docker compose logs -f ac-worldserver
```
Look for `[DatabaseLoader]` entries — these are the database import steps.

### Can't connect / wrong realm

Check realmlist.wtf contains `set realmlist 127.0.0.1` and that the authserver is running:
```bash
docker ps | grep authserver
```

### Compile failed

Check `~/playerbots-build.log` for the last error. Common causes:
- Network drop during clone — re-run the installer
- Disk full during Docker build — `df -h ~` to check
- Docker not running — `sudo systemctl start docker`

### Re-running the installer

Safe to re-run. If compiled images already exist in `~/wow-server-playerbots/`, the installer skips the 2–4 hour compile and restarts the server instead. To force a full rebuild:
```bash
sudo rm -rf ~/wow-server-playerbots
~/Downloads/install-wow-wotlk.sh
```

---

*Dad's MMO Lab — one-click offline MMO servers for Steam Deck.*
*youtube.com/@DadsMmoLab*
