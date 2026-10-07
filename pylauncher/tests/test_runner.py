"""Tests for `yulon.runner` (roadmap Phase 1.1)."""

from __future__ import annotations

import io
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import weakref
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

GIVE_UP = 0.3
"""The `timeout` handed to a `run()` that is MEANT to give up: its child sleeps 60 s.

A deadline the test wants to pass, not one it is judged by, so a loaded box
only makes it pass more surely. Its number is in the runner's own words
("timed out after 0.3s"), which the timeout tests read back.
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
    script = "import sys\nfor i in range(200_000):\n    print(i, file=sys.stderr)\nprint('done')\n"
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
    script = "import sys, time\nprint('started')\nsys.stdout.flush()\ntime.sleep(60)\n"
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
    `.notes/gates/7.10-ubuntu-2026-09-04/`, which is where it was found: take a
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


class _BlockedStream:
    """A `stream()` a worker thread is blocked inside, and the pid of its quiet child.

    Every test below needs the same three steps before it can ask its own
    question — start the generator, get a line out of it on a worker thread, and
    establish that the worker is now INSIDE the frame rather than about to be —
    and the third is the one that matters: a test that pressed its button before
    the worker reached `readline()` would pass by ending a child nobody was
    waiting on, which is not the shape any of this exists for.
    """

    def __init__(self, name: str) -> None:
        self.generator = stream(_python_cmd(_PID_THEN_SLEEP))
        self.lines: queue.Queue[str] = queue.Queue()
        self.outcome: list[BaseException] = []

        def work() -> None:
            try:
                for line in self.generator:
                    self.lines.put(line)
            except BaseException as exc:  # noqa: BLE001 - the shape under test
                self.outcome.append(exc)

        self.worker = threading.Thread(target=work, daemon=True, name=name)
        self.worker.start()
        self.pid = int(self.lines.get(timeout=HANG_BOUND))
        deadline = time.monotonic() + HANG_BOUND
        while not self.generator.gi_running and time.monotonic() < deadline:
            time.sleep(POLL_PACE)
        assert self.generator.gi_running, "the worker should be blocked inside the generator by now"

    def close(self) -> None:
        try:
            os.kill(self.pid, signal.SIGTERM)
        except OSError:
            pass
        self.worker.join(timeout=HANG_BOUND)


def test_end_streams_started_on_ends_the_child_a_worker_thread_is_blocked_reading() -> None:
    """The Stop button's reach: end the child of every live stream STARTED on one thread.

    This is the source half of the log-panel Stop defect (7.10, measured on
    `yulon-ubuntu2` 2026-09-08). A `LogPanel` worker sits in
    `for line in proc.stdout` inside a `stream()` generator; the panel's own
    `_stop` flag is only read between lines, so a source that has gone quiet is
    never asked again. `.notes/gates/7.10-rerun-ubuntu2-2026-09-08/` recorded 120
    seconds of `running=True cancelled=True worker._stop=True lines=210` after
    the click, and then `panel.wait(10000) -> False`.

    Nothing but the child ending releases that thread, so "the worker left the
    generator" is "the child was ended" on every platform, with no liveness
    probe that means something different on Windows — the observable
    `test_the_exit_hook_ends_the_child_of_a_stream_another_thread_is_inside`
    already chose for the same reason.

    Mutation this catches: dropping the `_end_child` call (the worker stays
    inside the generator and `worker.is_alive()` is still True at
    `HANG_BOUND`), and never recording `_Child.started_on` (nothing matches the
    ident, the count is 0, and the assertion below names the count).
    """
    blocked = _BlockedStream("test-end-streams-worker")
    try:
        assert runner.end_streams_started_on(blocked.worker.ident) == 1
        blocked.worker.join(timeout=HANG_BOUND)
        assert not blocked.worker.is_alive(), "still inside the generator: the child was not ended"
        assert blocked.generator.gi_frame is None, "the generator's finally never ran"
        # STILL A FAILURE, deliberately. A child we terminated exits non-zero
        # (143 on the live box, in that gate's own last line), and `stream()`
        # goes on raising it: `docker.run_attached()` reads a raised status as a
        # failed build and a swallowed one as `AttachedRun(0, ...)`, so a
        # `return` here would report a killed compile as a build that worked.
        # Whose stop it was is the PANEL's to know -- see `LogPanel.stop()`.
        assert isinstance(blocked.outcome[0], subprocess.CalledProcessError), blocked.outcome
    finally:
        blocked.close()


def test_a_stream_a_stop_ended_says_so_by_type_and_one_killed_otherwise_does_not() -> None:
    """T240: `StreamEnded` is the exit `end_streams_started_on()` caused, and only that one.

    Still a `CalledProcessError` either way (the reason is in the test above). The
    type is what lets a caller tell the Stop from the command failing: the
    containerized clone answered a Stop-killed docker CLI with a host-git clone.
    The second stream's child is killed from outside, with the same signal, and
    exits the same way; only who ended it differs.
    """
    stopped = _BlockedStream("test-stream-ended-worker")
    try:
        assert runner.end_streams_started_on(stopped.worker.ident) == 1
        stopped.worker.join(timeout=HANG_BOUND)
        assert isinstance(stopped.outcome[0], runner.StreamEnded), stopped.outcome
    finally:
        stopped.close()
    killed = _BlockedStream("test-stream-killed-worker")
    killed.close()
    assert isinstance(killed.outcome[0], subprocess.CalledProcessError), killed.outcome
    assert not isinstance(killed.outcome[0], runner.StreamEnded), "nobody pressed Stop"


def test_end_streams_started_on_returns_before_the_reap_it_asked_for_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """It is called from the GUI thread, so it must not wait for a child to die.

    `origin/rust-main:crates/dml-core/src/proc.rs`'s `abandon()` records this
    exact trap (`pyplan/rust-prior-art.md` does not mention that file, so it was
    read on the branch): "`kill()` can fail, and the `wait()` that followed it
    was then INFINITE against a process whose whole problem is that it outlives
    the deadline. Measured 2026-08-03: a 600ms-bounded call returned after 605
    SECONDS." `_end_child()` ends with an unbounded `proc.wait()` after its
    `kill()`, so calling it inline on the thread that painted the Stop button
    would freeze the window for as long as the child took to go — which on the
    one machine that measured it was ten minutes.

    Asserted through a blocking stand-in rather than a stopwatch, because the
    production hang needs `kill()` to fail and a test cannot force that (the
    same reasoning `proc.rs` gives for its `Abandonable` trait).

    Mutation this catches: ending the children inline (`_end_child(child.proc)`
    in the loop). `entered` is then set from this thread, `release` is never
    set, and the test hangs at `HANG_BOUND` inside the call instead of reaching
    the assertion below it.
    """
    entered = threading.Event()
    release = threading.Event()
    real_end_child = runner._end_child

    def blocking_end_child(proc: subprocess.Popen[str], job: object = None, **kw: bool) -> None:
        entered.set()
        release.wait(HANG_BOUND)
        real_end_child(proc, job, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "_end_child", blocking_end_child)
    blocked = _BlockedStream("test-end-streams-nonblocking")
    try:
        assert runner.end_streams_started_on(blocked.worker.ident) == 1
        assert entered.wait(HANG_BOUND), "the reap was never started"
        assert not release.is_set(), (
            "this thread only reaches here while the reap is still blocked; if the ending "
            "happened inline it would have waited for it"
        )
    finally:
        release.set()
        blocked.close()


def test_end_streams_started_on_leaves_a_stream_another_thread_started_alone() -> None:
    """A panel's Stop ends ITS job's children and nobody else's.

    Two things run streams in this app that must not be reachable from one
    panel's Stop: the other panels (an install in the Catalog window while the
    Console tab follows a log), and `docker.repair_import()`, which "cannot be
    cancelled, deliberately... the only way to abandon a running `compose up` is
    to terminate it -- which stops `ac-db-import` part-way through writing
    schemas" (`docker.py`). Both are streams on some other thread, and the
    filter is the whole of what keeps them out of reach.

    Mutation this catches: an ident filter that is not one (`if True`, or
    comparing against `threading.get_ident()` -- the CALLER's thread, which is
    the GUI thread and matches nothing, so the working case would break too).
    """
    blocked = _BlockedStream("test-end-streams-other-thread")
    try:
        # This thread's own ident: real, never equal to the worker's, and not a
        # number invented for the test.
        assert runner.end_streams_started_on(threading.get_ident()) == 0
        blocked.worker.join(POLL_PACE)
        assert blocked.worker.is_alive(), "another thread's stream was ended"
        assert not blocked.outcome, blocked.outcome
    finally:
        blocked.close()


def test_a_finished_stream_on_this_thread_is_not_counted_as_something_to_end() -> None:
    """A THREAD IDENT IS REUSED, and the count has to survive that.

    Found by the checks gate on `yulon-fedora` 2026-09-09, where the whole file
    runs in one process: the test above read `assert 2 == 1`. The registry outlives
    the threads in it -- an entry stays while its generator does -- so an earlier
    test's finished stream was still registered, the OS had handed its dead
    thread's ident to the new worker, and `started_on == ident` was true of both.

    Reproduced here without needing the recycling: this stream ran to completion
    ON THIS THREAD, so its entry is attributed to the caller's own ident, which is
    exactly the shape the gate produced. Asking the child settles it.

    Mutation this catches: filtering on `child.proc is not None` (the count is 1,
    and the panel's Stop is reported as having ended a job that had already ended).
    """
    finished = stream(_python_cmd("print('done')"))
    assert list(finished) == ["done"]
    child = runner._LIVE_STREAMS[finished]
    assert child.started_on == threading.get_ident(), "this stream ran here"
    assert child.proc is not None and child.proc.poll() is not None, "its child has exited"

    assert runner.end_streams_started_on(threading.get_ident()) == 0


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
        "GIVE_UP",
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
            # so a double with None here fails for the wrong reason. Text or
            # bytes as the spawn asked: `stream_progress()` reads binary pipes
            # on purpose (universal-newline translation eats the carriage
            # returns it exists for), and would decode a `str` as if it were.
            empty: object = io.StringIO("") if kw.get("text") else io.BytesIO(b"")
            self.stdout = empty
            self.stderr = empty

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
    list(runner.stream_progress(["true"]))

    assert seen, "no spawn site was reached; this test is measuring nothing"
    for env in seen:
        assert env is not None, "a frozen spawn passed env=None and so inherited the bundle's path"
        assert "LD_LIBRARY_PATH" not in env, env


# ---------------------------------------------------------------------------
# T35: `stream_progress()`, for the one command whose progress is stderr with no
# newlines in it.

GIT_PROGRESS = Path(__file__).parent / "fixtures" / "git-clone-progress.stderr"
"""A real `git clone --progress` stderr, carriage returns and all.

Recorded on this laptop (WSL2, Ubuntu 24.04, git 2.43.0) on 2026-09-12:

    git clone --progress https://github.com/psf/requests.git repo 2>stderr.raw

9958 bytes, 209 `\\r` and 7 `\\n`: 216 fragments, of which 103 are `Receiving
objects` readings from 0% to 100%, 102 are `Resolving deltas`, 4 are
`Compressing objects` and 7 are sentences (`Cloning into 'repo'...`,
`remote: Enumerating objects: 26865, done.`). Committed with `-text` in
`.gitattributes` so no checkout can normalise the bytes it was recorded for.
"""


class _Recorded:
    """A `Popen` double that serves recorded bytes and then exits.

    Binary streams, because `stream_progress()` reads binary: `text=True` puts
    the pipes through universal-newline translation, which turns every `\\r`
    into a `\\n` before this code can see it — so a fake that handed back text
    would make a `\\n`-only split look correct.
    """

    pid = 4321

    def __init__(self, *a: object, **kw: object) -> None:
        self.stdout = io.BytesIO(_Recorded.out)
        self.stderr = io.BytesIO(_Recorded.err)
        self.returncode = _Recorded.code
        # A child that has not been reaped answers `None` to `poll()`, and
        # `end_streams_started_on()` reads exactly that to tell a stream worth
        # ending from one already finished. A double that answered its exit
        # code from the start would be invisible to it.
        self._ended = False

    out: bytes = b""
    err: bytes = b""
    code: int = 0

    def wait(self, timeout: float | None = None) -> int:
        self._ended = True
        return self.returncode

    def poll(self) -> int | None:
        return self.returncode if self._ended else None

    def terminate(self) -> None:
        self._ended = True

    def kill(self) -> None:
        self._ended = True


def _serving(
    monkeypatch: pytest.MonkeyPatch, *, out: bytes = b"", err: bytes = b"", code: int = 0
) -> None:
    _Recorded.out, _Recorded.err, _Recorded.code = out, err, code
    monkeypatch.setattr(runner.subprocess, "Popen", _Recorded)


def test_stream_progress_splits_gits_carriage_returns_into_separate_fragments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every progress reading git wrote comes out as its own line.

    Git ends a progress update with a carriage return so a terminal overwrites
    the line in place, and `\\n` only when a phase finishes — measured in the
    recording above: 209 `\\r` against 7 `\\n`. A reader that splits on newlines
    alone therefore gets one enormous line per phase, and the only percentage
    it can show is the last one in it.

    Mutation: split stderr on `\\n` only (drop `\\r` from `_FRAGMENT`) and the
    103 `Receiving objects` readings collapse to a single 100%, which is what
    the assertions below count.
    """
    _serving(monkeypatch, err=GIT_PROGRESS.read_bytes())
    got = list(runner.stream_progress(["git", "clone", "--progress", "x"]))

    receiving = [
        int(found.group(1))
        for line in got
        if (found := re.search(r"Receiving objects:\s+(\d+)%", line))
    ]
    assert receiving[0] == 0 and receiving[-1] == 100
    assert len(receiving) > 50, f"the readings collapsed: {len(receiving)} of them"
    assert "Cloning into 'repo'..." in got
    assert not any("\r" in line for line in got), "a fragment kept its own separator"
    assert not any("\n" in line for line in got)


def test_stream_progress_carries_both_pipes_keeping_the_order_within_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both pipes, live, on two threads — which is the difference from `stream()`.

    `stream()` withholds stderr until the child has exited and says why. For a
    clone that is the whole output: git says nothing at all on stdout, so the
    panel would have had the entire progress arrive after the clone finished.

    **Nothing here depends on the order BETWEEN the pipes**, because two
    independent reader threads cannot promise one: what is asserted is that
    every fragment arrives, and that each pipe's own fragments keep their order.
    The test claimed cross-pipe arrival order until the 2026-09-12 review found
    the docstring promising what the implementation cannot.

    Mutation: drop the stderr reader and the two stderr fragments are missing.
    """
    _serving(monkeypatch, out=b"one\ntwo\n", err=b"first\rsecond\n")
    got = list(runner.stream_progress(["git", "fetch"]))
    assert sorted(got) == ["first", "one", "second", "two"]
    assert got.index("one") < got.index("two"), "stdout's own order was not kept"
    assert got.index("first") < got.index("second"), "stderr's own order was not kept"


def test_stream_progress_raises_on_a_non_zero_exit_like_stream_does(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The status is raised and not returned, so a failed clone cannot read as a done one.

    Mutation: return instead of raising and `git.clone_lines()` reports a
    successful clone for a repository that does not exist.
    """
    _serving(monkeypatch, err=b"fatal: repository not found\n", code=128)
    with pytest.raises(subprocess.CalledProcessError) as raised:
        list(runner.stream_progress(["git", "clone", "nope"]))
    assert raised.value.returncode == 128


def test_stream_progress_yields_everything_written_before_the_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure's own words reach the caller before the exception does.

    The tail of git's stderr is what says WHY a clone failed, and it arrives on
    the same pipe as the progress.

    Mutation: raise before draining the queue and the sentence is lost, leaving
    a `CalledProcessError` with nothing but a number.
    """
    _serving(monkeypatch, err=b"fatal: could not read Username\n", code=128)
    said: list[str] = []
    with pytest.raises(subprocess.CalledProcessError):
        for line in runner.stream_progress(["git", "clone", "nope"]):
            said.append(line)
    assert said == ["fatal: could not read Username"]


def test_stream_progress_ends_its_child_when_the_caller_walks_away(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Stop button's path: a generator that is closed leaves no clone running.

    `end_streams_started_on()` is how `LogPanel`'s Stop reaches a blocked read,
    and it can only reach a stream that REGISTERED itself. A `stream_progress()`
    that skipped the registry would be a clone nothing could stop.

    Mutation: skip `_register()` and `end_streams_started_on()` returns 0.
    """
    _serving(monkeypatch, err=b"Receiving objects:   1% (1/100)\r" * 50)
    generator = runner.stream_progress(["git", "clone", "x"])
    next(generator)
    assert runner.end_streams_started_on(threading.get_ident()) == 1
    generator.close()


def test_stream_progress_asks_for_binary_pipes_so_the_carriage_returns_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one fact about this spawn that no recording can show.

    `text=True` (and `universal_newlines`, and an `encoding`) puts both pipes
    through universal-newline translation, which rewrites every `\\r` as `\\n`
    BEFORE this module can see it. The split would then look perfectly correct
    and `_FRAGMENT`'s `\\r` would be dead code — and no fixture can catch it,
    because a fake process hands back whatever bytes the test chose. So the
    spawn's own arguments are what is asserted.

    Mutation: add `text=True` to the `Popen` call and this fails while every
    other `stream_progress` test still passes.
    """
    asked: dict[str, object] = {}

    class _Spy(_Recorded):
        def __init__(self, *a: object, **kw: object) -> None:
            asked.update(kw)
            super().__init__(*a, **kw)

    _Recorded.out, _Recorded.err, _Recorded.code = b"", b"", 0
    monkeypatch.setattr(runner.subprocess, "Popen", _Spy)
    list(runner.stream_progress(["git", "clone", "x"]))

    assert asked, "the spawn was never reached"
    assert not asked.get("text")
    assert not asked.get("universal_newlines")
    assert asked.get("encoding") is None


def test_stream_progress_hands_the_child_the_environment_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`env` goes through `child_env()`, exactly as `run()`'s does.

    The caller that needs it is the streamed git clone: `_no_prompt_env()` is
    what stops a credential prompt turning a headless clone into a wait with no
    end, and `run()` has carried it since the beginning. This took no `env` at
    all until the 2026-09-12 review, so the one git call that can block forever
    was the one running without the guard.

    Through `child_env()` and not straight into `Popen`, because that is the
    function that takes the bundle's `LD_LIBRARY_PATH` back out — a frozen
    launcher whose git loads the bundle's libraries is the measured failure in
    `child_env()`'s own docstring.

    Mutation: drop the parameter (or pass it past `child_env()`) and this fails.
    """
    asked: dict[str, object] = {}

    class _Spy(_Recorded):
        def __init__(self, *a: object, **kw: object) -> None:
            asked.update(kw)
            super().__init__(*a, **kw)

    _Recorded.out, _Recorded.err, _Recorded.code = b"", b"", 0
    monkeypatch.setattr(runner.subprocess, "Popen", _Spy)
    list(runner.stream_progress(["git", "clone", "x"], env={"GIT_TERMINAL_PROMPT": "0"}))

    assert asked.get("env") == {"GIT_TERMINAL_PROMPT": "0"}


# ------------------------------------- a timeout logged once per change (PR 291 fix round 2)


def _sometimes_hangs(flag: Path) -> list[str]:
    """One command line that hangs while `flag` exists and answers at once when it does not."""
    script = "import os, sys, time; time.sleep(60 if os.path.exists(sys.argv[1]) else 0)"
    return [sys.executable, "-c", script, str(flag)]


def _said(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == runner.logger.name]


def test_a_command_that_keeps_timing_out_is_logged_once_per_change(
    caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """The Windows live test of PR 291: "docker did not answer within 30.0s; giving up" every
    35 s for the five minutes Docker's engine was away (9 identical lines)."""
    flag = tmp_path / "hang"
    command = _sometimes_hangs(flag)
    other = _sometimes_hangs(tmp_path / "other")
    (tmp_path / "other").touch()
    caplog.set_level("INFO", logger=runner.logger.name)

    flag.touch()
    first = run(command, timeout=GIVE_UP)
    second = run(command, timeout=GIVE_UP)
    assert (first.returncode, second.stderr) == (124, f"timed out after {GIVE_UP}s")
    assert runner.timed_out(first) and runner.timed_out(second)
    gave_up = [r for r in caplog.records if "did not answer within" in r.getMessage()]
    assert len(gave_up) == 1, _said(caplog)
    assert gave_up[0].levelname == "WARNING"

    caplog.clear()
    run(other, timeout=GIVE_UP)
    assert (
        sum("did not answer within" in m for m in _said(caplog)) == 1
    ), "another command line timing out is news, and was not logged"

    caplog.clear()
    flag.unlink()
    assert run(command, timeout=HANG_BOUND).returncode == 0
    run(command, timeout=HANG_BOUND)
    assert sum("answers again" in m for m in _said(caplog)) == 1, _said(caplog)

    caplog.clear()
    flag.touch()
    run(command, timeout=GIVE_UP)
    assert (
        sum("did not answer within" in m for m in _said(caplog)) == 1
    ), "timing out again after it answered is a change, and was not logged"


def test_a_command_exiting_124_on_its_own_did_not_time_out() -> None:
    proc = run([sys.executable, "-c", "import sys; sys.exit(124)"], timeout=HANG_BOUND)
    assert proc.returncode == 124
    assert not runner.timed_out(proc)


# ---------------------------------------------------------------------------
# T246: on Windows a Stop ends the whole process tree it started.
#
# Measured on yulon-win11 2026-10-05 (`.notes/gates/t246-probe-yulon-win11-2026-10-05/`):
# Yu'lon's child docker.exe started docker-compose.exe, which started
# `docker-buildx.exe bake`. `terminate()` is TerminateProcess on docker.exe
# alone, so the other two ran on as orphans for 12.5 minutes and the build
# moved the live image tag 10 min 38 s after Stop. `taskkill /T /F /PID
# <docker.exe>` ended all three, the build ended in the engine as `Error`, and
# no tag moved in the 15 minutes watched.

_TREE_ROOT_PID = 4242
"""The pid the `_TreeRoot` double answers to; any value the test can recognise in an argv."""


class _TreeRoot:
    """A `Popen` double for docker.exe: alive until a tree kill, `terminate()` or `kill()` ends it.

    `events` is shared with the `subprocess.run` double, so one list says what
    happened in which order. `ignores_terminate` is a root that is still alive
    after `terminate()`, so its bounded wait expires and `kill()` is needed.
    """

    pid = _TREE_ROOT_PID

    def __init__(
        self, events: list[object], *, alive: bool = True, ignores_terminate: bool = False
    ):
        self.events = events
        self.alive = alive
        self.ignores_terminate = ignores_terminate

    def poll(self) -> int | None:
        return None if self.alive else 1

    def terminate(self) -> None:
        self.events.append("terminate")
        if not self.ignores_terminate:
            self.alive = False

    def wait(self, timeout: float | None = None) -> int:
        self.events.append("wait")
        if self.alive and timeout is not None:
            raise subprocess.TimeoutExpired("docker", timeout)
        self.alive = False
        return 1

    def kill(self) -> None:
        self.events.append("kill")
        self.alive = False


def _as_windows(monkeypatch: pytest.MonkeyPatch, platform: str = "win32") -> None:
    """Pretend to be `platform`. On win32 `CREATE_NO_WINDOW` must exist for `creationflags()`."""
    monkeypatch.setattr(runner.sys, "platform", platform)
    monkeypatch.setattr(runner.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setenv("SystemRoot", r"D:\Win")


def _taskkill_double(
    monkeypatch: pytest.MonkeyPatch,
    root: _TreeRoot,
    *,
    outcome: str = "ends the tree",
) -> list[dict[str, object]]:
    """Stand in for `subprocess.run` running taskkill; every call is recorded and returned.

    `outcome` is what the real taskkill did: ended the tree (the root is then
    dead, as on the box), exited 128 with the root still running, timed out, or
    could not be started at all.
    """
    calls: list[dict[str, object]] = []

    def fake_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        calls.append({"argv": list(argv), "root_alive": root.alive, **kw})
        root.events.append("taskkill")
        if outcome == "times out":
            raise subprocess.TimeoutExpired(argv, kw.get("timeout"))  # type: ignore[arg-type]
        if outcome == "cannot start":
            raise FileNotFoundError(2, "No such file", argv[0])
        if outcome == "exits 128":
            return subprocess.CompletedProcess(
                argv, 128, "", 'ERROR: The process "4242" not found.'
            )
        root.alive = False
        return subprocess.CompletedProcess(
            argv, 0, f"SUCCESS: The process with PID {root.pid} has been terminated.\n", ""
        )

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    return calls


def test_on_windows_a_stop_ends_the_tree_with_taskkill_before_terminate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`taskkill /T /F /PID <docker.exe>` first, while docker.exe still anchors the tree.

    Order is the point: taskkill finds descendants by their parent pid, so once
    `terminate()` has ended docker.exe the compose and buildx processes have a
    dead parent and `/T` cannot reach them from it. The system copy of
    taskkill.exe, not whatever PATH finds first; no console window; bounded.

    Mutations this catches: no tree kill at all (no call); the tree kill after
    `terminate()` (order, and `root_alive` False); `/T` or `/F` dropped, or the
    wrong pid (argv); `creationflags()` not passed; no timeout.
    """
    _as_windows(monkeypatch)
    events: list[object] = []
    root = _TreeRoot(events)
    calls = _taskkill_double(monkeypatch, root)

    runner._end_child(root)  # type: ignore[arg-type]

    assert len(calls) == 1, calls
    call = calls[0]
    assert call["argv"] == [r"D:\Win\System32\taskkill.exe", "/T", "/F", "/PID", "4242"]
    assert call["root_alive"] is True, "taskkill ran after docker.exe was already gone"
    assert events.index("taskkill") < events.index("terminate"), events
    assert call["creationflags"] == 0x08000000
    bound = call.get("timeout")
    assert isinstance(bound, (int, float)) and 0 < bound <= runner._SHUTDOWN_TIMEOUT_SECONDS
    assert not root.alive


@pytest.mark.parametrize("outcome", ["exits 128", "times out", "cannot start"])
def test_on_windows_a_taskkill_that_fails_falls_back_to_terminate(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    """A tree kill that did not end the root leaves the old ending in charge, and raises nothing.

    `exits 128` is taskkill refusing with the root still running; `times out`
    and `cannot start` are the two exceptions `subprocess.run` raises. A Stop
    must never be lost to its own helper: the worst case is today's behaviour.

    Mutations this catches: `terminate()` skipped on win32 (the root stays
    alive); either exception not caught (it escapes `_end_child`).
    """
    _as_windows(monkeypatch)
    events: list[object] = []
    root = _TreeRoot(events)
    calls = _taskkill_double(monkeypatch, root, outcome=outcome)

    runner._end_child(root)  # type: ignore[arg-type]

    assert len(calls) == 1
    assert events[:2] == ["taskkill", "terminate"], events
    assert not root.alive


def test_on_windows_without_systemroot_the_tree_kill_uses_the_default_windows_folder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset `SystemRoot` must not cost the Stop: taskkill is looked for under C:\\Windows.

    Mutation this catches (cold review N2): dropping the `C:\\Windows` default,
    after which `ntpath.join(None, ...)` raised a TypeError that no taskkill was
    ever run for.
    """
    _as_windows(monkeypatch)
    monkeypatch.delenv("SystemRoot", raising=False)
    events: list[object] = []
    root = _TreeRoot(events)
    calls = _taskkill_double(monkeypatch, root)

    runner._end_child(root)  # type: ignore[arg-type]

    assert [call["argv"] for call in calls] == [
        [r"C:\Windows\System32\taskkill.exe", "/T", "/F", "/PID", "4242"]
    ]


def test_on_windows_any_error_in_the_tree_kill_still_ends_the_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Not only the two exceptions `subprocess.run` documents: nothing may cost a Stop its ending.

    Mutation this catches (cold review N2): catching only `OSError` and
    `TimeoutExpired`, so any other error escapes `_end_child` before
    `terminate()` and the Stop ends nothing at all.
    """
    _as_windows(monkeypatch)
    events: list[object] = []
    root = _TreeRoot(events)

    def broken_run(argv: list[str], **kw: object) -> subprocess.CompletedProcess[str]:
        events.append("taskkill")
        raise RuntimeError("something nobody expected")

    monkeypatch.setattr(runner.subprocess, "run", broken_run)

    runner._end_child(root)  # type: ignore[arg-type]

    assert events[:2] == ["taskkill", "terminate"], events
    assert not root.alive


def test_on_windows_a_root_that_outlives_terminate_is_still_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last rung of the fallback: tree kill failed, terminate ignored, so `kill()`."""
    _as_windows(monkeypatch)
    events: list[object] = []
    root = _TreeRoot(events, ignores_terminate=True)
    _taskkill_double(monkeypatch, root, outcome="times out")

    runner._end_child(root)  # type: ignore[arg-type]

    assert events == ["taskkill", "terminate", "wait", "kill", "wait"], events
    assert not root.alive


def test_on_windows_a_child_that_already_exited_is_not_tree_killed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every stream's `finally` calls `_end_child`, finished or not; a finished one is left alone.

    That is what keeps a normal, completed command's ending exactly as it was:
    no taskkill is spawned after every `docker ps`.

    Mutation this catches: the tree kill moved outside the `poll() is None` check.
    """
    _as_windows(monkeypatch)
    events: list[object] = []
    root = _TreeRoot(events, alive=False)
    calls = _taskkill_double(monkeypatch, root)

    runner._end_child(root)  # type: ignore[arg-type]

    assert calls == [] and events == []


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_off_windows_a_stop_never_runs_taskkill(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """Linux, the Steam Deck and macOS keep `terminate()` then `kill()`, unchanged.

    Mutation this catches: the platform check dropped or widened (`!= "linux"`
    would still pass here on linux and fail on darwin).
    """
    _as_windows(monkeypatch, platform)
    events: list[object] = []
    root = _TreeRoot(events)
    calls = _taskkill_double(monkeypatch, root)

    runner._end_child(root)  # type: ignore[arg-type]

    assert calls == []
    assert events == ["terminate", "wait"], events


_TALKS_THEN_SLEEPS = "import sys, time\nprint('started')\nsys.stdout.flush()\ntime.sleep(60)\n"


def test_every_way_a_stream_is_stopped_ends_its_child_through_end_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stream()`, `stream_progress()` and `interact()` all end a running child through one helper.

    The tree kill lives in `_end_child`; a path with its own copy of
    terminate-then-kill would end docker.exe alone on Windows again.
    `interact()` had exactly that copy until T246. Each child is still running
    when it is stopped, so `_end_child` must have been asked about THAT child
    while it was alive — a spy that only saw finished children would prove
    nothing about the Stop.

    Mutation this catches: `interact()`'s `finally` restored to its inline
    `terminate()`/`kill()`.
    """
    alive_when_asked: list[bool] = []
    real_end = runner._end_child

    def note(proc: subprocess.Popen[str], job: object = None, **kw: bool) -> None:
        alive_when_asked.append(proc.poll() is None)
        real_end(proc, job, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "_end_child", note)

    gen = stream(_python_cmd(_TALKS_THEN_SLEEPS))
    assert next(gen) == "started"
    gen.close()

    progress = runner.stream_progress(_python_cmd(_TALKS_THEN_SLEEPS))
    assert next(progress) == "started"
    progress.close()

    cancel = threading.Event()
    said: list[str] = []
    for line in runner.interact(
        _python_cmd(_TALKS_THEN_SLEEPS), respond=lambda _line: None, cancel=cancel
    ):
        said.append(line)
        cancel.set()

    assert said == ["started"]
    assert alive_when_asked == [True, True, True], alive_when_asked


def test_only_end_child_terminates_a_child_in_the_runner() -> None:
    """The enumerating half: no function in `runner.py` but `_end_child` calls `.terminate()`.

    A per-path test covers the paths that exist today; this one fails on the
    next function that grows its own copy, which is how `interact()` came to
    miss the tree kill's predecessor fixes. `.kill()` is not audited: the one
    other call is `interact()`'s for a child whose reader thread could not even
    be started, which is not a Stop.
    """
    import ast

    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    callers: set[str] = set()
    for function in ast.walk(tree):
        if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(function):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "terminate"
            ):
                callers.add(function.name)
    assert callers == {"_end_child"}, callers


# ---------------------------------------------------------------------------
# T299: on Windows a streamed child is born into a Job object, and a Stop ends the job.
#
# `taskkill /T` (T246, above) finds a tree by parent pid at the moment of Stop,
# so it misses a descendant whose parent had already exited, one started after
# its snapshot, and can sweep in an unrelated process whose recorded parent pid
# was reused. A job tracks membership instead. The child is created suspended
# and put in the job before it runs, so nothing it starts is ever outside.
# `yulon.winjob`'s own tests cover the Win32 calls; these cover the wiring.

_CREATE_SUSPENDED = 0x00000004
_NO_WINDOW = 0x08000000
_REAL_POPEN = subprocess.Popen


class _FakeJob:
    """A `winjob.Job` double. `events` records start/end/close in order."""

    def __init__(
        self, *, starts: object = True, ends: bool = True, root: _TreeRoot | None = None
    ) -> None:
        self.starts = starts
        self.ends = ends
        self.root = root
        """A root this job holds: an `end()` that succeeds ends it, as TerminateJobObject does."""
        self.events: list[object] = []
        self.pid: int | None = None
        self.proc: subprocess.Popen[str] | None = None

    def start(self, pid: int) -> bool:
        self.events.append(("start", pid))
        self.pid = pid
        if isinstance(self.starts, BaseException):
            raise self.starts
        return bool(self.starts)

    def end(self) -> bool:
        self.events.append("end")
        if self.ends and self.root is not None:
            self.root.alive = False
        if self.ends and self.proc is not None and self.proc.poll() is None:
            # The real child of a `_windows_spawns` test: ended as the job would.
            # Through its Popen, never by a pid that may have been reaped and reused.
            self.proc.kill()
        return self.ends

    def close(self) -> None:
        self.events.append("close")

    def release(self) -> None:
        self.events.append("release")


def _windows_spawns(
    monkeypatch: pytest.MonkeyPatch, job: _FakeJob | None, *, exited: bool = False
) -> list[dict[str, object]]:
    """Pretend to be Windows with `job` as the job `winjob.create()` makes; record every Popen.

    The child is a real process, started with `creationflags=0` because POSIX
    refuses any other value: what the code ASKED for is in the record.
    """
    _as_windows(monkeypatch)
    monkeypatch.setattr(runner.winjob, "create", lambda: job)
    spawned: list[dict[str, object]] = []
    real_popen = _REAL_POPEN

    def recording_popen(argv: list[str], **kw: object) -> subprocess.Popen[str]:
        record: dict[str, object] = {"creationflags": kw.get("creationflags")}
        spawned.append(record)
        kw["creationflags"] = 0
        proc: subprocess.Popen[str] = real_popen(argv, **kw)  # type: ignore[call-overload]
        record["proc"] = proc
        if isinstance(job, _FakeJob):
            job.proc = proc
        if exited:
            proc.wait(timeout=HANG_BOUND)
        return proc

    monkeypatch.setattr(runner.subprocess, "Popen", recording_popen)
    return spawned


def test_on_windows_a_stream_child_starts_suspended_and_the_job_resumes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CREATE_SUSPENDED with the no-window flag, then `job.start(pid)`, then the job released.

    A command that ran out by itself has its job RELEASED, not closed: whatever
    it left running goes on as it did before jobs (Codex adversarial review).

    Mutations this catches: CREATE_SUSPENDED not asked for (docker.exe could
    start compose before it joined); `start` never called (the child would stay
    suspended for ever on Windows); the wrong pid; the job never let go, or
    closed with its kill-on-close still set.
    """
    job = _FakeJob()
    spawned = _windows_spawns(monkeypatch, job)

    lines = list(stream(_python_cmd("print('built')")))

    assert lines == ["built"]
    assert [record["creationflags"] for record in spawned] == [_NO_WINDOW | _CREATE_SUSPENDED]
    proc = spawned[0]["proc"]
    assert isinstance(proc, _REAL_POPEN)
    assert job.events == [("start", proc.pid), "release"]


def test_on_windows_with_no_job_the_child_is_not_started_suspended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No job means nobody would resume it: a suspended child there is a command that never runs.

    Mutation this catches: CREATE_SUSPENDED added whatever `create()` answered.
    """
    spawned = _windows_spawns(monkeypatch, None)

    assert list(stream(_python_cmd("print('built')"))) == ["built"]
    assert [record["creationflags"] for record in spawned] == [_NO_WINDOW]


def test_on_windows_a_child_that_did_not_join_its_job_is_stopped_with_taskkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`start()` answering False lets the job go at once, so a Stop falls back to taskkill.

    Mutation this catches: keeping the job, after which a Stop would end an
    empty job, call that success and never run taskkill on the real tree.
    """
    job = _FakeJob(starts=False)
    _windows_spawns(monkeypatch, job)
    lines = stream(_python_cmd("print('first', flush=True); import time; time.sleep(60)"))

    assert next(lines) == "first"
    try:
        assert job.events[1:] == ["close"], job.events
        child = runner._LIVE_STREAMS[lines]
        assert child.job is None
    finally:
        lines.close()
    assert "end" not in job.events


def test_on_windows_a_child_its_job_could_not_resume_is_killed_and_the_spawn_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A child that cannot be resumed would never run, so it is killed and the caller told.

    Mutations this catches: the child left running (or, on Windows, suspended)
    with nobody reading it; the job's handle leaked; the error swallowed so
    `stream()` hands back a command that will never produce a line.
    """
    job = _FakeJob(starts=OSError(5, "could not resume pid 1"))
    spawned = _windows_spawns(monkeypatch, job)

    with pytest.raises(OSError, match="could not resume"):
        next(stream(_python_cmd("import time; time.sleep(60)")))

    proc = spawned[0]["proc"]
    assert isinstance(proc, _REAL_POPEN)
    assert proc.poll() is not None, "the child the job could not resume is still running"
    assert job.events[-1] == "close"


def test_on_windows_a_spawn_that_fails_lets_its_job_go(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mutation this catches: a missing executable leaking a job handle per attempt."""
    job = _FakeJob()
    _windows_spawns(monkeypatch, job)

    with pytest.raises(OSError):
        next(stream(["/no/such/yulon-test-binary"]))

    assert job.events == ["close"]


def test_on_windows_abandoning_a_stream_ends_its_job_before_closing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End, then close: after `close()` the job's handle is let go and cannot end anything.

    Mutations this catches: the job not passed to `_end_child` from the
    stream's `finally` (no "end"); the job closed before it is ended.
    """
    job = _FakeJob()
    _windows_spawns(monkeypatch, job)

    def no_taskkill(argv: list[str], **kw: object) -> object:
        raise AssertionError(f"taskkill ran although the job ended the tree: {argv}")

    lines = stream(_python_cmd("print('first', flush=True); import time; time.sleep(60)"))
    assert next(lines) == "first"
    monkeypatch.setattr(runner.subprocess, "run", no_taskkill)
    lines.close()

    assert job.events[1:] == ["end", "close"], job.events


def test_on_windows_a_stop_ends_the_job_of_the_stream_the_thread_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`end_streams_started_on()` — the Stop button — hands the child's job to `_end_child`.

    Mutation this catches: the Stop thread started with the process alone, so
    the Stop falls back to taskkill and the job's reach is lost.
    """
    job = _FakeJob()
    _windows_spawns(monkeypatch, job)
    lines = stream(_python_cmd("print('first', flush=True); import time; time.sleep(60)"))
    assert next(lines) == "first"
    try:
        assert runner.end_streams_started_on(threading.get_ident()) == 1
        deadline = time.monotonic() + HANG_BOUND
        while "end" not in job.events and time.monotonic() < deadline:
            time.sleep(POLL_PACE)
        assert "end" in job.events, job.events
    finally:
        lines.close()


def test_on_windows_a_stream_ended_at_exit_ends_its_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exit hook's other branch, for a frame another thread is running, passes the job too.

    The generator here calls the hook from inside itself, which is the one way
    to make its own `close()` refuse with "generator already executing".

    Mutation this catches: `_end_child(child.proc)` without the job there.
    """
    _as_windows(monkeypatch)
    job = _FakeJob()
    events: list[object] = []
    root = _TreeRoot(events)
    taskkills = _taskkill_double(monkeypatch, root)

    def running() -> Generator[str, None, None]:
        runner._close_abandoned_streams()
        yield "done"

    generator = running()
    child = runner._Child()
    child.proc = root  # type: ignore[assignment]
    child.job = job  # type: ignore[assignment]
    runner._register(generator, child)
    try:
        assert next(generator) == "done"
    finally:
        with runner._LIVE_STREAMS_LOCK:
            runner._LIVE_STREAMS.pop(generator, None)

    assert job.events == ["end"]
    assert taskkills == []
    assert not root.alive


def test_on_windows_a_job_that_ended_the_tree_takes_the_place_of_taskkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Job ended: no taskkill and no `terminate()`, only the reap. Job failed: taskkill, as T246.

    `TerminateJobObject` ended docker.exe with the rest, so it is waited for and
    not terminated a second time (Codex's third adversarial review).

    Mutations this catches: taskkill run even after the job ended the tree; the
    job's failure not falling back to taskkill; `terminate()` dropped from the
    fallback.
    """
    _as_windows(monkeypatch)
    events: list[object] = []
    root = _TreeRoot(events)
    calls = _taskkill_double(monkeypatch, root)
    ended = _FakeJob(ends=True, root=root)

    runner._end_child(root, ended)  # type: ignore[arg-type]

    assert ended.events == ["end"]
    assert calls == []
    assert events == ["wait"], events

    events.clear()
    root.alive = True
    failed = _FakeJob(ends=False)

    runner._end_child(root, failed)  # type: ignore[arg-type]

    assert failed.events == ["end"]
    assert len(calls) == 1
    assert events[:2] == ["taskkill", "terminate"], events


def test_on_windows_stream_progress_runs_its_child_in_a_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The git clone a Stop can end goes through the same spawn.

    Mutation this catches: `stream_progress()` left on a bare `Popen`.
    """
    job = _FakeJob()
    spawned = _windows_spawns(monkeypatch, job)

    assert list(runner.stream_progress(_python_cmd("print('cloned')"))) == ["cloned"]
    assert [record["creationflags"] for record in spawned] == [_NO_WINDOW | _CREATE_SUSPENDED]
    assert job.events[-1] == "release" and job.events[0][0] == "start"  # type: ignore[index]


def test_on_windows_interact_runs_its_child_in_a_job(monkeypatch: pytest.MonkeyPatch) -> None:
    """`interact()` ends its child with `_end_child` too, so it gets the same job.

    Mutation this catches: `interact()`'s pipe branch left on a bare `Popen`.
    """
    job = _FakeJob()
    spawned = _windows_spawns(monkeypatch, job)

    out = list(runner.interact(_python_cmd("print('asked')"), respond=lambda line: None))

    assert "asked" in out
    assert [record["creationflags"] for record in spawned] == [_NO_WINDOW | _CREATE_SUSPENDED]
    assert job.events[-1] == "release" and job.events[0][0] == "start"  # type: ignore[index]


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_off_windows_no_job_is_ever_made(monkeypatch: pytest.MonkeyPatch, platform: str) -> None:
    """Linux and macOS spawn exactly as before.

    Mutation this catches: the platform check dropped, so `winjob.create()`
    (which would load kernel32) runs on every host.
    """
    _as_windows(monkeypatch, platform)

    def no_job() -> object:
        raise AssertionError("a job was made off Windows")

    monkeypatch.setattr(runner.winjob, "create", no_job)

    assert list(stream(_python_cmd("print('built')"))) == ["built"]


class _ThreadThatCannotStart(threading.Thread):
    """`RuntimeError: can't start new thread`, the failure a long-lived GUI process can meet."""

    def start(self) -> None:
        raise RuntimeError("can't start new thread")


@pytest.mark.parametrize("entry", ["stream", "stream_progress", "interact"])
def test_on_windows_a_reader_that_cannot_start_still_ends_and_closes_the_job(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """The child is running and in its job before the reader starts: a failed start ends both.

    From both Codex reviews of T299: the reader was started before the
    `try`/`finally` that ends the child, so the job's handle stayed open and the
    tree ran on. Closing the job (kill-on-close) is what reaches the tree.

    Mutation this catches: the failed start leaving the job open.
    """
    job = _FakeJob()
    spawned = _windows_spawns(monkeypatch, job)
    monkeypatch.setattr(runner.threading, "Thread", _ThreadThatCannotStart)
    command = _python_cmd("import time; time.sleep(60)")

    with pytest.raises(RuntimeError, match="can't start new thread"):
        if entry == "stream":
            next(stream(command))
        elif entry == "stream_progress":
            next(runner.stream_progress(command))
        else:
            next(iter(runner.interact(command, respond=lambda line: None)))

    proc = spawned[0]["proc"]
    assert isinstance(proc, _REAL_POPEN)
    assert proc.poll() is not None, "the child is still running"
    assert job.events[-1] == "close", job.events
    assert "release" not in job.events


@pytest.mark.parametrize("entry", ["stream", "stream_progress"])
def test_on_windows_a_stopped_stream_whose_root_exits_first_still_closes_its_job(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """A Stop already chosen wins over "it ran out by itself": the job is closed, not released.

    From Codex's second adversarial review: Stop marks the stream and hands
    `_end_child` to a thread; if docker.exe exits before that thread runs, the
    thread finds nothing alive and never ends the job, and the stream's
    `finally` saw a root that had exited and released the job, leaving the
    rest of the tree running. Here the root exits, the stream is marked as a
    Stop marks it, and the `finally` runs: kill-on-close must still be set.

    Mutation this catches: `_finish` deciding from `poll()` alone.
    """
    job = _FakeJob()
    spawned = _windows_spawns(monkeypatch, job)
    start = stream if entry == "stream" else runner.stream_progress
    lines = start(_python_cmd("print('first', flush=True)"))
    assert next(lines) == "first"
    proc = spawned[0]["proc"]
    assert isinstance(proc, _REAL_POPEN)
    proc.wait(timeout=HANG_BOUND)
    runner._LIVE_STREAMS[lines].ended = True

    assert list(lines) == []

    assert job.events[-1] == "close", job.events
    assert "release" not in job.events


def test_on_windows_a_root_that_outlives_its_ended_job_is_still_terminated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reap after a job's end is bounded; a root still there afterwards gets the old ending.

    Mutation this catches: returning after the bounded wait whether or not the
    root exited.
    """
    _as_windows(monkeypatch)
    events: list[object] = []
    root = _TreeRoot(events)
    _taskkill_double(monkeypatch, root)
    job = _FakeJob(ends=True)  # reports success, but the root is not in it

    runner._end_child(root, job)  # type: ignore[arg-type]

    assert events[:2] == ["wait", "terminate"], events
    assert not root.alive


def test_on_windows_an_error_while_ending_the_child_still_closes_its_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The job is let go in a `finally`, whatever `_end_child` did (Codex, third review).

    Mutation this catches: the close skipped when `_end_child` raises.
    """
    _as_windows(monkeypatch)
    root = _TreeRoot([])
    job = _FakeJob()

    def broken(proc: object, job: object = None, **kw: object) -> None:
        raise PermissionError(5, "Access is denied")

    monkeypatch.setattr(runner, "_end_child", broken)

    with pytest.raises(PermissionError):
        runner._finish(root, job)  # type: ignore[arg-type]

    assert job.events == ["close"]


def test_on_windows_a_cancelled_interact_closes_its_job_even_when_the_root_exited_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`interact()`'s cancel is its Stop: kill-on-close stays, whatever `poll()` says by then.

    From Codex's third review. The child here has exited before the loop first
    looks at `cancel`, which is the order that released the job.

    Mutation this catches: `interact()` passing no `stopped` to `_finish`.
    """
    job = _FakeJob()
    _windows_spawns(monkeypatch, job, exited=True)
    cancel = threading.Event()
    cancel.set()

    list(runner.interact(_python_cmd("pass"), respond=lambda line: None, cancel=cancel))

    assert job.events[-1] == "close", job.events
    assert "release" not in job.events


def test_on_windows_a_stop_reaches_a_stream_whose_root_exited_but_whose_job_is_unsettled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """docker.exe gone, its tree still in the job and still writing: Stop ends the job.

    From Codex's fourth adversarial review. Descendants inherit the output pipe,
    so the stream reads on after its root exits, and a Stop that chose streams
    by `poll()` alone skipped it. The job can end the tree without its root.

    Mutations this catches: selection by `poll()` alone (0 streams ended); the
    Stop thread gating `job.end()` on a live root (no "end").
    """
    job = _FakeJob()
    spawned = _windows_spawns(monkeypatch, job)
    lines = stream(_python_cmd("print('first', flush=True)"))
    assert next(lines) == "first"
    proc = spawned[0]["proc"]
    assert isinstance(proc, _REAL_POPEN)
    proc.wait(timeout=HANG_BOUND)
    try:
        assert runner.end_streams_started_on(threading.get_ident()) == 1
        deadline = time.monotonic() + HANG_BOUND
        while "end" not in job.events and time.monotonic() < deadline:
            time.sleep(POLL_PACE)
        assert "end" in job.events, job.events
    finally:
        lines.close()
    assert job.events[-1] == "close", job.events


def test_on_windows_a_stop_after_a_stream_let_its_job_go_ends_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Once a finished stream has released its job, it is history: a later Stop leaves it alone.

    Mutation this catches: selecting every child with a job, settled or not.
    """
    job = _FakeJob()
    _windows_spawns(monkeypatch, job)
    lines = stream(_python_cmd("print('first', flush=True)"))
    assert list(lines) == ["first"]
    assert job.events[-1] == "release"

    assert runner.end_streams_started_on(threading.get_ident()) == 0
    assert "end" not in job.events


@pytest.mark.parametrize("where", ["in the registry walk", "in the choice"])
def test_on_windows_a_stream_finalised_while_a_stop_is_choosing_does_not_deadlock(
    monkeypatch: pytest.MonkeyPatch, where: str
) -> None:
    """A stream whose last reference drops while a Stop is choosing ends without a deadlock.

    From the cold review of T299. `end_streams_started_on()` walks the weak
    registry under `_LIVE_STREAMS_LOCK`; a generator whose owner dropped it can
    be finalised right there, on that thread, when `WeakKeyDictionary.values()`
    lets go of its strong reference, or in a GC pass. Its `finally` runs
    `_finish`, which must not need a lock that thread already holds.

    `in the registry walk` drops the last reference inside `values()`, under
    the registry lock; `in the choice` drops it while the child's own lock is
    held. The Stop runs on its own thread with a registry lock of its own, so
    a deadlock fails this test instead of hanging the suite. Both deadlocked
    (timed out at `HANG_BOUND`) on the code the cold review read.

    Mutations this catches: `_finish` taking `_LIVE_STREAMS_LOCK` (walk); the
    child's lock not re-entrant (choice).
    """
    holder: list[Generator[str, None, None]] = []

    class FinalisingRegistry(weakref.WeakKeyDictionary):  # type: ignore[type-arg]
        def values(self):  # type: ignore[no-untyped-def]
            for value in super().values():
                if where == "in the registry walk" and holder:
                    holder.pop()  # the last reference: finalised here, under the lock
                yield value

    job = _FakeJob()
    _windows_spawns(monkeypatch, job)
    monkeypatch.setattr(runner, "_LIVE_STREAMS_LOCK", threading.Lock())
    monkeypatch.setattr(runner, "_LIVE_STREAMS", FinalisingRegistry())
    holder.append(stream(_python_cmd("print('first', flush=True); import time; time.sleep(60)")))
    assert next(holder[0]) == "first"
    ident = threading.get_ident()
    real_still_running = runner._still_running

    def finalising(proc: object) -> bool:
        if where == "in the choice" and holder:
            holder.pop()  # the last reference: finalised here, under the child's lock
        return real_still_running(proc)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "_still_running", finalising)
    answered: list[int] = []
    stop = threading.Thread(
        target=lambda: answered.append(runner.end_streams_started_on(ident)), daemon=True
    )
    stop.start()
    stop.join(HANG_BOUND)

    assert not holder, "the stream was never finalised where the test meant it to be"
    assert answered, f"the Stop deadlocked on a stream finalised {where}"
    assert job.events[-1] == "close", job.events


class _JobHoldingAPid(_FakeJob):
    """A job double whose `end()` ends one more process, the way the real job ends a descendant."""

    def __init__(self) -> None:
        super().__init__()
        self.member: int | None = None

    def end(self) -> bool:
        ended = super().end()
        if self.member is not None:
            os.kill(self.member, signal.SIGKILL)
        return ended


@pytest.mark.parametrize("entry", ["stream", "stream_progress"])
def test_on_windows_a_stop_that_ends_the_tree_after_its_root_exited_0_is_reported_as_a_stop(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """docker.exe exited 0, its tree was still writing, Stop ended the tree: that is a Stop.

    From the cold review of T299: the exit status alone read as success,
    although the work the root had handed its descendants was cut off.

    `stream_progress` is the git clone's path, from the scoped review of
    d3252e19: its own check could drop the flag with every test still green.

    Mutations this catches: either generator deciding from `returncode` alone;
    the error carrying exit 0, which `docker.run_attached()` would report as
    success.
    """
    job = _JobHoldingAPid()
    spawned = _windows_spawns(monkeypatch, job)
    # The root starts a grandchild that inherits the pipe, says its pid and
    # writes on; the root itself exits 0 at once.
    script = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', "
        "\"import os, time; print('pid', os.getpid(), flush=True); time.sleep(60)\"]); "
        "print('first', flush=True)"
    )
    start = stream if entry == "stream" else runner.stream_progress
    lines = start(_python_cmd(script))
    seen = [next(lines), next(lines)]
    said = next(line for line in seen if line.startswith("pid "))
    job.member = int(said.split()[1])
    proc = spawned[0]["proc"]
    assert isinstance(proc, _REAL_POPEN)
    assert proc.wait(timeout=HANG_BOUND) == 0

    try:
        assert runner.end_streams_started_on(threading.get_ident()) == 1
        with pytest.raises(runner.StreamEnded) as raised:
            list(lines)
    finally:
        lines.close()
    assert raised.value.returncode == 1, "a Stop must not read as exit 0"


class _JobKillingOnClose(_JobHoldingAPid):
    """`_JobHoldingAPid` whose `close()` also ends its member, as KILL_ON_JOB_CLOSE does.

    `release()` clears that flag first, so it ends nothing: the member goes on.
    """

    def close(self) -> None:
        super().close()
        if self.member is not None:
            try:
                os.kill(self.member, signal.SIGKILL)
            except ProcessLookupError:
                pass


def _gone(pid: int) -> bool:
    """True once `pid` has exited (a zombie counts: it runs nothing), within `HANG_BOUND`."""
    deadline = time.monotonic() + HANG_BOUND
    while time.monotonic() < deadline:
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as stat:
                if stat.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return True
        except (FileNotFoundError, ProcessLookupError):
            # Reaped before the open, or between the open and the read (ESRCH,
            # seen once in the mutation runs of 2026-10-07).
            return True
        time.sleep(POLL_PACE)
    return False


_ROOT_EXITS_GRANDCHILD_WRITES = (
    "import subprocess, sys; "
    "subprocess.Popen([sys.executable, '-c', "
    "\"import os, time\\nprint('pid', os.getpid(), flush=True)\\n"
    "while True:\\n    print('compiling', flush=True); time.sleep(0.05)\"], "
    "stderr=subprocess.DEVNULL); "
    "print('first', flush=True)"
)
"""A root that starts a grandchild on its own output pipe and exits 0 at once.

The grandchild says its pid, then writes a line every 50 ms for as long as it
lives: docker.exe ended by hand while docker-compose.exe and `buildx bake` go on
compiling into the pipe (T495, yulon-win11-gate 2026-10-06, step 2).
"""


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")
@pytest.mark.parametrize("entry", ["stream", "stream_progress"])
def test_on_windows_a_stream_closed_before_eof_after_its_root_died_ends_its_job(
    monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    """Root gone, tree still writing, consumer closes early: the job is closed, never released.

    T495, seen live on yulon-win11-gate 2026-10-06 (T299 step 2): Rebuild,
    docker.exe ended by hand, compose and buildx compiling on into the pipe,
    then Stop. The stream had been started on `native._pump()`'s worker, so the
    panel's `end_streams_started_on()` did not find it; the cancel event made
    `docker.run_attached()` return at the next line and close the generator.
    Its `finally` saw a root that had exited and read that as "ran out by
    itself": the job was RELEASED and the build compiled on for 11 minutes.

    A stream abandoned before its pipe reached EOF did not run out: somebody
    is still holding the pipe, and that somebody is the command's own work.

    Mutation this catches: `_finish` deciding "ran out" from `poll()` alone,
    without asking whether the stream was read to its end.
    """
    job = _JobKillingOnClose()
    spawned = _windows_spawns(monkeypatch, job)
    start = stream if entry == "stream" else runner.stream_progress
    lines = start(_python_cmd(_ROOT_EXITS_GRANDCHILD_WRITES))
    try:
        while job.member is None:
            line = next(lines)
            if line.startswith("pid "):
                job.member = int(line.split()[1])
        proc = spawned[0]["proc"]
        assert isinstance(proc, _REAL_POPEN)
        assert proc.wait(timeout=HANG_BOUND) == 0
        assert next(lines) in ("compiling", "first")  # the tree still writes, root or no root

        lines.close()  # `run_attached()`'s `closing` on a cancel: GeneratorExit at the yield

        assert "release" not in job.events, job.events
        assert job.events[-1] == "close", job.events
        assert _gone(job.member), "the job's tree outlived its stream"
    finally:
        lines.close()
        if job.member is not None and not _gone(job.member):
            os.kill(job.member, signal.SIGKILL)


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="reads /proc")
@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_off_windows_a_stream_closed_before_eof_after_its_root_died_is_as_before(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    """Off Windows T495 changes nothing: no job is made, and nothing signals the leftover tree.

    The same shape as the Windows test above. Off Windows a closed stream ends
    its root if the root still runs and nothing else; T298 measured on Linux
    that compose and `buildx bake` end with the docker CLI, so a dead root's
    leftovers are not this code's to end. The grandchild is still running
    after the close, as it was before T495.

    Mutation this catches: the early-close ending reaching a POSIX stream
    (signalling the tree, or making a job off Windows).
    """
    _as_windows(monkeypatch, platform)
    made: list[object] = []
    monkeypatch.setattr(runner.winjob, "create", lambda: made.append("job"))
    lines = stream(_python_cmd(_ROOT_EXITS_GRANDCHILD_WRITES))
    member: int | None = None
    try:
        while member is None:
            line = next(lines)
            if line.startswith("pid "):
                member = int(line.split()[1])
        child = runner._LIVE_STREAMS[lines]
        assert child.proc is not None
        assert child.proc.wait(timeout=HANG_BOUND) == 0

        lines.close()

        assert made == []
        assert child.job is None
        os.kill(member, 0)  # still there: raises ProcessLookupError if it was ended
        with open(f"/proc/{member}/stat", encoding="utf-8") as stat:
            assert stat.read().rsplit(")", 1)[1].split()[0] != "Z"
    finally:
        lines.close()
        if member is not None:
            os.kill(member, signal.SIGKILL)


def test_on_windows_a_stop_after_the_stream_gave_its_answer_leaves_it_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reader's answer and a Stop's choice are one decision: whichever is first wins whole.

    From the scoped review of d3252e19: the flag that turns a success into a
    Stop was set on the Stop's thread after the choice, so a reader that had
    just passed its exit check reported success while `_finish` then closed
    the job on the tree. Here the Stop lands in exactly that window, between
    the answer and `_finish`: it must find the stream answered and leave it
    alone, and the job is released as for any command that finished.

    Mutations this catches: the choice not skipping an answered stream (the
    Stop ends the job of a command reported as a success).
    """
    job = _FakeJob()
    _windows_spawns(monkeypatch, job)
    real_finish = runner._finish
    chosen: list[int] = []

    def stop_in_the_window(*args: object, **kw: object) -> None:
        chosen.append(runner.end_streams_started_on(threading.get_ident()))
        real_finish(*args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(runner, "_finish", stop_in_the_window)

    assert list(stream(_python_cmd("print('built')"))) == ["built"]

    assert chosen == [0]
    assert "end" not in job.events
    assert job.events[-1] == "release", job.events


def test_off_windows_a_root_that_exits_0_after_a_stop_chose_it_is_still_a_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a job nothing ends a root that has exited, so its exit status is the whole story.

    The Stop here chooses the stream while the root runs, and the root exits 0
    on its own before the Stop's thread could end anything (the thread is
    stubbed out to hold that order). Linux and macOS report it as before T299.

    Mutation this catches: `cut_short` set for a child with no job.
    """
    _as_windows(monkeypatch, "linux")
    monkeypatch.setattr(runner, "_stop_child", lambda child: None)
    lines = stream(_python_cmd("print('first', flush=True); import time; time.sleep(0.3)"))
    assert next(lines) == "first"

    assert runner.end_streams_started_on(threading.get_ident()) == 1
    assert list(lines) == []


# ---------------------------------------------------------------------------
# T365: a Stop whose own thread cannot start still ends the stream.
#
# `end_streams_started_on()` hands each ending to a thread, so the button that
# called it never waits on a child. `Thread.start()` can refuse ("can't start new
# thread") in a long-lived process that has run out of them; before T365 that
# error left the Stop on the first child, every later child was never asked, and
# `ended` claimed a Stop that never happened while the stream stayed blocked.


def test_a_stop_whose_thread_cannot_start_still_ends_the_stream_a_worker_is_blocked_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stream's worker leaves its blocked read, and its exit reads as the Stop's.

    Mutation this catches: no fallback (the `RuntimeError` escapes the Stop and
    the worker stays inside the generator).
    """
    blocked = _BlockedStream("test-stop-thread-cannot-start")
    try:
        monkeypatch.setattr(runner.threading, "Thread", _ThreadThatCannotStart)
        try:
            assert runner.end_streams_started_on(blocked.worker.ident) == 1
        finally:
            monkeypatch.undo()
        blocked.worker.join(timeout=HANG_BOUND)
        assert not blocked.worker.is_alive(), "still inside the generator: the child was not ended"
        assert isinstance(blocked.outcome[0], runner.StreamEnded), blocked.outcome
    finally:
        blocked.close()


def test_a_stop_whose_threads_cannot_start_ends_every_stream_not_only_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two streams this thread started: both children are ended when no thread will start.

    Mutation this catches: the fallback ending the first child and then
    stopping (a `break`, or the start's error raised again after it).
    """
    script = "print('first', flush=True); import time; time.sleep(60)"
    streams = [stream(_python_cmd(script)), stream(_python_cmd(script))]
    try:
        for lines in streams:
            assert next(lines) == "first"
        procs = [runner._LIVE_STREAMS[lines].proc for lines in streams]
        monkeypatch.setattr(runner.threading, "Thread", _ThreadThatCannotStart)
        try:
            assert runner.end_streams_started_on(threading.get_ident()) == 2
        finally:
            monkeypatch.undo()
        for proc in procs:
            assert proc is not None
            assert proc.wait(timeout=HANG_BOUND) is not None
    finally:
        for lines in streams:
            lines.close()


class _RootThatOutlivesKill(_TreeRoot):
    """A root that `kill()` does not end: the `proc.rs` case, where a wait after it never returns.

    An unbounded `wait()` is recorded rather than blocked on, so the test can
    say what the Stop would have done instead of hanging in it.
    """

    def kill(self) -> None:
        self.events.append("kill")

    def wait(self, timeout: float | None = None) -> int:
        if timeout is None:
            self.events.append("unbounded wait")
            return 1
        self.events.append("wait")
        raise subprocess.TimeoutExpired("docker", timeout)


def _registered(child_proc: object) -> tuple[Generator[str, None, None], runner._Child]:
    """A stream entry for `child_proc`, started on this thread; the caller unregisters it."""

    def body() -> Generator[str, None, None]:
        yield "never read"

    generator = body()
    child = runner._Child()
    child.proc = child_proc  # type: ignore[assignment]
    child.started_on = threading.get_ident()
    runner._register(generator, child)
    return generator, child


def _unregister(*generators: Generator[str, None, None]) -> None:
    with runner._LIVE_STREAMS_LOCK:
        for generator in generators:
            runner._LIVE_STREAMS.pop(generator, None)


def test_a_stop_ended_on_its_own_thread_never_waits_without_a_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback runs on the thread that painted Stop, so its every wait is bounded.

    Mutation this catches: the fallback calling the threaded ending as it is,
    whose `kill()` is followed by an unbounded `wait()` (`proc.rs`: a 600 ms
    call that returned after 605 seconds).
    """
    _as_windows(monkeypatch, "linux")
    events: list[object] = []
    root = _RootThatOutlivesKill(events, ignores_terminate=True)
    generator, child = _registered(root)
    try:
        monkeypatch.setattr(runner.threading, "Thread", _ThreadThatCannotStart)
        assert runner.end_streams_started_on(threading.get_ident()) == 1
    finally:
        _unregister(generator)
    assert events == ["terminate", "wait", "kill"], events
    assert child.ended


def test_a_stop_whose_inline_ending_fails_still_asks_every_other_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error ending one child on the Stop's own thread does not skip the next one.

    Mutation this catches: the fallback's error escaping the loop.
    """
    _as_windows(monkeypatch, "linux")

    class _RootThatCannotBeTerminated(_TreeRoot):
        def terminate(self) -> None:
            self.events.append("terminate")
            raise OSError(1, "Operation not permitted")

    first: list[object] = []
    second: list[object] = []
    entries = [_registered(_RootThatCannotBeTerminated(log)) for log in (first, second)]
    try:
        monkeypatch.setattr(runner.threading, "Thread", _ThreadThatCannotStart)
        assert runner.end_streams_started_on(threading.get_ident()) == 2
    finally:
        _unregister(*(generator for generator, _ in entries))
    assert first == ["terminate"] and second == ["terminate"], (first, second)


def test_on_windows_a_stop_whose_thread_cannot_start_ends_the_job_on_its_own_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback is the same ending as the thread's: the job first, before the call returns.

    Mutation this catches: the fallback ending the process alone, so the
    job's reach (everything docker.exe started) is lost.
    """
    job = _FakeJob()
    spawned = _windows_spawns(monkeypatch, job)
    lines = stream(_python_cmd("print('first', flush=True); import time; time.sleep(60)"))
    assert next(lines) == "first"
    try:
        monkeypatch.setattr(runner.threading, "Thread", _ThreadThatCannotStart)
        assert runner.end_streams_started_on(threading.get_ident()) == 1
        assert "end" in job.events, job.events
        proc = spawned[0]["proc"]
        assert isinstance(proc, _REAL_POPEN)
        assert proc.wait(timeout=HANG_BOUND) is not None
    finally:
        lines.close()
    assert job.events[-1] == "close", job.events


def test_on_windows_a_stream_started_in_a_job_still_gets_the_environment_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`stream(env=...)` (T376) reaches the child through the job's spawn too.

    Guards the merge of T299's suspended spawn with T376's `env`: the two met
    in the same `Popen` call. Mutation this catches: `child_env()` without the
    caller's `env` inside `_spawn`'s lambda.
    """
    job = _FakeJob()
    _windows_spawns(monkeypatch, job)
    env = {**os.environ, "YULON_T299_ENV_PROBE": "given"}

    lines = list(
        stream(_python_cmd("import os; print(os.environ['YULON_T299_ENV_PROBE'])"), env=env)
    )

    assert lines == ["given"]


# ------------------------------------------------- T529: a Stop ends what git started too

# A root that starts a child of its own and lets it hold the output pipe, as `git
# clone` leaves `index-pack` and `git checkout` its own `git fetch` (T529, m910q
# 2026-10-07): the grandchild's pid is the first line, then both sleep.
_GRANDCHILD_HOLDS_THE_PIPE = (
    "import subprocess, sys, time; "
    "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
    "print(kid.pid, flush=True); time.sleep(600)"
)


def _gone(pid: int) -> bool:
    """Has `pid` exited? A zombie waiting for its new parent to reap it counts as exited."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    stat = Path(f"/proc/{pid}/stat")
    try:
        return stat.read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except (OSError, IndexError):
        return False


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups; Windows ends a job")
def test_a_stop_ends_what_a_stream_progress_child_started_and_the_read_returns() -> None:
    """T529: the Stop reaches the whole clone, not just the `git` it started.

    Measured on m910q 2026-10-07 (`.notes/gates/probe-t529-t526-2026-10-07/`): a
    Stop during `Resolving deltas` signalled `git clone` alone, `index-pack` ran on
    for 13.8 s with PPID 1, and because it held the stream's pipe the read -- and
    so the Stop -- did not end until it did. A blob-less checkout's own `git fetch`
    kept downloading for 36 s the same way.

    Mutation this catches: `_progress_lines()` started without its own session, or
    `_end_child()` signalling the root and not its group. The grandchild sleeps
    600 s and holds the pipe, so the worker is still reading at `HANG_BOUND`.
    """
    generator = runner.stream_progress(_python_cmd(_GRANDCHILD_HOLDS_THE_PIPE))
    fragments: queue.Queue[str] = queue.Queue()
    outcome: list[BaseException] = []

    def work() -> None:
        try:
            for fragment in generator:
                fragments.put(fragment)
        except BaseException as exc:  # noqa: BLE001 - the shape under test
            outcome.append(exc)

    worker = threading.Thread(target=work, daemon=True, name="test-t529-worker")
    worker.start()
    grandchild = int(fragments.get(timeout=HANG_BOUND))
    try:
        assert runner.end_streams_started_on(worker.ident or 0) == 1
        worker.join(timeout=HANG_BOUND)
        assert not worker.is_alive(), "the read waited on a grandchild the Stop never reached"
        assert isinstance(outcome[0], runner.StreamEnded), outcome
        deadline = time.monotonic() + HANG_BOUND
        while not _gone(grandchild) and time.monotonic() < deadline:
            time.sleep(POLL_PACE)
        assert _gone(grandchild), f"the root's own child {grandchild} outlived the Stop"
    finally:
        try:
            os.kill(grandchild, signal.SIGKILL)
        except OSError:
            pass
        worker.join(timeout=HANG_BOUND)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX sessions; Windows is unchanged")
def test_only_stream_progress_starts_its_child_in_a_session_of_its_own(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T529's scope: host git gets a session (and no terminal input); docker streams do not.

    `stream()` runs only the docker CLI off Windows, and T298 measured that ending
    the CLI alone ends compose and its build. A group signal would also reach
    compose directly, which for `compose run` could stop a container its stage
    says keeps running, so `stream()` stays root-only until that is probed.
    `stdin` is closed for the session's child because a git that asks for input
    must fail, not wait on a terminal it no longer owns.

    Mutation this catches: dropping `start_new_session` from `_progress_lines()`,
    or adding it to `_stream_lines()`.
    """
    asked: list[dict[str, object]] = []

    class _Spy(_Recorded):
        def __init__(self, *a: object, **kw: object) -> None:
            asked.append(dict(kw))
            super().__init__(*a, **kw)

    _Recorded.out, _Recorded.err, _Recorded.code = b"", b"", 0
    monkeypatch.setattr(runner.subprocess, "Popen", _Spy)
    list(runner.stream_progress(["git", "clone", "x"]))
    # Never a real spawn (`_Spy`), and not spelled `docker`: the suite refuses that argv.
    list(runner.stream(["the-docker-cli", "compose", "build"]))

    git_spawn, docker_spawn = asked
    assert git_spawn.get("start_new_session") is True
    assert git_spawn.get("stdin") == subprocess.DEVNULL
    assert not docker_spawn.get("start_new_session")


def test_a_process_this_module_did_not_start_is_never_signalled_as_a_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A group signal goes only to a group a real child of ours leads (T529 adversarial focus).

    `os.killpg(pid, ...)` reaches every process in group `pid`. The double here has
    a pid (4321) that may well be some real process's group on the box running the
    suite; nothing this module spawned leads it, so nothing may be sent to it.

    Mutation this catches: taking the group from `proc.pid` alone, without asking
    whether `proc` is a child this module started as its session's leader.
    """
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(runner.os, "killpg", lambda pid, sig: signalled.append((pid, sig)))
    _Recorded.out, _Recorded.err, _Recorded.code = b"", b"Receiving objects:   1%\r" * 50, 0
    monkeypatch.setattr(runner.subprocess, "Popen", _Recorded)
    generator = runner.stream_progress(["git", "clone", "x"])
    next(generator)
    assert runner.end_streams_started_on(threading.get_ident()) == 1
    generator.close()
    assert signalled == []


# The root has gone and its child still holds the pipe: what a Stop meets when the
# leader exits a moment before the Stop's thread acts (Codex review of T529).
_ROOT_GONE_GRANDCHILD_HOLDS_THE_PIPE = (
    "import subprocess, sys; "
    "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)']); "
    "print(kid.pid, flush=True)"
)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups; Windows ends a job")
def test_a_stop_ends_the_group_of_a_stream_progress_root_that_has_already_exited() -> None:
    """T529, Codex's P1: a leader that exited does not take its group out of the Stop's reach.

    The stream is still reading -- a member of the group holds its pipe -- so it is
    still the job a Stop means, exactly as an unsettled Windows job is (T299). The
    group's id cannot belong to anything else while a member is alive.

    Mutation this catches: choosing only streams whose root is still running, or a
    `_stop_child` that does nothing once the root has exited (the worker is still
    reading at `HANG_BOUND`: the grandchild sleeps 600 s).
    """
    generator = runner.stream_progress(_python_cmd(_ROOT_GONE_GRANDCHILD_HOLDS_THE_PIPE))
    fragments: queue.Queue[str] = queue.Queue()
    outcome: list[BaseException] = []

    def work() -> None:
        try:
            for fragment in generator:
                fragments.put(fragment)
        except BaseException as exc:  # noqa: BLE001 - the shape under test
            outcome.append(exc)

    worker = threading.Thread(target=work, daemon=True, name="test-t529-root-gone")
    worker.start()
    grandchild = int(fragments.get(timeout=HANG_BOUND))
    try:
        with runner._LIVE_STREAMS_LOCK:
            root = next(
                c.proc for c in runner._LIVE_STREAMS.values() if c.started_on == worker.ident
            )
        assert root is not None
        root.wait(timeout=HANG_BOUND)  # the leader is gone; its child reads on
        assert runner.end_streams_started_on(worker.ident or 0) == 1
        worker.join(timeout=HANG_BOUND)
        assert not worker.is_alive(), "the Stop never reached the group of a root that had exited"
        assert isinstance(outcome[0], runner.StreamEnded), outcome
        deadline = time.monotonic() + HANG_BOUND
        while not _gone(grandchild) and time.monotonic() < deadline:
            time.sleep(POLL_PACE)
        assert _gone(grandchild), f"the exited root's child {grandchild} outlived the Stop"
    finally:
        try:
            os.kill(grandchild, signal.SIGKILL)
        except OSError:
            pass
        worker.join(timeout=HANG_BOUND)
