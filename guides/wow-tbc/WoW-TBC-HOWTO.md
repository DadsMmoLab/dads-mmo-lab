# Dad's MMO Lab — Burning Crusade Server: How-To Guide

**Expansion:** World of Warcraft: The Burning Crusade (patch 2.4.3, build 8606)
**Server:** CMaNGOS TBC + Playerbots, compiled from source
**Platform:** Steam Deck (SteamOS), Desktop Mode + Gaming Mode

---

## What This Installs

A fully offline, single-player-friendly Burning Crusade server running on your Steam Deck. No internet connection required after install. Includes:

- **CMaNGOS TBC** — the open-source WoW TBC server core
- **Playerbots** — 1,600–2,000 AI players that roam Azeroth and Outland, form parties, and run dungeons
- **AHBot** — populates the Auction House automatically (~15k items at steady state)
- **All four databases** — world content (creatures, items, quests, Outland), realmd, characters, logs
- **Gaming Mode launcher** — one button to start the server from your Steam library

The installer compiles everything from source inside Docker. This takes 2–4 hours on first run. Subsequent starts take seconds.

---

## Requirements

| Requirement | Details |
|---|---|
| WoW TBC client | Version **2.4.3, build 8606** — must contain `Data/expansion.MPQ` |
| Disk space | **20 GB free** minimum |
| RAM | 16 GB (standard Steam Deck spec) |
| Time | 3–5 hours wall-clock (mostly hands-off) |
| Power | Deck plugged in; flat hard surface for airflow |

> **Client note:** Your client must have `Data/expansion.MPQ` (the Burning Crusade expansion archive). Without it the installer will detect a Vanilla or damaged client and warn you. The locale folder `Data/enUS/` (or your locale equivalent) must also be present.

---

## Step 1 — Run the Installer

Open Konsole (Desktop Mode) and run:

```bash
chmod +x ~/Downloads/install-wow-tbc.sh
~/Downloads/install-wow-tbc.sh
```

The script walks you through everything interactively. You only need to answer a few prompts at the start; after that you can walk away.

---

## What Happens During Install

The installer runs five major phases. It prints your progress throughout.

### Phase 1: System Check (~1 min)
Verifies Linux, disk space, internet, and RAM. Installs Docker if it's not already present.

### Phase 2: Pre-Compile Summary (~1 min)
Shows what will be built and asks you to confirm before the long compile starts.

### Phase 3: Compile CMaNGOS TBC (2–4 hours)
Builds a Docker image with:
- CMaNGOS TBC core (`mangosd`, `realmd`, map extractors)
- Playerbots module compiled in
- AHBot compiled in
- TBC world database and Playerbots SQL bundled into the image

A heartbeat prints every 5 minutes so you know it's still running. The fan will be loud — that's normal.

> **If it fails:** The most common cause is a network drop mid-clone. Re-run the installer and it will detect the existing image if compile succeeded, or restart the compile if it didn't.

### Phase 4: Extract Client Data (15–50 min)

Reads your WoW TBC client and extracts three things into `~/wow-tbc-server/data/`:

| Output | What it is | Expected count |
|---|---|---|
| `maps/` | Zone geometry + encounter data | 3,000–6,000 files |
| `dbc/` | Game data tables | 150+ files |
| `vmaps/` | Line-of-sight obstacles | 2,000+ files |
| `mmaps/` | Pathfinding mesh (Playerbots) | 3,000+ files |

The mmap generation is the last big wait (~30–50 min). Progress streams to your terminal.

> **Extraction writes temporary folders into your client folder** (`Buildings/`, `Cameras/`, etc.) and moves them out automatically when done.

### Phase 5: Database Setup (~5 min)
- Starts MariaDB 11 in Docker
- Creates four databases: `mangos`, `realmd`, `characters`, `logs`
- Imports the full TBC world content database
- Applies all content updates, ACID AI scripts, and DBC-derived tables
- Imports Playerbots SQL (characters + world + TBC-specific tables)
- Verifies item count (expect 20,000+) and bot tables (expect 12+)

### Phase 6: Start Server + Configure
- Starts `mangosd` (world) and `realmd` (login)
- Waits up to 10 minutes for the world server to initialize
- Writes `~/wow-tbc-launcher.sh` for Gaming Mode
- Patches your client's `realmlist.wtf` to `127.0.0.1`

---

## Step 2 — Create Your Account (Required)

The installer can't create your account automatically (password hashing requires the live server console). This takes 30 seconds:

```bash
docker attach tbc-mangosd
```

At the `mangos>` prompt, type:

```
account create player player
account set gmlevel player 3 -1
```

Exit safely: **Ctrl+P then Ctrl+Q** (press them in sequence, not together).

> **Never press Ctrl+C inside `docker attach`** — that kills the server process.

---

## Step 3 — Set Up Your Client Realmlist

The installer tries to write `realmlist.wtf` automatically. If it succeeded you'll see "auto-configured" on the completion screen. If not, do it manually:

1. Find `realmlist.wtf` in your client folder. Common locations:
   - `[client]/realmlist.wtf`
   - `[client]/Data/enUS/realmlist.wtf`
   - `[client]/Data/enGB/realmlist.wtf`

2. Edit it to contain exactly:
   ```
   set realmlist 127.0.0.1
   ```

3. Lock it so other tools don't overwrite it:
   ```bash
   chmod 444 "[path]/realmlist.wtf"
   ```

---

## Step 4 — Add to Steam (Gaming Mode)

You need **two** Steam shortcuts: one for the server launcher, one for the WoW client.

### Shortcut 1: Server Launcher

1. Steam → **Add a Non-Steam Game** → browse to `/usr/bin/konsole`
2. Rename to: `Burning Crusade Server`
3. Right-click → **Properties** → Launch Options:
   ```
   --hold -e bash ~/wow-tbc-launcher.sh
   ```
4. Compatibility tab: **Proton OFF** (this is a Linux bash script)

### Shortcut 2: WoW Client

1. Steam → **Add a Non-Steam Game** → browse to `WoW.exe` in your client folder
2. Rename to: `Burning Crusade WoW`
3. Compatibility tab: **Force GE-Proton** (latest version)

---

## Daily Use — Gaming Mode

1. Launch **Burning Crusade Server** from your Steam library
2. A terminal opens showing startup progress
3. Wait for: **`OUTLAND IS READY!`**
4. Press the Steam button → switch to your library
5. Launch **Burning Crusade WoW**
6. Log in: **player / player** — realmlist: **127.0.0.1**
7. Select a Blood Elf, Draenei, or any race — all TBC races and classes are available
8. **Bots need 5–10 minutes after server startup to populate the world** — be patient on first login

When you close WoW, the launcher detects this and shuts the server down automatically. If WoW isn't detected within 5 minutes of launch, the server stays alive for 3 hours as a fallback.

---

## Useful Commands (Desktop Mode)

```bash
# Start server manually
cd ~/wow-tbc-server && docker compose up -d

# Stop server
cd ~/wow-tbc-server && docker compose down

# Watch live server logs
cd ~/wow-tbc-server && docker compose logs -f

# Check if containers are running
docker ps | grep tbc

# Attach to the server console
docker attach tbc-mangosd
# (Exit: Ctrl+P then Ctrl+Q)

# Create additional accounts
docker attach tbc-mangosd
# account create USERNAME PASSWORD
# account set gmlevel USERNAME 3 -1
# Ctrl+P then Ctrl+Q to exit
```

---

## Files and Paths

| Path | What it is |
|---|---|
| `~/wow-tbc-server/` | Server root |
| `~/wow-tbc-server/etc/` | Config files (`mangosd.conf`, `realmd.conf`, `aiplayerbot.conf`, `ahbot.conf`) |
| `~/wow-tbc-server/data/` | Extracted maps, dbc, vmaps, mmaps |
| `~/wow-tbc-server/compose.yml` | Docker Compose definition |
| `~/wow-tbc-server/MY_SERVER.txt` | Quick reference card (ports, commands) |
| `~/wow-tbc-server/.db_password` | MariaDB root password (keep this — reinstall needs it) |
| `~/wow-tbc-launcher.sh` | Gaming Mode launcher |
| `/tmp/wow-tbc-build.log` | Compile log |
| `/tmp/wow-tbc-extract.log` | Extraction log |

**Server ports:**

| Port | Service |
|---|---|
| 3724 | Login server (realmd) |
| 8085 | World server (mangosd) |

---

## Bot Settings

Bots are tuned for a solo player. Defaults applied by the installer:

| Setting | Value |
|---|---|
| `AiPlayerbot.MinRandomBots` | 1600 |
| `AiPlayerbot.MaxRandomBots` | 2000 |
| `AiPlayerbot.RandomBotAccountCount` | 400 |
| `AuctionHouseBot.Chance.Sell` | 75% |

To change these, edit the config files in `~/wow-tbc-server/etc/` and restart the server.

> **AH note:** CMaNGOS AHBot builds up its auction listings over hours — there's no single "total items" dial. At steady state with these settings, expect ~15k active auctions.

---

## Troubleshooting

### Server won't start / mangosd keeps restarting

```bash
docker logs tbc-mangosd --tail 100
```

Most common causes after a fresh install:
- **Missing spell_template columns** — the installer applies these automatically; if it failed, check the build log
- **Playerbots SQL didn't import** — verify: `docker exec -it tbc-db mariadb -u root -p mangos -e "SHOW TABLES LIKE 'ai_playerbot%'"`
- **Mmap files missing** — check `ls ~/wow-tbc-server/data/mmaps | wc -l` (expect 3,000+)

### "Access denied" or database won't connect

The DB password is stored in `~/wow-tbc-server/.db_password`. If this file is missing and the MariaDB volume still exists, the installer detects the mismatch and wipes the volume automatically on next run.

### Blood Elf / Draenei not available at character creation

The installer sets the account expansion to TBC (1) automatically. If they're still locked, run:

```bash
docker exec -it tbc-db mariadb -u root -p realmd -e "UPDATE account SET expansion=1;"
```

### WoW client can't connect

1. Check the realmlist: `cat [client]/realmlist.wtf` — must be `set realmlist 127.0.0.1`
2. Verify the server is actually running: `docker ps | grep tbc`
3. Check the realmd logs: `docker logs tbc-realmd --tail 30`

### Compile failed / "out of disk space"

The compile produces 5+ GB of intermediate artifacts. Check `df -h ~` before retrying. Re-run the installer — it will ask if you want to start fresh or reuse the existing build.

### Extraction produced too few files

Verify your client has both `Data/expansion.MPQ` and a locale folder (`Data/enUS/` or similar) with locale MPQ archives. A stripped repack missing these files will produce an incomplete extraction.

---

## Re-running the Installer

The installer is **safe to re-run**. It detects:

- **Compiled image already exists** → skips the 2–4 hour compile and asks if you want to rebuild
- **Server folder + image both exist** → asks if you want a full fresh install or to continue
- **DB password saved** → loads it automatically so it doesn't generate a new one that conflicts with the existing MariaDB volume

---

## What's Coming

Dad's MMO Lab is building a pre-built Docker image publishing pipeline via GitHub Actions. When it ships, a separate `install-wow-tbc-fast.sh` will do a 5-minute image pull instead of a 3–4 hour source compile.

Watch: [github.com/DadsMmoLab/dads-mmo-lab](https://github.com/DadsMmoLab/dads-mmo-lab)

---

*Dad's MMO Lab — one-click offline MMO servers for Steam Deck.*
*youtube.com/@DadsMmoLab*
