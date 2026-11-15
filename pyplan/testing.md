# Testing Yu'lon

The rules every change and every worker brief follows. Where a rule came from an incident in
this repo, the table below names it, so a reader can judge whether a case is the same kind. How
to run the suites is in [contribution.md](contribution.md); this file says what a test must be.

## The rules in brief

1. **Admission.** A test a change adds or edits names the behavior it protects: a contract a
   player or another module relies on (structural guards over the code included), a bug that
   happened (its ticket number in the name or docstring), or a reader of bytes Yu'lon did not
   write (a log line, `docker` output, a client file). A test that only restates the code beside
   it protects nothing. The rule applies to new and edited tests. Removing an existing test needs
   the maintainer's decision, written in the brief, never a worker's own call.
2. **It can fail.** Every new test is shown red once, and the report says how:
   - a regression test: red on the code before the fix (BEFORE red, AFTER green);
   - a new feature, an invariant or a guard: one reversible mutation of the code it protects,
     undone after.

   A BEFORE run that fails at import (`ImportError`, `AttributeError` on the new name) proves
   nothing and does not count. One of the two is enough; a test does not need both.
3. **Assert the property, not a stand-in.** Assert exactly what the name promises. Not a count
   that a legitimate change will move, and not "appears somewhere in the text".
4. **The arrange builds what the name claims.** A test named for an existing record writes that
   record first. Otherwise it tests something else and passes for the wrong reason.
5. **A test stops what it starts.** View jobs are guarded: `tests/conftest.py` fails a test that
   leaves a job from `yulon.ui.widgets.job` running or unanswered (T769, T771). Nothing guards
   anything else, so a test that starts a plain thread, a `QTimer` or an executor stops or joins
   it in its own `try`/`finally`, and a test that builds a view calls `view.shutdown()`.
6. **A test restores what it changes.** Change environment variables and module attributes
   through `monkeypatch`, never directly. `tests/conftest.py` resets only a named set of shared
   state, each in its own autouse fixture. New module-level state the code keeps (a cache, a
   resolved value) gets its own autouse reset there, named for what it resets.
7. **A flaky test is a bug.** It gets a ticket and a cause. One rerun to confirm a flake is
   fine; a rerun loop is not.
8. **A hand-kept list is checked against the real set, both ways.** When code keeps a
   hand-written list of what it covers (seams it guards, fields it copies, calls it exempts), a
   test asserts the list equals the real set: a new member fails until someone decides about it,
   and a listed name that no longer exists fails too.
9. **A switch that is off changes nothing.** A feature behind an opt-in switch (an environment
   variable, a setting) has a test that, with the switch off, the output, files written
   included, is byte-for-byte what it was before.
10. **Concurrency is tested with real overlap.** Two threads and a barrier, so the second really
    runs while the first holds what it holds, not two calls in a row that imitate it.
11. **Skips say why, and only these may skip:**
    - a platform capability the machine lacks (a Windows-only path on Linux, symlink privilege);
    - no reachable Docker daemon for an `integration` test;
    - an opt-in live fixture that is not configured (`YULON_WOTLK_SERVER_DIR` unset, its
      containers not in the state the test needs);
    - an optional host tool Yu'lon does not need at runtime (`git`, `bash`, `flock`).

    Anything else a test needs and does not find fails it. A gate that must not skip on Docker
    sets `YULON_REQUIRE_DOCKER=1`, which turns the Docker skips into failures.
12. **Proportion.** A change adds tests in proportion to the behavior it changes. Its report and
    pull request list the tests added and removed. A new test that takes more than about 2 s is
    marked `slow` (the marker's own definition in `pyproject.toml`).

## Where the rules came from

| Rule | What happened |
|---|---|
| 3 | A guard asserted "exactly one `networking.apply` call in controller_view.py". T885 legitimately added three more and turned CI red; what it protected was "every such call stays exempt". A render test checked that `user: "0:0"` appeared anywhere, which passed with two of three services wrong (T887). |
| 4 | `test_a_docker_record_without_the_switch_proceeds` never wrote a record: it tested an empty folder (T887 round 2). |
| 5, 7 | `test_stopped_distro_reads` failed three CI runs in a row. A tab job the test never waited for raced its "distro stopped" step; reruns hid it until it was ticketed (T886). |
| 6 | A Podman test set `DOCKER_HOST` directly and left it set, and nine unrelated tests went red one round later (T887). |
| 8 | The engine guard's completeness test found an unguarded field (`realm_name_current`) on its first run (T887 round 4). |
| 9 | Round 1 of T887 wrote a new key into every install record, switch or not. |
| 10 | A second Apply in the same tab ran beside the first, and only a two-thread test showed it (T885 round 3). |

## Levels and when to run them

| Level | What | When |
|---|---|---|
| Targeted | The test files or node ids for the code a change touches | While working, and in every report |
| Inner loop | `pytest -m "not integration and not slow" --ignore=tests/test_controller_view.py` | Before a commit that changes code |
| CI | Sharded `-m "not integration"` (6 shards per Python version), a separate live-Docker `integration` job, platform smoke on Windows and macOS, lint and types | Pull requests, and pushes to `main`, `Yulon`, `release/**` and `v*` tags. A topic branch gets CI only once it has a pull request. |
| Live | A real server on a test machine, driven by the change | Before merging a change to runtime behavior; evidence in `.notes/gates/` |

Targeted runs cannot see the guards that scan the whole package (seam counts, ready waits,
the write ledger, tab names, types for Windows and macOS). Before a branch merges, run the files
those guards live in, or let CI run on a pull request first: T887 phase 1 passed every
targeted run and still turned eight CI jobs red on five such guards.

`tests/test_controller_view.py` (about 1060 tests) is left out of the local inner loop: run its
node ids for what you touched, and let CI run the whole file. Paths are relative to `pylauncher/`.

## Repo guards workers meet

`tests/conftest.py` stubs some probes for every test. A test of the real path asks for it:
`real_database_read`, `real_image_read`, `real_left_tool_read`, or switching
`docker.RESERVATIONS_ON` back on. A test file that calls `spelled_bounds(__file__)` may not use
bare numbers in its sleeps, waits, joins and timeouts; it names each bound. Writes to the server go through
the ledger in [write-ledger.md](write-ledger.md). Read `conftest.py` before fighting a fixture.

## Fixtures

A fixture lives in `tests/data/` or `tests/fixtures/` only if it is the contract. The golden
compose renders are what Yu'lon promises to write, so they stay whole. Captures that tests read
in full (tail captures, byte-exact upstream files, files pinned by blob hash) stay whole too; any
other capture from a real machine is trimmed to the lines the test reads.

`test_no_secrets_in_evidence` scans only files git would publish, for the generated-password
shape. Gate evidence under `.notes/` is ignored by git and is not scanned, so check a gate log
for passwords by hand before quoting or committing any of it.

## What a fix report carries

Every worker report and fix pull request states:

- each new test, and how it was shown red (BEFORE red for a regression, or the mutation), with
  the reason it failed;
- the tests removed, and the decision that allowed it;
- the result line of each suite run (`N passed, M skipped in X s`), with no full logs, and any
  new test marked `slow`.
