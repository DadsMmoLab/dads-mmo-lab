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
        thread = QThread(self._parent)
        worker = _JobWorker(work)
        worker.moveToThread(thread)
        self._live.append((thread, worker))
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


def run_inline(work: Work, on_done: OnDone, on_error: OnError) -> None:
    """Run `work` synchronously with the same contract (tests, and headless callers)."""
    try:
        result = work()
    except Exception as exc:  # boundary, as above
        on_error(exc)
    else:
        on_done(result)
