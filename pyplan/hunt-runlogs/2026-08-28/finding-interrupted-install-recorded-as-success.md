# An interrupted install retried silently reports success — and the 2026-08-23 guard misses it

Severity: HIGH. Known and documented in the codebase; the guard added for it does not cover the
case a real interruption produces. Reproduced live on yulon-arch, round 3, 2026-08-28.

## The mechanism
1. `catalog/installers/wow-wotlk/install-wow-wotlk.sh:1273-1281` — when `$SERVER_DIR` exists, is
   non-empty, and has no compiled images, the script asks **"Remove it and start fresh?"**.
2. `catalog/installer.py:180` — `PROMPT_RULES` answers `"y" if o.reinstall else "n"`.
3. Nothing sets `reinstall=True` from the GUI — `ui/catalog_view.py:503` says so in a source comment.
4. So the answer is "n", the script prints "Keeping existing install — exiting." and **exits 0**.
5. `catalog_view.py` reads exit 0 as success, pins a compose project name into the folder, and grows
   a tab for a server that was never built. `cancelled_install_message()`'s docstring already spells
   this out, and notes the pin "is the part with teeth": `docker.py` records that an install-time pin
   is inherited by any copy of the folder, so Stop in a copy can stop the original's server.

## Why the existing guard does not save it
`catalog_view.py:497-510` (added by the 2026-08-23 review) downgrades a clean exit to a failure
**when `compose_file(server_dir) is None`**. But `compose_file()` (`installer.py:67-83`) only tests
whether a compose YAML exists in the folder — nothing about build state or images.

**The AzerothCore source tree contains `docker-compose.yml` at its root.** So the moment the clone
stage completes — long before anything is compiled — `compose_file()` returns non-None and the guard
stops firing. It closes the "empty or never-cloned folder" case and leaves open exactly the case a
real interruption produces: **source cloned, nothing built, retry no-ops, recorded as done.**

The guard's own comment explains the choice: "the compose file is the single thing every install of
every game has". That is true, and it is also the single thing a bare *clone* has — which is the flaw.

## Reproduced
An uncapped `-j9` build was killed ~1 minute in, leaving a complete clone. Relaunching the installer
against that folder exited 0 with "install finished" logged and nothing rebuilt. Recovery required
`rm -rf` of the directory. No operator-visible error at any point; only diffing the log against
expectations reveals it.

## What would actually close it
Roadmap 6.5 item 1 — a staged, resumable install — is the named, unbuilt work. Short of that, the
app would need to ask the question the script already asks itself (`dir_is_reusable`: are the
compiled images present?) rather than the proxy question of whether a YAML file exists. That is a
design decision, not a one-line fix: once installs are resumable, a source-only folder becomes a
legitimate mid-install state rather than a failure, so what counts as "installed" has to be decided
before the check is changed.

Related pattern: [[reviews-check-functions-not-call-sites]]. Here the fix is reachable and does run —
it just tests a proxy that becomes true too early to catch the case it was written for.
