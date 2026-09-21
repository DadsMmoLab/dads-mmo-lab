"""What the player watches while Yu'lon installs a new version of itself (T90 plan 3).

A label, a bar and a Cancel button, and one rule about all three: **the worker
thread never touches them.** The update runs through `ThreadedJobRunner`, and
the two things it says while it runs — the stage it has reached, and how many
bytes have arrived — go through `_Relay`, a `QObject` that lives on the GUI
thread and does nothing but emit. Qt queues the delivery and the bound `@Slot`s
below run where the widgets are.

That is `job.py`'s `LineRelay` rule, and it is not a style preference: a plain
callable connected to a worker-thread signal is delivered ON THE WORKER
(measured on PySide6 6.11.2, with an explicit `QueuedConnection`), which means
painting a widget from a thread that does not own it.

Cancel is a `threading.Event` for the same reason from the other side: the
worker reads it between chunks and between steps, and an `Event` is the one
thing both threads may touch.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from yulon.log import get_logger

logger = get_logger(__name__)

CANCELLING = "Cancelling…"
STARTING = "Getting ready…"


def as_megabytes(done: int, total: int) -> str:
    """`12.3 MB of 78.0 MB`, or just what has arrived when the total is unknown."""
    if total > 0:
        return f"{done / 1e6:.1f} MB of {total / 1e6:.1f} MB"
    return f"{done / 1e6:.1f} MB"


class _Relay(QObject):
    """Carries the worker's two kinds of news across the thread boundary.

    Its `emit_*` methods are safe to call from the worker — that is their whole
    job — and everything connected to the signals runs on the GUI thread.
    """

    progressed = Signal(int, int)
    staged = Signal(str)

    def emit_progress(self, done: int, total: int) -> None:
        """Called from the download's read loop. Emits and nothing else."""
        self.progressed.emit(done, total)

    def emit_stage(self, text: str) -> None:
        """Called from the sequence between steps. Emits and nothing else."""
        self.staged.emit(text)


class UpdateProgressDialog(QDialog):
    """The update's own window: where it has got to, how far, and a way out."""

    def __init__(self, version: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Installing Yu'lon {version}")
        self.setModal(True)
        self.resize(460, 180)
        self.cancel_event = threading.Event()
        """Set by Cancel; read by the worker between chunks and between steps."""

        column = QVBoxLayout(self)
        self.stage_label = QLabel(STARTING, self)
        self.stage_label.setObjectName("update-progress-stage")
        self.stage_label.setWordWrap(True)
        self.stage_label.setTextFormat(Qt.TextFormat.PlainText)
        column.addWidget(self.stage_label)

        self.bar = QProgressBar(self)
        self.bar.setObjectName("update-progress-bar")
        self.bar.setRange(0, 0)  # busy until the first byte says otherwise
        column.addWidget(self.bar)

        self.detail_label = QLabel("", self)
        self.detail_label.setObjectName("update-progress-detail")
        self.detail_label.setTextFormat(Qt.TextFormat.PlainText)
        column.addWidget(self.detail_label)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton("Cancel", self)
        self.cancel_button.setObjectName("update-progress-cancel")
        # A lambda is fine HERE: a GUI-thread signal on a GUI-thread widget.
        # `job.py`'s bound-slot rule is about a WORKER thread's signal.
        self.cancel_button.clicked.connect(lambda _checked=False: self.ask_to_cancel())
        row.addWidget(self.cancel_button)
        self.close_button = QPushButton("Close", self)
        self.close_button.setObjectName("update-progress-close")
        self.close_button.setVisible(False)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        column.addLayout(row)

        self.relay = _Relay(self)
        self.relay.progressed.connect(self.set_progress)
        self.relay.staged.connect(self.set_stage)

    @Slot(str)
    def set_stage(self, text: str) -> None:
        """Which of the five steps is running. Bound `@Slot`, so it runs on the GUI thread."""
        self.stage_label.setText(text)

    @Slot(int, int)
    def set_progress(self, done: int, total: int) -> None:
        """How far the download has got. A total of 0 leaves the bar indeterminate.

        `setRange(0, 0)` is Qt's busy indicator, and it is what a step with no
        byte count (unpacking, the smoke test) gets: a bar sitting at 0% for a
        minute reads as a hang, and a bar this app has to invent numbers for is
        a lie.
        """
        if total > 0:
            self.bar.setRange(0, total)
            self.bar.setValue(min(done, total))
        else:
            self.bar.setRange(0, 0)
        self.detail_label.setText(as_megabytes(done, total))

    def ask_to_cancel(self) -> None:
        """Tell the worker to stop, and say so. The dialog stays up until it has.

        Disabled immediately, because the worker can only notice between
        chunks: a button that still looks pressable after the first press
        invites a second one and says nothing about the first.
        """
        logger.info("self-update: the player asked to cancel")
        self.cancel_event.set()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText(CANCELLING)

    def finish_error(self, message: str) -> None:
        """The update was refused. Show why, and leave one button: Close.

        The message is `str(UpdateError)`, which every refusal in
        `yulon/selfupdate/` writes for a player rather than for a log.
        """
        self.stage_label.setText(message)
        self.bar.setVisible(False)
        self.detail_label.setText("")
        self.cancel_button.setVisible(False)
        self.close_button.setVisible(True)
        self.close_button.setDefault(True)
