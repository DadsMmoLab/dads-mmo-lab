# yulon-win11 — 2026-08-28 — run report

Windows 11 build 26100, PowerShell 5.1, 6 vCPU / 8 GB RAM.
Tested tip `1550a6ad` (the branch moved under the run: 80fb68a9 → 1550a6ad → f5882c2a, pushed by a
parallel session — see [[shared-checkout-branch-drift]]).

## Finding 1 — HIGH — `compose_file()` crashes on an unreachable WSL UNC path
Reproducible test failure on Windows:

    FAILED tests/test_catalog_view.py::test_adopting_a_wsl_server_remembers_the_distro_it_lives_in
    OSError: [WinError 64] The specified network name is no longer available:
      '\wsl.localhost\dml-arch\home\dml\games\wow-server-playerbots\compose.yaml'
    1 failed, 956 passed, 30 skipped in 78.23s

`compose_file()` (`yulon/catalog/installer.py:67-84`) calls `candidate.is_file()` unguarded.
`pathlib.is_file()` only swallows a Windows allowlist — `ERROR_FILE_NOT_FOUND`, `ERROR_PATH_NOT_FOUND`,
`ERROR_NOT_READY`, `ERROR_INVALID_NAME`. **WinError 64 (`ERROR_NETNAME_DELETED`) is not on it**, and
that is exactly what Windows returns for `\wsl.localhost\<distro>\...` when the distro is not running.
So it propagates through `_looks_like()` (`ui/catalog_view.py:156`) into `adopt_from_wsl()` (`:378`).

The intent to handle this is already written down: `_looks_like()`'s docstring (`catalog_view.py:150-155`)
says a folder reached over UNC "can fail for reasons unrelated to which game it is — refusing on
'I could not check' would block the migration this feature exists to provide", and it *does* wrap the
later `compose.read_text()` in `except OSError`. The guard was placed one call too late.

User-visible: clicking "Adopt from WSL" while the distro is not running crashes the action instead of
degrading. Note this test is **machine-dependent** — it passes on a box that has a `dml-arch` distro
(where `is_file()` just returns False) and fails on one that does not. Same class as the four console
tests fixed in #122.

## Finding 2 — HIGH (or a data/intent mismatch) — Tortoise can never be installed on Windows
    platform.detect() -> windows
    wow-tortoise install.platforms -> ('linux',)
    wow-tortoise install.script    -> wow-tortoise/install-tortoise-wow-wsl.sh
    preflight raised: UnsupportedPlatformError -> WoW Tortoise cannot be installed on Windows yet...

`catalog.json:226-227` declares `"platforms": ["linux"]` for an installer script whose name says WSL.
`installer_for()` (`installer.py:697`) reads only that list; there is no "windows via WSL" platform
value, so the refusal fires before WSL is ever touched.
**Either** `"windows"` belongs in `wow-tortoise.install.platforms`, **or** the WSL script is only ever
meant to run with the launcher itself running inside a WSL distro — in which case the naming is
misleading. This needs an owner decision, not a blind edit.
Second, independent blocker on this box: no WSL distro is installed (`wsl -l -v` shows only a stopped
`docker-desktop`), so there is nothing for a `.sh` to run in even if the catalog allowed it.

## Finding 3 — the agent's "nothing can render a window" conclusion is WRONG (corrected by the orchestrator)
The agent reported `MainWindowHandle = 0` for the launcher, for `notepad.exe`, and for Docker Desktop,
plus an all-white `CopyFromScreen`, and concluded the VM session could not render.
A Hyper-V framebuffer capture taken afterwards shows **Docker Desktop fully drawn and focused** on that
desktop, engine running, with a `yulon-wow-wotl…` container listed. So the desktop renders fine.
`Get-Process().MainWindowHandle` read from an SSH shell (session 0) does not reliably report windows
owned by session 1, and a `CopyFromScreen` must itself be launched into session 1 to capture anything.
**Consequence: the Windows GUI feature sweep is untested, not impossible.** It needs re-running.

## Finding 4 — provisioning gap on the test box
`yulon-win11` had **neither git nor a real Python** — `python.exe` on PATH was the Store stub, `git`
absent. Both installed via winget (`Git.Git`, `Python.Python.3.11`) before anything could run. If this
is a standing test box for the project, it was not provisioned for it.

Also: `~/bin/on-desktop.ps1` (orchestrator-written tooling, not product code) silently no-ops when
`-Exe` contains a space — schtasks returns `-2147024894`. Use the 8.3 short path or fix the quoting.

## Finding 5 — the documented Windows 9p fix could not be re-verified
`container_user()` (`yulon/catalog/composegen.py:263-289`) sets `user: "0:0"` on Windows with the
docstring claim "Measured on a clean Windows 11 box (2026-08-25): the identical import exits 0 with
this key and 1 without it." Re-verification did not reach a container run: Docker Desktop degraded
under memory pressure (`docker info` took **125 s**; `docker images` returned
`500 Internal Server Error ... /_ping`; `Available MBytes` ≈ 1 GB of 8 GB). Host disk was never the
issue — `W:` stayed 30.4 → 29.6 GB. The `ac-db-import` claim remains untested on this run.

## Clean results
- `ruff check .` — passed. `black --check .` — 76 files unchanged.
- `mypy yulon main.py` (CI's actual command, `ci.yml:50-57`) — clean on native, win32, darwin.
- `mypy .` — 375 errors, all in `tests/`, from `strict`+`no_implicit_reexport`. Not gated by CI, not
  caused by this branch. Third box to report the identical number.
- `pytest -q` — 956 passed, 30 skipped, 1 failed (finding 1). All 30 skips verified legitimate.
  Nice detail: `bash_available()` (`installer.py:382-407`) actually *runs* `bash -c "exit 0"` rather
  than trusting PATH, precisely because `C:\WINDOWS\system32\bash.exe` is the WSL shim that fails with
  no distro installed — exactly this box's state. It detected and skipped correctly.

---

# yulon-win11 — round 2 (tested f5882c2a)

## Finding 3 from round 1 is RETRACTED — the desktop renders fine
Launched through `on-desktop.ps1` into session 1 and captured the same way: clean screenshot,
title `Yu'lon — Dad's MMO Lab launcher 0.6.57`, Catalog tab.

What the screenshot shows, and it is a useful platform-gating check:
- **WoW WotLK (stable)** — Install, "Use existing…" and **"Find in WSL…"** all enabled (Windows is a
  supported platform for this entry).
- **WoW Vanilla (beta)** — Install **greyed out**, italic note *"Installer needs Linux — not
  available on this platform yet."*

Two measurement traps worth keeping: `Get-Process().MainWindowTitle`/`MainWindowHandle` only resolve
for windows in the *querying process's own session*, so they must be read from a script itself
launched into session 1; and each `on-desktop.ps1` call leaves a console window stacked on top, so a
screenshot taken right after a launch captures the automation's own terminal. Fix used: one combined
session-1 script that minimises WindowsTerminal windows (including its own host), raises the target
by title fragment, then captures.

## Finding 1 CONFIRMED at the function level, and sharpened
Direct repro against the deployed `installer.py:82`:

    compose_file(Path(r"\wsl.localhost\definitely-not-a-real-distro\home\test\wow-server"))
    → OSError: [WinError 64] ... winerror=64, errno=22, uncaught out of pathlib stat() → is_file()

**New nuance:** calling `_looks_like()` with the *same* path immediately afterwards did **not** raise —
it returned `True`. WSL's 9P network-name provider is stateful: the first access to a nonexistent
distro gets `WinError 64` (network name torn down mid-resolution); a later identical access resolves
to a not-found that maps to `ENOENT`, which `pathlib._ignore_error()` *does* swallow — it does not
swallow `winerror=64`. So the crash is real but **timing/state-dependent, not reproducible on demand**.
That argues for the broad fix — `try/except OSError: return False` around the probe — rather than
special-casing errno 64.

**GUI-level repro is impossible on this box**, for a concrete reason worth recording:
`wsl -l -v` lists exactly one distro, `docker-desktop` (Docker Desktop's own backend). Yu'lon's
`platform.wsl_distros()` does **not** filter that name out, so "Find in WSL…" is offered — but
`wsl -d docker-desktop -- docker compose ls` fails with *"It looks like you have tried to invoke the
docker CLI from the docker-desktop WSL2 distribution. This is not supported."*, so `_compose_ls()`
returns `""`, `find_servers()` returns `()`, and the click always ends in the safe
"No servers found in WSL" box without ever reaching `compose_file()`.
**Small separate finding: `wsl_distros()` should probably filter `docker-desktop` (and
`docker-desktop-data`) — they are never user servers.**

## Still open on Win11
Install-dialog edge cases (spaces, non-existent nested path, other drive, UNC), Console tab, accounts,
backups, modules, networking, maintenance/repair, log panel, self-update, and the `container_user()`
`ac-db-import` 9p claim. None reached before the resize stop.
Session 1 has leftover debris (stray python / conhost / WindowsTerminal windows) — clean before reuse.
