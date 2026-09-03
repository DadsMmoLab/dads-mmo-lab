# m910q — 2026-08-28 — controller feature sweep against a LIVE Tortoise server

Physical Ubuntu box, 4 cores / 15 GB. Live `tortoise-realmd`/`tortoise-mangosd`/`tortoise-db`
(CMaNGOS family, `~/tortoise-wow-server`), adopted through the launcher's "Use existing…".
Tested f5882c2a. This is the run that finally covered the controller features.

Needed before the GUI would start: `libxcb-cursor0 scrot xdotool wmctrl` — the Debian-family echo of
the Arch xcb finding. Everything else pip-installed clean.

## Finding 1 — HIGH — backup AND restore were broken on every MariaDB server. FIXED (66d4824e)
"Back up now" → *"20260828_150338_tw_char.sql.partial does not start like a mysqldump, so it is not a
database backup — the backup is INCOMPLETE."* The dump was complete and ended with
`-- Dump completed on 2026-08-28 13:05:20`.

MariaDB 10.6.28's `mariadb-dump` prepends `/*M!999999\- enable the sandbox mode */` before the banner,
so `-- MariaDB dump` is no longer at byte 0. `_DUMP_HEADER` (`controller_wow_wotlk/maintenance.py:147`)
was `^--\s+(MySQL|MariaDB)\s+dump\s` used with `.match()` — anchored twice over.
`plan_restore()`, `restore()` and `_safety_backup()` all gate on the same `verify_dump()` (line 540),
so **restore was broken by the identical cause**, not just backup.

Fixed: matched at any line start, `(?:\A|\n)` + `.search()` — the spelling `_USE_LINE` and
`_CREATE_DB_LINE` two lines below already use. Reproduced as a failing test first (identical
user-visible message), plus a companion test pinning that a file with no banner anywhere is still
refused. The trailer check, which is what actually proves completeness, is untouched.

## Finding 2 — MEDIUM — the Accounts tab is create-only
`ui/controller_view.py:931-975` builds only a Create form. `controller_wow_wotlk/accounts.py` has no
`list_accounts`, no `set_password`, no standalone `set_gm_level`. The raw Console can change a
password (`account set password ...` → "The password was changed"), but **there is no way to list
existing accounts from the app at all** — and `account list` is not a mangosd console command either,
so the Console is not a workaround for that half.

## Finding 3 — MEDIUM — self-update has never worked for anyone, and now we know why
`check_for_update()` is well built (TLS-verified, degrades gracefully, non-blocking) but
`https://api.github.com/repos/DadsMmoLab/dads-mmo-lab/releases/latest` returns 404. The repo has
**8 releases up to `v0.6.57Public`, and every one is flagged `prerelease: true`**. GitHub's
`/releases/latest` only answers from non-prerelease releases, so it 404s no matter how current the app
is. Release-process issue upstream, not a code bug — but it explains the identical "self-update is a
silent no-op" seen on all four other boxes. One release un-flagged fixes it everywhere.

## Correction to the brief (mine was wrong)
Tortoise's account scheme is **`mangos_sha`**, not `mangos_srp6` — `mangos_srp6` belongs to
`wow-tbc`/`wow-vanilla`. The code is correctly scheme-aware: account `TESTUSER1` (GM 3) created
through the GUI landed in `tw_logon` with a 40-char `sha_pass_hash`, SRP6 `s`/`v` NULL, `rank`=3 —
verified through the same entry-bound `DockerSql`/`schema_map()` path the app uses, against
`tw_logon` on `tortoise-db`, not AzerothCore's `acore_auth` default.

## Confirmed working
- **Console prompt is family-aware.** `entry.console.prompt` / `prompt_precedes_answer` (`mangos>`,
  precedes=False) are threaded to `send_command()` (`controller_view.py:153-159`) rather than
  AzerothCore's `AC>`. `server info` returned a clean answer with no prompt or log noise.
- **Networking plans are correct, including the `local_address_column: null` edge case.** LAN and
  Internet plans compute 3724/8090 and `tw_logon.realmlist.address`, write only the single address
  column since this family has no local-address column, and the Internet plan documents the
  split-horizon consequence ("most home routers have no hairpin NAT"). Apply was deliberately NOT
  clicked — it would flip `ufw --force enable` and rewrite the live realmlist row.
- **Port-conflict guard works.** Adopted the stopped AzerothCore stack and pressed Start while
  Tortoise held the ports: *"Another server is already using ports (3724, 8085): tortoise-realmd.
  Stop it first — only one server can run at a time."* `docker ps` confirmed nothing started.
- **Modules tab** correctly says "(this game has no manifests yet)" for Tortoise — gated to
  AzerothCore by design.
- **Log streaming** — "Follow worldserver log" streamed live and stopped cleanly.

## A false finding that was caught before it was reported
A "Check for updates"/"Refresh" control in the first screenshot belonged to a **stale legacy Tauri
"DML Launcher"** (`/usr/bin/launcher`, PID 34236, running since Aug 23) sitting at pixel-identical
window geometry behind the Yu'lon window — found with `wmctrl -lG`. Worth remembering: that process
is still on this box and will impersonate the app in screenshots.

## Not tested, and why
- Real client login with the created account — needs Wine/Proton for the Windows Turtle client;
  substituted DB-level scheme verification.
- Restore end-to-end — blocked by finding 1; code reading confirms it shares `verify_dump()`.
- Repair (finish DB import) — the button correctly stayed hidden, since neither adopted install had
  an unfinished import; inducing that state was destructive.
- GM-level change on an existing account — no control exists (finding 2).
- Firewall Apply — deliberately not run against the live box.

Residue left: test account `TESTUSER1` (id 7) in `tw_logon`, and the `~/yulon-run` clone.
