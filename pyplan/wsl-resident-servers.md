# WSL-resident servers

How Yu'lon manages a WoW server that lives **inside a WSL2 distro**, with its own
Docker CE, rather than on the Windows side through Docker Desktop.

Written 2026-08-26, after a spike against a real WSL-resident server. Every
mechanism below was measured rather than assumed; the measurements are quoted
where they decide something.

---

## Why

A tester reported a WotLK server installed by the DML Launcher living at
`\\wsl.localhost\dml-arch\home\dml\games\wow-server-playerbots`, with Docker CE
running *inside* `dml-arch`. Yu'lon could not manage it, and "Use existing…"
accepted the folder anyway and would have failed later — fixed separately as the
honest refusal (`fix/attach-folder-rule`).

Refusing clearly is not the end state. **Yu'lon is intended to replace the DML
Launcher**, so a topology a large part of the existing user base already runs
cannot stay unsupported: adopting those servers is the migration path off the
old tool, and a multi-hour compile is not something to ask a user to repeat.

## What this is not

Creating distros, or installing a server into one. The seam below is where that
would plug in — §7 names it — but nothing here provisions anything. Installing
remains the Windows-side or DML-Launcher path until a separate design says
otherwise.

---

## 1. The seam: a prefix, not a program

`platform.docker_program()` answers "what is the docker CLI called here" with a
name. That is the wrong shape for a docker that is not local. It becomes a
**prefix** — the argv that gets you to a docker daemon:

```python
docker_prefix(None)         -> ["docker"]
docker_prefix("dml-arch")   -> ["wsl", "-d", "dml-arch", "--", "docker"]
```

Callers splice rather than prepend a name. Four places build docker argv today
and each takes the prefix:

| file | what it runs |
|---|---|
| `docker.py::_docker()` | the buffered seam — 20 call sites behind one function |
| `docker.py::run_attached()` / `follow_logs()` | the **streaming** seam — image builds, one-shots, the Console log |
| `apply.py` | `docker exec … mysql` (the SQL runner) |
| `maintenance.py` | `docker exec` (backup and restore) |
| `console.py` | `docker attach` (GM console, POSIX-only today) |

**There are two seams in `docker.py`, not one**, and the first version of this
document said otherwise. `_docker()` buffers a run into a string; `run_attached()`
and `follow_logs()` stream it, and they resolve the CLI themselves rather than
going through `_docker`. A completeness test rooted at `_docker` blessed both,
so the Console's log stream and `build_staged()` shipped unable to name a daemon
at all. The test is now rooted at every function that asks platform *how* to
reach docker, whichever seam it then uses.

38 functions end up taking `wsl_distro`. Accepting it is not the same as
forwarding it, which is the subject of §4a.

The prefix is a pure function of its arguments, so it is table-tested and
cannot drift per platform.

**The local docker CLI is not consulted for a WSL install.** The machine that
prompted this work has no Docker Desktop at all - the daemon lives inside the
distro - so resolving `docker.exe` first would refuse a perfectly good server
for the absence of something it never uses.

### Where the distro comes from

`state.KnownInstall` gains one optional field:

```python
wsl_distro: str | None = None
```

Optional with a default, so every `state.json` written before this change still
loads. It sits beside `server_dir` because it is the same kind of fact: *where
this server is*. Nothing infers it at runtime — an install either was adopted
from a distro or was not.

### cwd

`_docker()` passes `cwd=server_dir` today. A Windows process cannot usefully
`cd` into a WSL path, so for a WSL install the location moves **out of the
process and into the argv**:

```
wsl -d dml-arch --cd /home/dml/games/wow-server-playerbots -- docker compose ps
```

`wsl --cd`, not compose's `--project-directory`. The spike measured both, and
the first draft of this document chose `--project-directory`; building it showed
that was wrong, because `_docker()` runs **every** docker subcommand and only
compose understands that flag. `docker exec`, `docker ps` and `docker inspect`
all need the same treatment and none of them would have taken it.

**`--cd` is an argument to `wsl.exe` and must precede the `--` separator.**
Everything after `--` is the command line handed to the distro's shell, so

```
wsl -d dml-arch -- --cd /home/... docker compose ps    # WRONG
```

reaches bash, which answers `--: invalid option`. This is placed by
`docker_prefix()` rather than by callers, so there is one place to get it right.
It is worth recording how it was found: a unit test asserted that `--cd` was
present and that the path followed it - both true of a command that could not
run - and it passed. A live run against a real distro failed immediately. The
test now asserts the flag's position relative to `--`.

---

## 2. Discovery: ask Docker, not the filesystem

Docker already knows where every compose project is. Measured inside `dml-arch`:

```
$ wsl -d dml-arch -- docker compose ls --all --format json
name='wow-server-playerbots'  status='running(1)'
configs='/home/dml/games/wow-server-playerbots/docker-compose.yml,
         /home/dml/games/wow-server-playerbots/docker-compose.override.yml'
```

That is the whole of discovery: project name, state, and the exact config paths.

**This is what keeps Yu'lon uncoupled from the DML Launcher.** No scanning for
`~/games/*`, no parsing another product's folder conventions, nothing that
breaks when that product reorganises. Docker is the authority on where its own
projects are, and Docker is what we ask.

### The flow

1. `wsl -l -v`, decoded UTF-16LE, for the distros and their state.
2. For each **running** distro, `docker compose ls --all --format json`.
3. Present what was found: distro, project name, status, path.
4. Adopting one writes a normal `KnownInstall` with `wsl_distro` set.

### Stopped distros are not probed

Measured: `wsl -d docker-desktop -- true` flipped that distro from `Stopped` to
`Running`. So probing every distro to see what is inside it **boots them all** —
slow, and a side effect nobody asked for by opening a dialog.

Stopped distros are therefore listed but not probed, each with an explicit
opt-in that says starting it is what will happen.

**Polling obeys the same rule**, and did not at first. The Server tab refreshes
every five seconds, so once `Controller.status()` correctly ran `wsl -d …` it
booted an adopted server's distro simply by opening the app — the rule written
for discovery, broken by the poll. `status()` now asks `wsl.is_running()` first
and reports nothing up when the distro is down, which is true rather than merely
convenient. Start still starts it, because that is something the user asked for. A user who knows their server
is in a stopped distro can still reach it in one click; a user who does not is
not made to wait for distros they do not care about.

### A distro lives only while a session is attached (T132)

Measured on `yulon-win11` (WSL 2.7.12, 2026-09-26): a distro stops **15-25 s after the
last `wsl.exe` attached to it exits**. Neither systemd, nor a running `dockerd`, nor running
containers keep it up, and Docker Desktop running beside it (its own distro, same VM)
changes nothing. `[wsl2] vmIdleTimeout` is about the VM, not the distro; `[general]
instanceIdleTimeout=-1` does keep a distro up, but for every distro of that user, and only
after a `wsl --shutdown`.

Start is one short `wsl -d … docker compose up -d`, so before T132 a WSL server lived only
while the Server tab's five-second poll kept calling in, and closing the app killed the world
and the database 25 s later. Now `wsl.hold()` keeps one detached `wsl.exe -d <distro> --exec
sh -c "… exec flock -n … sleep infinity"` alive, keyed by the world container's name. Its lock
and pid file live in `/dev/shm/yulon-<uid>/` (tmpfs, mode 0700, refused if it is a symlink or
not the caller's), so the next launch finds it without remembering it. Three things make a hold:

* `Controller.start()`, after the containers are up;
* `Controller.status()`, the first time a poll that already found the distro running sees
  the world up (a world started by hand, before Yu'lon opened, or by `unless-stopped`) and
  again if the hold it made has exited; a hold that never took is not retried per poll;
* the flock makes every repeat a no-op (exit 75).

`stop()`/`remove()` end it with `wsl.release()` when something of this install went down, and
`stop_conflicting()` releases every container name it stopped, since the other install's
key is one of them. `release()` kills the recorded pid only if it is still a `sleep` with
the start time recorded beside it (`/proc/<pid>/stat` field 22), so a recycled pid is left
alone, and it never asks a distro WSL says is stopped. A world stopped outside Yu'lon keeps
its hold until the next Stop/Remove, `wsl --shutdown` or sign-out.

Still open, measured the same night: **reading anything under `\\wsl.localhost\<distro>`
from the user's desktop session starts that distro** (1.35 s, then running). The Server
tab reads the install's files at open, so opening the app boots an adopted server's distro,
which is exactly what the poll rule above exists to prevent. A killed (not stopped) server's
containers then come back on their own through `restart: unless-stopped`.

---

## 4a. Accepting a parameter is not passing one

Two tests, at two levels, because each is blind to the other's failure.

`test_every_function_that_talks_to_docker_can_say_which_daemon` parses
`docker.py` and proves every function reaching either seam **accepts**
`wsl_distro`. It says nothing about whether a caller supplies one.

`test_every_controller_call_says_which_daemon_it_means` parses `Controller` and
proves every `docker.<fn>(...)` call whose target accepts `wsl_distro`
**passes** it. That gap was not hypothetical: the first version threaded all 33
functions and then forwarded the distro from two of the Controller's eight call
sites, so Start, Stop, Remove, Status, port_conflicts and repair_import all
addressed the local daemon — on a machine that has no local daemon.

The same shape bit twice more. `DockerSql._env()` called `mysql_env()` without
the distro while the WSLENV test called `mysql_env()` directly and passed; and
`adopt_from_wsl()` shipped with a signal, persistence and tests, and no button.
Test the path, not the piece.

## 3. Adopt once, then own

Adoption reads the distro, the project and its paths **at import time only**.
From then on the install is an ordinary `KnownInstall` and Yu'lon manages it
through its own seams.

Full control after adoption — start, stop, teardown, repair, accounts, backup,
restore, modules. A half-owned server ("you may start it but not repair it")
would be a worse product than either owning it or refusing it.

**This is settled, not assumed.** An adversarial review raised coexistence as an
open risk: the DML Launcher may still be installed and managing the same
containers, and nothing here detects that. The project owner's answer
(2026-08-26) is that **Yu'lon is taking over everything** — the launcher is
being retired rather than run alongside. So there is no ownership-transfer
protocol to design, because there is no second owner to negotiate with; what
remains is a migration, and adopting the servers people already have is what
makes that migration possible without a multi-hour rebuild.

Two consequences worth stating, because they follow from that decision rather
than from this design:

* **WSL-resident is a first-class topology, not a compatibility shim.** The same
  review suggested treating it as a bounded layer with an exit policy. That
  would be right if the launcher were staying and this were a bridge; it is not.
  The servers are where they are, and Yu'lon owns them there.
* **Installing into a distro becomes a real question later.** It is still out of
  scope here (§7), but "Yu'lon takes over everything" means the answer is
  eventually yes rather than never, and §7 names the seam it plugs into.


## 4. Two traps, measured

### `WSLENV` is mandatory

`apply.py` runs `docker exec -e MYSQL_PWD` with **no `=value`**, deliberately, so
the password reaches the container through the environment and never appears in
an argv that `ps` can read. Through `wsl.exe` that only works if `WSLENV` names
the variable:

```
without WSLENV : '[]'
with WSLENV    : '[from-windows]'
```

End to end against a real container: `docker exec -e MYSQL_PWD ac-database` →
`rc=0`, `[spike-probe-value]`. So the security property survives, and the failure
mode of forgetting it is an **empty password**, which surfaces as an
authentication failure rather than as a missing setting. Set it where the
prefix is built, not at each call site, so a new caller cannot forget.

### `wsl -l` output is UTF-16LE

```
raw    : b'd\x00m\x00l\x00-\x00a\x00r\x00c\x00h\x00\r\x00\n\x00d\x00o\x00c\x00k…'
utf-8  : ['d\x00m\x00l\x00-\x00a\x00r\x00c\x00h\x00', '\x00', …]   <- garbage
utf-16 : ['dml-arch', 'docker-desktop']
```

Decoding is in one function, tested with those exact captured bytes. This has
already cost time twice in one day; a test with real bytes is what stops a third.

---

## 5. What the spike settled

Run against a real WSL-resident server, read-only:

| question | answer |
|---|---|
| exit codes propagate? | **yes** — 7→7, 0→0, 127→127 |
| stdout/stderr separate? | yes |
| child output encoding | plain ASCII, no NUL bytes |
| `--project-directory` | works, `rc=0` |
| `docker compose ps` | `ac-database running` |
| stdin piping | works |
| `docker exec -e VAR` | works, value arrives |
| path translation | `wslpath -w` both ways |

Exit-code fidelity is the load-bearing one: the controller reads exit codes
everywhere, and `wsl.exe` swallowing them would have ended the idea.

---

## 6. Failure modes

| case | behaviour |
|---|---|
| named distro no longer exists | refuse naming it; offer to forget the install |
| distro stopped | `wsl -d` auto-starts it — say so, rather than appearing to hang |
| `wsl.exe` absent (Windows without WSL) | the existing "no docker CLI" sentinel shape, so callers are unchanged |
| Docker inside the distro not running | the existing daemon-down message, naming the distro |
| adopted project's path deleted | the existing "no compose file" refusal, naming the distro |

Nothing here invents a new error channel. Every case maps onto one the callers
already handle, which is what keeps the change to a seam rather than a rewrite.

---

## 6a. Rebuild and update inside a distro (T125)

"Rebuild the server…" and "Update the server to latest…" run for a server that
lives inside a distro, on that distro's Docker. Until T125 both were withheld
(Update hidden, Rebuild refusing by name), because the install engine's
`native.Seams` addressed only the local daemon: a rebuild would have compiled on
Windows' own Docker and left the distro's server on its old build.

**Who it serves.** A server that Yu'lon built on Linux INSIDE the distro and the
Windows app then adopted with "Find in WSL…". A DML-built server has no
`.yulon-install.json` and no Yu'lon compose files, and keeps the engine's own
refusal ("rebuild that one the way it was built"). Installing into a distro is
still out of scope (§7).

**How.**

* `installer_for_app(entry, wsl_distro=)` builds the engine on
  `native.Seams.in_wsl(distro)`: every seam whose default can name a daemon is
  that function with the distro bound; `docker_ready` is
  `docker.daemon_ready(wsl_distro=)` (`docker info` through the prefix, never
  the local CLI); `platform_id` is "linux", because Linux Yu'lon made the
  install and the recipe and compose files it re-renders must be the ones it
  rendered then; SELinux is answered off (the WSL kernel runs none). Seams only
  an INSTALL asks (provisioning, preflight, extraction, conf, the import check)
  refuse rather than default to the local daemon.
* Git runs through `git.ContainerGit(wsl_distro=)`. Only `_argv()` differs: the
  distro's docker prefix, the bind mount in the distro's own spelling (Docker
  Desktop refuses a `\\wsl.localhost\...` bind), and `--user` = the checkout's
  owner (`stat -c %u:%g` inside the distro; refused rather than run as root when
  unknown). Every git subcommand is the same argv, so the pin logic
  (`CloneSpec.rev`, T126's releases, "Return to the tested pin") is unchanged.
* **Two ids, and neither replaces the other.** The Windows-side id --
  `composegen.install_id()` of the `\\wsl.localhost\<distro>\...` spelling,
  lowercased -- keys the command-channel credential, its GM account name,
  dbsecret, altbot memory and the run records, and is left exactly as it was:
  re-hashing it would orphan every WSL install adopted before (and merge two
  distros holding the same Linux path). The distro's images and compose project
  are named after the id Linux Yu'lon RECORDED in `.yulon-install.json`: the WSL
  engine's `Seams.install_id` is `native.recorded_install_id()`, which reads it
  and refuses a missing or malformed one, and every `composegen.image_tag() /
  project_name() / built_image_refs() / render()` the engine calls takes it as
  `install_id=`. Measured on the fixture: recorded `2f1c23d4`, Windows-side
  `27a96c15`.
* **The distro is part of the path.** `platform.wsl_location()` parses the
  distro and the Linux path together, and `wsl_linux_path_in(path, distro)` --
  the one conversion `docker._docker()`, `run_attached()`, `compose_run_stdin()`
  and `ContainerGit` use -- refuses a folder whose UNC path names another distro
  than the one the install is remembered in (compared case-insensitively),
  before anything is read or run. The wiring checks it first of all.
* **§2 for the two readings.** `LatestRoute.source_version` (every tab reload)
  and `upstream_news` (T124, once a day) answer "nothing to say" while
  `wsl.is_running()` says the distro is down: no read of the folder (a UNC read
  boots it, T133) and no git. The presses may start it; the user asked.
* The backup offered before an update is the existing one, which already passes
  `MYSQL_PWD` through `WSLENV` (§4).
* Tests: `tests/test_wsl_update_route.py` -- a Seams completeness test derived
  from `inspect.signature`, and both presses driven end to end with every argv
  recorded at the `runner` level, asserted to start `wsl -d <distro>` (it found
  a host `stat -f` the compose render asked through the `fs_type` seam).

## 7. Where installing would plug in

`docker_prefix()` is the only thing that knows a server can live somewhere other
than here. An install-into-WSL design would add: choosing or creating a distro,
installing Docker CE inside it, and running the existing install engine with the
prefix already set. It needs no change to the 21 lifecycle call sites, because
they take the prefix rather than build it.

---

## 8. Testing

- **Prefix**: pure function, table test over `None` and a distro name.
- **Call sites**: argv assertions at each of the four, the pattern already used
  for the privilege rules — asserting on the emitted command, not on prose.
- **UTF-16 decoding**: the captured bytes above as a fixture.
- **Discovery**: the captured `compose ls` JSON as a fixture, plus the
  stopped-distro rule (a stopped distro must not be probed — asserted through
  the run seam, so it fails if anything shells into it).
- **`WSLENV`**: asserted on the environment the prefix builder produces.
- **Live gate**: the real `dml-arch` server, and the tester's machine.

## 9. Out of scope

- Creating or provisioning distros; installing Docker inside one.
- Installing a server into WSL (§7).
- WSL1. Docker needs WSL2, and the spike box reports WSL1 as unsupported.
- Managing a server over SSH or on another machine. The prefix would allow it;
  nobody has asked for it, and it is not designed for here.
