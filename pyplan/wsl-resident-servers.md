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
restore, modules. Yu'lon is replacing the DML Launcher, not sharing a server
with it, and a half-owned server ("you may start it but not repair it") is a
worse product than either owning it or refusing it.

---

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
