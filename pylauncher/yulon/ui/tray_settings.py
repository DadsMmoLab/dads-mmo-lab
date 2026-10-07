"""The tray's Settings (T540): keep running in the tray, and start in it at sign-in.

Opened from the flyout's gear and from the window header's "Settings…". Each
switch acts the moment it is clicked; the dialog only closes. Where the desktop
has no tray (GNOME without AppIndicator, a Steam Deck in Game Mode) both are
greyed and the dialog says once why closing Yu'lon quits it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from yulon import autostart
from yulon.log import get_logger
from yulon.ui.gamepad import BACK_CLOSES

if TYPE_CHECKING:
    from yulon.ui.tray import YulonTray

logger = get_logger(__name__)

KEEP = "Keep running when Yu'lon is closed"
SIGN_IN = "Start the tray when I sign in"
"""The owner's words on design C (T551), in the panel and the Settings dialog alike."""
NO_TRAY = (
    "This desktop has no system tray, so closing Yu'lon quits it, as it always has. "
    "Your servers keep running either way."
)
NEEDS_KEEP = "Turn on keeping Yu'lon in the tray first."


class TraySwitches(QWidget):
    """The tray's two switches and the one line that says what is not possible here, and why.

    Shared (T551): the Settings dialog shows them, and so does the foot of the
    tray's compact panel (design C). Each switch acts the moment it is clicked.
    """

    def __init__(self, tray: YulonTray, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.tray = tray
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(4)
        self.keep = QCheckBox(KEEP, self)
        self.sign_in = QCheckBox(SIGN_IN, self)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setObjectName("tray-settings-note")
        column.addWidget(self.keep)
        column.addWidget(self.sign_in)
        column.addWidget(self.note)
        self._said_problem = ""
        self.keep.toggled.connect(self._keep_toggled)
        self.sign_in.toggled.connect(self._sign_in_toggled)
        self.read()

    def read(self) -> None:
        """Show what is true now: the tray's setting and the sign-in entry on disk."""
        has_tray = self.tray.icon is not None
        for box, on in (
            (self.keep, self.tray.keep_in_tray and has_tray),
            (self.sign_in, autostart.is_enabled()),
        ):
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        self._settle()

    def _settle(self) -> None:
        """Which switch may be used, and the one line that says why one may not."""
        has_tray = self.tray.icon is not None
        self.keep.setEnabled(has_tray)
        why_not = autostart.why_not()
        if not has_tray:
            sign_in_why = NO_TRAY
        elif why_not is not None:
            sign_in_why = why_not
        elif not self.keep.isChecked():
            sign_in_why = NEEDS_KEEP
        else:
            sign_in_why = ""
        # An entry that is on can always be turned off (normal review): only
        # turning it ON needs a tray and an installed Yu'lon.
        self.sign_in.setEnabled(not sign_in_why or self.sign_in.isChecked())
        self.sign_in.setToolTip(sign_in_why)
        # The reason is said on screen, not only in a greyed box's tooltip.
        text = self._said_problem or sign_in_why
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _keep_toggled(self, on: bool) -> None:
        self._said_problem = ""
        if not on and self.sign_in.isChecked():
            # "Start the tray" with no tray to start in: the entry goes with it,
            # rather than staying on behind a greyed box (normal review).
            self.sign_in.setChecked(False)
        self.tray.remember_keep_in_tray(on)
        self._settle()

    def _sign_in_toggled(self, on: bool) -> None:
        self._said_problem = ""
        try:
            autostart.set_enabled(on)
        except OSError as exc:
            logger.warning(f"tray: could not turn start-at-sign-in {'on' if on else 'off'}: {exc}")
            self._said_problem = (
                f"Could not turn starting at sign-in {'on' if on else 'off'}: {exc}"
            )
            self.sign_in.blockSignals(True)
            self.sign_in.setChecked(not on)
            self.sign_in.blockSignals(False)
        self._settle()


class TraySettingsDialog(QDialog):
    """The header's Settings…: the tray's switches (`TraySwitches`) and Close."""

    def __init__(self, tray: YulonTray) -> None:
        super().__init__(None)
        from yulon.ui.tray import OWN_DIALOG

        self.tray = tray
        self.setWindowTitle("Yu'lon settings")
        self.setProperty(OWN_DIALOG, True)
        self.setProperty(BACK_CLOSES, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        column = QVBoxLayout(self)
        self.switches = TraySwitches(tray, self)
        self.keep = self.switches.keep
        self.sign_in = self.switches.sign_in
        self.note = self.switches.note
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        column.addWidget(self.switches)
        column.addWidget(buttons)
