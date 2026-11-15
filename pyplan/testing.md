# Testing Yu'lon

The rules every change and every worker brief follows. Each rule names the incident that taught
it, so a reader can judge whether a case is the same kind. How to run the suites is in
[contribution.md](contribution.md); this file says what a test must be.

## The rules in brief

1. **Admission.** A test exists only if it names the behavior it protects: a contract a player
   or another module relies on, a bug that happened (its ticket number in the name or docstring),
   or a reader of bytes Yu'lon did not write (a log line, `docker` output, a client file). A test
   that only restates the code it sits beside protects nothing.
2. **It can fail.** Breaking the protected behavior must turn the test red. Break it once and
   watch: every fix report names its mutation and the test it reddened.
3. **Assert the property, not a stand-in.** Assert exactly what the name promises. Not a count
   that a legitimate change will move, and not "appears somewhere in the text".
4. **The arrange builds what the name claims.** A test named for an existing record must write
   that record first. Otherwise it tests something else and passes for the wrong reason.
5. **A test waits for everything it starts.** Every job, thread or timer a test starts has
   delivered its answer before the test ends. `tests/conftest.py` fails any test that leaves one
   behind (T769, T771).
6. **State is reset around every test.** Environment variables, module caches and singletons
   are reset by an autouse fixture in `tests/conftest.py`, not by each test remembering to.
7. **A flaky test is a bug.** It gets a ticket and a cause. One rerun to confirm it is a flake is
   fine; a rerun loop is not.
8. **A hand-kept list is checked against the real set.** When code keeps a hand-written list of
   what it covers (seams it guards, fields it copies, calls it exempts), a test compares that
   list with the real set and names anything unlisted, so a new member needs a decision.
9. **A switch that is off changes nothing.** A feature behind a switch has a test that, with the
   switch off, the output (files written included) is byte-for-byte what it was before.
10. **Concurrency is tested with real overlap.** Two threads and a barrier, so the second really
    runs while the first holds what it holds, not two calls in a row that imitate it.
11. **Requirements fail loudly.** A missing tool or file fails the run. Only what the machine
    cannot have may skip, and the skip says what is missing: a Windows-only path on Linux, or
    `integration` tests with no reachable Docker daemon.
12. **Proportion.** A change adds tests in proportion to the behavior it changes. Its report and
    pull request list the tests added and removed, and the run time of the touched files before
    and after.

## Why each rule is here

| Rule | What happened |
|---|---|
| 3 | A guard asserted "exactly one `networking.apply` call in controller_view.py". T885 legitimately added three more and turned CI red; what it protected was "every such call stays exempt". A render test checked that `user: "0:0"` appeared anywhere, which passed with two of three services wrong (T887). |
| 4 | `test_a_docker_record_without_the_switch_proceeds` never wrote a record: it tested an empty folder (T887 round 2). |
| 5, 7 | `test_stopped_distro_reads` failed three CI runs in a row. A tab job the test never waited for raced its "distro stopped" step; reruns hid it until it was ticketed (T886). |
| 6 | A Podman test left `DOCKER_HOST` set past its end, and nine unrelated tests went red one round later (T887). |
| 8 | The engine guard's completeness test found an unguarded field (`realm_name_current`) on its first run (T887 round 4). |
| 9 | Round 1 of T887 wrote a new key into every install record, switch or not. |
| 10 | A second Apply in the same tab ran beside the first, and only a two-thread test showed it (T885 round 3). |

## Levels and when to run them

| Level | What | When |
|---|---|---|
| Targeted | The test files for the code a change touches, by file or node id | While working, and in every worker report |
| Inner loop | `pytest -m "not integration and not slow"` | Before a commit that changes code |
| Full | `pytest` (everything, integration self-skips without Docker) | CI on every push; not on the laptop |
| Live | A real server on a test VM, driven by the change | Before a merge; evidence in `.notes/gates/` |

The inner loop has no time budget or guard yet: it is the quick tier that exists today, not a
measured one.

On the laptop (WSL):

- Run tests through `flock /tmp/claude-1000/yulon-heavy.lock nice`, one suite at a time.
  Parallel suites have crashed WSL.
- Never run the whole of `tests/test_controller_view.py`; CI runs it. Run its node ids.
- Pass node ids, not `-k` expressions, to the `yt` runner, and set `YULON_REPO` to the worktree,
  or it tests the main checkout.

Servers, builds and Docker never run on the laptop: live checks happen on the test VMs.

## Fixtures

A fixture file lives in git only if it is small and it is the contract: the golden compose
renders in `tests/data/` are what Yu'lon promises to write, so they stay. A fixture copied from a
real machine is trimmed to the lines the test reads. Generated passwords never appear in a
fixture or in gate evidence (`test_no_secrets_in_evidence`).

## What a fix report carries

Every worker report and fix pull request states:

- each new test, and that it failed before the change (BEFORE red, AFTER green), with the reason;
- the mutation for each, and that it turned the test red;
- the tests removed, and why each no longer protected anything;
- the result line of each suite run (`N passed, M skipped`), with no full logs.
