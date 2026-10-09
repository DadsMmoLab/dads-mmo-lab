"""The Modules tab's box for client add-ons the player brings (T613 PR-3).

Three presses (a link, a folder, a zip) and, once there are some, the add-ons
Yu'lon put in the game client with an Update and a Remove for the one chosen.
Only a widget: the view owns what a press does (`ControllerView`), this owns what
is drawn and which press is live. Its words are constants so a test, and the
refusal that points at a button by name, read the same string.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from PySide6.QtCore import Signal, SignalInstance
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

ADDON_BOX_TITLE = "Game add-ons you bring"

ADDON_BOX_NOTE = (
    "Add a game add-on from a link (a GitHub, GitLab or Codeberg repository or a .zip link "
    "there), or from a folder or a zip already on this computer. It goes into the game client "
    "you play with. Add-ons you put there yourself are never touched."
)

ADDON_LINK_LABEL = "Add-on from link…"
ADDON_FOLDER_LABEL = "Add-on from folder…"
ADDON_ZIP_LABEL = "Add-on from zip…"
ADDON_UPDATE_LABEL = "Update"
ADDON_REMOVE_LABEL = "Remove"
ADDON_LIST_LABEL = "Added by Yu'lon:"

ADDON_BUSY_TIP = "Wait for the job that is running to finish."
ADDON_LINK_TIP = "Paste a repository link or a link to a .zip file."
ADDON_FOLDER_TIP = "Choose a folder holding an add-on, or a folder of add-ons."
ADDON_ZIP_TIP = "Choose a .zip file holding an add-on."


@dataclass(frozen=True)
class AddonRow:
    """One add-on Yu'lon put in the client: its record's id and what the list says."""

    id: str
    text: str


class ClientAddonsBox(QGroupBox):
    """The box. Its presses are signals; `set_rows()` and `set_busy()` are its two inputs."""

    link_pressed = Signal()
    folder_pressed = Signal()
    zip_pressed = Signal()
    update_pressed = Signal(str)
    remove_pressed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(ADDON_BOX_TITLE, parent)
        self._busy = False
        column = QVBoxLayout(self)
        self.note = QLabel(ADDON_BOX_NOTE, self)
        self.note.setWordWrap(True)
        column.addWidget(self.note)

        presses = QHBoxLayout()
        self.link_button = QPushButton(ADDON_LINK_LABEL, self)
        self.folder_button = QPushButton(ADDON_FOLDER_LABEL, self)
        self.zip_button = QPushButton(ADDON_ZIP_LABEL, self)
        for button, tip, signal in (
            (self.link_button, ADDON_LINK_TIP, self.link_pressed),
            (self.folder_button, ADDON_FOLDER_TIP, self.folder_pressed),
            (self.zip_button, ADDON_ZIP_TIP, self.zip_pressed),
        ):
            button.setToolTip(tip)
            button.clicked.connect(lambda _checked=False, s=signal: s.emit())
            presses.addWidget(button)
        presses.addStretch(1)
        column.addLayout(presses)

        self.list_row = QWidget(self)
        listing = QHBoxLayout(self.list_row)
        listing.setContentsMargins(0, 0, 0, 0)
        self.list_label = QLabel(ADDON_LIST_LABEL, self.list_row)
        self.choice = QComboBox(self.list_row)
        self.choice.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.choice.setMinimumContentsLength(24)
        self.update_button = QPushButton(ADDON_UPDATE_LABEL, self.list_row)
        self.remove_button = QPushButton(ADDON_REMOVE_LABEL, self.list_row)
        self.update_button.clicked.connect(lambda: self._chosen(self.update_pressed))
        self.remove_button.clicked.connect(lambda: self._chosen(self.remove_pressed))
        listing.addWidget(self.list_label)
        listing.addWidget(self.choice, 1)
        listing.addWidget(self.update_button)
        listing.addWidget(self.remove_button)
        column.addWidget(self.list_row)
        self.list_row.setVisible(False)

    def _chosen(self, signal: SignalInstance) -> None:
        item_id = self.choice.currentData()
        if isinstance(item_id, str) and not self._busy:
            signal.emit(item_id)

    def set_rows(self, rows: Sequence[AddonRow]) -> None:
        """The add-ons listed, keeping the chosen one when it is still there."""
        keep = self.choice.currentData()
        self.choice.clear()
        for row in rows:
            self.choice.addItem(row.text, row.id)
        index = self.choice.findData(keep)
        if index >= 0:
            self.choice.setCurrentIndex(index)
        self.list_row.setVisible(bool(rows))
        self._apply()

    def set_busy(self, busy: bool) -> None:
        """Grey every press while a job that writes the client or `modules/` runs."""
        self._busy = busy
        self._apply()

    def _apply(self) -> None:
        live = not self._busy
        tip = ADDON_BUSY_TIP
        for button, own in (
            (self.link_button, ADDON_LINK_TIP),
            (self.folder_button, ADDON_FOLDER_TIP),
            (self.zip_button, ADDON_ZIP_TIP),
        ):
            button.setEnabled(live)
            button.setToolTip(own if live else tip)
        for button in (self.update_button, self.remove_button):
            button.setEnabled(live and self.choice.count() > 0)
            button.setToolTip("" if live else tip)
