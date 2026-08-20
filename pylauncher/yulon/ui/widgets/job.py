"""One-shot background jobs for the UI (review finding, 2026-08-21).

Every long call a view makes — `docker compose up`, a module install that
clones and runs SQL, a networking plan that waits on HTTP — used to run inside
the button's slot, i.e. on the GUI thread, which is why the window went "not
responding" for the length of the work. `threaded_job_runner()` moves the call
onto a worker thread and delivers the outcome back through signals.

Two rules make this safe:

- **Callbacks must be bound methods of a GUI-thread `QObject`** (a view's own
  `@Slot`). A plain function or lambda has no thread affinity, so PySide6
  delivers it on the *worker* thread even with an explicit `QueuedConnection`
  (verified on 6.11.2) — which is the bug this module exists to avoid.
- **The worker is the only thread that touches the service**; the view is
  updated exclusively in the callbacks.

`run_inline()` is the same contract executed synchronously — what the tests
use so a click's effect is observable on the next line.
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


def threaded_job_runner(parent: QObject) -> JobRunner:
    """A `JobRunner` that runs each call on its own `QThread`, parented to `parent`."""

    def run(work: Work, on_done: OnDone, on_error: OnError) -> None:
        thread = QThread(parent)
        worker = _JobWorker(work)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(on_done)
        worker.failed.connect(on_error)
        worker.done.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.start()

    return run


def run_inline(work: Work, on_done: OnDone, on_error: OnError) -> None:
    """Run `work` synchronously with the same contract (tests, and headless callers)."""
    try:
        result = work()
    except Exception as exc:  # boundary, as above
        on_error(exc)
    else:
        on_done(result)
