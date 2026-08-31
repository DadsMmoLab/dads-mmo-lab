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


### One thing worth keeping

Three of these have an obvious fix that is **wrong**, and two of them arm a worse bug:

1. Setting `reinstall=True` (the natural fix for the silent-success bug) turns the unreliable image
   check from harmless into **deleting a good 2–4 hour build**. Fix the image check first.
2. Deleting `container_name:` makes every container `<project>-ac-worldserver-1`, and the controller
   finds nothing.
3. Changing the compose-file guard before installs are resumable breaks a case that currently works.

The pattern: *check whether the thing you are about to make true is guarded by something that is
only safe while it is false.*
