# MapleStory v83 — Dad's MMO Lab HOWTO

**Server:** Cosmic v83  
**Installer version:** 1.2.1  
**Platform:** Steam Deck (SteamOS) — Desktop Mode required for install

---

## What You're Getting

A fully offline MapleStory v83 server running on your Steam Deck, powered by [Cosmic](https://github.com/P0nk/Cosmic) — the most complete v83 emulator, built on over a decade of community development.

**Version 83** is the pre-Big Bang era: slower leveling, real grinding, classic classes, and the world as old-school Maplers remember it. Henesys, Perion, Kerning City, El Nath — all of it, completely offline.

**Server settings (applied by installer):**
- World: Scania only (no accidental 1x worlds)
- EXP / Meso / Drop / Boss Drop: 10x
- Quest rate: 5x
- Travel rate: 10x (fast transport)
- Channels: 3
- Auto-register: ON — just type any username/password at login
- PIN / PIC prompts: disabled

---

## Before You Start

You need **three client files** (the installer walks you through each one). All are linked from the [Cosmic GitHub README](https://github.com/P0nk/Cosmic):

| File | Where to get it |
|------|----------------|
| `MapleGlobal-v83-setup.exe` | Cosmic README → Google Drive link |
| `CosmicWZ-[date]-v[version].zip` | Cosmic README → CosmicWZ download |
| `HeavenMS-localhost-WINDOW.exe` | Cosmic README → hostr.co link |

**Disk space:** ~10 GB free required  
**Internet:** Required for install (GitHub clone + Docker pull)

---

## Running the Installer

```bash
chmod +x install-maplestory.sh
./install-maplestory.sh
```

The installer handles everything server-side automatically (~10–15 minutes, mostly Java compilation). It will then walk you through the client setup steps interactively.

---

## Client Setup (Step by Step)

### Step 1 — Install the base game

1. Add `MapleGlobal-v83-setup.exe` to Steam as a Non-Steam game
2. In Properties → Compatibility, force **any** Proton version temporarily
3. Run it — this installs the base v83 client files
4. After install, **delete these files** from the MapleStory folder:
   - `HShield/` (entire folder)
   - `ASPLnchr.exe`
   - `MapleStory.exe`
   - `Patcher.exe`

### Step 2 — Apply Cosmic WZ files

1. Extract `CosmicWZ-[date]-v[version].zip`
2. Copy **all `.wz` files** into your MapleStory install folder
3. Replace existing files when prompted

### Step 3 — Add the localhost client

1. Copy `HeavenMS-localhost-WINDOW.exe` into your MapleStory install folder
2. This is the exe you'll actually use to play — it points to `127.0.0.1`

> **Antivirus note:** Windows antivirus will flag `HeavenMS-localhost-WINDOW.exe` as suspicious. This is a **false positive** — it's flagged because it's a reverse-engineered executable patched to connect to localhost. The MapleStory private server community has used this file safely for years. Add an exclusion before downloading if needed.

### Step 4 — Install GE-Proton

MapleStory v83 uses **DirectX 8** — standard Proton lacks D3D8 support. GE-Proton includes DXVK 2.4+ which handles D3D8 natively.

1. Open the **Discover** app in Desktop Mode
2. Search for **ProtonUp-Qt** and install it
3. Open ProtonUp-Qt → Add Version → select **GE-Proton** (latest) → Install

### Step 5 — Add client to Steam

1. Open Steam in Desktop Mode
2. Games → Add a Non-Steam Game → Browse
3. Navigate to your MapleStory folder → select `HeavenMS-localhost-WINDOW.exe`
4. Click Add Selected Programs

### Step 6 — Configure GE-Proton

1. Find `HeavenMS-localhost-WINDOW` in your Steam library
2. Right-click → Properties → **Compatibility** tab
3. Check **Force the use of a specific compatibility tool**
4. Select **GE-Proton** (the version you just installed)

### Step 7 — Set Launch Options

1. In the same Properties window → **General** tab
2. Set **Launch Options** to:

```
%command%
```

That's it. MapleStory v83 is hardcoded to 800×600, and **Gaming Mode scales it to fill the Deck screen automatically** (correct 4:3 proportions, black bars on the sides) — you don't need to do anything for scaling.

> ⚠️ **Do NOT add a `gamescope …` launch option for Gaming Mode play.** Gaming Mode is *already* a gamescope session, and nesting another gamescope inside it can **hang the client at startup** on some Decks. It's also unnecessary — the outer compositor already scales the game.
>
> **Desktop Mode only:** if you launch the game from Desktop Mode (no compositor to scale for you), *then* use:
> ```
> gamescope -w 800 -h 600 -W 1280 -H 800 -f -- %command%
> ```

---

## Setting Up the Gaming Mode Launcher

The installer creates `~/maplestory-launcher.sh`. Add it to Steam so you can start the server from Gaming Mode:

1. Open Steam in Desktop Mode
2. Games → Add a Non-Steam Game → Browse to `/usr/bin/` → select **konsole**
3. Find konsole in your library → right-click → Properties
4. **Rename it:** `MapleStory Server`
5. **Launch Options:**
   ```
   --hold -e bash ~/maplestory-launcher.sh
   ```
6. **Do NOT enable Proton** on this shortcut — the launcher is a bash script, not a Windows exe

**How it works:** Launch "MapleStory Server" first → it starts the Cosmic server and waits for "Maple World is open" → press the Steam button and launch `HeavenMS-localhost-WINDOW` → play → when you close the client, the server shuts down automatically.

---

## Creating Your Account

MapleStory auto-registers accounts. At the login screen:

- Type **any username** you want
- Type **any password** you want
- The account is created automatically — no GM commands needed

---

## Server Details

| Setting | Value |
|---------|-------|
| Login server | `127.0.0.1:8484` |
| Channel 1 | `127.0.0.1:7575` |
| Channel 2 | `127.0.0.1:7576` |
| Channel 3 | `127.0.0.1:7577` |
| World | Scania |
| EXP rate | 10x |
| Meso rate | 10x |
| Drop rate | 10x |
| Boss drop rate | 10x |

---

## Useful Commands

```bash
# Start server manually
cd ~/maplestory-server && docker compose up -d

# Stop server
cd ~/maplestory-server && docker compose down

# Watch live server logs
cd ~/maplestory-server && docker compose logs -f

# Check running containers
docker ps

# Check server startup log
docker logs maplestory-server-maplestory-1 | tail -20
```

---

## Troubleshooting

**Server doesn't start / "Cosmic is now online" never appears**
- Check logs: `docker compose logs -f` from `~/maplestory-server/`
- First launch is slow — database initialisation takes 3–5 minutes

**Client hangs / black screen at startup in Gaming Mode**
- You almost certainly have a `gamescope …` launch option set. Remove it — set Launch Options to just `%command%`. Gaming Mode is already a gamescope session and nesting another one inside it hangs the client. (It works in Desktop Mode but not Gaming Mode — that's the tell.)

**Client shows "Cannot connect to server"**
- Confirm the server is running: `docker ps` should show two containers
- Confirm you're using `HeavenMS-localhost-WINDOW.exe`, not `MapleStory.exe`
- Make sure you launched the "MapleStory Server" shortcut first and waited for "ready"

**Launcher doesn't detect client / server doesn't shut down on exit**
- The launcher detects MapleStory by its process (`HeavenMS-localhost-WINDOW`). Confirm you're launching that exe via Steam (with GE-Proton).

**Client window is tiny / wrong resolution (Desktop Mode)**
- In **Gaming Mode** scaling is automatic — nothing to set.
- In **Desktop Mode** only, add the gamescope option from Step 7 to scale it.

**Graphics glitches or portal frame drops**
- A `dxvk.conf` is included in your MapleStory client folder with optimised settings
- If it's missing, create `dxvk.conf` in the MapleStory folder with:
  ```
  d3d8.maxFrameRate = 60
  dxgi.maxFrameLatency = 1
  ```

**"Eating cheese" tooltip on items / NPCs say "Greetings" only**
- Not applicable for this installer (Cosmic handles scripting internally)

---

## Links

- Cosmic server: https://github.com/P0nk/Cosmic
- Dad's MMO Lab YouTube: https://youtube.com/@DadsMmoLab
- Dad's MMO Lab GitHub: https://github.com/DadsMmoLab/dads-mmo-lab
- Support the channel: https://ko-fi.com/dadsmmolab
