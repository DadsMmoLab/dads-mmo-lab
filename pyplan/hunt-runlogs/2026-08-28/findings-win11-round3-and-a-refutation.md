# yulon-win11 round 3 — the native engine's first real install, and one finding refuted

8 vCPU / 23 GB, commit 66d4824e. **The native install engine ran end to end on Windows for the
first time** — the code path no Linux box ever executes.

## Independent confirmation of a fix landed the same day
The status-poll hang was reproduced live and in full: `docker ps` hung **8+ minutes** after Docker
Desktop degraded, the "status:" label stayed frozen on `unknown`, **three manual Refresh clicks did
nothing**, and it recovered only when the Docker process was killed. That is precisely the wedge
`STATUS_TIMEOUT_SECONDS` was added for, observed by someone who did not know the fix existed.

The app log shows the failure mode after the daemon died, once per poll, forever:
`background job failed: DockerCommandError: docker ps ... exited 1: failed to connect to the docker
API at npipe:...` — that is the recoverable shape. The unrecoverable one is the same call with no
answer at all.

## The install itself
Git clone → module clone → `docker compose build` (**~72 min**, 19:01→20:13) → db-import exited 0 →
`ac-database`/`ac-authserver`/`ac-worldserver` up. Installed into
`C:\Users\pk\wow test install\wow-server-playerbots` — **a path with spaces**, deliberately.
`container_user()`'s Windows `user: "0:0"` fix confirmed working: db-import wrote to the bound
`env/dist/etc` and exited 0, which settles the 9p question that had been unverified since 2026-08-25.
**Bot count correct on the fresh container**: `AC_AI_PLAYERBOT_MIN/MAX_RANDOM_BOTS=500`, log showing
`255/500 Bot ... logged in`.

## Finding 4 — "a successful install is not remembered" — REFUTED
Reported as HIGH: `state.json` unchanged, no second tab, "closing and reopening the app would have
made this install invisible". It would have gone into PR 1 as a serious bug. It is not one.

Evidence:
- `_pin_compose_project()` has exactly **one** caller — `catalog_view.py:525`, inside
  `_on_run_finished`'s `if ok:` branch, immediately before `self.installed.emit(...)`. The
  "Use existing…" adopt path does not call it.
- The app log records `pinned COMPOSE_PROJECT_NAME=yulon-wow-wotlk-5a15f8fd` at **20:20:50**.
- `state.json` mtime is **20:20:50**, and it contains the new install.

So `_on_run_finished` ran with `ok=True`, pinned, emitted, and the state was written — automatically.
**The screenshot showing "only one tab" was taken at 20:18, two minutes before the install finished.**
The containers came up at 20:13:33, but the native installer then waits for readiness, and the run did
not end until 20:20:50. "Not finished yet" was read as "not remembered", and the manual
"Use existing…" that appeared to fix it was redundant.

**The real finding underneath it, and it is small:** the native path logs nothing at completion. The
bash path logs `install of wow-wotlk finished`; nothing equivalent appears for `NativeInstaller`, so
the only way to know the run ended is the pin line — which is why a careful tester misread a 7-minute
readiness wait as a failure. Worth a completion log line.

Two hypotheses were ruled out from source before the log settled it, so neither is worth re-chasing:
the compose-file guard cannot be the cause (`composegen.BASE_FILE` is `docker-compose.yml`, which is
in `COMPOSE_FILENAMES`), and a stale cancel flag cannot be either (`LogPanel.run()` sets
`_stop_requested = False` before starting, `log_panel.py:177`).

## Finding 5 — tab titles collide — STANDS
`state.json` now holds two installs whose leaf names are both `wow-server-playerbots`, and the tab
title is `server_dir.name` (`main.py:220`). Since the installer always suggests the same default leaf
name, two installs in different parents are indistinguishable in the tab strip.
Not data loss — Stop is ownership-protected by compose project labels (`docker.py stop_staged()`) and
refuses across installs — but the older tab's up/down line does read the newer install's containers,
because `docker.status()` is name-only by design.

## Other confirmations
- `compose_file()` `WinError 64`: 5-call repro — call 1 raises, calls 2-5 on the identical path return
  cleanly. Intermittent on demand.
- `wsl_distros()` returns `('docker-desktop',)` with no real distro; the "Find in WSL…" button's
  visibility is gated on that list, so it shows on every tile including Linux-only games.
- Global container names: `preflight.gather()` on a brand-new folder returned
  `[refuse] the server's ports: ac-worldserver, ac-authserver already publish the ports this server
  needs` while the old install ran.
- Tortoise Install correctly disabled: "Installer needs Linux — not available on this platform yet."
- Home-folder refusal, console TTY refusal, backup (4 files ~400 MB), account creation (`HUNTTEST`,
  id 251, verified in `acore_auth.account`), teardown (~80 s), modules and networking tabs: all correct.

## Environment lesson worth keeping
**Docker Desktop's WSL2 backend does not follow a host-side VM resize.** `.wslconfig` still pinned
`processors=4, memory=14GB` from an earlier round, so Docker never saw the 8 vCPU / 23 GB. And
`preflight._cpu_check` reads **Docker's VM allocation**, not the host spec — it reported
"7 CPUs against 9 affordable" after `.wslconfig` was raised to 7/20.
