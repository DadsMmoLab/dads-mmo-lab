# Dad's MMO Lab — Project Guide for Developers

## 1. Project Overview

**Dad's MMO Lab** is a community-driven collection of automated installer scripts, Docker configurations, and step-by-step guides for running classic MMO private servers **completely offline** on a Steam Deck, any Linux machine (Arch/Fedora/Ubuntu/Debian), or Windows 10/11 (via WSL2).

The project packages open-source MMO server emulators into copy-paste-friendly, one-command installers. It is aimed at non-developers ("dad-friendly") but is built with solid DevOps practices: Docker containers, shell scripting, and careful cross-platform support.

**Key technologies:**
- **Languages:** Bash (Linux installers), PowerShell (Windows installers), Lua (game mods), YAML/Docker Compose
- **Runtimes:** Docker, Java (RuneScape), various native binaries
- **Frameworks:** Docker Compose for service orchestration
- **Target platforms:** SteamOS (Arch-based), Fedora/Bazzite, Ubuntu/Debian/PopOS!/Mint, Windows 10/11 (WSL2)

**High-level architecture:**
- Each game lives in its own subdirectory under `guides/`
- Every game has at least one installer script (`install-*.sh`) and an HOWTO guide (`*.md`)
- Most servers run inside Docker containers orchestrated via `docker compose`
- A "Gaming Mode launcher" shell script wraps startup, client detection, and auto-shutdown
- Windows support uses a WSL2 substrate (the "DML" environment) with PowerShell automation
- The uninstaller (`guides/uninstall.sh`) is a single, comprehensive menu-driven script covering all games

**Currently supported games (8 complete):** WoW Vanilla, WoW TBC, WoW WotLK (+Unbound add-on), RuneScape 2009 (SD + HD), MapleStory v83, Mu Online, and more in progress.

---

## 2. Getting Started

### Prerequisites
- **Required:** Git, a Unix-like shell (Linux/macOS/WSL2), and for most installers: Docker + Docker Compose
- **Optional:** Steam Deck hardware (nothing is Deck-exclusive; scripts work on any Linux distro), Steam + GE-Proton (for WoW client only)
- **Disk space:** 10-15 GB per game minimum
- **RAM:** 16 GB recommended (Steam Deck spec)

### Installation (for development)

Clone the repository and inspect the installer scripts:

```bash
git clone https://github.com/DadsMmoLab/dads-mmo-lab.git
cd dads-mmo-lab
```

There is no build step for the repository itself — it is a collection of standalone scripts and guides. To test an installer, pick a game and run its script:

```bash
# Example: test the WoW WotLK installer (Steam Deck / Arch)
cd guides/wow-wotlk
chmod +x install-wow-wotlk.sh
./install-wow-wotlk.sh
```

Each installer checks its own prerequisites (Docker, disk space, internet) and will prompt before making changes.

### Running tests

There is no formal test suite. Validation is done manually by running installers on target platforms:
- **Steam Deck (SteamOS):** primary target; use the `install-wow-wotlk.sh` style scripts
- **Fedora/Bazzite:** use `*-fedora.sh` variants
- **Ubuntu/Debian/PopOS!/Mint:** use `*-ubuntu.sh` variants
- **Windows (WSL2):** use the `.ps1` scripts in `guides/DML-Windows/`

---

## 3. Project Structure

```
dads-mmo-lab/
├── .gitignore                  # Ignores .DS_Store and .vscode
├── CONTRIBUTING.md             # Community contribution guidelines
├── DISCLAIMER.md               # Legal disclaimer (not affiliated with Blizzard, etc.)
├── LICENSE-AGPL                # GNU AGPL v3 (matching AzerothCore's license)
├── README.md                   # Project overview, quick start, supported games table
└── guides/
    ├── uninstall.sh            # Master uninstaller — menu-driven script for all games
    ├── Uninstall-HOWTO.md      # Uninstall guide
    ├── wow-vanilla/            # WoW 1.12.1 — CMaNGOS Classic + Playerbots
    │   ├── install-wow-vanilla.sh
    │   └── HOWTO-WOW-VANILLA.md
    ├── wow-tbc/                # WoW 2.4.3 — CMaNGOS TBC + Playerbots
    │   ├── install-wow-tbc.sh
    │   └── WoW-TBC-HOWTO.md
    ├── wow-wotlk/              # WoW 3.3.5a — AzerothCore + Playerbots (most feature-rich)
    │   ├── install-wow-wotlk.sh        # Steam Deck / Arch Linux
    │   ├── install-wow-wotlk-fedora.sh # Fedora / Bazzite
    │   ├── install-wow-wotlk-ubuntu.sh # Ubuntu / Debian / PopOS! / Mint
    │   ├── Install-WoW-WotLK.ps1       # Windows (WSL2)
    │   ├── wow-manage.sh               # Interactive server management menu
    │   ├── dml-start.sh                # Lightweight staged server start
    │   ├── WoW-WotLK-HOWTO.md          # Main install guide
    │   ├── WoW-Wotlk-NETWORKING.md     # LAN/multiplayer configuration
    │   ├── WoW-WotLK-CONTROLS-1.md     # Server management reference pt.1
    │   ├── WoW-WotLK-CONTROLS-2.md     # Server management reference pt.2
    │   ├── WoW-WotLK-CREATE-ACCOUNTS.md
    │   ├── WoW-Playerbots-Windows-HOWTO.md
    │   └── ALE-Kegs/                  # Optional Eluna Lua engine mods
    │       ├── BlackMarketAuctionHouse/
    │       └── SeasonOfDiscovery/
    ├── unbound-wrath/          # Wrath Unbound multi-class add-on (layers on WotLK)
    │   ├── install-wrath-unbound-addon.sh
    │   ├── uninstall-wrath-unbound-addon.sh
    │   └── Wrath-Unbound-Addon-HOWTO.md
    ├── runescape/              # RuneScape 2009 — 2009scape (native Java, no Docker)
    │   ├── install-runescape.sh
    │   ├── upgrade-runescape-hd.sh
    │   ├── HOWTO-RUNESCAPE.md
    │   └── RuneScape-HD-HOWTO.md
    ├── Maplestory/             # MapleStory v83 — Cosmic emulator
    │   ├── install-maplestory.sh
    │   └── MapleStory-v83-HOWTO.md
    ├── Mu-online/              # Mu Online — OpenMU
    │   ├── install-muonline.sh
    │   └── MU-Online-HOWTO.md
    ├── DML-Windows/            # Windows substrate (WSL2 + Arch + Docker + DML CLI)
    │   ├── Install-DML.ps1
    │   ├── Uninstall-DML.ps1
    │   ├── DML-Windows-HOWTO.md
    │   ├── HOWTO-WINDOWS-WSL2.md
    │   └── Uninstall-DML-HOWTO.md
    ├── Steam-Update-Fix/       # Post-SteamOS-update Docker repair script
    │   ├── fix-after-update.sh
    │   └── HOWTO-FIX-AFTER-UPDATE.md
    └── work-in-progress/       # In-development installers (Tortoise WoW, etc.)
```

### Key files and their roles

| File | Purpose |
|------|---------|
| `README.md` | Public-facing project overview, supported games table, quick start |
| `CONTRIBUTING.md` | Guide for community contributors; style rules, ground rules |
| `DISCLAIMER.md` | Legal disclaimer about Blizzard IP and public servers |
| `LICENSE-AGPL` | Full GNU AGPL v3 license text |
| `guides/uninstall.sh` | Master uninstaller — comprehensive menu for removing any/all games |
| `guides/wow-wotlk/dml-start.sh` | The "production" server start script — staged Docker container bring-up |
| `guides/wow-wotlk/wow-manage.sh` | Full interactive server management TUI |

---

## 4. Development Workflow

### Coding conventions

- **Shell scripts:** Bash with `set -euo pipefail` for strict error handling. Use color-coded output functions (`print_success`, `print_error`, `print_info`, `print_warning`). Each game should have a distinct color scheme (e.g., magenta for MapleStory, red for WotLK).
- **PowerShell scripts:** Administrative-grade automation for Windows. Require `Set-ExecutionPolicy Bypass -Scope Process`.
- **Documentation:** Markdown only. "Dad-friendly" language: short sentences, no assumed developer knowledge. Every command block must be copy-pasteable. Always include a troubleshooting section and credit the upstream emulator.
- **Naming conventions:**
  - Installer scripts: `install-<game>-<platform>.sh` or `.ps1`
  - HOWTO guides: `<Game>-<Platform>-HOWTO.md`
  - Launcher scripts generated for users: `~/<game>-launcher.sh`
  - Server directories generated for users: `~/<game>-server/` or `~/<game>-server-playerbots/`

### Adding a new game

1. Create a new subdirectory under `guides/` (e.g., `guides/new-game/`)
2. Write the `install-*.sh` following the established pattern:
   - Color definitions and helper functions at the top
   - `check_system()` — verify platform, disk space, internet
   - `install_docker()` — Docker setup with pacman keyring handling
   - Main install logic — clone/build/configure the emulator
   - Gaming mode launcher generation
   - `show_completion()` summary
3. Write the HOWTO `.md` guide in "dad-friendly" style
4. Add an uninstall function to `guides/uninstall.sh`
5. Add the game to the status table and supported games list in `README.md`
6. Test on all applicable platforms before submitting a PR

### Testing approach

- Manual testing on actual hardware is the standard
- Each installer should be tested on a clean (freshly installed) system when possible
- Key scenarios to test: first install, re-run (skip compile), start/stop cycle, Gaming Mode launch, uninstall
- The `dml-start.sh` script provides the reference implementation for staged Docker bring-up; test against it

### Build and deployment

No CI/CD exists. The project is a collection of scripts distributed via GitHub. Users download scripts directly or clone the repo. The raw GitHub URLs are used for `curl` one-liners in documentation.

---

## 5. Key Concepts

### Domain terminology

| Term | Meaning |
|------|---------|
| **DML** | Dad's MMO Lab — the project itself |
| **Playerbots** | AI players that populate the game world, run dungeons, and simulate a living server |
| **Gaming Mode** | Steam Deck's console-style UI (as opposed to Desktop Mode) |
| **Launcher script** | A generated shell script (`~/<game>-launcher.sh`) that starts the server, detects the game client, and auto-shuts down when the client closes |
| **ALE-Kegs** | Optional Eluna Lua Engine mods (Black Market AH, Season of Discovery) for WotLK |
| **Proton / GE-Proton** | Wine-based compatibility layers for running Windows games on Linux/Steam Deck |
| **WSL2** | Windows Subsystem for Linux v2 — used for the Windows substrate installer |

### Core abstractions

- **Installers are idempotent:** Running an installer again detects the existing server directory and skips the compile phase, only restarting the server
- **Docker as an isolation layer:** Most servers run in Docker containers. This means clean removal (`docker compose down -v`), reproducible environments, and no dependency conflicts between games
- **Launcher scripts as lifecycle managers:** Each game gets a generated launcher that handles start → wait-for-ready → detect-client → auto-shutdown. This is the primary user-facing abstraction
- **The uninstaller is the "master controller":** `guides/uninstall.sh` knows about every possible game and can surgically remove any combination

### Design patterns

- **Staged container bring-up:** Servers start in order: database → wait for healthy → auth + world. See `dml-start.sh` for the reference implementation
- **Platform detection + per-distro installers:** Rather than one script with complex branching, each supported distro family gets its own installer file. Shared patterns are copy-pasted (not abstracted into shared libraries)
- **Color-coded games:** Each game has a distinct terminal color scheme to distinguish it in the uninstaller menu and during installation
- **Confirmation gates:** Destructive operations (uninstall, keyring reset) require typing exact confirmation strings (`YES`, `DELETE ALL`, `yes`)

---

## 6. Common Tasks

### Testing an installer on Steam Deck

```bash
# 1. Clone the repo
cd ~/Downloads
git clone https://github.com/DadsMmoLab/dads-mmo-lab.git

# 2. Run an installer
cd dads-mmo-lab/guides/wow-wotlk
chmod +x install-wow-wotlk.sh
./install-wow-wotlk.sh

# 3. View build logs
tail -f ~/playerbots-build.log

# 4. Check server status
docker ps
docker compose -f ~/wow-server-playerbots/compose.yml logs -f
```

### Writing a new installer from scratch (template approach)

1. Copy the simplest existing installer as a starting point (e.g., `guides/Mu-online/install-muonline.sh` for a Docker-based game)
2. Update the game name, server directory path, ports, and color scheme
3. Replace the "clone and build" section with the new emulator's setup steps
4. Update the launcher script template to detect the new game's client process
5. Test end-to-end: install → start → connect with client → close client → verify auto-shutdown

### Adding an uninstall entry for a new game

Edit `guides/uninstall.sh` and add:

1. A status check entry in the `show_status()` function
2. An `uninstall_<game>()` function following the existing patterns (stop containers, remove dirs, remove launchers, prune volumes)
3. A menu entry in `main_menu()` with the next available number
4. Add to the `uninstall_all()` function's dirs array and launchers array

### Updating the README supported games table

Edit the table in `README.md` under `## ✅ Currently Working`. Keep columns consistent: Game, Emulator, Bot Support, Status, Guide link. Also update `## 📦 What's In This Repo` with the new game's file listing.

---

## 7. Troubleshooting

### Common issues in development

**Installer fails with "Docker not running"**
- On Steam Deck: run `sudo systemctl start docker` or use the `fix-after-update.sh` script
- On Fedora: `sudo systemctl start docker`
- On Ubuntu: `sudo service docker start`

**Pacman keyring is broken (Steam Deck)**
- Symptom: `error: required key missing` or signature errors when installing Docker
- Fix: Run `guides/Steam-Update-Fix/fix-after-update.sh` or manually run:
  ```bash
  sudo steamos-readonly disable
  sudo rm -rf /etc/pacman.d/gnupg
  sudo pacman-key --init
  sudo pacman-key --populate archlinux
  sudo pacman-key --populate holo
  sudo pacman -Sy docker docker-compose
  ```

**Docker Compose v1 vs v2**
- The project uses `docker compose` (v2 plugin) not `docker-compose` (v1 standalone)
- If a user has podman installed, its `docker-compose` shim can break things — the `Clean Docker environment` option in the uninstaller handles this

**Server compiles but won't start**
- Check `docker compose logs` for the specific game
- Verify port conflicts: each game must use unique ports (check existing port assignments in README tables before assigning new ones)
- Ensure `docker compose down -v` was run before re-installing (stale volumes can cause conflicts)

**Game client can't connect**
- Verify `realmlist.wtf` (or equivalent) is set to `127.0.0.1`
- Check that the auth/login server is running on the expected port
- For WoW: verify the realm address in the database matches (`docker exec <db-container> mysql -e "SELECT * FROM acore_auth.realmlist;"`)

### Debugging tips

- All installers write build logs to predictable locations (e.g., `/tmp/maple-build.log`, `~/playerbots-build.log`)
- Use `docker compose logs -f` (not just `docker logs`) to see live output from all containers in a stack
- The `dml-start.sh` script includes detailed logging — use it as a reference for expected server startup behavior
- For launcher script debugging, check `/tmp/<game>-launch.log`

---

## 8. References

### Upstream emulator projects

| Emulator | URL | License |
|----------|-----|---------|
| AzerothCore (WoW WotLK) | <https://github.com/azerothcore/azerothcore-wotlk> | AGPL-3.0 |
| CMaNGOS (WoW Vanilla/TBC) | <https://github.com/cmangos> | GPL-2.0 |
| 2009scape (RuneScape) | <https://github.com/2009scape/2009scape> | Various |
| Saradomin Launcher (RS HD) | <https://flathub.org/apps/org._2009scape.Launcher> | — |
| Cosmic (MapleStory v83) | <https://github.com/P0nk/Cosmic> | — |
| OpenMU (Mu Online) | <https://github.com/MUnique/OpenMU> | AGPL-3.0 |
| mod-playerbots | <https://github.com/mod-playerbots/mod-playerbots> | — |

### Project resources

- **YouTube channel:** <https://youtube.com/@DadsMmoLab>
- **Reddit (author):** <https://reddit.com/u/Kingspoken>
- **Support (ko-fi):** <https://ko-fi.com/dadsmmolab>
- **The Lab (GUI companion app):** <https://github.com/0xVe1L/the-lab>

### Platform references

- **Steam Deck / SteamOS:** Arch Linux derivative; uses pacman, systemd, and a read-only root filesystem
- **GE-Proton:** <https://github.com/GloriousEggroll/proton-ge-custom> — needed for running Windows game clients
- **WSL2 documentation:** <https://learn.microsoft.com/en-us/windows/wsl/>