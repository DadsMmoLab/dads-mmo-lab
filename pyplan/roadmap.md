# Yu'lon Roadmap

> **Audience:** humans and LLM coding agents. This file is the *execution* companion to
> `pyplan/README.md` (the design doc) and `pyplan/style-guide.md` (the code rules). It breaks the
> README's five phases into concrete, ordered, checkable steps.
>
> **Precedence:** `README.md` wins on *what* to build and *why*; `style-guide.md` wins on *how* to
> write it; this file wins on *what order to do it in*. If they conflict, flag it — don't silently
> pick one.
>
> **Conventions used throughout:** games are referred to by acronym only (WoW, RS, MS, MU — see
> style-guide §6); filenames are lowercase (style-guide §6a); every step that produces Python code
> is subject to the typed-Python and separation-of-concerns rules (style-guide §1–§5).

---

## How to read this roadmap

- Each phase maps 1:1 to `README.md` §7.
- Steps are ordered; a step is **done** only when its "Definition of done" is met.
- Steps marked **[style]** carry a specific style-guide obligation that must be satisfied before
  the step is considered complete.
- Steps marked **[blocked]** depend on an earlier step/phase and must not be started out of order.
- This roadmap sequences **Phase 1 before Phase 2**. Phase 2.1/2.2 (schema definition and the data
  port from `wow-manage.sh`) may run concurrently with Phase 1; only Phase 2.3 (`modules.py`)
  depends on Phase 1 being complete.
- See `pyplan/checklist.md` for the running checklist as this roadmap is executed. Nothing in
  this file should be edited to record notes, decisions made during implementation, or
  discoveries — keep it a clean plan (style-guide §9).

---

## Phase 0 — Tooling & hygiene (pre-work)

> Not in the README's phase list, but required before Phase 1 code lands cleanly.

1. **Pin the toolchain.** Add `requirements-dev.txt` with `pytest`, `mypy` (or `pyright`), `black`,
   and `ruff`, pinned to exact versions. **[style]** — style-guide §2 requires a static type
   checker in CI from Phase 1 onward.
   - *Definition of done:* `pip install -r requirements-dev.txt` succeeds on a clean venv.
2. **Add a CI lint/type/test job** (separate from the release matrix in `release.yml`). It runs
   `ruff`, `mypy`/`pyright`, and `pytest` on every push/PR.
   - *Definition of done:* a deliberately broken commit fails CI on type/lint, not just tests.
3. **Resolve remaining naming debt.** Confirm no uppercase filenames remain under `pylauncher/`,
   and that every importable package directory uses `snake_case` (never hyphens), per
   style-guide §6a. **[style]**
   - *Definition of done:* `find pylauncher -name '*[A-Z]*'` returns nothing unexpected, and
     every directory containing `__init__.py` has a valid, importable, `snake_case` name.
4. **Pin an AzerothCore compose fixture for integration tests.** Choose and document a specific
   AzerothCore version/tag and compose file used by Phase 1.5's integration suite and by CI, so
   "a real running AzerothCore compose project" is a reproducible fixture, not an ambient
   assumption.
   - *Definition of done:* a documented fixture (version pin + compose file location) that any
     contributor or CI runner can bring up identically.
5. **Write a minimal contributor/dev-setup doc for `pylauncher/`.** Cover venv creation,
   installing `requirements.txt` + `requirements-dev.txt`, running `main.py`, and running
   `pytest`/`mypy`/`ruff` locally.
   - *Definition of done:* a new contributor can go from clone to a passing local `pytest` run
     using only this doc.
6. **Establish a shared logging convention.** Decide the `logging` setup (logger naming,
   format, where logs are written per-OS — likely alongside `config_dir()` from §11) that
   `runner.py`, `docker.py`, and every later module will use. **[style]** — style-guide §2
   mandates `logging`, not `print`, everywhere except throwaway local debugging.
   - *Definition of done:* a `logging.getLogger(__name__)`-based helper exists and is used by at
     least one Phase 1 module as a working example for the rest of the codebase to follow.

---

## Phase 1 — Foundation (testable Python core, no UI)

> `README.md` §7 Phase 1. Build the shared, game-agnostic core first.

### 1.1 `runner.py` — subprocess streaming

1. Implement `stream(command, cwd)` yielding decoded stdout/stderr lines as they arrive, and
   `run(command, cwd)` returning a `CompletedProcess`. **[style]** — fully typed signatures,
   `pathlib.Path` for `cwd`, context-managed `Popen`, no bare `except`.
2. Handle both stdout and stderr without deadlock (read both streams; merge or tag them).
3. *Definition of done:* unit tests mock `subprocess.Popen` and assert line-by-line streaming
   order and exit-code propagation.

### 1.2 `platform.py` — OS detection + config dir

1. Implement `detect()` returning a normalized platform id (`linux`/`windows`/`macos`).
2. Implement `config_dir()` returning the per-OS state dir from `README.md` §11
   (`~/.local/share/yulon/`, `%APPDATA%\yulon\`, `~/Library/Application Support/yulon/`).
   **[style]** — `pathlib.Path`, no string path munging.
3. Stub `ensure_docker()` / `ensure_wsl2()` (raise `NotImplementedError` with a clear message;
   real logic lands in Phase 5). **[style]** — keep provisioning out of this phase's scope.
4. *Definition of done:* `config_dir()` returns the correct path on each OS (tested via
   monkeypatched `sys.platform`/env).

### 1.3 `docker.py` — shared Docker lifecycle logic

Each per-game `controller_<acronym>/docker_ctl.py` file stays per-game (matching README §5 and
the scaffold on disk), but its `start`/`stop`/`status`/port-conflict logic is implemented **once**
in a shared `yulon/docker.py` module that each per-game `docker_ctl.py` re-exports — the
*behavior* is shared and DRY (README §12); the *file* stays per-game (README §5, style-guide §3).

1. Implement the shared `start(server_dir)`, `stop(server_dir)`, `status()`, `health(container)`
   behavior (used by every per-game `docker_ctl.py`) by shelling out to the `docker` CLI via
   `runner.py`. **[style]** — no Docker SDK; `docker compose` semantics preserved (README §2).
2. Port the polling logic from `dml-start.sh` (`_wait_db_healthy`, `_wait_ready`) into typed
   helpers with timeouts.
3. Implement the **single-instance / port-conflict check** once, in this shared module
   (README §12). **[style]** — DRY: this must be used by every per-game controller, never
   reimplemented.
4. *Definition of done:* `start()`/`stop()`/`status()` work against a real running AzerothCore
   compose project (per the Phase 0.4 fixture); the port-conflict check correctly blocks a second
   start.

### 1.4 Base controller abstraction

1. Introduce a base `Controller` class (or protocol) that per-game controllers
   (`controller_<acronym>/`) subclass, holding the shared `yulon.docker` behavior and the
   port-conflict check from 1.3. **[style]** — composition over inheritance except where "is-a"
   is genuine (style-guide §2); call-down/signal-up (§5).
2. **Out of scope for this step:** manifest-driven behavior. The base `Controller` does *not* read
   or know about module/mod manifests yet — that's Phase 2.3's `modules.py`, layered on top later.
   Don't stub manifest reads here prematurely.
3. *Definition of done:* `controller_wow_wotlk/` can subclass it and inherit start/stop/status
   with zero reimplementation.

### 1.5 Tests

1. `pytest` unit tests (mocked `subprocess`) for `runner.py` and `docker.py` control flow.
2. A small integration suite (marked/skipped when Docker is absent) exercising `docker.py`
   against a real compose project.
3. *Definition of done:* mocked suite passes in CI without Docker; integration suite passes on a
   machine with Docker.

**Phase 1 exit criteria (README §7):** `docker.start()`/`stop()`/`status()` (via the WotLK
`docker_ctl.py` re-exports) work against a running AzerothCore compose project, and the mocked
`pytest` suite passes in CI without Docker.

---

## Phase 2 — Manifest schema & data port

> `README.md` §7 Phase 2. Turn `wow-manage.sh`'s hardcoded knowledge into data.

### 2.1 Finalize the manifest schema

1. Define the JSON schema for `modules.json`, `ale.json`, `mods.json`, and `kegs/` entries,
   starting from the example in `README.md` §6. **[style]** — acronyms only in `game`/`id` fields
   (`wow-wotlk`, never "Wrath of the Lich King").
2. Add a `repo` allow-list rule: reject any manifest whose `repo` isn't a legitimate open-source
   source (README §3a). **[style]** — this is a data-validation concern, not UI.
3. *Definition of done:* a schema document (or Pydantic model) exists and validates a sample
   manifest.

### 2.2 Port WotLK modules from `wow-manage.sh`

1. Read `wow-manage.sh` and extract every module/mod it manages (AH Bot, Solocraft, Transmog,
   etc.) into individual JSON files under `manifests/wow-wotlk/`. **[style]** — data, not code;
   DRY (README §6).
2. Capture the tacit knowledge explicitly: config-key differences (e.g. `AuctionHouseBot.GUIDs`
   plural vs `GUID` singular + `Account`), SQL apply ordering/globs, build targets, and
   dependencies (README §6 "Tacit knowledge to port").
3. *Definition of done:* the full module list and their config nuances are expressed as JSON; no
   Python change is needed to add a module.

### 2.3 `modules.py` — load/validate/fetch

1. Implement `load_module(path)` and `apply_module(manifest)` in `controller_wow_wotlk/modules.py`.
   **[style]** — convert JSON to a dataclass/Pydantic model at the parse boundary; no bare `Any`
   beyond `json.load()` (style-guide §2).
2. Implement manifest fetch-from-GitHub with a cache + ETag/timestamp (README §11).
3. *Definition of done:* loading a valid manifest yields a typed model; an invalid `repo` is
   rejected.

**Phase 2 exit criteria (README §7):** module list and config nuances fully expressed as JSON; no
code changes needed to add a module.

---

## Phase 3 — Catalog (catalog + installer)

> `README.md` §7 Phase 3. The "store" that holds the user's hand through install.

### 3.1 `catalog.json` — game list

1. Populate `catalog.json` with install metadata per game (repo, script, ports, client steps),
   using acronyms for `game` ids. **[style]** — acronyms only (§6). **v1 scope is exactly four
   servers:** WoW (Vanilla 1.12), WoW TBC, WoW WotLK, and WoW Tortoise.
2. *Definition of done:* the four v1 servers are described in the catalog (WotLK fully first;
   the other three follow the same `game`-id + metadata shape).

### 3.2 `installer.py` — orchestration (Phase 3a)

1. Implement the install orchestrator that shells out to the existing `install-*.sh` scripts via
   `runner.py`, streaming output to the UI later. **[style]** — separation of concerns: the
   installer orchestrates; it does not contain UI or per-game hardcoding (that's manifest data).
2. Enforce the legal boundary: never fetch client assets; locate/prompt for the user's own client
   (README §3a).
3. Provisions Docker + the virtualization layer and installs modules/mods as part of the install
   path (README §1 goal 5, §3b) — no dependency is left for the user to install by hand.
4. *Definition of done:* `installer.run()` completes a working server install with zero shell
   interaction, **invoked directly via a CLI entry point or test harness (no GUI exists yet —
   Phase 4 hasn't built the "install" button)**. Verified on a platform where Docker is already
   manually installed (realistically Linux with Docker Engine present) — automatic Windows/macOS
   Docker Desktop provisioning is not required for this step to pass; see 3.3.

### 3.3 Silent Docker/WSL provisioning (deferred to Phase 5)

1. Wire the `platform.ensure_docker()` / `ensure_wsl2()` stubs into the install path so they fail
   gracefully (a clean, logged, catchable error — not a crash) until Phase 5 lands.
   **[blocked]** — real provisioning logic is Phase 5.
2. *Definition of done:* on a machine without Docker present, `installer.run()` reports a clear
   "Docker isn't available yet — automatic setup lands in a future update" error rather than an
   unhandled exception or silent hang.

### 3.4 Networking auto-setup (README §13)

1. Implement `platform.py` firewall helpers (`open_ports(ports)` / `detect_lan_ip()` /
   `detect_public_ip()`), shelling out via `runner.py`: UFW (Debian/Ubuntu/SteamOS), firewalld
   (Fedora), `netsh` + network-profile check (Windows/WSL2), and WSL2 `netsh portproxy` when
   compose ports are `127.0.0.1`-bound. Carry the auth/world/db port table (3724/8085/3306) as
   `catalog.json` data, not hardcoded Python. **[style]** — one shared implementation (§4), data
   in manifests (§3), typed `pathlib`/dataclass return values (§2).
2. Add a `realmlist` updater that sets `address`/`localAddress` in the auth DB for **LAN**
   (local IP) or **internet play** (public IP), mirroring the `archive/.../WoW-Wotlk-NETWORKING.md`
   SQL and the `0.0.0.0` vs `127.0.0.1` port-binding check.
3. Detect and **prompt** (not silently fail) for the steps the app can't automate: DHCP
   reservation + TCP port forwarding on the router, and CGNAT/public-IP-change/DuckDNS guidance.
4. *Definition of done:* LAN mode completes end-to-end with no shell; internet mode detects a
   missing forward/CGNAT block and reports a clear, actionable message.

**Phase 3 exit criteria (README §7, with the caveat above):** one-game install completes with
zero shell interaction *at the orchestration layer*, verified on at least one platform that
already has Docker installed. The literal "click install" end-user experience is only exercisable
once Phase 4's `catalog_view.py` exists.

---

## Phase 4 — Controller UI (PySide6)

> `README.md` §7 Phase 4. The GUI, wired to the core via call-down/signal-up.

### 4.1 `log_panel.py` — streaming output widget

1. Implement a reusable widget that consumes `runner.stream()` output without blocking the UI
   thread (Qt thread/worker + signal). **[style]** — call-down/signal-up (§5): the worker emits
   signals; the panel doesn't reach into the runner's internals.
2. *Definition of done:* a long-running subprocess streams into the panel live, UI stays
   responsive.

### 4.2 `catalog_view.py` — browsable catalog

1. Render the catalog from `catalog.json`; each game tile triggers `installer.run()`.
   **[style]** — the view delegates to the installer; no Docker/business logic in the view (§3).
2. *Definition of done:* selecting a game and clicking install drives the Phase 3 installer.

### 4.3 `controller_view.py` — per-install management

1. One tab per install: docker lifecycle (start/stop/status/logs), live console, maintenance
   (cache clear, backups, SQL), and module/mod management driven by manifests.
   **[style]** — the view calls down into `controller_wow_wotlk/` methods; it never shells out
   directly.
2. Surface the single-instance/port-conflict block as a clear user message (README §12).
3. Surface the LAN / internet-play networking auto-setup (§3.4) as a selectable control, showing
   progress and the router-step prompts the app can't automate. **[style]** — the view delegates
   to §3.4's helpers via the controller; no `netsh`/`ufw` shelling in the view (§3/§5).
4. *Definition of done:* full start/stop/logs/accounts/module-toggle workflow via GUI only.

**Phase 4 exit criteria (README §7):** full start/stop/logs/accounts/module-toggle workflow via
GUI only.

---

## Phase 5 — Windows/macOS provisioning + packaging

> `README.md` §7 Phase 5. Make it distributable.

### 5.1 Silent Docker Desktop / WSL2 provisioning

1. Implement `platform.ensure_docker()` (Linux: Docker Engine; macOS: Docker Desktop) and
   `ensure_wsl2()` (Windows: WSL2 + Docker Desktop) with detect + verify + retry, and a manual
   fallback dialog (README §8). **[style]** — keep this in `platform.py`; the rest of the app
   stays 100% shared (README §3).
2. Update user-facing docs to honestly communicate that the underlying virtualization (WSL2 on
   Windows, a Linux VM via Docker Desktop on macOS) is *hidden*, not *removed* — per the README §8
   mitigation for the "no WSL expectation vs reality" risk. This is a doc deliverable, not just
   code.
3. *Definition of done:* a fresh Windows/macOS machine reaches a working Docker environment with
   no user shell interaction, and the shipped user docs accurately describe what's happening under
   the hood for anyone who goes looking.

### 5.2 PyInstaller specs

1. Finalize `build/pylauncher.spec` to bundle `manifests/` and `yulon/` correctly for each target.
   **Bundles a self-contained Python interpreter + all deps (PySide6, etc.) so end users never
   install Python themselves** (README §3b).
2. *Definition of done:* `pyinstaller build/pylauncher.spec` produces a runnable binary locally.

### 5.3 GitHub Actions release matrix

1. Complete `.github/workflows/release.yml`: `ubuntu-latest` → `.AppImage` (via `appimagetool`),
   `windows-latest` → `.exe`, `macos-latest` → `.dmg`. **[style]** — never attempt local
   cross-builds (README §8).
2. *Definition of done:* `git push` of a `v*` tag produces all three artifacts automatically.

### 5.4 Application self-update check (README §10)

1. Implement the GitHub Releases version check (compare running `__version__` against the latest
   tag) and a non-blocking notify banner/dialog with a download link, per README §10 and §14
   Next Action 6. **v1 scope is check + notify only** — no auto-download/auto-replace.
2. *Definition of done:* running an old build against a newer published release shows the notify
   banner; running the latest build shows nothing.

**Phase 5 exit criteria (README §7):** `git push` produces all three platform artifacts
automatically.

---

## Phase 6 — Cross-platform install paths (macOS + native Windows)

> **Not in README §7's phase list** (it is README §9's deferred "Phase 3b": the native
> reimplementation of installers). Raised to its own phase because the macOS pre-alpha run made
> the gap concrete: **all four v1 installers are Linux-only bash scripts** gated on
> `[[ "$OSTYPE" == "linux-gnu"* ]]` and hard-coupled to `pacman`/`systemctl`/`sudo`, so on macOS
> (`darwin*`) and native Windows they fail fast with "Requires Linux (SteamOS)" — *before* the
> Docker provisioning the app already does can help. This is the "install start but console shows
> nothing" failure: the script streams a few banner lines, exits 1, and the Catalog surfaces a
> bare "install failed". The app must either run these servers cross-platform or refuse to offer
> them off Linux.

### 6.0 Rehome the install scripts (prerequisite refactor)

> Before any macOS/Windows installer is added, the installers need a real home: today the
> executable `install-*.sh` scripts (and their sourced helpers) live mixed in with the
> human-facing guides under `archive/guides/<game>/`. Move them into a dedicated
> `catalog/installers/` area so `catalog.json` and the packaging spec point at one clean,
> data-only location that 6.2/6.3 will grow with per-platform variants.

1. Move the executable install scripts (plus the sourced helpers `dml-start.sh`, `wow-manage.sh`)
   out of `archive/guides/<game>/` into `pylauncher/catalog/installers/<game>/`, parallel to
   `pylauncher/manifests/`. `archive/guides/` keeps only the human-facing guides/HOWTOs.
   **[style]** — data (scripts) separate from docs; lowercase filenames (§6a); one source of
   truth (§4).
2. Update `catalog.json` `install.script` / `install.script_variants` for all four games (and the
   `apt`/`dnf` WotLK variants) to the new paths.
3. Update `build/pylauncher.spec`'s `script_globs` (and `resources.repo_root()` resolution if
   needed) so the frozen bundle still finds the scripts under `catalog/installers/**`.
4. Update tests that pin the old `archive/guides/...` paths (`test_catalog.py`'s script-existence
   pins; `test_installer.py`'s `script_for` table).
5. *Definition of done:* all four installers resolve, run, and bundle from the new home; `pytest`
   green; no `archive/guides/**/install-*.sh` reference remains in `catalog.json`, the spec, or
   tests. 6.2/6.3 then add their macOS/Windows variants into this same directory.

### 6.1 Honest platform gating (block the fast-fail)

1. Record, per catalog entry, which platforms its install script actually supports (initially
   `linux` for all four), as data in `catalog.json` — not a Python conditional. **[style]** — data
   in manifests (§3/§4); acronyms only (§6).
2. Have `catalog/installer.py` (or `catalog_view.py`) refuse to start an install whose script does
   not support the current `platform.detect()`, with a clear, honest message naming the gap
   ("this server's installer needs Linux/WSL") instead of streaming a script that exits 1.
3. Surface the install script's *actual* output (not just "exited with status N") in the failed
   dialog, so a script's own error is never swallowed.
4. *Definition of done:* clicking Install for WotLK on macOS shows the honest unsupported message
   before any subprocess runs; the failed-dialog path shows the script's real error text.

### 6.2 macOS install path (the macOS pre-alpha blocker)

1. Provide a macOS-installable path for each of the four v1 servers. Since the current scripts are
   SteamOS/`pacman`-bound and Docker Desktop already supplies the Linux kernel on macOS, the
   natural approach is a **macOS variant** of each installer (or a shared reimplementation, per
   README §9 "Phase 3b") that assumes Docker Desktop is present (the app already provisions it via
   `ensure_docker()`) and drives `docker compose` directly — no `pacman`, `systemctl`, or `sudo`
   package installs. **[style]** — keep per-game specifics in `catalog.json`/manifests; one shared
   implementation (§4).
2. Wire the new script(s) into `catalog.json` (`install.script_variants` or an equivalent
   platform→script map) so `Installer.script` resolves the macOS path the same way it already
   picks `apt`/`dnf` variants on Linux (Phase 3 live-gate finding).
3. *Definition of done:* `installer.run()` for WotLK completes a working server on a real macOS
   machine with Docker Desktop, with zero shell interaction, streaming output to the console.

### 6.3 Native Windows install path

1. Provide a native-Windows install path for each of the four v1 servers. The current scripts run
   under WSL2 (Docker Desktop's WSL backend), not native Windows; the app must either run the
   Linux script inside a provisioned WSL2 distro or ship a Windows-native equivalent that drives
   `docker compose` against Docker Desktop's WSL2 backend. **[blocked]** — 6.2 establishes the
   shared non-Linux install shape first.
2. *Definition of done:* `installer.run()` for WotLK completes a working server on a real Windows
   11 machine (no Linux distro pre-installed), with zero shell interaction.

### 6.4 Tests & gates

1. Unit-test the platform gating (6.1) through the `platform.detect()` seam, and the macOS/Windows
   script resolution (6.2/6.3) through `catalog.json` — no real macOS/Windows machine needed for
   the mocked suite.
2. Live-gate 6.2 on a real macOS machine and 6.3 on a real Windows 11 machine (both currently
   unverified — the whole point of this phase).

**Phase 6 exit criteria:** all four v1 servers install end-to-end on Linux, macOS, and native
Windows with zero shell interaction, and off-Linux clicks never silently fast-fail — they either
install or explain exactly why not.

---

## Cross-cutting obligations (apply to every phase)

- **[style]** Typed Python: no unannotated signatures, no `Any` escape hatches (style-guide §2).
- **[style]** Separation of concerns: UI never shells out; controllers never render; manifests
  hold data, code holds behavior (style-guide §3).
- **[style]** DRY: shared logic in `runner.py`/`platform.py`/base controller, never copy-pasted
  per game (style-guide §4).
- **[style]** Call down / signal up for every parent/child relationship (style-guide §5).
- **[style]** Acronyms only, lowercase filenames (style-guide §6, §6a).
- **[style]** `pathlib.Path` everywhere, never raw string path concatenation (style-guide §2).
- **[style]** Dataclasses/Pydantic models over bare `dict[str, Any]` for any structured data with a
  known shape, converted immediately at the JSON/subprocess parse boundary (style-guide §2).
- **[style]** `logging`, not `print`, for anything that ships (style-guide §2; see Phase 0.6).
- **[style]** No bare `except:`; no mutable default arguments; docstrings on every public
  function/class (style-guide §2).
- **[style]** Networking auto-setup ships once in `platform.py` + `catalog.json` data, not once
  per game (README §13); the app detects/prompts for router steps it can't automate rather than
  failing silently.
- **[legal]** Never bundle/fetch copyrighted client assets; user supplies their own client
  (README §3a).

---

## Out of scope (do not start these in v1)

- My Party / bot group builder, item database + in-game mail, teleport/GM in-game tools
  (README §9).
- Full native reimplementation of installers — Phase 3a wraps existing scripts; 3b is later.
- Code signing / notarization — accept OS gatekeeper warnings for v1 (README §8–9).
- In-place auto-update — check + notify only for v1 (README §10).