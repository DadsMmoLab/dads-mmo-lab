# Bug checklist

Every defect found by running Yu'lon on a real box, across every hunt round. One list, not one
per round — a bug found on Arch is usually present on Ubuntu too, and splitting them by where
they were noticed hides that. The running order of the rounds is in
[`hunt-rounds.md`](hunt-rounds.md).

## Round 1 — WotLK on Ubuntu 24.04, Fedora 44, Arch and Windows 11

Opened 2026-08-28 against `upstream/Yulon` (base `d576a00c`), plus the physical **m910q** box.
Tick as they land.

**Status key:** `[x]` fixed and verified on a real box · `[ ]` open · **drafted** = patch written,
not yet applied · **decision** = not a fix, an owner call.

Evidence for every item is in `dml-vmrun/runlogs/` (per-box reports and one file per major finding).
Nothing here is from reading code alone — each was reproduced.

---

### 1. Fixed and verified — PR #126, 21 commits, all three CI checks green

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

---

### 2. Found by reviewing the fixes themselves — all applied but two

Four rounds looked at the first fifteen commits: a six-lens in-house review, an adversarial
refutation pass, a critique, and finally an independent review from a different model family
(Codex). Everything below is in PR #126 except the two the refutation pass killed, which are
recorded here so nobody "fixes" them again.

- [x] **The collision guard was not on the path where the collision was measured.** The
  skip-compile branch ran `docker compose up -d | tail -5` and `return 0`, so the daemon's
  Conflict was printed, its exit status swallowed by `tail`, and the script reported success —
  the exact Arch scenario. Now a `PIPESTATUS` check, with the ownership diagnosis run *after*
  the failure rather than as a gate before it.
- [x] **An install folder the user cannot enter was reported "definitely not built"**, and the
  script then offered to `sudo rm -rf` it. A root-owned 700 folder from an earlier run hit this.
  Now "could not check".
- [x] **`refuse_foreign_containers` passed silently when `docker compose config` failed** — an
  unreadable compose file looked identical to "pins no names".
- [x] **Tests stayed green when the call site was removed.** Both `compose up` sites are now
  covered by a test that fails if the guard is not reached.
- [x] **The frozen artifact carried `yulon/__main__.py`**, pulling `main.py` in a second time as
  a library module. Excluded in the spec.
- [x] **Four fixes had no test naming them** — the CLI prompter, the native completion log, the
  update-worker hold, and `python -m yulon`.
- [x] **The dump banner check accepted executable SQL above the banner.** *(Codex)* The preamble
  was read line by line, so a line that both opened and closed a comment was skipped whole and
  nothing after the `*/` was examined. `/* comment */ DROP DATABASE unrelated;` above a real
  banner passed `verify_dump()`, and `plan_restore()` takes its safety dump only for databases
  the census names on their own lines — so that DROP had no backup behind it. The scan now
  walks bytes.
- [x] **A partially pruned build read as a finished one.** *(Codex)* Only the first image whose
  name contains `worldserver` was tested, so a prune that took `ac-db-import` still answered
  "built" and the reuse path ran `up` with no `--build`. Every image sharing the worldserver
  image's prefix must now be present — and third-party images are deliberately excluded,
  because `mysql:8.0` is pulled rather than built and requiring it would report a good build as
  missing, which is the delete prompt again.
- [x] **Return code 3 (somebody else's compose project) had no branch in the caller** and fell
  through to "Remove it and start fresh?" — an `rm -rf` offer on a folder just identified as
  not ours.

**Refuted, deliberately not applied.** Both were recommended by a review and both are wrong:

- **decision** — *"`InFlight.sweep()` must `wait()` before releasing a worker."* Three
  independent refuters showed `InFlight` does not own the update thread's C++ side, so the wait
  buys nothing and risks blocking the GUI thread on a job that is still working.
- **decision** — *"Canonicalise the install dir with `realpath` before comparing."* Compose
  records the *logical* working dir, so canonicalising makes the guard disagree with the label it
  is comparing against.

---

### 3. Fedora — SELinux

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

### 4. Arch — prerequisites

- [x] **The launcher will not start from source on a stock Arch desktop.** Qt names one missing
  library; `ldd` shows **six**: `xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image
  xcb-util-renderutil libxkbcommon-x11`. The README lists no Linux prerequisite. `libxkbcommon-x11`
  is one of the five sonames `check-bundle-closure.sh` already caught missing from the shipped
  tarball — same library, hit again on a path that gate does not cover.
  Fixed in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136): all six sonames listed in `pylauncher/README.md`, with the package names for both families.
- [x] **Document the Debian-family equivalent** (`libxcb-cursor0` and friends), found on m910q.
  Done in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136), alongside the Arch list.
- [x] **Document that LAN setup is only partly automatic on Arch** — `detect_firewall()` returns
  `"none"` (no `ufw`, no `firewall-cmd`) and the app degrades gracefully to manual instructions.
  Correct behaviour, but "networking works" means something weaker there.
  Done in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136) — the README now says what "networking works" means on a box with no `ufw` and no `firewall-cmd`.

### 5. Windows

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
- [x] **Tab titles collide.** `main.py:220` titles a tab `server_dir.name`, and the installer always
  suggests the same leaf name — two installs in different parents are indistinguishable in the tab
  strip. Not data loss: Stop is ownership-protected by compose labels and refuses across installs.
  Fixed in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136): `yulon/ui/tab_titles.py` distinguishes two installs whose leaf names match, without turning the tab strip into a path dump.

---

### 6. Decisions — not bugs to fix

- [x] **DECIDED 2026-08-29, by the owner: only one server runs at a time.** Every game in the
  catalog publishes the same ports, so this is the design rather than a limitation, and it means
  the ports never have to move. Both guards now say so and act on it: the installer asks
  "Stop the other server and continue?" before the compile, and the launcher offers the same on
  the tab when Start is refused. Accepting stops the whole compose project of the install holding
  the ports, not just the containers publishing them.
  The `YULON_AUTH_PORT` / `YULON_WORLD_PORT` / `YULON_DB_PORT` overrides stay as an escape hatch
  for anyone who genuinely wants two live at once — proven working on yulon-fedora, where Vanilla
  and WotLK ran side by side — but they are no longer the advertised answer. The reason that is
  an escape hatch rather than the default: **the game client dials the AUTH port out of
  `realmlist.wtf`**, so moving it silently would produce a server nobody could log in to.
  See [#128](https://github.com/DadsMmoLab/dads-mmo-lab/pull/128).

- [x] **Self-update has never worked for anyone.** `update.py:26` calls `/releases/latest`, which by
  definition excludes prereleases — and **every** upstream release is flagged `Pre-release`
  (`v0.6.57Public`, `v0.6.55Public`, `v0.6.53`). Two independent fixes, not exclusive:
  **code** (call `/releases?per_page=5` and take the first non-draft — works with the release
  practice as it is), and **process** (`gh release edit … --prerelease=false`, needs upstream write
  access). *Which depends on whether 0.6.x is meant to be pre-release.*
  Code half fixed in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136): `/releases?per_page=5`, first non-draft. The process half (`gh release edit ... --prerelease=false`) still needs upstream write access and the owner's answer on whether 0.6.x is meant to be pre-release.
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

### 7. Coverage — what round 1 exercised, and what is owed

Installed for real: **WotLK** via apt (Ubuntu), pacman (Arch) and the **native engine**
(Windows — first time ever, 72-minute build into a path with spaces, `ac-db-import` exited 0,
which settles the 9p question open since 2026-08-25). 500 bots verified four independent ways on each — but see section 9: that count came
from the test branch, and every shipping branch still says 1600-2000.

- [x] **Fedora's dnf variant has never completed a fresh compile** — CLOSED 2026-08-29, see section 9. — it reached 83% at 8 GB and
  swap-thrashed the box unreachable, so every Fedora finding so far comes from an *adopted*
  install rather than one the script built. This is the hole in round 1 and it is owed by
  Fedora's own pass at 23 GB.
- [x] **Tortoise, Vanilla and TBC have never been run** — Vanilla now HAS, end to end on Arch; TBC is mid-compile; Tortoise still owed. See section 10. — rounds 2, 3 and 4, in that order.
  See [`hunt-rounds.md`](hunt-rounds.md). Tortoise is WSL-only, so Windows is the only box that
  can host it. The Vanilla and TBC clients are downloading to yulon-arch directly from
  `wow.baerthe.com`; the copies on m910q are unreachable while that box is offline. **Vanilla's
  script refuses under 20 GB on both the target disk and Docker's root**, so prune between
  installs on the same box.

---

### 8. Round-4 hunts, 2026-08-29 — Arch, Windows 11, Fedora

Four lenses per box, run as workflows, every finding handed to a second agent whose job was to
REFUTE it. Only what survived is below; what did not is recorded at the end so it is not found
again. Two of these were downgraded by their own verifier, which is the process working.

**Arch**

- [x] **HIGH — Two of the four games' Install buttons are off the screen at the default window
  size.** `catalog_view.py:232` builds the tile description as a plain `QLabel` with no
  `setWordWrap(True)`, so each tile's width is set by its longest unwrapped line — WotLK's is 118
  characters. The 2-column grid (`catalog_view.py:213`) then demands roughly 1550—1630 px, and at
  the app's own default size a horizontal scrollbar is present from the first frame, with WoW TBC
  and WoW Tortoise pushed out of the viewport. Scrolling fully right showed their Install buttons
  as blank outlined rectangles with the labels still clipped beyond the splitter. Clicking blind
  inside one DID open the native folder picker ("Where should WoW TBC be installed?"), so the
  control works — it is simply invisible. The sibling `note` label twelve lines further down
  (`catalog_view.py:244-249`) does call `setWordWrap(True)`, so this is an omission rather than a
  design. Reproduced identically in the shipped v0.6.51 tarball.
  Fixed in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136). Wrapping only the description was NOT enough — it left the grid at 1533px against a 661px viewport, because the unwrapped `Emulator:` line then set the width. Every line of a tile now wraps, and the test asserts button geometry at `main.DEFAULT_WINDOW_SIZE` rather than that a property is set.
- [ ] **MEDIUM — On a screen narrower than 1100 px the window's own restore button strands its
  close button.** `main.py:306` calls `window.resize(1100, 750)` with no check against
  `QScreen.availableGeometry()`, and it is the only `resize()` in the package — there is no
  `QSettings`/`restoreGeometry` anywhere. On a 1024x768 desktop xfwm4 silently auto-maximises the
  oversized request, but the stored normal size stays 1100x750, so one click on the title bar's
  restore toggle moves the window to 1100x750 at position 5,56: title-bar minimise/maximise/close
  leave the right edge, and the log panel's Stop button (`log_panel.py:88`) is reduced to an
  18-pixel sliver. **Downgraded from high by the verifier**: a double-click on the still-visible
  title bar restores it (xfwm4's `double_click_action = maximize`), the window keeps a normal
  taskbar entry, and because no geometry is persisted a relaunch comes up correct. The reporter's
  "1280x720 is also affected" is wrong for the close button — at 1280 px wide only the bottom is
  lost.

**Windows 11**

- [x] **MEDIUM — An unwritable config directory kills startup with no message at all.**
  `main.py:410` calls `configure(config_dir=platform.config_dir())` as the FIRST statement of
  `main()`, before `QApplication` exists; `log.py:87` then constructs a `RotatingFileHandler` on
  `%APPDATA%\yulon\yulon.log` with no `try`/`except` anywhere between `__main__` and the
  handler. Denying write access with `icacls` and running the real entry point gave
  `PermissionError: [Errno 13]` and exit 1 with no window. The verifier made it worse and
  narrower at the same time: `build/pylauncher.spec:65` sets `console=False`, so the SHIPPED exe
  produces **no window, no dialog and no stderr at all** — not a visible traceback. Downgraded to
  medium because every trigger (managed corporate profile, read-only roaming share, redirected
  AppData) is a machine nobody compiles AzerothCore on, and an unset `APPDATA` falls back to
  `~/AppData/Roaming` and works. `save_state()` fails the same way at `main.py:248` and `:263`,
  but those are Qt slots, so they degrade a running app rather than preventing launch.
  Fixed in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136): the app starts, logs where it can, and says so once — which is what `console=False` in the shipped exe made impossible before.
- [x] **MEDIUM — Every `docker info` probe is unbounded, so a hung Docker CLI defeats the stated
  timeout.** `platform.docker_ready()` (`platform.py:1194`) defaults its runner to
  `lambda argv: runner.run(argv)`, and `runner.run()` (`runner.py:237`) defaults `timeout=None`, so
  the subprocess call never returns while the CLI hangs. `_wait_docker_ready()`
  (`platform.py:1789`) only tests its deadline BEFORE each call and only tests `cancel` AFTER one
  returns, so neither bounds the call in progress. Measured with a `docker` stub that runs
  `ping -n 999`: a 5-second budget was still inside the call at 32 seconds. No caller supplies a
  bound — `installer.py:442`, `native.py:270` and `preflight.py:125` all default to the bare
  probe, and the real GUI install path reaches it through `installer.py:572` and `native.py:427`.
  The module's own convention is the strongest argument against it: `platform.py:304` bounds
  `detect_alf_state` at `timeout=5.0` with the comment "Bounded, like every other probe in this
  module".
  Fixed in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136): the bound lives at the probe, where every caller that matters reaches it in its bare form, and `_wait_docker_ready()` can no longer overshoot its budget by the length of one hung call.

**Fedora** (found while preparing the box, before its hunt started)

- [x] **HIGH — Two games on one machine collide on the world port, and nothing checks first.**
  `vanilla-install.log` on yulon-fedora ends with
  `Bind for 0.0.0.0:8085 failed: port is already allocated` followed by
  `Failed to start mangosd/realmd`, against the WotLK stack that was already running there. The
  install had already cloned, compiled and imported by that point. This also corrects the coverage
  note above: **Vanilla HAS been run**, on Fedora, and this is how it ended.
  Fixed in [#128](https://github.com/DadsMmoLab/dads-mmo-lab/pull/128), before the compile rather than after it, with the offer to stop the other server. This box was left unticked when that merged.

**Refuted this round — do not report again**

- *"The collision guard never runs on the Linux script path."* True of the branch the Arch box was
  sitting on (`test/full-vm-run-2026-08-28`), already fixed on `fix/ubuntu-baseline`, and a
  restatement of item 2.1 besides. **Lesson: the test boxes hold stale checkouts, so a hunt must
  name the branch it judged.**
- *"Accepting 'Remove it and start fresh?' deletes any non-empty directory."* The verifier proved
  the mechanism is real and worse than filed — it built a live foreign compose project in a
  throwaway folder, ran the branch, and lost the container, the named volume and an unrelated file.
  But it is only reachable through `--reinstall`, which exists solely on `_main()`, whose own
  docstring calls it "the roadmap 3.2 test harness". Through the GUI `catalog_view.py:445` never
  sets `reinstall`, and `PROMPT_RULES` answers "n". A developer-harness hazard, not a user one.

---

### 9. Fedora round 4, 2026-08-29 — and the fresh compile that finally finished

**The round-1 coverage gap is closed.** `install-wow-wotlk-fedora.sh` completed a genuine fresh
compile on a 23 GB box for the first time: 1829 CXX objects over about 32 minutes, all five
containers up, `ready...` at 00:35:26Z, bots logging in to completion. Peak memory 8971 MB
(compile stage 6289 MB), peak swap 134 MB from the one-time DB import, `si=so=0` throughout the
`-j9` build, and no SELinux AVC denials. The 2026-08-25 failure at 8 GB was a resource floor, not
a broken script — **but nothing here proves the 8 GB floor is survivable**, only that 23 GB is.

- [ ] **HIGH — `wait_for_server()` can never report readiness on Fedora.** The loop runs
  `docker logs $WORLD_CONTAINER 2>/dev/null | grep -q "ready\.\.\."`. `grep -q` exits on the FIRST
  match, `docker logs` then dies of SIGPIPE, and `set -o pipefail` — which only the Fedora script
  sets, at line 135 — turns the whole pipeline's status into 141, so the `if` is never true.
  Measured 10 runs out of 10 returning 141, against a log that genuinely contains the string on
  line 1946. Live consequence on the fresh install: the worldserver was ready at 00:35:26Z and
  fully populated by 00:45, and the installer kept printing dots for the entire 1800-second
  timeout before announcing "Server is taking longer than expected" — 27 minutes after the
  server was, in fact, ready. `install-wow-wotlk.sh` and `install-wow-wotlk-ubuntu.sh` do not set
  `pipefail`, so this is Fedora-only. Verified against `fix/ubuntu-baseline`, not a stale branch.
- [ ] **HIGH — A fresh, empty folder skip-compiled against another install's images.** Hit for
  real while setting up the compile above: four `acore/ac-wotlk-*` images left behind by the
  ADOPTED install on that box made `compiled_images_present()` answer "built" for a brand-new
  empty directory, so the first attempt skipped the 32-minute compile entirely and brought up a
  stack on images from a different install. Only after those images were deleted did a real build
  run. This is the scope note at lines 224-229 of the script — `image:` is pinned with no project
  prefix, so the question the function can actually answer is "is this image on this machine",
  not "did this folder build it". It has now happened, unprompted, on a real box. The
  fix that landed this week (requiring every image sharing the worldserver image's prefix) does
  NOT cover this case: all four images were present, just built by something else.
- [x] **HIGH — The 500-bot decision never left the test branch.** The owner set
  min = max = 500 in the six places that decide the number, and that commit
  (`c5c7d20 Five hundred bots, in the six places that decide the number`) is contained in exactly
  one branch: `origin/test/full-vm-run-2026-08-28`. Every other branch, `upstream/Yulon` included,
  still says `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS: "1600"` / `MAX: "2000"` in all three installers and
  in `catalog.json`, and `tests/test_composegen.py:424-430` PINS 1600/2000, so restoring 500 also
  fails a green test. The fresh Fedora install proves what actually ships: `1633/1633 Bot Reyna
  logged in`.
  Fixed in [#136](https://github.com/DadsMmoLab/dads-mmo-lab/pull/136): the owner's own `c5c7d20` landed on a branch that ships, verified place by place against that commit.
- [ ] **LOW — The Modules tab opens with nothing selected and both action buttons enabled.**
  `controller_view.py:1307` `_module_action()` returns early when `selected_manifest()` is None,
  writing no message and no log line, so "Install selected" and "Remove selected" do nothing at
  all. The verifier made the reachability stronger than reported: `_build_modules_tab`
  (`:1276-1277`) gates enablement only on `store` and `applier` being present, never on
  selection, and nothing in the package ever calls `setCurrentRow`/`setCurrentItem` — so this is
  the tab's state on EVERY visit, not a user who forgot to select. Nothing wrong runs; it is the
  silent-control failure mode. The same view's own tests already assert the correct idiom for the
  backup list.

**Refuted this round — do not report again**

- *"The catalog designs port collisions in for 3 of 4 games."* The ports are upstream's, not
  Yu'lon's: the WotLK env vars come from AzerothCore's own compose file. What survives is smaller
  and worth keeping: **no game exposes a port setting anywhere in the product**, so a user with
  two games installed has nothing to change.
- *"A real SELinux denial leaves no AVC trace even with dontaudit disabled."* `ausearch` on that
  box is broken for ALL events, so an absent record proved nothing about denials.
- *"The Networking tab shows catalog ports rather than the install's live ports."* The divergence
  measured was the hunting agent's own staged port remap, not something the app can produce.

---

### 10. Arch installer marathon, 2026-08-29 — Vanilla runs, TBC is running, Tortoise did not start

**Vanilla completed end-to-end for the first time.** Driven through the launcher's own
`Installer.run()` on `fix/ubuntu-baseline`, not the bash script standalone: client validation,
mangos-classic + playerbots compile, map/vmap extraction, mmap generation for every map (Eastern
Kingdoms 687 tiles, Kalimdor 1018, plus instances and battlegrounds), all four schemas, then
`vanilla-db` / `vanilla-realmd` / `vanilla-mangosd` healthy with 8085 and 3724 bound. About **30
minutes** on 8 vCPU — against the script header's own estimate of 3-5 hours, which was written
for Steam Deck hardware.

**TBC then FINISHED on this box, and it is healthy.** Started through `Installer.run()` with a
real 2.4.3 client and left running detached, it completed unattended: `tbc-db`, `tbc-realmd` and
`tbc-mangosd` all running with **RestartCount 0**, ports 3724 and 8085 bound, and the world
ticking at Avg Diff 73. Checked deliberately against the Ubuntu finding that a restart-looping
mangosd gets reported as success — this one is genuinely up, not that failure mode. **First
completed TBC install on any machine.**

**Tortoise never started.** Its client could not be fetched at the time — see the refutation below,
which found the way through.

- [ ] **MEDIUM — The prompt table answers "yes" to the extraction failure that matters most.**
  `installer.py:189` has `PromptRule(r"Continue anyway\?", "n", "the script found the wrong client")`.
  The Tortoise script has TWO extraction guards: line 761 `"Continue anyway? (server may not boot
  without maps)"`, which matches and is correctly declined, and line 771 `"Continue and try to
  start anyway?"`, reached only when the extractors exit 0 and still produce zero `.map` or `.dbc`
  files. The words "and try to start" break the literal substring, so the second falls through
  every rule to the blanket `PromptRule(r"\(y/n\)", "y")` catch-all at `installer.py:242` and is
  answered YES. Reproduced with the shipped `make_responder()` against the exact strings
  `ask_yes_no` prints, including the colour codes: the first returns `n`, the second returns `y`.
  There is one rule table and one call site, and `tests/test_installer.py:95` pins only the
  vanilla/TBC wording, which is why the Tortoise wording was never noticed.
  **Downgraded from high by the verifier, for two reasons worth keeping**: the trigger the reporter
  named (wrong client, corrupt zip) actually routes to the FIRST prompt, which is declined
  correctly, because the script sets `pipefail` and a failing extractor makes the whole pipeline
  non-zero; and it is not silent — `start_server()` greps the world log for
  `Correct \*.map files not found` and prints the failure with `docker logs --tail 15`.
- [ ] **MEDIUM, unverified — `"Build mmaps now? (skip = faster)"` has no rule of its own**, so an
  app-driven install always takes the 1-3 hour path with no visibility into which branch it took.
  Reported by the install agent; the round's two verification slots went to the findings above, so
  this one has not been through a refuter yet.

**Refuted this round — do not report again**

- *"The Turtle client download is hard-blocked by Google Drive's quota page."* It is not blocked.
  Drive returns the 2009-byte quota page for **unbounded** GETs — which includes `curl -C -`, since
  that sends an open-ended `Range: bytes=N-` — while **bounded** `Range: bytes=start-end` requests
  serve the file normally. The refuter proved it by pulling real chunks. The host copy is now
  being assembled in 256 MB ranges, and any future fetch of a large Drive file must do the same.
- *"The script's REQUIRED post-install account-creation step fails every install."* The message is
  `Account with this name already exist!` — the account was already there from an earlier run. Low,
  and only a wording problem: the script never says that outcome is expected.

---

### 11. Ubuntu installer marathon, 2026-08-29 — Vanilla again, TBC for the first time anywhere

**Vanilla won here too**, independently of Arch: a real from-scratch compile (~15 min, no cached
image), extraction, 44 maps of pathfinding mesh (~62 min), a validated database import, and a
live server ticking with Playerbots moving and casting in the world.

**TBC ran for the first time on any machine.** Driven through `Installer.run()`, the same path as
the Install button: client validation, CMaNGOS TBC + Playerbots compile (~18 min against a stated
2-4 hours), and map+vmap extraction of all 83 maps, validated. mmap generation was still running
when the hunt ended; it was left detached and is not yet observed reaching database setup or
server start.

**Tortoise compiled in about 5 minutes** and then correctly refused to go on without a real
client. The agent continued past `locate_client()` with a synthetic placeholder purely to keep
exercising the pipeline — so everything below about Tortoise is a defect in the CODE PATH, found
either live or by running the launcher's own production prompt-answering code, not a claim that
a real Tortoise server was ever started.

- [ ] **HIGH — A finished build is wiped by a prompt the launcher answers "yes" to.** Vanilla AND
  TBC both ask "Start completely fresh instead?" when they find an existing install, and no
  `PromptRule` covers it, so the blanket `(y/n)` catch-all consents. The verifier confirmed the
  blast radius: folder, volumes AND image, followed by a forced recompile. A GUI-driven re-install
  therefore destroys a good build every time, and re-earns an hour or more of compile.
- [ ] **HIGH — `install-wow-tbc.sh` can never report readiness, on EVERY Linux distro.** The same
  mechanism as the Fedora WotLK finding in section 9, but wider: TBC sets `set -o pipefail`
  UNCONDITIONALLY at line 52, so `docker logs | grep -q` dies of SIGPIPE, the pipeline reports
  141, and "ready" is never seen anywhere — not just on Fedora. Two scripts now share this bug and
  it should be fixed as one thing.
- [ ] **HIGH — A TBC server whose mangosd is stuck in a restart loop is reported as a fully
  successful install**, end to end through the GUI.
- [ ] **MEDIUM — Tortoise advertises an account it can never create.** `create_default_account()`
  runs `printf 'account create player player\n' | timeout 20 docker attach tortoise-mangosd`, and
  the compose service declares `stdin_open: true` + `tty: true`. `docker attach` refuses a piped
  stdin against a tty-enabled container: `the input device is not a TTY`, exit 1. The script
  discards that with `>/dev/null 2>&1`, takes its "didn't confirm" branch, and `show_completion`
  still prints `Account: player / player` unconditionally. Reproduced against a container built
  with the same flags, from a real terminal, with the container receiving zero bytes. Corroboration
  that this is a defect rather than a choice: the Vanilla script deliberately does NOT auto-create,
  with a comment explaining that only the mangosd console knows the SRP6 math.
- [ ] **MEDIUM — A dead Tortoise server is recorded as an installed one.** A detected mangosd
  start failure only `return 1`s; the script has no `set -e`, so it runs on to print
  "TORTOISE WOW INSTALLED!" and exit 0. The app then records the install and pins a tab for a
  server that is not running.
- [ ] **MEDIUM, unverified — The compiled-image reuse check trusts one global fixed tag**, reported
  independently for BOTH Vanilla and TBC. This is now the **third** sighting of that class in two
  days: WotLK's `compiled_images_present()` (section 9, seen live on Fedora), Vanilla, and TBC. It
  is one design defect in three scripts, not three bugs.
- [ ] **LOW, unverified — `MoveMapGen` runs from the wrong working directory**, so every mmap tile
  silently skips off-mesh connections. Silent: the generation reports success either way.

**Refuted this round — do not report again**

- *"The host's client SMB share refuses every documented auth mode."* Correct as an observation and
  **my own setup error, not a product defect**: the host has Guest disabled and the share was
  created with no password, so `-o guest` could never work. The hunting instructions said to use
  it. Fixed by using the HTTPS URLs instead; a future round needs either credentials on that share
  or a plain HTTP server on the host.
- *"start_server()'s readiness check is broken."* For Vanilla the real story is smaller and worth
  keeping: the 600-second timeout is simply shorter than a real Vanilla + Playerbots boot, measured
  at **10m41s**, so a perfectly successful install ends on a false "taking longer than 10 minutes"
  warning.

---

### 12. Tortoise on m910q, 2026-08-29 — the last game, and a defect in that morning's fix

**Tortoise has been installed and running since 2026-08-26**, on the physical m910q box, at
`/home/pk/tortoise-wow-server`: all three containers up with RestartCount 0, ports 3724, 8090 and
3306 bound, mangosd running its realm loop. That corrects "Tortoise has never been run" — it had,
on the one machine nobody had looked at, which is also the only machine that already had the
Turtle client (9.3 GB, extracted, 172 files).

The launcher was then driven against a live Tortoise server for the first time — adoption, status
polling, the console, log streaming, accounts and GM level against a **MaNGOS-Zero** schema
rather than AzerothCore's, backups with `verify_dump()` and a restore-plan refusal, and the
networking plan — under Xvfb, with screenshots and matching database checks. The stack was never
stopped and stayed healthy throughout.

- [ ] **LOW — The port refusal claims more than it can know.** The guard prints
  "Nothing has been changed" while, on the Tortoise path, an **842 MB clone and a 748 MB image
  build** have already happened. Corrected the same day, together with the placement bug below.
- [ ] **LOW — For the first poll interval a controller tab says "status: unknown" with Start
  still enabled against a server that is already running.** `controller_view.py:325-335` starts a
  QTimer at `status_poll_ms = 5000` and never calls `refresh_status()` itself, so the first tick
  is a full five seconds away; the label starts at "status: unknown" (`:342`) and enablement is
  only ever written inside the status handler (`:494-495`). **Downgraded from high by its
  verifier**, which reproduced the window but refuted the harm story: Stop's enablement is not
  affected by it, so the "user reflexively stops their live worldserver" scenario does not hold.

**A defect in the port fix itself, found by this hunt and fixed the same morning**

- [x] **`check_ports_free` was written into the wrong line of the main sequence.** It landed
  AFTER `do_compile` in Vanilla and TBC — so it would have refused the install having just spent
  30-70 minutes building it, while printing "no compile was started" — and after `clone_source`
  in Tortoise. **Every test written alongside it passed**: they proved the function's behaviour
  on free ports, taken ports, the unanswerable case and the overrides, and said nothing about
  where it was called. It took an agent running the real installer, watching a clone finish and
  then reading "Nothing has been changed", to see it. The call now precedes the expensive step in
  all three, and a test asserts the ORDER in the shipped file; putting the call back by hand makes
  that test fail.

**Refuted this round — do not report again**

- *"mmaps were never generated."* True of that install, but it was the hunting agent's own answer
  file saying `n` to "Build mmaps now?", not anything the product did.
- *"Playerbots are configured to spawn zero."* Upstream's shipped default in the PlayerBot config
  block, not something the installer writes.
- *"A console command prints its answer twice."* Real output, but neither half is a launcher
  defect and the mechanism is already documented in the code the reporter cited.

---

### 13. Windows 11 through WSL, 2026-08-29 — two games run, and nothing survived verification

A real WSL2 distro was stood up on yulon-win11 for the first time (Ubuntu 26.04; the box had only
`docker-desktop` before), and both games were run inside it.

**Vanilla completed end to end on Windows/WSL2**, through its own "VANILLA WOW INSTALLED!"
banner: client fetched, ~26 minute compile, extraction, and mmap generation for every Vanilla map
(Eastern Kingdoms 687 tiles, Kalimdor 1018, instances and battlegrounds). **Tortoise's WSL2
branch of `install_docker()` passed for the first time ever**, and its full compile finished in
about 13 minutes for ~3400 files, with the schema (190 base files + 123 updates) importing
cleanly. Tortoise's mangosd correctly refused to boot for want of real map data, since the client
could not be fetched on that box.

**Six findings went to verification and all six were refuted.** That is worth as much as a
confirmation, and two of them are worth knowing about:

- *"The WSL2 VM restarts repeatedly, even mid-workload, destroying long-running container jobs."*
  Filed HIGH. It is a property of the harness, not the product: an unattended workload inside a
  hand-installed WSL distro dies when its `wsl.exe` session exits, under the default
  `vmIdleTimeout`, and that looks exactly like a VM recycle. **Operational lesson for any future
  round: work started inside a WSL distro over ssh needs something holding the session open.**
- *"install-wow-vanilla.sh declares success while the server is crash-looping."* Also filed HIGH,
  and a consequence of the first: what remained once the harness artefact was removed is the
  already-recorded fact that Vanilla's 600-second readiness timeout is shorter than a real
  Playerbots boot, so a healthy install can end on a false warning.

The rest were a restatement of checklist item 5.2, a stopped WSL distro correctly reporting no
servers, and the `docker_data.vhdx` prune accounting — which is a measurement note, not a defect:
on that box the vhdx is non-sparse, so `docker system prune` reported ~10 GB reclaimed while C:
gained about 1 GB. **Budget disk from `Get-PSDrive`, never from prune output.**

One finding did NOT reach a verifier and stays open on that basis:

- [ ] **MEDIUM, unverified — "Existing install found" fires on a directory that is completely
  empty**, a false positive on the very first run. Reported by the Vanilla-on-WSL agent; the
  round's two verification slots went to the two HIGH findings above.

---

### 14. Tortoise everywhere, and TBC on Windows — 2026-08-29

**Tortoise now installs on Fedora, Arch, Windows/WSL2 and the physical m910q box**, all with the
real Turtle 1.18.1 build 7272 client. On Fedora it came up under SELinux enforcing; on Arch,
without passwordless sudo; on Windows it is the first Tortoise anywhere to reach a full install
with real client data through the WSL2 branch of its own script — world server up in 11 seconds,
all three ports bound, RestartCount 0 across two independent starts. **TBC also completed on
Windows**, 2h19m end to end, all 83 maps including Outland.

- [ ] **HIGH — The readiness check greps for three strings the server cannot print.**
  `install-tortoise-wow-wsl.sh:1102` waits up to 600 seconds for
  `World initialized|MaNGOS.*started up successfully|Ready to login`. The core prints
  **`World server is up and running! Loading time: 0 minutes 11 seconds`**. The verifier settled
  it at the source rather than the log: `src/game/World.cpp:2399` of the cloned repo is the
  `sLog.outString("World server is up and running! ...")` call, and a tree-wide grep of the core
  for the three alternatives yields exactly one hit — a C++ comment in `realmd/Main.cpp` — so the
  binary cannot emit any of them. Confirmed on two independent boxes, including a 75,417-line log
  on m910q with zero matches. The sibling failure grep does not fire either, so the loop genuinely
  burns all 600 seconds and then warns "Server slow to report ready" about a server that was up in
  seconds. Present identically on all five branches checked.
  **This is the FOURTH sighting of one class** — a readiness pattern that does not match the core's
  actual vocabulary — alongside 9.1 (WotLK on Fedora) and 11.2 (TBC everywhere). They should be
  fixed as one thing.

**Refuted this round — do not report again**

- *"A healthy TBC server does not persist on Windows: it dies ~3 minutes after the WSL session
  goes idle."* The teardown is WSL2's own per-distro idle behaviour, not the installer's, and the
  box's `.wslconfig` had `vmIdleTimeout=-1` added earlier the same day for exactly this reason.
- *"`sudo rm -rf $SERVER_DIR` fails silently on a box with no passwordless sudo while printing
  'Old install removed'."* The branch is never entered by the product: `PROMPT_RULES` answers
  "n" to "Remove it and start fresh?" unless `--reinstall` is set, and that flag exists only on
  `_main()`, whose own docstring calls it "the roadmap 3.2 test harness". The verifier staged the
  reporter's worst case — a non-empty `src/`, a stale `.db_password`, a half-written compose —
  drove the real launcher path, and got "Keeping existing install — exiting" with `grep -ic sudo`
  on the run log returning **0**. The reporter's own driver had hardcoded "y".

**Two defects in the port fix, found in the field and fixed the same day**

- [x] **The offer stopped the port holders instead of the server.** On yulon-fedora it stopped
  `ac-authserver` and `ac-database` and left `ac-worldserver` running with its database gone —
  `restart: unless-stopped` looped it to RestartCount 18. Seen again on yulon-arch, where
  `tbc-mangosd` and `tbc-db` were left up after `tbc-realmd` went, and reported there as the fix
  *working correctly*, which is how a half-applied fix reads from outside. The unit is now the
  compose project, in both the GUI path and all six scripts.
- [x] **The Turtle tarball served 7.23 GB of a 9.98 GB tree for hours.** A test-fixture fault, not
  a product one, but it cost a round: the tar died with the ssh session that started it, nothing
  checked the result, and its Content-Length matched the truncation so `curl` had no reason to
  complain. Two agents downloaded it twice each and got byte-identical corruption that reads
  exactly like a network fault. It is now built to a temporary name by a detached task and only
  renamed into place after verifying the archive is larger than its own payload.

---

### 15. The matrix is closed — 2026-08-29

**Every game installs on every operating system.** Four games, four platforms, each one driven
for real rather than reasoned about:

| | Ubuntu 24.04 | Fedora 44 | Arch | Windows 11 |
| --- | --- | --- | --- | --- |
| **WotLK** | yes | yes, fresh dnf compile | yes | yes, native engine |
| **Vanilla** | yes | yes | yes | yes, through WSL2 |
| **TBC** | yes | yes, SELinux enforcing | yes | yes, 2h19m |
| **Tortoise** | yes | yes, SELinux enforcing | yes, no passwordless sudo | yes, its own WSL2 branch |

Tortoise also runs on the physical m910q box, where it has been up since 2026-08-26.

The Ubuntu run found **nothing new**, which is worth recording as a result rather than a blank:
it was the fourth Tortoise install of the day, on the reference platform, driven through the real
`build_window()` — adoption via "Use existing...", live status, and 204 lines of streamed
worldserver log — and everything it touched was already known or already correct.

**The port guard's second fix, validated in the field**

The one thing this run could do that no earlier one could: exercise the whole-project stop
against a real, unrelated running server. A `wow-tbc-server` stack was publishing 3724, the exact
port Tortoise needs. `check_ports_free()` found the collision, read `tbc-realmd`'s compose project
label, and stopped **all three containers of that project** — `tbc-realmd` (exit 0), `tbc-mangosd`
(exit 137, force-killed after ignoring SIGTERM) and `tbc-db` (exit 0), all inside a 13-second
window. Nothing was left running, and nothing was left restart-looping the way the half-applied
version left `ac-worldserver` on Fedora and `tbc-mangosd` on Arch earlier the same day.

Both of those earlier hunts predated the fix, so this is the first time the corrected behaviour
met the situation it was written for.

---

### 16. Fixed by Baerthe — macOS, 2026-08-29

Found and fixed independently of the four-platform hunt, on the platform none of these boxes
can reach. Recorded here in his own words, with his own verification notes, because this is one
list across every round and that has to include the rounds other people ran.

- [x] **ContainerGit error formatting produced glued string literals on failures**
  `yulon/git.py:507-510`
  `_capture()` had two adjacent f-strings without separator, causing exit codes and error messages
  to implicitly concatenate into garbled strings. Fixed with single clean error formatting.
  *Verified with unit tests in `test_git.py`.*
- [x] **ContainerGit inherited launchd file descriptors from GUI processes on macOS**
  `yulon/git.py:501`
  Running containerized git from Finder/.app spawned subprocesses inheriting non-standard stdin,
  which could interfere with docker child execution. Fixed by passing `stdin=subprocess.DEVNULL`
  to `runner.run()`.
  *Verified with unit tests in `test_git.py`.*
- [x] **Darwin sleep assertions failed to terminate on worker exit**
  `yulon/platform.py`
  `platform.keep_awake()` spawns `caffeinate -dims -w <pid>` on macOS; verified proper process
  lifecycle termination upon context exit.
  *Verified with unit tests in `test_platform.py`.*
  **Scope of that word, added 2026-09-03.** The unit tests DID run on Darwin (1039 passed on the
  M4 Pro, checklist 6.4), which is more than most of this list can say -- but they drive a fake
  `spawn`, so what is verified is the argv and the cleanup call, not a `caffeinate` process
  existing. `platform.py:3746` says it plainly: "neither claim has been executed on a Mac by this
  project." Nobody has yet watched a real install hold a Mac awake.

The same commit added `WotlkController` tests for `STOP_GRACE_SECONDS` teardown and for the
port-conflict guard on 3724 and 8085. Those tests and the port work in
[#128](https://github.com/DadsMmoLab/dads-mmo-lab/pull/128) landed in the same file from two
directions on the same day; both are kept.

---

### 17. Found by diffing two real `docker compose config` captures — 2026-08-31

Both captures are committed under `pylauncher/tests/data/`: the engine's own install on Ubuntu
after it reached `ready`, and a bash-installer server still standing on Fedora. Neither of these
was found by reading code; each is a line in a resolved compose document from a box that ran.

- [ ] **`ac-client-data-init` runs on compose's implicit `default` network, not `ac-network`.**
  `catalog/installers/wow-wotlk/docker-compose.yml.tmpl` — the service declares no `networks:`, so
  compose materialises a SECOND per-project bridge for it and every install ends up with two
  networks where the file names one. Harmless today: it fetches an archive into a named volume and
  talks to no other service. **Inherited, not introduced** — the bash script install does exactly
  the same thing, which is why nobody had noticed.
  *Recorded, not fixed, and the fix carries an obligation:* adding `networks: [ac-network]` changes
  what `docker compose config` resolves, so the committed native fixture
  (`tests/data/wotlk-compose-config.json`) and the byte snapshot under `tests/data/wotlk-rendered/`
  must BOTH be re-captured in the same commit, and the re-capture has to come off a real install —
  a hand-edited fixture is not a capture. `test_the_synthesised_default_declaration_is_modelled_and_not_erased`
  is what will go red first.

- [ ] **The captured shape of the SELinux label is not what the shipped Fedora script writes, and
  the z-vs-Z question it looked like it was raising is already answered in that same script.**
  `install-wow-wotlk-fedora.sh`'s override literally writes `:z` on SIX binds (worldserver,
  authserver and db-import's `env/dist/etc` and `env/dist/logs`, L1758-1759/1770-1771/1777-1778)
  and `:Z` on ONE (the worldserver's `./modules`, L1740) — but the captured fixture
  (`wotlk-compose-config-script.json`) shows `bind: {selinux: Z}` on that one modules mount and
  **no `selinux` field at all** on the other six, not `:z`. That exact shape — six labels
  stripped, one untouched — is precisely what `selinux_drop_z_from_override()` (L416-425) leaves
  behind: its `sed` matches only `/azerothcore/env/dist/*:z` and never touches `./modules:...:Z`.
  It runs when SELinux is enforcing on a filesystem that cannot hold labels; the Fedora VM
  (`yulon-fedora`) this capture came from is confirmed enforcing (§3 and §9 above), but the same
  VM's `stat -f -c %T ~` is recorded elsewhere as `xfs`/`btrfs`
  (`pyplan/phase7-plans/7.1-spine-azerothcore-linux.md`, the E.4 Fedora gate setup step), which
  `selinux_labels_supported()` treats as label-capable — so the drop path is not obviously why
  this particular box produced this shape, and this list cannot pin the mechanism down further
  from a capture and a script reading alone.
  **What IS settled, in the script's own words:** the override's comment
  (`install-wow-wotlk-fedora.sh` L1752-1757) already answers the "which label is correct for a
  shared tree" question — "`z`, not `Z`: three services share these mounts, and `Z` would give
  each container a private MCS category pair, so whichever relabelled last would lock the others
  out. The `./modules:...:Z` above is the opposite case — a single mounting service." In our own
  stack `./modules` is mounted by TWO services (`ac-worldserver` and `ac-db-import` — pinned
  divergence 2 above), so by that same reasoning `z` is the correct label for it, and
  `platform.bind_label()` labelling it `z` on every bind is already right. There is no open design
  question here.
  *What is still open:* confirm on the Fedora 7.1 E.4 live gate that a two-service `z`-labelled
  modules mount actually behaves (both containers get and keep access) — a one-line observation on
  a gate that runs anyway, not a reproduction owed to this list. The comparison vocabulary still
  reads neither spelling (`volume_from_config()` ignores `bind.selinux`, `_mount_mode()` drops
  `z`/`Z`), so nothing here is asserted by a test.

- [ ] **The script install publishes MySQL and the SOAP admin console on every interface, not
  loopback.** `catalog/installers/wow-wotlk/install-wow-wotlk{,-ubuntu,-fedora}.sh` write an
  override that never touches `ports:`, so all three inherit upstream's bare `3306:3306` and
  `7878:7878` — an unauthenticated `root`/`password` MySQL (the pair is upstream's own compose
  file, not ours) and an unauthenticated SOAP console (a remote shell in front of a GM account),
  both reachable from the LAN rather than only the host that runs them. The native engine's own
  install binds both to `127.0.0.1` — `native/base.yml.tmpl:62` for MySQL, `:264` for SOAP, each
  with a comment — because nothing in the launcher's maintenance path needs either port
  TCP-reachable: `apply.py`, `maintenance.py` and `accounts.py` all go over `docker exec`. Same
  capture as the two items above: `pylauncher/tests/data/wotlk-compose-config-script.json` shows
  `ac-database` and `ac-worldserver` publishing both ports with no `host_ip`, and
  `SCRIPT_INSTALL_DIVERGENCES` in `tests/test_compose_fixture.py` pins the difference so it cannot
  silently disappear from a future re-capture.
  *Recorded, not fixed:* whether the right fix is an override patch or simply deletion is the
  owner's call, not this list's — 7.2 (`pyplan/phase7-plans/7.2-retire-bash.md`) deletes all three
  scripts outright, so the fix may turn out to be "the file is gone" rather than a patch to it.
  Until then, these three scripts are what a user running `install-wow-wotlk*.sh` today actually
  gets.

- **`linux_package_manager()` cannot tell an immutable Fedora from an ordinary one** — 2026-08-31,
  found while fixing the buildx package name, not fixed here. It picks a package manager by which
  binary is on `PATH`, and `dnf` is on `PATH` on Bazzite and Silverblue too. There, `/usr` is an
  atomic ostree deployment: `dnf -y install moby-engine ...` is the wrong command whatever packages
  it names, because those systems layer with `rpm-ostree install` and take a reboot to activate it.
  `install-wow-wotlk-fedora.sh` has both branches and chooses between them; the Python engine has
  only the dnf one.
  *Recorded, not fixed:* the shape of the fix (a third `pm` value, or a flag on the dnf branch) is a
  design decision, and no immutable box exists to gate it on — the 7.1 Fedora gate box is ordinary
  Fedora 44. Naming it here so the next person to touch `docker_engine_commands()` knows the gap is
  known rather than overlooked.

- **`networking.plan()` cannot see past NAT, and the realm it advertises is unreachable** —
  2026-08-31, found while proving a real client login for the first time on any platform.
  `plan()` takes `lan_ip` from `platform.detect_lan_ip()`, which reports the address of the
  machine the engine runs on. Install into a VM on Hyper-V's Default Switch, VirtualBox NAT,
  or WSL, and that address is `172.x` — routable from the host and from nowhere else. The
  LAN step then writes it into `realmlist.address` and reports success, so the install is
  "finished" and every client outside the box sees an empty or unreachable realm list, with
  nothing anywhere naming the cause.
  Measured: the Fedora gate VM detected `172.30.61.209`; a client on the LAN could only
  connect once the address was overridden by hand to a reachable one and the host forwarded
  3724/8085 inward. A user has no such lever — `lan_ip` is a keyword argument, not a field
  in the Networking tab.
  *Recorded, not fixed:* the shape is a design decision. Detecting "the address a client
  would reach me on" is not something the box can answer alone; the honest options are to
  ask the user, to probe from outside, or to refuse to claim success when the detected
  address is in a NAT range (`10/8`, `172.16/12`, `192.168/16` behind another NAT, or a
  known hypervisor range) and say so. The last is cheap and would have turned tonight's
  silent wrong answer into a question.
  Related observation, not a defect: behind a port forward `account.last_ip` records the
  forwarder for every client, so per-IP account caps, ban-by-IP and geolocation all see one
  address for the whole world.

- **The CLI harness dies on Windows the moment its output is redirected** — 2026-09-01, found by
  the 6.3 clean-box gate on `yulon-win11-gate`, after every preflight check had passed:

      File "yulon\install_wiring.py", line 209, in main
        sys.stdout.write(line + "
")
      File "Lib\encodings\cp1252.py", line 19, in encode
      UnicodeEncodeError: 'charmap' codec can't encode character '→'

  The engine writes `→` in its own progress lines (it is in the provisioning JSON too). A Windows
  console is UTF-8, but a REDIRECTED stdout takes the locale encoding — cp1252 here — which has no
  `→`, so the process dies with exit 1 having done nothing wrong. It reached `--- clone-core` only
  after `PYTHONUTF8=1` was forced in the harness wrapper.
  This is not merely a gate artifact: `phase7-decisions.md:982` names this harness as the way to
  drive an install, and any user piping it to a log file hits the same crash. The GUI does not,
  because Qt does its own encoding.
  *Recorded, not fixed:* the fix is one of — reconfigure `sys.stdout` to UTF-8 with
  `errors="replace"` at entry, set `PYTHONUTF8` for the child, or stop emitting characters the
  stream cannot be guaranteed to encode. The first is smallest and keeps the arrows.

- **Docker cannot pull from a non-interactive Windows session** — same gate, same night. Docker's
  Windows credential helper needs an interactive logon, so under ssh or a scheduled task every pull
  fails with `error getting credentials - A specified logon session does not exist`. It cost the
  bind-mount probe its image and then killed `clone-core` outright.
  Environmental rather than a product defect, but it belongs here because it will meet anyone
  automating Windows: the fix is stub `docker-credential-wincred.bat`/`-desktop.bat` on PATH that
  answer `get` with `credentials not found in native keychain` **on stdout** (stderr leaves docker's
  `out:` empty and it errors anyway) plus `DOCKER_CONFIG` pointing at a config with `credsStore` empty.
  Worth noting what the engine got RIGHT: it did not blame the folder. It reported "that failure was
  the probe's own pull rather than an answer about the folder" and carried on — the three-outcome
  discipline behaving correctly against a real-world failure it had never seen.

- **`--provision` never asks for a sudo password, so the packaged artifact cannot provision any
  password-sudo Linux box** — 2026-09-01, found on clean Arch during the 7.1 gate. The shipped
  `.tar.gz`'s headless entry point runs `ensure_docker()`, finds `sudo -n` refused, and SKIPS:

      skipped: pacman -Sy --noconfirm docker docker-compose docker-buildx: exit 1 sudo: a password is required
      skipped: systemctl enable --now docker: exit 1 sudo: a password is required
      docker_group: "not-asked"   ok: false   docker_ready: false

  Running it under a real pty changed nothing — no question is ever asked on that path, so there was
  nothing for a driver to answer. The GUI gets a dialog and the CLI install harness prompts through
  `_terminal_prompter`; `--provision` alone has neither.
  Most Linux desktops have password sudo, so for them the artifact's headless provisioning can only
  ever hand back a list of commands to paste. That may be the intended contract for a non-interactive
  flag — but it is nowhere stated, and the 7.1 gate line asks for the PACKAGED ARTIFACT to install a
  server, which on those machines it cannot do unaided.
  *Recorded, not fixed:* the choice is between teaching `--provision` to prompt on a tty (it would
  then not be non-interactive), giving it an explicit `--sudo-askpass`/stdin contract, or stating
  plainly that provisioning on password-sudo Linux is a GUI-only path. Worth noting the behaviour
  itself is otherwise exemplary: it refused rather than blocked, named both commands verbatim, and
  declined the docker-group join because there was nobody to ask.

- **A commit pin added to a source that is already installed is never applied — no resume and no
  repair will ever move that checkout to it** — 2026-09-01, found by reviewing the commit-pin work
  (task G.1). Traced through the code and its tests, not reproduced on a box.
  `StagedInstaller.already_cloned()` (`pylauncher/yulon/catalog/native.py:1061`) answers True when
  the stage is recorded done AND the checkout's `origin` matches the source URL. Its three callers —
  `AzerothCoreInstaller._clone_core` (`catalog/families/azerothcore.py:112`), `._clone_modules`
  (`:164`) and `StagedInstaller.stage_clone_sources` (`native.py:1132`) — then yield
  "already … leaving it exactly as it is" and skip `_clone()` entirely, so the seam is never
  entered and `git.py`'s `_pin()` never runs. Give a source a `rev` in `catalog.json` after that
  source has been cloned and recorded, and every later run reports success while leaving the
  checkout on whatever commit it already had. That is precisely the case `CloneSpec.rev`'s own
  docstring anticipates — "a pin for cores whose upstream moves under a gate — Tortoise is pinned
  the day 7.6 passes" — and the day that pin lands is the day it silently does nothing on every
  machine that already installed Tortoise. The module and content half is NOT affected:
  `Applier.install()` (`pylauncher/yulon/apply.py:441`) has no such gate and re-clones and re-pins
  unconditionally on every apply.
  *Recorded, not fixed, and deliberately so:* the gate is 7.1's **"a resume must not be able to
  change what is being built"** invariant, written into `already_cloned()`'s docstring after the
  live Ubuntu gate (2026-08-30) lost its source tree to a fetch+reset on resume. A pin is arguably
  the one change to a source a resume SHOULD adopt, which makes this a collision between two
  correct rules rather than an oversight — so the fix (record the applied rev in the state file and
  re-pin when the catalog's differs, or exempt pinned sources from the gate) is a call for whoever
  owns 7.6 and its Tortoise pin, not a patch to make in passing.

- **The extractor cannot read the game client at all on enforcing SELinux, and `Mount` has no way to
  say so** — 2026-09-01, raised as a question by task H.1, **measured on `yulon-fedora-gate`**
  (Fedora 44, SELinux Enforcing, Docker 29.7.2) rather than argued. `docker.Mount` emits no SELinux
  label, and the plan's Group I builds `Mount(client_dir, CLIENT_MOUNT, read_only=True)` and
  `Mount(data_dir, OUT_MOUNT)` with none. Five cases, busybox, one minute:

  | case | mount | context | result |
  |---|---|---|---|
  | A. fresh dir, default label | none | `user_tmp_t` | **DENIED** |
  | B. parent relabelled first, `data/` created after | none | `container_file_t` | ALLOWED |
  | C. the relabel did not happen | none | `user_tmp_t` | **DENIED** |
  | D. same dir | `:z` | `container_file_t` | ALLOWED |
  | E. the user's client dir | `:ro` | `user_tmp_t` | **DENIED** |

  **B settles the argument about `data/` in the reviewer's favour, and against my own first reading.**
  `stage_generate_compose` relabels the whole server directory before extract runs, `data/` is created
  under it afterwards, and it inherits `container_file_t` - so the write succeeds without `:z`. C
  shows the exposure is real but narrow: that `chcon` is explicitly non-fatal, the compose-managed
  binds self-heal at `up` through `{{BIND_LABEL}}`, and the extractor's ephemeral mount has no second
  chance.

  **E is the blocker, and nobody predicted it.** The game client is the user's own directory. It lives
  OUTSIDE the server dir, so no `chcon` ever reaches it, and `:z`/`:Z` must never be used on it -
  they recursively rewrite the labels of the user's client, which is exactly why `bind_mount_ok()`'s
  probe bans them. So on any enforcing Fedora the extractor cannot read a single MPQ. Not a loss of
  redundancy: a certain failure, on the one distribution most likely to hit it.

  **The fix already exists in this codebase, for exactly this reason.** `--security-opt
  label:disable` - what `platform.label_disable_args()` returns and what `git.py`'s read path already
  uses for a repository it must not relabel. Measured: it read the client, and the client's context
  was byte-identical before and after (`user_tmp_t` both times). It also let the data dir be written.

  **This corrects the API shape recorded when H.1 landed.** A per-mount `label: str = ""` on `Mount`
  is the wrong seam: `label:disable` is a **container-level** `--security-opt`, not a mount suffix.
  The extractor's container holds both mounts - the client it must not touch and the data dir it must
  write - and one container-wide flag serves both. So the field belongs on `ContainerRun`, not on
  `Mount`, and Group I should ask `platform.label_disable_args(enforcing=selinux_enforcing())` for it,
  which already keeps the three-outcome answer (enforcing / not / could-not-ask).
  **FIXED 2026-09-01 in task I.5**, which is the task with the caller. `ContainerRun` gained
  `security_args`, fed from `container_security_args(enforcing=platform.selinux_enforcing())` and
  asked **once per plan** — container-level, as measured, with no `label` field on `Mount` and `:z`
  never on an extraction mount. H.1 was right to ship no uncalled field.

  **One residual risk, narrow and inherited rather than introduced.** "Could not ask" ships with
  confinement **on**: `label_disable_args` adds nothing for `None`, so a host where `getenforce`
  EXISTS but fails at runtime — the one case `selinux_enforcing()` answers `None` — still meets the
  denial this entry describes. That is the right default (disabling container confinement on no
  evidence is itself a decision, and it reuses `git.py`'s existing policy rather than inventing a
  second one), and it is the same three-outcome discipline the rest of this list turns on. Recorded
  so the next person seeing a Fedora denial checks whether `getenforce` answered at all before
  assuming this entry is stale.

- **`composegen.write_plan()` skips a CRLF file forever, and accuses the user when a read flickers** —
  2026-09-01, found while writing I.3's own version of the same "may we overwrite this" question, and
  **confirmed by running the real `write_plan()`**, not a reconstruction. Two defects, both in merged,
  shipping code (`yulon/catalog/composegen.py`, `is_ours()` and `write_plan()`):

  1. **The unchanged-file skip uses `read_text()`, which translates `
` to `
`.** So a
     byte-for-byte CRLF copy of our own compose file compares EQUAL, the write is skipped, and the
     CRLF stays on disk permanently — the skip preserving the exact thing it should repair. Measured:
     with a CRLF copy present, `write_plan()` returned `()` and left it. I.3's `_look()` avoids this
     by opening with `newline=""`.
  2. **Each file is read twice — once by `is_ours()`, once for the equality check.** A transient
     failure on the FIRST read makes `is_ours()` swallow the `OSError` and answer `False`, producing
     *"was not written by Yu'lon"* — an accusation against a file we did write. A failure on the
     SECOND read is not caught at all and escapes as a raw `PermissionError`. Both measured with a
     stateful `Path.open`.

  **Severity: real, currently low-probability.** `write_plan`'s targets are pure engine output with no
  shipped starting content, so the CRLF skip needs a `git` round-trip of a generated file or an editor
  with the wrong default. The double-read bites on any transient read during a resume, which is an
  installer-robustness gap rather than a rare one.

  *Recorded, not fixed, and the reason matters.* I.3 deferred it saying a change there would risk the
  A16 byte snapshot. **That justification does not hold** — the reviewer checked, and `render()`
  (which A16 exercises) and `is_ours()`/`write_plan()` are disjoint with no shared helpers. The honest
  reason to defer is scope: `write_plan` is load-bearing, has two other callers, and has its own test
  suite with no CRLF or double-read coverage yet. Fixing it is its own task, and it should consolidate
  with `dockerfile._look()` rather than grow a second three-way answer beside it — the same question
  currently gets an honest three-way answer for a Dockerfile and a two-way one for a compose file.

- **`conf.CONF_MODE = 0o600` is only safe while the CMaNGOS image runs as root** — 2026-09-01, raised
  during J.1's review, correct today, filed because the day it stops being correct nobody will look
  here. The patched `.conf` files are written by the HOST user; the server that reads them runs
  inside the container. Checked: `catalog/installers/shared/cmangos/{base,build,override}.yml.tmpl`
  carry no `user:` key and `wow-*/native/Dockerfile.tmpl` carries no `USER` directive, so the runtime
  image runs `mangosd`/`realmd` as root — and root bypasses POSIX permission checks, so a
  host-owned `0600` file is readable anyway.
  **The trap:** a future hardening pass adding a non-root `USER` to those Dockerfiles is an ordinary,
  well-intentioned change. It would silently make every conf file unreadable to the server, and the
  symptom is "the server will not boot" with nothing pointing at a permissions constant three modules
  away. `0o600` is also the first file-permission constant in this codebase (grepped `yulon/` — no
  other `chmod`/`0o6..`), so there is no convention to remind anyone.
  *Recorded, not fixed:* J.1 only defines the constant; `materialise()` (J.2) is what will `chmod`.
  The right moment to decide is J.2's review — either widen the mode, or make the Dockerfile's user
  and the conf mode answer to one place instead of two.

- **`Path("/etc/x").is_absolute()` is FALSE on Windows, so a path-escape check built on it admits
  the one pattern it exists to refuse — and the test that would catch it passes on Linux CI** —
  2026-09-01, found while writing `sqlplan._matches`, measured on this laptop, and filed here rather
  than in that module because it is a trap for **any** escape check in this codebase.

  Two measurements, both on Windows Python:
  - `Path("/etc/x").is_absolute()` -> **False**. A rooted POSIX pattern is not "absolute" to a
    `WindowsPath`, so a guard reading `if Path(pattern).is_absolute(): refuse` waves it through.
  - `Path("C:/srv") / "/etc/x"` -> **`C:\etc\x`**. The join then discards the server directory and
    escapes to the drive root — which is exactly what the guard existed to prevent.

  So the check admits the pattern **and** the join escapes, on the same platform, and the two halves
  only line up there. On Linux the guard works and any test of it passes, which is why CI is no help:
  the platform that needs the check is the one where it silently is not there.

  *The fix is to test the pattern's own spelling rather than ask `Path` what it thinks* — a leading
  `/` or `\`, a drive letter, and `..` segments, refused as text. Anyone adding a new escape check
  should do the same, and should write at least one test whose expectation is stated in terms of the
  pattern string rather than a `Path` predicate.
  *Found independently by two sessions working J.3 in parallel.*

- **`docker cp` of a directory containing a symlink FAILS on Windows, measured — so the conf stage's
  whole-directory copy is one upstream symlink away from breaking every Windows install** —
  2026-09-01, J.2. H.3 hit this on busybox's `/etc` (`mtab -> /proc/mounts`) and judged it
  speculative for CMaNGOS. J.2 is the first caller to copy a conf directory for real, so it was
  measured rather than assumed — with a busybox stand-in rather than a 40-minute CMaNGOS build:

  ```
  docker cp <c>:/opt/mangos/etc <dest>  exited 1: symlink \proc\mounts <dest>\mtab:
      A required privilege is not held by the client.
  ```

  Docker Engine 29.6.2, Windows 11, ordinary (non-elevated, non-developer-mode) user. `docker cp`
  extracts a tar and recreates each entry, and creating a symlink on Windows needs a privilege a
  normal account does not have. **One symlink anywhere in the directory fails the whole copy** — the
  conf files beside it are not extracted either.

  *Not a live defect today, and this time the check was done rather than inferred.* Upstream's own
  install rules say so: `cmangos/mangos-tbc`'s `src/mangosd/CMakeLists.txt` and
  `src/realmd/CMakeLists.txt` install every conf with plain
  `install(FILES ... DESTINATION ${CONF_DIR})`, and the only `install(DIRECTORY ...)` in them is
  `warden_modules`, which goes to `BIN_DIR`, not `CONF_DIR`. Nothing symlinks into
  `/opt/mangos/etc` at present.

  **What would break it, and what it would look like.** Any upstream change that ships a conf as a
  symlink — or the Tortoise fork's playerbots branch doing so, whose CMake nobody has read — turns
  every Windows install into a failed conf stage whose message names a *privilege*, which reads like
  a broken Docker installation rather than "this image ships a symlink". If it happens, the fix is a
  per-file `docker cp` of each missing `.dist`, which is three docker calls times the table and was
  rejected on cost, not on correctness.

  *Recorded, not fixed.* `conf.materialise()` does behave correctly when it happens — the failed copy
  leaves the staging directory removed and `etc/` empty, so a resume after enabling developer mode
  works, and that is covered by a test — but nothing turns the message into the sentence a user
  could act on.


### 18. Two ways the process itself stopped reporting — 2026-09-01

Neither is a defect in the launcher. Both are ways the machinery around it went quiet, which is the
same failure this file keeps recording one layer down.

**18a. After upstream squash-merges one of our PRs, its copy of a shared file is a snapshot of our
own past — and taking it is a silent revert, not a conflict resolution.**

`#140` was squash-merged, so `upstream/Yulon`'s `19d821f6` carries this branch's Phase 7.1 work as one
commit sharing no history with the branch that wrote it. The merge-base stayed back at `4044f59b`, and
`git merge upstream/Yulon` reported **add/add conflicts on all nine files Phase 7.3 had extended** —
not one of them a real disagreement.

The trap is the resolution, not the conflict. In an add/add conflict on a file you do not own,
**"theirs" looks like the safe, polite choice.** It is not: upstream's `extract.py` is the pre-I.4/I.5
version, its `sqlplan.py` still has the Protocols without `wsl_distro`, its `docker.py` still has
`argv = ["run", "--rm", *self.user_args]` from before `security_args`, and its `bug-checklist.md`
still says "Recorded, not fixed" about something I.5 fixed. And the failure mode is that it
**resolves cleanly and the suite may still pass**, because the work it reverted is newer than the
tests upstream is carrying.

What was actually done: measure the delta the correct way round (`git diff <ours> upstream/Yulon`
over the whole tree — **35 insertions against 2957 deletions**), **read all 35**, confirm every one is
an older version of something later work replaced, then `git merge -s ours --no-ff` with the audit in
the commit message, and verify with `git diff --stat <pre-merge-sha> HEAD` that the tree is
untouched. Only one of the 35 was not merely older wording, and it took a `git grep` to establish
that `shortfall()` still existed with that loop split into a helper.

**18b. On this repository an open pull request is the only thing that makes CI exist.**

`ci.yml` has no bare `push:` trigger — deliberately, and its own comment states the trade: "a topic
branch with no PR open gets no CI. Open the PR (draft is enough) and it does." The triggers are
`main`, `Yulon`, `release/**`, `v*` tags, and `pull_request`.

So the moment `#140` merged, `yulon-phase7` had **no CI at all**, and every merge onto it was verified
only by a local run. Nothing reported a failure, because nothing ran — and **"no runs found" reads
exactly like "not looked yet"**, which is the same shape as the gate that skipped and read like a
pass (§ throughout this file, and the standing rule: ask the machine, never the artifact).

The fix is to open the draft PR at the START of a long-lived working branch rather than at the end.
Done here as **#143**, and the branch is green verified by SHA (run `33538836096`,
`headSha == 18cbacdd`) — by SHA because `gh pr checks` will happily show a green run that predates
the current head.

### 19. The database password reaches the install log and a user-facing error — 2026-09-01, **FIXED 2026-09-01 at `2371b979`**

**Reproduced end to end, in already-merged code.** Not found by a test; found by a reviewer asking
where a docstring's authority came from.

`catalog.json`'s **Tortoise** entry (~line 958) ships an SQL phase whose statements include
`CREATE USER IF NOT EXISTS '{{DB_USER}}'@'%' IDENTIFIED BY '{{DB_PASSWORD}}'`. Those are streamed by
`sqlplan.apply()`. When the client rejects such a line it quotes the offending text back, so the
secret arrives in `proc.stderr` — and `apply()` puts that text in three places, none redacted:

- `sink(line)` — **the install log**. This is not merely "a log": it is the file users are
  *encouraged* to attach to a bug report, a channel whose entire purpose is being handed to a
  stranger. The same password in `logger.warning` sits on the user's disk; in `sink()` it is on
  its way to a GitHub issue. **The two look equivalent in a diff and are not.**
- the user-facing `InstallerError` on an `on_error: fail` phase;
- `logger.warning` on an `on_error: warn` phase.

Observed:

```
The import stopped: statement 1 failed while loading into the server
(ERROR 1064 (42000) at line 1: syntax error near "'tortoise-0a1b2c3d4e5f6a7b'").
```

**Why the guard that exists does not catch it.** Task J.5 added `_redact()` to `create_schemas()`, on
the stated premise that this is "the only SQL in the app that contains the secret." It is not — and
**Tortoise's `sql.create` is `()`** (pinned at `tests/test_catalog.py:719`), so `create_schemas()`
returns at its first line for the one shipped game whose plan actually creates the app user. The
redaction sits on the path that game never takes, while the path it does take is unguarded.

**The claim is the worst part.** `sqlplan.py`'s module docstring now asserts the secret appears in one
place and "is never logged". A redaction that advertises coverage it does not deliver is worse than
none, because it stops the next reader looking. Same family as §18 and as the run's standing rule
about a confident reason with nothing behind it.

**Bounding it honestly, so the next reader does not assume the worse case.** The database port is
bound to **loopback** — `127.0.0.1:${DOCKER_DB_EXTERNAL_PORT:-{{DB_PORT}}}:3306` in both
`shared/cmangos/base.yml.tmpl:46` and `wow-wotlk/native/base.yml.tmpl:62`, with the `127.0.0.1:`
prefix hardcoded and only the port NUMBER overridable by the environment. (Verified by reading the
templates, after the other session reported measuring it on all three gate boxes.) So a leaked
password is **not remotely exploitable on its own**; it needs local access or another foothold. That
does not excuse it — the secret is still in a file the user is encouraged to share, and it is the same
secret across a reinstall — but this is **"rotate and fix", not "an exposed database"**.

**LANDED at `2371b979` (2026-09-01 20:09), from `feat/7.3-j5-sqlplan-verify`.** Redacted where client
output ENTERS the module rather than at each call site, because K.7 would have added a fourth site and
sprinkling is how the next one gets missed. `apply()` passes the client's stderr through
`_redact_lines()` at the moment it splits it, and the install log, the `fail` message, the log record
and the `warn` line all read that one redacted local; `apply()`'s two `except` clauses, `verify()`'s
unanswerable rule, `_run_sql()` and the schema listing are separate entrances and each call `_redact()`
of their own. Checked 2026-09-02 at `f6ed1b9a` by reading the module: every place that puts client
text into a sink, an error or a log record reads a value that went through `_redact`. The heading
still said OPEN a day after the fix landed, which is how a reader concludes a live secret leak.

**Two mutations that survived the implementer's own "17 killed" table**, found by re-running it:

1. `_redact` weakened to `replace(password, "***", 1)` — nothing distinguished all-occurrences from
   first-occurrence. The code was right by `str.replace`'s default, not by any test.
2. The charset guard `_IDENTIFIER` widened from `[A-Za-z0-9_]+` to `[A-Za-z0-9_ ]+` — the only test
   used `"utf8mb4; DROP DATABASE mangos"`, which **any** rule refusing `;` also refuses. The test
   pinned "semicolons are refused", never "the rule is an identifier fullmatch".

**Open design note, deliberately not fixed here.**

- [ ] **`DbFacts.charset` has no `pattern=`, so a bad catalog entry fails mid-install instead of at
      load.** `sqlplan.create_schemas()` refuses a charset that is not a plain identifier, because
      `CHARACTER SET <charset>` is the one splice in that script with nothing around the value. The
      check is right and in the wrong place: it raises an `InstallerError` during phase 0, after the
      containers are up and the user has been told the install is running, for a defect entirely
      visible in `catalog.json`. `catalog.py:143` is `charset: str = "utf8mb4"` — no `pattern`, and
      no conformance test asserts the shipped entries satisfy the rule `sqlplan` applies to them.
      Right home: `Field(pattern=r"[A-Za-z0-9_]+")` plus a conformance test over every shipped entry,
      making it a load-time refusal and a failing test in CI. **The runtime check should stay even
      then** — `create_schemas()` takes `charset` as a `str`, not a `DbFacts`, so the model cannot be
      the only guard without changing that signature.

**One more, CHECKED and found already closed — recorded so nobody removes the thing that closes it.**
`_refuse_unquotable(password, ...)` runs only inside `create_schemas()`, and Tortoise never reaches it
(`create` is empty) while still splicing `{{DB_PASSWORD}}` into `IDENTIFIED BY '...'` through
`expand()`'s token fill. That looked like the same gap one layer up. It is not: `composegen.render()`
calls `_refuse_unsafe(password, "the database root password")` — cited here as `composegen.py:318`
until 2026-09-02, when it was at 319; the symbol keeps, the number does not — and
`_UNSAFE_SCALAR_CHARS = frozenset("$\"\;#{}

	'")` **includes the single quote, the backslash
and both line breaks** — so such a password is refused when the compose files are generated, which is
stage 4 of 12, six stages before `import`.
**But it is guarded by STAGE ORDERING, not by the function that appears to own the rule.** Anything
that reorders the stages reopens it.

**The edit to fear is NOT the one named here, and the difference was measured.** This paragraph said
that "removes `_refuse_unsafe` from `render()` as redundant … reopens it silently". Measured
2026-09-02 at `f6ed1b9a`, whole suite each time against a 1974-passed / 3-skipped baseline:

- **Deleting the `_refuse_unsafe(password, …)` call from `render()` is LOUD.** Four tests go red, all
  four parametrisations of `test_composegen.py::test_a_password_that_cannot_be_spliced_is_refused` —
  `pass"word`, `pass$word`, `pass;word`, `pass#word`. Nobody removes that call by accident.
- **NARROWING `_UNSAFE_SCALAR_CHARS` is SILENT.** Delete the single quote alone and the suite comes
  back **1974 passed, 3 skipped — byte-identical to the baseline**. The four parametrisations above
  are the only cases that exercise the set, and not one of them is a quote.

And the single quote is the character that matters here: it is what closes `IDENTIFIED BY '<pw>'` in
the shipped **Tortoise** phase, and Tortoise's `sql.create == ()` means `create_schemas()` — and so
`_refuse_unquotable()` — is never entered for it. So the whole of this game's protection rests on one
character of one frozenset, which no test names. Add a case for `'` before touching that set.

**Also known, harmless only by ordering:** `create_schemas()` returns on `if not plan.create` *before*
validating its schemas, so a plan with an empty `create` and a bogus `marker_db` is refused by
`expand()` and silently accepted here. The two call sites are not equivalent — and **Tortoise is
exactly the empty-`create` case**, so the asymmetry sits on a live path.

### 20. The secret-in-a-generated-file rule was decided for compose and never carried to the Dockerfile — 2026-09-01, **PREMISE GONE 2026-09-02; residual filed as §29**

**READ THIS FIRST — the entry below argues about a mapping that no longer exists.** Everything from
"Found while implementing K.2" down is the record of how the decision was reached, kept because the
reasoning is what makes §29 legible. What it argues FOR has landed. Verified 2026-09-02 at `f6ed1b9a`:

- **`CmangosInstaller._tokens()` is gone.** 7.3 split it by capability into `_public_tokens(server_dir)`
  — no `StageContext`, so `ctx.secrets` is not in lexical scope — and `_secret_tokens(ctx)`, which the
  conf and SQL consumers ask for by name. `_write_dockerfile` passes the public one, and it is the only
  caller that writes into the build context.
- **D4's "public bypass" is closed.** `dockerfile.write()` refuses text that is not a `_Rendered` —
  *"the … text did not come from dockerfile.render(), so nothing has checked it for a secret; refusing
  to write it into the build context"* — so hand-built marked text no longer reaches disk.
- **What is NOT closed** is the relationship between the mapping's keys and `native.Secrets`' fields: a
  secret filed under an undeclared name, or minted inside `_public_tokens` rather than passed in, is
  still invisible to a by-name refusal. That is §29, and it is where the live question lives now.

Found while implementing K.2, by asking a question the plan did not raise. **Nothing exploited it;
the exposure was one template edit away.**

`CmangosInstaller._tokens(ctx)` WAS ONE mapping used for the Dockerfile, the conf tables, the SQL
statements and verify — and it contained `DB_PASSWORD`. **K.4 hands that whole mapping to
`dockerfile.render()`** (plan ~line 4884). `dockerfile.render()` → `_render_one()` →
`composegen.fill()`, which refuses an **unfilled** `{{TOKEN}}` and says nothing about a spelled one —
its own docstring: *"Unused tokens are fine."*

**The compose side refuses this explicitly, and deliberately.** `composegen.generate()`
(`composegen.py` ~L322–329) raises `ComposeGenError` when a compose template contains
`{{DB_PASSWORD}}` while `password.mode == "generated"` — *"spell it `${DB_ROOT_PASSWORD:?…}` so the
secret stays in .env"* — and additionally omits the key from the token map in that mode, described in
the source as "belt to the refusal's braces". **`wow-tbc` is `mode: "generated"`.**

So the decision *the secret must not be rendered into a generated file* was taken once, for compose,
and not carried across. **A Dockerfile is the worse of the two:** it sits in the build context, and
through an `ENV` or `RUN` it lands in an image layer that `docker history` prints — surviving long
after the file is deleted.

Verified today: none of the six shipped `Dockerfile.tmpl`/`dockerignore.tmpl` names `{{DB_PASSWORD}}`.
K.2 added `test_no_dockerfile_template_names_the_secret_this_one_mapping_carries`, mutation-verified
by adding `ENV DB_PASSWORD={{DB_PASSWORD}}` to `wow-tbc/native/Dockerfile.tmpl`.

**A Dockerfile is worse than compose for a second reason, and it changes the REMEDIATION rather than
the exposure.** A secret rendered into a compose file is in **a file the user owns**: delete it and it
is gone. A secret in an image layer is **content-addressed and cached** — deleting the Dockerfile does
nothing, the layer persists, `docker history` prints it, and undoing it means finding and deleting
every image built from that layer. Compose is "delete a file and rotate"; the Dockerfile is "you now
have an artefact you must hunt". These are not one rule applied to a second file type at the same
severity.

**A test over today's templates is not the same guard as a by-name refusal**, which is what compose
has.

**DECIDED 2026-09-01 by an adversarial review from a second model (Codex), which found the hole both
of our own agents missed. Verdict: needs-attention — "do not ship Position B as the final contract."**

K.4 shipped the mapping WHOLE, arguing the refusal lives *inside* `dockerfile.render()` rather than at
a render site, so "the fourth caller inherits it by doing nothing". **That claim is false, and here is
why:** a caller inherits the protection only if it *chooses that renderer*. A new stage can take
`_tokens(ctx)` and reach disk through `composegen.fill()`, direct formatting, serialization, or
logging — and none of `render()`'s checks apply. Worse, **`dockerfile.write()` validates only the
generated marker, not secret CONTENT**, so even a caller reusing the writer while bypassing `render()`
can persist secret-bearing text into the build context.

**The deeper point, which neither of our agents reached:** conf's legitimate need for the password is
not a violation, but it **disproves the abstraction** — a shared `dict[str, str]` conveys **no
authority distinction** between consumers allowed and forbidden to handle secrets. That is ambient
authority, and every consumer has it by default.

**THE THIRD DESIGN, better than both of ours: capability-scoped token sets.** A public/build mapping
that *cannot* contain secrets, handed out by default; plus an explicitly obtained secret-bearing
mapping (or resolver) that conf/SQL/verify must ask for. Keep `render()`'s by-name refusal as defence
in depth. Add a test proving every build-context writer receives a mapping without `DB_PASSWORD`, and
make `dockerfile.write()` reject secret content if it stays a public bypass around `render()`.

**CONFIRMED INDEPENDENTLY, BY A DIFFERENT METHOD, THE SAME DAY.** K.4's own adversarial review
reached the identical conclusion by **mutation** rather than by reasoning, which is the strongest form
of agreement available here:

- **M15 — a SECOND secret key in `_tokens()` renders straight into the Dockerfile, and the mutation
  SURVIVED THE WHOLE SUITE: 1960 tests as it stood on 2026-09-01, recorded at `973b615f`** (the same
  mutation is written up as "1872 tests" in `cmangos.py`, recorded at `9e198c05`; both are dated
  counts of the suite of the day, not a size — it is 1974 at `f6ed1b9a`). The by-name refusal covers
  **one name**, not the property. This is precisely Codex's "ambient authority" point, arrived at from
  the opposite direction.
- **D4 — `dockerfile.write()` accepted hand-built marked text containing a secret and wrote it.** It
  validated only the generated marker, never content — exactly the public bypass Codex named.
  **Closed:** `write()` now refuses any text that is not a `_Rendered`, i.e. anything that did not come
  out of `render()` unchanged (checked 2026-09-02 at `f6ed1b9a`).

**What the review also established, and it bounds how much the tripwire is worth:** a template placed
**outside `resources.installers_dir()`** makes the `rglob` tripwire blind, and **`--installers-root`
(`install_wiring.py:195`) is a real flag** that repoints the whole tree. So the tripwire is a
*location* guard by construction. The refusal inside `render()` is what actually covers the property —
and it held against **five** attacks routed through `render()`. **K.4's central argument was correct
and could not be broken; it is simply incomplete**, because §20's premise was two claims and K.4
refuted only the first ("a guard someone must remember at every render site"), never the second
("a mapping that structurally cannot carry the secret needs nobody to remember anything").

**On deferring it:** rejected, and the reason is one to keep. *"This is the contract-forming change
and later migration only becomes harder."* **K.7 is the next consumer and it needs the password for
SQL — so the split must land BEFORE K.7, not after Group K.**

*(Superseded recommendation, kept as the record: split `_tokens()` rather than refuse by name.)* A by-name refusal is a guard someone
must remember to add at **every render site**, and K.4 is only the third consumer of `_tokens()`;
there will be a fourth, and the fourth is where it gets missed. This is the same argument that put
§19's redaction at the boundary instead of the call sites — **a mapping that structurally cannot carry
the secret needs nobody to remember anything.** (Reasoning from `dads-mmo-lab-58`.) Decide before K.4
lands; recorded as undecided rather than settled with a docstring.

### 21. `_stream()`/`_pump()` leak a running worker when the generator is abandoned — 2026-09-01, **CLOSED 2026-09-05 at `d2b963d5`**

**What was true, and how it was checked before the box was ticked.** `stop_abandoned_worker()`
(`catalog/native.py`) now runs at the abandonment itself — in an `except BaseException` around both
bridges' `yield` loops, `native.StagedInstaller._pump()` and
`families/cmangos.py::CmangosInstaller._stream()` — and both take the SAME `cancel` event their
`call` closed over, keyword-only and required, so a call site that forgets it does not compile past
review. `install_wiring.main()` makes one event per run and passes it, which is the path every
headless gate box drives and was the one case the closing test could not have exercised.

The two tests this entry asked for exist by the name it gave them —
`test_spine.py::test_abandoning_the_pump_stops_its_worker_with_no_cancel_from_the_caller` and
`test_families_cmangos.py::test_abandoning_the_stream_stops_its_worker_with_no_cancel_from_the_caller`
— and neither sets a cancel from outside, so a return to the ordering-only mitigation fails them.
Four more hold the edges: `…::test_an_interrupt_thrown_into_the_pump_stops_its_worker_too` and its
`_stream` twin (a Ctrl+C lands INSIDE the frame as a `KeyboardInterrupt`, which is why the clause is
`BaseException` and not `GeneratorExit`), `test_a_worker_that_ignores_the_cancel_is_left_rather_than_waited_for`,
and `test_the_abandoner_bound_is_the_streams_own_shutdown_timeout`.

**Re-derived on m910q 2026-09-05, on a copy of the tree at `6546b190`, not taken from the fix's own
record.** GREEN: `pytest tests/test_spine.py tests/test_families_cmangos.py -k 'abandon or
interrupt_thrown'` -> **7 passed, 203 deselected**. RED, with `stop_abandoned_worker(...)` deleted
from both `except BaseException` clauses and nothing else changed: **5 failed, 2 passed**, the first
of them `AssertionError: assert ['yulon-install-output'] == []` — this entry's own sentence, printed
by the suite — and the `_stream` twin `assert ['yulon-cmang...angos-output'] == []`, two workers
still live.

**What is deliberately NOT fixed, so it is not read as covered.** `cancel=None` — a caller with no
event at all — has no seam to pull, so the worker IS left running and the function logs
`"… was abandoned with no cancel event; its worker was left running"` rather than joining a thread
nothing will end. That is the documented behaviour, pinned by
`test_abandoning_the_pump_with_no_cancel_event_leaves_the_worker_and_says_so`, and it is why the
`cancel` parameter is required rather than defaulted. The join is bounded at
`ABANDONED_WORKER_SECONDS`, read from `runner._SHUTDOWN_TIMEOUT_SECONDS` rather than typed twice,
because this entry's own objection to an unbounded `join()` in a `finally` stands: it would block
the abandoner for the hours the extraction has left.

The record of what was wrong, kept because it is what the fix had to answer:

`CmangosInstaller._stream()` starts a worker thread and joins it only after the queue drains. If the
consumer abandons the generator — a downstream exception, a partial `list()` — `GeneratorExit` fires
at the `yield`, `worker.join()` is skipped, and **the worker keeps running and keeps pushing into a
queue nobody reads.** For the extract stage that is a live multi-hour extraction with no owner.

**The shape is inherited, not new:** `native.py:1507`'s `_pump()` is byte-identical in structure and
has shipped since 7.1.

**Mitigated in practice, by ordering rather than by structure** (see the sixth standing rule):
`LogPanel.stop()` sets the cancel event **before** `request_stop()`, so `run_container(cancel=…)`
returns and the daemon thread ends by itself; `install_wiring.py:208` exhausts the generator. An
abandonment *without* a cancel is not prevented by anything.

Not fixed: a `join()` in a `finally` would block the abandoner for the remaining hours of the run,
which is worse. Options not yet weighed: setting the cancel event in the `finally`, a bounded join, or
a weak reference. *(The bounded join is what landed, with the cancel set first so that the join has
something to wait for.)*

**The test that closes it is cheap and should be written whatever the fix is:** abandon the generator
**without** setting the cancel event, and assert no live worker remains. That converts the mitigation
from "`LogPanel.stop()` happens to set cancel first" into something a **reorder fails** — the sixth
standing rule's second exit. *(Written, in both bridges, and named above.)*

**No note anywhere says `_pump`'s hole was ever considered**, which is why this is recorded as
**unweighed rather than intentional**. "Inherited and shipped since 7.1" is not evidence it was
decided, and writing it down this way is what stops the next reader treating silence as approval.

### 22. The ownership guard exists, is reviewed, and is sitting in an unmerged PR — 2026-09-01, SEQUENCING

**This is PR #142's shape, still open, in a second engine.** Found by asking a sharper question than
"is this the only place": *is every caller that acts destructively **downstream** of it?* **A
uniqueness claim can be true and still protect nothing.**

`native.py`'s `read_claim()` genuinely **is** the only folder-ownership answer, and every
`StagedInstaller` clone path is downstream of it — `run()` → `_guard()` → `read_claim`, then per-stage
`refuse_unowned_checkout()` → `claimed_this_folder()` → `read_claim`, before `_clone()` →
`git.clone`. No early exit past `_guard()`. The docstring's claim is accurate.

**`yulon/apply.py` is a parallel destructive engine that never asks.** Verified:
`grep -n "read_claim\|claimed_this_folder\|refuse_unowned\|is_ours" yulon/apply.py` → **no hits**;
`apply.py` does not import `yulon.catalog.native` **at all**. So this is not a missed branch — **the
check was never wired in.**

The reachable path: `apply.py:441` `Applier.install()` → `apply.py:448` `self.git.clone(CloneSpec(...))`
→ `git.py:355` `RunnerGit.clone`, which is **`shutil.rmtree(spec.dest)`** (`git.py:361`) when the dest
exists without `.git`, or **`git reset --hard FETCH_HEAD`** (`git.py:430`) when it does. Reachable from
the GUI at `ui/controller_view.py:1363` and from `wotlk_modules.apply_module()`. Also
`Applier.remove()` → `shutil.rmtree` (`apply.py:494`, `:547`), and `_client()` →
`shutil.copytree(..., dirs_exist_ok=True)` into the user's `client_dir/Interface/AddOns`
(`apply.py:642`) with no gate at all.

**Mitigating, and stated so the entry is not read as worse than it is:** the dest is
`server_dir/modules/<id>`, a path the app creates — narrower than "an arbitrary user directory". But
`azerothcore.py:139-143` records a user's own `modules/` tree as a **past incident**, so the narrowing
is not as reassuring as it sounds. This is **wow-wotlk only** and entirely **pre-existing** — nothing
in Phase 7.3 touches it.

**CORRECTED THE SAME DAY — this is a SEQUENCING problem, not a missing guard.** The fix exists and is
green in **open PR #142** (`fix/modules-tab-ownership`), which is **not** an ancestor of
`yulon-phase7` (`git merge-base --is-ancestor` → not merged). That branch adds `_require_own_clone()`
(7 occurrences) gating `install()`, `remove()` and `configure()`, plus the four-fact adoption rule and
`server_dir_claim()`, which calls `native.read_claim(server_dir, valid=())` at `apply.py:221`.

**Two consequences, and they are the actionable part:**
1. **Do not "fix" this independently on `yulon-phase7`.** #142 is the side with the ownership work;
   a second answer hands Baerthe two implementations to reconcile. Anything merged here that touches
   `Applier` risks conflicting with it.
2. **The exposure window is however long #142 takes to merge.** On `yulon-phase7` today,
   `Applier.install()` → `git.clone` → `rmtree` / `reset --hard` is reachable from the GUI.

**A method note worth more than the entry, because it nearly cost a wrong conclusion twice.** The
audit's supporting claim was *"`apply.py` does not import `yulon.catalog.native` at all"* — true on
`yulon-phase7`, and **still true at module scope on #142**, because the import is **deliberately
deferred inside `server_dir_claim()`**: `apply.py` is imported by `networking`, `accounts`,
`maintenance`, `repair` and the UI, and none of them should load the native install engine to read one
JSON file. So **"does not import X" is a strictly weaker check than "does not call X", and a deferred
import makes the two disagree.** The `read_claim|claimed_this_folder|refuse_unowned|is_ours` grep is
the one that stays correct across that merge; the import-based half of the claim would have read as
"still unguarded" over a branch that guards it.

Two lesser notes from the same audit:
- **`composegen.is_ours()` and `dockerfile._look()` are two independent implementations of one
  file-level marker rule.** They agree today; **nothing enforces that they keep agreeing.**
- **`catalog_view._looks_like()` fails OPEN** (`catalog_view.py:151-162`) — and it is the supply route
  that admits a foreign folder into the `Applier`.

### 23. A future-version state file is silently downgraded on disk — 2026-09-01, **FIXED 2026-09-02**

Both families share one state filename (`STATE_FILE = ".yulon-install.json"`, no family suffix). A
**cross-family** file is refused by `_guard` on `game_id` (`native.py:894`) and `family` (`:899`) — but
**after** a silent name-drop.

The quiet case is a **same-family, future-version** state file: stage names the running binary does not
know are dropped at `native.py:335` **with no log at any level**, `write_state` then persists the
**post-filter** tuple (`:709`, `:377`), so **an older binary permanently strips the newer names from
disk**, and the only user-facing line (`:667-669`) prints the already-filtered tuple. A downgrade is
therefore lossy and silent in both directions.

**FIXED 2026-09-02, on the day K.8 was about to make it reachable.** `InstallState` gained `unknown` —
the names on disk this build does not recognise. `read_state()` now SPLITS rather than filters, logs a
warning naming what it did not understand, and `write_state()` persists both halves, so a downgrade is
no longer destructive. The two stay separate deliberately: this build must not act on a stage it cannot
interpret, so behaviour reads `completed` while persistence writes `completed + unknown`.

**The read filter was not the dangerous route.** Mutation found a second one that runs far more often:
`with_stage()` rebuilds `completed` from `order`, and a future name is by definition not in `order`, so
without `unknown` riding alongside, **the very first stage an older build completed would erase the
newer build's record** — the same loss, reached on every stage rather than only on a read. Three tests,
3/3 mutations killed, one of them that route specifically.

Original note follows. Unreachable for cmangos at the time (not in `FAMILIES` until K.8); recorded then
because **it is
precisely the mechanism that would hide a `stages()`/`STAGE_NAMES` mismatch** — the thing K.2's
inertness argument depends on being visible. **Note it in K.8's brief.**

### 24. The false 0600 guarantee K.3 removed is still live in `conf.py` — 2026-09-01, **FIXED 2026-09-02**

**Measured twice, on PKGAME-LAPTOP, Windows 10.0.26200, CPython 3.13.14, 2026-09-01.** On Windows the
POSIX mode is a **no-op** and the ACL is purely inherited:

- `os.open(p, O_WRONLY|O_CREAT|O_TRUNC, 0o600)`, `open()` + `os.chmod(0o600)`, and a plain `open()`
  **all** give `st_mode & 0o777 == 0o666`, with **byte-identical `icacls` output**.
- Under a folder first granted `*S-1-5-32-545:(OI)(CI)(RX)`, the "0600" file carried
  `BUILTIN\Brukere:(I)(RX)` — readable by every local user — and a following `os.chmod(0o600)`
  changed **neither the mode nor the ACL**.

K.3 removed its own *"never world-readable for even an instant"* claim on that evidence. **But
`conf.py`'s `_write` still asserts "the conf is never readable by anyone else even for an instant (the
database password is in it)"** — the same false guarantee, one module away, over a file that really
does contain the password (`LoginDatabaseInfo = "host;port;user;password;schema"`). A guarantee that is
written down stops the next reader checking, which is why this is worse than silence.

**And `conf.py` is strictly weaker than K.3 on POSIX too:** it writes with `open()` and chmods
**after**, so the temp file holds the password at the umask default until the chmod lands. K.3's
`os.open`-with-mode has no such window. (Read, not measured — flagged as read.)

**FIXED 2026-09-02, both halves.** The sentence is gone, replaced by a record of what was measured and
of what the mode does and does not buy. And the POSIX window it described is gone too: `_write` now uses
`os.open(tmp, O_WRONLY|O_CREAT|O_TRUNC, CONF_MODE)` + `os.fdopen`, so the mode is applied by the creating
syscall rather than by a `chmod` a moment later — the same shape `cmangos._write_secret` already had.

The test that guarded this was pointed at the wrong syscall. It asserted two `os.chmod` calls under the
heading "the conf is never briefly readable" — a *declaration* that something was intended, which passed
happily while the window was open. It now records `os.open`'s third argument, the value that decides the
outcome, and asserts no chmod follows. Verified by reverting `_write` to `open()` + `chmod` and watching
it fail. It deliberately claims nothing about Windows: the mode is a no-op there, and a test asserting a
guarantee on that platform would be this very bug restated as a test.

Deferring the Windows **fix** is right — a real DACL means pywin32 or an `icacls` subprocess on every
path that touches the file, and that is an app-wide posture decision. **Correcting the false sentence
is not deferrable.**

### 25. The bash lineage still deletes a volume silently — 2026-09-01, **GONE 2026-09-02: both halves deleted**

`install-wow-vanilla.sh` ran `docker volume rm "${db_volume}"` with no confirmation — **the exact
destructive answer the Python design rejects**, and the reason `db-password` refuses rather than
wiping.

While it existed it was unreachable only by **registration**: `installer_for()` fell back to a script
for an **unregistered** family, and K.8 registered `cmangos`. That made the guard a position rather
than a check — the sixth standing rule's shape — and this entry closed by saying "de-registering the
family for any reason re-arms it."

**That conclusion is now impossible, and it is the sentence to correct rather than the finding.**
Verified 2026-09-02 at `f6ed1b9a`: the file is gone (F.1 deleted all eight bash files), and the
fallback is gone with it (F.3) — `installer_for()` has one rule, `families.family_for()` on
`install.native.family`, and an unregistered family now raises "an install family this app does not
have" instead of reaching for a script. There is no longer anything for de-registration to re-arm.

The argv census below still holds and is worth keeping:

Enumerated at argv level across `yulon/` and `main.py` (re-run 2026-09-02 at `f6ed1b9a`): the **only**
volume argv in the Python app is `docker.volume_exists()`'s `["volume", "inspect", …]`, read-only.
No `volume rm`, no `volume prune`, no
`down -v`, no `--volumes`. `remove_staged` is `["compose","down","-t",…,"--remove-orphans"]`.

### 26. The three CMaNGOS entries name compose services that the rendered file does not have — 2026-09-01, **FIXED 2026-09-02 at `27209099`**

Found by **F.2's reviewer**, while reviewing a 7.2 deletion — a 7.3 defect surfaced from an unrelated
task. Verified independently at the time; the block below is what the catalog said **on 2026-09-01**,
not what it says now:

```
wow-tbc / wow-vanilla / wow-tortoise   catalog compose_services() -> ('db', 'realmd', 'mangosd')
shared/cmangos/base.yml.tmpl service keys -> {{CONTAINER_PREFIX}}db, ...realmd, ...mangosd
                                          -> rendered for wow-tbc: tbc-db, tbc-realmd, tbc-mangosd
wow-wotlk                              compose_services() -> None  (uses the default; consistent)
```

`docker.start_staged()` runs `compose up -d --no-deps db realmd mangosd`. Against the file this repo
actually generates that answers **`no such service`** — every one of the three is missing.

**It was not live only because `FAMILIES` registered `azerothcore` alone**, and K.8 was the task that
would register `CmangosInstaller`.

**FIXED at `27209099` (2026-09-02 00:07), before K.8 registered the family, which is the order this
entry asked for.** The three `containers.services` declarations were DROPPED rather than prefixed:
`wow-wotlk` returns `None` and takes the default because its containers ARE its services, and the
shared CMaNGOS template follows the same convention by design, so the entries had nothing left to
declare. Prefixing instead would have been refused at generation time — `_container_prefix()` rebuilds
the container names from `containers.services`, so `tbc-` + `tbc-db` raises rather than renders.
Verified 2026-09-02 at `f6ed1b9a`: no entry in `catalog.json` carries a `containers.services` key, and
`start_staged()` therefore asks compose for `tbc-db`, `tbc-realmd`, `tbc-mangosd` — the names the
rendered file defines.

**Read this before re-opening it.** The one shape `_container_prefix()` ACCEPTS for a
`containers.services` declaration is `container == prefix + service`, which makes `compose_services()`
the bare suffixes — exactly what `compose up` answers `no such service` for. So "put the service names
back and prefix them" re-arms this bug while passing every generation-time check. That asymmetry is
filed separately as §30.

**Two test-level reasons nobody caught it**, both worth more than the bug:
1. The deleted `test_no_catalog_compose_service_is_really_a_container_name` was the **only** test that
   cross-checked declared services against a compose file — and it read the **bash script**, so
   restoring it verbatim would catch nothing. Its replacement,
   `test_cmangos_games_select_compose_services_not_container_names`, **restates literals instead of
   cross-checking**, and its two halves already contradict each other while both pass.
2. `test_composegen.py::test_the_cmangos_services_are_named_after_their_containers` carried the
   docstring *"`ContainerSpec.services` keeps its default"* — false at the time, because the catalog
   set `containers.services` explicitly. A true-sounding premise attached to a passing test. **Both
   halves are now true**: `27209099` dropped the declaration and rewrote that docstring to say when it
   became true and what had been believed before (checked 2026-09-02 at `f6ed1b9a`).

**The test that should exist now does**, and landed in the same commit:
`test_composegen.py::test_every_service_the_catalog_selects_is_defined_in_the_rendered_compose_file`.
For every entry with a native block it asserts that each name in `container_spec().compose_services()`
(plus `import_service` and `containers.client_data`) appears as a service key in the **rendered** base
from `composegen.render()` — cross-check, not restatement. It also carries the vacuity guard the review
demanded: `compose_services()` mutated to `return self.services` answers empty for every shipped entry
now that none declares one, and the entry count alone could not see that, so the test asserts each
entry SELECTED something before checking what it selected. Verified passing 2026-09-02 at `f6ed1b9a`
(whole suite 1974 passed, 3 skipped). This paragraph read "and it fails today, which is the point"
until 2026-09-02 — a reader acting on it would have blocked K.8 and put back the one declaration shape
that re-arms the bug.

**Also minor, same branch — and STILL OPEN, checked 2026-09-02 at `f6ed1b9a`** (the FIXED in this
entry's heading is the compose-service defect, not this): `test_no_bash_installer_ships` has **no
`scanned >= N` vacuity guard** —
mutation-proven: rename the installers root and it passes while its two siblings in the same file
fail. Inconsistent with the guard F.2 deliberately added two files over.

### 27. One platform question, two answerers — the import-bound SELinux seams — 2026-09-01, **CLOSED 2026-09-05**

**Three of the four sites are fixed. `native.Seams` is not, and it is the one a real install reaches
most.** The heading and the text below were rewritten on 2026-09-05 because what stood here was a
present-tense description of `docker.bind_mount_ok()` that the 2026-09-04 fix had made false, left
under an OPEN marker — the entry read as live for a day after its subject had changed.

**What was found, 2026-09-01.** `docker.bind_mount_ok(… selinux_enforcing = platform.selinux_enforcing)`
bound the module function as its default, at import. Asked of the interpreter rather than read off the
page: `signature(docker.bind_mount_ok).parameters["selinux_enforcing"].default is
platform.selinux_enforcing` → **True**, while the same question of `extract.run_plan` → **None**. That
made `bind_mount_ok` the real instance of the trap `container_user_args()` documents against itself,
and the one **K.5 had wrongly attributed to `extract.run_plan()`** — a true fact about one function
asserted about another that does the reverse. K.5's docstring was corrected on
`fix/mmaps-audit-and-the-claim-that-was-backwards`, where the replacement carries the interpreter
output instead of a claim.

It was latent rather than live, by luck and not by wiring: the production caller
`preflight._default_bind_probe` passed no seam, so the bound default ran and asked the real host, which
was the right answer by accident; and under test it never ran at all, because `test_preflight.py` fakes
one level up. It was green only because every runner so far is non-enforcing.

**FIXED 2026-09-04/05 (m910q), in three modules, all to one shape** — default `None`, module attribute
resolved inside the call, which is `extract.run_plan()`'s shape:

* `docker.bind_mount_ok()` — 2026-09-04. Guard:
  `test_the_bind_probe_asks_the_selinux_seam_the_module_holds_at_call_time`, which patches
  `platform.selinux_enforcing`, **counts the call**, and asserts `label:disable` reached the argv. The
  rejected alternative was the one this entry used to prescribe — thread a seam down from `preflight`
  and leave the default alone — which fixes the one caller there is and leaves the same default waiting
  for the next.
* `git.ContainerGit.selinux_enforcing` / `.filesystem_type` — 2026-09-04, via `_ask_selinux()` and
  `_ask_filesystem()`. Before the change,
  `{f.name: f.default is getattr(platform, f.name) for f in fields(ContainerGit) if f.name != "image"}`
  → `{'selinux_enforcing': True, 'filesystem_type': True}`. Guard:
  `test_a_bare_container_git_asks_the_selinux_seams_the_module_holds_at_call_time` (`asked == []`
  against the old file).
* `preflight.gather()` — `selinux` 2026-09-04, **`fs_type` 2026-09-05**, which is the one the third
  pass had left bound. It was the sharpest of the three, because `ContainerGit` had already moved: one
  patch of `platform.filesystem_type` on m910q, 2026-09-05, gave
  `ContainerGit()._ask_filesystem(Path("/tmp"))` → `'btrfs'` while `gather()` handed back the host's
  `'ext2/ext3'`. Guard: `test_gather_asks_both_linux_seams_the_module_holds_at_call_time`, mutation-
  proven one default at a time with `__pycache__` purged between (`selinux rebound -> 1 failed`,
  `fs_type rebound -> 1 failed`).

**CLOSED 2026-09-05, the same day, once the lane that owned `native.py` had landed.**
`Seams.selinux_enforcing` and `Seams.fs_type` now default to `None` and resolve against `platform`
inside `Seams.ask_selinux()` / `ask_fs()` — the shape `docker.bind_mount_ok()`, `preflight.gather()`,
`git.ContainerGit` and `extract.run_plan()` already had. The one call site (`native.py`'s
`generate-compose` stage) reads through the resolvers, and `families/cmangos.py:505` now hands
`extract.run_plan()` the resolver rather than the import-bound field, which had been defeating
`run_plan`'s own correct default.

The guard the two withdrawn identity asserts owed is paid:
`test_one_patch_of_a_platform_probe_gets_one_answer_out_of_the_whole_install` patches both `platform`
attributes, drives a bare `Seams()` AND `preflight.gather()`, and asserts each fake was CALLED and
that one patch produced one answer. Proven RED against the import-bound shape rather than assumed:
restoring `= platform.selinux_enforcing` on the field gives
`AssertionError: Seams did not reach the patched probe / assert False is True` (m910q, 2026-09-05);
restoring the resolver passes. Full gate on the combined tree that day: 2536 passed, 4 skipped, mypy
clean on linux/win32/darwin, ruff and black clean.

`Seams.relabel` stays import-bound, deliberately: nothing else in the app asks the host whether a
folder was relabelled, so there is no second answerer and no split — the latent trap is recorded here
rather than changed for no measured defect. What was open when this section was last written: `Seams.selinux_enforcing`,
`Seams.fs_type` and `Seams.relabel` are still the `platform` functions bound at class-definition time,
so the split this entry is named for now sits between `Seams` and the three modules above. Measured on
m910q, 2026-09-05, with ONE `monkeypatch` of each attribute and the two consumers a single install
reaches (`_preflight_lines()` → `self._seams.gather(…)`, and `stage_generate_compose` →
`self._seams.selinux_enforcing()` / `.fs_type()`):

```
Seams.selinux_enforcing default is platform.selinux_enforcing -> True
Seams.fs_type default is platform.filesystem_type             -> True
gather(...).selinux_enforcing        -> True        (the fake)
Seams().selinux_enforcing()          -> False       (the host)
gather(...).server_fs_type           -> btrfs       (the fake)
Seams().fs_type(server_dir)          -> ext2/ext3   (the host)
```

`cmangos.stage_extract` makes it worse in a way its own docstring predicted and then walked into: it
passes `selinux_enforcing=self._seams.selinux_enforcing` into `extract.run_plan()` **to avoid** "one
question about one machine put to two answerers inside a single install" — and because the object it
passes is the import-bound one, that is now exactly what it causes. Left to default, `run_plan` would
resolve `platform` late and agree with `gather()`.

**Fix, not applied here because `native.py` is another lane's file this run:** give `Seams` the
`bind_mount_ok` treatment — the three fields default to `None`, and `ask_selinux()`, `ask_fs(path)` and
`ask_relabel(path)` resolve `None` against `platform` at call time; `native.py`'s two call sites in
`stage_generate_compose` and `cmangos.stage_extract`'s pass-through move to those. Tried on a lane copy
on m910q, 2026-09-05: the probe above then answers `True`/`True` and `btrfs`/`btrfs`.

**The guard to add with it**, and it is the distinction this entry exists for: patch
`platform.selinux_enforcing` and `platform.filesystem_type`, drive a bare `Seams()` **and**
`preflight.gather()`, and assert both fakes were **called** and that one patch produced one answer.
Asserting that the parameter or the field exists proves nothing; asserting its identity is worse,
because it pins the defect — which is what
`test_families_azerothcore.py::test_every_seam_defaults_to_the_real_function_it_stands_in_for` did
until the two asserts were **withdrawn** on 2026-09-05. Against a `Seams` fixed as above that test
failed on m910q at `assert None is <function relabel_for_containers>`; its comment now carries the
withdrawal and the guard that is owed.

### 28. The gaming-mode script cannot be used from the artifact it exists for — 2026-09-01, OPEN, packaging

`catalog/installers/steam-deck/setup-gaming-mode.sh` exists so a Steam Deck user can start a stack
from a Steam library entry, and its header documents the flow at line 31: **Games -> Add a Non-Steam
Game -> Browse**, pointing Steam at the file. A Steam non-Steam-game shortcut stores an **absolute
path**.

On Linux Yu'lon ships as an **AppImage** (`.github/workflows/release.yml`, "Package AppImage",
`appimagetool "$APPDIR" "Yulon-${YULON_REF}-x86_64.AppImage"`), and the script is inside it: the spec
ships `catalog/installers/` as a **tree** (`build/pylauncher.spec:29-31`), so the file lands at
`usr/bin/catalog/installers/steam-deck/` within the bundle. An AppImage self-mounts under
`/tmp/.mount_<name>XXXXXX/` with a fresh random suffix per launch and unmounts on exit — so the path
the user Browses to exists only while that particular run is live.

**Sharpened 2026-09-02 by the review of the F.5 branch; my first wording was too generous.** I wrote that
the flow "works once, and on the second launch the shortcut points into a mount that is gone". The mount
exists only while **that Yu'lon process is running**, so the shortcut is dead the moment the user closes
Yu'lon — including within the first session, and including the very first time they press play in gaming
mode, since gaming mode is exactly the situation where Yu'lon is not also running. The remedy and the
severity are unchanged; the failure is earlier and more certain than "the second launch".

**Verified here, 2026-09-01:** the AppImage is the Linux release artifact; a plain tarball is built
beside it (the workflow says why: "an AppImage cannot run without FUSE"); the script ships inside the
bundle as part of the installers tree; and the header does instruct the Browse flow.
**NOT verified here:** the random-per-launch mount path itself, which is asserted from AppImage's
documented runtime behaviour and has **not** been measured on a Deck or on any Linux box in this
project. It is the load-bearing link and it is the one taken on trust — measure it before acting.

The tarball and the Windows zip give stable paths and are unaffected. But **Steam Deck gaming mode is
the AppImage case**, so the one artifact this script exists for is the one it cannot be used from.
Nothing copies the script out of the bundle, and nothing tells the user to.

**Not a script change.** The fix is a packaging decision with at least three shapes: the app copies the
script to a stable location (`~/.local/share/yulon/`) during a Steam Deck install; the release ships it
as a loose file beside the AppImage; or the app grows a "Set up gaming mode" action that writes the
shortcut itself. Choosing is owner work.

**Interim, on `fix/steam-deck-pins-that-do-not-pin`:** the Steam block now says to copy the file out of
the bundle first, and why. That is a warning, not a fix — a user who does not read the header still
hits it, and per §1 nothing outside `pyplan/` points a user at the header at all.

### 29. The Dockerfile refusal covers declared FIELD NAMES, not secrets — 2026-09-02, the NAME half FIXED 2026-09-04 at `d2b963d5`, the VALUE half FIXED 2026-09-05 at `75bce609`, **CLOSED with the residue named below**

`75bce609` is on `lane/dockerfile-value` and unpushed as this is written, and so are the review
follow-ups that corrected this entry — `da34fb86` (what the containment floor is answerable to) and
`fd947b1c` (the false-positive refusal). The SHA is cited rather than the branch because the branch
is disposable and the neighbouring entries cite SHAs; whoever merges this rewrites it to the merged
one, which upstream's squash habit means will be a single new SHA.

**Both halves are now built, and the RED for each was re-derived rather than quoted.**

*The NAME half* landed 2026-09-04: `dockerfile.SECRET_NAME_WORDS` plus `announces_a_secret()`, read
on the MAPPING before anything is rendered. *The VALUE half* landed 2026-09-05: `render()` takes a
required keyword-only `secrets: Secrets` and refuses any key carrying one of its values, whatever
that key is called. The RED that separated them, probed against the shipped module on m910q
2026-09-05 on a copy of the tree at `0cc637c7`, `Secrets` declaring one field:

```
SOAP_PASSWORD  -> REFUSED: "SOAP_PASSWORD: each of those reads as a secret and matches no field of ..."
BUILD_ARG      -> ACCEPTED, secret in text: True
                  write() accepted it: ['Dockerfile', '.dockerignore']
                  Dockerfile on disk contains the secret: True
                  the line it wrote: ['ENV BUILD_ARG=tbc-0123456789abcdef']
EXTRA          -> ACCEPTED, secret in text: True
FOO            -> ACCEPTED, secret in text: True
```

The same probe against the FIXED module, same box, same day:

```
SOAP_PASSWORD    -> REFUSED: SOAP_PASSWORD (the value declared as DB_PASSWORD) - and this mapping ...
BUILD_ARG        -> REFUSED: BUILD_ARG (the value declared as DB_PASSWORD) - ...
EXTRA            -> REFUSED  |  FOO -> REFUSED
BUILD_ARG_BURIED -> REFUSED   (value `--db-pass=<the password> --verbose`; containment, not equality)
secret echoed in the refusal: False   (each of the five)
a caller that forgets the argument:
  TypeError: render() missing 1 required keyword-only argument: 'secrets'
```

**The closing condition said "at the call site rather than in `render()`", and what landed is not
quite that — deliberately.** The comparison is IN `render()`; the VALUES come FROM the call site.
`_write_dockerfile` passes `secrets=ctx.secrets`, which it holds one frame above the
`_public_tokens(server_dir)` it passes as the mapping, so 7.3's capability split is untouched: the
secret is named as the thing that must NOT be emitted, never added to the mapping. Putting the
comparison itself in `_write_dockerfile` would protect that call site and nothing else, and this
module has twice watched a LOCATION-shaped protection lose — a template planted in
`shared/cmangos/`, then `--installers-root` pointing the engine at a tree no glob walks. `render()`
is where every mapping passes, so that is where the property lives.

**What each half is worth, and why neither subsumes the other.** The name rule reads a key and so
catches a careless spelling around a value nobody here has ever seen. The value rule reads a value
and so catches a careless key around a value that is provably the install's own secret. A key that
trips both is reported by the value rule, because that one has *proved* something; the name rule's
remedy ("rename it, or declare it") is the wrong advice for a key holding the real password.

**What a "match" is, and the measurements behind it.** `carries_a_secret(value, secret)` is
containment at or above `MIN_CONTAINED_SECRET = 8`, equality below it, and never true for an empty
secret. Measured on m910q 2026-09-05, `server_dir=/tmp/fixedsrv/srv`, over the 34 distinct values the
three shipped CMaNGOS `_public_tokens()` mappings produce (the directory is named because three of
those values carry an 8-hex digest of it, so any count over their CHARACTERS moves with it — this
list said 30, 16 and 98 until 2026-09-05, from a run under per-game temporary directories):

* the empty string is contained in all 34, and 31 of the 36 single alphanumeric characters are
  contained in at least one (`a` alone in 14 — `/opt/mangos`, `characters`, …). Containment with no
  floor is not a strict rule, it is an install that can never run.
* `mangos`, six characters, is contained in five of them — so the floor has to be above 6. That is
  the lower bound, and it does not move with the directory.
* The upper bound is coverage: the floor must be at or below every secret the app itself PRODUCES,
  or containment degrades to equality for a real install. Measured the same day by calling
  `resolve_secrets()` on an empty server dir through `families.family_for()` for all four shipped
  entries: `wow-tbc` 20, `wow-vanilla` 24, `wow-tortoise` 25 (`<prefix><16 hex>`), `wow-wotlk` 8
  (its fixed `password`, contained in none of the 34). 8 is the largest number clearing both bounds,
  and `test_the_containment_floor_is_at_or_below_every_secret_this_app_itself_produces` asserts it
  as `floor <= min(...)` over what that function returns — not as `==` over `catalog.json`, which
  is what it did until 2026-09-05 and which measured a value production never renders: the only
  `mode: fixed` entry is `wow-wotlk`, whose family is `azerothcore` and never calls `render()`.
* **A larger floor buys nothing measurable**, and this is the claim worth writing down because it is
  the tempting one: the collision surface does not empty out with length. The same values yield 102
  distinct 8-character substrings and 78 distinct 12-character ones, and the longest value is 29
  characters (`yulon.local/cmangos-tortoise-`), so a containment collision is *possible* at any
  length a password can have. What makes 8 safe is not that collisions stop but that the strings
  which collide are catalog fragments — `mariadb:`, `/opt/man`, `haracter` — that nobody sets as a
  password.

Below the floor it is equality and not silence: all four leaks ever measured into this mapping (M15,
M-R2, §29's `SOAP_PASSWORD` probe, this entry's `BUILD_ARG` probe) put the password in VERBATIM
under another key, so the shape that has actually happened is caught at any length.

**The traps, each answered by a test rather than by this paragraph.**

* A refusal must never echo the secret —
  `test_the_refusal_about_a_carried_secret_never_prints_the_secret`, over the message, its `repr`
  and the exception's `args`. Not decoration: `_write_dockerfile` re-raises as
  `InstallerError(str(exc))` and `StagedInstaller.run()` hands that to `_record_error()`, which
  writes it into the state file as `last_error`. `native._without()`, the redaction that does exist,
  is spent on one unrelated path.
* The same secret under its DECLARED key is legitimate —
  `test_the_declared_token_may_carry_its_own_secret_and_nothing_else_may`. The exemption is the
  tokens `render()` DROPS (`SECRET_TOKENS`), NOT the tokens the instance declares: a subclass's
  extra secret has no token in `SECRET_TOKENS`, so exempting it would have opened a hole rather than
  closed one, and `test_a_secret_declared_only_on_a_subclass_is_refused_under_its_own_token_too` is
  what fails when the exemption is widened.
* A second caller who forgets the argument must FAIL — required and keyword-only, so it is a
  `TypeError` at the call.
  `test_a_caller_that_forgets_the_secrets_argument_fails_instead_of_losing_the_guard`, written as a
  runtime test because a `# type: ignore` is one line and mypy is not what runs in a user's install.
  An OPTIONAL `secrets=` is still rejected, for the reason this entry gave.
* The guard has to reach the PRODUCTION path, not just the function
  ([[reviews-check-functions-not-call-sites]]) —
  `test_the_write_dockerfile_stage_refuses_a_bland_key_carrying_the_install_password` drives the
  stage, and reaches the password by the route this entry measured (`resolve_secrets(server_dir)`
  reading what `db-password` wrote one stage earlier) rather than by a literal.
* The rule must not refuse a real install —
  `test_no_shipped_public_mapping_collides_with_a_password_the_catalog_can_declare`, all three games
  × {generated shape, `password`}, through the real shipped templates. That covers the two passwords
  the CATALOG can produce, and the catalog is not the only source: a user's own `.db_password` is
  read as written, and **1046 distinct strings collide** with a shipped public token value —
  measured on m910q 2026-09-05, `server_dir=/tmp/fixedsrv/srv`, 1031 by containment over the 19
  values at or above the floor plus 15 by equality over the values below it, `characters`,
  `mariadb:11`, `tw_logon`, `vanilla-`, `/opt/mangos` among them. **That surface is unchanged by
  anything here and deliberately so** — narrowing the rule to spare those is what would reopen the
  hole. What was wrong was the SENTENCE. Until 2026-09-05 such a user read
  `DB_IMAGE (the value declared as DB_PASSWORD) … Drop the key, or file the value under its
  declared token`, about a key this app puts in the mapping itself, which they cannot drop, while
  the one remedy that works — their own password — went unnamed. `render()` cannot name it: it
  holds a `Secrets` and never a path. So the refusal is now a `CarriedSecretError`, caught by
  `_write_dockerfile` ahead of every other `DockerfileError`, which appends
  `_password_origin_note()`: the full path of the password file, that the password is theirs, and
  what changing it costs (the `db-data` volume was created with the old one). Held by
  `test_a_password_that_collides_with_a_rendered_value_is_refused_by_naming_the_password_file`,
  which plants nothing in the mapping — it writes `mariadb:11` into `.db_password` and lets the
  shipped `DB_IMAGE` do the colliding.

**Ten mutations, each killed, m910q 2026-09-05** (`__pycache__` purged on both sides of every one;
the two files are 211 tests at baseline). The value refusal deleted → 11 failed. Containment
weakened to equality → 2. The empty-secret guard deleted → 1. The floor deleted → 2. `secrets` made
optional with a `Secrets("")` default → 1. The two rules' order swapped → 1. The exemption widened
to every token the instance declares → 1. The floor moved to 4 → 1; to 20 → 1. The call site's
`secrets=ctx.secrets` replaced with `Secrets("")` → 2. Two of those "survived" on the first pass and
neither was a survivor: the edits had not applied, and the script was then made to assert its own
substitution before it was allowed to believe a green.

**Rejected, with the measurement: also scanning the rendered TEXT for the secret values.** `render()`
now holds them, so it could, and `write()`'s docstring had only rejected the NAME-scanning version.
What it would add is a template that hard-codes a real secret, and that set is empty in both
directions: a generated password is minted per install, so no committed template can contain one,
and the only fixed password in the shipped catalog is `wow-wotlk`'s literal `password` — belonging
to an entry whose family does not call this function. Against that, containment over a whole
Dockerfile is a far larger false-positive surface than over one token value: a future template line
reading `# the password file is mounted at run time` would refuse that entry's install outright. No
shipped Dockerfile or dockerignore template spells the word today — and the scope matters, because
unscoped that sentence is false: measured on m910q 2026-09-05,
`grep -ril 'password\|passwd' catalog/installers --include=Dockerfile.tmpl --include=dockerignore.tmpl`
returns nothing, while the same grep over the whole installers tree returns
`shared/cmangos/base.yml.tmpl` and `wow-wotlk/native/base.yml.tmpl`, compose templates `render()`
never opens. Reconsider it the day a family with a short fixed password renders a Dockerfile.

**Ticked, and here is the residue, because the title's general form is not what closed.** What is
closed: *a value this app DECLARES as a secret cannot reach a generated Dockerfile through the token
mapping, under any key.* What is still true, and is the boundary rather than a regression:

* **A secret MINTED inside `_public_tokens` — never a field of anything, never in `ctx.secrets` — is
  invisible to this rule as it was to the last one.** The comparison is against the declaration, and
  a value that was never declared is not in it. This entry has said so since 2026-09-02 and it
  remains the honest limit of any by-declaration guard.
* **A secret shorter than 8 characters, embedded in a longer value, passes** — equality cannot see
  it. Two routes reach that state and only one of them is watched. No shipped entry DECLARES such a
  password, and `test_the_containment_floor_is_at_or_below_every_secret_this_app_itself_produces`
  goes red the day the app produces one. But a user's own `<server_dir>/.db_password` is read AS
  WRITTEN and has no floor at all: measured on m910q 2026-09-05, a file holding `abc` makes
  `resolve_secrets()` return a three-character secret, and `--db-pass=abc --verbose` under a bland
  key then renders. That is a limit of the floor and not a bug the floor can fix — containment on a
  three-character string refuses every install — and
  `test_a_user_written_password_below_the_floor_falls_to_equality_and_not_to_silence` is where it is
  measured rather than promised. This bullet said only the first half until 2026-09-05.
* **The build CONTEXT still holds the plaintext**, and has since K.3: `.db_password` at the root,
  `DB_ROOT_PASSWORD=` in `.env`, and `etc/*.conf` after the `conf` stage. Only the leading `*` in
  each `dockerignore.tmpl` keeps them out of what the daemon receives, and `_public_tokens()`'s
  docstring records which tests hold that line and which do not (`etc/*.conf` is asserted by
  nothing). **That is the larger hole of the two, and it is a different entry's shape** — nothing
  about token mappings touches it.
* **It is not a defence against deliberate code.** A caller can construct `Secrets("")`, exactly as
  one can construct a `_Rendered`. The guard is against the careless key, which is what every leak
  measured here has been.

---

**The original entry, unchanged below.**


Found by the independent review of `fix/dockerfile-refuses-any-secret-not-one-name` (merged at
`092bad91`), and **not a regression** — the merged change is strictly better than what preceded it.
It is the part the change does not reach.

`dockerfile.SECRET_TOKENS` is `frozenset(f.name.upper() for f in fields(native.Secrets))`. That moved
the coupling from *one hard-coded name* to *the declaration*, which was the point. But the mapping
handed to `render()` is still built by hand, and **nothing asserts that every secret-bearing key in it
corresponds to a `Secrets` field.** Proved on the VM against the real module, as a probe rather than by
reading:

```
render() with an undeclared secret key -> type _Rendered | secret in text: True
write() accepted it: ['Dockerfile', '.dockerignore']
Dockerfile on disk contains the secret: True
```

The key was `SOAP_PASSWORD`; `native.Secrets` has no `soap_password` field; the value went into the
build context and `write()` had no objection.

**The dangerous direction is the one the docstring does not mention.** `secret_tokens()`'s docstring
states the relationship as fact — "`_tokens()` spells each of its fields as the upper-cased token of
the same name ... so reading the dataclass IS reading the declaration". True today **by convention
only**. A secret that enters the mapping from somewhere other than `Secrets` — a `token_hex` generated
inline, a value read from a file, anything not a declared field — is invisible to this refusal.

**What the capability split changes, and what it does not.** `feat/7.3-token-sets-by-capability` gives
`_public_tokens(server_dir)` no `StageContext` at all, so `ctx.secrets` is not in lexical scope where
the build-context mapping is built.

**CORRECTED 2026-09-02, and the correction is the point of this entry.** What stood here was: *"That
closes the case above at the source, and it is the right level — the fix is structural rather than a
guard someone must remember."* **That was wrong, and I wrote it.** Two independent reviewers refuted it
by execution, each one refuting the round before:

- `_public_tokens(self, server_dir)` can reach the password through `self.resolve_secrets(server_dir)`,
  a public inherited method taking exactly the two things in scope. K.3's `db-password` stage
  (`STAGE_NAMES` index 1) wrote the password into `server_dir` **one stage** before `write-dockerfile`
  (index 2) — this said "two stages earlier" until 2026-09-02 — so `resolve_secrets` no longer mints:
  it reads the real install password back off disk. Six lines, full suite green, `ENV ROOT_PASSWORD=…`
  in a Dockerfile. **This route is live on a first install**, and it is the one that carries this
  entry's conclusion.
- Then: it needs neither a method nor a cache. `generate-compose` writes the plaintext password into
  `<server_dir>/.env` as `DB_ROOT_PASSWORD=`. A file read in `_public_tokens` leaks it, uncached and
  deterministic. Full suite green again. **But the ordering here was framed against the wrong
  consumer.** "One stage before `build`" is true of `build` (index 3 → 4) and is not the question:
  `_public_tokens()`'s only build-context caller is `_write_dockerfile`, the `write-dockerfile` stage
  at **index 2**, which runs *before* `generate-compose` at index 3, the sole writer of `.env`. So on a
  FIRST install this helper reads a file that does not exist yet. It is reachable on a **resume**, or
  on any second press into a server dir a previous attempt had already carried past
  `generate-compose` — and `_write_dockerfile` really does re-run there, because its own docstring
  records that `ctx.state` is not read in that body and a recorded `write-dockerfile` reaches it
  exactly like a first run. Measured 2026-09-02 at `f6ed1b9a` off `stages()` and `STAGE_NAMES`.

So the split is **a price, not a wall**: it raises the cost of writing that leak, and it removes the
secret from the mapping by default. It does not make the leak unwritable, and no arrangement of
parameters can, while the build context itself holds the plaintext — which it has since K.3. **That
conclusion is unchanged by the correction above**: the first route needs nothing but `server_dir`, and
it is live on the first press.

**The lesson, which is the reason to keep this paragraph rather than silently correct it:** every round
here stated a guarantee, and the next reviewer found it too strong. A claim of the form *"X cannot
happen"* is a standing invitation, and three of them in a row were wrong. State what was measured and
when. See [[guards-that-prove-declarations]] — this is the same failure at the level of a design
argument rather than a test.

**It does not close the general
case**: a secret MINTED inside `_public_tokens` rather than passed into it is still unreachable by a
by-name refusal, because it was never a field of anything.

**This is the "defects live between the parts" shape.** The relationship between what `_tokens()` puts
in the mapping and what `Secrets` declares has no owner, so nothing checks it. A cheap enumerating
guard exists and is the recommendation: for each `f in dataclasses.fields(native.Secrets)`, assert that
every key in the mapping whose VALUE equals `getattr(ctx.secrets, f.name)` is spelled `f.name.upper()`.
That checks the relationship by enumeration instead of restating a list, and it catches a
correctly-valued secret filed under a wrong name — which is exactly the case the by-name refusal is
blind to.

**Also fix the docstring either way:** it should stop asserting the link as though something enforced
it. A sentence that describes a convention in the voice of a guarantee is how the next reader concludes
the case is covered.

### 30. `_container_prefix()`'s rebuild branch is satisfiable only by the bug it should refuse — 2026-09-02, **CLOSED 2026-09-04 at `d2b963d5`**

**Closed by removing the field's only correct value rather than by correcting the rule**, which is
what this entry recommended: `_container_prefix()` now refuses `containers.services` outright for any
entry carrying an `install.native` block, whatever the value, and every shipped entry carries one.
The reason is in the code where it is declared: this module WRITES the compose file, and its service
keys come from the templates (`shared/cmangos/base.yml.tmpl` writes `{{CONTAINER_PREFIX}}db` /
`-realmd` / `-mangosd`; `wow-wotlk/native/base.yml.tmpl` writes the literal `ac-database` and
friends), so an entry-level declaration can only restate them or contradict them.

**Both docstrings this entry named at the point of declaration were rewritten, not annotated.**
`composegen._container_prefix()` no longer asserts *"That is literally the
`{{CONTAINER_PREFIX}}<service>` the templates write"* — the sentence that was true only in the buggy
configuration — and `catalog.Containers.services` no longer opens with *"when they differ from the
container names above"*; it says the field is refused since 2026-09-04, that the only correct state
is absent, and why it stays on the model at all (`docker.ContainerSpec.services`, which an adopted
project really does need). The refusal text this entry called "a set of instructions for reproducing
the bug" is gone and cannot come back: the test asserts `"after its service" not in message`.

**The one lower item is now documented where it is read.** `service_names()`'s
`^  ([A-Za-z0-9-]+):` still excludes `_`, and the helper's own docstring (`test_composegen.py:77`)
now records why it is left alone: an underscored service key would read as UNDEFINED, so the
cross-check fails loudly on a good file rather than passing quietly on a bad one, and widening it is
the permissive direction.

**Re-derived on m910q 2026-09-05, on a copy of the tree at `6546b190`.** GREEN:
`pytest tests/test_composegen.py tests/test_dockerfile.py` -> **160 passed**. RED, with the pre-fix
module dropped in whole (`git show d2b963d5^:…/composegen.py`, md5 `86ffd5ba3e8f21ab10a66c33864a40b8`
— the same md5 the test's docstring cites, so its provenance checks out): **3 failed, 88 passed**,
and the failure prints the old refusal verbatim —

```
AssertionError: Regex pattern did not match.
  Expected regex: 'containers\.services'
  Actual message: "the container prefix 'abc' of wow-tbc rebuilds abcone, abctwo, abcthree from its
  service names one, two, three ... Name every container after its service with one shared prefix in
  front of it."
```

The three that go red are `test_an_entry_this_module_renders_may_not_declare_compose_services` (the
discriminating one: it drives both the CORRECT declaration and the §26 bug through
`_container_prefix()`, `entry_tokens()` and `render()`, since a refusal nothing reaches is not a
refusal) and both cases of `test_the_refusal_names_no_service_the_rendered_file_does_not_define`,
which exist because the FIRST rewrite of this rule taught the bug again — it refused the declaration
and then named the entry's three container names as "the names compose must be given", every one of
which answers `no such service`.

---

**The original entry, unchanged below.**

Found by the independent review of `fix/cmangos-compose-service-names` (merged at `08fb785e`), which
fixed the *consequence* and left this. `composegen.py`, `_container_prefix()`, the rebuild branch.

The branch derives a container prefix by checking `container == prefix + service` for every declared
service. The reviewer probed it on the real `wow-tbc` entry, and the result is the wrong way round:

```
services = ("tbc-db","tbc-realmd","tbc-mangosd")   # the CORRECT declaration
  -> REFUSED: prefix 'tbc-' rebuilds tbc-tbc-db, tbc-tbc-realmd, tbc-tbc-mangosd
services = ("db","realmd","mangosd")               # the BUG that section 26 was about
  -> ACCEPTED, prefix='tbc-', compose_services() == ('db','realmd','mangosd')
```

So it is not "a constraint satisfied by no shipped entry", which is how the implementer described it.
**It is a constraint satisfiable only by the defect** — any entry it accepts has bare-suffix
`compose_services()`, which is exactly what `docker compose up` rejects with *no such service*. And its
refusal message — *"Name every container after its service with one shared prefix in front of it"* —
is a set of instructions for reproducing the bug, handed to whoever adds the fifth game.

**Not urgent, and it does not block K.8**: no shipped entry declares `containers.services` any more,
and `FAMILIES` registers only `azerothcore` today. The cross-check added on that branch catches the
consequence for any entry that ships, so this cannot bite silently — it bites the next person to add an
entry, at the moment they follow the error message's advice.

**Recommended: make it refuse rather than warn.** Since the cross-check now covers the separator-eaten
case the rebuild branch was written for, delete the branch and refuse `containers.services` outright
for any entry carrying an `install.native` block. That removes a field whose only correct value is
"absent" — the boundary argument: if the fix has to be remembered at the next site, it is at the wrong
level.

**Two docstrings still carry the false model at the point of declaration**, which is where someone will
read it:
- `composegen.py`, `_container_prefix`: the correction was *appended below* a bullet that still asserts
  *"That is literally the `{{CONTAINER_PREFIX}}<service>` the templates write"*. In the shipped template
  the prefix precedes a hard-coded literal suffix, never a `containers.services` value — that sentence
  is true only in the buggy configuration. Rewrite the bullet; do not annotate it.
- `catalog.py`, `Containers.services`: the field still opens with *"when they differ from the container
  names above"*, and nothing at the declaration warns that a native-rendered entry setting it
  *correctly* is rejected. One sentence, or resolve the above and delete the field.

**Also noted by the same review, lower:** the cross-check is one-directional (`selected` is a subset of
`defined`), so a `service_names()` helper that *over*-reports only makes it more permissive — a mutation
dropping its stop-at-top-level rule was killed by a TBC-only equality test, not by the cross-check. And
that helper's `^  ([A-Za-z0-9-]+):` excludes `_`, so an underscored service key would read as undefined:
loud, therefore acceptable, but undocumented.

### 31. `wsl.find_servers()` collapses fifteen situations into one sentence — 2026-09-02, OPEN

The same defect as the adoption guard fixed at `57130ff0`, one level up, and load-bearing. Found by
that branch's implementer, confirmed and **enlarged** by its reviewer, who counted the cases rather
than accepting "at least seven".

`adopt_from_wsl()` opens with `found = wsl.find_servers()` / `if not found:` and then tells the user
*"No servers found in WSL — No Docker Compose projects were found in the running WSL distros."*

An empty tuple is returned for **at least fifteen distinguishable situations**, counted from
`yulon/wsl.py` and `yulon/platform.py`:

*`distro_states()` yields nothing:* (1) `_which(WSL_PROGRAM)` is None — no `wsl.exe` on PATH;
(2) `wsl -l -q` raises `OSError`; (3) it raises `TimeoutExpired` at the 60 s probe timeout;
(4) any other `SubprocessError`; (5) a non-zero exit — the WSL service wedged;
(6) **the listing genuinely names no distro, the one case that means what the dialog says**.

*distros exist, none is probed:* (7) every distro is stopped and none is in `include`.

*a distro is probed and answers nothing:* (8) `docker_prefix(distro)` is None; (9) compose exited
non-zero — daemon down inside the distro, compose absent, permission denied; (10) `_compose_ls()`
raises `OSError`/`SubprocessError`, including the same 60 s timeout.

*compose answered, nothing survives parsing:* (11) stdout is not JSON — an older compose without
`--format json`, or an error printed to stdout; (12) JSON that is not a list; (13) every project lacks
`Name` or `ConfigFiles`; (14) every project's first config path starts with `/mnt/` — Docker Desktop
integration distros; (15) `wsl_unc_path()` returns None for every path.

The dialog does mention stopped distros, which covers (7). **The other thirteen are asserted as fact**,
and the user is sent off to start a distro that may already be running.

**The sharpest evidence that this is a real defect and not a reading**: `wsl.py` already argues this
exact point about itself, two functions above, in `missing_distro_problem()` —

> **An EMPTY listing is not evidence of anything.** `_wsl_list()` answers `()` for four different
> things — no wsl.exe on PATH, `OSError`, a timeout, and a non-zero exit — and only one of them means
> "there are no distros". […] Reading that silence as "the distro is gone" sent the user off to
> re-adopt a server that was never missing

The rule was written where a distro is accused of being deleted, and not applied where a user is told
they have no servers. **A rule that lives in one function's docstring protects one function** — the
boundary argument again: if it has to be remembered at the next site, it is at the wrong level.

**Not fixed with the adoption branch, deliberately**: it changes `find_servers()`'s return type and
every caller and test, and four agents were live in the tree. It is the natural next step after that
branch, and it is bigger than it looks — the return type has to carry *why* it is empty, the way
`Identification` now carries why a folder is unidentified.

**Also confirmed by the same review, separate and trivial:** `wotlk_modules.applier()`'s docstring says
*"pass `sql=None` explicitly via a caller that has no database to get every SQL step reported as
skipped instead"*. `sql=None` is the parameter's default, and the body is `runner = sql; if runner is
None: runner = DockerSql(...)` — so passing it explicitly is indistinguishable from omitting it and
both build a `DockerSql`. `Applier` genuinely has that skip-everything mode (`apply.py` documents it);
`applier()` intercepts every route to it. Same family: a `None` default folding "unset" into
"explicitly absent". Fix is a flag or deleting the sentence.

### 32. Three Install buttons now run an engine that has never installed a server — 2026-09-02, OWNER DECISION

Not a defect. Raised by K.8's independent review so that it is a choice rather than a discovery.

K.8 registered `cmangos` in `FAMILIES`, so `wow-tbc`, `wow-vanilla` and `wow-tortoise` no longer fall
back to their bash scripts — they dispatch to `CmangosInstaller`. That is exactly what the plan asks
for, and every unit-level guarantee is in place: dispatch is proved for all three entries enumerated
from `catalog.json`, the stage tuple is pinned, and a working-looking wrong answer
(`FAMILIES["cmangos"] = AzerothCoreInstaller`) is killed by test.

**What has NOT happened is a live install.** Gates 7.4a/b/c (TBC through `build`; extract + mmaps with
the 2.4.3 client; conf + import + ready) are unticked, and `pyplan/gates/` does not exist yet. The 7.3
primitives gate is written and ready but was deliberately not run — it starts containers and pulls
images, and the standing rule is that the owner starts a run himself.

**The blast radius is bounded by status**: those three entries are `beta`, `beta` and `wip`. A user who
presses Install gets a real attempt rather than a refusal, and the failure modes are the ordinary ones
of an unexercised path.

**The alternative, if that is not wanted yet**, is one line: leave the family registered and keep the
three entries off the Install path until their gates are ticked. It is a data change, not a code change.
Recording it here rather than deciding it — the trade is "an unexercised path is reachable" against
"the work 7.3 exists for is not usable", and that is the owner's call.

**Related and already corrected** (`514c2351`): the 7.3 primitives gate cannot be ticked from the
2026-09-01 record filed under 7.1. That run predates `test_sqlplan_live.py` entirely — 21 test functions
then, 23 now — so it could not have executed two of the six things the gate line names.

### 33. The suite has load-sensitive tests, and a flake let a merge go through on red — 2026-09-02, the steam-deck half FIXED 2026-09-02, **the rest CLOSED 2026-09-04 at `d2b963d5`**

**The log-panel half.** `test_log_panel.py::test_a_runner_dropped_before_its_thread_runs_does_not_leave_the_worker_dead`
held its job open with `gate.wait(5.0)`, so "provably still in flight" was true only for five
seconds of wall clock and a loaded box made the verdict depend on the box. The clock is gone —
`gate.wait()` with no bound — and every remaining wait in that file goes through `conftest.pump_until`,
which does not return silently on its deadline: it fails, naming the turns/s it actually got against
`PUMPED_HEALTHY` (98/s idle, 24/s with the whole suite 14-way at loadavg 83), so a starved box and a
wedged subject are told apart IN THE REPORT rather than by re-running. That is the damage this entry
is about, answered directly.

**"`--dist loadfile` is a harness setting, so the test is still order-sensitive for anyone who runs
the suite another way" — measured, and no longer true.** On m910q (4 cores, a live `mangosd` taking
~60% of one of them throughout), 2026-09-05, on a copy of the tree at `6546b190`, with **no
`--dist loadfile` anywhere**:

```
-m 'not integration' -n auto  x5   ->  2536 passed, 4 skipped   (18.0 / 19.5 / 17.7 / 20.9 / 21.2 s)
-m 'not integration' -n 14    x3   ->  2536 passed, 4 skipped   (20.2 / 20.7 / 19.2 s)
```

`-n 14` is 3.5x this box's cores; loadavg was 3.6, 5.4 and 10.4 after the three runs. Eight
whole-suite runs under the exact distribution this entry says the harness was chosen to avoid, zero
failures, and the count matches CI's baseline.

**The bound the fix leans on is audited rather than trusted.** `test_no_wall_clock_bound_in_this_file_is_written_as_a_bare_number`
parses the file and requires every deadline to be one of the named constants — proven RED here on
2026-09-05 by putting `gate.wait(5.0)` back: `AssertionError: … Extra items in the left set: '5.0'`,
`1 failed, 21 deselected`. `test_an_expired_pump_says_so_and_reports_how_fast_it_ran` holds the
report itself (restoring the silent return left `21 passed` without it, measured 2026-09-04). Five
files now audit their own deadlines through the one helper.

**What is recorded rather than fixed, because it cannot be fixed.** The original 60 s stall was never
reproduced, so what is proven is that this test no longer depends on the two things that were
removed — not that the red run of 2026-09-02 cannot recur. Its own docstring says exactly that. And
the identity of that failure is gone for good: the grep that read it kept only the totals. The
lesson survives as machinery in two places — `pump_until`'s report, and `run-tests-vm.sh --checks`,
which until 2026-09-04 chained every step through one `;`-joined ssh command and `exit $?`, so the
status was the LAST step's and a red suite exited 0. It now accumulates `rc` across the steps and
prints `=== --checks: RED (see !! lines above) ===`. **That helper lives on the laptop
(`C:\Users\perzi\run-tests-vm.sh`) and not in this repo**, so this is the one claim in this entry a
reader cannot re-derive from the tree; its reasoning is in the script's own header at lines 203-208.

---

**The original entry, unchanged below.**


**What happened, recorded because the process failure is the more useful half.** The verification run
before merging `fix/gate-7.2-7.3-code` reported **`1 failed, 2003 passed, 3 skipped`**. The merge went
through anyway, because the check and the merge were chained in one command and the result arrived
*after* the action. Three re-runs immediately afterwards gave **2004 passed / 3 skipped**, so the tree was
fine — but that is luck, not process. **Never chain a verification with the action it gates.**

**The flake itself is real and has at least two known candidates**, both load-sensitive rather than
order-sensitive:

- `test_steam_deck_script.py::test_without_a_terminal_the_client_wait_sleeps_instead_of_reading_stdin` —
  seen by a gate reviewer hitting its own **60 s subprocess timeout under 15-way parallel load**; 14/14
  green when re-run serially. A wall-clock timeout inside a parallel suite is a flake generator by
  construction: the bound is on the child, the contention is on the box.
- `test_log_panel.py::test_a_runner_dropped_before_its_thread_runs_does_not_leave_the_worker_dead` —
  documented in `run-tests-vm.sh`'s own header. It fails under plain `-n auto` and passes under
  `--dist loadfile`, which is why the helper uses that distribution. Thread-timing split across workers.

**Why it matters more than an occasional red run.** A suite that fails once in several runs teaches its
readers to re-run rather than to look — and the cost is not the wasted minute, it is that a *real* single
failure becomes indistinguishable from noise. Every merge decision tonight rested on a count.

**The steam-deck half is fixed (2026-09-02, `fix/remaining-error-boundary-holes`).** Measured on
yulon-ubuntu first: that test costs **0.16 s** serially, **under 0.61 s** inside `-n auto --dist loadfile`
(it did not reach the slowest-twelve list at all), and **0.24 s** at 30 workers on 15 cores. Against 0.16 s
the old bound was a stopwatch reading, and a 60 s stall is not contention of that shape — the VM runs its
fifteen workers with about 2 GB free, and a starved or swapping box stalls a healthy process for a minute.
So the bound is now `HANG_BOUND`, named and documented as a **deadlock breaker**: contention is bounded by
some multiple of a run costing milliseconds, a hang is unbounded, and any finite bound catches the
unbounded case. Deleting it is not an option — without one, that file's pty test wedged the suite when its
subject regressed, which CI reports as a stuck job rather than a red one. `DWELL_PROOF` names the one bound
in the file that IS an assertion and points the other way (the script must still be running when it
elapses, so load makes it pass more surely). An AST test requires every `timeout=` there to be one of the
two names, because a number typed at a call site carries no argument for its size. And a timeout in the
flaking test now reports how far the script actually got, so a starved run and a hung script are told apart
**in the report** rather than by re-running — which is the damage this entry is really about.

**Rejected, and why.** *A bound proportional to load* needs a calibration step that is itself load-sensitive,
and a bound that moves run to run cannot be reasoned about when it fires: you cannot tell a hang from a
starved calibration. *Marking the test serial* is not available — xdist's `--dist loadgroup` co-locates
tests, it does not serialise them, and a cross-worker lock would still leave the other fourteen workers
loading the box, so it would hide the bound's fragility behind a scheduling choice rather than remove it.
*A merely larger number* would have been the same defect with a bigger constant; what changed is what the
bound MEANS and that its reasoning now travels with it.

**Still open:** the `test_log_panel.py` candidate, mitigated but not fixed — `--dist loadfile` is a
harness setting, so the test is still order-sensitive for anyone who runs the suite another way. And the
identity of the failure in the run above was never captured — the grep that read it kept only the totals —
which is itself the lesson: **capture the failing test id, not the count.** The original 60 s stall was
NOT reproduced here; the sizing above rests on the measurements, not on a reproduction.

### 34. Two more refusals that were nobody's to catch, in the same spine — 2026-09-02, FIXED

The `composegen.render()` blocker (`b22ab381`) was one instance of a class, and an audit of every other
outward call in the install spine found two more. Both were reproduced through the real
`install_wiring.main()` on `697adca6` before either was touched, and both cost the same two things the
blocker did: a traceback where `main()`'s own docstring promises "the sentence written for a person", and —
wherever the call sits inside `run()` — no `_record_error`, so the state file kept `"last_error": ""` where
every other stage failure records its own.

**A folder that will not list.** Four sites asked `folder.iterdir()` bare: `_claim_folder()`, which all
four shipped games reach through preflight and `_guard()`; `stage_clone_sources()`, which the three CMaNGOS
games bind; and AzerothCore's `_clone_core()` and `_clone_modules()`. `OSError` is not an `InstallerError`,
and `iterdir()` raises it for a permission change, an unreadable mount, a drive that went away, or a stale
UNC path into a WSL distro — the app reaches folders that way often enough that `Identification.UNVERIFIED`
exists in the UI for it. All four go through one `_listing()` now, which **refuses rather than assuming
empty**: a listing that failed says nothing about what is in the folder, and the caller's next move on
"empty" is a clone whose seam `shutil.rmtree`s a destination it does not recognise. Mutating the helper to
return `[]` on `OSError` showed exactly that — the CMaNGOS run walked past the unreadable folder and died
four stages later asking for a client directory. `_guard()` stays outside `run()`'s `try`, which is correct
and not a bug (its refusals concern a state file that may be somebody else's), and the regression asserts
that folder was left as empty as it was found.

**A reset that will not finish.** `stage_import()`'s `partial` branch called `gate.reset()` bare. Its seam
on the AzerothCore path, `repair.reset_unfinished()`, declares three refusals — `MaintenanceError`,
`ApplyError` and a bare `RuntimeError` for player data — and none of the three is an `InstallerError`.
Wrapped with `except Exception` rather than a named tuple, because `gate` is an `ImportGate` **protocol**
and the spine cannot import `controller_wow_wotlk` to name two of them; `InstallerError` is re-raised
untouched and first, so `CallableGate`'s own no-reset-seam refusal is not buried inside the wrapper.

Regressions drive the real CLI at every site, assert the sentence the user gets, assert `last_error`
**equals** it, and assert the neighbouring rule was not the one that fired. Eleven mutations, eleven killed.

### One thing worth keeping

Three of these have an obvious fix that is **wrong**, and two of them arm a worse bug:

1. Setting `reinstall=True` (the natural fix for the silent-success bug) turns the unreliable image
   check from harmless into **deleting a good 2–4 hour build**. Fix the image check first.
2. Deleting `container_name:` makes every container `<project>-ac-worldserver-1`, and the controller
   finds nothing.
3. Changing the compose-file guard before installs are resumable breaks a case that currently works.

The pattern: *check whether the thing you are about to make true is guarded by something that is
only safe while it is false.*


### 35. Every CMaNGOS install is unreachable from any other machine — 2026-09-02, FIXED 2026-09-02

`realmd.realmlist.address` is left at `127.0.0.1`. A client on another machine authenticates
FINE — realmd answers on 3724, the account is accepted, the realm list arrives — and then the
client is told the world server lives at `127.0.0.1:8085`, which on the player's own PC means
the player's own PC. It hangs at "Connecting" or drops back to the realm screen with nothing
useful said.

**Found by driving it (2026-09-02).** WoW TBC on `m910q`, reached over Tailscale at
`100.78.24.50`. Auth succeeded and the realm `MaNGOS` appeared; the world connect could not.
One `update realmd.realmlist set address='100.78.24.50'` later, the same client reached a
character screen, created `Administrato` (guid 901, account 1, Troll warrior) and the world
server logged `Sessions online: 1`.

**The cause stated here when it was filed was WRONG, and the correction is the useful part.**
This entry said the installer "cannot guess" the address and that the work was therefore owed to
a future phase. That was not true when it was written. `yulon/networking.py` already held every
piece: `lan_ip()` to detect this machine's address on the network, `advertisable()` to decide
whether a value is reachable from anywhere else, and `realmlist_sql()` to write the row for
either family. The 6.5 networking work had built all of it. Nothing was missing except a CALL:
no install stage ever invoked any of it, so a complete set of working machinery sat one line
away from the defect it was built to prevent, and the entry blamed the problem for being hard.

Worth keeping as a lesson rather than editing away. "The installer cannot know this" is a
comfortable thing to write, it reads as analysis, and it stopped anyone looking for the module
that already knew. The check that would have caught it costs nothing: before recording that a
value is unknowable, grep for the thing that would compute it.

What IS true from the original paragraph: the address is whichever one the PLAYER can reach, and
guessing wrong is worse than `127.0.0.1` because it looks configured. That is why the fix writes
only a detected LAN address, says in plain words what it wrote, and — after a second review —
leaves ANY already-reachable row alone rather than any row merely equal to the LAN address. See
`native._advertise_realm()`.

**Not the same as the networking work in 6.5.** That line is about firewalls, port-forwarding
and a realmlist WRITER for the client side. This is the SERVER side — one row in the server's
own database — and a launcher that installs a server nobody outside the box can join has not
finished installing it.

**Fixed in `40fa2e76`, corrected in `f90d3e55`.** The first commit added the stage; the review
of it found that the guard compared the row against the LAN address instead of asking whether
the row was REACHABLE, so an ordinary resume overwrote a public address somebody had set for
internet play with a LAN one, while printing that other machines could now reach the server.

**Live evidence, and what is still owed.** VERIFIED on WotLK/AzerothCore: the Windows gate on
`yulon-win11` (2026-09-03 02:16) printed "The realm now advertises 172.30.52.116" and the server
came up — so the AzerothCore half, which this entry marked UNVERIFIED, is now driven. The
original TBC reproduction stands. Tortoise, on the run that closed 7.6: `172.30.55.119`.
And Vanilla — whose install finished three hours BEFORE the stage existed, so its log said nothing
either way — was resumed on 2026-09-03 and moved from `127.0.0.1` to `172.30.55.119`, read back
out of `realmd.realmlist` rather than off the log. **All four entries are now driven**, and the
UNVERIFIED note this section carried is spent.
### 36. A shipped SQL-plan fix never reaches an install that already has a marker — 2026-09-03, OPEN

`MarkerGate` reads a marker row as `imported` **whatever the plan hash says** — `cmangos._import()`
says so in as many words, and it is a deliberate decision with a good reason behind it: a marker is
written only after `verify()` passes, so it means "these databases were imported and checked", and
re-importing over somebody's live world to chase a catalog edit would be far worse than not.

**What it costs, found while driving 7.6.** Two SQL phases were added to `wow-tortoise` today
(`playerbots characters`, `playerbots world`) because the bots crash the worldserver on boot
without their tables. Re-running the installer over the existing install printed

    --- import
    They are already imported; leaving them alone.

and the new phases never ran. That is correct by the rule above and useless to the user: every
Tortoise install made before this fix stays broken, and the only route to the fix is a new install
into an empty folder. The same will be true of the next SQL correction, and of every one after it.

**Why the obvious fix is wrong.** Comparing the stored plan hash and re-importing when it moves
would re-run `world base` — 285 table dumps — over a database that may hold characters somebody
played. The five-branch table refuses `populated` for exactly that reason, so the honest shape is
probably a *migration* notion: phases marked as safe to re-apply, applied by name, recorded
individually — which is what the Tortoise core itself does with its `migrations` table, and what
`sqlplan` does not have.

**Not urgent, and not nothing.** No shipped install is affected yet because Tortoise has never been
released. It becomes urgent the moment one is, and the decision (accept a re-install, or build the
migration notion) is the owner's rather than one to make quietly inside a phase.

**Where to start.** `yulon/catalog/families/cmangos.py::_import` (the ordering argument),
`yulon/catalog/families/sqlplan.py` (the marker and `verify`), and `native.stage_import`'s
five-branch table. `SqlPlan.marker` already hashes the canonical plan, so the value needed to
detect the change is stored — nothing reads it back.
### 37. The vmap retry recipe could not fire on the failure it names — 2026-09-03, FIXED

`wow-vanilla`'s `ExtractPlan.retry` is `when_log_matches: "Segmentation fault|core dumped"` over
`vmap extract` / `vmap assemble`. `_retry_matches()` searched the tool's own output for that text.

**A crashed tool does not print it.** `Segmentation fault (core dumped)` is a message a SHELL's job
control writes about a child it reaped. These tools are `exec`'d as the container's PID 1 with no
shell in between, so a tool that dies of SIGSEGV writes nothing at all and the container reports
only the status. Probed on `yulon-ubuntu`: every signal-killed container returned **zero bytes** of
output and **zero** matches for the pattern.

**Why the test suite agreed with it.** `test_retry_matches_answers_every_ending_and_not_just_the_regex`
built its crash as `AttachedRun(139, ("Segmentation fault (core dumped)",))` — the status AND the
words. The fixture handed the matcher the text it was looking for, so nothing ever asked whether a
real crash produces that text. One line of a fixture stood between a green suite and a guard that
could not fire.

**Fixed** by `RetrySpec.when_returncode_in`, checked BEFORE the pattern, with `[139, 134]` on the
Vanilla entry (128+SIGSEGV and 128+SIGABRT). The text match is kept, because a tool that prints a
crash and exits non-zero on its own is a different and real case. A validator refuses `0` and the
negatives, which `_retry_matches` already answers before it looks at anything.

**What is still NOT proven, and is the reason 7.5 stays unticked.** The recipe has never fired on a
real crash, because the crash cannot be produced. `ulimit_stack_unlimited` is documented as existing
because "the vanilla vmap extractor overflows the default stack on some maps and segfaults"; that is
**unreproducible on this box and this client**, measured three ways against the real 1.12.1 client:

| forced condition | result |
| --- | --- |
| flag off (default 8 MB stack) | both tools completed — Buildings 5076, vmaps 5667 |
| `--ulimit stack=1048576` (1 MB) | both tools completed |
| `--ulimit stack=65536` (64 KB) | `vmap_extractor` completed, exit 0, 67 KB of output |

So the flag's own justification is now measured false here. It is NOT removed: one box and one
client cannot refute a crash reported on others, and the flag costs nothing. But the comment
claiming the behaviour should say where it came from rather than assert it as fact.

**One more thing the probing turned up, worth knowing before anyone tries this again.** A container's
PID 1 does not receive an unhandled signal sent from outside — `docker kill --signal=SEGV` on a
`sleep` left it `Status=running`. A crash has to come from INSIDE the process. That is why the
probes above squeeze the stack rather than signalling the container.
### 38. An interrupted first stage still leaves a folder the app disowns — 2026-09-03, FIXED

`_run_one()` writes `.yulon-install.json` only after a stage FINISHES. A stage-one failure
therefore leaves `src/` and no record, and `_guard()` refuses the retry with "is not empty and was
not created by this app" — a sentence that is false, and whose only remedy is deleting a
part-finished clone by hand.

`5eef8d9f` added `_claim_if_ours()`, which records the state on the `except InstallerError` path
when the folder started empty. **An adversarial review the same day returned "do not ship", and
its central point is correct:**

**1. It does not cover the failure that produced it.** The bug was found by a KILL — the
TBC-on-Windows gate died with its ssh session mid-clone and then refused its own 162 MB checkout.
SIGKILL, power loss and unhandled exceptions never reach an `except` block, so the harshest
failures still leave an unclaimed folder. The test that shipped with the fix drives a cooperative
`InstallerError`, which is a different thing from process death — the fix is real for clean stage
failures (a network error mid-clone) and absent for the case that motivated it.

**2. `started_empty` is a stale observation.** It is read once at the top of `run()`, with no lock
and no directory identity check. Between that read and the failure handler, a second install can
start in the same folder, the user can drop a file in, or the path can be replaced or remounted —
and the handler then writes an ownership record covering all of it. Ownership changes what later
runs are willing to do to a directory, so a wrong claim is not merely untidy.

**3. A failed claim is silent.** `_listing()` refuses on an unreadable directory and the handler
swallows it; `write_state()` only logs its own `OSError`. Either way the user is told the stage
error and not that the incomplete install could not be claimed, which is the fact that decides
whether their retry will work.

**The shape of the real fix, and why it was not done in that commit.** An install-intent claim
written BEFORE the first mutating stage, created atomically, carrying the run id.
`yulon/ownership.py` already has the vocabulary — `UNCLAIMED` / `OWNED` / `UNKNOWN` — so this is
not a new concept, it is one the install path does not yet use early enough. The obstacle is
real: three tests forbid an early write, two because an install into the USER'S OWN git checkout
must leave it untouched, and `_guard()` deliberately defers that judgement to the clone stage so
the refusal can name whose fork it is. The review's answer is to move that non-mutating checkout
question AHEAD of the claim. That is a design change across the spine and the families, not a
patch, and it is the owner's call.

**Also worth doing whatever is decided:** reword the refusal to state only what is observable —
no valid Yu'lon ownership record exists — rather than asserting who created the contents. That
sentence is wrong in exactly the case a user is most likely to hit it.

**4. The two guards disagree about who owns the folder (found 2026-09-03 by turning the fix's
own test from a negative into a positive).** `_claim_if_ours()` teaches `_guard()` that the
folder is this install's. It teaches `stage_clone_sources()` nothing: that stage asks whether
there is a `.git` at each `dest`, and a destination holding files and no `.git` is refused —
correctly, because the clone seam `shutil.rmtree`s a destination it does not recognise. So a
first stage that wrote non-git files and then died still ends at a manual delete, now behind a
second, narrower sentence ("has files in it but is not a checkout of …"). It is honest and it no
longer claims the app did not write those bytes, which is why this is a limitation and not a
regression. Measured, not argued: `test_a_claimed_folder_whose_leftovers_are_not_a_checkout_is_still_refused`.

The common case is fine, and the difference is worth knowing before deciding anything. `git
clone` creates `.git` in its destination within its first moments, and all four entries clone
into per-source directories (`.` for AzerothCore, `src/<repo>` for the three CMaNGOS games), so a
killed clone leaves a partial checkout that `stage_clone_sources()` recognises and resumes. The
refusal above needs a kill inside that first instant, or a stage that wrote something other than
a checkout. An early ownership claim the clone stage could CONSULT is what closes it, which is
the same design change §38 already turns on.

**Not a defect: the foreign-install-id mutation.** A review mutated `_claim_if_ours()` to write
somebody else's `install_id` and reported that the suite stayed green. It does, and the reason is
production code rather than a weak test: `_record_error()` runs on the very next line and
rewrites the file from the same `state`, so a wrong id written inside the claim is erased before
anything can observe it. `test_a_folder_this_app_filled_and_then_failed_in_is_still_its_own_on_the_retry`
now asserts the recorded `install_id` by VALUE anyway — the mask is real, and it is not a reason
for the tests to hold no opinion about what was claimed. Verified by mutating `_record_error()`
instead: two tests fail.

**FIXED 2026-09-03 by moving the claim ahead of stage one — and the obstacle recorded above was
not real.** `_claim_before_writing()` replaces `_claim_if_ours()`: the record is written
immediately after `_guard()` accepts the folder and before any stage runs, so SIGKILL, a power cut
and an unhandled exception are covered along with the cooperative failures. Finding 1 closed.

**Why the early write turned out to be safe, which this entry had wrong.** It said three tests
forbid writing before stage one and that the "whose checkout is this" question had to be moved
ahead of the claim first. Re-read: it is already ahead. `_guard()` refuses every non-empty folder
outright, with ONE deliberate exception — a directory holding `.git`, deferred so the clone stage
can name whose fork it is rather than say "this folder is not empty". So the only folder that can
reach stage one non-empty and unclaimed is somebody's own checkout, and `started_empty` is exactly
the predicate that excludes it. All three of the tests in question drive a `.git` directory; not
one of them constrains an empty folder. Proved by mutation rather than by reading: deleting the
`started_empty` guard so every folder is claimed fails precisely those three
(`test_a_first_install_into_the_users_own_checkout_is_refused_and_left_alone`,
`test_an_enforcing_box_still_recognises_a_users_own_checkout_of_this_repository`,
`test_a_failure_before_anything_was_written_leaves_no_state_file`) plus the new one written beside
them. They are the guardrail, not the obstacle.

**Finding 2 narrowed, not eliminated.** `started_empty` is still an observation without a lock, but
it is now read and acted on in consecutive statements instead of being carried across a
multi-hour install and spent at the end. A second install, a dropped-in file or a remount can
still race it; that race is microseconds wide rather than hours.

**Finding 3 closed.** A claim that does not land is a refusal naming the file, checked by reading
the path back — `write_state()` logs its own `OSError` and returns, which is right for the callers
recording PROGRESS and wrong for this one, where a silent loss rebuilds the whole bug. The late
version could not refuse anything: it ran with an `InstallerError` already in flight and raising
would have replaced the sentence the user was about to read. Nothing is in flight at the new site.

**Finding 4 (the two guards disagreeing) closed as a consequence.** `stage_clone_sources()` still
asks its own question — is there a `.git` at this dest — and still refuses non-git leftovers,
which is correct, because the clone seam `shutil.rmtree`s a destination it does not recognise.
What has changed is that the case is now unreachable from an interrupted install of ours: the
folder is claimed before anything is written, so the retry is a resume rather than a stranger.

**Tests.** `test_the_folder_is_claimed_before_the_first_stage_writes_a_single_byte` asks the
question from INSIDE the clone seam, which is the only place the answer distinguishes an early
claim from a late one — both leave the same file on disk afterwards.
`test_a_death_that_runs_no_except_block_still_leaves_the_folder_claimed` raises `KeyboardInterrupt`
out of stage one: `run()` catches `InstallerError` and nothing wider, so that traverses exactly the
code a signal would have skipped. Three mutants, three kills.

**Still owed:** the refusal wording. "is not empty and was not created by this app" is now
unreachable for our own interrupted installs, but it is still the sentence a user meets when they
point the app at a folder holding something else, and it still asserts who created the contents
rather than what is observable — that no valid Yu'lon ownership record exists.

---

### 39. The LAN step locks you out of a remote Linux box — 2026-09-04, OPEN

Found by 7.1's own gate, the hard way: the lane that pressed it lost its ssh session and had to
recover by driving the guest's GNOME desktop through the Hyper-V synthetic keyboard. Evidence and
the whole recovery are in `pyplan/gates/7.1-ubuntu-2026-09-04/ufw-lockout.txt`.

**What it does.** `networking.apply()` on a Linux box with `ufw` emits three commands and runs all
three:

```
[['ufw','allow','3724/tcp'], ['ufw','allow','8085/tcp'], ['ufw','--force','enable']]
```

`ufw enable` brings up ufw's default policy, which is **deny incoming**. The two game ports are
allowed. Port 22 is not. So on any box you reach over the network — which is every headless server
this app is for — the LAN step is a remote lockout with no warning:

```
report.done         = ['ufw allow 3724/tcp', 'ufw allow 8085/tcp', 'ufw --force enable', ...]
report.skipped      = []
report.manual_steps = []
```

Nothing warned, nothing was skipped, and no manual step mentioned SSH. The session that runs the
step SURVIVES — conntrack keeps an established flow alive — so the operator gets no signal at all
until the next connection:

```
Connection closed by UNKNOWN port 65535
Connection timed out during banner exchange
```

**Why it is worse than it looks.** The three-command plan is the same on a laptop and on a remote
server, and the app has no way to tell those apart today. On the laptop it is correct and useful.
On the server it costs the user their access, and the only recovery is physical or out-of-band —
here it took the hypervisor's synthetic keyboard, and `Msvm_Keyboard.TypeText` was unusable on that
guest (17 characters arrived as `dddmm`, twice), so the fix had to be typed as virtual key codes.
A user with a rented VPS and no console has no equivalent.

**Not a hypothetical severity.** Phase 6's privilege-transparency line already forbids the app from
quietly taking root-equivalent access; taking away the operator's only route back in is the same
class of harm and is not covered by that rule's wording.

**What a fix has to decide, and none of it is obvious:**

* whether to allow SSH before enabling. `ufw allow 22/tcp` is not right either — sshd may be on
  another port, and reading `sshd_config` to find out is a new dependency on a file we do not own.
  The honest version reads the port from the ACTIVE listener rather than from config.
* whether to enable ufw at all when the app did not find it already enabled. Turning a firewall ON
  is a change to the machine's posture that the user did not ask for; the ports could be opened and
  the enable left to them.
* whether "am I connected over the network right now?" is even answerable. `SSH_CONNECTION` in the
  environment answers it for the common case and is absent under a GUI, which is exactly the split
  that matters.

Until it is decided, the step should refuse on a box where `SSH_CONNECTION` is set, and say why.

**THE FIX WENT IN THE SAME NIGHT AND DID NOT CLOSE THIS, 2026-09-04.** An adversarial review
reproduced the original lockout against the committed code on `m910q`, with no mutation, and the
probe was re-derived a second time from that box's own `ss` output while writing this entry. The
fix has two halves and only one of them holds:

* **Withholding the enable — correct, and it is what closes the bug for every caller that exists
  today.** `plan()` defaults to `enable_firewall=False`, and that branch drops every command
  `_turns_ufw_on()` matches and returns `UFW_ENABLE_WITHHELD` on both `refusals` and `warnings`
  (`networking.py:247-253` at `4c959d70`). `ufw allow` lands whether ufw is on or off, so nothing
  the user asked for is lost.
* **The guard behind `enable_firewall=True` — wrong, and wrong in a way that re-arms the exact
  lockout.** `_sshd_listening_ports()` computes its second return value from *"did any socket carry
  an owner"* rather than *"could this probe have seen a **root-owned** socket"*: `attributed = True`
  is set on the first line that carries `users:(` at all — `networking.py:136-170` at `4c959d70`,
  the assignment itself at `:158` — whatever owns it. One listener owned by an ordinary user is therefore enough to make an unprivileged probe
  report "this table is readable, and there is no sshd on this box."

**Measured on `m910q`, read-only, as `pk`.** `ss --no-header --listening --tcp --numeric
--processes` there prints exactly one owner column — `LISTEN 0 10 *:3389 *:*
users:(("gnome-remote-de",pid=1067,fd=8))` — and pid 1067 runs as `pk`, uid 1000. sshd's own
`0.0.0.0:22` and `[::]:22` lines print with **no** owner column, because sshd is root's. Feeding
that real output through the partition in `_sshd_listening_ports()` returns `ports = []` and
`listeners_readable = True`. With `SSH_CONNECTION` unset — a GUI session, which is what this
launcher is, and also `sudo`, `tmux` and any systemd unit — `_guard_the_way_back_in()` takes
`if not asked.connected and asked.listeners_readable` (`networking.py:256-261` at `4c959d70`) and
returns the three commands **unchanged**, with `refusals` and `warnings` both empty. That is `ufw --force
enable` with only 3724 and 8085 allowed, and the same four empty reports this section opened with:
`report.done` carrying the enable, `report.skipped`, `report.manual_steps`, `warnings` all empty.

**The second route, which needs no ordinary-user listener at all.** A socket-activated sshd
(`ssh.socket`, the default on current Ubuntu and Fedora) shows its listener owned by **systemd,
pid 1** — `users:(("systemd",pid=1,fd=150))` — and `'"sshd'` does not match that, so even a probe
running as **root** reads the box as having no sshd: `ports = ()` with `listeners_readable = True`,
which is the same silent-enable branch. The shape is visible in this night's own gate capture,
`pyplan/gates/7.1-ubuntu-2026-09-04-clean/gate71-realm-and-account.log`, where `sudo ss -lntp` on
`yulon-ubuntu` prints `users:(("sshd",pid=17501,fd=3),("systemd",pid=1,fd=150))` — sshd there had
already been triggered, so both halves appear; on a box where it has not been, only the systemd
half is there.

**Neither route reached a user.** `enable_firewall` occurs in exactly two files —
`pylauncher/yulon/networking.py` and `pylauncher/tests/test_networking.py` — so no view, service or
controller can pass `True` today, and every production call takes the withholding branch. That is
what makes this a latent defect rather than a second outage, and it is also why the guard's tests
could all pass while the guard was wrong: the tests are the only caller that drives it.

**One claim filed with the fix is false, and was corrected beside the artifact.**
`pyplan/gates/bug39-ssh-lockout/README.txt` and `ss-format-live.txt` both end with "if that token
is ever wrong the probe finds no port, which lands on the REFUSE branch, not on a silent enable."
It does not: the REFUSE branch is reached only when `listeners_readable` is False or
`SSH_CONNECTION` is set. A wrong owner token with a readable table and no `SSH_CONNECTION` lands on
the enable. `ss-format-live.txt`'s other generalisation — "as a normal user there is NO
`users:((` column at all" — was measured on `dml-arch`, a WSL2 Arch box that happens to have zero
user-owned listeners; `m910q` refutes it. See `pyplan/gates/bug39-ssh-lockout/CORRECTION-2026-09-04.md`.

**What this section still wants**, stated as what was found rather than as a promise: a readability
test that asks whether a **root-owned** socket was visible to this probe (an owner column on a
socket the probe could not otherwise attribute), and an sshd match that survives socket activation.
A repair was in flight in this tree the same night; this entry records the defect, not its outcome.
Every line number above is pinned to `4c959d70` for that reason: `networking.py` was being edited
while this was written, and by morning they will point at something else.

### 40. Abandoning `logs_source()` aborts the interpreter at exit — 2026-09-04, **CLOSED 2026-09-05 at `d2b963d5`**

**What was done.** `runner.stream()` is now a plain function that builds the generator, registers it
weakly in `_LIVE_STREAMS` with a holder for the child `Popen`, and returns it; an `atexit` hook,
`_close_abandoned_streams()`, closes whatever is still open at exit. `atexit` runs BEFORE
finalisation, so the generator's own `finally` can still terminate the child and join the reader —
which is precisely what it could not do when the shutdown garbage collector reached it. The
registration is why `stream()` is no longer a generator function; the body moved to `_stream_lines()`
so no caller can skip it, and the laziness that change threatens is asserted rather than asserted-in-a-comment
by `test_stream_registers_at_the_call_and_starts_no_process_until_the_first_next`.

**The second shape, which is the app's own.** A generator another thread is RUNNING refuses
`close()` with `ValueError: generator already executing` — `native._pump()`'s worker sitting inside
`docker.run_attached()` inside `stream()`. That is logged at debug and the CHILD is ended directly
instead, because a frame that is executing cannot be entered from here; the thread that owns it then
leaves its `readline()` on EOF and runs the `finally` itself. Without that branch the driver exited
**0** — no abort, nothing contended — and left its grandchild alive with **PPID 1**.

**Re-derived on m910q 2026-09-05, on a copy of the tree at `6546b190`, at loadavg ~3 with a live
`mangosd` beside it.** GREEN: sixty consecutive runs of
`pytest tests/test_runner.py -k "worker_thread or abort_the_interpreter"` -> **`nonzero=0 fatal=0
runs=60`**. RED, with `atexit.register(_close_abandoned_streams)` commented out and nothing else
changed: **2 failed** on the first run, both shapes at once —

```
AssertionError: Fatal Python error: _enter_buffered_busy: could not acquire lock for
  <_io.BufferedReader name=5> at interpreter shutdown, possibly due to daemon threads
AssertionError: pid 2193871 outlived the driver
```

The 60-run count is not ceremony: this test's own driver had a 2-in-60 flake of its own (runs 16 and
54, `nonzero=2 fatal=2`, the same `_enter_buffered_busy` abort raised by the TEST rather than by
`runner`, because a bare `for _line in gen` let the terminate's `CalledProcessError` escape into
`threading.excepthook` mid-finalisation). The driver's `work()` catches `BaseException` for that
reason, and a 3%-of-60 flake needs a sample to say it is gone.

**Four tests hold it**, each a different half:
`test_abandoning_a_stream_without_closing_it_does_not_abort_the_interpreter` (the abort),
`test_the_exit_hook_ends_the_child_of_a_stream_another_thread_is_inside` (the refusal),
`test_a_stream_a_worker_thread_is_running_at_exit_leaves_no_child_behind` (the end-to-end orphan,
`/proc/<pid>` on Linux), and `test_the_exit_hook_goes_on_past_a_stream_whose_close_raises` (one bad
generator must not stop the ones after it).

**Two alternatives are rejected in writing rather than left for the next reader to retry**, both
measured on the same box and day: not closing `proc.stderr` while the reader is alive moves the
abort rather than removing it (the `BufferedReader`'s deallocation takes the same lock — still 134,
still `_enter_buffered_busy`), and making the reader non-daemon so `threading._shutdown()` joins it
first turns the abort into a launcher that will not close, because nothing has ended the child at
that point.

---

**The original entry, unchanged below.**

Found by 7.10's sweep. A `logs_source()` generator that is dropped without being closed makes the
interpreter abort at shutdown — SIGABRT, exit 134 — from inside `runner.stream()`'s `finally`.
Pre-existing and not Phase 7's doing: both functions are byte-identical to their 6.x versions.
Evidence in `pyplan/gates/7.10-ubuntu-2026-09-04/`.

It matters because the Server tab's log panel is exactly a caller that starts a stream and may stop
caring about it, and an abort at exit is the kind of thing that looks like "the app crashed on
close" in a bug report and gets attributed to whatever the user did last.

### 41. A realm cannot be set to loopback on purpose — 2026-09-05, OPEN

Found by the owner, reading Appendix C's reword: *"but make it possible to set it to 127.0.0.1"*.
Not a regression — the behaviour is deliberate and argued — but the deliberate half has no way out.

`ready`'s realm step (`catalog/native.py:1969-2005`) rewrites the realmlist row unless
`networking.advertisable()` accepts every column, and `advertisable()` refuses the loopback by
design (§35: a realm advertising `127.0.0.1` tells every client the world server is on the CLIENT's
machine, and the client hangs at "Connecting"). So the value a §35 fix exists to prevent by accident
is also unreachable on purpose: set `127.0.0.1` by hand and the next install press or resume
overwrites it, printing "players on other machines can reach this server".

`networking.Mode` is `Literal["lan", "internet"]`. The missing third mode is the small half. The
load-bearing half is that the choice must be REMEMBERED: `ready` has to distinguish a row that is
loopback because nobody set it from one that is loopback because the owner chose it, and only
overwrite the first. That is recorded intent, not a value read back out of the database.

- [ ] A mode that writes the loopback, reachable from the Networking tab.
- [ ] Intent persisted where a resume can read it, and `ready` reading it before it decides.
- [ ] The gate: choose loopback through the app, press Install again on the finished install, and the
      row is still `127.0.0.1` with a log line saying why it was left alone — while a server whose
      loopback was never chosen still ends up advertising a reachable address.

Deliberately not started on 2026-09-05: `networking.py` was mid-flight in §39 round 5 and
`catalog/native.py` in the §40/§21 lane, and two collisions that night came from editing a file
another lane owned. Decision recorded in `phase7-decisions.md` Appendix D.

### 42. A headless install writes no log at all — 2026-09-05, OPEN

Found while trying to satisfy a 7.1 clause that asked for a count out of `yulon.log`, on a box that
had just completed a full 7.2 install. There is no such file, and the reason is in the code rather
than the box.

`log.configure(config_dir=...)` is what opens the rotating `yulon.log`. Measured 2026-09-05:
`grep -c 'configure(config_dir' main.py` -> 1, `grep -c 'configure(' yulon/install_wiring.py` -> 0.
So the GUI writes a log and the CLI writes none, and `~/.local/share/yulon/` does not exist on
yulon-ubuntu after press 1, press 2, a SIGKILL and press 3 (the whole 7.2 gate).

**Why it matters beyond the gate.** The CLI is what every headless install runs — the gate boxes, a
Steam Deck started from a shortcut, anyone who scripts an install. When one of those fails, the only
record is whatever the terminal still holds, and a user who closes it has nothing to send. The GUI's
own `file_log_problem()` exists precisely because a missing log is worth telling the user about; the
CLI never even tries.

It also quietly falsified a gate criterion for six hours: 7.1's realm clause, reworded at 05:00 that
morning, asked for `ready`'s UPDATE "counted in `yulon.log`" — a measurement no headless run could
ever produce. Corrected the same day to read the database and the transcript instead.

- [ ] `install_wiring` configures file logging the way `main.py` does, or says in its own words why a
      CLI install deliberately does not.
- [ ] A test that fails if one entry point writes a log and the other does not.
- [ ] The gate: run the CLI installer headlessly, then find the log and the stage lines in it.
