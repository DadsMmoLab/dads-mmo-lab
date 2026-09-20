"""What is new in the release on offer, and the player's choice about it (T90).

The banner says an update exists; this says what is in it. The text is the
GitHub release body, which since plan 1 is built from `CHANGELOG.md` — so the
sentences a player reads here are the ones written for them, rather than a list
of commit subjects.

`choice` is read after `exec()` returns. It is LATER until a button says
otherwise, so Esc, the window's close button and a dialog dismissed by the
window manager are all "not now" and none of them is a skip.

Not themed here: `apply_dadcraft_theme(window)` styles `QDialog` through the
top-level stylesheet, which a dialog parented to the window inherits — the same
reason `ManifestPromptDialog` does not apply it either. Gamepad reachability
needs nothing either: `Navigator._context_root()` derives its subtree from Qt's
own modality, so an application-modal dialog IS the navigation context while it
is up.
"""

from __future__ import annotations

import enum

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from yulon.update import UpdateCheck

_NO_NOTES = "No release notes were published for this version."

_SERVERS_KEEP_RUNNING = "Your game servers keep running while Yu'lon updates."
"""Said in the dialog because it is the question a player has.

The servers run in Docker containers this process does not own; restarting the
app does not touch them.
"""


def as_shown_markdown(body: str) -> str:
    """The release body with every `<` neutralised, so raw HTML is shown and not parsed.

    Measured on PySide6 6.11 (T90): `setMarkdown()` hands a raw HTML block
    straight through to the document, and md4c swallows everything up to the
    next blank line with it — a body reading

        <img src='...'><script>x</script>
        - Ten.

    rendered as the heading alone. The bullet was GONE from the dialog, with
    nothing to say it had been dropped, and the `<img>` was a URL chosen by
    whoever wrote the release body that the widget would then go and fetch.

    `&lt;` is an entity markdown renders as a literal `<`, so the tag is shown
    to the reader as text and the rest of the body survives. `&` is left alone:
    escaping it too would turn every deliberate entity in a body into visible
    source, and an entity cannot start a tag.
    """
    return body.replace("<", "&lt;")


class _NotesView(QTextBrowser):
    """The notes box, which fetches nothing.

    `loadResource` is the one door a document has to the outside: an image in
    the body — markdown's own `![](http://…)` as much as a raw `<img>` — is
    GET-ed when the dialog opens, from a URL nobody in this process chose, with
    no user action at all. Shutting the door here covers every shape of it,
    which escaping the source alone does not.
    """

    def loadResource(self, type_: int, name: QUrl | str) -> object:
        return None


class UpdateChoice(enum.Enum):
    """What the player pressed. LATER is what every other way out means."""

    UPDATE = "update"
    LATER = "later"
    SKIP = "skip"


class UpdateDialog(QDialog):
    """Title, "you have X", the notes, and three buttons."""

    def __init__(
        self,
        result: UpdateCheck,
        *,
        action_label: str = "Open download page",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.choice = UpdateChoice.LATER
        self.setWindowTitle(f"Update to Yu'lon {result.latest}")
        self.setModal(True)
        self.resize(640, 480)
        column = QVBoxLayout(self)

        current = QLabel(f"You have {result.current}. {_SERVERS_KEEP_RUNNING}", self)
        current.setObjectName("update-current")
        current.setWordWrap(True)
        column.addWidget(current)

        notes = _NotesView(self)
        notes.setObjectName("update-notes")
        # The body is written by whoever cut the release. `setOpenExternalLinks`
        # sends a clicked link to the system browser instead of navigating this
        # widget to it, so the box can only ever show the text it was given.
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(as_shown_markdown(result.notes_markdown.strip()) or _NO_NOTES)
        column.addWidget(notes, 1)

        buttons = QHBoxLayout()
        # Kept in a dict rather than looked up again with `findChild`, which
        # mypy reads as possibly-None at every call. A caller that wants one
        # asks for it by the objectName below — that is the contract the tests
        # and the gate screenshots use.
        self._buttons: dict[UpdateChoice, QPushButton] = {}
        for name, label, choice in (
            ("update-action", action_label, UpdateChoice.UPDATE),
            ("update-later", "Later", UpdateChoice.LATER),
            ("update-skip", "Skip this version", UpdateChoice.SKIP),
        ):
            button = QPushButton(label, self)
            button.setObjectName(name)
            # A lambda is fine HERE: this is a GUI-thread signal on a GUI-thread
            # widget. `job.py`'s bound-slot rule is about a WORKER thread's
            # signal, which a plain callable would be delivered on.
            button.clicked.connect(lambda _checked=False, c=choice: self._choose(c))
            buttons.addWidget(button)
            self._buttons[choice] = button
        column.addLayout(buttons)
        self._buttons[UpdateChoice.UPDATE].setDefault(True)

    def _choose(self, choice: UpdateChoice) -> None:
        self.choice = choice
        self.accept()
