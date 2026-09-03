# yulon-fedora round 3 — the controller feature sweep, against a live 500-bot server

8 vCPU / 23 GB. Branch `test/full-vm-run-2026-08-28` @ c5c7d20a. Install dropped this round (Arch
owns it); this box did features only, which is what finally closed the biggest gap in the run.

## The 500 was verified empirically, and the check caught a real trap
The running `ac-worldserver` still had `1600/2000` baked in even though `docker-compose.override.yml`
on disk said 500 — logs read **`1852/1852 Bot ... logged in`**. After
`docker compose up -d --force-recreate ac-worldserver`, the container env read `MIN=500 MAX=500` and
the logs climbed to **`500/500 Bot ... logged in`**. Confirmed a second way through the real
worldserver console (`server info` via `wotlk_console.send_command`): **`Characters in world: 500`**,
both before and after a full backup/restore cycle.

**The lesson, which is the reusable part: editing the compose file changes nothing about a running
container.** A recreate is required, and the only honest check is
`docker inspect <container> --format '{{range .Config.Env}}...'` plus the bot count in the logs.

## Feature results

| Feature | How driven | Result |
|---|---|---|
| Console tab | direct (`send_console`) | real `server info` reply, 0 connected / 500 in world |
| Accounts: create, GM level, authenticate | the project's own `tests/integration/test_accounts_live.py` | **4/4 passed**, including the byte-identical SRP6 verifier check against the live worldserver — the project's own checklist-6.5 authentication gate |
| Backups: create, validate, restore | direct | **worked end to end**: 4 DBs ~400 MB, `verify_dump()` passed all, `plan_restore()` correctly refused while running, stopped only auth+world, restore succeeded with an automatic pre-restore safety backup, restarted, all 500 bots relogged |
| Modules / manifest applier | direct | `ManifestStore` loaded all four kinds live (21 module / 7 ale / 11 mod / 2 keg); real `Applier` built through the GUI wiring. A live `.install()` was **not** run — some manifests do schema/rebuild work and that was judged too risky against the shared DB |
| Networking LAN + internet, firewall, realmlist | direct | both plans `ready=True`; `apply()` ran **real** `firewall-cmd` commands (all succeeded), wrote the real `realmlist` SQL row, and `write_client_realmlist()` produced a correct `realmlist.wtf` |
| Maintenance / `repair_import()` | direct | correctly refused against the populated DB with the exact "press Stop first" message |
| Log panel streaming | direct | `logs_source()` yielded live bot-login lines in real time |
| Self-update | direct | **404, permanently — see below** |
| Port conflicts | direct | `Controller.port_conflicts()` correctly reported the foreign `vanilla-*` stack on 3724/8085 |

Regression check on the branch: `967 passed, 3 skipped`.

## GUI limitation, stated honestly
The catalog window drove fine over AT-SPI, but **"Use existing…" opens a native
`xdg-desktop-portal` dialog that exposes zero AT-SPI content** over an SSH-driven session
(`GTK_USE_PORTAL`/`QT_QPA_PLATFORMTHEME` changes made no difference; `xdotool` is unusable with no
`DISPLAY` under Wayland). Every controller tab sits behind that one dialog. So the tabs above were
driven through `ControllerServices.for_wotlk()` — **the same wiring `controller_view.py`'s buttons
call**, not a reimplementation — and that is stated per item rather than glossed.

## Self-update: third independent confirmation, same root cause
`GET /repos/DadsMmoLab/dads-mmo-lab/releases/latest` → 404 every time. The repo *has* releases
(latest `v0.6.57Public`), but they are flagged `prerelease: true`, and GitHub's `/releases/latest`
excludes prereleases by design. Not a code bug in `yulon/update.py`; a release-process fact.
**Un-flagging one release fixes self-update on every platform at once.**

## Adjudication — one agent claim that does not hold up
The Fedora agent reported that accounts *list* and *set-password* are absent **"by design, per
`accounts.py`'s own docstring — existing passwords are deliberately never rewritten."**
**Checked, and that statement is not in the file.** `accounts.py` (634 lines) contains no `list_*`
and no `set_password`, and its only two uses of "deliberately" are about `SEC_CONSOLE` being excluded
from the GM levels, and about a dataclass holding no password so no caller can log one. The module
docstring explains why *creation* writes the SRP6 row directly (console needs a TTY, so it fails on
Windows; SOAP cannot create the first account) — it says nothing about refusing to list or to change
a password.

So **the m910q finding stands**: the Accounts tab is create-only, there is no way to list existing
accounts from the app, and no documented decision says it should be that way.
