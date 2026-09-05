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
  - [ ] **The `.dmg` is arm64 only, and nothing said so until 2026-09-03.** `release.yml` builds on
    `macos-latest` with no Intel job, so an Intel Mac gets no artifact at all -- not a broken one,
    none. No line in this file, the README or the bug list mentioned it; found by an inventory pass
    that went looking for macOS items with no checklist entry. The fix exists on the unmerged branch
    `ci/macos-intel-dmg` (an 18-line workflow diff). Merging it or dropping Intel support on purpose
    is an owner decision; either way it stops being invisible.
  - [ ] Packaging: live-gated against the packaged `.AppImage`/`.dmg`/`.exe`, not just `python main.py` from source
    - [x] **Linux `.AppImage` (2026-08-25)** — the packaged artifact launched and driven on clean Fedora 44 (Wayland) and clean Arch + Xfce (X11), both from cold checkpoints. Qt picks the wayland plugin by default on Fedora; forcing `xcb` exercises the bundled libxcb-cursor. **The AppImage still requires FUSE** — `fusermount3` is a setuid helper and no packaging choice removes it, which is why the `.tar.gz` beside it exists; that fallback was confirmed on the FUSE-less Arch box. Two automation notes for whoever repeats this: Fedora's `xdotool` is libei-patched, so on Wayland every synthetic click raises an xdg-desktop-portal "Remote Desktop" consent dialog OVER the launcher — either grant it once or drive the app on a private Xvfb; and `pkill -f` matches the ssh command line carrying the script, killing the shell that ran it.
    - [x] **The SHIPPED artifact could not start on Arch at all (found 2026-08-25, fixed on the same branch)** — `Yulon-v0.6.51Public-x86_64.tar.gz` aborts before drawing anything with "From 6.5.0, xcb-cursor0 or libxcb-cursor0 is needed". **That message is a red herring**: `libxcb-cursor.so.0` IS bundled and #96 works. Qt prints that line whenever the xcb plugin fails to load, for any reason; `QT_DEBUG_PLUGINS=1` gives the real one — `libqxcb.so` cannot resolve `libxkbcommon-x11.so.0`. The bundle carries `libxkbcommon.so.0` and not the `-x11` one; they are different libraries and clean Arch has neither. Same tree, two builds: the CI artifact has one library and aborts, a build on a box with the package has both and starts. It is #96's trap one library over — PyInstaller bundles what the BUILD HOST has — and Fedora hid it because GNOME pulls the package in. `libxkbcommon-x11-0` added to the workflow's apt step. **A bundle nobody launches on a minimal distro will keep producing these one at a time; a CI check that runs the built binary headless, or asserts a soname list, would catch the class rather than the instance.**
    - [ ] **macOS (Baerthe)** — **record the Gatekeeper path for the CI-built `.dmg`.** This line said "the one artifact nobody has ever launched" until 2026-09-03, and that was false when it was written: Baerthe opened the 0.6.53 dmg from Finder on 2026-08-25, and that run is how the launchd-PATH bug in `docker_programs()` was found (Cross-cutting, "The first macOS run"). What is genuinely unrecorded is narrower and is the half that decides a shipping question. Needs no server, so it is the first thing to try. Wanted back: whether Gatekeeper blocks it and exactly what a user has to do about it — an unsigned `.dmg` is a shipping decision, not just a test result
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
  - [ ] Gate: yulon-ubuntu clean checkpoint — **starting state captured** (`docker --version; systemctl is-active docker; id -Gn; ls -d ~/wowserver`, before press 1); press 1: consent dialog + re-login report; re-login; a later press reaches `ready`; kill mid-build, and the resume **recovers the finished objects from the ccache mount** rather than compiling them again; `docker compose config` matches a fixture minted from a DIFFERENT run; auth log `127.0.0.1:8085` read from the authserver container's log, then the realm left advertising an address another machine can REACH — read out of the DATABASE and out of `ready`'s own line in the install transcript, not from `yulon.log` (see §42: the CLI writes none); account + client login from the host after the LAN step
    - **Three clauses were reworded on 2026-09-04, after an audit of a recovered run showed the old wording could not be satisfied by anything that actually happens.** Kept here rather than silently swapped:
      * "**two presses**" → "a later press reaches `ready`". The recovered run took FOUR presses (exit 1, 137, 0, 0) and that is the honest shape of a gate that includes a kill-mid-build: the kill costs a press. Counting presses was never the property worth pinning.
      * "**resume skips the compile**" → the ccache clause. The engine's own `The server is already built; skipping the compile.` fires on a re-press of a COMPLETE install, not on a resume after a kill — a resume re-enters the build step and re-issues every ninja edge, because BuildKit does not cache a partial `RUN`. What it really buys was measured: ~1,315 of 1,829 edges came back from the `--mount=type=cache,target=/ccache` mount in **13.9 s**, and the build finished in **610.7 s** instead of hours. 7.4a's entry made the same correction for its own line on 2026-09-02 ("describing the effect, not the mechanism"); this line had not caught up. Note the mechanism is evictable: a `docker builder prune` between the kill and the resume costs a full compile.
      * "`docker compose config` **matches the fixture**" now says the fixture must come from a different run. `tests/data/wotlk-compose-config.json` was minted FROM the 2026-08-31 capture and committed 2h31m after it, byte-identical at 8,658 bytes — so that run could not have failed its own check. The fixture is doing its job; it just cannot be both the subject and the standard.
      * **The starting-state capture is new**, and is the audit's one-line remedy for a "clean checkpoint" claim that no artifact could support.
    - **"sudo dialog once" was REMOVED from this line, and moved rather than dropped.** `pk` on `yulon-ubuntu` has `NOPASSWD: ALL`, so `SudoSession.verify()` is unreachable there — `_needs_password()` only fires on sudo's own "password is required" after a `sudo -n` step. The clause could never be ticked on the box its own line names. It is already satisfied on Fedora, where the 2026-08-31 record calls it "the first time `SudoSession` has ever been exercised", both other Linux boxes being passwordless. Recorded there; if the owner would rather prove it on Ubuntu too, the E.3 brief's own staging (move `/etc/sudoers.d/pk` aside, re-press) takes minutes.
    - **THE CLEAN RE-RUN HAPPENED ON 2026-09-04 AND THE BOX STILL DOES NOT TICK: nine of the
      thirteen clauses on this line are now earned on artifacts, four are not.** The run answers
      most of what the audit above found missing — the starting state, the account, the client
      login, and a `ready` that is not a continuation of a killed press. Evidence, all committed at
      `4c959d70`: `pyplan/gates/7.1-ubuntu-2026-09-04-clean/gate71-press1.log`,
      `…/gate71-press2.log`, `…/gate71-realm-and-account.log`,
      `pyplan/gates/7.1-client-login/LOGIN-2026-09-04.md` and
      `…/client-connection-20260904-2030.log`. Graded clause by clause, in the order this line
      states them:
      * **"yulon-ubuntu clean checkpoint" — MET, and falsifiable for the first time.**
        `pyplan/gates/7.1-client-login/checkpoint-clean-ssh-as-restored.txt:1-5` names the
        checkpoint (`clean-ssh`, taken 2026-08-28) and records `up 0 min`, `docker: command not
        found`, and no `/home/pk/wowserver`. Press 1's own probes agree from inside the run:
        `gate71-press1.log:30` `docker --version: NOT INSTALLED (no such executable)`, `:31-32`
        `systemctl is-active docker: exit 4 / inactive`, `:34` `id -Gn: | pk adm cdrom sudo dip
        plugdev users lpadmin` — **no docker group** — and `:35-36` `ls -ld /home/pk/wowserver:
        exit 2`. The audit's "unfalsifiable from these files" is answered.
      * **"starting state captured … before press 1" — MET.** `gate71-press1.log:29-40`, five
        read-only probes (the four the line names plus `df -h`), printed before the harness ran.
        Read past the first block: `:1-26` is a first invocation that died on
        `ModuleNotFoundError: No module named 'yulon'` — wrong working directory, `answered group
        x0`, nothing on the box changed (`:16-26` state-after is identical to `:3-14`
        state-before). The real press 1 starts at `:27`.
      * **"press 1: consent dialog" — MET.** `gate71-press1.log:53` `Add 'pk' to the docker group
        (grants root-equivalent access)? (y/n):`, answered `y`, and `:55` `docker group consent for
        pk: granted`. Asked once in the run (`:60` `answered group x1`).
      * **"+ re-login report" — MET, and better than 2026-08-31's.** `gate71-press1.log:56` names
        what provisioning did (`apt-get update; apt-get install -y docker.io docker-compose-v2
        docker-buildx; systemctl enable --now docker; usermod -aG docker pk`), and `:57-59` is the
        refusal: `Docker is installed and set up. It cannot be used from this session yet: your
        account was added to the docker group, and a session that was already open does not pick up
        a new group.` Exit 1 (`:60`).
      * **"re-login" — MET in effect; the act itself is still not in a log.** The pair the
        2026-08-31 set could not produce: `gate71-press1.log:67` state-AFTER press 1 still reads
        `pk adm cdrom sudo dip plugdev users lpadmin` — Docker 29.1.3 installed and active
        (`:62-65`), the group **not** in effect — and `gate71-press2.log:9` state-BEFORE press 2
        reads `docker adm cdrom sudo dip plugdev users lpadmin pk`. Press 2 was run under
        `sg docker -c`, which is the `newgrp docker` the product's own message offers; no line of
        either log spells the `sg`, so the act is inferred and only the effect is captured.
      * **"a later press reaches `ready`" — MET, and it was the very next press.**
        `gate71-press2.log:6044` `Step 9 of 9 (100%): ready`, `:6045` `--- ready`, `:6049` `The
        server is up.`, `:6051` `install of wow-wotlk finished`, `:6053` `exit status 0`. Nine
        stages in the pinned order from `:29` `--- clone-core` (19:31) to `:6045` (20:13), build
        1834 edges from cold, client data 1140 MB in 2m03s (`:5010-5011`). Free disk 74 GB → 53 GB
        (`:14`, `:6065`).
      * **"kill mid-build, and the resume recovers the finished objects from the ccache mount" —
        NOT MET BY THIS RUN.** Nothing was killed on 2026-09-04; press 2 ran straight through. The
        clause is carried only by the 2026-08-31 set — `7.1-ubuntu-2026-08-31/gate-press2.log:4103`
        SIGKILL at edge 1314 of 1829, `:4105` `COMMAND_EXIT_CODE="137"`, then
        `gate-press3.log:1622-1626` (edges 1312-1315 back at `#25 13.69`-`18.65`, real compilation
        resuming at edge 1316 at `#25 28.41`) and `:2174` `#25 DONE 610.7s`. Both runs are
        committed and either can be re-derived; what does not exist is one run that carries every
        clause, and no `ccache -s` capture exists in either.
      * **"`docker compose config` matches a fixture minted from a DIFFERENT run" — NOT MET.** No
        capture was taken on 2026-09-04: `grep -n "compose config"` over both press logs returns
        nothing, and the gate directory holds no `.yml` or diff artifact. This run is exactly the
        different run the clause asks for — the fixture was minted from the 2026-08-31 capture —
        so the missing artifact is one command (`docker compose config` from the server directory,
        **no `-f`**, per the Arch capture trap recorded below) plus
        `test_a_captured_compose_config_matches_the_fixture`.
      * **"auth log `127.0.0.1:8085` … read from the authserver container's log" — MET.**
        `gate71-realm-and-account.log`, closing addendum: `docker logs --tail 3 ac-authserver` →
        `Added realm "AzerothCore" at 127.0.0.1:8085.` The authserver container started at 20:08
        (`gate71-press2.log:6043`, `compose up -d` at 20:08:34), so it read the row AzerothCore
        ships before anything changed it.
      * **"with no `UPDATE`" — the CLAUSE was wrong, and was reworded 2026-09-05 (owner decision).**
        The old wording asked for behaviour the product deliberately dropped. The plan's definition is
        `phase7-plans/7.1-spine-azerothcore-linux.md:6561`: `grep -c "UPDATE"
        ~/.local/share/yulon/yulon.log` → 0 and `SELECT address,port FROM acore_auth.realmlist` →
        `127.0.0.1 8085` at `ready`, the first and only UPDATE coming later from the Networking tab
        (`:6576`). What was measured instead: the install itself issued it. `gate71-press2.log:6050`
        `The realm now advertises 172.30.55.119, so players on other machines can reach this
        server`, and the row read `172.30.55.119 / 172.30.55.119` at 20:21:36
        (`gate71-realm-and-account.log`, TASK 1's first `SELECT`) before any hand edit. That line
        is `catalog/native.py:1990-2001`, the tail of `ready`, and its docstring at `:1955-1968`
        argues the design on purpose (a 2026-09-03 review: the question is whether the row is
        REACHABLE, not whether it equals the LAN address). So this half is not a re-run away — it
        is an owner decision to reword the clause to what the engine does now, and no
        `yulon.log` UPDATE count was captured either way.
      * **The reworded clause was itself unmeasurable for six hours, and the fix is measured here
        (2026-09-05, yulon-ubuntu).** The 05:00 reword asked for `ready`'s UPDATE "counted in
        `yulon.log`". There is no `yulon.log` on a gate box: `~/.local/share/yulon/` does not exist
        on yulon-ubuntu after a full 7.2 run, and the reason is in the code, not the box —
        `configure(config_dir=platform.config_dir())`, the call that opens the rotating file, is made
        only by `main.py` (the GUI). `install_wiring.py`, which is what every headless gate drives,
        never calls it (`grep -c 'configure(' install_wiring.py` -> 0). So the criterion could only
        ever have been met by a run nobody performs. Filed as bug-checklist §42, because a headless
        install leaving no log at all is a defect in its own right, not just a gate problem.
        WHAT WAS MEASURED INSTEAD, and what the clause now asks for:
        `SELECT id,name,address,localAddress,port FROM acore_auth.realmlist` ->
        `1 AzerothCore 172.30.55.119 172.30.55.119 8085` (reachable, not loopback), and `ready`'s own
        line in the transcript, `gate72-press3.log:3377`: "The realm now advertises 172.30.55.119, so
        players on other machines can reach this server". Both halves of the clause are therefore MET
        on the 2026-09-05 run; what remains open in 7.1 is the client login and §39.
      * **What the reworded clause asks for, and why (owner decision, 2026-09-05).** The gate now
        reads: the auth log's `127.0.0.1:8085` line, *then* the realm left advertising an address
        another machine can REACH, with `ready`'s own `UPDATE` counted in `yulon.log`. The two
        options put to the owner were to reword the clause or to revert the engine; he chose the
        reword, on the recommendation that a fresh install should be reachable from another
        machine the moment it finishes rather than localhost-only until the user finds the
        Networking tab. The engine's argument is already written down where the behaviour lives
        (`catalog/native.py:1955-1968`): equality with the LAN address is the WRONG question,
        because `networking.apply()` exists so a user can advertise a PUBLIC address for internet
        play, and every ordinary resume runs `ready` again — comparing against the LAN address
        overwrote that public address and printed 'players on other machines can reach this
        server', which was the opposite of what had happened (review, 2026-09-03).
        **Still owed, and not earned by the reword:** no `yulon.log` UPDATE count has ever been
        captured, under either wording. The clause is met when a run records
        `grep -c 'UPDATE' ~/.local/share/yulon/yulon.log` together with the realmlist row at
        `ready`, and shows the count is exactly the one `ready` issues rather than zero or many.
      * **"account" — MET, through the GUI's own seam.** `gate71-realm-and-account.log`, TASK 2:
        `ControllerServices.for_entry` → `services.create_account('yulon', <password>, 3)` →
        `AccountResult(username='YULON', account_id=101, created=True, gm_level=3)`, the row read
        back by a different route (`SELECT id, username … WHERE username='YULON'` → `101 YULON`),
        `account_access` → `101 3 -1`, and the same call again converging rather than duplicating
        (`created=False`, `COUNT(*)` → 1). Not typed at a console.
      * **"client login" — MET in substance, and the audit's "Absent" is answered.**
        `pyplan/gates/7.1-client-login/client-connection-20260904-2030.log`, quoted at
        `LOGIN-2026-09-04.md:41,:43`: `20:30:22.955 GRUNT: state: LOGIN_STATE_AUTHENTICATED result:
        LOGIN_OK` and `20:30:24.125 ClientConnection Completed: COP_GET_CHARACTERS code=44
        result=TRUE`; server side, `acore_auth.account` `101 YULON last_login 2026-09-04 18:30:22
        online 1 failed_logins 0` (18:30 UTC = 20:30 CEST). Two limits the record states itself:
        `Enter World` was never clicked, so nothing here speaks to the world server beyond a
        character-list reply — the plan's own bar at `:6584` is "logged in **and entered the
        world**" — and the client was on the laptop over Tailscale, not on this line's "the host".
        **Met a second time on 2026-09-05 at 07:31 CEST, against the install 7.2's clean-box gate
        rebuilt** — `pyplan/gates/7.1-client-login/LOGIN-2026-09-05.md`, with
        `client-connection-20260905-0731.log` (`07:31:41.902 ClientConnection Completed:
        COP_AUTHENTICATE code=AUTH_OK result=TRUE`, `07:31:42.568 … COP_GET_CHARACTERS code=44
        result=TRUE`) and `server-side-20260905.txt` (`102 GATELOGIN last_login 2026-09-05 05:31:40
        failed_logins 0`, created through `ControllerServices` at 05:28:33). The route was not
        Tailscale: the `clean-ssh` checkpoint predates it and the restore took it away, so the client
        went through `ssh -L 3724/8085` with the realm at `127.0.0.1` — the loopback realm the owner
        asked to keep possible (§41), exercised for real. The same two limits apply: character list
        only, laptop not host. Until this record existed the 09-05 login was cited from memory alone.
      * **"after the LAN step" — NOT MET, and deliberately.** The launcher's LAN step
        (`svc.network_apply`) was never invoked; the realm row was repointed at the Tailscale
        address by hand (`docker exec … UPDATE acore_auth.realmlist … WHERE id=1`, `rows_changed`
        1), and `sudo ufw status` at the end of that log still reads `Status: inactive`. The reason
        is `bug-checklist.md` §39: the step would run `ufw --force enable` with only 3724 and 8085
        allowed and cut SSH to a box with no console. **This clause is blocked on §39, not on
        machine time** — and §39's own repair is not finished either (see it).
      **So: four clauses stand between this sub-gate and a tick** — a compose-config capture and
      diff, the kill-mid-build/ccache half re-exercised (or the line re-scoped to say it is carried
      by the 2026-08-31 set), the `no UPDATE` clause reworded to the engine's current design, and
      the LAN step, which waits on §39. **And the 7.1 box could not tick even if all four landed**,
      because the Fedora/Arch sub-gate below is open on its own terms: nothing was installed on
      Arch (the AppImage will not launch without `fuse2`), and Fedora still owes the kill-mid-build
      and a run from a cold checkpoint.
      **What the tick would cost was measured while deciding this, on `m910q`, 2026-09-04:**
      `tests/test_docs_pins.py` widens `test_every_test_these_pages_name_by_hand_actually_exists`
      to `phase7-plans/7.1-spine-azerothcore-linux.md` the moment this box reads `- [x] 7.1 `, and
      that page cites **141** test names as live, of which **13** resolve to nothing in
      `pylauncher/tests/`. Same 13 as the 2026-09-02 measurement — the plan's citations have not
      drifted further, and none of them is fixed by a gate run. With the box left open the guard
      stays scoped as it was: **4 passed** against this edit
      (`~/dads-mmo-lab/pylauncher/.venv/bin/python -m pytest tests/test_docs_pins.py -q` on
      `m910q`, run over a copy of these files).
      * **One number in the login record has no artifact behind it, recorded before it gets
        re-cited.** `LOGIN-2026-09-04.md:11` says "500/500 bots loaded". No capture from the 20:13
        install carries a bot count. The 500 that exists is
        `7.1-client-login/server-side-after-attempt.txt:9` (`characters.online=1 count` → 500),
        taken at **14:22** from the earlier attempt of the same day — a different install, account
        `GATE0904`, realm already at `172.30.55.119`, and `0` accounts with a non-null
        `session_key`, i.e. the attempt in which no client ever completed SRP6. That file's auth
        log also reads `Added realm "AzerothCore" at 172.30.55.119:8085`, which is what the
        `127.0.0.1:8085` clause looks like when the authserver starts *after* the advertise.
    - **A WHOLE GATE RUN WAS RECOVERED FROM INSIDE A CHECKPOINT ON 2026-09-04, AND AUDITING IT SAYS
      DO NOT TICK.** `pyplan/gates/7.1-ubuntu-2026-08-31/` — an E3 report headed `Verdict: PASSED`,
      four press logs, a compose-config capture. It had never been in the repo; restoring
      `pre-7.2-gate-2026-09-02` to run this gate is what surfaced it, one command before the restore
      would have destroyed it. Full audit beside it in `AUDIT-2026-09-04.md`; every count there was
      re-derived against the committed files rather than taken from the report.
      **Tally against this line's fifteen clauses: 3 satisfied, 1 unsatisfiable on this box, 11 not
      — of which 5 are contradicted by the logs and 6 have no artifact at all.**
      * **Genuinely earned, and worth keeping:** the consent dialog (`gate-press1.log:15-16`, asked
        exactly once across all four presses); the re-login report (`:17-18`, whose sentence is
        appended only when the group join actually succeeded, so it doubles as proof of that); and
        **kill mid-build** — SIGKILL at ninja edge **1314 of 1829** (`gate-press2.log:4103-4105`,
        exit 137).
      * **`ready` was not press 2.** Four presses ran, not two: exit 1, 137, 0, 0. Press 2 was the
        one that got killed; `ready` first came at `gate-press3.log:3334`.
      * **"resume skips the compile" is mis-worded, and the truth is better.** Press 3 re-entered
        the build and re-issued every one of the 1829 edges from edge 1. What it actually did is
        recover ~1315 of them from the BuildKit ccache mount in **13.9 s** and finish in **610.7 s**
        instead of hours. The engine's own `The server is already built; skipping the compile.`
        occurs exactly ONCE in the whole set, at `gate-press4.log:36` — a re-press of a COMPLETE
        install, not a resume after a kill. Reword the clause to what press 3 proves, and note the
        mechanism is evictable build cache: a `docker builder prune` between kill and resume would
        have cost a full compile.
      * **The compose-fixture clause is circular for this run.** Applying the documented transform
        to `gate-compose-config.yml` reproduces `tests/data/wotlk-compose-config.json`
        **byte-identical, 8658 bytes both**, same install id `243c46e3` — and the fixture was
        committed at `aa50d2ad`, **2h31m after** the capture. The fixture IS this capture. That is
        fine for what the fixture is FOR (it is meant to be a proven install's output); it means
        only that this run cannot also be the run that CHECKS against it. A comparison here could
        not have failed. `compose config` is mentioned in zero press logs.
      * **Six clauses have no artifact at all:** the `127.0.0.1:8085` auth line (zero IPv4 addresses
        in any log; press 3 started the stack detached so no container log was captured), the "no
        `UPDATE`" count (its zero is vacuous — the press logs hold no SQL text, and the same grep
        case-insensitively returns 289 and 315 hits, all `apt-get update` and `DBUpdater.cpp.o`),
        the account, the client login, the LAN step (the realm stayed on loopback, which the report
        itself concedes), and the starting state.
      * **"Clean checkpoint" is unfalsifiable from these files.** The only support is
        `Docker is not answering yet` — a daemon-REACHABILITY probe that reads identically for
        "installed, but `pk` is outside the docker group", which we know independently was the case.
        The strings `clean-ssh` and `checkpoint` appear in no log. One line before press 1 would fix
        this forever: `docker --version; systemctl is-active docker; id -Gn; ls -d ~/wowserver`.
      * **The report marks a criterion PASSED that it did not test.** `gate-e3-report.md:334` lists
        "No sudo password dialog on a passwordless box" under PASSED, for a clause that asks the
        dialog to APPEAR.
    - **The sudo clause needs re-scoping, not re-running, and that is an owner decision.**
      `pk` on `yulon-ubuntu` has `NOPASSWD: ALL`, so `SudoSession.verify()` is unreachable —
      `_needs_password()` only fires on sudo's own "password is required" after a `sudo -n` step.
      Either move the clause to a password-sudo box (`SudoSession`'s docstring names clean Fedora
      and Arch, and Fedora already exercised it on 2026-08-31), or stage this box as the E.3 brief
      actually specified — move `/etc/sudoers.d/pk` aside and re-press — which is minutes of work.
      Leaving it as written means the clause can never be ticked on the box the line names.
    - **7.2 cannot be ticked from this evidence either, and for a reason worth stating.** Its line
      asks for 7.1's gate "re-run from the same checkpoint with no other change" — a reproducibility
      clause, so one run cannot satisfy both lines. Presses 2/3/4 are not re-runs from a checkpoint;
      they are continuations of one accumulating install, with free disk falling 75 → 67 → 53 GB
      across them. One useful consequence: a proper 7.2 re-run would naturally produce the second,
      independent compose capture that removes the circularity above. Sequence it that way.
    - **RUN FROM A GENUINELY CLEAN CHECKPOINT, TWICE — 2026-09-04 (the 7.1 lane) and 2026-09-05
      (the 7.2 re-run, lane gate-71-72) — and clauses 1, 2, 8 and 9 are now on evidence.** Both
      runs restored `yulon-ubuntu` to `clean-ssh` (2026-08-28) and the driver's own before-probe
      read `docker --version: NOT INSTALLED (no such executable)`, `id -Gn` without `docker`, no
      `~/wowserver`, 75-78 GB free — the sentence the 08-31 logs could not produce.
      `pyplan/gates/7.1-ubuntu-2026-09-04-clean/` (press 1 + press 2, `ready` at
      `gate71-press2.log:6045`, 20:13:05) and `pyplan/gates/7.2-ubuntu-2026-09-05/` (press 1, a
      kill at edge 1226/1834, press 3 to `ready`, plus a second kill+resume cycle — its `README.md`
      answers all fifteen clauses in a table). What the second run adds to THIS line:
      * **Clause 9, `docker compose config` against a fixture from a different run: MET.**
        `docker compose config --format json`, in `~/wowserver` with no `-f`, from the 09-04 clean
        install (core `413bea61a`; the fixture's run was `47960183b`), diffed on m910q with BOTH
        `YULON_COMPOSE_CONFIG` and `YULON_COMPOSE_ROOT` set: `test_compose_fixture.py` **59
        passed**, `compare()` 0 service differences, `compare_stack()` 0 stack differences; the
        ROOT-only trap reproduced on purpose reads `SKIPPED [1]`. The 09-05 run's own capture was
        byte-identical (md5 `5ec739cc…`). `7.1-ubuntu-2026-09-04-clean/compose-diff.txt` and
        `COMPOSE-CAPTURE.md`.
      * **Clause 8, the ccache recovery, MET on the same box — but not on the first try, and the
        reason is worth more than the number.** Press 2 was SIGKILLed at edge 1226/1834 (BuildKit
        stage clock 366.1 s; `kill-record.txt`) and press 3 then compiled all 1834 edges from
        nothing, *slower* than press 2 at every edge (`edge-rate.txt`). Three busybox builds
        (`cachemount-diag*.txt`) found why: **a `docker build --no-cache` that names a cache mount
        RESETS it** on this daemon (BuildKit v0.26.2 in docker 29.1.3) — a marker written by a
        normal build is gone after a `--no-cache` build lists the mount — and the lane's ccache
        probe, run with `--no-cache` between the kill and the resume, had emptied the mount the
        clause is about. The kill loses nothing (a SIGKILLed writer kept its 52 MB). So the cycle
        was run again on a throwaway folder with a probe that has no `--no-cache`: SIGKILL at edge
        **605/1834** (210.7 s), `ccache -s` **590 misses / 340 MB**, the resume replayed the first
        590 edges in **~5 s** (edge 500 at 14.2 s against 168.2 s cold, knee at ~590), build
        **1078.1 s** against 1189.7 s cold, `ccache -s` afterwards **590 hits of 2400**.
        `cycle2-edge-rate.txt`, `ccache-stats.txt:63-91`. Add to the caveat above: the mechanism is
        evicted by `docker builder prune` AND by any `--no-cache` build naming `target=/ccache`.
      * **Clauses 3-7 and 13 re-earned on the 09-05 run** (consent asked once, re-login refusal
        verbatim with `id -Gn` unchanged after it, press 2 under `sg docker -c`, `ready` on press
        3, the kill above, account `YULON` id 101 GM 3 through `ControllerServices.create_account`
        with the second call converging). **Not touched by either clean run: 10-12** (the auth log
        was captured — `Added realm "AzerothCore" at 127.0.0.1:8085.` at `docker logs` line 42 —
        but the "no `UPDATE`" reading is the owner's call, as recorded above), **14** (no client on
        the box; the 09-04 run's login is in `pyplan/gates/7.1-client-login/`), and **15** (the
        LAN step is bug-checklist §39 and was not run; the realm row reads `172.30.55.119` because
        the engine's `ready` stage wrote it).
      * **A note on the edge count.** Every earlier record says 1829 ninja edges; core `413bea61a`
        has **1834**. A watcher written for `/1829]` never fired, which is why the first kill
        landed at 1226 rather than ~900. Pin the total from the log, not from a previous run.
  - [ ] Gate: packaged artifact on clean Fedora 44 (SELinux, password sudo, moby-engine + buildx) and clean Arch (pacman + buildx)
    - **ARCH, 2026-09-04: the artifact does not start on a clean Arch box, and the app underneath it
      is fine.** Evidence: `pyplan/gates/7.1-arch/71-arch-appimage.log`. Same artifact as the Fedora
      run — sha256 `cb7c1b7e751da93ffd81569bd8acc671a9f81832296f6509356765074bb1bf1b`, verified on
      the box, so this is not a different build. `yulon-arch`, kernel 7.1.8-arch1-3, **no docker
      installed**, which is what makes it a clean box for this line.
      * **Plain launch is refused before any of our code runs:**
        `Error: No suitable fusermount binary found on the $PATH` / `Cannot mount AppImage, please
        check your FUSE setup.` Neither `fuse2` nor `fuse3` is installed — Arch ships neither by
        default, and an AppImage's own runtime mounts itself with FUSE.
      * **The same artifact with `--appimage-extract-and-run`, which bypasses FUSE, starts**: Qt
        loads (offscreen), and the app reaches its own update check —
        `INFO [yulon.update] update check: current=0.6.59 latest=v0.6.59Public newer=False`.
      * **So the 6.5-era failure class does not recur.** That entry records the shipped tarball
        aborting on Arch because `libqxcb.so` could not resolve `libxkbcommon-x11.so.0`, and notes
        that "a CI check that runs the built binary headless, or asserts a soname list, would catch
        the class rather than the instance". Run headless here, the bundle resolves everything it
        needs. What stops it is one package outside the bundle.
      * **What a user is told, and why it is not enough.** The message says "check your FUSE setup"
        and links a wiki. On Arch the actual remedy is `pacman -S fuse2`. A launcher whose whole
        premise is that a person should not have to know this ought to say the sentence rather than
        link the concept — and it can, because the runtime's failure is detectable.
      * **NOT ticked by this.** The line asks for a packaged artifact installing a server on clean
        Arch, and nothing was installed: the run stops at launch, and the extract-and-run path was
        used to establish that the bundle is sound, not to do a gate. The Arch half needs
        `fuse2` present (or a launcher that says so), then a real install.
    - **Fedora 44 progress, 2026-08-31 — the ENGINE passed; the line stays open because it asks for the packaged ARTIFACT.** Run on `yulon-fedora-gate`, a clean box cloned from `yulon-fedora`'s `clean-desktop` (the owner's own Fedora box could not be restored — it carries his TBC and Tortoise servers with containers running). Driven headlessly over ssh through a pty driver, because the harness declines every question when stdin is not a tty and hangs forever on a pty fed by a heredoc. Press 1: consent once, **sudo password asked once — the first time `SudoSession` has ever been exercised**, both other Linux boxes being passwordless; `dnf -y install moby-engine docker-compose docker-buildx` installed 29.7.2 / 5.5.0 / 0.36.1; group joined; correct re-login refusal; no state file. Press 2: build 1829/1829, client data 1140 MB in 1m58s, **`ac-db-import` exit 0 under SELinux Enforcing** — the 2026-08-25 failure does not recur and this is the first proof of it under the Python engine. Schemas 22 / 111 / 30 / 315, byte-identical to the macOS, Ubuntu and Windows records. `:z` counts 7 + 1 as predicted; `env/dist/etc`, `env/dist/logs`, `modules` and the root all `container_file_t`; `relabel_for_containers()` proven in isolation (`user_home_t` → `container_file_t`, recursive, no sudo) because a successful relabel logs nothing and `:z` on the clone mount would have covered for it either way. Compose diff PASS (57 passed). Re-press on a finished install: **7 s**, compile skipped. Ports: 3306 and 7878 on `127.0.0.1`, 3724/8085 open as clients need. `ls ~/dads-mmo-lab-install-*.log` → 0: zero bash. **The plan's compose-diff command is stale** — it names `YULON_COMPOSE_ROOT`, but E.2 changed the mechanism and BOTH `YULON_COMPOSE_CONFIG` and `YULON_COMPOSE_ROOT` are needed; with only the root set the test SKIPS silently and reads exactly like a pass.
    - **THE APPIMAGE RUN IS DONE, 2026-09-04 — the half this line was actually asking for.**
      `Yulon-yulon-phase7-x86_64.AppImage` from release run 33803191903 (built at `6a8afb07`),
      sha256 `cb7c1b7e75…`, verified identical after the copy to `yulon-fedora-gate`. Fedora 44
      Workstation, **SELinux Enforcing**, docker 29.7.2, buildx 0.36.1. The owner clicked
      Install in the artifact's own GUI on the machine's Wayland session; every prior Fedora
      record came from `gate-driver.py` driving the CLI harness through a pty, which is exactly
      why this line stayed open after the 2026-08-31 engine pass.
      **Result:** `install of wow-wotlk finished` at 22:04:35, 47 minutes after the build
      started — build 21:17→21:54, `ac-db-import` 21:54→22:00, all three containers up, 3724
      and 8085 listening, `WORLD: World Initialized In 1 Minutes 56 Seconds`. Schemas at
      **22 / 111 / 315 / 30** — byte-identical to the Ubuntu, Windows and macOS gates.
      Transcript: `pyplan/gates/7.1-fedora44-appimage.log`.
      **What this run does NOT re-prove, said plainly:** the box was cleaned by removing the
      previous install (5 containers, 2 volumes, 4 built images, the 2.3 GB server dir) rather
      than restored from a cold checkpoint, so Docker was already present and `pk` already in
      the `docker` group. The consent dialog, the group join and the re-login step were not
      exercised again — `pyplan/gates/7.1-fedora44-press1.log` from 2026-08-31 is where those
      live. What is proven here is the packaged artifact driving a real install to a running
      server on an enforcing box.
    - **Still owed on Fedora:** the kill-mid-build interrupt, and a run from a cold
      `clean-desktop` checkpoint that exercises provisioning from the artifact too. (This
      bullet read "the run from the CI-built AppImage rather than the CLI harness, and the
      kill-mid-build interrupt" until 2026-09-04; the first half is the entry above.)
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
  - [x] Gate: busybox/mariadb:11 primitives live — **this box is a duplicate of 7.3's gate below, and was left open beside it while that one was ticked and evidenced.** One gate, one record: `pyplan/gates/7.3-yulon-ubuntu.log` (20 passed / 1 skipped on `yulon-ubuntu`, Docker 29.1.3). Ticked here by pointing at it rather than by running it again; noticed by an audit pass, 2026-09-03.
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
    - **That 2026-09-02 verification went stale two days later, and the clause above with it.**
      Measured 2026-09-05 at `6546b190`, straight out of `catalog.json`:
      `wow-tbc -> ["linux","windows"]`, `wow-vanilla -> ["linux","windows"]`,
      `wow-tortoise -> ["linux"]`. TBC and Vanilla were widened at `2f39a6d9` (2026-09-04), each on
      a Windows install that finished on `yulon-win11-gate` — Vanilla all twelve stages that
      morning, TBC's three containers up that evening — and 7.7 established the order the widening
      has to take: `Install.supports()` is `platform_id in platforms`, so a Windows install refuses
      BEFORE preflight while the list is Linux-only, and the widening cannot follow the run that
      justifies it. Tortoise stays `["linux"]`: never attempted on Windows.
      **So this clause is met in the sense that decided it and not as it is written.** What 7.2 owed
      was *do not empty the list* — the plan's Step 6 template still says `platforms: []`, which is
      the value that would have greyed out three of the four Install buttons — and no entry has
      ever been emptied. What it says, "KEEP `platforms: ["linux"]`", stopped being the value on
      2026-09-04 for two of the three, by a later phase's evidence. The clause is left as written
      rather than edited to match the tree: rewriting a criterion so it passes is the failure this
      checklist exists to prevent. Whoever ticks 7.2 states it in that form.
  - **The line's own clauses, audited 2026-09-05 at `6546b190` — seven of eight met outright, and
    the box is left OPEN on the eighth plus the gate.** Same method as 7.3's tick: read the line's
    claims against the tree rather than run anything new.

    | the line says | what answers it |
    |---|---|
    | six `install-*.sh`, `dml-start.sh`, `wow-manage.sh` gone | **MET.** `git ls-files` matches none of the eight outside `archive/guides/` (which holds four non-catalog installers no entry references). Deleted at `2fddaa0e`, *"chore: delete the six bash installers, dml-start.sh and wow-manage.sh"* |
    | (eight files, 19,451 lines) | **the count is the plan's, not the deletion's.** Measured 2026-09-05 off `2fddaa0e^`: 2603 + 1417 + 2837 + 122 + 2290 + 2104 + 2244 + 8263 = **21,880** lines (`git show --stat` says 21,881; `dml-start.sh` has no trailing newline). 19,451 comes from `7.2-retire-bash.md:567` and is repeated in `phase7-decisions.md:10,174`. Nothing turns on it — it is a size, not a criterion — but it is wrong in three places and should be corrected in all three or in none |
    | `installer.Installer`, `PROMPT_RULES`, `make_responder`, `bash_available` | **MET.** No definition of any of the four survives in `yulon/`; the only occurrences are two past-tense mentions in comments (`catalog/installer.py:286`, `runner.py:221`), which is this repo's own convention. What is left in `installer.py` is the shared surface: `compose_file()`, the error types, the copy, `InstallOptions`, `InstallEngine`, `installer_for()` |
    | script tests | **MET.** `tests/test_installer.py` is the post-7.2 file and says so in its first line — options, errors, copy, dispatch, 15 tests; the `interact()` transport pair runs against a throwaway script through `tests/support_bash.py::bash_available`, which is where F.1 copied the probe when the engine that needed it was deleted |
    | `Install.script*` fields | **MET.** The `Install` model carries `default_server_dir`, `password`, `requires_client_dir`, `platforms`, `native` and nothing else; no entry in `catalog.json` has a key containing `script` |
    | the three CMaNGOS entries KEEP `platforms: ["linux"]` | **NOT as written — see the bullet above.** Never emptied, which is what the clause was for; two of the three read `["linux","windows"]` since `2f39a6d9` |
    | gaming mode → `catalog/installers/steam-deck/setup-gaming-mode.sh` | **MET.** `pylauncher/catalog/installers/steam-deck/setup-gaming-mode.sh` is tracked; that directory otherwise holds only templates |
    | `contribution.md` harness paragraph rewritten | **MET and guarded.** `test_docs_pins.py::test_the_contribution_harness_is_the_engine_not_the_scripts` requires `python -m yulon.install_wiring wow-wotlk` and forbids `python -m yulon.catalog.installer`, `sudo -v`, `bash-script path` and `dml-start.sh` |
    | style-guide §3 rows for `catalog/installer.py` and `catalog/catalog.py` | **MET and guarded.** `…::test_the_style_guide_rows_describe_the_post_7_2_modules`, which also covers the `catalog/native.py` row — added 2026-09-02 after that row went on describing *"the same `run()` contract as `Installer`"* for as long as F.3 had deleted the class |

  - **Why the box is still `- [ ]` after that audit, said plainly so the next reader does not redo
    it.** Two things, neither of them the deletion:
    1. **The `platforms` clause no longer reads true**, and the fix is an owner-facing wording
       change rather than a measurement — see the bullet above. Ticking over it would put a `- [x]`
       next to a sentence a reader can falsify in one `grep` of `catalog.json`.
    2. **The gate box below is open on 7.1's clauses 14 and 15**, which the 2026-09-05 re-run could
       not re-earn (client login needs the owner's laptop; the LAN step is blocked on
       bug-checklist §39), and 7.1's own box is open for the same reasons. The previous reading of
       this line set that condition explicitly — *"the line ticks when those three are settled on
       the 7.1 line"* — and it is not overturned here.

    **What ticking will cost, measured 2026-09-05 on m910q so it is not a surprise later.**
    `test_docs_pins.py::test_every_test_these_pages_name_by_hand_actually_exists` widens to
    `phase7-plans/7.2-retire-bash.md` the moment this box reads `- [x] 7.2 `, and that page cites
    **58** test names as live, of which **19** resolve to nothing in `pylauncher/tests/` — mostly
    names of tests the plan told a task to write or delete. (`7.1-spine-azerothcore-linux.md`: 13
    of 141, unchanged since 2026-09-02. `7.3-cmangos-family.md`: **0 of 219**, its pass done.) With
    this box left open the guard stays scoped as it was: **4 passed** against this edit
    (`~/dads-mmo-lab/pylauncher/.venv/bin/python -m pytest tests/test_docs_pins.py -q` on m910q,
    run over a copy of these files).
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
    - **7.3's primitives gate WAS still owed when this was written, and was run on 2026-09-02** —
      22 passed / 1 skipped, `pyplan/gates/7.3-yulon-ubuntu.log`. The correction filed there stands
      and is what made a fresh run necessary: the 2026-09-01 record under 7.1 could not stand in,
      because `test_sqlplan_live.py` postdates it entirely (21 test functions then, 23 now).
    - **THE LIVE HALF WAS RUN ON 2026-09-05, from `clean-ssh`, with zero bash on the path — and
      the box stays unticked because "7.1's Ubuntu gate" is not itself fully earned.**
      `pyplan/gates/7.2-ubuntu-2026-09-05/README.md`, every clause with a file and a line. The
      short form, so this line can be read without it:
      * **"Full checks green" — MET at this code.** `run-tests-vm.sh --checks` on m910q against
        `lane/gate-71-72` = `2f39a6d9`'s `pylauncher/`: **2341 passed, 4 skipped**, mypy ×3 clean
        (71 files), ruff clean, black 136 unchanged, `ALL GREEN`, exit 0
        (`full-checks-m910q.txt`). The 09-04 Windows record above (2291 passed at `badee625`) stands
        beside it.
      * **"Re-run from the same checkpoint with no other change" — the run itself: MET.** The box
        was restored to `clean-ssh` at 23:57:34 (`state-before-restore.txt` holds what was there
        first; `state-as-restored.txt` reads no docker, no docker group, no `~/wowserver`, 78 GB,
        up 0 min). One change was made before press 1 and is recorded as such: `apt-get install
        python3.12-venv`, because `clean-ssh` cannot make a venv without it
        (`box-preparation.txt`); the 09-04 lane had the same need. Press 1 (consent, Docker
        installed, re-login refusal), press 2 under `sg docker -c` SIGKILLed at edge 1226/1834,
        press 3 to `--- ready` and exit 0 at 01:09:56 — 37 min 37 s from the press, of which the
        build ≈ 22 min, client-data ≈ 4, import 7.2, up-and-ready 4.6. Schemas **22 / 111 / 315 /
        30**, the same four numbers as every other platform; 500/500 bots online; ports as
        designed (`final-state.txt`).
      * **Zero bash, three ways.** No `install-*.sh` / `dml-start.sh` / `wow-manage.sh` exists
        under `$HOME` outside `archive/`; `~/dads-mmo-lab-install-*.log` never appeared; a
        sampler polled every 15 s for two hours — 466 samples, 465 with no lineage-shaped
        process and the one exception a recorder seeing another recorder's argv
        (`zero-bash-sampler.log:490`). The `.sh` processes it did see are the lane's own helpers
        and two inside containers (`docker-entrypoint.sh mysqld`; the client-data init reading
        upstream's `functions.sh` with `sed`). The transcripts' only `.sh` is the AzerothCore
        image's own entrypoint, under two spellings — `/azerothcore/entrypoint.sh` and
        `apps/docker/entrypoint.sh`, two occurrences each (`final-state.txt:377-378`). This said
        one spelling until the 2026-09-05 doc pass.
      * **Its own compose capture**, the "second, independent capture" the 7.1 audit asked this
        run to produce: taken at 01:58:59, byte-identical (md5 `5ec739cc…`) to the 09-04 clean
        run's, which passed the fixture diff (59 passed, 0/0 differences).
      * **Why not ticked.** The clause names 7.1's gate, and 7.1's clauses 10-12 (owner
        decision), 14 (client login, needs the owner's laptop; the 09-04 run has one in
        `7.1-client-login/`) and 15 (LAN step, bug-checklist §39, not run on purpose) are not part
        of what this run could re-earn. Everything a machine on its own could do is done and
        filed; the line ticks when those three are settled on the 7.1 line.
        **Re-checked 2026-09-05 by the doc pass below: unchanged. Clauses 14 and 15 are still
        unrun, 7.1's own box is still `- [ ]`, and nothing in this folder moves either.**
      * **The record was corrected on 2026-09-05, and one of the five corrections matters to a
        clause.** A doc pass over `pyplan/gates/7.2-ubuntu-2026-09-05/` against the box (read-only
        over ssh) found four citations that read as fact and one capture that had gone stale
        underneath them; all five are written up in that README, the fifth in a section of its own.
        The one that touches a clause: **10-12's `127.0.0.1:8085` is a snapshot of a container
        that no longer exists.** `final-state.txt:380` holds it, captured at 01:14:33; that
        `ac-authserver` was removed at 01:23:00 by the cycle-2 `compose down`, and the three
        containers on the box today were created at 01:56:40 by the cycle-2 cleanup
        (`docker inspect -f '{{.Created}}' ac-authserver` -> `2026-09-04T23:56:40Z`). Measured on
        `yulon-ubuntu` 2026-09-05: `docker logs ac-authserver | grep -n 'Added realm'` ->
        `41:Added realm "AzerothCore" at 172.30.55.119:8085.` The two readings do not conflict —
        the authserver reads `acore_auth.realmlist` once at startup, press 3's came up before
        `ready` rewrote the row and the cycle-2 restart read it after — and the row itself is
        `1 AzerothCore 172.30.55.119 172.30.55.119 8085` in both `final-state.txt:58` and
        `final-state-2.txt:38`. **Consequence for whoever settles 10-12:** settle it on the
        reworded criterion (the database row plus `ready`'s own line, `press3.log:3377`), which
        the box still supports, not on the auth-log capture, which it no longer does.
        The other four: every `press1.log` line number in the README's clause table was wrong
        (before-probe `:3-13` not `:3-14`; consent `:27`/`:29`/`:34` not `:29-31,44`; re-login
        report `:31-33` with `state-after: id -Gn` at `:40-41`, not `:34-37,45-51`);
        `cycle2-edge-rate.txt` labelled press A3 and press B as "press 2" and "press 3", because
        `edge-rate.sh` was re-run with its labels untouched (its headings now carry the correction
        and every figure is as printed); `cycle2-kill-record.txt`'s post-kill
        `compiler-processes=1` — flat across all twelve samples while press 2's read 0 — is the
        recorder's own shell, whose argv spells `~/gate72-ccache-stats.txt` and so matches the
        counter's `[c]cache`, and nothing said so; and the entrypoint spelling above.
      * **Two things learned that were not on the sheet**, both in the README: a `--no-cache`
        build naming a cache mount resets it (it cost this run its first ccache measurement), and
        a second install cannot even build on a box that holds another one's containers, because
        AzerothCore pins `container_name` and the engine refuses at the name.
- [x] 7.3 CMaNGOS data model + pure stage kinds — catalog 7.3 models (`Source.rev`, `dockerfile_dir`, `CmangosData`: `ClientSpec`, `DockerfileSpec`, `ExtractPlan`, `MmapPlan`, `ConfPatchTable`, `SqlPlan`); `families/cmangos.py`; `clientdir`/`dockerfile`/`extract`/`conf`/`sqlplan`; `docker.run_container`/`copy_from_image`/`exec_stdin`; all four entries validate; WotLK templates byte-identical; static catalog invariants test
  - **TICKED 2026-09-04, by auditing the parent line's own five claims rather than by running
    anything new.** The box had been left open with nothing written about why, while its only
    sub-gate was already ticked on a real run. Each claim, and the artefact that answers it:

    | the line says | what answers it |
    |---|---|
    | catalog 7.3 models | `catalog.py`: `ClientSpec` :222, `DockerfileSpec` :261, `ExtractPlan` :338, `MmapPlan` :365, `ConfPatchTable` :426, `SqlPlan` :523, `CmangosData` :552, `dockerfile_dir` :611; `Source.rev` in `manifest.py:71`. Validated by six tests in `test_catalog.py` (:622, :639, :663, :677, :703, :716) |
    | `families/cmangos.py` and the five stage kinds | registered at `families/__init__.py:25-27`; dispatch proved end-to-end, enumerated off `catalog.json`, by `test_families_cmangos.py:279` and `test_spine.py:571`; the stage tuple pinned at `test_families_cmangos.py:209,235` |
    | `docker.run_container`/`copy_from_image`/`exec_stdin` | `docker.py` :3025, :3097, :3201 (and `sql_query` :3397), each live-gated by the sub-gate below |
    | all four entries validate | `load_catalog()` is a strict `model_validate` (`catalog.py:1005-1013`); `test_catalog.py:32` pins the four ids, :203 and :731 walk them |
    | WotLK templates byte-identical | `test_composegen.py:991` renders WotLK and asserts `text == expected` against the three committed files in `tests/data/wotlk-rendered/` (`SNAPSHOT_DIR` :966); backed by `test_compose_fixture.py:83` |
    | static catalog invariants test | `test_catalog_invariants.py`, `ENTRIES = list(load_catalog().games)` — all four — across fifteen invariants (:292 through :958) |

    Nothing the line names is missing, and the static suite was green on record at `0e394d9b`
    (1974 passed / 3 skipped, mypy, ruff and black clean).
  - **What kept it open was its own prose, not a missing gate.** The three bullets below were
    written BEFORE the 2026-09-02 run and were never revised when `08e098bc` ticked the sub-box,
    so the tail of this block argued the gate was still owed while its own heading said PASSED.
    They are past-tensed in place rather than deleted, because what they predicted is the reason
    the run happened. Same for the 7.2 bullet above.
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
 The eleven steps are in `pyplan/phase7-plans/7.3-cmangos-family.md`, Task K.8 step 7: hand the box over with `yulon-use.ps1 ubuntu`, announce through `claude-say`, sync the checkout, run the unit suite, pull `busybox:1.36` and `mariadb:11`, run `pytest -m integration tests/integration`, copy the log to `pyplan/gates/7.3-yulon-ubuntu.log` (which did not exist when this was written and does now), record the five numbers here, shut the box down. K.8 landed the code half only — the family registered, the stage tuple pinned, dispatch proved for all three CMaNGOS entries — because this gate starts containers and pulls images and the standing rule is that the owner starts a run himself. Nothing was blocked; it needed the VM powered on and someone to press go, and on 2026-09-02 that happened. **Do not tick from a unit suite alone:** a `SKIPPED` in the integration run means the daemon was never reached, which is the failure a gate this shape exists to catch. The run's one `SKIPPED` is not that failure — `test_wotlk_live.py::test_wotlk_controller_start_ready_stop` skipped on `YULON_WOTLK_SERVER_DIR not set`, an environment-variable skip on a 7.4 fixture, and the 22 passes beside it are the proof the daemon was reached.
    - **This gate's text is duplicated verbatim under 7.1 above, where it is recorded as PASSED on 2026-09-01** (`yulon-ubuntu`, Docker 29.1.3, `yulon-phase7` at `79ea63c`, 20 passed / 1 skipped). **It is one gate written twice, mis-filed** — `79ea63c` is *"Task H.6: the Group H primitives get gates that run them for real"*, which is a 7.3 task recorded under 7.1.
    - **The 7.3 line still needed its own run when this was written, and an earlier draft of this note said otherwise. It got one on 2026-09-02.** It claimed "same suite, same two images, so the substance of the run below already exists". Measured 2026-09-02: `git diff --stat 79ea63c HEAD -- pylauncher/tests/integration/` is `conftest.py +10/-…`, `test_docker_live.py +101/-…`, and **`test_sqlplan_live.py` +160, which did not exist at all**. At `79ea63c` the integration suite held exactly **21** test functions (4 + 16 + 1) — precisely the 20 passed / 1 skipped on that record. Today it holds **23**. The live `sqlplan.apply` / `exec_stdin` + gzip proof against a real `mariadb:11` is two of the six things this gate line names, and it **postdates the recorded run**. So the 2026-09-01 numbers cannot stand for this gate; it was run fresh on 2026-09-02 (22 passed / 1 skipped, against that run's 20 / 1), and the 7.1 copy should be moved here rather than counted twice.
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
- [x] 7.4c WoW TBC conf + import + ready — every `warn` phase justified or flipped; marker written; interrupted import → `partial` → reset → re-run; second Install press ends in seconds; realmd's ready line recorded; client logs in
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

  - **The last thing owed, the interrupted `import` → `partial` → reset → re-run path, was run on
    2026-09-04 on `m910q` and is what ticks this box.** Evidence:
    `pyplan/gates/7.4c-m910q/` — both install logs, the watcher's own log, and the three scripts.

    * **The interruption is real, not carved.** A fresh `wow-tbc` install into `/home/pk/tbc-7.4c`
      ran unattended from 23:25:21, with `watch_74c.py` beside it counting applied SQL steps in
      the log and holding a `SIGKILL`. At **01:11:52** it wrote
      `KILLING pid 826323 after 22 SQL files - this is the interruption`, then
      `killed; process still present: False`. A hand-built half-written database would have
      proved only that the probe can read what we wrote, which is not the question.
    * **22 of 232 steps, and the shape of that matters.** The re-press applied **232** SQL steps;
      the first press had applied **22** (`grep -c ' -> '` on each log). The 22 are not the small
      ones — they include the `Full_DB` load — which is why the wreckage was already substantial:
      `mangos` **184 tables / 259 MB**, `characters` 68 / 2 MB, `realmd` 13, `logs` 3, and **no
      marker row**.
    * **`probe` reads `partial`, asked read-only and through the installer's own gate.**
      `controller_wow_tbc.repair.import_gate()` builds the same `sqlplan.MarkerGate` from the same
      plan, container and generated password that `stage_import()` will build seconds later, so
      this is not a second implementation agreeing with itself. It answered
      `partial — mangos, realmd, characters, logs exist but there is no import marker, so the
      import never finished`. The controller's own translation was recorded in the same breath
      and is `unreadable`, exactly as `controller_wow_tbc/repair.py`'s docstring says it must be:
      the installer may drop on this evidence and a controller may not.
    * **Preflight refused the re-press first, and that is a result, not an obstacle.** The box was
      at **39 GB** free against the entry's 40 GB floor (`min_data_root_gb` 20 + `min_server_dir_gb`
      20, added because both land on one drive). It refused with one row, not two — the merged
      `free space on Docker's disk and the server folder` row 7.4c's warn-phase work introduced.
      Cleared by reclaiming only re-pullable things (`docker builder prune`, 1.137 GB, plus
      `mysql:8.4`, `mariadb:10.6` and `ubuntu:22.04` — pulls, not builds); nothing built and
      nothing anybody's. At **42 GB** the same row reads `[warn] 42 GB free; 60 GB is the
      comfortable figure`, which is the band 7.4c already justified.
    * **The re-press drove the whole chain by itself**, in `stage_import()`'s own words:
      `The databases read as partial: …` → `Clearing the half-written databases first (…)` →
      four `dropping <name>: it was left half-written by an interrupted import` warnings →
      `Cleared mangos, realmd, characters, logs.` → `Importing 232 SQL steps over 12 phases.`
      → `The databases are imported and marked complete.`
    * **It finished: `WoW TBC is installed and running`, `INSTALL RETURNED CLEANLY`, 01:38:48.**
      Whole re-press **13m40s**; the rest after the import is the world server loading.
      **The import figure is a BOUND, not a measurement, and the first draft of this line got that
      wrong.** It said "65 s (01:25:08 → 01:26:13)", which summed a start time narrated into
      `claude-activity.log` when the run was launched with a completion time stamped by the
      install log itself — two different clocks, one of them uninstrumented. The install log
      stamps only its logger lines, never the per-step `X -> Y` stdout lines, so what it can
      actually support is `01:25:13` (the last `dropping <db>` warning, immediately before
      `Importing 232 SQL steps`) to `01:26:13` (`verified mangos: … item_template = 30396`):
      **drop + 232-step import + verify inside 60 s**, ±1 s for second-resolution stamps. Corrected
      by a review that grepped the committed logs instead of trusting the prose.
    * **The marker names this run and no other.** `mangos.yulon_install` holds
      `plan_hash 7936812f10440345`, `finished_unix 1788477973` = **2026-09-03 23:26:13 UTC** =
      01:26:13 local — the re-press's own import minute. There was no marker before it.
    * **The data is new, not the survivor of the drop.** `mangos` went **184 → 197 tables** and
      **259 → 426 MB**, `characters` 68 → 82. Content on the finished server: **18,799 creature
      templates, 6,599 quests, 30,396 items, 14,215 gameobjects**. Every one of those rows was
      written after a `DROP DATABASE`, which is the point.
    * **Which of those numbers a reader can re-derive, and which they cannot.** A review checked
      every figure on this line against the committed logs, and the honest split is:
      - **In the evidence.** The 22 and the 232 (`grep -cE '^\S+.* -> \w+\s*$'` over each log),
        the kill line, the 12 phases, `item_template = 30396` and the 12 `ai_playerbot%` tables
        (`tbc-74c-repress.log:1574-1575`), the 42 GB warn row (`:7`), and the 60 s bracket above.
      - **Captured afterwards, on 2026-09-04, into `pyplan/gates/7.4c-m910q/74c-db-after.txt`**:
        the finished server's table and size counts, the four content counts, and the marker row
        `7936812f10440345 / 1788477973 / 2026-09-03 23:26:13`. These were read live off the
        server when the line was first written and were in no artifact at all; the file exists
        because a review said so.
      - **NOT recoverable, and stated as such rather than quietly kept.** The BEFORE half —
        `mangos` at 184 tables / 259 MB, `characters` at 68 — was read from databases that this
        very run then dropped, and no probe output was captured at the time. The same goes for
        `probe_74c.py`'s stdout and the preflight refusal at 39 GB: both were read off a terminal.
        They are reported here as measurements taken, not as artifacts a reader can open. The
        `partial` reading itself is independently in the evidence, because `stage_import()`
        printed its own copy of it into `tbc-74c-repress.log:59`.
      - **The lesson, since it will recur:** a number read off a live server during a gate is
        gone the moment the gate changes that server. Capture it into a file in the same breath
        as reading it.
    * `realmd` again logged **`Added realm id 1, name 'MaNGOS'`** and `tbc-mangosd` reached
      **`CMANGOS: World initialized`**; all three containers up.
  - **What the earlier note said was still owed** — the same chain, which until this run had never
    been exercised on a CMaNGOS entry. (This read "on any entry" until 2026-09-04 and was
    over-broad: line 213 of this file records the identical chain live-gated on WotLK on
    2026-08-23 — `ac-db-import` killed 19 s in, probe reads `partial`, `acore_world` dropped
    and re-imported in 195 s, back at 316 tables, 10/10 checks. What was unproven was the
    CMaNGOS family's own `MarkerGate`, a different implementation of the same five branches, and
    that is what the 2026-09-04 run above exercised.)

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
- [x] 7.5 WoW Vanilla — data + templates only; full install with the 1.12.1 client incl. a forced vmap retry (**forced through the injected `Seams.run_container`, which is what "forced" has to mean**: bug §37 established the natural crash is not reproducible, and the harness override the plan imagined never existed. No production change is needed — the seam is already injectable, so a gate harness returns 139 for the first `vmap extract` and delegates afterwards); the change set contains no Python (**already false, and recorded rather than quietly dropped**: `make_out_dirs`, the HTTP/1.1 line in three templates, and since 2026-09-02 five more commits including `assert_update_level`. The honest claim is that no Vanilla-SPECIFIC Python was needed; every line it did take is spine-level and shared by all four games)
  - **TICKED 2026-09-04, on the second of the two counts that held it open.** The forced vmap
    retry now fires AND completes, landing on 5,076 / 5,667 / 2,008 — the shipped counts to the
    file (above). The other count was never work owed: the line predicted "the change set contains
    no Python", the prediction was falsified before this session started, and the line has carried
    its own correction inline ever since — no Vanilla-SPECIFIC Python was needed, and every line it
    did take is spine-level and shared by all four games. A phase box does not stay open because a
    prediction it existed to test turned out false; that is the box doing its job. What the
    prediction cost is recorded, not erased.
  - **Two things this line does NOT claim, said plainly so nobody reads them in.** No natural
    extractor crash has ever been produced on this client, so every retry evidence here is an
    injected status at a seam — read `force-vmap-retry.py`'s docstring, which is explicit about
    what is and is not faked. And the `leaving it alone` branch of the retry, for a tool that
    finished before the crash, has no live run behind it: the harness hard-codes the crash onto the
    extractor, so only the empty-and-re-run branch has been exercised outside the unit tests.
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
  - **The forced vmap retry, attempted 2026-09-03, and what it found instead.** The line asks for
    one. It could not be produced, and the attempt was worth more than the tick — see
    bug-checklist §37.
    - `ulimit_stack_unlimited` is documented as existing because the vanilla vmap extractor
      "overflows the default stack on some maps and segfaults", so dropping it should reproduce
      the crash the recipe matches. It does not, measured three ways against the real client:
      flag off, `stack=1048576`, and `stack=65536` all completed (Buildings 5076, vmaps 5667 —
      the shipped counts). The flag's own justification is unreproducible here.
    - Chasing that turned up why it would not have mattered: **the recipe could not fire on a real
      crash anyway.** `Segmentation fault (core dumped)` is a SHELL's job-control message and
      these tools are PID 1 with no shell, so a crashed tool prints nothing — every signal-killed
      container probed returned zero bytes. `RetrySpec.when_returncode_in` now carries
      `[139, 134]` and is checked before the text.
    - **That kept the line open at the time**: the recipe was reachable and unit-tested and had
      still never fired outside a test. It has since fired twice against real containers — once on
      2026-09-04 before the fix, where it could not recover, and once after, where it ran to a
      working server. Neither was a NATURAL crash, and §37's point stands: nobody has made a
      CMaNGOS extractor segfault on this client, and nobody should expect to.
  - **THE FORCED RETRY WAS RUN 2026-09-04 on `m910q`, it FIRED, and then it could not succeed.**
    Evidence: `pyplan/gates/7.5-m910q/vmap75-full.log` (7,876 lines), driven by
    `pyplan/gates/force-vmap-retry.py` against the real engine, real containers and the real
    1.12.1 client. A full `wow-vanilla` install ran from an empty folder; the harness replaced
    only the STATUS of the first `vmap extract`, with 139 and an empty tail, which is exactly
    what `docker.run_container` hands back for a signal-killed PID 1.

    **The recipe is reachable, which is what the line asked for.** The transcript, in order (five
    lines; this said "four" until a review counted them too):

    ```
    shipped recipe: statuses=(139,) tools=('vmap extract', 'vmap assemble')
    vmap extract: running /opt/mangos/bin/tools/vmap_extractor -d /client/Data
    [harness] reporting 139 for /opt/mangos/bin/tools/vmap_extractor
    vmap extract crashed the way the retry recipe expects; running vmap extract, vmap assemble again once
    vmap extract: retrying /opt/mangos/bin/tools/vmap_extractor -d /client/Data
    ```

    `when_returncode_in` works: a status with no log text behind it was matched, which is the
    whole of what bug §37 added it for, and no run had ever put it to the question.

    **And then the retry died on its first breath:**

    ```
    Your output directory seems to be polluted, please use an empty directory!
    install failed: vmap extract failed (exit 1), and that was already the one retry the
    plan's recipe asks for.
    ```

    **`vmap_extractor` refuses to start unless its output directory is EMPTY, and nothing empties
    it between the two attempts.** `make_out_dirs()` creates the folders a tool writes into and
    has never removed anything — its docstring is explicit that "creating a folder cannot make a
    tool look finished", which was the right property for the bug it was written for and is the
    wrong one here. `run_mmaps` is the contrast: it WIPES `mmaps/` before it runs, which is
    precisely the step `vmap extract` lacks.

    **So the one retry the recipe exists for cannot survive the crash it names.** At the moment
    of the refusal `data/Buildings` held **5,076 files** — the shipped Vanilla count — and the
    log shows the tool writing them one at a time, thousands of `Extracting World\wmo\…` lines.
    So a crash at any point after the first file should leave a directory the tool calls polluted,
    which would mean the only crash this recipe could recover from is one that happened before the
    tool wrote anything. **That last step is an inference, not a measurement**, and it is the one
    sentence on this line that was not observed: the run only ever produced TOTAL pollution. It
    rests on the tool's own wording — it asks for an empty directory, not a complete one — and on
    the log showing the files written one at a time. It would be settled by deleting half of
    `Buildings/` and re-running the tool, which nobody has done.

    **Stated against itself, because the harness is not a real crash.** The injected 139 arrived
    AFTER the first attempt had finished its work, so the pollution observed here is total where
    a real crash's would be partial. That difference does not rescue the recipe: the tool's own
    sentence asks for an empty directory, not a complete one. What has NOT been observed is a
    real mid-extract crash, and nobody has made a CMaNGOS extractor segfault on this client — §37
    says nobody should expect to.

    **The fix is not obvious and is deliberately not applied here.** Clearing the re-run tools'
    output before a retry is what the tool's message asks for, and it is also a change that
    DELETES a user's extracted data on a path that fires automatically. The recipe re-runs
    `vmap extract` AND `vmap assemble`, so a crash in the assembler would clear a perfectly good
    `Buildings/` and spend the whole extraction again. That trade wants the owner's eye rather
    than an unattended commit.

  - **The client this ran against is flagged by our own preflight**, recorded because it bears on
    every count above: `[warn] the client's origin: realmlist.wtf sits at the root of
    /home/pk/clients/WoW-Client-1.12.1 and there is no locale folder, which is how a repack
    looks`. The extraction still produced the shipped counts, so nothing came up short.

  - **THE RETRY COMPLETED 2026-09-04, after the fix, and that closes the first of the two counts.**
    `pyplan/gates/7.5-m910q/vmap75-postfix-retry-completed.log`, a fresh install into
    `/home/pk/vanilla-75b`. The whole chain, in the engine's own words:

    ```
    vmap extract crashed the way the retry recipe expects; running vmap extract, vmap assemble again once
    vmap extract: emptying Buildings before the retry, so it regenerates what the crashed attempt left rather than adding to it
    vmap extract: retrying /opt/mangos/bin/tools/vmap_extractor -d /client/Data
    vmap extract: done (Buildings: 5076 files)
    vmap assemble: emptying vmaps before the retry, ...
    vmap assemble: done (vmaps: 5667 files)
    mmaps: done (mmaps: 2008 files)
    WoW Vanilla is installed and running in /home/pk/vanilla-75b
    ```

    **5076 / 5667 / 2008 — the shipped counts, to the file.** The retry does not merely survive now,
    it produces the same data a clean run does.
    **The extractor was emptied and re-run rather than skipped, and that is the designed answer:**
    the retry branch is taken before `_conclude()`, so a crashed tool has no record, is not
    `satisfied`, and is redone. The `leaving it alone` branch — for a tool that finished earlier in
    the outer loop, which is the ASSEMBLER-crash case — is still unexercised by any live run, and
    that is written down rather than glossed: this harness hard-codes the crash onto the extractor.
  - **NOT ticked on one remaining count, and it is the line's own premise rather than missing work.**
    The line says **"the change set contains no Python"**: it does not. Reaching a running Vanilla needed
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
    **SETTLED 2026-09-03 by asking the database, and the answer is the harmless one: the warnings
    are noise.** Every schema is at the NEWEST update the checkout ships, so nothing is missing:

    | schema | update files | `*_db_version` column on the live server |
    |---|---|---|
    | `mangos` | 137, newest `z2837_01_mangos_gobject_near_link` | `required_z2837_01_mangos_gobject_near_link` |
    | `characters` | 27, newest `z2819_01_characters_item_instance_text_id_fix` | `required_z2819_01_characters_item_instance_text_id_fix` |
    | `realmd` | 10, newest `z2820_01_realmd_joindate_datetime` | `required_z2820_01_realmd_joindate_datetime` |
    | `logs` | 1, `z2778_01_logs_anticheat` | `required_z2778_01_logs_anticheat` |

    The base dump announces itself as `Classic DB version 1.12.1 "Melting Pot v2". For Classic core
    z2815` and already carried every update but the last, which is why exactly ONE file applied
    (`z2837`, the only one newer than the dump) and the other 171 could not: each update's first
    statement RENAMES the previous update's `required_*` column, and the dump had already renamed
    past all of them. The chain did not fail to start — it was already finished. World content
    agrees: 10,384 creature templates, 4,245 quests, 17,718 items, 10,744 gameobjects.

    **What is NOT settled is how anybody was supposed to know that from the run.** `on_error: warn`
    printed 171 `ERROR 1054` lines and then `WoW Vanilla is installed and running`, and the only
    instrument that separates "already applied" from "171 updates behind" is a query nobody runs.
    Both readings produce the identical transcript. The phase either has to skip updates the
    `*_db_version` column says are already in, or the verify step has to assert that column against
    the newest file in `sql/updates/<schema>/` — which is a rule this catalog can express and does
    not. Not a defect in this install; a defect in what the install can tell you about itself.
  - **It found the second-worst defect of the day.** The build died at cmake configure, before
    compiling anything: CMaNGOS's `FetchContent_MakeAvailable(zlib)` CLONES madler/zlib at configure
    time, and over HTTP/2 that clone fails (`could not read Username for 'https://github.com'` /
    `expected flush after ref listing`). Established by three runs on the same box — `alpine/git`
    worked, `ubuntu:22.04` failed, `ubuntu:22.04` with `-c http.version=HTTP/1.1` worked — and fixed
    in all three templates (`12d7e240`). `git.py` had already made this choice for the clones the
    app itself makes; the build context was the last place still speaking HTTP/2.
- [x] 7.6 WoW Tortoise — data + templates; first-ever extraction from a 7272 client; boot to the banner this core actually prints (`World server is up and running!` — the line originally read `Ready to login`, which was defect 3 of this very gate: no Tortoise worldserver prints it, and a criterion asking for it could only ever be met by a false reading); client connects; `status` promoted from `wip`; source pinned
  - **What code produced this tick cannot be recovered, established 2026-09-04.** No commit was
    recorded for the run, and the 7.3 procedure that requires one (`pyplan/phase7-plans/7.3-cmangos-family.md`,
    steps 3 and 9) has no 7.4/7.5/7.6 counterpart. Worse than unrecorded: no committed commit
    can reproduce it. The tick's own commit `9e00f999` is what ADDED the Tortoise `rev` pin, so
    the run happened on an unpinned catalog; and two of the catalog fixes the entry describes —
    `5c290188` (ready budget 1800→3600, 12:09:06) and `5e2ea700` (the fatal lookahead, 12:17:20)
    — landed AFTER the 28-minute boot they are about, six minutes before `25d72ec2` declared the
    gate closed. The run is traceable to a WINDOW (after `9c93ad6e` 08:44, before `9e00f999`
    14:08, on core `7c0fb278`) and provably not to a point. It stays ticked because the
    measurements are real and pinned by `tests/test_tortoise_boot_facts.py`; what it cannot
    claim is re-runnability.
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
- [ ] 7.7 Native Windows, all four — WotLK first (closes the 6.3 `ac-db-import` blocker), then TBC, Vanilla, Tortoise from **`yulon-win11-gate`**'s clean checkpoint (this line said `yulon-win11`, which is the working box and has carried an install since 2026-09-03; the clean-checkpoint box is the `-gate` one); 9p extract/mmaps throughput recorded; `platforms` widened per entry
  - **A WotLK server is ALREADY installed and running on `yulon-win11-gate`, and its schemas match
    every other platform byte for byte** (found 2026-09-04, not by a fresh run). `C:\gate\wotlk-server`,
    all three containers up, `ac-db-import` **Exited (0)** and `ac-client-data-init` Exited (0) —
    which is the 6.3 blocker cleared on native Windows. Schemas: `acore_auth` **22**,
    `acore_characters` **111**, `acore_world` **315**, `acore_playerbots` **30**, identical to the
    Ubuntu, Fedora and macOS records.
    **What is missing is its transcript, and that is why this does not tick the WotLK half.** The
    box's own evidence folder (`pyplan/gates/7.7-win11-gate/`) turns out to record a FAILED attempt
    — `20-install.exitcode` is **1**, and `20-install.log` ends with the Docker Desktop engine
    never answering after four minutes of retries. The install that is actually running there was
    made afterwards and nothing captured it. Worth keeping precisely because the filename says
    "install" and the contents say the opposite.
  - **The first thing 7.7 hits is its own `platforms` line, and it is a chicken-and-egg** (measured
    2026-09-04). A Vanilla install on `yulon-win11-gate` refuses before preflight:
    `WoW Vanilla cannot be installed on Windows yet: its installer needs Linux. Nothing was
    started.` That is `Install.supports()` reading `platforms: ["linux"]`, which `catalog.json`
    still carries for all three CMaNGOS entries. So "platforms widened per entry" is not a step
    that happens after the installs — it is a step that has to happen before one can be attempted
    at all, and the widening is the thing the run is supposed to justify.
    **Resolved for the gate by widening the catalog ON THAT BOX ONLY**, never in the repo: the
    three entries there now read `["linux", "windows"]`, the engine accepts, and preflight passes
    with two warnings (55 GB free against the 60 GB comfort line; the same repack-shaped client
    warning Linux gives). Whoever ticks this line commits the widening on the strength of the run,
    not ahead of it.
  - **The 1800 s ready budget is the wrong shape for 9p, measured 2026-09-04 on `yulon-win11-gate`.**
    Both CMaNGOS entries wait for the first `Avg Diff:` line with `timeout_s: 1800`. On this box the
    world server's own timestamps (`docker logs -t`) put that line **24.6 min** after `mangosd` started
    for Vanilla (06:12:43Z → 06:37:22Z, inside the budget) and **46.0 min** for TBC (18:59:55Z →
    19:45:58Z, outside it). So the TBC run's engine verdict was `install failed: The server started but
    never reported ready`, exit 1 at 12:29:57 box-local after a 7 h 10 min run — while `tbc-mangosd`
    was up with `restarts=0`, loaded the world and has been printing its diff loop since. The three
    containers are still up at the time of writing. The install is complete and correct; the verdict
    is not, and the difference between the two games is the size of the world being read over 9p at
    ~1.4 MB/s, not anything either core did. Tortoise, which booted in 28 min on Linux (7.6), was
    given `timeout_s: 10800` in the box-local copy for its own run rather than a value guessed to
    fit. What this asks of the engine is a decision, not a number: a fixed wall-clock budget cannot
    be right on both a native Linux disk and a 9p share, and a container that is alive, not
    restarting, and still printing `Loading …` lines is not a server that failed. Left for the
    owner; recorded here so the TBC exit code is read as what it is.
  - **The `platforms` widening for TBC and Vanilla landed at `2f39a6d9`**, on the Vanilla run above
    and the TBC run just described. Tortoise stayed `["linux"]` in the repo until its Windows run
    earned it — which it did at `eb5f3b3f`, the bullet after next; until then the copy on the box carried the widening so the run could start at all (the
    chicken-and-egg two bullets up).
  - **Windows Tortoise, started 2026-09-04 23:19 CEST (14:19 box-local)** on `yulon-win11-gate`, driven
    by `C:\gate\run-tortoise.cmd` as scheduled task `dml-tortoise-install`, log at
    `C:\gate\evidence\tortoise77.log`. Source: a copy of this repo at `2f39a6d9` in
    `C:\gate\tortoise-src`, widened box-locally by `widen-tortoise.py` (platforms + `windows`, ready
    `timeout_s` 3600 → 10800 on the TBC measurement above). Client: the 7.6 copy from m910q
    (`/home/pk/TurtleWoW`, 172 files, tar md5 `5bca6fa4…` verified on arrival), byte-identical in
    `WoW.exe`/`TurtleWoW.exe` to the owner's copy on the Hyper-V host. Two things the run needed
    first, both recorded in `windows-gate-box-recipes`: preflight refused with **10 GB free against
    40 GB needed** (the 120 GB disk now carries three servers), answered by a second 120 GB VHDX
    hot-added from the host and mounted as `D:`; then Docker could not see `D:` until the
    docker-desktop distro had it mounted by hand (`mount -t drvfs`, not persistent). Server folder
    is therefore `D:\gate\tortoise-server`, client still on `C:`. The TBC stack was stopped first.
  - **WOW TORTOISE IS INSTALLED AND RUNNING ON NATIVE WINDOWS, 2026-09-05 — the fourth of four.**
    That run ended `install of wow-tortoise finished` at **00:43:19 box-local (09:43 CEST), exit 0**,
    10 h 24 min after its 14:18:59 start; `tortoise-realmd` (3724), `tortoise-mangosd` (8090) and
    `tortoise-db` (3306 on loopback, healthy) up with `RestartCount=0` on every one, realm
    advertising `172.30.52.119`, and the worldserver's own banner — re-read from the live container
    with `docker logs -t`, not from the transcript — `07:41:17Z World server is up and running!
    Loading time: 59 minutes 18 seconds`. Evidence: `pyplan/gates/7.7-win11-tortoise/` (transcript,
    exit codes, the container captures, and a README that walks the stamps). Stage walls from the
    transcript's own timestamps: clone 6 min; build **1h17m**; extract **2h47m** (dbc 158, maps 2805,
    Buildings 5367, vmaps 6921); mmaps **5h08m** (2133 files — the same 2133 the 7.6 Linux run
    produced, so the mmaps side of the TBC counts question does not recur here); conf + start-db +
    import 4 min; up → ready **61m42s**.
    **The widening is now earned for all three CMaNGOS entries** — `wow-tortoise` reads
    `["linux", "windows"]` with this commit, `CMANGOS_PLATFORMS` in `test_catalog.py` records the run
    that did it — and it could not be committed alone. The ready stage measured **3702 s** wall
    (23:41:37 `start_staged()` → 00:43:19 finished) against the **3600 s** the repo carried for
    Tortoise; the run was reported as a success only because the box's copy had 10800 s. Shipping
    `windows` at 3600 would ship a Windows install measured to expire 102 s before it sees its own
    banner and to repeat TBC's `never reported ready` verdict, so the budget moved to **10800 s** in
    the same commit and `test_tortoise_boot_facts.py` now holds the 3702 s floor beside the Linux
    one — the value it pins is the measured stage wall, not the number typed. The "decision, not a
    number" note two bullets up still stands: 10800 is the third data point for that decision
    (Vanilla 24.6 min, TBC 46.0 min, Tortoise 61.7 min, all over 9p), not the end of it.
    One confound, kept: the box's `dml-tortoise-dl` task fired a second time at 23:59 and re-fetched
    the 9.98 GB client at 6.6 MB/s until 00:23 — inside the 59-minute load — and its `unpack` twin
    stopped on `MD5 MISMATCH` (that is the `2` in `tortoise-unpack.exitcode`; the client the install
    used was verified at 14:06). The boot may be quicker on a quiet box; the budget covers the boot
    that was measured. Still owed before this line ticks: WotLK's transcript on this box.
  - **Windows Vanilla, started 2026-09-04 04:20** into `C:\gate\vanilla-server` against the 5.14 GB
    1.12.1 client at `C:\gate\client`. Numbers already in hand from setting it up, since 7.7 asks
    for throughput: the client zip came down from `wow.baerthe.com` at about **13 MB/s** (5.33 GB,
    and its size matches the server's ETag exactly), and unpacking it needed `tar -xf` rather than
    `Expand-Archive` — **91 MB/s against under 1 MB/s**, a 100-fold difference on the same file and
    the same box. Anyone scripting a Windows gate should not use `Expand-Archive` on a client.
    Docker's VM there has **11.7 GB and builds with 2 jobs**; the compile took **68 minutes**
    (18:01 clone start → 19:22 extract start, box-local Pacific stamps).
  - **WoW VANILLA IS INSTALLED AND RUNNING ON NATIVE WINDOWS, 2026-09-04.** All twelve stages, from
    an empty folder, ending `WoW Vanilla is installed and running in C:\gate\vanilla-server`. Three
    containers up; schemas `mangos` 207, `characters` 72, `realmd` 13, `logs` 3. Transcript:
    `pyplan/gates/7.7-win11-gate/vanilla77.log` (27,488 lines), throughput
    `ninep.csv`. **This is the first CMaNGOS install on Windows.**

    | stage | box-local (PST) | elapsed |
    |---|---|---|
    | clone-sources → build | 18:01:02 → 19:22:21 | **1h21m** |
    | extract (`ad`, `vmap extract`, `vmap assemble`) | 19:22:21 → 21:48:52 | **2h27m** |
    | mmaps | 21:48:52 → 23:05:58 | **1h17m** |
    | conf + start-db | 23:05:58 → 23:11:02 | 5m |
    | import | 23:11:02 → 23:12:31 | **1m29s** |
    | up → ready | 23:12:31 → 23:37:24 | 25m |
    | **total** | | **5h36m** |

    Read that table against the 9p figures above and the shape is unmistakable: **extract and mmaps
    are 3h44m of the 5h36m**, and they are the two stages that write thousands of files through the
    bind mount. The import, which writes one big stream into a Docker volume rather than the mount,
    took **89 seconds**.

  - **THREE OF THE FIVE EXTRACTION COUNTS DIFFER FROM LINUX, AND THE SERVER CAME UP ANYWAY.**
    This is the open question 7.7 must answer before its Windows half is ticked.

    | | Linux (7.5) | Windows | |
    |---|---|---|---|
    | dbc | 158 | 158 | same |
    | maps | 2429 | 2429 | same |
    | Buildings | 5076 | **3913** | 1,163 fewer |
    | vmaps | 5667 | **6077** | 410 more |
    | mmaps | 2008 | **2009** | one more |

    The two `ad` outputs match exactly. Everything downstream of `vmap_extractor` does not, and the
    `mmaps` figure is off by exactly one, which is the kind of difference that usually means a
    boundary rather than a fault. **No cause is established here and none should be guessed at.**
    What is worth writing down:
    * the server reached `ready` and is serving, so whatever the difference is, it is not fatal;
    * our own preflight warned about this client on BOTH platforms — `the client's origin:
      realmlist.wtf sits at the root ... which is how a repack looks`, whose remedy reads "use a
      clean client of this expansion **if extraction comes up short**";
  - **SETTLED 2026-09-04: THE DIFFERENCE IS THE PLATFORM.** The measurement above was taken, and
    every variable anyone could hold has been held.
    * **The client is identical.** All 20 files under `Data/` match byte for byte in size across
      the two boxes, including the 1.9 GB `patch.MPQ`; and `wmo.MPQ` — the archive
      `vmap_extractor` reads to produce `Buildings` — hashes
      `9933d9ca23d481647880c9798dc7c225cd926f51026d148f09cd5251fc56edb7` on both. 149 files each,
      24 under `Data/`.
    * **The CMaNGOS source is the same commit**, `8ec338a1704e7dcb1c0213eb7ed58f9231ade40f`, read
      out of each box's own clone rather than assumed from the catalog pin.
    * **The differing image tags are a red herring**, and worth writing down so nobody else spends
      time on them. `native-0baff6f3` and `native-d61e7711` differ because `image_tag()` is
      `native-` plus `install_id()`, and `install_id()` is a hash of the install DIRECTORY PATH —
      `/home/pk/vanilla-75` against `C:/gate/vanilla-server`. It carries nothing about source or
      tools.
    * **And the Linux number is not a memory**: `/home/pk/vanilla-75/data/Buildings` on m910q holds
      **5,076 files** right now, produced from that same hash-verified client.

    So: same archive, same commit, same Dockerfile, **5,076 `Buildings` on Linux and 3,913 on
    Windows**.
  - **AND THE CONCLUSION THAT FOLLOWED THAT SENTENCE WAS BACKWARDS. Corrected 2026-09-04, hours
    later, by diffing the two file lists instead of comparing their counts.** This line said the
    shortfall was "on the Windows side"; it is not a shortfall at all, and the run that loses data
    is the LINUX one. Evidence: `pyplan/gates/7.7-win11-gate/buildings-shortfall-measurements.txt`
    and the lists beside it.
    * **Nothing is missing on Windows.** `comm` over the two sorted lists: 1,163 names in Linux and
      not Windows, **0** in Windows and not Linux. All 1,163 are `.m2`; the 814 `.wmo` and 1,464
      `.M2` are identical on both sides. Every one of the 1,163 has a case-insensitive twin present
      on Windows — **1,163 of 1,163** — and md5 + size of all 1,163 pairs, computed on m910q where
      both spellings exist, is **SAME 1,163 / DIFF 0**. Unique case-insensitive names in the Linux
      list: **3,913**, which is the Windows file count to the file. NTFS folded byte-identical
      duplicates onto their twin. **Zero unique bytes lost.**
    * **The Windows run is the MORE complete one**, and this is the part worth the entry.
      `Buildings/dir_bin` — the placement index — is **36,635,438 bytes on Windows against
      31,072,203 on Linux**. Distinct model names in it: **3,348 against 2,981**. Placements:
      **503,722 against 429,016 — Linux is missing 74,706**. Not a truncation: 284 of the 367
      Windows-only names first appear below Linux's own end-of-file offset.
    * **The cause is upstream, in CMaNGOS at `8ec338a1`.**
      `vmap_extractor/vmapextract/gameobject_extract.cpp:9` `ExtractSingleModel()` writes the file
      under the RAW name from the WMO `MODN` chunk (`INNBED.MDX` → `INNBED.M2`), applying neither
      `fixnamen()` nor `fixname2()`. `model.cpp:242` `Doodad::ExtractSet()` then looks the model up
      under the FIXED spelling (`Innbed.m2`) and does `if (!input) continue;` — **a silent drop of
      the placement**. On a case-sensitive filesystem that lookup misses and the doodad is
      discarded; on NTFS it hits. Witness on m910q: `ls Innbed.m2` → no such file, `ls INNBED.M2`
      → 840 bytes. Verified independently by this session, not taken from the lane.
    * **So the count check is the thing that is wrong, not the extraction.** Any comparison of
      `produces` counts across platforms has to fold case, and a Windows count BELOW a Linux one is
      the expected reading rather than a defect. 7.7's Vanilla half is not blocked by this.
    * **What a fix would be, and whose.** `ExtractSingleModel()` should apply the same
      `fixnamen()`/`fixname2()` its reader uses, so the write and the later `fopen()` agree; and
      `Doodad::ExtractSet()`'s `continue` should count what it drops rather than dropping silently.
      Both are upstream CMaNGOS, not ours. Ours is the count check — and the knowledge that **every
      Linux install this project has ever made is missing about 74,706 doodad placements**, which
      is a thing players would see.
  - **THE 9p FIGURE 7.7 ASKS FOR, measured 2026-09-04.** Everything a container writes into the
    server folder on Windows crosses Docker Desktop's 9p mount, and that is the whole of the
    Windows tax. Sampled once a minute into `pyplan/gates/7.7-win11-gate/ninep.csv` by
    `pyplan/gates/ninep-sampler.ps1` — into a FILE, because the lesson from 7.4c's review is that a
    number read off a live box during a gate is gone the moment the gate changes the box.

    | phase | MB/s | files/s | what it wrote |
    |---|---|---|---|
    | `clone-sources` (git, into `src/`) | **~1.4** | ~8 | 415 MB, 5,294 files, about 5 min |
    | `extract` (`ad` then `vmap extract`, into `data/`) | **0.253 mean** (0.197–0.330) | **4.97 mean** (2.80–8.77) | 151.8 MB / 3,060 files in the 11-minute window |

    **Extract is four to five times slower than the clone**, and the reason is visible in the two
    columns: the rate per FILE barely moves while the rate per BYTE collapses, because 9p charges
    per operation and the extractors write many small files. Vanilla's shipped counts are dbc 158,
    maps 2429, Buildings 5076, vmaps 5667 — about 13,300 files before mmaps — so at five files a
    second the extract stage alone is over an hour of pure mount overhead. On Linux the same stage
    runs at disk speed.
    **THE EXTRACT STAGE FINISHED AT 06:49, AND TWO OF ITS FOUR COUNTS DO NOT MATCH LINUX.**
    This is the most important thing on this line and it needs answering before any Windows tick.

    | tool | Linux (7.5) | Windows | |
    |---|---|---|---|
    | `ad` — dbc | 158 | **158** | same |
    | `ad` — maps | 2429 | **2429** | same |
    | `vmap extract` — Buildings | 5076 | **3913** | **1,163 FEWER** |
    | `vmap assemble` — vmaps | 5667 | **6077** | **410 MORE** |

    Fewer Buildings and more vmaps, from the same tools against a client fetched from the same URL.
    **No cause is established and none should be guessed at here.** What is worth writing down is
    that our own preflight warned about this client on both platforms — `the client's origin:
    realmlist.wtf sits at the root ... which is how a repack looks`, whose remedy line reads
    "use a clean client of this expansion **if extraction comes up short**" — and on Windows the
    extraction did come up short on the count that feeds the assembler. Whether the two boxes hold
    byte-identical clients has NOT been checked; 7.4b's method for exactly this question is a
    content hash of the client tree, and neither side has one here.
    **Until that is settled, "Windows produces the same data" is not a claim this line may make.**
    An earlier draft of this entry made it on the strength of dbc and maps alone, an hour before the
    vmap tools finished and disagreed.
    It wrote **170 MB / 2,598 files in at most 9.3 minutes**: **0.305 MB/s, 4.66 files/s**.
    "At most" because the sampler's first row already had 321 files in it, so the true start is
    inside that first minute.
    **And the phase after it is not write-bound at all.** Once `vmap extract` began, the write rate
    fell to **0.008 MB/s and 0.63 files/s** while the tool kept working — it is reading the client
    over the read-only mount and computing, not writing. So "9p throughput" is not one number for
    the extract stage: `ad` is write-heavy and pays the per-operation cost, `vmap_extractor` is
    read- and CPU-heavy and does not.
    **The rate over the whole extract stage, in ten-minute windows** (from the same CSV, minutes
    counted from the stage's first sample):

    | minutes | MB/s | files/s | files written |
    |---|---|---|---|
    | 0–10 | 0.238 | 4.45 | 2,756 |
    | 10–21 | 0.149 | 3.17 | 1,980 |
    | 21–31 | 0.008 | 0.31 | 194 |
    | 31–42 | 0.008 | 0.48 | 298 |
    | 42–52 | 0.008 | 0.37 | 233 |
    | 52–63 | 0.008 | 0.30 | 190 |
    | 63–73 | 0.008 | 0.25 | 157 |
    | 73–83 | 0.007 | 0.09 | 57 |

    The break at minute 21 is `ad` finishing and `vmap_extractor` starting. After it the BYTE rate
    is flat at eight kilobytes a second while the FILE rate decays by an order of magnitude, which
    is the shape of a tool that is reading and computing more per output file as it goes, not of a
    mount getting slower.

    **No completion estimate is given here on purpose.** A rate that falls from 0.31 to 0.09
    files/s across four windows cannot be extrapolated honestly, and the two projections this line
    could have carried (25 minutes, then 2.4 hours) were both made during the run and both wrong
    within the hour. What can be said is measured: the install started at 04:20 CEST, and **90
    minutes later it was still inside its FIRST of the two vmap tools**, with roughly 3,600 of
    Vanilla's 5,076 `Buildings` files written. A CMaNGOS install on Windows is not "Linux but
    slower" — the extract stage is on a different scale, and 7.7 should budget for it rather than
    discover it.

    **The CSV committed here is a snapshot taken while the run was still going**, which the file's
    own timestamps show; it is evidence of a rate, not of a finished install.
  - **OWNER DECISION OPEN (2026-09-05): how much margin the ready wait keeps over the slowest 9p
    boot.** The three first boots this phase measured are the only evidence the launcher has for how
    long a healthy server may say nothing: Vanilla **1479 s** (06:12:43Z → 06:37:22Z) and TBC
    **2763 s** (18:59:55Z → 19:45:58Z), both `docker logs -t` on `yulon-win11-gate` 2026-09-04, and
    Tortoise **3702 s** (23:41:37 → 00:43:19, ready-stage wall,
    `pyplan/gates/7.7-win11-tortoise/README.md`). A management wait bounded below the slowest of them
    refuses a server that was going to succeed — the 2026-09-04 "never reported ready" verdict. The
    first fix put the bound exactly ON 3702 s, and a review measured what that means: driven through
    the real `wait_ready_quietly()` at the 480 s callers, a 3702 s boot is accepted and a **3703 s
    boot is refused**.
    **Decided, and open to override:** the bound is now `ceil(3702 × 1.868) = 6916 s`, where 1.868 is
    2763 / 1479 — the widest gap between two adjacent boots in that evidence, applied once above the
    slowest of them. In code: `native.MANAGEMENT_FLOOR_MARGIN` and `native.MANAGEMENT_FLOOR_SECONDS`,
    both derived from `native.MEASURED_9P_FIRST_BOOTS_SECONDS` rather than typed, with the argument
    in their docstrings.
    **What the number does not rest on:** these are three DIFFERENT servers, timed once each. Nobody
    has booted the same server twice on 9p, so the project has no measurement of run-to-run variance
    and this margin stands in for a quantity nobody has measured. The way to close it is a second run
    of one of the three, not an argument.
    **What it costs:** the 7.9 controller gate's worst case — a server that keeps printing and never
    says ready, three `wait_ready_for_game()` calls per run — goes from 3 × 3702 s (3.1 h) to
    3 × 6916 s (5.8 h) for WotLK, TBC and Vanilla; Tortoise is unchanged at 3 × 21600 s (18 h),
    its 10800 s budget already landing on the install cap. A server that is merely quiet still ends
    its wait after ONE window, so none of this is paid by a server that is down.
    **A second cost, measured on m910q the same day:** `MANAGEMENT_CEILING_WINDOWS` used to be
    pinned to one value by the tests; with the floor at 6916 s the values 2 and 3 are
    indistinguishable, because 1800 × 3 no longer reaches above the floor. Recorded in that
    constant's docstring. Override by editing the margin expression, or close it with a fourth
    measurement.
- [ ] 7.8 macOS, all four — **[blocked]** on hardware
- [x] 7.9 Controllers — `controller_wow_tbc/`, `controller_wow_vanilla/`, `controller_wow_tortoise/` mirroring `controller_wow_wotlk/`; `mysql` → `db.client` in `apply.py`/`maintenance.py`; CMaNGOS-family account creation (was 7.1–7.3 before the scope change; still owed, now after install)
  - **TICKED 2026-09-04. The three unmeasured criteria were driven against all three live CMaNGOS
    servers, and the run found a defect on every one of them, which is why the bar was the right
    one to keep.** Harness: `pyplan/gates/gate-79-controller-surface.py`. Logs:
    `pyplan/gates/7.9-cmangos/`.

    It drives `ControllerServices` rather than `docker`, deliberately. Timing `docker.stop_staged()`
    would have timed a function the Server tab does not call; `Controller.stop()` is what the Stop
    button calls and what calls `stop_staged()` in turn (`controller.py:291`), so the button's own
    path is what was measured.

    | | TBC (m910q) | Vanilla (yulon-ubuntu) | Tortoise (yulon-ubuntu) |
    |---|---|---|---|
    | `console.send_command()` | 3.61 s | 3.60 s | 3.61 s |
    | `backup()` | 4.4 s, 4 dumps | 6.3 s, 5 dumps | 6.4 s, 4 dumps |
    | `verify_dump()` | 4/4 | 5/5 | 4/4 |
    | `stop_staged()` | **3.1 s** (and **301.2 s** on a second run — see below) | **23.2 s** | **6.2 s** |
    | `start_staged()` | 6.1 s | 6.6 s | 6.8 s |
    | to ready | 73.0 s | 45.8 s | 111.6 s |
    | `restore()` | 15.5 s | 29.6 s | **98.2 s, all 4 databases** |
    | ready again after it | 75.3 s | 45.9 s | 100.0 s |

    Ten checks each, **10 passed / 0 failed on all three**.
    **Those counts are the instrument as it stood on 2026-09-04.** The harness gained an
    eleventh check that afternoon — an unconditional wait for this game's ready marker before
    the console step, added because the run below consoled a server it had not waited for —
    so a re-run of the same scenario now reports 11, not 10. The numbers above are not
    reproducible against today's file, and are kept as what the run said rather than
    re-stated as what a re-run would say.

    * **The console step asks the harder question.** `send_command()` attaches to the worldserver's
      tty and detaches again, and a detach that forwards a signal kills the server — so
      `State.Pid`, `RestartCount` and `StartedAt` are read before and after **through the docker
      CLI**, not through the code under test. All three unchanged on all three games. TBC's five
      reply lines are cut on `mangos>`, which this core prints AFTER the answer rather than before
      it, and they name the world DB and the client build.
    * **`plan_restore()` refused over a running server on all three**, in its own words: the
      worldserver keeps characters in memory and would overwrite a restore within minutes.
    * **Tortoise's restore covers every database**; TBC's and Vanilla's cover one. That is a
      property of the HARNESS, not of the app, and it is recorded rather than smoothed over: the
      first version restored `report.dumps[0]`, which is always the alphabetically first database,
      so the world database was backed up and byte-verified and never restored. Fixed, and
      re-run on the one server that was free.
    * **`stop_staged()` ranges from 3.1 s to 301.2 s**, and the top of that range came from
      running the SAME call against the SAME TBC install an hour later. The compose template allows
      it: `stop_grace_period: 5m`. Anyone putting a spinner on the Stop button should size it for
      five minutes, not for the 3.1 s this table opens with.

  - **A SECOND TBC run, 03:02–03:12, and it disagrees with the first about the number that matters.**
    `pyplan/gates/7.9-cmangos/gate79-tbc-alldumps.log`. Run with the `core_databases` fix deployed
    and with the harness that restores every dump. **9 passed, 1 failed** — against the
    ten-check instrument of that morning; the same scenario reads 11 passed / 0 failed on the
    harness as it stands after the fix below, because the check that failed was the one the
    fix removes the cause of.
    * **`restore()` put back all four databases — including the 158 MB `mangos` — in 48.4 s.**
      That is the full round trip the first run only did for `characters`, and it is now proven on
      two games (TBC here, Tortoise at 98.2 s for four).
    * **The `acore_*` warning is gone from a live server**: `grep -c 'this install has no acore'`
      over this run's log is **0**, against 1 on the Vanilla run and 4 on Tortoise's before the fix.
      Verified where it was found, not only in the test that reproduces it.
    * **`stop_staged()` took 301.2 s here and 3.1 s in the first run — same function, same install,
      same day.** The compose template sets `stop_grace_period: 5m`, so 301 s is that window almost
      exactly; the container still exited **0**, so it shut down on its own rather than being
      killed. Both readings are real and the range is the finding: **a Stop button on a CMaNGOS
      worldserver has to tolerate five minutes**, and the 3.1 s in the table above is the lucky end
      of that range, not a typical figure.
    * **The one FAIL is the harness's fault and is recorded as such**: `the console prompt never
      appeared in the window; the reply was not delimited`. This run found all three containers
      already up — because a person had just `docker start`ed them — so it skipped its own
      wait-for-ready and attached to a worldserver that was still loading. The attach itself was
      clean (pid and StartedAt unchanged). A gate that consoles a server it did not wait for is
      asking a question the server cannot answer yet; the harness should wait for ready even when
      it did not do the starting.
  - **The worldserver-exits-alone mechanism now has THREE independent reproductions, and the third
    is the cleanest.** `docker start tbc-db tbc-realmd tbc-mangosd` — all three at once — put
    `tbc-mangosd` at **`RestartCount=5`** before it settled, with **five** `Could not connect to
    MySQL database at tbc-db: Can't connect to MySQL server on 'tbc-db:3306' (111)` lines and five
    `Cannot connect to world database` — one of each per restart
    (`pyplan/gates/7.9-cmangos/79-tbc-restart-evidence.log`, which now carries its own counts). The
    database container was simply not accepting connections yet.
    **This line said "twelve" until a review counted them.** Twelve was the answer to a different
    question — a `grep -c` over TWO patterns at once across the whole container log — and the
    evidence file first committed beside it held only four, because the capture had `head -14` on
    the end of a pipe. Both are the same mistake in different clothes, and it is the one this
    checklist recorded a lesson about eight hours earlier: capture the whole thing, and make the
    artifact carry the count so nobody has to trust the prose. The file now does.
    **That is precisely what `start_staged()` exists to prevent** — it waits for the database
    before starting the servers, which a bare `docker start` does not — and it is the mirror of
    the `stop_staged()` argument. Three triggers, one mechanism: **this core exits when its
    database is not there, and `restart: unless-stopped` brings it back alone.** The 2026-09-03
    journal's solo restarts are no longer a mystery about what could possibly cause them; they are
    a question about which of these three happened, and the containers that would say were deleted.
  - **What the run found, and what it means for the Server tab.**
    * **A restore on ANY CMaNGOS server announced three missing AzerothCore databases.** Once on
      Vanilla, four times on Tortoise, once on TBC: `this install has no acore_auth,
      acore_characters, acore_world; backing up what it does`, on servers with every database
      present. **Fixed**: `restore()` had no `core_databases` parameter at all, so its internal
      safety dump fell back to AzerothCore's names and no per-game wrapper could correct it. The
      names are now threaded through `restore()` → `_safety_backup()` → `backup()` and bound in
      all three wrappers. No data was ever at risk; the safety dump always took the right
      database. What was wrong was a message telling a user their healthy server was broken.
    * **A CMaNGOS worldserver exits non-zero when its database goes away, and `unless-stopped`
      then restarts it alone — which is the signature 7.9's open question describes.** Two
      mechanisms, both captured tonight:
      1. `tbc-mangosd` **exited 139** when `docker stop tbc-mangosd tbc-realmd tbc-db` took the
         database out from under it mid-shutdown. Its last words:
         `SQL ERROR: Lost connection to MySQL server during query` on
         `UPDATE characters SET online = 0`, then `Critical Error: A condition which must never be
         false was found to be false. Server was shut down to protect data integrity.` and
         `GetStmt(): false && "Unable to prepare SQL statement"`.
         `pyplan/gates/7.9-cmangos/74c-mangosd-sigsegv.log`.
      2. `vanilla-mangosd` reached **`RestartCount=8`** in a restart loop, exiting 1 within a
         second each time on `Could not connect to MySQL database at vanilla-db: Unknown MySQL
         server host 'vanilla-db' (-3)`, because its database container was down and DNS for the
         name no longer resolved. `pyplan/gates/7.9-cmangos/79-vanilla-restart-loop.log`.
    * **This is exactly what `stop_staged()` exists to prevent, and it does.** Its docstring says
      `compose stop` "walks the project's own `depends_on` graph, so the servers close their
      connections before the database goes away" — and every `stop_staged()` in these three runs
      stopped all three containers cleanly. Mechanism 1 was produced by a hand-typed
      `docker stop` of all three by name, which is precisely the un-ordered stop the staged one
      replaces.
    * **What this does NOT prove.** The 2026-09-03 journal shows `tbc-mangosd` restarting alone
      while `tbc-db` and `tbc-realmd` were untouched — no container was stopped. Mechanism 1 needs
      only a dropped CONNECTION, not a stopped container, so it remains a sufficient explanation
      of that signature; it is not proof of it, and the containers that would have proved it were
      deleted. What has changed is that "why would a worldserver exit on its own?" is no longer
      unanswerable: on this core, losing the database mid-statement is an abort, by design.

  - **UNTICKED again 2026-09-04, one day after it was ticked, and the reason is worth more than
    the box.** `pyplan/phase7-decisions.md` sets this line's bar as "start/stop/logs/accounts/backup
    on each installed server". What was driven on the three CMaNGOS games is account creation and
    a real client login — no backup/restore round trip, no `console.send_command()`, no timed
    `stop_staged`/`start_staged`. The tick was taken on the half that was measured. Narrowing the
    DoD was the other option and is the wrong one: that bar is what WotLK's own controller was
    held to and cleared on three platforms, and narrowing it would ship three games a Server tab
    whose Stop button has never been pressed against a CMaNGOS worldserver. Found by a
    verification pass over an audit, not by the audit itself.
  - **A live question is waiting in that gap.** `pyplan/gates/7.9-m910q-tbc-restarts.journal`
    holds dockerd's own record from m910q: `tbc-mangosd` rejoined its network ALONE at 13:18:57
    and again at 20:07:07 on 2026-09-03 — the two restarts behind `RestartCount=2` — while
    `tbc-db` and `tbc-realmd` were untouched. A solo worldserver restart under `unless-stopped`
    means it exited on its own, twice, on a server nobody was driving. WHY is no longer
    recoverable: the container and its logs were deleted that night to free the names for the
    7.4c run, and the journal's one `oom_kill` line belongs to a Tortoise BUILDER container from
    the previous day, not to this server. The pattern is evidence; the cause is gone.
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
  - **Console, modules and networking measured 2026-09-03 — nothing owed on any of the three.**
    The tempting instrument was the wrong one: diffing the keyword arguments each
    `_for_<game>()` passes reports `prompt`, `prompt_precedes_answer`, `scheme`, `import_probe`
    and `reset_unfinished` as missing from all three. They are not. The first three are bound
    INSIDE each package's own `console`/`accounts` module, and the last two are AzerothCore-only —
    a CMaNGOS install has no one-shot import service to re-run. That diff compares spellings at a
    call site, not capabilities.
    - **Console** — each package binds its own `PROMPT`, `prompt_precedes_answer` and container
      and calls the shared `send_command()`. The lower def-count in those files is WotLK holding
      the shared implementation, not a smaller surface.
    - **Modules** — WotLK-only, and correctly so: `manifests/` contains `wow-wotlk` and the schema
      and nothing else, so there is nothing for the other three to port yet (7.1 step 2 says "if
      any exist"). `_no_manifest_store()` returns None and warns if the catalog ever says
      otherwise.
    - **Networking** — entry-driven rather than per-package: one shared `networking.apply()`,
      wired for all four in the view.
    - **Asserted rather than read**, by `test_every_game_offers_the_whole_controller_surface_wotlk_does`:
      `ControllerServices` is enumerated with `dataclasses.fields()` and every field must arrive
      for every game, so a sixteenth capability cannot be wired for WotLK and forgotten for the
      rest. The one exception is required to be REAL both ways — the three must report `store`
      and `applier` as None and nothing else, because a non-None store there would mean a game
      was handed somebody else's manifests.
  - **Vanilla's account creation DONE 2026-09-03**, through the same two functions on the live
    server: `accounts.sql_for_install()` + `create_account()` on `yulon-ubuntu`, account id 105,
    `gmlevel 3`, `length(v) = length(s) = 64` — an SRP6 verifier and salt, not a password hash.
    The seam printed `client='mariadb'`, the `f8cacafc` binding in the field again. All three
    CMaNGOS account paths have now been driven against a real server.
  - **VANILLA CLIENT LOGIN DONE 2026-09-03 — 7.9 closed.** The owner drove a real 1.12.1 client
    from the LAPTOP (not the Hyper-V host) against `yulon-ubuntu`, and reached a character in the
    world. Server-side evidence, read after he said he was in: `realmd.account` row 106 `PERZI`
    with `length(sessionkey) = 80` and `active_realm_id = 1` — an SRP6 session negotiated by the
    core, not a row we wrote; `characters.characters` guid 901 `Perzi`, race 5, class 1, level 1,
    **`online = 1`**; and the world log's own `Avg Diff: 57. Sessions online: 1.` All three
    CMaNGOS games have now been logged into with a real client.
  - **The account had to be a NEW one, and the reason is a deliberate design choice.**
    `create_account()` never rewrites an existing account's salt and verifier — its docstring:
    "a second call with a different password does not lock the owner out" — so the password of
    the `YULON` account made earlier could not be reset through the app, and nothing had recorded
    it. `PERZI` (id 106) was created through the same `accounts.sql_for_install()` +
    `create_account()` path, which exercised the Vanilla account seam a second time; the seam
    printed `container='vanilla-db'`. `YULON`'s `sessionkey` is still NULL, which is the
    independent confirmation that it was never logged into and the password really was lost.
  - **The realm row now advertises the Tailscale address, and that is a real finding for 7.7/8.**
    It was `172.30.55.119` (Hyper-V Default Switch), which only the VM HOST can reach: a client
    on any other machine authenticates on 3724 and is then handed a world address that does not
    resolve for it. Set to `100.101.205.6`, which both the laptop and the host reach, and the
    login above went through from the laptop. The realm address a fresh install writes is
    whatever `detect_lan_ip()` returned on the server's own box, so an install on a machine with
    more than one network path can hand out the wrong one — worth an explicit choice rather than
    a detection, whenever networking is revisited.
- [ ] 7.10 Cross-server regression pass — re-run WotLK's 6.5 coverage gate after 7.1–7.9 land to confirm shared layers (`docker.py`, base `Controller`, `runner.py`, `platform.py`, `networking.py`) weren't regressed (was 7.4)
  - **The honest-cancel copy was seen arriving from a REAL cancelled install, 2026-09-05, through
    the widgets** — the one install-half item `pyplan/gates/7.10-gaps/README.md` (2026-09-04) said
    could not be produced on a box that refuses every install at preflight. On `yulon-ubuntu`
    after 7.2's press 1 (Docker installed, no server, 76 GB free), `widget_cancel_driver.py`
    built the real `CatalogView` over a real `LogPanel`, clicked the WotLK tile's `Install`
    (`QTest.mouseClick`, folder picker injected, throwaway folder), waited for the engine's own
    `Cloning mod-playerbots/azerothcore-wotlk` line (7.1 s after the click), clicked the panel's
    `Stop` 20 s later, and **320.4 s afterwards** `install_finished(ok=False)` arrived with a modal
    titled *Install cancelled* whose text **is** `cancelled_install_message()` for the folder as it
    then stood — compared as strings, not eyeballed. 15 OK / 0 FAIL, the tile still reads
    `Install`. `pyplan/gates/7.2-ubuntu-2026-09-05/widget-cancel.log`.
    **Two things the run showed that the copy does not know, filed as findings and not fixed
    here:** (1) the copy's compose-file split fired on **upstream's own `docker-compose.yml`**,
    which the clone brings in git-tracked and unmodified — generate-compose had never run, and the
    user was pointed at *Use existing…*; (2) the copy's other half, "press Install again and
    choose <folder>: the installer carries on from the last stage recorded in
    `.yulon-install.json`", was **refused** when tried on that folder — no state file existed
    after the cancel, and the engine said *"already a git checkout … no record here of an
    install this app made … Install into an empty folder instead"*
    (`cycle2-pressA2-refused-existing-checkout.log:31`). The refusal is right; the promise is
    not. Also measured: a Stop during clone-core cannot interrupt the containerized `git`
    (`docker ps` 5 s after Stop still showed the `alpine/git` container), so five minutes between
    Stop and the dialog is what a user pays for stopping there.
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
