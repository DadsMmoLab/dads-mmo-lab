"""Streaming log output widget (roadmap 4.1).

`LogPanel` shows the lines of a long-running job — an install, a rebuild,
`docker logs -f` — live, without blocking the UI thread. The job is any
`Iterator[str]` factory (e.g. `lambda: installer.run(options)` or
`lambda: runner.stream([...])`); it runs on a `QThread` inside a
`_StreamWorker` that emits `line(str)` and `finished(bool, str)` signals, and
the panel only connects to those. Call down / signal up (style-guide §5):
whoever owns the panel calls `run()`, the panel signals `run_finished` and
never reaches into the runner or into its parent.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from yulon import runner
from yulon.log import get_logger
from yulon.ui.widgets.job import in_flight

logger = get_logger(__name__)

LineSource = Callable[[], Iterator[str]]

_MAX_BLOCKS = 5000


class _StreamWorker(QObject):
    """Runs a `LineSource` to exhaustion on its thread, emitting each line."""

    line = Signal(str)
    finished = Signal(bool, str)  # ok, message

    def __init__(self, source: LineSource) -> None:
        super().__init__()
        self._source = source
        self._stop = False

    @Slot()
    def run(self) -> None:
        ok = True
        message = "done"
        try:
            for text in self._source():
                if self._stop:
                    message = "stopped"
                    break
                self.line.emit(text)
        except Exception as exc:  # boundary: anything the job raises becomes a UI message
            ok = False
            message = f"{type(exc).__name__}: {exc}"
            logger.warning(f"log panel job failed: {message}")
        self.finished.emit(ok, message)
        # Emit first, then end our own thread's event loop from inside it.
        # `finished` is also connected to `thread.quit`, but the QThread OBJECT
        # lives in the main thread, so that connection is queued — and the one
        # caller that most needs the join is `main._stop_background_threads()`,
        # which runs after `app.exec()` has returned and then blocks in
        # `wait()`. Nothing pumps the main thread's queue there, so `quit()` was
        # never delivered, `wait(5000)` timed out (measured: `wait(3000)` ->
        # False with the worker long finished) and Qt was torn down with the
        # QThread still running — the 0xC0000409 abort that function's own
        # docstring says it prevents. Called here it is direct, and the queued
        # copy stays as it was (review, 2026-08-23).
        thread = self.thread()
        if thread is not None:
            thread.quit()

    def request_stop(self) -> None:
        self._stop = True


class LogPanel(QWidget):
    """A read-only, auto-scrolling text panel fed by a background job."""

    run_started = Signal()
    run_finished = Signal(bool, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = QPlainTextEdit(self)
        self._text.setReadOnly(True)
        self._text.setMaximumBlockCount(_MAX_BLOCKS)
        self._status = QLabel("idle", self)
        self._stop_button = QPushButton("Stop", self)
        self._stop_button.setEnabled(False)
        self._stop_button.clicked.connect(self.stop)

        header = QHBoxLayout()
        header.addWidget(self._status, 1)
        header.addWidget(self._stop_button)
        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self._text, 1)

        self._cancel: threading.Event | None = None
        self._thread: QThread | None = None
        self._worker: _StreamWorker | None = None
        self._stop_requested = False

    # -- public ---------------------------------------------------------

    @property
    def running(self) -> bool:
        """True while a job is streaming into the panel."""
        return self._thread is not None and self._thread.isRunning()

    @property
    def cancelled(self) -> bool:
        """True if `stop()` was asked for the job now running, or the last one.

        Nothing downstream can work this out for itself. A cancelled source
        does not raise — `runner.interact()` returns when its cancel event is
        set — so the worker reports `ok=True` and a message ("done"/"stopped")
        that a completed job could also produce. The panel is the only thing
        that knows the Stop button was pressed, so it is the thing that says
        so. Reset by `run()` and only ever set for a job that was running, so it
        always describes the current job — see `stop()`.
        """
        return self._stop_requested

    def text(self) -> str:
        """Everything currently shown."""
        return self._text.toPlainText()

    def status_text(self) -> str:
        """What the header says about the job (tests / accessibility)."""
        return self._status.text()

    def clear(self) -> None:
        """Empty the panel."""
        self._text.clear()

    def append(self, line: str) -> None:
        """Append one line, without its terminal colour codes.

        Stripped HERE rather than in each source, because every source has the
        problem and none of them had the fix. `runner.interact()` yields the
        install script's lines raw and says so, and `docker.follow_logs()`
        streams the worldserver's own colour — confirmed in the real stream:
        `\\x1b[36m` on every `[mod-city-bots]` line, plus bracketed-paste
        `\\x1b[?2004h` around the console prompt. A QPlainTextEdit renders none
        of that, so the Console tab showed the escape sequences themselves on
        every coloured line, and the install panel would have too. The parser in
        `console.py` strips separately and must keep doing so — it reads the
        prompt out of the raw stream, long before anything is displayed
        (review, 2026-08-23).

        The stray-ESC removal is the second half: `strip_ansi()` only matches
        CSI sequences, so an `ESC(B`-style charset switch leaves the ESC byte
        behind, and that renders as a box glyph.

        Thread-safe only from the UI thread; the worker reaches it by signal.
        """
        self._text.appendPlainText(runner.strip_ansi(line).replace("\x1b", ""))

    def run(
        self,
        source: LineSource,
        *,
        title: str = "running",
        cancel: threading.Event | None = None,
    ) -> bool:
        """Start streaming `source()` into the panel. Returns False if a job is already running.

        `cancel`, when given, is set by `stop()` so a source that supports it
        (e.g. an engine's `run(cancel=...)`) can be interrupted even while blocked
        between lines (review finding, 2026-08-21).
        """
        if self.running:
            logger.debug("log panel busy; run() ignored")
            return False
        self._dispose_last_job()
        self._cancel = cancel
        self._stop_requested = False
        self._status.setText(title)
        self._stop_button.setEnabled(True)
        # No parent, and held by `in_flight()` until finished: a panel dropped
        # between `start()` and the OS scheduling the thread must not take the
        # worker down with it - see `job.InFlight`.
        thread = QThread()
        worker = _StreamWorker(source)
        worker.moveToThread(thread)
        in_flight().hold(thread, worker)
        thread.started.connect(worker.run)
        worker.line.connect(self.append)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        # Deliberately NOT `thread.finished.connect(worker.deleteLater)`, the
        # textbook pattern - see `_dispose_last_job()` for why it is the segfault.
        self._thread, self._worker = thread, worker
        thread.start()
        self.run_started.emit()
        return True

    def _dispose_last_job(self) -> None:
        """Delete the previous job's worker HERE, on the GUI thread, once its thread is gone.

        This used to be `thread.finished.connect(worker.deleteLater)` - the
        textbook Qt pattern, and it is the segfault. `deleteLater` posts the
        delete to the object's OWN thread, and Qt runs it inside that thread's
        final cleanup (`QThreadPrivate::finish`) - on the worker thread.
        `_StreamWorker` is a Python subclass, so tearing it down needs the GIL.
        If the GUI thread holds the GIL at that instant and is inside Qt's
        connection mutex - delivering a signal into a Python slot, which is
        most of what a GUI thread does - each side holds what the other needs.

        gdb on yulon-ubuntu, 2026-08-28: the main thread in
        `cleanOrphanedConnectionsImpl` -> `QBasicMutex::lockInternal`, reached
        from a slider `rangeChanged` into a Python slot; a thread named
        "QThread" in `~QObject()` -> `Shiboken::GilState::acquire`. That is the
        deadlock. The same race that does not deadlock corrupts memory instead:
        9 of 20 GUI-only runs died with SIGSEGV or SIGBUS, always at the
        boundary where one test module's last worker finished while the next
        module's fixture was building a window. Removing `processEvents()` from
        that fixture's teardown changed nothing, which is how the suspect moved
        from the fixture to here.

        And the panel already held `self._worker`, so the deferred delete was
        destroying the C++ side of an object Python still pointed at.

        Deleting from the GUI thread is safe once the worker's thread has
        finished: an object whose thread is gone has no affinity, and Qt permits
        its destruction from any thread. `running` is false to reach here, and
        `wait()` turns "not running" into "fully exited" rather than "about to".
        The QThread object itself lives on the GUI thread - it was created here -
        so ITS deferred delete is a GUI-thread event and is fine.
        """
        thread, worker = self._thread, self._worker
        self._thread = self._worker = None
        if thread is None:
            return
        thread.wait(5000)
        thread.deleteLater()
        del worker  # the last Python reference; the C++ object goes with it, on this thread

    @Slot()
    def stop(self) -> None:
        """Ask the running job to stop after its current line (and cancel a blocked one).

        A no-op when nothing is running, which is not tidiness: `cancelled`
        promises to describe the job, and `main._stop_background_threads()`
        calls `stop()` on EVERY registered panel at exit with no running check.
        Without the guard a panel that had finished its job cleanly ended the
        session reporting `cancelled is True` beside a header reading "finished:
        done" (review, 2026-08-23).
        """
        if not self.running:
            return
        self._stop_requested = True
        if self._cancel is not None:
            self._cancel.set()
        if self._worker is not None:
            self._worker.request_stop()

    def wait(self, timeout_ms: int = 30_000) -> bool:
        """Block until the job's thread exits (tests / shutdown). True if it did."""
        if self._thread is None:
            return True
        return bool(self._thread.wait(timeout_ms))

    # -- slots ----------------------------------------------------------

    @Slot(bool, str)
    def _on_finished(self, ok: bool, message: str) -> None:
        # Cancellation is asked about FIRST, because a stopped job arrives here
        # with ok=True and message "done" — indistinguishable, from here, from
        # one that ran to the end. Measured on a real install driven through
        # the Catalog's own button: Stop pressed during the source clone left
        # the panel reading "finished: stopped" (install gate, 2026-08-23).
        # "finished" is a claim about the work, and a stopped job did not
        # finish it. What was left behind is the caller's story to tell — the
        # panel does not know whether it was following a log or building a
        # server.
        if self._stop_requested:
            self._status.setText("cancelled")
        else:
            self._status.setText(("finished: " if ok else "FAILED: ") + message)
        self._stop_button.setEnabled(False)
        self.run_finished.emit(ok, message)
