"""One "Remove this server…" dialog, with the three removal routes behind it (T695).

The Danger zone's three removal presses ("Stop and remove containers…",
"Remove from Yu'lon…", "Uninstall…") are one press opening this dialog: three
radios ("Only take it off this list", "Uninstall, keep my characters",
"Uninstall everything") and a folded "Advanced" holding
"Stop and remove containers". A game whose `services.uninstall is None` shows
only the first radio and Advanced, plus `UNINSTALL_ABSENT`'s plain line.

The dialog removes nothing itself. It answers a `RemovalChoice`, or None for
Cancel, and the tab routes: "off this list" to `forget_install()`, "keep" to
`keep_characters_check.setChecked(True)` then `show_uninstall_plan()`,
"everything" to the same with False, Advanced to `_remove_containers_now()`.

Not themed here, and no gamepad code: `apply_dadcraft_theme(window)` styles
`QDialog` through the window's stylesheet, and an application-modal dialog is
the navigation context while it is up (`update_dialog.py` explains both).
"""

from __future__ import annotations

import enum
from collections.abc import Sequence

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)


class RemovalChoice(enum.Enum):
    """What the player chose in the remove dialog. None (Cancel) chooses nothing."""

    OFF_THE_LIST = "off_the_list"
    KEEP_CHARACTERS = "keep_characters"
    EVERYTHING = "everything"
    REMOVE_CONTAINERS = "remove_containers"


TITLE = "Remove this server?"
OFF_THE_LIST_LABEL = "Only take it off this list"
KEEP_CHARACTERS_LABEL = "Uninstall, keep my characters"
EVERYTHING_LABEL = "Uninstall everything"
ADVANCED_LABEL = "Advanced"
REMOVE_CONTAINERS_LABEL = "Stop and remove containers…"
ABSENT_LINE = (
    "Uninstall is not offered for this server yet: Stop and remove containers "
    "frees its containers, Only take it off this list takes it off the list, "
    "and the folder stays for you to delete."
)


class RemoveDialog(QDialog):
    """Three radios and a folded Advanced; `choice()` is what is selected now."""

    def __init__(
        self,
        parent: QWidget | None,
        *,
        uninstall_available: bool,
        preselect: RemovalChoice = RemovalChoice.OFF_THE_LIST,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.setModal(True)
        column = QVBoxLayout(self)

        self.off_radio = QRadioButton(OFF_THE_LIST_LABEL, self)
        self.off_radio.setToolTip(
            "Stop listing this server in Yu'lon. Nothing is deleted; you are asked first."
        )
        column.addWidget(self.off_radio)

        self.keep_radio = QRadioButton(KEEP_CHARACTERS_LABEL, self)
        self.keep_radio.setToolTip(
            "Remove the folder, the containers and the images, and keep the database "
            "volume so reinstalling to the same folder finds the characters again."
        )
        column.addWidget(self.keep_radio)

        self.everything_radio = QRadioButton(EVERYTHING_LABEL, self)
        self.everything_radio.setToolTip(
            "Remove the folder, the containers, the images and every volume, "
            "including the characters."
        )
        column.addWidget(self.everything_radio)

        self.absent_label = QLabel(ABSENT_LINE, self)
        self.absent_label.setObjectName("remove-absent")
        self.absent_label.setWordWrap(True)
        column.addWidget(self.absent_label)

        self.advanced_check = QCheckBox(ADVANCED_LABEL, self)
        self.advanced_check.setToolTip(
            "Show the container teardown: it stops the server and deletes its "
            "containers, and keeps the characters."
        )
        column.addWidget(self.advanced_check)

        self.containers_radio = QRadioButton(REMOVE_CONTAINERS_LABEL, self)
        self.containers_radio.setObjectName("remove-containers")
        self.containers_radio.setToolTip(
            "Stop the server and delete its containers. The characters are kept: "
            "the database lives in a Docker volume. The next Start recreates them."
        )
        column.addWidget(self.containers_radio)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.go = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.go.setText("Remove")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        column.addWidget(self.buttons)

        self.keep_radio.setVisible(uninstall_available)
        self.everything_radio.setVisible(uninstall_available)
        self.absent_label.setVisible(not uninstall_available)
        self.containers_radio.setVisible(False)
        self.advanced_check.toggled.connect(self.containers_radio.setVisible)

        if preselect is RemovalChoice.REMOVE_CONTAINERS:
            self.advanced_check.setChecked(True)
            self.containers_radio.setChecked(True)
        elif preselect is RemovalChoice.KEEP_CHARACTERS and uninstall_available:
            self.keep_radio.setChecked(True)
        elif preselect is RemovalChoice.EVERYTHING and uninstall_available:
            self.everything_radio.setChecked(True)
        else:
            self.off_radio.setChecked(True)

    def offered(self) -> tuple[RemovalChoice, ...]:
        """The choices this dialog offers, in the order it draws them."""
        choices = [RemovalChoice.OFF_THE_LIST]
        if not self.keep_radio.isHidden():
            choices.extend((RemovalChoice.KEEP_CHARACTERS, RemovalChoice.EVERYTHING))
        return tuple(choices)

    def choice(self) -> RemovalChoice:
        """What is selected now. Advanced's radio wins while it is checked."""
        if self.containers_radio.isChecked():
            return RemovalChoice.REMOVE_CONTAINERS
        if self.keep_radio.isChecked():
            return RemovalChoice.KEEP_CHARACTERS
        if self.everything_radio.isChecked():
            return RemovalChoice.EVERYTHING
        return RemovalChoice.OFF_THE_LIST

    def ask(self) -> RemovalChoice | None:
        """Run the dialog; the choice, or None for Cancel."""
        if self.exec() != QDialog.DialogCode.Accepted:
            return None
        return self.choice()


def ask_removal(
    parent: QWidget,
    *,
    uninstall_available: bool,
    preselect: RemovalChoice = RemovalChoice.OFF_THE_LIST,
) -> RemovalChoice | None:
    """Open the dialog over `parent`; the choice, or None for Cancel."""
    return RemoveDialog(parent, uninstall_available=uninstall_available, preselect=preselect).ask()


def offered_choices(uninstall_available: bool) -> tuple[RemovalChoice, ...]:
    """The choices offered for this wiring, in dialog order; what the seam is answered with."""
    choices: Sequence[RemovalChoice] = (
        (RemovalChoice.OFF_THE_LIST, RemovalChoice.KEEP_CHARACTERS, RemovalChoice.EVERYTHING)
        if uninstall_available
        else (RemovalChoice.OFF_THE_LIST,)
    )
    return tuple(choices)
