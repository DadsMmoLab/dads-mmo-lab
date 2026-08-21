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

from yulon.log import get_logger

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

    # -- public ---------------------------------------------------------

    @property
    def running(self) -> bool:
        """True while a job is streaming into the panel."""
        return self._thread is not None and self._thread.isRunning()

    def text(self) -> str:
        """Everything currently shown."""
        return self._text.toPlainText()

    def clear(self) -> None:
        """Empty the panel."""
        self._text.clear()

    def append(self, line: str) -> None:
        """Append one line (thread-safe only from the UI thread; the worker uses signals)."""
        self._text.appendPlainText(line)

    def run(
        self,
        source: LineSource,
        *,
        title: str = "running",
        cancel: threading.Event | None = None,
    ) -> bool:
        """Start streaming `source()` into the panel. Returns False if a job is already running.

        `cancel`, when given, is set by `stop()` so a source that supports it
        (e.g. `Installer.run(cancel=...)`) can be interrupted even while blocked
        between lines (review finding, 2026-08-21).
        """
        if self.running:
            logger.debug("log panel busy; run() ignored")
            return False
        self._cancel = cancel
        self._status.setText(title)
        self._stop_button.setEnabled(True)
        thread = QThread(self)
        worker = _StreamWorker(source)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.line.connect(self.append)
        worker.finished.connect(self._on_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        self._thread, self._worker = thread, worker
        thread.start()
        self.run_started.emit()
        return True

    @Slot()
    def stop(self) -> None:
        """Ask the running job to stop after its current line (and cancel a blocked one)."""
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
        self._status.setText(("finished: " if ok else "FAILED: ") + message)
        self._stop_button.setEnabled(False)
        self.run_finished.emit(ok, message)
