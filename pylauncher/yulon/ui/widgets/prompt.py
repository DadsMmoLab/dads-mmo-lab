"""Ask the user for a line of input on behalf of a subprocess (roadmap 6.1.5).

Docker provisioning runs `sudo`, and `sudo` asks for a password; the
docker-group join asks for consent. No rule can answer either — only the
person sitting at the machine can — so before this existed the install simply
stopped on the prompt and the window looked frozen.

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
from PySide6.QtWidgets import QDialog, QInputDialog, QLineEdit, QWidget

from yulon.log import get_logger

logger = get_logger(__name__)

# Prompts that are plainly NOT asking for a secret. Everything else is masked.
#
# This used to be the other way round — an allowlist of English secret words
# (`password|passphrase|secret|pin`) — and a miss meant EchoMode.Normal. The two
# failure directions are not symmetric: masking a folder path is a small
# annoyance, echoing a password onto a screen is not undoable. And the miss was
# guaranteed for anyone not running an English locale, where sudo prints
# "[sudo] adgangskode for pk:", "Passwort für", "Mot de passe de",
# "contraseña para", "wachtwoord voor" (review, 2026-08-22).
_NOT_SECRET = re.compile(
    r"\bpath\b|\bfolder\b|\bdirectory\b|\(y(?:es)?/no?\)|\([yn]/[yn]\)|press enter",
    re.IGNORECASE,
)

INSTALLER_TITLE = "The installer needs an answer"
"""The dialog's title unless the owner of the prompter names its own.

The SteamOS Docker repair names its own (T160): it runs from the Server tab
of a server that is already installed, where "the installer" is not what is
asking.
"""

# How often the worker wakes to re-check `cancel` while waiting for an answer.
_POLL_SECONDS = 0.1


def is_secret(prompt: str) -> bool:
    """True unless this prompt is recognisably asking for something harmless."""
    return not _NOT_SECRET.search(prompt)


def tidy(prompt: str) -> str:
    """The child's raw prompt as a question worth showing a person.

    Scripts colour their output and rarely end a prompt with a newline, so what
    arrives is a fragment like `[sudo] password for pk:`. That is already the
    clearest possible description of what is wanted, so it is shown as-is —
    only trimmed, and never truncated, because the tail is usually the part
    that says what is being asked.
    """
    return prompt.strip()


def explain(prompt: str, purpose: str | None) -> str:
    """The question as a player reads it: sudo's line said in words first (T194 C29).

    `[sudo] password for pk:` under "The installer needs an answer" does not
    say whose password, or why. A prompt that starts with `[sudo]` -- in any
    language, since sudo keeps its tag -- gets a sentence saying it is this
    computer's login password, what it is for, and that it is not kept; sudo's
    own line follows, so nothing it asked is hidden. Any other prompt is the
    script's own question and is shown as `tidy()` leaves it.
    """
    line = tidy(prompt)
    if not line.startswith("[sudo]"):
        return line
    why = f" to {purpose}" if purpose else ""
    return (
        f"Yu'lon needs this computer's password (the one you log in with){why}. "
        f"It goes to sudo and is never saved.\n\n{line}"
    )


class InputPrompter(QObject):
    """Bridges a blocked subprocess to a dialog, across the thread boundary.

    Keep a strong reference to this: PySide6 holds bound-method slots weakly, so
    a prompter that only a local variable owns is collected and its dialog never
    appears — the same trap that made the first Start button do nothing.
    """

    #: (prompt text, whether the answer must be masked). Emitted from the worker.
    requested = Signal(str, bool)

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        title: str = INSTALLER_TITLE,
        purpose: str | None = None,
    ) -> None:
        """`purpose` finishes "Yu'lon needs this computer's password ... to <purpose>" (T194)."""
        super().__init__(parent)
        self._title = title
        self._purpose = purpose
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
                self._answer = None
                return None
        # Handed over, not shared. The module docstring promises the answer is
        # never kept, and this object outlives the install: leaving the password
        # on `self._answer` left a live plaintext copy on a widget's child for
        # the rest of the session (review, 2026-08-22).
        answer, self._answer = self._answer, None
        return answer

    @Slot(str, bool)
    def _show(self, prompt: str, secret: bool) -> None:
        """Runs on the GUI thread; puts the question to the user.

        The cancel check is not redundant with `ask()`'s. This slot is queued,
        so a job cancelled between the emit and the GUI thread dequeuing it
        would otherwise open a modal dialog for an install that no longer
        exists, with nothing left waiting for the answer (review, 2026-08-22).

        The dialog stays application-modal on purpose: it is only ever opened
        for a prompt a child process is genuinely blocked on, so there is
        nothing else useful to do in the window until it is answered or
        dismissed. What made modality harmful before was opening it over
        ordinary build output, which the marker gate ended.
        """
        if self._cancel is not None and self._cancel.is_set():
            self._answered.set()
            return
        dialog = self._dialog_for(prompt, secret)
        try:
            ok = dialog.exec() == QDialog.DialogCode.Accepted
            text = dialog.textValue()
        finally:
            # The typed text is not left on a dialog waiting for deletion.
            dialog.setTextValue("")
            dialog.deleteLater()
        self._answer = text if ok else None
        self._answered.set()

    def _dialog_for(self, prompt: str, secret: bool) -> QInputDialog:
        """The dialog `_show` puts up for this prompt, built but not yet shown."""
        parent = self.parent()
        dialog = QInputDialog(parent if isinstance(parent, QWidget) else None)
        dialog.setWindowTitle(self._title)
        dialog.setInputMode(QInputDialog.InputMode.TextInput)
        dialog.setLabelText(explain(prompt, self._purpose))
        dialog.setTextEchoMode(QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
        dialog.setTextValue("")
        return dialog
