"""A `QMessageBox` whose buttons are never narrower than their labels (T157).

**Why a plain `QMessageBox` clips a long label under this app's theme.** The
theme's `QPushButton { min-width: 64px }` is meant as a floor under a button's
size, and in the size hint it is one. But Qt's style-sheet style also turns it
into an EXPLICIT `setMinimumWidth()` on every button it polishes -- 98px, once
the padding and border are added -- and a layout takes an explicit minimum over
the button's own `minimumSizeHint()`. Most layouts never notice, because they
hand a button its size hint whenever there is room. `QMessageBox` does not: it
fixes its own width at its layout's MINIMUM (no narrower than 500px on a wide
screen), so every button in it can be pressed down to 98px. "Update without a
backup" is 198px wide at its hint and was drawn 131px wide (measured offscreen,
2026-09-27, in the 400px box an 800px screen gets); on a 1920 desktop the same
day it read "Update without a backu".

**Why here and not in the theme.** Dropping `min-width` from the sheet would
also fix it, for every button in the app at once -- and that is the trouble.
The short buttons would lose their floor, and, by the same layout rule
read the other way, every row of buttons in the main window would stop being
able to shrink below its full labels: a new floor under a window that has to fit
a 960×640 handheld (Qt's rule, not measured on the window). The squeeze is only
wrong where a box sizes itself to its minimum, and that is this class.

**Why on every show AND every layout request, not once.** `QMessageBox` sizes
itself in its `showEvent()`, so the pin goes in just before the show reaches it
and the first frame is already right; pinned any later, the labels are drawn
clipped and then jump. And the style sheet sets the 98px again each time it
re-polishes a button: `main.py` re-applies the theme to the window on every
resize, dialog open or not. The re-polish is followed by a posted
`LayoutRequest`, the other event `QMessageBox` recomputes its width on, so the
pin goes in before that one too. Setting a minimum to the value it already has
is a no-op in Qt, so this does not feed itself.

Build every question through this class rather than `QMessageBox(...)`: the
three that relabel their buttons are the ones a long label reaches, and
`tests/test_message_box_buttons_fit.py` refuses a new bare construction.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QMessageBox, QPushButton

_REFIT_ON = (QEvent.Type.Show, QEvent.Type.LayoutRequest)
"""The two events `QMessageBox` recomputes its width on (`showEvent`, `event`)."""


class FittedMessageBox(QMessageBox):
    """`QMessageBox`, with each button held at least as wide as its own size hint."""

    def event(self, event: QEvent) -> bool:
        if event.type() in _REFIT_ON:
            fit_buttons_to_labels(self)
        return super().event(event)


def fit_buttons_to_labels(box: QMessageBox) -> None:
    """Set every button's minimum width to its size hint, which is its label's width.

    The size hint already carries the theme's 64px floor, so a short label
    keeps its old width and only a long one grows.
    """
    for button in box.findChildren(QPushButton):
        button.ensurePolished()
        button.setMinimumWidth(button.sizeHint().width())
