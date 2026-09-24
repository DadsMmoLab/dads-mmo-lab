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
- **The runner delivers every callback on the GUI thread, whatever it is**
  (T97). A plain function or lambda connected straight to a worker's signal has
  no thread affinity, so PySide6 delivers it on the *worker* thread even with an
  explicit `QueuedConnection` (verified on 6.11.2). That was a rule callers had
  to keep, and one did not: the Characters tab's list was cleared and refilled
  from a worker and the app segfaulted (m910q, 2026-09-23). So the worker's
  signals now go to `_Delivery`, a QObject living on the GUI thread, through a
  queued connection to ITS slots, and that slot calls the callback -- a lambda,
  a closure, a partial or a bound method all run where the widgets are.
- **The worker is the only thread that touches the service**; the view is
  updated exclusively in the callbacks.

`run_inline()` is the same contract executed synchronously — what the tests use
so a click's effect is observable on the next line.

`LineRelay` is the same three rules applied to a job that talks *while* it runs
rather than only at the end.
"""

from __future__ import annotations

from collections.abc import Callable

import shiboken6
from PySide6.QtCore import QCoreApplication, QObject, Qt, QThread, QTimer, Signal, Slot

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
        self._pairs: list[tuple[QThread, QObject, _Delivery | None]] = []

    def hold(self, thread: QThread, worker: QObject, delivery: _Delivery | None = None) -> None:
        """Own a started pair, and the delivery that carries its answer, if it has one.

        The delivery is held until it has DELIVERED, not only until the thread
        has finished: its slot is a queued call on the GUI thread, and a
        delivery dropped with that call still in the queue is the same freed
        receiver `InFlight` exists for, one hop later.
        """
        self._pairs.append((thread, worker, delivery))
        thread.finished.connect(self.sweep)

    @Slot()
    def sweep(self) -> None:
        """Drop every pair whose thread has finished. Runs on the GUI thread."""
        self._pairs = [
            (thread, worker, delivery)
            for thread, worker, delivery in self._pairs
            if thread.isRunning() or (delivery is not None and not delivery.delivered)
        ]

    def wait_all(self, timeout_ms: int = 10_000) -> bool:
        """Join everything still running (app shutdown). True if all finished in time.

        `quit()` ends an event loop; it cannot interrupt a `run()` still inside
        its work - a blocked `subprocess.run`, a `docker logs -f` with no new
        line. A pair that does not finish is left held, and Qt's abort at
        interpreter exit (a QThread destroyed while running) follows. That is
        the pre-existing contract and this class does not change it; what it
        can do is put a name in the log first, so the abort is not a mystery.
        """
        done = True
        for thread, worker, _delivery in list(self._pairs):
            if thread.isRunning():
                thread.quit()
                if not thread.wait(timeout_ms):
                    done = False
                    logger.warning(
                        f"a background job did not finish within {timeout_ms} ms at exit: "
                        f"{type(worker).__name__}; Qt will abort when it is destroyed"
                    )
        self.sweep()
        return done


_in_flight: InFlight | None = None


def in_flight() -> InFlight:
    """The process-wide holder, made on first use so it lives on the GUI thread."""
    global _in_flight
    if _in_flight is None:
        _in_flight = InFlight()
    return _in_flight


def _gone(owner: object) -> bool:
    """True for a QObject whose C++ side has been deleted; False for anything else."""
    return isinstance(owner, QObject) and not shiboken6.isValid(owner)


class _Delivery(QObject):
    """Carries one job's answer from its worker thread to its callback, ON the GUI thread.

    Created on the GUI thread and connected to the worker's signals with a
    queued connection to this object's own `@Slot`s, which is the one shape
    PySide6 delivers where the receiver lives. The slot then calls the caller's
    callable directly, so the callable's own nature -- lambda, closure, bound
    method -- no longer decides the thread (T97).

    **A deleted owner gets nothing, as before.** A bound slot connected straight
    to the worker was disconnected by Qt when its view was destroyed, so a job
    finishing after its tab closed called nothing. That is kept: the answer is
    dropped when the runner's owner, or the object a bound-method callback
    belongs to, has lost its C++ side. A lambda has no owner to ask, which is why
    the runner's parent is asked too.

    The callbacks are released once delivered, so a closure over a view does
    not keep that view alive for as long as `in_flight()` holds this.
    """

    def __init__(self, owner: object, on_done: OnDone, on_error: OnError) -> None:
        super().__init__()
        self._owner = owner
        self._on_done: OnDone | None = on_done
        self._on_error: OnError | None = on_error
        self.delivered = False

    @Slot(object)
    def done(self, result: object) -> None:
        self._deliver(self._on_done, result)

    @Slot(object)
    def failed(self, exc: object) -> None:
        self._deliver(self._on_error, exc)

    def _deliver(self, callback: Callable[[object], None] | None, value: object) -> None:
        if self.delivered or callback is None:
            return
        self.delivered = True
        owner, self._owner = self._owner, None
        self._on_done = self._on_error = None
        # Swept on a LATER turn of the loop, never from inside this slot: the
        # sweep drops the last reference to this object, and deleting a QObject
        # while its own slot is on the stack is the freed-receiver crash again.
        QTimer.singleShot(0, in_flight().sweep)
        if _gone(owner) or _gone(getattr(callback, "__self__", None)):
            logger.info("a background job finished after its owner was closed; answer dropped")
            return
        callback(value)


class ThreadedJobRunner:
    """Runs each call on its own `QThread`; call it like a function.

    Delivers `on_done`/`on_error` on the GUI thread through a `_Delivery`,
    whatever kind of callable they are (T97).

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
        delivery = _Delivery(self._parent, on_done, on_error)
        app = QCoreApplication.instance()
        if app is not None and delivery.thread() is not app.thread():
            # Only a caller off the GUI thread gets here, and its answer still
            # belongs where the widgets are.
            delivery.moveToThread(app.thread())
        self._live.append((thread, worker))
        in_flight().hold(thread, worker, delivery)
        thread.started.connect(worker.run)
        queued = Qt.ConnectionType.QueuedConnection
        worker.done.connect(delivery.done, queued)
        worker.failed.connect(delivery.failed, queued)
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
