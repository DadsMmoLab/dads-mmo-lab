"""Tests for `LogPanel` (roadmap 4.1): a job streams in without blocking, and finishes cleanly."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

from tests.conftest import process_events
from yulon.ui.widgets.log_panel import LogPanel


def _wait_for(panel: LogPanel, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while panel.running and time.monotonic() < deadline:
        process_events(20)
    panel.wait(1000)
    process_events(50)


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
            time.sleep(0.01)

    assert panel.run(source, title="job") is True
    assert panel.run(source) is False  # busy
    _wait_for(panel)
    assert panel.text().splitlines() == ["line 0", "line 1", "line 2"]
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
    _wait_for(panel)
    assert panel.text().splitlines() == ["before"]
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
            time.sleep(0.005)

    panel.run(endless)
    process_events(100)
    panel.stop()
    _wait_for(panel)
    assert finished and finished[0] == (True, "stopped")
    assert "tick 0" in panel.text()


def _cancellable(release: threading.Event) -> Iterator[str]:
    """A source shaped like `runner.interact()` under cancel: it RETURNS, never raises."""
    yield "cloning"
    release.wait(5.0)


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
    _wait_for(panel)

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
    _wait_for(panel)
    assert panel.cancelled is True

    panel.run(lambda: iter(["second job"]))
    _wait_for(panel)
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
    _wait_for(panel)
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
    assert panel.text().splitlines() == [
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
    assert panel.wait(5000) is True, "the join timed out with nothing pumping the main thread"
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
    assert panel.wait(5000)
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
    assert panel.wait(5000)
    _pump_until(qapp, lambda: bool(destroyed_on))

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
    assert panel.wait(5000)
    qapp.processEvents()  # type: ignore[attr-defined]  # give any deferred delete its chance to run

    assert panel._worker is not None
    assert shiboken6.isValid(panel._worker), "the worker's C++ side was deleted out from under it"


def _pump_until(qapp: object, done: Callable[[], bool], timeout: float = 10.0) -> None:
    """Pump the GUI event loop until `done()`, or `timeout` seconds - whichever first.

    A WALL CLOCK, not a number of turns. This counted iterations until
    2026-09-03 and pumped with a bare `processEvents()`, so it burned its whole
    budget in milliseconds and then gave up while the worker thread had simply
    not been scheduled yet. Measured on yulon-ubuntu with no code change
    between the runs: load 13.16 fail, load 0.26 pass, load 15.24 fail.

    `time.sleep` between pumps, and that part is the correction to a claim this
    docstring made when the deadline was added. It said `process_events(20)`
    "BLOCKS for up to 20ms inside Qt, so a thread that needs the CPU can have
    it". It does not. `conftest.process_events` loops on
    `processEvents(AllEvents, 10)`, and `AllEvents` WITHOUT `WaitForMoreEvents`
    returns immediately on an empty queue -- a review measured the loop at
    11109 spins in 20ms, CPU/wall 0.78, which is a busy wait bounded into
    slices, not a yield. The deadline alone did fix the test, so the fix was
    real and the reason written beside it was not; the next person under load
    would have acted on the reason. `sleep` is the thing that actually hands
    the CPU to the worker this loop is waiting for.
    """
    deadline = time.monotonic() + timeout
    while True:
        if done():
            return
        if time.monotonic() >= deadline:
            return
        process_events(5)
        time.sleep(0.005)


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
    """
    import gc
    import threading
    import weakref

    from yulon.ui.widgets.log_panel import LogPanel

    gate = threading.Event()

    def source() -> Iterator[str]:
        gate.wait(5.0)
        yield "released"

    panel = LogPanel()
    assert panel.run(source, title="t")
    thread = panel._thread
    assert thread is not None
    worker_ref = weakref.ref(panel._worker)  # type: ignore[arg-type]
    del panel
    gc.collect()

    assert worker_ref() is not None, "the worker died with the panel while its thread was live"

    gate.set()
    assert thread.wait(5000), "the thread never finished"
    _pump_until(qapp, lambda: worker_ref() is None)
    assert worker_ref() is None, "the worker was not released once its thread finished"


def test_a_runner_dropped_before_its_thread_runs_does_not_leave_the_worker_dead(
    qapp: object,
) -> None:
    """`ThreadedJobRunner` had the identical window: `_live` died with the view."""
    import gc
    import threading
    import weakref

    from PySide6.QtCore import QObject

    from yulon.ui.widgets.job import ThreadedJobRunner

    gate = threading.Event()

    def work() -> int:
        gate.wait(5.0)
        return 1

    owner = QObject()
    runner = ThreadedJobRunner(owner)
    runner(work, lambda _r: None, lambda _e: None)
    thread, worker = runner._live[0]
    worker_ref = weakref.ref(worker)
    del worker, runner, owner
    gc.collect()

    assert worker_ref() is not None, "the worker died with the runner while its thread was live"

    gate.set()
    # `_JobWorker` ends its thread through `done -> thread.quit`, a queued slot
    # on the GUI thread, so the loop must be pumped for the thread to exit at
    # all - which is what the app does, and what `ThreadedJobRunner.wait()`
    # sidesteps by quitting explicitly. A bare `thread.wait()` here just times out.
    _pump_until(qapp, lambda: not thread.isRunning())
    assert not thread.isRunning(), "the job thread never exited"
    _pump_until(qapp, lambda: worker_ref() is None)
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
