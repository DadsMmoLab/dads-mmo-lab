# Phase 6 — decisions and why

> Companion to `pyplan/roadmap.md` §6. The roadmap says *what* Phase 6 must achieve; this page
> records *how* it was decided and what was rejected, so a reviewer can challenge the reasoning
> instead of only the commits. Written 2026-08-21, before 6.2 was implemented.
>
> Method: four implementation approaches were designed independently, then ranked by three judges
> with different priorities (a maintainer, a shipper, and a skeptic hunting hidden assumptions).
> Separately, the earlier Rust launcher was mined for prior art — see
> [`pyplan/rust-prior-art.md`](rust-prior-art.md), which it turns out had already solved several of
> these problems, with the incident notes to prove it.

---

## The decision

**One shared, typed Python install engine for macOS and native Windows, dispatched by
`catalog.json` data, with Linux staying on its proven bash script until the native path earns its
own live gate.**

Two catalog fields do the dispatching:

| field | meaning |
|---|---|
| `install.platforms` | where the entry can be installed at all (drives the 6.1 refusal) |
| `install.script_platforms` | where the *bash script* is the mechanism |

Anything in `platforms` but not in `script_platforms` runs the native engine. Phase 6 lands WotLK
as `platforms: ["linux","macos"]`, `script_platforms: ["linux"]` — so **Linux takes zero
new code paths**, and TBC/Vanilla/Tortoise stay Linux-only per the roadmap's scope gate.

`NativeInstaller.run(options, cancel) -> Iterator[str]` keeps the same contract as today's
`Installer.run()`, so the catalog view, log panel and job runner need no changes.

---

## Scope, runtime, and privilege decisions (moved from `roadmap.md`, 2026-08-24)

Four decisions used to live in `roadmap.md`'s Phase 6 preamble. Moved here because the roadmap is a
clean plan (style-guide §9), not a decision log; this page is where the *why* belongs.

### Why Phase 6 is its own phase

Phase 6 is not one of README's design phases — it is README §9's deferred "full native
reimplementation of installers" (the former "Phase 3b"), now its own phase. It was raised to its
own phase because the macOS pre-alpha run
made the gap concrete: **all four v1 installers are Linux-only bash scripts** gated on
`[[ "$OSTYPE" == "linux-gnu"* ]]` and hard-coupled to `pacman`/`systemctl`/`sudo`, so on macOS
(`darwin*`) and native Windows they fail fast with "Requires Linux (SteamOS)" — *before* the Docker
provisioning the app already does can help. The app must either run these servers cross-platform or
refuse to offer them off Linux.

### Runtime strategy

Use **Docker Desktop** as the container runtime on **both** Windows and macOS. It is materially
easier than managing the VMs directly — Docker Desktop already owns the Linux VM (macOS) and the
WSL2 backend (Windows), so the app just provisions Docker Desktop (which 5.1 already does) and
drives `docker compose` against it. We do **not** build a bespoke VM/WSL2 manager, and we do
**not** reimplement installers natively just to avoid Docker Desktop — the Linux kernel constraint
is satisfied by Docker Desktop itself. (Also summarised in `README.md` §3, the canonical copy.)

### Scope gate — WotLK first, exclusively

Phase 6 targets **WoW WotLK only** — it is the one v1 server with a full controller
(`controller_wow_wotlk/`), so it is the one place "100% working coverage" is achievable and
checkable end-to-end right now. 6.0's script rehome may touch all four games mechanically (it is a
path move, not a feature), but 6.1–6.5's actual gating/installer/feature work targets WotLK only.
**TBC, Vanilla, and Tortoise's own cross-platform install paths and controllers are explicitly out
of scope for Phase 6 — they are Phase 7.** Phase 7 must not start until Phase 6's WotLK exit
criteria (6.5) are fully met on Linux, macOS, and native Windows.

### Privilege transparency — no silent escalation of host privileges

Every install path — the Linux bash scripts (bugfix-only until Phase 7 retires them), the native
engine (6.2/6.3), and `ensure_docker()`'s provisioning — must honor one binding rule: **never add
the user to the `docker` group, and never write a passwordless `sudo` rule, without the user's
explicit, informed consent.** Two facts a game-server audience won't infer on its own: (1)
`docker`-group membership **is** root — `docker run -v /:/mnt --rm -it alpine chroot /mnt sh` edits
any host file, so there is no privilege boundary to protect; (2) a `NOPASSWD` docker rule is
therefore **redundant** (pure attack surface, no benefit) and must never be written. The
first-generation `install-*.sh` scripts did exactly this — `enable_docker_sudo_wrapper()` wrote
`/etc/sudoers.d/docker-nopasswd` behind `|| true`, so it could fail undetectably — and the native
engine must not reintroduce it. Incident history lives in `pyplan/checklist.md` (Cross-cutting).

---

## Why, and what was rejected

**Per-platform script variants (macOS bash + Windows PowerShell) — rejected.** All four designs
rejected this independently. WotLK is already three near-identical ~1600-line scripts; the actual
install inside them is about six commands (a compose-images short-circuit, two clones, a 24-line
override heredoc, `compose up -d --build`, and a wait). Variants would mean five copies now and
twenty after Phase 7 — the style-guide §4 duplication Yu'lon exists to remove. Native Windows also
has no usable bash at all — measured twice, on machines in different states, with the same conclusion
and *different* mechanisms:

- On a Windows that has had WSL enabled at some point, `bash.exe` is the Store alias for WSL and fails
  with `execvpe(/bin/bash)` when no distro is installed (real Windows 11 box, 2026-08-21).
- On a genuinely clean Windows 11 Pro 25H2 (build 26200.8037, 2026-08-22) there is **no `bash.exe` at
  all**: not in System32, not as an execution alias, `where.exe bash` exits 1 and cmd returns
  ERRORLEVEL 9009. The alias only comes into existence once WSL has been enabled.

Quoting only the first mechanism reads as false to anyone who re-measures on a clean box, so both are
recorded. A related trap found in the same pass: `shutil.which("python")` returns a **truthy** path on
a clean Windows 11 (a zero-byte Store alias at `WindowsApps\python.exe` that exits 9009), so a
`which()`-only probe reports Python present on a machine that has none. Probes must run the binary.

**Running the existing script inside a container (docker-out-of-docker) — rejected.** Elegant in
principle, and its author made the sharpest single argument in the set. It breaks on compose
identity: `docker.py` runs `compose up -d`/`down` with `cwd=server_dir`, so an install performed
from `/server` inside a helper container produces a project whose `working_dir` label the host
lifecycle cannot find afterwards. It also staked account creation on a socket mount plus a busybox
`script` applet plus an inner TTY check — three assumptions verifiable nowhere but the target
machine — while the boring answer (`docker exec -i … mysql`, already implemented and tested) sat
unused.

**Streaming the build through `runner.stream()` — rejected on a mechanism defect.** `runner.py`'s
own docstring says stderr "is yielded only after the process exits", and BuildKit writes all
progress to stderr. That is a blank log panel for three hours — precisely the "install starts but
console shows nothing" failure the roadmap's Phase 6 preamble exists to end.

**Moving server data onto named volumes on macOS/Windows — rejected.** It would make the
macOS/Windows server structurally different from the Linux one: untriageable bug reports, server
files invisible on the host, per-OS backups, and a new `DbcCopier` implementation — all as an
*unmeasured* I/O optimisation. Ship one verbatim override; split only if a live gate produces a
number that demands it.

**Flipping Linux to the native engine inside Phase 6 — rejected.** That would un-prove the only
path that has ever built a real server while two platforms with zero runs are still in flight.

Judges split 2–1: the dissenter preferred the approach that *tests* its Docker Desktop assumptions
rather than asserting them. That objection was accepted as far as it goes — every one of its
checks was grafted in (below) — but its structural bets were not, because a bolt-on check can be
added in a day and a structural difference cannot be un-made.

## Grafted in from the approaches that did not win

- **A bind-mount probe** (`docker run --rm --entrypoint ls -v <nearest populated ancestor>:/probe:ro
  <git image> -A /probe`, bounded 30 s — the preflight table below records why it is neither the chosen
  folder nor `alpine`)
  before anything long. Docker Desktop's file-sharing list is user-editable, so static path rules
  can both falsely block a legitimate directory and miss a broken one; a probe cannot be wrong.
- **`keep_awake()`** — a dad who starts a four-hour compile *will* close the lid, and the Docker
  Desktop VM suspends with it. `caffeinate` / `SetThreadExecutionState` / `systemd-inhibit`.
- **Check the VM's own free disk and RAM**, not just the host's: Docker Desktop's virtual disk
  defaults near 64 GB and the build hits ENOSPC at hour two.
- **`server_dir_problem(path)`** — refuse OneDrive/iCloud-synced folders, UNC and mapped drives,
  with an explanation, layered on top of the probe rather than instead of it.
- **Full-depth core clone** (AzerothCore's CMake derives its revision from git metadata); depth
  stays a data field so it can be tightened once a gate proves shallow is safe.
- **Port-conflict check in preflight**, before a three-hour build rather than after it.
- **Honest cancel copy.** Terminating the compose client leaves BuildKit finishing its current step
  in the daemon — and that is *desirable*, because the work lands in the layer cache. Saying
  "stopping…" while implying an instant halt invites users to `docker builder prune`, destroying
  the thing that makes resume cheap.
- **`.dockerignore`** as the cheap mitigation for Windows build-context transfer — but only after
  checking whether AzerothCore ships its own, and only if a gate proves the build does not read git
  metadata from the context (it would otherwise fight the full-depth-clone decision).

## Account creation — the finding that changes the plan

**SOAP cannot create the first account.** AzerothCore's SOAP endpoint authenticates against an
existing administrator account, so it cannot bootstrap the account it needs. Three of the four
designers reached this independently, and the Rust launcher had already hit it and written it down:
its `account_write.rs` exists precisely because `docker attach` refuses piped stdin against a
TTY container, and without the tty it never returns.

Consequence for roadmap 6.5 item 3: option (a) — "land the SOAP-based account creation as the
Windows path" — rests on a false premise and should not be chosen as written. **Acted on:**
`roadmap.md` 6.5 item 3 now says account creation no longer depends on the console at all and sends
it to item 4's SRP6-over-`DockerSql` path; what is left in item 3 is live GM commands.

The path that works identically everywhere is the database: compute AzerothCore's SRP6 registration
values (32 random salt bytes, `x = SHA1(salt || SHA1(UPPER(user) ":" UPPER(pass)))`,
`verifier = pow(7, x, N)`, little-endian, verifier zero-padded to 32 bytes) and insert them through
the existing `DockerSql` seam. This becomes the **primary** account path on all three platforms —
one implementation, no platform conditional, and it works before the worldserver has printed its
first `ready...`. `console.py` keeps live GM commands where a pty exists; its Windows gap shrinks
from "cannot create accounts" to "no interactive GM console", which is a smaller, separately
tracked item.

The candidate fix for that remainder — attaching over the Docker engine API rather than the
`docker attach` client, since the TTY requirement is a client-side check — conflicts with
style-guide §7 ("Docker via the CLI, not the SDK") and therefore needs an argued exception rather
than a silent one.

## Fate of the Linux bash path

1. **Phase 6:** Linux keeps the script (`script_platforms: ["linux"]`). The scripts go
   bugfix-only the day 6.2 lands; no fix goes into both.
2. **6.5 Linux gate:** run the native path side by side behind `YULON_NATIVE_INSTALL=1` on a box
   that already has a working install.
3. **Phase 7 entry:** once native clears the same bar on Linux, delete `"linux"` from
   `script_platforms` — a one-word JSON edit — and retire the three script variants. Decide
   consciously at that point what happens to `setup_gaming_mode()` (~192 lines of SteamOS
   `.desktop` and Steam shortcut integration with no Python replacement): extract it to its own
   script, or drop it deliberately.

## Risks worth re-reading before 6.2 ships

- **SRP6 must be byte-exact or the account exists and can never log in.** Unit vectors cannot prove
  this alone: derive the vector from a real server (create an account via the console, read back
  `salt`/`verifier`, assert Python reproduces the verifier from that salt) and require a real client
  login at every live gate. **The vector half was done 2026-08-23:** two accounts the worldserver
  itself created over its console on the Ubuntu box had their `salt`/`verifier` read back out of
  `acore_auth.account`, and `accounts.verifier_for()` reproduces both byte for byte — including a
  `Café1234` password, which `str.upper()` in place of `fold()` gets wrong
  (`controller_wow_wotlk/accounts.py`, pinned by `SERVER_WRITTEN` in `tests/test_accounts.py`). The
  client-login half is still owed at every live gate.
- **Windows build-context transfer over 9p/drvfs is unmeasured.** Nobody has a number yet. Record
  it separately from the compile time at the first Windows gate.
- **Docker Desktop resource caps OOM-kill the compiler hours in**, surfacing as an opaque
  `signal 9`. Settled: the memory floor is a hard refusal on the native path, not a warning — a
  refusal costs a minute, a declined warning costs three hours. `NativeInstall.min_ram_gb` defaults
  to 6.0 (`catalog/catalog.py`), `preflight._ram_check()` refuses below it, and a refusal aborts the
  install rather than offering an override (`native.py::_preflight_lines`). The Rust launcher
  refuses below 6 GB for the same reason.
- **Dual maintenance is real** for the length of Phase 6, bounded by the 6.5 Linux gate and
  reversible in one line.
- **The step engine is fixed, not a DSL.** It assumes "clone N sources, write an override, compose
  up" — which is exactly WotLK. TBC and Vanilla need client-data extraction and a separate DB repo;
  generalising now, with one example, is the wrong bet.
- **Open question nobody could settle from the repo:** whether the upstream Playerbots compose file
  bind-mounts the build tree itself. If it does, the "the compile happens inside the image, so the
  cross-filesystem cost is a one-time context upload" reasoning that every approach rests on is
  wrong, and the Windows/macOS default server directory has to move. Verify the actual bind list at
  the first live gate.

## Already landed from this pass

Two live bugs the review found in existing code, neither of them Phase 6 regressions:

- `docker.start()` ran a bare `compose up -d`, which re-runs AzerothCore's one-shot
  `ac-db-import`/`ac-client-data-init` on every restart — the thing this project's own
  `dml-start.sh` warns "was killing the database". Now `docker.start_staged()`.
- **…and that fix did nothing until the stop half was fixed too.** `Controller.stop()` ran
  `docker compose down`, which *removes* the containers, so the next `start_staged()` found nothing to
  start by name and fell straight back to `compose up -d`. Only a run against a real daemon showed it;
  every unit test passed throughout, because each half is correct in isolation and the invariant lives
  in the pair. Now `docker.stop_staged()`, mirroring `dml-start.sh`'s `docker stop`/`docker start`
  pairing, with `docker.remove_staged()` kept as the explicit teardown path — it was
  `docker.stop()` until 30f0b7ff renamed it and gave it `stop_staged()`'s ownership proof.

  The general lesson, worth carrying into 6.2: **a defect can live in the seam between two functions
  that are each individually right.** Mocked tests cannot see that seam; they assert the argv each half
  emits, which is exactly what was wrong. The live fixture that found it — a one-shot container that
  appends one line per run to a bind-mounted file, so a test counts the runs — is now part of the
  integration suite, and is the same shape the native install engine will need.
- `DockerSql` put the MySQL root password in argv (`-p<pw>`) and inline SQL in `-e`, both readable
  by any local process. Now `MYSQL_PWD` in the environment and the statement over stdin.

Plus the CRLF sweep (generated conf/realmlist files are LF regardless of host) and the six review
fixes listed in `pyplan/checklist.md` under Phase 6.

## Who owns a container — settled 2026-08-22, after getting it wrong twice

AzerothCore pins its container names **globally**: every install of the game has an
`ac-database`, an `ac-authserver` and an `ac-worldserver`. So a name proves that *something* is
using it, never that the thing is ours. The only ownership proof Docker offers is the
`com.docker.compose.project` label, and compose derives that project from the **directory
basename** unless `COMPOSE_PROJECT_NAME` is set in `.env`.

Three designs were tried against that. Two are now permanently rejected, and the reasons are
worth keeping because both looked strictly better than what shipped:

1. **Read the identity off a container's own label.** Fixes the moved-folder case outright. Also
   means a *stopped* install adopts the *running* neighbour's project as its own and then stops
   it, because the two share names. Rejected.
2. **Write the identity into `.env` whenever it is provably right** — after our installer, and
   after a stop whose census confirmed the labels. The value written is correct. It stops being
   correct the moment somebody copies the folder (a second realm, a restored backup): `.env`
   travels with the copy, `install_project()` prefers it over the directory, and the copy
   inherits the original's identity. Measured end to end: Stop in the copy took down the
   original's running server. **A file the user can duplicate is a claim, not a proof.** Rejected
   for the attach and stop paths; the install-time pin survives only because it is what makes a
   folder movable at all, and is written when exactly one install exists.
3. **Filter `status()` and `port_conflicts()` by label too.** Sounds like the same fix applied
   consistently. It moves the status poll's source of truth from `docker ps` — which is at least
   honest about failing — onto a file, and then disables the Stop button whenever the two
   disagree. Since Stop is the only thing that *explains* a disagreement, a live server showed as
   "down" with no button and no message. Rejected.

**What shipped:** names for *observation* (`status`, `port_conflicts`), labels for *action*
(`stop_staged`), and a refusal — shown on the Server tab — whenever ownership cannot be
established. Refusing to stop a server is recoverable; stopping somebody else's is not. The known
limit is stated rather than papered over: with two installs of one game the port-conflict guard
cannot fire, and `compose up` surfaces the daemon's own "container name is already in use".

**The residual hole, stated plainly.** Not pinning at attach or stop does not make a copied
install safe; it only declines to add a third way in. Two remain:

- the **install-time** pin in `catalog_view._on_run_finished()` is inherited by any copy of the
  folder it wrote, and `pin_project_name()` never overwrites, so attaching the copy does not
  clear it;
- an **unpinned** copy resolves to its own basename, which catches `~/wow` → `~/wow2` and misses
  `~/wow-server` → `/mnt/backup/wow-server`, where the basename is unchanged.

**The candidate fix, not implemented.** Compose also stamps
`com.docker.compose.project.working_dir` at create time. That alone cannot separate a move from a
copy — it is stale in both — but *whether the directory it names still exists* can: gone means the
folder moved, still there and not us means this is a copy. It needs care (network mounts,
permissions, a path that exists but holds something else) and it should be designed rather than
bolted on at the end of a review round. Until then the failure is loud rather than silent: the
Stop refusal names both possibilities, and the unpinned remedy says in as many words not to adopt
the name if this folder is a copy.

Carry into 6.2: the native engine should give each install an identity that is **not** a file in
the install directory — the `.env` pin is a workaround for compose's basename rule, not a design.

## The repair / re-import action — settled 2026-08-23

Checklist 6.5 owed a deliberate "repair / re-import" for an install interrupted *after* its
containers were created but *before* the import finished. `start_staged()` names three services
with `--no-deps` so an ordinary Start can never re-run `ac-db-import`; the honest consequence is
that nothing in the app could finish an import that never finished itself. Five judgement calls
went into `docker.repair_import()` and `controller_wow_wotlk/repair.py`, and each had a plausible
alternative.

**1. Detection is drawn on player data, not on completeness.** The obvious design asks "did the
import finish" and refuses when the answer is yes. It cannot be built: AzerothCore writes no
completion marker, and the only alternative — "`acore_world` should have N tables" — is a number
that silently rots with every upstream release, in the direction that offers to overwrite a good
database. So the refusal is keyed on the thing whose loss cannot be undone: rows in
`acore_auth.account` and `acore_characters.characters`. That question is asked **before** any
judgement about completeness, because a half-imported database that holds characters is still
somebody's server; asked the other way round, an install whose world schema never finished would
read as `partial`, offer the button, and take the accounts with it.

**The third state, stated rather than papered over.** A machine that died *late* in the import —
every schema created, some tables missing — reads as `imported` here and is not offered a repair.
That is a known blind spot, not a claim of correctness. What makes it acceptable is that it is
only reachable on a database with no player data, where the fallback (remove the containers,
install again) costs time and nothing else. A machine that died *early* — a schema with no tables
at all — reads as `partial` and is offered the repair, which is the common case: the import fills
one schema at a time.

**2. Two seams, because neither answers the whole question.** `apply.DockerSql` puts the database
in argv (`mysql -uroot acore_auth`), so against a never-imported server every call it makes fails
with "Unknown database" — it cannot ask whether the schemas exist. `maintenance.DockerMysql`'s
`databases()` runs with no schema selected and can. So existence comes from `DockerMysql`, and
everything inside an existing schema comes from `DockerSql`, routed through a schema the first
answer proved is there. Neither seam was widened, and the probe declares the read-only half of
`DockerSql` rather than reusing `accounts.SqlSeam`, which also carries `run_statement()`: nothing
in a probe should be able to write.

**3. The action starts the database, and that is a deliberate widening of "runs only the one-shot
service".** The requirement was one command naming one service. It is two, and the first is
`compose up -d --no-deps <db>` when the database is not already running. Without it the action is
**unreachable through the app's own buttons**: it refuses while the servers are up, Stop is the
only thing that stops them, and Stop stops the database with them. The alternative — drop
`--no-deps` from the import command and let compose's `depends_on` start the database — was
rejected twice over. The dependency closure of `ac-db-import` has never been measured in this
repo (upstream's compose file is not in the tree), and dropping `--no-deps` would also break
attached mode: `compose up` returns when every attached container stops, and the database never
stops, so the command would hang forever. `--no-deps` is what makes the one-shot the only
container compose brings up, which is what makes attached mode terminate.

**4. The exit code is not the answer, for the same reason it is not for `compose down`.** A
one-shot that died part-way and one that never touched a table are the same exit code from
outside. The probe runs again afterwards, and what counts is the schemas being FULL — `imported`,
or `populated` with `complete` set.

That last clause is not a hedge, and it took two corrections to get right. It first read "only a
database that now reads as `imported` counts", which the live gate broke: an AzerothCore import is
not only AzerothCore's SQL, because every module in the tree gets its own `data/sql/db-auth` and
`db-characters` updates applied by the same one-shot. Measured on yulon-ubuntu, 2026-08-23: a
first-ever import of an install carrying mod-city-bots finished exit 0 with all three schemas full
AND 400 accounts plus 400 characters the module's own update files had written. So the action
failed itself over its own success, and would have on every install this project ships.

Widening it to accept `populated` then opened a second hole, which a review caught before it
shipped: the probe answers `populated` on the FIRST row it finds, deliberately, because that is
the refusal protecting player data and it must not wait to finish counting tables. An import that
applies the module's `db-auth` updates and then dies on the world schema is therefore `populated`
with `acore_world` empty — indistinguishable, by state alone, from the finished one above. Hence
`ImportState.complete`, carried alongside the state rather than folded into it: the state stays
ordered by danger for the refusal, and the post-check reads completeness, which is the question it
was actually asking all along.

**5. Visibility is a remembered answer, not a live one.** The probe costs three `docker exec`s and
the status poll runs every five seconds, so it is put once per time the database comes up (plus
whenever Refresh is pressed) and the answer is kept. Keeping it is not laziness: the state this
action exists for is reached by pressing Stop, after which the database cannot be asked at all, so
a strictly-live answer would hide the button exactly when it is needed. The action itself never
trusts that memory — it re-censuses ownership and re-probes the database before it does anything.

**Live-gated 2026-08-23, partly.** Run on yulon-ubuntu against a throwaway copy of the real
AzerothCore + playerbots + city-bots install, on a fresh empty volume, with container names
renamed so the copy could not touch the original. What it settled:

- **Attached `compose up --no-deps <one-shot>` terminates.** One `repair_import()` call took
  209.0s around a container that ran 208.0s.
- **It re-runs an EXITED one-shot** rather than no-opping, with no `--force-recreate`. The same
  container was started three times (16:59:10 → 17:05:06 → 17:09:38), exit 0 each time, refilling
  schemas that had been dropped in between.
- **A finished import does NOT leave the account table empty** — see point 4 above. This is the
  one that broke the action, and it would have broken it on every shipped install.
- The refusal on a real populated server was exercised against the 650-account install and pointed
  at Restore, as did the refusals for a running server, an unreadable database, and an install
  that cannot name its project.

**What the gate did NOT settle, and a review was right to say so:**

- ~~It never repaired a `partial` database~~ — **run 2026-08-23, and it changed the design.**
  Interrupting a real import (`docker kill ac-db-import`, 19 s in) left `acore_world` with 3
  tables of 316. Two findings. The probe called that `imported`, because it asked only whether a
  schema had any tables — fixed to read `updates`/`updates_include`, AzerothCore's own updater
  bookkeeping. And re-running the one-shot over it **reported success in 28 s and left the schema
  permanently unimportable**: AzerothCore skips the base data for a database that already exists,
  so `acore_world` went 3 → 5 tables while `acore_world.updates` gained 2671 rows recording every
  remaining file as applied. `partial` was therefore refused outright, and then
  **the fix was built and gated the same day**: `repair.reset_unfinished()` drops the unfinished
  schemas through a write seam of its own (`SqlWrite`, separate from the read-only probe), and
  `repair_import()` calls it before the one-shot. Re-gated against a fresh interruption that left
  `acore_world` with one table: dropped, re-imported in 195 s, back at 316 tables, with the
  finished schemas untouched and the same one-shot container re-run. The seam stays optional —
  without it `partial` is still refused, which is what makes it safe to offer at all.
- **Assumption 1 was proven by container NAME, not by container ID.** The three start/finish pairs
  could in principle be three different containers reusing the name. The run pinned `ac-database`'s
  ID for exactly this reason and did not pin the one-shot's.
- **`acore_playerbots` is outside the probe's `CORE_DATABASES`**, and the compose file gives
  `ac-db-import` no `AC_PLAYERBOTS_DATABASE_INFO`, so the one-shot never creates it. On the
  playerbots install this project actually ships, `repair_import()` can therefore report success
  with that database still missing.
- The counterfactual for `--no-deps` — that without it `up` attaches to the database and never
  returns — is still an argument, not a measurement.

## The native install engine (6.2) — designed 2026-08-23, before implementation

> The build-from spec for `NativeInstaller`. Everything above this line decided *that* there is one
> shared engine; this section decides what it is made of, where each piece lives, and which parts
> of it nobody on this side of the project can verify. The short version of that last part: every
> macOS-specific claim below is unverified until Baerthe runs it on a real Mac, and the section
> ends with the exact list.

### What lives where

The engine is three new modules under `catalog/`, small additions to three existing shared
modules, and data. Nothing else moves.

| piece | lives in | why there |
|---|---|---|
| `NativeInstaller` — stage engine, state file, resume, the `run(options, cancel) -> Iterator[str]` contract | `yulon/catalog/native.py` | it orchestrates one catalog entry's install, which is `catalog/`'s charter; `installer.py` keeps the script path and gains only the dispatch factory |
| compose generation — entry + identity → three file texts + `.env` text | `yulon/catalog/composegen.py` | pure functions, no subprocess, testable byte-for-byte; the *content* comes from template data (below), never from Python conditionals |
| preflight — gather facts, apply floors, produce a typed report | `yulon/catalog/preflight.py` | it reads per-game floors from catalog data and machine facts from `platform.py`/`docker.py`; neither of those may know the other's half |
| compose templates + defaults | `catalog/installers/wow-wotlk/native/` | data, not code (style-guide §3); 6.0 made this directory the one clean home and 6.2 was told to grow it |
| floors, dispatch fields, clone depths | `catalog.json` | per-game facts: a different game compiles at a different cost |
| `keep_awake()`, `docker_desktop_data_root()`, `vm_resources()`, `server_dir_problem()` | `yulon/platform.py` | per-OS knowledge with zero game knowledge — exactly its charter |
| `bind_mount_ok()`, `build_staged()`, `run_one_shot()`; `cancel`/`merge_stderr` on `run_attached()` | `yulon/docker.py` | docker argv construction stays here so no caller can mis-spell the `-f` discipline; everything game-specific is passed in, per the `ContainerSpec` rule |
| `merge_stderr: bool = False` on `stream()` | `yulon/runner.py` | the one runner change the engine needs; see "streaming the build" |
| `probe`/`reset` seams for the import stage | supplied by the caller | they are per-game (`controller_wow_wotlk/repair.py`); `catalog/` must not import a controller package, and the view already assembles per-game seams for the Server tab |

`docker.py` still never learns a game's container names or ports — the engine passes the entry's
`ContainerSpec` and file lists in, the same shape `repair_import()` already takes. `ui/` still
never builds a docker argv: `catalog_view.py`'s only change is calling a factory instead of a
constructor.

### Dispatch, and the contract that keeps the UI unchanged

`Install` gains `script_platforms: tuple[PlatformId, ...] | None`, defaulting to `None`, with
`scripted_platforms()` reading `platforms` back when it is absent — so every existing catalog entry
keeps meaning exactly what it said. A method rather than a field default, on purpose: `None` still
tells an entry that was never asked the question apart from one that answered "the script runs
everywhere" (`catalog/catalog.py`). The rules, in order:

1. platform not in `platforms` → the 6.1 refusal, unchanged;
2. platform in `script_platforms` → today's `Installer` (the bash path), unchanged;
3. platform in `platforms` but not `script_platforms` → `NativeInstaller`.

The decision is made in one place: `installer_for(entry, ...)` in `catalog/installer.py`, returning
either class behind a shared `Protocol` (`preflight(options, cancel)`, `run(options, *, cancel,
ask) -> Iterator[str]`). `NativeInstaller` accepts `ask` and never uses it: nothing on the native
path may prompt — Docker Desktop is already provisioned by 5.1, there is no `sudo`, and a step that
turns out to need interaction is a design failure to fix, not a dialog to add. WotLK's entry
becomes `platforms: ["linux","macos"]`, `script_platforms: ["linux"]`. (`"windows"` was in the plan here and is NOT in `catalog.json`: it belongs to 6.3, which has not started, so Windows still takes the 6.1 refusal. Corrected 2026-08-24 — this file said three platforms in two places while the data said two.)

An `install.native` block joins the entry — floors and the template directory — and the engine
refuses, honestly, to run an entry whose platform dispatches native but whose `native` block is
missing. `Source` gains `depth: int | None = 1`; the core repo sets `"depth": null` in
`catalog.json` because AzerothCore's `genrev.cmake` reads the revision out of git history and a
shallow clone hands a three-hour build the wrong answer (`rust-prior-art.md` §1). This puts the
existing `CloneSpec.depth` docstring's warning into data, where the style guide says it belongs.

### Compose generation: three files, one `.env`

Straight from `rust-prior-art.md` §2, because every row of that table is an incident report:

| file | may hold | may never hold |
|---|---|---|
| `docker-compose.yml` | `name:`, services, `container_name:`, image tags, **all** `ports:`, binds, volumes, `depends_on`, healthchecks, `stdin_open`/`tty`/`stop_grace_period` | `build:` blocks |
| `docker-compose.override.yml` | runtime `AC_*` env, the `./modules` mount | anything structural — the future config system rewrites this file and its YAML writer keeps nothing it does not understand |
| `docker-compose.build.yml` | `build:` blocks only, each with an explicit `dockerfile:` and per-service `target:` | anything auto-loaded behavior depends on |
| `.env` | merged, non-default keys only | the project name — see identity, below |

**The DB root password is NOT in that `.env` row, and the code never put it there.** `render()`
writes it into `docker-compose.yml` as the `${DB_ROOT_PASSWORD:-…}` interpolation default, in
ten places, and returns an empty `dotenv`. Nothing today passes a per-install password — the
value is the catalog's fixed one, which the base file has to carry anyway — so this is a plan that
was not built rather than a behaviour that is wrong. `merge_dotenv()`/`write_dotenv()` exist and
are tested, with no non-test caller; they are the seam a generated per-install secret would
arrive through when something needs one.

**The `ports:` trap is a hard rule with a test.** Compose CONCATENATES `ports:` across files — a
port added in the override does not replace the base's, it publishes both. So `ports:` appears in
exactly one file, and a unit test asserts the rendered override and build files contain no `ports:`
key at any depth. Same discipline for the build trap: a bare `docker compose build` in that
directory builds NOTHING and exits 0, because naming any `-f` disables auto-loading — so
`docker.build_staged()` is the only builder, it always passes `-f base -f override -f build`, and
`up` stays bare. The explicit `dockerfile:` is not optional politeness: omitting it killed the
Rust launcher's first real build after five green stages of unit tests against a fake docker.

Template facts carried over: MySQL published on `127.0.0.1` only; SOAP loopback-pinned via the
interpolation default (`${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878`); the auth port
unpinned; worldserver `stdin_open: true, tty: true` (the console transport needs the tty) and
`stop_grace_period: 5m` — which our own measurement now confirms rather than inherits
(`STOP_GRACE_SECONDS = 300`, three shutdowns at ~2000 characters, worst 90.7 s); the client-data
init downloads with `curl --retry 30 --continue-at -` plus `unzip -t`, because upstream's
downloader truncates and restarts at byte zero on a flaky link.

**Where the template content comes from — this is not optional.** The implementer derives the
base file from `docker compose config` run against the proven Linux install on yulon-ubuntu, then
diffs it against prior art's template facts, and writes the result into
`catalog/installers/wow-wotlk/native/`. Inventing service definitions from memory is how the
`acore_playerbots`/`AC_PLAYERBOTS_DATABASE_INFO` gap above happened — the generated compose must
give the one-shot that variable, and this is the moment the gap gets closed. It also resolves this
file's open question about bind mounts: we generate the compose, so the bind list is ours, and the
templates must not bind the source tree into any runtime container. What survives of that question
is only the build-context upload cost, which stays a number to record at each first gate.

### Identity: the generated `name:` retires the `.env` pin

The base file carries `name: yulon-<game>-<install8>`, where `install8` is the first 8 hex digits
of SHA-256 of the server dir's absolute path. This is the answer to "carry into 6.2" above: the
identity lives in the file the engine itself generates and owns. (**This said `pin_project_name()` is never called on the native path, and that is false** — `catalog_view.py` pins on install-finished and the view is engine-blind, so a native install is pinned like any other. Harmless today, because `compose_project_name()` reads the generated `name:` and the pin agrees with it; retiring the pin on this path is unbuilt work, not a property to rely on. Corrected 2026-08-24.) and `install_project()` needs no change — `compose_project_name()`
already reads `name:` out of the files. A moved folder keeps working because the name travels in
the file. Volumes are project-scoped (the live gates saw `wow-server-playerbots_ac-database`), so
per-install project names give each install its own database volume for free.

**Container names stay `ac-*`, deliberately.** Dropping `container_name:` would give compose-derived
per-project names and dissolve the whole shared-names ownership problem — and it is still the wrong
move now, for the same reason named volumes were rejected: it would make a native install
structurally different from every Linux install in the wild, and it would break every path that
addresses `ac-worldserver` by name (console attach, repair, status, the archived guides,
`wow-manage.sh` parity). That migration is real, cross-platform, and deferred on purpose. The known
limits therefore remain known limits: a *copied* folder's base file claims the original's project
(the state file's path-hashed `install_id` refuses a copied dir at install/resume time, and the
lifecycle keeps the loud daemon conflict), and a second install of the same game still collides on
container names with the daemon's own error.

### Preflight — every check, its threshold, and who measured it

`preflight.py` gathers facts and returns a typed report of refusals, warnings, and *unchecked*
items — the tri-state discipline is a rule: an unreadable measurement is reported as "unchecked —
that is not a pass", never as a pass and never as a refusal. A stopped Docker Desktop prints
zeroes, so nothing reads a resource number without confirming the engine actually spoke.

| check | threshold | action | provenance |
|---|---|---|---|
| Docker daemon answers (after one `ensure_docker()` attempt) | — | refuse, non-overridable | everything below fabricates zeroes without it |
| VM RAM (`docker info` MemTotal — the VM's, not the host's) | < 6 GB refuse, < 8 GB warn | refuse | inherited: Rust measured 2 GB per compiler job; below 6 the OOM killer SIGKILLs a compiler and the symptom is "dies at the same low % every retry" with a bare `Killed`. Hard refusal per the open question above, now closed: leaning yes became yes — a false refusal costs one settings change, a declined warning costs three hours |
| CPU-vs-RAM | warn when `ncpu+1 > floor(mem/2GB)`, naming the exact CPU count to set | warn | inherited: upstream's Dockerfile hardcodes `-j $(nproc+1)` inside the RUN, so no build-arg can change it |
| Docker data-root free space | < 40 GB refuse, < 60 GB warn | refuse | inherited. On macOS/Windows `/var/lib/docker` is INSIDE the VM — measuring the host answers for the wrong drive, so the root resolves from Docker Desktop's settings JSON; on macOS that resolution is fresh and unverified (list below), and until the Mac gate proves it, macOS reports this check as *unchecked* rather than guessing |
| server-dir free space | < 8 GB refuse, < 15 GB warn | refuse | inherited: checkout 2.4 GB but clone PEAK ~3.7 GB, measured in Rust |
| data root and server dir on the same volume | floors ADD (48 / 75 GB) | refuse/warn as above | inherited |
| bind-mount probe: `docker run --rm --entrypoint ls -v <nearest populated ancestor>:/probe:ro <git image> -A /probe` | bounded 30 s | refuse, with the file-sharing explanation | grafted-in above; the probe pulls `git.CONTAINER_GIT_IMAGE` — the exact **digest** the clone stages pull, since a tag is a different image reference and asking for `alpine/git` cost a second, unpinned pull. **Corrected during implementation:** the exit code of `ls` cannot see the failure this exists for, because an unshared folder mounts as an EMPTY directory and `ls` on an empty directory exits 0 — and the chosen folder is empty at preflight time by construction. The probe therefore mounts the nearest ancestor that has entries and checks the container sees entries too; an ancestor's answer is the chosen folder's, and it also stops `-v` creating the directory before `guard` has claimed it |
| `server_dir_problem()` | OneDrive/iCloud-synced, UNC, mapped drive | refuse, with the reason | grafted-in; layered on top of the probe, not instead of it — the probe reports whether a mount works, the path rules explain *why* |
| port conflict (`foreign_port_conflicts(spec, project)` + socket probe) | a listener on the entry's ports that is not this install's own container | refuse — before the build, not after | grafted-in; the socket half only ever WARNS — the refusal comes solely from `foreign_port_conflicts()`. It is a connect probe (`platform.probe_tcp()`; only a completed connection counts), not a bind, so `AddrInUse` never arises: a refusal, a timeout and a permission error all come back `unknown`, because Hyper-V/WSL reserved ranges and permission errors would otherwise hard-refuse a server that would have started (rust-prior-art §4). **Corrected 2026-08-24** — this row said the socket half refuses on `AddrInUse`. **Corrected during implementation:** `port_conflicts_for()` is a global scan with no concept of which install a container belongs to, and preflight re-runs on every resume — so a `ready` timeout left the three `restart: unless-stopped` containers up and the next Install was refused and told to remove the containers of the install it was finishing. The conflict list is filtered by compose project, the same ownership proof `guard` uses; an unreadable owner is not filtered out |

Every numeric floor above is **inherited, none is measured by this project**. That is stated here
so the first live gates know their job: record the actual peak RAM, the actual data-root growth,
and the actual context-transfer time (Windows 9p and macOS VirtioFS both unmeasured), and replace
this table's provenance column with measurements.

There is no git check: the engine clones through `git.ContainerGit`, whose docstring already makes
the argument — macOS and Windows require Docker Desktop anyway, so a containerized git removes the
second prerequisite instead of adding one. It also deletes two of §4's Windows traps at the root:
no `git.exe` discovery and no Git-for-Windows curl. **The third, `core.autocrlf`, is not deleted,
and the code never relied on it being:** every containerized git call carries
`-c core.autocrlf=false -c core.eol=lf`, and the clone persists both through `--config`, because
the wrapper form writes nothing into `.git/config` and the next `git reset --hard` re-checks-out
CRLF if a host git ever touches the tree (`git.py`, `_LINE_ENDING_ARGS`/`_LINE_ENDING_CONFIG`).
Corrected 2026-08-24. The HTTP/2 large-clone reset may still exist in the container image's own
curl, so the clone passes `-c http.version=HTTP/1.1` anyway — one flag of insurance, checked at the
gate. **Both halves of that sentence were written as if built and neither was, until 2026-08-24.** The flag now exists, in the wrapper form on every network invocation and the `--config` form persisted into the clone, so the update path cannot renegotiate HTTP/2 on a repository that already cost 2.4 GB (`git.py`, pinned by a test). `http.postBuffer=524288000` was in the measured Windows fix and is deliberately NOT added: the two were changed together, nothing separates which one worked, and it is a widely-copied setting with a real cost and no mechanism connecting it to this failure. **Clones still do NOT retry** — prior art retried 3x for transport resets and this project does not; that is unbuilt work, not a described behaviour.

### The stages, by name

    preflight, guard, clone-core, clone-modules, generate-compose, build, client-data,
    start-db, import, up, ready

**`start-db` was added during implementation, and it is not optional.** This design settled the
same point for the repair button ("The action starts the database… Without it the action is
unreachable") and then wrote an install stage list without it. The import stage probes first and
the probe is a `docker exec <db container> mysql …`, so with no database container it answers
`unreadable` and the engine refuses — after the multi-hour build, on every install and every
resume. Running the one-shot regardless would not have helped either: `run_one_shot()` passes
`--no-deps`, which prunes exactly the `depends_on: ac-database: condition: service_healthy` edge
the generated base file declares. `docker.start_database()` is now the one implementation both the
repair button and this stage call, so the two cannot drift. It is never recorded: it is a
precondition, not progress.

State file `.yulon-install.json` in the server dir: `{version, game_id, install_id, completed[],
last_error, updated_unix}`, `install_id` = SHA-256 of the ABSOLUTE server dir path — a state file
copied into another directory is refused, which is the engine's answer to the copied-folder hole.
The rules, each of which cost the Rust launcher an evening:

- **`preflight` and `guard` are never recorded complete.** A guard that a resume skips is not a
  guard. `start-db`, `up` and `ready` are never recorded either: a resume always ends by actually
  starting and verifying the server, and it must bring the database back up before the import
  stage can ask it anything. All are cheap re-runs.
- **The state file is a HINT; every stage re-checks disk evidence.** clone stages: `.git` exists
  and `git remote get-url origin` matches the catalog source (an existing valid clone gets
  fetch+reset via the seam's own update path; a directory with the wrong remote is refused by
  name, never deleted). build: every one of this install's image references exists, asked of the daemon with `docker image inspect` — NOT `compose images -q`, which answers for created containers and so answered nothing in the built-but-not-yet-up window every resume asks in (measured 2026-08-24). generate-compose: see below.
  The Rust incident this rule exists for: an `is_done` short-circuit let a dropped-in state file
  make generate-compose rewrite a real server's compose file and orphan its character volumes.
- **`generate-compose` rewrites only files carrying the engine's own first-line marker comment**,
  and refuses to overwrite compose files it did not write. Idempotent by content. **One exception,
  and every install needs it:** the server dir IS the emulator checkout and that repository ships
  its own `docker-compose.yml` at the root — which is why the Linux installer writes only an
  override — so the clone stage lays an unmarked base file down before this stage ever runs, and
  the marker rule alone refused every install with "point the install at an empty folder", said to
  a user who had. The three-file split colliding with upstream's own file was not settled here and
  was invented at implementation time; it is settled now. `generate-compose` asks git whether
  `docker-compose.yml` is tracked and unmodified (`git status --porcelain -- docker-compose.yml`
  prints nothing) and replaces it only then, because that is the one state in which `git checkout`
  restores it byte for byte. A file the user edited answers ` M`, an untracked one `??`, an
  unaskable git answers nothing at all — all three keep the refusal. The override and build files
  get no exception: upstream ships neither.
- **Failure mid-stage records nothing** — the stage re-runs. `last_error` is only written when the
  state file already exists, and `guard` treats a directory containing only our state file as
  empty, so the state file can never become the non-empty dir that blocks its own retry.
- **`guard` is the claim on the chosen directory**, distinct from `preflight`'s machine facts:
  the dir is empty, or ours (state file's `install_id` matches this path); no container wearing
  the entry's names belongs to a foreign project (the remedy named is Remove, which exists).
  **Existence is asked before ownership.** `container_project()` returns `UNREADABLE` for any
  non-zero `docker inspect`, and `docker inspect <missing>` exits 1 — so asking it about all three
  names unconditionally refused every machine that had never run this server, naming a container
  the user could not then find. `docker._running()` has always guarded the same way.

**The `import` stage is where today's live gates paid off**, and it reuses `repair_import()`'s
pieces rather than restating them — the run+verify core is extracted into a helper both call, and
only the refusals differ, because an installer and a repair answer different questions:

- probe says `absent` → run the one-shot (attached, `--no-deps`, exactly the gated argv);
- probe says `partial` → `reset_unfinished()` first, then run. This is the lesson written in blood
  above: re-running the one-shot over a half-written schema *reported success in 28 s and left the
  schema permanently unimportable*. The engine is always constructed with the reset seam on the
  native path — unlike the repair button, an installer with no reset would strand exactly the
  interrupted install a resumable engine exists for;
- probe says `imported`, or `populated` with `complete` → skip. A resume must not touch a finished
  import, and `populated` alone is not failure — mod-city-bots seeds 400 accounts through the same
  one-shot (measured above);
- probe says `unreadable` → refuse; an unanswerable database is not an empty one;
- probe says `populated` but NOT `complete` → refuse. Rows nothing here wrote belong to somebody,
  and importing over them would overwrite them. This is the fifth branch `native.py::_import()`
  has and this list did not, until 2026-08-24.

After the run, the same post-check as `repair_import()`: `imported`, or `populated` with
`complete`. Cancel during this stage terminates the compose client while the one-shot keeps
running in the daemon; that is fine *because* the resume re-probes — whatever the importer managed
lands in one of the five branches above.

Cancel generally: checked between stages and inside every streamed loop; abandoning
`runner.stream()` terminates the child (compose client) and BuildKit keeps finishing its current
step in the daemon — desirable, the work lands in the layer cache. The cancel copy must say so
("finishing the current build step in the background; already-built work is kept") and never imply
an instant halt, or users `docker builder prune` away the thing that makes resume cheap.

**That copy belongs to the build and to nothing else.** The first implementation said it as the
second line of every install and appended it to every cancellation, so a user who stopped 20
minutes into the 2.4 GB clone was told Docker was finishing a build step. Each stage now carries
the sentence that is true of it — the build's layer cache, the client-data fetch's
`--continue-at -` resume, the import's clear-then-re-run — and a cancel between stages carries
none, because there is nothing extra to say. The opening line says only what holds everywhere:
nothing is written outside the chosen folder, and a restart repeats only the interrupted step.

### Streaming the build — the one `runner.py` change

`runner.stream()`'s contract yields stderr only after the child exits, and BuildKit writes ALL
progress to stderr. That exact pairing is why an entire candidate approach was rejected above as
"a blank log panel for three hours". The fix is one parameter: `stream(..., merge_stderr=False)`
sets `stderr=subprocess.STDOUT` at `Popen` when true, `run_attached()` passes it through, and the
build runs with `--progress plain` (deterministic non-tty output, no ANSI spinner to strip). No
other runner change; `interact()` and the pty path are untouched — the native engine never prompts.

Two Windows notes that belong to the engine rather than to 6.3's provisioning list: `runner.py`
spawns with no `creationflags` today, so every subprocess on native Windows flashes a console over
the UI unless `CREATE_NO_WINDOW` is added (rust-prior-art §4) — an implementation item, verified at
the 6.3 gate; and the engine never calls `wsl.exe` (Docker Desktop owns its backend), so the
CR-strip and UTF-16 traps apply only if that ever changes.

### `keep_awake()` — what it can honestly promise

A context manager in `platform.py`, held from `clone-core` through `ready` — `native.py` enters it
around the stage loop, which starts after `preflight` and `guard` have already run. macOS: spawn
`caffeinate -dims -w <our pid>` — a child that dies with us, no cleanup path to forget. Windows:
`SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)` via ctypes, set and cleared on the
same thread because the assertion is per-thread — which means the worker thread running the
install, and the context manager asserts it is not called on the GUI thread. Linux: a no-op for
Phase 6 (the script path is unchanged; `systemd-inhibit` waits for the 6.5 Linux gate).

**The roadmap's sentence overpromises, and this design narrows it out loud rather than silently.**
"A dad closing the lid mid-compile must not suspend the Docker Desktop VM" is not deliverable by
any sleep assertion: on a laptop, closing the lid forces sleep regardless of `caffeinate` (absent
external display + power) and regardless of `SetThreadExecutionState` (the lid action is a power
setting the app must not rewrite). What `keep_awake()` delivers is that an *idle* machine never
dozes off mid-compile — which is the case that actually eats a four-hour build. The lid case is UI
copy: the install view says to leave the lid open, plainly, before the build starts. If the roadmap
line is re-read as a requirement rather than a wish, this is the flag.

### Readiness

Nothing new. `wait_db_healthy_for()` and `wait_ready_for()` already exist, already poll
`StartedAt` and read logs `--since` that timestamp (never `--tail` — the marker prints once and
scrolls out of any tail window on a busy playerbots boot, hit independently by Rust and by Yu'lon
on the same day), and already drain their pipes on a bounded probe. The engine calls them.

### What is genuinely unknown about macOS — Baerthe's list

Nothing below can be verified by anyone on this project without a Mac. Each item is written as a
claim to check, not a fact:

1. **Docker Desktop's settings JSON.** Believed to be
   `~/Library/Group Containers/group.com.docker/settings-store.json` on current Docker Desktop
   (`settings.json` on older), keys `DataFolder`/`dataFolder`/`diskPath`, absent meaning the
   default `~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw`. Until the gate
   confirms the path, the keys, and what "free space" even means against a sparse `Docker.raw`
   (host free on that volume vs VM allocation minus used), the macOS data-root check reports
   *unchecked* — it does not guess.
2. **`caffeinate`.** Assumed present on every macOS (it ships with the OS) and assumed `-dims`
   holds the right assertion set for a Docker Desktop VM. Neither assumption has been executed.
3. **Apple silicon vs amd64.** Building from source on arm64 should produce arm64 images natively
   and just work — but only if every base image in the generated compose (`mysql`, the build
   image) publishes an arm64 manifest, and any amd64-only image means Rosetta/QEMU emulation with
   unknown compile-time cost or qemu faults. The first Mac gate records `docker version`'s
   architecture and each image's platform before anyone writes arch-specific code.
4. **File sharing.** `/Users` is believed shared by default, making the bind-mount probe pass for
   any home-dir install; the probe is the check either way — that is why it exists.
5. **VirtioFS build-context and bind-mount performance.** No number exists. Record it at the gate,
   separately from compile time, as the Windows twin of the 9p note above.
6. **The per-application firewall and Gatekeeper.** The servers listen inside the VM, so the
   firewall prompt (if any) should attach to Docker Desktop's own binary, not ours — believed, not
   known. Gatekeeper affects launching the unsigned `.dmg` app, not the engine. Both belong to 6.5
   (networking, packaging), and are listed here so nobody claims the install gate covered them.

The 6.2 Definition of done itself — a working WotLK server on a real Mac, zero shell interaction,
clean resume after a mid-build cancel, clean second install to a different directory — is by
construction unverifiable until that machine runs it.

### What the first gate must run before this engine is trusted

Five blockers survived 677 green tests and a 41-mutation run, and every one of them survived
because a test double answered a question the real seam cannot answer. (This said "Four" until a
sweep counted the list under it, 2026-08-24.)

**The running tally, kept here because three places disagreed about it.** Items 2 and 3 have been
run and ANSWERED, and both found real defects. Item 4 has been ATTEMPTED and not answered — the
attempt paid for itself by turning up a third defect, but the case it exists to test needs a
Hyper-V-backend box or a Mac. Items 1 and 5 are unrun. So: **two answered, three open**, and
"attempted" is not "answered", which is the distinction the disagreeing tallies slid over. The tests have since been
rewritten to model the real answers, but a double that models a function correctly is still not
the function. These are the checks nobody here can perform, in the order they would fail:

1. ~~**Clone the real repository into a tmp dir and run `generate-compose` against it.**~~ **Half
   settled, 2026-08-24, and the half that mattered.** The premise the whole replace-it rule rests on
   is confirmed: `mod-playerbots/azerothcore-wotlk` at branch `Playerbot` — the repo and branch
   `catalog.json` names — **does** ship a `docker-compose.yml` at its root, 7,930 bytes, listed
   alongside `.dockerignore` and `env/` (GitHub contents API, so an observation rather than an
   inference from this repo's fixtures). That confirms the missed blocker was real: without the
   exception, `generate-compose` would have refused every native install because the file it wants
   to write is already there. Still open: that `git status --porcelain -- docker-compose.yml` prints
   nothing on a fresh clone. It follows from the file being tracked and untouched, and
   `is_unmodified()` keeps the refusal when git says anything else or cannot answer — so the
   remaining risk is that the rule is too strict, not that it overwrites something.
2. ~~**Diff the generated files against `docker compose config` on the proven yulon-ubuntu
   install.**~~ **Run 2026-08-24, and it found one defect.** Every service, container name, port,
   `depends_on` edge and healthcheck matched; the build overlay parses and names its dockerfile
   for all four buildable services. Missing were `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS` and
   `AC_AI_PLAYERBOT_MAX_RANDOM_BOTS`, so a native install would have taken mod-playerbots'
   defaults rather than the 1600/2000 the Linux script configures — the same user getting two
   different worlds from one button. Fixed — the values live in `catalog.json`'s
   `install.native.world_env`, not in a Python constant, after a review pointed at
   style-guide §3. They are one desktop's numbers and no RAM measurement stands behind
   them; an RSS reading at 2000 bots is owed by the gate list this item belongs to. Eight other environment differences were checked
   against the image's `entrypoint.sh` and deliberately not carried over; details in
   `checklist.md`. This proves the file resolves, not that it builds or runs.
3. ~~**`docker compose -f… images -q` against a project that has been built but never
   started.**~~ **Run 2026-08-24: it answers EMPTY, and the fear was right.** Compose starts
   answering only once containers exist (`compose create` is enough), so in the
   built-but-not-yet-up window — the only window a resume asks in — every resume would have
   re-run the compile. `images_built()` now asks the daemon by image reference instead
   (`composegen.built_image_refs()` + `docker image inspect`), proven live in the same window.
   Details in `checklist.md`.
4. **A folder outside Docker Desktop's file-sharing list.** *Attempted on yulon-win11 2026-08-24
   and still open* — that box runs the WSL2 backend, which mounts the whole of `C:` through one
   unfiltered 9p share and has no per-directory list to violate, so this case needs a
   Hyper-V-backend box or a Mac. The attempt paid anyway. Against a substitute with the same
   observable (a mounted ISO the VM does not map) Docker **did** present the source as an empty
   directory and exit 0 — confirming the inherited premise on Windows, and confirming that an
   exit-code-only probe would have called it a pass. And the run found that `bind_mount_ok()`
   passed `ls` after the image name while the pinned image's entrypoint is `git`, so the probe
   answered False for every folder and preflight refused **every native install on every
   platform**. Fixed, with a live test that no unit test could replace; see `checklist.md`.
5. **`compose up -d --no-deps <db>` on a brand-new install**, i.e. the `start-db` stage against
   images this engine just built rather than the existing install the repair gate used.

### What the implementer should NOT build yet

- **No step DSL, no per-game step data.** The stage list is a fixed Python tuple that is exactly
  WotLK. TBC and Vanilla need client-data extraction and a separate DB repo; generalizing now,
  with one example, is the wrong bet — restated from the risks above because it is the likeliest
  scope creep.
- **No Linux flip.** `script_platforms: ["linux"]` stays; the scripts are bugfix-only; the
  side-by-side gate behind `YULON_NATIVE_INSTALL=1` is a 6.5 item.
- **No named volumes for server data, no `container_name:` removal** — both rejected above, both
  for the same reason: a structural difference between platforms makes bug reports untriageable.
- **No ninja `[1803/1808]` percentage bar.** Prior art has the parsing rules when wanted; a blank
  console was the defect, a progress number is polish. Plain streamed BuildKit output ships first.
- **No `.dockerignore`** until someone checks whether AzerothCore ships its own AND a gate proves
  the build does not read git metadata from the context — it would otherwise fight the
  full-depth-clone decision.
- **No SRP6/account creation, no SOAP autosetup inside the engine.** That is 6.5 item 4's own
  path, with its own byte-exactness gate.
- **No macOS firewall work in the engine.** 6.5 item 7 still owns it. The macOS firewall path
  itself was designed and built on 2026-08-24 — `platform.detect_alf_state()`/`AlfState`/
  `alf_unblock_commands()`, read into `networking._alf_notes()` and `NetworkPlan.firewall_state`
  — so "undesigned" is no longer the reason it stays out; the scope fence is. Every fact in it
  is still inherited from Apple's documentation, because nobody here has a Mac.
- **No new Docker Desktop start/provision logic.** `ensure_docker()` owns it; preflight calls it
  once and refuses honestly if it cannot deliver.

---

## Four decisions that were made and not recorded here (written down 2026-08-24)

This file's job is to hold the *why* so a reviewer can challenge the reasoning rather than only
the commits. Four decisions were taken in code and argued in code comments, which means they were
reviewable only by whoever opened that file. A doc-freshness pass over all of `pyplan/` found
them; they are recorded now, with what is settled and what is not.

### Where the docker-group consent lives — and a conflict flagged rather than resolved quietly

**Decided: the ask seam lives with the escalation, in `platform.ensure_docker(ask=...)`.** Not in
`catalog/preflight.py`.

`roadmap.md` 6.2 says "privilege consent belongs in preflight, not after the fact", and 6.2's
definition of done asks that "the preflight records explicit consent for the docker-group join".
That is satisfied, but by a different shape than the words suggest, so the divergence is stated
rather than glossed: the question is asked *during* preflight — both `Installer.preflight()` and
the native engine's preflight stage make exactly one `ensure_docker()` call, and that call now
asks before it runs anything privileged — and the outcome is recorded on
`ProvisionReport.docker_group`. `preflight.py` itself grows no consent `Check`.

Why not a `Check` in `preflight.py`, which is the more literal reading:

- **The question and the escalation must not be separable.** A `Check` produces a verdict that
  some later caller has to honour; a seam means the only code that can join the group is the code
  that just asked. The literal reading needs a `join_docker_group: bool` travelling from preflight
  to provisioning, and any future caller can assert a consent it never obtained.
- **It would break this file's own fence.** "No new Docker Desktop start/provision logic —
  `ensure_docker()` owns it; preflight calls it once and refuses honestly if it cannot deliver."
  Settling consent inside preflight puts provisioning policy back into preflight, in *both*
  engines, which is also the DRY violation the style guide forbids.
- **The payoff lands on an unreachable path.** The value of a `Check` is the user seeing one
  coherent list of what the install needs. Preflight only runs the native engine's checks on the
  native path, and no shipped catalog entry dispatches native on Linux — which is the only
  platform with a docker group at all.

**Flagged, per `roadmap.md`'s precedence rule** (README wins on what, style-guide on how, roadmap
on order; flag a conflict rather than silently picking): if the roadmap meant a literal preflight
`Check`, this is the divergence to overrule. The rule it exists to serve — never escalate without
informed consent — is met either way, and is now argued on the emitted argv by a parametrized
test plus eight mutations.

**Also settled, and it is the opposite of what the sentence above it says:** this file states
that nothing on the native path may prompt "because Docker Desktop is already provisioned and
there is no `sudo`". That was only ever true because `catalog.json` happens not to dispatch any
entry natively on Linux. The engine calls `ensure_docker()` itself, and on Linux that call can
escalate. The native path now passes no `ask` down, so declining is structural rather than a
consequence of today's data.

### Image tags are per-install, and that closes an open item

**Decided in code, unrecorded until now:** `composegen.DEFAULT_IMAGE_PREFIX` is
`yulon.local/ac-wotlk-` and `image_tag()` derives a per-install tag from the server directory.

`checklist.md` recorded, after the 2026-08-24 install re-pointed `acore/ac-wotlk-*:master` at
binaries from a different checkout and silently upgraded a working server, that this was "worth a
decision in `phase6-decisions.md` before 6.2 generates compose files that build". The decision was
already taken — it is `rust-prior-art.md` §2's answer, adopted whole: a per-install tag so two
installs cannot collide, and a prefix with a dot in it so Docker treats `yulon.local` as a
registry host that can never resolve to Docker Hub and accidentally pull someone else's image.

**Still open, and not the same problem:** the Linux *script* path — where the incident actually
happened — still builds `acore/ac-wotlk-*:master`. That is upstream's compose file, the scripts
are bugfix-only, and nothing in the app guards it. A second install of the same game on one Linux
box still silently upgrades the first.

### The database healthcheck asserts what its waiters need

**Decided: the generated base file's healthcheck carries `-h ac-database --protocol=TCP`**, which
upstream's does not. (It carried `-h 127.0.0.1` first, and this line said so for a day. An
adversarial review pointed out that loopback inside the container is not the interface consumers
arrive on either, so that spelling did not prove what this section claims for it; conceded and
changed the same day. The runs below tested the loopback spelling — see `checklist.md`.)

Not because the first-run race was confirmed — it was not. See `checklist.md`: the socket/TCP
mechanism did not reproduce in 10 fresh-volume runs, and the diagnosis is downgraded to a
hypothesis that an idle box could not test. The change stands on its own smaller claim: every
waiter on `condition: service_healthy` reaches the database over TCP from another container, so a
probe that proves only "a client inside the container can log in" is asserting the wrong thing.
The runs establish that it goes healthy in the same time as upstream's spelling (17.1-18.2 s), so
it is a strictly stronger condition at no measured cost.

### `SUDO_USER` decides whose name goes in the question

**Decided:** `ensure_docker()` resolves the user as an explicit `user=` → `SUDO_USER` or
`DOAS_USER`, **trusted only when the process is actually running as root** → the effective
uid's own passwd name → `USER` → `USERNAME` → `deck`.

It used to skip `SUDO_USER`, so under `sudo yulon` every remaining source says `root` — and the
consent dialog would have offered to add **root** to the docker group, then done it. Invisible
while the join was silent; user-visible the moment the name went into a question. The `deck`
fallback stays: it is the SteamOS default this project targets.
