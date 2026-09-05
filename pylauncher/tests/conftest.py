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
import logging
import os
import time
from collections.abc import Callable, Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from yulon import apply as apply_module
from yulon import log as log_module
from yulon import platform

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

THE_USERS_OWN_CONFIG_DIR = platform.config_dir()
"""Where a REAL run of this app would put `yulon.log`, resolved once at import.

Read before any fixture has redirected anything, because the whole point is to
compare against the answer the user's own machine gives.
"""

UNGUARDED_OPEN_LOG = log_module._open_log
"""`log._open_log` as the app ships it, kept so the guard below can call through to it."""


def is_the_users_own_config_dir(directory: Path | str) -> bool:
    """Is `directory` the user's own app-state directory, or somewhere inside it?"""
    try:
        resolved = Path(directory).resolve()
    except OSError:  # pragma: no cover - a path the OS refuses to resolve is not the real one
        return False
    real = THE_USERS_OWN_CONFIG_DIR.resolve()
    return resolved == real or real in resolved.parents


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
      it. Those are removed and closed, and `_file_configured` goes back to
      False with them so the module's idea of "done" matches the handlers that
      exist.

    A handler that is NEITHER in `levels` NOR a file handler is left exactly
    alone: pytest puts its own `LogCaptureHandler` on the root logger per
    phase, and a teardown that removed everything it did not recognise would
    take `caplog`'s with it.

    `_file_configured` is CLEARED rather than restored, and that is the half
    that decides whether the NEXT test gets a log at all: `configure()` opens
    the file at most once per process, so a run that left the flag True hands
    every test after it a `configure(config_dir=...)` that quietly does
    nothing. Measured on m910q 2026-09-05 before this moved here, as
    `test_install_wiring.py`'s own fixture: green serially (i sorts before s)
    and two failures under `-n auto --dist loadfile`, on gw3. `_file_problem`
    goes with it, so one test's unwritable directory is never another test's
    diagnosis.
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
      whenever the real function would have answered the user's own. Only
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
    """
    scratch = tmp_path / "config-dir"
    real_config_dir = platform.config_dir

    def redirected() -> Path:
        wanted = real_config_dir()
        return scratch if is_the_users_own_config_dir(wanted) else wanted

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
