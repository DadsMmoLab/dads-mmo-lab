# The "images already built" check can offer to delete a complete build — 2026-08-28

Found on yulon-arch round 3, directly reproduced, cross-checked against `docker images`.
**This one is dangerous mainly in combination with another finding — read to the end.**

## The check
`catalog/installers/wow-wotlk/install-wow-wotlk.sh:1249-1250`:

    if [ -d "$SERVER_DIR" ] && \
       (cd "$SERVER_DIR" && docker compose images 2>/dev/null | grep -qi "worldserver"); then
        print_success "Compiled images already found in $SERVER_DIR"

`docker compose images` reports the images **of that project's existing containers**. It does not
query the image store. With no containers for the project, it prints nothing — regardless of how many
images are built.

## What happened
`docker compose up -d --build` failed on a container-name collision (see below), so zero containers
existed for the `yulon-arch-fresh` project. The fast path therefore saw nothing and the script
announced *"Existing folder found... (no compiled images present) — Remove it and start fresh?"* —
**on a folder holding a complete, successful ~35-minute build.** Cross-checked directly:
`docker images` showed freshly built `acore/ac-wotlk-worldserver:master`, `-authserver`,
`-db-import`, `-client-data`.

The block's own comment states the purpose the bug defeats: this branch "is how a user recovers an
install whose labels were reset — by `restorecon -R ~`, a selinux-policy update, or `/.autorelabel` —
and it is the one that must not send them back to a 2-4 hour rebuild."

Note also `dir_is_reusable()` (`:160-165`) returns true only for an **empty** directory
(`find -maxdepth 0 -empty`), so any populated folder falls to the "start fresh?" branch.

## Why this is the dangerous one — the interaction
Taken alone this is currently harmless, because `PROMPT_RULES` (`catalog/installer.py:180`) answers
**"n"** to "Remove it and start fresh?" — nothing sets `InstallOptions.reinstall`. So today the script
exits 0 and does nothing (which is its own HIGH bug — see
[[finding-interrupted-install-recorded-as-success]]).

**The obvious fix for that bug is to set `reinstall=True`. Doing that turns this bug from harmless
into destructive:** the answer becomes "y", and a folder holding a complete build gets deleted and
recompiled for 2-4 hours, with no way to say no.

So these two must be fixed together, and in the right order:
1. Make the "are the images built?" question reliable — ask the image store (`docker images`) rather
   than `docker compose images`, which is a question about containers.
2. Only then consider anything that lets the answer to "start fresh?" be "y".

Doing (2) before (1) is a regression that costs users hours of compile time and their existing build.

## Second finding from the same episode: container names are global, not project-scoped
`docker compose up -d --build` failed with
`Conflict... container name "/ac-database" is already in use`. The pre-existing (stopped, not removed)
`wow-server-playerbots` stack holds `ac-database`/`ac-authserver`/`ac-worldserver` as **fixed literal
names** — `catalog.json`'s `containers` section names them literally rather than deriving them from
the compose project. Consequences: two installs of the same game cannot coexist on one machine, and a
leftover stopped stack blocks a fresh install with a Docker error the launcher does not explain.
Workaround used: `docker rm` on the five stopped containers (no `-v`; volumes for both projects
confirmed intact afterwards). Project-scoped names would fix it, but that is a design decision —
`catalog.json`'s `containers` map is how the whole controller finds its containers.

## Also from this run: the compile itself was clean
1829/1829 objects at `-j6`, **peak 4.3 GB of 23 GB, swap 0 MB at every sample**. Confirms `-j9` would
also have been fine at this size; the cap only bought margin. Contrast yulon-fedora at 8 GB, which
swap-thrashed itself unreachable.

## Fresh-boot 500-bot verification (three independent ways)
- `docker inspect ac-worldserver` env → `AC_AI_PLAYERBOT_MIN_RANDOM_BOTS=500`, `MAX=500`
- worldserver log → exactly 500 "logged in" lines, last `500/500 Bot Lilealaes logged in`;
  world initialized in 1m54s, then the `(worldserver-daemon) ready...` line
- DB → `SELECT COUNT(*) FROM acore_characters.characters WHERE online=1` → **500**
  (1000 total character rows — the bot account pool exceeds the concurrent online count, which is
  expected Playerbots behaviour, not a discrepancy)
