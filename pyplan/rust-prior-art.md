# Rust prior art (what the earlier launcher already solved)

> **What this is.** Dad's MMO Lab has an earlier launcher written in Rust (branch `rust-main` of the
> same project): a Tauri app over a `dml-core`/`dml-wow` workspace that installs and drives the same
> AzerothCore + playerbots stack. **This is the "Hypeer Launcher" named in roadmap Phase 8** — same
> author, same repository, so porting from it raises no licensing or attribution question. It solved several problems Yu'lon is about to meet — and, more
> valuably, it records the incidents that produced each decision. This page is the distillation, so
> nobody has to read Rust to benefit from it.
>
> **How to use it.** Phase 6 (macOS + native Windows install paths) has now lifted most of
> sections 1-5: `catalog/native.py` (§1), `catalog/composegen.py` (§2), `catalog/preflight.py`
> (§3) and `controller_wow_wotlk/accounts.py` (§5). §4 is partial — `platform.py` has the
> `docker.exe` discovery and `git.py` the `core.autocrlf` and `http.version` pins, but nothing
> spawns with `CREATE_NO_WINDOW` and 6.3's native Windows path is unfinished, so what is left of
> it still lifts almost
> directly — the native installer exists precisely because the bash installers could not run off
> Linux. Phase 8 (feature parity with The Lab) is where the *bulk* of that codebase gets ported;
> section 7 lists what is waiting there. Nothing here is a mandate: it is evidence, with file:line
> so a claim can be checked.
>
> **Provenance.** Extracted 2026-08-21 by reading `origin/rust-main`. Line numbers refer to that
> branch; read any file with `git show origin/rust-main:<path>`. Two of its lessons were confirmed
> independently by Yu'lon on the same day (the `docker logs --tail` ready-marker miss, and
> `docker attach` refusing a non-TTY stdin), which is the main reason to trust the rest. A third
> was confirmed by measurement on 2026-08-23: §2's `stop_grace_period: 5m`. Against a populated
> server (1980 characters online) the worldserver took 90.7 s, 73.4 s and 58.3 s to shut down
> across three runs, and Docker's 10 s default SIGKILLed it, so Yu'lon's `STOP_GRACE_SECONDS` is
> 300 (`pylauncher/yulon/docker.py:741`).

---

Source of truth: `crates/dml-wow/src/{install_native,composegen,preflight,buildcap,soap,srp6,account_write,soap_autosetup,migrate}.rs`,
`crates/dml-core/src/{engine,compose}.rs`, `crates/dml-wow/data/*.tmpl`.

## 1. Staged, resumable install

`STAGE_ORDER` (install_native.rs:144-186), recorded by NAME not ordinal so reordering can never
re-interpret an existing install:

    preflight, guard, clone-core, clone-module, generate-compose, build, up, ready

State file `.dml-install.json` in the title dir: `{version, id, install_id, completed[], last_error, updated_unix}`.
`install_id` = FNV-1a of the title dir's ABSOLUTE path; a state file copied elsewhere is refused.

Rules that cost someone an evening each:
- `preflight` and `guard` never record completion — "a guard that a resume skips is not a guard".
- The state file is a HINT; every stage re-checks disk evidence (clone → `.git` + `git remote get-url origin`;
  build → `docker compose images -q` non-empty). An `is_done` short-circuit once let a dropped-in state file
  make generate-compose rewrite a real server's compose file and orphan its character volumes.
- Failure mid-stage records nothing (the stage re-runs); `last_error` is only written into a dir that already
  has content, or the state file itself becomes the non-empty dir that blocks the retry.
- Resume rests on the state file + BuildKit's layer cache; it is not process suspension.

Streaming a 2-4 h build:
- Probes bounded at 30 s (a wedged dockerd accepts the socket and never answers); build/clone/up UNBOUNDED.
- Bounded probes must DRAIN pipes — `try_wait` without reading deadlocked on a big `docker logs`.
- Real percentage from ninja's `[1803/1808]`, requiring the preceding token to be elapsed-seconds so BuildKit
  stage headers (`[ac-... 2/4]`) cannot jump the bar; largest denominator wins across parallel images; never
  decreases; emit only on change.
- `ready`: poll 10 s up to 1800 s; `docker inspect -f '{{.State.StartedAt}}|{{.RestartCount}}'` then
  `docker logs --since <StartedAt>` — **never `--tail`**: the marker prints once and scrolls out of any tail
  window on a busy playerbots boot. (Yu'lon independently hit this in the bash installer on 2026-08-21.)
- Clones retry 3x for transport resets; core cloned FULL depth (`genrev.cmake` reads history), module `--depth 1`.

## 2. Compose generation (three files + a merged .env)

| file | holds | why |
|---|---|---|
| `docker-compose.yml` | services, container names, image tags, ports, binds, `name:` | identity |
| `docker-compose.override.yml` | ONLY runtime `AC_*` env + `./modules` mount | auto-loaded AND rewritten by the config system (its YAML writer drops comments) — nothing structural may live here |
| `docker-compose.build.yml` | ONLY `build:` blocks (`dockerfile: apps/docker/Dockerfile`, per-service `target:`) | NEVER auto-loaded, so a post-install `up` can never trigger a multi-hour rebuild |
| `.env` | merged, only non-default keys | shared with compose interpolation and the DB reader |

**The trap:** a bare `docker compose build` in that directory builds NOTHING and exits 0. Build calls must pass
`-f base -f override -f build` (naming any `-f` disables auto-loading, so base+override must be listed too);
`up` stays bare. Resolve the `-f` set from disk evidence (buildcap.rs).

Identity: project `dml-<slug>-<install_id>`, image tag `native-<install_id>`, image prefix `dml.local/` (the dot
makes Docker treat it as a registry host that can never resolve to Docker Hub). Fixed names caused real
cross-title bugs: compose keys containers AND named volumes by project, so two dirs sharing a name = one stack.

Template facts: MySQL published on 127.0.0.1 only; SOAP loopback-pinned via the interpolation DEFAULT
(`${DOCKER_SOAP_EXTERNAL_PORT:-127.0.0.1:7878}:7878`); auth port deliberately unpinned; worldserver gets
`stdin_open: true, tty: true, stop_grace_period: 5m`; compose CONCATENATES `ports:` across files (never add a
port in the override); the client-data init uses resumable `curl --retry 30 --continue-at -` + `unzip -t`
because upstream's downloader truncates and restarts at byte zero (died at 66 MB of 1140 MB on a flaky link).
Omitting `dockerfile:` killed the first real build after five green stages — every unit test used a fake docker.

## 3. Preflight — refuse, don't warn

"A false refusal costs one `--allow-underspec` flag; a false pass costs an evening."

- RAM: 2 GB per compiler job (measured); refuse < 6 GB, warn < 8 GB. Below 6 the OOM killer SIGKILLs a compiler
  and the symptom is "dies at the same low % every retry" with a bare `Killed`.
- Docker data root: refuse < 40 GB, warn < 60 GB. Games dir: refuse < 8 GB, warn < 15 GB (checkout 2.4 GB but
  clone PEAK ~3.7 GB). **Same volume → the floors ADD** (48/75 GB).
- CPU advisory: upstream's Dockerfile hardcodes `-j $(nproc+1)` inside the RUN, so no build-arg can change it —
  warn when `ncpu+1 > floor(mem/2GB)` and name the exact CPU count to set in Docker Desktop.
- Tri-state discipline: a stopped Docker Desktop PRINTS ZEROES, so anything reading MemTotal without confirming
  the engine spoke refuses with a fabricated "0 GB". Unreadable measurement = warn saying "unchecked — that is
  not a pass", never a pass and never a refusal. Docker-unreachable / git-missing are non-overridable refusals.
- Data root on Docker Desktop is `/var/lib/docker` INSIDE the VM — measuring it answers for the wrong drive.
  Resolve from Docker Desktop's settings JSON (`DataFolder`/`dataFolder`/`diskPath`, absent = default) instead.
- Preflight STARTS the engine (`docker desktop start -d`, no dashboard popup) rather than refusing over a
  stopped one, then re-gathers facts.

## 4. Windows / Docker Desktop specifics

- `docker.exe` discovery: `DML_DOCKER` override → `%LOCALAPPDATA%\Programs\DockerDesktop\resources\bin\docker.exe`
  (default per-user install, NOT on machine PATH — the whole reason discovery exists) → `%ProgramFiles%\Docker\...`
  → bare `docker`. Same shape for `Docker Desktop.exe` but with NO bare-name fallback. Git likewise
  (`C:\Program Files\Git\cmd\git.exe`) because Git for Windows can install without touching PATH.
- `git clone --config core.autocrlf=input` (written into the NEW repo, so later pulls keep it) — a CRLF checkout
  hands broken shell scripts to the Linux build container.
- `git -c http.version=HTTP/1.1` on the clone only: a 1.3 GB clone over HTTP/2 dies with `curl 92 CANCEL (err 8)`
  presenting as `early EOF` (killed a real install at 9%).
- Canonicalize paths: the same dir arrives as `C:\Users\x`, `C:/Users/x`, `/c/Users/x`, `/mnt/c/Users/x`.
- Strip CR from anything crossing `wsl.exe` — `DML_SOAP_PASS=hunter2\r` authenticates as `hunter2\r`.
- Container names are global PER ENGINE: ownership = compose project-name match **or** working-dir label match
  (project name alone once refused the user's own server). A `docker ps` that cannot answer warns and proceeds.
- Port probe: refuse only on `AddrInUse` (Hyper-V/WSL reserved ranges and permission errors would hard-refuse a
  server that would have started). `compose down -t 180`.
- Spawn subprocesses with CREATE_NO_WINDOW or consoles flash over the UI.
- **macOS has NO counterpart in the Rust code** — `/Applications/Docker.app/Contents/Resources/bin/docker`,
  Homebrew paths, and `~/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw` must be written fresh.

## 5. First GM account with no console (lifted into `controller_wow_wotlk/accounts.py`, `859541e6`)

A fresh AzerothCore has zero accounts; SOAP needs a GM-3 account; **SOAP cannot create the account SOAP needs**;
and `docker attach` refuses piped stdin against a TTY container (and never returns without the tty). The
resolved answer is a direct SRP6 row write — the project's third sanctioned MySQL write.

Order: (1) SOAP enabled at install time via the override (`AC_SOAP_ENABLED=1`, `AC_SOAP_IP=0.0.0.0`,
`AC_SOAP_PORT=7878`; host side loopback-pinned by the port mapping); (2) world ready; (3) probe with
`server info` — act ONLY on an authenticated rejection, never on "unreachable" (a booting server is evidence of
nothing); (4) create via direct MySQL, user `dmlsoap`, 16 chars from the 70-symbol set AzerothCore's validator
accepts (its `{4,16}` is a CEILING — a 32-char "stronger" password is refused), rejection-sampled to avoid
modulo bias; (5) INSERT-only, refuse if the name exists, two rows — `account(username, salt, verifier)` then
`account_access(id, gmlevel=3, RealmID=-1)`; read the id back BY USERNAME, not `LAST_INSERT_ID` (pooled
connections); (6) **verify a real SOAP round-trip BEFORE persisting creds** — writing first reproduces exactly
the failure the step exists to prevent; (7) collisions: check the `dmlsoap_%` family (LIKE with `ESCAPE '!'` —
`\` breaks under `NO_BACKSLASH_ESCAPES`) or you insert one more GM account per tick, forever; (8) it is a state
machine (`Idle → Pending{user,pass,tries} → Done`): create-succeeded/verify-failed must RE-VERIFY, never
re-create.

SRP6 (srp6.rs): `x = SHA1(salt || SHA1(UPPER(user) ":" UPPER(pass)))`, `v = g^x mod N`, `g = 7`, 32-byte
protocol `N`, **little-endian throughout**, verifier zero-padded to exactly 32 bytes. Each of those four details
silently produces a well-formed verifier that never authenticates. A fifth, found when this was
written in Python: `UPPER` is AzerothCore's `Utf8ToUpperOnlyLatin`, which uppercases ASCII `a-z`
and nothing else — `str.upper()` also folds `é`, `ß` and Cyrillic, so the two diverge on any
non-ASCII password (`controller_wow_wotlk/accounts.py`'s `fold()`; its `Café1234` vector comes
out `78719A8A…` under `str.upper()`). Prove it in Python against one row
AzerothCore itself wrote.

## 6. What does not transfer

`migrate.rs` as a feature (no WSL-era servers in Yu'lon — but its lessons do), everything pacman/systemd/Steam
from the bash lineage, Tauri/NDJSON plumbing (Yu'lon has runner.py + LogPanel), byte-parity with `20-soap.sh`,
Rust mechanics (FNV-1a by hand, `GetDiskFreeSpaceExW` → `shutil.disk_usage`), and the pinned commit SHAs — the
POLICY transfers (pin only what a real end-to-end build proved; verify HEAD after checkout), the SHAs do not.
Note the Rust clones plain `azerothcore/azerothcore-wotlk`; Yu'lon's catalog names the `mod-playerbots` fork —
reconcile before pinning.

---

## 7. Waiting for Phase 8 (feature parity), not Phase 6

The same workspace already implements most of what Phase 8 lists, each with its own recorded
lessons. Worth reading when that phase opens, in rough order of value:

- **Backups** — **not waiting: shipped in Phase 6** as `controller_wow_wotlk/maintenance.py`
  (`da5e024d`, proven against a real server at `3c946825`), covering backup and restore both.
  Still worth reading against it: `backup.rs` (streamed `create`, plus
  `list`/`validate`/`delete`) and `restore.rs`
  (the one sanctioned whole-DB overwrite path, with a safety net taken first).
- **Module tuning** — `registry.rs` (embedded static registries) + `tuning.rs`: curated switches
  first, then every key the module's own `.conf.dist` knows, with the author's comments inline.
- **Server status as one composite** — `status.rs` builds containers + SOAP `server info` + bots +
  ports into a single verdict for the home card; `logsnap.rs` snapshots the worldserver log before
  every stop/restart, because `compose down` recreates containers and a recreated container starts
  with an empty log (that destroyed the evidence twice during a real incident).
- **Module lifecycle** — `modmgr.rs` (install/update/remove) and `moduletail.rs` (commits-behind
  check → per-module update → rebuild-required flag, conf-activate, place-npc, client-patch).
- **Quality-of-life** — all five live under `launcher/src-tauri/src/`, not the `crates/` workspace
  the rest of this page cites: `watch.rs` (auto-stop when the game exits, as a pure state machine
  so it is
  exhaustively testable), `power.rs` (keep-awake while the server is up — partly lifted already as
  `platform.keep_awake()`: macOS and Windows, a no-op on Linux), `single_instance.rs` (a
  dependency-free guard: binding a fixed loopback port is atomic), `realmlist.rs` (checks BOTH
  `realmlist.wtf` and the `Config.wtf` `SET realmList` fallback the client actually uses),
  `autostart.rs`.
- **The Lab features themselves** — `party.rs`/`party_specs.rs` (bot party + presets),
  `pages.rs`/`paperdoll.rs`/`stats.rs` (character, gear, statistics reads), `iteminfo.rs` (item
  lookup + tooltip cache), `botid.rs` (the single answer to "is this account a playerbot?", which
  exists because every check in the repo used to ask it differently).

Explicitly **not** worth porting: the WSL-distro provisioning lineage, `migrate.rs`'s import path
(Yu'lon has no WSL-era servers to import), the Tauri/NDJSON plumbing, and the `dml-wow-cli` JSON
envelope layer — Yu'lon is already in-process with its own streaming conventions.
