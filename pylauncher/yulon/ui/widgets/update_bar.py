"""The row under the header that says an update exists (T90).

Hidden unless it has something to say, which is most of the time: an update is
news for one launch out of many, and a permanently visible strip is a
permanently smaller Catalog tab.

It replaces a bare `QLabel` banner whose text was HTML built by interpolating
the tag straight out of the GitHub API into an `<a href=...>`. Nothing here is
markup: the label is `PlainText`, so a tag is shown, never rendered, and the
link it used to carry is now a button that opens the what's-new dialog.
"""

from __future__ import annotations

import html

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFontMetrics, QResizeEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from yulon.update import UpdateCheck


def as_plain_tooltip(text: str) -> str:
    """`text` as a tooltip that shows exactly `text`, whatever is in it.

    A tooltip is sniffed: Qt decides between plain and rich text with
    `Qt.mightBeRichText()`, and measured on 6.11 that answers **True** for
    `HTTP Error 403: <img src=x>` — a sentence this bar really can be handed,
    because an error from the check is shown verbatim. As rich text it rendered
    as `HTTP Error 403: ￼`: the tag became an image placeholder, i.e. a name Qt
    would go and resolve.

    So the sniff is decided rather than avoided — escaped, then wrapped, which
    makes the answer True on purpose and the content inert. Measured on the
    same build: the wrapped form round-trips through `QTextDocument.setHtml()`
    back to the original characters.
    """
    return f"<span style='white-space:pre-wrap'>{html.escape(text)}</span>"


class UpdateBar(QWidget):
    """One line and one button. `show_update` / `show_message` / `clear` are the whole API."""

    details_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("update-bar")
        self._text = ""
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 2, 14, 2)
        self.label = QLabel(self)
        self.label.setObjectName("update-bar-text")
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        # Selectable, because a message can be the only route the player has:
        # on a box with no browser and no `xdg-open` (yulon-arch, gate of
        # 2026-09-21) the download URL is printed here and nowhere else. Still
        # PlainText — the text comes off the network — and still elided, so the
        # clipboard copy that goes with that message is what carries the whole
        # URL; a selection can only ever be what is on screen.
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Ignored horizontally, and elided in `resizeEvent` below: the tag comes
        # off the network, so its length is not this app's to promise, and the
        # window's 960px minimum is measured without this row (MINIMUM_WINDOW_SIZE).
        # A label that asked for its natural width would raise that minimum by
        # however long somebody's tag happened to be.
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.details_button = QPushButton("See what's new", self)
        self.details_button.setObjectName("update-see-whats-new")
        self.details_button.clicked.connect(self.details_requested)
        row.addWidget(self.label, 1)
        row.addWidget(self.details_button, 0)
        self.setVisible(False)

    def text(self) -> str:
        """What the bar says, in full — the label itself may be showing it elided."""
        return self._text

    def show_update(self, result: UpdateCheck) -> None:
        """An update is on offer: say which, and offer to show what is in it."""
        self._say(f"Yu'lon {result.latest} is available (you have {result.current}).")
        self.details_button.setVisible(True)
        self.setVisible(True)

    def show_message(self, text: str, *, keep_details: bool = False) -> None:
        """Say one thing. `keep_details` leaves "See what's new" where it is.

        The default hides it, because most messages ("You have the newest
        version") have nothing to open. `keep_details=True` is for a message
        shown while an offer is still standing — the browser that would not
        open, where hiding the button would take away the player's only
        remaining route back to the release notes.
        """
        self._say(text)
        if not keep_details:
            self.details_button.setVisible(False)
        self.setVisible(True)

    def clear(self) -> None:
        """Nothing to say; the row gives its height back to the tabs."""
        self.setVisible(False)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _say(self, text: str) -> None:
        self._text = text
        self.label.setToolTip(as_plain_tooltip(text))
        self._elide()

    def _elide(self) -> None:
        """Fit the sentence to the label's current width, with a tail of dots.

        Elided rather than clipped: a clipped line is cut mid-glyph with nothing
        to say it was cut, and the whole sentence is on the tooltip either way.
        """
        metrics = QFontMetrics(self.label.font())
        self.label.setText(
            metrics.elidedText(self._text, Qt.TextElideMode.ElideRight, max(0, self.label.width()))
        )
