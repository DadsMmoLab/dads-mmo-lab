# Yu'lon Checklist

> Companion to `pyplan/roadmap.md`. This file is where **checkable progress** is tracked, while `roadmap.md` itself stays a clean plan with no commentary (style-guide §9).
>
> **How to use this file:**
> - Check items off as they're completed. Leave unchecked items as-is; don't delete steps even if scope changes — note the change instead.
> - This file is expected to grow throughout the project. That's fine; it's a checklist, not a plan.

---

## Phase 0 — Tooling & hygiene

- [x] 0.1 Pin the toolchain (`requirements-dev.txt`: pytest, mypy, black, ruff)
- [x] 0.2 Add a CI lint/type/test job (`ci.yml`, separate from `release.yml`)
- [x] 0.3 Resolve remaining naming debt (lowercase filenames, valid `snake_case` package dirs)
- [x] 0.4 Pin an AzerothCore compose fixture for integration tests
- [x] 0.5 Write a minimal contributor/dev-setup doc (`development.md`)
- [x] 0.6 Establish a shared logging convention

---

## Phase 1 — Foundation (testable Python core, no UI)

- [x] 1.1 `runner.py` — subprocess streaming (`stream()`, `run()`)
- [x] 1.2 `platform.py` — OS detection + `config_dir()` + provisioning stubs
- [x] 1.3 `docker.py` — shared Docker lifecycle logic + port-conflict check
- [x] 1.4 Base controller abstraction
- [x] 1.5 Tests (mocked unit tests + real-Docker integration suite)
- [x] **Phase 1 exit criteria met** (live AzerothCore run passed 2026-08-20 — see Cross-cutting)

---

## Phase 2 — Manifest schema & data port

- [x] 2.1 Finalize the manifest schema (+ `repo` allow-list validation)
- [x] 2.2 Port WotLK modules from `wow-manage.sh` into `manifests/wow-wotlk/`
- [x] 2.3 `modules.py` — load/validate/fetch
- [x] **Phase 2 exit criteria met** (41 manifests + 4 indexes validate in CI; adding a module is a JSON file and an index entry, no Python)

---

## Phase 3 — Catalog (catalog + installer)

- [x] 3.1 `catalog.json` — game list
- [x] 3.2 `installer.py` — orchestration (Phase 3a: shells out to existing scripts) — **live Linux run passed 2026-08-21 on a fresh Ubuntu 24.04 VM (see Cross-cutting → Phase 3 live gate)**
- [x] 3.3 Silent Docker/WSL provisioning stubs wired in (graceful failure until Phase 5)
- [x] 3.4 Networking auto-setup (LAN + internet play; firewall helpers, realmlist updater, router-step prompts) — README §13
- [x] **Phase 3 exit criteria met** (verified via CLI/test harness — no UI yet): `python -m yulon.catalog.installer wow-wotlk --server-dir ~/wow-server-playerbots` on a fresh Ubuntu 24.04 VM (12 vCPU, Docker provided by `ensure_docker()`) answered every prompt, built AzerothCore + playerbots (~11 min compile), and ended with `install of wow-wotlk finished` and all three containers up (2026-08-21 00:27)

---

## Phase 4 — Controller UI (PySide6)

- [x] 4.1 `log_panel.py` — streaming output widget
- [x] 4.2 `catalog_view.py` — browsable catalog
- [x] 4.3 `controller_view.py` — per-install management (+ LAN/internet networking auto-setup control)
- [x] **Phase 4 exit criteria met** — human click-through against a live server on the Ubuntu 24.04 VM, 2026-08-21 (see Cross-cutting → Phase 4 click-through)

---

## Phase 5 — Windows/macOS provisioning + packaging

- [x] 5.1 Silent Docker Desktop / WSL2 provisioning + doc update — Linux path verified for real on a fresh Ubuntu 24.04 VM (2026-08-20); the Windows detection/short-circuit/plan paths verified on a real Windows 11 box (2026-08-21). The silent Docker Desktop **install** itself is still unverified (both test machines already had Docker), and macOS has no machine at all — see Cross-cutting.
- [x] 5.2 PyInstaller specs finalized (local `pyinstaller build/pylauncher.spec` builds `build/dist/yulon/`; bundles manifests/, catalog.json and the install scripts; `YULON_SMOKE_TEST=1` runs the frozen exe headless)
- [x] 5.3 GitHub Actions release matrix complete — `ci.yml` + `release.yml` now live at the repo root `.github/workflows/` (2026-08-21), which is the only path GitHub reads; both run with `working-directory: pylauncher`. Neither upstream branch had a root `.github/`, so nothing was overwritten. The release job still only proves itself on a `v*` tag.
- [x] 5.4 Application self-update check (README §10)
- [x] **Phase 5 exit criteria met** (README §7: a push produces all three platform artifacts automatically) — proven 2026-08-21 by a throwaway `v*` tag on the fork: [run 32433417980](https://github.com/pjerra/dads-mmo-lab/actions/runs/32433417980), three runners green, artifacts `yulon-AppImage` (74 MB), `yulon-exe` (52 MB), `yulon-dmg` (42 MB), each also attached to the Release by `action-gh-release`; the tag and Release were deleted afterwards (they were a test), the run keeps its artifacts until 2026-11-19. The two artifacts we can run were then run — AppImage on Ubuntu 24.04, and the frozen `yulon.exe` out of the release zip on Windows 11 — both logging `window built, exiting 0`, which also makes 5.2 verified on real Windows from the shipped artifact rather than a local build. `ci.yml` is green on every push (run 32432706579). The `.dmg` is CI-built only — no Mac exists on this project, so it is unverified beyond building (Phase 6.5 item 9 covers it).

---

## Phase 6 — Cross-platform install paths (macOS + native Windows) — **WotLK only**

> **Scope gate:** Phase 6 targets WoW WotLK exclusively (6.0's script rehome may touch all four
> games mechanically, but 6.1–6.5's gating/installer/feature work is WotLK-only). TBC, Vanilla,
> and Tortoise are Phase 7 — not started until Phase 6's exit criteria are fully met.

- [x] 6.0 Rehome the install scripts — the eight executable files now live in `pylauncher/catalog/installers/<game>/` (parallel to `manifests/`), `catalog.json` paths are relative to that directory, `resources.installers_dir()` replaces `repo_root()`, `Installer(installers_root=…)` resolves them, and the spec ships the whole tree instead of globbing `archive/guides/**` — so the bundle no longer carries `archive/guides` at all (README §3a bonus). The Tortoise script was renamed to lowercase on the way (`install-tortoise-wow-wsl.sh`, style-guide §6a). Verified: 191 passed, and a frozen PyInstaller build contains all eight scripts under `catalog/installers/` and passes `YULON_SMOKE_TEST`. The DoD's third verb, *run*, is not re-evidenced post-move — but `git show --stat fcd95c5` shows all eight scripts as pure renames (0 changed lines) and `installer.py` already passed `cwd=self.script.parent` before the move, so what runs is byte-identical to what Phase 3 live-gated. `archive/guides/` keeps the human-facing guides plus the four non-catalog installers (Maplestory, Mu Online, RuneScape, the Unbound addon), which no catalog entry references.
- [x] 6.1 Honest platform gating — `install.platforms` is data in `catalog.json` (all four entries `["linux"]`), `Installer.preflight()` raises `UnsupportedPlatformError` with a user-readable message BEFORE any subprocess, the catalog tile disables Install with the reason on the tile ("Use existing…" stays enabled — managing a server works everywhere), `start_install()` refuses before the folder prompts, and a failed script's dialog now carries the script's own last 12 output lines ("It last said: …") instead of a bare exit status. Mocked through the `platform_id` seam per roadmap 6.4; 196 tests green.
- [ ] Rewrite the installer scripts off `pacman`/`systemctl`/`sudo` — the orphaned "update scripts and manifests to use proper systems and features" step, re-homed as a checkbox: it is subsumed by 6.2/6.3's native engine, and closes when WotLK installs without a shell script on macOS and Windows.
- [ ] 6.2 macOS install path — **runtime is Docker Desktop** (macOS variant drives `docker compose` against the Docker Desktop the app provisions; no `pacman`/`systemctl`/`sudo`, no manual VM management)
- [ ] 6.3 Native Windows install path — **runtime is Docker Desktop** (drives `docker compose` against Docker Desktop's WSL2 backend; no bespoke WSL2/VM manager; `[blocked]` on 6.2)
- [ ] 6.4 Tests & gates (mocked platform-gating + script-resolution tests; live-gate on real macOS and Windows 11 — WotLK only)
- [ ] 6.5 Full WotLK feature coverage on Linux, macOS, and native Windows (the Phase 6 exit gate):
  - [ ] Install (zero shell interaction, all three platforms)
  - [ ] Server lifecycle: start/stop/status/health polling + README §12 port-conflict guard
  - [ ] Console: `docker attach` pty transport + full `CONTROLS-2.md` GM console (safe attach/detach, GM commands); Windows gap resolved or explicitly re-scoped (SOAP follow-up)
  - [ ] Account creation (`CREATE-ACCOUNTS.md`/`CONTROLS-1.md`): `account create` + `account set gmlevel`, no password echo, "already exists" handled, all three platforms
  - [ ] Maintenance (`CONTROLS-1.md`): cache clear, DB backup/restore, SQL changes — `maintenance.py` implemented (currently placeholder), rebuild/restart wiring done, all three platforms
  - [ ] Modules/mods: install/remove via the applier + rebuild/restart; manifest store GitHub refresh + bundled fallback
  - [ ] Networking auto-setup (README §13, full `WoW-Wotlk-NETWORKING.md` scope): firewall (ufw/firewalld/netsh + **macOS firewall designed/implemented**), WSL2 portproxy, LAN/public IP detection, realmlist updater + client writer, 0.0.0.0 binding check, CGNAT/DuckDNS/router-step prompts
  - [ ] Self-update check (README §10) — no platform-specific `config_dir()` issues
  - [ ] Packaging: live-gated against the packaged `.AppImage`/`.dmg`/`.exe`, not just `python main.py` from source
  - [ ] User-facing README topics (`pylauncher/README.md` + `archive/guides/wow-wotlk/README.md`) accurately reflect each platform's real state — no "works on macOS" claim before 6.2 is done
- [ ] **Phase 6 exit criteria met** — WoW WotLK has 100% working feature coverage (6.5) on Linux, macOS, and native Windows, zero shell interaction, no silent off-Linux fast-fail. **Phase 7 does not start until this is fully met.**

---

## Phase 7 — Full coverage for the remaining WoW servers (TBC, Vanilla, Tortoise)

> **[blocked]** on Phase 6's exit criteria. Same "100% working coverage on Linux, macOS, and
> native Windows" bar as Phase 6 — not a lesser bar. v1 scope remains these four servers only.

- [ ] 7.1 `controller_wow_tbc/` — full controller package + manifest port + 6.1–6.5 repeated for TBC on all three platforms
- [ ] 7.2 `controller_wow_vanilla/` — full controller package + 6.1–6.5 repeated for Vanilla on all three platforms
- [ ] 7.3 `controller_wow_tortoise/` — full controller package + 6.1–6.5 repeated for Tortoise on all three platforms; resolve the `status: wip`/unverified-client caveat, promote `catalog.json` status once verified
- [ ] 7.4 Cross-server regression pass — re-run WotLK's 6.5 coverage gate after 7.1–7.3 land to confirm shared layers (`docker.py`, base `Controller`, `runner.py`, `platform.py`, `networking.py`) weren't regressed
- [ ] **Phase 7 exit criteria met** — all four v1 servers (WotLK, TBC, Vanilla, Tortoise) have 100% working feature coverage on Linux, macOS, and native Windows. **Phase 8 does not start until this is fully met.**

---

## Phase 8 — Feature parity with The Lab + Hypeer Launcher (TBD)

> **[blocked]** on Phase 7. **NOT a UI/UX pass** (that is Phase 9) — this is a *feature* phase
> folding two existing companion tools into Yu'lon. Scope still TBD.

- [x] **Identify "Hypeer Launcher"** — answered 2026-08-21: it is **this project's own Rust/Tauri launcher**, by the same author, living on the `rust-main` branch of this repository (`crates/dml-core` + `crates/dml-wow` + `launcher/src-tauri`). Nothing external and no licensing question — porting from it is porting our own code. Its user-facing feature list is `docs/FEATURES.md` on that branch; what is worth porting, and the incidents behind each design, are distilled in [`pyplan/rust-prior-art.md`](rust-prior-art.md) (§7 is the Phase 8 shopping list; §§1-5 are what Phase 6 needs).
- [ ] **The Lab feature parity** (candidate list, to be confirmed when scoped): My Party (5-man bot group), item database + in-game mail, teleport, module management (parity with Yu'lon's existing applier), Steam integration, auto-shutdown on WoW close
- [ ] **Hypeer Launcher feature parity** — the shipped feature set to match, from `docs/FEATURES.md` on `rust-main`: live dashboard (players/uptime/latency/bots) with streamed logs; GM console with history and autocomplete; character sheet (gear + wowhead tooltips, 3D paperdoll, talent trees, 1320 achievements); teleport to ~2000 locations; GM tools (revive/heal/level/gold/summon/rename); item mail; gear-set presets; My Party and Browse Bots over ~2500 bots; 38 modules with per-module update-checks, tuning knobs and a config editor; settings with guardrails; account-wide sharing; accounts; backups with validate/restore; self-update of core + playerbots; auto-stop when the game closes; LAN/internet play; doctor & shell. Several of these are Yu'lon Phase 4/5 features already — scope this as a **delta**, not a re-port.
- [ ] **Phase 8 exit criteria met** — TBD, to be defined after Phase 7 exits and Hypeer Launcher is identified/scoped

---

## Phase 9 — UI/UX pass for the v1 Alpha (TBD)

> **[blocked]** on Phase 8. **This IS the UI/UX pass** — polish the feature-complete app into a
> dad-friendly v1 Alpha. Scope TBD.

- [ ] **Phase 9 exit criteria met** — TBD; end state is a shippable v1 Alpha (all four v1 servers feature-complete + polished, consistent UI/UX on Linux, macOS, and native Windows)

---

## Cross-cutting

> Anything that doesn't cleanly belong to one phase — style-guide amendments, cross-document corrections, tooling gotchas, etc.

- **`pyplan/phase6-decisions.md` (2026-08-21):** why 6.2/6.3 is one shared Python install engine rather than per-platform scripts or a container wrapper, what was rejected and on what evidence, and the finding that **SOAP cannot create the first account** — so 6.5 item 3's option (a) rests on a false premise and SRP6-over-`DockerSql` becomes the primary account path on every platform.
- **Live-machine findings, 2026-08-21 (clean Ubuntu 24.04 VM, Docker 29.1.3)** — three things that only a real
  daemon could show, all now covered by tests that run against one:
  1. **`Controller.stop()` removed the containers.** It ran `docker compose down`, so the next `start()` found nothing
     to start by name and fell back to `compose up -d` — re-running the one-shot `ac-db-import` that `start_staged()`
     exists to prevent. The staged start had therefore *never* run in the launcher's own stop/start cycle. Fixed by
     `docker.stop_staged()` (stop world, auth, db by name); `docker.stop()` stays as the teardown path. The failing
     assertion before the fix was literally `stop() removed the containers`.
  2. **The live fixture that caught it now lives in the suite.** `tests/integration/` gained a one-shot container that
     appends a line per run to a bind-mounted file, so a test counts how many times the "import" ran: `compose up`
     re-runs it (the documented bug), a launcher stop/start cycle does not (the fix), an edited compose file is still
     applied. 5 passed / 1 skipped live. This is the plan's "alpine integration fixture" step, landed early because the
     bug hunt needed it.
  3. **`ensure_docker()` reports failure after a completely successful install.** On a clean Ubuntu it ran all four
     steps (`apt-get update`, `apt-get install docker.io docker-compose-v2 docker-buildx`, `systemctl enable --now
     docker`, `usermod -aG docker pk`) with zero skips, then returned `docker_ready=False`, because the calling
     process's group set predates `usermod` — the daemon was fine, and a fresh login used it immediately. The only
     remedy offered is "log out and back in". A user who provisions Docker from the launcher and is then told "Docker
     not reachable" has no way to tell that apart from a real failure. **Open:** distinguish the two states (probe the
     daemon under `sudo -n docker info`, or re-probe under `sg docker`) and say "installed — restart the launcher"
     rather than reporting it as not ready. See 6.5's provisioning coverage.

- **`ensure_docker()` cannot provision Docker on Windows — three high-severity defects, each reproduced by
  hand on the VM (2026-08-22).** Docker Desktop 4.87.0 now runs there, but only because every one of these
  was worked around manually. The roadmap's claim that "the app already provisions WSL2 + Docker Desktop"
  is true for the WSL half and **false for the Docker half**.
  1. **The download fails TLS verification on a fresh Windows install** (`_urllib_download`,
     `platform.py:399-406`, used at `:604`). The real run aborted after 0.4 s with
     `[SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate` and handed the user the exact
     manual step the product exists to remove. Isolated on the same box: Python 3.12.10 / OpenSSL 3.0.16,
     `ssl.get_default_verify_paths().cafile = None`, 18 CA certs — github.com, raw.githubusercontent.com and
     pypi.org all verify fine while `desktop.docker.com` does not. Fix deliberately, e.g. ship `certifi` or
     hand the download to `curl.exe`/BITS which use the OS store; **not** by disabling verification.
  2. **The start step runs a command that resolves nowhere** (`platform.py:623`):
     `Start-Process 'Docker Desktop'` exits 1 with "The system cannot find the file specified" on any
     Windows machine. `Start-Process 'C:\Program Files\Docker\Docker\Docker Desktop.exe'` works
     immediately. At least it is not silent — PowerShell exits 1, so `_run_steps` records the failure.
  3. **The readiness poll cannot succeed in the same run, structurally.** `docker_ready()` resolves `docker`
     from the *current process's* PATH, but the installer only adds its bin directory to the **machine**
     PATH, which an already-running launcher never sees. Reproduced with the engine fully up: strip the
     Docker bin dir from PATH and `shutil.which("docker")` is None and `docker_ready()` is False; restore it
     and both succeed. So even with 1 and 2 fixed, the first run always ends in a manual step. Resolve
     `docker` by absolute path after an install, or re-read the machine PATH before polling.

  Smaller, same pass: the dry-run plan at `:602` omits the download step it will actually perform; a `U+2192`
  arrow in log output crashes on the cp1252 console (`:605`, `:670`, and 13 sites in `apply.py`); and the
  629 MB installer is re-downloaded unconditionally with no resume or cache.

- **First launch of Docker Desktop is gated behind modal dialogs — a headless start waits forever.** The
  installer was run with `--accept-license` and Docker Desktop *still* showed license acceptance and an
  onboarding walkthrough; a human had to click both before the engine would boot. The state lands in
  `%APPDATA%\Docker\settings-store.json`, which after acceptance reads
  `{"AutoStart": false, "DisplayedOnboarding": true, "LicenseTermsVersion": 2, "SettingsVersion": 45}`.

  **This is a design decision, not a code fix.** Pre-seeding that file clears the dialogs, but
  `LicenseTermsVersion` is Docker's *subscription service agreement*, and accepting it silently on a user's
  behalf is a legal act the launcher should not perform quietly. The honest shape is to show the terms (with
  a link) in Yu'lon's own first-run, take consent there, and only then write the file. The value is a
  **version number**, so a future Docker bumps it and the gate returns: "engine never became ready" must be
  an explainable state with a "finish setup in Docker Desktop" message, never an infinite wait.
  `AutoStart: false` is the related reason the engine must be started explicitly on every run.

- **Two Windows results that must NOT be generalised from this VM.**
  - **Silent elevation "works" here for the wrong reasons.** `Start-Process -Verb RunAs -Wait` installed
    Docker Desktop unattended over SSH — but only because that session's token was **already elevated** and
    this box has non-default UAC (`ConsentPromptBehaviorAdmin=0`, `PromptOnSecureDesktop=0`). The case that
    matters — a non-elevated user double-clicking the launcher on a default-UAC machine — is **unmeasured**.
  - **The Docker credential helper fails without a real logon session.** `docker run` exits 125 with
    "error getting credentials … A specified logon session does not exist" over SSH, and even inside a
    Task Scheduler task with LogonType Interactive. Whether a GUI launcher in the user's own session avoids
    it is untested. Map that error to a comprehensible message before any headless `compose pull`.

- **Start no longer bootstraps an un-imported project — a repair action is now owed (2026-08-22).**
  `start_staged()` names the three long-running services, so `compose` can never select
  `ac-db-import`. That is the point, and it holds in every case the old code got wrong. The honest
  consequence: if an install was interrupted *after* the containers were created but *before* the import
  finished, pressing Start brings the servers up against an unimported database and they fail. The old
  code would have re-run the import there — by accident, via the same fallback that destroyed working
  databases everywhere else. **Needed: an explicit "repair / re-import" action**, deliberately chosen by
  the user with a warning about what it overwrites, rather than a silent side effect of Start. The
  installer remains the only thing that runs the import on a healthy path.

- **Compose project identity does not survive a moved folder — half fixed, half owed (2026-08-22).**
  Upstream AzerothCore's compose declares no top-level `name:`, and nothing sets
  `COMPOSE_PROJECT_NAME`, so the project is identified by the install directory's **basename** — while the
  containers are pinned by `container_name` and are therefore global. The two identities come apart in
  both directions, and both were measured on a real daemon:
  - **Rename or move the install folder.** `docker compose stop` there exits 0, prints nothing, and stops
    nothing. **Fixed**: `stop_staged()` now verifies by container name (which does not move with the
    folder) and finishes the job by name when compose stopped nothing. Reproduced before and after —
    `bare compose stop exit=0, still running: ['mv-db', 'mv-world']` then `still running: []`.
  - **A neighbour whose folder shares a basename.** Two installs at `…/pa/server` and `…/pb/server` are
    both project `server`, and compose selects on that label alone, so from one, `docker compose ps` lists
    the *other's* containers. The stop path no longer asks compose, so it is unaffected — but this is why
    it must not go back to asking.
  - **STILL OWED: the start side.** `start_staged()` is `compose up -d --no-deps <services>` in the
    install directory, so on a *moved* install it fails with
    `Conflict. The container name "/ac-database" is already in use` (measured, exit 1). A moved install
    can be stopped but not started. The fix is to stop the project name moving: write
    `COMPOSE_PROJECT_NAME` into the install's `.env` at install and at attach time — `wow-manage.sh:7553`
    already does exactly this on its own move command, so the precedent is in this repo. Until then, a
    user who reorganises their folders gets a raw daemon error.

- **Open follow-ups from the staged start/stop review (2026-08-22)** — found by a three-lens review whose
  findings were then adjudicated against a live daemon; the must-fix (parallel `docker stop`) and the
  latching config check are already fixed, these three are not:
  1. **Nothing in the app can remove a container any more.** `docker.stop()` (`compose down`) has no
     production caller now that Stop keeps containers. Container names are fixed per game in
     `catalog.json`, so install to directory A, press Stop, install the same game to directory B, and
     `compose up` dies with `Conflict. The container name "/ac-database" is already in use` — which worked
     before, because Stop removed them. Same wall for repair: a container wedged in its creation-time
     config survives every Stop/Start. **Needs a deliberate destructive action on the Server tab**
     ("Stop and remove containers") wired to `docker.stop()`.
  2. **`docker_ctl.py` re-exports `stop` as an equal peer of `stop_staged`.** The next contributor adding a
     restart-after-module-apply reaches for the shorter, button-named one and silently reinstates the
     import re-run. Rename it `teardown`, or make the compose primitives private, when (1) lands.
  3. **The 10-second SIGTERM grace is probably too short for a real shutdown save.** AzerothCore with the
     1600-2000 playerbots the installer configures does not finish its save queue in 10s and is SIGKILLed.
     This is *not* a regression — `compose down` had the same default — but it is a real data-loss risk
     that was noticed while fixing the ordering. Needs a measurement on a populated server before picking
     a `--timeout` value; do not guess a number.

- **Clean Windows 11 baseline, 2026-08-22 (Win11 Pro 25H2, build 26200.8037, Hyper-V guest, 20 GB RAM,
  15 vCPU, 75 GB free)** — items 1-4 were measured on a genuinely pristine box: three installed programs
  total, no Docker anything, no Python, no git, no bash. **That machine is no longer clean** (Docker
  Desktop, WSL2, git, Python and a cloned repo are on it now), so the from-zero gate has to be re-run from
  a fresh image or the `clean-ssh` checkpoint — see the provisioning defects below, none of which has ever
  been run green unaided.
  1. **Nested virtualisation must be enabled on the Hyper-V host — and the guest-side test for it is a
     lie.** A Hyper-V guest cannot run WSL2 or Docker Desktop until the host sets
     `Set-VMProcessor -VMName <vm> -ExposeVirtualizationExtensions $true` with static RAM
     (`Set-VMMemory -DynamicMemoryEnabled $false`), the VM powered off. Applied to `yulon-win11` at
     2026-08-22 00:10, after which Docker Desktop 4.87.0 installed and its engine served containers
     (Engine 29.7.2, Compose v5.4.0, `docker run --rm hello-world` exit 0).

     **Correction to an earlier version of this entry**, which called it a hard blocker and diagnosed it
     from inside the guest: **CPUID leaf 1 ECX bit 5 (VMX) and WMI's `VMMonitorModeExtensions` are not
     valid tests on Windows.** Both still read False *while WSL2 was running a live utility VM* — the
     Windows hypervisor masks VMX from its own root partition. Anything that gates on them will report a
     working machine as broken. The only trustworthy signal is host-side:
     `Get-VMProcessor -VMName <vm> | Select ExposeVirtualizationExtensions`.
  2. **The `bash.exe` claim in `phase6-decisions.md` had the right conclusion and the wrong mechanism.** On a
     clean Win11 there is no `bash.exe` at all — `where.exe bash` exits 1, cmd returns ERRORLEVEL 9009, and
     no execution alias exists. The Store-alias/`execvpe` state only appears once WSL has been enabled.
     Both mechanisms are now recorded in the doc and in `bash_available()`'s docstring.
  3. **`shutil.which()` is actively misleading on Windows.** `which("python")` returns a truthy path on a box
     with no Python: a zero-byte Store alias at `WindowsApps\python.exe` that exits 9009. Any interpreter or
     tool probe must run the binary and check the exit code, as `bash_available()` already does.
  4. Smaller traps worth keeping: `wsl.exe` writes its output in **UTF-16LE** (a UTF-8 read gets mojibake) and
     `wsl --status` exits **50**, not 1, when WSL is absent — `ensure_wsl2()` only checks `returncode == 0`,
     so it is correct today, but any future parse of that text must decode UTF-16.
     `(Get-ComputerInfo).WindowsProductName` still reports `Windows 10 Pro` on Windows 11; gate on
     `OsBuildNumber`/`OsName` instead.

- **`pyplan/rust-prior-art.md` (2026-08-21):** what the earlier Rust launcher (`rust-main`) already solved, distilled so nobody has to read Rust — the staged/resumable install machine, the compose three-file split and its build-file trap, preflight floors with the measurements behind them, Windows Docker Desktop specifics, and creating the first GM account via SRP6 (no console/pty needed, which is the open Windows console gap in 6.5 item 3). Sections 1-5 feed Phase 6; section 7 lists what is waiting for Phase 8's feature port.