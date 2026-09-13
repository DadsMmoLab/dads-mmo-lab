# T56 — preflight asks whether Docker answers, never whether Compose exists

**Status:** FIXED 2026-09-13.
**Filed:** by the lead, from NaniWat's report on 0.8.65-Public, relayed by the owner.
**Branch:** `fix/preflight-checks-for-compose`, from `upstream/Yulon`.
**Related:** T57 is the FIRST error the same user hit; this is the second.

## What the user saw

A Steam Deck owner installed Docker by hand (our own installer had failed — that is T57), then
started the install. It ran to **step 4 of 9** and died:

```
FAILED: InstallerError: the build failed (exit 125). Its last words were:
unknown shorthand flag: 'f' in -f / Usage: docker [OPTIONS] COMMAND [ARG...]
Step 4 of 9 · build
```

## The mechanism

Yu'lon builds with `docker compose -f <file> build` (`docker.py:3493`). Without the Compose v2
plugin, `compose` is not a command, so `-f` falls through to **`docker` itself** — which is why
the message ends in Docker's own top-level usage text and names neither Compose nor the file.
Exit 125 is the CLI's usage error.

`_docker_check()` asks one thing: `facts.docker_ready`, i.e. did the **daemon** answer. It did.
His engine was fine. The plugin is a separate package and nothing ever looked for it, so a
machine that cannot run a single build of ours passed the gate and failed four steps in.

## The fix

`platform.compose_ready()` probes `docker compose version`, shaped exactly like
`docker_ready()` — same candidate list, same shared budget, same "cannot start it at all is not
an answer". `gather()` asks it only when the daemon answered, and `_compose_check()` is its own
row.

Four decisions, each with a mutation:

1. **Its own row, not a clause inside `_docker_check()`.** The two fail independently and share
   no remedy: a dead daemon is *started*, a missing plugin is *installed*.
2. **Probes the plugin, not the daemon** (M1). `docker info` passes on his Deck — that is
   exactly why nothing caught this.
3. **Refuses rather than warns** (M2). A warning still lets a nine-step install start.
4. **The remedy names THIS machine's package manager** (M3). "Install the Compose plugin" is
   not an instruction anyone can follow, and pointing a Steam Deck at Docker Desktop is T40's
   mistake — which the same user had already hit once in `_docker_check()`'s own remedy.

What he would get now, before anything is written:

```
[refuse] Docker Compose: Docker is running, but the Docker Compose plugin is missing
  → Install Docker Compose v2 and try again. On Arch or SteamOS:
    `sudo pacman -S docker-compose`. On Debian or Ubuntu:
    `sudo apt install docker-compose-plugin`. Check it with `docker compose version`
    — note the SPACE: `docker-compose` with a hyphen is the old v1 and is not what
    Yu'lon runs.
```

## Evidence

Gate `4371 passed, 8 skipped`. ruff, black, mypy clean on `linux`, `win32`, `darwin`.
Three mutations, three killed (`pyplan/gates/t56-compose-preflight-2026-09-13/mutations/`).

**The suite's own docker guard did real work here.** Adding the probe made ten `gather()` calls
in `test_preflight.py` shell out to the real CLI, and one more in
`test_families_azerothcore.py` whose comment already enumerates the seams that "have to be
taken" or the test reaches the box's daemon. Every one now fakes `compose_ready` for the same
reason it already faked `docker_ready`.

## Still owed

A press on a real Steam Deck, or any machine with the engine and no plugin. The probe and the
refusal are unit-tested; nobody has yet watched the refusal appear on a machine that genuinely
lacks the plugin.
