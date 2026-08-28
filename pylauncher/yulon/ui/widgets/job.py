"""One-shot background jobs for the UI (review finding, 2026-08-21).

Every long call a view makes — `docker compose up`, a module install that
clones and runs SQL, a networking plan that waits on HTTP — used to run inside
the button's slot, i.e. on the GUI thread, which is why the window went "not
responding" for the length of the work. `ThreadedJobRunner` moves the call onto
a worker thread and delivers the outcome back through signals.

Three rules make this work:

- **The runner keeps a strong reference to every live thread and worker.**
  PySide6 connects to a bound method through a weak reference, so a worker held
  only by a local variable is collected the moment the function returns and its
  `run` slot never fires — the job silently never happens (observed on the test
  VM: the Server tab sat on "status: unknown" forever).
- **Callbacks must be bound methods of a GUI-thread `QObject`** (a view's own
  `@Slot`). A plain function or lambda has no thread affinity, so PySide6
  delivers it on the *worker* thread even with an explicit `QueuedConnection`
  (verified on 6.11.2) — which is the other half of what this module avoids.
- **The worker is the only thread that touches the service**; the view is
  updated exclusively in the callbacks.

`run_inline()` is the same contract executed synchronously — what the tests use
so a click's effect is observable on the next line.

`LineRelay` is the same three rules applied to a job that talks *while* it runs
rather than only at the end.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Signal, Slot

from yulon.log import get_logger

logger = get_logger(__name__)

Work = Callable[[], object]
OnDone = Callable[[object], None]
OnError = Callable[[object], None]
JobRunner = Callable[[Work, OnDone, OnError], None]


class _JobWorker(QObject):
    """Runs one callable on its thread and reports the outcome (never raises out)."""

    done = Signal(object)
    failed = Signal(object)

    def __init__(self, work: Work) -> None:
        super().__init__()
        self._work = work

    @Slot()
    def run(self) -> None:
        try:
            result = self._work()
        except Exception as exc:  # boundary: the view decides how to show it
            logger.warning(f"background job failed: {type(exc).__name__}: {exc}")
            self.failed.emit(exc)
        else:
            self.done.emit(result)


class InFlight(QObject):
    """Owns every started (thread, worker) pair until the thread has FINISHED.

    The crash this exists for, from a native backtrace on yulon-ubuntu
    (2026-08-28): a worker QThread is scheduled by the OS, emits `started`,
    PySide dispatches it to `worker.run` - and dies in `QMetaMethod::name()`
    because the worker's C++ object is already gone. Between `thread.start()`
    and the OS actually running the thread there is a window, and in it the
    panel or view that held the only Python reference to the worker can be
    garbage-collected. Python owns an unparented QObject, so the worker is
    deleted with it; the thread then wakes and calls a method on freed memory.
    SIGSEGV or SIGBUS, single Python frame on the main thread (it was waiting
    for the GIL the dying thread held), no frame on the worker thread at all.

    Load-dependent, because the window is scheduling latency: 1 in 20 runs on
    an idle box, 9 in 20 with a worldserver running beside it, and 100% under
    gdb, which is what finally produced the backtrace. The same sequence in
    the app is closing a tab right after pressing "Follow worldserver log".

    So a started pair is held HERE, by an object that lives on the GUI thread
    and outlives any panel, until `finished` says the thread is done - and only
    then dropped, on this thread, where deleting a QObject whose thread has
    exited is safe. `sweep()` is a Slot, so `thread.finished` reaches it as a
    queued call on the GUI thread rather than on the thread that is finishing;
    dropping the pair from the worker thread itself would be the OTHER crash
    (see `LogPanel._dispose_last_job`).

    The threads are also created without a parent. A QThread parented to a
    panel is destroyed with the panel, and a QThread destroyed while running
    ends the process rather than warning - so the panel must not be able to
    take the thread down with it either.
    """

    def __init__(self) -> None:
        super().__init__()
        self._pairs: list[tuple[QThread, QObject]] = []

    def hold(self, thread: QThread, worker: QObject) -> None:
        self._pairs.append((thread, worker))
        thread.finished.connect(self.sweep)

    @Slot()
    def sweep(self) -> None:
        """Drop every pair whose thread has finished. Runs on the GUI thread."""
        self._pairs = [pair for pair in self._pairs if pair[0].isRunning()]

    def wait_all(self, timeout_ms: int = 10_000) -> bool:
        """Join everything still running (app shutdown). True if all finished in time."""
        done = True
        for thread, _worker in list(self._pairs):
            if thread.isRunning():
                thread.quit()
                done = bool(thread.wait(timeout_ms)) and done
        self.sweep()
        return done


_in_flight: InFlight | None = None


def in_flight() -> InFlight:
    """The process-wide holder, made on first use so it lives on the GUI thread."""
    global _in_flight
    if _in_flight is None:
        _in_flight = InFlight()
    return _in_flight


class ThreadedJobRunner:
    """Runs each call on its own `QThread`; call it like a function.

    Holds every live (thread, worker) pair so neither is collected mid-flight,
    and `wait()` lets the app join them before Qt is torn down — a `QThread`
    destroyed while running aborts the process rather than warning.
    """

    def __init__(self, parent: QObject) -> None:
        self._parent = parent
        self._live: list[tuple[QThread, _JobWorker]] = []

    def __call__(self, work: Work, on_done: OnDone, on_error: OnError) -> None:
        self._prune()
        # No parent, and held by `in_flight()` until finished - see `InFlight`.
        thread = QThread()
        worker = _JobWorker(work)
        worker.moveToThread(thread)
        self._live.append((thread, worker))
        in_flight().hold(thread, worker)
        thread.started.connect(worker.run)
        worker.done.connect(on_done)
        worker.failed.connect(on_error)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.start()

    def _prune(self) -> None:
        self._live = [pair for pair in self._live if pair[0].isRunning()]

    def wait(self, timeout_ms: int = 10_000) -> bool:
        """Join every running job. True if they all finished within the timeout."""
        done = True
        for thread, _worker in list(self._live):
            if thread.isRunning():
                thread.quit()
                done = bool(thread.wait(timeout_ms)) and done
        self._prune()
        return done


def threaded_job_runner(parent: QObject) -> ThreadedJobRunner:
    """A `JobRunner` that runs each call on its own thread, parented to `parent`."""
    return ThreadedJobRunner(parent)


class LineRelay(QObject):
    """Carries a running job's output lines from its worker thread to a GUI slot.

    `ThreadedJobRunner` above delivers one outcome at the end of a job. A job
    that takes half an hour — the database import is the one this app has — has
    to say something in between, and the only mechanism that crosses a thread
    boundary correctly is a signal emitted on a `QObject` that lives on the GUI
    thread. So the sink handed down into `docker.repair_import()` is
    `emit_line`, which does nothing but emit; Qt queues the delivery and the
    connected `@Slot(str)` runs where the widgets are.

    Handing the view's own bound slot down instead would look identical and be
    the bug: a plain Python call is a plain Python call, and the widget would be
    written to from the worker thread. `LogPanel`'s `_StreamWorker` solved this
    once already; this is the same solution for a job the panel does not own.
    """

    line = Signal(str)

    def emit_line(self, text: str) -> None:
        """Hand one line over. Safe to call from a worker thread — that is the point."""
        self.line.emit(text)


def run_inline(work: Work, on_done: OnDone, on_error: OnError) -> None:
    """Run `work` synchronously with the same contract (tests, and headless callers)."""
    try:
        result = work()
    except Exception as exc:  # boundary, as above
        on_error(exc)
    else:
        on_done(result)
