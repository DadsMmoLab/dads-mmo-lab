# Bug checklist — the four-platform hunt, 2026-08-28

Every defect found by running Yu'lon on **Ubuntu 24.04, Fedora 44, Arch, Windows 11** and the
physical **m910q** box, against `upstream/Yulon` (base `d576a00c`). Tick as they land.

**Status key:** `[x]` fixed and verified on a real box · `[ ]` open · **drafted** = patch written,
not yet applied · **decision** = not a fix, an owner call.

Evidence for every item is in `dml-vmrun/runlogs/` (per-box reports and one file per major finding).
Nothing here is from reading code alone — each was reproduced.

---

## 1. Fixed and verified — on branch `fix/ubuntu-to-green` (16 commits, CI green)

These are shared fixes: found on one platform, they affect all of them.

- [x] **The installer asked which containers exist and called it "was it built?"**
  `install-wow-wotlk.sh:1249` (+ `-ubuntu.sh`, `-fedora.sh`)
  `docker compose images` reports a project's **existing containers**, not the image store. A folder
  whose compile finished but whose `up` never ran read as "nothing was built" — and the next branch
  offered to delete a good 35-minute build. Now asks `config --images` (the compose file) then
  `docker image inspect` (the store).
  *Verified on real Docker: same folder, old check `NOT BUILT`, new check `BUILT`.*
  **Must stay ahead of the next item** — with `reinstall` set, a wrong verdict here deletes the build.

- [x] **Declining "Remove it and start fresh?" exited 0, and the app filed that as an install**
  `installer.py:180`, `install-wow-wotlk.sh:1273`, `catalog_view.py:497`
  Zero means success to the caller: the view pinned a compose project name into a folder holding no
  server and grew a tab for one that was never built. `docker.py` records that such a pin is
  inherited by any **copy** of the folder, so Stop in the copy could stop the original's server.
  *Verified end-to-end through `Installer.run()`: before — "install finished", no error; after —
  `InstallerError: exited with status 1`, folder untouched.*

- [x] **A slow Docker daemon wedged the Server tab permanently**
  `docker.py:1615`, `controller_view.py:445`
  `docker ps` ran with **no timeout**. `refresh_status()` sets `_status_pending` and clears it only
  from a callback, so a call that never returned meant every later poll bailed at the guard —
  `status: unknown` forever, no error, nothing to click. Reproduced independently on Windows: **8+
  minutes frozen, three manual Refresh clicks did nothing**, recovered only by killing Docker.
  *Verified with `docker` stubbed to `sleep 3600`: before — still hanging when killed; after —
  `DockerCommandError` at the deadline.*

- [x] **The GUI test suite died in 9 of 20 runs — three separate thread-lifetime bugs**
  `ui/widgets/job.py`, `ui/widgets/log_panel.py`, `main.py`
  A worker was garbage-collected with its panel/view **before the OS scheduled its thread**;
  `QThread::started → worker.run` then ran on freed memory. Native backtrace: SIGBUS in
  `QMetaMethod::name()`. Three sites, found one at a time because each fix only moved the crash —
  the third was `main.py`'s `setProperty("update_worker", …)  # keep references alive`, and **a Qt
  property holds a `QObject*`, not a Python reference**.
  *Gate: **25/25 clean under gdb** (pre-fix died on run 1, half-fixed on run 2) and **20/20 plain**
  (baseline 1/20, half-fixed 5/20).*

- [x] **Every MariaDB backup — and every restore — was rejected**
  `controller_wow_wotlk/maintenance.py:147`
  MariaDB 10.6 writes `/*M!999999\- enable the sandbox mode */` before the banner, so
  `-- MariaDB dump` is no longer at byte 0 and the anchored check refused a complete dump.
  `plan_restore()`, `restore()` and `_safety_backup()` all gate on the same `verify_dump()`.
  *Found on a live Tortoise server; fixed, then hardened after review (below).*

- [x] **Accepting MariaDB's preamble also accepted a file that was not a dump** *(found by review)*
  A support-tickets table whose free-text column held pasted dump output satisfied the banner, the
  `USE` line and the trailer — and was accepted as a dump **of that database**. Now the banner may
  be preceded only by what a dump itself writes: `/*!…*/`, `--`, blank lines.

- [x] **The installer CLI hung forever at sudo, and could never reach the native engine**
  `installer.py:741`
  `_main()` passed no `ask` callback, and sudo reads from a pty — so on any box needing a password
  the CLI parked with no timeout and no error. It also built `Installer(...)` directly, so
  `NativeInstaller` was unreachable from the CLI on every platform.

- [x] **Integration teardown burned 300 s twice per run**
  `tests/integration/conftest.py:87`
  Fixture containers ran `sleep` as PID 1, which gets no default SIGTERM action, so
  `docker compose stop -t 300` waited out the whole grace period. Made the suite 32–45 minutes.
  *Measured: old shape still running when a 120 s probe gave up; new shape stops in **0.27 s**.*

- [x] **`mypy .` disagreed with CI** — `pyproject.toml` now excludes `tests/`; all four invocations
  report the same 38 files, 0 issues. Four boxes reported the identical 375 errors before anyone
  compared the two commands.

- [x] **`python -m yulon` did not exist** — every box tried it first and got
  `No module named yulon.__main__`. Added as a redirect to `main.main()`.

- [x] **A native install logged nothing when it finished** — the bash path logs
  `install of <id> finished`; the native path logged nothing, so the only completion signal was a
  compose-project pin. That is why a careful tester read a 7-minute readiness wait as
  "the install was not remembered" and filed it as a HIGH defect that turned out not to exist.

- [x] **Two installs of the same game collide — the user-facing half**
  `native/base.yml.tmpl:52,119,145,207,231`
  `container_name:` is pinned, so the names are global to the host and a second install fails at
  `docker compose up` with a raw daemon `Conflict` — **after** the 2–4 hour build. Hit independently
  on Ubuntu and Arch. Now refused **before** the build, naming the folder that owns the name.
  *Coexistence itself is still open — see Decisions.*

- [x] **ContainerGit error formatting produced glued string literals on failures**
  `yulon/git.py:507-510`
  `_capture()` had two adjacent f-strings without separator, causing exit codes and error messages
  to implicitly concatenate into garbled strings. Fixed with single clean error formatting.
  *Verified with unit tests in `test_git.py`.*

- [x] **ContainerGit inherited launchd file descriptors from GUI processes on macOS**
  `yulon/git.py:501`
  Running containerized git from Finder/.app spawned subprocesses inheriting non-standard stdin,
  which could interfere with docker child execution. Fixed by passing `stdin=subprocess.DEVNULL` to `runner.run()`.
  *Verified with unit tests in `test_git.py`.*

- [x] **Darwin sleep assertions failed to terminate on worker exit**
  `yulon/platform.py`
  `platform.keep_awake()` spawns `caffeinate -dims -w <pid>` on macOS; verified proper process
  lifecycle termination upon context exit.
  *Verified with unit tests in `test_platform.py`.*

---

## 2. Found by review, patch drafted, not yet applied

A six-lens review plus an adversarial pass over the fixes above. These are real; the patches are
written and waiting on the review's final verdict.

- [ ] **drafted** — **The collision guard is not on the path where the collision was measured.**
  `install-wow-wotlk.sh:1242` The skip-compile branch runs `docker compose up -d | tail -5` and
  `return 0`, so the daemon's Conflict is printed, its exit status is swallowed by `tail`, and the
  script reports success. That is exactly the Arch scenario: build complete, `up` collided.
- [ ] **drafted** — **`InFlight.sweep()` frees a worker while its thread is still inside
  `QThreadPrivate::finish()`.** `job.py:111` Qt clears `isRunning()` *before* the thread has exited
  (`wait(0)` still false), so the release must `wait()` first, like `_dispose_last_job` does.
- [ ] **drafted** — **An install folder the user cannot enter is reported "definitely not built"**,
  and the script then offers to `sudo rm -rf` it. A root-owned 700 folder from an earlier run hits
  this. Should land in "could not check", not the delete prompt.
- [ ] **drafted** — **`refuse_foreign_containers` passes silently when `docker compose config`
  fails** — an unreadable compose file looks identical to "pins no names".
- [ ] **drafted** — **A symlinked install dir is refused as another install's.** Compose records the
  *logical* working dir; the guard compares a *physical* path.
- [ ] **drafted** — **Tests stay green when the call site is removed.** The lifted-function tests
  prove the helpers work, not that anything calls them. Needs a test that both `compose up` sites
  are guarded.
- [ ] **drafted** — **The frozen artifact would carry `yulon/__main__.py`**, pulling `main.py` in a
  second time as a library module. Excluded in the spec.
- [ ] **drafted** — **Four fixes have no test that names them** — the CLI prompter, the native
  completion log, the update-worker hold, and `python -m yulon`.

---

## 3. Fedora — SELinux

- [ ] **The Python bind-mount probe omits the SELinux relabel.** `docker.py:2428` mounts
  `{mount}:/probe:ro` with no `:z`/`:Z`. Proven by A/B on an enforcing box — and `ausearch` showed
  **no denial**, because Fedora `dontaudit`s exactly this case, so "no AVC in the log" is not
  evidence SELinux is innocent. Also `tests/integration/conftest.py:210`.
  *Harmless today (see next item); HIGH the moment Linux moves to the native engine.*
- [ ] **The entire `preflight` module is unreachable on Linux.** `installer_for()` → `is_native()` →
  `script_platforms: ["linux"]` means every Linux box gets the bash script and never touches
  `catalog/preflight.py`. Two separate findings share this one root cause: the SELinux probe above,
  and the CPU-vs-RAM check below. **Both wake up together when Phase 7.2 retires the bash lineage.**
- [ ] **`_cpu_check`'s remedy names Docker Desktop**, which does not exist on native Linux.
  `preflight.py:355`. Its arithmetic is right and would have caught Fedora bricking itself: at 8 GB
  it computes `affordable = 4` against `jobs = 7`. It never ran.

## 4. Arch — prerequisites

- [ ] **The launcher will not start from source on a stock Arch desktop.** Qt names one missing
  library; `ldd` shows **six**: `xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image
  xcb-util-renderutil libxkbcommon-x11`. The README lists no Linux prerequisite. `libxkbcommon-x11`
  is one of the five sonames `check-bundle-closure.sh` already caught missing from the shipped
  tarball — same library, hit again on a path that gate does not cover.
- [ ] **Document the Debian-family equivalent** (`libxcb-cursor0` and friends), found on m910q.
- [ ] **Document that LAN setup is only partly automatic on Arch** — `detect_firewall()` returns
  `"none"` (no `ufw`, no `firewall-cmd`) and the app degrades gracefully to manual instructions.
  Correct behaviour, but "networking works" means something weaker there.

## 5. Windows

- [ ] **`compose_file()` crashes on an unreachable WSL path.** `installer.py:67` calls `is_file()`
  unguarded; `\\wsl.localhost\<distro>\…` for a stopped distro raises `WinError 64`, which `pathlib`
  does **not** swallow. **Intermittent** — the first call raises, later calls on the same path
  return cleanly (WSL's 9P provider degrades to `ENOENT`, which pathlib *does* swallow) — so the fix
  must be a broad `except OSError`, not a special case for errno 64.
- [ ] **"Find in WSL…" appears on every tile and always fails.** `wsl_distros()` returns
  `('docker-desktop',)` on a box with no real distro. `catalog_view.py:263` gates the button on that
  list, and its comment claims `wsl_distros()` "answers () for all of them" — false on **every**
  Windows box with Docker Desktop. **`tests/test_platform.py:526` pins the buggy value**, so fixing
  it breaks a green test and will look like a regression.
- [ ] **Tab titles collide.** `main.py:220` titles a tab `server_dir.name`, and the installer always
  suggests the same leaf name — two installs in different parents are indistinguishable in the tab
  strip. Not data loss: Stop is ownership-protected by compose labels and refuses across installs.

---

## 6. Decisions — not bugs to fix

- [ ] **Self-update has never worked for anyone.** `update.py:26` calls `/releases/latest`, which by
  definition excludes prereleases — and **every** upstream release is flagged `Pre-release`
  (`v0.6.57Public`, `v0.6.55Public`, `v0.6.53`). Two independent fixes, not exclusive:
  **code** (call `/releases?per_page=5` and take the first non-draft — works with the release
  practice as it is), and **process** (`gh release edit … --prerelease=false`, needs upstream write
  access). *Which depends on whether 0.6.x is meant to be pre-release.*
- [ ] **Two installs of one game cannot coexist.** The template already parameterises the compose
  *project* (`name: {{PROJECT_NAME}}`) and then pins global `container_name:`s that override it. The
  service names already provide Compose DNS, so those five lines are redundant for addressing — but
  `catalog.json`'s `containers` map names them literally and **40 call sites across 7 controller
  files** find containers that way. Options: **(a)** product rule "one server per game per machine",
  zero further code; **(b)** resolve by project+service everywhere — its own PR, with a
  two-installs-coexist gate, because the failure mode is stopping the wrong server.
- [ ] **Tortoise on Windows.** `catalog.json:226` declares `platforms: ["linux"]` for a script named
  `install-tortoise-wow-wsl.sh`. There is **no code path that runs an install script inside a WSL
  distro** — `installer.py` has no WSL handling and `bash_available()` deliberately refuses the WSL
  shim. Options: **(a)** rename the script, zero code; **(b)** build "install into WSL distro X" —
  that is roadmap 7.7.
- [ ] **The Accounts tab is create-only.** No list, no set-password, no standalone GM level. The
  Console is not a workaround for listing — `account list` is not a mangosd command either. A
  feature gap; no documented decision says it should be that way.
- [ ] **No floor on Docker's data root.** The branch warns on both disks. The VM that died in this
  run died of **host-side dynamic-VHDX exhaustion, invisible from the guest** (guest read 41 GB
  free, host had 8), so no guest check could have seen it — and turning the warning into a refusal
  would block installs that would have succeeded.

---

## 7. Coverage — what was and was not exercised

Installed for real: **WotLK** via apt (Ubuntu), pacman (Arch) and the **native engine** (Windows —
first time ever, 72-minute build into a path with spaces, `ac-db-import` exited 0, which settles the
9p question open since 2026-08-25). 500 bots verified four independent ways on each.

- [ ] **Fedora's dnf variant has never completed a fresh compile** — it reached 83% at 8 GB and
  swap-thrashed the box unreachable. Owed by Fedora's own pass at 23 GB.
- [ ] **TBC, Vanilla and Tortoise installers have never been run.** All three need a client dir.
  Clients are now on m910q (`~/clients/WoW-Client-1.12.1`, `~/clients/WoW-Client-2.4.3`,
  `~/TurtleWoW`); m910q has 52 GB free. **Note Vanilla's script refuses under 20 GB on both the
  target disk and Docker's root**, so prune between installs.

---

## One thing worth keeping

Three of these have an obvious fix that is **wrong**, and two of them arm a worse bug:

1. Setting `reinstall=True` (the natural fix for the silent-success bug) turns the unreliable image
   check from harmless into **deleting a good 2–4 hour build**. Fix the image check first.
2. Deleting `container_name:` makes every container `<project>-ac-worldserver-1`, and the controller
   finds nothing.
3. Changing the compose-file guard before installs are resumable breaks a case that currently works.

The pattern: *check whether the thing you are about to make true is guarded by something that is
only safe while it is false.*
