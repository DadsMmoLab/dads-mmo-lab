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
- [x] 0.5 Write a minimal contributor/dev-setup doc (`pyplan/contribution.md`)
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
- [x] 3.2 `installer.py` — orchestration (shells out to existing scripts) — **live Linux run passed 2026-08-21 on a fresh Ubuntu 24.04 VM (see Cross-cutting → Phase 3 live gate)**
- [x] 3.3 Silent Docker/WSL provisioning stubs wired in (graceful failure until Phase 5)
- [x] 3.4 Networking auto-setup (LAN + internet play; firewall helpers, realmlist updater, router-step prompts) — README §13
- [x] **Phase 3 exit criteria met** (verified via CLI/test harness — no UI yet): the CLI harness, spelled `python -m yulon.catalog.installer wow-wotlk --server-dir ~/wow-server-playerbots` on the day and `python -m yulon.install_wiring wow-wotlk --server-dir ~/wow-server-playerbots` today — **do not copy the old spelling out of this line**: `yulon/catalog/installer.py` has carried no `__main__` since 7.2, so it exits 0 having installed nothing (checked 2026-09-02 at `f6ed1b9a`) — was run on a fresh Ubuntu 24.04 VM (12 vCPU, Docker provided by `ensure_docker()`): it answered every prompt, built AzerothCore + playerbots (~11 min compile), and ended with `install of wow-wotlk finished` and all three containers up (2026-08-21 00:27)

---

## Phase 4 — Controller UI (PySide6)

- [x] 4.1 `log_panel.py` — streaming output widget
- [x] 4.2 `catalog_view.py` — browsable catalog
- [x] 4.3 `controller_view.py` — per-install management (+ LAN/internet networking auto-setup control)
- [x] **Phase 4 exit criteria met** — human click-through against a live server on the Ubuntu 24.04 VM, 2026-08-21 (see Cross-cutting → Phase 4 click-through)

---

## Phase 5 — Windows/macOS provisioning + packaging

- [x] 5.1 Silent Docker Desktop / WSL2 provisioning + doc update — Linux path verified for real on a fresh Ubuntu 24.04 VM (2026-08-20); the Windows detection/short-circuit/plan paths verified on a real Windows 11 box (2026-08-21). The silent Docker Desktop **install** was then proven on a third, Docker-free box (`yulon-win11`, 2026-08-23) — with the caveats Cross-cutting records: the elevation succeeded partly because that session's token was already elevated on a box with non-default UAC, and first launch still needed two manual clicks. macOS has no machine on this side of the project (Baerthe runs the macOS gates — see Phase 6).
- [x] 5.2 PyInstaller specs finalized (local `pyinstaller build/pylauncher.spec` builds `build/dist/yulon/`; bundles manifests/, catalog.json and the install scripts; `YULON_SMOKE_TEST=1` runs the frozen exe headless)
- [x] 5.3 GitHub Actions release matrix complete — `ci.yml` + `release.yml` now live at the repo root `.github/workflows/` (2026-08-21), which is the only path GitHub reads; both run with `working-directory: pylauncher`. Neither upstream branch had a root `.github/`, so nothing was overwritten. The release job still only proves itself on a `v*` tag.
- [x] 5.4 Application self-update check (README §10)
- [x] **Phase 5 exit criteria met** (README §7: a push produces all three platform artifacts automatically) — proven 2026-08-21 by a throwaway `v*` tag on the fork: [run 32433417980](https://github.com/pjerra/dads-mmo-lab/actions/runs/32433417980), three runners green, artifacts `yulon-AppImage` (74 MB), `yulon-exe` (52 MB), `yulon-dmg` (42 MB), each also attached to the Release by `action-gh-release`; the tag and Release were deleted afterwards (they were a test), the run keeps its artifacts until 2026-11-19. The two artifacts we can run were then run — AppImage on Ubuntu 24.04, and the frozen `yulon.exe` out of the release zip on Windows 11 — both logging `window built, exiting 0`, which also makes 5.2 verified on real Windows from the shipped artifact rather than a local build. `ci.yml` is green on every push (run 32432706579). The `.dmg` is CI-built only — no Mac on this side of the project, so it is unverified beyond building (Phase 6.5 item 9 covers it).

---

## Phase 6 — Cross-platform install paths (macOS + native Windows) — **WotLK only**

> **Scope gate:** Phase 6 targets WoW WotLK exclusively (6.0's script rehome may touch all four
> games mechanically, but 6.1–6.5's gating/installer/feature work is WotLK-only). TBC, Vanilla,
> and Tortoise are Phase 7 — not started until Phase 6's exit criteria are fully met.

> **What the Windows run of 2026-08-23 does and does not settle.** 148 checks against a stock
> AzerothCore server on `yulon-win11`, built from published images — no compile anywhere, which is
> what made it possible in a day. It found two real defects (`pinned_project_name()` could not read
> a `.env` with a UTF-8 BOM, which is what PowerShell 5.1 and Notepad write, so the one recovery
> path for an install with unreadable compose files did not work on Windows; and the restore merge
> above). A review then found three things worth carrying: **gate 3 was run twice** and the report
> did not say so, the first attempt having failed; **gate 1b's 18 checks occupy a twelve-second
> window** in the activity log, which is thin for what it claims; and two gates were left on the
> author's word that a reviewer reproduced locally in seconds. The boxes are ticked because the
> evidence is there, and these are recorded so nobody re-reads the run as tidier than it was.

> **The macOS gates, and why there are only eight of them.** There is no Mac on this side of the
> project; **Baerthe** runs the macOS boxes below. They are not blockers on our work — implementation
> lands unticked — but they do block the parent box and the phase exit, and a box is ticked only when
> his run is written down here the way the Linux and Windows runs are: what machine, what version,
> what was observed, and what failed on the way.
>
> **One precondition runs through all of them, and it changed under this section**
> (corrected 2026-08-24). WotLK declares
> `platforms: ["linux", "macos"]` with `script_platforms: ["linux"]` and an `install.native`
> block (`catalog.json`, since `5c697798`), so **on a Mac the Install button is live**, and it
> dispatches to `NativeInstaller` — the 6.2 engine, which has never been run against a real
> Docker daemon on any platform. The other three entries are still `["linux"]`. (6.3 later widened
> the same entry to `["linux", "macos", "windows"]` — `script_platforms` still `["linux"]` — so a
> Windows click now dispatches to the same engine too; see the 6.3 line.)
>
> **So there is a decision to make before Baerthe starts, and it is not ours to make quietly.**
> Either (a) the macOS run presses Install and becomes the engine's first live gate anywhere —
> the most valuable run available, and also the one most likely to fail in ways that cost him an
> evening; or (b) he stays on "Use existing…" with a hand-built AzerothCore compose, which is
> what the eight boxes below were written for, and the engine waits for a gate we drive. Nothing
> in `phase6-decisions.md` records a choice, which is itself the finding: the catalog was changed
> to enable a path the gate list still assumes is disabled. **Open — Perzi's call.**
>
> Whichever way it goes, two of the boxes need no server at all: the `.dmg` and the test suite.
>
> **What is deliberately NOT asked for yet:** the macOS firewall's *apply* half — `networking.py`'s
> `alf` branch reads the Application Firewall's state and reports manual steps, but nothing
> mutates it, because every change needs root and this path never asks for a password — and
> 6.4's live install gate. Adding boxes for those would be asking him to
> test our intentions. The install path itself is no longer in that list — it is built; what it
> has never been is *run*.

- [x] 6.0 Rehome the install scripts — the eight executable files now live in `pylauncher/catalog/installers/<game>/` (parallel to `manifests/`), `catalog.json` paths are relative to that directory, `resources.installers_dir()` replaces `repo_root()`, `Installer(installers_root=…)` resolves them, and the spec ships the whole tree instead of globbing `archive/guides/**` — so the bundle no longer carries `archive/guides` at all (README §3a bonus). The Tortoise script was renamed to lowercase on the way (`install-tortoise-wow-wsl.sh`, style-guide §6a). Verified: 191 passed, and a frozen PyInstaller build contains all eight scripts under `catalog/installers/` and passes `YULON_SMOKE_TEST`. The DoD's third verb, *run*, is not re-evidenced post-move — but `git show --stat fcd95c5` shows all eight scripts as pure renames (0 changed lines) and `installer.py` already passed `cwd=self.script.parent` before the move, so what runs is byte-identical to what Phase 3 live-gated. `archive/guides/` keeps the human-facing guides plus the four non-catalog installers (Maplestory, Mu Online, RuneScape, the Unbound addon), which no catalog entry references.
- [x] 6.1 Honest platform gating — `install.platforms` is data in `catalog.json` (all four entries were `["linux"]` when this landed; WotLK became `["linux", "macos"]` with 6.2 and `["linux", "macos", "windows"]` with 6.3 — see the macOS preamble above and the 6.3 line below), `Installer.preflight()` raises `UnsupportedPlatformError` with a user-readable message BEFORE any subprocess, the catalog tile disables Install with the reason on the tile ("Use existing…" stays enabled — managing a server works everywhere), `start_install()` refuses before the folder prompts, and a failed script's dialog now carries the script's own last 12 output lines ("It last said: …") instead of a bare exit status. Mocked through the `platform_id` seam per roadmap 6.4; 196 tests green.
- [ ] Rewrite the installer scripts off `pacman`/`systemctl`/`sudo` — the orphaned "update scripts and manifests to use proper systems and features" step, re-homed as a checkbox: it is subsumed by 6.2/6.3's native engine, and closes when WotLK installs without a shell script on macOS and Windows.
- [x] 6.1.5 Interactive input handling — the installer runs on a **pseudo-terminal** and answers `sudo`'s password prompt through a dialog, instead of dying seconds in on `sudo -v`. Two things were needed and the first attempt had neither. **Transport:** `sudo` reads from `/dev/tty`, not stdin, precisely so a piped stdin cannot feed it a password — so `interact(terminal=True)` opens a pty and the child claims it as its *controlling* terminal (via `sh` after exec, not `preexec_fn`: that runs Python bytecode after fork in a process with live Qt threads). **Recognition:** `SUDO_PROMPT` makes sudo announce itself with a per-install random marker, matched exactly — the first version guessed from the shape of a line (`: ? > ]` after a pause), which measurably fires on `[ 43%]`, `Get:12 … [345 kB]`, `note:` and every gcc diagnostic, and opened an application-modal dialog over a two-hour compile. Measured on the Ubuntu VM with sudo temporarily made to demand a password: **pipes → seam asked 0 times, `"sudo: a terminal is required to read the password"`; pty + marker → asked with the exact marker, every attempt read and evaluated by sudo, nothing typed echoed into the log.** Also: `ask()` receives only the prompt (it used to get the whole pending buffer, so `is_secret()` read a neighbouring "directory" and unmasked the password field), ECHO is off on the pty, and `DEBIAN_FRONTEND`/`NEEDRESTART_MODE` are set because a terminal re-arms every apt/dpkg dialog that gates on `isatty()`. Not yet exercised: the macOS/Windows variants' own prompts — those scripts do not run on this platform yet (6.2/6.3).
- [ ] **Privilege transparency (binding across every install path)** — no silent host privilege escalation, carried from the first-generation `install-*.sh` finding into the native engine: never write a `sudoers.d`/`NOPASSWD` docker rule (redundant beside the group, pure attack surface), never `chmod 666` the docker socket, and never `usermod -aG docker` without an explicit opt-in that states the group is root-equivalent (the `-v /:/mnt` container-mount example). Applies to `ensure_docker()` provisioning, the Linux bash scripts (bugfix-only), and the 6.2/6.3 native engine; tested structurally via the argv-parse seam and surfaced in the 6.5 install gate.
- [x] 6.2 macOS install path — the shared **native install engine** (`NativeInstaller`, per `phase6-decisions.md`): `install.platforms`/`install.script_platforms` dispatch, compose three-file generation + `.env` merge, preflight (refuse-don't-warn floors, bind-mount probe, `server_dir_problem()`, port-conflict before build), staged/resumable install, `keep_awake()`, readiness poll — all against Docker Desktop, no `pacman`/`systemctl`/`sudo`, no manual VM management (macOS has no Rust prior art; written fresh). **Progress 2026-08-24 (Baerthe's Mac, code-side only — still unticked because the live install gate has not run, no Docker on this side):** the one hard code gap on this line is now closed — `docker_desktop_data_root()` on macOS stopped returning `None` and now resolves the settings store's `diskPath`/`DataFolder` first, falling back to the documented `~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw` (`platform.py`, `_MACOS_DOCKER_RAW`). Preflight's macOS "Docker's disk" check is consequently **one-sided**: below the refuse floor is a refusal (the VM certainly has no more than the host), below the warn floor a warning, and *ample* host space is `unchecked`, never a pass — host free space is an upper bound on the sparse VM's room, and a false pass here is the doomed-build the tri-state discipline exists to prevent. Two Phase B pins landed beside it: `test_native.py` now asserts `Seams.ensure_docker` defaults to the real provisioning function (the one seam that can escalate), and `test_provision.py::test_macos_provisioning_never_escalates_privileges` drives `ensure_docker()` on `darwin` non-dry and asserts no `sudo` prefix, no `usermod`/`gpasswd`/`adduser` group join, no `sudoers`/`NOPASSWD`, no `docker.sock` `chmod` on the emitted argv (roadmap 6.4.3). The `caffeinate` argv/cleanup was already pinned at `test_platform.py:212/237`; verified, not re-added. **Live install gate closed 2026-08-29** — see 6.5's Install line for the full cold-start-to-running-server run on real Docker Desktop.
- [ ] 6.3 Native Windows install path — same native engine against Docker Desktop's **WSL2 backend** (no bespoke WSL2/VM manager). **Progress 2026-08-24 (code-side; still unticked — the clean-box live gate has not re-run):** the engine half of 6.3 is now built and the route is open. `catalog.json` widened WotLK to `platforms: ["linux","macos","windows"]` (still `script_platforms: ["linux"]`), so `installer_for()` dispatches a Windows click to `NativeInstaller`, and `test_native.py` now asserts `installer_for(ENTRY, platform_id=lambda: "windows")` is the native engine while TBC (still `["linux"]`) takes the 6.1 refusal. The one genuinely-new Windows code item — `rust-prior-art.md` §4's "spawn with `CREATE_NO_WINDOW` or consoles flash over the UI" — landed as `runner.creationflags()`, applied at every spawn site: `runner`'s `stream()`/`run()`/`interact()` (non-pty branch), `platform._spawn_detached()`, and the three sites that bypass `runner` — `apply.py`'s `subprocess.run` (the SQL runner), `maintenance.py`'s `subprocess.run` (`docker exec`), and `console.py`'s `subprocess.Popen` (`docker attach`; POSIX-only by `pty_supported()`, carried anyway so the one place that *can* add a Windows console stays consistent). The helper is public for the same reason `git.CONTAINER_GIT_IMAGE` is: a flag applied to some spawn sites but not others is a window that flashes anyway. The rest of the 6.3 hardening list was already landed by earlier work and is now *verified* rather than added: the three Windows provisioning defects (TLS cert, `Start-Process` path, PATH re-read) are fixed and clean-box-proven (Cross-cutting); `docker.exe` discovery (`platform.docker_programs()`), `git` discovery + `core.autocrlf`/`HTTP/1.1` (`git.py`, containerized so no host git is needed), path canonicalization (`composegen.install_id`/`project_name` lowercase-normalize on Windows), and the nested-virtualization gate are all in place. **Still owed, and the reason the box stays unticked:** 6.3's Definition of done is the native engine completing a WotLK server on a *clean* Windows 11 box (no distro, no pre-existing Docker/WSL), driven from the `clean-debloated` checkpoint — which requires that machine and has not been run.
  - [ ] **Windows 11 clean-box gate, partial (2026-08-25)** — driven from the shipped `Yulon-v0.6.51Public-windows-x64.zip` on a box restored from `clean-debloated`, no Docker and no WSL distro. Confirmed: `--provision` installs WSL2 and asks for the reboot (exit 3); after the reboot it downloads and silently installs Docker Desktop; the engine reaches 29.7.2; the GUI launches and the catalog renders; TBC, Vanilla and Tortoise all correctly show "Installer needs Linux — not available on this platform yet" while WotLK is enabled; the native engine reaches `build_staged()` with its own three compose files (`docker-compose.yml` + override + `docker-compose.build.yml`), which the shell installer never produces. Two notes for whoever repeats it: **restoring a Hyper-V checkpoint restores the VM CONFIG too**, which silently reverted `ExposeVirtualizationExtensions` and left Docker's engine at HTTP 500 forever with no useful error (`clean-nested-virt` is the checkpoint that has it on); and the bind-mount probe times out at 30 s against a freshly installed Docker Desktop that is still warming up — non-fatal, the install continues, but a first-run user will likely always see that warning.
  - [x] **BLOCKER (CLOSED 2026-08-26, see the gate below): the native Windows install cannot finish — `ac-db-import` exits 1 on a bind-mount it cannot write (clean-box gate, 2026-08-25).** Same symptom as the SELinux finding, entirely different cause, and the image's own advice is wrong for both. The chain, each step measured on `yulon-win11` restored from `clean-nested-virt`: the native engine's generated `docker-compose.yml` bind-mounts `./env/dist/etc` and `./env/dist/logs` from a WINDOWS path — deliberately, per its own comment *"env/dist/etc must be bound out to the host: it is where the module configs live"*; Docker Desktop mounts Windows drives into WSL2 over **9p/drvfs with `uid=0;gid=0`** and mode `drwxr-xr-x` (confirmed in `/proc/mounts`); the image's default user is **`acore`** (uid 1000), not root; so uid 1000 cannot write a root-owned 755 directory and the import dies with `cp: cannot create regular file '/azerothcore/env/dist/etc/authserver.conf.dist': Permission denied`. Probed directly: the mount is writable as root and NOT writable as `--user 1000:1000`. Windows-side ACLs are fine (`pk` has FullControl) — the restriction is entirely the 9p uid mapping. **`DOCKER_USER=root`, which the image's error text suggests, does not help**: the generated compose sets no `user:` key, so the image's `acore` wins. Three ways out, all design calls for the native engine: `user: "0:0"` on the services that write there (simplest; writes still land as the user on the Windows side through 9p); a named volume for `env/dist/etc` (breaks the stated intent, since module configs must be reachable from the host); or installing under `\\wsl$` rather than a Windows path (most correct, biggest change, and the picker would have to steer users there). Everything BEFORE this step works on a clean box — WSL2 install, reboot, Docker Desktop install, engine 29.7.2, GUI, platform gating, clone, and the whole `build_staged()` image build.
  - [ ] **Second clean-box attempt, from `clean-nested-virt` (2026-08-25 evening) — the fix for the blocker above is still UNVERIFIED, and two new findings came out of trying.** Driven from a Windows build of `fix/windows-bind-mount-user` (the `user: "0:0"` fix), on a box with nested virtualisation on, no Docker, and no WSL. The GUI half all worked: the launcher started, the catalog rendered, Install opened the native folder picker — and unlike Linux, that picker has a **New folder** button, so the Linux dead-end has no Windows equivalent. The install then stopped at provisioning:
    1. **The engine cannot install Docker for itself:** `DockerUnavailableError: Docker isn't available and could not be set up automatically. Open an Administrator PowerShell and run: wsl --install --no-distribution, then reboot.` Both optional features really were `Disabled`, so the diagnosis was right.
    2. **The remediation it prints is circular on the machine that needs it.** In an elevated PowerShell, `wsl --install --no-distribution` exits 1 printing *"The Windows Subsystem for Linux is not installed. You can install by running 'wsl.exe --install'."* — and plain `wsl --install` prints the same and exits 1. Enabling `Microsoft-Windows-Subsystem-Linux` and `VirtualMachinePlatform` via DISM and rebooting does not change it: the inbox `wsl.exe` is a stub, and modern WSL is a separate package. After installing WSL 2.7.12 from Microsoft's MSI by hand, **the exact command the product prints then succeeds (exit 0)**. So the text is not wrong, it is only actionable on a box that already has what it is telling you to get. Worth pointing at the MSI, or detecting the stub, since the stub is what a clean Windows 11 has.
    *Evidence note: the two quoted strings are in the source (`platform.py`'s remediation text and
    `native.py`'s `DockerUnavailableError`), but everything below about how the box BEHAVED is
    field observation with no log committed anywhere in this repo. Treat it as a lead for the next
    Windows session rather than as a measurement of the kind the Linux findings above carry.*
    Beyond that the run did not reach the thing it was for. Docker Desktop 4.x installed silently (exit 0) but its GUI process exits within seconds of launch and never creates its `docker-desktop` WSL distro, so no engine and no `ac-db-import`. `com.docker.service` was found `Stopped` and starting it did not change the outcome; nested virtualisation is confirmed on (`ExposeVirtualizationExtensions: True`, 15 vCPU, 20 GB static). Since the earlier `clean-debloated` gate did reach engine 29.7.2, this looks like VM-state rather than product, and is where the next Windows session should start. **`user: "0:0"` therefore still has no live evidence** — only the unit tests and the measured cause.

  - [x] **Native Windows 11, clean box, full install — the 6.3 blocker is CLOSED (2026-08-26).**
    A WotLK server built and running on Windows through the native engine, from the GUI, on a box
    restored from `clean-nested-virt` with no Docker and no WSL distro. **`ac-db-import` Exited
    (0)**; `ac-database` healthy, `ac-authserver` and `ac-worldserver` up, 3724 and 8085 listening,
    schemas at **22 / 111 / 315 / 30** — byte-identical to the Ubuntu and Fedora gates, which is
    what makes the import credible rather than merely finished. The worldserver is running
    playerbots for real: 1233 idle, 583 quests accepted, 142 rewarded.
    **The probe that proves the fix**, run inside the live worldserver against the bind mount that
    used to fail:

        uid=0(root) gid=0(root) groups=0(root)
        drwxr-xr-x 1 root root 4096 /azerothcore/env/dist/etc
        WRITABLE

    That is `user: "0:0"` (#99) in effect — the same probe returned `NOT-WRITABLE` as `acore`
    (uid 1000) when the blocker was found, and the image tag is `native-6d51a632`, so the native
    engine built it rather than a script.
    **Three things the run needed that no code change can supply, and which belong in the docs
    before testers hit them.** (1) **WSL and Docker each need a reboot to activate**, and the
    install had been failing partly because that reboot had not happened between them. (2) The
    inbox `wsl.exe` on a clean Windows 11 is a STUB: `wsl --install --no-distribution` exits 1
    printing "The Windows Subsystem for Linux is not installed", DISM plus a reboot does not change
    it, and only Microsoft's MSI (`microsoft/WSL` releases, 2.7.12) makes that same command
    succeed — so the remediation `platform.py` prints is circular on exactly the machine that needs
    it. (3) **Docker Desktop's GUI never opened at all** on this box, but `docker desktop start`
    (its own CLI plugin) brought the engine up and created the `docker-desktop` distro; over SSH it
    then has to run as an interactive scheduled task, because Windows OpenSSH kills the process
    tree when the command returns.
    **The app's own preflight earned its place**: it refused nothing but warned that 15 CPUs would
    start 16 compilers at ~2 GB each against 9.7 GB, which would have OOM-ed hours in. Taking its
    advice via `.wslconfig` (4 processors, 14 GB) turned that into `[pass] CPU vs memory: 4 CPUs
    against 6 affordable`, and the build then completed.
- [x] 6.4 Tests & gates (mocked platform-gating + script-resolution tests; live-gate on real macOS and Windows 11 — WotLK only). **The mocked, no-hardware half is done (2026-08-24).** Platform gating is tested end to end: `test_installer.py::test_installer_refuses_a_platform_its_script_cannot_run` (TBC on `macos`, nothing subprocesses, `UnsupportedPlatformError`, `issubclass` of `InstallerError`) and `test_native.py`'s dispatch table (`installer_for(ENTRY, platform_id=…)` → `Installer` on linux, `NativeInstaller` on both macOS and Windows; TBC still Linux-only). The 6.4.3 privilege-transparency rule is now asserted on the emitted argv for **all three** provisioning paths, not one: Linux (`test_linux_never_joins_the_docker_group_without_consent`, parametrized over five package managers), macOS (`test_macos_provisioning_never_escalates_privileges`, non-dry), and — newly added this pass — Windows (`test_windows_provisioning_never_escalates_privileges`, non-dry: asserts no `sudo`, no `usermod`/`gpasswd`/`adduser` group join, no `sudoers`/`NOPASSWD`, no `docker.sock` `chmod`, while either recording the `-Verb RunAs` UAC prompt as present so the run actually traversed the elevated install). **The live macOS gate closed 2026-08-29** — see the sub-line below and 6.5's Install line for the full run. The live Windows gate is unchanged (hardware-blocked per 6.3).
  - [x] **macOS (real Docker Desktop, 2026-08-29)** — the suite green on macOS: `pytest` (1039 passed, 8 skipped, after fixing one test that leaked real filesystem state — see below), `mypy` (0 issues, 37 files), `ruff check`, `black --check`. Run on an Apple M4 Pro with Docker Desktop 4.87.0 / engine 29.7.2 **actually installed and running** — the exact machine the macOS gate box was written for. One pre-existing test bug found and fixed on contact: `test_macos_plan_downloads_dmg_and_copies_the_app` assumed `/Applications/Docker.app` never exists and asserted the fresh-install dry-run plan; on a box that genuinely has Docker Desktop it took the "already installed" branch instead and failed. Now pins `Path.exists` false like its sibling privilege-escalation test does, so the assertion holds on any box, Docker-equipped or not.
  - [ ] **First Darwin interpreter run — partial, on a corporate machine (Baerthe, 2026-08-24).** The cheap no-Docker half of this box was run on a Mac that is a VPN/VPS corporate setup with **no `127.0.0.1` on `lo0`** (`ifconfig` shows `10.10.10.1/32`, `/etc/hosts` names `127.0.0.1` but `ping` gets 100% loss) and **no Docker** (`docker` not found, no `/Applications/Docker.app`). Result: `pytest` **784 passed, 2 skipped, 4 errors** (the run predates this session's two new privilege-transparency tests and the `console.py` creationflags fix, which brought the suite to **786 passed**); `mypy`, `ruff`, `black --check` all green. The 4 errors are all in `test_download.py` (the self-signed-TLS fixtures bind `127.0.0.1`, `OSError: Errno 49 Can't assign requested address`) plus `tests/integration/conftest.py` crashing at collection for the same reason — environmental, not code defects, and not to be generalized from: they are symptoms of this machine's loopback config. `platform.detect()` → `macos`, `config_dir()` → `~/Library/Application Support/yulon`, and `runner.pty_supported()` → `True` (the GM console is a macOS feature, confirmed on a Darwin interpreter). **Unticked**: the box asks for green *on macOS*, and neither the suite here (4 env-dependent errors) nor this machine (no Docker) can close it — the full `pytest` + live half is re-run on a Docker-equipped home Mac.
- [ ] 6.5 Full WotLK feature coverage on Linux, macOS, and native Windows (the Phase 6 exit gate):
  - [ ] Install (zero shell interaction, all three platforms) — incl. staged/resumable install, preflight floors refusing-not-warning, `keep_awake()`, honest cancel copy
    - [x] **macOS, real Docker Desktop, cold start to running server (2026-08-29)** — the engine's first live gate anywhere on Apple silicon, driven through the CLI harness — `python -m yulon.catalog.installer wow-wotlk` as it was spelled that day, `python -m yulon.install_wiring wow-wotlk` today, the old module having had no `__main__` since 7.2. The harness needed its own fix first — it never wired the `import_probe`/`reset_unfinished` seams `main.py`'s GUI factory wires, so a native install of any `import_service` entry — WoW WotLK on every platform — refused instantly at preflight with "this installer was built without a way to check it"; fixed to mirror `main.py::make_installer`. Machine: Apple M4 Pro, 12 CPU, 25.7 GB RAM, Docker Desktop 4.87.0 (engine 29.7.2). Preflight warned (not refused) on the CPU/memory floor — Docker's VM had 7.7 GB against 12 host CPUs, "13 parallel compilers, ~3 affordable" — and the build still completed. **Full result: `ac-db-import` Exited (0)**, all three containers up, 3724/8085 listening, schemas at **22 / 111 / 315 / 30** tables — byte-identical to the Ubuntu, Fedora and Windows gates. Compile (AzerothCore + playerbots, 1829 objects) took roughly 15 minutes wall-clock. **One real bug found and fixed on the way**: the containerized-git clone hit the exact race `git.py`'s own docstring had left as "still open" — `Cloning into '.'...` then `/git/.git: No such file or directory` against a directory `mkdir`'d immediately before the bind mount. It self-healed via the existing host-git fallback (Xcode CLT was present), but a dozen manual repeats of the identical command all succeeded, confirming a mount-propagation race rather than a real failure — so `ContainerGit.clone()` now retries the initial clone once on that exact signature (`_is_fresh_mount_race()`) before falling back to host git, which matters for a Mac *without* Xcode's Command Line Tools, the whole reason `ContainerGit` exists.
    - [ ] **Linux, through the Install BUTTON (2026-08-24)** — the gap the README review named is closed on the driving half and opened on another. `CatalogView.button_for("wow-wotlk").click()` offscreen, real `LogPanel`, real `Installer` from the factory, real sudo dialog (a watchdog `QTimer` found `activeModalWidget()` and typed into it, so the seam was driven the way a person drives it) — only `pick_dir` stubbed, since a `QFileDialog` cannot run headless and it is already a constructor seam. **33 m 23 s; the C++ compile SUCCEEDED (~30 min, 1828 objects, all four images built).** Every prompt was answered by the rule that should have answered it, and the anchored `^\s*Press ENTER` rule correctly did NOT eat the "Leave blank and press ENTER" hint, so the blank line reached `Install path:` as designed. **But `ok=False`**: `ac-db-import` died 0.3 s after Docker called the database healthy — see the first-run race below. The app's own `repair_import()` then finished the job in 212.1 s, and the resulting server started, reached ready, took an account (and refused the duplicate), and stopped in 49.8 s with `acore_auth` 22 / `acore_characters` 111 / `acore_world` 315 / `acore_playerbots` 30 tables. **Unticked because the two halves are each proven and the single uninterrupted run is not**, and because the script's post-install prompts (`Press ENTER when done creating accounts`, the stop-the-server question, the wow-manage download, the Steam/Gaming-mode launcher) were never reached, so those `PROMPT_RULES` entries are still untested against a live script. `ASK_THE_USER`'s docker-group question was skipped too — `pk` was already in the group
    - [x] **Linux, end to end through the packaged AppImage (2026-08-25)** — a WotLK server running on clean Fedora 44 from a click, on a box restored from a cold `clean-desktop` checkpoint (no Docker, untouched home), driving the AppImage's own GUI rather than `python main.py`. It took three fixes, each only findable once the previous was gone (PR #97). **One: the first install through the GUI could never succeed, on any Linux.** The picker only accepts a directory that ALREADY EXISTS (a typed non-existent name is refused, Choose greyed out) while its title tells the user to make a new one; the scripts treat any existing directory as an install to protect and ask "Remove it and start fresh?"; `PROMPT_RULES` answers "n" because `InstallOptions.reinstall` is never set by the GUI; `exit 0`, nothing installed. Measured: 35 s, empty directory, zero images, and the script's own log at `~/dads-mmo-lab-install-*.log` ending "Keeping existing install - exiting." The scripts now ask `dir_is_reusable` — `find -maxdepth 0 -empty` and `-w`, NOT `ls -A`, which prints nothing for a directory it cannot read exactly as it does for an empty one and would have cloned over a real install. **Two: the GUI-side folder rule was dead code on Linux.** `Installer.preflight()` never calls `preflight.gather()` — that belongs to the native engine — so `server_dir_problem()` was live for macOS and Windows and dead for the one platform whose scripts carry the rule. Unit tests, mutation testing, three review lenses and a Codex pass all read that diff and missed it; only running it found it. **Three: `sudo rm -rf /home` was reachable** — /home is one "up" click from where the picker opens, was in neither banned list, and `--reinstall` answers "remove it" with "y".
    - [x] **SELinux distros could not finish an install at all (2026-08-25)** — with the dead-end fixed the install reached `ac-db-import`, which exited 1 with "cp: cannot create regular file .../authserver.conf.dist: Permission denied" on files owned by the user. The image's own advice blames cloning as root and is wrong here. AzerothCore's compose bind-mounts `env/dist` WITHOUT `:z`, so the host directory keeps `user_home_t` and the container (`container_t`) is refused. Relabelling to `container_file_t` and re-running the identical import gave exit 0 and the whole stack came up. Affects Fedora, RHEL, Rocky, Alma, CentOS Stream and the Silverblue/Bazzite family — one of the two distros the original report named. The scripts now relabel before `compose up`; no sudo, since a user may relabel files they own, and a no-op wherever `getenforce` does not exist.
    - [x] **Arch, the DEFAULT script variant, end to end (2026-08-25)** — a working WotLK server on clean Arch + Xfce restored from its cold `clean-desktop` checkpoint: compile finished, `ac-worldserver`/`ac-authserver` up with `ac-database` healthy, playerbots logging in (705/1778 at the time of writing) and 3724/8085 listening. This is the variant no gate had ever run: `catalog.json`'s `script_variants` maps only `apt` and `dnf`, so a pacman host falls through to `install-wow-wotlk.sh`. It also exercises the FUSE-less path — Arch has no `fusermount`, so the `.tar.gz` from #96 is the only artifact that runs there, and it did. Two compose files are produced here (`docker-compose.yml` + the generated override), against three on Windows, which is a reminder that the native engine and the shell installer share very little.
    - [x] **Ubuntu 24.04, cold box, single uninterrupted run (2026-08-26)** — the run 6.5 had been
      missing: not two proven halves, one install from nothing to a server that answers.
      `yulon-ubuntu` restored to its `clean-ssh` checkpoint (no Docker, no images, untouched home),
      after checkpointing the existing box first so the previous state was recoverable rather than
      destroyed. Driven through the real `Installer` with the real `PROMPT_RULES`, answering the two
      things the app routes to a person - the sudo password as a secret, and the docker-group
      question - exactly as the GUI does. **4010.6 s (67 min), exit 0**, 5385 log lines.
      Result: `ac-database` healthy, `ac-db-import` **Exited (0)**, `ac-authserver` and
      `ac-worldserver` up, 3724 and 8085 listening, and the four schemas at
      **22 / 111 / 315 / 30** tables (auth / characters / world / playerbots) - the same counts the
      earlier Fedora gate produced, which is the cross-check that the import really completed.
      Worldserver mean diff 50 ms.
      **Three things this gate established that no earlier one had.** (1) The app's provisioning
      really does install Docker on a cold Ubuntu: apt repository, engine, service enable, all
      unattended. (2) The privilege rule works as written rather than as intended - with nobody to
      ask, it REFUSED the docker group, installed Docker anyway, and said what to run by hand; the
      consent text it produces states that the group is root-equivalent, gives the `usermod`
      alternative, and promises no passwordless-sudo rule and no socket `chmod`. Granting it then
      required a re-login before the install could proceed, exactly as the dialog said it would.
      (3) the CLI harness of the day - `installer._main()`, which 7.2 replaced with
      `yulon.install_wiring.main()` - passed no `ask`, so it DECLINED every `ASK_THE_USER` rule:
      correct for a harness, but it meant the CLI could only ever exercise the refusal path. A gate
      that drives the product has to supply that seam or it is testing half the code. **Since
      fixed**: `install_wiring.main()` calls `engine.run(options, ask=_terminal_prompter)`, and the
      comment above that argument records why the keyword is not optional (a run given no prompter
      answers neither the docker-group consent nor the sudo password). Verified 2026-09-02 at
      `f6ed1b9a`.
  - [ ] Server lifecycle: start/stop/status/health polling + README §12 port-conflict guard
    - [x] **Windows (2026-08-23)** — 23/23 against a stock server on `yulon-win11` (Windows 11 Pro 26200, Docker Desktop 29.7.2, WSL2, Linux containers). `Controller.start()` 3.2 s; `wait_db_healthy` 0.1 s; `wait_ready(127.0.0.1, 8085)` 27.7 s; **stop 8.8 s**, containers kept; `ac-db-import` stayed `Exited (0)` throughout, so `start_staged` never selected it. README §12 guard: a foreign container published on 3724 produced `PortConflictError` naming `yulon-port-hog` and nothing started, while `port_conflicts()` excused our own three. A further 18 checks covered the fallbacks nobody had run on Windows: with the compose file hidden and no pin, stop and remove both REFUSE; pinned, the by-name `docker stop -t 300` path stopped all three in 7.4 s
    - [x] **macOS, real Docker Desktop (2026-08-29)** — driven directly through `docker.status()`/`stop_staged()`/`start_staged()` against the live install below (Apple M4 Pro, Docker Desktop 4.87.0, engine 29.7.2). `stop_staged()` **13.8 s** (containers kept, not removed), `start_staged()` **5.8 s**, `status()` correct empty/populated before and after each. Docker Desktop for Mac's VM does **not** reproduce Linux's 300 s populated-worldserver drain — this box stopped a live playerbots worldserver in under 14 s, closer to Windows's 8.8 s than to the Linux measurement `STOP_GRACE_SECONDS` was set from. `port_conflicts_for()` correctly named `ac-worldserver`/`ac-authserver` as already holding 8085/3724 (our own containers) before the stop.
  - [ ] Server lifecycle follow-ups (from the staged start/stop review, Cross-cutting): ~~deliberate "Stop and remove containers" action (nothing can remove a container today)~~, ~~rename `docker_ctl.py`'s `stop` export away from a `stop_staged` peer~~, deliberate "repair / re-import" action for an install interrupted before import, and measure `stop_grace_period` on a populated server before picking a value. **The first two landed 2026-08-23** (`30f0b7ff`): `docker.stop()` — which nothing called and which checked nothing — became `remove_staged()`, asking *by project label* rather than by container name (AzerothCore pins names globally, so a name search finds the neighbouring install), refusing on the same ownership census `stop_staged` uses, and verifying what is actually gone instead of trusting `compose down`'s exit code. `docker_ctl.stop` is now `docker_ctl.remove`. A stale warning was removed from two docstrings on the way: both claimed removing containers "forces the next start back onto `compose up -d` and re-running the one-shot database import", which stopped being true at `639fdb8d` when `start_staged` began naming its three services with `--no-deps` — the warning outlived its danger and made a safe action look destructive, which is plausibly why nothing had been built to remove a container. UI: a two-press button on the Server tab, disarmed by Start, Stop or Refresh, whose armed text says the characters are kept. **Live-gated on yulon-ubuntu the same day**, on the playerbots install with 650 accounts: a marked row set, every container removed (`ac-worldserver`, `ac-authserver`, `ac-database`, `ac-db-import`), **both volumes still present** (`wow-server-playerbots_ac-database`, `_ac-client-data`), the stack started again from nothing, **`ac-db-import` never recreated**, and the marked row read back byte-identical with all 650 accounts intact — 14/14 checks. Four mutations proven to die: adding `-v`, asking by name instead of by label, dropping the stranger refusal, and reporting success without checking what is left. Order matters here: restore was proven live *before* teardown was, so a real 386 MB backup of that database existed on disk while the volume claim was being tested. Incidental measurement for the fourth item: a plain `docker compose stop` (10 s default) SIGKILLed the populated worldserver — exit 137 — so 10 s is demonstrably too short. **The third landed 2026-08-23** and the fourth is now measured rather than guessed. `STOP_GRACE_SECONDS = 300` applies to both stop paths (`compose stop -t` and the by-name `docker stop -t` fallback) and to the teardown's `compose down`. Measured on yulon-ubuntu against the playerbots install at **1980 characters online** (1845 bots, waited for the count to plateau): worldserver shutdown **90.7 s**, **73.4 s** and **58.3 s** across three runs, all exit 0 under a grace long enough not to bind; ac-authserver 0.22 s and ac-database 1.4 s are nowhere near the constraint. Almost all of it is one phase — `Closing down DatabasePool 'acore_characters'. Waiting for 7662 queries to finish...` — draining 7400-7700 queued character saves at 90-145 a second. The third run was `stop_staged()` itself with the constant in force, end to end, containers kept. 300 s is ~3.3x the worst sample; the margin is deliberately asymmetric, because an over-long grace only costs time on an already-hung server while a short one costs a player's characters. It agrees with the `stop_grace_period: 5m` the earlier Rust launcher wrote (`rust-prior-art.md` §2) — now a confirmed number rather than an inherited one. **Not the compose key** when this was written; the engine has since been built and its generated base file carries `stop_grace_period: 5m` (`catalog/installers/wow-wotlk/native/base.yml.tmpl`), so both halves now exist. **Unmeasured**: slower storage, a realm materially larger than ~2000 characters, a shutdown under heavy write load, and whether a genuinely hung worldserver sits out the full 300 s (no hang was induced). The remaining item on this line is the repair/re-import action, which landed the same day — see below
    - [x] **Windows (2026-08-23)** — teardown 18/18: `Controller.remove()` in 7.5 s took all five containers and **no volume at all**; restarted from nothing in 14.5 s, `ac-db-import` NOT recreated, every account row byte-identical. Repair 29/29 against a REAL interruption (`docker kill ac-db-import` 25 s in, `acore_world` left at 26 tables of ~312 with no updater record): probe read `partial`, the repair with NO reset refused and left those 26 tables untouched, and with the reset dropped `acore_world`, re-ran the one-shot in 292.3 s — **same container id `ef8ec937fbf0` before and after** — and brought it back to 312 tables with the other two schemas untouched
    - [ ] **macOS (Baerthe)** — the teardown and the repair on real hardware. Teardown: remove containers, confirm both volumes survive, start again, confirm `ac-db-import` is not recreated and the characters are intact. Repair: it only offers itself on a database that was never imported or was left half-written, so producing that state means interrupting an import on purpose — the same `docker kill ac-db-import` the Linux gate used. **Automated Darwin unit tests verified (2026-08-29)**: `remove_staged` volume preservation, `ImportProbe` state transitions, and `repair_import` reset flow.
    - [ ] **Repair / re-import** (landed and live-gated 2026-08-23): `docker.repair_import()` plus `controller_wow_wotlk/repair.py`'s five-state probe (absent / partial / imported / populated / unreadable), wired through `Controller.repair_import()` to a two-press button on the Server tab that is only *visible* when the probe says there is something to repair. The line that matters is drawn on player data rather than on completeness: it refuses on `populated` and points at Restore, refuses while this install's servers are running, and fails closed on `unreadable`. It starts the database alone first (`docker.start_database()` — the one-shot runs with `--no-deps`, so compose brings up nothing its `depends_on` edge names, and the probe would have no container to ask), then runs `compose up --no-deps <import_service>` and nothing else, and re-probes rather than trusting the exit code — a one-shot that fails having done nothing exits the same way as one that worked. Design notes in `phase6-decisions.md`. **Live-gated 2026-08-23** on yulon-ubuntu, against a throwaway copy of the real playerbots install on a fresh empty volume, with container names renamed so it could not touch the original — deliberately giving it container names and service names that DIFFER, which also live-verified the same day's `spec.db`-where-a-service-name-belongs fix. Two of the three owed assumptions held: attached `compose up --no-deps <one-shot>` terminates (a 209.0 s call around a 208.0 s container), and it **re-runs an exited one-shot** with no `--force-recreate` (the same container started three times, exit 0 each time, refilling schemas dropped in between). The third was **false and broke the action**: an AzerothCore import applies every module's `db-auth`/`db-characters` updates too, so a first-ever import of a city-bots install finished exit 0 with all three schemas full AND 400 accounts + 400 characters written by the module — and the post-check, demanding `imported`, failed the action over its own success on every install this project ships. Widening it to accept `populated` then opened a second hole a review caught before it shipped: the probe answers `populated` on the FIRST row, so an import that seeds a module's accounts and then dies on the world schema is indistinguishable from a finished one. `ImportState.complete` now carries completeness beside the state — the state stays ordered by danger for the refusal, the post-check reads completeness. **Progress output landed too**: the import streams through `run_attached()` into the Server tab (argv byte-identical, pinned by a test, so the gate's evidence still describes it), bounded at 200 retained lines, with no cancel offered because there is none — and closing the window during an import, which froze it for 330 s and then aborted the process, is now declined with a reason. **The `partial` gate then ran (2026-08-23) and took most of the feature away.** It interrupted a REAL import — `docker kill ac-db-import` 19 s in — instead of manufacturing `absent` with `DROP DATABASE`, and found two things. (1) The probe called the wreckage `imported`: `acore_world` held **3 tables of 316** (`achievement_category_dbc`, `achievement_criteria_data`, `achievement_criteria_dbc` — the base dump had reached the letter "a"), and the probe asked only whether each schema had *any* tables. So `repair_import()` refused with "there is nothing to repair" and the button built for this state never appeared — and the `complete` field added hours earlier was much weaker than its docstring claimed, having inherited the same test. Completeness now reads `updates` and `updates_include`, AzerothCore's own updater bookkeeping, which the interrupted database had in auth and characters and not in world. (2) Far worse: run against that state, the repair **reported success in 28 s and made the database permanently unimportable**. AzerothCore skips the base data for a schema that already exists, so `acore_world` went 3 → **5** tables while `acore_world.updates` gained **2671 rows** — every remaining SQL file recorded as applied, so no later run will ever apply them. The action destroyed the only route out of the state it was built for. **Then the fix that follows from it was built and gated the same day.** An empty schema is the only input the importer treats as work, so `repair.reset_unfinished()` drops the schemas the probe found unfinished and `repair_import()` calls it BEFORE the one-shot. Live-gated against a fresh real interruption (`ac-db-import` killed 19 s in, `acore_world` left with **one** table and no updater record): probe reads `partial`, `acore_world` is dropped, the import re-runs in 195 s, and `acore_world` comes back at **316 tables** with `acore_characters` 108 → 111 — 10/10 checks, including the container ID (`882faf1762a1` before and after, so the same one-shot re-ran rather than a new container reusing the name). `acore_auth` and `acore_characters` were finished and were left untouched. Shape: a second seam (`ResetUnfinished`, with its own `SqlWrite` protocol) rather than a wider probe, because `docker.py` must not know a schema is called `acore_world` and the probe stays read-only; **optional**, so without it `repair_import()` still refuses `partial` outright rather than making the install permanently unimportable, which is what makes widening `repairable` safe; and the player-data refusal is asked a second time inside `reset_unfinished()` so it survives someone reordering the caller. The drop is routed *through the schema being dropped*, because when every schema is unfinished there is no survivor to connect through. **Also still open**: `acore_playerbots` is outside the probe's schema set — `CORE_DATABASES` is auth, characters and world (`maintenance.py:126`) — so the post-check cannot see that database on any path. The `ac-db-import` half is closed on the native path only: the generated compose gives it `AC_PLAYERBOTS_DATABASE_INFO` (on the `ac-db-import` service in `catalog/installers/wow-wotlk/native/base.yml.tmpl` — named by service and key rather than by line, because the line number cited here was wrong in every committed version of that file and would have gone wrong again the first time the template grew), while the bash installers still do not name it there and leave the schema to the worldserver's own loader, so on a script-installed playerbots server the action can still report success with that database missing. (The container-ID gap the first gate left is closed twice over: the `partial` gate pinned `fe97a175b31d` before and after, and the reset gate above pinned `882faf1762a1`.)
  - [ ] Console: `docker attach` pty transport + full `CONTROLS-2.md` GM console (safe attach/detach, GM commands) on Linux/macOS; account creation no longer depends on the console (SRP6 path) — the Windows "no interactive GM console" gap is separately re-scoped, not left silently broken. **The re-scope landed 2026-08-23**: account creation is off the console entirely (its own tab, SRP6), and where there is no pty the Send button and command box are disabled with the reason on the tab, the same shape as 6.1's catalog tile. Following the worldserver log needs no pty and stays enabled. The GM console itself is still Linux/macOS only
    - [x] **macOS, real Docker Desktop (2026-08-29)** — `console.send_command()` run twice against the live playerbots worldserver below (`server info`, `account onlinelist`, the latter returning hundreds of real bot session lines). Each reply was cut to its own command by the prompt parser — no bleed from the previous one — and the detach did not touch the server: `docker inspect` read `RestartCount=0` and the same `State.Pid`/`StartedAt` before and after both attach/detach cycles. This is the item macOS can actually complete (`pty_supported()` is `hasattr(os, "openpty")`, True on Darwin), and it is now live-gated rather than only unit-tested.
  - [ ] Account creation (`CREATE-ACCOUNTS.md`/`CONTROLS-1.md`): SRP6-over-`DockerSql` (SOAP cannot bootstrap the first account) — byte-exact against a server-written verifier, no password echo, "already exists" handled, all three platforms. **Module and UI wiring landed 2026-08-23**, and the byte-exactness was verified against accounts a real server wrote (same salt in, same verifier out, non-ASCII passwords included). Unticked because macOS has not been exercised; Linux and Windows have (see the box below)
    - [x] **Windows (2026-08-23)** — created through the real `accounts.create_account()` over `DockerSql` against Docker Desktop; a second create of the same name reported "already exists" without duplicating the row; non-ASCII password round-tripped; gm level written
    - [x] **macOS, real Docker Desktop (2026-08-29)** — `accounts.create_account()` run directly against the live install below with a non-ASCII password (`Café1234`, the exact case `str.upper()` vs `fold()` gets wrong): first call `created=True`, second call on the same name `created=False` with "already exists", no duplicate row. The `DockerSql` seam behaves the same through Docker Desktop as it does on Linux/Windows. Login through a real client is still owed (needs a client install, out of scope for this pass).
  - [ ] Maintenance (`CONTROLS-1.md`): cache clear, DB backup/restore, SQL changes — `maintenance.py` implemented, rebuild/restart wiring done, all three platforms. **Backup/restore and the UI landed 2026-08-23**: restore is plan-then-apply, with every refusal shown at once, the button armed only by an allowed plan, and the slot refusing again regardless of the button. Cache-clear is deliberately NOT implemented, with the evidence in the module docstring. Unticked because macOS has not been exercised — Linux and Windows have. The Linux backup/restore round trip WAS run against a live server on 2026-08-23 (four schemas, a 292.2 MB `acore_world` dump, the restored value read back, a wrong token refused)
    - [x] **Windows (2026-08-23)** — backup and restore round trip against the live server, wrong token refused. **This gate found what the Linux one had missed**: a restore is a MERGE, not a replacement. A marker table created in `acore_world` after the backup was still there after a full 306 MB restore of that schema — 313 tables where the backup held 312 — because mysqldump emits `DROP TABLE IF EXISTS` per table and no `DROP DATABASE`. Not platform-specific: the Linux gate checked that the restored value read back, not that nothing extra survived. Behaviour deliberately unchanged (`--add-drop-database` would make a part-way failure leave nothing at all, which breaks the `interrupted_restore()` + safety-copy recovery), and the argv is now pinned so the flag cannot be added without meeting that argument. Three user-facing claims were corrected on 2026-08-24: the README said "replaces", `_safety_backup()` promised an undo, and the Maintenance tab warned "Every character on the server is replaced" on EVERY allowed plan with no check that `acore_characters` was in it
    - [ ] **macOS (Baerthe)** — back up a populated server, restore it, and confirm the wrong-token refusal still refuses. Worth watching: the backup moves multi-hundred-MB dumps through a bind mount, which is where Docker Desktop for Mac is slowest and where a timeout that is comfortable on Linux may not be. **Automated Darwin unit tests verified (2026-08-29)**: backup and restore plans, MariaDB 10.6 sandbox mode parsing, and merge-based restore semantics.
  - [ ] Modules/mods: install/remove via the applier + rebuild/restart; manifest store GitHub refresh + bundled fallback
    - [ ] **macOS (Baerthe)** — apply a module, rebuild, restart, confirm it is live; then remove it and confirm it is gone. **The longest of these by far** — the rebuild compiles AzerothCore — so it is worth doing last, and worth recording the wall-clock, since nobody knows what that build costs on Apple silicon through Docker Desktop
  - [ ] Networking auto-setup (README §13, full `WoW-Wotlk-NETWORKING.md` scope): firewall (ufw/firewalld/netsh + **macOS firewall designed/implemented**), WSL2 portproxy, LAN/public IP detection, realmlist updater + client writer, 0.0.0.0 binding check, CGNAT/DuckDNS/router-step prompts
  - [ ] Self-update check (README §10) — no platform-specific `config_dir()` issues
    - [x] **Windows (2026-08-23)** — `platform.detect()` -> `windows`, `config_dir()` -> `%APPDATA%\yulon`, the update check does not stall
    - [ ] **macOS (Baerthe)** — confirm `config_dir()` lands somewhere sane (`~/Library/Application Support/…`) and that the update check does not stall behind Gatekeeper. Needs no server. **Automated Darwin unit tests verified (2026-08-29)**: `platform.detect()` -> `macos`, `config_dir()` -> `~/Library/Application Support/yulon`, `detect_alf_state()` and `alf_unblock_commands()` for Application Firewall.
  - [ ] Packaging: live-gated against the packaged `.AppImage`/`.dmg`/`.exe`, not just `python main.py` from source
    - [x] **Linux `.AppImage` (2026-08-25)** — the packaged artifact launched and driven on clean Fedora 44 (Wayland) and clean Arch + Xfce (X11), both from cold checkpoints. Qt picks the wayland plugin by default on Fedora; forcing `xcb` exercises the bundled libxcb-cursor. **The AppImage still requires FUSE** — `fusermount3` is a setuid helper and no packaging choice removes it, which is why the `.tar.gz` beside it exists; that fallback was confirmed on the FUSE-less Arch box. Two automation notes for whoever repeats this: Fedora's `xdotool` is libei-patched, so on Wayland every synthetic click raises an xdg-desktop-portal "Remote Desktop" consent dialog OVER the launcher — either grant it once or drive the app on a private Xvfb; and `pkill -f` matches the ssh command line carrying the script, killing the shell that ran it.
    - [x] **The SHIPPED artifact could not start on Arch at all (found 2026-08-25, fixed on the same branch)** — `Yulon-v0.6.51Public-x86_64.tar.gz` aborts before drawing anything with "From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed". **That message is a red herring**: `libxcb-cursor.so.0` IS bundled and #96 works. Qt prints that line whenever the xcb plugin fails to load, for any reason; `QT_DEBUG_PLUGINS=1` gives the real one — `libqxcb.so` cannot resolve `libxkbcommon-x11.so.0`. The bundle carries `libxkbcommon.so.0` and not the `-x11` one; they are different libraries and clean Arch has neither. Same tree, two builds: the CI artifact has one library and aborts, a build on a box with the package has both and starts. It is #96's trap one library over — PyInstaller bundles what the BUILD HOST has — and Fedora hid it because GNOME pulls the package in. `libxkbcommon-x11-0` added to the workflow's apt step. **A bundle nobody launches on a minimal distro will keep producing these one at a time; a CI check that runs the built binary headless, or asserts a soname list, would catch the class rather than the instance.**
    - [ ] **macOS (Baerthe)** — **run the CI-built `.dmg`.** The one artifact nobody has ever launched: Phase 5 proved it builds and nothing more. Needs no server, so it is the first thing to try. Wanted back: whether Gatekeeper blocks it and exactly what a user has to do about it — an unsigned `.dmg` is a shipping decision, not just a test result
  - [ ] User-facing README topics (`pylauncher/README.md` + `archive/guides/wow-wotlk/README.md`) accurately reflect each platform's real state — no "works on macOS" claim before 6.2 is done. **Rewritten 2026-08-23.** The defect this item names did not exist: neither file contained the string "macOS" at all. The real one was the opposite — `pylauncher/README.md` was a single line saying the folder was "pending", with a link to `pyplan/README.md` that resolved to `pylauncher/pyplan/README.md` and 404ed. It is now a per-platform capability table with **three** values, not two: *run live*, *built* (code and tests, nobody has driven it) and *never run* — because "yes" was carrying both of the first two, which is the conflation this item exists to prevent. Review then found four claims in the rewrite that were themselves wrong, all corrected: that the app never downloads client files (WotLK is the one entry with `requires_client_dir` false, because the server fetches AzerothCore's own client-data archive — the same over-broad claim was in `installer.py`'s module docstring and is fixed there too); that the Catalog's Install *button* is proven, when the live install ran through the CLI harness; that account creation "works on all three platforms", when only the transport can and only Linux has been run; and "there is no Mac on this project", which claims more than "no Mac on this side of it". Restore now says what it destroys, the artifacts say "opens" rather than "launched" (the evidence is a `YULON_SMOKE_TEST` headless run), and `DISCLAIMER.md` and the Releases page are linked. The archived shell-script guide gained a header saying what it is and is not, and its three dead links were fixed. **Unticked**: the item asks that all three platforms be reflected accurately, and the macOS column is still "never run"
  - [ ] **TBC, Vanilla and Tortoise cannot be remembered by the GUI even after a successful install (found 2026-08-25, NOT fixed — the installers are being converted from shell to Python as part of the phasing, so this is a note for that work rather than a patch).** Two independent reasons, either alone sufficient: all three hardcode `SERVER_DIR` (`$HOME/wow-tbc-server` and friends) and have no `Install path:` prompt, so the folder the picker returns is ignored entirely; and TBC and Vanilla write `compose.yml` while `catalog_view.py` (`:217`, `:335`) requires `docker-compose.yml` in the folder it picked. So a multi-hour compile can complete and the launcher will still say there is nothing there. Also: the WotLK scripts' sudo banner promises "Fixing file ownership after build" and the files contain no `chown` at all — a password asked for something never done.
- [ ] **Phase 6 exit criteria met** — WoW WotLK has 100% working feature coverage (6.5) on Linux, macOS, and native Windows, zero shell interaction, no silent off-Linux fast-fail. ~~**Phase 7 does not start until this is fully met.**~~ **Gate lifted 2026-08-26** by owner decision (`pyplan/phase7-decisions.md`): Phase 7 runs Linux-first while the macOS gate waits for hardware and the Windows 6.3 blocker is closed by Phase 7.7. The items above stay owed.

---

## Phase 7 — One install engine for all four servers (Python everywhere; Linux first)

> **Scope change, 2026-08-26 (owner decision; `pyplan/phase7-decisions.md`).** Phase 7 no longer
> waits for Phase 6's macOS/Windows gates (no Mac exists; Windows is blocked at `ac-db-import` on
> 9p) and no longer starts with controller packages. It starts by putting every server on the one
> Python install engine — WotLK on Linux first, retiring the bash installers, then TBC, Vanilla and
> Tortoise on Linux, then Windows, then macOS when hardware exists — and gives each server its
> controller package after it can be installed. The four original 7.x lines are kept below, unticked
> and re-pointed, not deleted. Same "100% working coverage" bar; v1 scope remains these four servers.
> The roadmap's §7 has NOT been edited (roadmap is edited only when explicitly tasked);
> `phase7-decisions.md` Appendix A holds the proposed text.

> **Getting a game client onto a Hyper-V VM: use a VHDX, not the network (2026-09-02).** Filed here
> rather than in a gate log because every Phase 7 gate left needs a multi-GB client on a VM, and the
> next person to want one should not rediscover this. Over the network, laptop → Hyper-V guest ran at
> **0.42 MB/s** (200 MB in 8 minutes, measured) — the DERP-relayed path, so 4 GB that way is about
> **2.7 hours**. What was done instead: a **16 GiB dynamic VHDX created on the host**, formatted
> exFAT, filled **host-locally** (3.8 GB in 52 s, recorded as ~74 MB/s), then **hot-added to the
> RUNNING VM on the SCSI controller** and read by the guest (4.0 GB in 14.6 s, recorded as
> ~265 MB/s). **About 70 seconds end to end against 2.7 hours.**
>
> **Two gotchas, both from the run.** The exFAT volume comes up as **`/dev/sdb2`, not `sdb1`** — GPT
> puts a 16 MB Microsoft-reserved partition first, so mounting `sdb1` fails in a way that looks like
> a bad format. And **no VM restart is needed**: the disk was added to a live guest and appeared at
> once. That second one is independently confirmed rather than taken on trust — the file landed on
> `yulon-ubuntu` at 22:00 while `vanilla-db`, `vanilla-mangosd` and `vanilla-realmd` still read
> `Up 3 hours` at 22:07, so the guest that received the disk had been running since before the
> Vanilla install finished at 19:17.
>
> **The VHDX is kept at `D:\VMs\transfer\yulon-transfer.vhdx`** on the VM host (`ssh vmhost`), for
> the next gate to reuse rather than rebuild. Re-checked there 2026-09-02 22:1x: `Size`
> 17,179,869,184 (16 GiB), `FileSize` 4,097,835,008, `VhdType Dynamic`, **`Attached: False`** — so it
> is detached and free. The payload it carried this time is on the guest as
> `~/clients/TurtleWoW-1.18.1-7272-Hotfix-2026-04-12.zip`, **4,038,524,880 bytes**, which is the size
> the 14.6 s figure refers to. The durations above were taken by the run that did it; what is
> re-verified here is the geometry, the detached state, and the bytes that arrived.

- [ ] 7.1 Spine + `AzerothCoreInstaller`, Linux native — `StagedInstaller`/`Stage` extracted from `native.py`, WotLK stage names unchanged and pinned; the 7.1 catalog models (`EmulatorSource.dest`, `PasswordPlan`, `DbFacts`, `ReadyMarkers`, `NativeInstall.family/images/image_prefix/azerothcore`); `ask` forwarded to `ensure_docker`; once-only sudo password (`SudoSession`, `sudo -S`) in provisioning; `docker-buildx` on the dnf and pacman lists; SELinux facts + `{{BIND_LABEL}}` on every host bind line + relabel; `systemd-inhibit`; `install_wiring.py` (probe wiring + the CLI harness); `wait_ready(ReadySpec)`; the proven install's `docker compose config` committed as `tests/data/wotlk-compose-config.json`; wow-wotlk dispatches native on Linux
  - [ ] Gate: yulon-ubuntu clean checkpoint, **two presses** — press 1: consent dialog + sudo dialog once + re-login report; re-login; press 2: `ready`; kill mid-build, resume skips the compile; `docker compose config` matches the fixture; auth log `127.0.0.1:8085` with no `UPDATE`; account + client login from the host after the LAN step
  - [ ] Gate: packaged artifact on clean Fedora 44 (SELinux, password sudo, moby-engine + buildx) and clean Arch (pacman + buildx)
    - **Fedora 44 progress, 2026-08-31 — the ENGINE passed; the line stays open because it asks for the packaged ARTIFACT.** Run on `yulon-fedora-gate`, a clean box cloned from `yulon-fedora`'s `clean-desktop` (the owner's own Fedora box could not be restored — it carries his TBC and Tortoise servers with containers running). Driven headlessly over ssh through a pty driver, because the harness declines every question when stdin is not a tty and hangs forever on a pty fed by a heredoc. Press 1: consent once, **sudo password asked once — the first time `SudoSession` has ever been exercised**, both other Linux boxes being passwordless; `dnf -y install moby-engine docker-compose docker-buildx` installed 29.7.2 / 5.5.0 / 0.36.1; group joined; correct re-login refusal; no state file. Press 2: build 1829/1829, client data 1140 MB in 1m58s, **`ac-db-import` exit 0 under SELinux Enforcing** — the 2026-08-25 failure does not recur and this is the first proof of it under the Python engine. Schemas 22 / 111 / 30 / 315, byte-identical to the macOS, Ubuntu and Windows records. `:z` counts 7 + 1 as predicted; `env/dist/etc`, `env/dist/logs`, `modules` and the root all `container_file_t`; `relabel_for_containers()` proven in isolation (`user_home_t` → `container_file_t`, recursive, no sudo) because a successful relabel logs nothing and `:z` on the clone mount would have covered for it either way. Compose diff PASS (57 passed). Re-press on a finished install: **7 s**, compile skipped. Ports: 3306 and 7878 on `127.0.0.1`, 3724/8085 open as clients need. `ls ~/dads-mmo-lab-install-*.log` → 0: zero bash. **The plan's compose-diff command is stale** — it names `YULON_COMPOSE_ROOT`, but E.2 changed the mechanism and BOTH `YULON_COMPOSE_CONFIG` and `YULON_COMPOSE_ROOT` are needed; with only the root set the test SKIPS silently and reads exactly like a pass.
    - **Still owed on Fedora:** the run from the CI-built AppImage rather than the CLI harness (what this line actually asks for), and the kill-mid-build interrupt (the build finished before it could be taken; Ubuntu proved that path, Fedora only proves resume-on-complete).
    - **Arch progress, 2026-09-01 — the ENGINE passed; the line stays open because it asks for the packaged ARTIFACT.** Run on `yulon-arch`, driven headlessly through the same pty driver Fedora needed. Every stage reached in order: `clone-core`, `clone-modules`, `generate-compose`, `build` (1829/1829), `client-data`, `start-db`, `import`, `up`, `ready`; `install of wow-wotlk finished`, driver child exit 0, state file recording all six persisted stages. Schemas **22 / 111 / 315 / 30** (auth / characters / world / playerbots) — byte-identical to the macOS, Ubuntu, Windows and Fedora records, which is now five platforms agreeing. Ports as designed: 3306 and 7878 on `127.0.0.1`, 3724 and 8085 open as clients need. `ls ~/dads-mmo-lab-install-*.log` -> 0: zero bash. Compose diff **PASS (57 passed)** against `tests/data/wotlk-compose-config.json`.
    - **What Arch specifically proves, and nothing else could.** The pacman branch installed `docker 1:29.7.2-1`, `docker-compose 5.5.0-1` and **`docker-buildx 0.36.1-1`** — the package name this branch's `platform.py` fix exists for. Fedora's dnf branch wants `docker-buildx-plugin` after Docker's CE repo is added; the two names are not interchangeable and Arch is the only box that can show the pacman half is right. `getenforce` is absent here, so the SELinux path takes its **third** answer (could-not-ask -> `unchecked`) rather than either boolean — the case that produced the Fedora failures, exercised on a machine where it is the normal state rather than a fault.
    - **A capture trap, recorded so the next gate does not lose an hour to it.** `docker compose -f docker-compose.yml config` does NOT load `docker-compose.override.yml` — passing `-f` at all disables the automatic override discovery. The first Arch capture was taken that way and the diff reported two real-looking differences: no `./modules` bind on the worldserver, and four missing `AC_AI_PLAYERBOT_*` / `AC_PLAYERBOTS_UPDATES_ENABLE_DATABASES` env keys. Both live in the override file by design, and both were present all along. The correct capture is `docker compose config` run **from the server directory with no `-f`**. This is the same shape as the stale-marker and the `YULON_COMPOSE_ROOT`-only traps already recorded on this line: an incomplete capture reads exactly like a real defect, and in all three cases the artifact was believed before the machine was asked.
    - **Windows 11 progress, 2026-09-01 — the ENGINE passed on a real Windows box.** Run on `yulon-win11-gate`, a Hyper-V guest with genuine TPM 2.0, nested virtualisation, WSL2 + VirtualMachinePlatform and Docker Desktop 29.7.2 — retail apart from Secure Boot, which nothing here touches. All nine preflight checks passed, including *sharing the folder with Docker: a container can read `C:\gate\wotlk-server`*, the check every earlier Windows attempt died on. Every stage reached in order; state file records all six. Schemas **22 / 111 / 315 / 30** — identical to macOS, Ubuntu, Fedora and Arch, so **six platforms now agree on the same four numbers**. `World Initialized In 4 Minutes 18 Seconds`, **500/500 bots logged in**, all three containers healthy, ports as designed. Compose diff recorded as **PASS — but the test count filed with it, "57 passed", is UNVERIFIABLE and is not evidence of a Windows run.** `tests/test_compose_fixture.py` held **57** test functions until `cdbb5895` (2026-09-01 04:24) and **59** after, and `cdbb5895` is the same commit that made this gate runnable on Windows at all — it is the `support_compose.volume_from_config` fix recorded two bullets below. A Windows run of the diff can therefore only have been a 59-test file, so 57 is the pre-fix count, carried over from a Linux record rather than read off this run. The run's own log was destroyed the same night (next bullet), so the real number cannot be recovered; it is left unstated rather than guessed. Measured 2026-09-02 at `f6ed1b9a`: `git show cdbb5895^:…/test_compose_fixture.py | grep -c '^def test_'` = 57, the same at `cdbb5895` = 59.
    - **The evidence log of that successful run no longer exists, and the harness destroyed it.** `run-gate.cmd` redirects with `>`, so a second run truncates the log in place. A second run started ~2 hours later and overwrote 402 KB of a passing install with 3.8 KB ending `install failed: Docker isn't available`. The exit-code marker read `1`. **Both artefacts describe the second run's Docker probe, not the install** — which was verified from the machine instead: schemas, a populated 500-bot realm, listening ports and a state file with six completed stages. The second run failed because **Docker Desktop is per-user and its engine does not answer in a non-interactive session**; the install had been driven from an interactive one. Two fixes owed: `run-gate.cmd` must append or rotate rather than truncate — it already deletes the exit-code marker first, for exactly this class of bug, and then destroys the log — and the Windows gate must be documented as interactive-session-only.
    - **A defect in our own test support, found only because Windows was gated.** `tests/support_compose.py::volume_from_config` relativised bind paths with `root.rstrip("/")` and `startswith(f"{base}/")` — POSIX-only. Windows `compose config` reports `C:\gate\wotlk-server\env\dist\etc`, nothing stripped, and the shape could never match the fixture: **the compose-diff gate was unrunnable on Windows**, which is why no Windows compose diff exists before tonight. Fixed by deciding from the SHAPE of the paths, never from `os.name`, so one fixture serves all three platforms and a Windows-shaped capture normalises correctly on Linux CI. The sibling-directory guard (`…-server-backup` must not be rewritten into `./-backup`) is preserved and now tested on both separator styles, plus drive-letter case and UNC. Windows paths fold case; POSIX paths deliberately do not.
    - **A third capture trap, same family as the other two.** Windows PowerShell 5.1's `Out-File -Encoding utf8` writes a **BOM**, and `json.load` refuses it outright (`Unexpected UTF-8 BOM`). Captures must be written with `[System.IO.File]::WriteAllText(..., New-Object System.Text.UTF8Encoding($false))`. With the `-f` trap and the `YULON_COMPOSE_ROOT`-only skip, that is three ways to produce a capture that reads like a defect or a pass without being either.
    - **Real client login — PROVEN, 2026-08-31, first time on any platform.** The gap matrix built the same evening found this step at zero everywhere and on no run sheet. Account made through `accounts.create_account()`, realm advertised through `networking.plan()`/`apply()`, and the owner logged in from his laptop on a different machine. Server-side: `auth.account` 101 GATETEST `last_login 2026-08-31 20:48:17` `online=1` `failed_logins=0`; `characters` 1001 Gatetest night elf hunter level 1 `online=1`. Two things only a live run could show — the LAN step **refused rather than blocked** when sudo wanted a password, returning all four `firewall-cmd` lines by name in `report.skipped`; and `networking.plan()` cannot see past NAT, filed in `bug-checklist.md`.
    - **Linux console gate — PASS, 2026-08-31**, closing the row the Phase 6 matrix found reading macOS-only (the Linux evidence existed but lived in `console.py`'s docstring, never in this file). `server info` → 9 lines; `account onlinelist` → 504 lines; pid 2991 and `StartedAt` unchanged across both cycles, `RestartCount` unmoved, still running, replies distinct.
    - **Linux port-conflict guard — PASS both halves, 2026-08-31**, proven on Windows and macOS and never gated on Linux. A stranger holding 3724 is found by name (`['yulon-port-hog']`); the install's own containers are also flagged (`['ac-authserver', 'ac-worldserver']`) — the D6 limitation its own docstring admits, now measured rather than asserted.
  - [ ] Gate: busybox/mariadb:11 primitives live (`-u`, `:ro` refusal, `copy_from_image`, `exec_stdin` + gzip, `mariadb` client name, restart-loop detection)
    - **PASSED on Linux, 2026-09-01** — `yulon-ubuntu`, Docker 29.1.3, cloned from `yulon-phase7` at `79ea63c`: **20 passed / 1 skipped**, `docker ps -aq` and `docker volume ls -q` byte-identical before and after. Also 16 passed / 5 skipped on Windows/Docker Desktop 29.6.2, where four gates are Linux-only.
    - **What only Linux could prove.** `platform.container_user_args()` returns `[]` off Linux, so every `--user` assertion on Docker Desktop is a statement about busybox's default user, not about `ContainerRun` — a review demonstrated that by re-running the gate with `to_argv()`'s `*self.user_args` deleted and watching it still pass. On Linux the argv really carries `--user 1000:1000` and the container reports it back. Measured consequence: with the flag, extracted files land `uid=1000 gid=1000`; without it, `uid=0 gid=0` — a server folder full of root-owned map data on the user's own machine.
    - **A skip can no longer read as a pass.** `YULON_REQUIRE_DOCKER=1` turns an unreachable daemon into a failure; without it the skip reason names what did not run. All three branches exercised. CI is stronger still: the `integration (live Docker)` job runs `docker info` before pytest.
    - **`docker rm -f` was leaking a volume every run.** `mariadb:11` declares `VOLUME /var/lib/mysql`, so each gate stranded ~200 MB under a 64-hex name `docker ps -a` cannot show. Now `rm -f -v`, and the teardown asserts on survivors so a leak is an error naming the volume.

- [ ] 7.2 Delete the bash lineage — six `install-*.sh`, `dml-start.sh`, `wow-manage.sh` (eight files, 19,451 lines), `installer.Installer`/`PROMPT_RULES`/`make_responder`/`bash_available`, script tests, `Install.script*` fields; the three CMaNGOS entries KEEP `platforms: ["linux"]` (see the note below); gaming mode → `catalog/installers/steam-deck/setup-gaming-mode.sh`; `contribution.md` harness paragraph rewritten; style-guide §3 rows for `catalog/installer.py` and `catalog/catalog.py`
  - **The `platforms: []` half of that list was NOT applied, and must not be.** The 7.2 plan was
    written for an order in which 7.2 ran BEFORE 7.3; the owner's ruling reversed it, so by the time
    F.4 landed the three entries already had a family engine, and K.8 registered `cmangos` in
    `FAMILIES`. Emptying the list would have disabled the Install button on three of the four shipped
    games — `Install.supports()` is `platform_id in self.platforms`, and `ui/catalog_view.py` asks it
    twice (once to grey the tile, once in `start_install()`) — so F.4 kept `platforms: ["linux"]` on
    all three. It is not a value the model accepts any more either: `Install.platforms` carries
    `min_length=1`, pinned by `test_an_entry_installable_nowhere_is_refused`. Verified 2026-09-02 at
    `f6ed1b9a`: `wow-tbc`, `wow-vanilla` and `wow-tortoise` each read `"platforms": ["linux"]` in
    `catalog.json`, so those three Install buttons are **live on Linux** — which is exactly the state
    bug-checklist §32 puts to the owner. This line said the opposite until 2026-09-02, and §32 is read
    while deciding it.
  - [ ] Gate: full checks green; 7.1's Ubuntu gate re-run from the same checkpoint with no other change
    - **Static half PASSED, 2026-09-02, overnight run.** `yulon-phase7` at `0e394d9b`: **1974 passed,
      3 skipped** on yulon-ubuntu (`-m "not integration"`), mypy `Success: no issues found in 48 source
      files`, ruff `All checks passed!`, black `105 files would be left unchanged`, working tree clean.
      7.2 F.1-F.6 and the whole of 7.3 are merged.
    - **LIVE half NOT RUN, and deliberately.** The gate is a two-press WotLK install driven through the
      GUI on the restored `clean-ssh` checkpoint — press 2 is a **2-4 hour compile**. The plan's own
      Step 3 opens with *"the user's go-ahead first"*, and the standing rule is that the owner starts a
      build himself. Nothing is blocked; it needs the VM powered on and someone to press go.
    - **What it still owes when someone runs it:** press 2's wall clock (the `--- ready` timestamp minus
      the press), and `docker compose config --format json` from `~/wow-server-playerbots` diffed against
      `tests/data/wotlk-compose-config.json`, 7.1's fixture. **Do not tick this from a unit suite** — the
      integration tests self-skip without a daemon, so a green `pytest` says nothing about the half that
      matters. Same trap as the 7.3 primitives gate, recorded above.
    - **Also still owed by 7.3:** its primitives gate, written by K.8 and likewise not run. And note the
      correction filed there — the 2026-09-01 record under 7.1 **cannot** stand in for it, because
      `test_sqlplan_live.py` postdates that run entirely (21 test functions then, 23 now).
- [ ] 7.3 CMaNGOS data model + pure stage kinds — catalog 7.3 models (`Source.rev`, `dockerfile_dir`, `CmangosData`: `ClientSpec`, `DockerfileSpec`, `ExtractPlan`, `MmapPlan`, `ConfPatchTable`, `SqlPlan`); `families/cmangos.py`; `clientdir`/`dockerfile`/`extract`/`conf`/`sqlplan`; `docker.run_container`/`copy_from_image`/`exec_stdin`; all four entries validate; WotLK templates byte-identical; static catalog invariants test
  - [x] Gate: busybox/mariadb:11 primitives live (`-u`, `:ro` refusal, `copy_from_image`, `exec_stdin` + gzip, `mariadb` client name, restart-loop detection)
    - **PASSED 2026-09-02 on `yulon-ubuntu`** — Linux 7.0.0-30-generic, Docker 29.1.3, Python 3.12.3,
      `yulon-phase7` at `5a1098d9`. **22 passed, 1 skipped in 149.33s.** Full log:
      `pyplan/gates/7.3-yulon-ubuntu.log`. Run twice, 154.42s and 149.33s, same counts both times.
    - **`docker ps -aq` and `docker volume ls -q` byte-identical before and after** — 5 containers and
      2 volumes each way, diffed, not eyeballed. Nothing leaked.
    - **Run with `YULON_REQUIRE_DOCKER=1`**, so an unreachable daemon would have been a FAILURE rather
      than a skip. That is the guard that makes this tick mean something; without it a green run says
      only that pytest started.
    - **Each of the six named items has a test, checked by name before this box was ticked** rather
      than inferred from the total: `-u` → `…run_container_reads_the_client_read_only_and_writes_out_as_this_user`;
      `:ro` refusal → `…a_read_only_client_mount_refuses_a_write`; `copy_from_image` →
      `…copy_from_image_leaves_no_container_behind_either_way`; `exec_stdin` + gzip →
      `…exec_stdin_streams_a_gzipped_dump_and_sql_query_reads_it_back`; restart-loop →
      `…wait_ready_gives_up_on_a_crash_loop_long_before_its_timeout`; and the **`mariadb` client name**
      by `test_sqlplan_live.py`, which passes `client="mariadb"` to a real `mariadb:11` at two call
      sites. `docker.sql_query` takes the client's name as DATA (`DbFacts.client`) and does not know
      which binary the image ships, so that item is only exercised by a run that names it.
    - **The one skip is not this gate's:** `test_wotlk_live.py::test_wotlk_controller_start_ready_stop`
      needs `YULON_WOTLK_SERVER_DIR`, i.e. an AzerothCore server already installed on the box. It
      belongs to 7.4, not to the primitives.
    - **This is why the 2026-09-01 record under 7.1 could not stand in.** `test_sqlplan_live.py` — the
      two tests that carry the `mariadb` client-name item and the live `sqlplan.apply` proof — did not
      exist at `79ea63c`. Measured: 21 test functions in `tests/integration/` then, 23 now. The earlier
      run was real and was a different gate.
 The eleven steps are in `pyplan/phase7-plans/7.3-cmangos-family.md`, Task K.8 step 7: hand the box over with `yulon-use.ps1 ubuntu`, announce through `claude-say`, sync the checkout, run the unit suite, pull `busybox:1.36` and `mariadb:11`, run `pytest -m integration tests/integration`, copy the log to `pyplan/gates/7.3-yulon-ubuntu.log` (that directory does not exist yet), record the five numbers here, shut the box down. K.8 landed the code half only — the family registered, the stage tuple pinned, dispatch proved for all three CMaNGOS entries — because this gate starts containers and pulls images and the standing rule is that the owner starts a run himself. Nothing is blocked; it needs the VM powered on and someone to press go. **Do not tick from a unit suite alone:** a `SKIPPED` in the integration run means the daemon was never reached, which is the failure a gate this shape exists to catch.
    - **This gate's text is duplicated verbatim under 7.1 above, where it is recorded as PASSED on 2026-09-01** (`yulon-ubuntu`, Docker 29.1.3, `yulon-phase7` at `79ea63c`, 20 passed / 1 skipped). **It is one gate written twice, mis-filed** — `79ea63c` is *"Task H.6: the Group H primitives get gates that run them for real"*, which is a 7.3 task recorded under 7.1.
    - **But the 7.3 line still needs its own run, and an earlier draft of this note said otherwise.** It claimed "same suite, same two images, so the substance of the run below already exists". Measured 2026-09-02: `git diff --stat 79ea63c HEAD -- pylauncher/tests/integration/` is `conftest.py +10/-…`, `test_docker_live.py +101/-…`, and **`test_sqlplan_live.py` +160, which did not exist at all**. At `79ea63c` the integration suite held exactly **21** test functions (4 + 16 + 1) — precisely the 20 passed / 1 skipped on that record. Today it holds **23**. The live `sqlplan.apply` / `exec_stdin` + gzip proof against a real `mariadb:11` is two of the six things this gate line names, and it **postdates the recorded run**. So the 2026-09-01 numbers cannot stand for this gate; whoever runs it runs it fresh, and the 7.1 copy should be moved here rather than counted twice.
- [x] 7.4a WoW TBC through `build` — build time and context-transfer time recorded; kill + resume skips the build
  - **PASSED 2026-09-02 on `m910q`**, not on `yulon-ubuntu` as the line says. TBC has
    `requires_client_dir: true` and its preflight REFUSES without one, so "through build" cannot be
    reached on a box with no client; m910q is where the clients live. The line's box name was
    written before that was known.
  - **Build: 2357.1s (39m17s)** on 4 cores, producing `yulon.local/cmangos-tbc-server:native-1cbfa4ac`
    (395 MB). **Context transfer: 279.81 MB in 2.8s**; the Dockerfile itself 3.88 kB.
  - **Resume skips the compile**, in the engine's own words: `The server is already built; skipping
    the compile.` The three clones likewise: `cmangos/mangos-tbc is already in src/mangos-tbc;
    leaving it exactly as it is.`
  - **The mid-build kill was measured on WotLK instead, and the number is the useful part.** Killed
    at object 1597/1829 after 757s; the resume reached 1682/1829 in 120s, because
    `apps/docker/Dockerfile` line 83 mounts `--mount=type=cache,target=/ccache`. BuildKit does not
    cache a partial `RUN`, so the STAGE re-runs — what is skipped is the compilation, not the step.
    A gate line that says "resume skips the build" is describing the effect, not the mechanism.
- [x] 7.4b WoW TBC extract + mmaps with the 2.4.3 client — client untouched; per-tool counts; resume runs only the unfinished tool
  - **PASSED 2026-09-02 on `m910q`.** Per-tool counts: **dbc 185, maps 3586, Buildings 7171,
    vmaps 8099, mmaps 2819** across 72 maps.
  - **Nothing was written into the client**, established three ways rather than asserted: **0 files**
    under `~/clients/WoW-Client-2.4.3` have an mtime at or after the install began (15:35:50); the
    newest file in the whole 8.0 GB tree dates from **2023-03-24**; and **all four** client mounts
    across both runs were `:ro` with **zero** writable ones (`grep -c` on the recorded `docker run`
    lines). Baseline for future runs, taken after the install:
    `sha256 bff72303c63c1a202c78ce8b56f8bfe5342ca15816f45a71c68d220c6be3a358`, 200 files,
    8,490,172,040 bytes. **The mtime evidence is the weaker half** — a write that preserved mtimes
    would not show — which is why the content hash exists; it is a baseline, not a before/after.
  - **Resume ran only the unfinished tool.** The first run died inside `vmap assemble`; the resume
    reported `dbc and maps: already extracted (dbc: 185 files, maps: 3586 files)` and `vmap extract:
    already extracted (Buildings: 7171 files)` and re-ran the assembler alone. That is the criterion
    in substance; no separate kill-after-`ad` was staged because a real failure supplied one.
  - **No symlink-farm fallback was needed**: no tool refused the `:ro` mount. Recorded because the
    line asks for it, not because it happened.
  - **It found a real defect, which is why it took two runs.** `vmap_assembler Buildings vmaps` does
    not create its output folder; it died with `Cannot open vmaps/000.vmtree` and then `error
    converting Abandonedorcbarracks.wmo`, naming a model file rather than the missing directory.
    Fixed in `extract.make_out_dirs()` (2ce89000), with the same hole found by reading one stage
    later in `run_mmaps`, which WIPES `mmaps/` and never put it back.
- [ ] 7.4c WoW TBC conf + import + ready — every `warn` phase justified or flipped; marker written; interrupted import → `partial` → reset → re-run; second Install press ends in seconds; realmd's ready line recorded; client logs in
  - **Substantially done 2026-09-02 on `m910q`; deliberately NOT ticked.** `conf`, `start-db`,
    `import`, `up` and `ready` all completed and `WoW TBC is installed and running` was printed;
    three containers Up.
  - **realmd's ready line, recorded as the line asks:** `Added realm id 1, name 'MaNGOS'`. Note the
    catalog sets `ready.auth: null` for TBC, so the engine does not wait on the auth log at all —
    this line is evidence, not a marker in use.
  - **Second press: 70s**, from a state where only `up` and `ready` remained.
  - **The three preflight `warn` phases are answered, 2026-09-03 — one flipped, two justified.**
    What that run printed, and what the same code prints now:

    1. `[warn] CPU vs memory: 15 CPUs means 16 parallel compilers at about 2 GB each, and 19.5 GB
       affords about 9. Either raise the memory, or set Docker Desktop to 8 CPUs — the job count
       comes from the CPU count and cannot be set any other way.` **FLIPPED, because it was
       wrong on both halves.** TBC compiles with `make -j2`, fixed in `catalog.json`
       (`cmangos.dockerfile.make_jobs`), so the "16 parallel compilers" it named were nobody's
       build — the CPU count is not one of the two things being compared on any CMaNGOS entry.
       And the remedy named a Docker Desktop pane on a box running Docker Engine, which has no
       such setting. Both fixed on 2026-09-02 (`_build_jobs`, `_cpu_check`, and the rename to
       `compiler jobs vs memory`). The same code on the same box now prints
       `[pass] compiler jobs vs memory: 2 parallel jobs against about 5 the memory affords`.
    2. and 3. `[warn] free space on Docker's disk: 51 GB free; 60 GB is the comfortable figure`
       and the identical row again for the server folder. **JUSTIFIED as a warning, and the
       duplicate is gone.** They were two rows saying one thing because both paths were the same
       drive; the check now emits a single
       `free space on Docker's disk and the server folder` row that says so.

       The band itself (refuse below 40 GB, warn below 60, from `min_data_root_gb` 20 +
       `min_server_dir_gb` 20 and their warn twins) earned its keep the same day, on Tortoise:
       the install warned at **42 GB free**, and a later attempt on the same box **refused at
       33 GB** and could not proceed until about 9 GB was freed. So the warn band is not
       decoration — it is the interval in which an install that has not started yet is likely to
       cross the floor before it finishes, which is exactly what happened.

  - **Still owed:** the interrupted `import` → `partial` → reset → re-run path, which has never
    been exercised on any entry.

  - **CLIENT LOGIN DONE 2026-09-03.** The owner drove a real 2.4.3 client on the Hyper-V host
    against this server over Tailscale (`100.78.24.50`). The evidence is what the SERVER recorded,
    not what appeared on screen:
    - `realmd.account` id **105**, `YULON`, `gmlevel 3`, `active_realm_id 1`, `expansion 1`,
      `failed_logins 0`, and **`length(sessionkey) = 80`** — a session key exists only after a
      completed SRP6 exchange, so this is authentication and not merely a TCP connection.
    - `characters.characters` guid **903**, name `Ggkki`, **account 105**, race 2 / class 1,
      `online = 1` — a character created on that account, in the world.
    - `tbc-mangosd` logging `Avg Diff: 68. Sessions online: 1.`
    - **The account was written by this app**, through `controller_wow_tbc.accounts.sql_for_install()`
      + `create_account()` with the `mangos_srp6` scheme — so this run also proves the CMaNGOS SRP6
      encoding (salt byte-reversed before hashing, verifier stored big-endian, g=7) against a real
      client rather than against our own re-implementation of it. That is 7.9's
      CMaNGOS-family account-creation item, closed by the same act.
    - **One trap worth recording for whoever repeats this.** The 2.4.3 archive is a StormForge
      repack: it carries its own `realmlist.wtf` at the client root saying `logon.stormforge.gg`,
      and a background extraction that finished AFTER the realmlist had been set restored it,
      so the first attempt failed with "unable to connect" against a client that looked configured.
      Both `realmlist.wtf` (root) and `Data/enGB/realmlist.wtf` had to be written, and the locale
      is **enGB**, not enUS. Preflight's "the client's origin ... which is how a repack looks"
      warning was pointing at exactly this.
  - **This gate caught the worst defect of the day.** `ready` gave up after 600s on a boot that
    took **793s** (container start 15:51:15, first `Avg Diff:` 16:04:28) and told the user
    `The server started but never reported ready.` — while the server was healthy and idle. Raised
    to 1800s in `1b88d49d`; the test pins the floor at the measured 793, not at the shipped number.
- [ ] 7.5 WoW Vanilla — data + templates only; full install with the 1.12.1 client incl. a forced vmap retry; the change set contains no Python
  - **Installed and running 2026-09-02 19:17 on `yulon-ubuntu`** (15 cores), against
    `WoW-Client-1.12.1` downloaded to that box and extracted (5.1 GB, `Data/` with 14 MPQs). All
    twelve stages; `WoW Vanilla is installed and running in /home/pk/vanilla-server`.
    The twelve, in the order the log records them: `clone-sources`, `db-password`,
    `write-dockerfile`, `generate-compose`, `build`, `extract`, `mmaps`, `conf`, `start-db`,
    `import`, `up`, `ready`. **Still running when this was written** — re-checked on the box at
    22:07 the same evening: `vanilla-db` healthy, `vanilla-mangosd` and `vanilla-realmd` both `Up
    3 hours`, so it survived the ~3 h after the installer let go of it rather than only reaching
    `ready` once. The four data counts were read back off the log the same way
    (`dbc: 158 files`, `maps: 2429 files`, `Buildings: 5076 files`, `vmaps: 5667 files`,
    `mmaps: 2008 files`).
  - **Build 2744.3s (45m44s)**; context transfer 441.59 kB in 0.3s — three orders of magnitude
    smaller than TBC's 279.81 MB, because this entry's `.dockerignore` keeps the already-cloned
    sources out and the sources are COPYed rather than shipped in the context. Data counts:
    **dbc 158, maps 2429, Buildings 5076, vmaps 5667, mmaps 2008**.
  - **NOT ticked, on two counts.** The line requires a **forced vmap retry**, which this run never
    triggered — `plan.retry` exists and no tool crashed, so the path is unexercised. And the line
    says **"the change set contains no Python"**: it does not. Reaching a running Vanilla needed
    `make_out_dirs()` in `extract.py` and the HTTP/1.1 line in all three Dockerfile templates.
    That is not a failure of the run; it is the line's premise being wrong, and the premise is what
    7.5 was for — it predicted Vanilla would be data-only and it was not.
    **Both counts re-checked 2026-09-02 and both still hold**, rather than being carried forward on
    the earlier reading: `grep -c -i retry` over `~/vanilla2.log` on `yulon-ubuntu` returns **0**, so
    no retry path was entered at all; and the Python is still there and still needed —
    `make_out_dirs` at `yulon/catalog/families/extract.py:325`, plus the HTTP/1.1 line now in all
    three `native/Dockerfile.tmpl` files (`wow-tbc`, `wow-vanilla`, `wow-tortoise`).
  - **A third thing this run left open — not one of the line's own criteria, which is why it is a
    separate bullet: 171 of the 172 `core updates` SQL files FAILED, and `on_error: warn` printed
    success over them.** Counted off `~/vanilla2.log` on 2026-09-02: 172 files under
    `src/mangos-classic/sql/updates/` were attempted, exactly **one** applied
    (`z2837_01_mangos_gobject_near_link.sql`), and the other **171** died the same way —
    `ERROR 1054 (42S22) at line 1: Unknown column 'required_<the previous update>' in 'db_version'`
    (or `character_db_version`). Each file's first statement renames the column the previous update
    left behind, so a chain that does not start cannot continue: the earliest failure in the run,
    `z2683_01_mangos_scriptdev2_tables.sql`, already could not find
    `required_z2681_01_mangos_mangos_string`, and every one after it is that same miss inherited.
    Because the phase is `on_error: warn`, the engine logged each as `continuing because 'core
    updates' is on_error: warn` and finished with `WoW Vanilla is installed and running`.
    **UNVERIFIED, and it is the whole question:** whether the base dump this entry pins already
    contains those updates — in which case the warnings are noise and the phase is re-running
    applied work — or whether this realm is genuinely 171 updates behind, in which case a `warn` is
    covering a broken world. Nobody has asked the database which, so nobody knows. Worth settling
    before Vanilla is called done, and worth remembering that a run reporting `installed and
    running` is what it looked like from the outside.
  - **It found the second-worst defect of the day.** The build died at cmake configure, before
    compiling anything: CMaNGOS's `FetchContent_MakeAvailable(zlib)` CLONES madler/zlib at configure
    time, and over HTTP/2 that clone fails (`could not read Username for 'https://github.com'` /
    `expected flush after ref listing`). Established by three runs on the same box — `alpine/git`
    worked, `ubuntu:22.04` failed, `ubuntu:22.04` with `-c http.version=HTTP/1.1` worked — and fixed
    in all three templates (`12d7e240`). `git.py` had already made this choice for the clones the
    app itself makes; the build context was the last place still speaking HTTP/2.
- [x] 7.6 WoW Tortoise — data + templates; first-ever extraction from a 7272 client; boot to `Ready to login`; client connects; `status` promoted from `wip`; source pinned
  - **Five defects, 2026-09-03, `yulon-ubuntu`. Not ticked.** Every one is a fact about a binary
    this project did not write, and not one was visible from our own code — the suite was green
    through all five. This is the entry that justifies the gate existing.
    1. **MoveMapGen returns 1 when it SUCCEEDS.** `tools/mmap/src/generator.cpp:352` ends
       `return silent ? 1 : finish("Movemap build is complete!", 1)`; CMaNGOS ends `return 0`. The
       stage hard-coded 0, so a finished run — 58 maps, 2075 tiles, 2.5 GB, about four hours — was
       thrown away as a failure while quoting the tool's own line saying it had just written a
       file. Fixed by `MmapPlan.success_codes` (`9c93ad6e`); re-run reached
       `mmaps: done (mmaps: 2133 files)`.
    2. **The DB auto-updater was pointed one directory too high.** `Database.AutoUpdate.Path` said
       `/opt/tortoise/sql/`; the migrations are at `/opt/tortoise/sql/database_updates/world` (125
       files). `ProcessTargetUpdates` skips a missing directory in SILENCE, so the only symptom was
       the worldserver crash-looping later on `Unknown column 'script_name' in 'SELECT'`. Corrected:
       the updater applied all 125 and wrote 125 rows to `migrations`; `script_name` verified
       present in the database, not inferred from the log.
    3. **The ready marker named three lines this core never prints.** It looked for
       `World initialized|MaNGOS.*started up successfully|Ready to login`. A booted, ticking
       Tortoise worldserver prints none of them; it prints
       `World server is up and running! Loading time: 0 minutes 32 seconds`. Its two siblings are
       no guide — Vanilla and TBC match `Avg Diff:`, and `grep -c` for that over this core's whole
       log returns **0**. Every Tortoise install would have waited out 1800 s and then reported a
       healthy server as never ready.
    4. **The bots it is named for were compiled in and switched off.** `8176a2ec` built playerbots
       into the image; the conf table then materialised `mangosd.conf` and `realmd.conf` and not
       `aiplayerbot.conf`, though the image ships `aiplayerbot.conf.dist` beside them —
       `AI Playerbot is Disabled. No configuration file at /opt/tortoise/etc/aiplayerbot.conf`.
    5. **And their SQL was never imported.** With the conf written, the bots initialise, load 680
       area levels, and die on `Table 'tw_world.ai_playerbot_weightscales' doesn't exist`. Vanilla
       has carried `playerbots characters` / `playerbots world` phases all along (the second
       listing `sql/world/*.sql` AND `sql/world/classic/*.sql`); Tortoise's plan had neither.
    - **The verify is why 2, 4 and 5 all got through.** The only world-side check was
      `COUNT(*) FROM information_schema.tables >= 150`. The database that crash-looped the server
      had **285** tables, so it passed comfortably while missing every table the bots query and 125
      migrations besides. A count answers "did something get imported", never "did the right
      things". Now also counts `ai_playerbot% >= 10`.
    - **STILL OWED, and the reason it is not ticked.** The five fixes have not been proved by ONE
      uninterrupted run. Each was verified where it was found — the mmaps fix on the real path, the
      updater against the `migrations` table and the `script_name` column, the conf by
      `Bot configuration read from /opt/tortoise/etc/aiplayerbot.conf` — but the install that
      currently exists was corrected in place across several resumes, which proves the diagnoses
      and not the fixed catalog. A clean run needs an empty database, and `import` will not re-run
      over the existing one by design (bug-checklist §36). Owner decision pending: drop the four
      `tw_*` schemas on this test server, or install fresh into an empty folder (~90 min: the image
      is cached, extract and mmaps are not).
  - **TICKED 2026-09-03, every item on the line.**
    - *data + templates* — the six fixes above are all `catalog.json`; the only Python this gate
      needed was `MmapPlan.success_codes`, and that is a spine change the other three entries
      inherit rather than a Tortoise special case.
    - *first-ever extraction from a 7272 client* — `data/mmaps` at 2133 files from
      `/home/pk/clients/TurtleWoW`, the client untouched (mounted `:ro`).
    - *boot to ready* — `World server is up and running! Loading time: 28 minutes 38 seconds`,
      `RestartCount=0`, ports 3724 and 8090 listening, all three verify rules green including
      `ai_playerbot% = 12`.
    - *client connects* — the owner logged in from the Hyper-V host with `turtle-wow.exe` and
      played a character. Server-side: `tw_logon.account` id **104** `YULON`, `rank 3`,
      `online = 1`, `current_realm = 1`, `last_ip 172.30.48.1` (the host), `failed_logins 0`,
      `length(sessionkey) = 80`; `tw_char.characters` guid **901** `Kandranya`, account 104,
      `online = 1`, `totaltime 146`. The account was written by this app's own
      `accounts.sql_for_install()` + `create_account()`, and Tortoise's `account` table has a
      DIFFERENT shape from TBC's (`sha_pass_hash`/`rank`/`security`, not `gmlevel`) — so the
      shared account writer is now proven against two CMaNGOS variants, not one.
    - *`status` promoted* — `wip` → `beta`, level with its two siblings.
    - *source pinned* — the core was on a BRANCH while Eluna beside it had a `rev`. Every one of
      the six fixes is a measurement of one commit, so a branch tip was free to invalidate all of
      them. Pinned to `7c0fb278f3f8966422f219e6f5035cb09b76ada7`, the commit that was actually
      built, extracted, migrated, booted and logged into. A test now requires EVERY source of this
      entry to carry a `rev`.
    - **Two notes carried forward, neither blocking this line.** The run that produced it resumed
      over recorded `build`/`extract`/`mmaps` stages rather than starting from an empty folder —
      those three were each driven earlier the same day on the same commit. And an existing
      install cannot receive the two new SQL phases, by the marker's design: bug-checklist §36.
  - **Reached `build` and FAILED there, 2026-09-02 on `m910q`** — the first time this entry has been
    driven at all. Five stages ran in order — `clone-sources`, `db-password`, `write-dockerfile`,
    `generate-compose`, `build` — the build was invoked at 21:44:05 and the run ended at 21:44:49,
    killed at **cmake configure**, 0.585 s into the `cmake ..` step, before one object was compiled:

        Eluna submodule is missing.  Run: git submodule update --init --recursive
        src/modules/Eluna
        -- Configuring incomplete, errors occurred!

    `install failed: the build failed (exit 1)`, and the failing step is `Dockerfile:71`, the
    `cmake .. -DCMAKE_INSTALL_PREFIX=/opt/tortoise … -DBUILD_PLAYERBOTS=ON` line. Log kept at
    `~/tortoise.log` on m910q (104,741 bytes).
  - **What the failure proves is bigger than one broken build: the engine clones a repository and
    nothing fetches that repository's git SUBMODULES.** Established from the clone it produced
    rather than read off the error message. `src/tortoise-wow/.gitmodules` declares
    `[submodule "src/modules/Eluna"]` with `url = https://github.com/ElunaLuaEngine/Eluna.git`; the
    directory `src/modules/Eluna` exists on disk and is **empty** — git creates the mount point and
    stops there. And the argv `yulon.git` logged for the clone carries no `--recurse-submodules` and
    is followed by no `submodule update`: `clone --config core.autocrlf=false --config core.eol=lf
    --config http.version=HTTP/1.1 --depth 1 --branch playerbots-integration-gh
    https://github.com/Shyalya/tortoise-wow.git .`. So the defect is in the clone step and Tortoise
    is only the first of the four entries whose source needs one — nothing about this is
    Tortoise-specific except that it is the entry that exposed it. (A fix was being written
    elsewhere while this was recorded; what is recorded here is the failure and what it establishes,
    not the remedy.)
  - **Preflight on that box is worth reading before the next attempt, because it already warned
    about the thing 7.6 is for.** Nine checks passed and three warned: free space 44 GB against a
    60 GB comfortable figure (twice — Docker's disk and the server folder share the drive), and the
    one that matters, *"the client's origin: realmlist.wtf sits at the root of /home/pk/TurtleWoW
    and there is no locale folder, which is how a repack looks"*, alongside `21 MPQ archives in
    /home/pk/TurtleWoW/Data`. Whether a repack extracts completely is exactly what "first-ever
    extraction from a 7272 client" exists to answer, and this run stopped four stages short of it.
  - **The old Tortoise test server on m910q was removed by owner decision (2026-09-02)** — to free
    the container names, which AzerothCore/CMaNGOS compose stacks pin GLOBALLY, so a second Tortoise
    install cannot stand beside an old one. Three containers and two volumes went. The volumes were
    `tortoise-wow-hunt_dbdata` and `tortoise-wow-server_dbdata`, named in
    `~/tortoise-volumes-before.txt` before the removal and gone afterwards: the box now holds one
    volume (`yulon-wow-tbc-1cbfa4ac_db-data`) and three containers (the exited TBC set), checked
    after the fact. **The 428 MB the removal reclaimed was read at the time and cannot be
    re-measured** now that the volumes are deleted; it is recorded as reported, not as re-verified.
- [ ] 7.7 Native Windows, all four — WotLK first (closes the 6.3 `ac-db-import` blocker), then TBC, Vanilla, Tortoise from `yulon-win11`'s clean checkpoint; 9p extract/mmaps throughput recorded; `platforms` widened per entry
- [ ] 7.8 macOS, all four — **[blocked]** on hardware
- [ ] 7.9 Controllers — `controller_wow_tbc/`, `controller_wow_vanilla/`, `controller_wow_tortoise/` mirroring `controller_wow_wotlk/`; `mysql` → `db.client` in `apply.py`/`maintenance.py`; CMaNGOS-family account creation (was 7.1–7.3 before the scope change; still owed, now after install)
  - **`mysql` → `db.client` DONE 2026-09-03.** `apply.DockerSql` already carried it; `d157001d`
    threaded it into `maintenance.DockerMysql` and all four `mysql_for()` factories, `f8cacafc`
    into all four `accounts.sql_for()` (found by driving a live server, which printed
    `client=None` on the seam it had just built), and `102e2dd1` into
    `ui/controller_view._mysql_for()` — the Server tab's own backup/restore seam — plus
    `install_wiring.py` and `modules.py`. Audited now by
    `tests/test_every_db_seam_binds_its_client.py`, which parses the source and finds every
    `DockerSql(...)`/`DockerMysql(...)` construction anywhere under `yulon/` rather than naming
    the ones somebody remembered; the three misses above are why it is an AST audit and not a list.
  - **CMaNGOS account creation DONE 2026-09-03**, on two of the three entries and by driving the
    real servers, not the tests. TBC: account `YULON` (id 105) created through
    `accounts.sql_for_install()` + `create_account()` on `m910q`, then logged into by the owner's
    2.4.3 client — `sessionkey` present, character `Ggkki` online (see 7.4c for the full chain).
    Tortoise: the same two functions against `yulon-ubuntu`, account id 104, and the seam printed
    `client='mariadb'`, which is the `f8cacafc` fix visible in the field. Vanilla uses the same
    shared `create_account()` and scheme and is UNVERIFIED against a client.
  - **Still owed:** the three packages' remaining feature surface beyond accounts and backup —
    console, modules, networking — measured against `controller_wow_wotlk/`'s, and Vanilla's own
    account/client run.
- [ ] 7.10 Cross-server regression pass — re-run WotLK's 6.5 coverage gate after 7.1–7.9 land to confirm shared layers (`docker.py`, base `Controller`, `runner.py`, `platform.py`, `networking.py`) weren't regressed (was 7.4)
- [ ] **Phase 7 exit criteria met** — all four v1 servers install through one Python engine with zero shell interaction and are managed by the app on Linux and native Windows, and on macOS once a machine exists; no `install-*.sh` remains. **Phase 8 does not start until this is fully met.**

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

- **CI is red on `Yulon` itself, and the break is in a test harness rather than in the product
  (2026-08-25).** *Status when this was written: still red.* The relabel landed at 13:22 UTC and
  `Yulon` had been failing for about six hours; the fix is PR #100, green on its own checks and
  **not yet merged**, so every branch cut from `Yulon` still inherits the failure until it is. `test_the_installers_label_the_server_folder_only_where_selinux_enforces`
  failed on three parametrisations and both Python versions from the moment the SELinux relabel
  merged, so every branch cut from `Yulon` inherited it. The test lifts shell functions out of the
  shipped installer and runs them; it lifted **two** of the **four** that
  `selinux_label_for_containers` calls. An unlifted callee is not absent behaviour - it is
  `command not found`, which exits 127, and `if ! selinux_labels_supported "$1"` reads 127 as
  "this filesystem cannot hold labels", so the function returned having relabelled nothing and
  every positive case asserted `[]`. It merged because the test is
  `skipif(sys.platform.startswith("win"))`: on the Windows dev box it is one of the 20 skips, so a
  green local suite had nothing to say about it. The lesson that outlived the fix is the second
  half - the harness now fails if the probe's stderr contains `command not found`, because bash's
  127 is indistinguishable from a real "no" to every caller in that function, and the next helper
  added would otherwise arrive as another empty list that reads like a decision.

- **The Linux artifact could not start on Ubuntu 22.04 LTS, and no smoke test in CI could have
  found it (2026-08-25).** On a real 22.04.5 box the shipped v0.6.51 tarball dies before drawing
  anything: `libm.so.6: version 'GLIBC_2.38' not found`. It was built on `ubuntu-latest`, which is
  now 24.04 with glibc 2.39, and a glibc binary runs forward but never backward - the builder's
  glibc is the artifact's floor, which excluded 22.04 (2.35), Debian 12 (2.36) and RHEL 9 (2.34).
  Nobody had reported it because every box it had been tried on was newer. The pin is
  `ubuntu-22.04`, and the artifact built there was then confirmed to start on that same 22.04.5
  box. **This pin has a dated expiry:** actions/runner-images#14254 deprecates the image from
  2026-09-17 and retires it 2027-04-17, and every replacement GitHub offers reintroduces the bug,
  so the real answer is a pinned `container: ubuntu:22.04` rather than another runner label.

- **`build/check-bundle-closure.sh` closes the class that produced three separate shipped
  defects.** PyInstaller bundles what the BUILD HOST has, so a library missing from the runner is
  missing from the artifact - and testing the artifact on the machine that built it is blind to
  exactly that, which is why `libxcb-cursor0` (#96) and `libxkbcommon-x11` (v0.6.51, aborting on
  Arch) were both found by users. The gate resolves the bundle's own objects inside a bare
  `debian:bookworm-slim`. Run against real history it named **five** missing sonames on the shipped
  tarball: `libxkbcommon-x11` - the one that was aborting on Arch - plus `libxcb-icccm`,
  `libxcb-keysyms`, `libxcb-shape` and `libxcb-xkb`, four that had never been hit at all and
  survived only because Arch's Xfce happens to ship them. (`libxcb-cursor0` is NOT among them:
  #96 had already added it to the apt step, which is what that fix was. An earlier version of this
  note said "the two known plus" those four, which is six and misdescribes the set.) It then caught
  the glibc floor above, a bigger bug than the one it was written for. **One thing is skipped:**
  Qt's GTK platform theme, because Qt degrades past it - a missing `libqgtk3.so` costs the file
  dialog's GTK look, not the app, and this gate exists to fail a release that cannot RUN. Removing
  the skip was tried and reverted: PyInstaller does bundle the whole GTK stack today (254 objects
  instead of 253, still clean), but nothing in `release.yml` installs GTK, so that is the runner
  image's ambient package set rather than anything this repo declares - and resting a permanent
  rule on it is the same mistake the gate exists to catch.

- **`workflow_dispatch` had never produced an artifact, in a workflow whose own comment says it
  exists "so the matrix can be PROVEN without publishing anything".** `GITHUB_REF_NAME` is the tag
  on a release run but the **branch** on a manual one, and it went straight into the filename;
  every branch here is `fix/...`, `ci/...`, `feat/...`, so packaging wrote into a directory that
  did not exist and all three runners died after building successfully. Slugging the ref fixed it
  and is a no-op on a `v*` tag, since tags carry no slash. This is what made the Windows build of
  a fix branch obtainable at all.

### Privilege transparency: where we stand against the rule (audited 2026-08-24)

Baerthe's binding rule (`roadmap.md`, Phase 6 preamble, commit `7390e885`) is that no install path
adds the user to the `docker` group or writes a passwordless `sudo` rule without explicit informed
consent, and that a `sudoers.d`/`NOPASSWD` docker rule must never be written at all. Audited
immediately after it landed:

- **WotLK (`install-wow-wotlk.sh`, `-ubuntu.sh`, `-fedora.sh`) — compliant.** `docker_group_consent()`
  gates every `usermod -aG docker` in all three, and none writes an `/etc/sudoers.d` NOPASSWD rule,
  with the reasoning recorded in each: membership already *is* root, so the rule was attack surface
  with no benefit. They are three separate lineages on three version numbers, so the removal has
  three dates — `1.2.9` in the SteamOS/pacman script and `1.4.3` in the Debian one, both in their
  changelogs, while the Fedora one never wrote it and says so at the point it would have.
  (This bullet named two of the three scripts and credited "version 1.4.3" for all of them; the
  unnamed one is the script `catalog.json` actually points WotLK at.)
- **TBC, Vanilla and Tortoise — were NOT compliant, fixed 2026-08-24 (`0064b76b`).**
  `install-wow-tbc.sh`, `install-wow-vanilla.sh` and `install-tortoise-wow-wsl.sh` each ran
  `sudo usermod -aG docker "$USER"` with no consent gate and no warning, having been written
  before the rule existed. WotLK's `docker_group_consent()` was ported into all three verbatim
  rather than reworded — it was reviewed once already and says the two things a game-server
  audience will not infer, and three scripts saying it three ways would be three things to keep
  true. All 10 `usermod` call sites across the six scripts are now gated, and
  `test_no_installer_escalates_privileges_without_asking` fails on any non-comment
  `usermod -aG docker` that is not preceded by `docker_group_consent &&`, or on any `sudoers`
  /`NOPASSWD` line at all.
- **The native engine and every other Python path — the audit above was WRONG, and the bug it
  missed was the live one. Fixed 2026-08-24.** "Nothing in `yulon/` joins a group (grepped)" was
  false: the grep looked for the string `usermod -aG docker`, and `platform.py` spells the same
  command as a list — `["usermod", "-aG", "docker", user]`, returned by
  `docker_engine_commands()` and run under `sudo -n` by `_ensure_docker_linux()`. A negative
  audit result deserves more scepticism than a positive one; "we found nothing" is also what a
  wrong query returns. `roadmap.md` 6.4.3 had already prescribed the right technique — assert the
  rule **on the emitted argv through the run seam** — and the audit used a text search instead.

  It was reachable from all three callers of `ensure_docker()`: `main.py --provision`,
  `Installer.preflight()`, and `NativeInstaller`'s own preflight stage. And the ordering made it
  worse than an oversight: `Installer.preflight()` runs `ensure_docker()` **before** the bash
  script starts, so on any passwordless-sudo box — which is both of this project's Linux test
  VMs, and the SteamOS-shaped machine the code defaults its user name to — the launcher joined
  the group first and the script's own consent gate then found the user already a member and
  never asked. **The gate added to the scripts hours earlier could not fire on the machine it was
  written for.** It went unnoticed because every box this has ever run on already had Docker, and
  `ensure_docker()` returns early when a daemon answers.

  **Proven, not argued, in a throwaway `ubuntu:24.04` container on yulon-ubuntu** (a real
  `apt-get`, a real `sudo`, a real `usermod`; the container is the clean Linux box nobody has
  spare): `dad` went from groups `['dad']` to `['dad', 'docker']` with `was_anyone_asked: false`.

  The fix follows the shape the codebase already had for this: `ensure_docker(ask=...)` takes the
  same `runner.Prompter` seam the script path uses, and **with nobody to ask, a privilege change
  is declined** — `make_responder()`'s rule, applied at the layer that actually escalates. The
  argv now exists in exactly one place, inside the consent branch, so there is no second
  construction site a gate could be added to and then forgotten; `docker_engine_commands()` lost
  its `user` parameter so putting it back is a signature change rather than a one-line append.
  Consent is settled **before the first privileged command**, which is what the roadmap asks for
  and also puts the dialog in front of someone who just clicked Install rather than four minutes
  into an `apt-get`. `ProvisionReport.docker_group` records the outcome as one of six values —
  granted / join-failed / declined / not-asked / already-member / not-applicable — because the five
  ways of not joining are different events and must not read as one; it rides `--provision`'s
  support JSON. **`join-failed` arrived late, and its absence was the same defect one layer up**:
  the field carried the CONSENT answer for four commits, so a yes whose `usermod` was then refused
  for want of a sudo ticket was recorded as `granted` — a support JSON claiming a group membership
  the machine does not have. The manual steps had always drawn the distinction; only the
  machine-readable field had not (2026-08-24).

  Three things the fix had to get right that the design pass caught and the first draft did not.
  (1) **A granted join does not complete the install**: `usermod` does not change a running
  process's supplementary groups, so `docker_ready()` stays false for the rest of that run either
  way. The copy says "log out and back in once, then click Install again" instead of implying an
  install that cannot start. (2) **The re-login line is now conditional** — it used to print
  unconditionally, including on the two paths where no group change happened at all. (3) **Under
  `sudo yulon` the dialog would have offered to make `root` a docker user**, because the user
  resolution never consulted `SUDO_USER`; invisible while the join was silent, user-visible the
  moment the name went into a question.

  **Declining the group is not declining Docker.** The engine still installs; what the user keeps
  is the choice about their own machine.

  **Live-gated the same day, in the same container shape as the "before" run**, three users and
  three answers: no prompter → not asked, groups unchanged; "no" → asked once, declined, groups
  unchanged, engine still installed; "yes" → asked once, exactly one
  `sudo -n usermod -aG docker saidyes`, group joined, re-login advice shown. `id -nG` confirmed
  each independently of the app's own report. 751 tests green; **8 mutations, all died** —
  consent defaulting to granted, the argv put back in the engine plan, the join moved outside the
  gate, a dismissed dialog read as yes, the question asked after the fact, membership matched as
  a substring, the command spelled `gpasswd`, and the prompter dropped on the way to
  provisioning. (The mutation run was repeated after a bad splice left two shadowing copies of
  two tests in the file — the first run's evidence described tests that were not the ones being
  graded, so it was thrown away rather than reported.)

  **The GUI half is now gated too, offscreen (2026-08-24).** The chain was verified end to end —
  `catalog_view.py` passes `ask=prompter.ask` into `run()`, which forwards it to `preflight()`,
  which is where `ensure_docker()` is called; that middle hop is the one this change added, and
  without it the prompter reached the script and never the escalation. A test then drives the real
  `InputPrompter` against the real question on a worker thread, finds the modal dialog Qt actually
  opened, reads the text off its labels and answers it. Six mutations die against it: the question
  losing its `(y/n)` (which silently turns the answer into a **password box**, so a user typing
  `y` sees a dot and concludes the launcher wants their password), the dialog never opening, and
  each of the four things the copy has to say — that the group is full root access, the concrete
  thing that lets someone do, what saying yes costs, and what saying no costs. Two earlier
  attempts at those copy mutations SURVIVED and were the useful ones: the first anchored on a
  phrase that also appears in the other branch, the second removed only the first of several
  adjacent string literals, so the phrase stayed in the source. Both were fixed rather than
  reported.

  **Still open, and it is the reason 6.2's box stays unticked:** nobody has seen this on a real
  screen, during a real install, on a machine where the answer matters. Offscreen Qt renders the
  widgets but not the moment — whether the dialog lands before or after the log panel has said
  anything, and whether a user who has just clicked Install understands why they are being asked.
  That needs a fresh non-member user on a box with no Docker, which is why the seam gates used
  containers: `pk` is already in the group on both Linux VMs.

### The bind-mount probe refused every install, on every platform (found and fixed 2026-08-24)

The Windows file-sharing gate — first-gate blocker 4, asking whether Docker Desktop mounts an
unshared folder as EMPTY rather than failing — could not be run as written on `yulon-win11`, and
found something worse on the way.

**Why the gate itself was unrunnable there, which is a result and not a failure.** Docker Desktop
4.87.0 on the WSL2 backend has no per-directory file-sharing list to violate.
`%APPDATA%\Docker\settings-store.json` carries no file-sharing key at all, `locked-directories`
is `{}`, and inside the VM `/proc/mounts` shows **one 9p/drvfs mount for the whole of `C:\`** with
no filter. Measured rather than inferred: `C:\ProgramData` — never in any default share list —
listed 15 entries from a container. Only `C:` is mounted; `D:` and `E:` are not mounted at all.
So that blocker needs a Hyper-V-backend box or a Mac, and stays open.

**The inherited premise is nevertheless correct on Windows**, established against a substitute
with the same observable: `D:\` is a mounted ISO the VM does not map. `docker run -v "D:\:/probe:ro"
… -A /probe` **exited 0 with an empty listing**. An exit-code-only probe would have printed
`[pass]`. The 2026-08-23 correction — compare the container's listing against the host's, mount
the nearest populated ancestor — is vindicated by measurement. One counter-case worth keeping: a
`subst` drive failed LOUDLY instead (exit 125, `mkdir Y:\shared: The system cannot find the path
specified`), so both branches are real and `bind_mount_ok()` handles each.

**The defect.** `bind_mount_ok()` ran
`docker run --rm -v <mount>:/probe:ro <image> ls -A /probe`. The probe image is
`git.CONTAINER_GIT_IMAGE` — deliberately, so the probe pulls the exact digest the clone stages
pull instead of a second unpinned image — and `alpine/git`'s **ENTRYPOINT is `git`**. So it ran
`git ls -A /probe`, which exits 1 with `git: 'ls' is not a git command`, which the function read
as "Docker cannot see that folder", which `preflight` turns into a refusal that
`native.py::_preflight_lines` raises on with no override.

**The native install engine could not install anything, anywhere.** Not Windows-specific: the
image's entrypoint is the image's, and it reproduces identically on Linux —
`docker run --rm -v /tmp/bmprobe:/probe:ro <pinned> ls -A /probe` → exit 1, and the same run with
`--entrypoint ls` → the two files. The refusal also sent a WSL2 user to a Docker Desktop settings
page that does not exist for them.

**Why the tests could not see it.** `test_the_bind_mount_probe_mounts_the_folder_and_tells_no_from
_no_answer` asserted the exact argv **including `"ls"`** — it pinned the broken command — while a
monkeypatched `runner.run` returned a canned `CompletedProcess` that can never learn the image has
an entrypoint. The argv was exactly what its author intended; the defect lives **between** the
argv and the image's metadata, and neither half is wrong in isolation. This is the same shape as
the `start_staged`/`stop_staged` seam defect already recorded above.

It is also easy to see how it survived a reading: `git.py`'s `ContainerGit` uses the *same image*
correctly, building argv that begin `clone` / `fetch` / `status` precisely because the entrypoint
is `git`. Next to that, `ls` looks plausible. Those two are the only `docker run` argv sites in
the package, and the other one is fine.

**Fixed** to `docker run --rm --entrypoint ls -v <mount>:/probe:ro <image> -A /probe`, verified
live on both boxes. On Windows the fixed probe answers True for a shared folder, True for a folder
at the root of `C:` outside any user directory, and **False for `D:\` through the intended branch**
("a container saw D:\ as empty although the host sees files in it"), not the error branch.

**The guard is a live test, because no unit test can hold both halves.**
`tests/integration/test_docker_live.py` gained one that runs the real probe against a real daemon;
it self-skips without Docker like the rest of that suite. Proven RED then GREEN on yulon-ubuntu:
reverting to the shipped argv fails it with `git: 'ls' is not a git command` in the output,
restoring the fix passes. The unit test keeps its argv assertion, now with the reason `--entrypoint`
is load-bearing written next to it.

**What this says about the other first-gate items.** Three of the five have now been run and **all three**
found real defects — the missing `AC_AI_PLAYERBOT_*` values, `images -q`, and this.
The remaining unrun ones are not paperwork.

**Running is not answering, and the tally lives in one place now.** This item — a folder outside
Docker Desktop's file-sharing list — counts as run here, because its probe was exercised live on
both boxes and that is what found the defect above; it counts as still open further down, because
the case it exists to test has not been reachable on any machine this project has. Both are true.
`phase6-decisions.md` keeps the count; the two mentions here defer to it.

### `images_built()` could never have answered yes — blocker 3, confirmed and fixed (2026-08-24)

Third item on the first-gate list: "`docker compose -f… images -q` against a project that has
been built but never started. `images_built()` is documented as a hint precisely because compose
v2 enumerates the images of created CONTAINERS; if it answers empty here, every resume re-runs
the build." Asked, and the answer is the bad one.

**Measured on yulon-ubuntu, Docker 29.1.3 / Compose 2.40.3.** A four-file project shaped like the
engine's own — base, override, never-auto-loaded build overlay — with a two-line busybox image in
place of a 30-minute one, because the question is about compose's behaviour and not about
AzerothCore:

| state | `compose images -q` |
|---|---|
| after `compose -f base -f override -f build build` succeeded, no containers | **nothing**, both bare and with the same `-f` set |
| after `compose create` (containers made, never started) | 2 ids |
| after `compose up -d` | 2 ids |

So the answer turns on containers existing, not on images existing — and "built, no containers
yet" is the entire window a resume asks in. `images_built()` returned False for every finished
build, `_build()` re-ran the compile every time, and the state file's recorded `build` could never
take effect. BuildKit's cache would have made it cheap in wall-clock and it would still have been
wrong: the engine would have reported hours of work it did not need to do, on the stage the whole
resume design exists for.

**The same run confirmed the other half of the `-f` discipline**: a bare `docker compose build` in
that directory exited 0 and built nothing, leaving zero images on the host. That trap is inherited
from `rust-prior-art.md` §2 and had never been executed here either.

**Fixed by asking the daemon instead of asking compose.** `docker.images_built(refs)` now takes
image references and runs `docker image inspect --format {{.Id}}` on each;
`composegen.built_image_refs()` supplies them, since `docker.py` may not know a game's images.
ALL of them must exist, not any — a build that produced three of four is not a build, and skipping
it starts a server missing a binary. The two non-zero exits are told apart rather than merged,
because the difference is hours: `No such image` is an answer (False), and a daemon that will not
talk is not (None). Proven live in the same window that defeated the old question: `compose images
-q` empty, `image inspect` two real `sha256:` ids, and a never-built reference answering
`Error response from daemon: No such image:` — the string the code matches on.

**What is still not proven:** that this holds for a real AzerothCore build. The behaviour under
test is compose's, and a busybox image exercises it exactly, but the engine's four images come
from a multi-stage Dockerfile with `target:` per service and nobody has watched those get built.

### The compose diff against the proven install (2026-08-24) — blocker 2 of the first-gate list

`phase6-decisions.md` asks for this twice ("Diff the generated files against `docker compose
config` on the proven yulon-ubuntu install — already asked for above, still not done") and it is
the second item on "What the first gate must run before this engine is trusted". Run now, and it
found something.

**Method.** `composegen.render()` for a throwaway directory, written out, then `docker compose
config --format json` over both it and `~/wow-server-playerbots` — compose's own resolved view
rather than a text diff of templates — compared service by service on image, container name,
ports, `depends_on`, restart, environment keys, volumes, healthcheck, `stop_grace_period`, `tty`
and `stdin_open`. Read-only; nothing was started and the real install was only read.

**What matched.** All five services, by name (`ac-database`, `ac-db-import`, `ac-authserver`,
`ac-worldserver`, `ac-client-data-init`); every `container_name`; every published port; every
`depends_on` edge; `restart`; the healthcheck's presence; `tty`/`stdin_open` on the worldserver.
The build overlay parses and names `apps/docker/Dockerfile` for all four buildable services and
none for the database. So the shape of the thing is right, which is the part that was never
checked.

**Differences that are the design, not defects.** The image prefix and per-install tag
(`yulon.local/ac-wotlk-worldserver:native-5c09ea72` vs `acore/ac-wotlk-worldserver:master`) —
that is the collision fix. The project name (`yulon-wow-wotlk-5c09ea72` vs the folder basename).
`stop_grace_period: 5m0s`, which the proven install does not have at all and which our own
measurement earned. And `AC_PLAYERBOTS_DATABASE_INFO` on `ac-db-import`, which the repair gate
recorded as missing and the generated file **does** supply — that gap is closed on the native
path and remains open on the script path.

**The defect it found.** `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS` and `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS`
are absent from `DEFAULT_WORLD_ENV`. The proven install carries 1600 and 2000 — the Linux
installer script's numbers as they stood on 2026-08-24, this section's own date, before
[#134](https://github.com/DadsMmoLab/dads-mmo-lab/pull/134) ("Five hundred bots, in the six places
that decide the number") dropped every installer script AND `catalog.json`'s own
`install.native.world_env` to 500/500. **This is a different capture from, and an older number
than, the 2026-08-31 bash-install capture below** (`wotlk-compose-config-script.json`), which
reads 500/500 — the population the scripts and `catalog.json` actually ship today. A native
install would have taken mod-playerbots' own defaults instead. Not a crash — a user on macOS and a
user on Linux quietly getting different worlds from the same button, which is the class of
difference this project rejected named volumes to avoid. Fixed with the proven install's own
values, 1600/2000 at the time — but **not in `DEFAULT_WORLD_ENV`, which is where the sentences
above locate the defect**. An adversarial review pointed out that a per-game number in a module
constant is what style-guide §3 forbids, and that one machine's bot count is no default for every
machine, so the values moved into `catalog.json`'s `install.native.world_env` and a test now
forbids hardcoding either key in `DEFAULT_WORLD_ENV`. What lives in `catalog.json` today is
500/500, after #134 — not the 1600/2000 this paragraph is about.

**Where the numbers come from, stated once.** They are ONE desktop's, copied so that a native
install and a script install agree — never measured for RAM on anything. The first live gate
owes an RSS reading of a worldserver at 2000 random bots; until it has one, the population is
inherited from a `docker compose config` diff and the RAM floors in `catalog.py` are inherited
from the Rust launcher, and neither number is earned.

**Eight other environment differences were deliberately NOT carried over, after checking rather
than assuming.** `AC_CCACHE`, `CTYPE`, `CSCRIPTS`, `DATAPATH`, `USER_CONF_PATH` and the three
empty `AC_RESTARTER_*` appear on the proven install's runtime services because upstream's compose
file sets them for build and run alike. The image's `entrypoint.sh` reads none of them — it uses
`CONF_DIR`, `LOGS_DIR` and `ACORE_COMPONENT`, and the image sets `ACORE_COMPONENT=worldserver`
itself along with `AC_FORCE_CREATE_DB`, `AC_UPDATES_ENABLE_DATABASES`, `AC_DISABLE_INTERACTIVE`
and `AC_CLOSE_IDLE_CONNECTIONS`. Worth stating because `entrypoint.sh` runs under `set -euo
pipefail`, so an *unset* variable it referenced would abort where an empty one would not — the
reason to read the script instead of reasoning about it.

**Recorded, not fixed:** the volume names differ (`db-data` and `client-data` vs `ac-database`
and `ac-client-data`). Both are project-scoped so nothing collides, but a native install's
volumes are not named like a script install's, which matters to anyone reading `docker volume ls`
during support — and to any future path that looks a volume up by name rather than by project.

**What this does not prove.** That the generated stack builds, starts, or serves a client.
`docker compose config` resolves a file; it does not run one. (This sentence listed THREE
remaining first-gate items when it was written, including `images -q` — which the section above
it settled eight minutes later. **Three remain**, per the tally in `phase6-decisions.md`: item 1's
`git status --porcelain` half, a folder outside Docker Desktop's file-sharing list — attempted
since and still open for want of a Hyper-V box or a Mac — and `compose up -d --no-deps <db>`
against images this engine built. The count is kept in one file now, and both mentions here point at it.)

### The first macOS run: Docker was running and the launcher could not see it (2026-08-25)

Baerthe (Discord, the only Mac on the team) ran the 0.6.53 dmg: WotLK Install showed the two
opening lines, then nothing for minutes, then failed. Root cause is in `docker_programs()`, whose
docstring said "off Windows, PATH means the same thing to a running process as to the shell that
started it". True on Linux, false for a `.app` opened from Finder: it is a child of launchd, whose
PATH is `/usr/bin:/bin:/usr/sbin:/sbin`, and Docker Desktop's CLI is a symlink in `/usr/local/bin`.
So plain `docker` raised `FileNotFoundError`, `ensure_docker()` saw `Docker.app` in /Applications,
ran `open -a Docker`, polled `docker info` for the full 180 s against a binary it could never start,
and raised "Docker isn't available and could not be set up automatically". The Windows bug fixed
2026-08-23 (`_windows_docker_programs()`), on a second OS, with the same 180-second signature.

Fixed with `_macos_docker_bins()` / `_macos_docker_programs()` — `/usr/local/bin`,
`/opt/homebrew/bin`, `/Applications/Docker.app/Contents/Resources/bin`, tried only when plain
`docker` does not resolve — which also unblocks `ContainerGit`, since `docker_program()` picks from
the same list. The second half of the report ("no progress details") was real on its own:
`_preflight_lines()` yielded nothing between `OPENING_NOTE` and the end of provisioning, and
provisioning can be a .dmg download plus the poll. It now says "Checking Docker." and, when Docker is
not answering, that setup can take a few minutes with no output. Still unverified on a real Mac: the
run-sheet's remaining steps, and whether `xcode-select -p` on Baerthe's box makes the clone go through
the container git. Ask for `~/Library/Application Support/yulon/yulon.log` on the next run.

### The Mac clone: eight hypotheses, eight refutations, and the two real bugs found on the way (2026-08-27)

One tester on Discord, one Mac, one failure that is still open:

```
containerized git clone … --branch Playerbot https://github.com/mod-playerbots/azerothcore-wotlk.git .
  in /Users/js/wow3 exited 1: Cloning into '.'...
/git/.git: No such file or directory
```

Recorded because the refuted list is the useful part. Every hypothesis below
was plausible, several fit every observation available at the time, and each
was killed by a **run** rather than by an argument — which is the only reason
the list is short enough to write down.

**What was actually wrong, and is fixed.** Two real defects surfaced while
chasing this, neither of them the clone:

1. **The credential helper was not on PATH (#113, merged).** `docker_program()`
   resolved argv[0] out of Docker Desktop's bundle and stopped there. `docker`
   execs `docker-credential-<store>` *by name* through its parent's PATH, and a
   `.app` opened from Finder has launchd's — so every registry pull died at
   authentication, and the bind-mount probe reported that as an unshared
   folder. The user was told to add a folder that was already shared.
2. **The bind-mount probe read `ls`'s exit code instead of its listing
   (#115).** A Mac home directory has entries Docker Desktop cannot stat
   (`.Trash`, `Documents`), so busybox `ls` prints a full listing **and** exits
   non-zero. The chosen folder is empty at preflight time by construction, so
   the probe walks up to the nearest populated ancestor — home — and every
   first install on macOS was refused. Nothing the user could do would pass:
   he re-added the folder to file sharing, added its parent, tried other
   folders, and read a file back out of a container against that exact path.

Two instruments came out of it as well: #114 logs the resolved `docker run`
argv and the destination at INFO, and #117 puts the exit code in
`ContainerGit`'s error the way `_run_git()` always had. Both exist because this
investigation spent three Discord round trips recovering a string the process
already held, and a fourth deciding whether the process had been killed.

**The refuted list, for the clone failure itself.**

| # | Hypothesis | Killed by |
|---|---|---|
| 1 | `--user <uid>:<gid>` breaks the write | his clone WITH the flag, verified by `ls .git` |
| 2 | the folder was under `~/Documents` (TCC) | he had moved off it before the first report |
| 3 | the failed pull was the mount failing | #113 fixed the pull; the clone failure survived |
| 4 | `rmtree` + `mkdir` hands Docker a stale inode | `rm -rf`, recreate, clone — works on his Mac (#116, closed) |
| 5 | the app's argv differs from his | #114 logged it; he ran it verbatim; it cloned |
| 6 | the environment or the docker binary | `env -i` + the bundle's own `docker`; it cloned |
| 7 | the mount is `root:root`, so 501 cannot create `.git` | `touch` **and** `mkdir /git/.git` as `501:20` both exit 0 |
| 8 | recreate + `--user` **together** (the last untested cell) | the app's exact argv, recreated folder, `--user 501:20`: exit 0, `.git` present |

Hypothesis 1 is worth its own line. It was raised on day one, dropped on the
tester's "looks like its working", and returned as #119 five days later with
what looked like hard evidence — a container listing showing the mount as
`root:root`. That evidence was itself wrong: Docker Desktop presents that
ownership and permits the write anyway. **A report of "it worked" that nobody
verified cost five days**, and the fix for that is in how the tests are asked
for, not in the code: every request since has ended "paste the exit line and
the `ls`", and hypothesis 8 died the same hour.

#119 is left open and explicitly labelled not-a-fix. The change it makes is
still right — `ContainerGit`'s docstring says Docker Desktop must not get a
`--user`, and `hasattr(os, "getuid")` is a test for *Windows* wearing the name
of a test for Docker Desktop — but it must be judged as a correctness-of-intent
change and must not be described in a release as fixing the macOS install.

**Where it stands.** The command is exonerated in every dimension reachable
from a Discord thread: argv, destination lifecycle, uid, environment, binary,
and the daemon's view of the mount. The remaining difference is the process
that spawns it — a frozen PyInstaller `.app`, a launchd child, running the
subprocess off the GUI thread with `capture_output=True` and an inherited
stdin. None of that can be bisected without a Mac, and this project has none.
The handoff brief is `pyplan/macos-clone-handoff.md`; the first thing it asks
for is the run-sheet's Step 1, because **nothing in this project has ever run
on a Darwin interpreter**, and a machine that can run the app from source ends
the four-release-round-trip loop this investigation has been stuck in.

### The second macOS run: finding the CLI was only half of it (2026-08-26)

A tester on Discord ran the release on a Mac and preflight refused the install with

> sharing the folder with Docker: a container could not see /Users/j/wow-wotlk, so the server
> files would be invisible to it

He added the folder to Docker Desktop's file sharing, verified it, added its parent too, created
and read a file inside a container against that exact path, and tried several other folders. All
of it worked; the app kept refusing. `~/Library/Application Support/yulon/yulon.log` named the
real failure in one line:

```
INFO  [yulon.platform] docker is not on this process's PATH; found it at
      /Applications/Docker.app/Contents/Resources/bin/docker
WARNING [yulon.docker] the bind-mount probe of /Users/js/Documents failed: Unable to find image
      'alpine/git@sha256:c028...' locally
docker: error getting credentials - err: exec: "docker-credential-desktop": executable file not
      found in $PATH
```

**Root cause: the 2026-08-25 fix above resolved argv[0] and stopped there.** `docker` is not one
program. It execs `docker-credential-<store>` and its `cli-plugins` **by name, through the PATH of
the process that started it** — and that PATH is launchd's `/usr/bin:/bin:/usr/sbin:/sbin`, in
which `/usr/local/bin/docker-credential-desktop` is exactly as invisible as the `docker` symlink
beside it. So every command that had to reach a registry died at authentication. Confirmed by the
tester: launching the same `.app` as `PATH="/usr/local/bin:…" open /Applications/Yulon.app` got
past preflight and into the install.

`docker_program()` now adopts the directory it resolved the CLI from into `os.environ["PATH"]`
(`_adopt_cli_directory()`), which fixes every docker invocation at once rather than threading an
environment through the nine call sites that spell one. Windows gets it too, for the same reason:
`docker-credential-desktop.exe` sits next to the `docker.exe` the registry lookup found.

**Second defect, found while reading that log: `bind_mount_ok()` reported a failed pull as an
unshared folder.** Any non-zero exit from the probe was `False`, and `False` is a hard refusal. The
exit code cannot separate the two — a denied mount and a failed pull are both non-zero — and
matching on error wording would be a list of every message Docker has ever printed. It now asks the
daemon a second question on the failure path: `docker run` pulls before it mounts, so an image that
is not on the daemon proves the mount was never reached, and that is `None` (*unchecked*), never a
refusal. With the image in hand the refusal stands, which is the case the check exists for.

**Still open, from the same tester, once past preflight:** the containerized clone failed with
`Cloning into '.'... /git/.git: No such file or directory`, while the identical `docker run` he
typed by hand succeeded. The one difference we know of is `ContainerGit._user_args()`, which passes
`--user <uid>:<gid>` whenever `os.getuid` exists — and its docstring's premise ("on Docker Desktop
… `os.getuid` does not exist, which is the same condition") is simply false on macOS, so Darwin
gets the Linux branch the author meant to exclude. Not yet fixed: nobody has confirmed that the
flag is what breaks it, and the isolating run (his working command plus `--user $(id -u):$(id -g)`)
is what would.

### Two things the first button-driven install found (2026-08-24)

**1. A first-run failure whose diagnosis did not survive being tested, and a message that sends
the user the wrong way.** `ac-db-import` failed with `Can't connect to MySQL server on
'ac-database:3306' (111)` 0.3 s after compose reported the database healthy. The explanation
written here first — and repeated into `phase6-decisions.md` and the roadmap as a thing 6.2 "must
not inherit" — was that the healthcheck is
`mysql --user=root --password=… --execute "SHOW DATABASES;"` with **no `-h`**, so it goes over the
unix socket, and MySQL 8.4 initialising a brand-new data directory runs a *temporary server*
reachable on that socket and not on TCP.

**That mechanism did not reproduce (yulon-ubuntu, 2026-08-24, 10 runs).** A compose project shaped
exactly like the real one — upstream's healthcheck verbatim, `depends_on: condition:
service_healthy`, and a **second container** connecting to `ac-database:3306` over the compose
network, which is what `ac-db-import` is — connected on the first try in all 5 fresh-volume runs,
and so did all 5 runs with `-h 127.0.0.1 --protocol=TCP`. A first, weaker probe measuring the gap
from *inside* the database container found no gap either (healthy at 17.1-18.4 s, TCP reachable at
or before it, three runs); that one is recorded only because it tested the wrong path and the
right one was then run.

So the honest state is: **one failure, one inferred cause, and ten attempts that failed to
reproduce it.** What the runs cannot rule out is the condition the original had and an idle box
does not — the real one had just finished a 30-minute compile, so the database was initialising
under heavy I/O, which is exactly when a long init phase would widen any such window. The claim
is downgraded from mechanism to hypothesis; nobody should build on it, and nobody should treat
the single failure as explained.

**What changed anyway, and on what grounds.** The generated template's healthcheck now carries
`-h ac-database --protocol=TCP`. Not as a fix for the above — it is not established that there is
anything to fix — but because health should assert the thing its waiters need: every consumer of
`service_healthy` here reaches the database over TCP from another container, and the socket probe
proves something else. The 10 runs establish the only claim made for it: it goes healthy in the
same time as upstream's spelling, so it is a strictly stronger condition at no measured cost.
Pinned by a test, because it is one word in a string in a template and its absence is invisible
until a first-ever install.

**Then the spelling changed once more, and the measurement did not follow it.** What shipped first
was `-h 127.0.0.1` — the spelling the 5 non-upstream runs above used. An adversarial review
(Codex, 2026-08-24) pointed out that loopback INSIDE the container is not the interface any
consumer arrives on either, so the probe still did not establish what the paragraph above claims
for it; it held only because MySQL's default bind is `*`, which is "the claim happens to be true",
not "the probe proves it". Conceded, and changed to `-h ac-database`, verified once against a
probe project carrying that exact `test:` with a second container that connected first try.
**So the 17.1-18.2 s timing above is measured of the loopback spelling and inherited by the shipped
one**, on the argument that neither changes when the server starts listening. Nobody has re-run
the ten.

**One coupling the round turned up and nobody wrote down.** A review seat predicted this probe
could never authenticate at all: nothing sets `MYSQL_ROOT_HOST`, and MySQL treats `'localhost'`
as meaning the unix socket specifically, so a TCP login as `root` would be denied. That
contradicted ten live runs, so it was settled by looking rather than by arguing. On a brand-new
volume the official image's entrypoint runs `file_env 'MYSQL_ROOT_HOST' '%'` and then, guarded by
`[ -n "$MYSQL_ROOT_HOST" ] && [ "$MYSQL_ROOT_HOST" != 'localhost' ]`, creates `root@%`; a real TCP
login returns `CURRENT_USER() = root@%`. **Refuted.** The refutation is worth more than the finding
would have been, because it names a tripwire: setting `MYSQL_ROOT_HOST: localhost` on that service
— an entirely plausible hardening edit — deletes `root@%`, and this healthcheck then never passes,
so every waiter hangs forever on a database that is running and perfectly fine. It is recorded in
the template beside the probe, which is where someone about to make that edit is looking.

**Still true and still unfixed:** the installer script's `PIPESTATUS` check reported that failure
as `❌ Compilation failed. Check ~/playerbots-build.log`, which was false — the compile had
succeeded thirty minutes earlier. That message is in a shell script the native engine replaces.

**2. Image tags are global, and nothing in the app protects them.** The build re-pointed
`acore/ac-wotlk-{worldserver,authserver,db-import}:master` at binaries from the new checkout, so the
EXISTING install on that box is now running images it did not ask for. This is the same class of
collision as the pinned container names — `remove_staged()` guards volumes and names, and there is
nothing equivalent for tags. Verified rather than assumed: the original install starts on the new
binaries, reaches ready, and its data is intact (650 accounts, 2901 characters, 18665 playerbot
rows), but it is a de-facto server upgrade nobody asked for and it will have applied any pending DB
updates on that first boot. A second install of the same game on one machine therefore silently
upgrades the first. Worth a decision in `phase6-decisions.md` before 6.2 generates compose files
that build.

> Anything that doesn't cleanly belong to one phase — style-guide amendments, cross-document corrections, tooling gotchas, etc.

- **`pyplan/phase6-decisions.md` (2026-08-21):** why 6.2/6.3 is one shared Python install engine rather than per-platform scripts or a container wrapper, what was rejected and on what evidence, and the finding that **SOAP cannot create the first account** — so 6.5 item 3's option (a) rests on a false premise and SRP6-over-`DockerSql` becomes the primary account path on every platform.
- **Live-machine findings, 2026-08-21 (clean Ubuntu 24.04 VM, Docker 29.1.3)** — three things that only a real
  daemon could show, all now covered by tests that run against one:
  1. **`Controller.stop()` removed the containers.** It ran `docker compose down`, so the next `start()` found nothing
     to start by name and fell back to `compose up -d` — re-running the one-shot `ac-db-import` that `start_staged()`
     exists to prevent. The staged start had therefore *never* run in the launcher's own stop/start cycle. Fixed by
     `docker.stop_staged()` (stop world, auth, db by name); `docker.stop()` stays as the teardown path —
     renamed `remove_staged()` at `30f0b7ff`. The failing assertion before the fix was literally
     `stop() removed the containers`.
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

- **What the three Windows provisioning fixes actually close, and what they do not.** They were 6.3
  prerequisites landed early, not live-defect fixes. When they landed no `catalog.json` entry
  listed `windows` — WotLK was `["linux", "macos"]` and the other three `["linux"]` — so
  `Installer.preflight()` raised `UnsupportedPlatformError` before
  `ensure_docker()` was reached — in BOTH preflights, `Installer`'s and `NativeInstaller`'s, two
  gates rather than one — and the provisioning chain was not reachable through the app. **That
  changed with 6.3 (2026-08-24): WotLK is now `["linux", "macos", "windows"]`, so a Windows
  Install click dispatches to `NativeInstaller` and the clean-box provisioning chain is the live
  6.3 gate that has yet to run.** The other three entries remain `["linux"]`. Live on Windows
  today, and therefore genuinely fixed now:
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

- **Start no longer bootstraps an un-imported project — a repair action was owed (2026-08-22), and landed 2026-08-23.**
  `start_staged()` names the three long-running services, so `compose` can never select
  `ac-db-import`. That is the point, and it holds in every case the old code got wrong. The honest
  consequence: if an install was interrupted *after* the containers were created but *before* the import
  finished, pressing Start brings the servers up against an unimported database and they fail. The old
  code would have re-run the import there — by accident, via the same fallback that destroyed working
  databases everywhere else. **Needed: an explicit "repair / re-import" action**, deliberately chosen by
  the user with a warning about what it overwrites, rather than a silent side effect of Start. The
  installer remains the only thing that runs the import on a healthy path. **The repair landed
  2026-08-23**: `docker.repair_import()`, `controller_wow_wotlk/repair.py`'s five-state probe, and a
  two-press button on the Server tab that is only visible when the probe says there is something to
  repair. What that cost to get right, and what is still open, is on 6.5's Repair / re-import line.

- **Compose project identity does not survive a moved folder — stop side and start side both fixed (2026-08-22).**
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
    the install's own `.env` when an install finishes — `catalog_view.py`'s `_on_run_finished()`, the one
    production call site — so the project identity stops moving with the folder. Attach deliberately does
    NOT pin (`catalog_view.py:199`): an already-moved install is exactly what that path exists to adopt,
    and pinning its current basename onto containers compose created under the old one would make the
    mismatch permanent. The value is *asked of compose* (`compose config --format json` → `name`) rather than
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
  latching config check are already fixed, these three were not. **All three landed on 2026-08-23** —
  1 and 2 in `30f0b7ff`, 3 measured on a populated realm and pinned at `STOP_GRACE_SECONDS = 300`; the
  evidence for each is on 6.5's server-lifecycle follow-ups line. The findings are kept as written
  because they are why the fixes exist:
  1. **Nothing in the app can remove a container any more.** `docker.stop()` (`compose down`, now
     `remove_staged()`) has no production caller now that Stop keeps containers. Container names are fixed per game in
     `catalog.json`, so install to directory A, press Stop, install the same game to directory B, and
     `compose up` dies with `Conflict. The container name "/ac-database" is already in use` — which worked
     before, because Stop removed them. Same wall for repair: a container wedged in its creation-time
     config survives every Stop/Start. **Needs a deliberate destructive action on the Server tab**
     ("Stop and remove containers") wired to `docker.stop()` — built at `30f0b7ff`.
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
- **Start was broken for all three CMaNGOS games, and only the catalog knew (Discord report, 2026-08-26)** —
  a user with a working WotLK install ("server starting and shutdown without issue, console is running fine")
  tried an existing Tortoise install from the same family of scripts and got
  `docker compose up -d --no-deps tortoise-db tortoise-realmd tortoise-mangosd exited 1: no such service:
  tortoise-db`. Not an attach problem and not specific to that install: **every** Tortoise, TBC and Vanilla
  install fails the same way, ours included, because their compose files name the services `db`/`realmd`/
  `mangosd` and give the *containers* the `<game>-` prefix. `ContainerSpec` had modelled that distinction
  since it was written and `compose_services()` falls back to container names only "for every
  AzerothCore-derived game"; `catalog.json` simply never filled the `services` field in, so the fallback
  applied to games it was never true for. WotLK hid it — `ac-database`/`ac-authserver`/`ac-worldserver` are
  both names at once, so the fallback is right there and only there.

  Two things this says beyond the fix. A data field whose default is right for the one game everyone tests
  is indistinguishable from a field nobody filled in — the live integration fixture had deliberately used
  differing service and container names since 2026-08-22, so the *code* was proven and the *catalog* was
  never checked against the installers it ships. The regression test now reads the compose services straight
  out of each installer script and refuses any catalog service that is really a container name there.

### The compose diff against the proven install, re-run against a real capture (2026-08-31)

7.1's task E.2 committed `pylauncher/tests/data/wotlk-compose-config.json` — `docker compose
config` taken off the live gate's own install after it reached `ready` on a clean Ubuntu box,
with the project `name:` and the absolute install path stripped so the fixture names no machine.
The first run of `support_compose.compare()` and `compare_stack()` against it was not clean, and
what the seven lines said is worth keeping.

**The reference is a NATIVE install, not the script install.** Everything the 2026-08-24 section
above records was measured against `~/wow-server-playerbots`, which upstream's compose built. The
committed fixture is the engine's own output. That single fact settles two open questions.

**The volume names no longer differ.** "Recorded, not fixed" above says `db-data`/`client-data`
against `ac-database`/`ac-client-data`; the capture declares `client-data` and `db-data`, because
the engine rendered them. Every comparison the vocabulary now performs — the fixture test, the
E.3/E.4 gates, 7.2's re-run — is native render against native capture, so there is no rename to
translate: `support_compose.DESIGN_VOLUME_NAMES` is empty, and the staleness guard that reported
this is what said so. The record above still describes the script install and is left standing.
The upside is that volume names are now compared exactly as written, which is stronger than
before: a renamed or relocated store is reported by name.

**`ac-client-data-init` is not on `ac-network`.** It declares no `networks:`, so compose puts it
on the implicit `default` and materialises a second per-project bridge — visible only in the
resolved config, never in the file. Recorded, not fixed: the service only fetches an archive into
a named volume and talks to nothing, and changing the template would have invalidated the byte
snapshot this task exists to compare against. `Service` now carries `networks`, so a service that
falls off `ac-network` is reported instead of being a blind spot.

**The fixture pins the runtime stack, not the build overlay.** A bare `docker compose config`
resolves `docker-compose.yml` plus `docker-compose.override.yml` and never
`docker-compose.build.yml`, which is deliberately not auto-loaded — so the capture has no `build:`
at all and reported `build ... vs None` on all four built services. The comparison drops the
overlay on the rendered side too rather than forgiving a missing `build`, so the two sides are the
same two documents; the overlay stays owned by `test_composegen.py`, by the byte snapshot and by
`test_shape_from_plan_merges_the_three_files`. Anyone capturing for the E.3/E.4 gate must capture
the same way: `docker compose config --format json` in the install directory, no `-f`.

**What still matched, and it is the whole point.** All five services and their container names,
every published port and protocol, every bind and named-volume mount including client data's
`:ro`, every `depends_on` edge with its condition, `restart`, and every environment KEY on every
service.

**Said precisely, because the first draft of this line overstated it.** The comparison reads
environment KEYS, never values — `test_env_values_are_not_compared_which_is_its_cost` shows a bot
population of 0 comparing equal to 500 — so what it established is that
`AC_AI_PLAYERBOT_MIN_RANDOM_BOTS` and `_MAX_` are present on the worldserver, not that they are
500. The 500/500 is readable in the committed fixture and is asserted, as a value, by
`test_composegen.py` against `catalog.json`. Two different claims, and only the weaker one belongs
to this diff.

### The bash script install, captured at last — and what it proved (2026-08-31)

The 2026-08-24 diff above was run against `~/wow-server-playerbots` by hand and never committed;
the E.2 fixture that was, turned out to be a NATIVE install, which cannot exercise a single one of
the design differences the comparison vocabulary forgives. The script install was then found still
standing on the **Fedora** box, left over from the hunt, and captured read-only:
`pylauncher/tests/data/wotlk-compose-config-script.json` (project `wow-server-playerbots`,
upstream's compose file, upstream's published `acore/*:master` images).

**Three differences stand, and all three are pinned in `SCRIPT_INSTALL_DIVERGENCES` rather than
forgiven.**

1. **The script install publishes MySQL on every interface.** `3306` with no host binding, where
   the native stack pins it to `127.0.0.1`. An unauthenticated `root`/`password` MySQL reachable
   from the LAN — the credentials are upstream's fixed pair and are in the compose file. Ours has
   never needed the binding: the launcher's maintenance path uses `docker exec`.
2. **The same for the SOAP admin port, `7878`** — a remote console in front of a GM account.
3. **The native stack mounts the modules tree into `ac-db-import` and the script install does
   not.** Deliberate: the import applies each module's own `db-auth`/`db-characters` SQL, which is
   how the native path has the playerbots schema the script path's repair gate found missing.

**Everything else matched, which is the finding that matters.** All five services and container
names, all `depends_on` edges with their conditions, `restart`, every other mount, both
`build.dockerfile` values and all four `target`s, and every environment key modulo the two
recorded allowances. Upstream's `AC_CCACHE`/`CTYPE`/`CSCRIPTS`/`DATAPATH`/`USER_CONF_PATH` and the
three empty `AC_RESTARTER_*` are present exactly as `BUILD_TIME_ENV` describes them.

**The volume rename is CONFIRMED.** "Recorded, not fixed" (2026-08-24) said `db-data`/`client-data`
against `ac-database`/`ac-client-data` on the strength of a `docker volume ls` reading. The capture
declares those two names, so the record is now evidence. It lives in
`support_compose.SCRIPT_INSTALL_VOLUME_NAMES` and applies to that pairing only — the native fixture
carries no registry, and a `Stack` remembers which one reduced it so the two cannot be crossed.

**`ac-client-data-init` is on `default` upstream too.** The 2026-08-31 note above recorded it as
ours; it is inherited. Not a Yu'lon defect, and the same second bridge network appears in both.

**Four things the capture shows that the comparison still cannot see**, each now with a real
example rather than a hypothetical one: `build.args` (upstream passes `USER_ID`/`GROUP_ID`/
`DOCKER_USER` into the image build; we pass none), `build.context` (absolute in the RAW capture —
the absolute-path surprise that was predicted for `dockerfile`, which is relative on both — but
relativised with the rest of the install path before the fixture was committed, so the committed
file and the test that reads it both say `"."`), the
healthcheck (theirs over the local socket, ours over TCP by service name), and **the SELinux
label**: the script install's capture carries `bind: {selinux: Z}` on exactly ONE mount, the
worldserver's modules tree, and nothing on its other SIX binds — not the six `:z` the shipped
Fedora script actually writes on disk, which the capture's shape does not match. `Z` is
private-to-one-container relabelling and `z` is shared, and the script's own comment already
answers which is right for a tree two services mount (`z`) — which settles it for our engine's
`./modules`, mounted by both `ac-worldserver` and `ac-db-import`. Nothing compares the two
spellings, so this is read from the captures, not asserted by a test. Recorded in
`bug-checklist.md` §17, with what is and is not actually open.
