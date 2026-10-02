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
from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, Signal
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


FADE_MS = 15_000
"""How long a message said with `fade=True` stays before the bar clears itself (T179).

For the announcements nothing else ever clears in a session -- "Updated to Yu'lon X",
"You have the newest version" -- so a notice waiting behind one gets its turn."""


class UpdateBar(QWidget):
    """One line and one button: `show_update`, `show_message` (optionally fading),
    `offer_notice` (a lower-ranked notice that waits for the bar to be idle) and
    `clear` are the whole API."""

    details_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("update-bar")
        self._text = ""
        # T179: a notice (`offer_notice`) waiting for the bar to be idle, and whether
        # the one on show now is one -- an offer or a message arriving over it wins.
        self._waiting: tuple[str, Callable[[], None] | None] | None = None
        self._notice_on_show = False
        # Every `_say` starts a new message; a fade timer clears only its own.
        self._said = 0
        self._fading: int | None = None
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
        self._step_aside()
        self._say(f"Yu'lon {result.latest} is available (you have {result.current}).")
        self.details_button.setVisible(True)
        self.setVisible(True)

    def show_message(self, text: str, *, keep_details: bool = False, fade: bool = False) -> None:
        """Say one thing. `keep_details` leaves "See what's new" where it is.

        The default hides it, because most messages ("You have the newest
        version") have nothing to open. `keep_details=True` is for a message
        shown while an offer is still standing — the browser that would not
        open, where hiding the button would take away the player's only
        remaining route back to the release notes.
        """
        self._step_aside()
        self._say(text)
        if not keep_details:
            self.details_button.setVisible(False)
        self.setVisible(True)
        if fade:
            said = self._fading = self._said
            QTimer.singleShot(FADE_MS, self, lambda: self._fade(said))

    def fading(self) -> bool:
        """Whether what the bar says now will clear itself (`show_message(fade=True)`)."""
        return self._fading is not None and self._fading == self._said

    def _fade(self, said: int) -> None:
        if said == self._said and not self.isHidden():
            self.clear()

    def offer_notice(self, text: str, on_shown: Callable[[], None] | None = None) -> None:
        """Say `text` when nothing else is being said (T179 Task 5, fix round 1).

        A notice ranks below an update offer and an announcement ("Updated to
        Yu'lon X"): over either it waits and is said when the bar clears, and an
        offer or a message arriving over it puts it back to wait. One waits at a
        time; a newer one replaces it. `on_shown` is called once, the first time the
        notice is actually on the bar -- never while it only waits.
        """
        if self.isHidden():
            self._show_notice(text, on_shown)
        else:
            self._waiting = (text, on_shown)

    def clear(self) -> None:
        """Nothing to say; the row gives its height back to the tabs -- or a waiting notice."""
        self._notice_on_show = False
        waiting, self._waiting = self._waiting, None
        if waiting is not None:
            self._show_notice(*waiting)
            return
        self.setVisible(False)

    def _show_notice(self, text: str, on_shown: Callable[[], None] | None) -> None:
        self._say(text)
        self.details_button.setVisible(False)
        self.setVisible(True)
        self._notice_on_show = True
        if on_shown is not None:
            on_shown()

    def _step_aside(self) -> None:
        """A notice on show waits again under what is about to be said instead.

        Its `on_shown` was spent when it first showed, so it comes back without one.
        """
        if self._notice_on_show:
            self._notice_on_show = False
            if self._waiting is None:
                self._waiting = (self._text, None)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._elide()

    def _say(self, text: str) -> None:
        self._said += 1
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
