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
- [ ] **Phase 1 exit criteria met**

---

## Phase 2 — Manifest schema & data port

- [x] 2.1 Finalize the manifest schema (+ `repo` allow-list validation)
- [x] 2.2 Port WotLK modules from `wow-manage.sh` into `manifests/wow-wotlk/`
- [x] 2.3 `modules.py` — load/validate/fetch
- [x] **Phase 2 exit criteria met** (41 manifests + 4 indexes validate in CI; adding a module is a JSON file and an index entry, no Python)

---

## Phase 3 — Catalog (catalog + installer)

- [x] 3.1 `catalog.json` — game list
- [ ] 3.2 `installer.py` — orchestration (Phase 3a: shells out to existing scripts) — **code-complete + unit-tested (2026-08-20); the live Linux run of `python -m yulon.catalog.installer wow-wotlk` is outstanding**
- [x] 3.3 Silent Docker/WSL provisioning stubs wired in (graceful failure until Phase 5)
- [x] 3.4 Networking auto-setup (LAN + internet play; firewall helpers, realmlist updater, router-step prompts) — README §13
- [ ] **Phase 3 exit criteria met** (verified via CLI/test harness — no UI yet) — code for 3.1–3.4 is in and unit-tested; the one-game live install on a Linux box with Docker (`python -m yulon.catalog.installer wow-wotlk`) has not been run yet

---

## Phase 4 — Controller UI (PySide6)

- [ ] 4.1 `log_panel.py` — streaming output widget
- [ ] 4.2 `catalog_view.py` — browsable catalog
- [ ] 4.3 `controller_view.py` — per-install management (+ LAN/internet networking auto-setup control)
- [ ] **Phase 4 exit criteria met**

---

## Phase 5 — Windows/macOS provisioning + packaging

- [ ] 5.1 Silent Docker Desktop / WSL2 provisioning + doc update
- [ ] 5.2 PyInstaller specs finalized
- [ ] 5.3 GitHub Actions release matrix complete
- [ ] 5.4 Application self-update check (README §10)
- [ ] **Phase 5 exit criteria met**

---

## Cross-cutting

> Anything that doesn't cleanly belong to one phase — style-guide amendments, cross-document
> corrections, tooling gotchas, etc.

- **Phase 1 exit criteria — what is still open (2026-08-20):** 1.1–1.5 are implemented and
  green (60 mocked tests; the live suite `tests/integration/test_docker_live.py` passed against a
  real Docker daemon, exercising compose up/healthy/ready/status/port-conflict-guard/down through
  `Controller`). The one unticked half is the literal "against a running AzerothCore compose
  project" check: `tests/integration/test_wotlk_live.py` is written and opt-in
  (`YULON_WOTLK_SERVER_DIR=<fixture dir> pytest -m integration tests/integration`) but has not
  yet been run against a built `tests/fixture.md` install. Tick the exit-criteria box once it has.
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
- **Tooling:** the `integration` pytest marker is registered in `pyproject.toml`; CI's plain
  `pytest -q` runs the busybox live test on runners that have Docker and skips it elsewhere.
