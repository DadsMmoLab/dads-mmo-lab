"""Tests for `LogPanel` (roadmap 4.1): a job streams in without blocking, and finishes cleanly."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator

import pytest

from tests.conftest import (
    HANG_BOUND,
    HANG_BOUND_MS,
    JOB_PACE,
    PUMPED_HEALTHY,
    process_events,
    pump_until,
    spelled_bounds,
    wait_for_panel,
)
from yulon.ui.widgets.log_panel import LogPanel

STAMP = re.compile(r"^\[(\d\d:\d\d:\d\d)\] ")
"""The wall clock `append()` puts on every line. Elapsed is a header field, not a prefix."""

DWELL_PROOF = 1.2
"""The one bound here that IS an assertion, and it points the other way.

`test_the_elapsed_field_stops_counting_when_the_job_ends_but_keeps_its_total`
requires the elapsed field NOT to move while this elapses, so a slow or loaded
box makes it pass more surely rather than less. It is small for the same reason
`HANG_BOUND` is large. `CLOCK_GAP` is the same shape.
"""

CLOCK_GAP = 0.05
"""Also an assertion, also pointing the other way.

`test_the_elapsed_clock_counts_from_this_run_and_not_from_the_last_one` needs
the second run's zero to be strictly later than the first's; any real delay
between them proves it, and load only widens the gap.
"""

EXPIRY_PROBE = 0.05
"""The bound `test_an_expired_pump_says_so_and_reports_how_fast_it_ran` MUST reach.

The same shape as `DWELL_PROOF`: it points the other way, and a loaded box
reaches it more surely. It is the one place in the suite where `pump_until`'s
deadline is meant to fire, and the number is small because the test's whole
cost is this wait -- at 0.05s it costs about as much as one `process_events`
slice, and the report it exists to pin says the same thing at any size.
"""


def _unstamped(panel: LogPanel) -> list[str]:
    """The panel's lines with their stamps removed, and the stamp REQUIRED to be there.

    A helper that merely stripped an optional prefix would keep every test
    below green if stamping stopped happening, which is the failure it would be
    hiding. `sub(count=1)` so a line whose own text looks like a stamp keeps it.
    """
    lines = panel.text().splitlines()
    for line in lines:
        assert STAMP.match(line), f"line reached the panel unstamped: {line!r}"
    return [STAMP.sub("", line, count=1) for line in lines]


def test_lines_stream_in_on_a_background_thread_and_finish(qapp: object) -> None:
    panel = LogPanel()
    ui_thread = threading.get_ident()
    seen_threads: set[int] = set()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))

    def source() -> Iterator[str]:
        seen_threads.add(threading.get_ident())
        for i in range(3):
            yield f"line {i}"
            time.sleep(JOB_PACE)

    assert panel.run(source, title="job") is True
    assert panel.run(source) is False  # busy
    wait_for_panel(panel)
    assert _unstamped(panel) == ["line 0", "line 1", "line 2"]
    assert seen_threads and ui_thread not in seen_threads
    assert finished == [(True, "done")]
    assert panel.running is False


def test_job_exception_becomes_a_failed_status_not_a_crash(qapp: object) -> None:
    panel = LogPanel()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))

    def source() -> Iterator[str]:
        yield "before"
        raise RuntimeError("boom")

    panel.run(source)
    wait_for_panel(panel)
    assert _unstamped(panel) == ["before"]
    assert finished == [(False, "RuntimeError: boom")]


def test_stop_ends_an_endless_job(qapp: object) -> None:
    panel = LogPanel()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))

    def endless() -> Iterator[str]:
        n = 0
        while True:
            yield f"tick {n}"
            n += 1
            time.sleep(JOB_PACE)

    panel.run(endless)
    # Waits for the first line rather than pumping for a fixed 100 ms, which is
    # what this was until 2026-09-04. The assertion at the bottom is that a tick
    # arrived before the stop, so a box slow enough to deliver none inside the
    # slice failed it -- and failed it saying "tick 0" was missing, which is
    # what a panel that had stopped streaming would also say. The condition is
    # the thing being waited for, so wait on the condition.
    pump_until(lambda: "tick 0" in panel.text(), "the endless job produced its first line")
    panel.stop()
    wait_for_panel(panel)
    assert finished and finished[0] == (True, "stopped")
    assert "tick 0" in panel.text()


def _cancellable(release: threading.Event) -> Iterator[str]:
    """A source shaped like `runner.interact()` under cancel: it RETURNS, never raises.

    `HANG_BOUND` rather than the unbounded wait the two dropped-worker tests
    use, because both callers `set()` the event unconditionally -- no assertion
    stands between `run()` and the release -- so the bound is never what ends
    this wait. It is here as a deadlock breaker for the edit that forgets.
    """
    yield "cloning"
    release.wait(HANG_BOUND)


def test_a_stopped_job_says_cancelled_and_not_finished(qapp: object) -> None:
    """A cancelled source reports success, so only the panel can tell the difference.

    `runner.interact()` returns when its cancel event is set rather than
    raising, so the worker ends its loop normally and reports `ok=True` with
    the same "done" any completed job produces. Measured through the Catalog's
    Install button against the real install script: Stop during the source
    clone left the header reading "finished: stopped" (install gate,
    2026-08-23).
    """
    panel = LogPanel()
    finished: list[tuple[bool, str]] = []
    panel.run_finished.connect(lambda ok, msg: finished.append((ok, msg)))
    release = threading.Event()

    assert panel.cancelled is False
    panel.run(lambda: _cancellable(release), title="Installing")
    process_events(50)
    panel.stop()
    release.set()
    wait_for_panel(panel)

    assert finished and finished[0][0] is True  # the job itself claims success
    assert panel.cancelled is True
    assert panel.status_text() == "cancelled"


def test_the_next_job_is_not_still_cancelled_from_the_last_one(qapp: object) -> None:
    """`run()` resets the flag, or every job after a Stop would be called cancelled."""
    panel = LogPanel()
    release = threading.Event()
    panel.run(lambda: _cancellable(release))
    process_events(50)
    panel.stop()
    release.set()
    wait_for_panel(panel)
    assert panel.cancelled is True

    panel.run(lambda: iter(["second job"]))
    wait_for_panel(panel)
    assert panel.cancelled is False
    assert panel.status_text() == "finished: done"


def test_stopping_a_panel_that_is_not_running_does_not_call_the_last_job_cancelled(
    qapp: object,
) -> None:
    """`main._stop_background_threads()` stops EVERY panel at exit, running or not.

    A panel that finished its job cleanly then ended the session reporting
    `cancelled is True` beside a header reading "finished: done" — two
    statements about the same job, one of them false.
    """
    panel = LogPanel()
    panel.stop()
    assert panel.cancelled is False, "a panel that never ran a job reported a cancelled one"

    panel.run(lambda: iter(["a line"]))
    wait_for_panel(panel)
    assert panel.status_text() == "finished: done"
    panel.stop()
    assert panel.cancelled is False


def test_terminal_colour_codes_never_reach_the_panel(qapp: object) -> None:
    """Both sources feed raw escapes: the install script's colour and the worldserver's.

    `\\x1b[36m` on every `[mod-city-bots]` line and the bracketed-paste
    `\\x1b[?2004h` around the console prompt are confirmed in the real stream,
    and a QPlainTextEdit renders neither — it showed the sequences themselves.
    """
    panel = LogPanel()
    panel.append("\x1b[36m[mod-city-bots] completed pending teleport for Ella\x1b[0m")
    panel.append("\x1b[?2004hAC> No gamemasters.")
    panel.append("\x1b(Bplain")
    assert _unstamped(panel) == [
        "[mod-city-bots] completed pending teleport for Ella",
        "AC> No gamemasters.",
        "(Bplain",
    ]
    assert "\x1b" not in panel.text()


def test_the_thread_can_be_joined_without_an_event_loop(qapp: object) -> None:
    """The one caller that must join is the one with no event loop left to pump.

    `main()` calls `_stop_background_threads()` from its `finally`, after
    `app.exec()` has returned, and that blocks in `panel.wait()`. The worker's
    `finished -> thread.quit` connection is queued into the main thread, so it
    could never be delivered from inside that wait: measured `wait(3000)` ->
    False with the worker long since done, then Qt torn down with the QThread
    still running (0xC0000409).
    """
    panel = LogPanel()
    panel.run(lambda: iter(["one", "two"]))
    assert (
        panel.wait(HANG_BOUND_MS) is True
    ), "the join timed out with nothing pumping the main thread"
    assert panel.running is False


def test_the_worker_is_destroyed_on_the_gui_thread_not_its_own(qapp: object) -> None:
    """Which thread runs `~QObject()` for the worker is the whole bug, so assert on it.

    `thread.finished.connect(worker.deleteLater)` - the textbook pattern - ran
    the worker's destructor on the WORKER thread during Qt's thread teardown.
    `_StreamWorker` is a Python subclass, so that destructor needs the GIL; if
    the GUI thread held it inside Qt's connection mutex at that instant, each
    side held what the other needed. gdb on yulon-ubuntu (2026-08-28) caught
    exactly that deadlock, and the same race that does not deadlock corrupted
    memory: SIGSEGV or SIGBUS in 9 of 20 GUI-only runs under load.

    A race is not a test, so this pins the invariant behind it instead:
    `destroyed` fires from inside the destructor, on whatever thread is running
    it, and a direct connection delivers it there. Old code: the worker's
    thread. Fixed code: this one.
    """
    import threading

    from PySide6.QtCore import Qt

    from yulon.ui.widgets.log_panel import LogPanel

    panel = LogPanel()
    assert panel.run(lambda: iter(["one line"]), title="t")
    assert panel.wait(HANG_BOUND_MS)
    worker = panel._worker
    assert worker is not None, "the panel must still own its worker after the job"

    destroyed_on: list[int] = []
    worker.destroyed.connect(
        lambda *_: destroyed_on.append(threading.get_ident()),
        Qt.ConnectionType.DirectConnection,
    )
    del worker

    # A second run disposes of the panel's reference; `job.InFlight` drops its
    # own once `finished` reaches `sweep()` on the GUI thread - a queued slot,
    # so the loop has to be pumped for the destructor to run at all.
    assert panel.run(lambda: iter(["two"]), title="t")
    assert panel.wait(HANG_BOUND_MS)
    pump_until(lambda: bool(destroyed_on), "the first worker was destroyed")

    assert destroyed_on, "the first worker was never destroyed"
    assert (
        destroyed_on[0] == threading.get_ident()
    ), "the worker was destroyed on a thread other than the GUI thread"


def test_a_finished_job_leaves_a_live_worker_not_a_dangling_wrapper(qapp: object) -> None:
    """The panel kept `self._worker` AND handed it to `deleteLater` - one had to give.

    Under the old code the deferred delete destroyed the C++ object while the
    Python attribute still pointed at it; shiboken then reports the wrapper as
    invalid. Now Python is the sole owner until `_dispose_last_job()`.
    """
    import shiboken6

    from yulon.ui.widgets.log_panel import LogPanel

    panel = LogPanel()
    assert panel.run(lambda: iter(["x"]), title="t")
    assert panel.wait(HANG_BOUND_MS)
    qapp.processEvents()  # type: ignore[attr-defined]  # give any deferred delete its chance to run

    assert panel._worker is not None
    assert shiboken6.isValid(panel._worker), "the worker's C++ side was deleted out from under it"


def test_a_panel_dropped_before_its_thread_runs_does_not_leave_the_worker_dead(
    qapp: object,
) -> None:
    """The crash, from a native backtrace: `QThread::started` -> `worker.run` on freed memory.

    Between `thread.start()` and the OS scheduling the thread there is a window,
    and in it the panel holding the only reference to the worker can be
    garbage-collected. Python owns an unparented QObject, so the worker dies
    with the panel; the thread then wakes, PySide looks `run` up on it, and
    `QMetaMethod::name()` reads freed memory. SIGBUS on yulon-ubuntu under gdb
    (2026-08-28), 100% of runs; 1 in 20 idle, 9 in 20 under load - the window
    is scheduling latency. Closing a tab right after "Follow worldserver log" is
    the same sequence in the app.

    Held open here with a gate the source blocks on, so the panel can be dropped
    while the job is provably still in flight. Under the old code this test does
    not fail - the process dies.

    The hold has NO CLOCK ON IT, and that is the 2026-09-04 change. It was
    `gate.wait(5.0)`, so "provably still in flight" was true only for five
    seconds of wall time: a box that stalled this test between `run()` and the
    assertion below released the source on its own, the job finished, and the
    failure read "the worker died with the panel while its thread was live" --
    naming the crash of 2026-08-28 on a run where nothing at all had gone wrong.
    A premise held open by a stopwatch is not held open. The `finally` is what
    makes an unbounded wait safe: without it, an assertion failure would leave a
    QThread running at interpreter exit, which Qt answers by aborting the
    process rather than warning (see `job.InFlight.wait_all`).

    The `try` opens on the statement after `run()` -- the first one at which a
    thread is blocked on the gate -- and not one later. It opened after
    `assert thread is not None` until 2026-09-04, and the gap was measured
    rather than argued on m910q that day: with that assertion made to fail,
    the test went red in 0.20s and the interpreter then died at exit with
    `QThread: Destroyed while thread '' is still running`, exit 134, which is a
    whole-suite run that reports every other test green and still fails.

    Releasing the gate is not enough on that path either, and that was the
    second measurement of the day: `finished -> thread.quit` is a QUEUED slot
    on this thread, so a released source ends `run()` and leaves the thread's
    event loop running with nothing pumping it. With the `try` moved and a
    bare `gate.set()` in the `finally`, the sibling test's mutation (`_live[1]`)
    still aborted at exit, 134 in 2s. The failure path therefore joins through
    `in_flight()`, which holds every pair and quits each thread explicitly; the
    success path does not, because the assertions after it are about the
    thread exiting by the queued route.
    """
    import gc
    import threading
    import weakref

    from yulon.ui.widgets.job import in_flight
    from yulon.ui.widgets.log_panel import LogPanel

    gate = threading.Event()

    def source() -> Iterator[str]:
        gate.wait()
        yield "released"

    panel = LogPanel()
    assert panel.run(source, title="t")
    try:
        thread = panel._thread
        assert thread is not None
        worker_ref = weakref.ref(panel._worker)  # type: ignore[arg-type]
        del panel
        gc.collect()

        assert worker_ref() is not None, "the worker died with the panel while its thread was live"
    except BaseException:
        gate.set()
        in_flight().wait_all(HANG_BOUND_MS)
        raise
    gate.set()
    assert thread.wait(HANG_BOUND_MS), "the thread never finished"
    pump_until(lambda: worker_ref() is None, "the worker was released")
    assert worker_ref() is None, "the worker was not released once its thread finished"


def test_a_runner_dropped_before_its_thread_runs_does_not_leave_the_worker_dead(
    qapp: object,
) -> None:
    """`ThreadedJobRunner` had the identical window: `_live` died with the view.

    This is the test bug-checklist section 33 names as the suite's second
    load-sensitive one: that entry records it failing under plain `-n auto` and
    passing under `--dist loadfile`, and the harness that ran it chose the
    distribution rather than fixing the test. The two things that made its
    verdict depend on the box were removed on 2026-09-04 -- the hold below has
    no clock on it (see the sibling test above for the argument), and
    `pump_until` reports which way its deadline went. Measured after, on m910q
    (4 cores, that day): 6/6 green with 96 CPU spinners beside it at loadavg
    36-98, and green in three whole-suite runs with this file's tests dealt
    round-robin across 14 concurrently running pytest processes at loadavg
    64-65, which is the distribution `loadfile` exists to prevent, run without
    it. The failure itself was not reproduced on that box before the change,
    so this is evidence the test no longer depends on the two things that were
    removed, not a claim that the red run of section 33 cannot recur.

    The `try` opens on the statement after the job is started, and its failure
    path joins through `in_flight()`, for the reasons the sibling test gives;
    here the measured mutation was `_live[1]`: exit 134 with the `try` one
    statement late, exit 134 again with it moved and only the gate released,
    and a plain `1 failed`, exit 1, with the explicit join.
    """
    import gc
    import threading
    import weakref

    from PySide6.QtCore import QObject

    from yulon.ui.widgets.job import ThreadedJobRunner, in_flight

    gate = threading.Event()

    def work() -> int:
        gate.wait()
        return 1

    owner = QObject()
    runner = ThreadedJobRunner(owner)
    runner(work, lambda _r: None, lambda _e: None)
    try:
        thread, worker = runner._live[0]
        worker_ref = weakref.ref(worker)
        del worker, runner, owner
        gc.collect()

        assert worker_ref() is not None, "the worker died with the runner while its thread was live"
    except BaseException:
        gate.set()
        in_flight().wait_all(HANG_BOUND_MS)
        raise
    gate.set()
    # `_JobWorker` ends its thread through `done -> thread.quit`, a queued slot
    # on the GUI thread, so the loop must be pumped for the thread to exit at
    # all - which is what the app does, and what `ThreadedJobRunner.wait()`
    # sidesteps by quitting explicitly. A bare `thread.wait()` here just times out.
    pump_until(lambda: not thread.isRunning(), "the job thread exited")
    assert not thread.isRunning(), "the job thread never exited"
    pump_until(lambda: worker_ref() is None, "the worker was released")
    assert worker_ref() is None


def test_a_long_refusal_does_not_make_the_panel_demand_the_whole_window(
    qapp: object,
) -> None:
    """The status label wraps, so a refusal cannot squeeze whatever sits beside it.

    `_finished()` hands this label the whole of a refusal. An unwrapped `QLabel`
    has a size hint as wide as its text, that hint becomes the panel's minimum
    width, and a `QSplitter` has to honour it -- so the pane next to it is
    squeezed to nothing.

    Measured 2026-09-02 on yulon-ubuntu, in a 986px window, with the real
    home-folder refusal: the catalog pane went 684px -> 88px while this panel
    demanded 1478px. Tiles clipped mid-word, Install buttons off-screen, no way
    back without resizing the window. The owner hit it on the first refusal a
    real user would ever see.

    Asserts the WIDTH THE PANEL DEMANDS rather than `wordWrap()`, which is a
    declaration and would still pass if the label were replaced by something
    else that does not wrap. Compares a short status against one twenty times
    longer instead of pinning a pixel count, because the number depends on the
    font the box happens to have.
    """
    panel = LogPanel()
    panel.resize(400, 300)

    panel._status.setText("idle")
    # `activate()` is load-bearing, and its absence made the first version of
    # this test pass against the bug: a layout that has not been activated
    # returns the size hint it last computed, so both readings below were the
    # same stale number and the mutation survived. Caught by removing the fix
    # and watching this test stay green.
    panel.layout().activate()
    short = panel.minimumSizeHint().width()

    panel._status.setText(
        "FAILED: InstallerError: /home/pk is your home folder itself. A server "
        "install owns the folder it is given - a reinstall removes it - so pick a "
        "dedicated subfolder inside your home folder instead. Pick a different "
        "folder and try again. Nothing was written."
    )
    panel.layout().activate()
    long = panel.minimumSizeHint().width()

    assert long <= short * 2, (
        "a long status inflated the panel's minimum width from "
        f"{short}px to {long}px, so a splitter must starve whatever is beside it"
    )


def test_the_status_label_still_says_what_failed(qapp: object) -> None:
    """Wrapping must not have been bought by truncating the sentence.

    The neighbour of the fix above: a label that elides its text would also stop
    demanding the window's width, and would pass that test while telling the user
    less than it used to. This asserts the whole refusal is still readable.
    """
    panel = LogPanel()
    message = (
        "/home/pk is your home folder itself. A server install owns the folder it "
        "is given - a reinstall removes it - so pick a dedicated subfolder."
    )
    panel._status.setText("FAILED: " + message)
    assert panel.status_text() == "FAILED: " + message


# ---------------------------------------------------------------------------
# The stamp and the stickiness (2026-09-03). Both asked for by the owner while
# watching a real install: an hour of build output with no clock on it answers
# neither "when did this step start" nor "how long has it been going", and a
# panel that stops following makes the newest line the one you cannot see.


def test_every_line_carries_a_wall_clock_and_the_elapsed_field_ticks_beside_stop(
    qapp: object,
) -> None:
    """Two fields in two places, and the split is the point.

    The clock goes on the line, because a line is a moment. Elapsed goes in the
    header beside Stop, because it is a property of the RUN and has to keep
    moving when nothing is being printed -- an hour of `build` emits lines in
    bursts, and a per-line elapsed stops exactly when the reader most wants to
    know the install has not died (owner, 2026-09-03).
    """
    panel = LogPanel()
    assert panel.elapsed_text() == "", "an elapsed time before any run has nothing to elapse from"
    panel.append("before any job")
    (early,) = panel.text().splitlines()
    assert STAMP.match(early), early
    assert "+" not in early, f"elapsed is still being stamped onto lines: {early}"

    def source() -> Iterator[str]:
        yield "inside the job"

    panel.run(source)
    wait_for_panel(panel)
    assert panel.elapsed_text().startswith("0:00:"), panel.elapsed_text()
    during = panel.text().splitlines()[-1]
    assert STAMP.match(during) and "+" not in during, during


def test_the_elapsed_field_stops_counting_when_the_job_ends_but_keeps_its_total(
    qapp: object,
) -> None:
    """The last value is the run's total, which is the number wanted after a long install.

    So the ticker stops -- a finished job must not go on counting -- and the
    field is written once more on the way out rather than cleared. Asserted by
    letting real time pass after the job and requiring the text NOT to move.
    """
    panel = LogPanel()

    def source() -> Iterator[str]:
        yield "done bit"

    panel.run(source)
    wait_for_panel(panel)
    settled = panel.elapsed_text()
    assert settled, "the elapsed field was cleared when the job ended"
    time.sleep(DWELL_PROOF)
    process_events(5)
    assert panel.elapsed_text() == settled, (
        f"the elapsed field kept counting after the job finished: {settled} -> "
        f"{panel.elapsed_text()}"
    )


def test_the_elapsed_clock_counts_from_this_run_and_not_from_the_last_one(qapp: object) -> None:
    """The zero is the RUN's, which is the whole reason it is set in `run()`.

    One panel serves the next install and the console after it. An elapsed
    field anchored to the widget's construction would tell somebody four hours
    into a job that started a minute ago -- worse than no field, because it
    reads as a measurement.
    """
    panel = LogPanel()

    def source() -> Iterator[str]:
        yield "first"

    panel.run(source)
    wait_for_panel(panel)
    first_start = panel._started_at
    assert first_start is not None
    time.sleep(CLOCK_GAP)

    def again() -> Iterator[str]:
        yield "second"

    panel.run(again)
    wait_for_panel(panel)
    assert panel._started_at is not None and panel._started_at > first_start, (
        "the second run kept the first run's zero, so its elapsed clock is wrong by the gap "
        "between them"
    )


def test_elapsed_keeps_its_hours_field_from_the_first_second(qapp: object) -> None:
    """`0:00:04` and `4:31:07`, never `00:04` growing an hours field partway through.

    An install has stages measured in seconds and stages measured in hours, and
    a field that changes shape at the hour mark stops lining up in the middle of
    the run that most needs reading.
    """
    from yulon.ui.widgets.log_panel import _elapsed

    assert _elapsed(0) == "0:00:00"
    assert _elapsed(4) == "0:00:04"
    assert _elapsed(59.9) == "0:00:59"
    assert _elapsed(600) == "0:10:00"
    assert _elapsed(3600) == "1:00:00"
    assert _elapsed(16267) == "4:31:07"
    # Never negative, whatever the clock does underneath.
    assert _elapsed(-5) == "0:00:00"


def test_the_panel_follows_the_bottom_while_it_is_already_at_the_bottom(qapp: object) -> None:
    """The default: the newest line is the visible one.

    Asserted through the scrollbar rather than by trusting `appendPlainText`,
    because the question is not whether the text arrived but whether it can be
    SEEN -- which is the complaint this was written for.
    """
    panel = LogPanel()
    panel.resize(400, 80)
    panel.show()
    process_events(5)
    for i in range(200):
        panel.append(f"line {i}")
    process_events(5)
    bar = panel._text.verticalScrollBar()
    assert bar.maximum() > 0, "the panel never filled; this test proves nothing about scrolling"
    assert (
        bar.value() >= bar.maximum() - 4
    ), f"the panel stopped following: at {bar.value()} of {bar.maximum()}"


def test_scrolling_up_holds_the_view_still_and_scrolling_back_resumes(qapp: object) -> None:
    """The other half, and the reason following is conditional rather than absolute.

    A panel that always jumps to the end cannot be read while it is running:
    scrolling up to look at the error that just went past yanks the reader back
    on the very next line. So a reader who has scrolled away is left alone --
    and, crucially, gets to come back without a button, by scrolling to the
    bottom again.
    """
    panel = LogPanel()
    panel.resize(400, 80)
    panel.show()
    process_events(5)
    for i in range(200):
        panel.append(f"line {i}")
    process_events(5)
    bar = panel._text.verticalScrollBar()

    bar.setValue(0)  # the reader scrolls up to look at something
    for i in range(20):
        panel.append(f"later {i}")
    process_events(5)
    assert bar.value() == 0, (
        f"appending yanked the reader back to the bottom (now {bar.value()}); the line they "
        "scrolled up to read is gone"
    )

    bar.setValue(bar.maximum())  # and scrolls back down
    for i in range(20):
        panel.append(f"newest {i}")
    process_events(5)
    assert bar.value() >= bar.maximum() - 4, (
        "following did not resume when the reader returned to the bottom, so it can only be "
        "turned off once"
    )
    assert panel.text().splitlines()[-1].endswith("newest 19")


def test_an_expired_pump_says_so_and_reports_how_fast_it_ran(qapp: object) -> None:
    """`pump_until` must FAIL on expiry, name the condition, and report the loop's rate.

    The headline of the 2026-09-04 log-panel fix was that expiry says which of
    the two things happened -- the condition never came true, or the box never
    scheduled this process -- and until this test existed nothing pinned it.
    Measured on m910q that day: a `return` inserted before the report, which
    is exactly the silent expiry the fix replaced, left this file at 21 passed.
    With this test in place the same mutation fails here.

    The turn count is checked against the calls the loop really made, not
    against a number, because it is the count that becomes the rate.
    """
    calls = 0

    def never() -> bool:
        nonlocal calls
        calls += 1
        return False

    with pytest.raises(pytest.fail.Exception) as expired:
        pump_until(never, "a condition that never comes true", timeout=EXPIRY_PROBE)

    report = str(expired.value)
    assert report.startswith(
        f"a condition that never comes true: never happened within {EXPIRY_PROBE}s"
    ), report
    turns = re.search(r"pumped (\d+) times in", report)
    assert turns is not None, report
    # `done()` is asked once before every pump and once more at the deadline.
    assert int(turns.group(1)) == calls - 1, (int(turns.group(1)), calls)
    assert re.search(r"\(\d+ turns/s, against " + str(PUMPED_HEALTHY) + "/s", report), report
    assert "never scheduled this process" in report, report


def test_no_wall_clock_bound_in_this_file_is_written_as_a_bare_number() -> None:
    """Every bound here must be spelled as one of the named ones, and nothing else.

    A source-shape read of THIS file, and deliberate: what it pins is that the
    reasoning stays attached to the number. The bounds are the only thing in
    this file a loaded box can move, and bug-checklist section 33 is what a bare
    one costs -- a red run nobody could explain, three clean re-runs, and a merge
    made on the count. A number typed at a call site carries no docstring, so
    the next person to add a wait here cannot inherit the argument for its size.

    Set EQUALITY rather than "no literal, and every name is one of these",
    which is what this test asserted until 2026-09-04. That version walked the
    tree by hand and skipped nested defs so the job sources' own `sleep` would
    not need a name, and the skip was measured blind to the two bounds the fix
    itself had removed -- `gate.wait(5.0)` inside both job sources -- and to a
    bound written as an expression (`timeout=HANG_BOUND * 2`): each left it at
    `1 passed` on m910q that day. `spelled_bounds` walks everything; the job
    sources' pace is `JOB_PACE`, named, so it has somewhere to be.

    The names are listed individually rather than counted, because they do not
    mean the same thing: `HANG_BOUND` must never be reached, `DWELL_PROOF`,
    `CLOCK_GAP` and `EXPIRY_PROBE` must be, and `JOB_PACE` is not a deadline at
    all. An audit that only asked "is it a name?" would let a hang bound be
    sized like a dwell one, and one that only counted would let a seventh
    constant with no argument behind it in.
    """
    assert spelled_bounds(__file__) == {
        "CLOCK_GAP",
        "DWELL_PROOF",
        "EXPIRY_PROBE",
        "HANG_BOUND",
        "HANG_BOUND_MS",
        "JOB_PACE",
    }
