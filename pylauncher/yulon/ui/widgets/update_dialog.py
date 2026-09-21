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
`SAFE_MARKDOWN`, `_strip_resources` and `_NotesView`): markdown that renders a
file, a document that still names one in a format, and a link whose scheme is
not http.

Not themed here: `apply_dadcraft_theme(window)` styles `QDialog` through the
top-level stylesheet, which a dialog parented to the window inherits — the same
reason `ManifestPromptDialog` does not apply it either. Gamepad reachability
needs nothing either: `Navigator._context_root()` derives its subtree from Qt's
own modality, so an application-modal dialog IS the navigation context while it
is up.
"""

from __future__ import annotations

import enum
from collections.abc import Callable

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import (
    QBrush,
    QDesktopServices,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextTable,
)
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
from yulon.update import MAX_NOTES_CHARS, UpdateCheck, clipped_notes

logger = get_logger(__name__)

_NO_NOTES = "No release notes were published for this version."

_SERVERS_KEEP_RUNNING = "Your game servers keep running while Yu'lon updates."
"""Said in the dialog because it is the question a player has.

The servers run in Docker containers this process does not own; restarting the
app does not touch them.
"""

IMAGE_REMOVED = "🖼"
"""What an image in a release body is replaced with: a character, never a fetch."""


def default_open_url(url: str) -> bool:
    """Hand `url` to the desktop. **False means nothing opened**, and it happens.

    `QDesktopServices.openUrl()` answers a bool and the app used to throw it
    away: on yulon-arch (gate, 2026-09-21) — no browser installed and no
    `xdg-open`, only `exo-open` and `gio` — pressing "Open download page"
    closed the dialog and did nothing, silently, leaving the player with no
    route to the release at all.

    **What False does NOT catch, measured on yulon-ubuntu the same day:** where
    `xdg-open` exists, it returns True as soon as the helper is *started*. A
    snap Firefox that then dies for want of a display is a successful open as
    far as Qt is concerned. Nothing here can see that, and this does not try —
    the fallback is for the answer Qt does give.
    """
    return bool(QDesktopServices.openUrl(QUrl(url)))


_OPENABLE_SCHEMES = ("http", "https")
"""The only schemes a link in the notes may hand to the desktop.

`file:`, `smb:` and a bare `//host/share` path are the ones this excludes, and
they are excluded because opening one is not "showing a page": on Windows a UNC
path is an SMB connection, which is an NTLM handshake with a host the release
body named.
"""

SAFE_MARKDOWN = (
    QTextDocument.MarkdownFeature.MarkdownDialectGitHub
    | QTextDocument.MarkdownFeature.MarkdownNoHTML
)
"""How a release body is parsed: GitHub's dialect, with raw HTML not interpreted.

**This replaced a regex that tried to escape every `<` outside code**, and it
replaced it because that regex had to agree with md4c about where a fenced
block begins and ends — and did not. Measured through the real dialog on 6.11
(second cold review, 2026-09-21), each of these painted a file off the disk the
moment the dialog opened, **59,978 red pixels** in a `grab()`:

* a backtick in the info string of the opening fence, so md4c reads a paragraph
  where the regex read a fence opener, and everything the regex then left alone
  arrived at `setMarkdown` as raw HTML;
* a closing fence indented three spaces, which md4c honours and the regex did
  not.

And what arrived was not an image: `<table background='…'>` and
`<td background='…'>` put the file on the format's **brush**. A brush is not an
image char-format, so the walk below never saw it, and `loadResource` returning
None does not stop Qt painting it.

`MarkdownNoHTML` ends the whole class of bug at its cause — nothing has to
guess where a fence is, because no HTML is interpreted anywhere. Measured on
the same build, with it: all of the above render as literal text and load
nothing; `` `<config_dir>` `` and a fenced `<not a tag>` finally show their
angle brackets instead of `&lt;`; `<https://…>` autolinks keep their href (an
autolink is not HTML); the GitHub dialect's tables and strikethrough still
parse; and the older "a raw HTML block swallows the rest of the body" defect is
gone with its cause rather than worked around.

It does NOT stop a markdown image (`![i](file:///…)` still loaded, 60,000 red
pixels) — that is what `_strip_resources` is for.
"""


def _mark_format(replaced: QTextCharFormat) -> QTextCharFormat:
    """The format the 🖼 mark is written in: nothing of the image, but the link.

    `[![badge](img)](https://…)` is an image INSIDE a link, and replacing it
    with an empty format dropped the anchor with it, so every badge link in a
    release body went dead (third cold review, 2026-09-21). The href and its
    underline are the author's text, not the image's resource, so they stay.
    """
    kept = QTextCharFormat()
    if replaced.isAnchor():
        kept.setAnchor(True)
        kept.setAnchorHref(replaced.anchorHref())
        kept.setFontUnderline(True)
    return kept


def _is_textured(brush: QBrush) -> bool:
    """Does this brush name something Qt would have to go and load?"""
    return brush.style() == Qt.BrushStyle.TexturePattern or not brush.textureImage().isNull()


def _strip_resources(document: QTextDocument) -> int:
    """Clear every format in `document` that names a resource. Returns how many.

    Walks more than images, and the second cold review is why: an image lives on
    a char format, but a background lives on a **brush**, and a brush can hang
    off a frame, a table, a table cell, a block or a run of characters.
    `isImageFormat()` sees none of those, and neither does `loadResource`.

    Run while the document belongs to no widget, so nothing has been laid out
    and no name has been resolved when the formats go.

    **Which door catches what, measured rather than assumed** (red pixels in a
    `grab()` of the notes box, one 300x200 red PNG named by the body):

        case              no doors   flag only   walk only   both
        fence-a + table     59,978           0           0      0
        fence-b + table     59,978           0           0      0
        bare table          59,978           0           0      0
        markdown image      60,000      60,000           0      0

    So the IMAGE half of this walk is load-bearing — a markdown image is not
    HTML and the flag does not touch it — and the BRUSH half is belt and
    braces: with the flag on, no release body can put a texture on a format at
    all, and dropping the flag alone leaves the table cases green because this
    walk catches them too. That is the point of having two, and it is why
    dropping the flag reddens the "swallows nothing" tests rather than the
    table ones. The brush half is driven directly by
    `test_the_walk_clears_a_textured_brush_no_markdown_can_currently_make`,
    which builds a document Qt cannot currently be talked into building.
    """
    cleared = 0
    cursor = QTextCursor(document)

    frames = [document.rootFrame()]
    while frames:
        frame = frames.pop()
        frames.extend(frame.childFrames())
        frame_format = frame.frameFormat()
        if _is_textured(frame_format.background()):
            frame_format.clearBackground()
            frame.setFrameFormat(frame_format)
            cleared += 1
        if isinstance(frame, QTextTable):
            for row in range(frame.rows()):
                for column in range(frame.columns()):
                    cell = frame.cellAt(row, column)
                    cell_format = cell.format()
                    if cell.isValid() and _is_textured(cell_format.background()):
                        cell_format.clearBackground()
                        cell.setFormat(cell_format)
                        cleared += 1

    images: list[tuple[int, int, QTextCharFormat]] = []
    brushes: list[tuple[int, int, QTextCharFormat]] = []
    block = document.begin()
    while block.isValid():
        block_format = block.blockFormat()
        if _is_textured(block_format.background()):
            block_format.clearBackground()
            cursor.setPosition(block.position())
            cursor.setBlockFormat(block_format)
            cleared += 1
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                char_format = fragment.charFormat()
                where = (fragment.position(), fragment.length(), char_format)
                if char_format.isImageFormat():
                    images.append(where)
                elif _is_textured(char_format.background()):
                    brushes.append(where)
            iterator += 1
        block = block.next()

    # ONE edit block for the lot. Measured (third cold review, 2026-09-21): a
    # body of `"![a](x) " * 20000` — 160 KB, which fits in one GitHub release
    # body — spent **22.8 s** here against 0.03 s in `setMarkdown`, because
    # every `insertText` re-laid the document out and pushed an undo step. The
    # same body is now well under a second. Undo is off for the same reason:
    # nothing can undo a document this app builds and then hands over.
    undo_was = document.isUndoRedoEnabled()
    document.setUndoRedoEnabled(False)
    cursor.beginEditBlock()
    try:
        # Back to front: every replacement moves the positions after it.
        for position, length, char_format in reversed(images):
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(IMAGE_REMOVED, _mark_format(char_format))
            cleared += 1
        for position, length, char_format in reversed(brushes):
            # A background is cleared, NOT replaced: the text under it is the
            # author's and has done nothing wrong.
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.MoveMode.KeepAnchor)
            plain = QTextCharFormat(char_format)
            plain.clearBackground()
            cursor.setCharFormat(plain)
            cleared += 1
    finally:
        cursor.endEditBlock()
        document.setUndoRedoEnabled(undo_was)

    # Nothing may leave a base that a relative name could be resolved against.
    document.setBaseUrl(QUrl())
    document.setMetaInformation(QTextDocument.MetaInformation.DocumentUrl, "")
    if cleared:
        logger.info(f"release notes: {cleared} resource(s) cleared, none fetched")
    return cleared


class _NotesView(QTextBrowser):
    """The notes box: it renders a release body and reaches nothing.

    **There is no `loadResource` override any more, and that is a measurement
    rather than a tidy-up.** It was here to refuse resources; it never refused
    one. With it removed this widget renders a document holding an image in
    **60,000 red pixels** — byte for byte what a plain `QTextBrowser` does —
    because Qt's own image handling opens the path when the resource comes back
    null (measured twice: first cold review for the image case, third for this
    one). Deleting the call was invisible to all 68 tests, which is the
    definition of the dead defence the second review made this file remove
    elsewhere. What actually stops a resource is `SAFE_MARKDOWN` and
    `_strip_resources`, before this widget is ever given the document.

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
        self.open_url: Callable[[str], bool] = default_open_url
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self._clicked)

    def set_release_body(self, markdown: str) -> int:
        """Show `markdown`, with the images taken out before this widget owns it.

        Built in a document of its own and handed over afterwards, so the
        stripping happens while nothing is laid out — a widget that already
        held the document could resolve an image name on the way.
        """
        # Capped again here. `evaluate_feed` caps what it assembles, but
        # `notes_markdown` is an ordinary field and this widget is public: a
        # caller that built an `UpdateCheck` by hand would otherwise hand Qt a
        # body that takes minutes to lay out on the GUI thread.
        document = QTextDocument(self)
        document.setMarkdown(clipped_notes(markdown, MAX_NOTES_CHARS), SAFE_MARKDOWN)
        removed = _strip_resources(document)
        self.setDocument(document)
        return removed

    def _clicked(self, url: QUrl) -> None:
        """A link in the release notes was clicked."""
        self.open_link(url)

    def open_link(self, url: QUrl) -> bool:
        """Open `url` if its scheme is allowed. False = refused, or the opener could not.

        The answer is returned rather than dropped: an opener that cannot open
        anything is the yulon-arch case, and somebody has to tell the player.
        """
        # No `.lower()`: `QUrl` lower-cases a scheme when it parses one, so the
        # call did nothing and removing it left every test green. Dead defence
        # reads like a guard and defends nothing.
        if url.scheme() not in _OPENABLE_SCHEMES:
            logger.info(f"release notes: refused to open a {url.scheme()!r} link")
            return False
        return bool(self.open_url(url.toString()))


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
        open_url: Callable[[str], bool] | None = None,
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
        # One opener for both routes out of this dialog — the action button and
        # a link in the notes — so one place decides what happens when it fails.
        if open_url is not None:
            notes.open_url = open_url
        notes.set_release_body(result.notes_markdown.strip() or _NO_NOTES)
        self.notes = notes
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
