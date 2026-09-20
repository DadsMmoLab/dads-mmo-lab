"""What is new in the release on offer, and the player's choice about it (T90).

The banner says an update exists; this says what is in it. The text is the
GitHub release body, which since plan 1 is built from `CHANGELOG.md` — so the
sentences a player reads here are the ones written for them, rather than a list
of commit subjects.

`choice` is read after `exec()` returns. It is LATER until a button says
otherwise, so Esc, the window's close button and a dialog dismissed by the
window manager are all "not now" and none of them is a skip.

**The body is text from the network and this file is where that is contained.**
Three separate doors, because each of them was measured open (see
`as_shown_markdown`, `_NotesView` and `_strip_images`): markdown that renders an
image, a document that still holds an image fragment, and a link whose scheme
is not http.

Not themed here: `apply_dadcraft_theme(window)` styles `QDialog` through the
top-level stylesheet, which a dialog parented to the window inherits — the same
reason `ManifestPromptDialog` does not apply it either. Gamepad reachability
needs nothing either: `Navigator._context_root()` derives its subtree from Qt's
own modality, so an application-modal dialog IS the navigation context while it
is up.
"""

from __future__ import annotations

import enum
import re
from collections.abc import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QTextCharFormat, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from yulon.log import get_logger
from yulon.update import UpdateCheck

logger = get_logger(__name__)

_NO_NOTES = "No release notes were published for this version."

_SERVERS_KEEP_RUNNING = "Your game servers keep running while Yu'lon updates."
"""Said in the dialog because it is the question a player has.

The servers run in Docker containers this process does not own; restarting the
app does not touch them.
"""

IMAGE_REMOVED = "🖼"
"""What an image in a release body is replaced with: a character, never a fetch."""

_OPENABLE_SCHEMES = ("http", "https")
"""The only schemes a link in the notes may hand to the desktop.

`file:`, `smb:` and a bare `//host/share` path are the ones this excludes, and
they are excluded because opening one is not "showing a page": on Windows a UNC
path is an SMB connection, which is an NTLM handshake with a host the release
body named.
"""

_LEAVE_ALONE = re.compile(
    r"""
      (?P<fence>^(?P<ticks>`{3,}|~{3,})[^\n]*\n.*?(?:^(?P=ticks)[^\n]*$|\Z))
    | (?P<code>(?P<span>`+)(?!`)[^\n]*?(?<!`)(?P=span)(?!`))
    | (?P<autolink><https?://[^>\s]+>)
    """,
    re.DOTALL | re.MULTILINE | re.VERBOSE,
)
"""The three runs of a release body that must reach markdown exactly as written.

A fenced block and a code span because they are the one place `<` means `<` —
`` `<config_dir>` `` read as prose and escaped shows the reader `&lt;config_dir>`,
which is what this file did until the cold review of 2026-09-21. An autolink
because `<https://…>` is the spelling that makes a bare URL clickable, and
escaping its first character turned every one of them into plain text.
"""


def as_shown_markdown(body: str) -> str:
    """A release body that markdown may render: no raw HTML, and no image.

    Two rewrites, and both were measured rather than assumed, on PySide6 6.11:

    * **`<` outside code becomes `&lt;`.** `setMarkdown()` hands a raw HTML
      block straight through to the document, and md4c swallows everything up
      to the next blank line with it — a body reading

          <img src='...'><script>x</script>
          - Ten.

      rendered as the heading alone. The bullet was GONE from the dialog with
      nothing to say it had been dropped. `&lt;` is an entity markdown renders
      as a literal `<`, so the tag is shown to the reader as text and the rest
      of the body survives.
    * **`![` becomes `[`,** which turns an image into an ordinary link. An
      image is the one markdown construct that reaches outside the process
      with no click at all: `![i](file:///…)` rendered the file (measured:
      1600 red pixels in a `grab()` of the notes box), and `![](//host/x.png)`
      on Windows is an SMB connection to a host the release body chose. As a
      link it is inert until clicked, and `_NotesView` decides what a click may
      open.

    `&` is left alone: escaping it too would turn every deliberate entity in a
    body into visible source, and an entity cannot start a tag.

    This is the FIRST of the two doors on images. `_strip_images` is the other,
    and it exists because this one is a text rewrite and text rewrites are
    guesses about a parser (`_NotesView.setMarkdown`).
    """
    out: list[str] = []
    last = 0
    for match in _LEAVE_ALONE.finditer(body):
        out.append(_neutralise(body[last : match.start()]))
        out.append(match.group())
        last = match.end()
    out.append(_neutralise(body[last:]))
    return "".join(out)


def _neutralise(text: str) -> str:
    """The rewrite of one run of ordinary prose. See `as_shown_markdown`."""
    return text.replace("<", "&lt;").replace("![", "[")


def _strip_images(document: QTextDocument) -> int:
    """Replace every image fragment in `document` with a character. Returns how many.

    The second door, and the one that does not depend on reading markdown the
    way md4c reads it: whatever syntax produced it, an image in the document is
    a `QTextCharFormat` that `isImageFormat()`, and Qt resolves its name when
    the document is laid out. Overriding `loadResource` is NOT enough and that
    was measured: with it returning None, `![i](file:///…/red.png)`, a bare
    absolute path and a reference-style image each still rendered the file
    (1600 red pixels in a `grab()`), because Qt's own image handling opens the
    path when the resource comes back null.

    Applied to a document that no widget owns yet, so nothing has been laid out
    and no name has been resolved when the fragments go.
    """
    spots: list[tuple[int, int]] = []
    block = document.begin()
    while block.isValid():
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid() and fragment.charFormat().isImageFormat():
                spots.append((fragment.position(), fragment.length()))
            iterator += 1
        block = block.next()
    cursor = QTextCursor(document)
    # Back to front: every removal moves the positions after it.
    for position, length in reversed(spots):
        cursor.setPosition(position)
        cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
        # An empty format, or the replacement inherits the image format it replaced.
        cursor.insertText(IMAGE_REMOVED, QTextCharFormat())
    if spots:
        logger.info(f"release notes: {len(spots)} image(s) replaced, none fetched")
    return len(spots)


class _NotesView(QTextBrowser):
    """The notes box: it renders a release body and reaches nothing.

    `loadResource` returning None is kept as the last of the three doors, and
    its docstring no longer claims to be the first: it does not stop an image
    (measured — see `_strip_images`). What it does stop is a resource this
    widget would fetch for any OTHER reason, which costs nothing to refuse.

    Links are NOT handed to `setOpenLinks`/`setOpenExternalLinks`, and both
    halves of why were measured on 6.11 by clicking a real anchor:

    * `[share](smb://host/share)` with `setOpenExternalLinks(True)` **was
      handed to `QDesktopServices`** — a scheme the release body chose, started
      by the desktop. On Windows the same shape over a UNC path is an SMB
      connection, i.e. an NTLM handshake, before it is anything else.
    * `[run](file:///etc/hostname)` was not launched but **navigated to**: the
      widget's `source()` became that path and its text became the file's
      contents (`'PKGame-Laptop'`, off this disk). A release body could put the
      contents of a local file in front of the reader by naming it. Overriding
      `loadResource` did not stop that either.

    So both doors are shut: `setOpenLinks(False)` means a click navigates
    nothing, and `_clicked` opens an http(s) URL and ignores every other
    scheme.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.open_url: Callable[[str], object] = lambda url: QDesktopServices.openUrl(QUrl(url))
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._clicked)

    def set_release_body(self, markdown: str) -> int:
        """Show `markdown`, with the images taken out before this widget owns it.

        Built in a document of its own and handed over afterwards, so the
        stripping happens while nothing is laid out — a widget that already
        held the document could resolve an image name on the way.
        """
        document = QTextDocument(self)
        document.setMarkdown(as_shown_markdown(markdown))
        removed = _strip_images(document)
        self.setDocument(document)
        return removed

    def loadResource(self, type_: int, name: QUrl | str) -> object:
        return None

    def _clicked(self, url: QUrl) -> None:
        """A link in the release notes was clicked. http(s) only, and only outward."""
        if url.scheme().lower() in _OPENABLE_SCHEMES:
            self.open_url(url.toString())
            return
        logger.info(f"release notes: refused to open a {url.scheme()!r} link")


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
        notes.set_release_body(result.notes_markdown.strip() or _NO_NOTES)
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
