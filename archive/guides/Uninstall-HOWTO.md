# Dad's MMO Lab — Uninstaller: How-To Guide

**Tool:** Dad's MMO Lab Master Uninstaller
**Platform:** Steam Deck (SteamOS), Desktop Mode

---

## What This Does

The uninstaller is a menu-driven tool that cleanly removes any game server installed by Dad's MMO Lab. It handles everything automatically:

- Stops running server processes and Docker containers
- Removes Docker volumes (the database lives in a volume, not just a folder)
- Deletes the server folder and all game data
- Removes the Gaming Mode launcher from your home folder
- Cleans up any game-specific extras (Flatpaks, client caches, PostgreSQL data, `/etc/hosts` patches)

**This is permanent.** Character data, progress, and all server files are gone after uninstall. There is no undo.

---

## Supported Games

The uninstaller covers every game in the project:

| # | Game | Server folder removed |
|---|------|-----------------------|
| 1 | WoW: Wrath of the Lich King | `~/wow-server-playerbots/` |
| 2 | WoW: Vanilla 1.12 | `~/wow-vanilla-server/` |
| 3 | WoW: The Burning Crusade | `~/wow-tbc-server/` |
| 4 | Dark Age of Camelot | `~/daoc-server/` |
| 5 | Ragnarok Online | `~/ro-server/` |
| 6 | Monster Hunter Frontier Z | `~/mhf-server/` + `~/mhf-pgdata/` |
| 7 | MapleStory v83 | `~/maplestory-server/` |
| 8 | EverQuest 1 | `~/eq1-server/` |
| 9 | Tibia | `~/tibia-server/` |
| 10 | Lineage 2 | `~/lineage2-server/` |
| 11 | Final Fantasy XI | `~/ffxi-server/` |
| 12 | Star Wars Galaxies | `~/swg-server/` |
| 13 | Ultima Online | `~/uo-server/` + `~/ClassicUO/` (optional) |
| 14 | RuneScape 2009 | `~/runescape-server/` + SD launcher + HD launcher + Saradomin Flatpak |
| 15 | PSO Blue Burst | `~/pso-server/` |
| 16 | MU Online | `~/muonline-server/` |
| 17 | LEGO Universe | `~/lego-server/` |

Plus a **Clean Docker Environment** tool and a **nuclear "Uninstall Everything" option**.

---

## Step 1 — Run the Uninstaller

Open Konsole (Desktop Mode) and run:

```bash
chmod +x ~/Downloads/uninstall.sh
~/Downloads/uninstall.sh
```

The menu opens immediately, showing which games are currently detected as installed.

---

## The Menu

```
╔══════════════════════════════════════════════════╗
║   🗑️  DAD'S MMO LAB — UNINSTALLER  v1.2.0       ║
╚══════════════════════════════════════════════════╝

  ✅ WoW: Playerbots (WotLK)
  ✅ RuneScape 2009
  ·  Monster Hunter Frontier Z
  [...]

━━━ World of Warcraft ━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   1)  WoW: Playerbots (WotLK)
   2)  WoW: Vanilla (1.12)
   3)  WoW: The Burning Crusade
━━━ Classic MMOs ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   4)  Dark Age of Camelot
   [...]
━━━ Tools ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
   D)  Clean Docker environment
━━━ Nuclear Option ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  ALL)  Uninstall EVERYTHING

   Q)  Quit
```

Green ✅ = game is installed. Dim · = not found. You can only uninstall what's installed — choosing a number for a missing game does nothing harmful.

---

## Uninstalling a Single Game

1. Type the number for the game and press ENTER
2. A summary shows what will be deleted
3. **Type `YES` exactly** (capital letters, nothing else) to confirm
4. Anything else cancels — nothing is deleted

That's it. The uninstaller stops the servers, removes all files, and returns you to the menu.

---

## What Each Game Uninstall Removes

### WoW (Vanilla, TBC, WotLK)
- Docker containers and named volumes (the MariaDB database)
- Server folder (`~/wow-*-server/`)
- Docker image built during compile (`dml/cmangos-*:local`) — reclaimed disk space
- Dangling Docker build-cache images
- Gaming Mode launcher (`~/wow-*-launcher.sh`)

### RuneScape 2009
- Server folder (`~/runescape-server/`) including bundled MySQL and all character saves
- SD Gaming Mode launcher (`~/runescape-launcher.sh`)
- HD Gaming Mode launcher (`~/runescape-hd-launcher.sh`)
- Saradomin HD Launcher Flatpak (`org._2009scape.Launcher`)
- Saradomin config and data (`~/.var/app/org._2009scape.Launcher/`)
- SD client cache (`~/.runite_rs/`)

### Monster Hunter Frontier Z
- Erupe binary and server folder (`~/mhf-server/`)
- PostgreSQL data directory (`~/mhf-pgdata/`) — lives separately from the server folder
- Gaming Mode launcher (`~/mhf-launcher.sh`)
- `/etc/hosts` patch (if present — SteamOS updates erase this automatically)

### Ultima Online
- Server folder (`~/uo-server/`) and Docker volumes
- Gaming Mode launcher (`~/uo-launcher.sh`)
- ClassicUO client folder (`~/ClassicUO/`) — offered separately with its own YES/NO prompt

### All other games
- Server folder + Docker volumes + Gaming Mode launcher

---

## 🐳 Clean Docker Environment

Found under option **D** in the menu. Use this if a game installer failed because Docker is misconfigured — typically:

- **Podman is installed** and its docker-compose shim is overriding real Docker, causing `docker compose` to fail or behave wrong
- **A broken docker-compose plugin** is present at `~/.docker/cli-plugins/docker-compose`

The tool diagnoses both issues, asks for `YES` confirmation, removes the problem software, and optionally installs real Docker + Compose.

You don't need to use this tool unless a game installer specifically told you Docker wasn't working.

---

## 💀 Uninstall Everything

Found under option **ALL** in the menu. The nuclear option — removes every game server found on the machine in a single pass.

**Confirmation required:** Type `DELETE ALL` (exact, with the space) to proceed. Anything else cancels.

What it does in order:
1. Stops all running Docker containers
2. Kills native processes (Erupe for MHF, newserv for PSO, RuneScape Java servers)
3. Runs `docker compose down -v` on every server stack
4. Deletes all server directories
5. Removes all extra data: `~/mhf-pgdata/`, `~/ClassicUO/`, `~/.runite_rs/`, Saradomin Flatpak and data
6. Removes all Gaming Mode launchers
7. Prunes all Docker volumes
8. Cleans any `/etc/hosts` patches

---

## What Is NOT Removed

The uninstaller only removes things it put there. It does not touch:

- **Your game client files** (`~/Games/`, Proton prefixes, etc.) — you own these
- **Steam, Docker, Java, or other system tools** that were installed as dependencies — removing them could break other things
- **SteamOS system files** — everything runs in user space
- **Other installed games** — each game is removed individually unless you use ALL

---

## Re-installing After Uninstalling

Safe to re-run any installer after uninstalling. All installers check for existing data and skip steps that are already done — or ask if you want to start fresh.

```bash
# Example: re-install RuneScape from scratch
~/Downloads/install-runescape.sh
```

---

## Troubleshooting

### "Nothing gets deleted — it says cancelled"

You need to type `YES` exactly — capital letters, no spaces, nothing else. Lowercase `yes` or `Yes` won't work.

### Docker container won't stop

If `docker compose down` hangs, open another Konsole tab and run:
```bash
docker stop $(docker ps -q)
docker rm -f $(docker ps -aq)
```
Then re-run the uninstaller.

### "sudo: steamos-readonly: command not found" warnings

Harmless on non-SteamOS Linux. The uninstaller checks for SteamOS's read-only filesystem toggle but skips it gracefully if not present.

### Server folder still exists after uninstall

The uninstaller uses `sudo rm -rf` for directories that may contain root-owned files (from Docker builds). If it fails, you can manually remove:
```bash
sudo rm -rf ~/[game]-server
```

### Saradomin won't uninstall automatically

Run manually:
```bash
flatpak uninstall --user org._2009scape.Launcher
```

---

*Dad's MMO Lab — one-click offline MMO servers for Steam Deck.*
*youtube.com/@DadsMmoLab*
