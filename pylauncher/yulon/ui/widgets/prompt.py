"""Ask the user for a line of input on behalf of a subprocess (roadmap 6.1.5).

The install scripts run `sudo`, and `sudo` asks for a password. No rule in
`installer.PROMPT_RULES` can answer that — only the person sitting at the
machine knows it — so before this existed the script simply stopped on the
prompt and the window looked frozen.

The awkward part is which thread is which. The installer runs on a worker
thread (the window must not freeze during a two-hour install), but a dialog may
only be created on the GUI thread. `InputPrompter.ask()` is therefore called
*from the worker*, hands the question to the GUI thread through a queued
signal, and blocks until the answer comes back — which is exactly the
behaviour the subprocess needs, since it is sitting on a read.

Style-guide §3/§5: the write to the child's stdin lives in `runner.py`; this
only supplies the text. It never touches the subprocess.
"""

from __future__ import annotations

import re
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtWidgets import QInputDialog, QLineEdit, QWidget

from yulon.log import get_logger

logger = get_logger(__name__)

# Prompts whose answer must never be echoed, logged, or kept.
_SECRET = re.compile(r"password|passphrase|secret|\bpin\b", re.IGNORECASE)

# How often the worker wakes to re-check `cancel` while waiting for an answer.
_POLL_SECONDS = 0.1


def is_secret(prompt: str) -> bool:
    """True if this prompt is asking for something that must not be shown."""
    return bool(_SECRET.search(prompt))


def tidy(prompt: str) -> str:
    """The child's raw prompt as a question worth showing a person.

    Scripts colour their output and rarely end a prompt with a newline, so what
    arrives is a fragment like `[sudo] password for pk:`. That is already the
    clearest possible description of what is wanted, so it is shown as-is —
    only trimmed, and never truncated, because the tail is usually the part
    that says what is being asked.
    """
    return prompt.strip()


class InputPrompter(QObject):
    """Bridges a blocked subprocess to a dialog, across the thread boundary.

    Keep a strong reference to this: PySide6 holds bound-method slots weakly, so
    a prompter that only a local variable owns is collected and its dialog never
    appears — the same trap that made the first Start button do nothing.
    """

    #: (prompt text, whether the answer must be masked). Emitted from the worker.
    requested = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._answer: str | None = None
        self._answered = threading.Event()
        self._cancel: threading.Event | None = None
        self.requested.connect(self._show, Qt.ConnectionType.QueuedConnection)

    def bind_cancel(self, cancel: threading.Event | None) -> None:
        """Let a cancelled job stop waiting for an answer nobody is going to give."""
        self._cancel = cancel

    def ask(self, prompt: str) -> str | None:
        """Called ON THE WORKER THREAD. Blocks until the user answers or cancels.

        Returns the typed text, or None if the user dismissed the dialog or the
        job was cancelled — which `runner.interact()` treats as "unanswered",
        leaving the prompt alone rather than sending an empty line that the
        script would read as a confirmation.
        """
        self._answer = None
        self._answered.clear()
        secret = is_secret(prompt)
        logger.info(f"asking the user for input: {'<secret prompt>' if secret else prompt!r}")
        self.requested.emit(tidy(prompt), secret)
        while not self._answered.wait(_POLL_SECONDS):
            if self._cancel is not None and self._cancel.is_set():
                logger.info("input prompt abandoned: the job was cancelled")
                return None
        return self._answer

    @Slot(str, bool)
    def _show(self, prompt: str, secret: bool) -> None:
        """Runs on the GUI thread; puts the question to the user."""
        parent = self.parent()
        text, ok = QInputDialog.getText(
            parent if isinstance(parent, QWidget) else None,
            "The installer needs an answer",
            prompt,
            QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal,
            "",
        )
        self._answer = text if ok else None
        self._answered.set()
