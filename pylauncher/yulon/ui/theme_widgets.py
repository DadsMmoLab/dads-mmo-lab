"""Qt widget event filters for the Yu'lon theme engine (`style-guide.md` §3).

Houses `_SpinNumberGuard` and `_SpinBoxWatcher` (T821), which keep spin-box number
fields clear of their −/+ buttons across Qt 6.11+ stylesheet sub-control layouts.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QStyle,
    QStyleOptionSpinBox,
    QWidget,
)

TOUCH_TARGET_PX = 32
"""Minimum short-side size (px) for any interactive control.

The classic 44px figure is Apple's; on a 1280x800 handheld the whole viewport is
only 800px tall, so 44px on every row would fit eleven rows and nothing else. 32
is the Steam Deck's own on-screen-keyboard key floor and is the value Valve's
Game Mode UI uses for secondary controls — it stays comfortable under a thumb
while leaving the vertical budget the dense tabs need.
"""

_SPIN_NUMBER_GAP_PX = 6
"""Clearance (px) the spin-box number keeps from each of its −/+ buttons (T821)."""

_SPIN_GUARD_PROPERTY = "yulon_spin_guarded"
"""Dynamic property marking a spin box as carrying a `_SpinNumberGuard`."""


class _SpinNumberGuard(QObject):
    """Keep a spin box's number field clear of its −/+ buttons (T821).

    Qt 6.11.3's style sheet computes the number field as the spin rect minus
    exactly the two button rects, edge to edge, ignoring the sheet's
    horizontal padding (6.11.2 shrank the field by the 10 px padding plus the
    1 px border, so the number sat 11 px clear). Edge to edge means the
    number's last column IS the up button's first column, which the layout
    test reads as an overlap. The guard re-reads the buttons' real
    sub-control rects from the style on every resize and caps the line
    edit's maximum width so the number ends `_SPIN_NUMBER_GAP_PX` before the
    up button. The cap is a ceiling only: where the style already leaves
    room, the number is untouched.
    """

    def __init__(self, spin: QAbstractSpinBox) -> None:
        super().__init__(spin)
        self._spin = spin
        self._updating = False
        spin.installEventFilter(self)
        spin.lineEdit().installEventFilter(self)
        self._apply()

    def _apply(self) -> None:
        """Cap the number width from the buttons' live rects, when known."""
        spin = self._spin
        option = QStyleOptionSpinBox()
        spin.initStyleOption(option)
        control = QStyle.ComplexControl.CC_SpinBox
        up = spin.style().subControlRect(control, option, QStyle.SubControl.SC_SpinBoxUp, spin)
        edit = spin.lineEdit().geometry()
        if up.width() < TOUCH_TARGET_PX or edit.width() <= 0:
            return
        cap = max(0, up.x() - edit.x() - _SPIN_NUMBER_GAP_PX)
        field = spin.lineEdit()
        if cap < field.maximumWidth():
            self._updating = True
            try:
                field.setMaximumWidth(cap)
            finally:
                self._updating = False

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Re-cap the number after any resize settles the button rects."""
        if event.type() == QEvent.Type.Resize and not self._updating:
            self._apply()
        return False


def _guard_spin_numbers(root: QWidget) -> None:
    """Cap every spin box under `root` so its number clears its buttons."""
    for spin in root.findChildren(QAbstractSpinBox):
        if spin.property(_SPIN_GUARD_PROPERTY) == "1":
            guard = getattr(spin, "_yulon_spin_guard", None)
            if isinstance(guard, _SpinNumberGuard):
                guard._apply()
            continue
        spin.setProperty(_SPIN_GUARD_PROPERTY, "1")
        spin._yulon_spin_guard = _SpinNumberGuard(spin)  # type: ignore[attr-defined]


class _SpinBoxWatcher(QObject):
    """Guard spin boxes created after the theme went on (T821).

    Dialogs inherit the application sheet, so they never see their own
    `apply_dadcraft_theme` call. The watcher rides on the application and
    guards each spin box the first time it is shown, when its button rects
    are real.
    """

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """Guard every spin box at its first show."""
        if event.type() == QEvent.Type.Polish:
            if isinstance(watched, QAbstractSpinBox):
                if watched.property(_SPIN_GUARD_PROPERTY) != "1":
                    watched.setProperty(_SPIN_GUARD_PROPERTY, "1")
                    guard = _SpinNumberGuard(watched)
                    watched._yulon_spin_guard = guard  # type: ignore[attr-defined]
        return False


def _ensure_spin_watcher(app: QApplication) -> None:
    """Install the late-spin watcher on `app`, once."""
    if app.property(_SPIN_GUARD_PROPERTY) == "1":
        return
    app.setProperty(_SPIN_GUARD_PROPERTY, "1")
    app.installEventFilter(_SpinBoxWatcher(app))
