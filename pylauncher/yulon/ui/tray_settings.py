"""The tray's Settings (T540): keep running in the tray, and start in it at sign-in.

Opened from the flyout's gear and from the window header's "Settings…". Each
switch acts the moment it is clicked; the dialog only closes. Where the desktop
has no tray (GNOME without AppIndicator, a Steam Deck in Game Mode) both are
greyed and the dialog says once why closing Yu'lon quits it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QLabel, QVBoxLayout

from yulon import autostart
from yulon.log import get_logger
from yulon.ui.gamepad import BACK_CLOSES

if TYPE_CHECKING:
    from yulon.ui.tray import YulonTray

logger = get_logger(__name__)

KEEP = "Keep running in the tray when Yu'lon is closed"
SIGN_IN = "Start Yu'lon in the tray when I sign in"
NO_TRAY = (
    "This desktop has no system tray, so closing Yu'lon quits it, as it always has. "
    "Your servers keep running either way."
)
NEEDS_KEEP = "Turn on keeping Yu'lon in the tray first."


class TraySettingsDialog(QDialog):
    """Two switches and a line that says what is not possible here, and why."""

    def __init__(self, tray: YulonTray) -> None:
        super().__init__(None)
        from yulon.ui.tray import OWN_DIALOG

        self.tray = tray
        self.setWindowTitle("Yu'lon settings")
        self.setProperty(OWN_DIALOG, True)
        self.setProperty(BACK_CLOSES, True)
        self.setAttribute(Qt.WidgetAttribute.WA_QuitOnClose, False)
        column = QVBoxLayout(self)
        self.keep = QCheckBox(KEEP, self)
        self.sign_in = QCheckBox(SIGN_IN, self)
        self.note = QLabel(self)
        self.note.setWordWrap(True)
        self.note.setObjectName("tray-settings-note")
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        buttons.rejected.connect(self.close)
        column.addWidget(self.keep)
        column.addWidget(self.sign_in)
        column.addWidget(self.note)
        column.addWidget(buttons)
        self._said_problem = ""
        has_tray = tray.icon is not None
        self.keep.setChecked(tray.keep_in_tray and has_tray)
        self.sign_in.setChecked(has_tray and autostart.is_enabled())
        self.keep.toggled.connect(self._keep_toggled)
        self.sign_in.toggled.connect(self._sign_in_toggled)
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
        self.sign_in.setEnabled(not sign_in_why)
        self.sign_in.setToolTip(sign_in_why)
        # The reason is said on the dialog, not only in a greyed box's tooltip.
        text = self._said_problem or sign_in_why
        self.note.setText(text)
        self.note.setVisible(bool(text))

    def _keep_toggled(self, on: bool) -> None:
        self._said_problem = ""
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
