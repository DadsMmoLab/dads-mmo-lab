"""The Server tab's one Docker banner: what Yu'lon could not ask, and what to do (T194).

Shown while the tab's status poll cannot reach Docker, hidden by the first
poll that can. The words come from `docker_advice`; this only draws them.
Open Docker Desktop and Try again are its own; a press that lives elsewhere
in the tab (the SteamOS reinstall) is added with `add_press()` and keeps its
own visibility rules.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout, QWidget

from yulon.docker_advice import Advice
from yulon.ui.theme import COLOR_BG_PARCHMENT, COLOR_TEXT_WARNING
from yulon.ui.widgets.flow_layout import flow_bar

BANNER_NAME = "docker-banner"
OPEN_DOCKER_DESKTOP = "Open Docker Desktop"
TRY_AGAIN = "Try again"


class DockerBanner(QFrame):
    """An amber-bordered box: a title, the advice, a press row, and a line for a press's answer."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(BANNER_NAME)
        # By ID, so nothing inside the box is restyled (T188 C1).
        self.setStyleSheet(
            f"QFrame#{BANNER_NAME} {{ background-color: {COLOR_BG_PARCHMENT}; "
            f"border: 1px solid {COLOR_TEXT_WARNING}; }}"
        )
        box = QVBoxLayout(self)
        box.setContentsMargins(8, 6, 8, 6)
        self.title_label = QLabel("", self)
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(f"color: {COLOR_TEXT_WARNING}; font-weight: bold;")
        self.body_label = QLabel("", self)
        self.body_label.setWordWrap(True)
        self.body_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse  # so a command can be copied
        )
        self.open_button = QPushButton(OPEN_DOCKER_DESKTOP, self)
        self.open_button.setProperty("primary", True)
        self.retry_button = QPushButton(TRY_AGAIN, self)
        self.presses = flow_bar(self)
        self.presses.flow().addWidget(self.open_button)
        self.presses.flow().addWidget(self.retry_button)
        # What the last Open Docker Desktop press came back with; hidden while empty.
        self.note_label = QLabel("", self)
        self.note_label.setWordWrap(True)
        self.note_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.note_label.setVisible(False)
        box.addWidget(self.title_label)
        box.addWidget(self.body_label)
        box.addWidget(self.presses)
        box.addWidget(self.note_label)
        self.setVisible(False)

    def add_press(self, button: QPushButton) -> None:
        """Put `button` in the press row, before Try again; its visibility stays its owner's."""
        flow = self.presses.flow()
        flow.removeWidget(self.retry_button)
        button.setParent(self.presses)
        flow.addWidget(button)
        flow.addWidget(self.retry_button)

    def show_advice(self, advice: Advice) -> None:
        """Say `advice`, with Open Docker Desktop only where it is the press."""
        self.title_label.setText(advice.title)
        self.body_label.setText(advice.body)
        self.open_button.setVisible(advice.action == "open-desktop")
        self.setVisible(True)

    def say(self, text: str) -> None:
        """The answer to a press, under the presses; "" hides the line."""
        self.note_label.setText(text)
        self.note_label.setVisible(bool(text))

    def withdraw(self) -> None:
        """Docker answered: the box goes, and a press's old answer with it."""
        self.say("")
        self.setVisible(False)
