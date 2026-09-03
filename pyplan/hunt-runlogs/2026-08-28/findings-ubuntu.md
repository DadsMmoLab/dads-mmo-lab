# yulon-ubuntu — 2026-08-28 — run report (partial: VM froze mid-install)

Branch under test: `fix/installers-honour-chosen-folder` @ 80fb68a9. Ubuntu 24.04.4, Python 3.12.3.

## Blocker that ended the run
`yulon-ubuntu` went to `PausedCritical` ("Disk(s) encountered critical IO errors"). Host volume
`U:` reached 0 bytes free. `U:` (119 GB) held the VM's base disk (13.5 GB) plus **two unmerged
checkpoint differencing disks (54.2 GB + 48.9 GB)**. The guest never saw pressure — it reported
41 GB free on `/` throughout, because the VHDX is dynamic and grows host-side per guest write.
Docker image builds land on `/var/lib/docker` on the main disk regardless of where the server
folder was chosen, which is what consumed the last of it.

Recovery taken (owner-approved): power off, `Move-VMStorage` to `D:` (298 GB free), keeping all
three checkpoints.

## Confirmed working — both fixes on this branch
1. **Chosen folder is honoured** (`95da92ae`): answered `Install path: /mnt/tbcwork/yulon-run/wotlk-new`;
   installer printed `Server files will be installed to: /mnt/tbcwork/yulon-run/wotlk-new` and cloned
   there, not into the default `~/wow-server-playerbots`.
2. **Free space checked on the right disks, as a warning** (`258b7a1e`, `80fb68a9`): printed
   `✅ Disk space OK on /home/pk (41GB available)` **and**
   `✅ Disk space OK on /var/lib/docker (41GB available, Docker's images)` — informational, not a gate.
3. apt variant resolution: `platform.linux_package_manager()` → `apt`;
   `CatalogEntry.install.script_for("apt")` → `wow-wotlk/install-wow-wotlk-ubuntu.sh`. Matches catalog.
4. `check_for_update("0.6.57")` degrades cleanly: upstream has zero GitHub Releases, so the API 404s
   and the check returns `available=False, error='HTTP Error 404: Not Found'` without raising.
   Correct behaviour — but self-update is a silent no-op for every user until upstream cuts a Release.

## Findings
1. **[real, known-but-now-demonstrated] Docker images ignore the chosen server folder.**
   The branch fixed where *server files* go; images still go to `/var/lib/docker` on the main disk and
   nothing gates on that volume's headroom. This run is a live demonstration of that gap causing an
   actual VM failure, not a theoretical risk. `80fb68a9`'s own message says the floor "is not in this branch".
2. **[DX] Integration-test teardown wastes ~10 minutes per local run.**
   `tests/integration/conftest.py:85-113` stages busybox containers running bare `sh -c "sleep 600"`.
   As PID 1 they ignore SIGTERM, so `docker compose stop -t 300` (`STOP_GRACE_SECONDS = 300`,
   `yulon/docker.py:791`) burns the full 5-minute grace before SIGKILL — twice
   (`test_docker_live.py:80` and `:129`). The 300 s production grace is right; the fixture containers
   should trap SIGTERM and exit. Confirmed by `py-spy dump`:
   `_communicate (subprocess.py:2115) → runner.py:271 → docker.py:189 → stop_staged (docker.py:1576)`.
3. **[process] `mypy .` ≠ what CI runs.** CI runs `mypy yulon main.py` (×3 platforms) — clean, 37 files.
   A bare `mypy .` picks up `tests/` and throws 375 errors, nearly all `[attr-defined]` on mock fakes
   under `strict = true`. `pyproject.toml`'s `[tool.mypy]` sets no `files =`, so the two diverge.
   Not a code bug; scope the config or the next tester chases 375 false alarms.
4. **[minor] `python3 -m yulon` does not work** — `No module named yulon.__main__`. The entry point is
   `main.py`. Worth an `__main__.py` if `-m` is expected to work.

## Not tested, and why
- GUI click paths: pure Wayland GNOME, no `DISPLAY`, `xdotool`/`wmctrl` fail with "Cannot open display".
  Equivalent code paths were driven directly from Python instead; noted per item above.
- Controller tabs (console/accounts/backups/modules/networking/maintenance/repair/logs) against a live
  server — not reached before the freeze.
- `pytest -q` completion and WotLK install completion — both in flight at freeze. Guest logs at
  `/mnt/tbcwork/yulon-run/logs/{pytest,install-ubuntu}.log`, readable once the VM is back.

Launcher window WAS confirmed on screen (screenshot): "Yu'lon — Dad's MMO Lab launcher 0.6.57",
Catalog tab, WotLK (stable) + Vanilla (beta) tiles.
