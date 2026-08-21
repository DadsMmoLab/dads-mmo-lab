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

- [x] 5.1 Silent Docker Desktop / WSL2 provisioning + doc update — **Linux path verified for real on a fresh Ubuntu 24.04 VM (2026-08-20, see Cross-cutting); Windows/macOS fresh-machine runs NOT yet verified**
- [x] 5.2 PyInstaller specs finalized (local `pyinstaller build/pylauncher.spec` builds `build/dist/yulon/`; bundles manifests/, catalog.json and the install scripts; `YULON_SMOKE_TEST=1` runs the frozen exe headless)
- [x] 5.3 GitHub Actions release matrix complete — `ci.yml` + `release.yml` now live at the repo root `.github/workflows/` (2026-08-21), which is the only path GitHub reads; both run with `working-directory: pylauncher`. Neither upstream branch had a root `.github/`, so nothing was overwritten. The release job still only proves itself on a `v*` tag.
- [x] 5.4 Application self-update check (README §10)
- [ ] **Phase 5 exit criteria met**

---

## Cross-cutting

> Anything that doesn't cleanly belong to one phase — style-guide amendments, cross-document
> corrections, tooling gotchas, etc.

- **Phase 1 exit criteria — CLOSED (2026-08-20):** 1.1–1.5 are implemented and green (60 mocked
  tests). The live suite ran on the Ubuntu 22.04 test box (4 cores, Docker 29) against a real,
  already-built AzerothCore + playerbots compose project:
  `YULON_WOTLK_SERVER_DIR=~/games/wow-server-playerbots YULON_WOTLK_REALM_ADDRESS=100.78.24.50
  pytest -m integration tests/integration` → 3 passed in 57 s (busybox lifecycle, db-healthy
  timeout, and `WotlkController` start → db healthy → `ready...` → stop). Lesson recorded: the
  first attempt timed out in `wait_ready()` because that install's realm is registered at the
  box's Tailscale IP, not `127.0.0.1` — the auth-log marker is `<realm_host>:<realm_port>`, so
  the realm address MUST match `acore_auth.realmlist.address`. Follow-up for the controller (not
  done): read the realm address from the DB/`realmlist` instead of trusting a default.
- **1.4 design note:** the base `Controller.port_conflicts()` filters this install's *own*
  containers out of `docker.port_conflicts_for()`'s global scan, so a restart of the same install
  is never reported as a README §12 conflict; only a foreign container binding the ports blocks
  `start()` (`PortConflictError`, carrying the offending names for the UI to show).
- **2.1 decisions (2026-08-20):** the schema is pydantic v2 models (`yulon/manifest.py`, new
  runtime dependency `pydantic>=2.7` in `requirements.txt`) plus a generated JSON Schema at
  `manifests/schema/manifest.schema.json` (test-pinned against drift). README §6's example was
  updated to the finalized shape: `conf` is a list of `{file, template, keys[]}` (not a bare
  path) and `build_targets` became `build: {rebuild}` — `wow-manage.sh` has no per-module build
  target, a rebuild is always `docker compose up -d --build`. Index files are now
  `{schema_version, game, type, items: [ids]}`; `kegs/account-wide.json` was removed because the
  script's `accountwide` is a plain ALE script (Aldori15's repo), while the only real kegs are the
  in-repo `ALE-Kegs` bundles `bmah` and `sod` (`archive/guides/wow-wotlk/ALE-Kegs/README.md`
  defines the term) — they get `kegs.json` + `kegs/<id>.json` like every other family.
- **2.2 port record (2026-08-20):** `wow-manage.sh` v2.2.8 (8,017 lines — README said ~2,300)
  was read in full and its four registries (`MODULE_REGISTRY`, `ALE_SCRIPT_REGISTRY`,
  `SQL_MOD_REGISTRY`, `MODULE_UPDATE_FILES`) plus every per-key `case` table were ported into 41
  manifests: 21 `modules/` (incl. `mod-custom-login`, which the script files under SQL mods as
  `conf_module` but is a C++ module, and `mod-arac`, the one data-only module with
  `build.rebuild=false` and directly-applied SQL), 7 `ale/`, 11 `mods/` (4 mutually exclusive mob
  tweaks + xp-rates are inline/no-clone), 2 `kegs/` (`bmah`, `sod` — sparse checkouts of this
  repo's `ALE-Kegs`). Not ported: the script's `levelupreward` ALE key — it has install/remove
  code paths but is in no registry and names no repo, so it is unreachable from the menus.
  `mod-player-bot-level-brackets` declares `requires: ["mod-playerbots"]`; there is no
  `mod-playerbots` manifest yet (it is the installer's core choice, not a toggleable module) —
  2.3's dependency check must treat it as satisfied by the playerbots server type.
  Config markers/state files the script keeps (`sql_scripts/installed/*.installed`,
  `sql_scripts/config/*.conf`, `.arac_sql_applied`) are deliberately NOT manifest data — they are
  app state and belong under `platform.config_dir()` (README §11).
- **2.3 decisions (2026-08-20):** loading/fetching live in the shared `yulon/manifest_store.py`
  (`ManifestStore` over any tree, `ManifestFetcher` mirroring `<game>/<family>.json` + items
  from a raw-GitHub prefix into the cache with `.etag` sidecars and `If-None-Match`, atomic per
  file, validated before use) and the apply engine in `yulon/apply.py` (`Applier.install/
  configure/remove` over seams `Git`/`SqlRunner`/`DbcCopier`; clones go to `modules/<id>`,
  `ale_scripts/<id>`, `sql_scripts/clones/<id>` like the script; `db-import` SQL is never run by
  the app; every step a run could not perform is listed in `ApplyReport.skipped`; templates
  `{key}` render from prompt values with prompt defaults as fallback, and a missing value is an
  `ApplyError`; bool prompt values render as the strings `true`/`false`). `controller_wow_wotlk/
  modules.py` only binds game id, bundled dir, refresh URL and the DB container.
  **Open:** `MANIFEST_BASE_URL` points at `DadsMmoLab/dads-mmo-lab` `main`, which will only
  serve manifests once this branch lands there — until then refresh 404s (cleanly) and the
  bundled copy is used. Dependency resolution (`requires`/`conflicts_with`, incl. the
  `mod-playerbots` pseudo-id) is not in the engine yet — it belongs to the controller/UI layer
  that decides what to install (Phase 4.3), not to the applier.
- **3.1 record (2026-08-20):** `catalog.json` describes exactly the four v1 servers from their
  installers — WotLK (AzerothCore mod-playerbots fork, `ac-*`, 3724/8085/3306, root `password`),
  TBC and Vanilla (CMaNGOS + cmangos/playerbots, `tbc-*`/`vanilla-*`, MariaDB root password
  generated into `.db_password`), Tortoise (`Penqle/tortoise-wow`, `tortoise-*`, world port
  **8090**, client 1.18.1 build 7272, status `wip` — its installer is unverified with a real
  client). The port table README §13/3.4 needs lives here (`ports.auth/world/db`), and
  `CatalogEntry.container_spec()` is how a future `controller_wow_tbc/` etc. gets its
  `ContainerSpec` without retyping the names. Tests pin that every referenced install script
  exists and that the WotLK entry equals `docker_ctl.SPEC`.
- **3.2/3.3 record (2026-08-20):** the installers are interactive (`ask_yes_no`, `press_enter`,
  `choose_install_dir`, `Enter path to your … client folder`, the Steam Deck keyring `yes`), so
  Phase 3a needed an expect-lite: `runner.interact()` merges stdout/stderr, reads in chunks so a
  prompt with no trailing newline surfaces after a quiet interval, and writes whatever the
  `Responder` returns to stdin. `catalog/installer.py` answers from the typed `PROMPT_RULES`
  table (first match wins; destructive/optional offers — reinstall, README, wow-manage
  download, stop-server, 'continue anyway' — are declined; progress gates accepted; paths are
  passed in POSIX form because the child is bash). Games whose script loops until given a
  client folder carry `install.requires_client_dir` in `catalog.json` and are refused up front
  without one — the app never fetches a client (§3a). **Known constraint:** the scripts open
  with `sudo -v`; with no tty and no cached credentials that fails immediately, so Phase 3a
  needs passwordless/cached sudo (Phase 4/5 must hand the UI a real password path, e.g.
  `SUDO_ASKPASS` + `-A` or pkexec). 3.3's graceful error is `DockerUnavailableError` raised
  from `preflight()` before the script starts, pinned by test.
- **Phase 3 live gate — findings from the first real run (2026-08-20, fresh Ubuntu 24.04 VM,
  12 vCPU, Docker absent, `python -m yulon.catalog.installer wow-wotlk --server-dir ~/wow-server-playerbots`):**
  1. `catalog.json` pointed `wow-wotlk` at the pacman/SteamOS script; on Debian hosts it would
     call `pacman`. Fixed: `install.script_variants` (`apt` → `-ubuntu.sh`, `dnf` → `-fedora.sh`)
     + `Installer(package_manager=…)` seam (`host_package_manager()`; None off Linux).
  2. The script calls `clear` and exits 1 with `TERM environment variable not set.` — the normal
     state of a desktop-launched app. Fixed: `Installer.script_env()` inherits `os.environ`,
     defaults `TERM=xterm-256color`, applies `env` overrides on top (overrides used to REPLACE
     the environment).
  3. The unanchored `Press ENTER` rule answered the hint "Leave blank and press ENTER to use the
     default location." and the blank was consumed by `Install path:` → the chosen server dir was
     silently dropped. Fixed: rules anchored (`^\s*Press ENTER`, `press ENTER to shut down`).
  4. **Open (needs design):** the Debian script runs `sudo -v` up front; without a TTY sudo
     cannot ask for the password and the script aborts. The gate ran with passwordless sudo.
     Product answer is sudo's own: set `SUDO_ASKPASS` to a helper that hands over a password the
     UI asked for once (sudo falls back to the helper when no terminal is present). Note that
     `sudo -v` still prompts when a password-requiring group rule coexists with a NOPASSWD rule.
  (Step review, 2026-08-21: `script_variants` keys are now the `PackageManager` Literal, an
  empty `TERM=""` is defaulted too, and `main.py` keeps one controller tab per (game, server
  dir) — a repeat "Use existing…" focuses it. Known limitation kept: `wow-tbc`/`wow-vanilla`/
  `wow-tortoise` have NO Debian/Fedora script ports in the repo, so on apt/dnf hosts they run
  the pacman script; tbc/vanilla carry partial `apt-get` fallbacks and Docker is provided by
  `ensure_docker()` first, but that path is unverified.)
  6. Upstream script bug (not Yu'lon): the Debian installer's wait loop greps only
     `docker logs --tail 100` for `ready...`; on first boot that line is ~1,900 lines back, so
     the script waits its full 30-min timeout and then continues with a warning. Yu'lon's own
     `wait_ready()` reads the full log and was unaffected. Worth a PR to the script.
  7. Under the OLD unanchored rules one stray answer reached "Would you like to stop the server
     now? (y/n)" ("Please answer y or n", then `n` was accepted) — the re-ask is harmless; the
     next live run (with anchored rules) should show no re-ask. Outcome of the run: finished,
     server left running, `MY_SERVER.txt` written, realmlist 127.0.0.1.
  5. 5.1 Linux path passed for real on the same VM: `ensure_docker()` ran `apt-get update`,
     `apt-get install -y docker.io docker-compose-v2`, `systemctl enable --now docker`,
     `usermod -aG docker pk` and reported "log out and back in" honestly (`docker_ready=False`
     for the current process; a new session had the group). `docker.io` ships no buildx — the
     script fell back to a manual plugin download — so the apt list now includes
     `docker-buildx` (24.04 candidate 0.30.1).
- **Phase 4 click-through (2026-08-21, Ubuntu 24.04 VM, live AzerothCore install):** exercised in
  the GUI: Modules (install `mod-transmog` → 3 steps, rebuild-required; then remove), Networking
  (internet plan + apply → 4 done / 4 manual router steps; LAN apply → 4 done / 0 manual), and
  Stop (all three containers down, no raw Docker error). The console was verified against the
  same live worldserver but headlessly (`console.send_command("server info")` returned the real
  reply), not through the Console tab; Start was not re-clicked after the last fix. Three real
  bugs came out of this session and are fixed:
  1. **Nothing happened when a button was clicked.** PySide6 connects to a bound method through a
     WEAK reference, so the job worker was collected the moment the factory returned and its slot
     never fired — no work, no callback, no error (Server tab stuck on "status: unknown", Start on
     "status: starting…"). `ThreadedJobRunner` holds every live (thread, worker) pair now, and
     `tests/test_job.py` fails without it.
  2. **The window froze** for the length of every action: start/stop, module install, the network
     plan and each console command ran in the button's slot. They all run on a worker thread now
     (`yulon/ui/widgets/job.py`), answering on the GUI thread through the view's own slots.
  3. **The console could not send anything**: the worldserver container runs with `tty: true`, so
     `docker attach` refuses a non-terminal stdin ("the input device is not a TTY"), and writing to
     `/proc/1/fd/0` only prints to the terminal without reaching the console's input. It opens a
     pty now; Windows has no pty, so it refuses with an explanation and the terminal command —
     **SOAP is the follow-up that fixes Windows** (what the Rust launcher does: `soap.rs` +
     `srp6.rs`/`account_write.rs` write the first GM account straight into `acore_auth`).
  Also found: a fresh Linux box needs the app RESTARTED after `ensure_docker()` adds the user to
  the `docker` group — the running process keeps its old groups, so it reports "Docker not
  reachable" until relaunched. The report already says "log out and back in"; the UI should say it
  where the status is shown (follow-up).
- **3.4 record (2026-08-20):** mirrors `WoW-Wotlk-NETWORKING.md` exactly — ufw/firewalld/netsh
  command blocks (SteamOS wraps ufw in `steamos-readonly disable/enable`), `ip route`-equivalent
  LAN detection (inside WSL it asks Windows via `powershell.exe`, never the 172.x guest IP),
  icanhazip/ipify public IP with IPv4 validation, the `0.0.0.0` vs `127.0.0.1` binding check
  (`docker.published_bindings()`) with a WSL2/Windows portproxy stopgap, `UPDATE
  <auth>.realmlist SET address[, localAddress] WHERE id=1` built from the new catalog
  `realmlist` field (Tortoise has no `localAddress` column), CGNAT detection (100.64/10 or a
  private 'public' IP), and the router DHCP-reservation / TCP-forwarding / DuckDNS / hairpin-NAT
  steps as `manual_steps`. Linux firewall commands run under `sudo -n`; a password-needing sudo
  is a reported skip with the exact command, not a hang. `write_client_realmlist()` edits the
  user's own client (retail `Data/<locale>/` or repack top-level layout). Not done: a true
  from-outside port-forward probe (needs an external checker; from inside the LAN most routers
  have no hairpin NAT, so a local connect proves nothing — `platform.probe_tcp` says `unknown`).
- **Phase 4 record (2026-08-20):** UI is three widgets + `main.py` wiring, all call-down/signal-up.
  `LogPanel.run(source)` runs any `Iterator[str]` factory on a `QThread` (`_StreamWorker` emits
  `line`/`finished`; `run_finished(ok, msg)` goes up; Stop cooperatively ends an endless job).
  `CatalogView(catalog, installer_factory, log_panel, pick_dir=…)` asks for the server folder
  (+ the user's client folder only for `requires_client_dir` games), preflights, streams
  `Installer.run()` into the panel, emits `installed(game, server_dir, client_dir)`.
  `ControllerView(entry, ControllerServices)` — Server (status/start/stop, §12 message), Console
  (`docker logs -f` follow, command line, Create-account form = `account create` + `account set
  gmlevel … -1`, passwords never echoed; transport is `controller_wow_wotlk/console.py`'s
  `docker attach --sig-proxy=false` one-shot), Modules (every manifest family from the store;
  install/remove through the applier; report shows rebuild/restart), Networking (LAN/internet
  radio → `networking.plan()` text incl. manual steps → Apply → report). `ControllerServices.
  for_wotlk()` is the real wiring; tests inject fakes. `state.py` remembers installs in
  `config_dir()/state.json`; `main.py` builds Catalog + one tab per remembered install and adds a
  tab when the catalog view reports a finished install. UI tests run offscreen
  (`tests/conftest.py` sets `QT_QPA_PLATFORM=offscreen`). Not built (later phases/UI polish): a
  Maintenance tab (cache clear/backups/SQL — `maintenance.py` is still placeholders), module
  prompts UI (values come from manifest defaults for now), a rebuild button (the report names
  the need; `docker compose up -d --build` wiring is a follow-up), per-game controllers beyond
  WotLK (`for_wotlk()` is the only services factory; other catalog entries get a Catalog tile
  but no controller tab until their `controller_<acronym>/` exists).
- **Phase 5 record (2026-08-20):** 5.4 `yulon/update.py` compares `__version__` with the latest
  GitHub release tag (numeric compare; offline/rate-limit/odd tag → no banner, never a crash)
  and `main.py` shows a non-blocking banner with the release link from a background thread.
  5.2 `build/pylauncher.spec` bundles `manifests/`, `yulon/catalog/catalog.json` (non-Python
  package data — the first frozen run crashed on exactly this) and the install scripts under
  `archive/guides/**` (748 KB; NOT the 160 MB of guides/MPQs/DBCs), and `yulon/resources.py`
  resolves `manifests_dir()`/`repo_root()` from `sys._MEIPASS` when frozen; local build verified
  with `YULON_SMOKE_TEST=1 build/dist/yulon/yulon.exe` exiting 0. 5.1 `platform.ensure_docker()`
  (Linux: Docker Engine via the distro package manager under `sudo -n`, SteamOS readonly
  toggles, `usermod -aG docker` + a reported re-login; Windows: `ensure_wsl2()` = `wsl --status`
  else elevated `wsl --install --no-distribution` with `reboot_required`, then Docker Desktop
  downloaded to `config_dir()/downloads` and installed silently/elevated with
  `--accept-license --backend=wsl-2`, then started and polled; macOS: Docker.dmg download,
  `hdiutil attach`/`cp -R Docker.app`/`open -a Docker`, polled) returns a `ProvisionReport`
  (done/skipped/manual_steps/reboot_required/docker_ready; `dry_run` = plan only) and the
  installer's preflight turns a not-ready report into `DockerUnavailableError` with the manual
  steps. **Unverified on a fresh machine** — the Windows/macOS paths were built from Docker's
  documented installer switches and are exercised only by the seam tests; the first real run
  on a clean VM is the open gate for 5.1. The user-facing honesty doc is `pylauncher/README.md`
  (WSL2/VM hidden, not removed; what the app asks for; unsigned builds).
- **Tooling:** the `integration` pytest marker is registered in `pyproject.toml`; CI's plain
  `pytest -q` runs the busybox live test on runners that have Docker and skips it elsewhere.
