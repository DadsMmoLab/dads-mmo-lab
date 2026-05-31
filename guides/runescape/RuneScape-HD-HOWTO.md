# Dad's MMO Lab — RuneScape 2009 HD Upgrade: How-To Guide

**Game:** RuneScape 2009 era (pre-Evolution of Combat)
**Upgrade:** Saradomin Launcher (HD experimental client)
**Platform:** Steam Deck (SteamOS), Desktop Mode + Gaming Mode

---

## What This Upgrade Does

The standard RuneScape 2009 install uses the legacy Java client bundled with 2009scape Singleplayer Edition. It works, but it renders at a fixed small window and doesn't scale cleanly on the Steam Deck's screen.

This upgrade installs the **Saradomin Launcher** — an HD-capable experimental client maintained by the 2009scape team. It replaces the bundled Java client while leaving the server completely untouched.

What you get after the upgrade:

- **HD graphics that actually work** — textures, models, lighting
- **Proper scaling** on the Steam Deck's 1280x800 screen
- **Plugin system** for quality-of-life features
- **A second launcher** (`runescape-hd-launcher.sh`) — the original SD launcher is kept and untouched, you can switch between them any time

---

## Requirements

| Requirement | Details |
|---|---|
| Base install | **`install-runescape.sh` must be done first** — this upgrade only installs the HD client layer on top |
| Disk space | **~200 MB** for the Flatpak (~150MB) + experimental client (~50MB) |
| Internet | Required to download Saradomin and the experimental client (first-run only) |
| Time | **~10 minutes** (mostly the Flatpak download) |

> **You do not need to reinstall the server.** This upgrade adds a new client — your character data, database, and server configuration are untouched.

---

## Step 1 — Run the Upgrade

Open Konsole (Desktop Mode) and run:

```bash
chmod +x ~/Downloads/upgrade-runescape-hd.sh
~/Downloads/upgrade-runescape-hd.sh
```

The upgrade will walk you through each step. Here's what it does:

### What Happens During the Upgrade

**Checking prerequisites**
Verifies that `~/runescape-server/` exists and `server.jar` / `ms.jar` are in place. If not, you'll be told to run `install-runescape.sh` first.

**Installing the Saradomin Launcher via Flatpak (~150MB)**
Saradomin is installed as a user Flatpak (`org._2009scape.Launcher`). If Flatpak itself isn't installed, the script installs it — and correctly re-enables SteamOS's readonly filesystem afterward.

**Pre-configuring Saradomin (localhost + 1280x720)**
Writes Saradomin's config file at:
```
~/.var/app/org._2009scape.Launcher/data/2009scape/config.json
```
This sets the server IP to `localhost` and the render resolution to 1280x720. Saradomin reads this on startup — you won't need to manually configure it.

**Writing the HD launcher**
Creates `~/runescape-hd-launcher.sh`. This starts the same backend (MySQL → ms.jar → server.jar) as the original launcher, then opens Saradomin instead of the bundled Java client.

---

## Step 2 — First Launch

Run the HD launcher once in Desktop Mode to complete Saradomin's one-time setup:

```bash
bash ~/runescape-hd-launcher.sh
```

When Saradomin's window opens:

1. **Click Play** — the server is pre-configured for localhost, no settings to change
2. The experimental client downloads (~50MB, one-time, needs internet)
3. Log in with any username + any password — first login auto-creates the account

> **If Saradomin shows "stable" server** instead of connecting locally: click the gear icon → **Server Profile** → select **local** (or set the IP to `127.0.0.1`). This only happens if the config wasn't written correctly — after setting it once, Saradomin remembers it.

After the first login, the local profile is saved permanently and you can play offline forever.

---

## Step 3 — Add to Steam (Gaming Mode)

### Option A — Swap the existing entry (recommended)

1. In Steam Desktop Mode, right-click your **RuneScape 2009** library entry
2. **Properties → Launch Options**
3. Change:
   ```
   --hold -e bash ~/runescape-launcher.sh
   ```
   to:
   ```
   --hold -e bash ~/runescape-hd-launcher.sh
   ```
4. Compatibility: **Proton OFF** — still native Java, no Proton needed

### Option B — Keep both entries

Add a second non-Steam game pointing to Konsole:
- **Target:** `/usr/bin/konsole`
- **Launch Options:** `--hold -e bash ~/runescape-hd-launcher.sh`
- **Name it:** `RuneScape 2009 HD`
- **Proton:** OFF

This lets you launch either client from your library.

---

## Daily Use — Gaming Mode

1. Launch **RuneScape 2009** (or RuneScape 2009 HD) from your library
2. A terminal opens — wait for: **`SERVER READY — Launching Saradomin`**
3. Saradomin opens and connects automatically
4. Click **Play**
5. Log in with your username

The launcher waits up to 30 seconds for the server to save your character data when you exit.

---

## Saving Your Character

Saving works identically to the SD client — the backend is unchanged:

- The server **auto-saves every ~5 minutes**
- For a reliable save before quitting, use the **in-game Logout button** — don't just close the client window
- The launcher waits for the server to flush saves on exit and prints `✅ Done!` when complete

---

## Switching Between SD and HD

The two launchers share the same server. You can use either one — just don't run both at the same time.

| Launcher | Client | Launch command |
|---|---|---|
| Standard (SD) | Bundled `client.jar` | `bash ~/runescape-launcher.sh` |
| HD | Saradomin + experimental client | `bash ~/runescape-hd-launcher.sh` |

To switch back to SD in Gaming Mode, update the Steam launch options to point at `~/runescape-launcher.sh`. No reinstall, no data loss.

---

## Files and Paths

| Path | What it is |
|---|---|
| `~/runescape-hd-launcher.sh` | HD Gaming Mode launcher |
| `~/runescape-launcher.sh` | Original SD launcher (unchanged) |
| `~/runescape-server/` | Server root (unchanged by upgrade) |
| `~/runescape-server/data/players/` | Character save files |
| `~/.var/app/org._2009scape.Launcher/` | Saradomin Flatpak data |
| `~/.var/app/org._2009scape.Launcher/data/2009scape/config.json` | Saradomin config (IP, resolution) |
| `/tmp/rs-hd-launch.log` | HD launcher runtime log |
| `~/runescape-server/MY_SERVER_HD.txt` | Quick reference card |

---

## Troubleshooting

### "Error: js5connect" in Saradomin

Saradomin is pointed at the public stable server, not your local one. Go to **Settings (gear icon) → Server Profile → local**. If there's no "local" option, manually set the IP to `127.0.0.1`.

This shouldn't happen on a fresh upgrade (config.json is pre-written), but can occur if Saradomin reset its config after an update.

### "Connecting, this may take a long time..." then nothing

The server isn't running. Saradomin launched but the game server didn't start before it. Quit Saradomin and re-launch via the HD launcher — the launcher starts the server first, then opens Saradomin only after the server is ready.

### Saradomin opens at 800x600 / wrong resolution

The config.json resolution setting wasn't applied. Check:
```bash
cat ~/.var/app/org._2009scape.Launcher/data/2009scape/config.json
```
It should show `"width": 1280, "height": 720`. If not, re-run the upgrade or write it manually.

In-game fix: **Settings → Graphics → Display mode → Resizable** — this makes the render resolution match the window size.

### Saradomin won't launch at all

Test it directly:
```bash
flatpak run org._2009scape.Launcher
```
Most common cause: the first launch needs internet to download the experimental client. Once downloaded it's cached and works offline.

### "My character keeps resetting" / saves not working

This is a Java version issue — not caused by the HD upgrade, but inherited from the base install. The server uses Nashorn (Java's built-in JavaScript engine, removed in Java 15) to write character saves. The HD launcher pins to Java 11, which has Nashorn.

Verify Java 11 is installed:
```bash
/usr/lib/jvm/java-11-openjdk/bin/java -version
```

If that path doesn't exist:
```bash
sudo steamos-readonly disable
sudo pacman -Sy jre11-openjdk
sudo steamos-readonly enable
```

### Bundled MySQL won't start

Same diagnosis as the base install — see `RuneScape-2009-HOWTO.md`. The HD launcher uses the same MySQL backend. Check `/tmp/rs-hd-launch.log` for the cause.

### Re-running the upgrade

Safe to re-run at any time. If Saradomin is already installed, the Flatpak step is skipped. The config.json and launcher are always rewritten (idempotent). Run it again if you want to reset to known-good defaults:

```bash
~/Downloads/upgrade-runescape-hd.sh
```

---

*Dad's MMO Lab — one-click offline MMO servers for Steam Deck.*
*youtube.com/@DadsMmoLab*
