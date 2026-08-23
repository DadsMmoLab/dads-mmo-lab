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

> **Who runs the macOS gates.** There is no Mac on this side of the project, so every macOS box
> below is verified by **Baerthe**, not by us. That splits each affected item in two: *we* write and
> unit-test the code against the mocked `platform_id` seam, *he* runs it on real hardware and sends
> back evidence. The macOS sub-boxes are therefore **not blockers on our work** — implementation
> proceeds and lands unticked — but they ARE blockers on the parent box and on Phase 6's exit
> criteria. A macOS box is ticked only when his run is written down here the way the Linux and
> Windows runs are: what machine, what version, what was observed, and what failed on the way. "It
> should work on macOS" has never been evidence and is not evidence here either. Where a macOS run
> finds a defect, the fix is ours; where it finds a design gap (Docker Desktop settings paths,
> Gatekeeper/quarantine, the firewall API), record it in `pyplan/phase6-decisions.md` first.

- [x] 6.0 Rehome the install scripts — the eight executable files now live in `pylauncher/catalog/installers/<game>/` (parallel to `manifests/`), `catalog.json` paths are relative to that directory, `resources.installers_dir()` replaces `repo_root()`, `Installer(installers_root=…)` resolves them, and the spec ships the whole tree instead of globbing `archive/guides/**` — so the bundle no longer carries `archive/guides` at all (README §3a bonus). The Tortoise script was renamed to lowercase on the way (`install-tortoise-wow-wsl.sh`, style-guide §6a). Verified: 191 passed, and a frozen PyInstaller build contains all eight scripts under `catalog/installers/` and passes `YULON_SMOKE_TEST`. The DoD's third verb, *run*, is not re-evidenced post-move — but `git show --stat fcd95c5` shows all eight scripts as pure renames (0 changed lines) and `installer.py` already passed `cwd=self.script.parent` before the move, so what runs is byte-identical to what Phase 3 live-gated. `archive/guides/` keeps the human-facing guides plus the four non-catalog installers (Maplestory, Mu Online, RuneScape, the Unbound addon), which no catalog entry references.
- [x] 6.1 Honest platform gating — `install.platforms` is data in `catalog.json` (all four entries `["linux"]`), `Installer.preflight()` raises `UnsupportedPlatformError` with a user-readable message BEFORE any subprocess, the catalog tile disables Install with the reason on the tile ("Use existing…" stays enabled — managing a server works everywhere), `start_install()` refuses before the folder prompts, and a failed script's dialog now carries the script's own last 12 output lines ("It last said: …") instead of a bare exit status. Mocked through the `platform_id` seam per roadmap 6.4; 196 tests green.
- [ ] Rewrite the installer scripts off `pacman`/`systemctl`/`sudo` — the orphaned "update scripts and manifests to use proper systems and features" step, re-homed as a checkbox: it is subsumed by 6.2/6.3's native engine, and closes when WotLK installs without a shell script on macOS and Windows.
- [x] 6.1.5 Interactive input handling — the installer runs on a **pseudo-terminal** and answers `sudo`'s password prompt through a dialog, instead of dying seconds in on `sudo -v`. Two things were needed and the first attempt had neither. **Transport:** `sudo` reads from `/dev/tty`, not stdin, precisely so a piped stdin cannot feed it a password — so `interact(terminal=True)` opens a pty and the child claims it as its *controlling* terminal (via `sh` after exec, not `preexec_fn`: that runs Python bytecode after fork in a process with live Qt threads). **Recognition:** `SUDO_PROMPT` makes sudo announce itself with a per-install random marker, matched exactly — the first version guessed from the shape of a line (`: ? > ]` after a pause), which measurably fires on `[ 43%]`, `Get:12 … [345 kB]`, `note:` and every gcc diagnostic, and opened an application-modal dialog over a two-hour compile. Measured on the Ubuntu VM with sudo temporarily made to demand a password: **pipes → seam asked 0 times, `"sudo: a terminal is required to read the password"`; pty + marker → asked with the exact marker, every attempt read and evaluated by sudo, nothing typed echoed into the log.** Also: `ask()` receives only the prompt (it used to get the whole pending buffer, so `is_secret()` read a neighbouring "directory" and unmasked the password field), ECHO is off on the pty, and `DEBIAN_FRONTEND`/`NEEDRESTART_MODE` are set because a terminal re-arms every apt/dpkg dialog that gates on `isatty()`. Not yet exercised: the macOS/Windows variants' own prompts — those scripts do not run on this platform yet (6.2/6.3).
- [ ] 6.2 macOS install path — the shared **native install engine** (`NativeInstaller`, per `phase6-decisions.md`): `install.platforms`/`install.script_platforms` dispatch, compose three-file generation + `.env` merge, preflight (refuse-don't-warn floors, bind-mount probe, `server_dir_problem()`, port-conflict before build), staged/resumable install, `keep_awake()`, readiness poll — all against Docker Desktop, no `pacman`/`systemctl`/`sudo`, no manual VM management (macOS has no Rust prior art; written fresh)
  - [ ] **macOS (Baerthe)** — the whole of 6.2 on real hardware: a clean install of WotLK end to end through `NativeInstaller` with zero shell interaction, on a Mac that has never had Docker Desktop, and again on one that already does. Wanted back: macOS version and chip (Intel vs Apple silicon — the AzerothCore images are amd64, so Rosetta/`platform` behaviour under Docker Desktop is a real unknown, not a formality), Docker Desktop version, which preflight floors fired, where the Docker data root resolved from its settings JSON, whether the bind-mount probe passed, and the wall-clock of the build
- [ ] 6.3 Native Windows install path — same native engine against Docker Desktop's **WSL2 backend** (no bespoke WSL2/VM manager; `[blocked]` on 6.2); requires the three Windows provisioning defects in Cross-cutting fixed first (TLS cert, `Start-Process` path, PATH not re-read), plus `docker.exe`/`git` discovery, `autocrlf`+`HTTP/1.1`, path canonicalization, CR-strip across `wsl.exe`, and the nested-virtualization gate — proven on a clean box
- [ ] 6.4 Tests & gates (mocked platform-gating + script-resolution tests; live-gate on real macOS and Windows 11 — WotLK only)
  - [ ] **macOS (Baerthe)** — the live gate: the suite green on macOS (`pytest`, `mypy`, `ruff`, `black --check`), plus the same clean-box first run the Windows gate got. The mocked platform-gating tests are ours and run everywhere; this box is only the real machine
- [ ] 6.5 Full WotLK feature coverage on Linux, macOS, and native Windows (the Phase 6 exit gate):
  - [ ] Install (zero shell interaction, all three platforms) — incl. staged/resumable install, preflight floors refusing-not-warning, `keep_awake()`, honest cancel copy
    - [ ] **macOS (Baerthe)** — install, cancel mid-install and confirm the copy is honest about what was left behind, then resume and confirm the staged installer picks up by stage NAME rather than restarting; confirm `keep_awake()` actually holds the Mac awake through a long build (`caffeinate`/`IOPMAssertion` — untested by anyone so far)
  - [ ] Server lifecycle: start/stop/status/health polling + README §12 port-conflict guard
    - [ ] **macOS (Baerthe)** — start, stop, restart, status and health polling against a real install, plus the port-conflict guard with something already holding 3724/8085
  - [ ] Server lifecycle follow-ups (from the staged start/stop review, Cross-cutting): ~~deliberate "Stop and remove containers" action (nothing can remove a container today)~~, ~~rename `docker_ctl.py`'s `stop` export away from a `stop_staged` peer~~, deliberate "repair / re-import" action for an install interrupted before import, and measure `stop_grace_period` on a populated server before picking a value. **The first two landed 2026-08-23** (`30f0b7ff`): `docker.stop()` — which nothing called and which checked nothing — became `remove_staged()`, asking *by project label* rather than by container name (AzerothCore pins names globally, so a name search finds the neighbouring install), refusing on the same ownership census `stop_staged` uses, and verifying what is actually gone instead of trusting `compose down`'s exit code. `docker_ctl.stop` is now `docker_ctl.remove`. A stale warning was removed from two docstrings on the way: both claimed removing containers "forces the next start back onto `compose up -d` and re-running the one-shot database import", which stopped being true at `639fdb8d` when `start_staged` began naming its three services with `--no-deps` — the warning outlived its danger and made a safe action look destructive, which is plausibly why nothing had been built to remove a container. UI: a two-press button on the Server tab, disarmed by Start, Stop or Refresh, whose armed text says the characters are kept. **Live-gated on yulon-ubuntu the same day**, on the playerbots install with 650 accounts: a marked row set, every container removed (`ac-worldserver`, `ac-authserver`, `ac-database`, `ac-db-import`), **both volumes still present** (`wow-server-playerbots_ac-database`, `_ac-client-data`), the stack started again from nothing, **`ac-db-import` never recreated**, and the marked row read back byte-identical with all 650 accounts intact — 14/14 checks. Four mutations proven to die: adding `-v`, asking by name instead of by label, dropping the stranger refusal, and reporting success without checking what is left. Order matters here: restore was proven live *before* teardown was, so a real 386 MB backup of that database existed on disk while the volume claim was being tested. Incidental measurement for the fourth item: a plain `docker compose stop` (10 s default) SIGKILLed the populated worldserver — exit 137 — so 10 s is demonstrably too short. **The third landed 2026-08-23** and the fourth is now measured rather than guessed. `STOP_GRACE_SECONDS = 300` applies to both stop paths (`compose stop -t` and the by-name `docker stop -t` fallback) and to the teardown's `compose down`. Measured on yulon-ubuntu against the playerbots install at **1980 characters online** (1845 bots, waited for the count to plateau): worldserver shutdown **90.7 s**, **73.4 s** and **58.3 s** across three runs, all exit 0 under a grace long enough not to bind; ac-authserver 0.22 s and ac-database 1.4 s are nowhere near the constraint. Almost all of it is one phase — `Closing down DatabasePool 'acore_characters'. Waiting for 7662 queries to finish...` — draining 7400-7700 queued character saves at 90-145 a second. The third run was `stop_staged()` itself with the constant in force, end to end, containers kept. 300 s is ~3.3x the worst sample; the margin is deliberately asymmetric, because an over-long grace only costs time on an already-hung server while a short one costs a player's characters. It agrees with the `stop_grace_period: 5m` the earlier Rust launcher wrote (`rust-prior-art.md` §2) — now a confirmed number rather than an inherited one. **Not the compose key**: that belongs to 6.2's unwritten install engine; what landed is the CLI grace. **Unmeasured**: slower storage, a realm materially larger than ~2000 characters, a shutdown under heavy write load, and whether a genuinely hung worldserver sits out the full 300 s (no hang was induced). The remaining item on this line is the repair/re-import action, which landed the same day — see below
    - [ ] **Repair / re-import** (landed 2026-08-23, unit-tested only): `docker.repair_import()` plus `controller_wow_wotlk/repair.py`'s five-state probe (absent / partial / imported / populated / unreadable), wired through `Controller.repair_import()` to a two-press button on the Server tab that is only *visible* when the probe says there is something to repair. The line that matters is drawn on player data rather than on completeness: it refuses on `populated` and points at Restore, refuses while this install's servers are running, and fails closed on `unreadable`. It runs `compose up --no-deps <import_service>` and nothing else, then re-probes rather than trusting the exit code — a one-shot that fails having done nothing exits the same way as one that worked. Design notes in `phase6-decisions.md`. **Not live-gated**: no Docker daemon was involved, and three assumptions are still owed a real run — that `compose up --no-deps` re-runs an *exited* one-shot rather than no-opping, that `--no-deps` is what makes attached mode terminate, and that a finished AzerothCore import leaves `acore_auth.account` empty. Also known and not fixed: the import reports no progress for the 10-30 minutes it takes, while `follow_logs`/`LogPanel` already exist
    - [ ] **macOS (Baerthe)** — the teardown and the repair action on real hardware: remove containers, confirm both volumes survive, start again and confirm `ac-db-import` is not recreated and the characters are intact. The same 14 checks the Linux run made
  - [ ] Console: `docker attach` pty transport + full `CONTROLS-2.md` GM console (safe attach/detach, GM commands) on Linux/macOS; account creation no longer depends on the console (SRP6 path) — the Windows "no interactive GM console" gap is separately re-scoped, not left silently broken. **The re-scope landed 2026-08-23**: account creation is off the console entirely (its own tab, SRP6), and where there is no pty the Send button and command box are disabled with the reason on the tab, the same shape as 6.1's catalog tile. Following the worldserver log needs no pty and stays enabled. The GM console itself is still Linux/macOS only
    - [ ] **macOS (Baerthe)** — the GM console over the `docker attach` pty transport: attach, run a command, detach WITHOUT killing the worldserver (the detach sequence is the part that has never been exercised off Linux), and confirm the log follow keeps working after a detach
  - [ ] Account creation (`CREATE-ACCOUNTS.md`/`CONTROLS-1.md`): SRP6-over-`DockerSql` (SOAP cannot bootstrap the first account) — byte-exact against a server-written verifier, no password echo, "already exists" handled, all three platforms. **Module and UI wiring landed 2026-08-23**, and the byte-exactness was verified against accounts a real server wrote (same salt in, same verifier out, non-ASCII passwords included). Unticked because only Linux has been exercised end to end
    - [ ] **macOS (Baerthe)** — create an account through the Accounts tab, log into the game with it, and confirm a second create of the same name reports "already exists" without duplicating the row. Byte-exactness is settled by the Linux run; this box is the `DockerSql` seam behaving the same way on Docker Desktop
  - [ ] Maintenance (`CONTROLS-1.md`): cache clear, DB backup/restore, SQL changes — `maintenance.py` implemented, rebuild/restart wiring done, all three platforms. **Backup/restore and the UI landed 2026-08-23**: restore is plan-then-apply, with every refusal shown at once, the button armed only by an allowed plan, and the slot refusing again regardless of the button. Cache-clear is deliberately NOT implemented, with the evidence in the module docstring. Unticked because only Linux has been exercised: the backup/restore round trip WAS run against a live server on 2026-08-23 (four schemas, a 292.2 MB `acore_world` dump, the restored value read back, a wrong token refused) — this line said "none of it has been run against a live server on any platform yet" for several hours after that stopped being true, which a review caught
    - [ ] **macOS (Baerthe)** — back up a populated server, restore it, and confirm the wrong-token refusal still refuses. Worth watching specifically: the backup moves multi-hundred-MB files through a bind mount, which is where Docker Desktop for Mac is slowest and where a timeout that is fine on Linux may not be
  - [ ] Modules/mods: install/remove via the applier + rebuild/restart; manifest store GitHub refresh + bundled fallback
    - [ ] **macOS (Baerthe)** — apply a module, rebuild, restart, and confirm it is live; then remove it and confirm it is gone. The rebuild is the long pole and needs its wall-clock recorded
  - [ ] Networking auto-setup (README §13, full `WoW-Wotlk-NETWORKING.md` scope): firewall (ufw/firewalld/netsh + **macOS firewall designed/implemented**), WSL2 portproxy, LAN/public IP detection, realmlist updater + client writer, 0.0.0.0 binding check, CGNAT/DuckDNS/router-step prompts
    - [ ] **macOS (Baerthe)** — the macOS firewall path end to end (the application firewall is per-application, not per-port, which is a different shape from ufw/firewalld and may make the whole step a no-op worth saying out loud), LAN IP detection, the realmlist writer, and a second machine on the LAN actually connecting
  - [ ] Self-update check (README §10) — no platform-specific `config_dir()` issues
    - [ ] **macOS (Baerthe)** — confirm `config_dir()` lands somewhere sane on macOS (`~/Library/Application Support/...`) and that the update check does not stall behind a Gatekeeper prompt
  - [ ] Packaging: live-gated against the packaged `.AppImage`/`.dmg`/`.exe`, not just `python main.py` from source
    - [ ] **macOS (Baerthe)** — run the CI-built `.dmg`. This is the one artifact nobody has ever launched: Phase 5 proved it BUILDS and nothing more. Wanted back: whether Gatekeeper blocks it and what the user has to do about it, since an unsigned `.dmg` is a shipping decision and not just a test result
  - [ ] User-facing README topics (`pylauncher/README.md` + `archive/guides/wow-wotlk/README.md`) accurately reflect each platform's real state — no "works on macOS" claim before 6.2 is done. **Rewritten 2026-08-23.** The defect this item names did not exist: neither file contained the string "macOS" at all. The real one was the opposite — `pylauncher/README.md` was a single line saying the folder was "pending", with a link to `pyplan/README.md` that resolved to `pylauncher/pyplan/README.md` and 404ed. It is now a per-platform capability table with **three** values, not two: *run live*, *built* (code and tests, nobody has driven it) and *never run* — because "yes" was carrying both of the first two, which is the conflation this item exists to prevent. Review then found four claims in the rewrite that were themselves wrong, all corrected: that the app never downloads client files (WotLK is the one entry with `requires_client_dir` false, because the server fetches AzerothCore's own client-data archive — the same over-broad claim was in `installer.py`'s module docstring and is fixed there too); that the Catalog's Install *button* is proven, when the live install ran through the CLI harness; that account creation "works on all three platforms", when only the transport can and only Linux has been run; and "there is no Mac on this project", which erases Baerthe's ownership. Restore now says what it destroys, the artifacts say "opens" rather than "launched" (the evidence is a `YULON_SMOKE_TEST` headless run), and `DISCLAIMER.md` and the Releases page are linked. The archived shell-script guide gained a header saying what it is and is not, and its three dead links were fixed. **Unticked**: the item asks that all three platforms be reflected accurately, and the macOS column is still "never run"
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

  **All three are fixed and merged (2026-08-23).** Caching and resume landed with 1. 344 passed, exit 0.
  Three corrections worth keeping, because each invalidates what the brief assumed:
  - Defect 2's start step could not be fixed by hardcoding `C:\Program Files\Docker\Docker\Docker
    Desktop.exe` either, which is what the brief suggested. Measured on the VM: Docker Desktop 4.83.0 is a
    **per-user** install under `%LOCALAPPDATA%\Programs\DockerDesktop`, with nothing under Program Files, no
    `HKLM:\SOFTWARE\Docker Inc.` key, no `App Paths` entry in either hive and nothing on PATH — the Start
    menu shortcut was the only source that answered. So the probe asks Windows several ways and keeps the
    first candidate that resolves to a real file; hardcoded layouts are the fallback, not the answer.
    Reverting it costs 603 seconds in the test suite, because the old code polls out the full `wait_seconds`
    after a start that resolved nowhere — the wall clock *is* the defect.
  - The TLS failure is **not** Docker's CDN. Windows ships a small root set and fetches the rest on demand
    through CryptoAPI while schannel builds a chain; OpenSSL reads a *snapshot* of that store and never
    triggers the fetch. `desktop.docker.com` chains to Amazon Root CA 1 (absent), github.com to
    Sectigo/USERTrust (present) — which is exactly why three hosts verified and one did not. Fixed with two
    transports, System32 `curl.exe` by absolute path (schannel, so it sees the on-demand roots *and*
    enterprise MITM roots) and `certifi` as the in-process backstop. Verification is never weakened.

    **Two corrections to the in-process backstop, from an adversarial review against a real self-signed
    server (2026-08-23).** (a) `create_default_context(cafile=certifi.where())` **replaces** the OS store
    rather than widening it — it skips `load_default_certs()` whenever it is given a `cafile`. Measured
    here: 58 OS CA certs, 121 in certifi, and 33 of the 58 absent from certifi, including every
    administrator-installed root, i.e. exactly the enterprise-MITM case the curl transport was chosen for.
    `verify_context()` now loads the OS store and adds certifi on top (154 roots, both sets contained), and
    an unreadable certifi bundle degrades to the OS store instead of raising. (b) The "a bad certificate is
    not 'offline'" fix was **inert**: `urlopen` never lets an `ssl.SSLCertVerificationError` escape, it
    re-raises it inside `urllib.error.URLError`, so the predicate answered False for everything production
    could raise. Every test that exercised the flag built the exception by hand, which is why it passed.
    Both are fixed, with a test that runs the real `urllib` stack against a self-signed HTTPS server on
    127.0.0.1 rather than constructing the failure.
  - The stale-PATH fix must read **both** registry hives, not `HKLM`. Measured: Docker Desktop had installed
    to `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin` and written the **user** PATH; `HKLM` named no
    docker directory at all, and `C:\Program Files\Docker\Docker\resources\bin` did not exist. Registry
    before hardcoded paths, since the registry is what the installer actually wrote.

- **Both blindnesses outside `ensure_docker()` are now closed** (merged 2026-08-23, each implemented in
  an isolated worktree and then adversarially reviewed twice — both were rejected on the first review).
  1. **PATH — done.** `platform.docker_program()` resolves the CLI once and every argv is built from it:
     the nine sites in `docker.py`, `console.attach_argv()`, and `git.ContainerGit`/`apply.DockerSql`,
     which the original brief had missed. `installer.docker_available()` was deleted rather than fixed —
     it was `platform.docker_ready()` written a second time (style-guide §4). Cache a hit, never a miss:
     measured 7.5 ms resolved / 14.7 ms unresolved against 308 ms for one real `docker inspect`, and
     never caching the miss is what lets a launcher started on a bare box pick up the docker its own
     installer just wrote. The review then found the failure path was *dishonest* in two places — the
     Stop button answered "no Docker" by blaming the user's install for having no
     `COMPOSE_PROJECT_NAME`, and `wait_ready()` turned an instant hard failure into 480 s of silent
     polling. Both fixed; all four modules now log the real errno before degrading to the shared
     sentence, so an ACL or AV block is never reported as "install Docker Desktop".
  2. **TLS — done, after the first attempt turned out not to work.** All three `urlopen` calls now pass
     a verifying context, and an AST test fails on any *future* `urlopen` without one. Two defects the
     suite could not see, both found by a reviewer running a real self-signed server: (a) the
     "certificate, not offline" branch never fired, because `urlopen` wraps
     `SSLCertVerificationError` in `URLError` and the predicate only checked the outer type — the unit
     tests passed by raising a shape the real stack cannot produce; (b) `create_default_context(cafile=)`
     **replaces** the OS trust store rather than widening it, dropping 33 of 58 OS roots and silently
     breaking manifest refresh behind a corporate TLS proxy. The context is now a genuine union
     (OS roots + certifi, verified by DER SHA-256), and a bundle it cannot read degrades to the OS
     store instead of raising — a PyInstaller packaging fault must not present as "you are offline".

- **What the three Windows provisioning fixes actually close, and what they do not.** They are 6.3
  prerequisites landed early, not live-defect fixes: every `catalog.json` entry is
  `platforms: ["linux"]` and `Installer.preflight()` raises `UnsupportedPlatformError` before
  `ensure_docker()` — its only caller — is reached, so on Windows the provisioning chain is not
  reachable through the app at all. Live on Windows today, and therefore genuinely fixed now:
  attach-to-existing-install → Start, Stop, `docker logs -f`, and the `docker exec … mysql` behind a
  module apply and the realmlist UPDATE. **Not** the Console tab's `docker attach` — `send_command()`
  refuses on `pty_supported()` first, and 6.5 already scopes the console to Linux/macOS. Two successive
  commit messages claimed more than this and were corrected; the claim is easy to make and worth
  checking each time.

- **`yulon --provision` exists so the chain can be exercised on a clean box before 6.3 makes it
  reachable.** Headless, no Qt imported, one `YULON_PROVISION_JSON` line on stdout, and exit codes as a
  protocol for the harness: 0 ready, 3 reboot required (`wsl --install` forces one on a box with no
  WSL), 2 needs a human. Also a support diagnostic. `main.py` had no tests before it.

- **THE CLEAN-BOX RUN PASSED (2026-08-23 10:01).** `yulon.exe --provision` on a Windows 11 box that
  had never had Docker: `ok: true`, `docker_ready: true`, `skipped: none`, `manual_steps: none`,
  and independently `docker version` -> `client=29.7.2 server=29.7.2`. The chain it walked, in
  order: WSL2 installed under a UAC prompt -> `reboot_required` -> exit 3 -> reboot -> a
  **659,189,680-byte Docker Desktop download over the certifi-widened TLS path** -> silent install
  under a second UAC (`install --quiet --accept-license --backend=wsl-2`) -> Docker Desktop found
  and started -> daemon ready.
  **`docker_cli` came back as `C:\Program Files\Docker\Docker\resources\bin\docker.EXE`, not
  `docker`** — Cross-cutting defect 3 demonstrated rather than argued: the process that ran the
  installer really cannot see the PATH it wrote, and really does resolve the CLI another way.
  So all three Windows provisioning prerequisites 6.3 names are now proven on real hardware state,
  not by mechanism. 6.3 itself remains `[blocked]` on 6.2 — this proves the prerequisites, not the
  install path.

- **Two defects only the clean box could find, both in code the suite called green.**
  1. `--provision` crashed with `UnicodeEncodeError` one line after the 659 MB download.
     `json.dumps(..., ensure_ascii=False)` met a redirected Windows stdout, which is cp1252, and
     platform.py's own step text contains an arrow. The report line was unencodable exactly when it
     had something worth reporting. Fixed; the test encodes the line as cp1252, which is what raised.
  2. Nested virtualisation was off on the guest, so `wsl --install` succeeded and WSL2 still could
     not start. Now enabled by the harness on every run, because restoring a checkpoint restores VM
     *configuration* too and a by-hand fix is silently undone.
  Also learned the hard way: **do not start any process in the guest's interactive session while a
  UAC prompt is up.** Doing so switches away from the secure desktop and the prompt comes back as
  `Start-Process: The operation was canceled by the user`, which reads exactly like a product
  failure. One whole pass was lost to that.

- **How the clean-box run has to be driven (measured on the Win11 VM, 2026-08-23).** A plain `ssh`
  exec cannot do it, for two independent reasons. (1) An ssh session is **SessionId 0**, and Windows
  OpenSSH kills the whole descendant process tree when the ssh command returns — a fire-and-forget
  `Start-Process 'Docker Desktop.exe'` brings the daemon up in 17.3 s and then it dies with the ssh
  call. (2) `docker pull` from session 0 **always** fails with "A specified logon session does not
  exist", because the credential helper is DPAPI-bound to the interactive logon; redirecting
  `DOCKER_CONFIG` with `credsStore` removed does *not* work (fails in 0.1 s, measured). So the
  payload runs in interactive session 1 via `Register-ScheduledTask` +
  `New-ScheduledTaskPrincipal -LogonType Interactive` — **not** `schtasks /Create /TR`, which strips
  the quotes off a spaced exe path and leaves a task with Last Result -2147024894 that silently
  launches nothing. That session exists at boot only because the box has `AutoAdminLogon`; without
  it an interactive task stays queued forever, silently. A UAC prompt raised from that session
  appears on the console and a human clicks it — the run is automatic except that one click, and
  stubbing UAC out would make it prove less.

- **The clean box is a checkpoint, not a scarce one-shot.** `yulon-win11` has `clean-ssh` (fallback)
  and `clean-debloated` (the test baseline: ssh-ready, debloated, autologon on, **no Docker, no WSL,
  no real Python** — `python` on PATH is only the stock 0-byte Store alias stub, which is why the
  harness ships a PyInstaller bundle rather than running the repo). Restoring is cheap and
  repeatable, so the run can be repeated as often as the fixes need. Toolkit on the Hyper-V host at
  `C:\Users\PK\claude\debloat\`, with a verifier that refuses to let a half-applied debloat become
  the baseline.

- **Run the suite on a second OS and a second Python before believing it (2026-08-23).** CI pinned
  Python 3.11 on Linux and was green while the suite was red on every 3.12+ Linux box — `shutil.which`
  grew a `_winapi` call in 3.12, and tests that set `sys.platform = "win32"` change it for the whole
  stdlib, not just the module under test. Hardening that fake then exposed a second bug in the same
  tests: `_windows_docker_programs()` stats the real filesystem regardless of the injected `which`
  seam, so on a Windows box with Docker installed those tests were asserting about the host. CI now
  runs 3.11 and 3.13.

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
  - **The start side is now fixed too.** `docker.pin_project_name()` writes `COMPOSE_PROJECT_NAME` into
    the install's own `.env` at install **and** attach time, so the project identity stops moving with the
    folder. The value is *asked of compose* (`compose config --format json` → `name`) rather than
    recomputed, because compose's own normalisation is not obvious — measured: `WoW_Server 2` becomes
    `wow_server2`, `_leading` becomes `leading`, `Ünïcode` becomes `ncode` — and pinning a wrong value
    would *rename* the project and orphan the containers it exists to protect. An existing pin is never
    overwritten, since re-attaching a moved install must not repoint it at its new basename. Proven end
    to end: pinned as `wow-server`, folder renamed, project still resolves to `wow-server`, stop works and
    start works where it previously died with `Conflict. The container name is already in use`.

- **Windows: the launcher only works from the user's own desktop session (2026-08-22, measured
  three ways).** Docker Desktop's credential helper fails with `A specified logon session does not
  exist. It may already have been terminated.` from any non-interactive context — **even for an
  anonymous pull of a public image**. Established by a clean three-way comparison, so it is the
  *session* and not the login:

  | context | result |
  |---|---|
  | SSH (non-interactive), desktop logged out | fails |
  | desktop session 1 (interactive) | **6 passed, 1 skipped** in 83.75s |
  | SSH (non-interactive), desktop logged **in** | fails identically |

  Neither clearing `credsStore` from `~/.docker/config.json` nor pointing `DOCKER_CONFIG` at a
  credential-free directory avoids it — Docker Desktop reinjects the helper. **Good news for the
  product**: the GUI launcher runs in the user's session, so it is unaffected. **Bad news for
  automation**: a CI runner, a service, or any headless gate cannot pull images on Windows, so the
  Windows live gate must be driven from an interactive session (a scheduled task with `/IT`), not
  over SSH.

- **The full suite now runs on real Windows (2026-08-22).** Win11 Pro 25H2 with `core.autocrlf=true`
  at system *and* repo level — the environment the CRLF guard exists for, where it had never once
  executed because CI is Linux-only. Result: **221 passed, 6 skipped**, and the CRLF assertions ran
  rather than passing vacuously. The four extra skips versus Linux are honest and expected: 2 ×
  "no pty on this platform" (`test_console.py`) and 4 × "no bash that can run a script on this
  machine" (`test_installer.py`, `test_runner.py`) — the clean-Windows findings, holding. Live
  integration on Docker Desktop (Engine 29.7.2, Compose v5.4.0, WSL2, 15 CPUs, 9.7 GB): **6 passed,
  1 skipped in 83.75s**, against 58s on the Linux VM.

- **Two build-machine traps found while installing a real server on Windows (2026-08-22).**
  1. **Large clones need HTTP/1.1.** `git clone` of `azerothcore-wotlk` (224k objects) died with
     `fetch-pack: invalid index-pack output` / `unexpected disconnect while reading sideband packet`.
     `git -c http.version=HTTP/1.1 -c http.postBuffer=524288000` fixes it. The native install engine
     must set both, or its very first step fails on a large repo.
  2. **A build must not be attached to a console.** The first attempt ran in a scheduled task with a
     visible window; because the clone was failing silently the window looked blank, was closed, and
     the build died with `STATUS_CONTROL_C_EXIT` (`-1073741510`). Long jobs need `-WindowStyle Hidden`
     with a *separate*, disposable viewer — which is also how the launcher should treat its own log
     window.

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