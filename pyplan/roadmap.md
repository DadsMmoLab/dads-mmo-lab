# Yu'lon Roadmap

> **Audience:** humans and LLM coding agents. This file is the _execution_ companion to
> `pyplan/README.md` (the design doc) and `pyplan/style-guide.md` (the code rules). It breaks the
> README's five phases into concrete, ordered, checkable steps.
>
> **Precedence:** `README.md` wins on _what_ to build and _why_; `style-guide.md` wins on _how_ to
> write it; this file wins on _what order to do it in_. If they conflict, flag it — don't silently
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
   - _Definition of done:_ `pip install -r requirements-dev.txt` succeeds on a clean venv.
2. **Add a CI lint/type/test job** (separate from the release matrix in `release.yml`). It runs
   `ruff`, `mypy`/`pyright`, and `pytest` on every push/PR.
   - _Definition of done:_ a deliberately broken commit fails CI on type/lint, not just tests.
3. **Resolve remaining naming debt.** Confirm no uppercase filenames remain under `pylauncher/`,
   and that every importable package directory uses `snake_case` (never hyphens), per
   style-guide §6a. **[style]**
   - _Definition of done:_ `find pylauncher -name '*[A-Z]*'` returns nothing unexpected, and
     every directory containing `__init__.py` has a valid, importable, `snake_case` name.
4. **Pin an AzerothCore compose fixture for integration tests.** Choose and document a specific
   AzerothCore version/tag and compose file used by Phase 1.5's integration suite and by CI, so
   "a real running AzerothCore compose project" is a reproducible fixture, not an ambient
   assumption.
   - _Definition of done:_ a documented fixture (version pin + compose file location) that any
     contributor or CI runner can bring up identically.
5. **Write a minimal contributor/dev-setup doc for `pylauncher/`.** Cover venv creation,
   installing `requirements.txt` + `requirements-dev.txt`, running `main.py`, and running
   `pytest`/`mypy`/`ruff` locally.
   - _Definition of done:_ a new contributor can go from clone to a passing local `pytest` run
     using only this doc.
6. **Establish a shared logging convention.** Decide the `logging` setup (logger naming,
   format, where logs are written per-OS — likely alongside `config_dir()` from §11) that
   `runner.py`, `docker.py`, and every later module will use. **[style]** — style-guide §2
   mandates `logging`, not `print`, everywhere except throwaway local debugging.
   - _Definition of done:_ a `logging.getLogger(__name__)`-based helper exists and is used by at
     least one Phase 1 module as a working example for the rest of the codebase to follow.

---

## Phase 1 — Foundation (testable Python core, no UI)

> `README.md` §7 Phase 1. Build the shared, game-agnostic core first.

### 1.1 `runner.py` — subprocess streaming

1. Implement `stream(command, cwd)` yielding decoded stdout/stderr lines as they arrive, and
   `run(command, cwd)` returning a `CompletedProcess`. **[style]** — fully typed signatures,
   `pathlib.Path` for `cwd`, context-managed `Popen`, no bare `except`.
2. Handle both stdout and stderr without deadlock (read both streams; merge or tag them).
3. _Definition of done:_ unit tests mock `subprocess.Popen` and assert line-by-line streaming
   order and exit-code propagation.

### 1.2 `platform.py` — OS detection + config dir

1. Implement `detect()` returning a normalized platform id (`linux`/`windows`/`macos`).
2. Implement `config_dir()` returning the per-OS state dir from `README.md` §11
   (`~/.local/share/yulon/`, `%APPDATA%\yulon\`, `~/Library/Application Support/yulon/`).
   **[style]** — `pathlib.Path`, no string path munging.
3. Stub `ensure_docker()` / `ensure_wsl2()` (raise `NotImplementedError` with a clear message;
   real logic lands in Phase 5). **[style]** — keep provisioning out of this phase's scope.
4. _Definition of done:_ `config_dir()` returns the correct path on each OS (tested via
   monkeypatched `sys.platform`/env).

### 1.3 `docker.py` — shared Docker lifecycle logic

Each per-game `controller_<acronym>/docker_ctl.py` file stays per-game (matching README §5 and
the scaffold on disk), but its `start`/`stop`/`status`/port-conflict logic is implemented **once**
in a shared `yulon/docker.py` module that each per-game `docker_ctl.py` re-exports — the
_behavior_ is shared and DRY (README §12); the _file_ stays per-game (README §5, style-guide §3).

1. Implement the shared `start(server_dir)`, `stop(server_dir)`, `status()`, `health(container)`
   behavior (used by every per-game `docker_ctl.py`) by shelling out to the `docker` CLI via
   `runner.py`. **[style]** — no Docker SDK; `docker compose` semantics preserved (README §2).
2. Port the polling logic from `dml-start.sh` (`_wait_db_healthy`, `_wait_ready`) into typed
   helpers with timeouts.
3. Implement the **single-instance / port-conflict check** once, in this shared module
   (README §12). **[style]** — DRY: this must be used by every per-game controller, never
   reimplemented.
4. _Definition of done:_ `start()`/`stop()`/`status()` work against a real running AzerothCore
   compose project (per the Phase 0.4 fixture); the port-conflict check correctly blocks a second
   start.

### 1.4 Base controller abstraction

1. Introduce a base `Controller` class (or protocol) that per-game controllers
   (`controller_<acronym>/`) subclass, holding the shared `yulon.docker` behavior and the
   port-conflict check from 1.3. **[style]** — composition over inheritance except where "is-a"
   is genuine (style-guide §2); call-down/signal-up (§5).
2. **Out of scope for this step:** manifest-driven behavior. The base `Controller` does _not_ read
   or know about module/mod manifests yet — that's Phase 2.3's `modules.py`, layered on top later.
   Don't stub manifest reads here prematurely.
3. _Definition of done:_ `controller_wow_wotlk/` can subclass it and inherit start/stop/status
   with zero reimplementation.

### 1.5 Tests

1. `pytest` unit tests (mocked `subprocess`) for `runner.py` and `docker.py` control flow.
2. A small integration suite (marked/skipped when Docker is absent) exercising `docker.py`
   against a real compose project.
3. _Definition of done:_ mocked suite passes in CI without Docker; integration suite passes on a
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
3. _Definition of done:_ a schema document (or Pydantic model) exists and validates a sample
   manifest.

### 2.2 Port WotLK modules from `wow-manage.sh`

1. Read `wow-manage.sh` and extract every module/mod it manages (AH Bot, Solocraft, Transmog,
   etc.) into individual JSON files under `manifests/wow-wotlk/`. **[style]** — data, not code;
   DRY (README §6).
2. Capture the tacit knowledge explicitly: config-key differences (e.g. `AuctionHouseBot.GUIDs`
   plural vs `GUID` singular + `Account`), SQL apply ordering/globs, build targets, and
   dependencies (README §6 "Tacit knowledge to port").
3. _Definition of done:_ the full module list and their config nuances are expressed as JSON; no
   Python change is needed to add a module.

### 2.3 `modules.py` — load/validate/fetch

1. Implement `load_module(path)` and `apply_module(manifest)` in `controller_wow_wotlk/modules.py`.
   **[style]** — convert JSON to a dataclass/Pydantic model at the parse boundary; no bare `Any`
   beyond `json.load()` (style-guide §2).
2. Implement manifest fetch-from-GitHub with a cache + ETag/timestamp (README §11).
3. _Definition of done:_ loading a valid manifest yields a typed model; an invalid `repo` is
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
2. _Definition of done:_ the four v1 servers are described in the catalog (WotLK fully first;
   the other three follow the same `game`-id + metadata shape).

### 3.2 `installer.py` — orchestration (Phase 3a)

1. Implement the install orchestrator that shells out to the existing `install-*.sh` scripts via
   `runner.py`, streaming output to the UI later. **[style]** — separation of concerns: the
   installer orchestrates; it does not contain UI or per-game hardcoding (that's manifest data).
2. Enforce the legal boundary: never fetch client assets; locate/prompt for the user's own client
   (README §3a).
3. Provisions Docker + the virtualization layer and installs modules/mods as part of the install
   path (README §1 goal 5, §3b) — no dependency is left for the user to install by hand.
4. _Definition of done:_ `installer.run()` completes a working server install with zero shell
   interaction, **invoked directly via a CLI entry point or test harness (no GUI exists yet —
   Phase 4 hasn't built the "install" button)**. Verified on a platform where Docker is already
   manually installed (realistically Linux with Docker Engine present) — automatic Windows/macOS
   Docker Desktop provisioning is not required for this step to pass; see 3.3.

### 3.3 Silent Docker/WSL provisioning (deferred to Phase 5)

1. Wire the `platform.ensure_docker()` / `ensure_wsl2()` stubs into the install path so they fail
   gracefully (a clean, logged, catchable error — not a crash) until Phase 5 lands.
   **[blocked]** — real provisioning logic is Phase 5.
2. _Definition of done:_ on a machine without Docker present, `installer.run()` reports a clear
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
4. _Definition of done:_ LAN mode completes end-to-end with no shell; internet mode detects a
   missing forward/CGNAT block and reports a clear, actionable message.

**Phase 3 exit criteria (README §7, with the caveat above):** one-game install completes with
zero shell interaction _at the orchestration layer_, verified on at least one platform that
already has Docker installed. The literal "click install" end-user experience is only exercisable
once Phase 4's `catalog_view.py` exists.

---

## Phase 4 — Controller UI (PySide6)

> `README.md` §7 Phase 4. The GUI, wired to the core via call-down/signal-up.

### 4.1 `log_panel.py` — streaming output widget

1. Implement a reusable widget that consumes `runner.stream()` output without blocking the UI
   thread (Qt thread/worker + signal). **[style]** — call-down/signal-up (§5): the worker emits
   signals; the panel doesn't reach into the runner's internals.
2. _Definition of done:_ a long-running subprocess streams into the panel live, UI stays
   responsive.

### 4.2 `catalog_view.py` — browsable catalog

1. Render the catalog from `catalog.json`; each game tile triggers `installer.run()`.
   **[style]** — the view delegates to the installer; no Docker/business logic in the view (§3).
2. _Definition of done:_ selecting a game and clicking install drives the Phase 3 installer.

### 4.3 `controller_view.py` — per-install management

1. One tab per install: docker lifecycle (start/stop/status/logs), live console, maintenance
   (cache clear, backups, SQL), and module/mod management driven by manifests.
   **[style]** — the view calls down into `controller_wow_wotlk/` methods; it never shells out
   directly.
2. Surface the single-instance/port-conflict block as a clear user message (README §12).
3. Surface the LAN / internet-play networking auto-setup (§3.4) as a selectable control, showing
   progress and the router-step prompts the app can't automate. **[style]** — the view delegates
   to §3.4's helpers via the controller; no `netsh`/`ufw` shelling in the view (§3/§5).
4. _Definition of done:_ full start/stop/logs/accounts/module-toggle workflow via GUI only.

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
   Windows, a Linux VM via Docker Desktop on macOS) is _hidden_, not _removed_ — per the README §8
   mitigation for the "no WSL expectation vs reality" risk. This is a doc deliverable, not just
   code.
3. _Definition of done:_ a fresh Windows/macOS machine reaches a working Docker environment with
   no user shell interaction, and the shipped user docs accurately describe what's happening under
   the hood for anyone who goes looking.

### 5.2 PyInstaller specs

1. Finalize `build/pylauncher.spec` to bundle `manifests/` and `yulon/` correctly for each target.
   **Bundles a self-contained Python interpreter + all deps (PySide6, etc.) so end users never
   install Python themselves** (README §3b).
2. _Definition of done:_ `pyinstaller build/pylauncher.spec` produces a runnable binary locally.

### 5.3 GitHub Actions release matrix

1. Complete `.github/workflows/release.yml`: `ubuntu-latest` → `.AppImage` (via `appimagetool`),
   `windows-latest` → `.exe`, `macos-latest` → `.dmg`. **[style]** — never attempt local
   cross-builds (README §8).
2. _Definition of done:_ `git push` of a `v*` tag produces all three artifacts automatically.

### 5.4 Application self-update check (README §10)

1. Implement the GitHub Releases version check (compare running `__version__` against the latest
   tag) and a non-blocking notify banner/dialog with a download link, per README §10 and §14
   Next Action 6. **v1 scope is check + notify only** — no auto-download/auto-replace.
2. _Definition of done:_ running an old build against a newer published release shows the notify
   banner; running the latest build shows nothing.

**Phase 5 exit criteria (README §7):** `git push` produces all three platform artifacts
automatically.

---

## Phase 6 — Cross-platform install paths (macOS + native Windows)

> **Not in README §7's phase list** (it is README §9's deferred "Phase 3b": the native
> reimplementation of installers). Raised to its own phase because the macOS pre-alpha run made
> the gap concrete: **all four v1 installers are Linux-only bash scripts** gated on
> `[[ "$OSTYPE" == "linux-gnu"* ]]` and hard-coupled to `pacman`/`systemctl`/`sudo`, so on macOS
> (`darwin*`) and native Windows they fail fast with "Requires Linux (SteamOS)" — _before_ the
> Docker provisioning the app already does can help. This is the "install start but console shows
> nothing" failure: the script streams a few banner lines, exits 1, and the Catalog surfaces a
> bare "install failed". The app must either run these servers cross-platform or refuse to offer
> them off Linux.
>
> **Runtime strategy (decided):** use **Docker Desktop** as the container runtime on _both_
> Windows and macOS. It is materially easier than managing the VMs directly — Docker Desktop
> already owns the Linux VM (macOS) and the WSL2 backend (Windows), so the app just provisions
> Docker Desktop (which 5.1 already does) and drives `docker compose` against it. We do **not**
> build a bespoke VM/WSL2 manager, and we do **not** reimplement installers natively just to
> avoid Docker Desktop — the Linux kernel constraint is satisfied by Docker Desktop itself.
>
> **Scope gate (decided): WotLK first, exclusively.** Phase 6 targets **WoW WotLK only** — it is
> the one v1 server with a full controller (`controller_wow_wotlk/`), so it is the one place
> "100% working coverage" is achievable and checkable end-to-end right now. 6.0's script rehome
> may touch all four games mechanically (it is a path move, not a feature), but 6.1–6.5's actual
> gating/installer/feature work targets WotLK only. **TBC, Vanilla, and Tortoise's own
> cross-platform install paths and controllers are explicitly out of scope for Phase 6 — they are
> Phase 7.** Phase 7 must not start until Phase 6's WotLK exit criteria (6.5) are fully met on
> Linux, macOS, and native Windows.
> **Privilege transparency (decided): no silent escalation of host privileges.** Every install
> path — the Linux bash scripts (bugfix-only until Phase 7 retires them), the native engine
> (6.2/6.3), and `ensure_docker()`'s provisioning — must honor one binding rule: **never add the
> user to the `docker` group, and never write a passwordless `sudo` rule, without the user's
> explicit, informed consent.** Two facts a game-server audience won't infer on its own: (1)
> `docker`-group membership **is** root — `docker run -v /:/mnt --rm -it alpine chroot /mnt sh`
> edits any host file, so there is no privilege boundary to protect; (2) a `NOPASSWD` docker rule
> is therefore **redundant** (pure attack surface, no benefit) and must never be written. The
> first-generation `install-*.sh` scripts did exactly this — `enable_docker_sudo_wrapper()` wrote
> `/etc/sudoers.d/docker-nopasswd` behind `|| true`, so it could fail undetectably — and the native
> engine must not reintroduce it. Incident history lives in `pyplan/checklist.md` (Cross-cutting).
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
5. _Definition of done:_ all four installers resolve, run, and bundle from the new home; `pytest`
   green; no `archive/guides/**/install-*.sh` reference remains in `catalog.json`, the spec, or
   tests. 6.2/6.3 then add their macOS/Windows variants into this same directory.

### 6.1 Honest platform gating (block the fast-fail)

1. Record, per catalog entry, which platforms its install script actually supports (initially
   `linux` for all four), as data in `catalog.json` — not a Python conditional. **[style]** — data
   in manifests (§3/§4); acronyms only (§6).
2. Have `catalog/installer.py` (or `catalog_view.py`) refuse to start an install whose script does
   not support the current `platform.detect()`, with a clear, honest message naming the gap
   ("this server's installer needs Linux/WSL") instead of streaming a script that exits 1.
3. Surface the install script's _actual_ output (not just "exited with status N") in the failed
   dialog, so a script's own error is never swallowed.
4. _Definition of done:_ clicking Install for WotLK on macOS shows the honest unsupported message
   before any subprocess runs; the failed-dialog path shows the script's real error text.
5. **Interactive input handling (the terminal must be able to pause and prompt).** The install
   scripts invoke things that block on stdin — the canonical case is the Linux installer's
   `sudo` prompt during `pacman`/`systemctl` steps, but also `pacman` mirror/country selection
   and `y/N` confirmations. The in-app terminal must **pause** on that prompt, surface it, and
   let the user type an answer (a modal dialog for secret/structured input, not an invisible
   hang), then forward it to the subprocess's stdin and resume streaming. **[style]** — the
   stdin write lives in `runner.py` (core), driven by the view via call-down/signal-up; the view
   never shells out and never touches the subprocess handle directly (§3/§5). This is distinct
   from 6.2/6.3 moving off `sudo`: the Linux path (and any script that prompts) still needs it,
   and 6.2/6.3 (Docker Desktop, no `sudo`) must still handle any non-`sudo` prompts they emit
   through the same shared input path.
   - _Definition of done:_ a script that reads a line from stdin (or a real `sudo` step) causes
     the log panel to stop on that line, present a prompt dialog, and forward the typed value
     back to the process; the stream then advances on the next line instead of deadlocking,
     EOF-ing the prompt, or erroring "no tty". Verified once on Linux (real `sudo`) and once
     against the macOS/Windows installer variants' own prompts via the shared path.

### 6.2 macOS install path — the shared native install engine

> Per `pyplan/phase6-decisions.md`, macOS (and 6.3's Windows) install is **one shared, typed
> Python install engine**, not per-platform script variants — dispatched by `catalog.json` data:
> `install.platforms` = where the entry is installable at all (drives the 6.1 refusal),
> `install.script_platforms` = where the *bash script* is the mechanism; anything in `platforms`
> but not `script_platforms` runs the native engine. WotLK lands `platforms: ["linux","macos",
> "windows"]`, `script_platforms: ["linux"]`, so **Linux takes zero new code paths**. Runtime is
> Docker Desktop on both non-Linux platforms (already provisioned by 5.1); no `pacman`/
> `systemctl`/`sudo`, no manual VM management.

1. Implement `NativeInstaller` (in `catalog/installer.py` or a sibling) with the **exact contract
   of today's `Installer.run(options, cancel) -> Iterator[str]`**, so the catalog view, log panel,
   and job runner need no changes. **[style]** — one shared implementation (§4); per-game specifics
   stay in `catalog.json`/manifests (§3); typed, dataclass-led (§2).
2. **Compose generation** (`rust-prior-art.md` §2): the three-file split — base
   `docker-compose.yml` (services/names/images/ports/binds/`name:`), a runtime-only override
   (`AC_*` env + `./modules` mount; auto-loaded and rewritten, so nothing structural may live
   here), and a never-auto-loaded `docker-compose.build.yml` — plus a merged `.env` holding only
   non-default keys. Build calls pass `-f base -f override -f build` (a bare `docker compose
   build` in that directory builds *nothing* and exits 0); `up` stays bare. Reuse
   `docker.pin_project_name()` and `docker.start_staged()`/`stop_staged()`; MySQL/loopback pinning
   and worldserver `stdin_open`/`tty`/`stop_grace_period` per prior-art §2.
3. **Preflight — refuse, don't warn** (`rust-prior-art.md` §3): RAM 2 GB/job (refuse < 6 GB, warn
   < 8 GB), Docker data-root refuse < 40 GB, games-dir refuse < 8 GB, CPU-vs-RAM advisory, the
   5-second bind-mount probe, `server_dir_problem()` (OneDrive/iCloud/UNC/mapped-drive), and the
   port-conflict check *before* the build. Privilege consent belongs in preflight, not after the
   fact: before any `docker`-group join or privileged provisioning step, name the change, state
   plainly that group membership == root (with the `-v /:/mnt` chroot example), and require an
   explicit opt-in; never write a `sudoers.d`/`NOPASSWD` docker rule and never `chmod 666` the
   docker socket — both are redundant beside the group and violate the preamble's
   no-silent-escalation rule. macOS specifics are **unwritten in the Rust prior
   art** — resolve Docker Desktop's data root from its settings JSON (`DataFolder`/`diskPath`,
   absent = default) and write the macOS firewall/driver facts fresh.
4. **Staged, resumable install** (`rust-prior-art.md` §1): stage order recorded by NAME, a state
   file re-checked against disk evidence, `preflight`/`guard` never recorded complete, honest
   cancel copy (BuildKit keeps finishing its current step — desirable layer cache), and a
   resumable client-data download (`curl --retry 30 --continue-at -`).
5. **`keep_awake()`** — a dad closing the lid mid-compile must not suspend the Docker Desktop VM;
   `caffeinate` on macOS (shared interface also covering 6.3's `SetThreadExecutionState`).
6. **Readiness** — poll `docker inspect … StartedAt` + `docker logs --since <StartedAt>` (never
   `--tail`); bounded probes drain their pipes.
7. _Definition of done:_ the native engine completes a working WotLK server on a real macOS
   machine (Docker Desktop, no other Linux), zero shell interaction, streaming output to the
   console — with a clean resume after a mid-build cancel and a clean second install to a
   different directory — and the preflight records explicit consent for the docker-group join,
   with no `sudoers.d`/`NOPASSWD` write and no socket `chmod 666` anywhere in the path.

### 6.3 Native Windows install path

1. Drive the **same native engine** from 6.2 against Docker Desktop's **WSL2 backend** (the app
   already provisions WSL2 + Docker Desktop via `ensure_wsl2()`/`ensure_docker()`), rather than
   managing WSL2/VM directly or shipping a Linux distro. **[blocked]** — 6.2 establishes the shared
   engine first.
2. **Windows-specific hardening** (`rust-prior-art.md` §4), each item verified not assumed:
   `docker.exe` discovery (override → per-user install path → `%ProgramFiles%` → bare `docker`;
   Docker Desktop.exe with **no** bare-name fallback); absolute-path `git` discovery; `core.
   autocrlf=input` *and* `http.version=HTTP/1.1` on the clone; path canonicalization (`C:\Users\x`
   == `C:/Users/x` == `/mnt/c/…`); strip `\r` from anything crossing `wsl.exe`; container names
   global per engine (ownership = project-name *or* working-dir match); port probe refuses only on
   `AddrInUse`; spawn with `CREATE_NO_WINDOW`.
3. **Fix *before* 6.3 can pass** — the three Windows provisioning defects recorded in
   Cross-cutting (installer-download TLS cert failure; `Start-Process 'Docker Desktop'` resolving
   nowhere; PATH not re-read so `docker_ready()` can never succeed on the first run), plus the
   nested-virtualization gate and the `which()`/`wsl.exe` UTF-16 traps. A clean-box run is the only
   evidence that counts — the baseline VM is no longer clean.
4. _Definition of done:_ the native engine completes a working WotLK server on a real, clean
   Windows 11 machine (no Linux distro, no pre-existing Docker/WSL), zero shell interaction, with
   the same resume and second-install checks as 6.2.

### 6.4 Tests & gates

1. Unit-test the platform gating (6.1) through the `platform.detect()` seam, and the macOS/Windows
   script resolution (6.2/6.3) through `catalog.json` — no real macOS/Windows machine needed for
   the mocked suite.
2. Live-gate 6.2 on a real macOS machine and 6.3 on a real Windows 11 machine (both currently
   unverified — the whole point of this phase), **WotLK only** (per the scope gate above).
3. Assert the privilege-transparency rule structurally: a test fails if any install/provision path
   emits a `sudoers.d`/`NOPASSWD` write, a docker-socket `chmod 666`, or a bare
   `usermod -aG docker` without a recorded consent — parse the emitted argv through the same
   mocked seam as the port-conflict check, so the native engine cannot reintroduce the bug.

### 6.5 Full WotLK feature coverage on Linux, macOS, and native Windows (Phase 6 exit gate)

> This is the actual "100% working coverage" bar for Phase 6. Every feature the app has already
> built for WotLK (Phases 1–5) must be **live-gated and passing on all three platforms**, not just
> "installs." A feature that only works on the Ubuntu VM that originally proved it is not done —
> it is a Linux-only regression waiting to be found on macOS/Windows the way 6's own bug was.

1. **Install.** `installer.run()` completes WotLK end-to-end with zero shell interaction — Linux
   (already proven), macOS (6.2), native Windows (6.3). Includes the install-time robustness the
   native engine must ship: staged/resumable install (a mid-build cancel resumes, never re-runs the
   import), preflight floors that refuse-not-warn (RAM/disk/data-root + bind-mount probe +
   `server_dir_problem()`), privilege transparency (no silent docker-group join or `sudoers.d`
   write anywhere in the path), `keep_awake()` across the compile, and honest cancel copy — each
   live-gated, not just unit-tested, on all three platforms.
2. **Server lifecycle (README §12).** Start/stop/status/health polling, and the single-instance
   port-conflict guard, all correct on each platform's `docker compose` (native Linux Engine,
   Docker Desktop's macOS VM, Docker Desktop's WSL2 backend on Windows). This item also closes the
   four lifecycle follow-ups from the staged start/stop review (Cross-cutting), each a real
   coverage gap, not polish:
   - **A deliberate "Stop and remove containers" action** on the Server tab, wired to
     `docker.stop()` — nothing can remove a container today, so a second install of the same game
     to a new directory dies with `Conflict. The container name is already in use`, and a wedge
     container survives every Stop/Start.
   - **Rename `docker_ctl.py`'s `stop` export** (to `teardown`, or make the compose primitives
     private) so the next contributor adding restart-after-module-apply doesn't silently reinstate
     the import re-run by reaching for the shorter, button-named one.
   - **A deliberate "repair / re-import" action** — an install interrupted *after* containers were
     created but *before* the import finished now comes up against an unimported DB on Start, with
     a warning about what it overwrites; the installer stays the only healthy-path importer.
   - **Measure the `stop_grace_period`** against a populated server (1600–2000 playerbots) before
     picking a value — the 10-second SIGTERM grace is likely too short and SIGKILLs a live save;
     do not guess a number.
3. **Console (live GM).** `controller_wow_wotlk/console.py`'s `docker attach --sig-proxy=false`
   pty transport is POSIX-only by design. **Account creation no longer depends on it** — the
   SOAP-based path was shown to rest on a false premise (SOAP cannot create the account SOAP needs;
   `phase6-decisions.md` + Cross-cutting), so account creation is the SRP6-over-`DockerSql` path in
   item 4 on every platform. What remains for the console itself is *live GM commands*:
   attach/detach safely (Ctrl+P/Ctrl+Q, never Ctrl+C) and the `CONTROLS-2.md` GM surface, on
   Linux/macOS where a pty exists. The Windows "no interactive GM console" gap is a smaller,
   separately tracked item — either land an argued exception to style-guide §7 (attach over the
   engine API instead of the `docker attach` client) or explicitly re-scope it; it is never left
   silently broken.
4. **Account creation (`CREATE-ACCOUNTS.md` / `CONTROLS-1.md`).** Compute AzerothCore's SRP6
   registration values (`x = SHA1(salt || SHA1(UPPER(user) ":" UPPER(pass)))`, `verifier = 7^x mod
   N`, little-endian, zero-padded to 32 bytes) and insert through the existing `DockerSql` seam —
   the *primary* account path on all three platforms, because SOAP cannot bootstrap the first
   account and `docker attach` refuses piped stdin against a TTY container. Do not echo passwords,
   handle "account already exists," and prove byte-exactness against a verifier the server itself
   wrote. This is the one page the WotLK README calls out as "bookmark and share with family," so
   it is a first-class coverage item.
5. **Maintenance (`CONTROLS-1.md`).** Cache clear, database backup and restore, and any SQL
   changes. **This is the known hole:** `controller_wow_wotlk/maintenance.py` is still a
   placeholder (Phase 4 record), and the rebuild/restart wiring is a follow-up — these must be
   implemented (not stubbed) and live-gated on all three platforms before Phase 6 exits.
6. **Modules/mods (Phase 2/4.3).** Install/remove through the shared applier, and the manifest
   store's GitHub refresh with the bundled-copy fallback, work identically on all three platforms
   (path handling via `pathlib.Path` should make this "just work," but it must be _verified_, not
   assumed). Includes the rebuild (`docker compose up -d --build`) and restart the applier reports
   as needed.
7. **Networking auto-setup (README §13, full `WoW-Wotlk-NETWORKING.md` scope).** LAN *and*
   internet-play, item-for-item against the guide, on all three platforms:
   - firewall open (`ufw` / `firewalld` on Linux; `netsh` + Windows "Private" network check on
     Windows; **macOS's firewall path must actually be designed and implemented** — currently
     unverified/undesigned, see Cross-cutting gap);
   - WSL2 `netsh interface portproxy` when compose ports are `127.0.0.1`-bound;
   - local IP (LAN) and public IP (internet) detection, incl. WSL2 reading the Windows host
     address, never the `172.x` guest;
   - realmlist updater (`address`/`localAddress`) + `write_client_realmlist()` for the user's own
     client;
   - the `0.0.0.0` vs `127.0.0.1` binding check;
   - CGNAT detection, dynamic-IP/DuckDNS guidance, and the router DHCP-reservation/TCP-forwarding
     steps the app must detect-and-prompt for (not silently fail on).
8. **Self-update check (README §10).** The GitHub Releases check and banner behave identically
   (this is pure Python/HTTP, but confirm no platform-specific path/permission issue in
   `config_dir()` usage).
9. **Packaging (Phase 5).** The `.dmg` (macOS) and `.exe`/zip (Windows) artifacts from `5.3`'s
   release matrix are the actual tested vehicle — WotLK live-gating for this step happens against
   the **packaged app**, not just `python main.py` from source, so PyInstaller bundling gaps
   (Phase 5.2's own history of surprises) are caught here too.
10. **User-facing README topics (`pylauncher/README.md` + `archive/guides/wow-wotlk/README.md`).**
    Every topic the WotLK README covers — install & first-time setup (HOWTO), daily-use controls
    (CONTROLS-1/-2), account creation, networking, Windows WSL2 usage, unsupported-platform
    honesty — is accurately reflected in the shipped docs for the platform a user is actually on.
    This is a doc deliverable, not just code: the app must not ship a "click Install" that the
    docs claim works on macOS while 6.2 is unfinished.
11. _Definition of done:_ every item above passes on a real Linux machine, a real macOS machine,
    and a real Windows 11 machine, run against the packaged (`.AppImage`/`.dmg`/`.exe`) artifact
    where applicable — not just a from-source dev run.

**Phase 6 exit criteria:** WoW WotLK — and only WotLK — has **100% working feature coverage**
(6.5's full list) on Linux, macOS, and native Windows, with zero shell interaction for user, and off-Linux
install clicks never silently fast-fail. **Phase 7 must not begin until every item in 6.5 is
verified on all three platforms.**

---

## Phase 7 — Full coverage for the remaining WoW servers (TBC, Vanilla, Tortoise)

> **[blocked]** on Phase 6's exit criteria. Extends WotLK's now-proven cross-platform pattern
> (6.0–6.5) to the other three v1 servers, one at a time, each held to the **same** "100% working
> coverage on Linux, macOS, and native Windows" bar Phase 6 set for WotLK — not a lesser bar.
> **v1 scope is these four servers only** (README §1); no server outside this list is considered
> until Phase 7 is done.

### 7.1 `controller_wow_tbc/` — full controller + cross-platform coverage

1. Build the WoW TBC controller package (`controller.py`, `docker_ctl.py`, `console.py`,
   `maintenance.py`, `modules.py`), mirroring `controller_wow_wotlk/`'s shape exactly
   (style-guide §4 DRY — reuse `yulon.docker`/base `Controller`, do not reimplement).
2. Manifest port: TBC's own modules/mods (if any exist beyond the base install), following Phase
   2's process for WotLK.
3. Repeat 6.1–6.5 for TBC: honest platform gating, macOS install path, native Windows install
   path, and the full 6.5 feature-coverage gate (install, lifecycle, console, modules, networking,
   self-update, packaging) on Linux, macOS, and native Windows.
4. _Definition of done:_ TBC has 100% working feature coverage on all three platforms, matching
   6.5's bar item-for-item.

### 7.2 `controller_wow_vanilla/` — full controller + cross-platform coverage

1. Same shape as 7.1, for WoW Vanilla.
2. _Definition of done:_ Vanilla has 100% working feature coverage on all three platforms.

### 7.3 `controller_wow_tortoise/` — full controller + cross-platform coverage

1. Same shape as 7.1, for WoW Tortoise. Tortoise's installer is currently `status: wip` and
   unverified with a real client (`catalog.json`/checklist 3.1 record) — this step must resolve
   that before claiming coverage, not carry the caveat forward silently.
2. _Definition of done:_ Tortoise has 100% working feature coverage on all three platforms, and
   its `catalog.json` `status` is promoted from `wip` once verified.

### 7.4 Cross-server regression pass

1. Re-run Phase 6's WotLK 6.5 coverage gate once more after 7.1–7.3 land, to confirm the shared
   layers (`docker.py`, base `Controller`, `runner.py`, `platform.py`, `networking.py`) were
   extended for the new servers without regressing WotLK (style-guide §4 DRY makes this the main
   risk: a change made "for TBC" that quietly breaks WotLK).
2. _Definition of done:_ all four v1 servers pass their full feature-coverage gate on Linux,
   macOS, and native Windows in the same pass — no server's coverage regresses another's.

**Phase 7 exit criteria:** all four v1 servers (WotLK, TBC, Vanilla, Tortoise) have 100% working
feature coverage on Linux, macOS, and native Windows. **Phase 8 must not begin until Phase 7 is
fully met.**

---

## Phase 8 — Feature parity with The Lab + Hypeer Launcher (TBD)

> **[blocked]** on Phase 7.
> (Phase 4/6); Phase 8 is a *feature* phase: folding the capabilities of two existing companion
> tools into Yu'lon so users don't need three apps.
>
> **Scope (still TBD — placeholder):**
> - **The Lab** ([github.com/0xVe1L/the-lab](https://github.com/0xVe1L/the-lab)) — the graphical
>   server manager the WotLK guides already recommend. Its known feature set (from
>   `archive/guides/wow-wotlk/README.md` + `WoW-WotLK-HOWTO.md`):
>   - **My Party** — build a 5-man bot group (pick role, class, spec, level); The Lab spawns and
>     gears them. (README §9 currently lists this as out of v1 scope / "separate later project".)
>   - **Item database + in-game mail** — search any item and mail it to a character.
>   - **Teleport** — to any location / map coordinates.
>   - **Module management** — toggle modules and tune settings in-app (Yu'lon already has this via
>     manifests/applier in Phase 2/4.3; this is parity, not greenfield).
>   - **Steam integration** — add the server + WoW client to the user's Steam library automatically.
>   - **Auto-shutdown on WoW close** — stop the server when the game client exits.
> - **Hypeer Launcher** — NOT referenced anywhere in this repo (no file, doc, or prior note). Its
>   scope is genuinely **unknown/TBD**: identify what it is, which of its features are in demand,
>   and whether they belong in Yu'lon, before any planning. Do not invent a feature list for it.
>
> **Ordering rule:** Phase 8 must not begin until Phase 7 exits (all four v1 servers at full
> coverage). When Phase 8 is scoped it must be broken down with the same numbered-step /
> Definition-of-done structure as every other phase in this document — not left as free-form
> prose. The in-game/DB-touching features (My Party, item mail, teleport) are the heavy end and
> were previously scoped out of v1 (README §9); folding them into Yu'lon is a deliberate v1-scope
> *expansion*, so each must earn its own step + DoD rather than ride along on Phase 7's servers.

**Phase 8 exit criteria:** TBD — to be defined when this phase is scoped, after Phase 7 exits and
after "Hypeer Launcher" has been identified and its in-scope features enumerated.

---

## Phase 9 — UI/UX pass for the v1 Alpha (TBD)

> **[blocked]** on Phase 8. This IS the UI/UX pass (Phase 8 was feature parity, not UI). Its goal
> is to take the feature-complete app from Phases 6–8 and polish the interface into a coherent,
> dad-friendly **v1 Alpha** — the first build that is meant to be shown to non-developers as
> "this is what it will look and feel like," not just "this is what it can do." Scope is **TBD**;
> it must be broken down with the same numbered-step / Definition-of-done structure as every other
> phase once Phase 8 exits, and must honor the project's core promise (README §1 goal 1): no
> user-facing shell, buttons instead of terminals, clear/actionable messages.

**Phase 9 exit criteria:** TBD — to be defined when this phase is scoped, after Phase 8 exits. The
end state is a shippable **v1 Alpha**: all four v1 servers feature-complete (Phases 6–8) with a
polished, consistent UI/UX across Linux, macOS, and native Windows.

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
