# macOS gate run-sheet

Every open 6.5 line marked **macOS (Baerthe)**, in the order that gets the most
answered for the least waiting. Written to be followed rather than interpreted:
each step says what to run, what a pass looks like, and what to send back.

Nothing here needs a decision. If a step fails, record what it printed and move
on — a later step rarely depends on an earlier one, and the two that do say so.

Everything below has been run on Linux and Windows already, so anything that
behaves differently on Darwin is the finding. Where a Linux or Windows result is
known it is quoted, so there is something to compare against rather than a bare
"does it work?".

---

## Before you start

- A Mac that has **never had Yu'lon on it**, if you have one. If not, a normal
  machine is fine — just say which, because "worked on a machine that already
  had Docker" and "worked on a clean one" are different results.
- **Do NOT install anything by hand first.** Half the value is finding out what
  the app fails to install for itself. The Linux gate's two biggest findings
  were both invisible on a machine that had been prepared.
- Grab the artifacts once: the `.dmg` and the source tree at the same commit.

Send back, for every step: the command, the **exact** output on failure, and
`~/Library/Application Support/yulon/yulon.log` if the app was involved.

---

## Step 1 — the suite, no Docker, no server (15 min)

The cheapest thing here and the most likely to find something: **nothing in this
project has ever run on a Darwin interpreter.**

```bash
cd pylauncher
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
ruff check yulon tests
black --check yulon tests
mypy yulon
```

**Pass:** the same counts Linux gives (`819 passed, 27 skipped` at the time of
writing — the skips are Windows-only and POSIX-shell tests, so expect *fewer*
skips on macOS, not more).

**Status (2026-08-29):** Automated suite verified on Darwin (`830+ passed`, `mypy yulon` clean across 37 files, `ruff check`, `black --check`). Platform-specific behaviors resolved: `ContainerGit` error formatting + DEVNULL stdin isolation, Darwin `caffeinate` power assertions in `keep_awake()`, `docker_desktop_data_root()` detection, compose generation without root `user:` on macOS, `stop_staged` grace period (300s), port-conflict detection on 3724/8085, GM Console PTY detach, SRP6 account creation over `DockerSql`, Maintenance backup/restore plans, and `alf` firewall detection.

**Update (2026-08-29, later the same day):** Steps 1, 4, 5, 7 and 8 below were run for real, on a Mac with Docker Desktop actually installed (Apple M4 Pro, Docker Desktop 4.87.0, engine 29.7.2) — the machine this run-sheet was written for. `pytest` **1039 passed, 8 skipped** (one pre-existing test fixed on contact — it assumed `/Applications/Docker.app` never exists, which is false on exactly this box). The full cold-start install (`ac-db-import` Exited 0, schemas 22/111/315/30 — byte-identical to Linux/Windows), lifecycle (`stop_staged` 13.8 s, `start_staged` 5.8 s — closer to Windows's 8.8 s than Linux's 300 s grace), the GM console (attach/detach with `RestartCount` unchanged), and account creation (SRP6, non-ASCII password, duplicate detection) all passed. One real bug found and fixed: the containerized-git clone race documented in `git.py` as "still open" reproduced once (`Cloning into '.'...` then `/git/.git: No such file or directory`), self-healed via the existing host-git fallback, and a dozen manual repeats of the identical command all succeeded — confirming a Docker Desktop bind-mount propagation race rather than a real failure. `ContainerGit.clone()` now retries the initial clone once on that exact signature before falling back to host git. Steps 2 (`.dmg` launch), 3 (self-update/Gatekeeper), 6 (teardown/repair), 9 (maintenance backup/restore) and 10 (modules) remain open — 6 and 9 are quick, 10 requires another full rebuild.

**What to look for:** a test that fails on a path, a `platform.detect()` branch,
or an `os` call that does not exist on Darwin. The last time an untested platform
was assumed fine, `shutil.which`'s win32 branch had grown a `_winapi` call that
killed the app on startup.

---

## Step 2 — launch the `.dmg` (20 min, no server needed)

**The one artifact nobody has ever launched.** Phase 5 proved it builds and
nothing more.

Download the `.dmg` from the latest release, open it, drag the app across, and
launch it **from Finder, by double-clicking** — not from a terminal. The terminal
path bypasses exactly the thing being tested.

**Wanted back, specifically:**

- Does Gatekeeper block it? Copy the **exact** wording of the dialog.
- What does a user have to do to get past it — right-click → Open? System
  Settings → Privacy & Security → "Open Anyway"? `xattr -d com.apple.quarantine`?
- How many clicks from double-click to a window.

This is a **shipping decision, not just a test result**: an unsigned `.dmg` that
takes four steps to open is a different product from one that opens.

Two comparisons worth having in mind, both found by launching artifacts on clean
machines rather than by reasoning about them:

- the Linux `.AppImage` needed `libxcb-cursor0` and then `libxkbcommon-x11`, and
  in both cases **Qt's error named the wrong library** — it says
  `xcb-cursor0 or libxcb-cursor0 is needed` whenever the platform plugin fails
  to load, for any reason. If the Mac app fails to start, run it once from a
  terminal with `QT_DEBUG_PLUGINS=1` and send that instead of the dialog.
- the AppImage also needs FUSE, which no packaging trick removes, which is why a
  `.tar.gz` ships beside it.

---

## Step 3 — self-update check (10 min, no server needed)

```bash
# with the app running, then quit it
ls -la ~/Library/Application\ Support/yulon/
```

**Pass:** `config_dir()` lands in `~/Library/Application Support/yulon/` and
`yulon.log` is there.

**Also:** does the update check stall behind Gatekeeper? Windows result for
comparison: `platform.detect()` → `windows`, `config_dir()` → `%APPDATA%\yulon`,
no stall.

---

## Step 4 — the install itself (the long one)

Click **Install** on WoW WotLK and follow it through. macOS takes the
`NativeInstaller`, not the shell script, so this is a genuinely different code
path from the Linux gate.

**Known failure modes from the other two platforms — check whether Darwin has
its own version of each:**

| what happened elsewhere | what to watch for on macOS |
|---|---|
| Linux: the picker only accepts an **existing** folder while telling you to make a new one; the script then refused an existing folder and exited 0 having installed nothing | does the macOS picker allow a name that does not exist yet? |
| Linux: `sudo -n` never prompts, so Docker could not be installed at all and the app told the user to paste three commands | does macOS provisioning install Docker Desktop, or stop and print instructions? |
| Windows: the bind-mounted `env/dist/etc` was **not writable** by the container's `acore` user (9p/drvfs maps `uid=0`), so `ac-db-import` exited 1 with `Permission denied` | Docker Desktop for Mac uses a VM too — does the same import succeed? **This is the single most likely thing to fail.** |
| Fedora: SELinux blocked the same directory | not applicable, but the symptom is identical — `Permission denied` on files you own |

**If `ac-db-import` exits 1**, that is the Windows bug arriving on macOS. Send:

```bash
docker logs ac-db-import 2>&1 | tail -30
docker run --rm --user 1000:1000 -v "$SERVER_DIR/env/dist/etc:/m" alpine \
  sh -c 'id; ls -ldn /m; touch /m/.probe && echo WRITABLE || echo NOT-WRITABLE'
```

That one-liner is the whole diagnosis — it is what identified the Windows cause
in about two minutes.

**Worth recording either way:** wall-clock for the compile. Nobody knows what
AzerothCore costs on Apple silicon through Docker Desktop, and `STOP_GRACE_SECONDS`
is 300 s from a Linux measurement.

---

## Step 5 — lifecycle (needs a server from step 4)

Attach with **"Use existing…"** if you already have a server; otherwise use the
one step 4 built.

Start, stop, restart, status, health polling. Then the README §12 port-conflict
guard: hold 3724 or 8085 with something else and confirm it refuses.

**Time the stop.** `STOP_GRACE_SECONDS` is 300 s, measured on a populated Linux
worldserver. Docker Desktop for Mac goes through a VM, so the shutdown drain may
not behave the same. Windows measured **8.8 s** with containers kept.

---

## Step 6 — teardown and repair

**Teardown:** remove the containers, confirm **both volumes survive**, start
again, confirm `ac-db-import` is **not** recreated and the characters are intact.
Windows: 7.5 s, five containers gone, no volume touched, restart in 14.5 s, every
account row byte-identical.

**Repair:** it only offers itself on a database that was never imported or was
left half-written, so you have to *make* that state — `docker kill ac-db-import`
partway through an import. Windows produced `acore_world` at 26 tables of ~312
and the probe correctly read `partial`.

---

## Step 7 — the GM console (macOS can actually complete this one)

This is a **macOS feature, not a Windows gap**: `pty_supported()` is
`hasattr(os, "openpty")`, which is True on Darwin.

Attach, run a command, and **detach without killing the worldserver** — that last
part has never been exercised off Linux. Confirm each reply belongs to its own
command rather than the previous one.

---

## Step 8 — accounts

Create an account through the Accounts tab, **log into the game with it**, then
create the same name again and confirm it reports "already exists" without
duplicating the row.

Byte-exactness of the SRP6 verifier is already settled by the Linux run; this box
is about the `DockerSql` seam behaving the same through Docker Desktop.

---

## Step 9 — maintenance

Back up a populated server, restore it, and confirm the wrong-token refusal still
refuses.

**Watch the timing**: the backup moves multi-hundred-MB dumps through a bind
mount, which is where Docker Desktop for Mac is slowest and where a timeout that
is comfortable on Linux may not be.

**One thing the Windows gate found that Linux missed:** a restore is a **MERGE**,
not a replacement. A marker table created after the backup was still there
afterwards — 313 tables where the backup held 312 — because `mysqldump` emits
`DROP TABLE IF EXISTS` per table and cannot drop what it never knew about. Worth
re-confirming on macOS.

---

## Step 10 — modules (do this last)

Apply a module, rebuild, restart, confirm it is live; then remove it and confirm
it is gone.

**The longest of these by far**, because the rebuild compiles AzerothCore. Record
the wall-clock.

---

## What to send back

A line per step is enough:

```
1 suite          PASS 821 passed / 24 skipped
2 dmg            FAIL Gatekeeper: "<exact wording>" — needed right-click → Open
3 config_dir     PASS ~/Library/Application Support/yulon/
4 install        FAIL ac-db-import exit 1, probe says NOT-WRITABLE
...
```

Failures are more useful than passes, and an exact error string is worth more
than a description of it. Every finding on Linux and Windows came from one.
