# Gate E3 — WoW WotLK native Python installer, live on yulon-ubuntu

Verdict: **PASSED**. Install reached `ready` and exited 0. Defects found are UX/wording
and one design-vs-promise mismatch; none blocked the install.

Box: yulon-ubuntu, Ubuntu 24.04, kernel 7.0.0-30, 15 CPU, 19.5 GB RAM, 78 GB free at start,
user `pk`, reverted to `clean-ssh` (no Docker, no server dirs).
Code: github.com/pjerra/dads-mmo-lab branch `yulon-phase7`, HEAD `12b7ebb09e2b0ab113e6c7d0ee1ec7652af96223`.

`claude-say` does NOT exist on this box (`command -v claude-say` -> nothing). No activity
terminal was available; the run proceeded without it.

## 1. Setup

- `git clone --branch yulon-phase7 ... ~/gate` -> HEAD 12b7ebb0, clean.
- `python3 -m venv` FAILED first: Ubuntu 24.04 ships no `python3.12-venv`.
  Verbatim: `You may need to use sudo with that command.  After installing the python3-venv
  package, recreate your virtual environment.` Fixed by `apt-get install python3.12-venv`.
  This is a HOST prerequisite, not a product defect, but note it for the Linux bootstrap.
- All requirements installed cleanly, PySide6 6.11.2 included. No missing Qt libs
  (suite run with QT_QPA_PLATFORM=offscreen).
- Unit suite on Linux: **1320 passed, 2 skipped, 16 deselected in 35.01s**.
  (Laptop reported 1252 — Linux adds platform-conditional tests. No Linux-only failures.)

Harness invocation, read from the module docstring, not memory:
`python -m yulon.install_wiring <game> [--server-dir] [--client-dir] [--installers-root]`.
Run as: `.venv/bin/python -u -m yulon.install_wiring wow-wotlk --server-dir /home/pk/wowserver`
inside tmux + `script(1)` so the prompter saw a real tty (`_terminal_prompter` declines every
question when `sys.stdin.isatty()` is false).

## 2. Press 1 — from clean, no Docker

Questions asked: exactly ONE, the docker-group consent. Verbatim (log `~/gate-press1.log`):

```
Yu'lon can add 'pk' to the docker group, so it can use Docker without asking for your password every time.

Heads up: membership in the docker group is effectively full root access on this machine. A docker user can, for example, mount your entire disk inside a container and change any file.

If you say yes: you'll need to log out and back in once before it takes effect, then click Install again.
If you say no: Yu'lon still installs Docker Engine, but it runs docker directly (never through sudo), so it cannot install or manage a server here until you join the group yourself: sudo usermod -aG docker pk, then log out and back in.

Yu'lon never creates passwordless sudo rules and never changes the docker socket's permissions.

Add 'pk' to the docker group (grants root-equivalent access)? (y/n):  y
2026-08-31 14:29:29 INFO [yulon.platform] docker group consent for pk: granted
```

Asked **once**, in press 1 only. Counted across all four logs
(`grep -c "grants root-equivalent access"`): press1=1, press2=0, press3=0, press4=0.

NO sudo password dialog fired. `grep -iE "sudo password|password is required|\[sudo\]"` over all
four logs -> nothing. `SudoSession` stayed `unasked` (its verify() logs
"sudo password accepted/refused/declined" and none appear).

End of press 1, verbatim, exit code 1:

```
2026-08-31 14:30:19 ERROR [__main__] install failed: Docker isn't available and could not be set up automatically. Log out and back in (or run `newgrp docker`) so pk can use Docker without sudo, then click Install again.
install failed: Docker isn't available and could not be set up automatically. Log out and back in (or run `newgrp docker`) so pk can use Docker without sudo, then click Install again.
```

Outcome was in fact a full success: docker.io 29.1.3, containerd 2.2.1, docker-buildx 0.30.1,
docker-compose-v2 2.40.3 installed; `systemctl is-active docker` -> active; `pk` gained gid 124
(docker). No state file was written (nothing installed yet) — correct.

DEFECT D1 (UX, high visibility): a run that did everything right is reported as
`install failed: Docker isn't available and could not be set up automatically.`
Docker WAS set up automatically. The remedy sentence is correct; the lead sentence is false.

DEFECT D2 (UX): "then click Install again" is GUI wording emitted by the CLI harness. There is
no Install button in a terminal.

## 3. Re-login and press 2

Re-login = a fresh SSH session; `id -Gn` -> `... docker`, `docker ps` works without sudo.

Preflight, verbatim:

```
[pass] Docker: the daemon answered
[pass] memory: Docker's VM has 19.5 GB
[warn] CPU vs memory: 15 CPUs means 16 parallel compilers at about 2 GB each, and 19.5 GB affords about 9 Either raise the memory, or set Docker Desktop to 8 CPUs — the job count comes from the CPU count and cannot be set any other way.
[pass] free space on Docker's disk: 75 GB free
[pass] free space on the server folder: 75 GB free
[pass] the server folder: /home/pk/wowserver looks usable
[pass] sharing the folder with Docker: a container can read /home/pk/wowserver
[pass] SELinux: not enforcing
[pass] the server's ports: nothing else is using them
Using /home/pk/wowserver (a fresh install)
```

DEFECT D3 (text): the CPU-vs-memory warn has no sentence break —
`...affords about 9 Either raise the memory...`. A period or dash is missing after "9".

DEFECT D4 (wrong platform): on Linux it advises "set Docker Desktop to 8 CPUs". There is no
Docker Desktop here; this is Docker Engine 29.1.3 from the distro. The advice is unactionable.

The warn was also not predictive: 16 parallel compilers on 19.5 GB completed with no OOM.

Stage sequence in press 2: `clone-core -> clone-modules -> generate-compose -> build`
(killed during build). State file after generate-compose:

```
{"version": 1, "game_id": "wow-wotlk", "family": "azerothcore", "install_id": "243c46e3",
 "completed": ["clone-core", "clone-modules", "generate-compose"], "last_error": "", "updated_unix": 1788179802}
```

## 4. The interrupt test

Compile ran under way from 14:36:42. Killed at 14:46:56 with `pkill -9` on the harness, after
~10 minutes of continuous compiler output. Last line before the kill:

```
#25 454.4 [1314/1829] Building CXX object modules/CMakeFiles/modules.dir/mod-playerbots/src/Ai/Base/Actions/TravelAction.cpp.o
Script done on 2026-08-31 14:46:56+02:00 [COMMAND_EXIT_CODE="137"]
```

State file after the kill still listed only the three clone/compose stages — `build` was
correctly NOT recorded. No orphaned `systemd-inhibit ... sleep infinity` process was left behind.

Press 3 (resume) started 14:47:35. The build step reached object 1295 at **13.61 seconds** in:

```
#25 13.61 [1295/1829] Building CXX object modules/CMakeFiles/modules.dir/mod-playerbots/src/Ai/Base/Actions/TellEmblemsAction.cpp.o
#25 13.65 [1309/1829] Building CXX object modules/CMakeFiles/modules.dir/mod-playerbots/src/Ai/Base/Actions/TravelAction.cpp.o
#25 28.41 [1316/1829] Building CXX object modules/CMakeFiles/modules.dir/mod-playerbots/src/Ai/Base/Actions/WipeAction.cpp.o
```

1294 objects replayed from the persisted build tree in 13.6 s, then compilation continued from
1314 — where it was killed. **The resume did not re-compile.** RESULT: PASS.

The engine also says so up front, verbatim:

```
Stopping now leaves Docker finishing the build step it is already on, in the background. That is deliberate: the work it has done is kept, and starting this install again picks up from there instead of compiling it all a second time.
```

### Press 4 — a re-press on a COMPLETE install (extra, and the cleanest proof)

15:15:50 -> 15:16:06, **16 seconds**, exit 0:

```
Using /home/pk/wowserver (resuming)
Already finished: clone-core, clone-modules, generate-compose, build, client-data, import
--- clone-core
mod-playerbots/azerothcore-wotlk is in place.
--- clone-modules
--- generate-compose
Wrote docker-compose.yml
--- build
The server is already built; skipping the compile.
--- client-data
Server data is in place.
--- start-db
The database is up.
--- import
--- up
--- ready
The server is up.
WoW WotLK is installed and running in /home/pk/wowserver
Script done on 2026-08-31 15:16:06+02:00 [COMMAND_EXIT_CODE="0"]
```

DEFECT D5 (promise vs behaviour). The opening banner says:
`starting the install again continues from the last step that finished — only the step that was
interrupted runs again.` That is not what happens. `InstallState.has()` is documented in
`yulon/catalog/native.py` as "Did a previous run finish `stage`? Never a reason to skip on its
own", and the spine runs EVERY stage every time. The `Already finished: ...` line is printed and
then all the listed stages run anyway. On press 3 that meant `clone-core` did:

```
... fetch origin Playerbot ... into /home/pk/wowserver
... reset --hard FETCH_HEAD ... into /home/pk/wowserver
```

and `clone-modules` did the same with `--depth=1`. So every resume performs a
`git reset --hard FETCH_HEAD` on the core and the modules. Cheap here (7 s), but it (a) discards
any local change to the source tree without warning, and (b) can move the source to a newer
upstream commit between an interrupted build and its resume, producing a half-old/half-new
compile. The user-facing sentence promises the opposite.

DEFECT D6 (minor): on a re-press of a RUNNING install the port check flags the install's own
containers as a foreign conflict:

```
[warn] the server's ports: something on this machine is already listening on 3724, 8085 If the server cannot start, that is the first thing to look at.
```

Same missing sentence break as D3 (`...3724, 8085 If the server...`).

DEFECT D7 (minor): a finished WotLK install consumes enough disk that the free-space check
warns on every subsequent press — 75 GB at start, 67 GB at press 3, 53 GB at press 4, against a
"75 GB is the comfortable figure" threshold. The threshold does not account for the install
itself already being on disk.

## 5. Final state

Full stage sequence reached (press 3):
`clone-core -> clone-modules -> generate-compose -> build -> client-data -> start-db -> import -> up -> ready`

State file `/home/pk/wowserver/.yulon-install.json`:

```
{
  "version": 1,
  "game_id": "wow-wotlk",
  "family": "azerothcore",
  "install_id": "243c46e3",
  "completed": ["clone-core", "clone-modules", "generate-compose", "build", "client-data", "import"],
  "last_error": "",
  "updated_unix": 1788181688
}
```

Note: `start-db`, `up` and `ready` are not recorded (their Stage.recorded is false).

Ending lines of press 3:

```
--- ready
Waiting for the database.
Waiting for the world server to finish loading (this can take many minutes).
The server is up.
2026-08-31 15:12:38 INFO [yulon.catalog.native] install of wow-wotlk finished
WoW WotLK is installed and running in /home/pk/wowserver
Script done on 2026-08-31 15:12:38+02:00 [COMMAND_EXIT_CODE="0"]
```

`docker ps`:

```
NAMES            IMAGE                                              STATUS                    PORTS
ac-worldserver   yulon.local/ac-wotlk-worldserver:native-243c46e3   Up 7 minutes              127.0.0.1:7878->7878/tcp, 0.0.0.0:8085->8085/tcp, [::]:8085->8085/tcp
ac-authserver    yulon.local/ac-wotlk-authserver:native-243c46e3    Up 7 minutes              0.0.0.0:3724->3724/tcp, [::]:3724->3724/tcp
ac-database      mysql:8.4                                          Up 12 minutes (healthy)   127.0.0.1:3306->3306/tcp, 33060/tcp
```

Auth container realm line:

```
Started auth database connection pool.
Added realm "AzerothCore" at 127.0.0.1:8085.
```

World container ready marker:

```
WORLD: World Initialized In 1 Minutes 49 Seconds
AzerothCore rev. 47960183bb03+ 2026-08-28 21:04:11 +0200 (Playerbot branch) (Unix, RelWithDebInfo, Static) (worldserver-daemon) ready...
```

OBSERVATION O1: the realm is advertised at `127.0.0.1:8085`. Only a client on this same host can
connect. Whether that is the intended default for a fresh install is a product question, not a
gate failure.

OBSERVATION O2: worldserver logs a config gap on every boot —
`> Config: Missing property AiPlayerbot.ForceRebuffOnReadyCheck in config file
/azerothcore/env/dist/etc/worldserver.conf`. The generated conf is behind the playerbots module.

`docker compose config` saved to `/home/pk/gate-compose-config.yml` (219 lines, exit 0, empty
stderr). Compose project name: `yulon-wow-wotlk-243c46e3`.

## 6. The three questions

### Q1 — Does `sudo -S` behave as the code assumes?

Two answers.

(a) In THIS run: not exercised. `sudo -n` succeeds on this box for every command, so
`_needs_password(proc.stderr)` was never true, `SudoSession.verify()` was never called, the
session stayed `unasked`, and no privileged command was ever run as `sudo -S -p ''`. Therefore
**no privileged command in this run received a stray line on stdin.** Proof: no
"sudo password accepted/refused/declined" log line in any of the four logs.

(b) The hazard is real and reproduced on this box:

```
$ printf "SECRETLINE\nSECOND\n" | LC_ALL=C sudo -S -p "" cat
SECRETLINE
SECOND
--- exit=0
```

When sudo does not prompt, it consumes NOTHING from stdin and the whole payload reaches the
child. `SudoSession.run()` unconditionally feeds `password + "\n"` to every subsequent command
once `elevated` is set. On a MIXED sudoers — where the first package command needs a password
and a later one is NOPASSWD — that later command would receive the user's root password on its
own stdin. `_run_steps()` sets `elevated = True` once and never re-checks. Latent; not triggered
by an all-NOPASSWD box like this one, and not triggered by an all-password box either. Fedora
(the password path) should be watched for exactly this.

### Q2 — Is `"sudo: a password is required"` the real C-locale string here?

**Yes, exactly.** The host runs sudo 1.9.15p5 and cannot be made to prompt (`(ALL) NOPASSWD: ALL`
in its sudoers, which was not modified). Measured in a container running the identical version:

```
Sudo version 1.9.15p5
=== LC_ALL=C sudo -n true as a password-requiring user:
sudo: a password is required
exit=1
```

The literal is also present in the host's own `/usr/libexec/sudo/sudoers.so`
(`grep -ao "a password is required"` -> hit). `platform._needs_password()` matches on the
substring `"password is required"` lowercased, which is a superset of the exact string — correct
and slightly more robust than an exact compare. `_c_locale_env()` pins `LC_ALL=C` and clears
`LANGUAGE`, so the trigger is sound on this distro.

### Q3 — Does chcon / SELinux matter here?

**No, and the probe answers `False`, not `None`** — confirmed directly:

```
$ .venv/bin/python -c "from yulon import platform; print(repr(platform.selinux_enforcing()))"
False
```

and the preflight rendered the `False` branch, not the `unchecked` branch:

```
[pass] SELinux: not enforcing
```

The `None` branch would have printed `[unchecked] SELinux: whether SELinux is enforcing could not
be read — that is not a pass`. It did not. No `chcon` was attempted and none was needed; no `:z`
bind option appears in `docker compose config`.

## 7. Summary

PASSED:
- Provisioning from a truly clean box: Docker installed, group joined, correct re-login instruction.
- Docker-group consent asked exactly once, in press 1 only.
- No sudo password dialog on a passwordless box; SudoSession stayed unasked.
- SELinux probe -> False (not None).
- Full stage sequence to `ready`, exit 0, all three containers up, realm added, world server ready.
- Interrupt/resume: the compile is NOT redone. 1294 objects replayed in 13.6 s; a full re-press
  of a finished install takes 16 s and says "The server is already built; skipping the compile."
- State file written, parseable, and correct at every checkpoint.

FAILED / DEFECTS:
- D1 a successful provisioning run reports `install failed: Docker isn't available and could not be set up automatically.`
- D2 "click Install again" GUI wording in the CLI.
- D3 missing sentence break in two warn strings ("...affords about 9 Either raise..." and "...3724, 8085 If the server...").
- D4 "set Docker Desktop to 8 CPUs" advised on a Linux Docker Engine host.
- D5 the banner promises "only the step that was interrupted runs again"; every stage runs every
  time, and clone-core/clone-modules do `git reset --hard FETCH_HEAD` on every resume.
- D6 the port preflight flags the install's own running containers as a conflict.
- D7 the free-space threshold does not discount the install already on disk, so every re-press warns.

Latent, not triggered here: the `sudo -S` stdin passthrough on a mixed-sudoers host (Q1b).

Artifacts on this box:
`~/gate-press1.log`, `~/gate-press2.log`, `~/gate-press3.log`, `~/gate-press4.log`,
`~/gate-compose-config.yml`, `~/gate-e3-report.md`, install at `~/wowserver`.
