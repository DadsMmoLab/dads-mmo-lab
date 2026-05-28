# Dad's MMO Lab — RuneScape 2009 Server: How-To Guide

**Game:** RuneScape 2009 era (pre-Evolution of Combat)
**Server:** 2009scape Singleplayer Edition for Linux
**Platform:** Steam Deck (SteamOS), Desktop Mode + Gaming Mode

---

## What This Installs

A fully offline RuneScape 2009 singleplayer server running natively on your Steam Deck — no Docker, no Proton, no Wine. Pure Java on Linux. Includes:

- **2009scape Singleplayer Edition** — the full 2009 game server, management server, and Java client bundled together
- **Bundled MySQL database** — ships with the repo, no separate install needed
- **Gaming Mode launcher** — one-button start from your Steam library

This is the singleplayer edition. You play solo locally. No other players, no internet connection needed after install.

---

## Requirements

| Requirement | Details |
|---|---|
| Disk space | **500 MB** minimum |
| Java | **Java 11** (installer handles this automatically) |
| Time | **~5 minutes** total |
| Internet | Required for initial clone only |

> **Why Java 11 specifically?** The 2009scape server uses Nashorn (Java's built-in JavaScript engine) to save character data. Nashorn was **removed in Java 15**. If you run the server on Java 17 or 21, saves silently fail and your character resets to the tutorial on every login. The installer installs Java 11 specifically and the launcher pins to it — your system's default Java is not affected.

---

## Step 1 — Run the Installer

Open Konsole (Desktop Mode) and run:

```bash
chmod +x ~/Downloads/install-runescape.sh
~/Downloads/install-runescape.sh
```

The installer takes about 5 minutes and handles everything automatically.

---

## What Happens During Install

### Step 1: Dependencies (~1–2 min)
Installs:
- **Java 11** (`jre11-openjdk`) — pinned version for Nashorn support
- **git + git-lfs** — needed to clone the repo and download JAR files
- **wmctrl + xdotool** — for auto-resizing the client window to Steam Deck native (1280x800)
- **libxcrypt-compat + libaio** — runtime libraries required by the bundled MySQL binary

### Step 2: Clone & Initialize Database (~3 min)
- Clones [2009scape/Singleplayer-Edition-Linux](https://github.com/2009scape/Singleplayer-Edition-Linux) to `~/runescape-server/`
- Downloads JAR files via Git LFS (server.jar, ms.jar, client.jar)
- Starts the bundled MySQL, creates the `global` database, imports world data
- Creates the `data/players/` save directory
- Shuts MySQL back down cleanly

### Step 3: Launcher Setup (~10 sec)
Creates `~/runescape-launcher.sh` and saves a reference card to `~/runescape-server/MY_SERVER.txt`.

---

## Step 2 — Add to Steam (Gaming Mode)

1. Steam → **Add a Non-Steam Game** → browse to `/usr/bin/konsole`
2. Rename to: `RuneScape 2009`
3. Right-click → **Properties** → Launch Options:
   ```
   --hold -e bash ~/runescape-launcher.sh
   ```
4. Compatibility: **Proton OFF** — Java runs natively, no Proton needed

---

## Daily Use — Gaming Mode

1. Launch **RuneScape 2009** from your library
2. A terminal opens — wait for: **`GIELINOR IS OPEN!`**
3. The Java client launches automatically
4. At the login screen: **type any username + any password**
   - First login with a new username creates the account automatically
5. **Click the LEFT button (Standard Detail / SD)** — not HD

> **IMPORTANT — Never click HD.** The legacy 2009scape client cannot load HD assets from a local server and will show "error connecting to server" if HD is selected. Always use Standard Detail (SD). If you accidentally saved HD, delete `~/.runite_rs/preferences.json` and relaunch.

The launcher waits up to 30 seconds for the server to save your character data when you exit.

---

## Saving Your Character

- The server **auto-saves every ~5 minutes**
- For a reliable save before quitting, use the **in-game LOGOUT button** — don't just close the client window
- The launcher monitors for save activity on exit and reports:
  - `✅ Character data saved successfully` — good
  - `⚠️ no new save data was written` — possible issue (see troubleshooting)

---

## Getting Admin Rights (In-Game)

To give your character admin privileges:

```bash
cd ~/runescape-server
./run-linux.sh
# Choose option 4
# Enter your username
```

---

## Files and Paths

| Path | What it is |
|---|---|
| `~/runescape-server/` | Server root |
| `~/runescape-server/server.jar` | Game server |
| `~/runescape-server/ms.jar` | Management server |
| `~/runescape-server/client.jar` | Java client |
| `~/runescape-server/database/` | Bundled MySQL |
| `~/runescape-server/data/players/` | Character save files (JSON) |
| `~/runescape-server/data/global.sql` | World data (restored on re-init) |
| `~/runescape-server/MY_SERVER.txt` | Quick reference card |
| `~/runescape-launcher.sh` | Gaming Mode launcher |
| `/tmp/rs-launch.log` | Runtime log |

---

## Startup Sequence

The launcher starts processes in this order (and shuts them down in reverse):

1. **MySQL** — the database must be up before anything else
2. **ms.jar** — management server (handles account creation, admin tools)
3. **server.jar** — game server
4. **client.jar** — the Java client

On shutdown, the server gets a 30-second grace period to write character saves before MySQL is touched.

---

## Troubleshooting

### "Bundled mysql failed to start" / database won't come up

Check the log:
```bash
cat /tmp/rs-launch.log | grep -i "error\|fail\|missing"
```

Most common causes:

**1. Missing libcrypt.so.1 (most common on SteamOS)**
```bash
sudo steamos-readonly disable
sudo pacman -Sy libxcrypt-compat
sudo steamos-readonly enable
```

**2. Missing libaio**
```bash
sudo pacman -Sy libaio
```

**3. Port 3306 in use** by another database (Docker container, system MariaDB)
```bash
sudo ss -tlnp | grep 3306
sudo systemctl stop mysqld mariadb 2>/dev/null
# Or stop the relevant Docker container
```

**4. Corrupted database** (after a hard crash — destructive fix)
```bash
rm -rf ~/runescape-server/database/data
bash ~/Downloads/install-runescape.sh  # re-initializes the database
```

The launcher auto-detects all of these and prints a `💡 FIX:` line when it spots the cause.

### "Error connecting to server" at login

You clicked HD. Restart the launcher and click SD (Standard Detail). If the client remembered your HD choice:
```bash
rm -f ~/.runite_rs/preferences.json
```

### "My character keeps resetting to the tutorial"

**First check: Java version.** This is the most common cause.

```bash
/usr/lib/jvm/java-11-openjdk/bin/java -version
```

If that path doesn't exist, install Java 11:
```bash
sudo steamos-readonly disable
sudo pacman -Sy jre11-openjdk
sudo steamos-readonly enable
```

The launcher auto-detects Java 11 at `~/.usr/lib/jvm/java-11-openjdk`. Once installed, saves will work.

**Other causes:**
- Playing less than 5 minutes (below the autosave interval)
- Closing the client window instead of using the in-game logout button
- Server didn't have write permission to `~/runescape-server/data/players/`

Check your save files:
```bash
ls -la ~/runescape-server/data/players/
```
You should see `.json` files named after your character.

Check the log for the Nashorn NullPointerException:
```bash
grep -B3 "scriptEngine.*null" /tmp/rs-launch.log
```
If you get hits, it's the Java version issue — install Java 11.

### Git LFS / JARs downloaded as tiny stub files

If `server.jar`, `ms.jar`, or `client.jar` are under 10KB after cloning, Git LFS didn't download the real files. Manual fix:
1. Go to [github.com/2009scape/Singleplayer-Edition-Linux](https://github.com/2009scape/Singleplayer-Edition-Linux)
2. Click **Code → Download ZIP**
3. Extract to `~/runescape-server/`
4. Re-run the installer

### "Launcher closes immediately, Java never opens"

Same diagnosis as database failure above. Check `/tmp/rs-launch.log`.

### Manual start (if launcher fails)

```bash
cd ~/runescape-server
./run-linux.sh
# Option 1 = run game
```

---

## Re-running the Installer

The installer is safe to re-run. If `~/runescape-server/` exists with an initialized database, it skips the clone and database init entirely. To force a complete fresh install:

```bash
rm -rf ~/runescape-server
~/Downloads/install-runescape.sh
```

---

*Dad's MMO Lab — one-click offline MMO servers for Steam Deck.*
*youtube.com/@DadsMmoLab*
