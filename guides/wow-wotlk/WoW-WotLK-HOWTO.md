# Dad's MMO Lab — Wrath of the Lich King Server: How-To Guide

**Expansion:** World of Warcraft: Wrath of the Lich King (patch 3.3.5a)
**Server:** AzerothCore WotLK + mod-playerbots, compiled from source
**Platform:** Steam Deck (SteamOS), Desktop Mode + Gaming Mode

> 📍 **Not on a Steam Deck?**
> - **Arch Linux / SteamOS:** The server runs in Docker the same way — the server management, account creation, and networking sections in this guide all apply to you. The automated installer script is Steam Deck specific.
> - **Fedora / Bazzite:** The server runs in Docker the same way — the server management, account creation, and networking sections in this guide all apply to you. The automated installer script is Steam Deck specific. Use the fedora script.
> - **Debian / Ubuntu / PopOS! / Linux Mint:** The server runs in Docker the same way — the server management, account creation, and networking sections in this guide all apply to you. The automated installer script is Steam Deck specific. Use the ubuntu script.
> - **Windows 10/11:** Use the [DML Windows Installer guide](../DML-Windows/DML-Windows-HOWTO.md) for a full Windows walkthrough, then return here for the server management and networking sections. You should be using the `Install-WoW-WotLK.ps1` script from that guide, not the Linux installer scripts in this guide.

---

## What This Installs

A fully offline, single-player-friendly Wrath of the Lich King server running on your Steam Deck. No internet required after install. Includes:

- **AzerothCore WotLK** — the open-source WoW WotLK server core
- **mod-playerbots** — Several hundred AI players that roam Azeroth and Northrend, group up, and run dungeons
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

**Before you start — make sure you have all of these:**

__HOLD ON!__ Fedora / Bazzite users: skip the Steam Deck-specific instructions. Use `install-wow-wotlk-fedora.sh` to install AzerothCore WotLK + Playerbots on Fedora systems.

__HOLD ON!__ Ubuntu / Debian / Linux Mint users: skip the Steam Deck-specific instructions. Use `install-wow-wotlk-ubuntu.sh` to install AzerothCore WotLK + Playerbots on Ubuntu/Debian/Linux Mint systems.

- [ ] `install-wow-wotlk.sh` downloaded into your **Downloads** folder. To download it: go to [github.com/DadsMmoLab/dads-mmo-lab](https://github.com/DadsMmoLab/dads-mmo-lab) → open the `guides/wow-wotlk/` folder → click `install-wow-wotlk.sh` → click the **download** icon (arrow pointing down) → save to your Downloads folder.
   - Alternative: You can use curl to download it directly;
   - **SteamOS**:
   ```bash
   curl -o ~/Downloads/install-wow-wotlk.sh https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/install-wow-wotlk.sh
   ```

   - **Fedora**:
   ```bash
   curl -o ~/Downloads/install-wow-wotlk-fedora.sh https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/install-wow-wotlk-fedora.sh
   ```

   - **Debian**:
   ```bash
   curl -o ~/Downloads/install-wow-wotlk-ubuntu.sh https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/install-wow-wotlk-ubuntu.sh
   ```
- [ ] A **WoW 3.3.5a (Wrath of the Lich King)** game client already on your device. The server software does not include game files — you supply your own client. Chromiecraft HD works great for this, or you can use your own legally obtained copy of the game. The client must be **3.3.5a** — other versions will not work, Classic Era from Battle.net does not work.
- [ ] **Docker** — the installer will install and start Docker automatically if it isn't already running. You don't need to install it manually. To verify Docker is already running (optional): open Konsole and run `docker ps`. If you see a table header (even empty), Docker is running. If you get an error, don't worry — the installer handles it.
- [ ] **GE-Proton** installed in Steam (for the WoW client shortcut in Step 4). Install it via **ProtonUp-Qt** from the Discover app store.

> **New to Steam Deck Desktop Mode?**
> Press **Steam button → Power → Switch to Desktop** to reach the desktop. To get back to Gaming Mode, double-click **Return to Gaming Mode** on the desktop.

---

## Step 1 — Run the Installer

> **In Linux (and on your Steam Deck), `~` is a shortcut that means your home folder** — the personal folder where your files live. So `~/Downloads` is your Downloads folder.

**Open Konsole or Terminal** — the black terminal app. You'll find it pinned to the taskbar in Desktop Mode, or search for it in the application launcher. This is where you type commands.

Run these two commands:

SteamOS / Arch Linux:
```bash
chmod +x ~/Downloads/install-wow-wotlk.sh
~/Downloads/install-wow-wotlk.sh
```

Fedora / Bazzite:
```bash
chmod +x ~/Downloads/install-wow-wotlk-fedora.sh
~/Downloads/install-wow-wotlk-fedora.sh
```

Ubuntu / Debian / Linux Mint / PopOS!:
```bash
chmod +x ~/Downloads/install-wow-wotlk-ubuntu.sh
~/Downloads/install-wow-wotlk-ubuntu.sh
```

> The first command (`chmod +x`) gives the file permission to run as a program. The second command starts it.  
> **To paste in Konsole:** right-click → Paste, or press **Ctrl+Shift+V**.

The script will ask you a few setup questions in the terminal — read each one, type your answer, and press **Enter**. After that, it runs unattended for 2–4 hours while it compiles everything.

---

## What Happens During Install

The installer handles everything automatically. This section is just for reference — **you don't do these steps yourself**.

### Phase 1: Summary & Confirm (~1 min)
Confirms what will be built and asks you to start.

### Phase 2: Compile AzerothCore + Playerbots (2–4 hours)
The installer automatically:
- Downloads the server source code from [mod-playerbots/azerothcore-wotlk](https://github.com/mod-playerbots/azerothcore-wotlk) and [mod-playerbots/mod-playerbots](https://github.com/mod-playerbots/mod-playerbots)
- Builds the server using Docker (a tool that packages software in a self-contained way — you don't need to understand it)
- Compiles four components: worldserver, authserver, db-import, client-data

The fan will be loud during compile — that's normal.

> **If it fails:** Re-run the installer. It detects an existing project directory and skips the compile automatically.

### Phase 3: Wait for Server Ready (~5–15 min first boot)
The installer waits for the world server to print `ready...` before continuing. The first launch after compilation includes a full game database import — this is normal and takes 10–15 minutes once. Every start after that takes ~30 seconds.

### Phase 4: Create Your Account
The installer pauses here and shows you the exact commands. See **Step 2** below.

### Phase 5: Gaming Mode Setup
Creates `~/wow-playerbots-launcher.sh` and saves a reference card to `~/wow-server-playerbots/MY_SERVER.txt`.

---

## Step 2 — Create Your Account (Required)

When the installer pauses at account creation, leave the first terminal window open and open a **second Konsole window** (right-click the taskbar Konsole icon → New Window, or open it again from the app launcher). Then run:

```bash
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

If it worked, your terminal prompt changes to `AC>`. That means you're talking directly to the server console.

At the `AC>` prompt, type the following — **replace `YOURNAME` and `YOURPASSWORD` with your own login details:**

```
account create YOURNAME YOURPASSWORD
account set gmlevel YOURNAME 3 -1
```

For example, if you want the username `john` and password `mypassword`:
```
account create john mypassword
account set gmlevel john 3 -1
```

> `account set gmlevel ... 3 -1` gives your account administrator ("GM") powers in-game, which lets you use commands like teleport, summon items, and more. This only affects your local server.

---

> ⚠️ **IMPORTANT — exiting the console:**  
> Press **Ctrl+P, then Ctrl+Q** (one after the other) to safely detach from the server console.  
> **Do NOT press Ctrl+C** — that stops the server entirely.

---

Return to the installer window and press **Enter** to continue.

> 📍 **Linux users (Ubuntu / Debian / Fedora):** You won't have an installer window — you're here after starting your server manually with `docker compose up -d`. Skip the "press Enter" instruction above. Your server is already running; just make sure the account was created successfully, then move on to Step 3.

---

## Step 3 — Set Your Realmlist

The **realmlist** tells your WoW client which server to connect to. Setting it to `127.0.0.1` points the game at the server running on your own Steam Deck.

In your WoW WotLK client folder, find `realmlist.wtf` — it's usually inside the `Data` subfolder, in a folder named after your client's language:

- **Primary location:** `[client]/Data/enUS/realmlist.wtf` *(use your locale — enGB, deDE, etc.)*
- Fallback: `[client]/realmlist.wtf`

On Steam Deck, your WoW client is usually somewhere like:
- `/home/deck/Games/WoW/` (if you copied it there manually)
- Or inside a Proton prefix under `~/.steam/steam/steamapps/compatdata/[AppID]/` — where `[AppID]` is a number Steam assigns to the game shortcut

> 💡 **Not sure where your realmlist.wtf is?** Open **Dolphin** file manager, press **F5** to show hidden files, then use the search bar (magnifying glass icon) and search for `realmlist.wtf`. It'll find it wherever it is.

Open the file in a text editor (right-click → Open With → Kate or Text Editor) and make sure it contains exactly:

```
set realmlist 127.0.0.1
```

Then lock the file so the WoW launcher can't overwrite it:
```bash
chmod 444 "[path]/realmlist.wtf"
```

> `chmod 444` makes the file read-only. If the command returns to a blank prompt with no error, it worked. If you ever need to edit it again, unlock it first:
> ```bash
> chmod 644 "[path]/realmlist.wtf"
> ```

---

## Step 4 — Add to Steam (Gaming Mode)

You need two Steam shortcuts. A **Non-Steam Game** is just a shortcut in your Steam library to a program Steam didn't install — it lets you launch anything from Gaming Mode.

### Shortcut 1: Server Launcher

1. Steam → **Add a Non-Steam Game** → browse to `/usr/bin/konsole`
   *(In the file picker, navigate to the root of the filesystem `/`, then `usr` → `bin` → select `konsole`)*
   - For Fedora, the path is `/usr/bin/konsole` as well if you have KDE installed. If you don't have KDE, use your terminal app's path instead (e.g., `gnome-terminal` or `xterm`).
   - For Debian, Ubuntu, and PopOS!, the path is different, if you have gnome, use `/usr/bin/gnome-terminal` instead of `konsole`.
2. Rename to: `WoW Playerbots Server`
3. Right-click → **Properties** → Launch Options:
   ```
   --hold -e bash ~/wow-playerbots-launcher.sh
   ```
   *(This tells Konsole to open and run your server launcher script, keeping the window open so you can see status messages)*
4. Compatibility tab: **disable Proton** (this is a Linux script, not a Windows program)

### Shortcut 2: WoW Client

1. Steam → **Add a Non-Steam Game** → browse to `WoW.exe` in your client folder
2. Rename to: `World of Warcraft: Wrath of the Lich King`
   - The name helps steam find controller layouts.
3. Compatibility tab: **Force a specific Steam Play compatibility tool** → select **GE-Proton** (latest)  
   *(If you don't see GE-Proton, install it first via ProtonUp-Qt from the Discover app store)*

---

## Daily Use — Gaming Mode

1. Launch **WoW Playerbots Server** from your library — a terminal window will open
2. Wait until you see **`AZEROTH IS READY!`** near the bottom of that window — this means the server is fully loaded
3. **Leave the server window open** (don't close it — the server is running inside it)
4. Press the Steam button → switch to your library
5. Launch **Wrath of the Lich King**
6. Log in using the **username and password you created in Step 2** — this only works if your `realmlist.wtf` is set to `127.0.0.1`
7. **Bots take 5–10 minutes after server start to populate** — be patient on first login

When you close WoW, the launcher shuts the server down automatically. If WoW isn't detected within 5 minutes, the server stays alive for 3 hours as a fallback.

---

## Managing Your Server

After install, you have two tools to manage your server — pick whichever suits you.

---

### Option A — wow-manage.sh (Interactive Menu)

`wow-manage.sh` is an interactive, menu-driven terminal script included in this repository. It wraps all common server tasks behind numbered menus — no typing raw Docker commands required.

**Where to find it:** [`guides/wow-wotlk/wow-manage.sh`](https://github.com/DadsMmoLab/dads-mmo-lab/blob/main/guides/wow-wotlk/wow-manage.sh) in this repo.

**Download and run it** (paste these into Konsole):
```bash
curl -o ~/Downloads/wow-manage.sh https://raw.githubusercontent.com/DadsMmoLab/dads-mmo-lab/main/guides/wow-wotlk/wow-manage.sh
chmod +x ~/Downloads/wow-manage.sh
~/Downloads/wow-manage.sh
```

It opens a full-screen interactive menu with three sub-menus:

| Menu | What it does |
|---|---|
| **Server Controls** | Start, stop, restart, check status, view live logs, attach to the server console |
| **Server Modifications** | Add/remove AzerothCore modules, ALE Lua mods, and SQL mods |
| **Configurations** | Set your WoW client folder, configure AH Bot and ALE, rebuild the worldserver |

Navigate with the numbers shown on screen. Press **Enter** with no input to go back.

---

### Option B — The Lab (GUI App — No Terminal Required)

**The Lab** is a full graphical app for managing your server with clicks, not commands. It's built for Steam Deck Gaming Mode and works great from the couch.

**Download:** [github.com/0xVe1L/the-lab](https://github.com/0xVe1L/the-lab) — grab `TheLab.AppImage` from the latest release.

Features include:
- **Start / stop / restart** the server with a live, readable console
- **My Party** — build a 5-man bot group: pick role, class, spec, and level — The Lab spawns and gears them for you
- **Item database & in-game mail** — search any item and send it to your character instantly
- **Teleport** to any named location or map coordinates
- **Module management** — toggle AzerothCore modules on/off; tune their settings in-app
- **Steam integration** — adds the server and WoW client to Steam with artwork
- Auto-shutdown when you close WoW

> **Already have a server from this guide?** The Lab detects existing Dad's MMO Lab installs and migrates them in — your characters and data are untouched.

---

## Useful Commands (Desktop Mode)

These are for manual control or troubleshooting. You don't need them for normal daily use.

> **To paste in Konsole:** right-click → Paste, or press **Ctrl+Shift+V**  
> **To browse folders:** you can paste any `~/...` path into the Dolphin file manager's address bar.

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
# Exit: Ctrl+P then Ctrl+Q  — DO NOT press Ctrl+C (that stops the server)

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

To change these, open `~/wow-server-playerbots/docker-compose.override.yml` in a text editor (right-click → Open With → Kate). Only change the numbers after the `:` — do not change spacing or punctuation, or the file will break. Then restart:
```bash
cd ~/wow-server-playerbots && docker compose down && docker compose up -d
```

---

## Troubleshooting

### Server won't start / worldserver keeps restarting

```bash
cd ~/wow-server-playerbots && docker compose logs --tail 50 ac-worldserver
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
  *(This is an administrator command — it may ask for your Steam Deck password)*

### Re-running the installer

Safe to re-run. If an existing project directory is detected at `~/wow-server-playerbots/`, the installer skips the 2–4 hour compile and restarts the server instead.

To force a completely clean rebuild:

> ⚠️ **WARNING — this permanently deletes the server folder and all local data inside it.** Only run this if you want to wipe the install and start from scratch.

```bash
cd ~/wow-server-playerbots && docker compose down -v --rmi local
sudo rm -rf ~/wow-server-playerbots
~/Downloads/install-wow-wotlk.sh
```

The first line removes Docker containers, volumes, and locally-built images. The second removes the project folder. The third re-runs the installer from scratch.

---

*Part of the Dad's MMO Lab project — free forever.*

**youtube.com/@DadsMmoLab**
**github.com/DadsMmoLab/dads-mmo-lab**
