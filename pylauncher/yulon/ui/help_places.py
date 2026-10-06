"""Where to get help: the places that look after each part of a server (T520).

The owner (2026-10-06) wanted players told where to take a problem: the
server's own project for a server bug, the bot project for a bot bug, the
community for a question, and Yu'lon for Yu'lon. Which places those are is
catalog data (`CatalogEntry.help_places`); this is the box that lists them.

The box is the app's fitted question box (`FittedMessageBox`, T243), so it fits
the screen it opens on however many places a game has. Each place is a link
button with one line beside it; the buttons scroll with the words, and the pad
and Tab stop on each. A press opens the page in the player's browser and leaves
the box open, so a second place can be opened too. Close, or Escape, puts it away.
"""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGridLayout, QLabel, QMessageBox, QPushButton, QWidget

from yulon.catalog.catalog import CatalogEntry, HelpPlace
from yulon.log import get_logger
from yulon.ui.message_box import FittedMessageBox, show_warning

logger = get_logger(__name__)

HELP_BUTTON = "Where to get help…"
"""The Server tab's press that opens the box."""

HELP_TITLE = "Where to get help"

HELP_INTRO = (
    "Each place below looks after one part of {name}. Pick the one that matches your "
    "problem, and it opens in your web browser."
)

BROWSER_REFUSED_TITLE = "Could not open your browser"
BROWSER_REFUSED = "Yu'lon could not open your web browser. The address is:\n\n{url}"

HELP_PURPOSE = "help-place-purpose"
"""The object name of each place's line."""


class HelpPlacesBox(FittedMessageBox):
    """The places for one game, a link button each, in a box that fits the screen."""

    def __init__(self, parent: QWidget | None, name: str, places: Sequence[HelpPlace]) -> None:
        # Made with no text, then given it: the base class lays the question out
        # in its own constructor, before `_places` could be set for the rows.
        super().__init__(
            QMessageBox.Icon.Information, HELP_TITLE, "", QMessageBox.StandardButton.Close, parent
        )
        self._places = tuple(places)
        self._links: list[QPushButton] = []
        self._purposes: list[QLabel] = []
        self.setDefaultButton(QMessageBox.StandardButton.Close)
        self.setEscapeButton(QMessageBox.StandardButton.Close)
        self.setText(HELP_INTRO.format(name=name))

    @property
    def link_buttons(self) -> list[QPushButton]:
        """The places' presses, in the catalog's order."""
        return list(self._links)

    def purpose_texts(self) -> list[str]:
        """Each place's line, in the same order."""
        return [label.text() for label in self._purposes]

    def _rows_under_the_question(self) -> list[QWidget]:
        places = getattr(self, "_places", ())
        if not places:
            return []
        self._links, self._purposes = [], []
        table = QWidget()
        grid = QGridLayout(table)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setColumnStretch(1, 1)
        for row, place in enumerate(places):
            button = QPushButton(place.label, table)
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.setToolTip(place.url)
            button.clicked.connect(lambda _=False, url=place.url: self._open(url))
            purpose = QLabel(place.purpose, table)
            purpose.setObjectName(HELP_PURPOSE)
            purpose.setTextFormat(Qt.TextFormat.PlainText)
            purpose.setWordWrap(True)
            grid.addWidget(button, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(purpose, row, 1, Qt.AlignmentFlag.AlignVCenter)
            self._links.append(button)
            self._purposes.append(purpose)
        return [table]

    def _open(self, url: str) -> None:
        """Open `url` in the browser; say the address when the system will not."""
        opened = QDesktopServices.openUrl(QUrl(url))
        logger.info(
            f"Where to get help: opened {url}"
            if opened
            else f"Where to get help: the system would not open {url}"
        )
        if not opened:
            show_warning(self, BROWSER_REFUSED_TITLE, BROWSER_REFUSED.format(url=url))


def show_help_places(parent: QWidget | None, entry: CatalogEntry) -> None:
    """Show `entry`'s places until the player closes the box."""
    box = HelpPlacesBox(parent, entry.name, entry.help_places)
    try:
        box.exec()
    finally:
        box.deleteLater()
