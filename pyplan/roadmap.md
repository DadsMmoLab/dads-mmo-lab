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

> **Flagged deviation from README §7:** the README's own text recommends the order
> **"2 → 1 → 3 → 4 → 5"** (manifest schema before the foundation code). This roadmap instead
> sequences **Phase 1 before Phase 2** (foundation first). This is a deliberate, flagged deviation,
> not a silent contradiction: `runner.py`/`docker_ctl.py` need to exist and be testable (Phase 1)
> before the manifest-driven module loader (`modules.py`, Phase 2.3) has anything concrete to load
> into. Phase 2.1/2.2 (schema definition and the data port from `wow-manage.sh`) could genuinely
> start in parallel with Phase 1 with no blocking dependency, if capacity allows — only Phase 2.3
> depends on Phase 1 being done. If you want to strictly honor README's literal order, treat
> Phase 2.1/2.2 as running concurrently with Phase 1 rather than strictly after it.

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
3. **Resolve remaining naming debt.** Confirm no uppercase filenames remain under `py-launcher/`
   (the `Catalog/` → `catalog/` and `Catalog_view.py` → `catalog_view.py` renames are already
   done; verify nothing else slipped through). **[style]** — style-guide §6a.
   - *Definition of done:* `find py-launcher -name '*[A-Z]*'` returns nothing unexpected.
4. **Pin an AzerothCore compose fixture for integration tests.** Choose and document a specific
   AzerothCore version/tag and compose file used by Phase 1.5's integration suite and by CI, so
   "a real running AzerothCore compose project" is a reproducible fixture, not an ambient
   assumption.
   - *Definition of done:* a documented fixture (version pin + compose file location) that any
     contributor or CI runner can bring up identically.
5. **Write a minimal contributor/dev-setup doc for `py-launcher/`.** Cover venv creation,
   installing `requirements.txt` + `requirements-dev.txt`, running `main.py`, and running
   `pytest`/`mypy`/`ruff` locally.
   - *Definition of done:* a new contributor can go from clone to a passing local `pytest` run
     using only this doc.
6. **Establish a shared logging convention.** Decide the `logging` setup (logger naming,
   format, where logs are written per-OS — likely alongside `config_dir()` from §11) that
   `runner.py`, `docker_ctl.py`, and every later module will use. **[style]** — style-guide §2
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
   (`~/.local/share/yulon/`, `%APPDATA%\Yulon\`, `~/Library/Application Support/Yulon/`).
   **[style]** — `pathlib.Path`, no string path munging.
3. Stub `ensure_docker()` / `ensure_wsl2()` (raise `NotImplementedError` with a clear message;
   real logic lands in Phase 5). **[style]** — keep provisioning out of this phase's scope.
4. *Definition of done:* `config_dir()` returns the correct path on each OS (tested via
   monkeypatched `sys.platform`/env).

### 1.3 `docker_ctl.py` — shared Docker lifecycle logic (flagged naming/placement conflict)

> **Flagged conflict:** README §5's project-structure tree and style-guide §3's ownership table
> both place `docker_ctl.py` *inside* each `controller-<acronym>/` package (per-game), while
> README §12 talks about "the shared `docker_ctl.py` / `runner.py` layer." This roadmap resolves
> the conflict as follows, rather than silently picking one reading: the **per-game
> `controller-<acronym>/docker_ctl.py` files stay** (matching README §5 and the actual scaffold on
> disk), but their `start`/`stop`/`status`/port-conflict logic is implemented **once** in a shared
> base class/helper (see 1.4) that each per-game `docker_ctl.py` calls into — so the *behavior* is
> shared and DRY (satisfying README §12's intent) while the *file* stays per-game (satisfying
> README §5 and style-guide §3's ownership table). If this reading is wrong, README §5/§12 should
> be reconciled directly rather than left ambiguous.

1. Implement the shared `start(server_dir)`, `stop(server_dir)`, `status()`, `health(container)`
   behavior (used by every per-game `docker_ctl.py`) by shelling out to the `docker` CLI via
   `runner.py`. **[style]** — no Docker SDK; `docker compose` semantics preserved (README §2).
2. Port the polling logic from `dml-start.sh` (`_wait_db_healthy`, `_wait_ready`) into typed
   helpers with timeouts.
3. Implement the **single-instance / port-conflict check** once, in this shared behavior
   (README §12). **[style]** — DRY: this must be inherited by every per-game controller, never
   reimplemented.
4. *Definition of done:* `start()`/`stop()`/`status()` work against a real running AzerothCore
   compose project (per the Phase 0.4 fixture); the port-conflict check correctly blocks a second
   start.

### 1.4 Base controller abstraction

1. Introduce a base `Controller` class (or protocol) that per-game controllers
   (`controller-<acronym>/`) subclass, holding the shared `docker_ctl` behavior and the
   port-conflict check from 1.3. **[style]** — composition over inheritance except where "is-a"
   is genuine (style-guide §2); call-down/signal-up (§5).
2. **Out of scope for this step:** manifest-driven behavior. The base `Controller` does *not* read
   or know about module/mod manifests yet — that's Phase 2.3's `modules.py`, layered on top later.
   Don't stub manifest reads here prematurely.
3. *Definition of done:* `controller-wow-wotlk/` can subclass it and inherit start/stop/status
   with zero reimplementation.

### 1.5 Tests

1. `pytest` unit tests (mocked `subprocess`) for `runner.py` and `docker_ctl.py` control flow.
2. A small integration suite (marked/skipped when Docker is absent) exercising `docker_ctl.py`
   against a real compose project.
3. *Definition of done:* mocked suite passes in CI without Docker; integration suite passes on a
   machine with Docker.

**Phase 1 exit criteria (README §7):** `docker_ctl.start()`/`stop()`/`status()` work against a
running AzerothCore compose project, and the mocked `pytest` suite passes in CI without Docker.

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

1. Implement `load_module(path)` and `apply_module(manifest)` in `controller-wow-wotlk/modules.py`.
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
   using acronyms for `game` ids. **[style]** — acronyms only (§6).
2. *Definition of done:* at least one game (WoW WotLK) is fully described in the catalog.

### 3.2 `installer.py` — orchestration (Phase 3a)

1. Implement the install orchestrator that shells out to the existing `install-*.sh` scripts via
   `runner.py`, streaming output to the UI later. **[style]** — separation of concerns: the
   installer orchestrates; it does not contain UI or per-game hardcoding (that's manifest data).
2. Enforce the legal boundary: never fetch client assets; locate/prompt for the user's own client
   (README §3a).
3. *Definition of done:* `installer.run()` completes a working server install with zero shell
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
   **[style]** — the view calls down into `controller-wow-wotlk/` methods; it never shells out
   directly.
2. Surface the single-instance/port-conflict block as a clear user message (README §12).
3. *Definition of done:* full start/stop/logs/accounts/module-toggle workflow via GUI only.

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

1. Finalize `build/py-launcher.spec` to bundle `manifests/` and `py/` correctly for each target.
2. *Definition of done:* `pyinstaller build/py-launcher.spec` produces a runnable binary locally.

### 5.3 GitHub Actions release matrix

1. Complete `.github/workflows/release.yml`: `ubuntu-latest` → `.AppImage` (via `appimagetool`),
   `windows-latest` → `.exe`, `macos-latest` → `.dmg`. **[style]** — never attempt local
   cross-builds (README §8).
2. *Definition of done:* `git push` of a `v*` tag produces all three artifacts automatically.

### 5.4 Application self-update check (README §10)

1. Implement the GitHub Releases version check (compare running `__version__` against the latest
   tag) and a non-blocking notify banner/dialog with a download link, per README §10 and §13
   Next Action 6. **v1 scope is check + notify only** — no auto-download/auto-replace.
2. *Definition of done:* running an old build against a newer published release shows the notify
   banner; running the latest build shows nothing.

**Phase 5 exit criteria (README §7):** `git push` produces all three platform artifacts
automatically.

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
- **[legal]** Never bundle/fetch copyrighted client assets; user supplies their own client
  (README §3a).

---

## Out of scope (do not start these in v1)

- My Party / bot group builder, item database + in-game mail, teleport/GM in-game tools
  (README §9).
- Full native reimplementation of installers — Phase 3a wraps existing scripts; 3b is later.
- Code signing / notarization — accept OS gatekeeper warnings for v1 (README §8–9).
- In-place auto-update — check + notify only for v1 (README §10).