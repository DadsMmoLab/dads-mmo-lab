"""Tests for `yulon.runner` (roadmap Phase 1.1)."""

from __future__ import annotations

import io
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Generator
from pathlib import Path

import pytest

from tests.conftest import spelled_bounds
from tests.support_bash import bash_available
from yulon import runner
from yulon.runner import creationflags, run, stream

# Not just `which bash`: on Windows that finds the Store alias for WSL, which fails
# with execvpe(/bin/bash) when no distro is installed (Windows test VM, 2026-08-21).
needs_bash = pytest.mark.skipif(
    not bash_available(), reason="no bash that can run a script on this machine"
)

HANG_BOUND = 30.0
"""A DEADLOCK BREAKER for every in-process wait here, not an assertion about speed.

Each wait it sizes is for a child process to print its first line, or for a
worker thread to leave a generator once its child has been ended — work that
costs milliseconds when it happens at all. Measured on m910q (4 cores)
2026-09-05 with `--durations`, the two tests these bounds sit in reported
`0.07s call` and `0.26s call`, and most of the second is a Python interpreter
starting. Thirty seconds is two orders of magnitude above the worse of them.

Large on purpose, because the bound is on THIS process while the contention is
on the box — bug-checklist §33 records what a stopwatch-sized bound cost: 60
seconds against a run measured at 0.16s went red under 15-way load, and the run
it happened in was read as a real failure. Deleting the bounds is not an option
either: every wait here is on a thread whose only way out is a child process
ending, so a wedged subject with no bound is a suite that never returns, which
CI reports as a stuck job rather than a red one.
"""

DRIVER_BOUND = 120.0
"""`HANG_BOUND` for the driver SUBPROCESSES, and deliberately larger than it.

The drivers below bound their own waits with `HANG_BOUND` — it is substituted
into their source, so there is one number and not two. This one has to outlast
that: a driver that fails its own assertion must get to write the sentence
saying which half went wrong, rather than be killed by `subprocess.run`'s
timeout first and reported as "timed out". Measured on m910q 2026-09-05, the
two tests that spawn a driver reported `0.07s call` and `0.26s call`.
"""

POLL_PACE = 0.01
"""How often a poll loop re-reads `gi_running` or `/proc`. NOT a deadline.

Nothing is judged by it and lengthening it only makes the loop coarser. It is
named because `conftest.spelled_bounds` reads every `time.sleep` in the file
and an unnamed one is indistinguishable from a bound — the same reason
`conftest.JOB_PACE` is named.
"""


def _python_cmd(script: str) -> list[str]:
    """Build a python -c command list that runs the given script."""
    import sys

    return [sys.executable, "-c", script]


def test_creationflags_no_window_on_windows_zero_elsewhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`CREATE_NO_WINDOW` on native Windows, 0 off it — the console-flash guard (6.3).

    The one place to ask, so a flag applied to some spawn sites but not others
    cannot leave a window that flashes anyway. `sys.platform` is monkeypatched,
    not the stdlib, so the `subprocess.CREATE_NO_WINDOW` attribute (which does
    not exist on POSIX) is only consulted behind the platform check.
    """
    monkeypatch.setattr(runner.sys, "platform", "win32")
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    assert creationflags() == 0x08000000

    monkeypatch.setattr(runner.sys, "platform", "linux")
    assert creationflags() == 0


def test_run_captures_stdout() -> None:
    """`run()` captures stdout as a string."""
    proc = run(_python_cmd("print('hello')"))
    assert proc.returncode == 0
    assert proc.stdout == "hello\n"


def test_run_captures_stderr_separately() -> None:
    """`run()` keeps stderr distinct from stdout."""
    proc = run(_python_cmd("import sys; print('out'); print('err', file=sys.stderr)"))
    assert proc.returncode == 0
    assert "out" in proc.stdout
    assert "err" in proc.stderr


def test_run_does_not_raise_on_nonzero_exit() -> None:
    """`run()` returns the process; it does not raise on non-zero exit."""
    proc = run(_python_cmd("import sys; sys.exit(3)"))
    assert proc.returncode == 3


def test_run_raises_oserror_on_missing_executable() -> None:
    """`run()` propagates OSError if the executable can't be found/started."""
    with pytest.raises(OSError):
        run(["this-command-should-not-exist-anywhere-1234"])


def test_stream_yields_stdout_lines() -> None:
    """`stream()` yields each stdout line without trailing newline."""
    lines = list(stream(_python_cmd("print('a'); print('b')")))
    assert lines == ["a", "b"]


def test_stream_yields_stderr_lines_after_stdout() -> None:
    """`stream()` surfaces stderr, but only after stdout is exhausted.

    This is a documented behavior, not real-time interleaving: stderr is
    drained on a background thread purely to avoid pipe-buffer deadlock, then
    appended as a block once the process exits.
    """
    script = "import sys; print('o1'); print('e1', file=sys.stderr); print('o2')"
    lines = list(stream(_python_cmd(script)))
    assert lines == ["o1", "o2", "e1"]


def test_stream_can_interleave_stderr_live_for_a_command_whose_output_is_stderr() -> None:
    """`merge_stderr` puts both streams on one pipe, in the child's own order.

    Written for the native install engine's build stage (roadmap 6.2): BuildKit
    writes ALL of its progress to stderr, so the default ordering above turns a
    two-to-four-hour compile into a log panel that stays blank until the build
    has already finished.
    """
    script = (
        "import sys\n"
        "print('o1'); sys.stdout.flush()\n"
        "print('e1', file=sys.stderr); sys.stderr.flush()\n"
        "print('o2'); sys.stdout.flush()\n"
    )
    assert list(stream(_python_cmd(script), merge_stderr=True)) == ["o1", "e1", "o2"]
    # And the default is unchanged, because every other caller reads a command
    # whose stderr is an error report rather than its output.
    assert list(stream(_python_cmd(script))) == ["o1", "o2", "e1"]


def test_stream_still_reports_a_failure_with_the_streams_merged() -> None:
    with pytest.raises(subprocess.CalledProcessError):
        list(stream(_python_cmd("import sys; sys.exit(5)"), merge_stderr=True))


def test_stream_raises_on_nonzero_exit() -> None:
    """`stream()` raises CalledProcessError if the command exits non-zero."""
    with pytest.raises(subprocess.CalledProcessError):
        list(stream(_python_cmd("import sys; sys.exit(5)")))


def test_stream_raises_oserror_on_missing_executable() -> None:
    """`stream()` propagates OSError if the executable can't be found/started."""
    with pytest.raises(OSError):
        list(stream(["this-command-should-not-exist-anywhere-1234"]))


def test_stream_respects_cwd(tmp_path: Path) -> None:
    """`stream()` runs the child in the requested working directory."""
    (tmp_path / "marker.txt").write_text("hi", encoding="utf-8")
    script = "import pathlib; print(pathlib.Path('marker.txt').exists())"
    lines = list(stream(_python_cmd(script), cwd=tmp_path))
    assert lines == ["True"]


def test_stream_does_not_deadlock_on_large_stderr_payload() -> None:
    """A stderr payload bigger than the OS pipe buffer must not hang stream().

    This is the actual regression test for the background stderr-reader
    thread: without it, a child writing enough to stderr to fill the pipe
    buffer (commonly ~64KB) while nobody reads it would deadlock, because the
    child blocks on the full pipe and the parent blocks waiting for the child.
    """
    # 200,000 short stderr lines is comfortably larger than any common pipe
    # buffer size, on every platform this runs on.
    script = (
        "import sys\n"
        "for i in range(200_000):\n"
        "    print(i, file=sys.stderr)\n"
        "print('done')\n"
    )
    start = time.monotonic()
    lines = list(stream(_python_cmd(script)))
    elapsed = time.monotonic() - start

    assert lines[0] == "done"
    assert len(lines) == 200_001  # 1 stdout line + 200,000 stderr lines
    assert elapsed < 30  # generous bound; a real deadlock hangs indefinitely


def test_stream_terminates_child_on_early_generator_abandonment() -> None:
    """Abandoning a stream() generator early must not leak the child process.

    Regression test: a caller that `break`s out of a `for line in stream(...)`
    loop (or otherwise never exhausts the generator) must not leave the child
    process running indefinitely, and must not hang waiting for it.
    """
    # A child that would run "forever" if not terminated by stream()'s cleanup.
    script = "import sys, time\n" "print('started')\n" "sys.stdout.flush()\n" "time.sleep(60)\n"
    gen = stream(_python_cmd(script))
    first_line = next(gen)
    assert first_line == "started"

    start = time.monotonic()
    gen.close()  # triggers GeneratorExit at the suspended yield
    elapsed = time.monotonic() - start

    # Cleanup (terminate + join) must complete promptly, not wait for the
    # child's full 60-second sleep.
    assert elapsed < 10


# The abandonment above says `close()`. This one does not — and that is the
# whole difference between the two (bug-checklist §40).
_ABANDON_AND_EXIT = """
import sys
sys.path.insert(0, sys.argv[1])
from yulon.runner import stream

KEEP = []
TICKER = "\\n".join([
    "import sys, time",
    "while True:",
    "    print('tick'); sys.stdout.flush(); time.sleep(0.05)",
])


def main():
    gen = stream([sys.executable, "-c", TICKER])
    KEEP.append(gen)
    taken = 0
    for _line in gen:
        taken += 1
        if taken >= 5:
            break


main()
"""


def test_abandoning_a_stream_without_closing_it_does_not_abort_the_interpreter() -> None:
    """An unclosed `stream()` must not turn "the user closed the app" into SIGABRT.

    The RED, on m910q (Ubuntu, CPython 3.11.15), 2026-09-04 — exit 134:

        Fatal Python error: _enter_buffered_busy: could not acquire lock for
        <_io.BufferedReader name=5> at interpreter shutdown, possibly due to
        daemon threads
          Garbage-collecting
          File ".../yulon/runner.py", line 236 in stream

    Same shape as `sweep_driver2.py` in
    `pyplan/gates/7.10-ubuntu-2026-09-04/`, which is where it was found: take a
    few lines, `break`, never close, let the process exit. `KEEP` is why the
    generator is still alive at shutdown — the driver got that reference for
    free from its own frame; holding it deliberately is what makes the
    reproducer deterministic rather than dependent on when the cycle collector
    runs.

    It has to be a child process: the abort happens during interpreter
    finalisation, which pytest's own process never performs mid-run, and it is
    a fatal error rather than an exception, so nothing in-process could catch
    it. Asserting on the exit status is the only way to see it at all.
    """
    package_root = Path(runner.__file__).resolve().parent.parent
    proc = subprocess.run(
        _python_cmd(_ABANDON_AND_EXIT) + [str(package_root)],
        capture_output=True,
        text=True,
        timeout=DRIVER_BOUND,
    )
    assert "Fatal Python error" not in proc.stderr, proc.stderr
    assert proc.returncode == 0, f"exit {proc.returncode}\n{proc.stderr}"


# A child that says who it is, then produces nothing: whoever reads its stdout
# is blocked in `readline()` until it ends, which is the shape below.
_PID_THEN_SLEEP = "import os, sys, time; print(os.getpid()); sys.stdout.flush(); time.sleep(600)"


def test_the_exit_hook_ends_the_child_of_a_stream_another_thread_is_inside() -> None:
    """The exit hook must reach a `stream()` that a worker thread is RUNNING, not just holding.

    The driver test above abandons its generator suspended at a `yield`, and
    `close()` from the hook runs its `finally`. That is not the app's shape.
    `native._pump()` starts a daemon worker that executes
    `docker.run_attached()`, which sits in `for line in lines` INSIDE a
    `stream()` generator — so the frame is being run by the worker at the
    moment the hook fires, and `close()` from the main thread cannot enter it.
    Measured on m910q 2026-09-04, this exact shape:

        could not close an abandoned stream() at exit: generator already executing

    logged at debug, the `finally` never run, the worker still blocked in
    `readline()` ten seconds later, and the child alive with PPID 1 once the
    launcher was gone. The hook is called directly rather than through a real
    exit: what is under test is what it does to a running frame, and that is
    the same code either way.

    The observable is the WORKER: nothing but the child ending can release it
    from `readline()`, so "the worker left the generator" is "the child was
    ended", on every platform, without a liveness probe that means something
    different on Windows.
    """
    generator = stream(_python_cmd(_PID_THEN_SLEEP))
    lines: queue.Queue[str] = queue.Queue()
    outcome: list[BaseException] = []

    def work() -> None:
        try:
            for line in generator:
                lines.put(line)
        except BaseException as exc:  # noqa: BLE001 - the shape under test
            outcome.append(exc)

    worker = threading.Thread(target=work, daemon=True, name="test-stream-worker")
    worker.start()
    pid = int(lines.get(timeout=HANG_BOUND))
    deadline = time.monotonic() + HANG_BOUND
    while not generator.gi_running and time.monotonic() < deadline:
        time.sleep(POLL_PACE)
    assert generator.gi_running, "the worker should be blocked inside the generator by now"
    try:
        runner._close_abandoned_streams()
        worker.join(timeout=HANG_BOUND)
        assert not worker.is_alive(), "still inside the generator: the child was not ended"
        assert generator.gi_frame is None, "the generator's finally never ran"
        assert isinstance(outcome[0], subprocess.CalledProcessError), outcome
    finally:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        worker.join(timeout=HANG_BOUND)


# The same abandonment as `_ABANDON_AND_EXIT`, in the app's shape: the frame is
# being RUN by a worker thread when the process exits, not held suspended.
_WORKER_RUNS_STREAM_AND_EXIT = """
import sys, threading, time
sys.path.insert(0, sys.argv[1])
from yulon.runner import stream

PIDFILE = sys.argv[2]
TICKER = "; ".join([
    "import os, sys, time",
    "open(sys.argv[1], 'w').write(str(os.getpid()))",
    "print('tick')",
    "sys.stdout.flush()",
    "time.sleep(600)",
])
KEEP = []
RAISED = []


def main():
    gen = stream([sys.executable, "-c", TICKER, PIDFILE])
    KEEP.append(gen)
    got_one = threading.Event()

    def work():
        # The shape production's bridge worker has: `native._pump()`'s own
        # `work()` catches BaseException and hands it to its consumer. This
        # driver has no consumer, so it keeps it here. What must not happen is
        # the exception escaping into `threading.excepthook`, which writes to
        # stderr -- see the test's docstring for what that cost.
        try:
            for _line in gen:
                got_one.set()
        except BaseException as exc:
            RAISED.append(exc)

    threading.Thread(target=work, daemon=True).start()
    assert got_one.wait(__HANG_BOUND__), "the ticker never ticked"
    deadline = time.monotonic() + __HANG_BOUND__
    while not gen.gi_running and time.monotonic() < deadline:
        time.sleep(__POLL_PACE__)
    assert gen.gi_running, "the worker should be back inside the generator"


main()
""".replace("__HANG_BOUND__", repr(HANG_BOUND)).replace("__POLL_PACE__", repr(POLL_PACE))


def test_a_stream_a_worker_thread_is_running_at_exit_leaves_no_child_behind(
    tmp_path: Path,
) -> None:
    """The end-to-end shape of the test above: a real exit, and the grandchild must be gone.

    Measured on m910q 2026-09-04, before the exit hook could reach a running
    frame: the driver exited 0 — no abort, because the child was never
    touched and no lock was ever contended — and the ticker was still alive
    afterwards with PPID 1, a `python -c` sleeping for ten minutes that
    nothing would ever own. That is the launcher's own shape: `_pump()`'s
    worker inside `docker.run_attached()` inside `stream()`, a `docker compose
    build` outliving the app that started it.

    The liveness probe is `/proc/<pid>`, so the grandchild assertion runs on
    Linux only; the exit status and the absence of a fatal error are asserted
    everywhere. Once the driver has gone its orphan is reparented and reaped
    on death, so a `/proc` entry that persists is a process that persists.

    **The driver's own `work()` catches `BaseException`, and that is not
    decoration — it is the difference between this test flaking and not.**
    Until 2026-09-05 it was a bare `for _line in gen`, so the
    `CalledProcessError` the exit hook's terminate raises inside the generator
    escaped into `threading.excepthook`, which prints a traceback to stderr —
    while the interpreter was finalising, which is exactly when stderr's lock
    may be held by a daemon thread that will never be resumed. Measured on
    m910q (4 cores) 2026-09-05, sixty consecutive runs of this test on the
    combined tree: **2 of 60 aborted (runs 16 and 54), `nonzero=2 fatal=2`**,

        Exception in thread Thread-1 (work): ... File "<string>", line 23, in work
        Fatal Python error: _enter_buffered_busy: could not acquire lock for
        <_io.BufferedWriter name='<stderr>'> at interpreter shutdown, possibly
        due to daemon threads

    — the very `_enter_buffered_busy` abort bug-checklist §40 exists to close,
    raised here by the TEST rather than by `runner`. The meta review measured
    the same shape twice more, at 1 in 150 raw runs. Production's bridge worker
    has caught `BaseException` since 7.1; the driver now models it, and
    **180 runs afterwards on the same box (60 then 120) gave
    `nonzero=0 fatal=0` both times** — a 3%-of-60 flake needs a sample to say
    it is gone, not a single green run.
    """
    package_root = Path(runner.__file__).resolve().parent.parent
    pidfile = tmp_path / "ticker.pid"
    proc = subprocess.run(
        _python_cmd(_WORKER_RUNS_STREAM_AND_EXIT) + [str(package_root), str(pidfile)],
        capture_output=True,
        text=True,
        timeout=DRIVER_BOUND,
    )
    pid = int(pidfile.read_text()) if pidfile.exists() else None
    try:
        assert "Fatal Python error" not in proc.stderr, proc.stderr
        assert proc.returncode == 0, f"exit {proc.returncode}\n{proc.stderr}"
        assert pid is not None, "the ticker never wrote its pid"
        if sys.platform == "linux":
            deadline = time.monotonic() + HANG_BOUND
            while Path(f"/proc/{pid}").exists() and time.monotonic() < deadline:
                time.sleep(POLL_PACE)
            assert not Path(f"/proc/{pid}").exists(), f"pid {pid} outlived the driver"
    finally:
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


def test_the_exit_hook_goes_on_past_a_stream_whose_close_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """One generator whose `finally` fails must not stop the ones after it being closed.

    The hook runs at `atexit` and must let nothing escape — an exception there
    is reported as an error during shutdown, for a generator that is not the
    hook's to police — and must keep going, because the generator behind the
    failing one may be the launcher's own `docker compose build`. Registered
    through `_register()` the way `stream()` registers, in insertion order, so
    the failing one is reached first.
    """
    closed: list[str] = []

    def refuses() -> Generator[str, None, None]:
        try:
            yield "one"
        finally:
            raise RuntimeError("the finally itself failed")

    def behaves() -> Generator[str, None, None]:
        try:
            yield "one"
        finally:
            closed.append("behaves")

    first, second = refuses(), behaves()
    next(first)
    next(second)
    runner._register(first, runner._Child())
    runner._register(second, runner._Child())

    with caplog.at_level("DEBUG", logger="yulon.runner"):
        runner._close_abandoned_streams()

    assert closed == ["behaves"]
    assert first.gi_frame is None, "the failing generator was left open"
    assert any("the finally itself failed" in record.getMessage() for record in caplog.records)


def test_stream_registers_at_the_call_and_starts_no_process_until_the_first_next(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stream()`'s two halves: registration is EAGER, the child is LAZY.

    §40 turned `stream()` from a generator function into a plain function that
    builds one and registers it, and its docstring says both things about the
    result — that the registration happens at the call, and that "the body is
    still lazy, so no process starts until the first `next()`". Until
    2026-09-05 neither half had a test: the meta review found the laziness
    sentence owned by nothing, and the obvious way to lose it is a refactor
    that fills `_Child.proc` in eagerly so the exit hook never sees a `None` —
    which would start a `docker compose build` for a generator the caller may
    never iterate.

    Both halves are asserted here because they pull against each other. A
    version that starts the child at the call satisfies the registration
    assertion and fails the laziness one; a version that registers lazily (a
    plain generator function again, registering from inside its own body) does
    the reverse, and its hook can never see a generator nobody started.
    `_LIVE_STREAMS` is keyed weakly, so this checks membership rather than
    holding the dict.
    """
    started: list[list[str]] = []

    def _refuse(command: list[str], *args: object, **kwargs: object) -> None:
        started.append(command)
        raise AssertionError("Popen ran before the first next()")

    monkeypatch.setattr(runner.subprocess, "Popen", _refuse)
    generator = stream(["a-command-that-is-never-run"])
    try:
        assert started == [], started
        assert generator in runner._LIVE_STREAMS, "the exit hook cannot see this generator"
        assert (
            runner._LIVE_STREAMS[generator].proc is None
        ), "a child was recorded for a generator nobody has started"
        with pytest.raises(AssertionError, match="before the first next"):
            next(generator)
        assert started == [["a-command-that-is-never-run"]], started
    finally:
        generator.close()


def test_no_wall_clock_bound_in_this_file_is_written_as_a_bare_number() -> None:
    """Every bound here must be spelled as one of the named ones, and nothing else.

    The audit `test_log_panel.py`, `test_job.py`, `test_prompt.py`,
    `test_catalog_view.py` and `test_steam_deck_script.py` already run on
    themselves (bug-checklist §33), opted into here on 2026-09-05: the §40
    tests above added four kinds of bound to a file that had no audit -- until
    2026-09-05 they were spelled `lines.get(timeout=30)`,
    `worker.join(timeout=10)`, `subprocess.run(timeout=120)` and three
    hand-built `time.monotonic() + 5`; this commit renamed every one of them to
    `HANG_BOUND` / `DRIVER_BOUND`, which is what the audit reads today. That
    is the state §33 is about — a
    number typed at a call site carries no argument for its size, and these
    bounds are the only thing in this file a loaded box can move.

    `HANG_BOUND`, `DRIVER_BOUND` and `POLL_PACE` are named individually rather
    than counted, because they mean three different things: one is a deadlock
    breaker, the second must OUTLAST the first (a driver has to outlive its own
    internal bound to report its own failure), and the third is a poll interval
    nothing is judged by.

    **What this audit cannot see, stated so the price is known.** It reads the
    source of this file, so the bounds inside the driver scripts — which are
    string constants until a subprocess compiles them — are invisible to it;
    they are spelled `__HANG_BOUND__` and `__POLL_PACE__` and substituted from
    the names above, so there is one number rather than two, but nothing
    enforces that. `time.monotonic()` is in the set because three tests here
    measure elapsed time as their ASSERTION, and that costs this file the
    strongest thing the audit does elsewhere: a NEW hand-built deadline would
    not change the set. Neither `threading.Timer(1.0, ...)` nor the
    `assert elapsed < 30`-shaped proofs are read at all — `conftest.spelled_bounds`
    documents its reading, and a comparison is not a call.

    **Measured both ways on m910q 2026-09-05.** A bare `time.sleep(3)` appended
    to this file fails this test — `Extra items in the left set: '3'`. The same
    bound spelled through an alias (`import time as _t` then `_t.sleep(3)`)
    left it at `1 passed`: `conftest.spelled_bounds` matches the SPELLING
    `time.sleep`, so an aliased import is invisible to it in every file that
    runs this audit, not only here.
    """
    assert spelled_bounds(__file__) == {
        "HANG_BOUND",
        "DRIVER_BOUND",
        "POLL_PACE",
        "time.monotonic()",
    }


@needs_bash
def test_interact_cancel_interrupts_a_child_stuck_on_a_prompt(tmp_path: Path) -> None:
    """An unanswered no-newline prompt used to block forever; `cancel` gets out of it."""
    script = tmp_path / "stuck.sh"
    script.write_text(
        "#!/bin/bash\necho hello\necho -n 'A question no rule answers: '\nread -r answer\n",
        encoding="utf-8",
    )
    cancel = threading.Event()
    lines: list[str] = []
    started = time.monotonic()
    threading.Timer(1.0, cancel.set).start()
    for line in runner.interact(
        ["bash", str(script)],
        respond=lambda _line: None,
        quiet_seconds=0.2,
        cancel=cancel,
    ):
        lines.append(line)
    assert time.monotonic() - started < 20  # would never return before this fix
    assert lines and lines[0] == "hello"


# ---------------------------------------------------- the frozen library path
# The bug these cover shipped and could not be seen from here: it exists ONLY
# in the packaged app, and everything in this file runs from source.


def _frozen(monkeypatch: pytest.MonkeyPatch, **env: str) -> None:
    """Pretend to be the PyInstaller bundle, with the environment it creates."""
    monkeypatch.setattr(runner.sys, "frozen", True, raising=False)
    monkeypatch.setattr(runner.os, "environ", dict(env))


def test_running_from_source_leaves_the_child_environment_alone() -> None:
    """None means "inherit", which is what every caller has always got.

    The fix must not change the unfrozen case at all: the bug does not exist
    there, and a source checkout that suddenly spawns children with a rebuilt
    environment would be a new bug wearing the old one's clothes.
    """
    assert runner.child_env() is None
    assert runner.child_env({"A": "1"}) == {"A": "1"}


@pytest.mark.parametrize(
    "var", ["LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "DYLD_FRAMEWORK_PATH", "LIBPATH"]
)
def test_a_frozen_launcher_hands_back_the_users_own_loader_path(
    monkeypatch: pytest.MonkeyPatch, var: str
) -> None:
    """PyInstaller saves the pre-launch value; the child must get THAT one.

    Parametrised across all four because the bug is one bug and a fix applied
    to `LD_LIBRARY_PATH` alone is a fix on Linux only - macOS carries the same
    breakage under two different names.
    """
    _frozen(monkeypatch, **{var: "/bundle/_internal", f"{var}_ORIG": "/opt/mine/lib"})
    got = runner.child_env()
    assert got is not None
    assert got[var] == "/opt/mine/lib"
    assert f"{var}_ORIG" not in got


@pytest.mark.parametrize("orig", ["", None])
def test_a_frozen_launcher_unsets_a_path_the_user_never_had(
    monkeypatch: pytest.MonkeyPatch, orig: str | None
) -> None:
    """No _ORIG, or an empty one, means it was unset before the bundle set it.

    Unset is not the same as empty: an empty `LD_LIBRARY_PATH` means "look in
    the current directory" to some loaders, so leaving one behind would swap a
    library-path bug for a subtler one. Measured inside the real AppImage:
    `LD_LIBRARY_PATH_ORIG` is present and EMPTY there, which is exactly this
    case and the one that bites (2026-08-24).
    """
    env = {"LD_LIBRARY_PATH": "/bundle/_internal"}
    if orig is not None:
        env["LD_LIBRARY_PATH_ORIG"] = orig
    _frozen(monkeypatch, **env)
    got = runner.child_env()
    assert got is not None
    assert "LD_LIBRARY_PATH" not in got
    assert "LD_LIBRARY_PATH_ORIG" not in got


def test_a_caller_supplied_environment_is_sanitised_too(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`platform._c_locale_env()` copies os.environ, so it carries the poison.

    A caller passing an environment is not opting out of this - it is usually
    os.environ plus one key, which is the exact shape that hands the bundle's
    libraries to a child while looking deliberate.
    """
    _frozen(monkeypatch)
    poisoned = {"LC_ALL": "C", "LD_LIBRARY_PATH": "/bundle/_internal"}
    got = runner.child_env(poisoned)
    assert got == {"LC_ALL": "C"}


def test_every_spawn_site_in_this_module_sanitises_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Asked of the seam, not of the source.

    Grepping for `child_env(` would pass on a call that computes the right
    environment and then throws it away, and the defect being guarded against
    is precisely a spawn site that forgets. So each entry point is driven and
    the environment `subprocess` was actually handed is read back.
    """
    _frozen(monkeypatch, LD_LIBRARY_PATH="/bundle/_internal", PATH="/usr/bin")
    seen: list[dict[str, str] | None] = []

    class _Proc:
        returncode = 0
        pid = 1234

        def __init__(self, *a: object, **kw: object) -> None:
            seen.append(kw.get("env"))  # type: ignore[arg-type]
            # Real file objects: `stream()` asserts on both and iterates stdout,
            # so a double with None here fails for the wrong reason.
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def wait(self, timeout: float | None = None) -> int:
            return 0

        def poll(self) -> int:
            return 0

        def kill(self) -> None:
            return None

    def fake_run(*a: object, **kw: object) -> subprocess.CompletedProcess[str]:
        seen.append(kw.get("env"))  # type: ignore[arg-type]
        return subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner.subprocess, "Popen", _Proc)

    runner.run(["true"])
    runner.run(["true"], env={"LD_LIBRARY_PATH": "/bundle/_internal", "X": "1"})
    list(runner.stream(["true"]))

    assert seen, "no spawn site was reached; this test is measuring nothing"
    for env in seen:
        assert env is not None, "a frozen spawn passed env=None and so inherited the bundle's path"
        assert "LD_LIBRARY_PATH" not in env, env
