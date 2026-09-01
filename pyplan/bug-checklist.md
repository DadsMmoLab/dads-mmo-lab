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

### 19. The database password reaches the install log and a user-facing error — 2026-09-01, OPEN

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

*Fix in flight* on `feat/7.3-j5-sqlplan-verify`: redact where client output enters the module rather
than at each call site, because **K.7 will add a fourth site** and sprinkling is how the next one gets
missed. Update this entry to FIXED with the mutation evidence when it lands.

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
calls `_refuse_unsafe(password, ...)` at `composegen.py:318`, and
`_UNSAFE_SCALAR_CHARS = frozenset("$\"\;#{}

	'")` **includes the single quote, the backslash
and both line breaks** — so such a password is refused when the compose files are generated, which is
stage 4 of 12, six stages before `import`.
**But it is guarded by STAGE ORDERING, not by the function that appears to own the rule.** Anything
that reorders the stages, or removes `_refuse_unsafe` from `render()` as redundant, reopens it
silently. Named here so that edit gets challenged.

**Also known, harmless only by ordering:** `create_schemas()` returns on `if not plan.create` *before*
validating its schemas, so a plan with an empty `create` and a bogus `marker_db` is refused by
`expand()` and silently accepted here. The two call sites are not equivalent — and **Tortoise is
exactly the empty-`create` case**, so the asymmetry sits on a live path.

### 20. The secret-in-a-generated-file rule was decided for compose and never carried to the Dockerfile — 2026-09-01, OPEN

Found while implementing K.2, by asking a question the plan did not raise. **Nothing exploits it
today; the exposure is one template edit away.**

`CmangosInstaller._tokens(ctx)` is ONE mapping used for the Dockerfile, the conf tables, the SQL
statements and verify — and it contains `DB_PASSWORD`. **K.4 hands that whole mapping to
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
  SURVIVES ALL 1960 TESTS.** The by-name refusal covers **one name**, not the property. This is
  precisely Codex's "ambient authority" point, arrived at from the opposite direction.
- **D4 — `dockerfile.write()` accepts hand-built marked text containing a secret and writes it.** It
  validates only the generated marker, never content — exactly the public bypass Codex named.

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

### 21. `_stream()`/`_pump()` leak a running worker when the generator is abandoned — 2026-09-01, OPEN, inherited

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
a weak reference.

**The test that closes it is cheap and should be written whatever the fix is:** abandon the generator
**without** setting the cancel event, and assert no live worker remains. That converts the mitigation
from "`LogPanel.stop()` happens to set cancel first" into something a **reorder fails** — the sixth
standing rule's second exit.

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

### 23. A future-version state file is silently downgraded on disk — 2026-09-01, OPEN, pre-existing

Both families share one state filename (`STATE_FILE = ".yulon-install.json"`, no family suffix). A
**cross-family** file is refused by `_guard` on `game_id` (`native.py:894`) and `family` (`:899`) — but
**after** a silent name-drop.

The quiet case is a **same-family, future-version** state file: stage names the running binary does not
know are dropped at `native.py:335` **with no log at any level**, `write_state` then persists the
**post-filter** tuple (`:709`, `:377`), so **an older binary permanently strips the newer names from
disk**, and the only user-facing line (`:667-669`) prints the already-filtered tuple. A downgrade is
therefore lossy and silent in both directions.

Unreachable for cmangos today (it is not in `FAMILIES` until K.8). Recorded now because **it is
precisely the mechanism that would hide a `stages()`/`STAGE_NAMES` mismatch** — the thing K.2's
inertness argument depends on being visible. **Note it in K.8's brief.**

### 24. The false 0600 guarantee K.3 removed is still live in `conf.py` — 2026-09-01, OPEN

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

Deferring the Windows **fix** is right — a real DACL means pywin32 or an `icacls` subprocess on every
path that touches the file, and that is an app-wide posture decision. **Correcting the false sentence
is not deferrable.**

### 25. The bash lineage still deletes a volume silently — 2026-09-01, unreachable today

`install-wow-vanilla.sh:2043` runs `docker volume rm "${db_volume}"` with no confirmation — **the exact
destructive answer the Python design rejects**, and the reason `db-password` refuses rather than
wiping.

Unreachable while `cmangos` is registered: `installer.py:896` falls back to a script only for an
**unregistered** family. So the guard is **registration**, not a check — the sixth standing rule's
shape (a guard held by its position rather than by the thing it protects). 7.2 deletes this lineage;
until then, note that de-registering the family for any reason re-arms it.

Enumerated at argv level across `yulon/` and `main.py`: the **only** volume argv in the Python app is
`docker.py:679` `["volume","inspect",...]`, which is read-only. No `volume rm`, no `volume prune`, no
`down -v`, no `--volumes`. `remove_staged` is `["compose","down","-t",…,"--remove-orphans"]`.

### 26. The three CMaNGOS entries name compose services that the rendered file does not have — 2026-09-01, OPEN, goes LIVE at K.8

Found by **F.2's reviewer**, while reviewing a 7.2 deletion — a 7.3 defect surfaced from an unrelated
task. Verified independently here:

```
wow-tbc / wow-vanilla / wow-tortoise   catalog compose_services() -> ('db', 'realmd', 'mangosd')
shared/cmangos/base.yml.tmpl service keys -> {{CONTAINER_PREFIX}}db, ...realmd, ...mangosd
                                          -> rendered for wow-tbc: tbc-db, tbc-realmd, tbc-mangosd
wow-wotlk                              compose_services() -> None  (uses the default; consistent)
```

`docker.start_staged()` runs `compose up -d --no-deps db realmd mangosd`. Against the file this repo
actually generates that answers **`no such service`** — every one of the three is missing.

**Not live today** only because `FAMILIES` registers `azerothcore` alone; `CmangosInstaller` exists but
is unregistered. **K.8 is the task that registers it, so K.8 is when this becomes a broken install.**
Fix before K.8, not after.

**Two test-level reasons nobody caught it**, both worth more than the bug:
1. The deleted `test_no_catalog_compose_service_is_really_a_container_name` was the **only** test that
   cross-checked declared services against a compose file — and it read the **bash script**, so
   restoring it verbatim would catch nothing. Its replacement,
   `test_cmangos_games_select_compose_services_not_container_names`, **restates literals instead of
   cross-checking**, and its two halves already contradict each other while both pass.
2. `test_composegen.py::test_the_cmangos_services_are_named_after_their_containers` carries the
   docstring *"`ContainerSpec.services` keeps its default"* — **false**; the catalog sets
   `containers.services` explicitly. A true-sounding premise attached to a passing test.

**The test that should exist** (five lines, and it fails today, which is the point): for every entry,
assert every name in `container_spec().compose_services()` appears as a service key in the **rendered**
`base` from `composegen.render()`. Cross-check, not restatement.

**Also minor, same branch:** `test_no_bash_installer_ships` has **no `scanned >= N` vacuity guard** —
mutation-proven: rename the installers root and it passes while its two siblings in the same file
fail. Inconsistent with the guard F.2 deliberately added two files over.

### 27. The import-bound SELinux seam is in `docker.py`, not in `run_plan` — 2026-09-01, OPEN, latent

`docker.bind_mount_ok(… selinux_enforcing: Callable[[], bool | None] = platform.selinux_enforcing)`
(`docker.py:2728`) **is** bound at import. Asked of the interpreter, not read off the page:
`signature(docker.bind_mount_ok).parameters["selinux_enforcing"].default is platform.selinux_enforcing`
→ **True**.

This is the real instance of the trap `container_user_args()` documents against itself — and the one
**K.5 wrongly attributed to `extract.run_plan()`**, whose default is `None` with a late module lookup
(`extract.py:755`, correct). K.5's docstring took a true fact about one function and asserted it about
another that does the reverse; corrected on `fix/mmaps-audit-and-the-claim-that-was-backwards`, where
the replacement now carries the interpreter output instead of a claim.

**Latent, not live — and the reason is luck, not wiring.** Its production caller,
`preflight._default_bind_probe` (`preflight.py:257`), passes **no** seam, so on a real machine the
default runs and asks the real host — which is the right answer by accident. Under test it never runs
at all: `test_preflight.py` fakes one level up (`bind_mount_ok=lambda _p: …`). `test_docker.py` passes
`selinux_enforcing=` explicitly at 3147 and 3207 and **omits it at 3188/3191/3303/3325/3377/3393/3418**,
so those calls take the runner's own host answer — **green today only because every runner is
non-enforcing. A Fedora runner is what would change that.**

**Fix:** have `preflight` thread its own seam for this question (it has none), rather than changing the
default in place. **The guard to add first** is a test that patches `platform.selinux_enforcing` and
asserts the value **arrives** — not that the parameter exists. That distinction is this run's third
standing rule and it has caught three separate defects.

### One thing worth keeping

Three of these have an obvious fix that is **wrong**, and two of them arm a worse bug:

1. Setting `reinstall=True` (the natural fix for the silent-success bug) turns the unreliable image
   check from harmless into **deleting a good 2–4 hour build**. Fix the image check first.
2. Deleting `container_name:` makes every container `<project>-ac-worldserver-1`, and the controller
   finds nothing.
3. Changing the compose-file guard before installs are resumable breaks a case that currently works.

The pattern: *check whether the thing you are about to make true is guarded by something that is
only safe while it is false.*
