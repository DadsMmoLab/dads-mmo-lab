# Dad's MMO Lab — Unified Launcher (PySide6)
## Project "Yu'lon" - The Great Serpent

> Planning & execution document for replacing the shell-script install workflow with a cross-platform desktop application. This revised plan outlines how we can build a better, easier to maintain, system for managing our offline server installers all the while minimizing our reliance on platform-specific scripting and ensuring a consistent user experience across all supported platforms. This will also help us stay within the legal boundaries of software distribution of this kind.

---

## 1. Problem Statement

Dad's MMO Lab currently ships installers and managers as **Bash scripts** (`install-wow-wotlk.sh`, `wow-manage.sh`, `dml-start.sh`) and **PowerShell scripts** (`Install-DML.ps1`). This works for developers but is hostile to the target audience ("dad-friendly", zero Linux knowledge). This in turn has caused a lot of issues for our users and headaches for us. The scope of the project has moved beyond simple scripting to a full-fledged desktop application.

Goals:

1. **No user-facing shell.** Users click buttons, never touch Konsole/PowerShell/WSL.
2. **One unified codebase.** Write once, run on macOS, Windows, and Linux (Arch/Debian/Fedora/Bazzite/SteamOS).
3. **Catalog + Controller.** A catalog of installable servers ("Catalog") that holds the users hand tightly, and a per-install management surface ("Controller").
4. **Data-driven module/mod management.** Modules and mods become JSON manifests fetched from GitHub, so adding content is a data-entry task, not a code task.

---

## 2. Technology Decisions

| Concern | Decision | Rationale |
|---|---|---|
| Language | **Python 3.11+** | No JS, No Rust, No Bash; Python has a large FOSS community and is easy to learn. Matches the existing Bash/PowerShell scripting DNA. Contributor-friendly. |
| UI toolkit | **PySide6 (Qt)** | Avoids the "ugly Python GUI" (Tkinter) reputation; native widgets, QSS styling, cross-platform. |
| Packaging | **PyInstaller** | De-facto standard; one binary per platform. Can be setup with GH Actions for automated builds. |
| CI | **GitHub Actions build matrix** | Required because PyInstaller cannot cross-compile — each platform builds on its own runner. |
| Docker interaction | **Docker CLI via `subprocess`** (Docker SDK later) | 100% parity with existing scripts; `docker compose` semantics preserved. |
| Module catalog (for WotLK) | **JSON manifests** fetched from GitHub | Makes the controller extensible without code changes. |

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

## 4. Package Targets (per platform)

| Platform | Artifact | Tooling | Notes |
|---|---|---|---|
| macOS | `.dmg` | PyInstaller | Built on `macos-latest` runner. |
| Windows | `.exe` (or MSI) | PyInstaller | Built on `windows-latest` runner. |
| Linux (all) | `.AppImage` | PyInstaller + `appimagetool` | **Hero artifact** — one file runs on Arch/Debian/Fedora/Bazzite/SteamOS. |

**Key insight:** the Linux `.AppImage` erases the distro matrix. Ship AppImage first; add `.deb`/`.rpm` only on request. SteamOS (Arch-based) runs AppImages natively — same as The Lab did.

---

## 5. Proposed Project Structure

```
py-launcher/
├── py/
│   ├── Catalog/
│   │   ├── catalog.json          # list of games + install metadata
│   │   └── installer.py          # orchestrates install (deps → clone → build → config)
│   ├── controller/
│   │   ├── docker_ctl.py         # start/stop/status/logs/health
│   │   ├── console.py            # attach to worldserver console
│   │   ├── maintenance.py        # cache clear, backups, SQL changes
│   │   └── modules.py            # reads/writes module JSON manifests
│   ├── runner.py                 # subprocess streaming (shared by all)
│   ├── platform.py               # OS detection + silent Docker/WSL provisioning
│   └── ui/
│       ├── Catalog_view.py
│       ├── controller_view.py
│       └── widgets/
│           └── log_panel.py      # streaming log output widget
├── manifests/                    # module/mod JSON (synced from GitHub)
│   └── wow-wotlk/
│       ├── modules.json
│       └── mods/
│           ├── ah-bot.json
│           ├── solocraft.json
│           └── transmog.json
├── main.py
├── requirements.txt
├── build/                        # PyInstaller specs
│   └── py-launcher.spec
└── .github/workflows/
    └── release.yml               # build matrix → AppImage/dmg/exe
```

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
  "requires": ["playerbots"],
  "description": "Populates the auction house with bot-posted items."
}
```

### Tacit knowledge to port (from `wow-manage.sh`)

`wow-manage.sh` is ~2,300 lines with heavy embedded knowledge that must become manifest fields:

- **Module config key differences** — e.g. `AuctionHouseBot.GUIDs` (AH Bot Plus, plural) vs the original `AuctionHouseBot.GUID` (singular) + `Account` key. This kind of nuance must be explicit in the JSON.
- **SQL apply ordering** and glob patterns per module.
- **Build targets** — which modules require a worldserver rebuild vs. pure-SQL.
- **Dependencies** — e.g. AH Bot requires Playerbots.

---

## 7. Execution Phases (ordered)

> Recommended order: **2 → 1 → 3 → 4 → 5**. Build the testable core first, then data, then UI, then orchestration, then packaging.

### Phase 1 — Foundation (testable Python core, no UI)

- `runner.py` — subprocess wrapper with live line-by-line stdout/stderr streaming.
- `docker_ctl.py` — start/stop/status/logs/health, mirroring `dml-start.sh` (`_wait_db_healthy`, `_wait_ready` polling logic).
- `platform.py` — OS detection; stubs for Docker/WSL provisioning.
- Unit tests proving the core works against a real Docker install.

**Exit criteria:** `docker_ctl.start()` / `stop()` / `status()` work against a running AzerothCore
compose project.

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

- `Catalog_view.py` — browsable catalog of games.
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

---

## 9. Out of Scope (v1)

- **My Party / bot group builder** — was in The Lab; requires in-game/DB work, not script-wrapping.
- **Item database + in-game mail** — separate later milestone.
- **Teleport / GM in-game tools** — later.
- **Full native reimplementation of installers** — start by wrapping existing scripts.

---

## 10. Next Actions

1. Scaffold `py-launcher/` project structure (folders + stub files + `requirements.txt`).
2. Implement `runner.py` + `docker_ctl.py` + `platform.py` (Phase 1).
3. Add unit tests.
4. Add GitHub Actions `release.yml` with the build matrix.
5. Begin Phase 2 (manifest schema + WotLK module port).