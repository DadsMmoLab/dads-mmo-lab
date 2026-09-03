# A slow Docker daemon wedges the Server tab permanently — 2026-08-28

Severity: **HIGH**, and the most user-visible defect found in the run. Reproduced live on
yulon-win11 with an 8+ minute hang; the same condition was hit independently in round 1
(a 125-second `docker info`).

## The mechanism, verified in the source
1. `docker.status()` (`yulon/docker.py:1615-1626`) runs `_run(["ps", "--format", "{{.Names}}"])`.
2. `_run()` (`docker.py:217-229`) takes **no timeout parameter at all** and delegates to `_docker()`
   → `runner.run()`. There is no deadline anywhere on this path; a py-spy trace from a separate box
   shows it parked in `subprocess._communicate`.
3. `ControllerView.refresh_status()` (`ui/controller_view.py:445-456`) does:

       if self._status_pending:
           return  # a poll is already in flight; never queue them up
       self._status_pending = True
       self._run(self.services.controller.status, self._status_ready, self._status_failed)

   `_status_pending` is cleared only by `_status_ready` / `_status_failed`.

**If `docker ps` never returns, neither callback ever fires, so `_status_pending` is never cleared,
so every subsequent 5-second poll returns immediately at the guard.** The Server tab's "status:"
label stays `unknown` forever. Not slow — permanently wedged, with no error, no timeout, and no way
back short of restarting the app.

The guard itself is correct and well-reasoned ("never queue them up"). The defect is that it has no
counterpart for the call that never completes.

## Why this is realistic, not exotic
A hung or slow Docker daemon is the normal failure mode of Docker Desktop on Windows under memory
pressure — this project has now hit it twice on the same box, at 8 GB and again at 23 GB. It is also
what a user sees while Docker Desktop is still starting up.

## What a fix has to cover (not applied — hunt-only)
A timeout on the `docker ps` path is necessary but not sufficient: `_status_pending` must also be
cleared on *any* terminal outcome, including one that never produces either callback. Both halves,
or the tab still wedges on the next unanticipated failure.

---

# The "Find in WSL…" button is offered on every Windows box with Docker Desktop

`platform.wsl_distros()` returns `('docker-desktop',)` on a box with **no real WSL distro** — Docker
Desktop registers its own backend distro and nothing filters it out.

`ui/catalog_view.py:263-267` gates the button on that raw list, and its comment states the reasoning
explicitly:

    # Only where a WSL-resident server can exist. On Linux, macOS, and on a
    # Windows box with no distros, this button would be an offer the machine
    # cannot honour - and `wsl_distros()` answers () for all of them, so the
    # one check covers every case.

**That claim is false in the most common Windows configuration.** Any Windows box with Docker
Desktop — i.e. every box that can run this app at all — has a non-empty list, so the button appears
on **every catalog tile**, including Linux-only games that cannot be installed on Windows at all.
Clicking it always ends in "No servers found in WSL", because
`wsl -d docker-desktop -- docker compose ls` is explicitly unsupported by Docker.

**And a test pins the buggy value.** `tests/test_platform.py:526` asserts
`platform.wsl_distros() == ("dml-arch", "docker-desktop")`. The test's actual subject is UTF-16
decoding, which is legitimate — but its expected value bakes in the missing filter, so adding the
filter breaks a green test and looks like a regression.

Fix would need: filter `docker-desktop`/`docker-desktop-data` in `wsl_distros()` (or at the gate),
and update that test's expectation with a comment saying why.
