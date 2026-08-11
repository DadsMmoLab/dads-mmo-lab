# Dad's MMO Lab — Vanilla WoW Server: How-To Guide

**Expansion:** World of Warcraft Vanilla (patch 1.12.1, build 5875)
**Server:** CMaNGOS Classic + Playerbots, compiled from source
**Platform:** Steam Deck (SteamOS), Desktop Mode + Gaming Mode

---

## What This Installs

A fully offline, single-player-friendly Vanilla WoW server running on your Steam Deck. No internet required after install. Includes:

- **CMaNGOS Classic** — the open-source WoW 1.12.1 server core
- **Playerbots** — 1,600-2,000 AI players that roam Azeroth, form parties, and run dungeons
- **AHBot** — populates the Auction House automatically (~15k items at steady state)
- **All four databases** — world content (creatures, items, quests), realmd, characters, logs
- **Gaming Mode launcher** — one-button start from your Steam library

The installer compiles everything from source inside Docker. This takes 2-4 hours on first run. Subsequent starts take seconds.

---

## Requirements

| Requirement | Details |
|---|---|
| WoW Vanilla client | Version **1.12.1, build 5875** — must contain `Data/dbc.MPQ` |
| Disk space | **20 GB free** minimum |
| RAM | 16 GB (standard Steam Deck spec) |
| Time | 3-5 hours wall-clock (mostly hands-off) |
| Power | Deck plugged in; flat hard surface for airflow |

> **Client note:** Your client must have `Data/dbc.MPQ` (the DBC data archive). This is the most critical check — without it, extraction will fail after the 3-hour compile. The installer verifies this before starting the compile.

---

## Step 1 — Run the Installer

Open Konsole (Desktop Mode) and run:

```bash
chmod +x ~/Downloads/install-wow-vanilla.sh
~/Downloads/install-wow-vanilla.sh
```

The script walks you through everything interactively. You enter your client path at the start, then walk away.

---

## What Happens During Install

### Phase 1: System Check + Client Validation (~2 min)
Verifies Linux, disk space, internet, and RAM. Then asks for your WoW client path and validates:
- `Data/` folder exists
- `Data/dbc.MPQ` is present (critical for extraction)
- At least 5 MPQ files found

### Phase 2: Pre-Compile Summary (~1 min)
Shows what will be built and asks you to confirm before the long compile.

### Phase 3: Compile CMaNGOS Classic (2-4 hours)
Builds a Docker image containing:
- CMaNGOS Classic core (`mangosd`, `realmd`, map extractors)
- Playerbots module compiled in
- AHBot compiled in
- Classic world database and Playerbots SQL bundled

A heartbeat prints every 5 minutes. The fan will be loud — that's normal.

### Phase 4: Extract Client Data (15-50 min)

Reads your WoW Vanilla client and extracts into `~/wow-vanilla-server/data/`:

| Output | What it is | Expected count |
|---|---|---|
| `maps/` | Zone geometry + encounter data | 2,000-4,000 files |
| `dbc/` | Game data tables | 150+ files |
| `vmaps/` | Line-of-sight obstacles | 3,000+ files |
| `mmaps/` | Pathfinding mesh (Playerbots) | 2,000+ files |

> **Extraction writes temporary folders** (`Buildings/`, `Cameras/`, etc.) into your client folder and moves them out automatically when done.

### Phase 5: Database Setup (~5 min)
- Starts MariaDB 11 in Docker
- Creates four databases: `mangos`, `realmd`, `characters`, `logs`
- Imports the full Classic world content database (~17k items, ~10k creatures, ~4k quests)
- Applies all content updates, ACID AI scripts, and core schema updates
- Imports Playerbots SQL (characters + world + Classic-specific tables)
- Verifies item count (expect ~17,718) and bot tables (expect 12+)

### Phase 6: Start + Configure
- Starts `mangosd` and `realmd`
- Waits up to 10 minutes for world server initialization
- Writes `~/wow-vanilla-launcher.sh`
- Patches your client's `realmlist.wtf` to `127.0.0.1`

---

## Step 2 — Create Your Account (Required)

```bash
docker attach vanilla-mangosd
```

At the `mangos>` prompt:

```
account create player player
account set gmlevel player 3 -1
```

Exit safely: **Ctrl+P then Ctrl+Q** — never Ctrl+C (that kills the server).

---

## Step 3 — Set Your Realmlist

The installer tries to write `realmlist.wtf` automatically. If it succeeded you'll see "auto-configured" on the completion screen. If not, find it manually:

1. Common locations:
   - `[client]/realmlist.wtf`
   - `[client]/Data/enUS/realmlist.wtf` (or your locale: enGB, deDE, frFR, etc.)

2. Set contents to:
   ```
   set realmlist 127.0.0.1
   ```

3. Lock it:
   ```bash
   chmod 444 "[path]/realmlist.wtf"
   ```

---

## Step 4 — Add to Steam (Gaming Mode)

### Shortcut 1: Server Launcher

1. Steam → **Add a Non-Steam Game** → `/usr/bin/konsole`
2. Rename to: `Vanilla WoW Server`
3. Launch Options:
   ```
   --hold -e bash ~/wow-vanilla-launcher.sh
   ```
4. Compatibility: **Proton OFF**

### Shortcut 2: WoW Client

1. Steam → **Add a Non-Steam Game** → `WoW.exe` in your client folder
2. Rename to: `Vanilla WoW`
3. Compatibility: **Force GE-Proton** (latest)

---

## Daily Use — Gaming Mode

1. Launch **Vanilla WoW Server** from your library
2. Wait for: **`AZEROTH IS READY!`**
3. Press Steam button → launch **Vanilla WoW**
4. Log in: **player / player** — realmlist: **127.0.0.1**
5. **Bots take 5-10 minutes to populate** — be patient on first login

The server shuts down automatically when WoW closes. If WoW isn't detected within 5 minutes, the server stays alive for 3 hours as a fallback.

---

## Useful Commands (Desktop Mode)

```bash
# Start server manually
cd ~/wow-vanilla-server && docker compose up -d

# Stop server
cd ~/wow-vanilla-server && docker compose down

# Watch live logs
cd ~/wow-vanilla-server && docker compose logs -f

# Check containers
docker ps | grep vanilla

# Attach to server console
docker attach vanilla-mangosd
# Exit: Ctrl+P then Ctrl+Q

# Create more accounts
docker attach vanilla-mangosd
# account create USERNAME PASSWORD
# account set gmlevel USERNAME 3 -1
```

---

## Files and Paths

| Path | What it is |
|---|---|
| `~/wow-vanilla-server/` | Server root |
| `~/wow-vanilla-server/etc/` | Config files (`mangosd.conf`, `realmd.conf`, `aiplayerbot.conf`, `ahbot.conf`) |
| `~/wow-vanilla-server/data/` | Extracted maps, dbc, vmaps, mmaps |
| `~/wow-vanilla-server/compose.yml` | Docker Compose definition |
| `~/wow-vanilla-server/MY_SERVER.txt` | Quick reference card |
| `~/wow-vanilla-server/.db_password` | MariaDB root password (keep this) |
| `~/wow-vanilla-launcher.sh` | Gaming Mode launcher |
| `/tmp/wow-vanilla-build.log` | Compile log |
| `/tmp/wow-vanilla-extract.log` | Extraction log |

**Server ports:**

| Port | Service |
|---|---|
| 3724 | Login server (realmd) |
| 8085 | World server (mangosd) |

---

## Bot Settings

| Setting | Value |
|---|---|
| `AiPlayerbot.MinRandomBots` | 1600 |
| `AiPlayerbot.MaxRandomBots` | 2000 |
| `AiPlayerbot.RandomBotAccountCount` | 400 |
| `AuctionHouseBot.Chance.Sell` | 75% |

To change: edit `~/wow-vanilla-server/etc/aiplayerbot.conf` or `ahbot.conf` and restart.

> **AH note:** CMaNGOS AHBot builds up listings over hours — no single "total items" dial. At steady state these settings target ~15k active auctions.

---

## Troubleshooting

### Server won't start / mangosd keeps restarting

```bash
docker logs vanilla-mangosd --tail 100
```

Most common causes after a fresh install:
- **Missing spell_template columns** — the installer applies these automatically
- **Playerbots SQL didn't import** — verify: `docker exec -it vanilla-db mariadb -u root -p mangos -e "SHOW TABLES LIKE 'ai_playerbot%'"`
- **Mmap files missing** — check `ls ~/wow-vanilla-server/data/mmaps | wc -l` (expect 2,000+)

### "Access denied" to database

The password is in `~/wow-vanilla-server/.db_password`. If missing and a MariaDB volume exists, the installer auto-resolves this on next run by wiping the stale volume.

### Extraction produced too few files

Verify `Data/dbc.MPQ` exists in your client folder. Check the log:
```bash
cat /tmp/wow-vanilla-extract.log
```

### Compile failed

Check `/tmp/wow-vanilla-build.log`. Common causes:
- Network drop mid-clone — re-run the installer
- Disk full (compile uses 5+ GB) — `df -h ~`
- OOM kill (Deck overheated) — plug in and retry

### Re-running the installer

Safe to re-run. If the compiled image exists, it skips the 2-4 hour compile. To force a full rebuild, choose "Start completely fresh" when prompted.

---

*Dad's MMO Lab — one-click offline MMO servers for Steam Deck.*
*youtube.com/@DadsMmoLab*
