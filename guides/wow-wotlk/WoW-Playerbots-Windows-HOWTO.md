# Dad's MMO Lab — WoW Playerbots (Windows): How-To Guide

**Expansion:** World of Warcraft: Wrath of the Lich King (patch 3.3.5a)
**Server:** AzerothCore WotLK + mod-playerbots, compiled from source
**Platform:** Windows 11 (build 22H2 or newer), requires DML substrate installed first
**Installer version:** 1.2.4 or newer

---

## What This Installs

A fully offline, single-player-friendly Wrath of the Lich King server running inside WSL2 on your Windows PC. No internet required after install. Includes:

- **AzerothCore WotLK** — the open-source WoW WotLK server core
- **mod-playerbots** — 200–250 AI players that roam Azeroth and Northrend, group up, and run dungeons
- **DML Launcher integration** — start, stop, and manage the server from your system tray

This installer uses AzerothCore's own Docker Compose build system, which handles map data download automatically. No WoW client path is required.

> **Prerequisites:** You must have already run `Install-DML.ps1` before running this installer. The DML substrate (Arch Linux + Docker inside WSL2) must be set up first.

---

## Requirements

| Requirement | Details |
|---|---|
| DML substrate | `Install-DML.ps1` completed successfully |
| Disk space | **30 GB free** inside WSL2 (`dml-arch`) |
| RAM | 8 GB minimum; 16 GB recommended |
| Time | 2–4 hours compile (hands-off) + first-boot database setup (see below) |
| Power | Keep your PC running and plugged in during compile |
| Internet | Required during install |

> **How to check WSL2 disk space inside dml-arch:** Open PowerShell and run `wsl -d dml-arch -u dml -- df -h /home`. The `Avail` column is your free space.

---

## Step 1 — Run the Installer

### Open PowerShell

1. Press the **Windows key**, type `PowerShell`
2. Click **Windows PowerShell** — no administrator needed for this installer
3. Paste and run:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
& "C:\DML\Install-WoW-WotLK.ps1"
```

> **If you saved it somewhere else:** Change the path to match. For example: `& "$env:USERPROFILE\Downloads\Install-WoW-WotLK.ps1"`

The script walks you through everything interactively. Answer the prompts at the start, then walk away.

> ⚠️ **Do not click inside the PowerShell window while the installer is working.** Clicking selects text, and Windows *pauses the script's output* until you press a key — it will look frozen even though it's running fine. If you accidentally click, just press **Enter** once to unfreeze it. Wait until the installer finishes before copying any text out of the window.

---

## What Happens During Install

### Step 1/3: Preflight Checks (~1 min)

| Check | What it's looking for |
|---|---|
| WSL2 | Available and working |
| dml-arch | Registered and startable |
| Docker | Running inside dml-arch (auto-starts if not) |
| Disk space | 30 GB free inside dml-arch |
| Internet | Can reach github.com |

If any check fails the installer stops and tells you exactly what to fix.

### Step 2/3: Compile AzerothCore + Playerbots (2–4 hours)

The installer confirms what it's about to build and asks you to confirm before starting. Once you say yes:

- Clones [mod-playerbots/azerothcore-wotlk](https://github.com/mod-playerbots/azerothcore-wotlk) with the Playerbot branch
- Clones [mod-playerbots/mod-playerbots](https://github.com/mod-playerbots/mod-playerbots)
- Writes `docker-compose.override.yml` with your bot settings
- Builds Docker images: worldserver, authserver, db-import, client-data

Your CPU will be loud during compile — that's normal.

> **If it fails:** Re-run the installer. It detects existing compiled images and skips the 2–4 hour compile automatically, then resumes where it left off. Re-running is always safe.

### First Boot: Database Setup (varies — be patient!)

After compile, the server starts in three phases, and the installer shows progress for each:

1. **`Starting database...`** — MySQL sets itself up for the first time. On most PCs this takes 1–5 minutes, but on slower drives it can take much longer. You'll see `Database still starting... (90s elapsed)` progress messages every 30 seconds — **as long as those keep appearing, everything is working.** The installer waits up to a full hour before giving up.
2. **`Importing world databases...`** — all of Azeroth's data is loaded (5–10 minutes).
3. **`Initializing acore_playerbots...`** then the world server and auth server start.

Subsequent server starts take ~30 seconds — the slow part is first boot only.

### Step 3/3: Create Your Account

The installer pauses here and shows you the exact commands. See Step 2 below.

### After the installer completes

Your server appears automatically in the DML Launcher tray. Right-click the tray icon to start or stop it any time.

---

## Step 2 — Create Your Account (Required)

**Easiest way:** Right-click the **DML Launcher** tray icon → **wow-server-playerbots** → **Attach to Console**. A terminal opens connected directly to the server.

**Manual way:** Open a **new PowerShell window** and run:

```powershell
wsl -d dml-arch -u dml
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
```

Either way, at the `AC>` prompt, type:

```
account create player player
account set gmlevel player 3 -1
```

Replace `player` / `player` with your chosen username and password.

Exit safely: **Ctrl+P then Ctrl+Q** (sequential — do NOT press Ctrl+C, that kills the server).

Return to the installer window and press **Enter** to continue.

> **To create more accounts later:** Start the server from the tray, then use **Attach to Console** and the same commands.

---

## Step 3 — Set Your Realmlist

In your WoW WotLK client folder, find `realmlist.wtf` and make sure it contains:

```
set realmlist 127.0.0.1
```

Common locations:

- `[client]\realmlist.wtf`
- `[client]\Data\enUS\realmlist.wtf`

> **Tip:** If the WoW client keeps resetting your realmlist, right-click `realmlist.wtf` → Properties → check **Read-only**.

---

## Daily Use — DML Launcher

1. Right-click the **DML Launcher** icon in your Windows system tray
2. Click **wow-server-playerbots** → **Start**
3. The server starts in the background — no terminal window needed
4. Wait 30 seconds, then launch your WoW client
5. Log in: your username / password — realmlist: **127.0.0.1**
6. **Bots take 5–10 minutes after server start to populate** — be patient on first login

To stop the server: right-click the tray icon → **wow-server-playerbots** → **Stop**

> ⚠️ **Keep the DML Launcher running while you play.** Windows shuts down the WSL2 environment (and your server with it) about a minute after the last connection to it closes. The DML Launcher keeps the connection alive. If you exit the tray app, your server will stop shortly after — nothing is damaged, but you'll need to start it again.

> **The tray shows all your installed DML servers** — if you install other titles later they appear here too.

---

## Play Together — LAN Play

Want the rest of the house on your server? LAN play lets any PC on your home network join — no router changes, no port forwarding, nothing to install on the other PCs beyond the WoW client itself.

**On the server PC (this one):**

1. Start the server from the tray as usual
2. Right-click the tray icon → **wow-server-playerbots** → **LAN Play** → **Enable LAN Play...**
3. The launcher shows your PC's network address (something like `192.168.1.50`) — write it down, then click **Yes**

**On each other PC:**

1. Install the same WoW 3.3.5a client
2. Open `realmlist.wtf` in the client folder and set it to the address from step 3:
   ```
   set realmlist 192.168.1.50
   ```
3. Log in with an account created on the server (see *Create Your Account* — each player needs their own)

The server PC itself keeps working with `127.0.0.1` — no change needed there.

**Good to know:**

- **Give it half a minute.** The login server re-reads the realm address every few seconds — anyone already sitting at the login screen when you enable LAN play should log out and back in.
- **LAN play survives restarts.** Once enabled it stays enabled, and the launcher re-checks your PC's address every time you start the server.
- **If your router hands this PC a new address**, re-run `Install-DML.ps1` once — Windows pins its LAN forwarding to the address it saw at install time. The Enable LAN Play dialog warns you when this has happened. (Best fix: give this PC a DHCP reservation, see below.)
- **Your network must be marked Private in Windows.** Windows blocks incoming connections on *Public* networks by design. Check **Settings → Network & internet → Ethernet** (or your Wi-Fi network) → Network profile type → **Private**. This is the #1 reason a LAN client can't connect.
- **Turning it off:** tray → **LAN Play** → **Disable LAN Play**. The server goes back to accepting world connections from your PC only.
- **Tip:** give the server PC a fixed address (DHCP reservation) in your router settings so the family never has to update `realmlist.wtf` again.
- The one-time Windows plumbing (firewall + port proxy) is set up automatically when you run `Install-DML.ps1`. If you installed before LAN play existed, re-run it once.

---

## Playing over the Internet (Advanced)

LAN play works because everyone is behind the same router. Letting friends connect **over the internet** means opening your home network to the outside, and DML deliberately does *not* automate it — whether to expose your PC to the internet should always be your own, deliberate call. If you want it:

1. **Set up LAN play first** and confirm a second PC on your network can log in — internet play is LAN play plus a router hop, and this isolates problems.
2. **Forward TCP ports 3724 and 8085** on your router to this PC's LAN address (the one from LAN Play setup). Every router's admin page calls this something slightly different — "port forwarding", "virtual server", "NAT rules".
3. **Find your public IP** (search "what is my IP") and set the realm address to it from a DML shell:
   ```
   dml lan wow-server-playerbots on YOUR_PUBLIC_IP
   ```
4. Friends set `realmlist.wtf` to that public IP.

**Honest caveats before you start:**

- **CGNAT:** many ISPs (especially fiber) put whole neighborhoods behind one shared IP. If your router's "WAN IP" doesn't match what "what is my IP" shows, you're behind CGNAT and **hosting from home is not possible** without a VPN-tunnel service — no router setting fixes it.
- **Your public IP changes.** Most home connections get a new IP every so often; when it changes, friends are locked out until you re-run step 3 with the new one. (The launcher never touches a public realm address — its automatic refresh only manages LAN addresses.)
- **Security:** anyone on the internet can knock on a forwarded port. Use strong, unique account passwords, never raise strangers' accounts to GM, and forward **only** 3724 and 8085 — never 3306 (the database).
- When you're done hosting, remove the forwarding rules from your router.

---

## Optional — Make It Faster

Windows Defender scans every read and write to the WSL2 disk file in real time, which noticeably slows the database and compile steps. Since it's a Linux disk image Defender can't meaningfully inspect anyway, excluding it is safe and gives a real speed boost.

Run once in an **Administrator PowerShell**:

```powershell
Add-MpPreference -ExclusionPath "$env:LOCALAPPDATA\DadsMMOLab"
```

Or via Settings: **Windows Security → Virus & threat protection → Manage settings → Exclusions → Add folder** → pick `C:\Users\<you>\AppData\Local\DadsMMOLab`.

This is entirely optional — the server works fine without it, just slower on first boot.

---

## Useful Commands

Run these from **PowerShell or Windows Terminal**:

```powershell
# Open the DML environment
wsl -d dml-arch -u dml

# Shut down all running servers and the WSL environment
wsl --terminate dml-arch
```

Run these from **inside the dml-arch shell** (after `wsl -d dml-arch -u dml`):

```bash
# Start server manually
cd ~/games/wow-server-playerbots && docker compose up -d

# Stop server
cd ~/games/wow-server-playerbots && docker compose down

# Watch live logs
cd ~/games/wow-server-playerbots && docker compose logs -f

# Check running containers
docker ps | grep -iE "worldserver|authserver"

# Attach to server console (create accounts, run GM commands)
docker attach $(docker ps --format '{{.Names}}' | grep worldserver | head -1)
# Exit: Ctrl+P then Ctrl+Q

# Create additional accounts (from inside the server console)
# account create USERNAME PASSWORD
# account set gmlevel USERNAME 3 -1
```

---

## Files and Paths

| Path | What it is |
|---|---|
| `/home/dml/games/wow-server-playerbots/` | Server root (inside dml-arch) |
| `/home/dml/games/wow-server-playerbots/modules/mod-playerbots/` | Playerbots module source |
| `/home/dml/games/wow-server-playerbots/docker-compose.override.yml` | Bot settings and build targets |
| `/home/dml/games/wow-server-playerbots/env/dist/etc/playerbot.conf` | Playerbot configuration |
| `/home/dml/playerbots-build.log` | Compile log |
| `%TEMP%\dml-wow-install.log` | Windows-side install log |

> **To browse the server files from Windows Explorer:** Press **Windows key + R**, type `\\wsl$\dml-arch\home\dml\games\wow-server-playerbots`, press Enter.

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
| `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS` | 200 |
| `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS` | 250 |
| `AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN` | 1 (enabled) |

To change these, open the dml-arch shell and edit the file:

```bash
wsl -d dml-arch -u dml
nano ~/games/wow-server-playerbots/docker-compose.override.yml
```

Then restart the server from the tray or:

```bash
cd ~/games/wow-server-playerbots && docker compose down && docker compose up -d
```

> **Tip — turn bots off temporarily** (e.g. while testing LAN play, for much faster server starts): set `AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "0"` in the same file and restart. Set it back to `"1"` to bring them back — nothing is lost.
>
> These environment variables win over the module's own config file, so this file is the only place you need to edit.

---

## Troubleshooting

### The installer looks frozen

Two common causes, both harmless:

1. **You clicked inside the window.** Windows pauses script output when text is selected. Press **Enter** once to unfreeze it.
2. **First-time database setup is genuinely slow** on some drives. As long as `Database still starting... (Ns elapsed)` messages keep appearing every 30 seconds, it's working — the installer allows up to an hour.

### "dependency failed to start: container ac-database is unhealthy"

This error came from installer versions before 1.2.4, which gave the database a fixed time limit that slow drives could exceed. **Download the latest installer and re-run it** — it now waits patiently with progress messages, and it resumes from where the previous attempt stopped (no recompile).

### Server won't appear in the DML Launcher tray

The tray scans `/home/dml/games/` inside dml-arch for folders containing a `docker-compose.yml`. Check:

```powershell
wsl -d dml-arch -u dml -- ls ~/games/
```

You should see `wow-server-playerbots` in the list. If it's missing — or if your server is at the old location `/home/dml/wow-server-playerbots` from an early installer — re-run **either** installer (`Install-DML.ps1` or `Install-WoW-WotLK.ps1`) with the server stopped; both migrate old installs to the right place automatically.

### Server stops by itself a minute or two after starting

You closed the DML Launcher (or never started it). Windows shuts down WSL2 — and everything running inside it — shortly after the last connection closes. Keep the DML Launcher tray app running while you play; it holds the environment open.

### Worldserver keeps restarting (database error about a missing `playerbots` table)

Older installer versions could leave the bots database half-created, which crash-loops the world server on every start. **Re-run the latest installer** — it detects the incomplete database and rebuilds it automatically. No recompile, and your accounts and characters are not affected.

### "ready..." never appears / server takes longer than expected

AzerothCore's first boot includes a full database import. Watch the logs:

```powershell
wsl -d dml-arch -u dml -- bash -c "cd ~/games/wow-server-playerbots && docker compose logs -f ac-worldserver"
```

Look for `[DatabaseLoader]` entries — these are the database import steps. The server is ready when you see `ready...`.

### Server starts but bots don't appear

Bots log in gradually after server start — watch the `N/200 Bot ... logged in` counter in the logs (tray → Attach to Console). Populating a few hundred bots can take 10–15 minutes, longer on slower drives. If nothing is logging in at all:

1. Check the bot settings are present (see *Bot Settings* above):
   ```powershell
   wsl -d dml-arch -u dml -- grep PLAYERBOT ~/games/wow-server-playerbots/docker-compose.override.yml
   ```
   You should see `AC_AI_PLAYERBOT_RANDOM_BOT_AUTOLOGIN: "1"` with min/max bot counts.
2. If the settings are missing or the file was hand-edited, re-run `Install-WoW-WotLK.ps1` — it rewrites the override file.
3. Restart the server from the tray.

> **Note:** these environment variables are the source of truth — they override anything in the module's config file (`env/dist/etc/modules/playerbots.conf.dist`). Edit the override file, not the conf.

### Another PC on the network can't connect (LAN play)

Work down this list on the **server PC** — it's ordered by how often each one is the culprit:

1. **Network marked Public.** Windows blocks incoming game traffic on Public networks. **Settings → Network & internet** → your network → profile type must be **Private**.

2. **LAN play not actually enabled.** Tray → **wow-server-playerbots** → **LAN Play** → **Status**. It should say `ON` with your current LAN address. If the address is stale or wrong, click **Enable LAN Play...** again.

3. **Windows plumbing missing or stale** (installed before LAN play existed, rules were removed, or your PC got a new address from the router). Check in PowerShell:
   ```powershell
   netsh interface portproxy show v4tov4
   ```
   You should see entries listening on **your PC's current LAN address** (the one from the LAN Play dialog) for ports **3724** and **8085**, each pointing at `127.0.0.1`. Missing, or listening on an old address? Re-run `Install-DML.ps1` once as Administrator — it rebuilds the rules for the current address.

   > ⚠️ If you ever see an entry listening on `0.0.0.0`, remove it and re-run the installer — a wildcard rule there breaks **local** play too (one early build did this).

4. **Wrong realmlist on the client PC.** `realmlist.wtf` there must contain the server PC's LAN address (from the LAN Play dialog), not `127.0.0.1`.

5. **Logs in, sees the realm, but gets stuck connecting?** That's the classic symptom of the realm address pointing at the wrong place — do step 2 again.

### Can't connect / wrong realm

Check `realmlist.wtf` contains `set realmlist 127.0.0.1` and that both auth and world servers are running:

```powershell
wsl -d dml-arch -u dml -- docker ps
```

You should see containers for both `authserver` and `worldserver`.

### Compile failed

Check the build log for the last error:

```powershell
wsl -d dml-arch -u dml -- cat ~/playerbots-build.log
```

Common causes:
- **Network drop during clone or Docker build** — re-run the installer
- **Disk full during build** — run `wsl -d dml-arch -u dml -- df -h /home` to check
- **Docker not running** — the installer auto-starts Docker, but if it failed: `wsl -d dml-arch -u root -- systemctl start docker`

### Re-running the installer

Safe to re-run at any time. If compiled images already exist, the installer detects them, skips the 2–4 hour compile, and resumes any unfinished setup steps. To force a complete rebuild from scratch:

```powershell
wsl -d dml-arch -u dml -- bash -c "cd ~/games/wow-server-playerbots && docker compose down -v"
wsl -d dml-arch -u dml -- rm -rf ~/games/wow-server-playerbots
```

Then re-run `Install-WoW-WotLK.ps1`.

> **Warning:** This deletes all game data and player accounts. Back up anything you want to keep first.

### "dml-arch is not installed" error

The DML substrate isn't set up. Run `Install-DML.ps1` first, then re-run this installer.

### "Not enough space" error

Free up disk space on your Windows C: drive, or reduce what's inside dml-arch. Compilation needs 30 GB free inside the WSL2 environment. The WSL2 VHD lives at `%LOCALAPPDATA%\DadsMMOLab\wsl\ext4.vhdx`.

---

## Advanced — Reclaiming Disk Space

The WSL2 disk file grows as needed but never shrinks on its own — even after deleting things inside it. To hand the space back to Windows (safe, but follow the order exactly):

1. Trim the filesystem: `wsl -d dml-arch -u root -- fstrim -v /`
2. Shut down WSL: `wsl --shutdown`
3. In an **Administrator PowerShell**, run `diskpart`, then:
   ```
   select vdisk file="C:\Users\<you>\AppData\Local\DadsMMOLab\wsl\ext4.vhdx"
   attach vdisk readonly
   compact vdisk
   detach vdisk
   exit
   ```

> ⚠️ Do **not** use `wsl --manage dml-arch --set-sparse true --allow-unsafe`, even though some guides suggest it. Microsoft disabled sparse mode by default because it can corrupt the disk image — the diskpart method above achieves the same thing safely.

> **Note:** The first server start after compacting will be slower than usual while the disk file re-expands. That's normal.

---

*Dad's MMO Lab — one-click offline MMO servers for Windows & Steam Deck.*
*youtube.com/@DadsMmoLab*
