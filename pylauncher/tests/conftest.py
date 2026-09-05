"""Shared pytest fixtures: an offscreen Qt application, and a pinned docker CLI name.

Also the one home for every wall-clock bound the Qt tests wait on, and for the
audit that keeps those bounds named. Until 2026-09-04 four files each carried
their own copy of the same pump-and-join helper (`_wait_for`, `_wait`, `_drain`,
`_pump_until`), each with its own typed-in number and each expiring silently;
the fix that named the numbers and reported the expiry landed in one of the
four and left three verbatim. One helper, one docstring, one report.
"""

from __future__ import annotations

import ast
import atexit
import inspect
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping
from logging.handlers import RotatingFileHandler
from pathlib import Path, PureWindowsPath

import pytest

from yulon import apply as apply_module
from yulon import log as log_module
from yulon import platform

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

THE_ANSWER_HANDED_TO_CHILDREN = "YULON_TEST_THE_USERS_OWN_CONFIG_DIR"
"""Env var carrying the REAL `config_dir()` down to every child this suite starts.

Without it the child redirect below defeats itself, silently. That redirect
hands a child a scratch `XDG_DATA_HOME`/`APPDATA`/`HOME`, and a child that is
ITSELF a pytest -- every `xdist` worker is one, spawned by a controller that
has already imported this module -- would then import this module and resolve
`THE_USERS_OWN_CONFIG_DIR` under the scratch it was handed. Every assertion in
this file would go on passing, about a scratch directory, with nothing left
between that worker and the user's own log. The answer travels down with the
redirect rather than being recomputed under it.
"""


def _the_users_own_config_dir() -> Path:
    """The real `config_dir()`, or the answer a parent process already resolved.

    Reads `os.environ` and takes no environment argument. It carried an
    `env: Mapping | None = None` parameter until 2026-09-05; nothing ever
    passed it, and `test_log.py`'s nested-pytest test records the reason it
    never would -- the claim is about what SURVIVES a process boundary, and
    a dict crosses none. Measured on m910q 2026-09-05 (review, round 3):
    hard-wiring the branch to `os.environ` left the suite at `2554 passed,
    4 skipped`, which is what a parameter no caller can reach is worth.
    """
    handed_down = os.environ.get(THE_ANSWER_HANDED_TO_CHILDREN)
    return Path(handed_down) if handed_down else platform.config_dir()


THE_USERS_OWN_CONFIG_DIR = _the_users_own_config_dir()
"""Where a REAL run of this app would put `yulon.log`, resolved once at import.

Read before any fixture has redirected anything, because the whole point is to
compare against the answer the user's own machine gives.
"""

THE_AMBIENT_CONFIG_DIR = platform.config_dir()
"""What `config_dir()` answers from the environment this PROCESS was handed.

The same directory as `THE_USERS_OWN_CONFIG_DIR` in a run started by a person,
and a different one in a run started by this suite: an `xdist` worker inherits
the scratch `XDG_DATA_HOME` the child guard gave it, so the ambient answer
there is a directory shared by every worker on the box and every test in it.

Both have to be redirected away from and for different reasons -- the first
because it is the user's, the second because it is nobody's -- which is what
`a_directory_no_test_chose` is. Measured on m910q 2026-09-05, `--checks` under
`-n auto --dist loadfile` with only the first clause:
`test_usage_and_a_bad_flag_leave_no_config_dir_behind` failed on
`assert not PosixPath('/tmp/yulon-test-child-home-p1qhr7ic/yulon').exists()`
-- a directory an earlier child had already made, in a test whose whole
premise is that its config dir is untouched. Serial, that run was green.
"""

UNGUARDED_OPEN_LOG = log_module._open_log
"""`log._open_log` as the app ships it, kept so the guard below can call through to it."""

UNREDIRECTED_CONFIG_DIR = platform.config_dir
"""`platform.config_dir` as the app ships it, kept because the fixture below replaces it.

A test that needs to ask what a REAL run would answer -- rather than what a test
is allowed to see -- has to call this one: the redirect is installed on the
module attribute, so `platform.config_dir()` inside a test is the redirect.
"""


def _is_at_or_inside(directory: Path | str, other: Path) -> bool:
    """Is `directory` `other` itself, or somewhere inside it?"""
    try:
        resolved = Path(directory).resolve()
    except OSError:  # pragma: no cover - a path the OS refuses to resolve is not the real one
        return False
    outer = other.resolve()
    return resolved == outer or outer in resolved.parents


def is_the_users_own_config_dir(directory: Path | str) -> bool:
    """Is `directory` the user's own app-state directory, or somewhere inside it?"""
    return _is_at_or_inside(directory, THE_USERS_OWN_CONFIG_DIR)


def a_directory_no_test_chose(directory: Path | str) -> bool:
    """Is `directory` one this suite must answer a scratch path for instead?

    Two of them, and the second exists only because this suite starts children:
    the user's own, and whatever `config_dir()` answers from the ambient
    environment -- which in an `xdist` worker is the scratch home the child
    guard handed it, shared by every worker and every test. See
    `THE_AMBIENT_CONFIG_DIR` for the failure that named the second.
    """
    return is_the_users_own_config_dir(directory) or _is_at_or_inside(
        directory, THE_AMBIENT_CONFIG_DIR
    )


def _guarded_open_log(directory: Path, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    """`log._open_log`, refusing the user's own directory before it creates anything.

    `AssertionError`, deliberately, and not `OSError`: `configure()` catches
    `OSError` and falls back to the temp dir, so an `OSError` here would be
    handled, logged as a "could not write its log" notice and swallowed. An
    `AssertionError` comes out through `configure()` and fails whatever asked.
    """
    assert not is_the_users_own_config_dir(directory), (
        f"a test asked to open the user's own log under {directory}. That file is what a "
        "user sends to support; the suite may not append to it. Point this test's "
        "`platform.config_dir()` at a tmp_path instead."
    )
    return UNGUARDED_OPEN_LOG(directory, max_bytes, backup_count)


log_module._open_log = _guarded_open_log
"""Installed HERE, at conftest import, and not inside the autouse fixture below.

A fixture's `monkeypatch` covers a test and nothing else, and a log file can be
opened outside every such window: module-scope code runs at COLLECTION, before
any fixture exists, and a background thread outlives the `monkeypatch` undo of
the test that started it.

Measured on m910q 2026-09-05 against a one-line test module whose BODY called
`configure(config_dir=platform.config_dir())`, same command both ways:

    guard installed by the autouse fixture   1 passed                +98 bytes
    guard installed here, at import          1 error during collection   +0

and the 98 bytes were `INFO [tests.test_zzz_import_time] a module body wrote
this at collection time`, in `/home/pk/.local/share/yulon/yulon.log`. The
redirect below stays per-test, because it hands out a FRESH directory and a
session-wide one would let each test read the last one's log.
"""


VARS_THAT_DECIDE_A_CHILDS_CONFIG_DIR = ("XDG_DATA_HOME", "APPDATA", "HOME", "USERPROFILE")
"""Every environment variable `platform.config_dir()` can reach the user's log through.

All four, on every OS, rather than this OS's two: the value is handed to a
CHILD, and the cheap version of this ("set the one this platform reads") is a
guard whose coverage depends on which box the suite is running on. `HOME` is in
the list for macOS, where `config_dir()` has no override of its own and
`Path.home()` is the only way in -- which is why
`test_every_entry_point_that_runs_for_a_user_leaves_the_same_log_behind` is
skipped there and this is not.

`test_log.py::test_the_variables_the_child_guard_rewrites_really_move_config_dir`
drives the list against the real `config_dir()` on whatever box is running, so
a rewrite of this list that no longer covers this platform fails here rather
than in a support inbox.
"""

CHILD_SCRATCH_HOME = Path(tempfile.mkdtemp(prefix="yulon-test-child-home-"))
"""The home/data directory every child process the suite starts is pointed at.

Per PROCESS (each `xdist` worker makes its own), and removed at exit.
"""

atexit.register(lambda: shutil.rmtree(CHILD_SCRATCH_HOME, ignore_errors=True))

UNGUARDED_POPEN_INIT = subprocess.Popen.__init__
"""`subprocess.Popen.__init__` as CPython ships it, kept so the guard can call through."""

ENV_IS_POSITIONAL_ARGUMENT = list(inspect.signature(UNGUARDED_POPEN_INIT).parameters).index("env")
"""How many positional arguments a `Popen(...)` call needs before it is passing `env`.

Asked of the signature this interpreter actually ships, counting `self`, so
the guard below can refuse a positional `env` without restating CPython's
parameter order. It was the literal `11` until 2026-09-05; a literal that
drifts stops the guard SEEING the environment it exists to rewrite, and it
would drift silently, because nothing downstream reads the number back
(review, round 3: widening it so it could never fire left the whole suite
at `2554 passed, 4 skipped` on m910q).
"""


def leads_to_the_users_own_log(value: str) -> bool:
    """Would a child pointed at `value` end up writing the user's own `yulon.log`?

    True for the directory itself and for every directory it hangs under, which
    is what makes one check enough for all four variables: `HOME` and
    `USERPROFILE` are ancestors of the user's config dir, `XDG_DATA_HOME` and
    `APPDATA` are its parent.
    """
    try:
        candidate = Path(value).resolve()
    except OSError:  # pragma: no cover - a path the OS refuses to resolve is not the real one
        return False
    real = THE_USERS_OWN_CONFIG_DIR.resolve()
    return candidate == real or candidate in real.parents


def child_env_with_the_users_own_log_out_of_reach(
    env: Mapping[str, str] | None,
) -> dict[str, str]:
    """The environment a child process gets: the caller's, with no route to the user's log.

    The child half of `_the_users_own_log_is_out_of_reach`, and the half that
    fixture cannot do. Its redirect patches `platform.config_dir` IN THIS
    PROCESS; a child has its own, resolves it from its own environment, and
    inherits the real `HOME` from the suite. Measured on m910q 2026-09-05, the
    whole suite run under a stand-in home (`HOME=/tmp/red-home`) with this
    rewrite removed: `/tmp/red-home/.local/share/yulon/yulon.log` was created
    and written -- the same appending-to-support's-evidence the in-process
    guard was added to stop, through the one door it does not cover.

    What wrote it was `test_log.py`'s own probe child, and that is worth being
    exact about: of the three sites where this suite already spawns the app,
    none writes the user's log TODAY, because each happens to point its child
    at a directory of its own. The hole is that nothing said they had to. The
    in-process rule had exactly the same shape until `test_spine.py` grew a
    fourth `install_wiring.main()` call and wrote 54,500 bytes into the real
    file.

    Same conditional shape as the in-process redirect, for the same reason: a
    variable the CALLER named is left exactly as the caller named it, so
    `test_main.py`'s deliberately unwritable `APPDATA` and
    `test_every_entry_point_that_runs_for_a_user_leaves_the_same_log_behind`'s
    per-entry-point `XDG_DATA_HOME` still reach the child. "Named by the
    caller" is `var in given` AND a value that differs from what this process
    would have handed down.

    `var in given` is load-bearing, and was missing until 2026-09-05 (review,
    round 3). Without it an OMITTED variable also "differs" -- `given.get(var)`
    is `None` -- so a partial `env=` dict read as four deliberate unsets and
    every one of the four was DROPPED from the child's environment instead of
    being pointed at the scratch home. An unset route is not a closed one: on
    POSIX `Path.home()` falls back to the passwd database, which is the user's
    own, and on macOS that is `config_dir()`'s only input.

    Measured on m910q 2026-09-05, a probe child spawned the way the suite
    spawns one and handed `{"EXIT_CODE": "3", "PYTHONPATH": ...}`: under
    `-n 2 --dist loadfile` it reached the OS with all four unset and
    answered `/home/pk/.local/share/yulon`, the user's own; serially only
    `HOME` was dropped (the box's own `XDG_DATA_HOME` was unset, so that
    one compared equal to the omission and was rewritten) and the child
    landed on the scratch. Green on the spelling CI runs, open on the
    spelling the local gate runs.

    That is not a shape only a probe can make. `runner.interact(...,
    env={"EXIT_CODE": "3"})` in
    `test_installer.py::test_interact_raises_on_nonzero_exit_after_yielding_output`
    reaches `Popen(env=child_env(env))` with that one-key dict verbatim --
    `yulon/runner.py:241` returns it unchanged off-frozen, `:623` spawns
    with it (read, not run). The round-3 review's census of `-n auto --dist
    loadfile` on m910q, same date, found 138 real children and exactly that
    one holding all four routes as `None`; it runs `bash`, so nothing was
    written that day, which is word for word this guard's own argument for
    why the in-process rule's shape was a hole.

    A caller that names one of them AS the user's own directory is refused
    outright rather than rewritten, exactly as `_guarded_open_log` refuses it
    in-process: rewriting would hide the mistake, and the caller stated an
    intent that the suite may not carry out.
    """
    inherited = dict(os.environ)
    given = dict(os.environ) if env is None else dict(env)
    for var in VARS_THAT_DECIDE_A_CHILDS_CONFIG_DIR:
        named_by_the_caller = var in given and given[var] != inherited.get(var)
        if named_by_the_caller:
            assert not leads_to_the_users_own_log(given[var]), (
                f"a test asked to start a child process with {var}={given[var]}, which points "
                "it at the user's own yulon.log. That file is what a user sends to support; "
                "the suite may not append to it. Point this child at a tmp_path instead."
            )
            continue
        given[var] = str(CHILD_SCRATCH_HOME)
    given[THE_ANSWER_HANDED_TO_CHILDREN] = str(THE_USERS_OWN_CONFIG_DIR)
    return given


def _guarded_popen_init(self: subprocess.Popen, *args: object, **kwargs: object) -> None:
    """Every child process in the suite, `subprocess.run` and `Popen` alike, through one seam.

    `Popen.__init__` and not `subprocess.run`, because `run`, `call`,
    `check_call` and `check_output` are four spellings of the same constructor
    and a guard on any one of them is a guard on one call site.
    """
    assert len(args) < ENV_IS_POSITIONAL_ARGUMENT, (
        "a test passed `env` to Popen positionally, so the guard on the user's own log "
        "cannot see it. Pass it as a keyword."
    )
    kwargs["env"] = child_env_with_the_users_own_log_out_of_reach(
        kwargs.get("env")  # type: ignore[arg-type]
    )
    UNGUARDED_POPEN_INIT(self, *args, **kwargs)  # type: ignore[arg-type]


subprocess.Popen.__init__ = _guarded_popen_init  # type: ignore[method-assign,assignment]
"""Installed at conftest import, for the reasons `_guarded_open_log` is, and one more.

The in-process rule covers what this process opens. It cannot see a child at
all: a child gets its own interpreter, its own `platform.config_dir` and the
real `HOME` this process was started with, so a suite that had closed every
in-process route was still one `subprocess.run` away from writing the file it
had just been fixed to protect -- and the suite ALREADY spawns the app as a
child, at three sites -- `test_main.py`'s launcher and `test_install_wiring.py`'s
`--help` run and entry-point table -- two of which drive a `main()` whose first
act is to open a log (review, 2026-09-05).
"""


def root_handler_levels() -> dict[logging.Handler, int]:
    """The level of every handler currently on the root logger, keyed by the handler itself."""
    return {handler: handler.level for handler in logging.getLogger().handlers}


def restore_root_logging(levels: dict[logging.Handler, int]) -> None:
    """Undo what a test's `configure()` did to the process-global root logger.

    Two separate leaks, measured on m910q 2026-09-05 by a probe test run after
    `tests/test_spine.py` in the same process:

    * `stderr handler pinned at WARNING`. `configure(stderr_level=...)` applies
      a level to the handler module-scope `get_logger()` built at import, which
      is the point of it — and nothing ever takes that level off again, so one
      `install_wiring.main()` in a test silences INFO on stderr for the rest of
      the process. Every handler that was there before the test gets its level
      back.
    * `leaked file handlers:
      ['/home/pk/.local/share/yulon/yulon.log']`. A `RotatingFileHandler` a
      test opened stays on the root logger, writing every record the remaining
      ~2,450 tests emit into a directory that is usually deleted underneath
      it. Those are removed and closed, and `_file_configured` is recomputed
      from the handlers that remain, so the module's idea of "done" matches the
      handlers that actually exist.

    A handler that is NEITHER in `levels` NOR a file handler is left exactly
    alone: pytest puts its own `LogCaptureHandler` on the root logger per
    phase, and a teardown that removed everything it did not recognise would
    take `caplog`'s with it.

    `_file_configured` is RECOMPUTED rather than restored, and that is the half
    that decides whether the NEXT test gets a log at all: `configure()` opens
    the file at most once per process, so a run that left the flag True hands
    every test after it a `configure(config_dir=...)` that quietly does
    nothing. Measured on m910q 2026-09-05 before this moved here, as
    `test_install_wiring.py`'s own fixture: green serially (i sorts before s)
    and two failures under `-n auto --dist loadfile`, on gw3. `_file_problem`
    goes with it, so one test's unwritable directory is never another test's
    diagnosis.

    Recomputed from the handlers that REMAIN, and not simply set to False,
    because the two answers differ for a file handler that was in `levels` --
    one that existed before the test, was therefore not leaked by it, and is
    left in place by the loop above. `False` there would tell the module that
    file logging is off while the handler is still on the root logger, and the
    next `configure(config_dir=...)` would open a second one. No test in the
    suite produces that state on its own today, so the branch is driven
    directly by
    `test_log.py::test_a_file_handler_that_was_already_there_keeps_file_logging_marked_done`;
    before it, `= False` passed everything (review, 2026-09-05).
    """
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler in levels:
            handler.setLevel(levels[handler])
        elif isinstance(handler, RotatingFileHandler):
            root.removeHandler(handler)
            handler.close()
    log_module._file_configured = any(
        isinstance(handler, RotatingFileHandler) for handler in root.handlers
    )
    log_module._file_problem = None


@pytest.fixture(autouse=True)
def _the_users_own_log_is_out_of_reach(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """No test may write `~/.local/share/yulon/yulon.log`, nor leave logging changed behind it.

    Measured on m910q 2026-09-05, `pytest -q tests/test_spine.py`, same command
    both ways: at `6546b190` the user's own log grew by 0 bytes; one commit
    later, with the CLI harness configuring file logging the way every other
    entry point does, it grew by **54,500 bytes** — 2,876,987 to 2,931,487 —
    and its last two lines were a fabricated `install of wow-wotlk finished`
    and the warning from a worker the fixture abandoned. That file is what a
    user sends to support. A suite that appends invented installs to it has
    destroyed the evidence it exists to preserve, and neither the fix nor the
    tests were wrong about anything else: nothing in the suite had ever said
    where a test's `config_dir()` may point.

    Three things, in the order they matter:

    * **The redirect.** `platform.config_dir()` answers a scratch directory
      whenever the real function would have answered one no test chose --
      the user's own, or the ambient one an `xdist` worker inherits. Only
      then, so the five `test_platform.py` tests that drive `config_dir()`
      against a made-up `$HOME`/`%APPDATA%` still see their own answer, and so
      a test that sets up its own directory keeps it. This is what makes
      `test_spine.py`'s four `install_wiring.main()` sites harmless without
      any of them knowing about logging at all.
    * **The guard**, `_guarded_open_log` above, which is not part of this
      fixture at all: it is installed once at import, so that collection and a
      background thread are covered too. The redirect is the fix; the guard is
      what makes the fix provable, and it fails loudly in whatever asked.
      `test_log.py` drives it directly.
    * **The restore.** `install_wiring.main()` calls
      `configure(stderr_level=WARNING)`, which pins a process-global handler
      for good, and leaves a file handler on the root logger behind it. Both
      are undone by `restore_root_logging()`, which carries the measurements
      and the reasoning; `test_log.py` drives it directly, because a fixture
      cannot assert on its own teardown.

    All three are IN-PROCESS, and that was the whole of the rule until
    2026-09-05 (review). A child process has its own `platform.config_dir`,
    its own `log._open_log` and the real `HOME` this one was started with, so
    none of the three can see it -- and this suite spawns the app as a child
    at three sites. The child half is
    `child_env_with_the_users_own_log_out_of_reach`, installed on
    `subprocess.Popen.__init__` above.
    """
    scratch = tmp_path / "config-dir"
    real_config_dir = platform.config_dir

    def redirected() -> Path:
        wanted = real_config_dir()
        return scratch if a_directory_no_test_chose(wanted) else wanted

    monkeypatch.setattr(platform, "config_dir", redirected)

    levels = root_handler_levels()
    yield
    restore_root_logging(levels)


@pytest.fixture(autouse=True)
def _docker_cli_is_the_plain_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every argv assertion in the suite gets `docker` at argv[0], on any machine.

    `platform.docker_program()` answers with what THIS host has: the plain name
    where docker is on the live PATH, an absolute `...\\resources\\bin\\docker.exe`
    on a Windows box where it is not, and `None` where Docker is not installed
    at all. All three are correct, and all three would be baked into the ~60
    tests that assert `["docker", "compose", ...]` — which would then pass or
    fail on the developer's Docker install rather than on the code. Pinning the
    resolved value keeps those tests about argv *shape*, and keeps the promise
    that the suite needs no Docker (and no Windows install) to run.

    Pinning the cache rather than the function leaves `docker_programs()` and
    `docker_ready()` untouched, so `test_provision.py` still exercises the real
    resolution; the tests that exercise `docker_program()` itself set the cache
    back to None first, and a `monkeypatch` from the test body wins over this
    one.
    """
    monkeypatch.setattr(platform, "_resolved_docker_cli", "docker")


@pytest.fixture(scope="session")
def qapp() -> Iterator[object]:
    """One offscreen `QApplication` for the whole session (Qt allows exactly one)."""
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def process_events(ms: int = 50) -> None:
    """Pump the Qt event loop for `ms` milliseconds (lets queued signals deliver)."""
    from PySide6.QtCore import QCoreApplication, QDeadlineTimer, QEventLoop

    deadline = QDeadlineTimer(ms)
    while not deadline.hasExpired():
        QCoreApplication.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)


HANG_BOUND = 60.0
"""A DEADLOCK BREAKER for every Qt wait in the suite, not an assertion about how fast Qt is.

Every wait that takes this is for a queued signal to cross a thread boundary,
and a healthy run costs milliseconds. Measured on m910q (4 cores) 2026-09-04,
the worst of them --
`test_log_panel.py::test_a_runner_dropped_before_its_thread_runs_does_not_leave_the_worker_dead`,
the one bug-checklist section 33 names -- reported `0.03s call` on an idle box,
and 0.59s to 0.97s in six consecutive runs with 96 CPU spinners beside it as
loadavg climbed from 36 to 98. Sixty seconds is sixty times the worst of those
and two thousand times the idle one, and the spread is what sizes it: a box
loaded 25x over its cores moved this test by a factor of 30, not of 2000.

The number is large on purpose, because the bound is on THIS PROCESS and the
contention is on the BOX. `test_steam_deck_script.py` records what a
stopwatch-sized bound cost: 60 seconds against a run measured at 0.16s went
red under 15-way parallel load, passed 14 of 14 re-run serially, and the run it
happened in was read as a real single failure -- three clean re-runs later a
merge went through on a result nobody had explained. Contention is bounded,
some multiple of a run costing milliseconds; a hang is unbounded, and any
finite bound catches an unbounded wait eventually. (That file keeps its own
`HANG_BOUND`, sized by its own measurements of a bash subprocess.)

Deleting the bounds is not an option. Every wait here is on work whose only way
out is a queued slot on this thread, so a wedged subject with no bound is a
suite that never returns -- which CI reports as a stuck job rather than a red
one. A minute per genuinely wedged test is the price of reporting it as a
failure.

Rejected: a bound proportional to measured load, which needs a calibration step
that is itself load-sensitive -- when it fires you cannot tell a hang from a
starved calibration. And a merely bigger number with no name, which is the same
defect with a bigger constant: what changed is that the argument for the size
travels with it, and that expiry says WHICH of the two happened (`pump_until`).
"""

HANG_BOUND_MS = int(HANG_BOUND * 1000)
"""`HANG_BOUND` for the Qt joins, which are spelled in milliseconds.

Derived rather than typed, so the two cannot drift apart.
"""

PUMP_YIELD = 0.005
"""The CPU `pump_until` hands to the thread it is waiting for. Not a deadline.

Nothing is judged by it, and lengthening it only makes the loop coarser. It is
named because it is the one number here whose size has an argument nobody would
guess: `process_events` busy-spins on `processEvents(AllEvents, 10)`, which
returns immediately on an empty queue, so without a real `sleep` the loop
yields nothing at all -- measured on m910q (4 cores, loadavg 2) 2026-09-04:
one `process_events(20)` made 6684 calls to `processEvents` in 20.0 ms of
wall clock at CPU/wall 1.00, and 5806 in 20.8 ms at 0.85 with the whole suite
running `-n auto` beside it. Five milliseconds against a `process_events(5)`
slice puts the loop at roughly half its time asleep.
"""

PUMPED_HEALTHY = 98
"""Turns per second `pump_until` gets on an unloaded box, measured, not guessed.

m910q (4 cores) 2026-09-04, this exact loop body: **98 turns/s** at loadavg 9,
14 and 22 -- flat, because one `process_events(5)` plus one `PUMP_YIELD` is
about 10 ms of wall clock and the loop is asleep for half of it -- and **24
turns/s** with the whole suite running 14-way beside it at loadavg 83. Later
the same day, after the helper moved here: 98, 98 and 99 turns/s at loadavg 2,
and 84, 94 and 89 turns/s with the whole suite running `-n auto` (four
workers on the four cores) beside it at loadavg 3 -- xdist at its default
width on this box moves the rate by a tenth, not by four.

The factor of four between the two is what makes the rate worth reporting. A
starved run and a wedged subject are told apart by it IN THE REPORT rather than
by re-running, which is the damage bug-checklist section 33 is really about:
the cost of a rare red is not the wasted minute, it is that a real single
failure stops being distinguishable from noise.
"""

JOB_PACE = 0.005
"""How often a fake job yields a line while it waits to be stopped. Not a deadline.

The `time.sleep(JOB_PACE)` inside a test's job source is the JOB'S OWN WORK --
the thing the panel is supposed to stream while it happens -- and nothing is
judged by it. It is named anyway, because the audit below reads every `sleep`
in a file and an unnamed one would be indistinguishable from a bound: the
exclusion that used to let it through ("skip nested defs") also let through
`gate.wait(5.0)` inside a job source, which IS a bound and the one whose
removal the log-panel fix consisted of (measured 2026-09-04 on m910q: with
that exclusion, reverting the fix left the audit at `1 passed`).
"""


def pump_until(done: Callable[[], bool], what: str, *, timeout: float = HANG_BOUND) -> None:
    """Pump the GUI event loop until `done()`, and FAIL saying which way it went wrong.

    A WALL CLOCK, not a number of turns. The log-panel copy of this counted
    iterations until 2026-09-03 and pumped with a bare `processEvents()`, so it
    burned its whole budget in about a second on a loaded box: `processEvents`
    returns immediately on an empty queue, and the worker whose signal the loop
    was waiting for had not been scheduled yet. `sleep` is the thing that
    actually hands the CPU to the worker this loop is waiting for.

    **Expiry is reported here.** The copies this replaced RETURNED on the
    deadline, silently, leaving the caller's own `assert` to say "the job thread
    never exited" -- the sentence of a regression -- whether the thread had
    wedged or the box had simply never scheduled it. Both readings were
    available and neither was supported, so the honest response to a red run
    was to re-run, which is exactly the habit bug-checklist section 33 says
    costs more than the red run itself. The rate below separates them with a
    measurement: see `PUMPED_HEALTHY`. The report is pinned by
    `test_log_panel.py::test_an_expired_pump_says_so_and_reports_how_fast_it_ran`;
    without that test, restoring the silent return left 21 passed (measured
    2026-09-04, m910q).

    `timeout` is keyword-only so the audit can find it: a bound passed
    positionally to a helper whose other arguments are a lambda and a sentence
    cannot be told from them by shape.
    """
    started = time.monotonic()
    deadline = started + timeout
    turns = 0
    while True:
        if done():
            return
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            rate = turns / elapsed if elapsed else 0.0
            pytest.fail(
                f"{what}: never happened within {timeout}s. The GUI loop was pumped {turns} "
                f"times in {elapsed:.1f}s ({rate:.0f} turns/s, against {PUMPED_HEALTHY}/s "
                "measured on an idle box and 24/s on one running the whole suite 14-way at "
                "loadavg 83). "
                "A rate near the idle figure means the loop ran and the condition never came "
                "true, which is the regression this wait exists to catch; a rate far below it "
                "means the box never scheduled this process, and nothing is wrong with the code."
            )
        process_events(5)
        time.sleep(PUMP_YIELD)
        turns += 1


def wait_for_panel(panel: object, *, timeout: float = HANG_BOUND) -> None:
    """Pump until a `LogPanel`'s job has stopped running, then join its thread.

    The join used to be `panel.wait(1000)` (or 2000) with its result thrown
    away, in three separate copies of this helper, so a box that had not
    finished the job left every assertion after the call reading a
    half-written panel and failing for a reason that named the panel's
    contents rather than the wait. It is `HANG_BOUND_MS` and asserted now:
    whatever went wrong, it is said here.

    Typed as `object` because this module is imported by tests that never load
    a widget; the two attributes used are the `LogPanel` ones.
    """
    pump_until(
        lambda: not panel.running,  # type: ignore[attr-defined]
        "the panel's job stopped running",
        timeout=timeout,
    )
    assert panel.wait(HANG_BOUND_MS), (  # type: ignore[attr-defined]
        "the panel reported the job done but its thread never joined"
    )
    process_events(50)


def spelled_bounds(test_file: str) -> set[str]:
    """Every wall-clock bound written in `test_file`, as the source text of its argument.

    For the audit each Qt test file runs on itself: the set this returns must
    EQUAL the file's list of named bounds, so a typed number, an expression, or
    a sixth name with no docstring behind it fails by not being in the list.
    That equality is the shape `test_steam_deck_script.py` used first, and it
    is stronger than "no literal and only these names" in two ways measured on
    m910q 2026-09-04 against the log-panel audit: `gate.wait(5.0)` inside a
    nested job source, and `timeout=HANG_BOUND * 2`, each left that audit at
    `1 passed`. Both are in the set here; neither is in any allow-list.

    What is read, from every call in the file, nested defs and lambdas
    included (`ast.walk`):

    * a `timeout=` keyword on ANY call -- the spelling `subprocess.run`,
      `Thread.join`, `Event.wait` and the helpers above all share;
    * every positional argument of `time.sleep`, of anything spelled `.wait`,
      `.wait_all` or `.communicate`, and of `.join` on anything but a string literal
      (`"\\n".join(...)` is a string operation with the same spelling, and an
      audit that flagged it would be reporting a genexp as a bound);
    * a call to `time.monotonic` itself, spelled `time.monotonic()`, because a
      deadline built by hand from the clock (`deadline = time.monotonic() +
      5.0`, and there were four of those) has no call argument to read. The
      wait it feeds goes through `pump_until`, which is why no test file needs
      the clock.

    Not read, and stated so the price is known: a bound passed positionally to
    a helper other than those named (`pump_until` and `wait_for_panel` make
    theirs keyword-only for this reason), and a loop bounded by a turn count
    rather than a clock (`for _ in range(50): process_events(20)` -- there were
    two; both went through `pump_until` on 2026-09-04).
    """
    tree = ast.parse(Path(test_file).read_text(encoding="utf-8"))
    spelled: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = ast.unparse(node.func)
        if func == "time.monotonic":
            spelled.add("time.monotonic()")
            continue
        bounds = [keyword.value for keyword in node.keywords if keyword.arg == "timeout"]
        positional_is_a_bound = (
            func == "time.sleep"
            or func.endswith((".wait", ".wait_all", ".communicate"))
            or (
                func.endswith(".join")
                and isinstance(node.func, ast.Attribute)
                and not (
                    isinstance(node.func.value, ast.Constant)
                    and isinstance(node.func.value.value, str)
                )
            )
        )
        if positional_is_a_bound:
            bounds += node.args
        spelled.update(ast.unparse(value) for value in bounds)
    return spelled


@pytest.fixture(autouse=True)
def _no_modal_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never let a test block on a modal dialog.

    `QMessageBox.warning()` and friends are modal: called from a slot in an
    offscreen test they wait for a click that never comes, and the whole run
    hangs. Tests that care about a dialog patch it themselves and see their own
    patch (this one is applied first).
    """
    try:
        from PySide6.QtWidgets import QMessageBox
    except ImportError:  # pragma: no cover - Qt-less environments skip UI tests anyway
        return
    for name in ("warning", "information", "critical", "about"):
        monkeypatch.setattr(QMessageBox, name, lambda *a, **k: QMessageBox.StandardButton.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No)


@pytest.fixture(autouse=True)
def _classic_mysql_client_names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer the client probe without touching the seam the tests assert on.

    `apply.mysql_client()` asks the database container whether it has `mysql` or
    only `mariadb` (mariadb:11 dropped the mysql* symlinks). That probe is a
    `docker exec` like every other call in these modules, so left real it would
    appear in argv assertions that are about SQL. Answering None here means the
    classic names are used, which is what every test written before the probe
    existed expects; `test_apply.py` drives the resolver itself directly.
    """
    monkeypatch.setattr(apply_module, "_probe_client", lambda container, candidates: None)
    apply_module._client_cache.clear()


DOCKER_ARGV0S = ("docker", "docker.exe", "wsl", "wsl.exe")
r"""Every argv[0] `docker._docker()` can produce, so the guard below knows one when it sees one.

`platform.docker_prefix()` answers `["docker"]`, an absolute
`...\resources\bin\docker.exe`, or `["wsl", "-d", <distro>, "--", "docker"]`.
The guard matches on the BASENAME, so the pinned Windows path is caught too.
"""


def argv_reaches_the_docker_cli(command: object) -> bool:
    """Does this argv start a docker CLI? The one rule the guard below is built on.

    Public and named so a test can drive the RULE rather than the fixture: an
    autouse fixture that refuses something is very hard to prove is doing
    anything at all, and until 2026-09-05 nothing proved this one. See
    `tests/test_docker_guard.py`.
    """
    if not isinstance(command, (list, tuple)) or not command:
        return False
    # `PureWindowsPath` on every host, not `Path`. `Path` is the RUNNING
    # platform's flavour, and on Linux a Windows argv[0] has no separator it
    # recognises: `Path(r"C:\Program Files\Docker\resources\bin\docker.exe").name`
    # is that whole string there, so the pinned Windows spelling the guard's
    # docstring claims to catch would sail through on the box this suite
    # actually runs on. Windows flavour splits on both `/` and `\`, so it is
    # right for either shape.
    return PureWindowsPath(str(command[0])).name.lower() in DOCKER_ARGV0S


@pytest.fixture(autouse=True)
def _no_unit_test_talks_to_a_real_docker_daemon(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail a non-integration test that reaches `docker` through `runner`.

    A unit test that shells out is not a unit test: it is slow, it answers
    differently on a box with Docker than on one without, and on the owner's
    laptop a single `docker` invocation auto-starts Docker Desktop's WSL2 VM
    (~2.4 GB) — the failure mode that crashed his PC on 2026-09-01 and the
    reason this suite is run on a test box at all.

    The reach is easy to add by accident and invisible once added, because a
    real `docker inspect` against a container that does not exist returns a
    perfectly usable "no" — which is what the test wanted anyway. The wait this
    file's `test_ready_budget.py` covers acquired exactly that: the management
    waits went through `native.wait_ready_quietly()`, whose first act is a state
    read, and every test that patched only `docker.wait_ready_for` started
    inspecting a container on the box running the suite.

    `runner.run`/`runner.stream` rather than `docker._docker`, because `_docker`
    is where the argv is BUILT and the tests that assert argv shape patch below
    it. Only docker argv is refused; every other subprocess (git, wsl-less
    shells) still runs, so this cannot quietly disable a test that was never
    about docker.

    TWO OF THE THREE ROUTES, and the third is named rather than implied. `docker.py`
    reaches the CLI through `runner.run()`, through `runner.stream()`
    (`run_attached()` at docker.py:2518, `follow_logs()` at :2351) and through a
    bare `subprocess.Popen` in `exec_stdin()` (docker.py:3338). `stream` was
    added here on 2026-09-05 after a review pointed out the guard covered one
    route and the commit message claimed all of them; adding it changed no
    result (m910q, whole suite, 0 failures either way), so what it buys is the
    NEXT accidental reach, not a defect today. `exec_stdin()` is still open:
    patching `subprocess.Popen` for the whole suite would catch every unrelated
    child process this repo spawns, and that is a bigger decision than a guard.
    """
    if request.node.get_closest_marker("integration"):
        return
    from yulon import runner

    real_run, real_stream = runner.run, runner.stream

    def refuse(command: object) -> None:
        if argv_reaches_the_docker_cli(command):
            pytest.fail(
                f"a unit test shelled out to the real docker CLI: {command!r}. "
                "Patch the seam the code under test reads (docker.container_state, "
                "docker._logs, native._world_output, or the installer's seams) instead."
            )

    def guarded(
        command: list[str], *args: object, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        refuse(command)
        return real_run(command, *args, **kwargs)  # type: ignore[arg-type]

    def guarded_stream(command: list[str], *args: object, **kwargs: object) -> object:
        # NOT a generator function. `runner.stream()` is one, and a `def` with a
        # `yield` in it runs no code at all until the first `next()` — the
        # refusal would then fire wherever the caller happened to iterate, or
        # not at all for a caller that builds the generator and drops it.
        refuse(command)
        return real_stream(command, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "run", guarded)
    monkeypatch.setattr(runner, "stream", guarded_stream)


@pytest.fixture
def a_world_container_that_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """`native._world_output()` answering "up, never restarted, printing", with no daemon.

    For the tests whose subject is a ready wait's ARGUMENTS. Since 2026-09-04 a
    management wait polls in windows and reads the container's state and log
    between them (`native.wait_ready_quietly`), so patching `wait_ready_for`
    alone no longer covers everything the call touches: the state read is the
    FIRST thing it does, before any window, and it went to whatever docker the
    box running the suite had. Four tests were doing that until 2026-09-05 —
    `docker inspect t-world`, `ac-worldserver`, `tbc-mangosd`,
    `tortoise-mangosd` — and passing.

    A constant, and a benign one: every test that takes this fixture also makes
    the wait answer True on its first window, so exactly one reading is taken
    and nothing is decided by it. A test ABOUT the reading builds its own (see
    `tests/test_ready_budget.py`, where `FakeWorld` is the machine).
    """
    from yulon.catalog import native

    monkeypatch.setattr(
        native,
        "_world_output",
        lambda spec, **_kwargs: native.WorldOutput(text="loading", restarts=0, status="running"),
    )


@pytest.fixture
def the_compose_project_is_not_pinned(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """`docker.pin_project_name()` recorded instead of run. Returns what it was asked to pin.

    `catalog_view._pin_compose_project()` runs at the end of every successful
    install and shells out to `docker compose config --format json` to learn
    what compose is calling the project. Four GUI tests whose subject is a
    BUTTON reached the box's daemon through it (measured m910q 2026-09-05); on
    a box with no docker they were passing through the best-effort `except`
    instead, which is a third behaviour again. Neither is the button.
    """
    from yulon import docker

    pinned: list[Path] = []
    monkeypatch.setattr(
        docker, "pin_project_name", lambda server_dir, **_kw: pinned.append(server_dir)
    )
    return pinned
