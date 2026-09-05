# Fix plan — one distro at a time, one PR each

Rule (owner, 2026-08-28): fix one distro to 100% before starting the next. Hunt is finished first.
Order: **Ubuntu → Fedora → Arch → Windows.** Target `DadsMmoLab/dads-mmo-lab`; Baerthe merges.
No Claude footer, no session link, anywhere.

## Definition of "100% operational" (the bar, per distro)
1. Clean clone; ruff, black, `mypy yulon main.py` (+win32/darwin) green
2. Fast suite green **and 20 consecutive GUI-subset runs with no segfault**
3. Launcher window opens on a clean box, prerequisites documented
4. Fresh install completes into a user-chosen folder
5. Server boots; bot count matches config; account creates **and authenticates**
6. Feature sweep green: console, accounts, backups+restore, modules, networking, maintenance, logs
7. Install safely re-runnable — no silent success, no build deletion
8. Fresh VM checkpoint taken as the new baseline

**Excluded from the bar:** self-update. It is dead because every GitHub release is flagged
`prerelease: true`, which no PR can fix — raise as an issue for Baerthe instead of blocking all four.

---

## PR 1 — Ubuntu to green (the big one; carries the shared fixes)
Scoped to what actually blocks Ubuntu's bar, not to every defect found.

| # | Defect | File:line | Note |
|---|---|---|---|
| 1 | **Fast-path image check is unreliable** — `docker compose images` reports a project's *containers*, not the image store | `install-wow-wotlk.sh:1249-1250` | **MUST land before #2** |
| 2 | **Interrupted install recorded as success** — `PROMPT_RULES` answers "n"; script exits 0; view records a server never built | `installer.py:180`, `install-wow-wotlk.sh:1273-1281`, `catalog_view.py:497-510` | Only safe once #1 is fixed |
| 3 | **Container names are global** | `native/base.yml.tmpl`; `catalog.json` `containers` | **User-facing half fixed**: `refuse_foreign_containers()` names the owning install before the build instead of a raw daemon error after it. **Coexistence NOT fixed** — 40 literal-name call sites across 7 controller files; project-scoped names are a design change whose failure mode is stopping the wrong server. Owner decision |
| 4 | **Status poll wedges forever** — no timeout on `docker ps`; `_status_pending` never cleared | `docker.py:217-229,1615-1626`; `controller_view.py:445-456` | Needs BOTH a timeout and clear-on-any-outcome |
| 5 | **GUI test segfault** — TWO bugs. (a) `LogPanel` `deleteLater` ran a Python QObject's destructor on the worker thread (the gdb deadlock) — fixed, deterministic test. (b) The measured crash: a worker GC'd with its panel/view *before the OS scheduled its thread*; `QThread::started → worker.run` on freed memory (native backtrace, SIGBUS in `QMetaMethod::name`). Fixed by `job.InFlight` owning every started pair until finished + unparented threads | `ui/widgets/job.py`, `ui/widgets/log_panel.py`, `main.py` | **GATE PASSED 2026-08-28: 25/25 clean under gdb** (pre-fix died on run 1, half-fixed on run 2). Three sites: LogPanel, ThreadedJobRunner, main.py update thread (`setProperty` keeps no Python ref). **Plain 20x: 20/20 clean** (baseline 1/20, half-fixed 5/20). GATE MET |
| 6 | Installer CLI `_main()` passes no `ask=` and bypasses `installer_for()` | `installer.py:741-780` | Hangs forever on a password sudo |
| 7 | Docker images ignore the chosen folder; no floor on `/var/lib/docker` | — | **Documented, not fixed.** The branch already warns on both disks. The VM death was HOST-side dynamic-VHDX exhaustion, invisible from the guest; no guest check can see it, and turning the warning into a refusal would block installs that would have succeeded. Owner call if a floor is wanted |
| 8 | Integration teardown burns 300 s ×2 on fixtures that ignore SIGTERM | `tests/integration/conftest.py:87,104,113`; `docker.py:791` | DX; makes the suite 30-45 min |
| 9 | `mypy .` ≠ CI's `mypy yulon main.py` (375 tests-only errors) | `pyproject.toml` | Config/doc |
| 10 | `python -m yulon` fails — no `__main__.py` | — | Trivial |

**Ordering constraint that must not be violated:** #1 before #2. Setting `reinstall=True` while the
image check is unreliable turns a harmless no-op into "deletes a good 2-4 hour build".

## PR 2 — Fedora to green (base: PR 1)
- SELinux `:z`/`:Z` missing from the Python bind probe (`docker.py:2428`) and the test fixture
  (`tests/integration/conftest.py:210`) — 5 integration tests fail on any enforcing box.
- **Preflight is unreachable on Linux** (`installer_for()` → `is_native()` → `script_platforms`), so
  the SELinux probe and `_cpu_check` are both dead there and wake up together when Phase 7.2 lands.
  Decide whether that is in scope for a fix now or a Phase 7 item.
- `_cpu_check`'s remedy text names Docker Desktop, which does not exist on native Linux.

## PR 3 — Arch to green (base: PR 2)
- Document the system prerequisites: `xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image
  xcb-util-renderutil libxkbcommon-x11` — without them the launcher will not start from source, and
  Qt's own error names only one of them.
- Note the Debian-family equivalent (`libxcb-cursor0` and friends) found on m910q.
- `detect_firewall()` → `"none"` on Arch is correct behaviour; document that LAN setup is only
  partly automatic there.

## PR 4 — Windows to green (base: PR 3)
- `compose_file()` unguarded `is_file()` → `WinError 64` on an unreachable WSL UNC path. Intermittent
  (first call raises, later calls return), so the fix must be a broad `except OSError`, not errno 64.
- `wsl_distros()` does not filter `docker-desktop`, so "Find in WSL…" shows on every tile and always
  fails. **`tests/test_platform.py:526` pins the buggy value** — update it with a comment.
- Native-engine install / `ac-db-import` on 9p — pending the current build's result.
- `.wslconfig` stale caps: Docker Desktop's WSL2 backend does not follow a host VM resize.

## Not PRs — raise as issues
- **Self-update**: every release flagged `prerelease: true`; `/releases/latest` excludes prereleases.
  One un-flagged release fixes it on all platforms. Baerthe's call.
- **Tortoise on Windows**: `platforms: ["linux"]` on a script named `-wsl.sh`. Either add `"windows"`
  or the naming misleads — owner decision, not a blind edit.
- **Accounts tab is create-only** (no list, no set-password). A feature gap, not a defect; no
  documented decision says it should be that way.

## Three fixes whose obvious form is wrong
Recorded so the fixing pass does not walk into them: `reinstall=True` (arms the build deletion),
deleting `container_name:` (controller stops finding containers), and changing the compose-file guard
before installs are resumable. See `obvious-fixes-that-are-wrong` in memory.

## Installer coverage gap found 2026-08-28 (owner asked "did the hunt test all the installers?")
Only **WotLK** was installed: apt (Ubuntu, full), pacman (Arch, full), native engine (Windows, full).
The dnf variant never completed a fresh compile on Fedora (died at 83% at 8 GB; dropped for the
sweep at 23 GB) — Fedora's own pass must do it. **TBC, Vanilla and Tortoise installers were never
run**: all three have `requires_client_dir: true` and no VM holds a client. Client search
2026-08-28: only `m910q:/home/pk/TurtleWoW` (Tortoise 7272) exists; no 2.4.3 or 1.12.1 client on
any box; yulon-win11 has none. Tortoise is added to the Ubuntu operational run (m910q). TBC and
Vanilla wait on the owner for client locations.
