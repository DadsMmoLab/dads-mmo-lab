# Dad's MMO Lab — Unified Launcher (PySide6)
## Project "Yu'lon" - The Great Serpent

> Planning & execution document for replacing the shell-script install workflow with a cross-platform desktop application. This revised plan outlines how we can build a better, easier to maintain, system for managing our offline server installers all the while minimizing our reliance on platform-specific scripting and ensuring a consistent user experience across all supported platforms. This will also help us stay within the legal boundaries of software distribution of this kind.

---

## Document map (where each planning doc lives)

There are **three `README.md`s**, each with a distinct audience, plus the `pyplan/` companions with a deliberate division of labour:

| File | Role |
|---|---|
| `README.md` (repo root) | Project landing page — license, ethos, and the guide index. |
| `pyplan/README.md` (this file) | **The design doc**: *what* to build and *why*. Precedence on architecture. |
| `pylauncher/README.md` | **User-facing**: what the shipped app does under the hood. |
| `pyplan/style-guide.md` | **The code rules**: *how* to write it. Precedence on style. |
| `pyplan/roadmap.md` | **The execution plan**: *what order* to do it in. Clean plan only — no decisions or notes. |
| `pyplan/checklist.md` | **The checkable progress tracker**: completed `[x]` / pending `[ ]` items per phase, each tick citing its gate folder. |
| `pyplan/bug-checklist.md` | **The defect register**: numbered findings from reviews and hunts, each closed against a commit or carried as known. |
| `pyplan/write-ledger.md` | **Every write the app makes outside its own folder**, by function, with what it touches and under which running-world state it refuses. |
| `.notes/` (not in the repository) | **The maintainers' local working notes**: gate evidence, tickets, session handoffs, hunt logs, the Phase 7 plans and the Phase 8 working papers. Kept out of the public tree (`.gitignore`) because they name our own machines, accounts and paths. A `.notes/...` path cited in code or in these pages points there. |
| `pyplan/phase6-decisions.md` | **The Phase 6 decisions log ("why")**: reasoning, rejections, incident history. |
| `pyplan/phase7-decisions.md` | **The Phase 7 decisions log**: one install engine for all four servers; what it overturns in the pages above, by name. |
| `pyplan/phase8-decisions.md`, `phase8-parity-decisions.md`, `phase8-delta.md` | **Phase 8's scope and decisions**: the decisions log, the parity decisions across the four trees, and the delta against the Rust launcher. The kickoff brief is in `.notes/archive/`. |
| `pyplan/phase8-owner-answers-2026-09-08.md`, `phase8-exit-review-2026-09-09.md` | **Owner answers and the exit review** — decision sources cited from code and tests, kept beside the phase. |

**Precedence (restated once, here and in the other two):** `README.md` wins on *what/why*;
`style-guide.md` wins on *how*; `roadmap.md` wins on *order*. If they conflict, flag it — don't
silently pick one.

The division that matters most for drift control: **avoid putting a decision in the roadmap, avoid
putting narrative in the checklist, and avoid putting status in the design doc.** When content
starts to migrate, move it to the column above it, not into a fourth place.

---

## 1. Problem Statement

Dad's MMO Lab currently ships installers and managers as **Bash scripts** (`install-wow-wotlk.sh`, `wow-manage.sh`, `dml-start.sh`) and **PowerShell scripts** (`Install-DML.ps1`). This works for developers but is hostile to the target audience ("dad-friendly", zero Linux knowledge). This in turn has caused a lot of issues for our users and headaches for us. The scope of the project has moved beyond simple scripting to a full-fledged desktop application.

Goals:

1. **No user-facing shell.** Users click buttons, never touch Konsole/PowerShell/WSL — including networking (LAN & internet play) auto-setup (§13).
2. **One unified codebase.** Write once, run on macOS, Windows, and Linux (Arch/Debian/Fedora/Bazzite/SteamOS).
3. **Catalog + Controller.** A catalog of installable servers ("Catalog") that holds the users hand tightly, and a per-install management surface ("Controller").
4. **Data-driven module/mod management.** Modules and mods become JSON manifests fetched from GitHub, so adding content is a data-entry task, not a code task.
5. **The app installs everything.** Every dependency is silently installed and verified by the app — including **Docker itself** (Docker Engine on Linux, Docker Desktop on Windows/macOS), the **virtualization layer it needs** (WSL2 on Windows, the Linux VM on macOS), the open-source emulator server (cloned from its official repo), any modules/mods, and even the app's own runtime. The user never installs anything by hand — not Docker, not WSL, not Python. See §3a (what the app may and may not install) and §3b (how the platform dep stack, Docker, WSL/VM, and Python are each handled).

**v1 scope — four servers.** Until v1 is complete, the Catalog/Controller focuses on exactly four servers: **WoW** (Vanilla 1.12), **WoW TBC**, **WoW WotLK**, and **WoW Tortoise** (the Turtle-WoW solo fork). These are the only acronyms/`game` ids the v1 Catalog needs to know about; other games in the wider DML catalogue are explicitly out of v1 scope (README's planned/in-progress lists) and are only added after v1 ships. **This is a target, not current state** — all four are described in `catalog.json` (Phase 3.1), but only WotLK has a controller package; see §5's status note and `roadmap.md` §7.1–7.3 for the work that covers the other three.

---

## 2. Technology Decisions

| Concern | Decision | Rationale |
|---|---|---|
| Language | **Python 3.11+** | No JS, No Rust, No Bash; Python has a large FOSS community and is easy to learn. Matches the existing Bash/PowerShell scripting DNA. Contributor-friendly. |
| UI toolkit | **PySide6 (Qt)** | Avoids the "ugly Python GUI" (Tkinter) reputation; native widgets, QSS styling, cross-platform. |
| Packaging | **PyInstaller** | De-facto standard; one binary per platform. Can be setup with GH Actions for automated builds. |
| CI | **GitHub Actions build matrix** | Required because PyInstaller cannot cross-compile — each platform builds on its own runner. |
| Docker interaction | **Docker CLI via `subprocess`** (not the Docker SDK — see style-guide §7) | 100% parity with existing scripts; `docker compose` semantics preserved. |
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

**Runtime strategy:** use **Docker Desktop** as the container runtime on **both** Windows and
macOS — it already owns the Linux VM (macOS) and the WSL2 backend (Windows), so the app only
provisions Docker Desktop and drives `docker compose` against it. The kernel constraint is
satisfied by Docker Desktop itself; no bespoke VM/WSL2 manager is built. (Full rationale in
`pyplan/phase6-decisions.md`.)

This means the provisioning layer (`platform.ensure_docker()`) carries platform-specific "ensure
a Linux container environment exists" logic, but the **application code remains 100% shared** —
including the installer. From Phase 7 (`pyplan/phase7-decisions.md`; a target as of 2026-08-26,
landing step by step from 7.1) there is **one typed Python install engine for every server on
every platform**: a shared staged/resumable spine and
one engine per emulator family (AzerothCore for WotLK; CMaNGOS for TBC, Vanilla and Tortoise),
with every per-game fact in `catalog.json` and templates. The bash installers that Phases 3–6
wrapped on Linux are retired, one live gate at a time.

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

- **End users never install Python.** Each distributable artifact (`.AppImage` / `.dmg` / the
  Windows zip, §4) is built with **PyInstaller**, which bundles a self-contained Python
  interpreter and all third-party packages (PySide6, etc.) alongside the launcher executable. On
  Linux and macOS that is one file; on Windows the zip unpacks to a folder the user runs
  `yulon.exe` from. No platform needs a `python3` install, `pip`, or a virtualenv.
- **The Python-install burden is shifted to the build machine, not the user.** Only the CI build
  runner (GitHub Actions, §4) needs a real Python toolchain; the shipped artifact carries what it
  needs.
- **This supersedes the developer-facing setup path** — `CONTRIBUTING.md`'s
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
| Windows | zipped one-dir build (`yulon.exe` + its runtime files) | PyInstaller | Built on `windows-latest` runner; `release.yml` zips `dist/yulon`. |
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
│   │   ├── catalog.json          # the four v1 servers: emulator sources (+ dest), install family + native blocks, containers, ports, DBs, client (Phase 3.1 → 7.3; `script` fields gone at 7.2)
│   │   ├── catalog.py            # typed Catalog/CatalogEntry models + load_catalog(); entry.container_spec() feeds the controller
│   │   ├── installer.py          # InstallOptions, the error types, the InstallEngine protocol, installer_for() (dispatch by family); never runs a subprocess itself (Phase 3.2 → 7.2)
│   │   ├── native.py             # the install SPINE: StagedInstaller, Stage, Seams, state file + hint semantics, guard, preflight lines, generic clone/compose/build/start/ready stage bodies (Phase 6.2 → 7.1)
│   │   ├── families/             # one engine per emulator family; stage ORDER is Python here, stage PARAMETERS are catalog data (Phase 7)
│   │   │   ├── __init__.py       # FAMILIES: the one place a family id becomes a class
│   │   │   ├── azerothcore.py    # AzerothCoreInstaller — WotLK's stages, names unchanged and pinned by a test
│   │   │   ├── cmangos.py        # CmangosInstaller — TBC, Vanilla and Tortoise from one class and three catalog entries
│   │   │   ├── clientdir.py      # pure validation of the user's client folder → preflight Checks (refuse / warn)
│   │   │   ├── dockerfile.py     # render the per-game Dockerfile.tmpl with the generated-file marker
│   │   │   ├── extract.py        # client-data extraction: one docker run per tool, client mounted :ro, per-tool evidence; the mmaps stage
│   │   │   ├── conf.py           # ini-style .conf patching (pure patch() + materialise from the image once)
│   │   │   └── sqlplan.py        # ordered SQL plan over docker exec -i, per-phase error policy, completion marker, MarkerGate probe/reset
│   │   ├── preflight.py          # gather() the machine's facts, pure evaluate() against catalog.json's floors → refuse / warn / unchecked / pass (Phase 6.2; client folder + SELinux facts → 7.1/7.3)
│   │   └── composegen.py         # pure generation of the three compose files + .env keys from templates; tokens and image refs come from the entry (Phase 6.2 → 7.3)
│   ├── controller_wow_wotlk/     # each server has its own controller package for siloing
│   │   ├── __init__.py
│   │   ├── controller.py         # WotlkController(Controller) — supplies SPEC, inherits the rest (Phase 1.4)
│   │   ├── docker_ctl.py         # start/stop/status/logs/health
│   │   ├── console.py            # attach to worldserver console
│   │   ├── maintenance.py        # cache clear, backups, SQL changes
│   │   ├── modules.py            # binds the shared store/fetcher/applier to WotLK (game id, bundled dir, DB container)
│   │   ├── accounts.py           # writes the SRP6 registration row through DockerSql — the one account path that works on all three platforms (Phase 6.5)
│   │   └── repair.py             # answers docker.ImportProbe: does this install's database look imported? (Phase 6.5 repair / re-import)
│   ├── controller.py             # base Controller: ContainerSpec + server dir, start guarded by §12 (Phase 1.4)
│   ├── install_wiring.py         # installer_for_app(): the one place the AC import probe/reset is wired into an engine; also the CLI harness `python -m yulon.install_wiring` (Phase 7.1)
│   ├── runner.py                 # subprocess streaming (stream/run) + interact(): a pty with a marker-recognised prompt (used by the script path today; by provisioning's once-only sudo-password step → 7.1)
│   ├── docker.py                 # shared Docker lifecycle + port-conflict check (shared)
│   ├── git.py                    # clone/update git sources: RunnerGit (the host's git) or ContainerGit (git in a container, for hosts without one) (Phase 6.2)
│   ├── platform.py               # OS detection, config_dir, §13 helpers (firewall/IP/portproxy/CGNAT) + 5.1 provisioning: ensure_docker()/ensure_wsl2() → ProvisionReport (Docker Engine via pacman/apt/dnf/zypper, WSL2 + Docker Desktop on Windows, Docker Desktop on macOS; dry_run plans)
│   ├── networking.py             # §13 orchestration: plan() (pure) + apply() for LAN/internet play, realmlist UPDATE, router-step prompts, client realmlist writer (Phase 3.4)
│   ├── log.py                    # shared logging convention (get_logger/configure — Phase 0.6)
│   ├── state.py                  # per-user app state (state.json under config_dir: remembered installs) (Phase 4)
│   ├── resources.py              # bundle_root/manifests_dir/installers_dir for source checkouts AND PyInstaller builds (Phase 5.2, 6.0)
│   ├── update.py                 # GitHub Releases check → UpdateCheck; -Public tags only, once a day, decimal version order (Phase 5.4, T90)
│   ├── update_state.py           # update.json under config_dir: last check, ETag, cached feed, skipped version (T90)
│   ├── selfupdate/               # installing a new build of Yu'lon itself — Qt-free (T90 plan 3)
│   │   ├── detect.py             # which of the five install kinds this process is, and its artifact name
│   │   ├── layout.py             # the marked .yulon-new/.yulon-old work dirs, shipped entries, the instance lock, the helper's nonce/lock/stand-down names
│   │   ├── fetch.py              # download with progress + cancel under a stall watchdog; SHA256SUMS parse and verify
│   │   ├── stage.py              # unpack INSIDE the install under a size bound, decide which entries may be replaced
│   │   ├── swap.py               # the sh/PowerShell helper that moves the entries AFTER this process exits; paths as argv; one armed attempt at a time
│   │   ├── cleanup.py            # first start after a swap: remove .yulon-old, or report a half-done one
│   │   └── apply.py              # the sequence, with every refusal leaving the install untouched
│   ├── manifest.py               # the manifest schema: pydantic models + repo allow-list (Phase 2.1)
│   ├── manifest_store.py         # load manifests from a tree + refresh from GitHub with ETags (Phase 2.3)
│   ├── apply.py                  # declarative apply engine: manifest → install/configure/remove steps (Phase 2.3)
│   └── ui/
│       ├── __init__.py
│       ├── catalog_view.py       # tiles from catalog.json; Install → folder prompts → Installer streamed into the LogPanel (Phase 4.2)
│       ├── controller_view.py    # per-install tabs Server/Console/Modules/Networking over ControllerServices seams (Phase 4.3)
│       └── widgets/
│           ├── __init__.py
│           ├── log_panel.py      # streaming log output widget: QThread worker → line/finished signals (Phase 4.1)
│           ├── job.py            # ThreadedJobRunner: one-shot background jobs, so a long service call never runs on the GUI thread
│           └── prompt.py         # asks the user for a line a subprocess wants (sudo's password): worker → GUI thread → back (Phase 6.1.5)
├── catalog/                      # data the app EXECUTES (roadmap 6.0; archive/guides is for humans)
│   └── installers/               # templates per game; NO install-*.sh after Phase 7.2 (they are deleted, not archived)
│       ├── shared/cmangos/       # base/override/build.yml.tmpl for the whole CMaNGOS family, tokenised (Phase 7.3)
│       ├── steam-deck/           # setup-gaming-mode.sh — the one surviving shell script (Steam Deck launcher), not an install stage
│       ├── wow-wotlk/native/     # base/override/build.yml.tmpl (Phase 6.2)
│       ├── wow-tbc/native/       # Dockerfile.tmpl + dockerignore.tmpl
│       ├── wow-vanilla/native/   # Dockerfile.tmpl + dockerignore.tmpl
│       └── wow-tortoise/native/  # Dockerfile.tmpl (compiles inside the image) + dockerignore.tmpl
├── manifests/                    # module/mod JSON (synced from GitHub)
│   ├── schema/
│   │   └── manifest.schema.json  # JSON Schema generated from yulon/manifest.py (Phase 2.1)
│   └── wow-wotlk/
│       ├── modules.json          # index (ids); per-module files in modules/<id>.json (Phase 2.2)
│       ├── ale.json              # index; per-ALE-mod files in ale/<id>.json
│       ├── mods.json             # index; per-SQL-mod files in mods/<id>.json
│       └── kegs.json             # index; per-keg files in kegs/<id>.json — kegs are the in-repo ALE bundles (bmah, sod)
├── tests/                        # pytest suite (see pyplan/roadmap.md Phase 0/1)
│   ├── __init__.py
│   ├── fixture.md                # pinned AzerothCore compose fixture (Phase 0.4)
│   ├── test_setup_sanity.py
│   ├── test_log.py               # covers yulon/log.py
│   ├── test_runner.py            # covers yulon/runner.py (Phase 1.1)
│   ├── test_platform.py          # covers yulon/platform.py (Phase 1.2)
│   ├── test_docker.py            # covers yulon/docker.py + WotLK docker_ctl (Phase 1.3)
│   ├── test_controller.py        # covers yulon/controller.py + WotlkController (Phase 1.4)
│   ├── test_manifest.py          # covers yulon/manifest.py (Phase 2.1)
│   ├── test_manifest_store.py    # covers yulon/manifest_store.py + the WotLK modules.py binding (Phase 2.3)
│   ├── test_apply.py             # covers yulon/apply.py (Phase 2.3)
│   ├── test_catalog.py           # covers yulon/catalog/catalog.py + catalog.json (Phase 3.1)
│   ├── test_installer.py         # covers catalog/installer.py dispatch + options (the script-path half is deleted in Phase 7.2)
│   ├── test_spine.py             # covers catalog/native.py: state file, guard, ask forwarding (Phase 7.1)
│   ├── test_families_azerothcore.py  # the WotLK engine; pins the stage-name tuple (Phase 7.1)
│   ├── test_families_cmangos.py  # the CMaNGOS engine against a Recorder machine + tmp server dir (Phase 7.3)
│   ├── test_clientdir.py         # pure client-folder validation rules (Phase 7.3)
│   ├── test_conf.py              # byte-for-byte ini patching incl. match_commented (Phase 7.3)
│   ├── test_sqlplan.py           # glob expansion, natural sort == ls -v, gzip, into_each, marker hash (Phase 7.3)
│   ├── test_extract.py           # per-tool completion records + count evidence, evidence-file mismatch, retry trigger (Phase 7.3)
│   ├── test_dockerfile.py        # marker rule for the rendered Dockerfile (Phase 7.3)
│   ├── test_catalog_invariants.py  # every entry: templates exist, ports in one file, BIND_LABEL on every host bind, no game literal in the family module (Phase 7.3)
│   ├── test_networking.py        # covers yulon/networking.py + platform §13 helpers + docker.published_bindings (Phase 3.4)
│   ├── conftest.py               # offscreen QApplication fixture (QT_QPA_PLATFORM=offscreen) for the UI tests
│   ├── test_state.py             # covers yulon/state.py
│   ├── test_console.py           # covers controller_wow_wotlk/console.py (docker attach transport, fake Popen)
│   ├── test_log_panel.py         # covers ui/widgets/log_panel.py (Phase 4.1)
│   ├── test_catalog_view.py      # covers ui/catalog_view.py (Phase 4.2)
│   ├── test_controller_view.py   # covers ui/controller_view.py (Phase 4.3)
│   ├── test_resources.py         # covers yulon/resources.py (source + frozen layouts)
│   ├── test_update.py            # covers yulon/update.py: what one answer means (Phase 5.4, T90)
│   ├── test_update_state.py      # covers yulon/update_state.py: update.json, two writers, one file (T90)
│   ├── test_update_cache.py      # covers check_with_cache: once a day, ETag, skip (T90)
│   ├── test_update_widgets.py    # covers the update bar and the what's-new dialog (T90)
│   ├── test_provision.py         # covers platform.ensure_docker/ensure_wsl2 plans per OS through seams (Phase 5.1)
│   └── integration/              # live-Docker suite, marked `integration`, self-skipping without a daemon (Phase 1.5)
│       ├── conftest.py           # docker gate + throwaway busybox compose project shaped like an install
│       ├── test_docker_live.py   # real compose up/healthy/ready/status/conflict-guard/down
│       └── test_wotlk_live.py    # WotlkController vs the AzerothCore fixture; opt-in via YULON_WOTLK_SERVER_DIR
├── main.py                       # wires logging → config_dir, update banner (Phase 5.4), Catalog tab + one ControllerView tab per remembered install; YULON_SMOKE_TEST=1 builds the window and exits
├── README.md                     # user-facing: what the app installs under the hood (WSL/VM hidden, not removed), unsigned builds, updates (Phase 5.1.2)
├── requirements.txt
├── requirements-dev.txt          # pytest, mypy, black, ruff — `>=` floors, not pinned versions
├── pyproject.toml                # black/ruff/mypy/pytest config
└── build/                        # PyInstaller specs
    └── pylauncher.spec

<repo root>/pyplan/contribution.md  # contributor setup doc: venv, running the app, the checks, the PyInstaller build
<repo root>/.github/workflows/      # NOT under pylauncher/: GitHub only runs workflows from the repo root
├── ci.yml                        # lint + type-check + test on every push/PR (working-directory: pylauncher)
└── release.yml                   # build matrix → AppImage/dmg/Windows zip
```

**Status as of this writing (2026-08-26):** the tree above shows the Phase 7 target; the
`families/` package, `install_wiring.py`, `catalog/installers/shared/` and the new tests do not
exist yet (nor do the client/SELinux facts in `preflight.py` or provisioning's use of
`runner.interact()`), and the six `install-*.sh` scripts still do. Only `controller_wow_wotlk/` exists.
Catalog entries exist for all four (Phase 3.1). What the other three v1 servers do **not** have is
controller packages or manifests; `manifests/` holds `wow-wotlk/` only. Phase 7
(`pyplan/phase7-decisions.md`) first makes every server *installable* through the one engine,
Linux first, then gives each its own `controller_<acronym>/` package (`controller_wow_tbc/`,
`controller_wow_vanilla/`, `controller_wow_tortoise/`) following the same file layout and
underscore naming — a controller needs a server to control. No server outside v1's four is
considered before that (see style-guide §6 for acronym conventions).

---

## 6. The Manifest Schema (for WotLK)

The entire project succeeds or fails on the module/mod manifest format. It must capture everything `wow-manage.sh` currently hardcodes as **data**, not code.

**Finalized in Phase 2.1** as the pydantic models in `pylauncher/yulon/manifest.py`; the
language-neutral JSON Schema is checked in at `pylauncher/manifests/schema/manifest.schema.json`
(regenerate with `python -m yulon.manifest --dump-schema`; a test fails if it drifts). One schema
serves all four families (`type`: `module` | `ale` | `mod` | `keg`) — the primitives are a closed
set (`source`, `build`, `sql`, `conf`, `deploy`, `patches`, `client`, `server_dbc`, `npcs`,
`prompts`, `notes`); unknown keys are rejected, ids/game are lowercase kebab slugs, and `source.repo`
must be a GitHub `owner/name` slug or an `https://` URL on an allow-listed forge (§3a). Every
field except `id`/`name`/`type`/`game` is optional — plus `source`, which `module`, `ale` and
`keg` items all require, and `source.sparse_path`, which a `keg` also requires — so a simple
module stays short:

```json
{
  "schema_version": 1,
  "id": "mod-ah-bot",
  "name": "Auction House Bot",
  "type": "module",
  "game": "wow-wotlk",
  "description": "Populates the auction house with bot-posted items.",
  "source": {"repo": "azerothcore/mod-ah-bot"},
  "build": {"rebuild": true},
  "conflicts_with": ["mod-ah-bot-plus"],
  "sql": [{"db": "world", "path": "data/sql/db-world/*.sql", "applied_by": "db-import"}],
  "conf": [{
    "file": "env/dist/etc/modules/mod_ahbot.conf",
    "template": "conf/mod_ahbot.conf.dist",
    "keys": [
      {"key": "AuctionHouseBot.Account", "default": "{bot_account}"},
      {"key": "AuctionHouseBot.GUID", "default": "{bot_guid}"}
    ]
  }],
  "prompts": [
    {"key": "bot_guid", "question": "GUID of the bot character", "kind": "int"}
  ]
}
```

Per-category index files (`modules.json`, `ale.json`, `mods.json`, `kegs.json`) are
`{"schema_version": 1, "game": "wow-wotlk", "type": "module", "items": ["mod-ah-bot", ...]}` and
the per-item files live in the matching subdirectory (`modules/mod-ah-bot.json`, ...).

### Tacit knowledge to port (from `wow-manage.sh`)

> **Ported in Phase 2.2** — every item below now lives as data in `pylauncher/manifests/wow-wotlk/`
> (41 manifests; see `pyplan/checklist.md` Cross-cutting for the port record). Kept here as the
> statement of what the schema had to be able to say.

`wow-manage.sh` is ~2,300 lines with heavy embedded knowledge that must become manifest fields:

- **Module config key differences** — e.g. `AuctionHouseBot.GUIDs` (AH Bot Plus, plural) vs the original `AuctionHouseBot.GUID` (singular) + `Account` key. This kind of nuance must be explicit in the JSON.
- **SQL apply ordering** and glob patterns per module.
- **Build targets** — which modules require a worldserver rebuild vs. pure-SQL.
- **Dependencies** — e.g. `mod-player-bot-level-brackets` requires the Playerbots module to function (verified in `wow-manage.sh`; AH Bot has no such dependency — an earlier draft of this doc incorrectly used AH Bot as the dependency example).

---

## 7. Execution Phases (ordered)

See Roadmap.md for the full Phasing plan. The README only summarizes the high-level goals of each phase.

---

## 8. Known Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Python packaging is painful | Standardize on PyInstaller + CI matrix from day one; treat AppImage as the only Linux artifact. |
| PyInstaller cannot cross-compile | Build matrix — each platform on its own runner. Never attempt local cross-builds. |
| Docker Desktop silent provisioning is fragile | Detect + verify with clear, retryable checks; offer manual-install fallback dialog. |
| Porting `wow-manage.sh` tacit knowledge | Do it as an explicit Phase 2 with exit criteria; don't fold it into the UI work. |
| "No WSL" expectation vs reality | Communicate honestly in docs (WSL is hidden, not removed). |
| Scope creep on bot-party/item-mail features | Kept in-game tools (My Party, item mail, teleport) out of Phases 1–7. Phase 8 scoped them on 2026-09-06, each with its own step and definition of done, and cut the ones with no console-legal mechanism (heal, summon, coordinate teleport) rather than inventing one — `pyplan/phase8-parity-decisions.md`. |
| Unsigned binaries trigger OS gatekeeper warnings (Windows SmartScreen, macOS Gatekeeper) | For v1, document the click-through steps for users. Revisit code signing/notarization once the project has a budget/identity for certificates; track as a post-v1 milestone, not a blocker. |

---

## 9. Out of Scope (v1)

Out of scope for the installer and controller work of Phases 1–7. The first three were deferred,
not refused, and are **no longer out of scope at all**: Phase 8 was scoped on 2026-09-06 and the
owner expanded them into v1, each with its own step and definition of done
(`pyplan/phase8-parity-decisions.md`; the boxes are `checklist.md` Phase 8). Kept struck through so
the reversal is visible.

- ~~**My Party / bot group builder**~~ — **no longer out of scope.** Expanded into v1 on
  2026-09-06 as step 8.6, WoW WotLK only (owner answers 2b and 4), by a server-side route: the
  launcher's own Lua bridge over the command channel, never an addon written into the user's
  client (answer 5). Browse Bots is step 8.5 on all four servers.
- ~~**Item database + in-game mail**~~ — **no longer out of scope.** Expanded into v1 on
  2026-09-06 as part of step 8.4 (owner answer 2a): item search is a database read, item mail is
  the server's own command, and the items-per-message cap is a per-tree fact — twelve on WotLK and
  TBC, one on Vanilla and Tortoise.
- ~~**Teleport / GM in-game tools**~~ — **no longer out of scope.** Expanded into v1 on 2026-09-06
  as the rest of step 8.4: named teleport, revive, set level, rename and mailed money, each drawn
  only on a server whose own command table has it. Teleport to raw map coordinates stays out,
  because it would need a direct write to the character database while the server runs, which
  owner answer 7 refuses.
- ~~**Full native reimplementation of installers on Linux**~~ — **no longer out of scope.**
  Overturned 2026-08-26 by the owner: Phase 7 puts every server on the one Python engine on every
  platform, Linux included, and deletes the bash installers (`pyplan/phase7-decisions.md`, "What
  this overturns, by name"). Kept here struck through so the reversal is visible.
- **Code signing / notarization** — accept OS gatekeeper warnings for v1; revisit later (see §8).

---

## 10. Application Self-Update

Users download this app directly (AppImage/exe/dmg) with no package manager tracking updates, so the app must check for new versions itself.

- On launch, ask the GitHub Releases API which `-Public` tag is newest — at most once a day, revalidated with `If-None-Match` so the usual answer is a 304 and costs nothing against the rate limit. Only `v1.2.3-Public` counts: this repository's feed also carries the `-fixtest` and `-DeckTest` builds the gates are cut from, and before T90 they were offered to players.
- **Versions order decimal-style in the last number** (owner, 2026-09-21): major and minor are whole numbers, the last number is a decimal fraction of its digits, so `.6 < .65 < .66 < .7` and `.7 == .70`. `v0.8.7-Public` was cut after `v0.8.65-Public`; compared as integers nobody on 0.8.65 was ever offered it. One ordering, in `update.version_key()`, in exact `Fraction` arithmetic.
- If found, a quiet line under the header says so, and a dialog shows the release notes of every public release between the running version and the one offered: `Update now` / `Later` / `Skip this version`. A `Check for updates` control in the header asks straight away and answers either way, failures included. The notes are shown and never run — images are replaced rather than fetched, and a link is opened only when it is `http(s)` and under this project's own repository.
- **Scope (T90 plan 3):** check, notify, **and install**. `Update now` downloads the artifact this machine's install kind names, checks it against the release's `SHA256SUMS`, unpacks it beside the install, runs it once under `YULON_SMOKE_TEST` and requires exit 0, then replaces the install and restarts. macOS and a folder the user cannot write to download and verify only. A source checkout, an unsupported machine, and any release with no `SHA256SUMS` get the release page.
- **The swap replaces ENTRIES, never the install folder** (cold review 1, 2026-09-21). The folder the executable sits in is not assumed to be Yu'lon's: a Windows zip has no top-level directory, so "Extract here" in a Downloads folder makes that folder the install. The first design renamed it, and the next start deleted the backup — measured, with a `thesis.docx` and a `photos/` folder destroyed. So the update works inside the folder: `<install>/.yulon-new/` holds the staged build, `<install>/.yulon-download/` holds the archive (its own directory, because everything in the staging one is by definition an entry of the new build — an archive left there was installed as part of the program), `<install>/.yulon-old/` holds what was replaced, all three carry a `.yulon-marker` this app wrote, and **nothing without that marker is ever deleted**. The marker is bound to the install's path and to a random token kept in `update.json`, so forging one needs to read the config directory. A staged entry may replace an existing one only if the RUNNING build shipped that name, read from `_internal/yulon-shipped.txt` (written by `release.yml`, falling back to the executable plus `_internal`); a collision with anything else refuses the whole update and offers the download instead.
- **The running app never renames its own files.** A one-dir build reads its own folder for as long as it is open (Qt plugins, `base_library.zip`, every lazy import), and on Windows an open file cannot be renamed at all. So the app stages everything, writes a small `sh` / PowerShell helper, starts it detached and quits through the normal close path; the helper waits for the pid, moves each entry to `.yulon-old/` and each staged entry into place, undoes everything in the exact reverse on any failure, relaunches and deletes itself. The executable is the first thing to leave and the last to arrive, so a half-done swap is "no executable" rather than "the new executable on the old libraries". **Paths reach the helper as arguments, never interpolated into its text**; it validates every one of them before it moves anything; its wait bound is an argument too, so the give-up path is testable; and PowerShell is run by its absolute path under `%SystemRoot%`, never through the PATH.
- **The app does not close for a helper it cannot prove is its own** (round 4, 2026-09-21, after a Windows 11 gate where it closed for a helper that had already exited). Three parts, each of which was a measured hole in the version before it: a **nonce** minted per attempt, passed in argv and written into the helper's `helper-started` stamp — a stamp is otherwise just a file, and one left by an earlier attempt answered for a spawn that started nothing; a **lock**, `mkdir <.yulon-old>/helper.lock`, which is atomic, so two helpers on one install cannot both swap (six runs of six produced a mixed-version install with no backup at all, the loser having moved the winner's new executable into the backup); and a **stand-down** file the app writes when it gives up, read by the helper on every tick of its wait AND again immediately before its first move, together with a full re-validation at that same moment — an orphaned helper swapped twelve seconds after the app had told the player nothing had changed. The app keeps the `Popen` handle and terminates it as well, holds `_restarting` across the whole handshake so a click delivered inside `processEvents()` cannot start a second attempt, and waits 30 s rather than 10 because a cold Windows box can take that long to get PowerShell to its first line.
- **Stopping a helper is not the same as it having stopped** (round 5). `/bin/sh` is dash and dash runs no EXIT trap on SIGTERM; `TerminateProcess` runs nothing at all. So the lock outlives the process, and the next press got exit 73 and no stamp for the rest of the session. Three things together: the scripts trap TERM/HUP/INT and exit, which makes their own EXIT trap run; `end_helper` escalates (terminate, wait, kill, wait) and RETURNS whether it really went, and a helper that would not die stops this session trying again rather than sending a second one in; and the app itself removes the lock — but only one whose `owner` file holds the nonce it just ended, and only after the process is confirmed gone, and the stamp and stand-down only after that (removing the stand-down of a live helper is telling it to carry on). The lock records the helper's pid for the same reason: it is how a live helper is told from a dead one's leavings. And a backup directory a LIVE helper still holds is never rmtree'd for a new attempt — that helper is stood down through the same file and waited for, and if it will not let go the update is refused in the player's words.
- **One script per attempt, written at the moment of use.** `plan_swap` returns a description and `arm()` writes the file; they both used to arm, so every update and every refused attempt left an orphaned `.ps1` in `%TEMP%` for ever (measured on the Windows gate). A helper that starts deletes its own script on every exit path; the paths where none ran — a spawn that raised, a stand-down, a cancel — delete it from the app. Windows creation flags are `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` (+ `CREATE_BREAKAWAY_FROM_JOB`, retried without it: under a scheduled task it is refused with `Access is denied`); `DETACHED_PROCESS` is forbidden — it is mutually exclusive with `CREATE_NO_WINDOW` and leaves PowerShell 5.1 with no console at all, which is how the helper was spawned 40 times and never ran.
- **Every refusal leaves the running install byte for byte as it was** and removes what it staged. There is deliberately **no setting and no environment variable** that changes where an update comes from: the artifact name is computed by the app from the tag, the download address is built from the repository constant and vetted, and redirects are followed only over https.
- **Two copies of Yu'lon do not both update.** The marked staging directory is the lock (its marker holds the pid), and before the helper is started the app refuses if another process is running the same executable (`/proc` on Linux; on Windows the locked files are what refuses, which the gate measures).
- **First start after a swap:** `.yulon-old` is removed, and the bar says "Updated to Yu'lon X" once — but only when the marker says the swap was TO the version now running and every entry it names is present. The two versions are compared with exactly one leading `v` stripped from each side: the marker carries the feed's tag (`v0.8.73-Public`) and `__version__` carries what `build/stamp_version.py` wrote (`0.8.73-Public`), and a plain `!=` between them was true of every real build — so nothing was ever cleaned up and the banner never appeared (cold review 2). A swap that stopped part-way is REPORTED and nothing is touched; a rollback is reported and the staged build discarded. **The startup pass may delete a marked working directory and nothing else** — it never moves, renames or restores a build entry, because that would be the live process renaming the files it is executing out of, with no undo of its own, for a state that is reachable almost never (entries leave executable-first and arrive executable-last, so a half-done swap leaves no executable and the app cannot start at all). An in-process repair existed for one round and was removed on the lead's decision, 2026-09-21. The way back is one instruction with ONE source — `cleanup.recovery_steps()` returns it as data, the message renders it, the README quotes it under a docs pin, and a test executes the steps it reads back out of the message itself at every kill point of a two-entry, a three-entry and a growing build, then runs the restored executable (before round 4 they were three copies and the app's was the wrong one); another hashes the whole install tree across every half-done state and fails if the startup pass changes a byte. None of it happens under `YULON_SMOKE_TEST`, which is how a staged build is proved while the old one is still installed: that run also starts no update check, writes no `update.json`, and does not repair `state.json`.
- **What a release carries (T90):** every `v*` tag publishes a `SHA256SUMS` file beside its artifacts, so an update can be proved before it is trusted, and a release body made from `CHANGELOG.md` — the entries the tag added since the previous `-Public` tag, under their own headings. A tag whose changelog gained nothing keeps GitHub's generated notes, and a release is never refused over either. A packaged build also reports the tag it was built from rather than a hand-edited string, so the version the check compares is the version that was shipped. Whoever cuts the release does nothing new.
- **Measured live.** Each of these is a dated press of a NAMED build, and says nothing about any other:
  - 2026-09-21, `a7dda53b`: Windows 11 (zip, unpacked flat beside the player's own files), Ubuntu (AppImage and tar.gz), Arch (tar.gz) — each updated itself, the player's own files byte for byte as they were, no working directories left.
  - 2026-09-21, `14ba50c5`: Windows 11 (zip) with the nonce/lock/stamp handshake, through PowerShell 5.1 — breakaway refused with `[WinError 5]` and retried without it, the helper reported in, the app closed, the swap ran, a new pid was on the desktop about five seconds later and the next start removed the backup and said so.
  - 2026-09-22, `df6a11d8`: Fedora 44 (tar.gz and AppImage), the first presses on that distribution — about 30 s and 18 s, one "helper written" line each, no script left in `/tmp`, player files identical.
  **The press of the final head this work merges as is recorded in the pull request, not here** — a live gate counts only for the build it was made on, and a sentence in this file about "the current code" goes stale the moment the head moves.

---

## 11. Local Data & Config Storage

The app needs a per-OS location to persist its own state — remembered server install paths, last-used settings, cached manifests — separate from the server data itself (which lives under the user-chosen server directory, e.g. `~/wow-server-playerbots/`).

| Platform | Config/state directory |
|---|---|
| Linux | `~/.local/share/yulon/` (XDG Base Dir convention) |
| Windows | `%APPDATA%\yulon\` |
| macOS | `~/Library/Application Support/yulon/` |

- Store: known install locations per game, chosen client paths, cached copies of fetched manifests (with an ETag/timestamp for refresh), and `update.json` — when the releases feed was last asked for, its ETag, the feed itself (so a cached answer is re-judged against whatever version is running now) and a skipped version. `update.json` is its own file and never part of `state.json`: `AppState` is `extra="forbid"`, so a build older than the one that wrote it would call the whole file corrupt and open with an empty list of installs (T90).
- This directory is **not** the server directory — never conflate app state with server/database files. Server files stay wherever the user chose during install (matching existing script behavior, e.g. `choose_install_dir()` in `install-wow-wotlk.sh`).
- Owned by `platform.py` (add a `config_dir()` helper alongside `detect()`); implemented in Phase 1, consumed by Catalog/Controller from Phase 3 onward.

---

## 12. Single-Instance / Port Conflict Rule

Existing docs are explicit: **"Only run ONE server at a time — they share the same ports"** (`WoW-WotLK-CONTROLS-1.md`). The Controller must enforce this instead of silently reproducing the shell version's foot-gun:

- Before starting a server, the Controller checks whether the required ports (e.g. 3724, 8085) are free. It is one **global** scan of running containers for anything publishing them — so it also catches containers with nothing to do with Yu'lon — with this install's own three excluded by name. It does NOT enumerate other managed installs; this line said it did, and `port_conflicts()`'s own docstring says it "has no concept of which install a container belongs to".
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