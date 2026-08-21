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
as `platforms: ["linux","macos","windows"]`, `script_platforms: ["linux"]` — so **Linux takes zero
new code paths**, and TBC/Vanilla/Tortoise stay Linux-only per the roadmap's scope gate.

`NativeInstaller.run(options, cancel) -> Iterator[str]` keeps the same contract as today's
`Installer.run()`, so the catalog view, log panel and job runner need no changes.

## Why, and what was rejected

**Per-platform script variants (macOS bash + Windows PowerShell) — rejected.** All four designs
rejected this independently. WotLK is already three near-identical ~1600-line scripts; the actual
install inside them is about six commands (a compose-images short-circuit, two clones, a 24-line
override heredoc, `compose up -d --build`, and a wait). Variants would mean five copies now and
twenty after Phase 7 — the style-guide §4 duplication Yu'lon exists to remove. Native Windows also
has no usable bash at all: `bash.exe` there is the Store alias for WSL and fails with
`execvpe(/bin/bash)` when no distro is installed (measured on a real Windows 11 box, 2026-08-21).

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

- **A 5-second bind-mount probe** (`docker run --rm -v <server_dir>:/probe alpine ls /probe`)
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
Windows path" — rests on a false premise and should not be chosen as written.

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
  login at every live gate.
- **Windows build-context transfer over 9p/drvfs is unmeasured.** Nobody has a number yet. Record
  it separately from the compile time at the first Windows gate.
- **Docker Desktop resource caps OOM-kill the compiler hours in**, surfacing as an opaque
  `signal 9`. Open question: make the memory floor a hard refusal on the native path rather than a
  warning. Leaning yes — a refusal costs a minute, a declined warning costs three hours. The Rust
  launcher refuses below 6 GB for exactly this reason.
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
  pairing, with `docker.stop()` kept as the explicit teardown path.

  The general lesson, worth carrying into 6.2: **a defect can live in the seam between two functions
  that are each individually right.** Mocked tests cannot see that seam; they assert the argv each half
  emits, which is exactly what was wrong. The live fixture that found it — a one-shot container that
  appends one line per run to a bind-mounted file, so a test counts the runs — is now part of the
  integration suite, and is the same shape the native install engine will need.
- `DockerSql` put the MySQL root password in argv (`-p<pw>`) and inline SQL in `-e`, both readable
  by any local process. Now `MYSQL_PWD` in the environment and the statement over stdin.

Plus the CRLF sweep (generated conf/realmlist files are LF regardless of host) and the six review
fixes listed in `pyplan/checklist.md` under Phase 6.
