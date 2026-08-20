# Dad's MMO Lab — Unified Launcher (PySide6)
## Project "Yu'lon" - The Great Serpent

> Planning & execution document for replacing the shell-script install workflow with a cross-platform desktop application. This revised plan outlines how we can build a better, easier to maintain, system for managing our offline server installers all the while minimizing our reliance on platform-specific scripting and ensuring a consistent user experience across all supported platforms. This will also help us stay within the legal boundaries of software distribution of this kind.

---

## 1. Problem Statement

Dad's MMO Lab currently ships installers and managers as **Bash scripts** (`install-wow-wotlk.sh`, `wow-manage.sh`, `dml-start.sh`) and **PowerShell scripts** (`Install-DML.ps1`). This works for developers but is hostile to the target audience ("dad-friendly", zero Linux knowledge). This in turn has caused a lot of issues for our users and headaches for us. The scope of the project has moved beyond simple scripting to a full-fledged desktop application.

Goals:

1. **No user-facing shell.** Users click buttons, never touch Konsole/PowerShell/WSL — including networking (LAN & internet play) auto-setup (§13).
2. **One unified codebase.** Write once, run on macOS, Windows, and Linux (Arch/Debian/Fedora/Bazzite/SteamOS).
3. **Catalog + Controller.** A catalog of installable servers ("Catalog") that holds the users hand tightly, and a per-install management surface ("Controller").
4. **Data-driven module/mod management.** Modules and mods become JSON manifests fetched from GitHub, so adding content is a data-entry task, not a code task.
5. **The app installs everything.** Every dependency is silently installed and verified by the app — including **Docker itself** (Docker Engine on Linux, Docker Desktop on Windows/macOS), the **virtualization layer it needs** (WSL2 on Windows, the Linux VM on macOS), the open-source emulator server (cloned from its official repo), any modules/mods, and even the app's own runtime. The user never installs anything by hand — not Docker, not WSL, not Python. See §3a (what the app may and may not install) and §3b (how the platform dep stack, Docker, WSL/VM, and Python are each handled).

**v1 scope — four servers.** Until v1 is complete, the Catalog/Controller focuses on exactly four servers: **WoW** (Vanilla 1.12), **WoW TBC**, **WoW WotLK**, and **WoW Tortoise** (the Turtle-WoW solo fork). These are the only acronyms/`game` ids the v1 Catalog needs to know about; other games in the wider DML catalogue are explicitly out of v1 scope (README's planned/in-progress lists) and are only added after v1 ships. **This is a target, not current state** — as of Phase 1, only WotLK has a controller package; see §5's status note and `roadmap.md` §3.1 for the catalog work that will cover the other three.

---

## 2. Technology Decisions

| Concern | Decision | Rationale |
|---|---|---|
| Language | **Python 3.11+** | No JS, No Rust, No Bash; Python has a large FOSS community and is easy to learn. Matches the existing Bash/PowerShell scripting DNA. Contributor-friendly. |
| UI toolkit | **PySide6 (Qt)** | Avoids the "ugly Python GUI" (Tkinter) reputation; native widgets, QSS styling, cross-platform. |
| Packaging | **PyInstaller** | De-facto standard; one binary per platform. Can be setup with GH Actions for automated builds. |
| CI | **GitHub Actions build matrix** | Required because PyInstaller cannot cross-compile — each platform builds on its own runner. |
| Docker interaction | **Docker CLI via `subprocess`** (Docker SDK later) | 100% parity with existing scripts; `docker compose` semantics preserved. |
| Module & ALE catalog (for WotLK) | **JSON manifests** fetched from GitHub | Makes the controller extensible without code changes. |
| Modification Catalog (all servers) | **JSON manifests** fetched from GitHub | Makes the controller extensible without code changes. Focus on simple database and GM commands that can be run to modify the server. |

---

## 3. Critical Constraint: Docker Requires a Linux Kernel

The app drives Docker, which runs Linux containers (AzerothCore `ac-worldserver` is a Linux ELF).
There is **no native-Windows or native-macOS way** to run these containers. Therefore:

| Platform | What the app must silently provision |
|---|---|
| **Linux** (any distro) | Docker Engine directly — no virtualization layer needed. |
| **Windows** | Docker Desktop (WSL2 backend) — app installs/verifies both, user never sees "WSL". |
| **macOS** | Docker Desktop (runs a Linux VM) — app installs/verifies it. |

**Promise to users:** "Click install." — the app hides Docker/WSL/virtualization entirely.
**Reality to engineers:** the virtualization layer cannot be removed on Windows/macOS; it is only *hidden*.

This means each installer carries platform-specific "ensure a Linux container environment exists" logic, but the **application code remains 100% shared**.

---

## 3a. Legal & Distribution Boundaries

The intro to this document promises the app helps "stay within the legal boundaries of software distribution of this kind." That promise must be enforced as a concrete rule on the Catalog/Installer design, matching the project's existing ethos (see root `README.md`: "open source emulators only — no copyrighted assets, no game files distributed"):

- **The app never downloads, bundles, or redistributes copyrighted game client files or assets.** The Catalog only ever installs: open-source emulator server software (cloned/pulled from its official repo), platform dependencies (Docker, its virtualization backend — see §3b), Docker images, and configuration. Nothing proprietary.
- **The user always supplies their own legally obtained client.** The installer's job is to *locate* an existing client install (or prompt the user to point at one / acquire one themselves) — never to fetch it on the user's behalf.
- **Manifests must not reference or link to piracy sources.** Any `repo` field in a module/mod manifest must point at a legitimate open-source project (e.g. `azerothcore/mod-ah-bot`), never a ROM/warez site.
- **This rule applies uniformly across all games in the Catalog**, not just WotLK — it should be checked as part of Phase 3's `installer.py` design and Phase 2's manifest schema validation (reject any manifest whose `repo` isn't an allowed source).

---

## 3b. Platform Dependencies Are Installed Automatically (Docker, WSL/VM, and Python)

Goal 5 (§1) says "the app installs everything." Concretely, this means all three layers of the
platform dependency stack are provisioned automatically, never by the user:

### Docker (the container runtime)

- **Linux (any distro):** the app installs **Docker Engine** directly (via the distro's package
  manager, e.g. `pacman`/`apt`/`dnf`), then verifies the daemon is running. No virtualization
  layer is needed on Linux.
- **Windows:** the app installs **Docker Desktop**, whose backend is **WSL2** — it silently
  provisions WSL2 first, then Docker Desktop, then verifies both. The user never sees "WSL".
- **macOS:** the app installs **Docker Desktop**, which runs the Linux containers inside a
  **Linux VM** that Docker Desktop manages itself.

### The virtualization layer (Windows/macOS only)

- This is a *consequence* of §3's kernel constraint — Linux containers can't run natively on
  Windows/macOS — so provisioning Docker Desktop already implies provisioning its VM/WSL2 backend.
  The app treats this as one silent, atomic "make containers work" operation, not a separate step
  the user must be aware of.

### Python (the app's own runtime)

- **End users never install Python.** Each distributable artifact (`.AppImage` / `.dmg` / `.exe`,
  §4) is built with **PyInstaller**, which bundles a self-contained Python interpreter and all
  third-party packages (PySide6, etc.) directly into the binary. The user runs one file: no
  `python3` install, no `pip`, no virtualenv.
- **The Python-install burden is shifted to the build machine, not the user.** Only the CI build
  runner (GitHub Actions, §4) needs a real Python toolchain; the shipped artifact carries what it
  needs.
- **This supersedes the developer-facing setup path** — `pylauncher/development.md`'s
  "`python3 -m venv .venv`" instructions exist *only* for contributors running from source; they
  are never part of the end-user experience.
- **If a platform can't bundle Python** (e.g. a future distribution format forbidding
  self-contained runtimes), the app falls back to *silently detecting and installing* the smallest
  available Python runtime the same way it provisions Docker — still no user-facing shell.

---

## 4. Package Targets (per platform)

| Platform | Artifact | Tooling | Notes |
|---|---|---|---|
| macOS | `.dmg` | PyInstaller | Built on `macos-latest` runner. |
| Windows | `.exe` (or MSI) | PyInstaller | Built on `windows-latest` runner. |
| Linux (all) | `.AppImage` | PyInstaller + `appimagetool` | **Hero artifact** — one file runs on Arch/Debian/Fedora/Bazzite/SteamOS. |

**Key insight:** the Linux `.AppImage` erases the distro matrix. Focus on appimage as it should run *fine* on most linux distros that people actually use.

---

## 5. Proposed Project Structure

> This reflects the structure actually scaffolded in `pylauncher/` (see repo). Each package has an `__init__.py`; manifests are indexed by a top-level JSON per category, with individual mod/module files added under `mods/` as Phase 2 data-porting work produces them (not created empty ahead of time).
>
> **Two naming fixes discovered during Phase 0 setup, applied and reflected below:**
> 1. The top-level package is `yulon/`, not `py/` — a package literally named `py` shadows the
>    third-party `py` PyPI package that `pytest` itself depends on internally, which crashes
>    pytest with `AttributeError: module 'py' has no attribute 'path'`. This is not a style
>    preference; it's a real, reproducible bug.
> 2. Per-game controller packages use underscores, not hyphens (`controller_wow_wotlk/`, not
>    `controller-wow-wotlk/`) — a directory name containing a hyphen is not a valid Python
>    package name and cannot be imported (`import controller-wow-wotlk` is a syntax error; `mypy`
>    also rejects it: "contains `__init__.py` but is not a valid Python package name"). This
>    corrects style-guide §6a's own kebab-case-for-non-Python-directories guidance, which didn't
>    account for the fact that these directories *are* real Python packages.

```
pylauncher/
├── yulon/
│   ├── __init__.py
│   ├── catalog/
│   │   ├── __init__.py
│   │   ├── catalog.json          # list of games + install metadata
│   │   └── installer.py          # orchestrates install (deps → clone → build → config)
│   ├── controller_wow_wotlk/     # each server has its own controller package for siloing
│   │   ├── __init__.py
│   │   ├── controller.py         # WotlkController(Controller) — supplies SPEC, inherits the rest (Phase 1.4)
│   │   ├── docker_ctl.py         # start/stop/status/logs/health
│   │   ├── console.py            # attach to worldserver console
│   │   ├── maintenance.py        # cache clear, backups, SQL changes
│   │   └── modules.py            # reads/writes module JSON manifests
│   ├── controller.py             # base Controller: ContainerSpec + server dir, start guarded by §12 (Phase 1.4)
│   ├── runner.py                 # subprocess streaming (shared by all)
│   ├── docker.py                 # shared Docker lifecycle + port-conflict check (shared)
│   ├── platform.py               # OS detection + silent Docker/WSL provisioning
│   ├── log.py                    # shared logging convention (get_logger/configure — Phase 0.6)
│   └── ui/
│       ├── __init__.py
│       ├── catalog_view.py
│       ├── controller_view.py
│       └── widgets/
│           ├── __init__.py
│           └── log_panel.py      # streaming log output widget
├── manifests/                    # module/mod JSON (synced from GitHub)
│   └── wow-wotlk/
│       ├── modules.json          # index; per-module files added here in Phase 2
│       ├── ale.json              # index; per-ALE-mod files added here in Phase 2
│       ├── mods.json             # index; per-mod files (ah-bot.json, solocraft.json, transmog.json, ...) added in Phase 2
│       └── kegs/                 # Unique to WotLK for LUA mods with ALE
│           └── account-wide.json
├── tests/                        # pytest suite (see pyplan/roadmap.md Phase 0/1)
│   ├── __init__.py
│   ├── fixture.md                # pinned AzerothCore compose fixture (Phase 0.4)
│   ├── test_setup_sanity.py
│   ├── test_log.py               # covers yulon/log.py
│   ├── test_runner.py            # covers yulon/runner.py (Phase 1.1)
│   ├── test_platform.py          # covers yulon/platform.py (Phase 1.2)
│   ├── test_docker.py            # covers yulon/docker.py + WotLK docker_ctl (Phase 1.3)
│   └── test_controller.py        # covers yulon/controller.py + WotlkController (Phase 1.4)
├── main.py
├── requirements.txt
├── requirements-dev.txt          # pytest, mypy, black, ruff — pinned dev tooling
├── pyproject.toml                # black/ruff/mypy/pytest config
├── development.md                # contributor setup doc
├── build/                        # PyInstaller specs
│   └── pylauncher.spec
└── .github/workflows/
    ├── ci.yml                    # lint + type-check + test on every push/PR
    └── release.yml               # build matrix → AppImage/dmg/exe
```

**Status as of this writing:** only `controller_wow_wotlk/` exists. The other three v1 servers
(§1 goal 5's "four servers") — WoW, WoW TBC, and WoW Tortoise — do **not** have controller
packages, catalog entries, or manifests yet; that work is tracked in Phase 3 (`roadmap.md` §3.1)
and beyond, not implemented as of Phase 1. Each will get its own `controller_<acronym>/` package
(`controller_wow_vanilla/`, `controller_wow_tbc/`, `controller_wow_tortoise/`) following the same
file layout and underscore naming, in that order, before any server outside v1's four-server
scope is considered (see style-guide §6 for acronym conventions).

---

## 6. The Manifest Schema (for WotLK)

The entire project succeeds or fails on the module/mod manifest format. It must capture everything `wow-manage.sh` currently hardcodes as **data**, not code.

```json
{
  "id": "ah-bot",
  "name": "Auction House Bot",
  "type": "module",
  "game": "wow-wotlk",
  "repo": "azerothcore/mod-ah-bot",
  "branch": "master",
  "sql": ["data/sql/db-world/*.sql"],
  "conf": "conf/ahbot.conf",
  "build_targets": ["MODULES=mod-ah-bot"],
  "requires": [],
  "description": "Populates the auction house with bot-posted items."
}
```

### Tacit knowledge to port (from `wow-manage.sh`)

`wow-manage.sh` is ~2,300 lines with heavy embedded knowledge that must become manifest fields:

- **Module config key differences** — e.g. `AuctionHouseBot.GUIDs` (AH Bot Plus, plural) vs the original `AuctionHouseBot.GUID` (singular) + `Account` key. This kind of nuance must be explicit in the JSON.
- **SQL apply ordering** and glob patterns per module.
- **Build targets** — which modules require a worldserver rebuild vs. pure-SQL.
- **Dependencies** — e.g. `mod-player-bot-level-brackets` requires the Playerbots module to function (verified in `wow-manage.sh`; AH Bot has no such dependency — an earlier draft of this doc incorrectly used AH Bot as the dependency example).

---

## 7. Execution Phases (ordered)

> Recommended order: **2 → 1 → 3 → 4 → 5**. Build the testable core first, then data, then UI, then orchestration, then packaging.

### Phase 1 — Foundation (testable Python core, no UI)

- `runner.py` — subprocess wrapper with live line-by-line stdout/stderr streaming.
- `docker_ctl.py` — start/stop/status/logs/health, mirroring `dml-start.sh` (`_wait_db_healthy`, `_wait_ready` polling logic).
- `platform.py` — OS detection; stubs for Docker/WSL provisioning.
- Unit tests using **`pytest`**: mock `subprocess` calls in `runner.py` (no real Docker required to test control flow), then a smaller set of integration tests that exercise `docker_ctl.py` against a real running AzerothCore compose project.

**Exit criteria:** `docker_ctl.start()` / `stop()` / `status()` work against a running AzerothCore
compose project, and the mocked `pytest` suite passes in CI without Docker present.

### Phase 2 — Manifest schema & data port

- Define `modules.json` + per-mod JSON schema.
- Port the WotLK modules out of `wow-manage.sh` into the JSON manifests.
- `modules.py` — load/validate/fetch manifests from GitHub.

**Exit criteria:** the module list and their config nuances are fully expressed as JSON, no code changes
needed to add a module.

### Phase 3 — Catalog (catalog + installer)

- `catalog.json` — game list with install metadata (repo, script, ports, client steps).
- `installer.py` — orchestrates install. **Phase 3a:** shells out to existing `install-*.sh`. **Phase 3b (later):** reimplements install natively in Python.
- Silent Docker/WSL provisioning on Windows/macOS.

**Exit criteria:** clicking "install" for one game completes a working server with zero shell
interaction, verified on at least one platform.

### Phase 4 — Controller UI (PySide6)

- `catalog_view.py` — browsable catalog of games.
- `controller_view.py` — one tab per install: docker lifecycle, live console, maintenance (cache clear, backups, SQL), module/mod management (driven by manifests).
- `log_panel.py` — reusable streaming output widget.

**Exit criteria:** full start/stop/logs/accounts/module-toggle workflow via GUI only.

### Phase 5 — Windows/macOS provisioning + packaging

- Complete the silent Docker Desktop / WSL2 provisioning paths.
- PyInstaller specs for `.AppImage` / `.dmg` / `.exe`.
- GitHub Actions `release.yml` build matrix.

**Exit criteria:** `git push` produces all three platform artifacts automatically.

---

## 8. Known Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Python packaging is painful | Standardize on PyInstaller + CI matrix from day one; treat AppImage as the only Linux artifact. |
| PyInstaller cannot cross-compile | Build matrix — each platform on its own runner. Never attempt local cross-builds. |
| Docker Desktop silent provisioning is fragile | Detect + verify with clear, retryable checks; offer manual-install fallback dialog. |
| Porting `wow-manage.sh` tacit knowledge | Do it as an explicit Phase 2 with exit criteria; don't fold it into the UI work. |
| "No WSL" expectation vs reality | Communicate honestly in docs (WSL is hidden, not removed). |
| Scope creep on bot-party/item-mail features | Treat in-game tools (My Party, item mail, teleport) as a *separate later project*; they are not part of the launcher. |
| Unsigned binaries trigger OS gatekeeper warnings (Windows SmartScreen, macOS Gatekeeper) | For v1, document the click-through steps for users. Revisit code signing/notarization once the project has a budget/identity for certificates; track as a post-v1 milestone, not a blocker. |

---

## 9. Out of Scope (v1)

- **My Party / bot group builder** — was in The Lab; requires in-game/DB work, not script-wrapping.
- **Item database + in-game mail** — separate later milestone.
- **Teleport / GM in-game tools** — later.
- **Full native reimplementation of installers** — start by wrapping existing scripts.
- **Code signing / notarization** — accept OS gatekeeper warnings for v1; revisit later (see §8).

---

## 10. Application Self-Update

Users download this app directly (AppImage/exe/dmg) with no package manager tracking updates, so the app must check for new versions itself.

- On launch, check the GitHub Releases API for a newer tag than the running `__version__`.
- If found, show a non-blocking banner/dialog with a link/button to download the new artifact for the user's platform.
- **v1 scope:** check + notify only. Auto-download/auto-replace-the-running-binary is deferred — replacing a running AppImage/exe from within itself is fiddly per-platform and not worth the risk before the core app is stable.
- Revisit in-place auto-update once Phase 5 packaging is proven reliable.

---

## 11. Local Data & Config Storage

The app needs a per-OS location to persist its own state — remembered server install paths, last-used settings, cached manifests — separate from the server data itself (which lives under the user-chosen server directory, e.g. `~/wow-server-playerbots/`).

| Platform | Config/state directory |
|---|---|
| Linux | `~/.local/share/yulon/` (XDG Base Dir convention) |
| Windows | `%APPDATA%\yulon\` |
| macOS | `~/Library/Application Support/yulon/` |

- Store: known install locations per game, chosen client paths, last-checked update version, cached copies of fetched manifests (with an ETag/timestamp for refresh).
- This directory is **not** the server directory — never conflate app state with server/database files. Server files stay wherever the user chose during install (matching existing script behavior, e.g. `choose_install_dir()` in `install-wow-wotlk.sh`).
- Owned by `platform.py` (add a `config_dir()` helper alongside `detect()`); implemented in Phase 1, consumed by Catalog/Controller from Phase 3 onward.

---

## 12. Single-Instance / Port Conflict Rule

Existing docs are explicit: **"Only run ONE server at a time — they share the same ports"** (`WoW-WotLK-CONTROLS-1.md`). The Controller must enforce this instead of silently reproducing the shell version's foot-gun:

- Before starting a server, the Controller checks whether another managed install is already running (via `docker_ctl.status()` across all known installs) and whether the required ports (e.g. 3724, 8085) are free.
- If a conflict is found, the UI blocks the "Start" action and clearly tells the user which install is already running and needs to be stopped first — no raw port-in-use errors surfaced from Docker.
- This check belongs in the shared `docker_ctl.py` / `runner.py` layer (Phase 1) so every per-game controller inherits it for free, rather than each `controller_<acronym>/` reimplementing the check.

---

## 13. Networking Auto-Setup (LAN & Internet Play)

Goal 1 (§1) promises "no user-facing shell." That extends to getting other players connected —
the most error-prone, shell-heavy part of the current experience, covered today by the manual
guide `archive/guides/wow-wotlk/WoW-Wotlk-NETWORKING.md`. The Controller automatically sets up
both modes, surfacing only clear prompts/status rather than terminal commands.

**Two modes, two levels of automation:**

| Mode | Audience | What the app automates | What it cannot automate |
|---|---|---|---|
| **LAN** (same Wi-Fi) | Friends/household on the local network | Detect local IP; set `realmlist` `address`/`localAddress` (ports 3724/8085) in the auth DB; open the host firewall (UFW / firewalld / `netsh` + Windows "Private" network check); on WSL2, add `netsh interface portproxy` if ports are bound to `127.0.0.1`; write `realmlist.wtf` for each client | Nothing — LAN is fully automatable |
| **Internet play** (anyone, anywhere) | Remote friends | Everything in LAN mode, plus: detect the public IP; set `realmlist` `address` to it | Router admin — DHCP reservation, port forwarding (TCP 3724/8085). The app **detects and prompts** for these steps (with router-specific pointers) rather than silently failing; a router without UPnP cannot be configured by the app |

**Cross-platform realities the app must handle** (all documented in the existing guide):
- **Windows/WSL2:** the `172.x.x.x` WSL2 address is *not* the LAN address — always use the
  Windows IPv4 from `ipconfig`. The app must read the Windows host address, not the WSL2 guest.
- **Ports must be bound to `0.0.0.0`, not `127.0.0.1`**, or LAN players can't connect. The app
  verifies the compose port bindings and warns/fixes before starting.
- **CGNAT** (mobile/LTE ISPs) blocks port forwarding entirely — the app detects a failed
  forwarding attempt and explains it clearly rather than reporting a silent timeout.
- **Dynamic public IP** — the app re-checks the public IP on change and offers a free dynamic-DNS
  option (DuckDNS) for persistent hosting, mirroring the guide's "Dynamic IP Warning."

**Design constraints:**
- This is **data + orchestration, not UI or per-game hardcoding.** The port table (auth 3724,
  world 8085, optional db 3306 — see the guide's "Port Reference") and the per-OS firewall
  commands belong in `catalog.json`/manifests and a `platform.py` firewall helper, following
  style-guide §3 (separation of concerns), §4 (DRY — one shared implementation, not one per game).
- **UI copy may spell out for the user what the app is doing** (style-guide §6's "one exception"),
  but logs/identifiers stay acronym-only.
- **Sequenced as its own step** in `roadmap.md` Phase 3/4 (installer + controller wiring), since
  it depends on both the shared port-conflict check (§12) and the UI's controller surface.

---

## 14. Next Actions

1. ~~Scaffold `pylauncher/` project structure (folders + stub files + `requirements.txt`).~~ — **done**.
2. Implement `runner.py` + `docker_ctl.py` + `platform.py` for real (Phase 1), including the single-instance/port-conflict check (§12) and `config_dir()` helper (§11).
3. Add `pytest` unit tests (mocked `subprocess`) and a small integration suite against a real Docker/AzerothCore install.
4. Flesh out `.github/workflows/release.yml` build matrix (currently a placeholder AppImage packaging step) and `build/pylauncher.spec`.
5. Begin Phase 2: manifest schema finalization, WotLK module port from `wow-manage.sh`, and manifest `repo` allow-list validation (§3a).
6. Design the self-update check (§10) as part of Phase 1's `platform.py`/`main.py` wiring, even though the UI hook lands later.
7. Implement networking auto-setup (§13) — LAN fully automated; internet play with router-step detection/prompting — as part of the Phase 3 installer and Phase 4 controller surface.